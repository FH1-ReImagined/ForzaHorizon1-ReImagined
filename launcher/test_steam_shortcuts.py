"""Temporary Steam fixtures only: no client, account, or real config access."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import steam_shortcuts as steam

EMPTY = b"\0shortcuts\0\x08\x08"
CMD = steam.Command('"/home/player/Games/Forza Launcher"', '"/home/player/Games"')
ART = {".png": b"banner", "_icon.png": b"logo"}


def row(index="7", name="A different game", appid=17, extra=b""):
    return (b"\0" + index.encode() + b"\0" + steam._integer("appid", appid)
            + steam._string("AppName", name) + steam._string("Exe", '"/games/other"')
            + extra + b"\x08")


class BinaryFixtures(unittest.TestCase):
    def test_preserves_unknown_fields_non_utf8_and_top_level_data(self):
        original_row = row(extra=b"\1unknown\0\xff\xfe\0\0nested\0\7value\0" + b"z" * 8 + b"\x08")
        original = b"\0shortcuts\0" + original_row + b"\x08\1unknown-root\0unchanged\0\x08"
        result = steam.append_shortcut(original, CMD, "/icon.png")
        self.assertTrue(result.startswith(b"\0shortcuts\0" + original_row))
        self.assertTrue(result.endswith(b"\x08\1unknown-root\0unchanged\0\x08"))
        roots = steam.parse(result)
        added = roots[0].value[1]
        self.assertEqual(added.key, b"0")
        self.assertEqual(steam._field(added.value, b"appid", 2), struct.pack("<I", CMD.appid))
        self.assertEqual(steam._field(added.value, b"exe", 1), CMD.exe.encode())

    def test_duplicate_name_or_renamed_matching_command_does_not_overwrite(self):
        result = steam.append_shortcut(EMPTY, CMD, "/icon.png")
        self.assertIsNone(steam.append_shortcut(result, CMD, "/new-icon.png"))
        renamed = result.replace(steam.NAME.encode(), b"My custom title")
        self.assertIsNone(steam.append_shortcut(renamed, CMD, "/icon.png"))
        changed_command = result.replace(CMD.exe.encode(), b'"/my/old/install"')
        self.assertIsNone(steam.append_shortcut(changed_command, CMD, "/icon.png"))

    def test_colliding_id_and_ambiguous_records_refused(self):
        cases = [row(appid=CMD.appid), row() + row(), row(extra=steam._integer("AppName", 1)),
                 row(extra=steam._string("appname", "duplicate"))]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(steam.SteamError):
                steam.append_shortcut(b"\0shortcuts\0" + data + b"\x08\x08", CMD, "icon")

    def test_invalid_or_unsupported_input_refused(self):
        for data in (b"", b"\0shortcuts", EMPTY[:-1], EMPTY + b"extra", b"\0shortcuts\0\2x\0\1",
                     b"\0shortcuts\0\xffx\0\x08\x08", b"\x08", EMPTY.replace(b"shortcuts", b"other"),
                     b"\0x\0" * 40 + b"\x08" * 41):
            with self.subTest(data=data), self.assertRaises(steam.SteamError):
                steam.append_shortcut(data, CMD, "icon")

    def test_flatpak_command_and_special_path(self):
        with patch.object(steam.paths, "IN_FLATPAK", True):
            with self.assertRaisesRegex(steam.SteamError, "Flatpak"):
                steam.launcher_command()
        for value in ('a"b', "a\nb", "a$b", "a`b", "a\\b"):
            with self.assertRaises(steam.SteamError):
                steam._quote(value)


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.account = steam.Account(self.home / ".local/share/Steam", "123")
        self.account.config.mkdir(parents=True)
        self.dest = self.account.config / "shortcuts.vdf"
        self.original = b"\0shortcuts\0" + row() + b"\x08\x08"
        self.dest.write_bytes(self.original)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, **kwargs):
        return steam.add(self.account, CMD, ART, closed_check=kwargs.pop("closed_check", lambda: None), **kwargs)

    def test_success_backup_preservation_and_idempotence(self):
        grid = self.account.config / "grid"
        grid.mkdir()
        art = grid / f"{CMD.appid}.png"
        art.write_bytes(b"player artwork")
        self.assertIn("Added to Steam", self.add())
        backups = list(self.account.config.glob("shortcuts.vdf.forza-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertEqual(art.read_bytes(), b"player artwork")
        self.assertEqual((grid / f"{CMD.appid}_icon.png").read_bytes(), b"logo")
        after = self.dest.read_bytes()
        self.assertIn("already exists", self.add())
        self.assertEqual(self.dest.read_bytes(), after)
        self.assertEqual(len(list(self.account.config.glob("shortcuts.vdf.forza-backup-*"))), 1)
        self.assertFalse(list(self.account.config.glob(".forza-shortcut*")))

    def test_first_shortcut_and_selected_account_only(self):
        self.dest.unlink()
        other = self.account.root / "userdata/456/config"
        other.mkdir(parents=True)
        (other / "shortcuts.vdf").write_bytes(self.original)
        self.assertEqual(len(steam.accounts(self.home)), 2)
        self.add()
        self.assertEqual(len(steam.parse(self.dest.read_bytes())[0].value), 1)
        self.assertEqual((other / "shortcuts.vdf").read_bytes(), self.original)
        self.assertFalse(list(self.account.config.glob("shortcuts.vdf.forza-backup-*")))

    def test_atomic_replace_failure_rolls_back_new_art(self):
        with patch.object(steam.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaises(OSError):
                self.add()
        self.assertEqual(self.dest.read_bytes(), self.original)
        self.assertFalse(list((self.account.config / "grid").iterdir()))
        self.assertFalse(list(self.account.config.glob(".forza-shortcut*")))
        self.assertEqual(next(self.account.config.glob("shortcuts.vdf.forza-backup-*")).read_bytes(), self.original)

    def test_backup_failure_never_commits(self):
        with patch.object(steam.os, "link", side_effect=OSError("injected backup failure")):
            with self.assertRaises(OSError):
                self.add()
        self.assertEqual(self.dest.read_bytes(), self.original)
        self.assertFalse((self.account.config / "grid").exists())

    def test_running_steam_before_commit_rolls_back_art(self):
        count = 0
        def check():
            nonlocal count
            count += 1
            if count == 2:
                raise steam.SteamError("Steam started")
        with self.assertRaisesRegex(steam.SteamError, "Steam started"):
            self.add(closed_check=check)
        self.assertEqual(self.dest.read_bytes(), self.original)
        self.assertFalse(list((self.account.config / "grid").iterdir()))

    def test_external_write_wins(self):
        count = 0
        def check():
            nonlocal count
            count += 1
            if count == 2:
                self.dest.write_bytes(b"external update")
        with self.assertRaisesRegex(steam.SteamError, "changed during"):
            self.add(closed_check=check)
        self.assertEqual(self.dest.read_bytes(), b"external update")

    def test_existing_lock_and_malformed_file_untouched(self):
        lock = self.account.config / ".forza-shortcut.lock"
        lock.write_bytes(b"other owner")
        with self.assertRaises(steam.SteamError):
            self.add()
        self.assertEqual(lock.read_bytes(), b"other owner")
        lock.unlink()
        self.dest.write_bytes(b"broken")
        with self.assertRaises(steam.SteamError):
            self.add()
        self.assertEqual(self.dest.read_bytes(), b"broken")

    def test_linked_shortcuts_refused(self):
        linked = self.account.config / "linked"
        os.link(self.dest, linked)
        with self.assertRaises(steam.SteamError):
            self.add()
        self.assertEqual(linked.read_bytes(), self.original)

    def test_art_failure_after_one_publication_rolls_it_back(self):
        real_link = steam.os.link
        count = 0
        def link(source, target):
            nonlocal count
            count += 1
            if count == 3:  # backup and first art succeeded
                raise OSError("injected second art failure")
            return real_link(source, target)
        with patch.object(steam.os, "link", side_effect=link), self.assertRaises(OSError):
            self.add()
        self.assertEqual(self.dest.read_bytes(), self.original)
        self.assertFalse(list((self.account.config / "grid").iterdir()))
        self.assertFalse(list(self.account.config.glob(".forza-shortcut*")))

    def test_stage_flush_failure_keeps_original(self):
        with patch.object(steam.os, "fsync", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.add()
        self.assertEqual(self.dest.read_bytes(), self.original)
        self.assertFalse(list(self.account.config.glob(".forza-shortcut*")))

    def test_flatpak_steam_and_running_client_never_start_transaction(self):
        flatpak = steam.Account(self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam", "123")
        with self.assertRaisesRegex(steam.SteamError, "Flatpak Steam"):
            steam.add(flatpak, CMD, ART, closed_check=lambda: None)
        def running():
            raise steam.SteamError("Steam is running")
        with self.assertRaisesRegex(steam.SteamError, "running"):
            self.add(closed_check=running)
        self.assertEqual(list(self.account.config.iterdir()), [self.dest])
        self.assertFalse(flatpak.config.exists())

    def test_alias_accounts_are_not_duplicated_and_config_symlink_is_refused(self):
        alias = self.home / ".steam"
        alias.mkdir()
        try:
            (alias / "steam").symlink_to(self.account.root, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlinks unavailable")
        self.assertEqual(steam.accounts(self.home), [self.account])
        other = self.account.root / "userdata/789"
        other.mkdir()
        (other / "config").symlink_to(self.account.config, target_is_directory=True)
        with self.assertRaises(steam.SteamError):
            steam.add(steam.Account(self.account.root, "789"), CMD, ART, closed_check=lambda: None)
        self.assertEqual(self.dest.read_bytes(), self.original)


class ProcessChecks(unittest.TestCase):
    def test_native_exit_codes_and_timeout(self):
        for code in (0, 1, 2, 3, 127):
            result = subprocess.CompletedProcess([], code, b"", b"")
            with patch.object(steam.subprocess, "run", return_value=result):
                if code == 1:
                    steam.require_closed(False)
                else:
                    with self.assertRaises(steam.SteamError):
                        steam.require_closed(False)
        with patch.object(steam.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 5)):
            with self.assertRaises(steam.SteamError):
                steam.require_closed(False)

    def test_flatpak_fails_closed_without_trying_host_commands(self):
        with patch.object(steam.subprocess, "run") as run:
            with self.assertRaisesRegex(steam.SteamError, "Flatpak"):
                steam.require_closed(True)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
