# Render regression tests

`tools/render_test.py` plays a few fixed scenes in the TU4 game, takes screenshots and compares
them with stored baselines. Run it by hand before a release and on pull requests that change
rendering (the SDK overlay, shaders, graphics settings and their defaults). It is Linux only
and needs the game build, a GPU, Pillow and a profile save. CI runs only the synthetic image,
filesystem and fake clock-worker tests; it never starts the game or opens guest memory.

A check is one game session: 2 min 36 s on the test machine, comparison included, plus about
36 s for each #512 hang it recovers from. Recording baselines plays the session three times
(about 8 minutes).

## Scenes

All scenes come from one session (`tools/render_tests/scenes.toml`): the title screen right
after the boot, SINGLE PLAYER, free roam at the spawn point of the career save (the GoPro
Millennia Masters sign-up), the cockpit camera, photo mode, then that race.

| Scene | What it shows | Tolerance (dssim, changed) | Noise, October (dssim, changed) | Noise, November |
|---|---|---|---|---|
| `title` | PRESS START over the first shot of the attract sequence (compared at 1/8 size) | 0.015, 1 % | 0.0091, 0 % | 0.0050, 0 % |
| `freeroam_midday` | free roam, chase camera, 12:00 | 0.005, 0.5 % | 0.0016, 0.14 % | 0.0037, 0.29 % |
| `freeroam_dawn` | the same at 07:47 | 0.010, 1 % | 0.0079, 0.21 % | 0.0074, 0.18 % |
| `cockpit` | cockpit camera, 12:00 | 0.003, 0.5 % | 0.0004, 0.10 % | 0.0014, 0.10 % |
| `photo_mode` | photo mode's default camera, 12:00 | 0.016, 2.5 % | 0.0123, 1.89 % | 0.0138, 1.81 % |
| `race_start` | the race 14 s after the start, the player's car on the grid | 0.008, 1.2 % | 0.0043, 0.69 % | 0.0041, 0.51 % |

Noise is the worst score of an unchanged build (October 2026, one Linux machine): each of six
recorded runs against the other runs of its recording, and seven checks against three recorded
runs (four of the unchanged build, three with the flags below that change nothing visible). None
of these checks failed. November 2026 (main at 14beb4ae, the same machine, 8 cores, so with the
split command processor): three recorded runs against each other and four checks against them,
one of them with the build of another folder at the same commit; none failed.

What the tolerances catch (dssim of the scene; title, the two free roam scenes, cockpit, photo
mode, race):

| Change | Scores | Flagged |
|---|---|---|
| `--anisotropic_override=0` (default 3) | 0, 0.036, 0.022, 0.0087, 0.041, 0.031 | 5 of 6 scenes |
| `--scene_msaa=off` (default 4x) | 0, 0.028, 0.032, 0.0089, 0.021, 0.013 | 5 of 6 scenes |
| `--world_shadow_detail=ultra`, `--world_shadow_filter=soft`, `--world_texture_detail=high` | within the noise | none |
| title logo painted over, title gamma 1.1 (edited images) | 0.049, 0.027 | the title |

The title's first attract shot is the same with both flags. Small objects' shadows beyond 60 m,
shadow edges 1.5 times softer and a texture LOD bias of -0.5 change too few pixels at 1280x720 to
be told apart from run-to-run noise: the check finds changes over a large part of the picture
(texture filtering, antialiasing, lighting, exposure, colour, a missing effect or object of some
size), not single pixels.

## Recording baselines

Baselines are game images: they never go into the repository. They live in
`~/.local/share/forza-render-baselines` (`--baselines DIR` or `FORZA_RENDER_BASELINES`), a
folder the tool owns. The first recording needs a profile save, which is copied there and used
by every later run:

```sh
tools/render_test.py record --save PATH/TO/4D5309C9 --xuid E030000004B490FA
```

The save must be a TU4 career save that has seen the 1000 Club introduction and stands in free
roam at the GoPro Millennia Masters sign-up, in the Lamborghini Aventador, and that owns DLC
cars: the runs install no DLC, so the game shows its MISSING CONTENT notice after START. The
scenes' inputs assume all of this (X at the spawn point signs up for the race); the user's
Xenia career save after the TU4 introduction is such a save. `--xuid` is the profile's XUID. Later recordings
reuse the stored save: `tools/render_test.py record`.

`record` plays the session three times (`--runs N`) and keeps every frame of every run as a
baseline (18 to 30 a scene, about 140 MB in all). Each run is also compared with the other
two: that is the scene's run-to-run noise on this build and machine. It is printed and stored in
`<scene>/baseline.json`, and a scene noisier than its tolerance says so. Record again after an
intended rendering change, on the build that has it, after a driver update, and after a
default that changes when the game's frames reach the screen. The split command processor (on
with 6 or more cores since November, #764) captures the title's attract camera a frame later
and moves the free roam scenes by about 0.003 (dssim): against baselines recorded without it,
free roam midday scored 0.0052 to 0.0069 of its 0.005 in each of six checks, and 0.0024 to
0.0050 in three with `--gpu_split_command_processor=false`. The pictures are the same (the
trees' leaves and the ridge line a pixel apart), so the baselines were recorded again.

## Running a check

```sh
tools/render_test.py check                                  # all scenes
tools/render_test.py check --scenes photo_mode,cockpit      # some of them
tools/render_test.py check -- --anisotropic_override=0      # extra game flags after --
```

The game is `forza-recomp/out/build/linux-tu4` (`--build DIR`) with the SDK of `REXSDK` or the
overlay the run scripts use (`--sdk DIR`) and the game data in `extracted/game` and
`extracted/tu4` (`--game-data`, `--update-data`). Every run gets scratch XDG folders and a fresh
copy of the stored save as its only `--user_data_root`, so the player's settings, saves and
Mesa shader cache are never read or written. The SDK reads its settings from `forza.toml`
next to the executable, and the launcher writes the player's there when they play from that
build folder (world detail, ambient occlusion, the Forza Live server): so the game runs from
`game/` in its run folder, hard links of the build folder's files with a copy of
`tools/prerelease_forza.toml` (offline, no Discord activity, everything else the defaults) as
its `forza.toml`; the prerelease boot and server checks run the same way (#846). Before
that, such a file changed every free roam scene and brought Forza Live's messages and photo
mode's Big Shot line into them. Every run starts with empty shader caches (the game's and
Mesa's, in its run folder under `~/.cache/forza-render-tests`, `--work DIR` or
`FORZA_RENDER_WORK`; #960, below). The game runs offline with `--audio_mute` and
`REX_AUDIO_FAIL_OPEN=all`, in a 1280x720 window at 60 fps, with the fixed settings of
`[game] args` in `scenes.toml`. Like the run scripts it waits while someone else holds the
benchmark lock (`tools/bench_lock.sh`) and does not start with less than 10 GB of free memory.

The exit status is 0 when every scene passes, 1 when one fails and 2 when a scene could not
be captured.

`tools/prerelease.sh` runs this check as its `render` step (`--only render`) against the
`linux-tu4` build under test, with `--retries 2`: it passes when every scene is within its
tolerance, fails when one is outside it or could not be captured (the report is
`render-report/index.html` in the prerelease log folder), and is skipped with a message when
there are no baselines yet. Baselines recorded before #960 fail it ("ended without a report",
with the reason just above): record them again on a known-good build.
`PRERELEASE_RENDER_FLAGS="--anisotropic_override=0"` adds game flags, like `--` above. It plays
no part in CI.

### Hangs

The game sometimes stops presenting right after it enters free roam (#512). The tool watches
the frame log (`RECOMP_PERF_CSV`) and the screenshots: no new frame for 40 s (`--hang-secs`),
no screenshot within 10 s of the trigger, no main loop frame for `--hang-secs` after `world`,
a car at the spawn that did not drop within 60 s or that came to rest where it did in no
recorded run (#960, below), or a game that exited ends the attempt.
The tool stops that game (by its own PID) and plays the session again from the boot, twice by
default (`--retries N`). The game's guest memory file in /dev/shm is removed once no process maps it.

## Reading the report

The report is `~/.cache/forza-render-tests/reports/<time>/index.html` (or `--report DIR`),
with `report.json` next to it. For each scene:

- **dssim**: 1 minus the mean structural similarity (SSIM) of the two pictures, computed on the
  luma of 8x8 blocks of the half-size picture (each block covers 16x16 pixels of the 1280x720
  capture). 0 is identical. Run-to-run noise is a few thousandths; a different texture filter
  or a missing effect gives hundredths.
- **changed**: the share of blocks whose SSIM is below 0.80 or whose colour (Cb or Cr, 0-255)
  moved by more than 6. It catches a local change, like a missing object, a broken shadow or a
  tint, that is too small to move the mean.
- **mean abs diff**: the mean difference per channel (0-255), for orientation only.

A scene passes when both dssim and changed are within its tolerance. Its picture shows the
baseline frame, this run's frame and a map of what changed (red: lower SSIM or a colour shift;
blue: masked), with links to the full-size frames.

The frames compared are the closest pair: each scene is captured six times about 0.3 s apart
(the title ten times, 0.1 s apart) and compared with every baseline frame of the three recorded
runs (ranked on 1/8 size thumbnails, the six closest compared fully). That absorbs the small
timing differences between runs.

## How the scenes are made repeatable

- **Fixed settings and a fixed save**, as above. FH1 has no weather.
- **Time of day.** The tool holds the clock by writing guest memory every 15 ms
  (`clock HH:MM`). TU4: the world is `*(*(*0x833CAAE4 + 4) + 4)` and its time-of-day object
  `*(world + 232)`, whose first word points back at the world. The clock is a float of seconds
  after midnight at `+10488`; the game's rate is at `+10492` (28 game seconds per second at
  noon, 40 at night) and is set by the game every frame, so it cannot be zeroed. The whole
  chain is checked before every write. This is a best-effort check: there is no native lifetime
  lock, so the game can still free the object between the check and the write during loading.
  The clock does not exist on the title screen. A worker failure invalidates the session and
  produces an ERROR (exit 2 for check/probe), rather than a passing comparison.
- **Game time, not loading time.** The chase and cockpit cameras sway by a few pixels over
  several seconds even with the car standing, and a 2-pixel shift of foliage is a large SSIM
  change: frames 2 s apart in one run differ by 0.1 to 0.5 dssim. The sway follows game time,
  so the steps are timed from the moment free roam starts running (`world`: during the load the
  clock reads the flow's 16:00, then jumps to the save's time, and it starts to advance when the
  simulation starts) and not from the loading screen, whose length varies.
- **The car at the spawn (#960).** Free roam starts with the car dropped half a metre onto the
  road, and where it came to rest was not the same every run: up to 0.06 degrees of heading and
  a few millimetres apart. The chase camera, the cockpit and photo mode's default camera turn
  with the car (photo mode's YAW read 280.0 instead of 280.1), so free roam, cockpit and photo
  mode failed together with dssim of 0.03 to 0.09, in about half the runs on a busy PC.
  Two things decided it (found by reading the car's position from guest memory every 10 ms):
  - how long the frames of the drop took. The game integrates with the real frame time, and
    the frames right after the load are long (pipelines compiled while the world appears, more
    so while other games run). The game runs with `FORZA_FRAME_TIME=fixed`: every main loop
    frame is 1/60 s of game time (its dt), however long it took, and adds a line to
    `frametime.csv` in the run folder. The drop is then the same, frame by frame, every run.
  - whether the road held the car at once, which depends on the shader caches. With warm ones
    the car sank about 11 cm into the road and was pushed back up some frames later, and came
    to rest at one of several spots 0.005 degrees apart. With empty ones the road nearly always
    held it, and it rests at the same spot: every run starts with empty shader caches. With
    the fixed frame time everything else follows from the spot, the traffic too: runs at the
    same spot give free roam pictures 0.0001 apart. `land` waits until the car has come to rest
    and logs where, `record` keeps each run's spot in `baseline.json` (`spawns`), and a check
    whose car rests where no recorded run's did (more than 0.002 degrees or 1 mm away; it
    happened when another program kept the CPU busy) is played again (`--retries`, twice by
    default).
- **Game time.** The game runs behind real time after a long frame, so after `world` the steps
  count frames: `at S` waits for the frame `S * 60` after the latest `world` or `start`, and
  `wait S` and the shots of a scene count game time too. Before `world` (boot, menus, the load)
  `wait` stays in real seconds. The race's loading and introduction take more or fewer frames
  from run to run, so `start` waits for the race itself to start (a CPU driver that stood for a
  second moves off), and the race scene is 14 s of game time after that. The game looks for the
  screenshot trigger at every frame (it looked at every tenth, up to 9 frames late).
- **Baselines from before #960** show the car at another spot and the race at another moment:
  `check` refuses them (every scene, the title too), record them again. The build must have
  `FORZA_FRAME_TIME=fixed`: an older build writes `frametime.csv` in blocks of about 100
  frames, and the scenes come late. The noise columns of the table above were measured before
  #960.
- **The title screen** has no clock; its attract sequence starts with the boot and its camera
  moves from the first frame, so it is captured right after the first frame and compared at
  1/8 size, where a frame or two of difference in the boot does not matter.
- **Masks** leave out what still differs between runs: traffic on the far road and the right
  lane (now and then it still differs, #960) and the minimap's traffic dots in free roam, the
  road ahead and the mirror in the cockpit, and in the race the race clock, the standings, the crowds and the inflatable. Photo mode pauses the
  world, but edges (the shadows on the road and the rail) still move by a fraction of a pixel
  between runs, so its tolerance is wider.
- `--sync_pipeline_creation=true`: draws wait for their pipelines instead of being skipped
  while one compiles, so a cold shader cache does not leave objects out.

## Writing a scene

`tools/render_test.py probe [--session NAME]` plays a session and keeps every capture,
including captures whose name is not a scene, in `~/.cache/forza-render-tests/runs/`. Add
steps like `capture probe_x shots=10 interval=1` to look at a stretch of the game, pick the
moment, add the scene to `scenes.toml`, then record. `trace on` logs the clock every 20 ms to
the run folder. Steps and scene keys are described at the top of `scenes.toml`.
`tools/render_test.py compare A.png B.png --scene NAME --out /tmp/render-diff.png` compares two images with
a scene's masks. Keep the scenes in the one session: every boot is another chance of the #512
hang. Menu presses are 0.1 s long: the default 0.3 s of the input FIFO moves two menu rows.

## Limits

- Run folders have unique names and are created exclusively. Cleanup keeps the four newest
  finished runs marked as owned by this tool, preserving active and unmarked directories.
  Recording replaces only marked save/baseline directories. For baselines recorded by an
  older version without ownership markers, choose a fresh baseline folder and record again.
  Source saves and output folders must not overlap; symlinks and Windows reparse paths are
  rejected. Keep these private directories free of concurrent edits while the tool runs.
  An explicit `--report` must name a new directory; image outputs must stay outside the repo.
- Baselines belong to one machine (GPU, driver), build and save; another machine needs its own.
- Only the guest output is captured (before the presenter's upscaling filter and the HDR
  output), at resolution scale 1.
- A change that touches only a few blocks (one small object) can stay under the tolerances.
- Online play, DLC content, night and other cars are not covered.

## Credits

The idea of fixed scenes with image baselines and a tolerance comes from Pinyon Shift's
`tools/run-fh1-render-test.py` and `config/render-tests/` (arcanite24/pinyon-shift,
BSD-3-Clause; licence in `forza-recomp/LICENSE-Pinyon-Shift`). Their scenarios give inputs
and captures by frame number on a fixed clock of their runtime; ours has no such clock, so the
scenes are timed from the game's own clock instead. No code was copied.
