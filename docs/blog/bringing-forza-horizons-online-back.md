# Bringing Forza Horizon's online back

On 22 August 2023 the online services of Forza Horizon (2012) and Forza Horizon 2 closed. The [closure notice](https://forza.net/news/forza-horizon-online-services-closure) was specific about what would stop: "all activities that rely on online matchmaking, such as races, game modes, free roam and car meets", and "all online leaderboards, and the browsing, downloading and sharing of online UGC". Single player stayed playable.

For the first Forza Horizon, that meant online free roam and the Find Game playlists, the leaderboards, Rivals and their ghost laps, car clubs, and the storefront where players shared paint jobs and photos. When no server answers, the game says so: Multiplayer shows "Server Unavailable", and Find Game offers only the built-in Quick Match playlist, which awards no multiplayer XP.

[Forza Horizon 1: ReImagined](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined) is a static recompilation of the Xbox 360 game to native Windows and Linux. The original PowerPC executable is translated to C++ with [ReIGlue](https://github.com/FH1-ReImagined/ReIGlue), the project's fork of the [ReXGlue SDK](https://github.com/rexglue/rexglue-sdk), and compiled as a PC program. The repository contains no game code and no game data. Players bring their own copy (a disc image, a Games on Demand package or an extracted folder), and the launcher generates the recompiled C++ from that copy and builds the game on their machine. The project is not affiliated with, endorsed by or sponsored by Microsoft, Xbox Game Studios, Turn 10 Studios or Playground Games.

This post covers the online half: [Forza Live](https://github.com/FH1-ReImagined/forza-live), the project's AGPL-3.0-or-later server, which answers the game's own online calls on a public server or on one you run yourself.

## Two layers of online

The first is the console's system layer: sign-in, presence, friends, sessions and matchmaking, reached through Xbox 360 system calls such as `XSessionSearchEx`. In the recompiled game the project's runtime implements those calls and talks to Forza Live over UDP port 21200, for sign-in, presence, rendezvous with UDP hole punching, and a relay for players who cannot reach each other directly. Players forward no ports.

The second is Turn 10's title services, which the game calls over HTTP for its Horizon sign-in, the Find Game playlists, online XP, scoreboards and Rivals ghosts, car clubs, photos, designs and the Message Center. Forza Live answers them on TCP port 21201. The protocol was read from the game's executable and checked against its requests and its reaction to the server's answers.

### Finding the title server

Every 30 seconds the game enumerates title servers (`XTitleServerCreateEnumerator`) and keeps one whose description matches its service mask, `Turn10.*`. In the recompiled game the enumeration returns one entry: the Forza Live server, with the description `Turn10.Horizon` so that it matches the game's pattern. The server has no connection to Turn 10. The game then connects to that address on TCP port 80. The runtime's socket layer sends the connection to the server's HTTP port, and the game posts:

```
POST /Services.Horizon/o.xtsw HTTP/1.1
Host: <server>
Content-Type: bin/xtsw
Content-Length: <n>
User-Agent: Xls Client
Connection: Keep-Alive
X-TitleId: 1297287625                                   (0x4D5309C9)
X-ClassName: Forza.WebServices.Hopper, Forza.WebServices
X-Method: GetList
X-UserId: <the caller's XUID>

<parameters>
```

### The wire format

Parameters and results are records described by type descriptors in the executable's data. On the wire they are big-endian and packed, fields in declaration order; strings and arrays are a u32 count followed by the data. The game has 292 such records. A reply is a 12-byte header (status, code, message length) followed by the results.

The game ignores bytes after the results, so a success header followed by enough bytes set to zero reads as empty results (zero, false, empty strings and lists). A few field types are the exception: an empty date, for one, fails the whole reply. Forza Live answers every method it does not implement that way. Many screens take it as "nothing there"; where the game did not, as for the DLC string table and photo uploads, the server now gives a real answer.

Two details came from watching the game:

- Before an online feature, the game looks it up in a permission table that the sign-in (`User.Logon2`) returns. A feature missing from the table shows "FEATURE UNAVAILABLE" without a request ever reaching the server, so the server sends all 256 entries, enabled.
- The game keeps one HTTP connection open for all its calls. When the server closed idle connections after 15 seconds, the game's next call minutes later was never sent. The server now leaves them open for 6 hours.

## What Forza Live answers

The server is one Python 3 program that uses only the standard library, and it talks to no Microsoft or Turn 10 service.

**Horizon World.** The first playlist in Find Game is a shared online free roam that the server calls Horizon World. Its rounds are the game's own free roam preset at the festival, for up to 8 players. The server places a searching player into the fullest open world and never offers a full one. When no world is open, the player hosts one and drives at once.

This needed client hooks too. In the game's own Find Game sessions, a player who arrived late had to wait for the round to end. The project's client makes the Horizon World behave like a custom free-roam session instead, so a late player gets the game's JUMP IN button and drives straight into the running world. When the host leaves, the game hands the host role to another player by itself. Every player in a world also registers it with the server as a backup, so new players are still placed with the others after the host has gone. In a test on a local server, four copies of the game shared one world; 5 to 8 players have not been tested yet.

**Events and challenges in the world.** The server can take a world to one of 70 festival events from the game's database. Everyone in the world goes to a 30-second wait lobby, races, and returns to the free roam. CPU drivers fill the grid, which the original game's online races never had: every online preset sets `MaxAIPlayers="0"`. Their difficulty follows the share of humans on the 8-car grid: 1 or 2 humans get Easy, 3 to 5 Medium, 6 to 8 Hard.

In free roam, a player can drive up behind another and press X to challenge them. On accept, both race along the game's own GPS route to a landmark about 2.5 km away; the first car within 40 m of it wins. Other players in the world get no prompt. The server's public status page does list running challenges with both gamertags, and the position, heading and speed of each player who sends one. For this feature the game sends the car's position to the server four times a second in Horizon World free roam, and Settings > Online > "Head-to-head challenges" turns that off.

**Friends and chat.** The server keeps friends lists, requests and blocks per player. Text chat covers a session, a world and direct messages between friends. The server relays chat. It keeps the last 200 messages in memory, where only the server's administrator can read them, and never writes them to disk, so they are gone when the server restarts. Chat, like all traffic to the server, is not encrypted (see below).

**Leaderboards and Rivals.** At the end of an event the game submits its best lap or race time with the car, class and assists. When it is the player's new best, the server asks for the ghost, and the game uploads its recording of the lap. When another player picks X RIVALS, their game downloads the ghost and the ghost car drives the recorded lap beside them. When they beat another player, the game reports it and the server puts a RIVAL CHALLENGE in that player's Message Center.

Speed traps, speed zones, events and Rivals, online XP and Title Update 4's 1000 Club all have boards in the game. Monthly Rivals is a server option with two sample trials: they list and load, and a trial raced to its end has not been tried yet. The public server's speed trap, speed zone, event and Rivals boards are also on the [project website](https://forza.dyotson.dev/leaderboards.html). The game does not keep online XP in the save: it reads it from the server at every sign-in. So the server stores the online XP from each profile update the game sends.

A signature (below) proves who sent a score, not that the score was driven. Speeds above a trap's best known Xbox 360 result are flagged in the server's log, and with the trap limits on they are dropped. One run with the limits on the public server, on 23 October 2026, removed 66 such results.

**Car clubs.** They work through the server and the Message Center: join requests, invitations, roles, and the club garage, where a member can share a car. Creating a club in the game and borrowing a shared car have not been tested yet.

**The storefront.** Players share paint jobs, vinyl groups and photos, and can browse, buy (everything is free), rate and apply designs. Forza Horizon has no tune or replay sharing. Each design gets a six-character share code that finds it from the game's search, and uploads are taken only from games that sign their requests. Designs are listed in the game at once. The project website's designs page shows a design, with its preview, share code and the creator's gamertag (never the XUID), only after its preview has passed automatic screening.

**Big Shots.** Photo mode can render a "Big Shot": a 3840x2160 picture made in 3 x 3 tiles and uploaded in 95 chunks of 256 KiB. The game's message still says it is uploading to ForzaMotorsport.net. The picture actually goes to the Forza Live server the player is on, not to Microsoft or Turn 10. On Forza Live, a Big Shot from a signed game becomes a 1920x1080 PNG, credited by gamertag and never by XUID, in a public feed that the project website copies into its gallery. Each player can publish 10 a day and 200 in all. With default settings, builds before v0.2.3 captured the loading screen instead of the scene; the server detects that and publishes nothing.

On the public server, new photos wait for a screening worker before they are published. The worker is a separate, optional program. It checks each picture on the same machine as the server, on the CPU, with three image models (a nudity classifier, an image-text model for hate symbols, gore and nudity, and text recognition) and word lists, and sends it to no outside service. It releases clear pictures, hides confident hits and leaves doubtful ones for the maintainer. A server without the worker publishes photos at once.

**Co-op campaign.** The Co-op Festival playlist is a Horizon World among friends. The host types `/event FR05` in World chat, and everyone races that festival event with CPU drivers and the event's class and car restrictions. A player with no qualifying car is lent a stock one that meets the event; for the four events no stock car fits, the server warns that only players with a fitting upgraded car can race. The server keeps a ledger of each human's place, cross-checked against the host's game. After the session, the launcher writes each player's own result into their save, after a backup, and only when it beats the result already there.

**Not there.** Base-game and Title Update 4 players do not meet. The server can store auctions, but the game has no Auction House screens to open them. The server serves no DLC string tables: it answers the game's request with the game's own "no table".

## Identity: signing instead of sharing a secret

The game's requests name the player in one header, `X-UserId`, and anyone can send it. IP addresses cannot do the job either: several players can share one address, and through a tunnel the connection itself comes from the tunnel, not the player. An address says where a packet came from, not who sent it.

A player's identity is a random XUID created on their PC the first time they play online. The XUID is not a secret: other players, and the public server's status page, see it next to the gamertag. From v0.1.15 to v0.2.1, the game proved it with a "friends key": 16 random bytes sent in the clear in every sign-in and, from v0.1.33, in every title service call, to whichever server the player used. A tunnel, anyone on the path, or another server's operator could copy it. Players on v0.2.1 or older should update: until the cutoff below, their game still sends the old key in the clear.

Since v0.2.2 the game and the launcher have an Ed25519 key pair and sign every sign-in and every HTTP request with it. Its private seed is stored in the player's `live_identity.txt`, which should never be shared or posted. The server binds the public key to the XUID on first use and keeps only the public key, so nothing it receives or stores can sign. An HTTP request carries one header:

```
X-Forza-Auth: FORZA-ED25519-V1 Server=<name>, Xuid=<16 hex>, Time=<unix>, Nonce=<32 hex>,
              Signature=<128 hex>
```

The signature covers ten lines: the scheme tag, the method, the path and query, the server name, the XUID, the time, the nonce, `X-ClassName`, `X-Method` and the SHA-256 of the body.

- Because the server name is signed, a server that lists its names refuses a signature made for another one.
- A signature more than 120 s off the server's clock is refused, and each nonce is accepted once.
- The UDP sign-in signs a challenge from the server in the same way.
- The server verifies with a pure-Python implementation of RFC 8032. A check costs about 1 ms, and the server makes at most 500 a second.

On the public server, existing accounts move over with one upgrade: the game sends its old friends key once, inside a signed sign-in, and from then on that key is refused for the XUID. On other servers the game does not send the old key; an account still bound to one stays off friends and chat until the operator lets the new game in (`--rebind-legacy`). Once the server has a key on record for an XUID, any call that changes data must be signed with that XUID's key, or, while the server still takes old friends keys, carry the XUID's one; otherwise it is refused.

On 15 November 2026 at 12:00 UTC the public server stops taking the old friends keys. Versions before v0.2.2 then lose friends, chat and online saves until they update.

Signing proves who sent a request and that its method, path and body were not altered. It does not encrypt: traffic to the server, chat included, is plain.

## Run your own

Forza Live is free software under the GNU Affero General Public License, version 3 or later (AGPL-3.0-or-later): if you run a modified version for other people over a network, you must offer them its source. It needs Python 3 and nothing else:

```
python3 forza_live_server.py --http-port 21201
```

The repository also has a Dockerfile. Forward UDP 21200 and TCP 21201 on the router to the host, then players pick **Settings > Online > My own server** in the launcher and enter the address. The server gives the players in a room each other's public and LAN addresses, so their games can connect directly, as the Xbox 360 game did. On the public server this works through the playit.gg tunnel too: the tunnel passes on each player's real address in a PROXY protocol v2 header, and the server hands that to the others in the room. When a server can't learn a player's public address, it gives the others none, and their traffic goes through the server instead. When an ISP drops unsolicited UDP, a tunnel such as playit.gg works. The launcher's **This PC** mode runs the same server on the player's own PC, for that player only.

The server ships no game data. World events and the Co-op Festival need the game's database from the operator's own copy (`--game-db`), and leaderboard and car names come from a file the operator writes from their copy. `--server-name` lists the names players use to reach the server, which signatures are checked against.

## What's next

From the docs and the open issues:

- Volunteer regional relays ([#516](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/516)), and verified leaderboard entries from the main server, tagged by server and build ([#517](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/517)).
- Daily and weekly challenges run by the server, and scheduled festival playlists in the Horizon World ([forza-live #1](https://github.com/FH1-ReImagined/forza-live/issues/1), [#2](https://github.com/FH1-ReImagined/forza-live/issues/2)).
- More than 8 players in a world ([#2](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/2)); the engine's per-player state beyond 8 is untested.
- For co-op: applying results inside the game, for players who start it without the launcher, and the events' prize cars ([#309](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/309)).

## Links

- Game: https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined
- Server (Forza Live): https://github.com/FH1-ReImagined/forza-live
- Website and leaderboards: https://forza.dyotson.dev
- Discord: https://discord.gg/yu7zScdcWw

Forza Horizon is © Microsoft. Forza, Forza Horizon and Xbox are trademarks of Microsoft, named here only to describe what the project works with.
