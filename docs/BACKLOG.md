# Backlog

Planned work, not started; each item is also a GitHub issue. Findings and progress go to
`docs/PROJECT.md` (status log) or the topic's doc once work begins.

## Installers for Windows and Linux ([#1](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/1))

An installer per platform, built by GitHub Actions and published as release assets, so
players do not have to follow the build steps in the README.

- Players still provide their own game files (the Games on Demand package, and DLC if
  they have it). The installer asks for the package's path and does what the README does
  by hand today: `tools/xcontent_extract.py` into the install directory,
  `tools/install_dlc.py` for DLC, `forza.toml` (public server or offline), and a
  shortcut. Windows: the Visual C++ 2015-2022 x64 redistributable as well.
- Starting points: `tools/package_linux.sh` and `tools/build_windows.sh` already make
  the packages in `forza-recomp/out/dist/{linux,windows}` (`tools/dist/` has the
  launchers and READMEs that go in them).
- Open: the binary contains the generated code, which is translated game code and is
  kept out of this repository (`tools/sync_generated_repo.sh` pushes it to a private
  one). Publishing a prebuilt binary as a public release needs a decision on that
  first; the alternative is an installer that runs codegen and the build on the
  player's machine from their own `default.xex`, which needs the toolchain and the
  ReXGlue SDK there.
- CI also needs the patched SDK (`tools/build_rexglue_sdk.sh`), the generated code (or
  codegen, which needs the game's `default.xex`) and, for Windows, the MSVC CRT and
  Windows SDK from `xwin` (Microsoft's license).

## More than 8 players in free roam ([#2](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/2))

Raise the Horizon World's cap of 8 players (`docs/online_vision.md`, "Session cap: 8").

- The session side takes more: served `MaxPlayers` 16, the host created 16 public slots
  and its lobby opened normally.
- Everything else the game makes is 8 (the custom game's MAX PLAYERS option, the party
  session's slots, Turn 10's playlist XML, the bucket's `MaxPlayers`, the co-op
  challenges). The question is whether the engine's per-player state holds more than 8:
  lobby player lists, the replicated cars and `CarsPerUniverse` (Tracks) are the places
  to read.
- Server side: 8 is also in the server, in the placement (one world per 8 players,
  `server/forza_live_server.py`), the served playlist's `MaxPlayers`
  (`server/forza_live/title_services/lsp.py`) and `MAX_PLAYERS` (event grids, `server/forza_live/world/world.py`).
  A broadcast counts once per recipient against the sender's relay budget
  (256 KB/s, `--rate-limit`, sized for a world of 8 at 240 fps), so a larger world needs a larger
  budget.
- Testing needs more than the 2 instances this machine runs.
