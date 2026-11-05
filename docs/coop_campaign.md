# Co-op campaign (#309): feasibility

The idea (#309): friends in a Horizon World party play the festival's single-player events
together, with CPU drivers filling the grid, and each player's career gets the result, as in
the newer Horizons. This is a study, not an implementation: how a campaign event is defined
and started, whether the online event flow can run one, how the career records a result and
how it could be granted to every participant, the differences between the base game and
Title Update 4, the risks, and a plan.

Method (2026-10-23): the executables' code was read in the recompiler's output (the
generated C++ carries every function's PowerPC disassembly: `forza-recomp/generated/` for
the disc build, `generated_tu4/` for TU4), with string, cross-reference and RTTI scans of both
images (`tools/xrefs.py`, `tools/xex_image.py`). Ghidra's headless decompiler stopped at the
register-save helpers of these functions, so the disassembly was used instead. Data: the
game's `media/db/gamedb.slt` (disc) and TU4's (in `media.zip`), the flow and settings XML in
`gamemodes.zip`, `gametunablesettings.zip` and TU4's `media.zip`, and a scratch copy of a
TU4-converted career save decoded with the launcher's own codecs. No game was run: the
machine was busy with another game session and did not have the memory for two more
instances; the measurements still needed are listed under "Open questions". Addresses are
TU4's unless marked "base"; the table at the end has both where they were looked up.

## Summary

1. **A campaign event is database rows plus a race mode, and the race mode is the same
   GameOptions block online rounds use.** `Events` (class, AI skill per difficulty, prize,
   level, grid position, number of drivers), `Races` (track, laps), `EventParticipants` (the
   named CPU drivers and their cars), `EventRestrictions` (make, year, drive and so on).
   `CareerEventTypes.RaceModeId` picks a mode of `CareerRaceModes.xml`, whose `<GameOptions>`
   are parsed by the same function as the online playlists' XML (base `sub_8260A4D8`, TU4
   `sub_826B0DF8`). Started from the world (an activity's trigger zone, `CLoadIntoCareerRace`),
   it runs as race type 3, `single_player_career_race`; an online race is type 6.
2. **Yes, an online round can carry a campaign event's parameters.** The Horizon World's
   events already do (track, laps, class, CPU drivers; 11 of 11 clean two-console runs in
   #67). The server can also send the grid order, end condition, traffic, time of day, field
   size and the full car restrictions: the bucket's `CarRestrictions` takes every dimension
   `EventRestrictions` encodes, and the online AI fill picks CPU cars under that filter. The
   per-event AI skill needs a client hook, and so would the named grid. Showcases, Nemesis
   races and the Headline race can't run this way.
3. **The career result lives in the save's `PlayerDatabase` (`Career_EventResults`,
   `Career_RaceResults`, `CareerEventUnlocks`, `CareerHubUnlocks`) and in `ForzaProfile`
   (`Main/XP`, `Main/WristbandLevel`, `Main/Credits`).** It is written by the career
   component's post-race update, and online races (type 6) don't create that component, so
   an online event changes no career state today. All three ways to grant it are possible:
   - **In game**: call the game's own writer from a hook. This needs the most reverse
     engineering.
   - **A server message**: the Message Center can only give credits and cars.
   - **The launcher writes the save after the session**: the launcher's codecs already do
     this (a grant on a scratch save round-tripped cleanly, MAC, AES and SQLite integrity
     included).
4. **Base vs TU4:** the career data and save tables are the same; TU4 only adds Horizon
   Rally's (`MultiStageSeries*`, `RallyScoring`, `Main/RallyXP`). Code addresses and object
   offsets differ (online game +4, the career component's post-race slot moved from +0x28 to
   +0x30), so hooks need both address sets. Base and TU4 players never meet online (#20).
5. **The risks:**
   - CPU drivers that are far too strong: the online "Easy" is harder than the career's Pro.
   - Results trusted from peer-to-peer clients.
   - Players at different wristband levels.
   - Double rewards (online XP and credits on top of career credit).
   - Save writes. These are low for the launcher, which already backs up, verifies and
     replaces atomically, and higher for writes made in the game from a hook.

**Recommendation:** run campaign events as Horizon World event rounds (option A below) in a
co-op world for friends. The server keeps a ledger of results, and the launcher applies a
player's results to their save after the session. The first milestone: two players race a
festival event the host picked, with its real settings and CPU drivers, and only the host's
career advances. A later in-game grant (option B) can use the same ledger.

## What a campaign event is

### The data

`Events` (119 rows on the disc, 155 with TU4's Horizon Rally events; the 119 are unchanged
in TU4), by `CareerEventStyle`:

| Style | Events | What | Unlock |
| --- | --- | --- | --- |
| 1 | 70 (`CareerTypeId` 1, the point-to-point mode, 28; `CareerTypeId` 2, the circuit mode, 42) | festival races, `Level` 0-6 = the wristband tier | per level |
| 2 | 30 (`CareerTypeId` 4) | street races, with traffic | `UnlockPointsReq` (career XP), 3 hubs |
| 3 | 10 (`CareerTypeId` 7, 8) | showcases (single make, airborne vs a plane) | `PopularityPointsReq` |
| 5 | 7 (`CareerTypeId` 11) | Nemesis races, 1 CPU driver | XP 220, 750, 1450, 2500, 4100, 6400: the wristband thresholds |
| 6 | 1 (`CareerTypeId` 12) | the Headline race | `UnlockPointsReq` 10000 |
| 4 | 36 (TU4 only) | Horizon Rally | rally XP |

The columns a race needs:

- `TargetClass`, `PlayerCarId`, `PlayerCarTargetClass`.
- `NumberOfDrivers` (7 CPU drivers; 1 for Nemesis races).
- `PlayerGridPosition` (8: the player starts last) and `NemesisGridPosition`.
- `AISkill{Easy,Med,Hard,Pro}`, `AITemperament*`, `AIRubberband*`, `UpgradeAI`.
- `TimeOfDayStart`/`End`, `CrowdSize`, `CrowdExcitement`.
- `CashPrize`, `CreditsMultiplier`, `BonusId`.
- `HorizonEventID` (the world activity, e.g. `FR05`) and `HubId`.

Related tables:

- `Races` holds the track and laps of each race of an event (every disc event has one race).
- `EventParticipants` holds the grid's CPU drivers: `AIPlayerID` (a named driver), the car
  (`Ordinal`), colour, livery and tuning level. Event 4 has 7.
- `EventRestrictions.Value` is hex: a 32-bit field mask, a byte, then a value per set bit. For
  example `00030000 04 000007A8 000007B1` is the years 1960-1969, and `00000800 04 0002` is a
  2-byte id. 13 distinct masks occur.
- Rewards come from `WristbandScoring` (points per wristband level and place: level 0 gives
  100/90/.../30, level 6 gives 250/210/.../70), `EventScoring` (credits per place),
  `Rewards_EventPrizes` (a car for 17 events, all of them showcases and Nemesis races: see
  "Prize cars"), `Rewards_Wristband` and `Rewards_EventUnlock`.
- Wristband levels are thresholds on career XP (`CareerWristbandLevels`: 0, 220, 750, 1450,
  2500, 4100, 6400).

The AI skill columns, like `MPAIDifficultyMap`'s, are row ids of `AISkills`, and a lower id
is a stronger driver (cornering maximum 1.25 at id 1, 0.56 at id 76). Event 4 uses 76/56/40/23
for Easy/Medium/Hard/Pro. The online map uses 20/16/6/1. So today's online "Easy" CPU drivers
(id 20: cornering 0.95-1.00) are stronger than event 4's Pro (id 23: 0.90-0.97).

### Race modes: the same GameOptions as online

`CareerEventTypes.RaceModeId` picks a `<Mode>` of `gametunablesettings/CareerRaceModes.xml`.
Each mode is a `<GameOptions>` block:

- Festival point to point (2) and circuit (3): `UseTraffic="false"`, `GridOrderingType=
  "PIWithPlayerLast"`, `EndConditionOptions Type="NumLaps"`.
- Elimination (4).
- Street point to point (5): traffic on, `Type="Checkpoint"`, `WinningsDistribution=
  "TopThreeFinish"`.
- Showcase (7, 8, 9, 14), Mission (10; TU4 changed it to `RallyGridOrderingMode`), Nemesis
  (12) and Headline (13, `WinnerTakesAll`).

The parse (base `sub_8260ABF8`, TU4 `sub_826B15A0`) hands each block to the GameOptions
reader (base `sub_8260A4D8`, TU4 `sub_826B0DF8`). That is the function
docs/online_vision.md found for the hopper XML, and in TU4 the same reader also loads
`GameOptionPresets.xml` (`sub_827C7958`), the default playlists (`sub_8292C838`,
`sub_8292E018`) and the service's options (`sub_82919FC0`). Its attributes include
everything the event needs: `TrackId`, `MaxAIPlayers`, `AIFieldId`, `AIDifficulty`,
`UseTraffic`, `HorizonTimeOfDay`, `TimeOfDayIndex`, `CrowdSize`, `CrowdExcitement`,
`WinningsDistribution`, `StartingGridOptions`, `EndConditionOptions` and the buckets with
`CarRestrictions`.

### How one starts

1. In single-player free roam, every event is an activity of `career_event_activations.xml`.
   An `ActivityCareerEventActivation` named after the `HorizonEventID` has a `TriggerZone`
   (object `FR05`, radius 25) and a state that runs `CLoadIntoCareerRace event_id="FR05"`.
   The online free roam flow (`game_multiplayer_freeroam_flow.xml`) has no activities.
2. `CLoadIntoCareerRace` (vtable `0x820928C4`, enter `sub_829D0D90`) calls the event setup
   `sub_825C54B0(this, eventId)` (base `sub_82553ED0`). The setup:
   - sets the race type to 3 (`sub_82689D40(*(0x833ED710), 3)`);
   - stores the event id in the "current career event" global `0x833ED728` (base
     `0x832FEBB8`);
   - reads `SELECT * FROM Events WHERE Id=%i`: the player car and class, the restrictions
     (`sub_826B17D0` -> `sub_826B0D68`), `Driver01`/`02`, `UpgradeAI`, `NumberOfDrivers`, the
     participants (`sub_826B1C30`) and `NemesisGridPosition`.
   `CSetCareerEventId` (enter `sub_829FE1E0`) keeps the id and the `HorizonEventID` at
   `*(0x833CAACC) + 0x388` / `+0x398`.
3. `CGameTypeDecision` (TU4 update `sub_829BC9F0`, base `sub_82909A38`) maps the race type to
   a flow: 3 `single_player_career_race`, 6 `multi_player_race`, 8 replay, 12 time trial,
   17 single-player free roam, 18 multiplayer free roam, and in TU4 19/20 rally free roam/race.
4. `game_start_career_race` runs `determine_event_type_preset` and goes to the festival,
   street, showcase, Nemesis or Headline race flow (`game_*_race_flow.xml`).
5. The festival flow ends with the career screens and their states: the earnings
   (`ShowEarningsBreakdownScreen`), the career points (`ShowPostRaceCareerPointsScreen`), the
   reward car, `CPostRaceUpdateProfile` and the unlocks (`CTriggerEventUnlocks
   trigger_type="race_completion"`). The multiplayer race flow has `CPostRaceUpdateProfile`,
   `CShowPostRaceXPScreen` (online XP) and `CSaveProfile`, but no unlocks or career points.

## Running a campaign event online

### What the world event sends today, and what is missing

`server/forza_live/world/world.py` picks from the 70 style-1 events (tracks with `VisibleInMP = 1` and
ribbon 0 or 2) and answers the host's `GetGameOptions` with one bucket.

| Event setting | Campaign | World event today | Carried by the XML |
| --- | --- | --- | --- |
| Track, laps, circuit / point to point | `Races`, ribbon | yes | yes |
| Class | `TargetClass` | yes (`CarClassId`) | yes |
| Make, year, drive, ... restrictions | `EventRestrictions` | no | yes: `CarRestrictions` has `YearMin/Max`, `MakeId`, `ModelId`, `CountryId`, `RegionId`, `DriveTypeId`, `CarTypeId`, `BodyFamilyId`, `EngineConfigId`, `AspirationId`, PI, power, weight, ... (parser `sub_826A8E88`); the mask bits must be mapped to them |
| Field size | `NumberOfDrivers` (7) | `MaxAIPlayers` 7 | yes |
| Grid order | `PIWithPlayerLast` | `Random` | yes; untested online with several humans |
| End condition, traffic, payout split | race mode | `NumLaps`, no traffic | yes |
| Time of day | `TimeOfDayStart` | random | yes (`HorizonTimeOfDay`) |
| CPU skill | per event and difficulty | by human share, `MPAIDifficultyMap` | no: a hook (below) |
| The named CPU drivers and their cars | `EventParticipants` | random names, cars of the bucket's class | no: `AIFieldId` might select a field (untested); otherwise a hook |
| Nemesis / rivals | `NemesisGridPosition`, `RivalsBeaten` | none | no |

The online AI fill picks each CPU driver's car with the bucket's restriction filter (base
`sub_828920F8`: `SELECT Id FROM Data_Car AS Garage WHERE <restriction> AND Id=...`). With
the event's restrictions in the bucket, the CPU cars follow them too, and so do the players'
cars (SELECT CAR swaps a car that doesn't qualify).

**Per-event CPU skill (client).** The world event hook already rewrites every CPU driver's
level after each AI fill: base `sub_82877470` -> `sub_82568BD8` stores the level at driver
`+0x25B` and the skill/temperament row ids at `+0x260`/`+0x264`. For a campaign event it can
write the event's `AISkill<level>`/`AITemperament<level>` instead, at the host's chosen
difficulty or a fixed Medium. Today's rule (more humans give stronger CPU drivers) would then
pick the column. The career reads those columns in `sub_826B0170`.

**What doesn't fit:**

- Showcases (scripted opponents, mostly tracks not offered online).
- The 7 Nemesis races (ribbon 6, `VisibleInMP = 0`).
- The Headline race (special flow).
- Street races (ribbon 6 routes with traffic) need a test: their tracks are `VisibleInMP = 1`,
  but the server excludes ribbon 6 today and online traffic is unknown.

So the first scope is the 70 festival races.

### Picking the event and keeping it among friends

- **The world.** The Horizon World places everyone into the fullest world, so strangers would
  be taken to the event too. A co-op world needs a placement the server controls: a separate
  playlist ("Co-op Festival", hopper 20 as built) whose search only finds sessions hosted by a friend
  (`forza_live/social/friends.py`), or a flag on the world. This is a server change only.
- **The pick.** The host has to choose the event. A WORLD chat command (`forza_live/social/chat.py`
  already relays the world channel), such as `/event FR05` or a short list from `/events`,
  is a server-only first version. It sends the existing `WORLD_EVENT` path with
  `&event=<id>`. The in-world version turned out simpler than a prompt of our own: the
  game's own event markers can be loaded into the online world, and their map card starts the
  event (see "Milestone 4: events from the map").

## How the career records a result

### Where it is stored

The decoded scratch save (a Gold-wristband TU4 career: all 89 entered events at place 1)
holds:

- **`ForzaProfile`** (the prefix the launcher's profile editor reads): `Main/XP` 12480,
  `Main/WristbandLevel` 6, `Main/Credits` 2,471,887. Next to them are the counters a race
  updates (`Main/NumRaces`, `NumVictories`, `NumPodiums`, `TotalWinnings`,
  `TimeInFestivalRaces`, ...) and, separately, `Main/OnlineXP`, `Main/OnlineWristbandLevel`
  and `Main/NumOnlineRaces`.
- **`PlayerDatabase`** (the SQLite database the launcher's garage editor reads; its empty
  schema is `NewProfile_*` in `gamedb.slt`):
  - `Career_EventResults(EventId, Place, RivalsBeaten)`: 89 rows;
  - `Career_RaceResults(RaceId, Place)`: 89;
  - `CareerEventUnlocks(EventId)`: 110;
  - `CareerHubUnlocks(HubId)`: 3;
  - `Career_AIHistory` (the named CPU drivers' records), `Career_Garage` (per car `NumRaces`,
    `NumVictories`, `TotalWinnings`), `ChangeTracking` and `GameplayAggregate`.

### The code path

1. **`CPostRaceUpdateProfile`** (vtable `0x8209382C`, enter `sub_829DCB88`) runs in both the
   festival and the multiplayer race flows. It calls `sub_825858F8`, which calls the post-race
   virtual function (`+0x30`) of every profile component.
2. **`CCareerComponent`** (vtable `0x8203852C`, base `0x82029E9C`) is one of them. Its
   post-race update (`sub_826B06A8`, slot `+0x30`; base `sub_82609DF0`, `+0x28`) gets the
   local player from the online game object (`*(0x833F4A80)` -> `sub_82915BE0`; base
   `0x83305CC4`, `sub_828653D8`). Single-player races go through that object too. When the
   component's event id (`+12`) is not 500, it calls the writer `sub_826ADDD0` (base
   `sub_82607BF8`). For TU4's multi-stage (rally) series it calls `sub_826AE3E8`.
3. **The writer** walks the event's races (`SELECT Id,COALESCE(Place,0) AS FinishPlace FROM
   Races LEFT JOIN Career_RaceResults ...`) and inserts or updates `Career_RaceResults`. It then
   reads `SELECT Place FROM Career_EventResults WHERE EventId=%u`: it inserts
   `(EventId, Place, 0)` when there is no row, and updates only when the new place is better
   (`cmpw` new < stored). Last, it counts the events won (`SELECT COUNT(DISTINCT EventId) ...
   WHERE Place=1`).
4. **Wristband XP is the improvement only.** `sub_8253B230` gives an event's remaining points:
   the first place's points at the event's level, minus the points of the stored place
   (`... JOIN WristbandScoring w ON w.Place = r.Place`). `sub_826A7A88` reads the points table
   (`WristbandScoring`, or `RallyScoring` for style 4 in TU4). `sub_824BEF10` sets up the
   `Main/XP` / `Main/WristbandLevel` / `CareerWristbandLevels` tracker (TU4 adds the rally
   one). Replaying an event at the same or a worse place earns no wristband XP.
5. **The payout** (`sub_826C0C00`) reads `CashPrize` (`sub_825353B8`) and the points per place.
   The stats (`sub_826C01E0`) update the `Main/*` counters, online and offline.
6. **Unlocks** happen only in the festival flow. `CTriggerEventUnlocks` (vtable `0x8209C584`,
   enter `sub_82A37C70`; base `0x8208BEE4`, `sub_82989FC0`) runs `INSERT OR IGNORE INTO
   CareerEventUnlocks` (`sub_8253B658`), the hub unlocks (`sub_8253C1E0`) and the
   XP/popularity checks (`sub_8253BDA0`, `sub_82542C00`).

**Online races have no career component.** The race's components are built by `sub_826B7068`
with a jump table on the race type (table `0x82038170`). Types 3 (career), 4 and 20 (rally
race) build a `CCareerComponent` (`sub_826AC9D0`, `sub_826ACC48`, `sub_826ACEC8`), which copy
the current career event id `0x833ED728` into `+12` and `0x833ED72C` into `+8`. Type 6 builds
`sub_826AF3C0`'s set, which has none. So an online race can't write `Career_*`. A stale event
id left over from an earlier career race can't leak into one either.

### Granting the result to each participant

**In game (a client hook after the online race).** Run what the career race would have run:

- the writer with a career-component-shaped object whose `+12` is the event id;
- the unlock logic of `CTriggerEventUnlocks`;
- the XP through the wristband tracker, so that a level-up and its rewards still happen;
- the multiplayer flow's `CSaveProfile` then saves.

Not known yet:

- what the writer reads from the local player in an online race (the place, all races of the
  event);
- the meaning of the component's `+28`/`+30` flags (set from a manager at `*(0x832FF1D8)` in
  the base game);
- how the career points screen and the reward car sequence behave when they never run.

All of it is per build.

**A server message.** The Message Center already gives credits (`0x8304`, `RetrieveCreditsGift`)
and cars (`0x8306`, `RetrieveCarGift`) in the game today (docs/title_services.md). That could
deliver the cash prize and a prize car from `Rewards_EventPrizes`. There is no notification
type for XP, event results or unlocks, so this can only add to another route.

**The launcher after the session.** The launcher already decodes, edits and re-signs both
files with keys from the player's own executable. The profile editor (`save_editor.py`)
handles `Main/XP`, `Main/WristbandLevel` and `Main/Credits`. The garage editor (`garage.py`)
edits `PlayerDatabase` with a backup, a check that the save didn't change, an atomic replace
and a re-decode check. It already talks to the server signed with the player's Forza Live
identity (`shader_share.py`, after each session). A grant is then:

- `Career_RaceResults`/`Career_EventResults` set to the best place;
- the XP improvement from `WristbandScoring`;
- `Main/WristbandLevel` from the thresholds;
- the cash prize;
- the unlocks the game's rules give.

The game must be closed, so the credit shows at the next start.

Offline check (scratch copy, launcher codecs, no game run): a result was granted for event 38
(place 3) to the save above. It added a `Career_EventResults` and a `Career_RaceResults` row
(89 -> 90 each), XP 12480 -> 12560 (+80, level 0, place 3) and credits +35,000 (`CashPrize`).
Both files kept their sizes (6,291,464 and 51,208 bytes), decoded again with the game's keys
(MAC, AES) and passed SQLite's integrity check. Whether the game loads such a save and shows
the event as done is the first thing to run (open questions).

## Base game and Title Update 4

- **Data:** the disc's 119 events, their races, scoring and rewards are identical in TU4's
  database. TU4 adds 36 rally events (style 4), `MultiStageSeriesEvents`/`Races` (in TU4 the
  career's series id lives at race settings `+0x2D4`, mapped by `sub_8266DCA8`/`sub_8266DBD0`),
  `RallyScoring`, `RallyWristbandLevels`, `Main/RallyXP` and `Main/RallyWristbandLevel`.
  `CareerRaceModes.xml` differs only in the Mission mode.
- **Code:** the same functions at other addresses (the evidence table). `CCareerComponent`
  gained a virtual function: its post-race update moved from `+0x28` to `+0x30`. TU4 added
  race types 19/20 (rally) to the game type decision and the component builder. The points
  queries take the table by name (`WristbandScoring` or `RallyScoring`: `sub_8253B230`,
  `sub_826A7A88`), where the base game names `WristbandScoring` in the SQL (`sub_82602B40`).
- **Online:** the online game object has 4 more bytes before the options in TU4 (docs/dlc.md).
  Base and TU4 games are kept apart by the server (`#TU4` room), so a co-op world is always
  one version.
- **Saves:** the career tables and profile fields are the same. TU4 converts a save one way
  (`VersionFlags`), so the launcher's grant has to work on both. The key tables are read from
  the installed executable, and a base profile is signed with another HMAC key than a TU4 one
  (the 32 bytes before the table, not its second half): the launcher read only TU4 profiles
  until the acceptance run of 2026-11-04 found it (see "The two-game acceptance test").

## Risks

- **Save corruption.** The launcher route uses the codecs that the unicorn-car and profile
  edits already ship with: a backup, verification and atomic replace, the game closed. The
  real risk is derived state: unlocks, the wristband level-up and its reward car (Rewards_
  Wristband), the Nemesis race at each threshold, hub unlocks by XP. A grant that skips them
  leaves a save the game might not expect. Mitigation: do what the game does (the SQL above),
  and check the game re-runs its unlock checks at load. The in-game route writes into the
  live database from a flow that never did; an error there is saved by `CSaveProfile`.
- **Desync.** The race itself is the proven online event path (#67: 11 of 11 clean runs, the
  hosts and peers leaving, three players). New online settings are untested: the
  `PIWithPlayerLast` grid with several humans, `Checkpoint` and `Elimination` end conditions,
  traffic. Each needs a two-console run before it is used. The places must agree: each console
  computes its own `PLACE x/8`.
- **Cheating.** The game is peer to peer and the server can't see the race. The server can:
  - credit only the rounds it served (it knows the event, the session and the players at
    launch);
  - accept each place once;
  - cross-check a player's own report with the host's `XSessionWriteStats` ranking of the
    humans (already received, `forza_live/competition/stats.py`);
  - cap one grant per event and session.
  A modified client can still lie. On a private server among friends that is acceptable; on
  the public server the credit should be limited to what a single-player run of that event
  can give anyway.
- **Players at different career stages.** A guest may not have the host's event unlocked, or
  may be at a lower wristband. Style-1 events unlock a whole level at a time (`sub_82542ED8`:
  `... WHERE (Level = %d) AND CareerEventStyle=%d AND (UnlockPointsReq=0 ...)`); street events
  unlock by XP (`sub_82542C00`: `UnlockPointsReq <= %d`).
  Policy: the result goes into the ledger for everyone, and each launcher applies it only if
  the event is in that save's `CareerEventUnlocks`; otherwise it is kept until it is. The
  host's own event is always unlocked for the host, hence the host-only first milestone.
- **Double rewards and difficulty.** Players get online XP and credits from the multiplayer
  flow as well. The co-op playlist's `payoutModifier` (a playlist field) or the grant can
  account for that. The CPU drivers must use the event's skill (above), or co-op events will
  be much harder than the same event alone.
- **The launcher is not always used.** A player who starts the game some other way gets the
  grant at their next launcher start. Results can wait on the server (bounded, per XUID).

## Options

Effort is in developer days for someone who knows this codebase, including tests, rough.

### A. Online event rounds, a result ledger, the launcher applies it (recommended)

- **Server** (4-6 days):
  - a co-op world (a playlist whose placement is friends-only);
  - the host picks the event by WORLD chat command;
  - the campaign event's XML: grid order, end condition, traffic, time of day, field size, and
    `EventRestrictions` decoded into `CarRestrictions`;
  - a ledger of event rounds (session, event, the humans at launch, their places);
  - a signed endpoint the launcher reads, like the shader list upload.
- **Client** (2-3 days):
  - the event's AI skill and temperament in the existing AI difficulty hook;
  - each player's finishing place reported for an event round, unless `XSessionWriteStats`
    turns out to carry places;
  - the event id told to every peer, not only the host (`WORLD_EVENT` gets a second op).
- **Launcher** (3-5 days):
  - fetch the player's results after a session and before Play;
  - apply them to the save (results at the best place, the XP improvement, the wristband
    level, the cash prize, the unlocks), with the existing backup;
  - synthetic-save unit tests like `test_garage.py`.
- **Validation** (2-3 days): two TU4 games, a base pair, the edited saves loaded and checked
  in Race Central and on the map, a normal career race after it.
- **Total: about 11-17 days. Risk: low to medium** (no new in-game writes; the derived state
  is the main risk).

### B. Online event rounds, granted in the game

- **Server:** as A, without the launcher endpoint (3-5 days).
- **Client** (8-12 days): reverse-engineer and hook the career writer, the unlock trigger and
  the XP tracker for both builds, and call them after `CPostRaceUpdateProfile` of an event
  round with the event id. The credit shows at once, with the game's own rules.
- **Validation:** 3-5 days.
- **Total: about 14-22 days. Risk: medium to high** (state writes in a flow that never had
  them, two builds). It can come after A and use the same ledger and policy, replacing only
  the last step.

### C. The career race itself as the networked race (not recommended)

Load race type 3 and the festival flow on every console of a session. The career flow has no
lobby, no player status steps, no peer car hand-off and no results exchange: all of what #67
had to fix for CPU drivers in the online flow, and more. Weeks to months, high risk, and the
online flow already runs the same race.

### Stopgap: the Message Center

The cash prize and a prize car as Message Center gifts after an event round: server only, half
a day to a day. It doesn't record the event or XP, so it is only a complement to A.

## Recommended first milestone

**Two TU4 players in a private co-op world race a festival event the host picked, with the
event's settings and CPU drivers; only the host's career advances.**

1. Server: a "Co-op Festival" playlist with friends-only placement (the Horizon World's code
   otherwise: drop-in, standby hosts, the 30 s wait lobby). `/event <id or HorizonEventID>` in
   WORLD chat from the world's host starts it. The campaign XML for style 1 with
   `PIWithPlayerLast`, `NumLaps`, the event's time of day, class and decoded restrictions.
2. Client: the event's `AISkill`/`AITemperament` columns at Medium for the CPU drivers of a
   campaign round.
3. Ledger: the server records the round, the requester and the requester's place (its own
   report, cross-checked with the host's `XSessionWriteStats`).
4. Launcher: after the session it applies the host's result to the host's save: the race and
   event result at the best place, the XP improvement, the wristband level and the cash prize.
5. The guest gets what any online race gives today (online XP, credits) and no career change.

Acceptance:

- the event's lobby shows its class, restrictions and 6 CPU drivers in their restricted cars;
- both race it;
- after closing the game and starting it from the launcher, the host's Race Central shows the
  event done at the right place, with the XP and credits added once;
- a second run at a worse place changes nothing.

About 8-12 days.

**Then:**

- **M2, guests too:** each launcher applies the result if the event is unlocked in that save.
  Street races if the traffic test passes. The prize car through the Message Center or the
  launcher (`garage.py` already adds stock cars). Done, and street races stay off: see
  "Milestone 2". No festival race has a prize car, so there is none to grant: see "Prize cars".
- **M3:** the base game, then the in-game grant (option B) for instant credit (see "What
  milestone 3 needs" and "The base game").
- **M4:** the event prompt at its world marker instead of the chat command; the named CPU
  field if `AIFieldId` or a hook makes it possible. The start from the map is done (TU4): see
  "Milestone 4: events from the map".

## Milestone 1 progress

### Phase A (2026-10-23): the measurements and the race

Measured with TU4 games against a local Forza Live server: two games (DriverA, DriverB:
copies of one Xenia career save, each with its own `XDG_DATA_HOME`, headless gamescope at
960x540 and 30 fps, `tools/autodrive.py` at the wheel), and a single-player game on scratch
copies of a granted save. Addresses are TU4's.

**Question 1, a launcher-granted save: it loads cleanly, and the game reads the result; the
unlocks are the grant's job.** A copy of the TU4-converted save was given a result with the
launcher's codecs (the study's offline grant): FR60 at 3rd (+160 XP at level 6, +18,000
credits), and two of its `CareerEventUnlocks` rows were deleted. The game loaded it without a
complaint, showed the credits (655,181) and wrote its profile again with the granted XP and
credits intact (decoded after the game's own save). The same grant at 1st turned STATS > RACES
"Festival events won" from 65 into 66 of 70
(`screenshots/2026-10-23_coop_granted_save_festival_events_won.jpg`), so the game counts a
`Career_EventResults` row it never wrote. The two deleted unlocks were still missing after
loading the single-player world and the game's save: nothing re-runs the unlock checks at load.
The flows unlock events after a race (`CTriggerEventUnlocks trigger_type="race_completion"`:
`Rewards_EventUnlock` for the event, `sub_8253B988`, then the XP unlocks, `sub_82546080`: events
with `0 < UnlockPointsReq <= XP` of styles 1, 2, 5 and 6 up to the wristband level, a street race
only once its hub is unlocked, and the hubs with `EventHubs.UnlockPointsReq <= XP`, which add
their `EventHubInitialEvents`), on popularity, and with the wristband upgrade. The wristband
level is not the grant's job: the free roam reminds the player when the XP is past the next
threshold and Race Central awards it (`race_central.xml`: `is_new_wristband_available` ->
`CIsWristbandUpgradeAvailable`, then `CTriggerWristbandUpgrade`, which runs the level's unlocks
and its reward). So a grant writes the result, the XP improvement, the payout and the event's
own unlock rewards, and leaves `Main/WristbandLevel` and the unlocks by XP to the game (phase B).

**Question 2, `X_PROPERTY_RELATIVE_SCORE`: the place among all cars.** The host's
`XSessionWriteStats` gives each human `cars - place`: 7th of 8 wrote 1, 8th 0, 4th 4 (and in
#12's four-human event 5th to 8th wrote 3, 2, 1, 0). So the server has every human's place
among all cars from one write, without client code; each player's own `WriteRaceStatsEx`
(`userFinishPosition`/`maxPlaces`) says the same, and the two cross-check. Its finished flag is
not a finish: a player still at 3% of the route when the race ended was reported "place 8/8
finished 1". `X_PROPERTY_SESSION_TEAM` was 6 and 7 for the two humans in every campaign round
(they started 7th and 8th; in #12's random grid it was 3, 0, 6, 7): probably the starting slot.

**Question 3, `AIFieldId`: no.** A round of FR50 sent with `AIFieldId="231"` (server test knob
`--coop-ai-field`) had the usual CPU drivers, Terry Adams, James Alvord, Thomas Binder, Jed
Brown, Adam Barr and Chris Ashton (`AIPlayers` 1, 3, 13, 20, 10, 6), all in Ford GT40s, not
event 231's field (Hailey Harper in a GT40 MkII, Jon Orton in a 1965 Cooper S, Cliff Didcock in
a 250 GTO, ...). In the code only the career event setup (`sub_825C54B0`) and an event preview
screen (`sub_8285E898`) load `EventParticipants`; the online fill names low `AIPlayers` ids and
picks cars with the bucket's filter. The named field would need a hook.

**Question 4, the race settings online with two humans:**

| Setting | Online | Evidence |
| --- | --- | --- |
| `GridOrderingType="PIWithPlayerLast"` | works | the two humans started 7th and 8th (`PLACE 7/8` and `8/8` at 00:00.000) although their cars had the highest PI (600 against 507-541) (`screenshots/2026-10-23_coop_street_race_start_players_7th_8th.jpg`) |
| Time of day | works, as one of four | the online options name a time with `HorizonTimeOfDay` only: Dawn 07:47, Midday 12:00, Sunset 17:30, Midnight (`sub_82696DB8`); an event's `TimeOfDayStart` (seconds) goes to the nearest: FR05 (17:39) raced at sunset, FR50 (09:17) at dawn. The exact time would need the options' float written by a hook (TU4 options `+716`) |
| `Checkpoint` (street race, preset 198) | runs | Plains Run loaded on both consoles with the PROGRESS HUD and the GPS route; not driven to its end (the test drivers left the road) |
| Traffic (`UseTraffic="true"`) | no | no civilian car on the route's first stretch, and the race's car list held only the 8 racers. The flows explain it: `game_street_race_flow.xml` turns traffic spawning on (`global.enable_traffic_spawning`, `CEnableTraffic`), and `game_multiplayer_race_flow.xml` turns it off before every online race (`global.disable_traffic_spawning`), whatever the options say. Street races are therefore off by default (`--coop-street` offers them, without traffic); traffic would need that step changed for campaign rounds |

Two side findings. The peer's race clock started about 12 s after the host's in all three
rounds (its intro ran longer). With the event's own CPU skill the CPU drivers also get the
event's rubberband: an `AISkills` row's `Rubberbands_id` (1 for the online rows 20/16/6, 4 to 10
for events' 40-76) cuts their torque from 120-400 m ahead, so when both test drivers were stuck
off the road the six CPU cars stopped about 770 m ahead and waited, as in single player.

**Question 5, `EventRestrictions`: decoded.** `sub_826AE848` is the restriction list's
`push_back`; the decoder is `sub_826A33B8` (called by `sub_826AEFF8` for each row of
`SELECT * FROM EventRestrictions WHERE EventId=%d`), and it fills the same 76-byte restriction
as the bucket XML's `CarRestrictions` (`sub_826A8E88`). `Value` is hex: a 40-bit mask in 5 bytes
(byte k holds bits 8k-8k+7), then a big-endian value per set bit in bit order. Bits 0
`CarClassId` (u8), 2/3 `PIMin`/`PIMax` (u16), 4/5 power, 6/7 weight (floats), 8/9
`YearMin`/`YearMax` (u32), then pairs of a comparator (u8: 0 `Equals`, 1 `DoesNotEqual`) and an
id: 10/11 body family, 12/13 car type, 14/15 region, 16/17 country, 18/19 make (u16 id), 20/21
model family (u16), 22/23 model (u16), 24/25 drive type, 26/27 engine configuration, 28/29 engine
placement, 30/31 cylinders, 32/33 aspiration, 35/36 family special; 37/38 `BaseCostMin`/`Max`
(u32); bits 34 `AllowUpgrades` and 39 `RallyOnly` are flags without a value. A mask of 0 leaves
the defaults (no restriction, upgrades allowed). The game's decoder stores bit 4 (PowerMin) in
PowerMax's place; no event uses either. The masks in use set years, make, model family, body
family, region, country, drive type and base cost (`server/forza_live/world/campaign.py`
`decode_restrictions`, tested on them). The lobby matched the tickets: FR05 "Oakley Blitz",
ticket "AWD ONLY", gave a lobby of B class AWD cars only (the players' S-class cars became
GT-R R34s; CPU Delta S4, Impreza 22B, ML 63, Lancer Evo VI) and a garage of "3 available"; FR50
"MUST BE 1960 - 1969" gave Shelby Daytonas and GT40s; FR24 "CAR VALUE < 15,000 Cr" decodes to
`BaseCostMax="15000"`, FR38 "MUST BE ASTON" to `MakeId="2"`.

**The Co-op Festival** (`server/forza_live/world/campaign.py`, `forza_live_server.py`, `forza_live/world/world.py`):

- A playlist (hopper 20, after Playground Games) with the Horizon World's code: the same search
  settings, drop-in, standby hosts and world events. The client runs it as a world
  (`LiveIsWorldPlaylist`), and the server lists it only to games whose HELLO has feature 0x08,
  so an older build never sees a playlist it would run as a plain free roam.
- **Placement: friends only.** A search for it gets only the worlds whose host (or, after the
  host left, its standby lead) is on the searcher's friends list (`forza_live/social/friends.py`, accepted
  both ways); without one the searcher's game opens a world of its own, and its friends are
  placed into that. The server knows nothing of parties; the friends list is the one
  relationship it keeps, both players chose it, and a block already removes it.
- **`/event <code or id>`** in World chat (`FR05` or `4`), from the world's host only and only
  in a Co-op Festival world, starts the event through the world event path; everyone in the
  world gets a notice like "DriverA starts FR05: B class, AWD, 7 CPU drivers, 2 laps, Sunset".
  `/events [level|street]` lists the codes; any other "/" line is chat. The scheduled world
  events (`--world-event-minutes`) leave co-op worlds alone.
- **Elsewhere `/event` says where it works** (#395: the host of a Custom Free Roam typed
  `/event FR05` in World chat and got only "You are not in a Horizon World"). A custom game,
  another playlist or single player: "/event needs a Co-op Festival world (custom games have
  none): Multiplayer > Find Game > Co-op Festival"; the public Horizon World: "... not the
  Horizon World: ..."; Session chat: "Type /event in World chat (To: Horizon World), in a
  Co-op Festival world". World chat outside a world: "World chat is for a Horizon World or
  Co-op Festival world (Find Game): use Session chat here". The menu names are the game's
  (MULTIPLAYER, FIND GAME, CO-OP FESTIVAL) and the chat panel's ("To: Horizon World").
- **Not in a Custom Free Roam, even a private one among friends.** An event is the world's
  next round: the host's game asks the server for that round's options (`GetGameOptions`,
  Find Game's playlist; `world_events.cpp` ends the free roam only while Find Game runs a
  world playlist, `LiveIsWorldPlaylist`). A custom game's rounds come from its host's own
  Setup Game and never ask the server, so `/event` there would need the client to rewrite a
  custom game's setup (track, game type, restrictions, CPU drivers, which Create Custom Game
  does not offer for festival events) and to end its free roam: not done. A private
  Co-op Festival is what a Custom Free Roam among friends is for: its placement already takes
  only the host's friends.
- **The round** (`campaign_xml`): the event's track, laps, preset (1 circuit, 2 point to point,
  198 street with `--coop-street`), `TargetClass` and decoded restrictions in the bucket,
  `NumberOfDrivers` CPU drivers (`MaxAIPlayers`, `MaxPlayers` one more), `PIWithPlayerLast`,
  the nearest `HorizonTimeOfDay`, the crowd, `AIDifficulty="Medium"`.
- **CPU skill** (client, `world_events.cpp`): the server sends every console of the world the
  event's `AISkillMed`/`AITemperamentMed` rows (WORLD_EVENT op 2) until the round starts; in a
  round on that event's track the host's AI difficulty is Medium and every CPU driver gets the
  event's rows, through a hook on the level's lookup (`sub_82568BD8`, TU4 `sub_825DEDD8`) and on
  every console at the round start and when its player goes in game. Seen on both consoles:
  all six at `00000038 00000006` (FR05's 56/6, instead of Easy's 20/8) at the round start and in
  game. The career adds each named driver's `AIPlayers` modifiers (nearly all 0); the online
  fill's drivers get the event's rows as they are.
- Tests: `server/tests/world/test_campaign.py` (the masks of the game's database, comparators, the
  PowerMin quirk, the time of day, the round's XML, the listing, the playlist only for games
  that have it, friends-only placement also after the host left, `/event` from the host and its
  refusals, the CPU skill to every member, results named with the round, no scheduled events in
  co-op worlds).

**Demo** (two games, local server): A picked Co-op Festival and drove alone at once; B picked it
and was placed into A's world (friends), JUMP IN. B's `/event FR05` was refused ("Only the
world's host (DriverA) can start an event"); A's started it. The lobby (both games): CO-OP
FESTIVAL CIRCUIT RACE, Gladstone Dirt Sprint, 2 laps, DriverA and DriverB in GT-R R34s, six CPU
drivers in B class AWD cars, the 30 s wait
(`screenshots/2026-10-23_coop_fr05_lobby_A_left_B_right.jpg`). Both raced to the end
(`screenshots/2026-10-23_coop_fr05_both_finished.jpg`: "FINISHED 7TH", "FINISHED 8TH"), and the
server logged `co-op: world 88f17d51eb63d38e campaign event 4 (FR05, asked by DriverA): DriverA
place 7/8 finished 1` and the same for DriverB; then both were back in the world. FR50 (point to
point, the 1960s, dawn) and the street race followed in the same world.

### Phase B (2026-10-23): the ledger and the launcher's grant

**Server: the ledger** (`server/forza_live/world/coop_ledger.py`). A campaign round opens when the event
round starts (its own prefetch, `forza_world.Worlds.next_round`, asked by the host's game with
its own friends key) and closes when the world's next round starts: the world session, the
event, its grid (the `MaxPlayers` it was served with), the player who asked for it (the
world's host) and the humans in the world. Only calls that carry the caller's own friends key
(`forza_live_lsp.caller_trusted`, `docs/title_services.md` "Who is calling") move a campaign
along or report a place; a call without it gets the world's round and changes nothing. Each
human's place is its first own `WriteRaceStatsEx` of the Co-op Festival playlist for that
world's session while the round is open (later reports, other worlds and the public Horizon
World's races are not taken), cross-checked with the requester's game's own
`XSessionWriteStats` (`cars - score` with the round's grid; writes of other games are ignored,
and only the first after the start counts: the game writes again for the session when the
world's next free roam ends, which the first two-game run showed). A result goes to the career
when the player is the round's requester (milestone 1: the host's own career), finished, on the
grid, and its game's write (if any) agrees. The launcher asks for its player's results and
confirms what it settled, signed like the shader list upload (X-Forza-Auth, #515):
`GET /coop/results`, `POST /coop/results/applied {"ids": [...]}`. Kept in
`DATA_DIR/coop_ledger.json`, written from the server's tick at most every 2 s (not per report):
30 days, a round dropped once settled or when it closed without a result, at most 20 rounds a
player asked for and has not settled (its `/event` is refused beyond them, and its oldest go
first; other players' rounds are never evicted for them) and 2000 in all.

The `/event` command is refused while a round runs in the world ("start the next one when you
are back in the free roam"), and a request the host's game never takes (it has to be in the
free roam) is dropped after 2 minutes with a notice to the world. The operator's
`/world/event?session=all` leaves Co-op Festival worlds alone. The playlist's id is 20, not 4:
Find Custom Game searches with the game type tab's value (Street Race 4), and the server only
sees the key. Placement into a friend's world needs both the searcher's and the leader's XUID
proven by their friends keys (`friends_ok`). The playlist is listed only when the server has
events for it (the game's database, `--game-db`).

**Launcher: the grant** (`launcher/forza_launcher/coop_grant.py`). After every session (the
same place as the shader list upload, in a background thread, public or custom servers only)
it fetches the player's results and applies them to the session's save, as the career's race
end would:

- `Career_RaceResults` and `Career_EventResults` at the new place, only when it beats the save's
  (a worse or equal place changes nothing, so a result applied twice is harmless);
- the wristband XP of the improvement (`WristbandScoring` at the event's level: new place's
  points less the old place's);
- the prize at that place, decoded from `sub_8269BA50` and `sub_826984E8`: the festival modes
  pay `CashPrize * (cars - place + 1) / cars`, the street mode 100/50/25 % to the top three, and
  the amount is rounded to two significant figures (`CreditsMultiplier` is 1.0 for every event;
  the difficulty bonus is not applied). This answers most of question 6;
- the event's own unlock rewards (`Rewards_EventUnlock`).

Which results, and into which save: the PC's Forza Live identity is shared by every save on it
(the local profile, each Xenia copy, a custom save folder), so the launcher keeps its sessions
(`coop_sessions.json`: the save profile, start and end, for 30 days) and applies a result only to
the save whose session it was earned in (its time within 10 minutes of the session, for the
server's clock). A result of another save's session waits for that save's next session (also
when the launcher was closed before the grant ran); a result of a game the launcher did not
start waits unapplied. A result for an event the save has not unlocked (`CareerEventUnlocks`:
`/event` can start any festival event) waits until it is. Waiting results are not confirmed.

Left to the game: `Main/WristbandLevel` (Race Central awards it, see question 1) and the unlocks
by XP. `CTriggerEventUnlocks` runs those with the save's XP after every race, so the player's
next race catches up; the Nemesis races there have conditions of their own: a first version that
unlocked by XP alone (the query of `sub_82546080` without them) gave a real save five Nemesis
races (NR02-NR06) the game had not unlocked. The edit is the save editor's: the save lock, both
files decoded and verified with the player's keys, one backup ("Before co-op results"), both
files checked unchanged before a complete staged save is published through the save manager's
directory-swap journal, and decoded again after. An interruption before publication rolls back
the whole save; one after publication keeps both edits. Recovery runs before the next grant or
game session, so a partial result cannot skip its XP/credits on retry. Tests include failures
writing the second staged file and journal, a failed directory swap, and real child-process
exits after each rename followed by recovery and an idempotent retry.
Tests: `launcher/test_coop_grant.py` (synthetic saves and database: payout and rounding,
a new result, a better place paying the difference once, a worse or equal place changing
nothing and making no backup, events it does not take, the wristband and XP unlocks left alone,
a save changing during the backup, fetch-apply-confirm, locked events and results of other
saves' sessions waiting) and `server/tests/world/test_coop_ledger.py` (forged and late reports,
other games' writes, the round's grid, unverified calls, refusals, limits, the batched file).

**Acceptance, checked with two games** (local server, FR69 "Advan Asphalt Attack", level 6, R3,
unlocked and not raced in the host's save; first with the code above, then again from fresh
saves and a fresh server with the review fixes and the caller check of `docs/title_services.md`):

| Milestone 1 acceptance | Result (the run with the fixes) |
| --- | --- |
| The lobby shows the event's class, restrictions and CPU drivers in restricted cars | FR05 (phase A); FR69: R3, Red Rock Descent 6.61 km, six CPU drivers in R3 cars (`screenshots/2026-10-23_coop_fr69_lobby_A_left_B_right.jpg`) |
| Both race it | twice: host 7th, guest 8th (`screenshots/2026-10-23_coop_fr69_A_7th_B_8th.jpg`), then host 8th, guest 4th. Every call carried the players' keys (no unverified call logged, the race stats `verified: true`); `/event FR05` typed during the race got "An event is running in this world"; each round closed when the world's free roam came back. Ledger: `round b76a36042714a1c3 (FR69): DriverA place 7/8`, the host game's write `relative score 1`; the guest's results kept but not offered (`GET /coop/results` for DriverB: none) |
| After closing the game, the host's save shows the event done at the right place, with XP and credits added once | the launcher's grant (session recorded, then after_session): "FR69 at place 7 (was not raced): +80 XP, +4,300 CR", "applied 1, unchanged 1, locked 0, waiting 0", one backup; the save decoded after: XP 11,230 -> 11,310, credits 635,931 -> 640,231, `Career_EventResults` 233 = 7. In the game the credits show 640,231 and the map's card says FR69 "170 PTS" (250 for 1st less the 80 earned at 7th), where an unraced level-6 event says "250 PTS" (`screenshots/2026-10-23_coop_granted_fr69_7th_170pts.jpg`; the first run's 4th showed "130 PTS", `screenshots/2026-10-23_coop_granted_fr69_4th_130pts_vs_unraced_250pts.jpg`); with a granted 1st, STATS counts the event won (question 1) |
| A second run at a worse place changes nothing | the second round's 8th was offered and left unchanged; both were confirmed, and the next grant found nothing (`GET /coop/results`: none) |

Not done in milestone 1: guests' careers (M2: the ledger keeps their results; each launcher
would apply them when the event is unlocked in its save), the prize car (`Rewards_EventPrizes`),
the base game's addresses for the client hook (the server and launcher parts are the same for
both), the exact time of day, the named CPU field and traffic.

## Milestone 2: the guests' careers (2026-10-24)

**Every player's own result goes to that player's career.** The ledger
(`server/forza_live/world/coop_ledger.py`) now offers each human of a round its own result, not only the
host's. The milestone 1 rules stay:

- only a player's own calls count (its friends key);
- only its first report for that world's session counts, and only while the round is open;
- the place must be on the round's grid and the race finished.

A guest's place must also agree with the host's game. Only the requester's game writes
`XSessionWriteStats` for every human at the race's end (phase A), so a guest's own report is
checked by a second console. Only a write from a game that proved its XUID with its friends key
counts (`forza_stats.write(verified=...)`): a second client claiming the host's XUID can neither
confirm nor deny a guest's place. Without that write (the host's game left before the end) the
guest's result is not offered. The host's own place still counts without it, as in milestone 1,
because its own game wrote both. The stats store skips a session's writes for a minute after
rating it; the ledger now gets every write first (`forza_stats.write`), so that skip can't hide
the race's write from it. `GET /coop/results` now also gives each result's `until` (when the
server drops it unapplied) and `host` (the gamertag of the host who started it). The co-op log
line names the campaign round of the world the result was reported in, and none when that world
had no campaign round.

**A guest whose career has not reached the event.** `/event` can start any of the 70 festival
events. A guest at a lower wristband may not have the event unlocked: style-1 events unlock a
whole level at a time. The launcher doesn't confirm such a result, so it waits on the server.
It goes in at the first session after the career unlocks the event, at the place it was earned,
if that still beats the save's place. After the session the Play page lists what was added and
what waits, for example: "Co-op results: FR05 (8th in DriverA's world) waits: this career has not
unlocked FR05 yet. It is added at the first session after you unlock it; the server keeps it
until 23 Nov 2026."

The bounds on waiting results:

- they are kept for 30 days (`KEEP_DAYS`);
- a player has at most 20 (`MAX_WAITING`) grantable results of closed rounds. Beyond that, the
  ones earned as a guest are dropped first, then the oldest, with a log line. A report in a round
  that is still open doesn't count yet, since it may never become grantable (not finished, no
  host write);
- the host's `/event` is refused while 20 results of rounds it asked for wait, so its own rounds
  never drop its own results. Results it earned as a guest don't count, since the cap drops those
  first. The notice says why they wait (an event the career has not unlocked) and when the oldest
  expires;
- the launcher drops, with a note, a result it can never apply: one from a game it did not start,
  or from a time when two saves' sessions overlapped. A result earned with another save waits for
  that save's next session;
- a round leaves the ledger once it is closed and none of its results waits (each one confirmed,
  dropped, expired or not grantable).

Waiting was chosen over dropping because the result is real, and the career unlocks the
event's level with a later wristband.

**Double rewards: the grant pays the prize less what the online race paid.** An online race
pays credits of its own, online winnings (`Main/OnlineWinnings`, added to `Main/Credits`), and
online XP (`Main/OnlineXP`, the XP REWARDS screen). In the runs below the online winnings were:

| Round | A (7th) | B (8th) |
| --- | --- | --- |
| FR69 | 5,000 | 5,000 |
| FR05 | 2,500 | 1,875 |
| FR05, second session | 2,500 | 2,500 |
| FR05, third session | 2,500 | 2,500 |

The career prize is a different amount: FR69's 7th place pays 4,300 and FR05's 750.

Two ways to stop paying both were checked:

- **The playlist's `payoutModifier`: no.** The post-race payout (`sub_826C0C00`) multiplies a
  race's credits by `sub_82917058`'s float: the online game's `+2069` in a Find Game (mode 7 at
  `+40`), 1.0 otherwise. Its place, after three flags, fits the playlist's `payoutModifier`,
  but no copy into it was found. A Co-op Festival served with `payoutModifier` 0 still paid
  both players 2,500 for FR05 (the second session). So either that float is not the playlist's,
  or the world's rounds don't run in that mode, or the winnings come from another path. The client's
  multiplayer rewards are Sean's draft PR #231's area, so this milestone doesn't hook them.
- **The grant: yes.** A race's stats (`User.WriteRaceStatsEx`) carry the player's
  `onlineWinnings`, written before that race pays: A's FR05 write in the second session said
  649,731 credits and 13,750 winnings, and its save after the race had 652,231 and 16,250. So
  the ledger keeps each result's value at the round's report and at the player's next race,
  from any playlist, with that race's time (`winnings`, `winnings_at` in `GET /coop/results`).
  The launcher pays the event's prize less the difference. Every save of a PC shares the Forza
  Live identity, so it trusts the next race only when its time falls in a session of the same
  save, and only when the value grew. Otherwise the save's own `Main/OnlineWinnings` stands in,
  but only when the grant runs right after the session the result was earned in. When neither
  applies (a locked result applied weeks later, after races in another save), or the result has
  no `winnings` (an older server, or a milestone 1 ledger's round), what the race paid is not
  known. The grant then adds the result and the XP but no prize: "+0 CR (no prize: what the
  online race paid is not known)". The player gets the larger of the two at most, never both.

What stays:

- The online XP is the online track's. The grant's wristband XP goes to `Main/XP`, which the
  online races left alone (11,230 before and after the first session in both saves).
- The grant pays the prize and the wristband XP once per improvement. That is less than single
  player, which pays the prize again on every replay; replaying a co-op event earns only the
  online winnings.

**Street races: off, and no career result.** Phase A found that the multiplayer race flow
(`game_multiplayer_race_flow.xml`) turns traffic spawning off before every online race. Traffic
would need two things beyond this milestone: a change to that step for campaign rounds, and
traffic shared between the consoles (each game spawns its own, and FH1's online free roam has
none). A street race without traffic is not the career's street race. So:

- the server keeps street races off by default;
- `--coop-street`, a private server's test switch, offers them for fun, and the `/event` notice
  says "street race (no traffic online, so no career result)";
- the ledger gives their results to nobody's career;
- the launcher's street payout (100/50/25 % to the top three) stays for when traffic works.

**Acceptance, two TU4 games** (local server; DriverA hosts, DriverB is the guest). Both saves
were copies of one career with FR69 (level 6, R3) and FR05 (level 0, B class AWD) not raced. In
B's save, FR05 was locked, as for a guest whose career is behind.

| Milestone 2 acceptance | Result |
| --- | --- |
| Both race; each place is in the ledger and checked against the host's game | FR69 (Red Rock Descent, six CPU drivers in R3 cars): A 7th, B 8th; FR05 (Gladstone Dirt Sprint, 2 laps): A 7th, B 8th. The host's game wrote relative scores 1 and 0 for both rounds (7th and 8th), and every call carried the players' keys; `round 4c2dddcd27130f9c (FR69) closed with 2 result(s); DriverA place 7/8; DriverB place 8/8` |
| The guest's launcher applies the guest's result | B: "FR69 at place 8 (was not raced): +70 XP, +2,100 CR". The save decoded after: XP 11,230 -> 11,300, `Career_EventResults` 233 = 8. B's next game loaded it and showed the credits, 646,536 after both of B's grants (`screenshots/2026-10-24_coop_guest_granted_save_credits.jpg`) |
| A locked event waits, with a note, and applies once unlocked | B: "FR05 (8th in DriverA's world) waits: this career has not unlocked FR05 yet ..." (`applied 1, unchanged 0, locked 1`). `GET /coop/results` kept FR05 for B. After FR05 was unlocked in the save (standing in for the career's own unlock), the next grant gave "FR05 at place 8 (was not raced): +30 XP, +380 CR", and the server had nothing left for B |
| The host as in milestone 1 | A: FR69 7th (+80 XP, +4,300 CR), FR05 7th (+40 XP, +750 CR); both confirmed, and the ledger file emptied once every result was settled. A second session's FR05 (7th and 8th again) was "unchanged" for both and confirmed |
| No double rewards | The grants above ran before the deduction existed: they paid the prize on top of the online winnings, which is the double reward. A third session raced FR05 again with its result removed from both saves (so it counts as not raced). The server sent `"winnings": [16250, null]` for A and `[15625, null]` for B. The saves after the session had `Main/OnlineWinnings` 18,750 and 18,125: the race had paid both 2,500. The grants: "FR05 at place 7 (was not raced): +40 XP, +0 CR (the prize less the 2,500 CR the online race paid)", and the same at 8th with +30 XP. Credits stayed at 654,731 and 651,536; the result and the XP went in. `Main/XP` was unchanged by all three sessions' races |

Seen along the way:

- `/event FR63` ("MUST BE AUDI", class S): neither save had an S-class Audi, so both games said
  "NO CARS AVAILABLE ..." in the lobby. The round started and ended at once with nobody on the
  grid (`closed with 0 result(s)`). The server can't see garages. The `/event` notice already
  names the restriction ("FR63: S class, Audi, 7 CPU drivers, 1 lap, Sunset"); a check against
  the garage would need the client (M4).
- The log line `co-op: world ... campaign event ...` named an earlier world's round while that
  round was within its 20-minute window. It now uses the world the result was reported in.

Tests: `server/tests/world/test_coop_ledger.py`:

- each player's result goes to that player's launcher only, and once;
- a guest's place needs the host's agreeing write; the host's own place doesn't;
- a write from a client that claims the host's XUID without its key is ignored;
- the host's write counts even within the stats store's one-minute repeat filter;
- street races give no result;
- waiting results are capped per player without touching others: guest results go first, and a
  report in a round still open doesn't push out a valid one;
- 30 days at most;
- `/event` counts only the rounds the player asked for, and its notice says why and until when;
- a milestone 1 ledger file still loads;
- the online winnings around a round: its report and the player's next race, with its time.

`server/tests/world/test_campaign.py`: the reported world's round names a result, and a world without
a campaign round names none. In `launcher/test_coop_grant.py`:

- a guest's locked result waits unconfirmed and applies at a later session once unlocked;
- the prize is less what the online race paid, using the server's bracket only when its race was
  in the same save, or the save's `Main/OnlineWinnings` only right after the result's session;
- otherwise, or without `winnings`, no prize;
- the notes are shown even when the confirmation fails;
- results of a game the launcher did not start are dropped with a note;
- the notes' wording and places.

### What milestone 3 needs

- **The base game:** a two-game run. The client's CPU skill hook is already written against the
  base addresses (`sub_82568BD8` and the world event op 2 path) and mapped to TU4's; the server
  and launcher parts serve both builds unchanged (see "The base game").
- **The in-game grant (option B):** instant credit, and for players who start the game outside
  the launcher (their results wait, unapplied, today). It can read the same ledger and keep its
  rules (own results only, the host's write, improvement only, no wristband level).
- **The prize car** (`Rewards_EventPrizes`, 17 events): none of them is a festival race, so a
  co-op round never earns one (see "Prize cars"). Other multiplayer rewards and unlocks are
  Sean's draft PR #231's area.
- **Results that wait:** an in-game note when a co-op result waits (today only the launcher's
  Play page says so), and a way for a player to drop one.
- **Before `/event`:** whether every player has a car for the event (FR63 above). Done
  differently in milestone 3: every player gets a loaner that meets the event (see "Events
  nobody has a car for").
- **Online winnings at the source:** the game asks the server for a race multiplier at every
  race start (`Hopper.GetRaceMultipliers`, `specialMultiplier`, always 1.0 today). Measured in
  milestone 3 (see "The race multiplier").

## Milestone 3 (2026-10-24)

### Events nobody has a car for ("NO CARS AVAILABLE")

`/event FR63` ("MUST BE AUDI", S class) showed "NO CARS AVAILABLE" on both games, and the round
ended at once with no results. The game's database explains it (the queries were logged with
`FORZA_SQL_LOG=1`, `src/hooks/database_query.cpp`):

- **Online rounds give a player without a qualifying car of its own a car from the rental
  garage.** (M1's FR50 used the saves' own Shelby Daytonas.) The presets' `<Overrides>`
  (TU4 `GameOptionPresets.xml`, which a round without its own `Overrides` inherits) set
  `ForceOnlyRentalCars="1"`. The lobby then picks
  `SELECT Id FROM Restrictions_Data_Car AS Garage WHERE Garage.IsRentable=1 AND ... ORDER BY
  PerformanceIndex DESC`. `Data_Car.IsRentable` marks one stock car per class: Rabbit GTI,
  Fiat 500 Abarth, Scion FR-S, Skyline GT-R V-Spec II (B, AWD), Ford GT (A), Gallardo LP570
  (S), Gumpert Apollo (R3), 599XX (R2) and FXX (R1). That is why the earlier rounds ran in GT-Rs
  (FR05, B AWD) and Gumpert Apollos (FR69, R3). For a player who owns no qualifying car, an
  event that the class's one rental does not meet has no car. By the restrictions alone that
  is about 40 of the 70 events.
- **With `ForceOnlyRentalCars="0"` the game lends any car that meets the event.** The same
  pick runs without `IsRentable` (`... WHERE Garage.ClassId=6 AND Garage.MakeId=3 AND
  IsUnicorn=0 AND IsCarDisc1(Id) ...`), over every installed car, upgrade levels included, best
  PI first. In a round of FR63 with it, the guest, whose save has no S-class Audi, got an Audi
  R8 GT. Both raced to the end, and the guest's garage was unchanged afterwards (the car is
  only lent). The host had an R8 GT of its own in that run (added to its scratch save), and the
  lobby showed the same car for both.
- **The fix (server):** campaign rounds send their own `<Overrides>` with
  `ForceOnlyRentalCars="0"` (`forza_campaign.CAMPAIGN_XML`). Every event whose restriction some
  installed car meets is playable; no client change was needed.
- **And an explanation when a round still ends at once:** a campaign round that closes within
  90 s without a single report (`forza_world.EMPTY_ROUND`) is told to the world: "FR63 ended at
  once and nobody raced it: its cars must be S class, Audi (a game that has no such car shows
  NO CARS AVAILABLE)". This uses what the server sees (the host's game moving to the next round
  with nobody's race stats).
- **And a warning before it, where the database tells:** the loaner is a stock car, and four
  events have none: FR24 and FR65 (A class, up to 15,000 and 20,000 CR), FR60 (A class, FWD) and
  FR25 (A class, FWD, one body type). Their career race expects a cheaper car upgraded into A.
  The server checks every event against `Data_Car` at start (`forza_campaign.car_fits`, the
  restriction columns the lobby's pick names). For these four, `/event` adds "No stock car fits
  it (A class, FWD): only players whose own upgraded car fits can race it". The server can't see
  garages, so this is as far as it can tell in advance.

Tests: `server/tests/world/test_campaign.py` (the round's `ForceOnlyRentalCars="0"`, the restriction
text, the warning for an event no stock car fits) and `server/tests/world/test_coop_ledger.py` (a
round nobody raced is told to the world and a raced one is not, and the multiplier knob gives a
campaign round's players its values in turn and everyone else 1.0).

### The race multiplier (`Hopper.GetRaceMultipliers`)

At every race start the game asks the server for a multiplier (`Hopper.GetRaceMultipliers`,
`specialMultiplier`; the server has always answered 1.0). The test knob
`--coop-race-multiplier 2` or `0,1` gives a campaign round's players that value, one value per
round in turn. Three rounds of FR63 were raced, with A 7th and B 8th each time, and both saves
were decoded around each round:

| Multiplier | A online XP | A online winnings (online level-ups) | B online XP | B online winnings (level-ups) |
| --- | --- | --- | --- | --- |
| 2 | +8,680 | +17,000 (2) | +7,200 | +9,000 (1) |
| 0 | +4,030 | +5,000 (0) | +3,600 | +20,500 (1) |
| 1 | +4,030 | +10,500 (1) | +3,300 | +20,500 (1) |

- **Online XP:** 2 doubles it (the XP REWARDS screen's total), and 0 is taken as 1, not as
  nothing.
- **Credits:** no sign of it. A's round without a level-up paid 5,000 at 0, the same race payout
  the other rounds imply. `Main/OnlineWinnings` also takes the online level-ups' rewards, which
  vary from 4,000 to 15,500 here, so those rounds can't separate the two.

So the multiplier can't make an event round pay no credits, and it would change the online XP
that all online play earns. **The grant's deduction stays**, and it is also the more robust of
the two. It works from what the save itself recorded around the race, needs nothing from the
client, and errs on the safe side: when the race's own winnings are not known it pays no prize.
One more thing the table shows: a level-up's reward lands in `Main/OnlineWinnings` too. So a
round in which the player levels up online deducts that reward from the prize as well. That
lowers the prize, never pays twice, and an in-game grant (below) could separate the two.

### Design: the in-game grant (option B), for players who start without the launcher

Today a co-op result reaches a career only through the launcher, after the session. A player
who starts the game some other way (a shortcut, Steam, a script) gets nothing: their results
wait 30 days and the launcher drops them as "from a game the launcher did not start". Option B
grants in the game, right after the race, with the same rules and the same ledger. It is a
design only; nothing of it is built.

**When.** After an online campaign round's race, in the multiplayer race flow, once
`CPostRaceUpdateProfile` (TU4 enter `sub_829DCB88`) has run and before its `CSaveProfile`. The
client already knows a campaign round: `world_events.cpp` keeps the event and track the server
sent (WORLD_EVENT op 2), and `CampaignGame()` tells the round from the world's free roam. A hook
on the flow state after the post-race update starts the grant on each console for its own
player.

**What it asks the server.** The ledger stays the authority: only it has the host game's write
that confirms a guest's place. The client asks over the Forza Live UDP channel, which already
carries the world events and chat and proves the player with its friends key:

- a new WORLD_EVENT op ("my result of round R") is answered by the round's entry for that
  player, as `GET /coop/results` gives it (event, place, places, winnings), or by "not yet";
- the host's `XSessionWriteStats` and the guest's `WriteRaceStatsEx` arrive at the race's end,
  so the client retries for a few seconds while the result screens show;
- a result that isn't grantable gets a reason (no host write, not finished, a street race).

**What it writes.** The same as the launcher, through the game's own code where it exists, so
that the game's cached state stays right:

- **the results:** the career writer (`sub_826ADDD0`, base `sub_82607BF8`), called with a
  career-component-shaped object whose `+12` is the event id. It inserts or improves
  `Career_RaceResults` and `Career_EventResults` exactly as a career race does. What it reads
  from the local player in an online race must be found first (the open question under
  "Granting the result to each participant");
- **the XP improvement:** the points from `sub_8253B230` (what the event has left to earn),
  added through the wristband tracker that `sub_824BEF10` sets up, so the free roam's "new
  wristband" reminder follows. `Main/WristbandLevel` stays Race Central's;
- **the event's own unlocks:** `Rewards_EventUnlock` through `sub_8253B988`;
- **the prize, less what the race paid:** in game this is exact, with no bracket and no session
  matching. The post-race payout's result (`sub_826C0C00`, its output `+0`) or the
  `Main/OnlineWinnings` change across the race is the online winnings. The grant adds
  `max(0, prize - winnings)` to `Main/Credits`;
- the flow's own `CSaveProfile` then saves all of it.

**Settling.** After the save, the client confirms the round to the server (the same
"applied" the launcher sends). The two paths can't pay twice. The place only ever improves, and
the launcher pays the prize only when it improves the save's place. So a result that the game
granted, and that the launcher fetches again because the confirmation was lost, comes back
"unchanged" there. A locked event (not in the live `CareerEventUnlocks`) is not granted in game
either: it stays on the server for the launcher or for a later in-game check, which could run
when the free roam loads.

**What the player sees.** A line on the results screen or a toast: "Career: FR69 7th, +80 XP,
+4,300 CR", or "FR69 waits until your career unlocks it". Today only the launcher's Play page
says so.

**Per build.** All of it needs the TU4 and the base addresses (`src/tu4_addresses.h` maps
the hooks). The base game's career component has its post-race slot at `+0x28`, not `+0x30`.
Base and TU4 games never share a world, so a world is one build.

**Risks and order of work:**

1. Find what the career writer reads in an online race (its local player through the online
   game object `*(0x833F4A80)`), and call it on a scratch save from a test hook outside any
   race.
2. Grant from the flow in a two-game run: the results, XP and credits in the saved file, Race
   Central's wristband prompt, and the map's points card.
3. Behind a cvar (`coop_ingame_grant`, off at first). The launcher path stays and settles
   whatever the game did not. The server needs only the new op.

The risk is writing career state from a flow that never did. A wrong call is saved by
`CSaveProfile`, so every step is checked on scratch saves, as the launcher's grant was.

### Option B: findings so far (parked)

An attempt to build the design above, for TU4 only, stopped before any code was written: writing
XP and credits from a hook needs more reverse engineering than expected. The launcher grant
stays the way a co-op result reaches a career. What was found:

1. **The profile's values are a tree keyed by hashes.** In a running game, the in-memory
   profile is a tree of nodes keyed by a 32-bit hash. It has the same fields and types as the
   `ForzaProfile` file the launcher edits. `Main/Credits` is the node keyed `0xB2050D51`; its value
   object has the vtable `0x821551C0`, the u32 value at `+8` and the type 3 at `+16`. It was
   found by scanning guest memory for the save's credits. The strings `Main/Credits`
   (`0x8200DFD4`) and `Main/XP` (`0x82005948`) are referenced by no code. The hash is none of
   CRC32, CRC32C, FNV-1/1a, djb2, sdbm, one-at-a-time, lookup2 or murmur2 over the name, its path
   or UTF-16 forms. So a hook can't find these values safely, and writing them directly is out.
2. **The career's own components can do the work.** The career race's component set (TU4
   `sub_826AC9D0`) builds:
   - a 360-byte payout component, `sub_826C67D0(this, *0x833ED72C, *0x833ED728 (the event id),
     byte 0x833ED720, 0)`. Its event id at `+16` is what `sub_826C0C00` reads to pay the
     event's `CashPrize`;
   - a CCareerComponent (vtable `0x8203852C`, the event id at `+12`). Its post-race step
     (`sub_826B06A8`) calls the career writer `sub_826ADDD0`;
   - `sub_82592AA0`, and more when the byte at `0x833ED721` is 10.

   The online race's set (`sub_826AF3C0`) builds the same payout component with an event id of
   0, which is why an online race's prize base is 0.
3. **The plan.** For a campaign round only, behind a cvar (`coop_ingame_grant`):
   - pass the event id to that payout component, so the game pays the career prize itself;
   - add a CCareerComponent to the online set, so the race's own `CPostRaceUpdateProfile` writes
     the result with the game's code;
   - run the event's unlock rewards (`sub_8253B988`);
   - let the multiplayer flow's `CSaveProfile` save, then send the server an "applied" message
     (a new WORLD_EVENT op), so that the launcher finds the result settled. This also works for a
     game started outside the launcher.
4. **Unknowns to settle first:**
   - where an online race's own winnings come from. If they don't come from that payout
     component, the career prize would be paid on top of them again;
   - where a career race adds its wristband XP: the career component, or a step only the
     festival flow runs (`ShowPostRaceCareerPointsScreen`);
   - what the career writer reads from the online game object (`*(0x833F4A80)`) in an online
     race.
5. **Default:** the cvar off until a two-game run shows the result, the XP and the credits each
   landing exactly once. After that, on, with the launcher grant kept as the fallback for
   whatever the game doesn't settle.
6. **Estimate:** about a day. That is about two hours of reverse engineering, then two or three
   build and two-game test rounds of about half an hour each; a client build is incremental.
   The risk is medium to high, because it changes what an online race's components do.

A smaller first step would write only the result and the event's unlocks in game, through the
game's own SQL on the `Profile0_*` tables (the query function `src/hooks/database_query.cpp`
wraps). XP and credits would stay with the launcher. That needs a ledger flag: otherwise the
launcher finds the place already stored and pays no XP or credits.

## Milestone 2, continued (2026-11-03): prize cars, the base game, the acceptance test

Done from the code, the game's data and scratch save copies, without running the game. The
guests' careers (milestone 2 above) are unchanged: their tests pass on main (server 52, launcher
26 co-op tests), and the acceptance test below runs them again with today's server and launcher.

### Prize cars

**No event a co-op round can run has a prize car, so the grant has none to give.** The game
has three ways to give a car after a career race, and none of them applies to a festival race:

- **`Rewards_EventPrizes`** (17 rows, the same in the disc's and TU4's `gamedb.slt`): every one
  is a showcase (style 3: the exhibitions and the races against a plane) or a Nemesis race
  (style 5: NEM_TEST, NR02-NR06, NEM_FINAL). No festival race (style 1) and no street race
  (style 2) has one. The payout component's post-race step (TU4 `sub_826C84D8`, base
  `sub_82620A70`) asks for the event's car (`sub_82535298`, base `sub_824C7270`: `SELECT
  RewardCarId, RewardCarUpgradeClass FROM Rewards_EventPrizes WHERE EventId=%d`) when the player
  finished 1st (1st or 2nd for event 101, the first plane race), and keeps it (its `+344` car,
  `+348` upgrade class) only when the save had no result for the event or a place below that
  (`sub_82522108`). So a prize car is a first win's. The festival flow then shows it
  (`ShouldShowRewardCarSequence`, `CIsPendingCarReward`: vtable `0x82092340`, slot 9
  `sub_829D05C8`, a pending car unless the manager's `+308` is -1). A real career save shows
  what the game writes: a Gold wristband TU4 career save has ten of the 17 cars (not the others), each a
  `Career_Garage` row with `Flags` 2, its owner's gamertag as `OriginalOwner` and one owner, and
  no other car with `Flags` 2, with 65 of its 70 festival events won.
- **The career car tombola** (`ShouldShowCarTombolaScreen` -> `CIsCareerCarTombolaEvent`: vtable
  `0x8202D3CC`, slot 9 `sub_8264C338`, base `sub_825C20C8`): only after a street race
  (`SELECT CareerEventStyle FROM Events WHERE Id=%d` must give 2, `sub_82534CB0`), with a flag of
  the race settings (`*(0x833CAACC)+969`) set, and only when the event is the last of its hub's
  list (`sub_8264BB68`: the hub's events ordered by `UnlockPointsReq, CashPrize, TargetClass,
  Id`). The car is drawn at random (`CCareerEventPrizeCarTombola`).
  Co-op street races give no career result (traffic, above), and a random draw is not something
  the launcher could repeat as the game would.
- **The wristband's reward**: Race Central gives it with the wristband (`CTriggerWristbandUpgrade`),
  which the grant leaves to the game. (No string of either build names `Rewards_Wristband`.)

The online flow's own car draw (`show_car_tombola`, `Rewards_DriverLevelv3`) is the online
driver level's, which the online race already runs. So milestone 2's grant stays as it is. A
result for a showcase or a Nemesis race never reaches a save (`launcher/test_coop_grant.py`
tests both), and the server's `/event` offers neither. Should a later milestone run them online,
the grant would add the car as the game does: on a first win (as above), a stock `Career_Garage`
row with `Flags` 2 upgraded to `RewardCarUpgradeClass`, through `garage.py`'s stock car builder.

### The race completion's unlocks

The grant writes the event's own unlocks and leaves the unlocks by XP to the game (milestone 1).
Read again for this milestone, with both builds' code:

- **The event's own unlocks** (`Rewards_EventUnlock`, `sub_8253B988`, base `sub_824CD7D0`): the
  race completion trigger (TU4 `sub_82A35AC8`, base `sub_82989B40`) gives them when the stored
  place is within the event style's place to pass (`sub_825112D8`, a tunable per style), and for
  a street race also when the XP reaches its own threshold. Its rows are the Headline race's (it
  unlocks NEM_FINAL), the rally championships' and one for an event the database doesn't have.
  **No festival race has one**, so for the events co-op runs today this step writes nothing.
- **The unlocks by XP** (`sub_82546080`, base `sub_824D7A10`): `SELECT Id, CareerEventStyle, HubId
  FROM Events WHERE (UnlockPointsReq <= XP AND UnlockPointsReq > 0) AND (CareerEventStyle=5, 2, 6
  or 1) AND Level <= wristband level`, then per style:
  - a street race only when its hub is unlocked (`CareerHubUnlocks`) and either the hub is 1 or
    the world's `eventhub001` activity has its flag set (`sub_82535C68`, in-world state);
  - a Nemesis race only when the save has no result for it within a place held in memory
    (`*(0x833CFE24+8)`, a tunable);
  - the Headline race and the festival races at their XP;
  - then the hubs by XP (`sub_82542D30`, base `sub_824D4610`).
  Each unlock also starts a notice (the radio or a phone message) and updates the world's event
  markers. Two of the conditions are in the running game's memory only, and the save's wristband
  level, which only Race Central raises, holds back every Nemesis race and festival level. So the
  launcher can't run this rule exactly; the game runs it after the player's next career race,
  with its notices. The festival races it unlocks are six of levels 0 and 1 (FR01 and FR05 at 30
  XP; FR03, FR06, FR08 and FR10 at 430): only a career in its first hour has them still locked.

### The base game

**Nothing in the game or server part of the co-op path is TU4 only.** The base pair of the
acceptance run (2026-11-04) raced and got its results like the TU4 one; only the launcher had to
learn the base game's profile key (below).

| Part | Base game |
| --- | --- |
| The Co-op Festival in Find Game | listed: both builds send the HELLO features `0x5F` (`src/kernel/net.cpp`), which include the Co-op Festival's `0x08`. Base and TU4 games sign in to different rooms (`#TU4`), so a co-op world is always one build |
| Friends-only placement, `/event`, the round's XML | server only, the same for both. The round's XML has no TU4-only element (TU4's `MultiStageSeriesOptions` is left out and defaults); the base `GameOptionPresets.xml` has the same `<Overrides>` with `ForceOnlyRentalCars="1"`, which the round replaces with 0, and the base image knows the attribute. The disc's 9 rental cars are the same (TU4 adds 9 more) |
| CPU drivers and the event's skill | `src/hooks/world_events.cpp` is written against the base build's addresses (the online game `0x83305CC4`, the AI lookup `sub_82568BD8`, the round's hooks) and mapped to TU4's by `src/tu4_addresses.h`; its field offsets have both builds' values (`FORZA_TU4 ? ... : ...`). The Horizon World's CPU drivers were built on the base build first |
| Places and online winnings | the server reads them from `XSessionWriteStats` and `WriteRaceStatsEx`, the same records in both builds (#12's four-human places were the base game's) |
| The launcher's grant | the same `Career_*` tables and `Main/*` values, the disc's `gamedb.slt` for both (its 119 events are TU4's), the keys from the installed `default.xex`. A base profile (`VersionFlags` 0) has the same AES key as a TU4 one but its HMAC key comes from the 32 bytes before the key table; the launcher knew only the TU4 key, so every base save failed its integrity check, fixed in `save_editor._keys` |

What a base run had to show, all of it seen in the acceptance run (step 8): the Co-op Festival
listed, the round's lobby with the event's cars, the log line `WORLD: campaign event ... CPU
driver(s) at the event's skill`, a loaner where a save has no car that fits, both places in the
ledger, and the grant decoded from the base saves (after the launcher fix).

**For the in-game grant (option B), the base addresses** (found with `tools/tu_address_map.py`
and checked by the strings and calls each one makes):

| What | TU4 | Base |
| --- | --- | --- |
| Payout component post-race (the prize car, the event-101 rule) | `sub_826C84D8` | `sub_82620A70` |
| Prize car of an event (`Rewards_EventPrizes`) | `sub_82535298` | `sub_824C7270` |
| Post-race payout | `sub_826C0C00` | `sub_82619798` |
| Payout component constructor | `sub_826C67D0` | `sub_8261F0B8` |
| Career race's component set | `sub_826AC9D0` | `sub_82606AA0` |
| Online race's component set | `sub_826AF3C0` | `sub_82608B70` |
| Components by race type | `sub_826B7068` | `sub_82610448` |
| Race completion unlocks | `sub_82A35AC8` | `sub_82989B40` |
| The event's own unlocks (`Rewards_EventUnlock`) | `sub_8253B988` | `sub_824CD7D0` |
| Unlocks by XP | `sub_82546080` | `sub_824D7A10` |
| XP / wristband tracker setup | `sub_824BEF10` | `sub_8248EFC8` |
| Career car tombola check (street races) | `sub_8264C338`, `sub_8264BB68` | `sub_825C20C8`, `sub_825C15D8` |
| An event's `CareerEventStyle` | `sub_82534CB0` | `sub_824C6BD8` |
| `CIsPendingCarReward` | `sub_829D05C8` | `sub_8291D5F8` |
| Race settings (event id `+0x388`) | `0x833CAACC` | `0x832DF00C` |

The career writer, the career component's post-race step (`+0x28` in the base game), the
current event id and the online game object are in the "Evidence" table.

### The two-game acceptance test (run 2026-11-04)

For the guests' careers on today's server and launcher (signed requests #515, relays #516 and
the servers page came after milestone 2's run), the loaner, and a base pair. Never on a real
save: copies of a test career save, a TU4 one and a base one.

**Set-up.** Two games as in phase A: DriverA (the host) and DriverB (the guest), each with its own
data folder, Forza Live identity and copy of the save, friends with each other, against a local
server from `server/` started with the game's database (`--game-db`). Each game is started from
the launcher (or its session recorded with `coop_grant.record_session` and the grant run with
`coop_grant.after_session` once the game is closed, as milestones 1 and 2 did). The
TU4 test save used so far (a Gold wristband career) has FR69 (level 6, R3), FR63 (S class,
Audi), FR60, FR68 and FR59 unlocked and not raced.
Before and after each session, for both saves:

    tools/career_state.py SAVE --game GAME --xuid XUID --event FR69 --event FR63

(SAVE: the `00000001` folder; XUID: the profile's, which the save's keys are bound to.)

**Steps and what to check:**

1. **Both race, each place in the ledger.** A and B pick Co-op Festival (B is placed into A's
   world), A types `/event FR69` in World chat. Check: the lobby has the event's class and six CPU
   drivers in R3 cars, both race to the end, the server logs `round ... (FR69) closed with 2
   result(s)` with both places, and every call is verified (no unverified-call line).
2. **Each save gets its own result.** Close both games. Each launcher's Play page says "Co-op
   results: FR69 at place N (was not raced): +X XP, +Y CR ...". `career_state.py` after: FR69 at
   that player's own place, its race too; `Main/XP` up by the event's points at that place (level
   6: 80 at 7th, 70 at 8th, as in milestone 2); `Main/Credits` up by the prize
   less what the race paid online (`Main/OnlineWinnings` before and after, the note names it);
   `Main/WristbandLevel` unchanged; `CareerEventUnlocks` and `Career_Garage` unchanged by the grant,
   no new `prize_cars` (the session itself adds a car at each online level-up: the online driver
   level's reward, `Flags` 264, its owner the gamertag). Each save has one new backup, "Before co-op results". `GET /coop/results` has
   nothing left for either player.
3. **A worse place changes nothing.** `/event FR69` again, both finish lower or equal: both
   grants return "applied 0, unchanged 1" (the Play page shows no co-op note for an unchanged
   result), the saves' state is the same as after step 2, and no backup is added.
4. **The loaner.** `/event FR63` (no S-class Audi in the test save): both games lend a car
   (an Audi R8 GT), both race, both get a result; their garages (`Career_Garage`) are unchanged.
5. **In the game.** Start each game from its granted save: the credits shown match
   `career_state.py`, the map's FR69 card shows the points left (250 less the points earned),
   and a single-player race afterwards runs normally and saves.
6. **Optional, the locked wait.** On B's copy remove FR59 from `CareerEventUnlocks` before the
   session (the launcher's codecs: `garage.decode` / `encode`), race `/event FR59`: B's note says
   it waits, `GET /coop/results` keeps it for B, and it is applied at B's first session after the
   unlock is put back.
7. **Optional, no festival prize car.** In single player, win an unraced festival event (FR68):
   no reward car sequence, and `prize_cars` unchanged.
8. **The base game.** Steps 1, 2 and 4 with two base builds and base saves. Also check in A's log
   `WORLD: campaign event 233 (...): 6 CPU driver(s) at the event's skill ...`.

**The run (2026-11-04).** Main at `bf75fbd` (server `74affee`), the TU4 and base builds of that
commit, a local server with `--game-db`, each game on its own headless gamescope (960x540, 30 fps),
`tools/autodrive.py` at the wheel. The TU4 pair: two copies of the golden TU4 career save (630,931
CR, XP 11,230, Gold wristband), B's with FR59 removed from `CareerEventUnlocks`; the base pair: two
copies of the Xenia career save the golden save was converted from (`VersionFlags` 0, the same
career). The grants ran as `window.py` runs them at a session's end (`record_session`, then
`after_session` against the local server with each player's identity). Three TU4 sessions (FR69 and
FR59; FR69 again; FR63), one base session (FR69 and FR63), then a single-player session on each
build.

| Step | Result |
| --- | --- |
| 1. Both race | Pass. B was placed into A's world (friends); `/event FR69` gave CO-OP FESTIVAL POINT-TO-POINT RACE, Red Rock Descent 6.61 km, DriverA, DriverB and six CPU drivers in R3 cars (Saleen S7, LP700-4, McLaren F1 GT). Both games logged `WORLD: campaign event 233 (round start): 6 CPU driver(s) at the event's skill 48 temperament 6`. Ledger: `round 91e56b8e2bc25e26 (FR69) closed with 2 result(s); DriverA place 8/8; DriverB place 4/8`, the host's write `relative score 4` for B and `0` for A. No unverified call. A's car stuck at a hairpin at 4% (autodrive), so A's 8th is a race not driven to the end: the game reports it "finished 1" when the race timer ends (phase A), and it counts |
| 2. Each save its own result | Pass. A: "FR69 at place 8 (was not raced): +70 XP, +1,475 CR (the prize less the 625 CR the online race paid)" and "FR59 at place 7: +80 XP, +0 CR (the prize less the 5,000 CR ...)"; XP 11,230 -> 11,380, credits 636,556 -> 638,031, FR69 and race 169 at 8, FR59 at 7. B: "FR69 at place 4: +120 XP, +1,500 CR (the prize less the 9,500 CR ...)"; XP 11,230 -> 11,350, credits 645,431 -> 646,931, FR69 at 4. Both: `WristbandLevel` 6, `CareerEventUnlocks` and `Career_Garage` unchanged by the grant, `prize_cars` the same ten, one backup "Before co-op results"; `GET /coop/results`: nothing for A, B's locked FR59 only |
| 3. Worse or equal place | Pass. A 8th (not driven) and B 4th again (`round 8ae1d18b7ea17ae1`): both grants "applied 0, unchanged 1", no note, no backup added, `career_state.py` identical before and after the grant |
| 4. The loaner | Pass. `/event FR63`: "DriverA starts FR63: S class, Audi, 7 CPU drivers, 1 lap, Sunset"; both games and all six CPU drivers in Audi R8 GTs (S 648); A 7th, B 8th; grants +80 XP and +70 XP (+0 CR, the online race paid more than the prize). No R8 GT in either garage afterwards |
| 5. In the game | Pass. The multiplayer lobby showed 638,031 CR (A) and 646,931 CR (B) after step 2, the `career_state.py` values. A in single player: the map's FR69 card "Advan Asphalt Attack R3 180 PTS" (250 less the 70 earned at 8th); GoPro Millennia Masters raced to the end (8th), and the save after it had `Main/NumRaces` 90 -> 91 and credits 643,031 -> 647,656 |
| 6. The locked wait | Pass. B's FR59 (8th): "FR59 (8th in DriverA's world) waits: this career has not unlocked FR59 yet ... until 04 Dec 2026", kept by the server through B's second session; after FR59 was put back, B's third grant: "FR59 at place 8 (was not raced): +70 XP, +0 CR (the prize less the 5,000 CR the online race paid)", with the winnings bracket of B's next race (`[9500, 14500]`) |
| 7. No festival prize car | Not run: the test driver trails the CPU drivers and does not win. `prize_cars` stayed the same ten through every session and grant above |
| 8. The base game | Pass after a launcher fix. The base pair: Co-op Festival listed, B placed into A's world, FR69 with six CPU drivers in R3 cars and the log line above in A's log, `round e3594d3b2aa8926b (FR69) closed with 2 result(s); DriverA place 7/8; DriverB place 8/8`; FR63 with R8 GT loaners for both (`round 8e582ae1028dfe3c`: A 7th, B 8th). **The grant failed at first:** `career_state.py` and the launcher refused both base saves ("The profile integrity check failed"), and so would the grant, the profile editor and the design import. The garage decoded; the profile did not. A base profile is signed with another HMAC key: the AES key is the TU4 one, the HMAC key comes from the 32 bytes before the key table instead of its second half (found by trying the table's neighbourhood against the base save's MAC; the bytes are the same in the disc and TU4 images). With the fix (`save_editor._keys` offers both pairs, an edit keeps the pair the save decoded with): A "+80 XP ... +0 CR" for each event, XP 11,230 -> 11,390; B +70 each, 11,230 -> 11,370; `VersionFlags` still 0. The base game loaded A's granted save: 648,431 CR and the FR69 card "170 PTS" (250 less 80 at 7th) |

Seen along the way:

- **A race not driven to the end still counts.** A's car never left the first hairpin (4%), and
  the race timer's end gave it 8th "finished 1", which the ledger, the host's write and the grant
  all took: +70 XP and the prize less what the race paid. Last place is the least an event gives,
  so this was left as it is; what a career race gives a player who stops was not measured.
- **Online level-ups add a car each** (the online driver level's reward, `Rewards_DriverLevelv3`,
  `Career_Garage` `Flags` 264): six cars in the four co-op sessions. Their rewards land in
  `Main/OnlineWinnings` too, so a round with a level-up pays a smaller prize (milestone 3).

## Milestone 4: events from the map (2026-11-04)

**The host of a Co-op Festival world starts an event from the map, as a single-player career
picks one: the event's marker, its card (X DETAILS), then SELECT "START CO-OP EVENT".** It
sends the server the same as typing `/event <id>` in World chat, so the round is the same:
the event's class, restrictions, grid and CPU drivers at the event's skill, and the ledger's
results. `/event` keeps working. Title Update 4 only (the card's hooks are TU4's, like Custom
Race's); a base game world keeps `/event`.

### How the map starts an event offline, and what an online world had

- **The markers are activities.** Every festival event is an
  `ActivityCareerEventActivation` of `career_event_activations.xml` (vtable `0x8200C104`, the
  event id at `+404`). The career creates the activities of the events the save has unlocked
  (88 in the test save), and each puts a marker on the map and the minimap
  (`CMapEntityActivity`, vtable `0x82024460`, its activity at `+0x28`). The map shows
  SET ROUTE (A), DETAILS (X), FAST TRAVEL ANYWHERE (Y, locked in the test save) on a marker.
- **The card.** DETAILS opens `ForzaUI::CCareerEventDescription`, the same card the event's
  start shows. On the map it has no career entry (`+376` is 0): its A sets a route, and its
  footer reads SET ROUTE, BACK, RESTRICTIONS, LEADERBOARD. The event id is the race settings'
  current event (`*(0x833CAACC)+0x388`), set by the map. **Offline the map never starts an
  event:** the race starts at its sign-up zone (the `TriggerZone`, radius 25: SIGN ME UP, the
  same card with its career entry, whose A runs `CLoadIntoCareerRace`).
- **An online world had no markers.** The free roam loads its activity files by race type
  (`sub_826A0AC0`, file ids of the resource table at `0x833930C8`). Race type 17, the
  single-player free roam, loads ids 146-168. Type 18, the online free roam, leaves out 151
  (paintshop), 156 (race activations), 160 (race encounters), 162 (multiplayer), 164
  (career event activations), 166 (DLC center) and 168 (street race hubs). A census of the
  map's markers found 853 records of career event markers in single player and none in a
  Co-op Festival world.

### What the client does (`forza-recomp/src/hooks/coop_map_event.cpp`)

1. **The markers.** In a Co-op Festival world (Find Game's playlist 20, the round's free
   roam), the hook on `sub_826A0AC0` also loads file 164 (`sub_8296EDD0(loader, 164)`) after
   the others. The career creates the same activities as in single player.
2. **Their info.** A career marker reads its event's entry in the career's event map info: a
   component (`*(sub_8269B4F8())[*(0x833EDC58)]`) whose map at `+0xC034` is filled by
   `sub_827445A0`'s SQL, with the markers themselves (96 bytes each from `+16`, their count at
   `+0xC018`). Nothing fills it online. Without it, two things crashed in the test runs:
   - the career activity's map filter (`sub_82505098`, vtable slot 25) read a missing
     entry in the free roam's first seconds;
   - the map's card under the cursor (`sub_828B6998`) read the marker's own copy (`+0x40`,
     null) as soon as the cursor reached an event.
   `sub_825CB940`, which `CTriggerEventUnlocks` runs after a career race, fills the map and
   copies each marker's entry. The hook runs it before the file is loaded. It runs again once
   the markers exist (they are made a frame or more later): the hook on `sub_825CB068` (the
   map's test whether a marker shows) runs it when a career marker has no entry, once per
   count of markers. With both, the map and the minimap show the events as in single player:
   the won ones with a trophy, the new ones NEW, with name, class and prize under the cursor.
3. **The card.** `custom_race.cpp`'s hooks on the card (bind `sub_82733A68`, input
   `sub_82738948`, destructor `sub_82727530`) ask `coop_map_event.cpp` first. On a map card in
   a Co-op Festival free roam it adds SELECT "START CO-OP EVENT" to the footer (slot 13, the
   Custom Race slot; Custom Race is offline only). The map clears the card's footer after the
   bind to add its own prompts, so the hook on `sub_8289A908` (a footer's clear) puts the
   prompt back. SELECT sends the World chat command `/event <id>` (`LiveChatCommand`,
   `live_friends.cpp`: World chat's SAY, not shown in the Chat tab). The server's notices
   are shown as usual.
4. **On a guest's console** the card offers the same prompt. The server refuses the command
   from anyone but the world's host, and the guest gets the notice "Only the world's host
   (DriverA) can start an event". The guest's map and minimap show its own career's events,
   since each console loads them from its own save.
5. **The sign-up zone.** Online the zone at an event's start shows SIGN ME UP too, but its X
   does nothing there: driven to FR17 "Oakley Heat Wave" and stopped in the zone,
   X opened no card. So no single-player race can be loaded out of the online world. Should a
   sign-up card (with its career entry) open in a Co-op Festival free roam anyway, its A starts
   the co-op event and its X (Rivals, a single-player time trial) does nothing (not seen: no
   such card opened).

The world's free roam ends into the event's lobby while the host is still on the map's card
(`world_events.cpp` takes the event from the free roam's update, which runs under the map).
Cvar `coop_map_events` (on) turns it all off; `FORZA_COOP_MAP_TRACE=1` logs the refreshes and
every card.

The server needs no change. Its Co-op Festival description in Find Game now names the map:
"Its host starts festival events for everyone in it: an event's Details on the map, then
SELECT, or /event FR05 in World chat."

### The two-game check (2026-11-04)

A TU4 build of this branch, the local server from `server/` with `--game-db`, DriverA (host)
and DriverB (guest) on copies of the golden TU4 save, each on its own headless gamescope,
`tools/autodrive.py` at the wheel.

| Step | Result |
| --- | --- |
| The markers online | Both games logged `COOP MAP: the festival's events are on the map` on entering the world, and the refresh found 393 markers. The map showed the career's events as in single player (a trophy on the won ones, NEW on the new ones); under the cursor: "Chevron Sixties Speedway, A, 15,000 Cr" |
| The card | X DETAILS opened the festival card (FR50, MUST BE 1960 - 1969, BEST FINISH 1ST) with SELECT START CO-OP EVENT next to SET ROUTE, BACK, RESTRICTIONS, LEADERBOARD; the card's event id read 231 (FR50) |
| A guest's SELECT | B's card sent `/event 231`; B was told "Only the world's host (DriverA) can start an event" (the HUD notice) and nothing started |
| The host's SELECT | A's card sent `/event 231`: "DriverA starts FR50: A class, 1960-1969, 7 CPU drivers, 1 lap, Dawn" on both, `next round is campaign event 231 (FR50), 2 human(s), AI Medium (skill 40, temperament 6)`, the free roam ended from under the map's card. Lobby on both: CO-OP FESTIVAL POINT-TO-POINT RACE, Main Stage Run 4.31 km, DriverA and DriverB and six CPU drivers in Ford GT40s (A 582) |
| Both race | A finished 4th (driven to the end); B's car stuck at 15% and the race timer gave it 8th "finished 1" (as in the acceptance run). The host's write: relative scores 4 (A) and 0 (B). Ledger: `round 0e9171f81203a1c9 (FR50) closed with 2 result(s); DriverB place 8/8; DriverA place 4/8`; `GET /coop/results` offered each player its own result |
| The grants | Both launcher grants (record_session, then after_session): "applied 0, unchanged 1" (FR50 is won at 1st in the test save), confirmed; the server had nothing left for either |
| Back in the world | The markers loaded again in the world's next free roam (393), the card offered the start again, and `/event FR17` typed in World chat still started an event |

Not done: the base game (the card's hooks are TU4 only, like Custom Race's; a base world
keeps `/event`), a prompt at the event's start zone that starts the co-op event (its X does
nothing online), and a check of the guest's garage against the event before the start.

## Open questions (measure first)

Questions 1-5 are answered under "Milestone 1 progress", most of 6 under its phase B.

1. Does the game load a launcher-granted save cleanly and show the event done? Does it re-run
   its unlock and wristband checks at load, or does the grant have to write them? (A
   single-player run on the scratch save from the offline check.)
2. What does `XSessionWriteStats`'s `X_PROPERTY_RELATIVE_SCORE` hold for an online race: the
   finishing place among all cars, or a score? If it is the place, the server gets every
   human's place without client code.
3. Does `AIFieldId` select a field of named CPU drivers (an event's `EventParticipants`)?
   Send `AIFieldId="<event id>"` in a world event and look at the lobby's CPU names and cars.
4. Do `GridOrderingType="PIWithPlayerLast"` and a time of day work in an online round with
   two humans? Do `Checkpoint` (street) and traffic work online?
5. The `EventRestrictions` mask bits mapped to `CarRestrictions` attributes: decode
   `sub_826AE848` (TU4) against the 13 masks in use, and check that the lobby's car list
   matches the single-player ticket.
6. The exact cash payout of a career race: `CashPrize`, `EventScoring`, `CreditsMultiplier`,
   the race mode's `WinningsDistribution` and the difficulty bonus, all read by
   `sub_826C0C00`. The grant should pay what the single-player race would.
7. The co-op playlist's placement: friends only, the party session, or an invite code? Party
   membership is not on the server today.
8. Event names for the chat list: `Events.Name` is a string id (`_&202713995`); the names
   are in the game's string tables (`stringtables/*/careereventui.str` and others).

## Evidence

| What | Base | TU4 |
| --- | --- | --- |
| GameOptions reader (online hoppers, presets, career modes) | `sub_8260A4D8` | `sub_826B0DF8` |
| `CareerRaceModes.xml` parse | `sub_8260ABF8` | `sub_826B15A0` |
| Bucket `CarRestrictions` attributes | `sub_82603F90` | `sub_826A8E88` |
| `EventRestrictions` load / decode | `sub_8260A448` | `sub_826B0D68`, `sub_826AEFF8`, `sub_826AE848` |
| Event AI skill columns (`SELECT %s,%s,%s FROM Events`) | `sub_826098B8` | `sub_826B0170` |
| `EventParticipants` grid | `sub_8260AF78` | `sub_826B1C30` |
| Career event setup from an event id | `sub_82553ED0` | `sub_825C54B0` |
| Current career event id (global) | `0x832FEBB8` | `0x833ED728` |
| `CLoadIntoCareerRace` vtable, enter | `0x82082230`, `sub_8291DD80` | `0x820928C4`, `sub_829D0D90` |
| `CSetCareerEventId` vtable, enter | `0x82085494`, `sub_8294B2D0` | `0x82095B04`, `sub_829FE1E0` |
| `CGameTypeDecision` vtable, update | `0x8207FA54`, `sub_82909A38` | `0x820900F0`, `sub_829BC9F0` |
| Race components by race type | | `sub_826B7068` (table `0x82038170`) |
| `CCareerComponent` vtable | `0x82029E9C` | `0x8203852C` |
| Career component post-race update | `sub_82609DF0` (`+0x28`) | `sub_826B06A8` (`+0x30`) |
| Career results writer | `sub_82607BF8` | `sub_826ADDD0` |
| `CPostRaceUpdateProfile` vtable, enter | `0x820831BC`, `sub_82929AE8` | `0x8209382C`, `sub_829DCB88` -> `sub_825858F8` |
| `CTriggerEventUnlocks` vtable, enter | `0x8208BEE4`, `sub_82989FC0` | `0x8209C584`, `sub_82A37C70` |
| `INSERT OR IGNORE INTO CareerEventUnlocks` | `sub_824CD4A0` | `sub_8253B658` |
| Remaining wristband points of an event | | `sub_8253B230` |
| Points per place (`WristbandScoring`) | `sub_82602B40` | `sub_826A7A88` |
| `CashPrize, CareerEventStyle` | `sub_824C7390` | `sub_825353B8` |
| Post-race payout, stats | | `sub_826C0C00`, `sub_826C01E0` |
| XP / wristband tracker setup | | `sub_824BEF10` |
| Online game object | `0x83305CC4` | `0x833F4A80` |
| Online AI car pick (bucket filter) | `sub_828920F8` | |
| `EventRestrictions.Value` decoder (hex -> restriction) | | `sub_826A33B8` (rows: `sub_826AEFF8`) |
| `HorizonTimeOfDay` -> seconds after midnight | | `sub_82696DB8` |
| A CPU driver's level and its `MPAIDifficultyMap` rows | `sub_82568BD8` | `sub_825DEDD8` |
| Career CPU skill: `AISkill<level>` + `AIPlayers` modifiers | | `sub_826B0170` |
| Race completion unlocks (`CTriggerEventUnlocks`) | | `sub_82A35AC8`: `sub_8253B988` (`Rewards_EventUnlock`), `sub_82546080` (by XP), `sub_82542D30` (hubs) |
| Winnings split by `WinningsDistribution`, rounding | | `sub_8269BA50`, `sub_826984E8` |
