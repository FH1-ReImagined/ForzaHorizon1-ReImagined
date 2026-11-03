#!/usr/bin/env bash
# Test runner: start the game for a limited time with scripted input and logs.
# Usage: tools/run_forza.sh [seconds] [extra forza flags...]
#
# Environment:
#   FORZA_RUN_DIR   where the runtime log (forza.log), stdout/stderr (run.out) and frame
#                   dumps go (default ${XDG_RUNTIME_DIR:-/tmp}/forza_run)
#   FORZA_BUILD     build dir under forza-recomp/out/build (default linux)
#   REXSDK          SDK prefix whose runtime libraries are used (default: the patched
#                   overlay from tools/build_rexglue_sdk.sh, else RecompiledHaloReach's,
#                   else the plain nightly, which renders textures 256x too dark)
#   FORZA_AUTOPRESS scripted presses (src/kernel/input_autopress.cpp), default none
#   FORZA_LOG_LEVEL log level (default info)
# Refuses to start with less than 10 GB of available memory (FORZA_IGNORE_MEM=1 skips
# the check): the game maps 5 GB of guest memory and the GPU caches grow on top.
# Adapted from RecompiledHaloReach's tools/run_reach.sh.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECS="${1:-60}"
shift || true
. "$ROOT/tools/startup_movies.sh"
forza_startup_movie_args "$@" || exit $?
RUN="${FORZA_RUN_DIR:-${XDG_RUNTIME_DIR:-/tmp}/forza_run}"
mkdir -p "$RUN"
. "$ROOT/tools/bench_lock.sh"
bench_lock_check || exit 3
avail_gb=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
if [ "${FORZA_IGNORE_MEM:-0}" != 1 ] && [ "$avail_gb" -lt 10 ]; then
    echo "only ${avail_gb} GB of memory available; not starting (FORZA_IGNORE_MEM=1 overrides)" >&2
    exit 2
fi
cd "$ROOT/forza-recomp/out/build/${FORZA_BUILD:-linux}"
[ -f "$RUN/forza.log" ] && mv -f "$RUN/forza.log" "$RUN/forza.prev.log"
export FORZA_AUTOPRESS="${FORZA_AUTOPRESS-}"
export FORZA_FRAMEDUMP_DIR="${FORZA_FRAMEDUMP_DIR:-$RUN}"
# Scripted input is injected for user 0; SDL ignores every pad so that the user's own
# controllers (and Steam Input's or Sunshine's virtual ones) are never touched.
export SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT-0x0000/0x0000}"
export SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT="${SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT-0x0000/0x0000}"
DEFAULT_SDK="$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64"
[ -d "$DEFAULT_SDK/lib" ] || DEFAULT_SDK="$HOME/rexglue-sdk-patched/0.10.0.24/linux-amd64"
[ -d "$DEFAULT_SDK/lib" ] || DEFAULT_SDK="$HOME/rexglue-sdk-nightly/0.10.0.24/linux-amd64"
export LD_LIBRARY_PATH="${REXSDK:-$DEFAULT_SDK}/lib:$PWD"
timeout --signal=INT --kill-after=10 "$SECS" ./forza \
    --game_data_root="${FORZA_DATA:-$ROOT/extracted/game}" \
    --log_file="$RUN/forza.log" --log_level="${FORZA_LOG_LEVEL:-info}" \
    "${STARTUP_MOVIE_ARGS[@]}" > "$RUN/run.out" 2>&1
echo "EXIT=$?" >> "$RUN/run.out"
tail -1 "$RUN/run.out"

# The runtime backs guest memory with /dev/shm/xenia_memory_* (~5 GB each) and does not
# unlink it when the process is interrupted. Remove our orphans (files no running process
# maps) so repeated runs don't fill /dev/shm, which then fails with SIGBUS.
for shm in /dev/shm/xenia_memory_*; do
    [ -O "$shm" ] || continue
    grep -qs "$(basename "$shm")" /proc/[0-9]*/maps || rm -f -- "$shm"
done
