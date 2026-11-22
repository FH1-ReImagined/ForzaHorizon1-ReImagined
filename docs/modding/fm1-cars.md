# Forza Motorsport (2005) cars in FH1

Forza Motorsport, the original Xbox game, has cars FH1 lacks. #930 brings them into FH1 from the
player's own FM1 disc, cars first, then circuits. This page holds what is known of FM1's car
files and how far the import has come. Nothing of either game is in this repository.

- [Where it stands](#where-it-stands)
- [Try it](#try-it)
- [FM1's car files](#fm1s-car-files)
- [How a car is built](#how-a-car-is-built)
- [A car of its own](#a-car-of-its-own)
- [Checked](#checked)
- [Not done yet](#not-done-yet)
- [Credits](#credits)

## Where it stands

- `tools/fm1_cars.py` builds an FM1 car's model, textures and lamps into an FH1 car's files (the
  donor).
- `tools/fm1_import.py` makes the FM1 car a car of its own: a mod with its files, its database
  rows (the donor's, under a new id), its names with "(FM1)", FM1's chassis physics and FM1's
  wheels, and an engine and drivetrain of its own with FM1's engine and gearbox in them. With
  `--package` it has FM1's own engine recordings (`tools/fm1_sound.py`), else it sounds like an
  FH1 car with a similar engine (`ENGINE_SPECS`). Its car list picture is FM1's
  model drawn by `tools/fm1_thumbnail.py`, its steering wheel turns about FM1's wheel.
- Next:
  - its place in the shared car id table (`tools/car_ids.json`, #924);
  - the launcher's importer.

## Try it

```
python tools/fm1_cars.py parts FM1_DISC bordermr2
python tools/fm1_import.py FM1_DISC FH1_GAME bordermr2 MAZ_RX7_97 OUT [--update TU] [--base-mod FM4_MOD]
    [--package DLC/4D5309C9/00000002/464D3143415253000000000000000000000000004D] [--skip-elsewhere]
python tools/fm1_sound.py FM1_DISC bordermr2 [--wav FOLDER]
```

`--package` gives the car FM1's own engine sound: its banks go into that DLC package folder, in
the user data folder the game reads DLC from (`fm1_sound.py` lists a car's loops per level and
can write them as `.wav` files).

`FM1_DISC` is a folder with FM1's files (its `media\cabs`), `FH1_GAME` the FH1 game folder, `TU`
the title update's folder. Copy `OUT` into the mods folder: the game then has the car
(id 55239, `TOY_BorderMR2_95`). The mod holds a full copy of the game's database and of its
`data_car.str` tables, with the car added. With an FM4 cars mod also installed, pass that mod's
folder as `--base-mod`, so its cars and names stay, and order the FM1 mod after it (the game
uses one mod's database).
`fm1_cars.py car ... --mod` puts the FM1 car into the donor's own files instead (the RX-7 then is
the FM1 car). Standard library only.

Several cars in one mod: pass `FM1=DONOR` pairs, comma-separated, in place of the car and the
donor:

```
python tools/fm1_import.py FM1_DISC FH1_GAME bordermr2=MAZ_RX7_97,HondaS2000=HON_S2000_03 OUT [...]
```

Each car's rows go into the database the previous car's import wrote; a car that fails is left
out (listed at the end, exit status 2) and the rest go on. The best donor is FH1's own version
of the car where FH1 has one (27 of FM1's production cars have one); its engine spec, sound
and ratings then stand in for what FM1 lacks.

`--skip-elsewhere` leaves out the FM1 cars another game has already: FH1 with its official DLC,
Forza Horizon 2, Fast & Furious, or FM4 with its DLC (`FM1_ELSEWHERE` in `fm1_import.py`, 153
of FM1's 231 cars, each with that car's MediaName and the games that have it). The list was
matched by hand from FH1's database with its DLC merged in and `car_ids.json` (#924) on make,
model, generation and trim:

- A race car matches only that race car. FM1's #12 Impul Skyline is FM4's `NIS_12_Skyline_03`,
  but its #16 CTS-V race car has no match.
- FM1's numbered cars of one race model match it: the Audi R8 #4 and #38 are both FM4's R8.
- A car of another generation doesn't match. FM1's 2005 CL65 is not FM4's 2010 one.
- Tuner cars don't match: FM1's AB Flug, Veilside, Mugen NSX and Tommy Kaira cars are left in.

## FM1's car files

Measured on the game's disc. Little-endian.

| File | What |
|---|---|
| `media\cabs\car_<car>.cab` | the car's archive: `cars\<car>\car.carbin` (the model), `cars\<car>\liveries\standard\livery0.xpr`, `livery0hi.xpr` and `livery0..6.livery`, `cars\<car>\physics\...`, the engine's sounds |

**Archives** (`.cab`; `.ca2` and `.xb2` are the same, the whole file stored as back-to-back zlib
streams): u32 magic `0xC0C0AAAA`, u32 version 15, u32 count, u32 flags (bit 0: each file is its
own zlib stream), more header words; from `0x206`, count − 9 entries of 6 × u32 (hash, offset,
size, unpacked size, name offset, descriptor offset); a u32; the NUL-terminated names.

**car.carbin** is a tree of chunks: u32 size (the whole chunk), u32 type, a NUL-terminated name,
the payload, then child chunks; u32 counts sit between some of them.

| Chunk | Payload |
|---|---|
| `Car` | none; children `Attribs`, `Driver`, a u32 part count, `Part`s, two `Flare`s |
| `Attribs` | u32 1, u32 5, the 4 wheel centres (f32 × 3 each: LF, RF, RR, LR) |
| `Driver` | seat and camera positions (**not decoded**) |
| `Part` | u32 hash, u32 name length, the name; children `Verts`, `Damage`, `Pivots`, `Attributes`, a u32 count, `Subpart`s |
| `Verts` | u32 count, 20-byte vertices: s16 × 3 position × 5/32768 (metres), s16 × 3 normal / 32767, u16 (**not decoded**), u16 0, s16 × 2 UV / 32768 |
| `Subpart` | u32 LOD (1 the most detailed, up to 5), u32 material id, u32 6, u32 index count, 16 bytes (zero at LOD 1, **not decoded** at the others), the u16 triangle-strip indices. Every LOD indexes its part's one vertex list. |
| `Flare` | u32 0, u32 colour, f32 size, f32 × 3 position, f32 × 3 direction: a lamp's glow (the Border MR2's two are its raised headlights) |

The car's space: x to its right (its left is −x), y up, z forward.

**Parts** are named `<part>_LOD1` (`body_LOD1`, `hood_LOD1`...; `steering_wheel` and `driver01`
have no suffix). Lettered parts (`bumperFa`..`g`, `hooda`..`d`) are the visual upgrades, `a` the
stock one; race parts carry `race` or `cage`; `headlight[LR]down` and `headlight[LR]up` are
pop-up lamps lowered and raised.

**Material ids** are not named in FM1's files (`Mega.ca2 cars\shadersettings\defaults.xml` names
its shader groups: Body, Window, HeadLights, TailLights, BlackGlass, Chrome, Badge, Grille1...
but not their ids). Found by drawing each id's meshes of the Border MR2 (**measured on one car**):

| Id | Where | FH1 technique used |
|---|---|---|
| 1 | the paint | `body` |
| 2 | windows | `window` |
| 3 | tail and brake lamps | `taillight2S` |
| 4 | head lamps, the bumper's lamps | `oldhead` |
| 6 | tinted glass (the MR2's T-top) | `black_glass` |
| 7 | exhaust tips, mirror glass | `chrome` |
| 9 | black trim, grilles, wheel arches | `black` |
| 14 | the cockpit | `interior` (`plastic` in `_cockpit`) |
| 18 | suspension, underbody | `undercarriage` |
| 19 | the wing's planes | `carbon_fiber` (**a guess**) |
| 21 | window seals | `rubber_trim` |
| 25 | badges | `badge` (`emblem` in `_lod0`) |
| 27 | lamp reflectors | `textured_reflector` |
| 5, 15, 20, 28 | small trim (lower openings, side mouldings, engine cover vents) | `black` |

The wheels are `Mega.ca2 tires\art\<car>\tire.carbin` (the same chunks, one wheel) and
`MegaXPR.ca2 tires\art\<car>\wheel.xpr` (64 × 64, mip levels) and `wheel_vol.xpr` (a volume of
4 slices, the spin blur); materials 8 tire, 11 rim, 12 inner barrel, 13 the flat blur disc. The
wheel is drawn once; FM1 mirrors it for the other side. Its outer face is at −x, as in FH1. A car
names its wheel in `Cars.ca2 cars\<car>\tooldata.xml`, `<WheelStyle><P0 File="...">`: the Border
MR2's is the MR2 Turbo's, `toyotamr2`.

**Textures** (`.xpr`, XPR0): u32 magic, u32 size, u32 header size, then 20-byte descriptors (u32
common, u32 data offset from the header's end, u32 lock, u32 format, u32 size) up to
`0xFFFFFFFF`. Format bits: 4-7 dimensions (3 a volume), 8-15 the colour format (`0x0F` DXT5),
16-19 mip levels, 20-31 log2 width, height, depth. DXT blocks are stored linearly, level after
level; a volume's slices are interleaved block by block (block *i* of slice *s* at *i* × depth + *s*).

- `livery0hi.xpr`: 1024 × 1024 DXT5 with its 11 levels, the car's atlas: the paint's decals
  (badges, stripes), the lamps, the cockpit pieces.
- `livery0.xpr`: 512 × 512 × 2 DXT5, one level: the same atlas; slice 0 clean, slice 1 with
  dirt and scratches.
- The atlas's alpha is FH1's own convention for `nodamage.xds`: opaque where something is
  printed, clear where the paint shows.

## How a car is built

`tools/fm1_cars.py` reads the car; `tools/fh1_carbuild.py` builds it into the donor's
`<car>.carbin`, `<car>_lod0.carbin` and `<car>_cockpit.carbin` (a plain-Python port of Forza
Imported DLC Maker's builder); `tools/fh1_carbin.py` reads and writes the files (parse and build
give back the same bytes for every model of the cars tried) and `tools/fh1_xds.py` the textures.

- **Placement.** FM1's z is negated (FH1's models face −z), which mirrors the model, so every
  triangle's winding is turned. The car is moved so its axles' middle and wheel centres' height
  sit on the donor's (`CarPartPositions` 5-8 in FH1's database), so the donor's wheels and
  suspension fit its arches.
- **Parts.** The stock parts go into the donor's sections by name (`bumperF` into `bumperFa`,
  `hood` into `hooda`...); a part the donor has no section for goes into the body (the MR2's
  wing goes onto the trunk lid). The donor's upgrade variants (`bumperFb`, `hoodq`) get the stock
  part, its wheel stays, and every other section of the donor is emptied (a zero-area triangle).
  The driver and the raised pop-up lamps are left out; the lowered lamps are fixed in place.
  Left and right exhausts go into `exhaustLa`/`exhaustRa` (or the one `exhausta`), lamp glass
  into `glassLHL`, `glassRHL`, `glassLTL`, `glassRTL`.
- **The stock look** is the car's `Cars.ca2 cars\<car>\tooldata.xml`: each visual group
  (`FrontBumper`, `RearBumper`, `Wing`, `SideSkirt`, `Hood`, `Mirror`, `Headlight`, `RearLense`,
  `Antenna`) lists its options `P0`, `P1`... with their parts (`<Parts P0="bumperRa_LOD"
  P1="exhaustLa_LOD"/>`); `P0` is stock. The model keeps `P0`'s parts and every part no group
  names, and leaves out the other options and the lettered parts of a family the groups use,
  listed or not (the S2000 has `winga`-`wingg` though its stock wing is `wingNone` and only
  `wingb`/`wingc` are options). Some cars have the driver as separate limb parts (`Head`,
  `Chest`, `Left_forearm`...): they are left out with `driver01`. Without a tooldata, of a
  lettered group only the "a" is kept.
- **LODs.** FH1's LOD *k* (1-5) gets FM1's LOD *k*, and past the car's last FM1 LOD that one
  again; a part FM1 stops drawing earlier stops in FH1 too. FH1's LOD 0 (`_lod0` and `_cockpit`)
  gets FM1's LOD 1. The cockpit's `a_pillar` gets the body's triangles within the donor's
  `a_pillar`, as FH1's cars repeat that part of the body there.
- **Encoding** as FH1's: positions as a direction and its length within the section's bounds
  (every shipped vertex's xyz has length 1), a tangent-frame quaternion, UVs within each mesh's
  own rectangle, triangle strips. The steering wheel's section is centred on FM1's wheel.
- **The turning steering wheel.** The game turns the `steering_wheel` section about the pivot and
  axis in the model header's DriverControls (`CBaseCarModelData`, version 3: 16 rows of 4
  big-endian floats, model space), not its section's centre. Rows in the main model: 0 pivot,
  1 axis (towards the driver), 2/3 the hands on the rim, 4 the seat, 5 on the car's middle
  (likely the gear lever); in `_cockpit`: 6-11 points round the rim, 12 pivot, 13 axis, 14 the
  seat. With the RX-7's rows the MR2's wheel swung about the RX-7's pivot, 0.77 m to its right,
  up over the roof as soon as it turned. The import puts the pivot on FM1's wheel, moves the hands
  and seat with it, leaves row 5, and sets the axis to the normal of the plane FM1's wheel lies in
  (its least-spread direction; the faces' mean normal leant it 7°): for the MR2
  (0.003, 0.238, 0.971), the RX-7's being (0.017, 0.233, 0.972).
- **Baked lighting (SH0):** 4 signed bytes per vertex, fitted on the normal from the donor's own
  vertices. On FH1's cars they are a positive constant, then three terms that follow the normal's
  z, x and y. Read as unsigned bytes, as Forza Imported DLC Maker fits them, the fit misses the
  RX-7's by 75-89 per byte; read signed, by 30-38. Each section is fitted on the donor's section of
  that name (`SHModel(..., by_section=True)`, sections of 200 vertices or more): one fit over the
  whole car mixes the dark interior, glass and underside into the paint's (the '94 Miata: a
  constant of 75 over the car, 90 over its body), and FM1's cars drew darker than FH1's in the
  same paint (the MX-5 in the Miata's red: maroon). With both fixes below it is close to it.
- **The second uv set** is one point, (0, 0), as on most of FH1's meshes (the Miata's body: scale
  0); FM1's uv given again there darkened the paint too.
- **Draw lists.** The header's per-LOD lists are rebuilt: a rebuilt section is drawn at the LODs
  the donor drew it at, in the transparent or the opaque list as the donor draws its material.
- **Textures.** `livery0hi.xpr` becomes the donor's `Nodamage`, `lights`, `damagelights` (and
  their `_LOD0`) and `interior_LOD0` textures with FM1's own mip levels, the blocks only
  reordered, so the car's UVs stay; `livery0.xpr`'s second slice becomes `Damage` (mip levels
  made here). FH1's DXT5 layout (tiled, 16-bit words swapped, the smallest levels packed into
  one 32 × 32-block surface) rebuilds the donor's own textures byte for byte.
- **Lamps.** The main model's light list gets a headlight and its flare at each of FM1's
  `Flare`s, and a tail light and two brake sprites at each side's tail lamp meshes (FM1 gives
  those no `Flare`). One set a side, at the middle of all of that side's lamp pieces: with a set
  at each piece of a lens with a gap (the MX-5's), the brakes glowed twice as much as the
  donor's.
- **Lamp lenses.** FM1's tail lamp material becomes FH1's lamp glass (`lights_gls_taillight2S`,
  else `lights_gls_tail_light`), as the donor's lamps have it over their lamp body: the lamp
  body material (`taillight2S`, `tail_light`) shines what its texture shows, and with FM1's red
  lens on it the MX-5's lamps and third brake light glowed in daylight where the '94 Miata's,
  on the same road, stayed dark. On the glass they look like the Miata's (the brake sprites
  light them). The lenses go into the donor's `taillightL`/`taillightR` of their side wherever
  FM1 has them (the MR2's are in its body, the MX-5's third brake light in its trunk).
  When the donor's head lamps are pop-ups (its `caranimationevents.xml` animates `headlight`, as
  the '94 Miata's), FM1's fixed head lamps go into the body: in `headlightL`/`R` they stood up
  with the pop-up animation.
- **Lamp glass and other materials found by where FM1 uses them** (all 211 cars with a model):
  28 is lamp glass (173 `glassLHL`/`RHL` headlight glass parts, 162 headlights, tail lamp and
  other lamp glass) and becomes `lights_glass` or `detail_glass_clear`: mapped to black before,
  it covered the MX-5's headlights with a black shell over their reflectors (27, the same shape
  under it); with it clear the reflectors and lamps show. 31 clear lamp covers (body, bumpers,
  lamps), 16 tail lamp glass (`glassLTL`/`RTL`), 30 the reverse lamps in the tail lamps, 35 the
  front bumpers' and hoods' grilles (197 uses). 29 (trunks, interiors, wings) is not placed and
  stays black.
- **Head lamps (4)** go on the reflector material (`textured_reflector`), as under FH1's lamp
  glass: on FH1's lamp materials (`oldhead`) FM1's lamp texture shone, and the Supra's lamps
  blew out white where FH1's own Supra's glowed softly on the same road at the same time. A
  donor without lamp glass (the RX-7, the S2000) gives the tail lamps `detail_glass_red` before
  its lamp body. Checked against FH1's own F50, RX-7 and Supra on the same road: no glare; the
  FM1 F50's tail lamps stay a little brighter than FH1's, the FM1 RX-7's a little darker (FH1's
  are lit softly there, with the lights on).

## A car of its own

`tools/fm1_import.py` makes a mod from an FM1 car and a donor FH1 car.

**What FM1 gives** (`Mega.ca2`; measured):
- `cars\carlist.xml`: per car `<car name= displayName= Make= Ordinal=>`. The Ordinal is FM1's id
  (the Border MR2: `BorderMR2`, "Border MR2 Turbo T-bar (Wiedebody)", make "Border", 239).
- `forzadb\cardb\model.dat`: 104-byte rows, u32 Ordinal at 0, make id at 4, year at 16 (the MR2:
  57, 1995).
- `make.dat`: 28-byte rows, u32 id, region, string id, then the name (57 is Toyota).
- The liveries (`livery0`-`6.livery`): a paint colour each, metallic or not.

**The rows.** The donor's rows are copied the way Forza Imported DLC Maker's `fh1_port.clone_db`
copies them: every row that names the donor's id goes under the new id, including the upgrade
lists and their physics rows, whose ids are the car's id × 1000 + n. Unlike `clone_db`, the body
gets the new id too: `Data_CarBody` and the body upgrades, which name it by `CarBodyID`. Copied
under the donor's body id, they would sit on the donor's own body. A copied row whose unique key is
not made from the car's id gets the table's next free key (`Combo_Engines.EngineComboID`;
otherwise it collides with the donor's row). The engine and drivetrain get ids of their own (see
**Engine** below), so the tables keyed by `EngineID` or `DrivetrainID` are left to that step.
Left out are the events the donor is in (`EventParticipants`, `EventRecommendedCars`, its
`CarChallenges`), the AI drivers, the rewards and the arcade lists.

Two rules learnt from cars that hung the game at the title's "Loading" (the profile loads the
current car's physics; `FORZA_SQL_LOG=1` showed the last query, `Data_CarBody`, and the step after
it never ran):
- Tables with no car or body column are copied too when their rows are named by the car's id
  × 1000 + n: `List_AeroPhysics`, which the wing rows name (the Stratos's and the Impreza 22B's
  aero rows are numbered from the car; other donors share aero rows). Leaving it out left the
  wing rows pointing at nothing.
- A body's id is its car's (424) or its car's × 1000 (458000); the new body's takes the same form.

**What changes from the donor:**

| Field | From |
|---|---|
| id | 55000 + FM1's id (#924's rule for a car new to FH1): 55239 |
| `MediaName` | FH1's code for the make, FM1's name and the year: `TOY_BorderMR2_95` |
| `MakeID`, `Year` | the FH1 make of FM1's make (by `List_CarMake.IconPathBase`), FM1's year |
| `DisplayName`, `ModelShort` | FM1's name and "make name", each with " (FM1)": "Border MR2 Turbo T-bar Widebody (FM1)", "Toyota Border MR2 Turbo T-bar (FM1)" (FM1's "(Wiedebody)" spelt and unbracketed) |
| `Combo_Colors` | the liveries' paints, each in the colour class of FH1's nearest paint |
| `CarPartPositions` | 0/1 the head lamps, 2/3 the tail lamps, 4 the steering wheel's centre (FM1's), 9 where it sat from the steering wheel in the donor (mirrored: the MR2 is left-hand drive, the RX-7 right). 5-8, the wheels, stay the donor's: the model is placed on them |
| `Thumbnail` | the new id's file: FM1's model drawn by `fm1_thumbnail.py` (below) |
| `StockWheelID` | a `List_Wheels` row of its own: the donor's wheel's, under the car's id (or the table's next free id), `MediaName` and `IconPath` the car's `MediaName` |

**The mod:**
- `media/cars/<MediaName>/`: the donor's files renamed, with the FM1 models (and their `stripped_`
  copies) and textures in them.
- `media/wheels/<MediaName>/`: the donor's wheel archive (`media/wheels/MAZ_RX7_97.zip`)
  renamed, with FM1's wheel in its model. The model has one section, `wheel`, with LOD 0 in one
  vertex pool and LODs 1-5 in the other. FM1's rim, inner barrel and blur disc replace the
  donor's (FH1's LOD 0 from FM1's LOD 1; FH1's LODs 1-5 from FM1's 1-4). The tire stays the
  donor's: FH1 draws it with its shared tread textures, which FM1's tire is not mapped for. FM1's
  wheel is scaled to the donor's tire radius (0.316 / 0.297 for the MR2), which the physics' tire
  sizes still use. The donor's tire is then fitted to FM1's rim: its sidewall is stretched
  radially so its bead meets FM1's (0.236 m → 0.201 m for the MR2's rim in the RX-7's tire; left
  as it was, a 3.5 cm ring showed between rim and tire). Its tread radius and UVs stay. `wheel.xds` and `wheel_LOD0.xds` are FM1's `wheel.xpr`; `wheel0-3.xds`, the
  spin blur, are its `wheel_vol.xpr` slices.
- `media/db/gamedb.slt`: the game's database, or the `--base-mod`'s, with the rows added. A car in
  a database patch (`media/db/patch/<n>_merge.slt`) counts as not installed, as
  [fm4-cars.md](fm4-cars.md#the-database) found. Tried here with a patch: the game read it, then
  put the player in another car of the garage instead.
- `media/stringtables/<lang>/data_car.str`: the names, under keys free in every language, with 0
  in the table's undecoded fourth field.
- `media/audio/cars/carmodeltuning/<MediaName>_CMT.xml` and
  `media/audio/cars/engines/enginetuning/<MediaName>_ET.xml`: the sound tuning of the car
  `ENGINE_SPECS` names (the MR2: `TOY_MR2GT_95`, FM4's, still in FH1's sound archives), renamed.
  The engine tuning is found by the engine's `Data_Engine.MediaName`, so it takes effect only
  with an engine of the car's own: with the RX-7's engine shared, the game read the car's
  `_CMT.xml` but never its `_ET.xml`; with its own engine it reads both.
- `media/ui/textures/thumbnails/thumbnail_<id>.xds`: the car list's picture. FH1's are 768 ×
  288, format 6 (8_8_8_8, 8-in-32 byte order), tiled, one level, behind a 52-byte header: the car
  cut out (clear alpha round it, a soft shadow under it) from its front left at three quarters
  in x 128-470, and its right side turned nose up in x 670-752. `fm1_thumbnail.py` draws FM1's
  model that way (the stock parts at FM1's LOD 1, FM1's wheel at the four wheel centres, the
  paint FM1's first livery colour under the livery texture, one light, a z-buffer, 2 × 2
  samples; plain Python, about 4 s) into the donor's thumbnail as the template. FM1's space is
  left-handed (x right, y up, z forward): the first try came out mirrored.

## Checked

| What | How |
|---|---|
| FM1 reading | every strip index of the Border MR2 (43,758) within its part's vertices |
| FH1 files | `fh1_carbin` parse → build gives back the same bytes for all 11 models of the RX-7 and the Alfa 8C; its stripped copies too; `carbin_dump.py` parses the built models to their end |
| Geometry | the built models drawn from four sides at LODs 1, 3, 5 and 0: the MR2's shape, its arches on the RX-7's wheel positions |
| Textures | `fh1_xds` rebuilds the RX-7's 512, 1024 and 2048 textures byte for byte; the built ones decode to FM1's atlas |
| Tests | `tools/test_fm1_cars.py`: synthetic FM1 and FH1 files, no game file |
| In the game | the Border MR2 as a mod in place of the RX-7 (ReImagined v0.37, the festival hub at dusk, two runs): the game reads the mod's 15 files; the car loads, drives and steers; from behind, both sides and the front (looking back) it is the MR2 with its wing, T-top, aero parts and closed pop-up lamps on the RX-7's wheels; its tail lamps light and its badges show; the hood, bumper and cockpit cameras work; nothing new in `forza.log`. (In that first build the cockpit's eye was the RX-7's, on the right; `CarPartPositions` 4 and 9 put it behind FM1's wheel, on the left, since.) |

Also in the game: `fm1_import.py`'s mod, after an FM4 cars mod. The garage's current car is
55239: the game loads it in FM1's green paint and it drives. Dak's Garage lists it as "Border MR2
Turbo T-bar Widebody (FM1)", "1995 Toyota", with the donor's picture. The RX-7 stays the RX-7.

The car list's picture, in the game: the game reads the mod's `thumbnail_55239.xds`; the card
and the list's tile show FM1's green MR2 instead of the red RX-7.

Steering, on the Top Gear runway (rolling at 2-7 mph, the stick full right for 3 s, in the
chase camera and then the cockpit): with the RX-7's DriverControls the steering wheel rose out
of the car, above the roof in the chase camera and up across the windscreen in the cockpit;
with FM1's wheel as the pivot it turns in place in front of the driver and comes back to centre.
The cockpit's eye is behind the wheel, on the left; the interior is FM1's simple one (FM1 has no
cockpit view).

The wheels, in the game: the game reads the mod's `media/wheels/toy_bordermr2_95/` files and the
car loads with FM1's rim inside the RX-7's tire, a size smaller than the RX-7's rim; nothing new in
`forza.log`. Seen from both sides (the chase camera turned with the right stick, the car
standing): FM1's five-spoke MR2 rim with its five lug nuts on all four wheels, its outer face out
on both sides. The built `wheel.xds` decodes to FM1's `wheel.xpr` texel for texel. FM1's LOD 3 has no rim mesh, only the barrel and blur
disc, so FH1's LODs 3-5 have none either.

The engine, in the game: the car loads with engine 55239; the rev counter's scale ends at 8,000
(FM1's redline at the last cam level; the RX-7's ended at 10,000); the game reads the car's
`_ET.xml` (the MR2 GT's tuning); nothing new in `forza.log`.

Acceleration, on FM4's Top Gear circuit (its start is on the airfield's runway; the track mod's
two database rows, `Environments` and `Tracks`, merged into the car mod's database for the
test): full throttle from a standstill, no steering, upshifts at the same times (the save uses
manual gears), against the previous build (the RX-7's engine), same input. Speeds every second
(mph):

| | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 14 | 23 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RX-7's engine | 2 | 6 | 12 | 23 | 28 | 40 | 45 | 52 | 58 | 61 | 75 | 89 |
| FM1's engine | 2 | 5 | 13 | 25 | 30 | 42 | 46 | 53 | 58 | 61 | 74 | 91 |

Both reach 5th gear at 74-75 mph. The first FM1 run never did: a gearbox row's `NumGears` counts
the reverse gear too (the RX-7's 5-speed says 6, the S15's 6-speed 7), and the import wrote the
forward gears only, so the game offered 4. Both cars leave the runway onto grass after about 15
s, so top speed is not measured.

**Physics** (`Cars.ca2 cars\<car>\physics\*.xml`, the same value names as FH1's database), into
the car's own rows:
- `weight\l0-l3.xml` → `List_UpgradeCarBodyWeight` levels 0-3, and `Data_Car.CurbWeight` (mass
  / 100) and `WeightDistribution` (`CM_BackFront`). The MR2: 1,343 kg, 0.42.
- `suspension\l0`, `l2` and `l3` → the springs, dampers and anti-roll bars of FH1's levels 1-3
  (stock, sport, race). FM1's `l1` (street) has no FH1 level, and FH1's level 4 (the rally
  suspension) stays the donor's. FM1 keeps the anti-roll bars' default the same at every level
  and widens their range.
- `aero\body.xml` → `Data_Car.BodyAero*`, `TireAeroHack*`.
- `nonupgradedata.xml` → `Data_Car.SteerMaxAngle`.
- Still the donor's:
  - ride heights, wheelbase and tracks: the model sits on the donor's wheel positions and tire sizes;
  - the steering's rates and FH1's `FixListing` handling corrections.

**Ratings.** The car list's PI and bars are `Data_Car.PerformanceIndex`, `ClassID` and the five
`*Rating` columns, shown as they are. The game also works them out itself, live, for the car as
it is: the garage header's PI and a part list's STATS on the Custom Upgrade screens. With the
donor's rows the card showed the RX-7's B 445 while the header showed C 317. Nothing outside the
game computes these, and fits don't stand in for it: only 12 cars pair up between FM1's
`gamecars.dat` and FH1 (by their identical engine figures), and within FH1 the ratings follow
the `Sim*` figures only loosely (the speed rating from top speed: rms 1.2). So `RATINGS` in
`fm1_import.py` holds them per car, read from the game; a car without an entry keeps the
donor's, and the import's report says so. The MR2 (ReImagined v0.37): C 317, speed 3.8,
handling 8.0, acceleration 6.0, launch 6.7, braking 7.7. Its low speed is FM1's own: FM1's
figures give it a top speed of 116.6 against 148 for its RX-7 and S15 (its drag is high).

`PerformanceIndex` is the shown PI through `CarClasses`: a class's `MaxDisplayPerformanceIndex`
(B 500) is its `MaxPerformanceIndex` (B 0.6505), linear in between (the RX-7's 0.6213 shows
445). The exact value for 317 showed 318, so the game rounds up: the import aims a quarter
below. FM1's `gamecars.dat` (176-byte rows starting with the car's ordinal twice) gives all 14
`Sim*` figures FH1 keeps in `Data_Car`, in the same units (`GAMECARS_FIGURES`):

| offset | column | the RX-7: FM1 / FH1 | the F50: FM1 / FH1 |
|---|---|---|---|
| 12-28 | `SimPeakPower`, `SimPeakAngVel`, `SimPeakTorque`, `SimPeakTorqueAngVel`, `SimRedlineAngVel` | FH1's are FM1's or within 3 % (also the S15) | |
| 48 | `SimTimeTo60MPH` (s) | 5.50 / 5.67 | 3.72 / 3.80 |
| 52 | `SimTimeTo100MPH` (s) | 13.07 / 13.22 | 7.45 / 8.35 |
| 56 | `SimTimeQuarterMile` (s) | 13.95 / 14.12 | 11.53 / 11.97 |
| 60 | `SimSpeedQuarterMile` (m/s) | 46.2 / 46.3 | 57.9 / 54.8 |
| 64 | `SimTopSpeed` (m/s) | 70.4 / 80.2 | 89.0 / 88.2 |
| 68 | `SimBrakeDistance100MPH` (m) | 101.7 / 112.6 | 85.8 / 94.5 |
| 72 | `SimBrakeDistance60MPH` (m) | 36.4 / 45.6 | 31.4 / 39.4 |
| 76 | `SimLatGees60MPH` (g) | 0.90 / 0.89 | 0.98 / 1.01 |
| 80 | `SimLatGees120MPH` (g) | 0.92 / 0.93 | 1.08 / 1.15 |

They are FM1's own measurements of its stock car, which is what the import builds.

**Engine** (`Cars.ca2 engines\<engine>\physics\*.xml`, the engine named by the car's
`tooldata.xml` `<Engines><E0 File=>`, and `cars\<car>\physics\transmission`). FH1 keys every
engine part table by `EngineID` and every drivetrain part table by `DrivetrainID` (rows
`EngineID` × 1000 + level), so the car gets an engine and a drivetrain of its own: the donor's
rows cloned under the car's id (55239, or the next free one), then FM1's engine put in. FH1's
values were compared with FM1's for two cars both games have, FM1's `nissansilvia` and
`mazda_rx7` against FH1's `NIS_SilviaSpecR_00` and `MAZ_RX7_97`:
- `enginetuning\l0-l3` → `List_UpgradeEngineCamshaft` (FH1's S15 stock row is FM1's exactly:
  redline 7,250, start 4,436.707 rpm) and a `List_TorqueCurve` per level. Most FH1 engines have
  three cam levels, stock at 1 (FM1's l0, l1, l3 go in), some four, 0-3 (the RX-7's: l0-l3). The curve is
  FM1's full-throttle curve (`NumCurveValuesToRead` values up to `MaxRPM`); its last value is
  FM1's zero-throttle value there, as every FH1 curve ends (−2.63). Its `TorqueScale` is FM1's
  full-throttle scale times the parts FM1 fits as stock (the MR2's intake/exhaust is at its l1,
  × 1.031; FH1's stock part rows are 1.0). FH1's own scales are within ±6 % of FM1's with no common
  factor, so FM1's are kept as they are.
- `turbo\l0`, `l1`, `l3` → the stock turbo (FH1's level 1) and its upgrades (levels 2, 3), as FH1's
  S15 (its level 2 is close to FM1's l1, level 3 to l3): `MaxScale`, `MinScale`, `RobScale` FM1's;
  `MomentInertia` × 0.31 and `PowerMaxScale` × 0.75 (FH1's S15 over FM1's); `PowerMinScale` 10,
  as every FH1 turbo. A twin turbo goes in `List_UpgradeEngineTurboTwin`; the donor's other kinds
  of forced induction are left out.
- `supercharger\l0`, `l1`, `l3` → `List_UpgradeEngineDSC` the same way (or `CSC`):
  `ZeroRPMScale`, `RedlineRPMScale`, `RobScale` FM1's (FH1's Ford GT's 1.408 / 0.928 are FM1's
  1.40 / 0.929); `TorqueDropOff*` FH1's (the same on every forced row).
- The aspiration is what FM1's engine fits as stock: its turbo or its supercharger switched on
  (`OnOff="ON"`) at its `StockLevel` (the engine's `tooldata.xml`), else natural. A naturally
  aspirated engine gets no forced-induction rows and no intercooler, like most of FH1's. FM1 has
  one turbo part: a twin turbo, or a centrifugal supercharger, is the spec's
  (`"aspiration": "twin"` / `"csc"`), checked against FM1's.
- `base.xml` → `Data_Engine.EngineMass-kg`; its `Inertia` split between `Data_Engine` and the
  flywheel levels in the donor's proportions.
- `transmission\l0-l2` → FH1's stock gearbox level and the next two: inertia, shift time, final
  drive and the ratios (the MR2's 5-speed: 3.23, 1.91, 1.32, 0.92, 0.73, final drive 4.29).
  The row's `NumGears` counts reverse too (6 for the MR2); `Data_Car.NumGears` does not (5).
  FH1's race and rally gearboxes (the same ratios on the S15 and the RX-7) stay the donor's.
- `model.dat` (FM1's car row: 52 the drive type, 1 AWD, 2 FWD, 3 RWD; 56 the engine placement,
  1 front, 2 mid, 3 rear) → `Data_Car.DriveTypeID`, `EnginePlacementID`, `PowertrainID`;
  `Data_Drivetrain.DrivetypeID`. The next two fields are not the engine's, as first assumed: 64
  is 1 for production cars, 2 for tuner cars, 3 for race cars (the R8 and the Speed 8: 3, every
  "(Widebody)": 2), and 60 runs 1-6 with no link to the engine (the Viper SRT-10: 1, the PT
  Cruiser: 6); the MR2 came out right by chance (a tuner car with a 4 there).
- FM1 holds no layout, cylinder count, displacement, compression or boost for its engines.
  `ENGINE_SPECS` in `fm1_import.py` gives them per car (the MR2: the third-generation 3S-GTE, an
  inline 4, 1,998 cc, 8.5:1, 0.9 bar), with an FH1 car whose sound tuning suits the engine. A car
  without an entry takes them, and the sound, from the donor's stock engine: right when the donor
  is FH1's own version of the car (FM1's S2000 on FH1's `HON_S2000_03`).
- **The sound** must be an engine of the same kind. A spec without one gets FH1's car whose stock
  engine has the same layout, cylinder count and aspiration, of the same make first, then the
  nearest year, and whose sound files exist (`pick_sound`: a 1995 Toyota inline-4 turbo gets the
  Celica GT-Four ST185, the MR2 Turbo's 3S-GTE; never the donor's rotary). The car's sound files
  are three, by its MediaName: `_CMT.xml` (where the sounds sit), `_ET.xml` (the engine's banks,
  found through the engine's `Data_Engine.MediaName`) and `_DSPTuning.xml` (effects).
  Which banks the game loads can be seen with a mod holding copies of the candidate banks and
  harmonic tunings: the log names each one it reads. With the MR2 sharing the RX-7's engine the
  game read the RX-7's rotary tunings (`2rt2_rx7_*`); with its own engine only the MR2's and an
  SR20's (`4t3_mr2_*`, `4n0_mr2_*`, `4t2_sr20_*`).
- **FM1's own engine sound** (`--package`, `tools/fm1_sound.py`). FM1 keeps a car's engine
  recordings in its cab (`engines\<car>\audio\lod1\<car>.xwb`, an XACT wave bank of Xbox ADPCM
  loops) with one settings file per upgrade level and part (`l00`-`l03`, `enginea.ini`,
  `exhaust.ini`: each loop's rpm range, the rpm it was recorded at, its pitch and volume). FH1
  works the same way: the engine's `_ET.xml` names a harmonic tuning per part and level, and that
  names its bank and lists the same loop fields. The import keeps the matched FH1 sound's tuning
  (effects, burbles, the engine ambience, turbo and transmission, which FM1 shares between cars)
  and puts FM1's loops in its intake (FM1's engine) and exhaust parts, at all four levels (the
  Border MR2's exhaust changes above 4,500 rpm at levels 2-3, as in FM1); the audio rpm range is
  FM1's (`engineaudio.ini` `RPMMin`/`RPMMax`). The loops are decoded to 16-bit PCM in FMOD FSB4
  banks (FH1's own are XMA). The game opens a bank only as a file
  (`game:\Media\audio\cars\Engines\Soundbanks\LOD1\<bank>`, [#776](fm4-cars.md#engine-sound-banks-776)):
  a bank in the mod, or next to a zip that does not hold the car, is not found and the game stops
  at load. FH1's own DLC packs bring theirs as a folder their `Media/DLCZips/zipmount.xml` mounts
  there, and so does the import: `--package` writes a package of its own
  (`464D3143415253000000000000000000000000004D`, "FM1CARS", offer 64801) with the banks in
  `Media/DLCZips/FM1Banks_pri_60/`, its `zipmount.xml` and its header; the car stays in the mod.
  Without `--package` the car keeps the matched FH1 sound.
- **The bolt-on parts' gains** (`PART_TABLES`, `apply_part_gains`). FM1's engine has three
  (`intakeexhaust`, `fuelignition`, `intercooler`, levels 0-3, torque or boost multipliers, per
  car as FH1's are: its intake gains at level 3 take 78 values over 176 cars). FM1's intake and
  exhaust is one part, FH1's two (FH1's S15 at level 3: 1.085 x 1.137 = 1.234, FM1's 1.126), and
  so are fuel and ignition (1.125 x 1.103 = 1.240, FM1's 1.260): each FH1 level of the pair gets
  FM1's gain, shared between the two parts in the donor's proportion. FM1's gain at its stock
  level is in the torque curve already, so the rows hold FM1's gains relative to it, FH1's levels
  above stock spread evenly up to FM1's top (geometric in between): the MR2's intake and exhaust
  (stock at FM1's level 1) reach 1.091 at FH1's level 3, its fuel and ignition (two FH1 levels
  for FM1's three) 1.259, its intercooler 1.13. FH1's valves, pistons, displacement, oil cooling
  and flywheel have no FM1 counterpart.
- Still the donor's:
  - the upgrade prices and the bolt-on parts' masses (relative to stock): FH1 set its own prices
    (the RX-7's brakes cost 400 / 700 / 2200 in FM1, 1600 / 3300 / 7400 in FH1; its tires 3600 /
    7300 / 14500 and 2000 / 5000 / 10000), so FM1 cars' upgrades cost what FH1's do. The car's own
    price is FM1's North American dealer price (`model.dat` offset 20 → `Data_Car.BaseCost`; 24
    and 28 are Europe's and Asia's): the F50 267000 (FH1's 400000), the Ford GT 133000 (150000),
    the RX-7 30000 (18000). `tooldata.xml`'s `<Misc Price=>` (the F50 78114) is not the dealer's;
  - the clutch, differentials and driveline: FH1 retuned FM1's (the S15's clutch 830 Nm and
    0.15 s in, FM1's 1000 Nm and 0.09 s; its diffs other torques), and they are generic (the
    S15's and the RX-7's differentials are the same rows);
  - the brakes: FM1's levels are the generic `GameFrictionScaleBraking` 1.00, 1.01, 1.03, 1.05
    (and bias 0.5 / 0.5, handbrake 1.5) of every car checked in both games (RX-7, S15, S2000,
    Corrado; the F50 and the Ford GT start at 1.03, where FH1 has fewer levels), as FH1's;
  - the aero kits (`List_AeroPhysics` has FM1's `SpoilerAero` fields, but FH1 retuned them: its
    S15 race wing gives 34 / 91 kg of downforce, FM1's 25 / 75, and on other levels).

### Engine sound (2026-11-16)

The Border MR2 imported with `--package` (Windows, the v0.3.7 runtime, a copied save with the MR2
in every garage slot, FM1's Tsukuba imported, `FORZA_AUDIOLOG` every 0.5 s; 12 s of full throttle
from the start line, then 7 s at the limiter with the brake held). The game opened both banks and
the car drove (41 mph at 5 s of the lap). The car's channels of the mix the game submits (center
and surrounds; the radio is in front):

| | full throttle | limiter |
|---|---|---|
| FH1's matched sound (`TOY_MR2GT_95`) | -39 to -40 dB | -49 to -53 dB |
| FM1's own loops | -37 to -39 dB | -50 to -52 dB |
| the same banks with every sample zeroed | -43 to -44 dB | -56 to -58 dB |
| engine volume 0 (`car_engine_volume`) | -50 dB | -59 dB |

So FM1's loops play, about 6 dB above what is left without them (FH1's ambience, transmission and
turbo layers), and the car is as loud as with FH1's sound. The 21-car batch with `--package` gives
every car its own banks (42, 67 MB): the Lancia Delta Integrale, whose FH1 intake is FH1's
`NA.xml` placeholder (the import starts from `FALLBACK`'s tuning then), loads and drives the same
way, -29 dB at full throttle and -49 to -51 dB at the limiter, against -33 and -47 to -50 dB on
FH1's sound for it. The F50 and the S7 have only FM1's far sound (`lod2`) on this disc copy and
get that. Before that, without the package (the
bank next to a zip of the FM4 cars package, then also with the car in that zip), the game looked
for the bank in `Game:\` only and stopped at load. At the festival hub the crowd and the radio
cover the engine: the same check there showed no difference even with the engine at 0 or 200 %.

### Driving figures and part gains (2026-11-16)

The Border MR2 imported with FM1's 14 `Sim*` figures (0-60 mph 6.45 s, top speed 64.6 m/s) and
its part gains (intake and exhaust up to 1.091, fuel and ignition 1.259, intercooler 1.13) loads
and drives on FM1's Tsukuba as before (41 mph 4 s into the lap; the same engine level). The
upgrade screens with these parts fitted were not looked at.

## Not done yet

- The ratings are read from the game by hand (`RATINGS`).
- FM1's geometry. Its `maxdata.xml` has FH1's layout, with its height 0 at the ground, about
  12 cm below the model's.
- The wheels' upgrade rims (`List_UpgradeRimSize*`, the donor's), the exhaust emitters (the donor's),
  the pop-up lamps' movement. The picture could come from the game itself: it draws a garage
  picture when a part is upgraded.
- The livery masks (`LiveryMasks/*.tga`, the donor's, made for its UVs).
- FM1's visual upgrade parts (the lettered and race body parts of the model).
- FM1's own clutch, differential and aero kit figures (FH1 retuned them; see the engine section).
- FM1's makes FH1 lacks, the shared car id table (#924), the launcher.
- The engine sound: by ear (the check above is levels only), FM1's own turbo, supercharger,
  transmission and backfire sounds (FM1 shares them between cars; the FH1 ones are kept), the
  far-away LOD2 banks.

## Credits

The geometry builder is a port of Forza Imported DLC Maker's (warshack4874). The FH1 model
layout follows rustybusgaming's fh1_rewrite and this repository's `carbin_dump.py`.
