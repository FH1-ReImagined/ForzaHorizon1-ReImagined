# Privacy policy

This policy covers Forza Horizon 1: ReImagined: the Windows installer, the Linux packages, the launcher, the game the launcher builds on your PC, the project's public Forza Live server, and the project website, forza.dyotson.dev, with its optional website accounts. It says what is sent over the network, when, where it goes, what is stored and for how long, what is public, and how to turn each thing off.

Last updated: 2026-11-04. Every change to this policy is a change to this file, so its history on GitHub lists them: https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/commits/main/PRIVACY.md

## In short

- The launcher and the game have no analytics, no telemetry and no advertising. Crash reports are never sent by the launcher on its own.
- These features are on by default and send data over the network:
  - online play on the project's public Forza Live server;
  - after each session online: sharing shader lists with that server, and asking it for your co-op campaign results;
  - asking the public server for your progress in its daily and weekly challenges, while the Play page is open;
  - a check for launcher updates on GitHub each time the launcher opens;
  - your activity in the game (with your car, the event and the area of Colorado), shown through the Discord desktop app if it is running.
- One choice turns all of them off: "Online features" in the Windows installer, or the same question the launcher asks the first time it opens on Linux. You can also turn each one off later in the launcher; see "How to turn each thing off".
- Separately, on Windows with an NVIDIA graphics card, the first Play downloads NVIDIA's DLSS runtime from GitHub, and actions you start (Build, Make HD pack, Make decal pack) download tools. See "Downloads".
- Settings > Online > Server > "Offline" stops all contact with game servers.
- When you play online, the server receives your gamertag, a random player ID (your XUID) and your IP addresses. Other players see your gamertag and XUID, and the server gives the players in your room your IP addresses so that their games can connect to yours.
- Traffic to the game server is not encrypted. That includes chat messages. Your keys are not sent: the game and the launcher sign their requests with your signing key, and the server keeps only its public key. The one exception is your friends key from older versions, which the game sends once to the public server to move your account to the signing key.
- The public server keeps most records with no time limit. Its status page and leaderboards are public.
- Website accounts are optional, and you don't need one to play. If you make one (with Discord, GitHub or an email address), the website stores an account id, a number from Discord or GitHub or a one-way hash of your email address, and your sign-ins. It uses one sign-in cookie and one security cookie, and has no analytics, tracking or advertising. You can link the account to your game from the launcher; a linked account can like community photos and designs and delete your own. You can unlink it or delete the account at any time. See "The website and website accounts".
- What you choose to share is public: a photo mode **Big Shot** you upload is published on the project website with your gamertag, and paint jobs and vinyl groups you share on the storefront can be found, downloaded and rated by other players in the game, with their previews shown on the project website. They carry your player ID (the one the leaderboards publish) and their like totals, and the website has a page for each player that shows them together. Your XUID is not published with them.

## Who is involved

- Your PC runs the launcher and the game.
- The project maintainer runs the public Forza Live server, forza-reimagined.playit.plus. You reach it through a tunnel run by playit.gg. playit.gg therefore sees your IP address and carries your game traffic, which is not encrypted, so playit.gg can read it.
- GitHub hosts the releases, the update check, most downloads and the issue tracker. GitHub sees your IP address whenever the launcher or your browser contacts it.
- The project maintainer also runs the server that serves the project website, forza.dyotson.dev, and its website accounts.
- If you sign in to the website with Discord or GitHub, that service handles the sign-in. If you sign in with an email address, a transactional email service (an email relay) delivers the sign-in code to you.
- Microsoft, Discord and Flathub are involved only in the cases described below.
- If you play on a server that you or a friend host ("My own server"), that server's operator receives what this policy says goes to "the server". This policy does not cover what that operator does with it.

Their privacy policies:
- GitHub: https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement
- playit.gg: https://playit.gg/privacy-policy
- Discord: https://discord.com/privacy
- Microsoft: https://www.microsoft.com/en-us/privacy/privacystatement

## What the launcher sends

**Steam shortcut setup (Linux).** About > Add to Steam works locally. It lists account-directory
numbers so you can choose one, checks whether Steam is running, and writes the selected account's
shortcut file and bundled artwork only after you click the button. A backup stays beside the shortcut
file until you remove it. No Steam credentials are read or sent and no Steam web API is contacted.
The Flatpak opens manual instructions and does not write Steam files or run host commands.
The Deck clock reminder only reads local sysfs information;
it never changes clock settings. Opening the setup guide opens GitHub in your browser.

### Update check (on by default)

- What: an HTTPS request to api.github.com that asks for this project's latest release. It names the launcher and its version in its User-Agent header and contains nothing else about you.
- When: 1.5 seconds after the launcher opens (packaged launchers only, not a source checkout), and when you click "Check for updates" on the About page.
- Turn off: About > "Check for updates when the launcher opens".
- An update is downloaded only when you click "Download update" or "Update and restart". It comes from this project's GitHub releases, and the launcher checks its SHA-256 checksum before using it.

### Shader-list sharing (on by default when you play online)

- What: after each game session, the launcher sends the server descriptions of the graphics pipelines the game built: shader hashes, render settings, how to rebuild each shader from your own game files, and which kind of render-target path your graphics card supports. Each entry is sent once; shader_share.json on your PC records what was sent. No game code or data, file paths, gamertag or logs are sent.
- The request headers carry your XUID and a signature made with your signing key (not the key itself), so that the server counts each player once. The request uses plain HTTP, so it is not encrypted.
- Where: the server you play on (the public server or your own). Nothing is sent in "Offline" or "This PC" mode.
- What the server keeps: for each entry, a hashed form of your XUID, with no time limit, and counters per day that are cleared every day. It publishes only entries that several players reported, without player IDs.
- Turn off: Settings > Online > "Share shader list". "Offline" and "This PC" also stop it.
- Setup > "Prebuild shaders" downloads the published list from the same server. That request sends only a version string. "Share shader list" does not stop this download; "Offline" or "This PC" does.

### Co-op campaign results (on by default when you play online)

- What: after each session on "Public server" or "My own server", the launcher asks the server for your pending co-op campaign results, then confirms the ones it handled. It does this only when you have a save and a Forza Live identity on this PC. These requests carry your XUID and a signature made with your signing key (not the key itself) over plain HTTP. The answer can include the gamertag of the player who hosted the event.
- Turn off: Settings > Online > "Co-op campaign results". "Offline" and "This PC" also stop it.

### Daily and weekly challenges (on by default with "Public server")

- What: the Play page's Challenges card asks the public server for its current daily and weekly challenges and your progress in them (`/challenges/progress`). The request carries your XUID and a signature made with your signing key (not the key itself) over plain HTTP. The answer contains the objectives, your progress and how many players finished each objective; no other player's name or ID.
- When: a moment after the Play page shows, then at most every 5 minutes while it stays open, and after each game session. Only when Server is "Public server" and this PC has a Forza Live identity. Nothing is asked with "My own server", "This PC" or "Offline".
- The server counts your progress from the results your game already sends it when you play signed in (races, speed traps and zones, 1000 Club medals, Big Shots). Turning the card off stops the launcher asking; it does not change what the game sends.
- The public server may send a weekly credits gift for finished challenges to your Message Center in the game, like any gift. A community server that runs challenges keeps its own progress; the launcher shows only the public server's.
- Turn off: Settings > Online > "Daily and weekly challenges". "Online features" off turns it off too.

### Website account link (only when you click Link)

- What: when you click Settings > Online > Website account > "Link to website account", the launcher asks the website's accounts service for a link, opens the website in your browser, then asks every 3 seconds whether a website account claimed it, and confirms the account you approve (or declines it, with "Not me"). "Unlink" and "Sign out everywhere" send one request each. These requests carry your XUID and a signature made with your signing key (not the key itself), over HTTPS. The answers contain the website account's id, its sign-in method and the day it was made, never another player's XUID.
- When: only when you click one of those buttons. After a link is made, the launcher also asks for the link's state once a session, when the Website account card is shown.
- Where: forza.dyotson.dev (the project website's accounts service), whichever game server you use.
- Turn off: don't link. "Unlink" ends the link; after that the launcher sends nothing until you link again.

### Servers page (only when you open it)

- What: an HTTP request to each server on the page for its status page (`/status`: whether it is up and how many players are online), to show the server's ping, and a request to the public server for its signed list of community servers (`/servers.json`). They contain nothing about you; each server sees your IP address, and the User-Agent header names the launcher.
- When: when you open the launcher's Servers page, then every 30 seconds while the page stays open. Nothing is sent when the page is closed.
- Where: the public server, the servers on its list, and the addresses you added on the page. Community servers are run by their owners, not by this project.
- Turn off: do not open the page. With "Online features" off, the page sends nothing until you click "Check servers".

### Downloads

Each of these downloads shows the site it comes from your IP address, and its User-Agent header names the launcher. Nothing else about you is sent. Unless noted, the launcher checks each file against a SHA-256 checksum kept in its own code. None of them is part of the "Online features" choice: they run only when you start the action named, or, for DLSS, when you press Play.

- Build (the first time, or when a tool is missing): LLVM, CMake and Ninja from GitHub. On Linux, also the ReXGlue SDK from GitHub.
- Build on Windows without Visual Studio 2022: the xwin tool from GitHub. xwin then downloads Microsoft's C++ runtime and Windows SDK files from Microsoft's servers. Only the xwin archive is checked against a checksum kept in the launcher; the files xwin downloads from Microsoft are not. This runs only after you tick "I accept Microsoft's license terms...".
- Make HD pack and Make decal pack: the Real-ESRGAN upscaler from GitHub, after you confirm (once for both).
- NVIDIA DLSS (Windows with an NVIDIA graphics card): when you press Play, the launcher downloads NVIDIA's DLSS runtime (about 56 MB) and its license from NVIDIA's repository on GitHub, if they are not on your PC yet. With Settings > Display > "Graphics API" set to Vulkan (the default), this happens even when DLSS is off, so that the in-game menu can turn DLSS on later. To avoid it, set "Graphics API" to Direct3D 12 and leave "NVIDIA DLSS" and "NVIDIA DLAA" off. If the download fails, the game still starts.

### Crash reports

- The launcher uploads nothing after a crash. It shows a report that you can read and edit, then "Copy report", "Save report..." or "Open GitHub issue".
- "Open GitHub issue" opens your browser at a new GitHub issue with the report filled in. The report travels in the page address, so your browser sends it to github.com as soon as the page loads, before you press Submit. A report too long for that is copied to the clipboard instead.
- The report contains:
  - the launcher and game versions and your operating system version;
  - your CPU, graphics card, RAM and video memory;
  - your online and profile mode and some launch settings;
  - the crash details and up to 200 lines of the game's log.
- Before you see the report, the launcher tries to remove file paths with your user name, XUIDs and other IDs of that form, IPv4 addresses, web addresses and tokens. This is best effort. It does not remove gamertags or chat messages, which the log can contain. GitHub issues are public, so read the report before you post it.
- If the game crashes in a way its own crash handler does not catch (for example inside the graphics driver), Windows Error Reporting, which is part of Windows, may send data about the crash to Microsoft, depending on your Windows settings. The project does not receive that data.

### Links

Buttons that open web pages (the project on GitHub, this policy, Microsoft's license, SteamVR's store page, WiVRn) open your browser. The launcher itself sends nothing for them.

## What the game sends

### Online play (Forza Live)

Settings > Online > Server has four choices. "Public server" is the default. "This PC" runs a server on your own PC that only you use. "My own server" connects to a server that you or a friend host. "Offline" connects to no server.

While a server is set, the game sends it the following every few seconds, from the moment the game starts until you quit:
- your XUID, a random player ID created on your PC the first time you play online;
- your gamertag. If you have not set one in Settings > Profile > Gamertag, the game makes one: your computer's login name where it can read one (on Linux and the Steam Deck it can), otherwise "Driver" and four digits;
- your PC's local network (LAN) IP address and the game's network port;
- your room name, your public key and a signature made with your signing key (not the key itself), which proves to the server that the XUID is yours. If the public server still has your account under the friends key of an older version, the game also sends that friends key once, to move the account to your signing key.

The server also sees the IP address you connect from. On the public server you connect through playit.gg's tunnel, so playit.gg sees it. These messages are sent even when "Sign in to Xbox LIVE" is off. Only "Offline" stops them.

While "Sign in to Xbox LIVE" is on (the default), the game also:
- fetches the list of players in your room every 3 seconds: their gamertags, XUIDs, status and cars;
- publishes your presence: your online status, your current car and the session you host;
- sends your matchmaking searches and the sessions you host, and Xbox LIVE skill stats around online races;
- syncs your friends list every 10 seconds, and sends the friend requests, blocks and gamertag searches you make;
- sends the chat messages you write, the list of sessions you are in, and the content your game has that changes which cars exist: the numbers of the DLC packs installed and the names of the mods you loaded that change cars or the game database (not your other mods). The server compares it with the players in your session and tells you, and them, what you do not share, because a car that one game lacks looks like a plain placeholder in the other. It is kept in memory only, never written to disk, and shared only with the players of your session as that notice, and asks the server every 10 seconds which car club you are in;
- in Horizon World free roam, sends your car's in-game position, heading and speed four times a second, for head-to-head challenges (Settings > Online > "Head-to-head challenges" turns this off);
- sends an invite only when you send one ("Show game invites" controls whether invites from others are shown to you);
- uses the server's title services over plain HTTP: sign-in (gamertag and career level), profile progress (XP, credits, race history), race scores and lap times, Rivals ghost replays, photos you upload or share (image, preview, title, description), photo mode Big Shots you upload (a 3840x2160 picture and its file name), paint jobs and vinyl groups you share on the storefront (the design, its preview, title, description and keywords, and the car it is for), car clubs and the cars shared in them, auctions and gifts, and Message Center checks. Each of these requests carries your XUID, a signature made with your signing key (not the key itself) and the version of the game that sends it (for example 0.2.1/tu4).

None of this traffic is encrypted.

Turning "Sign in to Xbox LIVE" off stops the list above, but it does not hide you. The server still receives the messages described before it, counts you as online, lists you on its status page, and gives the players in your room your gamertag and XUID. Only "Offline" hides you.

Your IP address and other players:
- The server gives the other players in your room your public IP address and port and your LAN IP address and port, and the game connects to them directly when it can, as the Xbox 360 game did. They can then see your public IP address.
- This is the same on the public server, which you reach through playit.gg, and on a server you connect to directly ("My own server"). The public server learns your public address from the tunnel. Until late October 2026 it could not, and it gave other players no IP address.
- When the server does not know your public address (a tunnel that does not report it), it gives other players none, and the traffic between you and them goes through the server.

### Discord activity (on by default)

- If the Discord desktop app is running, the game tells it what you are doing (for example "Exploring Colorado", "Racing online", or the number of players in an online lobby) and when you started. This goes to the Discord app on your PC over a local connection. Discord then shows it to your Discord contacts, under Discord's own privacy settings and privacy policy. Your gamertag, XUID and IP address are not part of it.
- It also tells it your current car with its class and PI, the event you are racing in and, in free roam, the area of Colorado you are in (for example "Red Rock"), with the names the game uses. Nothing you type is part of it.
- Turn off: Settings > Online > "Share activity on Discord". To keep only the activity: Settings > Online > "Share car, event and area".

### What stays on your PC

- Controller haptics (DualSense adaptive triggers and Xbox trigger rumble, off by default) send car data from the game to the launcher over 127.0.0.1 only.
- VR (off by default) talks only to your PC's OpenXR runtime. Streaming to a headset, for example with WiVRn over Wi-Fi, is that runtime's own traffic.
- "This PC" mode runs the server on 127.0.0.1 only.
- Hardware detection reads your graphics card, CPU and RAM with tools on your PC. It is used to recommend settings, and in crash reports that you choose to send.
- The first time you play online on Windows, Windows Firewall may ask whether to allow the game on networks.

## What the installer and the packages do

- Windows installer: it shows this policy and the "Online features" choice (ticked by default; untick it to turn all of the features above off). It installs the launcher for your user account only, by default in %LOCALAPPDATA%\Programs\ForzaHorizon1-ReImagined, and adds a Start menu entry, a desktop shortcut (which you can untick) and an uninstall entry. It writes your "Online features" choice to install_options.json in the install folder, where the launcher reads it. When you install an update with "Online features" ticked, your current launcher settings stay as they are. For a silent install, /ONLINE=no turns the features off.
- If the Microsoft Visual C++ 2015-2022 x64 runtime is missing, the installer says so before installing, then installs that runtime for all users of the PC, and Windows asks for administrator approval. The runtime is included in the installer, so the installer itself downloads nothing. Uninstalling leaves the runtime installed.
- If "Start the launcher" is ticked on the installer's last page, the launcher starts, and with "Online features" on, its update check runs.
- Linux .tar.gz: install.sh copies files into ~/.local/share/forza-reimagined and adds a menu entry. It uses no network. The first time the launcher opens, it asks the "Online features" question.
- Flatpak: Flatpak itself downloads the Freedesktop runtime and an LLVM extension from Flathub, after asking you. The first time the launcher opens, it asks the "Online features" question.

## What is stored on your PC

- The launcher's own files, in %APPDATA%\ForzaReImagined (Windows) or ~/.config/forza-reimagined (Linux):
  - launcher.json, your settings;
  - launcher.log, the launcher's errors with technical details. It starts over at 2 MB and keeps one older copy;
  - updates, the launcher updates you downloaded. In the Flatpak they go to ~/Downloads/ForzaHorizon1-ReImagined instead.
- Game data, in %LOCALAPPDATA%\ForzaReImagined (Windows; older installs used Documents\forza) or ~/.local/share/forza (Linux), or in a folder you chose in the launcher:
  - saves, the built game, downloaded tools and shader caches;
  - forza.log, the game's log of the last session (at most 100 MB: from 50 MB it continues in a new file and keeps the first part as forza.1.log), and forza.previous.log (with forza.previous.1.log) from the session before;
  - shader_share.json, which records the shader entries already sent;
  - live_identity.txt, which holds your XUID, gamertag, friends key and signing key;
  - with "This PC": the local server's data in live_server, including its server.log and its player and friends records;
  - with "Continue a Xenia Canary save": a copy of that save in a xenia-<XUID> folder.
- In the Flatpak, both folders are inside ~/.var/app/io.github.dyotson.ForzaHorizon1ReImagined.
- Uninstalling keeps all of these files. Delete the folders yourself to remove them.
- forza.log (and its other copies) can record your LAN IP address, your public IP address (on a server you connect to directly), the server's address, your country and language settings, other players' LAN IP addresses, the first two numbers of their public IP addresses (from 0.2.4; older versions wrote them whole), their gamertags and XUIDs, and the chat messages you send and receive. Check it before you attach it to a public issue.
- Keep live_identity.txt private. Its signing key (and, for older versions, its friends key) is the secret that proves to the server that your friends list is yours.

## What the public server keeps, and for how long

Records are kept by XUID.
- Player record: gamertag, last sign-in time, number of sign-ins, career level and progress, credits, and details of your last race. No time limit.
- Friends: your gamertag, when you were last seen, the public key of your signing key (until your game has moved your account to it: a hash of your friends key), and your friends, friend requests, blocks and chat mutes. If you have no friends, requests or blocks, the record is removed when the server restarts more than 180 days after you were last seen. Otherwise there is no time limit.
- Scores and Rivals ghosts: your best score on each leaderboard, with gamertag, date, car, class and assists, and your best ghost replay (the server clears your gamertag from the replay). Each score also records the name of the server that took it, the game version that sent it, and whether the request was signed with your key. A result from a request that was not, or whose ghost fails the server's checks, is kept off the leaderboards for review (your best such result on each leaderboard, with its ghost). A new best replaces the old one. No time limit. The maintainer may use the driven lines in ghost replays to draw roads on the map on the project website; no names or player IDs are shown there.
- Skill stats: rating and number of games, with gamertag. No time limit.
- Photos you upload: image, preview, title, description and time, up to 100 per player. You can remove shared photos in the game, and the oldest can be replaced. No time limit.
- Community photos (Big Shots), only from games that sign their requests: a 1920x1080 copy of the picture, with your gamertag at the time, the upload time and your XUID (kept private, to apply limits and bans). The 3840x2160 original is not kept. Up to 10 a day and 200 in total per player. The copy is kept until you delete it from your account page on the website (with an account linked to your game) or the maintainer removes it; to have a photo removed otherwise, see "Contact". New photos may be checked automatically for nudity, violence or hate symbols before they are published; that check runs on the server and sends the picture nowhere else.
- Shared designs (paint jobs and vinyl groups): the design, its preview, title, description, keywords, car, share code, your XUID (private) and gamertag, the time, its downloads and ratings, and which players reported it. Up to 25 of each kind per player. The design itself contains your XUID, so it is only given to players who download it in the game, never published elsewhere. You can remove a design in the game (REMOVE in MY SHARED DESIGNS) or from your account page on the website; a design reported by three players is hidden. No time limit.
- Car clubs, auctions and gifts: club memberships, roles and history, shared cars, auction listings and bids, and gifts with their messages. No time limit. The server uses your club membership to deliver your Club chat messages, to show your clubmates (by gamertag, with `/club`) that you are online and, in the same room, whether you are in a Horizon World or Co-op Festival world and how many players it has, and to offer you a Horizon World with a clubmate first when you search. Players who have blocked each other don't see each other this way.
- Message Center notifications: removed when you clear them. After 30 days they are no longer shown, but they are not deleted.
- Co-op campaign results: removed once they are handled, or after 30 days.
- Daily and weekly challenges: your progress on the objectives of the current and the previous day and week, with the game versions that sent the results that counted, and how many objectives you have finished. Older days' and weeks' progress is removed when a new day or week starts; the number finished has no time limit. For the weekly gift: how many objectives you finished in each of the last 8 weeks, and when a gift was sent (the gift itself is kept like other gifts).
- Shader lists: see "Shader-list sharing" above.
- Community events, only if an organizer enrolled you: your XUID, your public key (or a hash of your friends key), attendance times and rewards. No time limit.
- Content lists: the DLC pack numbers and car mod names your game reports are held in memory while you are online, to tell the players of a session what they do not share. They are not written to disk and are lost when the server restarts.
- Chat: the last 200 messages are held in memory, where only the server's administrator can read them. A message to your car club (the Club channel) goes to every member of your club who is online, wherever they are, and is held the same way, with the club's tag. They are lost when the server restarts and are not written to disk. Mutes and bans set by the administrator are stored until they expire.
- Connection state: for the players online at that moment, the server writes their addresses and ports, session token, XUID, gamertag, room and presence to a file every 30 seconds, so that a restart does not drop them. The file is overwritten, not deleted.
- Backups: the server may keep one previous copy of each of its data files.
- Server log: the log records sign-ins (gamertag, XUID, room and the address and ports the player connected from), web requests with the address they came from, profile and race updates, scores, friend and block actions, and gift messages. Chat text is not logged. For players who come through the playit.gg tunnel, the log records the address the tunnel reports for them, which is their own. The log is not published. There is no fixed period after which it is deleted yet.

To have your records on the public server removed, see "Contact".

## The website and website accounts

The project website, forza.dyotson.dev, is static pages, plus an accounts service at forza.dyotson.dev/api/. Both run on a server the project maintainer runs.

### Visiting the website

- The website's web server logs each request: your IP address, the time, the page or file (without anything after a "?"), the result and your browser's User-Agent. These logs are kept for 14 days.
- The pages load nothing from other sites, and there are no analytics, tracking or ads.
- Only the account page talks to the accounts service, and, once you have signed in on that browser, the photo and design pages, which ask it which photos and designs your game liked and for the current like totals. The pages remember in your browser (localStorage, key fh-signed-in) whether you signed in, so that the menu can say "Account" and only a signed-in visitor's pages call the service. That value never leaves your browser. Every other visitor's pages load nothing from the accounts service.

### Website accounts (optional)

A website account is optional, and you don't need one to play, online or offline. Until you link it to your game (see "Linking your game") it can only browse. You must be 13 or older to make one. You make one by signing in for the first time on the account page (forza.dyotson.dev/account.html), in one of three ways:
- **Discord or GitHub.** The browser goes to Discord or GitHub, which asks you to allow the sign-in, then comes back. The website asks Discord only for the "identify" permission and asks GitHub for no permission. It keeps only your account's numeric id at that service. It does not keep your username, display name, avatar, email address or the access token.
- **An email address.** The website mails you an 8-digit code, which works for 10 minutes and only in the browser where you asked for it. The email relay receives your address and the mail, and keeps them under its own policy. The website never stores your address: it keeps only a one-way hash of it (HMAC-SHA256 with a secret key), which can't be turned back into the address. It is used to find your account the next time you sign in with the same address.

What the website stores about an account:
- the account id (for example K7Q2-XM4P), when the account was made and when it last signed in;
- the numeric Discord or GitHub id, or the hash of the email address;
- your sign-in sessions. A session ends 30 days after you last used it, 90 days after the sign-in, or when you sign out. An account keeps at most 20;
- while you sign in: the emailed code or the Discord or GitHub sign-in in progress, in your browser's session, for at most 10 minutes;
- recent activity: sign-ins, sign-outs, when the account was made, when a game was linked, moved or unlinked (with that game's gamertag), and photos and designs you deleted (with their title), without IP addresses. It is shown on your account page and kept for 90 days;
- to slow down abuse, the accounts service counts sign-in attempts and code requests for each IP address and each email hash. Those counts are kept for an hour or a day.

Cookies, set only when you use the account page:
- `__Host-fh_session` holds your sign-in session.
- `__Host-fh_csrf` protects your account from requests made by other websites.

Neither is used for tracking or shared with anyone.

### Linking your game

Linking makes your game key the account's second factor: the website's features for players (likes, deleting your own photos and designs, and later uploads) need a linked game. You start a link in the launcher (Settings > Online > Website account), sign in on the website page it opens and click Link there, then confirm the website account's id in the launcher. Both sides have to approve, so nobody can link your game to their account, or their game to yours, without you.

What a link stores:
- your XUID, the public half of your signing key (never the key itself), your gamertag (shown on your account page), and when the link was made and moved;
- while a link is in progress, for 10 minutes (deleted within an hour): your XUID, public key and gamertag, one-way hashes of the link's secret token and code, and the website account that claimed it;
- the random number of each signed launcher request, for 4 minutes, so that no request works twice.

To check the requests, the accounts service reads, without changing anything, what the public game server already keeps about you: the public key your XUID is bound to, your gamertag, when your game first signed in with its key, and whether your XUID is banned. It needs one sign-in to the public server signed by your game's key before you can link.

Rules: a game links to one account and an account to one game. If you link your game to another account, the link moves, and the old account's page says so. A game or an account can change its link once a day. The launcher's "Unlink" and "Sign out everywhere" work even if someone else got into your website account. If your XUID is later bound to a different key, the link stops working until the maintainer checks it with you.

Unlinking, on the account page (after a sign-in in the last 10 minutes) or in the launcher, deletes the link at once.

What is not stored: passwords (there are none), your email address, your Discord or GitHub username, avatar or token, and your IP address with your account.

Who can see it: an account is not public. No page shows your account id, sign-in method, Discord or GitHub id, anything about your email address, which game is linked to which account, or what you liked. Your account page shows your own account, and the gamertag of your linked game, only to you. The website never shows your XUID. The launcher of the linked game sees the account's id, sign-in method and creation day.

Deleting:
- On the account page, "Delete my account" deletes the account, its sign-in methods, sessions, activity, game link and your game's likes at once (it asks you to have signed in within the last 10 minutes). Your photos and designs belong to your game and stay; delete them on the account page first if you want them gone.
- Accounts unused for 30 days that are not linked to a game are deleted automatically, and so is an email account whose first code was never used, after a day.
- The server's backups of the accounts database are kept for 7 days, so a deleted account is gone from them a week later.

### Likes, and your own photos and designs

With a linked account you can like community photos and designs on the website (one like per photo or design, never your own), and see and delete your own on the account page.

- A like is stored with your game's XUID (not the website account: a like stays one like if you move the link to another account), the photo or design, and the time.
- Only the total for each photo and design is public, on the website and in the accounts service's public totals. Who liked what is never shown, to anyone.
- Your likes count in the public totals once your game first signed in to the public server with its key at least 72 hours ago, and has signed in on at least 2 different days. Until then they are stored and not counted. They are not counted while the link is unlinked or on hold, or while your XUID is banned.
- To show your photos and designs, and which can be liked, the accounts service reads, without changing anything, the public game server's records of the community photos and shared designs and who owns them.
- On the account page you see your game's photos and designs in every state (on the website, waiting for the automatic check, held, hidden) with their likes, and can delete them (after a sign-in in the last 10 minutes). The accounts service asks the game server to delete the photo or design, which checks again that it is yours. A deleted photo can be uploaded again; a deleted design leaves the game's storefront too. Its likes are deleted with it, and it leaves the website at the next update of the pages (within about an hour).
- A like can be taken back with the same button. Deleting the account deletes your game's likes. Unlinking keeps them stored but not counted, until your game is linked again.

## What is public

- The server's status page (http://forza-reimagined.playit.plus:56678/ and /status), for everyone on the internet. For every player online at that moment it shows the gamertag, XUID, room, time online, data relayed, presence status and session, and matchmaking sessions. It also shows the open Horizon Worlds with the host's gamertag and the gamertag and XUID of whoever asked for an event, the head-to-head challenges with both players' gamertags, and the in-game position, heading and speed of each player who sends one in Horizon World free roam. It shows no IP addresses.
- Leaderboards (/leaderboards on the same address, and the project website https://forza.dyotson.dev, updated daily): gamertag, score, car, class, date, and a player ID made from your XUID and a secret. A score stays published for as long as it is on the server.
- Treat these as public too, since the game shows them to players through the server:
  - your gamertag, XUID, presence and car (to players in your room);
  - your public and LAN IP addresses and ports (to players in your room; see "Your IP address and other players");
  - your scores and ghost replays;
  - your shared photos, with your gamertag and XUID;
  - your shared paint jobs and vinyl groups: preview, title, description, car, share code, downloads, rating and your gamertag;
- Shared designs on the project website's designs page (https://forza.dyotson.dev/designs.html): the preview, title, description, share code, car, downloads and rating, your gamertag and player ID, and the design's like total, once the design has passed the automatic check. The design itself and your XUID are not published.
- Player pages on the project website (forza.dyotson.dev/profile.html?p=<player ID>): your gamertag, your community photos and shared designs that are on the website, and their like totals. Only what the photo and design pages already show; nothing about a website account.
- Community photos (Big Shots you upload): the 1920x1080 copy, your gamertag, your player ID (the one the leaderboards publish, made from your XUID and a secret) and the upload time, in the server's public gallery feed and on the project website's photo pages, with the photo's like total on the website, for as long as the photo is kept.
  - in the car club invite list, the gamertag, XUID, career level and online XP of every player who has signed in to the server and is not in a club.
- The published shader list, which contains no player IDs.
- The daily and weekly challenges (/challenges on the same address): the objectives and how many players finished each, with no names or player IDs.
- Your Discord activity, for your Discord contacts.
- Anything you post in a GitHub issue.
- Nothing about website accounts is public, and neither is who liked what.

## How to turn each thing off

- Everything in "In short" at once: untick "Online features" in the Windows installer, or answer the launcher's first-run question with it unticked.
- All contact with game servers: Settings > Online > Server > "Offline". ("This PC" keeps everything on your own PC.)
- Room list, presence, friends, chat, stats and title services: Settings > Online > "Sign in to Xbox LIVE" off. You stay visible to the server and your room; see "Online play".
- Position sharing in free roam: Settings > Online > "Head-to-head challenges".
- Invites from others: Settings > Online > "Show game invites".
- Shader-list sharing: Settings > Online > "Share shader list".
- Co-op campaign results: Settings > Online > "Co-op campaign results".
- The Play page's daily and weekly challenges: Settings > Online > "Daily and weekly challenges".
- Update check: About > "Check for updates when the launcher opens".
- Discord activity: Settings > Online > "Share activity on Discord". Only the car, event and area in it: Settings > Online > "Share car, event and area".
- Your computer's login name as gamertag: set Settings > Profile > Gamertag.
- The DLSS download: Settings > Display > "Graphics API" set to Direct3D 12, with "NVIDIA DLSS" and "NVIDIA DLAA" off.
- Website accounts: don't make one. To end one: "Sign out" or "Sign out everywhere" on the account page, or "Delete my account" there.
- The website account link: don't link. To end it: "Unlink" in Settings > Online > Website account, or on the account page.
- Likes: don't like, or take a like back with the same button. Deleting the website account deletes your game's likes.
- Your community photos and shared designs on the website: delete them on the account page (with an account linked to your game), or remove a design in the game.

## Contact

You can delete your website account, and your community photos and shared designs, yourself on the account page (forza.dyotson.dev/account.html). For questions or corrections, or to ask for your records on the public server to be removed, open an issue at https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues. Issues are public. Include your gamertag, but never post your friends key, your signing key or the contents of live_identity.txt.
