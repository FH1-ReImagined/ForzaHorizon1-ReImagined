#!/usr/bin/env bash
# CI helper (release.yml, sdk-linux): everything the SDK build needs on a fresh Ubuntu 22.04 runner.
# The overlay is built on 22.04 so its binaries need glibc 2.35 only. 22.04 has no C++23 standard
# library headers (g++ 12) and CMake 3.22 (the SDK wants 3.25), so GCC 14 comes from below and
# CMake, ninja and LLVM are the launcher's pinned downloads.
# The first choice is GCC 14 built from GNU's sources for 22.04 (build_gcc14_jammy.sh, published by
# ci-toolchain-gcc.yml), unpacked into ~/gcc-14; the pinned clang uses its headers and libstdc++
# through a config file next to clang (see use_gcc_home). Without it, g++-14 comes from the
# ubuntu-toolchain-r PPA's packages: first the project's GitHub copy of them
# (ci-toolchain-mirror.yml), then the PPA, then Launchpad's file links (Launchpad can be
# unreachable for hours).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
export DEBIAN_FRONTEND=noninteractive
# APT with retries, and archive.ubuntu.com when the runner's Azure mirror stops resolving.
apt_get() { "$HERE/ci_apt.sh" "$@"; }
apt_get update
apt_get install -y software-properties-common ca-certificates curl xz-utils
# add-apt-repository asks api.launchpad.net for the PPA's signing key. When that stays unreachable,
# the packages come from Launchpad's file links below (librarian_debs) instead.
add_ppa() {
    local attempt
    for attempt in 1 2 3; do
        if $SUDO add-apt-repository --no-update -y ppa:ubuntu-toolchain-r/test; then
            return 0
        fi
        [ "$attempt" -lt 3 ] || break
        echo "add-apt-repository failed (attempt $attempt/3); retrying in $((attempt * 10)) seconds" >&2
        sleep "$((attempt * 10))"
    done
    echo "Could not add the toolchain PPA; using Launchpad's file links" >&2
    return 1
}
# Ubuntu 24.04 and newer ship g++-14 in the archive itself: no PPA (PR checks run there, so they
# don't depend on Launchpad; release builds stay on 22.04 for the glibc 2.35 overlay).
native=0
os_release="${CI_OS_RELEASE:-/etc/os-release}"
if [ -r "$os_release" ]; then
    os_id="$(. "$os_release"; echo "${ID:-}")"
    os_major="$(. "$os_release"; echo "${VERSION_ID%%.*}")"
    if [ "$os_id" = ubuntu ] && [ "$os_major" -ge 24 ] 2>/dev/null; then native=1; fi
fi
# ppa.launchpadcontent.net can answer 503 for hours (the index, the packages or both) while the
# older ppa.launchpad.net name still serves the same PPA (APT checks its signature either way).
old_ppa_host() {
    echo "The toolchain PPA failed; trying ppa.launchpad.net" >&2
    $SUDO sed -i 's#https://ppa.launchpadcontent.net/#http://ppa.launchpad.net/#' \
        /etc/apt/sources.list.d/ubuntu-toolchain-r-ubuntu-test-*.list
    apt_get update
}
# Only when the GitHub copy did not do: adding the PPA asks Launchpad (and waits on it when it is down).
ppa=0
use_ppa() {
    ppa=1
    if add_ppa; then apt_get update; else ppa=0; fi
    # APT only warns when an index fails, and a missing package name is read as a pattern, so look
    # for g++-14 itself.
    if [ "$ppa" = 1 ] && ! apt-cache show g++-14 2>/dev/null | grep -x "Package: g++-14" >/dev/null; then
        old_ppa_host || true
    fi
}
# Without the PPA, "install g++-14" matches clang-14 and the like instead of failing, so each
# attempt counts only once the C++23 headers are there.
have_headers() { [ -d "${CI_CXX_HEADERS:-/usr/include/c++/14}" ]; }
# The packages in ci_toolchain_debs.txt, one per line ("#" starts a comment; CRLF is fine).
toolchain_debs() {
    local name
    while IFS= read -r name || [ -n "$name" ]; do
        name="${name%$'\r'}"
        case "$name" in ''|'#'*) continue ;; esac
        printf '%s\n' "$name"
    done < "$HERE/ci_toolchain_debs.txt"
}
# First choice on 22.04: the same packages from the release ci-toolchain-jammy-gcc14, which
# ci-toolchain-mirror.yml fills from the PPA. Each file must match its SHA-256 in
# ci_toolchain_sha256sums.txt, checked into this repository, so a changed release file is never
# installed. GitHub doesn't keep + and ~ in release file names: the copy spells them plus and tilde.
MIRROR_URL="${CI_TOOLCHAIN_MIRROR:-https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/releases/download/ci-toolchain-jammy-gcc14}"
asset_name() {
    local name="${1//+/plus}"
    printf '%s\n' "${name//\~/tilde}"
}
pinned_sum() {
    local sum file
    while read -r sum file; do
        file="${file%$'\r'}"
        if [ "$file" = "$1" ]; then printf '%s\n' "$sum"; return 0; fi
    done < "$HERE/ci_toolchain_sha256sums.txt"
    return 1
}
github_debs() {
    local name asset sum names=() sums=()
    [ -r "$HERE/ci_toolchain_sha256sums.txt" ] || return 1
    while IFS= read -r name; do
        asset="$(asset_name "${name}_amd64.deb")"
        if ! sum="$(pinned_sum "$asset")"; then
            echo "ci_toolchain_sha256sums.txt has no checksum for $asset; skipping the GitHub copy" >&2
            return 1
        fi
        names+=("$asset"); sums+=("$sum")
    done < <(toolchain_debs)
    echo "Downloading the toolchain packages from the project's GitHub copy" >&2
    debs="$(mktemp -d)"
    local i
    for i in "${!names[@]}"; do
        # --retry leaves a 404 alone: a copy that isn't there yet costs one request.
        if ! curl -fsSL --connect-timeout 20 --retry 3 --retry-connrefused \
                -o "$debs/${names[$i]}" "$MIRROR_URL/${names[$i]}"; then
            echo "Could not download ${names[$i]} from the GitHub copy; using the toolchain PPA" >&2
            return 1
        fi
        if ! printf '%s  %s\n' "${sums[$i]}" "$debs/${names[$i]}" | sha256sum -c --quiet - >&2; then
            echo "${names[$i]} from the GitHub copy does not match ci_toolchain_sha256sums.txt; using the toolchain PPA" >&2
            return 1
        fi
    done
}
# GCC 14 from GNU's sources (build_gcc14_jammy.sh): the release's tarball, installed only when it
# matches its line in ci_toolchain_sha256sums.txt. It unpacks to GCC_HOME and needs nothing from APT.
GCC_TARBALL=gcc-14.3.0-jammy-x86_64.tar.xz
GCC_HOME="${CI_GCC_HOME:-$HOME/gcc-14}"
GCC_INSTALL_DIR="$GCC_HOME/lib/gcc/x86_64-linux-gnu/14.3.0"
GCC_HEADERS="$GCC_HOME/include/c++/14.3.0"
LLVM_HOME="${CI_LLVM_HOME:-$HOME/llvm}"
github_gcc() {
    local sum tmp
    [ -r "$HERE/ci_toolchain_sha256sums.txt" ] || return 1
    if ! sum="$(pinned_sum "$GCC_TARBALL")"; then
        echo "ci_toolchain_sha256sums.txt has no checksum for $GCC_TARBALL; using the g++-14 packages" >&2
        return 1
    fi
    # Already unpacked by an earlier step of the job (the same pinned file).
    if [ -f "$GCC_HOME/.fh1-sha256" ] && [ "$(cat "$GCC_HOME/.fh1-sha256")" = "$sum" ] &&
            [ -d "$GCC_HEADERS" ]; then
        return 0
    fi
    echo "Downloading GCC 14 (built from GNU's sources) from the project's GitHub release" >&2
    tmp="$(mktemp -d)"
    if ! curl -fsSL --connect-timeout 20 --retry 3 --retry-connrefused \
            -o "$tmp/$GCC_TARBALL" "$MIRROR_URL/$GCC_TARBALL"; then
        echo "Could not download $GCC_TARBALL; using the g++-14 packages" >&2
        rm -rf "$tmp"; return 1
    fi
    if ! printf '%s  %s\n' "$sum" "$tmp/$GCC_TARBALL" | sha256sum -c --quiet - >&2; then
        echo "$GCC_TARBALL does not match ci_toolchain_sha256sums.txt; using the g++-14 packages" >&2
        rm -rf "$tmp"; return 1
    fi
    rm -rf "$GCC_HOME" && mkdir -p "$GCC_HOME"
    if ! tar -xJf "$tmp/$GCC_TARBALL" -C "$GCC_HOME" --strip-components=1 || [ ! -d "$GCC_HEADERS" ]; then
        echo "$GCC_TARBALL did not unpack the C++23 headers; using the g++-14 packages" >&2
        rm -rf "$tmp" "$GCC_HOME"; return 1
    fi
    printf '%s\n' "$sum" > "$GCC_HOME/.fh1-sha256"
    rm -rf "$tmp"
}
# Every clang in the job compiles and links with GCC_HOME's headers, libstdc++ and libgcc: clang
# reads clang.cfg / clang++.cfg next to its binary on every run (CMake's and the release.yml steps'
# alike). --gcc-install-dir also puts GCC_HOME/lib64 (libstdc++.so) on the link path. Programs built
# here and run during the job (tests, the SDK's build-time tools) need GCC_HOME's libstdc++ at run
# time: 22.04's is GCC 12's, which lacks GLIBCXX_3.4.31 and newer. The overlay doesn't ship it (the
# player's system libstdc++ is used, as with the PPA's g++-14), and no RUNPATH points at GCC_HOME.
# The variables go to $GITHUB_ENV for the job's later steps.
use_gcc_home() {
    local flags="--gcc-install-dir=$GCC_INSTALL_DIR"
    printf '%s\n' "$flags" > "$GCC_HOME/clang.cfg"
    if [ -d "$LLVM_HOME/bin" ]; then
        cp "$GCC_HOME/clang.cfg" "$LLVM_HOME/bin/clang.cfg"
        cp "$GCC_HOME/clang.cfg" "$LLVM_HOME/bin/clang++.cfg"
    fi
    export CI_CXX_HEADERS="$GCC_HEADERS"
    export LD_LIBRARY_PATH="$GCC_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    if [ -n "${GITHUB_ENV:-}" ]; then
        {
            echo "CI_GCC_HOME=$GCC_HOME"
            echo "CI_CXX_HEADERS=$GCC_HEADERS"
            echo "CI_CLANG_GCC_FLAGS=$flags"
            echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
        } >> "$GITHUB_ENV"
    fi
    echo "Using GCC 14 from $GCC_HOME ($flags)" >&2
}
# Last resort when both PPA hosts are down: Launchpad's file links for the exact packages.
librarian_debs() {
    echo "The toolchain PPA is unreachable; downloading its packages from Launchpad's files" >&2
    debs="$(mktemp -d)"
    local name
    while IFS= read -r name; do
        curl -fsSL --connect-timeout 20 --retry 3 --retry-all-errors -o "$debs/${name}_amd64.deb" \
            "https://launchpad.net/~ubuntu-toolchain-r/+archive/ubuntu/test/+files/${name}_amd64.deb" || return 1
    done < <(toolchain_debs)
}
install_packages() {
    if [ "$native" = 1 ]; then
        if apt_get install -y "$@" && have_headers; then return 0; fi
        echo "g++-14 did not install the C++23 headers from the Ubuntu archive" >&2
        exit 1
    fi
    if github_gcc; then
        # The rest of the list from the Ubuntu archive; g++-14 is GCC_HOME. In its place what it
        # brought along: the C library's headers and binutils (clang links with its ld), which the
        # runner has and a fresh image lacks.
        local rest=() pkg
        for pkg in "$@"; do
            if [ "$pkg" = g++-14 ]; then rest+=(libc6-dev binutils); else rest+=("$pkg"); fi
        done
        if apt_get install -y "${rest[@]}"; then gcc_home=1; return 0; fi
        echo "The other build packages did not install" >&2
        exit 1
    fi
    if github_debs; then
        if apt_get install -y "$debs"/*.deb "$@" && have_headers; then return 0; fi
        echo "The GitHub copy's packages did not install the C++23 headers; using the toolchain PPA" >&2
    fi
    use_ppa
    if [ "$ppa" = 1 ]; then
        if apt_get install -y "$@" && have_headers; then return 0; fi
        old_ppa_host || true
        if apt_get install -y "$@" && have_headers; then return 0; fi
    fi
    if librarian_debs && apt_get install -y "$debs"/*.deb "$@" && have_headers; then return 0; fi
    echo "g++-14 did not install the C++23 headers; is the toolchain PPA reachable?" >&2
    exit 1
}
# A cached SDK overlay still needs C++23 headers for the stock-header HUD check.
# Avoid installing the SDK's complete build dependencies for that compile alone.
gcc_home=0
if [ "${1:-}" = --compiler-only ]; then
    install_packages g++-14
    [ "$gcc_home" = 0 ] || use_gcc_home
    exit 0
fi
# The -dev packages of PulseAudio and ALSA (and D-Bus, fribidi, libthai) matter at run time too: SDL3 inside
# the runtime only gets those audio and input backends if its headers are present when it is built;
# without them the game finds no audio device and runs without sound (before SDK patch 0103 it stopped).
install_packages g++-14 make ninja-build python3 git patchelf pkg-config \
    libx11-dev libx11-xcb-dev libxcb1-dev libxext-dev libxfixes-dev libxcursor-dev libxi-dev \
    libxrandr-dev libxss-dev libxrender-dev libxtst-dev x11proto-dev libwayland-dev \
    libxkbcommon-dev libdrm-dev libgl1-mesa-dev libvulkan-dev \
    libpulse-dev libasound2-dev libdbus-1-dev libfribidi-dev libthai-dev
python3 "$HERE/ci_fetch.py" cmake-linux "$HOME/cmake" --strip 1
python3 "$HERE/ci_fetch.py" llvm-linux "$LLVM_HOME" --strip 1
[ "$gcc_home" = 0 ] || use_gcc_home
python3 "$HERE/ci_fetch.py" sdk-linux "$HOME/rexglue-sdk-nightly/0.10.0.24"
