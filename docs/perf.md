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
thread, most of each frame). None of this has run on Windows yet, so there it is off unless the setting
`gpu_wait_blocking` is on (Advanced > GPU, "Sleep while waiting for the GPU (testing)"); it is on by default on Linux
and macOS (`launcher/test_gpu_wait_blocking.py` checks both defaults). Off, the waits are the ones before it.
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
### Decoding and recording on two threads (#474): plan, not built

Not built: the two changes above and their measurements took the window. What a build needs, from reading ReIGlue `origin/main`
(`src/graphics/command_processor.cpp`, `src/graphics/vulkan/command_processor.cpp`) and Pinyon Shift's
`gpu_record_thread` (ShiftGlue `9362a07`, 419 changed lines in the same Xenia-derived processor).

**Shape.** Keep PM4 parsing (`ExecutePrimaryBuffer`, `ExecutePacket*`) on the "GPU Commands" thread, the decoder. It
reads a *shadow* register file (`decode_regs()` in Pinyon) and appends, in order, to a batch: register runs (copied:
the ring and the game's constant memory are reused after the packet), and closures or small records for the backend
work (draw, copy/resolve, shader load, swap, event/fence writes, interrupts). A second thread, the recorder, takes
batches and applies them to the real `register_file_` and the backend (`VulkanCommandProcessor::WriteRegister`
bookkeeping, `IssueDraw`, `IssueCopy`, `IssueSwap`...). The decoder publishes a batch when it is full, at a swap,
and before it waits.

**What stays on the decoder and what moves.** Today's cost sits in `WriteRegistersFromMem` (per-register
`WriteRegister` dispatch and the Vulkan constant-block invalidation), `ExecutePacket`, `IM_LOAD`
(`fh1_shader_load_memo.h`, patch 0140) and the draw's front half. After the split the decoder only compares and copies
words (Pinyon: drop unchanged register writes `9bf779f`, merge float-constant runs `99f52ca`, compare four registers
at a time `e00387b`), the recorder owns everything that reads the register file, the caches and the Vulkan state.
The memos 0140/0340/0360-0362 run on the recorder side (they read the register file and caches); the `IM_LOAD`
address-to-shader memo needs the recorded dwords, so `IM_LOAD` is recorded whole and memoized on the recorder.

**Ordering rules (what the guest can observe).**
- `MEM_WRITE`, `COND_WRITE`, `REG_TO_MEM`, `EVENT_WRITE_SHD/EXT` that the CPU reads, and occlusion queries
  (`EVENT_WRITE_ZPD`, `VIZ_QUERY`): the decoder `RecordSync()`s first (the recorder has run everything before),
  then performs them itself. Rare packets.
- `WAIT_REG_MEM` (about a frame boundary and the "GPU idle" waits): publish the recorded work, then poll. The register it
  polls is the *real* file for registers the recorder writes (`COHER_STATUS_HOST`, scratch) and memory otherwise;
  `MakeCoherent` runs on the recorder.
- Fence writes (`EVENT_WRITE_SHD`) are recorded, not waited for: the game must see a fence only after the recorder
  has consumed the draws before it, because it reuses their memory after the fence. Avoid Pinyon's `b3b3cbb`
  (awaiting a fence before the worker has submitted it): `AwaitSubmissionThread`/`CheckSubmissionFenceAndDeviceLoss`
  must be asked about submissions the recorder has made, not the decoder's count.
- `XE_SWAP` is recorded; the swap's `IssueSwap` runs on the recorder (with #476 the presenter step is queued on the
  submission thread behind it). `rexui_SetNextGuestOutputTag`, `fh1_frame_trace::OnSwap` and `Fh1StereoOnSwap` must
  be recorded with it (they bracket the swap on the decoder today).
- SDK 0115's futex wake (`WakeWordWaiters` after scratch/MEM_WRITE stores) and the `gpu_wait` / `render_idle` hooks
  must fire when the *recorder* publishes the store, not when the decoder parses the packet.
- The stereo/VR recording (`Fh1StereoOnDraw/Replay`, patch 0396) replays whole draw segments: record the segment
  markers and keep that state on the recorder, or leave the split off while `vr_mode` is on.

**Register file readers on the decoder** (these need the shadow file or a recorder round trip): the packet loop reads
`PA_SC_VIZ_QUERY` (draw skip), `VGT_*` (it writes them), `COHER_*`, `SCRATCH_*`, `RB_*` only through `IssueDraw`
(recorder). `ReadRegisterValue` callers in `WAIT_REG_MEM`, `REG_RMW` (read-modify-write: the decoder can do it on the
shadow and record the result), `REG_TO_MEM`. The shadow is written by every packet write (cheap) and never by the
backend; the few registers the backend itself writes (`WriteRegister` from `OnPrimaryBufferEnd`, gamma ramp
sequence, scratch) need the same write recorded on both files.

**Cvar and rollout.** `gpu_record_thread` (restart), default off, the Vulkan backend opts in
(`SupportsRecordThread()`); D3D12 and the VR stereo path stay on one thread. Same thread-naming rule as #475
(`GPU Recorder` next to `GPU Commands`) so `linux_thread_cores` can give each a core.

**Measure** with the harness used for #476/#475: Deck-like limits (`taskset -c 0-3`, 30 fps and uncapped, the
busy-loop slow-CPU variant), benchmark and free roam, in-run toggling is not possible (restart cvar), so ABBA pairs;
metrics: fps and 1% lows, `fh1_frame_cost_csv` CP ms (now two numbers: decoder and recorder) and the sum, process CPU
per frame (the split adds copying: expect +3-6 % total CPU, which the Deck pays for on the SMT sibling), hitches
(`GPU slow frames`, `FORZA_STALL`). Correctness: paused-scene screenshots at the same spots, the `render_tests`
captures (`tools/render_tests`), a race with 7 AI cars, a photo-mode Big Shot (readback resolve), and a cvar-off run
that is bit-identical to today's.

**Cost estimate.** Pinyon's first step was 419 changed lines; ours also has per-register Vulkan invalidation, the
stereo path, readback-resolve `RecordSync` points (`readback_resolve_wait_range`, #552, 0411) and the pipeline
look-ahead (`fh1_pipeline_lookahead`, run at the frame end), so budget 2-3 days of work plus the measurements, in
steps that each leave the cvar-off path unchanged: (1) the batch/recorder thread running closures for draws and
copies only, registers still written by the decoder under a sync at every draw (correct but slow, tests the ordering);
(2) shadow register file and recorded runs; (3) the sync rules for the CPU-visible packets; (4) the follow-ups
(unchanged-write drop, constant-run merge). Expected gain: the 25-30 % decode share of the thread's 7-8 ms on this PC
(2-2.5 times that on a Deck) comes off the thread the frame waits for, minus the batch hand-off; Pinyon measured
18.1 to 17.0 ms (6 %) from the split alone.

### Where this leaves #559

Nothing here is on by default and nothing here was measured on a Deck. `--vulkan_present_on_submission_thread=true`
removes an idle wait from the thread the frame waits for and showed +12.7 % uncapped on four cores with the Deck
preset, with identical pictures and no extra hitches; it should be tried on a Deck (Settings, Advanced, extra arguments)
and, before it becomes the default, on the paths that use the deferred step differently: the VR export (frame tag and
pose pairing), native upscaling with DLSS (the replay's split submission), and a Windows run. `--linux_thread_cores=auto`
is the Deck layout of Pinyon Shift; it does nothing on a CPU without SMT because on four cores with one thread each it
measured slower, so only a Deck can say whether it helps. The command processor split (#474) is the large item and is
not started.
