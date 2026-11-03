# Contributing

Thanks for helping with Forza Horizon 1: ReImagined! This page is the short version of how
work gets in. The details live in `docs/` (start with `docs/PROJECT.md`).

## Ground rules

- **No game code or data, ever.** No `default.xex`, extracted files, generated code
  (`forza-recomp/generated*/`), saves, DLC, disc images or builds in commits, issues or PRs.
  Screenshots and logs are fine.
- Don't say where a copy of the game came from. Players use their own.
- Keep the repository free of anything you can't publish under its license: GPL-3.0-or-later
  with the additional permission in `NOTICE.md` (`LICENSE`). Your contribution is licensed the
  same way. Third-party code needs a license that is compatible with the GPL (MIT, BSD,
  Apache-2.0, zlib and similar) and its notice kept beside it.
  Third-party binaries (for example NVIDIA's DLSS runtime) are downloaded by the launcher
  at run time with a pinned hash, never committed.

## Workflow

1. **Pick or open an issue.** Issues are the backlog. Labels: `priority: 1` (highest) to
   `priority: 5`, `WIP` + a `[WIP] ` title prefix + an assignee while someone works on it,
   and `nvidia` / `amd` for GPU-specific problems. Comment on an issue before you start so
   work isn't done twice.
2. **Branch from `main`** (`fix/...`, `feature/...`) and open a pull request to `main`.
   Direct pushes, force pushes and deleting `main` are blocked.
3. **A PR needs:** one approving review from the code owner, resolved review threads,
   signed commits ([how to sign](https://docs.github.com/en/authentication/managing-commit-signature-verification)),
   a linear history (squash or rebase), and the required checks green:
   `launcher-linux`, `launcher-windows`, `flatpak`, `installer-test` (the `release`
   workflow runs them on every PR). A new push needs a new approval.
4. **Keep PRs focused.** Separate fixes from features (e.g. a renderer fix and a new upscaler
   in different PRs) so the small, safe part can be merged first.
5. **Releases and tags** (`v*`) are made by the maintainer only.

## Code

- **SDK changes** go to [ReIGlue](https://github.com/FH1-ReImagined/ReIGlue), the project's
  fork of the ReXGlue SDK: one commit per change on its `main`, the subject starting with the
  next free number (`NNNN Area: what`) and the message saying what changes and why. A new
  ReIGlue revision is then pinned in `tools/build_rexglue_sdk.sh` (`REIGLUE_COMMIT`), which CI builds on
  Linux, Windows and macOS. A reviewed full commit can be used before a maintainer publishes
  a tag; leave `REIGLUE_TAG` empty in that case. When supplied, the tag must resolve to the
  pinned commit. Both paths verify ancestry against the nightly SDK base. To try a change locally, build your ReIGlue checkout with
  `REIGLUE_DEV=1 REXSDK_SRC=<checkout> tools/build_rexglue_sdk.sh` (its source and submodules
  are used as checked out; initialize and prepare submodules separately). Change behaviour on every
  GPU only on purpose: gate vendor-specific fixes on the vendor or a cvar, and keep debug
  options off by default. (`patches/rexglue-sdk/` and `docs/sdk_patches.md` are the old patch
  series, kept for reference; they are no longer applied.)
- **Game hooks** go in `forza-recomp/src/`. Title Update 4 is the default build: hooks use
  base-game names and get TU4 addresses through `forza-recomp/src/tu4_addresses.h`
  (`tools/tu_address_map.py`, `docs/dlc.md`). Both builds must compile.
- **Launcher** code is in `launcher/` (`docs/launcher.md`), with unit tests in
  `launcher/test_*.py`. Long work never runs on the GUI thread, and errors stay visible with
  the log path.
- **Server**: the Forza Live server is its own repository,
  [FH1-ReImagined/forza-live](https://github.com/FH1-ReImagined/forza-live), checked out here as the `server/` submodule
  (`git clone --recurse-submodules`, or `git submodule update --init` in an existing clone).
  Server changes are pull requests there; then a pull request here moves `server/` to the
  new commit (`git -C server fetch && git -C server checkout <commit> && git add server`).
  Tests: `cd server && python3 -m unittest discover -s tests -t .`.

## Testing

- Launcher link-preservation tests require file and directory symlink support in the temporary
  folder. They skip when the filesystem or Windows account lacks it; copy-fallback tests still run.
- `tools/prerelease.sh` runs the local checks in one go: SDK overlay, both builds, unit
  tests, booting to the main menu with each upscaling filter, a local Forza Live server,
  the render scenes against image baselines (`docs/render-tests.md`; skipped until you record
  baselines) and the launcher's disc check (`--only <check>`, `--skip-build`, `docs/PROJECT.md`).
- Test with a copy of a save, never your real profile, and against a local server, never the
  public one.
- For rendering bugs, `docs/rendering.md` has the RenderDoc recipe and the driver checks
  (validation layers, lavapipe, `tools/rdc_float_scan.py`). Say which GPU and driver you
  tested on; GPU-specific fixes need a test on that vendor.

## Docs

Update the matching file in `docs/` with what you found or changed (measurements with the
method, addresses, how to reproduce). Short and factual beats long.
