# A world past 8,192 m

Off by default. Colorado and every circuit run the game's own code.

## The limit

The game keeps positions relative to the camera as 32-bit fixed point with 18 fraction bits
(`Vec4Fixed<18>`; the name and the layout are from the FH2 prototype's symbols, where the same
type is `Vec4Fixed<17>`). That is 4 micrometre steps and a range of ±8,192 m, and the
conversion saturates: a position further out becomes exactly ±8,192 m.

Colorado fits. A converted world that reaches further does not:

- Fast & Furious's roads run to 8.5 km south of its origin. Past z = -8,192 m the chase camera
  stops following the car.
- FH2's world reaches 10.8 km east. A start at x 9.1 to 9.4 km streamed only the zone at
  x 8.25 km: the car hung with no road and no collision under it.

## The switch

`ICamera::GetPos` returns the type, and 83 instructions in Title Update 4 convert to or from it
(the cameras, `CMultiCam::UpdateViewAudioData`, `CGame::Update`, the cars, the collision
objects, the render thread, the menu cameras). Each is one vector instruction with the 18 in it:
`vcfpsxws128` / `vctsxs` to fixed, `vcsxwfp128` / `vcfsx` to float.

`forza-recomp/hints_tu4/wide_world.toml` puts a hook before each
(`src/hooks/wide_world.cpp`):

- switch off: the hook returns false and the game's instruction runs as it is. The cost is one
  predictable branch at each of the 83 places;
- switch on: the hook converts with 17 bits (8 micrometre steps, ±16,384 m) and the
  instruction is jumped over.

The switch is `FORZA_WIDE_WORLD=1`, read once before the game runs (values of the two scales
must never meet, so it cannot change while the game runs). A mounted world
(`fm4_track_dir`) whose folder has a `wide_world.txt` gets it by default; `FORZA_WIDE_WORLD=0`
keeps it off. The log says `Wide world: positions kept with 17 fraction bits` when it is on.

Title Update 4 only (the build every player runs); the disc image's build has no hooks and
no switch.

## Not covered

- Positions sent to other players were not looked at. A wide world is for players who all
  run it.
- ±16,384 m is the new limit. FH2's roads reach 14.8 km east, inside it; its zones end at
  10.85 km, and nothing past them was driven.

## Checking the list

`tools/wide_world_hooks.py --tu4 /path/to/tu4/default.xex` finds every such conversion in the
executable's code and fails if one has no hook, a hook differs, or a hook is anywhere else
(`--write` rewrites the list). `tools/test_wide_world_hooks.py` checks the tool and the list's
shape with made-up instructions, no game files.
