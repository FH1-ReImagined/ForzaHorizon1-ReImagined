# Forza Motorsport 4 circuits in FH1 (experimental)

FH1 is built on Forza Motorsport 4's engine. The launcher can convert FM4's circuits from your
own FM4 disc and let you drive them in FH1 instead of Colorado (#648, "The Festival"). It is
experimental and off until you import a circuit: with nothing imported, the launcher and the
game behave exactly as before.

- [What you need](#what-you-need)
- [Importing circuits](#importing-circuits)
- [Driving a circuit](#driving-a-circuit)
- [Time attack and your laps](#time-attack-and-your-laps)
- [What works and what does not yet](#what-works-and-what-does-not-yet)
- [Your disc, your PC](#your-disc-your-pc)
- [Removing circuits](#removing-circuits)
- [How it works](#how-it-works)

## What you need

- **Your own Forza Motorsport 4 Disc 1** (the play disc). Disc 2 holds car packs only, so it is
  not needed here. The import takes Disc 1 in the same forms as **Import FM4 cars**: a disc image
  (`.iso`, also split or trimmed), a Games on Demand package, a folder with the disc's files, or
  an archive of one of them ([the full list](modding/fm4-cars.md#what-the-import-accepts)).
- FH1 set up in the launcher with **Title Update 4** (Setup), as for playing.
- Free space: a converted circuit takes about 3 times its size on the disc (Suzuka: about
  300 MB), and the import needs room for one circuit's work files while it runs. The import
  dialog shows both before you start.

## Importing circuits

1. **Mods → Mods → Forza Motorsport 4 tracks → Import FM4 tracks…**
2. Choose your FM4 Disc 1.
3. The launcher lists the disc's circuits (24 on Disc 1), each with its size once imported.
   Tick the ones you want, or **All**.
4. **Import**. Each circuit is converted on your PC (Suzuka takes about 40 seconds from a disc
   image). The progress
   bar shows which one, and **Cancel** stops the import: circuits finished before stay, and a
   circuit imported earlier is only replaced once its new copy is complete.

The list on the Mods page shows the imported circuits with their size, the date and your best
lap.

## Driving a circuit

Once a circuit is imported, the **Play** page shows **Drive in**: **FH1's Colorado (default)**,
or one of your circuits. Choose one and press **Play**: the game loads the circuit in place of
Colorado and puts your car on its grid, behind the start line. Choose **FH1's Colorado** to go
back.

A start on a circuit:

- plays **offline**: other players are in Colorado;
- **backs up your save** first, whatever the save manager's setting is;
- uses the car you last drove in Colorado. Choose it there first (play with **FH1's
  Colorado**): there is no car choice on a circuit yet.

## Time attack and your laps

Cross the start line to start a timed lap. The game's own HUD shows:

- the lap number and its time, and the gap to your best lap at the same place on the track
  (the ghost: `-1.25` is 1.25 s ahead of it);
- at each sector's end (FM4's own sectors: three on Suzuka), the sector time and its gap to the
  best lap's;
- when the lap ends, its time, and **NEW BEST LAP** when it is.

A lap counts when it passes every sector in order. Driving back over the start line, a shortcut
(a jump along the track) or the game's rewind ends the lap without counting it; the next
crossing starts a new one.

Your laps are kept per circuit, also when you remove or import the circuit again. **Mods → FM4
laps** lists them: each circuit's best lap first, then the newest, with each sector's time. That
tab appears once a circuit is imported.

## What works and what does not yet

Works:

- Free driving on the circuit: its buildings, stands, crowds, road and kerbs, and its collision
  (walls and the ground). A circuit imported with an earlier launcher loads its scenery only a
  short way ahead of the car, so at speed it appears as you drive: import it again.
- Timed laps with sectors, the best-lap ghost and the laps page.
- The circuit's road on the minimap, from FM4's AI line. A circuit imported with an earlier
  launcher has none: import it again.
- All 24 circuits of Disc 1 convert and load. Suzuka is the most tested one.

Not yet:

- **Races and events.** FH1's race flow loads on a circuit and puts the grid on it, but the AI
  cars do not drive yet. An import writes the road network and the race route FH1's races need;
  no race has been run with them yet, and nothing starts one on a circuit.
- **AI cars and traffic** on the circuit.
- **Choosing a car** on a circuit: you drive the car you last used in Colorado.
- **Shadows of the circuit's own buildings and trees.** Your car's shadow is drawn on the
  circuit with the Vulkan renderer (with ReGPU, the D3D12 renderer, circuits have no shadows
  yet). A circuit imported with an earlier launcher has none: import it again.
- **The map and GPS** (FH1's map is Colorado's).
- **1000 Club challenges.** They use Colorado's roads and locations. Play in Colorado to run them.
- **Online play** on a circuit.

Please report what you see, with your `forza.log`, on the issue tracker
([#648](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/648)).

## Your disc, your PC

- The import reads your own disc and converts on your PC. Nothing is downloaded, and nothing
  of FM4 is in the launcher, the game or the release. Do not share converted circuits.
- A session on a circuit sends nothing online: no server connection, no Discord activity, no
  shader-list upload and no co-op results. Laps are kept on your PC only.
- [PRIVACY.md](../PRIVACY.md) lists what the launcher and the game send in general.

## Removing circuits

Select a circuit in the list (**Mods → Mods → Forza Motorsport 4 tracks**) and press **Remove
circuit**. Its files and its database mod are deleted. Your laps stay in the laps folder below.

Where the files are, in the game's data folder (`%LOCALAPPDATA%\ForzaReImagined` on Windows,
`~/.local/share/forza` on Linux, or the folder you chose):

| Folder | What |
|---|---|
| `fm4-tracks/<circuit>/` | the converted circuit |
| `fm4-tracks/laps/<circuit>.jsonl` | your laps there, one a line |
| `fm4-tracks/laps/<circuit>.ghost.txt` | the best lap's ghost |
| `mods/.fm4-track-<circuit>/` | its database and race route, a mod the Mods page does not list |

## How it works

The launcher runs the converter (`tools/fm4_track_mod.py`, then `tools/fm4_pvs.py`) on each
circuit: the track's files and layout in FH1's formats, its collision, its shaders adapted to
FH1's, and the database rows that make free roam load it. `tools/fm4_timing.py` takes the lap's
line and sectors from FM4's AI line and database, and `tools/fm4_routes.py` builds the circuit's
road network (what the minimap draws) and a race route from the same line. A start on a circuit
passes the game
`--fm4_track_dir=<the circuit's folder>`: the game mounts that folder inside its own game folder
(the game folder on disk is never changed) and turns on what a converted circuit needs. The game
is built with ReIGlue, our fork of ReXGlue. The converter's notes are in
[festival_track_spike.md](festival_track_spike.md).
