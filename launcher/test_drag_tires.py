"""Drag tires for every car (#924): the setting, the hidden mod and the launch arguments. The builder
(tools/drag_tires.py, tested in tools/) is replaced by a fake: no game files."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import drag_tires, mods  # noqa: E402
from forza_launcher.config import Config, render_toml  # noqa: E402


class DragTireModTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        mods.mods_dir(self.cfg).mkdir(parents=True)
        self.builds = []
        data = t / "drag_tire.json"
        data.write_text("{}", encoding="utf-8")
        code = t / "drag_tires.py"
        code.write_text("# builder", encoding="utf-8")
        self.fm4db = t / "gamedb.slt"

        def build(fh1, out, extra=(), data=None):
            self.builds.append(list(extra))
            p = Path(out) / "media" / "db" / "patch" / "924006_merge.slt"
            p.parent.mkdir(parents=True)
            p.write_bytes(b"patch")
            return {"cars": 3, "languages": ["en"], "keys": {}}

        fake_fh1 = mock.MagicMock()
        self.tools = types.SimpleNamespace(DATA=str(data), __file__=str(code), build=build,
                                           fm4_cars=types.SimpleNamespace(FH1=lambda *a: fake_fh1))
        patches = [mock.patch.object(drag_tires, "_tools", lambda: self.tools),
                   mock.patch.object(drag_tires, "sources",
                                     lambda cfg: (t / "game", None, None, self.fm4db if self.fm4db.exists() else None))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def args(self, write=True, mods_arg="--forza_mods=Mine"):
        args = ["--game", mods_arg]
        drag_tires.launch_args(self.cfg, args, write, log=lambda _t: None)
        return args

    def test_off_by_default_no_mod(self):
        self.assertFalse(drag_tires.enabled(self.cfg))
        self.assertEqual(self.args(), ["--game", "--forza_mods=Mine"])
        self.assertFalse((mods.mods_dir(self.cfg) / drag_tires.MOD).exists())

    def test_the_setting_is_kept_and_is_not_a_game_setting(self):
        drag_tires.set_enabled(self.cfg, True)
        self.assertTrue(drag_tires.enabled(Config(self.cfg.path)))  # saved
        self.assertNotIn("drag", render_toml(self.cfg))

    def test_on_first_in_the_order_built_once(self):
        drag_tires.set_enabled(self.cfg, True)
        self.assertEqual(self.args(), ["--game", "--forza_mods=.reimagined-drag-tires|Mine"])
        target = mods.mods_dir(self.cfg) / drag_tires.MOD
        self.assertTrue((target / "media" / "db" / "patch" / "924006_merge.slt").is_file())
        self.assertIn('name = "Drag tires"', (target / "mod.toml").read_text(encoding="utf-8"))
        self.assertEqual(drag_tires.status(self.cfg)["cars"], 3)
        self.assertEqual([m.folder for m in mods.scan(mods.mods_dir(self.cfg))], [])  # the Mods page does not list it
        self.args()
        self.assertEqual(len(self.builds), 1)  # the second start reuses it

    def test_an_fm4_import_rebuilds_it_with_its_cars(self):
        drag_tires.set_enabled(self.cfg, True)
        self.args()
        self.fm4db.write_bytes(b"cars")  # an FM4 cars import made since
        self.args()
        self.assertEqual(self.builds, [[], [str(self.fm4db)]])

    def test_with_no_other_mod(self):
        drag_tires.set_enabled(self.cfg, True)
        self.assertEqual(self.args(mods_arg="--forza_mods=" + mods.NONE), ["--game", "--forza_mods=.reimagined-drag-tires"])

    def test_a_preview_names_it_only_once_built(self):
        drag_tires.set_enabled(self.cfg, True)
        self.assertEqual(self.args(write=False), ["--game", "--forza_mods=Mine"])
        self.args()
        self.assertEqual(self.args(write=False), ["--game", "--forza_mods=.reimagined-drag-tires|Mine"])

    def test_a_failure_starts_the_game_without_them(self):
        drag_tires.set_enabled(self.cfg, True)
        logged = []

        def broken(*a, **k):
            raise RuntimeError("no upgrades.str in the game's files")
        self.tools.build = broken
        args = ["--forza_mods=Mine"]
        drag_tires.launch_args(self.cfg, args, True, log=logged.append)
        self.assertEqual(args, ["--forza_mods=Mine"])
        self.assertTrue(any("upgrades.str" in m for m in logged))
        self.assertFalse((mods.mods_dir(self.cfg) / drag_tires.MOD).exists())


if __name__ == "__main__":
    unittest.main()
