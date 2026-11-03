Forza Horizon 1: ReImagined for Linux - https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined
Built from commit @COMMIT@.

Needs x86-64 Linux with glibc @GLIBC@ or later and libstdc++ @GLIBCXX@, a Vulkan 1.3 GPU
driver (tested on AMD with Mesa RADV), the X11/Wayland client libraries, about 8 GB of free
RAM and your own copy of Forza Horizon for Xbox 360 (Games on Demand package). This package
contains no game files.

1. Extract the game files from your package with Python 3 (it is only read):

       python3 xcontent_extract.py /path/to/4D5309C9/00007000/<name> extract ~/forza/game

2. Play, pointing at the extracted files (remembered for the next time):

       ./play.sh ~/forza/game
       ./play.sh --resolution_scale=2

   play.sh starts a Forza Live server on this machine (needs Python 3), so the game is
   signed in to an emulated Xbox LIVE. To play without LIVE: ./play.sh --offline

3. Playing online with others: run the server where everyone can reach it
   (python3 server/forza_live_server.py --http-port 21201; open UDP 21200 and TCP 21201)
   and start each game with
       FORZA_SERVER=host:21200 ./play.sh
   or put   live_server = "host:21200"   in forza.toml next to play.sh. Everyone on the
   server is your friend. Two ways to play together:
   - Party: in Multiplayer, one player picks Invite Friends in the party lobby (this
     invites everyone on the server) and the others join it; the leader picks Create
     Custom Game, sets the game type under Setup Game and starts once all are ready.
   - Find Game, then the Races playlist: the first player hosts the lobby, the next ones
     join it, and the race starts after the intermission once two players are in.
   The server also stands in for Turn 10's title services ("Turn10.Horizon", the
   playlists and online XP, kept per player in the server's data directory).

Saves, your online identity and the log (forza.log) are in ~/.local/share/forza.
If the game dies with SIGBUS at start, /dev/shm is full of guest memory left by killed
games: delete the /dev/shm/xenia_memory_* files no running game uses.
