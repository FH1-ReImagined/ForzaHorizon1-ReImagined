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
  surface fixups set at run time). One thing of a place stays in: the low two bits of a shader's
  microcode address, the shader type, which every fixup keeps.
- **The untiled form is the same template.** `scene_msaa=2x|off` and the game's 12- and 16-car
  races run the scene pass's lists with their tile-bound packets turned into NOPs
  (`src/hooks/scene_tiling.cpp`). The template records those packets' headers: the key takes a
  header or its NOP as the same word, and both views have an untiled mode that skips exactly those
  packets. A list is converted from its original words; one that is untiled at that moment is
  refused as input.
- **Everything outside what the files hold is refused, with a named reason** (25 of them, each
  with a test): another resource type or flag, more than one segment, a type-1 packet, another
  opcode, a register outside the set above, a tile block of another shape, a draw, shader load or
  literal load of another form, an address that is not a place, a place that lands on no known
  field, a place with sub-code 62 or 63 (the path through the table at `cb+112`, not decoded). A
  refused list runs as PM4, as today. And whatever is not refused must be right: the test also
  changes words of its lists at random, and every changed list that still converts has to agree
  with the interpreter (run by hand at 3 million changed lists, 1.58 million of them converted,
  and at 450,000 under the address and undefined-behaviour sanitizers).

**Offline proof.** `tools/native_list_check.py` (below) reads every model of an archive, converts
every list and runs both views beside a plain PM4 interpreter that knows nothing of templates
(`tests/native_list_reference.h`): the state before every draw (the whole register file the list
wrote, the bound shaders, the bin mask), every draw's arguments and whether its predicate lets it
run, and the state after the list. Each list runs for the bin select of each of the three tiles,
without tiling, and with a bin mask that excludes the bin; lists scene_msaa unties run all five
again untiled (untiled by `scene_tiling.h`'s own function); and everything runs twice, with the
words as they are in the file and with every place written with made-up values, as after the
game's fixups. Counts, from the game's files on the test PC (2026-11-14) **[M]**:

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
| Lists scene_msaa unties; their tile-bound packets equal to `scene_tiling.h`'s | 104,221; 104,221 | 15; 15 | 10,490; 10,490 | 0 |
| Runs beside the interpreter in which everything agrees (2 forms x 5 modes) | 4,106,390 of 4,106,390 | 410 of 410 | 410,260 of 410,260 | 32,870 of 32,870 |
| ... the same, untiled | 1,042,210 of 1,042,210 | 150 of 150 | 104,900 of 104,900 | none |
| Draws compared in those runs | 16,492,630 | 12,090 | 1,776,770 | 55,900 |
| Key the same after fixups / same template after fixups / with flag 0x2000 | 410,639 each | 41 each | 41,026 each | 3,287 each |
| Key the same for the untiled list; that list refused as input | 104,221; 104,221 | 15; 15 | 10,490; 10,490 | none |
| Another key after one changed PM4 word / table word / mask | 410,639 each | 41 each | 41,026 each | 3,287 each |
| Table entries with sub-code 62 or 63 | 0 of 29,224,048 | 0 of 122,827 | 0 of 3,454,427 | 0 of 255,519 |
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
  lists flagged 0x800 are refused as `tile-block`. Two lists write `RB_ALPHA_REF` (0x210E), outside
  the register set. The other 3,287 (depth pre-pass, the 256x256 passes, the one-pass scene)
  convert and agree. Six imported circuits together: 43,545 lists, 22,656 converted, 20,862
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

**Not done yet** (the rest of M4's list work, in order): a measurement build that answers what
only a run can (RunCommandBuffer calls by device and pass group, the live header masks, the time
the command processor spends inside list buffers); the registry (conversion when a model loads,
invalidation by the game's one fixup dispatcher) with a shadow compare of every list draw in the
running game; replay of the templates in the command processor; then native passes, which also
need M3's executor and a path for the command buffers D3D records at run time (the cars). FM4's
`SET_CONSTANT2` tile blocks are a decision for after that census.

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
