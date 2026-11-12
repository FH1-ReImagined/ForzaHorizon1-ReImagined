import contextlib
import csv
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("trim_pyside6", REPO / "flatpak/trim_pyside6.py")
trim_pyside6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trim_pyside6)

# The files the 6.12 wheels share: each of Essentials, Addons and WebEngine lists them.
SHARED = ["PySide6/__init__.py", "PySide6/_config.py", "PySide6/_git_pyside_version.py",
          "PySide6/py.typed"]
WHEELS = {
    "pyside6_essentials-6.12.0": SHARED + [
        "PySide6/QtCore.abi3.so", "PySide6/QtWidgets.abi3.so", "PySide6/Qt/lib/libQt6Core.so.6",
        "PySide6/Qt/lib/libQt6Quick.so.6", "PySide6/Qt/qml/QtQuick/qmldir"],
    "shiboken6-6.12.0": ["shiboken6/__init__.py", "shiboken6/Shiboken.abi3.so"],
    "pyside6_addons-6.12.0": SHARED + [
        "PySide6/QtCharts.abi3.so", "PySide6/QtCharts.pyi", "PySide6/Qt/lib/libQt6Charts.so.6",
        "PySide6/Qt/lib/libQt6Positioning.so.6", "PySide6/Qt/lib/libQt6WebChannel.so.6",
        "PySide6/QtWebChannel.abi3.so", "PySide6/QtWebChannel.pyi", "PySide6/balsam"],
    "pyside6_webengine-6.12.0.140": SHARED + [
        "PySide6/QtWebEngineCore.abi3.so", "PySide6/QtWebEngineWidgets.abi3.so",
        "PySide6/QtWebEngineQuick.abi3.so", "PySide6/Qt/lib/libQt6WebEngineCore.so.6",
        "PySide6/Qt/lib/libQt6WebEngineQuick.so.6", "PySide6/Qt/libexec/QtWebEngineProcess",
        "PySide6/Qt/resources/qtwebengine_resources.pak",
        "PySide6/Qt/resources/qtwebengine_devtools_resources.pak",
        "PySide6/Qt/translations/qtwebengine_locales/en-US.pak",
        "PySide6/Qt/translations/qtwebengine_locales/de.pak",
        "PySide6/Qt/qml/QtWebEngine/qmldir"],
}


class Trim(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.site = Path(temp.name)

    def install(self, wheels):
        for dist, files in wheels.items():
            info = self.site / f"{dist}.dist-info"
            info.mkdir()
            with open(info / "RECORD", "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for rel in files + [f"{dist}.dist-info/RECORD"]:
                    writer.writerow([rel, "sha256=x", "1"])
                    path = self.site / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        path.write_bytes(b"x")

    def remaining(self):
        return {p.relative_to(self.site).as_posix() for p in self.site.rglob("*")
                if p.is_file() and ".dist-info" not in p.as_posix()}

    def test_package_files_shared_with_essentials_survive(self):
        self.install(WHEELS)
        trim_pyside6.trim(self.site)
        left = self.remaining()
        for rel in SHARED:
            self.assertIn(rel, left)

    def test_unused_addons_and_webengine_parts_go(self):
        self.install(WHEELS)
        trim_pyside6.trim(self.site)
        left = self.remaining()
        for gone in ("PySide6/QtCharts.abi3.so", "PySide6/QtCharts.pyi", "PySide6/balsam",
                     "PySide6/Qt/lib/libQt6Charts.so.6", "PySide6/QtWebEngineQuick.abi3.so",
                     "PySide6/Qt/lib/libQt6WebEngineQuick.so.6",
                     "PySide6/Qt/resources/qtwebengine_devtools_resources.pak",
                     "PySide6/Qt/translations/qtwebengine_locales/de.pak",
                     "PySide6/Qt/qml/QtWebEngine/qmldir"):
            self.assertNotIn(gone, left)
        for kept in trim_pyside6.ADDONS_KEEP | {
                "PySide6/QtWebEngineCore.abi3.so", "PySide6/QtWebEngineWidgets.abi3.so",
                "PySide6/Qt/lib/libQt6WebEngineCore.so.6", "PySide6/Qt/libexec/QtWebEngineProcess",
                "PySide6/Qt/resources/qtwebengine_resources.pak",
                "PySide6/Qt/translations/qtwebengine_locales/en-US.pak"}:
            self.assertIn(kept, left)

    def test_essentials_and_shiboken_files_all_stay(self):
        self.install(WHEELS)
        trim_pyside6.trim(self.site)
        left = self.remaining()
        for dist in ("pyside6_essentials-6.12.0", "shiboken6-6.12.0"):
            for rel in WHEELS[dist]:
                self.assertIn(rel, left)

    def test_refuses_without_the_essentials_record(self):
        wheels = {k: v for k, v in WHEELS.items() if not k.startswith("pyside6_essentials")}
        self.install(wheels)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            trim_pyside6.trim(self.site)
        self.assertIn("PySide6/__init__.py", self.remaining())


if __name__ == "__main__":
    unittest.main()
