#!/usr/bin/env python3
"""Assemble the release folder around the PyInstaller output:

  ForzaHorizon1-ReImagined/
    forza-launcher(.exe) + _internal/      the launcher
    project/forza-recomp, project/tools/cmake   the project's own sources (no generated code)
    server/                                the Forza Live server (the server/ submodule, FH1-ReImagined/forza-live)
    sdk-overlay/rexglue-overlay-<os>-amd64.tar.gz   the patched ReXGlue SDK pieces (built by CI)
    licenses/ (with PRIVACY.txt, the privacy policy), README.txt
  (Linux also: install.sh, uninstall.sh, forza-reimagined.desktop, forza-reimagined.png)

It never contains game code or data, and no built game: the player's machine builds that.

  make_package.py --platform linux --pyinstaller-dist DIST --overlay FILE --out OUTDIR [--sdk-license FILE] [--tar]
"""
import argparse
import shutil
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from forza_launcher import builder  # noqa: E402

README = """Forza Horizon 1: ReImagined - launcher
https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined

This package contains the launcher, the project's source code and the patched ReXGlue SDK
pieces. It contains NO game code and NO game data. Open the launcher, point it at your own copy
of Forza Horizon (Xbox 360, Games on Demand package), and it builds the game on your computer.

What the launcher and the game send over the network, and how to turn it off:
licenses/PRIVACY.txt (https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/blob/main/PRIVACY.md).

License: GNU General Public License, version 3 or later, with an additional permission for graphics,
VR and platform components (licenses/ForzaHorizon1-ReImagined.txt and licenses/NOTICE.txt). The
Forza Live server in server/ is under the GNU Affero General Public License (licenses/forza-live.txt).

Forza Horizon is (c) Microsoft. This project is not affiliated with, endorsed by or sponsored
by Microsoft, Playground Games or Turn 10 Studios.
"""


def copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["linux", "windows"], required=True)
    ap.add_argument("--pyinstaller-dist", required=True, help="the forza-launcher folder PyInstaller made")
    ap.add_argument("--overlay", help="rexglue-overlay-<os>-amd64.tar.gz (omit for a development package)")
    ap.add_argument("--sdk-license")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tar", action="store_true", help="also write OUT/<name>.tar.gz (Linux)")
    ap.add_argument("--version", default="dev")
    a = ap.parse_args()

    name = "ForzaHorizon1-ReImagined"
    stage = Path(a.out) / name
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(a.pyinstaller_dist, stage, symlinks=True)

    if a.platform == "windows" and not (REPO / "forza-recomp" / "thirdparty" / "ReGPU" / "CMakeLists.txt").is_file():
        # (The optional ReGPU plugin, FORZA_REGPU_PLUGIN, is off by default: the package still builds the game.)
        print("warning: forza-recomp/thirdparty/ReGPU is empty, so this package cannot build the ReGPU plugin "
              "(git submodule update --init forza-recomp/thirdparty/ReGPU)", file=sys.stderr)
    for rel in builder.source_files(REPO, windows=a.platform == "windows"):
        copy(REPO / "forza-recomp" / rel, stage / "project" / "forza-recomp" / rel)
    for f in sorted((REPO / "tools" / "cmake").glob("*.cmake")):
        copy(f, stage / "project" / "tools" / "cmake" / f.name)
    for resource in builder.NATIVE_MENU_FILES:
        copy(REPO / "tools" / resource, stage / "project" / "tools" / resource)
    for resource in builder.NATIVE_TUNING_FILES:
        copy(REPO / "tools" / resource, stage / "project" / "tools" / resource)
    if not (REPO / "server" / "forza_live_server.py").is_file():
        sys.exit("server/ is empty: the Forza Live server is a git submodule (git submodule update --init server)")
    for f in builder.server_files(REPO / "server"):
        copy(REPO / "server" / f, stage / "server" / f)
    if a.overlay:
        copy(Path(a.overlay), stage / "sdk-overlay" / f"rexglue-overlay-{a.platform}-amd64.tar.gz")
    copy(REPO / "LICENSE", stage / "licenses" / "ForzaHorizon1-ReImagined.txt")
    # The GPLv3 section 7 additional permission and the relicensing note travel with the license, and so
    # does the license of the Forza Live server (AGPL-3.0-or-later), whose files are packaged above.
    copy(REPO / "NOTICE.md", stage / "licenses" / "NOTICE.txt")
    if (REPO / "server" / "LICENSE").is_file():
        copy(REPO / "server" / "LICENSE", stage / "licenses" / "forza-live.txt")
    # The privacy policy: the Windows installer shows it (installer.iss, InfoBeforeFile).
    copy(REPO / "PRIVACY.md", stage / "licenses" / "PRIVACY.txt")
    if a.sdk_license:
        copy(Path(a.sdk_license), stage / "licenses" / "rexglue-sdk.txt")
    (stage / "README.txt").write_text(README + f"\nVersion {a.version}\n", encoding="utf-8")

    if a.platform == "linux":
        for f in ("install.sh", "uninstall.sh", "forza-reimagined.desktop"):
            copy(HERE / f, stage / f)
        (stage / "install.sh").chmod(0o755)
        (stage / "uninstall.sh").chmod(0o755)
        copy(REPO / "docs" / "images" / "logo.png", stage / "forza-reimagined.png")
        if a.tar:
            out = Path(a.out) / f"{name}-linux-x86_64.tar.gz"
            with tarfile.open(out, "w:gz") as t:
                t.add(stage, arcname=name)
            print(out)
    print(stage)


if __name__ == "__main__":
    main()
