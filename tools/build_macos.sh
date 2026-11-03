#!/usr/bin/env bash
# Experimental Apple Silicon build. See docs/macos.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE=all
TU=0
for arg in "$@"; do
    case "$arg" in
        --sdk-only) MODE=sdk ;;
        --game-only) MODE=game ;;
        --host-check) MODE=host ;;
        --gpu-check) MODE=gpu ;;
        --tu4) TU=4 ;;
        *) echo "Usage: $0 [--sdk-only|--game-only|--host-check|--gpu-check] [--tu4]" >&2; exit 2 ;;
    esac
done
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "This experimental build requires native Apple Silicon macOS." >&2
    exit 1
fi
for tool in cmake ninja git xcrun; do
    command -v "$tool" >/dev/null || { echo "Missing $tool (see docs/macos.md)." >&2; exit 1; }
done
SDK="${FORZA_MACOS_SDK:-$ROOT/forza-recomp/out/macos-sdk}"
SRC="${REXSDK_SRC:-$ROOT/forza-recomp/out/macos-sdk-src}"
JOBS="${REXSDK_JOBS:-6}"

if [ "$MODE" = all ] || [ "$MODE" = sdk ]; then
    # Reuse the pinned ReIGlue checkout (tools/build_rexglue_sdk.sh), then build the whole SDK.
    # A complete install keeps codegen, runtime, headers and GPU plugin together.
    REXSDK_SRC="$SRC" "$ROOT/tools/build_rexglue_sdk.sh" --source-only
    # Shallow checkouts omit the v* ancestor used by the SDK's version generator.
    if [ "$(git -C "$SRC" rev-parse --is-shallow-repository)" = true ]; then
        git -C "$SRC" fetch --unshallow --tags --recurse-submodules=no origin
    fi
    (
        cd "$SRC"
        cmake --preset mac-arm64 -DREXGLUE_USE_VULKAN=ON \
            -DCMAKE_CONFIGURATION_TYPES=Release -DCMAKE_DEFAULT_BUILD_TYPE=Release \
            -DCMAKE_INSTALL_PREFIX="$SDK"
    )
    cmake --build "$SRC/out/build/mac-arm64" --config Release --target install --parallel "$JOBS"
fi
[ "$MODE" != sdk ] || exit 0
if [ ! -x "$SDK/bin/rexglue" ]; then
    echo "SDK not found at $SDK; run $0 --sdk-only first (or set FORZA_MACOS_SDK)." >&2
    exit 1
fi

if [ "$MODE" = host ] || [ "$MODE" = gpu ]; then
    BUILD="$ROOT/forza-recomp/out/build/macos-host-$TU"
    cmake -S "$ROOT/tools/cmake/macos-host-check" -B "$BUILD" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$SDK" -DFORZA_TITLE_UPDATE="$TU" \
        -Drexglue_DIR="$SDK/lib/cmake/rexglue" -DFORZA_NATIVE_PAUSE=ON
    if [ "$MODE" = gpu ]; then
        cmake --build "$BUILD" --target forza_macos_gpu_check --parallel "$JOBS"
        for lib in librexruntime.dylib librexgpu-xenos.dylib; do
            cmake -E copy_if_different "$SDK/lib/$lib" "$BUILD/$lib"
        done
        REX_VULKAN_SDK="$SDK" "$BUILD/forza_macos_gpu_check"
    else
        cmake --build "$BUILD" --target forza_host_check --parallel "$JOBS"
    fi
    exit 0
fi

GAME_VERSION=game
[ "$TU" != 4 ] || GAME_VERSION=tu4
if [ ! -f "$ROOT/extracted/$GAME_VERSION/default.xex" ]; then
    echo "Missing extracted/$GAME_VERSION/default.xex; extract your game first (docs/macos.md)." >&2
    exit 1
fi
BUILD="$ROOT/forza-recomp/out/build/macos-$TU"
cmake -S "$ROOT/forza-recomp" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$SDK" \
    -Drexglue_DIR="$SDK/lib/cmake/rexglue" \
    -DFORZA_REXGLUE="$SDK/bin/rexglue" \
    -DFORZA_XENOS_PLUGIN="$SDK/lib/librexgpu-xenos.dylib" -DFORZA_TITLE_UPDATE="$TU"
cmake --build "$BUILD" --parallel "$JOBS"
# SDK changes need staging even when the game did not relink.
for lib in librexruntime.dylib librexgpu-xenos.dylib; do
    cmake -E copy_if_different "$SDK/lib/$lib" "$BUILD/$lib"
done
echo "Built $BUILD/forza. See docs/macos.md for an isolated offline launch."
