"""tools/prerelease.sh's game runs never read the build folder's forza.toml (#846); no game needed.

A made-up build folder holds the player's forza.toml (a broken one) and a stand-in for the game
that records what it was started with: its folder, the forza.toml next to it (the only settings
file the SDK reads), its environment and its options. The runs start through
tools/prerelease_game.sh and tools/live_session.sh, as the boot and server checks start them.

Run: python3 -m unittest discover -s tools -p test_prerelease_game.py (Linux, bash)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWN = ROOT / "tools" / "prerelease_forza.toml"
PLAYER = b'# The player\'s settings, broken on purpose\nlive_server = "192.0.2.1\nframe_rate = [\n'

# Stands in for the game. Like the SDK it takes forza.toml from its own executable's folder.
FAKE_GAME = r"""#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.realpath(sys.argv[0]))
toml = os.path.join(here, "forza.toml")
seen = {
    "cwd": os.getcwd(), "here": here, "args": sys.argv[1:],
    "toml": open(toml, "rb").read().decode("latin-1") if os.path.exists(toml) else None,
    "env": {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                                         "XDG_CACHE_HOME", "FORZA_SERVER", "FORZA_NET", "FORZA_XUID",
                                         "FORZA_GRAPHICS_SETTINGS", "REX_LIVE_SERVER", "REX_AUDIO_FAIL_OPEN")},
}
out = os.environ["FORZA_FRAMEDUMP_DIR"]   # tools/live_session.sh: the run folder
with open(os.path.join(out, "seen.json"), "w") as f:
    json.dump(seen, f)
"""


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"), "Linux shell tools")
class PrereleaseGameRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prerelease-846-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.build = self.tmp / "build"
        (self.build / "logs").mkdir(parents=True)
        (self.build / "logs" / "forza.log").write_text("the player's log\n")
        (self.build / "forza").write_text(FAKE_GAME)
        (self.build / "forza").chmod(0o755)
        (self.build / "librexgpu-xenos.so").write_bytes(b"plugin")
        (self.build / "update").symlink_to(self.tmp)
        (self.build / "forza.toml").write_bytes(PLAYER)
        self.player = (self.build / "forza.toml").stat()

    def bash(self, script, *args, env=None):
        # No display, no sound: should anything start a real game, it opens no window.
        full = {k: v for k, v in os.environ.items() if not k.startswith(("FORZA_", "BENCH_"))
                and k not in ("DISPLAY", "WAYLAND_DISPLAY", "PULSE_SERVER", "PULSE_SINK")}
        full.update({
            "SDL_VIDEO_DRIVER": "dummy", "SDL_VIDEODRIVER": "dummy", "SDL_AUDIO_DRIVER": "dummy",
            "XDG_RUNTIME_DIR": str(self.tmp / "runtime"),
            "BENCH_LOCK": str(self.tmp / "no-lock"),  # nobody else's benchmark lock
            "FORZA_IGNORE_MEM": "1", "FORZA_DATA": str(self.tmp / "game-data"),
            # As in a shell of a player who also plays online, or a launcher's child.
            "FORZA_SERVER": "192.0.2.1:21200", "FORZA_NET": "1", "REX_LIVE_SERVER": "192.0.2.1:21201",
            "FORZA_GRAPHICS_SETTINGS": str(self.tmp / "player-graphics.json"),
            # The caller's own setting for the checks' games: kept.
            "REX_AUDIO_FAIL_OPEN": "all",
        })
        full.update(env or {})
        return subprocess.run(["bash", "-c", f'. "{ROOT}/tools/prerelease_game.sh"; ' + script,
                               "test", *args], env=full, capture_output=True, text=True, timeout=60)

    def start(self, name, *pairs):
        run = self.tmp / "log" / name
        r = self.bash('prerelease_start_game "$1" "$2" 10 "${@:3}" -- --audio_mute --present_effect=fsr',
                      str(run), str(self.build), *pairs,
                      env={"PRERELEASE_XDG_CACHE": str(self.tmp / "xdg-cache")})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for _ in range(200):
            if (run / "seen.json").exists():
                break
            time.sleep(0.05)
        pid = int((run / "pid").read_text())
        for _ in range(200):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        self.assertTrue((run / "seen.json").exists(), (run / "run.out").read_text())
        return run, json.loads((run / "seen.json").read_text())

    def assert_player_file_untouched(self):
        now = (self.build / "forza.toml").stat()
        self.assertEqual((self.build / "forza.toml").read_bytes(), PLAYER)
        self.assertEqual((now.st_ino, now.st_nlink, now.st_mtime_ns),
                         (self.player.st_ino, 1, self.player.st_mtime_ns))
        self.assertEqual(sorted(p.name for p in self.build.iterdir()),
                         ["forza", "forza.toml", "librexgpu-xenos.so", "logs", "update"])
        self.assertEqual((self.build / "logs" / "forza.log").read_text(), "the player's log\n")

    def test_the_game_runs_with_the_checks_settings_not_the_players(self):
        # A build name that cannot exist: were the copy ever skipped, live_session.sh finds no game.
        run, seen = self.start("boot-linux-tu4-fsr", "FORZA_BUILD=no-such-build-846", "FORZA_XUID=E0000000000000AA")
        game = run / "game"
        # It ran from the run's own copy of the build, so that copy's forza.toml was its settings.
        self.assertEqual(Path(seen["here"]), game)
        self.assertEqual(Path(seen["cwd"]), game)
        self.assertEqual(seen["toml"], KNOWN.read_text())
        with open(KNOWN, "rb") as f:
            known = tomllib.load(f)
        self.assertEqual(known["live_server"], "")
        self.assertIn("--audio_mute", seen["args"])
        self.assertIn("--present_effect=fsr", seen["args"])
        # Offline, and the identity / relaunch files outside ~/.local/share.
        self.assertIsNone(seen["env"]["FORZA_SERVER"])
        self.assertIsNone(seen["env"]["FORZA_NET"])
        self.assertIsNone(seen["env"]["REX_LIVE_SERVER"])
        self.assertIsNone(seen["env"]["FORZA_GRAPHICS_SETTINGS"])
        self.assertEqual(seen["env"]["REX_AUDIO_FAIL_OPEN"], "all")
        self.assertEqual(seen["env"]["FORZA_XUID"], "E0000000000000AA")
        for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            self.assertTrue(Path(seen["env"][k]).is_relative_to(run), k)
        self.assertEqual(Path(seen["env"]["XDG_CACHE_HOME"]), self.tmp / "xdg-cache")
        # The copy: the build's files, no logs/ or symlinks, and a forza.toml that is no link.
        self.assertEqual(sorted(p.name for p in game.iterdir()), ["forza", "forza.toml", "librexgpu-xenos.so"])
        self.assertEqual((game / "forza.toml").stat().st_nlink, 1)
        self.assert_player_file_untouched()

    def test_the_server_check_still_names_its_server(self):
        _, seen = self.start("server-game", "FORZA_SERVER=127.0.0.1:21310")
        self.assertEqual(seen["env"]["FORZA_SERVER"], "127.0.0.1:21310")
        self.assertEqual(seen["toml"], KNOWN.read_text())
        self.assert_player_file_untouched()

    def test_a_retry_in_the_same_folder(self):
        run, _ = self.start("boot-retry")
        (run / "game" / "forza.toml").write_text("changed by the first attempt\n")
        (run / "seen.json").unlink()
        _, seen = self.start("boot-retry")
        self.assertEqual(seen["toml"], KNOWN.read_text())
        self.assert_player_file_untouched()

    def test_the_copy_goes_after_the_run(self):
        run, _ = self.start("boot-drop")
        r = self.bash('prerelease_drop_game "$1"', str(run))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((run / "game").exists())
        self.assertTrue((run / "seen.json").exists())
        self.assertEqual((self.build / "forza").read_text(), FAKE_GAME)
        self.assert_player_file_untouched()

    def test_game_flags_need_the_separator(self):
        r = self.bash('prerelease_start_game "$1" "$2" 10 FORZA_XUID=1 --audio_mute',
                      str(self.tmp / "log" / "x"), str(self.build))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not NAME=VALUE", r.stdout)
        self.assertFalse((self.tmp / "log" / "x" / "game").exists())

    def test_no_executable(self):
        (self.build / "forza").unlink()
        r = self.bash('prerelease_stage_game "$1" "$2"', str(self.build), str(self.tmp / "game"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no forza executable", r.stdout)
        self.assertFalse((self.tmp / "game").exists())

    def test_prerelease_starts_its_games_this_way(self):
        # Every game prerelease.sh starts itself goes through prerelease_start_game.
        text = (ROOT / "tools" / "prerelease.sh").read_text()
        self.assertIn('. "$ROOT/tools/prerelease_game.sh"', text)
        self.assertIn("prerelease_start_game ", text)
        self.assertNotIn("tools/live_session.sh \"", text)


if __name__ == "__main__":
    unittest.main()
