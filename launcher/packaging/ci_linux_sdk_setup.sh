#!/usr/bin/env bash
# CI helper (release.yml, sdk-linux): everything the SDK build needs on a fresh Ubuntu 22.04 runner.
# The overlay is built on 22.04 so its binaries need glibc 2.35 only. 22.04 has no C++23 standard
# library headers (g++ 12) and CMake 3.22 (the SDK wants 3.25), so g++-14 comes from the
# ubuntu-toolchain-r PPA and CMake, ninja and LLVM are the launcher's pinned downloads.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
export DEBIAN_FRONTEND=noninteractive
# APT with retries, and archive.ubuntu.com when the runner's Azure mirror stops resolving.
apt_get() { "$HERE/ci_apt.sh" "$@"; }
apt_get update
apt_get install -y software-properties-common ca-certificates curl
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
ppa=0
if [ "$native" = 0 ]; then
    ppa=1
    if add_ppa; then apt_get update; else ppa=0; fi
fi
# ppa.launchpadcontent.net can answer 503 for hours (the index, the packages or both) while the
# older ppa.launchpad.net name still serves the same PPA (APT checks its signature either way).
old_ppa_host() {
    echo "The toolchain PPA failed; trying ppa.launchpad.net" >&2
    $SUDO sed -i 's#https://ppa.launchpadcontent.net/#http://ppa.launchpad.net/#' \
        /etc/apt/sources.list.d/ubuntu-toolchain-r-ubuntu-test-*.list
    apt_get update
}
# APT only warns when an index fails, and a missing package name is read as a pattern, so look
# for g++-14 itself.
if [ "$ppa" = 1 ] && ! apt-cache show g++-14 2>/dev/null | grep -x "Package: g++-14" >/dev/null; then
    old_ppa_host || true
fi
# Without the PPA, "install g++-14" matches clang-14 and the like instead of failing, so each
# attempt counts only once the C++23 headers are there.
have_headers() { [ -d "${CI_CXX_HEADERS:-/usr/include/c++/14}" ]; }
# Last resort when both PPA hosts are down: Launchpad's file links for the exact packages.
librarian_debs() {
    echo "The toolchain PPA is unreachable; downloading its packages from Launchpad's files" >&2
    debs="$(mktemp -d)"
    local name
    while IFS= read -r name || [ -n "$name" ]; do
        name="${name%$'\r'}"
        case "$name" in ''|'#'*) continue ;; esac
        curl -fsSL --connect-timeout 20 --retry 3 --retry-all-errors -o "$debs/${name}_amd64.deb" \
            "https://launchpad.net/~ubuntu-toolchain-r/+archive/ubuntu/test/+files/${name}_amd64.deb" || return 1
    done < "$HERE/ci_toolchain_debs.txt"
}
install_packages() {
    if [ "$native" = 1 ]; then
        if apt_get install -y "$@" && have_headers; then return 0; fi
        echo "g++-14 did not install the C++23 headers from the Ubuntu archive" >&2
        exit 1
    fi
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
if [ "${1:-}" = --compiler-only ]; then
    install_packages g++-14
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
python3 "$HERE/ci_fetch.py" llvm-linux "$HOME/llvm" --strip 1
python3 "$HERE/ci_fetch.py" sdk-linux "$HOME/rexglue-sdk-nightly/0.10.0.24"
