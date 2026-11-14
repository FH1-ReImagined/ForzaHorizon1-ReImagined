"""The Big Shot upload's texts name forza.dyotson.dev (#875): the carflow.str rewrite, the hidden
mod and the launch arguments. Synthetic string tables only (no game files)."""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import game, loading_tips, mods, upload_site  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
from test_loading_tips import TABLE as TIPS, lsb2, strings  # noqa: E402

# A carflow.str in small: the five Big Shot entries (the old name in both of the game's
# spellings), the movie upload's and the storefront's, which stay.
EN = [("IDS_Photo_Upload_Success", "Your photo was successfully uploaded to the Forza Horizon Storefront."),
      ("IDS_BigShot_Upload_Failure", "Your Big Shot photo was NOT successfully uploaded to the ForzaMotorsport.net "
                                     "server. Make sure you have a connection to Xbox LIVE, and try again later."),
      ("IDS_Photo_Upload_Confirm_Overwrite", "You have already uploaded a big shot photo to Forzamotorsport.net. "
                                             "Do you want to overwrite it?"),
      ("IDS_BigShot_Upload_Failure_Lsp", "The Forza Horizon service is not available. Please try again later."),
      ("IDS_Photo_ConfirmExport_Msg", "You are about to create a large photo and upload it to ForzaMotorsport.net.  "
                                      "This process can take a bit of time.  Are you sure you want to continue?"),
      ("IDS_Movie_Upload_Success", "Your movie was successfully uploaded to Forzamotorsport.net."),
      ("IDS_BigShot_Upload_Success", "Your Big Shot photo was successfully uploaded to the ForzaMotorsport.net "
                                     "server. You can retrieve it from there later."),
      ("IDS_Photo_BigShot_Msg", "This photo will be automatically uploaded to ForzaMotorsport.net where you can "
                                "access and download it.")]
DE = [("IDS_Photo_BigShot_Msg", "Dieses Foto wird automatisch bei ForzaMotorsport.net hochgeladen, wo Sie darauf "
                                "zugreifen und es herunterladen können."),
      ("IDS_BigShot_Upload_Success", "Ihre Großaufnahme wurde erfolgreich auf den ForzaMotorsport.net-Server "
                                     "hochgeladen."),
      ("IDS_Movie_Upload_Success", "Ihr Film wurde erfolgreich auf Forzamotorsport.net hochgeladen.")]
JP = [("IDS_Photo_BigShot_Msg", "このフォトは自動的に ForzaMotorsport.net "
                                "にアップロードされます")]
IDS = upload_site.ENTRIES["carflow"]


def loc():
    """The game's LOC placeholder: an LSB2 table without an id section."""
    data = bytearray(lsb2([("IDS_X", "CarFlow:IDS_X")]))
    data[24:28] = bytes(4)
    return bytes(data)


class Rewrite(unittest.TestCase):
    def test_only_the_site_name_in_only_those_entries(self):
        data = lsb2(EN)
        out = upload_site.rewrite(data, IDS)
        self.assertEqual([k for k, _ in strings(out)], [k for k, _ in EN])  # the ids and their order stay
        new, old = dict(strings(out)), dict(EN)
        changed = {k for k in old if new[k] != old[k]}
        self.assertEqual(changed, set(IDS))
        for k in IDS:
            self.assertNotIn("motorsport.net", new[k].lower())
            self.assertEqual(new[k].count("forza.dyotson.dev"), 1)
            # the rest of the text is the game's
            self.assertEqual(new[k].replace("forza.dyotson.dev", "@"),
                             upload_site.OLD.sub("@", old[k]))
        self.assertEqual(new["IDS_BigShot_Upload_Success"],
                         "Your Big Shot photo was successfully uploaded to the forza.dyotson.dev server. "
                         "You can retrieve it from there later.")
        self.assertEqual(new["IDS_Photo_Upload_Confirm_Overwrite"],
                         "You have already uploaded a big shot photo to forza.dyotson.dev. "
                         "Do you want to overwrite it?")
        # the movie upload's and the storefront's texts are not this flow's
        self.assertEqual(new["IDS_Movie_Upload_Success"], "Your movie was successfully uploaded to Forzamotorsport.net.")
        self.assertIn("Storefront", new["IDS_Photo_Upload_Success"])

    def test_a_table_round_trip(self):
        data = lsb2(EN)
        self.assertEqual(upload_site.texts(data), dict(EN))
        out = upload_site.rewrite(data, IDS)
        self.assertEqual(upload_site.texts(out), dict(strings(out)))
        self.assertEqual(loading_tips.string_ids(out), loading_tips.string_ids(data))
        # what follows the text section (the ids and the other sections) is the game's, byte for byte
        tail = data[data.index(b"IDS_Photo_Upload_Success") - 8 * len(EN) - 8:]
        self.assertTrue(out.endswith(tail))
        self.assertEqual(len(data) - len(out), 2 * len(IDS) * (len("ForzaMotorsport.net") - len("forza.dyotson.dev")))

    def test_every_language_keeps_its_own_wording(self):
        out = dict(strings(upload_site.rewrite(lsb2(DE), IDS)))
        self.assertEqual(out["IDS_Photo_BigShot_Msg"], "Dieses Foto wird automatisch bei forza.dyotson.dev "
                                                        "hochgeladen, wo Sie darauf zugreifen und es herunterladen können.")
        self.assertEqual(out["IDS_BigShot_Upload_Success"], "Ihre Großaufnahme wurde erfolgreich auf den "
                                                             "forza.dyotson.dev-Server hochgeladen.")
        self.assertEqual(out["IDS_Movie_Upload_Success"], dict(DE)["IDS_Movie_Upload_Success"])
        out = dict(strings(upload_site.rewrite(lsb2(JP), IDS)))
        self.assertEqual(out["IDS_Photo_BigShot_Msg"], dict(JP)["IDS_Photo_BigShot_Msg"]
                         .replace("ForzaMotorsport.net", "forza.dyotson.dev"))

    def test_nothing_to_change(self):
        self.assertIsNone(upload_site.rewrite(lsb2(TIPS), IDS))              # a table without the entries
        self.assertIsNone(upload_site.rewrite(lsb2([("IDS_Movie_Upload_Success", "to Forzamotorsport.net.")]), IDS))
        self.assertIsNone(upload_site.rewrite(upload_site.rewrite(lsb2(EN), IDS), IDS))  # already changed
        self.assertEqual(upload_site.texts(loc()), {})
        self.assertIsNone(upload_site.rewrite(loc(), IDS))                  # the LOC placeholder

    def test_not_a_table(self):
        for bad in (b"NOPE" + bytes(40), b"LSB2", lsb2(EN)[:60]):
            with self.assertRaises(upload_site.SiteError, msg=bad[:8]):
                upload_site.rewrite(bad, IDS)


class ModAndLaunch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.settings[loading_tips.SETTING] = False
        self.tables = t / "game" / "media" / "stringtables"
        self.tables.mkdir(parents=True)
        for lang, table in (("EN", EN), ("DE", DE)):
            with zipfile.ZipFile(self.tables / f"{lang}.zip", "w") as z:
                z.writestr("CarFlow.str", lsb2(table))
                z.writestr("Tips.str", lsb2(TIPS))
        with zipfile.ZipFile(self.tables / "LOC.zip", "w") as z:
            z.writestr("CarFlow.str", loc())
        self.cfg.d["game_root"] = str(t / "game")
        # the game Play starts: its own folder, so that no build of this checkout is looked at
        (t / "rt").mkdir()
        (t / "rt" / game.EXE).write_bytes(b"")
        self.cfg.advanced["game_dir"] = str(t / "rt")
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)
        mods.mods_dir(self.cfg).mkdir(parents=True)
        self.built = mods.mods_dir(self.cfg) / upload_site.MOD
        self.logged = []

    def args(self, write=True):
        return game.build_plan(self.cfg, self.install, write=write, log=self.logged.append).argv

    def mods_arg(self, write=True):
        return next(a for a in self.args(write) if a.startswith("--forza_mods=")).split("=", 1)[1]

    def table(self, lang):
        return dict(strings((self.built / "media" / "stringtables" / lang / "carflow.str").read_bytes()))

    def player_mod(self, name="Mine"):
        p = mods.mods_dir(self.cfg) / name / "media" / "stringtables" / "en" / "carflow.str"
        p.parent.mkdir(parents=True)
        p.write_bytes(lsb2([("IDS_Photo_BigShot_Msg", "My own text.")]))

    def test_online_by_default_and_a_players_mod_wins(self):
        self.assertEqual(self.cfg.online["mode"], "public")
        self.player_mod()
        # a later mod wins: the player's carflow.str is the one the game reads
        self.assertEqual(self.mods_arg(), ".reimagined-upload-site|Mine")
        stringtables = self.built / "media" / "stringtables"
        # only the tables that change, and only carflow.str (the LOC placeholder has nothing to change)
        self.assertEqual(sorted(p.relative_to(stringtables).as_posix() for p in stringtables.rglob("*") if p.is_file()),
                         ["de/carflow.str", "en/carflow.str"])
        self.assertEqual(self.table("en")["IDS_Photo_BigShot_Msg"],
                         "This photo will be automatically uploaded to forza.dyotson.dev where you can "
                         "access and download it.")
        self.assertIn("auf den forza.dyotson.dev-Server", self.table("de")["IDS_BigShot_Upload_Success"])
        self.assertIn("Forzamotorsport.net", self.table("en")["IDS_Movie_Upload_Success"])
        self.assertEqual(json.loads((self.built / upload_site.MARKER).read_text())["languages"], ["de", "en"])
        # the Mods page does not list it
        self.assertEqual([m.folder for m in mods.scan(mods.mods_dir(self.cfg))], ["Mine"])
        self.assertEqual(self.logged, [])

    def test_with_the_loading_tips_both_before_the_players_mods(self):
        self.cfg.settings[loading_tips.SETTING] = True
        self.player_mod()
        order = self.mods_arg().split("|")
        self.assertEqual(sorted(order[:2]), [".reimagined-tips", ".reimagined-upload-site"])
        self.assertEqual(order[2:], ["Mine"])
        self.assertTrue((mods.mods_dir(self.cfg) / loading_tips.MOD / "media/stringtables/en/tips.str").is_file())
        self.assertFalse((self.built / "media/stringtables/en/tips.str").exists())

    def test_built_once_and_again_when_the_game_tables_change(self):
        self.args()
        marker = self.built / upload_site.MARKER

        def aged():
            """The marker's time after setting it to a day in 2001: a rebuild writes a new marker."""
            os.utime(marker, (10**9, 10**9))
            return marker.stat().st_mtime_ns
        old = aged()
        with mock.patch.object(upload_site, "rewrite", side_effect=AssertionError("rebuilt")):
            self.assertEqual(self.mods_arg(), ".reimagined-upload-site")
        self.assertEqual(marker.stat().st_mtime_ns, old)
        self.assertEqual(self.logged, [])
        # another table of the game (another title update, another language): rebuilt
        with zipfile.ZipFile(self.tables / "JP.zip", "w") as z:
            z.writestr("CarFlow.str", lsb2(JP))
        self.assertEqual(self.mods_arg(), ".reimagined-upload-site")
        self.assertNotEqual(marker.stat().st_mtime_ns, old)
        self.assertIn("forza.dyotson.dev", self.table("jp")["IDS_Photo_BigShot_Msg"])
        # and when the entries or the site change
        old = aged()
        with mock.patch.object(upload_site, "SITE", "photos.example"):
            self.args()
            self.assertIn("uploaded to photos.example where", self.table("en")["IDS_Photo_BigShot_Msg"])
        self.assertNotEqual(marker.stat().st_mtime_ns, old)

    def test_the_title_updates_table_before_the_discs(self):
        update = Path(self.tmp.name) / "rt" / "update"
        update.mkdir()
        newer = [(k, "TU4: " + v) for k, v in EN]
        with zipfile.ZipFile(update / "media.zip", "w") as z:
            z.writestr("stringtables/en/carflow.str", lsb2(newer))
        self.args()
        self.assertEqual(self.table("en")["IDS_Photo_BigShot_Msg"],
                         "TU4: This photo will be automatically uploaded to forza.dyotson.dev where you can "
                         "access and download it.")
        self.assertFalse(self.table("de")["IDS_Photo_BigShot_Msg"].startswith("TU4"))  # the disc's, as the game

    def test_a_custom_server_too(self):
        self.cfg.online.update(mode="custom", custom_server="example.org:21200")
        self.assertEqual(self.mods_arg(), ".reimagined-upload-site")

    def test_this_pc_and_offline_keep_the_games_text(self):
        for online in ({"mode": "offline"}, {"mode": "local"}, {"mode": "custom", "custom_server": ""}):
            self.cfg.online.update(online)
            self.assertEqual(self.mods_arg(), mods.NONE, online)
            self.assertFalse(self.built.exists(), online)
        # a mod built while online is not loaded either
        self.cfg.online.update(mode="public")
        self.args()
        self.cfg.online.update(mode="offline")
        self.assertEqual(self.mods_arg(), mods.NONE)
        self.assertEqual(self.mods_arg(write=False), mods.NONE)

    def test_a_preview_names_the_mod_only_once_built(self):
        self.assertEqual(self.mods_arg(write=False), mods.NONE)
        self.assertFalse(self.built.exists())
        self.args()
        self.assertEqual(self.mods_arg(write=False), ".reimagined-upload-site")

    def test_no_table_names_the_old_site_no_mod(self):
        for z in self.tables.glob("*.zip"):
            z.unlink()
        self.assertEqual(self.mods_arg(), mods.NONE)       # no tables at all
        with zipfile.ZipFile(self.tables / "EN.zip", "w") as z:
            z.writestr("CarFlow.str", upload_site.rewrite(lsb2(EN), IDS))
        self.assertEqual(self.mods_arg(), mods.NONE)       # tables that name it no more
        self.assertFalse(self.built.exists())
        self.assertEqual(self.logged, [])

    def test_a_failure_starts_with_the_games_text(self):
        with mock.patch.object(upload_site, "sources", side_effect=ModuleNotFoundError("No module named 'x'")):
            self.assertEqual(self.mods_arg(), mods.NONE)
        self.assertTrue(any("Photo upload site name" in m and "No module named" in m for m in self.logged))
        with zipfile.ZipFile(self.tables / "EN.zip", "w") as z:
            z.writestr("CarFlow.str", lsb2(EN)[:60])       # a broken table
        self.assertEqual(self.mods_arg(), mods.NONE)
        self.assertFalse(self.built.exists())

    def test_a_folder_of_that_name_that_is_not_the_launchers_is_left_alone(self):
        (self.built / "media").mkdir(parents=True)
        (self.built / "media" / "mine.xml").write_text("x")
        self.assertEqual(self.mods_arg(), mods.NONE)
        self.assertTrue((self.built / "media" / "mine.xml").is_file())
        self.assertTrue(any("was not made by the launcher" in m for m in self.logged))


if __name__ == "__main__":
    unittest.main()
