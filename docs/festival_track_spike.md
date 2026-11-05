# FM4 circuits in FH1: the track spike (#648)

Work notes for putting a Forza Motorsport 4 circuit (Suzuka so far) into FH1 TU4's free
roam. Everything here is dev tooling: it reads the player's own FM4 and FH1 files and writes
converted files next to a copy of the game folder. Nothing from either game is in the repo.

## Pipeline

1. `tools/fm4_track_mod.py` builds a mod from FM4's track folder (`media/tracks/<track>`),
   the TU4 `media.zip` and FM4's game database: the track's files, a gamedb that makes free
   roam load the circuit (`--free-roam 317` for Suzuka) and the gamemode files.
2. `tools/fm4_pvs.py convert MOD FH1_DISC LOOSE` converts the mod's layouts (ribbons):
   - each PVS from FM4's version 27 to FH1's 50, split into 100 m hex zones (`.hex`,
     `__RnnZnnnnn.pvsz`, `FilenameMap_nn.dat`, `PVSZLookup_nn.dat`); big models go into every
     zone their extent reaches, and each zone also takes the large pieces (25 m and over)
     of the FM4 visibility cell that best matches its centre (`FM4_PVS_VISIBLE*`);
   - texture records (FM4's atlas flag 8 and projected-lightmap flag 0x10 dropped), shader
     names, instance flags (high half 0x083F, FM4's low 16 bits, bit 28 dropped);
   - the sky dome as an object of every zone; one padding zone-table entry that sizes FH1's
     zone pools (`FM4_PVS_POOL_ZONES`);
   - a ground under the circuit, in every zone: FM4's flattest terrain piece placed again 15
     times wider, 4 m under the lowest ground (`FM4_PVS_GROUND*`);
   - each ribbon's time-of-day curves (`TimeOfDay*.xml`): Colorado's, every curve held at
     13:00 (`FM4_PVS_TOD`, minutes; `keep`, `off`);
   - FM4's track shaders patched for FH1's inputs (`tools/fm4_shaders.py`: the shadow map and
     the projected track lightmap, whose sampler 13 holds FH1's shadow mask, read as 1;
     the lightmap colours and the shadow colour `shad_color` made constants, from each
     shader's own literals);
   - each ribbon's `.col` in FH1's cell layout (`tools/fm4_col.py`);
   - the track archive `media/tracks/<track>/bin.zip` in FH1's layout (stored members, data
     offset in extra field 0x1123, files ordered by CRC-32 of the lower-case path) and a
     `media/zipmanifest.xml` that mounts it.
   The loose files go over a copy of the game folder; the mod goes through `--forza_mods`.
3. `tools/fm4_line.py TRACK.col TRACKSEGCAMS.xml OUT.txt [VMAX]` builds a driving line for
   the dev autopilot (asphalt path between the trackside cameras, centred, 3.5 m from walls).

## Dev switches (forza-recomp/src/hooks/fm4_track.cpp, src/debug/autopilot.cpp)

| Variable | What |
| --- | --- |
| `FORZA_INSECURE_FILES='zipmanifest.xml\|\tracks\<track>\'` | lets the changed manifest and the track's files open as plain files |
| `FORZA_FM4_STX='<Track>\|LOOSE/<track>_stx_numbers.bin'` | FM4's static texture pack read as FH1's |
| `FORZA_TRACK_SPAWN=x,y,z[,fx,fz]` | the player's teleports go there (facing fx,fz); FM4TRACK diagnostics on. Suzuka's grid: `605.6,598.5,-134.1,0.61,-0.79` (15 m behind FM4's first AI waypoint, `media/AI/Tracks/suzuka_00.geo`) |
| `FORZA_FM4_COL=1` | the collision manager reads every cell from the `.col` (no `.fiz` streaming) |
| `FORZA_FM4_STREAM_POOL=1` | more streaming file blocks when the 23 MB pool runs dry |
| `FORZA_FM4_ZONE_POOL=n` | n x the live-instance and placement pools (not needed with the padding zone) |
| `FORZA_TRACK_NO_TRAFFIC=1` | the other cars go far below the circuit |
| `FORZA_AUTOPILOT=LINE[,scale]` | drives the line (pure pursuit, speed from the line x scale) |
| `FORZA_AUTOPILOT_TRACE=FILE`, `_DELAY=s`, `_GAIN=k` | 10 Hz wheel/input trace, a start delay, the steering gain |
| `FORZA_ZONE_TRACE=1` | zones made live/dropped with the pools in use |
| `FORZA_FIZ_TRACE=1` | collision queries, depot reads, teleport orientation |
| `FORZA_PVS_TRACE=FILE` | every read of the PVS reader (layout work) |

Converter test switches: `FM4_PVS_FLAGS`, `FM4_PVS_FLAGMASK`, `FM4_PVS_SHADERS`,
`FM4_PVS_PROJLM`, `FM4_PVS_SKY`, `FM4_PVS_SHADER_PATCHES`, `FM4_PVS_LIGHTMAPS`, `FM4_PVS_HEX`,
`FM4_PVS_EXTENT`, `FM4_PVS_VISIBLE`, `FM4_PVS_VISIBLE_NEAR`, `FM4_PVS_VISIBLE_CELLS`, `FM4_PVS_VISIBLE_SIZE`,
`FM4_PVS_TOD`, `FM4_PVS_TRACKSETTINGS`, `FM4_PVS_TRACKSETTINGS_FM4`, `FM4_SHADERS_LIGHTMAP`,
`FM4_SHADERS_LIGHTMAP_COLOURS`, `FM4_SHADERS_SHADOW_COLOUR`, `FM4_SHADERS_SHADOW_CLIP`, `FM4_SHADERS_TINTS`, `FM4_PVS_PASS_GROUPS`, `FM4_PVS_PASS_GROUP_DROP`, `FM4_PVS_GROUND`, `FM4_PVS_GROUND_SCALE`, `FM4_PVS_GROUND_BELOW`, `FM4_PVS_GROUND_MODEL`,
`FM4_COL_NORMALS`, `FM4_COL_WALL`, `FM4_COL_EDGES`, `FM4_COL_REMAP`, `FM4_COL_GRID`,
`FM4_COL_SURFACE`, `FM4_COL_KEEP_DEGENERATE`; `FM4_DISC` (FM4's disc folder) for the
surface map.

## What works

- Suzuka (Ribbon_00) loads in free roam, renders buildings, stands, crowds, trees, grass,
  fences, the road texture and FM4's horizon landscape.
- Collision: the car drives, hits walls (they slow it), falls and lands. A full lap with the
  autopilot at about 34 km/h: 615 s, line held within 2.6 m, no crash.

## Findings worth keeping

- FH1's collision cell: 0x90-byte header (route ids at +0x3C, count +0x7A, mask +0x7C), 16-byte
  vertices with a packed normal (z, x signed 11 bits, y 10 bits), 32-byte triangles
  (flags 0xTTMM8000: TT 0x0E ground, 0x08 walls; MM surface), 12x12 squares of u16 lists.
- The wheels follow the vertex normals: true (tilted) normals lifted the front wheels at fixed
  spots and launched the car; upright ground normals fixed it (bank no longer felt).
- A triangle's last word 0x00FFFFFF made walls throw the car; FM4's 0 is kept.
- Zone pools are sized 3 x (largest zone + 128) + dynamic instances; hex zones need more.
- Meshes placed by their model (road, terrain, kerbs, buildings: placement at 0, 0, 0) hold
  their vertices with z negated, and their placement's rotation (1, 1, -1) turns them round;
  FH1 applies it, as for Colorado's. The converter zones them by the rotated centre: zoned by
  the stored one, most of the road near the camera was never loaded.
- FM4's `clr_0` shader draws nothing under FH1, so it can't be used as a flat-colour test.
- FH1's hex zone (col, row) has its centre at origin + (1 + 1.5 col, sqrt3/2 + sqrt3 row +
  sqrt3/2 if col is odd) radii (TU4 sub_8320EA30/sub_8320EAA8, the `.hex` reader
  sub_8320EBF0). The converter had 2 + 1.5 col, so every zone's contents sat 100 m off the
  zone FH1 draws from; road a few metres ahead was in a live zone but not drawn. FH1 draws
  the camera's zone (`FORZA_ZONE_TRACE` lists the live ones; the first is the camera's).
- FM4's PVS cells are visibility sets with no position, in order round the lap. Bigger zones
  (everything over 25 m from them) gave a flat black road, missing ground and cyan, green
  or blue kerbs and verges that change from run to run, so only pieces of 120 m and over
  were added. The cause was the stale constants (below): with them fixed, 25 m draws right
  and is the default.
- FH1 lights a track from `Tracks\<track>\Ribbon_NN\TimeOfDay{,A,B,Neutral}.xml` (sun,
  sky, clouds, haze, the clock's speed, keyed by minutes after midnight). FM4's circuits have
  none, and Suzuka came out with a deep blue night sky and a moon. With Colorado's curves
  held at 13:00 it reads as day whatever the clock says.
- A placement's rotation may scale: the ground is a terrain piece placed with (15, 1, -15).
- The flat cyan, green, blue or magenta surfaces are stale pixel-shader constants, not
  textures or pipelines. FH1 sets none of the constants FM4's track pixel shaders read except
  by chance (another draw's values left in the register): the lightmap colours
  V2LightmapColor1/2 (c247, c248; lightmap = lerp(c247, c248, texel)) and the material slots
  c360_0..6 (c0-c6: texture tints and UV scales) at least. Shown at one spot (the grid): with
  the same files the road and walls came out blue, then green; with the lightmap fetch forced
  to 1 every lightmapped surface went saturated red, blue or white; with c247/c248 replaced
  (`tools/fm4_shaders.py`, `FM4_SHADERS_LIGHTMAP_COLOURS`) the walls, signs and buildings draw
  right, while the blend surfaces (road, grass, shoulders) keep a random tint from c0-c6 and
  the road constants. Making the texel x c0-c6 multiplies read 1 (`FM4_SHADERS_TINTS=1`, off by
  default) turned the pit wall black: more constants are stale than that.
- Why the constants are stale: a model's materials (in `.rmb.bin`, after the shader names) are
  a header of 8 words, then pass groups: u32 2, u32 n, n 8-byte records (u16 register, u8
  pass, u8 size, u8 kind 0x41-0x49, 0, u16 data offset: material constant blocks, samplers,
  the uvOS constants), u32 m, m 8-byte entries, 3 sizes. The record layout is the same in
  both games, and each material's float block (the c360_N values: tint, tiling, e.g. 0.50,
  0.57, 0.49 then 4, 4, 1, 50 for TERR_SZKA_014) sits just before the groups. The order
  differs: Colorado's run populated, empty, populated (main pass first, `h_road...`), FM4's
  start with empty groups and put the populated one (25 records) after them (TERR_SZKA_014:
  E E E P, E E P, E E P, E E P, E E P, E E E E E). FH1 most likely takes the first group as
  the main pass and finds no records, so none of the material's constants or sampler states
  are set. The records turned out to be command-buffer fixups: TU4 sub_82D19210 switches on
  the first word's low 4 bits (1, 2 vertex/index addresses; 3; 4 viewport floats; 5; 6
  scissor; 7, 8; 9 a constant-data address; 10 an address). FH1's main passes use 1, 2, 3,
  8, 9; FM4's also 4, 5, 6, 10. Swapping FM4's first populated group into group 0
  (`FM4_PVS_PASS_GROUPS=1`, a test) makes FH1 run them: type 4 faulted on a null pointer
  (FH1 passes no viewport data); with 4 and 6 made type 0 (skipped) the track loaded, but the
  blend surfaces drew flat purple, and black with 5 dropped as well. So the group order is not
  the whole story: the constants still do not reach the shaders. Next: the hook (upload each
  material's float block, the c360_N values, and FM4's V2LightmapColor defaults per draw).
  With the swap on and fixup types 4 and 6 skipped (5 kept), two full 9-spot passes in a row
  gave different colours at every spot (road grey at some spots in one run, purple, red, blue
  or black in the other): the swap does not make FM4's constants reach the shaders reliably.
  The road pieces also keep a populated group 0 of their own (ROAD_SZKA_063: types 9, 10, 3,
  8, no constant block), so the swap leaves them alone; swapping two populated groups would
  also need their data moved (after the groups: the command buffers, sum of the groups'
  first sizes, header word 3; constant data, header word 4; fixup locations, the third
  sizes).
- Toward the hook (not built): the set-fixup calls (TU4 sub_8248B570, the same signature as
  sub_82D19758: handle, type, register, data) come from sub_8248B5D0 (types 1, 2: vertex and
  index buffers), sub_8248B2D8 (3, 8), sub_8248C200 (9, 10) and sub_8249E4E0 (4, 5, 6, per
  frame: type 5 takes device values, not material constants). So FH1 does not set material
  constants through fixups. Each group's command buffer is a precompiled D3D command buffer
  (a header of masks and sizes, then register-write entries such as 0xC4002487); FM4's and
  FH1's differ in their entries, likely an older XDK's format. The material constants must
  come in where FH1 draws a mesh without a command buffer for that pass (FM4's, with an
  empty group 0): its effect pass and constant commit. That path, and FH1's pixel-constant
  setter, are the hook's place; not found yet.
- FH1's own track shaders need the same: every Colorado road and blend shader reads per-
  material c360_N constants (c0-c6), which FH1 gets from each mesh's group-0 command buffer.
  FM4's road drawn with FH1's H_ROAD_DIFF1_MODULATE (`FM4_PVS_SHADERS`) faulted in
  sub_8248C200 (setting the type 9/10 fixups FH1's effect expects in that buffer). So moving
  the materials to FH1's shaders also needs FH1-format command buffers in group 0; both that
  and converting FM4's buffers come down to writing FH1's precompiled command-buffer format
  (a header of state masks and sizes, then tagged register-write entries: 0x84.., 0x88..,
  0xC4.., 0xC8..), not decoded yet.
- PM4 decoding (the SDK's packet layout): a group's first data area is the buffer's header
  and state masks; the second (header word 4 bytes, from the end of the first) is plain PM4
  in both games: NOPs, type-0 writes of VS c0.. and PS c0..c7 (FM4's carry the material's
  tint and tiling, e.g. PS c0 = 0.503, 0.565, 0.485, 0.518 and c1 = 4, 4, 1, 50 for
  TERR_SZKA_014), PS c116..c127 (zeros, patched by type 8: the uvOS), IM_LOAD (shaders),
  LOAD_ALU_CONSTANT, the fetch constants, viewport and scissor registers, DRAW_INDX (index
  address 0xFFFFFFFF, patched). FH1's (Colorado's) have the same shape: group 0 is a
  one-shader pass (depth), groups 3 and 6 the colour passes with two shaders, as FM4's group
  3. The fixup kinds match too (5/0x41, 4/0x43 viewport, 6/0x42 scissor, 9, 10, 3, 8), so the
  formats are compatible. Skipping FM4's fixup types (`FM4_PVS_FIXUP_DROP`, a test): 4
  removes most draws (viewport), 6 changes nothing visible, 5 breaks the framing; none makes
  the colours right. So FM4's buffers, with their constant writes, are most likely not what
  FH1 draws FM4's colour pass with; the next step would be a run-time trace of the PS
  constants at each draw (a command-processor IssueDraw wrapper) to see which path draws
  them.
- The run-time trace (ReIGlue, a scratch build only: `FORZA_GPU_PS_TRACE=FILE`, logging starts
  when `FILE.trigger` appears; `_FILTER` a list of pixel-shader hashes from
  `tools/fxobj_shaders.py list` of the converted `bin.zip`; per draw the pixel shader, PS
  c0-c7, every float constant the two shaders read, and for each the packet and buffer that
  last wrote it) settled where FM4's constants come from. Every FM4 track draw comes from
  FM4's own command buffer: ring, FH1's indirect buffer, then the pass group's buffer
  (depth 3), and PS c0-c7 at the draw are the ones that buffer wrote just before it
  (TERR_SZKA_014: 0.503, 0.565, 0.485, 0.518 and 4, 4, 1, 50; age 0). So the material
  constants were never the problem, and the c0-c6 conclusion above was wrong. What FH1 does
  not set are FM4's globals, which keep FH1's own data in those registers: PS c240
  `shad_color` reads (denormal, NaN, 2.99, denormal) in the road, shoulder, grass and
  barrier shaders, and VS c149-c151 `shad_clip_near` and c220 `shad_clip_misc` the same kind
  of bit patterns. These shaders end with lerp(shad_color, 1, shadow), so the NaN coloured
  the whole road (flat teal at the grid in both traced runs). The other globals they read
  (c214 SunGainScale, c223/c224 RoadSpecularParams, c242 FogColor, c249-c251 TimeGain, sunDir,
  sunColor) hold finite values. Also found: the shaders' literal constants (c252-c255, from
  the blob's definitions table) differ per shader; c255 is (1, 0, 0, 0) only in some, and
  many shaders have none, so the lightmap-colour patch's "c255.x, c255.y" made some lightmaps
  black, 1.5x or constant. Both patches now take the 1 and the 0 from the shader's own
  literals (none: the shader is left alone; FH1 then left c247/c248 at (0, 0, 0, 1) and
  (1, 1, 1, 1) in the traced frames, the identity lerp), and `shad_color` reads 1 (the
  negate bits kept, so 1 - shad_color is 0). Result, two 9-spot passes in a row: grey road,
  green grass, red-and-white kerbs, the same in both at 8 of the 9 spots (the grid was flat
  teal before at both traced runs). Spot 4 drew right in the first pass and flat black
  (road, kerbs, verge) in the second; a traced third run there drew right again, with no
  NaN left in the pixel constants, only in VS c149-c151 and c220 (shad_clip_near/misc:
  (8e-40, NaN, 25.18, 9e-40) there, (NaN, 2.99, ...) at the grid: FH1's data, which changes
  by place and run). Those are the next suspect for the black: the vertex shader's shadow
  coordinates feed the pixel shader's shadow selects (cndgt/cndeq on interpolators). (That
  pass ran with three games on the PC; a shot 25 s after loading may also have caught
  pipelines still building.) The vertex shaders now read them as 0 (the literal's 0, from
  the VS blob's own literal block, first register below 256; `FM4_SHADERS_SHADOW_CLIP`, on by
  default): 252 reads in 12 files, every VS that uses them has a literal 0. With it, two
  9-spot passes in a row and three more runs at spot 4 (shot 45 s after loading) drew the
  same everywhere: grey road, green grass, red-and-white kerbs, no black. The zone-size
  glitch is gone too: converted with `FM4_PVS_VISIBLE_SIZE=25` (every FM4 piece of 25 m and
  over in a zone's visible set; the largest zone 504 instances instead of 404), two passes
  drew the same and right, with more of the circuit (stands at the grid). So that glitch was
  the same stale constants; 25 m could be the default once the streaming pool and frame
  time are checked with it. Runs at `--frame_rate=30` (run.sh, `FRAME_RATE`).
- FM4's `TrackSettings.xml` (version 59) lacks FH1's renderer settings (Brightness, Contrast,
  ExposureBias, LightHemiTop/Bottom, WorldClear, bloom, height fog); read as it is, the
  circuit drew far too dark. The converter writes Colorado's with FM4's sun gain, lightmap,
  road and fog values (`FM4_PVS_TRACKSETTINGS`: merge, fh1, fm4).
- Shots for comparisons: a shared shader cache for one's own runs and 25 s after loading;
  with a fresh cache, draws are skipped while pipelines build and pieces go missing.

## Open

- (c) The road, terrain and buildings draw with FM4's own shaders, at 13:00, over a ground.
  Still to do: FM4's shadows (the shadow map reads 1, `shad_color` 1 and the shadow clip
  constants 0: no shadows on the circuit; the shader rewrite they need is below).
- Shadows, as far as looked into:
  - Shadows: FH1's colour-pass track shaders take shadows from a screen-space mask on s13
    (`ShadowMaskSamp`, fetched at the clip position the VS exports, divided by w in the PS);
    the cascades (CSMShadowMapLevel0-2TM c180-c191, s8) are only read by the passes that draw
    that mask, and they are depth maps FM4's grayscale fetch cannot compare against. FM4's VS
    exports no screen position (o3/o4 are M_shad_near/far x the world position), so real
    shadows mean rewriting each shadowed FM4 shader pair: o3 := the clip position (slots
    32-35 of ROAD_BLND_2's VS: dp4 with c128-c131 and r4), and in the PS a reciprocal of w,
    the 0.5 scale and bias (no 0.5 among FM4's literals) and the s11 fetches turned into s13
    with serialisation, which needs instructions inserted (control flow rewritten). Not done.
    FM4's own shadow colour is TrackSettings ShadowColor (0.389, 0.460, 0.565); with the
    shadow term at 1 it changes no pixel, so shad_color stays 1 until there is a shadow source.
- Ground: a pass with `FM4_PVS_GROUND=0` showed where the ground is seen: the holes past the
  circuit's edge, as at spot 9 (behind the fence); the pale areas at spots 4, 6 and 7 are FM4's
  own horizon landscape and run-off, the same with the ground off. The stretched piece took a
  corner of its blend mask everywhere (pale sand and patches) whichever flat terr_blnd piece
  it was (900, 866, 855, 272, 230, 503 tried). `FM4_PVS_GROUND_MASK=n` (a test, off) draws it
  with a copy of its model whose mask (the texture record after its lightmap's atlas tile)
  is texture n: at 120 m, 621 made the ground green at spot 9 and changed nothing else in a
  9-spot pass, 3 made it white (the other layer). But with the 25 m visible set, 621 left a
  hole at the grid's left verge (the horizon showing through, still after 60 s), where the
  ground without it draws pale green and white: 621 is likely not a flat texture (it looks
  bluish and clouded there). Not the default. Next: find (decode) a really flat texture in the
  static pack, or point the mask at the grass layer's own texture and check which channel the
  TERR_BLND shaders blend by. The copy is <prefix>.NNNNN.rmb.bin with the next model number
  (Ribbon_00 and 02; 01 picks another ground model and keeps it as it is). Sheets (trk-spike,
  not in the repo): shots/suzuka-spots-ground-mask.png (default, 621, 3 at 120 m) and
  shots/suzuka-spots-25m-mask-off-on.png.
- The visible set's 25 m default (`FM4_PVS_VISIBLE_SIZE`, 120 before): two autopilot runs from
  the grid (`lap.sh NAME 450 0.5` in trk-spike, 5.1 km of the 5.8 km lap, 30 fps cap, frame cost
  CSV and zone trace; `lap_cmp.py`) at 120 m and 25 m: the cap held in both (frame p99 33.8 ms,
  no GPU wait), command processor CPU 2.24 -> 2.39 ms a frame, draws 1385 -> 1443 on average;
  streaming blocks in use at most 24037 -> 34518, the pool topped up once -> three times (4096
  blocks, 4 MB of guest heap, each; `FORZA_FM4_STREAM_POOL`, needed at 120 m as well).
  No crash. So 25 m is the default; the largest zone holds 504 instances (404 at 120 m). Two
  9-spot passes at 25 m drew the same and right (shots/suzuka-spots-vis25-run1-run2.png); it
  also brings in geometry the 120 m set left out, such as the dark back of the grandstand at
  spot 3.
- The streaming block pool still needs `FORZA_FM4_STREAM_POOL`.
- AI, the airport entry, ribbons 01 and 02, other circuits.
