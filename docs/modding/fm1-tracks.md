# Forza Motorsport (2005) circuits (#930)

Players import them with **Import FM1 tracks…** ([fm4-tracks.md](../fm4-tracks.md#forza-motorsport-2005-circuits)).
This page is for working on the conversion.

## The pipeline

1. `tools/fm1_tracks.py stage FM1_DISC FM4_DISC1 CIRCUIT OUT` lays an FM1 circuit out as an FM4
   one: a folder shaped like FM4's Disc 1 (`Media/tracks/<Circuit>`, `Media/AI/Tracks`,
   `Media/db`, `Media/physics`). Its `bin.zip` holds FM1's meshes as FM4 models and FM1's
   textures as FM4 `.bix` pairs; the PVS, collision, AI lines, database and track settings are
   FM4's for the same circuit.
2. The launcher converts that folder as it converts FM4's own circuits
   (`launcher/forza_launcher/fm4tracks.py`: `tools/fm4_track_mod.py`, then `tools/fm4_pvs.py`).

`tools/fm1_tracks.py list FM1_DISC FM4_DISC1` names the circuits both discs have. Both tools
are plain Python (no numpy or PIL): a circuit takes 15 to 50 seconds to lay out.

| Circuit | FM1 folder | Collision shared with FM4 | Visibility reach |
|---|---|---|---|
| Tsukuba | `tsukuba` | 88 % of the vertices | 700 m |
| Road America | `RoadAmerica` | 95 % | 500 m |
| Silverstone | `Silverstone` | 81 % | 700 m |
| Maple Valley | `FarmersLip` | 81 % | 700 m |
| Laguna Seca | `Lagunaseca` | 30 % | 700 m |
| Sunset Peninsula | `SunsetSpeedway` | 43 % | 700 m |

The share of vertices undersells Laguna Seca and Sunset: FM4 cut their collision into other
triangles but kept the ground. What decides it is the height of FM4's collision under the
centre of each of FM1's ground triangles: within 15 cm for 99.8 % of Laguna Seca's road (94 %
of its grass) and all of Sunset's road, as for Tsukuba (99.8 %). The Nürburgring, Road Atlanta,
the test track and Fujimi Kaido (14 %, FM4's covers another part of the mountain) were rebuilt
for FM4; Rio, Tokyo, Pacific Shipyards, New York, Alpine Ring and Blue Mountains have no FM4 twin
on Disc 1.

### Circuits with FM1's own collision

Nine come with everything FM1 has of its own (`OWN_CIRCUITS`), staged under names of their own
so none replaces an FM4 import: **Fujimi Kaido Hill Climb** (`FM1Fujimi`, `JapaneseMountain`),
**Tokyo** (`FM1Tokyo`), **New York** (`FM1NewYork`), **Pacific Shipyards**
(`FM1PacificShipyards`, `Vancouver`), **Rio de Janeiro** (`FM1Rio`), **Alpine Ring**
(`FM1AlpineRing`, `AlpineYodel`), **Blue Mountains** (`FM1BlueMountains`, `FigJam`), and FM1's
own **Road Atlanta** (`FM1RoadAtlanta`; FM4 rebuilt it) and **Test Track** (`FM1TestTrack`).

- **Collision:** FM1's `QuickPreview1.col` is FM4's format but for byte order and record sizes
  (FM4's Tsukuba is FM1's, converted): `fm1_collision` writes it as an FM4 `.col`, which
  `tools/fm4_col.py` converts as it does FM4's.
- **Lap line:** the middle of FM1's `roadinner` / `roadouter` splines (`road_centre`; Tsukuba's
  first point is FM4's AI line's waypoint 0 within 0.2 m), begun level with grid slot 1 and
  written as an FM4 AI line (`write_geo`, read by `tools/fm4_spawn.py` and
  `tools/fm4_timing.py`). Without splines, a way over the collision's road between FM1's trackside
  cameras (`cameras_line`, `tools/fm4_line.py`). Lines: Tokyo 3,624 m (FM1: 3,622), New York
  6,235 m (6,277), Rio 5,025 m (5,042), Alpine Ring 3,556 m (3,562), Blue Mountains 5,004 m
  (4,989), Road Atlanta 4,106 m (4,088), Test Track 7,698 m (7,690; its splines stop 393 m short
  of their start and are closed over the road).
- **Grid, sectors, lap length, name:** `media/cabs/Mega.ca2` (`tracks/<folder>/quickpreview.xml`,
  `forzadb/tracklist.xml`; `mega_track`). The disc image import reads that archive too.
- **Database and settings:** Tsukuba's (`OWN_HOST`), with an `Environments` row of the
  circuit's own (ids 130-136, 138 and 139, unused by both games), a `Tracks` row with FM1's length and FM1's
  split points. Tsukuba's model data, static texture pack (renamed `<circuit>.stx.bin`: the game
  waits for that name) and track settings.
- **Point to point:** Fujimi's hill climb and Pacific Shipyards (one road from the docks into
  town) are not laps (`POINT_TO_POINT`): their line runs from the grid to the end.
  `tools/fm4_timing.py` marks a line whose ends are over 100 m (and five of its usual steps)
  apart `open 1` in `lap.txt` (none of FM4's Disc 1 layouts is), and
  the game's timer (`forza-recomp/src/platform/fm4_lap.h`) then times a run from the start to the
  finish: FM1's course length along the line (Pacific Shipyards' spline runs 360 m past the
  barrier its course ends at). Driven: a run of Pacific Shipyards timed its five sectors and
  finished in 4:50.13; Fujimi's 10.8 km climb its nine, in 10:05.20.

**The game's load list.** The game loads a circuit's models and textures through one list of
6,144 entries with no bound (`CTrackPresentation::UpdatePVS`'s `TCachedLoadBatcher<6144>`;
`CachedLoadBatcher::AddRequest` writes past it). Tokyo, 4,861 models and 2,518 textures, faulted
as it loaded; a short view did not help. So:
- meshes without a lightmap are joined per 100 m, base texture and light (`merge_unlit`), in
  larger tiles while a circuit is over the list (`MERGE_TILES`: Road America 300 m);
- where that is not enough (Tokyo, New York, Rio, Road Atlanta), FM1's lightmaps of 128 or less a side go into
  512 x 512 atlases of one size each, their blocks copied (`fm1_tex.Atlases`; Tokyo's 1,142 into
  21 pages; DXT5 ones as DXT1), and lit meshes are joined like the unlit ones;
- the stage refuses a circuit still over `MAX_LOAD`. Tokyo now: 3,563 models and 1,397 textures.

The nine were driven on the rig (2026-11-17, a build of this branch): Tokyo, New York, Rio,
Alpine Ring and Blue Mountains timed laps and sectors, Road Atlanta and the Test Track laps;
Pacific Shipyards and Fujimi timed a run from start to finish. Tokyo, New York and Pacific Shipyards are dusk or night circuits in FM1
(`TimeOfDay` 20.75, 19.00, 1.00): their lightmaps are lifted onto FM4's daylight like Sunset's.

Maple Valley (`FarmersLip`) was exported by an older FM1 tool: `texcoordscale` and
`exporterversion` are named files in its archive, the hidden slots before the first file start
with the trees' `instscalescale`, and an instanced tree's record holds its instance number and
then its texture (no volume lightmap). Placed as on the other circuits, those 1,302 trees drew
as leaf planes floating over the road and hid the crowds and fences behind them (the same lap
driven with and without them), so they are left out: Maple Valley's meshed trees still line
the whole lap. Elsewhere an instanced tree's canopy normally starts 2 to 3 m over the ground
(Laguna Seca 2.3, Road America 2.7 at the median; the trunks are meshes of their own), but
Fujimi's started at a median 9.7 m, up to 20, and drew floating over the road: a canopy more than
5 m over the circuit's own meshes under it is lowered to 2.5 m (`settle_trees`; Fujimi 536 trees,
Blue Mountains 164, New York 125, Road America 3, Laguna Seca none).
FM4's Maple Valley has no `diff_1` model, so the two static-pack textures come from a
four-texture `diff_spec_1` one.

## What was found

The docstring of `tools/fm1_tracks.py` lists the formats and rules: FM1's resource archive, the
vertex layouts and uv decoding, the z mirror, mesh records' texture ids, blend meshes and their
masks, instanced trees, why every texture keeps its full mip chain and why each model carries an
FM4 model's command buffers. `tools/fm1_tex.py` converts the textures.

## Testing

`python -m unittest discover -s tools -p 'test_fm1_tracks.py'` (synthetic data, no game file).
In game, every circuit was imported with the launcher's own import and driven with the rig's
autopilot on its lap line, beside FM4's own import of the same circuit where FM4 has one. The
lap timer: `forza-recomp/tests/fm4_lap_test.cpp` (laps, and an open line's runs).

## Not done

- FM1's own visibility (`QuickPreview1.pvs`) is not decoded: cells of 150 m see what is within
  the reach above.
- Each instanced tree is one shade (the mean of its volume lightmap), not lit per texel.
- The sky box and power lines are left out; Tsukuba's pond has not been seen in game.
- Why atlas lightmaps fault on Laguna Seca, Road America and Alpine Ring.
- FM1's Nürburgring is not offered: it is about 1.34 times FM4's (and the real one's) size in
  world units (its road splines 28.1 km round, FM1's listed lap 20.99 km; its collision 8.3 by
  6.6 km against FM4's 7.2 by 5.8), so it would drive a third too large. It stages and fits the
  load list with 600 m tiles. FM1's shorter and reverse layouts are not offered.
- FM1's night lighting (Tokyo, New York, Pacific Shipyards) is lifted to daylight, not drawn at
  night.

**Lightmap format.** FM1's lightmaps are written with the texture format word all of FM4's
lightmaps have (`0x1A207F52`, Silverstone's 1,367; `LM_FORMAT`), not `fm1_tex`'s plain DXT1
word, which FM4 uses for colour textures. Their brightness is FM4's already (median 92 against
91 at Silverstone, quartiles 62 / 121 against 63 / 122), but under the plain word the circuits
drew washed out: pale asphalt, grass and walls beside FM4's own Silverstone from the same spot.
With FM4's word they come out close to it (the asphalt a little lighter, as FM1's texture is).

**Dark circuits.** FM1 lit some circuits for the evening or night: Sunset's infield drew black
under FH1's daylight (FM1's lightmap median 55; its deciles 21-41 where FM4's are 78-112), and
Tokyo, New York and Pacific Shipyards sit near 30. Where FM1's median is under `LM_DARK` (62),
the lightmaps' deciles up to the median are laid onto FM4's for the circuit (FM1-only ones:
Tsukuba's), and from the median the curve runs back to white unchanged (`lightmap_curve`,
`fm1_tex.grey_curve`, `fm1_tex.map_grey`). A lifted texel's colour cast shrinks as much as it is
lifted (Sunset's dark lightmaps are bluish; scaled, the infield went blue). The daylight
circuits keep theirs: Maple Valley (71) and Laguna Seca (110) drew paler than FM4's own with it.

**Cut-out textures.** The game draws alpha-tested track models with their alpha as a dither,
so FM1's soft-edged leaves (a third of their texels in between) drew as a speckled see-through
screen with each card's rectangle showing, and their alpha thinned down the mip chain (Laguna
Seca's oak leaves 16 % solid at the top, 5 % three levels down) until trees from afar were pale
cards. Alpha-tested textures now keep the top level's coverage on every level and are made
solid or clear (`fm1_tex.keep_alpha_coverage`, `fm1_tex.hard_alpha`): Laguna Seca's canopies
are solid on their trunks.

**Long thin textures.** Textures past 8:1 (Tokyo's 64 x 1024 kerbs) are widened to 8:1, a shape
whose mip layout is FM4's own.

**Lightmap atlases** are used only where a circuit does not fit the load list without them. On
Laguna Seca, Road America and Alpine Ring a model with an atlas lightmap faulted in
`CBaseModelRenderer::FixupTextures` as the circuit loaded (Tokyo, New York, Rio and five more
loaded with them); the cause is not found, and none of those three needs them.

A disc image is read by `tools/xiso_extract.py` (the original Xbox's game partition is at XGD1's
offset, 0x18300000; a rebuilt image has it at 0): `fm4tracks.fm1_folder` reads the chosen
circuits' `media/Tracks/<folder>` into the import's work folder and stages from there. Staged
from an image holding FM1's Tsukuba folder and from the folder itself, the circuit is the same
(every `bin.zip` member's data identical; only the zip's timestamps differ).
