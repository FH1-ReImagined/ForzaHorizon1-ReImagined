"""ReImagined loading tips: the data file, the tips.str rewrite, the hidden mod and the launch
arguments. Synthetic string tables only (no game files)."""
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import game, loading_tips, mods  # noqa: E402
from forza_launcher.config import Config  # noqa: E402


def lsb2(entries):
    """An LSB2 string table with [(id name, text)] in that order (the layout radio.patch_strings
    reads): a text section, an id section and two empty ones."""
    keys = list(range(100, 100 + len(entries)))
    text, offs = bytearray(), []
    for _, t in entries:
        offs.append(len(text) // 2)
        text += t.encode("utf-16-be") + b"\0\0"
    sec1 = struct.pack(">II", 0, len(entries))
    sec1 += b"".join(struct.pack(">HI", k, o) for k, o in zip(keys, offs))
    sec1 += struct.pack(">HI", 0xFFFF, len(text) // 2) + bytes(text)
    pool, ids = bytearray(), bytearray()
    for k, (name, _) in zip(keys, entries):
        ids += struct.pack(">HHI", k, 0, len(pool))
        pool += name.encode("ascii") + b"\0"
    sec2 = struct.pack(">II", 0, len(entries)) + bytes(ids) + bytes(pool)
    a = 36
    b = a + len(sec1)
    c = b + len(sec2)
    head = b"LSB2" + struct.pack(">3I", 0x01000000, 0x02000000, 36) + struct.pack(">I", 4)
    head += struct.pack(">4I", a, b, c, c + 8)
    return head + sec1 + sec2 + bytes(16)


TABLE = [("IDS_PulseTip", "If you like indie music..."), ("IDS_PulseTitle", "HORIZON PULSE"),
         ("IDS_KingTip", "Hold the crown."), ("IDS_KingTitle", "KING"), ("IDS_CarToken_Desc", "A token."),
         ("IDS_CarToken_Title", "TOKEN"), ("IDS_FastTravelTip2", "Fast travel to the Festival."),
         ("IDS_PhotoShareTip", "Share your photos."), ("IDS_PhotShareTitle", "PHOTO SHARING")]


def strings(data):
    """[(id, text)] back from an LSB2 table."""
    n = struct.unpack_from(">I", data, 16)[0]
    a, b = struct.unpack_from(f">{n}I", data, 20)[:2]
    count = struct.unpack_from(">I", data, a + 4)[0]
    pool = a + 8 + 6 * (count + 1)
    out = []
    names = loading_tips.string_ids(data)
    for i in range(count):
        _, off = struct.unpack_from(">HI", data, a + 8 + i * 6)
        pos = end = pool + off * 2
        while data[end:end + 2] != b"\0\0":
            end += 2
        out.append((names[i], data[pos:end].decode("utf-16-be")))
    return out


class DataFile(unittest.TestCase):
    def test_the_shipped_tips_fit(self):
        entries = loading_tips.load()
        self.assertEqual(len(entries), 12)
        self.assertEqual(entries[0][0], "SHARE YOUR SHOTS")
        self.assertEqual(len({t for t, _ in entries}), 12)  # a title of its own each
        self.assertTrue(all(len(t) <= loading_tips.MAX_TITLE and len(x) <= loading_tips.MAX_TIP
                            for t, x in entries))

    def test_limits(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "tips.json"
            for bad in ([{"title": "T", "tip": "x" * (loading_tips.MAX_TIP + 1)}],
                        [{"title": "T" * (loading_tips.MAX_TITLE + 1), "tip": "ok"}],
                        [{"title": "", "tip": "ok"}], [{"title": "T"}], ["just text"], []):
                p.write_text(json.dumps({"tips": bad}))
                with self.assertRaises(loading_tips.TipsError, msg=bad):
                    loading_tips.load(p)


DEFS = b"""<LoadingDefinitions>
  <LoadingGroup name="FreeRoam" tipcount="2" order="random">
    <Tip title="Tips:IDS_KingTitle" message="Tips:IDS_KingTip" displaytime="8" />
    <Tip title="Tips:IDS_PhotShareTitle" message="Tips:IDS_PhotoShareTip" displaytime="8" />
  </LoadingGroup>
  <LoadingGroup name="Race" tipcount="1" order="sequential">
    <Tip title="Tips:IDS_KingTitle" message="Tips:IDS_KingTip" displaytime="8" />
  </LoadingGroup>
</LoadingDefinitions>
"""
ENTRIES = [("ONE", "one"), ("TWO", "two"), ("THREE", "three")]


class Rewrite(unittest.TestCase):
    def test_the_slots_come_from_loadingdefs(self):
        self.assertEqual(loading_tips.slot_pairs(DEFS), [("IDS_KingTitle", "IDS_KingTip"),
                                                         ("IDS_PhotShareTitle", "IDS_PhotoShareTip")])
        self.assertEqual(loading_tips.slot_pairs(DEFS.decode().encode("utf-16")),
                         loading_tips.slot_pairs(DEFS))

    def test_each_title_with_its_tip(self):
        data = lsb2(TABLE)
        self.assertEqual(loading_tips.string_ids(data), [k for k, _ in TABLE])
        out = dict(strings(loading_tips.rewrite(data, ENTRIES, loading_tips.slot_pairs(DEFS))))
        # the loading screens' pairs first, even with names that differ (PhotShare/PhotoShare)
        self.assertEqual((out["IDS_KingTitle"], out["IDS_KingTip"]), ("ONE", "one"))
        self.assertEqual((out["IDS_PhotShareTitle"], out["IDS_PhotoShareTip"]), ("TWO", "two"))
        # then the others by name, the entries in turn
        self.assertEqual((out["IDS_PulseTitle"], out["IDS_PulseTip"]), ("THREE", "three"))
        self.assertEqual((out["IDS_CarToken_Title"], out["IDS_CarToken_Desc"]), ("ONE", "one"))
        self.assertEqual(out["IDS_FastTravelTip2"], "two")

    def test_without_loadingdefs_by_name(self):
        out = dict(strings(loading_tips.rewrite(lsb2(TABLE), ENTRIES)))
        self.assertEqual((out["IDS_PulseTitle"], out["IDS_PulseTip"]), ("ONE", "one"))
        self.assertEqual((out["IDS_KingTitle"], out["IDS_KingTip"]), ("TWO", "two"))

    def test_not_a_table(self):
        with self.assertRaises(loading_tips.TipsError):
            loading_tips.rewrite(b"NOPE" + bytes(40), ENTRIES)


class ModAndLaunch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.online["mode"] = "offline"
        g = t / "game" / "media" / "stringtables"
        g.mkdir(parents=True)
        for lang in ("EN", "FR"):
            with zipfile.ZipFile(g / f"{lang}.zip", "w") as z:
                z.writestr("Tips.str", lsb2(TABLE))
        (t / "game" / "media" / "ui").mkdir()
        (t / "game" / "media" / "ui" / "LoadingDefs.xml").write_bytes(DEFS)
        self.cfg.d["game_root"] = str(t / "game")
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)
        mods.mods_dir(self.cfg).mkdir(parents=True)

    def args(self, write=True):
        return game.build_plan(self.cfg, self.install, write=write, log=lambda _t: None).argv

    def test_on_by_default_first_in_the_order(self):
        p = Path(self.tmp.name) / "home" / "mods" / "Mine" / "media" / "x.xml"
        p.parent.mkdir(parents=True)
        p.write_text("x")
        argv = self.args()
        self.assertIn("--forza_mods=.reimagined-tips|Mine", argv)
        built = mods.mods_dir(self.cfg) / loading_tips.MOD / "media" / "stringtables"
        self.assertEqual(sorted(d.name for d in built.iterdir()), ["en", "fr"])
        entries = loading_tips.load()
        out = dict(strings((built / "en" / "tips.str").read_bytes()))
        self.assertEqual((out["IDS_KingTitle"], out["IDS_KingTip"]), entries[0])
        self.assertEqual((out["IDS_PhotShareTitle"], out["IDS_PhotoShareTip"]), entries[1])
        # the Mods page does not list it
        self.assertEqual([m.folder for m in mods.scan(mods.mods_dir(self.cfg))], ["Mine"])
        # built once: the second start reuses it
        stamp = (built.parent.parent / loading_tips.MARKER).stat().st_mtime_ns
        self.args()
        self.assertEqual((built.parent.parent / loading_tips.MARKER).stat().st_mtime_ns, stamp)

    def test_a_preview_names_the_mod_only_once_built(self):
        self.assertIn("--forza_mods=|", self.args(write=False))
        self.assertFalse((mods.mods_dir(self.cfg) / loading_tips.MOD).exists())
        self.args()
        self.assertIn("--forza_mods=.reimagined-tips", self.args(write=False))

    def test_off_gives_the_game_its_own_tips(self):
        self.cfg.settings[loading_tips.SETTING] = False
        argv = self.args()
        self.assertIn("--forza_mods=|", argv)
        self.assertFalse((mods.mods_dir(self.cfg) / loading_tips.MOD).exists())

    def test_the_setting_is_not_a_game_setting(self):
        from forza_launcher.config import render_toml
        self.cfg.settings[loading_tips.SETTING] = False
        self.assertNotIn("reimagined_loading_tips", render_toml(self.cfg))

    def test_a_broken_data_file_starts_with_the_game_tips(self):
        with mock.patch.object(loading_tips, "load", side_effect=loading_tips.TipsError("bad")):
            self.assertIn("--forza_mods=|", self.args())

    def test_no_game_tables_no_mod(self):
        for z in (Path(self.tmp.name) / "game" / "media" / "stringtables").glob("*.zip"):
            z.unlink()
        self.assertIn("--forza_mods=|", self.args())




class ToolsMissingTest(unittest.TestCase):
    """#758: the Flatpak shipped without tools/extract_map_points.py, so building the launch plan
    raised ModuleNotFoundError and Play and the shader prebuild stopped. A tips failure of any kind
    must leave the game's own tips instead."""

    def test_import_error_leaves_the_games_tips(self):
        from unittest import mock
        from forza_launcher import loading_tips
        logged = []
        cfg = mock.Mock()
        with mock.patch.object(loading_tips, "enabled", return_value=True), \
             mock.patch.object(loading_tips.mods, "mods_dir", return_value=Path("/nonexistent")), \
             mock.patch.object(loading_tips, "sources", side_effect=ModuleNotFoundError("No module named 'extract_map_points'")):
            self.assertIsNone(loading_tips.ensure_mod(cfg, logged.append))
        self.assertTrue(any("extract_map_points" in m for m in logged))

    def test_flatpak_stages_every_tool_the_frozen_build_bundles(self):
        import ast, re
        repo = Path(__file__).resolve().parent.parent
        stage = (repo / "flatpak" / "stage.py").read_text(encoding="utf-8")
        tools = ast.literal_eval(re.search(r"^TOOLS = (\(.*?\))", stage, re.S | re.M).group(1))
        spec = (repo / "launcher" / "forza-launcher.spec").read_text(encoding="utf-8")
        frozen = ast.literal_eval(re.search(r"for _tool in (\(.*?\)):", spec, re.S).group(1))
        self.assertEqual(sorted(set(frozen) - set(tools)), [])

if __name__ == "__main__":
    unittest.main()
