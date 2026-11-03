"""The environment of the programs a frozen launcher starts (#87): PyInstaller puts its _internal folder
on LD_LIBRARY_PATH and rexglue, cmake, ninja, clang, git and the game failed with
"libstdc++.so.6: version GLIBCXX_3.4.31 not found". Run: python3 -m unittest launcher/test_cleanenv.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forza_launcher import build_env, cleanenv, game, gpu  # noqa: E402
from forza_launcher.config import Config  # noqa: E402

INTERNAL = str(Path("/opt/ForzaHorizon1-ReImagined/_internal"))
USER_LIBS = os.pathsep.join([str(Path("/home/me/lib")), str(Path("/usr/local/lib"))])


def frozen_env(original=USER_LIBS, **extra):
    """What a PyInstaller bootloader leaves in os.environ."""
    env = {"PATH": "/usr/bin", "HOME": "/home/me", "LD_LIBRARY_PATH": INTERNAL + (os.pathsep + original if original else "")}
    if original is not None:
        env["LD_LIBRARY_PATH_ORIG"] = original
    env.update(extra)
    return env


def frozen(env):
    """Run the body as the frozen launcher with os.environ == env."""
    stack = [mock.patch.object(sys, "frozen", True, create=True),
             mock.patch.object(sys, "_MEIPASS", INTERNAL, create=True),
             mock.patch.dict(os.environ, env, clear=True)]

    class Ctx:
        def __enter__(self):
            for s in stack:
                s.__enter__()

        def __exit__(self, *a):
            for s in reversed(stack):
                s.__exit__(*a)
    return Ctx()


class CleanEnvironment(unittest.TestCase):
    def test_restores_the_original_library_path(self):
        with frozen(frozen_env()):
            env = cleanenv.clean_environment()
        self.assertEqual(env["LD_LIBRARY_PATH"], USER_LIBS)
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_no_original_means_no_library_path(self):
        with frozen(frozen_env(original=None, LD_LIBRARY_PATH=INTERNAL)):
            env = cleanenv.clean_environment()
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_empty_original_means_no_library_path(self):
        with frozen(frozen_env(original="")):
            env = cleanenv.clean_environment()
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_no_original_keeps_entries_outside_the_bundle(self):
        with frozen(frozen_env(original=None, LD_LIBRARY_PATH=os.pathsep.join(
                [INTERNAL, "/opt/cuda/lib", INTERNAL + "/PySide6"]))):
            env = cleanenv.clean_environment()
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/cuda/lib")

    def test_bundle_variables_do_not_leak(self):
        extra = {"QT_PLUGIN_PATH": INTERNAL + "/PySide6/Qt/plugins", "QML2_IMPORT_PATH": "/home/me/qml",
                 "SSL_CERT_FILE": INTERNAL + "/certifi/cacert.pem", "PYTHONHOME": INTERNAL,
                 "_MEIPASS2": INTERNAL, "_PYI_APPLICATION_HOME_DIR": INTERNAL}
        with frozen(frozen_env(**extra)):
            env = cleanenv.clean_environment()
        for k in ("QT_PLUGIN_PATH", "SSL_CERT_FILE", "PYTHONHOME", "_MEIPASS2", "_PYI_APPLICATION_HOME_DIR"):
            self.assertNotIn(k, env)
        self.assertEqual(env["QML2_IMPORT_PATH"], "/home/me/qml")   # the player's own value stays

    def test_not_frozen_changes_nothing(self):
        base = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/home/me/lib", "QT_PLUGIN_PATH": "/x", "SSL_CERT_FILE": "/c"}
        with mock.patch.dict(os.environ, base, clear=True):
            self.assertEqual(cleanenv.clean_environment(), base)

    def test_base_is_not_modified(self):
        base = frozen_env()
        with frozen(base):
            cleanenv.clean_environment(base)
        self.assertEqual(base, frozen_env())

    @mock.patch.object(sys, "platform", "linux")
    def test_process_environment_restored_for_programs_qt_starts(self):
        with frozen(frozen_env()):
            cleanenv.restore_process_environment()
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], USER_LIBS)
            self.assertNotIn("LD_LIBRARY_PATH_ORIG", os.environ)
        with frozen(frozen_env(original=None, LD_LIBRARY_PATH=INTERNAL)):
            cleanenv.restore_process_environment()
            self.assertNotIn("LD_LIBRARY_PATH", os.environ)

    @mock.patch.object(sys, "platform", "win32")
    def test_windows_process_environment_is_unchanged(self):
        with frozen(frozen_env()):
            before = dict(os.environ)
            cleanenv.restore_process_environment()
            self.assertEqual(dict(os.environ), before)


class EveryLaunchPath(unittest.TestCase):
    """Each place the launcher starts a program gets the clean environment."""

    def assertClean(self, env, user=USER_LIBS):
        self.assertNotIn(INTERNAL, (env or {}).get("LD_LIBRARY_PATH", ""))
        if user is not None:
            self.assertEqual((env or {}).get("LD_LIBRARY_PATH"), user)

    def test_build_tools(self):
        tools = build_env.Tools()
        tools.path_dirs = ["/tools/bin"]
        with frozen(frozen_env()):
            env = build_env.tool_env(tools)
        self.assertClean(env)
        self.assertTrue(env["PATH"].startswith("/tools/bin"))

    def test_compiler_check_and_version_queries(self):
        original_run = gpu.subprocess.run
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="cmake version 3.30.1", stderr=""))
        # Other launcher workers share Python's subprocess module. Patch this
        # module's dependency instead of counting their concurrent GPU probes.
        process = SimpleNamespace(run=run, SubprocessError=build_env.subprocess.SubprocessError)
        with frozen(frozen_env()), mock.patch.object(build_env, "subprocess", process):
            self.assertIs(gpu.subprocess.run, original_run)
            self.assertIsNone(build_env._cxx_works("g++"))
            self.assertEqual(build_env._version_of(["cmake", "--version"], r"(\d+\.\d+)"), (3, 30))
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertClean(call.kwargs.get("env"))

    def test_gpu_queries(self):
        with frozen(frozen_env()), mock.patch.object(gpu.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="")
            gpu._run(["lspci"])
            ours = [c for c in run.call_args_list if c.args and c.args[0] == ["lspci"]]
            self.assertEqual(len(ours), 1)
            self.assertClean(ours[0].kwargs["env"])

    def test_the_game_and_the_server(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "rec.txt"
            fake = Path(d) / "fake.py"
            fake.write_text("import os,sys\nopen(sys.argv[1],'a').write(os.environ.get('LD_LIBRARY_PATH','-')+'\\n')\n")
            (Path(d) / "srv").mkdir()
            plan = game.LaunchPlan(game=None, cwd=Path(d), argv=[sys.executable, str(fake), str(rec)],
                                   env={"LD_LIBRARY_PATH": "/game/lib:" + USER_LIBS}, unset=[],
                                   toml_path=Path(d) / "t.toml", toml_text="",
                                   server_argv=[sys.executable, str(fake), str(Path(d) / "srv" / "rec.txt"),
                                                "--data-dir", str(Path(d) / "srv")])
            with frozen(frozen_env()):
                s = game.Session(plan, log=lambda _t: None)
                self.addCleanup(s.cleanup)
                s.start()
                s.proc.wait(timeout=30)
                s.server.wait(timeout=30)
            # the game keeps its own lib dirs and the player's path, never _internal
            self.assertEqual(rec.read_text().strip(), "/game/lib:" + USER_LIBS)
            self.assertEqual((Path(d) / "srv" / "rec.txt").read_text().strip(), USER_LIBS)

    @mock.patch.object(game.paths, "IS_WINDOWS", False)
    def test_the_game_without_a_saved_library_path(self):
        with frozen(frozen_env(original=None, LD_LIBRARY_PATH=INTERNAL)), tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "launcher.json")
            cfg.advanced["data_root"] = str(Path(d) / "home")
            cfg.online["mode"] = "offline"
            install = game.GameInstall(Path(d) / "rt", Path(d) / "rt" / game.EXE, lib_dirs=[Path("/sdk/lib")])
            plan = game.build_plan(cfg, install, write=False, log=lambda _t: None)
        self.assertEqual(plan.env["LD_LIBRARY_PATH"], f"{Path('/sdk/lib')}:{install.dir}")

    @mock.patch.object(game.paths, "IS_WINDOWS", False)
    def test_the_game_plan_keeps_the_players_path(self):
        with frozen(frozen_env()), tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "launcher.json")
            cfg.advanced["data_root"] = str(Path(d) / "home")
            cfg.online["mode"] = "offline"
            install = game.GameInstall(Path(d) / "rt", Path(d) / "rt" / game.EXE, lib_dirs=[Path("/sdk/lib")])
            plan = game.build_plan(cfg, install, write=False, log=lambda _t: None)
        self.assertEqual(plan.env["LD_LIBRARY_PATH"], f"{Path('/sdk/lib')}:{install.dir}:{USER_LIBS}")

    def test_build_steps_get_a_clean_environment(self):
        from forza_launcher import builder
        import inspect
        src = inspect.getsource(builder)
        # no step may pass the raw environment to a child
        self.assertNotIn("os.environ.copy()", src)
        self.assertNotIn("dict(os.environ)", src)
        with frozen(frozen_env()), mock.patch.object(builder.subprocess, "Popen") as popen:
            popen.return_value.stdout = iter([])
            popen.return_value.wait.return_value = 0
            popen.return_value.returncode = 0
            popen.return_value.poll.return_value = 0
            try:
                builder.run(["rexglue", "--version"], "/tmp", cleanenv.clean_environment(),
                            mock.Mock(cancelled=lambda: False))
            except Exception:
                pass
            # other threads may use the shared subprocess module meanwhile: pick our call
            ours = [c for c in popen.call_args_list if c.args and c.args[0] == ["rexglue", "--version"]]
            self.assertEqual(len(ours), 1)
            self.assertClean(ours[0].kwargs["env"])


if __name__ == "__main__":
    unittest.main()
