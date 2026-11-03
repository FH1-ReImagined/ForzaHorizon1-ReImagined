#!/usr/bin/env bash
# Run the Windows build (forza-recomp/out/dist/windows, tools/build_windows.sh) on Linux under Wine,
# with ReGPU (--gpu_plugin=regpu: Direct3D 12 through vkd3d-proton) or the SDK's Vulkan backend
# (winevulkan) for comparison. A test bed for ReGPU on Linux and the Steam Deck: see
# docs/linux-vkd3d-proton.md in https://github.com/FH1-ReImagined/ReGPU for what works.
#
# Usage:
#   tools/run_regpu_wine.sh setup PROTON_DIR    a Wine prefix from a copy of a Proton install
#                                               (e.g. ".../steamapps/common/Proton 11.0"; only read)
#   tools/run_regpu_wine.sh run [--vulkan] [forza flags...]
#                                               start forza.exe; ReGPU unless --vulkan
#
# setup copies PROTON_DIR/files into $REGPU_WINE_DIR/proton, makes the prefix, installs Proton's
# vkd3d-proton (d3d12, d3d12core) and DXVK's dxgi into it, turns Wine's audio drivers off, and puts
# Microsoft's d3dcompiler_47.dll beside forza.exe (Wine's own can't compile ReGPU's shaders): the
# Windows SDK 10.0.26100 redistributable (Redist\D3D\x64), downloaded from Microsoft and checked
# against pinned SHA-256s. (winetricks' d3dcompiler_47 is the Windows 8.1 SDK's 6.3.9600, which has
# no shader model 5.1.)
#
# run passes the game data and a user data root of your choice through forza flags, as on Windows
# (--game_data_root, --update_data_root, --user_data_root, ...); Unix paths in those flags are turned
# into Wine's Z: paths. ReGPU's log (regpu.log) goes to $REGPU_WINE_LOG_DIR. The game's own scripted
# input (FORZA_AUTOPRESS_FIFO, a plain file the game tails on Windows) and RECOMP_PERF work as on Linux;
# Unix paths in those variables are converted too.
#
# Environment:
#   REGPU_WINE_DIR      prefix, Proton copy and Wine's XDG dirs (default ~/.cache/forza-regpu-wine)
#   REGPU_WINE_LOG_DIR  where regpu.log goes (default $REGPU_WINE_DIR/logs)
#   FORZA_DIST          the Windows build (default forza-recomp/out/dist/windows)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${REGPU_WINE_DIR:-$HOME/.cache/forza-regpu-wine}"
DIST="${FORZA_DIST:-$ROOT/forza-recomp/out/dist/windows}"
LOGS="${REGPU_WINE_LOG_DIR:-$DIR/logs}"
P="$DIR/proton/files"

# The Windows SDK 10.0.26100 cab that holds Redist\D3D\x64\d3dcompiler_47.dll (10.0.26100.7705).
D3DC_CAB_URL="https://download.visualstudio.microsoft.com/download/pr/6452c1f1-dc1e-413c-8b19-991b61870a8b/ce88775024f6b22848b03dad4af285a9/61d57a7a82309cd161a854a6f4619e52.cab"
D3DC_CAB_SHA256=a29f471c9a242aab488f9afa067d45f7a462f1c12fc892d66652be7f37a619d1
D3DC_FILE=fil3585cb2ea5db13cc0838f8d06b5c9679
D3DC_SHA256=a05f99734f7c4822fefc12b367af21fd0976ed6608752fb1e1e80b6ece7ecbbb

wine_env() {
    [ -x "$P/bin/wine" ] || { echo "no Wine at $P (run: $0 setup PROTON_DIR)" >&2; exit 1; }
    export WINEPREFIX="$DIR/pfx" WINEARCH=win64 WINEDEBUG="${WINEDEBUG:--all}"
    export PATH="$P/bin:$PATH"
    export WINEDLLPATH="$P/lib/wine/x86_64-unix:$P/lib/wine/x86_64-windows:$P/lib/wine/i386-unix:$P/lib/wine/i386-windows"
    export LD_LIBRARY_PATH="$P/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    # No audio driver (nothing reaches the host's devices), no menu entries or file associations on
    # the host, no Mono/Gecko installs; vkd3d-proton, DXVK's dxgi and Microsoft's d3dcompiler_47.
    export WINEDLLOVERRIDES="winepulse.drv=d;winealsa.drv=d;wineoss.drv=d;winemenubuilder.exe=d;mscoree=d;mshtml=d;d3d12=n;d3d12core=n;dxgi=n;d3dcompiler_47=n"
    export XDG_DATA_HOME="$DIR/xdg/data" XDG_CONFIG_HOME="$DIR/xdg/config"
    mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"
}

# /a/b -> Z:\a\b; anything else unchanged.
winpath() { if [[ "$1" == /* ]]; then local p="${1//\//\\}"; echo "Z:$p"; else echo "$1"; fi; }

setup() {
    local proton="${1:?usage: $0 setup PROTON_DIR}"
    [ -x "$proton/files/bin/wine" ] || { echo "no files/bin/wine in $proton" >&2; exit 1; }
    [ -f "$DIST/forza.exe" ] || { echo "no forza.exe in $DIST (tools/build_windows.sh)" >&2; exit 1; }
    mkdir -p "$DIR"
    rm -rf "$DIR/proton"
    mkdir -p "$DIR/proton"
    cp -a --reflink=auto "$proton/files" "$DIR/proton/"
    [ -f "$proton/version" ] && cp "$proton/version" "$DIR/proton/"
    wine_env
    wine wineboot -i > "$DIR/wineboot.log" 2>&1
    local sys="$WINEPREFIX/drive_c/windows/system32"
    cp "$P/lib/wine/vkd3d-proton/x86_64-windows/d3d12.dll" "$P/lib/wine/vkd3d-proton/x86_64-windows/d3d12core.dll" "$sys/"
    cp "$P/lib/wine/dxvk/x86_64-windows/dxgi.dll" "$sys/"
    wine reg add 'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d '' /f > /dev/null 2>&1
    # d3dcompiler_47.dll beside forza.exe, where ReGPU looks first.
    local tmp
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/sdk.cab" "$D3DC_CAB_URL"
    echo "$D3DC_CAB_SHA256  $tmp/sdk.cab" | sha256sum -c --quiet
    cabextract -q -d "$tmp" -F "$D3DC_FILE" "$tmp/sdk.cab" 2> /dev/null
    echo "$D3DC_SHA256  $tmp/$D3DC_FILE" | sha256sum -c --quiet
    cp "$tmp/$D3DC_FILE" "$DIST/d3dcompiler_47.dll"
    rm -rf "${tmp:?}"
    wineserver -w
    echo "prefix ready: $WINEPREFIX (Wine $(wine --version), $(tr -d '()' < "$P/lib/wine/vkd3d-proton/version" | awk '{print $NF}'))"
}

run() {
    local gpu=(--gpu_plugin=regpu --gpu_backend=d3d12)
    if [ "${1:-}" = --vulkan ]; then gpu=(--gpu_backend=vulkan); shift; fi
    [ -f "$DIST/d3dcompiler_47.dll" ] || { echo "no d3dcompiler_47.dll beside forza.exe (run setup)" >&2; exit 1; }
    wine_env
    mkdir -p "$LOGS"
    export REGPU_LOG_DIR; REGPU_LOG_DIR="$(winpath "$LOGS")"
    local v
    for v in FORZA_AUTOPRESS_FIFO FORZA_SCREENSHOT_TRIGGER FORZA_FRAMEDUMP_DIR RECOMP_PERF_CSV; do
        [ -n "${!v:-}" ] && export "$v=$(winpath "${!v}")"
    done
    local args=() a
    for a in "$@"; do
        if [[ "$a" == --*=/* ]]; then a="${a%%=*}=$(winpath "${a#*=}")"; fi
        args+=("$a")
    done
    cd "$DIST"
    exec wine ./forza.exe "${gpu[@]}" "${args[@]}"
}

case "${1:-}" in
    setup) shift; setup "$@" ;;
    run) shift; run "$@" ;;
    *) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
