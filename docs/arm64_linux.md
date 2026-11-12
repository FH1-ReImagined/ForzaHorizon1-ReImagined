# ARM64 Linux

Status: **builds, not yet played.** The game has run on ARM64 before, on Apple Silicon
([macos.md](macos.md)), but no ARM64 Linux machine with a Vulkan GPU has booted it yet. This is
step 1 of the Android study ([#788](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/788)).

## What is checked

- **ReIGlue CI** (`build-linux-aarch64.yaml`, on every PR, GitHub's `ubuntu-24.04-arm` runners):
  the SDK built with its tests, then ctest. That covers the unit tests, the 0xE0000000 view at
  4, 16 and 64 KB granularity, and the PPC instruction tests with the FMA cases, which run
  recompiled guest code on NEON: 1824 tests, 1588 of them PPC cases, all passing on ReIGlue
  main (2026-11-12). Four calendar tests are left out: they fail on x86-64 Linux too
  (libstdc++).
- **This repository's CI** (`arm64-linux.yml`, on PRs that touch the game's sources, its CMake or
  the SDK pin, on `ubuntu-24.04-arm`): the pinned ReIGlue built and installed natively, then
  every host source in `forza-recomp/src` compiled against it for the disc and TU4
  (`tools/cmake/linux-host-check`). It has no game files, so the generated code is not compiled
  there.
- **Cross-built here.** The SDK, its tests and the whole TU4 game were built for aarch64 with
  clang and an Ubuntu 24.04 arm64 sysroot. Under qemu-aarch64 the SDK tests pass: 1801 of 1805,
  and the four that fail are those calendar tests.

## Page sizes

ARM64 Linux kernels use 4, 16 or 64 KB pages: 16 KB on Asahi and Android 15+ devices, 64 KB on
some server kernels. Every ARM64 build maps the guest's 0xE0000000 view the same way whatever the
page size (ReIGlue 0500, [sdk_patches.md](sdk_patches.md)), so one binary runs on all of them.

An x86-64 build on a kernel with pages over 4 KB refuses to start and says why. It used to run
with that memory 4 KB off.

## Building

There is no nightly SDK for linux-arm64, so the build uses a complete ReIGlue install. The game's
CMake picks the `linux-arm64` platform when `CMAKE_SYSTEM_PROCESSOR` is `aarch64`.

Both ways below start from the game's checkout and use two variables: `GAME`, the checkout's
absolute path, and `SDK`, the folder ReIGlue is installed into. `tools/build_rexglue_sdk.sh
--source-only` checks ReIGlue out at the pinned commit into `REXSDK_SRC` and patches its SDL and
FFmpeg; it builds nothing.

```sh
export GAME="$PWD" SDK="$HOME/reiglue-arm64" REXSDK_SRC="$HOME/reiglue-src"
tools/build_rexglue_sdk.sh --source-only
```

**On an ARM64 Linux machine:**

1. Build and install ReIGlue:

   ```sh
   cd "$REXSDK_SRC"
   cmake --preset linux-arm64 -DCMAKE_CONFIGURATION_TYPES=Release -DCMAKE_INSTALL_PREFIX="$SDK"
   cmake --build out/build/linux-arm64 --config Release --target install
   cd "$GAME"
   ```

2. Configure and build the game:

   ```sh
   cmake -S forza-recomp -B forza-recomp/out/build/linux-arm64 -G Ninja -DCMAKE_BUILD_TYPE=Release \
       -DCMAKE_PREFIX_PATH="$SDK" -DFORZA_REXGLUE="$SDK/bin/rexglue" \
       -DFORZA_XENOS_PLUGIN="$SDK/lib/librexgpu-xenos.so" -DFORZA_TITLE_UPDATE=4
   ninja -C forza-recomp/out/build/linux-arm64
   ```

   The packages needed are the ones the SDK's CI installs (and `.github/workflows/arm64-linux.yml`
   here).

**Cross build from x86-64.** Use `tools/cmake/linux-aarch64-clang.cmake` (clang and lld) with an
aarch64 sysroot that has the same packages, for example an Ubuntu 24.04 arm64 root filesystem.
CMake looks for a relative toolchain file in the build folder, so give it the absolute path:

```sh
export FORZA_AARCH64_SYSROOT=/path/to/arm64-root
# ReIGlue:
cd "$REXSDK_SRC"
cmake --preset linux-arm64 -B out/build/linux-arm64-cross \
    -DCMAKE_TOOLCHAIN_FILE="$GAME/tools/cmake/linux-aarch64-clang.cmake" \
    -DCMAKE_CONFIGURATION_TYPES=Release -DCMAKE_INSTALL_PREFIX="$SDK"
cmake --build out/build/linux-arm64-cross --config Release --target install
cd "$GAME"
# The game, after a native codegen run on x86-64 (the generated code is the same for every CPU):
cmake -S forza-recomp -B forza-recomp/out/build/linux-arm64-cross -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$GAME/tools/cmake/linux-aarch64-clang.cmake" -DCMAKE_PREFIX_PATH="$SDK" \
    -DFORZA_REXGLUE="$SDK/bin/rexglue" -DFORZA_XENOS_PLUGIN="$SDK/lib/librexgpu-xenos.so" \
    -DFORZA_TITLE_UPDATE=4
ninja -C forza-recomp/out/build/linux-arm64-cross
```

A cross build never runs codegen itself (`CMAKE_CROSSCOMPILING`). With qemu-user installed, the
toolchain runs the target's test programs under `qemu-aarch64 -L <sysroot>`, and with
`QEMU_LD_PREFIX=<sysroot>` binfmt runs the build's own aarch64 tools: ReIGlue's
`-DREXGLUE_BUILD_TESTS=ON` builds and runs its tests that way. The TU4 game built like this links
with 82 MB of aarch64 code (84 MB on x86-64).

## Still open

- Boot it on real hardware with a Vulkan GPU (an Asahi Mac, RK3588, Snapdragon).
- The launcher has not been tried on ARM64 (its PySide6 dependency has aarch64 wheels).
- Android needs more (fibers without ucontext, robust mutexes, SIGSEGV chaining): see #788.
