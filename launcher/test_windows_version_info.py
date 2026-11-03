"""The Windows version resource of forza-launcher.exe and the installer, and the CI wiring that keeps
Windows Defender's machine-learning guesses (#482, #354) away: a bootloader compiled from source, a
version resource, and the Linux job left alone. Run: python3 -m unittest launcher/test_windows_version_info.py"""
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGING = HERE / "packaging"
RELEASE_YML = HERE.parent / ".github" / "workflows" / "release.yml"


def load(name):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wvi = load("windows_version_info")


class Recorder:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def parse(text):
    """Evaluate a version file the way PyInstaller does, with recording stand-ins for its classes."""
    names = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo",
             "VarStruct")
    info = eval(text, {name: type(name, (Recorder,), {}) for name in names})
    table = info.kwargs["kids"][0].args[0][0]
    strings = {row.args[0]: row.args[1] for row in table.args[1]}
    return info, table, strings


class NumericVersion(unittest.TestCase):
    def test_release_prerelease_and_development_versions(self):
        self.assertEqual(wvi.numeric_version("0.2.0"), (0, 2, 0, 0))
        self.assertEqual(wvi.numeric_version("v1.2.3"), (1, 2, 3, 0))
        self.assertEqual(wvi.numeric_version("0.3.0-rc.1"), (0, 3, 0, 0))
        self.assertEqual(wvi.numeric_version("0.0.0-dev.abc1234"), (0, 0, 0, 0))
        self.assertEqual(wvi.numeric_version("1.2.3.4"), (1, 2, 3, 4))
        self.assertEqual(wvi.numeric_version("2"), (2, 0, 0, 0))
        self.assertEqual(wvi.dotted_version("0.2.0"), "0.2.0.0")

    def test_junk_and_out_of_range_are_refused(self):
        for bad in ("", "dev", "abc.1.2", "70000.0.0"):
            with self.assertRaises(ValueError, msg=bad):
                wvi.numeric_version(bad)

    def test_command_line_prints_the_four_part_number(self):
        out = subprocess.run([sys.executable, str(PACKAGING / "windows_version_info.py"), "0.0.0-dev.abc1234"],
                             check=True, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "0.0.0.0")
        out = subprocess.run([sys.executable, str(PACKAGING / "windows_version_info.py"), "0.2.0"],
                             check=True, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "0.2.0.0")


class VersionFile(unittest.TestCase):
    def test_names_the_program_and_its_version(self):
        info, table, strings = parse(wvi.version_file_text("0.2.0"))
        self.assertEqual(info.kwargs["ffi"].kwargs["filevers"], (0, 2, 0, 0))
        self.assertEqual(info.kwargs["ffi"].kwargs["prodvers"], (0, 2, 0, 0))
        self.assertEqual(table.args[0], "040904B0")
        self.assertEqual(strings, {
            "CompanyName": "Dyotson",
            "FileDescription": "Forza Horizon 1: ReImagined launcher",
            "FileVersion": "0.2.0.0",
            "InternalName": "forza-launcher",
            "LegalCopyright": "Copyright (c) 2026 Dyotson and Forza Horizon 1: ReImagined contributors",
            "OriginalFilename": "forza-launcher.exe",
            "ProductName": "Forza Horizon 1: ReImagined",
            "ProductVersion": "0.2.0",
        })

    def test_a_development_build_keeps_its_tag_in_the_product_version(self):
        _, _, strings = parse(wvi.version_file_text("0.0.0-dev.abc1234"))
        self.assertEqual(strings["ProductVersion"], "0.0.0-dev.abc1234")
        self.assertEqual(strings["FileVersion"], "0.0.0.0")

    def test_pyinstaller_reads_it_when_it_can_be_imported(self):
        try:
            from PyInstaller.utils.win32 import versioninfo
        except Exception as exc:  # PyInstaller missing, or its Windows-only imports (pefile, pywin32-ctypes)
            self.skipTest(f"PyInstaller's version resource code is not importable here: {exc}")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "version.txt"
            path.write_text(wvi.version_file_text("0.2.0"), encoding="utf-8", newline="\n")
            info = versioninfo.load_version_info_from_text_file(str(path))
        self.assertTrue(info.toRaw())


class Installer(unittest.TestCase):
    def setUp(self):
        self.script = (PACKAGING / "installer.iss").read_text(encoding="utf-8")

    def define(self, name):
        return re.search(rf'^\s*#define {name} "([^"]*)"', self.script, re.M).group(1)

    def test_publisher_and_copyright_match_the_launcher_resource(self):
        self.assertEqual(self.define("Publisher"), wvi.COMPANY)
        self.assertEqual(self.define("LegalCopyright"), wvi.COPYRIGHT)
        self.assertEqual(self.define("AppName"), wvi.PRODUCT)
        self.assertIn("AppPublisher={#Publisher}", self.script)

    def test_installer_has_its_own_version_resource(self):
        for line in ("VersionInfoVersion={#VersionNumeric}", "VersionInfoProductTextVersion={#AppVersion}",
                     "VersionInfoCompany={#Publisher}", "VersionInfoProductName={#AppName}",
                     "VersionInfoDescription={#AppName} Setup", "VersionInfoCopyright={#LegalCopyright}",
                     "VersionInfoOriginalFileName=ForzaHorizon1-ReImagined-Setup.exe"):
            self.assertIn(line, self.script.splitlines())
        self.assertRegex(self.define("VersionNumeric"), r"^\d+\.\d+\.\d+\.\d+$")

    def test_installer_original_name_fits_innos_resource_slot(self):
        # Inno copies into a 50-character OriginalFileName slot. The actual CI
        # development download name is 52 characters and lost the final "xe".
        original = re.search(r'^VersionInfoOriginalFileName=(.+)$', self.script, re.M).group(1)
        self.assertNotIn('{#AppVersion}', original)
        self.assertLessEqual(len(original), 50)
        check = (PACKAGING / "test_installer.ps1").read_text(encoding="utf-8")
        self.assertIn(f"$siName -eq '{original}'", check)
        self.assertIn("OutputBaseFilename=ForzaHorizon1-ReImagined-Setup-{#AppVersion}", self.script)


class Workflow(unittest.TestCase):
    def job(self, name, following):
        text = RELEASE_YML.read_text(encoding="utf-8")
        start = text.index(f"\n  {name}:\n")
        return text[start:text.index(f"\n  {following}:\n", start)]

    def test_windows_job_builds_the_bootloader_and_checks_it_before_building_the_launcher(self):
        job = self.job("launcher-windows", "installer-test")
        for needle in ('PYINSTALLER_COMPILE_BOOTLOADER: "1"', "pip install --no-binary pyinstaller -r launcher/requirements-build.txt",
                       "launcher/packaging/check_bootloader.py", "windows_version_info.py", "/DVersionNumeric=$numeric"):
            self.assertIn(needle, job)
        self.assertLess(job.index("check_bootloader.py"), job.index("pyinstaller --noconfirm forza-launcher.spec"))
        self.assertIn("TODO(code signing", job)

    def test_linux_job_is_unchanged_by_it(self):
        job = self.job("launcher-linux", "launcher-windows")
        for needle in ("PYINSTALLER_COMPILE_BOOTLOADER", "no-binary", "check_bootloader", "windows_version_info"):
            self.assertNotIn(needle, job)
        self.assertIn("pip install -r launcher/requirements.txt -r launcher/requirements-build.txt", job)


if __name__ == "__main__":
    unittest.main()
