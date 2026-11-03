# Discord Rich Presence

Status display for #146. **Settings > Online > Discord > Share activity on Discord**
is on by default and applies at the next game launch. The Discord desktop client
must be running as the same OS user; Discord in a browser cannot receive local RPC.
Playing offline in Forza Live settings still supports Discord status display.

The game publishes its activity (free roam, festival/street/showcase races, rivals,
PR stunts, autoshow, paint shop, workshop, race central, car club, lobby and online
game type), elapsed game-session time, and session-local player counts when known.
With **Settings > Online > Discord > Share car, event and area** (`discord_presence_details`,
on by default) it also publishes the current car with its class and PI, the event and, in
free roam, the zone of Colorado, all with the names the game shows:

| Scene | Details | State |
| --- | --- | --- |
| Free roam | Exploring Colorado · Clear Springs | 2012 Lamborghini Aventador LP700-4 · R3 797 |
| Festival race | Racing in a festival event · GoPro Millennia Masters | 2012 Lamborghini Aventador LP700-4 · R3 797 |
| Rivals | Challenging a rival · GoPro Millennia Masters | 2012 Lamborghini Aventador LP700-4 · R3 797 |
| Autoshow | Browsing cars | Autoshow |
| Online free roam | Exploring Colorado online · Festival | 1984 Volkswagen Rabbit GTI · E 192 |

Menus (Autoshow, Paint shop, Workshop, Race Central, ...) and the online lobby keep their
own wording; a name the game does not give is left out, never replaced by a placeholder.
Both lines stay within Discord's 128 characters. Off, the status is the activity alone, as
before. It does not publish gamertags, profile IDs, save contents, server addresses, session
keys, join secrets, anything the player typed or precise locations (the zone is one of eight).
The connection runs on worker threads; game callbacks only copy small pieces of state.
Updates are coalesced to at most one every five seconds. Reconnect sends the latest activity.
Closing the game closes the connection and clears its activity; a crash closes the pipe as well.

## Setup

The project has its own Discord application (ID `1563015276583460934`, named **Forza Horizon 1:
ReImagined**), set by default, so there is nothing to create:

1. **Settings > Online > Share activity on Discord** is on by default; turn it off to stop
   sharing. Discord's own activity privacy settings must also allow sharing.
2. To use another Discord application (a fork), put its public **Application ID** in
   **Settings > Advanced > Discord setup > Discord application ID**. No bot, OAuth login, token
   or client secret is used.

For a direct launch, set `discord_presence = true` and
(the application ID defaults to the project's) in `forza.toml`.
Missing/stopped Discord is optional: the game continues and retries in the background.
The log uses `DISCORD:` for connection and activity updates; it does not log Discord
account details or server-provided error text.

Linux also searches Discord's Flatpak runtime directories for stable and Canary.
The launcher Flatpak grants access to the standard desktop IPC socket and these
specific runtime directories. There is no network listener. A desktop Discord
using a nonzero IPC slot inside the game Flatpak may need an extra narrow permission,
for example `flatpak override --user --filesystem=xdg-run/discord-ipc-1
io.github.dyotson.ForzaHorizon1ReImagined`.

## Implementation and validation

`platform/discord_presence.cpp` owns the service, `discord_model.h` formats only
allowlisted primary-user Xbox context IDs, and `kernel/live_xmsg.cpp` observes XGI
`XUserSetContext` (`0xFB/0xB0006`) before its Forza Live sign-in gate. The presence
context is `0x8001`; online mode `0x12`, free-roam variant `0x15` and event variant
`0x16`. Mode values were checked against the player's own executable metadata;
no game strings or metadata are bundled. Party counts come from the newest local
hosted/joined presence session, not the server's global online-player roster.

The native client is Discord's [MIT-licensed classic RPC implementation](https://github.com/discord/discord-rpc),
which Discord has archived in favor of its Social SDK. `cmake/discord.cmake` builds
a static library from the pinned sources under `forza-recomp/thirdparty`, using a
newer pinned RapidJSON revision for current compiler compatibility. Configuring
the game does not download either dependency; revisions, archive hashes and license
locations are recorded in `thirdparty/README.md`. The native runtime retains
both license notices under `licenses/`, including after the launcher cleans the build
tree. The Linux transport supports Flatpak socket paths and queues fragmented reads.
Protocol reference: [Discord's RPC-only Rich Presence guide](https://github.com/discord/discord-rpc/blob/master/documentation/hard-mode.md).

Build isolated tests (no game files or SDK required):

```sh
cmake -S forza-recomp/tests/discord -B out/discord -DCMAKE_BUILD_TYPE=Release
cmake --build out/discord --config Release
ctest --test-dir out/discord -C Release --output-on-failure
python tools/test_discord_rpc.py --exe out/discord/forza -v
# Windows multi-config generator: --exe out/discord/Release/forza.exe
```

Tests change the pipe prefix to a private fixture namespace and never connect to real
Discord. They cover model states, ID validation, framed handshakes, fragmented messages,
ping/pong, activity payloads, reconnect/resend and explicit clear. Windows and Linux run
the same tests in `discord-presence-tests.yml`.

Joining friends through Discord is a later phase of #146. This implementation advertises
no Join button or join secret.

### Car, event and zone (#527)

The Xbox presence contexts carry none of these, so `hooks/discord_details.cpp` (TU4 builds)
samples them every 2 s on the main loop thread after the world update (the hook in
`hooks/telemetry.cpp`), only while sharing and `discord_presence_details` are on, and hands
`discord_presence.cpp` the names. Everything comes from the game:

- **Car:** the loaded car of the player facade, as the pause menu's PlayerBar reads it
  (`platform/fh1_current_car.h`): its `Data_Car` Id, class and 0-1 performance index; the PI
  shown is the class badge's (`sub_82C9F6A0` with the game's `CarClasses` table, so R3 797
  as in the PlayerBar). Year, make and model: `Data_Car` joined to `List_CarMake`, as the car
  select screen shows them; the class letter: `CarClasses.DisplayName`.
- **Event:** the current career event id (`0x833ED728`, see `docs/coop_campaign.md`) ->
  `Events.Name`; also set for Rivals. Free roam's own `FREE_ROAM` event is left out.
- **Zone:** the game files every race track under a zone (`Tracks.EnvironmentId` ->
  `Environments.ShortDisplayName`, the event cards' zone and the map's area labels: Festival,
  Clear Springs, Gladstone, Kettle Hills, The Rockies, Carson, Montano Plains, Red Rock). The
  player's zone is that of the nearest track start (`start_location_00` of the track's
  `media/tracks/colorado/Ribbon_00/TrackRouteNNN.xml`: 99 starts, read once in a few ms) to
  the car's position (`platform/telemetry.cpp`).

Text columns go through the game's own localized getter, so the names are those of the
game's language, DLC cars included. `FORZA_DISCORD_TRACE=1` logs each sample (`DISCORD:
sample: ...`): the car's Data_Car Id, class, raw performance index, event id, position and
the names.
