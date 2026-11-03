# World detail beyond the console (#361)

**Settings > Display > World detail** has two options; both default to the console's values
and apply from the next start (the game reads them when it loads the track):

| Launcher | cvar | Console | High | Ultra |
| --- | --- | --- | --- | --- |
| Shadow detail | `world_shadow_detail` | small objects cast shadows up to 60 m x their size factor | 3x as far (180) | 10x (600) |
| Distant texture detail | `world_texture_detail` | the track's texture LOD bias, 0 | -0.5 | -1 |

A third option, **Shadow softness** (`world_shadow_filter`, #380), changes how the shadows
are filtered rather than which objects cast them; it applies live and is also in the in-game
Graphics screen. See [Shadow softness](#shadow-softness-380) below. A fourth,
**Ambient occlusion** (`world_ambient_occlusion`, #380), darkens the sunlight where surfaces
meet; also live and in the Graphics screen, see [Ambient occlusion](#ambient-occlusion-380).

`src/hooks/world_detail.cpp` changes the values after the game reads the track's
`media/tracks/<track>/TrackSettings.xml` (loader `sub_82D505C0`, Title Update 4
`sub_82E128B8`; the struct layout is the same in both builds). Both values are read again
every frame, so a debugger or `FORZA_TRACK_SETTINGS="1272=600,1404=-1"` (float overrides by
struct offset, a test aid) changes them live.

## What the game has, and what each knob does

- **Shadow casters, `<screenAreaDistances depth="60">`** (struct +1272). The view setup
  (TU4 `sub_82E91710`) copies the four `screenAreaDistances` into the cull records of its
  views (cube map faces, mirror, main view, shadow/depth view), each with the camera position,
  the distance D, D² and a projection factor k. An object is drawn in that view only while
  k·d² <= D²·size (TU4 `sub_8246FE78`, called per PVS item by `sub_8246FED8`; items with flag
  0x00400000 skip it). For the shadow view D is 60: trees up a hillside, posts and bushes stop
  casting shadows a few tens of metres away. Live test at a forest road: D = 0.5 removes the
  trees' shadows on the slope, 60 leaves 32 objects a frame without a shadow, 600 22, 6 104.
- **Texture LOD bias, `<MipMapLodBiasModifier value="0">`** (+1404), added by the track pass
  (TU4 `sub_82469B18`) to the bias of the track's textures. +4 visibly blurs the road and verge;
  -0.5/-1 picks sharper mip levels for the road, gravel and terrain further out. Ultra can
  shimmer slightly on fine patterns; 8x-16x anisotropic filtering (Display > Picture) helps.

Looked at and **not** made options:

- **Model LOD and draw distance of the world.** Colorado is built from authored meshes per
  area (`TERR_..._PATCH_`, `_MIDDIST_`, `_LOD00`/`_LOD01`/`_LOD02`, `_NOLOD`), and which of them
  are drawn is decided per PVS zone in the track data: `tracks/colorado/bin.zip` holds 3,502
  `__R00Znnnnn.pvsz` zone lists (zone 394: 1,444 item entries) that the game streams as the
  camera moves, and records into command buffers per zone (`<CommandBufferRecording>`). There
  is no run-time LOD distance to scale; the pop-in seen while driving is the zone switch.
  Raising it means rewriting the zone lists to keep finer meshes in more zones, which streams
  and keeps more models in the console's 512 MB guest memory: inside the streaming system,
  with stall and out-of-memory risk. Skipped.
- **The main view's size test, `<screenAreaDistances mainScene="350">`**: the same test as the
  shadow casters for the main view. In free roam it culls about 3 objects a frame; 1,000 or
  3,000 changed neither the culled count nor the draws. No visible gain.
- **Shadow map size**: the shadow cascades are EDRAM render targets (1040x2528 and 520x1264
  per frame), so the render scale (Display > Picture) already sharpens them with the rest of
  the frame. `ShadowEnds` (`InGameShadowEnd` 202, `TrackSegCamShadowEnd` 200) are not read
  in free roam (no access in a minute of driving, camera changes, pause and map); 15 changed
  nothing on screen. The command-line option `UseSmallShadowMap` only switches a
  512 to a 256-wide viewport of a smaller shadow pass.
- **Grass**: `GrassFade*`, `GrassLOD*` and `GrassMin/MaxNumShells` are read every frame but
  Colorado's grass is meshes (`GrassMaxNumShells` 0); a 1-2 m fade or 0 and 8 shells changed
  nothing.
- **Cube map (car reflections)** `<Cubemap MeshLimit FarDistance VisibilityThreshold>`,
  `screenAreaDistances cubeMap`: the reflections of the world in the cars. Cars already look
  right; left for later. `<CollidableDropDistance>` (300): no visible change at 5 m.
- **Car and crowd LOD** (`media/renderscenarios.zip`: `CarLODTransDist`,
  `CarLODTransDistTraffic`, `CrowdLODCountScales`; `TrackSettings <Crowds lod0-3>`): known
  and cheap to raise, but lowest priority (#361).

## Cost

Free roam, TU4, RX 9060 XT (RADV), 1280x720 scene, uncapped (`--frame_rate=240`), the car
standing; the three levels switched live in the same session (4 x 12 s each, the first 2 s
of each phase skipped), BENCH lock held. Command processor = its CPU ms per frame
(`fh1_frame_cost_csv`), GPU = the process's DRM engine time per frame.

| Spot | Cores | Level | fps | Command processor ms | GPU ms | Draws |
| --- | --- | --- | --- | --- | --- | --- |
| Spawn (forest hill) | 8 | console | 157.8 | 5.07 | 5.23 | 3,289 |
| | | high | 158.0 | 5.03 | 5.22 | 3,263 |
| | | ultra | 157.1 | 5.08 | 5.26 | 3,275 |
| Spawn | 4 (`taskset 0-3`) | console | 153.0 | 5.05 | 5.37 | 3,207 |
| | | high | 153.9 | 5.10 | 5.37 | 3,308 |
| | | ultra | 155.3 | 5.05 | 5.31 | 3,262 |
| Forest road | 8 | console | 146.7 | 5.67 | 5.58 | 3,501 |
| | | high | 149.3 | 5.60 | 5.55 | 3,510 |
| | | ultra | 143.6 | 5.74 | 5.63 | 3,495 |

Within the noise of the measurement (about ±2% fps): the extra shadow casters are a few to a
few tens of draws, and the texture bias costs no measurable GPU time here. The built-in
benchmark is too noisy for this: the same settings gave 4,300 to 5,200 draws a frame and
104-121 fps from race to race (console 104.5 and 107.7, high 114.9, ultra 116.5 and 119.6).
No streaming stalls (`FORZA_STALL`) or artifacts were seen in these runs.

## Screenshots

Kept out of Git. Live A/B at the same moment (2560x1440, render scale 2): the spawn's forest
hill (Ultra adds the shadows of the conifers further up the slope; the road edge and verge
sharpen) and a stop above the valley with the mountain view. On a run where Shadow detail
went from 60 to 0.5 and to 6,000 at a forest road, the trees' shadows on the slope disappear
and return.

These knobs could also sit in the in-game Graphics screen (#116): both values are read
every frame, so a live change only needs the settings struct the hook already sees.

## Shadow softness (#380)

**Settings > Display > World detail > Shadow softness** and the in-game **Graphics** screen
(Title Update 4) offer Console (default), Soft and Softer. The value is read at every shadow
pass, so the Graphics screen's Save applies it without a restart.

| Level | `world_shadow_filter` | Filter disc | Mask blur |
| --- | --- | --- | --- |
| Console | `console` | the game's | the game's 2x2 box |
| Soft | `soft` | 1.5x as wide | the game's 2x2 box |
| Softer | `softer` | 2x as wide | 6-tap horizontal + vertical |

### How the game filters its shadows

The world shaders do not sample the shadow maps. The renderer (TU4 `sub_824829D8`, disc
`sub_824532F0`) first draws a screen-space shadow mask (`ShadowMask`, read by the world and car
materials as `ShadowMaskSamp`): for each cascade, `ApplyShadows` (`sub_82C3AF68`, TU4
`sub_82CFB8D8`) picks a technique of the engine effect in the executable by its r10 and draws a
full-screen pass that rebuilds the position from the scene depth and compares the cascade's
shadow map. Then the mask is blurred (`sub_823EFC08`, TU4 `sub_8241FD00`) and resolved.

- r10 = 4, `ApplyShadowsLQ` (`...WithFading_Depth` for the last cascade): 4 point taps at the
  `FilterTaps` offsets (its r9, float4[4] built per cascade; Colorado's nearest cascade has 2
  and 1.4 texels), turned by an angle hashed from the pixel position, then averaged. This is
  what the game uses in play, for all 11 mask passes of a frame in free roam (3 cascades, then
  8 passes over four more maps with inner/outer fades and a wider "spread" kernel).
- r10 = 8, `ApplyShadowsHQ`: 8 taps. Photo mode uses it for the first cascade (a flag at +9519
  of the view object). Forced in play it lightened the near shadows (the four extra offsets
  look unset outside photo mode), and the last cascade then asks for an `...HQWithFading`
  technique the effect does not have, so that cascade drew nothing.
- r10 = 1-3, `ApplyShadowsPCF2x2/3x3/4x4WithFading`: a 4x4 grid of point taps weighted with
  `getWeights2D` (bilinear PCF). A dead development path (the renderer's +8428 is only cleared
  by its constructor), and it only exists with fading. Forcing the 4x4 one in play (with the
  fading flag on the first two cascades, whose fade range starts past them) works, but gives
  **sharper** edges than the console's disc, not softer, and changes little at render scale 2.
- The blur kinds: 0 the 2x2 box (what play uses), 1/2 a 4-tap horizontal/vertical pair (picked
  with the same +9519 flag), 3/4 a 6-tap pair that nothing selects. The renderer's +8440 is the number of blur
  passes (1).

So Soft and Softer scale `FilterTaps` for the call (and write the game's values back after
it). Softer, whose wider disc leaves more grain, also replaces the box with the 6-tap pair, with
the same resolve of the mask between the two passes that the game does between its own. Console calls both functions unchanged; switching
back to Console in the same paused frame gives the same image (no pixel differs by more than
2 of 255, the output dithering). `tests/shadow_filter_test.cpp` checks the levels.

Not changed: the dual-map pass (`ApplyDualShadows`, `sub_82C3B3F8`; not drawn in free roam)
and the shadow map resolution (it follows the render scale). The blur is screen-space and not
depth-aware, as the game's box is: the wider blur can darken the lit edge of an object right
next to a shadow by a pixel or two more.

### Cost

Title Update 4, RX 9060 XT (RADV), 1920x1080 window, render scale 2 (2560x1440 scene), 2x
MSAA, uncapped (`--frame_rate=240 --vsync=false`), the car standing at the free roam spawn; the
levels switched live in the Graphics screen in one session, twice each (15 s per phase, 3 s
after each switch skipped), BENCH lock held. The GPU was the limit (busy 99% of the time), so
the frame time is the GPU time.

| Level | fps (run 1, run 2) | ms per frame |
| --- | --- | --- |
| Console | 87.8, 89.1 | 11.27 |
| Soft | 88.2, 88.5 | 11.29 |
| Softer | 85.5, 83.4 | 11.83 |

Soft costs nothing measurable: the taps only move. Softer's two 6-tap passes and the extra
resolve of the 2560x1440 mask cost about 0.5 ms (3-5% fps while the GPU is the limit; nothing
under a 60 fps cap here). An earlier session with the 6-tap blur on both levels gave the same
0.4-0.5 ms for it (Console 87.1 fps, both levels 83.4-84.9).

### Screenshots

Kept out of Git. The same paused frame at the free roam spawn (2560x1440, render scale 2, 2x
MSAA), switched live in the Graphics screen: the car's own shadow on the road, a roadside
tree's shadow and the rail and pole shadows get progressively softer edges with Soft and
Softer; the shadowed areas themselves stay as dark.

## Ambient occlusion (#380)

**Settings > Display > World detail > Ambient occlusion** and the in-game **Graphics** screen
offer Off (default), Low and High (`world_ambient_occlusion`). It applies live (Save in the
Graphics screen) and needs Vulkan: SDK patch 0350 (`src/graphics/vulkan/fh1_world_ao.cpp`)
does the work; the Graphics screen dims the row on Direct3D 12.

### Where it goes

The game has no ambient occlusion of its own, and no normal buffer, but it gives a pass
everything a depth-only one needs. Each frame it draws a depth pre-pass of the main view
(1280x720 guest, no MSAA, reversed depth: 1 at the 0.3 m near plane, 0 for the sky),
writes a stencil per object (0 the static world, 3 x an object id for the cars, 128 the
ground around the player's car, 129 the alpha-tested trees and grass) and resolves both to a
texture (`WorldDepth`; the speed blur reads its stencil later). The sun shadow mask's passes
(`ApplyShadows`, see [Shadow softness](#shadow-softness-380)) read that texture as
`k_24_8_FLOAT` and rebuild each pixel's view position from it with their pixel constants:
view z = c25.y / (1 - c25.x - depth), x = z * ndc.x / c0.x, y = z * ndc.y / c1.y. They write
the sun's visibility to the mask's red channel (green 1, blue and alpha 0), the blur follows,
and the world materials multiply their sunlight by it.

The patch multiplies an occlusion term into that red channel. That darkens sunlight only,
and before the materials add their fog, so distant fogged hills are not darkened twice. A
multiply over the finished scene color was the alternative: it would also darken the
ambient light in shade, but it would have to undo the fog, would land on glass, smoke and
other transparent surfaces drawn after the opaque world, and would sit inside Sean's native
world-color composition (#157). Shaded areas (under a tree's own shadow, the side of a
building facing away from the sun) therefore get no occlusion; sunlit contact areas do.

### What it does

At the first shadow pass of a frame (its pixel shader is one of the effect's seven
`ApplyShadows` techniques and its target's pitch is the depth texture's width, so a
mirror's mask is left alone), on the GPU, outside the game's render pass:

1. Linear depth at half the scene's resolution from 1920 pixels wide (1280x720 at render
   scale 2), the scene's own below; the nearest of each 2x2 block, so thin rails and posts stay.
2. Scalable ambient obscurance (McGuire et al. 2012): 12 samples on a spiral within 1.5 m,
   turned per pixel by interleaved gradient noise; the normal from the depth (the smaller step
   on each axis). Low uses intensity 0.6, High 1.0. It fades out from 60 to 160 m.
3. A 9-tap Gaussian each way, weighted by depth so it does not cross silhouettes.
4. Full resolution: the result upsampled with depth weights into an RGBA8 image (four pixels
   per texel). Pixels whose pre-pass stencil marks a car or the alpha-tested foliage, and the
   sky, keep 1: the cars keep their own shading (no darker paint in their creases) and the
   noisy foliage is not darkened. Both still occlude the ground (contact shadows under cars).
   The stencil comes from an 8-bit view of the same resolve, which the texture cache loads
   through fetch constant 31 (unused by the shadow passes; set for the request and put back).

Right after the next draw into the mask by another shader (the 2x2 box blur in play; with
Shadow softness Softer, the 6-tap horizontal pass, whose vertical pass then blurs the
product; photo mode's 4-tap pair should take the same path, not checked), one full-screen
draw multiplies the factor into the red channel (blend destination x source, red only). Off
records nothing.

### Cost

Title Update 4, RX 9060 XT (RADV), 1920x1080 window, render scale 2 (2560x1440 scene), 2x
MSAA, at the free roam spawn; the levels switched live in the Graphics screen in one session,
in the order Off, Low, High, High, Low, Off, twice (12 s per phase, 3 s after each switch
skipped), BENCH lock held. GPU = the process's DRM engine time.

| Run | Off | Low | High |
| --- | --- | --- | --- |
| Uncapped (`--frame_rate=240 --vsync=false`), paused frame, GPU-bound (99.5% busy): fps | 89.7-89.9 | 85.8-85.9 | 85.7-85.9 |
| the same, GPU ms per frame | 11.07-11.10 | 11.58-11.59 | 11.58-11.60 |
| 60 fps cap (`--frame_rate=60 --vsync=true`), car standing: fps | 59.8-60.0 | 59.9-60.0 | 59.7-60.0 |
| the same, GPU busy ms per second | 823-839 | 838-840 | 839-840 |

About 0.5 ms a frame at 2560x1440 (the five compute passes, the 1280x720 occlusion among
them, and the multiply), the same for Low and High (only the strength differs): 4-5% fps
while the GPU is the limit, nothing measurable under a 60 fps cap (the GPU's clock drops with
the headroom, so its busy time per frame is higher at 60 than uncapped for every level). The paused frame keeps traffic and the
sky still; in play with the car standing, uncapped, the same switch gave 2-3 fps less
(85.7-88.2 Off, 83.3-84.9 Low and High), with more spread from passing traffic.

### Screenshots

Kept out of Git. The same paused frame, switched live in the Graphics screen (2560x1440,
render scale 2, 2x MSAA), Off / Low / High: at the free roam spawn the road behind and beside
the car gets a soft contact shadow, the crease where the road meets the gravel shoulder and
the grass tufts at its edge darken slightly, and the rail on the right changes little (the
ground under it is already in its shadow). In the grass beside the road a few metres on, the
ground between the blades and the foot of the rail posts darken clearly; the rail's face
darkens too (the grass in front of it counts as an occluder). Switching back to Off gives
the identical image (no pixel differs).

### Limits

Depth-only and screen-space: what is off screen or hidden behind a nearer surface does not
occlude, so the darkening at a screen edge or behind a car can change as the camera moves.
Sunlight only, as above. The pass depends on the game's own shaders (their hashes) and its
depth/stencil layout; checked on Title Update 4 in free roam (Console and Softer shadow
softness), RADV; the validation layers (with synchronization validation) report nothing from
its passes. (They do report `VUID-vkCmdEndQuery-None-07007` once per host screenshot,
`FORZA_SCREENSHOT_TRIGGER`, with the occlusion off as well.) The seven shadow-pass shaders are
byte-identical in the disc's executable (`tools/fxobj_shaders.py` finds the same hashes), but
the disc build was not run.
