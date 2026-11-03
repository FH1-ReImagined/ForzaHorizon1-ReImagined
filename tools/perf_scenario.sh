#!/usr/bin/env bash
# Scripted performance scenarios with frame statistics (RECOMP_PERF, docs/perf.md).
#
# Usage: tools/perf_scenario.sh SCENARIO DIR [cold|warm] [extra forza flags...]
#   menu      boot, title screen, START, main menu
#   drive     a profile that has not finished the prologue: main menu, Single Player,
#             the festival intro video (skipped), then the opening drive at full throttle
#   freeroam  a profile in free roam: main menu, the "MISSING CONTENT" notice, Single Player, 90 s
#             of free roam driving (use FORZA_XUID and --user_data_root for a copied save)
#   race      the user's Xenia save (as freeroam): free roam, the GoPro Millennia Masters
#             sign-up at the spawn point, the race with the throttle held RACE_SECS (26) s
#             from the countdown; screenshots every ~1.6 s (race clock, speedometer) and the
#             cars' positions in DIR/cars.txt (tools/carlog.py)
#   cold      runs with an empty shader cache (--cache_root=DIR/cache_cold); warm (default)
#             uses the normal one.
# The boot videos are skipped by --skip_startup_movies (default; FORZA_SKIP_MOVIES=false
# plays them and START skips each); the in-game videos are skipped with START/A through the
# input FIFO as they start; each scene
# is labelled once it is reached (SCENE:<name>), so loading and videos stay out of the
# scene's numbers.
#
# Results are only valid on an idle machine: other jobs share it. The script records
# other game processes (reach, forza), the 1-minute load average and MemAvailable at the
# start and the end, and prints "BENCH: ... status=CONTENDED" if another game ran or the
# load average was above 2 before the run. Take a clean window (BENCH_LOCK) for numbers
# that go into docs/perf.md.
# Above 30 fps the FRAMETIME lines (main-loop dt) are printed too.
# PERF_CPUPROF=1 also samples the CPU during the measured scene (src/debug/cpu_profile.cpp)
# into DIR/cpuprof.txt; summarize with tools/cpuprof_report.py.
# PERF_CPUSET=0-3 runs the game's threads on those CPUs only (taskset; a smaller CPU, docs/perf.md
# "Low-end CPUs"). PERF_THREADS=1 samples every thread's CPU time, run-queue wait and switches
# and the game's GPU time each second into DIR/threads.jsonl, the scene's start and end into
# DIR/marks.txt; summarize with tools/thread_stats.py report DIR.
# Prints the BENCH line and the PERF lines; the log stays in DIR.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO="$1"; DIR="$2"; CACHE="${3:-warm}"
shift 3 2>/dev/null || shift $#
. "$ROOT/tools/startup_movies.sh"
forza_startup_movie_args "$@" || exit $?
mkdir -p "$DIR"

machine_state() {  # others load1 mem_gb
    local others
    others=$(ps -C reach,forza -o pid=,comm= | grep -v -w "${GAME_PID:-0}" | awk '{print $2"("$1")"}' | tr '\n' ' ')
    echo "${others:-none}|$(cut -d' ' -f1 /proc/loadavg)|$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo)"
}
start_state=$(machine_state)

extra=()
if [ "$CACHE" = cold ]; then
    rm -rf "$DIR/cache_cold"; mkdir -p "$DIR/cache_cold"
    extra+=(--cache_root="$DIR/cache_cold")
fi
export RECOMP_PERF=1 RECOMP_PERF_SCENE=boot RECOMP_PERF_CSV="$DIR/frames.csv"
# Above 30 fps: the main loop's dt statistics and per-frame log (src/hooks/frame_time.cpp),
# read by tools/frametime_report.py DIR.
export FORZA_FRAMETIME_LOG=1 FORZA_FRAMETIME_CSV="$DIR/frametime.csv"
export FORZA_CPUPROF="$DIR/cpuprof.txt"
prof() {
    [ "${PERF_CPUPROF:-0}" = 1 ] && say "PROF:$1"
    [ "$1" = START ] && pin
    [ "${PERF_THREADS:-0}" = 1 ] && echo "$(date +%s.%N) $([ "$1" = START ] && echo start || echo end)" >> "$DIR/marks.txt"
    return 0
}
"$ROOT/tools/live_session.sh" "$DIR" 600 "${extra[@]}" "${STARTUP_MOVIE_ARGS[@]}" > /dev/null || exit $?
GAME_PID=$(ps -o pid= --ppid "$(cat "$DIR/pid")" | tr -d ' ')
# Threads made later inherit the CPU set of the thread that makes them; pin again at the scene.
pin() { [ -n "${PERF_CPUSET:-}" ] && taskset -a -p -c "$PERF_CPUSET" "$GAME_PID" > /dev/null; return 0; }
pin
rm -f "$DIR/marks.txt"
[ "${PERF_THREADS:-0}" = 1 ] && python3 "$ROOT/tools/thread_stats.py" sample "$GAME_PID" "$DIR/threads.jsonl" &
fifo="$DIR/in.fifo"
say() { echo "$1" > "$fifo"; }
alive() { kill -0 "$(cat "$DIR/pid")" 2>/dev/null; }
wait_s() { local end=$((SECONDS + $1)); while [ $SECONDS -lt $end ] && alive; do sleep 1; done; }

# Boot videos (Microsoft Studios, Playground): skipped by the game's --skip_startup_movies
# (live_session.sh passes it; FORZA_SKIP_MOVIES=false plays them, then START skips each as it
# starts). The title screen ("PRESS START") follows; START there opens the main menu.
if [ "$STARTUP_SKIP_MOVIES" = true ]; then
    wait_s 10; say SCENE:title; wait_s 6
else
    for t in 8 10 12 14; do wait_s $((t - SECONDS)); say START; done
    wait_s 6; say SCENE:title; wait_s 10
fi
say START; wait_s 6; say SCENE:main_menu
case "$SCENARIO" in
    menu) prof START; wait_s 30; prof STOP ;;
    drive)
        wait_s 10; say A; say SCENE:loading
        for _ in 1 2 3 4 5 6; do wait_s 4; say A; done  # skip the festival video
        wait_s 20; say SCENE:drive; prof START
        for _ in $(seq 1 12); do say RT:5; wait_s 5; done
        prof STOP ;;
    freeroam)
        # As in race: the first A dismisses the Xenia save's "MISSING CONTENT" (DLC) notice,
        # the second picks SINGLE PLAYER.
        wait_s 10; say A; wait_s 3; say A; say SCENE:loading; wait_s 40
        say SCENE:freeroam; prof START
        for _ in $(seq 1 18); do say RT:5; wait_s 5; done
        prof STOP ;;
    race)
        # The first A dismisses the "MISSING CONTENT" (DLC) notice, the second picks SINGLE
        # PLAYER. At the spawn: X, A, A, X sign up for the festival race; A starts it.
        wait_s 10; say A; wait_s 3; say A; say SCENE:loading; wait_s 45
        say X; wait_s 4; say A; wait_s 5; say A; wait_s 6; say X; wait_s 16
        say SCENE:race_intro; say A; wait_s 12; say SCENE:race; prof START
        python3 "$ROOT/tools/carlog.py" "$GAME_PID" "${RACE_SECS:-26}" "$DIR/cars.txt" &
        carlog=$!
        say "RT:${RACE_SECS:-26}"
        for i in $(seq 1 $(( ${RACE_SECS:-26} / 2 ))); do
            sleep 1.6; "$ROOT/tools/live_shot.sh" "$DIR" "race$i" > /dev/null
        done
        wait "$carlog"; prof STOP ;;
    *) echo "unknown scenario $SCENARIO" >&2 ;;
esac
say SCENE:end; wait_s 2
end_state=$(machine_state)
kill -INT "$(cat "$DIR/pid")" 2>/dev/null
wait_s 15

IFS='|' read -r s_others s_load s_mem <<< "$start_state"
IFS='|' read -r e_others e_load e_mem <<< "$end_state"
status=CLEAN
if [ "$s_others" != none ] || [ "$e_others" != none ] || awk "BEGIN {exit !($s_load > 2)}"; then
    status=CONTENDED
fi
echo "BENCH: scenario=$SCENARIO cache=$CACHE start: others=$s_others load1=$s_load mem=${s_mem}G" \
     "end: others=$e_others load1=$e_load mem=${e_mem}G status=$status" | tee "$DIR/bench.txt"
grep -h "PERF: scene=\|FRAMETIME: " "$DIR/forza.log" | sed 's/^.*\(PERF\|FRAMETIME\): /\1: /'
for shm in /dev/shm/xenia_memory_*; do
    [ -O "$shm" ] || continue
    grep -qs "$(basename "$shm")" /proc/[0-9]*/maps || rm -f -- "$shm"
done
