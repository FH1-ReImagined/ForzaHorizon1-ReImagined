# Online vision: a shared Horizon world

The long-term goal for online play, built in phases. Phase 1 (automatic shared free roam)
is in; the later phases are designs, based on what phase 1 showed. Notes from reverse
engineering that matter for them are collected at the end.

## The goal

Forza Horizon online where every online player is in the same world, MMO-like shared
free roam:

- Players join the game's events from the world with their own unlocked cars, under each
  event's conditions (class and car restrictions and so on), and race the other players.
- Free grid slots are filled with CPU opponents.
- The AI difficulty follows the share of humans on the grid: at most 1/3 humans gives an
  easier AI, around 1/2 intermediate, 2/3 or more harder. On an 8-car grid: 1-2 humans
  easy, 3-5 intermediate, 6 or more hard.
- Every online event gets a pre-race wait lobby. When a player starts or joins an event,
  the race waits 30 seconds for other players in the world to join before it starts, so
  by default it never starts with one human and an all-AI grid. When the 30 s run out,
  the free slots are filled with AI, whose difficulty is set from the final human share.
  *Assumption (not stated by the user):* if the grid fills with humans before the 30 s
  are up, the race starts right away.
- The game's own online pre-race lobby and countdown are the likely base for this; notes
  on them go under Findings.

## Phase 1 (built): the shared world, automatic placement

**How to play:** Multiplayer, Find Game, **Horizon World** (the first playlist). Nothing
else: the server either places you into an open world (its lobby offers **JUMP IN**, which
drives you into the running free roam) or you open a new one and drive at once, alone, and
the next players drop into yours. The world is an endless online free roam.

### The trigger: a "Horizon World" playlist in Find Game

The Forza Live server serves a third playlist (hopper 3, `server/forza_live_lsp.py`),
listed first, whose rounds are online free roam: `GameTypePreset="7"`
(`IDS_PresetFreeRoam` in `media/gametunablesettings/GameOptionPresets.xml`), the festival
as the starting location (track 1101), `StartType="OpenWorld"`, `MaxPlayers="8"`.

Why this trigger:

- It is how Playground built public free roam: the game's own fallback playlist (Quick
  Match, `DefaultPlaygroundHopperOptions.xml`) is the same preset 7 free roam through
  matchmaking. Find Game searches with `XSessionSearchEx`, which `live_xmsg.cpp` answers
  from the server, so the server decides who goes where, and the game itself joins the
  session it is given or hosts one when there is none.
- No setup: a custom game's Free Roam needs its host to go through Create Custom Game,
  Setup Game and Game Type first, and invites (the invite picker and the accept prompt, #90; the party
  lobby's Invite Friends) only reach a party whose leader has already set that up.
- It is two presses from the party lobby, and the playlist's options are server data:
  the round's GameOptions XML, its search settings and its intermission.

### Placement

- A host's client publishes its matchmaking session on the server (`MATCH_PUBLISH`:
  XSESSION_INFO, slots, properties, QoS data) under its playlist; the session's filled
  slots follow `XSessionJoin`/`Leave`. The playlist is the search property `0x10000030`
  (Forza's hopper id; Find Custom Game puts the game type tab's value there, free roam 8);
  before, the client looked only for Reach's `0x10000015`, so every Forza search saw every
  hosted session.
- A search (`MATCH_SEARCH`) gets the sessions with a free public slot, **the fullest
  first**, then the oldest: players gather in as few worlds as possible, and a full world
  (8/8) is never offered. The game joins the first one it reaches
  (`maxConnectAttemptsBeforeCreating` 2, `maxConnectSecondsBeforeCreating` 10 in the
  world's search settings) and hosts a new world otherwise.
- The first player in an empty world drives at once: its lobby starts the 5 s
  intermission with one player and loads the world (see "Solo start" below; before, it
  waited with "WAITING FOR PLAYERS TO JOIN..." until a second player came).
- The server logs every decision (`match #id 'name' playlist 3: 1 open session(s), first
  <session> from #host (1/8)` or `no open session, it hosts`), and its status page lists
  each player's published session (`match`: playlist, players, slots).
- Car clubs (#145, `server/forza_club_world.py`): the server cannot move a game that is
  already in a world; it only orders the answer to a search. For a player in a car club the
  open worlds with one of its online clubmates come first (the one with the most clubmates,
  then the fullest as above); the line then ends `, N clubmate(s) in it`. A full world is
  not offered whoever is in it, and a player without a club, or without a clubmate in an
  open world, is placed as before. `/club join [gamertag]` (a chat command in any channel)
  puts one clubmate's world first for 10 minutes. This was chosen over a "join my club's
  world" button because the game's own Join Game (friends screen) needs a presence-joinable
  session and a Find Game world is not one (`social_presence.h`), and over a forced move
  because there is none: the player opens Find Game > Horizon World again. Only the
  Horizon World playlist is ordered; the Co-op Festival keeps its friends-only placement.
  Tested with one TU4 game and three scripted players on a local server: the game (in club
  TST) searched with a 5/8 world of a player outside the club and a 2/8 world of a clubmate
  open, and was offered the clubmate's first; `/club` listed the clubmates and where they
  were, and the Club channel carried messages both ways between the game and the club's
  members only.

### Joining a world in progress: drop-in (`src/hooks/world_drop_in.cpp`)

Find Game runs its games differently from a custom game, and the difference is a mode
of the online game (the object at `*(0x83305CC4)`, mode at `+40`, set by `sub_828733A8`
when a game starts): Find Game is 7, Find Custom Game 10 (`sub_8287F2B8` picks 7 or 10 from
the matchmaking kind), a custom game's host 8 (race) or 9 (free roam). Three things follow
from it, and in the Horizon World (playlist 3, a free-roam round, mode 7) the client
(`world_drop_in`, on by default) now gives each the custom free roam's behaviour:

1. **Join in progress.** `sub_82865340(mode)` is "custom game" (8, 9, 10). Only then does
   the lobby of a game in progress say FREE ROAM IN PROGRESS (lobby state 10, the label
   picked at `0x8281B358`) and offer **JUMP IN** (button 6 of the lobby's table, handler
   `0x826EE1D0`; its filter needs "custom", and `0x82701018` makes it available), does
   the host accept the joiner's ready flag while the game runs (`0x82893B1C`), and does
   every peer add the car of a player whose status becomes "in game" (`0x8288DCD4`). In
   Find Game the joiner sat in "GAME IN PROGRESS / WAITING FOR PLAYERS TO FINISH GAME".
   The hook answers "custom" at those call sites only (by return address, plus
   `0x82624B00` in the free roam); the lobby's other buttons and the game's options
   (`0x828837A4`: the hopper's GetGameOptions, not a custom setup) stay Find Game's.
   From the code (not measured): JUMP IN is offered once the free roam has run
   `Online/Multiplayer/MinimumTimeToJumpInToFreeRoam` (20 s, `GlobalRegistry.xml`); the
   lobby counts that down (`sub_82819670`).
2. **The flow.** The launch (`sub_8286A2E0`) starts a custom game as race type 18, the
   online free roam flow (`game_start_multiplayer_playground` in `gamemodes.zip`: co-op
   challenges, joiners placed into the running world through `set_car_to_joining_session`),
   with HUD mode 17; everything else as race type 6, an online race (the TIME/RACE/LAP
   timers and PLACE 1/2 phase 1 saw, a timed end) with HUD mode 21. The type is the race
   settings' `+44` (`sub_82C0F2C0`, read by `CGameTypeDecision`, `0x82909A38`: 18 =
   multi_player_free_roam, 6 = multi_player_race). With only the join fixed, a player who
   pressed JUMP IN in a race-type world stayed on the loading screen. The hook on
   `sub_825548A0` (called from the launch) makes the world type 18 and HUD mode 17.
3. **Solo start.** The host's lobby (`sub_8287F670`) starts the intermission once
   `sub_82876D90` says ready: enough players (`+2016`, the search settings'
   `minPlayersToLaunch`, 1 in the world), nobody still joining, and in Find Game the
   team/bucket check done (`+2148`, `sub_82895018`, run by `sub_82875FB8` when `+2152` is
   set, which happens when a remote player's status changes). A host alone never had it
   run, so it waited for a second player. For a world host alone in its lobby the hook on
   `sub_82875FB8` sets the flag; the check runs, the 5 s intermission starts and the
   world loads with one player.

Measured (two instances): A alone picked Horizon World; the world launched after the 5 s
intermission and A was driving about 40 s after picking it, with the free roam HUD ("SELECT A CO-OP CHALLENGE FROM THE PAUSE MENU", no timers or place).
B picked it, was placed into A's world, saw FREE ROAM IN PROGRESS and JUMP IN, and drove in
the world about 30 s after pressing it (B's HUD "WAITING FOR HOST TO SELECT A CO-OP
CHALLENGE", both names in both lists, A's car with its tag in B's view after A drove up).

The world has no timed end now: the free roam flow ignores the round's
`<EndConditionOptions Type="Time">` (a 60 s round served from `hoppers/3.xml` ran on
past 2 minutes, no TIME in the HUD). So the server's rounds are endless by default
(`--world-round-minutes 0`); the option is kept for clients with `world_drop_in = false`,
which get phase 1's race-type world where late players wait for the round's end. A round
still ends when the host picks **END FREE ROAM** in the pause menu ("All players will
return to the lobby"): everyone goes back to the lobby, the intermission runs and the
next round loads with everyone (tested; the host asks `GetGameOptions` for it, so the
next round is the server's choice). Players (host or not) can QUIT at any time.

### Session cap: 8

- The world's size is the round's `MaxPlayers` (a byte in the game options, offset 24 of
  the block `sub_8260A4D8` serializes): the game creates its session with that many public
  slots. Served 16, the host created `16 public + 0 private slots` and its lobby opened
  normally.
- Everything the game makes itself is 8: the custom game's MAX PLAYERS option stops at 8,
  the party session has 8 slots, Turn 10's playlist XML says 8, the bucket's `MaxPlayers` 8,
  and the co-op challenges are written for 8 ("8 PLAYERS MUST COMPLETE THE CHALLENGE...").
- Raising it: the session side takes any count (the XML), so the question is whether the
  engine's per-player state holds more than 8: lobby player lists, the replicated cars and
  `CarsPerUniverse` (Tracks, 0 for the festival tracks) are the places to read. Not tried,
  and not testable on this machine with 2 instances; keep 8.

### When the host leaves

- The game migrates by itself: the remaining player keeps driving, now as the host (its
  HUD switches to the host's "SELECT A CO-OP CHALLENGE FROM THE PAUSE MENU"), the list
  drops to 1/1, and the world goes on. It calls no `XSessionMigrateHost`; the guest's
  session object is not touched at all (traced with `FORZA_NETTRACE`). The leaving host's
  client withdraws its session, so nobody advertised the world any more: the next player
  opened a second world and the two players ended up apart.
- Fix (re-placement): every player in a world someone else hosts publishes a **standby**
  record of it (`MATCH_PUBLISH` op 2: the session's info with its own address as the host,
  the playlist and QoS data it joined with; op 3 withdraws it when the game deletes the
  session). The server advertises a session's standby only while nobody hosts that
  session id: once the host left, searchers are placed into the remaining players' world,
  advertised by its oldest remaining member with the number of members as its filled
  slots. Tested: A (host) quit, B kept driving alone; A picked Horizon World again and the
  server placed it into B's world (`first 86f19a2a1352a5db from #1206 'DriverB'`), B's
  game accepted the join as the new host, and both drove together after the round. With
  drop-in A now jumps straight into B's world (test 9).
- Which member the game makes its new host (with more than two players it need not be
  the oldest) is visible in the game: the online game's host flag (`+344`,
  `sub_82865378`) turns on for it. The client watches that flag every second while it
  publishes a standby (`forza::LiveMatchTick`, `OnlineGameIsHost` in
  `world_drop_in.cpp`) and then sends its standby as op 4 ("standby of the game's host";
  op 2 again if the flag goes off); the server advertises an op 4 member before the
  oldest one. Tested with two players: A (host) quit, B's flag turned on within a second
  (`the game made us the host of the session we joined`, server: `DriverB is now the
  game's host of ...`), and A was placed into B's world. With three players: see "Three
  players" below.
- A host whose game was closed stays registered until it times out (30 s); the game moves
  the host after about 16 s. A session that a member reports to host (op 4) is then no
  longer advertised under the player that published it, and the latest member to report
  it leads (`server/forza_world_host.py`; see "Three and four players").
- `XSessionMigrateHost` (0xB001E) is handled anyway (the SDK left the info untouched): the
  new host gets its own address in the info and publishes the session as its own.

### Tests (two instances, private server on UDP 21600 / HTTP 21601)

1. B joins A's world automatically: A picked Horizon World and hosted; B picked it and was
   placed into A's world; both loaded free roam at the festival with DriverA and DriverB
   in the list (`screenshots/2026-10-11_mmo_01_world_A_top_B_bottom.jpg`).
2. A leaves: B stays in the world as its host; A picks Horizon World again and is placed
   into B's world (`..._02_host_A_left_B_hosts_A_placed_back.jpg`, A in B's lobby).
3. Rejoin: after the round, A drives in B's world
   (`..._03_A_rejoined_B_world.jpg`). Earlier, B quit and picked Horizon World again and
   joined A's world (placed into it, then waited for the round).
4. Rounds: a 90 s world round ended into the lobby and the next round
   (`..._04_timed_round_then_lobby.jpg`).
5. Join in progress in a custom free roam: B jumped into A's running custom free roam
   (`..._05_custom_free_roam_jump_in.jpg`).

Drop-in (`world_drop_in.cpp`, server rounds endless):

6. Solo start: A alone picked Horizon World and drove in the world after the 5 s
   intermission, free roam HUD (`..._07_solo_start_free_roam_hud.jpg`).
7. Drop-in: B picked it, was placed into A's world, its lobby showed FREE ROAM IN
   PROGRESS and JUMP IN (`..._08_find_game_world_jump_in.jpg`); B pressed it and drove in
   A's world about 30 s later (`..._06_drop_in_A_top_B_bottom.jpg`).
8. Seen: after A drove up to Race Central, B saw A's car with its tag
   (`..._10_B_host_sees_A_car_after_jump_in.jpg`).
9. Host leaves: A quit; B's game made B the host (op 4 to the server); A picked Horizon
   World, was placed into B's world and jumped in
   (`..._09_host_left_A_jumped_into_B_world.jpg`).
10. END FREE ROAM (host B): both went to the lobby and loaded into the next round together
   after the intermission. A 60 s timed round did not end the free roam (2+ minutes).

## Phase 2 (built): events from the world, a 30 s wait lobby, CPU drivers

**How to play (operator):** run the server with the game's database (default
`extracted/game/media/db/gamedb.slt`) and either `--world-event-minutes N` (every world
gets an event after N minutes of free roam) or ask for one: `curl
'http://SERVER:HTTP_PORT/world/event?session=all'` (or `session=<id>` from the status page's
`worlds`, `&event=<Events.Id>` for a given event; the endpoint has no authentication,
keep the HTTP port private). The players in that world are taken to the event's lobby,
wait 30 s (others who pick Horizon World meanwhile join them), race the event, and go
back to the free roam after it. CPU drivers fill the grid at any human count (their
level follows the human share, see below). Server
options: `--world-events 4,26` (pick from these `Events.Id`), `--world-event-laps N`,
`--world-event-ai N` (`MaxAIPlayers`, default 7), `--world-event-players N` (grid, default
8), `--game-db PATH`. Client cvars (`[Network/Forza Live]`): `world_events` (on),
`world_event_wait_seconds` (30), `world_event_cpu_with_peers` (on; off: only a solo
event gets CPU drivers), `world_event_cpu_ready` (on: the results screen doesn't wait for
the CPU drivers), `world_event_ai_grid` (0; a test knob, below).

CPU drivers online are this project's addition and exist only in these world events and
the Co-op Festival's events (#67). The original game's online races have none: every online preset in
`GameOptionPresets.xml` and both default playlists (`Default*HopperOptions.xml`) set
`MaxAIPlayers="0"` (the Playground presets even lock it), and Create Custom Game's GAME
SETUP (RULES, ADVANCED RULES, OVERRIDES) offers no AI option (checked in the game; the
lobby's `IDS_OptionMaxAIPlayers` / "REMOVE AI PLAYER" strings, dated 2011, are unused
in it). The server's playlist rounds keep `MaxAIPlayers="0"` too. The launcher's
"This PC" server schedules no world events; it offers the Co-op Festival, whose `/event`
races a festival event with CPU drivers, alone too. That needs the game's database, which
the launcher passes to it (`--game-db`, its game folder's `media/db/gamedb.slt`) since #67:
the server only finds it itself in a source checkout, and without it lists no Co-op Festival.

Checked again 2026-10-25 (two TU4 games, a local server; the game's own options for the
round, `FORZA_EVENT_TRACE`): a custom Circuit Race (`max AI 0`; GAME SETUP's three pages
have no AI option) and the Races playlist (`max AI 0`) raced with the two humans only (PLACE
1/2, 2/2); a Horizon World event had 2 humans and 6 CPU drivers (PLACE 8/8); and on a server
started the way the launcher's "This PC" one is, the Co-op Festival was missing from Find Game
without the database and listed with it, where DriverA alone sent `/event FR05` in World chat
and raced 7 CPU drivers (`1 human(s) of 8: CPU drivers Medium (campaign event)`, PLACE 8/8;
`screenshots/2026-10-25_coop_festival_solo_this_pc_7_cpu_drivers.jpg`).

**Traffic.** The world's rounds say `UseTraffic="false"`, as the game's own free roam preset
(`IDS_PresetFreeRoam`) and its Quick Match playlist do, and the online free roam follows it:
with `false` the host's car list held no traffic car, and with `UseTraffic="true"` (served
from `DATA_DIR/hoppers/3.xml`) the game made its pool of 16 traffic cars (car list kind 4) and
civilians drove on the roads around the host (a pickup and a bus ahead of it,
`screenshots/2026-10-25_world_traffic_on_usetraffic_true.jpg`). Each console
spawns its own: DriverB's game, in the same world, listed DriverA's car but none of the 7
traffic cars around it (its own pool stayed unplaced at the festival), so a car one player
meets is not there for the others. The server keeps it off; an operator can serve
`UseTraffic="true"` in `hoppers/3.xml` with that caveat.

Code: `server/forza_world.py` (events, world state, the AI rule), `forza_live_server.py`
(`WORLD_EVENT` message, `/world/event`, the `worlds` of the status page),
`forza_live_lsp.py` (`GetGameOptions` answers the event), `src/hooks/world_events.cpp`
(the client side), `net.cpp` (message 18).

### How a round's options reach the game (traced)

- The session host asks `Hopper.GetGameOptions` for the **next** round when a round
  starts: `sub_82875840` (round start, every peer) -> `sub_8286E660` -> host only
  `sub_8286A800(game, 0)`: round counter `+0x7DC` + 1, hopper `+0x758`, the requester
  object `+0x870` (vtable `0x820763B8`, `sub_82869690` sends it; `roundNum` = counter +
  `+0x7DE`, or random with `+0x7F0`). The answer lands in the online game's next-round
  options (`+0x488`, 0x2D0 bytes, flag `+0x484`) and becomes the round's options
  (`+0x1B4`, via `sub_8287E538` from `sub_8287F5D0`) when the players are back in the
  lobby. So the lobby after a round already shows the next one, and the first round of a
  playlist is the one asked for when it was picked (`sub_8287F2B8`).
- Vote to skip (`sub_82896178`) asks again with `sub_8286A800(game, 1)`; the event path
  uses the same call.
- Game options fields (`sub_8260A4D8` serializes them, offsets from the options):
  `GameTypePreset` +0, `TrackId` +4, `MaxPlayers` +0x18, `MaxAIPlayers` +0x19,
  `AIFieldId` +0x1C, `AIDifficulty` +0x296 (Easy 0, Medium 1, Hard 2, Pro 3: the rows of
  `MPAIDifficultyMap`), `TimePlayerAFKUntilKick` +0x2C0. In the online game add 0x1B4
  (preset `+0x1B4`, max players `+0x1CC`, max AI `+0x1CD`, AI difficulty `+0x44A`).

### CPU drivers in an online lobby (step 1 of the plan)

- The game has them. The host keeps `min(MaxPlayers - humans, MaxAIPlayers)` CPU players
  in the online lobby (`sub_8287BBE0`, run when options are applied, `sub_8287BDC0`, and
  when a player's status changes): each is a player of the player manager
  (`sub_82577AD8`, singleton `0x832E681C`) with the CPU flag (status record +7 = 1,
  `sub_82881E30`), a name from `AIPlayers` ("(CPU) Ben Andrews") and a car of the bucket's
  class (`sub_82877240`); `sub_82877470` gives each the round's `AIDifficulty`
  (`sub_82568BD8`). Every peer lists them in the lobby with their cars (seen with
  `MaxAIPlayers="6"`: 2 humans and 6 CPU drivers in both lobbies). Turn 10's XML always
  says `MaxAIPlayers="0"`; `IDS_SS_OptionNumAIPlayers` hints at a custom-game option.
- The CPU players count for the launch: a host alone with CPU drivers is "enough players"
  and launches (no solo-start hook needed for an event).
- **Into the race** they need one fix. At the round start (`sub_82875840`) the host sets
  every player local to it to "loading" (status 3), its CPU drivers included, but only
  its own player is set "in game" (4) when it has loaded (`CSetPlayerToInGame`,
  `sub_82942208`). The race waits up to 45 s for every player to be in game
  (`CWaitForAllPlayersToHaveLoadedToInGame`, `sub_828784C0`), then kicks the others
  (`CKickFailedLoadPlayers`, `sub_82878630` -> `sub_82876378`), and kicking a CPU driver,
  a player local to the host, throws the host itself out ("voted out", reason 3) or, when
  several such removals were queued, crashed it (see "Fixed on the way"). With the CPU
  drivers set in game together with the host's player (hook on `sub_8286A468`), they
  race: solo test, event 4 (track 324, 1 lap, class B), 7 CPU drivers on the grid, the
  host started 5th of 8 ("(CPU) Chris Ashton 1, (CPU) Alan Brewer 4, DriverA 5, (CPU) Joe
  Andershak 6"), CPU cars with their name tags ahead, AI passing and finishing, "RACE ENDS
  IN 0:19" after the first finisher, the results and rewards screens, then the lobby and
  the world's free roam again (`screenshots/2026-10-11_events_01_cpu_drivers_race.jpg`).
  After the race the results screen waits for every player to be ready to return to the
  lobby (`CWaitForAllPlayersReadyToReturnToLobby`, `sub_82878530`: no player at status
  3 or 4), up to 45 s; the CPU drivers never are, so the host sat about 50 s on "WAITING
  FOR OTHER PLAYERS...". In a world event the CPU drivers count as ready in that check:
  hook on its test `sub_828655E0` (only used there; cvar `world_event_cpu_ready`), their
  status stays 4. The results screen now goes on as soon as the humans are ready (two
  players: both lobbies about 4 s after the last human's finish instead of 45 s). Setting
  their status to 5 instead had worked for the wait but was followed by a host crash 4 s
  later ("Call to invalid or unregistered function at guest address 0", no stack) when the
  lobby removed them (status 6, `sub_8287DEC8`), which it does without trouble from
  status 4.
- **Two consoles: the CPU drivers are spread over them.** The host creates all of them
  (all flagged local on the host at the round start), and when the race loads it hands
  some of them to the peer, which runs their simulation (player +152, `vfunc 0x9C`: 3 of
  6 with two consoles; on the host those 3 are then not local). Clearing that flag on the
  peer made the race never end ("RACE ENDS IN 0:05" ran out and both kept racing): the
  peer has to run its share. A player's lobby status is its owner's to set: on the host
  the peer's CPU drivers show whatever the peer last sent (0, 3 or 4), which is enough for
  the race's load wait. Every CPU driver on both consoles carries the host's
  `AIDifficulty` (checked with Medium and Hard, see below).
- **A second human didn't load the event** (it stayed in the lobby, the host's race
  waited 45 s and kicked it to the main menu, and raced with the CPU drivers alone). The
  round start (`sub_82875840`) loads the round only if none of the players this console
  flags local (player +152, `vfunc 0x9C`) is already loading or in game (status 3-5,
  `sub_828809B8`: a player who joined a game in progress). On a peer, CPU drivers carry
  that flag too (3 of the 6 in the test), and the host had just set them loading, so the
  peer skipped its load. Now CPU drivers don't count for that check (hook on the status
  getter `sub_82880A88` at that call, `lr 0x82875A10`), and a peer never sets a CPU
  driver's status. With that, both consoles load, race the event together with the CPU
  drivers (both "PLACE x/8", the CPU cars and their tags in both views) and see the
  results.
- **Fixed: back from a race with CPU drivers and two consoles, one console crashed or
  hung** (4 runs out of 4 before the fix). When the race unloads,
  `CRemoveAIPlayersOnUnloadComponent` (`sub_826337A8`) walks the player manager's players
  and removes **every** CPU driver through the manager's local-player removal (`vfunc
  0x14` -> `sub_82573660(manager, player, broadcast 1)`: the session's local-user removal,
  `0x834A8F64` `vfunc 0x14`, a broadcast that this console removed its player, then
  `sub_825723B0` -> `sub_8287DEC8`, status 6). With two consoles half of them are run by
  the other console: for those the local path is the wrong one (a player of another
  console leaves through the session, `sub_825761E0` -> the session's remote removal
  `vfunc 0x20` -> `sub_825723B0`; the game's own AI fill, `sub_8287BBE0`, calls
  `sub_82869CE0` first for a CPU driver another console runs), and the owner's own
  removal arrives on top. Turn 10 never shipped CPU drivers online, so this path was
  never run with several consoles. Traced (`FORZA_EVENT_TRACE`, two consoles, one
  event): the host's unload removed its 3 CPU drivers and the peer's 3 the local way, and
  in the same frame its game thread (the one running the session update,
  `sub_82CF78E0`) blocked for good in `RtlEnterCriticalSection(0x832DF040)` from the
  session's message pump (`sub_82CEC5A8`); `0x832DF040` is no critical section (the
  global allocator object; its words read "lock count 0, owner FFFFFFFF" all the time),
  so the pump was working on a corrupted queue. The earlier symptoms (the host's "invalid
  function at guest address 0", the peer's null node read in the same pump, the peer's
  hang) are the same corruption landing elsewhere. **Fix** (`world_events.cpp`, hook on
  `sub_82573660` when called from the unload, `lr 0x82633824`): at the unload each
  console removes only the CPU drivers it runs; the others go when their owner's removal
  arrives through the session (or come back to this console's ownership, after which the
  lobby's AI fill removes them the local way). Seen both ways: the peer unloading first
  (it skips the host's 3, the host gets the peer's 3 through the session), the host first,
  and both in the same frame (with the shorter results wait both consoles unload within
  0.1 s). `world_event_cpu_with_peers` is on by default since. The trace also showed the
  old "worker thread busy in the gamma ramp" lead was a red herring: that thread is the
  screen fade's, computing every frame. A `REX_FATAL` (such as the "invalid function"
  one) now logs the guest call chain too (SIGABRT handler in `fault_guard.cpp`).
- **Seen once (before the results-wait change; the run's shared memory files had been
  unlinked by another job's cleanup):** both humans finished (status 5) in the same
  second, the peer went to the lobby 49 s later (the old 45 s wait) and removed its CPU
  drivers, the host got their removals at the moment its own 45 s ran out and stayed on
  the results screen (status 5, in game) for 3+ minutes. Not seen in the 11 two-console
  and 4 solo events with the shorter wait since. If it comes back: a screenshot of the host's screen, and whether
  `CWaitForAllPlayersReadyToReturnToLobby` (`sub_82878530`) passes.

### The flow (built)

1. **Request.** The server marks a world (its session id) "event pending" and sends
   `WORLD_EVENT` (UDP message 18: op 1, event id u32, session id) to the world's host
   every 2 s (its published session, or the standby host after a migration); a request
   not taken in 2 minutes is dropped.
2. **The host takes it** (`world_events.cpp`, hook on `sub_825ADB30`, the update of
   `CIsFreeRoamSessionOver` which every peer runs each frame of the free roam): it asks
   for the next round again (`sub_8286A800(game, 1)`); the server answers that
   `GetGameOptions` with the event (state "next"); once the next-round options are the
   event (`+0x484` set, `+0x488` preset not 7; or after 15 s) the host ends the free roam
   the way END FREE ROAM does: its own player's status becomes 5 (`sub_8286A468`; status
   5 is what `CSetPlayerReadyToReturnToLobby` with the host flag sets, `sub_82942198`).
   `CIsFreeRoamSessionOver` on every peer reads the host's status 5 (`sub_825ADB30`:
   `sub_828752B8` = the host's player) and everyone returns to the lobby.
3. **The wait lobby.** In the lobby the event is the round. While an event round of the
   world playlist is in the lobby, the intermission (`+0x7E4`/`+0x7E8`, copied from the
   playlist's search settings, 5000 ms for the world) is `world_event_wait_seconds` (30)
   on every peer (hook on the lobby update `sub_8287F670`), and the world's 5 s come
   back for the next free-roam round. The host starts the countdown once the lobby is
   ready (the CPU drivers make it ready at once); `INTERMISSION 30` counts down in every
   lobby. Players pick a car with SELECT CAR under the bucket's restriction: a car that
   doesn't qualify is swapped by the game for one that does (both test players' S-class
   cars became a B-class Nissan GT-R R34 for a class 4 event). A grid full of humans
   (humans >= `MaxPlayers`) starts at once: the hook sets the launch time (`+0x4C`) to 1,
   which the lobby update takes as passed (not tested: needs 8 players).
4. **The race**, then the lobby: the event round's own start asks for the next round,
   which the server answers with the world (the event is consumed); after the race the
   lobby launches the world round (5 s) and the free roam goes on.

### Tests (private server on UDP 21600 / HTTP 21601; event 4, 1 lap, `--world-events 4
--world-event-laps 1`; the event requested with `curl .../world/event?session=all`)

1. **CPU drivers online (solo):** A alone in the world; the event: the free roam ended,
   the lobby showed the event with 7 CPU drivers and INTERMISSION counting from 30, the
   race had all 8 cars (A started 5th of 8, CPU cars racing ahead with name tags, "RACE
   ENDS IN" after the first finisher), results and rewards, then the lobby (5 s) and the
   world's free roam again with A's own car
   (`screenshots/2026-10-11_events_01_cpu_drivers_race.jpg`). Later, 4 events in a row
   in one session, all clean; the results screen went to the lobby 4 s after A's finish
   (was about 50 s).
2. **Two players, CPU drivers:** A hosts, B jumped into A's world; the event took both to
   the lobby (both lobbies: DriverA, DriverB and 6 CPU drivers, both cars swapped for a
   class B car), both loaded (after the fix above) and raced with the CPU drivers (both
   screens "RACE ENDS IN 00:04", A 7th, B 8th behind CPU drivers), results, lobby, the
   world's free roam. Several events in a row in one session (script: world, B jumps in,
   then event -> race -> back to the world, checked on both consoles, 3 per session):
   before the unload fix the host hung at the first one; with it 11 of 11 clean in four
   sessions (Easy, Medium and Hard via `world_event_ai_grid`; the last session with B
   joining during the first event's wait, below), plus 1 of 2 in an earlier session (the
   "seen once" stall above).
3. **Two players, late join, no CPU drivers** (`world_event_cpu_with_peers` off): A alone in the world, the event, B picked
   Horizon World 6 s into the 30 s wait: the server placed B into A's session, B was in
   the lobby 2 s later, the CPU drivers left (2 humans), both raced (A saw B's car), both
   went back to the world's free roam together, A hosting
   (`screenshots/2026-10-11_events_02_two_players_late_join_race_world.jpg`). With
   `world_event_cpu_with_peers` on: B picked Horizon World during the wait, the host's AI
   fill went from 7 CPU drivers to 6, the race split them 3/3 over the consoles, both went
   back to the world, and a second event with both was clean too.
4. A world request reaches the host 1 s after the `curl`; the host asks for the next round
   again and the server answers with the event 0.4 s later; the free roam ends at once.

Not tested: a grid full of humans starting early (needs 8 players, or
`--world-event-players 2` with two), the schedule (`--world-event-minutes`; its state
machine was unit-tested in Python), three or more consoles.

### Players leaving during an event (two consoles)

A player "quits" by closing the game (the process exits at once, as with the window's
close button: no goodbye to the session; the others' sessions drop it about 15-20 s
later). Script `t10.sh` (B jumps into A's world, then steps such as `race:peer`,
`lobby:host`, `free:peer`, `rejoin:gone`; the roles come from the game's host flag),
each step checked on the console that stays: alive, back in the world's free roam, the
world's host, and the world still advertised by the server under its name (status page
`worlds`). No fix was needed; what the game does:

- **The peer quits mid-race while it runs 3 of the 6 CPU drivers** (`race:peer`): the
  host races on; about 15 s later the session removes the peer and its 3 CPU drivers
  (`sub_825761E0`, "session removes ... cpu 1 local 0"), which come back to the host
  (at the race's unload they are local on the host and are removed the local way with
  its own 3). Results, lobby, the world's free roam, A still the host and advertised.
- **The host quits mid-race** (`race:host`): the peer races on, becomes the game's host
  (op 4 to the server: `worlds` names it), finishes, and goes back to the world as its
  host.
- **The host quits in the 30 s wait lobby** (`lobby:host`): 19 s later the peer's
  session removes the host and the host's 6 CPU drivers; the peer becomes host, its AI
  fill makes 7 new CPU drivers ("1 human(s) of 8: CPU drivers Easy"), the 30 s wait runs
  again, and it races the event alone with them, then back to the world as its host.
- **The peer quits in the wait lobby** (`lobby:peer`): the host launched before the
  session had dropped the peer (it went 22 s later, during the load); the host raced
  with its 6 CPU drivers and went back to the world.
- **A player quits in the free roam 5 s after an event** (`free:peer`, `free:host`): the
  other keeps driving in the world, as its host, still advertised.
- After each, the one who quit started the game again, picked Horizon World, was placed
  into the remaining player's world and jumped in (`rejoin`).

Clean / runs (nine sessions; no crash or hang in any; all but the first two steps with
the SDK overlay's patches 0028-0032):

| case | clean |
| --- | --- |
| the peer quits mid-race (it runs 3 CPU drivers) | 3 / 3 |
| the host quits mid-race | 3 / 3 |
| the host quits in the wait lobby | 3 / 3 |
| the peer quits in the wait lobby | 2 / 2 |
| the peer quits in the free roam 5 s after an event | 2 / 2 |
| the host quits in the free roam 5 s after an event | 2 / 2 |
| the one who quit comes back into the same world | 13 / 13 |

Not counted: two steps that the session's own time limit cut short (both games were
stopped by `run.sh`'s deadline during a rejoin; `t10.sh` now skips a step that can't end
in time) and one where the script quit the wrong console (a variable clobbered by a
loop).

Test scripts (copy and adapt; the scripts were scratch files and are not in the repo): `t7.sh` two
players, B jumps into A's world, then N events in a row, each checked on both consoles
(`st.py` reads the online game: mode, state, in game, players, host, preset, max AI, AI
difficulty) with a gdb dump of every thread on a hang; `t8.sh` the same solo; `t9.sh` B
joins during the first event's wait. `FORZA_EVENT_TRACE=1` logs every removal with its
guest call chain and the CPU drivers' owner and level. Related scripts: `t10.sh` (players leaving, below), `t11.sh`-`t13.sh`
(head-to-head challenges) and `carpos.py` (every car's kind and position, from
`/proc/PID/mem`).

### The events

From the game's database (`server/forza_world.py`): `Events` with `CareerTypeId` 1 or 2
(the festival road races; 4 are street races, 7/8/11/12 the showcases and specials),
`IsDLC = 0`, joined with their first `Races` row (track, laps) on a track with
`VisibleInMP = 1` and `RibbonConfiguration` 0 (circuit, `GameTypePreset` 1) or 2 (point to
point, preset 2): 70 events. The bucket's `CarRestrictions CarClassId` is the event's
`TargetClass` (a `CarClasses` id: 2 D, 3 C, 4 B, 5 A, 6 S, 7-9 R3-R1). Not decoded yet:
`EventRestrictions.Value` (hex: a 32-bit field mask, a byte, then the values; e.g. mask
`0x00030000` with `000007BC 000007C5` = years 1980-1989), which carries the make/year/
drive restrictions the career events show. `--world-events 4,26` limits the pick,
`--world-event-laps N` overrides the laps (tests), `--world-event-ai N` sets
`MaxAIPlayers` (default 7: CPU drivers fill the 8 places at any human count).

### AI difficulty from the human share

`AIDifficulty` follows the human share of the 8-car grid: at most 1/3 humans Easy, 2/3 or
more Hard, between them Medium (1-2 humans Easy, 3-5 Medium, 6-8 Hard). The server puts
it in the event's XML from the world's player count when the event is asked for; the
host's client sets `+0x44A` again from the lobby's human count before every
`sub_82877470` (each AI fill: whenever a player joins or leaves), so the level at the
launch is the one of the final grid. `sub_82877470` passes the level to every CPU
driver's driver data (player `vfunc 0x78`): `sub_82568BD8` stores it at `+0x25B` and the
level's `MPAIDifficultyMap` values at `+0x260`/`+0x264` (Easy 0x14/0x08, Medium 0x10/0x05,
Hard 0x06/0x03). Checked with two consoles (`FORZA_EVENT_TRACE` logs every CPU driver at
the round start and when going in game): 2 humans of 8 Easy; with the client test knob
`world_event_ai_grid` (the grid size the rule uses, 0 = the event's; the CPU drivers still
fill the 8 places) 4 gives "2 human(s) of 4: CPU drivers Medium" and 3 gives "Hard", and
all 6 CPU drivers on both consoles, those the peer runs included, carried level 1 (2) with
its values. (`--world-event-players N` on the server changes the real grid instead, and
with it the number of CPU drivers.)

### Fixed on the way

- **Kicked while racing crashed the host** (null read in `sub_82CF6288`). A local player
  removed from the online game (`sub_8286FC98`, reason 3 "voted out" or 7 "kicked for
  inactivity") ends it (`sub_8288B840` -> `sub_8287DA10`), whose session teardown
  (`sub_82CF7278`) pumps the session's queued messages (`sub_82CEC5A8`); a second
  removal message in the queue ended the game again inside it, tore the session down
  (`+0x684` = 0) and the outer teardown then read it. Nested removals are dropped
  (`world_events.cpp`). Seen with an idle host 50 s into a race with CPU drivers.
- **CPU drivers kicked = the host kicked.** `sub_82876378` (kick) on a player whose status
  record says local shows "voted out" and ends the online game: a CPU driver is the
  host's own player, so kicking one (the race's `CKickFailedLoadPlayers` after 45 s of
  `CWaitForAllPlayersToHaveLoadedToInGame`, which wants every player at status 4) threw
  the host out of its own session. CPU drivers are never kicked now, and they are set in
  game (4) when the host's player is (hook on `sub_8286A468`).

## Phase 3 (built): head-to-head challenges in the world

**How to play:** in the Horizon World's free roam, drive up right behind another player:
the text box at the top of the screen says "X: challenge the driver ahead". Press **X**.
That player gets "DriverA challenges you to a race / X accept B decline (10)" and has 10 s
to answer; a decline or no answer is reported back to the challenger ("DriverB declined
your challenge", "DriverB didn't answer your challenge"). On accept both get "vs DriverB:
race to the Wind Farm", the game's GPS route to it on the minimap (the green line, as
for a map waypoint), a 3 s countdown, then GO and the distance and direction to the
finish ("2.2 km, ahead, left"); the first car within 40 m of it wins ("YOU WIN: You beat
DriverB to the Wind Farm (2:31.4)" / "YOU LOSE"). Both just keep driving in the world.

**The route chosen:** the race runs **inside the free roam**, by our code and the server,
not through the game's race machinery. Both alternatives of the design notes disturb the
world: the online free roam's co-op challenges (`DriveFromAToBFreeRoamChallenge` in
`FreeRoamChallenges.xml`) are started by the session host for everyone in the session,
and a separate 2-player point-to-point round means leaving the world session and making
a new one (menus, matchmaking, the way back). An in-world race needs only each car's
position, so the other players in the world see nothing of it, and nothing in the
session changes.

### How it works

- **The cars** (`src/hooks/world_challenge.cpp`): the code of
  `CStoreRestorePlayerCarPosition` (`sub_82983A90`) reads the player's car as root =
  `*(0x832DF024)`, list = `*(*(root + 4) + 4)`, the list's vehicles a vector at
  `+0x80`/`+0x84`, car = `*(vehicle + 0x2D90)`, position = the car's vfunc 0x13C, which
  returns the vector at `car - 0xA20` (x, y, z; y up). The list holds every car on the
  console with its kind at `vehicle + 0x2DD0`: 0 this console's player, 1 a CPU driver it
  runs, 2 another console's player, 3 another console's CPU driver (a race with two
  consoles: A's 0/1/1/1/2/3/3/3 is B's 2/3/3/3/0/1/1/1, the same positions on both). The
  car's forward vector is at `car - 0xA40` (it matched the direction the car had moved
  in), its up vector at `car - 0xA50`. All plain memory reads, no guest calls.
- **Every frame of the world's free roam** (from the `sub_825ADB30` hook in
  `world_events.cpp`, on every peer): the client reads its own car and the other
  players' cars, sends its position, heading and speed to the server 4 times a second
  (`CHALLENGE` op POS), and shows the hint when another player's car is within the
  server's range and ahead (dot of our heading with the direction to it >= 0.5).
- **The pad** (`XamInputGetState` in `input_autopress.cpp` calls `WorldChallengePad`):
  X with a car ahead sends REQUEST and is kept from the game; without one the game gets
  X (in free roam X opens the LEADERBOARDS screen). While a prompt shows, X accepts and B
  declines, and both are kept from the game.
- **The server** (`server/forza_challenge.py`, message `CHALLENGE` = UDP kind 19, ops in
  the module's docstring) decides: on REQUEST the closest player in the same world (the
  session id of its hosted or joined Horizon World session) within `--challenge-range`
  (40 m) and ahead of the challenger; PROMPT to it; ANSWER, or no answer after 10 s; on
  accept START to both with the destination: the landmark whose distance from the two
  cars' midpoint is closest to 2.5 km (1.2-5 km preferred), from the endpoints of the
  game's own A-to-B free roam challenges (Carson Warehouses, Red Foot Ranch, Red Rock
  Outpost, Finley Dam, KH Outpost, the Steelworks, the Bunker, the Media Centre, the
  Lakeside Diner, the Carson fountain, the Wind Farm, the Beaumont cable car station,
  Beaumont Outpost; world x/z from `FreeRoamChallenges.xml`, in
  `media/gametunablesettings.zip` with zip method 21: `tools/unzip21.py` extracts it,
  with `tools/unxmem.c` built against the SDK's libmspack). Each
  client checks its own car against the finish after GO and sends FINISH; the first
  one wins (END to both). A racer who quits (no positions for 15 s, or gone from the
  server) loses by forfeit; one who leaves the free roam (LEFT: an event, the menu) too,
  unless the other leaves within 3 s (an event started in the world: called off). No
  winner after 10 minutes. The status page lists the `challenges` and every player's
  last `positions`.
- **The GPS route** to the finish: done as `CSetSatNavDestinationPos` (`sub_829423B0`)
  and the map's waypoint (`0x826E04E8`) do it: components = `*sub_825F6FB0()` =
  `*(*(*(0x832DF024) + 4) + 0x4C)`, node = `sub_82CDC5A0(components[*(0x832FF090)],
  position in v1)` (the road network's node for a position; v1 holds x, y, z, 1, which
  `lvx` byte-reverses: x is lane 3 of the host register), then
  `sub_8299FB40(components[*(0x832FF054)], node)` (the sat nav's destination; node 0
  clears it). Set on the game thread when START arrives (the destinations carry their
  height for the node search), cleared at the end.
- **Native HUD** (#10, `src/hooks/challenge_hud.cpp`): the game's existing
  `CHudCombinedNotificationControl` carries a headline and caption for requests,
  countdowns, distance/direction and results. Its original fonts, placement and
  SHOW/HIDE animations render into the game frame, so `tools/live_shot.sh` captures
  include the prompts. Every message is still logged (`HUD: title | text`). Friends,
  chat and invite screens continue to use their separate overlay.
  Network/input threads publish a bounded message with a monotonic deadline; the
  native UI update copies it before calling any guest code. Repeated updates to the
  same message refresh its lifetime without replaying its animation. An ordinary
  game notification has priority; challenge text can resume after the control is idle,
  and expiry never hides a notification that replaced it.
- Settings: client cvar `world_challenges` (on); server `--challenge-range M`, tests:
  `--challenge-any-direction`, `--challenge-test-finish M` (the finish M ahead of the
  challenged car instead of a landmark), `--challenge-finish-radius M`.
  `FORZA_CHALLENGE_TRACE=1` logs the car position every 5 s and the race's HUD text every
  3 s.

### Tests (two consoles, B jumped into A's world; script `t11.sh`)

Server with `--challenge-range 3000 --challenge-any-direction` (the two cars start about
100 m apart at the festival):

1. A pressed X: B got the prompt; B pressed B: A "DriverB declined your challenge", B
   "Challenge declined".
2. A pressed X, B didn't answer: after 10 s both "challenge expired" / "didn't answer".
3. B pressed X, A accepted with X: both "vs ...: race to the test finish", the countdown,
   and (`--challenge-test-finish 5 --challenge-finish-radius 15`: A at the finish at GO)
   A "YOU WIN: You beat DriverB to the test finish", B "YOU LOSE: DriverA got to the test
   finish first", both still in the world (2 players, free roam).
4. B challenged, A accepted (no test finish): "race to the Wind Farm (-1768, -2248), 2277
   m", both minimaps showed the green GPS route ("road node 43103DA0"); then B's game was
   closed mid-race: 15 s later A "YOU WIN: DriverB left: you win the head-to-head" and
   A's GPS route was cleared (`screenshots/2026-10-11_h2h_01_gps_route_minimaps.jpg`:
   A's and B's minimaps during the race, A's after it).

5. The "behind" rule for real (`t12.sh`, `--challenge-range 150`, ahead only): after B
   jumped in, B's car was 139 m from A's and ahead of it (A facing 0.90, -0.45; B in
   that direction), so A was behind B. A's text box said "X: challenge
   the driver ahead", A's X sent the challenge (server: "#A challenges #B (139 m)").
   B's X found nobody ahead (A was behind it): no challenge, and the game got the X: B's
   screen showed LEADERBOARDS.
6. The pause menu (MAP, CO-OP CHALLENGES, ..., END FREE ROAM, QUIT) open for 6 s: the
   free roam's frame kept running (no LEFT), so pausing during a race doesn't forfeit.
7. A world event during a race (`t13.sh`): B challenged, A accepted (race to the Wind
   Farm), then the server started an event in the world: both clients left the free
   roam (LEFT), the server "both left the free roam: called off", both "Race called off",
   and both went to the event's lobby.

Seen on the way: in free roam X opens the LEADERBOARDS screen (B's screen during test 3
of the first run), hence X is taken from the game only with a car ahead.

### Open

- Real landmark finish (2026-10-12, not run): the cars need a driver that follows roads;
  `tools/autodrive.py` only replays a recorded lap or follows CPU cars. Idea left for a
  follow-up: a server test option for one fixed destination (a point on the Millennia path,
  which autodrive can follow from the festival) or an image-based follower of the minimap's
  green GPS line (the minimap is a perspective view, heading up, the route green).
  A circuit has its own AI route now (`tools/track_path.py`, #13: Cat & Mouse driven to its
  end, `docs/title_services.md`); a landmark needs a chain of road routes (`route_NNN.owt`,
  as the opening drive in `docs/rendering.md` follows two of them).

- Not tested with real driving to a landmark: scripted driving goes straight and hits
  the festival's barriers (in the first run A was stuck against the grandstand). The
  distance and direction text updates every 0.2 s from the same position. The left/right
  words take x as right of z (left-handed, y up): checked by steering left at full lock,
  the forward vector turned from +x through +z to -x (`t10.sh` step `steer`).
- The default range of 40 m was not used in a test (the cars start 90-140 m apart at the
  festival); test 5 used 150 m with the same rule.
### Native HUD implementation (#10)

The general notification layout uses `TEXT_NOTIFICATION_LINE_ONE` (offset 0x78)
as its large headline and `TEXT_NOTIFICATION_TITLE` (0x6C) as its small caption.
The second body line is hidden in this layout, so accept/decline instructions,
countdown and distance/direction go into the visible caption; single-line notices
and results use their title there. Native UTF-16 reference objects are constructed and retained through the
game's own string/binding functions; temporary guest scratch memory is freed after
each update. Incoming UTF-8 is bounded and malformed characters are replaced.
Ownership resets when the control binds, unbinds, or receives an ordinary notification.

Action messages explicitly request button artwork. The native `MiscIcon` and
`MailIcon` materials load the game's own `Controller/X_Button.tga` and
`Controller/B_Button.tga` through the original texture binding; no game artwork
is distributed. Their model caches place 20-pixel buttons on the caption's visual
centre, with equal gaps before the Accept/Decline labels. The native model-property
updater uses the same position, scale, opacity and dirty fields on disc (`82F22B08`)
and TU4 (`82FE3AB8`).
Original texture references are retained while borrowed, and resources and layout
are restored on response, expiry, a normal notification, or unbind. Countdown and
result messages remove the action buttons.

The race uses the native sat-nav route and destination marker. A plausible road
node alone is insufficient: `SetDestination` must return success. A tutorial can
lock the sat-nav, so the challenge temporarily borrows the destination and lock,
resets the tracker using the game's unlock helper, and restores both afterward.
Failed setup retries at 500 ms intervals. Results, leaving free roam and disabling
challenges schedule cleanup; the HUD update can finish restoration after the
world hook stops. A changed manager invalidates the old lease without touching
its former objects.

| Hook | Disc | Title Update 4 |
| --- | --- | --- |
| Bind | 0x827E8000 | 0x8289AF88 |
| Update | 0x827ED4D8 | 0x828A0608 |
| Unbind | 0x827E8098 | 0x8289B020 |
| Ordinary notification | 0x827EF050 | 0x828A2980 |

`forza-recomp/tests/challenge_hud_test.cpp` is a standalone C++17 test for message
lifetime, action-state transitions, request/countdown/race/result layout, Unicode
limits, and concurrent updates.
The `challenge-hud-tests` workflow runs it on Windows and Linux without game data.

Validation (2026-10-18): the standalone tests and full TU4 builds passed on Windows
(clang-cl 23) and Fedora Linux (clang 22); the disc bridge compiled on Windows and
passed a Linux syntax check. Runtime tests used disposable copied saves and a
private packet/input fixture in offline free roam, exercising the unchanged
challenge state machine. Windows ran on an RTX 5060 Ti (617.14); Linux ran under
WSL/Xvfb with Mesa llvmpipe (LLVM 22), asynchronous shader compilation and vsync
disabled. Game-frame captures show the native request/instructions and race
distance/direction on both platforms. These checks do not constitute a two-client
online race or a disc runtime test.

Button-artwork follow-up (2026-10-18): Windows and Linux captures show the actual
blue X/red B textures, native green sat-nav route and destination marker. Both
runtimes exercised accept/decline and results, restored the ordinary mail icon,
and kept that ordinary notification after challenge expiry. Direct guest-memory
checks on both platforms confirmed the pre-race destination and lock byte match
exactly after the result. Expanded action-state tests and clean TU4 builds passed
on both platforms; both changed disc bridges compiled on Windows and passed Linux
syntax checks. Initial Linux fixture attempts faulted during loading before any
button message. The successful run used a fresh disposable save and delayed
fixture activation until free roam loaded; the loading fault's cause was not
isolated. These remain offline fixture checks, not a two-client race.

## Likely building blocks

1. A self-hosted server that emulates Xbox LIVE sessions and presence plus Turn 10's
   services. RecompiledHaloReach's Reach Live (`server/`, `src/kernel/live_xmsg.cpp`,
   `src/kernel/net.cpp`) is the template: a virtual network with UDP hole punching and a
   relay, XSession*/presence/QoS answered from the server.
2. The server places players into persistent free-roam sessions automatically. Needs the
   engine's per-session player cap and what sizes it.
3. Campaign event definitions loaded by the online race mode.
4. Host-simulated AI drivers replicated to clients as remote cars.
5. AI difficulty set per race from the human share.

## What to look for (and what was found)

Open questions, to fill in as the reverse engineering finds them:

- Netcode entry points: XSession*/XNet*/QoS usage, the packet layer.
- The max-players constants and arrays (Forza Horizon's online free roam holds 8
  players; the session size is the round's `MaxPlayers`, see "Session cap" above).
- How online races are set up (playlists, event selection, grid building).
- Where the single-player AI difficulty ("Drivatar" difficulty setting) is stored and
  applied.
- How race grids are built and how many AI cars a grid can take.
- The game's online pre-race lobby: its countdown, who may still join, and when the
  grid is frozen.

### Findings so far

- XAM/Live imports the game uses (Xenia's import listing of `default.xex`): the XMsg
  family (`XMsgInProcessCall`, `XMsgStartIORequest[Ex]`, `XMsgSystemProcessCall`,
  `XMsgCompleteIORequest`, `XMsgCancelIORequest`), which carries the XSession*/XUser*/
  presence/friends calls as XAM app messages (XGI 0xFB, XLiveBase 0xFC), as in Reach.
  `XNetLogonGetTitleID`, `NetDll_XNetGetTitleXnAddr` are imported too.
- The game polls `XamXStudioRequest(0x1003, &out)` every 30 ms from code around
  `0x82AD3810` (callers `0x82AD3830` ... `0x82AEDA3C`); with Kinect (NUI) imports
  around it, it looks Kinect-related rather than online.
- Single-player festival races have an 8-car grid (a 4-lap circuit race: the player
  and 7 named AI drivers). Event tickets carry the restrictions (e.g. "R2, made 2000
  or later") and the rewards (points, credits). Before the start the pre-race menu
  offers START RACE / DIFFICULTY / OPTIONS / QUIT RACE: the AI difficulty is chosen
  per race there, a natural place to set it from the human share.
- The multiplayer game types are data-driven presets, and they already know about AI
  opponents. Field names next to each other in `.rdata` (`0x8202C2A0`..`0x8202C900`):
  `CarRestrictions`, `MaxPlayers`, `MinPlayers`, `CanStartEndOfRaceCountdown`,
  `ScoringMethod`, `NumPlayers`, `NumPlayerBuckets`, `CollisionMode`,
  `DamageDifficulty`, `UseTraffic`, `GameTypePreset`, `StartingGridOptions`,
  `FieldWindowOptions`, `EndConditionOptions`, `MaxRecommendedCarClass`,
  `MinRecommendedCarClass`, `DraftingEnabled`, `AIDifficulty`, `AIFieldId`,
  `MaxAIPlayers`. Code reading them: `sub_82605F80` and `sub_82606318` (`MaxPlayers`,
  `NumPlayers`), `sub_8260A4D8` (`MaxPlayers`, `AIDifficulty`). `MaxAIPlayers` and
  `AIFieldId` suggest the engine can already fill an online grid with AI; the vision's
  "fill free slots with AI" may only need the preset values changed.
- AI difficulty data lives in the game database (SQL queries in `.rdata`):
  `SELECT * FROM MPAIDifficultyMap` (with `AISkill`, `AITemperament`; loaded in
  `sub_825704D8`), `SELECT SkillModifier,AggroModifier, RubberBandModifier FROM
  AIPlayers WHERE Id=%u`, per-event columns `AISkill%s`/`AITemperament%s`/
  `AIRubberband%s` (`SELECT %s,%s,%s FROM Events WHERE Id = %u`), and
  `SELECT * FROM EventRestrictions WHERE EventId=%d` for the event conditions. The
  single-player levels are Easy/Medium/Hard/Pro (`IDS_AIDifficulty_*`) with
  `EasyDifficultyUpperBound`..`ProDifficultyUpperBound` and `DynamicDifficulty`
  (`sub_82521200`). An MPAIDifficultyMap row picked from the human share is the
  obvious hook for the 1/3, 1/2, 2/3 rule.
- Free roam online: strings `onlinefreeroam`, `SET_MODE_FREEROAM` (`sub_825E0C60`),
  `CIsFreeRoamSessionOver` (`sub_825B3968`), and the message "... player's console as
  the session is full." `SELECT CarsPerUniverse FROM Tracks WHERE Id='%d'` may bound
  the number of cars per world.
- XAM app 0xFE message `0x2B003` is sent once when the single-player game starts
  (Xenia answers success, "Games used in: 4D5309C9").

## Three and four players (#12)

### Checked 2026-10-22: four games

Method: four TU4 games (DriverA-D: copies of one Xenia save, each with its own
`XDG_DATA_HOME` and `live_identity.txt`), headless gamescope at 960x540 and 30 fps (D at 144
for the relay), all through the server's relay (`FORZA_SERVER_RELAY=1`), against a local
server (`--world-event-laps 1`; for the challenges `--challenge-range 3000
--challenge-any-direction --challenge-test-finish 60 --challenge-finish-radius 20`).

| Check | Result |
| --- | --- |
| Placement and JUMP IN for the 3rd and 4th player | Pass. C was placed `(2/8)`, D `(3/8)`, both pressed JUMP IN, and all four drove in the world with the other three in the HUD list (`screenshots/2026-10-22_world_4_players.jpg`). B and C picking Horizon World in the same second were both placed into D's new world. |
| The host leaves with three others | Pass in the game, fixed on the server. The game made B its host 16 s after A's game was closed (`is now the game's host`); B, C and D kept driving, B with the host's HUD. But a gone host stays registered until it times out (30 s), and a searcher in that time was placed with its record: D searched 0.1 s after C became the host of a world whose standby host B had been closed, was sent to B (`first ... from #2950 'DriverB'`), the join failed and D opened a world of its own. Fixed (below); after the fix, B and later A searched less than a second after the game's new host took over (once after a standby host was closed, once after the player that had published the world), were placed with the new host and jumped in. |
| A world event with 3 humans | Pass, led by the standby host B: the 30 s wait lobby (INTERMISSION, 3 humans and 5 CPU drivers, 1 lap), `3 human(s) of 8: CPU drivers Medium` (server: `AI Medium`), the grid and the race with 8 cars, the results, and the same world's free roam again (`screenshots/2026-10-22_world_event_race_3_humans.jpg`). Also with 4 humans: 4 CPU drivers, Medium, each console running one. |
| Joining during an event race | A, placed into the world during the race, waited (GAME IN PROGRESS, WAITING FOR PLAYERS TO FINISH GAME) and came into the free roam with the others after it. Seen: the host's AI fill ran at A's join and one CPU car left the running race (PLACE x/8 became x/7). |
| Head-to-head with others nearby | Pass, two at once in a world of four: D challenged A and B challenged C 2 s apart; both accepted, both races ran (countdown, GPS route) and ended (`C wins in 0:13.7`, `D wins in 1:03.5`), and nobody else got a prompt (`screenshots/2026-10-22_world_4_players_two_h2h.jpg`). A challenge not answered in 10 s expired. |
| Server restart with four in a world | Pass: `restored 4 player(s)`, the world with its 4 humans and its host. |
| Relay budget | Nothing dropped in any run. Measured below; the budget is raised for 240 fps. |

What the server relayed per player (payload, KB/s, over 17-25 s):

| Case | Per sender | Per peer | Bytes per datagram |
| --- | --- | --- | --- |
| 3 players, free roam, 30 fps | 5.9 | 3.0 | 100 |
| 4 players, free roam, 30 fps | 8.9 | 3.0 | 100 |
| 4 players, free roam, D at about 120 fps | D 36.6 | 12.2 | 100 |
| 4 players, event race (4 CPU drivers), 30 fps | 17.5-17.8 | 5.9 | 192 |
| 4 players, event race, D at about 140 fps | D 81.2 | 27.1 | 192 |
| 4 players, the event's wait lobby | 0.4-1.5 | | |

A game sends each peer about one datagram a frame, about 100 bytes per car it runs: its own
car, and in an event its share of the CPU drivers (one each with four humans and four CPU
drivers). Driving or standing made no difference. So a player in a full world of 8 (7 peers,
one car each) makes the server send about 7 x fps x 108 bytes (with the relay header): 22 KB/s
at 30 fps, 106 KB/s at 144, 181 KB/s and 1680 datagrams a second at 240. The old limits (160
KB/s per player, 1500 datagrams/s per address) held up to about 200 fps; the defaults are now
256 KB/s (`--rate-limit`) and 2500 datagrams/s. Only players without a direct path to each
other use the relay. D, set to 144, ran at 115-140 fps with four games (and two more) on this
8-core machine.

Fixed (server, `server/forza_world_host.py`): a session that one of its members reports to
host (op 4) is no longer the session of the player that published it, and among the members
that report it, the latest to become the host leads. Searchers and world events reach that
player at once instead of after the gone host's timeout. Left: the game itself takes about
16 s to notice that its host is gone; a player who searches in that time is still placed with
the gone host, fails to join and opens a world of its own.

Tests: `server/test_forza_world_players.py` (a closed host's record and two hosts in a row
give way to the game's latest host, a claim in another world changes nothing, a minute of a
full world at 240 fps through the relay loses nothing).

Not checked: 5 to 8 players (this machine holds four games), a grid full of humans starting
early, a player at 240 fps.

### Checked 2026-10-16: three games

Method: three TU4 games (DriverA, B, C; copies of one Xenia save, own `XDG_DATA_HOME` each,
30 fps, scale 1) against a local server (`--world-event-laps 1`). Public server, last 24 h of
`docker logs` (since its restart at 11:01): 8 worlds hosted, none with more than one player
(`hosts ... (1/8)`), no standby records, no errors: nothing with 3+ humans there yet.

- Placement and JUMP IN: A hosted, B was placed (`1 open session(s), first ... (1/8)`), C
  was placed into the world with two (`(2/8)`); the lobby listed all three and the three
  drove in the world, each with the other two in the HUD list. Twice (another host).
- The host leaves with two others (game closed): after the 30 s timeout the game made one of
  the two its host (`is now the game's host`), the server showed the world with 2 humans
  under it, the other kept playing; a restarted third player was placed into that world
  (`first ... (2/8)`) and jumped in. Twice in a row (A, then C as the host).
- World event with 3 humans, led by a standby host: wait lobby ("INTERMISSION" 30 s, three
  humans and five CPU drivers, AI Medium), the grid with the three humans and five CPU
  drivers, the race (1 lap), the results and back in the free roam of the same world, all
  three (`screenshots/2026-10-16_world_3_players_event_lobby.jpg`,
  `..._host_left.jpg`).
- Relay (`FORZA_SERVER_RELAY=1`, A and C at 30 fps, B at 60, free roam): what each player
  makes the server send, per other player in the world: 3.0 KB/s at 30 fps, 6.1 at 60 fps
  idle, 4.4 and 7.6 driving (two other players: 6, 12 and 9, 15 KB/s). At 144 fps that is (extrapolated)
  about 15-18 KB/s per peer, 7 peers about 100-130 KB/s of the 160 KB/s budget; no packet
  dropped in the run. (The earlier 50 KB/s per peer at 144 fps is too high by a factor of 3.)
- Not done: head-to-head challenges with a third player nearby (a server restart in the
  middle of the world split it: A and C each became "the game's host"; fixed in #93, see
  "Server restart" below), 4 to 8 players, 144 fps with three players.
- `server/test_forza_world_players.py`: placement of the third player, fullest world first,
  full worlds not offered, the standby host after the host left, AI level per human count.

## A game that went silent (#140)

A suspended Steam Deck, or a game process stopped for a while, sends no HELLO; the server
drops it after `PEER_TIMEOUT` (30 s). Before, its next HELLO registered it under a NEW id
(log: `leave #479 (timed out)`, then `join #480`; the game: `lost the server ...;
reconnecting`, `server error 1: not registered`, `signed in ... (player #480)`), so its
XNADDR changed under the session it hosted, as after a restart without the state file
(below). Now the server keeps the token, XUID and id of a player it dropped for silence
(`GONE_KEEP`, 12 hours, at most 5,000) and gives the id back when that token and XUID say
HELLO again, from any address (`back  #479`); the id is not handed to anyone else
meanwhile. A player that said BYE, a new game process (new token) or another XUID gets a
new id. The game says HELLO at once instead of within 5 s when its network thread did not run
for 2 s or more (the game was stopped) or, on Linux, when `src/platform/resume.cpp` finds that
the machine slept: `FORZA_LIVE: back after 45 s; signing in to ... again`, then `signed in ...
(player #484)` with the same id. Tested with a hosted Horizon World on a local server, the game
stopped (SIGSTOP) for 10 s, 45 s and 2 minutes: after the two that outlasted the timeout the
server logged `back  #484` and had the world's record again at once (same session, 1/8), and
the game drove on in the world. `server/test_forza_live_resume.py`.

## Server restart in a lobby or a world (#93)

Cause: the server kept its players only in memory. After a restart the games' packets got
`server error 1: not registered`, they sent HELLO again and were registered under NEW
player ids, while their lobby (the other game's old id, XNADDR and VIP) and the standby
records of a world still named the old ids: each game saw the lobby without the other, and
in a world A and C each became "the game's host" (#12).

Fix (server only): `live_state.json` in the data folder (see docs/hosting.md) holds the
registrations, presence and session records; a start loads it if it is at most 10 minutes
old. The choice over "the client asks for its previous id": the games already send the
same token every 5 s from the same socket, so a restored registration answers the first
HELLO (or any packet) with the same id and epoch; it works with every released client and
the epoch in the XNADDR does not change either (punches and forwards check it). A HELLO
from a new address with the token and XUID of a restored player that was not heard from
yet moves the registration (a NAT port that changed meanwhile); a live registration is never
moved. `server/test_forza_live_restart.py`: same ids and epoch, relay continues without a
HELLO, rooms and presence, a world with its host and joiner (and a second world), the
standby host, an old or damaged file ignored, ids not reused, restored players that do not
return time out.

Real test (Linux, TU4, two games DriverA and DriverB from copies of one save, local server on
UDP 21730): both signed in (#2734, #2735), Find Game > Horizon World, A hosted and B was placed
into its world (2/8), the lobby listed both. The server was stopped with SIGTERM and started
again 4 s later: `restored 2 player(s)`, no `join` line, no new sign-in or `not registered` in
either game's log, `/status` showed the same ids and one world with 2 humans; A pressed JUMP IN, B
followed, both drove in the world with the other in the HUD list. A second restart in the
free roam with `kill -9` (only the last periodic write on disk) also kept both
(`screenshots/2026-10-16_server_restart_world_A_top_B_bottom.jpg`).

## Any car class in the Horizon World (#6, checked 2026-10-16)

The world's lobby accepted only S-class cars (View Setup: CAR CLASS S; a D or A class car
was swapped for an S one). The server's `GAME_OPTIONS_XML` sent
`CarRestrictions CarClassId="255"` and `Min/MaxRecommendedCarClass="255"`. 255 is the
bucket's "any class" (the garage filter SQL skips the class for it), but the options loader
(`sub_825F5AA8`, TU4 `sub_82699E48`) replaces a 255 of an online game's buckets: both
recommended classes 255 give 6 (S), equal ones that class, else the class vote's pick. The
game's own free roam presets say -1 (`IDS_PresetFreeRoam` in `GameOptionPresets.xml`:
`CarClassId="-1"`; `DefaultPlaygroundHopperOptions.xml`: `Min/MaxRecommendedCarClass="-1"`),
and the database's `Tracks` has 0 and 10 for the world's track 1101.

- Server only did not work. Tried with `forza_live_data/hoppers/3.xml` overrides: -1 in all
  three places, the game's own preset XML, `DefaultPlaygroundHopperOptions.xml`, 0 and 10,
  one bucket per class 0 to 10: View Setup said S every time (and 255/0/10 with the class
  vote on said nothing different). The loader turned "any" into S whatever the options said.
- Client (`world_drop_in.cpp`, "4. The car class"): in the world's playlist the loader is
  skipped, so the buckets keep "any" (255). The server now sends -1 for the world
  (`ANY_CAR_CLASS`, as the game's presets; the Races playlist keeps 255, `RACES_CAR_CLASS`).
  The TU4 address came from `tools/tu_address_map.py` (`tu4_addresses.h`).
- Events keep their own class (`TargetClass`, `forza_world.py`): the event lobby of event 178
  said class A and both players drove A-class cars. The Races playlist is unchanged.
- Test (two TU4 games, local server, copies of one Xenia save): A in a Ford Focus RS500 (A,
  600) hosted, B in a Fiat Abarth 500 (D, 287) was placed into the world, both drove in
  it with each other in the HUD list (`screenshots/2026-10-16_world_any_class_fiat_d_class.jpg`);
  a world event (`/world/event`) took both to its lobby with A-class cars, 1 lap, and both
  were back in the world lobby after it (`..._event_lobby_a_class.jpg`).
- The effect needs the client build with the hook; the server's -1 is the game's own value
  (an old client with the new server was not tested).
- `server/test_forza_round_options.py`: the world's round says -1 in the three places, the
  Races rounds 255.

## A car the other game lacks (#567, checked 2026-11-02)

Report (0.2.3, public server, Title Update 4 and DLC, a public custom King game with six
players): the king and the others stayed ghosted for the reporter for the whole round (they
could not be seen or hit; one player was seen only when he hit the crown), and it was gone in the
next game. The reporter had the FM4 pack, UDLC v1.2, the UDLC Traffic Car Unlocker and the FH1 Rally
Freeroam Unlocker; one player lacked the Traffic Car Unlocker and tried to pick the bus ("i cannot
select it"), another ran custom wheels. The reporter's log (`forza.log`, the last session, 20:36
to 20:54 on 1 November) shows a player hosting 6 of 8 slots, one player leaving 70 s after the
round started, and nothing about cars: the game said nothing about what it did with a car.

**What UDLC and the unlocker are.** Community DLC packages from ModDB (UDLC v1.2: the Forza
Horizon 2 and Fast & Furious cars of the Xbox 360 releases; the Traffic Car Unlocker needs UDLC and
makes the traffic cars selectable in the Autoshow; FM4 Imported / "FM4 Reimagined": 446 FM4
cars). The reporter's log lists 18 DLC packages, 13 with the offers of `docs/dlc.md` and five (729,
732, 737, 3060, 3061, all `_pri_99`) that are not Microsoft's. None of them is a game-folder
mod; the `Mods:` lines list only the launcher's radio mod. Their files were not read (not
downloadable here, and not run).

**The traffic cars are in everyone's game.** Data_Car has them (`BUS_TOUR_12`, `FOR_TaurusSHO_10`,
`VW_Beetle_04`, ... 33 rows with `IsSelectable=0`, the Tour Bus among them) and their folders are
in `media/cars` (without the files of a player car: `CarAttribs.xml`, `ColorShaderSettings*.xml`,
`car_skeleton_LOD05.gr2`, the interior, for the bus and others). An unlocker only has to make them
selectable.

**What the game does with a car it cannot build** (traced; TU4 addresses in brackets). A car's
description is built from its id by `sub_82570358` [`sub_825E61B8`]: it queries
`SELECT * FROM Data_Car WHERE Id=%d` and, with no row, calls `sub_824C3C98` [`sub_82531BE0`], which
makes it `NUL_Car_00` (Data_Car id 1215, 0x4BF): the car id is replaced, the original kept at +0x2C8
and the flag +0x2C5 set (a vehicle of the car list keeps the description at +0x2E50, the flag at
+0x3115). The stand-in is a plain black model without paint. The lobby names such a car "DLC Car"
(`IDS_DLCCar`).

Two games on a local server (`FORZA_SERVER`, a King game, golden save copies, the second game
with a mod that replaces its `gamedb.slt`; tools in the scratch notes, not in the repository):

| The second game | What it showed of the first game's car (Nissan GT-R R34, id 445) |
| --- | --- |
| no row for car 445 | the lobby: "DLC Car"; the race: the black stand-in, solid and driven (car list: id 1215, flag 1); its own car (a rental, picked by the game) normal |
| the row, but its `MediaName` has no folder | the stand-in for both its own and the remote car (ids 1215, flag 1); the race ran |
| the Tour Bus selectable on the first game only (a mod flips `IsSelectable`/`IsRentable`) | the bus, normal, in the lobby and the race (the second game did not have the unlock) |
| the remote car's stock wheel row missing (the second game's own car another one) | the remote car normal |
| its own car's stock wheel row missing | grey world with the HUD only, its car's position NaN (the same picture as the first screenshot of #567); not seen as a remote car |

So a missing or unknown car is handled by the game and is **not** what ghosts a player here, and a
traffic car is not special to a game without the unlocker. A ghosted remote car was not
reproduced: it needs a failure these cases do not make (the reporter's packs were not at hand). What
the log lacked is now logged: `Cars: car N is not in this game's database or its files are missing:
the placeholder car ... is used` (`src/hooks/car_placeholder.cpp`, once per car id), so a
report says which cars a game could not show.

**First step of mod sync (#152).** The game tells the server what it has that changes which cars
exist: the offers of the installed DLC packages and the names of the loaded mods that have files
under `media/db`, `media/cars` or `media/wheels` (a radio or text mod is not listed), as text
(`dlc=729,732;mods=A,B`, `src/kernel/content_manifest.h`) in chat op 4 at sign-in and every 30 s.
The server (`forza_chat.py`, `test_forza_content.py`) compares the manifests of the players of
one session (a Horizon World excepted) and tells each side once what they do not share: "X has
Traffic unlocker that you do not: their car may look like a plain placeholder to you" and "Y does not
have Traffic unlocker: your car may look like a plain placeholder to them". Nothing is stored; a
server without op 4 ignores it. Tests: `tests/content_manifest_test.cpp`, the server's tests and two
games with different mods (below).
