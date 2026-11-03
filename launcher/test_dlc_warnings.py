"""Unknown DLC remains installable, with a named warning in Setup and the install log (#118)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import build_env, installer
from test_xcontent import build as build_stfs

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None

KNOWN = next(iter(installer.install_dlc.KNOWN_NAMES))
UNKNOWN = "DEADBEEF01234567"
DISPLAY = "Unicorn cars <unlock>"
PAYLOAD = b"synthetic DLC test content"


def extracted(root, name, display):
    package = root / name
    package.mkdir(parents=True)
    (package / "fixture.bin").write_bytes(PAYLOAD)
    header = root.parent / "Headers" / root.name / (name + ".header")
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_bytes(installer.install_dlc.header_bytes(name, display, installer.install_dlc.TITLE_ID))
    return package


def packed(root, name, display):
    root.mkdir(parents=True, exist_ok=True)
    data = bytearray(build_stfs(PAYLOAD))
    struct.pack_into(">I", data, 0x344, installer.install_dlc.MARKETPLACE)
    struct.pack_into(">I", data, 0x360, installer.install_dlc.TITLE_ID)
    text = display.encode("utf-16-be")
    data[0x411:0x411 + len(text)] = text
    package = root / name
    package.write_bytes(data)
    return package


class DlcWarnings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        installer._DLC_CACHE.clear()
        self.addCleanup(installer._DLC_CACHE.clear)
        # The package reader exposes raw files rather than a context manager. Keep
        # fixture readers alive and close them before Windows removes the temp tree.
        packages = []
        open_package = installer.xcontent_extract.open_package

        def tracked_open(path):
            package = open_package(path)
            packages.append(package)
            return package

        def close_packages():
            for package in packages:
                for file in getattr(package, "files", []):
                    file.close()

        reader = patch.object(installer.xcontent_extract, "open_package", side_effect=tracked_open)
        reader.start()
        self.addCleanup(reader.stop)
        self.addCleanup(close_packages)

    def test_all_known_package_names_are_case_insensitive(self):
        found = list(installer.install_dlc.KNOWN_NAMES.items())
        self.assertEqual(installer.dlc_warning(found), "")
        self.assertEqual(installer.dlc_warning([(n.lower(), d) for n, d in found]), "")
        self.assertEqual(installer.dlc_warning([]), "")

    def test_display_names_do_not_bypass_recognition(self):
        display = installer.install_dlc.KNOWN_NAMES[KNOWN]
        warning = installer.dlc_warning([(UNKNOWN, display), ("CAFEBABE", "")])
        self.assertIn(display, warning)
        self.assertIn(UNKNOWN, warning)
        self.assertIn("CAFEBABE", warning)
        self.assertIn("You can still install", warning)
        self.assertIn("endless loading", warning)

    def test_mixed_packaged_and_extracted_dlc_warns_and_installs_all(self):
        source = self.root / "input"
        known = packed(source, KNOWN.lower(), "Official fixture")
        unknown = extracted(source, UNKNOWN, DISPLAY)
        before = known.read_bytes()
        found, skipped = installer.describe_dlc_folder(str(source))
        self.assertEqual(skipped, [])
        self.assertEqual(len(found), 2)
        warning = installer.dlc_warning(found)
        self.assertIn(DISPLAY, warning)
        self.assertIn(UNKNOWN, warning)
        self.assertNotIn(KNOWN.lower(), warning)
        logs = []
        data = self.root / "installed"
        count = installer.install_dlc_folder(str(source), data, lambda *_: None, log=logs.append)
        self.assertEqual(count, 2)
        self.assertIn(warning, logs)
        destination = data / "0000000000000000" / "4D5309C9" / "00000002"
        self.assertEqual((destination / KNOWN.lower() / "b.bin").read_bytes(), PAYLOAD)
        self.assertEqual((destination / UNKNOWN / "fixture.bin").read_bytes(), PAYLOAD)
        self.assertEqual(known.read_bytes(), before)
        self.assertEqual((unknown / "fixture.bin").read_bytes(), PAYLOAD)

    def test_unknown_stfs_package_is_named_and_installed(self):
        source = self.root / "input"
        packed(source, UNKNOWN, DISPLAY)
        found, _ = installer.describe_dlc_folder(str(source))
        self.assertEqual(found, [(UNKNOWN, DISPLAY)])
        logs = []
        data = self.root / "installed"
        self.assertEqual(installer.install_dlc_folder(str(source), data, lambda *_: None, log=logs.append), 1)
        self.assertIn(installer.dlc_warning(found), logs)
        self.assertEqual((data / "0000000000000000/4D5309C9/00000002" / UNKNOWN / "b.bin").read_bytes(), PAYLOAD)

    def test_official_only_dlc_installs_without_warning(self):
        source = self.root / "input"
        extracted(source, KNOWN, "Official fixture")
        found, _ = installer.describe_dlc_folder(str(source))
        self.assertEqual(installer.dlc_warning(found), "")
        logs = []
        self.assertEqual(installer.install_dlc_folder(str(source), self.root / "installed",
                                                     lambda *_: None, log=logs.append), 1)
        self.assertFalse(any("Unrecognized" in line for line in logs))

    def test_xenia_layout_without_header_uses_package_name(self):
        content = self.root / "content"
        source = content / "0000000000000000/4D5309C9/00000002"
        package = source / UNKNOWN
        package.mkdir(parents=True)
        (package / "fixture.bin").write_bytes(PAYLOAD)
        (source / "not-a-package").mkdir()
        (source / "not-a-package" / "readme.txt").write_text("not DLC")
        found, skipped = installer.describe_dlc_folder(str(content))
        self.assertEqual(found, [(UNKNOWN, "DLC " + UNKNOWN)])
        warning = installer.dlc_warning(found)
        self.assertIn(UNKNOWN, warning)
        self.assertNotIn("not-a-package", warning)
        self.assertEqual(skipped, [("not-a-package", "not a package name")])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class DlcWarningUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        installer._DLC_CACHE.clear()
        self.addCleanup(installer._DLC_CACHE.clear)
        build_env.forget_tools()
        self.addCleanup(build_env.forget_tools)
        for p in (patch("forza_launcher.paths.config_dir", return_value=self.root),
                  patch.object(build_env, "_detect_tools", return_value=build_env.Tools())):
            p.start()
            self.addCleanup(p.stop)
        cfg = Config(self.root / "launcher.json")
        cfg.gpu["checked"] = True
        cfg.loaded = True
        cfg.d["check_updates"] = False
        cfg.advanced["data_root"] = str(self.root / "data")
        self.win = MainWindow(cfg)
        self.addCleanup(self.win.close)
        self.setup = self.win.setup
        self.wait_done()

    def wait_done(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.app.processEvents()
            # A fast install can finish its thread before Qt delivers the queued
            # done signal; wait for _finished to consume it and clear task too.
            if not self.setup._probes and self.setup.task is None:
                for thread in getattr(self.setup, "_zombies", []):
                    self.assertTrue(thread.wait(1000))
                return
            time.sleep(.005)
        self.fail("DLC scan/install did not finish")

    def select(self, folder):
        self.setup.dlc.setText(str(folder))
        self.setup._dlc_changed()
        self.wait_done()

    def test_warning_survives_install_without_confirmation_or_block(self):
        source = self.root / "input"
        extracted(source, UNKNOWN, DISPLAY)
        self.select(source)
        warning = self.setup.dlcwarning
        self.assertFalse(warning.isHidden())
        self.assertEqual(warning.textFormat(), Qt.PlainText)
        self.assertIn(DISPLAY, warning.text())
        self.assertIn(UNKNOWN, warning.text())
        self.assertTrue(self.setup.go2.isEnabled())
        with patch.object(QMessageBox, "warning") as alert, patch.object(QMessageBox, "question") as question:
            self.setup.go2.click()
            self.wait_done()
            alert.assert_not_called()
            question.assert_not_called()
        self.assertIn("Installed 1 DLC package", self.setup.msg2.text())
        self.assertFalse(warning.isHidden())
        destination = self.root / "data/0000000000000000/4D5309C9/00000002" / UNKNOWN / "fixture.bin"
        self.assertEqual(destination.read_bytes(), PAYLOAD)

    def test_unknown_beyond_the_preview_limit_is_still_named(self):
        source = self.root / "input"
        for name, display in installer.install_dlc.KNOWN_NAMES.items():
            extracted(source, name, display)
        extracted(source, "FFFFFFFFFFFFFFFF", DISPLAY)
        self.select(source)
        self.assertNotIn(DISPLAY, self.setup.dlclist.text())
        self.assertIn(DISPLAY, self.setup.dlcwarning.text())
        self.assertIn("FFFFFFFFFFFFFFFF", self.setup.dlcwarning.text())

    def test_folder_changed_during_probe_is_scanned_again(self):
        source = self.root / "input"
        extracted(source, KNOWN, "Official fixture")
        describe = installer.describe_dlc_folder
        calls = []

        def changed_after_scan(folder):
            answer = describe(folder)
            calls.append(folder)
            if len(calls) == 1:
                before = source.stat()
                extracted(source, UNKNOWN, DISPLAY)
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 3_000_000_000))
            return answer

        with patch.object(installer, "describe_dlc_folder", side_effect=changed_after_scan):
            self.select(source)
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(self.setup.dlcwarning.isHidden())
        self.assertIn(DISPLAY, self.setup.dlcwarning.text())

    def test_changing_to_known_empty_missing_or_cleared_folder_removes_warning(self):
        unknown = self.root / "unknown"
        known = self.root / "known"
        empty = self.root / "empty"
        extracted(unknown, UNKNOWN, DISPLAY)
        extracted(known, KNOWN, "Official fixture")
        empty.mkdir()
        for folder in (known, empty, self.root / "missing", ""):
            with self.subTest(folder=str(folder)):
                self.select(unknown)
                self.assertFalse(self.setup.dlcwarning.isHidden())
                self.select(folder)
                self.assertTrue(self.setup.dlcwarning.isHidden())
                self.assertEqual(self.setup.dlcwarning.text(), "")


if __name__ == "__main__":
    unittest.main()
