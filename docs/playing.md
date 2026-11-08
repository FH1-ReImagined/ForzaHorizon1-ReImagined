# Playing: details

Moved out of the README; the short version is there.

## Running the game

```sh
tools/play.sh                 # play with your own profile (~/.local/share/forza)
tools/play.sh --xenia         # continue your Xenia Canary save (a copy; see below)
tools/play.sh --offline       # without Xbox LIVE
tools/play.sh --base          # the base game instead of Title Update 4
tools/run_forza.sh 120        # a two-minute test run; logs in ${XDG_RUNTIME_DIR:-/tmp}/forza_run
```

- **The log:** `forza.log` in the data folder (launcher: Play > **Open log folder**; with
  `tools/play.sh`, `~/.local/share/forza`) holds the current session only. Each start moves the
  last one's to `forza.previous.log`, replacing the older copy (#609). A session that passes
  50 MB rolls over to `forza.1.log` and starts a new `forza.log`, so one session's log stays under
  100 MB (`log_file_max_mb`, 0 = no limit). For a report, attach `forza.log`, and
  `forza.previous.log` if the problem happened in the session before.
- **Profile and controllers:** the game runs signed in to one local profile on player 1,
  and every connected controller drives player 1, so START on any pad gets you past the
  title screen (with Steam Input or Sunshine virtual pads around, your pad is often not
  the first one, and the game used to ask that pad to sign in).
  `FORZA_PAD_PER_USER=1` gives each pad its own player again.
- **Achievements:** unlock notifications use the native HUD with the game's trophy icon,
  achievement name and Gamerscore. Multiple unlocks queue in order; ordinary alerts and
  challenge prompts take priority, then the interrupted unlock resumes. The F7 achievement
  browser remains available in the overlay.
- **Cockpit gauges:** the game lights the instruments only when the headlights are on;
  `cockpit_gauges_lit = true` in `forza.toml` (launcher: Display, Picture, Cockpit gauges
  always lit) keeps them lit by day too, for your own car only.
- **Radio station changes:** Audio > Radio > **Station changes** keeps the original
  **Game** behavior by default: the station banner delays repeated changes and the
  music fades out and in over 0.5 s each. **Fast** removes the banner gate, uses a
  0.15 s repeat guard and the native 0.25 s fades. **Instant** uses a 0.075 s repeat
  guard and 0.04 s fades, retaining the native zero-volume stream handoff. Both
  quicker modes retarget an ongoing fade to the newest accepted station request,
  including Radio Off, and apply to stock and custom radio stations. The repeat
  guard bounds held/repeating bindings. Set `radio_station_switch` to `"game"`,
  `"fast"`, or `"instant"` in `forza.toml`.
  Stream startup, scheduling and the audio mixer still add latency. In two TU4
  custom test-tone transitions, captured output crossed a fixed tone-amplitude
  threshold 0.216 and 0.250 s after an accepted request; this measures output,
  not the first audible sample or a guaranteed latency. Stock/custom station
  cycles and pause/resume were exercised in bounded game runs. Disc/TU4 CPU
  fixtures cover rapid retargeting and native locks; live sub-ramp input stress
  and scripted-event transitions were not exercised. Global music, dialogue,
  ducking and scripted-event fade rates retain their original values.
- **Radio during races:** Audio > Radio > **Keep radio during races**
  (`radio_keep_in_events = true`, off by default; restart after changing) restores the
  selected stock or custom station, including Radio Off, at qualified career
  and Rally race-opening music cues. Earlier unqualified intro music remains native.
  The original soundtrack remains the default.
  Story cinematics, post-race music, dialogue and sound effects keep their native
  behavior; normal loading and finish fades still apply. Unrecognized or numbered
  fallback cues retain the original soundtrack.
- **Rubber banding (#433):** the game's CPU drivers race against where you are. Their event's
  `AIRubberbands` row (or their skill's) starts a slow-down mode 1-50 m ahead of you and cuts
  their torque from 10-600 m ahead (the weaker drivers' down to a fifth); 40-150 m behind (20
  m more a place) they brake and corner toward the best values, and 10-25 m behind they get
  their skill's best braking and cornering. `ai_rubber_banding = "off"` (launcher: Gameplay,
  CPU drivers, Rubber banding: Off) races every CPU driver at its own skill wherever you are,
  in single-player races and free roam. Online races (Horizon World events, Co-op Festival
  rounds) keep the game's AI on every console. The difficulty (the skill rows), the post-race
  skill adjustment and two boosts in the game's code (by grid place at the start, and for a
  Nemesis rival) stay the game's. Measured with `FORZA_AI_TRACE=1` on Recaro Rush (FR02,
  Medium: skills 66-73, rows 8-9) with the player standing on the grid: Game kept every CPU
  driver in the torque cut (down to 0.19 of their torque) and the first reached the finish,
  about 5 km on, after 182 s; Off had no cut, and six of seven finished after 150-159 s.
  Details in `src/hooks/ai_catchup.cpp`.
- **Fast Travel Anywhere (#424, Title Update 4):** the map's Y offered it for Tokens, which were
  bought on Xbox LIVE and can no longer be bought (offline the offer ends at "You must have an
  Xbox LIVE enabled profile"). `fast_travel_anywhere = true` (the default; launcher: Gameplay,
  Map, Fast Travel Anywhere) makes the game read the unlock as bought: Y on any road asks "Are
  you sure you want to Fast Travel here?" and travels for free, offline and online. It gives no
  Tokens, the Token balance is not touched, and the other Token offer (RB, BUY TREASURE MAP)
  stays as it is. Nothing is written to the save, so `false` brings the offer back unless the
  profile bought it. Details in `src/hooks/fast_travel_anywhere.cpp`.
- **Loading-screen tips:** the loading screens show tips about ReImagined in place of the
  original game's (which point to the ForzaMotorsport.net forums, Token offers and other things that are
  gone). Launcher: Gameplay, Loading screens, ReImagined loading tips (on by default); off shows
  the original tips. English in every language for now. Details in [launcher.md](launcher.md).
- **VR headset (experimental, #333):** launcher Display, VR (experimental), VR headset (experimental): Virtual
  screen or Cockpit (`vr_mode = "screen"` / `"cockpit"`). Start the OpenXR runtime (WiVRn,
  Monado, SteamVR) and connect the headset first; Home or both sticks held for a second
  recentre. What works and how to test it: [vr.md](vr.md#testing-on-a-quest-3-with-wivrn).
- **Keyboard and mouse:** `mnk_mode = true` in `forza.toml` (or `--mnk_mode=true`, or the
  launcher's Controls page); bindings and test results [below](#keyboard-and-mouse).
- **Mouse cursor:** the game hides it after three seconds without movement and shows it
  when the mouse moves. Mouse-look capture keeps it hidden; interactive host overlays
  and text entry keep it visible. The same policy applies in windowed, borderless and
  fullscreen modes. It only controls the game window, not the launcher. The standalone
  `tools/test_cursor_idle.cpp` checks timing, focus, overlays and capture transitions;
  Windows/X11/Wayland desktop and in-game checks are still needed before release (#291).
- **Xbox LIVE:** the game signs in to an emulated Xbox LIVE, as a Gold member, through a
  Forza Live server (`server/forza_live_server.py`, Python 3 standard library only).
  `play.sh` starts one on this machine for the session (UDP 21200, HTTP 21201, data in
  `~/.local/share/forza/live_server/`), or uses the one already running there. To play
  with others, run the server where everyone can reach it
  (`python3 server/forza_live_server.py --http-port 21201`) and start the game with
  `FORZA_SERVER=host[:port]` (or `live_server = "host:port"` in `forza.toml` next to the
  binary). Your online identity (XUID, gamertag: your login name unless
  `FORZA_GAMERTAG` is set) is kept in `~/.local/share/forza/4D5309C9/live_identity.txt`.
  Everyone in the server's room is your friend. Title Update 4 and base-game players
  can't play together: a TU4 game uses the room's "#TU4" version, so each meets only
  players of its own version.
  **The shared world:** Multiplayer, Find Game, **Horizon World** (the first playlist).
  The server puts you into the fullest open world (8 players each) or you open a new one
  and start driving at once, alone, and everyone who picks it after you is placed with
  you: their lobby says FREE ROAM IN PROGRESS, and **JUMP IN** drives them into the
  running world. The world is an endless online free roam (co-op challenges from the
  host's pause menu); when its host leaves, the others keep driving and the next players
  are placed with them. The host's END FREE ROAM takes everyone back to the lobby, and the
  world starts again after 5 s. `world_drop_in = false` in `forza.toml` turns this off
  (the world is then Find Game's race-like free roam, which a late player only enters
  after the round; `--world-round-minutes` on the server ends those rounds on a timer).
  Details: [`docs/online_vision.md`](docs/online_vision.md).
  To play together in a party: everyone opens
  Multiplayer; one player picks Invite Friends in the party lobby, which opens the invite
  picker (there is no Guide): type part of a gamertag to search the players online (Up/Down
  or the D-pad, LB/RB jump to the next letter), A or Enter picks a player (up to 4), Start
  or "Send invites" sends, B or Escape cancels. Only the players picked get an invite. An
  invited player sees "X invited you to their game": hold Back + A (keyboard: Tab + Space,
  or Enter) to join, Back + B or Escape to ignore (that player's invites stay hidden for 2
  minutes), or let it go away after 20 s. The picker lists your friends online first, then
  everyone else online.
  **Friends (#92):** the server keeps your friends list; the game's friends list (who is
  online, what they are doing, joining a friend's game) shows only your friends. Press `F8`
  (`live_friends_key` in `forza.toml`) or hold the left stick press (`F` with `mnk_mode`)
  to open the friends screen over the game (Online → Online → Controller friends shortcut
  turns the left stick hold off for players who hold it while driving, #194); B or Escape closes it. Its tabs (LB/RB or
  Left/Right): **Friends**, **Requests** (asks you, or waiting for an answer), **Add
  friend** and **Blocked**. On Add friend, type part of a gamertag (keyboard) or press Y
  for the on-screen keyboard (D-pad moves, A types, X deletes, Y space, B done); it lists
  the matching players online and those the server knows. A or Enter on a player offers
  what you can do: send a friend request, accept, decline, take a request back, remove,
  block, unblock. A request or an accepted one shows a short notice. A blocked player can't
  invite you or send you requests (they are dropped without telling them). Your friends
  are tied to `signing_seed` (and, for older versions, `friends_key`) in
  `live_identity.txt`: keep that file private (it also holds your XUID), and copy it to keep
  your friends on another computer. With a server older than this, everyone
  online counts as a friend, as before.
  **Text chat (#92):** `F9` (`live_chat_key`) opens the friends screen on its **Chat** tab
  (with a controller: hold the left stick press, then LB to Chat; a friend's "Send a
  message" goes there too). Up/Down picks where the message goes: **Session** (the players
  in your party or game session), **Horizon World** (the players in your world), **Club**
  (shown when you are in a car club, #145: the members of your club who are online, wherever
  they are), or a friend online (a direct message; only friends). Type and press Enter; with a controller,
  A or Y opens the on-screen keyboard and Start sends. A message that arrives while the tab
  isn't open pops up for 6 s. "Mute in chat" in a player's menu hides their messages (also
  players who are not your friends: find them on Add friend); blocking hides them both ways.
  At most 200 characters a message and 5 messages in 10 s. Messages are not stored: the
  server passes them on to the players online and keeps only the last 200 in memory for its
  admin. Lines that start with `/` are commands for the server, whichever channel you type
  them in; its answers appear in the chat log. **Car clubs in the Horizon World (#145):**
  `/club` lists the members of your club who are online and where they are (a Horizon
  World with its players, a Co-op Festival world, or just online). In Multiplayer, Find
  Game, Horizon World the server places you in a world with one of them first, when one has
  room, and in the fullest world as before otherwise. `/club join Gamertag` (or `/club join`
  for the world with most of them) puts that clubmate's world first for 10 minutes: if you
  are in a world, leave it, then pick Horizon World again. Your clubmates see that you are
  online and in which kind of world; blocking a player removes them from both. A player in free roam restarts the game to join,
  as on the console, which takes a few seconds. `live_accept_invites = false` ignores all
  invites. Games up to v0.1.12 invited everyone online and joined every invite at once
  (#90). The party leader then picks Create Custom Game, sets the game
  type under Setup Game (Free Roam, races, ...), saves the setup (YES) and starts once
  the others are ready. Or, without a party: Find Game, then the Races playlist; the
  first player hosts the playlist's lobby and the next ones join it, and the race starts
  after the intermission once two players are in. The server also stands in for Turn 10's
  title services ("the Horizon server", `docs/title_services.md`): the playlists, the
  rounds' tracks, and online XP, which it keeps per player (`players/` in its data
  directory) and gives back at every sign-in; credits stay in your save. It also keeps
  every player's best lap on each event with its ghost (`scoreboards.json`, `ghosts/`):
  Race Central's LEADERBOARDS show them, and X RIVALS on an event's ticket races you
  against another player's ghost for a bounty.
- **Frame rate:** `frame_rate = 60` in `forza.toml` (or `tools/play.sh --frame_rate=60`)
  runs the game at 60 fps instead of the console's 30; 120, 144 or any rate up to 240 work
  too. The game times its physics and clocks in real time, so they keep their speed, and
  players at different frame rates race together. Free roam runs 60 fps at 60, about 115
  at 120 and 112 at 144; a race with 7 AI cars 60, 76 and 70, bound by the GPU emulation
  ([`docs/framerate.md`](docs/framerate.md)). 144 needs the SDK overlay with patch 0037
  (`tools/build_rexglue_sdk.sh`); without it 144 drops to about 72 in free roam. A PC that
  cannot hold 30 can run 20 (`frame_rate = 20`): each frame then stays on a 60 Hz screen for
  exactly 3 refreshes, where 22 fps alternates 2 and 3 and judders; 40 does the same on a
  120 Hz screen (#263).
- **Title Update 4 (default):** `play.sh` runs `forza-recomp/out/build/linux-tu4` when it
  exists (how to build it: README, Building; `FORZA_BUILD=linux-tu4`) and mounts the
  update's files (`extracted/tu4/`, or `FORZA_UPDATE`) as `update:` with
  `--update_data_root`. Horizon Rally and the 1000 Club are in this build only, and the
  Xenia save of a TU4 player needs it. TU4 converts a profile save when it loads it
  (`ForzaProfile/VersionFlags` 0 to 4, flags 2, a `NeedsAA` file); the base build then
  refuses the profile ("MISSING TITLE UPDATE: Your profile save game requires the latest
  title update"). `--base` (or `FORZA_BUILD=linux`) runs the base build: only for profiles
  that never ran on TU4, so keep a copy of the profile folder before the first TU4 run.
  Without a TU4 build `play.sh` uses the base one. Details: [`docs/dlc.md`](dlc.md).
- **DLC:** install your DLC packages (the `4D5309C9/00000002/` folder of your content)
  once with `python3 tools/install_dlc.py /path/to/4D5309C9/00000002` (all 23 packages; a
  Xenia `content` folder with the DLC already extracted works too, or its `00000002` folder,
  and so does the launcher's DLC step;
  the Rally Expansion's and the 1000 Club's modes need the TU4 build, their cars work on both).
- **Driving locked after a save loads, only START works:** that is the 1000 CLUB tutorial
  (TU4, signed in to Forza Live; #237). Once a career is past the point where the 1000 CLUB
  opens, the game plays its intro and then gives the car no throttle, steering, camera or
  map until the tutorial is done, at every load until then. The hints at the top left and
  bottom say what to do: START, **1000 CLUB**, select the challenge card, pick a car and
  **Begin challenges in this car**, then D-pad up for **Current car challenges** and B.
  The launcher's **Edit profile…** can also mark it finished.
- **Saves** live in `~/.local/share/forza/B13EBABEBABEBABE/4D5309C9/`.
- **Your Xenia save:** `tools/play.sh --xenia` copies your Xenia Canary profile's Forza
  Horizon save (`~/xenia_canary_windows/content/<XUID>/4D5309C9`, or
  `$FORZA_XENIA_CONTENT/<XUID>/4D5309C9`) into `~/.local/share/forza/xenia-<XUID>/` and
  plays it signed in with that XUID: Forza ties its save to the profile's XUID, and
  refuses (freezes on the title screen) a save made under another one. The copy is made
  once, so progress made here stays in it; `--xenia-refresh` copies the Xenia save again.
  The Xenia original is never modified. With several Xenia profiles, name one:
  `tools/play.sh --xenia E030000004B490FA`. (Equivalent by hand: copy the save under the
  runtime's profile folder and start with `--profile_xuid=<XUID>`.)
- **Car pictures:** the game renders each car's picture (Car Select, the garage tiles) once
  and saves it in the profile (`ForzaProfile/Thumbnails/`). Builds before resolve readback
  was turned on (`readback_resolve = "some"`, now the default) saved noise there, and so did
  Xenia without that setting. Those pictures stay broken until the car changes: an upgrade
  or a new paint job in the garage renders it again. Deleting the files only makes them
  black. `readback_resolve = "none"` in `forza.toml` turns readback off (no measurable gain over
  `some`, broken pictures again).
- **Typing a name** (a photo, a tune, a livery; #294): the game's text prompt is an
  on-screen keyboard over the game, as on the 360. Controller: D-pad or left stick moves,
  A types the key, B deletes (on an empty name it cancels), X types a space, Y switches
  abc, ABC and symbols, LB/RB move the cursor in the name, Start or **Done** confirms,
  **Cancel** cancels. The keyboard works too (Enter confirms, Escape cancels). Names take
  letters, digits, space and `! @ # $ % & ' ( ) + = - _ . , ; ~ [ ] ^` (what the server
  and a Windows folder name keep), up to the game's length; the game gets no controller
  input while the keyboard shows.
- **Photos:** photo mode saves the picture in the profile (`photo/<name>/`); SAVE asks for
  a name (above). Builds before SDK patch 0048 saved
  a garbled picture (#54); photos saved by them stay garbled, take them again.
  The launcher's **Photo gallery** browses local saved pictures and exports PNG/JPEG
  copies. Press Refresh after saving a new picture ([gallery details](launcher.md#photo-gallery)).
- **Online photo (#576):** the game leaves Photo Mode out of the pause menu online, because it
  cannot pause a shared session. With **Online photo** on (launcher: Online > Online > Horizon World;
  `forza_online_photo = true` in `forza.toml`; off by default), the pause menu of Horizon World
  free roam and online lobbies has **ONLINE PHOTO**: the game's photo camera, without the HUD,
  while the session keeps running. Other cars keep moving; your car rolls to a stop and takes
  no input until you leave (B back to the pause menu, then Resume), and the others see it
  parked. TAKE PHOTO keeps the frame on screen at that moment: one render, so no depth of field
  or photo anti-aliasing, and no Big Shot (the RB size switch is gone). SAVE stores it like any
  other photo (1280x720 in `photo/<name>/`, listed in the launcher's gallery), and the game then
  offers its storefront upload as usual. The website's community photos still come only from a
  Big Shot, which needs single player.
- **Hitches:** a frame that takes over 200 ms logs `FORZA_STALL` lines in `forza.log` with
  what the game was doing, and a game database query of 50 ms or more a `FORZA_SQL` line; attach
  them to an issue ([details](perf.md#long-frames-forza_stall)). Title Update 4 builds up to
  0.1.31 had two or three such frames (0.1-0.3 s) in the first seconds after every load, from
  the update's car list query (#167, [details](perf.md#hitches-after-loads-title-update-4s-car-view-167-2026-10-23)).
- **No sound:** when the sound output cannot be opened (another program holds it in exclusive
  mode, Windows refuses its format, no audio server on Linux), the game runs on without sound and
  `forza.log` says `No audio output could be opened (<reason>)`. It asks for the output again every
  5 s (unless SDL found no audio driver at all), so the sound comes back once the device is free.
  Builds before SDK patch 0103 stopped there (#244). On Windows, try another default output, or
  untick "Allow applications to take exclusive control" in the device's Properties > Advanced.

## Car sound levels (#730)

Audio > Car sounds > **Engine** (`car_engine_volume`) and **Turbo and supercharger**
(`car_turbo_volume`), 0 to 200 % in `forza.toml`, 100 by default (the game's mix). The game
has one level per car sound bus (`media/audio/AudioEngineConfig.xml`: `gamePlayerEngine`
0.45, `gameOpponentEngine` 0.25, `gameSuperCharger` 0.5, `gameTurbo` 0.3, ...), which the mixer
snapshots (`AudioMixerSnapshots.xml`: free roam, race, pause, menus) fade. Every car sound
reads its bus level when it sets the volume of its FMOD events, and the settings scale that
read (`forza-recomp/src/hooks/car_audio_volume.cpp`):

- Engine: the engine voices of every car (player and opponent levels both).
- Turbo and supercharger: the turbo spool and whistle, the blow-off valve (`Cars/Turbos`, its
  Hi and Lo layers) and the supercharger whine (`Cars/SuperChargers`) of every car.

Tyres, wind, transmission, backfires, collisions, the radio and the rest keep the game's
level, and the mixer's fades work as before (it reads the unscaled levels). Measured in the
game (TU4, a copied save, free roam, output muted, the mix the game submits dumped inside
the game and folded to stereo, a stock 2011 WRX STI, one run each, EBU R128 loudness):

| | idle | limiter (brake-stand) | full throttle |
|---|---|---|---|
| Engine 0 % against 100 % | −12.3 LU | −15.6 LU | −8.4 LU |
| Engine 200 % against 100 % | +3.7 LU | +2.4 LU | +3.0 LU |

With the engine at 0 %, the 0.3 s after letting go of the throttle (the blow-off, the rpm
falling from about 3,500) were 1.7 to 8.9 dB louder at Turbo 200 % than at 100 %. 200 % on
the engine adds less than 6 dB because the tyres and wind stay in the mix. The settings
apply when the game starts.

## Recommended picture settings

- Upscaling filter **AMD FSR 1** (the launcher's first-run default) at render scale 1: a sharp
  image at 1080p, 1440p or 4K for about the cost of bilinear. `forza.toml`: `present_effect = "fsr"`.
- Render scale 2 adds real detail for about 30 % fewer fps (42 fps at a 60 fps target on an
  RX 9060 XT); fine at 30 fps. Above 2 is experimental and halves the frame rate (#47).
- **AMD FSR 3 (temporal)** is a heavier, temporal upscaler; on integrated graphics and the Deck, FSR 1 is the better choice. `cas` only sharpens. See `docs/rendering.md`.
- **3D scene resolution** 540p (Display > Picture, `scene_resolution = "540"`) is for
  integrated graphics that can't hold the frame rate: the 3D scene is drawn smaller and scaled to
  720p, the HUD and menus stay sharp at 720p. It only saves GPU time; a PC limited by its CPU gains
  nothing. Not with FSR 3, DLSS, SMAA or ultrawide. Measurements in
  [perf.md](perf.md#scene-resolution-below-720p-scene_resolution-347-2026-10-24).
- **Motion blur** (`motion_blur = false` in `forza.toml` or Display > Picture, or the in-game
  Graphics screen): turns off high-speed blur while leaving tone mapping, bloom and vignette
  untouched (#588). Useful for high refresh rates or players sensitive to motion blur.

## Steam Deck

Install, settings, controls and suspend: [steam_deck.md](steam_deck.md). In short: on SteamOS use the Flatpak
launcher and add it to Steam as a non-Steam game. The launcher detects the Deck and applies (or, to an install that
already had settings, offers once) its preset: 30 fps, render scale 1 with AMD FSR 1, Scene anti-aliasing Off,
Console shadow and texture detail. The Deck is limited by its CPU (the GPU emulation's command processor,
[perf.md](perf.md#steam-deck-and-4-core-cpus-the-built-in-benchmark-45-2026-10-23)); 40 fps suits its screen at
40 Hz, Battery saver (*Auto on battery*) starts it on the lightest settings off the charger, and the game carries
on after a sleep, signed in to Forza Live again with the same player id.

## Auto-pause (#328)

The launcher's **Controls > Auto-pause** offers **Pause when window loses focus** and
**Pause when controller disconnects**. Both are off by default; restart the game after changing them.
The matching `forza.toml` settings are `forza_pause_on_focus_loss` and
`forza_pause_on_controller_disconnect`.

While driving, a selected event requests the game's normal pause menu. Resume manually; returning
to the window or reconnecting a controller never resumes the game. The disconnect option watches
the physical pad or wheel last used, including when keyboard emulation keeps player 1 connected.
Keyboard-only play and unplugging an idle spare controller do not trigger it. Menus, loading,
rewind, text/invite/friends dialogs and benchmark input do not request another pause.
Online sessions keep the game's usual pause behavior: opening the menu does not stop other players.
Focus-loss auto-pause is suppressed while an OpenXR session is running, including virtual-screen
and cockpit modes; controller-disconnect auto-pause remains available.
Controller detection needs the optional `ForzaInputDisconnectSequence` export. Its ReIGlue
implementation is [change 0399, SDK PR #3](https://github.com/FH1-ReImagined/ReIGlue/pull/3).
The current SDK pin `ff1db6f6ff64f237ec2dbd28f06680205a6b8b1a` includes the merged change.
An older runtime without the export logs a warning once if disconnect detection is requested
and still supports focus auto-pause.

Validation (Windows x64, 2026-10-29): `tools/tests/auto-pause` passes all five tests against
the pinned SDK. The production world-update hook publishes each new world to both auto-pause
and the camera before forwarding the native update, exercised for disc and TU4. The production
pause poller checks manual START, paused, loading, rewind and inaccessible-world guards.
A separate process requires the actual SDK export through production symbol discovery;
another injects its absence to test the older-runtime fallback. XR cases cancel a pending
focus pulse and consume new focus events without deferring them until the session ends.
Disconnect requests remain active in VR, including when both reasons were pending.

The exact pinned SDK's device-assignment fixture also passes against the full CI-built SDK
runtime, covering active pad loss with keyboard still connected, idle spare removal, reconnect
and synthetic removal. Client input hooks compile for disc and TU4; five launcher auto-pause
and camera-settings tests pass. These fixtures inject events/device lists. Live Alt-Tab,
physical USB/Bluetooth unplug, wheel, multiplayer and headset checks remain pending.

## Keyboard and mouse

`mnk_mode = true` makes the SDK present the keyboard (and mouse) as a controller on player 1.
It was played end to end this way: menus, free roam, a Festival race from the sign-up to the
countdown and the race itself, the pause menu, the map and photo mode. Keys work while the
game window has focus.

| Controller | Default keys | In the game |
| --- | --- | --- |
| Left stick | `W` `A` `S` `D` | steering (`A` `D`), menu and map navigation |
| Right stick | arrow keys, or the mouse | free look, photo mode camera, map zoom |
| Right trigger | `E` or `O` | accelerate |
| Left trigger | `Q` or `I` | brake, reverse |
| A | `Space` or `;` | select, handbrake, take photo |
| B | `Backspace` or `'` | back, resume; shift up with manual gears |
| X | `L` | sign up for an event, focus in photo mode; shift down with manual gears |
| Y | `P` | rewind (Difficulty: Rewind must be on), effects in photo mode |
| Left / right bumper | `1` / `3` | (unused) / change camera |
| Left / right stick press | `F` / `K` | (unused) / look straight back |
| Back | `Z` or `Tab` | map; with manual gears, enter an event |
| Start | `X` or `Return` | pause menu, past the title screen |
| D-pad | `Shift` + arrow keys | radio |

Triggers and keys are all or nothing, as on a pad pressed to the stop; there is no analog
throttle. The left stick ramps instead (`mnk_stick_ramp`, default 0.2 s): a key moves it to
60 % at once and to full lock over 0.2 s, and releases in half that, so a tap steers
gently and a held key still reaches full lock. Without the ramp (`mnk_stick_ramp = 0`) a
key is full lock at once (a second of it took the starter car from 54 to 27 km/h).

**The mouse** (`mnk_mouse = true`) moves the right stick by its speed: 1000 pixels a second
is half the stick at `mnk_sensitivity = 1`, and the camera returns to centre when the mouse
stops, as with a stick (raise the sensitivity for a slow mouse). While the game has focus
the pointer is captured and hidden (Alt+Tab or the compositor's own shortcut leaves). The
game's menus have no pointer, so the buttons do nothing there, and the wheel is unused.

**Rebinding:** `keybind_<name>` in `forza.toml` or on the launcher's Controls page, e.g.
`keybind_right_trigger = "W,E"`. Each takes comma-separated alternatives. Names: letters,
digits, `F1`-`F24`, `Space`, `Return`, `Tab`, `Backspace`, `Escape`, `Up` `Down` `Left`
`Right`, `Semicolon`, `Quote`, `Comma`, `Period`, `Slash`, `Backslash`, `LBracket`,
`RBracket`, `Minus`, `Plus`, `Backtick`, `Numpad0`-`Numpad9`, `Home` `End` `PageUp`
`PageDown` `Insert` `Delete`, `LMB` `RMB` `MMB` (mouse buttons), and `Shift`, `Control`,
`Alt`, alone or in a combination (`Shift+Up`). A combination only fires with exactly those
modifiers down, which is why the D-pad (`Shift+Up`) and the right stick (`Up`) can share
the arrow keys. Names: `keybind_a b x y left_trigger right_trigger left_shoulder
right_shoulder lstick_up lstick_down lstick_left lstick_right lstick_press rstick_up
rstick_down rstick_left rstick_right rstick_press dpad_up dpad_down dpad_left dpad_right
back start guide`.

Results by activity:

| Activity | Result |
| --- | --- |
| Menus (title, main, pause, options, difficulty, event cards) | works; `W` `S` `A` `D` navigate (`A` `D` change a setting), `Space` selects, `Backspace` goes back; a tap of 70 ms or more registers |
| Free roam: steer, accelerate, brake, reverse, handbrake | works; steering ramps (above); throttle and brake are all or nothing |
| Gears (Difficulty: Shifting manual) | works: `Backspace` up, `L` down |
| Camera change, look back, free look (arrows or mouse), rewind | works; rewind needs Difficulty: Rewind on (it is off by default) |
| Race: sign-up (`L`, or `Z` with manual gears), enter event, start, drive, rewind, pause menu | works |
| Map | works: `WASD` moves the cursor, the arrows or the mouse zoom, `Backspace` leaves |
| Photo mode | works: `WASD` moves the camera, arrows or mouse turn it, `Space` takes the photo, `Backspace` exits |
| Awkward | no analog throttle, brake or steering; no pointer in menus; with manual gears `Z` enters events, so open the map from the pause menu |
| Not tried | the radio keys, the confirmation of "Retire from event" by keyboard (another window took focus during that test), online play |

SDK patch 0040 (`docs/sdk_patches.md`) adds the stick ramp, the mouse speed model and
modifier keys as bindings; without it `mnk_stick_ramp` does not exist and the mouse is
about five times weaker.

## Remapping a controller (#279)

Any control of a controller can be put on another input: open **Controls** in the launcher, pick the
controller under **Bindings**, press **Bind** on a row (Accelerate, Brake / reverse, Steer, Handbrake, the
bumpers, the sticks, the d-pad, ...) and press the button, pull the trigger or move the stick you want for it.
The live view above the rows shows what the controller sends. **Clear** unbinds a row, **Reset to default**
gives it its own input back, and **Reset all to default** drops the controller's bindings. A line in orange
lists inputs that two rows share: binding Accelerate to A leaves A on the handbrake too until that row is
cleared or bound elsewhere. While a Bind waits for an input (10 seconds, Esc cancels), the launcher's own
controller navigation ignores that controller.

- A **button on a trigger** presses it fully (throttle on A); a **trigger or a stick direction on a trigger**
  stays analog (throttle on the right stick pushed up); a **trigger or a stick direction on a button** presses
  it past half way; a **stick on a stick** moves with it, inverted when you moved it the other way than asked
  (Steer on the right stick, or the sticks swapped).
- Bindings belong to the controller model (SDL's GUID: USB vendor and product), so a DualSense and an Xbox
  pad each keep their own, over USB or Bluetooth. PlayStation pads show their own button names.
- They apply the next time the game starts, through SDK patch 0116 (SDL input backend, the default; not
  with the XInput backend). The game's log names the profile it used: `SDL Controller remap (profile ...)`.
- Wheels have their own rows (below). Keyboard keys are set in Settings, Controls.

The launcher stores them as `controller_remap` (Settings, Controls, Controller bindings), which can also be
written by hand: profiles separated by `;`, each the controller's GUID (or `*` for every controller without
its own) and `target=source` pairs, for example `*:rt=a,a=none` (throttle on A, nothing on the handbrake) or
`*:lx=rx,ly=ry,rx=lx,ry=ly` (sticks swapped). Targets: `a b x y lb rb back start guide ls rs up down left
right lt rt lx ly rx ry`. Sources: a button (those names plus `misc1`, `paddle1` to `paddle4`, `touchpad`) or
`none`; `lt`, `rt`; a stick axis (`-lx` inverted) or one direction of it (`lx+` right, `lx-` left, `ly+` up,
`ly-` down), with `@0.3` for how far it must move to press a button. Without a rule a control keeps its own
input, and the Elite paddles press Y, B, X and A as before.

Tested with a virtual Xbox 360 pad (Linux uinput) bound in the launcher: A on the throttle and the handbrake
cleared; in the game the car accelerated while A was held, and the pad's own RT no longer did. Not tested
with a real DualSense or Xbox pad yet: please report what works.

## Racing wheels (issue #4)

Any wheel SDL sees as a joystick (Logitech, Thrustmaster, Fanatec, Moza, ...) is presented to the game as the
**Xbox 360 Wireless Racing Wheel**: the game asks the controller for its capabilities, and a wheel answers
SubType 2 with the force feedback and wireless flags, which is what FH1 checks for. Steering comes in on the
left stick X over its full range, throttle and brake on the right and left trigger, the buttons on A B X Y,
the bumpers, Back and Start, and a hat as the d-pad. The clutch pedal and an H-pattern shifter go in the way
the game reads them from the 360 wheel (below). Gamepads and keyboard and mouse work as before.

The game then treats it as a wheel: My Profile, Options, Controls shows the *Wheel layout* (the paddles shift,
the face buttons are clutch, handbrake, Activate and Rewind), its Advanced wheel options (steering, pedal and
clutch deadzones, force feedback scale) apply, the car uses the game's full-range wheel steering and the game
sends its own force feedback (below). Until #219 a bug in our capabilities hook hid the wheel from the game,
which took every wheel for a gamepad (found by a G923 owner in #4).

Tested here with a virtual wheel (Linux uinput) and by a Logitech G29 owner and a G923 for Xbox owner on Windows
(issue #4: steering, pedals, buttons and force feedback work; the G923 also the clutch and H-shifter). Other wheels
are untested: please report the model and what works.

**Known wheels** (Logitech G25, G27, G29, G920, G923, Driving Force GT, by USB vendor and product id) have a
built-in profile: fixed axes, pedals that rest at the maximum and read the minimum when pressed, and for the
G29, G920 and both G923 versions their own button and shifter maps (below). The axes are never guessed from where they sit at start-up for these wheels.
The profile numbers are SDL's: on Windows steering 0, throttle 1, brake 2, clutch 3; on Linux steering 0,
clutch 1, throttle 2, brake 3. If a pedal then works backwards on your system, switch `wheel_invert_throttle`
or `wheel_invert_brake`; `wheel_axes` and `wheel_buttons` still override the profile. Other wheels are detected
as described below. A pedal is also ignored until it has been seen at rest once, so a pedal that reads 0 for a
moment when the game opens it (or is held down at start) no longer presses itself.

**Thrustmaster wheels (#308)** have their axes and buttons built in too, by product id (SDK patch 0135): TMX
(044f:b67f), TX (b669), TS-XW (b692), TS-PC Racer (b689), T300 RS (b66e), T150 (b677) and T-GT (b684), from
Project Motor Racing's device maps. Steering on X, brake on Y, throttle on Rz and clutch on the slider (SDL axes
0, 1, 2 and 3; on the T-GT 0, 1, 5 and 6), the paddles on the bumpers (they shift), the face buttons where
each wheel has them (the TMX: A 4, B 8, X 2, Y 9). Where the pedals rest is read from the wheel. A TMX on
Windows has no name (the log calls it `"44F B67F"`) and read 0 on every axis until its first report, so the
detection found no pedal and the steering needed a quarter turn before anything happened (#308): now any wheel
whose axes all read exactly 0 when the game opens it has their rest read from its first report (the log says
`axis N rests at ... (first report)`). The T248 is not built in: no source gives its axes, so bind it (or
calibrate it) under Bindings.

**Setting a wheel up (#279).** Open **Controls** in the launcher and pick the wheel under **Bindings**. The live
view shows its axes as bars and its buttons lighting up while pressed, by the numbers the game uses. Each game
action has a row with its current input (and whether that is the default): **Bind** asks you to turn the wheel,
press the pedal or press the button for it and takes the first input that moves (for an axis also its direction
and where it rested: binding the steering to the right sets *Invert steering* if the axis went the other way,
and a pedal's invert switch follows from where it rests), **Clear** unbinds it, **Reset to default** gives back
the profile's or the detected input. Inputs that two rows share are listed in orange (a paddle on a shifter
gear, for example: the shifter wins). Rows: steering, throttle, brake and clutch pedal; shift up and down (RB
and LB, the paddles), clutch button (A), handbrake (B, or an axis: below), Activate (X), Rewind (Y), camera (Back), pause
(Start), the stick presses and the d-pad (the hat by default; a button bound to a direction replaces the hat's,
and Clear turns the direction off, SDK patch 0127); gears 1 to 7, reverse and the sequential up and down of a
shifter. With combined pedals (*Pedals: Combined*) one axis is both: the throttle row binds and clears it for
both pedals, and the brake row has nothing of its own to change.

**Pedals and a shifter on their own USB device (#272).** Many setups plug the pedals and the shifter into the
PC, not into the wheel base: Simagic P1000 or P2000 pedals and a DS-8X next to an Alpha or EVO base, Fanatec
USB pedals or the ClubSport Shifter SQ, a Thrustmaster T-LCM or TH8A. The game's wheel driver reads them together
with the first wheel as one wheel: steering, buttons and force feedback stay on the base, the throttle, brake
and clutch come from the pedal device and the gears from the shifter device (SDK patch 0128). They are found by
themselves: known models by USB id (the table in `sdk_patches.md`, 0128) and any device named like pedals or a
shifter (a TH8A, Moza's SR-P and HGP); the picker lists them as *pedals, read with the wheel* and *shifter, read
with the wheel*, and they never become a second controller. In Bindings, with the wheel picked, a pedal or gear
row binds on whatever device you press: press the pedal on the pedal set and that device becomes the pedal device
(`wheel_pedal_device`, its axes in `wheel_pedal_axes`); put the shifter in a gear and it becomes the shifter
device (`wheel_shifter_device`, `wheel_shifter_device_buttons`). The rows then say where they read, e.g. *Axis 2
on SIMAGIC P1000*. Steering and the button rows stay on the wheel. **Calibrate wheel** finds the pedals on their
own device too. Known pedal sets have their axes built in when they report the three pedal axes PMR's maps list
(Simagic: throttle Rz, brake Ry, clutch Rx); others are detected like a wheel's pedals, from where each axis
rests at start (keep them released). Known shifters have their gears built in (the DS-8X: gears 1 to 7 on
buttons 0 to 6, reverse 8); set **Shifter** to H-pattern (or Sequential) as for a wheel's own shifter. A pedal
bound on the wheel itself (`wheel_axes`) stays there, so a wheel's own clutch can go with separate pedals.
Unplugged while the game runs, the pedals or shifter stop and the wheel's own pedals and buttons count again;
plugged back in, they are read again (the log says `Wheel: pedal device "..." removed` and `... is read with
"..."`). `wheel_pedal_device = none` (or `wheel_shifter_device = none`) keeps the wheel's own. The H-pattern's
gear count goes to the game when it first sees the wheel, so plug the shifter in before starting the game. On
Linux the launcher (SDL 2) does not list a device without an X and a Y axis (most pedal sets, shifters); it reads
those straight from evdev with the numbers the game's SDL 3 gives them (`launcher/forza_launcher/evdev_joysticks.py`).
A pedal set or shifter without buttons that Linux does not mark as a joystick at all needs a udev rule, such as
those of [simracing-hwdb](https://github.com/JacKeTUs/simracing-hwdb).
Tested with Linux uinput devices: a "SIMAGIC EVO PRO" base, Simagic P1000 pedals (0483:0525, Rx/Ry/Rz) and a
DS-8X shifter (0483:0531, 11 buttons). In the launcher the throttle, brake, clutch and gears 1, 2 and reverse were
bound by pressing them on their devices; in the game the P1000's throttle drove the car (45 km/h in
*Automatic*), and with *Manual with clutch* the DS-8X's first gear engaged (N to 1 with the P1000's clutch
pressed, then 63 km/h in 1st) and the car went back to N when the lever left the gear. Not tested with real
devices yet: please report the models and the log's `Wheel:` lines.

**Calibrate wheel** goes through the rest: centre the wheel with the pedals released, turn it to full left and
full right lock (the steering axis, its direction and how much of the axis it covers), turn it a quarter turn
(the top of the rim pointing right: the axis spans the whole rotation, so 90 degrees reading 20 percent is a
900 degree wheel; this sets *Wheel rotation* and lowers *Steering lock* to it if it was higher), then press each
pedal fully (its axis, whether it is inverted, and a warning when it reaches only part of its axis, which no
setting here can make up for: set the pedal's range in the wheel's own software). Throttle and brake on the two
sides of one axis are taken as combined pedals. Any step but the first can be skipped. The last step has the
**steering deadzone** as a slider, with a bar showing the steering the game would get as you turn the wheel.
Apply writes the settings below; they take effect the next time the game starts.

Tested with a virtual wheel of an unknown model (Linux uinput: 4 axes, 24 buttons, a hat): throttle, a paddle and
a shifter gear bound by pressing them, then calibrated (steering axis 0, 900 degrees from the quarter turn,
throttle, brake and clutch on axes 1 to 3). In the game (*Manual* shifting) the bound throttle pedal drove the
car and the bound paddle shifted up from 1st to 3rd. Not tested with a real wheel yet: please report the model
and whether the launcher's axis and button numbers match the game's (the log's `Wheel:` lines). The launcher
reads devices with SDL 2 and the game with SDL 3; on Linux both gave the same numbers and GUIDs.

**Which device is the wheel (#306).** Each wheel the driver takes is a controller of its own, and the first one
is the first player's and the one pedals, a shifter or a handbrake on their own USB device are read with. With
*Racing wheel: On* a virtual joystick (vJoy, which SimHub feeds with pedals, a handbrake and a shifter) or a button
box that came before the wheel base in the system's order became the wheel. Now the wheel base comes first by
itself: a device with force feedback, then one SDL lists as a wheel, then the system's order (SDK patch 0134); a
pedal, shifter or handbrake device is never a wheel. To choose, pick the device under **Bindings** and press **Use
as the wheel** (it sets `wheel_device`; on that device **Choose the wheel automatically** clears it): the picker
calls it *wheel* and any other *wheel, a second controller*. The game's log names its choice: `Wheel: "..." is the
wheel (the first player's; ...)`. Tested with a virtual vJoy (1234:bead, listed first) and a wheel base with force
feedback: the base is the wheel; `wheel_device` set to the vJoy makes it the wheel, and as the pedal device it is
no wheel at all.

**A device the launcher and the game name differently (#562).** The launcher (SDL 2) and the game (SDL 3) can read
different names for the same device on Windows: a Thrustmaster wheel or pedal set (a TX, a T-LCM) is
"Thrustmaster TX Racing Wheel" in the launcher and `"44F B669"` in the game, which gets no name for it. A device
picked by name then matched nothing in the game (the log said `wheel_pedal_device "T-LCM Pedals" is not
connected`, and the pedals were never read). Bindings now saves the wheel, pedal, shifter and handbrake devices by
their USB ids (`wheel_pedal_device = 044f:b371`), which both read alike, and rewrites a device saved by its name
the next time Controls is open with it connected. In the game, a name that matches nothing is also taken for a
joystick without a name of its own that is a known device of that kind (a T-LCM for the pedal device, a TH8 for
the shifter, a Thrustmaster wheel for `wheel_device`), and when a configured device is not found the log lists
every joystick the game sees once (`Wheel: SDL sees N joystick(s)`, then their name, GUID, VID, PID, axes,
buttons and hats), so a report shows what the game has. Not tested with a real TX and T-LCM: please report them
with the `Wheel:` lines.

**Analog handbrake (#303).** The game's wheel layouts bind the handbrake to the left stick up and down as well as
to B (gamedb.slt `WheelLayouts`, as the clutch is on the right stick Y): the Xbox 360 wheel's analog handbrake.
The game takes the stronger of the two, and a stick direction is analog (0 to 1), so the driver sends a handbrake
axis there (SDK patch 0133) and B still works. The **Handbrake** row in Bindings takes both: **Bind**, then pull
the handbrake (an axis) or press a button on the wheel; binding one keeps the other, **Clear** unbinds both and
**Reset to default** gives back B and a handbrake device found by itself. The axis is read from the wheel (a
handbrake plugged into the wheel base: `wheel_axes` `handbrake=N`) or from a handbrake on its own USB device
(`wheel_handbrake_device`, its axis `wheel_handbrake_axis`): a Simagic TB-1 or TB-RS, a Fanatec ClubSport
Handbrake or a Thrustmaster TSS is found by its USB id, others by a name with "handbrake" in it, and Bindings
sets the device when you pull it there (the picker lists it as *handbrake, read with the wheel*). A pedal set or
shifter that carries the handbrake (Logitech's RS Shifter and Handbrake) is named in `wheel_handbrake_device`
and keeps its pedals or gears. Buttons on a handbrake device are not read: in button mode, bind its axis or a
wheel button. Where the handbrake rests is read when the game opens it, like a pedal (keep it released then);
one that works backwards is fixed with *Invert handbrake*. Tested with a virtual TB-RS: from about 48 km/h in 1st
with the throttle off, a handbrake a third of the way slowed the car by about 2 m/s², two thirds by 6, fully by 8.

The settings these write (Settings, Controls, Racing wheel; the keys are the `forza.toml` names) can still be
edited by hand:

| Setting | Default | What it does |
| --- | --- | --- |
| `wheel_mode` | `auto` | `auto`: SDL says it is a wheel, or its name looks like one (G29, T300, Fanatec, Moza, ...). `on`: every controller without a gamepad mapping is a wheel. `off`: wheel support off |
| `wheel_device` | found | The wheel (#306): the first player's, which pedal, shifter and handbrake devices are read with. Empty: one with force feedback first, then one SDL lists as a wheel, then the first; a device name (as Bindings lists it), SDL GUID or `vid:pid` (what Bindings saves) picks one and makes it a wheel (launcher: Use as the wheel) |
| `wheel_rotation`, `wheel_lock` | 900, 900 | The wheel's rotation as set in its driver, and the travel (lock to lock) that gives the game's full steering. 900/540 makes the steering about 1.7 times quicker |
| `wheel_deadzone`, `wheel_invert_steering` | 0, off | Steering deadzone (0 to 0.5); swap left and right |
| `wheel_steer_offset` | 0.05 | Only with `wheel_900_steering` off: compensates an inner steering deadzone (below) |
| `wheel_pedals` | `auto` | Separate pedals; `combined` is one axis with throttle one way and brake the other |
| `wheel_invert_throttle`, `wheel_invert_brake` | off | Pedals found backwards |
| `wheel_pedal_device` | found | Pedals on their own USB device: empty finds a known pedal set or one named like pedals; a device name (as Bindings lists it), SDL GUID or `vid:pid` (what Bindings saves) picks one; `none` reads the wheel's own pedals |
| `wheel_pedal_axes` | by model or detected | Axis numbers on the pedal device, e.g. `throttle=2,brake=1,clutch=0`; a pedal set here reads the pedal device, one set in `wheel_axes` the wheel |
| `wheel_throttle_curve`, `wheel_brake_curve` | 1.0 | Gamma: 1 linear, above 1 softer at the start |
| `wheel_axes` | detected | e.g. `steer=0,throttle=2,brake=5,clutch=1`; a number past the wheel's axes (Clear writes 99) unbinds, a negative one means the default. `handbrake=N`: an analog handbrake on the wheel (#303; 99: none, also no handbrake device) |
| `wheel_handbrake_device` | found | An analog handbrake on its own USB device (#303): empty finds a known one or one named like a handbrake; a name, SDL GUID or `vid:pid` picks one (also the pedal or shifter device); `none` reads none |
| `wheel_handbrake_axis`, `wheel_invert_handbrake` | 0, off | The handbrake's axis on the handbrake device; a handbrake found backwards |
| `wheel_buttons` | see below | e.g. `a=0,b=1,x=2,y=3,lb=5,rb=4,back=8,start=9,ls=11,rs=10,up=12,down=13,left=14,right=15` (SDL button numbers; -1 unmaps). The hat is the d-pad for the directions this leaves out: `up=-1` turns hat up off, `up=12` puts it on button 12 instead (0127). A button the shifter uses presses none of these |
| `wheel_ffb`, `wheel_ffb_strength` | on, 60 | Force feedback switch and strength (0 to 100 percent) |
| `wheel_ffb_min_strength` | 0 | Minimum steering force (#303): the game's steering forces weaker than this percentage of the full force are scaled up, stronger ones stay (launcher: Minimum force; 0 is off) |
| `wheel_ffb_spring_strength` | 100 | The centring spring and the damper, in percent of `wheel_ffb_strength` (launcher: Centring strength) |
| `wheel_rumble_engine_strength`, `wheel_rumble_road_strength` | 50, 80 | The rumble from the game's engine motor (its right motor) and from its road motor (left: road surface, kerbs, impacts), in percent of `wheel_ffb_strength` (launcher: Engine rumble, Road rumble); 100, 100 before #273 |
| `wheel_vibration_rate` | 60 | How many times a second the wheel's vibration is updated, 15 to 120, whatever the frame rate (launcher: Wheel vibration updates per second). 60 was the smoothest on a Fanatec CSR Elite and a Simagic Alpha EVO Pro; lower it (30) if the vibration feels too strong (#467) |
| `wheel_rumble_style` | `fine` | `fine`: each rumble motor a light, quick vibration (40 and ~71 Hz); `classic`: one 25 Hz vibration, as before (#273) |
| `wheel_ffb_reverse` | off | Reverse the game's steering force, for a wheel that pulls away from the centre in corners |
| `wheel_ffb_keep_playing` | 0 | For a wheel that ends force feedback effects by itself (#304): start every effect that plays again every this many seconds. 0: never (an effect the wheel says has stopped is still started again); -1: not even that |
| `wheel_clutch`, `wheel_invert_clutch` | on, off | The clutch pedal as the game's analog clutch; a clutch found backwards |
| `wheel_shifter` | `off` | `h`: an H-pattern shifter selects the gear; `sequential`: its up and down buttons shift like the paddles |
| `wheel_shifter_buttons` | by model | e.g. `1=12,2=13,3=14,4=15,5=16,6=17,r=18` (gears 1 to 7 and reverse) or `up=..,down=..`; SDL button numbers, -1 unmaps |
| `wheel_shifter_clutch`, `wheel_clutch_threshold` | off, 0.5 | H-pattern: a gear engages only with the clutch pressed this far (0.1 to 1) |
| `wheel_shifter_neutral_ms` | 400 | H-pattern: how long the lever may sit in neutral between two gates and still go straight from one gear to the other, so the game shows an upshift as one (#468); 0 to 2000 milliseconds, 0: neutral at once, as before |
| `wheel_shifter_follow_slot` | on | H-pattern: on, the game takes the lever's gear whenever it sets the gear itself (race and event starts and finishes), as on the Xbox; off, a gear engages only when the lever moves, for switching to the paddles (#303; launcher: Gear follows the lever's slot) |
| `wheel_shifter_device` | found | A shifter on its own USB device: empty finds a known one or one named like a shifter; a name, SDL GUID or `vid:pid` picks one; `none` reads the wheel's own buttons |
| `wheel_shifter_device_buttons` | by model | Its gears, e.g. `1=0,2=1,3=2,4=3,5=4,6=5,r=6`, or `up=..,down=..`; while a shifter device is connected every gear comes from it |

**Steering around the centre.** A G29 owner reported that the first degrees of every turn did nothing: the game
took the wheel for a gamepad then (#219) and applied a gamepad's inner deadzone, and a 900 degree wheel turned 50
degrees is only 5.5 percent of its range. `wheel_steer_offset` compensated: any movement beyond a hair (about 1
degree) already gives that share of the full steering (0.05 = 5 percent). Now that the game knows the wheel, its
full-range steering (`wheel_900_steering`, on by default) follows the wheel's angle from the centre, and the
offset is not applied there: its step at the centre made the car twitch left and right when driving straight
(#219). It still applies in the 270 degree mode (`wheel_900_steering = false`), where the game filters the
steering. The game's own wheel deadzones are in its Advanced wheel options.

**Clutch pedal and H-shifter (issue #125).** The game has both for the 360 wheel (third-party 360 wheels had
them; on the Xbox One a G920's clutch and shifter work in this game). Its wheel layouts bind the right stick Y
to the clutch, and when the wheel's capabilities say it has an H-pattern shifter, the car takes the gear from
the right stick X (-1 reverse, 0 neutral, 1 to 8). So the driver sends the clutch pedal (0 released, full
travel pressed) on the right stick Y and, with `wheel_shifter = h`, the gear of the held shifter button on the
right stick X; no gear button held is neutral. The game's own gearbox does the shift: one shift with its normal
shift time, also from 2nd straight to 5th. The game's *Wheel layout* screen (My Profile, Options, Controls)
shows the rest of the wheel's controls; in Wheel layout 1 the bumpers shift up and down and A is the clutch
button.

- **Clutch:** choose *Manual with clutch* (pause menu: My Profile, Options, Difficulty, Shifting), then the
  pedal works like the clutch on a real car (pressed, the engine revs freely and the car does not pull) and the
  game's wheel clutch deadzones apply. The wheel's clutch axis is the one
  from the profile (Logitech) or `wheel_axes` (`clutch=N`), else the third pedal found at start. Off with
  `wheel_clutch = false`.
- **H-pattern shifter:** set `wheel_shifter = h` (launcher: Controls, Shifter and clutch, Shifter: H-pattern)
  and *Manual* or *Manual with clutch* shifting in the game; with *Automatic* the game ignores the shifter.
  Even with *Manual with clutch* the game takes the lever's gear without the clutch (see realistic shifting).
  The Logitech Driving Force Shifter on a G29, G920 or G923 is built in, by the wheel's USB product id: gears 1
  to 6 are buttons 12 to 17 on all of them, reverse (pushed down) is 18 on a G29 or a G923 for PlayStation
  (046d:c24f, c266) and 11 on a G920 or a G923 for Xbox (c262, c26e): those wheels number reverse before the gears
  (Logitech's and PCGamingWiki's button tables, and a G923 for Xbox owner, #256). Other shifters: bind the
  gears on the Controls page (Bindings, the Shifter rows: put the lever in each gear when asked), which writes
  `wheel_shifter_buttons`, e.g.
  `1=8,2=9,3=10,4=11,5=12,6=13,r=14`; up to 7 gears and reverse (the log prints the map: `Wheel: H-pattern
  shifter on the wheel, buttons r=...`). A shifter that is its own USB device (Thrustmaster TH8A, Fanatec USB
  shifters, a Simagic DS-8X) is read with the wheel: [above](#racing-wheels-issue-4), *Pedals and a shifter on
  their own USB device*.
- **The shifter's buttons press nothing else.** Every button the shifter uses (its gears and reverse with `h`, up
  and down with `sequential`) is taken out of the wheel's pad buttons, whatever the defaults or `wheel_buttons`
  say: on a G923 for Xbox reverse is button 11, which the generic map had as the left stick press, so engaging
  reverse also opened the friends screen (#256).
- **The lever's slot, or gears on lever moves (#303).** The game turns the lever's gear into a gear request
  every frame, shifts when the request changes, and takes it again whenever it sets the gear itself: at race and
  event starts and finishes the car goes into the gear the lever sits in, or into neutral with the lever out.
  That is what the Xbox does with a 360 wheel's shifter, so it stays the default (*Gear follows the lever's
  slot*, `wheel_shifter_follow_slot`). If you switch to the paddles or a sequential shifter mid-session, turn it
  off (SDK patch 0132): the driver then asks for a gear only for a moment after the lever moves into a slot or
  out to neutral, and for nothing otherwise, so the paddles keep their gear and a start keeps the game's until
  you move the lever (where the lever sits when the game starts is not a move). Tested with it off, a virtual
  wheel and DS-8X, the lever left in 3rd: the paddles shifted freely, a race started in 1st, and moving the
  lever to 4th engaged 4th.
- **Through neutral (#468).** The game numbers its gears R 0, 1 to 8 and neutral 9, and takes a change to a
  higher number for an upshift (that is what the cockpit's paddle animation shows). The lever crosses neutral
  between two gates, so every H-pattern shift used to reach the game as neutral, then the gear: a downshift, and
  a car with paddles pulled the downshift paddle on every upshift. Now a lever that leaves a gear keeps it for
  `wheel_shifter_neutral_ms` (400 ms by default) while it is in neutral: one that reaches another gate in that
  time goes straight there (2nd to 3rd is an upshift), one that stays in neutral longer is neutral then (SDK
  0404). The log shows the first few changes and how the game takes them, e.g. `Wheel: H-pattern 2 > 3, an
  upshift for the game (the lever crossed neutral in 180 ms)`. If slow shifts still show the downshift paddle,
  raise it; 0 gives neutral at once, as before.
- **Realistic shifting:** `wheel_shifter_clutch` makes a new gear engage only while the clutch is pressed at
  least `wheel_clutch_threshold` (default half way); moved into a gear without the clutch, the car stays in
  neutral until you press it. Off by default (the lever's gear engages at once).
- **Sequential:** `wheel_shifter = sequential` with `wheel_shifter_buttons = up=N,down=M` adds a sequential
  shifter next to the paddles (up and down act as the right and left bumper, the wheel layouts' shift up and
  shift down). At a standstill the game's downshift drops to 1st, as with the bumpers.

Tested with a virtual G29 with Driving Force Shifter and clutch pedal (Linux uinput): gears 1 to 6, reverse and
neutral, 2nd straight to 5th at speed, the clutch holding the car while the engine revs in *Manual with clutch*,
realistic shifting and the sequential buttons; the shifter is ignored with *Automatic*. Not tested with a real
shifter or clutch pedal yet: please report the model and what works.

The shifter type is read when the game first sees the wheel, so restart the game after changing it. The clutch
and gear are sent only once the game has read the wheel's capabilities (the log says `Wheel: the game asked for
capabilities`); a game that took the wheel for a gamepad would turn them into right stick movement. They share
the right stick with any other controller connected at the same time (the stick pushed furthest wins), so a
gamepad whose right stick drifts can disturb the gear: unplug it while driving with the wheel. Keep
`right_stick_deadzone_percentage` at 0 for the same reason.

Axis detection (wheels without a profile) reads where each axis rests when the wheel is plugged in: the one near
the middle is the steering, those resting at an end are the pedals (throttle, brake, clutch in axis order).
**Keep the pedals released while plugging in or starting the game**; a pedal that works backwards is
fixed with the invert switch or **Calibrate wheel**, a wrong axis with **Bind** (or `wheel_axes`). The log line `Wheel: "..." axes(..) buttons(..)`
and the `axis N rests at` lines show what was found. Default buttons: 0-3 A B X Y, 4 right bumper, 5 left bumper,
8 Back, 9 Start, 10 and 11 stick presses; the G29 and G923 for PlayStation map is
`a=0,b=2,x=1,y=3,rb=4,lb=5,back=8,start=9,ls=11,rs=10` (cross, circle, square, triangle, right paddle, left
paddle, Share, Options, L3, R3; R2 and L2 are free). The G920 and G923 for Xbox map is
`a=0,b=1,x=2,y=3,rb=4,lb=5,start=6,back=7,rs=8,ls=9` (Menu, View, RSB, LSB; the Xbox button, 10, and the shifter's
11 to 17 are free): their buttons follow PCGamingWiki's G920 and Xbox G923 tables, where the generic map had put
the stick presses on the Xbox button and the shifter's reverse (#256, not yet confirmed on the wheel). With the
game's Wheel layout 1 the paddles shift up and down, cross is the clutch, circle the handbrake, square Activate,
triangle Rewind and Share the camera (the paddles were on B and X while the game took the wheel for a gamepad,
whose layout shifts with those). Wheel buttons differ between models, so check the numbers (for example
with `jstest-gtk`) and override as needed. Wheels that SDL also maps as a gamepad are taken by the wheel driver
instead; Xbox (Microsoft) wheels keep using the gamepad path.

**Force feedback (#219).** The game drives the 360 wheel's force feedback itself, through the kernel's
`XInputdFF*` calls, which SDK patch 0101 plays on the wheel with SDL haptics. It sets up five effects and keeps
them running: a **constant force** that it updates every frame with the car's steering force (in a corner it pulls
the wheel back towards the centre; scraping a wall or crashing makes it kick), a **centring spring** that is off
at a standstill, grows with speed and fades while the rear wheels slide, a light **damper**, and an inertia and a
friction effect set to zero. Its tuning is `media/ControllerFFB.ini`; the game's Advanced wheel options scale it
(Force feedback scale) and the rumble (Vibration scale). `wheel_ffb_strength` scales everything (default 60
percent), `wheel_ffb = false` switches it off. Three more strengths (#256), in percent of it, each set one part:
`wheel_ffb_spring_strength` the centring spring and the damper (100 by default; the spring grows with speed, so at
speed small turns can feel heavy; lowering it softens the centring without weakening the steering force and the
road feel), `wheel_rumble_engine_strength` the game's right motor, which carries the engine and climbs towards the
limiter (50 by default), and `wheel_rumble_road_strength` its left motor (road surface, kerbs, impacts, tyre
slip; 80 by default).

**Rumble style (#273).** The game's two rumble motors were made for the Xbox 360 wheel's small motors in the
rim, which have to shake hard to be felt; played through a force feedback base's main motor at those levels they
rattle the whole wheel (a Fanatec CSR Elite owner in #4: engine and tyre-slip rumble together). `wheel_rumble_style`
(launcher: Rumble style) picks how they play, SDK patch 0129:
- **Fine** (the default): each motor its own light, quick vibration on the steering axis, the road motor a 40 Hz
  sine and the engine motor a ~71 Hz one (period 14 ms), each at most a quarter of the force feedback strength
  (FH6 plays these effects as micro-vibrations on the main motor too). Both play at once, so the road comes
  through while the engine hums.
- **Classic**: as before, one 25 Hz sine at the stronger motor's level, up to 60 percent of the force feedback
  strength, for a wheel that should shake like the 360 wheel's rim motors.
Fine needs the wheel's periodic (sine) effect and room for two effects; a wheel that rejects them plays classic
(the log says `Wheel: fine vibration rejected ...: classic vibration instead`); one without a sine effect plays no
rumble in either style (the log says so). The defaults are softer since #273: measured in free roam with a
virtual wheel, the engine motor runs at half or more whenever the car accelerates (about 0.45 on average flat out
off-road, peaks at full) and the road motor sits at 0.1 to 0.2 on tarmac and climbs to full off-road and in
slides, so the engine starts at 50 percent and the road at 80. With the defaults (strength 60) fine gives the
road at most 0.12 and the engine 0.075 of the motor's force, where classic at the old 100 percent gave 0.36. The
style is read when the game opens the wheel: restart after changing it. Please tell us which values feel right
on your wheel (#4).

**The same vibration at every frame rate (#467).** The game sets its rumble once a frame, and every change used to
go to the wheel: about 60 updates of the vibration a second at a 60 fps cap and two to four times as many at
higher caps. A wheel that starts its sine again at every update (DirectInput does when it cannot change a playing
effect) then plays a buzz at the frame rate instead of the 40 and 71 Hz vibration, which a Simagic owner felt
"through the entire rig" at 240 fps. The vibration is now updated about 30 times a second at every cap, each time
with the average strength (root mean square) since the last update, so it carries the same energy a second
whatever the frame rate (SDK 0403). The log's 10 s force feedback summary shows it: `..., 1203 rumbles, 309
vibration updates (31 a second, up to 12%), ...` (the game's rumbles, then what the wheel got).

**Minimum force (#303).** The game's steering force follows the car: light cars give a light wheel, and a heavy
one (a WRX STI on a Simagic EVO Pro in #4) several times more. `wheel_ffb_min_strength` (launcher: Minimum force,
0 by default) lifts the weak end, SDK patch 0131: a steering force weaker than this percentage of the full force
is scaled up, at most 4 times near the centre and less the closer it is to the minimum, and from the minimum up
it plays as the game sends it. The curve is smooth and starts at 0, so there is no step when the force changes
sides in the middle and no floor that flattens the road feel; it applies to the game's force before
`wheel_ffb_strength`, and the centring spring, the damper and the rumble are not changed. With a minimum of 30, a
force of 10 percent plays at about 23, one of 20 at 27 and one of 30 or more as it is. Try 10 to 30.

The
wheel's own autocentre is off while the game runs; on a wheel without a spring effect (the Linux kernel driver of
the G25, G27 and G29 has only constant force and autocentre) the game's spring sets the autocentre strength
instead. On Linux the wheel needs read and write access to its `/dev/input/event*` node (a udev rule for most
wheels).

**Not driving: the forces fade out (#256).** The game keeps its wheel effects going when the car is not under the
player's control: in the pause menu it sets the centring spring to full and leaves it and the damper on, a rewind
plays the steering force and rumble of the car going backwards, and replays keep the last spring. So the wheel
pulled and buzzed there (a G923 owner, #4). The driver now fades all the game's forces and its rumble to nothing
in 0.25 s while the player is not driving and back in over 0.5 s when they are. The game tells it
(`ForzaWheelPlayerDriving`, an export of the runtime) from its own simulation state, once a frame: the pause flag
of its world (the pause and rewind menus, replays), the time scale (negative while a rewind plays the car
backwards), whether the world has started (loading) and whether the car's physics stepped, and the pad polls for
menus where no world runs. Driving counts again after half a second of physics (which leaves out most of the
burst when a replay starts; the one from the rewind menu's *View replay* is about 1.3 s long). The game's rumble
cannot tell with a wheel: it sends a wheel's rumble only through the force feedback calls. With a virtual G923:
in the pause menu and while rewinding the spring, damper, constant force and rumble the wheel receives go to 0
within half a second, and come back half a second after *Resume* or *Resume from here*.

**Hitches no longer fade the forces (#304).** The first version (#256) counted a quarter of a second without a
physics step as not driving, so every frame that long or longer (a shader compiled after a crash, the window
losing focus or streaming, for example) took the forces away for about a second: out at once, back half a second
later, then the fade. A Simagic Alpha EVO Pro owner at 900 degrees felt the force feedback go weak after hitting
walls, alt-tabbing and a few minutes of play, and their log showed it switching like that. The state is now
counted in frames, so a hitch, a wall hit, a focus change or a long drive never fades anything (tested with a
virtual Simagic at 60 and 144 fps: 0.7 s and 2 s freezes, a crash into the roadside, the window minimised, a 5
minute drive). The log names the reason each time: `Wheel: not driving (pause)`, `(rewind)`, `(loading)`, `(menu
or loading)`, then `Wheel: driving`, and the driver adds `the game's forces are faded out` and `the game's forces
are back at full strength`. Back at full strength the driver also starts every effect that should be playing
again, in case the wheel dropped one meanwhile (DirectInput stops a device's effects when it has to acquire it
again), and a failed update or start of the game's effects is logged as a warning.
`FORZA_WHEEL_DRIVING_GATE=0` switches the fade off.

Windows source-validation follow-up (2026-10-22): integrated main `7f9a716`,
retaining both the #304 fade/restart fix and patches 0131-0134. All 608 launcher
tests passed (37 skips), as did six standalone C++ wheel tests covering minimum
force, gear requests, analog handbrake, primary selection, shifter mapping and
force feedback. The actual combined wheel adapter compiles against Windows
SDK/SDL3 headers. No physical force-feedback device or game/GPU session was used;
DirectInput acquisition, force feel and real Fanatec/vJoy ordering remain hardware
validation gates.
**The steering force no longer stops about 30 s after a pause (#304).** The same Simagic Alpha EVO Pro owner
(Windows) then felt the wheel go light about 30 s after every pause or fast travel, often around an autosave:
the reactive force gone, only a centring spring left, until the next pause. The driver gives every effect SDL's
endless length, but SDL's DirectInput (Windows) and macOS backends turn it into 4294966296 microseconds (the
length times 1000 in 32 bits), about 71 minutes and not DirectInput's endless value, and a HID PID wheel's
duration field (16 bits of milliseconds) cannot hold that. The game starts its steering force and damper once
(the driver starts them again only when the forces come back to full strength after a pause), but stops and
starts its centring spring whenever the spring's gain passes 0 with the speed, every few seconds: a wheel that
ends effects after about half a minute keeps only the spring, which is what the report describes (not yet
confirmed on the wheel itself). SDL patch 0147 now sends endless effects as endless on Windows and macOS. For a
wheel that still ends effects by itself, every 10 seconds the driver asks the wheel whether the game's effects
still play (where SDL can ask: DirectInput, macOS, SDL's own Logitech driver): one that stopped is logged (`Wheel: "..." says the game's effect block 5
(constant) has stopped ...; started again`) and started again, and `wheel_ffb_keep_playing` (0 by default)
starts every effect again every so many seconds (5 is a good start; some wheels may feel each restart as a
slight bump). Reproduced in free roam with a virtual Simagic (uinput) that ends each effect 32.767 s after its
start: without restarts the steering force and damper ended 32.8 s after the forces came back to full strength,
while the spring went on; with restarts every 5 seconds nothing ended over a drive of several minutes with a
pause. The autosave itself (the profile is written every minute or so while driving) changes nothing
the game sends. To see it in a log, the driver now also logs what the game sends and what the wheel gets
(SDK patch 0145): a block's line when it changes (`Wheel: the game's effect block 4 is now ...`), the game's
effect operations and device controls, and every 10 seconds a summary: `Wheel: the last 10 s: block 5
constant: game 300 updates, gain 255, force -62..48, playing; wheel 208 updates, 2 restarts, level
-7431..9598, running | ...`.

**An effect the wheel refuses (#308).** A TMX on Windows refused the game's steering force ("Haptic error
Unable to create effect"), and the driver tried again every frame: 86,000 warnings in one log. Now an effect is
created on SDL's steering axis, else as a Cartesian or polar direction (SDK patch 0136); a type the wheel refuses
in every direction is logged once (`Wheel: "..." refuses the game's ...`) and not tried again until the game
resets its force feedback or the wheel is plugged in again. A refused centring spring becomes the wheel's own
autocentre where it has one. Tested with a virtual TMX (uinput) that refuses constant forces: three tries (one
per direction), one warning, the spring still playing; after a reset, three more.

Tested with a virtual wheel (uinput) that logs what it receives, and by a G29 and a G923 owner. Wheel drivers disagree about
which way a positive force turns the wheel: if yours pulls further into a corner instead of back to the centre,
turn on `wheel_ffb_reverse` (launcher: Reverse force direction), and please report the model.

A game that takes the wheel for a gamepad only sets the vibration motors; the driver then makes an approximate
centring spring and vibration from them.
