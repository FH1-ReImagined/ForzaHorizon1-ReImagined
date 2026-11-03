#!/usr/bin/env bash
# Package the Linux build in forza-recomp/out/dist/linux so it runs without the SDK or a
# build tree: the game and its guest-DLL modules, the SDK runtime, the patched Xenos GPU
# plugin, play.sh, the Forza Live server and the package extractor. Adapted from
# RecompiledHaloReach's tools/package_linux.sh.
#
# Environment: FORZA_BUILD (build dir under forza-recomp/out/build, default linux),
# REXSDK (SDK prefix; default the patched overlay that tools/build_rexglue_sdk.sh makes),
# REXSDK_SRC (SDK source, for its license), DIST (output dir).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/forza-recomp/out/build/${FORZA_BUILD:-linux}"
SDK="${REXSDK:-$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64}"
SRC="${REXSDK_SRC:-$HOME/rexglue-sdk-src-forza/sdk}"
DIST="${DIST:-$ROOT/forza-recomp/out/dist/linux}"
MODULES=(forza_XMediaFacade_default forza_SpeechFacade_default)

[ -x "$BUILD/forza" ] || { echo "no build at $BUILD (see README: Building)" >&2; exit 1; }
[ -f "$SDK/lib/librexruntime.so" ] || { echo "no SDK at $SDK" >&2; exit 1; }
[ -f "$SDK/lib/librexgpu-xenos.so" ] && [ ! -L "$SDK/lib/librexgpu-xenos.so" ] ||
    { echo "no patched GPU plugin in $SDK (run tools/build_rexglue_sdk.sh)" >&2; exit 1; }

rm -rf "$DIST"
mkdir -p "$DIST/licenses" "$DIST/server"
# One flat directory: forza finds librexruntime.so through its $ORIGIN runpath, and the
# runtime dlopen()s the plugin and the modules from there.
cp "$BUILD/forza" "$DIST/"
cp "$BUILD/forza-telemetry.json" "$DIST/"
for module in "${MODULES[@]}"; do cp "$BUILD/$module" "$DIST/"; done
cp -L "$SDK/lib/librexruntime.so" "$SDK/lib/librexgpu-xenos.so" "$DIST/"
# The OpenXR loader for the VR setting, when the overlay has one (docs/vr.md, "Packaging").
[ -f "$SDK/lib/libopenxr_loader.so.1" ] && cp -L "$SDK/lib/libopenxr_loader.so.1" "$DIST/"
# The plugin comes out of the SDK's build tree, whose runpath points into that tree.
if command -v patchelf > /dev/null; then
    patchelf --set-rpath '$ORIGIN' "$DIST/librexgpu-xenos.so"
else
    rpath_script="$(mktemp)"
    printf 'file(RPATH_SET FILE "%s" NEW_RPATH "$ORIGIN")\n' "$DIST/librexgpu-xenos.so" > "$rpath_script"
    cmake -P "$rpath_script"
    rm -f "$rpath_script"
fi
cp "$ROOT/tools/xcontent_extract.py" "$ROOT/tools/xiso_extract.py" "$ROOT/tools/install_dlc.py" "$DIST/"
# The Forza Live server: its entry point and the forza_live package (no tests).
cp "$ROOT/server/forza_live_server.py" "$DIST/server/"
cp -r "$ROOT/server/forza_live" "$DIST/server/"
find "$DIST/server" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/LICENSE" "$DIST/licenses/ForzaHorizon1-ReImagined.txt"
# The GPLv3 section 7 additional permission and the relicensing note, and the server's license.
cp "$ROOT/NOTICE.md" "$DIST/licenses/NOTICE.txt"
[ -f "$ROOT/server/LICENSE" ] && cp "$ROOT/server/LICENSE" "$DIST/licenses/forza-live.txt"
[ -f "$SRC/LICENSE" ] && cp "$SRC/LICENSE" "$DIST/licenses/rexglue-sdk.txt"
[ -f "$SDK/share/licenses/SDL3/LICENSE.txt" ] && cp "$SDK/share/licenses/SDL3/LICENSE.txt" "$DIST/licenses/SDL3.txt"
# AMD FidelityFX FSR 3 sources compiled into the GPU plugin (SDK patch 0072, MIT)
fsr3_license="$SRC/out/linux-amd64/Release/AMD-FidelityFX-LICENSE.txt"
[ -f "$fsr3_license" ] && cp "$fsr3_license" "$DIST/licenses/AMD-FidelityFX-FSR3.txt"
smaa_license="$SRC/out/linux-amd64/Release/SMAA-LICENSE.txt"
[ -f "$smaa_license" ] && cp "$smaa_license" "$DIST/licenses/SMAA.txt"
cp "$ROOT/tools/dist/play.linux.sh" "$DIST/play.sh"
chmod +x "$DIST/play.sh"

commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
# The newest glibc and libstdc++ symbol versions the shipped binaries use.
newest() { (cd "$DIST" && objdump -T forza librexruntime.so librexgpu-xenos.so "${MODULES[@]}") |
           grep -o "$1[0-9.]*[0-9]" | sort -V | tail -1; }
glibc="$(newest GLIBC_2. | cut -d_ -f2)"
glibcxx="$(newest GLIBCXX_3.)"
sed -e "s/@COMMIT@/$commit/" -e "s/@GLIBC@/$glibc/" -e "s/@GLIBCXX@/$glibcxx/" \
    "$ROOT/tools/dist/README.linux.txt" > "$DIST/README.txt"
echo "Linux package: $DIST"

missing="$(cd "$DIST" && LD_LIBRARY_PATH="$DIST" ldd forza librexgpu-xenos.so "${MODULES[@]}" | grep 'not found' || true)"
[ -z "$missing" ] || { echo "unresolved libraries:" >&2; echo "$missing" >&2; exit 1; }
