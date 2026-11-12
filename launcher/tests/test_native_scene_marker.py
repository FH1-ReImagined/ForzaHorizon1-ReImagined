import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forza_launcher import builder, native_scene_marker as marker


class NativeSceneMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdk = self.root / "sdk"
        self.build = self.root / "build"
        self.runtime = self.root / "runtime"
        self.sdk.mkdir()
        self.build.mkdir()
        self.runtime.mkdir()

    @staticmethod
    def mark(path, renderer, api=1):
        path.write_text(json.dumps({"api": api, "renderer_sha256": hashlib.sha256(renderer.read_bytes()).hexdigest()}))

    def run_stage(self, windows, mutate_rpath=False, rpath_status=0):
        folder = self.sdk / ("bin" if windows else "lib")
        folder.mkdir(exist_ok=True)
        plugin = folder / ("rexgpu-xenos.dll" if windows else "librexgpu-xenos.so")
        plugin.write_bytes(b"SDK renderer")
        capability = folder / marker.NAME
        self.mark(capability, plugin)
        if windows:
            (self.build / "forza.exe").write_bytes(b"exe")
        else:
            (folder / "librexruntime.so").write_bytes(b"runtime")
            for name in ("forza", "forza_XMediaFacade_default", "forza_SpeechFacade_default"):
                (self.build / name).write_bytes(b"exe")
        def change_rpath(*args, **kwargs):
            if mutate_rpath:
                (self.runtime / "librexgpu-xenos.so").write_bytes(b"SDK renderer with new rpath")
            return SimpleNamespace(returncode=rpath_status)
        with patch.object(builder, "IS_WIN", windows), patch.object(builder, "EXE", ".exe" if windows else ""), \
             patch.object(builder, "runtime_dir", return_value=self.runtime), \
             patch.object(builder.subprocess, "run", side_effect=change_rpath):
            builder.stage_runtime(SimpleNamespace(root=self.root), SimpleNamespace(), self.build, self.sdk)
        return plugin, capability

    def test_windows_stage_and_remove_stale(self):
        _, capability = self.run_stage(True)
        self.assertEqual(json.loads((self.runtime / marker.NAME).read_text()), json.loads(capability.read_text()))
        capability.unlink()
        with patch.object(builder, "IS_WIN", True), patch.object(builder, "EXE", ".exe"), \
             patch.object(builder, "runtime_dir", return_value=self.runtime):
            builder.stage_runtime(SimpleNamespace(root=self.root), SimpleNamespace(), self.build, self.sdk)
        self.assertFalse((self.runtime / marker.NAME).exists())

    def test_linux_stage_binds_after_rpath_change(self):
        self.run_stage(False, mutate_rpath=True)
        data = json.loads((self.runtime / marker.NAME).read_text())
        self.assertEqual(data, {"api": 1, "renderer_sha256": marker._sha256(self.runtime / "librexgpu-xenos.so")})
        self.assertFalse((self.runtime / "fh1-native-dlss.json").exists())

    def test_linux_failed_rpath_never_publishes_capability(self):
        (self.runtime / marker.NAME).write_text("stale")
        self.run_stage(False, mutate_rpath=True, rpath_status=1)
        self.assertFalse((self.runtime / marker.NAME).exists())
        self.assertTrue((self.runtime / "librexgpu-xenos.so").is_file())

    def test_linux_failed_rpath_even_if_bytes_unchanged(self):
        self.run_stage(False, mutate_rpath=False, rpath_status=1)
        self.assertFalse((self.runtime / marker.NAME).exists())

    def test_invalid_or_changed_inputs_remove_stale(self):
        src, dst, cap, result = (self.root / n for n in ("src", "dst", "cap", marker.NAME))
        src.write_bytes(b"sdk")
        dst.write_bytes(b"different")
        for api in (True, 2, "1"):
            self.mark(cap, src, api)
            result.write_text("stale")
            self.assertFalse(marker.stage(cap, src, dst, result, pre_rpath_sha256=marker._sha256(src)))
            self.assertFalse(result.exists())
        self.mark(cap, src)
        self.assertFalse(marker.stage(cap, src, dst, result))
        self.assertFalse(marker.stage(cap, src, dst, result, pre_rpath_sha256="bad"))
        dst.write_bytes(src.read_bytes())
        self.assertFalse(marker.stage(cap, src, dst, result, pre_rpath_sha256="bad"))
        dst.write_bytes(b"different")
        src.write_bytes(b"changed after marker")
        self.assertFalse(marker.stage(cap, src, dst, result, pre_rpath_sha256="bad"))
        cap.write_text("{broken")
        self.assertFalse(marker.stage(cap, src, dst, result))

    def test_windows_package_copies_and_removes_marker(self):
        bash = shutil.which("bash")
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if builder.IS_WIN:
            bash = str(git_bash) if git_bash.is_file() else None
        if not bash:
            self.skipTest("bash unavailable")
        prefix, dest = self.root / "prefix", self.root / "dist"
        (prefix / "bin").mkdir(parents=True)
        (dest / "licenses").mkdir(parents=True)
        capability = prefix / "bin" / marker.NAME
        original = prefix / "bin/rexgpu-xenos.dll"
        original.write_bytes(b"renderer")
        staged = dest / original.name
        staged.write_bytes(original.read_bytes())
        self.mark(capability, original)
        script = (Path(__file__).resolve().parents[2] / "tools/build_windows.sh").read_text()
        begin = script.index("# Native NGX capability")
        end = script.index('cp "$ROOT/tools/xcontent_extract.py"', begin)
        def package():
            subprocess.run([bash, "-c", 'set -e; PREFIX="$1"; DIST="$2"; ROOT="$3"; '
                            'python3() { "$PYTHON_EXECUTABLE" "$@"; };\n' + script[begin:end],
                            "--", prefix.as_posix(), dest.as_posix(),
                            Path(__file__).resolve().parents[2].as_posix()],
                           env={**os.environ, "PYTHON_EXECUTABLE": sys.executable}, check=True)
        package()
        self.assertEqual(json.loads((dest / marker.NAME).read_text()), json.loads(capability.read_text()))
        staged.write_bytes(b"mismatched staged renderer")
        package()
        self.assertFalse((dest / marker.NAME).exists())
        staged.write_bytes(original.read_bytes())
        capability.write_text('{"api":true}')
        package()
        self.assertFalse((dest / marker.NAME).exists())
        self.mark(capability, original)
        package()
        self.assertTrue((dest / marker.NAME).exists())
        capability.unlink()
        package()
        self.assertFalse((dest / marker.NAME).exists())


if __name__ == "__main__":
    unittest.main()
