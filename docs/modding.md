# Mods guide

A mod is a set of files the game reads in place of its own: a changed text, a tuned car, a new
setting. Forza Horizon 1: ReImagined loads mods without changing a single game file: when the
game starts, each mod's files are laid over the game's archives. Your mods stay through
rebuilds, launcher updates and a reinstall of the game files, and turning a mod off brings the
original back.

- [For players](#for-players): install, turn on and off, order, remove, check that a mod loads.
- [For modders](#for-modders): make a mod, find a file's path, package it.
- [Restrictions](#restrictions): what mods can and cannot do.
- [How it works](#how-it-works) and [why editing media.zip hung the game](#why-editing-mediazip-hung-the-game-117).

## For players

### Install a mod

1. Download the mod. It is usually a `.zip` file; it can also be a folder.
2. In the launcher, open **Mods** in the sidebar.
3. Press **Add mod…**, then **Zip file…** or **Folder…**, and pick the mod. You can also drag
   the `.zip` or the folder onto the **Mods** page.
4. The launcher checks the mod with the same rules the game uses, then copies it into your mods
   folder. It says **Added …** under the list, or why it did not add it (see
   [Troubleshooting](#troubleshooting)).
5. If a mod with the same name is there already, the launcher asks before it replaces it.
6. Press **Play**. Mods load when the game starts: if the game is running, close it and start it
   again (the **Mods** page says so while the game runs).

The launcher finds the mod inside the `.zip` by itself: at the top of the zip, or in its one
folder. It refuses a zip that would write outside the mods folder, holds links, or unpacks to more
than 8 GB.

### Turn a mod on or off

Each mod in the list has a tick. Untick it to turn the mod off; tick it again to turn it back on.
With a controller, select the mod and press A. Nothing is deleted: a mod that is off stays in the
folder and in its place in the list.

### Load order and conflicts

Mods load from the top of the list to the bottom. When two mods change the same file, **the one
lower in the list wins**: the game uses its copy. Select a mod and press **Move up** or **Move
down** to change its place. The details under the list say which files a mod shares with other
mods, and whose copy is used.

### Remove a mod

Select it and press **Remove**, then confirm. Its folder is deleted from the mods folder. Close
the game first: the launcher does not remove a mod while the game runs.

### Where the mods folder is

**Open mods folder** on the **Mods** page opens it. It is the `mods` folder in the launcher's
data folder:

| System | Folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ForzaReImagined\mods` |
| Linux | `~/.local/share/forza/mods` |
| Flatpak | `~/.var/app/<app id>/data/forza/mods` |

If you chose another **Game data folder** in **Settings > Advanced**, `mods` is in that folder.
You can also put a mod folder there yourself and press **Refresh**.

### Check that a mod loaded

The game writes what it did with your mods in `forza.log`, in the data folder (**Open log
folder** on the **Play** page). Look for the lines that start with `Mods:`:

```
Mods: .../mods in load order (a later mod wins): BetterTitle, Gearbox
Mods: Better title screen 1.2 by Sam: 1 file(s)
Mods: media/stringtables/en/main.str from BetterTitle: in place of the title update's copy
Mods: media/db/gamedb.slt from Gearbox: in place of the title update's copy, and the loose file game:\media\db\gamedb.slt
Mods: update:\media.zip holds 918 files (2 replaced, 0 added by mods; 1 loose file(s) replaced), 13.6 MB
Mods: the game reads media/stringtables/en/main.str (Better title screen)
```

- One line per mod that loaded, and `Mods: skipped <mod>: <why>` for one that did not.
- One line per file: which mod it comes from and what it replaces.
- `the game reads ...` the first time the game uses a mod's file. A file without this line has
  not been used yet (the game reads some files only when you get to them, a car's files when you
  drive it).
- When two mods have the same file: `... is in A and B: B's is used`.

### Troubleshooting

A mod the game would skip shows **(will not load)** in the list; select it to see why. The same
reasons appear in `forza.log`:

| Reason | What to do |
| --- | --- |
| no media folder | The mod's files must be under a `media` folder. The mod is packed differently: ask its author, or fix the folder (see [For modders](#for-modders)). |
| no files under media/ the game can use | Its files have names with letters outside plain ASCII, paths over 255 characters, or are whole `.zip` archives. |
| cannot read media/... | A file of the mod cannot be opened: check its permissions, or add the mod again. |
| two files differ only in case | The game does not tell `A.xml` from `a.xml`: keep one. |
| ... is over 64 MB ... | A file is too big for the game's archive and does not replace a loose game file. See [Restrictions](#restrictions). |
| the folder's name has a \| / a folder named * | Rename the mod's folder. |
| folder not found | The mod was moved or deleted: press **Refresh**. |

If the game behaves strangely with mods, untick them all and start the game again: if the
problem goes away, turn them back on one at a time to find the mod that causes it. A mod made for
the Title Update 4 build (most are) may not work with the base game.

## For modders

### Folder layout

A mod is one folder:

```
BetterTitle/
  mod.toml
  media/
    stringtables/en/main.str
    gamemodes/autoshows.xml
    db/gamedb.slt
```

- `media/` holds the files, each at the game's own path under `game:\media\` (below). Folder and
  file names are not case-sensitive.
- `mod.toml` (optional, but please add it) names the mod in the launcher and the log:

  ```toml
  name = "Better title screen"
  version = "1.2"
  author = "Sam"
  description = "The title screen reads MODS LOADED."
  ```

  All four fields are optional; without `name` the folder's name is shown.
- Anything outside `media/` and `mod.toml` (a readme, screenshots) is ignored by the game.

### Find a file's game path

The game sees all of its archives as one tree under `game:\media\`; a mod file's path is its
place in that tree.

- **An archive of the disc** (`media\*.zip`, `media\cars\*.zip`, `media\stringtables\*.zip`, ...;
  `media\zipmanifest.xml` lists them all): the archive's path without `.zip` is a folder, and its
  members are inside it.
  - `media\gamemodes.zip` member `Colorado\speed_camera.xml` is `media/gamemodes/colorado/speed_camera.xml`
  - `media\stringtables\EN.zip` member `Data_Car.str` is `media/stringtables/en/data_car.str`
  - `media\renderscenarios.zip` member `Global.xml` is `media/renderscenarios/global.xml`
- **The title update's `media.zip`** (in the update folder of your build): its members are
  already at these paths (`db/gamedb.slt` is `media/db/gamedb.slt`). Where the title update has
  a file, it is the one the game uses, so start from the title update's copy, not the disc's.
- **A DLC package's archives** (`Media\DLCZips\` in the package): `zipmount.xml` there gives each
  archive's mount point. `StringTables_pri_1650.zip` is mounted at `game:\Media\StringTables\`,
  so its member `EN/Data_Car.str` is `media/stringtables/en/data_car.str`; the members of
  `<offer>_pri_65.zip` start with `media/` already.
- **A loose file** of the game (`media\db\gamedb.slt`, `media\audio\radio\Radio_Music.fsb`, ...):
  the same path.

To check a path, put the file in a mod, start the game and look in `forza.log`: `in place of the
title update's copy` means it matched a file of the title update; `added (over any copy in the
game's other archives)` means it is new to the title update's archive, and `the game reads ...`
shows that the game used it.

Most members of the game's archives are compressed with the Xbox's LZX (zip method 21), which
common zip tools cannot read. `tools/unzip21.py ARCHIVE MEMBER OUTDIR` extracts one (it uses
`tools/unxmem`, built from `tools/unxmem.c`). `tools/make_example_mods.py UPDATE_MEDIA_ZIP OUTDIR`
makes three example mods from your own title update (a title screen text, the same text from a
second mod to try the load order, and the six-speed gearbox of #117), and shows how to extract,
change and lay out a file.

Change files with a tool that keeps their format. A mod file is used as it is: the mod loader
stores it uncompressed, and the game reads stored files as well as compressed ones. For
`gamedb.slt` (SQLite) any SQLite editor works; leave the journal mode alone (no WAL, which the
game's SQLite predates).

### Package it for others

Zip the mod's folder so that the zip holds that one folder (or the `media` folder and
`mod.toml` at its top, in which case the zip's name becomes the mod's name):

```
BetterTitle.zip
  BetterTitle/
    mod.toml
    media/stringtables/en/main.str
```

**Add mod…** accepts such a zip. Use a normal zip (stored or deflate): no links, no paths with
`..`, no files outside the mod's folder. Before you share it, add it with **Add mod…** yourself
and check `forza.log`.

Share only your own work: the files you changed, never the game's original files, whole
archives of the game or a full copy of `gamedb.slt` that you did not change.

## Restrictions

- **64 MiB per file** (67,108,863 bytes). The game's archive index keeps a file's size in 26 bits
  (TU4 `sub_82D22528`; the largest file of the game's own archives is 47 MB). A bigger file, up
  to 2 GB, only works in place of a loose file of the game with the same path (such as
  `media/audio/radio/Radio_Music.fsb`); otherwise the mod is skipped.
- **Paths up to 255 characters under `media/`, plain ASCII names** (letters, digits, spaces and
  punctuation, no `:*?"<>|`). The game copies archive names into a fixed buffer. Longer paths and
  other names are left out.
- **Files only, no whole archives.** A `.zip` inside a mod (`media/cars/X.zip`) is left out: the
  game finds a disc archive's directory at the offset `zipmanifest.xml` gives, so a different
  archive there would not be read. Put the changed files from inside it in the mod instead.
- **Title Update 4 build or base game.** Mods are added to the title update's archive. The base
  game (built without the title update) gets an empty archive for them in the cache folder
  (`mods_update`); a file taken from the title update (its `gamedb.slt`, say) may not suit the
  base game.
- **Mods load at start.** Changes to the list or to a mod's files apply the next time the game
  starts. Do not edit a mod while the game runs: a file that changes size reads as zeros past its
  new end (and `forza.log` says so).
- **Online play does not check mods.** In a session, players with different mods may see
  different things. A car one game lacks (a car pack, a car unlocker or a database mod the
  other player does not have) shows as a plain black placeholder car there, and its lobby entry
  says "DLC Car". Your game tells the server which DLC packs and which car or database mods it
  has (not your other mods), and the server tells the players of a session what they do not share
  in a chat notice; `forza.log` has a `Cars:` line for each car it showed as the placeholder
  (#567; [`docs/online_vision.md`](online_vision.md), "A car the other game lacks").
- **Your own work only.** Never share the game's original files or whole archives.
- The archive holds at most 4 GB and 65,534 files in all; at most 64 mod files are open at once,
  the others are opened again when the game reads them.

## Known to work

Checked in the game (2026-10-24, Linux):

| Mod | Result |
| --- | --- |
| `media/stringtables/en/main.str` with "PRESS START" changed (`ExampleTitleText`) | the title screen reads MODS LOADED; with `ExampleTitleText2` after it, LATER WINS! (load order); the same through a mod whose `stringtables` folder is a link; the base game too |
| `media/db/gamedb.slt` with the change of #117 (`ExampleGearbox`: a sixth gear for the Street transmission of the 1970 Boss 429 Mustang; the copy tested also had a car row added, one database page more) | the game starts and drives in free roam; its in-memory database holds the six-gear row (found in guest memory, and the original row in a run without the mod) |
| `media/renderscenarios/global.xml` (the disc's `renderscenarios.zip` member `Global.xml`, one setting changed; the title update has no copy) | added to the archive; the log shows the game reading the mod's copy at start |
| `media/stringtables/en/data_car.str` from the December IGN Car Pack's `StringTables_pri_1650.zip`, with a car's name changed, the DLC installed | the pause menu shows the changed name: a mod outranks the DLC's archives too |
| `media/audio/radio/Radio_Music.fsb` (680 MB, a link to the game's own file) | over 64 MB: replaces the loose file only, and the game reads it |

Expected to work the same way, not checked one by one yet: any other file of the game's archives
(other XML settings such as `media/gametunablesettings/globalregistry.xml`, a car's files under
`media/cars/<car>/`, textures, sounds).

## Why editing media.zip hung the game (#117)

Every file of the title update's `media.zip` carries an extra field (id `0x1123`) with the
absolute offset of its data, which the game reads instead of the file's local header. A zip tool
that replaces one file rewrites the archive and moves every file after it, but copies that field
unchanged. `db/gamedb.slt` is the twelfth of 918 files: when it changes size, the game reads each
of the 906 files after it at its old offset, wrong bytes for nearly everything it loads. That is
the hang on the very first screen (the copyright text) of #117, and it does not depend on the
database: the same archive rebuilt with a modified `gamedb.slt` stored, the fields stale, hangs
there; with the fields corrected it plays. The database change itself was fine. A mod avoids it:
the original files never move, and the mod's files get fields of their own.

## How it works

The game mounts its archives into one tree by priority: the disc's archives (`zipmanifest.xml`,
priority 95 and 80), the DLC's (`Media\DLCZips\<name>_pri_<n>.zip`: 65 and 1650 in the packages
seen) and the title update's `update:\media.zip`, mounted at `game:\media\` with priority 100000
(TU4 `sub_82581DB8`). At start the runtime puts its own file in the place of
`update:\media.zip`: the update's archive byte for byte up to its central directory, then each mod
file as a stored member, then a new central directory (the original records, except those a mod
replaces, plus the mods' records with their `0x1123` fields) and the end record. Reads of it come
from the original archive and the mod files; nothing is copied or written. The game's own zip
reader then finds a mod's file wherever it would find the update's, ahead of every other archive
and loose file. A mod file with the path of a loose file replaces that file's entry too. The base
game asks for `update:\media.zip` as well and mounts it the same way; without a title update it
gets an empty archive to hold the mods.

The game gets the mods as `--forza_mods_dir` (the folder) and `--forza_mods=A|B` (the folders to
load in load order; `|` alone for none). Without them (`play.sh`, a source build) every folder in
`<user data>/mods` loads, by name.

Source: `forza-recomp/src/platform/mod_archive.h` (the archive, the mod folders),
`forza-recomp/src/kernel/mod_overlay.cpp` (the runtime part), `launcher/forza_launcher/mods.py`
and `ui/mods_page.py` (the **Mods** page). Tests: `forza-recomp/tests/mod_archive_test.cpp`,
`launcher/test_mods.py`.

The alternative, hooking the game's archive functions (open, directory lookup, read), would be
two sets of addresses (base and TU4) and the game's LZX streams and caches to deal with; the
archive file needs neither.
