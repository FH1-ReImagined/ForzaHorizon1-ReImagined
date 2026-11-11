# Cross build for ARM64 Linux (aarch64-linux-gnu) with clang and lld from an x86-64 Linux host,
# against an aarch64 sysroot (docs/arm64_linux.md). Used for the game and for ReIGlue:
#   cmake -S forza-recomp -B forza-recomp/out/build/linux-arm64 -G Ninja \
#       -DCMAKE_TOOLCHAIN_FILE=tools/cmake/linux-aarch64-clang.cmake \
#       -DFORZA_AARCH64_SYSROOT=/path/to/sysroot ...
# The sysroot needs the same development packages as a native build (the SDK's CI list).
# A cross build compiles the generated code a native codegen run made (CMAKE_CROSSCOMPILING).

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(FORZA_AARCH64_SYSROOT "$ENV{FORZA_AARCH64_SYSROOT}" CACHE PATH "aarch64 Linux sysroot")
if(NOT FORZA_AARCH64_SYSROOT OR NOT EXISTS "${FORZA_AARCH64_SYSROOT}/usr/include")
    message(FATAL_ERROR "FORZA_AARCH64_SYSROOT must name an aarch64 Linux sysroot "
                        "(got '${FORZA_AARCH64_SYSROOT}')")
endif()
set(CMAKE_SYSROOT "${FORZA_AARCH64_SYSROOT}")

set(CMAKE_C_COMPILER clang)
set(CMAKE_CXX_COMPILER clang++)
set(CMAKE_C_COMPILER_TARGET aarch64-linux-gnu)
set(CMAKE_CXX_COMPILER_TARGET aarch64-linux-gnu)
set(CMAKE_ASM_COMPILER_TARGET aarch64-linux-gnu)
set(CMAKE_AR llvm-ar)
set(CMAKE_RANLIB llvm-ranlib)
set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "-fuse-ld=lld")

# pkg-config (SDL's Wayland, PipeWire, ...) reads the sysroot's .pc files.
set(ENV{PKG_CONFIG_SYSROOT_DIR} "${CMAKE_SYSROOT}")
set(ENV{PKG_CONFIG_LIBDIR}
    "${CMAKE_SYSROOT}/usr/lib/aarch64-linux-gnu/pkgconfig:${CMAKE_SYSROOT}/usr/lib/pkgconfig:${CMAKE_SYSROOT}/usr/share/pkgconfig")
set(ENV{PKG_CONFIG_PATH} "")

# Test programs and build-time tools of the target (ReIGlue's rexglue recompile-tests) run
# under qemu-user: binfmt_misc, or this emulator with the sysroot as its library prefix.
find_program(FORZA_AARCH64_EMULATOR NAMES qemu-aarch64-static qemu-aarch64)
if(FORZA_AARCH64_EMULATOR)
    set(CMAKE_CROSSCOMPILING_EMULATOR "${FORZA_AARCH64_EMULATOR};-L;${CMAKE_SYSROOT}")
endif()

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)
