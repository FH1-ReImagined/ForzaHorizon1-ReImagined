# Forza Motorsport 4 cars in FH1

FH1 is built on Forza Motorsport 4's engine, and FM4 has about 400 cars that FH1 lacks. The
launcher's **Mods → Forza Motorsport 4 cars → Import FM4 cars…** converts them from the
player's own FM4 discs into a mod (#648). Nothing is downloaded: the cars are made on the
player's PC from their discs and stay there.

- [For players](#for-players)
- [What a converted car is made of](#what-a-converted-car-is-made-of)
- [physicsdefinition.bin](#physicsdefinitionbin)
- [FH1's handling retune](#fh1s-handling-retune)
- [Checked in the game](#checked-in-the-game)
- [Not done yet](#not-done-yet)
- [Credits](#credits)

## For players

1. **Mods → Forza Motorsport 4 cars → Import FM4 cars…**
2. Choose FM4's **Disc 1** (the play disc): a disc image (`.iso`) or a folder with its files.
3. Choose **Disc 2** (the content install disc) for its three car packs, or **Skip**.
4. The launcher checks that Disc 1 is FM4 (title `4D530910`), unpacks what it needs into a
   temporary folder next to the mods folder, converts every car, and adds the **FM4 cars** mod
   and a package of the cars' files (below). With the discs' files already unpacked it takes
   about 2 seconds a car; it needs about 5 GB for the result and as much again while it runs
   from disc images.
5. Start the game: the cars are in the **Autoshow** with the other cars of their makes (and
   in **Change Car** once bought).

The result is in two parts: the **FM4 cars** mod in the mods folder (the database and the car
names, a few MB) and a DLC-style package with the cars' files, `464D34434152530...4D` in the
data folder's `0000000000000000/4D5309C9/00000002` (where installed DLC goes), with its header.
The mod overlay is one archive of at most 4 GB for all mods together, and the cars take about
5 GB (FM4's textures are kept in their compressed form, as the package's zips can hold them;
a mod's files are stored uncompressed). The package's zips (`Media/DLCZips/fm4cars<n>_pri_60.zip`)
are read by the game like a DLC pack's.

Untick **FM4 cars** on the Mods page to hide the cars; **Remove FM4 cars** deletes the mod and
the package. Cars
of the import in a garage then show as missing content until they are imported again (the save
itself is not changed: import them again and they are back).

Import again after installing or removing DLC: the import carries FH1's car names table with
the names of the DLC installed at the time (the page says when it is out of date).

The mod replaces `media/db/gamedb.slt` with a copy of your own game database that has the new
cars in it (see [why](#the-database)). Another mod that replaces the database too wins or
loses by its place in the load order: keep **FM4 cars** below it, or merge the two.

The tool behind it can also be run by hand:

    tools/fm4_cars.py list    --fm4 FM4_DISC1 --fm4-content FM4_PACKS --fh1 GAME --update TU4
    tools/fm4_cars.py convert --fm4 FM4_DISC1 --fm4-content FM4_PACKS --fh1 GAME --update TU4 \
                              [--dlc USER_DATA/0000000000000000/4D5309C9/00000002] \
                              --out MODS/FM4Cars [--package USER_DATA/0000000000000000/4D5309C9/00000002/464D3443415253000000000000000000000000004D] \
                              (--cars BUG_Veyron_09,1106 | --all)

Without `--package` the cars' files go into the mod too (for a few cars: the mod overlay's
4 GB limit).

`FM4_DISC1` is the unpacked disc 1, `FM4_PACKS` a folder with the unpacked Disc 2 packages
(`tools/xcontent_extract.py`), `GAME` FH1's game folder and `TU4` its title update folder.

## What a converted car is made of

Which cars: every `Data_Car` row of FM4's database whose id and `MediaName` FH1's database (and
the installed DLC's patches) does not have, that has a model in the given files, and that is not
one of FH1's DLC cars (`FH1_DLC_CARS`: a DLC installed later must not meet a second copy). The
two games share one car id registry: no id means two different cars. FM4 Disc 1 and the three
Disc 2 car packs give 322 such cars; the Autovista pack (Disc 2, package 4) has only showroom
models and is not used.

| Part | From | Change |
|---|---|---|
| `<car>.carbin`, `_lod0`, `_cockpit`, calipers, rotors | FM4 | `tools/fm4_carbin.py` (below) |
| `stripped_*.carbin` | made | the converted file with every buffer flagged as not in the file |
| textures (`*.xds`), livery masks, digital gauges, animations (`*.gr2`), `ShaderSettings.xml`, `Physics/MAXData.xml` | FM4 | none (same formats) |
| `CarAttribs.xml` | FM4 | version 16 → 21: FH1's damage, part drop, scrape and shadow settings (from one of the player's FH1 cars; the same in all of them) over FM4's file |
| `physicsdefinition.bin` | made | [below](#physicsdefinitionbin) |
| the stock wheel (`media/wheels/<wheel>/`) | FM4 | the wheel model converted like the car's, when FH1 has no files for it (FH1's database has the rows) |
| `Thumbnail_<id>.xds` | FM4 | none (the same 884,788-byte format); the game draws a new one after a paint job |
| database rows | FM4 | [below](#the-database) |
| names (`data_car.str`, `list_carmake.str`) | FM4 | added to FH1's tables ([strings](#strings)) |
| engine sound | FH1 | FH1 has the sound banks, harmonic tuning, `CarModelTuning` and `EngineTuning` of the FM4 cars checked; what it lacks would be taken from FM4's archives |

`interior_emissive_LOD0.xds` and `zlights*.xds` are not made: 19 of FH1's own 175 cars have no
`interior_emissive_LOD0.xds` either, and FM4's cockpit materials do not sample it.

### The model (.carbin)

The two games' serializers are the same; FH1's files are a few versions newer.
[carbin-format.md](carbin-format.md) has FH1's layout. What `fm4_carbin.py` changes:

| Group | FM4 | FH1 |
|---|---|---|
| root | 2: `CCarModelData` (0x150 bytes) | 5: the same 0x150 bytes, then a light list (`u32` count, 32-byte records) |
| `CBaseCarModelData` | version 16 | 17 (the fields read the same) |
| `VertexBuffer` | version 2 | 3: a `flags` word (bit 0 a key follows, bit 1 the data is not in the file) |
| vertex | 32 bytes: SH0 as 4 × `s16` | 28 bytes: SH0 as 4 × `s8` |
| `IndexBuffer` | version 3 | 4: a `flags` word |

For the Alfa Romeo 8C, which both games ship, positions, UVs and the tangent frame are byte
for byte the same in 23,099 of 23,103 compared vertices, and FH1's SH0 is
`round(s16 / 32767 × 127)` of FM4's wherever the lighting was not re-baked. The game keeps
loaded buffers by key, so every converted buffer gets a key of its own (a CRC-32 of the car,
file and buffer number). The light list is written empty: FH1's own cockpit models have none,
and the converted cars' headlights still light the road at night (their light sprites and
FH1's indicator lamps are what is missing).

### The database

The rows come from FM4's database: the car's `Data_Car` row and the tables keyed by its id
(`CameraOverrides`, `CarExceptions`, `CarPartPositions`, `Combo_Colors`, `Combo_Engines`, the
`List_Upgrade*` tables by `Ordinal`, the suspension and anti-roll physics), then the engine,
body, drivetrain and motor rows those name (`Data_Engine`, `Data_CarBody`, `Data_Drivetrain`,
`Data_Motor` and their `List_Upgrade<Part>*` tables), the torque curves and aero rows any of
them names, and the make (`List_CarMake`) when FH1 has none. Rows whose key FH1 already has
are left out (FH1's database still holds many FM4 rows, the same ones); the 9 columns FH1 added
to `Data_Car` get the value most of FH1's cars have (`IsSelectable` 1).

They go into a full copy of the player's own `gamedb.slt` (the title update's when there is
one), not into a DB patch: a `media/db/patch/<n>_merge.slt` in the mod is found and read by the
game (the patches are looked up under `game:\media\db\patch\`, where the mod archive sits), but
the game then answers `IsCarInstalled(id) = 0` for the new cars and the Autoshow leaves them
out. With the cars in the database the game loads, it answers 1 and they are listed.

### Strings

A car's names are `"_&N"` references; `N & 0xFFFF` is the string's key in the table
(`Data_Car.str` for the car's names, `List_CarMake.str` for a make's). Both games give an
`IDS_` name the same key. A new string keeps FM4's key unless FH1's table uses it for another
string (40 of FM4's 800 new car strings): then it gets the next free key and the row's
reference is rewritten. The tables are rebuilt from FH1's copy (the title update's, then the
installed DLC's in priority order, as the game merges them), byte for byte the same when
nothing is added. The section after the IDS names (one `u16` per string) is kept as each game
wrote it; it is not decoded.

## physicsdefinition.bin

Every FH1 car archive has one (2-3 KB); FM4 has no such file and no string for it. It is FH1's
collision body of the car for the world (`PhysicsDefinition`, read by the loader next to
`\Physics\MAXData.xml`, TU4 `0x82CB4790`). The layout, from the serializer's field names
(TU4 `0x82DFDFA0`, `0x82DFD370`, `0x82DFFF1C`, `0x82E000C8`) and checked on all 176 files of
the game, which `tools/fm4_physdef.py` parses to their end:

```
u32 NumDefinitions (1)
PhysicsDefinition: u32 version (13), u32 DefinitionType (1)
  f32 Mass (0.01 in every car), f32[9] InertiaTensor, f32[9] InverseInertiaTensor,
  f32[3] HalfExtents, f32 BoundingRadius, f32[3] GraphicsOffset, u32 NumChildren (0),
  f32[3] AabbCentreOffset
u32 NumCollisionShapes, each: u32 ShapeType and
  0 sphere: u16 XShapeFlags, f32[4] PositionPlusRadius
  2 convex hull: u32 ConvexHullSize, the hull
00000001 0000 ffffffff (the same in every car)
```

- **The spheres are `MAXData.xml`'s `<CollSpheres>`**, in order, with their `Flags` (2 the
  body, 1 the small corner points): for the 8C all 36 match to 5e-6. FM4 cars have the same
  `MAXData.xml`, so these are exact.
- **The hull** is a convex polytope of 20-28 vertices, 13-26 faces (the side profile extruded
  across the car, corners cut): `u8 2, u8 4`, 14 zero bytes, an identity matrix, the offsets
  of its vertex, vertex-record, edge and face tables with the counts `V, E, F`, then
  `24 + 16 × (F / 2)` zero bytes (filled at load), `V × f32[4]` vertices, per vertex
  `(u32, u32, u8 2k, u8 k, u16 0)` pointing at its `k` edge pairs and its `k` faces, the edges
  as vertex pairs, and the faces as vertex lists wound clockwise from outside (`V − E + F = 2`
  in every file). The converter builds one of the same kind: the planes of 26 fixed directions
  (the box, the side profile every 22.5°, 45° corners) through the extreme points of the
  body's vertices (glass, lights, mirrors, wheels and the optional race parts left out), a
  little inside them as the game's hulls are (for the 8C the generated hull is 1-5% larger
  than FH1's own in each direction).
- **The rigid-body fields** (inertia, half extents, radius, offsets) are not the hull's mass
  properties and do not follow from the hull or the model in the game's files (a fit over the
  176 cars leaves most of their spread). A converted car gets the values of the FH1 car whose
  hull is closest in size, scaled axis by axis to the new hull (the inertia by the product of
  the two axes' scales).

## FH1's handling retune

FH1 retuned every car it took from FM4. The tyre compounds (37% more grip) are global tables a
converted car shares with FH1's own. The per-car values that changed are mapped from FM4's
value with a least-squares fit over the cars both games have (107, same id and `MediaName`),
computed on the player's PC from their two databases:

- body downforce (`BodyAeroForwardDownforceFront/Rear`): FH1 has small positive values where
  FM4 has negative ones; a value that maps to zero or less becomes FH1's "none" (front 0,
  rear −1), as on FH1's own cars;
- the ratings and the simulated figures the car lists show (`HandlingRating`,
  `BrakingRating`, `SimLatGees*`, `SimBrakeDistance*`, ...);
- the performance index from FM4's index and ratings (an RMS error of 0.016 on the shared
  cars, a third of a class), then the class from FH1's `CarClasses.MaxPerformanceIndex`.

The game computes a car's index and class itself once it is owned (upgrades and the garage):
the Veyron 16.4 is listed at R2 818 from the database and shows R3 770 in its garage; the fit
only decides where a car sits in the Autoshow's lists.

## Checked in the game

Linux, the Title Update 4 build of main `adb878f`, a copied save, offline, audio muted;
three cars converted from the owner's FM4 Disc 1: the 2009 Bugatti Veyron 16.4 (a road car),
the 2008 #009 Aston Martin Racing DBR9 (a race car) and the 1957 Ford Thunderbird (an old car).

| What | Result |
|---|---|
| Autoshow | the three are listed with their makes (Aston Martin, Bugatti, Ford), with name, year, picture, class and price; the Veyron's showroom shows the car and its stats |
| Garage | added to the garage with the launcher's garage code: listed in Change Car with their pictures and stats ("Veyron 16.4, 2009 Bugatti, R2 818"; "Thunderbird, 1957 Ford, D 258") |
| Drive | Veyron and Thunderbird driven in free roam (127 km/h, a guard rail hit and stopped by it, traffic around), chase, hood and cockpit views; the cockpits have their dials, steering wheel and the driver's hands |
| Upgrades | the Veyron's Custom Upgrade: Race Intake bought and installed (736 → 747 kW, R3 770 → 775), kept after a restart |
| Paint | the Veyron's body painted green in the paint shop and saved; the garage picture was redrawn green |
| Save | quit to the title screen (the game wrote the profile), restarted with the mod: the Veyron is the current car, green, with the intake. Without the mod the same save loads into free roam (the car shows as FH1's placeholder for missing content); with the mod again everything is back |
| Night | the Veyron's headlights light the road |
| Sound | not heard (muted runs); the cars' tuning and banks are FH1's own files of the same names |

## Not done yet

- The light list of `CCarModelData` (light sprites, indicators) is empty.
- The physicsdefinition.bin rigid-body fields are a scaled stand-in, not decoded semantics.
- The race car (DBR9) was bought and listed but only briefly driven; career events, online
  (other players without the import see FH1's placeholder car, as for a car mod) and every car
  one by one were not checked.
- FM4's title updates and FM4 DLC car packs are not read.

## Credits

The community mod **FM4 Imported** (https://www.moddb.com/mods/fm4-imported), built on
GTTeancum's **Forza Horizon 1 XE Mod** (https://www.moddb.com/mods/forza-horizon-xe-mod, with
D3FEKT, Wonder2K19 and Modforce), showed that FM4's cars can live in FH1. This converter was
written from the two games' files and executables; nothing was taken from those mods, and
their packages are not used or needed.
