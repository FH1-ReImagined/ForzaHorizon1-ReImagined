"""Entry point: the window, or a few command-line modes.

  forza-launcher                 open the launcher
  forza-launcher --dry-run       print the forza.toml and command line Play would use (nothing is written)
  forza-launcher --live-server SCRIPT [args]   run the Forza Live server (the frozen app does this
                                 for the local server, so the player needs no Python)
  forza-launcher --run-script SCRIPT [args]   run a bundled Python tool (the frozen app has no Python)
  forza-launcher --extract PACKAGE [--root DIR]   extract your Games on Demand package without the window
  forza-launcher --build [--root DIR] [--jobs N] [--clean]   build the game without the window
  forza-launcher --version
  forza-launcher --vulkan-devices FILE   write this PC's Vulkan devices to FILE (the launcher runs it, #672)
  forza-launcher --web-view-probe   check that the website account window can start (the launcher runs it, #852)
"""
import runpy
import sys

from . import __version__


def run_live_server(argv):
    script = argv[0]
    sys.argv = [script] + argv[1:]
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    runpy.run_path(script, run_name="__main__")


def dry_run():
    from . import game
    from .config import Config
    cfg = Config()
    install, looked = game.find_game(cfg)
    if install is None:
        print(game.missing_message(looked))
        return 1
    plan = game.build_plan(cfg, install, write=False)
    from . import gpu
    print(f"# graphics card: {gpu.label(cfg.gpu_vendor())} (detected: {cfg.gpu.get('name') or 'no'})")
    from . import specs
    print(f"# {specs.describe(cfg.pc_specs())}")
    print(f"# {plan.toml_path}")
    print(plan.toml_text, end="")
    print("# environment")
    for k, v in sorted(plan.env.items()):
        print(f"{k}={v}")
    for k in plan.unset:
        print(f"unset {k}")
    print("# command (in %s)" % plan.cwd)
    print(plan.command_line())
    if plan.server_argv:
        print("# local server\n" + " ".join(plan.server_argv))
    for n in plan.notes:
        print("# note: " + n)
    return 0


def headless_extract(argv):
    import argparse
    from pathlib import Path
    from .config import Config
    ap = argparse.ArgumentParser(prog="forza-launcher --extract")
    ap.add_argument("package", help="the Games on Demand package (4D5309C9/00007000/<name>), next to its .data folder")
    ap.add_argument("--root", help="data root (default: the launcher's); the game goes to ROOT/game")
    a = ap.parse_args(argv)
    from . import installer
    root = Path(a.root) if a.root else Config().data_home()
    last = {"pct": -1}

    def progress(done, total, name):
        pct = int(100 * done / total) if total else 100
        if pct != last["pct"] and pct % 5 == 0:
            last["pct"] = pct
            print(f"extracting: {pct}% ({done / 2**30:.1f} of {total / 2**30:.1f} GB)", flush=True)
    try:
        total = installer.extract_game(a.package, root / "game", progress, note=print)
    except installer.InstallError as e:
        print(f"extraction failed: {e}", file=sys.stderr)
        return 1
    print(f"extracted {total / 2**30:.1f} GB to {root / 'game'}")
    return 0


def headless_build(argv):
    import argparse
    from pathlib import Path
    from . import builder
    from .config import Config
    ap = argparse.ArgumentParser(prog="forza-launcher --build")
    ap.add_argument("--root", help="data root (default: the launcher's)")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--accept-microsoft-license", action="store_true")
    ap.add_argument("--title-update", default="",
                    help="your Title Update 4 package, a zip holding it or a folder (default: the one set in the "
                         "launcher; TU4 is recommended: Horizon Rally, 1000 Club)")
    ap.add_argument("--base", action="store_true", help="build the base game, without a title update")
    ap.add_argument("--download-toolchain", action="store_true",
                    help="use the pinned LLVM, CMake and ninja even when the system has them")
    a = ap.parse_args(argv)
    cfg = Config()
    root = Path(a.root) if a.root else cfg.data_home()

    class Console(builder.Reporter):
        def log(self, text):
            print(text, flush=True)

        def status(self, text):
            print(f"== {text}", flush=True)

    opts = builder.Options(root, a.jobs, accept_ms_license=a.accept_microsoft_license, clean=a.clean,
                           title_update="" if a.base else (str(Path(a.title_update).expanduser().resolve()) if a.title_update
                                                else cfg["tu_package"].strip()),
                           prefer_downloaded=a.download_toolchain)
    try:
        rt = builder.run_build(opts, Console(), root / "game")
    except builder.BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    print(f"runtime: {rt}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--live-server"]:
        run_live_server(argv[1:])
        return 0
    if argv[:1] == ["--run-script"]:
        run_live_server(argv[1:])  # the same runner: a script with its folder on sys.path
        return 0
    if argv[:1] == ["--vulkan-devices"]:
        from . import vulkan_probe
        return vulkan_probe.main(argv[1:])
    if argv[:1] == ["--web-view-probe"]:
        from .ui import web_view
        return web_view.probe_main(argv[1:])
    if argv[:1] == ["--version"]:
        print(__version__)
        return 0
    if argv[:1] == ["--extract"]:
        return headless_extract(argv[1:])
    if argv[:1] == ["--build"]:
        return headless_build(argv[1:])
    if argv[:1] == ["--dry-run"]:
        return dry_run()
    from .ui.window import run
    return run(argv)
