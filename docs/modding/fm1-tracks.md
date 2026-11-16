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

Laguna Seca shares half its collision with FM4's and is not offered; the Nürburgring, Road
Atlanta, Sunset and the test track were rebuilt for FM4.

## What was found

The docstring of `tools/fm1_tracks.py` lists the formats and rules: FM1's resource archive, the
vertex layouts and uv decoding, the z mirror, mesh records' texture ids, blend meshes and their
masks, instanced trees, why every texture keeps its full mip chain and why each model carries an
FM4 model's command buffers. `tools/fm1_tex.py` converts the textures.

## Testing

`python -m unittest discover -s tools -p 'test_fm1_tracks.py'` (synthetic data, no game file).
In game, the three circuits were checked with the rig on root-v037 (they load, drive, and the
scenery matches FM1's); the tool's output was checked byte for byte against the builds tested
there.

## Not done

- FM1's own visibility (`QuickPreview1.pvs`) is not decoded: cells of 150 m see what is within
  the reach above.
- Each instanced tree is one shade (the mean of its volume lightmap), not lit per texel.
- The sky box and power lines are left out; Tsukuba's pond has not been seen in game.
- FM1's disc is read from a folder only.
