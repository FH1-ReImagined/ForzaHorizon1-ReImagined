"""Startup-only argument handling and actual launch/boot blocks, with no game or sleeps."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
BASH = (str(Path("C:/Program Files/Git/bin/bash.exe")) if os.name == "nt" and
        Path("C:/Program Files/Git/bin/bash.exe").is_file() else shutil.which("bash"))
CASES = [(None, [], True), ("false", [], False), ("FALSE", [], False),
         ("false", ["--skip_startup_movies=true"], True),
         ("true", ["--skip_startup_movies=false"], False),
         ("false", ["--skip_startup_movies"], True),
         ("true", ["--no-skip_startup_movies"], False),
         ("false", ["--no-skip_startup_movies=false"], True),
         ("true", ["--skip_startup_movies=0"], False),
         ("true", ["--no-skip_startup_movies=+0"], False),
         ("false", ["--no-skip_startup_movies=0"], True),
         ("false", ["--skip_startup_movies=yes"], True),
         ("true", ["--skip_startup_movies=false", "--skip_startup_movies=true"], True),
         ("false", ["--skip_startup_movies=true", "--skip_startup_movies=false"], False)]


def initialization(text):
    start = text.index('. "$ROOT/tools/startup_movies.sh"')
    end = text.index('\n', text.index('forza_startup_movie_args "$@"', start))
    return text[start:end] + '\n'


@unittest.skipUnless(BASH, "requires bash")
class StartupMovieTools(unittest.TestCase):
    def environment(self, folder, value):
        env = dict(os.environ, TOOL_REPO=str(ROOT), CASE_DIR=str(folder),
                   ARGV_TRACE=str(folder / "argv"), MSYS_NO_PATHCONV="1")
        env.pop("FORZA_SKIP_MOVIES", None)
        if value is not None:
            env["FORZA_SKIP_MOVIES"] = value
        return env

    def launch(self, tool, value, args):
        text = (ROOT / "tools" / tool).read_text()
        if tool == "live_session.sh":
            start = text.index('SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT=')
            end = text.index('echo $! >', start)
        else:
            start = text.index('timeout --signal=INT')
            end = text.index('echo "EXIT=', start)
        # Run the production argument initialization and launch block only. No lock, cleanup,
        # shared-memory operations or real process wrapper is invoked.
        code = ('set -u\nROOT="$TOOL_REPO"; DIR="$CASE_DIR"; RUN="$CASE_DIR"; SECS=1; '
                'DEFAULT_SDK="$CASE_DIR"\n' + initialization(text) +
                'timeout() { shift 3; "$@"; }; nohup() { "$@"; }\n' + text[start:end] + '\nwait\n')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            fake = folder / "forza"
            fake.write_bytes(b'#!/bin/sh\nprintf "%s\\0" "$@" > "$ARGV_TRACE"\n')
            fake.chmod(0o755)
            result = subprocess.run([BASH, "-c", code, "probe", *args], cwd=folder,
                                    env=self.environment(folder, value), capture_output=True,
                                    text=True, timeout=10)
            argv = (folder / "argv").read_bytes().split(b'\0')[:-1] if (folder / "argv").is_file() else []
            return result, [a.decode() for a in argv]

    def test_actual_launch_blocks_emit_one_effective_flag_and_preserve_other_args(self):
        other = ["--marker=word with spaces", "car name", "false", "--no-unrelated"]
        for tool in ("live_session.sh", "run_forza.sh"):
            for value, args, expected in CASES:
                with self.subTest(tool=tool, value=value, args=args):
                    result, argv = self.launch(tool, value, args + other)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    flags = [a for a in argv if a.startswith("--skip_startup_movies=")]
                    self.assertEqual(flags, ["--skip_startup_movies=" + str(expected).lower()])
                    self.assertEqual(argv[-len(other):], other)

    def test_double_dash_keeps_following_startup_text_as_positional(self):
        for tool in ("live_session.sh", "run_forza.sh"):
            result, argv = self.launch(tool, "true", ["--", "--skip_startup_movies=false", "literal value"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(argv[-4:], ["--skip_startup_movies=true", "--", "--skip_startup_movies=false", "literal value"])

    def test_invalid_value_stops_before_launch(self):
        for tool in ("live_session.sh", "run_forza.sh"):
            result, argv = self.launch(tool, "true", ["--skip_startup_movies=invalid"])
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid skip_startup_movies", result.stderr)
            self.assertEqual(argv, [])

    def test_actual_boot_input_block_uses_the_same_effective_value(self):
        text = (ROOT / "tools/perf_scenario.sh").read_text()
        start = text.index('# Boot videos (Microsoft Studios, Playground): skipped')
        end = text.index('case "$SCENARIO"', start)
        code = ('ROOT="$TOOL_REPO"\n' + initialization(text) +
                'wait_s() { :; }; say() { printf "%s\\n" "$1"; }\n' + text[start:end])
        for value, args, expected in CASES:
            with self.subTest(value=value, args=args), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run([BASH, "-c", code, "probe", *args],
                                        env=self.environment(Path(tmp), value), capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines().count("START"), 1 if expected else 5)
                self.assertIn("SCENE:main_menu", result.stdout)


if __name__ == "__main__":
    unittest.main()
