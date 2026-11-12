# FM4 circuits in FH1: the track spike (#648)

Work notes for putting a Forza Motorsport 4 circuit (Suzuka so far) into FH1 TU4's free
roam. Everything here is dev tooling: it reads the player's own FM4 and FH1 files and writes
converted files next to a copy of the game folder. Nothing from either game is in the repo.

Status (converter with insert_fetch_constant): the sections below are in the order the work
was done, so the dark or black road patches the batch sections describe were fixed later ("The
black roads", "Atlas tiles", the lightmap binding, the added fetch constant). Road-level shots
at spots 1 and 5 of all 24 converted circuits show textured, lit roads; the known faults left
are Infineon 5-6's grey road, Indianapolis 7's pale sand and Top Gear 3's orange ground (see
"The last leftovers").

## Pipeline

1. `tools/fm4_track_mod.py` builds a mod from FM4's track folder (`media/tracks/<track>`),
   the TU4 `media.zip` and FM4's game database: the track's files, a gamedb that makes free
   roam load the circuit (`--free-roam 317` for Suzuka) and the gamemode files. With
   `--fm4-ai` (FM4's `Media/AI/Tracks`) also the circuit's road network and race route
   (`tools/fm4_routes.py`, "The road network and the race route" below).
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
| `FORZA_TRACK_SPAWN=x,y,z[,fx,fz]` | the player's teleports go there (facing fx,fz); the game's fatal file errors (the ones its own check stops on, not the 997s of every overlapped read) and its dirty disc message logged. Suzuka's grid: `605.6,598.5,-134.1,0.61,-0.79` (15 m behind FM4's first AI waypoint, `media/AI/Tracks/suzuka_00.geo`) |
| `FORZA_TRACK_DIAG=1` | the FM4TRACK diagnostics: zones made live, zone, texture and resource table checks, files streamed, the texture pack (off on a player's start on a circuit, #825) |
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
  ROAD_BLND_TRILIN_2 and RDLINE_BLND_SPEC_OPAC_3, at Suzuka none. Replaced since: the game
  gives the tiles their atlas (below, "Atlas tiles"), and the patch is off by default.
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

## Which circuits convert today (survey)

`tools/fm4_track_survey.py` (offline) over every circuit on FM4's disc 1. The predictor is the
share of road pieces that take a texture from an atlas tile with no file of its own (Laguna
Seca's black road; Suzuka 1%):

| Circuit | Layouts | Atlas-only records | Models using one | Road pieces using one | AI line | Unseen shaders |
| --- | --- | --- | --- | --- | --- | --- |
| Suzuka | 3 | 32% | 38% | 1% | all | 0 |
| Alps | 3 (+2 PVS v26) | 4% | 21% | 13% | none | 39 |
| FujimiKaido | 1 | 8% | 33% | 15% | all | 17 |
| SunsetInfield | 3 | 25% | 34% | 15% | all | 21 |
| Nurburgring (Nordschleife) | 1 | 27% | 56% | 20% | all | 10 |
| Autopark | 7 | 60% | 51% | 20% | all | 15 |
| MapleValley | 2 | 34% | 26% | 21% | all | 16 |
| AmalfiGP | 1 | 30% | 33% | 26% | none | 20 |
| Infin | 4 | 41% | 23% | 28% | all | 22 |
| LeMans | 2 | 46% | 28% | 41% | some | 32 |
| RoadAtlanta | 2 | 45% | 68% | 42% | all | 13 |
| RoadAmerica | 1 | 69% | 75% | 51% | all | 10 |
| Indi | 2 | 48% | 37% | 51% | all | 24 |
| CrownJewel | 10 | 24% | 59% | 52% | all | 36 |
| LagunaSeca | 1 | 50% | 53% | 55% | all | 12 |
| Sebring | 3 | 78% | 72% | 59% | all | 9 |
| Catalunya | 3 | 59% | 67% | 65% | all | 14 |
| Silverstone | 3 | 9% | 26% | 68% | all | 10 |
| Motegi | 4 | 34% | 62% | 73% | all | 17 |
| Tsukuba | 2 | 28% | 54% | 74% | all | 24 |
| Hockenheim | 4 | 26% | 39% | 75% | none | 21 |
| NurburgringF1 | 2 | 43% | 52% | 78% | none | 21 |
| Mugello | 2 | 34% | 51% | 87% | all | 10 |
| TopGear | 7 | 35% | 63% | 93% | none | 19 |

(Road share: the worst layout. "AI line": FM4's media/AI/Tracks/<track>_NN.geo for the spawn;
without it the grid is found by hand. "Unseen shaders": names Suzuka's conversion has no patch
experience with, each a possible new stale-constant case.) Every circuit but Suzuka has a road
share of 13% or more, most over 40%: the atlas tiles gate nearly all of them, so they came
first (below). No other blocker showed: every layout is PVS version 27 (Alps' 03 and 04 are 26),
every circuit has its static pack, and the model counts (903 to 6314) are in Suzuka's range or,
for the Nordschleife (27366 instances), past anything converted yet.

## Atlas tiles: drawn from their atlas

- A model's draw (sub_82D1B080) fills an 800-byte binding block through the track binder's
  vtable slot 1 (sub_82EAF1B0; vtable 82256734, a sub-object of the track presentation at
  +59700, built in sub_82EB5A40), then applies it as fixups (sub_8248C200: 9, 10;
  sub_8248B2D8: 3 textures, 8 UV offsets; sub_8248B5D0: buffers). The binder reads each slot
  from the model's own texture table (*(draw setup + 1192), entries at +32, their count at +36,
  32 bytes each: +0 the texture's header, +4 a count, +8 8242950C, +16..+28 the rectangle).
- On Laguna Seca the tile slot's entry has a null texture (+0 = 0) and the tile's rectangle
  (e.g. 0.0078, 0.0039, 0.9648, 0.5293), so the binder takes its stand-in: the black road.
- The texture object (+0x1C resource id, its users list) is not where the texture is: even a
  pack entry's own record has +0x1C = -1 and nothing in the 52-byte block at +44 the
  constructor (sub_82EB2BE8) zeroes. The pack loop registers each entry's texture in the
  streaming resource table (manager +16, 12-byte entries by id: +0 the texture, +4 its memory,
  +8 a count; an id already there only counts up) under the numbers file's id.
- Tried, all off: the tiles' objects given the pack's resource id (`FORZA_FM4_ATLAS_IDS`), the
  tiles added to the pack loop's record map (`FORZA_FM4_ATLAS_MAP`), and pack ids moved past
  the archive's file numbers with every pack-drawn record taking its entry's id
  (`FM4_PVS_PACK_IDS=1` with `FORZA_FM4_PACK_IDS=1`: the road stayed black and the terrain lost
  its pack textures too, so the resource id is not what a pack record's slot reads either).
- Rectangles cannot pick the atlas: 99% of tiles share their rectangle with tiles of other
  atlases (a common grid), and a model texture table carries no record number.
- What writes a texture table's +0: the streaming bind loop (sub_82E2B8F8, at 82EB4ED8 through
  sub_82E18E78, lr 82E18E80; found with the write watch, `FORZA_FM4_TEXWATCH`). It rewrites the
  entries every frame, a tile's back to null (a fill made once was undone by the next frame),
  and the binder is called before the draw setup's own body, so the fill has to sit in the
  binder itself.
- The fix (fm4_track.cpp `FillAtlasTiles`, on unless `FORZA_FM4_ATLAS_FILL=0`): the pack loop's
  resource registration (sub_82E2EAD8) is hooked to keep each pack entry's texture header;
  just before the binder copies a table's textures, every tile slot gets the header of its
  atlas's pack entry. The rectangle was already right (the trace: c116 holds the tile's).
- Which table is which model: the table's +4 points at its model's record (presentation
  +55368, 84 bytes each; the index is the converter's model number, checked on 60 tables at
  Sunset Infield). The converter writes `<track>_stx_tiles.bin`: per model (ribbon 0's), each
  slot's pack entry (-1: not a tile) and rectangle; the game takes the model from +4 and fills
  each tile slot whose rectangle agrees with the file. A streamed tile whose own file is in
  has the identity rectangle (the game's own doing) and its texture, so it is not filled; a
  slot with any other rectangle is logged and left alone (Suzuka: a few plain tiles the game
  gives the identity rectangle, model 265's slots 6 and 7).
  First tried, and replaced: finding the model by its slots' rectangles. The atlases share one
  grid, so 13 of 60 sampled tables got another model's atlases that way.
- Streamed tiles (flag 0xC: a file of their own, 128 x 64 for Laguna's lightmaps, the atlas in
  the pack until it streams in) also get the atlas, but only while their slot is null, so the
  own texture wins once it is there.
- With the tiles drawn the converter no longer patches the blend masks to 1
  (`FM4_PVS_ATLAS_MASK=1` brings that back).
- Laguna Seca spot 2 before and after: the black ground left of the road and the black hills
  are sand and grass now (trk-spike shots/spots-lagF4/2.png, spots-lagFD/2.png); 3 null slots
  were left in that view, against 40+ before.
- Laguna Seca, 9-spot pass (lagX1 against lagA, the mask patch run): the black verges, hills
  and run-off are sand, grass and kerbs at every spot, the road has its asphalt and lines
  (it read the mask as 1 before); left: a flat grey road stretch at spot 2 and a dark patch at
  spot 7. A 420 s autopilot lap: 4.23 km, 0 crashes, 33.3 ms mean frame (p99 33.8) at the
  30 fps cap, 27171 streaming blocks at most, none ran out.

## More circuits: Fujimi Kaido and Sunset Infield

Converted with the same steps and no tool changes (`fm4_track_mod.py` as for Laguna Seca,
`reconvert_track.sh NAME`, spots from `fm4_spawn.py <track>_00.geo 9` in
trk-spike/lines/<track>_spots.txt, `spots_track.sh NAME TAG`). Only the converter's tile file
now takes every converted ribbon's models (Sunset Infield converts three layouts, 00, 01, 04;
Fujimi Kaido's other ribbons are left alone, as before).

- Fujimi Kaido (Ribbon_00: 7770 instances, 2231 models, 875 zones of 100 m, the largest 629
  instances; 159 atlas tiles; 29439 streaming blocks asked for): drawn at all 9 spots, 0
  crashes, town, mountain road, guard rails, trees, the tunnel (trk-spike shots/spots-fujX1).
- Sunset Infield (3 layouts, about 2000 instances each, 120 zones; 520 atlas tiles; 62411
  streaming blocks asked for, under the hook's 65536): drawn at all 9 spots, 0 crashes, pit
  building and grandstands, the oval and infield (shots/spots-sunX1). Left: at spot 2 the
  infield grass is a flat red and green, the road there flat grey, and a few billboards black;
  the asphalt looks pale and soft at several spots.
- Lines and laps: `fm4_line.py` takes FM4's AI line (`<track>_NN.geo`) in place of the
  cameras (`fm4_line.py TRACK.col TRACK_NN.geo OUT.txt`): Fujimi Kaido's collision has no
  asphalt path the camera way, Sunset Infield none between its cameras. 420 s autopilot laps
  at 30 fps, 0 crashes, no streaming block ran out:

  | Circuit | Driven | Frame ms mean / p99 | CPU ms mean / p99 | Draws mean / p99 | Blocks at most / pool |
  | --- | --- | --- | --- | --- | --- |
  | Laguna Seca | 4.23 km | 33.34 / 33.77 | 2.39 / 3.63 | 1473 / 1799 | 27171 / 33446 |
  | Fujimi Kaido | 4.15 km | 33.34 / 33.78 | 2.15 / 3.27 | 1354 / 2034 | 16285 / 29439 |
  | Sunset Infield | 4.79 km | 33.34 / 33.76 | 2.65 / 4.18 | 1558 / 2187 | 50377 / 62411 |
- The flat grey roads and dark verges (Sunset Infield's spot 2, Laguna's spot 2, Autopark,
  Maple Valley) are streamed textures (flag 0x4, a .bix and a _B.bix of their own) left on the
  static pack's resident mips (32 x 32), for one cause: the streamed models' vertex and index
  buffers come from one of the game's own fixed-size heaps (sub_825CB038, its XMemAlloc, from
  the buffer helpers 82AA4FC8, vertex data, and 82AA5090, index data, attributes B2800000;
  called with count x stride and count x index size from the model loader at 82E4AD94 and
  82E4AFF4), sized for Colorado, and a model whose buffers get no memory never finishes
  loading, nor binds its textures. At Sunset's spot 2 the heap gave 35 MB of vertex and 5 MB
  of index data, then every further request failed (about 140000 failed calls a minute,
  retried for ever); Fujimi
  Kaido's spot 1 needed less and got everything (`FORZA_FM4_ALLOCDIAG=1` logs it).
  Found by a host thread that, 30 s in, checks every streamed slot of the tables seen:
  applied (the table points into the texture object's own header, +44) or not
  (`FORZA_FM4_FILLDIAG`'s FM4OBJ lines); the binder runs only when a zone goes live, so the
  slot changes later without it.
- The fix (fm4_track.cpp, on unless `FORZA_FM4_TEX_FALLBACK=0`): on a converted track a
  failed allocation from those two callers comes from the guest's 4 KB-page physical heap,
  and the game's free and size calls for it (sub_825CB040, sub_825CB048) go there too. Sunset
  spot 2: 405 textures applied instead of 278 (a few hundred allocations, 9 MB, from the
  fallback), no failures left; the road has its asphalt, skid marks and kerbs.
- The rest at Sunset's spot 2 (about 112 streamed textures, the flat red-green grass, black
  billboards, part of the road) was the zones' texture flags: each zone lists its textures
  with a flag byte (Colorado's: 0 for 88%, 1 for the rest) and the converter wrote 0 for
  all. With 0 those textures were asked for every frame and never loaded (a texture object's
  +8: bit 0x200 loaded, the low 6 bits a request count that ran round and round). Ruled out on
  the way: no allocation failed (sub_825CB038 nor sub_82D81300, `FORZA_FM4_ALLOCDIAG`), the
  users lists were right (+0x10 count, +0x14 8-byte entries: u16 model, u32 slot), and neither
  the TrackSettings ResourcePriority (4x) nor a 2.4 times larger streaming pool changed it.
  With 1 for every texture (`FM4_PVS_ZONE_TEXTURE_FLAG`, default 1 now) all of them load: 699
  textures, 1 left; grass, billboards and road at spot 2 drawn (trk-spike
  shots/spots-sunF1/2.png). What 0 and 1 mean to FH1 exactly is not traced.
- 9-spot passes with flag 1 and the fallback, against the fallback alone (trk-spike shots/
  sunsetinfield-flag.png, laguna-flag.png, autopark-flag.png, maplevalley-flag.png,
  fujimikaido-flag.png, suzuka-flag.png), 0 crashes in all: Sunset Infield better at every
  spot (spot 2's grass, billboards and kerbs; spot 7's dark road; spot 8's fence); Autopark
  better (spot 1's black area gone, the stands and kerbs textured); Laguna Seca and Maple
  Valley the same (Laguna's spot 7 and Maple Valley's spot 3 dark patches stay: another
  cause); Fujimi Kaido and Suzuka the same or with more detail (Suzuka's spot 1 trees, spot 3
  grass and fence).
- Sunset Infield lap with both (420 s autopilot): 4.80 km, frame 33.34 ms mean / 33.77 p99,
  CPU 2.58 / 4.37 ms, draws 1584 / 2219, 50377 streaming blocks at most, none ran out, 0
  crashes; the same as before the two fixes (4.79 km, 33.34 / 33.76, 2.65 / 4.18), guest
  physical memory 356 MB as before.
- Tried and left off: listing each zone's textures' files in its file list as Colorado does
  (`FM4_PVS_ZONE_TEXTURES=1`): no change (FH1 asks for a streamed texture itself), and about
  three times the streaming pool.
- Suzuka re-checked with the tile fill on and the mask patch off (spots-suzX1 against
  spots-suzR): the same at all 9 spots, 0.05% to 3.7% of pixels differ by more than 24 levels
  (clouds, the far trackside), 0 crashes.
- Four more circuits, from the survey, with no tool changes (FM4's AI line gives the spots
  for every one; `--free-roam` ribbon 0 each):

  | Circuit | Ribbon 0 | Zones | Tiles | Blocks asked | 9-spot pass |
  | --- | --- | --- | --- | --- | --- |
  | Alps | 2040 instances, 1301 models | 238 | 54 | 62555 | crashed loading (fixed below) |
  | Nordschleife | 27367 instances, 6314 models | 2226 | 1420 | 37640 | all 9 drawn, 0 crashes |
  | Autopark | 2214 instances, 2555 models | 1026 | 2374 | 53935 | all 9 drawn, 0 crashes |
  | Maple Valley | 1915 instances, 984 models | 176 | 456 | 26308 | all 9 drawn, 0 crashes |

  - The Nordschleife's 27367 instances stream within the pool: its largest zone is 752
    instances, and the blocks asked for (37640) are under the hook's 65536.
  - Alps crashed while the circuit loaded, at every spot, with or without the tile fill and
    without its PVS version 26 ribbons (03, 04) too: a track element's float setter (vtable
    820019BC slot 14, sub_82EADA68: *(this + 16) + 208 = f1) has no null check, unlike its
    sibling (slot 4, sub_82EACA20), and two of Alps' elements have no block at +16 (their
    words 820019BC, a parent, 083F0004, 0, 0); FH1's deferred call queue (8247F9E0) calls it
    for them and writes to guest 0xD0. Not traced which data the elements come from (not
    the collision objects: Alps' are 39 cones, as the Nordschleife's 53 that load; nor
    TrackSettings: the same elements as Fujimi Kaido's). The fix (fm4_track.cpp): on a
    converted track the setter returns when the block is null, as slot 4 does (logged).
    Alps then loads and draws at all 9 spots, 0 crashes (shots/alps-spots.png; a flat grey
    patch beside the road at spot 8).
  - Autopark: the stands, pit wall and road are there; the near road is flat grey and there
    are black patches beside it at several spots, as at Laguna's spot 2 and Sunset's spot 2.
    Maple Valley: dark patches beside the road at spots 2 and 3.
- Suzuka re-checked after the model-number match and the per-slot check (spots-suzX3 against
  spots-suzR, shots/suzuka-recheck-2.png): the same at all 9 spots, 0.3% to 3.3% of pixels
  differ, 0 crashes.
- Sheets (trk-spike shots/): laguna-atlas-before-after.png, fujimikaido-spots.png,
  sunsetinfield-spots.png, suzuka-atlas-recheck.png, nordschleife-spots.png, autopark-spots.png,
  maplevalley-spots.png, suzuka-recheck-2.png.

## More circuits, batch 1: Infineon, Le Mans, Road Atlanta, Road America

Same steps as before (`fm4_track_mod.py`, spots from FM4's AI line, `reconvert_track.sh`,
`spots_track.sh`); with the streaming fixes (physical-heap fallback, zone texture flag 1).

| Circuit | Ribbon 0 | Zones | Tiles | Blocks asked | 9-spot pass |
| --- | --- | --- | --- | --- | --- |
| Infineon (Infin) | 2551 instances, 903 models | 130 | 725 | 46051 | 9/9 drawn, 0 crashes |
| Le Mans | 10634 instances, 4975 models | 805 | 2036 | 37808 | 9/9 drawn, 0 crashes |
| Road Atlanta | 5038 instances, 2071 models | 154 | 888 | 26141 | 9/9 drawn, 0 crashes |
| Road America | 8581 instances, 2179 models | 169 | 2655 | 21850 | 9/9 drawn, 0 crashes |

- Le Mans has no ribbon 0 (layouts 01 and 02, AI line for 01 and 02 only). Its free roam on
  ribbon 1 crashed while loading, in the collision manager just after the collision index
  (a read of guest 0, lr 82DD8FEC). With the same ribbon as ribbon 0 it loads and draws (pit
  straight, stands, crowds): `fm4_track_mod.py` now moves the free-roam layout's ribbon to
  Ribbon_00 when a circuit has none (its files' _NN to _00, the PVS's ribbon field, a u16 at
  16, to 0, and <track>_ribbonNN.sh to ribbon00).
- No new shaders with stale constants seen, no pool near the 65536 cap (46051 the most).
- Left for the polish pass: black ground beside the road at Le Mans spots 2 and 5, Infineon 3
  and 8, Road America 2 (the same look as Laguna's spot 7, Maple Valley's 3, Alps' 8); a flat
  grey road at Infineon spots 5 and 6.
- Lost runs, not the circuits': Road Atlanta's spots 1-6 and Infineon's 9 died at start-up
  with SIGBUS (another session's script removed every unmapped xenia_memory file in /dev/shm,
  a just-started game's too); rerun, all drawn.
- Sheets (trk-spike shots/): infin-spots.png, lemans-spots.png, roadatlanta-spots.png,
  roadamerica-spots.png.

## More circuits, batch 2: Indianapolis, Crown Jewel, Sebring, Catalunya

| Circuit | Ribbon 0 | Zones | Tiles | Blocks asked | 9-spot pass |
| --- | --- | --- | --- | --- | --- |
| Indianapolis (Indi) | 3948 instances, 1541 models | 153 | 1487 | 57265 | 9/9 drawn, 0 crashes |
| Crown Jewel | 1510 instances, 3255 models | 378 | 501 | 73419 | 9/9 drawn, 0 crashes |
| Sebring | 7731 instances, 2118 models | 315 | 3212 | 35228 | 9/9 drawn, 0 crashes |
| Catalunya | 4330 instances, 1831 models | 120 | 2155 | 42348 | 9/9 drawn, 0 crashes |

- Crown Jewel asks for 73419 streaming blocks, past the hook's 65536 cap; its largest need
  without the margin is 58735 and no run ran out (no "ran out" warning in 9 runs).
- The dark surfaces got worse: the road itself black at Sebring (all spots but 3),
  Catalunya (2 to 6), Indianapolis (5, 6, 8) and Crown Jewel (1 to 3; 4 flat grey). Not the
  shader alone (ROAD_BLND_2 draws at Suzuka, black at Sebring and Catalunya) nor the tile
  fill (the same few slots left alone as at Road Atlanta, which draws). Next: a trace of a
  black road draw.
- Sebring's spot 6 lost its run to run.sh's guard: an old game.pid's number taken by another
  process. The guard now only counts a forza started on trk-spike's game folder.
- Sheets (trk-spike shots/): indi-spots.png, crownjewel-spots.png, sebring-spots.png,
  catalunya-spots.png.

## More circuits, batch 3a: Silverstone, Motegi, Tsukuba, Hockenheim

| Circuit | Ribbon 0 | Zones | Tiles | Blocks asked | 9-spot pass |
| --- | --- | --- | --- | --- | --- |
| Silverstone | 3190 instances, 1417 models | 304 | 177 | 50633 | 9/9 drawn, 0 crashes |
| Motegi | 3524 instances, 2226 models | 324 | 796 | 54095 | 9/9 drawn, 0 crashes |
| Tsukuba | 1719 instances, 1091 models | 176 | 401 | 42603 | 9/9 drawn, 0 crashes |
| Hockenheim | 4415 instances, 1990 models | 144 | 471 | 50141 | 9/9 drawn, 0 crashes |

- Left: Silverstone's road black at spots 3 and 4 (patches at 7, 8); Tsukuba dark ground at
  2, a white haze at 9; Motegi a little dark ground at 1 and 7; Hockenheim flat red ground at
  spots 7 and 8 (new: not black).
- The black roads (Sebring, Catalunya, Indianapolis, Crown Jewel, Silverstone), traced at
  Sebring's spot 2: the road (ROAD_BLND_2: Blend_BSampler s0, Blend_ValueSampler s1,
  Light_MapSampler s7 with Light_Map_uvOS c116, layer A the constant c0 = 0) is black from
  its lightmap. With every lightmap fetch read as 1 (`FM4_SHADERS_NO_LIGHTMAP=1`, a test
  that breaks other surfaces) the road has its asphalt and skid marks; neither the blend
  mask read as 1 (`FM4_PVS_ATLAS_MASK=1`), the tile fill off (`FORZA_FM4_ATLAS_FILL=0`: the
  stand-in, as black) nor texture flag 0 changes it. The road's lightmap is an atlas tile
  drawn from the pack (record 3131, flag 9, 1/128 x 1/256 of atlas file 697, which has no
  file of its own: only the static pack holds it). Next: what that part of the pack's atlas
  holds (decode it from <track>.stx.bin), and whether Light_Map_uvOS's order (offset, scale)
  reaches the shader as the shader reads it.
- Sheets (trk-spike shots/): silverstone-spots.png, motegi-spots.png, tsukuba-spots.png,
  hockenheim-spots.png; shots/spots-sebL1/2.png (Sebring spot 2, lightmaps read as 1).

## More circuits, batch 3b: Mugello, Amalfi, Nürburgring GP, Top Gear

| Circuit | Ribbon 0 | Zones | Tiles | Blocks asked | 9-spot pass |
| --- | --- | --- | --- | --- | --- |
| Mugello | 2157 instances, 1921 models | 208 | 376 | 70275 | 9/9 drawn, 0 crashes |
| Amalfi (AmalfiGP, ribbon 01 as 00) | 1934 instances, 782 models | 4312 | 375 | 33301 | 9/9 drawn, 0 crashes |
| Nürburgring GP (NurburgringF1, ribbon 05 as 00) | 3495 instances, 1852 models | 361 | 1169 | 62229 | 9/9 drawn, 0 crashes |
| Top Gear | 1491 instances, 1054 models | 242 | 430 | 36101 | 9/9 drawn, 0 crashes |

- Mugello asks for 70275 blocks, past the 65536 cap (its largest need 56220): no run ran out.
- Amalfi and the Nürburgring GP have no ribbon 0: the moved ribbon loads (fm4_track_mod.py).
- Left: the Nürburgring GP's road black at spots 2 and 4 to 8 (the lightmap, as above);
  Top Gear's ground in flat colours (blue at spot 6, orange at 3, black at 9) and its runway
  flat grey at 3 to 5 and 7, as Hockenheim's red at 7 and 8: a second kind of wrong surface
  beside the black lightmaps; Amalfi's road dark at spot 6. Mugello clean.
- Sheets (trk-spike shots/): mugello-spots.png, amalfigp-spots.png, nurburgringf1-spots.png,
  topgear-spots.png.

All 27 circuits on FM4's disc 1 but the HomeSpace sets now convert and draw at every test
spot without a crash. What stops them looking right, in order of how many circuits it hits:
the black lightmaps on roads (Sebring, Catalunya, Indianapolis, Crown Jewel, Silverstone,
Nürburgring GP, some of Le Mans, Laguna, Maple Valley, Infineon, Road America, Amalfi), the
flat-coloured grounds (Top Gear, Hockenheim), flat grey roads (Infineon 5-6, Crown Jewel 4,
Top Gear's runway).

## The black roads: static pack textures cut short

- Cause: FH1's static texture pack holds only each texture's mips (base levels stream from
  .bix files), and its registration (sub_82E2EAD8) asks the texture layout (sub_82AFFA70)
  then allocates and copies mip size + 4 KB (4 KB alone with no mips), while the header keeps
  the base level at the start and the mips after it. FM4's pack holds whole textures. A
  512 x 512 DXT1 atlas (Sebring's 697: base 128 KB, mips 56 KB) got its first 0xF000 bytes;
  the rest of its base level was the next texture's memory (the next pack entry sat 0xF000
  further on, the same at Laguna Seca). The road lightmap tiles in an atlas's lower half
  (Sebring record 3131 at v 0.84) read black, so the roads did.
- How it was found: a pack entry's header and base level dumped from the running game
  (`FORZA_FM4_DUMPPACK=DIR:k,k...`) and decoded (Xenos tiling, 8in16, DXT1; the decoder checked
  on a streamed .bix: tree bark); the dump matched the pack file for 0xF000 bytes only, the
  file holding 0x2D000 for the entry. Ruled out first: the UV rectangle (the shader reads
  Light_Map_uvOS as uv * zw + xy, as the binder gives it), the tile fill, the blend mask.
- Fix (fm4_track.cpp, on unless `FORZA_FM4_STX_WHOLE=0`): while FM4's pack is read, the mip
  size from that layout call gets the base size added (a texture with no mips larger than
  4 KB gets a mip address too), so the whole texture is allocated and copied: the dump then
  matches the file for the whole base level, and Sebring's spot 2 road has its asphalt.
- 9-spot passes with the fix against the passes before, 81 runs, 0 crashes (trk-spike shots/
  <track>-whole.png):
  - Fixed: Sebring (all 8 black spots), Silverstone (3, 4, 7, 8), Catalunya (2 to 6),
    Crown Jewel (1 to 3 black, 4 flat grey, 8 dark), Nürburgring GP (2, 4 to 8), Indianapolis
    5 and 6, Top Gear's blue (6) and black (9) ground and its flat grey runway (4, 5, 7).
  - Left: Indianapolis 8 (part black) and 7 (pale); Top Gear 3 (orange ground); Hockenheim's
    flat red ground at 7 and 8 (unchanged: another cause).
  - Suzuka the same as before (0.03% to 2.4% of pixels differ).
- A second round, the circuits with smaller dark patches (81 runs, 0 crashes): fixed Laguna
  Seca 7 and 8, Maple Valley 2 and 3, Le Mans 2 and 5, Infineon 3 and 8, Road America 2 and
  6, Amalfi 6 and 7, Alps 6 and 8, Tsukuba 2 and 6, Motegi 1, 7 and 9: the dark ground
  beside roads was the same cut-short pack textures.
- Left after both rounds (another cause each, not looked into yet): Hockenheim's flat red
  ground (7, 8), Top Gear's orange ground (3), Infineon's flat grey road (5, 6), Indianapolis
  8 (part black) and 7 (pale), Tsukuba's white haze at 9.

## The five leftovers: one cause, the instance lightmap's uvOS (not fixed)

Infineon's flat grey road (5, 6), Indianapolis 8 (part black) and 7, Tsukuba's white haze
(9), Top Gear's orange ground (3) and Hockenheim's red ground (7, 8), traced at Infineon 5,
Indianapolis 8, Tsukuba 9, Top Gear 3 and Hockenheim 7 (PS trace, trk-spike runs/*P*.pstrace):
each wrong surface is a draw whose Light_Map_uvOS (PS c116) does not come from its own
command buffer. The draws that look right have their c116 written there (fixup type 8, at
+0x180 or +0x540: e.g. (0.2031, 0.6719, 0.0625, 0.0625), that is offset u, v then scale
u, v). The wrong ones take it from FH1's buffer (depth 2):
- as a rectangle with the u scale 0: (0.3906, 0.0156, 0, 0.0625) at Top Gear, (0.2031,
  0.1094, 0, 0.0625) at Hockenheim, for instances whose lightmap record is (0.0625, 0.0625,
  0.3906, 0.0156): the offsets and v scale right, u scale zero, the lightmap read along one
  column (flat colour); with 1/128 tiles (record 0.0078, 0.0078, 0.9648, 0.0273) the same
  write comes out right, (0.9648, 0.0273, 0.0078, 0.0078);
- or as whatever the last draw left: (-5.07, -168.8, 0, -175.5) at Infineon, (18.3, -87, 0,
  -174) at Tsukuba, (1, 2.4e-8, 0, -1e-5) at Hockenheim.
The models' own type-8 fixups (word 0: (X << 16) | (slot << 4) | 8; X 0x78 or 0x74) hold no
entry for that sampler (DIFF_CLR_SPEC_OPAC_REFL_4's models: 0x780008, 0x780018 or 0x780408
only), so the lightmap's uvOS is set outside the pass group: in FM4 at run time from the
instance's lightmap record; in FH1 by its own instance-lightmap code, which writes c116 in a
layout FM4's shaders do not read the same way. The binder (sub_82EAF1B0) maps sampler 7 to a
valid slot with "write uvOS" set for every draw at Infineon (`FORZA_FM4_BINDDIAG=1` counts
the map values and flags), so it is not the binder's -1/-2 cases.
Followed further:
- FH1's depth-2 c116 is a global: 82E4D308 writes PS c116..c119 once from a block at
  0x835A0E10 (sub_82465E28(dev, 116, block, 4)); a draw that does not write its own c116
  takes that, or what an earlier draw left. The (offset, 0, scale) values above are such
  leftovers from other FM4 draws, not a per-instance write.
- The healthy road models' colour passes write the uvOS block c116..c127 (a type-0 write of
  12 registers, patched by their type-8 fixups, X = 0x74 for c116 and 0x78 for c120). The
  broken materials (DIFF_CLR_SPEC_OPAC_REFL_4 at Tsukuba, Hockenheim; the models drawn with
  stale c116 at Infineon, Indianapolis) have type-8 fixups for c120 only (X = 0x78, slots 0,
  1 or 0x40) and no write of c116, while their pixel shader reads Light_Map_uvOS at c116 (and
  Diffuse_Texture_uvOS at c122). So FM4 itself must supply their lightmap uvOS another way:
  constant relocation of the shader's registers to the material's block, or a per-instance
  write in its renderer. Not found; tracing FM4's renderer for these materials is the next
  step (more than the hour this box allowed per item).
- The run-time trace agrees: a broken DIFF_CLR_SPEC_OPAC_REFL_4 draw at Tsukuba writes PS c0,
  c1, c2 from its own buffer and reads c116 (and c148) from FH1's.
- A possible fix: FH1 binds each instance's lightmap texture (the 256 x 128 file at s7 in
  Infineon's broken draw) without a uvOS, its shaders needing none. A hook on that binding
  could call FH1's pixel-constant setter (sub_82465E28(device, 116, data, count), as 82E4D374
  does) with the instance lightmap record's (ou, ov, su, sv). Needs the binding code, the
  device and the instance's record found first.

### The fix attempt (3-hour box): not fixed

- The converter now lists the affected models (`<track>_stx_lmuv.bin`, `lightmap_uv_models`
  in tools/fm4_pvs.py): a model whose shader reads Light_Map_uvOS at c116 and whose file has no
  PM4 write of PS constants covering c116 (only block-aligned writes count: a c114 run in
  model 59 was data). Tsukuba 65 of 1091 models, Infineon 110, Hockenheim 365, Top Gear 38,
  Indianapolis 79, Suzuka 118 (where it shows no fault). Checked: Tsukuba's model 59 and 272
  are in, road model 208 (c116..c127 written) is not.
- These models' shaders are shared with healthy models (ROAD_BLND_2 at Hockenheim: 207 broken,
  74 healthy), so patching the shader files in place is out; per-model shader copies would be.
- For these models the binder's sampler 7 is -1 (no slot) and the model texture table holds
  exactly the model's slots: the instance lightmap is bound by FH1's per-instance path, not
  through the table, so its rectangle is not in the binder's reach.
- Tried, no effect (reverted): calling the device's pixel constant setter (sub_82465E28 with
  the device at draw setup +1196, register 116, mask 1 << 34) from the binder during the draw
  setup (which runs when a zone goes live: the draws are recorded); and overriding c116 in
  FH1's per-pass global write (82E4D308). The trace showed c116 unchanged both times: the
  value these draws read comes from a fixed place in FH1's depth-2 buffer (+0xF490 at Tsukuba,
  written together with c148), the constant upload of another object, not found.
- Next: find what writes PS c116 and c148 at that depth-2 spot (a write watch on the guest
  ring, or the ReIGlue trace extended to log the writer's PM4 packet and the guest code
  emitting it), or give the listed models shader copies that read Light_Map_uvOS as the
  identity, or as their instances' rectangle passed another way.

### The shader copies (2-hour box, converter only): parked

Both tests are in tools/fm4_pvs.py, off by default (the default output is byte-for-byte the
same as before: Suzuka's and Hockenheim's bin.zip and stx files match the previous converter's).

- Is the identity right? An instance's lightmap record (T[idx & 0xFFFFFF]) is either a
  streamed tile with a file of its own (`_0x<id>.bix`, the tile alone once it is in) or an atlas
  tile with no file (the record's rectangle, e.g. (0.0625, 0.0625, 0.3906, 0.0156) as scale u, v,
  offset u, v). Listed models whose instances all have their own file: Tsukuba 12 of 65,
  Infineon 68 of 110, Hockenheim 164 of 365, Suzuka 49 of 118, Top Gear 3 of 38,
  Indianapolis 45 of 79. The identity is wrong for the rest.
- `FM4_PVS_LMUV_IDENTITY=1`: those all-own models get copies of their shaders in which the
  lightmap coordinate's mad, uv * c116.zw + c116.xy, becomes max(uv, uv) (most passes have no
  literal holding a 1 and a 0; fm4_shaders.lightmap_uv_identity). The copies are named like the
  shader with another last letter, so the model files' shader names keep their length.
  Infineon: 68 models, 14 shaders; Hockenheim 108; Top Gear 1.
- `FM4_PVS_LMUV_RECT=1`: every other listed model writes its instance's rectangle into a
  register of its own c120..c127 block (FM4 writes 8 registers there; the shaders read
  one to three of them). It uses the lowest register that no shader blob reads and that no
  type-8 fixup sets (0x780408 sets c121). Its shader copies read the mad's offset and scale from
  that register (fm4_shaders.lightmap_uv_register). A model whose instances have different
  rectangles gets one model-file copy per rectangle, with the same new model numbers in every
  ribbon. Infineon: 883 models (841 copies); Hockenheim 781 (526); Top Gear 132 (95). The trace
  confirms the values arrive: the copies' draws read c122 = (0, 0, 1, 1) from their own
  buffer. Draws reading a stale c116 went from 514 to none at Hockenheim 7. At Infineon 5,
  with identity copies for every listed model (`FM4_PVS_LMUV_ALL=1`), they went from 70 to 12;
  the 12 were from a shader whose copy name collided, a bug fixed since.
- The shots did not get right (before against after, 30 fps, hidden display):
  - Infineon 5 and 6: the road is still flat grey. At spot 5 the dark wedge past the barrier
    turned into sand.
  - Top Gear 3: one patch turned into grass; the orange ground stays.
  - Hockenheim 7: the flat red runoff turned into a green-to-brown wash. At 8 it turned into black
    and green blocks; with the record's rectangle for the own-file lightmaps too
    (`FM4_PVS_LMUV_OWN=rect`) it turned into green and orange blocks.
  - Tsukuba 9 and Indianapolis 7 and 8 were not shot with the copies: the cause below showed
    first.
- Why: these draws do not have the instance lightmap at their lightmap sampler (s7). At
  Hockenheim 7, every draw that sets its own c116 has a texture at s7. Of the listed models'
  draws, 334 of 514 had nothing there (with the copies, 330 of 498). So the sampler reads
  whatever another draw left at fetch constant 7: grass and gravel textures, or one texel of one
  (the flat red). The rectangle only moves which part of that texture is read. At Infineon 5,
  66 of the 70 stale draws had a texture at s7, and the grey road did not change either: there
  the grey road is something else again.
- Next, if these are taken up again: in FM4, the c116 write and the s7 texture come together
  with the instance's lightmap, and both are missing here. A converter-only route would give
  each instance's model copy its lightmap record as an extra texture slot, point the material's
  sampler 7 (now -1, no slot) at it, and keep the rectangle above. A game-side route would bind
  it in FH1's per-instance lightmap path. Infineon's grey road needs its own trace.

## Dynamic shadows on FM4 tracks (4-hour box): car shadows with an SDK branch, off

Route (b), FH1's cascades read by FM4's own shaders, was tried first. FH1's shadow-receiving
track shaders (Colorado's H_BLND1, H_ROAD_DIFF1_...) take CSMShadowMapLevel0TM (VS c180-c183)
times the world position and sample CSMShadowMapLevel0Samp at fetch constant 8 (1024 x 1024,
k_24_8). FM4's vertex shaders export M_shad_near and M_shad_far times the world position to
one interpolator; `FM4_SHADERS_CSM_SHADOWS=1` makes those four dot products read c180-c183, and
points the s_grayscaleShadowMap fetches at fetch constant 8 and that register, marked. A local
ReIGlue commit (fh1-648-fm4-shadow, 21da36b, not pushed) gives a marked fetch a four-tap depth
compare.
- The matrix is right: in FM4's draws, c180-c183 are written every frame from FH1's per-pass
  constants (Suzuka's grid: rows (0, 0.0029, -0.0086, -2.62), (0.0084, -0.0033, -0.0011,
  -3.00), (-0.0002, -0.0004, -0.0001, 0.858), (0, 0, 0, 1), both traced frames). The
  coordinates land on the map (u 0.3-0.6, z about 0.7 on the road).
- The map is empty there: debug views of the depth at the pixel, of depth < 0.9999, and of the
  map zoomed out 4x over the road all read 1. FH1's cascades hold its own track objects (FM4's
  models have no caster pass); the car's shadow is not drawn into them.
- So for track objects' shadows, route (a) would still be needed: FM4 models drawn into FH1's
  cascade pass.

The car's shadow is in FH1's screen-space mask (s13), which the earlier test
(`FM4_SHADERS_FH1_SHADOWS=1`) already samples at the pixel. Its "black silhouette in front of
the car" was the car's real shadow: the sun is behind the camera (sunDir (-0.63, 0.48, 0.61)
against the grid's heading (0.61, -0.79)), and shad_color 0 made the shadow black. The branch now
maps the mask's 0 to 0.45 (`FH1_FM4_SHADOW_FLOOR`), close to FM4's TrackSettings ShadowColor.
- Suzuka, 9 spots (bin-csm, the spike build with the branch): a soft car shadow ahead of the
  car at spots 1 to 7; a darker contact patch under the car; at 8 and 9 part of the road draws
  pale (the mask is lighter than lit there). Elsewhere the image matches the unshadowed one.
- Not safe without the branch: with the release SDK's Vulkan translator (the plain build), the
  shadowed surfaces drew black (2 of 2 spots). The marked fetch's result is the constant 1,
  which should leave them lit, but did not reach the shader; not understood. The D3D12
  translator has no branch either, and ReGPU would see the same marked fetch, so both must
  be expected to draw black too. Both switches stay off; the default output is unchanged.
- Next: why the constant-1 fetch result does not arrive without the branch (then the
  fallback is no shadow), the pale road at spots 8 and 9, and FM4 models in FH1's cascade pass
  for the track's own shadows.

### The five leftovers, sampler 7 and Infineon's grey road (3-hour box): parked

- The instance's lightmap is in the model's own texture table already. For each listed model
  looked at (Hockenheim 269, 271, 272), slot 2 holds the very record its instances name as
  their lightmap (863, 863, 925), the same slot healthy road models (257) use for theirs.
- The material's sampler map sits just before the shader names: u32 1, u32 8, then for sampler
  registers 0 to 7 the texture slot each reads (-1 none, -2 not used), then u32 1 and the
  shader count. Model 257 (healthy) has 0 1 3 4 -2 -2 -2 2; model 269 (listed) has the same
  with -1 for sampler 7. The binder (sub_82EAF1B0) copies each mapped slot's texture and
  rectangle into the draw's block.
- `FM4_PVS_LMUV_MAP=slot` (a test, off) points sampler 7 at that slot in 363 of Hockenheim's 365
  listed models. Spot 7 stayed flat red; at spot 8 the grass got black patches. That is not
  enough on its own: the listed models' command buffers have no fetch constant 7 write (healthy
  257: PS c116-c127 and fetch constants 0-3, 7 and 31; listed 269: PS c120-c127 and fetch
  constants 0-3 and 31) and no fixups for it (257's pass groups have type 3 for fetch constant
  7 and type 8 for c116; 269's have neither). So the block's texture never reaches the GPU.
  The fix needs a fetch-constant-7 packet, a type-3 fixup and a c116 write (or the c12x
  rectangle above) inserted into each pass of each listed model. That means growing the pass
  buffers and every offset after them in an undocumented layout, more than this box allowed.
- Infineon's grey road (spots 5, 6) is not lighting. With every FM4 lightmap read forced to 1
  (`FM4_SHADERS_NO_LIGHTMAP=1`) the scene around it turned green, but the road stayed grey.
  With the converter's ground off (`FM4_PVS_GROUND=0`) it was the same. Leaving out the FM4
  road shader's draws (ROAD_BLND_REFL_NORM_NOCUBE_4, 20 draws; a local SDK switch,
  `FH1_SKIP_PS=<hash>`) changed nothing. Leaving out pixel shader A4A965C189287B99 (471 draws a
  frame, at Suzuka too; in neither FM4's nor FH1's track shader lists) turned the lower half of
  the screen black with a few glows. So FH1 draws the grey there with a shader of its own, and
  FM4's road under it never shows. Next: what A4A965 is (its draws' objects), and why FM4's
  road at spots 5 and 6 is hidden behind it (depth, pass or culling).

### The five leftovers: the lightmap bound through a spare fetch constant (3-hour box)

- Converter only, `FM4_PVS_LMUV_BIND` (on by default; `=0` leaves them): a listed model's material
  map sends sampler 7 to no slot and its command buffers write no fetch constant 7. Each such
  model's map now sends a fetch constant its command buffers do write (a type-3 fixup
  `(fc << 16) | 3`) but none of its shaders samples to the slot holding its instance's
  lightmap record; its shader copies fetch the lightmap from that fetch constant
  (`fm4_shaders.lightmap_fetch_constant`) and read its rectangle from a spare register of
  the c120 block (the rectangle copies above). Each lightmap record gets its own copy of the
  model with that record in the slot, each instance its record's copy.
- Fixed: Hockenheim 7 (the flat red run-off is asphalt with its kerbs), Hockenheim 8, Tsukuba 9
  (the white haze is the grass and gravel); Mugello 1's pink barrier and a dark strip at
  Hockenheim 1 too.
- Copies are streamed as files of their own: with every listed model bound, Laguna Seca and
  Catalunya asked for 107000 and 104000 streaming blocks (the game's cap is 65536) and
  Catalunya's pool ran dry at spot 5 (a crash in sub_82E3CAE8's caller). A model with more than
  `FM4_PVS_LMUV_BIND_MAX` (4) lightmap records is left: Catalunya then asks for 43083, Laguna
  33695, Hockenheim 50141, and the fixes above hold.
- Left: models whose command buffers write only fetch constants their shaders sample (the road
  shaders with a normal map, `ROAD_BLND_REFL_NORM_NOCUBE_4` and others): Infineon 5-6,
  Indianapolis 7-8 and Top Gear 3 stay as they were. A fix needs a fetch constant packet and a
  type-3 fixup added to their command buffers, or the normal map given up. Infineon's grey is
  also behind FH1's own full-screen passes (pixel shader A4A965, a rect-list clear/copy shader).
- The three left need a fetch constant packet and its fixup added. The fixups are D3D's
  precompiled command buffer fixups: sub_82D19210 dispatches a record by its type (type 3 to
  sub_824AAAC0), which takes the record's second word (kind 0x47, data offset) to a patch list
  of the buffer (sub_824AAA28) and writes the texture's fetch constant into each place listed.
  Infineon's road model 211 has, per pass, a buffer of 0x254 bytes (fetch constants 0-2 at
  +0x19c, fetch constant 31 after it) and the pass group's sizes 0x110, 0x254, 0x10; where the
  patch lists live in the file is not found yet, so nothing is inserted (found the next morning: below).
- Sweep: all 24 converted circuits (Suzuka with them), spots 1 and 5, against each one's latest
  pass: no crash but Catalunya 5 before the cap above; mean differences 1.3-7.7 except where a
  fix shows (Hockenheim 1 10.6, Mugello 1 10.6, Autopark 1 11.2: the barrier's lightmap) and
  Alps 1 (37: the car settled elsewhere).
### The last leftovers: a fetch constant added to the command buffers (3-hour box)

- The patch table's place and format (found with a dev-only hook on sub_82D19210, not shipped, that
  logs each type-3 fixup with its object, table and places): a
  pass group's data after the 21 groups is three areas. Area A holds each populated group's
  first size: D3D's command buffer header (0xC8 bytes, the object sub_824AAA28 reads: +0x68
  first size - 8, +0x70 the table's end, +0x78 second size - 8, +0x7C the table's words, +0x90
  0xC8, +0xC0 0x81000000 | (second size - 8) / 4), the patch table, zeros to the table's 8-byte
  aligned end + 8, and the 11223344 55667789 marker. Area B holds the PM4 buffers, each at the
  next 32-byte boundary from the area's start (the .fx header's word 4 is the area's size, word 3
  area A's, word 5 area C's, word 7 eight times the groups' records). A texture record's second
  word is 0x47000000 | index into the table; from there each word (0x84/0xC4 one word, 0x88/0xC8
  a word and a value) is a buffer place (bits 6-25 the dword, bit 30 another follows) where
  sub_824AAAC0 writes the texture's six fetch constant dwords, keeping the template's sampler
  bits. A buffer can hold several draws, each with its own write of fetch constants 0-2.
- Converter (`fm4_pvs.insert_fetch_constant`, used by the lightmap binding when no spare fetch
  constant is left; `FM4_PVS_LMUV_INSERT=0` leaves them): after every write of fetch constants
  from 0, a type-0 packet for fetch constant 7 with a clamped template (Hockenheim 257's), a
  record (7 << 16) | 3, one table word per draw, every later place moved by 7 dwords, the sizes,
  the header words and the following buffers moved. Checked offline: every existing record of
  every model it applies to (Infineon 204, Indianapolis 149, Top Gear 100, Hockenheim 1210)
  still patches the same words, and the new places hold the new packets. The lightmap's
  rectangle comes from a spare c12x register as before (the c120 writes are now found again
  after the insertion). Models given one: Infineon 55, Indianapolis 40, Top Gear 11.
- Results (trk-spike shots/spots-insB*): Indianapolis 8's black road is lit asphalt; Infineon
  5's black band of distant ground is textured. Unchanged, so another cause: Infineon 5-6's grey
  road (FH1's A4A965 passes, above), Indianapolis 7's pale sand, Top Gear 3's orange ground and
  flat grey foreground. No crash.
- Sweep: all 23 circuits with spot lists reconverted (Crown Jewel and Motegi get no insertion),
  spots 1 and 5 against the previous night's sweep: no crash anywhere (Catalunya 5 included).
  The mean differences are larger than that sweep's (9-27, Alps 1 44: the car settled
  elsewhere) with the same scenes, roads paler at Hockenheim and Nürburgring GP. Not the
  insertion: the same build with `FM4_PVS_LMUV_INSERT=0` differs from it by 0.6 and 1.0
  (Hockenheim 1, 5) and 1.5 (Nürburgring GP 5). The shared SDK under the default run
  (rexglue-sdk-patched-forza) was replaced after the previous sweep, the likely cause; not
  looked into.
- Note for runs: the converted circuits share one zipmanifest.xml (the last one reconverted
  mounts only its own track); running another circuit needs its loose-<track>/media/zipmanifest.xml
  copied back (reconvert_track.sh does it), or the load stops at its texture pack.

### Car shadows made shippable (3-hour box, stopped for the night): state

- Root cause of the black fallback: the SDK's Vulkan translator stores nothing for a texture
  fetch whose result is all constants (SpirvShaderTranslator::ProcessTextureFetchInstruction
  returned before StoreResult unless a component came from the texture; the register array is
  not initialised). The D3D12 translator stores them (StoreResult(LF(0))), and so does ReGPU
  (hlsl.cpp, `s == 5 ? 1.0`). The converter's shadow fetches have been constant 1 all along, so
  under Vulkan their registers read undefined (0 here): today Vulkan draws FM4 tracks without
  FM4's sun term, while D3D12 and ReGPU include it. With FM4_SHADERS_FH1_SHADOWS (shad_color 0)
  that 0 drew black.
- Decision (A), the coordinator's: ReIGlue stores constant-only fetch results on Vulkan as
  D3D12 does; FM4_SHADERS_FH1_SHADOWS stays off by default until a release ships that ReIGlue.
  Local ReIGlue: fh1-648-fm4-shadow 779fb22 (the fix on the dev branch), and a PR-ready branch
  fh1-648-fm4-shadows-pr on origin/main: 0439 (the fix) and 0440 (the mask fetch for marked
  fetches plus the 0.45 floor, without the cascade test or the dev switches), both with
  docs/reiglue/commits.md rows. Not pushed. It builds (overlay-pr) but has not been run yet.
- Scan of FH1's own shaders: no texture fetch with an all-constant result among 3823 unique
  shaders (media incl. Colorado's bin.zip, and default.xex's image), nor among 3004 from the
  title update (media.zip and its default.xex); the scanner finds 19 in Suzuka's converted
  archive (positive control). So the fix changes nothing of FH1 itself. A Colorado
  before/after (release plugin twice, then the fix) was started and stopped for the night: not
  measured yet. DLC content is not scanned.
- Pale road (Suzuka 8, 9): it is FM4's sun term itself. The ReIGlue fix alone, with the
  shadows switch off, gives the same pale left lane (fixDef against today: spot 8 mean 15.2,
  16.7% of pixels over 24). D3D12 and ReGPU should already draw it today (not yet checked on
  the laptop). Forcing SunGainScale to 0 changed nothing (FH1 sets it 0); forcing
  RoadSpecularParams1/2 to 0 turned the road red, so it is not those alone. FM4's own
  s_grayscaleShadowMap is its dynamic car shadow (TrackSettings ShadowNear 25, end 202); the
  ribbon .sh files are not a shadow map. Open: whether the lane is FM4's look or a stale FH1
  constant.
- Converter: with FM4_SHADERS_FH1_SHADOWS, shad_color 0 again by default
  (FM4_SHADERS_FH1_SHADOW_COLOUR=1, a test, keeps it 1: exactly today's look on the release
  SDK, Suzuka 1 and 8 mean 4.3, but the shadow then removes only the sun term).
  FM4_SHADERS_CONST_TEST=NAME=0|1 is a test that forces a float constant's reads to a literal.
  A per-fetch "dead component" workaround (so every SDK stores the 1s) was tried and dropped:
  many shadow fetches (ROAD_BLND_2's third, into an interpolator register) have no free
  component.
- Next: run the PR branch (overlay-pr) on Suzuka 1-9 with and without the shadows switch;
  finish the Colorado before/after; check D3D12 on the Windows laptop when the coordinator
  frees it (pale lane today, and the fallback with the switch on); decide on the pale lane;
  then the ReIGlue PR text with the scan and the shots.

### Car shadows: the PR build on Suzuka and Colorado (morning)

- ReIGlue PR branch (local fh1-648-fm4-shadows-pr: 0439 the fix, 0440 the mask fetch with the
  floor) run on Suzuka's 9 spots (bin-pr, overlay-pr), switch off and on, and Colorado.
- The pale road was the sun term times FH1's TimeGain.w (c249 = (the hour, 0, 0, 8)): the
  shadowed FM4 shaders multiply the sun term by it. The converter now reads it as 1 in those
  shaders (9bdd806, `FM4_SHADERS_TIME_GAIN=0` leaves it). Spot 9 against today: mean 48 with
  the fix alone, 18 with TimeGain 1; the road keeps its tyre marks.
- Release build (no 0439), the new converter, marks off: all 9 spots match today (0.7-4.1).
- PR build, marks off: spots 1-7 match today (0.2-3.1); 8 and 9 gain a sunlit lane (5.8, 18.2).
  PR build, marks on: the car's soft shadow ahead of the car (sun behind the camera) at most
  spots, no black.
- Colorado (40 s in): release against release differs by a mean of 8.2 (traffic, time of day);
  release against the PR build by 2.2 and 8.4: the same lighting, other traffic.
- D3D12 on Windows (GTX 1650 Ti, this branch built with a release SDK overlay): Suzuka spots 1,
  8 and 9 with the marks off and on draw the same (mean difference 4.7, 1.7, 2.6), no shadow and
  no black; the sunlit lane at 8 and 9 shows there too. Left: the ReIGlue PR.

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

## Reaching a circuit from inside the game (looked into, not built)

Today a circuit replaces Colorado for a whole session (the launcher's Drive in). Travel inside
one session, Colorado to a circuit and back (an airfield or pause-menu entry), was looked into
and left for later. What it needs:

- A world change in the session. FH1 reloads free roam with the state machine's
  `load_into_free_roam` (state_definitions.xml, CLoadIntoFreeRoam: after races), which loads
  Tracks row 317. Pointing 317 at the circuit for one load could use a TEMP shadow of Tracks, the
  way Custom Race shadows Events and Races through the game's own database object
  (hooks/custom_race.cpp); the way back drops it and reloads again. Not found yet: how to
  request `load_into_free_roam` from free roam itself (the benchmark requests an activity's
  states, platform/benchmark_session.cpp, but free roam's reload is not one of an activity's).
- Per-world switches. The converted track's switches are process-wide today: FORZA_FM4_COL
  makes every collision index load whole, FORZA_TRACK_SPAWN moves every teleport of the
  player's car (and is read once), FORZA_TRACK_NO_TRAFFIC sends every other car below the
  world. With Colorado and a circuit in one session they must follow the loaded world (the
  track's own name at load, against the mounted circuit), in hooks/fm4_track.cpp and the lap
  timer (which would show its HUD in Colorado now).
- The mount from the session's start for every imported circuit (cheap: the mount already
  adds folders; their archive lines to one zipmanifest.xml).
- An entry point: the pause menu has native pages (platform/native_pause_*), the airfield none
  of ours yet.
- Free roam on a circuit runs Colorado's game modes (the converter copies them), so its
  festival, events and map pins are Colorado's in the wrong place; a world change in a session
  makes that more visible, and the save keeps the place you leave from.

Each piece has a precedent in the codebase, but together they are several boxes of work: the
state-machine request first (one experiment decides it), then the per-world switches.

## The road network and the race route (tools/fm4_routes.py)

FH1's sat-nav and its races read files FM4's circuits lack. `fm4_track_mod.py --fm4-ai` (the
launcher's import passes it) writes them from FM4's AI line of the layout that is the circuit's
world (`media/AI/Tracks/<track>_NN.geo`: the waypoints, a vector across the road as long as half
its width, the racing line's place on it):

- `media/tracks/<track>/<track>.nav`, the road network (the format `tools/build_map_roads.py`
  reads): a node every 20 m of the line (Colorado's median spacing), tagged a main road
  (`road_type` a) without traffic (`traffic_disabled`) under the track's name. It is the
  road of that one layout: the circuit's other layouts' own stretches are left out, since
  the layout's barriers close them and a road on the map that cannot be reached would mislead
  the map and whatever plans a route (`fm4_routes.py write ... OTHER_GEO` joins them at both
  ends, for the day barriers go by route). With it `<track>.owr` (12 bytes: the magic word,
  0, the magic word) and Colorado's 32-byte `colorado.oww` as the circuit's (what it holds is
  not known).
- `media/tracks/<track>/ribbon_00/trackroute101.xml`, a lap race's places, laid out as
  Colorado's `TrackRoute049.xml` (measured there: the trigger 57.9 m before waypoint 0, the
  cannons from 35 m to 3 m before it, the place after the race at 26 m, the grid from 10.7 m
  back every 6 m): 8 start places from 10 m behind the line, 6 m apart, at 0.35 of the half
  width to either side; 5 finish cannons a side over the last 35 m; their trigger 58 m
  before the line; the place after the race; a route waypoint about every 250 m; the
  gantry's cannon.
- `media/aiopenworld/<track>/ribbon_00/route_101.owt`, the route's line every 2 m (centre,
  the vector across, FM4's racing line as the factor). A file of the game's aiopenworld
  archive, so the import keeps it with the circuit's database mod.

What FH1 needs of a road network, found on TU4 before this converter, with Suzuka loaded as
a track of its own in a package:

- The tag names the game looks up, in the file's own table, which is sorted (a binary search,
  sub_82D9D4A8). The game asks for a name and uses the answer unchecked: with a table of 4
  names, free roam crashed once the network had more than one road (sub_82D98D20 asks for
  `oneway` and calls sub_82D9C8E0 with it for each road), and so did the start of a race.
  The names TU4 asks for: `ai_disabled`, `allowedfordemo`, `area_type`, `deadend`,
  `discovery_zone`, `display_name`, `first_time_flow_disabled`, `iefestivaldrive`, `oneway`,
  `point_of_interest` (not in Colorado's table either), `road_type`, `sat_nav_junction`,
  `traffic_density`, `traffic_disabled`. The table carries Colorado's 17 names and its values,
  from the player's own `colorado.nav`.
- The map screen's `cursor_area`, a rectangle round the circuit (400 m here): without it the
  circuit sat in a corner of the map.

Kept from the network that then worked, and not isolated as needs of their own: no closed road
(Colorado has none, its closed ways are areas: the lap is a ring of open ways that share their
end nodes, cut at junctions and every 60 nodes, in two at least), and a road node near every
route waypoint.

`fm4_routes.check_network` refuses a network without those names, with its tag names or values
out of byte order (Colorado's two tables are in it, each string once), with a closed road or
with a waypoint off the roads; `fm4_routes.checked` refuses an AI line that is not 100 m to
100 km of finite numbers (one whose points all coincide passes for a lap). The step is
optional and never stops an import: whatever goes wrong in it, `fm4_track_mod.add_routes`
says so in a line and leaves the circuit without the files, as before. They are written beside
the mod and moved in once all are there, and a move that fails takes the others out again.
A race row's RouteId is not 0 (free roam's): 101. In that package FH1 loaded a track's world
from ribbon 0 whatever the row's RibbonIndex was and the route's files from the row's ribbon,
so they are in `ribbon_00` (`--fm4-ai` needs `--free-roam`, which makes that ribbon).

Measured:

- The formats: the player's own `colorado.nav` (12,036 nodes, 459 ways, 585,674 bytes) reads
  and writes back byte for byte, and so do all 46 routes of `aiopenworld.zip` (25 laps).
- All 24 circuits of Disc 1 get a network that passes the check and a race route. The
  smallest is Crown Jewel's (49 nodes, 2 roads, a 900 m lap), the largest the Nordschleife's
  (1,229 nodes, 21 roads, 24.5 km), then Fujimi Kaido's (14 roads, 16.4 km) and Le Mans's
  (12, 13.6 km). Every world layout is a lap (its line's ends 2 m apart).
- An import of Suzuka by the launcher's code with and without `--fm4-ai` (98 s either way):
  230 of the 231 files are the same byte for byte (the archive, the database, the game modes
  and the rest); `track.json` differs in its size alone, by the five new files' 166,790 bytes.
- In the game (Windows 11, Radeon RX 9070 XT, Vulkan, the 0.3.2 build, offline on a copy of a
  save, the 1000 Club pack installed; started as the benchmark starts, with its event's
  activity renamed so it stays in free roam; shots by `FORZA_SCREENSHOT_TRIGGER`): Suzuka and
  Infineon without and with the files, 80 to 85 s each. Both load to the grid alike, with
  the lap timer's prompt. Without, the minimap is the player's arrow alone (and Colorado's
  pins); with, it draws the circuit's roads round it. Top Gear (the most roads) and Le Mans
  (no ribbon 0: its ribbon 1 is the world), with the files, 80 s each: both load to the grid
  with their roads on the minimap. The game's own places for the two other cars it teleports
  in free roam differ: (0, -1020, 0) and (0, -1030, 0) with the files on all four circuits,
  places near the origin without (Suzuka: (0.6, -123.0, 0.0) and (0.8, -119.8, 0.0)).
- Suzuka against the package above, which was driven on ReImagined before (free roam with
  the circuit's roads on the map, and lap races on its three layouts from multiplayer race
  rows; AI drivers were not looked at there): the route's line (`route_101.owt`) and the
  `.owr` are the package's byte for byte; the route's places differ in the finish trigger's
  one line (the package had it 1 m before the line); the network is the package's byte for
  byte when the tool is given the other layouts' lines (10 roads), and without them its lap's
  5 roads.
- A lap driven with the files (this branch on `main` with #777, #778 and #780; Suzuka
  imported afresh by the launcher's code, started through the game's own menus on a copy of
  a save; the dev autopilot on FM4's AI line at up to 80 km/h): 400 s, the whole 5.8 km lap
  and 550 m of the next, the circuit's road on the minimap all the way round, the lap timer
  running, no guest fault and no `ExecutePacketType0 overflow` in the log.

Not done:

- No Tracks row uses route 101, so nothing reads the race route yet. What a race row needed
  in that package: RouteId 101, RibbonIndex 0, RibbonConfiguration 0 (a looped circuit; FH1's
  leftover FM4 rows carry -1, which hid them as races), SurfaceTypeRibbonConfiguration 0, and
  `gamemodes/<track>/cutscenes/tracks/cutscenes_<DevName>.xml` holding Colorado's
  `prerace_countdown_<DevName>` cutscene renamed (its cameras are in car space; a route's
  other Colorado cutscenes place cameras and people at Colorado's positions).
- A race is not checked in this flow. With the benchmark's event (FR02) pointed at Suzuka's
  row 37 in the circuit's database, on a start on the circuit with `FORZA_TRACK_NO_TRAFFIC=0`,
  the event's load did not end on the 0.3.2 build: a black screen for 110 s without these
  files (row 37 as it is), the loading screen for 250 s with them (row 37 set as above, the
  countdown cutscene in place). The benchmark's own thread list at 120 s shows every guest
  thread waiting. So whether the AI drives with the network is still to measure, with the
  race tooling.
- One difference seen in those runs: with the files in place the benchmark logs `BENCHMARK
  AI: created native race driver for player container` when the world loads (6 runs of 6,
  on the four circuits); without them it does not (3 of 3): the opponent factory
  (sub_82A5D748) gives no container.
- Only the world's own lap gets a route and its road. A race on another layout needs the
  roads between them open and its barriers by route (FH1's way: instances and collision
  polygons that carry route ids); until then the other layouts' roads stay out of the
  network.
- The map screen and GPS routes on a circuit were not looked at; the map's picture and pins
  are Colorado's.

## Open

- (c) The road, terrain and buildings draw with FM4's own shaders, at 13:00, over a ground.
  Car shadows: `FM4_SHADERS_FH1_SHADOWS`, on by default since 0.2.9 shipped ReIGlue 0439 and
  0440 (0 turns it off); see "Car shadows made shippable" and the morning runs above (the
  D3D12 fallback checked). Shadows from the track's own objects
  are still open (FM4 models are not in FH1's cascade pass). The older notes below are kept.
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
    The draw trace (the PS trace on TERR_BLND draws, a world matrix with 15 x 15 scale, at
    spot 1; `find_scaled.py` in trk-spike) corrected that: with 866 the ground is drawn (3
    draws, 1400 indices), at exactly the placement the converter writes (c140-c142 = the
    instance's), but its stretched geometry lands away from the circuit: the placement
    (`ground_instance`) assumes a piece's vertices sit round its model matrix's translation, as
    900's do and 866's evidently do not (its centre would need the vertex bounds, not read
    yet). With 900 (the default) the ground is drawn (6 draws) and fills the holes. A copy of
    900's model (any new model number: the one-layer copy, `FM4_PVS_GROUND_LAYER=A|B`, or a
    plain copy, `=copy`) is not drawn at all (0 draws), shader renamed or not: a model past
    FM4's own count is skipped somewhere FH1 sets up a track's models (the zone data lists it
    as any other). So the ground keeps model 900 and its pale, patchy blend; a green ground
    needs either that model limit found (then the one-layer copy), or the piece's own shader
    patched for every use of it (it is also the grid's terrain). `FM4_PVS_GROUND_LAYER` stays,
    off. Sheets (trk-spike, not in the repo):
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
