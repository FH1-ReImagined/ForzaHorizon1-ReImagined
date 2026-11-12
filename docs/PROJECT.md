# Forza Horizon 1: ReImagined: native static recompilation of Forza Horizon (Xbox 360)

Goal: run Forza Horizon (Xbox 360, Title ID `4D5309C9`, base version, no title update)
natively on x86-64 Linux (later Windows) by statically recompiling `default.xex` and its
facade DLLs (PowerPC Xenon) to C++ with the ReXGlue SDK. The approach, tooling and many
runtime fixes come from [RecompiledHaloReach](https://github.com/Dyotson/RecompiledHaloReach)
(same SDK, same author); files taken from it say so.

## Native achievement notifications

Achievement notifications now subscribe to the SDK event API and queue for the game's
CHudCombinedNotificationControl. The native trophy texture accompanies the achievement
name and Gamerscore; no ImGui achievement notification dialog is registered. Only the guest
UI thread binds native elements. Unlocks retain their remaining visible time across
loading/unbinding, ordinary alerts and challenge prompts. Duplicate pending IDs are
suppressed; distinct unlocks keep delivery order. F7's achievement browser is unchanged.
Standalone queue tests cover timing, interruption, ordering and concurrent delivery on
Windows and Linux.

## The game files

| | |
| --- | --- |
| Container | Games on Demand (content type `0x7000`), SVOD: a header file `4D5309C9/00007000/589C7B8B0AE2A7A0CBE0` plus 46 fragments in `589C7B8B0AE2A7A0CBE0.data/` (7.6 GB), no XSF header, XDVDFS magic at `0x12000` of `Data0000` |
| `default.xex` | Base version (Xenia module hash `D48ABF1704CE5C4A`, matching `patches/4D5309C9 - Forza Horizon.patch.toml`; TU4 would be `336A44DC03EC8782`). Version 0.0.0.7, built 2012-09-04, Media ID `2B7A1346` (Europe/Asia disc), 20.9 MB, load address `0x82000000`, image size `0x1620000`, entry point `0x82A6FBC0`, AES-encrypted with basic (zero-run) compression |
| DLLs | `XMediaFacade_default.xex` (loads at `0x88000000`) and `SpeechFacade_default.xex` (`0x89000000`), loaded at run time with `XexLoadImage` |
| Data | `media/` (cars, tracks, audio, shaders, UI zips, string tables...), `nuisp*` Kinect speech models, `$SystemUpdate/` (dashboard update, not used) |
| DLC | `4D5309C9/00000002/`: 23 LIVE (STFS, read-only format) marketplace packages, content type 2: car packs, VIP, Season Pass, Rally Expansion (1 GB), 1000 Club Expansion. Each holds `Media/<id>.puboffer` and `Media/DLCZips/<id>_pri_65[.zip]` (the expansions also `spa.bin`). Not used yet |

`tools/xcontent_extract.py` (standard library only, read-only) lists and extracts both
container kinds; it is a port of the SDK's `StfsContainerDevice` (from Xenia):

```sh
G=~/forza-horizon-.../4D5309C9
python3 tools/xcontent_extract.py $G/00007000/589C7B8B0AE2A7A0CBE0 info
python3 tools/xcontent_extract.py $G/00007000/589C7B8B0AE2A7A0CBE0 extract extracted/game
python3 tools/xcontent_extract.py $G/00000002/<package> list
```

The extraction was checked against the SVOD level-0 hash tables (SHA-1 of every 4 KB
data pair in all 46 fragments: no mismatch). SVOD block mapping: each 0xA290000-byte
fragment is one level-1 hash block, then 0xCB groups of (level-0 hash block + 0x198
data sectors of 0x800 bytes).

`tools/xex_image.py XEX OUT --elf` writes the decrypted, decompressed image as a PowerPC
ELF (via `openssl enc` for AES), and `tools/ppcdis.sh ADDR [+LEN]` disassembles it with
`llvm-objdump` (VMX128 shows as `<unknown>`). Quicker than Ghidra for looking at a few
instructions; both read `extracted/analysis/`.

## Layout

| Path | What |
| --- | --- |
| `extracted/game/` | Extracted game (gitignored, never committed) |
| `extracted/analysis/` | Decoded xex images for disassembly (gitignored) |
| `forza-recomp/forza_manifest.toml` | Codegen manifest (`default.xex` + 2 DLL modules) |
| `forza-recomp/hints/` | Function boundaries, rexcrt mappings, per-module hints |
| `forza-recomp/src/` | Runtime code: app class, kernel/XAM overrides, debug tools |
| `forza-recomp/generated/` | Codegen output; only `rexglue.cmake` is tracked here, the rest goes to the private `RecompiledForzaHorizon-generated` repo (`tools/sync_generated_repo.sh`) |
| `patches/rexglue-sdk/` | the former SDK patch series, now commits in [ReIGlue](https://github.com/FH1-ReImagined/ReIGlue) (kept for reference, not applied) |
| `ghidra_projects/ForzaHorizon` | Ghidra project with `default.xex` (gitignored) |

## Tooling

- ReXGlue SDK nightly 0.10.0.24 (`nightly-20261002-bd833a2a`) at
  `~/rexglue-sdk-nightly/0.10.0.24/linux-amd64`.
- Patched Xenos GPU plugin, runtime and codegen tool, and the SDK's CMake helpers (the code model
  of the game's code, #473): `tools/build_rexglue_sdk.sh` builds them
  from [ReIGlue](https://github.com/FH1-ReImagined/ReIGlue) (the project's fork of the SDK, at
  the tag the script pins) into this project's own overlay
  `~/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64` from its own clone
  `~/rexglue-sdk-src-forza` (never Reach's `~/rexglue-sdk-src`/`~/rexglue-sdk-patched`).
  Until Forza needs a patch of its own, CMake and the run scripts fall back to Reach's
  overlay, which has the same patch set.
- Clang 23, CMake, Ninja from Homebrew (`/home/linuxbrew/.linuxbrew/bin`).
- Ghidra 12.1.4 + XEXLoaderWV: `ghidra_projects/ForzaHorizon` was made with
  `analyzeHeadless ... -import extracted/game/default.xex` (about 25 minutes with 6 GB heap).

Build (always through the same path, `/var/home/...`; `/home` is a symlink and a
different spelling makes CMake rebuild everything). The SDK prefix is the overlay, as in the
launcher, so the build takes ReIGlue's CMake helpers; a build folder configured with the nightly
as its prefix keeps the nightly's (`-mcmodel=large`) until it is configured again with
`-Drexglue_DIR=$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64/lib/cmake/rexglue`:

The launcher refreshes a development SDK and its configure stamp when the overlay's CMake
helper changes. Older overlays without that helper remain usable with the nightly's large
code model; rebuild the overlay to use the new policy.

```sh
export PATH=/home/linuxbrew/.linuxbrew/bin:$PATH
cmake -S forza-recomp -B forza-recomp/out/build/linux -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_PREFIX_PATH=$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64
nice -n 15 ninja -C forza-recomp/out/build/linux -j4
```

## Pre-release checks

`tools/prerelease.sh [--skip-build] [--only sdk,build,unit,boot,server,render,launcher,ci] [--iso PATH] [--ci]`
runs them all, non-interactively, in about 30 minutes: one PASS/FAIL line per check (SKIP when a check cannot
run here and says why), the last 30 lines of a failing check's log, full logs in
`~/.cache/forza-prerelease/<timestamp>/`, exit status = failures (skips do not count).

| Check | What |
|---|---|
| sdk | the SDK overlay matches the ReIGlue tag `tools/build_rexglue_sdk.sh` pins (reinstalled only when that script changed since the last install; a game running from the overlay is affected) |
| build | ninja of `linux-tu4` and `linux`, then restores `generated/rexglue.cmake` |
| unit | `launcher/test_*.py` and `server/test_*.py` |
| boot | TU4 build to the main menu on a scratch copy of the Xenia save (never `~/.local/share/forza`) with `present_effect` bilinear, cas and fsr, then the base build: no fault, frame rate above 5 fps, a non-flat presenter image that changed after START (the screenshot hook captures the guest output before the upscaling filter, so the filter itself is not seen; a crash or Presenter layout mismatch with the SDK would show) |
| server | a Forza Live server on ports 21310/21311: the game signs in, reaches the menu, its stats reads (XUserReadStats) are answered |
| render | `tools/render_test.py check` (#507, `docs/render-tests.md`): the `linux-tu4` build plays six scenes (title, free roam at midday and dawn, cockpit, photo mode, a race start) and each is compared with the image baselines in `~/.local/share/forza-render-baselines` (`FORZA_RENDER_BASELINES`). SKIP when there are no baselines (record them once on a known-good build); FAIL when a scene is outside its tolerance or cannot be captured; the report is `render-report/index.html` in the check's log folder. `PRERELEASE_RENDER_FLAGS` adds game flags (a graphics change under test) |
| launcher | the launcher as a frozen build runs it (foreign `LD_LIBRARY_PATH`, #53): AES helper, default.xex and facades out of the disc image (`--iso`, default `HorizonISO/*.iso`) |
| ci | `--ci`: `gh workflow run release.yml` (artifacts only) and wait for it; prints the run id |

Leaderboard screens, Windows and Steam Deck are not scripted: a hand run in the VM / on the device.

## Hard rules

- Never modify, move or delete anything in the game folder, the zip next to it, Xenia
  (`~/xenia_canary_windows`) or the Reach project and its SDK checkout/overlay.
- No game code or data in this repository: `extracted/`, `*.xex`, generated C++ and
  Ghidra projects are gitignored. The generated C++ goes only to the private repo.
- Game runs: under `timeout`, with every SDL pad ignored, after checking `free -g`
  (the run scripts refuse with less than 10 GB available); kill by PID.

## Status log

- 2026-11-12: **A garage car with another car's part loads with the stock part (#836).**
  When a save's current car has a part from another car, START on the title screen ended
  the game with exit code 134. A mod's database or a save edit had put the part there; the
  official data has no such part. The car physics loader (TU4 `sub_82CB4500`, base
  `sub_82BF4FB0`) builds each part's record with one of five creators. Each creator checks
  the part's parent column (Ordinal, EngineID, MotorID, DrivetrainID, CarBodyID) against
  the car's and leaves the record NULL when they differ. A NULL clutch is dereferenced in
  `sub_82CAAB60` (lr `82CAAB8C`). `src/hooks/car_part_fallback.cpp` runs a creator again
  when it made no record. It uses the expected parent's `IsStock=1` part, or -1 (no part,
  an empty record) when that parent has none, as for a turbo on an engine without one. It
  logs one warning per car and part. A record the creator made is left as it is. Measured on
  the TU4 build with copies of golden-save-tu4, offline: with the Aventador's clutch set to
  3000 (drivetrain 3's), the game reaches the main menu and free roam, and the car drives
  at about 150 km/h. The same commit without the hook faults at START (NULL r3, lr
  `82CAAB8C`). Mismatched brakes, camshaft, transmission and hood give one line each and
  the car drives; without the hook that save faults at lr `82CAA648`. A foreign engine and
  a foreign twin turbo load as the stock engine without a turbo (that engine has no stock
  turbo), and the car drives. An intact save logs no fallback line, and its warnings match
  the build without the hook. Pure logic: `forza-recomp/tests/car_part_fallback_test.cpp`.

- 2026-10-23: **Internal heap stop in the NVIDIA-labelled crash #299.** Read-only
  disassembly identifies TU4 `sub_82423000` (base `sub_823F3048`) as the memory
  manager's allocation loop. When no allocator succeeds, it compares heap `+0x154`
  minus bytes in use (`+0x44`) with the request. With the heap's stop flag (`+0x174`)
  enabled, it deliberately stores to guest address 4 if capacity is insufficient,
  or address 5 if capacity is sufficient but no suitable block was returned. The
  stores are TU4 `0x824230C0/C8`, base `0x823F3108/10`.
  The #299 registers identify a 16,384-byte request (`r29=0x4000`), alignment 32
  (`r27=0x20`), and final heap `r30=0x83396AF0`: category `0x21`, arena 1's high
  heap, from table `0x8339664C` plus `0x314 + 0x190`. This is distinct from the
  movie allocator's guest-address-6 stop (#262). The local crash explanation and
  report now recognize the matching fault, thread, saved link register and register
  state, preserving the raw fault and call chain. Synthetic base/TU4 logs and the
  original report's log tail validate recognition; unrelated/incomplete faults
  remain generic. This classifies an internal heap allocation failure, not an
  NVIDIA device loss or proof of exhausted system RAM/VRAM. The cause of the
  unsuitable free blocks and a safe runtime fix remain unproven; no game/GPU
  reproduction was performed for this change.

- 2026-10-10: project started. Game extracted from the GOD container (base version, see
  above). Codegen of `default.xex` + both facade DLLs succeeds: 76,788 functions in
  `default.xex`, 248 MB of C++. Hints needed so far:
  - 20 tail-call-only entry points (accessors, forwarders, this-adjusting thunks placed
    right after an unconditional branch) that failed validation with "target not in any
    function" (`hints/tail_targets.toml`, `hints/xmediafacade.toml`,
    `hints/speechfacade.toml`), found by iterating codegen on its error list.
  - The statically linked XAPI fiber routines (same code as in Reach) mapped to the SDK's
    native fibers (`hints/rexcrt.toml`); without the mapping codegen could not even
    resolve SwitchToFiber's tail call into its context save at `0x830ED910`.
  - Known benign warnings: `bdz` at `0x82AD8138/813C` branch to `0x82AD836C`, the zero
    word just past the function (MSVC's marker for unreachable switch cases); function
    `0x8241A370` is 2.5 MB (one huge initializer).

- 2026-10-10 (later): **boots to gameplay.** The game plays the Microsoft Studios intro
  video, shows the title screen ("PRESS START"), the first-run video calibration screen,
  the main menu (Single Player / Multiplayer), the live-action festival intro video, and
  then the in-engine prologue: the SRT Viper cinematic on the road and the drive to the
  festival with HUD (minimap, speedometer), steerable with scripted pad input. Rendering
  looks right with the patched GPU plugin (Reach's 19 patches). Screenshots in
  `screenshots/` (gitignored). What it took:
  - 220 indirectly referenced entry points (vtables, callback tables, CRT initializers)
    and 357 orphan functions in `default.xex`, plus 37/22 and 89/19 in the two DLLs,
    found with Reach's tools ported to read the XEX image instead of Ghidra
    (`tools/find_missing_entries.py`, `tools/find_orphan_functions.py`). The first boot
    died in `XMediaFacade`'s CRT initializers ("Call to invalid or unregistered function
    at 0x882435D8", a `__xc_a` entry). Six hints that split functions were pruned
    (`tools/prune_split_hints.py` plus two by hand: a jump-table case after a call at
    `0x829CBE14`, an EH funclet at `0x82ABAF38`), and 20 that landed on import thunks
    (codegen: "outranked by import") were dropped: they left undefined `sub_831EDxxx`
    symbols, and in a DLL an undefined symbol only shows up when `dlopen` fails at run
    time ("Failed to load shared library for module").
  - `cache:` mounted on a host folder (`<cache_root>/cache`) in `ForzaApp::OnPreLaunchModule`,
    as Xenia does with `mount_cache`: the game keeps ghost and replay streams there.
  - Codegen rewrites the DLL sources during the build, but only `default.xex`'s sources
    are declared codegen outputs, so ninja picks up DLL changes on its next run:
    `tools/build.sh` runs ninja twice.
  - Open: `Resolve region is empty` + `PM4_DRAW_INDX_2(3, 8, 2): Failed in backend`
    logged ~180 times a second (Xenia later made empty resolves a silent no-op,
    "log spam in Forza Horizon 1/2"); `XamXStudioRequest` stub logged 33 times a second
    (Xenia also gets it, 314k calls in the user's log); audio not verified.

- 2026-10-10 (afternoon): **the opening drive plays at 30 fps with sound.**
  - Own SDK clone and overlay (`~/rexglue-sdk-src-forza`, `~/rexglue-sdk-patched-forza`)
    with patch 0023: an empty resolve region is a no-op, as in Xenia, which changed it
    for Forza Horizon. It was logged twice per resolve, about 180 lines a second
    (`Resolve region is empty` + `PM4_DRAW_INDX_2(3, 8, 2): Failed in backend`).
  - `src/kernel/xam_nui.cpp`: Kinect and XAM answers as in Xenia:
    `XamNuiGetDeviceStatus` returns 0xC0050006 (no Kinect; the SDK returned nothing),
    `XamXStudioRequest` (polled every 30 ms) is left unanswered without a warning per
    call, XAM app 0xFE message 0x2B003 succeeds.
  - Title screen and the drive run at a steady 29.5-30 fps (`FORZA_FPSLOG=1`); the
    drive was held at full throttle for two minutes (89 mph on the road, then into the
    woods) without errors. Audio frames carry sound (`FORZA_AUDIOLOG=1`: RMS 0.01-0.04
    during the intro and menus). Saves: the profile (`ForzaProfile`) is written under
    `~/.local/share/forza/<xuid>/4D5309C9/00000001/`; the second run skips the first-run
    screen.
  - Online vision (design only, for later): `docs/online_vision.md`.

- 2026-10-10 (afternoon, later): **free roam, events and races; DLC; Xenia saves.**
  - DLC: `tools/install_dlc.py` extracts the 23 LIVE packages into the runtime's content
    layout (`<user data>/0000000000000000/4D5309C9/00000002/<package>/` plus a
    `Headers/00000002/<package>.header`), byte-identical to what the user's Xenia
    installed. The game mounts them as `DLCRoot0:`..`DLCRoot22:` after START.
  - Saves are tied to the profile: the user's Xenia save (profile `E030000004B490FA`)
    copied under the runtime's profile folder froze the title screen after START. The
    game checks its save with a keyed hash (`sub_82C66308`, key decoded from an
    obfuscated table at `0x82518CF8`, failure callback `sub_824F31F8`), and the failure
    path calls the fatal-error handler `sub_82C09F00` (records four values at
    `0x83442EBC`, sets `0x83442ED1`, spins in `b .` at `0x82C09FA8`). With
    `profile_xuid=E030000004B490FA` (`src/kernel/profile_identity.cpp`; also
    `FORZA_XUID`, `profile_gamertag`/`FORZA_GAMERTAG`) the save loads: free roam in the
    user's Aventador with traffic, a festival event ticket, car select, the pre-race
    menu and a 4-lap circuit race against 7 AI drivers at 30 fps.
  - `src/kernel/xekeys.cpp`: `XeKeysGetConsoleID`, `XeKeysConsolePrivateKeySign`,
    `XeKeysConsoleSignatureVerification` and `XamUserGetIndexFromXUID` answer as in
    Xenia (the SDK's stubs wrote nothing, or returned FALSE).
  - `src/debug/fault_guard.cpp`: a fault nothing handles now logs a host stack
    (module+offset) and aborts, instead of re-running the faulting instruction forever.
    One run with the copied save hit a null read (`read of guest 0x00000000` on one
    thread, logged 1.6 million times: 260 MB in a minute) 30 s after entering single
    player; the next identical run did not.
  - That fault, chased with a repeat script (copy the Xenia save, START, Single Player,
    watch for 110 s): it hits in roughly half the runs, always while free roam loads
    around the saved position, never in the prologue. Two forms: a read of guest
    `0x0000000F` and a write of guest `0x701F0120`, both under `sub_82D3DB00` (called
    from `sub_82D44B90` <- `sub_82D42638` <- `sub_82D43818` <- `sub_82D4D9D0`/
    `sub_82D4DCC8`), the loader of world physics/collision definitions (strings
    `PhysicsDefinition`, `ConvexHull`, `BreakSpeedMph`...). `sub_82D3DB00` keeps an
    array of at most 8 records of 544 bytes in its 4544-byte frame and fills one per
    entry of a 12-byte record chosen by its index argument (`*(r3+84) + index*12`,
    count byte at +9, list pointer at +4). The bad runs read a count above 8 or a
    pointer of 0xF there: the record is garbage, and the loop writes past the frame,
    through the thread's PCR (`r13` = `0x701EE890`) up to the end of the stack at
    `0x701F0000`. So the input is not ready or not what the game expects when it is
    read; the timing dependence points at synchronization (a race the console's timing
    hides, or an emulated primitive that misbehaves). Ruled out: the critical-section
    replacement (it faults with `FORZA_SDK_CRITICAL_SECTIONS=1` too) and fiber
    migration between threads (`FORZA_FIBERTRACE=1`: the game switches fibers once).
    Fixed later the same day (Xenon dot-product overflow, see below).

- 2026-10-10 (evening): **any controller gets past the title screen; Xenia saves from
  `play.sh`; perf tooling from Reach.**
  - The user was stuck at the title screen: the runtime signs in one profile, on user 0,
    and gives every SDL pad its own user slot in connection order. With Steam Input and
    Sunshine virtual pads present, the user's pad was not slot 0, so START came from an
    unsigned user and the game waited for a sign-in nobody could give (reproduced with
    `FORZA_PAD_PER_USER=1` and a scripted press on slot 1: `PAD1:START` on the input FIFO).
    `src/kernel/input_autopress.cpp` now merges every pad into user 0 (buttons combined,
    strongest stick/trigger wins, vibration to all) and reports users 1-3 as without a
    controller: START on a slot-1 or slot-2 pad reaches the first-run screen and the main
    menu from a fresh data folder.
  - `tools/play.sh --xenia [XUID]` plays a copy of the user's Xenia save signed in with
    its XUID (`~/.local/share/forza/xenia-<XUID>/`, DLC and shader cache shared).
  - Frame statistics, Vulkan hooks (present-mode log, present and pipeline counts), the
    FIFO present-mode default and the on-demand achievement toast (the SDK's toast kept
    the UI thread repainting nonstop) ported from RecompiledHaloReach. SDK patch 0024
    (a presenter hook) is gone again: the Vulkan hooks do it without an SDK change.
  - Diagnostics for the physics fault: the item index that overruns `sub_82D3DB00`'s
    array is the low byte of the w lane of a 16-byte closest-point result
    (`sub_82D44B90` r1+384/+400, written whole by `sub_82D41F28` from GJK vertices with
    `vmaddfp`/`vsel`); in failing runs that lane holds pointers or 0xBEBEBEBE fill, i.e.
    the vertex data's w lanes carry garbage. Next: who writes the vertex records
    (r3+0x1AA0 + id*32 in the solver workspace).
- 2026-10-10 (night): **Xbox LIVE through a Forza Live server; Gold; the party
  lobby.** The user saw "You must have an Xbox LIVE Gold Membership" and "no connection
  to Xbox LIVE" under Multiplayer.
  - RecompiledHaloReach's Reach Live client is ported (`src/kernel/identity.cpp`,
    `xam_signin.cpp`, `net.cpp`, `live_xmsg.cpp`, `live_tcp.cpp`, `live_lsp.cpp`,
    `src/platform/socket.*`) with its server (`server/forza_live_server.py`, UDP 21200 and
    HTTP 21201; Reach Live keeps 21100/21101, and an instance of each game can run side by
    side). `tools/play.sh` starts a local server unless `FORZA_SERVER` or `live_server`
    names one (`--offline`: none). The game signs in to LIVE as user 0 with an online XUID
    (`0009...`, kept in `<data>/forza/4D5309C9/live_identity.txt`) while the save stays
    tied to the offline XUID.
  - Gold, always (`xam_signin.cpp`): `XamUserCheckPrivilege` grants everything,
    `XamUserGetMembershipTier[FromXUID]` is Gold, sign-in state 2 with the LIVE flag.
  - Entering Multiplayer then crashed (read of guest `0xC` in `sub_82CA41C8`). Two causes:
    - `XUserReadStats` (XGI `0xB0021`): the SDK answers success and leaves the results
      empty; the game reads its TrueSkill row (column 0 as an int64, two doubles: mu and
      sigma) through the empty view list right after creating the party session.
      `live_xmsg.cpp` now writes a row per player and view: the server's
      (`server/forza_live/competition/stats.py`, which also keeps and rates what `XSessionWriteStats`
      writes at the end of an online game; `docs/title_services.md`, "Every
      leaderboard"), or a new player's defaults (skill 1, mu 3.0, sigma 1.0, other columns
      null).
    - The session socket: Forza's session networking (`0x8313xxxx`-`0x8315xxxx`) opens a
      VDP socket, binds it, reads its port with `getsockname` (an SDK stub), and keeps an
      overlapped `WSARecvFrom` posted on it (`sub_83143C40`; `WSA_IO_PENDING` hands the
      OVERLAPPED to an I/O object that waits on its event, `sub_831425C8` collects it
      with `WSAGetOverlappedResult`). The SDK's `WSARecvFrom` always fails, so the socket
      was closed again at once. `net.cpp` now does overlapped receives on the virtual
      sockets (pending receives complete when a datagram arrives: buffers, sender,
      OVERLAPPED status and byte count, then its event), `WSAGetOverlappedResult`,
      `WSACancelOverlappedIO`, `WSAEventSelect`, `WSASendTo`/`WSASend` (gathered,
      completed at once), `getsockname`, `getsockopt` (it probes `SO_RCVBUF` first),
      `getpeername`. The game's Winsock wrappers are at `0x82A628C8`-`0x82A62B88`.
  - Multiplayer now opens the PARTY LOBBY (Find Game, Find Custom Game, Create Custom
    Game, Difficulty, Invite Friends) with the player listed and a hosted party session
    (flags `0x823`: host, presence, peer network, friends join via presence).
  - "The Horizon server is currently unavailable, you will not be awarded XP for online
    multiplayer games": Turn 10's title services. The game enumerates title servers
    every 30 s and keeps one whose description matches the service name it wants
    (wildcards `*`/`?`, `sub_83153D48` called from `sub_83155238`); it talks "Xls Client"
    HTTP/1.1 to it (`POST /Services/o.xtsw`, `Content-Type: bin/xtsw`, `X-TitleId`,
    `X-ClassName`, `X-Method`, `X-UserId`, `X-TransactionId` headers). Our title server
    is now described as `*`, so every service resolves to the Forza Live server, whose
    `forza_live/title_services/lsp.py` logs the requests.
  - MISSING CONTENT: not reproduced in the user's flow (main menu, Multiplayer) with all
    23 DLC packages installed. The license path was checked: each DLC's license mask
    comes from `XContentCreateEx` (`sub_825166xx`, the package header written by
    `tools/install_dlc.py`: all bits) and feeds `UPDATE ContentOffers SET IsInstalled=1,
    IsPurchased=(LicenseMask & %u)=LicenseMask` (`sub_825162D0`); the only
    `XamContentGetLicenseMask` caller (`sub_82C01748`) ignores the mask, so the SDK's
    `license_mask` setting does not matter here. The messages are chosen in
    `sub_826B4200` (lobby: DLC / Disc 2 / unknown) and by a content check
    (`sub_825A56E0` returning 4) before `0x8275C238`.
- **Status and open items (end of 2026-10-10):**
  - Xbox LIVE: signed in, Gold; two players in one party, online free roam and an online
    race work (see "two players online" below). Open: matchmaking ("Find Game", "Find
    Custom Game") and Turn 10's "Xls" title services (XP), which come together.
  - MISSING CONTENT: not reproduced (see above). Next: the user's exact flow with
    `--xenia` and the place it shows; log `sub_825A56E0`'s result and the content id.
  - Free-roam physics crash (`sub_82D3DB00`): fixed, see the next entry.
  - Performance: no clean-window numbers yet (docs/perf.md has contended ones only).
  - Frame-rate unlock: done, `frame_rate = 60` (see "60 fps" at the end of the log).

- 2026-10-10 (late night): **the free-roam physics fault is fixed: Xenon's dot product
  returns QNaN on overflow.**
  - Not the SDK bug RecompiledHaloReach found the same day (ObDereferenceObject on an
    XEnumerate task's X_KENUMERATOR, which has no dispatcher header, releases whatever
    object took its stale handle, running threads included). A port of Reach's
    `object_refs.cpp` in trace mode logged no dereference of an object the runtime does not
    own in 3 loads, 2 of which faulted. Forza has the same XAPI code (XEnumerate
    `sub_830EC788`, task body `sub_830EC680`), but this flow does not run it; the port was
    not kept.
  - The bad item indices come from a GJK simplex vertex that was never written.
    `FORZA_PHYS_SEED=1` (new hooks in `src/hooks/physics_loader_check.cpp`: before each
    query, fill the w lanes of the EPA workspace's 30 vertex slots and of each new simplex's
    four vertices with sentinels naming the slot) made the fault hit 3 of 3 runs, always
    with simplex vertex 0's sentinels (`+396 CCCC00C0 +412 BBBB00C0`). Without seeding that
    slot holds whatever an earlier call left on the stack (0xBEBEBEBE is the SDK's fill of
    a new thread stack, 0x82B8D9C0 a function pointer), so it faulted only when that
    leftover made a large index.
  - Why: the GJK (`sub_82D43290`) starts a new pair with the estimate v = splat(-FLT_MAX)
    (`sub_82D12178`) and the previous squared distance (FLT_MAX, -FLT_MAX, 0, 0)
    (`sub_82D41210`). Its termination test (`sub_82D420F0`) computes |v|^2 with
    `vmsum3fp128` at `0x82D42120` and stops when FLT_EPSILON * previous >= previous - |v|^2
    in every lane. 3 * FLT_MAX^2 overflows. Xenon's VMX128 dot products return QNaN on
    overflow (Xenia emulates this, see its `use_fast_dot_product` option), which makes the
    test false. The codegen's SSE `dpps` returns +infinity, so the test passed, GJK
    returned before adding its first support point, and the closest points came from an
    empty simplex (`sub_82D41608` -> `sub_82D0F6E0`), their w lanes from vertex 0.
  - Fix: `src/hooks/gjk_dot_overflow.cpp` (`hints/gjk_dot_overflow.toml`), a hook right
    after that `vmsum3fp128` that turns an overflow of finite inputs into QNaN
    (superseded by SDK patch 0025 the same night, see below; the hook is gone).
    `FORZA_GJK_DOT_INF=1` brought the infinity back. The general fix belongs in the SDK's
    codegen (`build_vmsum3fp128`/`build_vmsum4fp128` in `sdk/src/codegen/builders/vector.cpp`
    emit a bare `simde_mm_dp_ps`); not done here, because it needs a codegen build and a
    rebuild of all generated code. Other dot products in the game may rely on the same
    overflow result.
  - Measured with a repeat script (now `tools/repro_freeroam.sh`; the user's Xenia save, START, Single Player,
    up to 2 minutes of free roam): before, 2 of 3 loads faulted
    ("about half" over the earlier runs); with seeding, 3 of 3. After: 10 of 10 loaded and
    ran free roam without a fault (each ~2 minutes in, 30 fps, the Aventador on the road
    in a screenshot); with seeding, 2 of 2.
- 2026-10-10 (late night, later): **every dot product now overflows to QNaN, in the codegen
  (SDK patch 0025, shared with RecompiledHaloReach).**
  - `build_vmsum3fp128`/`build_vmsum4fp128` keep `dpps` and branch on the exponent of its
    result; a non-finite result is redone as Xenia's `DOT_PRODUCT_3/4` (float64 products and
    sums, in Xenia's order): an overflow of finite inputs is QNaN `0x7FC00000`, infinities
    and NaNs from the inputs pass through. 2883 sites in this game. Reported upstream as
    rexglue/rexglue-sdk#491, with new cases in the SDK's PPC tests (all 1468 pass).
  - `tools/build_rexglue_sdk.sh` now also builds `rexglue` into the overlay's `bin/`, and
    the build runs codegen with it (`FORZA_REXGLUE`); a changed tool drops codegen's
    stamps, so the next build regenerates everything. The GJK hook
    (`gjk_dot_overflow.cpp`, `FORZA_GJK_DOT_INF`) is gone.
  - `tools/repro_freeroam.sh` (the user's Xenia save): 10 of 10 loads ran free roam without a
    fault (29.8-30.0 fps at 175 s), with the codegen fix alone.

- 2026-10-10 (night, later): **two players online: one party, online free roam and an
  online race, each seeing the other's car.**
  - The last two-instance test misread the menus: one scripted DOWN (0.3 s hold) moves
    the lobby cursor two rows, so DriverB had picked "Create Custom Game", not "Find Custom
    Game" (`DOWN:0.1` moves one row). Neither instance ever searched for a session.
  - "Invite Friends" in the party lobby calls `XamShowFriendsUI` (the Guide's friends
    list; the player sends invites from there), an SDK stub. `live_xmsg.cpp` now answers
    `XamShowFriendsUI` and `XamShowGameInviteUI` by inviting every other player in the
    server room to the session a friend should join (`InviteSession`). The rest was
    already there: the invitee's game auto-accepts (`live_accept_invites`), takes the
    session from `XInviteGetAcceptedInfo`, leaves its own party (`XSessionLeave`), looks
    the host up (`XNetQosLookup`, from the host's presence), joins the party session
    (`XSessionCreate` without the host flag, `XSessionJoin`) and the two talk directly
    (UDP 2130x on one machine, "direct path to player").
  - Test (two `tools/play.sh` instances, DriverA and DriverB, a private server on UDP
    21220 / HTTP 21221, fresh profiles): both open Multiplayer; A: Invite Friends ->
    both party lobbies list DriverA and DriverB, B shows "Please wait while the party host
    selects a race". A: Create Custom Game (a "Private Custom Circuit Race" with both
    players), Setup Game, Game Type -> Free Roam (sixth step right), B (back) -> "save
    now?" YES (NO drops the changes), Start Free Roam; B: Ready. Both load online free
    roam at the festival ("Co-op challenges"; host: "select a co-op challenge from the
    pause menu", B: "waiting for host"), with DriverA and DriverB in the HUD list. A sees
    B's car with its "1 DriverB" tag driving up, B sees A's. The host's game session has
    flags `0x2D` (host, stats, matchmaking, peer network); B joins it on its own (it
    hears of it through the party), both call `XSessionStart`. Peer traffic is about 65
    datagrams/s each way in free roam. Second test: a 1-lap Circuit Race (Beaumont
    Circuit) from the same lobby: both on the grid, each seeing the other's car, racing
    (A 2nd in a Fiat 500, B 1st in a Nissan Skyline). No faults in either test.
  - Matchmaking and title services: "Find Game" / "Find Custom Game" depend on Turn 10's
    hopper services (`ForzaHopperGetList`, `ForzaHopperGetSearchSettings`,
    `ForzaHopperGetSessionListFilterSettings`, `ForzaHopperReportQosData`; the lobby has
    `IDS_LobbyLspUnavailableDefaultHopper` for when they are down). The title-server
    choice was misread before: `sub_83155238` calls `Matches(mask, description)`
    (`sub_83153D48`, logged as `Matches("%s", "%s") == %d`) with the game's service mask
    as the pattern (`*`, `?`, case-insensitive; the config's `defaultMask` is `Turn10.*`)
    and our description as the text, so the description `*` never matches and the
    enumeration fails with `0x8054001F` every 30 s. A description such as
    `Turn10.Horizon` (`FORZA_LSP_SERVICES`) should make the game connect (port 80,
    `/Services.Horizon/o.xtsw` per the LspProvider config) and send its first requests.
  - Next: set the description and record the requests (`--dump-requests`); then answer
    the hopper calls in Turn 10's `bin/xtsw` format (unknown yet) so that "Find Custom
    Game" searches and players can find each other without an invite; join in progress
    from the friends list (presence) is untested.

- 2026-10-10 (late night): **Turn 10's title services: the Horizon sign-in, Find Game
  matchmaking and online XP** (format and services in `docs/title_services.md`).
  - The title server now describes itself as `Turn10.Horizon` (the game's mask is
    `Turn10.*`), and the game calls Turn 10's "Xls" services on it: HTTP POST
    `/Services.Horizon/o.xtsw`, `bin/xtsw` bodies. The format came from the game's type
    descriptors (9 words per field; member lists filled at startup, read from a running
    game's memory) and the reply parser (`sub_82AA96F0`: a 12-byte status header, then
    the results, `sub_82A95910`). Packed big-endian, u32-length-prefixed strings, byte
    arrays and arrays; the 111-byte `Logon2` request decodes exactly. No hashes or
    signatures anywhere.
  - `server/forza_live/network/xtsw.py` (codec, the message types used) and `forza_live/title_services/lsp.py`
    answer `User.Logon2`, the profile uploads, the hopper calls (`GetList`,
    `GetSearchSettings`, `GetGameOptions`, `UserRegister`, `GetRaceMultipliers`, ...);
    any other method gets success and zero bytes, which reads as empty results. The HTTP
    side keeps connections alive (the game sends `Connection: Keep-Alive`).
  - Playlists: hoppers with `maxAllowedRaces` etc. at 0 were all filtered out (the
    beginner-playlist check, `sub_828864D0`; -1 is no limit). A failed `GetGameOptions`
    disconnects; an empty one gives a lobby with "content not installed" (track 0). The
    round XML follows the game's `DefaultRacesHopperOptions.xml` (zip method 21 =
    XMemCompress LZX, decompressed with libmspack); tracks from the game database's
    `HopperRaces`.
  - Matchmaking: Find Game searches with `XSessionSearchEx` (0xB001C, property
    0x10000030 = hopper id), which went to the SDK (no results), so everyone hosted;
    `live_xmsg.cpp` now answers it like `XSessionSearch`.
  - Leaving an online race faulted both games: the game unloads and reloads
    XMediaFacade, and the SDK's unload looks the recompiled library up by the module's
    resolved path (no match), so the reload is refused ("already loaded; refusing
    duplicate load") and the game reads through a null module. `module_resident.cpp`
    keeps the facade DLLs loaded on their last `XexUnloadImage` (the SDK bug itself is
    not patched).
  - Test (two `tools/play.sh` instances, private server, `--dump-requests`): both Find
    Game -> Races; B joins A's lobby ("Clear Springs Run", intermission), both race
    (A driven along the minimap's route by a script, B stuck): A 1st, "XP REWARDS" 1,320
    XP (level 2 -> 3), B 935 XP, credits 500 -> 4,250 and 1,125; then the next round's car
    class vote. After a restart, credits came back from the save but online XP was 0:
    the game takes online XP from `Logon2` (`userData.currentXPTotal`), so the server
    keeps it (`players/<XUID>.json`, from `UpdateAll`); with that, both restarted at
    level 3 / 2 with their XP.
  - Next: the Playground playlist's game (preset 7) untested; Find Custom Game; the other
    services (storefront, auctions, scoreboards) answer empty.

- 2026-10-10 (late night): **60 fps: `frame_rate = 60`.** Details in `docs/framerate.md`.
  - The game's vblank callback (`sub_8257E888` → `sub_8247C678`) sets the main loop's
    event every second vblank; the main loop (`sub_823ED888`) waits on it and takes its
    frame time from `KeTimeStampBundle->TickCount` (whole milliseconds; a 16-17 ms frame
    counts as 1/59.94 s, the engine's 60 fps path from Forza Motorsport 4). So a 120 Hz
    guest vblank runs the game at 60 fps with its simulation still in real time; the
    checks below found nothing that counts frames or vblanks. Developer options in the binary (`unlockframerate`,
    `framesperrender`, `frontend30fps`, `lockupdatetimeto60fps`, `forcecarphysicsupdatehz`,
    ...) show the same design; retail has no command line to set them.
  - Checked: the race clock of a festival race runs at real time at 30, 60 and a 120 fps
    target (240 Hz vblank), the car accelerates the same (0-100 km/h in about 2.8 s), no
    rendering faults in free roam or races, intro videos keep their speed, 10 free-roam
    loads at 60 fps without a fault (`tools/repro_freeroam.sh`, `REX_FRAME_RATE=60`).
  - Clean window (docs/perf.md), 30 → 60: main menu 30.0 → 60.0 fps, free roam 29.9 →
    58.9 fps (p50 16.8 ms, p95 19.5), a race with 7 AI cars 30.0 → 41.6 fps (p50 24.8 ms).
    In races the render thread spins in D3D's `BlockOnFence` (`sub_823E91F0`) about 70% of
    the time, waiting for the GPU emulation, at both 120 and 240 Hz: faster GPU emulation
    is the next step for 60 in races and for more than 60.
  - `FORZA_XMP_NOSLEEP` is not needed: free roam holds 60 fps with the SDK's sleep.
  - The Xenia report (textures and physics break when unlocked): Xenia's `vsync = false`
    is a 1 kHz guest vblank, so the game runs uncapped and its frame time becomes 0-3 whole
    milliseconds; not reproduced here (the port does not run that fast).

- 2026-10-11: **a shared world: the Horizon World playlist** (phase 1 of
  `docs/online_vision.md`, which has the details, the tests and the next phases).
  - Find Game's first playlist (hopper 3) is online free roam (preset 7, festival). The
    server answers its searches with the open worlds, fullest first, so players gather in
    one world of up to 8; nobody open: the player hosts one. Searches now key on Forza's
    hopper property `0x10000030` (the client knew only Reach's `0x10000015`).
  - Find Game's lobby has no join in progress for free roam (the joiner waits for the round
    to end; Find Game's free roam runs as mode 7, a custom game's as mode 9, which offers
    JUMP IN): the world's rounds end every 10 minutes (`--world-round-minutes`).
  - Host leaving: the game migrates on its own (no `XSessionMigrateHost`, no XSession call
    at all), so guests publish a standby record of their world and the server advertises
    it once its host is gone; a returning player was placed into the remaining player's
    world. `XSessionMigrateHost` is answered anyway.
  - Cap: the round's `MaxPlayers` sizes the session (16 served -> 16 slots); the game's own
    UI and data stop at 8.

- 2026-10-11 (later): **drop-in: the Horizon World is one endless free roam that players
  JUMP IN to, and it starts with one player** (`src/hooks/world_drop_in.cpp`; details and
  tests in `docs/online_vision.md`).
  - Find Game runs as mode 7 of the online game (`*(0x83305CC4)+40`, set by `sub_828733A8`
    from the launch `sub_8287D528`; Find Custom Game is 10, a custom host 8/9). Three things
    hang on it, and the world (playlist 3, a preset 7 round, mode 7) now gets the custom
    free roam's version of each: the "custom game" check `sub_82865340` answers yes at the
    join-in-progress call sites (lobby label, JUMP IN button and its availability, the
    host taking a joiner's ready flag, peers adding the joiner's car); the launch
    `sub_8286A2E0` makes it race type 18 (online free roam flow) with HUD mode 17 instead
    of 6 (online race) / 21 (hook on `sub_825548A0`); and a host alone in its lobby gets
    the team/bucket check run (`+2152`, hook on `sub_82875FB8`), which Find Game otherwise
    runs only when a remote player's status changes, so the 5 s intermission starts.
  - Guest functions are weak symbols: a hook is a strong `extern "C" REX_FUNC(sub_X)` in
    `src/` that calls `__imp__sub_X`; `ctx.lr` holds the guest return address, so one call
    site can be told from another. No codegen or hint change, only a relink.
  - Tested with two instances: solo start, drop-in (JUMP IN, about 30 s to be driving),
    each seeing the other's car, the host leaving (the client reports the game's new host
    to the server: `MATCH_PUBLISH` op 4, from the game's host flag `+344`) and rejoining,
    and the host's END FREE ROAM (everyone back to the lobby and into the next round).
  - The free roam flow ignores a timed end condition, so the server's world rounds are
    endless by default now (`--world-round-minutes 0`).

- 2026-10-11 (night): **events from the Horizon World** (phase 2 of
  `docs/online_vision.md`: details, traces and tests there).
  - The Forza Live server starts one of the game's festival races in a world (on request,
    `GET /world/event`, or every N minutes): it tells the world's host (UDP message 18),
    whose client (`src/hooks/world_events.cpp`) asks for the next round again (the game
    asks `GetGameOptions` for round N+1 when round N starts), gets the event, and ends the
    free roam the way END FREE ROAM does (its player's status 5). The lobby waits 30 s,
    the event runs, the world comes back.
  - CPU drivers: the online lobby already has them (`MaxAIPlayers`; the host keeps
    min(MaxPlayers - humans, MaxAIPlayers), names from `AIPlayers`, cars of the bucket's
    class). To race they must be set "in game" with the host's player; kicking one throws
    the host out of its own session; a nested removal crashed the host (all fixed). With
    two consoles the CPU drivers are spread over them; both race fine, but back from the
    race one console crashes or hangs, so multi-human events run without CPU drivers
    for now (`world_event_cpu_with_peers`).
  - Hooks call guest functions on a copy of the context (`CallGuest`) and walk the player
    manager's guest `std::list` on the guest stack (`ManagerPlayers`).
  - `tools/ghidra_decomp.sh` keeps a headless Ghidra running on `ghidra_projects/ForzaHorizon`
    and decompiles functions on request (it repairs the no-return `__savegprlr` helpers
    that cut the import's functions short).
- 2026-10-11: **SDK patches 0034 and 0035 (shared with RecompiledHaloReach).** 0034 makes the
  SDK's GLSL tessellation shaders read the system constants from set 1. They read them from
  the shared memory buffer, which gave them garbage factors and index bounds. 0035 draws
  tessellated triangle strips and fans as lists (Halo: Reach's cinematic faces). Forza issues
  no such draws. Free roam was smoke-tested with both: it boots, renders and drives at 30 fps.
  Details: RecompiledHaloReach `docs/rendering.md`.

- 2026-10-12: **120 and 144 fps.** `frame_rate` takes 30 or 60-240 (details and
  measurements in `docs/framerate.md`).
  - Above 60 the game's frame time (whole milliseconds from `KeTimeStampBundle`) jittered
    5-9 ms at 144 and moved the car unevenly (16.5% frame-to-frame); `src/hooks/frame_time.cpp`
    answers the main loop's `sub_8247C168` call with `f31` from the guest tick clock (the
    nominal vblank time of an on-target frame), so frames get exactly 1/144 s and motion is
    even (2.8%). The guest vblank runs at twice the frame rate; SDK patch 0037 lets it reach
    288 Hz (the SDK capped it at 240), without which 144 needs a 144 Hz vblank and drops to
    72 fps when the GPU emulation misses a vblank.
  - Clean windows: free roam 115 fps at 120 and 112 at 144; races 76 and 70 (bound by the
    GPU emulation; the main loop still runs at the target). The race clock, the
    acceleration, an autodriven lap (114.6 / 114.6 / 115.1 s at 60 / 120 / 144) and the
    audio rate are unchanged.
  - Online: 144 against 60 races fine (each car's copy within 0.4 m of the real one, about
    20 ms ahead), the 144 player sends up to twice the datagrams (about 50 KB/s per peer).
    Rivals ghosts play back the same at 60 and 144, and one recorded at 144 is the size of
    a 30 fps one and plays back at 60 in step with the lap that made it.
- 2026-10-15: **Paint Shop / outpost garage / Autoshow crash (#42, #55, #57)**: a guest read
  of 0x1A4 on guest thread F8001188 (start `sub_82F0CF00`) as the screen opens. A libjpeg
  decode (`sub_82AC4728`, TU4) calls the CRT `setjmp` (`sub_82B3F830`); libjpeg's
  `error_exit` calls `longjmp` (`sub_82B3F300`) when the data does not decode. Both were
  plain recompiled guest code, so the longjmp returned into the host frame that called it
  with the saved registers (r31 = 0) and `read_markers` (`sub_82ADA2A0`, cinfo+420 =
  `unread_marker`) read 0x1A4. Fix: `setjmp_address` / `longjmp_address` in
  `hints_tu4/rexcrt.toml` (base: `hints/rexcrt.toml`) make the codegen emit host
  setjmp/longjmp (a codegen run, 28 files change). Needs no readback setting. Why the
  decode fails is open: the game now takes its error path instead of crashing.
- 2026-10-15: **Crash after buying the first car, then endless loading (#66, #63)**, one
  player (Windows, RX 6700 XT, v0.1.7, TU4 + DLC, `frame_rate` 240). The log: a new
  career, the "Take Her for a Spin!" achievement (156, a car bought in the Autoshow), 5 s
  later a guest read of 0x4 on the render thread (call chain `8249E118 824A1E6C 8247F9E4
  82616BFC 8261E1C0 82B3A4FC`, TU4: the render thread's command list `sub_8247F818` runs
  `sub_824A1E40`, which waits on one of 12 slots and applies its state with
  `sub_824469B0(device, slot+0x14, 0)`); every later start loads the career straight
  away and stops right after `joining session 0000000000000000`, the loader thread
  silent. Not the libjpeg longjmp (that fix is in v0.1.7). Not reproduced on Linux
  (TU4, scratch profiles, one game at a time): at 240 a new profile plays the intro and
  the opening drive; the Xenia save buys a car in the Autoshow (also at 30 and 60),
  returns to free roam with it and loads Single Player after a restart, also after a
  hard kill 5 s after the purchase. 240 is not the cause seen here. Open: the player's
  save folder (`ForzaProfile`) would show whether the load hang comes from the save.
  The fault line now names the pc as `forza.exe+0xOFFSET` on Windows, with a host stack
  (`src/debug/fault_guard.cpp`), so the next Windows crash log maps onto the executable.
  Seen on the way, every rate: the thumbnail made at a purchase is nearly black
  (`Thumbnail_46.xdc`, 23,734 bytes).

- 2026-10-16: **Endless loading after the first save (#76)**, Windows 11, RX 9070 XT, v0.1.8,
  TU4 + DLC, 240 fps, local server. The first session (new career, prologue, first race, the
  Corrado, save, Alt+F4 at the title screen) played; every later start stops right after
  `joining session 0000000000000000`. The save is not the cause: its files are complete (TU4's
  `VersionFlags` 4/2 and `NeedsAA`, sizes as ours), and a copy loads into free roam on Linux
  (TU4, 240 fps, local server) and in the TU4 Windows build under Proton/RADV with the player's
  picture settings (1440p, render scale 2, 16x AF, FXAA extreme), a warm shader cache and the
  player's DLC set (#76 and #66 have the same one: `DLCRoot1` without `zipmount.xml`, no Season
  Pass package; copied that way, it loads too). #66's last session hangs with an empty shader
  cache, so that is not it either. What the hang logs (#76, #66) share: SpeechFacade loads on a
  job worker, the game thread (`F800007C`, the `[tN]` of the `joining` line) logs the join, and
  nothing follows; normally the loading fiber unloads XMediaFacade 0.4-0.9 s later. Traced with
  gdb on Linux: after the join the game thread answers XSessionCreate, converts to a fiber,
  creates the loading fiber (`82CCB7A0`, 128 KB) and switches to it, which queues the world's
  jobs; the speech job ends (both `XamContentOpenFile("nuispeech", ...nuisp1033)` fail as in
  Xenia) and its worker goes back to its idle wait. So on the player's PC the game thread or its
  loading fiber stops before the unload, with nothing logged. Windows-only differences seen on
  the way: the I/O worker the game starts with creation flag 0x20 (`82B317A8`, base `82A746F8`:
  an IRP queue served under a spin lock) runs at THREAD_PRIORITY_ABOVE_NORMAL on Windows (the
  SDK's `set_priority(1)`; EPERM on Linux, ignored by Wine), and host fiber stacks reserve 1 MB
  on Windows (256 KB on Linux). Not reproduced; the log of the next hang will say more: closing
  the window now logs every guest thread's last call site and call chain (`LogGuestThreads`,
  `src/debug/fault_guard.cpp`).
  Windows now behaves like Linux here: `FORZA_THREAD_PRIORITY` (default `normal` on Windows,
  `game` restores the request) keeps a guest priority request from raising a host thread above
  normal, on thread creation (flags 0x20/0x40, set by the SDK in `XThread::Create`) and in
  `KeSetBasePriorityThread`; one log line per clamped thread (`src/debug/thread_trace.cpp`).

- 2026-10-16: **"MARKETPLACE ERROR: not enough space for the marketplace save file" when
  entering Horizon Rally (#79)**, Windows 11, Arc B580, v0.1.9, ISO + TU4 + DLC, public and
  local server. Not the free space: the SDK answers `XamContentGetDeviceData` with constants
  (3 of 20 GB) on every host, and the 15 MB `tmpfile` the game writes into a new
  "marketplace" save works (an ENOSPC shim on its `ftruncate` only drops the save). The
  server's empty answer to `DLCMetadata.GetStringTableInfo` (`Id` 0, no `Path`) made the
  marketplace manager (`0x833F4CEC`, steps in `sub_829540B8`) download content 0 and save it
  as `marketplace:\` + "" (`sub_829536A0`): the open fails on Linux too (the three
  `NtCreateFile FAILED: path='marketplace:\' -> 0xc00000ba` lines every online start), the
  failure handler `sub_82950A10` sets the manager's state to 3, and `sub_82753DB0` shows the
  error for state 3 (title `MARKETPLACE ERROR`, the "damaged" text for error 2, else "not
  enough space"). The game runs that check on the way into Horizon Rally and from some
  free-roam menus; with the Xenia save (rally already started) the main menu's HORIZON
  RALLY does not call it, so Linux never showed the dialog although the state was 3 there
  too. The server now answers `Id` -1, the game's "no table": no download, no save, state 1
  (Linux, TU4, local server: no failed open, state 1 instead of 3, Rally starts). Needs the
  public server updated and the launcher's built-in server from the next release. The three
  "Unhandled guest access violation: read of guest 0x708E0003" in that log came from
  closing the window: `LogGuestThreads` (v0.1.9) walked a thread's stack chain into a
  released page; the chain now stops at unreadable pages (`src/debug/fault_guard.cpp`).
  `ResolvePath(\Device) failed` is the game's `\Device\Image` probe, in every log.
  Follow-up the same day (v0.1.10): the player's new screenshot shows the other text,
  "XBOX LIVE MARKETPLACE ERROR: Failed to download required Xbox LIVE Marketplace data". The
  game in that log mounts 17 DLC packages (the `DLCRootN:` lookups; 21 in the player's first
  sessions, 23 here) and none of them is the Rally Expansion Pack (`1600_pri_65`). Reproduced on
  Linux with the player's ForzaProfile, TU4 and the DLC without that package: the main menu's
  HORIZON RALLY gives that message (marketplace manager in state 1, error 0, so the earlier
  fix holds); with the package (with or without its header) the same save starts the Rally
  intro. Before b0daca0 the manager was also in state 3, which picks the "not enough space"
  text. The game now logs its DLC packages and offers at start and warns when a TU4 build
  has no Rally Expansion Pack; the launcher warns at Play (docs/dlc.md, "Horizon Rally").
- 2026-10-16: **The game closes 0.3 s after accepting an invite in free roam (#81)**, Windows
  11, v0.1.10, TU4 + DLC, public server: the log ends with `FORZA_LIVE: joining Driver4799's
  game` and `Execution complete`, no crash. In free roam the game joins a friend by
  restarting itself: its invite state machine (`sub_8291A298`, TU4) keeps the accepted
  `X_INVITE_INFO` (0x58 bytes) as launch data and calls `sub_824AC7E8`, which launches its
  own executable (`XamLoaderLaunchTitle("default.xex", 0)`); in the menus (main menu, party
  lobby) it joins in place. At boot `sub_824B32E0` reads the launch data (0x58 bytes: the
  invite, +496 of the game object; or the text "softreboot", which `sub_824AC7E8` sets when
  it has none) and sets the "soft reboot" byte +39, with which the boot skips its movies and
  goes straight to the invite's party. The SDK's `XamLoaderLaunchTitle` only ended the
  title. `src/kernel/xam_loader.cpp` now starts the game again with the same command line
  and hands it the launch data in `relaunch_launch_data.bin` (next to `live_identity.txt`,
  read only by a run with `FORZA_RELAUNCHED=1`): on Linux by `execv` in the same process
  (same PID, so the launcher and scripts keep waiting on it; descriptors close on exec and
  the guest memory file is unlinked first); on Windows a run from the launcher exits with
  code 75 and the launcher starts it again (keeping its local server), otherwise the game
  starts a new process of itself. The byte +39 stays set after the boot, and with it
  Single Player never loaded afterwards (the main menu stayed up; same with "softreboot"
  data alone; clearing the byte in a running game fixed it): the main loop's entry
  (`sub_823ED888`, base) clears it. `title_relaunch = false` restores the old exit.
  Tested (Linux, TU4, two instances, local server): B in free roam, A in the party lobby
  picks Invite Friends; B restarts within 1 s and is in A's party lobby 4 s after the
  invite (`screenshots/2026-10-16_invite_in_free_roam_A_top_B_bottom.jpg`); B leaves the
  party and Single Player loads free roam; an invite at the main menu joins without a
  restart. Windows (TU4 build under Proton/RADV, Vulkan, A on Linux, local server): standalone,
  B in free roam starts a new process within 0.4 s, the old one exits, the new one reads the
  launch data and is in A's party lobby 4 s later; with `FORZA_LAUNCHER_RELAUNCH=1` the
  game exits with code 75 (umu-run's exit status) and a start with `FORZA_RELAUNCHED=1`
  joins the party and appends to the same log. The launcher gives up on a game that exits
  75 more than 4 times within 60 s (`launcher/test_relaunch.py`).

- 2026-10-16: **Crash in races, read of guest 0x0AF048B8 (#83)**, Windows 11, RX 9070 XT,
  v0.1.9, render scale 2, offline, TU4 + DLC, after an hour of play. The fault is on the
  resource loader thread (`sub_82B3A4C0` starts it; `sub_82F0CF00` waits for work,
  `sub_82F0BEE0` pops a job, `sub_82F0B690` runs its state, `sub_82F0B2D0` calls the job's
  load, vtable slot 22), in `sub_82CE2A38` (TU4; disc `sub_82C222C8`), the load step of the
  cacheable resources (`CCacheableResource`, `CTextureResource`, `CTrackModelResource`,
  `CTrackSHZoneResource` and the other track and texture resources share it): it runs the
  resource's own load (slot 15), then takes the data object at resource+0x40, clears the
  field and calls the object's Release (vtable +12, `sub_82478698`: refcount at +0x20). The
  object (0x2EA0AD90) had a vtable pointer of 0x0AF048AC: it had been freed and its memory
  reused, so reading the Release slot faulted. A resource's unload (slot 16, e.g.
  `sub_82CE1E30`, `sub_82CE3000`) takes and releases the same field the same way, without
  a lock. Who freed it first is not known: not reproduced on Linux (TU4, Xenia save copies,
  `resolution_scale = 2`, the race scenario with 150 s races, 3 runs). `hints/resource_data_release.toml`
  + `src/hooks/resource_data_release.cpp` now take the field with an atomic exchange (two
  threads cannot both take one object) and drop an object whose vtable is not in the image,
  with a `RESOURCE:` line naming the resource's class (RTTI) and the object's first words.
  Mapping the player's `forza.exe+0x49970CF` took a rebuild: the player's executable is
  built on their machine and which `forza_recomp.N.cpp` holds a function comes from
  `codegen.partition.json`, which keeps every earlier assignment, so their layout follows
  their codegen history. A fresh codegen did not match; a codegen with the hints before
  v0.1.7 (no setjmp/longjmp lines), then one with v0.1.9's on the same output, compiled with
  the cross build (clang-cl 23.1.2) and linked with `/MAP`, put every frame on the guest
  chain (`sub_82CE2A38+0x1FF` and its callers) and the fault handler frames at the player's
  offsets exactly. The fault line now names the guest function itself
  (`forza.exe+0x... sub_XXXXXXXX+0xN`, from the recompiled functions' table), and the
  register dump printed r4..r31 and lr as r3..r31 (`PPCContext` keeps r3 first); fixed. Seen
  on the way: `extracted/analysis/tu4.elf` is a copy of `default.elf`, not the update's image
  (`tools/xex_image.py extracted/tu4/default.xex OUT --elf` makes the right one).

- 2026-10-16: **Horizon Rally, Kettle Hills stage 1: the road ahead missing, then the game
  hangs (#86)**, Windows 11, RX 6700 XT, v0.1.10, Direct3D 12 with resolution scaling, TU4 +
  the player's 22 DLC packages (the same package folders as ours, without the Season Pass),
  local server. The player closed three hung games in a row at the same place; each log's
  `guest threads at close` has the resource loader thread (`sub_82F0CF00` -> `sub_82F0B2D0`
  -> the cacheable resources' load step `sub_82CE2A38` -> a resource's deserialize
  `sub_82CE5140` -> `sub_82CE4BC0`) in the first read of the resource's header, inside
  `IOSys::CCompressedCacheStream::Read` (TU4 `sub_82D39EA0`, disc `sub_82C78C88`), at a
  different leaf each time: running, not waiting. Not #83 (that is after the load). The
  stream decodes a zip member whose compressed bytes are in memory (a block of the archive's
  cache, `+0x38`, or a page list, `+0x3C`): `Read` loops "decode a step (`sub_82D37380` /
  `sub_82C76508`) while bytes are wanted and the step says there is more", and the step (copy
  for method 0, inflate for 8, XMemDecompressStream for 21; fields: output `+0x24` of
  `+0x2C`, method `+0x28`, input left `+0x50`, read `+0x4C`) says "more" whenever the output
  is short of the member's size. If the input runs out first (or the decoder stops taking
  it), every later step copies nothing and still says "more": the loader, the only thread
  that streams resources, never comes back, so the road ahead is not streamed in and the game
  stops when the car gets there; the video shows exactly that (the road gone at 86 %, the
  picture frozen at 89 % with the race clock running).
  Not reproduced on Linux (TU4, Vulkan, a copy of the Xenia save, our DLC: Rally Heats
  finished, HORIZON RECOMMENDS bought the Lancer and its rally conversion, the gamedb in a
  copy of the update's media.zip with the championships' `PopularityPointsReq` at 0 so Kettle
  Hills opens, the stage driven to the finish at scale 1 at 25 m/s and at scale 2 at up to
  34 m/s; the road there, no hang). The ColoradoDirt zip (484 MB, 27,378 members, no local
  headers: the central directory points at the data) decodes completely with
  `tools/unxmem`, every CRC right, so our copy is sound; which member the player's loader was
  on, and why its input ran out, is not known (a damaged copy, or bytes in the cache block
  that are not the file's: Windows-only or Direct3D 12-only paths are the suspects).
  `src/hooks/compressed_stream_stall.cpp` now ends such a read: a step that wanted bytes but
  neither took input nor gave output 8 times in a row returns "no more", the read comes back
  short (as for a damaged member) and the loader goes on with the next resources; one
  `ZIPREAD:` line names the stream, method, sizes, positions and the cache block's state.
  Checked with `FORZA_ZIPREAD_TEST=N` (the N-th step loses its input, as in the hang): the
  stream of a 1.9 MB LZX member at the Rally load stopped at 589,824 bytes, the read came
  back, the Rally Heats started and played. The next log of this hang names the member.
  2026-10-17, v0.1.18 log (Windows, RTX 4070 laptop, Vulkan; a second Windows player has
  the same): 5 identical `ZIPREAD` lines, method 21, `32768 of 81792 bytes out, input 28426
  of 28426 read`, at `+D728A` of a different cache block each time, then guest access
  violations (the loader going on with a short resource). The only member of that size in
  our DLC is `coloradodirtout.09183.rmb.bin` in the Rally pack's `ColoradoDirt_pri_65.zip`
  (data at 0x5742A8A, CRC 988ea569): two full 32 KB LZX frames of 14,998 and 11,928
  compressed bytes and a last one of 16,256 bytes out. Not a Windows codegen difference: the
  game's XMemDecompressStream (TU4 `sub_831AE7D8`) with its 66 callees, cut out of the
  generated code into a harness (context made as `sub_82D38810` makes it: `sub_831AFBE8` /
  `sub_831AFBA0`, flags 1 = stream), decodes the member completely, CRC right, built with
  clang for Linux and with clang-cl for Windows and run under Proton, at any output size per
  call, with the input given in pieces, and for write-combined output. The log's numbers
  come out exactly when the compressed bytes after the first frame (from member offset
  ~15,000, cache block offset ~0xDAF22) are wrong: the decoder takes all the input, gives
  the first 32 KB and then nothing, no error. So the player's game had other bytes in the
  cache block there: a different copy of the zip, or a read that put other data into the
  block on Windows (no read failure is logged). A `ZIPREAD` line now also gives the CRC-32
  of the stream's compressed bytes in memory, whole and per 2 KB; our copy:
  `37af238d`, per 2 KB `efca69a3 a66fb47f 199242bd 58a91478 3432a2a6 28fcbdc4 b2f044c8
  b5577b16 933af614 16178e3f a43058bb 7eed4979 21246ce5 86b86629`, so the next log shows
  which part differs (a whole page or read chunk, or scattered bytes).

- 2026-10-16: **One press of Invite Friends pulled every player online into that party
  (#90)**, public server, v0.1.12: the party lobby's Invite Friends (`XamShowFriendsUI`)
  invited every player in the room (on our server the friends list is the room), and every
  game accepted every invite at once, so 13 players were pulled in. A player waiting in his
  own public lobby was taken out of it, and players in the opening Corrado drive had their
  game restarted (#81) into the party. Hotfix the same day: the public server runs
  `--no-invites`. Now (`src/kernel/live_invites.cpp`):
  - Sending: `XamShowFriendsUI`, `XamShowGameInviteUI` and `XInviteSend` open an invite
    picker in the text box over the game (`src/platform/hud_overlay.h`). The player types
    part of a gamertag to search the players online, moves with Up/Down (LB/RB jump to the
    next letter), picks up to 4 with A or Enter and sends with Start or "Send invites"; B or
    Escape cancels. Only the players picked get an invite. The picker lists sections, and a
    friends list (players you add, a separate issue) plugs in as the first one
    (`FriendSection`). `XFriendsCreateEnumerator` still lists the room, for the lobbies'
    presence and joinable games, but no invite goes to that list on its own. While the picker
    is open the game gets no input. With `mnk_mode` the keys also drive the pad, so a pad
    button counts only if no keyboard key went down between its press and release.
  - Receiving: "X invited you to their game": hold Back + A (keyboard Tab + Space, or Enter)
    to join, Back + B or Escape to ignore (that player's invites stay hidden for 2 minutes),
    no answer for 20 s ignores it. Only a join sends `XN_LIVE_INVITE_ACCEPTED`. While the
    prompt shows the game does not get Back or Start, nor A or B while Back is held. One
    prompt at a time. The opening drive can't be detected, but the game never joins (or
    restarts) without the player's yes. `live_accept_invites = false` ignores every invite.
  - Server: at most 4 invites per inviter in 30 s (the rest are dropped and logged). Invites
    go only between games that send the new HELLO features byte (`FEATURE_INVITE_PICK`,
    after the gamertag; old servers ignore it), so v0.1.12 games neither send nor get any,
    and the public server can deliver invites again once it runs this version.
    `server/tests/server/test_invites.py`.
  - Test (Linux, TU4, three games DriverA/B/C from copies of one Xenia save, local server
    on UDP 21470): A in the party lobby, Invite Friends: the picker listed DriverB and
    DriverC; B cancelled it with nothing sent and the lobby stayed. A picked B and sent: only
    B (in free roam) got the prompt, C got nothing. With no answer it was ignored after 20 s
    and B kept driving. A invited C (party lobby): C's Back + B ignored it, C's lobby did
    not get the B, and A's next invite to C was hidden. A invited B again: B held Back + A,
    the game restarted 0.4 s later (#81) and B was in A's party lobby 4 s after the invite
    (`screenshots/2026-10-16_invite_accepted_from_free_roam_A_left_B_right.jpg`). Not
    driven here: typing a search on a real keyboard (the test games run headless with
    scripted pad input), and the Y "INVITE Xbox LIVE Party" button, which calls none of
    these.
- 2026-10-16: **Friends (#92, step 1)**: everyone online counted as a friend. The server
  now keeps a friends list per player (`server/forza_live/social/friends.py`, `friends.json`), and the
  game shows only those friends (`src/kernel/live_friends.cpp`):
  - Protocol: HELLO features byte `0x02` (`FEATURE_FRIENDS`), then the friends key (u8
    length + 16 bytes; old servers ignore it). New UDP kind 22 `FRIEND` (op byte): LIST
    (with the version of the list the game has; the server answers SAME or the list in
    parts: XUID, relation, a friend's presence state and status, gamertag), SEARCH,
    REQUEST (by XUID or gamertag), ACCEPT, DECLINE (also takes back our request), REMOVE,
    BLOCK, UNBLOCK; the server sends NOTICE (a request from, accepted by, or a text). The
    game asks for the list every 10 s (3 s while the screen shows); the server sends it
    after every change and when a friend signs in or out. The roster (PEERS) still lists
    the whole room: matchmaking, QoS and stats need it, and old games keep counting it as
    their friends. A key that doesn't match the XUID's first one gets no list.
  - Game: `XFriendsCreateEnumerator` lists the friends (in our room with the roster's
    presence and joinable session, online elsewhere without one, offline last);
    `XamUserAreUsersFriends` answers from it. The invite picker's `FriendSection` is the
    friends online in the room, then "Everyone online" without friends and blocked players.
    The friends screen (F8 / hold LS; `live_osk.h` has the pad reader and the on-screen
    keyboard) manages requests; notices show when nothing else is on screen. Before a server
    answers (an older server) everything stays as it was.
  - Tests: `server/tests/social/test_friends.py` (16: request by gamertag, accept, request both
    ways, decline and take back, remove, block hides requests and drops invites, unknown
    players, rate limit, caps, 151 entries in parts and SAME, online first, sign-in push,
    key mismatch, old games, search, the file). Three games (Linux TU4, copies of one save,
    local server UDP 21590): A opened the screen with LS held, went to Add friend (DriverB,
    DriverC listed) and sent B a request; B got "DriverA sent you a friend request", held
    LS (the screen opened on Requests) and accepted; both games' friends lists changed to
    each other ("the game's friends list: 1 of 1: DriverB" / "DriverA"), C's stayed empty
    with A and B online. A's Invite Friends picker listed "Friends: DriverB" then "Everyone
    online: DriverC". C typed "d" with the on-screen keyboard (DriverA, DriverB found), then
    blocked A; A's invite to C was dropped by the server ("dropped (blocked)").
- 2026-10-16: **Text chat (#92, step 2)** (`server/forza_live/social/chat.py`, the friends screen's Chat
  tab in `src/kernel/live_friends.cpp`):
  - Protocol: HELLO features `0x04`; UDP kind 23 `CHAT` (op byte): SESSIONS (the ids of
    the sessions the game hosts or joined, `LiveSessionIds`; sent on change and every 10 s;
    count 0xFF asks for the mute list), SAY (channel 1 session, 2 Horizon World, 3 direct
    to a friend's XUID; text u16 length + UTF-8), MUTE (XUID, on); the server sends
    MESSAGE (channel, sender XUID and gamertag, text), NOTICE (text) and MUTED (XUIDs).
    Session reaches the players sharing a session id with the sender, Horizon World
    sessions excepted; World the players of the sender's world (`world_of_peer`).
  - Server: control characters stripped, one line, 200 characters; 5 messages per 10 s;
    blocks (either way) and the reader's mutes hide a message; admin mute and kick (ban)
    for N minutes at `/admin/mute`, `/admin/kick` (`moderation.json`), the last 200
    messages in memory at `/admin/chat`; nothing written about messages.
  - Tests: `server/tests/social/test_chat.py` (9: session, world, direct only to friends and
    online, cleaning and cap, rate limit, blocks and mutes, admin mute and kick with the
    file, the 200-message ring, old games). Three games (same setup as step 1; the friends
    from step 1 still there after restarting server and games): A sent B a direct message
    with the on-screen keyboard ("hi"; B: "[direct] DriverA: hi"); A invited B into its
    party (picker, Back + A); B's session message "yo" reached A, not C (in no session);
    C's session and world messages were refused ("You are not in a session", "... a
    Horizon World"); `/admin/mute?who=DriverA&minutes=5` refused A's next message ("muted
    you (5 min left)"), `minutes=0` let the next one through; `/admin/chat` listed the three
    delivered messages; `/admin/kick?who=DriverC&minutes=1` dropped C and refused its
    sign-in ("server error 4"); after B's "Mute in chat" on A, A's session message did not
    reach B. Not driven: the world channel in a real Horizon World (unit tests only), and
    typing on a real keyboard (headless games, pad input only).

- 2026-10-22: **Crash quitting to the title screen, write to guest 0x00000006 (#262)**, Linux
  Mint, Intel Iris Xe, v0.1.27, base game (no Title Update), offline: a new career, Pause >
  Quit right after the first autosave at the festival; the player's next quit worked. The
  render thread (`sub_825A6208`) runs the movie manager (`sub_823E5988` -> `sub_82E638F0`),
  which opens the main menu's background movie (`sub_82E6C108`: it pushes the movie allocator
  0x834B49F8, `sub_82E5E9F8`, as the thread's XMemAlloc heap, then calls XMediaFacade's export
  3 through `sub_82E5D730`). XMediaFacade (0x88000000 and up: `sub_88159030` -> `sub_8815E468`
  -> `sub_8815B9F8`, its malloc) asked for 0x2B2100 bytes (two 1280x720 4:2:0 frames:
  r20 = 45 macroblock rows, r21 = 80 columns), attributes 0x248C8001 (allocator 0x8C, XMV;
  read/write; not physical). XMemAlloc (`sub_82CC1B10`) finds the thread's heap
  (`sub_82CBC390`: thread id -> heap, table at 0x8349F978) and calls it: the movie allocator
  (`sub_82E5B7C8`) takes blocks of 64 KB and more from heap 0x20, "streamingGPU" (88,832 KB:
  `sub_82E5B0B0` -> `sub_82CBFDC8` -> `sub_82CBFBA8` -> the heap's alloc `sub_823FCCA8`, the
  crash's ctr), and when that gives nothing XMemAlloc writes the size to address 6 on purpose
  (`stw r27,6(r0)` at 0x82CC1D90; r27 was 0x2B2100). Title Update 4 has the same code
  (XMemAlloc `sub_82D83590`, the store at 0x82D83810, the allocator `sub_82F1D3F0`). The
  heaps: eight arenas at 0x832AAC24 (`sub_82CBBDA0`), layout in `src/platform/game_heap.h`.
  Measured (Linux, base, the test save's free roam, 9 quits, heap 0x20 sampled every 20 ms):
  the world holds 55 MB of the heap, the quit frees it from 30 ms before the quit's XAM
  message 0x2B004 to 150 ms after (56 -> 9 MB within 20 ms, the rest in steps), and the movie
  opens 240 ms after the message with 18.6 MB in use and a 70 MB free block. The menu counts
  on the world being gone first. In the player's log every frame before the quit took 200-500
  ms, and 0.42 s after the message the heap had no 2.7 MB in one piece: the world was not
  freed yet (or left the heap in pieces) when the movie opened. Not reproduced in 17 quits on
  the test PC (Ryzen 7 7800X3D, RX 9060 XT), 2 of them on 2 cores at render scale 3: the
  frees always came first.
  `src/hooks/movie_memory.cpp` now takes a movie block that heap 0x20 cannot give from
  physical memory (XPhysicalAllocEx `sub_82A6F2D8` with the protection and alignment the
  allocator gives its own physical blocks: physical like the heap's, so the GPU can read the
  movie's planes from them), and XMemFree (`sub_82CC1DA0`) and XMemSize (`sub_82CC1ED8`)
  answer for those blocks (the game would take them for its general heap's) and free them
  with XPhysicalFree (`sub_82CBB348`).
  Each logs a `FORZA: a movie needs N bytes ...` line with the heap's state, so a recurrence
  shows in the log. `FORZA_MOVIE_FALLBACK=always` takes every such block from physical memory:
  4 quits that way on the base game (209 blocks: the boot videos, title and menu movies) and
  2 on Title Update 4 (151) played right, every block of a finished movie went back, no
  fault. `=0` switches the hook off. Unit test: `forza-recomp/tests/game_heap_test.cpp`.

- 2026-10-22: **Typing a name with a controller (#294).** `XamShowKeyboardUI` (photo, tune
  and livery names) is answered by `src/kernel/xam_keyboard.cpp` instead of the SDK's text
  window, which needed a keyboard: an on-screen keyboard in the HUD overlay, 360 style
  (`live_osk.h`, layout `kPrompt`; the friends screen's `kFriends` layout is unchanged). It
  runs on the kernel's deferred-completion thread like the SDK's dialogs (XN_SYS_UI, and
  `XamIsUIActive` is overridden too); the input hook hands player 1's pad to it and the game
  reads a neutral pad. Names take letters, digits, space and ASCII punctuation a Windows folder
  name keeps. Every keystroke is logged (`Keyboard UI: ...`). Checked on Linux (TU4, the test
  save, scripted pad): photo mode, SAVE, the prompt "NAME SAVE FILE" (default `PHOTO1`, at
  most 15): deleted the default, typed `Max! 7` with the pad (case and symbol pages, LB/RB),
  Start: **SAVE SUCCESSFUL** and `photo/Max! 7/` in the save; a second prompt cancelled with B
  on the empty name returned to the slot picker. Unit test: `forza-recomp/tests/osk_test.cpp`.

- 2026-10-23: **Hitches after loads on Title Update 4: the car view query (#167).** In a player's
  log, 14 of 17 long frames (0.27-0.32 s, two or three after every load) had the main loop in the
  game's SQLite 3.6.19 by their `FORZA_STALL` chains (TU4 `sqlite3_step` `sub_83146F20`), running
  `SELECT * FROM Restrictions_Data_Car WHERE IsCarInstalled(ModelId) ORDER BY ModelId` through the
  database's query method (`sub_82CD8FD8`, base `sub_82C19130`). That view is TU4's (the Rally
  Expansion): LEFT JOINs to three derived tables, which 3.6.19 materializes and scans once per
  car (no automatic indexes). 131 ms per query here with all DLC (251 cars), twice after every
  load into free roam; an event's sign-up runs three more (103-105 ms) on another thread while
  the main loop waits for the lock. `src/hooks/database_query.cpp` creates the view with the same
  columns and rows from joins on the tables plus four indexes: 16-18 ms and 6-7 ms; the frames
  that held the query went from 155-193 to 32-74 ms (2 runs each, docs/perf.md).
  `tools/check_car_view.py` compares both views on the local game files (PASS with the 17 DLC
  database patches and edge cases); in the game every value read back was the same.
  `FORZA_GAME_CAR_VIEW=1` keeps the game's view. Every query of 50 ms or more now logs a
  `FORZA_SQL` line (`FORZA_SQL_LOG=1`: all of them).

## Debugging recipes

- Find a codegen hint's target: `tools/ppcdis.sh 0x82F76188 +0x80`.
- Decompile: `tools/ghidra_decomp.sh start`, then `tools/ghidra_decomp.sh 8287BBE0 ...`
  (C files under `/tmp/forza-decomp-$USER/out`; `stop` when done).
- Runs: `tools/run_forza.sh SECS [flags]` (logs in `$FORZA_RUN_DIR`), or
  `tools/live_session.sh DIR` + `tools/live_step.sh DIR WAIT INPUT...` (input FIFO,
  screenshot `DIR/shot.png` through `FORZA_SCREENSHOT_TRIGGER`). Headless frame dumps come
  out all zero (seen in Reach); check rendering with screenshots from a windowed run.
- Keyboard and mouse: `python3 tools/uinput_keys.py FIFO &` makes a virtual keyboard and
  mouse (uinput); `echo "tap SPACE 0.1" > FIFO` presses keys, `move 1500 0 1000` moves the
  mouse 1500 px in 1 s. Start the game with `--mnk_mode=true --mnk_mouse=true` through
  `tools/live_session.sh`, and keep its window focused: another game window opening takes
  the keys away (a game that ignores the keys is usually that). Screenshots as above.
- `FORZA_FPSLOG=1` logs the frame rate every 5 s; `FORZA_AUDIOLOG=1` the peak/RMS of the
  audio the game submits (non-zero means it makes sound), and the RMS of each of its 6
  channels (front left, front right, center, LFE, back left, back right);
  `FORZA_AUDIOLOG_MS` sets the window (default 5000).
- A game that hangs: close its window; the log ends with `FORZA: guest threads at close`, one
  line per guest thread (handle, `[tN]` on Windows, start address, the return address of its
  last call and its call chain). A thread waiting in the kernel shows the call site of the wait.
- A fault no handler takes aborts with a host stack in the log (`src/debug/fault_guard.cpp`):
  `addr2line -f -e forza-recomp/out/build/linux/forza 0xOFFSET` names the `sub_XXXXXXXX`
  frames. `FORZA_FAULT=wait` parks the thread instead, for `gdb -p`. On Windows the
  frames carry the guest function (`sub_XXXXXXXX+0xN`) since #83; an older log's
  `forza.exe+0xOFFSET` only maps onto an executable built from the player's codegen history
  (#83 entry: replay the codegens of the versions they built, cross build, link with `/MAP`;
  `src/debug` frames at the right offsets confirm the layout).
- Never pass two files to `xxd` (`xxd a b` writes the dump into `b`): that clobbered a
  save header once.
- Testing with the user's Xenia save without touching it: copy
  `~/xenia_canary_windows/content/E030000004B490FA/4D5309C9` into a scratch
  `<dir>/B13EBABEBABEBABE/`, symlink `<dir>/0000000000000000` to the installed DLC, run with
  `--user_data_root=<dir>` and `FORZA_XUID=E030000004B490FA`.
- `tools/repro_freeroam.sh WORKDIR N` loads free roam from a fresh copy of the Xenia save
  N times and reports faults (one game at a time). `FORZA_PHYS_SEED=1` fills never-written
  GJK/EPA vertex slots with sentinels, so reading one faults every time instead of
  sometimes.
- `tools/xrefs.py ADDR` lists branches, lis/addi pairs and data words pointing at an
  address (a quick stand-in for Ghidra's xrefs).
- Kernel imports can be overridden from `src/` by defining `extern "C" REX_FUNC(__imp__Name)`;
  `forza::SdkImport("__imp__Name")` finds the SDK's original. Guest pointers in hand-written
  code: always `forza::GuestPtr(base, addr)` (Windows maps 0xE0000000+ 4 KB further on).
- Guest address X is host `0x100000000 + X` on Linux: `gdb -p PID`, `rwatch *(int*)0x1XXXXXXXX`,
  `bt` shows the `sub_XXXXXXXX` frames.
