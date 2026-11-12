# Fast & Furious's world in free roam (#821)

Forza Horizon 2 Presents Fast & Furious (Xbox 360, 2015) is a standalone part of Forza Horizon 2:
its world, `media/tracks/Anthem`, is FH2's southern Europe map cut to an area around Nice, with
places of its own (an airfield, docks). `tools/fh2ff_world.py` makes it a world FH1 drives in
free roam, from the player's own copies of the games. This is the first, command-line step: the
launcher's import comes later. The output is the player's own and is never shared.

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
FORZA_TRACK_NO_TRAFFIC=1
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
- **Spawn.** F&F streams 1,132 zones on the western 66 x 50 of FH2's 104 x 50 hex grid (FH2:
  2,798). Outside every zone FH1 draws from the nearest one, which sees the place from afar: a
  spawn there had the road under the car invisible. The spawn is the start of the longest
  stretch of road whose cells and the cells around them have zones.
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
  the full map with the car in the right place; the prototype it was ported from, 5 minutes
  along 4.6 km with 34 zones;
- a closed road driven 1.3 km past the edge of the zones: no reset;
- a closure in Nice: the barrier line and cones across the road are gone;
- the minimap draws the formerly closed roads; the full map (Start, Map) shows FH2's art under
  the roads, aligned.

`tools/test_fh2ff_world.py`: synthetic files (the version 51 layout and placements, the
procedural geometry block, the hex grid, FH1's file order, the closures, the road network and
spawn, the archive); with `FH2FF_DISC` set, F&F's own world converted and read back.
`tools/fh2ff_world.py`'s output was compared with the prototype that was driven: every file and
archive member the same.

## Not done yet

- The launcher's import (this is the tool only).
- Animated scenes (procedural type 5); the PDB's `CProceduralAnimatedScene` structs.
- F&F's own audio; its game modes (events, the map's icons are Colorado's).
- Traffic and AI on F&F's roads.
- The rest of FH2's map (the zones F&F cut away come from FH2's own world).
- NVIDIA and Intel GPUs, Linux, macOS, ReGPU.
