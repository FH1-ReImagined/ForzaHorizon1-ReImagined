# Performance

## Multi-resolution reconstruction cost

The launcher supports 1080p, 1440p and 4K output with Quality, Balanced,
Performance and DLAA. NGX receives its recommended input dimensions; the scene
still renders at an integer emulator scale rounded up to contain that input.
Between-scale inputs therefore pay for extra scene pixels and an input resample.
For example, 4K Performance currently renders a 1440p scene, samples it to 1080p,
and reconstructs to 4K. 1080p Quality renders 720p directly and reconstructs to
1080p. The HUD raster stays at output resolution in both cases. See
[Native DLSS](native_dlss.md) for the dimension table and validation limits.
These launch checks are not a matched performance benchmark.

## Co-op stall and sunrise FPS report (#444, 2026-10-25)

I inspected both attached logs: they are successive snapshots of the same
session. Sustained low FPS precedes the multi-second GPU fence waits, and the
longer attachment shows recovery. The runtime cause remains unresolved.
[Evidence, timeline and effective startup settings](issue-444-investigation.md).

## Native NVIDIA reconstruction, 2026-10-15 (before review fixes)

These measurements predate the scene/HUD shader-cache correction. They are
historical bring-up results, not performance validation of the corrected renderer.
A new matched performance comparison is still needed.

Windows, RTX 5060 Ti, Vulkan, 2560x1440 output, 120 FPS cap and 16x anisotropic
filtering. Each isolated run loaded a fresh copy of the same profile at the opening
spawn, with the car stationary. The table uses guest-frame CSV samples from seconds
55â€“70 after startup. No FH2 process was running in this set. These are one-scene
measurements, not a race benchmark or a guarantee of 120 FPS.

| Mode | Scene input | HUD | Guest FPS | Guest p99 (ms) |
| --- | --- | --- | ---: | ---: |
| Native | 2560x1440 | 2560x1440 | 53.9 | 20.0 |
| DLSS Performance | 1280x720 | 2560x1440 | 88.1 | 12.6 |
| DLAA | 2560x1440 | 2560x1440 | 50.0 | 21.7 |

The earlier proxy rendered the scene at full resolution and downsampled it before
DLSS. The native path instead uses a separate 1x scene cache, game depth/object
reprojection, and viewport jitter, and reconstructs before the native HUD draws.
These measurements use the corrected tonemapped-input flag and include the public
main changes through `cc638e7` and the NVIDIA reflection-sampling correction
(SDK patch 0047). All three runs have zero scene-transfer failures;
the NVIDIA runs also have zero feature/evaluation failures. See [Native DLSS](native_dlss.md) for scope and known issues.

## Vulkan presentation pipeline cache, 2026-10-14

SDK patch 0045 records the output format when the presenter creates its pipeline.
Previously it waited for the prior submission, destroyed the pipeline and created
it again on every paint. The unfixed 75-second run created over 3,100 host graphics
pipelines; the corrected run stopped repeatedly creating that pipeline. This fixes
frame delivery overhead, without promising a particular rendering frame rate.

## Frame statistics

Frame statistics use the same definitions as RecompiledHaloReach so the two projects
compare (`src/debug/frame_stats.cpp`, enabled with `RECOMP_PERF=1`):

```
PERF: scene= src=<guest|present> t= fps= p50= p95= p99= max= low1= stutters= hitches= ontarget= pipelines=
```

- One line per 10 s window, per scene change and at exit, for each source:
  `guest` = the game's frames (VdSwap), `present` = frames the GPU plugin handed to the
  presenter (SDK patch 0024; frames that drew with an async placeholder shader are not
  presented).
- Frame times in ms; `low1` = 1000 / mean of the slowest 1% of frame times;
  `stutters` = frames longer than 2x the window's p50; `hitches` = frames longer than
  100 ms; `ontarget` = % of frames within +-20% of the target frame time (33.3 ms, or
  16.7 ms with `frame_rate = 60`; two guest vblanks); `pipelines` = graphics pipelines
  created in the window (the GPU plugin's `vkCreateGraphicsPipelines` and
  `vkCreateComputePipelines` calls, placeholders included; the plugin's "Creating graphics
  pipeline state" log lines stop after the first 32 since ReIGlue 0422, #609).
- `RECOMP_PERF_CSV=<file>` writes every frame; `RECOMP_PERF_SCENE` names the first scene;
  `SCENE:<name>` on the input FIFO starts a new one.

## Long frames (FORZA_STALL)

Always on (`src/debug/stall_watch.cpp`, #187): when the game hands no frame to VdSwap for
200 ms, `forza.log` gets a `FORZA_STALL: no frame for <ms> ms at t=<s>` line followed by the
guest call chains of the main loop's thread and the render thread at that moment, and when
the frame comes, `FORZA_STALL: frame took <ms> ms from t=<s>`. `main loop N frames` says
whether the main loop kept running (N > 0: the render thread was held up) or was stuck too.
Nothing is logged in the first minute; at most one chain dump every 10 s (50 a session).
No frame for 10 s logs `not presenting` once and `presenting again` at the end. Loading
screens can log long frames too. A player who sees a hitch can attach these lines to an
issue: the chains are guest return addresses (look them up in Ghidra). `FORZA_STALL_TEST=1`
makes the main loop sleep 500 ms once, 90 s after the first frame, to check the lines.
A render thread at lr 82419338 (Title Update 4; 823E9288 on the disc) is in the D3D library's
wait for a GPU fence (`src/hooks/gpu_wait.cpp`): the emulated GPU was behind, and the
`GPU slow frames` line of the same seconds says what the command processor did (#167: it waited
for new pipelines at the frame end, which it counted as idle, "the game").

## Slow database queries (FORZA_SQL)

Always on (`src/hooks/database_query.cpp`, #167): the game runs its SQLite queries on the
thread that asks, the main loop included. A query that takes 50 ms or more logs
`FORZA_SQL: <ms> ms (lock <ms> ms) on thread <handle>: <the SQL>` (at most 100 lines a
session); `lock` is the time outside the statements, mostly waiting for another thread's query
(the game's cached copy calls the plain one on a miss; only the outer one is timed). `FORZA_SQL_LOG=1`
logs every query with its time (about 200 a second in free roam). A FORZA_STALL chain in
`sub_83146F20` / `sub_83146E30` (TU4 `sqlite3_step`) is a query; the FORZA_SQL line says which.

## Scenarios

`tools/perf_scenario.sh SCENARIO DIR [cold|warm] [flags]` drives the game through the
input FIFO and prints the PERF lines:

| Scenario | What |
| --- | --- |
| `menu` | boot (intro video), title screen, START, main menu |
| `drive` | Single Player from a profile that has not finished the prologue: intro video, then the opening drive at full throttle |
| `freeroam` | Single Player from a profile in free roam (e.g. the user's Xenia save in a scratch `--user_data_root` with `FORZA_XUID`): loading, then 90 s of driving |

`cold` starts with an empty shader cache (`--cache_root` in the run directory); `warm` uses
the normal one.

**Machine load matters.** The test machine (Ryzen 7 7800X3D, 8 cores; Radeon RX 9060 XT,
RADV) was shared with other game instances and builds, each game
using 1.5-2.5 cores. Rules:

- Numbers for this file and for before/after comparisons come from a clean window: take
  the lock file `BENCH_LOCK` (`owner expiry-epoch purpose`, see `tools/bench_lock.sh`) for at most
  15 minutes and pause other load. Delete it when done.
- The run scripts (`tools/live_session.sh`, `tools/run_forza.sh`, `tools/build.sh`)
  refuse to start while someone else holds an unexpired lock (`tools/bench_lock.sh`).
- `tools/perf_scenario.sh` records other game processes, the load average and free memory
  at the start and end and prints `status=CONTENDED` when another game ran or the
  1-minute load average was above 2. Contended results are not baselines.
- The boot videos are skipped with `--skip_startup_movies=true`, which `tools/live_session.sh`,
  `tools/run_forza.sh` and so `tools/perf_scenario.sh` pass by default (`FORZA_SKIP_MOVIES=false`
  plays them; a flag after the script's arguments wins). The in-game videos (the festival
  intro) are still skipped through the input FIFO, and each scene is only labelled once
  it is reached.
  The tools emit one startup flag (the last explicit startup option wins over the
  environment), and the scenario's boot input sequence follows that same effective value.

## Results

Results marked CONTENDED were taken while other games ran; they are not baselines.

| Date | Commit | Scenario | Cache | Load avg | Scene | Source | fps | p50 | p95 | p99 | max | low1 | stutters | hitches | ontarget |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-10 | 741ccf4 | menu | warm | CONTENDED: ~15-20, 2 Reach instances | intro video | guest | 59.9 | 16.6 | 18.7 | 20.3 | 50.5 | 35.9 | 1 | 0 | n/a (60 fps video) |
| 2026-10-10 | 741ccf4 | menu | warm | CONTENDED: ~15-20, 2 Reach instances | main menu | guest | 30.0 | 33.4 | 35.4 | 36.8 | 39.1 | 26.1 | 0 | 0 | 100 |
| 2026-10-10 | 741ccf4 | menu | warm | CONTENDED: ~15-20, 2 Reach instances | main menu | present | 30.0 | 33.3 | 48.1 | 48.9 | 49.9 | 20.1 | 0 | 0 | 54-69 |
| 2026-10-10 | 741ccf4 | freeroam (Xenia save) | warm | CONTENDED: ~23, 2 Reach instances | free roam (8 windows) | guest | 27.2-29.4 | 33.5-35.7 | 47.5-55.0 | 52.7-74.2 | 54-118 | 10.3-18.5 | 0-9 | 0-1 | 57-71 |
| 2026-10-10 | 741ccf4 | freeroam (Xenia save) | warm | CONTENDED: ~23, 2 Reach instances | free roam (8 windows) | present | 27.0-29.3 | 33.4-33.8 | 40.3-56.6 | 50.9-83.1 | 54-118 | 9.3-18.5 | 0-8 | 0-1 | 81-94 |
| 2026-10-10 | 96c43fd+ | freeroam (Xenia save), SDK XMPGetPlaybackController (10 ms sleep) | warm | 1.9-4.5, no other game (CLEAN) | free roam (10 windows) | guest | 29.9-30.0 | 33.3-33.5 | 35.0-35.7 | 35.9-51.4 | 37-62 | 17.6-27.4 | 0 | 0 | 96-100 |
| 2026-10-10 | 96c43fd+ | freeroam (Xenia save), XMPGetPlaybackController answered without the sleep | warm | 4.4-4.5, no other game (CONTENDED by load) | free roam (10 windows) | guest | 29.6-30.0 | 33.3-33.5 | 35.0-36.4 | 35.9-63.5 | 37-81 | 13.7-27.0 | 0-1 | 0 | 96-100 |
| 2026-10-10 | build before 0025 (GJK hook) | freeroam (Xenia save) | warm | 2.5, no other game | free roam | guest | 29.8 | 33.3 | 36.0 | 47.7 | 75.5 | 17.1 | 5 | 0 | 96.9 |
| 2026-10-10 | build before 0025 (GJK hook) | freeroam (Xenia save) | warm | 2.5, no other game | free roam | present | 29.6 | 33.4 | 34.5 | 65.3 | 100.7 | 14.2 | 15 | 1 | 98.2 |
| 2026-10-10 | patch 0025, no hook | freeroam (Xenia save) | warm | 4.2, no other game | free roam | guest | 29.9 | 33.3 | 35.3 | 37.8 | 66.6 | 20.8 | 0 | 0 | 99.1 |
| 2026-10-10 | patch 0025, no hook | freeroam (Xenia save) | warm | 4.2, no other game | free roam | present | 29.8 | 33.4 | 34.4 | 35.5 | 68.2 | 19.1 | 3 | 0 | 99.3 |
| 2026-10-10 | frame_rate 30 | freeroam (Xenia save) | warm | 4.0-5.8, no other game (reserved) | main menu | guest | 30.0 | 33.5 | 34.1 | 34.2 | 34.4 | 29.1 | 0 | 0 | 100 |
| 2026-10-10 | frame_rate 60 | freeroam (Xenia save) | warm | 4.0-5.8, no other game (reserved) | main menu | guest | 60.0 | 16.8 | 17.3 | 17.4 | 17.8 | 56.9 | 0 | 0 | 100 |
| 2026-10-10 | frame_rate 30 | freeroam (Xenia save) | warm | 4.0-5.8, no other game (reserved) | free roam | guest | 29.9 | 33.4 | 35.5 | 38.9 | 90.4 | 18.7 | 2 | 0 | 98.7 |
| 2026-10-10 | frame_rate 30 | freeroam (Xenia save) | warm | 4.0-5.8, no other game (reserved) | free roam | present | 29.9 | 33.4 | 34.4 | 34.9 | 84.8 | 20.1 | 2 | 0 | 99.3 |
| 2026-10-10 | frame_rate 60 | freeroam (Xenia save) | warm | 4.0-5.8, no other game (reserved) | free roam | guest | 58.9 | 16.8 | 19.5 | 24.8 | 46.8 | 32.4 | 8 | 0 | 94.8 |
| 2026-10-10 | frame_rate 60 | freeroam (Xenia save) | warm | 4.0-5.8, no other game (reserved) | free roam | present | 58.8 | 16.8 | 18.0 | 25.5 | 46.7 | 31.1 | 10 | 0 | 96.6 |
| 2026-10-10 | frame_rate 30 | race (festival race, 7 AI cars) | warm | 4.0-5.8, no other game (reserved) | race | guest | 30.0 | 33.4 | 35.8 | 37.1 | 58.0 | 24.1 | 0 | 0 | 99.4 |
| 2026-10-10 | frame_rate 60 | race (festival race, 7 AI cars) | warm | 4.0-5.8, no other game (reserved) | race | guest | 41.6 | 24.8 | 30.2 | 34.1 | 44.8 | 26.4 | 0 | 0 | 18.4 |
| 2026-10-10 | frame_rate 60 | race (festival race, 7 AI cars) | warm | 4.0-5.8, no other game (reserved) | race | present | 41.6 | 24.9 | 32.4 | 34.0 | 50.3 | 26.5 | 1 | 0 | 18.6 |
| 2026-10-11 | GPU A/B: SDK as shipped (nightly runtime) | race (festival race, 7 AI cars) | warm | under 2 at start, no other game (reserved) | race | guest | 41.4 / 42.4 | 24.8 / 24.7 | 29.4 / 30.5 | 32.8 / 33.6 | 50.1 / 53.7 | 26.6 / 26.8 | 1 / 1 | 0 | 15.8 / 23.8 |
| 2026-10-11 | GPU A/B: runtime with Reach SDK patch 0028 only | race (festival race, 7 AI cars) | warm | under 2 at start, no other game (reserved) | race | guest | 49.3 | 17.8 | 25.9 | 26.8 | 32.4 | 35.8 | 0 | 0 | 56.7 |
| 2026-10-11 | GPU A/B: plugin with Reach SDK patches 0029-0032 only | race (festival race, 7 AI cars) | warm | under 2 at start, no other game (reserved) | race | guest | 50.3 | 18.4 | 26.2 | 27.8 | 42.1 | 32.7 | 1 | 0 | 60.2 |
| 2026-10-11 | GPU A/B: Reach SDK patches 0028-0032 | race (festival race, 7 AI cars) | warm | under 2 at start, no other game (reserved) | race | guest | 59.7 / 59.7 | 16.8 / 16.8 | 17.9 / 17.9 | 20.9 / 20.7 | 33.6 / 35.7 | 42.6 / 43.2 | 1 / 1 | 0 | 98.6 / 98.6 |
| 2026-10-12 | frame_rate 60, before the frame time hook | race (festival race, 7 AI cars), 26 s | warm | 1.5, no other game (reserved) | race | guest | 59.6 | 16.8 | 18.1 | 21.6 | 28.7 | 41.5 | 0 | 0 | 97.9 |
| 2026-10-12 | frame_rate 60, before the frame time hook | race (festival race, 7 AI cars), 26 s | warm | 1.5, no other game (reserved) | race | present | 59.0 | 16.8 | 17.4 | 32.1 | 43.9 | 25.4 | 14 | 0 | 97.8 |
| 2026-10-12 | frame_rate 60 (frame time hook) | race (festival race, 7 AI cars), 26 s | warm | 3.5, no other game (reserved) | race | guest | 59.6 | 16.8 | 18.0 | 21.9 | 29.1 | 42.5 | 0 | 0 | 98.0 |
| 2026-10-12 | frame_rate 60 (frame time hook) | race (festival race, 7 AI cars), 26 s | warm | 3.5, no other game (reserved) | race | present | 58.7 | 16.8 | 17.7 | 32.7 | 44.2 | 25.4 | 15 | 0 | 96.7 |
| 2026-10-12 | frame_rate 120 (240 Hz vblank) | race (festival race, 7 AI cars), 26 s | warm | 2.7, no other game (reserved) | race | guest | 75.7 | 13.0 | 17.6 | 18.6 | 23.9 | 50.8 | 0 | 0 | 25.8 |
| 2026-10-12 | frame_rate 120 (240 Hz vblank) | race (festival race, 7 AI cars), 26 s | warm | 2.7, no other game (reserved) | race | present | 73.0 | 13.1 | 17.2 | 18.2 | 30.3 | 49.5 | 1 | 0 | 19.9 |
| 2026-10-12 | frame_rate 144 (144 Hz vblank) | race (festival race, 7 AI cars), 26 s | warm | 5.1, no other game (reserved) | race | guest | 65.2 | 14.3 | 20.8 | 21.8 | 31.8 | 42.8 | 1 | 0 | 0.0 |
| 2026-10-12 | frame_rate 144 (144 Hz vblank) | race (festival race, 7 AI cars), 26 s | warm | 5.1, no other game (reserved) | race | present | 65.3 | 14.1 | 21.0 | 21.5 | 28.2 | 44.2 | 1 | 0 | 0.1 |
| 2026-10-12 | frame_rate 60, before the frame time hook | freeroam (Xenia save), 50 s | warm | 4.6, no other game (reserved) | free roam | guest | 59.9 | 16.7 | 18.1 | 19.7 | 30.5 | 42.4 | 0 | 0 | 98.3 |
| 2026-10-12 | frame_rate 60, before the frame time hook | freeroam (Xenia save), 50 s | warm | 4.6, no other game (reserved) | free roam | present | 59.9 | 16.8 | 17.5 | 18.8 | 32.9 | 47.4 | 0 | 0 | 99.5 |
| 2026-10-12 | frame_rate 60 (frame time hook) | freeroam (Xenia save), 50 s | warm | 3.6, no other game (reserved) | free roam | guest | 59.9 | 16.7 | 17.8 | 19.3 | 31.3 | 44.7 | 0 | 0 | 99.0 |
| 2026-10-12 | frame_rate 60 (frame time hook) | freeroam (Xenia save), 50 s | warm | 3.6, no other game (reserved) | free roam | present | 59.9 | 16.8 | 17.2 | 18.0 | 32.9 | 48.7 | 0 | 0 | 99.6 |
| 2026-10-12 | frame_rate 120 (240 Hz vblank) | freeroam (Xenia save), 50 s | warm | 3.7, no other game (reserved) | free roam | guest | 115.2 | 8.4 | 11.5 | 13.1 | 27.6 | 63.7 | 15 | 0 | 91.0 |
| 2026-10-12 | frame_rate 120 (240 Hz vblank) | freeroam (Xenia save), 50 s | warm | 3.7, no other game (reserved) | free roam | present | 113.1 | 8.5 | 12.5 | 16.3 | 26.7 | 55.0 | 42 | 0 | 89.0 |
| 2026-10-12 | frame_rate 144 (144 Hz vblank) | freeroam (Xenia save), 50 s | warm | 4.6, no other game (reserved) | free roam | guest | 71.9 | 13.8 | 15.3 | 16.4 | 27.7 | 52.1 | 1 | 0 | 0.1 |
| 2026-10-12 | frame_rate 144 (144 Hz vblank) | freeroam (Xenia save), 50 s | warm | 4.6, no other game (reserved) | free roam | present | 71.9 | 13.8 | 14.8 | 15.7 | 21.8 | 57.6 | 0 | 0 | 0.0 |
| 2026-10-12 | frame_rate 144, SDK patch 0037 (288 Hz vblank) | race (festival race, 7 AI cars), 26 s | warm | 1.3, no other game (reserved) | race | guest | 70.1 | 14.2 | 18.0 | 19.0 | 23.2 | 49.8 | 0 | 0 | 2.3 |
| 2026-10-12 | frame_rate 144, SDK patch 0037 (288 Hz vblank) | race (festival race, 7 AI cars), 26 s | warm | 1.3, no other game (reserved) | race | present | 70.1 | 13.9 | 18.0 | 18.4 | 23.7 | 50.5 | 0 | 0 | 2.6 |
| 2026-10-12 | frame_rate 144, SDK patch 0037 (288 Hz vblank) | freeroam (Xenia save), 50 s | warm | 4.3, no other game (reserved) | free roam | guest | 111.5 | 9.2 | 11.2 | 12.5 | 23.1 | 67.3 | 10 | 0 | 41.4 |
| 2026-10-12 | frame_rate 144, SDK patch 0037 (288 Hz vblank) | freeroam (Xenia save), 50 s | warm | 4.3, no other game (reserved) | free roam | present | 110.6 | 9.5 | 10.8 | 13.3 | 23.7 | 65.0 | 6 | 0 | 40.8 |
| 2026-10-12 | GPU A/B: SDK as shipped (0001-0037) | race (festival race, 7 AI cars), frame_rate 144 | warm | previous run's game, none other (reserved) | race | guest | 72.8 | 13.9 | 17.7 | 18.7 | 26.6 | 50.8 | 0 | 0 | 3.5 |
| 2026-10-12 | GPU A/B: + SDK patches 0036, 0038 | race, frame_rate 144 | warm | same | race | guest | 78.8 | 13.5 | 15.9 | 17.4 | 26.7 | 53.8 | 0 | 0 | 6.8 |
| 2026-10-12 | GPU A/B: + 0036, 0038, `--clear_memory_page_state=false` | race, frame_rate 144 | warm | same | race | guest | 93.2 | 10.6 | 14.3 | 15.9 | 23.7 | 54.9 | 2 | 0 | 18.5 |
| 2026-10-12 | GPU A/B: + 0036, 0038, 0039 | race, frame_rate 144 | warm | same | race | guest | 97.2 | 10.5 | 14.0 | 15.0 | 25.4 | 62.9 | 1 | 0 | 24.1 |
| 2026-10-12 | GPU A/B: SDK as shipped | race, frame_rate 120 | warm | same | race | guest | 72.3 | 13.4 | 17.6 | 18.4 | 24.0 | 51.1 | 0 | 0 | 13.6 |
| 2026-10-12 | GPU A/B: + 0036, 0038 | race, frame_rate 120 | warm | same | race | guest | 82.4 | 12.5 | 16.8 | 17.6 | 26.9 | 51.4 | 2 | 0 | 27.1 |
| 2026-10-12 | GPU A/B: + 0036, 0038, 0039 | race, frame_rate 120 | warm | 0.4, no other game (reserved) | race | guest | 90.4 | 12.0 | 13.3 | 14.2 | 36.6 | 62.1 | 1 | 0 | 34.9 |
| 2026-10-12 | GPU A/B: SDK as shipped | race, frame_rate 60 | warm | same | race | guest | 59.8 | 16.8 | 17.7 | 20.1 | 27.4 | 44.9 | 0 | 0 | 98.6 |
| 2026-10-12 | GPU A/B: + 0036, 0038, 0039 | race, frame_rate 60 | warm | same | race | guest | 60.0 | 16.8 | 17.5 | 18.1 | 35.9 | 49.6 | 1 | 0 | 99.6 |
| 2026-10-12 | GPU A/B: SDK as shipped | freeroam (Xenia save), 50 s, frame_rate 144 | warm | same | free roam | guest | 131.4 | 7.3 | 10.5 | 11.3 | 20.5 | 79.4 | 7 | 0 | 80.0 |
| 2026-10-12 | GPU A/B: + 0036, 0038, 0039 | freeroam (Xenia save), 50 s, frame_rate 144 | warm | same | free roam | guest | 140.8 | 7.1 | 8.6 | 10.6 | 21.6 | 86.6 | 5 | 0 | 93.3 |

- 2026-10-12 rows (docs/framerate.md): one reserved window, one game at a time, back to back
  (the load average is the previous run's game), the race driven at full throttle for 26 s
  from the countdown. "Before" is the binary before `src/hooks/frame_time.cpp`. Above 60 fps
  the guest frames are the render thread's (free roam: 50 s at full throttle): the main loop
  runs at the target rate whatever the render thread makes (`FORZA_FRAMETIME_LOG`), and render frames come in whole guest
  vblanks (12.5 or 16.7 ms at 120, 13.9 or 20.8 ms at 144 with a 144 Hz vblank, multiples
  of 3.5 ms with SDK patch 0037's 288 Hz), so `ontarget` (8.3 / 6.9 ms) is low when the GPU
  emulation is the bound. The 144 Hz-vblank rows are the build before patch 0037 (frame_rate
  144 falls back on a divider of one without it).
- `frame_rate = 60` (docs/framerate.md), one reserved window, one game at a time; the load
  average is the game's own (it keeps 3-4 cores busy). Free roam: 90 s at full throttle
  from the user's Xenia save; race: GoPro Millennia Masters (signed up at that save's spawn
  point), 25 s at full throttle from the start. `ontarget` counts against 16.7 ms at 60.
  Races are bound by the GPU emulation (the render thread waits in D3D's `BlockOnFence`).
- SDK patch 0025 (dot products give QNaN on overflow) A/B in one reserved window with no
  other game running (the script still says CONTENDED: the load average was above 2). The
  run before compiled 52 pipelines in free roam and the run after 20 (the first run warmed
  the shared cache), which explains the better tail after; the patch itself costs nothing
  measurable.

Reading so far:

- XMPGetPlaybackController (music player app 0xFA, message 0x7001B): RecompiledHaloReach
  found its audio thread asking once per frame and the SDK's 10 ms sleep (on non-main
  threads) holding it to 45 fps. Forza polls it from a loop instead: about 46 calls/s
  with the sleep, about 10,500/s without it (a core spinning), and free roam holds 30 fps
  either way (the two rows above). The SDK's answer stays; `FORZA_XMP_NOSLEEP=1` answers
  at once, for frame-rate experiments above 30 fps.
- (Contended runs.) The game held its 30 fps cap in menus and close to it in free roam
  (27-29 fps) even with two Reach instances running. The 12 fps the user saw has not been
  reproduced in a measured run yet; candidates are a heavier scene, a cold shader cache,
  or more load on the machine at that moment (other games and builds).
- Free roam: one guest frame in ten takes 47-55 ms (p95), and the slowest 1% 70-120 ms.
  That is the stutter to explain first.
- Main menu presents: frames reach the presenter every 33 ms on average but p95 is 48 ms
  (the present cadence alternates around the guest's regular 33 ms frames): pacing to
  look at.

## GPU emulation speed (2026-10-11)

Races at `frame_rate = 60` were bound by the emulated GPU's command processor. Profiled with
user-space `perf_event_open` sampling (frame-pointer call chains; samply refuses at
`perf_event_paranoid = 2`, this works) and a wall-clock sampler of the command processor
thread; the details are in RecompiledHaloReach `docs/perf.md` > "GPU emulation speed", where
the fixes live as SDK patches 0028-0032:

- **0028 (runtime, `librexruntime.so`):** every write-watch fault (the game writing vertex
  data the GPU had uploaded, thousands a second) rechecked the page's protection by reading
  and parsing all of `/proc/self/maps` (~1,800 lines) while holding the global critical
  region. The command processor needs that lock for its shared-memory uploads and waited on
  it ~17% of the time. Now one `PROCMAP_QUERY` ioctl (Linux 6.11+).
- **0029-0032 (GPU plugin):** register writes as ranges (they were 30% of the command
  processor: 40 M a second, each with a register-table switch only for a debug log),
  `WAIT_REG_MEM` wakes on the guest vblank interrupt instead of 1 ms sleeps, shared-memory
  requests without the lock when the pages are valid, and unchanged fetch constants keep
  their texture bindings.

Results are the four "GPU A/B" rows above (one reserved window, same game binary, two runs
where two values are given): the race goes from 41-42 fps (p99 33 ms) to 59.7 fps (p50
16.8, p99 20.8 ms, 98.6% of frames on target). Either half alone reaches about 50 fps:
frames snap to the 120 Hz guest vblank, so the command processor had to fit both. This
project's SDK overlay ships the nightly `librexruntime.so`, so 0028 needs the overlay to
carry a patched runtime as Reach's does. Screenshots of the race menu and the race match the
unpatched build.

## GPU emulation at high refresh rates (2026-10-12)

Above 60 fps the races were bound by the emulated GPU's command processor again (its thread
busy 99.7% of the time, the render thread spinning in `BlockOnFence`). RecompiledHaloReach
`docs/perf.md` > "GPU emulation at high refresh rates" has the profile; the fixes are SDK
patches carried by both projects (`docs/sdk_patches.md`):

- **0036:** a vertex or index upload no longer ends the current render pass when no draw in it
  has read the uploaded pages; it goes before the render pass with the other uploads there
  (52,000 render pass instances and 133,000 barriers a second in the race at 144 before,
  10,600 and 27,000 after).
- **0038:** the deferred command buffer is replayed into Vulkan and submitted on a thread of its
  own (that was 11% of the command processor thread).
- **0039:** `clear_memory_page_state` off by default, as in Xenia. On (the SDK's default),
  every page the CPU had written and the GPU read was uploaded again after every frame: 74,000
  uploads (2 GB) a second in the race at 144; 1,800 off.

The "GPU A/B" rows above (two reserved windows, runs back to back with one game at a time, the
same game binary and runtime, only the executable-folder plugin differs): the race at 144 goes
from 72.8 to 97.2 fps (p99 18.7 to 15.0 ms), the race at 120 from 72.3 to 90.4 fps (p99 18.4
to 14.2 ms), free roam at 144 from 131.4 to 140.8 fps, and the
race at 60 keeps 60 fps with a shorter tail (p99 20.1 to 18.1 ms). The remaining command
processor time is spread out: descriptor updates 17%, register writes 14%, hash lookups 8%,
render target updates 7%.

Installed 2026-10-12 13:39 (`tools/build_rexglue_sdk.sh`: the overlay `~/rexglue-sdk-patched-forza`
and `forza-recomp/out/build/linux`). `--vulkan_hoist_shared_memory_uploads=false`,
`--vulkan_threaded_submission=false` and `--clear_memory_page_state=true` switch each change off.

## Resolve readback (2026-10-14)

`readback_resolve = "fast"` became the default (car thumbnails, `docs/framerate.md`). Its
cost, race scenario at 144 (base build, Xenia save), runs back to back, one game at a time;
CONTENDED: a VM ran the whole time (load 4-5), so only the differences within a pair count:

| Pair | readback_resolve | Race intro fps | Race fps (p50 / p95 / p99 ms) |
| --- | --- | --- | --- |
| 1 | none | 104.2 | 91.0 (10.6 / 14.0 / 14.9) |
| 1 | fast | 98.4 | 80.6 (13.3 / 15.0 / 16.8) |
| 2 | none | 103.1 | 88.9 (11.1 / 14.9 / 16.3) |
| 2 | fast | 95.2 | 68.8 (14.2 / 19.4 / 29.2) |

So `fast` costs 6-8% in the intro and 11-23% in the race at 144: it copies every resolve
back every frame, while the game only needs the thumbnail renders. Not measured at 30 and
60. Next: read back only while a thumbnail is being made (the hooks can set the setting at
run time: it is read at each resolve), or `some` (copy on cache miss) if it gets the
thumbnails right. `readback_resolve = "none"` in `forza.toml` restores the old speed.
Superseded: the default is `some` now (next section).

### readback_resolve cost (#27, 2026-10-14/15)

`readback_resolve` (SDK `IssueCopy_ReadbackResolvePath`): in every mode but `none`, each
resolve (EDRAM to memory) also copies its written range into a host readback buffer
(extra barriers and a shared-memory state change). `fast` then waits for nothing, maps
the *other* of two buffers (last resolve of the same address and length, so a frame
late), invalidates it and memcpy's it into guest memory on every resolve; a first-seen
(address, length) is a cache miss and waits for the GPU (`AwaitAllQueueOperationsCompletion`)
and reads the new data. `some` does the same but only copies on a cache miss (buffers
are evicted after 60 unused frames, so a car picture rendered rarely is mostly a miss).
`full` waits for the GPU on every resolve. `none` skips all of it.

Measured on the TU4 build, scratch copy of the Xenia save (pristine copy for every run),
warm shader cache, one game at a time and no other game running (the BENCH lock held;
the status line says CONTENDED only because the 1-minute load average was still 1.5-4 from
the previous run, nothing else ran). Guest fps of the race scene / free roam (p50 / p99 ms),
and the race intro:

| Scenario | none | some | fast | full on resolves of 200,000-884,736 bytes (tried) |
| --- | --- | --- | --- | --- |
| Race at 144 | 93.5 / 93.0 (10.7 / 15.2) | 93.5 / 91.2 (10.6 / 15.0) | 77.8 / 78.1 (13.5 / 17.4) | 79.2 / 62.1 (13.3 / 16.3) |
| Race intro at 144 | 102.4 / 102.5 | 102.6 / 102.3 | 96.7 / 97.9 | 82.8 / 75.7 |
| Race at 60 | 60.0 (16.8 / 18.2) | not run | 59.9 (16.8 / 18.5) | 58.1 (16.9 / 24.3) |
| Free roam at 144 | 137.3 (7.2 / 11.0) | 139.9 (7.1 / 10.7) | 108.1 (9.6 / 12.1) | 85.0 (11.0 / 15.8) |

(Two numbers = two runs.) `fast` costs 16% in the race and 21% in free roam at 144 and
nothing at 60; `some` costs nothing measurable. The extra GPU copy `some` still does per
resolve is cheap; the cost of `fast` is the invalidate and `memcpy` of every resolve
(frame-sized ones, 1-4 MB, several a frame) into guest memory, and `full` (a GPU wait per
resolve) is worse still.

Thumbnails (scratch profile, Dak's Garage, Auto Upgrade of a car whose picture was noise;
new `Thumbnail_N.xdc` decoded with `tools/xdc_decode.py`):

- `fast`: a correct BMW M1 render (91,135 bytes).
- `some`: correct renders of the BMW M1 (89,165 bytes) and, 75 s later in the same session,
  a Toyota Trueno (86,605 bytes). This is the default now.
- `none`: noise (the bug of #3).
- The picture is three kinds of resolves: the car rendered into two 640x448 targets
  (1,146,880 bytes) and composed into the 768x288 picture by two resolves (864,256 and
  806,912 bytes, overlapping inside the 884,736 byte texture, so a filter on that size
  reads back nothing), and one resolve of 262,144 bytes (an extra render target the car
  shader probably samples; without its readback the car comes out darker and the side view dim, 51 KB).
  Hence the targeted ideas in the old notes (an SDK length filter, a guest hook) were not
  needed: SDK patch 0041 (copy back only resolves in a length window) was written and
  tested, then dropped because `some` is already free.
- `some` copies a resolve back only the first time its (address, length) is seen or after
  60 frames unused, so two pictures made less than 60 frames apart could come out stale;
  the game makes one every few seconds at most.

**Photo mode (#54, 2026-10-15).** The saved photo was garbage while the screen looked right:
`some` saved noise with the shapes of an old frame (and a minimap in a corner), `fast`
(v0.1.4) saved the photographed scene in orange and green contour bands. At SAVE the game
composes the photo into EDRAM, resolves it (1280x720 `k_8_8_8_8`, 3,768,320 bytes) to
physical 0x1DAC5000 and encodes it on the CPU (JPEG, `photo/<name>/image@...`; the 416x240
`thumb` is a separate, new resolve and was always right). The same range takes a depth
resolve (copy source 4) every frame, so under the old (address, length) key the photo was
a hot key: `some` never copied it back, `fast` copied the previous resolve of the range,
the depth buffer (hence the bands). Found by filling the candidate ranges with marker colours
through `/proc/PID/mem` before taking a photo: the saved photo came out in the colour of
0x1DAC5000. SDK patch 0048 adds the copy's source (depth or color) and destination format
to the key, so the photo resolve is a first-seen key and is copied back after a GPU wait.
Checked on the TU4 build (scratch copy of the Xenia save, photo saved, then loaded from
VIEW PHOTO): correct at render scale 1 and 2. A key is first-seen whenever its range alone
was, so every copy `some` made before (car thumbnails) is still made.

Race at 144 with 0048 (same plugin, fresh save copy each run, BENCH lock held; CONTENDED
only by the load average left by the previous run):

| readback_resolve | Race intro fps | Race fps (p50 / p95 / p99 ms) |
| --- | --- | --- |
| some (0048) | 103.3 | 93.7 (10.6 / 14.1 / 15.1) |
| none | 103.1 | 94.4 (10.6 / 14.0 / 15.0) |
| some (0048) | 103.3 | 93.1 (10.7 / 14.0 / 15.0) |

Saving a photo needs the name dialog (`XamShowKeyboardUI`), an SDK ImGui window: Enter or
OK with the keyboard or mouse. With `--headless=true` (no dialog) the save fails ("File
open failed").

The Paint Shop's `Design Creator` crashed the game once (guest read of address 0x1A4, with
readback on); not looked at here.

Notes from the first try:
- Pictures are 768x288 `k_8_8_8_8` tiled textures (docs/framerate.md);
  `tools/xdc_decode.py FILE.xdc OUT.png` decodes them (Thumbnail_38/44 of the Xenia save
  are correct renders: a 3/4 view and a side view of the car; Thumbnail_9 is a different,
  parts atlas).
- The save is `sub_82546168` (TU4 `sub_825B60C0`), called only from `sub_82546460`
  (callers 82555E18, 826D5E2C, 827B7E98, 827BD678). The render before it is not found yet.
- Targeted readback must switch on before the guest issues the picture's commands (the
  GPU thread resolves asynchronously, before the CPU compresses), e.g. a guest hook
  setting an SDK flag at the render start and clearing it after the save; or an SDK filter
  on the resolve size (thumbnail texture 884,736 bytes).
- The race scenario needs the pristine Xenia save (`~/xenia_canary_windows/content`), not a
  profile the game has since changed (a garage screen came up instead of the race).

### First-seen resolves without a GPU wait: the ones that repeat every frame (#536, 2026-11-02)

Report (RX 6600, RADV, render scale 2, `scene_msaa = off`, built-in benchmark at 60): a pair of
40-48 ms frames about once a second, `cause: resolve readbacks` with 16-25 ms GPU waits in
`forza.log`; `none` removed them (1% low 22.0 to 31.6 fps, p99 39.6 to 26.6 ms) but breaks the
pictures; `fast` still had the waits.

**What misses.** ReIGlue 0410 logs each `some` cache miss (`Readback miss (some): frame, address
+bytes, color/depth, format, width x height, first seen / last resolved N frames before, misses
and resolves of the key, waited N ms`: the first 20, then one every 10 s;
`vulkan_readback_log_misses = true` logs all). After loading, nearly every miss is a car's: each
car that comes into view gets new 64x64 RGBA8 render targets (with 128x128 to 32x1 mipmap
levels) at fresh addresses, resolved again every frame. Traffic in free roam brings one every
0.5-3 s; the race start brings the seven opponents' at once (48 misses in one frame, then one a
frame for seven frames); a car out of view for over 60 frames (its readback buffer evicted) is
a miss again, which is what #49 found on NVIDIA (16 KB resolves, 33-35 ms waits). The other
misses that wait at a race start are keys that come back after a gap: the four 1024x1024 depth
targets of the shadow cascades (2 MB each, resolved every third frame) and the rear-view
mirrors' 320x192 targets (resolved every 15-40 frames).

**Why a fence on the copy does not help.** The miss waits with
`AwaitAllQueueOperationsCompletion`, which already ends the submission holding the copy and
waits for that submission's fence, no later work. A fence signals only when everything queued
before it on the queue is done, so a fence for the copy alone would signal at the same time:
the wait is the GPU's backlog (the frames the command processor is ahead), 9-13 ms here at 60
fps and 20-21 ms uncapped (GPU-bound, about the reporter's 16-25 ms).

**Holding the game's view of the GPU instead (tried, no gain).** Keeping the copy in flight and
holding every write the game can poll (fences, scratch register write-back, MEM_WRITE, occlusion
results) and every interrupt until the data is in guest memory would be correct by construction.
It gains nothing: the game writes its next fence 0.6 ms of command processor time after the
resolve and soon waits for it (the command processor reaches a WAIT_REG_MEM for the game with
that fence held), so the stall only moves (the worst of the three frames around a car's miss,
uncapped: 30 ms waiting at the miss, 33-34 ms held).

**First version: defer every first-seen copy (0410 as first drafted, withdrawn).** The copy was
recorded and put in guest memory when the GPU had done it, a frame or two later, with a short
list of pictures that kept the wait. Fine for the cars, wrong for every one-off resolve the CPU
reads at once: the game then reads the guest memory before the data lands. On the golden save,
render scale 2, the same flow with `vulkan_readback_defer_all = true` (that first version) and
with the old wait:

| One-off resolve the CPU reads | Resolve | First version (`defer_all`) | Wait |
| --- | --- | --- | --- |
| Thumbnail of a saved vinyl group (MY VINYL GROUPS, storefront preview) | 256x128 RGBA8, 128 KB | garbage (noise) | the green group |
| Livery texture sent with UPLOAD TO STOREFRONT (Auction.UploadAuctionTextures) | 1024x1024 | garbage | the livery's UV layout |
| Photo (SAVE) and its thumbnail | 1280x720, 416x240 | correct (kept on the wait list) | correct |
| Big Shot (RB) | 1280x720 front buffers | correct (ReIGlue 0411's `readback_resolve_wait_range`) | correct |

**The change (0410, `vulkan_readback_defer`, on).** A first-seen (or evicted) `some` key's copy
is deferred, put in guest memory when the GPU has done it (checked at every resolve and frame
start, a frame or two later) and not at all if a later resolve wrote over the range first or
the game wrote to it meanwhile, only when it is one of the render targets resolved every frame.
Everything else waits as before. The rules, in this order (`fh1_readback_history.h`,
`RepeatTracker`, tested by `tools/test_readback_history.cpp`):

1. A mode other than `some`, `vulkan_readback_defer = false`, or a range of
   `readback_resolve_wait_range` (the Big Shot) or a thumbnail resolve (768x288 and the 1280x720
   staging surfaces): wait.
2. The photo (1280x720 RGBA8), its 416x240 thumbnail and the 256x256 car picture target: wait.
3. While `sync_pipeline_creation` is on (the game's 8 s window around a car's picture, a livery
   render and photo mode): wait.
4. A key that was itself resolved at least 4 times in a run with no gap over 60 frames before
   (a car's target back after its buffer was dropped, a shadow cascade, a mirror), of any size:
   deferred.
5. A new key of 128 KB or more: wait.
6. A new key of a small shape (format, size, byte order: no address) of which 2 other keys were
   resolved in 4 frames in a row recently, and not mostly one-offs (more than 4 keys that ended
   within 4 frames for each repeating one): deferred. So a session's first car or two still wait,
   and any resolve of a shape that never repeated, which is every picture, waits.

`vulkan_readback_log_misses = true` logs the decision of each miss and why (`deferred ... shape
repeats (N repeating keys, M one-offs)`, `waited ... a picture is being rendered`, ...); the cost
CSV has a `readbacks_deferred` column. Testing only: `vulkan_readback_defer_all` (the first
version), `vulkan_readback_defer_skip` (a resolve that would be deferred is not copied back at
all, to tell the copy's own cost from the wait's).

Checked with this rule (golden save, render scale 2): the vinyl group thumbnail, the storefront
livery texture, a photo and its thumbnail, the Big Shot and the car preview are all correct.

Built-in benchmark, render scale 2, `scene_msaa = off`, 1920x1080 window, RX 9060 XT, RADV, BENCH
lock held, no other game; medians of three interleaved runs per mode. "Before" is the 60 s before
the measuring (the drive to the event through traffic, the grid, the countdown and the start),
from the frame cost CSV, loading frames over 200 ms left out:

| Frame cap | Mode | avg fps | p99 (ms) | frames > 40 ms | before: readback waits | before: waits > 5 ms | before: frames > 25 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| none (`vsync = false`) | `some`, waiting (`vulkan_readback_defer = false`) | 99.5 | 11.9 | 1 | 736 ms | 45 | 47 |
| none | `some` (this change) | 99.9 | 11.7 | 2 | 244 ms | 18 | 28 |
| none | `some`, `defer_skip` | 98.3 | 12.2 | 2 | 238 ms | 17 | 29 |
| none | `none` | 99.2 | 12.4 | 1 | 0 | 0 | 24 |
| 60 | `some`, waiting | 60.0 | 18.1 | 0 | 453 ms | | |
| 60 | `some` (this change) | 59.9 | 18.4 | 0 | 198 ms | | |
| 60 | `none` | 60.0 | 18.2 | 0 | 0 | | |

The measured race has no miss on this PC in any mode (no readback in its minute), so it is the
same in all of them, and the reporter's pairs in it do not show here. Before it, the periodic
waits are gone (the old wait: one of 12.5 ms every half second on the grid, keys back after a
gap, now deferred); what still waits is the race start's first cars of a shape (one or two
repeating keys are too few), the 1024x1024 shadow cascades and the larger one-offs, 228-246 ms
in all. On the
reporter's RX 6600 the first version, which waited for none of those, still had the pairs, so
they may not come from the waits at all: their run of this build with `vulkan_readback_log_misses`
and `defer_skip` is the test.

### The pairs that stayed: readback buffers reused (#536, 2026-11-03)

With 0410 the reporter (RX 6600, 8 GB, RADV, render scale 2, `scene_msaa = off`, 60 fps cap) still
had the pairs of 40-48 ms frames: 13-19 a minute with `some` (22 before 0410), 10 with
`vulkan_readback_defer = false`, 4-5 with `none`, from their `frames.csv` files (two frames over
30 ms within three frames). Their deferred misses do not wait, so the waits are not all of it.

**Not reproduced here.** RX 9060 XT (16 GB), RADV, the same settings in a 1920x1080 window on a
hidden gamescope (1920x1080 at 60 Hz), BENCH lock held, no other game: no pair in any mode, in the
benchmark race at render scale 2 and 3 (scale 3 is GPU-bound at 43.6 fps; `some` 1% low 40.5 / p99
24.2 ms, `none` 40.5 / 24.3) or in free roam (the golden save, `tools/autodrive.py` on a road loop
for 60 s, 11-20 misses a minute). The race has no miss on this PC at either scale
(`vulkan_readback_log_misses`), and the frame cost CSV's columns are the same in `some` and `none`
there: `some` does nothing then that `none` does not. On the reporter's PC the misses do happen in
the race (`fast` and `vulkan_readback_defer = false` show readback waits there), and on a miss
`some` still does more than the wait: a dedicated host buffer created and mapped
(`vkAllocateMemory`, `vkMapMemory`), the copy, the data written into guest memory, and the buffer
freed again when its key is evicted, 60 frames (a second at 60 fps) after the car's last resolve.
`none` does none of it.

**The change (ReIGlue 0426, `vulkan_readback_reuse_buffers`, on).** `some` needs a key's buffer only
until its copy is in guest memory: it goes back to a pool then (and when its key is evicted), and a
miss takes a buffer of its size class from there. So misses and evictions no longer allocate or free
memory on the command processor thread, and no buffer is kept for a key after its copy (before: a
buffer for every key in use, 97 buffers and 62 MB in the long session of #45). Pooled buffers unused
for 600 frames are freed, the pool keeps at most 64 MB. The frame cost CSV gains `readback_buffers_created`,
`readback_buffers_freed` and `readback_free_ms`.

| Scene (60 fps cap, scale 2) | Mode | 1% low (fps) | p99 (ms) | pairs a minute | buffers created / freed a minute |
| --- | --- | ---: | ---: | ---: | ---: |
| Benchmark race | `some`, before (`reuse_buffers = false`) | 48.2, 50.6 | 18.4, 18.7 | 0, 0 | 0 / 0 |
| Benchmark race | `some`, 0426 | 50.7, 46.2 | 18.5, 19.7 | 0, 0 | 0 / 0 |
| Benchmark race | `none` | 50.9, 44.8 | 18.2, 19.0 | 0, 0 | 0 / 0 |
| Free roam | `some`, before | 51.2, 52.8 | 17.4, 17.4 | 0, 0 | 16-20 / 17-20 |
| Free roam | `some`, 0426 | 53.7, 55.3 | 17.2, 17.3 | 0, 0 | 2-6 / 2-4 |
| Free roam | `none` | 57.3, 52.0 | 17.1, 17.5 | 0, 0 | 0 / 0 |

(Two interleaved rounds; benchmark from its `frames.csv`, free roam from the cost CSV's frame times.)
Here a created buffer costs about 0.04 ms and a free about 0.02 ms, so the difference is in the
noise; whether these calls are what costs the reporter's GPU is their A/B to make:
`vulkan_readback_reuse_buffers = false` against the default, and `vulkan_readback_defer_skip = true`
(a deferred key not copied at all), each with `fh1_frame_cost_csv` (the pairs are under the 50 ms of
the `GPU slow frames` lines). Checked with 0426 on the golden save at render scale 2 (warm pipeline
cache): the vinyl group thumbnail (MY VINYL GROUPS and the storefront preview), the paint job preview,
the storefront livery texture, two saved photos with their thumbnails, and the car's thumbnail after
an Auto Upgrade at a Horizon Outpost (`Thumbnail_N.xdc`, 768x288, the 3/4 and side views) are correct.

**The reporter's A/B (#536, RX 6600, Linux).** The pairs came from a non-default kernel parameter,
`amdgpu.vm_update_mode=3` (CPU page-table updates), set to work around another game. With it, each
readback buffer free took about 25 ms; with the default (`-1`) 0.02-0.03 ms. With the parameter, 0426
lifted the 1% low from 26.4 to 31.5-32.6 fps (12 of 20 frames over 33 ms held a free without it);
without the parameter reuse makes no measurable difference. Players with that parameter set should
keep `vulkan_readback_reuse_buffers` on, or remove the parameter.
In the first check run, with an empty pipeline cache, the paint job preview came out black; that run
logged resolves copied back while draws' pipelines were still being built (as in #74). The same flow
with the cache warm is correct with and without 0426.

## Shader cache, cold and warm (#34, 2026-10-15)

Where the pipeline cache lives: the SDK keeps no `VkPipelineCache` blob. It stores the
guest shaders (`<cache_root>/shaders/shareable/4D5309C9.xsh`) and the descriptions of
every pipeline it made (`4D5309C9.fbo.vk.xpso`); the next start translates those shaders
and creates all stored pipelines before the game runs, and the driver's own disk cache
(Mesa's, NVIDIA's) turns that creation into a lookup. `cache_root` defaults to
`<user_data_root>/cache`: `~/.local/share/forza/cache` on Linux (play.sh and the
launcher; under Flatpak, XDG_DATA_HOME is inside `~/.var/app/<id>` and persists too),
`Documents\forza\cache` on Windows before 0b40b58 and the launcher's folder
(`%LOCALAPPDATA%\ForzaReImagined\cache`) since. A pipeline not ready in time is drawn with
a placeholder (async creation, `async_shader_compilation`) and that frame is not shown
(`vulkan_async_skip_incomplete_frames`, default on): a cold cache shows as dropped frames,
not as missing objects.

Freeroam scenario, TU4 build, scratch copy of the Xenia save. Cold: empty `--cache_root`
and empty `MESA_SHADER_CACHE_DIR` (a first start on a new machine); warm: the same two
folders on the next run. CONTENDED (the other agent's two game instances ran, load 4-10),
so only the difference counts:

| Cache | Scene | Source | fps | p50 | p95 | p99 | max | low1 | stutters | hitches | pipelines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cold | loading into free roam | present | 28.6 | 33.4 | 35.1 | 67.2 | 500.3 | 5.5 | 12 | 6 | 485 |
| warm | loading into free roam | present | 29.8 | 33.3 | 34.5 | 50.2 | 166.3 | 12.3 | 5 | 2 | 51 |
| cold | free roam, 90 s | guest | 30.0 | 33.3 | 35.9 | 40.9 | 62.3 | 21.2 | 0 | 0 | 149 |
| cold | free roam, 90 s | present | 29.4 | 33.4 | 35.5 | 66.5 | 102.4 | 14.0 | 17 | 1 | 149 |
| warm | free roam, 90 s | guest | 30.0 | 33.3 | 35.4 | 37.7 | 55.5 | 23.6 | 0 | 0 | 14 |
| warm | free roam, 90 s | present | 29.9 | 33.4 | 34.4 | 35.7 | 67.9 | 22.5 | 2 | 0 | 14 |

The warm start created the 348 stored pipelines in 0.17 s. The game itself never slows
(guest p99 41 ms cold); what the player sees on a cold cache is the presenter dropping the
frames that used a placeholder, 17 stutters in 90 s of driving, each one or two frames, and
a few half-second holds while loading. They go away once the cache is kept, so a cache that
is not kept (a folder Windows blocks or OneDrive holds, a driver cache that is cleared) means
stutter every session. `--vulkan_async_skip_incomplete_frames=false` shows those frames
instead (objects whose pipeline is not ready are missing for a frame or two).

## Pipelines built ahead of their draws (#378, 2026-10-24)

With an empty cache the command processor met new pipelines one draw at a time. With asynchronous
creation (the default) each new one cost it a placeholder pipeline, built on its own thread before the
next draw (the placeholder is never drawn with: the draw is skipped and the frame held), and the real
one was queued; with synchronous creation (*Compile shaders in the background* off, or
`sync_pipeline_creation` around a car's picture) it built each one itself, one after another. SDK
patch 0270 (the idea is ReGPU's `GpuLookahead`, MIT) walks, at such a draw, the commands the game has
already queued behind it, works out their pipelines and starts all the missing ones together on the
creation threads, without placeholders; with synchronous creation the draw then waits
once for the whole set. The log sums it up every 10 s while pipelines are built (`Pipelines: ...`).

Ryzen 7 7800X3D, RX 9060 XT (RADV), Linux, TU4 build, 30 fps, the race scenario of
`tools/perf_scenario.sh` (boot, title, main menu, loading into free roam, the festival race at the spawn,
26 s of it) with a fresh copy of the Xenia save, `BENCH_LOCK` held, no other game running (one run
started at load 2.8). Cold: empty `--cache_root` and `MESA_SHADER_CACHE_DIR`; warm: copies of the
cache and Mesa cache of a cold run with the lookahead. Frames from the title on (the boot's warm-up
not counted); "shown" are the presented frames (`RECOMP_PERF`), "CP thread" the command processor's
own pipeline time (placeholders, synchronous creation and waits, `fh1_frame_cost_csv`), slow frames
the 0180 warnings:

| Creation, cache | Lookahead | Pipelines (built ahead / drawn) | Placeholders | CP thread | Draws skipped | Frames held | Slow frames | Shown frames >= 50 / >= 100 ms | Time in them | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| async, cold | off | 399 | 339 | 831 ms | 2051 | 80 | 17 | 54 / 9 | 4.92 s | 402 ms |
| async, cold | off | 408 | 348 | 851 ms | 2040 | 84 | 22 | 62 / 9 | 5.42 s | 501 ms |
| async, cold | on | 397 (324 / 323) | 70 | 315 ms | 2272 | 78 | 15 | 50 / 9 | 4.50 s | 434 ms |
| async, cold | on | 400 (326 / 325) | 71 | 323 ms | 2428 | 77 | 17 | 53 / 10 | 4.58 s | 402 ms |
| async, cold | on, past waits | 402 (332 / 330) | 67 | 328 ms | 1834 | 76 | 16 | 56 / 9 | 4.60 s | 301 ms |
| async, cold | on, past waits | 413 (337 / 335) | 72 | 393 ms | 2013 | 83 | 16 | 62 / 10 | 5.14 s | 316 ms |
| sync, cold | off | 403 | 0 | 3057 ms | 0 | 0 | 29 | 34 / 11 | 3.65 s | 518 ms |
| sync, cold | on | 402 (343 / 343) | 0 | 1351 ms | 0 | 0 | 19 | 21 / 2 | 1.41 s | 133 ms |
| sync, cold | on, past waits | 401 (345 / 345) | 0 | 1293 ms | 0 | 0 | 16 | 18 / 3 | 1.22 s | 134 ms |
| async, warm | off | 409 (396 stored) | 13 | 102 ms | 121 | 10 | 11 | 21 / 0 | 1.33 s | 78 ms |
| async, warm | on | 409 (396 stored; 11 / 10) | 2 | 100 ms | 69 | 5 | 12 | 16 / 0 | 0.99 s | 83 ms |

- **The guesses are right.** 99.4-100 % of the pipelines built ahead were drawn with (the rest are
  stored only if a draw uses them, so a wrong guess never reaches the cache); about 80 % of a cold run's
  pipelines were built ahead. No GPU errors in any run, and the race screenshots of both settings show the
  same scene.
- **Synchronous creation** gains the most: the command processor's pipeline time falls from 3.1 s to
  1.3-1.4 s (its own pipelines plus about 85 waits for a whole set, 0.77 s), slow frames from 29 to 16-19,
  shown frames of 100 ms or more from 11 to 2-3 and the worst from 518 to 134 ms (loading into free roam).
  The game's own frames (guest) of 100 ms or more: 10 to 3.
- **Asynchronous creation**: the placeholders the command processor builds fall from about 340 to 70 and
  its pipeline time from 0.84 s to 0.32 s, so it no longer stalls on them (the slow frames where it spent
  10 ms or more on shaders and pipelines: 11 to 3). The frames held while pipelines are built barely
  change (77-84): a pipeline started a few milliseconds before its draw is still not ready for that frame.
  With the lookahead, synchronous creation now holds the screen less than asynchronous creation does
  (1.2-1.4 s in frames of 50 ms or more against 4.5-4.6 s), as each held frame of the asynchronous mode
  waits for its pipelines anyway.
- **Warm cache**: no change beyond noise (a few pipelines the stored set lacks were built ahead).
- **Cost**: about 115 walks a cold run, 8,000-12,000 packets each, 1.2-1.4 ms on average (140-175 ms in all,
  most of it translating the shaders the draws would translate anyway). Most walks stop at a wait for a
  value the game has not written yet (`WAIT_REG_MEM`), usually the end of the queued frame;
  `vulkan_pipeline_lookahead_past_waits` (hidden) goes on past it to the write pointer: as accurate in
  these runs, a little more built ahead, and no clear gain, so it stays off.

## Steam Deck approximation (#45)

No Deck was available. The test machine (Ryzen 7 7800X3D, 5 GHz, RX 9060 XT) was restricted to 4 cores
(`taskset -c 0-3`) and each of those cores shared with N busy loops pinned to it, so every game thread gets
1/(N+1) of a core (N=1: 50 %, 2: 33 %, 3: 25 %, 4: 20 %). This approximates a slower CPU only; the Deck's GPU
(about 1/15 of this card, shared memory bandwidth, shared power budget) is not approximated. `frame_rate = 30`,
render scale 1, `present_effect = fsr`, warm cache, no other game.

| CPU share per core | Scenario | fps | p95 | on target |
| --- | --- | --- | --- | --- |
| 100 % (4 cores) | free roam | 30.0 | 33.9 | 100 |
| 50 % | free roam | 30.0 | 36.4 | 99.6 |
| 33 % | free roam | 30.0 | 38.2 | 97.0 |
| 25 % | free roam | 29.9 | 40.8 | 85.6 |
| 25 % | race | 27.9 | 49.1 | 63 |
| 20 % | race | 22.5-24.2 (3 runs) | 56-58 | 27-38 |

The profile (`tools/cpuprof_report.py`) shows the game's main guest thread and the GPU command processor
saturating first (about 0.2 and 0.15 of a 5 GHz core in a race; the rest, audio, the XMA/WMA decoder, the
Vulkan submission thread, is each under 5 %). At 20 % per core the race runs 22-24 fps, the range reported on
the Deck. Settings tried at 20 % in the race (fps, guest): `native_2x_msaa=false` 23.0,
`readback_resolve=none` 23.2, `vulkan_threaded_submission=false` 22.9, `ignore_thread_priorities=false` 22.7
(rebuilt game), against 22.5-24.2 for the defaults: none moves the CPU-bound frame rate. Shader compilation
(warm cache), audio decode and readback are not what limits it; the main thread looked like it (but see the next section: most of its CPU time was a busy wait).

### CPU time per frame (#45, 2026-10-16)

Method: the race scenario (`tools/perf_scenario.sh race`, TU4 build, a fresh copy of the Xenia save each
run, `--present_effect=fsr`, `frame_rate = 30`, warm cache) with `PERF_CPUPROF=1` (the built-in 1 ms CPU-time
sampler, `src/debug/cpu_profile.cpp`; `tools/cpuprof_report.py` run from the build directory so `./forza`
symbolizes). CPU time per frame = a thread's samples in the race scene / the frames of the scene. Call
stacks of the command processor thread came from `eu-stack -1 -p TID` taken a few hundred times during a race
(perf is not installed and samply needs `perf_event_paranoid <= 1`). BENCH lock held, no other game; the
status lines that say CONTENDED only saw the load average of the previous run.

What the profile showed (before; ms of CPU per frame at 30 fps, 5 GHz Zen 4):

- **Render thread, 31 ms of its 33 ms frame:** 93% in the D3D library's wait for the GPU (`sub_824192A0`
  calling `sub_82AAD418` in a loop, TU4 names; disc `sub_823E91F0` / `sub_829F04A8`). The game waits there
  twice a frame for the command processor to pass a point of the ring buffer (the previous frames' swaps,
  so this is where the 30 fps cap is spent: about 14 ms a wait), polling the read pointer write-back every
  few nanoseconds. Its own work is 4-5 ms. This is the "main thread saturates" of the approximation above.
- **Command processor, 7 ms:** a flat profile (packet parsing, register writes, `UpdateBindings` and its
  descriptor updates ~15%, texture and render target lookups) except for one waste: after every draw the
  memexport readback check looked the legacy setting up by name (registry lock, two `std::string`s, a hash
  lookup, the value formatted as a string): 5-15% of the thread, nearly all of its `malloc` and `memcpy`.
- **Timer thread, 1.6-3 ms:** the runtime's timer queue polls between timers (spin, `sched_yield`, 1 ms
  sleeps) and the 1 ms timestamp timer keeps it busy.
- Not wasted: an audio thread (4.5 ms, the game's own float DSP code; `std::fma` is a libm call because the
  generated code is built without `-mfma`), the XMA/WMA decoder, Vulkan submission.

Changes: `src/hooks/gpu_wait.cpp` (each wait polls as before for 20 us, yields up to 200 us, then sleeps
50 us per poll on Linux / yields on Windows; `FORZA_GPU_WAIT=spin` keeps the game's loop,
`FORZA_GPU_WAIT_LOG=1` logs the waits), SDK patches 0049 (memexport check after the empty-range test) and
0050 (the timer queue blocks on POSIX). Before = the previous plugin and runtime with `FORZA_GPU_WAIT=spin`
(a copy of the build directory with the old plugin), after = this commit; three runs each, ms of CPU per frame:

| Race, 30 fps | fps | p95 / p99 ms | ontarget | All threads | Render thread | Command processor | Timer thread |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Before (3 runs) | 30.0 / 30.0 / 30.0 | 34.4-34.6 / 34.8-35.6 | 99.5-99.8 | 52.6-53.4 | 31.3-31.4 | 7.0-7.2 | 1.6-3.2 |
| After (3 runs) | 30.0 / 30.0 / 30.0 | 34.3 / 34.7-34.9 | 99.6-99.8 | 20.7-22.4 | 4.7 | 6.7-7.1 | 0 |
| Before, 2 cores (`taskset -c 0-1`, 2 runs) | 29.6 / 29.7 | 40.5-40.8 / 49.1-50.2 | 90.2 | 53.1-53.2 | 28.3-28.7 | 8.1-8.5 | |
| After, 2 cores (2 runs) | 29.9 / 29.9 | 37.4-37.7 / 41.6-42.1 | 97.0-97.4 | 26.7-26.9 | 5.4 | 7.6-7.7 | 0 |
| Before, 20% per core (as above, 3 runs) | 25.6 / 24.5 / 25.6 | 53.1-54.4 / 61.1-63.1 | 39-49 | 25.0-25.5 | 6.0-6.3 | 4.6-4.7 | |
| After, 20% per core (3 runs) | 25.7 / 25.2 / 25.7 | 49.3-51.1 / 54.4-57.4 | 48.5-52.8 | 18.8-19.9 | 4.1-4.2 | 4.9-5.1 | |

So the game uses 59% less CPU per frame (53 to 22 ms), the render thread 85% less, the command processor
3-8% less (the memexport lookup is gone; the rest of its profile did not change), and the timer thread
nothing. With two cores the spinning render thread had one of them to itself; now the race keeps 30 fps with
97% of frames on target instead of 90% and a p99 of 42 instead of 50 ms. The 20%-per-core approximation does
not move (25.2 to 25.5 fps, within the run-to-run spread): busy loops on every core leave no idle time to
win back, and the frame there is bound by how fast each thread gets the CPU after it wakes, not by the CPU
time the game uses. On a Deck the busy wait took one of its 8 hardware threads (and the SMT sibling's share of
that core, and power from the shared CPU/GPU budget), so that is where the gain should show; a Deck log is
still needed to tell.

The launcher now selects guarded [x86-64-v3 generated code](#cpu-target-x86-64-v3-220-2026-10-21)
on supported CPUs (#233). The narrower Linux-only `FORZA_GUEST_FMA` experiment remains off by
default for manual comparisons with `FORZA_CPU_TARGET=baseline`; see
[the BC-250 FMA validation plan](bc250-performance.md). Neither change has
a confirmed BC-250 fps result.

Other ideas left: cache Vulkan descriptor sets in
`UpdateBindings` (~15% of the command processor); a futex wake instead of the 50 us sleeps (done for the swap
waits, see [Low-end CPUs](#low-end-cpus-45-263-268-2026-10-22); the word they poll is not the read pointer
write-back but one the GPU writes while it executes the ring).

## CPU target x86-64-v3 (#220, 2026-10-21)

The SDK compiles the generated code for SSE4.1 (`-msse4.1`, `rexglue_helpers.cmake`). `-DFORZA_CPU_TARGET=x86-64-v3`
(`forza-recomp/cmake/CpuTarget.cmake`) compiles it, its precompiled header and the two facade DLLs with
`-march=x86-64-v3 -ffp-contract=off` (clang-cl: `/clang:-march=x86-64-v3 /clang:-ffp-contract=off`): AVX2, FMA,
BMI1/2, F16C, LZCNT and MOVBE, which every Intel CPU since Haswell (2013), AMD since Excavator / Zen and the Steam
Deck have. The host code keeps the baseline. The launcher passes it when the build machine's CPU and OS have all
of it (`specs.x86_64_v3`: `/proc/cpuinfo` flags on Linux, CPUID and XGETBV on Windows) and `baseline` otherwise;
`FORZA_CPU_TARGET=baseline|x86-64-v3` in the launcher's environment overrides. Adapted from the guarded FMA
experiment of #176 (`-mfma -ffp-contract=off` on Linux, early CPU check).

What changes in the generated code (TU4, 246 objects): the 7,314 calls of libm's `fma` (`ppc_fma` without
`__FMA__`: a runtime CPU test, then a call) become 11,064 inline `vfmadd`/`vfmsub` instructions; 728,000
big-endian loads and stores use `movbe` instead of a load and `bswap` (`bswap` 798,000 to 105,000); BMI2 shifts
(`shlx`, `shrx`, `rorx`) and AVX2 broadcasts and permutes appear; 6.9% fewer instructions overall.

**Same results.** `-ffp-contract=off` keeps the compiler from fusing any multiply and add of its own: only
`ppc_fma`, already fused by `std::fma`, uses FMA instructions, and both round once. #176's arithmetic check
(`tests/guest_fma` there: 4,334,912 cases, float and double, four rounding modes, all FTZ/DAZ combinations)
passes bit for bit with these flags, and a separate multiply and add still compiles to `vmulsd` + `vaddsd`. The
race itself is not reproducible from run to run, whatever the build: the throttle starts on a host timer, so after
the 26 s the cars of two runs of the same build stood 20-56 m apart (mean over the 8 cars), and runs of the two
builds 16-59 m apart. That shows no difference, and could not show a small one.

Measured: TU4, `tools/perf_scenario.sh race` and `freeroam` (copy of the Xenia save, warm shared
`--cache_root`, `--present_effect=fsr`, `frame_rate = 30`, `PERF_CPUPROF=1`), A (baseline) and B (x86-64-v3)
alternating, three runs each, BENCH lock held, every run CLEAN. CPU ms per frame = a thread's 1 ms CPU
samples in the scene / its guest frames; threads by their guest entry function (`eu-stack` at the end of the scene):
main loop `sub_825CEC88`, render `sub_8261E178`, audio `sub_83075D18` (four threads, the game's mixer and DSP, the
most `ppc_fma` calls). Mean [min-max]:

| Scene | Build | fps | p99 ms | All threads | Game code (`forza`) | libm | Main loop | Render | Audio | GPU commands |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| race | A | 30.0 | 35.0 | 22.2 [21.4-22.9] | 10.8 [10.0-11.4] | 0.39 | 2.21 [1.86-2.39] | 2.32 [1.89-2.59] | 5.03 [4.91-5.09] | 7.25 |
| race | B | 30.0 | 34.9 | 21.0 [20.5-21.9] | 9.6 [9.3-10.4] | 0.06 | 1.96 [1.75-2.38] | 2.04 [1.77-2.48] | 4.19 [4.14-4.23] | 7.46 |
| free roam | A | 30.0 | 35.1 | 35.7 [34.5-36.9] | 27.5 [27.1-28.2] | 1.17 | 2.54 [2.44-2.62] | 20.2 [20.0-20.3] | 4.04 [3.86-4.17] | 3.76 |
| free roam | B | 30.0 | 35.1 | 33.0 [29.7-34.8] | 25.4 [21.9-27.2] | 0.03 | 2.19 [2.07-2.26] | 18.3 [15.3-19.9] | 3.34 [3.27-3.46] | 4.17 |

- The audio threads use 17% less CPU in both scenes (the ranges do not overlap), the main loop 14% less in free
  roam. In the race the main loop and render threads change by 0-6% between runs of the same mode: both builds
  had runs at about 2.4 and at about 1.8 ms, the means differ by 11-12% only because two of the three B runs
  fell in the lower mode.
- The render thread spends about 20 ms of every free-roam frame whatever the scene (also with the car stuck
  against a rail) and whatever the build (B: 19.8 and 19.9, once 15.3); its hottest function is `sub_82468E50`
  (29%, float math, called from `sub_82CE8948`). In the main menu behind the "MISSING CONTENT" notice that
  thread uses a whole core. Faster code does not shorten it, so it looks like work that fills a time budget,
  not a fixed amount. Worth a look for slow CPUs: it was the render thread's idle loop, see
  [Render thread idle loop](#render-thread-idle-loop-234-2026-10-21).
- The GPU command processor (SDK plugin, not rebuilt) and the frame rate (capped at 30) do not change.

**A slower CPU.** With [the approximation above](#steam-deck-approximation-45) (4 cores, each shared with 4 busy
loops: every thread gets 20% of a core), race, two runs each, alternating: A 23.6 and 23.6 fps (p95 53.4 and 57.2
ms, on target 35 and 37%), B 24.0 and 23.6 fps (54.2 and 57.4 ms, 41 and 36%): within the noise, like every
setting #45 tried there (the frame waits for each thread to get the CPU back, not for its CPU time). B used 18.5
and 19.2 ms of CPU per frame against 19.7 and 19.8, the audio threads 3.9 against 4.9-5.1. A Deck is a Zen 2 at
2.4-3.5 GHz within a 15 W budget shared with the GPU, roughly 2 to 2.5 times this machine's time per instruction:
the same 8-11% less work in the game's code (17% in the audio threads, 14% in the free-roam main loop) means more
idle time on its 8 hardware threads and less power drawn from that budget, which helps where the CPU is busy
(free roam at 60 fps) more than in this approximation. The x86-64-v3 code uses no PDEP/PEXT (slow microcode on Zen
2). A Deck log is still needed to tell the fps.

The build with the CMake option (generated code only, host at the baseline, guard included) ran the race normally:
21.4 ms of CPU per frame, game code 10.2, audio 4.1, libm 0.06.

**Guard.** A built game can be copied to an older PC, where the first AVX2 instruction would end it with SIGILL at
a random point. `src/platform/cpu_target.cpp` (compiled with the baseline flags) checks CPUID (leaf 1, leaf 7,
0x80000001) and XCR0 for every x86-64-v2 and v3 feature before any generated code runs: on Linux from
`.preinit_array`, which runs even before the shared libraries' initializers (the SDK runtime's static constructors
can call inline functions the executable exports, and those may come from a generated-code object); on Windows from
a constructor of priority 101 (`.CRT$XCA00101`, before every C++ initializer; a DLL never calls into the
executable). On a CPU without them it prints which features are missing and "Rebuild the game on this PC: in the
launcher, Setup > Build" (a message box too on Windows when not started by the launcher) and exits with code 76,
which the launcher explains instead of offering a crash report. The launcher also refuses Play itself for its own
v3 build on such a CPU, and records the target in `build/state.json` (`cpu_target`) and in the build id, so a
changed CPU (or a data folder moved to another PC) shows "Rebuild needed". Tests: `launcher/test_cpu_target.py`,
`forza-recomp/tests/cpu_target_test.cpp` (feature bits; `-DFORZA_CPU_TARGET_V3=1` also runs the guard before
`main`). With gdb clearing BMI1/2, AVX2 and MOVBE in the CPUID words, the TU4 game's guard printed them from
`_dl_init` and exited with 76; under Wine (GE-Proton 9-27), the guard cross-compiled for Windows with AVX2 hidden
did the same before the C++ initializers and `main`. The launcher's Windows CPUID machine code, run on Linux behind
an argument shim, read the same words as the game.

The TU4 game also builds for Windows with the option (cross clang-cl with xwin, `tools/cmake/windows-clang-cl.cmake`):
the generated objects have the FMA and MOVBE instructions and no `fma` calls, the guard's object none of them.
Not verified: the Windows game running (no Windows PC with a GPU here; measured on Linux only) and a Steam Deck.

## Small code model on Linux (#473, 2026-10-28)

The SDK compiled all Linux x86-64 code with `-mcmodel=large` and linked the game with `--no-relax`
(ReIGlue `CMakeLists.txt` for the runtime, GPU plugin and codegen tool; `cmake/rexglue_helpers.cmake` for
the game's executable, its generated code and the two facade DLLs): every call a `movabs` of the target's
address and an indirect call, for code that could lie anywhere. ReIGlue 0400 makes the small model the
default (`-DREXGLUE_X86_64_CODE_MODEL=large` gives back the old flags, `--no-relax` included). The game's
code is far below the small model's 2 GB. Idea from Pinyon Shift (ShiftGlue `24e59df`, BSD-3), which
measured on a Steam Deck race, together with x86-64-v3: process CPU -5.5%, simulation thread -10.5%, p99
frame time -6.6%.

The game takes its compile flags from the SDK's CMake helpers, and the Linux overlay used to take those
from the nightly (`lib/cmake` linked to it). `tools/build_rexglue_sdk.sh` now installs ReIGlue's
`rexglue_helpers.cmake` into the overlay and `tools/package_sdk_overlay.sh` ships it, so the launcher's
builds (the Flatpak's too) get the small model; a source build gets it with the overlay as its SDK prefix
(docs/PROJECT.md).

**What changes** (TU4, `-DFORZA_CPU_TARGET=x86-64-v3`, the same generated sources; A = ReIGlue `7d17ea51`,
B = with 0400):

| | A (large) | B (small) |
| --- | --- | --- |
| Executable code (`.ltext` / `.text`) | 68.5 MB | 62.8 MB |
| All loaded sections | 81.0 MB | 71.4 MB (3.5% of the small model's 2 GB) |
| `movabs` | 533,097 | 23,894 |
| Direct calls | 408 | 411,824 |
| Indirect calls (through a register / through memory) | 126,907 / 331,464 | 48,871 / 854 |
| GOT entries | 45,820 | 30 |
| Relocations at load (`R_X86_64_RELATIVE`) | 128,165 | 83,044 |
| `librexruntime.so` / `librexgpu-xenos.so` | 16.4 / 8.4 MB | 13.4 / 7.4 MB |

The calls that stay indirect in B are the real ones (function pointers, the guest's indirect branches).
B links without `--no-relax`: GNU ld relaxes the objects' 1,311 GOT loads (`R_X86_64_REX_GOTPCRELX`), the
GOT keeps 30 entries. No relocation overflow with the host's Homebrew clang 23.1.2 and GNU ld 2.46.1
(Fedora), nor with the Flatpak's toolchain (org.freedesktop.Sdk 26.08 with its LLVM 22 extension: clang
22.1.8, GNU ld 2.47, CMake 4.4.3), which built and linked the whole TU4 game (`.text` 63.1 MB, 30 GOT
entries).

**Measured** (2026-10-28): TU4 with `-DFORZA_CPU_TARGET=x86-64-v3`, both builds with the #512 fix (branch
`fix/512-ffcb-kick`, `15df09b`: without it the small-model build hung entering free roam in 4 of 8 runs at
`frame_rate = 60`, the large one in none of 7), `tools/perf_scenario.sh`'s `race` (26 s) and `freeroam` (90 s)
inputs, `frame_rate = 60` (a 120 Hz guest vblank), `--present_effect=fsr --resolution_scale=1`, 1280x720
window, a copy of the golden TU4 save with 12 DLC packages, offline, audio muted, warm scratch shader cache,
every game thread on CPUs 0-3 from the start (the samplers on 4-7). A and B alternating (ABBA), four runs each,
BENCH lock held, no other game, at most 19% of a core of other load on CPUs 0-3 in every run. CPU ms per guest
frame from `/proc` (`tools/thread_stats.py`), threads by their guest entry function (`eu-stack` at the end of
the scene). Mean [min-max]:

| Scene | Build | fps | p99 ms | All threads | Main loop | Render | Audio mixers | GPU commands | Vulkan submission |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| race | A (large) | 60.0 | 18.9 [18.7-19.1] | 19.77 [19.50-20.26] | 2.29 [2.27-2.31] | 3.25 [3.13-3.40] | 2.42 | 7.03 [6.89-7.34] | 1.51 |
| race | B (small) | 59.9 | 18.8 [18.7-18.9] | 19.08 [18.70-19.24] | 2.20 [2.17-2.22] | 3.21 [3.11-3.34] | 2.41 | 6.59 [6.38-6.68] | 1.48 |
| free roam | A (large) | 60.0 | 19.2 [18.8-19.5] | 15.30 [14.86-15.80] | 2.23 [2.17-2.30] | 2.70 [2.63-2.79] | 1.95 | 4.31 [4.14-4.50] | 1.22 |
| free roam | B (small) | 60.0 | 19.2 [18.9-19.5] | 15.04 [14.68-15.59] | 2.17 [2.07-2.24] | 2.72 [2.63-2.83] | 1.91 | 4.23 [4.13-4.41] | 1.20 |

- Race: 3.5% less CPU per frame in all; the ranges do not overlap. The GPU command processor (the SDK plugin,
  now small-model too) uses 6.3% less, the main loop 3.9% less (no overlap either); render, audio and the
  Vulkan submission thread change by 0-2%.
- Free roam: 1.7% less in all, the main loop 2.7% and the command processor 1.9% less: within the run-to-run
  spread, not a measurable gain.
- Frame rate and p99 do not change: at 60 fps four of these 5 GHz Zen 4 cores are not the limit. Pinyon Shift's
  figures are from a Steam Deck (Zen 2, slower cores, simulation thread -10.5%); a Deck run is still needed.

## Render thread idle loop (#234, 2026-10-21)

In free roam the render thread used about 20 ms of CPU in every 33 ms frame (a race: about 2), and behind the
main menu's "MISSING CONTENT" notice a whole core. Its hottest function, `sub_82468E50` (disc `sub_82438EA8`), is
the CRT's `pow`. Both were the thread's loop waiting for the next main frame without ever blocking.

**Call chains** (perf `--call-graph dwarf` on that thread, TU4 names):

- free roam: `sub_8261E178` (the thread) > `sub_826164D0` (one pass of its loop) > `sub_825FCD50` (the screen
  gamma transition) > `sub_825FCC28` > (vtable +200) `sub_82CE8948` (builds a 128-entry PWL gamma ramp, three
  channels, and sets it with `sub_82AAEE68`; 256 entries and `sub_82AAED98` in its other mode) > `pow` 128 times,
  and `sub_82CE8810` > `sub_82CE78E0` > `pow` 128 more. `pow` and its helpers (`sub_824989D0` ldexp,
  `sub_8241F8C0` frexp, `sub_82B43BC0`) were 70% of the thread's samples, the ramp's D3D calls 10%.
- main menu: the same pass, no `pow`: its loop of 32 virtual calls on render object +0x970 (`sub_82440A20`,
  `sub_82472748`, 37%), the render-thread jobs (`sub_82F0BAB8`, 11%), the end-of-pass virtual call
  (`sub_82D13A50` > `sub_824797A8`, 16%), `sub_824AA568`'s `NtSetEvent` (8%). No kernel wait anywhere: the
  thread never blocked.

**What the loop does.** `sub_8261E178` calls `sub_826164D0` until the game ends. Each pass begins with per-pass
work (the gamma transition among it) and takes the next main frame, if there is one, to draw it (or skip it).
A pass that has none returns, in one of two ways:

- idle: the main frames' count (render object +0xA50) is not past the frame it took last (+0x94C), or, in mode 2
  (+0xCB0: free roam and races), the queue has none ready. It gives the render-thread jobs their time budget
  (`sub_82F0CD48`) and calls `SleepEx(0, TRUE)` (`sub_82B2A848`). On the console that yields to the other
  threads of the core; here it returns at once (the SDK's `AlertableSleep(0)` neither sleeps nor yields on POSIX).
- empty: the count says there is a frame but the queue has none to take yet (`sub_824A4070`: its list +0x20 and
  current +0x38 are empty). In the menus (mode 0) the count runs ahead of the queue, so every pass between two
  frames is one of these, and nothing sleeps at all.

So between two main frames the thread runs pass after pass: a busy wait for the main loop, by design of the game,
that costs a core here. The gamma transition (`sub_825FCD50`, disc `sub_82586CD8`) makes each pass dear: while
its start time (+0x16E0) is above 0 it lerps the three gamma parameters over its duration (+0x16E8, 2 s) and
rebuilds and sets the ramp. It is meant to stop once the progress passes 1, but the progress is clamped to 1
first, so a transition never ends: after the game's first gamma change (behind the menus, before free roam) every
pass rebuilds the ramp. In free roam that made about 19,000 idle passes a second (640 a frame) of about 30 us
each, nearly all of it the ramp. Behind the DLC notice no transition has started yet, so the passes are cheaper
and the thread is all loop (its `NtSetEvent`s kept other threads busy too). Faster code does not change it
(#220): faster passes only make more of them.

**Free roam against the race.** In a race the render thread spends its frame in D3D's wait for the GPU (the
previous frames' swaps, `gpu_wait.cpp` above), so the next main frame is waiting when it returns: in the race scene
of the four A runs below it never went idle (two of the B runs idled about 250 times a second). In free roam it
finishes its frame early and spends about half of each frame waiting for the next main frame.

**Change** (`src/hooks/render_idle.cpp`): such a pass, at the idle pass's `SleepEx` call and at the empty pass's
end (its call of `sub_82E9C3B8` with no frame taken; each call site told by its return address, the other six
`SleepEx` callers left alone), waits for the pass's own conditions first. As in `gpu_wait.cpp`, the first 20 us
since the thread last took a frame keep the game's loop and the next 180 us yield; then it sleeps in 50 us steps
(timer slack 5 us) until the pass would find work: a frame to take (the pass's own test), a message (+0x8) or a
render-thread job queued, or 1 ms has passed, and the pass runs again. A pass that finds jobs queued goes on at
once, as before. A new main frame is taken within about 50 us, so frame pacing does not change; the per-pass
work, the gamma ramp included, still runs, only in fewer passes (free roam: 730 a second instead of 19,000). Windows
keeps the game's loop (its `SleepEx(0)` yields; its sleeps are whole milliseconds). `FORZA_RENDER_IDLE=spin`
keeps the game's loop; `FORZA_RENDER_IDLE_LOG=1` logs the idle and empty passes, waits and time waited every 10 s.

Measured: TU4, `tools/perf_scenario.sh` (copy of the Xenia save, warm shared `--cache_root`,
`--present_effect=fsr`, `frame_rate = 30`, `PERF_CPUPROF=1`), one build, A = `FORZA_RENDER_IDLE=spin` (the game's
loop) and B = the wait, alternating, BENCH lock held. CPU ms per frame = a thread's 1 ms CPU samples in the scene /
its guest frames (as in [CPU target x86-64-v3](#cpu-target-x86-64-v3-220-2026-10-21)). Mean [min-max]:

| Scene | Mode | fps | p99 ms | All threads | Main loop | Render | Audio | GPU commands |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| free roam (3 runs each) | A | 30.0 | 35.1 [35.0-35.3] | 36.6 [36.2-36.9] | 2.50 [2.46-2.56] | 20.3 [20.2-20.4] | 4.33 [4.16-4.63] | 4.15 [4.14-4.15] |
| free roam | B | 30.0 | 35.0 [34.8-35.2] | 18.3 [16.6-19.5] | 2.27 [2.20-2.35] | 2.77 [2.71-2.89] | 4.06 [3.82-4.18] | 4.22 [3.75-4.55] |
| race (4 and 3 runs) | A | 30.0 | 34.9 [34.8-35.1] | 23.2 [22.8-23.9] | 2.27 [1.85-2.46] | 2.44 [2.30-2.60] | 5.36 [5.08-5.73] | 7.31 [7.10-7.50] |
| race | B | 30.0 | 34.8 [34.7-34.9] | 21.9 [20.6-22.6] | 2.24 [1.94-2.40] | 2.42 [2.36-2.51] | 5.00 [4.78-5.12] | 7.01 [6.49-7.28] |
| main menu, DLC notice (2 runs each) | A | 29.8 | 34.1 [34.0-34.2] | 55.6 [55.6-55.6] | 0.26 | 28.5 [28.4-28.5] | 0.33 | 0.81 |
| main menu, DLC notice | B | 30.0 | 33.8 [33.8-33.9] | 6.5 [6.4-6.5] | 0.30 | 0.41 [0.41-0.42] | 0.26 | 0.71 |

- Free roam: the render thread uses 86% less CPU (20.3 to 2.8 ms a frame), the whole game half (36.6 to 18.3).
  Idle passes went from about 19,100 to 730 a second; the thread waits 54% of the time, and about 30 waits a
  second (one a frame) end with a main frame, the rest after 1 ms.
- Main menu: the render thread from a whole core to 0.4 ms a frame and the game from 55.6 to 6.5 ms (1.7 cores to
  0.2): the spinning pass also kept other threads busy. 2,700 empty passes a second remain.
- Races do not change (the render thread hardly idles in them). A fourth B race, left out above, ran while
  another job loaded the machine (load average 7 at its end): 24.3 ms, p99 37.5.
- Frame pacing: the same frame rate, p99 and share of frames on target in every scene; the race intro (a
  cutscene) 30.0 fps, p99 35.0 ms with B; screenshots of the menu, free roam, the intro and the race looked as
  before. Free roam at 60 fps (one run each): 60.0 and 60.0 fps, p99 18.5 and 18.1 ms, render thread 3.7 and
  2.2 ms. At 240 (three runs each, bound by the GPU emulation, render sync waiting every frame): A 154-160 fps,
  B 149-172 (means 157.6 and 158.1; the drive differs between runs, 127-198 fps in 10 s windows), render thread
  3.0-3.2 against 2.1-2.2 ms.

Left: the gamma ramp (still about 30 us a pass, 730 idle passes a second in free roam: about 0.7 ms a frame)
could be cached from its inputs, or the transition ended at progress 1 (the game's intent, but every pass
re-applying the ramp is what it does now); Windows could wait on a high-resolution waitable timer instead of
yielding; with #240 the wait should also end when the main loop waits for render sync (that wait now ends up to
1 ms later). The first two are done in #259 (below).

## Gamma transition end and the Windows idle wait (#259, 2026-10-22)

**The gamma transition.** The game starts one with `sub_825FCCA0` (disc `sub_82586C28`): it sets one gamma
channel's target and the start time (render object +0x16E0). In the free roam scenario there is one: when the DLC
notice is dismissed, channel 1 goes from 0.5 to 0.7 in 2 s. Each render-thread pass then lerps the three channels
by min(progress, 1), builds and sets the ramp (`sub_825FCC28` > vtable +200 `sub_82CE8948`), and clears the start
time only when that clamped progress is above 1, so it never does. From then on every pass built the same ramp
again: 730 times a second in free roam with #234's wait, and on Windows, where the thread still spun, in every
pass.

**Change** (`src/hooks/gamma_transition.cpp`): the transition's call of `sub_825FCC28` (told by its return
address; the device setup and immediate gamma changes call it too) clears the start time after the ramp is
built at progress 1, as the game does above 1. The next gamma change starts a new transition as before.
`FORZA_GAMMA_TRANSITION=game` keeps the endless transition.

**Same ramp.** The D3D setter (`sub_82AAEE68`) keeps the applied ramp (1536 bytes at device +15408) and sends
it to the GPU only when it changes. A temporary diagnostic build (TU4: title, DLC notice, menu, free roam, race
sign-up, intro and start) hashed it after every set:

- Game's transition: 1,906 different ramps during the 2 s, then d7d40275a1988239, set again in every pass
  (760 a second) until the end of the run.
- Change: the same fade (1,909 different ramps), the transition ends 2.0 s after it starts at
  d7d40275a1988239, and nothing sets the ramp after that.
- Every 0.5 s with no transition running, the diagnostic built the ramp the endless transition would have set
  (the three channels at progress 1 from their current values). All 200 builds, through menu, free roam and the
  race, matched the applied ramp.
- Nothing changed the other inputs (+0x16E8 to +0x1713, the device's format, the flag at 0x8353A1E0) without
  starting a transition. In the code, the only writers of those fields are the constructor (`sub_8261E320`),
  `sub_825FCCA0` (which always restarts the transition) and the transition itself.
- Screenshots of every step look the same; still scenes have the same mean colour to 0.1 (sign-up
  77.8/66.0/65.6 in both).

**Windows idle wait** (`src/hooks/render_idle.cpp`). Windows now waits with the same policy as Linux: spin
20 us, yield up to 200 us, then sleep in 50 us steps until a frame, a message or a job, or 1 ms. Each step
waits on a high-resolution waitable timer (`CreateWaitableTimerExW` with
`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`; `Sleep` and `SleepEx` count whole milliseconds). Windows before 10
version 1803 has no such timer: the log says so once, and each step yields (`SwitchToThread`) instead. The timer
wait is not alertable, so APCs queued to the thread run at the game's next alertable wait (its `SleepEx`),
within about 1 ms. `FORZA_RENDER_IDLE=spin` still keeps the game's loop. It compiles with clang-cl (the TU4 and disc
Windows cross builds); not run on Windows yet.

**Measured** (Linux, TU4, `tools/perf_scenario.sh` as in #234, `frame_rate` 30, one build, A =
`FORZA_GAMMA_TRANSITION=game`, B = the change, alternating, BENCH lock held, all runs CLEAN; CPU ms per guest
frame in the scene, mean [min-max]):

| Scene | Mode | fps | p99 ms | All threads | Render thread | Ramp functions on it |
| --- | --- | --- | --- | --- | --- | --- |
| free roam (3 runs each) | A | 30.0 | 35.1 [34.8-35.3] | 19.0 [18.6-19.6] | 2.74 [2.64-2.82] | 0.39 [0.33-0.43] |
| free roam | B | 30.0 | 34.9 [34.4-35.4] | 18.2 [16.5-19.5] | 2.21 [2.17-2.25] | 0 |
| main menu, DLC notice (3 runs each) | A | 30.0 | 33.8 | 6.45 [6.37-6.50] | 0.39 [0.38-0.41] | 0 |
| main menu, DLC notice | B | 30.0 | 33.8 | 6.46 [6.42-6.52] | 0.40 [0.39-0.41] | 0 |

- Free roam: the render thread uses 0.5 ms less per frame (19%). The ramp's own functions were 0.39 ms of it
  (`pow` and its helpers, the ramp builders, the D3D setter); the rest went with the pass's other work around
  them. All threads vary more between drives than that.
- Idle passes go from about 730 to 4,700 a second, because a pass without the ramp takes about a microsecond, so
  more fit in the first 200 us after each frame (the spin and yield phase, the same CPU time). The waits and
  the time waited (54-56%) do not change.
- The DLC notice has no gamma transition yet, so it does not change there.
- Not measured: Windows. There the change matters more: with the game's loop the render thread built the ramp
  in every pass between frames.

## Low-end CPUs (#45, #263, #268, 2026-10-22)

Three players see low frame rates at the lowest settings: a Steam Deck (#45: 4 Zen 2 cores, 8 threads,
0.1.27: 17 fps in free roam, 8 in an 8-car race, a choppy frame-time graph), an i5-1135G7 laptop with Iris Xe
(#263: 4 cores, 8 threads, Linux, 720p, 30 fps target: 15-25 fps, better at the bumper camera) and a Ryzen 7
7730U laptop with Vega graphics (#268: 8 cores, Windows, an AMD driver from 2023: spiky frame times).

**A four-core budget.** This PC (Ryzen 7 7800X3D) runs without SMT (`/sys/devices/system/cpu/smt/control` says
`notsupported`), so the game ran on four cores without SMT siblings (`PERF_CPUSET=0-3`: every game thread `taskset`
to CPUs 0-3, the samplers on 4-7). The clock cannot be lowered without root (`scaling_max_freq` is root's), so each
core still runs at up to 5 GHz: this budget has fewer cores than a Deck, not slower ones. Settings: the launcher's
low tier (frame rate 30, render scale 1, AMD FSR 1), TU4, `-DFORZA_CPU_TARGET=x86-64-v3` (as the launcher builds
for these three CPUs), a copy of the Xenia save, a warm scratch shader cache, offline, audio muted (the silent
output takes the game's audio at the device's pace, so the audio threads do the same work). Scenes: the inputs of
`tools/perf_scenario.sh freeroam` (90 s of driving) and `race` (the 8-car festival race, 26 s from the start; here
without its screenshots and car log). `PERF_CPUSET=0-3 PERF_THREADS=1 tools/perf_scenario.sh race DIR`, then
`tools/thread_stats.py report DIR --cpus 0-3`, repeats such a run. Per-thread figures come from `/proc` once a
second (`PERF_THREADS=1`, `tools/thread_stats.py`: CPU time, time runnable but waiting for a CPU, voluntary
switches, which are mostly wakeups), the GPU time from the game's DRM client (`drm-engine-gfx` in fdinfo), hot
functions from `PERF_CPUPROF=1`, the threads' guest entry functions from `eu-stack`. BENCH lock held, no other game
running.

**Where the time goes** (before; % of one 5 GHz core over the scene, 3 runs each on four cores; ms per frame at
30 fps in brackets):

| Thread (guest entry) | Free roam | Race | Wakeups/s | Hot spots |
| --- | --- | --- | --- | --- |
| GPU command processor | 14.9 (5.0) | 25.2 (8.4) | 850 | `UpdateBindings`, `ExecutePacket`, `WriteRegistersFromMem`, texture and render target lookups (a flat profile) |
| Render thread (`sub_8261E178`) | 10.5 (3.5) | 12.4 (4.1) | 15,500 | the game's own work about 7%; the rest its waits: 50 us sleeps in `gpu_wait.cpp` and `render_idle.cpp`, their yield phase (`sched_yield` 12% of its samples) |
| Audio mixer (`sub_83075D18`, one of four) | 10.4 (3.5) | 13.3 (4.4) | 850 | the game's DSP code (`sub_830CE6C0`, `sub_83087D70`, ...), and `pthread_cond_broadcast` (4%): every event it sets woke the threads below |
| Main loop (`sub_825CEC88`) | 6.9 (2.3) | 8.2 (2.7) | 100-200 | game code |
| Vulkan submission | 3.5 | 4.4 | 120 | the driver |
| Audio Worker (SDK), `sub_83187E38`, `sub_83187E98` (game audio) | 1.4-1.6 each | 1.5-2.0 each | 4,000-5,700 each | re-scanning their handles: patch 0033's wakeups |
| XMA decoder | 0.6 | 1.1 | 1,600-2,900 | |
| Five threads in alertable waits, GPU VSync | 0.3-1.5 each | | 950-1,100 each | 1 ms polls |
| All threads | 65 (21.7) | 82 (27.3) | 37,000-38,000 | |

- The four cores are far from full (0.65 and 0.82 of a core in all), and the frame rate holds 30 (p99 35 ms) in
  both scenes; with all eight cores the same (CPU 66% and 83%). What changes is the time threads spend runnable
  but waiting for a CPU: 32% and 37% of a core on four cores against 3-5% on eight. The wakeups come in bursts
  (one signal woke four threads at once), so they queue behind each other and behind the game's threads.
- The GPU: 7.9 ms of this RX 9060 XT per free-roam frame, 8.3 ms per race frame (at the clock it picks for a
  30 fps load). The Deck's GPU has about a quarter of this card's compute units at about half the clock and a
  quarter of its memory bandwidth: if the frame cost scales with that (8-15 times), a Deck frame needs 60-120 ms of
  GPU, 8-17 fps. That matches what the Deck shows (17 and 8 fps), so the Deck may well be bound by its GPU, not
  its CPU; only a Deck can tell (below).
- The 2 ms of CPU per frame the race adds sits in the command processor (more draws) and the audio mixer (more cars).

**#263: Vertical sync off.** That player turned *Vertical sync* off. The setting is the SDK's `vsync`: off, the
guest vblank runs at 1,000 Hz instead of 60, and the game's main loop, which the vblank paces, runs as often as
it can. Their log shows it: during long frames the main loop ran 35-400 frames a second (`FORZA_STALL: ... main
loop 710 frames meanwhile` over 1.8 s), against 2-30 on the Deck. Every one of those frames is game simulation
on the CPU the renderer needs. Here, with both fixes on four cores, `--vsync=false` in free roam ran 164 guest
frames a second and used 2.9 cores (the main loop 91% of one, the command processor 84%) against 0.64 with vsync
on: on a four-core laptop whose CPU and GPU share one power budget that is the whole CPU, and power taken from
the Iris Xe.

**Fix 1: waits wake only their own threads (SDK patch 0114, runtime, Linux and macOS).** Patch 0033 made a
thread blocked on several handles (`WaitForMultipleObjects`; the SDK's Audio Worker waits on its nine audio
clients' semaphores, two of the game's audio threads on several events) sleep on one process-wide condition
variable: every signal of any event or semaphore in the process woke all of them to re-scan their handles, about
4,000 times a second in free roam and 5,500 in a race, each. Alertable waits (`SleepEx(n, TRUE)`,
`WaitForSingleObjectEx(..., TRUE)`) re-checked every 1 ms for a queued APC. Now such a thread registers with each
handle it waits on and with its own APC queue, and only a change of one of those wakes it (a 20 ms re-check stays
as a safety net). `tools/test_wait_wakeups.cpp` checks both (Linux SDK CI job): a two-handle wait during 300 ms
of unrelated signals switched 2,969 times before and 15 after; a 200 ms alertable sleep 191 times and 11. It also
fixes a zero-timeout alertable wait, which timed out without looking at the handle.

**Fix 2: the render thread's waits for the GPU block until the GPU writes (`src/hooks/gpu_wait.cpp`, SDK patch
0115).** The D3D loop that waits for the swaps polls a word the GPU writes as it executes the ring (device +0x2B10
points to it; in TU4 it sits 0x3C before the read pointer write-back, which the earlier text called it). The hook
slept 50 us per poll after a 200 us spin and yield: about 15,000 wakeups a second. It now blocks on the word
(Linux futex, Windows `WaitOnAddress`, at most 1 ms per poll), and patch 0115 has the command processor wake the
threads blocked on a word after each of its writes to guest memory (scratch register and read pointer
write-backs, `MEM_WRITE`, `COND_WRITE`, `EVENT_WRITE_SHD`, `REG_TO_MEM`). The value the loop compared is taken from
its registers, so a write between its check and the block is not missed. With `FORZA_GPU_WAIT_LOG=1` in free roam:
300 waits per 10 s, about 3,700 blocks, of which 300 end with the GPU's write (one a wait) and the rest at the 1 ms
limit while the command processor waits for a vblank. The other six D3D wait loops (their conditions can depend
on other words) keep the 50 us sleeps; on Windows those now sleep on a high-resolution waitable timer instead of
yielding on every poll, which kept the waiting thread on a core for the whole wait (#268: it is the race's render
thread, most of each frame). The setting `gpu_wait_blocking` (Advanced > GPU on Windows, "Sleep while waiting for
the GPU") turns it on; it was off on Windows until it ran there and is on everywhere since #640 (measured on a
4-core Windows laptop, [below](#a-4-core-laptop-with-a-gtx-1650-ti-and-8-gb-640-2026-11-04);
`launcher/test_gpu_wait_blocking.py` checks the default). Off, the waits are the ones before it.
`FORZA_GPU_WAIT=block|sleep|spin` overrides the setting (sleep: the old waits; spin: the game's loop).

**Measured** (four cores, 3 runs each, B and C alternating; A = neither fix: the main-branch game, runtime and
plugin, plus one run of this branch's game with the private build of the base runtime and plugin and
`FORZA_GPU_WAIT=sleep`, which matched; B = fix 1; C = fixes 1 and 2; mean [min-max]; the run-queue wait is the
median of the scene's per-second values):

| Scene | Build | fps | p99 ms | 1% low fps | CPU, % of a core | Run-queue wait (median a second) | Voluntary switches/s | Render thread | GPU ms/frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Free roam | A (3 runs + 1 on the private build) | 30.0 | 35.0 [34.7-35.3] | 26.5 [26.2-27.1] | 66 [62-68] | 29% [28-30] | 38,200 [37,200-39,100] | 10.5%, 15,500/s | 7.9 |
| Free roam | B | 30.0 | 34.9 [34.9-35.0] | 26.4 [26.1-26.7] | 60 [59-62] | 3% [2-4] | 23,800 [23,700-23,900] | 10.4%, 15,700/s | 7.9 |
| Free roam | C | 30.0 | 35.4 [35.0-35.7] | 26.0 [25.6-26.6] | 64 [63-64] | 5% [3-7] | 18,100 [18,000-18,100] | 9.9%, 9,800/s | 7.8 |
| Race | A (3 + 1) | 30.0 | 35.5 [35.1-35.8] | 25.4 [24.9-25.9] | 83 [81-84] | 34% [27-47] | 37,000 [36,500-37,300] | 12.2%, 15,400/s | 8.4 |
| Race | B | 30.0 | 35.7 [35.5-36.0] | 25.2 [24.9-25.5] | 76 [75-77] | 16% [10-24] | 24,700 | 12.0%, 15,600/s | 8.4 |
| Race | C | 30.0 | 35.6 [35.4-35.8] | 24.8 [24.3-25.2] | 74 [74-75] | 15% [10-24] | 10,800 [10,500-11,200] | 9.8%, 1,400-2,000/s | 8.3 |

- The frame rate holds 30 and the frame times do not change: four cores at 5 GHz never run out of CPU in these
  scenes, so the fixes cannot show as frames here. What they change is the load: per frame the race needs 3.0 ms
  less CPU (27.7 to 24.7 ms), free roam 0.7-2 ms less; the threads wait for a CPU 83-90% less in free roam and half
  as long in the race, and wake 2.1 to 3.4 times less often.
- Fix 1 alone: the SDK's Audio Worker from 4,500-5,700 wakeups a second to 290 (one per audio frame it renders),
  the two game audio threads and the five alertable waits from thousands to tens; all threads 6-7% of a core less.
- Fix 2: in the race the render thread wakes 1,400-2,000 times a second instead of 15,500 and uses 2.2% of a core
  less; in free roam it is mostly in `render_idle.cpp`'s waits (below), so only a third of its wakeups go. The
  command processor's futex wakes cost little: about 1,900 a second in free roam (a temporary counter).
- Free roam C used 4% more CPU than B, within the spread of the A runs (62-68%); the command processor alone varies
  13.4-16.3% between runs of the same build.
- One B run of free roam was taken again: another job used CPUs 0-3 for half of it (run-queue wait 77%, p99 39 ms).
  `tools/thread_stats.py report DIR --cpus 0-3` shows such load: how busy the four CPUs were besides the game.

**A slower CPU** (one busy loop at the same priority on each of the four cores, so a game thread gets at most
half a core and waits for it after each wakeup; one run each):

| Scene | Build | fps | p99 ms | 1% low fps | max ms | Voluntary switches/s |
| --- | --- | --- | --- | --- | --- | --- |
| Free roam | A | 30.0 | 42.8 | 20.6 | 63 | 23,800 |
| Free roam | C | 30.0 | 41.2 | 22.6 | 62 | 10,300 |
| Race | A | 29.9 | 43.4 | 17.8 | 91 | 21,900 |
| Race | C | 29.9 | 41.8 | 19.1 | 92 | 12,100 |

The slowest frames get 1.5 ms shorter and the 1% low 1.3-2 fps higher in both scenes; single runs, so a hint, not
a measurement. The frame rate holds 30 here too.

**Stability** (both fixes, Linux): a 20-minute session (free roam; the 8-car festival race with photo mode, a
restart and quitting the race; photo mode and the profile's stats in free roam; the map and fast travel to the
festival; the Paint Shop's design menus and car select; the Autoshow's car select; quit to the title screen;
Single Player again; driving) ran without a hang, a `FORZA_STALL` line or an error; its seven frames over 100 ms
were all loading screens (start, Single Player, the race restart, quit and reload). Signed in to a local Forza Live
server: Multiplayer, Find Game, Horizon World (hosted world, 8 minutes driving, the pause menu, End Free Roam
and back into the world): one `FORZA_STALL` (216 ms, entering the world), no hang. Saving a photo did not finish
in either build (the save slot screen stays; same with the base runtime), so it is not from these changes.

**What is left**

- Free roam: the render thread's idle waits for the next main frame (`render_idle.cpp`) still sleep in 50 us
  steps (about 9,800 wakeups a second). Blocking there needs a wake from the main loop's frame hand-over, whose
  writer is not found yet. The XMA decoder (1,900 a second, one per kick), one host thread at 1,100 a second and
  the GPU VSync thread are the other regular wakeups left.
- The GPU VSync thread (SDK) sleeps 1 ms at a time to raise 60 vblanks a second: 950 wakeups a second. Sleeping
  until the next vblank is due would also make the vblanks exact to the timer slack instead of up to 1 ms late.
- The command processor (5-8.4 ms per frame here, 2-2.5 times that on a Deck) is the busiest thread and the one
  every frame waits for; #179 and #192 (opt-in) cut `UpdateBindings`.
- The game's audio mixer (3.5-4.4 ms per frame) is game code (x86-64-v3 already took 17% off it, #220).
- Not emulated here: a slower clock, SMT siblings sharing a core, and a power budget shared with the GPU (the
  Deck, both laptops). Fewer wakeups matter more there: an idle core can sleep instead of waking every 50 us, and
  the power it does not use goes to the GPU.
- Windows (#268): fix 2's Windows paths (the hook and patch 0115) compile with clang-cl, but were not run. Patch
  0114 is Linux and macOS only: Windows waits use the system's. The spikes in that log are shader
  compilations (#226) and an old driver; frame rate 60 at 1080p is a lot for a Vega 8.
- Only a Deck can tell whether it is bound by the GPU: Steam's performance overlay at level 4 (GPU and CPU load
  and clocks) in a race, and a log with the frame-time graph.

## Hitches after loads: Title Update 4's car view (#167, 2026-10-23)

**The report.** A player's log (RX 6700 XT, Windows, 0.1.31, Title Update 4, 21 DLC packages; the prebuilt
shader cache loaded all 1,165 pipelines and about 5 were new) had 17 frames of 200-323 ms. 13 of them came in
twos and threes in the first two seconds after a load (fast travel, back to free roam after an event, joining a
session), e.g. 313, 297 and 280 ms, and one (278 ms) while driving. The FORZA_STALL chains (one dump per
group) put the main loop thread in the game's SQLite each time (TU4 addresses): `sqlite3_step` (`sub_83146F20`;
chain entries 83146F50 and 83146E30, its `sqlite3Step` calling the VDBE), the VDBE's loop and helpers (83141024,
831415FC, 83141854, 831438A4, 83122B90 numeric affinity), the b-tree (8310E954 a cell insert, 83131CF0), under
the database's query method (`sub_82CD8FD8`, base `sub_82C19130`: lock, run every statement of the string, copy
all rows into a recordset in `sub_82CD8778`). The query is
`SELECT * FROM Restrictions_Data_Car WHERE IsCarInstalled(ModelId) ORDER BY ModelId` (`sub_8253D9E0`, string
0x82010FB0), the car list a game state builds when it is entered (`sub_82742D98` <- `sub_827431B0` <-
`sub_825CB940` <- the state machine `sub_82F111F0`). The other 3 long frames were the render
thread's (the main loop ran 9-12 frames meanwhile).

**What it is.** The game keeps its data in an in-memory SQLite 3.6.19 database (`:memory:`, the tables copied
from `gamedb.slt` and patched by each DLC's `*_merge.slt`) and runs queries synchronously on the thread that asks.
Title Update 4 (the Rally Expansion) creates the view `Restrictions_Data_Car`: every drivable car (`Data_Car`)
with `CarValue`, `IsRally` and `CouldRally`, LEFT JOINed to three derived tables: the level 4 springs, the level 5
tyres, and the level 4 transmissions joined to their drivetrain. 3.6.19 has no automatic indexes (3.7.0) and
does not flatten a derived table on the right of a LEFT JOIN, so it materializes the three and scans each one for
every car: with all DLC, 251 cars x about 250 rows x 3, plus the transmissions' own join (986 x 251) without an
index. The disc version has no such view (its `Drivable_Data_Car` is a plain filter). The main loop runs the query
twice after every load into free roam. Signing up for an event runs three others on the view on another thread
(F80013AC: the career garage's qualification, at the event card, the car select and the upgrade prompt); the main
loop waits for the database lock meanwhile when it queries too. The queries are CPU-bound game code; nothing on
our side waits or reads files.

**The fix** (`src/hooks/database_query.cpp`). When the game creates the view (the statement is recognized by its
text's length and hash), the query method gets a rewrite instead: the same columns (names, order, declared types)
and rows, from joins on the tables themselves (the springs and tyres with their level in the ON clause; the
transmissions through the car's drivetrains that have a level 4 transmission, `EXISTS`, then those
transmissions), followed by indexes on the four joined columns (the game itself indexes the springs' `Ordinal`
later, `i_5`). Each join becomes an index lookup. When the game rebuilds its database (its vtable+88, TU4
`sub_82CD4C50`), it copies the tables without their indexes and runs each view's SQL from `sqlite_master` again
through the query method; the hook knows its own view's stored text too and gives the indexes again (without
them each join of the rewrite scans its whole table, slower than the game's view). `FORZA_GAME_CAR_VIEW=1` keeps the game's
view. The statements and their recognition are in `src/platform/car_view.h` (unit test
`forza-recomp/tests/car_view_test.cpp`).

Checked equal: `tools/check_car_view.py` (the game's statement from the update's default.xex, the rewrite from
the hook, TU4's gamedb plain, with the 17 database patches of the update and all DLC, and with edge cases: parts
missing, duplicate level 4 parts, extra drivetrains with and without a level 4 transmission, no drivetrain, cars
not drivable): columns and rows identical (PASS x3); it fails on a variant without the `EXISTS`. It also replays
the rebuild (the stored view text is the hook's statement; the 4 indexes come back; the rows stay the same). In the game, a
debug build hashed every value the recordsets read (type, integer, double, text): the 251 rows of the main loop's
query hash the same with either view, and so does every other statement of the session except reads of
`sqlite_master` (the schema lists 4 more indexes). The other game queries on the four tables look rows up by `Id`
or by one `Ordinal` / `DrivetrainID` and return at most one row, so the new indexes do not change their order.

**Measured** (TU4, Ryzen 7 7800X3D, RX 9060 XT, Linux, the test save with all 23 DLC packages, 30 fps, no other
game running; 2 runs per view, interleaved; the route: boot to free roam, sign up for the festival race at the
spawn, quit from the race intro back to free roam, drive 10 s):

| | Game's view | Indexed view |
| --- | --- | --- |
| Main loop query after a load (8 runs) | 129-133 ms | 16.2-17.7 ms |
| The frame that holds it | 155-193 ms | 32-74 ms |
| Longest frame within 1 s of it | 155-193 ms | 66-75 ms |
| Event sign-up: the 3 garage queries on the view (6 runs, thread F80013AC) | 103-105 ms | 6.2-7.5 ms |
| A main loop query waiting for the lock meanwhile (car select) | 104-105 ms (both runs) | none over 50 ms |
| Time in queries on the view, per run | about 830 ms | about 90 ms |

The creation of the view and its indexes takes 6 ms once at start. On the player's PC the query took about 0.3 s;
at the same ratio it takes about 40 ms there. Frames of 60-75 ms remain in the first seconds after a load on this
PC with either view; they are not the database's (no query took 50 ms with the indexed view).
## Hitches when new cars come into view: the frame end waited for new pipelines (#167, 2026-10-28)

**The report.** The same player (RX 6700 XT, AMD's Windows driver 26.10.1) on 0.2.0, after the car view fix:
most hitches gone, but some remain "in certain spots, usually when there are cars around", shorter than before.
The 0.2.0 session of the log (32 minutes, online free roam, races, the festival) had 12 frames of 200-979 ms. In
all but one (just after a load) the FORZA_STALL chains put the main loop in its wait for room in the
render thread's command buffer (TU4 chain 82424FB4, a loop around the sleep `sub_82B2A6B0`), the render thread in the D3D
library's wait for a GPU fence at its swap (TU4 `sub_824192A0`, lr 82419338, the loop
`src/hooks/gpu_wait.cpp` sleeps in), and the command processor's slow-frame line named "the game" with 13-18 ms
of CPU in frames of 250-300 ms: it was neither working nor waiting for the GPU. Each stall came with new
pipelines in the log, from one (16:11:10: a placeholder in 0.1 ms on the command processor, the real pipeline on
a creation thread, a 286 ms frame) to 60 (16:17:43, entering the festival area: 979 ms, below). That session created
540 pipelines the store did not have (1,332 at its start, 1,871 at the next). The `FORZA_SQL` lines of the
session (a 145 ms query of `Profile0_View_UpgradeAreas` on another thread while the main loop waited for the
database lock, 8 times in 2.5 minutes at the festival workshop) are a separate, smaller cost.

**What it is.** With asynchronous creation the SDK skips a draw whose pipeline is not built yet (a placeholder,
or one the lookahead of 0270 is building ahead) and does not show that frame
(`vulkan_async_skip_incomplete_frames`). But `VulkanPipelineCache::EndSubmission`, at every submission end, the
swap included, built the queued pipelines on the command processor and then waited until the creation threads
had built every pipeline in flight (Xenia's design, where recorded commands could refer to pipelines still being
created; here nothing recorded does). So each pipeline the game met for the first time stopped the command
processor for as long as the driver took to compile it, and with it the render thread and the main loop. The
wait is on an event, so the frame-cost report counted it as idle time. On RADV a pipeline takes 5-30 ms, which
is why it never showed on Linux; AMD's Windows driver takes 0.2-0.3 s for the game's bigger shaders. Since the
lookahead (0.1.34) one miss can start up to 64 pipelines at once, and the frame end waited for all of them. New cars
bring new materials, hence "when there are cars around"; the pipelines are kept, so the same cars do not hitch
again in later sessions.

Reproduced on this PC by emulating a slow driver: a local, unshipped build of the plugin that sleeps 150 ms before
each real pipeline creation. Stacks (`eu-stack`) at each FORZA_STALL: the command processor in
`VulkanPipelineCache::EndSubmission`, waiting for the creation threads (4 of 6), creating a pipeline for a draw
without a pixel shader itself (1: such a draw has no placeholder, and unless the lookahead queued it the command
processor builds it; one in the player's session), in `WAIT_REG_MEM` just after the load (1).

**The fix (ReIGlue 0364, two commits).** The frame waits for the pipelines in flight for at most
`vulkan_async_frame_end_wait_ms` (default 33; 0 not at all; -1 the old wait), one budget from swap to swap that
every submission end of the frame (draw-limit splits, readbacks) and both pipeline caches share; the creation
threads go on in the background, and the time waited counts as pipeline time in the frame's cost. Frames that need
pipelines still being built are held back (`vulkan_async_skip_incomplete_frames`); for at most
`vulkan_async_skip_incomplete_frames_ms` (default 100) if they only lack object draws, after which they are shown
without those objects until the pipelines are ready. A frame that skipped a draw whose absence would leave stale or
wrong contents is held however long the build takes: a depth or stencil only draw (shadow casters, Z, stencil
masks), a screen-space pass (6 host vertices or fewer: a full-screen effect, a composite, a movie frame, a HUD
element), a draw into the 1280-wide unmultisampled 10-bit front buffer or UI target or the native HUD, and any draw
of the VR stereo segment (the right eye replays the left's draws and could find the pipeline built). Synchronous
creation (`async_shader_compilation` off, or `sync_pipeline_creation`, `src/hooks/thumbnail_sync.cpp`) keeps the old
wait; besides a car's picture and its livery, photo mode now holds it (its photo is composed and read back on the
CPU at SAVE; docs/rendering.md). The `Pipelines:` summary gives the frames held past the hold, the skipped draws
that held them by kind, the frames shown without draws, and the resolves copied back for the CPU while draws were
skipped (each also logs `Pipelines: a resolve copied back for the CPU ... missed draws`; a cold cache's boot and
loads make about 100 of those, as before 0364).

**Measured** (TU4, Ryzen 7 7800X3D, RX 9060 XT, RADV, Linux, 60 fps, 1920x1080 window, offline, the golden save,
an empty `--cache_root` and `MESA_SHADER_CACHE_DISABLE=true` so every run compiles the same pipelines, 10 minutes
of free roam driven by `tools/autodrive.py` round the #45 loop with the traffic, about 470 pipelines built per
run; frames of the drive after the boot and load; "game" are the game's frames (VdSwap), "shown" the presents;
`BENCH_LOCK` held, no other game running; "before" is the SDK of 7d17ea51 with the same game build; 0364 is
ReIGlue 420ec4f, without 30c3afb, which only changes when replaced placeholders are destroyed):

| Driver compile | SDK | Game frames >= 100 ms (total, longest) | Shown frames >= 100 ms (total, longest) | Shown >= 50 ms | FORZA_STALL | Frames held past the hold / shown without draws |
| --- | --- | --- | --- | --- | --- | --- |
| RADV as is | before | 0 (longest 51 ms) | 0 (longest 79 ms) | 17 | 0 | - |
| RADV as is | 0364 | 0 (longest 34 ms) | 0 (longest 67 ms) | 7 | 0 | 3 / 0 |
| 150 ms emulated | before | 109 (20.8 s, 352 ms) | 105 (20.9 s, 542 ms) | 105 | 21 | - |
| 150 ms emulated | 0364 | 12 (2.1 s, 199 ms) | 93 (19.5 s, 767 ms) | 96 | 0 | 314 / 0 |
| 150 ms emulated | 0364, hold 50 ms, wait 16 ms | 10 (1.7 s, 182 ms) | 92 (19.3 s, 509 ms) | 96 | 0 | 770 / 0 |

On RADV the change shows nothing: no game or shown frame reaches 100 ms either way. With the emulated slow driver the
game no longer stops: its frames of 100 ms or more go from 109 to 12 (those left are draws without a pixel shader
that the lookahead did not reach, built on the command processor), FORZA_STALL from 21 to 0. The picture does not
come back sooner, though: no frame was shown without draws in any run, because every frame that skipped draws also
skipped one that must not be missing. Of the 134,934 draws the 0364 run skipped, 72,613 were depth or stencil only
and 59,381 went to a 1280-wide unmultisampled 10-bit target (screen passes 2,940): a new car brings its shadow and
depth draws with its materials, and the 1280-wide test matches more than the front buffer and the UI here (which
targets, not checked). So the
picture holds for as long as the pipeline takes, as before (shown frames over 100 ms 105 and 93, mostly 150-200 ms,
the emulated compile), while the game, its sound and the controls go on; the hold and wait settings change nothing
there. Showing such frames sooner would need the depth and 1280-wide rules relaxed, with the stale contents they
guard against. The frame-end wait is now counted as pipeline time, which is why the command processor's pipeline
time in the drive rose from about 1.9 s to 15 s: 33 ms in each frame that met pipelines still being built.

Not changed: a draw without a pixel shader that the lookahead did not reach has no placeholder and is still built
on the command processor (the 10-12 game frames over 100 ms of the emulated runs); one the lookahead queued is
skipped, and holds its frame. The 8 s `sync_pipeline_creation` hold after each livery render still makes every new
pipeline in that time wait, which is what the 979 ms frame at the festival also had (929 ms of `Fh1WaitForAhead`,
the `waits` of the `Pipelines:` line); the upgrade view query above.

## Steam Deck and 4-core CPUs: the built-in benchmark (#45, 2026-10-23)

A Deck with every shader prebuilt (0.1.30) runs the built-in benchmark (docs/benchmark.md) at 19.7 fps, 1% low
10.9 fps, 591 of 1,182 frames over 50 ms; an i5-1135G7 (4 cores, 8 threads) about 22 fps (#263).

**The approximation and how far it goes.** This PC (Ryzen 7 7800X3D) has no SMT
(`/sys/devices/system/cpu/smt/control` is `notsupported`), so four cores are four hardware threads, not a Deck's
four cores with eight. The game ran on CPUs 0-3 (`taskset`, again after the threads it makes later); the samplers
on 4-7. Three configurations:

- **p4 at 30 fps**: four 5 GHz Zen 4 cores, the shipped frame cap. Each thread is 2-2.5 times faster than on a Deck
  (Zen 2, 2.4-3.5 GHz, in a 15 W budget shared with the GPU), so this shows how the CPU time divides, not the
  Deck's frame rate.
- **p4 at 240 fps** (`--frame_rate=240`): the same four cores without the cap. The frame rate is then bounded by
  the CPU side (the GPU emulation's command processor, below), so it moves with the CPU time that thread needs.
- **p4h1 at 30 fps**: one busy loop per core at the same priority from the benchmark's settling time, so a game
  thread gets about half a core: a "2x slower core". Unlike a slower clock it also delays every wakeup (the
  threads wait 3-50% of the time in the run queue), which a Deck does not.

The Deck's GPU is not approximated (this RX 9060 XT spends 5.7-8.1 ms per benchmark frame, `drm-engine-gfx`), and
it is not what limits the Deck: Steam's performance overlay in the player's free-roam video (0.1.28) shows the GPU
50-74% busy at 700-950 MHz, not boosting, while all eight CPU threads run at 35-45% at 3.35-3.5 GHz, also at 15-18
fps. No single CPU thread is shown full either: the frame waits on the chain of a few busy threads (the command
processor and the threads waiting for it), spread by the scheduler over the eight logical CPUs. So the command
processor's time per frame, below, is the number that matters for the Deck and the 4-core laptops.

Method: TU4 build with `-DFORZA_CPU_TARGET=x86-64-v3` (as the launcher builds it for these CPUs), the patched
SDK of main plus the change below, the benchmark started directly (`benchmark-request.txt` in a scratch
`--user_data_root` holding a copy of the Xenia save), `--present_effect=fsr --resolution_scale=1`, offline,
audio muted, warm scratch shader cache, BENCH lock held, no other game. Per-thread CPU from `/proc` once a second
(`tools/thread_stats.py`) over the measured minute; hot functions from `PERF_CPUPROF=1`; guest entry functions
from `eu-stack`; packet and section counts from a temporary counting build of the GPU plugin (not committed).

**Where the time goes** (p4, 30 fps, three runs; ms of CPU per frame; all threads 23.1-24.5; one run on all eight
cores: 25.1, the same split):

| Thread (guest entry) | ms per frame | What it does |
| --- | --- | --- |
| GPU command processor | 7.1-7.7 | PM4 parsing and register writes, then per draw: bindings, textures, render targets, pipeline lookups |
| Audio mixer (`sub_83075D18`) | 4.1-4.5 | the game's mixer and DSP (`sub_830CE6C0`, `sub_8306F2E8`, `sub_83087D70`, ...): 4.4 ms a frame at 30 fps is 13% of a core, fixed per second |
| Render thread (`sub_8261E178`) | 3.3-3.5 | D3D command building (`sub_824701B8`, `sub_82446860`, ...), plus its waits' yield phase (`sched_yield` 9% of its samples) and write-watch faults (`mprotect`, the fault handler) |
| Main loop (`sub_825CEC88`) | 2.6 | simulation, physics, AI |
| Vulkan submission | 1.3-1.4 | the driver (`ioctl`, the deferred command buffer's replay) |
| `sub_82C6BD58`, `sub_82CCC368`, `sub_82EE37A0` (game workers) | 1.0, 0.75, 0.74 | game code |
| XMA decoder | 0.3-0.4 | 2,500-3,000 wakeups a second |

Uncapped (p4 at 240) the benchmark runs 109-116 fps with the command processor busy 82% of the time (7.1-7.6 ms
a frame), the render thread 39%, the main loop 25%: the command processor is what the frame waits for. At 2-2.5
times this time per instruction that alone caps a Deck near 50-60 fps, lower where a busy SMT sibling shares its
core.

Synchronization costs little now (#286): `sched_yield` 1.4% of all samples, `mprotect` 1.1% (950-2,100 calls and
240-360 write-watch faults a second), futex lock and wake 0.6%, about 11,000 voluntary switches a second.

**The command processor.** A flat profile (top self time: `ExecutePacket` 8.5%, `UpdateBindings` 6.2%,
`WriteRegistersFromMem` 5.4%, `WriteRegister` 4.5%, `IssueDraw` 3.3%; the shader hash `XXH3` plus `LoadShader`
3.1%), because of how much it is fed. Each second of the race at 30 fps: 2.0-2.8 million type-0 packets (27-38
million register words), 1.7-2.6 million type-2 padding packets, 1.4-2.2 million type-3 packets: about 4,200 draws
a frame (`PM4_DRAW_INDX` 125,000-195,000 a second), as many `PM4_IM_LOAD` shader loads, 20,000-34,000
`SET_BIN_MASK` packets, 7,700 predicated packets skipped and about 100 resolves a frame. With section timers, per
1,000 draws: 1.95 ms of busy time, 1.12 of it in the draws (bindings 27% of that, textures 14%, shader
modifications, translations and samplers 13%, render targets 12%), 0.23 in register ranges, 0.04 in shader loads
and 0.50 in the rest of the packet loop.

**The EDRAM tiles (the VR study's lead, #335).** Free roam and the 8-car and festival races draw the scene with
4x MSAA in three predicated EDRAM tiles; the command processor runs the scene's command buffer once per tile. Its
time per bin select (`SET_BIN_SELECT`, three tile passes a frame) at 30 fps: the tile passes took 146-158 ms a
second of its 260 busy (56-60%), with 70,000 of its 126,000 draws a second (tiles 1,320, 540 and 520 draws a
frame). The game itself can render with less tiling: its render scenarios (`media/renderscenarios.zip`) set
`TilingScenario`, an index into a table in the executable: 0 "4xMSAA" (free roam, 8-car and festival races, car
menus), 1 "2xMSAA" (two tiles: Global.xml, 12- and 16-car races, Autocross, Bowling), 2 "NonTiling", 3
"Small2xMSAA", 4 "SplitScreen_NonTiling2xMSAA". A test hook (not committed) set the value after the game reads
it (`sub_82E40028`, the integer setting's XML read, for the setting whose name function is `sub_82E42F98`). In the
benchmark, 2 gave one pass: 2,300-2,800 draws a frame instead of 4,200, the command processor's draw time 84
instead of 150 ms a second, the GPU 4.5 instead of 8.0 ms a frame; 1 gave two tiles, 6.2 ms of GPU. But in both
the static world (track, terrain, barriers, hills, signs) was missing: only the cars, the crowd, trees and the sky
were drawn. So the lead holds (the tiles are most of the command processor's time, and 4x MSAA a large share of
the GPU's), but switching the game's scenario is not enough: the world's renderer has some setup of its own for
the tiled 4x MSAA path: its recorded command lists (found and handled in [One-pass scene](#one-pass-scene-scene_msaa-45-2026-10-23)).

**Change: SDK patch 0140** (`docs/sdk_patches.md`): the packet loop skips runs of type-2 padding in one loop and
has no stack frame on its hot path; register ranges that do not wrap the ring are written without the runtime's
`BeginRead`/`EndRead`; the Vulkan backend stores the registers without side effects of a range itself; `PM4_IM_LOAD`
keeps the shader of each guest address while the address holds the same words (`memcmp`), instead of hashing the
microcode for every draw. The same commands and register values reach the backend. With the section timers, per
1,000 draws: busy 1.95 to 1.79 ms (-8%), register ranges 0.23 to 0.18, shader loads 0.044 to 0.032, the rest of the
packet loop 0.50 to 0.40; the draws unchanged.

Benchmark, A = main's SDK (with 0138), B = with 0140, same game binary, alternating, BENCH lock held:

| Configuration | Build | Runs | fps | 1% low fps | p99 ms | All threads, ms/frame | Command processor, ms/frame |
| --- | --- | --- | --- | --- | --- | --- | --- |
| p4, 240 | A | 6 | 112.6 [108.6-115.5] | 74.8 [68.8-77.6] | 12.4 [11.7-13.2] | 18.3 [17.9-18.6] | 7.35 [7.07-7.61] |
| p4, 240 | B | 6 | 119.0 [114.9-122.1] | 78.9 [76.7-84.2] | 11.7 [11.0-12.1] | 17.7 [17.3-18.2] | 6.85 [6.68-7.15] |
| p4, 30 | A | 5 | 30.0 | 26.9 [26.4-27.6] | 35.8 [35.3-36.0] | 23.9 [23.1-24.5] | 7.41 [7.11-7.60] |
| p4, 30 | B | 4 | 30.0 | 26.8 [26.4-27.2] | 35.9 [35.7-36.1] | 23.6 [22.8-24.1] | 7.02 [6.64-7.32] |
| p4h1, 30 | A | 4 | 30.0 | 23.1 [22.8-23.3] | 41.3 [41.0-41.6] | 23.4 [22.5-24.9] | 7.59 [7.10-8.45] |
| p4h1, 30 | B | 2 | 30.0 | 23.2 [22.9-23.5] | 41.5 [41.1-41.8] | 22.2 [21.9-22.4] | 6.66 [6.55-6.77] |

(Some runs use the series before 0138, which changes nothing on this discrete GPU: A at 240 115.5, 112.1, 111.2 and
114.7 fps without it, 113.2 and 108.6 with it; B 114.9, 119.4 and 120.0 without, 120.0, 117.3 and 122.1 with. Left
out: one A run at 240 that another job's signal hit, one B run it ended, and one run of each at 30 fps with
another job on CPUs 0-3.)

- Uncapped, the frame rate rises 6% (112.6 to 119.0 fps) and the 1% low 5%: the command processor needs 7% less
  time a frame, and the game 3% less in all.
- At 30 fps the frame rate is capped and the frame times do not move (also on half-speed cores, p4h1, where the
  1% low stays at 23 fps: that configuration is bound by the run-queue delays the busy loops add); the command
  processor's time falls 5-12%.
- Rendering: the benchmark's race completes in both builds (the same validity checks), screenshots of the race match.

**Left, for a Deck:**

- The EDRAM tiles (above): the largest single cost of the command processor and a large share of the GPU's;
  `scene_msaa` ([One-pass scene](#one-pass-scene-scene_msaa-45-2026-10-23)) removes them at the price of MSAA.
- `UpdateBindings` and the texture bindings, 41% of the draw time: Sean's drafts #179 (dynamic constant
  descriptors) and #192 (reuse of unchanged texture and sampler bindings) measured +7.7% and +6.5% fps on a
  CPU-bound PC; both are opt-in and need game validation.
- For the guest code generation (#176, #220): the Deck build already uses x86-64-v3. In the audio mixer `ppc_fma`
  (now the inline `vfmadd`) is still 3.2% of its samples, the byte-swapping vector loads and stores
  (`simde_mm_load_si128`, `simde_mm_shuffle_epi8`, `simde_mm_store_si128`) 12%, and the MXCSR flush-mode switches
  between VMX and FPU code (`enableFlushMode`/`disableFlushMode`, an `ldmxcsr` each) 0.5% of all samples and 1.3-1.4%
  of two worker threads.
- On the Deck the command processor's chain is the bound (above): a Deck log of the benchmark with these changes,
  with the performance overlay, would show how much of the gap they close.

## The command processor per draw, outside the bindings (#45, 2026-10-25)

The command processor is the thread a frame waits for on a Deck and on 4-core CPUs (above). Sean's drafts #179
(dynamic constant descriptors) and #192 (reuse of texture and sampler bindings) work on `UpdateBindings`,
`RequestTextures` and the samplers; this looks at the rest of its time per draw.

Method: the TU4 game build of main (048b553) with the SDK series of a8288c0 built with `-gline-tables-only` (the
same code, so samples see inlined functions), the user's Xenia save in a scratch `--user_data_root`, the local
Forza Live server, audio muted, a warm scratch shader cache, the low tier (`--frame_rate=30 --resolution_scale=1
--present_effect=fsr`), the inputs of `tools/perf_scenario.sh race` (the 8-car festival race, 26 s) and `freeroam`
(90 s of driving). Four cores: every game thread on CPUs 0-3 (`taskset`, as `PERF_CPUSET=0-3`), the samplers on
4-7; all cores: no `taskset`. The command processor's CPU and draws per frame come from 0180's
`fh1_frame_cost_csv` over the scene; hot functions from `PERF_CPUPROF=1` symbolized with their inlined frames
(`llvm-symbolizer --inlining`); call stacks from `eu-stack -1 -i` a few hundred times a run (at
`--frame_rate=240`, so the thread is busy); repeat rates from temporary counters (not committed). BENCH lock
held. Other jobs shared the machine during parts of the day (game runs that ignored the lock, a decompiler, a
busy wait): every run records the other games and how busy the CPUs were besides the game
(`tools/thread_stats.py report --cpus`), and runs with another game or more than 45% of a core of other load on
CPUs 0-3 (70% on all eight) were run again; they are not in the tables. (Its "race" was the car standing at the spawn point:
the 1000 Club intro took the sign-up inputs; see round 3 below.)

**Where the time goes** (four cores, 30 fps; % of the command processor's CPU samples by the function that was
running, inlined code counted in its caller; race 4.92 ms a frame, 3,040 draws, 1.62 ms per 1,000 draws; free
roam 3.44 ms, 2,060 draws, 1.67 ms):

| Area | Race | Free roam | What |
| --- | --- | --- | --- |
| Bindings, textures, samplers (#179, #192) | 28.4% | 27.8% | `UpdateBindings`, `RequestTextures`, sampler lookups, the driver's `vkUpdateDescriptorSets` |
| Register writes | 14.4% | 15.0% | `WriteRegistersFromMem` (constants and render state), `WriteRegisterRangeFromRing`, `copy_and_swap` (0140 already) |
| Render target setup | 8.6% | 7.6% | `RenderTargetCache::Update`: the EDRAM ownership walk (`ChangeOwnership`, a `std::map`), render target lookups, the sort of the bases, `PerformTransfersAndResolveClears`, the render pass key and framebuffer |
| Packet parsing | 8.2% | 8.3% | `ExecutePacket`, `ExecutePacketType3`, `ExecuteIndirectBuffer` |
| Shader and pipeline state | 7.2% | 6.1% | `PM4_IM_LOAD`'s memo (0140: a `memcmp` of the microcode), translation lookups, `GetCurrentStateDescription`, the pipeline map |
| `IssueDraw` itself | 6.3% | 5.8% | among it `GetVulkanDevice` (a virtual `provider()` call, 0.8%) and `rex::bit_scan_forward` (1.4%: a call into the runtime library for one instruction) |
| System constants, viewport, dynamic state | 6.3% | 5.7% | `UpdateSystemConstantValues`, `GetHostViewportInfo`, `UpdateDynamicState` |
| Shared memory and memory watches | 6.0% | 6.8% | `RequestRanges` (vertex and index data), `mprotect`, `FireWatches` |
| `memcpy`, `memcmp` (callers above) | 5.4% | 6.0% | |
| Sync, command recording, barriers, submission | 5.1% | 6.4% | `sched_yield`, futex wakes, `DeferredCommandBuffer`, `EndSubmission` |
| Primitive processing | 0.8% | 1.0% | `PrimitiveProcessor::Process` |

How often a draw repeats the one before (race, temporary counters): 95% of the draws have the render target
registers of the previous draw with nothing touching the render targets in between (and then the full setup
finds no transfer, no barrier and the same render pass); only 4.6% change no render state register at all, the
system constants change in 70%, and 60% use the previous draw's pipeline. So caching everything until any
register changes (as Pinyon Shift's register epoch does) would rarely hit here; the render target setup can be
kept almost always.

**Change 1: SDK patch 0340, the render target setup of the previous draw.** `VulkanRenderTargetCache::Update`
keeps the previous draw's result when the inputs the common `Update` reads are the same
(`RenderTargetCache::GetUpdateInputs`: rasterization, depth and stencil use, the color mask, `RB_SURFACE_INFO`,
`RB_DEPTH_INFO`, the four `RB_COLOR_INFO`, the clamp setting and the height `DrawExtentEstimator` gives), nothing
changed the cache since (`update_state_generation`: every `ChangeOwnership` call, a new render target, a reset of
the accumulated bindings, a clear or trim) and the bound render targets are still in their draw layout and
access (a resolve or a dump moves them out; then a barrier is due). Such a draw owns its EDRAM ranges already: the
full update would find no transfer, the same render pass and framebuffer and no barrier, so only the transfer
lists are cleared. Setting `vulkan_render_target_update_reuse` (default on); `..._verify` still runs the full
update for a reused draw and logs a difference in the ownership map, the bindings, the render pass, the
framebuffer, the commands or the barriers recorded; once a minute the log says how many draws reused it
(`Render target update reuse: N of M draws`). In a race and in free roam 94.6-94.8% of the draws reuse it
(menus and loading 56-78%).

- Verified: a race and free roam with `vulkan_render_target_update_reuse_verify`, every reused draw checked
  against its full update: no difference.
- Pixel check: the pause menu in free roam redraws the whole scene every frame (2,940-3,040 draws) and the
  picture stands still (two shots 3 s apart identical). Shots with the setting on, then switched off in the
  running game (its storage written through `/proc/PID/mem`), then on again: identical.

**Measured.** Other jobs kept loading the machine through the day, so the A/B runs switch the setting in the
running game, alternating within the scene (every 2 s in the race, 5 s in free roam, every 5 s on the pause menu;
the setting's storage written through `/proc/PID/mem`; the first 0.3-0.5 s of each window left out), three runs per
configuration starting with either state, the BENCH lock held, other load besides the game 6-23% of a core on
CPUs 0-3 in all but one run. The command processor's CPU per frame and per 1,000 draws over each state's windows;
the frame rate and p99 from the command processor's frame intervals (at 240 the guest's frame rate; the window
itself shows 144, the display's). Mean [min-max] over the runs:

| Scene | Cores, cap | 0340 | ms a frame | ms per 1,000 draws | draws a frame | fps | p99 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Race | 4, 30 fps | off | 4.99 [4.83-5.19] | 1.644 [1.626-1.681] | 3,032 | 30.0 | 34.1 |
| Race | 4, 30 fps | on | 4.62 [4.49-4.81] | 1.522 [1.508-1.547] | 3,037 | 30.0 | 34.1 |
| Free roam | 4, 30 fps | off | 3.50 [3.27-3.61] | 1.688 [1.677-1.709] | 2,071 | 30.0 | 34.2 |
| Free roam | 4, 30 fps | on | 3.16 [3.04-3.34] | 1.579 [1.566-1.589] | 2,001 | 30.0 | 34.1 |
| Race | 4, 240 fps | off | 4.78 [4.73-4.81] | 1.586 [1.572-1.609] | 3,016 | 158.4 [158.0-159.1] | 8.3 [8.0-8.4] |
| Race | 4, 240 fps | on | 4.44 [4.39-4.47] | 1.467 [1.454-1.491] | 3,023 | 165.1 [164.1-166.9] | 7.8 [7.6-8.1] |
| Race | 8, 30 fps | off | 4.99 [4.92-5.11] | 1.636 [1.635-1.637] | 3,047 | 30.0 | 33.8 |
| Race | 8, 30 fps | on | 4.62 [4.55-4.71] | 1.517 [1.514-1.521] | 3,043 | 30.0 | 33.8 |
| Free roam | 8, 30 fps | off | 3.52 [3.27-3.71] | 1.693 [1.671-1.712] | 2,080 | 30.0 | 33.8 |
| Free roam | 8, 30 fps | on | 3.19 [3.09-3.40] | 1.591 [1.568-1.613] | 2,010 | 30.0 | 33.8 |
| Pause menu (same frames) | 4, 30 fps | off / on | 4.91 / 4.54 | 1.637 / 1.518 | 2,994 | 30.0 | |
| Pause menu (same frames) | 8, 30 fps | off / on | 5.06 / 4.69 | 1.691 / 1.567 | 2,994 | 30.0 | |

- The command processor needs 7.3-7.5% less CPU per 1,000 draws in the race (four or eight cores, capped or not),
  6.0-6.4% less in free roam and 7.3% in the paused scene, whose frames are the same: about 0.12 ms per 1,000
  draws, 0.37 ms of a race frame here. Free roam's per-frame figures move with the scenery each window shows;
  per 1,000 draws they do not.
- Uncapped on four cores, the race runs 4.2% faster (158 to 165 fps) and its p99 is 0.5 ms shorter: the
  command processor is what that frame rate waits for. At 30 fps the frame rate is capped and the frame times do
  not move.
- Separate whole runs (on, off, alternating) agreed where no other job ran (race 4.43 against 5.10-5.13 ms a
  frame, free roam 3.26-3.29 against 3.55), but most of them shared the machine and are left out.
- Extrapolating to a Deck (2-2.5 times this time per instruction, above) suggests about 0.8-0.9 ms of a race
  frame on the thread the frame waits for. This is an estimate, not a measurement on a Deck.

**Change 2: SDK patch 0341, the memory watch walk.** A write to guest memory (a resolve on the command
processor, a CPU write fault on a game thread) tests every watch node of its 4 MB buckets: about 40 nodes a
write, 3,700 a frame in free roam here, 2,800-6,600 in the minute lines of an i7-8700K's log (#274), each a
dependent load of the node and of its range, under the global lock the game's write faults also take. The nodes
now hold their range's pages, and `gpu_memory_watch_bucket_log2` sets the bucket size (18-22, log2 bytes; 22, the
old 4 MB, is the default). The same ranges fire. Free roam, nodes checked a frame (the minute lines) and the
watches' nodes:

| `gpu_memory_watch_bucket_log2` | Nodes checked a frame | Watch nodes | `FireWatches` + `WatchMemoryRange`, CP samples |
| --- | --- | --- | --- |
| 22 (4 MB, before) | 3,670 | 875 | 0.8% |
| 20 (1 MB) | 2,628 | 932 | 0.6% |
| 18 (256 KB) | 1,027 | 958-966 | 0.6% |

Race on four cores at 30 fps, separate runs alternating (the setting is read at start; 0340 on in both): 22 gave
1.506 and 1.501 ms per 1,000 draws, 18 gave 1.510, 1.500 and 1.513 (a first 22 run that shared the machine with
another game, 1.585, left out), with 3,667-3,698 against 1,028-1,030 nodes checked a frame and 735-749 against
939-949 watch nodes. On this CPU the walk is 0.5-0.8% of the command processor and the difference is inside the
spread, so the default stays 22; the nodes' own pages (no change in behaviour) are always on. Worth a test where
cache misses cost more (#274's i7-8700K checks 2,800-6,600 nodes a frame, a Deck): `gpu_memory_watch_bucket_log2 =
18` and the minute line's `memory watch nodes checked` and `us a draw`.

**Left** (outside #179 and #192):

- `IssueDraw` and its helpers call `GetVulkanDevice()` (a virtual `provider()` call) several times a draw, and
  `rex::bit_scan_forward` / `tzcnt` are calls into the runtime library through the PLT for one instruction each:
  together about 2% of the command processor, gone with a cached device pointer and inline bit scans in the
  plugin (no behaviour change, so nothing to switch).
- Register writes (14-15%) are mostly the copies themselves after 0140; packet parsing 8%.
- Shader and pipeline state (6-7%): 0140's `PM4_IM_LOAD` memo compares the microcode with `memcmp` for every
  load; a translation lookup per shader per draw could keep the shader's last modification; 40% of the draws
  hash their pipeline description for the map.
- The system constants change in 70% of the draws, and 95% of the draws follow a change of some render state
  register, so a cache that holds until any register changes (Pinyon Shift's register epoch,
  `docs/PERFORMANCE_BACKLOG.md` in that project; its notes were read, nothing from them run) would rarely hold
  here. Most of their per-draw work went into the descriptor, sampler and texture binding steps (here #179 and
  #192); their render target memo, keyed on a register epoch, hit 50-62% of the draws, 0340's key (the render
  target registers only) 95%.

## The command processor per draw, round 3 (#45, 2026-10-25)

Round 2 (above) left the register writes (14-15 % of the command processor), the packet parsing
(8 %), the shader and pipeline state (6-7 %) and `IssueDraw`'s calls into the runtime library
(about 2 %). This round takes those, still outside #179 and #192.

Method: as round 2 (the same TU4 game build; the SDK series of main 673c1b8, with 0350, built
with `-gline-tables-only`; scratch XDG dirs and `--user_data_root`, a local Forza Live server,
audio muted, the low tier, four cores with `taskset` or all eight), with one difference: the
save. **The `race` scenario never reached the race:** with the user's Xenia save, TU4 plays the
1000 Club introduction video (about 45 s) the first time free roam loads signed in to Forza Live,
and then holds the car at the spawn point until the 1000 Club tutorial is done. Every run copies
the save afresh, so every run played it: the scenario's sign-up inputs (X, A, A, X) went into the
video and its "race" was the car standing at the spawn point (about 3,000 draws a frame, the
figures of round 2's race rows and of the first runs here, "Spawn" below); its free roam began
with about 15 s of the video. The runs below copy a save made once past the intro and the
tutorial (`scratchpad/golden-save-tu4`, its README says how): the race runs (5,000-5,600 draws
a frame, 7 ms of command processor a frame at 30 fps on four cores) and free roam drives for the
whole 90 s (2,900 draws a frame).

**What a draw costs in packets** (race, temporary counters, not committed): 14.7 type-0 packets
with 203 registers (8.0 ranges of render state registers, 3 registers each and 90 % of the
values unchanged; 4.4 float constant ranges of 32 registers; 2.3 fetch constant ranges; 1.1
packets that write one register repeatedly), 8.8 type-3 packets (4.5 `SET_BIN_MASK_LO/HI`, 1.1
`IM_LOAD` of 143 dwords, 1.0 draw, 0.85 `LOAD_ALU_CONSTANT`, 0.7 `SET_CONSTANT` and
`SET_CONSTANT2` of registers, 0.26 indirect buffers) and 13 padding words in 6 runs. 39 % of the
draws follow no change of any render state register.

**Changes** (`docs/sdk_patches.md` has the details):

- **0360**, register ranges in the packet loop (`gpu_plain_register_ranges`, default on): a
  range made only of render state registers the Vulkan backend just stores is stored by the
  packet loop from the ring, without the two virtual calls (`WriteRegisterRangeFromRing`,
  `WriteRegistersFromMem`) and the backend's sorting of the range; a range inside the ring no
  longer divides for its read offset.
- **0361**, no calls into the runtime library for one instruction: `GetVulkanDevice` keeps the
  device pointer instead of calling the virtual `provider()`; `rex::bit_scan_forward`, `tzcnt`
  and `lzcnt` are inline in the GPU plugin. No setting (no behaviour change):
  `tools/test_inline_bit_scans.cpp` checks every 32-bit value and 2^28 random 64-bit values
  against the runtime's functions.
- **0362**, the last translation of each shader (`gpu_shader_translation_memo`, default on;
  `..._verify` also looks it up and logs a difference): a draw that asks a shader for the same
  modification as the draw before skips the map lookup.

Checks: a race and free roam with `gpu_shader_translation_memo_verify`, no difference; the paused
scene (pause menu over free roam, the same frames) with 0360 and 0362 on, switched off in the
running game and on again: the shots are identical.

**Measured**, 0360 and 0362 together, switched in the running game every 2 s (race, spawn) or
5 s (free roam) as in round 2 (the cvars' storage written through `/proc/PID/mem`, the first
0.3 s of each window left out), 0361 on in both states. The BENCH lock held; the user's desktop
and other agents' builds kept 10-35 % of a core busy on CPUs 0-3 and 50-90 % on all eight, so
the limits of round 2 were 45 % and 100 %. Command processor CPU per 1,000 draws, mean [min-max]
over the runs; "paired" compares each on window with its neighbouring off windows:

| Scene | Cores, cap | Runs | off | on | change | paired | fps off / on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Race | 4, 30 fps | 2 | 1.355 [1.334-1.375] | 1.317 [1.308-1.326] | -2.7 % [-4.9, -0.6] | -2.4 % | 30 / 30 |
| Free roam | 4, 30 fps | 2 | 1.504 [1.484-1.525] | 1.460 [1.441-1.479] | -3.0 % [-3.0, -2.9] | -2.8 % | 30 / 30 |
| Race | 4, 240 fps | 1 | 1.296 | 1.282 | -1.1 % | -1.2 % | 114.7 / 118.7 |
| Free roam | 4, 240 fps | 1 | 1.388 | 1.359 | -2.1 % | -2.1 % | 201.7 / 203.6 |
| Race | 8, 30 fps | 1 | 1.403 | 1.331 | -5.1 % | -4.6 % | 30 / 30 |
| Free roam | 8, 30 fps | 1 | 1.513 | 1.484 | -1.9 % | -2.1 % | 30 / 30 |
| Spawn (standing) | 4, 30 fps | 1 | 1.429 | 1.392 | -2.6 % | -2.5 % | 30 / 30 |
| Spawn (standing) | 4, 240 fps | 1 | 1.349 | 1.309 | -3.0 % | -2.9 % | 178.1 / 185.2 |
| Spawn (standing) | 8, 30 fps | 1 | 1.460 | 1.452 | -0.5 % | -0.6 % | 30 / 30 |

- 0360 and 0362 take 2-3 % off the command processor's CPU per draw (about 0.04 ms per 1,000
  draws here; 0.2 ms of a 5,300-draw race frame). The race moves from window to window (draws
  a frame differ by 10 % between neighbouring windows), so its single runs scatter (-0.6 to
  -5.1 %); the standing scene and free roam agree on 2-3 %. Uncapped on four cores the guest's
  frame rate rose 3.5-4 % where the command processor bounds it (race, spawn), not in free roam.
- 0361 was not measured on its own: in the profiles before it, `bit_scan_forward` and
  `provider()` were 2.2-2.3 % of the command processor's samples, now gone from the plugin's
  imports; expect about that.
- Fewer runs than planned (1-2 per configuration instead of 3): the round was cut short.

**Left** (outside #179 and #192):

- Each of 0360 and 0362 on its own, 0361 against main (whole runs alternating, the standing
  scene), and three runs per configuration: the harness has the plans (`stand`/`standab`
  scenarios); not run.
- The register writes left are mostly the float constants (5 ranges, 170 registers a draw, the
  copy itself) and the stream itself: the packet loop's first read of each packet, where
  reading the game's command buffer stalls, is about 2 % of the command processor here; on a CPU with a small cache or slow memory (BC-250's GDDR6, a Deck) that
  share is probably larger. Not tried: prefetching the ring ahead.
- `IM_LOAD`'s memo compares the shader's 143 dwords with guest memory on every load (1.1 a
  draw); the pipeline description (`GetCurrentStateDescription`, about 1 %) is still built for
  every draw.
- System constants, viewport and dynamic state (5-6 %) as round 2 left them.

## One-pass scene: `scene_msaa` (#45, 2026-10-23)

The three EDRAM tiles above are most of the command processor's time. The game can render
without them: its render scenario table (`TilingScenario`) has a two-tile 2x MSAA entry, used by
its 12- and 16-car races, and a one-pass entry without MSAA. Switching to either lost the static
world (above). Why, found with RenderDoc captures of the benchmark (a tiled and a one-pass frame
compared draw by draw, `qrenderdoc --python`), packet counters in the command processor and call
counts with gdb:

- The 3D scene of the one-pass frame had 918 draws against 2,989 over the three tiles, and 316
  distinct opaque draws of the tiled frame (806 with the tile repeats) were not in it at all:
  the game never submitted them (the command processor's early-outs counted none).
- The track (static world) presentation, `Presentation_Unified::CTrackPresentation`, draws each
  pass from command lists recorded per pass and layer (index pass * 3 + layer, `sub_82466418`).
  Its scene pass depends on the tile count of the scenario (`sub_82469B18`: 4 for three tiles, 3
  for two, 5 for one), and the game records lists for pass 4 only: in a one-pass race every pass-5
  call found no list (68,000 a second, against 87,000 calls with a list in pass 4 tiled).
- `sub_82466418` has a second path, chosen by its last argument: `sub_82EAF3B0` draws the same items
  directly, without recorded lists. 0.1.32 took it for passes 3 and 5 without a list, which brought
  the road and terrain back but not everything: the procedural models
  (`proceduralGeometry::CProceduralModels`, trees, concrete barriers: `sub_82447B70`, the pass in
  its ninth argument) draw from the same kind of lists and have no direct path, and the track skips
  instanced items for which `sub_82EAC930` finds no list of the pass. Players saw trees and barriers
  vanish as they drove past in the Recaro Rush races with `off` (#268); in the benchmark race the
  concrete barriers on both sides of the road were missing.
- Pass 4's lists as they are cannot be replayed in a pass of two tiles or one. Their PM4 packets
  (dumped from the game): each starts, per tile, with a bin mask and a predicated SET_CONSTANT of
  PA_SC_WINDOW_OFFSET and the window scissor (rows 0, -256, -512), repeated before the first draw
  and at the end, and its first draw sets the render target of a 1280x256 tile with 4x MSAA
  (RB_SURFACE_INFO, RB_COLOR_INFO, and RB_DEPTH_INFO with the depth at EDRAM tile 1024) and
  PA_SC_AA_CONFIG. Replayed in one pass, the window moved up (the sky drawn into the bottom of the
  screen) and the items and everything after them went into the tiled EDRAM layout (the road and
  terrain gone). D3D marks such lists (command buffer flag 0x800: recorded with tiling rectangles;
  the shadow, reflection and split-screen passes' lists have none) and flushes all pending state
  before it runs one (`sub_824469B0`), so the scene's own window and render target are already set
  when a list starts.

**Change** (`src/hooks/scene_tiling.cpp`, `src/platform/scene_tiling.h`, launcher Display >
Picture > "Scene anti-aliasing (MSAA)"): `scene_msaa = game` (default) keeps the game's 4x MSAA in
three tiles; `2x` lowers the 4x scenario to the game's two-tile 2x MSAA one; `off` lowers 4x and 2x
to its one-pass scenario without MSAA. The scenario is lowered right after the game reads it from
the XML (`sub_82E40028`, for the setting whose name function is `sub_82E42F98`). Passes 3 and 5
draw pass 4's lists: `sub_82EAC930` finds them, `sub_82466418` draws them with its list path
(`sub_82466560`) after its own work, the procedural models are given pass 4, and when a pass's view
changes (`sub_82EADC80`: its lists are fixed up again) pass 4 is reset with it. When a list
recorded for the tiles runs for pass 3 or 5 (`sub_824467A8`, which hands it to D3D's
RunCommandBuffer), its window and scissor writes, render target and MSAA config become NOPs of the
same length, and they are put back (the headers are kept per list) before it runs for another
pass: the game reads all its scenarios' `TilingScenario` values at startup (0, 1 and 4) and uses
the current screen's, so a list can run for pass 4 later in the session (changing the values in
free roam switched the tiling from the next frame; a race after that drew as in `game`). The
list's first window write being a NOP marks it as done (one loaded again at the same address
has its packets back). The direct path is not used any more. Pass 4, the game's 4x scenario, is
unchanged. Disc build: `sub_82D7DD68`, `sub_82D80DD8`, `sub_82436468`, `sub_824365B0`,
`sub_82DEA700`, `sub_82417BC0`, `sub_82DEBA08`, `sub_824167F8`.
`forza-recomp/tests/scene_tiling_test.cpp` checks the scenario choice, the pass rule and which
packets of a list are skipped.

**Screenshots** (TU4, scratch copy of the Xenia save, kept out of Git): the benchmark race every 2 s
from its start, and an 18.8 km free-roam loop south of the festival (County Route 203 from the save's
spawn, Routes 28, 29 and 4, the Montano Plains outpost by the Recaro Rush races, the festival's
outer loop; `tools/nav_route.py`, driven by `tools/autodrive.py`) with a screenshot every 240 m,
in each mode. With the direct path of 0.1.32 the concrete barriers along the benchmark race were
missing in `off`; now the track, terrain, barriers, guard rails, fences, signs, trees, crowd and
cars are there in `2x` and `off` as in `game` at each point, also where the two tiles meet in
`2x`; the edges are more jagged without MSAA. Shots of the same point differ by the traffic, the
time of day and where the car is.

**Measured** with the direct path of 0.1.32, when `2x` and `off` lacked the procedural models and
some track items (benchmark as in the 4-core section, the same build with patch 0140, two runs per
mode and configuration, alternating, BENCH lock held, no other game; ms per frame):

| Configuration | scene_msaa | fps | 1% low fps | Command processor | Render thread | All threads | GPU (RX 9060 XT) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 cores, uncapped | game | 114.9 [112.8-116.9] | 78.8 [77.6-80.0] | 7.16 [7.00-7.31] | 3.54 | 18.2 | 5.8 |
| 4 cores, uncapped | 2x | 138.9 [130.4-147.4] | 90.7 [89.0-92.3] | 5.90 [5.46-6.34] | 4.10 | 16.8 | 5.3 |
| 4 cores, uncapped | off | 142.2 [135.4-149.0] | 92.6 [91.4-93.8] | 5.13 [4.82-5.44] | 3.99 | 15.7 | 4.9 |
| 4 cores, 30 fps | game | 30.0 | 26.9 [26.5-27.2] | 6.86 [6.80-6.92] | 3.41 | 23.2 | 8.0 |
| 4 cores, 30 fps | 2x | 30.0 | 26.9 [26.6-27.2] | 5.85 [5.39-6.31] | 4.07 | 22.9 | 7.5 |
| 4 cores, 30 fps | off | 30.0 | 27.3 [27.3-27.3] | 4.86 [4.75-4.97] | 4.04 | 21.6 | 6.9 |

With pass 4's lists for those passes (2026-10-24, main after 0.1.32 with the change, 4 cores,
uncapped, two runs per mode alternating, BENCH lock held, no other game; `2x` one run, a second
was dropped when other games started):

| scene_msaa | fps | 1% low fps | Command processor | Render thread | All threads | GPU (RX 9060 XT) |
| --- | --- | --- | --- | --- | --- | --- |
| game | 119.5 [116.6-122.4] | 78.2 [76.0-80.3] | 6.82 [6.66-6.97] | 3.45 | 17.5 | 5.7 |
| off, 0.1.32 (direct path) | 156.3 [154.1-158.4] | 98.3 [98.0-98.5] | 4.55 [4.45-4.64] | 3.84 | 14.6 | 4.8 |
| off | 137.4 [134.8-139.9] | 91.0 [90.2-91.7] | 5.38 [5.30-5.46] | 3.42 | 15.4 | 4.9 |
| 2x | 143.4 | 87.8 | 5.70 | 3.33 | 15.6 | 5.2 |

- `off`: the command processor needs 21% less time a frame than `game` (6.82 to 5.38 ms), the GPU
  13% less, and the benchmark runs 15% faster with a 1% low 16% higher. The 0.1.32 figures were
  better because the trees, barriers and part of the track were not drawn at all; drawing them
  costs the command processor 0.8 ms a frame, and running their lists again instead of the direct
  path saves the render thread 0.4 ms. The first table's `off` and `2x` rows are of that build.
- `2x`: two tiles instead of three: the command processor 16% less than `game`, the GPU 8% less.
- On a Deck, where the frame waits on the command processor's chain (above), the command processor's
  1.4 ms a frame at 5 GHz is about 3 ms of a Deck frame.
- Not tested yet: with the FSR 3, DLSS and SMAA scene paths (they look for the game's scene and HUD
  targets), ultrawide, render scale above 1, photo mode, Windows and a Deck. It stays off by default.

## Scene resolution below 720p: `scene_resolution` (#347, 2026-10-24)

**Status: experimental** (launcher Display > Picture, "3D scene resolution").
`scene_resolution = "540"` draws the 3D scene and
its effects at 960x540 under the 720p HUD. Free roam, the pause menu, the map, photo mode, car
select, the event screens and races (loads, restarts, a full race, rewind, photo mode, the
replay) render correctly with no GPU errors. Loading a race crashed until the textures kept their
720p memory (below). Default `"720"` changes nothing (the hooks pass through).

The idea: render scale starts at 1 (the game's 1280x720). For integrated graphics that cannot hold
the frame rate (#263 Iris Xe, #268 Vega), draw the scene smaller and let the game's tonemap scale it
onto its 720p front buffer; the HUD, text and menus are drawn on that front buffer at 720p as
before, and the SDK's upscaling filter (FSR 1) takes the 720p picture to the window. Game hooks only
(`src/hooks/scene_resolution.cpp`, `src/platform/scene_resolution.h`), no SDK change: the SDK's
render target cache scales by whole numbers only, so a fractional scale there would mean
fractional EDRAM tiles, resolves and texture coordinates for some passes and not others (the
ultrawide path, `docs/ultrawide.md`, gave up on fractional heights for that reason).

**How the game sizes its scene** (disc addresses; TU4 ones in the hook file):

- The render scenario (`TilingScenario`, `src/platform/scene_tiling.h`) is an entry of a table at
  `0x832A4860` (TU4 `0x833901F8`), 0x4D0 bytes each: name, width, height, MSAA, tile count and the
  EDRAM tiles as rectangles. All five are 1280x720: "1280x720 4xMSAA Horizontal Split" (three
  tiles), "2xMSAA" (two), "1xMSAA No Split", "Largest 2xMSAA No Split" (one 1120x584 tile, chosen
  by no XML) and split screen (a 1280x368 tile per player). `sub_82C3F374` makes each scenario's
  surfaces from it (`sub_82C3F2E8`: full size and tile size; colour, 8888, depth), the scenario
  switch (`sub_82C3F548`) writes its size into the D3D presentation parameters, and the scene's
  passes take their sizes from it. `UITilingScenario` (2 in every XML) is the menus' one.
- The screen textures come from a table of 41 entries at `0x832295B0` (TU4 `0x8330C830`, 0x2C
  bytes): a flag, then width and height as divisors of a hard-coded 1280x720 (`WorldRender`,
  `ScratchBuffer`, `ShadowMask`, `WorldDepth`, `Bloom`, the post chain's `Shared` buffers, the
  `FrontBuffer_0/1` the HUD is drawn on and VdSwap presents, `MapRender`), or absolute sizes
  (shadow maps, cube map, a 640x360 half-size buffer). `sub_8257C220` copies it into the
  renderer's slots (its r3 + 0x1A0, 0x34 bytes each: the entry, then its memory size and
  address), makes the headers of the D3D textures that follow the slots (r3 + 0x9F4, 0x34 bytes
  each, in table order) and adds up their memory.
- The textures share memory. An entry whose +0x1C is 0x29 has memory of its own; any other value
  is the entry whose memory it lies in, at its start or right after it (+0x20). So most `Shared`
  buffers lie in `ScratchBuffer` or the front buffers, `MapRender` (1280x720) and two `Shared`
  textures in `WorldDepth`, and three 1024x1024 depth targets (12 MB) start in `ScratchBuffer` and
  cover the textures after it. `sub_82482E70` allocates one block for
  the memory pools and these textures, and the game's file heap comes right after it;
  `sub_8257C428` places the textures and lends parts of the block to other users with 720p sizes
  written in the code (0x384000 = 1280x720x4 from `WorldRender`, for example).
- The world view (`sub_82444E60`) draws with the view's viewport (+0x1A8), 0,0,1280,720, placed
  at (surface - viewport): with a smaller surface the scene moved off it.
- The post-processing quad (`sub_823E5628`) maps a rectangle in screen pixels to its source texture
  texel for texel; the tonemap (the screen in two halves) names 720p rectangles.

**What it does** (found with RenderDoc captures and call logs, `qrenderdoc --python`):

1. Scenario 3 becomes one pass at 960x540 with the most MSAA that fits the 10 MB of EDRAM: 2x
   (`scene_msaa = off` gives none), and the full-screen scenarios 0-2 are replaced by it (the
   scenario hook of `scene_tiling.cpp`; the track draws its one-tile pass directly, as with
   `scene_msaa = off`). The game's own tiled path at 960x540 (three tiles of 192 lines) left the
   first tile blank.
2. The scene's screen textures get 960x540 (the divided ones 960/d and 540/d rounded up to 32, as
   the game rounds); the front buffers and `MapRender` stay 720p. They keep their 720p memory: the
   header function (`sub_82A40F28`, TU4 `sub_82AFE678`, which returns the memory size) is called
   with the 720p size first, for the size, then with the scene's, for the header. The block lies
   exactly as at 720p. Changing only the scenario drew the scene but resolved it into 1280x720
   textures ("Resolve region 0 <= x < 1280 is outside the surface pitch 960"); changing the menus'
   scenario too broke the HUD.
3. The world view's 1280x720 viewport is 960x540 while it is drawn, and back afterwards (the
   HUD's projected name tags read it). Without this the scene was empty: RenderDoc showed a
   degenerate host viewport.
4. A post-processing rectangle ending at x = 1280 or y = 720 over a 960x540 source is scaled to it:
   the tonemap stretches the scene over the 720p front buffer (bilinear). Without it the scene was
   drawn texel for texel in the top-left 960x540 with the edges smeared.
5. Scenario 3's full-size 2:10:10:10 surface stays 1280x720: the render target manager's
   full-screen filters (fades and blurs on loads and camera cuts, `sub_82C3E310`..`sub_82C3EA60`)
   draw there and resolve into the 720p front buffers; at 960 their resolves were refused (60 a
   second for 9 s after loading free roam, 7 at each camera change).
6. A 1280x720 post-processing pass (`sub_824426B8`) into one of the scene's textures is drawn at
   960x540. On menu screens and loads the render target manager's full-screen filter
   (`sub_82C3E310`, from `sub_82586170`) copies the last 720p front buffer this way into the
   `Shared` 2:10:10:10 texture in `WorldDepth`.
7. The D3D device keeps a 1280x720 back buffer (it is made with the first scenario's size).
8. The sun's shadow mask (`sub_824532F0`, TU4 `sub_824829D8`; docs/graphics-detail.md "Shadow
   softness") is drawn at the scene size. Its code sets a 1280x720 viewport
   (`D3DDevice_SetViewport`, `sub_82410C30`, TU4 `sub_82440BE0`) and passes 1280x720 to the
   cascades' volume quad (`sub_82DDDEE0`, TU4 `sub_82EA04E8`) and the blur's taps (`sub_823E2C48`,
   TU4 `sub_82412BD8`). While the mask is drawn, those three get the scene size. Before this
   (#682, from 0.2.7, when entry GPUs got 540p by default) the mask passes covered 1280x720 of a
   surface whose depth and stencil came from the 960x540 pre-pass. The mask was then resolved
   cropped to 960x540, so the car's shadow became a streak running up and to the left of the car,
   and its real shadow was missing (RenderDoc: the pre-pass at 960x540, the 11 mask passes and the
   blur at 1280x720). It is the same on Vulkan and ReGPU, on AMD and NVIDIA, because it is in the
   guest's draws. The same spot in free roam at 540p and 720p: the shadow pixels' centre was
   (-71, -56) px off at 1280x720 and the shadow overlapped 720p's by 44%. With the fix it is
   (+1, +2) px off and overlaps by 91%; the remaining difference is the lower resolution.

It stays at 720p with the SDK's scene reconstruction (`fh1_upscaler`: FSR 3, DLSS, SMAA), separate
scene anti-aliasing or ultrawide gameplay, which find the scene by its 720p targets (logged).

**The race-load crash (fixed).** With the first version, signing up for the festival race at the
spawn and starting it crashed while the race loaded (3 of 3): a null read in the pool of 1028-byte
stream buffers (`sub_82D78310`, TU4 `sub_82E3A2E8`, returning to `82E3CBA8`): its free list ended
early. A temporary hook that walked the list found it cut within 50 ms of the pool's set-up for the
race, with 2:10:10:10 pixels (`C0100401`: dark, full alpha) in the links of free buffers 130 to 560.
The pool lies in the file heap right after the screen texture block, which the 540p sizes had made
6.5 MB smaller. The full-screen filter's 1280x720 pass (item 6) resolved into the 960x540 `Shared`
texture in `WorldDepth`, the second-to-last texture of the block, and ran past the block's end into
the pool (`readback_resolve = some` copies resolved data back into guest memory). Item 6 was meant
to stop that pass but looked for the texture among the renderer's slots instead of the D3D textures
after them, so it never applied. The smaller memory also put `MapRender` (1280x720 in a 960x540
`WorldDepth`) past the block's end, and the three 1024x1024 depth targets over `CSMBuffer`, which a
race draws in the same frames. Fixed by keeping every texture's 720p memory (item 2) and finding the
D3D textures in item 6. Earlier attempts: 720p sizes for `sub_82A40F28` alone gave 720p headers
(garbled picture); patching the texture objects after `sub_8257C220` (720p memory, 540p sizes) did
not crash, but in some states the scene came out 1:1 in the lower right 960x540 of a 720p target.

**360p** (640x360, 4x MSAA in one pass) came out magnified with blown-out rectangles and magenta,
and after about 10 s of driving the render thread looped in the allocator's list walk
(`sub_82D78490`): the same kind of corruption, sooner. The game has textures of its own at 640x360
that a 640x360 scene collides with. Not offered, and not tried again with the 720p memory.

**Measured** (TU4 on this PC's RX 9060 XT, RADV; a copy of the Xenia save, warm scratch shader
cache, offline, audio muted, 1920x1080 window with FSR 1; BENCH lock held, no other game; GPU-side
plugin from the 2026-10-23 overlay). Each run: free roam, 20 s standing at the spawn, then 15 s at
full throttle; guest fps from the `RECOMP_PERF` CSV, GPU time from the game's DRM client
(`drm-engine-gfx` in fdinfo). A GPU-limited PC is emulated with render scale 3 uncapped (9 host
pixels per guest pixel, the GPU 99% busy): the per-pixel work grows as on a GPU 9 times weaker,
the per-draw work does not. Two runs each at scale 3, alternating; mean of standing / driving:

| Render scale, frame rate | Scene | MSAA | fps (stand / drive) | GPU ms a frame | GPU busy |
| --- | --- | --- | --- | --- | --- |
| 3, uncapped | 720p | 4x, three tiles (game) | 34.9 / 35.0 | 28.4 / 28.3 | 99% |
| 3, uncapped | 540p | 2x, one pass | 50.3 / 50.5 | 19.6 / 19.6 | 99% |
| 3, uncapped | 720p | none (`scene_msaa = off`) | 42.1 / 42.5 | 23.5 / 23.3 | 99% |
| 3, uncapped | 540p | none (`scene_msaa = off`) | 54.1 / 53.4 | 18.3 / 18.5 | 99% |
| 1, uncapped | 720p | 4x, three tiles | 144.9 / 158.3 | 5.64 / 5.20 | 82% |
| 1, uncapped | 540p | 2x, one pass | 203.0 / 210.3 | 4.06 / 3.94 | 83% |
| 1, 30 fps | 720p | 4x, three tiles | 30.0 / 30.0 | 6.86 / 5.93 | 21% / 18% |
| 1, 30 fps | 540p | 2x, one pass | 30.0 / 30.1 | 6.16 / 6.76 | 18% / 20% |

- GPU-limited: 540p gives +44% fps (35 to 50) and 31% less GPU time a frame, of which the smaller
  scene alone (both without MSAA) is +28% fps and 22% less GPU time; the rest is 2x MSAA in one
  pass instead of 4x in three tiles. The HUD, post-processing at 720p and the FSR 1 pass do not
  shrink.
- At render scale 1 uncapped the frame rate rises 33-40% (145-158 to 203-210 fps).
- At 30 fps the GPU clocks down with the load, so its time a frame says nothing here (an integrated
  GPU that cannot hold 30 is the case above).
- Not measured on a real integrated GPU (none here; this PC's CPU graphics are off) or a Steam Deck,
  which is limited by the command processor's per-draw work (#45): one pass instead of three tiles
  also takes most of the tiles' CPU work away (as `scene_msaa = off` does), which may help it more
  than the pixels.
- Screenshots (scratch, not in Git): 720p and 540p side by side at the spawn, driving, bumper and
  cockpit cameras, pause menu, map, photo mode, event and car select screens: the scene is slightly
  softer, the HUD text is the same.

## Flush pending Vulkan shader-cache writes before waiting (#49)

Patch 0064 completes a pending stdio flush before the storage writer sleeps,
and drains shader/pipeline records queued before shutdown. Without it, a final
flush request could consume its notification and go to sleep with buffered
records, so hard exits left the next run without those cached states.

The Windows regression harness executes the actual StorageWriteThread body with
real files, a worker thread, one queued shader and pipeline, then 64 pipeline
records queued immediately before shutdown. Before: the one-second flush check
fails and shutdown drops the queued records. After: both checks pass, including
all 65 pipeline records. This is a cache-persistence fix, not an FPS claim or a
fix for every freeze in #49. No GPU-specific commands or policies change; AMD
runtime validation remains with the maintainer. The ordered standalone SDK
patch stack applies to bd833a2.

## NVIDIA Vulkan resolve-readback overhead (#49, 2026-10-15)

SDK patch 0065 avoids repeating GPU readback work in `readback_resolve=some`
after that resolve key has a valid CPU copy. The regular GPU resolve and
cross-scale synchronization still run. The first CPU copy is unchanged;
the validity flag is set only after the guest-memory copy succeeds. Keys include
the address, byte length and format signature. Reused entries still update their
last-used frame, and eviction discards the flag along with the buffers, preserving
the existing refresh-after-inactivity behavior. `fast` and `full` are unchanged.

Previously `some` skipped the repeated CPU `memcpy`, but still dispatched the
downscale, copied into host buffers, and inserted transfer/host barriers on every
resolve. This was substantial overhead on the tested NVIDIA system. Disabling
readback entirely is a diagnostic control, not the fix: it removes CPU results
needed by car-picture generation.

RTX 5060 Ti, driver 617.14, Windows 11, Vulkan/FIFO, 120 FPS cap, 1440p output,
16x AF and warmed persistent shader storage. No other game or build ran during
measurement. Each run used the same copied save and the festival throttle sequence
described below. These are short scene tests, not race-wide performance claims.

| Native 1440p scene and HUD | Guest FPS | Renderer p99 (ms) |
| --- | ---: | ---: |
| Before: `some`, incomplete-frame skipping on | 52.9 | 22.8 |
| Before: `some`, incomplete-frame skipping off | 52.4 | 23.4 |
| Diagnostic: readback `none` | 68.1 | 18.3 |
| Fixed: readback `some` | 68.8 | 18.2 |

The table uses seconds 30-54. A new pipeline still caused a roughly 185 ms hitch
near second 30 in the fixed native run; improving readback does not eliminate
first-use shader compilation. The measured interval had zero renderer gaps over
50 ms after the fix versus four before it. Do not treat this as all of #49 being
resolved, or as a fix for missing story cutscenes or broken car thumbnails (#50).

The DLSS Performance compatibility check retained 1280x720 scene rendering,
native 2560x1440 HUD and the 120 FPS cap. A before/after repeat measured
81.4 FPS / 17.0 ms renderer p99 before, versus 117.6 FPS / 12.7 ms afterwards.
The first fixed run measured 118.5 FPS / 12.1 ms. Neither fixed measurement had
renderer intervals over 25 ms in seconds 30-54. Both reported active NGX and
native HUD rendering without evaluation failures. The installed renderer was
updated only after building and validating the fix; the previous DLL and matching
capability metadata were backed up. The patch also applies independently on the
public main SDK patch series, without depending on the DLSS work.

The table above is the earlier integration measurement, retained with its
original method and limits; it is not a fresh measurement of this split branch.
The standalone patch applies to main without DLSS, ultrawide or thumbnail changes.
The readback key, first successful CPU copy and eviction lifetime are unchanged.

## NVIDIA readback cache-miss stalls (#49, 2026-10-16)

The redundant-transfer fix does not remove the first CPU copy after a readback
cache miss. A warmed Horizon Heats race still produced intermittent long frames.
A diagnostic run with `readback_resolve=none` removed these spikes, without
improving average FPS. Disabling readbacks is not the fix: car-picture generation
requires CPU results.

Private timing scopes identified repeated 16 KB resolve readbacks at cache misses.
The entries had been evicted after 61 inactive frames. The measured slow calls
spent about 33-35 ms in `CheckSubmissionFenceAndDeviceLoss`, under
`IssueCopy_ReadbackResolvePath`; these were not thumbnail overrides or new pipeline
compilations. That wait was added to the normal frame workload.

Patch 0069 uses two queued frames by default on NVIDIA, limiting CPU lead before
such a wait. It keeps the three-slot resource ring and the existing fence-based
retirement; AMD and other vendors retain the previous three-frame default. The
copy, its first-result synchronization, readback keys and cache lifetime are
unchanged. `vulkan_max_frames_in_flight=3` restores the previous queue depth;
positive values are clamped to 1-3 and nonpositive values select the vendor
default. Restart after changing this option. Queue depth 1 is exposed for
diagnosis, but was not benchmarked here.

RTX 5060 Ti, driver 617.14, Windows 11, Vulkan/FIFO, Windows set to 2560x1440
at 360 Hz, native 1440p scene and HUD, 16x AF, 120 FPS cap, DLSS off. The comparison
uses main `75149f1` (including the shadow and readback fixes), a copied save,
eight drivers in Horizon Heats, and the same scripted road route. Each row is one
60-second measured driving interval after a race warm-up. No build or other game
ran during measurement; screenshots were taken outside the interval. AI positions
and traffic interactions vary, so these are route tests, not identical frames.
All measured intervals created zero new pipelines.

| Variant | Presented FPS | p99 (ms) | Worst frame (ms) | Frames over 50 ms |
| --- | ---: | ---: | ---: | ---: |
| Main, three-frame queue | 59.35 | 22.04 | 51.20 | 7 |
| Diagnostic: readbacks disabled | 57.91 | 21.89 | 23.75 | 0 |
| Two-frame prototype | 58.74 | 21.79 | 38.40 | 0 |
| Patch 0069, NVIDIA automatic default | 59.00 | 21.56 | 35.13 | 0 |
| Same patch binary, forced three-frame queue | 59.49 | 21.61 | 54.50 | 11 |

The earlier three-frame race pass measured 59.32 FPS, a 54.85 ms worst frame and
11 intervals over 50 ms. The queue change reduces the long waits; it does not
eliminate every frame over 25 ms or make native 1440p reach 120 FPS. Validation
is limited to this NVIDIA system. Other vendors' default queue depth is unchanged,
but no AMD hardware was tested here. The timing scopes and captures are private
diagnostic files, not part of the SDK patch.
## Scaled texture upload cost (#101)

Issue #101 reports about 40 FPS at render scale 2 on an RTX 4060, even when
lowering output resolution or anisotropic filtering. Scale 2 still renders the
game at 2560x1440 when the presentation window is lowered to 1920x1080. Output
resolution alone therefore does not reduce this rendering or texture-upload work.

On an RTX 5060 Ti (driver 617.14, Windows 11, desktop 2560x1440 at 360 Hz), a
copied TU4 save parked in free roam was GPU-bound at scale 2. The 120 FPS cap,
DLSS off, 16x anisotropic filtering, Vulkan, and native 1440p scene/HUD were held
constant. Initial controls measured 119.7 FPS at scale 1, 59.8 at scale 2, 60.3
with scale 2 and 1080p output/4x filtering, and 58.8 with IMMEDIATE presentation.
Those four measurement windows compiled no new pipelines. Changing the cap to
60 also measured about 59 FPS. This did not reproduce a fixed 40 FPS limit.

A three-frame RenderDoc capture attributed 13.34 ms of 47.68 ms of GPU command
time to buffer-to-image copies. The scaled loader first untiled data into a
scratch buffer, then copied it into the sampled image. Patch 0071 fuses those
steps for eligible NVIDIA textures, writing identical bits through an R32_UINT
storage view. It does not change render scale, texel format, HUD resolution,
texture filtering, frame pacing, or CPU readbacks.

The final comparison used main `a428cec` plus 0071, the same renderer binary
with `vulkan_direct_scaled_texture_upload` false/true, the same copied save,
scripted menu input, and physical controller/keyboard driving excluded by a
private test harness. Each session warmed for 70 seconds and measured for 45:

| Upload path | Presented FPS | p95 frame time | p99 frame time | Worst frame |
| --- | ---: | ---: | ---: | ---: |
| Original scratch buffer + copy | 59.68 | 19.99 ms | 21.38 ms | 41.71 ms |
| Direct storage image | 68.89 | 17.50 ms | 18.56 ms | 35.60 ms |

That is a 15.4% FPS gain in this scene. The original measurement compiled no new
pipelines; the direct measurement compiled two at about 77 seconds. Taking the
same final 30-second window (85-115 seconds) in both runs, with no new pipeline
compilations, gives 59.73 versus 68.97 FPS, and p99 21.36 versus 18.20 ms. These
are local measurements on a 5060 Ti, not confirmation of the reporter's 4060
configuration. Keep #101 open until that configuration has been retested.

The path is limited to tiled, scaled, single-layer 2D base levels in RGBA8,
packed 10-bit color, or R32 float. Other vendors and texture layouts retain the
original path. Storage format support is checked, allocation/view failures fall
back, and `--vulkan_direct_scaled_texture_upload=false` disables it for comparison.

The opt-in GPU check compares the original compiled loader against the new image
shader for independent X/Y scales 1-7, all four endian modes, aligned and ragged
extents, a source offset, and canary pixels outside the destination rectangle.
All 1,176 cases passed on this NVIDIA device. It needs the optional Python
`vulkan` package and an SDK with the patch series applied:

```sh
python tools/test_vulkan_scaled_texture_upload.py --sdk /path/to/rexglue-sdk
```

The final renderer also reached free roam with official NGX Super Resolution
(1280x720 scene, 2560x1440 HUD/output) and DLAA (2560x1440 scene/HUD/output).
Both features initialized successfully and the captured output was inspected;
these were compatibility checks, not additional performance comparisons.

The GLSL source and its precompiled SPIR-V header are included in 0071; no shader
compiler is required when building the game. To regenerate the shader binary,
compile `texture_load_32bpb_scaled_image.cs.glsl` with glslang (`-V -S comp`) and
convert the resulting little-endian uint32 words into the checked-in header.

## NVIDIA scaled depth uploads (#500, duplicate #523, 2026-10-28)

At a 2560x1440 scene, the scaled loader still converted UNorm24 and float20e4
depth textures into a scratch buffer and copied them into R32 float images.
ReIGlue 0406 extends the existing direct storage-image upload to those depth
formats. It preserves the original conversion bits, including UNorm24 rounding
and float20e4 denormals. Scene/HUD resolution, MSAA, filtering and depth/stencil
render-target transfers are unchanged.

The path remains limited to NVIDIA, tiled scaled resolves, single-layer 2D base
levels and supported storage views. Other vendors/layouts and allocation/view
failures retain the original path. `--vulkan_direct_scaled_depth_upload=false`
disables just this extension; the existing `vulkan_direct_scaled_texture_upload`
switch also disables it.

A local Windows RTX 5060 Ti (617.14) comparison used game `cbd5855` and SDK
`74085fd` plus 0406, the same renderer binary with the new switch false/true,
copied Mustang saves, and the production native FR02 benchmark. Output was set
to 1440p, world/HUD were 2560x1440, Game MSAA, upscaling off, AF2x, soft/high
shadows, AO off, VSync on and cap120. Windows DPI made the host swapchain
3200x1800 in the captures. Each run settled for 15 seconds and measured 60
seconds of guest swaps. Runs were in the background with focus uncontrolled;
AI traffic and collisions vary, so this is not a deterministic replay or a
foreground shipping FPS claim.

| Depth upload | Average FPS | p99 frame time | Worst frame |
| --- | ---: | ---: | ---: |
| Scratch buffer + copy | 74.22 | 15.16 ms | 27.88 ms |
| Direct storage image | 80.56 | 15.56 ms | 44.96 ms |
| Scratch buffer + copy, repeat | 73.63 | 15.64 ms | 28.55 ms |
| Final renderer, default direct upload | 80.59 | 15.03 ms | 25.68 ms |

The first pair gained 8.5% average FPS; it does not establish better tail latency.
All four measurement intervals compiled zero pipelines/shaders and had no
texture evictions or render-target trims. GPU clocks averaged about 2802 MHz.
These results do not reproduce or resolve the reporters' RTX2060Super/3060Ti
performance; keep #500 open for their retests.

Paired three-swap RenderDoc captures at FR02 settling reduced buffer-to-image
event GPU time from 3.918 to 0.201 ms, removing the five R32 depth destinations'
15 copies. Summed GPU event time was 44.505 versus 41.000 ms; scene variation
also changes other work, and these sums exclude idle/barrier/CPU/present time.
The capture host privately retried failed Vulkan instance creation without the
portability-enumeration extension and used non-sparse, non-threaded submission;
it is excluded from ordinary FPS measurements and is not shipped. Both last
presented frames were inspected: world, cockpit, shadows, mirrors and HUD render
correctly. They are different simulation frames, not a pixel-equality test.

The GPU equivalence check now covers both depth formats alongside the three
existing color formats. All 1,960 cases passed against the unmodified compiled
buffer-loader shaders: independent X/Y scales 1-7, four endian modes,
aligned/ragged extents, source offsets, destination canaries, and depth
conversion boundaries (including 0xC00000 UNorm rounding and float20e4
denormals). Run the optional test with the updated SDK:

```sh
python tools/test_vulkan_scaled_texture_upload.py --sdk /path/to/ReIGlue
```

Windows validation rebuilt the changed Vulkan texture-cache translation unit
and linked the GPU plugin at j1 against audited, unchanged SDK objects. No
runtime/header ABI changes are involved. Linux/macOS builds and the reporters'
hardware remain CI/retest coverage.

SDK change: [ReIGlue #11](https://github.com/FH1-ReImagined/ReIGlue/pull/11),
commit `ff1db6f6ff64f237ec2dbd28f06680205a6b8b1a`; the game pin depends on its
review. Regenerating the final GLSL produces exactly the checked-in 2,081-word
SPIR-V header.

## Steam Deck frame collapses (#45, 2026-10-23)

The player's Deck video (0.1.28, Steam overlay on): steady free roam at 28-30 fps, but 10-15 fps near AI
drivers or traffic, dramatic drops on fast camera changes (looking back at the cars behind) and 10-12 fps
with heavy stutter for a while after the pause menu or the map. The overlay showed the GPU 50-74% busy at
700-950 MHz and all eight CPU threads at 35-45%: a few CPU threads, mainly the GPU command processor, set
the frame. This looks at what the command processor does in those moments.

**The simulation and how far it goes.** TU4, `-DFORZA_CPU_TARGET=x86-64-v3`, a copy of the Xenia save, offline,
audio muted, a 1280x800 window (the guest renders 1280x720, as on the Deck), 30 fps, render scale 1, AMD FSR 1,
a warm shader cache (as after *Prebuild shaders*), the game's threads on CPUs 0-3, the samplers and
`tools/autodrive.py` on 4-7; once with one busy loop per core (a game thread gets half a core). Faithful: the
settings, the guest work per frame, the frame cap, four cores. Not: the cores are Zen 4 at 5 GHz with 96 MB of
L3, about 2-2.5 times a Deck core (Zen 2, 2.4-3.5 GHz, 4 MB of L3); this CPU has no SMT
(`smt/control` is `notsupported`), so there are no busy siblings; no CPU and GPU power budget; the GPU (RX
9060 XT, 7.7-8.6 ms of GPU a free-roam frame) is a discrete card with 16 GB. Memory: the Deck's BIOS carve-out
is 1 GB, but RADV shows APUs two thirds of carve-out plus GTT as device-local memory, so the game sees several
GB, and the player's 0.1.30 log has no `Render targets trimmed` line. The runs below use the driver's budget.
The first two runs used the 1 GB test cap (`vulkan_device_local_budget_mb=1024`): no trim and no texture
eviction either; the cap only moved new textures (1,400, 94 MB) and some guest memory pages to system memory
once render targets (540-553 MB) and the rest filled 80-95% of it. What the kernel does with the real 1 GB
carve-out (moving buffers between it and GTT) cannot be reproduced on this card.

Method: the SDK counts what the command processor does in every frame (patch 0180 below; the hidden
`fh1_frame_cost_csv` writes each frame): draws, its CPU time, its waits for the GPU and for the game,
textures created, loaded and evicted, guest memory uploads, shaders and pipelines, render targets, resolve
readbacks. Per-thread CPU from `tools/thread_stats.py`, cars within 30/60/120 m from the car list
(`tools/autodrive.py`'s reader), the command processor's profile from `PERF_CPUPROF`. Scripted: free roam
following the traffic for 40 s, the right stick press (look back) four times while driving, the five
cameras (RB), the pause menu for 8 s, the map for 8 or 40 s, driving again; the 8-car festival race with
three looks back; four map round trips per run.

**What each trigger does to the frame** (command processor, ms of CPU a frame on this CPU; a Deck needs
2-2.5 times that):

| Moment | Draws a frame | Command processor | Anything else |
| --- | --- | --- | --- |
| Free roam, no car within 60 m | 2,865-3,144 | 5.3-5.9 ms | |
| Free roam, 4 or more traffic cars within 60 m | 3,345-3,983 | 6.3-7.2 ms | |
| 8-car race, grid and intro | 4,314 | 7.4 ms | |
| 8-car race, first 15 s in the pack | 5,802 | 9.8 ms | one 51 ms frame at the start (26 ms, 16 ms GPU waits) |
| Looking back (free roam / race) | +0-15% / +7-22% | +0-0.6 / +0.2-1.1 ms | |
| Camera changes | 2,500-3,900 by camera | 4.5-7.1 ms | 0-0.3 pipelines a frame (warm cache) |
| Pause menu | 3,370 (the world goes on) | 5.8-6.2 ms | nothing on return |
| Map | 1,560-1,800 | 2.0-2.3 ms | |
| First frame back from the map | 3,450-4,130 | 29-31 ms | 9-10 resolve readbacks, 23-24 MB, 23-25 ms; 7-11 ms GPU waits; a 50 ms frame |

- Cars cost draws, at the same 1.7-1.8 us of command processor per draw as the rest of the frame: the pack of
  a race adds 70-80% to the frame's draws, traffic up to 30% when several cars are close. None of them brings
  texture creation, uploads (0.5-0.9 MB a frame), pipelines, render targets or evictions. On a Deck that holds
  28-30 fps with the command processor near its limit, every such frame passes 33 ms and waits for the next
  vblank: 20 fps. Most of the draws are the scene's three EDRAM tile passes (one pass is worked on
  separately); there is no per-car overhead of its own to remove.
- Looking back and changing the camera change the draws by the view; with a warm cache no shader, pipeline,
  texture or render target work shows up.
- No render target trim, texture eviction (the texture cache stayed at 171-172 MB, under its 384 MB limit;
  render targets 540-553 MB, 38-39 of them) or CPU texture conversion (texture loads are compute dispatches: about 90 a frame, 0.1 ms to record) in any
  run, with or without the 1 GB cap.
- Back from the map: every scene resolve was copied back to guest memory again. `readback_resolve = some`
  (the default) copies a resolve the first time its key is seen and drops the key after 60 unused frames;
  after the map (8 s or 40 s) all of the scene's were first-seen again: a GPU wait, a new readback buffer and
  a copy each. Fixed by 0181 (below). The pause menu draws the world behind it, so nothing is dropped there.
- Not reproduced: a lasting slowdown after the map. Here the frame returns to 30 fps (5.6-6.6 ms of command
  processor) right after that first frame, and the threads' CPU time is what it was before the map (61-67% of
  a core in all; the per-second samples show no burst of the game's own threads). On the Deck the likely
  causes of a slowdown that lasts seconds are outside these counters: buffers moved between the 1 GB carve-out and GTT after the map's
  textures and render targets pushed the world's out, or the GPU clock coming back up after the light map
  screen. The log line below now tells whether the command processor did extra work in those frames.

**Change 1: SDK patch 0180, why a frame was slow, in the log.** From 30 s after the first frame, frames of at
least 50 ms and 1.5 times the typical frame are collected for 10 s, then one warning names what they did on
average and the largest cost (at most 60 such lines, then one a minute):

```
GPU slow frames: 7 of 296 in 10 s over 50 ms (worst 133 ms, typical 33 ms); each: command processor 8 ms CPU,
3 ms GPU waits, 6 ms game waits, 1179 draws (typical 3200); textures 14.6 new (4.2 MB, 0.0 ms), 54.3 loaded
(26.4 MB, 0.1 ms), 0.0 evicted (0.0 MB); uploads 3.3 MB (1.4 ms); shaders 0.0 (0.0 ms), pipelines 0.0 (0.0 ms);
render targets 0.9 new (10.3 MB, 0.0 ms), 0 trims; resolve readbacks 7.2 (3.3 MB, 3.4 ms, 3.1 ms GPU waits);
cause: the game (the command processor waited for its commands)
```

(that one is a race loading screen). Causes: `more draws` (the draws above the typical count at the typical
cost per draw), `textures`, `guest memory uploads`, `shaders and pipelines`, `render targets`, `resolve
readbacks`, `waits for the GPU`, `the game` (the command processor was mostly idle: CPU and GPU waits under half
the frame, so the game's own threads were late; `FORZA_STALL` has their call chains when it took 200 ms), or
`unclear`. The CPU time is the thread's own (`CLOCK_THREAD_CPUTIME_ID`; 0 on Windows), so time spent waiting
for a CPU on a busy Deck shows as a frame longer than its CPU time. The counters are increments on the
command processor thread; clocks are read only around the rare paths. `tools/test_frame_cost.cpp`.

**Change 2: SDK patch 0181, the scene's resolves are not copied back again.** A readback key resolved in 150
frames in a row is a scene render target; when its buffer was dropped and it comes back, it counts as copied
(no GPU wait, no copy). Car pictures already copy every time (their resolves are recognised), photos are a
key seen once, and keys made only when the world starts (four 2 MB depth resolves) still copy.
`tools/test_readback_history.cpp`. Four map round trips (8 s in the map) per run, two runs each, alternating,
BENCH lock held:

| Build | Worst frame back from the map | First world frame: command processor | Its GPU waits | Copies back |
| --- | --- | --- | --- | --- |
| 0180 only (`vulkan_readback_skip_render_targets=false`) | 50-51 ms (8 of 8) | 28.8-31.0 ms | 6.7-10.7 ms | 9-10, 23-24 MB |
| 0180 and 0181 | 34 ms (8 of 8) | 14.3-16.4 ms | about 3 ms | 4, 7.8 MB (6 skipped) |

With the map open for 40 s the same: 34 ms. The world renders the same; the copies only fill guest memory the
game's CPU does not read for these targets ("some" left them stale after their first copy already).

**For a Deck player to confirm:** a `forza.log` of a session on a build with 0180 that goes near traffic, into
a race, looks back and opens the map: each collapse leaves `GPU slow frames` lines with the cause. If the drop
after the map still lasts seconds with `cause: waits for the GPU` or `the game` and little command processor
CPU, the time is outside the emulation's work (memory moves or clocks); a run with the BIOS UMA frame buffer
at 4 GB instead of 1 GB, and Steam's overlay at level 4 showing the GPU clock right after leaving the map,
would tell which.

## Long sessions (#45, 2026-10-23)

**The reports.** Steam Deck players: "the first 10 minutes are fine, but then it just keeps getting worse"; earlier
"better at first, then back to 15 fps after a few minutes". A BC-250 player's MangoHud (0.1.31, Linux): GPU 56% at
1850 MHz, CPU 25% with six threads at 31-38% and their siblings at 11-16%, 40 fps on average, 1% low 17 fps: no
GPU or CPU thread is full, so the frame waits on a chain of threads (as above). The question here: does anything
grow with play time and make each frame cost more?

**The soak.** As in [Steam Deck frame collapses](#steam-deck-frame-collapses-45-2026-10-23): TU4,
`-DFORZA_CPU_TARGET=x86-64-v3`, a copy of the Xenia save, offline, audio muted, a 1280x800 window, 30 fps, render
scale 1, AMD FSR 1, a warm scratch shader cache, the game's threads on CPUs 0-3 and everything else on 4-7, the SDK
of main (with 0140, 0180, 0181) plus 0200 below, BENCH lock held, no other game. The car drives itself
(`tools/autodrive.py` with `AD_PATH_LOAD`) round a 28.5 km loop that `tools/nav_route.py` lays on the game's own
road graph from the save's spawn: county roads, Gladstone's streets, Interstate 76, Route 29, Interstate 70 and
the town on Route 29, with the traffic, about 38 minutes a lap. Recorded: every frame the command processor's
counters (`fh1_frame_cost_csv`), every second each thread's CPU and run-queue time and the GPU time
(`tools/thread_stats.py`), every 30 s the process's memory (resident, mappings, the guest memory file's resident
pages, the DRM client's VRAM and GTT), and once a minute the new summary line (0200). Two runs at 30 fps (60 and 45
minutes) and one without the frame cap (45 minutes).

**Per minute** (60-minute run; the minute that ends there, counted from free roam; command processor = its CPU
time a frame, in brackets fitted to 3,000 draws over the minute's frames, since the draws follow the view; the
45-minute run gives the same, within the minute-to-minute spread):

| Minute | fps | p99 ms | 1% low | Command processor ms (at 3,000 draws) | Draws | GPU ms | Main loop, render thread, mixer % | Textures (MB) | Evicted/s | Watch nodes a frame | Readback keys | RSS MB | VRAM MB | VMAs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 30.0 | 34.0 | 29.2 | 5.80 (4.99) | 3566 | 7.6 | 7.4, 11.3, 9.4 | 3025 (189) | 0.0 | 3902 | 133 | 1169 | 1097 | 2642 |
| 5 | 30.0 | 33.9 | 29.3 | 4.45 (4.94) | 2639 | 7.4 | 7.0, 9.5, 8.6 | 4473 (238) | 0.0 | 3817 | 144 | 1204 | 1361 | 2416 |
| 10 | 30.0 | 33.9 | 29.4 | 4.48 (5.16) | 2527 | 6.8 | 6.7, 8.7, 9.3 | 5599 (286) | 0.0 | 3843 | 154 | 1213 | 1375 | 2317 |
| 20 | 30.0 | 34.1 | 29.2 | 4.75 (4.95) | 2868 | 6.5 | 7.2, 10.0, 9.3 | 8137 (384) | 0.4 | 3815 | 167 | 1240 | 1375 | 2853 |
| 30 | 30.0 | 34.0 | 29.2 | 5.15 (4.86) | 3178 | 7.0 | 7.7, 9.8, 9.5 | 8000 (384) | 5.4 | 3809 | 163 | 1250 | 1382 | 2671 |
| 40 | 30.0 | 33.9 | 29.4 | 5.60 (5.32) | 3176 | 7.9 | 7.2, 10.2, 11.6 | 8028 (384) | 2.0 | 4000 | 168 | 1256 | 1386 | 2821 |
| 50 | 30.0 | 34.1 | 29.2 | 4.55 (5.05) | 2651 | 7.5 | 7.8, 8.1, 8.9 | 7821 (384) | 6.4 | 3856 | 179 | 1259 | 1387 | 2965 |
| 60 | 30.0 | 34.2 | 29.2 | 5.51 (4.94) | 3346 | 7.1 | 9.1, 9.0, 11.5 | 8093 (384) | 3.7 | 3867 | 183 | 1261 | 1387 | 2783 |

- **Nothing that costs per frame grows.** The frame holds 30 fps, p99 34 ms and 1% low 29 from minute 2 to 60; the
  command processor needs 4.9-5.3 ms for 3,000 draws in every 10-minute block of both runs (1.6 us a draw); the GPU
  6.5-7.9 ms a frame; the main loop, render thread and audio mixer the same share of a core. The writes to guest
  memory walk the same 3,800-4,000 watch nodes a frame (about 100 writes of 38 nodes each) whether the texture
  cache holds 3,000 textures or 8,000.
- **What grows, and stops:** the texture cache, from 3,000 textures (190 MB) at minute 2 to its 384 MB limit
  (`texture_cache_memory_limit_soft`) at minute 19-20 with 8,000 textures; after that it evicts 2-14 textures a
  second that were unused for 30 s and makes about as many: 0.17 new textures a frame before and after, 0.05 ms of
  the command processor's frame for texture creation and loads in both halves. With it the GPU memory (1,097 to
  1,387 MB device-local) and the process's resident memory (1,168 to 1,260 MB, half of it the guest's own pages),
  both flat after minute 20-30. The readback keys of 0181 go from 133 to 183 (kept at most 2,048, forgotten after
  30 minutes). Render targets (39, 553 MB), shaders (907-913), pipelines (812-825), framebuffers (61-63), watched
  ranges (1,200-2,000), readback buffers (85-105, 55-85 MB), the guest's heaps (206 MB virtual, 360 MB physical)
  and the threads (76) stay as they are from the start; the memory mappings move between 2,200 and 3,200 without a
  trend; the open file descriptors climb from 125 to 291, slower and slower.
- One run changed its rhythm at minute 41: the render thread woke 2,400 times a second instead of 9,800 and the
  main loop 830 instead of 100 (as if the two had swapped which one waits for the other), with the same frame times
  and CPU time to the end. The other run never did it; it is a phase of the two threads, not something that grew.

**Without the frame cap** (`--frame_rate=240`, the same four cores and route, 45 minutes, BENCH lock held): the
command processor is busy 77-80% of the time and the game's waits are 0.7 ms a frame, so the frame rate is its
speed: 145-163 fps minute by minute, following the draws. Fitted to 3,000 draws, a frame takes 6.52, 6.48, 6.37,
6.46 and 6.62 ms in the five 10-minute blocks (153, 154, 157, 155 and 151 fps; the last, 5 minutes long, is the
place on the route where all three runs drew their dearest frames), and the command processor 5.04, 4.99, 4.93,
5.00 and 5.23 ms. Five times the frames of the capped runs, and the same sizes at the same minutes: the texture
cache full at minute 15-20, the resident memory 1,198 to 1,256 MB, the readback keys and file descriptors as
above. What grows does so with time and place (the world streamed in), not with the frames drawn.

So on this PC the emulation's work per frame does not depend on how long the game has run. What a Deck adds and
this PC cannot show: the texture cache's growth to 384 MB in the first 20 minutes is GPU memory that a Deck with
the default 1 GB UMA frame buffer does not have in its carve-out (render targets and the guest memory's GPU copy
fill most of it from the start; with 0138 new textures then go to system memory), and a 15 W chip that gets hotter
and slower as it runs. A frame of the same work then costs more without anything growing.

**Change: SDK patch 0200, a minute's summary in the log.** One info line a minute from the command processor
(`docs/sdk_patches.md`; minute 30 of the 45-minute run, counted from the first frame):

```
GPU minute 30: 30.0 fps (1% low 29.3, worst 35 ms); a frame: command processor 4.4 ms CPU (1.68 us a draw), 0.0
ms GPU waits, 28.5 ms game waits, 2598 draws, 0.60 MB uploads, 0.0 resolve readbacks, 3784 memory watch nodes
checked (100.6 writes); a second: 3.0 textures new, 2658.1 loaded, 3.6 evicted, 0.0 pipelines new; now: textures
7939 (384 MB), render targets 39 (553 MB), shaders 916, pipelines 830, framebuffers 63, memory watches 1267
(1285 nodes), readback buffers 97 (62 MB, 182 keys), guest memory 206 MB virtual + 360 MB physical, GPU memory
1382 of 15577 MB device-local, 127 MB system, process 1241 MB resident; clocks: CPU up to 4.97 GHz, GPU 1.01 GHz
39 C 30.0 W
```

How to read a long session's log: the fps and 1% low minute by minute show when it got worse. Then:

- `draws` up: the view has more to draw (traffic, a town, a race), not play time.
- `us a draw` up with the same draws, and `CPU up to` lower: the CPU got slower (heat or the power limit); the
  GPU's clock, temperature and power (on Linux, the amdgpu hwmon: on a Deck the whole chip's power) say which.
- `game waits` down and the command processor's CPU the same: the game's own threads are later.
- A size that keeps climbing (textures past 384 MB, render targets, pipelines, readback buffers, guest memory, the
  process's memory) and frame costs with it: something on our side grows; that line pair is the report.
- `GPU memory ... device-local` at its budget and `system` growing: the GPU's memory is full.

**For a Deck player to send:** a `forza.log` of one session of 30 minutes or more on a build with 0200, from start
to quit, and when it got slow (the minute lines are timestamped). If the minutes that got slow show `us a draw`
up and the clocks down, the same session with the Deck kept cooler (a cooler room, the fan not blocked) would
confirm heat; if the GPU memory is full, the same session with the BIOS UMA frame buffer at 4 GB, or with
`texture_cache_memory_limit_soft = 192` in `forza.toml` (a smaller texture cache: unused textures go after 30 s
once it holds 192 MB), would tell whether memory placement is the cause.


## HDD write amplification (#443)

The issue attachment `forzalog.zip` contains a 150,249,543-byte `forza.log`,
2026-10-25 20:27:05.214 through 21:08:21.135 (timestamps as logged). Counting
records by severity yields 1,197,547 lines, including 1,193,642 debug records:
896,546 XMA unknown-register messages and 125,297 PRESENT messages. The SDK
uses synchronous spdlog loggers and a buffered basic file sink; its default
flush threshold is info, with periodic flush disabled. Debug records do not
individually force a flush. The log does not record actual filesystem write
calls, flush settings or disk queue occupancy, so bytes and message counts
cannot establish the physical HDD workload. The maintainer has already asked
for an info-level retest; that result remains necessary.

Independently, the log records 22 successful driver-cache saves totaling
11,795,138,021 bytes (11.8 GB), retaining 252 pipelines in files between
536,063,947 and 536,204,779 bytes. Fourteen successive saves have the same byte
count as their predecessor. Equal lengths alone do not prove equal contents.
Source inspection identifies a redundant-write path: adding any pipeline
increments the global driver-cache generation, including pipelines omitted by
the 512 MiB retained-subset budget. The next storage-thread save rewrites the
whole retained subset even when none of its entries changed. Writes use a
temporary file, stream flush and atomic replacement; on Windows replacement
uses MOVEFILE_WRITE_THROUGH. They are separate from debug log writes.

Patch 0342 compares the retained subset with the last successful save before
opening a temporary file. It compares identity, destination, ordered keys,
fresh sizes and immutable payloads (byte comparison if a pointer changed, even
when checksums match). Weak references avoid extending payload lifetimes.
Unchanged subsets acknowledge the captured generation without I/O. Failed
writes retain the successful snapshot and retry later. Startup/reset begins
with no snapshot; the on-disk format and restart reads are unchanged, as are
0320's 512 MiB cap, smaller-entry packing and atomic replacement. Only the
serialized storage writer uses the state; startup saves precede its creation,
shutdown joins it before the final save/reset, and pipeline producers still
publish under the existing mutex. A newer generation remains dirty when a
save acknowledges an older captured generation.

The log selects a GTX 1650 with about 4 GB device-local memory, although the
report lists an RTX 5060 (both are enumerated). At minute 40 it reports about
1541 MB device-local of a 3557 MB budget, 245 MB system GPU memory, 384 MB
textures, 572 MB render targets, 32 MB readback buffers and 273+360 MB guest
memory. Readback stalls occur in some windows, but these records do not show
exhausted VRAM or measure host process memory/paging. Streaming can compete
with the writes on an HDD; the log does not measure those disk reads. There
is no disk trace or controlled game reproduction establishing which events
caused the reporter's hitches. This change fixes the demonstrated redundant
save path; it does not claim the entire issue is resolved.

Validation on Windows: `tools/test_driver_pipeline_cache.cpp` compiled against
an isolated SDK snapshot at bd833a2ab69d305f99ddf57418fc38ffec8af93d with all 124
public patches applied. It passes bounded omitted-only additions, same-size
changes, forced checksum collision, changed key/metadata, displaced entries,
smaller-entry packing, separately loaded identical data, destination/identity
changes, reset, weak payload lifetime, failed replacement/retry and concurrent
readers, plus the existing corruption, replacement and six-start reuse model.
A negative control disabling the unchanged fast path fails the omitted-only
assertion. A private harness compiled the exact patched
`SaveDriverPipelineCache` body and passed generation acknowledgement/no-write
and denied Windows replacement/retry checks. Initial validation did not include
a full GPU plugin/game build, HDD trace or game run.


Follow-up Windows build validation: a private reconstruction of the pinned SDK
plus all 124 public patches, with exact local gitlink copies and the carried
SDL patches, configured with Clang 23.1.3, Vulkan/D3D12, DLSS, FSR3, Tracy and
performance counters enabled. The three affected Vulkan translation units
compiled first. The remaining `rexgpu-xenos` target then compiled and linked
with ordinary Ninja `-j1 -k1` in 361 seconds, within a 20-minute limit. The
matching `rexruntime.dll` linked too; the Ninja graph and all 1369 tracked SDK
source hashes were unchanged. Static AMD64 PE inspection found the required
`rex_gpu_abi_version` and `rex_gpu_create` exports plus the two existing GPU
selection hints, and the plugin imports `rexruntime.dll`. Its DLSS capability
manifest matches its SHA-256. No DLL was loaded, no shared prefix installed,
and no game, persistence/restart reuse or HDD stutter validation was performed.

## Steam Deck: ideas from Pinyon Shift (#559, #476, #475, #474)

Three ideas from a read-only study of Pinyon Shift (BSD-3; same executable as ours) aimed at the thread the
frame waits for on a Deck, the GPU command processor ([the benchmark](#steam-deck-and-4-core-cpus-the-built-in-benchmark-45-2026-10-23)).
Each is a ReIGlue commit behind a cvar that defaults to off; credits are in ReIGlue's `NOTICE` and in the commit
messages. This section holds the measurements. No Deck was available.

**Machine and method.** Ryzen 7 7800X3D (8 cores, no SMT, 5 GHz), RX 9060 XT (RADV). The game on CPUs 0-3
(`taskset -c 0-3`, set once; the samplers are not pinned), the launcher's Steam Deck preset
([steam_deck.md](steam_deck.md)): 30 fps, render scale 1, AMD FSR 1, `scene_msaa=off`, 720p scene, background shader
compilation; the TU4 build of main with a ReIGlue plugin and runtime built from the branches below, the golden TU4
save, audio muted, offline, scratch `--user_data_root`, warm scratch shader cache. Scenes: the built-in benchmark
(Recaro Rush, 15 s settling and 60 s measured, [benchmark.md](benchmark.md)) and a free-roam drive (90 s at full throttle from the spawn). Because four
5 GHz Zen 4 cores hold 30 fps easily (the command processor is busy about 15 % of the frame), two harder
configurations stand in for the Deck's slower cores: **uncapped** (`--frame_rate=240`: the frame rate then
follows the CPU side) and **busy loops** (one busy loop per core at the same priority, so a game thread gets about
half a core, "p4h1" above). These show how the time divides; they are not the Deck's frame rate.

**In-run alternation.** The machine was shared with other jobs (builds and other benchmarks, which took the benchmark lock
in turn), and the benchmark's race is not the same twice (the AI cars are not deterministic: the fps over the minute
differs by 15 % from run to run). So the cvar is flipped while the game
runs (hot-reload cvars: #476 through the cvar's storage in the running process, a test aid; #475 through `REX_THREAD_CORES_FILE`), every 3-4 s over
the benchmark's measured minute, and the table pools the windows: frame times of all "on" windows against all "off"
windows of the same runs, and the paired change of each "on" window against the "off" window next to it. Runs were
started with the opposite state first in turn. The first 0.5 s of each window is left out.

### Present without waiting for the frame's replay (#476, ReIGlue 0414)

Every swap ended the frame's submission and then waited for the submission thread to replay the whole frame
(`AwaitSubmissionThread`), because the presenter's step after the swap (making the image the next one to present,
painting it, the VR export, the fence that tracks the refresher's use of the image) queues work that reads the image
and must follow the frame on the queue. The command processor sat idle for that replay, about 1.3-1.4 ms a frame on
this PC (the "Vulkan submission" row of the thread table above).

`--vulkan_present_on_submission_thread=true` (hot reload, default off) queues the presenter's step as a job on the
submission thread behind the frame's submission; the next swap waits for it only if it has not run, which a frame
later it nearly always has. Shutdown and a switch-off wait for the queued steps first. The frame tag of the swap
(`rexui_SetNextGuestOutputTag`, which the command processor clears as soon as the swap returns) is captured when the
refresh is made. The change is contained in `IssueSwap`, the presenter's `RefreshGuestOutput` (split into the
refresh and `PublishGuestOutput`) and the Vulkan presenter; D3D12 is untouched.

**Results (#476).** In-run alternation over the benchmark's measured minute (free roam: over a 90 s drive at full
throttle from the golden save's spawn, from 10 s on), the cvar flipped every 3 s (9 windows of each state per run in
the benchmark), Deck preset, CPUs 0-3, off against on. Rows are the pooled frames of all windows of all runs;
the last row of a group is the change of each "on" window against the "off" window next to it. Command processor =
its CPU time per frame (`fh1_frame_cost_csv`); it does not change, the gain is the time it no longer waits.

| Configuration | State | Runs | fps | p99 ms | 1% low fps | Frames over 50 ms | Command processor ms/frame |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 cores, uncapped (`--frame_rate=240`) | off | 4 | 147.8 | 9.4 | 98.4 | 0 | 4.66 |
| | on | 4 | 169.2 | 8.4 | 105.8 | 0 | 4.68 |
| | paired | 68 pairs | **+15.3 %** (67 of 68 pairs higher) | | | | -0.2 % |
| 4 cores, a busy loop on each, 30 fps | off | 4 | 29.9 | 40.7 | 21.5 | 5 | 4.68 |
| | on | 4 | 30.0 | 41.9 | 21.2 | 5 | 4.65 |
| | paired | 68 pairs | +0.1 % (39 of 68) | | | | +2.7 % |
| 4 cores, 30 fps | off | 2 | 30.0 | 36.1 | 27.0 | 0 | 4.88 |
| | on | 2 | 30.0 | 36.1 | 26.5 | 1 | 4.82 |
| | paired | 34 pairs | -0.0 % (14 of 34) | | | | -1.0 % |
| Free roam (90 s of driving), 4 cores, uncapped | off | 2 | 219.6 | 7.5 | 89.5 | 0 | 2.74 |
| | on | 2 | 226.2 | 7.0 | 110.9 | 0 | 2.74 |
| | paired | 48 pairs | **+3.6 %** (35 of 48 pairs higher) | | | | +0.4 % |
| Free roam, 4 cores, 30 fps | off | 1 | 30.0 | 40.1 | 23.2 | 1 | 2.69 |
| | on | 1 | 30.0 | 38.4 | 22.8 | 2 | 2.63 |
| | paired | 24 pairs | +0.0 % (12 of 24) | | | | -1.7 % |

With the game's own 4x MSAA (`scene_msaa=game`, three EDRAM tiles; the same three configurations, run earlier while
another job still used the machine): uncapped 115.5 against 127.2 fps (+10.3 %, 60 of 68 pairs higher), with busy loops
29.9 against 29.9 and at 30 fps 30.0 against 30.0 (the cap holds), 1% low 18.9 against 20.5 and 25.6 against 27.5 fps,
frames over 50 ms 6 against 4 and 1 against 0.

- The gain shows where the frame waits for the command processor: uncapped, the frame rate rises 10-15 % in the benchmark's
  race (4,000 draws a frame; the frame loses the replay of its last submission, about 0.8-1 ms of 6.8) and 3.6 % in free
  roam (2,100 draws a frame, the command processor busy 60 % of a 4.5 ms frame). At the 30 fps cap nothing is left to gain on four
  5 GHz cores (the command processor is busy about 15 % of the frame), and with a busy loop on each core the run queue,
  not the command processor, sets the frame times. A Deck at 20 fps is the case in between; its replay is slower too
  (about 1.3 ms here, so 3 ms if it scales as the rest of the thread table), so 5-10 % is the guess for a Deck, not a
  measurement.
- Alternation is needed because the benchmark's race is not the same twice. Whole runs started with and without the cvar
  (2 pairs, uncapped, `--vulkan_present_on_submission_thread=true` on the command line, the benchmark's own minute)
  gave 166.4 [163.1-169.8] against 168.6 [167.6-169.5] fps, but the fps over that minute runs from 137-155 early to
  164-185 late and differs from run to run (the AI cars are not deterministic), more than the effect; the alternating
  runs cover a slightly earlier, heavier stretch (the off windows average 148 fps). Two controls with the same marks
  but the value never changed show that the switching itself does nothing: always on, both kinds of window 168.5 and
  168.3 fps (2 runs, 34 pairs, -0.0 %); always off, 144.8 and 144.6 fps (2 runs, -0.3 %). The first off window of a run
  (never on before) is already the slow one (122-125 fps against 155-158 for the next on window).
- Hitches: no frame over 50 ms in the uncapped runs in either state with the Deck preset; the counts with busy loops
  (5 against 5) and at 30 fps are within the noise of a 60 s window. With the game's MSAA one run had a cluster of 65
  and 69 ms frames at the end of an "on" window that the others did not repeat in 36 further switches; 2 frames over
  50 ms in the on windows against 0 in the off windows of that set.
- Picture: a paused free-roam scene (the pause menu over the Aventador on the road) shown in turn with the cvar on, off,
  on (two runs of five screenshots each): all screenshots are pixel-identical (largest difference 0 of 255). Standing
  at the spawn and driving with the cvar on (screenshots every 4 s, two while driving) shows no artifacts.

### Thread placement by name (#475, ReIGlue 0415)

No thread had an affinity: the scheduler moved the command processor, the simulation, the render thread and the audio
mixer around the four cores of a Deck and let any thread run on the command processor's core.
`--linux_thread_cores=...` (hot reload, default `off`) pins threads by name:

- `NAME=CORES;NAME=CORES;*=CORES`: `NAME` is the start of the thread's name or of its guest entry (`Guest_825CEC88`;
  `_` also matches a space), `*` every thread no other entry names, `CORES` a list such as `6-7` or `0,2-5` inside the CPUs
  the process may use (so `taskset` still applies). Guest threads are matched by their entry because their names carry
  a host address that differs from run to run (`XThread7FFF86C0`): `XThread` registers `Guest_<entry>` for each one.
- `auto`: for a CPU of four cores with two threads each (a Deck: 4 Zen 2 cores, 8 threads), from the SMT siblings in
  `/sys/devices/system/cpu/cpuN/topology/thread_siblings_list` among the CPUs the process may use: the main loop
  (`Guest_825CEC88`) and the audio mixer (`Guest_83075D18`) on core 0, the render thread (`Guest_8261E318`) and
  `Vulkan Submission` on core 1, `GPU Commands` alone on core 2's first CPU, everything else on the rest. A Deck gives
  `Guest_825CEC88=0;Guest_83075D18=1;Guest_8261E318=2;Vulkan_Submiss=3;GPU_Commands=4;*=5-7`. Any other CPU is left to
  the scheduler: the same layout on four cores without SMT (below) was slower. This is Pinyon Shift's Deck layout
  (ShiftGlue `2849938`); they measured it on a Deck at process CPU -6.1 and -6.8 % and p95 0.8-1.7 ms better. It is not
  measured on a Deck here.
- A placement thread applies the rules every 0.5 s for the first 20 s (threads inherit the mask of the thread that starts
  them) and every 5 s after; setting the value to `off` puts every pinned thread back on the process's original mask.
- `REX_THREAD_CORES_FILE=path` (environment): the first line of the file, while it exists, replaces the cvar's value and
  is read every 250 ms. It is how the in-run comparisons below switch a layout on and off.

**Results (#475).** This PC has no SMT, so the Deck's case (two threads on one core) cannot be measured here. What can be
measured is whether pinning by name helps or hurts on four cores with one thread each: CPUs 0-3, the cvar's value switched
through `REX_THREAD_CORES_FILE` every 4 s over the benchmark's minute (first 0.8 s of each window left out), Deck preset,
`off` restores the process's mask. Two layouts: the four-core form of the Deck layout (`Guest_825CEC88=0;
Guest_83075D18=0;Guest_8261E318=1;Vulkan_Submiss=1;GPU_Commands=2;*=3`, what `auto` produced on four cores without SMT
in these runs) and the command processor alone on a CPU (`GPU_Commands=3;*=0-2`).

| Configuration | State | Runs | fps | p99 ms | 1% low fps | Frames over 50 ms | Command processor ms/frame |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Deck layout on 4 cores, uncapped | off | 3 | 152.4 | 9.8 | 87.5 | 0 | 4.49 |
| | on | 3 | 143.7 | 11.4 | 68.7 | 0 | 4.57 |
| | paired | 39 pairs | **-4.4 %** (11 of 39 pairs higher) | | | | +0.3 % |
| Deck layout on 4 cores, 30 fps | off | 2 | 30.0 | 36.6 | 25.4 | 0 | 4.97 |
| | on | 2 | 30.0 | 40.4 | 22.2 | 2 | 5.01 |
| Deck layout on 4 cores, free roam, uncapped | off | 2 | 202.6 | 7.9 | 89.8 | 0 | 2.94 |
| | on | 2 | 171.2 | 13.4 | 55.5 | 0 | 3.11 |
| | paired | 36 pairs | **-15.3 %** (2 of 36 pairs higher) | | | | +5.8 % |
| Command processor alone, uncapped | off | 3 | 141.7 | 9.7 | 92.5 | 0 | 4.93 |
| | on | 3 | 138.4 | 10.6 | 82.3 | 0 | 4.99 |
| | paired | 39 pairs | -1.3 % (13 of 39 pairs higher) | | | | +0.2 % |

- Pinning does what it says (`tasks.txt` of a run with `auto`: `GPU Commands` allowed on CPU 2 only, the main loop and the
  audio mixer on 0, the render thread on 1, `Vulkan Submission` on 1, every other thread on 3), and costs nothing at
  default: the cvar is off, nothing starts. But on four CPUs with one thread each it is slower, not faster: the Deck
  layout puts all other threads (the pipeline compilers, the XMA decoder, the game's workers, the disk and driver
  threads) on one CPU, and a command processor that has a CPU to itself runs no faster than one the scheduler moves.
  So `auto` does nothing on a CPU without SMT.
- The SMT gain Pinyon Shift measured (the command processor with its sibling free, the simulation and the audio mixer
  not sharing with it) is exactly what this PC cannot show. The mechanism and the layout need one run on a Deck:
  Settings, Advanced, extra arguments `--linux_thread_cores=auto`, then the benchmark, with and without; the log
  lists each pinned thread (`linux_thread_cores:` lines).
### Decoding and recording on two threads (#474, ReIGlue 0420)

`--gpu_split_command_processor=true` (restart, default off; Vulkan) splits the GPU command processor in two, after
Pinyon Shift's `gpu_record_thread` (ShiftGlue `9362a07`, draws as plain records `7440c33`, unchanged register writes
dropped `9bf779f`). "GPU Commands" (the decoder) parses the PM4 stream against a shadow of the register file and
records, in order, the register writes (copied from the ring or the game's constant memory) and the backend work;
"GPU Recorder" applies the writes to the register file and runs the draws, copies, shader loads (memo 0140), swaps
(with the frame's tag), fence writes (`EVENT_WRITE_SHD`, with SDK 0115's futex wake, so `gpu_wait` and `render_idle`
wake when the recorder stores the word), interrupts, `EVENT_WRITE_EXT` and the end-of-buffer submission. Everything
that reads the register file, the caches or the Vulkan state runs on the recorder, so the memos 0140, 0340 and
0360-0362 stay as they are. The rules for what the CPU sees:

- `MEM_WRITE`, `COND_WRITE`, `REG_TO_MEM` and the occlusion queries (`EVENT_WRITE_ZPD`) wait for the recorder to run
  everything before them, then run on the decoder with the register file (the recorder is idle meanwhile).
- `WAIT_REG_MEM` for memory: while the recorder has work, the decoder polls again each time it finishes a batch (at
  most 100 us apart) instead of sleeping until a vblank, because the word may be a fence the recorder is still to
  write; this was the first build's problem (half the frame rate, the decoder sleeping ~8 times a frame).
- `WAIT_REG_MEM` for `COHER_STATUS_HOST` (Direct3D's coherency wait, about a hundred a frame) is answered on the
  shadow, as Pinyon Shift does; a wait for another register, and `REG_RMW` of a register below 0x2000 (the CPU and the
  backend write some of those directly), wait for the recorder and read the register file.
- Fences are recorded, not waited for. The read pointer is written by the decoder at once (it has copied what it
  needs from the ring); the work before it is published first. The decoder never asks the Vulkan backend about
  submissions, so Pinyon's `b3b3cbb` (awaiting a fence before the worker submitted it) cannot happen: the recorder alone
  ends and awaits submissions, and the threaded submission already waits for a fence only once it is submitted.
- Off while split: the pipeline lookahead (#378, it walks the decoder's ring position from inside a draw), the
  stereo replay of VR true stereo (0396) and `FORZA_GPU_FRAME_TRACE`.
- `--gpu_split_elide_unchanged_registers` (default on, only with the split): the decoder records only the context
  and constant registers (0x2000 on) whose value changes, except the two initiators: about 30 % of the 360,000-530,000
  register writes a frame are recorded.
- A log line once per 1,800 frames: batches a frame, register writes recorded, and how often the decoder waited for
  the recorder, by reason (`wait_memory` about 8 a frame, `register_read` 1, the rest 0 in free roam).

**Results.** Same machine and Deck preset as above, uncapped (`--frame_rate=240`), the TU4 build of main b9011fa with
ReIGlue 0420 ([ReIGlue #26](https://github.com/FH1-ReImagined/ReIGlue/pull/26)), one build, the cvar on the command line, runs started alternately (on first, then off first),
benchmark: its measured minute; free roam: 90 s of full throttle from the spawn. "4 cores" is `taskset -c 0-3`,
"8 cores" the whole CPU. GPU Commands / GPU Recorder ms/frame: each thread's CPU time per guest frame, sampled from
`/proc` (unsplit, GPU Commands is the whole command processor). Runs during which another game ran on the machine
were left out.

| Scene | Split | Runs | fps | 1% low fps | p99 ms | GPU Commands ms/frame | GPU Recorder ms/frame | GPU Commands busy % | GPU Recorder busy % | Process CPU ms/frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Benchmark, 4 cores | off | 3 | 151.6 [147.7-158.3] | 95.0 [92.5-99.6] | 9.4 [9.0-9.7] | 4.56 [4.20-4.82] | - | 69 [66-71] | - | 14.9 [14.2-15.4] |
| Benchmark, 4 cores | on | 3 | 154.9 [143.3-163.7] | 87.8 [77.3-94.0] | 9.8 [9.1-11.1] | 2.59 [2.49-2.76] | 4.00 [3.78-4.12] | 40 [36-43] | 62 [59-65] | 16.7 [16.1-17.1] |
| Benchmark, 4 cores | change | | **+2.2 %** | | | | | | | |
| Free roam, 4 cores | off | 3 | 221.8 [220.0-223.3] | 117.9 [109.4-124.4] | 6.9 [6.5-7.4] | 2.57 [2.39-2.72] | - | 57 [53-60] | - | 10.5 [10.3-10.7] |
| Free roam, 4 cores | on | 3 | 196.6 [189.8-203.1] | 100.2 [90.8-108.5] | 8.1 [7.4-8.9] | 1.80 [1.77-1.82] | 2.56 [2.50-2.63] | 35 [35-36] | 50 [50-51] | 12.6 [12.3-12.9] |
| Free roam, 4 cores | change | | **-11.4 %** | | | | | | | |
| Benchmark, 8 cores | off | 3 | 155.2 [151.5-158.9] | 98.8 [97.2-102.0] | 9.0 [8.9-9.1] | 4.39 [4.23-4.60] | - | 68 [67-70] | - | 15.2 [14.8-15.6] |
| Benchmark, 8 cores | on | 3 | 162.8 [157.6-168.2] | 107.9 [105.5-111.2] | 8.1 [7.7-8.4] | 3.05 [2.93-3.21] | 4.14 [3.94-4.38] | 50 [49-50] | 67 [66-69] | 18.0 [17.5-18.6] |
| Benchmark, 8 cores | change | | **+4.9 %** | | | | | | | |
| Free roam, 8 cores | off | 3 | 204.5 [202.6-206.9] | 106.7 [103.4-109.0] | 7.4 [7.1-7.7] | 3.06 [2.89-3.15] | - | 63 [60-64] | - | 11.8 [11.3-12.1] |
| Free roam, 8 cores | on | 3 | 215.4 [209.1-220.6] | 121.5 [113.4-137.5] | 6.5 [6.1-6.8] | 1.92 [1.83-2.00] | 2.58 [2.49-2.67] | 41 [40-42] | 56 [54-58] | 13.3 [13.0-13.5] |
| Free roam, 8 cores | change | | **+5.3 %** | | | | | | | |

- On four cores without SMT the split is slower in free roam (-11.4 %, every run) and level in the benchmark (+2.2 %,
  inside the race's run-to-run spread of 143-164 fps; the 1% lows are 7 fps lower). The recorder alone
  costs about what the whole unsplit command processor did (the recorded writes are applied through the generic
  `WriteRegistersFromMem` instead of 0360's direct copy from the ring, and elided writes save little on the recorder),
  and the decoder adds 1.8-3.1 ms of CPU a frame on top: the process uses 2-3 ms more CPU per frame on four cores the
  game already shares with its own threads. Neither GPU thread is saturated (35-66 % busy) at these frame rates:
  the frame waits on CPU-GPU round trips (`wait_memory`, about 8 a frame), which the split cannot shorten.
- With eight cores the decoder has a core of its own: +4.9 % in the benchmark and +5.3 % in free roam, 1% lows 9-14 %
  higher, p99 about 1 ms shorter; the process uses 1.5-2.8 ms more CPU a frame.
- A Deck (4 Zen 2 cores, 8 threads) is in between: the decoder could take an SMT sibling, but the recorder is the
  same length as today's thread, so expect little. One busy loop per core (CPUs 0-3, the slow-CPU stand-in above)
  gave on/off 81.6/91.2 fps in free roam and 63.6/71.6 in the benchmark (one pair each): slower.
- Correctness (split on): `tools/render_test.py` (festival session: title, free roam midday and dawn, cockpit, photo
  mode, race start) against baselines recorded with the split off, 6 of 6 scenes pass, each within the off-against-off
  noise of the baselines; the paint shop (Montano Plains outpost: Paint Shop, Design Creator, Add Decals) and fast
  travel work; a 10-minute free-roam session at 60 fps (paint shop, then 10 minutes of driving): 59.98 fps, longest
  frame 47 ms, no hang; one 760 ms frame entering the paint shop's editor (a loading screen, not compared with off).

#### On by default with 6 or more cores (2026-11-08)

Measured again on main 2e28667 with ReIGlue main a5fd408 and 0420 rebased on it, and with
`vulkan_present_on_submission_thread=true` in both arms (on by default since #607's #613; the swap's wait for the
replay was a fixed part of the command processor's frame in the runs above). The PC's default preset (AMD FSR 1, the
game's 4x MSAA, 1280x720, render scale 1) instead of the Deck preset, uncapped (`--frame_rate=240`), on the hidden
display (headless gamescope), one build with the cvar on the command line. Benchmark: 3 runs per state, interleaved
(off first, then on first); free roam: 3 minutes of driving from the spawn, one run per state. "6 cores" is
`taskset -c 0-5` on this 8-core CPU without SMT, standing in for a Ryzen 5 5600X (6 cores, 12 threads: more than
this). Command processor ms/frame: unsplit, the frame cost CSV's `cpu_ms` (`fh1_frame_cost_csv`); split, each
thread's CPU time per guest frame from `/proc` (the CSV then times the recorder only).

| Scene | Split | Runs | fps | 1% low fps | p99 ms | Command processor ms/frame | Decoder ms/frame | Recorder ms/frame | Process CPU ms/frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Benchmark, 8 cores | off | 3 | 147.0 [146.4-147.9] | 96.0 [93.9-98.5] | 9.37 [9.22-9.48] | 6.00 [5.96-6.07] | - | - | 17.8 |
| Benchmark, 8 cores | on | 3 | **166.3** [164.5-168.3] | 109.0 [107.1-110.3] | 8.00 [7.76-8.24] | - | 3.83 | 4.84 | 19.7 |
| Benchmark, 6 cores | off | 3 | 148.9 [146.7-151.4] | 95.6 [93.1-97.8] | 9.36 [9.28-9.47] | 5.86 [5.73-6.09] | - | - | 17.3 |
| Benchmark, 6 cores | on | 3 | **157.5** [155.2-162.0] | 100.2 [93.6-104.1] | 8.76 [8.58-9.02] | - | 4.12 | 5.26 | 20.5 |
| Free roam, 8 cores | off | 1 | 174.1 | 109.8 | 7.61 | 4.70 | - | - | 14.6 |
| Free roam, 8 cores | on | 1 | **232.7** | 131.2 | 6.15 | - | 2.56 | 3.32 | 14.8 |
| Free roam, 6 cores | off | 1 | 198.4 | 111.3 | 7.27 | 4.14 | - | - | 13.3 |
| Free roam, 6 cores | on | 1 | **227.5** | 128.3 | 6.41 | - | 2.29 | 3.05 | 14.1 |

- The benchmark: +13.1 % on eight cores, +5.8 % on six; every run with the split was faster than every run without,
  at both core counts, and the 1% lows and p99 are better. No frame over 50 ms in any of the 12 runs.
- Free roam: +14.7 % on six cores; on eight the run without the split was the slowest of the four (8 % other load on
  the machine), so its +34 % overstates it. Frames over 50 ms in the 3 minutes: 0 and 1 (six cores), 0 and 2 (eight).
- The gain is larger than in the runs above (+5 % on eight cores) because the swap no longer waits for the replay:
  then the unsplit thread's frame held an idle wait the split could not shorten.
- Two benchmark runs from empty game and driver shader caches (8 cores, one per state) ran at 30 fps (the fresh
  cache folder had no frame-rate setting), so they show only the hitches: 1 frame over 50 ms without the split, 0 with
  it. The split turns off the pipeline lookahead (#378); with the prebuilt pipelines (#276) that did not show.
- Correctness: `tools/render_test.py` (festival session, 6 scenes) against the stored baselines: with the split, 6 of 6 in the last run; in two earlier runs on the same build each state failed one scene by a little (without the split `freeroam_dawn`, with it `freeroam_midday` at 0.68 % changed against 0.50 %), and a first run with it (the #613 build) failed four, among them `photo_mode` with the camera turned the other way (yaw 100 against 280), the scripted input's timing rather than the picture.

The game turns it on by default (`ApplyForzaGpuCvarDefaults`, `src/main.cpp`) when the process may run on 6 or more
physical cores (its affinity; SMT siblings count once, `src/platform/cpu_cores.h`) and no VR headset is used: a
Steam Deck (4 cores, 8 threads) and the four-core runs above stay off. `gpu_split_command_processor` in `forza.toml`
or on the command line still sets it either way. The log says which: `Physical CPU cores available: N
(gpu_split_command_processor default true|false)`, and the performance settings line lists it.

### Where this leaves #559

Nothing here is on by default and nothing here was measured on a Deck. `--vulkan_present_on_submission_thread=true`
removes an idle wait from the thread the frame waits for and showed +12.7 % uncapped on four cores with the Deck
preset, with identical pictures and no extra hitches; it should be tried on a Deck (Settings, Advanced, extra arguments)
and, before it becomes the default, on the paths that use the deferred step differently: the VR export (frame tag and
pose pairing), native upscaling with DLSS (the replay's split submission), and a Windows run. `--linux_thread_cores=auto`
is the Deck layout of Pinyon Shift; it does nothing on a CPU without SMT because on four cores with one thread each it
measured slower, so only a Deck can say whether it helps. `--gpu_split_command_processor=true` (#474) is built and
correct here but only pays with a spare core (+5 % on eight cores, -11 % in free roam on four without SMT); a Deck run
with and without it would say whether its second thread on an SMT sibling helps there (`--linux_thread_cores=auto`
does not place "GPU Recorder" yet: it falls under `*`).

## Where a frame's CPU time goes (#607, 2026-11-03)

Step 1 of #607: per frame, which thread uses the CPU, what the GPU command processor spends it on, and what each
thread waits for, on this PC with 8 and 4 cores, with the Steam Deck preset, and in the players' logs. Nothing was
changed in the game or the SDK.

**Method.** TU4 game of main 9724662 (`-DFORZA_CPU_TARGET=x86-64-v3`) with main's ReIGlue plugin and runtime
(0.10.0.24 overlay), Ryzen 7 7800X3D (8 cores, no SMT), RX 9060 XT (RADV, Mesa 26.2.1), the golden TU4 save in a
scratch `--user_data_root`, scratch XDG dirs, offline, audio muted, warm scratch shader cache, BENCH lock held, no
other game. Scenes: **free roam** (60 s on County Route 203 and Route 28, `tools/autodrive.py` on a
`tools/nav_route.py` loop), **race** (GoPro Millennia Masters with 7 AI cars, 50 s from the start, autodrive
following the AI) and the **benchmark** (its measured minute). Configurations: **pc8** = the AMD preset (60 fps,
render scale 1, AMD FSR 1, the game's 4x MSAA) on all 8 cores; **pc4** = the same on CPUs 0-3 (`taskset`, again at
the scene); **deck4** = the launcher's Steam Deck preset (30 fps, `scene_msaa=off`, console shadow and texture
detail, 720p scene) on CPUs 0-3; **u** = the same with `--frame_rate=240`, where the frame rate follows the CPU side.
One run each. Per thread: CPU time and run-queue wait from `/proc/<pid>/task/*/schedstat` once a second
(`tools/thread_stats.py sample`) over the scene; roles from an `eu-stack` snapshot after the scene (guest entry
functions); the command processor's CPU, draws and timed waits from `--fh1_frame_cost_csv`; the render thread's waits
from `FORZA_GPU_WAIT_LOG=1`; what each thread runs from `PERF_CPUPROF` (1 ms CPU-time samples, self time only),
symbolized with `llvm-addr2line` (librexruntime, the plugin, `forza`) and `eu-addr2line` (RADV's MiniDebugInfo). perf
is not installed on this Fedora Atomic host and `perf_event_paranoid` is 2 (no `perf sched`, no kernel samples), so
time in system calls shows as the libc function that made the call. Other load on the game's CPUs during the scenes
(the desktop, the samplers, autodrive): 25-130% of a core, 187% in `bench pc8u`.

### Threads, ms of CPU a frame

| Run | fps | Draws | All threads | Command processor | Render thread | Main loop | Audio mixer | Vulkan submission | Other game threads | XMA decoder | Host, other |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| free roam pc8 | 60.0 | 3,417 | 15.9 | 4.86 | 3.30 | 2.13 | 1.52 | 1.33 | 2.39 | 0.07 | 0.27 |
| race pc8 | 59.9 | 4,561 | 18.9 | 6.27 | 3.40 | 2.47 | 2.06 | 1.55 | 2.72 | 0.14 | 0.24 |
| benchmark pc8 | 59.9 | 4,196 | 19.2 | 5.90 | 3.39 | 2.45 | 2.31 | 1.42 | 3.26 | 0.19 | 0.33 |
| free roam pc4 | 59.7 | 3,499 | 16.7 | 5.12 | 3.20 | 2.50 | 1.64 | 1.35 | 2.51 | 0.06 | 0.29 |
| race pc4 | 59.9 | 4,500 | 17.7 | 6.03 | 3.08 | 2.38 | 1.90 | 1.42 | 2.52 | 0.10 | 0.28 |
| benchmark pc4 | 59.7 | 4,194 | 19.1 | 6.02 | 3.44 | 2.53 | 2.29 | 1.43 | 2.91 | 0.14 | 0.33 |
| free roam deck4 | 30.0 | 2,812 | 19.3 | 4.05 | 3.44 | 2.96 | 3.02 | 1.19 | 4.02 | 0.13 | 0.47 |
| race deck4 | 30.0 | 3,360 | 19.3 | 4.45 | 3.24 | 2.75 | 3.63 | 1.10 | 3.49 | 0.23 | 0.44 |
| benchmark deck4 | 30.0 | 3,190 | 20.9 | 4.31 | 3.55 | 2.99 | 4.23 | 1.13 | 3.84 | 0.31 | 0.53 |
| benchmark pc8u | 129.5 | 4,461 | 17.3 | 6.07 | 3.67 | 2.26 | 1.15 | 1.45 | 2.42 | 0.09 | 0.17 |
| benchmark pc4u | 132.6 | 4,284 | 15.8 | 5.70 | 3.36 | 2.12 | 1.04 | 1.30 | 2.11 | 0.06 | 0.15 |
| benchmark deck4u | 156.7 | 3,558 | 13.8 | 4.26 | 3.32 | 2.07 | 0.92 | 1.06 | 2.01 | 0.06 | 0.12 |
| race deck4u | 173.4 | 3,193 | 12.3 | 3.87 | 2.72 | 2.15 | 0.65 | 1.01 | 1.86 | 0.00 | 0.09 |

- **Command processor** (`GPU Commands`): PM4 decoding and everything per draw, 1.2-1.6 us a draw. Per frame it
  follows the draws: the Deck preset's one-pass scene draws 20-25% less than the 4x MSAA scene (three EDRAM tiles)
  on the same four cores, and its command processor needs 21-28% less.
- **Render thread** (`sub_8261E178`, the game's D3D command building): 81-87% guest code, 6-10% `sched_yield` (the
  yield phase of `gpu_wait.cpp`'s waits), the rest locks, `mprotect` and write-watch faults.
- **Main loop** (`sub_825CEC88`, simulation, physics and AI): 94-97% guest code. 2.1-3.0 ms a frame, a little more
  per frame at 30 fps than at 60.
- **Audio mixer** (`sub_83075D18`): fixed per second (110-127 ms a second, 11-13% of a core), so 3.6-4.2 ms a frame
  at 30 fps and 0.7-1.2 ms uncapped; 83-89% guest DSP code, 8-13% the WMA decoder in the runtime.
- **Vulkan submission**: the deferred command buffer's replay, 74-78% inside RADV (`radv_bind_descriptor_sets`,
  `radv_CmdBindPipeline`, `radv_emit_all_graphics_states`, `radv_CmdDrawIndexed`), 6-7% `ioctl`.
- **Other game threads**: the game's workers (`sub_82C6BD58` 0.6-1.1 ms, `sub_82EE37A0`, `sub_82CCC368`,
  `sub_824A2080`, `sub_82F0CF00`, 0.1-0.8 ms each). Host threads (pipeline compilers, vsync, input) cost little with a
  warm cache.

The game's own threads (render thread, main loop, audio mixer, workers) use more CPU in total than the emulation of the
GPU (command processor and submission: 26-29% of all CPU with the Deck preset at 30 fps, 39-41% at 60 fps and
uncapped), but they run in parallel; the frame waits on one chain (below).

### The command processor, % of its CPU

| Area | free roam pc8 | race pc8 | bench pc8 | free roam pc4 | race pc4 | bench pc4 | free roam deck4 | race deck4 | bench deck4 | bench pc8u | bench deck4u | race deck4u |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Bindings, textures, samplers (`UpdateBindings`, `RequestTextures`, texture map, views, sampler lookups) | 22.2 | 20.8 | 20.3 | 20.4 | 20.8 | 20.4 | 21.3 | 21.9 | 20.1 | 20.2 | 20.7 | 21.1 |
| PM4 packet loop (`ExecutePacket*`, with 0360's inline register stores) | 16.3 | 16.9 | 17.1 | 16.3 | 16.6 | 16.7 | 13.3 | 15.2 | 14.8 | 17.0 | 15.7 | 14.6 |
| Vulkan driver (RADV: `radv_UpdateDescriptorSets` and its buffer descriptors, 85-90% of it) | 7.2 | 9.9 | 9.7 | 9.4 | 8.9 | 10.0 | 10.2 | 8.2 | 9.3 | 9.4 | 7.9 | 8.2 |
| Register writes (`WriteRegistersFromMem`, `copy_and_swap`, `WriteRegister`) | 9.3 | 8.6 | 9.1 | 8.8 | 9.0 | 8.9 | 8.0 | 8.3 | 9.1 | 9.1 | 8.8 | 8.6 |
| Shared memory, memory watches (`RequestRanges`, `mprotect`, `EnableAccessCallbacks`) | 7.6 | 7.2 | 7.8 | 7.0 | 7.4 | 7.7 | 8.4 | 8.9 | 9.7 | 8.4 | 9.3 | 9.1 |
| Shaders and pipelines (`IM_LOAD`, translations, pipeline map) | 6.8 | 6.6 | 6.6 | 7.0 | 6.8 | 6.4 | 7.4 | 6.4 | 7.0 | 6.3 | 6.5 | 6.9 |
| System constants, viewport, dynamic state | 6.1 | 6.2 | 6.0 | 5.7 | 5.6 | 5.4 | 5.4 | 5.9 | 5.5 | 5.9 | 6.1 | 6.2 |
| `memcpy`, `memcmp`, `memset` | 5.8 | 5.3 | 5.3 | 5.4 | 5.3 | 5.4 | 6.3 | 6.2 | 6.7 | 5.4 | 5.7 | 5.6 |
| System calls (futex, yield, ioctl) | 3.8 | 3.0 | 3.4 | 5.2 | 4.3 | 4.5 | 3.0 | 2.8 | 2.8 | 3.5 | 3.2 | 3.2 |
| `IssueDraw` itself | 3.4 | 3.5 | 3.4 | 3.0 | 3.4 | 3.3 | 3.2 | 3.6 | 3.3 | 3.3 | 3.6 | 3.7 |
| Render targets | 2.4 | 2.4 | 2.2 | 2.6 | 2.3 | 2.5 | 3.1 | 3.0 | 2.7 | 2.5 | 3.0 | 2.9 |
| Command recording, submission, sync | 1.8 | 1.8 | 1.8 | 1.9 | 1.9 | 1.7 | 2.5 | 1.8 | 2.0 | 1.8 | 1.9 | 1.9 |
| Primitive processing | 1.2 | 1.6 | 1.2 | 1.2 | 1.4 | 1.1 | 1.3 | 1.5 | 1.1 | 1.2 | 1.5 | 1.5 |
| Other runtime, plugin and libc code | 6.2 | 6.2 | 6.3 | 6.1 | 6.4 | 5.8 | 6.7 | 6.3 | 5.9 | 5.9 | 6.2 | 6.3 |
| **= ms of CPU a frame** | 4.85 | 6.26 | 5.90 | 5.12 | 6.02 | 6.02 | 4.05 | 4.46 | 4.32 | 6.07 | 4.26 | 3.87 |

The split hardly moves between scenes, presets and core counts: it is a cost per draw. In four groups:

- **Draw setup that touches descriptors** (bindings, textures, samplers and the driver's descriptor writes): 29-31%,
  1.1-1.9 ms a frame. The command processor's own Vulkan calls are almost only `vkUpdateDescriptorSets`; its
  command recording goes into the deferred command buffer (1.8-2.5%), which the submission thread replays.
- **PM4 decode and register writes** (packet loop, register writes, the copies): 28-32%, 1.1-1.9 ms.
- **The rest of the draw setup** (shaders, system constants, viewport, render targets, primitives, `IssueDraw`,
  command recording): 20-23%.
- **System calls and other runtime, plugin and libc code**: 9-11%.
- **Guest memory** (shared memory ranges, `mprotect`, watches): 7-10%.

The Vulkan driver in all, per frame: 1.1-1.8 ms, two thirds of it on the submission thread (the replay), one third on
the command processor (descriptor writes).

### Waits, ms a frame

| | free roam pc8 | race pc8 | bench pc8 | free roam pc4 | race pc4 | bench pc4 | free roam deck4 | race deck4 | bench deck4 | bench pc8u | bench pc4u | bench deck4u | race deck4u |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Frame time | 16.68 | 16.70 | 16.69 | 16.74 | 16.69 | 16.75 | 33.33 | 33.34 | 33.34 | 7.72 | 7.54 | 6.38 | 5.77 |
| Render thread waiting for the GPU emulation (`GPUWAIT`) | 11.89 | 11.93 | 11.64 | 11.47 | 11.83 | 11.07 | 27.20 | 29.39 | 27.40 | 1.97 | 2.01 | 1.75 | 1.75 |
| Command processor waiting for the game (`WAIT_REG_MEM`) | 11.15 | 9.54 | 9.86 | 9.93 | 9.57 | 9.01 | 27.57 | 27.62 | 27.60 | 0.65 | 0.71 | 0.81 | 0.64 |
| Command processor waiting for the GPU | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Command processor waiting for readbacks | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Command processor off the CPU otherwise (frame - CPU - timed waits - run queue) | | | | | | | | | | 0.68 | 0.69 | 1.09 | 1.04 |
| Run-queue wait (runnable, no free CPU): command processor | 0.18 | 0.29 | 0.35 | 1.18 | 0.50 | 1.11 | 0.91 | 0.29 | 0.51 | 0.31 | 0.44 | 0.22 | 0.22 |
| Run-queue wait: render thread | 0.17 | 0.29 | 0.35 | 1.32 | 0.74 | 1.39 | 1.39 | 0.36 | 0.84 | 0.44 | 0.80 | 0.40 | 0.45 |
| Run-queue wait: all threads | 1.2 | 2.1 | 6.1 | 9.9 | 6.1 | 10.0 | 10.5 | 4.3 | 7.7 | 2.5 | 4.4 | 2.4 | 2.4 |

- **Capped**, every frame fits: the command processor is busy 29-38% of a 60 fps frame and 12-13% of a 30 fps frame,
  and the frame cap is spent in two matching waits: the render thread in the D3D library's wait for the swaps
  (`gpu_wait.cpp`) and the command processor in `WAIT_REG_MEM` for the next frame's commands.
- **Uncapped**, the command processor is the chain the frame waits for: it runs 67-79% of the frame, waits 0.6-0.8
  ms for the game's commands and is off the CPU for another 0.7-1.1 ms outside its timed waits, about the submission
  thread's replay of the frame (1.0-1.45 ms a frame): the swap waits for that replay (`AwaitSubmissionThread`, see
  #476 above; `vulkan_present_on_submission_thread` is off by default). The render thread waits for it 1.8-2.0 ms a
  frame.
- No GPU waits and no readback waits on this PC in any scene (#545's readback changes); the RX 9060 XT is never the limit.
- Four cores cost run-queue time, not CPU time: at 60 fps the command processor waits for a CPU 0.5-1.2 ms a frame
  on four cores against 0.2-0.35 on eight, the render thread 0.7-1.4 against 0.2-0.35; the CPU time per frame is the
  same.

### Players' logs (Windows)

The `GPU minute` lines of the logs attached to recent issues: the median over the minutes with at least 1,000 draws
and a timed command processor, per GPU and frame cap; CPUs as the reporters named them. "Busy" = command processor CPU
x fps.

| Issue | CPU | GPU (driver) | Cap, MSAA | Minutes | fps | Command processor ms a frame | us a draw | Busy | GPU waits ms | Game waits ms | Draws |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| #564 | i7-14700K | RX 6750 XT (AMD) | 120, game | 304 | 60.8 | 5.3 | 1.51 | 32% | 0.1 | 1.4 | 3,395 |
| #534 | Core Ultra 7 255HX | RTX 5070 Laptop | 60, game | 190 | 60.0 | 5.3 | 1.60 | 32% | 0.0 | 10.2 | 3,242 |
| #550 | | RTX 4090 | 120, game | 6 | 118.8 | 4.5 | 1.58 | 53% | 0.0 | 3.4 | 2,808 |
| #546 | | RTX 5060 Ti | 240, off | 7 | 162.8 | 3.2 | 1.91 | 52% | 0.1 | 0.3 | 1,597 |
| #562 | Ryzen 7 5700X3D | RTX 4070 Ti SUPER | 60, game (scale 3) | 16 | 58.7 | 5.7 | 2.25 | 33% | 2.3 | 5.8 | 2,471 |
| #600 | Ryzen 7 5700X | Arc B580 (Intel) | 60, game | 27 | 59.3 | 6.5 | 2.50 | 39% | 0.0 | 8.9 | 2,669 |
| #567 | i7-10700 | RTX 2060 SUPER | 60, game | 259 | 59.3 | 7.8 | 2.81 | 46% | 0.5 | 5.7 | 2,781 |
| #567 | i7-10700 | RTX 2060 SUPER | 240, game | 159 | 76.8 | 9.2 | 2.86 | 71% | 0.6 | 0.8 | 3,070 |
| #490 | Ryzen 5 5600X | RTX 3060 | 60, game (scale 2) | 59 | 41.4 | 7.0 | 2.22 | 29% | 12.2 | 2.7 | 3,114 |
| #556 | Ryzen 3 1200 (4 cores) | GTX 1650 | 60, off | 18 | 52.6 | 8.4 | 3.84 | 44% | 0.8 | 3.9 | 2,126 |
| #543 | Ryzen 3 3100 (4 cores, 8 threads) | GTX 1050 Ti | 60, game | 14 | 39.2 | 10.1 | 3.58 | 40% | 8.4 | 2.7 | 2,764 |
| #551 | Ryzen 7 7730U | Radeon Vega iGPU (AMD) | 30, off | 114 | 29.1 | 12.9 | 6.21 | 38% | 4.2 | 5.3 | 2,088 |
| #551 | Ryzen 7 7730U | Radeon Vega iGPU (AMD) | 60, off | 45 | 31.0 | 15.5 | 6.08 | 48% | 11.6 | 0.0 | 2,459 |

- The command processor's cost per draw spans 4x: 1.5-1.6 us on recent desktop and laptop CPUs (this PC: 1.2-1.6), 2.2-2.9
  on Zen 3 and Comet Lake desktops, 3.6-3.8 on 4-core Zen 1 and Zen 2, 6 on a 15-28 W Zen 3 laptop. The draws per
  frame are about the same everywhere (1,600-3,400), so the per-draw cost is the frame's cost.
- Where the frame is below its cap, it is either the GPU (`GPU waits` 8-12 ms: #490 at render scale 2, #543, #551 at 60)
  or the command processor chain (#567 at 240: busy 71%, game waits 0.8 ms, GPU waits 0.6; the other 2.4 ms of its
  13 ms frame it is off the CPU, as here, only longer). On the Vega laptop both: 13-16 ms of command processor and 4-12 ms of GPU waits a frame.
- The logs carry no per-thread numbers for the game's threads; those come only from runs here.

### Ranked: where the frame's critical chain goes

Uncapped on four cores with the Deck preset (race deck4u, 5.77 ms a frame), the closest this PC gets to the Deck's
bound; the 8-core benchmark (bench pc8u, 7.72 ms, 4x MSAA) in brackets:

1. Command processor, descriptor-related draw setup (bindings, textures, samplers, `vkUpdateDescriptorSets`):
   1.14 ms, 20% of the frame (1.80 ms, 23%).
2. Command processor, PM4 decode and register writes with their copies: 1.12 ms, 19% (1.91 ms, 25%).
3. Command processor off the CPU waiting for the submission thread's replay at the swap: about 1.0 ms, 18% (0.7 ms, 9%).
4. Command processor, the rest of the draw setup: 0.90 ms, 16% (1.28 ms, 17%).
5. Command processor waiting for the game's commands: 0.64 ms, 11% (0.65 ms, 8%).
6. Command processor, system calls and other runtime and libc code: 0.37 ms, 6% (0.57 ms, 7%).
7. Command processor, guest memory ranges and watches: 0.35 ms, 6% (0.51 ms, 7%).
8. Command processor waiting for a CPU: 0.22 ms, 4% (0.31 ms, 4%).

Off the chain and in parallel, per frame: the render thread 2.7-3.7 ms (of it 0.2-0.35 ms yielding in its GPU waits),
the main loop 2.1-3.0 ms, the submission thread 1.0-1.5 ms (three quarters in RADV), the audio mixer 0.65-4.2 ms
(fixed per second), the workers 1.9-4.0 ms in all. On a Deck these share four cores and eight threads with the chain;
with each thread 2-2.5 times slower there, the chain above would be 12-14 ms (70-85 fps) if nothing else got in the
way, so the Deck's 20-30 fps also carries the waits for a CPU and the SMT sharing, which this PC cannot show. A Deck
log with the Deck preset is what #45 and #559 still need.

### The cuts this points at

1. **Present without waiting for the replay** (`vulkan_present_on_submission_thread`, #476, built and off by default):
   the 0.7-1.1 ms a frame the command processor sits off the CPU at the swap. Measured before: +15% uncapped with the
   Deck preset, +10% with 4x MSAA, identical pictures. Estimated saving: 1.0 ms of 5.8 (Deck preset), 0.7 of 7.7 (4x MSAA)
   on this PC, about 2-2.5 ms on a Deck. Left: a Deck run, VR, DLSS and a Windows run before it becomes the default.
2. **Reuse unchanged bindings** (#192, texture and sampler bindings; #179, dynamic constant descriptors): 29-31% of the
   command processor, 1.1-1.9 ms a frame, with `vkUpdateDescriptorSets` 7-8% of the thread. If half of it goes,
   0.6-0.9 ms a frame here (10-15% of the command processor), 1.3-2 ms on a Deck. Sean's drafts measured +6.5% and +7.7%
   fps on a CPU-bound PC.
3. **Decode on one thread, record on another** (#474): PM4 decode and register writes are 28-32% of the command
   processor (1.1-1.9 ms a frame) and could leave the thread the frame waits for, less the hand-off; estimated 0.8-1.3
   ms a frame here, 2-3 ms on a Deck, if the decoder gets a core of its own (Pinyon Shift measured 6% from the split
   alone; the plan is in the #474 section above). It needs free cores, so it helps 6-8-core PCs first; on a Deck it
   depends on the SMT sibling, which this PC cannot show.

Not worth it first: the guest memory watches (6-10%, rounds 2-3 above already took the walk), the render thread's
yields (0.2-0.35 ms a frame, not on the chain), the GPU and readback waits (zero here; on players' PCs a GPU limit is
a setting, render scale or MSAA). The audio mixer is a fixed 11-13% of a core here, 2-2.5 times that on a Deck, where it
competes for the four cores; worth a look after the three above.

### Step 2: the hottest instruction of the command processor (#607, ReIGlue 0435, 2026-11-04)

After the cuts above (present without the replay wait #613, binding reuse #179/#192, the split #474) and the
dropped register elision (ReIGlue #32), what is left on the command processor that none of them covers?

**Method.** TU4 game of main f95b52c (`-DFORZA_CPU_TARGET=x86-64-v3`), ReIGlue 641a19c (the pin) built with
`-gline-tables-only` (same code; samples resolve to inlined functions), the golden TU4 save in a scratch
`--user_data_root`, scratch XDG dirs, offline, audio muted, the launcher's Steam Deck preset with
`--frame_rate=240` (uncapped: the frame waits on the command processor), BENCH lock held, no other game running
(checked every 10 s). Free roam (60 s of `tools/autodrive.py` on the County Route loop) and the race (GoPro
Millennia Masters, 60 s from the start); 8 cores, or CPUs 0-3 with `taskset`. `PERF_CPUPROF` samples (1 ms of CPU
time, self time) symbolized with their inline chains (`llvm-symbolizer --inlining`) and attributed to the physical
function; `eu-stack` call stacks of the command processor (264 in a race) for the waits. perf is still not
installed.

**Where the command processor's time goes** (free roam, ms of CPU a frame; 4 cores: 3.50 ms, 2,755 draws,
188 fps; 8 cores: 3.70 ms, 2,808 draws, 181 fps):

| Area | 4 cores | 8 cores | Covered by |
| --- | --- | --- | --- |
| Bindings, textures, samplers, `vkUpdateDescriptorSets` | 0.99 (28 %) | 1.04 (28 %) | #179, #192 (Sean's drafts) |
| PM4 decode, register writes and their copies | 0.86 (25 %) | 0.90 (24 %) | #474 (ReIGlue #26), ReIGlue #32 dropped |
| Guest memory: `RequestRanges`, `mprotect`, `EnableAccessCallbacks`, watches, lock wakes | 0.34 (10 %) | 0.32 (9 %) | |
| Shaders and pipelines (`IM_LOAD` memo, translation and pipeline lookups) | 0.21 (6 %) | 0.23 (6 %) | |
| System constants, viewport, dynamic state | 0.21 (6 %) | 0.22 (6 %) | |
| `memcmp` | 0.08 (2 %) | 0.07 (2 %) | |
| Yields in `WAIT_REG_MEM` (waiting for the game) | 0.05 (1 %) | 0.10 (3 %) | |
| `IssueDraw`, primitives, render targets, uploads, the rest | 0.77 (22 %) | 0.81 (22 %) | |

Wall clock (call stacks, race, 8 cores): 47 % in `IssueDraw`, 16 % waiting for the submission thread at the swap
(#613), 13 % waiting for the game (`WAIT_REG_MEM`). Off the chain: the render thread 2.8 ms a frame (83 % guest
code, 7 % `sched_yield`), the main loop 2.2 ms (93 % guest code), the submission thread 1.0 ms (78 % RADV).

Outside the covered areas the profile is flat: no function above 2 % of the thread. But one instruction stands
out: **3.6-3.9 % of all the command processor's samples land right after the `xchg` in
`TextureCache::RequestTextures`**, `texture_became_outdated_.exchange(false)` on every draw. A locked instruction
waits for the store buffer to drain, and at that point it holds the draw's register, constant and command
stores. The flag is only set by memory watch callbacks. The next was `ExecutePacket`'s division of the packet's
start offset (for the VR stereo log, 0396), 1.2 % of the thread, about 24 times a draw.

**Change: ReIGlue 0435.** The flag is loaded first and exchanged only when set (a callback that sets it after the
load is seen by the next draw, as before after the exchange); `ExecutePacket` keeps the read offset and divides
only when it logs the packet. No setting: the behaviour is the same. In the profiles `RequestTextures` falls from
4.7 % to 0.9 % of the thread and `ExecutePacket` from 6.3 % to 5.4 %.

**Measured.** One game build, the two plugins (A = the pin, B = 0435; the same runtime), 3 runs each, A and B
alternating which ran first, 24 runs, no other game. Mean [min-max]; fps, 1 % low and p99 from the command
processor's frame intervals over the 60 s:

| Scene | Build | fps | 1 % low fps | p99 ms | CP CPU ms a frame | CP us a draw | Draws |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Race, 8 cores | A | 186.5 [185.2-187.7] | 115.2 [114.0-116.9] | 8.45 [8.45-8.45] | 3.70 [3.67-3.73] | 1.258 [1.255-1.263] | 2,942 |
| Race, 8 cores | B | 191.5 [190.5-192.3] | 116.0 [114.5-116.8] | 8.42 [8.40-8.44] | 3.54 [3.53-3.56] | 1.204 [1.202-1.204] | 2,943 |
| Race, 8 cores | change | **+2.7 %** | +0.7 % | -0.4 % | -4.3 % | -4.3 % | |
| Race, 4 cores | A | 183.7 [181.1-185.4] | 108.6 [106.6-110.2] | 8.51 [8.47-8.54] | 3.67 [3.62-3.72] | 1.237 [1.236-1.238] | 2,964 |
| Race, 4 cores | B | 186.1 [184.1-187.6] | 111.9 [111.2-112.4] | 8.45 [8.44-8.45] | 3.57 [3.53-3.58] | 1.186 [1.176-1.195] | 3,005 |
| Race, 4 cores | change | **+1.3 %** | +3.0 % | -0.8 % | -2.8 % | -4.1 % | |
| Free roam, 8 cores | A | 186.5 [181.7-189.6] | 113.6 [107.8-123.6] | 7.35 [6.64-8.02] | 3.60 [3.54-3.67] | 1.311 [1.310-1.312] | 2,742 |
| Free roam, 8 cores | B | 193.0 [181.7-202.1] | 118.6 [117.6-119.6] | 6.92 [6.50-7.21] | 3.42 [3.28-3.60] | 1.251 [1.236-1.264] | 2,735 |
| Free roam, 8 cores | change | **+3.5 %** | +4.3 % | -5.8 % | -4.8 % | -4.6 % | |
| Free roam, 4 cores | A | 190.7 [183.2-195.6] | 103.5 [103.1-104.4] | 8.22 [7.94-8.46] | 3.47 [3.38-3.54] | 1.277 [1.267-1.289] | 2,716 |
| Free roam, 4 cores | B | 186.0 [182.1-191.9] | 98.7 [87.6-106.4] | 8.40 [7.75-9.11] | 3.46 [3.35-3.61] | 1.235 [1.214-1.256] | 2,804 |
| Free roam, 4 cores | change | **-2.4 %** | -4.7 % | +2.2 % | -0.2 % | -3.3 % | |

- The command processor's CPU per draw falls 4.3-4.6 % on 8 cores and 3.3-4.1 % on 4, every run of one build
  outside the other's range: about 0.05-0.06 us a draw, 0.15-0.2 ms of a 3,000-draw frame.
- The frame rate follows on 8 cores (race +2.7 %, the runs apart; free roam +3.5 %, inside its spread). On 4 cores
  the race gains 1.3 % (the ranges overlap) and free roam shows no gain: its runs differ by 3 % in draws a frame and
  by 7 % in fps within a build. With four cores the command processor is not the only thing the frame waits for
  (run-queue waits, the render thread), so part of the saving becomes waiting.
- Same output: the change keeps the behaviour. `ExecutePacketType3 overflow` / `INDIRECT RINGBUFFER` errors came
  in 1 of 12 A runs and 2 of 12 B runs (once with draws failing in the same burst); they are not new, and neither
  change touches the packet parsing.

**Left** (this round, not built): the guest memory area (9-10 %: three `mprotect` calls per protected range, one
for each of the physical memory's views, under the global lock the game's write faults take; 88 texture loads
a frame in free roam), the `IM_LOAD` memo's `memcmp`, `Fh1DirtSeamInterpolator`'s per-draw binary search
(0.6-0.9 %).

## Stutter in the first part of a drive (#614, 2026-11-03)

**The report.** A side-by-side video of the same replayed drive on one Windows PC: our Vulkan path showed tall
frame-time spikes over the first ~30 s of a race and a flat graph afterwards (about 108 fps on average), ReGPU (#611)
far fewer (93 fps), after building its recorded pipeline seed at boot. Players with NVIDIA cards report hitches early
in a session even after Prebuild shaders (#400, #550).

**Method.** TU4 game of main 77a7c33, ReIGlue 0422 with 0423 (the frame cost CSV also gives the pipelines built on any
thread and their driver time, the frames held back for pipelines, skipped draws and CPU writes to watched memory),
Ryzen 7 7800X3D, RX 9060 XT (RADV), Linux; the golden TU4 save in a scratch `--user_data_root`, scratch XDG dirs,
offline, audio muted, 60 fps, FSR 1, render scale 1, 1920x1080 window. Two drives, each a fresh session from the boot:
**race**, the GoPro Millennia Masters sign-up at the spawn and 60 s of the race from the start with `tools/autodrive.py`
following the AI; **free roam**, 70 s on the County Route 203 loop (`tools/nav_route.py`, the #45 loop) from the spawn.
**Empty**: an empty `--cache_root` and an empty `MESA_SHADER_CACHE_DIR`. **Prebuild**: the launcher's step on an
empty cache root and Mesa cache (the shipped list merged with the server's, which added nothing, rebuilt from the game
files, then `--shader_prebuild_only`), then the drive on that cache. Spikes are presented frames over 25 ms: from
`--fh1_frame_cost_csv`, a held frame adds its time to the next shown one (the same counts as `RECOMP_PERF_CSV`'s
presents within one or two frames); each is put down to what took its time (held for pipelines, else the largest of
the command processor's costs, else the game: the command processor waited for its commands). `BENCH_LOCK` held from
the load; another agent's game ran during the second prebuild batch (load 2-4).

| Drive | Cache | Spikes > 25 ms (> 50 ms), worst | Their cause | Pipelines built in the drive / in the session after the warm-up |
| --- | --- | --- | --- | --- |
| race, 60 s | empty | 14 (3), 58 ms | all held for pipelines (9-22 ms each on RADV) | 25 / 433 |
| race, 60 s | prebuild, list of main | 2 (0), 34 ms; 2 (0), 34 ms | held for pipelines | 2 / 27; 2 / 27 |
| race, 60 s | prebuild, new list | 0; 0 | - | 0 / 1; 0 / 0 |
| free roam, 70 s | empty | 42 (3), 83 ms | all held for pipelines | 76 / 367 |
| free roam, 70 s | prebuild, list of main | 2 (0), 34 ms; 2 (0), 34 ms | held for pipelines | 2 / 26; 2 / 26 |
| free roam, 70 s | prebuild, new list | 14 (0), 31 ms (load 7.5 at its end); 1 (0), 25 ms | the game | 0 / 0; 0 / 0 |

Ranked, on the first drive:
1. **Pipelines not built yet** (frames held back until a creation thread has built them): every spike of both drives
   on an empty cache, about 400 pipelines a session. The prebuild removes nearly all of them.
2. After the prebuild, still **25-27 pipelines a session** built in play, most of them in the loading and the first
   seconds of free roam: 18-20 were **in the shipped list but never imported**, because they use two shaders the
   prebuild could not rebuild from the game files (`missing` recipes, below); the other 5-7 were not listed (two
   pixel shaders with early depth test, a strip variant, a render target format, one new vertex shader).
3. **The game's own long frames while loading** (the command processor waiting for its commands): 20-35 a session
   over 25 ms during the load into free roam and the race sign-up, the same with or without the prebuild. Not
   something the GPU side waits for.
4. The rest is small: one resolve readback (4 ms) in a cold run; texture loads, uploads, render targets and GPU waits
   never reached 4 ms in a spike; 15-20 CPU writes to watched memory a frame, under 0.1 ms in all.

On RADV a pipeline takes 5-30 ms, so even the empty cache stays under 100 ms. NVIDIA's driver takes 40-65 ms for one:
in the #550 log (RTX 4090, 0.2.1, a fresh install after Prebuild shaders) the warm-up created the same 838 of 1148
listed pipelines, the session built about 60 more, and every slow frame it reports after the load is `pipelines 1.0
(40-65 ms)` on the command processor: a draw without a pixel shader has no placeholder, so its pipeline is built there,
and 11 of the 27 unimported ones above are such draws. Each of them is a 40-65 ms stall on that card in a first session.

**Why the 27 were not imported.** The list's `missing` recipes were a 96-byte vertex shader (`B6C9863F710683EC`) and a
36-byte pixel shader (`A4A965C189287B99`) that are not blobs: the D3D library keeps them in the executable image without
a blob header (Title Update 4 image offsets 882440 and 882536, the disc's 810200 and 810296), so `fxobj_shaders.py`,
which finds blobs by their header, never saw them. 39 of the 42 listed pipelines that could not be built use them, 27
in the `main` partition. The new `image` recipe (docs/shader_prebuild.md) says where they are, size and offsets, no
code; the build checks the hash. The other two `missing` shaders are not in the executable verbatim and stay missing.
The list also gains the 7 pipelines these drives met that it did not have.

**After** (the game PR): the prebuild imports 876 pipelines for this partition (838 before; 1194 of the 1197 listed can
be built, 1148 of 1190 before), the game's part of the prebuild takes 3.0-3.4 s (3.0-3.1 s before), and the drives build 0-1 pipelines in a session
instead of 26-27; the race minute and the free-roam drive have no spike from pipelines left. What remains on this PC
is the game's own frames while it loads. Not changed: on NVIDIA every start still compiles what its driver cache does
not keep (#406), at the warm-up, before the title screen, not in play.

## Memory: what the game takes in RAM and VRAM, for 8 GB PCs (#640, 2026-11-04)

**Measured** on the RX 9060 XT PC (Linux, RADV, 30 GB): TU4, the golden save, offline, audio muted (the XMA decoder
still decodes), the launcher's defaults (60 fps, AMD FSR 1, the game's 4x scene MSAA), render scale 1, a 1280x720
window, a warm shader cache. Two scenes: free roam after a 4-minute drive (throttle and steering on the roads
around the festival) and the GoPro Millennia Masters race after 2 minutes (`tools/autodrive.py`). Each number is
the process's own (`tools/memory_snapshot.py`: `/proc/PID/smaps`, the guest memory file's pages, the DRM client's
VRAM and GTT), so other games on the PC do not change it.

| MB | Free roam, after the drive | Race, 2 minutes in |
| --- | --- | --- |
| RSS (PSS) | 1,186 (933) | 1,204 (942) |
| Guest memory: pages written (of the 4.5 GB file) | 488 | 491 |
| Guest memory: host commit (Windows: commit charge), in use now | 704, 528 | 704, 529 |
| Heaps and other private memory (anon) | 343 | 350 |
| The game's code (forza, file-backed, evictable) | 64 | 63 |
| SDK, driver and system libraries: RSS (PSS) | 77 (36) | 77 (36) |
| GTT (system RAM the GPU driver holds for us, not in RSS) | 59 | 79 |
| VRAM (device-local) | 1,094 | 1,106 |

The guest's pages are mapped twice (virtual and physical views), so RSS counts them twice and PSS once. Over a
60-minute soak (#45, above) RSS grows another 90 MB, flat after 20-30 minutes, as the texture cache fills.

**The heaps**, by allocation site (an `LD_PRELOAD` heap tracker, allocations of 4 KB and more, free roam after the
drive; 297 MB tracked):

| MB | What |
| --- | --- |
| 143 | XMA decoder: an FFmpeg codec context for each of the 320 contexts, 450 KB each, set up at start (the game uses 48 in free roam, 70 after the drive) |
| 40 | XMA decoder: a 128 KB packet buffer for each of the 320 contexts, never used |
| 11 | XMA decoder: the decoders in use (MDCT tables) |
| 18.6 + 18 | shader translations: our SPIR-V (473 translations) and the driver's copy in the shader modules |
| 20 | glslang's built-in symbol tables, kept after the startup compiles |
| 12 | three descriptor set allocators |
| 9.5 | the guest heaps' page tables |
| 4.5 | the audio system |
| 3.4 | the driver pipeline cache's entries (RADV: 10 KB a pipeline) |

**On Windows** the guest memory is a page-file-backed section (`CreateFileMappingW` with `SEC_RESERVE`, 4.5 GB
reserved, nothing committed). `Memory::Initialize` then commits the whole physical memory (512 MB) at start, and
every guest allocation commits its pages; guest releases never give them back (a mapped view cannot be decommitted),
so the guest's commit charge is the high-water mark: 576 MB at the title screen, 642 MB in free roam, 704 MB after a
drive (ReIGlue 0427 logs it: `Guest memory: N MB committed on the host`). Committed pages the game never wrote cost
no RAM; the ones it wrote stay in RAM or the page file. On top come the heaps (all committed, also what Linux never
touched, like the 128 KB packet buffers), the driver's own memory (not measured here) and the driver pipeline cache
below. An estimate for NVIDIA, from the code and #406's sizes: guest 0.7 GB, heaps 0.35 GB, the driver pipeline
cache 1-1.6 GB in a long session: 2-2.7 GB of commit plus the driver's; after the fixes here 0.7 + 0.2 + at most 0.5:
1.4 GB plus the driver's. An 8 GB PC's commit limit is its RAM plus the page file (system-managed: a few GB that
grows on demand), so the limit is not the risk; the working set is: what the game wrote stays in RAM or goes to the
page file, next to Windows' own 3-4 GB, and VRAM that overflows a 4 GB card goes to system RAM too.

**What fits in 4 GB of VRAM** (the race, device-local, the GPU minute lines' split):

| | VRAM MB | Render targets | Textures |
| --- | --- | --- | --- |
| Render scale 1, scene MSAA 4x (default) | 1,106 | 38, 540 MB | 211 MB |
| Render scale 1, `scene_msaa = off` | 1,044 | 35, 494 MB | 186 MB |
| Render scale 2, scene MSAA 4x | 3,129 | 38, 2,020 MB | 372 MB |
| Render scale 2, `scene_msaa = off` | 2,942 | 35, 1,839 MB | 369 MB |

Every host render target covers the whole EDRAM's worth of rows for its pitch, whatever the MSAA, so MSAA off saves
little; render scale 2 multiplies them by four. The texture cache then grows to its 384 MB limit in 20 minutes
(`texture_cache_memory_limit_soft`), and the guest memory's GPU copy holds the 360 MB of physical memory in use.
At render scale 1 a 4 GB card has room (1.1 GB, 1.4 GB after an hour); at render scale 2 the game alone takes 3.1 GB
two minutes into the race and more as the texture cache grows toward its hard limit (768 MB), so with the desktop
on the same card there is little or no room left, and the driver moves memory to system RAM (Windows: "shared GPU
memory"), which an 8 GB PC does not have to spare either.

**Fixed** (ReIGlue 0428-0430; before and after, the same runs):

| MB | Free roam after the drive | Race |
| --- | --- | --- |
| RSS | 1,186 -> 1,035 (0428 only) | 1,204 -> 1,037 (0428-0430) |
| PSS | 933 -> 785 | 942 -> 781 |
| Heaps (anon) | 343 -> 205 | 350 -> 194 |
| Private writable mappings (the closest Linux has to commit) | 1,339 -> 1,163 | 1,378 -> 1,184 |

- 0428, the XMA decoder: FFmpeg's state is made on a context's first decode and freed when the game releases it,
  and the packet's buffer reference wraps the frame array the data was already in. Decoding is the same: with a hash
  of every decoded frame (the input frame and the PCM it gave) over a 150 s drive, 39 of the 31,006 input frames
  both builds decoded gave no output in common, and 30 of 30,662 between two runs of the unchanged build (frames
  whose output depends on the frame before).
- 0429, the driver pipeline cache: every pipeline's driver data stayed in memory until exit, to be saved, though a
  save keeps 512 MB. NVIDIA's data is about 2 MB a pipeline (#406): a session of 800 pipelines held about 1.6 GB.
  Now new data is kept only while the total fits what a save keeps. Nothing changes on RADV (10 KB a pipeline);
  with the bound lowered to 2 MB for the test (`vulkan_store_pipeline_cache_memory_mb`), the save stays within it and
  the next start creates the pipelines it holds from it. Not measured on NVIDIA here (the laptop was offline): on
  the 8 GB GTX 1650 Ti it should be the largest of the three, up to about 1 GB less in a long session.
- 0430: a shader translation's SPIR-V is freed once its shader module exists (18.6 MB after the drive, twice that in
  a long session).
- The render tests: baselines recorded from the unchanged build (`tools/render_test.py record`, 3 runs; the stored
  October baselines no longer match main in 4 scenes, fixes or not), then `check` with 0427-0430: 6 of 6 scenes
  pass, each within its run-to-run noise (dssim title 0.0000, free roam 0.0016 and 0.0007, cockpit 0.0006, photo
  mode 0.0084, race 0.0035).

**Not done, with estimates:**

- Render targets sized to the rows the game draws instead of the whole EDRAM: each of the 38 is a full EDRAM's rows
  for its pitch (`GetRenderTargetHeight`), 540 MB at render scale 1 and 2 GB at render scale 2. If a 720p scene
  uses about half of those rows (not measured), half of that: 250 MB and 1 GB. A change to the EDRAM emulation's
  aliasing, so a careful one; the biggest VRAM saving for 4 GB cards.
- A smaller texture cache on 4 GB cards (`texture_cache_memory_limit_soft = 192`): 190 MB of VRAM late in a session,
  at the cost of reloading textures unused for 30 s. A launcher setting for a Low preset.
- The driver pipeline cache on NVIDIA still holds up to 512 MB: reading entries from the file when a pipeline needs
  them (or a smaller bound on 8 GB PCs) would keep it to the entries in use. Windows cannot replace a mapped file,
  so mapping it needs the save to write elsewhere first.
- Texture packs: up to 256 MB of decoded textures wait for upload (`kMaxPendingDecodedBytes`), at 32 MB a frame;
  64 MB would keep two frames' worth queued: up to 190 MB less RAM while a pack streams. Not measured (no pack here).
- The physical memory committed whole at start (Windows): 512 MB of commit for about 360 MB used, 150 MB of commit
  but no RAM. Committing it on demand changes what the GPU may read of released memory; little to gain.
- glslang's built-in tables (20 MB) after the startup compiles; the guest heaps' page tables and descriptor pools
  (22 MB): small.

**To repeat on Windows** (the 8 GB laptop): start the game from the launcher at its defaults, then in a PowerShell
window `powershell -ExecutionPolicy Bypass -File tools\memory_snapshot.ps1` (a CSV row every 30 s on the desktop:
working set, private bytes, the GPU process memory's dedicated and shared usage, the system's commit and its limit,
free RAM, pages read from the disk a second). Play to free roam, drive 4 minutes, note the time, start the GoPro
Millennia Masters race and drive 2 minutes, note the time, quit. The `forza.log` adds the guest's commit (`Guest
memory:`) and a line a minute with the textures, render targets, GPU memory and resident memory (`GPU minute`).
Then the same at render scale 2. Paging shows as `hard_faults_s` above a few hundred while driving, and as private
bytes close to the commit limit.

## A 4-core laptop with a GTX 1650 Ti and 8 GB (#640, 2026-11-04)

**The PC.** Core i5-10300H (4 cores, 8 threads, 45 W), GeForce GTX 1650 Ti (4 GB, 50 W, driver 555.97) plus Intel UHD
Graphics (Optimus, driver 26.20.100.7985 from 2020, Vulkan 1.2.131), 8 GB of RAM (7.8 GB visible, 2.3 GB page file
that Windows grows), the internal 1920x1080 144 Hz screen, Windows 11 (build 26300), on mains power, Balanced plan.
Windows and the apps a player has open (here a desktop app of about 1 GB) leave 4 GB of RAM free before the game
starts.

**Method.** The game of main f21286d built by the launcher (TU4) on that PC; the golden TU4 save in a fresh user
folder for each run, offline, audio muted, the startup movies skipped; the launcher's settings for this PC (60 fps,
AMD FSR 1, the 720p scene with the game's 4x MSAA, render scale 1, fullscreen, vsync), each run a fresh start from
a scheduled task in the signed-in session. **Benchmark**: the built-in one (Recaro Rush, 15 s settling, 60 s
measured; its fps, 1% low and p99). **Free roam**: from the spawn on County Route 203, full throttle for 150 s with a
2.5 s brake and turn every 25 s (`FORZA_AUTOPRESS`), measured over the frames while the car moves
(`FORZA_FRAMETIME_CSV`, 2-5 km a run). Process memory every 10 s (`tools/memory_snapshot.ps1`), thread CPU times
every 15 s (the game log's thread ids name the command processor, main loop and render thread), `nvidia-smi` every
5 s. Runs of a comparison alternate, 3 each, at the laptop's steady temperature; mean of the runs, the range in
brackets where it matters.

**What it does at the launcher's settings** (GTX 1650 Ti, before fixes 1 and 2; the driver pipeline store off, as
in fix 1's runs: fix 3 has what it adds):

| | Benchmark | Free roam |
| --- | --- | --- |
| fps, 1% low, p99 | 42.7 [38.7-46.6], 28.7, 33.2 ms | 54.9 [53.7-55.6], 30.7, 30.1 ms |
| GPU (`nvidia-smi`), power | 74-92% busy, 49 W of 50 | 95-97%, 49 W |
| Command processor | 77% of a thread, 17.9 ms CPU a frame, 4.2 us a draw, 4,000-4,500 draws | 70%, 14.6 ms, 4.4 us, 3,100-3,500 draws |
| Render thread | 86-88% of a thread | 88-90% |
| Main loop, the busiest other thread (an SDK one) | 30%, 42-49% | 42%, 39-40% |
| Game process CPU | 3.4 threads (of 8) | 3.7 |
| Working set, private bytes (commit) | 1.36-1.39 GB, 2.65-2.69 GB | 1.27-1.34 GB, 2.64-2.68 GB |
| VRAM: dedicated, shared | 1.22-1.24 GB, 0.23 GB | 1.22-1.24 GB, 0.21-0.23 GB |
| System: commit of its limit, RAM free (least) | 9.5 of 10.3 GB, 2.27-2.28 GB | 9.5 of 10.3 GB, 2.28-2.32 GB |

The lowest settings the launcher has (3D scene 540p, Scene anti-aliasing Off, anisotropic filtering off, motion blur
off, 60 fps), first start of those settings: benchmark 53.8 fps, 1% low 25.9 (the pipelines of the new settings were
built in it), p99 29.5 ms.

**Where the frame goes.**
- **The GPU first.** The GTX 1650 Ti is 91-98% busy at its 50 W limit in free roam and most of the race, at the
  720p scene: the game's 4x MSAA scene in three passes and the EDRAM emulation's render targets cost far more GPU
  time than 720p suggests. Lighter scene settings are what helps (fix 2).
- **Then the four cores.** On Windows the render thread's waits for the GPU yielded on every poll
  (`gpu_wait_blocking` was off there, untested): `SwitchToThread` returns at once when no other thread waits for
  that CPU, so the thread spun for its whole wait, 86-90% of a CPU, next to a command processor at 70-77%. With
  eight threads on four cores that takes a core's share of execution and power from the command processor (fix 1).
  Once the GPU has room (540p), the benchmark stops at 54-55 fps with the command processor at 12-14 ms CPU a frame
  and 2-3 ms of game waits: that chain is #607's.
- **Not RAM.** The game's working set is 1.3-1.4 GB with 2.2 GB of RAM free and few pages read back from the disk;
  of the 2.7 GB of private bytes, 1.29 GB are write-combined or uncached (GPU-visible) mappings, the same with or
  without the pipeline file below, and much of the rest is committed but never touched (Windows trims the working set to
  0.75 GB late in a run without hard faults). The exception is the driver pipeline file (fix 3).
- **Not VRAM.** 1.2-1.35 GB of the 4 GB at render scale 1, whatever the scene settings (540p adds render targets:
  677 MB instead of 562 MB).

**Fix 1: the render thread sleeps on Windows too** (`gpu_wait_blocking` on by default everywhere,
`src/hooks/gpu_wait.cpp`; launchers that saved the old off turn it on once). Both arms with the driver pipeline
store off (fix 3) so the starts are the same:

| | Benchmark, off | Benchmark, on | Free roam, off | Free roam, on |
| --- | --- | --- | --- | --- |
| fps | 42.7 [38.7-46.6] | 46.8 [45.9-47.9] | 54.9 [53.7-55.6] | 54.7 [54.0-55.1] |
| 1% low, p99 | 28.7, 33.2 ms | 30.3, 30.9 ms | 30.7, 30.1 ms | 29.1, 29.9 ms |
| Render thread, command processor | 86%, 77% | 45%, 77% | 89%, 70% | 40%, 66% |
| Command processor a frame, a draw | 17.9 ms, 4.18 us | 16.0 ms, 3.83 us | 14.6 ms, 4.42 us | 13.4 ms, 4.26 us |
| Game process CPU, CPU clock (of 2.5 GHz) | 3.4 threads, 126-128% | 3.0 threads, 130% | 3.7, 122-125% | 3.05, 129-130% |

The benchmark gains 10% and holds it from run to run; free roam, where the GPU is the limit, keeps its frame rate
with 0.65 threads less CPU and a higher CPU clock. No freeze in the 24 runs with it on.

**Fix 2: the 540p scene on entry cards** (`launcher/forza_launcher/specs.py`: a new launcher on integrated graphics,
under 4 GB, or a GTX / RX 400-500-era card with 4 GB gets `scene_resolution = 540`, and an existing launcher.json
there once, with a notice (`config.py`, `migrated.scene_540`), where 540p is drawn; not on a Steam Deck, whose CPU is
its limit). Benchmark, render thread
sleeping, 3 runs each, rotated:

| Scene | fps | 1% low | p99 | Draws | Command processor a frame |
| --- | --- | --- | --- | --- | --- |
| 720p, 4x MSAA (the game's) | 46.8 [45.7-48.1] | 30.6 | 30.4 ms | 4,000-4,350 | 15.9 ms |
| 720p, Scene anti-aliasing Off | 52.5 [51.8-53.3] | 35.0 | 26.7 ms | 3,200-3,600 | 12.2 ms |
| 540p (one pass, 2x MSAA) | 54.3 [52.7-55.2] | 34.3 | 27.0 ms | 3,400-3,800 | 13.8 ms |
| 540p, Off, anisotropic off, no motion blur | 54.9 [53.3-56.8] | 35.2 | 26.2 ms | 3,000-3,650 | 11.8 ms |

540p alone gives 16%; the lowest settings add little to it (the CPU chain is next). 540p keeps 2x MSAA and the
sharp HUD, so it is the one the launcher picks. In free roam (3 drives each, alternating; these drives got farther
along the road than fix 1's, 4.0-4.5 km against 3.0-3.6 km, so the 720p arm is faster than there) it reaches the
60 fps cap: 57.7 [56.6-58.3] -> 59.9 [59.7-60.0] fps, 1% low 33.4 -> 48.6, p99 27.0 -> 18.0 ms, the GPU 98% ->
91-94% busy.

**Fix 3: no driver pipeline file on NVIDIA** (the NVIDIA preset sets `vulkan_store_pipeline_cache = false`;
NVIDIA launchers turn it off once). The SDK keeps each pipeline's driver data in
`shaders/local/*.vkpc` and seeds that pipeline's creation with it at the next start. On this driver not one of 404
to 949 stored pipelines was reused in any of the 9 starts that read the file (`0 pipelines created from stored
driver data, N compiled (N stale entries rebuilt)`: the creation feedback reports a miss, so each is created
twice). Each entry also grew from about 60 KB in the first session to about 2 MB once later sessions added
pipelines (a new pipeline's data held much more than itself), so the file reached the 512 MB a save keeps after
five sessions: 252 pipelines in 510 MB, all of it held in RAM while playing. Benchmark, 3 starts each, alternating, with that file:

| | File kept (0.2.6) | Off |
| --- | --- | --- |
| Shader warm-up, start to the race loading | 17 s, 24 s | 3 s, 10 s |
| Private bytes, working set (peak) | 3.29 GB, 1.96 GB | 2.72 GB, 1.42 GB |
| RAM free (least), system commit | 1.69 GB, 10.1 of 10.3 GB | 2.20 GB, 9.5 of 10.3 GB |
| fps, 1% low, p99 | 45.2, 28.8, 31.8 ms | 46.0, 28.8, 31.5 ms |

One start after the file had dropped out of the file cache spent 78 s reading it and reached the race loading
after 106 s. Without the file the driver's own shader cache does the work: no pipeline stalls over 50 ms in either
arm. On RADV the entries are reused (10 KB each), so the setting stays on for AMD and Intel.

**Intel UHD (the same laptop, `--gpu_vendor=intel`): not playable with its driver.** The first start reached the
benchmark at 1.4 fps: 3,500 `Failed to submit Vulkan sparse binds`, then upload buffers that could not be allocated,
private bytes up to 6.1 GB, the system commit at its limit (Windows grew the page file from 10.3 to 13.4 GB) and
314 MB of RAM free. Two more starts never showed a frame (`FORZA_STALL: no frame for 59 s`). With
`--vulkan_sparse_shared_memory=false` the device was lost after 20 s. The driver is from 2020 (Vulkan
1.2.131); the game needs a current Intel driver there, and the launcher already
sends this laptop to the GeForce (#89). Not measured: a current Intel driver.

**For #607.** On this CPU the command processor costs 3.7-4.6 us a draw (12-18 ms a frame at 3,000-4,400 draws)
and is 65-77% busy; with the GPU relieved it is the next limit at 54-55 fps in the benchmark.

## The GTX 1650 Ti laptop at 0.2.8, and its Intel UHD (#640, 2026-11-06)

**Setup.** The laptop of the section above (i5-10300H, GTX 1650 Ti 4 GB on driver 555.97, Intel UHD on its 2020
driver, 8 GB), on a "High performance" plan with the fans on Turbo. Main 64ea381 (0.2.8 and #713) built by the
launcher, with the launch plan the launcher makes for this PC on a first start: the 540p scene (one pass with 2x
MSAA), AMD FSR 1, 60 fps, vsync, 1920x1080 fullscreen, Vulkan, the driver pipeline file off. The golden TU4 save in a
fresh user folder for each run, offline, audio muted, a warm shader cache shared by the runs of a setting (one
warm-up session first). Scripted input through the SDK's keyboard pad from a helper in the signed-in session.
**Benchmark**: the built-in one (its result.json). **Free roam**: 3 minutes of `tools/autodrive.py` on County Route
203 at noon. **Race**: Goodyear Mountain Blast, 120 s of autodrive following the AI. Frame times from
`FORZA_FRAMETIME_CSV`, command processor counters from `--fh1_frame_cost_csv`, CPU time of every thread every 15 s
and process memory every 5 s (Win32: Toolhelp, GetThreadTimes, GetProcessMemoryInfo), `nvidia-smi` every 0.5 s.

| | Benchmark (6 runs) | Free roam (3) | Race (3) |
| --- | --- | --- | --- |
| fps | 54.7 [52.8-56.0] | 59.4 (60 cap) | 59.7 (60 cap) |
| 1% low, p99 | 37.9, 25.0 ms | 39.3, 22.9 ms | 40.3, 22.5 ms |
| GPU busy | 95-97% | 97% | 93% |
| Command processor: share of a thread, CPU a frame, draws | 68-70%, 12.0-13.0 ms, 3,300-3,700 | 59-60%, 10.7-10.8 ms, 2,630 | 57-59%, 10.2-10.5 ms, 2,810 |
| Command processor waiting for the GPU / for the game, a frame | 1.3-2.1 / 2.2-2.5 ms | 2.1-2.4 / 2.7-2.9 ms | 0-0.1 / 5.0-5.3 ms |
| Next busiest threads | three game threads 39-48%, Vulkan submission 26% | game threads 31-46%, submission 27% | game threads 34-38%, submission 21-22% |
| Game process CPU | 3.2 threads (of 8) | 3.15 | 2.8-2.9 |
| Working set, private bytes | 1.26-1.36 GB, 2.74-2.76 GB | 1.04-1.14 GB, 2.74 GB | 0.82-0.85 GB, 2.74 GB |
| RAM free (least), system commit | 3.3-3.5 GB, 7.7 of 11.4 GB | 3.5-3.6 GB, 7.7 GB | 3.7 GB, 7.8 GB |
| VRAM (`nvidia-smi`) | 1.31 GB | 1.31 GB | 1.31 GB |

Against 0.2.7 (the 540p row of fix 2 above: 54.3 fps, 1% low 34.3, p99 27.0 ms) the benchmark holds its frame rate,
with better lows. Free roam and the race stay at the cap. The GPU is the limit: 95-97% busy at its 50 W in the
benchmark and free roam, with the command processor at two thirds of a thread and 2-3 ms a frame of waits.

**Cuts tried on the benchmark.** One warm-up and one measured run each, on a copy of the warm cache:

| Change | fps | 1% low |
| --- | --- | --- |
| Scene anti-aliasing Off (`scene_msaa=off`) | 51.6 | 35.9 |
| Motion blur off | 54.7 | 38.0 |
| Anisotropic filtering off | 54.4 | 38.3 |
| Bilinear upscaling instead of FSR 1 | 57.1 | 39.3 |
| `vulkan_present_on_submission_thread=true` (#613) | 56.0 | 40.3 |
| `render_target_path_vulkan=fsi` | crash at start | |

All within the run-to-run spread of the benchmark (52.8-56.0 at the same settings; its AI race draws 3,150-3,750
draws a frame from run to run). The upscaler, the most promising, alternated 3 runs each: FSR 1 55.6 [55.0-56.0],
bilinear 56.4 [55.7-56.8], CAS 55.9 [54.9-56.6], so FSR 1 costs under 1 fps and stays. #613 (the presenter's step on
the submission thread), 3 runs each alternating on its rebased branch: benchmark 54.5 against 54.7 fps off, free roam
p99 16.7-21.6 ms against 20.3-21.5 ms and 1% low 46.8 against 42.7 (scratchpad note for #613). Nothing cheap is left on
the GPU here; what remains is the GPU's own work at 540p, which the SDK cannot split per pass yet (no GPU timestamps; now `fh1_gpu_profile`, below).

`render_target_path_vulkan=fsi` (experimental) stops at once on this NVIDIA card: `Unhandled read fault at host 0x15
(pc rexgpu-xenos.dll+0x30E00)` on the command processor thread (guest thread F8000018), the host chain
`rexgpu-xenos.dll+0x30E00`, `+0xC9858`. On an Iris Xe it froze after the first frame (#201).

**The Intel UHD.** With "Intel" chosen as the graphics card the launcher plans Direct3D 12 (#672: its Vulkan driver
is 1.2.131), the 540p scene and 30 fps. On Vulkan the game still shows no frame (`FORZA_STALL: no frame for 59 s`), now
without running out of memory.
- On Direct3D 12 the picture is right (none of #201's Vulkan artifacts in free roam, the cockpit or the race), but
  slow: 5.5-5.7 fps in free roam (`FORZA_FPSLOG` 7.2-7.4 over the session), 4.9 fps in the benchmark, with frames of
  0.5-1.2 s. Private bytes 2.1-2.3 GB, working set 1.6-1.7 GB, 2.9 GB of RAM free.
- `FORZA_FRAMETIME_CSV` wrote nothing at 30 fps: the frame time hook leaves the game's own timing at the console
  rate before the CSV. It now writes a row per guest swap there (`frame_time.cpp`); rows of the main loop would be
  wrong there, as the main loop keeps running at 30 when the render thread stops (it did, below).
- The benchmark did not start in 2 of 3 runs on the first day (`benchmark_start_timeout`), and a race froze at its
  start: the render thread waited in the HUD command buffer's slot wait (`FORZA_STALL` chain `82419284 8249E108
  824A1E6C 8247F9E4 82616BFC 8261E1C0`, sub_824A1E40), right after `FFCB: woke the HUD command buffer worker: slot
  ready, after 41-77 ms` (2 ms on the GeForce): #512's wake (`ffcb_wake.cpp`) wakes the worker every 2 ms while a slot
  is not ready and falls back to the game's infinite wait after 2 s.
- Those runs were the first with an empty Direct3D shader cache of the driver (`%LOCALAPPDATA%\D3DSCache`). With it
  warm, 24 benchmarks alternating (8 each) did not freeze in any arm: the defaults 7 completed (4.8-5.1 fps), with
  `FORZA_FFCB_WAKE=0` 6, with the first wake after 250 ms (`FORZA_FFCB_WAKE_AFTER_MS`, test only) 7; the 4 others, in
  all three arms, ended in `Graphics device lost` as the race started (frames of up to 1.2 s; Windows resets a GPU
  that runs 2 s). 12 more with the game's cache emptied each time (4 each: the defaults, the wake off, the wakes paced
  to one per 250 ms): 11 completed, 1 device lost.
- With the driver's cache moved aside before each run, 11 of 12 benchmarks (4 per arm; the 12th: device lost) ended in
  `benchmark_start_timeout`, the wake off included, with no wake logged: the game kept presenting at about 10 fps
  after 7-12 s frames of shader compiles, and the event did not load within the benchmark's 5 minutes. Race sign-ups
  on a cold driver cache (3 per arm): 22 s without a frame after the move; with the wake off 0 of 3 crashed, with the
  defaults 0 of 3, with the wakes paced (one per 250 ms) 3 of 4 crashed with `Unhandled read fault ... guest
  0x00000004` on the render thread (chain `8249E118 824A1E6C 8247F9E4 82616BFC 8261E1C0`, lr 824469B8, r4 = 0: the
  slot's state applied as null, the signature of #66). Pacing the wakes is therefore not the fix; the defaults stay.
- On the GeForce the benchmark starts at 30 fps (30.0) and on Direct3D 12 (44.8, 45.0 fps).
- **The benchmark's start limit (#725).** With an empty Direct3D shader cache of the driver the Intel UHD keeps
  presenting at about 10 fps after 7-12 s frames of shader compiles, but the event does not load: 11 of 12
  benchmarks timed out at the old 5 minutes in every #512 arm, and 2 of 2 at the new limit (it now waits while
  frames come, up to 30 minutes; `benchmark_start.h`). That load stalls outright; with a warm driver cache it does
  not happen.
- **Device lost on Direct3D 12 (#725).** Vulkan ends a submission after 1000 draws on integrated GPUs
  (`vulkan_submission_draw_limit`, #268) so that none runs past Windows' 2 s GPU timeout; the Direct3D 12 path had
  no such limit. ReIGlue 0441 (its own PR; not yet in the pinned SDK) adds `d3d12_submission_draw_limit` (-1 = 1000 on UMA GPUs).
  Benchmark race starts on the Intel UHD, warm caches, the SDK DLLs of one toolchain with and without it,
  alternated: without the limit 1 of 10 lost the device, with it 0 of 10 (4.8-5.1 fps either way). With the earlier
  runs without it (5 of 36) that is 6 of 46 without and 0 of 10 with: a hint, not proof. On the GeForce (not UMA, so
  no split) the benchmark gives 55.3 fps on Vulkan and 45.5 on Direct3D 12, as before.

## The benchmark's event that never loads after an empty driver shader cache (#725, 2026-11-07)

**The symptom.** On the Intel UHD with its driver caches emptied (Direct3D 12, 540p, 30 fps), the benchmark asks for
its event (`BENCHMARK: fixed event FR02 requested`) and the game takes the request: the world pauses (`Wheel: not
driving (pause)`), and then nothing more happens. The world stays paused and keeps updating, the game draws a
steady 10 fps for 29 minutes, and the load never begins (`benchmark_start_timeout`, 4 of 4 runs). In a warm run the
world stops updating 4.5 s after the request (`Wheel: still not driving (menu or loading)`) and the race loads.
After the first minute nothing is held and nothing spins. The busiest guest thread is F80012FC (one of four job
workers started at 83075D18), at 15% of a core.

**On Linux:** lavapipe (Mesa's CPU Vulkan driver, `VK_DRIVER_FILES=.../lvp_icd.x86_64.json`, about 2 fps) with an
empty scratch `MESA_SHADER_CACHE_DIR`, the benchmark from a fresh profile. Right after the request it has the long
frames the Intel UHD has: waits of 1-3.4 s for submissions full of the transition's new pipelines.

| Build | Mesa cache | Runs | Race loads |
| --- | --- | --- | --- |
| No main-loop wait (`FORZA_MAIN_WAITS_RENDER=0`, main before #738) | empty | 7 | 0: stuck as on the Intel UHD in 5; the #725 null read 6 s after the request in 1; no frame (`benchmark_stalled`) in 1 |
| No main-loop wait | warm | 2 | 2 |
| #738 (the main loop waits for a render pass held over 1 s) | empty | 7 | 6; the 7th hung in the HUD slot wait (lr 8249E108) after the race loaded |

So the event doesn't load when the render thread is held for seconds right after the request while the main loop
runs on. Compiles alone don't cause it: with a warm cache the same slow renderer loads. Keeping the main loop with
the render thread lets it load. On the Intel UHD, #738's first version (250 ms, waits for the HUD worker counted as
held, the waits capped at 10 s, one run hitting the cap) did not help. The current version is untested there.

**Where it stops.** The request is taken: the activity's state machine (activity + 0x164) accepts `load_into_event`.
Then its current state object (state machine + 0x2C) stays the same for minutes. In a run that loads, the activity
is freed within 6 s. The state machine is script-driven, so its logic is in the game's data, not the executable:
- the definition object's vtable is 8209004C, with methods 829B9AD0, 829B9B78, 829B9B80, 829BB2A0 and 829BC990;
- 829BB2A0 sets the script context (833F6CA0) and runs 82F111F0;
- 829BC990 drains its event queue.

So this is a waiting state machine: not a dropped request, and not a stuck file load (no reads are pending). What
the script waits for is the next step.

**The HUD worker, for reference** (render object `ro`):
- The UI object (`ro + 0x978`, made by sub_82D13BC0 with pointers to `ro + 0x1A0C` and `ro + 0x1A19`) asks for a
  HUD frame in sub_82D11BA0. If the pending flag `ro + 0x1A19` is clear, it sets it and the worker's event
  `ro + 0x1A0C`.
- The worker (sub_824A2080, guest thread F8001204) waits for that event, records with
  `sub_824A3558(ro, *(ro + 0x1A1C) == 1)`, sets `ro + 0x1A10`, and waits again. `ro + 0x1A18` ends it.
- The render pass (sub_826164D0) clears the pending flag, builds its command list (sub_8247F818; `ro + 0x1A1C` = 1
  for the HUD path, 0 without), then, if the pending flag is set again, waits for `ro + 0x1A10` and clears it.

**Diagnostic.** When the event was requested but no race has started 2 and 10 minutes later, and when the benchmark
gives up, the log lists every guest thread's call chain:
`BENCHMARK: the race has not started N s after the event's request (...); guest threads below (#725)`. Players' logs
then show where it waits. Laptop runs ended with `terminate()` never reached the dump at window close.
## The render thread held for seconds breaks the HUD command buffers (#725, #582, 2026-11-07)

**The repro.** `FORZA_TEST_SWAP_STALLS="s:ms,..."` (testing only, `src/debug/frame_dump.cpp`) holds the render
thread in its swap for `ms` once, `s` seconds after the first swap, as a slow GPU compiling shaders does (an Intel
UHD with an empty Direct3D shader cache of the driver: frames of 7-12 s right after free roam appears). On Linux,
the benchmark from a fresh profile with `12:6000,20:8000,30:10000,42:10000,54:8000` (the first hold lands a few
seconds after free roam has loaded):
- #512's wake on (the default): 2 of 2 crashed 6 s later, `Unhandled read fault at host 0x100000004 = guest
  0x00000004` on the render thread (F80011AC), chain `8249E118 824A1E6C 8247F9E4 82616BFC 8261E1C0`, lr 824469B8,
  r4 = 0. The signature of #66, #457, #540 and #582 (#582: the same on WARP at about 1 fps), and of the Intel UHD's
  cold first races.
- `FORZA_FFCB_WAKE=0`: no crash, but no frame again (`benchmark_stalled` after 60 s).
- Without the knob the same build starts the benchmark as before (below).

**The HUD's fixed-function command buffers** (TU4): a ring of 12 slots of 60 bytes at render object +0x470.
- The worker records slot `produce` (+0x760): sub_8249E800 begins recording into the slot's command buffer
  (+0x14), sub_824198A8 ends it, signals the slot's event (+0x1C) and moves `produce` on (mod 12).
- The render thread's command list (sub_8247F818) consumes slot `consume` (+0x75C): sub_824A1E40 ->
  sub_8249E0E0 waits for the slot's event (sub_82419270, infinite) and applies its command buffer
  (sub_824469B0(device, slot+0x14, 0)), then moves `consume` on. Nothing keeps `produce` within 12 of
  `consume`: with the render thread held, the worker goes round the ring, and the slot the render thread then
  applies has no command buffer (the null read).
- A throttle that made begin wait while all 12 slots were recorded and not consumed (an experiment, not kept)
  removed the crash (0 of 4) but not the hang: the worker waited for a free slot for 30 s while the render thread
  waited, after its command list, for the event at render object +0x1A10 (sub_826164D0, when +0x1A19 is set; not
  the ring).

**Who sets +0x1A10.** The FFCB worker (guest thread F8001204, from 824A2118, called from 82B3A4FC), once per main
frame, after it has recorded that frame's slots; the render pass waits for it every frame (from 82616B9C and
82616C28, through sub_82419270). The render thread's waits pair with the worker's signals only while the two stay
within a frame or two of each other. During a hold of seconds the main loop does not wait for the render thread
(it free-runs some 1,300 frames in a 10 s hold), the worker records and signals for every one of them, and when the
render thread comes back its consume index and the worker's produce index no longer point at the same frames: it
either applies a slot the worker is rewriting (the null read) or waits on an event whose signal was spent on an
earlier frame (no frame).

**The fix** (`src/hooks/frame_time.cpp`, `src/hooks/ffcb_wake.cpp`): the main loop (sub_8247C168) waits while the
render thread has been working inside one render pass (sub_8259F3E8) for more than 1 s, until the pass ends, for at
most 10 s. The render thread's waits for the HUD worker (the slot wait and the +0x1A10 waits) do not count: the
worker is paced by the main loop, so the main loop must not wait for a pass that is waiting for it. A pass of
normal length never triggers it; `FORZA_MAIN_WAITS_RENDER=0` turns it off, `FORZA_MAIN_WAITS_RENDER_MS` (testing)
sets the limit. Linux, the knob above, the benchmark at 30 fps:

| Build | Wake on | Wake off |
| --- | --- | --- |
| Before (8b83c6c) | 2 of 2 crashed (null read) | no frame (`benchmark_stalled`) |
| Fix, `FORZA_MAIN_WAITS_RENDER=0` | crashed (null read) | no frame (`benchmark_stalled`) |
| First fix (250 ms, worker waits counted) | 3 of 3 complete, 1% low 28.4-28.7 | 3 of 3 complete, 1% low 28.7-28.8 |
| Fix, 250 ms | 3 of 3 complete, 1% low 23.7-28.1 | 3 of 3 complete, 1% low 26.2-27.8 |
| Fix, 500 ms | 1 of 1 complete, 1% low 28.1 | 1 of 1 complete, 1% low 26.9 |
| Fix, 1 s (the default) | 2 of 2 complete, 1% low 28.0-28.3 | 1 of 1 complete, 1% low 27.7 |

All at 30.0 fps (the lower lows with another game on the PC), the main loop waiting 4 times per run. Holds of
1-4 s instead (13 in one benchmark, `12:1500,16:2500,20:4000,...`) do not crash without the fix either: 2 of 2
complete (and 2 of 2 with it at 1 s). Without the knob: the benchmark 5 of 5 complete (30.0 fps, 1% low 26.9-28.5),
3 minutes of free roam 3 of 3 without a crash (30.0 fps, 1% low 25.7-25.9), and the main loop waited 0 times.

**WARP** (`--gpu_backend=d3d12 --d3d12_adapter=-2`, the GTX 1650 Ti laptop's i5-10300H; #582's renderer): the game
reaches free roam and runs 7 minutes there without a crash in every arm (12 runs). Its frames take 100-500 ms, so a
low limit or counting the worker waits costs it:

| Build | Free roam fps | Frames over 1 s |
| --- | --- | --- |
| No wait (`FORZA_MAIN_WAITS_RENDER=0`) | 7.4, 7.4 | 0, 0 |
| First fix (250 ms, worker waits counted) | 4.8, 4.8 | 597 in 20 min, 241 in 7 min |
| 250 ms | 6.2, 6.1 | 115, 113 |
| 500 ms | 6.2, 6.5 | 39, 45 |
| 1 s (the default) | 6.8, 7.0 | 12, 11 |

At 1 s WARP still loses 0.4-0.6 fps: some of its passes still take over 1 s once the main loop waits, which a run
without the wait does not show (its longest frame: 504 ms). Which other wait of the pass depends on the main loop is
not known yet.

**The Intel UHD with cold driver caches** (D3DSCache, NVIDIA's and Intel's caches moved aside for each run; the
benchmark from a fresh game cache, 540p, 30 fps, the first fix): on Direct3D 12 the first minute has frames of 7-12 s,
the main loop waited there 10-13 times, then the game presents a steady 10 fps for the remaining 29 minutes and the
event (FR02, requested about 3 minutes in) never starts: `benchmark_start_timeout`, 2 of 2 with the fix and 2 of 2
with `FORZA_MAIN_WAITS_RENDER=0`; no crash, no device lost. That stall is not the ring: after the first minute no
pass is held and nothing spins (the busiest guest thread, F80012FC, at 15% of a core); it stays open in #725. On
Vulkan its old driver (1.2.131) shows no frame at all, 4 of 4 in both arms (#672). The GeForce, warm caches, the
benchmark with the fix: 52.7 and 54.8 fps (52.8-56.0 before), the main loop waiting 0 times.

## Descriptor reuse in the command processor (#192, #179, 2026-11-08)

The biggest cost of the GPU command processor was descriptor setup (20-23 % of its CPU time, #607 above): every
draw wrote a new constants set and a new texture set for each stage, and acquired its samplers and requested its
textures again. ReIGlue 0442-0445 reuse what has not changed, on by default; each part has an off switch:

- `vulkan_dynamic_constants` (0442, Sean O'Connor's #179; at startup): the guest constants are bound as dynamic
  uniform buffers, the draw's slices selected by offsets at bind time, and the frame's sets with the same buffers
  and ranges are used again.
- `vulkan_binding_request_reuse` (0443, Sean's #192): within a submission, unchanged samplers are kept and a texture
  request whose bindings are all in sync is skipped. 0443 also clears the bound flags of the descriptor sets a
  pipeline layout change makes incompatible (missing upstream).
- `vulkan_texture_descriptor_reuse` (0444): the texture sets written in a frame are remembered by their exact
  contents (layout, then every view and sampler handle) and used again, still bound if it is the bound one;
  forgotten at each frame, when the pools are reset and when any view or sampler is destroyed (a later handle may
  have the same value). Sean's first version of #192 reused texture sets by comparing a draw with the previous
  one only, and drew wrong textures; it was removed.

`FORZA_VK_DYNAMIC_CONSTANTS`, `FORZA_VK_BINDING_REUSE` and `FORZA_VK_TEXTURE_SET_REUSE` (0/1) override the cvars
for A/B runs; `FORZA_VK_BINDING_VERIFY=1` runs the full sampler and texture path on predicted hits and logs a
mismatch; `FORZA_VK_BINDING_STATS=1` logs the hits every 300 frames.

**Method.** TU4 game of main 2e28667 with the ReIGlue branch's plugin and runtime, Ryzen 7 7800X3D, RX 9060 XT
(RADV), the built-in benchmark (Recaro Rush, 60 s measured) uncapped (`--frame_rate=240`), 1280x720, the golden
TU4 save in a scratch `--user_data_root`, scratch XDG dirs and caches (warmed by a run first), audio muted,
`nice -n 10 ionice -c3`, BENCH lock held, each run started with a 1-minute load under 2 and no compiler running.
Six cores: `taskset -c 0-5` from the start (a Ryzen 5 5600X stand-in; only Mesa's disk cache threads escaped it).
The command processor's CPU time from `--fh1_frame_cost_csv` over the measured minute. Each race's AI draws a
different amount (4,070-5,170 draws a frame), and the frame rate follows that; the command processor's time per
draw is the steady figure.

**All three off and on**, three interleaved pairs each:

| | fps | 1% low | p99 ms | Command processor, ms a frame | us a draw |
| --- | --- | --- | --- | --- | --- |
| 8 cores, off | 133.4 (126.8, 146.2, 127.2) | 89.4 | 10.22 | 6.04 | 1.319 |
| 8 cores, on | 147.3 (145.0, 150.7, 146.1) | 94.7 | 9.44 | 5.31 | 1.221 |
| 6 cores, off | 136.8 (135.1, 145.7, 129.8) | 90.7 | 10.15 | 5.88 | 1.317 |
| 6 cores, on | 142.2 (151.6, 149.4, 125.6) | 95.0 | 9.51 | 5.45 | 1.221 |

- Time per draw: 1.311-1.330 us off and 1.194-1.243 us on in all 12 runs (7.4 % less on both core counts). With
  the same draws (about 4,130 a frame): 146.2 to 150.7 fps on 8 cores, 145.7 to 149.4 on 6.
- Hits in the race: about 95 % of the constants sets, 75-82 % of the texture sets (30-40 % still bound), 54 % of
  the sampler acquisitions and 57 % of the texture requests; the texture set memo was never cleared within a frame.

**Each part**, 8 cores (one off run and two of each, then two pairs of the other two parts without the texture set
memo against all three, the same session):

| | fps | 1% low | Command processor, us a draw |
| --- | --- | --- | --- |
| Off | 138.1 | 90.6 | 1.320 |
| Dynamic constants only | 132.9, 134.1 | 87.9, 91.3 | 1.264, 1.265 |
| Sampler and texture-request reuse only | 147.0, 147.8 | 94.0, 96.5 | 1.294, 1.301 |
| Texture set memo only | 135.6, 133.2 | 89.6, 86.3 | 1.336, 1.314 |
| Dynamic constants and request reuse | 139.8, 139.8 | 89.5, 90.1 | 1.273, 1.229 |
| All three | 146.1, 141.1 | 94.4, 91.9 | 1.227, 1.243 |

The dynamic constants save the most (4 % of the time per draw), the request reuse 2 %. The texture set memo alone
saves nothing measurable (hashing the set costs about what writing it did); with the other two it is about 1 % more,
within the runs' spread, and it also leaves 30-40 % of the texture sets bound, which the submission thread then does
not bind again (not measured separately). The frame rates of the single parts follow their races' draws (4,140-4,680
a frame).

**Free roam**, 3 minutes from the golden save's spawn (throttle with a brake and a turn every 25 s), 45-210 s, 8 cores:
off 180.8 fps, 1% low 91.3, the command processor 4.18 ms a frame (1.412 us a draw); with the defaults (all three
on, no overrides) 183.1 fps, 1% low 96.7, 3.97 ms (1.344 us a draw, 4.8 % less).

**Correctness.**

- Benchmark with all three on, `FORZA_VK_BINDING_VERIFY=1` and the Khronos validation layer (1.4.341): complete, no
  sampler or texture-request mismatch, no validation error (only `ShaderOutputNotConsumed`, as without the change).
  With the defaults and synchronization validation (`VK_KHRONOS_VALIDATION_VALIDATE_SYNC=true`): no hazard, no error.
- `tools/render_test.py`: baselines recorded with all three off (three runs), then a check with all three on on
  the same build: 6/6 scenes pass within their noise (race start dssim 0.0030 of 0.008, photo mode 0.0110 of 0.016).
- VR true stereo's right eye (which resets every fetch constant and texture binding), the native HUD's texture
  cache, texture packs and the upscalers' own pipelines were checked in the code, not in a run.
- Only RADV is measured. The Vulkan path is the same on NVIDIA, Intel and Windows (dynamic uniform buffers are core
  Vulkan 1.0; the constants need 5 of the 8 every device has); Direct3D 12 does not change.

**With the command processor on two threads (0420, #474).** The ReIGlue branch with 0420 and 0442-0445, the game of
#762 (the split on by default with 6 or more cores), the same PC and method; all off (`--gpu_split_command_processor=false`,
the three reuses off) against all on (the split and the three reuses), three interleaved pairs on each core count:

| | fps | 1% low | p99 ms | Draw thread, us a draw | Draws a frame |
| --- | --- | --- | --- | --- | --- |
| 8 cores, off | 123.6 (133.2, 117.5, 120.0) | 83.0 | 11.09 | 1.333 | 4,924 |
| 8 cores, on | 153.5 (155.4, 161.0, 144.1) | 97.6 | 9.08 | 1.046 | 4,558 |
| 6 cores, off | 131.5 (127.3, 136.1, 131.0) | 87.6 | 10.44 | 1.339 | 4,579 |
| 6 cores, on | 153.0 (152.4, 157.5, 149.2) | 96.3 | 9.35 | 1.034 | 4,539 |

- With the same draws: 120.0 to 144.1 fps on 8 cores (about 5,030 draws a frame), 136.1 to 157.5 on 6 cores
  (about 4,400). The draw thread is the recorder when split (the decoder's CPU is on another core); its time per
  draw drops 22-23 %, the reuses' 7 % and the PM4 decoding that moves to the decoder.
- The memos are only used on the thread that runs the draws (the recorder when split): the decoder writes only its
  shadow register file and records the writes, which the recorder applies (and with them the fetch constant
  writes that the sampler and texture-request reuse follow).
- Render tests with all on against the all-off baselines above: 6/6 within their noise (race start dssim 0.0027 of
  0.008, photo mode 0.0087 of 0.016). No crash, no binding mismatch.

**Windows, NVIDIA** (the GTX 1650 Ti laptop: i5-10300H, 4 cores, Windows 11, Vulkan; the split stays off with 4
cores): the branch built by the launcher from a source checkout, the built-in benchmark uncapped at 1280x720, all three
reuses off against on with `FORZA_VK_BINDING_VERIFY=1`: 47.8 fps (one off run; the other lost the device, below)
against 46.7 and 47.2: the GPU is the limit there. The command processor 3.83 against 3.57 us a draw (7 % less, with
the verification's extra work on), about the same hit rates as on RADV (92 % of the constants sets, 73-83 % of the texture
sets), no mismatch; screenshots at the same point of the race look the same. One run with the reuses off lost the
device 57 s into the measured minute (`VK_ERROR_DEVICE_LOST`, draw 431 of 2,381, #551), as players see without the
change.

## Where the GPU frame goes on a GTX 1650 Ti, and the stencil of render target transfers (#640, 2026-11-08)

**Tool.** `--fh1_gpu_profile=5` (ReIGlue 0460) logs every 5 s the GPU time of a frame's command
processor submissions, by kind and by render pass, from Vulkan timestamps written where the kind
of work changes; `--fh1_gpu_profile_csv=FILE` also appends the reports to a CSV,
`--fh1_gpu_profile_shaders=true` splits render passes by pixel shader. The presenter's own work
(FSR 1 to the window) is outside those submissions. On, it costs the benchmark about 3% (53.2-53.9
fps against 55.3-55.8 without it).

**The laptop of the sections above** (GTX 1650 Ti 4 GB, driver 555.97, i5-10300H, Windows 11) at
the launcher's settings for it (the 540p scene, AMD FSR 1, 60 fps, Vulkan), main 6e891d4 built by
the launcher, the built-in benchmark, warm caches. The measured minute, ms of GPU time a frame:

| Work | ms a frame | Notes |
| --- | --- | --- |
| Guest draws | 7.8 | the scene (960x540, 2x MSAA) 3.9; the reflection cube 0.7; shadow cascades, post-processing, bloom 0.3-0.6 each |
| Render target ownership transfers | 5.3 | 41 a frame; 4.5 of them depth: the shadow maps and scene depth reinterpreted between 4x MSAA and 1x at the same EDRAM tiles |
| Texture loads | 2.3 | resolved screen and shadow textures: the 1024x1024 shadow depth (k_24_8, 4 a frame) 0.6 |
| Resolves | 1.7 | the render target dumped to the EDRAM buffer 0.9, the copy to guest memory 0.8 |
| Uploads, swap | 0.5 | |
| **All** | **17.7** | of a 18.8 ms frame (53 fps): the GPU is the limit, as `nvidia-smi` says (97%) |

The transfers were the surprise. Without `VK_EXT_shader_stencil_export`, which NVIDIA's driver
lacks (AMD's and Intel's have it), the SDK copies a depth render target's stencil in eight
full-size draws, one per bit, each reading the source and discarding where its bit is clear: 9
passes over every transferred pixel. The game draws each shadow map as a 4x MSAA depth target and
reads it as a 1x one twice the size (and the reverse at the next frame), so every frame moves
about 4 such surfaces each way. Skipping the bit draws altogether (a test, wrong in general) took
the transfers from 5.3 to 1.9 ms and the frame from 17.4 to 14.5 ms. At a render scale of 2 those
surfaces have 4 times the pixels, which fits the NVIDIA reports of a 1440p scene costing far more
than its pixel count (#700, #500) and of ultrawide (#546).

**Fix 1 (ReIGlue 0461, `vulkan_transfer_stencil_bit_mask`, on).** Before such a transfer a compute
pass ORs the stencil of the rows each depth source is read from into eight words, and each bit's
draws are conditional on its word (`VK_EXT_conditional_rendering`, which every desktop driver
has). The stencil is still cleared to 0 before the bit draws, so the result is the same as
before; a source whose stencil is 0 (FH1's shadow maps) costs the compute pass and no draws.
Devices with stencil export and transfers from color are unchanged. In the benchmark, 85% of the
depth transfers had their bit draws made conditional; transfers 5.3 -> 3.5 ms, the frame 17.4 ->
16.0 ms.

**Fix 2 (ReIGlue 0462, `vulkan_direct_unscaled_texture_upload`, on).** NVIDIA's direct texture
upload (0071, 0406: the texture written straight into a storage image, without the scratch buffer
and its buffer-to-image copy) applied only to scaled resolves, so at render scale 1 FH1's resolved
screen and shadow textures took the copy path. The same shader reads the shared memory at scale 1:
texture loads 2.2 -> 1.9 ms.

**Measured** (the laptop above, 3 runs each way alternating, warm caches, the benchmark's minute):

| Benchmark | Before | After |
| --- | --- | --- |
| 540p scene, 60 fps cap (the launcher's settings), fix 1 alone | 55.5 fps [55.3-55.8], GPU 97% busy | 58.1 fps [57.8-58.6], GPU 94% |
| 540p scene, uncapped (`frame_rate=120`, vsync off), fix 1 alone | 55.8 fps [54.4-56.5] | 59.7 fps [59.0-60.9] |
| 540p scene, 60 fps cap, fixes 1 and 2 | 55.1 fps [52.7-57.0], GPU 97% | 58.5 fps [56.3-59.9], GPU 92% |
| Render scale 2 (the 1440p scene), 60 fps cap, fixes 1 and 2 | 19.0 fps [18.9-19.1], 1% low 16.6 | 23.7 fps [23.4-23.8], 1% low 20.8 |

At render scale 2 the profile shows what the reports describe: of 52.1 ms of GPU time a frame,
transfers took 19.4 ms (37%), more than the guest's draws (15.7 ms); with the fixes 9.6 ms, the
frame 41.8 ms. What is left of the transfers there is mostly depth made from color (the stencil
bits of a color source are not masked) and the depth copies themselves. Pictures (free roam at
noon, two spots, both settings) show the same shadows and scene; the result is the same by
construction. AMD and Intel drivers have stencil export, so fix 1 changes nothing there; fix 2 is
NVIDIA only, like 0071.

## The split command processor: decoding, register writes and the hand-off (#607, 2026-11-08)

Item 2 of #607 (PM4 decode and register writes, 19-25 % of the command processor in the ranking above), with
the command processor on two threads (0420, on by default with 6 or more cores): which thread is the limit now,
where the decoder's time goes, and ReIGlue 0450-0452.

**Method.** One build of the game of #764 (its branch before main's #761 was merged in, so the swap setting is
on the command line) with ReIGlue a4aff83's plugin (#764's pin) against one built from ReIGlue 0450-0452, both with `-gline-tables-only` (the same code); the built-in benchmark uncapped (`--frame_rate=240`),
`--gpu_split_command_processor=true --vulkan_present_on_submission_thread=true`, 1280x720, the PC's default
preset, the golden TU4 save in a scratch `--user_data_root`, scratch XDG dirs and warm scratch caches, audio
muted, the hidden display, `nice -n 10 ionice -c3`, BENCH lock held, no other game; 6 cores is `taskset -c
0-5`. Per thread: CPU time from `/proc/<pid>/task/*/schedstat` between the measured minute's start and end;
the recorder's time per draw from `--fh1_frame_cost_csv` (split, the CSV times the recorder); `PERF_CPUPROF`
over the measured minute in separate runs. The races draw 4,150-4,890 a frame and the frame rate follows that
(about 0.4-0.7 us a draw), so the frame rates below are also given fitted to the same draws.

**Where the time goes (base, 8 cores, 176.6 fps, 4,145 draws a frame).** Neither thread is saturated: the
recorder ("GPU Recorder", the draws) 4.35 ms a frame and 77 % busy, the decoder ("GPU Commands") 3.52 ms and
62 % busy. The recorder is the longer one; most of the rest of its frame it waits for the game.

- Decoder: 24 % of its samples were `RecordWaitProgress`, polling while the recorder worked. About 13
  `WAIT_REG_MEM` memory waits a frame, about 250 us each, all on three words written by scratch register
  write-back (Direct3D's fences: `SCRATCH_ADDR` + 4 x register 0, 1 and 5), never on an `EVENT_WRITE_SHD`
  fence and never with the recorder idle; the decoder polled after every batch the recorder finished and the
  recorder signalled it after each one. 26-30 % the elision of unchanged registers
  (`PacketWriteRegistersFromMem`), whose hot lines are the first loads of the game's freshly written command
  data, 11 % `ExecutePacket` (the same: the first word of each packet), 8 % `sched_yield` (waiting for the
  game's next commands), 2-3 % growing the record vector (zero-filled, then overwritten).
- Recorder: the register work (applying the recorded runs: `ExecuteRecordBatch`, the backend's
  `WriteRegistersFromMem`, `WriteRegister`, the byte swaps) 12.3 % of its samples; the rest is draw setup
  (#607 item 4, flat), `memcpy` 7.5 %, `mprotect` 4.2 %, the hand-off's signals and lock wakes 3.1 %.

**Changes (ReIGlue 0450-0452, split path only).**

- 0450: the decoder records register runs in host byte order (it swaps them for its shadow anyway) and marks
  runs whose registers the backend only stores; the recorder copies those into the register file with no
  call (0360's direct copy, off while split).
- 0451: the elision builds a 64-register bit mask of the changed values (SSSE3) and reads the runs from it;
  the record words grow without zeroing; single writes of plain registers (draw initiator, index buffer) are
  plain runs too.
- 0452: the decoder notes where the recorder will store a word (scratch write-back from its shadow,
  `EVENT_WRITE_SHD`) and in which batch; a wait for such a word sleeps until the batch before it, polls only
  during that batch, and is woken once (at most 100 us at a time, as before for the CPU's writes). The recorder
  wakes the decoder only at the batch it waits for. While the recorder has run everything published, a batch
  goes after 4 draws instead of 32.

**Measured**, three interleaved pairs each (mean, per run in brackets for the draw-bound columns):

| | fps | 1% low | p99 ms | Draws a frame | Recorder us a draw | Decoder ms a frame | Decoder busy | Process CPU ms a frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 cores, base | 168.5 (164.0, 176.9, 164.6) | 110.5 | 7.89 | 4,614 | 1.018 (1.013, 1.033, 1.009) | 3.76 | 63 % | 19.5 |
| 8 cores, 0450-0452 | 168.6 (168.2, 166.9, 170.6) | 110.9 | 7.83 | 4,755 | 0.985 (0.985, 0.972, 0.997) | 2.69 | 46 % | 18.5 |
| 6 cores, base | 176.4 | 109.9 | 7.85 | 4,206 | 1.036 | 3.47 | 61 % | 18.2 |
| 6 cores, 0450-0452 | 172.7 (171.8, 170.9, 175.3) | 109.2 | 8.05 | 4,482 | 1.000 (0.993, 1.018, 0.989) | 2.56 | 44 % | 17.7 |

- The decoder's CPU time falls 26-28 % a frame (0.82 to 0.57 us a draw, -31 %), almost all of it the polling;
  in the profile `RecordWaitProgress` (23.5 %) is gone and the targeted wait takes 0.9 %; the recorder's
  signals to the decoder (1.1 % of it) are gone.
- The recorder's time per draw falls 3.2 % on 8 cores and 3.5 % on 6, every run of the change below every base
  run; its register work 12.3 % -> 9.8 % of its samples (0450-0451 alone, one profile run each).
- The frame rate: fitted to the same draws a frame, 168.2 -> 171.7 fps on 8 cores (+2 %) and 174.2 -> 173.6 on 6
  (the same). The process uses 0.5-1.0 ms of CPU a frame less.
- 0451's vectorised compare changes little by itself: the elision is bound by reading the game's command data
  for the first time, not by the compare.

**Render check** (2026-11-11, rebased on ReIGlue 5408d20 and main: the base is 5408d20, against 0450-0451 and
0450-0452 on it; same game build, split on). `tools/render_test.py` (festival session, 6 scenes) against the
stored baselines, interleaved, passes per scene:

| | Runs | All 6 pass | title | free roam midday | free roam dawn | cockpit | photo mode | race start | Scenes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Base | 6 | 4 | 5 | 4 | 5 | 5 | 4 | 5 | 28/36 |
| 0450-0451 | 3 | 1 | 2 | 2 | 3 | 3 | 3 | 1 | 14/18 |
| 0450-0452 | 6 | 4 | 5 | 5 | 5 | 5 | 5 | 6 | 31/36 |

The first check's 2 of 6 (on a4aff83) was the run's scene, not the picture: the base fails the same way. In one
run of the base and one of 0450-0452 the car rests a little differently at the spawn point (photo mode's yaw
reads 280.0 instead of 280.1, or the trees move against the car), which fails cockpit, both free roam scenes and
photo mode together; the other failures are traffic and drivatar name tags in view, the race clock up to 2 s apart at
the start, and the title's attract camera a frame early (0.0150 against 0.015). When the pose matches, 0450-0452
matches the baselines as closely as the base (free roam midday 0.0009 and dawn 0.0006 against 0.005 and 0.01).

**Benchmark on the rebased stack** (same method as above, 8 cores, base 5408d20 against 0450-0452 on it, base,
change, change, base):

| | fps | 1% low | p99 ms | Draws a frame | Recorder us a draw | Decoder ms a frame | Decoder busy | Process CPU ms a frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Base | 171.8 (173.4, 170.3) | 112.1 | 7.72 | 4,339 | 1.052 | 3.67 | 63 % | 19.1 |
| 0450-0452 | 172.4 (171.1, 173.7) | 118.3 | 7.44 | 4,345 | 1.027 | 2.65 | 46 % | 18.0 |

At the same draws a frame: the decoder 28 % less CPU a frame, the recorder 2.4 % less a draw, the 1% low +5.5 %
(Windows on an RTX 5060 Ti: +7 %), the average the same.

**Left.** The recorder still runs 77-79 % of the frame and its time is the draw setup; the draw-setup study
(#607 item 4) found it flat and points at register-group dirty bits that the decoder could produce (what
changed since the previous draw) so the recorder skips the unchanged groups' work: the next step here.

## The GTX 1650 Ti's GPU frame after #770, and two transfer cuts that do not pay (#640, 2026-11-11)

**The frame now.** The laptop above (GTX 1650 Ti 4 GB, i5-10300H, 8 GB, Windows 11), main
7156c95 built by the launcher (ReIGlue pin 5408d20, as #770 left it), the launcher's settings for it
(the 540p scene, AMD FSR 1, 60 fps, Vulkan), warm caches, `--fh1_gpu_profile=5`. Two benchmark
runs (the measured minute) and a 3-minute free-roam drive at noon, ms of GPU time a frame:

| Work | Benchmark (2 runs) | Free roam |
| --- | --- | --- |
| Guest draws | 8.14, 7.92 | 7.96 |
| Transfers to depth from depth | 1.98, 2.00 | 1.89 |
| Texture loads | 1.90, 1.94 | 1.88 |
| Resolves (to the EDRAM buffer, then to guest memory) | 0.87 + 0.83, 0.87 + 0.84 | 0.87 + 0.83 |
| Transfers to depth from color | 0.72, 0.72 | 0.73 |
| Transfers between color targets | 0.46, 0.46 | 0.44 |
| Uploads | 0.39, 0.41 | 0.27 |
| Color clears, transfers to color from depth, the swap, depth clears | 0.19, 0.07, 0.07, 0.04 | 0.20, 0.08, 0.07, 0.05 |
| **All** | **15.68, 15.54** | **15.28** |

The benchmark: 58.4 fps without the profile, 57.4 and 58.6 with it. Transfers are still the largest
cost after the guest's draws: 3.5 ms a frame, about 25 a frame to depth. The test without any
stencil bit draws in the section above (wrong in general) puts the bit draws that are left at about
1.1 ms of the transfers from depth and 0.5 ms of those from color: the depth target at EDRAM tile 720
(kD24S8) has stencil in it, and 0461 can skip only bits that are set nowhere in the source.

**A test hook (ReIGlue 0480).** `FORZA_TEST_NO_STENCIL_EXPORT=1` leaves
`VK_EXT_shader_stencil_export` off, so an AMD or Intel GPU takes the NVIDIA transfer path (the
stencil in one draw per bit) and a change to it can be checked on Linux: the render tests'
pictures with it match those without it within the run-to-run noise on an RX 9060 XT (RADV).

**Cut 1, not kept: transfers whose result is known.** Counted on that RX 9060 XT with the hook, a
render-test session (title, free roam, cockpit, photo mode, a race start), about 96 000 transfers
to depth a minute:
- 56% copy tiles back to the depth target they were last copied from, which has not been written
  since. But the target in between had always drawn into them (depth or stencil writes), so none
  could be skipped.
- 4.7% come from a source that holds only a resolve clear's value where they read, and could be a
  clear. They are small ones (about 0.003 ms each): not worth the bookkeeping.
- None of the transfers to depth from color copy back unchanged data, and the color sources that
  had been resolve-cleared were cleared elsewhere than the tiles transferred.

**Cut 2, not kept: stencil bit draws only over the tiles with the bit.** A compute pass listed,
for each bit, the destination's tiles whose source tile has it, and each bit's draw went
indirect over that list (depth sources, and k_8_8_8_8 color sources, whose stencil is their red
byte). The pictures were unchanged (Linux, with the hook), but on the GTX 1650 Ti, one profiled
benchmark run each way: transfers to depth from depth 1.97 -> 2.09 ms, from color 0.72 -> 0.74 ms
(the GPU frame 15.30 -> 15.90 ms). FH1's stencil, where it is set, is set in most tiles, so the
lists leave almost every tile in, and their pass and the many small quads cost more than they
save. At the 60 fps cap, three alternating runs each way: 59.2 fps [58.1-59.8] off against 56.9
[53.4-59.4] on. (The exact stencil of k_2_10_10_10 and k_2_10_10_10_FLOAT sources gave wrong
pictures and was left out.) The code is on ReIGlue's branch `exp/640-stencil-tile-lists`.

**Next.** The stencil bit draws (about 1.6 ms) need a way to write the stencil other than one draw
per bit: for a one-sample destination (about half of these transfers: the scene depth and the
shadow map going back to 1x) a compute pass could write the stencil bytes to a buffer and copy them
into the image's stencil aspect, which Vulkan allows for single-sample images. Then the texture
loads (1.9 ms; the 1024x1024 k_24_8 shadow map loaded 4 times a frame is 0.52 ms of it) and the
resolves (1.7 ms in 88 a frame, about 10 us each, mostly fixed cost).

## Pipelines the driver was given twice (#607, 2026-11-11)

**The question.** Reviewing ReIGlue #53 (the driver cache file stores a blob shared by several
descriptions once) found at least 324 pipeline descriptions whose NVIDIA driver blobs were byte for
byte the same as another's. If those were the same pipeline, the SDK compiled each of them again:
stutter on first play and a bigger cache.

**What the blobs say.** On this PC (RX 9060 XT, RADV) the same holds: a first built-in benchmark
stores 405 descriptions with 311 distinct driver blobs. But an identical blob does not mean an
identical pipeline. RADV (and, by the same numbers, NVIDIA) keeps the compiled shaders in its cache
data, not the fixed-function state, so descriptions that differ only in depth test, stencil,
winding or depth format get the same blob. A diagnostic build (not merged) logged, for every
pipeline created, a key of everything `vkCreateGraphicsPipelines` is given (the SPIR-V by hash,
every state structure, the layout, the attachment formats). Cold caches, with another game running
(counts, not timings):

| | First benchmark | First free-roam drive |
| --- | --- | --- |
| Descriptions created | 396 | 404 |
| Distinct creation info as given | 371 | 372 |
| ... after writing ignored state one way (0520) | 361 | 368 |
| ... also leaving out cull mode, winding, depth and stencil state | 323 | 332 |
| Distinct (vertex, pixel) SPIR-V pairs | 272 | 288 |

What differs inside each group of descriptions with the same creation info:
- **The guest shader hash, nothing else** (18 of the 19 benchmark groups, 23 of 24 in free roam):
  the microcode is the same except one word of the three after the last instruction
  (`0x4E4Axxxx`, then `d9f68398 e5d6f0df`, which the translator never reads). The SPIR-V is the
  same, so the pipeline is.
- **The depth format** (one group): D24S8 and D24FS8 are both `D32_SFLOAT_S8_UINT` on the host.
- After canonicalisation, 10 (benchmark) and 4 (free roam) more groups: **the alpha blend factors of
  attachments whose write mask has no alpha**. The stencil-of-a-culled-face and winding rules
  merged nothing more in these runs (FH1's winding changes come with the stencil test on).

The next 38 (benchmark) and 36 (free roam) merges would need the depth, stencil, cull and winding
state to be dynamic (`VK_EXT_extended_dynamic_state`): the same shaders with other fixed-function
state. They are cheap already: in the diagnostic's driver times, such a pipeline took 0.10 ms
(median; 0.09 to 0.12 ms over three runs, p90 5 to 7 ms) against 14 to 21 ms for one with new shader
code, because the driver finds its compiled shaders. 39 of them took 165 ms of a 9.8 s first
benchmark. Not done: it changes every draw's state setting for about 2% of the driver time.

**ReIGlue 0520.** Descriptions whose creation info is the same, after the ignored state is written
one way (the blend of channels a write mask leaves out, the factors of min and max blending, the
stencil operations of a culled face, the winding without culling, stencil test or a shader reading
`gl_FrontFacing`), share one pipeline. That rewriting is only for the comparison: the pipeline is
created with the first description's own creation info, so the driver cache entries stored before
0520 stay valid. A second creation of the same key waits for the first. The
key compares the SPIR-V (XXH3-128 of each translation's code), so a shader variant or guest shader
that translates the same is merged without having to prove which modification bits matter.
Duplicates built one after the other were cheap (median 0.1 ms) but two built at once both compiled:
38 duplicates took 304 ms of that 9.8 s benchmark.

**Measured** (BENCH_LOCK, 60 fps cap, 1280x720, the golden TU4 save; cold: empty game and Mesa
caches; warm: the restart on that cache; `driver time` is the frame cost CSV's background pipeline
time). Clean runs only: the third benchmark round and one free-roam pair ran with another game
started during the lock and are left out (the second clean A drive started at a load of 11, just
after another game had ended).

| Run | 46ae834 (A) | 0520 (B) |
| --- | --- | --- |
| Benchmark cold: pipelines built | 404, 408 | 367 + 34 shared, 372 + 37 shared |
| Benchmark cold: driver time | 3.9 s, 3.8 s | 3.7 s, 3.8 s |
| Benchmark cold: fps, 1% low, p99 | 60.0 / 49.8 / 18.4 ms, 60.0 / 47.1 / 19.0 ms | 60.0 / 48.8 / 18.6 ms, 60.0 / 45.9 / 19.5 ms |
| Benchmark cold: frames held for pipelines, draws skipped | 92, 2203; 90, 2029 | 89, 2230; 99, 2026 |
| Benchmark warm: warm-up | 404 in 174 ms, 411 in 180 ms | 403 (36 shared) in 172 ms, 409 (37 shared) in 172 ms |
| Benchmark warm: fps, 1% low | 60.0 / 51.8, 60.0 / 51.9 | 60.0 / 49.7, 60.0 / 48.9 |
| Free roam cold, 120 s drive: pipelines built | 385, 389 | 355 + 30 shared, 353 + 33 shared, 358 + 33 shared |
| Free roam cold: driver time; 1% low | 3.6 s, 4.1 s; 44.0, 38.8 | 3.6 s, 3.6 s, 3.4 s; 46.2, 40.3, 45.6 |
| Driver cache file after the cold benchmark | 3.09 MB (405 entries), 3.18 MB (418) | 3.17 MB (375 entries for 411), 3.17 MB (374 for 413) |

So on RADV 0520 removes about 9% of the pipelines (and their objects and driver cache entries) and
nothing measurable in time: the duplicates were already cheap, the cache file was already
deduplicated by #53, and the 1% low moves within its run-to-run spread (39 to 52 over all the benchmark runs, contended ones included).

**Upgrading.** The first launch after the update, on a cache that 46ae834 made: a warm benchmark
of 0520 (30 fps cap, another game running, load 9) created 366 pipelines from stored driver data,
compiled 0 and rebuilt 0 stale entries, gave 39 descriptions another's pipeline, and warmed up in
186 ms (46ae834 on its own cache, without the other game: 174 and 180 ms). The driver is given
each description's own creation info, so every entry stored before 0520 still matches it. The
measurements above ran an earlier build of 0520 that also gave the driver the rewritten state; on
RADV that made no difference either (the same run with it: 366 from stored data, 0 compiled, 0
stale, 184 ms), as RADV's entries hold the shaders and not that state.

**The picture.** `tools/render_test.py`: baselines recorded with A (two runs), checked with B: 6 of 6
scenes pass (title, free roam at noon and dawn, cockpit, photo mode, a race start), each closer to
the baseline than A's own two runs were to each other (photo mode dssim 0.0100 against A's 0.0560,
free roam at noon 0.0017 against 0.0457). B's run gave 48 descriptions another's pipeline (461
built). The final 0520 (the driver given each description's own state) passes 6 of 6 too, photo
mode at 0.0072 and free roam at noon at 0.0034, with 52 descriptions given another's pipeline.

**On NVIDIA** (GTX 1650 Ti, Windows 11, driver 555.97, the launcher's NVIDIA preset: 60 fps cap,
540p, no game driver pipeline file). The built-in benchmark with A and B as the only change (the
SDK DLLs built from 46ae834 and from 0520 for the same game build), the golden TU4 save, in the order
A, B, B, A. Cold: an empty game cache root and an empty NVIDIA shader cache
(`__GL_SHADER_DISK_CACHE_PATH`, one folder per run); warm: the restart on both. Driver time is the
frame cost CSV's pipeline creation time (`bg_pipeline_ms`, real pipelines on any thread); the race
window runs from the benchmark's wait for race movement (17 to 19 s of race start on a cold run)
through the 15 s settling and the 60 s measured, 92 to 94 s.

| Run | 46ae834 (A) | 0520 (B) |
| --- | --- | --- |
| Cold: pipelines built | 410, 406 | 375 + 33 shared, 376 + 33 shared |
| Cold: driver time, whole run | 58.3 s, 66.3 s | 59.1 s, 59.6 s |
| Cold: driver time in the race window (pipelines created there) | 21.4 s (148), 30.9 s (154) | 23.8 s (130), 22.7 s (126) |
| Cold: fps, 1% low, p99 | 58.6 / 22.8 / 23.6 ms, 58.1 / 20.9 / 24.8 ms | 58.7 / 22.1 / 23.2 ms, 57.7 / 21.9 / 24.9 ms |
| Cold: frames over 50 ms, measured (race window) | 11 (64), 12 (69) | 12 (65), 12 (59) |
| Cold: draws skipped for pipelines, whole run (race window) | 47908 (1633), 44793 (1776) | 29901 (1494), 25230 (1948) |
| Cold: NVIDIA shader cache after the run | 20.7 MB, 20.6 MB | 20.5 MB, 20.8 MB |
| Warm: warm-up | 405 in 413 ms, 397 in 406 ms | 405 (34 shared) in 419 ms, 404 (33 shared) in 411 ms |
| Warm: driver time | 0.23 s, 0.97 s | 0.22 s, 0.67 s |
| Warm: fps / 1% low, frames over 50 ms | 59.1 / 40.8 and 0, 59.8 / 43.1 and 0 | 59.8 / 41.6 and 1, 59.2 / 41.6 and 0 |

The upgrade (B's first start on copies of A's cold caches, both rounds): 414 (38 shared) and 411 (37
shared) created from storage in 418 and 421 ms, 0.38 and 0.25 s of driver time, 59.4 / 38.7 and
59.6 / 41.2, no frame over 50 ms: the driver found its stored shaders for every pipeline.

So NVIDIA is like RADV: 0520 builds about 8% fewer pipelines, and nothing it saves shows in time.
A first benchmark costs about a minute of driver time here (140 to 165 ms a pipeline, the slowest
0.8 to 1.5 s), and a duplicate costs next to nothing of it, as the driver already has the shaders;
its shader cache does not grow with them either (20.5 to 20.8 MB both ways). B skipped fewer draws
over the whole run (the loading screens before the race), not in the race. In this cold benchmark, the
1% low (21 to 23) and the 11 or 12 measured stalls coincide with first-play compiles of new shader code,
which 0520 does not touch; the prebuilt shader list (#276) is the change aimed at those. It does not
remove every first-play stall: the fresh-profile A/B for #400 still had the same four transition hitches
with 251 and with 575 startup hits, and those frames recorded no foreground pipeline time and no
background pipeline completions, so pipeline work does not explain them.

**Not merged.** As it saves no time on either GPU, 0520 was not taken into ReIGlue; the code stays on
ReIGlue's branch `fh1-607-pipeline-share`. The microcode trailer could also be left out of the
shader hash, which would translate each such shader once, but the hash is the key of every stored
description.
