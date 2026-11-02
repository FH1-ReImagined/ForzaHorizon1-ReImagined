#!/usr/bin/env bash
# Pre-release checks, non-interactive: one PASS/FAIL line per check and a summary. Full
# output goes to ~/.cache/forza-prerelease/<timestamp>/<check>.log; a failing check prints
# the last lines of its log. Exit status = number of failed checks.
#
# Usage: tools/prerelease.sh [--skip-build] [--only CHECK[,CHECK...]] [--iso PATH] [--ci]
#   --skip-build  skip the "sdk" and "build" checks (use the binaries as they are)
#   --only        run only these checks (names below)
#   --iso PATH    the disc image for the "launcher" check (default: ./HorizonISO/*.iso)
#   --ci          also dispatch the release workflow (artifacts only, no release) and wait for it
#
# Checks (in this order):
#   sdk       SDK overlay matches the ReIGlue tag tools/build_rexglue_sdk.sh pins (the script runs
#             only when it changed since the last install: a stamp in
#             ~/.cache/forza-prerelease/sdk.stamp). Reinstalling affects every running game.
#   build     ninja -j4 (nice) of forza-recomp/out/build/linux-tu4 and linux, then restores
#             forza-recomp/generated/rexglue.cmake (codegen rewrites it)
#   unit      unit tests: server/test_*.py, launcher/test_*.py
#   boot      the TU4 build to the main menu on a scratch profile (a copy of the Xenia save,
#             $FORZA_XENIA_CONTENT, never ~/.local/share/forza) once per present_effect bilinear,
#             cas, fsr, fsr3 (temporal, Performance at 1440p), smaa (SMAA 1x at 1440p, #132); the base build once. "Main menu
#             reached" = the game still runs, its log has no fault, the frame rate is above
#             5 fps and the window shows a changed, non-flat image after START (the title
#             screen is gone). Needs Pillow and 10 GB of free memory.
#   server    a local Forza Live server on ports 21310/21311: the TU4 build signs in to it, reaches
#             the main menu and sends its stats reads (XUserReadStats) without hanging
#   launcher  the launcher the way the frozen build runs it (LD_LIBRARY_PATH pointing at foreign
#             libraries, #53): default.xex out of the disc image and its check, the AES helper
#   ci        (--ci) gh workflow run release.yml and wait; prints the run id
#
# Environment: FORZA_XENIA_CONTENT (default ~/xenia_canary_windows/content), FORZA_XUID (the Xenia
# profile; default the only one with a Forza Horizon save), PRERELEASE_JOBS (ninja jobs, default 4).
# Only starts processes it stops again; one game at a time.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# The Forza Live server is the server/ submodule (FH1-ReImagined/forza-live).
[ -f server/forza_live_server.py ] || git submodule update --init server || { echo "server/ is empty: git submodule update --init server"; exit 1; }
STATE="$HOME/.cache/forza-prerelease"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$STATE/$STAMP"
mkdir -p "$LOG"
BUILD_ROOT="$ROOT/forza-recomp/out/build"
JOBS="${PRERELEASE_JOBS:-4}"
CONTENT="${FORZA_XENIA_CONTENT:-$HOME/xenia_canary_windows/content}"
TITLE=4D5309C9
SERVER_PORT=21310
SERVER_HTTP=21311

skip_build=0; only=""; iso=""; ci=0
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-build) skip_build=1 ;;
        --only) only=",$2,"; shift ;;
        --iso) iso="$2"; shift ;;
        --ci) ci=1 ;;
        -h|--help) sed -n 2,35p "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

wanted() {  # wanted CHECK
    if [ -n "$only" ]; then [[ "$only" == *",$1,"* ]]; return; fi
    case "$1" in
        sdk|build) [ "$skip_build" = 0 ] ;;
        ci) [ "$ci" = 1 ] ;;
        *) return 0 ;;
    esac
}

fails=0; passes=0
run_check() {  # run_check NAME FUNCTION: FUNCTION's output goes to $LOG/NAME.log; its status is the verdict
    local name="$1" fn="$2" t0 rc detail
    wanted "$name" || return 0
    t0=$(date +%s)
    "$fn" > "$LOG/$name.log" 2>&1
    rc=$?
    detail="$(grep -a '^RESULT:' "$LOG/$name.log" | tail -1 | sed 's/^RESULT: *//')"
    if [ $rc -eq 0 ]; then
        passes=$((passes + 1))
        printf 'PASS %-9s %4ds  %s\n' "$name" $(( $(date +%s) - t0 )) "$detail"
    else
        fails=$((fails + 1))
        printf 'FAIL %-9s %4ds  %s\n' "$name" $(( $(date +%s) - t0 )) "$detail"
        tail -n 30 "$LOG/$name.log" | sed 's/^/    | /'
    fi
}

# ---------------------------------------------------------------- sdk
check_sdk() {
    local want have
    want="$(sha256sum < tools/build_rexglue_sdk.sh | cut -d' ' -f1)"
    have="$(cat "$STATE/sdk.stamp" 2>/dev/null || true)"
    if [ "$want" = "$have" ] && [ -d "$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64/lib" ]; then
        echo "RESULT: overlay up to date (stamp)"
        return 0
    fi
    nice -n 15 tools/build_rexglue_sdk.sh || return 1
    echo "$want" > "$STATE/sdk.stamp"
    echo "RESULT: overlay reinstalled"
}

# ---------------------------------------------------------------- build
check_build() {
    local b rc=0
    for b in linux-tu4 linux; do
        echo "== $b"
        FORZA_BUILD=$b FORZA_JOBS=$JOBS tools/build.sh || rc=1
        git checkout -- forza-recomp/generated/rexglue.cmake 2>/dev/null
    done
    [ $rc = 0 ] && echo "RESULT: linux-tu4 and linux built"
    return $rc
}

# ---------------------------------------------------------------- unit
check_unit() {
    local rc=0 n=0 f
    for f in launcher/test_*.py; do
        python3 -m unittest "$f" 2>&1 | tail -n 3 || rc=1
        [ "${PIPESTATUS[0]}" = 0 ] || rc=1
        n=$((n + 1))
    done
    (cd server && python3 -m unittest discover -p 'test_*.py' 2>&1 | tail -n 3; exit "${PIPESTATUS[0]}") || rc=1
    [ $rc = 0 ] && echo "RESULT: $n launcher test files and the server tests"
    return $rc
}

# ---------------------------------------------------------------- game runs
GAME_DIR=""; GAME_PID=""
xuid_of_save() {
    local d p c=()
    if [ -n "${FORZA_XUID:-}" ]; then echo "$FORZA_XUID"; return; fi
    for d in "$CONTENT"/*/$TITLE; do
        p=$(basename "$(dirname "$d")")
        [ "$p" = 0000000000000000 ] || c+=("$p")
    done
    [ ${#c[@]} = 1 ] && echo "${c[0]}"
}

cleanup_game() {
    local g
    [ -n "$GAME_PID" ] || return 0
    g=$(pgrep -P "$GAME_PID" | head -1)
    kill -INT "$GAME_PID" 2>/dev/null
    for _ in $(seq 1 20); do kill -0 "$GAME_PID" 2>/dev/null || break; sleep 0.5; done
    [ -n "$g" ] && kill -0 "$g" 2>/dev/null && kill -9 "$g" 2>/dev/null
    kill -0 "$GAME_PID" 2>/dev/null && kill -9 "$GAME_PID" 2>/dev/null
    for _ in $(seq 1 20); do { [ -n "$g" ] && kill -0 "$g" 2>/dev/null; } || break; sleep 0.5; done
    GAME_PID=""
    for shm in /dev/shm/xenia_memory_*; do   # only our own orphans (no process maps them)
        [ -O "$shm" ] || continue
        grep -qs "$(basename "$shm")" /proc/[0-9]*/maps || rm -f -- "$shm"
    done
}

# game_run NAME BUILD EFFECT [env...]: boots NAME's session in $LOG/NAME and presses START; leaves
# the shots title.png / menu.png and the log there. Returns non-zero when the game died or never ran.
game_run() {
    local name="$1" build="$2" effect="$3" xuid d ud
    shift 3
    xuid="$(xuid_of_save)"
    [ -n "$xuid" ] || { echo "no single Xenia profile with a Forza Horizon save in $CONTENT (FORZA_XUID)"; return 1; }
    d="$LOG/$name"; ud="$d/ud"
    mkdir -p "$ud/B13EBABEBABEBABE" "$d"
    cp -r "$CONTENT/$xuid/$TITLE" "$ud/B13EBABEBABEBABE/" || return 1
    local args=(--user_data_root="$ud" --present_effect="$effect" --audio_mute)
    # fsr3: the launcher's AMD FSR 3 (temporal) arguments, Performance at 1440p (FSR 1 presents)
    [ "$effect" = fsr3 ] && args=(--user_data_root="$ud" --audio_mute --present_effect=fsr --resolution=1440p
        --resolution_scale=2 --fh1_output_width=2560 --fh1_output_height=1440
        --fh1_upscaler=fsr3_performance)
    [ "$effect" = smaa ] && args=(--user_data_root="$ud" --audio_mute --present_effect=fsr --resolution=1440p
        --swap_post_effect=none --fh1_upscaler=smaa --fh1_scene_720p_native_hud=true
        --fh1_native_scene_scale=2 --resolution_scale=2 --fh1_output_width=2560 --fh1_output_height=1440)
    [[ "$build" == *tu4* ]] && args+=(--update_data_root="${FORZA_UPDATE:-$ROOT/extracted/tu4}")
    GAME_DIR="$d"
    env "$@" FORZA_BUILD="$build" FORZA_XUID="$xuid" FORZA_FPSLOG=1 FORZA_LOG_LEVEL=info \
        tools/live_session.sh "$d" 200 "${args[@]}" || return 1
    GAME_PID="$(cat "$d/pid")"
    # wait for the first frames of the title screen (the log says when the game presents)
    local i
    for i in $(seq 1 60); do
        kill -0 "$GAME_PID" 2>/dev/null || { echo "the game exited during start-up"; return 1; }
        grep -q "FORZA_FPSLOG" "$d/forza.log" 2>/dev/null && break
        sleep 2
    done
    sleep 35   # the title screen needs ~45 s of game time before START is read
    tools/live_shot.sh "$d" title > /dev/null || { echo "no screenshot of the title screen"; return 1; }
    echo START > "$d/in.fifo"
    sleep 22
    kill -0 "$GAME_PID" 2>/dev/null || { echo "the game exited after START"; return 1; }
    tools/live_shot.sh "$d" menu > /dev/null || { echo "no screenshot of the menu"; return 1; }
    return 0
}

# verdict_menu DIR: the log has no fault, the frame rate is sane, the menu differs from the title
verdict_menu() {
    local d="$1" fps
    if grep -aqE "Unhandled|Graphics device lost|SIGSEGV|Segmentation|Assertion|terminate called" "$d/forza.log" "$d/run.out" 2>/dev/null; then
        echo "fault in the log:"; grep -aE "Unhandled|Graphics device lost|SIGSEGV|Segmentation|Assertion|terminate called" "$d/forza.log" "$d/run.out" | head -3
        return 1
    fi
    fps=$(grep -ao "[0-9.]* fps" "$d/forza.log" | tail -1 | cut -d' ' -f1)
    [ -n "$fps" ] && awk -v f="$fps" 'BEGIN{exit !(f > 5)}' || { echo "frame rate ${fps:-none}"; return 1; }
    python3 -I - "$d/title.png" "$d/menu.png" <<'EOF' || return 1
import sys
from PIL import Image, ImageChops, ImageStat
a, b = (Image.open(p).convert("RGB") for p in sys.argv[1:3])
std = sum(ImageStat.Stat(b).stddev) / 3
diff = sum(ImageStat.Stat(ImageChops.difference(a.resize(b.size), b)).mean) / 3
print(f"menu image {b.size[0]}x{b.size[1]}, contrast {std:.1f}, change from the title screen {diff:.1f}")
if std < 8:
    print("the image is flat (black or garbage)"); sys.exit(1)
if diff < 3:
    print("the menu looks like the title screen: START did not get past it"); sys.exit(1)
EOF
    echo "fps $fps"
}

check_boot() {
    local rc=0 summary="" cfg build effect d
    for cfg in "linux-tu4 bilinear" "linux-tu4 cas" "linux-tu4 fsr" "linux-tu4 fsr3" "linux-tu4 smaa" "linux fsr"; do
        set -- $cfg; build=$1; effect=$2
        [ -x "$BUILD_ROOT/$build/forza" ] || { echo "no build $build"; rc=1; continue; }
        d="boot-$build-$effect"
        echo "== $build $effect"
        # one retry: a start can lose to the machine being busy
        local ok=0 try
        for try in 1 2; do
            if game_run "$d" "$build" "$effect" && verdict_menu "$LOG/$d"; then ok=1; break; fi
            cleanup_game
            echo "(attempt $try failed)"
        done
        cleanup_game
        if [ $ok = 1 ]; then summary="$summary $build/$effect"; else rc=1; fi
    done
    [ $rc = 0 ] && echo "RESULT: main menu:$summary"
    return $rc
}

# ---------------------------------------------------------------- server
check_server() {
    local d="$LOG/server-data" spid rc=0 ok=0 try
    mkdir -p "$d"
    python3 server/forza_live_server.py --host 127.0.0.1 --port $SERVER_PORT --http-port $SERVER_HTTP \
        --data-dir "$d" > "$LOG/server.out" 2>&1 &
    spid=$!
    sleep 1
    kill -0 $spid 2>/dev/null || { echo "the server did not start"; cat "$LOG/server.out"; return 1; }
    for try in 1 2; do
        if game_run server-game linux-tu4 fsr FORZA_SERVER=127.0.0.1:$SERVER_PORT; then
            ok=1; break
        fi
        cleanup_game
    done
    if [ $ok = 1 ] && verdict_menu "$LOG/server-game"; then :; else rc=1; fi
    local g="$LOG/server-game/forza.log"
    if grep -aq "FORZA_LIVE: signed in to" "$g"; then
        grep -a "FORZA_LIVE: signed in to" "$g" | head -1 | cut -c1-160
    else
        echo "the game did not sign in to the local server"; rc=1
    fi
    if grep -aq "XUserReadStats" "$g"; then
        grep -a "XUserReadStats" "$g" | tail -2 | cut -c1-200
        grep -aq "XUserReadStats.*from the server" "$g" || { echo "stats read not answered by the server"; rc=1; }
    else
        echo "(no XUserReadStats call before the main menu; the stats protocol is covered by server/test_forza_stats.py)"
    fi
    cleanup_game
    kill $spid 2>/dev/null; wait $spid 2>/dev/null
    [ $rc = 0 ] && echo "RESULT: signed in to the local server, main menu, no hang"
    return $rc
}

# ---------------------------------------------------------------- launcher (#53)
check_launcher() {
    local isos=() fake="$LOG/fakelibs" rc
    mkdir -p "$fake"
    # What a frozen launcher puts on the library path: libraries the system openssl cannot load
    head -c 4096 /dev/urandom > "$fake/libcrypto.so.3"
    head -c 4096 /dev/urandom > "$fake/libssl.so.3"
    # ... and what the programs it starts (git, rexglue, cmake, the game) load: the build machine's
    # libstdc++ is newer than the system's (#87: GLIBCXX_3.4.31 not found)
    head -c 4096 /dev/urandom > "$fake/libz.so.1"
    head -c 4096 /dev/urandom > "$fake/libpcre2-8.so.0"
    head -c 4096 /dev/urandom > "$fake/libstdc++.so.6"
    if [ -n "$iso" ]; then isos=("$iso"); else isos=(HorizonISO/*.iso); fi
    [ -f "${isos[0]}" ] || { echo "no disc image (--iso PATH, default HorizonISO/*.iso)"; return 1; }
    FAKE_LIBS="$fake" PYTHONPATH="$ROOT/launcher" python3 -I - "${isos[0]}" "$LOG/disc" "$ROOT" <<'EOF'
import os, subprocess, sys
# Like a frozen launcher: its own Python (and ssl) load fine, but the programs it starts
# (the system openssl) inherit a library path with libraries they cannot load.
os.environ["LD_LIBRARY_PATH"] = os.environ["FAKE_LIBS"]
iso, out, root = sys.argv[1:4]
sys.path[:0] = [root + "/launcher", root + "/tools"]
import apply_title_update as atu
from pathlib import Path
from forza_launcher import installer, xexcheck
from forza_launcher.installer import xiso_extract, xcontent_extract

# the AES helper under foreign libraries: the same answer as the built-in one
key, data = bytes(range(16)), bytes(range(256)) * 4
got = atu.aes_decrypt(key, data, cbc=True)
real = os.environ["PATH"]
os.environ["PATH"] = "/nonexistent"
want = atu.aes_decrypt(key, data, cbc=True)
os.environ["PATH"] = real
assert got == want, "openssl with the foreign library path gave a different result"
print("AES: openssl under a foreign LD_LIBRARY_PATH matches the built-in one")

# the disc: inspect, extract default.xex and the small executables, check them
p = installer.resolve_package(iso)
assert installer.is_disc_image(p), "not recognised as a disc image"
pkg = xiso_extract.open_iso(p)
entries = [e for e in pkg.entries() if e.path.split("/")[0].lower() not in installer.DISC_SKIP]
names = {e.path.lower(): e for e in entries}
assert "default.xex" in names, "no default.xex on the disc"
Path(out).mkdir(parents=True, exist_ok=True)
for e in entries:
    if e.is_dir:
        continue
    if e.path.lower() == "default.xex" or (e.path.lower().endswith((".dll", ".xex")) and e.size < 64 << 20):
        xcontent_extract.extract(pkg, e, out)
ok, msg = installer.inspect_xex(Path(out) / "default.xex")
print("default.xex:", msg)
assert ok, "default.xex refused"
ok, msg = installer.check_facades(Path(out))
print("facades:", msg)
assert ok, "facade check failed"
for f in getattr(pkg, "files", []):
    f.close()

# every program the launcher starts, as the frozen build starts it: PyInstaller's _internal on
# LD_LIBRARY_PATH (with and without the saved original), none of them may load its libraries (#87)
import subprocess
from forza_launcher import build_env, cleanenv, game
fake = os.environ["FAKE_LIBS"]
sys.frozen, sys._MEIPASS = True, fake
real_libs = os.environ.get("LD_LIBRARY_PATH_ORIG", "")
for orig in (None, "/usr/lib64"):
    os.environ["LD_LIBRARY_PATH"] = fake + (":" + orig if orig else "")
    os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig:
        os.environ["LD_LIBRARY_PATH_ORIG"] = orig
    bad = subprocess.run(["git", "--version"], capture_output=True, text=True)
    assert bad.returncode != 0, "the fake libraries do not break git: this check proves nothing"
    r = subprocess.run(["git", "--version"], capture_output=True, text=True, env=build_env.tool_env(build_env.Tools()))
    assert r.returncode == 0, "git under the tool environment: " + r.stderr.strip()
    plan = game.LaunchPlan(game=None, cwd=Path(out), argv=["git", "--version"], env={}, unset=[],
                           toml_path=Path(out) / "t.toml", toml_text="", server_argv=None)
    s = game.Session(plan, log=lambda _t: None)
    s.start()
    assert s.proc.wait(timeout=30) == 0, "the game process inherited the launcher's library path"
    s.cleanup()
    env = cleanenv.clean_environment()
    assert fake not in env.get("LD_LIBRARY_PATH", ""), env.get("LD_LIBRARY_PATH")
print("children: no frozen LD_LIBRARY_PATH leaks (saved original: %s)" % ("yes" if orig else "no"))
EOF
    rc=$?
    # the entry point the frozen build runs
    python3 -c "import sys; sys.path.insert(0, '$ROOT/launcher'); from forza_launcher import cli; sys.exit(cli.main(['--version']))" || rc=1
    [ $rc = 0 ] && echo "RESULT: ${isos[0]##*/}: default.xex and facades pass under a foreign library path"
    return $rc
}

# ---------------------------------------------------------------- ci
check_ci() {
    local before id t0
    before=$(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || echo 0)
    gh workflow run release.yml || return 1
    for _ in $(seq 1 30); do
        sleep 4
        id=$(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null)
        [ -n "$id" ] && [ "$id" != "$before" ] && break
        id=""
    done
    [ -n "$id" ] || { echo "the run did not appear"; return 1; }
    echo "run $id"
    timeout 5400 gh run watch "$id" --exit-status > "$LOG/ci-watch.txt" 2>&1
    local rc=$?
    gh run view "$id" --json conclusion,jobs -q '"conclusion: \(.conclusion)", (.jobs[] | "  \(.name): \(.conclusion)")'
    echo "RESULT: run $id $([ $rc = 0 ] && echo green || echo FAILED)"
    return $rc
}

trap 'cleanup_game; [ -n "${spid:-}" ] && kill "$spid" 2>/dev/null' EXIT
echo "logs: $LOG"
run_check sdk check_sdk
run_check build check_build
run_check unit check_unit
run_check boot check_boot
run_check server check_server
run_check launcher check_launcher
run_check ci check_ci
echo "summary: $passes passed, $fails failed (logs: $LOG)"
exit $(( fails > 255 ? 255 : fails ))
