"""Damaged DLC is named at Install DLC, at Play and in crash reports, also when it came as an extracted
folder (a Xenia content folder) that has no hash table (#86, #327, #422):
python3 -m unittest launcher/test_dlc_integrity.py"""
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import crash_report, dlccheck, game, installer  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
from test_xcontent import build as build_stfs  # noqa: E402

RALLY = "6F6992766050D818245ADD408031E280FB5F4E634D"
ZIP = "Media/DLCZips/ColoradoDirt_pri_65.zip"
GOOD = bytes(range(256)) * 64          # 16 KB: four 4 KB blocks
DLC = Path("0000000000000000") / "4D5309C9"
# The two ZIPREAD lines of the #422 log (a player's Windows game on the bad Rally copy).
ZIPREAD = [
    "[2026-10-25 12:43:50.892] [error] [core] [t4044] ZIPREAD: stream 2E97D8A0 (method 21, 32768 of 81792 "
    "bytes out, input 28426 of 28426 read, 0 left, block 2E444A80 state 4/4 at +D728A, pages 00000000) "
    "cannot go on; the read ends short (#86)",
    "[2026-10-25 12:43:50.892] [error] [core] [t4044] ZIPREAD: stream 2E97D8A0 input at BF84D12A, CRC-32 "
    "e4eba1e0, per 2 KB: 3726f23a 7ef3c8b5",
    "[2026-10-25 12:43:51.116] [critical] [core] [t30928] Unhandled read fault at host 0x1000000044 "
    "(pc forza.exe+0x174283A)",
]


def sha1(data):
    return hashlib.sha1(data).hexdigest()


class Fixture(unittest.TestCase):
    """A manifest for one package (the Rally pack's name, a synthetic 16 KB zip and a small file) with
    blocks 1-2 of the zip known to be damaged in a bad copy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        manifest = self.root / "dlc.json"
        manifest.write_text(json.dumps({
            "known_damage": {RALLY: {ZIP: [{"offset": 4096, "size": 8192, "sha1": sha1(GOOD[4096:12288]),
                                            "effect": "Kettle Hills stage 1 crashes", "zipread_crc": "e4eba1e0"}]}},
            "packages": {RALLY: {ZIP: [len(GOOD), sha1(GOOD)], "Media/1600.puboffer": [3, sha1(b"abc")]}},
        }))
        patcher = mock.patch.object(dlccheck, "MANIFEST", manifest)
        patcher.start()
        self.addCleanup(patcher.stop)

    def folder(self, where: Path, zip_bytes=GOOD, offer=True, name=RALLY):
        """An extracted package as Xenia leaves it: <where>/<name>/Media/..."""
        pkg = where / name
        (pkg / "Media" / "DLCZips").mkdir(parents=True)
        (pkg / ZIP).write_bytes(zip_bytes)
        if offer:
            (pkg / "Media" / "1600.puboffer").write_bytes(b"abc")
        return pkg


def damaged(data=GOOD):
    out = bytearray(data)
    out[5000:5010] = b"\xff" * 10       # inside the known damaged region (blocks 1-2)
    return bytes(out)


class Compare(Fixture):
    def test_intact_copy_passes(self):
        self.assertEqual(dlccheck.compare(RALLY, {ZIP: (len(GOOD), sha1(GOOD)),
                                                  "Media/1600.puboffer": (3, sha1(b"abc"))}), [])

    def test_changed_bytes_and_missing_files_are_named(self):
        bad = dlccheck.compare(RALLY.lower(), {ZIP.lower(): (len(GOOD), sha1(damaged()))})
        self.assertEqual(len(bad), 2)
        self.assertIn(ZIP, bad[0])
        self.assertIn("differs from an intact copy", bad[0])
        self.assertIn("1 file(s) are missing (Media/1600.puboffer)", bad[1])

    def test_unknown_package_is_not_checked(self):
        self.assertEqual(dlccheck.compare("DEADBEEF", {}), [])


class InstallFromFolder(Fixture):
    """Setup > DLC with a Xenia content folder: every copied file is hashed while it is copied."""

    def install(self, source):
        logs, bad = [], []
        n = installer.install_dlc_folder(str(source), self.root / "data", lambda *_: None, log=logs.append,
                                         damaged=bad)
        return n, logs, bad

    def test_damaged_xenia_copy_installs_with_a_named_warning(self):
        source = self.root / "content" / "0000000000000000" / "4D5309C9" / "00000002"
        self.folder(source, damaged())
        n, logs, bad = self.install(self.root / "content")
        self.assertEqual(n, 1)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][0], "Rally Expansion Pack")
        self.assertIn(ZIP, bad[0][1])
        self.assertTrue(any(line.startswith("WARNING: " + RALLY) and ZIP in line for line in logs), logs)
        note = installer.damaged_note(bad)
        self.assertIn("Damaged: Rally Expansion Pack", note)
        self.assertIn("intact copy of the package file", note)

    def test_partial_copy_is_reported(self):
        self.folder(self.root / "src", GOOD[:4096], offer=False)
        _n, _logs, bad = self.install(self.root / "src")
        self.assertEqual([p for _, p in bad][0], f"{ZIP} differs from an intact copy")
        self.assertIn("missing (Media/1600.puboffer)", bad[1][1])

    def test_intact_copy_installs_quietly(self):
        self.folder(self.root / "src")
        n, logs, bad = self.install(self.root / "src")
        self.assertEqual((n, bad), (1, []))
        self.assertFalse(any("WARNING" in line for line in logs))
        installed = self.root / "data" / DLC / "00000002" / RALLY / ZIP
        self.assertEqual(installed.read_bytes(), GOOD)
        self.assertEqual(installer.damaged_note([]), "")

    def test_package_file_with_failing_blocks_is_reported_too(self):
        data = bytearray(build_stfs(GOOD, damage_block=1))
        struct.pack_into(">I", data, 0x344, installer.install_dlc.MARKETPLACE)
        struct.pack_into(">I", data, 0x360, installer.install_dlc.TITLE_ID)
        src = self.root / "pkgs"
        src.mkdir()
        (src / RALLY).write_bytes(data)
        _n, logs, bad = self.install(src)
        self.assertEqual(len(bad), 1, logs)
        self.assertIn("damaged 4 KB block", bad[0][1])


class AtPlay(Fixture):
    def test_known_bad_copy_already_installed_is_found(self):
        self.folder(self.root / DLC / "00000002", damaged())
        problems = dlccheck.installed_problems(self.root)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], RALLY)
        self.assertIn(ZIP, problems[0][1])
        self.assertEqual(problems[0][2], "Kettle Hills stage 1 crashes")

    def test_missing_or_short_files_are_found(self):
        self.folder(self.root / DLC / "00000002", GOOD[:8000], offer=False)
        problems = dlccheck.installed_problems(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("are missing or incomplete", problems[0][1])

    def test_intact_and_unknown_packages_pass(self):
        self.folder(self.root / DLC / "00000002")
        self.folder(self.root / DLC / "00000002", damaged(), name="DEADBEEF")
        self.assertEqual(dlccheck.installed_problems(self.root), [])
        self.assertEqual(dlccheck.installed_problems(self.root / "nothing-here"), [])

    def test_play_warns(self):
        cfg = Config(self.root / "launcher.json")
        home = self.root / "home"
        cfg.advanced["data_root"] = str(home)
        (home / "runtime" / "update").mkdir(parents=True)
        self.folder(home / DLC / "00000002", damaged())
        header = home / DLC / "Headers" / "00000002" / (RALLY + ".header")
        header.parent.mkdir(parents=True)
        header.write_bytes(b"h" * 0x344)
        install = game.GameInstall(home / "runtime", home / "runtime" / game.EXE)
        notes = game.content_warnings(cfg, install, home, home)
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("Your Rally Expansion Pack is damaged", notes[0])
        self.assertIn("(Kettle Hills stage 1 crashes)", notes[0])
        self.assertIn("intact copy of its package file", notes[0])


class CrashReport(Fixture):
    def test_known_bad_stream_names_the_pack(self):
        text = dlccheck.short_read_summary(["noise"] + ZIPREAD)
        self.assertIn("32768 of 81792 bytes", text)
        self.assertIn("known damaged copy of the Rally Expansion Pack", text)
        self.assertIn(ZIP, text)

    def test_other_short_read_is_still_explained(self):
        text = dlccheck.short_read_summary([ZIPREAD[0]])
        self.assertIn("damaged game or DLC file", text)
        self.assertNotIn("Rally", text)

    def test_no_short_read_no_section(self):
        self.assertIsNone(dlccheck.short_read_summary([ZIPREAD[2]]))

    def test_report_has_the_section(self):
        plan = mock.Mock()
        with mock.patch.object(crash_report.game, "log_path_of", return_value=self.root / "forza.log"), \
                mock.patch.object(crash_report.game, "log_is_current", return_value=True), \
                mock.patch.object(crash_report.game, "read_log_tail", return_value=ZIPREAD), \
                mock.patch.object(crash_report.game, "game_version", return_value="test"):
            (self.root / "forza.log").write_text("\n".join(ZIPREAD))
            report = crash_report.collect(plan, 134, {
                "private": (), "pc": {"cpu": "c", "ram": "r", "vram": "v"}, "gpu": "g", "settings": {},
                "online": "offline", "profile": "xenia", "packaging": "Source"})
        self.assertIn("## Damaged game file", report.body)
        self.assertIn("Rally Expansion Pack", report.body)


class ShippedList(unittest.TestCase):
    """data/dlc-4D5309C9.json as shipped (tools/dlc_manifest.py)."""

    def test_rally_pack_and_its_known_bad_copy(self):
        files, damage = dlccheck._load(str(Path(dlccheck.__file__).parent / "data" / "dlc-4D5309C9.json"))
        self.assertIn(RALLY, files)
        _path, size, digest = files[RALLY][ZIP.lower()]
        self.assertEqual(size, 484262732)
        self.assertRegex(digest, "^[0-9a-f]{40}$")
        region = damage[RALLY][ZIP][0]
        # the 12 blocks a bad copy has wrong: file blocks 22339-22350 (#86)
        self.assertEqual((region["offset"], region["size"]), (22339 * 4096, 12 * 4096))
        self.assertEqual(region["zipread_crc"], "e4eba1e0")
        for name, entries in files.items():
            self.assertIn(name, installer.install_dlc.KNOWN_NAMES)
            for _key, (_p, sz, sha) in entries.items():
                self.assertGreaterEqual(sz, 0)
                self.assertRegex(sha, "^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
