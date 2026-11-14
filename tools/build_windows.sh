#!/usr/bin/env bash
# Cross-compile the Windows version from Linux and package it in
# forza-recomp/out/dist/windows:
#   1. the ReXGlue SDK (runtime and Xenos GPU plugin) from source, ReIGlue (this project's
#      fork) with the Vulkan backend (the SDK's prebuilt Windows plugin has none), and
#   2. the game against it, reusing the generated code of the Linux build.
#
# Usage: tools/build_windows.sh [--sdk-only | --game-only]   (two chunks: the SDK build is long)
#
# Needs, besides what the Linux build needs (run it first: it runs codegen, and
# tools/build_rexglue_sdk.sh checks out the SDK source):
#   - Homebrew's llvm (clang, clang-cl, llvm-lib, llvm-rc) and lld (lld-link);
#   - the MSVC CRT and Windows SDK, unpacked by xwin (https://github.com/Jake-Shadle/xwin).
#     Running it means accepting Microsoft's license:
#       xwin --accept-license splat --output ~/.local/opt/xwin/sdk
#
# Environment: XWIN_DIR (default ~/.local/opt/xwin/sdk), REXSDK_SRC (default
# ~/rexglue-sdk-src-forza/sdk), REXSDK_WIN_PREFIX (where the Windows SDK build is installed,
# default ~/rexglue-sdk-patched-forza/0.10.0.24/win-amd64).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
XWIN="${XWIN_DIR:-$HOME/.local/opt/xwin/sdk}"
SRC="${REXSDK_SRC:-$HOME/rexglue-sdk-src-forza/sdk}"
PREFIX="${REXSDK_WIN_PREFIX:-$HOME/rexglue-sdk-patched-forza/0.10.0.24/win-amd64}"
JOBS="${JOBS:-$(nproc)}"

[ -d "$XWIN/crt/include" ] || { echo "no MSVC CRT at $XWIN (run xwin splat, see above)" >&2; exit 1; }
[ -e "$SRC/.git" ] || { echo "no SDK source at $SRC (run tools/build_rexglue_sdk.sh [--source-only] first)" >&2; exit 1; }
[ -f "$ROOT/forza-recomp/generated/default/sources.cmake" ] ||
    { echo "no generated code (build the Linux version first)" >&2; exit 1; }

# The SDK's sources include some Windows headers in mixed case (ObjBase.h, SDKDDKVer.h);
# the Windows SDK ships them in lower case and Linux file systems are case-sensitive.
# The toolchain files search this directory last.
python3 - "$XWIN" "$SRC" <<'EOF'
import os, re, sys
xwin, src = sys.argv[1], sys.argv[2]
dirs = [os.path.join(xwin, d) for d in ("crt/include", "sdk/include/ucrt", "sdk/include/um",
                                        "sdk/include/shared", "sdk/include/winrt")]
index = {}
for d in dirs:
    for root, _, files in os.walk(d):
        for f in files:
            index.setdefault(os.path.relpath(os.path.join(root, f), d).lower(), os.path.join(root, f))
names = set()
for top in ("src", "include", "thirdparty"):
    for root, _, files in os.walk(os.path.join(src, top)):
        for f in files:
            if f.endswith((".h", ".hpp", ".cpp", ".c", ".cc", ".inl")):
                text = open(os.path.join(root, f), errors="ignore").read()
                names.update(re.findall(r'#\s*include\s*<([A-Za-z0-9_./]+)>', text))
casefix = os.path.join(xwin, "..", "casefix")
for name in names:
    if name == name.lower() or any(os.path.exists(os.path.join(d, name)) for d in dirs):
        continue
    target = index.get(name.lower())
    link = os.path.join(casefix, name)
    if target and not os.path.lexists(link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(target, link)
EOF

# simde (an SDK submodule) stores an MMX value with a builtin that clang >= 23 rejects on
# MSVC's union __m64; take its plain store there instead.
python3 - "$SRC/thirdparty/simde/simde/x86/sse.h" <<'EOF'
import sys
path = sys.argv[1]
text = open(path).read()
old = "#elif HEDLEY_HAS_BUILTIN(__builtin_nontemporal_store) && ( \\\n      defined(SIMDE_ARM_NEON_A32V7_NATIVE) || defined(SIMDE_MIPS_LOONGSON_MMI_NATIVE)"
new = "#elif HEDLEY_HAS_BUILTIN(__builtin_nontemporal_store) && !defined(SIMDE_X86_MMX_USE_NATIVE_TYPE) && ( \\\n      defined(SIMDE_ARM_NEON_A32V7_NATIVE) || defined(SIMDE_MIPS_LOONGSON_MMI_NATIVE)"
if old in text:
    open(path, "w").write(text.replace(old, new, 1))
EOF

STEP="${1:-all}"
if [ "$STEP" != --game-only ]; then
# 1. The SDK for Windows: runtime, Xenos plugin (D3D12 and Vulkan), codegen tool.
cmake -S "$SRC" -B "$SRC/out/build/win-cross" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_STANDARD=23 -DREXGLUE_USE_VULKAN=ON -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/tools/cmake/windows-clang.cmake" > /dev/null
ninja -C "$SRC/out/build/win-cross" -j "$JOBS"
cmake --install "$SRC/out/build/win-cross" > /dev/null
# The OpenXR loader for the VR setting (docs/vr.md, "Packaging"), as the release workflow adds it.
openxr_tmp="$(mktemp -d)"
python3 "$ROOT/launcher/packaging/ci_fetch.py" openxr-loader-windows "$openxr_tmp" > /dev/null
cp "$openxr_tmp/x64/bin/openxr_loader.dll" "$PREFIX/bin/"
mkdir -p "$PREFIX/share/licenses/openxr-loader"
cp "$openxr_tmp/share/doc/openxr/LICENSE" "$PREFIX/share/licenses/openxr-loader/LICENSE.txt"
rm -rf "${openxr_tmp:?}"
fi
[ "$STEP" = --sdk-only ] && exit 0

# 2. The game.
# ReGPU (the optional plugin, FORZA_REGPU_PLUGIN) is a git submodule (FH1-ReImagined/ReGPU).
if [ ! -f "$ROOT/forza-recomp/thirdparty/ReGPU/CMakeLists.txt" ] && [ -e "$ROOT/.git" ]; then
    git -C "$ROOT" submodule update --init forza-recomp/thirdparty/ReGPU
fi
BUILD="$ROOT/forza-recomp/out/build/windows"
cmake -S "$ROOT/forza-recomp" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/tools/cmake/windows-clang-cl.cmake" \
    -DCMAKE_PREFIX_PATH="$PREFIX" -DCMAKE_FIND_ROOT_PATH="$PREFIX" \
    -Drexglue_DIR="$PREFIX/lib/cmake/rexglue" > /dev/null
ninja -C "$BUILD" -j "$JOBS"

# 3. Package: the game, its guest DLLs, the SDK's runtime DLLs and the package extractor, the Forza Live server and play.bat.
DIST="$ROOT/forza-recomp/out/dist/windows"
rm -rf "$DIST"
mkdir -p "$DIST/licenses"
cp "$BUILD"/forza.exe "$BUILD"/forza_*.dll "$DIST/"
cp "$BUILD/forza-telemetry.json" "$DIST/"
if [ -f "$BUILD/rexgpu-regpu.dll" ]; then  # ReGPU plugin (--gpu_plugin=regpu)
    cp "$BUILD/rexgpu-regpu.dll" "$DIST/"
    [ -f "$BUILD/d3dcompiler_47.dll" ] && cp "$BUILD/d3dcompiler_47.dll" "$DIST/"  # Wine and Proton lack it
    cp "$ROOT/forza-recomp/thirdparty/ReGPU/LICENSE" "$DIST/licenses/ReGPU.txt"
    cp "$ROOT/forza-recomp/thirdparty/fidelityfx-fsr1/LICENSE.txt" "$DIST/licenses/AMD-FidelityFX-FSR1.txt"
fi
for dll in "$PREFIX"/bin/*.dll; do
    case "$(basename "$dll")" in
        *d.dll | *rd.dll) ;;  # debug and RelWithDebInfo variants
        *) cp "$dll" "$DIST/" ;;
    esac
done
# Native NGX capability metadata and SDK notices must travel with the GPU DLL.
if [ -f "$PREFIX/bin/fh1-native-scene.json" ] && [ -f "$PREFIX/bin/rexgpu-xenos.dll" ]; then
    python3 "$ROOT/launcher/forza_launcher/native_scene_marker.py" \
        "$PREFIX/bin/fh1-native-scene.json" "$PREFIX/bin/rexgpu-xenos.dll" \
        "$DIST/rexgpu-xenos.dll" "$DIST/fh1-native-scene.json"
else
    rm -f "$DIST/fh1-native-scene.json"
fi
if [ -f "$PREFIX/bin/fh1-native-dlss.json" ]; then
    cp "$PREFIX/bin/fh1-native-dlss.json" "$DIST/"
    cp "$PREFIX/share/licenses/nvidia-dlss/LICENSE.txt" "$DIST/licenses/NVIDIA-DLSS.txt"
fi
if [ -f "$PREFIX/share/licenses/amd-fidelityfx-fsr3/LICENSE.txt" ]; then
    cp "$PREFIX/share/licenses/amd-fidelityfx-fsr3/LICENSE.txt" "$DIST/licenses/AMD-FidelityFX-FSR3.txt"
fi
cp "$ROOT/tools/xcontent_extract.py" "$ROOT/tools/xiso_extract.py" "$ROOT/tools/install_dlc.py" "$DIST/"
mkdir -p "$DIST/server"
# The Forza Live server: its entry point and the forza_live package (no tests).
cp "$ROOT/server/forza_live_server.py" "$DIST/server/"
cp -r "$ROOT/server/forza_live" "$DIST/server/"
find "$DIST/server" -name __pycache__ -prune -exec rm -rf {} +
if [ -f "$PREFIX/share/licenses/openxr-loader/LICENSE.txt" ]; then
    cp "$PREFIX/share/licenses/openxr-loader/LICENSE.txt" "$DIST/licenses/OpenXR-loader.txt"
fi
if [ -f "$PREFIX/share/licenses/smaa/LICENSE.txt" ]; then
    cp "$PREFIX/share/licenses/smaa/LICENSE.txt" "$DIST/licenses/SMAA.txt"
fi
cp "$ROOT/LICENSE" "$DIST/licenses/ForzaHorizon1-ReImagined.txt"
# The GPLv3 section 7 additional permission and the relicensing note, and the server's license.
cp "$ROOT/NOTICE.md" "$DIST/licenses/NOTICE.txt"
[ -f "$ROOT/server/LICENSE" ] && cp "$ROOT/server/LICENSE" "$DIST/licenses/forza-live.txt"
cp "$SRC/LICENSE" "$DIST/licenses/rexglue-sdk.txt"
[ -f "$PREFIX/licenses/SDL3/LICENSE.txt" ] && cp "$PREFIX/licenses/SDL3/LICENSE.txt" "$DIST/licenses/SDL3.txt"
# play.bat is the Windows play.sh (local Forza Live server, --offline); CRLF for cmd.exe.
sed 's/$/\r/' "$ROOT/tools/dist/play.bat" > "$DIST/play.bat"
commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
sed "s/@COMMIT@/$commit/" "$ROOT/tools/dist/README.windows.txt" | sed 's/$/\r/' > "$DIST/README.txt"
echo "Windows build: $DIST"
