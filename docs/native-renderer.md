# Native renderer: plan, tools and baselines

Today the game's Xbox Direct3D library writes Xbox GPU commands (PM4) and the SDK
([ReIGlue](https://github.com/FH1-ReImagined/ReIGlue)) emulates the Xbox GPU: it decodes those
commands, emulates the GPU's 10 MB of on-chip memory (EDRAM) with copies, and translates the
shaders while the game runs. The native renderer catches the game at its Direct3D calls instead
and draws with Vulkan directly:

- render targets are real GPU images, at any size, with no EDRAM tiles;
- copies happen only where the game really reads a result;
- shaders are translated when the game is built, not while it runs.

The emulated path stays in the build as a switch for every group of passes, so each step can be
compared against it and turned off if it breaks. Later, the same layer can drive Direct3D 12
through ReGPU.

Decisions so far (2026-11-12):

- Work starts with milestones M0 (tools, baselines) and M1 (build-time shaders).
- The native renderer covers the Title Update 4 build only; the disc build stays emulated.
- Native rendering stays opt-in until every feature of M6 works on it.

Measured numbers below are marked **[M]** with their source; **[I]** marks what is inferred from
code, from other numbers or from judgment. Shares of "PM4" count what the command processor read
(packets predicated off, padding and tile replays included), not CPU or GPU time.

## Why

Most of what the emulator costs is overhead, not the game's own drawing:

- **GPU, GTX 1650 Ti laptop** (the launcher's preset, benchmark; [perf.md](perf.md), "The GTX
  1650 Ti's GPU frame after #770"): the game's draws are 7.9-8.1 of 15.5-15.7 ms a frame.
  Render target transfers (3.2 ms), loads of resolved textures (1.9 ms) and resolves (1.7 ms)
  are most of the rest **[M]**. A renderer without EDRAM needs none of the transfers and few of
  the copies: about 5-6 ms of the 15.5 ms, an upper bound to be checked at M4 **[I]**.
- **CPU:** the command processor is the busiest game thread, 4.9-6.3 ms a frame at 1.2-1.6 us a
  draw ([perf.md](perf.md), "Where a frame's CPU time goes", #607) **[M]**. About half of what it
  reads is shader constants, and it reads the 4x MSAA scene about twice because of EDRAM tiling
  **[M]**. Natively there is no command decoding, no tiling and no register emulation **[I]**.
- **Simpler features:** some exist only to work around EDRAM: DLSS's forced integer render
  scale, NVIDIA's stencil transfer cost, the Big Shot readback history.

## What the D3D layer looks like (TU4)

From the study's map of the game's D3D library (two scenes: the benchmark race and a stationary
free roam; 207 D3D functions wrapped and counted; the tools are under [Tools](#tools)):

| What the command processor read, race | Dwords | Packets |
| --- | --- | --- |
| Written by the D3D library | 51.3% | 27.9% |
| ... RunCommandBuffer `sub_824469B0`'s own packets (the state flush before each list, the IBs) | 31.5% | 11.5% |
| ... DrawVertices `sub_82445F18` / DrawIndexedVertices `sub_82446330` | 9.0% / 6.2% | 7.4% / 5.1% |
| ... everything else (Resolve, clears, UP draws, predication, viewport, swap, fences) | 4.6% | 3.9% |
| Command lists D3D did not write ("foreign"), run by RunCommandBuffer | **48.7%** | **72.1%** (64% of the draws) |
| ... with SET_CONSTANT, an opcode this D3D never writes: lists recorded offline into the model data | 23.4% | 40.5% |
| ... without SET_CONSTANT (origin still open) | 25.3% | 31.6% |

- **Everything reaches the GPU through the D3D library or those lists [M].** No raw PM4 from other
  game code was found at run time, and a static scan found no PM4 headers built outside the D3D
  library's two address ranges (0x82410000-0x824AFFFF, 0x82AA0000-0x82ABFFFF).
- **The setters write no PM4 [M].** SetTexture (2,363 calls a frame), SetStreamSource (1,801),
  the shader, declaration, index and render-state setters only fill the device's shadow state;
  the draws and RunCommandBuffer flush it. So the native layer takes over about 15 flush points
  (the draws, RunCommandBuffer, Resolve, the clear, predication, swap, fences and waits, the
  segment functions) and keeps the setters as guest code. The device's shadow state stays what
  later game code reads.
- **The model lists are loaded once and patched [M].** Two of them were caught being written at
  race load by the resource stream reader (`sub_82D378E8`, memcpy `sub_82B3A7E0`); the game
  patches texture fetch constants into them (`sub_82D19210` -> `sub_824AAA28`/`sub_824AAAC0`, 52
  a frame in the race). They become native draw records once, by a converter, not per frame.
- **Constants dominate:** the ALU constant flush `sub_82446860` alone is 43% of every dword read
  **[M]**; the D3D-written segments run about 2.0 times a frame (predicated tiling) **[M]**.
- **No shader uses memexport [M]** (0 of the game's 6,359 shader blobs).
- **GPU results read on the CPU:** Big Shot, thumbnails and garage tiles, the livery bake, the
  online photo, and about one occlusion query a frame in free roam **[M]**.

## Architecture

```
 game code (unchanged)
   |  D3D setters (guest, unchanged) --> guest device shadow state
   |  D3D flush points (hooked): Draw*, RunCommandBuffer, Resolve, Clear, Swap, fences
   v
 host device (new)                                    +-- emulated path (today's SDK),
   state reader: device shadow -> draw record         |   per group of passes, for A/B
   list converter: model PM4 lists -> draw records    |   and as the fallback
   surface manager: render target images, resolves   |
   sync: fences, waits, the read pointer, on the guest's own words
   v
 backend interface (images, buffers, pipelines, draws, copies, queries, present)
   +-- Vulkan (ReIGlue: device, pipeline cache with the build-time shader pack, texture cache,
   |           presenter, DLSS / FSR 3 / HDR / VR)
   +-- Direct3D 12 (ReGPU's core) -- M7
```

- **Draw records** are built on the game's render thread from the device's shadow state (ALU
  constants at device +1920 / +6016 with their dirty masks at +0 / +8, the state dirty masks at
  +16..+40, the register shadows at +10112..+10832, fetch constants at device + (48 + n) x 24,
  shaders at +13068 / +13072, the vertex declaration at +12240, the indices at +12812, the render
  targets and viewport at +12816..+13116). Pipeline lookups, descriptors and Vulkan commands run
  later, on the GPU thread, as the split command processor does now.
- **In the hybrid milestones** the host device writes one `FH1N` NOP packet ("run host list k")
  into the guest command buffer, which keeps native and emulated work in order on both command
  processor threads without locks (proven by the prototype below).
- **Render targets** are host images per surface, format and sample count; where the game relies
  on EDRAM aliasing (shadow maps drawn as 4x MSAA depth and read as 1x at twice the width) an
  explicit copy does it. **Resolves** bind the host image as the texture the game samples where
  the formats allow; CPU readers get a guest memory write-back from an explicit list.
- **Shaders** come from a build-time pack (M1); in the host device a shader is identified by the
  guest shader object, with no IM_LOAD and no per-draw hash. Settings that are baked into the
  SPIR-V today (resolution scale, raster scale, the native viewport, NVIDIA's cube LOD, 2x MSAA)
  become specialization constants.
- **Switches:** `--native_render=off|hybrid|full` and one switch per group of passes, each with a
  compare mode that draws both paths and compares them on the GPU every frame. Anything the host
  device cannot handle falls back to the guest D3D call for that draw, decided before any guest
  work is skipped.

A prototype moved one pass (the shadow mask's "softer" blur) to two compute dispatches behind a
host packet: 98.7-98.9% of the mask's pixels bit-identical in an in-frame compare and the rest
within 1/255, all six render test scenes passing, and no speed-up measurable on its own (166.1
vs 167.0 fps, inside the noise) **[M]**. Its dawn failure, a pass that relied on a clear and a
resolve outside it, is why groups of passes go native together, chosen from a measured pass
map.

## Risks

| Risk | Mitigation |
| --- | --- |
| Hidden dependencies between passes (the prototype's dawn bug) | groups of passes from the M0 pass map; a fallback for any shape M0 did not see; compare mode per group; more render test scenes |
| Game code reading GPU results on the CPU | an explicit readback list; a debug mode that protects resolve destinations to catch unknown readers |
| Predicated tiling inside the model lists (19K bin masks a frame) | decode the bin mask meaning in M0; keep a packet any tile shows |
| EDRAM aliasing the game uses on purpose (25-41 transfers a frame on the 1650 Ti) | list every transfer whose data is read; explicit copy passes |
| Command lists of unknown origin (the foreign class without SET_CONSTANT) | parent tags per foreign buffer (the PM4 trace), watchpoints, a review of the remaining PM4-like constants |
| Features built on the emulator: VR true stereo, DLSS, FSR 3, HDR, texture packs, world AO, ultrawide, the split HUD raster | ported in M6; each stays emulated until ported, and native refuses to start with an unported feature on |
| Two paths to maintain | one group at a time; render test and compare mode gate every change |
| Record building moves work onto the render thread (3.3 ms today) | keep records light; measure the render thread at every milestone |
| Memory on 8 GB PCs | the M5 exit: RAM and VRAM not above emulated |

## Milestones

Common measurements at each exit: the render test in all scenes (baselines recorded on the same
build with native off); compare mode per native group over at least 10,000 frames (8-bit
targets: at least 98% of pixels identical and at most 1/255 on 99.99%; depth exact or within one
unit of 24 bits; every pixel off by more than 2/255 listed and explained); the built-in
benchmark, uncapped, four alternating runs per mode on RADV and on the GTX 1650 Ti laptop (fps,
1% low, p99, per-thread CPU, `fh1_gpu_profile` zones in at least two profile pairs); free roam at
a fixed 30 fps for CPU time a frame; a one-hour soak. A native group may not cost GPU time or
more than 0.1 ms of render thread time; speed targets apply from M4.

| Stage | What | Exit | Ships |
| --- | --- | --- | --- |
| M0 | Tools, baselines, inventory: the pass map and the groups of passes, the transfer list, the origin of every class of foreign command list, the bin mask meaning, the CPU readers | the pass map and groups exist for all scenes; every class of foreign list has a known writer; every transfer says whether its data is read; baselines recorded | no |
| M1 | Build-time shaders (below; ReIGlue 0590, 0600) | pack SPIR-V byte-identical to a fresh translation; a stale pack ignored; warm start at most 0.1 s plus the pack read; render test passes, benchmark unchanged | **yes**, on its own: faster starts |
| M2 | The host device in shadow mode: draw records built from the shadow state and the converted lists, compared field by field with the command processor's own state at each draw | 100% of draws match over a race, a free roam and the M0 scenes; record building at most 0.3 ms a frame on the render thread | no |
| M3 | The first native group end to end: the shadow mask (clear, 11 cascade passes, blurs, the world pass's sampling), render scale above 1 and world AO included | compare mode in budget at dawn and in photo mode; render test passes; GPU zones not worse in two pairs on both machines | behind a switch |
| M4 | Shadow maps, the depth pre-pass and the 3D scene (one 4x MSAA image, no tiles, model lists converted), the reflection cube, post-processing, the HUD and menus | on the 1650 Ti: transfers from 3.2 to at most 0.5 ms, loads and resolves from 3.6 to at most 1.5 ms; fps up beyond the noise; command processor CPU down at least 30% at 30 fps | opt-in |
| M5 | The whole frame native: no per-frame PM4, no EDRAM buffer, no render target cache | soak; frame pacing not worse; RAM/VRAM not above emulated on 8 GB; decoder and recorder CPU at most half of today's | opt-in, experimental |
| M6 | VR true stereo, DLSS (exact input sizes), FSR 3, HDR, SMAA, ultrawide, the split HUD raster, texture packs, world AO, FM4 tracks, Big Shot and photo upload; Windows and NVIDIA | every feature passes its existing test; the 1650 Ti on Windows passes the common set | default, emulated one setting away |
| M7 | A Direct3D 12 backend through ReGPU, with its author | the common set on Windows (D3D12) | with ReGPU |

### M1: shaders translated at build time

FH1 has 6,359 unique shader blobs (3,117 vertex, 3,242 pixel) in the base game, Title Update 4,
the tracks, the executable and the DLC; the shipped prebuild list ([shader_prebuild.md](shader_prebuild.md))
needs 2,170 (shader, modification) translations for 2,831 pipelines **[M]**. The SDK's translator
handles all of them offline: the list in 0.78 s and every blob in 1.9 s on one thread, no
failure, every module valid, and byte-identical when translated twice **[M]**.

Translation was 87-97% of every warm start's shader warm-up (0.22-0.86 s in players' and test
logs, AMD and NVIDIA, Linux and Windows) because the warm-up translated serially before it
created the pipelines, which is a driver cache lookup by then **[M]**. M1 (ReIGlue 0590 and 0600,
in this build) changes two things:

1. **The warm-up translates on several threads** (0590, every player, nothing to set up). The
   analysis and the translation objects stay on one thread (the shader's map, its memo and the
   disassembly buffer are not thread-safe); the translations run on 3/4 of the logical
   processors (`vulkan_shader_translation_threads`), each thread with its own translator; the
   texture binding layouts are filled afterwards on one thread, in the same order as before.
2. **The shader pack** (0600). The launcher's *Prebuild shaders* run (Setup, the game started
   with `--shader_prebuild_only`) translates every pair of the stored and listed pipelines and
   writes their SPIR-V and bindings to `<cache root>/shaders/translated/<title>.<key>.vk.xspv`
   (with split rendering, one in each of the two caches). Every later start takes them from it
   and translates only what it lacks; so does play, for a pair the warm-up did not need.
   - **Identity:** the SDK build (its version and the size and checksum of the GPU plugin's own
     file, which the translator is compiled into) and the translator configuration the game logs
     at start (device features, render target path, MSAA choices, draw resolution and raster
     scales, the cvars the translator reads). A pack is used only when both are exactly the
     game's: another plugin binary (an update, a local rebuild), GPU, driver feature set or
     graphics setting looks for another file. Four of the warm-up's pairs, chosen at random, are
     translated again at each start and compared byte for byte (about 0.5 ms each), against a
     translator that is not deterministic: a pack that differs is not used.
   - **Damage:** the header, the index and each module carry checksums; a damaged pack is not
     used, a damaged module is translated instead.
   - **Size:** each distinct module once, its 32-bit words as LEB128: 22 MB for the 1,496 pairs
     of the main cache with the list (64 MB of SPIR-V, 1,036 distinct modules). Writing a pack removes
     the title's packs of other builds; this build's packs for other settings stay, the newest
     three.
   - **Off:** Advanced > *Keep translated shaders* (`vulkan_shader_pack=false`): nothing read or
     written, the warm-up translates at every start (still on several threads).
   - **Launcher:** the Prebuild shaders card is offered again when the pack it wrote is gone,
     or when the setting was off then and is on now, besides the existing reasons (another
     plugin, driver or setting).
   - The pack is made on the player's PC from the player's own game files and is never shipped
     or uploaded.
3. No `spirv-opt -O`: it raises register use and makes one family of pixel shaders 36-53% slower
   on RDNA4 **[M]**; a light SSA cleanup is neutral on the GPU and only saves pack size, so the
   pack holds the translator's own SPIR-V.

The SDK tool `rexglue-shaders` was planned to write the pack; the game writes it instead, in the
Prebuild shaders run, because only the running game knows its exact configuration (the device's
features come from Vulkan) and the launcher already runs that step again when the plugin, the
driver or a setting changes. The tool reads and checks packs (`check-pack`, below), and can write
one for tests.

Measured on the test PC (M1 measurements in [perf.md](perf.md#native-renderer-m1-shaders-translated-at-build-time-2026-11-12)):

| Warm start, the prebuild list (1,496 translations, 2,254 pipelines) | Translation | Shader warm-up |
| --- | --- | --- |
| main (one thread) | about 600 ms | 659-699 ms |
| M1, pack off (6 threads) | 109-111 ms | 169-177 ms |
| M1, with the pack | 48-55 ms (all from the pack) | 109-121 ms |

In the benchmark race after a prebuild, play translated nothing with or without M1 (the list
covers the race) and created the same 12 pipelines; the frame rate was the same within the noise
(181.8 fps for main and 187.4 for M1, three runs each, spread 167-198) **[M]**.

Listed shaders were already translated in the warm-up before M1, so play translates only pairs
outside it, as before: the gain is at start. GPU time is unchanged (the SPIR-V is the same bytes).

## Tools

The M0 tools are build options: off, the game and the SDK are the same as without them.

### D3D trace (game) and PM4 trace (SDK)

What the command processor executes, counted by the D3D function that wrote it:

- **Game:** `-DFORZA_D3D_TRACE=ON` (TU4, Linux) adds `src/debug/d3d_trace.cpp`: a counting
  wrapper around every function in `src/debug/d3d_trace_hooks.inc` (207 D3D functions, with their
  roles). Each call is counted, and the command-buffer dwords it appends are tagged with its id
  in shared memory (`src/debug/d3d_trace_shm.h`): the innermost wrapped call and the outermost
  one, the entry point the game called. Functions the game already hooks (`kChained` in the list)
  open `FORZA_D3D_TRACE_SCOPE` in that hook instead. It costs every D3D call: never for players.
- **SDK:** ReIGlue built with `-DREXGLUE_FH1_PM4_TRACE=ON` (patch 0570). With
  `FH1_PM4_STATS=<file>` its command processor counts every packet it reads, by opcode, against
  the tag of its first dword, classifies every indirect buffer (written by D3D, mixed, foreign,
  foreign with SET_CONSTANT) and counts each foreign buffer against its **parent**, the writer of
  the INDIRECT_BUFFER packet that ran it. Every `FH1_PM4_STATS_EVERY` swaps (300) it appends a
  block with the game's call counters to the file.
- **Report:** `tools/d3d_trace_report.py <file> [--blocks A-B] [--opcodes] [--json OUT]`: shares
  by class, the entry point and writer tables (calls a frame, shares of dwords, packets and
  draws), the indirect buffers a frame, foreign buffers by parent, and packets by opcode.

Both sides must be the trace builds for tags; with only the SDK side every packet counts as
foreign or primary ring. Run it as any test game (a hidden display, a scratch user data folder,
`--audio_mute`), at a fixed frame rate: the counts are per frame, not timed.

### Offline shader translation (SDK)

`rexglue-shaders` (ReIGlue `-DREXGLUE_BUILD_SHADER_TOOLS=ON`, patch 0580) compiles the GPU
plugin's own shader analysis and SPIR-V translator sources and translates exactly as the Vulkan
pipeline cache's warm-up does:

```sh
rexglue-shaders translate --store 4D5309C9.xsh --pairs pairs.txt --out spv/ --check > translate.csv
rexglue-shaders translate --store 4D5309C9.xsh --pipelines 4D5309C9.fbo.vk.xpso
rexglue-shaders translate --store all.xsh --defaults            # every stored shader once
```

- Inputs: a shader store (`.xsh`, the game's or the launcher's prebuild), and the pairs as text
  (`<hash> <modification>`, hexadecimal), from a pipeline store (`.xpso`), or every stored shader
  with the default modification.
- `--config "..."`: the translator configuration. The game logs it once at start
  (`SPIR-V translator configuration: spirv_version=0x10500 max_storage_buffer_range=...`): device
  features, MSAA and interlock choices, resolution and raster scales, the dirt seam fix. Pasted
  into `--config`, the tool translates as that player's game did; `--set key=value` changes one
  word. The default is a Vulkan 1.2 desktop GPU at render scale 1.
- `--check` translates each pair twice from scratch and compares the bytes; `--repeat N` keeps
  the fastest of N for timing. The CSV has the times, the SPIR-V size and its XXH3.
- `--dump_shaders=<dir>` now also works on Vulkan: the game writes every translation's SPIR-V
  (`shader_<hash>_<modification>.vk.bin.<vert|frag>`) before the module is made, to compare
  with the tool's output.

The shader pack (patch 0600, M1):

```sh
# every pair of a pack the game wrote, translated again and compared, bindings included
rexglue-shaders check-pack cache/shaders/translated/4D5309C9.<key>.vk.xspv \
    --store cache/shaders/prebuild/4D5309C9.xsh --store cache/shaders/shareable/4D5309C9.xsh \
    --config "<the log's SPIR-V translator configuration>"
# a pack from the tool, for tests (--pack-config: a pack that claims another configuration)
rexglue-shaders translate --store 4D5309C9.xsh --pairs pairs.txt --pack test.vk.xspv
```

- The game writes a pack with `--shader_prebuild_only=true` (the launcher's Prebuild shaders), or
  at any start with `--vulkan_shader_pack_write=true`; `--vulkan_shader_pack=false` turns it off.
- The log says what happened: `Vulkan shader pack: wrote ...`, `Vulkan shader warm-up: N
  translations in X ms, M from the shader pack, K translated on T threads`, or why a pack was
  not used (none for this configuration, another build or configuration, damaged, differs from
  this build's translation).
- `check-pack` takes a pack whose identity has the configuration given; with `--plugin <the
  game's librexgpu-xenos.so>` the identity must be that plugin's too, as the game requires;
  `--any-identity` checks the bytes of any pack. `translate --pack` writes a pack the game takes
  only with `--plugin` (its plugin file) and the game's configuration.

## Baselines (2026-11-12)

On the test PC (RX 9060 XT, RADV), main with every M0 option off; the tables are in
[perf.md](perf.md#native-renderer-baselines-m0-2026-11-12):

- **Benchmark:** 174.6 fps (167.8-182.0 over four runs), 1% low 113, p99 7.7 ms; command processor
  2.65, GPU recorder 4.56, Vulkan submission 1.80 ms of CPU a frame. GPU busy 4.8 ms a frame:
  2.6 ms the game's draws, 0.8 ms render target transfers, 0.65 ms loads of resolved textures,
  0.64 ms resolves.
- **Free roam at 30 fps:** command processor 1.9-2.0, recorder 3.5-3.6 ms a frame; 3,300-3,400
  draws.
- **What the command processor reads:** 1.31 M dwords and 4,889 draw packets a frame in the race
  (0.86 M and 3,514 in free roam), 52% (47%) of the dwords written by D3D; RunCommandBuffer
  launches all foreign lists but the 15 small game-recorded buffers a frame of `sub_8249F310`.
- **Shaders:** the game's SPIR-V is byte-identical to the offline tool's with the logged
  configuration (501 translations).
- **Render test:** baselines of all six scenes recorded on this build (three sessions) and kept
  on the test PC, outside the repository; a check against them passes 6/6. Two of the three
  recorded sessions are identical in every free roam scene, the third shows another moment of the
  scene (its noise is over four scenes' tolerances): the check takes the closest frames, and a
  milestone's check against these baselines is read with that in mind.
