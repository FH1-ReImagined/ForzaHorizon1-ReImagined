# Fast & Furious's world in free roam (#821)

Forza Horizon 2 Presents Fast & Furious (Xbox 360, 2015) is a standalone part of Forza Horizon 2:
its world, `media/tracks/Anthem`, is FH2's southern Europe map cut to an area around Nice, with
places of its own (an airfield, docks). `tools/fh2ff_world.py` makes it a world FH1 drives in
free roam, from the player's own copies of the games, and FH2's own world the same way (its
`media/tracks/Anthem` is the whole map: 2,798 zones to F&F's 1,132). The output is the player's
own and is never shared.

## In the launcher

Mods > Mods > Forza Motorsport 4 tracks > **Import Fast & Furious…** (experimental) asks for the
folder of F&F's files and, optionally, FH2's (for its map art and road types), and runs `build`
(`launcher/forza_launcher/fh2ffworld.py`); **Import Forza Horizon 2…** asks for FH2's and builds
its world. Each game's `default.xex` (title 4D530AB5 F&F, 4D530AA4 FH2) tells them apart. A world
is kept where the imported FM4 circuits are, `<data>/fm4-tracks/anthem` or `.../fh2` (its
`track.json` of kind `fh2ff` or `fh2`), with its mod as `<data>/mods/.fm4-track-<key>`, so the
Play page's picker offers "Fast & Furious (FH2 world)" and "Forza Horizon 2 (FH2 world)" and
Remove takes one out. A start in one mounts the folder as a circuit's (`--fm4_track_dir`), loads
the mod last and plays offline, with the world's own switches, set by the launcher:
`FORZA_INSECURE_FILES` and `FORZA_TRACK_SPAWN`, and `FORZA_FM4_COL=0` and
`FORZA_TRACK_NO_TRAFFIC=0`: the world's `fm4_stream_blocks.txt` (below) is the converter's marker
the game gives a circuit's switches by, and a 0 turns one off. So the world's collision streams
as FH1's own does, traffic stays, its textures need no `FORZA_FM4_STX` and no FM4 grid node is
added to its `GameObjs.xml`. A variable in the launcher's extra environment still wins. Only
folders of the games' files are read, not disc images. (A marker of a world's own in the game's
mount would make the two 0s unneeded: a change to the game left for later.)

## Running it (dev)

```
python tools/fh2ff_world.py build FF_DISC FH1_DISC UPDATE_MEDIA_ZIP OUT [--fh2 FH2_DISC]
python tools/fh2ff_world.py check FF_DISC
```

`build` writes `OUT/fm4-tracks/anthem` (the track folder, as FM4's circuits use it with
`--fm4_track_dir`) and `OUT/mods/fh2ff-anthem` (a mod). With `--fh2` (FH2's own disc folder) the
closed roads take FH2's road types, F&F's road closures are taken out and the full map shows
FH2's map art. The game then runs with

```
--fm4_track_dir=OUT/fm4-tracks/anthem --forza_mods_dir=OUT/mods --forza_mods=fh2ff-anthem
FORZA_INSECURE_FILES='zipmanifest.xml|\tracks\anthem\'
FORZA_TRACK_SPAWN=<the line of OUT/fm4-tracks/anthem/spawn.txt>
FORZA_FM4_COL=0 FORZA_TRACK_NO_TRAFFIC=0
```

and free roam loads the world in place of Colorado (the mod puts free roam's Tracks row 317 on
Environments 70, F&F's own id for Anthem, which FH1 has not). `FORZA_AUTOPILOT=<autopilot.txt>,1.0`
with `FORZA_AUTOPILOT_DELAY=6` drives the line `build` writes along the spawn's road.
`check` reads F&F's layout, every zone and every procedural geometry file, converts them in
memory and reads the zones back.

## What is converted, and why

Most of the world is in formats FH1 reads already: models (version 6), textures (BIX1, CAFF
packs and bundles), collision (`.fiz` cells, the ribbon's `.col`), the world's own shaders (FH2's
builds of them: 52 share a name with Colorado's, 31 are new; Colorado's in their place drew the
terrain flat and black), the road network. The rest, each found from a load that stopped or a
wrong picture:

- **Layout and zones, version 51.** The same layout as FH1's 50 except that a placement's route
  numbers are u16. `tools/fm4_pvs.py` reads 51 (`read_fh1`, `read_cell`, `read_placement`) and
  never writes it; the tool maps the 41 route numbers to bytes (1 to 41) and writes 50.
- **File numbers.** A zone names its files by their number in FH1's order of the archive (by
  CRC-32 of the lower-case path, `fm4_pvs.archive_order`); F&F's `FilenameMap_00.dat` is in
  FH2's own order, and FH1 read a texture as the first zone. The tool writes FH1's map, every
  zone file entry's number and `PVSZLookup_00.dat` in FH1's order.
- **Zones' files.** As FH1's zones do, a zone lists each visible model's file to stream with it
  (type 9; 422,685 entries); the 8 models with no file and their 1,002 instances are taken out.
- **Hex grid.** HEXY 102 has two bytes a zone after the cells; FH1's 101 has the second only.
- **Procedural geometry** (`.pgeo`; decoded with the FH2 prototype's PDB:
  `proceduralGeometry::CProceduralGeometry::CGeometryData`, 0x60 bytes, type at +0x30, the
  parts sized by their pointer fields and laid out 16-byte aligned). FH2's
  `PVS::CPVSModelInstanceDynamic3DData` is 76 bytes (16-bit routes and a pad byte), FH1's 56:
  582 files carry one (crowds, animated scenes, placed models) and FH1 read their other parts
  20 bytes off and faulted. The tool rewrites the block FH1's size and moves the rest. Animated
  scenes (type 5, 27 files) still fault after the load and are left out of the zones
  (`FH2FF_PGEO_DROP` lists the types left out).
- **Road network.** FH1's map looks the tag name `oneway` up and uses the result unchecked (TU4
  sub_82D98D20); F&F's table has `oneway_forward` and `traffic_density_name`, renamed to FH1's.
  Traffic asks for more names the same way (a fault at sub_82D9C8E0 once it spawned), so the
  table carries every name of Colorado's too. FH1 reads `traffic_density` as an id of
  `AIOpenWorld.xml`'s densities (Colorado's are 2 to 17) and F&F names them: with the names no
  traffic came. Each is FH1's id of the same kind of road: `none` 1 (empty), `b` and
  `open_world` 2, `a` 3, `freeway` 4, `a_high` and `a_large` 5, `festival` 8, `dirt` 12,
  `b_high` 14.
  F&F marks 192 roads out of its area `road_type=blocked`, which FH1's map does not draw; with
  `--fh2` each takes FH2's type on the same nodes (190 found; 2 become `b`).
- **Audio.** F&F's sound banks are FH2's FMOD project and its soundscapes name them by number:
  a ribbon file naming a missing bank ended the load. The zones' sound bank entries are left out,
  the soundscapes written silent, and Colorado's audio stands in, as for FM4's circuits.
- **Lighting.** F&F's own: its grading lookups, post-processing zones and templates (and the one
  grading map they name that FH1 has not, `amalfi.dds`), its time-of-day curves held as for FM4's
  circuits. Its `TrackSettings.xml` is version 66, FH1's 64: given 66, FH1 lit the world black
  with the car blown out white at any hour. Without the five elements FH1's has not
  (`CarGlowScale`, `Particles`, `TintColor`, `ToneMapHighBumperExposure`, `ToneMapHoodExposure`)
  and marked 64, it lights right (found by laying F&F's files over Colorado's one set at a time).
- **Collision surfaces.** F&F's surface table is FH1's 68 and four more (RallyGrass, Dirt2,
  Dirt3, RallyGravel). Only RallyGravel (71) is used: 2,263 triangles in 58 cells, past the end of
  FH1's table. Each extra surface becomes FH1's of its name without "Rally" and a trailing number
  (71 Gravel, 68 Grass, 69 and 70 Dirt), in the cells that use one.
- **The archive's copies.** FH2's `bin.zip` stores a file again near each zone that streams it
  (220,842 members for 50,956 files, 3.2 GB), as FH1's Colorado does; F&F's once. Every copy is
  the same file: the tool reads one of each (an archive whose copies differ is refused) and
  writes them once, 0.8 GB.
- **Streaming pool.** FH1 streams a zone's model files into a pool of 22,440 1 KB blocks. FH2's
  world ran it dry at its first place (a null block, sub_82E3A2E8). The game links in as many as
  a track's `fm4_stream_blocks.txt` asks for (`hooks/fm4_track.cpp`, up to 65,536): the tool
  writes the largest union of a hex cell's zone's model files and its six neighbours', in KB,
  1.25 times over (FH2 50,234 and 62,793 asked for; F&F 52,089, capped at 65,536 less the
  rounding, 65,111; F&F drove on the game's own pool before, so the measure is generous).
- **Spawn.** F&F streams 1,132 zones on the western 66 x 50 of FH2's 104 x 50 hex grid (FH2:
  2,798). Outside every zone FH1 draws from the nearest one, which sees the place from afar: a
  spawn there had the road under the car invisible. The spawn is the start of the longest
  stretch of road whose cells and the cells around them have zones, 2 m right of the road's
  centre line (the nodes): on the centre an oncoming car met the car head on.
- **Road closures** (`--fh2`). F&F closes its area with objects FH2 does not have: crowd fence
  netting, cones, pole reflectors, metal mesh barriers, "route barree" signs, intersection
  bollards (709 in `CollObjs.xml`). Each is moved under the world (the list keeps its order) and
  its instance, found by position, left out of the zones. F&F's other additions (benches,
  planters, its smash boards) stay.
- **Map** (`--fh2`). FH1's map profiles are global and point at Colorado's background tiles. F&F's
  tiles are FH2's western ones darkened outside its area, with leftover developer tiles; FH2's
  own art is taken, renamed (FH2 names a tile high-row-col, FH1 high-col-row) into the mod with
  FH1's fullscreen profile set to FH2's background size and offset. The minimap has no
  background in either game.

F&F's free-roam resets (the "hand of god" when leaving its area) are not in its data: no
surface is a reset trigger, and the HandOfGodReset values are the races' off-track ones. With
FH1's code and Colorado's game modes there are none: a drive along a closed road 1.3 km past the
zones' edge went on without a reset or a wall.

## Checked

Windows 11, Radeon RX 9070 XT (AMD 26.10.1), Vulkan, the 0.3.2 build, offline, a fresh copy of
a save for every run, the dev autopilot:

- the world loads in free roam and draws in daylight: the road, its lines, verges, guard rails,
  fences, walls, signs, buildings, trees, hills;
- this tool's build: 5 minutes along about 4 km of road with 30 zones streamed in, no fault, then
  the full map with the car in the right place; with traffic, 150 s and cars on the road ahead
  and alongside, no fault; the launcher's import (the converter run by `fh2ffworld.build`, 80 s)
  and the start the launcher plans, 150 s with traffic and a free-roam driver passing, no fault;
  the prototype it was ported from, 5 minutes
  along 4.6 km with 34 zones;
- a closed road driven 1.3 km past the edge of the zones: no reset;
- a closure in Nice: the barrier line and cones across the road are gone;
- the minimap draws the formerly closed roads; the full map (Start, Map) shows FH2's art under
  the roads, aligned;
- with the streaming pool and the launcher's switches (the two 0s): F&F 200 s with traffic and a
  free-roam driver, no fault;
- FH2's own world: its build (3.5 minutes, 1.0 GB), 240 s along 5.2 km from the same start with
  traffic, no fault; 150 s on its eastern motorway (x 5.8 to 7.4 km, beyond F&F's area); a road
  driven across x 8.2 km to 8.8 km.

`tools/test_fh2ff_world.py`: synthetic files (the version 51 layout and placements, the
procedural geometry block, the hex grid, FH1's file order, the closures, the road network and
spawn, the archive); with `FH2FF_DISC` set, F&F's own world converted and read back.
`tools/fh2ff_world.py`'s output was compared with the prototype that was driven: every file and
archive member the same.

## Not done yet

- Animated scenes (procedural type 5); the PDB's `CProceduralAnimatedScene` structs.
- F&F's own audio; its game modes (events, the map's icons are Colorado's).
- FH2's far east. FH1 streams the zones round a point it clamps near x = 8.2 km: a start at
  x 9.1 to 9.4 km (zones 2422 to 2535) had zone 2250 (x 8.25 km) made live and nothing else, the
  car held still with no collision under it. FH2's grid reaches x 10.85 km (about the last
  sixth of it) and its roads 14.8 km. Either the clamp found in the game (17 floats of 8192 in
  the TU4 image to look through) or the world moved 3 km west (every placement, the hex grid,
  the collision cells and index, the road network, the ribbon's XML) is the next step.
- F&F's places in FH2's world (its airfield, docks): one world or the other for now.
- NVIDIA and Intel GPUs, Linux, macOS, ReGPU.
