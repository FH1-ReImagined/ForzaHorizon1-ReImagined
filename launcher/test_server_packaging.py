"""A release must contain its pinned local server, including from source archives."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"


def server_payload():
    """What a release must ship of server/: the entry point and the forza_live package with its
    data (no tests, no caches), by path relative to server/."""
    files = [SERVER / "forza_live_server.py"]
    files += [f for f in (SERVER / "forza_live").rglob("*")
              if f.is_file() and f.suffix in (".py", ".xml") and "__pycache__" not in f.parts]
    return {f.relative_to(SERVER).as_posix(): f.read_bytes() for f in files}


def tree(root):
    return {f.relative_to(root).as_posix(): f.read_bytes() for f in root.rglob("*") if f.is_file()}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load("server_package", REPO / "launcher/packaging/make_package.py")
flatpak = load("server_flatpak", REPO / "flatpak/stage.py")


class ServerPackaging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        for name in ("launcher/forza_launcher/__init__.py", "launcher/main.py", "LICENSE", "NOTICE.md", "PRIVACY.md",
                     "forza-recomp/src/fixture.cpp", "tools/cmake/fixture.cmake"):
            self.write(name)
        for name in flatpak.ASSETS:
            self.write("docs/images/" + name)
        for name in set(flatpak.TOOLS) | set(package.builder.NATIVE_TUNING_FILES) | set(package.builder.NATIVE_MENU_FILES):
            self.write("tools/" + name)
        self.dist = self.base / "pyinstaller"
        self.dist.mkdir()
        (self.dist / "forza-launcher").write_bytes(b"fixture")
        self.overlay = self.base / "overlay.tar.gz"
        self.overlay.write_bytes(b"fixture")

    def write(self, name):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    def server(self, state):
        if state == "empty":
            (self.repo / "server").mkdir()
        elif state == "initialized":
            # the whole checkout of the submodule, tests included: they must stay out
            shutil.copytree(SERVER, self.repo / "server", ignore=shutil.ignore_patterns(".git", "__pycache__"))

    def assemble(self, module):
        out = self.base / "out"
        if module is package:
            args = ["make_package.py", "--platform", "linux", "--pyinstaller-dist", str(self.dist),
                    "--overlay", str(self.overlay), "--out", str(out), "--tar"]
            stage = out / "ForzaHorizon1-ReImagined"
        else:
            args = ["stage.py", "--overlay", str(self.overlay), "--out", str(out)]
            stage = out
        with patch.object(module, "REPO", self.repo), patch.object(sys, "argv", args):
            module.main()
        return stage

    def check_payload(self, stage):
        payload = server_payload()
        self.assertIn("forza_live/server.py", payload)
        self.assertIn("forza_live/title_services/playground_rounds.xml", payload)
        self.assertEqual(tree(stage / "server"), payload)

    def test_package_initialized(self):
        self.server("initialized")
        stage = self.assemble(package)
        self.check_payload(stage)
        # The privacy policy the Windows installer shows (installer.iss InfoBeforeFile, #497).
        self.assertEqual((stage / "licenses" / "PRIVACY.txt").read_bytes(), b"fixture")
        import tarfile
        with tarfile.open(self.base / "out/ForzaHorizon1-ReImagined-linux-x86_64.tar.gz") as archive:
            prefix = "ForzaHorizon1-ReImagined/server/"
            payload = {m.name[len(prefix):]: archive.extractfile(m).read() for m in archive.getmembers()
                       if m.isfile() and m.name.startswith(prefix)}
        self.assertEqual(payload, tree(stage / "server"))

    def test_flatpak_initialized(self):
        self.server("initialized")
        self.check_payload(self.assemble(flatpak))

    def check_missing(self, module, state):
        self.server(state)
        with self.assertRaisesRegex(SystemExit, "git submodule update --init server"):
            self.assemble(module)

    def test_package_empty(self):
        self.check_missing(package, "empty")

    def test_package_absent(self):
        self.check_missing(package, "absent")

    def test_flatpak_empty(self):
        self.check_missing(flatpak, "empty")

    def test_flatpak_absent(self):
        self.check_missing(flatpak, "absent")


if __name__ == "__main__":
    unittest.main()
