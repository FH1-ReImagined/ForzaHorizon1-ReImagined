#!/usr/bin/env bash
# Start the game for interactive, scripted stepping (see tools/live_step.sh).
#
# Usage: tools/live_session.sh DIR [seconds] [extra forza flags...]
#   DIR receives the input FIFO (in.fifo), screenshots, run.out and the log (forza.log).
#   Inputs written to DIR/in.fifo, one per line ("A", "UP", "START:0.5"), are pressed as
#   they arrive; tools/live_shot.sh DIR saves what the window shows; touching
#   DIR/dump.trigger dumps the next front buffer from guest memory instead.
#
# Environment: REXSDK, FORZA_BUILD, FORZA_DATA, FORZA_LOG_LEVEL as in tools/run_forza.sh.
# Refuses to start with less than 10 GB of available memory (FORZA_IGNORE_MEM=1 skips).
# The game runs in the background; stop it with kill -INT on the PID in DIR/pid (the
# `timeout` wrapper; the game is its child). Never use pkill -f.
# Adapted from RecompiledHaloReach.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$(mkdir -p "$1" && cd "$1" && pwd)"
SECS="${2:-1800}"
shift 2 2>/dev/null || shift $#
. "$ROOT/tools/startup_movies.sh"
forza_startup_movie_args "$@" || exit $?
. "$ROOT/tools/bench_lock.sh"
bench_lock_check || exit 3
avail_gb=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
if [ "${FORZA_IGNORE_MEM:-0}" != 1 ] && [ "$avail_gb" -lt 10 ]; then
    echo "only ${avail_gb} GB of memory available; not starting (FORZA_IGNORE_MEM=1 overrides)" >&2
    exit 2
fi
rm -f "$DIR/in.fifo" "$DIR/dump.trigger" "$DIR/shot.trigger" "$DIR"/forza_frame_*.bin "$DIR"/forza_frame_*.json
# Guest memory files (5 GB each) of instances that were killed or crashed: left behind,
# they fill /dev/shm and later instances die with SIGBUS.
for shm in /dev/shm/xenia_memory_*; do
    [ -O "$shm" ] || continue
    grep -qs "$(basename "$shm")" /proc/[0-9]*/maps || rm -f -- "$shm"
done
# The runtime appends to its log; keep one previous run.
[ -f "$DIR/forza.log" ] && mv -f "$DIR/forza.log" "$DIR/forza.prev.log"
mkfifo "$DIR/in.fifo"
DEFAULT_SDK="$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64"
[ -d "$DEFAULT_SDK/lib" ] || DEFAULT_SDK="$HOME/rexglue-sdk-patched/0.10.0.24/linux-amd64"
[ -d "$DEFAULT_SDK/lib" ] || DEFAULT_SDK="$HOME/rexglue-sdk-nightly/0.10.0.24/linux-amd64"
cd "$ROOT/forza-recomp/out/build/${FORZA_BUILD:-linux}"
SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT=0x0000/0x0000 SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT=0x0000/0x0000 \
FORZA_AUTOPRESS="${FORZA_AUTOPRESS-}" FORZA_AUTOPRESS_FIFO="$DIR/in.fifo" \
FORZA_FRAMEDUMP_TRIGGER="$DIR/dump.trigger" FORZA_FRAMEDUMP_DIR="$DIR" \
FORZA_SCREENSHOT_TRIGGER="$DIR/shot.trigger" \
LD_LIBRARY_PATH="${REXSDK:-$DEFAULT_SDK}/lib:$PWD" nohup timeout --signal=INT --kill-after=15 "$SECS" \
    ./forza --game_data_root="${FORZA_DATA:-$ROOT/extracted/game}" --log_file="$DIR/forza.log" \
    --log_level="${FORZA_LOG_LEVEL:-info}" \
    "${STARTUP_MOVIE_ARGS[@]}" > "$DIR/run.out" 2>&1 &
echo $! > "$DIR/pid"
echo "started pid $! in $DIR"
