# Rendering issues

## Cross-scale minimap corruption at 3x

The scene and native HUD have separate texture caches when their integer render
scales differ. At 4K DLSS Quality, for example, the scene backing is 2x and the
HUD is 3x. `Fh1SynchronizeResolve` mirrors render-to-texture results between them.
The old transfer treated storage as repeated individual pixels, but the SDK
actually repeats 16-byte quads in column-major order. This scrambled roads and
icons in the minimap. Disabling `fh1_fast_resolve_copy` did not fix the underlying
byte-path mapping.

SDK patch 0100 resamples within those quads and preserves the resolve's endian
order. Whole-word loads remain enabled for compatible pixel layouts; the byte
fallback uses the same corrected address calculation. No game assets change.

The standalone Vulkan check executes the actual shader against independently
encoded dense image patterns, including 2x-to-3x and 3x-to-2x transfers, six endian
modes, five pixel sizes, offset bindings and destination guards. Both fast-copy
settings pass 1,470 cases each on Windows / RTX 5060 Ti. A pre-fix 2x-to-3x RGBA
control fails as expected. Run `tools/test_vulkan_cross_scale_resolve.py --sdk
/path/to/patched-sdk --fast-copy 1` (and `--fast-copy 0`); Python `vulkan` and
`glslangValidator` are required. A baseline source can be checked with
`--original-control --baseline-cpp /path/to/old/command_processor.cpp`.

The full Windows TU4 host and SDK build with Vulkan and D3D12 enabled. Matched
offline festival captures on RTX 5060 Ti / driver 617.14 reproduce the minimap
corruption before the fix and show clean road/icon edges and restored markers
afterward at 4K DLSS Quality (2x scene / 3x HUD). A live DLSS Quality to native
3x to native 2x sequence completes with visible scene shadows retained. This is
not a reproduction or a confirmed fix of the separately reported shadow failure.

A private TU4 fixture stimulates `CHudDrivingFeatsControl` through its native
feat method (`828A62A0`) on the UI update thread, with a synthetic label, score,
and icon selection. It awards no gameplay points. Captures retain the native
score, multiplier, label and icon through 1x, 2x and 3x, including live cache
replacement. The same simulated feat is visible at 4K DLSS Quality both before
and after the minimap correction, and at 4K FSR 3 Quality afterward. This does
not reproduce the missing-icon report or cover the gameplay event-generation
path. The fixture, binaries, copied profiles and captures remain outside Git;
neither test hook is part of patch 0100.

An additional 4K DLSS simulation selects native feat kinds 0, 1 and 2 explicitly.
Kinds 0/1 produce text-only presentations; kind 2 produces the combo icon and
backing. This agrees with `8289B968`, which updates `COMPONENT_COMBO_ICON` only
when the child control's kind at +0xF8 is 2. Event/layout selection is therefore
a separate lead for the missing-icon report; forcing every event to kind 2 is
not included as a fix. An affected gameplay event has not been captured yet.

## Minimap icon resolution

The minimap profile requests `MapIconSheetSmall.tga`: a 512x512 BC3 atlas
with 64x64 cells. The full-screen map already uses `MapIconSheet.tga`, a
1024x1024 atlas with 128x128 cells. Magnifying the small artwork makes arrows,
icon outlines and the NEW label visibly soft at higher render scales.

`src/hooks/minimap_icons.cpp` redirects only that small atlas name when the
native map symbolizer parses its Texture property. It removes `Small` from the
owned native string before resource lookup, so asynchronous loading and cache
keys use the full-size path. Language folders, normalized 8x8 UV cells, icon
sizes, placement and visibility are retained. The full atlas contains the
original full-screen-map artwork, including its more detailed borders/lettering.
No replacement images or game XML are shipped or modified.

The property callback is disc `82CE6CD8` / TU4 `82DA8EE0`; its texture string is
at object + 0xA8. Native string erase is disc `8247EF40` / TU4 `824AE5D8`.
Both versions' layouts were checked against the executables. Set
`FORZA_MINIMAP_HD_ICONS=0` before launch to compare the original atlas.

Private asset validation found matching large atlases for all 20 language
folders, and the 45 minimap / 61 full-screen-map atlas symbolizers use the same
normalized 8x8 layout. TU4 builds and disc hook compile checks pass on Windows
and Linux. Windows Vulkan checks on RTX 5060 Ti (617.14), using disposable
copied saves offline, cover 720p/1x, 1440p/2x and 4K/3x. The 1440p before/after
captures show sharper icons; opening the full-screen map and returning at 4K
keeps the correct icons. This changes source artwork, not the HUD render target
or presentation filter: a HUD rasterized at 720p and enlarged still has that
resolution limit. D3D12 and a full disc runtime were not tested.

## Barn-find minimap transparency (#111)

The minimap profile draws a barn-find region twice: an opaque white ellipse for
colour, then a colour-preserving ellipse with alpha 51/255. The map renderer uses
source-alpha factor ZERO to suppress alpha writes during the first pass, but
leaves the alpha equation at MAX. Host MAX blending ignores the factors: the
first pass writes alpha 1 and the following MAX(0.2, 1) keeps it opaque.

`src/hooks/map_alpha.cpp` intercepts only the native map renderer's ellipse
source-alpha state change. The colour pass uses ADD with ZERO/ONE to preserve
destination alpha; the separate alpha pass restores MAX. Roads and icons retain
their original equations. Both changes use the native state scope and unwind
with it. Game XML, textures and shader microcode are unchanged.

The state helper is disc `824679E0` / TU4 `82497228`, called at disc `8244B728` /
TU4 `8247ADE8`. At that call, r16 identifies the ellipse technique, state 0x54
selects the source-alpha factor and state 0x5C selects the alpha equation. Disc
and TU4 both set that ellipse flag and use the same state layout. Set
`FORZA_MAP_ALPHA_FIX=0` for a baseline comparison.

RenderDoc pixel history on Windows confirms the two ellipse passes leave alpha
0 then 0.2 with the fix, versus 1 then 1 without it. Later road pixels still reach
alpha 1. A private fixture registers a transient native barn-find region near
the player's car, using disposable copied saves; it does not unlock a rumor or
alter progression. The fixture, captures, game data and saves stay outside Git.

Full TU4 builds and before/after Vulkan runtime checks passed on Windows
(clang-cl 23, RTX 5060 Ti, driver 617.14) and Linux (clang 22, Fedora WSL/Xvfb,
Mesa llvmpipe LLVM 22). Both retain normal road/icon opacity and a correct
full-screen map. The disc hook compiled on Windows and passed Linux syntax
checks; a full disc runtime was not tested.

The successful Linux fixed run used a fresh shader cache and activated the test
region after free roam loaded. Several warm-cache loading attempts stopped in a
guest UI job wait, including a control with the correction disabled; their cause
was not isolated. RenderDoc pixel-history checks used a capture-compatible SDK
snapshot, separately from the installed-SDK Windows/Linux runtime checks.

## Fixed: hard-edged rectangle in the speed blur (2026-10-14)

**Symptom.** At speed the game blurs the edges of the screen. A rectangle around the
player's car was never blurred, and its edge was sharp; on the console the blur fades in
smoothly toward the car.

**Cause.** A shader constant set for the console's texture fetch, compared against the
host's exact value. Found with RenderDoc captures in the Millennia Masters race (TU4 build,
60 fps, full throttle, about 14 s after GO), analysed with qrenderdoc Python scripts (draw
list with shader hashes, targets and viewports; texture saves; the stencil state of every
draw; raw constant buffers).

- The scene is drawn 4x MSAA in three EDRAM tiles; the post-processing runs once at the end
  of the frame. Before the scene, in each tile, one rectangle-list draw marks a ground
  rectangle around the player's car with stencil 128 (depth test and write on, stencil
  always/replace, alpha test off). The scene's draws then replace the stencil with their
  object's value where they pass (0 for the world, 15 for the player's car, 3-24 for the
  other cars), so the rectangle keeps 128 on the ground around the car.
- Velocity pass (`PPMotionBlurDepthHorizonVel`, ucode `E68FE55ECD482EC5`): one full-screen
  rectangle into a 640x360 target. It reads the resolved depth and stencil as an 8888 texture
  (stencil in the low byte, float24 depth above it) and picks the reprojection matrix by
  the stencil: `stencil >= c253.w` means world (matrix c32-c35); otherwise object
  `stencil / 3`, matrix `c[32 + 4 * id]` (the player's car, 15, gets c52-c55, which
  follows the camera: zero velocity).
- `c253.w` is a literal of that pixel shader: 0x3F008100, 128.0019 / 255. The host fetch
  returns exactly 128 / 255 (0x3F008081) for a stencil of 128, which is below it. So 128
  counted as object 43, whose constants (c204-c207) happened to hold an identity matrix:
  the whole rectangle got zero velocity and stayed sharp, and the combine
  (`BloomTonemap_MotionBlur_Vignette`, five scene taps along the velocity) blurred
  everything around it. 0x3F008100 is (0x8080 + 1) / 65536: the value an 8-bit 128 gives
  if the fetch widens it to 16 bits and rounds up. That suggests the console's fetch
  returns a few ulps more than 128 / 255 and the rectangle counts as world there. An
  object index of 43 means nothing in this table either way.
- The other passes were checked and ruled out: no scissor, viewport, mask texture or
  rectangle on the blur draws; the velocity is filtered linearly; the 2-tap pass after the
  velocity (`ECE830AC0333767F`) only copies the pixel below at depth edges.

**Fix.** `src/platform/motion_blur_fix.cpp` finds the shader's literal block (c252-c255)
in the executable's image before the game starts and sets the threshold to 0.5 (between
127 and 128). It is found the same way in the disc's executable and Title Update 4's
(0x821C30F0 in TU4). `FORZA_BLUR_THRESHOLD=0` leaves the image alone, for comparisons.
The blur now fades toward the car, and the fence and the road beside it blur as on the
console. GPU work is unchanged (one constant), so the frame rate is unchanged.

**Not done.** Xenia was not compared: on Linux it only takes pad input, and a global
virtual pad is not allowed. Other shaders that compare 8-bit values against exact
thresholds may show the same off-by-ulps difference; none is known.

**What the code shows (sub_ addresses of the disc executable).**
- `sub_82461108` draws the technique `PPMotionBlurDepthHorizonVel` (a velocity pass from
  depth): a full-screen quad given as normalized shorts (-32767..32767).
- `sub_82402678` uses `BloomTonemap_MotionBlur_Vignette`, probably the final combine that
  applies the blur.
- The camera definitions set a `MotionBlurAmount` per shot (`media/camera.zip`, for
  example `321Cams.xml`).


## Screening for driver-dependent bugs (#34, 2026-10-15)

Issue #34 (Windows 11, RTX 3080) reported flicker and greenish reflections on Direct3D 12
(#46), flicker and coloured artifacts at render scale 3 (#47), and traffic cars that blow
out into a cyan glow (#48), "mainly on NVIDIA". The test machine has a Radeon (RADV), so
these checks look for behaviour that depends on the driver: if RADV, lavapipe and
vkd3d-proton all agree and the validation layers are quiet, the bug is in what one driver
does with an undefined case.

**What was run (free roam, Xenia save copy, 2-3 min of driving, screenshots every 2 s):**

| Check | Result |
| --- | --- |
| Vulkan, RADV, scale 1, 3, 4 | clean |
| Vulkan, lavapipe (CPU), scale 1 | same picture as RADV |
| Direct3D 12 (Windows build, vkd3d-proton on RADV), scale 1 and 3 | clean, no green tint |
| Validation layers + synchronization validation, RADV | one hazard, harmless (below) |
| Every new color render target filled with magenta first (scale 3) | no magenta anywhere: the game does not read render target memory it never wrote |
| NaN / Inf / values above 31.875 in the float targets, after every 20th draw of a captured frame | none; the 7e3 targets peak at exactly 31.875 |

The one validation report is `SYNC-HAZARD-WRITE-AFTER-WRITE` between two `vkCmdCopyBuffer`
into the same readback buffer in consecutive submissions with no barrier
(`IssueCopy_ReadbackResolvePath` with `readback_resolve = some`: the copy is recorded for every
resolve, the CPU reads only on a cache miss, after waiting for the GPU). Formally a race, in
practice the same bytes are written twice; not a cause of the reports. The other report,
"fragment shader writes Location 0 with no attachment", is a performance warning.

**A likely mechanism, not confirmed.** On the host render target path (the default on every
vendor but Intel, Vulkan and D3D12), pixel shader colours go to the float16 targets that stand
in for the console's 7e3 format **unclamped**. The console clamps to [0, 31.875] and turns NaN
into 0 before blending; the SDK's interlock path does the same, the host path does not. A
shader that gives Inf, NaN or a huge value on one driver (denormals, `0 * inf`, `log(0)`)
then feeds the bloom on that driver only. Clamping there would match the console on every
driver; it needs a capture from an affected machine first to see which draw.

### How to run the checks

- **Validation layers** (user install, no root): `dnf download vulkan-validation-layers`,
  unpack with `rpm2cpio ... | cpio -idm` into `~/.local/opt/vvl`, and copy
  `usr/share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json` into
  `~/.local/opt/vvl/layers/` with `library_path` set to the absolute path of
  `usr/lib64/libVkLayer_khronos_validation.so`. Then run the game (`tools/live_session.sh`
  passes the environment through) with
  `VK_ADD_LAYER_PATH=~/.local/opt/vvl/layers VK_LOADER_LAYERS_ENABLE='*khronos_validation'
  VK_KHRONOS_VALIDATION_VALIDATE_SYNC=true VK_KHRONOS_VALIDATION_REPORT_FLAGS=error,warn
  VK_KHRONOS_VALIDATION_DEBUG_ACTION=VK_DBG_LAYER_ACTION_LOG_MSG
  VK_KHRONOS_VALIDATION_LOG_FILENAME=/path/vvl.txt
  VK_KHRONOS_VALIDATION_DUPLICATE_MESSAGE_LIMIT=3` and `--vulkan_log_debug_messages=false`
  (keeps forza.log small). Free roam stays playable with synchronization validation on.
  GPU-assisted validation (`VK_KHRONOS_VALIDATION_GPUAV_ENABLE=true`) is much slower.
- **lavapipe:** `VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json`. The game
  runs at about 10 guest fps and 2-3 presented fps, so scripted inputs need three times the
  waits (the title screen takes 60 s, loading into free roam about 4 min), and a screenshot
  trigger should be touched without waiting for the file. A RenderDoc capture made on RADV
  does not replay on lavapipe ("tried to allocate memory from index 3, but on replay we only
  have 1 memory types"), so compare screenshots of the same place instead.
- **Direct3D 12 without Windows:** `tools/build_windows.sh`, then from the package folder
  `WINEPREFIX=<scratch prefix> GAMEID=0 PROTONPATH=GE-Proton11-1 umu-run ./forza.exe
  --gpu_backend=d3d12 --game_data_root=Z:\...` (Windows paths under `Z:`). The input FIFO is
  POSIX-only: use the `FORZA_AUTOPRESS="8:START,10:START,...,95:RT:40"` schedule. Set
  `FORZA_FRAMEDUMP_DIR` (a `Z:` path) or screenshots land in `/tmp`.
- **Uninitialized render targets:** `--vulkan_render_target_initial_fill=2` (SDK patch 0042)
  fills every new color render target with magenta (64, 0, 64, 1) before its first use; any
  magenta on screen is render target memory the game reads without writing, which is zero on
  some drivers and old data on others. `=1` fills with zero. Each fill is logged
  ("filled new render target", first 64).
- **D3D12 fence completion ([SDK #13](https://github.com/FH1-ReImagined/ReIGlue/issues/13)):**
  the SDK pin includes [SDK PR #14](https://github.com/FH1-ReImagined/ReIGlue/pull/14),
  which checks both the queue signal and event-registration HRESULT before waiting for
  out-of-submission operations. The original boolean check could skip registration and
  wait indefinitely. The actual production branch passes 32 CPU mock cases; the full
  translation unit passes a syntax check. No D3D12 device or Rt.25 map traversal was tested,
  so this correction does not establish the cause of the reported location-specific hitch.
- **NaN / Inf / out of range per draw:** capture a frame (`renderdoccmd capture -w -c DIR/cap
  ./forza ...` with `VK_ADD_IMPLICIT_LAYER_PATH=~/renderdoc/layer`, so the RenderDoc layer is
  found, and `FORZA_RDCAPTURE_TRIGGER`), then
  `RDC_CAP=cap.rdc RDC_OUT=DIR qrenderdoc --python tools/rdc_float_scan.py`: the first draw that
  leaves NaN, Inf or more than 31.875 in each float target, with texel positions. For a blown-out
  object, take RenderDoc's pixel history on it from there.
- **RenderDoc startup (#511):** the SDK connects only to a RenderDoc library that is already
  loaded into the process. Installing RenderDoc alone does not activate it; launch through
  RenderDoc when a capture is intended. ReIGlue 0407 uses a counted `RTLD_NOLOAD` probe on
  Linux and `GetModuleHandleExW` on Windows; macOS capture remains unsupported. The small
  real-library fixture passes on Linux and Windows, including reference lifetime and final
  unload. This is loader validation, not a new interactive capture qualification. The approach
  follows Pinyon Shift and retains its BSD notice in ReIGlue.
- **A colour on screen:** a short script over the screenshots (Pillow) that counts saturated
  pixels of the reported colour (cyan: G and B above 215, R 70 below G) finds the frames to look at;
  check the hits, the radio HUD's logo is cyan too.

Xenia Canary was not compared: on Linux it takes pad input only, and no global virtual pad is
allowed on the test machine.


## Upscaling (#44, 2026-10-15)

**What the launcher offered did nothing.** `present_effect` listed bilinear, cas, fsr, fsr2 and
fsr3, but the nightly SDK (and so our runtime) is built without the `REXGLUE_ENABLE_FIDELITYFX`
option: the cvar allowed only `bilinear` and the other values were rejected with a log warning.
SDK patch 0043 builds the spatial effects in without the FidelityFX library (their shaders are
precompiled in the SDK source), on Vulkan and Direct3D 12: `present_effect` = `bilinear`, `cas`
(sharpen, up to 2x) or `fsr` (AMD FSR 1: EASU upscaling, repeated up to 4 times for scales over
2x, then RCAS). Not used: `present_cas_additional_sharpness`, `present_fsr_sharpness_reduction`
(default 0.2 stops) stay at their defaults.

**fsr2 / fsr3 are not available, and would not work as written.** They need the FidelityFX
runtime (Linux also needs its shaders precompiled by hand), which the SDK does not ship. The
SDK's temporal path (`vulkan_presenter.cpp`, `DispatchTemporalUpscaler`) passes the colour
image as the depth buffer and as the motion vectors, a jitter of 0, a fixed camera
(near 0.1, far 1000, fov 60) and `reset = true` on every frame: no history is ever used, so
it is a spatial upscaler with wrong inputs. The launcher no longer lists them (a stored value
becomes `fsr`); the cvar rejects them without the runtime.

**Measurements** (Linux, Vulkan, RADV, RX 9060 XT; free roam at the spawn, car standing,
`frame_rate = 60`, 4 s windows of the last 30 s; headless gamescope gives the output size;
screenshots of the final output). CONTENDED: two other game instances ran the whole time
(GPU 50-60 % busy without ours), so absolute numbers are low-confidence; the ordering is clear.

| Output | Render scale | Filter | fps | p95 ms | Sharpness (banner edge energy) |
| --- | --- | --- | --- | --- | --- |
| 1080p | 1 | CAS | 59.9 | 19.1 | 29.8 |
| 1080p | 1 | FSR 1 | 59.9 | 18.4 | 38.6 |
| 1080p | 2 | bilinear | 41.9 | 28.9 | 35.4 |
| 1440p | 1 | bilinear | 60.0 | 18.2 | 15.9 |
| 1440p | 1 | FSR 1 | 59.9 | 18.4 | 28.5 |
| 1440p | 2 | bilinear | 42.5 | 29.2 | 35.9 |
| 1440p | 3 | bilinear | 26.0 | 47.2 (p99 124) | 35.4 |
| 4K | 1 | bilinear | 59.9 | 17.9 | 8.7 |
| 4K | 1 | FSR 1 | 60.0 | 18.3 | 16.8 |
| 4K | 2 | bilinear | 41.9 | 29.2 | 16.5 |
| 4K | 2 | FSR 1 | 41.9 | 29.6 | n/a |
| 4K | 3 | bilinear | 28.0 | 42.6 (p99 104) | n/a |

- FSR 1 at scale 1 costs about nothing in fps (GPU busy +0-11 points at 4K, contended) and
  recovers about two thirds of the edge sharpness that a native 2x render has; text and
  outlines get crisp. It cannot invent detail: the 2x render shows texture detail (grilles,
  small print) that FSR 1 does not, and flat textures can look painted.
- Scale 2 is real detail for about 30 % fewer fps here; scale 3 halves the frame rate and
  breaks the 60 fps target (p99 above 100 ms). FSR on top of scale 2 changes little.
- The scene was not identical between runs (the spawn view and the traffic vary), so the
  sharpness numbers come from the screen-space "Millennia Masters" banner, which is the same
  in every run; RMS differences between runs are not comparable.

**Defaults and advice.** First-run launcher default: `present_effect = fsr` at render scale 1
(a stored choice is kept). 1080p and above: FSR 1 at scale 1; a fast GPU can take scale 2 for
detail (expect about 40 fps at a 60 fps target; 30 fps is fine); stay at or below 2. Not
tried: guest video modes below 720p for slow GPUs (Steam Deck, #45).

**Temporal upscaling (FSR 2/3, DLSS): feasible, large.** Done since: native DLSS (#60,
[native_dlss.md](native_dlss.md)) and AMD FSR 3 on the same inputs
([below](#temporal-upscaling-with-amd-fsr-3-51-2026-10-16)). The notes that follow are the
original assessment.
- Depth: the main scene's depth is resolved to a texture for the velocity pass; the Vulkan
  render target cache could export that image to the presenter (new plugin-to-presenter
  interface; the scene pass is found by its shader or target).
- Motion vectors: the game's velocity pass `PPMotionBlurDepthHorizonVel` writes camera and
  per-object (stencil id) reprojection velocity into a 640x360 target, only while motion blur
  is on and scaled by `MotionBlurAmount`; it needs decoding to FSR's convention and an upsample.
  Wheels and the player's car (zero velocity, camera-relative) are fine; transparencies are not.
- Jitter: the emulator owns the viewport transform (`ndc_offset` in `graphics/util/draw.cpp`),
  so a sub-pixel jitter can be added to the scene draws without the game, if the scene targets
  are identified so the post-processing and HUD are not jittered.
- The catch: a presenter-side upscaler sees the final image (tonemapped, motion-blurred, HUD
  on top); ghosting of HUD elements needs a reactive mask. Doing it before the post passes
  means one render scale for scene and post targets, which the SDK does not separate.
  Also needs the FidelityFX runtime built (shaders on Linux) and 60 fps or more to look good.
- DLSS needs NVIDIA's SDK and an NVIDIA GPU; not tested here (AMD only).


## Temporal upscaling with AMD FSR 3 (#51, 2026-10-16)

Settings > Display > Upscaling filter > **AMD FSR 3 (temporal)**, with **FSR 3 quality**
(Quality, Balanced, Performance, Native AA) and **FSR 3 output resolution** (1080p, 1440p, 4K).
Any GPU with Vulkan; on NVIDIA with DLSS on, DLSS wins. It is the FidelityFX FSR 3.1 upscaler
only (no frame generation), as a second backend behind the plumbing DLSS added (#60,
[native_dlss.md](native_dlss.md)): the scene renders at the mode's input size into its own
render target cache, the HUD, menus and labels render natively at the output size, and the
reconstruction runs between the two.

**Inputs (shared with DLSS).** Colour: the tonemapped scene (LDR, so no HDR flag, exposure 1).
Depth: the game's resolved depth/stencil (inverted). Motion vectors: reconstructed from the
constants of `PPMotionBlurDepthHorizonVel` (camera and per-object reprojection, in render
pixels, current to previous, jitter-free). Jitter: the Halton(2,3) sequence on main-scene
draws only, as for DLSS, over FSR's recommended phase count (`8 x ratio^2`: 32 for
Performance, 18 for Quality, 8 for Native AA; DLSS keeps 16). Reset: the frame after one
without the motion blur pass (cuts, menus, loading), like DLSS. FSR's view-space depth uses the
game's own depth reconstruction (`z = B / (1 - A - depth)` from the same constants); the field
of view is not exposed there, so 60 degrees vertical stands in (it only scales FSR's
disocclusion thresholds). No reactive or transparency mask (FSR's defaults); RCAS
sharpening 0.4 after accumulation (`FORZA_FSR3_SHARPNESS`, 0-1, negative turns it off).

**How FSR 3 gets in (route).** SDK patch 0072 fetches the FidelityFX SDK v1.1.4 GPU sources
(MIT) at configure time, pinned by commit and SHA-256 per file (31 files, the way 0046 fetches
the NGX headers; `REXGLUE_FSR3_SDK` takes a local checkout, `REXGLUE_USE_FSR3=OFF` builds
without), and embeds the GLSL passes and their headers in the GPU library. The SDK's own Vulkan
backend (`ffx_vk.cpp`, 230 KB) and its prebuilt permutation blobs (built by a Windows-only
shader compiler) are not used: the GPU library already links glslang (the DLSS motion-vector
pass is compiled with it), so the renderer compiles the one permutation it needs at start-up
(63-355 ms on this machine) and a compact host dispatcher (`fh1_fsr3.cpp`) replays
`ffx_fsr3upscaler.cpp`'s sequence: prepare inputs, luma pyramid, shading change pyramid, shading
change, prepare reactivity, luma instability, accumulate, RCAS, with the SDK's ping-pong
history, clears and constants. Bindings come from glslang's reflection of each pass. The
permutation: LDR input, render-size motion vectors, no jitter in the motion vectors, inverted
depth, no FP16 and no wave operations, so it runs on any Vulkan 1.0 GPU. FSR 3.1 rather than
FSR 2.2 because its GLSL is published the same way and it ghosts less.

**Fallback.** If the passes cannot be built (a missing storage format, a shader error) the
renderer logs `FSR 3: upscaler unavailable; this session scales the scene bilinearly` once and
keeps the scene/UI split with a bilinear scene scale (tested with `FORZA_DLSS_TEST_FAILURE=create`:
the game reached the menus). If the split itself cannot start (an incompatible render scale or
render-target path) the game renders natively and presents with FSR 1, which the launcher keeps
as `present_effect` for that reason.

**Measurements** (Linux, Vulkan, RADV, RX 9060 XT, TU4, the race scenario
`tools/perf_scenario.sh race` on a copy of the Xenia save, warm cache, 1440p window).

| Path | Scene | HUD | `frame_rate` | fps | p50 / p95 ms |
| --- | --- | --- | --- | --- | --- |
| FSR 1, render scale 1 | 1280x720 | 720p, upscaled | 144 | 97.2 | 10.5 / 13.8 |
| FSR 3 Performance | 1280x720 | native 1440p | 144 | 91.5 | 10.7 / 13.9 |
| FSR 3 Performance | 1280x720 | native 1440p | 60 | 59.7 | 16.8 / 17.1 |

The FSR 3 run at 144 started with the previous run's load average (2.35, CONTENDED by the
script's rule; no other game ran). About 6 % fewer frames at an uncapped rate for the same
720p scene: FSR 3's passes plus the native 1440p HUD; a 60 fps target holds. Quality (1706x960
input) renders the scene raster at 2x and resamples, like DLSS's between-scale modes, so it
costs about what render scale 2 does.

**Never below native (#110).** The game's scene raster is 1280x720; a player log showed FSR 3
Performance at a 1920x1080 output (input 960x540) ending in a guest access violation (read of
guest 0x4) about 20 s in. It did not reproduce on AMD/Linux (Mesa: free roam for 3 minutes at 1080p
Performance, 60 fps), but the input is now clamped on every path (patch 0073, FSR 3 and DLSS): each
dimension is raised to at least 1280x720 (and kept within the output), logged once as `FH1 <mode>:
input 960x540 is below the native 1280x720; using 1280x720`. At a 1080p output FSR 3 Performance
and Balanced therefore run a 1280x720 input (Quality 1280x720 as before); 1440p and 4K are
unchanged. DLSS 1080p Performance asked NGX for 960x540 and had the same hole.

**Startup movies at a 1080p output (#539).** With the scene/HUD split at a 1080p output (FSR 3
in every mode and SMAA 1x; DLSS and DLAA take the same path, not tested) the movies showed only their
top-left 75%, enlarged, and the legal screen before them filled only the top-left 1280x720.
1440p and 4K were not affected. The HUD target's raster at 1080p is 1920x1080 of the 2560x1440
that render scale 2 gives, and the HUD's shaders scale the coordinates of every scaled screen
texture to that part. A movie frame is decoded into a target in the scene, whose raster fills
the whole surface, and a HUD draw then puts it on screen. ReIGlue 0409 records for each resolve
whether it came from the scene and leaves those textures' coordinates unscaled. It also presents
a frame without the HUD layer (the legal screen, unscaled; a frame between two movies) at its own
size, letterboxed, instead of the output size. Such a size change replaces the presenter's output
images; the log lists the first 16 (`FH1 split: frame N presents WxH ...`). A boot to free roam
at 1080p has 8 or 9, at the legal screen, between movies and at loading, never one a frame. Checked
with the startup movies on (TU4, RADV, in a 1280x720 window): FSR 3 Native AA, FSR 3 Quality and
SMAA 1x now frame the movies and the legal screen as game default does. FSR 3 Native AA at
1440p and game default are unchanged. The menus, the HUD in free roam and the pause menu still
present at the output size.

Quality, from the race screenshots (FSR 3 output at 1440p against the 720p FSR 1 input
enlarged with Lanczos as a stand-in for EASU, so the FSR 1 side is if anything kinder than
the real filter): the player car's panel lines and the engine cover slats are continuous with
FSR 3 where the 720p image stair-steps them; the speedometer, labels and race text are native
1440p (the FSR 1 path enlarges the 720p HUD). No ghost trails on the cars or around the HUD in
the 13 shots taken during the race (the HUD is composited after the upscale, so it cannot
ghost); stills 1.6 s apart do not show shimmering either way. Not covered: transparencies
(no reactive mask yet), rain and night, camera cuts beyond the race start.

## NVIDIA car reflection sampling (2026-10-15)

### Fractional display raster and off-screen views (#330)

Patch 0150 applies the existing display-viewport classification to fractional
raster scaling as well. A screen pitch alone is insufficient: FH1 also uses
screen-pitched EDRAM for square reflection faces and shadow maps. These passes
need integer viewport, scissor and VPOS scaling to match resolve storage.
Display and cinematic viewports retain their fractional display scale; software
viewports require the explicitly bounded 1280x720 display rectangle.

Windows / RTX 5060 Ti, driver 617.14, TU4, offline copied saves: #330's green
race-intro reflections reproduce at the reporter's exact saved 1080p/2x/off
settings both after a completed benchmark and in a fresh run without one.
Active graphics flags match before/after the benchmark return. A RenderDoc
capture shows a generated 512x512 reflection face with only its upper-left
384x384 area rendered correctly and striped right/bottom bands, matching the
0.75 fractional display factor. The NVIDIA implicit cubemap LOD workaround is
enabled; this case has corrupted source texture data.

`tools/test_fh1_screen_raster.cpp` covers display/downsample/cinematic views,
square reflections and shadow maps with either Y sign, and software viewports.
It passes locally on Windows and runs in both SDK CI jobs. In the corrected
live/captured Recaro intro, the same car's reflections and headlights are clean
and the complete 512x512 reflection face contains valid scene data. In face 0's
previously corrupt edge bands, pixels with any channel at least 192 drop from
17,611 to zero (PNG exports; this is a scene-specific comparison). The capture
uses non-sparse shared memory and non-threaded submission for replay fidelity;
the symptom also reproduced without those capture settings.

Final ordinary-runtime validation repeats the actual Windows launcher Session
benchmark/relaunch/return flow with the reporter's exact saved settings: 15 s
settling and 1,800 measured frames over 60 s complete normally; inspected active
flags restore byte-for-byte and the subsequent Recaro intro/car menu remain
clean. Live 1080p/1440p switches also complete. Source save hashes are unchanged.
The exact transient car-shadow symptom in #329 did not reproduce before the
change; it remains unconfirmed rather than being claimed fixed. Other GPU
vendors have not been runtime-tested here.

**Reproduced.** Traffic cars on the Windows RTX 5060 Ti showed a colored checker
pattern that bloom turned into a white/cyan/magenta glow. It occurs with DLSS off
as well. Clamping the 7e3 output range before blending did not fix it and that
experiment was reverted. Disabling anisotropic filtering also reproduced the
blowout; the road's filtering need not be reduced.

**Capture evidence.** A native 2560x1440, 120-FPS-cap run under RenderDoc triggered
its capture when the glow was visible. The material buffer already contains the
checker pattern before bloom. Pixel history identified the Durango body draw;
the captured GPU pixel was approximately `(0.4363, 2.9199, 0.2661)`. Replacing the
shader with an identical binary reproduced that pixel. The shader validates with
the device's enabled scalar-block layout. All six reflection cubemap faces and
mip levels contained valid-looking data, rather than an uninitialized mip chain.

The CPU shader debugger (with only its unsupported explicit FP32 RTE declaration
removed in the diagnostic copy) produced `(0.22796, 0.06113, 0.09175)`. Removing
that declaration in the GPU shader did not fix the corruption. Forcing the cube
sample to mip zero removed the body blowout, but would sacrifice normal mip
selection. Replacing its explicit coarse-gradient cube samples with implicit LOD
instead gave `(0.22791, 0.06113, 0.09174)` on the GPU, matching the reference after
float16 quantization. The cause is narrowed to this sampling path; the precise
NVIDIA driver/compiler failure is not established.

**Correction.** SDK patch 0047 uses implicit LOD for ordinary automatic cubemap
fetches on NVIDIA. Fetch-constant and instruction mip bias still apply, and the
sampler retains its filters and anisotropy. Guest register gradients and explicit
LOD keep their explicit path. Other GPU vendors are unchanged. The startup cvar
`vulkan_implicit_cube_lod=false` restores the previous path for comparisons.

**Live validation.** Two 105-second native-1440p runs used the same renderer binary,
profile copy, 16x anisotropic filtering and 120 FPS cap, changing only that cvar.
The old path reproduced the blowout. The correction showed normal materials in all
16 captured traffic frames, including close passes. A fixed road-region detector
counted up to 1,850 almost-white pixels with the old path and zero in that region
across the corrected captures; this is a scene-specific check, not an all-game
artifact detector. Screenshots and the RenderDoc capture are retained locally.

This correction is independent of DLSS and does not establish that every moving-scene
artifact is resolved.

## The car's picture with an empty pipeline cache (#74, 2026-10-16)

Buying a car saved its picture (`ForzaProfile/Thumbnails/Thumbnail_N.xdc`, 768x288) nearly
black: 23-33 KB, mean luma under 2/255, only the narrow side view on the right edge and no
car (a correct one is 80-110 KB with the 3/4 view on the left, `tools/xdc_decode.py`).
Cause: the SDK skips a draw whose pipeline is still a placeholder
(`async_shader_compilation`, Vulkan `IssueDraw`: the frame is not shown, but a render made
once keeps the hole). With an empty pipeline cache (a new install's first purchase) the
showroom and the picture need dozens of new pipelines, so the car's draws (MSAA 2x, scissor
1216x256, about 90 draws in the run below) were skipped and the picture was saved without them.

Test (TU4, Linux, RADV, 60 fps, headless gamescope, scratch copy of the pristine save, buy the
F50 GT from the 1000 Club car list; cold = empty `--cache_root` and `MESA_SHADER_CACHE_DIR`):

| Run | Picture |
| --- | --- |
| cold cache, async on (before) | 27,109 bytes, mean 0.8: side view only |
| warm cache of a run with other cars (M6 Coupe, new pipelines) | 33,130 bytes, mean 1.2: same fault |
| cold cache, `--async_shader_compilation=false` | 93,116 bytes, mean 5.0: correct |
| cold cache, with the fix | 93,116 bytes, mean 5.0: identical to the previous row |
| same cache again (warm), with the fix | 93,116 bytes, mean 5.0 |

Fix: SDK patch 0051 adds `sync_pipeline_creation` (draws wait for their real pipeline while
it is set) and `src/hooks/thumbnail_sync.cpp` sets it for 8 s from the guest call that starts
a picture (`sub_82546460`, TU4 `sub_825B63B8`; its callee `sub_82546168` is only the save
setup). A window by time and not by the call: the guest returns long before the GPU reaches
the render (270 ms later in the cold run, behind about 1,400 draws of the new scene), so a
setting cleared at the return was off again when the draws came. Draws in those 8 s wait for
their pipeline as with `async_shader_compilation=false` (a stutter only while pipelines are
new); a warm cache waits for nothing. `FORZA_THUMB_SYNC=0` switches the hook off. Direct3D 12
(`--gpu_backend=d3d12`) is not covered.


## Photo mode lights gone at render scale 2 (#77, 2026-10-16)

At resolution scale 2 (240 fps, scale 2, AMD RX 9070 XT, Windows) the car's head and tail
lights vanished in photo mode. Reproduced on Linux/RADV at scale 2 (Pagani Zonda R at dusk,
free roam, START > Photo mode > Take photo, 1280x720 guest): scale 1 shows the lights'
glow, scale 2 shows dark lamps, with `readback_resolve` some and full alike (so not a
readback problem). RenderDoc captures at scale 1 and 2 (`FORZA_RDCAPTURE_TRIGGER`, the 3
captured frames, `qrenderdoc --python`): the car's lamp draw (
1019 indices) is issued in both and covers the same pixels (pixel history, same primitive), but its
pixel shader writes (0.56, 0.56, 1.22) at scale 1 and (0, 0, 0) at scale 2. Its textures: one
1024x1024 DXT5 texture was bound as 2048x2048 at scale 2 and was all zero.

Cause: photo mode resolves the scene every frame into a 3,768,320-byte range starting at
physical 0x1BDB1000 (and two more, 0x1C149000 and 0x1D728000). The lamp texture's mip chain
(0x1BD53000 + 393,216 bytes) ends in the first 8 KB of that range. The texture cache turns
a texture into a "scaled resolve" texture when any page of its range was resolved to
(Xenia's rule); such a texture is read from the scaled resolve buffer, which is zero where no
resolve wrote. So 2 pages of tiny mips (2 % of the mip range, none of the base level) made the
whole texture black. At scale 1 the rule is off, so the data the game wrote is used.

Fix: SDK patch 0052, `scaled_resolve_min_coverage_percent` (default 50): the texture counts as
a resolve target only if resolves wrote at least 50 % of the pages of its base level or of its
mip range (a resolve target is covered by its own resolve, 100 %). 0 restores the old rule.
Checked at scale 2 in the same photo mode view: lamps lit as at scale 1. The textures that are
scaled resolves (1280x720 photo and depth targets, the bloom chain) keep their scaled size.
The red line on the car in the report was not reproduced (it may be the same fault on another
texture: any streamed texture whose tail overlaps a resolve range is affected the same way).

**Follow-up checks (2026-10-16).** Headlight beam on the ground in photo mode: not
reproduced. Same save, Zonda R at dusk, scales 1, 2 and 3 (scale 2 and 3 also with the
reporter's AF 16x and FXAA extreme): the default photo mode view, the camera pitched down
to the ground in front of the car (-41 to -43 degrees), turned away from the car (yaw 318)
and the photo taken from there: the lit patch is there at every scale, with the same
brightness within 1-2 levels (8x9 grid of the screenshots). The bloom over the car's nose in
the pitched-down view depends on the pitch (it shows at -41.4 degrees at scale 2 and 3, not
at -42 or -43), not on the scale.

A car's livery lost layers with an empty pipeline cache (found while looking for the red
line). At the festival (the BMW M3 GTR the 1000 Club flow ends in, photo mode default view)
the first run with a new cache showed the left front wing blue where it is white in every
later run. It first showed at scale 2 only because that was the first festival load with
that cache: an empty `--cache_root` and `MESA_SHADER_CACHE_DIR` show it at scale 1 too.
RenderDoc: the car's two 1024x1024 RGBA8 livery textures (6 mips, 2048x2048 at scale 2)
differ from a good run in a few areas (mean difference 42-48/255 in the area of the wing
shape), the same in every mip, so the layer was never drawn (not a resolve fault). The game
draws the livery once when the car is loaded, so the draws `async_shader_compilation` skips
for a placeholder pipeline (#74) are missing for as long as the car stays loaded.
`--async_shader_compilation=false` with an empty cache: correct.

Fix: `src/hooks/thumbnail_sync.cpp` also holds `sync_pipeline_creation` (patch 0051) for 8 s
from each call of `sub_82C8FE60` (TU4 `sub_82D50EA0`), which sets up a livery element render
(render scenario `RenderLiveryElements` or `RenderLiveryElementsHighlight`; 10 calls within
1 ms at the festival load, none in free roam or photo mode afterwards). Empty cache with the
fix: wing white at scale 1 and 2, the same as a warm cache. `FORZA_LIVERY_SYNC=0` switches the
hook off. The red line in the report (a red VW Corrado, not in the test save) was not
reproduced on the cars at hand (Zonda R, M3 GTR at scale 2, AF 16x, FXAA extreme); a livery
layer missing in the same way is a likely cause.

Photo mode (#167, 2026-10-28): since ReIGlue 0364 the pipelines a frame meets are built in the
background across frames, and draws whose pipelines are not ready are skipped in every frame
until they are, not only in the first. A photo taken soon after entering photo mode on a slow
driver (AMD's on Windows, 0.2-0.3 s a pipeline) could then miss them, as the photo is composed,
resolved and read back on the CPU at SAVE. `src/hooks/thumbnail_sync.cpp` holds
`sync_pipeline_creation` while photo mode renders (it draws the first shadow cascade with the
high-quality filter, technique 8 of `ApplyShadows`, which play never uses; renewed at most once a
second, 8 s after the last such frame), so its frames wait for their pipelines as before 0364.
`FORZA_PHOTO_SYNC=0` switches it off. Any other render the game reads back while draws were
skipped logs `Pipelines: a render the game reads back ... missed draws` and is counted in the
`Pipelines:` line.


## Reused car thumbnail readbacks (Vulkan)

Related: #50. FH1 reuses the 1280x720 RGBA8 thumbnail staging surfaces
for its small garage tile and large car card. With `readback_resolve=some`,
a reused key can retain the earlier CPU copy. SDK patch 0053 refreshes
the known partial staging resolves and the 768x288 thumbnail atlas using
the full readback path. Other resolves retain their configured policy;
`readback_resolve=none` still disables readback.

This is separate from the cold-pipeline fix for #74 (patch 0051 and
`thumbnail_sync.cpp`), which must remain enabled. It does not change saved
thumbnail dimensions or add DLSS. The scaled-thumbnail filtering and
1x-scene/native-HUD synchronization are part of the graphics work in #59.

Validation: copied-save regeneration and restart were tested at native
2560x1440 with a 120 FPS cap on the graphics integration based on 05cbff8.
The standalone patch also applies to the current public SDK patch stack.
That gameplay test predates the public cold-pipeline fix; it does not prove
this change alone resolves every report in #50. Original saves were untouched.


### Black thumbnail at render scale 3 (#102)

At render scale 3 (and above) a newly bought car's picture was saved black (768x288, 938 bytes) on
AMD (RX 9060 XT, RADV); scale 1 and 2 were fine. Cause: the readback path (0053/0065) downscales
the scaled resolve data on the GPU with a compute shader that read the whole scaled resolve
buffer (`VK_WHOLE_SIZE`). That buffer is 512 MB x scale^2: 2 GB at scale 2, 4.5 GB at scale 3,
which is over RADV's `maxStorageBufferRange` (4 GB). An oversized descriptor range is invalid and
reads came back zero (the shader also computes 32-bit byte offsets, which cannot address above
4 GB anyway). The log showed every 1280x720 tile and the atlas read back with 0 non-zero bytes.
The same also dropped other resolves above 4 GB at scale 3 (silently skipped).

Fix: SDK patch 0068 binds only a window of the buffer (offset aligned down to 4 KB or the device
offset alignment, range up to the end of the resolved range) and passes the offset inside the
window to the shader. It applies only when 512 MB x scale^2 is larger than `maxStorageBufferRange`;
smaller buffers keep the whole-buffer binding, so scale 1 and 2 are unchanged on AMD (they also
get the window on GPUs with a smaller range, which is correct there too).

Validation (copy of a save, local server, RX 9060 XT / RADV, TU4): buying the F50 GT saves
89,715 bytes at scale 3 (was 938, black), 89,838 at scale 2 and 93,116 at scale 1, all showing
the car.


### Car picture of upgrade parts after the paint shop (#241)

Report (Windows, RX 9070 XT, 4096x2160, render scale 3): after the decal editor, the WRX
STI's picture in Car Select and on the car card showed upgrade part images. It stayed after a
restart; saving a design rewrote it correctly. The player's save holds it
(`Thumbnail_3.xdc`, written 01:43:29): `tools/xdc_decode.py` shows rows of part images, the
guest memory the picture's resolve was meant to overwrite.

Cause: device memory ran out. The player's log has no GPU error for 1 h 54 min, then failed
render target images (`Failed to create a 2520x2352 4xMSAA ... render target image`), failed
scaled-resolve memory (`Failed to allocate sparse memory for the scaled resolve buffer`,
`Failed to obtain the resolve destination memory region`) and 800,000 failed draws until the
game was closed. A resolve that cannot get its memory writes nothing, so the CPU compressed
what was there before. The Vulkan render target cache keeps every render target image for the
session, about 90 MB each at scale 3, and the paint shop adds dozens (one per car side and
layer size): 6.0 GB in free roam, 11.4 GB in the decal editor and 13.2 GB after switching
sides here (720p, scale 3). The original game has nothing like it: the console has no host
render target images.

Fix: SDK patch 0112 frees the cached render targets that hold no EDRAM data when device-local
memory reaches 90 % of the driver's budget, or after an allocation failed
(`vulkan_render_target_trim_percent`, [SDK patches](sdk_patches.md)). Cancelling a decal
(answering No) does not write a picture; saving one does, and wrote correct pictures at scale 3
before and after trimming. A picture already broken stays until the car's design is saved
again.

## Photo mode's Big Shot was the loading screen (#552)

BIG SHOT : 8.3 MEGAPIXEL (RB in photo mode's camera, then "create a large photo and upload it to
ForzaMotorsport.net") sends a 3840x2160 BMP (`LargeFileUpload.Create(..., "ForzaBigshot.bmp")`,
then `Content.UploadContent`). With the default `readback_resolve = some` all nine 1280x720 tiles
of it were the first loading screen; with `full` it was right.

How the game makes it (TU4 addresses; the disc build's are in `src/hooks/bigshot_readback.cpp`):
YES posts the request (`sub_828006B8` calls `sub_826103D0` with 3840x2160), and the render thread
starts it in `sub_82614850`. The tile state (the object of `sub_8248B278` + 148) gets the 3840x2160
buffer and the flag that sends the tiles there. Each tile is accumulated over 96 frames. On the
last one `sub_825F4418` calls `sub_825F3CE8`, which locks that frame's front buffer (one of the
renderer's two D3D textures at 0x833D5150 and 0x833D5184: 1280x720 2_10_10_10 at physical
0x1BDB1000 and 0x1C149000) and untiles it into the buffer. After the ninth tile `sub_82CF45C8`
resets the state, and the BMP is uploaded. Hardware watchpoints on the front buffers (gdb `awatch`
on their 0xC0000000 view) caught nine reads during a Big Shot, all from `sub_825F3CE8` (through
`sub_82B010E0`, the untiling).

The game resolves both front buffers twice every frame. `some` copies a resolve back to guest
memory only when its key (range, source, format) is new or its readback buffer was dropped after
60 unused frames. The front buffers are never unused, so they were copied back once, at the first
loading screen, and the CPU read that picture for every tile. A trace of every resolve during a
Big Shot showed no resolve of its own for the tiles. The only new key is the 1280x720 float
accumulation target at 0x1E759000, which the CPU does not read.

Fix: ReIGlue 0411 adds `readback_resolve_wait_range`, a guest physical range whose resolves wait
for the GPU and are copied back as with `full` (Vulkan and D3D12; 0 for none).
`src/hooks/bigshot_readback.cpp` sets it to the two front buffers (from their fetch constants)
once `sub_82614850` has started a capture to the CPU buffer, and clears it in `sub_82CF45C8` (or
after 10 minutes if the end never comes). Only the front buffers' resolves during a Big Shot pay:
1,728 copies (2 a frame for 864 frames), about 2.5 ms of GPU waits a frame while it runs. At a
60 fps cap the capture took 15.1 s, against 14.5 s before. Other frames do not change.
`FORZA_BIGSHOT_READBACK=0` switches it off. `readback_resolve = none` still turns readback off,
and the Big Shot with it.

Checked (TU4, RADV on an RX 9060 XT, render scale 1, `some`): a Big Shot taken in free roam is the
photographed scene, nine different tiles that join without seams, and the local server publishes it
(forza-live #17). A photo saved with SAVE loads correctly from VIEW PHOTO and its thumbnail is
right. An Auto Upgrade at the Montano Plains outpost wrote a correct car thumbnail. FSR 3 Native AA at
render scale 2 (1080p output) gives a correct Big Shot too; its capture takes 21 s. The built-in
benchmark at render scale 1 (1280x720, 240 fps cap, four runs each, alternating) stays within its
run-to-run spread: median 141.2 fps (120.2 to 148.1) against 143.3 (136.6 to 146.7) on main, 1% low
93.9 against 92.8, p99 9.71 against 9.60 ms. The D3D12 path was not run.

## Festival video flashes purple (#91, open)

Report: RX 9070 XT, Windows 11, Vulkan, v0.1.12, `frame_rate` 240, render scale 2, AF 16x,
FXAA extreme, a new save. The video after "Race to the Festival" (`media/ui/videos/FMV_01.wmv`,
136 s: Kayla with the clipboard, the Corrado, the mechanic in the Horizon garage) flashes
purple and nearly white; later videos are fine. In the reporter's video the purple frames have
the right picture (luma) and colour planes near 255, with striped bands along whole tile rows;
the white frames in between have a wrong luma plane as well.

How the videos are drawn (RenderDoc, a boot video, TU4, scale 2): one quad samples three
`R8_UNORM` textures, Y 1280x720 and U, V 640x360, which XMediaFacade's decoder writes with
the CPU (unscaled textures), into the EDRAM; that is resolved and drawn by the UI. So in the
purple frames the U and V textures (in the white ones also Y) hold other data than the decoder
wrote: stale GPU copies or the wrong memory, not a shader fault.

Not reproduced on Linux (TU4, RX 9060 XT on RADV, the reporter's settings, a new profile
driven to the festival): FMV_01 played clean under RenderDoc, with render sync on (122 fps
during the video) and with `FORZA_RENDER_SYNC=0` (main loop 240, render thread 122). Both
machines enable the same Vulkan extensions (fragment shader interlock included). Not tried:
the Windows build under Proton (its input FIFO is POSIX-only, and the video is 13 minutes of
driving in). FMV_01 copied over a boot video (`T10_MS_Combined.wmv`) stays black, and over
the new-game intro (`Forza_Tone.wmv`) it is skipped, so it only plays in its own place.

Reaching the video: a new `--user_data_root`, START through the boot videos and the title,
A on the video calibration and on Single Player. The opening drive follows the AI routes
`colorado/Ribbon_00/route_160.owt` from the start to (2019, -982), then `route_155.owt`
backwards to the diner (`tools/owt_path.py`, autodrive with `AD_PATH_LOAD` and `AD_OPEN=1`);
"Race to the Festival" follows the CPU car (autodrive's leader mode, skipping the parked cars
at y -1020 and below). The Viper spins at full throttle: throttle pulses of about half a tick
keep it on the road. About 13 minutes from boot to the video; nothing saves on the way.

Cold pipeline cache ruled out as the cause (2026-10-17): the boot videos on TU4 with an empty
`--cache_root` (57 pipelines created during boot, async shader compilation on) played clean in
12 screenshots from the first video to the title. With async compilation a draw whose pipeline
is not ready is skipped, but the SDK then does not present that frame at all
(`vulkan_async_skip_incomplete_frames`, on by default), so a cold cache gives held frames at
worst, never stale render-target contents on screen. Since ReIGlue 0364 (#167) a frame that
only lacks object draws is shown after `vulkan_async_skip_incomplete_frames_ms` (100 ms) of
holding, without those objects; a frame that lacks a screen-space pass (a movie frame, a
composite, a full-screen effect: 6 vertices or fewer), a depth or stencil only draw, a draw
into the 1280-wide front buffer or UI target or the native HUD, or a draw of the VR stereo
segment is still held until the pipeline is built, so stale contents still never reach the
screen. With `vulkan_async_skip_incomplete_frames=false` they can (objects or passes missing
for as long as their pipelines build); the `Pipelines:` line counts frames shown without
draws. Purple/white frames with the right luma
and wrong colour planes do not fit that either, so no movie sync window was added. Not tested
on AMD Windows (#108: the same cutscene was fine on the player's next run, so check whether
that run had a warm cache and the same settings, and whether `vulkan_async_skip_incomplete_frames`
was changed).

Open: with Linux clean, Windows or the AMD Windows driver is the likely factor. Asked for a
run at render scale 1, one with Direct3D 12, and whether the video looked right on their first
save with v0.1.8.

## Missing dynamic shadows in the stencil-copy fallback (#75)

Reproduced on Windows / RTX 5060 Ti / Vulkan at draw scales 1 and 2, with
DLSS off and synchronous shader compilation. In a stationary daylight scene,
the car had its contact patch but no projected shadow, and nearby tree shadows
were absent. The same copied save and default photo view were used for both scales.

The car silhouette was present in its shadow map. The projection pass failed
stencil testing: a depth/stencil ownership transfer changed source stencil zero
into destination 255, while the projection required zero. On the bit-copy path,
`GetTransferShader` deliberately declares only the stencil texture, but the
packing branch was conditional on having loaded depth. `packed` stayed unset,
so none of the eight bit-copy shaders emitted their conditional discard. Each
pass therefore wrote its bit across the transfer rectangle.

SDK patch 0055 feeds the loaded stencil value directly into the existing bit
test. Color-to-stencil and the direct stencil-export path are unchanged. This
fix applies to the fallback wherever it is used, rather than matching a vendor
or an FH1 shader hash.

Validation: the public SDK patch stack applies cleanly to bd833a2; the Windows
GPU plugin built with Vulkan and D3D12 enabled. Live gameplay and matched photo
views at scales 1 and 2 restore the car, traffic and tree shadows. A post-fix
scale-1 GPU capture has 485,788 zero and 435,812 one stencil pixels in the
1280x720 active area (no 255 values); the two used stencil-only shader variants
now contain the conditional discard. Before the fix the scale-2 active area
was entirely 255 and the projection's stencil reference was zero.
The integration binary also contains the separate DLSS/HUD work; this patch
itself is standalone. Other GPU vendors have not been runtime-tested here.
No original saves were modified, and screenshots/captures remain outside Git.

Capture caveat: RenderDoc with sparse shared memory produced zeroed index data
and a misleading collapsed shadow mesh. A live GPU readback matched the CPU's
index and vertex data byte-for-byte. For reliable replay, the subsequent
capture used `vulkan_sparse_shared_memory=false` and
`vulkan_threaded_submission=false`; ordinary live validation retained defaults.

## Filter supersampled car thumbnails (#50)

### Scaled storage layout correction (#336)

Patch 0149 corrects the RGBA8 box filter introduced by 0066. Scaled resolve
storage expands each 16-byte guest quad into scaled host quads. The old filter
instead averaged consecutive words as though each guest pixel owned a contiguous
sample block, mixing neighboring pixels and losing detail. Only the thumbnail
box-filter path changes; ordinary point/center readbacks retain their policy.

Windows / RTX 5060 Ti, TU4, offline copied save: at 1440p with 2x Game MSAA,
an identical Mustang auto-upgrade regenerated a visibly striped/jagged garage
tile before the fix and a clean tile/card atlas after it. Display samplers were
already linear. Existing saved thumbnails require regeneration through a normal
paint/upgrade change; this patch does not rewrite profiles.

`tools/test_vulkan_thumbnail_readback.py --header SDK/src/graphics/shaders/vulkan_spirv/resolve_downscale_cs.h`
dispatches the embedded shader against independently packed physical quad data:
scales 1–3 on both axes, offsets 0/16/256, one/two tiles and output guards.
The corrected shader passes 27/27; the pre-fix shader fails 18/27 with
`--original-control`. The earlier 81-case test used pixel-contiguous source data
and therefore did not validate the renderer's actual storage layout.

Patch 0066 averages the RGBA8 samples in each supersampled guest pixel when
reading back a known car-thumbnail surface. The old shader chose one corner
sample, discarding the other three samples at 2x or eight at 3x. Depth, other
formats and ordinary resolves keep their existing point/center policy. The
thumbnail dimensions and refresh rule from patch 0053 are unchanged.

The earlier integration validation ran 81 actual Vulkan compute cases at scales
1-3, including nonzero offsets, multiple tiles and output bounds, and regenerated
Mustang tile/card previews on a copied TU4 save at native 1440p. Those results
predate this standalone split; the shader algorithm is unchanged. No claim is
made that old saved thumbnails are rewritten automatically, that all #50 cases
are fixed, or that other GPU vendors have been runtime-tested here.

Standalone validation (2026-10-16, Windows / RTX 5060 Ti): dispatched the actual
embedded SPIR-V for both versions through Vulkan. The matrix covers 1x-3x on
each axis, point/center/box modes, source offsets 0/16/256 bytes, one or two
tiles and a deliberately excess workgroup with guarded output. Before: 57/81
cases pass, with all 24 nontrivial box-filter cases failing. After: 81/81 pass,
including the output guards. No game assets are part of the test inputs.

## Windows WMV texture updates

The WMV player writes Y/U/V planes on the CPU, then calls the guest cache-clean
primitive for each plane. The recompiled cache instructions do not notify the
renderer. `movie_texture.cpp` forwards only these three WMV call sites to the
physical-memory write watches, allowing reused video textures to be uploaded again.
Other callers retain their original behavior. TU0 and TU4 have separate addresses.

`FORZA_MOVIE_CACHE_FLUSH=0` disables this invalidation for comparison. It is enabled
by default and does not require the graphics-menu or save-lab branches.

Validation: the TU4 Windows host built and played the original festival conversation
at native 2560x1440 output on an RTX 5060 Ti, with native HUD, SMAA and a 120 FPS cap.
A disposable profile created from empty user data was saved and restored; a private
test harness entered the original career graph, including the normal pre/post-movie
behaviors. The complete parent sequence restored the map and continued into car
selection. These harness changes and captures are not part of this fix. TU0 was
compile-checked; playback on TU0 and AMD hardware was not tested.

This addresses missing CPU-to-GPU invalidation. The original purple/white striped
transition has not been independently reproduced with the fix disabled, so the
related AMD report still needs confirmation on the affected machine.

## Vulkan DLSS initialization and presentation lifetime

Patch 0076 enables the buffer-device-address extension and feature required by
the NVIDIA runtime. Feature creation and a first evaluation use cleared private
images in a dedicated initialization submission. Its fence completes before the
renderer reports the feature ready; the first game frame still resets temporal
history. This does not add a CPU wait to each game frame or reduce the normal
frame queue depth.

Evaluation barriers also cover NGX's private history and scratch allocations,
which are outside the renderer's input/output image barriers. The input clear
and its first blit have an explicit transfer-write dependency.

Presentation-complete semaphores belong to swapchain images rather than CPU
frame slots. Reacquiring an image permits reuse of its semaphore. Swapchain
retirement drains the presentation queue before destroying these resources.

## Cockpit gauges lit by day (#183)

In cockpit view the instrument backlight only shows when the car's headlights are on. The
game sends a car's headlight amount (0 by day, 1 with the lights on) through
`ICarPresentation::SetHeadLightAmount` (the deferred one on the game thread, TU4 `82E81A48`,
disc `82DBFB78`; the render thread's stores it at render object + 0x342C) and `82448F90`
(disc `82418FE0`) copies it into the pixel shader constant `dynamicLights2.x` (c108.x) once per
car and view. Every car material that glows with the lights multiplies by it: the
headlight, tail light and number plate lenses as well as the cockpit's gauge faces and
needles (`CockpitGaugesEmissive * GaugeEmissiveScale * dynamicLights2.x` in the
`cockpit_gauge` shaders of `media/shaders/Cars/shaders_v16.fxobj`). Raising the amount for the
whole car lights the lamps too (checked: the tail lights glow).

The car is drawn model by model: `8246CEC8(presentation, ?, pass, group, lod)` (disc
`8243CDC0`) per model group, each drawn at once by `824469B0(constants, model)` (disc
`82416A00`). Group 16 is the cockpit interior, 1-15 are the analog gauge needles (the table of
`8248A5A0`); raising c108.x for group 16 alone lit the gauge faces and left the tail lights
as they were, groups 1-3 lit the 300SL's needles. `src/hooks/cockpit_gauges.cpp`
(`cockpit_gauges_lit`, launcher Display > Picture > **Cockpit gauges always lit**, off by
default) raises c108.x to at least 1 around those draws of the local player's car only and
writes the game's value back after each (constant shadow: pixel register r at
`(r + 376) * 16`, dirty flag bit `63 - r / 4` of the word at +8, `src/platform/cockpit_gauges.h`).
The local car is the vehicle of kind 0 in the vehicle list (`world_challenge.cpp`); its
deferred presentation is at vehicle + 0x2D94, whose + 4 is the render thread's presentation.
The game thread matches each car's `SetHeadLightAmount` against the list and publishes that
pointer; the render thread only compares pointers, so CPU drivers and other players' cars
online keep the game's lighting.

Checked on TU4 offline, by day at the Montano Plains outpost (setting off and on, same spot
and time): Mercedes 300SL Gullwing, Aston Martin DB5 and Plymouth Cuda 426 HEMI gauges lit with
the setting on and dark without; the Cuda's tail lights and the rest of the picture unchanged;
at dusk with the headlights on the gauges look as the game draws them. The Lamborghini
Aventador's digital display (drawn from a UI texture) is lit either way.
