"""Native menu preparation and installation using only synthetic files; no game data."""
import json
import errno
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from forza_launcher import builder  # noqa: E402


class Reporter(builder.Reporter):
    def __init__(self):
        self.lines = []
        self.cancel = False

    def log(self, text):
        self.lines.append(text)

    def cancelled(self):
        return self.cancel


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*") if p.is_file()}


class NativeMenuInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opts = builder.Options(self.root, jobs=1)
        self.rep = Reporter()
        self.tu = builder.tu_dir(self.root)
        self.tu.mkdir(parents=True)
        for name in (*builder.TU_EXECUTABLES, "media.zip"):
            (self.tu / name).write_bytes(("stock " + name).encode())
        self.original = snapshot(self.tu)
        self.stock = builder.runtime_dir(self.root) / "update"
        self.target = builder.runtime_dir(self.root) / "update-menu"
        shutil.copytree(self.tu, self.stock)
        self.target.mkdir(parents=True)
        (self.target / "previous.txt").write_text("previous complete install")
        self.script = self.root / "project" / "tools" / "native_menu_scene.py"
        patch = mock.patch.object(builder, "native_menu_script", return_value=self.script)
        patch.start()
        self.addCleanup(patch.stop)

    def prepare(self, cmd, cwd, env, reporter, **kwargs):
        self.assertEqual(cwd, self.root)
        self.assertIn(self.tu, cmd)
        output = cmd[cmd.index("--output") + 1]
        # The builder resolves its workspace; Windows CI's TEMP can use an 8.3
        # alias (RUNNER~1) for the same long path returned by resolve().
        self.assertTrue(output.resolve().is_relative_to(builder.build_dir(self.root).resolve()))
        self.assertFalse(output.resolve().is_relative_to(self.tu.resolve()))
        self.assertFalse(output.exists())
        shutil.copytree(self.tu, output)
        (output / "media.zip").write_bytes(b"prepared synthetic resources")
        (output / "native-menu-provenance.json").write_text(json.dumps({
            "schema": 1, "backend": "fh1-native-menu-resources"}))
        return output

    def test_success_installs_complete_overlay_and_preserves_source(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen), mock.patch.object(builder.paths, "FROZEN", frozen), \
                    mock.patch.object(builder, "run", side_effect=self.prepare) as run:
                self.assertTrue(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
                self.assertEqual("--run-script" in run.call_args.args[0], frozen)
                self.assertEqual(snapshot(self.stock), self.original)
                installed = snapshot(self.target)
                self.assertEqual(installed["media.zip"], b"prepared synthetic resources")
                self.assertNotIn("previous.txt", installed)
                for executable in builder.TU_EXECUTABLES:
                    self.assertEqual(installed[executable], self.original[executable])
                self.assertEqual(snapshot(self.tu), self.original)
                self.assertEqual(list(builder.build_dir(self.root).glob("native-menu-*")), [])
                self.assertEqual(list(self.target.parent.glob(".update-install-*")), [])

    def test_preparation_error_discards_partial_output_and_installs_stock(self):
        def fail(cmd, *args, **kwargs):
            output = cmd[cmd.index("--output") + 1]
            output.mkdir()
            (output / "media.zip").write_bytes(b"partial output")
            raise builder.BuildError("unsupported or modified FH1 archive")

        with mock.patch.object(builder, "run", side_effect=fail):
            self.assertFalse(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
        self.assertEqual(snapshot(self.stock), self.original)
        self.assertFalse(self.target.exists())
        self.assertEqual(snapshot(self.tu), self.original)
        self.assertTrue(any("Native menu unavailable" in line and "stock FH1" in line
                            for line in self.rep.lines))

    def test_incomplete_success_is_not_published(self):
        def incomplete(*args, **kwargs):
            output = self.prepare(*args, **kwargs)
            (output / "SpeechFacade_default.xex").unlink()

        with mock.patch.object(builder, "run", side_effect=incomplete):
            self.assertFalse(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
        self.assertEqual(snapshot(self.stock), self.original)
        self.assertFalse(self.target.exists())

    def test_preparation_uses_resolved_workspace_for_aliased_root(self):
        (self.root / "lexical-parent").mkdir()
        self.root = self.root / "lexical-parent" / ".."
        self.opts = builder.Options(self.root, jobs=1)
        with mock.patch.object(builder, "run", side_effect=self.prepare):
            self.assertTrue(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
        self.assertEqual((self.target / "media.zip").read_bytes(), b"prepared synthetic resources")

    def test_invalid_manifest_is_not_published(self):
        for report in ("invalid JSON", "[]", '{"schema": 9}'):
            def invalid(*args, **kwargs):
                output = self.prepare(*args, **kwargs)
                (output / "native-menu-provenance.json").write_text(report)
            with self.subTest(report=report), mock.patch.object(builder, "run", side_effect=invalid):
                self.assertFalse(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
                self.assertEqual(snapshot(self.stock), self.original)
                self.assertFalse(self.target.exists())

    def test_missing_tools_fall_back_without_launching_python(self):
        with mock.patch.object(builder, "native_menu_script", return_value=None), \
                mock.patch.object(builder, "run") as run:
            self.assertFalse(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
            run.assert_not_called()
        self.assertEqual(snapshot(self.stock), self.original)
        self.assertFalse(self.target.exists())

    def test_base_game_removes_old_update_without_preparation(self):
        with mock.patch.object(builder, "native_menu_script") as script, \
                mock.patch.object(builder, "run") as run:
            self.assertFalse(builder.stage_native_menu_update(self.opts, self.rep, None))
            run.assert_not_called()
            script.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(snapshot(self.tu), self.original)

    def test_menu_failure_preserves_independent_stock_and_tuning_resources(self):
        tuning = self.target.parent / "update-native"
        tuning.mkdir()
        (tuning / "media.zip").write_bytes(b"standalone tuning only")
        with mock.patch.object(builder, "run", side_effect=builder.BuildError("unsupported menu")):
            self.assertFalse(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
        self.assertEqual(snapshot(self.stock), self.original)
        self.assertEqual((tuning / "media.zip").read_bytes(), b"standalone tuning only")
        self.assertFalse(self.target.exists())

    def test_build_prepares_tuning_and_menu_independently_from_pristine_update(self):
        tuning_script = self.root / "project" / "tools" / "native_tuning_assets.py"
        def prepare(cmd, *args, **kwargs):
            if tuning_script in cmd:
                output = cmd[cmd.index("--output") + 1]
                shutil.copytree(self.tu, output)
                (output / "media.zip").write_bytes(b"standalone tuning only")
                (output / "native-tuning-provenance.json").write_text(json.dumps(
                    {"schema": 1, "source_archive_sha256": "a" * 64, "entries": []}))
            else:
                self.prepare(cmd, *args, **kwargs)
        with mock.patch.object(builder, "native_tuning_script", return_value=tuning_script), \
                mock.patch.object(builder, "run", side_effect=prepare) as run:
            self.assertTrue(builder.stage_runtime_update(self.opts, self.rep, self.tu))
            self.assertTrue(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
            self.assertEqual(run.call_count, 2)
        self.assertEqual(snapshot(self.stock), self.original)
        self.assertEqual(snapshot(self.tu), self.original)
        self.assertEqual((self.target.parent / "update-native" / "media.zip").read_bytes(),
                         b"standalone tuning only")
        self.assertEqual((self.target / "media.zip").read_bytes(), b"prepared synthetic resources")

    def test_cancellation_preserves_installed_update(self):
        previous = snapshot(self.target)
        with mock.patch.object(builder, "run", side_effect=builder.Cancelled):
            with self.assertRaises(builder.Cancelled):
                builder.stage_native_menu_update(self.opts, self.rep, self.tu)
        self.assertEqual(snapshot(self.target), previous)
        self.assertFalse(any("unavailable" in line for line in self.rep.lines))

    def test_cancel_after_copy_preserves_installed_update(self):
        previous = snapshot(self.target)
        self.rep.cancel = True
        with self.assertRaises(builder.Cancelled):
            builder._publish_runtime_update(self.root, self.tu, self.rep, "update-menu")
        self.assertEqual(snapshot(self.target), previous)

    def test_copy_failure_preserves_installed_update(self):
        previous = snapshot(self.target)
        with mock.patch.object(builder.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                builder._publish_runtime_update(self.root, self.tu, self.rep, "update-menu")
        self.assertEqual(snapshot(self.target), previous)

    def test_publish_failure_restores_previous_update(self):
        previous = snapshot(self.target)
        replace = os.replace

        def fail_new(source, destination):
            if Path(source).name == "new":
                raise OSError("update directory is locked")
            replace(source, destination)

        with mock.patch.object(builder.os, "replace", side_effect=fail_new):
            with self.assertRaises(OSError):
                builder._publish_runtime_update(self.root, self.tu, self.rep, "update-menu")
        self.assertEqual(snapshot(self.target), previous)

    def test_failed_rollback_retains_previous_update_for_recovery(self):
        previous = snapshot(self.target)
        replace = os.replace

        def fail_new_and_restore(source, destination):
            if Path(source).name in ("new", "previous"):
                raise OSError("update directory is locked")
            replace(source, destination)

        with mock.patch.object(builder.os, "replace", side_effect=fail_new_and_restore):
            with self.assertRaisesRegex(builder.BuildError, "preserved at"):
                builder._publish_runtime_update(self.root, self.tu, self.rep, "update-menu")
        backups = list(self.target.parent.glob(".update-install-*/previous"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(snapshot(backups[0]), previous)

    def test_source_and_runtime_cannot_overlap(self):
        previous = snapshot(self.target)
        for source in (self.target, self.target.parent, self.root):
            with self.subTest(source=source), self.assertRaises(builder.BuildError):
                builder._publish_runtime_update(self.root, source, self.rep, "update-menu")
        self.assertEqual(snapshot(self.target), previous)

    def test_source_cannot_contain_temporary_build_workspace(self):
        with mock.patch.object(builder, "run") as run, self.assertRaises(builder.BuildError):
            builder.stage_native_menu_update(self.opts, self.rep, builder.build_dir(self.root))
        run.assert_not_called()

    def link(self, link, target):
        # An existing destination is a broken fixture, not unavailable link support.
        self.assertFalse(os.path.lexists(link), f"Link destination already exists: {link}")
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if isinstance(error, FileExistsError):
                raise
            if os.name == "nt":
                result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                        capture_output=True, text=True)
                if result.returncode == 0:
                    return
                self.fail(f"Junction fixture creation failed: {result.stdout} {result.stderr}")
            if error.errno not in (errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP):
                raise
            self.skipTest(f"Directory symlinks unavailable: {error}")

    def test_linked_runtime_and_build_are_supported(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            runtime = self.target.parent
            build = builder.build_dir(self.root)
            # Create the source once, before moving it; never mkdir the link destination.
            build.mkdir(exist_ok=True)
            (build / "linked-build-marker").write_text("retained")
            shutil.move(runtime, external / "runtime")
            self.link(runtime, external / "runtime")
            shutil.move(build, external / "build")
            self.link(build, external / "build")
            for link in (build, runtime):
                self.assertTrue(link.is_symlink() or
                                (hasattr(link, "is_junction") and link.is_junction()))
            with mock.patch.object(builder, "run", side_effect=self.prepare) as run:
                self.assertTrue(builder.stage_native_menu_update(self.opts, self.rep, self.tu))
            run.assert_called_once()
            self.assertEqual(build.resolve(), (external / "build").resolve())
            self.assertEqual(runtime.resolve(), (external / "runtime").resolve())
            self.assertEqual((build / "linked-build-marker").read_text(), "retained")
            self.assertEqual(snapshot(self.stock), self.original)
            self.assertEqual((self.target / "media.zip").read_bytes(), b"prepared synthetic resources")

    def test_update_child_links_are_rejected_without_touching_target(self):
        for name in ("update", "update-menu"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as outside:
                external = Path(outside)
                (external / "keep").write_text("untouched")
                target = self.target.parent / name
                if target.exists():
                    shutil.rmtree(target)
                self.link(target, external)
                try:
                    with self.assertRaises(builder.BuildError):
                        builder._publish_runtime_update(self.root, self.tu, self.rep, name)
                    self.assertEqual((external / "keep").read_text(), "untouched")
                finally:
                    if target.is_symlink():
                        target.unlink()
                    else:
                        target.rmdir()  # Remove the junction itself, never its contents.


class NativeMenuDistribution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        (self.project / "server").mkdir(parents=True)
        (self.project / "server" / "forza_live_server.py").write_text("synthetic server")
        (self.project / "forza-recomp").mkdir(parents=True)
        (self.project / "forza-recomp" / "CMakeLists.txt").write_text("synthetic sources")
        for name in (*builder.NATIVE_MENU_FILES, *builder.NATIVE_TUNING_FILES):
            path = self.project / "tools" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic " + name)

    def test_preparation_script_or_manifest_changes_request_rebuild(self):
        digest = builder.sources_hash(self.project)
        self.assertEqual(builder.sources_hash(self.project), digest)
        for name in (*builder.NATIVE_MENU_FILES, *builder.NATIVE_TUNING_FILES):
            path = self.project / "tools" / name
            path.write_text(path.read_text() + " changed")
            changed = builder.sources_hash(self.project)
            self.assertNotEqual(changed, digest)
            digest = changed
        (self.project / "tools" / builder.NATIVE_MENU_FILES[-1]).unlink()
        self.assertNotEqual(builder.sources_hash(self.project), digest)

    def test_project_resources_preferred_and_bundle_fallback_requires_complete_tools(self):
        bundled = self.root / "bundle"
        shutil.copytree(self.project / "tools", bundled / "tools")
        with mock.patch.object(builder, "project_dir", return_value=self.project), \
                mock.patch.object(builder.paths, "LAUNCHER_ROOT", bundled):
            self.assertEqual(builder.native_menu_script(), self.project / "tools" / "native_menu_scene.py")
            (self.project / "tools" / "native_menu/fh1_donors.json").unlink()
            self.assertEqual(builder.native_menu_script(), bundled / "tools" / "native_menu_scene.py")
            (bundled / "tools" / "offline_cars_flow.py").unlink()
            self.assertIsNone(builder.native_menu_script())

    def test_release_package_contains_original_preparation_resources(self):
        package = runpy.run_path(str(HERE / "packaging" / "make_package.py"))
        package["main"].__globals__["REPO"] = self.project
        (self.project / "LICENSE").write_text("synthetic license")
        (self.project / "NOTICE.md").write_text("synthetic notice")
        (self.project / "PRIVACY.md").write_text("synthetic privacy policy")
        frozen = self.root / "frozen"
        frozen.mkdir()
        (frozen / "forza-launcher.exe").write_text("synthetic launcher")
        output = self.root / "dist"
        with mock.patch.object(sys, "argv", ["make_package.py", "--platform", "windows",
                                            "--pyinstaller-dist", str(frozen), "--out", str(output)]):
            package["main"]()
        installed = output / "ForzaHorizon1-ReImagined" / "project" / "tools"
        for name in (*builder.NATIVE_MENU_FILES, *builder.NATIVE_TUNING_FILES):
            self.assertEqual((installed / name).read_bytes(), (self.project / "tools" / name).read_bytes())

    def test_flatpak_package_contains_original_preparation_resources(self):
        package = runpy.run_path(str(HERE.parent / "flatpak" / "stage.py"))
        package["main"].__globals__["REPO"] = self.project
        (self.project / "launcher" / "forza_launcher").mkdir(parents=True)
        (self.project / "launcher" / "main.py").write_text("synthetic launcher")
        (self.project / "LICENSE").write_text("synthetic license")
        (self.project / "NOTICE.md").write_text("synthetic notice")
        (self.project / "PRIVACY.md").write_text("synthetic privacy policy")
        for asset in package["ASSETS"]:
            path = self.project / "docs" / "images" / asset
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic image")
        for tool in package["TOOLS"]:
            (self.project / "tools" / tool).write_text("synthetic " + tool)
        overlay = self.root / "overlay.tar.gz"
        overlay.write_bytes(b"synthetic SDK overlay")
        output = self.root / "flatpak-stage"
        with mock.patch.object(sys, "argv", ["stage.py", "--overlay", str(overlay), "--out", str(output)]):
            package["main"]()
        for name in (*builder.NATIVE_MENU_FILES, *builder.NATIVE_TUNING_FILES):
            self.assertEqual((output / "project" / "tools" / name).read_bytes(),
                             (self.project / "tools" / name).read_bytes())

    def test_linux_tar_keeps_package_name_after_staging_nested_resources(self):
        import tarfile
        package = runpy.run_path(str(HERE / "packaging" / "make_package.py"))
        package["main"].__globals__["REPO"] = self.project
        (self.project / "LICENSE").write_text("synthetic license")
        (self.project / "NOTICE.md").write_text("synthetic notice")
        (self.project / "PRIVACY.md").write_text("synthetic privacy policy")
        logo = self.project / "docs" / "images" / "logo.png"
        logo.parent.mkdir(parents=True)
        logo.write_bytes(b"synthetic image")
        frozen = self.root / "frozen"
        frozen.mkdir()
        (frozen / "forza-launcher").write_text("synthetic launcher")
        output = self.root / "dist"
        with mock.patch.object(sys, "argv", ["make_package.py", "--platform", "linux", "--tar",
                                            "--pyinstaller-dist", str(frozen), "--out", str(output)]):
            package["main"]()
        archive = output / "ForzaHorizon1-ReImagined-linux-x86_64.tar.gz"
        with tarfile.open(archive) as tar:
            names = set(tar.getnames())
        for resource in builder.NATIVE_MENU_FILES:
            self.assertIn("ForzaHorizon1-ReImagined/project/tools/" + resource, names)


if __name__ == "__main__":
    unittest.main()
