# DLC and Title Update 4

Status of the downloadable content (issues #5, #9, #16, #17, #18) and of Title Update 4
(#15). Newest findings first within each section.

## Title Update 4

### What the update is

`Forza Horizon (World) (v4) (4000D145) (Title Update)`: an STFS package, content type
`0xB0000` (`4D5309C9/000B0000/tu00000001_00000000`), media id `4000D145`, base version 12.
Files: `default.xexp` (2.8 MB), `XMediaFacade_default.xexp`, `SpeechFacade_default.xexp`
(delta patches, module flags `0x50`, compression type 3) and `media.zip` (10.9 MB).

The executable it updates (`0.0.0.12` -> `0.0.4.12`) is not byte-for-byte the one in this
project (`0.0.0.7`, media id `2B7A1346`): the patch's source signature hash does not match,
and the header delta copies bytes from a header laid out differently (security info right
after the optional header table, the header data back to back; this copy has 0x80 bytes of
zeros after the table, 16-byte aligned header data and a zeroed RSA signature). The code is
the same: with the header repacked to the original layout, the update's block hashes pass
and 370 of the 371 page groups of the patched image match the update's page digests. The
one that does not is `0x83610000-0x8361FFFF` (in `.edata`'s page, copied unchanged from
this copy's `0x83520000`: data, not code). The facades patch with every page group
verified.

### Applying it: `tools/apply_title_update.py`

    tools/apply_title_update.py BASE_XEX TITLE_UPDATE OUT_XEX

`TITLE_UPDATE` is the package, a zip holding it, a folder, or a bare `.xexp`. Writes the
patched `default.xex` (unencrypted, one "basic" block), the patched facade DLLs next to it,
and the update's other files under `OUT_DIR/update/` (the folder to mount as `update:`).
Pure Python (`tools/lzx_delta.py` is a port of libmspack's LZX decoder with a preloaded
window; AES uses `cryptography` or `openssl` when present). About 2 s.

How it checks itself: the patched headers must parse with consistent sizes, every patch
block must match its SHA-1 (so the image key is right), and the result is checked against
the XEX page digest chain (`SHA-1(pages of descriptor i + descriptor i)` is stored in
descriptor i-1, descriptor 0's in the security info's section digest). Digests the header
delta copied from the other copy's headers are rebuilt from the verified pages; ranges that
still fail are reported, and the tool refuses when one is in code.

Notes:
- ReXGlue's codegen already applies a `default.xexp` that sits next to the input XEX
  (`UserModule::LoadFromFile` looks for a sibling `.xexp`), but through
  `XexModule::ApplyPatch`, which (from Xenia) bounds the header records by the first
  record's length: right for `default.xexp` (one record), wrong for the facades' patches
  (several), and it fails on this copy's headers anyway. The pre-patched XEX avoids both.
- The base game already asks for `update:\media.zip` (Xenia's log: "ResolvePath(update:\
  media.zip) failed"). The SDK mounts `update:` from its `update_data_root` setting.

### Building it

    python3 tools/apply_title_update.py extracted/game/default.xex <TU package or zip> extracted/tu4/default.xex
    cmake -S forza-recomp -B forza-recomp/out/build/linux-tu4 -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_PREFIX_PATH=$HOME/rexglue-sdk-nightly/0.10.0.24/linux-amd64 \
      -DFORZA_TITLE_UPDATE=4
    FORZA_BUILD=linux-tu4 tools/build.sh
    FORZA_BUILD=linux-tu4 tools/play.sh

- `-DFORZA_TITLE_UPDATE=4` picks `forza_manifest_tu4.toml` (input `extracted/tu4/`, hints in
  `hints_tu4/`, output `generated_tu4/`; codegen rewrites `generated/rexglue.cmake` with
  the paths of the manifest that ran, so each build includes its own copy, made at
  configure time; after a TU4 codegen run git shows that file changed: `git checkout` it). Both
  versions build from the same tree into separate build folders; the base build is
  unchanged.
- Hints: `tools/tu_address_map.py` maps base addresses to TU4 ones (functions paired
  through both images' `.pdata`, aligned in order with difflib on normalized code; changed
  functions paired by similarity within their gap; data addresses through the lis/addi
  pairs of matched code). It ported every file of `hints/` to `hints_tu4/` (entries it could
  not map are commented out), then `tools/find_missing_entries.py` (+87) and
  `tools/find_orphan_functions.py` (+202) were run on the TU4 output until they found
  only what they find on the base (5 and 8 rejects). `hints_tu4/tu4_fixes.toml`: one
  function end (`sub_827611D8`) the scanner cut short.
- Hooks: sources name guest functions and addresses as in the base executable.
  `src/tu4_addresses.h` (written by `tu_address_map.py --cpp forza-recomp/src ...`)
  renames each `sub_XXXXXXXX`/`__imp__sub_XXXXXXXX` they use and maps each
  `FORZA_ADDR(0x...)` constant; `src/platform/guest_addr.h` includes it in a TU4 build.
  Entries marked "check" sit in functions the update changed; each was reviewed against
  the TU4 code (see "Online" below). Struct layouts the update changed are picked with
  `FORZA_TU4` (`guest_addr.h`).
- Runtime: the TU4 build mounts the patched files' folder as `update:` (the SDK's
  `update_data_root`, default `tu4` next to the game folder) and links
  `game:\default.xex` and the two facades to it. TU4 looks the facades' exports up
  again and again; the SDK made a new thunk for each lookup and ran out within a second,
  so `src/kernel/proc_address_cache.cpp` gives each (module, ordinal, caller) its first
  thunk again.
- Cost: codegen about 70 s, a full build about 15 minutes at -j4 on this machine.

### For the launcher

- `apply_title_update.py BASE_XEX TU_PACKAGE OUT_XEX` writes `default.xex`, both facades
  and `media.zip` into OUT_XEX's folder; that folder is the game's `update:`.
- Build with `-DFORZA_TITLE_UPDATE=4` from `forza_manifest_tu4.toml`, rewriting its
  `../extracted/tu4` paths (the three `file_path`s and `game_root`) to that folder. Not
  `forza_manifest.toml` with the patched XEX: the base hints are at base addresses.
- Run with `--update_data_root=<that folder>` (the default is `tu4` next to the game
  folder). `--game_data_root` stays the disc's files.

### Status

- TU4 reaches the main menu, which lists HORIZON RALLY next to Single Player and
  Multiplayer (the base has no such entry). With a profile without progress the game says
  "You must earn a yellow wristband in Horizon Single Player before you can enter Horizon
  Rally" (game rule). With a Xenia save, Horizon Rally loads its map and resumes the rally
  progress; its events and championships play to the end and are saved, without the
  co-driver's voice ("Horizon Rally" below); the free roam pause menu lists 1000 CLUB,
  which says "not available because you are not signed into Xbox LIVE" when offline;
  signed in (Forza Live server) it opens (tutorial, then the challenge cards); its
  challenges can be completed and are kept in the save, and the server has its leaderboard
  ("The 1000 Club" below).
- Saves: TU4 converts a profile save when it loads it (`ForzaProfile/VersionFlags` goes
  from 0 to version 4, flags 2; a `NeedsAA` file appears). The base build then refuses it:
  "MISSING TITLE UPDATE: Your profile save game requires the latest title update". Going
  to TU4 is one way: keep a copy of the profile folder to go back.

### Online (#20)

Checked 2026-10-14 with two TU4 games (DriverA, DriverB) and one base game (BaseC) on a
local Forza Live server, each with its own scratch copy of a Xenia career save (TU4
converts it) and its own `XDG_DATA_HOME` (the online identity file lives there).

What the update changed for the hooks, and the fixes:

- Mapping (`tools/tu_address_map.py`): the 13 "check" functions and 5 call sites were
  compared with the TU4 code (the generated code of both builds carries the disassembly,
  so a base function and its TU4 pair diff directly, branch targets mapped). Two were
  wrong, both paired by the code around them: the player list's constructor
  `sub_8247F018` (paired with `sub_824AE6B0`, a tree header's: 16-byte node, flags) and
  its clear `sub_828736E0` (paired with another list type's clear); TU4 has them at
  `sub_825A1EB8` and `sub_824B06A0`, the same code. The tool now places a function start
  the alignment left out by its callers: each call site in a matched function of the same
  length names the new target (a majority of at least 2, 80 %); such entries are marked
  "check" only when the code differs. The other 11 and the 5 call sites (each call site's
  call goes to the mapped function) are right; the changes in them: the launch passes a
  new flag to `sub_825548A0`, `sub_828786E8` checks one more condition per player, the
  session teardown `sub_82CF7278` skips its message pump when a field is clear. None of
  that touches what the hooks do. The hints (`hints_tu4/`) map the same with the new tool.
- The online game object grew: TU4 put 4 bytes in front of the game options (the preset
  at +0x1B8, the track +0x1BC, MaxPlayers/MaxAIPlayers +0x1D0/+0x1D1, AI difficulty
  +0x44E) and 8 at their end (0x2DC bytes, was 0x2D0), so everything after them moved
  (next round ready/options +0x490/+0x494, round counter +0x7F4, intermission +0x7FC/
  +0x800, the launch check +0x87C/+0x880). The fields before the options (mode, state, in
  game, players, host, launch time) and the vtable slots the hooks call did not move.
  Found by diffing the field offsets of every matched function in the online code
  (`0x82860000-0x828A0000`); `world_events.cpp` and `world_drop_in.cpp` use the TU4
  offsets in a TU4 build. Read with the base offsets, the world hooks would have taken
  every round for an event (preset 0) and written the intermission into other fields.
- Title services: TU4 asks for the playlists with `Hopper.GetListEx` (each hopper also
  has `requiredOfferId`, a u64 after `intermissionTimeMs`); with the server's empty
  answer the game showed only its default playlist ("The Forza Horizon service is not
  available"). The server answers it (no offer needed). A dump of TU4's service records
  from a running game (`server/forza_live/network/xtsw_all.py`) found the other 292 unchanged and 7
  new: `GetListEx`, `Challenge.ChallengeCompleted(carId u16, challengeThumbprint u32)`
  (the 1000 Club; no results), `SharedCar.CreateEx` / `GetClubCarsEx` (the car data with
  `hasRallyUpgrades`, rows with `contentId`), now handled like `Create` / `GetClubCars`.

Results (TU4 + TU4):

| Test | Result |
|---|---|
| Sign-in (Gold), party lobby, Find Game | playlists listed (after `GetListEx`) |
| Horizon World | B alone: the world started at once (free roam HUD); A was placed into B's world, JUMP IN, both in the world, each console's car list has the other's car at the other's position |
| Event (`/world/event`, event 4, 1 lap) | both to the event lobby: 2 humans + 6 CPU drivers, INTERMISSION from 30, class B cars; the race with 8 cars (each console runs 3 CPU drivers, sees the other's 3), A saw B's car and tag; CPU level Easy (2 of 8 human); after the race each console removed only its own CPU drivers, both back in the world's free roam; online race scores submitted, XP and credits given |
| Head-to-head | A drove up behind B (29 m), X: B got the prompt, accepted; both "race to the Wind Farm", GPS route on both minimaps; B's game closed: A "YOU WIN: DriverB left" |
| Rivals | GoPro Millennia Masters: Rivals screen (`GetRivalAndPersonalBest`, `GetBestScores`), Rivals race started and quit (`WriteRaceStatsEx`); a finished lap (a rival time) was not driven |
| 1000 Club, signed in | opens (tutorial, challenge cards, `Scoreboard.GetRows`), challenges begun in a car; a challenge completed and kept, the leaderboard (below) |
| Host leaves | when B quit, A became the world's host |

TU4 and base together: they can't play together. The base game placed into a TU4 world
joined the session and left it within 60 ms ("session teardown"), then hosted a world of
its own; a TU4 game placed into a base world did the same. No crash, but the next player
of either version could be sent into the other version's world and end up alone too. So a
TU4 build signs in to the server's room with "#TU4" added (`LiveRoom()`,
`src/kernel/identity.cpp`): matchmaking, the friends list and invites only see players of
the same version. Tested: base BaseC and TU4 DriverA each hosted their own world (no
cross-version join), TU4 DriverB was placed into DriverA's.

### Horizon Rally (#16)

HORIZON RALLY needs the Rally Expansion Pack (the package with `Media/1600.puboffer`, 1 GB).
Without it (#79; a copy of a player's save on Linux, TU4, the DLC folder without that package)
the main menu checks the marketplace instead (TU4 addresses: `sub_8279E4F8` runs the check
`sub_82753DB0`, then waits for the managers at `0x833F4CF8` and `0x833F4CEC`) and
`sub_827956F0` answers "XBOX LIVE MARKETPLACE ERROR: Failed to download required Xbox LIVE
Marketplace data", or "MARKETPLACE ERROR: There was not enough space for the marketplace save
file" while the marketplace manager is in state 3 (the failed save before b0daca0). With the
package (header or not) the same save starts the Rally intro and no check runs. The game logs
its DLC at start (`DLC: N packages in ..., offers ...`, `src/debug/dlc_log.cpp`) and warns
when a TU4 build has no offer 1600; the launcher warns at Play too.

Checked 2026-10-14/15 with the TU4 build, offline, a scratch copy of the Xenia career save
(rally progress: the Rally Heats event in progress) and scripted driving, at 30, 60 and
144 fps.

- The mode's data: `gamedb.slt` (TU4's media.zip) has the 7 championships (`Events`
  271-277, `MS_RALLY_01`-`07`, B to R class, 10,000-50,000 Cr), each of 4 stages
  (`MultiStageSeriesEvents` / `MultiStageSeriesRaces`: stages 1-3 are `RALLY_nA`-`nC`,
  stage 4 `RALLY_nD`, 8-12 km), and `RALLY_HEATS` (event 279, the qualifier in a given
  Impreza). Each stage is a `ColoradoDirt` track whose `RouteId` names the AI route in the
  Rally package (`media/AIOpenWorld/coloradodirt/Ribbon_00/route_NNN.owt`; a route can run
  on past the stage's finish).
- Driving: `tools/owt_path.py` makes an autodrive path from a stage's route,
  `tools/autodrive.py` with `AD_OPEN=1 AD_PATH_LOAD=<path>` drives it (a TU4 game's car list
  is at `0x833CAAE4`) and stops when the game holds the car after the finish. The car keeps
  within 1-4 m of the route at 25-30 m/s, with a few crashes at 34 m/s; it finishes last
  (50th, 1.5-2 times the winners' totals), which is enough to play everything but the
  final.
- **Played:** HORIZON RALLY from the main menu loaded the Rally Heats in progress (26 %);
  finished it (50th of 50: 45,000 Cr, the mileage and assists bonuses); HORIZON
  RECOMMENDS: bought the Lancer Evolution VIII MR (20,000 Cr), the rally conversion
  (8,514 Cr of parts, B 436 -> 497); then the rally hub. Three championships to the end,
  each stage after the other (start menu, countdown, HUD with progress, race clock, splits
  and the rivals around you, pace-note icons, finish gantry, STAGE RESULTS, RALLY
  STANDINGS, and after stage 4 RALLY RESULTS, each total the sum of its stages):

  | Championship | fps | Stages | Total |
  |---|---|---|---|
  | Beaumont (B, 10,000 Cr) | 30 | 3:43.312, 2:39.901, 5:08.718, 7:37.371 | 19:09.302 |
  | Montano Plains (A, 15,000 Cr; the game's automatic upgrade to A 597, 10,296 Cr) | 60 | 2:52.767, 4:31.712, 4:36.732, 7:41.341 | 19:42.552 |
  | North Plains (B, 20,000 Cr, night; "Illegal car": the game configured the Lancer back to B 499) | 144 | 3:26.160, 3:17.601, 4:43.562, 8:07.972 | 19:35.295 |

  After each, the hub shows the championship's best time and unlocks the next one (after
  North Plains: The Rockies, A, 25,000 Cr); "To Final" goes down by 10 points per
  championship at 50th (310, 300, 290, 280). No crash or hang in the 3 sessions.
- Timing: the race clock against the log's screenshot times: 60.335 s of clock in
  60.339 s at 30 fps, 60.333 in 60.354 at 60, 60.396 in 60.409 at 144 (94-144 fps in the
  stages).
- Saving: the career is written at the end of each championship and when quitting to the
  main menu (QUIT HORIZON RALLY); not between stages. After a restart the hub showed the
  best times, the unlocked championships, the credits and the car.
- **The co-driver speaks** (#41, checked 2026-10-15 in the Rally Heats at 30 and 144 fps;
  `FORZA_CODRIVER_LOG=1`, `src/debug/codriver_log.cpp`, describes the chain). Each pace note
  is queued (`CoDriver_EN/CoDriver/Distance` / `150Distance`, `.../Turns/Right` /
  `MedRight`...), FMOD finds every event (result 0), each plays for its 0.6-0.9 s at volume 1
  and applies the `RallyCoDriverPlaying` snapshot. In the audio the game submits
  (`FORZA_AUDIOLOG` per channel) the voice is in front left/right only, the cars mostly in
  the center and surrounds: over the 9 phrase groups of a minute of driving front left rose
  2.1 dB and the center fell 2.1 dB (the cars ducked to 0.75); with the co-driver's volume
  at 0 (Rally Co-Driver OFF) neither moved, and no snapshot. With every other volume at 0 or
  0.01 the voice alone comes out of the game's stream at -18 dB RMS, the level of the whole
  mix. So the voice is there, about as loud as the engine after the 5.1 to stereo downmix,
  and in a stereo recording it replaces the 2.5 dB the cars lose: the band level stays flat,
  which is why the ON/OFF recordings looked alike.
  - Its volume is the SFX volume (`Main/CoDriver` ON) or 0 (OFF), so SFX 0 silences it too.
- Logs: one XMA decode error in 3 hours ("ffmpeg: overflow ... in spectral RLE"), and
  ambient banks opened by bare name (`ResolvePath(AMB_Mountains.fsb) failed`, twice, in a
  North Plains stage), which other runs' logs show for the base game's banks too
  (AMB_FA_Autoshow, AMB_FA_Workshop): not checked whether an ambience goes missing.
- Not played: the other 4 championships and the final (it needs rally points that a
  last place does not give), the Service Area and the All Access Pass.
- **Kettle Hills stage 1 (#86, 2026-10-16):** a player's game hung there three times (the
  road ahead missing, then frozen: the resource loader looping in a zip member read, see
  docs/PROJECT.md). On Linux it played to the finish (Kettle Hills is the fifth
  championship, S class, 30,000 Cr; stage 1 is track 1129 `RALLY_016`, route 16, 5,021 m,
  from about 0.1 to 0.6 of the route). To get there without playing the four before it: the
  gamedb in a copy of the update folder's `media.zip` with
  `UPDATE Events SET PopularityPointsReq=0 WHERE Id IN (271,273,274,276,277)` (the zip may
  hold it stored, method 0), the Rally Heats driven to the end, the Lancer bought (pick a
  colour first, then A: "BUY CAR?"), the rally conversion, then the hub's fifth card; "RACE
  ANYWAY" skips the class upgrade. Swapping the Rally Heats' track instead
  (`Races.TrackId`) crashes its resume (read of guest 0x44): the save's Heats in progress
  belongs to its own track.

#### Kettle Hills short zip read (#86): a damaged package, not an extractor rule

The archive copy of the Rally pack (`4D5309C9/00000002/6F6992...4D`, 1,083,125,760 bytes, CRC-32
`5f8d11e1`, the same bytes the zip in the archive holds) extracts `Media/DLCZips/ColoradoDirt_pri_65.zip`
with 12 wrong 4 KB blocks (file blocks 22339-22350, data blocks 65865-65876). The STFS layout was
checked exhaustively: every one of the 262,866 data blocks was mapped with the read-only hash-table
skipping and compared with the SHA-1 in the package's own level-0 hash table, and exactly those 12
blocks fail. The correct data (the zip of a player whose copy works matches the table's SHA-1 for
all 12) does not occur anywhere in the package (no aligned or unaligned match), so no block mapping,
table choice or chain rule can recover it: the 12 blocks of this copy are damaged. The other 451 files
of the package extract identically to the known-good install. `xcontent_extract.extract` now
returns the file blocks that fail their hash and `install_dlc.install` (so the launcher's log)
prints a `WARNING ... damaged 4 KB block(s)` line naming the file. The fix for a player is a
different copy of the package; extractions made earlier cannot be told apart without that check,
so a pack added before this update has to be added again from a good copy.

The extractor also verifies the last block of a file whose size is not a multiple of
4 KB. STFS hashes the complete stored block, including padding beyond the declared
file size. Earlier extraction skipped that partial block, so corruption in its payload
or padding was not reported. Verification now reads the stored padding without adding
it to the output; a truncated stored block fails with a short-read error. Synthetic
single-block and multi-block packages cover damaged tails, damaged padding, intact
extraction and truncation. This closes a validation gap; it does not establish the
source of the distinct compressed-data mismatch reported in #327.

Package entry paths are checked before creating an output file or directory. Extraction
rejects absolute paths, parent traversal, Windows drive/stream syntax and existing links
that resolve outside the chosen output folder. This applies to STFS and SVOD extraction;
valid nested paths retain their package layout. Synthetic-package tests verify that a
parent-path entry cannot overwrite an existing sibling file. Link tests use symlinks or
Windows junctions when available.

#### The same bad copy in #327 and #422, and checks that see it in an extracted folder

Both players crash about halfway through Kettle Hills stage 1, and both logs have the same
`ZIPREAD` line just before the fault: `32768 of 81792 bytes out, input 28426 of 28426 read`,
compressed-input CRC-32 `e4eba1e0`, and the same 14 CRCs per 2 KB. The member is
`coloradodirtout.09183.rmb.bin`, whose data starts at 0x5742A8A of `ColoradoDirt_pri_65.zip`
(file block 22338 + 0xA8A). The archive
copy's 12 wrong blocks (22339-22350) cover the member from its byte 1,398 to its end, and the
zip extracted from that copy (SHA-256 `80ea0f61...`, the one of the #86 player) has exactly
`e4eba1e0` and the same 14 CRCs there; the intact zip has `37af238d`. So both players have
this bad copy, from its package or from a Xenia content folder made from it (Xenia does not
check the blocks either). The intact zip is 484,262,732 bytes, SHA-1
`f0ce7054a11f0128e7fb5b6eccae9405768d543a`; every block of the installed intact pack matches
the archive copy's own hash table, which is intact.

Reproduced on Linux (TU4 build, offline, a copy of the #327 save, which opens on the rally
hub with Kettle Hills, the Impreza, `tools/autodrive.py` with `AD_OPEN=1` on
`tools/owt_path.py`'s path of `route_016.owt`; `FORZA_BENCHMARK_AI=1` made a driver but the
car did not move in a rally stage): with the archive copy's zip, at 60 % (route point 985) the
same `ZIPREAD` line, CRC `e4eba1e0`, then within 0.3 s reads of guest 0x8 and 0x170 on two
threads and the game ends. With the intact zip the stage finished (50th), no `ZIPREAD`.

What #422's fault is: the read ends short (the hook of #86), the resource loader goes on
with a 32 KB resource of garbage, writes past it (a write fault at guest 0x2EB3A000 on the
loader thread 5 ms earlier), and the main thread then calls a virtual function of an object
whose vtable pointer it overwrote: TU4 `sub_82414068` at 0x82414234, `lwz r11,68(r11)` in
the loop over the list at +60/+64 that calls vtable slot 0x44 (registers: r3 = 2EA0C960 the
object, r11 = 0, lr = 82414240 after the previous call). The address read (0x44, 0x8,
0x170...) depends on what got overwritten; the cause is the member. No change in the game
can bring the missing bytes back.

Checks (`launcher/forza_launcher/dlccheck.py`):

- `launcher/forza_launcher/data/dlc-4D5309C9.json`: size and SHA-1 of every file of the 12
  packages whose package files are at hand (1,107 files: Rally, 1000 Club, 10 car packs),
  written by `tools/dlc_manifest.py`, which checks every 4 KB block of an extracted folder
  against the package's own hash table (with the stored padding for the last block), so a
  damaged package copy can still vouch for a good folder. Plus `known_damage`: the 12 blocks
  of the Rally zip with the SHA-1 of the right bytes and the bad stream's CRC.
- Install DLC from an extracted folder (Xenia's content folder) hashes each file while it
  copies it (one read) and compares: a damaged, short or missing file gives a `WARNING`
  line naming it, like a package whose blocks fail. Either way the result is shown in
  orange with a "Damaged DLC" dialog (it used to be the log only). The pack is still
  installed: the rest of it works.
- Play checks the installed known packages: every file's size, and the known damaged region
  (a 48 KB read), so a bad Rally pack installed before this check is named in the Play
  notes ("Your Rally Expansion Pack is damaged: ... (Kettle Hills stage 1 of Horizon Rally
  crashes about halfway)").
- A crash report whose log has a `ZIPREAD` short read gets a "Damaged game file" section,
  naming the Rally pack when the CRC is `e4eba1e0`.
- `launcher/test_dlc_integrity.py`.

### The 1000 Club (#17)

Checked 2026-10-14 with the TU4 build, a scratch copy of the Xenia career save (free roam
in the Lamborghini Aventador LP700-4, car 1398) and a local Forza Live server.

- The challenges are the 1000 Club package's (`2700_pri_65` zip:
  `media/carchallenges/carchallenges_<carId>.xml`, 218 cars, 1145 challenges; TU4's
  `gamedb.slt` has their counts and titles). The Aventador's: ONE EIGHTY SKILL (any
  grade), 4 great near misses, 190 mph for 10 s, a skill road trip, 5 trash cans in 30 s,
  a festival event.
- Flow: pause menu, 1000 CLUB: the tutorial ("Select the My First Challenge achievement"),
  the eligible garage cars, BEGIN CHALLENGES IN THIS CAR; back in free roam the HUD lists
  the car's challenges (1-6, "1000 Club Info"), D-pad up shows CURRENT CAR CHALLENGES.
- **ONE EIGHTY SKILL completed** with scripted input (e-brake and full lock from 20 m/s
  until the car had turned about 150 degrees, then throttle with a little counter-steer;
  the car stopped turning near 180 and drove off the other way; it took one or two tries in
  an open field). The HUD's challenge 1 turned into a medal, and the game sent
  `Challenge.ChallengeCompleted(carId 1398, challengeThumbprint 0xAE6828D3)` and wrote
  `ForzaProfile`. The 1000 CLUB screen then moved on to the next achievements (14 and 20
  medals) and its LEADERBOARDS showed the 1000 Club board (`GetRows` type 5, board 1).
- **Saving:** the medal is kept with the career, which the game saves when leaving single
  player (pause menu, QUIT: `ForzaProfile` and `PlayerDatabase` written). Quit that way and
  restarted: CURRENT CAR CHALLENGES showed the medal on ONE EIGHTY SKILL. Killed right
  after the medal instead (only the `ForzaProfile` write at the medal): after the restart
  the medal was gone (the tutorial's progress was kept), and doing the challenge again
  sent the same `ChallengeCompleted`. So, as for the rest of the career: quit to the title
  screen before closing the game.
- **The leaderboard, two players** (two TU4 games, DriverA and DriverB, each with a fresh
  copy of the save, one server): each did the ONE EIGHTY SKILL (B first); the server kept
  both `ChallengeCompleted`. 1000 CLUB, LEADERBOARDS showed "1 DriverB 1 (medal)
  10/15/2026, 2 DriverA 1 (medal) 10/15/2026", "YOU ARE IN THE TOP 100% OF 2 PLAYERS" on
  A and "TOP 1%" on B; X went through Favorites, Near Me and Global (views 3, 1, 0), same
  rows. The score column is the medal count, the date the latest medal's (UTC). The
  server side is in docs/title_services.md (scoreboards).
- **Speed trap and speed zone challenges (#564):** a car's `carchallenges_<carId>.xml` can
  hold a `speed_camera` element with a `type` (`trap` or `zone`), a `name` that ends in the
  board id (traps 201-230 of `Colorado/speed_camera.xml`, zones 101-112 of
  `average_speed.xml`) and `min_speed` in mph. The game fires one event per trap pass (the
  speed at the camera, in mph) and one per completed zone (the average speed over the
  stretch, in mph); each carries the type and the board id, and a challenge
  (`CarChallenge::CSpeedCameraTrigger`, handler sub_824FD2C8 in TU4, event sub_8295D038)
  counts it only when both match and the speed reaches `min_speed`. So a pass of the Rt. 27
  Speed Trap (227) never counts for a challenge on the Rt. 27 Speed Zone (109), however
  fast, and the game words such a challenge "Set a speed above 60 mph at the Rt. 27 Speed
  Zone" (the 2012 MINI John Cooper Works Coupe's fifth). Checked 2026-11-02 with a TU4 game
  and a local server (a car's challenge file swapped for a test one; gdb breakpoints on the
  two functions): a trap pass at 35 mph completed a trap challenge with `min_speed` 30 and
  was ignored by the zone ones; driving the zone at a 76.7 mph average completed both zone
  challenges (60 and 30), and the server got a `ChallengeCompleted` for each.

## Every package (#18)

The 23 installed packages (`tools/install_dlc.py`), and what the game answers for their
cars (`FORZA_DLC_LOG=1`, `src/debug/dlc_log.cpp`: each IsCarInstalled /
IsCarInstalledAndPurchased / IsCarOnDisc answer is logged once). Car lists come from each
package's database patch (`media/db/patch/*_merge.slt` in its DLC zips); single-car
packages map their offer to a car of the disc's database (`ContentOffersMapping`).
Measured 2026-10-14 with all 23 installed, signed in to the Forza Live server: base build
with the normal profile, TU4 build with a Xenia save, each into free roam.

| Package | Offer | Cars (media names) | Base | TU4 |
|---|---|---|---|---|
| 1000 Club Expansion Pack | 2700 | 2: RUF_CTR2_95, FOR_F100_56 | installed | installed |
| 2010 Ferrari 458 Italia | 1002 | 1: FER_F142_10 | installed | installed |
| 2010 Nissan 370Z | 1004 | 1: NIS_370Z_10 | installed | installed |
| 2010 Volkswagen Golf R | 1005 | 1: VW_GolfR_10 | installed | installed |
| 2011 Mercedes-Benz SLS AMG | 1001 | 1: MER_SLSAMG_11 | installed | installed |
| 2012 Aston Martin Virage | 1003 | 1: AST_Virage_12 | installed | installed |
| 2013 Ford Mustang Boss 302  - Pizza Hut Edition | 2800 | 1: FOR_MustangBoss302_PZA_13 | installed | installed |
| 2013 Ford Shelby GT500 - Rockstar Energy | 1300 | 1: FOR_ShelbyGT500LCE_13 | installed | installed |
| April TopGear Car Pack | 2600 | 6: FOR_TransitSSV_11, BOW_NemesisEXR_12, ALF_ZagatoTZ3Stradale_12, BMW_M3GTR_HZN_05, AST_Vanquish_13, MER_CLKGTR_HZN_98 | installed | installed |
| December IGN Car Pack | 1500 | 7: AST_DBS_09, AMC_JavelinAMX_71, MCL_F1_93, FER_F12_13, FOR_GTX1_06, HUM_H1AlphaOpenTop_06, FOR_SVTRaptorHALO_12 | installed | installed |
| February Jalopnik Car Pack | 1900 | 7: NIS_510_70, DEV_GTX_10, FOR_CountrySquire_66, MAZ_RX3_73, TOY_FJCruiser_12, SUB_BRZ_13, FER_512TR_HZN_91 | installed | installed |
| Honda Challenge Car Pack | 1800 | 3: HON_CivicSi_13, HON_HPDRallyFit_10, HON_CivicSi_86 | installed | installed |
| January Recaro Car Pack | 1700 | 7: MER_SLR_05, PAG_ZondaCinque_09, FOR_Mustang_95, LEX_LFANurburgring_12, LOT_ExigeS_12, GMC_Vandura_83, CAD_Escalade_12 | installed | installed |
| LCE: Day1 DLC Pack | 1200 | 11: CHE_BelAir_57, FOR_MustangBoss302_13, AUD_LCESportQuattro_83, VW_LCESciroccoR_11, FOR_LCESVTRaptor_12, MIT_LCELancerEvoXGSR_08, DOD_LCEChargerSRT8_12, LAM_AventadorJ_12, ALF_8CSpider_10, FER_599XXEvolution_12, GUM_ApolloEnraged_12 | installed | installed |
| March Meguiar's Car Pack | 2400 | 7: PEU_206RC_04, JOS_JT1_10, BMW_M135i_13, FER_360Challenge_HZN_03, NIS_R390_HZN_98, AUD_RS4Avant_13, MER_G65AMG_13 | installed | installed |
| November Bondurant Car Pack | 1400 | 7: SHE_Cobra427_65, NIS_240SX_94, JEE_Wrangler_12, BMW_M3GTS_10, LAM_GallardoLP570Spyder_12, SHE_1000_12, FOR_ShelbyGT500LCE_13 | installed | installed |
| October Car Pack | 1100 | 11: CHE_BelAir_57, FOR_MustangBoss302_13, AUD_LCESportQuattro_83, VW_LCESciroccoR_11, FOR_LCESVTRaptor_12, MIT_LCELancerEvoXGSR_08, DOD_LCEChargerSRT8_12, LAM_AventadorJ_12, ALF_8CSpider_10, FER_599XXEvolution_12, GUM_ApolloEnraged_12 | installed | installed |
| Pre-Order Car Pack | 2500 | 5: NIS_370Z_10, FER_F142_10, MER_SLSAMG_11, VW_GolfR_10, AST_Virage_12 | installed | installed |
| Rally Expansion Pack | 1600 | 16: FOR_FocusSVT_03, SUB_WRXSTi_05, MIT_LancerEvoVIIIMR_04, FOR_EscortRSCosworth_92, LAN_037Stradale_82, TOY_CelicaST185_90, SUB_WRXSTi_05, AUS_MiniCooperS_65, TOY_Sprinter_85, PEU_205T16_84, LAN_DeltaS4_86, AUD_RS4_06, FOR_RS200Evolution_87, LAM_LP7004_12, ULT_GTR_12, FER_FXX_05 | installed | installed |
| Season Pass | 206931968 | 0:  | - | - |
| Season Pass: 2006 Lamborghini Miura Concept | 1450 | 1: LAM_Miura_06 | installed | installed |
| VIP Membership & Cars Pack | 1006 | 5: PAG_Huayra_12, LAM_SestoElemento_11, RUF_Rt12R_11, KOE_Agera_11, AUD_R8GTSpyder_12 | installed | installed |
| VIP Membership & Cars Pack | 2100 | 5: LAM_SestoElemento_11, KOE_Agera_11, PAG_Huayra_12, RUF_Rt12R_11, AUD_R8GTSpyder_12 | installed | installed |

- Every DLC car counts as installed on both builds, the Rally and 1000 Club cars too
  (the base merges their database patches; TU4's own `gamedb.slt`, in its media.zip,
  already has the 15 Rally cars and the 1600 and 2700 offers). What the expansions add
  besides cars is TU4 code: the Horizon Rally mode and its map (#16) and the 1000 Club
  (#17) exist only in the TU4 build.
- The 12 packages of the second DLC set (1000 Club, Rally, the car packs, VIP) are byte
  for byte the installed ones; `install_dlc.py` needs no change.
- Not checked one by one: each car in the Autoshow and garage (model, audio, thumbnail).
- The failed `DLCRoot` lookups in the log (#5) are the game probing every package for
  optional files (`DLCRootN:\media\dlczips\stringtables.xml`, per-zip
  `media\db\patch\`, `effects\`, `dynamicpost\...` folders): not errors.

## Missing DLC content message (#9)

The text is `TitleUpdate2.str` 0x44E6: "You have cars in your garage that are missing DLC
content. These cars will be unavailable until you re-install the content or connect to
Xbox LIVE." (`Main.str` has an older variant). So it is a garage check (cars whose content
is not available), not a license check of packages. The string ids around it in the
executable: `IDS_MissingContent_DLC`, `IDS_MissingContent_Disc2`, `IDS_GarageMissingContent`
(`0x8209C8EC`).

(in progress)

How it is decided (base addresses): the profile load handler `sub_827A1CD0` shows it
(`TitleUpdate2`, id `0x83310F54` = `IDS_GarageMissingContent`) when a vtable check on the
profile is false and `sub_82500AD8(profile)` is true. That runs
`SELECT COUNT() FROM <profile>Career_Garage WHERE IsCarInstalled(CarId)=0 AND
IsCarOnDisc(CarId)=0` on the game database. The SQL functions are registered in
`sub_8250B9xx`: `IsCarInstalled` = `sub_82502DD8`, `IsCarInstalledAndPurchased` =
`sub_82502E18`, `IsCarOnDisc` = `sub_824F1A80` (both installed checks go through a cache,
`sub_82502CB8`). So the message means: the save's garage holds a car whose content is
neither on the disc nor installed.

Not reproduced (2026-10-14): base build with the normal profile and with the Xenia save,
signed in or not, START pressed at once or after the attract screens; TU4 build with the
Xenia save. Every IsCarInstalled answer was 1 (`FORZA_DLC_LOG=1`). The message can only
come from a garage car the game does not count as installed: a car from a package that is
not installed in the user data folder the game uses (`<user data>/0000000000000000/
4D5309C9/00000002`; `play.sh --xenia` and the launcher link it into the Xenia copy), or a
package installed without its header (`Headers/00000002/<name>.header`). With
`FORZA_DLC_LOG=1` the log names the car: a line `IsCarInstalled(id N, type 1) = 0`.
