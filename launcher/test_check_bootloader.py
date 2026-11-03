"""check_bootloader.py tells a stock PyInstaller bootloader from one compiled in CI (#482, #354)."""
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "packaging" / "check_bootloader.py"
spec = importlib.util.spec_from_file_location("check_bootloader", SCRIPT)
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


class CheckBootloader(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def installed(self, run=b"run", runw=b"runw"):
        folder = self.root / "installed"
        folder.mkdir(exist_ok=True)
        (folder / "run.exe").write_bytes(run)
        (folder / "runw.exe").write_bytes(runw)
        return cb.installed_hashes(folder)

    def wheel(self, run=b"run", runw=b"runw"):
        path = self.root / "pyinstaller-1.0-py3-none-win_amd64.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"PyInstaller/bootloader/{cb.PLATFORM}/run.exe", run)
            archive.writestr(f"PyInstaller/bootloader/{cb.PLATFORM}/runw.exe", runw)
            archive.writestr(f"PyInstaller/bootloader/{cb.PLATFORM}/run_d.exe", b"debug")
        return cb.stock_hashes(path)

    def test_identical_bootloaders_are_the_stock_ones(self):
        self.assertEqual(cb.stock_ones(self.installed(), self.wheel()), ["run.exe", "runw.exe"])

    def test_one_stock_bootloader_is_enough_to_fail(self):
        self.assertEqual(cb.stock_ones(self.installed(run=b"compiled here"), self.wheel()), ["runw.exe"])

    def test_compiled_bootloaders_differ_from_the_stock_ones(self):
        self.assertEqual(cb.stock_ones(self.installed(b"a", b"b"), self.wheel()), [])

    def test_a_missing_bootloader_stops_the_build(self):
        (self.root / "empty").mkdir()
        with self.assertRaises(SystemExit):
            cb.installed_hashes(self.root / "empty")


if __name__ == "__main__":
    unittest.main()
