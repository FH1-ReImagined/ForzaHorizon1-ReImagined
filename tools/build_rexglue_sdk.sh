#!/usr/bin/env bash
# Build the ReXGlue Xenos GPU plugin (librexgpu-xenos.so), the runtime and the codegen tool
# (rexglue) from ReIGlue, this project's fork of the ReXGlue SDK
# (https://github.com/FH1-ReImagined/ReIGlue), at the revision pinned below, and install them
# into an overlay of the nightly SDK so the forza build (its codegen step and the plugin it
# ships) and tools/run_forza.sh pick them up. Adapted from RecompiledHaloReach.
#
# Usage: tools/build_rexglue_sdk.sh [--source-only]
#   --source-only  only check out the SDK source (all tools/build_windows.sh needs)
#
# Why a fork: the SDK's Xenos emulation lacks many Xenia fixes; the first one found
# (in Halo: Reach) was the SPIR-V translator reading the texture-fetch exponent
# bias from fetch-constant word 4 instead of word 3, which darkens textures 256x.
# Upstream closed the report (rexglue/rexglue-sdk#456) without taking GPU changes.
# ReIGlue is the nightly's commit with one commit per change on top (they used to be
# patches/rexglue-sdk/, which docs/sdk_patches.md still describes).
#
# Environment:
#   REXSDK_NIGHTLY  installed nightly SDK the overlay is based on
#                   (default ~/rexglue-sdk-nightly/0.10.0.24/linux-amd64)
#   REXSDK_SRC      source checkout (default ~/rexglue-sdk-src-forza/sdk; a clone of upstream
#                   ReXGlue that the patch series was applied to is taken over as it is)
#   REXSDK_JOBS     parallel compile jobs (default 4; the build runs at nice 15)
#   REXSDK_PATCHED  overlay prefix to create
#                   (default ~/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64)
#   REIGLUE_URL     where to fetch ReIGlue from (default its GitHub repository)
#   REIGLUE_DEV     1: build REXSDK_SRC as it is checked out (SDK development on a ReIGlue
#                   branch), including its submodules; initialize/prepare those separately
#
# The defaults are this project's own clone and overlay: RecompiledHaloReach uses
# ~/rexglue-sdk-src and ~/rexglue-sdk-patched, and the two may differ.
#
# Only the plugin, the runtime and bin/rexglue are taken from this build; every other SDK
# file in the overlay is a symlink into the nightly, so they must be built from a ReIGlue
# revision based on the nightly's exact commit to stay ABI-compatible with it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK_COMMIT=bd833a2ab69d305f99ddf57418fc38ffec8af93d  # nightly 0.10.0.24 (nightly-20261002-bd833a2a)
# Includes the merged cockpit instrument raster fix (#419, ReIGlue PR #2),
# controller disconnect reporting (#3), digital keyboard taps (#4), 0364: asynchronous pipelines
# built across frame ends (#167, ReIGlue PR #7), and 0400: the small code model on Linux (#473,
# ReIGlue PR #10), 0406: direct scaled depth uploads on NVIDIA (#500,
# ReIGlue PR #11), and 0407: connect only to an already-loaded RenderDoc (#511,
# ReIGlue PR #12), and the D3D12 out-of-submission fence HRESULT correction
# (ReIGlue PR #14), and 0403/0404: racing wheel vibration rate and H-shifter neutral
# (#467, #468, ReIGlue PR #8), and 0409: the startup movies whole with the scene/HUD split
# at a 1080p output (#539), and 0411: resolves into readback_resolve_wait_range copied back
# with a GPU wait (photo mode's Big Shot, #552), and 0410: first-seen resolves of per-frame
# render targets copied back without a GPU wait (#536, ReIGlue PR #15), and 0421/0422: the log
# file rolls over at log_file_max_mb (50 MB) and the lines that filled players' logs are rarer
# (#609).
# A reviewed full commit can be pinned before a maintainer publishes a tag.
# When a tag is supplied, its target must still match this immutable commit.
REIGLUE_TAG=
REIGLUE_COMMIT=b9a8894f9ea25f1eff79cc051574e9f1b23c1354  # 0421/0422 (#609), on top of #15 (0410) and merged #20-#25 (0413-0419)
REIGLUE_URL="${REIGLUE_URL:-https://github.com/FH1-ReImagined/ReIGlue}"
NIGHTLY="${REXSDK_NIGHTLY:-$HOME/rexglue-sdk-nightly/0.10.0.24/linux-amd64}"
SRC="${REXSDK_SRC:-$HOME/rexglue-sdk-src-forza/sdk}"
OVERLAY="${REXSDK_PATCHED:-$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64}"
BREW=/home/linuxbrew/.linuxbrew

SOURCE_ONLY=0
[ "${1:-}" = --source-only ] && SOURCE_ONLY=1
[ "$SOURCE_ONLY" = 1 ] || [ -d "$NIGHTLY/lib" ] || { echo "nightly SDK not found at $NIGHTLY" >&2; exit 1; }

# The old applied series may be dirty relative to HEAD, but its files already match the
# target tree. Preserve those and unrelated untracked files; refuse other local work before
# changing HEAD, the index or any source. This also protects SDL development edits.
require_expected_changes() {
    local repo="$1" target="$2" label="$3" file expected actual
    while IFS= read -r -d '' file; do
        git -C "$repo" diff --cached --quiet "$target" -- "$file" ||
            { echo "$label has staged changes in $file; preserve them or use REIGLUE_DEV=1" >&2; return 1; }
    done < <(git -C "$repo" diff --cached --name-only -z HEAD)
    while IFS= read -r -d '' file; do
        # Target additions must also be checked for ignored untracked collisions. An
        # absent, untracked addition is normal and will be created by the checkout.
        if [ ! -e "$repo/$file" ] && [ ! -L "$repo/$file" ]; then
            git -C "$repo" ls-files --error-unmatch -- "$file" > /dev/null 2>&1 || continue
        fi
        expected="$(git -C "$repo" rev-parse --verify "$target:$file" 2>/dev/null || true)"
        if [ -z "$expected" ]; then
            # Untracked build/output files outside the target are left alone.
            git -C "$repo" ls-files --error-unmatch -- "$file" > /dev/null 2>&1 || continue
            [ -e "$repo/$file" ] || [ -L "$repo/$file" ] || continue
        fi
        actual="$(git -C "$repo" hash-object --path="$file" "$repo/$file" 2>/dev/null || true)"
        [ -n "$expected" ] && [ "$actual" = "$expected" ] ||
            { echo "$label has local changes in $file; preserve them or use REIGLUE_DEV=1" >&2; return 1; }
    done < <(git -C "$repo" diff --ignore-submodules --name-only -z HEAD; git -C "$repo" ls-files --others --exclude-standard -z; git -C "$repo" diff --diff-filter=A --name-only -z HEAD "$target")
}

if [ "${REIGLUE_DEV:-0}" = 1 ]; then
    [ -e "$SRC/.git" ] || { echo "REIGLUE_DEV=1 needs a ReIGlue checkout at $SRC" >&2; exit 1; }
    echo "building $SRC as checked out: $(git -C "$SRC" describe --always --dirty)"
else
    fresh=0
    if [ ! -e "$SRC/.git" ]; then
        # Checked out below (a clone --branch of an annotated tag warns that it is not a commit)
        git clone --quiet --no-checkout "$REIGLUE_URL" "$SRC"
        fresh=1
    fi
    if [ -n "$REIGLUE_TAG" ]; then
        git -C "$SRC" rev-parse --quiet --verify "refs/tags/$REIGLUE_TAG" > /dev/null ||
            git -C "$SRC" fetch --quiet "$REIGLUE_URL" "+refs/tags/$REIGLUE_TAG:refs/tags/$REIGLUE_TAG"
        target="$(git -C "$SRC" rev-parse "$REIGLUE_TAG^{commit}")"
        [ "$target" = "$REIGLUE_COMMIT" ] ||
            { echo "$REIGLUE_TAG resolves to $target, expected $REIGLUE_COMMIT" >&2; exit 1; }
    else
        [[ "$REIGLUE_COMMIT" =~ ^[0-9a-f]{40}$ ]] ||
            { echo "REIGLUE_COMMIT must be a full immutable commit" >&2; exit 1; }
        git -C "$SRC" cat-file -e "$REIGLUE_COMMIT^{commit}" 2>/dev/null ||
            git -C "$SRC" fetch --quiet "$REIGLUE_URL" "$REIGLUE_COMMIT"
        target="$(git -C "$SRC" rev-parse "$REIGLUE_COMMIT^{commit}")"
    fi
    git -C "$SRC" merge-base --is-ancestor "$SDK_COMMIT" "$target" ||
        { echo "$target is not based on the nightly's commit $SDK_COMMIT" >&2; exit 1; }
    # Check out the revision without touching files that already match it: HEAD and the index go to
    # the revision, then only the files whose content differs are rewritten. Files that already match
    # keep their timestamps, so a re-run changes no source file, and a clone that had the old
    # patch series applied (the same content) is taken over without rebuilding the SDK.
    [ "$fresh" = 1 ] || require_expected_changes "$SRC" "$target" SDK
    if [ "$fresh" = 1 ] || [ "$(git -C "$SRC" rev-parse HEAD)" != "$target" ]; then
        git -C "$SRC" update-ref --no-deref HEAD "$target"
        git -C "$SRC" read-tree "$target"
    fi
    git -C "$SRC" update-index -q --refresh > /dev/null || true
    changed="$(git -C "$SRC" diff-files --ignore-submodules --name-only | wc -l)"
    if [ "$changed" -gt 0 ]; then
        git -C "$SRC" diff-files -z --ignore-submodules --name-only |
            git -C "$SRC" checkout-index -f -z --stdin
    fi
    echo "SDK source at ${REIGLUE_TAG:-$target} ($changed file(s) updated)"
fi
[ "${REIGLUE_DEV:-0}" = 1 ] || git -C "$SRC" submodule update --init --recursive --depth 1 --quiet

# SDL (the SDK's thirdparty/sdl3 submodule, built into the runtime): ReIGlue keeps its changes
# as patches in patches/sdl3/, against the commit the SDK records for the submodule. Applied
# idempotently: the patched tree is built in a temporary index, then only the files whose
# content differs from it are rewritten (files that already match keep their timestamps).
apply_series() {
    local repo="$1" commit="$2" label="$3"
    shift 3
    local patched_index changed patch
    patched_index="$(mktemp)"
    GIT_INDEX_FILE="$patched_index" git -C "$repo" read-tree "$commit"
    for patch in "$@"; do
        [ -f "$patch" ] || continue  # an empty patch folder leaves its glob as it is
        if ! GIT_INDEX_FILE="$patched_index" git -C "$repo" apply --cached "$patch"; then
            echo "patch does not apply to $label $commit: $(basename "$patch")" >&2
            rm -f "$patched_index"; exit 1
        fi
    done
    if ! require_expected_changes "$repo" "$(GIT_INDEX_FILE="$patched_index" git -C "$repo" write-tree)" "$label"; then
        rm -f "$patched_index"; return 1
    fi
    GIT_INDEX_FILE="$patched_index" git -C "$repo" update-index -q --refresh > /dev/null || true
    changed="$(GIT_INDEX_FILE="$patched_index" git -C "$repo" diff-files --ignore-submodules --name-only)"
    if [ -z "$changed" ]; then
        echo "$label patches already applied ($#)"
    else
        while IFS= read -r file; do
            GIT_INDEX_FILE="$patched_index" git -C "$repo" checkout-index -f -- "$file"
            echo "patched: $label $file"
        done <<< "$changed"
    fi
    rm -f "$patched_index"
}
# FFmpeg (the XMA decoder in the runtime): the same for patches/ffmpeg/ (0413: the xmaframes
# decoder reports each frame's skip fields, which the XMA context needs to drop a wave's
# warm-up frame). An empty or missing patches/ffmpeg/ leaves the glob as it is and apply_series
# skips it, so then this only checks the submodule out at its recorded commit; an SDK without
# the submodule has nothing to patch.
if [ "${REIGLUE_DEV:-0}" != 1 ]; then
    apply_series "$SRC/thirdparty/sdl3" "$(git -C "$SRC" rev-parse "HEAD:thirdparty/sdl3")" SDL \
        "$SRC"/patches/sdl3/*.patch
    if ffmpeg_commit="$(git -C "$SRC" rev-parse --verify --quiet "HEAD:thirdparty/FFmpeg")"; then
        apply_series "$SRC/thirdparty/FFmpeg" "$ffmpeg_commit" FFmpeg "$SRC"/patches/ffmpeg/*.patch
    fi
fi
[ "$SOURCE_ONLY" = 0 ] || exit 0

# Building the SDK needs X11/XCB and Wayland headers (its UI module and SDL3
# require them). Hosts without them (immutable Fedora) can use Homebrew's:
#   brew install libx11 libxcb libxext libxfixes libxcursor libxi libxrandr \
#                libxscrnsaver libxrender libxtst xorgproto wayland systemd
#   (systemd supplies libudev.h: without it SDL is built without udev and sees no joystick)
cmake_args=()
if [ -d "$BREW/opt/xorgproto/include" ]; then
    xp="$BREW/opt/xorgproto"
    inc=""
    for pkg in xorgproto wayland libxcb libx11 libxau libxdmcp systemd; do
        inc+=" -I$BREW/opt/$pkg/include"
    done
    export PKG_CONFIG_PATH="$BREW/lib/pkgconfig:$BREW/share/pkgconfig:$xp/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    # The -L only serves this build's own librexruntime.so, which the plugin
    # links against but which is never shipped (the nightly's is used).
    cmake_args+=(-DCMAKE_PREFIX_PATH="$BREW" -DX11_X11_INCLUDE_PATH="$BREW/include"
                 "-DCMAKE_C_FLAGS=-march=x86-64-v2 $inc"
                 "-DCMAKE_CXX_FLAGS=-march=x86-64-v2 $inc"
                 "-DCMAKE_SHARED_LINKER_FLAGS=-L$BREW/lib")
fi
( cd "$SRC" && cmake --preset linux-amd64 "${cmake_args[@]}" > /dev/null )
nice -n 15 cmake --build "$SRC/out/build/linux-amd64" --config Release --target rexgpu-xenos rexglue rexruntime \
    --parallel "${REXSDK_JOBS:-4}"

built="$SRC/out/linux-amd64/Release/librexgpu-xenos.so"
[ -n "$built" ] || { echo "librexgpu-xenos.so not found in the build tree" >&2; exit 1; }

# Overlay: symlink everything from the nightly, then replace the plugin.
mkdir -p "$OVERLAY/lib"
for entry in "$NIGHTLY"/*; do
    case "$(basename "$entry")" in
        lib|bin) ;;
        *) ln -sfn "$entry" "$OVERLAY/$(basename "$entry")" ;;
    esac
done
for entry in "$NIGHTLY"/lib/*; do
    # The plugin and runtime are installed below (atomically); never swap them for a symlink.
    # lib/cmake is a directory of its own (below).
    case "$(basename "$entry")" in librexgpu-xenos.so|librexruntime.so|cmake) continue ;; esac
    ln -sfn "$entry" "$OVERLAY/lib/$(basename "$entry")"
done
# The SDK's CMake helpers come from ReIGlue too (0400: the code model a game's code is compiled
# with); the other package files are the nightly's. Older overlays linked lib/cmake to the
# nightly's: remove that link (only the link) before making the directory.
for dir in "$OVERLAY/lib/cmake" "$OVERLAY/lib/cmake/rexglue"; do
    [ -L "$dir" ] && rm "$dir"
    mkdir -p "$dir"
done
for entry in "$NIGHTLY"/lib/cmake/*; do
    [ "$(basename "$entry")" = rexglue ] || ln -sfn "$entry" "$OVERLAY/lib/cmake/$(basename "$entry")"
done
for entry in "$NIGHTLY"/lib/cmake/rexglue/*; do
    [ "$(basename "$entry")" = rexglue_helpers.cmake ] ||
        ln -sfn "$entry" "$OVERLAY/lib/cmake/rexglue/$(basename "$entry")"
done
helpers_new="$OVERLAY/lib/cmake/rexglue/.rexglue_helpers.cmake.new"
cp "$SRC/cmake/rexglue_helpers.cmake" "$helpers_new"
mv -f "$helpers_new" "$OVERLAY/lib/cmake/rexglue/rexglue_helpers.cmake"
echo "SDK CMake helpers: $OVERLAY/lib/cmake/rexglue/rexglue_helpers.cmake"
# Install by rename: overwriting a mapped .so in place can crash a running game.
plugin_new="$OVERLAY/lib/.librexgpu-xenos.new"
cp "$built" "$plugin_new"
mv -f "$plugin_new" "$OVERLAY/lib/librexgpu-xenos.so"
echo "patched plugin: $OVERLAY/lib/librexgpu-xenos.so"
# Keep this outside the nightly's symlinked share tree.
if [ -f "$SRC/out/linux-amd64/Release/SMAA-LICENSE.txt" ]; then
    mkdir -p "$OVERLAY/fh1-licenses"
    cp "$SRC/out/linux-amd64/Release/SMAA-LICENSE.txt" "$OVERLAY/fh1-licenses/SMAA.txt"
fi

# The runtime (ReIGlue 0028: faster memory protection queries, and others). Built on the
# nightly's commit, so it is a drop-in; give it the nightly's RUNPATH (its own is the build tree's).
runtime_new="$OVERLAY/lib/.librexruntime.new"
cp "$SRC/out/linux-amd64/Release/librexruntime.so" "$runtime_new"
rpath_script="$(mktemp)"
printf 'file(RPATH_SET FILE "%s" NEW_RPATH "$ORIGIN:$ORIGIN/../lib")\n' "$runtime_new" > "$rpath_script"
cmake -P "$rpath_script"
rm -f "$rpath_script"
mv -f "$runtime_new" "$OVERLAY/lib/librexruntime.so"
echo "patched runtime: $OVERLAY/lib/librexruntime.so"

# The OpenXR loader for the VR setting (docs/vr.md, "Packaging"): Khronos' OpenXR-SDK at a pinned
# release, Apache-2.0. The game opens it only when vr_mode is on; the launcher copies it next to
# the game, so the Linux package and the Flatpak (whose runtime has no loader) carry it.
OPENXR_VERSION=1.1.62
OPENXR_SHA256=dcc7c89b6af5d0364447debfe1b0437713bcedd0e8c0c2819c75ec6f546b393d
openxr_dir="$(dirname "$SRC")/openxr-sdk-$OPENXR_VERSION"
if [ ! -f "$openxr_dir/build/src/loader/libopenxr_loader.so.$OPENXR_VERSION" ]; then
    openxr_tar="$(dirname "$SRC")/openxr-sdk-$OPENXR_VERSION.tar.gz"
    if [ ! -f "$openxr_tar" ]; then
        curl -fsSL -o "$openxr_tar.part" \
            "https://codeload.github.com/KhronosGroup/OpenXR-SDK/tar.gz/refs/tags/release-$OPENXR_VERSION"
        mv "$openxr_tar.part" "$openxr_tar"
    fi
    echo "$OPENXR_SHA256  $openxr_tar" | sha256sum -c --quiet
    rm -rf "${openxr_dir:?}" && mkdir -p "$openxr_dir"
    tar -xzf "$openxr_tar" -C "$openxr_dir" --strip-components=1
    # The SDK's own arguments: its CMake finds X11 (the default presentation backend) as the SDK did.
    cmake -S "$openxr_dir" -B "$openxr_dir/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTS=OFF -DBUILD_API_LAYERS=OFF -DBUILD_CONFORMANCE_TESTS=OFF \
        -DBUILD_WITH_SYSTEM_JSONCPP=OFF -DDYNAMIC_LOADER=ON ${cmake_args[@]+"${cmake_args[@]}"} > /dev/null
    nice -n 15 cmake --build "$openxr_dir/build" --target openxr_loader --parallel "${REXSDK_JOBS:-4}"
fi
loader_new="$OVERLAY/lib/.libopenxr_loader.new"
cp "$openxr_dir/build/src/loader/libopenxr_loader.so.$OPENXR_VERSION" "$loader_new"
mv -f "$loader_new" "$OVERLAY/lib/libopenxr_loader.so.1"
mkdir -p "$OVERLAY/fh1-licenses"
cp "$openxr_dir/LICENSE" "$OVERLAY/fh1-licenses/OpenXR-SDK.txt"
echo "OpenXR loader: $OVERLAY/lib/libopenxr_loader.so.1"

# The codegen tool: ReIGlue changes the generated code too (0025: Xenon's dot-product
# overflow). Older overlays linked bin/ to the nightly's; it is a directory now.
[ -L "$OVERLAY/bin" ] && rm "$OVERLAY/bin"
mkdir -p "$OVERLAY/bin"
for entry in "$NIGHTLY"/bin/*; do
    [ "$(basename "$entry")" = rexglue ] || ln -sfn "$entry" "$OVERLAY/bin/$(basename "$entry")"
done
# The build tree's rexglue has that tree's absolute RUNPATH; give it the nightly's
# ($ORIGIN/../lib is the overlay's lib/, which links into the nightly).
tool_new="$OVERLAY/bin/.rexglue.new"
cp "$SRC/out/linux-amd64/Release/rexglue" "$tool_new"
rpath_script="$(mktemp)"
printf 'file(RPATH_SET FILE "%s" NEW_RPATH "$ORIGIN:$ORIGIN/../lib")\n' "$tool_new" > "$rpath_script"
cmake -P "$rpath_script"
rm -f "$rpath_script"
if cmp -s "$tool_new" "$OVERLAY/bin/rexglue"; then
    rm -f "$tool_new"
else
    mv -f "$tool_new" "$OVERLAY/bin/rexglue"
    # Code generated by another tool is stale. The forza build reruns codegen when the
    # tool is newer than its output, but codegen skips modules whose stamp fingerprint
    # (its inputs, not the tool) matches: drop the stamps.
    rm -f "$ROOT"/forza-recomp/generated/*/codegen.stamp
fi
"$OVERLAY/bin/rexglue" --help > /dev/null
echo "patched codegen: $OVERLAY/bin/rexglue"

# Stage it next to existing builds of the same SDK right away (the forza
# POST_BUILD step only re-copies it when forza relinks). Builds against other
# SDK versions keep their own plugin: it must match their librexruntime.so.
for build in "$ROOT"/forza-recomp/out/build/*/; do
    # Some build dirs have the plugin but no CMakeCache.txt (copies made for tests);
    # under set -e the sed would end the script before it reached the others.
    [ -f "$build/librexgpu-xenos.so" ] && [ -f "$build/CMakeCache.txt" ] || continue
    sdk_dir="$(sed -n 's/^rexglue_DIR:PATH=//p' "$build/CMakeCache.txt")"
    case "$sdk_dir" in
        "$NIGHTLY"/*|"$OVERLAY"/*)
            # Rename over it: running instances may have the old file mapped.
            cp "$OVERLAY/lib/librexgpu-xenos.so" "$build/.librexgpu-xenos.new"
            mv -f "$build/.librexgpu-xenos.new" "$build/librexgpu-xenos.so"
            echo "staged into $build" ;;
    esac
done
