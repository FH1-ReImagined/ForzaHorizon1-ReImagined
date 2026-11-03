# Native NVIDIA reconstruction (Windows development branch)

Settings > Display > NVIDIA DLSS offers Off, Quality, Balanced, Performance, and
DLAA, with a separate 1080p, 1440p or 4K output choice. These use NVIDIA's NGX
Vulkan runtime on a supported RTX GPU and driver. They are separate from the
presenter's spatial upscaling filter. Changes require restarting the game.

These controls appear only for an NVIDIA GPU. A saved DLSS setting is inactive
on AMD, Intel or an unknown GPU: it does not download the runtime or override
the native renderer settings.

On first Play, the launcher downloads the runtime and its license directly from
NVIDIA's public DLSS SDK, verifies pinned SHA-256 hashes, and stores them under
`<data folder>/graphics/nvidia-dlss/<SDK revision>`. Later launches reuse verified
files offline; changed or incomplete files are downloaded again. Preparation runs
in the background and can be cancelled. No manual DLL copying or system
installation is needed. Completed downloads are moved into the private runtime
folder instead of retaining a second copy. Unchanged-file verification is cached;
settings previews do not hash binaries. Session-owned status files are removed
when the game ends.

The output selector sets the native HUD/menu/label resolution independently of
the scene. `NGX_DLSS_GET_OPTIMAL_SETTINGS` supplies the scene dimensions for the
chosen quality mode and output. DLAA requests full output-resolution input. For
example, 1080p Quality normally selects 1280x720 input; 4K Performance normally
selects 1920x1080 input. The runtime's reported dimensions, not a fixed ratio,
control feature creation, input images, motion vectors, jitter and evaluation.

Guest EDRAM resolves still require an integer scene raster scale. The scene is
rendered at the smallest integer multiple of 1280x720 that contains NGX's input,
then sampled into the exact recommended input image. For example:

| Mode | Scene raster | NGX input | Output / native UI |
| --- | --- | --- | --- |
| 1080p Quality | 1280x720 | 1280x720 | 1920x1080 |
| 1080p Performance | 1280x720 | 1280x720 (NGX recommends 960x540; raised, #110) | 1920x1080 |
| 1440p Performance | 1280x720 | 1280x720 | 2560x1440 |
| 4K Quality | 2560x1440 | 2560x1440 | 3840x2160 |
| 4K Performance | 2560x1440 | 1920x1080 | 3840x2160 |

An input below the game's native 1280x720 is raised to 1280x720 (never above the output), logged once: a
scene raster below native is not safe for the guest (#110).

The runtime may recommend other exact input dimensions. Between-scale modes do
**not** yet shade only the NGX input pixel count: they pay for the ceiling raster
and an input resample. 1080p DLAA likewise receives a native 1080p input sampled
from a 1440p scene raster. This keeps the integration functional without claiming
the performance of a renderer that supports arbitrary scene dimensions. A true
fractional scene raster remains separate renderer work; the attempted padded
scene implementation corrupted reflection/post-processing resolves and is not
used here.

The output selector independently controls the HUD/menu/label raster. The 1080p
UI occupies an exact 1920x1080 rectangle inside the integer-tiled UI allocation;
its viewport, scissor and VPOS match that rectangle. NGX writes its output before
HUD draws. Texel-addressed compute presentation excludes padded texels without
resizing the HUD. Pipeline caches keep matching render-target caches/translators
and separate shader storage by scene input/output dimensions and scene/UI role.
Renderer capability API 2 prevents an older 1440p-only SDK from silently ignoring
the new output controls.

Depth and camera/object reprojection come from the game's resolved depth/stencil
texture and the constants of its `PPMotionBlurDepthHorizonVel` pass. A compute pass
reconstructs pixel motion without that shader's motion-blur scaling or clamping.
The viewport receives a subpixel jitter on main-scene depth draws; the HUD and
post-processing viewports do not. NGX executes on the Vulkan submission thread,
with its frame inputs copied into the deferred command stream. Guest pipeline and
descriptor bindings are invalidated afterward. Input is the tonemapped scene,
so the NGX HDR-input flag is not set. Jitter begins only after a successful NGX
evaluation; failure publishes a disabled state to the draw thread and subsequent
frames use zero jitter. Already-recorded in-flight frames cannot be retroactively
changed. NGX logging is off by default. Diagnostic environment and Vulkan dispatch
lookups are cached rather than repeated for every draw or blit.

The launcher reports active reconstruction only after the renderer successfully
evaluates a frame. If NGX or an auxiliary cache cannot initialize, the game keeps
native rendering and reports the failure. An incompatible render scale or render-
target path (including an extra-argument override) also disables reconstruction
instead of aborting GPU initialization. If feature creation or evaluation fails later, it reports
the temporary bilinear fallback and asks the player to disable DLSS and restart
for native rendering. Transfer usage on color render targets is enabled only for
the active scene/UI split. NGX-only Vulkan extensions and float16 are requested
only for the NVIDIA reconstruction path; DLSS Off keeps the ordinary feature set.
Details remain in the game log.

SDK patch 0046 contains the renderer integration. Windows SDK builds fetch pinned
NGX headers, the static loader and its license automatically. An existing local
SDK may be selected with `REXGLUE_DLSS_SDK`; `REXGLUE_USE_DLSS=OFF` builds without
NGX. The NVIDIA SDK license is copied beside the GPU DLL and into the SDK's license
directory. NVIDIA binaries are not committed to this repository. The pinned SDK
revision is `374959484e79a640feaba44c93ac8cfb0a03f5b5`; its
[license](https://github.com/NVIDIA/DLSS/blob/374959484e79a640feaba44c93ac8cfb0a03f5b5/LICENSE.txt)
applies to those components. Both launcher staging and `tools/build_windows.sh`
include the renderer capability metadata and NVIDIA SDK notice in packaged builds.

This remains a development integration limited to 16:9 1080p, 1440p and 4K output. Gameplay,
title-screen startup and native HUD composition have been exercised locally;
camera cuts, transparency and all race/menu transitions need broader testing.
The separate NVIDIA traffic-car blowout was reproduced with DLSS disabled and
traced to automatic cubemap sampling. SDK patch 0047 uses hardware implicit LOD for
those fetches on NVIDIA, retaining mip bias and filtering; register gradients and
explicit guest LOD remain explicit. The old path reproduced the defect in the live
comparison, while the correction showed clean traffic in all 16 captured frames.
See [Rendering issues](rendering.md#nvidia-car-reflection-sampling-2026-10-15).

A subsequent moving gameplay recording still showed a colored artifact at the
edge. The shader-cache correction is not yet established as a fix for every
edge artifact. The stationary traffic comparison does not establish that all motion
artifacts are fixed; that case remains under investigation.

The independent Windows build, Vulkan output-pipeline and NVIDIA cubemap fixes
were merged separately in [PR #58](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/pull/58).
This DLSS draft is based on main after that merge.

Review validation (RTX 5060 Ti, Windows): the rebuilt SDK ran DLSS, DLAA and Off;
forced NGX creation/evaluation failures continued in bilinear fallback. An
injected evaluation failure was followed by `jitter disabled; offsets=0,0`.
An extra-argument scale-1 override and `render_target_path_vulkan=fsi` continued
with native rendering rather than failing GPU setup. A 75-second isolated TU4
session through the launcher's plan/session path reached active DLSS with native
1440p HUD and game reprojection, including driving, and cleaned up its status file.
The 16 runtime/packaging tests and offscreen success/cancel/failure worker checks
passed. These checks do not establish that the remaining edge artifact is fixed.


Multi-resolution validation (2026-10-15, Windows / RTX 5060 Ti): the updated SDK
was rebuilt, and the complete ordered patch series applies cleanly. The launcher
output/mode matrix covers all twelve combinations; 20 runtime/packaging tests,
54 upstream launcher tests (10 skipped), and offscreen settings/worker checks
passed. Isolated TU4 driving runs exercised 1080p Quality/Performance/DLAA,
1440p Balanced, and 4K Quality/Performance/DLAA. Native output captures are
1920x1080 and 3840x2160 respectively. The renderer logs both actual scene raster
and NGX input dimensions rather than conflating the two.

When scene and UI use different scaled-resolve buffers, a GPU compute pass
resamples the per-guest-pixel sample blocks into the other cache and invalidates
its texture data through the normal resolve notifications. This preserves
scene-produced minimap/HUD textures without borrowing image views or reading
back to the CPU. The live 4K Performance test caught a missing minimap before
this synchronization; the corrected capture includes it.

The earlier edge artifact remains visible in some captures. 4K DLAA also shows
scene seams consistent with the existing warning for 3x scene rendering (#47).
These modes remain experimental; this work does not establish artifact-free
rendering or a matched performance improvement.

Review corrections (2026-10-16): patch 0046 leaves Vulkan instance extension
selection unchanged from upstream. Vulkan 1.1+ physical-device properties2
queries remain enabled with reconstruction off, and portability enumeration is
requested on Windows as before. Color targets retain TRANSFER_DST when either
initial fill or the DLSS split needs it. The ordered 46-patch stack applies to
bd833a2; the 20 DLSS runtime/packaging tests pass. The general launcher suite on
this Windows host has the same six failures and five errors on unmodified main
(134 tests, 20 skipped), so those are not claimed as passing here.

The corrected Windows plugin built with Vulkan and D3D12 enabled. A copied-save
Vulkan session with DLSS off, native 2560x1440, a 120 FPS cap and
`vulkan_render_target_initial_fill=true` reached free roam and produced a native
output capture. D3D12 and other GPU vendors were not runtime-tested.

The split of PR #59 moves DLSS-only patches 0057/0058 (projected-geometry jitter,
rotated depth/motion reconstruction and tile-consistent displacement) and 0062
(1x scene resolves copied into the native HUD cache) here. These operate without
ultrawide; 0062 depends on the DLSS cache split and is not a general readback fix.

Shared inputs (2026-10-16): AMD FSR 3 (temporal) is a second backend behind the same
plumbing (`fh1_upscaler=fsr3_*`, SDK patch 0072): the scene/UI split, the colour input, the
depth and motion-vector pass, the main-scene jitter and the reset rule are the ones described
here; only the reconstruction step differs, and it needs no NVIDIA runtime. See
[Rendering issues](rendering.md#temporal-upscaling-with-amd-fsr-3-51-2026-10-16).
