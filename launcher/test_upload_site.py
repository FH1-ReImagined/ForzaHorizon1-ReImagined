"""The photo and movie upload texts name forza.dyotson.dev (#875): the carflow.str and main.str
rewrite, the hidden mod and the launch arguments. Synthetic string tables only (no game files)."""
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

SITE = "forza.dyotson.dev"
# A carflow.str in small: the Big Shot's five entries and the movie upload's four (the old name
# in both of the game's spellings), and the storefront's and the service's, which stay.
CARFLOW = [
    ("IDS_Photo_Upload_Success", "Your photo was successfully uploaded to the Forza Horizon Storefront."),
    ("IDS_BigShot_Upload_Failure", "Your Big Shot photo was NOT successfully uploaded to the ForzaMotorsport.net "
                                   "server. Make sure you have a connection to Xbox LIVE, and try again later."),
    ("IDS_Photo_Upload_Confirm_Overwrite", "You have already uploaded a big shot photo to Forzamotorsport.net. "
                                           "Do you want to overwrite it?"),
    ("IDS_Movie_Upload_Failure", "Your movie was NOT successfully uploaded to Forzamotorsport.net."),
    ("IDS_BigShot_Upload_Failure_Lsp", "The Forza Horizon service is not available. Please try again later."),
    ("IDS_Photo_ConfirmExport_Msg", "You are about to create a large photo and upload it to ForzaMotorsport.net.  "
                                    "This process can take a bit of time.  Are you sure you want to continue?"),
    ("IDS_Movie_Upload_Success", "Your movie was successfully uploaded to Forzamotorsport.net."),
    ("IDS_Movie_Upload_Confirm", "Would you like to share this movie with others by uploading it to "
                                 "Forzamotorsport.net?"),
    ("IDS_Movie_Upload_Title", "UPLOAD MOVIE"),
    ("IDS_Movie_Upload_Confirm_Overwrite", "You have already uploaded a movie or big shot to Forzamotorsport.net. "
                                           "Do you want to overwrite it?"),
    ("IDS_BigShot_Upload_Success", "Your Big Shot photo was successfully uploaded to the ForzaMotorsport.net "
                                   "server. You can retrieve it from there later."),
    ("IDS_Photo_BigShot_Msg", "This photo will be automatically uploaded to ForzaMotorsport.net where you can "
                              "access and download it.")]
# A main.str in small: the movie list's two entries, and IDS_InstallBestResults3, which names the
# old site too but is about news, not an upload: it must stay.
MAIN = [
    ("IDS_LoadSave_WatchMovie_Msg", "In order to watch your movies, you must first upload them to "
                                    "ForzaMotorsport.net. This requires an Xbox LIVE Gold Membership."),
    ("IDS_LoadSave_Upload", "UPLOAD TO FORZA HORIZON STOREFRONT"),
    ("IDS_InstallBestResults3", "Visit ForzaMotorsport.net for all the latest Forza Horizon news and "
                                "community events."),
    ("IDS_LoadSave_WatchMovie_Title", "HOW TO WATCH MOVIES"),
    ("IDS_LoadSave_Upload_to_Server", "Upload to ForzaMotorsport.net")]
CARFLOW_DE = [
    ("IDS_Photo_BigShot_Msg", "Dieses Foto wird automatisch bei ForzaMotorsport.net hochgeladen, wo Sie darauf "
                              "zugreifen und es herunterladen können."),
    ("IDS_BigShot_Upload_Success", "Ihre Großaufnahme wurde erfolgreich auf den ForzaMotorsport.net-Server "
                                   "hochgeladen."),
    ("IDS_Movie_Upload_Success", "Ihr Film wurde erfolgreich auf Forzamotorsport.net hochgeladen."),
    ("IDS_Livery_Upload_Success", "Ihr Design wurde erfolgreich in den Forza Horizon-Shop hochgeladen.")]
MAIN_DE = [
    ("IDS_LoadSave_Upload_to_Server", "Auf ForzaMotorsport.net hochladen"),
    ("IDS_InstallBestResults3", "Auf ForzaMotorsport.net finden Sie alle Neuigkeiten zu Forza Horizon.")]
# The upload action in capitals (Hungarian), and with the game's own slip in the name (French).
MAIN_HU = [("IDS_LoadSave_Upload_to_Server", "FELTÖLTÉS A FORZAMOTORSPORT.NET WEBHELYRE"),
           ("IDS_LoadSave_WatchMovie_Msg", "A filmeket a megtekintés előtt fel kell tölteni a ForzaMotorsport.net "
                                           "webhelyre.")]
MAIN_FR = [("IDS_LoadSave_Upload_to_Server", "ENVOYER À FORZAMOTOSPORT.NET"),
           ("IDS_LoadSave_WatchMovie_Msg", "Pour regarder vos films, commencez par les envoyer à "
                                           "ForzaMotorsport.net. Abonnement Xbox LIVE Gold requis.")]
CARFLOW_JP = [("IDS_Photo_BigShot_Msg", "このフォトは自動的に ForzaMotorsport.net にアップロードされます"),
              ("IDS_Movie_Upload_Success", "Forzamotorsport.net にムービーをアップロードしました")]
TABLES = {"carflow": CARFLOW, "main": MAIN}
IDS = upload_site.ENTRIES


def rewritten(table, entries):
    return dict(strings(upload_site.rewrite(lsb2(entries), IDS[table])))


def loc():
    """The game's LOC placeholder: an LSB2 table without an id section."""
    data = bytearray(lsb2([("IDS_X", "CarFlow:IDS_X")]))
    data[24:28] = bytes(4)
    return bytes(data)


class Entries(unittest.TestCase):
    def test_the_big_shots_the_movies_and_the_movie_lists(self):
        self.assertEqual(sorted(IDS), ["carflow", "main"])
        self.assertEqual(len(IDS["carflow"]), 9)
        self.assertEqual(len([i for i in IDS["carflow"] if "Movie" in i]), 4)
        self.assertEqual(sorted(IDS["main"]), ["IDS_LoadSave_Upload_to_Server", "IDS_LoadSave_WatchMovie_Msg"])
        # the fixtures hold every entry, so the tests below see each of them change
        for table, entries in TABLES.items():
            self.assertLessEqual(set(IDS[table]), {k for k, _ in entries}, table)


class Rewrite(unittest.TestCase):
    def test_only_the_site_name_in_only_those_entries(self):
        for table, entries in TABLES.items():
            with self.subTest(table):
                out = upload_site.rewrite(lsb2(entries), IDS[table])
                self.assertEqual([k for k, _ in strings(out)], [k for k, _ in entries])  # the ids and their order stay
                new, old = dict(strings(out)), dict(entries)
                self.assertEqual({k for k in old if new[k] != old[k]}, set(IDS[table]))
                for k in IDS[table]:
                    self.assertNotIn("motorsport.net", new[k].lower())
                    self.assertEqual(new[k].count(SITE), 1)
                    # the rest of the text is the game's
                    self.assertEqual(new[k].replace(SITE, "@"), upload_site.OLD.sub("@", old[k]))

    def test_the_texts(self):
        new = rewritten("carflow", CARFLOW)
        self.assertEqual(new["IDS_BigShot_Upload_Success"],
                         "Your Big Shot photo was successfully uploaded to the forza.dyotson.dev server. "
                         "You can retrieve it from there later.")
        self.assertEqual(new["IDS_Photo_Upload_Confirm_Overwrite"],
                         "You have already uploaded a big shot photo to forza.dyotson.dev. "
                         "Do you want to overwrite it?")
        self.assertEqual(new["IDS_Movie_Upload_Confirm"],
                         "Would you like to share this movie with others by uploading it to forza.dyotson.dev?")
        self.assertEqual(new["IDS_Movie_Upload_Confirm_Overwrite"],
                         "You have already uploaded a movie or big shot to forza.dyotson.dev. "
                         "Do you want to overwrite it?")
        self.assertEqual(new["IDS_Movie_Upload_Success"], "Your movie was successfully uploaded to forza.dyotson.dev.")
        self.assertEqual(new["IDS_Movie_Upload_Failure"],
                         "Your movie was NOT successfully uploaded to forza.dyotson.dev.")
        # the storefront's and the service's texts name no site and stay
        for k in ("IDS_Photo_Upload_Success", "IDS_BigShot_Upload_Failure_Lsp", "IDS_Movie_Upload_Title"):
            self.assertEqual(new[k], dict(CARFLOW)[k])
        new = rewritten("main", MAIN)
        self.assertEqual(new["IDS_LoadSave_Upload_to_Server"], "Upload to forza.dyotson.dev")
        self.assertEqual(new["IDS_LoadSave_WatchMovie_Msg"],
                         "In order to watch your movies, you must first upload them to forza.dyotson.dev. "
                         "This requires an Xbox LIVE Gold Membership.")
        # an entry that names the old site but is not about an upload stays as it is
        self.assertEqual(new["IDS_InstallBestResults3"], dict(MAIN)["IDS_InstallBestResults3"])
        self.assertIn("ForzaMotorsport.net", new["IDS_InstallBestResults3"])
        self.assertEqual(new["IDS_LoadSave_Upload"], "UPLOAD TO FORZA HORIZON STOREFRONT")

    def test_a_table_round_trip(self):
        for table, entries in TABLES.items():
            with self.subTest(table):
                data = lsb2(entries)
                self.assertEqual(upload_site.texts(data), dict(entries))
                out = upload_site.rewrite(data, IDS[table])
                self.assertEqual(upload_site.texts(out), dict(strings(out)))
                self.assertEqual(loading_tips.string_ids(out), loading_tips.string_ids(data))
                # what follows the text section (the ids and the other sections) is the game's, byte for byte
                tail = data[data.index(entries[0][0].encode()) - 8 * len(entries) - 8:]
                self.assertTrue(out.endswith(tail))
                self.assertEqual(len(data) - len(out),
                                 2 * len(IDS[table]) * (len("ForzaMotorsport.net") - len(SITE)))

    def test_every_language_keeps_its_own_wording(self):
        out = rewritten("carflow", CARFLOW_DE)
        self.assertEqual(out["IDS_Photo_BigShot_Msg"], "Dieses Foto wird automatisch bei forza.dyotson.dev "
                                                        "hochgeladen, wo Sie darauf zugreifen und es herunterladen können.")
        self.assertEqual(out["IDS_BigShot_Upload_Success"], "Ihre Großaufnahme wurde erfolgreich auf den "
                                                             "forza.dyotson.dev-Server hochgeladen.")
        self.assertEqual(out["IDS_Movie_Upload_Success"], "Ihr Film wurde erfolgreich auf forza.dyotson.dev hochgeladen.")
        self.assertEqual(out["IDS_Livery_Upload_Success"], dict(CARFLOW_DE)["IDS_Livery_Upload_Success"])
        out = rewritten("carflow", CARFLOW_JP)
        for k, text in CARFLOW_JP:
            self.assertEqual(out[k], upload_site.OLD.sub(SITE, text))
        out = rewritten("main", MAIN_DE)
        self.assertEqual(out["IDS_LoadSave_Upload_to_Server"], "Auf forza.dyotson.dev hochladen")
        self.assertEqual(out["IDS_InstallBestResults3"], dict(MAIN_DE)["IDS_InstallBestResults3"])

    def test_in_capitals_where_the_old_name_was_and_the_french_slip(self):
        out = rewritten("main", MAIN_HU)
        self.assertEqual(out["IDS_LoadSave_Upload_to_Server"], "FELTÖLTÉS A FORZA.DYOTSON.DEV WEBHELYRE")
        self.assertEqual(out["IDS_LoadSave_WatchMovie_Msg"],
                         "A filmeket a megtekintés előtt fel kell tölteni a forza.dyotson.dev webhelyre.")
        out = rewritten("main", MAIN_FR)
        self.assertEqual(out["IDS_LoadSave_Upload_to_Server"], "ENVOYER À FORZA.DYOTSON.DEV")
        self.assertEqual(out["IDS_LoadSave_WatchMovie_Msg"], "Pour regarder vos films, commencez par les envoyer à "
                                                              "forza.dyotson.dev. Abonnement Xbox LIVE Gold requis.")

    def test_nothing_to_change(self):
        for table in IDS:
            with self.subTest(table):
                self.assertIsNone(upload_site.rewrite(lsb2(TIPS), IDS[table]))      # a table without the entries
                done = upload_site.rewrite(lsb2(TABLES[table]), IDS[table])
                self.assertIsNone(upload_site.rewrite(done, IDS[table]))            # already changed
                self.assertIsNone(upload_site.rewrite(loc(), IDS[table]))           # the LOC placeholder
        # the old name only in entries that are not about an upload
        self.assertIsNone(upload_site.rewrite(lsb2([("IDS_InstallBestResults3", "Visit ForzaMotorsport.net.")]),
                                              IDS["main"]))
        self.assertEqual(upload_site.texts(loc()), {})

    def test_not_a_table(self):
        for bad in (b"NOPE" + bytes(40), b"LSB2", lsb2(CARFLOW)[:60]):
            with self.assertRaises(upload_site.SiteError, msg=bad[:8]):
                upload_site.rewrite(bad, IDS["carflow"])


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
        for lang, carflow, main in (("EN", CARFLOW, MAIN), ("DE", CARFLOW_DE, MAIN_DE)):
            with zipfile.ZipFile(self.tables / f"{lang}.zip", "w") as z:
                z.writestr("CarFlow.str", lsb2(carflow))
                z.writestr("Main.str", lsb2(main))
                z.writestr("Tips.str", lsb2(TIPS))
        with zipfile.ZipFile(self.tables / "LOC.zip", "w") as z:
            z.writestr("CarFlow.str", loc())
            z.writestr("Main.str", loc())
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

    def table(self, lang, table="carflow"):
        return dict(strings((self.built / "media" / "stringtables" / lang / f"{table}.str").read_bytes()))

    def files(self):
        root = self.built / "media" / "stringtables"
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

    def player_mod(self, name, table, entry):
        p = mods.mods_dir(self.cfg) / name / "media" / "stringtables" / "en" / f"{table}.str"
        p.parent.mkdir(parents=True)
        p.write_bytes(lsb2([entry]))

    def aged(self):
        """The marker's time after setting it to a day in 2001: a rebuild writes a new marker."""
        marker = self.built / upload_site.MARKER
        os.utime(marker, (10**9, 10**9))
        return marker.stat().st_mtime_ns

    def test_online_by_default_and_a_players_mods_win(self):
        self.assertEqual(self.cfg.online["mode"], "public")
        self.player_mod("Mine", "carflow", ("IDS_Photo_BigShot_Msg", "My own text."))
        self.player_mod("Titles", "main", ("IDS_LoadSave_Upload_to_Server", "My own label"))
        # a later mod wins: the player's carflow.str and main.str are the ones the game reads
        self.assertEqual(self.mods_arg(), ".reimagined-upload-site|Mine|Titles")
        # only the tables that change, both of each language (the LOC placeholder has nothing to change)
        self.assertEqual(self.files(), ["de/carflow.str", "de/main.str", "en/carflow.str", "en/main.str"])
        self.assertEqual(self.table("en")["IDS_Photo_BigShot_Msg"],
                         "This photo will be automatically uploaded to forza.dyotson.dev where you can "
                         "access and download it.")
        self.assertEqual(self.table("en")["IDS_Movie_Upload_Success"],
                         "Your movie was successfully uploaded to forza.dyotson.dev.")
        self.assertIn("auf den forza.dyotson.dev-Server", self.table("de")["IDS_BigShot_Upload_Success"])
        self.assertEqual(self.table("en", "main")["IDS_LoadSave_Upload_to_Server"], "Upload to forza.dyotson.dev")
        self.assertEqual(self.table("de", "main")["IDS_LoadSave_Upload_to_Server"], "Auf forza.dyotson.dev hochladen")
        # the entry that is not about an upload keeps the game's text, in the mod's copy too
        self.assertIn("ForzaMotorsport.net", self.table("en", "main")["IDS_InstallBestResults3"])
        self.assertIn("Storefront", self.table("en")["IDS_Photo_Upload_Success"])
        marker = json.loads((self.built / upload_site.MARKER).read_text())
        self.assertEqual((marker["languages"], marker["tables"]), (["de", "en"], 4))
        # the Mods page does not list it
        self.assertEqual([m.folder for m in mods.scan(mods.mods_dir(self.cfg))], ["Mine", "Titles"])
        self.assertEqual(self.logged, [])

    def test_with_the_loading_tips_both_before_the_players_mods(self):
        self.cfg.settings[loading_tips.SETTING] = True
        self.player_mod("Mine", "main", ("IDS_LoadSave_Upload_to_Server", "My own label"))
        order = self.mods_arg().split("|")
        self.assertEqual(sorted(order[:2]), [".reimagined-tips", ".reimagined-upload-site"])
        self.assertEqual(order[2:], ["Mine"])
        self.assertTrue((mods.mods_dir(self.cfg) / loading_tips.MOD / "media/stringtables/en/tips.str").is_file())
        self.assertNotIn("en/tips.str", self.files())

    def test_built_once_and_again_when_the_game_tables_change(self):
        self.args()
        marker = self.built / upload_site.MARKER
        old = self.aged()
        with mock.patch.object(upload_site, "rewrite", side_effect=AssertionError("rebuilt")):
            self.assertEqual(self.mods_arg(), ".reimagined-upload-site")
        self.assertEqual(marker.stat().st_mtime_ns, old)
        self.assertEqual(self.logged, [])
        # another table of the game (another language): rebuilt, with the tables it has
        with zipfile.ZipFile(self.tables / "JP.zip", "w") as z:
            z.writestr("CarFlow.str", lsb2(CARFLOW_JP))
        self.assertEqual(self.mods_arg(), ".reimagined-upload-site")
        self.assertNotEqual(marker.stat().st_mtime_ns, old)
        self.assertIn(SITE, self.table("jp")["IDS_Movie_Upload_Success"])
        self.assertEqual(self.files(), ["de/carflow.str", "de/main.str", "en/carflow.str", "en/main.str",
                                        "jp/carflow.str"])
        # a main.str that changed (another title update)
        old = self.aged()
        with zipfile.ZipFile(self.tables / "DE.zip", "w") as z:
            z.writestr("CarFlow.str", lsb2(CARFLOW_DE))
            z.writestr("Main.str", lsb2(MAIN_DE + [("IDS_New", "Neu")]))
        self.args()
        self.assertNotEqual(marker.stat().st_mtime_ns, old)
        self.assertEqual(self.table("de", "main")["IDS_New"], "Neu")
        # and when the entries or the site change
        old = self.aged()
        with mock.patch.object(upload_site, "SITE", "photos.example"):
            self.args()
            self.assertIn("uploaded to photos.example where", self.table("en")["IDS_Photo_BigShot_Msg"])
            self.assertEqual(self.table("en", "main")["IDS_LoadSave_Upload_to_Server"], "Upload to photos.example")
        self.assertNotEqual(marker.stat().st_mtime_ns, old)

    def test_the_title_updates_tables_before_the_discs(self):
        update = Path(self.tmp.name) / "rt" / "update"
        update.mkdir()
        with zipfile.ZipFile(update / "media.zip", "w") as z:
            z.writestr("stringtables/en/carflow.str", lsb2([(k, "TU4: " + v) for k, v in CARFLOW]))
            z.writestr("stringtables/en/main.str", lsb2([(k, "TU4: " + v) for k, v in MAIN]))
        self.args()
        self.assertEqual(self.table("en")["IDS_Photo_BigShot_Msg"],
                         "TU4: This photo will be automatically uploaded to forza.dyotson.dev where you can "
                         "access and download it.")
        self.assertEqual(self.table("en", "main")["IDS_LoadSave_Upload_to_Server"], "TU4: Upload to forza.dyotson.dev")
        # a language the title update does not have: the disc's, as the game
        self.assertFalse(self.table("de")["IDS_Photo_BigShot_Msg"].startswith("TU4"))
        self.assertFalse(self.table("de", "main")["IDS_LoadSave_Upload_to_Server"].startswith("TU4"))

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
            z.writestr("CarFlow.str", upload_site.rewrite(lsb2(CARFLOW), IDS["carflow"]))
            z.writestr("Main.str", upload_site.rewrite(lsb2(MAIN), IDS["main"]))
        self.assertEqual(self.mods_arg(), mods.NONE)       # tables that name it no more where it matters
        self.assertFalse(self.built.exists())
        self.assertEqual(self.logged, [])

    def test_one_table_of_the_two(self):
        for z in self.tables.glob("*.zip"):
            z.unlink()
        with zipfile.ZipFile(self.tables / "EN.zip", "w") as z:
            z.writestr("Main.str", lsb2(MAIN))             # no CarFlow.str in this archive
        self.assertEqual(self.mods_arg(), ".reimagined-upload-site")
        self.assertEqual(self.files(), ["en/main.str"])
        self.assertEqual(self.logged, [])

    def test_a_failure_starts_with_the_games_text(self):
        with mock.patch.object(upload_site, "sources", side_effect=ModuleNotFoundError("No module named 'x'")):
            self.assertEqual(self.mods_arg(), mods.NONE)
        self.assertTrue(any("Upload site name" in m and "No module named" in m for m in self.logged))
        with zipfile.ZipFile(self.tables / "EN.zip", "w") as z:
            z.writestr("CarFlow.str", lsb2(CARFLOW))
            z.writestr("Main.str", lsb2(MAIN)[:60])        # a broken table
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
