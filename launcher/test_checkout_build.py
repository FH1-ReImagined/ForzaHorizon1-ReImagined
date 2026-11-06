"""A launcher run from a source checkout plays the checkout's own build (forza-recomp/out/build/$FORZA_BUILD):
Setup shows that build in use instead of "Not built", and Prebuild shaders does not ask for a rebuild.
The DLC installed in the data folder reach a game whose files are an extracted folder elsewhere."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import builder, datafolder as df, game, paths, shader_prebuild  # noqa: E402
from forza_launcher.config import Config  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

RALLY = "D5A1E8E7F8C1A1E0D0C0B0A090807060504D5309C9"


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def install_dlc(root: Path, name=RALLY):
    touch(root / df.DLC_DIR / "00000002" / name / "media" / "dlczips" / "a.zip")
    touch(root / df.DLC_DIR / "Headers" / "00000002" / (name + ".header"), b"h" * 0x344)


class CheckoutBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.repo = t / "repo"
        self.home = t / "data"
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.home)
        env = {k: v for k, v in os.environ.items() if k not in ("FORZA_BUILD", "FORZA_GAME_DIR")}
        for p in (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(paths, "REPO_ROOT", self.repo),
                  mock.patch.object(paths, "IS_WINDOWS", False),
                  mock.patch.object(paths, "in_checkout", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def checkout_build(self, name="linux"):
        d = self.repo / "forza-recomp" / "out" / "build" / name
        touch(d / game.EXE)
        return d


class CheckoutBuildDir(CheckoutBase):
    def checkout_dir(self):
        return [d for d, why, _sdk in game.candidate_dirs(self.cfg) if why == game.CHECKOUT_BUILD][0]

    def test_forza_build_names_the_folder(self):
        os.environ["FORZA_BUILD"] = "linux-tu4"
        self.assertEqual(self.checkout_dir(), self.repo / "forza-recomp/out/build/linux-tu4")

    def test_unset_empty_or_blank_forza_build_is_linux(self):
        self.assertEqual(self.checkout_dir(), self.repo / "forza-recomp/out/build/linux")
        for value in ("", "  "):
            os.environ["FORZA_BUILD"] = value
            self.assertEqual(self.checkout_dir(), self.repo / "forza-recomp/out/build/linux")

    def test_found_with_an_empty_forza_build(self):
        d = self.checkout_build("linux")
        os.environ["FORZA_BUILD"] = ""
        install, _looked = game.find_game(self.cfg)
        self.assertEqual((install.dir, install.source), (d, game.CHECKOUT_BUILD))

    def test_frozen_or_installed_launcher_has_no_checkout_build(self):
        self.checkout_build()
        with mock.patch.object(paths, "in_checkout", return_value=False):
            self.assertNotIn(game.CHECKOUT_BUILD, [why for _d, why, _s in game.candidate_dirs(self.cfg)])
            self.assertIsNone(game.outside_build(self.cfg))


class OutsideBuild(CheckoutBase):
    def test_checkout_build_is_in_use(self):
        os.environ["FORZA_BUILD"] = "linux-tu4"
        d = self.checkout_build("linux-tu4")
        install = game.outside_build(self.cfg)
        self.assertEqual(install.dir, d)
        text = game.outside_build_text(install)
        self.assertTrue(text.startswith(f"Using the build in this checkout ({d})"), text)

    def test_launchers_own_build_comes_first(self):
        self.checkout_build()
        touch(builder.runtime_dir(self.home) / game.EXE)
        self.assertIsNone(game.outside_build(self.cfg))

    def test_advanced_game_dir_is_in_use(self):
        d = Path(self.tmp.name) / "mine"
        touch(d / game.EXE)
        self.cfg.advanced["game_dir"] = str(d)
        install = game.outside_build(self.cfg)
        self.assertEqual((install.dir, install.source), (d, game.SET_IN_ADVANCED))
        self.assertIn("Advanced settings", game.outside_build_text(install))

    def test_nothing_built(self):
        self.assertIsNone(game.outside_build(self.cfg))


class PrebuildGuard(CheckoutBase):
    def test_checkout_build_is_not_sent_back_to_rebuild(self):
        self.checkout_build()
        with mock.patch.object(builder, "build_status", return_value=("none", "x")):
            self.assertEqual(shader_prebuild.build_problem(self.cfg), "")

    def test_advanced_game_dir_is_not_sent_back_to_rebuild(self):
        d = Path(self.tmp.name) / "mine"
        touch(d / game.EXE)
        self.cfg.advanced["game_dir"] = str(d)
        self.assertEqual(shader_prebuild.build_problem(self.cfg), "")

    def test_launchers_own_out_of_date_build_still_is(self):
        self.checkout_build()
        touch(builder.runtime_dir(self.home) / game.EXE)
        with mock.patch.object(builder, "build_status", return_value=("stale", "x")):
            self.assertEqual(shader_prebuild.build_problem(self.cfg), shader_prebuild.REBUILD_FIRST)

    def test_no_build_at_all_still_is(self):
        with mock.patch.object(builder, "build_status", return_value=("none", "x")):
            self.assertEqual(shader_prebuild.build_problem(self.cfg), shader_prebuild.REBUILD_FIRST)


@unittest.skipIf(sys.platform == "win32", "Linux: the game's default user data folder")
class ExtractedGameFolderDlc(unittest.TestCase):
    """game_root is an extracted folder outside the data folder; the DLC are installed in the data folder.
    The game reads DLC from its user data root, never from the game files' folder: the launcher's data
    folder is that root (the game's own default on Linux, XDG_DATA_HOME/forza, or --user_data_root)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.online["mode"] = "offline"
        self.extracted = t / "checkout" / "extracted" / "game"
        touch(self.extracted / "default.xex")
        self.cfg["game_root"] = str(self.extracted)
        self.build = t / "build"
        touch(self.build / game.EXE)
        self.install = game.GameInstall(self.build, self.build / game.EXE, source=game.CHECKOUT_BUILD)
        p = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(t / "xdg")})
        p.start()
        self.addCleanup(p.stop)
        self.home = self.cfg.data_home()
        install_dlc(self.home)

    def dlc_notes(self, plan):
        return [n for n in plan.notes if "DLC" in n]

    def test_own_profile_game_default_root_is_the_data_folder(self):
        self.assertEqual(self.home, Path(self.tmp.name) / "xdg" / "forza")  # the SDK's GetUserFolder()/forza
        plan = game.build_plan(self.cfg, self.install, write=False, log=lambda _t: None)
        self.assertIn(f"--game_data_root={self.extracted}", plan.argv)
        self.assertFalse(any(a.startswith("--user_data_root") for a in plan.argv), plan.argv)
        self.assertEqual(self.dlc_notes(plan), [])

    def test_own_profile_with_a_data_root_passes_it(self):
        other = Path(self.tmp.name) / "elsewhere"
        self.cfg.advanced["data_root"] = str(other)
        install_dlc(other)
        plan = game.build_plan(self.cfg, self.install, write=False, log=lambda _t: None)
        self.assertIn(f"--user_data_root={other}", plan.argv)
        self.assertEqual(self.dlc_notes(plan), [])

    def test_xenia_profile_links_the_data_folders_dlc(self):
        xenia = Path(self.tmp.name) / "xenia" / "content"
        touch(xenia / "E03000000FB1630A" / "4D5309C9" / "00000001" / "save")
        self.cfg.d["profile"] = {"mode": "xenia", "xenia_content": str(xenia), "xenia_xuid": ""}
        plan = game.build_plan(self.cfg, self.install, write=True, log=lambda _t: None)
        root = self.home / "xenia-E03000000FB1630A"
        self.assertIn(f"--user_data_root={root}", plan.argv)
        self.assertEqual(df.dlc_packages(root), {RALLY})
        self.assertEqual(self.dlc_notes(plan), [])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class SetupBuildCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher import build_env
        from forza_launcher.ui.window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.loaded = True
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        self.cfg.d["check_updates"] = False
        self.build = self.root / "repo" / "forza-recomp" / "out" / "build" / "linux-tu4"
        self.install = game.GameInstall(self.build, self.build / game.EXE, source=game.CHECKOUT_BUILD)
        self.found = (None, [])
        self.patches = [mock.patch("forza_launcher.paths.config_dir", return_value=self.root),
                        mock.patch("forza_launcher.game.find_game", side_effect=lambda _cfg: self.found),
                        mock.patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools()),
                        mock.patch("forza_launcher.builder.build_status",
                                   return_value=("none", "The game has not been built yet."))]
        for p in self.patches:
            p.start()
        self.window = MainWindow(self.cfg)

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_checkout_build_shows_in_use(self):
        self.found = (self.install, [])
        page = self.window.setup
        page.refresh()
        self.assertEqual(page.c3.badge.text(), "In use")
        self.assertIn(f"Using the build in this checkout ({self.build})", page.status3.text())
        self.assertEqual(page.go3.text(), "Build")       # a launcher-made build is still offered
        self.assertNotEqual(page.c4.badge.text(), "Rebuild first")

    def test_nothing_built_still_says_not_built(self):
        page = self.window.setup
        page.refresh()
        self.assertEqual(page.c3.badge.text(), "Not built")
        self.assertEqual(page.go3.text(), "Build")


if __name__ == "__main__":
    unittest.main()
