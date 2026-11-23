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
| M2 | The host device in shadow mode: draw records built from the shadow state and the converted lists, compared field by field with the command processor's own state at each draw (first group, the shadow mask: below; ReIGlue 0610, 0620) | 100% of draws match over a race, a free roam and the M0 scenes; record building at most 0.3 ms a frame on the render thread | no |
| M3 | The first native group end to end: the shadow mask (clear, 11 cascade passes, blurs, the world pass's sampling), render scale above 1 and world AO included (in progress: the blurs and their resolves, below) | compare mode in budget at dawn and in photo mode; render test passes; GPU zones not worse in two pairs on both machines | behind a switch |
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
     scales, the cvars the translator reads, and the FM4 shadow test variables when they are
     set). A pack is used only when both are exactly the
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

### M2: shadow mode, the first group of passes (the shadow mask)

The host device layer exists, behind a switch, and records what it would draw without drawing
it: `--native_render=shadow` (testing; `off` is the default). It covers one group of passes
first, the sun's shadow mask, the one the prototype started on. Nothing drawn changes: the guest's
D3D calls all run as before, and the GPU plugin only compares.

- **The flush points (game, `src/hooks/native_device.cpp`).** 16 TU4 functions are hooked:
  DrawVertices, DrawIndexedVertices, BeginVertices, DrawVerticesUP, DrawIndexedVerticesUP, Resolve,
  the clear, predication, tiling, the segment kick, the fence, the three waits and the swap, and
  RunCommandBuffer through the hook `cockpit_gauges.cpp` already had. Off, each is one load and
  one branch before the original call.
- **The group.** From the shadow mask function (`sub_824829D8`) to the world pass's resolve of
  the mask (`sub_824933E0` returning to `0x8242A538`), every flush call makes a draw record. A
  frame's group is 19 calls: 2 clears, the 11 cascade passes, 3 UP draws (a quad and the
  horizontal and vertical blurs, with the softer filter), and 3 resolves.
- **A record** (`src/platform/native_shadow_record.h`) holds what a native renderer would draw
  from, read from the D3D device's shadow state when the call returns:
  - the 161 registers the device shadows (8 blocks, device +10112) and the 32 fetch constants;
  - the call's own arguments (primitive, count, the index address, an UP draw's first vertices)
    and the bound shader objects;
  - the ALU constants as a native renderer would upload them: all 512 in the group's first
    record (the seed), then only the chunks of 4 constants the device had marked dirty when the
    call started (device +0 and +8, bit 63 the first chunk);
  - the shader literal constants the draw loads from the shader object (D3D's
    LOAD_ALU_CONSTANT, `sub_82441B80`, inside the draw's flush), which the device shadow does not
    hold (below).
- **How it travels.** A record and its constants go into a 2 MB ring in guest memory; one 6-word
  host packet (ReIGlue 0610: a type-3 NOP whose first word is "FH1N") behind the call's own words
  in the device's command buffer points at them. D3D guarantees only 40 words of room past its
  soft limit, and making space there does not always start a new segment, so the 400-2,600-word
  records cannot go into the command buffer itself: the first build lost 1-3% of them that way.
- **What the plugin checks** (ReIGlue 0610, `fh1_native_shadow.cpp`), in stream order, so with the
  register file exactly as the guest's packets before the record left it:
  - registers, fetch constants and ALU constants, those the bound shaders read (the shader
    analysis: texture and vertex bindings, the float constant map; render targets 1-3 and their
    blending only when the pixel shader writes them; the alpha reference only with the alpha test
    on) apart from the others;
  - one draw executed since the previous record for a draw call (a copy for a resolve), with the
    record's primitive type, count and index address;
  - a guest shader object is always the same microcode;
  - per group: the draws and copies executed against the game's calls.
  A resolve is compared on its source surface and copy registers, and a clear not at all (both
  draw with their own state, below).
- **Contents, at a low rate** (ReIGlue 0620): every `native_shadow_compare_every` groups (60, about
  one a second at 60 fps), the plugin also computes the softer blur from the records alone (the
  mask's fetch constant and the quad from the horizontal blur's record, the depth from the cascade
  records) with the prototype's compute shaders into a private buffer, and after the world pass's
  resolve compares it on the GPU with the mask the emulated passes produced. The counts are read
  back from that submission.

Off, a player's game is the same as without M2 apart from the hooks' one branch. The log in shadow
mode: `native_render shadow:` lines from the game every 600 frames (records, their cost on the
render thread, the flush points a frame), `FH1 native shadow` from the plugin every 300 groups
(agreement, what differed and the first values, the check's cost) and `FH1 native shadow compare`
every 50 compares.

**What the records found** **[M]** (benchmark race and free roam):

1. **Shader literal constants are not in the device's shadow.** The first runs agreed on 31.6% of
   the records: the pixel constants c252-c255 (c508-c511) that the cascade and blur shaders read
   (values such as -0.5, 2 pi, 1 and 0.22) are written by D3D at the draw from the shader object
   (LOAD_ALU_CONSTANT), not through the device's constant shadow. With the literal loads in the
   record, every record agrees. A native renderer must take a shader's literals from its object.
2. **The cascade passes draw through DrawIndexedVerticesUP** (`sub_8242A040`, indices and vertices
   copied into the command buffer), which the M0 map listed only as a nested writer
   (`sub_82438B20`): with five draw functions hooked, the group's counts were 5 draw calls against 16 draws executed. With it,
   the counts agree in every group.
3. **A clear and a resolve draw with their own state.** The mask's clear sets its own surface
   (RB_SURFACE_INFO 0A020280: pitch 640 with 4x MSAA over the mask's 1280x720 tiles, against the
   shadow's 14000500), its own shaders and constants; a resolve likewise, except for its copy
   registers and source surface, which match the shadow. Natively, both are built from the call's
   arguments, not from the shadow.
4. **The shadow holds values the GPU never sees, which nothing in the group reads:**
   RB_COLOR1_INFO and RB_COLOR2_INFO (render targets not bound), RB_ALPHA_REF (alpha test off),
   RB_HIZ_CLEAR, the fetch constants of samplers 3, 6 and 7, and ALU constants the shaders do not
   read. So "agrees on everything" is 26.3% (the clears and resolves), "agrees on what the shaders
   read" 100%: a native renderer has to read exactly what the bound shaders use.
5. **The ALU constants a native renderer would upload are enough:** the seed, then the dirty
   chunks and the literals, give every constant the shaders read, in every record. Only the shadow
   mask group is shown so far; model lists (RunCommandBuffer) write constants D3D does not track.
6. **UP draws read their vertices from the command buffer** through fetch constant 31, which
   BeginVertices writes past the shadow (it clears the constant's dirty bit): a native UP draw takes
   the vertices from the call.
7. **Shader identity:** 12 shader objects in the group, each always the same microcode.

**Measured** **[M]** (RX 9060 XT, RADV, 8 cores; 1280x720, render scale 1, softer shadow filter,
world AO off; the benchmark race uncapped, its window 60-130 s; free roam standing at 30 fps,
window 90-150 s; each run alone with the benchmark lock, hidden display, no `forza.toml`). Eight
alternating rounds, each build once a round in a rotated order; CPU a frame is the game process's
CPU time over the window (`/proc`) divided by the frames in it; the M2 builds with the M2 plugin:

| The race, 8 rounds | Runs | fps | Window fps | 1% low | p99 ms | CPU ms a frame |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| M1 (game and plugin) | 8 | 171.4 (167.9-179.1) | 185.4 | 119.1 | 7.58 | 16.82 |
| **M2, `native_render=off`** | 8 | **171.8** (165.3-177.9) | 186.3 | 120.6 | 7.52 | **16.85** |
| Control: M2 linked, no hooks | 8 | 174.7 (162.1-185.7) | 186.6 | 122.1 | 7.42 | 16.89 |
| M2 off, the off path as tail calls (not kept) | 8 | 177.2 (165.5-191.1) | 190.2 | 122.3 | 7.39 | 16.59 |
| M2 `shadow` (the tail-call build) | 4 | 176.0 (172.0-183.3) | 188.9 | 121.4 | 7.41 | 16.66 |

| Free roam, 30 fps, 4 rounds | Runs | CPU ms a frame | Against M1 |
| --- | ---: | ---: | ---: |
| M1 (game and plugin) | 4 | 21.07 (20.80-21.24) | |
| **M2, `native_render=off`** | 4 | **21.17** (20.45-21.59) | +0.5% (t = 0.4) |
| Control: M2 linked, no hooks | 4 | 22.66 (21.76-23.14) | +7.5% (t = 4.8) |
| M2 off, tail calls (not kept) | 4 | 21.65 (21.25-22.07) | +2.8% (t = 2.9) |

**Off costs nothing measurable.** Against M1, M2 off is +0.2% fps in the race (t = 0.15; the
round-paired difference +0.4 fps, standard error 2.3), +0.2% CPU a frame (t = 0.25) and +0.5% CPU
a frame at 30 fps (t = 0.4). The 3-5% the first runs showed (5-8 runs each, another link of the
same code) did not hold up. Structurally off is one load and one branch, plus one more call level
than the alias the hooks replace, at each of about 3,100 flush calls a frame in the race (the
counts below): a few microseconds a frame **[I]**.

The control separates the hooks from the link: the M2 objects linked without the hook trampolines
(the hooked functions stay the generated aliases, RunCommandBuffer without its scope), so it runs
M1's code. It is the most expensive build at 30 fps (+7.5%, t = 4.8), more than M2 off with the
hooks. What a relink moves (the generated functions start 1,952 bytes later in all three M2
links, and the game's own objects and data after `native_device.cpp` move) changes CPU time by
several percent either way, without any of the new code running: that is the size of the
differences between two links on this PC, and the hooks' cost is below it. A variant whose off
path is a tail call (no stack frame: the load, the branch and a jump; the shadow mode's work out of
line) measured +3.3% fps in the race and +2.8% CPU at 30 fps, both directions again, so it is not
kept. Shadow mode is within the same spread as off.

Flush points a frame, the race (shadow mode's counters): DrawVertices 387, DrawIndexedVertices
464, BeginVertices 15, DrawVerticesUP 9, DrawIndexedVerticesUP 11, Resolve 41, the clear 16,
RunCommandBuffer 929, predication 807, tiling 2, segment kick 49, fence 7, waits 5, swap 1.

The shadow mode's own work, from its counters:

| | Race | Free roam |
| --- | ---: | ---: |
| Groups (runs) | 123,300 (5) | 7,800 (2) |
| Records, 19 a group | 2,342,700 | 148,200 |
| Agree on what the shaders read | **100%** | **100%** |
| Agree on everything (the clears and resolves) | 26.3% | 26.3% |
| Groups whose draws and copies match the calls | **100%** | **100%** |
| Record building, render thread | 5.6-6.2 us a frame (0.30-0.33 us a record) | 6.3 us a frame |
| The check, recorder thread | 0.93-1.08 us a record (about 19 us a frame) | 1.0-1.1 us a record |
| Command buffer words a frame (the 6-word packets, begin and end) | 135 | 135 |
| Content compares | 2,000 (one group in 60) | 100 |
| Mask pixels bit-identical | 98.71% of 1.84 G | 98.07% of 92 M |
| Off by 1/255 | 1.29% | 1.93% |
| Off by 2/255 / by more | 44 / 5 pixels | 1 / 0 pixels |

Four more race runs in shadow mode (the A/B above): 99,000 groups, 1,881,000 records, all agree on
what is read, every group's counts match; 98.73% of 1.47 G mask pixels identical, 25 pixels off by
2/255 or more; record building 4.9-5.8 us a frame, the check 0.93-0.96 us a record.

- The record building is far below the M2 budget (0.3 ms a frame for the whole frame): at
  0.3 us a record, the race's ~850 D3D draws a frame would cost about 0.25 ms. RunCommandBuffer's
  ~930 calls a frame and the model lists they run are not recorded yet.
- **Contents:** within the plan's budget (98% identical, 1/255 on 99.99%). The 1/255 differences
  are the prototype's (the native blur's fp32 rounds up where the host's filtered fetch and 8-bit
  target round down). The 5 pixels off by 3/255 or more in 1.84 G (the largest 27/255, one pixel
  in one compare: emulated FF, native E4, depth test passing natively) are taken to be a depth
  test decided differently at a tie: natively the quad's z against the resolved 20e4 depth, in
  the emulator against the host depth buffer **[I]**.
- **The render test's scenes** (title, free roam at midday and dawn, cockpit, photo mode, race
  start; the console filter, so its one box blur): 6,300 groups in 9 shapes (the group shrinks to
  2, 5, 8, 11 or 14 draws on some frames: fewer cascades), all 107,280 records agree on what is
  read and every group's counts match.

Render test against the M0 baselines, the same M2 build: **6/6 with the mode off and 6/6 in
shadow mode comparing every group** (a second shadow session: 7,500 groups, all 127,671 records
agree on what is read, every group's counts match). The first shadow session passed 1 of 6 scenes
(title); the five others differed in world state (other AI drivers' names, the race timer 13.18 s
against 13.81 s, a car that is not in the baseline), not in the shadows: the world moved on
differently in that session, as in one of M1's write sessions (#860). Nothing presented changes.

The final build (ReIGlue 0ce74d3, main merged), five sessions alternating: **off 2/6, 6/6, 6/6;
shadow comparing every group 6/6, 6/6** (15,000 groups, all 253,157 records agree on what is
read, every group's counts match). The off session that passed 2 of 6 ran none of M2's code; its
four failing scenes differ in the moment, not in shading: another traffic car and the camera's
yaw 280.1 against 280.0 in photo mode, the trees' wind animation in free roam.

**Not done yet** (the rest of M2): records for every flush call of the frame (the scene's ~850
DrawVertices and DrawIndexedVertices a frame in the race) and the converter for the model lists
RunCommandBuffer runs (about 930 calls a frame), checked the same way; the M0 scene set beyond the
render test's (menus, garage, photo capture, online, FM4 circuits); world AO on and render scale
above 1 (the content compare refuses a scaled mask; the state check runs).

### M3: the shadow mask natively (first part: its blurs and resolves)

`--native_render=hybrid` (testing; `off` stays the default) records the shadow mask group as shadow
mode does, and the GPU plugin draws part of it natively: **the mask's blurs and the mask's resolves
after them** are no longer made as D3D calls. Everything else in the frame, the mask's clear and its
11 cascade passes included, stays emulated. `--native_shadow_mask=false` keeps the whole group
emulated in hybrid mode. Off, nothing changes (the hooks' one branch, as in M2).

- **The flush points skip the call.** Inside the blur hook (`src/hooks/shadow_filter.cpp`, the
  shadow renderer's own mask only) a `BlurScope` marks the records of the blur's DrawVerticesUP with
  `kRecordBlur` and the blur's kind; a `MaskResolveScope` marks the resolves of the mask after the
  group's first blur (`kRecordMaskResolve`): the Softer filter's resolve between its passes, photo
  mode's, and the world pass's. In a native group those flush points build their record and do not
  call D3D (`kRecordNative`). The D3D setters still run, so the device's shadow state stays what
  later game code reads; the dirty bits the skipped flushes would have cleared are flushed by the
  next emulated draw.
- **Decided before anything is skipped.** At the group's first blur (`ShadowMaskBlurNative`), and
  kept for the group: hybrid mode, `native_shadow_mask` on, the plugin's status bit set, world AO
  off, and a blur quad that covers the screen (the no-sun path at dawn blurs a zeroed quad after a
  clear, and the world pass's resolve must copy that clear: the prototype's dawn failure). The
  status word is guest memory the game passes in each group's begin packet; the plugin writes it at
  each begin (ReIGlue 0630): set when it can draw the blurs with the current settings (Vulkan, any
  integer render scale; no fractional raster, no independent scene size). D3D12, ReGPU and older
  plugins never set it, so the game draws the group itself there. A plugin that cannot draw a record
  although the game skipped the call (settings changed in the last frame or two) leaves the mask the
  cascades resolved, unblurred, and clears its bit for 300 groups.
- **What the plugin draws** (`fh1_native_shadow_compare.cpp`): each blur is one compute dispatch in
  stream order, from its record alone: the mask's fetch constant (fetch constant 1) and the quad from
  the blur's record, the depth test from the record's RB_DEPTHCONTROL (LEQUAL, no writes), the depth
  pre-pass resolve from the cascade passes' records. All of the game's blurs: the console filter's
  2x2 box (`58CE51CE56247C16`, four bilinear taps), photo mode's 4-tap pair (`85C2871B0E0A740B`,
  `F6EE736B656E5942`) and the 6-tap pair Softer uses (`DBD55E02669D5CB1`, `D9A1FFC33C029F54`); every tap
  is at a half texel of a one-to-one quad, at any render scale (the translator scales the offsets),
  so each is the mean of two or four neighbours. A pass reads the mask from guest memory or a
  private buffer and writes the other; when its input is a private buffer it writes the mask's own
  memory and marks it resolved, as a resolve does (render scale above 1: in the scaled resolve
  buffer, with the scaled texture loads' addressing). A resolve only says where the mask is from
  then on, and at the group's end the mask is copied to its memory if it is not there. Softer is two
  dispatches (the horizontal pass into a private buffer, the vertical one into the mask), the console
  box one and a copy.
- **Compare mode** is shadow mode's (`--native_render=shadow --native_shadow_compare_every=N`): the
  game makes every call, and the same dispatches run into private buffers only; after the world
  pass's resolve a compare dispatch counts the pixels where they differ from the emulated mask. It
  now covers every blur kind and render scale above 1, and checks the blurs' weights against the
  literals the guest's draws load (`blur H loads c255 = (0.27272728, 0.045454547, 0.18181819, 0)`,
  the box `(0.25, 0, 0, 0)`, the 4-tap pair `(0.2, 0.3, 0, 0)`).
- **Logs:** `native_render hybrid:` every 600 frames from the game (groups drawn natively, calls
  skipped, groups the game drew itself and why); `FH1 native shadow mask` from the plugin (groups,
  blurs by kind, refusals) and the state check's `drawn natively` count. GPU time: the profile zone
  `fh1 native shadow mask`.

**Measured** **[M]** (RX 9060 XT, RADV; 1280x720, hidden display, no `forza.toml`; one game and
plugin build for every mode, only the flags differ):

| Compare mode (shadow mode, every group) | Compares | Pixels | Identical | Off by 1/255 | Off by 2 / by 3+ |
| --- | ---: | ---: | ---: | ---: | ---: |
| Race, Softer (6-tap pair), scale 1 | 10,550 | 9.72 G | 98.64% | 1.36% | 473 / 12 |
| Race, console (box), scale 1 | 11,650 | 10.74 G | 99.80% | 0.20% | 0 / 6 (after the first compare) |
| Race, Softer, render scale 2 (2560x1440) | 2,100 | 7.74 G | 98.62% | 1.36% | 238,246 / 570,582 |
| Render test scenes, console: free roam at midday | 544 | 501 M | 99.66% | 0.34% | 0 / 4 |
| ... at dawn | 6 | 5.5 M | 99.66% | 0.34% | 0 / 0 |
| ... cockpit | 750 | 691 M | 98.50% | 1.50% | 4 / 2 |
| ... photo mode (the 4-tap pair) | 1,300 | 1.20 G | 99.23% | 0.77% | 606 / 4 |
| ... race start | 4,300 | 3.96 G | 99.69% | 0.31% | 0 / 2 |

- **In the plan's budget everywhere at render scale 1** (98% identical, 1/255 on 99.99%). The
  1/255 differences are the prototype's (fp32 rounding against the host's filtered fetch and 8-bit
  target). The few pixels off by 3/255 or more are single pixels (12 in 9.7 G in the race), taken
  to be the depth test decided differently at a tie, as in M2 **[I]**.
- **The first compare of a session is not counted** in the box row: its emulated blur draws were
  skipped while their pipelines were still being built (the log's "draws skipped" line at that
  frame; the scratch caches start cold), so the emulated mask there is the unblurred one (92,818
  pixels differ, up to 191/255). The Softer race's first compare differs the same way in the two
  bytes the blur writes as constants (713,484 pixels, red identical).
- **Render scale 2 is in budget but not as close:** 99.993% of the pixels within 1/255, and every
  logged pixel off by 3/255 or more (252 of 252 samples) is in the mask's first column or first row:
  the emulated sampling of the scaled mask at its top and left edges, which the native pass clamps
  differently **[I]**; to match in a follow-up.
- **At dawn** the no-sun path blurs a zeroed quad after clearing the mask: the game keeps those
  groups emulated (721 groups in a render test session, "quad"), and the 6 compares are the frames
  with sun shadows before it.
- **The compare found a wrong constant:** photo mode's 4-tap pair loads c255 = (0.2, 0.3), not the
  0.25 first assumed; before the fix photo mode's compare had 94.7% identical and 23 M pixels off by
  3/255 or more, yet the render test passed photo mode (dssim 0.0072 against 0.016). Compare mode is
  the check that catches a native pass's arithmetic.
- **Hybrid in the race:** every group native after the plugin's first status (2 groups emulated at
  the start), 4 calls skipped a group with Softer (2 blurs, 2 resolves), 2 with the console box, 0 refusals, 0
  calls made inside a native blur, and the state check still agrees on everything read in every
  record and on every group's counts. At render scale 2: 6,000 groups, 0 refusals. With world AO
  on, every group stays emulated ("world AO").

Render test against the M0 baselines, the same game build: **hybrid 6/6 in all four sessions, off
4/6, 6/6, 6/6, shadow mode comparing every group 5/6, 6/6** (the last two hybrid sessions, one off
and the second compare session with the final plugin). In a hybrid session the plugin drew the box
(about 4,960 groups), the 4-tap pair (about 1,040 each) and the game kept 721 dawn groups emulated.
The off session at 4/6 failed free roam at midday (0.0058 against 0.005) and at dawn (0.0101 against
0.010), and the first compare session midday (0.0061): neither draws anything natively, so this is
the world moving on differently between sessions, as in M2's off sessions (#860). The first two
hybrid sessions ran photo mode's first, wrong weights and still passed.

The A/B: the same game and plugin, `native_render=off` (O) against `hybrid` (H), six alternating
rounds (the order flipped every round), each run alone with the benchmark lock; the race uncapped
with Softer (its window 60-130 s), free roam standing at 30 fps (90-150 s). One free roam pair was
run again: the first H run of round 3 had other processes busy on 1.8 cores (25.1 ms a frame).

| Race, 6 rounds | O | H | H against O |
| --- | ---: | ---: | ---: |
| fps (result.json) | 170.5 (159.6-185.1) | 173.0 (161.2-186.3) | +1.4% (t = 0.44; paired +2.5, se 7.0) |
| Window fps | 182.1 | 182.4 | +0.2% (t = 0.05) |
| 1% low / p99 ms | 108.9 / 7.94 | 109.3 / 7.91 | t = 0.07 / -0.13 |
| CPU ms a frame (the game's) | 16.88 | 16.84 | -0.3% (t = -0.12) |
| Render thread / GPU recorder / decoder, ms a frame | 3.19 / 4.49 / 2.51 | 3.25 / 4.44 / 2.50 | within one sd (0.25 / 0.24 / 0.06) |
| **Free roam, 30 fps: CPU ms a frame** | 22.06 | 22.16 | +0.5% (t = 0.24) |

| GPU zones, race, ms a frame (windows 60-130 s) | O pair 1 | H pair 1 | O pair 2 | H pair 2 |
| --- | ---: | ---: | ---: | ---: |
| The blur's two render passes (`ps DBD55E02...`, `ps D9A1FFC3...`) | 0.046 | 0 | 0.049 | 0 |
| Loads of 1280x720 k_8_8_8_8 textures (the mask's among them) | 0.105 | 0.068 | 0.106 | 0.066 |
| `fh1 native shadow mask` (two dispatches) | 0 | 0.088 | 0 | 0.090 |
| All resolves (about 24 a frame, two of them skipped in H) | 0.618 | 0.613 | 0.671 | 0.635 |
| **Sum** | **0.769** | **0.769** | **0.826** | **0.791** |
| The 11 cascade passes (still emulated) | 0.089 | 0.097 | 0.097 | 0.099 |

- **No gain survives the noise, and none was expected from this part alone:** the fps, CPU and
  thread differences are all below one standard error. The GPU work of the group is the same within
  0.005 ms without the resolves (removed 0.084-0.089 ms of render passes and texture loads, added
  0.088-0.090 ms of dispatches) and 0.000-0.035 ms less with them; the busy totals differ by the
  scene. Not worse, as M3 asks, on this PC.
- **Room in the dispatches:** each pass is 0.044 ms at 1280x720 (the prototype's estimate for the two
  fused in one dispatch, the row in shared memory, is about 0.02 ms). The saving M3 is for comes
  with the cascades: their 11 passes and the mask's EDRAM render target go native too.

**Seams with other work.** The plugin's `Fh1NativeShadowMaskSceneSupported` is where M3 meets
independent scene dimensions (ReIGlue #56, `fh1_native_scene_width`/`height`, not in this build):
with a fractional raster or a scene size set it does not set the status bit, so the game keeps the
group emulated; with that work merged, the check becomes a size check there. The one call that
registers M3's writes as a resolve is the plugin's `MarkRangeAsResolved` after each pass into the
mask's memory, where #56's resolve provenance would tag the range as the scene's resolve of the
mask, as the emulated world pass's resolve does. World AO is the other seam: its factor is
multiplied into the mask by a draw inside the blur's render pass (`fh1_world_ao.cpp`), which a
native blur does not have; until the plugin can apply the factor in its own pass, AO on keeps the
group emulated (decided by the game, before anything is skipped).

**Not done yet** (the rest of M3):

1. **The mask's clear and its 11 cascade passes natively.** They are the game's ApplyShadows
   shaders (7 pixel shaders, one vertex shader), drawn through DrawIndexedVerticesUP, reading the
   pre-pass depth and the cascade's shadow map, and testing against the scene's depth and stencil
   in EDRAM (with two depth/stencil clears between them). They need what this part does not have:
   **a general draw executor** (a draw from a record with the M1 pack's translated shaders, the
   system constants the translator expects, texture bindings from the record's fetch constants),
   **host images** for the mask's target and for that depth and stencil (or an explicit copy), and
   the **exit state written back** for the next emulated draw. M4's native passes (the shadow maps,
   then the scene's model lists) are planned on that same executor, so it is the next step, not a
   shadow-mask special case like the compute blurs here.
2. **The world pass sampling the mask as a host image** instead of from guest memory and the
   texture cache (today the native mask goes through memory as a resolve's does, and the world
   pass loads it as a texture).
3. **World AO in the native pass** (today AO on keeps the group emulated).
4. **The top and left edges at render scale 2** (above), and the two dispatches fused.
5. **The GTX 1650 Ti laptop:** the measurements above are this PC's only.

### M4: the model lists (first step: the converter and its offline proof)

A track's models do not draw through DrawIndexedVertices. Each carries, per pass group (pass x 3 +
layer), one **precompiled D3D command buffer** that RunCommandBuffer runs. The lists "with
SET_CONSTANT" of the table at the top are the scene pass's (23.4% of the dwords the command
processor read in the race **[M]**); the lists of the depth pre-pass, the shadow maps and the two
256x256 passes are part of the class without it (25.3%), whose split between model lists and the
command buffers D3D records at run time no trace has made yet. M4 starts by converting these lists;
this first step is the converter alone, proven on the game's files. **Nothing runs in the game
yet:** no hook, no switch, no change for a player.

**What a list is [M, every list of the files below].** A 0xC8-byte header (the state masks it
inherits and leaves, flags, one segment), a patch table, plain PM4 and a table of buffer fixups.
The PM4 holds type-0 register writes, padding and six type-3 opcodes (`DRAW_INDX`, `IM_LOAD`,
`LOAD_ALU_CONSTANT`, and in the scene pass `SET_BIN_MASK_LO/HI` with `SET_CONSTANT`): 34 state
registers, VS c0-c11, PS c0-c7 and c116-c127, fetch constants 0-7 and 31, the shaders' literal
blocks, and D3D's own marks. Every word the game changes after loading (buffer and shader
addresses, textures, the viewport, the surfaces, the scissor, a block of pixel constants) is named
by an entry of the two tables: a **place**.

**The converter** (`src/platform/native_list.h`, pure functions, `tests/native_list_test.cpp` in
CI) turns one list into a **template**, a block of words with two views of the list:

- **the ops in list order** (register runs, tile blocks, shader loads, literal loads, draws):
  replaying them writes what the packets write, in the same order. This is what a replay in the
  command processor needs, tile blocks and predicate bits included;
- **the draws**: per draw its arguments and predicate bit, the shaders, vertex buffer and textures
  bound when it runs (carried forward when the draw sets none), and the registers written since
  the previous draw, reduced to the last write of each. Folded, they give the list's own state at
  draw k. This is what a native draw needs.

Rules it keeps:

- **Places are slots, never values.** A template holds where a patched word is in the live list
  (fixup type, argument, dword), never what it holds; the words are read from the list when a
  fixup has touched it. So a template is a function of the list's static words alone. The kind of
  a place is read from the field it lands on, not from the game's lookup records, so a list can
  be converted from its command buffer alone.
- **The key** is 128 bits over those static words: the PM4 with every place as zero, the patch
  table, the buffer fixups, the ten masks and the flags without 0x2000 (which the texture and
  surface fixups set at run time). One thing of a place is taken as static and stays in: the low
  two bits of a shader's microcode address, the shader type. That a fixup keeps them is read from
  the code and the files, not from a run **[I]**: the game's two shader fixups write the entry's
  value word plus the shader's address (with bit 0 set in it for a pixel shader's microcode), and
  every such value word in the files is a multiple of 4 (table below), so the bits are the file's
  as long as a shader's address is a multiple of 4 too. A live word with other bits would have
  another key, and its list would run as PM4.
- **The untiled form is the same template.** `scene_msaa=2x|off` and the game's 12- and 16-car
  races run the scene pass's lists with their tile-bound packets turned into NOPs
  (`src/hooks/scene_tiling.cpp`). The template records those packets' headers: the key takes a
  header or its NOP as the same word, and both views have an untiled mode that skips exactly those
  packets. A list is converted from its original words; one that is untiled at that moment is
  refused as input. Which form a live list is in is read from its word 4, the first tile's window
  write, as `scene_tiling.cpp` reads it (`Untiled()`). So a list recorded for the tiles has to
  start with a tile block, as every one in the files does; one that starts otherwise is refused,
  since that word could then be a value that only reads as the header.
- **Everything outside what the files hold is refused, with a named reason** (25 of them, each
  with a test): another resource type or flag, more than one segment, a type-1 packet, another
  opcode, a register outside the set above, a tile block of another shape, a list recorded for
  the tiles that does not start with one, a draw, shader load or literal load of another form, an
  address that is not a place, a place that lands on no known field, a place with sub-code 62 or
  63 (the path through the table at `cb+112`, not decoded). A refused list runs as PM4, as today.
- **Whatever is not refused has to be right, also for a list no file has.** The test changes
  words of its lists at random and cuts the lists short, and every one that still converts has to
  agree with the interpreter. Each part of a list it hands to the converter ends at a page that
  cannot be read, and CI runs it a second time unoptimized under the address and
  undefined-behaviour sanitizers (an optimized build drops a read whose value goes unused), so a
  read past a list's end fails it. This is evidence, not a proof: it holds for what the test
  reaches. Run by hand **[M]**: with one check of the converter removed or weakened at a time (73
  single changes), the unoptimized test fails on all 73; 3 million changed lists, 1.57 million of
  them converted, all agree; and unoptimized under the sanitizers, 450,000 more (236,160
  converted) and a separate fuzz of 430,032 changed, cut and made-up lists (168,316 converted),
  with every part in a block of exactly its size, give no disagreement and no report. Nor does
  that fuzz, under the sanitizers, over the 142,334 lists of the distinct models of Colorado, the
  UI tracks, the DLC and six FM4 circuits, each list as it is, cut short nine ways and changed
  four ways (1,992,676 lists, 321,582 converted).

**Offline proof.** `tools/native_list_check.py` (below) reads every model of an archive, converts
every list and runs both views beside a plain PM4 interpreter that knows nothing of templates
(`tests/native_list_reference.h`): the state before every draw (the whole register file the list
wrote, the bound shaders, the bin mask), every draw's arguments and whether its predicate lets it
run, and the state after the list. Each list runs for the bin select of each of the three tiles,
without tiling, and with a bin mask that excludes the bin; lists scene_msaa unties run all five
again untiled (untiled by `scene_tiling.h`'s own function, and replayed in the mode `Untiled()`
reads from the words); and everything runs twice, with the words as they are in the file and
with every place written with made-up values, as after the game's fixups. Counts, from the
game's files on the test PC (2026-11-14) **[M]**:

| | Colorado | UI tracks (5 archives) | Rally DLC (ColoradoDirt) | FM4 circuit (Suzuka, imported) |
| --- | ---: | ---: | ---: | ---: |
| `.rmb.bin` members read (distinct by CRC and size) | 102,012 (14,231) | 15 (15) | 9,858 (9,851) | 1,393 (1,347) |
| Lists | 410,639 | 41 | 41,026 | 6,087 |
| PM4 dwords | 253,695,465 | 190,570 | 27,112,955 | 2,065,593 |
| **Converted** | **410,639** | **41** | **41,026** | 3,287 |
| **Refused** | **0** | **0** | **0** | 2,800 (2,798 `tile-block`, 2 `register`) |
| Distinct keys | 51,633 | 41 | 36,899 | 2,874 |
| Draws | 1,324,931 | 905 | 142,001 | 9,960 (5,590 in converted lists) |
| Slots | 17,790,277 | 12,906 | 1,909,118 | 125,088 |
| Places of the game's own records that are a slot of the record's type and length | 29,200,919 of 29,200,919 | 122,824 of 122,824 | 3,452,168 of 3,452,168 | 149,148 of 149,148 |
| Slots no record names | 0 | 0 | 0 | 0 |
| Lists scene_msaa unties; their tile-bound packets equal to `scene_tiling.h`'s; `Untiled()` right for both forms | 104,221; 104,221; 104,221 | 15; 15; 15 | 10,490; 10,490; 10,490 | 0 |
| Runs beside the interpreter in which everything agrees (2 forms x 5 modes) | 4,106,390 of 4,106,390 | 410 of 410 | 410,260 of 410,260 | 32,870 of 32,870 |
| ... the same, untiled | 1,042,210 of 1,042,210 | 150 of 150 | 104,900 of 104,900 | none |
| Draws compared in those runs | 16,492,630 | 12,090 | 1,776,770 | 55,900 |
| Key the same after fixups / same template after fixups / with flag 0x2000 | 410,639 each | 41 each | 41,026 each | 3,287 each |
| Key the same for the untiled list; that list refused as input | 104,221; 104,221 | 15; 15 | 10,490; 10,490 | none |
| Another key after one changed PM4 word / table word / mask | 410,639 each | 41 each | 41,026 each | 3,287 each |
| Table entries with sub-code 62 or 63 | 0 of 29,224,048 | 0 of 122,827 | 0 of 3,454,427 | 0 of 255,519 |
| Table entries of a shader load's address whose value word is not a multiple of 4 | 0 of 7,632,010 | 0 of 56,262 | 0 of 1,026,154 | 0 of 43,084 |
| Draws that load no vertex shader | 15 | 0 | 15 | 0 |
| Distinct register deltas / fetch templates / exit sets / vertex constant blobs / pixel constant blobs | 397 / 97 / 242 / 20,018 / 1,552 | 36 / 8 / 16 / 123 / 65 | 380 / 86 / 220 / 16,536 / 1,190 | 151 / 25 / 97 / 1,001 / 166 |

- **Colorado: every list converts, none is refused, and every comparison holds on all of them.**
  The run reads every member, the archive's repeats included, in under a minute (47 and 50 s in
  two runs with three reading processes).
- The last row is what a list's draws reduce to when a place counts as its fixup type: Colorado's
  1.3 M draws share 397 sets of state registers and 97 sets of fetch constants.
- **The 15 draws that load no vertex shader** are in 6 lists (5 draws each in groups 0, 12 and
  18): each writes only `VGT_INDX_OFFSET` before the draw, no fetch constant and no shader, 12 of
  them with 32-bit indices, none the first draw of its list. Such a draw continues the one before
  it with the same shader and vertex buffer, which the draw records carry forward (15 of 15). The
  DLC has 15 of the same kind, in 6 lists.
- **Conversion costs 15 to 19 ns a dword** on one thread (Colorado's 253.7 M dwords in 3.7, 3.8
  and 4.8 s in three runs on a PC doing other work), and a template is 2.06 words for each dword
  of its list (39% of it the list's static words, 20% the ops, 16% the per-draw register runs,
  14% the slots).
- **The FM4 circuit is not covered in its scene passes.** FM4's models have their own lists for
  the two-tile and the one-pass scene (groups 9-11 and 15-17, which no Horizon model has), and
  their tile blocks set the window with `SET_CONSTANT2` and have two tiles in pass 3: all 2,798
  lists flagged 0x800 are refused as `tile-block`. Two lists of the one-pass scene are refused as
  `register`: they write `RB_ALPHA_REF` (0x210E). The other 3,287 (depth pre-pass, the 256x256
  passes, the one-pass scene) convert and agree. Six imported circuits together: 43,545 lists, 22,656 converted, 20,862
  `tile-block`, 27 `register`. FM4's lists also have no SCRATCH_REG6 marks, name their vertex and
  index buffers in the buffer fixups only, and have all mask bits set in every pass. These
  circuits were imported before the importer began to turn the window write at the end of their
  depth pre-pass lists into padding (#827). With that step applied in memory to this circuit's
  models, 154 change, and their 154 group 0 lists convert and agree in 1,540 of 1,540 runs.

What the files settled for the next steps:

- **D3D's SCRATCH_REG6 marks are one-register packets** (type 0 with bit 15: every word goes to
  the one register), 2,438,078 of them in Colorado, and the only use of that form. A walker that
  reads them as a register range reaches 0x57F-0x581.
- **A draw packet writes three registers itself** (`VGT_DRAW_INITIATOR`, `VGT_DMA_BASE`,
  `VGT_DMA_SIZE`), so the register file after a list holds its last draw that ran; both views
  carry that.
- **A place's entry says its own length** (bits 26-29: one word, or two with a value word), so
  the tables can be walked without the game's records. A surfaces fixup's chain may start with an
  entry of sub-code 9 that names dword 0 and writes nothing (it changes how the chain's surface
  formats are read); 130 texture fetch constants in Colorado have no place at all and are the
  file's words. A Horizon list's patch table names its vertex and index buffers as the buffer
  fixups do: all 410,639 Colorado lists, the DLC's 41,026 and the UI tracks' 41 get the same slots
  with the buffer fixups left out. FM4's lists name them there only (0 of 3,287).
- **In the files the inherited and the left-untouched masks are equal:** all bits set for passes
  1, 2 and 4, and `FFFFFFF7` in the third mask of both for passes 0 and 6, in all 410,639 lists.
  Whether the headers in memory are the files' needs a run.

### M4, second step: what the game's command lists cost (the measurement build)

The list trace (game `FORZA_LIST_TRACE`, ReIGlue 0640: [Tools](#list-trace-game-and-sdk)) answers
the questions of the model list plan that only a run can. It changes nothing in a normal build:
both options are off by default and compiled out.

Every number below comes from one set of builds of this step's code (the game's list trace and
hooks as committed, ReIGlue 0640 on main as of #63) and names its runs. Main merged afterwards
brought ReIGlue 0800 (stencil transfers without stencil export, which RADV has) and game changes to
FM4 tracks and cars, none of which the trace or the measured scenes touch; the code comparison
below is repeated at the merged heads. RX 9060 XT,
RADV; 1280x720 on a hidden display, no `forza.toml`, a copy of the test save, Colorado, offline.
"Other load" is the CPU that processes other than the game, its samplers and its hidden display
used during a run's window, from `/proc` (cores).

**No change with the options off [M].** Main (M) against this branch built with both options off
(O); the benchmark, uncapped, window 60-130 s; each run alone (no other game, the benchmark lock)
and only after the PC's CPU over 10 s was below 0.3 cores; two rounds of four pairs:

| Benchmark, 16 runs | Main (M) | Options off (O) | O against M (95% from the 8 pairs) |
| --- | ---: | ---: | ---: |
| fps (result.json) | 178.6 (162.2-195.2) | 174.2 (161.2-194.0) | -4.5 (-2.5%) ± 13.9, t = -0.81 |
| ... round 1, M O O M M O O M | 185.4 | 174.7 | -10.7 (-5.8%), t = -1.57 |
| ... round 2, O M M O O M M O | 171.9 | 173.6 | +1.7 (+1.0%), t = +0.21 |
| window fps (60-130 s) | 190.5 | 189.6 | -0.9 (-0.5%) ± 16.0 |
| decoder / recorder / render thread, ms a frame | 2.41 / 4.37 / 2.82 | 2.45 / 4.42 / 2.86 | +0.04 ± 0.14 / +0.05 ± 0.28 / +0.04 ± 0.41 |
| other load before the run / in its window | 0.17-0.24 / 0.23-1.93 | 0.18-0.23 / 0.22-0.97 | |

- **No difference within the noise, and the noise is the benchmark's own.** On a quiet PC a run's
  fps still varies by about 11 fps (6%): the AI race differs from run to run. Round 1 alone showed
  -5.8%, round 2 +1.0%; all 16 runs give -2.5% (t = -0.81), and the eight pairs bound the
  difference only to about ±8%. The A/B cannot see a smaller change; the code comparison shows
  there is none.
- **The code is the same.** The timed binaries: the two games' `.text` sections (64,157,141 bytes)
  differ in 15 bytes: 4 are source line numbers one higher in the log calls of two
  `scene_tiling.cpp` hooks (the include line above them), 11 are the build's commit id, which the
  game embeds in its request headers (`.rodata`'s 9 differing bytes are the same id). The two
  plugins' `.text` (3,175,290 bytes) differ in 38 bytes, line numbers a few higher in the log and
  assert macros of `command_processor.cpp` and `graphics_system.cpp`, and `.rodata` in the version
  string. Rebuilt at the merged heads (main against this branch, options off): the games' `.text`
  (64,166,069 bytes) differ in the same 4 line numbers and 10 bytes of the commit id, the plugins'
  (3,180,282 bytes) in the same 38 bytes. The macros in the hooks compile to nothing and
  `list_trace.cpp` / `fh1_list_trace.cpp` are not built.
- A first attempt (16 runs on a PC with 0.9-3.8 cores of other load) showed -9% in one round of
  four pairs and +1.6% in the other: that was the other load, and it is replaced by the runs above.

**Runs [M]** (trace builds of both sides unless marked; times are a frame's, over the window):

| Run | Scene | Window | Other load |
| --- | --- | --- | --- |
| el-1 ... el-8, el-10, el-11 | the built-in benchmark race (8 cars), uncapped, each alone on a quiet PC; E: elision on (as shipped), N: `--gpu_split_elide_unchanged_registers=false`; order E N N E E N N E, then N E (el-10, el-11; a run first numbered el-9 was overwritten by a script error and is not used) | 60-130 s, 11,700-12,300 frames | 0.21-0.56 (el-1: 1.8, the desktop file indexer) |
| stand | free roam at 30 fps, standing at the spawn (no input after the menus) | 100-190 s | 2.6 |
| free | free roam at 30 fps, driving (throttle held) on the road from the spawn | 100-190 s | 1.5 |
| race | GoPro Millennia Masters at the spawn, 8 cars, standing on the grid, 30 fps | 160-220 s | 1.0 |
| race2x | the benchmark race at 30 fps with `scene_msaa=2x`: the game's two-tile render scenario, the one its 12- and 16-car races use (below) | 60-130 s | 1.2 |
| photo | photo mode (TAKE PHOTO) at the spawn, 30 fps | 105-165 s | 1.1 |
| msaaoff | free roam driving at 30 fps with `scene_msaa=off` (one pass, pass 4's lists untiled) | 100-160 s | 1.2 |
| attr | the benchmark at 30 fps with the D3D and PM4 traces as well (calls by device, every foreign buffer); not for times | whole run | |

- The 30 fps runs are not timed runs: another short game check ran next to some of them (other
  load 1.0-2.6 cores). Counts do not depend on it; their shares of the decoder's time a little.
- **A 16-car race cannot be run offline.** Every event in the game's database has 8 cars (105 with
  7 drivers, 14 with 0 or 1, `Events.NumberOfDrivers`), Custom Race allows at most 7 opponents,
  and online lobbies are 8 players ([#766](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/766)
  is open). What a 16-car race changes for the lists is its render scenario: the game picks the
  two-tile 2x MSAA one (`16-Car_Race.xml`, as for 12 cars), whose scene pass is 3, so pass 4's
  lists run without their tile layout. `scene_msaa=2x` forces that scenario on the 8-car
  benchmark race (race2x). It measures the lists' path of a 16-car race, not the cost of 16 cars
  (more clones and draws).
- The first version of this section used runs of intermediate builds; none of them is used here.

Reading the classes below: "D3D segments" are the device's own command buffer segments the ring
runs (the state flush in front of each list, D3D's draws); "model lists" are the files' lists
(told by the files' marker after the patch table, which the loader keeps); "clones" are command
buffers of type 9 made by D3D's command buffer clone (below): the cars and other objects drawn the
same way; the HUD's command buffer slots are counted with them.

**Where the decoder's time goes [M]** (ms a frame: the buffers' own time measured with the time
stamp counter, in the trace build; % of the decoder's time in all buffers; the decoder thread's
CPU from `/proc`; ns a dword: the class's own time over its dwords):

| Run | fps | Decoder thread, CPU | In the buffers | D3D segments | **Model lists** | ... pass 4 | ... passes 0, 1, 6 | Clones | ... cars | ns a list dword | ns a segment dword |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bench el-1 | 169 | 2.67 | 2.43 | 0.94 (39%) | **1.00 (41%)** | 0.80 | 0.20 | 0.49 (20%) | 0.23 | 2.36 | 1.41 |
| bench el-4 | 179 | 2.60 | 2.27 | 0.90 (39%) | **0.98 (43%)** | 0.79 | 0.19 | 0.39 (17%) | 0.17 | 2.34 | 1.38 |
| bench el-5 | 177 | 2.65 | 2.48 | 0.91 (37%) | **0.80 (32%)** | 0.61 | 0.20 | 0.76 (31%) | 0.37 | 2.33 | 1.47 |
| bench el-8 | 175 | 2.73 | 2.52 | 0.94 (37%) | **0.90 (36%)** | 0.70 | 0.20 | 0.68 (27%) | 0.32 | 2.37 | 1.43 |
| bench el-11 | 183 | 2.66 | 2.39 | 0.92 (39%) | **0.94 (40%)** | 0.75 | 0.20 | 0.51 (21%) | 0.24 | 2.39 | 1.42 |
| stand | 30 | 2.14 | 1.92 | 0.72 (38%) | **0.71 (37%)** | 0.52 | 0.20 | 0.47 (25%) | 0.23 | 2.61 | 1.85 |
| free | 30 | 2.10 | 1.87 | 0.71 (38%) | **0.74 (40%)** | 0.55 | 0.20 | 0.41 (22%) | 0.19 | 2.57 | 1.78 |
| race | 30 | 2.28 | 2.06 | 0.95 (46%) | **0.73 (36%)** | 0.51 | 0.22 | 0.37 (18%) | 0.16 | 2.56 | 1.67 |
| race2x | 30 | 2.19 | 1.95 | 0.76 (39%) | **0.92 (47%)** | 0.69 | 0.23 | 0.27 (14%) | 0.10 | 2.53 | 1.66 |
| photo | 30 | 2.74 | 2.32 | 0.72 (31%) | **0.82 (35%)** | 0.64 | 0.18 | 0.77 (33%) | 0.44 | 2.48 | 1.66 |
| msaaoff | 30 | 1.27 | 1.13 | 0.39 (34%) | **0.38 (34%)** | 0.24 | 0.15 | 0.35 (31%) | 0.17 | 2.59 | 2.10 |

- **The milliseconds are the trace build's wall times, not a normal build's.** The decoder can be
  preempted inside a buffer, and the trace's bookkeeping between buffers (registry lookups, the
  per-run counters) is counted in the D3D segments' own time, which makes their share and ns a
  dword a little high and the lists' share conservative. The trace itself costs the decoder thread about 0.2 ms a frame in the benchmark: 2.60-2.73 ms in the elision-on runs against 2.30-2.57 ms (mean 2.45) with the options off. Use the shares; times for
  PR 4 come from PR 4's own baseline (below).
- The benchmark's 60-130 s window catches different parts of the race from run to run (loading
  takes longer or shorter): lists 345,000-423,000 dwords a frame, clones 191,000-382,000. Hence the
  ranges; a single run's figure is never the benchmark's.
- Model lists never ran while the decoder waited for the recorder (0 synced runs in every run),
  so their time is their own. `sub_8249F310`'s 15 small buffers a frame (675 dwords) are 0.01 ms.

**Answers [M unless marked]** (bench = el-1, el-4, el-5, el-8 and el-11, the elision-on runs):

| # | Question (plan section 7, PR 1) | Answer | Numbers | Run, counter |
| --- | --- | --- | --- | --- |
| 1 | Live header masks and flags against the files | **Masks: the files'.** **Flags: not the files'**: the loader sets 0x101 and clears 0x6 (the files' 0x86 / 0x886 are 0x181 / 0x981 in memory), and the texture and surface fixups set 0x2000. The live list with its flags put back in the files' form has the key of a file list (the whole static part: masks, flags, patch table, PM4 outside the places) | first looks: passes 1 and 4 all ones, passes 0 and 6 `FFFFFFF7` in the third pair, inherited = untouched, in every list of every run; flags 0x181, 0x2181 (passes 0, 1, 6), 0x2981 (pass 4), nothing else. Keys in the files' set (Colorado's distinct models, the converter of PR 1): every converted list of every run, 16,998 of 16,998 (bench 1,930-2,071 a session, stand 223, free 1,848, race 769, race2x 1,097, photo 334, msaaoff 836, attr 1,978), with the same pass group. Later calls: the masks never changed; the flags changed only in 0x2000 | all runs; game counters 6, 7, 25; `FH1_LIST_KEYS` against `native_list_check` keys |
| 2 | RunCommandBuffer calls by device, mode, flags, pass group, caller; the queued path | **One device, no recording, no queue.** Every call is on the main device (40162C00); its mode byte is 0x00, 0x20 or 0x21 (only 0x00 with `scene_msaa=off`; never 0x80, so no call is recorded into another buffer), and no list is copied inline (flag 0x20 never set). `sub_824467A8` never takes the queued path (its thread flag is 0 on every call) and `sub_82CE9258` is never called. Callers: `sub_824467A8` (from 0x8244706C: track and procedural models; 0x8246CF14: cars; 0x82469998, 0x8244A254, 0x8247C2B4, 0x8247D41C, 0x8247D968, 0x82426B9C, and in photo mode 0x82E5A300-0x82E5A894: the other clones) for all but the HUD's 0.9-2.6 a frame (`sub_8249E0E0`, 0x8249E118) | calls a frame in the window: bench 789-934 (model lists 520-656, clones 150-379, of them cars 37-98); stand 439 (292 lists, 147 clones), free 466, race 538, race2x 747, photo 634 (269 clones), msaaoff 453 | game counters 1-5, 15, 16, 24 |
| 3 | The exact split of the other foreign class | **Every buffer is classified, not only the 60 most read.** Dwords the command processor read a frame: bench 1.26-1.37 M: D3D segments 46-52%, model lists 26-33% (pass 4 19-27%, passes 0, 1, 6 6.2-6.5%), clones 15-28% (cars 83,000-181,000 dwords), `sub_8249F310`'s buffers 675. The plan's "26-43%" for model lists: inside it, near its floor in the benchmark. The class "foreign without SET_CONSTANT" of M0 is the untiled model lists plus every clone | lists / clones: stand 31.8% / 22.7%, free 33.3% / 20.4%, race 27.9% / 16.2%, race2x 38.8% / 12.4%, photo 29.3% / 31.9%, msaaoff 30.8% / 30.8% | `C` records, depth 2; `.lists` |
| 4 | Decoder time inside list buffers, by class (the ceiling of replay) | **Model lists are a third or more of the decoder's time in the buffers in every scene: 32-47%**, 2.3-2.6 ns a list dword. Replay can save at most that share of the decoder, and less: see below | bench 32%, 36%, 40%, 41%, 43% (el-5, el-8, el-11, el-1, el-4: the share follows the window's content, table above); stand 37%, free 40%, race 36%, race2x 47%, photo 35%, msaaoff 34%; pass 4 three quarters of it (76-81% in the bench). D3D segments 1.4-2.1 ns a dword of their own (mostly constant blocks) | `C` records (`ticks`, `sync_runs`) |
| 5 | Recorder cost of un-elided register runs | **78% of the register writes in model lists are elided today** (74-79% in every scene): unchanged values the decoder never hands to the recorder. A replay that copied the lists' runs without that check would hand the recorder about 240,000 more writes a frame in the benchmark. **That is not shown to fit PR 4's recorder limit (0.05 ms): replay must elide.** Priced from five alternating pairs with the elision off for everything (1.07 M more writes a frame), the lists' extra writes cost the recorder 0.01-0.04 ms a frame by the best estimates, but the upper ends of the 95% intervals are 0.06-0.07 ms. The decoder, which no longer compares, spent 0.16 ms less a frame (for all 1.07 M writes) | five pairs, N then E or E then N (el-1/2, 3/4, 5/6, 7/8, 10/11): recorder +0.049 ms a pair (sd 0.185), the lists' 245,003 extra writes +0.010 ms (95% -0.044 to +0.064); the four quiet pairs (el-1 had 1.8 cores of other load): +0.026 ms (95% -0.015 to +0.067); the recorder's time fitted against the writes handed on and the writes read (the window's content): all ten runs 0.139 ns a write (se 0.041), +0.034 ms (95% +0.010 to +0.058), the eight quiet runs 0.164 ns a write (se 0.033), +0.039 ms (95% +0.019 to +0.060). Lists' writes read / handed on a frame: bench 279,310-334,456 / 60,354-71,379; stand 221,240 / 57,420; race2x 284,790 / 62,894 | `C` records `rin`, `rout`; thread CPU from `/proc`; `--gpu_split_elide_unchanged_registers=false` |
| 6 | Runs per call for pass 4 lists, and what restricts a call to some tiles | **Every pass 4 call is read once per tile (three times; twice with two tiles) and runs on fewer**: the INDIRECT_BUFFER packet is predicated, and the bin mask in force when it is read (set by D3D before the call, from the game's predication `sub_8246BFE8`, 590-810 calls a frame in attr) excludes the tiles the object does not touch. The emulator writes full-screen EVENT_WRITE_EXT extents, so they are not what restricts it | runs / reads a call of pass 4 lists: bench 1.83-1.99 / 3.00; stand 2.26, free 2.19, race 2.08, photo 2.33 / 3.00; race2x 1.50 / 2.00; msaaoff 1.00 / 1.00. Untiled passes 1.000-1.001 runs a call (in four bench sessions 1,089-3,753 of their 5.1-5.7 M calls were predicated off) | `.lists` (calls, runs, skipped); attr `G 24` |
| 7 | What the second context records each frame; IBs to model lists in its buffers | **The HUD, and no model list.** The second context (4037FD80) records DrawIndexedVertices with their setters and makes no RunCommandBuffer call: its buffers hold no INDIRECT_BUFFER to a list. The main device runs the HUD's command buffer slots with `sub_8249E0E0` (flags 0x1); their words change from frame to frame (re-recorded). Every list and every clone runs at depth 2 (ring, D3D segment, buffer) | attr, per block of 300 frames: 4037FD80 made 0 RunCommandBuffer calls in all 11 of its blocks and 50,677-63,155 DrawIndexedVertices in the full blocks (169-211 a frame); every RunCommandBuffer was on 40162C00. HUD slots: 31-63 buffers a session, their sampled words changed 20-1,208 times | attr `GD`; `.lists`; `C` depths |
| 10 | Who records the 0x14D-0x161 buffers; are they re-recorded | **D3D's command buffer clone, `sub_82AA8220`**: it copies a command buffer's PM4 and tables into new memory (`sub_82AA8028`, memcpy) and marks the copy with 0x00100000 in its first word, which every buffer of this class has (0x00500009 against the files' lists' 0x00400009). It is called from the car presentation's `sub_82E98930` (return address 0x82E98AFC, 7,588-9,657 times a session) and 6 times a session from `sub_824269E8` (0x82426ED4): those are the six buffers with flags 0x8281, which `sub_824269E8`'s caller 0x82426B9C runs. Every type-9 buffer that runs and is not a clone is a HUD slot (flags 0x1): the first looks not made by the clone equal the HUD slots' first looks in every run (bench 444-529 of 2,357-2,664). The sources are command buffers of type 9 without the files' marker that never run themselves; who records them is open. **Re-recorded: not known.** The trace hashes a buffer's words every 64th run and does not restart the hash when a command buffer object is bound to another buffer (3,347-4,134 rebinds of clone objects a bench session), so its count of changes mixes reuse and rewriting and can miss a rewrite: clones (flags 0x281, 0xA81) changed 0-14 times a session in 2.5-6.7 M runs of each kind, the HUD slots hundreds of times. No measured scene damages, upgrades or repaints a car, which is what a re-recording would follow | first words found written by the clone's memcpy (0x82AA808C): bench 623-740 a session | game counters 6, 20, 28-30; `.lists` `changes` |
| 11 | Type 10 buffers, `sub_82D10908`, `sub_82CE9258` | **None of them runs in these scenes**: 0 command buffers of type 10, 0 calls of `sub_82D10908` (RunCommandBuffer's third caller), 0 of `sub_82CE9258` (the queued path's end) | 0 in every run | game counters 1, 15, 16 |
| 12 | Readers of SCRATCH_REG6/7 (and of SHADER_CONSTANT_FLUSH_FETCH) and of the EVENT_WRITE_EXT extents | **Nothing reads the marks.** SCRATCH_UMSK is 0x37 at the start of every buffer (bits 6 and 7 clear: scratch registers 6 and 7 are never written back to memory); no packet reads them; the CPU reads none of them through MMIO (its 2 register reads a frame are other registers); the emulator gives 0x5000-0x5002 no meaning. The extents: not what decides a list's tiles (row 6); whether guest code reads them is not closed | whole runs: UMSK 0x37 on every buffer start; register-reading packets: WAIT_REG_MEM on 0x0A31 (most), memory polls, REG_RMW on 0x1841 (once a frame) and once a session on 0x0081, 0x0082 and 0x1E4E, COND_WRITE on 0x1924 (7,808 a session), WAIT_REG_MEM on 0x1973 twice a session; none on 0x578-0x57F or 0x5000-0x5002 | `UMSK`, `READ`, `CPUREAD` |
| PR 1 | Live IM_LOAD address words keep their low two bits | **Yes**: every live IM_LOAD address word has 0 or 1 in its low bits (bits 2 and 3 never), and the keys (which keep those bits) are the files'. After a shader fixup the bits stay, but that rests on a few dozen checks, all in benchmark sessions: in free roam, the race start, photo mode and with `scene_msaa=off` no list was checked after a shader fixup (the fixups there hit command buffer objects being reused, which a first look resets). No key was ever checked on an untiled run (0 untiled checks in every run), so "the key is the same tiled and untiled" is not shown live | first looks: pass 4 pixel shader (1) first, vertex (0) second in every pair (bench 2,471-2,577 pairs a session); after a shader fixup: el-1 5 pass 4 pairs, el-4 4 pass 1 pairs, el-5 4 pass 4, 2 pass 0 and 3 pass 6 pairs, el-11 8 pass 4 pairs, attr 8 pass 4 pairs, race2x 1 pass 0 and 1 pass 6 pair, all keeping 1/0; other runs 0 | counter 13 (`when`); counter 11 (untiled bit) |
| PR 1 | Where area C's length is at run time | **At +0x80 of the header (words), as in the files; its address (+0x94) is 0 in memory for every Horizon model list**: area C is not kept. A converter at run time takes the lists without it, as the trace does; their keys then match the files' keys computed without area C | the files: +0x80 equal to the area C length in 996 of 996 sampled lists (offline, `tools/rmb_lists.py`); in memory +0x94 = 0 at every first look of every run | counter 10 |
| PR 1 | Can a list reach its first conversion untiled | **Yes, every pass 4 list with `scene_msaa=off` and with the two-tile scenario of the 12- and 16-car races**: its first run is for pass 5 or pass 3, already untiled. The lazy net at RunCommandBuffer must convert a retiled copy (`scene_tiling.cpp` keeps the headers) or the registry must convert at load | msaaoff: 543 of 543 pass 4 lists refused `untiled` at their first look; race2x: 1,010 of 1,010; passes 0, 1, 6 converted (msaaoff 836, race2x 1,097) | counter 8 |
| PR 1 | Does anything write a list's static words at run time | **Not in free roam (standing or driving), the race start, photo mode or with `scene_msaa=off`**: every key check agreed with the list's first look. **In four of seven benchmark sessions, yes:** one model's lists got another value in the first vertex constant words they load (dwords 4 and 5 in passes 0 and 6; dwords 64 and 65 in pass 4) after their first run, words no place names; who writes them is open. Command buffer objects are reused: an unloaded list's object later holds another list | key checks: stand 1,467,624, free 1,569,824, race 2,021,914, photo 1,429,628, msaaoff 408,294, all the same; bench el-1 14.0 M, el-4 14.6 M, el-8 14.0 M all the same, el-5 26,588 of 12.2 M differ (passes 0, 4, 6 of one model, from the change on), el-11 341 of 15.0 M (pass 4, dword 65); attr 996 of 2.27 M (one pass 4 list, dword 64); race2x 578 of 0.86 M (passes 0 and 6, dword 4). Reused objects: model lists 12-50 a session, clones 19-552 | counters 11, 12, 26, 27; the log's first changes |
| PR 1 | Bin select per tile; the bin mask when an untiled-pass list starts | **Selects 0x80000003, 0x0000000C, 0x00000030** (the first tile also has bit 31); the masks a pass 4 list starts with vary (row 6). Lists of passes 0 and 1 start with select and mask 0xFFFFFFFF. **Pass 6 lists mostly do, but not all:** some start with mask 0x8 or 0xA (0x2 with two tiles). Their draws pass (select and mask overlap), but a replay must take the live mask, not assume 0xFFFFFFFF | pass 6 starts with another mask: bench 1,682-8,377 of 5.1-5.5 M untiled-pass starts, free 2,757 of 603,083, race 883 of 873,304, race2x 47, attr 2,523; none in stand, photo, msaaoff | `BIN` |
| PR 1 | Template memory in a real session | 2.05 words of template a list dword, as offline (2.03-2.06 in every run that converts pass 4); a session converts every list it runs once | bench sessions 1,930-2,071 lists, 2.08-2.19 M template words (8.3-8.7 MB); free 1,848 lists, 2.27 M (9.1 MB) | counter 9 |

**What the answers mean for the next steps:**

- **Replay goes ahead (the plan's rule: list decode at least 15% of the decoder's time in the
  race or the 16-car race).** Model lists are 32-47% of the decoder's time in the buffers in every
  scene measured: 36% at the race start, 47% with the 16-car races' two-tile scenario, 32-43% in
  the benchmark, 34-40% in free roam (standing or driving), photo mode and with `scene_msaa=off`;
  pass 4 three quarters of it. **That share is the ceiling of replay, and replay will not reach
  it**: the decoder still has to put each list's register values into its register file, decide
  which changed and hand those to the recorder, issue the draws (1.8-2.3 runs a call) and the
  literal loads; replay saves the parsing of the packets, 2.3-2.6 ns of the decoder per list dword
  in the trace build.
- **PR 4's targets are re-based on PR 4's own baseline.** The milliseconds of this section are the
  trace build's wall times. PR 4 takes the lists' share from here (32-43% of the decoder's time in the buffers
  in the benchmark, 34-40% in free roam) and applies it to its own baseline: a build with the options
  off, the same scene and PC, four runs. On this PC the options-off decoder thread used 2.30-2.57 ms
  a frame in the benchmark (the 8 O runs above, mean 2.45), so half of the ceiling is about
  0.4-0.5 ms a frame there; free roam at 30 fps needs PR 4's own free-roam baseline. (The first
  version of this section derived 0.33 and 0.49 ms from trace times.)
- **Replay and elision:** **replay must elide.** A template's register runs handed to the recorder as the list
  writes them would add about 240,000 writes a frame in the benchmark. Five alternating pairs with
  the elision off for everything price them at 0.01-0.04 ms a frame of recorder time (by pair and
  by a fit against the window's content), but the 95% intervals reach 0.06-0.07 ms, past PR 4's
  "recorder CPU not up by more than 0.05 ms". So replay compares each value a template writes with
  the decoder's register file and hands the recorder only the changed ones, as the emulated path
  does. The compare costs the decoder about 0.15 ns a write (0.16 ms a frame less without it for
  all 1.07 M writes; about 0.04 ms for the lists' runs), which comes out of replay's saving.
- **Step 3's converter input differs from the files' in two ways the plan did not foresee:**
  1. **the flags**: in memory every model list has 0x101 set and 0x6 cleared by the loader, so
     the converter of PR 1 refuses every live list (`flags`). Converted with the flags put back in
     the files' form, `(flags & ~0x2101) | 0x6`, every live list converts and has its file key
     (every run: 16,998 of 16,998 first looks). Step 3 takes the flags that way, or its key leaves
     0x101 and 0x6 out the way it leaves 0x2000 out;
  2. **area C is not in memory** (+0x94 is 0): the key and the slots come from the header, the
     patch table and the PM4 alone, which PR 1 already showed gives the same slots for every
     Horizon list. The key then covers area C's length (+0x80) at most.
- **The lazy net needs the original words.** With `scene_msaa=off` (the Deck, VR and power-saving
  presets) and with the two-tile scenario of the 12- and 16-car races every pass 4 list first runs
  untiled (543 of 543 and 1,010 of 1,010), so a conversion at the first RunCommandBuffer refuses
  them. Step 3 either converts at model load or converts a copy retiled from the headers
  `scene_tiling.cpp` keeps (`g_untiled`). Nothing yet shows that a list's key is the same tiled
  and untiled in a running game (no untiled run was checked): step 3's shadow compare must.
- **The registry by physical address is right, and needs the unload:** a command buffer object is
  reused for another list after an unload (12-50 model list objects a session), so an object
  pointer alone is not a key, and a dropped list's PM4 address can carry another list later.
  `kOpListDrop` at unload, or a check of the length the INDIRECT_BUFFER packet carries against the
  registered one at every lookup (free: the IB has it).
- **Where the plan worried, the runs show no case:** in every scene, the standing free roam and
  the two-tile race included, every RunCommandBuffer is on the main device, never recorded into
  another buffer (mode 0x80), never copied inline (flag 0x20), never queued for the deferred
  context; there is no type 10 buffer and no call of `sub_82D10908`. The second context records
  the HUD and no IB to a list. Every list runs at depth 2. The fallback stays, but the shadow
  compare can treat any of these as "unexpected" and count it.
- **Tiling at replay:** a pass 4 list is read for each tile, and the predicate of its
  INDIRECT_BUFFER packet (D3D's bin mask, from the game's own predication) already decides which
  runs, before the decoder reaches the list. Replay works per run, with the live bin select and
  the live mask: the first tile's select is 0x80000003, not the 0x3 of PR 1's proof (the same
  result for every mask a list writes; D3D's own mask after a list, 0x80000000, matches only the
  first tile), and pass 6 lists sometimes start with mask 0x8, 0xA or 0x2, not 0xFFFFFFFF.
- **The marks can go:** nothing reads SCRATCH_REG6/7 or 0x5000-0x5002 (no packet, no CPU read, no
  write-back to memory), so a replay may drop them; the register file then differs from the
  emulated path only in registers nothing reads, which step 3's end-of-list compare must exclude.
- **Static words:** in free roam (standing and driving), the race start, photo mode and with
  `scene_msaa=off` no static word of a converted list changed in 6.9 million checks. In four of
  seven benchmark sessions one model's lists (passes 0, 4 and 6, or some of them) had their first
  vertex constant words changed after their first run, words no place names. So the plan's check
  of the static words on every run in step 3 is needed, and a list whose key no longer matches
  runs as PM4, as planned.
- **The clones are 12-32% of the dwords** (cars 37-54% of them) and up to a third of the decoder's
  buffer time (photo mode, one benchmark window). They are made by D3D's command buffer clone
  (`sub_82AA8220`) when the car presentation (`sub_82E98930`) sets an object up, copies of command
  buffers that never run themselves, with resolved addresses; a decode-once cache at the
  INDIRECT_BUFFER or a conversion at clone time is step 5's choice. Whether a clone is re-recorded
  when a car is damaged, upgraded or repainted was not measured.

**Still open:**

- **Who records the clones' sources** (command buffers of type 9 without the files' marker that
  never run): a hook on D3D's command buffer recording (begin and end on a recording device) or a
  write watchpoint on one source's first word at a car's load.
- **Whether clones are re-recorded** when a car is damaged, upgraded or repainted: a scene that
  does it, with a hash that restarts when a command buffer object is rebound.
- **Who writes the benchmark model's vertex constant words** (row "static words"): the trace can
  log the word's address at the first difference; a write watchpoint armed then names the writer.
- **Whether guest code reads the EVENT_WRITE_EXT extents** (row 12): a read watchpoint on one
  tiled call's entry, or a plugin that writes distinctive extents and a run that looks for a change
  in the bin masks.
- **The key of an untiled run** (row IM_LOAD): no key check ran on an untiled run; step 3's shadow
  compare covers it.
- **A real 16-car race** needs 16-car lobbies (#766); here its render scenario was measured on 8
  cars. **The GTX 1650 Ti and Windows** were not run in this step.
- **Plan questions 8, 9, 13 and 14** were not this step's: the load hook (step 3), fixups against
  run order while streaming (step 3's shadow compare), fixup rates in a 16-car race and the Big
  Shot (photo mode here), and which 256x256 pass is the reflection cube.

**Not done yet** (the rest of M4's list work, in order): the registry (conversion when a model
loads, a retiled copy for lists first seen untiled, invalidation by the fixup dispatcher and at
unload) with a shadow compare of every list draw in the running game; replay of the templates in
the command processor; then native passes, which also need M3's executor and a path for the
clones (the cars). FM4's `SET_CONSTANT2` tile blocks are a decision for after that census.

### M4, third step: the registry and the shadow compare (`native_lists=shadow`)

The game now converts the track's model lists while it runs and registers them with the GPU
plugin, and the plugin compares every run of a registered list with what its template says the
list does. **Nothing drawn changes:** every buffer is still decoded as before; the compare runs
beside it. `--native_lists=off` is the default (read once, at the start), and off the game and
the plugin do what they did (below).

**The game** (`src/hooks/native_lists.cpp`, the pure part `src/platform/native_list_registry.h`):

- At each RunCommandBuffer call (the hook in `cockpit_gauges.cpp`) of a model's list (a command
  buffer of type 9 with the files' marker after its patch table) on the game's D3D device (the
  device of the first such call: a call on any other is counted, `not registered: on another
  device`, and none was seen) that is not registered yet, the list is converted from a copy of its header, its patch table and its
  PM4, with the measurements of the second step taken in: the flags in the files' form
  (`FilesFormFlags`: `(flags & ~0x2101) | 0x6`, now in `native_list.h`), and no area C (it is not
  in memory: the key and the slots come from the header, the patch table and the PM4).
- **A list first seen untiled is converted from a retiled copy.** With `scene_msaa=2x|off` and in
  the game's 12- and 16-car races every scene pass list first runs for pass 3 or 5, already
  untiled; the copy gets the headers `scene_tiling.cpp` replaced put back (it keeps them per
  list, `SavedHeaders`), and only if every saved word is still that header's NOP (otherwise the
  list is refused as `retile`). Conversion at the model's load would have needed the loader's
  thread and timing, which nothing has measured; the first run comes after the load in stream
  order, and the retiled copy is the list as it was loaded.
- The template goes into a 4 MB ring of guest memory, once per key (lists with the same static
  words share it), with **`kOpListDefine`** (ReIGlue 0650): its id, address, words, a sequence
  number, a hash, and the address of an acknowledgement word, where the plugin stores the
  sequence number once it has read the template. The word is 64-byte aligned; the plugin stores
  it with release order after its last read of the template and the game loads it with acquire
  before it reuses the memory, so the pair orders the reads and the reuse on any CPU, not only on
  x86. A define whose sequence number is not newer than the last one acknowledged (a tiled replay
  decodes the same packet again) is dropped before the plugin reads anything, so a replay of a
  define that failed (hash, cap) cannot read ring memory the game has reused since; a define
  whose packet could not be written gives its ring memory and its sequence number back at once
  (PRs #934 and ReIGlue #68). The ring reuses only memory the plugin has
  acknowledged (a define the ring has no room for waits for the next call; a template larger than
  the whole ring is refused, `too-large`, rather than converted again at every call). This is
  not the plan's `kOpRef` ring of 0610: a `kOpRef` message holds at most 16,384 words and nobody
  acknowledges it, while a template can be larger and the game must know when it may reuse the
  memory.
- **`kOpListBind`**: the list's physical address and dwords, its template (or the converter's
  refusal with its code: a refused list is bound too, so its runs are counted), a state word,
  the command buffer, its patch table and the pass group being drawn (a scope in the track and
  procedural models' pass hooks, `scene_tiling.cpp`). The **state word** (one of 65,536 in a
  guest table) holds the list's generation: the fixup dispatcher's hook (`sub_82D19210`, moved
  here from the list trace, which keeps its counting) adds one before the fixup writes the list
  and one after, so the generation is odd while a fixup is under way (with release fences around
  the fixup's writes, a seqlock's writer; on x86 they only hold the compiler back).
- **`kOpListDrop`**: when a command buffer object holds another list now (its segment, length or
  patch table changed), and when a model is unloaded: `sub_82D19668` frees a model's headers
  (`+4`, `+16` bytes) and its PM4 (`+8`, `+20`), and every list whose command buffer is in the
  headers' range or whose PM4 is in the PM4's range is dropped at the next call (a model that
  shares another's data, `+0` set, frees nothing).
- The packets are host packets (ReIGlue 0610's NOP) written into the main device's command
  buffer just before the call's own words (`EmitHostPacket`, `native_device.cpp`), so a list is
  registered before the indirect buffer that runs it is decoded.

**The plugin** (ReIGlue 0650, `fh1_native_list.cpp`, the pure part `fh1_native_list.h` with a
copy of the template's readers): the three ops are handled on the decoder, as it reads them
(today's host packets go to the recorder). `ExecuteIndirectBuffer` looks the buffer's address
up; for a registered list:

- **the lookup's checks**, any failure counted with its reason: the indirect buffer's length is
  the registered one (`length`), the command buffer still names this buffer (`rebound`), and the
  list's static words still have the template's key (the key read again on every run, tiled or
  untiled). A key that differs is counted by when: **`key-at-bind`** when no key check since the
  list was bound found the template's key (the template does not describe the list it was bound
  for: a slot in the wrong place, a word the converter got wrong; never the game's doing, so it
  must be 0), **`static-words-changed`** when an earlier run since the bind had the template's key
  (the game wrote a static word after that run);
- **the pool**: the template's slots read from the live list when the state word's generation
  changed since the last run, as a replay would; read on every run too, and a pool that differs
  while the generation did not is counted (`stale`: a slot written without a fixup). The read is
  a seqlock's: the generation, the slots, the generation again (acquire fences between); an odd
  generation, or one that changed between the two reads, is a run during a fixup and is not
  compared (`fixup-in-progress`: a torn snapshot could disagree falsely, or in principle agree).
  The generation is read once more after the list's last packet, and a run a fixup started or
  ended in gets no verdict either (`fixup-during-run`, its draws left out of the counts): the
  fixup may have rewritten words the packets read;
- **every draw**, also one its predicate skips: the template's view of the state the draw starts
  with (the register file at the buffer's start, the list's writes up to the draw overlaid, the
  tile block applied for the **live** bin select and mask, the draws' own three registers) against
  the register file the packets produced, over what a draw's shaders and fixed function read
  (registers 0x2000-0x23FF, the ALU, fetch, bool and loop constants); the vertex and pixel
  shader loaded; the bin mask; whether the draw runs; and that it is the template's next draw;
- **the end of the list**: the whole register file except D3D's marks, the bin mask and the
  shaders. The marks (`SCRATCH_REG6/7`, 0x5000-0x5002) are left out: nothing reads them (no
  packet, no CPU read, no write-back to memory: second step, question 12), so a replay may drop
  them, and this compare does not hold a replay to them.
- **the registers written**, before each draw and after the last: the set of registers the
  packets wrote since the previous draw (the command processor's write paths report every write
  while a run is open: `PacketWriteRegister`, `PacketWriteRegistersFromMem`,
  `WriteRegisterRangeFromRing`, `Fh1WritePlainRangeFromRing`, COND_WRITE's register write)
  against the set the template writes there (the draw's delta, and the tile block's window when
  the block's packets are among the draw's), whatever the values. The values alone cannot see a
  template write whose value is already in the register file the run starts from: D3D's segment
  sets the selected tile's window before the list, so a tile block whose mask is another tile's
  (the block not applied for the live select) agreed by value; so would a write the template
  leaves out or puts on another register where both already hold the value. The registers a
  packet writes as part of what it does (the event initiator 0x21F9; a draw's DMA base, size and
  initiator 0x21FA-0x21FC) are left out of the sets, their values are compared as before, and so
  are the marks. Two tile blocks of the same draw where the later one overwrites the earlier
  still look the same in the template and in the packets, which is what a replay would do too.
- A lookup that finds nothing is counted with the other buffers, except at an address a list was
  bound at before (dropped since: `misses at a dropped list's address`, apart, so a drop decoded
  before its list's last run would show). A bind repeated with the same words is kept (the packet
  replayed for another tile); one that differs in anything, the pass group and the retiled flag
  included, replaces the binding. The plugin keeps at most 16 M template words (64 MB; past it a
  define is acknowledged and counted, `past the cap`, and its lists' runs are `no-template`).
- A summary every 600 frames: runs and draws compared and agreeing, by pass group; every run not
  compared by reason; the key checks tiled and untiled; generation changes and stale pools; the
  compare's own time. Self-checks, in the running game: `FH1_NATIVE_LISTS_PERTURB=N` changes, in
  every Nth run's template view, the value of the first register the list writes before its first
  draw; `FH1_NATIVE_LISTS_PERTURB_TILE=N` gives, in every Nth run of a tiled list, the first tile
  block of the template view the second tile's mask (the case the values alone could not see);
  `FH1_NATIVE_LISTS_PERTURB_SLOT=N` moves a one-word slot of every Nth template one dword on as the
  plugin reads it (a slot table one dword off).

Unit tests: `tests/native_list_registry_test.cpp` (game, CI) for the registry's state, the
template ring and its acknowledgements, the state words, the retiled copy (and its refusals),
and two lists whose templates are kept as a fixture (`tests/native_list_fixture.h`): the test
fails if the converter makes others. ReIGlue's `tests/fh1_native_list_test.cpp` runs the
plugin's readers, registry and compare on that same fixture beside a small interpreter of the
lists' packets: `Validate` takes both templates and refuses damaged ones (every single word of
both changed six ways, the readers run on each one it takes, under the sanitizers); the key the
same after fixups, with the loader's flags and untiled, another after a static word, a mask, a
table word or a shader's type bits changed; a define repeated, a bind repeated or replaced, a
lookup with a wrong length, an address dropped and bound to another list; and the compare agrees
for each tile's bin select (the first tile's with bit 31), the masks lists are seen to start with
(0xFFFFFFFF, 0x80000000, 0x8, 0xA, 0x2), a select outside every tile, and untiled, and
disagrees, in the field it should, for a register the template does not know, a stale pool, a
predicate the other way, draws out of order, and a template told untiled for a tiled list; by the
registers written alone (the values agreeing), for a tile block with another tile's mask while
the register file holds the selected tile's window, and for a template write moved to another
register where both hold the value; and the key check says `key-at-bind` for a template whose
slot is one dword off, from the first check, and `static-words-changed` only after a check that
matched (a bind replaced starts again).

**Acceptance [M].** Every scene of the second step, with `--native_lists=shadow`, the binaries
of this step: the game at `7e9f53bc` (this branch's code; the merges after it changed no file of
it) and the plugin at ReIGlue `6191326` (0650 with the registers-written check and the key
check's two reasons). RX 9060 XT, RADV; 1280x720 on a hidden display, no `forza.toml`, a copy of
the test save, Colorado, offline; two games at a time, so these are not timed runs. Frames are the
swaps the command processor decoded from the first registry packet on (the menus included); the
counts are those of the last summary of each run (every 600 frames). A run is one list's indirect
buffer run once (a scene pass list runs once per tile its INDIRECT_BUFFER packet is not predicated
off for: second step, question 6). The plugin's first version (`3c08ad4`, without the
registers written) agreed the same way in the same eight scenes (70.5 M runs, 150.0 M draws).

| Run | Scene | Frames | Runs compared (untiled) | Agree | Draws compared | Agree | Not compared | Key checks tiled / untiled, differing | Stale pools | Generation changes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |
| g-bench | the built-in benchmark race (8 cars), uncapped | 15,600 | 14,347,878 (0) | **100%** | 26,121,789 | **100%** | none | 14,347,878 / 0, 0 / 0 | 0 | 981 |
| g-race | race start at 30 fps: GoPro Millennia Masters, 8 cars, standing on the grid | 12,600 | 7,556,100 (0) | **100%** | 16,086,808 | **100%** | none | 7,556,100 / 0, 0 / 0 | 0 | 568 |
| g-free | free roam at 30 fps, driving (throttle held in pulses) | 11,400 | 5,746,150 (0) | **100%** | 13,272,203 | **100%** | none | 5,746,150 / 0, 0 / 0 | 0 | 640 |
| g-stand | free roam at 30 fps, standing at the spawn | 11,400 | 5,966,277 (0) | **100%** | 14,237,001 | **100%** | none | 5,966,277 / 0, 0 / 0 | 0 | 158 |
| g-photo | photo mode (TAKE PHOTO) at the spawn, 30 fps | 11,400 | 7,128,077 (0) | **100%** | 17,655,387 | **100%** | none | 7,128,077 / 0, 0 / 0 | 0 | 143 |
| g-msaaoff | free roam at 30 fps, driving, scene_msaa=off (one pass) | 11,400 | 3,547,865 (2,106,615) | **100%** | 11,133,852 | **100%** | none | 1,441,250 / 2,106,615, 0 / 0 | 0 | 1,626 |
| g-race2x | the benchmark race with scene_msaa=2x (the two-tile scenario of the 12- and 16-car races), uncapped | 15,000 | 11,998,360 (8,784,736) | **100%** | 22,481,112 | **100%** | none | 3,213,624 / 8,784,736, 0 / 0 | 0 | 982 |
| g-race2x30 | the same at 30 fps (a shorter run) | 5,400 | 4,315,498 (3,142,767) | **100%** | 8,173,270 | **100%** | none | 1,172,731 / 3,142,767, 0 / 0 | 0 | 979 |
| g-perturb | the benchmark race, uncapped, FH1_NATIVE_LISTS_PERTURB=1000 (a value) | 13,200 | 11,971,406 (0) | **99.9000%** | 22,267,936 | **99.9101%** | none | 11,971,406 / 0, 0 / 0 | 0 | 962 |
| g-tile | the benchmark race, uncapped, FH1_NATIVE_LISTS_PERTURB_TILE=1000 (a tile mask) | 15,600 | 13,503,266 (0) | **99.9805%** | 25,568,471 | **99.9897%** | none | 13,503,266 / 0, 0 / 0 | 0 | 1,091 |
| g-slot | the benchmark race, uncapped, FH1_NATIVE_LISTS_PERTURB_SLOT=4 (a slot one dword off) | 15,600 | 11,284,199 (0) | **100%** | 19,611,359 | **100%** | key-at-bind 2,493,282 | 13,777,481 / 0, 2,493,282 / 0 | 0 | 777 |
| g-tile2x | the benchmark race with scene_msaa=2x, FH1_NATIVE_LISTS_PERTURB_TILE=1000 | 16,200 | 13,001,686 (9,404,512) | **100%** | 24,634,010 | **100%** | none | 3,597,174 / 9,404,512, 0 / 0 | 0 | 951 |

**Again after the last fixes [M]** (the off-path seams, the seqlock read, the bind's group and
flag, the misses counted apart, the template cap; game `dc53a72c`, plugin ReIGlue `2ba5fd6`; the
same PC, two games at a time):

| Run | Scene | Frames | Runs compared (untiled) | Agree | Draws compared | Agree | Not compared | Misses at a dropped list's address | Runs during a fixup / stale |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| h-bench | the built-in benchmark race, uncapped | 16,200 | 14,940,493 (0) | **100%** | 27,249,409 | **100%** | none | 0 | 0 / 0 |
| h-race | race start at 30 fps, standing on the grid | 12,600 | 7,554,816 (0) | **100%** | 16,083,830 | **100%** | none | 0 | 0 / 0 |
| h-race2x | the benchmark race with `scene_msaa=2x`, uncapped | 16,200 | 11,636,213 (8,333,635) | **100%** | 23,232,207 | **100%** | none | 0 | 0 / 0 |

No define went past the cap (1.59 M template words at most), no list was refused, none ran on
another device.

**At the merged heads [M]** (the acknowledgement's release and acquire, a failed define's
reservation given back, replays dropped by sequence, a run during a fixup not compared, the
generation read again at the list's end; game `923db1b5`, plugin ReIGlue `c34ef49`; one game at a
time):

| Run | Scene | Frames | Runs compared (untiled) | Agree | Draws compared | Agree | Not compared | Fixup in progress / during the run | Fixups of bound lists |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| i-bench | the built-in benchmark race, uncapped | 15,000 | 12,371,399 (0) | **100%** | 23,664,824 | **100%** | none | 0 / 0 | 7,837 |
| i-race2x | the benchmark race with `scene_msaa=2x`, uncapped | 18,000 | 12,631,368 (8,651,795) | **100%** | 25,365,571 | **100%** | none | 0 / 0 | 7,665 |

No stale pool, no miss at a dropped list's address, no ring full; 1,354 and 677 defines were
replays (the same packet decoded again for another tile) and were dropped by their sequence
number, none failed its hash check. No run met a fixup at either read of the generation, so the
new skips are shown by the unit test only.

**On Windows [M]** (Vulkan, an RTX 5060 Ti, the benchmark race at 60 fps, plugin ReIGlue
`832648f`: the acknowledgement and the first read's skip, without the list end's read): with
`native_lists=off` and `shadow` the runs completed; in shadow mode, with the game's MSAA 5,032,933
runs and 9,564,595 draws compared, with `scene_msaa=2x` 4,170,565 runs (2,945,249 untiled) and
8,215,139 draws, every one agreeing; 12,142 fixups and 673 unloads in the two runs, no stale
pool, no ring full, no run during a fixup.

- **Every run compared agrees, and every draw**: 60.6 million runs (14.0 million of them untiled)
  and 129.2 million draws in the eight scene runs, on what the shaders read, the shaders, the bin
  mask, whether the draw runs, the registers written before each draw and after the last, and the
  whole register file at each list's end. The pass groups seen are 0 (the depth pre-pass), 3
  (pass 1), 12-14 (the scene pass's three layers) and 18 (the shadow maps); no list of pass 2
  (groups 6-8) ran in these scenes.
- **The untiled runs' key is the retiled template's (self-consistency):** with `scene_msaa=off`
  and `2x` the scene pass lists ran untiled 2.11 M, 8.78 M and 3.14 M times. On every one of them
  the key the plugin read from the untiled words (it reads a NOP as the tile-bound header it
  replaced) equalled the key of the template the game had converted from a retiled copy (845, 940
  and 965 lists): the two readings of the same list agree with each other. That is all these runs
  show. No run compared a retiled copy's key with the same list's key while it runs tiled, or with
  the files' keys (the second step's `FH1_LIST_KEYS` method), so that the retiled copy is the list
  as loaded rests on `Retile`'s checks (every saved word still that header's NOP) and its unit
  test. No key check differed in any scene run, so `key-at-bind` and `static-words-changed` are 0.
  The other lists of those runs (passes 0, 1 and 6) run as they were recorded.
- **Question 9 of the plan (a fixup against run order) [M]:** no pool was ever stale (a slot whose
  word changed while the generation did not: 0 in every run), and no run met a fixup under way (an
  odd generation, or one that moved during the slots' read or the run: 0). The generation changed
  143-1,626 times a run, each time the slots were read
  again: every slot write the game made went through the fixup dispatcher, as the plan read from
  the code.
- **The live bin values:** the compare starts from the bin select and mask the command processor
  has when the list starts (the second step saw the first tile's select 0x80000003, and pass 6
  lists starting with mask 0x8, 0xA or 0x2), and every run agrees with what it started from. No
  counter says which start masks these runs met, so that 0x8, 0xA and 0x2 in particular agree
  rests on the unit test, as the rewritten static words do (below). No draw inside a list
  was skipped by its predicate (0 in every run): after a tile block the mask is all ones, and the
  tiles a call skips are those whose INDIRECT_BUFFER packet is predicated off, which are not runs.
- **The self-checks**, each a benchmark race with one fault put into what the plugin reads:
  - **a value (g-perturb):** 11,971 runs had one register value of their template view changed,
    and exactly those 11,971 runs disagree (20,021 of their draws and 10,881 of their ends, all in
    the state registers); every other run agrees.
  - **a tile mask (g-tile):** 10,386 runs of tiled lists had their first tile block's mask made the
    second tile's; for 2,636 of them the live select is one no block of the view matches any more
    (the others select a tile whose own block still applies last, so the view does not change),
    and exactly those 2,636 runs disagree, each in one draw, in the registers written alone: the
    window values agree, since D3D's segment set the first tile's window before the list (the case
    the values alone could not see; the log names register 0x2080, written by the packets and not
    by the template). With `scene_msaa=2x` (g-tile2x) no run was perturbed: every scene pass
    list runs untiled there, and the lists that run tiled have no tile block.
  - **a slot one dword off (g-slot):** 349 templates had a one-word slot moved one dword on as the
    plugin read them; their lists' 2,493,282 runs were all counted `key-at-bind` (not compared),
    none `static-words-changed`, and every other run agrees.

| Run | Lists converted (from a retiled copy) | Refused | Templates sent (words) | Lists bound at the end | Dropped (unloads) | Not registered | Fixups of bound lists | Game hook, in all |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| g-bench | 1,976 (0) | 0 | 1,397 (1,582,037) | 926 | 1,050 (559) | none | 8,457 | 846.279 ms (0.07 us a call) |
| g-race | 769 (0) | 0 | 650 (803,368) | 717 | 52 (27) | none | 5,456 | 551.607 ms (0.08 us a call) |
| g-free | 1,472 (0) | 0 | 1,327 (1,905,567) | 469 | 1,003 (496) | none | 7,471 | 413.761 ms (0.08 us a call) |
| g-stand | 225 (0) | 0 | 193 (225,661) | 225 | 0 (0) | none | 1,344 | 614.962 ms (0.11 us a call) |
| g-photo | 334 (0) | 0 | 270 (318,231) | 334 | 0 (0) | none | 1,324 | 656.898 ms (0.09 us a call) |
| g-msaaoff | 2,180 (845) | 0 | 1,737 (2,165,912) | 863 | 1,317 (636) | none | 22,092 | 994.82 ms (0.18 us a call) |
| g-race2x | 2,000 (940) | 0 | 1,435 (1,598,583) | 865 | 1,135 (596) | none | 8,504 | 1011.702 ms (0.09 us a call) |
| g-race2x30 | 2,046 (965) | 0 | 1,396 (1,580,740) | 825 | 1,221 (634) | none | 8,722 | 666.878 ms (0.16 us a call) |
| g-perturb | 1,958 (0) | 0 | 1,403 (1,589,654) | 980 | 978 (523) | none | 8,213 | 1034.696 ms (0.1 us a call) |
| g-tile | 2,107 (0) | 0 | 1,437 (1,603,255) | 809 | 1,298 (671) | none | 9,133 | 857.547 ms (0.08 us a call) |
| g-slot | 2,064 (0) | 0 | 1,398 (1,583,921) | 809 | 1,255 (656) | none | 9,199 | 885.838 ms (0.08 us a call) |
| g-tile2x | 1,967 (922) | 0 | 1,410 (1,588,611) | 981 | 986 (531) | none | 8,426 | 993.123 ms (0.08 us a call) |

- The lists are converted at their first run on the render thread: 225-2,180 a session, 4.4-49.3
  ms in all. Templates are shared by key: 193-1,737 sent. A template goes into the ring once; no
  define ever waited for room, no packet lacked room in the command buffer, and no list ran on
  another device or into a recording one (as the second step found).
- **Drops:** every list an unload dropped was found by its command buffer in the freed headers'
  range (by the PM4's range alone: none). A drop packet is decoded again when its D3D segment is
  run again for another tile; the plugin then finds nothing to drop (368-370 in the benchmark
  races, 184-185 with `scene_msaa=2x`, none in the other runs).

**What is not compared, and why:**

- **Every buffer that is not a model list** (234-597 a frame: D3D's own segments, which hold the
  state flush in front of each list and D3D's draws; the clones, the cars and the objects drawn the
  same way; the HUD's slots). They are not registered: their path is the plan's step 5.
- **A run whose lookup check fails:** a length that is not the registered one, a command buffer
  that no longer names the buffer, a key that is not the template's (`key-at-bind` or
  `static-words-changed`, above), a list inside a list. **None happened in the scene runs.** The
  second step saw one benchmark model's first vertex constant words rewritten after their first
  run in four of seven benchmark sessions (trace builds); in the 16 benchmark race sessions of
  this step in shadow mode (the first version's eight and this version's eight, `scene_msaa=2x`
  and the self-checks among them) it did not happen. That such a run becomes a counted fallback
  (`static-words-changed`) and not a disagreement rests on the unit test (a static word changed
  after a check that matched) and on the code (the plugin counts and skips the run before any
  compare), not on a run. `key-at-bind` is what a template wrong from the start gives instead
  (g-slot above: every run of such a list, from the first, and never `static-words-changed`); it
  is a converter error and must stay 0.
- **A list the converter refuses:** none of Colorado's (0 refused in every run); it would be bound
  with its reason, and its runs counted by reason.
- **Lists the game did not register** (another device, a recording one, a full template ring, no
  room in the command buffer): none.
- **Inside a compared run,** D3D's marks at the list's end (above), and, at each draw, the
  registers outside 0x2000-0x23FF and the constants, which the end-of-list compare covers. The
  registers-written sets leave out the marks and the four registers a draw or event packet writes
  itself (0x21F9-0x21FC), whose values are compared.
- **FM4 circuits and the converted worlds** are not this step's: Colorado only. Their lists the
  converter does not know are refused (the first step's census), so they would be bound as refused
  and decoded as before.
- **The 16-car race** cannot be run offline (second step); its render scenario can, and is the
  `scene_msaa=2x` runs.

**What shadow mode costs [M]** (the same runs; the compare's own time on the decoder thread from
its clock, the game's hook from its own, both over each whole run):

| Run | Runs a frame | Draws a frame | Compare, us a run (start / draws / end) | Compare, ms a frame | Game hook, ms a frame |
| --- | ---: | ---: | --- | ---: | ---: |
| g-bench | 920 | 1,674 | 5.36 (2.77 / 1.39 / 1.20) | 4.93 | 0.054 |
| g-race | 600 | 1,277 | 5.97 (3.07 / 1.65 / 1.25) | 3.58 | 0.044 |
| g-free | 504 | 1,164 | 6.42 (3.31 / 1.85 / 1.27) | 3.24 | 0.036 |
| g-stand | 523 | 1,249 | 6.95 (3.64 / 2.01 / 1.31) | 3.64 | 0.054 |
| g-photo | 625 | 1,549 | 6.95 (3.53 / 2.02 / 1.41) | 4.35 | 0.058 |
| g-msaaoff | 311 | 977 | 11.06 (5.94 / 3.33 / 1.79) | 3.44 | 0.087 |
| g-race2x | 800 | 1,499 | 5.69 (2.96 / 1.48 / 1.25) | 4.55 | 0.067 |
| g-race2x30 | 799 | 1,514 | 6.63 (3.54 / 1.70 / 1.39) | 5.30 | 0.123 |

- **The decoder pays 3.2-5.3 ms a frame**, about 1.3 to 2 times its whole work without
  the compare (2.3-2.6 ms in the benchmark: second step). The start of a run is the largest part:
  a copy of the whole register file (80 KB) as the base of the template's view, and the key over
  the list's static words; the draws' part is the registers-written check's bookkeeping (it was
  0.5-0.9 us a run without it, 1.4-3.3 us now), the end compares the whole file again. That is the
  price of comparing everything a run could change; a replay needs none of it (it checks the
  generation, not the key, and writes instead of comparing). These runs shared the PC with other
  games, and the times are wall-clock times on the decoder thread, so they include its waits for
  the CPU: the first version measured 1.9-4.2 ms a frame in the same scenes.
- **The game's render thread pays 0.04-0.12 ms a frame** (the plan's limit for this step is 0.1
  ms): a lock and a lookup per RunCommandBuffer call (0.07-0.18 us), and the conversions, 4-49 ms
  in a whole session, at the lists' first runs. The game's code is the same as in the first
  version's runs, which measured 0.03-0.05 ms in the scenes they shared (0.073 ms in the shorter
  `scene_msaa=2x` run at 30 fps, whose 0.123 here is the only one above the limit): the hook's
  clock is wall-clock time on a loaded PC. Whether the hook stays within 0.1 ms with the PC to
  itself was not measured here; the worst single frame of a load was not measured either.
- **The frame rate, timed [M]:** the built-in benchmark, each run alone on the PC (no other game,
  the benchmark lock held), uncapped, `gpu_split_command_processor` on (this PC's default), three
  rounds in a rotated order, frame times and the decoder thread's CPU time over the same window
  of each run (game at this branch, `923db1b5`, plugin ReIGlue `c34ef49`; main: the game at main
  `1e651936` and ReIGlue main `18fa1cc`, built the same way):

  | Binaries | Benchmark fps (three rounds) | Mean | Decoder thread, ms a frame | Recorder thread, ms a frame |
  | --- | --- | ---: | ---: | ---: |
  | main | 173.2, 143.5, 163.1 | 159.9 | 2.62 | 4.83 |
  | this branch, `native_lists=off` | 176.0, 153.7, 156.0 | 161.9 | 2.63 | 4.84 |
  | this branch, `native_lists=shadow` | 102.4, 93.3, 86.6 | 94.1 | 8.27 | 8.00 |

  **Off is within the noise of main:** +1.2% fps and +0.3% decoder time, where the rounds of the
  same binaries spread by about 4% around their mean either side (143.5-173.2 fps for main
  alone). **Shadow is a test mode, and it costs:** -41% fps in the benchmark and 3.2 times the
  decoder's time a frame (the compare beside every list run, above), so it is for these
  measurements and the replay's checks, never for play.

**Off costs nothing measurable [M]** (the timing above), and the instructions it adds are these.
With `native_lists=off` the game sends no registry packet, and the plugin's registry is never
made: each seam in the command processor is a test of a null pointer (the write paths' seams too:
`fh1_list_reader_` is null outside a compared run), plus what the compiler lays out around it
(below: `ExecuteIndirectBuffer` runs about 8 instructions more per indirect buffer). Shown by
comparing the binaries, function by function, with main (the game at main's tree, ReIGlue main
`18fa1cc`) built the same way (instructions compared with addresses, call targets and padding
taken out, `objdump -d`):

| | Functions | The same | Changed | Added |
| --- | ---: | ---: | --- | ---: |
| Game | 85,620 / 85,678 | 85,611 | the hooks of RunCommandBuffer (`sub_824469B0`) and of the track's and the procedural models' scene pass (`sub_82466418`, `sub_82447B70`): each gains a load of the switch's state and two compares before its old body (6 instructions); one more instance of each of the three functions a string switch's definition makes (the new switch's) | 58: `native_lists.cpp`, the converter, `EmitHostPacket`, and the hooks of `sub_82D19210` and `sub_82D19668` |
| GPU plugin | 4,426 / 4,468 | 4,410 | the seams: `ExecuteIndirectBuffer`, `ExecutePacketType3` (two draw lines), `_NOP`, `_IM_LOAD`, `_XE_SWAP`, `Shutdown`, the constructor (the two new members); the write paths' seams (below): `PacketWriteRegister`, `PacketWriteRegistersFromMem`, `WriteRegisterRangeFromRing`, `_COND_WRITE`, and the three packet handlers `Fh1WritePlainRangeFromRing` is inlined into (`ExecutePacketType0`, `_SET_CONSTANT`, `_SET_CONSTANT2`); and two that only moved: `std::vector<uint32_t>::operator=`, which the plugin imported and now has a copy of, and a table address in the Vulkan swap's lambda added with `or` instead of `add` | 42: `fh1_native_list.cpp` and what it instantiates |

The game row is the first round's (game `7e9f53bc` against main's tree; its 6 instructions for
the scene pass hooks and its plain view of the two new hooks were too low, see the check at the
merged heads below). The fixes after the
reviews changed, in the game, only `native_lists.cpp`'s own functions (`OnRun`, `Register`) and
the fixup dispatcher's hook (`sub_82D19210`), whose path with the switch off is the same
instructions as before (the load of the switch's state, two compares, the call of the original);
the other differences between those two builds are main's (merged).

**Again at the merged heads, an independent check [M]** (game `923db1b5` against main
`1e651936`, plugin `c34ef49` against `18fa1cc`), the path with the switch off, instruction by
instruction:

- **Game:** the RunCommandBuffer hook (`sub_824469B0`) runs 6 instructions more (the switch's
  state loaded, two compares, two branches). The scene pass hooks run more than that: the track's
  (`TrackDrawPass`, `sub_82466418`) 26 and the procedural models' (`ProceduralDrawPass`,
  `sub_82447B70`) 18, as the compiler reads guest words there with four byte loads instead of a
  load and a `bswap` and the hooks lost their tail calls. `sub_82D19210` (the fixup dispatcher)
  and `sub_82D19668` (the unload) are plain functions on main; here each is a hook that tests the
  switch and calls the original, about 20-23 instructions more per call (a frame of five saved
  registers and the call). All are a few instructions per call of functions called at most a
  few thousand times a frame; the timing shows none of it.
- **GPU plugin:** `ExecuteIndirectBuffer` about 8 instructions more per indirect buffer (one more
  saved register, its two arguments kept for the lookup, the registry's null test before the loop
  and the result's test after it); the write paths, the NOP, IM_LOAD and swap handlers main's plus
  the test and a branch never taken.
- **aarch64** (the plugin's `command_processor.cpp` compiled for `aarch64-linux-gnu` with the
  same flags, main and this branch; CI also builds the whole SDK on aarch64 at the pin): it
  compiles, and there the off path is not only a test: `ExecuteIndirectBuffer` saves one register
  pair more (4 to 5 `stp`/`ldp`), `_SET_CONSTANT` one more (2 to 3) and `_SET_CONSTANT2` two more
  (2 to 4), since clang has no `preserve_most` there; the other functions keep main's frame and add
  the test (9-26 instructions more by count, mostly the cold blocks). Not timed on an aarch64 host.

**The write paths' seams, instruction by instruction [M].** These are the decoder's hottest
functions: every register write and range of the PM4 stream goes through them, and with
`gpu_split_command_processor` off (the default below six cores, where the decoder limits the
frame rate) `PacketWriteRegister` is on main four instructions (`cmp record_split_; je; mov; jmp
[vtable]`). The first fix round's seams (an inline null test whose call kept its arguments live)
cost more than the test: the compiler saved and restored registers on every call, so that path
became about 21 instructions (five pushes, moves, the test, moves, four pops, the tail jump), and
`Fh1WritePlainRangeFromRing` saved seven registers instead of four, before its ring-room check.
Now the call is out of line and cold (`Fh1ListNoteWrites`, with clang's `preserve_most` on
x86-64, hidden so it is a direct call, never through the PLT), `PacketWriteRegister`'s unsplit
path hands a write made during a run to a cold function as a tail call, the split paths test
after their first counter (so the compiler does not hoist the load above the split test), and
`Fh1WritePlainRangeFromRing` is main's again (its three call sites test the run and hand a range
to a cold function). Compared with main, registers' names taken out too (a renaming is no
difference):

| Function | Main, switch off | First fix round (`6191326`) | Now |
| --- | --- | --- | --- |
| `PacketWriteRegister`, unsplit | 4 instructions | about 21 (registers saved and restored) | main's 4 + `cmp fh1_list_reader_, 0` + `jne` (never taken) |
| `PacketWriteRegister`, split | | arguments moved into saved registers | main's + the test and branch |
| `PacketWriteRegistersFromMem` (each path), `WriteRegisterRangeFromRing`, `_COND_WRITE` | | 3-7 moves and the test | main's + the test and branch |
| `Fh1WritePlainRangeFromRing` | 92 instructions, 4 registers saved after the ring-room check | 7 saved, before the check | the same as main |
| `ExecutePacketType0`, `_SET_CONSTANT`, `_SET_CONSTANT2` (the plain range inlined) | | the same pattern inlined | main's + the test and branch; the cold call and the backend's fallback call (a range that is not plain) laid out at the end, and in `ExecutePacketType0` the one-register write loop's exit now jumps over that fallback (one jump once per such packet, one fewer for the fallback) |

The cold blocks only run with a run open (`native_lists=shadow`).

**Render test [M]:** `tools/render_test.py check` against the stored baselines, each run in a
fresh work folder (cold caches), with main's binaries (the game at main's tree, ReIGlue main
`18fa1cc`) beside this step's, interleaved over one hour while other games ran on the PC:

| Run (start) | Binaries | Scenes passing | Failing (structure difference / tolerance) |
| --- | --- | ---: | --- |
| main1 (15:52) | main | 6 of 6 | none (photo mode 0.0128 / 0.016) |
| off1 (15:54) | this step, `native_lists=off` | 2 of 6 | cockpit 0.0083 / 0.003, freeroam_dawn 0.0276 / 0.01, freeroam_midday 0.0415 / 0.005, photo mode 0.0620 / 0.016 |
| shadow1 (15:57) | this step, `shadow` | 6 of 6 | none |
| main2 (16:00) | main | 2 of 6 | cockpit 0.0086, freeroam_dawn 0.0201, freeroam_midday 0.0232, photo mode 0.0306 |
| off2 (16:22) | `off` | 4 of 6 | freeroam_midday 0.0058, photo mode 0.0195 |
| shadow2 (16:25) | `shadow` | 6 of 6 | none |
| main3 (16:28) | main | 6 of 6 | none (title 0.0148 / 0.015) |
| off3 (16:42) | `off` | 2 of 6 | cockpit 0.0042, freeroam_dawn 0.0140, freeroam_midday 0.0155, photo mode 0.0260 |
| shadow3 (16:45) | `shadow` | 6 of 6 | none |
| off4 (16:48) | `off` | 2 of 6 | cockpit 0.0090, freeroam_dawn 0.0246, freeroam_midday 0.0264, photo mode 0.0425 |
| main4 (16:50) | main | 2 of 6 | cockpit 0.0121, freeroam_dawn 0.0271, freeroam_midday 0.0368, photo mode 0.0557 |

Main's binaries fail the same four scenes, by the same amounts and in the same way as `off`
(mostly the foliage over the whole frame and a traffic car in the distance; not the player's
car), and in the same windows (main2 while another of this step's games was
loading, main4 right after off4): on this PC the test's spread is wider than its tolerances,
whichever binaries run, so it does not tell `off` from main. That `off` is main is shown by the
binary comparison above, not by this test. All three `shadow` runs passed; why shadow mode's
slower decoder lands inside the tolerances more often than main was not looked into. The first
version's single runs (6 of 6 each) are no evidence either way.

**Plan questions closed here:** 8 (the load hook: not needed; a list is converted at its first
run, from a retiled copy when it is untiled, and the unload is `sub_82D19668`), 9 (above). The
plan's row 3 asked for 0 static-hash mismatches (0), 100% of runs at registered addresses hit (all
model lists were registered at their first call; no lookup check failed) and a render thread cost
of at most 0.1 ms a frame (0.04-0.09 ms in seven of the eight scene runs, 0.12 in the shorter
`scene_msaa=2x` run on a loaded PC where the first version measured 0.07 with the same game code:
above).

**Left for the fourth step** (shadow mode only; with the switch off none of it runs):

- **Templates are never freed.** The plugin keeps every template it was sent until exit
  (0.2-2.2 M words, up to 8.7 MB, in these sessions), the game its key map (one small entry per
  key: a few MB even for all 51,633 of Colorado's keys). Freeing a template needs both sides to
  agree that its id is dead (a count of the lists bound to it on each side, and a tile replay of
  an old define must not bring it back after its ring memory was reused), which is the replay's
  protocol work. Until then the plugin caps what it keeps at 16 M words (64 MB, seven times the
  most measured) and counts a define past it.
- **A define the ring has no room for** converts its list again at the next call, until the
  plugin has acknowledged enough of the ring (`ring full`, 0 in every run; it costs conversion
  time only). A template that can never fit is refused (`too-large`), not retried. Replay keeps
  the converted template instead.
- **The binding's command buffer header and patch table are read from guest memory at every
  run,** and the model's unload frees that memory before the drop is decoded. On x86 that cannot
  fault (a guest page is at least a host page there, so freed guest memory stays mapped); on a
  host with 16 KB pages (the aarch64 work) a freed 4 KB page can become inaccessible, and a buffer
  of the same address and length run before the drop would read it. Before `native_lists` runs
  on such a host: check the binding's pages before reading, or hold the free until the drop is
  decoded.
- **The seqlock's fences** (release around the fixup in the game, acquire around the slots and
  before the list end's read in the plugin) and the acknowledgement's release and acquire have
  only run on x86, where they only hold the compiler back; on aarch64 they are what orders the
  reads (CI builds them there; not run).

**Still open:** the static-word change of the second step was not met, so the fallback for it is
shown by the unit test only; a real 16-car race; the GTX 1650 Ti (not run in this step); the
render thread's hook in shadow mode on a quiet PC; the off path timed on an aarch64 host. For the
fourth step, in the game's scene pass hooks: the guest word loads (four byte loads where a load
and a `bswap` would do) and the lost tail calls. Next: replay (the plan's step 4), which reuses this registry and the generations, and
whose double-run check can reuse this compare.

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

Since M4 the PM4 trace also counts each device's calls by function (`GD` lines in the report file;
the shared layout is version 3 on both sides) and, with `FH1_PM4_STATS_ALL=1`, writes every
foreign buffer (`F`), not only the 60 most read.

### List trace (game and SDK)

What the game's RunCommandBuffer calls run and what they cost the command processor, for M4's
model lists. A measurement build on both sides; off (the default) neither the game nor the plugin
has any of it:

- **Game:** `-DFORZA_LIST_TRACE=ON` (TU4, Linux) adds `src/debug/list_trace.cpp`. One line in the
  hooks of RunCommandBuffer (`sub_824469B0`, `cockpit_gauges.cpp`) and of `sub_824467A8`
  (`scene_tiling.cpp`), and a pass-group scope in the track's, the procedural models' and the
  car's draw functions (`FORZA_LIST_TRACE_CONTEXT`, `src/debug/list_trace.h`); the macros are
  empty in other builds. Per call it counts, into keyed counters in shared memory
  (`src/debug/list_trace_shm.h`): the device and its mode byte, the command buffer's type, flags
  and class (a model's list from the files, told by the files' marker after its patch table; any
  other command buffer of type 9, which D3D recorded; type 10), the pass group, whether the list
  is untiled, the caller, and whether `sub_824467A8` queued the list. It registers the buffers
  the call names by physical address. The first time a command buffer is seen, its header is
  classified and a model list is converted (`native_list.h`) from a copy, with its flags in the
  files' form; every later call compares the masks and flags with that first look and the live
  list's key with the template's. The fixup dispatcher (`sub_82D19210`, whose hook is
  `native_lists.cpp`'s: it calls the trace's `FORZA_LIST_TRACE_FIXUP`), `sub_82D10908` and
  `sub_82CE9258` are hooked to count their calls by type or caller, and D3D's command buffer clone
  (`sub_82AA8220`) and memcpy (`sub_82B3A7E0`) to tell who wrote a command buffer that is not a
  file list when it first runs. It costs every RunCommandBuffer call (a lookup and a hash of the
  list's static words) and every memcpy: never for players.
- **SDK:** ReIGlue built with `-DREXGLUE_FH1_LIST_TRACE=ON` (patch 0640). With
  `FH1_LIST_STATS=<file>` the command processor times every indirect buffer it runs (the time
  stamp counter around `ExecuteIndirectBuffer`; a run in which the decoder waited for the
  recorder is counted apart) and counts it by the class the game registered, pass group and
  depth: runs, dwords, the register writes the packets made and the ones handed to the recorder
  (the others were unchanged and elided), INDIRECT_BUFFER packets predicated off, the bin select
  and mask a list starts with, SCRATCH_UMSK. It also counts packets and CPU reads of the scratch
  registers and the decoder's CPU time in the ring. Every `FH1_LIST_STATS_EVERY` swaps (600) it
  appends a block with the game's counters, and `<file>.lists` holds every registered buffer's
  totals (calls, runs, runs predicated off, time).
- **Report:** `tools/list_trace_report.py <file> [--blocks A-B] [--json OUT]`. The time of the
  runs that waited for the recorder is taken at the mean of the others.
- `FH1_LIST_KEYS=<file>`: the key of every list converted at its first look; `FH1_LIST_CHECK_EVERY=N`
  checks a list's key on every Nth call only; `FH1_LIST_DUMP=N`: the first N command buffers of
  each kind word by word in the log.

The two can be combined with the D3D and PM4 traces (the fixup dispatcher's wrapper then opens
the D3D trace's scope). For times, use the list trace alone.

### Model list check (offline)

`tools/native_list_check.py` converts every model list of a track archive and proves the result
(M4, above). It reads the player's own files and writes a report of counts: no word of a list and
no member name.

```sh
tools/native_list_check.py <game>/media/tracks/colorado/bin.zip --jobs 3 --no-refusals \
    --expect-reduction 397,97,242,20018,1552 --json colorado.json
tools/native_list_check.py <game>/media/tracks/ui*/bin.zip       # several archives, one report
tools/native_list_check.py <archive> --distinct                  # one member per (CRC, size)
```

- `tools/rmb_lists.py` finds a model's lists (the 21 pass groups after its shader names, the
  three areas); `tools/native_list_check.cpp`, built by the script with `$CXX` or g++, is the
  census, the converter (`src/platform/native_list.h`) and the comparison with the interpreter
  (`tests/native_list_reference.h`).
- The archives' members are XMemCompress LZX: with `tools/unxmem` built (`tools/unxmem.c`) a full
  Colorado run takes under a minute; without it Python decodes, slowly.
- It also takes a DLC's track zip and the archive `tools/fm4_pvs.py` writes for an FM4 circuit.
- Exit status 1 when anything disagrees; `--no-refusals` makes a refused list a failure too.
  `tools/test_rmb_lists.py` runs it on a made-up model (CI).
- The checker keeps every part of a list in a block of exactly its size: built with
  `-O0 -fsanitize=address,undefined -D_GLIBCXX_SANITIZE_VECTOR` and passed with `--checker`, it
  also reports a read past a part (several minutes for Colorado's distinct members).

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
