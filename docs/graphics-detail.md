# World detail beyond the console (#361)

**Settings > Display > World detail** has two options; both default to the console's values
and apply from the next start (the game reads them when it loads the track):

| Launcher | cvar | Console | High | Ultra |
| --- | --- | --- | --- | --- |
| Shadow detail | `world_shadow_detail` | small objects cast shadows up to 60 m x their size factor | 3x as far (180) | 10x (600) |
| Distant texture detail | `world_texture_detail` | the track's texture LOD bias, 0 | -0.5 | -1 |

**Car detail** (`world_car_detail`) keeps other cars' detailed models farther away and on more
cars (High 2x, Ultra 4x the game's distances and counts); it applies live and is also in the
in-game Graphics screen, see [Car detail](#car-detail-361).

**Paint job detail** (`livery_bake_scale`) draws each car's vinyls and decals for driving at 2x
the game's 1024x1024 (photos keep the game's size for their final textures); see [Paint job detail](#paint-job-detail-659).

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
  the frame. A separate, higher resolution for the shadow maps alone would be a per-render-target
  scale in the GPU emulation (an SDK change to the EDRAM and resolve paths), not a game
  setting. `ShadowEnds` (`InGameShadowEnd` 202, `TrackSegCamShadowEnd` 200) are not read
  in free roam (no access in a minute of driving, camera changes, pause and map); 15 changed
  nothing on screen. The command-line option `UseSmallShadowMap` only switches a
  512 to a 256-wide viewport of a smaller shadow pass.
- **Shadow distance**: already long. The shadow renderer (TU4 `sub_824829D8`) fits nine
  light-space boxes a frame (their corners from TU4 `sub_8241C100`, logged at the free roam
  spawn): the three sun cascades of the mask are about 10, 36 and 92 m across, and the six
  more of the outer passes 50, 68, 100, 255, 400 and about 1,300 m, the last reaching beyond
  a kilometre. What ends a distant object's shadow is the caster cull above (Shadow detail),
  not the cascades' reach; stretching the cascades would spread the same texels wider, so
  blurrier shadows near the car.
- **Track far distance**: the command-line option `trackfardistance` (TU4 `sub_82563570`
  stores it at +4192 of the options object, 55 with `perfmode`): no load of that field was
  found in Title Update 4's code.
- **Grass**: `GrassFade*`, `GrassLOD*` and `GrassMin/MaxNumShells` are read every frame but
  Colorado's grass is meshes (`GrassMaxNumShells` 0); a 1-2 m fade or 0 and 8 shells changed
  nothing.
- **Cube map (car reflections)** `<Cubemap MeshLimit FarDistance VisibilityThreshold>`,
  `screenAreaDistances cubeMap`: the reflections of the world in the cars. Cars already look
  right; left for later. `<CollidableDropDistance>` (300): no visible change at 5 m.
- **Crowd LOD** (`CrowdLODCountScales` and `CrowdSpriteDistScale` in the render settings,
  `TrackSettings <Crowds lod0-3 SpriteDistance>`): only the festival sites have crowds; not
  changed. The cars' LOD is an option, [Car detail](#car-detail-361) below.
- **Density**: the game has no foliage or detail density setting (the grass above is meshes,
  placed per zone like the trees). The density values it has are gameplay: traffic and festival
  cars (`AI/Traffic/Density/*`, `AI/Spawning/Density/*` in `gametunablesettings/GlobalRegistry.xml`),
  the festival crowd's size (`<Performance CrowdSize="1024">`), and the particles' rate
  (`ParticleRateScale`, a render setting at 1). More traffic changes the game, and the
  multiplayer sessions sync it; left alone.

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

## Car detail (#361)

**Settings > Display > World detail > Car detail** and the in-game **Graphics** screen (Title
Update 4) offer Console (default), High and Ultra (`world_car_detail`). It applies live (Save in
the Graphics screen).

### How the game picks a car's model

Each car has up to five models, LOD0 (the full one) to LOD4. The game's render settings
(`DynamicRenderSettings`: the defaults in the executable, scenario files in
`media/renderscenarios.zip` on top, none of which set these) give two sets of distances and a
count, each read through its own getter (TU4 addresses; disc in `src/hooks/car_detail.cpp`):

- `CarLODTransDist` (getter `sub_8248CBD0`): `LODdist_Near` 15, `LODdist_Far` 25,
  `LODdist_VeryFar` 50. The selection (`sub_824351D8`, per car each frame) takes the car's
  distance to the camera, less its size, over the camera's zoom: LOD0 within 15 m, LOD1 to
  25 m, LOD2 to 50 m, LOD3 beyond.
- `CarLODTransDistTraffic` (`sub_82E99A08`), for traffic: `LODdist_T_LOD2`-`T_LOD5` 15, 25, 50,
  100 (LOD4 beyond 100 m), `T_LOD4_Closest` 20, `T_LOD5_Closest` 80.
- `CarLODThresholds` (`sub_8247C5C8`): 2, 4, 6. Before the selection, the cars are ranked by
  distance (`sub_82477368`): only the two nearest cars other than the player's may use LOD0,
  up to the fourth LOD1, the rest LOD2 at best; from six cars on, both counts drop by one.
  The selection then clamps to that minimum and to the LODs the car has loaded.

In free roam that leaves almost every other car on a simplified model: at the spawn and on the
first two minutes of County Route 203, 92% of the picks were LOD3 or LOD4 (table below).

### What the levels do

| Level | Distances | Cars allowed LOD0, LOD1; one fewer each from |
| --- | --- | --- |
| Console | 15 / 25 / 50 m, traffic 15 / 25 / 50 / 100 m | 2, 4, 6 cars |
| High | x2: 30 / 50 / 100 m, traffic up to 200 m | 4, 8, 12 |
| Ultra | x4: 60 / 100 / 200 m, traffic up to 400 m | 8, 16, 24 |

`src/hooks/car_detail.cpp` scales what the three getters return; the stored settings stay the
game's, so Console calls them unchanged and a change applies at the next frame. The selection
still clamps to the models the car has (its LOD count); no streaming stalls, missing models or
extra hitches showed in the runs below.
`tests/car_detail_test.cpp` checks the levels.

### Pop-in

`FORZA_CAR_LOD_STATS=1` logs, every 5 s, how many LOD picks the selection made, how many
changed a car's LOD from its last pick (a model switch: the pop-in) and at what distance from
the camera (to the car's centre, the selection's own measure before it takes the car's size
off), and the share of each LOD. Title Update 4, a career save, chase camera, 1280x720, one run
per level: two minutes of free roam from the spawn with `tools/autodrive.py` on a
`tools/nav_route.py` loop (County Route 203 to Route 28, the same road each run; the traffic
differs run to run; about 24 cars picked a frame, most of them traffic out of sight or far away):

| Level | Model switches | within 25 m | 25-50 m | 50-100 m | 100-200 m | beyond 200 m | Picks at LOD0-2 | LOD3 | LOD4 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Console | 111 | 28 | 31 | 50 | 2 | 0 | 7.5% | 30.2% | 62.4% |
| High | 120 | 0 | 33 | 34 | 53 | 0 | 13.1% | 30.2% | 56.7% |
| Ultra | 140 | 0 | 0 | 36 | 32 | 72 | 26.2% | 37.2% | 36.6% |

A car still goes through its models as it comes closer, so the count of switches stays about the
same (it grows a little: the farther bands hold more cars); what changes is where they happen.
On Console half of them are within 50 m, a quarter within 25 m of the camera; High has none
within 25 m, Ultra none within 50 m. An earlier run of the same drive per level (without the
distances) gave 93, 132 and 160 switches and the same shift of the picks.

The race grid (GoPro Millennia Masters, the seven CPU cars standing 5 to about 60 m ahead during
the countdown, 10 s): LOD0 in 36% of the picks on Console (the player's car and two more), 62%
on High and 90% on Ultra; LOD3 15%, 0.2% and 0%.

Frame times over the drives (60 fps cap): no level adds hitches (frames over 25 ms, two runs a
level: Console 41 (a cold shader cache) and 13, High 13 and 25, Ultra 12 and 8; none over 50 ms).

### Cost

Under a 60 fps cap nothing measurable on an RX 9060 XT: the drives above hold 60 fps on every
level (mean frame 16.67-16.68 ms, p99 18.8-20.1 ms). Uncapped:

Free roam spawn, the car standing, traffic passing; TU4, RX 9060 XT (RADV), uncapped (`--frame_rate=240 --vsync=false`), 1280x720 window; the levels switched live with `FORZA_CAR_DETAIL_FILE` in one session per render scale (Console, High, Ultra, Ultra, High, Console, Console, High, Ultra; 15 s each, the first 3 s skipped), BENCH lock held. GPU = the process's DRM engine time, CPU = the whole process's CPU time, per frame; the mean of the three phases of a level, the range of fps in brackets.

| Render scale | Level | fps | GPU ms | GPU busy | CPU ms |
| --- | --- | --- | --- | --- | --- |
| 1 | Console | 196.2 (182.1-207.5) | 4.29 | 84% | 17.51 |
| 1 | High | 188.2 (168.4-199.7) | 4.47 | 84% | 18.14 |
| 1 | Ultra | 203.4 (193.6-216.2) | 4.13 | 84% | 16.58 |
| 2 | Console | 79.8 (79.5-80.0) | 12.78 | 102% | 28.95 |
| 2 | High | 79.2 (79.1-79.3) | 12.82 | 101% | 29.36 |
| 2 | Ultra | 79.1 (78.9-79.2) | 12.85 | 102% | 29.37 |

Within the noise: at render scale 1 the phases spread by 20-40 fps with the traffic passing
(the ranges of all three levels overlap, and Ultra came out fastest); at render scale 2, where
the GPU is the limit, High and Ultra cost about 0.05 ms of GPU time a frame (under 1% fps). The
spawn has few cars near enough for the levels to change their models; a busier scene (the race
grid, traffic jams) costs more triangles, but the cars are a small part of a frame.

### Screenshots

Kept out of Git. At fixed points of the drive above (15, 30, 45, 60, 90 and 120 s from the
spawn, 1280x720) the three levels give the same road with different traffic, so they compare the
world, not the cars: no other change. On the race grid during the countdown, switched live
(`FORZA_CAR_DETAIL_FILE`, the frames a second apart), the seven CPU cars 5 to 60 m ahead look
the same on all three levels at 1280x720: those LODs differ in what a car of that size on screen
does not show (the switch is a visible pop only when it happens near the camera, which is what
the levels move away). The differences between the frames are the camera settling and the crowd.
Photo mode cannot show it: the selection does not run while the world is paused there (no picks
are logged), so the cars keep the models they had when it opened.

## Paint job detail (#659)

**Settings > Display > World detail > Paint job detail** (`livery_bake_scale`): Console (1,
default) or 2x. Vinyls and decals look sharp in the Paint Shop and soft when driving and in
photos; 2x draws the paint job for driving at twice the game's resolution (photo mode's own
copy stays at the game's size, below). It applies from the next start.

### How the game draws a paint job

The Paint Shop draws the side being edited into a 2048x2048 layout every frame. For driving and
photos the game draws the whole design once, when the car loads, through a chain of render
textures (TU4 addresses; disc ones in `src/hooks/livery_bake.cpp`), all made with
`sub_82CF9058`:

| Texture | Size (Aventador) | Made by | What it holds |
| --- | --- | --- | --- |
| `LivInt-<n>-0` to `-4` | 640x150 to 1882x724 | `sub_82D4DBC8` | each side's layers |
| `LivRenderTexture` | 1024x1024 (Paint Shop 2048), 8:8:8:8 | `sub_82D4BD60` | the sides put together in the car's texture layout (`sub_82D511B8`, at rectangles given in pixels of the 1024x1024 layout) |
| `LivRenderPristineN`, `LivRenderDamageN` | 1024x1024, 6 levels, 8:8:8:8 | `sub_82D53420` | the layout without and with damage, and its mips (`sub_82D54030`, `sub_82D50040`: drawn in strips that fit the 10 MB of EDRAM) |
| `LivPristineN`, `LivDamageN` | 1024x1024, 6 levels, DXT5 | `sub_82D5B3D0` | the same compressed on the CPU (`sub_825AC118`, `sub_82CFAB18`, through two 1024x1024 scratch textures from `sub_8259A318`): the textures the car is drawn with |

(`LivRenderPristine0`/`Damage0` and `LivPristine0`/`Damage0`, 2048x2048, are made only when a
setup's flags ask for them; no car seen here did.) The whole car (sides, roof, bumpers) shares
the 1024x1024, so a decal gets about half the texels it has in the Paint Shop. Render scale does
not help: the CPU compresses the guest's 1024x1024 copy, so the car is drawn with a 1024x1024
DXT5 texture at every render scale.

### What 2x does

`src/hooks/livery_bake.cpp` makes every 1024x1024 texture of the chain 2048x2048 (with one more
mip level where it has mips, so the smallest stays 32x32), the layout size the draws read (+60
of the setup), the sides' rectangles for the blit into the layout, and the compression's
scratch textures with them. The draws, EDRAM strips, mips and the compression follow the sizes.
The Paint Shop's own 2048x2048 layout and the sides (`LivInt`, already up to 1882 wide) stay as
they are. `tests/livery_bake_test.cpp` checks the sizes.

Each step matters: with only the render textures scaled, the CPU compression read them as
1024x1024 (the car showed a magnified corner of the layout); with the compressed textures too
but not the scratch ones, the car got DXT5 garbage; with the layout too but not the sides'
rectangles, the sides landed in a quarter of the layout and the car showed no decals. Only a driving
car's paint job is scaled (mode 6 or 8 at +232 of the setup, whose final textures are the DXT5
pair). The Paint Shop's editor (+236 -1) has its own 2048x2048 layout with its sides placed in
it; scaled too, the editor's car was black and torn. Photo mode draws the car's paint job again
for the photo (mode 7), into two uncompressed 8:8:8:8 1024x1024 final textures instead of the
DXT5 pair; at 2048x2048 those did not fit next to the car's own textures (the texture was not
made and Take Photo crashed), so photos keep the game's 1024x1024, without DXT5 compression.

Photo mode's copy of the paint job (+236 is 2; a car's own is 1) draws no sides. Once a car's
paint job is drawn, the game saves its layout, compressed, as the layout's size squared x 4
bytes (`sub_82D4E7D0`, queued by the setup's steps in `sub_82D5C5F8`), and the copy loads that
save into its own layout (`sub_82D4E9F0`), as many bytes as its own layout holds. At 2x the save
is 2048x2048 (16 MB raw) and a 1024x1024 copy read its first 4 MB: a quarter of the design, cut
into bands and moved, so in photo mode decals were gone or in the wrong place and vinyls had
other diagonals and hard cuts (#680). The hook keeps each save's size with its address. The copy
keeps the game's 1024x1024 layout; when its save was 2048x2048, the hook reads the whole save in
four 4 MB parts (`sub_82D3E878`, through the layout's own memory) and halves it into the layout,
each texel the average of 2x2 through the Xenos tiling. The photo's final textures are
1024x1024 either way. A first version made the copy's layout 2048x2048 instead: photo mode's
heap has no room for that, and from the heap of the render copies it was not made once a
damaged car's copies were there (the game stopped entering photo mode, 1 in 6 on Windows and on
the first entry after a crash into a wall here).

The CPU compresses the render copies (`LivRenderPristineN`/`DamageN`) from guest memory, which
the GPU's resolves write. With `readback_resolve = some` (the default) a resolve is copied back
only when its key is new; at 2x the damage copy's was not, so `LivDamageN` came out the same as
`LivPristineN` (one texture, one fingerprint in a texture dump) and the car showed no scratches
when driving (#680), while photo mode, which draws its own uncompressed damage copy, had them.
With `readback_resolve = full` the 2048x2048 `LivDamageN` has the scratches. While a paint job is
drawn at 2x, from its first render copy until 2 s after its last compression, the hook puts all
of physical memory in `readback_resolve_wait_range` (`src/platform/readback_wait.h`, owner
`kLiveryBake`), the way the online photo does at its shutter: those resolves wait for the GPU
and are copied back. That is a car loading with a paint job at 2x, about a second; Console
never asks for it.

### Memory

| Texture (per car with a paint job) | Console | 2x |
| --- | --- | --- |
| `LivRenderPristineN` + `DamageN` (8:8:8:8, mips) | 2 x 5.3 MB | 2 x 21.3 MB |
| `LivPristineN` + `DamageN` (DXT5, mips) | 2 x 1.3 MB | 2 x 5.3 MB |
| `LivRenderTexture` | 4 MB | 16 MB |
| scratch textures (during the compression only) | 5 MB | 20 MB |
| Total kept | about 17 MB | about 69 MB |

The game makes these from memory it reserves at start for render textures: the guest's
physical memory in use was the same at Console and 2x (357 MB in free roam). The graphics card
holds a copy of each (the render ones at the render scale squared) and the EDRAM strips while
the paint job is drawn. Only cars with a paint job pay: in single player that is the player's
car (the 7 CPU cars of the GoPro Millennia Masters race and the traffic have none); online,
each player's car with one. 4096x4096 (4x) does not fit: the first such texture was not made
and the game stopped on the missing livery, so the option stops at 2x.

### Checked

Title Update 4, Linux/RADV, the Aventador with an Amsoil decal on the rear bumper and on the
right side (placed in the Paint Shop on a copy of the test save), free roam at the festival,
chase camera at the spawn, the same frame at both levels. At 1280x720, render scale 1, the
decal's outlines are slightly crisper at 2x; at render scale 2 (2560x1440) the difference is
clear: at Console the letters' edges show DXT5 blocks and texel steps, at 2x they are smooth
and the black outlines thin and even. At 2x the Paint Shop's menus and decal editor show the
car and its design as at Console, and the car after leaving it carries the design. Take Photo
works at 2x in free roam and after the Paint Shop, and a race (GoPro Millennia Masters, 7 CPU cars, test save) ran 35 s with no paint job made. No
errors in the logs. Not checked: online
lobbies with many paint jobs, Windows, the frame time while a car loads, and the disc build
(its functions come from `tools/tu_address_map.py`, it compiles, it was not run).

Photo mode's copy (#680), same save: the layout the car saved and the one photo mode loaded,
dumped from guest memory and untiled. At Console both are the same 4 MB, byte for byte. At 2x
before the fix the save was 16 MB and the copy's layout a cut-up quarter of it (the rear decal
in four bands at the bottom of the layout, the side decal missing); a 2048x2048 copy loaded all
16 MB, byte for byte the save; the save halved as the hook does it differs from the Console layout
by 0.013 on average per byte (0 to 255), so the design is where the Console one has it.

Scratches (#680), same save, a texture dump of free roam: at Console `LivPristineN` and
`LivDamageN` are two 1024x1024 DXT5 textures, the damage one with scratches over the body. At 2x
before the fix the dump held a single 2048x2048 DXT5, the pristine one (the damage texture had
the same picture); with `readback_resolve = full`, and after the fix with the default `some`, it
holds both, the damage one with the same scratches as at Console (the same fingerprint in both
runs).

### The decals themselves

Vinyl shapes (`media/Livery.zip`, `vinyls/*.carbin`, 880 files) are meshes and stay sharp at
any size. Decals (`decals/*.carbin`, 490 files, 478 different pictures) are bitmaps: 256x256 (7
of them 256x128) 8:8:8:8 with 9 mip levels, one per file. A decal stretched over most of a side
(about 900 pixels of the 2048x2048 layout for the Amsoil one above) has a quarter of the layout's
texels at 2x, so it is the next limit. They load as ordinary textures, so the launcher's **Make
decal pack** (Texture packs page, [texture packs](texture_packs.md#make-decal-pack)) upscales them
2x or 4x from the game files into a texture pack.

Checked (same save, Title Update 4, render scale 2, Paint job detail 2x, the car's side at the
festival spawn): with the decal pack the Amsoil decal's letters and outlines are crisp and the
registered mark readable, where the game's decal shows soft, stepped edges; 4x is a little
crisper than 2x there. On the rear bumper, where the decal is small, the pack changes little. The pack must be in memory when the car loads (the paint job is drawn once
then), which is why the decal pack asks to be read at start (its `pack.toml`, SDK patch 0436).
Without that the car showed the game's decal.

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
