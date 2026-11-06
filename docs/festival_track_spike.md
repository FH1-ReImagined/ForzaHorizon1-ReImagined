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
`FM4_SHADERS_LIGHTMAP_COLOURS`, `FM4_SHADERS_SHADOW_COLOUR`, `FM4_SHADERS_SHADOW_CLIP`, `FM4_SHADERS_TINTS`, `FM4_PVS_PASS_GROUPS`, `FM4_PVS_PASS_GROUP_DROP`, `FM4_PVS_GROUND`, `FM4_PVS_GROUND_SCALE`, `FM4_PVS_GROUND_BELOW`, `FM4_PVS_GROUND_MODEL`, `FM4_PVS_GROUND_LAYER`, `FM4_PVS_GROUND_AT`, `FM4_PVS_STREAM_MARGIN`,
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

## A second circuit: Laguna Seca

To check the tools are not Suzuka's alone: Laguna Seca (one layout, 3.6 km, 0 to 53 m above
sea level, the Corkscrew) went through the same steps (in trk-spike: `reconvert_track.sh
LagunaSeca`, `spots_track.sh LagunaSeca TAG`, `pstrace_track.sh`, and the mod from
`fm4_track_mod.py <disc1>/Media/tracks/LagunaSeca <tu4 media.zip> mods/FM4LagunaSecaFinal
--fm4-db <disc1>/Media/db/gamedb.slt --free-roam 317 --fh1-disc <FH1 disc> --loose loose`).
The mod and the conversion ran unchanged (5238 instances, 224 zones, the largest 710
instances; 33446 streaming blocks asked for). Suzuka-specific assumptions found and removed:

- `fm4_line.py` told the trackside cameras from the in-car ones by height (over 100 m): true at
  Suzuka (600 m), none left at Laguna Seca. In-car cameras are the ones within 20 m of 0, 0
  now. Line: 916 points, 3574 m; Suzuka's line comes out the same as before.
- The grid spawn was found by hand. `tools/fm4_spawn.py` reads FM4's AI line
  (media/AI/Tracks/<track>_NN.geo, MLPData arrays: in FH1's terms a waypoint is (X, Z + 1, Y))
  and gives the grid (15 m behind the start line) and test spots round the lap; it
  reproduces Suzuka's hand-found grid (605.6, 598.6, -134.0, 0.612, -0.791).
- Blend masks from an FM4 atlas: FM4 packs small textures into one (a record with flag 0x8 and
  a rectangle; Laguna's road masks are 1/128 of a 1024 texture, 8 x 8 texels). FH1's records
  have no such flag, so FH1 binds the whole texture, and the road's pixel shader, which samples
  its mask with no offset and scale of its own, read another part of it: the road drew black
  at 6 of the 7 spots shot (the trace showed the road's own constants right and nothing stale:
  layer A x c0 = 0 where the mask is 0). Suzuka's road masks are plain (its atlases are the
  lightmaps, flag 0xC, which do get their rectangle as Light_Map_uvOS). Now a blend shader most
  of whose models take an atlas mask reads the mask as 1, its asphalt layer
  (`fm4_shaders.ATLAS_MASK_SHADERS`, from `fm4_pvs.atlas_mask_shaders`): at Laguna Seca
  ROAD_BLND_TRILIN_2 and RDLINE_BLND_SPEC_OPAC_3, at Suzuka none. The real fix would cut the
  tiles out into textures of their own, or point FH1's fetch at the tile.
- Not solved: Laguna Seca's road still draws black at most spots (the Corkscrew's draws right),
  with the mask patch in and at the 120 m visible set as well. A texture trace (the PS trace now
  logs each draw's texture fetch constants: base, size, format) shows why: the road's lightmap
  sampler (s7) holds a 32 x 32 8888 texture, where Suzuka's road gets its lightmap atlas
  (texture 783, _0x0000030F.bix, 128 x 512 DXT1). Laguna's road lightmap is texture 1323
  (_0x0000052B.bix, 512 x 256 DXT1, the same format), so FH1 draws with a stand-in. Traced
  further (`FORZA_STREAM_LOG=1` logs every streamed file): the black road pieces (e.g. model
  567) have no lightmap record of their own kind (flag 12, atlas | 0x4); their lightmap and
  mask are atlas tiles (flag 9, atlas | 0x1: 1058 records) with no texture file of their own.
  FM4 draws those from the atlas, the static pack's entry of the record's file (857), at the
  record's rectangle. FH1 ties one record to each pack entry (the numbers file); of the
  streamed records only those with 0x4 ever stream (274 of 533 lightmaps by spot 2, none of
  the 1382 flag 8 and 9 records), and the other records of an atlas keep a texture object that
  never gets a texture: the 32 x 32 stand-in, black through the lightmap. Suzuka has 51 such
  records, Laguna Seca 1058, nearly all its road. Tried: the pack entry taking the first
  lightmap record of a file shared with tiles (kept: harmless, no change); pointing every tile's
  texture-table entry at its atlas's object before the user lists are made (the converter
  writes <track>_stx_files.bin, a pack entry per atlas-only record; the hook,
  `FORZA_FM4_ATLAS_ALIAS=1`, a test): the game crashed soon after loading in FH1's per-model
  texture binding (a null read at +0xC, lr 82E18E80), so shared objects are not the way. Next:
  give each tile's own object the atlas's texture (the texture object's layout, from the pack
  loader sub_82E323A0/sub_82D09648 and the binding near sub_82E18FC8), or cut the tiles out
  of the atlas into textures of their own in the converter (Xenos untiling of the pack's
  texture formats).
- Cutting the tiles out (looked into, not done): the atlases are only in the static pack. A
  record's file number is not a texture number: `_0x00000359.bix` is texture 857's own file,
  not atlas file 857, and many atlas files (all of Suzuka's) have no file at all. The pack is a
  sectioned CAFF archive (875 symbols, a `.data` section and others) with no reader in the repo
  yet. The per-piece textures FH1 streams are BIX1 files: a 28-byte header (width, height, mip
  count, the format word, the whole mip chain's size, the base level's size) and the mips 1..n
  in `_0x<number>.bix`, the base level in `_0x<number>_B.bix`, all Xenos-tiled; formats 2, 18
  (DXT1), 19, 20 (DXT4/5), 49 and 59 occur, never 8888. The cut needs the CAFF layout (and
  any compression), the texture headers in it (fetch constants: base, size, format, packed
  mips), untiling, decoding the tile's texels (a tile is 2 x 2 to 64 x 64 texels of a 128 to
  512 texture), and writing a BIX FH1 accepts (re-encoded in the atlas's DXT format, or a
  format FH1 has not streamed from a track before), with flag 0x4 set on the record: more than
  a few hours. The alternative, giving each tile's own texture object the atlas's texture in
  the game, needs the texture object's layout instead.
- Giving each tile its atlas's texture in the game (option 1, time-boxed, parked; Laguna Seca's
  road stays black, the default path is unchanged). What was found:
  - The tile's rectangle does reach the draw: the road's Light_Map_uvOS (c116) is the tile
    record's rectangle, e.g. (0.9648, 0.5293, 0.0078, 0.0039), written into the command
    buffer from the record. So a tile only lacks its texture.
  - A texture object (the table at presentation+2812, one per record): +0 vtable 820016F8,
    +4 the record's UV rectangle (in the PVS record array), +0xC the record's flags (0x104,
    0x101...), +0x10 its user count (u16) and +0x14 the users (8 bytes: u16 model, u32 texture
    slot; sub_82E18FC8 adds a model's slots, into arrays sized when the lists are made, so
    objects cannot be shared: that was the crash), +0x18 user indices, +0x1C the streaming
    resource id of its texture (a streamed record's file; -1 for a tile).
  - The pack loop (sub_82E323A0) maps each entry's record to the entry (sub_82E66690, a map
    local to the loop) and registers the entry's texture as a streaming resource under the
    record number (sub_82E2EAD8 -> sub_82E2E890); when a texture is there, the users' models
    are bound again (the loop in sub_82E2B8F8).
  - Tried, each a test switch, off: registering the atlas texture again under each tile's number
    (models vanished: those ids are the archive's own resources too; removed);
    `FORZA_FM4_ATLAS_IDS=1`, a tile object's +0x1C set to its atlas entry's resource id (no
    change, nothing broken); `FORZA_FM4_ATLAS_MAP=1`, the tiles added to the loop's record map
    with their atlas's entry (no change). So the record-to-texture step FH1 uses for a pack
    texture is still to be found: the binding that takes a model slot's record to a GPU
    texture (inside the rebind the streaming loop calls), next to trace with the PS trace's
    fetch constants on a tile's draw while each switch is on.
  - `FORZA_FM4_TEXDUMP=1` logs the objects of an atlas's record and its tiles, and the
    presentation's fields round the object table, at track load and as zones go live.
- The pack's numbers now take, for a file shared by lightmap records and atlas tiles, the
  first lightmap record (default). A 9-spot pass on Suzuka with it and the 25 m set drew the
  same as before at every spot (shots/suzuka-spots-recheck-before-after.png in trk-spike).
- Still one converted track at a time: the converter writes FH1's zipmanifest.xml with one
  line for its track, so the last converted circuit is the one mounted
  (`reconvert_track.sh` copies it into the game folder).

## Towards players: a plan

What a player would need, beyond the spike (none of this is built yet):

1. **Launcher: Mods -> Forza Motorsport 4 tracks -> Import FM4 tracks...**, next to the cars
   import (#654) and built the same way: it reads FM4 Disc 1 (an image or a folder), lists the
   circuits FH1 lacks, and for each runs what reconvert.sh runs now: `fm4_track_mod.py` (the
   track's files, the database row, the game modes), `fm4_pvs.py convert` (layouts, zones,
   collision, shaders, the archive, `fm4_stream_blocks.txt`). No driving line is needed for
   players (`fm4_line.py` is the dev autopilot's). Output: the track's loose files and
   `bin.zip` must be loose files of the game (the mod overlay only adds members to the update's
   media.zip, and FH1 opens a track's ribbons, sound banks and zipmanifest by path), so the
   import writes them into a package in the user data folder that the game mounts at
   game:\media\tracks\<track>\ (as the cars' DLC-style package is), plus a zipmanifest entry.
   Remove deletes both. Time: Suzuka converts in about 5 s once its files are read.
2. **The game side by default, not by variable**: what the dev switches do must happen for a
   converted track whenever one is loaded (a track folder with fm4_stream_blocks.txt is the
   marker the streaming pool already uses): the plain-file opening of the track's files and
   its zipmanifest (`FORZA_INSECURE_FILES`), the collision read whole from the .col
   (`FORZA_FM4_COL`), the static texture pack numbers (`FORZA_FM4_STX`, its file next to the
   track), no traffic (`FORZA_TRACK_NO_TRAFFIC`), and a start position on the circuit
   (`FORZA_TRACK_SPAWN`: from the track's own data, FM4's grid or AI path, written by the
   converter).
3. **How a player gets there**: today `--free-roam` replaces Colorado with the circuit, which
   no player wants. Proposed: FH1's airfield (the festival site's flights) gets a "fly to
   <circuit>" choice that loads the circuit's ribbon as a free-roam world of its own (the same
   world load with another Tracks row and layout), and a way back to Colorado (the pause menu
   or the airfield's own spot on the circuit). First version: free driving and the car's own
   lap time; then events.
4. **Still missing for that**: events and timing (start/finish and sectors from FM4's
   TrackSegCams / AI files; FH1's race events are Colorado routes), AI and traffic on the
   circuit (FM4's AI paths are in media/AI/Tracks/<track>_NN.geo, not read), shadows (above:
   the shader rewrite), the minimap/GPS (FH1's map is Colorado's), ribbons 01 and 02 and other
   circuits tried, the airfield menu and world switch, the package mount and the Windows
   launcher, and a frame-time check on low-end PCs. The car import's rule holds: only from the
   player's own discs, nothing of either game in the repo or the release.

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
  - Tried (a test, off): FH1's mask at the pixel instead of the rewrite. The converter points
    each s_grayscaleShadowMap fetch at fetch constant 13 and marks it (texel offsets x -16 and
    y 15, raw), keeping its result 1 and making shad_color 0 (`FM4_SHADERS_FH1_SHADOWS=1`);
    a local ReIGlue branch (fh1-648-fm4-shadow-main, not pushed) has the Vulkan translator
    sample that texture at gl_FragCoord over its size for a marked fetch (component 0: 1 lit,
    0 shadow; 2 and 3 read 0 everywhere, 1 none). FH1's own free roam draws the same with it
    (two runs each, 0.69% of pixels differ, against 0.37% between two runs of the shared SDK:
    the traffic car; trk-spike shots/colorado-before-after.png). On Suzuka the car's shadow
    appears, but wrong: a black silhouette in front of the car at 6 of 9 spots and at sunset,
    part of the road white at one spot (shots/suzuka-spots-shadows-off-on.png,
    suzuka-evening-mask-shadows.png). FH1 fills the mask in a collector pass over its own track
    meshes (the variants with CSMShadowMapLevel0Samp and CamPosWorld); FM4's models have no such
    pass, so the mask has no right shadow where FM4's surfaces are. Real shadows on FM4's
    surfaces need either that collector pass for FM4's models (FH1-format pass groups with
    FH1's collector shader) or the cascades read in FM4's pixel shaders (a world position per
    pixel and the CSM matrices, which FH1 sets for its collector draws only).
- Ground: a pass with `FM4_PVS_GROUND=0` showed where the ground is seen: the holes past the
  circuit's edge, as at the grid's left verge and behind the fence at spot 9; the pale areas
  at spots 4, 6 and 7 are FM4's own horizon landscape and run-off, the same with the ground
  off. The ground (model 900, TERR_SZKA_014, stretched 15x) fills them, but in a corner of its
  blend mask: pale sand and green patches. Not solved; what was tried:
  - Another flat terr_blnd piece as the ground (`FM4_PVS_GROUND_MODEL`: 866, 855, 272, 230,
    503): at 25 m none of them is drawn at spots 1 and 9 (the holes come back), only 900 is
    (shots/suzuka-ground-models-25m.png).
  - A copy of 900 for the ground alone (<prefix>.NNNNN.rmb.bin, the next model number;
    `ground_model_copy`): with its mask record pointed at another texture
    (`FM4_PVS_GROUND_MASK`, removed again: 621 drew green at spot 9 at 120 m, but left the grid's
    verge empty at 25 m), or with its own copy of the blend shader that reads the mask as 0
    or 1 (`FM4_PVS_GROUND_LAYER=A|B`, `fm4_shaders.one_layer`; TERR_BLND_SPEC_3 blends Blend_A,
    tinted by c0, and Blend_B by the mask's green channel, fetched at slot 7 into r0.w): not
    drawn at spots 1 and 9 at all, also with the copy given the sky dome's extent (never culled).
    Traced further, and ruled out: the converted zone data (the spot-1 zone lists model 866
    and the ground instance, and its file is in the zone's file list); the instance objects
    (all made when the zone goes live, 0 null of 3757, in the 900, 866 and copy runs alike);
    the instance's own fields (the same flags, LOD half-floats -1, no lightmap for every
    candidate); culling by distance: the ground's origin is 8 to 10 km off the circuit (the
    15x scale moves the piece's own vertex offset), but neither a copy with an extent reaching
    over the whole circuit (`ground_model_copy`, kept) nor 866 centred on spot 1
    (`FM4_PVS_GROUND_AT=x,z`, a test) at scale 15 or 1.5 drew there. What holds in every run:
    the ground draws only with a model that also has its own, normal placement in that zone
    (900 at spots 1 and 9). A second, unscaled placement of the ground's model in every zone
    (tried, removed) did not make the copy draw, and turned the road at spot 9 flat grey.
    Next: the draw itself, with the PS trace (TERR_BLND draws with the ground's world matrix,
    scale 15, at spot 1 for 900 and for 866): whether FH1 submits the draw at all, then its
    per-zone model batches. `FM4_PVS_GROUND_LAYER` stays, off. Sheets (trk-spike, not in the repo):
    shots/suzuka-ground-mask.png, suzuka-spots-25m-mask-off-on.png,
    suzuka-ground-layer-unculled.png.
- The visible set's 25 m default (`FM4_PVS_VISIBLE_SIZE`, 120 before): two autopilot runs from
  the grid (`lap.sh NAME 450 0.5` in trk-spike, 5.1 km of the 5.8 km lap, 30 fps cap, frame cost
  CSV and zone trace; `lap_cmp.py`) at 120 m and 25 m: the cap held in both (frame p99 33.8 ms,
  no GPU wait), command processor CPU 2.24 -> 2.39 ms a frame, draws 1385 -> 1443 on average;
  streaming blocks in use at most 24037 -> 34518, the pool topped up once -> three times (4096
  blocks, 4 MB of guest heap, each, by the dev switch that is gone now: see the pool below).
  No crash. So 25 m is the default; the largest zone holds 504 instances (404 at 120 m). Two
  9-spot passes at 25 m drew the same and right (shots/suzuka-spots-vis25-run1-run2.png); it
  also brings in geometry the 120 m set left out, such as the dark back of the grandstand at
  spot 3.
- The streaming block pool, sized from the track (no dev switch any more): FH1's pool is made
  on the first streamed file (sub_82E3A208: 22440 blocks of 1028 bytes, 23 MB, a free list at
  0x835A0B40), and made again when a world loads. `fm4_pvs.py` writes
  media/tracks/<track>/fm4_stream_blocks.txt: the largest union of a zone's and its six
  neighbours' model files in KB (34226 for Suzuka at 25 m), times `FM4_PVS_STREAM_MARGIN` (1.25):
  42783. When the pool is made, the game (hooks/fm4_track.cpp) reads that file of every folder
  in game:\media\tracks\ and links that many blocks in all, up to 65536 (64 MB); when the
  pool is made again, the same blocks are linked again (none allocated twice). A track
  without the file (Colorado) keeps the game's pool. Should a converted track still empty
  it, 4096 more are added, up to the cap, with a warning naming the file to raise. The union
  matched the blocks measured on a lap within 2% (24454 for 24037 at 120 m, 34225 for
  34518 at 25 m). A full lap at 25 m (6.4 km, past the start line again, 30 fps cap): 42823
  blocks, at most 34630 in use, no top-up, no crash, frame p99 33.7 ms, CPU 2.23 ms a frame.
  For players the file must be a loose file of the track's folder (as it is now): mods'
  files are archive members, which ResolvePath does not list.
- AI, the airport entry, ribbons 01 and 02, other circuits.
