"""Import FM4 cars (#648): publishing, removal and the disc checks, with a stand-in converter
and synthetic files (no game file is read)."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import fm4cars, mods  # noqa: E402
from forza_launcher.config import Config  # noqa: E402


def xex(title):
    """A minimal XEX2 header with the execution info (title id) xexcheck.header_info reads."""
    head = bytearray(0x100)
    head[:4] = b"XEX2"
    struct.pack_into(">I", head, 0x14, 1)
    struct.pack_into(">II", head, 0x18, 0x40006, 0x40)
    struct.pack_into(">IIII", head, 0x40, 0x26FF1101, 8, 8, title)
    return bytes(head)


class FakeConverter:
    runs = []

    def __init__(self, fm4, fh1, out, package=None):
        self.out = Path(out)
        self.package = Path(package)

    def candidates(self):
        return [(1119, "BUG_Veyron_09", None), (1500, "XYZ_Thing_10", "its make is not in FH1")]

    def run(self, ids, progress=None, cancelled=lambda: False):
        FakeConverter.runs.append(list(ids))
        (self.out / "media/cars/BUG_Veyron_09").mkdir(parents=True)
        (self.out / "media/cars/BUG_Veyron_09/carattribs.xml").write_text("<CarAttributes/>")
        (self.out / "mod.toml").write_text('name = "FM4 cars"\n')
        (self.package / "Media/DLCZips").mkdir(parents=True)
        (self.package / "Media/DLCZips/fm4cars1_pri_60.zip").write_bytes(b"PK")
        header = self.package.parent.parent / "Headers/00000002" / (self.package.name + ".header")
        header.parent.mkdir(parents=True)
        header.write_bytes(b"h")
        return {"cars": [(1119, "BUG_Veyron_09", 8, 872)], "skipped": [(1500, "XYZ_Thing_10: no model")]}


class _Setup:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.disc1 = t / "fm4"
        (self.disc1 / "Media/db").mkdir(parents=True)
        (self.disc1 / "default.xex").write_bytes(xex(fm4cars.FM4_TITLE))
        (self.disc1 / "Media/db/gamedb.slt").write_bytes(b"")
        game = t / "game/media"
        game.mkdir(parents=True)
        self.cfg.d.setdefault("setup", {})
        FakeConverter.runs = []
        self.patches = [patch.object(fm4cars, "_update_root", lambda cfg: None),
                        patch.object(Config, "game_files", lambda self_: t / "game")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def build(self, **kw):
        return fm4cars.build(self.cfg, self.disc1, None, converter=FakeConverter, **kw)


class Fm4ImportTests(_Setup, unittest.TestCase):

    def test_publishes_the_mod_enabled_last_and_removes_it(self):
        msg = self.build()
        self.assertIn("Added 1", msg)
        self.assertIn("1 could not be converted", msg)
        self.assertEqual(FakeConverter.runs, [[1119]])
        target = mods.mods_dir(self.cfg) / fm4cars.MOD
        self.assertTrue((target / "media/cars/BUG_Veyron_09/carattribs.xml").is_file())
        self.assertEqual(fm4cars.status(self.cfg)["cars"], ["BUG_Veyron_09"])
        self.assertEqual(self.cfg.d["mods"]["order"][-1], fm4cars.MOD)
        self.assertNotIn(fm4cars.MOD, self.cfg.d["mods"]["disabled"])
        self.assertFalse(fm4cars.stale(self.cfg))
        # nothing left behind in the mods folder but the mod
        self.assertEqual([p.name for p in mods.mods_dir(self.cfg).iterdir()], [fm4cars.MOD])
        package, header = fm4cars._package(self.cfg)
        self.assertTrue((package / "Media/DLCZips/fm4cars1_pri_60.zip").is_file())
        self.assertTrue(header.is_file())
        self.assertFalse(fm4cars.stale(self.cfg))  # its own package is not DLC
        fm4cars.remove(self.cfg)
        self.assertFalse(target.exists())
        self.assertFalse(package.exists())
        self.assertFalse(header.exists())
        self.assertIsNone(fm4cars.status(self.cfg))

    def test_a_second_import_replaces_the_first(self):
        self.build()
        self.build()
        self.assertEqual(len(FakeConverter.runs), 2)
        self.assertEqual([p.name for p in mods.mods_dir(self.cfg).iterdir()], [fm4cars.MOD])
        package, _ = fm4cars._package(self.cfg)
        self.assertEqual([p.name for p in package.parent.iterdir()], [package.name])

    def test_refuses_another_disc(self):
        (self.disc1 / "default.xex").write_bytes(xex(0x4D5309C9))
        with self.assertRaises(fm4cars.Fm4Error):
            self.build()
        (self.disc1 / "default.xex").unlink()
        with self.assertRaises(fm4cars.Fm4Error):
            self.build()

    def test_never_takes_over_a_players_folder(self):
        own = mods.mods_dir(self.cfg) / fm4cars.MOD
        own.mkdir(parents=True)
        (own / "mine.txt").write_text("keep")
        with self.assertRaises(fm4cars.Fm4Error):
            self.build()
        self.assertEqual((own / "mine.txt").read_text(), "keep")

    def test_cancel_keeps_the_previous_import(self):
        self.build()
        from forza_launcher.fetch import Cancelled

        class Cancelling(FakeConverter):
            def run(self, ids, progress=None, cancelled=lambda: False):
                import fm4_cars
                raise fm4_cars.Cancelled()
        with self.assertRaises(Cancelled):
            fm4cars.build(self.cfg, self.disc1, None, converter=Cancelling)
        self.assertEqual(fm4cars.status(self.cfg)["cars"], ["BUG_Veyron_09"])

    def test_package_name_matches_the_tool(self):
        self.assertEqual(fm4cars.PACKAGE, fm4cars._tools().PACK_NAME)

    def test_stale_after_dlc_changes(self):
        self.build()
        d = Path(self.cfg.data_home()) / "0000000000000000/4D5309C9/00000002/PACK"
        d.mkdir(parents=True)
        self.assertTrue(fm4cars.stale(self.cfg))


try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Fm4CardTests(_Setup, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_card_reports_and_removing_the_mod_removes_the_package(self):
        from forza_launcher.ui.mods_page import ModsPage
        self.build()
        page = ModsPage(self.cfg, window=None)
        try:
            self.assertIn("1 FM4 car(s) imported", page.fm4_message.text())
            self.assertTrue(page.fm4_remove.isEnabled())
            page.remove_folder(fm4cars.MOD)
            package, header = fm4cars._package(self.cfg)
            self.assertFalse(package.exists())
            self.assertFalse(header.exists())
            self.assertFalse(page.fm4_remove.isEnabled())
        finally:
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
