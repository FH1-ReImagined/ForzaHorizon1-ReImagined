# Forza Motorsport 4 cars in FH1

FH1 is built on Forza Motorsport 4's engine, and FM4 has about 400 cars that FH1 lacks. The
launcher's **Mods → Forza Motorsport 4 cars → Import FM4 cars…** converts them from the
player's own FM4 discs into a mod (#648). Nothing is downloaded: the cars are made on the
player's PC from their discs and stay there.

- [For players](#for-players)
  - [What the import accepts](#what-the-import-accepts)
  - [FM4 DLC](#fm4-dlc)
- [What a converted car is made of](#what-a-converted-car-is-made-of)
  - [The id table](#the-id-table-924)
- [physicsdefinition.bin](#physicsdefinitionbin)
- [FH1's handling retune](#fh1s-handling-retune)
- [Checked in the game](#checked-in-the-game)
- [Not done yet](#not-done-yet)
- [Credits](#credits)

## For players

1. **Mods → Forza Motorsport 4 cars → Import FM4 cars…**
2. Choose FM4's **Disc 1** (the play disc): a disc image (`.iso`), a Games on Demand package,
   a folder with its files or an archive of one of them ([what is accepted](#what-the-import-accepts)).
3. Choose **Disc 2** (the content install disc) for its three car packs, or **Skip**.
4. **Add FM4 DLC**: choose the car packs you bought for FM4 ([below](#fm4-dlc)), or **Skip**.
5. The launcher checks that Disc 1 is FM4 (title `4D530910`), unpacks what it needs into a
   temporary folder next to the mods folder, converts every car, and adds the **FM4 cars** mod
   and a package of the cars' files (below). With the discs' files already unpacked it takes
   about 2 seconds a car; it needs about 5 GB for the result and as much again while it runs
   from disc images (and room for what an archive holds, unpacked, while it runs from one).
6. Start the game: the cars are in the **Autoshow** with the other cars of their makes (and
   in **Change Car** once bought). Four FM4 cars FH1 has only as traffic cars (the 2004 Beetle
   and PT Cruiser GT, the 2010 Taurus SHO, the 2011 Prius) come in as cars of their own; FH1's
   traffic cars stay as they are ([how](#fh1s-traffic-cars-841)). Every car takes its id from one
   table, the same for every player, so a car has the same id on every game it is imported into
   ([the id table](#the-id-table-924)).

### What the import accepts

| Form | Disc 1 (the play disc) | Disc 2 (the content install disc) |
|---|---|---|
| Disc image | a full (Redump) image, the game partition at the XGD3 `0x2080000` (or `0x2090000`), XGD2 `0xFD90000` or XGD1 `0x18300000` offset, or a rebuilt or trimmed image (extract-xiso, XDVDFS tools) with it at 0; any name (`.iso`, `.xiso`, `.img`, `.bin`, none); its `.dvd` file | the same |
| Split image | any of its parts, named `fm4.iso.1`, `fm4.iso.001`, `fm4.001`, `fm4.part1.iso`, `fm4.iso.part1` or `fm4.1.iso`: the other parts are found next to it (a missing part is named) | the same |
| Games on Demand | the package file, its `.data` folder, a `DataNNNN` file in it, the folder that holds them (`00007000`, `4D530910`, `Content` or the drive above it), from the console or a converter (iso2god and the like); a hard drive install (`00004000`) the same way | the Disc 2 package the same way, or the folder holding both discs' packages (each disc takes its own) |
| Car packs | refused: they are Disc 2's | the packages (`4d53091000000001`…, any name), in a folder or the console's `4D530910/00000002`, or one of them; unpacked ones (each with `Media/DLCZips`) |
| Folder | extract-xiso or Xenia output with `default.xex` at its root (any letter case), that `default.xex` itself, or a folder above it: the parent of the disc folders, Xenia's `content` folder or its own folder | the same, with its `content` folder |
| Archive | a `.zip` or `.tar` (`.tar.gz`, `.tgz`...) holding any of the above, read by the launcher itself; a `.7z` or `.rar` (or a split one: `.7z.001`, `.part1.rar`, `.z01`), unpacked with 7-Zip, unrar, unar or bsdtar when one is installed, else the import says to extract it first | the same: one archive with both discs is unpacked once |

A folder is searched 8 levels down, the shallowest find first; each disc takes the first thing
that is that disc. Disc 1 is checked by its `default.xex` title id before anything is copied, and
must have `Media/db/gamedb.slt` (Disc 2 has a `default.xex` of FM4 too). Car packs are found by
their package header (FM4's title, content type 2), not by their names. A title update
(`000B0000`, `TU_...`), DLC, saves or another game's files next to the discs are left alone.

FM4 has one title id, `4D530910`, on its discs and its Games on Demand copy; the regions and
editions (the Limited Collector's Edition, the later reprints) share the game data and are told
apart by the discs' media id, which the import does not check. The demo's package is refused.

Anything else is named instead of converted: another game's disc or Games on Demand package
(Forza Horizon `4D5309C9`, Forza Horizon 2 `4D530AA4`, Forza Motorsport 3 `4D53084D`, or the
title id), a content package (DLC, title update, save and so on, with its title), FM4's demo or
a title update chosen as a disc, a lone XEX, Disc 2 chosen as Disc 1 and the other way round, a
Games on Demand package without its `.data` folder (or the other way round) or with parts
missing, a truncated image, a damaged or unsafe archive (links, devices or paths leading out of
its folder), an archive bigger than the free space,
and an archive with no program here to unpack it. A file named like `4D5309C900003061` is an
Xbox 360 content package of that title (here Forza Horizon's), and renaming one to `.iso` does
not make it a disc image (#681).

### FM4 DLC

FM4's car packs from Xbox Live (the monthly packs, the Porsche Expansion, the Limited
Collector's Edition and bonus packs, the SRT Viper and Hyundai packs and the like) are content
packages of FM4's title (`4D530910`, content type `00000002`), STFS `LIVE` or `PIRS` files with
a hash name. The **Add FM4 DLC** step takes them in any of these forms: the package files (pick
several at once), the folder holding them (the console's `Content/0000000000000000/4D530910/00000002`,
a folder above it, or any folder of packages), unpacked packages (each with `Media/DLCZips`), or a
`.zip`, `.7z` or `.rar` of any of these. A package is found by its header, not its name.

A car pack holds its cars' files in `Media/DLCZips/<offer>_pri_65.zip` (`media/cars`, `media/wheels`,
the cars' pictures and sound tuning; one pack names it in hex, `5c00_pri_65.zip`), their database
rows in that zip's `media/db/patch/<n>_merge.slt`, and their names in `StringTables_pri_<n>.zip`.
The import merges each pack's rows over a copy of Disc 1's database (a table keyed by its first
column takes the pack's row, as the game reads the patch; the others get the rows they lack), then
converts the DLC's cars like the discs', with the same fixes (engine level, makes, logos), into
the same **FM4 cars** mod and package. The Mods page says how many came from the DLC.

Two things the disc cars never met: FH1's database has the stock wheel rows (`List_Wheels`) of
every disc car but not of 33 DLC cars (the Porsche Expansion's and some later packs'), and a car
without its wheel row did not load (the 959 in a grey void, the 997 GT3 RS standing still with its
textures wrong), so the import adds those rows. And FH1 gives some of the DLC's ids to other
cars and parts: car `1541`, the Porsche Cayman R's, is FH1's Explorer (a traffic car), and engine
`999`, the Porsche 911 GT2 RS engine, is FH1's Camry engine. Such a car or part comes in under an id
of its own, 55000 + its id ([the id table](#the-id-table-924)): the Cayman R is 56541, and the
engine swap to `999` that four Porsches have names 55999. Before #924 those cars were left out
and the swap too, and so was the Austin-Healey Sprite, whose engine `1034` is a Fiat 500's in FH1
(it is now 56034).

Left out, each named in the import's message: a pack with no cars (the Launch Track Pack: tracks,
a soccer field and events), Disc 2's three car packs and its Autovista pack when they are among the
DLC (the same content type, without a database patch: used as Disc 2 when Disc 2 was skipped, else
not read twice), small packages (offers, tokens), title updates, other games' packages, the DLC
cars FH1 or its own DLC has already, and a car whose rows name something no file given has (its
engine, body, drivetrain, make or wheel, as for a car that relies on another pack): such a car is
skipped with that reason rather than added broken.

Sixteen of FM4's packs, checked with the converter's list: 139 cars FH1 lacks are converted, on
top of the discs' 385 (381, and the four [traffic cars'](#fh1s-traffic-cars-841) copies); 25 of
the 139 take an id other than FM4's ([the id table](#the-id-table-924)). The rest are FH1's cars or
its DLC's.

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

    tools/fm4_cars.py list    --fm4 FM4_DISC1 --fm4-content FM4_PACKS [--fm4-dlc FM4_DLC] --fh1 GAME --update TU4
    tools/fm4_cars.py convert --fm4 FM4_DISC1 --fm4-content FM4_PACKS [--fm4-dlc FM4_DLC] --fh1 GAME --update TU4 \
                              [--dlc USER_DATA/0000000000000000/4D5309C9/00000002] \
                              --out MODS/FM4Cars [--package USER_DATA/0000000000000000/4D5309C9/00000002/464D3443415253000000000000000000000000004D] \
                              (--cars BUG_Veyron_09,1106 | --all)

Without `--package` the cars' files go into the mod too (for a few cars: the mod overlay's
4 GB limit); a car that needs a sound bank FH1 lacks is then left out, as the game reads banks
only from a package ([why](#engine-sound-banks-776)). `tools/fm4_cars.py check` lists what an
installed import lacks ([the same section](#engine-sound-banks-776)).

`FM4_DISC1` is the unpacked disc 1, `FM4_PACKS` a folder with the unpacked Disc 2 packages
(`tools/xcontent_extract.py`), `FM4_DLC` a folder with unpacked FM4 DLC packs, `GAME` FH1's game
folder and `TU4` its title update folder. `list` names FM4's id and name of each car, and after it
the id and name the import gives it when they are not FM4's (`1541 POR_CaymanR_12 ok (as 56541
POR_CaymanR_12)`); `--cars` takes FM4's.

## What a converted car is made of

Which cars: every FM4 car of the [id table](#the-id-table-924), under the table's id, that has a
model in the given files and that no installed DLC pack has. The table has every `Data_Car` row of
FM4's database whose `MediaName` neither FH1 nor any of its official DLC packs has (a DLC installed
later must not meet a second copy; `FH1_DLC_CARS` names them too), and the FM4 cars FH1 has only
as traffic cars, as cars of their own ([below](#fh1s-traffic-cars-841)). The two games mostly share
one car id registry, and the table keeps FM4's id where FH1 and its DLC leave it free. FM4 Disc 1
and the three Disc 2 car packs give 381 such cars under FM4's ids (60 of them of the 25 makes FH1 lacks, such as Acura, Saab
and Vauxhall: their `List_CarMake` row and names come along). FH1 has the logos of all of them
but Renault's, which come from FM4's `UI/Textures.zip` (`UI4/Logos/<size>/L_MAN_Renault_Logo.xds`,
the same format; without them its Autoshow tile was black, #710). An FM4 make FH1 has under
another id, by its English name, is FH1's: FM4's Eagle (87, the Talon) joins FH1's Eagle (102,
the Speedster) instead of a second Eagle in the Autoshow (#710); Eagle is the only one.

The DLC packs bring makes of their own: Porsche (39), smart (95), MG (98), Triumph (99), Viper
(104) and Lincoln (105). Their logos (`L_MAN_POR`, `_SMA`, `_MG`, `_TRI`, `_VIP`, `_LIN`) are
only in the packs' zips (`media/UI/Textures/UI4/Logos/<size>/...`), so the logos come from the
packs as well as from Disc 1, a later pack's over an earlier one's (FH1's own `L_MAN_Porsche_Logo`
is not the one FM4's Porsche row names; #772). Three FM4 make ids are FH1's for another make:
80 (FM4's "GMC Truck", FH1's GMC: the same make, kept), 102 (FM4's Hennessey, FH1's Eagle) and
105 (FM4's Lincoln, FH1's Viper; the 1962 Continental showed as a "Viper Continental", #772). A
make whose id FH1 uses for another one joins FH1's make of its name when there is one (FM4's
Hennessey, 102, is FH1's 103); otherwise it and its cars take a new id after the highest either
game uses (the Lincoln: 108), and its name goes into FH1's string table under a key of its own
(both games key a make's name by its id, so the Lincoln's and FH1's Viper's had the same key and
`IDS_` name). FH1's DLC packs add make ids 62, 63, 76, 85 and 100, none above its own 107.
Two cars have no model in those files and are left out. The Autovista pack (Disc 2, package 4)
has only showroom models and is not used. All 381 convert (15 minutes on this PC
from unpacked files; 5.6 GB of package, 10 MB of mod).

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
| engine sound | FH1, else FM4 | FH1 has the sound banks, harmonic tuning, `CarModelTuning` and `EngineTuning` of all 381 disc cars ([checked](#checked-in-the-game)); what it lacks is taken from FM4's archives and DLC packs, its sound banks too ([below](#engine-sound-banks-776)). The `EngineTuning` of an engine no FH1 car uses goes into the mod with its `GlobalCarVolume` curve times 1.6, at most 1.0 ([level](#engine-level-712)): FH1's copy, else FM4's, and FM4's where FH1's is a cut traffic car's ([below](#traffic-car-tunings-840)) |

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
| damage morph entry | a unit direction and the dent's size | the dent's offset ([below](#the-damage-morphs-747)) |

For the Alfa Romeo 8C, which both games ship, positions, UVs and the tangent frame are byte
for byte the same in 23,099 of 23,103 compared vertices, and FH1's SH0 is
`round(s16 / 32767 × 127)` of FM4's wherever the lighting was not re-baked. The game keeps
loaded buffers by key, so every converted buffer gets a key of its own (a CRC-32 of the car,
file and buffer number). The light list is made from the model ([below](#the-light-list)).

### The damage morphs (#747)

Both games pack a section's damage morphs the same way: one `u32` a vertex, `FMT_10_11_11`
(x bits 0-10, y 11-21, z 22-31, signed). FH1 reads the entry as the vertex's dent offset
([the morph formula](carbin-format.md#the-morph-formula)). FM4's entries mean something
else: x and z are a unit direction, its y is left out (`sqrt(1 - x² - z²)`, never
negative), and the y field holds the dent's signed size. Copied as they were, almost every
FM4 entry read as an offset of about 1.0, so any damage moved the vertex by up to 2 m: with
**Cosmetic Damage** on (Options → Difficulty), an imported car that hit something drew its
body torn into long spikes (the 1979 Camaro Z28 and the Versa in #745, and every other FM4
car). The wheels, seats and interior have no morphs and stayed whole. `fm4_carbin.py` now
writes size × direction, which is FH1's format:

| Check | Copied as they were | Converted |
|---|---|---|
| the 77 cars of FM4's disc 1 that FH1 ships too (888,207 entries of vertices whose positions match in both): within 2 steps of FH1's own entry | 1.2 % | 94.8 % |
| the 8C, per section: median cosine to FH1's offsets, median length ratio | −0.99 to 1.00, 0.006 to 0.026 | 0.94 to 1.00, 0.83 to 1.06 |
| median entry length of a section, all 501 FM4 cars of disc 1 and the disc 2 packs (FH1's cars: 0.006) | 0.99 | 0.012 |

The rest are entries FH1 re-baked (the 8C's front bumper, glass and body). Wheel models'
16-byte morphs are the same in both games and are copied.

### The light list

FH1's car models (`<car>.carbin` and its `stripped_` copy; not `_lod0` or `_cockpit`) carry a
list of the car's lamp sprites, the glows other cars show at night from a distance: 4-24
records of 32 bytes in FH1's cars. FM4 has no such list (it has no night) and no other file
with it; its lamps are only meshes with the `head_light`, `tail_light` and `reverse_light`
techniques. The record, from all of FH1's cars:

| Bytes | Field |
|---|---|
| 0-11 | position, 3 × `f32`: the model's x and y, and its z negated (the list's z points forward) |
| 12-17 | direction, 3 × `s16` normalized: (0, 0, 32767) forward, (0, 0, −32767) backward |
| 18 | not decoded (0-9; FH1's most common value is used) |
| 19 | sprite kind: 1, 2 (bluish, on cars from 2010), 3 (warm, older cars) headlights; 4 the halo of a headlight (front) or the tail light (rear); 0 the others |
| 20 | side: 1 the car's left (x < 0), 2 its right (2,398 of FH1's 2,444 records) |
| 21 | when it shines: 1 lights on, 2 braking, 4 braking (a second sprite), 8 indicators, 16 reversing |
| 22-24 | colour, RGB |
| 25 | size |
| 26 | 0-7 by kind: 1, 2 or 4 headlights, 5 brake, 6 indicator and reverse, 7 halos (probably which of `media/carlights/headlight_glow_1-7.xds`) |
| 27-29 | an aim offset, 3 × `s8` (0 in most records) |
| 30, 31 | 30 and 50 in most records (70 for a headlight's halo), 255 and 255 in some; not decoded |

The converter finds each lamp, on each side: the lamp technique's meshes in the lamp's own
sections (`headlightL`, `taillightR`, ...), else its lens (`glassLHL`, `glassRTL`, ...), else
the technique's meshes in the stock parts (not the race or other optional parts such as
`bumperFrace`, `bumperFb`, `hoodc`, nor a pop-up lamp's lowered position). Vertices 4 cm or
more apart are separate lamps (up to two a side). Each lamp gets FH1's most common records of its kind,
3 cm inside the lens: a headlight and its halo 2 cm ahead of it (kind 3 before 2000, 1 from
2000, 2 from 2010, as FH1 gives its own cars), a tail light, a brake light and the second
brake sprite, a reverse light. A lamp in the middle (a third brake light) is left out, as on
most of FH1's cars. Indicators are left out: only 18 of FH1's cars have indicator records, and
every one is a traffic car (`IsSelectable` 0).

Checked on the 77 cars both games ship (FM4's model through the converter against FH1's own
list): an FH1 headlight sprite is a median 4 cm (sideways), 1 cm (height) and 6 cm (depth)
from the nearest generated one of its side, a brake sprite 5, 1 and 3 cm.
Of the 381 cars the import adds, 374 get headlight sprites and 375 brake sprites (4-24 records
a car); the others have no lamp meshes the converter knows (the two NASCAR cars' lamps are
decals, for example) and get fewer or none.

### The database

The rows come from FM4's database: the car's `Data_Car` row and the tables keyed by its id
(`CameraOverrides`, `CarExceptions`, `CarPartPositions`, `Combo_Colors`, `Combo_Engines`, the
`List_Upgrade*` tables by `Ordinal`, the suspension and anti-roll physics), then the engine,
body, drivetrain and motor rows those name (`Data_Engine`, `Data_CarBody`, `Data_Drivetrain`,
`Data_Motor` and their `List_Upgrade<Part>*` tables), the torque curves and aero rows any of
them names, and the make (`List_CarMake`) when FH1 has none. Rows whose key FH1 already has
are left out (FH1's database still holds many FM4 rows, the same ones, and 16 that FH1 retuned:
a camera override, a car exception, 13 piston and one gearbox upgrade; FH1's are kept); the 9
columns FH1 added
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

### The id table (#924)

Online, and in saves, an id has to mean one car on every player's game: two players whose imports
gave one id to two different cars break each other's sessions. So the import does not work a car's
id out from the player's files. It takes it from `tools/car_ids.json`, one table of every car an
import can bring, the same for everyone. `tools/car_ids.py build` makes it from the games
themselves: FH1 with its title update and all of its official DLC packs, Forza Horizon 2, Fast &
Furious, and FM4 with its DLC. The table is committed, and a new build only adds to it: an id in
it never changes.

| Rule | Example |
|---|---|
| A car is its `MediaName`: a car several games have has one id | FM4's and FH2's SLK55 AMG are one car |
| FH1's cars and those of its official DLC (every pack, installed or not) keep their ids, and no other game's version of them is imported | FH1's DLC has the Hennessey Venom GT (1564), so FM4's (1532) is not imported |
| ...but for FH1's 18 traffic cars: another game's full car is a car of its own, under 55000 + the traffic car's id and a 5 after the make in its `MediaName` ([below](#fh1s-traffic-cars-841)) | `VW_5_Beetle_04`, 55282 |
| Otherwise the first game in the order FH2, Fast & Furious, FM4 that has the car gives its id | FM4's SLK55 AMG (1525) has FH2's id, 51525, as in UDLC |
| A car keeps its game's id unless a car before it in that order has it; it then takes 55000 + it | FM4's Cayman R (1541) is 56541: FH1's 1541 is the Explorer, a traffic car |
| A car id stays under 65536: with car ids above that, no imported car loaded. The build stops when one would not | |

FM4's parts go by the same rule (`car_ids.fm4_parts`). An engine, drivetrain or stock wheel takes
55000 + its id when FH1 or its DLC has that id for another part (an engine or wheel of another
`MediaName`, a drivetrain that only other cars use), or has it only on its traffic cars, which FH1
cut down. FM4's engine 999, the Porsche 911 GT2 RS's, is 55999, as FH1's 999 is the Camry's. An
engine whose `MediaName` FH1 has gets `_FM4` after it (`VW_Beetle_04_FM4`), as the game finds an
engine's `EngineTuning` by that name and the disc's file would win. A part FH1 keeps with no car of
its own using it stays shared, as before, so an imported car's parts keep their ids. The table
moves 26 engines, 30 drivetrains and 35 wheels of FM4's. The import moves the part's rows with it
(`Database._move_parts`: those keyed by the part's id, or by id × 1000 + n, and the torque curves
in that range), and a car the table renumbers takes its rows along (`Database._renumber_cars`:
the rows keyed by its id, or by id × 1000 + n, and the references between them).

The table also lists, under `renumbered`, every id an older import gave a car or part that the
table gives another, for the launcher to move a save's cars to their new ids (not done yet: it is
to ask first and keep a backup). They are those of ReImagined's import before the table (the two
2013 SRT Viper race cars, 1606 and 1637 when FH1's DLC was not installed, where FH1's DLC has the
Shelby GT500 and the Escalade, now 56606 and 56637, and three parts of cars that kept their ids:
drivetrains 946 and 947, and engine 36) and the 158 cars of Forza Imported DLC Maker that move.

    tools/car_ids.py build --fh1 GAME --update TU4 --dlc FH1_DLC --fh2 FH2 --ff FH2FF \
                           --fm4 FM4_DISC1 [--fm4-content FM4_PACKS] [--fm4-dlc FM4_DLC] \
                           [--old-ids "Forza Imported DLC Maker=fm4_car_ids.json"]
    tools/car_ids.py check

`build` needs all of those games, and only who maintains the table runs it; `FH1_DLC` is a folder
holding all of FH1's official DLC packs (named by their 42-digit hash; other packs there are left
out). `check` finds a table's own errors: an id or `MediaName` used twice, a car id out of range,
a part id used twice.

### FH1's traffic cars (#841)

Five FM4 cars are in FH1 only as traffic cars: the 2004 Beetle and PT Cruiser GT, the 2010
Taurus SHO, the 2011 Prius and Leaf. FH1 has their id and `MediaName` with `IsSelectable` 0, and
cut-down models (`media/cars/VW_Beetle_04.zip` holds only the `_lod0` and `_cockpit` models). An
import before this fix took them for FH1's own cars and left them out without saying so. Now
FH1's traffic cars stay as they are, so traffic loads nothing bigger, and FM4's car comes in
beside each one as a car of its own, as the [id table](#the-id-table-924) numbers and names it:

| | FH1's traffic car | FM4's car in the import |
|---|---|---|
| id | 282 | 55282: 55000 + the id (`car_ids.new_id`), a 55 in front of a three-digit id (the 1241 Taurus SHO is 56241) |
| `MediaName`, files | `VW_Beetle_04` | `VW_5_Beetle_04`: its models, gauge and wheel files under that name (`twin_name`, `twin_file`). Files under the traffic car's name would lose to the disc's |
| rows keyed by the car | 282000 + n | 55282000 + n, with the references between them kept. The engine combos get new keys |
| stock wheel | 30 `VW_Beetle_04`, cut down too | its own: 55030 `VW_5_Beetle_04` |
| engine, drivetrain | FH1's, cut down for traffic | its own copy of FM4's: 55000 + the id (55036, 55035), engine `VW_Beetle_04_FM4` (below) |
| picture, `CarModelTuning` | FH1's | FM4's, as `Thumbnail_55282.xds` and `VW_5_Beetle_04_CMT.xml` |

Every new id is 55000 + the old one. That stays clear of the ids FH2 gave the FM4 cars it shares,
which have a 5 in front (FM4's 1525, the SLK55 AMG, is FH2's 51525), and which UDLC carries into
FH1. A car id has to stay under 65536. With the Taurus SHO and the Prius at 551241 and 551340 (a
55 in front of a four-digit id), no imported car loaded at all: the game spawned its default
car, the Corrado. With 55282 and 56340, they load. Engine and drivetrain ids above 32767 work
(the Prius' 55800 and 55744 below, and UDLC's engines up to 51038), and so do wheel ids above
65535 (the Prius' 65895).

FH1 cut down these cars' parts too. FH1's `EngineTuning` of each of their four engines is a
traffic one ([`traffic_tuning`](#traffic-car-tunings-840)); FM4's is not. FH1's Prius gearbox is
not a CVT (`CVTShiftTime` −1, FM4's 1.0), and its torque curves are not FM4's. FH1's Leaf has six
gears (five forward); FM4's has one. So a part that FH1 has only on its traffic cars becomes the
twin's own ([the id table's parts](#the-id-table-924)). The engine or drivetrain gets FM4's rows
under 55000 + its id: engine 800 becomes 55800, its rows 800000 + n become 55800000 + n, and so do
the torque curves in that range (the body's rows are the car's, under its id). The game finds an engine's `EngineTuning` by the engine's
`MediaName`, and the disc's file of the old name, the traffic one, would win over the import's.
So the copy is named `<name>_FM4`, as Forza Imported DLC Maker names such an engine (its 2006
Focus ST, Spark and xD were silent with FH1's traffic tuning). Its tuning is FM4's, at FH1's
[level](#engine-level-712). A part that a car you can drive uses (a swap to the Golf R's engine,
for one) stays shared, as for any imported car.

The Leaf is left out for now. The import names it with the cars that could not be converted:
"an electric car without an engine row, which FH1 needs". FM4's Leaf has only an electric motor (`Data_Motor`), and Forza Imported DLC
Maker found that FH1 builds the `EngineTuning` file name from the car's stock engine: a car without
one asked for "Attempt to access invalid field or record_ET.xml" and loaded to a grey world.
FH1's own Leaf is dressed as a petrol car (an engine row, five gears). That tool gives FM4's Leaf
an engine row of its own; doing the same here is not done yet.

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

Linux, the Title Update 4 build of main `adb878f`, copied saves (the TU4 golden save), offline,
audio muted.

Three cars converted by hand first, from the owner's FM4 Disc 1: the 2009 Bugatti Veyron 16.4
(a road car), the 2008 #009 Aston Martin Racing DBR9 (a race car) and the 1957 Ford
Thunderbird (an old car):

| What | Result |
|---|---|
| Autoshow | the game counts them as installed (`IsCarInstalled` 1, `FORZA_DLC_LOG=1`) and lists them with their makes |
| Garage | added with the launcher's garage code (the unicorn-car action's): listed in Change Car with their pictures and stats ("Veyron 16.4, 2009 Bugatti, R2 818"; "Thunderbird, 1957 Ford, D 258"; "#009 Aston Martin Racing DBR9, 2008 Aston Martin, R1 910") |
| Drive | all three driven in free roam: chase, hood, bumper and cockpit views; the Veyron at 127 km/h and into a guard rail, which stopped it; the DBR9 sliding on its slicks on the outpost's dirt |
| Interior | each cockpit has its dials or digital display, steering wheel, the driver's hands and, in the DBR9, its roll cage |
| Upgrades | the Veyron's Custom Upgrade: Race Intake bought and installed (736 → 747 kW, R3 770 → 775), kept after a restart |
| Paint | the Veyron's body painted green and saved; its garage picture was redrawn green |
| Save | quit to the title screen, restarted: the Veyron is the current car, green, with the intake. Without the import the same save loads into free roam (the car is FH1's placeholder for missing content); imported again, everything is back |
| Night | the Veyron's headlights light the road |

Lights and engine sound, the Title Update 4 build of this branch, a copied save, with the
2007 Murciélago LP640 (FM4 only) against FH1's own Aventador LP700-4:

| What | Result |
|---|---|
| Light list | the LP640 gets 22 records (two headlights a side with their halos, two tail lamps a side with tail, brake and second brake sprites, a reverse light a side), the Veyron 12 |
| Close up, at night (22:00 held) | headlights light the road; tail lamps lit, brighter with the brake, reverse lamps when reversing: the same with an empty list (the lamp meshes do that, not the list) |
| From a distance | with every free roam rival (`FreeRoamDrivers`) made an LP640 in a test database: with the list the oncoming rivals' headlights glow down the road, as FH1's cars' do; with an empty list an oncoming LP640 shows none |
| Engine bank | the game opened `12N2_LP640_Exh.fsb`, `12N2_LP640_Int.fsb` and `6N0_Boxster_EngAmb.fsb` (watched with inotify on a private copy of the game files), the three its `EngineTuning` names; the Aventador uses `12N2_LP640_Int`, `12N2_Murc_Exh` and `6N3_GT3R_EngAmb` |
| Engine sound | the game's mix as it submits it (a local dump of the samples `FORZA_AUDIOLOG` measures, no output device), 3 s idle then 10 s full throttle from the spawn: LP640 −15.6 dBFS idle, −13.5 full throttle; Aventador −19.0 and −12.4. No dropped frames (187.7 a second for 55 s), no silence (quietest 50 ms at full throttle −16.7 dBFS; Aventador −26.7). The strongest partial near the V12's firing frequency (rpm / 60 × 6) follows the rpm from telemetry: r = 0.85 over 1,550-8,700 rpm (Aventador 0.88 over 2,000-8,500) |

Every FM4 disc car the import adds has its `CarModelTuning` and `EngineTuning` in FH1, and every
sound bank those name is one of FH1's (`NA.fsb`, named for five of the cars, is FH1's own
placeholder of its `NA_*_HT.xml` files: no file has it, and 24 of FH1's own cars name it). Three
FM4 DLC cars name banks FH1 lacks ([below](#engine-sound-banks-776)).

### Engine sound banks (#776)

An engine's `EngineTuning` names a harmonic tuning file per part (`Exhaust`, `EngineIntake`,
`EngineAmbient`), and that file names its sound bank (`<Soundbank name="5T1_A200b_EXH.fsb">`).
FM4's DLC packs bring the banks of their new engines as loose files, in a folder named after the
pack's car zip (`Media/DLCZips/29_pri_65/Media/audio/cars/Engines/Soundbanks/LOD1/`); FH1's DLC
packs do the same. Of the cars the import adds, three name banks FH1 lacks: the 1986 Audi Sport
quattro S1 (`5T1_A200b_EXH.fsb`, through `5T1_A200_1478_Exh_HT.xml`), the 1985 Mazda RX-7
(`2RN3_FBRX7_EngAmb`, `_Exh`, `_Int`) and the 1991 #55 Mazda 787B (`4R3_787B_EngAmb`, `_Exh`,
`_Int`), seven banks, 1.5 MB. An import before this fix copied their harmonic tuning but not the
banks, and the game stopped with its disc read error ("XamShowDirtyDiscErrorUI" in `forza.log`,
after `NtCreateFile FAILED ... Soundbanks\LOD1\5T1_A200b_EXH.fsb`) when it loaded the car: right
after buying it in the Autoshow, and at every start once it was the save's current car (the save
holds its garage row and the current car, nothing broken in itself).

The game reads a bank only as a file, never from a zip: with the bank in the mod (the title
update's archive) it opened `update:\media\Audio\...\5T1_A200b_EXH.fsb` as a loose file, which
does not exist, and crashed. A DLC package's zip gets a folder of loose files where a folder of
the zip's name is next to it (the game looks in `DLCRootN:\media\dlczips\<zip>\` for the
package's `media\effects\` and the like only then). So the import copies each bank FH1's disc
lacks from FM4's files into `Media/DLCZips/fm4cars<n>_pri_60/Media/audio/cars/Engines/Soundbanks/`
next to the zip that holds the car. A car whose tuning or bank no given file has is left out
("its engine sound bank ... is in none of the files given"), checked before any of its files is
written, and so is one that needs a bank without `--package`.

An import made before this fix is repaired once, when the launcher next starts the game
(`fm4cars.repair_sound`; the import's `.reimagined-fm4-cars.json` has no `"banks"`): each harmonic
tuning file of the import whose bank no file has gets FH1's most similar one in the mod, which
the game reads before the package's copy (same part, same kind of engine and cylinder count, the
longest common name: the S1 gets `5T1_A200_Exh_HT.xml`, the RX-7 and the 787B the RX-8's). The
cars then drive with that engine's sound and a save stuck on one starts again; the launcher says
so, and importing FM4 cars again gives them their own banks.

`tools/fm4_cars.py check --fh1 GAME --update TU4 --out MOD --package PACKAGE` lists what an
installed import lacks that the game reads for each of its cars: the model, the
`CarModelTuning`, the engine rows and `EngineTuning`, the harmonic tuning those name and the
banks that names, each where the game looks for it.

Checked (Linux, a main build, a copy of the TU4 golden save with the S1 made its current car
with the launcher's garage code, offline, audio muted): an import of the S1, the RX-7, the 787B
and the LP640 by the tool before the fix reproduced the report (the bank's failed open, then
the disc read error, at the start); the same with the bank in the mod crashed on the
`update:\` path; with the bank next to the package's zip, and after `repair_sound` on the old
import, the save started in free roam in the S1 and it drove. `check` named the seven banks of the
old import and nothing for the new one or the repaired one.

### Traffic car tunings (#840)

FH1 keeps an `EngineTuning` for five FM4 cars it cut as traffic cars and does not have (no model,
no database row): the 2006 Ford Focus ST (also the 2011 Ka's swap), the 2011 Audi A4 race car
(`AUD_2_A4_11`), the 2011 Chevrolet Spark and Volt and the 2009 Scion xD. They are cut down
("removing proper assignments to make this car lightweight for civilian traffic in Horizon",
FH1's own comment around the Focus ST's real slots): `EngineAmbient` and `Intake` name
`NA_EngAmb_HT.xml` and `NA_Int_HT.xml` (the `NA.fsb` placeholder, no sound), and every exhaust
level the same `Traffic_*_Exh_HT.xml`, a few low loops only (the Focus ST's 5 up to 3,908 rpm,
against `5T1_S40_Exh_HT.xml`'s 9 up to 20,000; its redline is 6,800). An import before this fix
took those copies, as for any engine no FH1 car uses, so the cars had no intake or engine
ambient sound, an exhaust that ends at about 3,900 rpm and exhaust upgrades that changed nothing.

The import now takes FM4's own copy where FH1's live (uncommented) exhaust, intake or engine
ambient slot names a `Traffic_*_HT.xml` or an `NA_*_HT.xml` and FM4's does not
(`tools/fm4_cars.py` `traffic_tuning`), at FH1's level as before. The Volt is electric and NA in
FM4 too, so it keeps FH1's copy. Every harmonic tuning file FM4's four copies name is in FH1's
`HarmonicTuning.zip`, and every bank those name is one of FH1's disc banks: nothing else comes in.
Of FH1's 807 tunings, 24 are traffic ones (`traffic_tuning`); FM4 has 2 (the Volt's and the Leaf's).

Checked with `tools/fm4_cars.py convert` on the six cars that use these engines (the Focus ST,
the Ka, the xD, the Spark, the Volt, the A4): the four tunings are FM4's (the Focus ST's
`10N2_SRT10_j_EngAmb_HT.xml`, `5T1_A200_Int_HT.xml`, `5T1_S40_Exh_HT.xml`), the Volt's and the
other engines' the same as before, no harmonic tuning file or bank added, and `check` finds
nothing missing. Over all 381 cars of the discs, `audio_plan` before and after this fix differs
only in those four tunings. Not yet heard in the game.

An import made before this fix keeps the traffic tunings until FM4 cars are imported again (the
right files come only from FM4's discs). Its `.reimagined-fm4-cars.json` has no
`"engine_tuning": 2`; when its mod folder holds one of the four, the launcher says so once when
it starts the game (`fm4cars.tuning_notice`), and the Mods page until the next import.

### Engine level (#712)

FH1 retuned the engine sound of the cars it sells (`EngineTuning`, `CarModelTuning` and harmonic
tuning per car); the FM4 cars it does not sell kept FM4's tuning, only FH1's new shift boost
(`ShiftVolumeScalar` 2.5) was set in all of them. The sound banks are the same files in both games
(351 of the 353 on FM4's disc 1 byte for byte). Measured in the game (a TU4 build of main
`9c4a2e7`, a copied save, offline, output muted; the mix the game submits dumped inside the
game and folded to stereo as the runtime folds it; radio off, chase camera, 6 s at the limiter
with the brakes held, then 5 s at full throttle; EBU R128 loudness):

| | limiter | full throttle |
|---|---|---|
| the same FH1 car with FM4's tuning instead of FH1's (S2000, '69 Camaro SS, MP4-12C, 430 Scuderia) | −2.1 LU | −1.1 LU |
| an imported FM4 car against a similar FH1 car (7 pairs: Murciélago LP640 / Aventador, R33 / R34 GT-R, Focus ST '06 / '12, Camaro SS '10 / ZL1, WRX STI '08 / '11, M5 '09 / '12, RX-7 FC / FD), before | −2.2 LU (−4.5 to −0.3) | −2.0 LU |
| the same, imported with `GlobalCarVolume` × 1.6 | −0.7 LU (−4.9 to +1.9) | −0.5 LU |

`GlobalCarVolume` is the one per-car engine level the game follows: × 1.5 on the LP640 gave
+1.9 and +2.1 LU at the limiter in two runs (the same car repeats within 0.2 LU), its harmonic
loop volumes × 1.25 +0.9, the camera's `EmissionGroupVolumes` 0.8 → 1.0 +0.8, while
`EngineVolume` 0.8 → 1.0 changed nothing. FH1's own curves never pass 1.0, and the Focus ST with
its curve at 1.38 was not louder than at 1.0, so the import stops at 1.0: the Focus ST and the
RX-7 FC, whose curves reach 1.0 first (0.86 and 0.72 before), stay 4.9 and 0.1 LU under their
FH1 pair. Peaks stayed at 0.30 of full scale or less; FH1's own cars are not changed (the
Aventador with the import loaded: −21.2 LU, −21.8 to −22.1 without). The engines an FH1 car
uses, also as a swap, keep FH1's tuning.

Then all 381 cars (`--all`, the cars' files in the package): the Autoshow's makes include the
ones FH1 lacked (Acura, listed with its RSX Type-S, TSX and Integra from the Disc 2 car packs);
the RSX Type-S was bought with credits (colour, BUY CAR?), driven out of the Autoshow (a
burnout scored) and the Veyron, DBR9 and Thunderbird loaded from the package zips as they did
from the mod.

Then the launcher's own path, headless (`fm4cars.build` with a scratch data folder) on the two
FM4 disc images: Disc 1 and Disc 2 read, the three car packs unpacked, 381 cars converted and
published in 911 s (the mod 9.8 MB, the package 5.6 GB in two zips, its header; the temporary
folder removed). A game started on that data folder lists the new makes in the Autoshow
(Mosler, Morgan, Acura...) and shows the 2010 Mosler MT900S in its showroom.

### Traffic cars (#841)

Windows, the 0.3.7 release runtime, a copy of a save, offline. `tools/fm4_cars.py convert` of
the five cars, with their files in the package:

| What | Result |
|---|---|
| Import | four added (55282 `VW_5_Beetle_04`, 55414 `CHR_5_PTCruiser_04`, 56241 `FOR_5_TaurusSHO_10`, 56340 `TOY_5_Prius_11`) and the Leaf listed as left out. Every id the twins' rows name is a row of the import. Their own engines are 55036, 55168, 55701 and 55800 (`<name>_FM4`), their own drivetrains 55035, 55167, 55645 and 55744, their wheels 55030, 55153, 65794 and 65895. The Prius gearbox is FM4's CVT and its torque curves are FM4's. FH1's rows of the five traffic cars and their parts are unchanged, and `check` finds nothing missing. `list` with FM4's discs and DLC offers the four as cars to convert |
| Change Car | the Beetle, added with the launcher's garage code, is listed as "Beetle, 2004 Volkswagen, D 209" with its picture, and Get In Car loaded it (seen with an earlier version of this change, which numbered it 5282 and shared FH1's engine) |
| Drive | the Prius and then the Beetle, each the save's current car (in every garage slot of a copy of the save), spawned at the festival and drove off (16 mph and a turn). The game read `toy_prius_11_fm4_et.xml` and `vw_beetle_04_fm4_et.xml`, their own engines' tunings. The PT Cruiser and the Taurus SHO were not driven |
| Car ids | with the Taurus SHO and the Prius at 551241 and 551340, not even the Beetle loaded, and the game spawned its default car instead. With every car id under 65536, it loaded. Moving only the Beetle's car (55282), wheel (5530), engine (5536) or drivetrain (5535) to a new id did not stop it loading |

### Ids from the table (#924)

Windows, the 0.3.7 release runtime, a copy of a save, offline.

| What | Result |
|---|---|
| Table | `tools/car_ids.py build` with FH1 (title update 4, all of its official DLC), FH2, Fast & Furious, FM4 Disc 1 and 16 FM4 DLC packs: 555 cars (88 of FH2's, 9 of Fast & Furious', 458 of FM4's, 5 of them copies of FH1's traffic cars), 26 engines, 30 drivetrains and 35 wheels of FM4's under ids of their own, 163 ids under `renumbered`. `check`: no problem. Against a database of FH1 with all of its DLC and UDLC: every FH1 and official DLC car is FH1's in the table, and the 97 FH2 and Fast & Furious cars all have UDLC's ids |
| List | `tools/fm4_cars.py list` with FM4's discs and DLC: 524 cars to convert (385 from the discs, 139 from the DLC), 29 of them under an id other than FM4's: the four traffic cars' copies, 4 under FH2's ids (51525, 51535, 51549, 51558) and 21 under 55000 + their id (56519 to 56637). Left out: the Leaf ([above](#fh1s-traffic-cars-841)) and two cars without a model in the files |
| Import | six cars, their files in the package: the Beetle's and the Prius' copies (55282, 56340, as before), the Cayman R (56541), the Sprite (56574), the SLK55 AMG (51525) and the 997 GT3 (263, its own id; its swap to the GT2 RS engine names 55999). Every row of FH1's database is in the import unchanged. Every id the new cars' rows name is a row of the import, none is an FM4 part id the table moves, and nothing is left under a renumbered car's FM4 id or in its range. Each moved engine and drivetrain has all of FM4's rows under its new id. `check` finds nothing missing |
| Drive | the Cayman R, the SLK55 AMG and the Sprite, each the save's current car (in every garage slot of a copy of the save), spawned at the festival and drove off (9 to 19 mph). The game read their engines' own tunings (`por_caymanr_12_et.xml`, `mer_slk55amg_12_et.xml`, `ah_sprite_58_et.xml`) |

## Not done yet

- Indicators: FH1 gives indicator sprites only to its traffic cars, so the converted cars get
  none.
- The physicsdefinition.bin rigid-body fields are a scaled stand-in, not decoded semantics.
- Career events, online (other players without the import see FH1's placeholder car, as for a
  car mod) and the 381 cars one by one were not checked; the three above and the RSX were.
- FM4's title updates are not read. Of the DLC packs' extra content only the cars are used:
  liveries, events, tracks and achievements are not.
- The 2011 Leaf, an electric car without an engine row ([above](#fh1s-traffic-cars-841)).
- The rest of #924: the launcher moving a save's cars and parts to the ids the table gives them
  (`renumbered`), FH2 and Fast & Furious as sources to import from, and the rally parts Forza
  Imported DLC Maker gives FM4 cars.

## Credits

The community mod **FM4 Imported** (https://www.moddb.com/mods/fm4-imported), built on
GTTeancum's **Forza Horizon 1 XE Mod** (https://www.moddb.com/mods/forza-horizon-xe-mod, with
D3FEKT, Wonder2K19 and Modforce), showed that FM4's cars can live in FH1. This converter was
written from the two games' files and executables; nothing was taken from those mods, and
their packages are not used or needed.
