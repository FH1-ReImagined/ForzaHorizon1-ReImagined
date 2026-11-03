# Built-in ultrawide gameplay and HUD layout

Enable **Settings > Display > Ultrawide gameplay**, then restart. The gameplay
camera follows the physical window aspect with a wider horizontal view. The
viewport query supplies both projection and culling, so the newly visible sides
do not rely on stretching the original camera or cropping its vertical view.
The feature is off by default and does not load external libraries or manifests.

Gameplay draws at the initial physical window width. At 3440x1440, the
main-view viewport and output image are 3440x1440; presentation is 1:1. Viewport
coordinates, screen-resolve sampling and generated pixel positions account for
the active dimensions. The tiled EDRAM/resolve storage retains an integer stride
(3840x1440 in this case), with unused columns outside the active drawing area.
It does not shade a 3840x2160 scene and shrink it to fit.

Reflection resolves have a separate square cube view. A 768x512 integer-stride
resolve is uploaded through a 2D array and filtered into 512x512 cube faces,
including the loaded mip levels. A rectangular image is never exposed as a cube
view. Layout barriers and cache memory accounting cover both images.

**2560x1080 and other fractional window heights are supported through vertical
supersampling.** The drawing height rounds up to the next multiple of 720, then
the presenter filters the complete image to the physical window. At 2560x1080,
drawing and storage are 2560x1440; the output window is 2560x1080. This draws 33%
more pixels than native 1080p (not a measured frame-time increase). At 3440x1440,
drawing remains 3440x1440 with a padded 3840-pixel storage stride.

Camera framing and HUD proportions use the physical window aspect, including
when drawing has a different aspect. Shrinking the raster viewport vertically
without remapping EDRAM tile origins and resolve rows produced bands. This path
keeps those rows at their original integer scale; it does not implement native
fractional-height resolves. Supported physical bounds are 1–8960 pixels wide
and 1–5040 pixels high, subject to the host's window limits.

Native ultrawide currently requires Vulkan host render targets. The launcher
selects Vulkan and disables manual render scaling while it is enabled. Windows
DPI scaling is included: 2752x1152 logical pixels at 125% gives 3440x1440 physical
pixels. Resizing updates framing; restart to change the drawing resolution.
The resolution preset sets a guest/window default, not the borderless monitor's
physical size. Enable Ultrawide gameplay and restart at the desired window size.
Use Game MSAA without DLSS/FSR 3 or separate scene AA for this rendering path.

The in-game graphics page shows the physical window size and automatic
drawing size in ultrawide. Output, reconstruction, AA, scene scale and quality
presets are read-only in that mode; frame pacing, filtering and HUD controls
remain available. Saving those controls does not overwrite launcher rendering
settings. Ordinary default/720p and custom launcher resolutions also display
their actual current mode rather than falling through to the first 1080p choice.
On Windows, live rendering changes at those modes require choosing a supported
1080p/1440p/4K output, or using the launcher and restarting.

## HUD controls

Enable **Customize HUD layout** to use the sliders at either 16:9 or ultrawide.
This can be enabled independently of camera widening. The default ultrawide HUD is centered in a 16:9 rectangle, retaining its proportions on a
wider display. **HUD safe width/height** fit that rectangle uniformly within a
smaller safe area; the smaller percentage controls the scale. This scales the
HUD as a group rather than stretching gauges independently along each axis.

**HUD horizontal/vertical position** move the group, with offsets clamped so the
rectangle remains on screen. Horizontal positive moves right; vertical positive
moves down. At 100% height there is no vertical space to move into: reduce the
safe area first. These are whole-HUD controls, not individual minimap/speedometer
anchors or an in-game drag editor. The launcher applies changes on the next run;
the corresponding engine cvars can also be adjusted through the SDK console.

The renderer transforms non-depth-tested packed front-buffer UI draws. World
geometry and depth-tested overlays are excluded. The map viewport is excluded
from camera widening; a recent map view or disappearance of gameplay projection
queries restores 16:9 presentation. The swap hook updates this state even while
gameplay timing is paused.

Judge HUD proportions using the final presentation image. Older guest-output
dumps used the padded storage aspect and could make text and gauges look
squashed. Native-mode output dumps now have the active 3440x1440 dimensions.

## Validation and current limits

The SDK revision pinned by the source build includes the cockpit instrument
texture correction from [ReIGlue PR #2](https://github.com/FH1-ReImagined/ReIGlue/pull/2).
The instrument image and its downsample passes retain their own texture coordinates,
so their contents stay aligned with the dashboard material as the gameplay view
widens. This correction does not change camera FOV. Installed launcher packages
need an SDK overlay built from the updated pin to receive the correction.

Windows/Vulkan qualification on 2026-10-28 used public game main `52d5784` and
ReIGlue `7d17ea51`, with all 313 generated guest objects, PCHs and authored code
compiled fresh. The native 3440x1440 Viper (model 1544) cockpit capture at
283.99 seconds was checked against the earlier before/after images for ReIGlue
PR #2. The instrument numerals and digital text lie inside the physical cluster;
the original leftward shift is absent. No obvious new HUD or cockpit geometry
distortion is visible in that stationary frame. The PNG is pixel-identical to
the preserved SDK PPM, and the logs confirm 3440x1440 drawing with 3840x1440
padded storage. The separate cyan roof artifact remains visible.

A 2560x1440 16:9 control reached the same Viper, HUD and cockpit, with correct
instrument alignment observed through the Windows UI capture. No complete
native cockpit image was preserved for that control. Both runs used Stock FOV,
normal startup movies and copied profiles. Their 300-second owned watchdogs
closed them at 300.55 and 300.53 seconds (exit 1); these are not normal-exit
tests. Each closure recorded an empty owned job, no remaining game process,
and all nine source-profile files and three source-cache files unchanged.

Earlier negative attempts remain preserved: one used stale generated hints,
a fully regenerated run still stalled before the HUD with startup movies
skipped, and another harness run ended because its visible-frame acknowledgement
was missing. Reaching gameplay with normal movies does not establish the cause
of those loading stalls. This qualification covers the reported Viper at 16:9
and 3440x1440; it does not establish 32:9, another car, AMD hardware, or macOS/Linux
gameplay behavior.

1080-line support and menu validation (2026-10-21): a full TU4 Windows host build
from main `425dfa2` plus these host/launcher changes ran on an RTX 5060 Ti,
driver 617.14, with the existing public SDK stack. Offline runs used copied
saves. Logs confirmed physical 2560x1080 output with 2560x1440 drawing, and
3440x1440 output/drawing. A private negative-control host, differing in using
1080 drawing rows, reproduced two broad horizontal bands; the integer-height
backing removed them. Camera framing and proportional HUD were checked in the
scaled gameplay captures. A 125% DPI run also exercised 3200x1350 output with
3200x1440 drawing.

The real native graphics page showed the ultrawide window/drawing sizes and
the ordinary 1280x720 current mode. Saving in ultrawide omitted rendering
settings, including after changing the performance overlay to FPS; saving at
ordinary 720p preserved that output. Both standalone C++
tests (`ultrawide_math_test.cpp`, `graphics_resolution_test.cpp`), 34 targeted
launcher/UI tests, and a disc/base-game graphics-menu compilation passed. AMD
hardware, Linux runtime, combined DLSS/FSR 3 and fractional ultrawide, and native
1080-line raster efficiency were not validated by this test.

The native-resolution path was built from this PR's public SDK patch series,
without DLSS, and tested on Windows/Vulkan with an RTX 5060 Ti. An isolated
3440x1440 run covered driving and entering the pause menu. RenderDoc captures
show the main-view viewport at 3440 pixels wide, final output at 3440x1440 and
square reflection faces. Separate testing covers inset/offset HUD controls.
Launcher, persistence and standalone C++ layout/state checks passed, including
square-widget proportions in final 2560x1440, 3440x1440 and 5120x1440 coordinates.

Full map-screen transitions, car-name overlays in races, split-screen, cutscenes,
and Linux runtime behavior still need testing. Pink highlights remain visible
on the car and road in the standalone NVIDIA gameplay test as well as some
paused backgrounds. Fixing square cube allocation does
not resolve every renderer artifact (issue #47); this remains experimental.
DLSS is now part of main through PR #60. Patch 0063 preserves its deferred
commands and fractional texture-coordinate correction, and the launcher retains
the DLSS settings locks when ultrawide is toggled. This does not establish
combined DLSS/ultrawide runtime support; that mode still needs separate validation.

For the standalone math/state test, compile and run
`forza-recomp/tests/ultrawide_math_test.cpp` with a C++17 compiler. Launcher tests
run with `python -m unittest launcher/test_ultrawide.py`.

**Expand HUD to ultrawide edges** anchors supported side HUD draws outward while
retaining the same native widget scale. At 3440x1440 and 100% safe area, each side
moves 440 pixels outward from the centered 16:9 layout. The original game insets
remain; widgets do not touch the last screen pixel. Safe-area controls still
reduce the occupied area, and group offsets are clamped against the wider bounds.
Menus retain protected presentation. SDK patch 0063 recognizes the game's affine
HUD/font shader families and translates whole draws based on their origins;
unknown families and the central origin band retain centered placement. This is
experimental: large centered text, race overlays and projected labels still need
broader coverage before treating it as a complete per-widget anchoring system.

PR #59 was split at the maintainer's request. Patch 0063 contains only native
ultrawide rasterization, square reflection faces and proportional/edge-anchored
HUD placement. No NVIDIA runtime, reconstruction controls or general readback
changes are required. Earlier screenshots came from the combined development
build; the standalone split was rebuilt and checked separately at 3440x1440.

Pinyon Shift attribution: `src/platform/ultrawide.cpp` and
`src/hooks/ultrawide_projection.cpp` in forza-recomp carry the adapted view-state
and projection strategy. In patch 0063, the presentation cvars and HUD viewport
transforms in `src/graphics/flags.cpp`, the D3D12/Vulkan command processors and
`src/ui/presenter.cpp` carry adapted presentation logic. The original BSD-3-Clause
notice is retained in both the game source/package and the SDK patch directory.

Standalone split validation (2026-10-16): the complete 47-patch stack applies to
bd833a2, the Windows GPU plugin builds with Vulkan and D3D12 enabled, and the
TU4 game builds from the native-only source. The C++ layout/state checks and
three launcher persistence/bounds tests pass. An offscreen Settings-page check
confirms Vulkan/automatic-scale dependencies and native HUD sliders, with no
DLSS controls. Fresh standalone gameplay and launcher captures are attached to the PR rather
than Git. The game reached free roam, moved the car and entered the pause menu
at an actual 3440x1440 output, with DLSS absent. This is a functional smoke test,
not a performance benchmark or a claim that the pink highlights are fixed.
