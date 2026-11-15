# Drag tyres

FH1's own cars have no drag tyre. Their tyres stop at level 3 (street, sport, race), apart from the
Rally Expansion's rally tyre (level 5, compound 9). The FM4 car import leaves FM4's level-4 drag tyre
out ([why](fm4-cars.md#rally-parts-924)). **Mods → Drag tyres** gives every car a drag tyre as a
sixth tyre compound in **Custom Upgrade → Tires and Rims → Tire Compound** (#924). It has far more
grip off the line, and less in corners and on loose ground.

- [For players](#for-players)
- [What the mod is made of](#what-the-mod-is-made-of)
- [Checked in the game](#checked-in-the-game)
- [Credits](#credits)

## For players

1. Open **Mods** in the launcher and tick **Drag tyres for every car**.
2. Press **Play**. The launcher makes the tyre from your game's own files before the game starts
   (about 2 seconds), and makes it again whenever your DLC or your imported FM4 cars change.
3. In the game: **Dak's Garage → Custom Upgrade → Tires and Rims → Tire Compound**. **Drag Tire
   Compound** is the last card, after Race (and Rally with the Rally Expansion), at 10,000 Cr.

Every car means:
- FH1's own cars;
- the cars of every DLC or fan pack you have installed;
- the cars of your FM4 cars import.

Untick the box to take the tyre away again: the game then starts without the mod. **First fit another
tyre compound to every car that has the drag tyre.** Without the mod, a car with the drag tyre fitted
does not load: the game gives you another car instead, as for any missing content. Tick the box
again and the car is back with its drag tyre. The launcher asks before turning the mod off.

The name and description are in English in every language.

## What the mod is made of

The launcher builds a hidden mod, `mods/.reimagined-drag-tyres` (`forza_launcher/drag_tyres.py`),
with `tools/drag_tyres.py`. It loads before your own mods, so a mod of yours with an `upgrades.str`
wins. It is made again only when the data file, the builder, the title update, the installed DLC or
the FM4 cars import change (a fingerprint in `drag_tyres.json`). A failure never stops the game from
starting: it starts without drag tyres, and the log says why.

| File | What it holds |
|---|---|
| `media/db/patch/924006_merge.slt` | a database patch the game merges over its database as it does a DLC pack's: the compound, its curves, its upgrade and a tyre upgrade for each car (below) |
| `media/stringtables/<lang>/upgrades.str` | the game's own table (the title update's, with the installed DLC's), plus the two strings: `IDS_Name_DragTire` "Drag Tire Compound" and `IDS_Description_DragTire` |

The rows in the patch, all from `tools/drag_tyre.json`:

| Table | Rows |
|---|---|
| `List_TireCompound` | compound 10, `10_DragTire`, with its own friction values: more longitudinal grip than FM4's drag tyre (1.95 against 1.2 under acceleration) and more lateral grip (1.65 against 1.2) |
| `List_TireFrictionMultiCurve`, `List_TireFrictionCurve`, `List_TireAffectCurve` | its 18 curves: multi curves 19 and 20, friction curves 37 to 40, affect curves 109 to 120 |
| `Upgrades` | row 273: a tyre compound upgrade (type 21) at level 6. It shows FM4's drag tyre icons (`tire_compound_4`, `tire_compound_LG_4`), which FH1's own UI textures have |
| `List_UpgradeTireCompound` | for every car, key car id × 1000 + 6: level 6, compound 10, 10,000 Cr, no mass change. A car with a level-6 tyre of its own already keeps it |

The ids are free in FH1's title update, in all of its official DLC and in UDLC (checked against a
database of all of them). The builder stops when one of them is another row. So are the string keys, 32624 and 63249, in all 20
of FH1's languages. A key another string has in any language moves to the next one free in all of
them, as the upgrade's reference is the same for every language. The cars come from FH1's database,
from the title update's and every installed pack's database patches, and from the FM4 cars import's
database.

    tools/drag_tyres.py --fh1 GAME --update TU4 [--dlc DLC] [--cars-db MODS/FM4Cars/media/db/gamedb.slt] --out MOD

## Checked in the game

Windows, the 0.3.7 release runtime, a copy of a save, offline, with the mod built from the game's
files (FH1, its installed DLC and an FM4 cars import of six cars): drag tyres for 257 cars.

| What | Result |
|---|---|
| Against the original mod | the compound, its 18 curves and its upgrade row are the same as in warshack4874's mod. The tyre rows of the 252 cars both have are the same. The strings are added to every language's table, and none of FH1's strings changes |
| Loading | the game reads `media/db/patch/924006_merge.slt` and the mod's `upgrades.str` (its log) |
| An FH1 car | a Subaru BRZ of the save: **Tire Compound** lists, after Race and Rally, **Drag Tire Compound** at 8,600 Cr (10,000 less the 14% discount), grip −0.06 against its race tyres, PI 500 → 452 |
| An imported FM4 car | the Cayman R, with the drag tyre fitted in the save, spawned at the festival and drove off |
| The mod turned off | the same save, with the drag tyre fitted, started without the mod: the Cayman R did not load, and the game spawned another car. With the mod on again it loads |

Not checked: a drag race with it, and how it compares in numbers with the other compounds.

## Credits

The drag tyre is warshack4874's: the compound's values and curves, its upgrade and its texts come
from their drag tyre mod, with their permission.
