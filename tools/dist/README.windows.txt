Forza Horizon 1: ReImagined for Windows - https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined
Built from commit @COMMIT@.

Needs the Microsoft Visual C++ 2015-2022 x64 redistributable, a Vulkan 1.3 GPU driver and
your own copy of Forza Horizon for Xbox 360 (Games on Demand package). This package
contains no game files.

1. Extract the game files from your package with Python 3 (it is only read):

       python xcontent_extract.py "C:\path\to\4D5309C9\00007000\<name>" extract C:\path\to\extracted\game

2. Play, pointing at the extracted files (remembered for the next time):

       play.bat C:\path\to\extracted\game
       play.bat --resolution_scale=2

   play.bat starts a Forza Live server on this PC (needs Python 3), so the game is signed
   in to an emulated Xbox LIVE. To play without LIVE: play.bat --offline
   Without play.bat: forza.exe --game_data_root=C:\path\to\extracted\game

3. Playing online with others: run the server where everyone can reach it
   (python server\forza_live_server.py --http-port 21201; open UDP 21200 and TCP 21201)
   and start each game with
       set FORZA_SERVER=host:21200
       play.bat
   or put   live_server = "host:21200"   in forza.toml next to forza.exe. Everyone on the
   server is your friend. Two ways to play together:
   - Party: in Multiplayer, one player picks Invite Friends in the party lobby (this
     invites everyone on the server) and the others join it; the leader picks Create
     Custom Game, sets the game type under Setup Game and starts once all are ready.
   - Find Game, then the Races playlist: the first player hosts the lobby, the next ones
     join it, and the race starts after the intermission once two players are in.
   The server also stands in for Turn 10's title services ("Turn10.Horizon", the
   playlists and online XP, kept per player in the server's data directory).

Saves, your online identity and the log (forza.log) are in Documents\forza.
Settings go in forza.toml next to forza.exe, e.g.   resolution_scale = 2
