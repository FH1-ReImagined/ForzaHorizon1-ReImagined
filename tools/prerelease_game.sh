# Sourced by tools/prerelease.sh: how its checks start the game (#846).
#
# The SDK reads its settings from forza.toml next to the executable, and the launcher writes the
# player's settings there when they play from that build folder (frame rate, upscaler, world
# detail, the Forza Live server). A check's game must not see them, and must not change anything
# in that folder. So each run gets a folder of its own:
#   DIR/game/   hard links (copies on another file system) of the build folder's files, without
#               forza.toml and logs/, plus a copy of tools/prerelease_forza.toml as forza.toml
#   DIR/xdg/    XDG_CONFIG_HOME, XDG_DATA_HOME and XDG_STATE_HOME: the Forza Live identity and
#               the relaunch data stay out of ~/.local/share (XDG_CACHE_HOME: PRERELEASE_XDG_CACHE,
#               default DIR/xdg/cache: the driver's shader cache and any other cache)
# and it runs offline unless the caller passes FORZA_SERVER. The player's environment variables
# for the game (PRERELEASE_UNSET: the server, the launcher's graphics settings and relaunch
# hand-over, scripted input) do not reach it; REX_* settings other than REX_LIVE_SERVER do, as
# the caller's own choice (REX_AUDIO_FAIL_OPEN). Nothing in the build folder is written, so there
# is nothing to put back afterwards; prerelease_drop_game removes DIR/game after the run.

PRERELEASE_TOML="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/prerelease_forza.toml"
PRERELEASE_UNSET=(FORZA_SERVER FORZA_NET FORZA_SERVER_RELAY FORZA_ROOM FORZA_GAMERTAG FORZA_LIVE_SIGNIN
    FORZA_GRAPHICS_SETTINGS FORZA_RELAUNCHED FORZA_LAUNCHER_RELAUNCH FORZA_AUTOPRESS
    FORZA_SCREENSHOT_BURST FORZA_GAME_DIR REX_LIVE_SERVER)

# prerelease_stage_game BUILD_DIR DEST: DEST (made if missing) gets the build's files and the
# checks' forza.toml. Top-level regular files only, as tools/render_test.py stage_build: the game
# loads its plugin from its own folder and gets every other path on the command line.
prerelease_stage_game() {
    local build="$1" dest="$2" f name
    [ -f "$build/forza" ] || { echo "no forza executable in $build"; return 1; }
    mkdir -p "$dest" || return 1
    for f in "$build"/*; do
        name="${f##*/}"
        case "$name" in forza.toml|logs) continue ;; esac
        [ -f "$f" ] && [ ! -L "$f" ] || continue
        [ -e "$dest/$name" ] && continue   # a retry in the same folder: linked already
        ln "$f" "$dest/$name" 2> /dev/null || cp -p "$f" "$dest/$name" || return 1
    done
    # A new file every time (never a link to anything), so the game may even rewrite it.
    rm -f "$dest/forza.toml"
    cp "$PRERELEASE_TOML" "$dest/forza.toml"
}

# prerelease_start_game DIR BUILD_DIR SECS [NAME=VALUE...] [-- game flags...]: tools/live_session.sh
# DIR SECS flags, from DIR/game with the environment above plus the NAME=VALUE pairs.
prerelease_start_game() {
    local dir="$1" build="$2" secs="$3" vars=() unset=() x
    shift 3
    while [ $# -gt 0 ] && [ "$1" != -- ]; do
        [[ "$1" == [A-Za-z_]*=* ]] || { echo "prerelease_start_game: not NAME=VALUE before --: $1"; return 2; }
        vars+=("$1"); shift
    done
    [ $# -gt 0 ] && shift
    for x in "${PRERELEASE_UNSET[@]}"; do unset+=(-u "$x"); done
    prerelease_stage_game "$build" "$dir/game" || return 1
    x="${PRERELEASE_XDG_CACHE:-$dir/xdg/cache}"
    mkdir -p "$dir/xdg/config" "$dir/xdg/data" "$dir/xdg/state" "$x" || return 1
    env "${unset[@]}" \
        XDG_CONFIG_HOME="$dir/xdg/config" XDG_DATA_HOME="$dir/xdg/data" \
        XDG_STATE_HOME="$dir/xdg/state" XDG_CACHE_HOME="$x" \
        ${vars[@]+"${vars[@]}"} FORZA_GAME_DIR="$dir/game" \
        "$(dirname "$PRERELEASE_TOML")/live_session.sh" "$dir" "$secs" "$@"
}

# prerelease_drop_game DIR: removes DIR/game (once the game has stopped): kept, each run's copy
# would hold on to old executables after the next build, or be a full copy on another file system.
prerelease_drop_game() {
    [ -n "$1" ] && [ -d "$1/game" ] && [ ! -L "$1/game" ] || return 0
    rm -rf -- "$1/game"
}
