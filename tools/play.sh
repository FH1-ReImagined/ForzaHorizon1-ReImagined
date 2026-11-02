#!/usr/bin/env bash
# Play the game: no time limit and no scripted input (tools/run_forza.sh is the test
# runner). Extra arguments are passed to the game, e.g. --resolution_scale=2.
#
#   tools/play.sh                 # your profile and progress in ~/.local/share/forza
#   tools/play.sh --xenia [XUID]  # continue your Xenia save (see below)
#   tools/play.sh --offline       # no Xbox LIVE (no Forza Live server)
#   tools/play.sh --base          # the base game instead of Title Update 4 (see below)
#
# Title Update 4 (docs/dlc.md): when its build exists (forza-recomp/out/build/linux-tu4, see
# README: Building) it is the default: Horizon Rally and the 1000 Club need it. The update's
# files (tools/apply_title_update.py: patched default.xex, facades, media.zip) are mounted as
# update: from FORZA_UPDATE (default extracted/tu4). A profile save that TU4 loaded is
# converted (ForzaProfile/VersionFlags, NeedsAA) and the base build refuses it ("MISSING
# TITLE UPDATE"): --base is for profiles that never ran on TU4. Without the TU4 build the
# base build is used.
#
# Xbox LIVE: the game is signed in to LIVE through a Forza Live server (server/), as a
# Gold member. live_server in forza.toml or FORZA_SERVER=host[:port] names a shared one;
# otherwise this script starts one on this machine (127.0.0.1:21200, HTTP 21201, data in
# ~/.local/share/forza/live_server) for the session, or uses the one already running there.
#
# Profile: the runtime signs in one local profile (XUID B13EBABEBABEBABE) on player 1, and
# every controller drives player 1, so START on any pad gets past the title screen. Saves
# go to ~/.local/share/forza/B13EBABEBABEBABE/4D5309C9/.
#
# --xenia [XUID]: plays with a COPY of your Xenia Canary profile's Forza Horizon save
# (Xenia's content/<XUID>/4D5309C9; the original is never modified). Forza ties its save
# to the profile's XUID, so the game runs signed in with that XUID. The copy lives in
# ~/.local/share/forza/xenia-<XUID>/ and is made once: progress you make here stays there.
# --xenia-refresh copies the Xenia save again (replacing that copy). XUID defaults to the
# only Xenia profile with a Forza Horizon save; Xenia's content folder is
# $FORZA_XENIA_CONTENT (default ~/xenia_canary_windows/content). Installed DLC
# (tools/install_dlc.py) is shared with the normal profile.
#
# Environment: FORZA_BUILD (build dir under forza-recomp/out/build, default linux-tu4 when it
# exists, else linux), FORZA_UPDATE (the update: folder of a TU4 build), REXSDK
# (SDK prefix; default the patched overlay that tools/build_rexglue_sdk.sh makes),
# FORZA_DATA (game files, default extracted/game). The log is ~/.local/share/forza/forza.log.
# Adapted from RecompiledHaloReach's tools/play.sh.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="$ROOT/forza-recomp/out/build"
DATA="${FORZA_DATA:-$ROOT/extracted/game}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/forza"
TITLE=4D5309C9
LOCAL_PROFILE=B13EBABEBABEBABE  # the runtime's own profile folder

xenia=0; refresh=0; xuid=""; offline=0; base=0
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --xenia|--xenia-refresh)
            xenia=1
            [ "$1" = --xenia-refresh ] && refresh=1
            if [ $# -gt 1 ] && [[ "$2" =~ ^[0-9A-Fa-f]{16}$ ]]; then xuid="${2^^}"; shift; fi ;;
        --offline) offline=1 ;;
        --base) base=1 ;;
        *) args+=("$1") ;;
    esac
    shift
done

# Which build: --base forces the base game; else FORZA_BUILD; else Title Update 4 if built.
if [ "$base" = 1 ]; then FORZA_BUILD=linux
elif [ -z "${FORZA_BUILD:-}" ]; then
    if [ -x "$BUILD_ROOT/linux-tu4/forza" ]; then FORZA_BUILD=linux-tu4; else FORZA_BUILD=linux; fi
fi
BUILD="$BUILD_ROOT/$FORZA_BUILD"
if [[ "$FORZA_BUILD" == *tu4* ]]; then
    UPDATE="${FORZA_UPDATE:-$ROOT/extracted/tu4}"
    [ -f "$UPDATE/default.xex" ] || { echo "no patched game files at $UPDATE (tools/apply_title_update.py, see README: Building)" >&2; exit 1; }
    case " ${args[*]:-} " in *" --update_data_root"*) ;; *) args=(--update_data_root="$UPDATE" "${args[@]}") ;; esac
    echo "play.sh: Title Update 4 build ($FORZA_BUILD); --base for the base game" >&2
else
    echo "play.sh: base game build ($FORZA_BUILD): no Horizon Rally, no 1000 Club" >&2
fi
[ -x "$BUILD/forza" ] || { echo "no build at $BUILD (see README: Building)" >&2; exit 1; }
[ -f "$DATA/default.xex" ] || { echo "no game files at $DATA (see README: Building, step 2)" >&2; exit 1; }
SDK="${REXSDK:-$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64}"
[ -d "$SDK/lib" ] || SDK="$HOME/rexglue-sdk-patched/0.10.0.24/linux-amd64"
if [ ! -d "$SDK/lib" ]; then
    echo "patched SDK overlay not found; using the plain nightly (textures may render dark)" >&2
    SDK="$HOME/rexglue-sdk-nightly/0.10.0.24/linux-amd64"
fi
mkdir -p "$DATA_HOME"

if [ "$xenia" = 1 ]; then
    content="${FORZA_XENIA_CONTENT:-$HOME/xenia_canary_windows/content}"
    if [ -z "$xuid" ]; then
        candidates=()
        for d in "$content"/*/"$TITLE"/00000001; do
            [ -d "$d" ] || continue
            p=$(basename "$(dirname "$(dirname "$d")")")
            [ "$p" = 0000000000000000 ] || candidates+=("$p")
        done
        if [ ${#candidates[@]} -ne 1 ]; then
            echo "found ${#candidates[@]} Xenia profiles with a Forza Horizon save in $content;" \
                 "name one: tools/play.sh --xenia <XUID>" >&2
            exit 1
        fi
        xuid="${candidates[0]}"
    fi
    src="$content/$xuid/$TITLE"
    [ -d "$src" ] || { echo "no Forza Horizon save at $src" >&2; exit 1; }
    root="$DATA_HOME/xenia-$xuid"
    dest="$root/$LOCAL_PROFILE/$TITLE"
    if [ "$refresh" = 1 ] || [ ! -d "$dest" ]; then
        rm -rf "$dest"
        mkdir -p "$(dirname "$dest")"
        cp -r "$src" "$dest" || exit 1
        echo "copied the Xenia save of $xuid to $dest"
    fi
    # Installed DLC lives with the normal profile; share it.
    mkdir -p "$DATA_HOME/0000000000000000"
    [ -e "$root/0000000000000000" ] || ln -s ../0000000000000000 "$root/0000000000000000"
    # The shader cache stays shared with the normal profile.
    args=(--user_data_root="$root" --cache_root="$DATA_HOME/cache" --profile_xuid="$xuid" "${args[@]}")
fi

server_pid=""
if [ "$offline" = 0 ] && [ -z "${FORZA_SERVER:-}" ] &&
   ! grep -qsE '^\s*live_server\s*=\s*"[^"]+' "$BUILD/forza.toml"; then
    # No shared server configured: a local one (or the one another instance started).
    if ! (exec 3<>/dev/tcp/127.0.0.1/21201) 2>/dev/null; then
        mkdir -p "$DATA_HOME/live_server"
        # The game's database has the Co-op Festival's events (#67).
        db=()
        [ -f "$DATA/media/db/gamedb.slt" ] && db=(--game-db "$DATA/media/db/gamedb.slt")
        [ -f "$ROOT/server/forza_live_server.py" ] ||
            echo "server/ is empty (the Forza Live server is a submodule): git submodule update --init server" >&2
        python3 "$ROOT/server/forza_live_server.py" --host 127.0.0.1 --port 21200 \
            --http-port 21201 --server-name 127.0.0.1 --rebind-legacy --data-dir "$DATA_HOME/live_server" "${db[@]}" \
            > "$DATA_HOME/live_server/server.log" 2>&1 &
        server_pid=$!
        sleep 0.5
    fi
    export FORZA_SERVER=127.0.0.1:21200
fi
[ "$offline" = 1 ] && unset FORZA_SERVER

cd "$BUILD"
LD_LIBRARY_PATH="$SDK/lib:$BUILD" ./forza --game_data_root="$DATA" \
    --log_file="$DATA_HOME/forza.log" "${args[@]}"
status=$?
[ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null
# Guest memory (/dev/shm/xenia_memory_*, 5 GB) is not unlinked when the game is killed.
for shm in /dev/shm/xenia_memory_*; do
    [ -O "$shm" ] || continue
    grep -qs "$(basename "$shm")" /proc/[0-9]*/maps || rm -f -- "$shm"
done
exit $status
