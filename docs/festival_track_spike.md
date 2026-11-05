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
     zone their extent reaches, and each zone also takes the large pieces (120 m and over)
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
     the projected track lightmap, whose sampler 13 holds FH1's shadow mask, read as 1);
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
`FM4_PVS_TOD`, `FM4_PVS_GROUND`, `FM4_PVS_GROUND_SCALE`, `FM4_PVS_GROUND_BELOW`, `FM4_PVS_GROUND_MODEL`,
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
  are added. The cause (a per-zone or live-set limit) isn't known.
- FH1 lights a track from `Tracks\<track>\Ribbon_NN\TimeOfDay{,A,B,Neutral}.xml` (sun,
  sky, clouds, haze, the clock's speed, keyed by minutes after midnight). FM4's circuits have
  none, and Suzuka came out with a deep blue night sky and a moon. With Colorado's curves
  held at 13:00 it reads as day whatever the clock says.
- A placement's rotation may scale: the ground is a terrain piece placed with (15, 1, -15).
- Kerbside walls and barriers sometimes draw flat cyan, green or blue, differently from run to
  run with the same files (seen with and without the ground). Cause not known.
- Shots for comparisons: a shared shader cache for one's own runs and 25 s after loading;
  with a fresh cache, draws are skipped while pipelines build and pieces go missing.

## Open

- (c) The road, terrain and buildings draw with FM4's own shaders, at 13:00, over a ground.
  Still to do: the track surfaces are darker than FM4's (FM4's shaders take FH1's sun and
  lightmap constants); the stretched ground shows its blend texture's sandy channel more
  than grass; the zone-size and barrier colour glitches above.
- The streaming block pool still needs `FORZA_FM4_STREAM_POOL`.
- AI, the airport entry, ribbons 01 and 02, other circuits.
