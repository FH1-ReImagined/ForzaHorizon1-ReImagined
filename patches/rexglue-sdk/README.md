# Former SDK patch series (not applied)

These patches are now commits in [ReIGlue](https://github.com/FH1-ReImagined/ReIGlue), the
project's fork of the ReXGlue SDK: one commit per patch, with the same numbers, on ReXGlue
nightly 0.10.0.24 (`bd833a2`). `tools/build_rexglue_sdk.sh` checks out the ReIGlue tag it pins
(`REIGLUE_TAG`) and does not read this folder any more; the SDL patch in `sdl3/` is in ReIGlue's
`patches/sdl3/`. The folder and `docs/sdk_patches.md` are kept for reference for now and will
be removed later. New SDK changes go to ReIGlue (see CONTRIBUTING.md).
