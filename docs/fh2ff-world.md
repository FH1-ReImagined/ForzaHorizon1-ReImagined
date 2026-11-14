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
  20 bytes off and faulted. The tool rewrites the block FH1's size and moves the rest to keep
  its 16-byte alignment. An animated scene (type 5: the fairgrounds' rides, fireworks, stage
  lights) is laid out as Colorado's 202 are: its name is followed directly (4-byte aligned) by
  its geometry references (each the file number of an animated object, type 4, with its Granny
  animation) and its 28-byte `CAnimatedSceneData`; only its 128-byte object instances start
  16-byte aligned. Moved to the next 16 bytes, the 8 scenes with the block lost their reference
  (the Ferris wheel's 14 became 0) and FH1 faulted in their update (lr 82ED8CE4, 82ED67B0): the
  references and data move with the name now, and every scene is in (`FH2FF_PGEO_DROP=5` leaves
  them out, a test switch).
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
- **Races and playground arenas** (data only: not tried in a lobby yet). FH1's races and its King,
  Infected and Cat & Mouse arenas are Tracks rows (RibbonConfiguration 0 a circuit, 2 point to
  point, 7 an arena) with a `TrackRouteNNN.xml` in the ribbon. F&F's and FH2's databases have the
  same Tracks table; the world's rows with a route file in its ribbon (F&F: 54 circuits, 119 point
  to point, 35 arenas at the docks, the abbey, the chateau, the airfield, the warehouse;
  FH2: 54, 121 and 35) go into the mod's database as ids 5000 on, on Environments 70, showing
  their developer name (F&F's string ids are not in FH1's tables). Their route files take FH1's names: an arena's edge
  `Arena_NN` is `route_boundary_NN`, a race gets `post_race_location_NN` (from F&F's
  `end_position_NN`, else its start places), and every facing is a unit vector (F&F's are 2 to 4
  long). Their AI lines (`aiopenworld.zip`'s `RouteNNNN.owt`) are OWTM version 2: a table of
  segments (first waypoint, the next segment, the other at a branch, the count) over 48-byte
  waypoints (position, the vector across the road, -0.5, 0.5, the racing line). FH1 reads
  version 1, one line: the tool follows the main chain from the first segment (looped when it
  comes back to it) into `media/aiopenworld/anthem/ribbon_00/route_NNN.owt` in the mod (F&F: 86
  lines, 24 looped; the largest step between points 19 m).
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
- **Gameplay markers.** Both games place their event starts (`e465`), bucket list spots (`bl05`),
  festival sites, town hubs and resets (`freeroam_reset_09_left`) as instances of 15 plain
  shapes, shown or hidden by name (the placement's last 32 bytes, `GameObjs.xml`'s GameplayID).
  FH1 knows none of the names and drew them all, always: pink boxes, discs on poles and tubes
  across the roads. A named placement with no physics key whose instance lacks the
  shadow-casting flag (0x08000000, the FH2 prototype PDB's `SubsetMask_CastCSMShadows`) is left
  out of the zones (F&F: 12,180 placements over its zones; FH2: 38,727). The named things (speed
  cameras, reward boards, barn doors) have the flag or a physics key and stay.
  `FH2FF_MARKERS=keep` draws them (a test).
- **What FH2 does not show.** FH2's instance flags (the prototype PDB's `PVS::ESubsetMask`) mark
  objects as shadow only (bit 23: simplified shapes that just cast shadows) and cube map only
  (bit 24: stand-ins for reflections). They are whole-world models placed at the origin and
  listed in about a tenth of the zones each (F&F: 50 and 76 instances; 7,865 zone placements).
  FH1 reads the bits as other things (Colorado never sets bit 23, and sets
  bit 24 on its roads) and drew them over the world: the road black for a stretch after a
  motorway overpass, black walls in the coast towns, black guard rails. They are left out of the
  zones; `FH2FF_UNSHOWN=keep` draws them (a test). The flag "hidden" (bit 0) is another matter:
  its 239 instances are the roadside trees, fences and shrubs, and stay.

F&F's free-roam resets (the "hand of god" when leaving its area) are not in its data: no
surface is a reset trigger, and the HandOfGodReset values are the races' off-track ones. With
FH1's code and Colorado's game modes there are none: a drive along a closed road 1.3 km past the
zones' edge went on without a reset or a wall.

## One world: FH2's with Fast & Furious's docks (`tools/fh2ff_combine.py`)

The big part of Fast & Furious's world that FH2 lacks is its docks. `fh2ff_combine.py` writes a
third world folder, in the two games' own format, that is FH2's whole world with F&F's docks in
it; `fh2ff_world.py build` then converts that folder as it converts either game's:

    tools/fh2ff_combine.py FH2_DISC FF_DISC OUT_DISC
    tools/fh2ff_world.py build OUT_DISC FH1_DISC UPDATE_MEDIA_ZIP OUT --fh2 FH2_DISC

OUT_DISC must be new, empty or an earlier output (the tool writes `.fh2ff-combined` into it and
replaces only a folder that has it), and not one of the game folders or in one. The combine step
takes about 4.7 GB of memory at its peak, with either choice (both worlds' zones are held read);
the build step after it about 1.2 GB.

With `--all`, F&F's zone is used in every cell F&F has one (1,132): F&F's version of its whole
area, with its extra trees and detail, in FH2's world (see "The whole area" below).

In the launcher: **Import FH2 + F&F…** on the Mods > FM4 tracks card asks for both games' folders
and whether to take F&F's whole area or only its docks, and runs both steps (about 10 minutes);
the worlds are kept as `fm4-tracks/fh2ffall` and `fm4-tracks/fh2docks`.

**Where the docks are.** The two worlds share a hex grid. F&F has zones in 24 cells FH2 has none
in, and rebuilt 37 more round them, where FH2 has an older dock area of its own (a crane,
warehouses and plain tanks on sand). In those 37 cells the two worlds share 8 to 30% of their
placements; elsewhere 50 to 90%. The tool takes F&F's zone in the cells FH2 lacks and, growing
out from them, in every neighbouring cell whose two zones are less than 40% alike (`--similar`):
61 cells. FH2's zone stays everywhere else.

**What is renumbered.** F&F's tables go after FH2's in the layout, and everything of F&F's that
names one moves with them:
- zones, models, instances and texture records; model, procedural geometry, texture, bundle,
  zone and soundscape files by the number in their name;
- procedural geometry (`.pgeo`, read with the FH2 prototype's PDB and checked on all 15,684 files
  of both worlds): texture record numbers after the name, a scene's geometry file numbers, and a
  Models file's layout instance numbers (in its model references' LOD lists);
- bundles: their textures by record number (not by id), their own ids carrying on after FH2's
  (an id is 0x10000001 + 2k);
- a zone's file records: a zone's own resource number is the zone, FH2's other resources move
  past the added zones, and a file both worlds name takes FH2's record **of the same type** (by
  the file alone, a collision record took the numbers of FH2's preload record of that file, and
  the game broke its stream list as the first added zone went live).

**Collision.** Both worlds index collision as 100 m cells, but F&F's grid starts 12.72 m north of
FH2's (the index's header). F&F's cells are cut again for FH2's grid (`retile_fiz`: the triangles
of a cell and its neighbours that reach the new square, and the cell's 12 x 12 squares' lists made
again; made in place, a cell's lists come out as the original's). With F&F's cells as they were,
the car fell through the road in a strip at every row's edge (z = -1856.4, -1956.4, ...).
A cell also has a list of ids (in both games a race's route number: its barriers), and a
triangle's low flag bits select from its own cell's list; a triangle with 0x8000 is always there.
The lists differ from cell to cell (1,136 of F&F's 3,964 cells have a neighbour with another
list), so a cut cell's list is the ids its triangles select and their bits are set again for it;
the largest such list is 10 ids of the 15 the bits allow. Free roam selects none of them.

**What is merged.** In the 61 cells: F&F's collision cells and shadow squares; F&F's collision
objects (`CollObjs.xml`; its road-closure fences there are left out, with their instances) in
place of FH2's; F&F's roads that FH2 has not, joined to FH2's network where they meet it (each
within a metre of an FH2 node). A road both worlds have stays FH2's, with its types and traffic:
F&F marks its roads near its edge closed and empty, and traffic stood still where they met FH2's. Round the 61 cells, FH2's zones lose FH2's old dock instances and
procedural geometry and see F&F's docks as F&F's zone of the same cell does. The rest (lighting,
races and arenas, map art) is FH2's; FH2's dock arenas' route files are byte for byte F&F's.

**Breakable objects.** A zone's placement of a sign, lamp post or fence names a key in the
world's `PhysicsDefinitions.bin` (TU4 `sub_82E00038` reads it; the fields are the FH2 prototype
PDB's): a count and flat records (version 13; a definition is a record and its children, which
share its name hash), a table of keys to definitions, and a key's layout model numbers. Each
world numbers its keys for itself (FH2: 145 keys over 139 definitions; F&F: 157 over 153; 135
definitions shared by name hash). With FH2's file alone F&F's zones took other objects'
definitions, and its keys from 145 on none: the game stopped on a null definition in F&F's
hills and towns (three of eight survey lines). The files are merged: FH2's definitions, F&F's
18 of its own after them (157 definitions, 454 records), F&F's keys after FH2's (302) with
their models numbered for the combined layout, and every F&F placement's key moved by 145.
`SmashableObjectTypes.xml` is the longer of the two (F&F's is FH2's with one more type).

**Linked instances.** An object with models for several distances is an instance for each,
linked in a ring by a signed 16-bit offset in the layout's instance table (the PDB's
`m_LODParentPVSModelInstanceOffset`; an offset longer than 16 bits is stored cut, and the
target is the one of the three readings inside the table). Leaving instances out and
numbering the rest again broke the links: the instances a kept one is linked to are kept with
it (663 more for the docks, 2,200 for the whole area) and every link is written for the new
numbers.

**Checked** (the rig of the runs above, every run's frames looked at). Docks only: the dock road
for 100 s, two more dock roads for 90 s each, FH2's coast road across the docks' edge for 150 s
with traffic. The whole area: the same four, 2.4 km through Nice in 200 s, and F&F's eastern
border crossed both ways with traffic. No fault, fall or missing surface; the docks draw as in
F&F's own world (ground, striped tanks, containers), and the map shows the dock roads. FH2 combined with itself over 22 cells also runs (a check that the
renumbering alone changes nothing). With the merged physics table and the links: eight
lines over the whole area at speed (170 s each, 2 to 3 km: Nice, the coast, the hills, three of
which stopped the game before), the docks and seam drives again, and two stretches side by side
with F&F's own world (the same picture).

**Textures.** 5,072 of F&F's 10,233 texture records are byte for byte one of FH2's under another
id (files or bundled data, and the same record): those use FH2's record; the rest get records of
their own.

**The whole area (`--all`).** Outside the docks F&F changed the shared world a little everywhere:
in the 1,071 cells both have, F&F's zones name 26% more vegetation files, 304 cells have a tenth
more placements than FH2's and 149 a tenth fewer. With `--all`:
- FH2's 1,108 zones under F&F's are written empty, and the 2,779 model and procedural geometry
  files only they used are left out: 63,798 files in all (the archive format takes 65,535);
- FH2's zones outside F&F's cells keep FH2's own view of the area, which changes to F&F's as the
  car crosses in (no gap or pop seen on the road driven both ways);
- F&F's models are not FH2's byte for byte (57 of 6,812 are), so both sets are kept: 1.25 GB.

**Not done:** F&F's lighting; bundles are stored uncompressed.

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
- with the world's races and arenas in the database (208 rows): F&F loads and drives, 150 s;
- animated scenes in: 5 minutes along the spawn's road (where a build with them faulted at 27 s
  before the fix), and the Nice fairground drawn with its rides moving (the SkyMaster's arms);
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

- F&F's own audio; its game modes (events, the map's icons are Colorado's).
- FH2's far east. FH1 streams the zones round a point it clamps near x = 8.2 km: a start at
  x 9.1 to 9.4 km (zones 2422 to 2535) had zone 2250 (x 8.25 km) made live and nothing else, the
  car held still with no collision under it. FH2's grid reaches x 10.85 km (about the last
  sixth of it) and its roads 14.8 km. Either the clamp found in the game (17 floats of 8192 in
  the TU4 image to look through) or the world moved 3 km west (every placement, the hex grid,
  the collision cells and index, the road network, the ribbon's XML) is the next step.
- F&F's places in FH2's world (its airfield, docks): one world or the other for now.
- Races and arenas in a lobby, and AI racers on the converted lines (the data is in: see above);
  the routes' own names (FH1 shows the developer's); a route's branches (its main line only).
- NVIDIA and Intel GPUs, Linux, macOS, ReGPU.
