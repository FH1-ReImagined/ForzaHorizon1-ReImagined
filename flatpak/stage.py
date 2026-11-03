#!/usr/bin/env python3
"""Assemble the folder the Flatpak manifest installs (flatpak/stage/, not tracked by git):

  stage/
    forza_launcher/ main.py          the launcher (plain Python, run by the SDK's python3)
    assets/                          logo.svg, logo.png, social-preview.png
    tools/                           the Python tools the launcher runs (extraction, DLC, title update)
    project/forza-recomp, project/tools/cmake   the project's own sources (no generated code)
    server/                          the Forza Live server (the server/ submodule, FH1-ReImagined/forza-live)
    sdk-overlay/rexglue-overlay-linux-amd64.tar.gz   the patched ReXGlue SDK pieces (built by CI)
    licenses/
    wheels/                          not here: the manifest downloads the PySide6 wheels itself

Nothing of the game goes in: no game code, no game data, no built game. The player's machine
builds that. The same pieces launcher/packaging/make_package.py puts in the .tar.gz.

  flatpak/stage.py --overlay rexglue-overlay-linux-amd64.tar.gz [--sdk-license FILE] [--version X]
"""
import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "launcher"))
from forza_launcher import builder  # noqa: E402

TOOLS = ("xcontent_extract.py", "xiso_extract.py", "install_dlc.py", "apply_title_update.py", "lzx_delta.py", "xdc_decode.py", "livery_codec.py",
         "pipeline_list.py", "fxobj_shaders.py", "xxh3.py")
ASSETS = ("logo.svg", "logo.png", "social-preview.png")


def copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", required=True, help="rexglue-overlay-linux-amd64.tar.gz")
    ap.add_argument("--sdk-license", help="the ReXGlue SDK's LICENSE (BSD-3-Clause), reproduced in the package")
    ap.add_argument("--out", default=str(HERE / "stage"))
    ap.add_argument("--version", help="launcher version (default: the one in _version.py)")
    a = ap.parse_args()
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)

    shutil.copytree(REPO / "launcher" / "forza_launcher", out / "forza_launcher",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copy(REPO / "launcher" / "main.py", out / "main.py")
    if a.version:
        (out / "forza_launcher" / "_version.py").write_text(
            f'"""Launcher version."""\n__version__ = "{a.version}"\n', encoding="utf-8")
    for n in ASSETS:
        copy(REPO / "docs" / "images" / n, out / "assets" / n)
    for n in TOOLS:
        copy(REPO / "tools" / n, out / "tools" / n)
    for resource in builder.NATIVE_TUNING_FILES:
        copy(REPO / "tools" / resource, out / "project" / "tools" / resource)
    for rel in builder.source_files(REPO):
        copy(REPO / "forza-recomp" / rel, out / "project" / "forza-recomp" / rel)
    for f in sorted((REPO / "tools" / "cmake").glob("*.cmake")):
        copy(f, out / "project" / "tools" / "cmake" / f.name)
    for name in builder.NATIVE_MENU_FILES:
        copy(REPO / "tools" / name, out / "project" / "tools" / name)
    if not (REPO / "server" / "forza_live_server.py").is_file():
        sys.exit("server/ is empty: the Forza Live server is a git submodule (git submodule update --init server)")
    for f in builder.server_files(REPO / "server"):
        copy(REPO / "server" / f, out / "server" / f)
    copy(Path(a.overlay), out / "sdk-overlay" / "rexglue-overlay-linux-amd64.tar.gz")
    copy(REPO / "LICENSE", out / "licenses" / "ForzaHorizon1-ReImagined.txt")
    # The GPLv3 section 7 additional permission and the relicensing note travel with the license, and so
    # does the license of the Forza Live server (AGPL-3.0-or-later), whose files are packaged above.
    copy(REPO / "NOTICE.md", out / "licenses" / "NOTICE.txt")
    if (REPO / "server" / "LICENSE").is_file():
        copy(REPO / "server" / "LICENSE", out / "licenses" / "forza-live.txt")
    if a.sdk_license:
        copy(Path(a.sdk_license), out / "licenses" / "rexglue-sdk.txt")
    print(out)


if __name__ == "__main__":
    main()
