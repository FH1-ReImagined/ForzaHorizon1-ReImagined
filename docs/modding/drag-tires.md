# Drag tires

FH1's own cars have no drag tire. Their tires stop at level 3 (street, sport, race), apart from the
rally tire (level 5, compound 9). The FM4 car import keeps FM4's level-4 drag tire only on the cars
its released version had ([why](fm4-cars.md#rally-parts-924)). **Mods → Drag tires** gives every
car a drag tire as a sixth tire compound in **Custom Upgrade → Tires and Rims → Tire Compound**
(#924).

Against FH1's Race tires (compound 8, `8_RacingSlick` in the title update's database), from
`tools/drag_tire.json`:

| | Race (8) | Drag (10) |
|---|---|---|
| grip under acceleration (`TorqueFreeLongFrictionScaleAccel0` / `Accel1`) | 1.2 / 1.2 | 1.95 / 1.6 |
| the speed of the second value (`TorqueFreeLongFrictionScaleAccelSpeed1`) | 15 | 25 |
| grip under braking (`TorqueFreeLongFrictionScaleBrake`) | 1.7 | 1.39 |
| lateral grip scale (`TorqueFreeLatFrictionScale`) | 1.65 | 1.65 (the same) |
| lateral friction curves' scale, light / heavy load (curves 37, 38 against Race's) | 1.29 / 1.03 | 1.21 / 0.97 |
| where the lateral curves peak (point of 99 over the slip range) | 12 / 8 | 20 / 16 |
| the lateral curves at the end of the slip range | 0.852 / 0.826 | 0.818 / 0.789 |
| grip at 60 and at 300 on the temperature curve | 1.0 / 0.99 | 0.9 / 0.9 |
| the wet values | 0.8, 1.5 | 0.8, 1.5 (the same) |

So it has far more grip under acceleration and less under braking. In corners its lateral scale is
Race's, but its lateral friction curves peak about 6% lower (1.21 against 1.29, 0.97 against
1.03), at a larger slip angle, and fall further past the peak; it also loses more grip when cold
or overheated. That is what the game showed on a BRZ: grip −0.06 against its race tires. Nothing in
the database sets a compound's grip per surface, so nothing here says how it does on dirt or grass.
The tire's description in the game (below) says the same: more grip off the line, less under
braking and slightly less in corners.

- [For players](#for-players)
- [What the mod is made of](#what-the-mod-is-made-of)
- [Checked in the game](#checked-in-the-game)
- [Credits](#credits)

## For players

1. Open **Mods** in the launcher and tick **Drag tires for every car**.
2. Press **Play**. The launcher makes the tire from your game's own files before the game starts
   (about 2 seconds), and makes it again whenever your DLC or your imported FM4 cars change.
3. In the game: **Dak's Garage → Custom Upgrade → Tires and Rims → Tire Compound**. **Drag Tire
   Compound** is the last card, after Race (and Rally with the Rally Expansion), at 10,000 Cr.

Every car means:
- FH1's own cars;
- the cars of every DLC or fan pack you have installed;
- the cars of your FM4 cars import.

Untick the box to take the tire away again: the game then starts without the mod. **First fit another
tire compound to every car that has the drag tire.** Without the mod, a car with the drag tire fitted
does not load: the game gives you another car instead, as for any missing content. Tick the box
again and the car is back with its drag tire. The launcher asks before turning the mod off. Not
checked yet: whether such a car is still in the garage after the game saved while the mod was off.

Why the part fallback does not help here: ReImagined loads a car part the game's database doesn't
have as the car's stock part (#836, #862, `forza-recomp/src/hooks/car_part_fallback.cpp`), and the
tire compound is one of the parts it covers. But that fallback is in the car physics loader's five
part creators. Before them, `CCar::CreateCarParams` (TU4 `0x8253F6D0`) reads the car with one query
that joins `List_UpgradeTireCompound ON List_UpgradeTireCompound.Id = <the fitted tire>` (an inner
join, whenever the car has a tire part): with no such row the query has no row at all, and the car
is not made. Covering it needs a hook of its own in that function (the installed parts it is given
and where the tire id sits in them), checked in the game; that is not part of this mod.

Online, a player without the mod has no `car id × 1000 + 6` row. What their game shows of another
player's car on drag tires has not been checked. [online_vision.md](../online_vision.md) has a
remote car whose stock wheel row is missing shown normally, but the tire goes through the query
above, so that case does not tell.

Leaderboards: a score the game submits names only the car, its class and PI and the assists, so
Forza Live's boards cannot tell a run on drag tires from one on Race tires (the tire also lowers
the PI: the BRZ went from 500 to 452). Keeping such runs off the ranked boards is not done yet.

The name and description are in English in every language.

## What the mod is made of

The launcher builds a hidden mod, `mods/.reimagined-drag-tires` (`forza_launcher/drag_tires.py`),
with `tools/drag_tires.py`. It loads before your own mods, so a mod of yours with an `upgrades.str`
wins. It is made again only when the data file, the builder, the title update, the installed DLC or
the FM4 cars import change (a fingerprint in `drag_tires.json`). A failure never stops the game from
starting: it starts without drag tires, and the log says why.

| File | What it holds |
|---|---|
| `media/db/patch/924006_merge.slt` | a database patch the game merges over its database as it does a DLC pack's: the compound, its curves, its upgrade and a tire upgrade for each car (below) |
| `media/stringtables/<lang>/upgrades.str` | the game's own table (the title update's, with the installed DLC's), plus the two strings: `IDS_Name_DragTire` "Drag Tire Compound" and `IDS_Description_DragTire` |

The rows in the patch, all from `tools/drag_tire.json`:

| Table | Rows |
|---|---|
| `List_TireCompound` | compound 10, `10_DragTire`, with its own friction values: more longitudinal grip than FM4's drag tire (1.95 against 1.2 under acceleration) and more lateral grip (1.65 against 1.2) |
| `List_TireFrictionMultiCurve`, `List_TireFrictionCurve`, `List_TireAffectCurve` | its 18 curves: multi curves 19 and 20, friction curves 37 to 40, affect curves 109 to 120 |
| `Upgrades` | row 273: a tire compound upgrade (type 21) at level 6. It shows FM4's drag tire icons (`tire_compound_4`, `tire_compound_LG_4`), which FH1's own UI textures have |
| `List_UpgradeTireCompound` | for every car, key car id × 1000 + 6: level 6, compound 10, 10,000 Cr, no mass change. A car with a level-6 tire of its own already keeps it |

The ids are free in FH1's title update, in all of its official DLC and in UDLC (checked against a
database of all of them). The builder stops when one of them is another row. So are the string keys, 32624 and 63249, in all 20
of FH1's languages. A key another string has in any language moves to the next one free in all of
them, as the upgrade's reference is the same for every language. The cars come from FH1's database,
from the title update's and every installed pack's database patches, and from the FM4 cars import's
database.

    tools/drag_tires.py --fh1 GAME --update TU4 [--dlc DLC] [--cars-db MODS/FM4Cars/media/db/gamedb.slt] --out MOD

## Checked in the game

Windows, the 0.3.7 release runtime, a copy of a save, offline, with the mod built from the game's
files (FH1, its installed DLC and an FM4 cars import of six cars): drag tires for 257 cars.

| What | Result |
|---|---|
| Against the original mod | the compound, its 18 curves and its upgrade row are the same as in warshack4874's mod. The tire rows of the 252 cars both have are the same. The strings are added to every language's table, and none of FH1's strings changes |
| Loading | the game reads `media/db/patch/924006_merge.slt` and the mod's `upgrades.str` (its log) |
| An FH1 car | a Subaru BRZ of the save: **Tire Compound** lists, after Race and Rally, **Drag Tire Compound** at 8,600 Cr (10,000 less the 14% discount), grip −0.06 against its race tires, PI 500 → 452 |
| An imported FM4 car | the Cayman R, with the drag tire fitted in the save, spawned at the festival and drove off |
| The mod turned off | the same save, with the drag tire fitted, started without the mod: the Cayman R did not load, and the game spawned another car. With the mod on again it loads |

Not checked: a drag race with it, and runs on it against Race tires on the same car (the numbers
above are the data, not measured runs).

## Credits

The drag tire is warshack4874's: the compound's values and curves, its upgrade and its texts come
from their drag tire mod, with their permission.
