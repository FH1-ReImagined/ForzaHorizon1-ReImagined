# Launcher, installers and landing page

Issue #1. State: Linux path built and tested end to end; Windows path built and run once in a VM (see "Windows"), **on real
hardware**; `release.yml` ran green by hand (run 38572896631: SDK for Linux and Windows,
both launchers, Windows installer; `publish` only runs on tags and has not run).

## Understanding launcher errors

Setup and launch failures name the operation that failed and show the original
error, a next step and the launcher log path. Builds also name the last reported
step, such as preparing the SDK. Missing files, access failures, a full disk,
insufficient memory, download timeouts and HTTP failures have distinct guidance.
If the cause is unknown, the message asks for the log and reproduction steps
instead of assuming another rebuild will fix it.

Failed build commands retain their exit code, working directory, initial error
lines and a bounded output tail in the error details. The full command output
remains in the build log. Download integrity failures name the archive and both
SHA-256 values, and state which invalid partial file was removed; unrelated
cached downloads remain. Certificate failures retain verification and describe
clock/trust/proxy checks without asserting an unproven security-software cause.

After fixing the reported problem, Resume build retries the build. Cancellation
is shown separately from failure. These messages do not remove or re-import the
player's original game files. See [car tuning](car-tuning.md) for the persisted
diagnostics used when a game build succeeds but tuning preparation fails.

## The idea

The **Designs & vinyls** page browses local `LivCatalog` / `LivGroups` save packages
and extracted folders. It shows saved previews and creator details, exports archival
copies, and imports selected FH1 package designs into an existing local profile after
backing up its catalog. Apply the imported design to a matching car in-game. See
[UGC preservation](ugc-preservation.md) for the workflow and recovery findings.

**Website account (website #4):** Online > Online > Website account, shown with the public
server only, links the game to an optional account on forza.dyotson.dev (Discord, GitHub or an
emailed code), the second factor the website's player features need. **Link to website account**
signs `POST https://forza.dyotson.dev/api/game/link/start` with the game's key (`live_auth.py`,
the `signing_seed` of `live_identity.txt`; the address is built in, never the server setting, and
no friends key or key made from it is sent), opens the answer's `account.html#link=<token>` in the
browser (`QDesktopServices.openUrl`, the OpenURI portal on Flatpak) and shows an 8-character code
for another device (`forza.dyotson.dev/account.html`). It polls the request every 3 seconds in a
`ui/workers.Task` thread. Once the browser's account claims it, the launcher asks "Link this game
to website account K7Q2-XM4P (Discord, created 2 Nov 2026)?": **Link** confirms that account by
its id, **Not me** (or closing the dialog) declines it. After a link the card shows the account
and offers **Unlink** and **Sign out everywhere** (every browser of the linked account), which
work even if someone else took over the web account. The launcher remembers that it made a link
(`web_account.linked` in `launcher.json`) and only then asks the service for the link's state,
once a session when the card is shown. The calls are in `web_account.py` (HTTPS through
`fetch.urlopen`, the operating system's certificates on Windows), the card and the dialog in
`ui/web_account.py`; `test_web_account.py` runs them against a fake service that checks every
signature. The service is forza-live's `accounts/` (its README has the API and the rules).

Discord status sharing is available under **Online > Online > Discord** (off by
default). See [Discord Rich Presence](discord.md) for the one-time public application
ID setup, supported activities, privacy and local IPC tests.

**HDR output (#33):** Settings > Graphics > HDR provides the Vulkan scRGB toggle,
paper white, display peak, highlight strength, source gamma, comparison and
calibration controls. It defaults off and falls back to SDR on unsupported
surfaces. See [HDR setup, Windows online steps and validation](hdr.md).

**Texture packs (#384):** Settings > Graphics > Texture packs has **Dump textures**
(`texture_dump`), **Use texture packs** (`texture_replace`, Vulkan only), **Open textures
folder** (`textures` in the data folder, passed to the game as `texture_pack_root` while
either option is on) and **Texture packs page…** (below). Both default off. See
[texture packs](texture_packs.md).

No prebuilt game is ever shipped (the generated C++ is translated game code). The release
holds only the **launcher**, the project's own sources, the Forza Live server and pieces of
ReIGlue, the project's fork of the ReXGlue SDK (SDK code, BSD-3-Clause). The launcher builds the game on the player's
machine from their own copy:

1. **Setup > Game files**: the player's Games on Demand package (`4D5309C9/00007000/<name>`
   plus `<name>.data/`), a disc 1 ISO (`tools/xiso_extract.py`, docs/disc.md), a bare
   `default.xex`, or an already extracted folder. A package is extracted with
   `tools/xcontent_extract.py`; `default.xex` is extracted first and checked before the long copy
   (`installer.inspect_xex`, below): title `4D5309C9`, entry point `0x82A6FBC0`, version equal to its base
   version (not patched). The supported printing (0.0.0.7, media id `2B7A1346`) is accepted on its header;
   any other printing is accepted only when its code is the same (image fingerprint). A title update
   already applied, a patch file, another game or other code is refused with the reason.
2. **Title Update 4** (recommended: Horizon Rally and the 1000 Club need it; still optional)
   and **DLC** (optional): pickers for the player's own TU4 package (the STFS package under
   `4D5309C9/000B0000`, or a zip holding it) and DLC folder. The DLC folder is listed with the
   packages' names and installed with `tools/install_dlc.py` (to the game's user data folder). It takes
   a folder of STFS package files, or DLC that is already extracted in Xenia's layout (a Xenia
   `content` folder, its `0000000000000000`, `4D5309C9` or `00000002` folder; package folders named
   like the package files), or both in one folder. Each package folder is copied as it is, with its
   `../Headers/00000002/<name>.header` when there is one (license mask set to all bits), else a
   header is generated (display name from a table of the 23 known package names, else "DLC <name>").
   Folders that do not look like DLC (a name that is not 8 to 42 hex digits, no files inside) are
   skipped and listed in the card.
   The scan is recursive (#410): up to 8 folders deep, links are not followed, hidden and system
   folders are left out, and files that are not packages are ignored quietly. `.zip` archives are
   accepted too (`launcher/forza_launcher/dlcscan.py`): only the entries that look like STFS
   packages are unpacked, one level of zip inside a zip, to a `tmp/dlc-*` folder under the data
   folder, then installed as usual and removed afterwards (also on failure or cancel). A zip with
   an absolute or `..` path or a link is refused whole; a damaged one is skipped; the total is
   capped at 8 GB and the free disk space is checked first (room for two copies). The card lists
   what was found and what was skipped with the reason (duplicate package names install once, a
   package that is not DLC, a zip with no DLC, ...). `launcher/test_dlc_scan.py` covers this with
   synthetic packages and zips.
   Packages outside the known official package-name table show an amber **Unrecognized DLC**
   warning (#118), naming every package by its display name and filename, including packages
   beyond the list's 12-item preview. Installation remains available without a confirmation
   dialog. The warning explains that unofficial or modified packs can cause endless loading
   and suggests removing the named packages first if that happens. It stays visible after
   installation and clears when a different selection has no unrecognized packages. Both STFS
   files and extracted Xenia folders use the same case-insensitive name check; this does not
   verify the contents or authenticity of a recognized package. Installation also emits the
   warning through its log callback. `launcher/test_dlc_warnings.py` covers recognition,
   synthetic STFS/extracted-folder installs, and the offscreen Setup UI on Windows and Linux.
   With a TU4 package the build runs `tools/apply_title_update.py BASE_XEX TU_PACKAGE
   <data>/build/tu/default.xex` (patched `default.xex`, both facade DLLs and `media.zip`, about
   2 s), copies `forza_manifest_tu4.toml` with its three `file_path`s and `game_root` pointed at
   `build/tu`, configures with `-DFORZA_TITLE_UPDATE=4` (`hints_tu4/`, `generated_tu4/`) and
   stages `build/tu` as `runtime/update`, which Play passes as `--update_data_root`
   (`--game_data_root` stays the disc's files). Without it the base manifest is built and
   `runtime/update` removed. `--build` takes `--title-update PATH` (default: the launcher's
   setting) and `--base`.
3. **Build**: preflight (RAM for the chosen job count, disk, cores), toolchain, SDK, codegen from `default.xex`,
   CMake configure, ninja (progress parsed from `[n/m]`, log view, cancel, resume), then the
   result is staged into `<data>/runtime`. The generated code is compiled for x86-64-v3 (AVX2, FMA,
   BMI2, MOVBE) when this PC's CPU has all of it, else for any x86-64 CPU (`-DFORZA_CPU_TARGET`,
   [CPU target](perf.md#cpu-target-x86-64-v3-220-2026-10-21)). The build is repeated only when the launcher or
   project version changes, the title update is added, removed or replaced, or the CPU target changes (a hash of
   `forza-recomp/` sources, the launcher version, the SDK pin, the TU4 package and the CPU target). "Free disk space" deletes the build tree (about 1 GB); the game keeps working.
4. **Prebuild shaders** (optional, Vulkan, #226): rebuilds the shaders of the shipped pipeline list
   (`forza_launcher/data/pipelines-4D5309C9.json`: hashes and decoded render state, no game data) from
   the player's `media/shaders`, track `bin.zip`s, the update's `media.zip` and the executable image,
   writes them with the list to `<cache root>/shaders/prebuild/`, and starts the game with
   `--shader_prebuild_only`: it builds every listed and stored pipeline into the driver's cache, with
   the usual `SHADER_WARMUP` progress, and exits before the game starts. Cancel stops it. The card says
   "Recommended" again after a graphics driver change (the driver cache files of the last prebuild are
   gone), a launcher, list or GPU plugin update, or a graphics setting that selects other pipeline
   stores (split scene/HUD rendering, output size, render scale, render target path, API). Every later
   start also warms the listed pipelines. The step refuses to start the game while the Build step says "Rebuild needed" or "Not built" (a game built by an older launcher does not know `--shader_prebuild_only`): it shows "Rebuild first" and asks for Setup > Build. Before it builds, the step also fetches the server's list (below) and merges it with the shipped one; offline or on any failure it uses the shipped list alone. [Shader prebuild](shader_prebuild.md).
   **Share shader list (#276, Online > Online, on by default):** after a game session the launcher uploads the pipeline descriptions of your own pipeline cache that it has not sent before to the server you play on: shader hashes and render settings, plus how to rebuild each shader from game files (no shader code, no game data, nothing personal). The server publishes entries that at least 3 different players reported. Only the public server and your own server are used, never offline or "This PC"; the upload runs in the background with short timeouts, failures only go to the launcher log, and the launcher keeps a record of what was sent (`shader_share.json` in the data folder). The setting is launcher-only and is not written to `forza.toml`.
   **Co-op campaign results (#309):** after a game session the launcher asks the server you play on (the public one or your own, never offline or "This PC") for your results of the Co-op Festival events you raced, as the world's host or as a guest (a guest's place counts when the host's game saw the same place), and applies each to the save of the session it was earned in (the launcher keeps which save each session used; another save's results wait for that save's next session, and a game started outside the launcher gets none), when that save has the event unlocked and the result beats its place for the event: the event's and race's result, the wristband XP of the improvement, the event's prize at that place less the online winnings that race already paid (you get the larger of the two once; no prize when that amount is not known, for example after races in another save), and the event's own unlock rewards, with a backup ("Before co-op results") and the save editor's checks. The wristband level and the unlocks by XP are left to the game (Race Central and your next race). The Play page lists what was added and each result that waits for an event your career has not unlocked yet (a guest behind the host's wristband): it is added at the first session after you unlock the event. Results it settled are confirmed to the server; results of a game it did not start can never go to a save and are dropped with a note; the others, and failures, wait on the server (30 days, at most 20 a player; results earned as a guest go first, then the oldest) for a later session, and failures only go to the launcher log (`coop_grant.py`, `docs/coop_campaign.md`).
   **Daily and weekly challenges (forza-live #1, Online > Online, on by default):** the Play page's Challenges card shows the main Forza Live server's three objectives of the day (00:00 to 00:00 UTC) and three of the week (from Monday 00:00 UTC), the same for every player, with your progress and the time left. The server counts progress from the results your game sends it while you play signed in: races won, on the podium or finished (in a class, or against other players), speed traps and zones at a speed (different ones count), 1000 Club medals and Big Shots (server `forza_live/competition/objectives.py`, `docs/website-data.md` there). The main server may also send a weekly credits gift through the game's Message Center for finished challenges (server `--daily-challenges-gifts`); the card then says how many of the week's challenges you finished toward it, or that this week's gift was sent. A community server may run challenges of its own, but the card shows only the main server's. The card asks `GET /challenges/progress` of the main server, signed like the co-op results, two seconds after it shows, then at most every 5 minutes while the launcher is open, and after each game session; only with Server set to Public server and a Forza Live identity on this PC (the game makes it at its first online start). With "My own server" it says challenges count on the main server only, with "This PC" and "Offline" it is hidden, and with the setting off (the "Online features" choice turns it off) it sends nothing and says how to turn it on (`challenges.py`, `ui/challenges_card.py`).
5. **Play**, **Settings**, log folders, versions. On Play, **Open log folder** opens the game data folder containing `forza.log` (the current or last session; each start moves the one before to `forza.previous.log`, #609); **Open launcher log folder** opens the separate configuration folder containing `launcher.log` (launcher errors and tracebacks). Start failures also show the full launcher log path.

Data lives in the game's user folder (`~/.local/share/forza`; on Windows `%LOCALAPPDATA%\ForzaReImagined`, or
`Documents\forza` for an install that already lives there; changeable under Setup > "Where the game is built", see "Round 5"): `game/` (extracted files), `toolchain/`, `downloads/`,
`sdk/`, `build/`, `runtime/` (what Play runs, with `forza.toml`), saves, `forza.log`.
The launcher's own config is `~/.config/forza-reimagined/launcher.json`
(`%APPDATA%\ForzaReImagined\launcher.json`).

## Settings search

Use the search field above the Settings categories, or press **Ctrl+F** while in
Settings. Search matches setting names, help text, option names and configuration
keys across every category, including the launcher's profile and advanced options, the
Online settings (shown as **Online** in the results; they open Online > Online) and the Servers
list and address field (they open Online > Servers). Select a result or press Enter to open its
category and focus the existing control; a folded card (Racing wheel, Shifter and clutch, Keyboard bindings) or a card's
**More options** that holds the result opens first. Results overlay the tabs without
shifting the page. Down moves into the results; Escape clears the search, and clicking
outside dismisses the dropdown.

Searching does not change configuration. Disabled settings remain disabled with
their existing explanations, and unsupported graphics options stay hidden. Search
indexes descriptions, not personal field values such as paths or server addresses.

## Crash reports

After an unexpected game exit, **Play > Review crash report…** shows an editable report
with the fault, host/guest call chain when logged, launcher/game versions, OS, GPU,
launch-time graphics settings and the last 200 log lines (at most 64 KiB). Startup GPU
and driver details are also collected from the first 32 KiB. Collection runs in the
background; Play waits until collection finishes so another launch cannot replace
the log being read. Navigation remains available. Normal closes, launcher Stop and
successful game relaunches do not offer a crash report.
Before each start (Play and the shader prebuild) the launcher moves `forza.log` to
`forza.previous.log` and its 50 MB roll `forza.1.log` to `forza.previous.1.log`, replacing the
older copies (#609), so the report reads this session only; it reads the start of the session
from `forza.1.log` when the session rolled over. On Windows a `forza.log` that an older game
still has open stays, `launcher.log` notes it, and the new session is added to its end. If
startup fails before writing a new log, the previous run's log is excluded from both crash
classification and the report. The report ends with which files to attach.

Known folders, profile IDs, custom server addresses and common secret fields are
removed on a best-effort basis. Arbitrary environment variables, command lines,
profile data and game files are excluded. Review the text for anything else personal
and add reproduction steps. **Copy report** and **Save report…** work without a browser.
The report stays in memory until the next successful launch or launcher exit; saving
is explicit. The original log is unchanged.

**Open GitHub issue** opens a prefilled draft on this project's Issues page. It does
not submit an issue. If the reviewed report exceeds the browser link limit, the full
text is copied and the launcher asks you to paste it into the draft description.
Nothing is uploaded automatically. `test_crash_report.py`, `test_game_exit.py` and
`test_launcher_ui.py` cover bounded reads, missing logs, redaction, report actions,
unexpected/normal exits, and background collection with a disposable child process.
Secret redaction handles JSON-style quoted keys, quoted values containing spaces
or escaped quotes, and common authorization/API-key/token spellings. Reports
remain editable for review before the player chooses to copy, save or open a draft.

## Photo gallery

Open **Library > Photos** in the launcher sidebar to browse locally saved photo-mode
pictures. The page uses the data folder and profile selected under Settings,
including the launcher's local copy of an imported Xenia profile. It does not
copy or modify the original Xenia save. A profile without photos shows instructions
for taking and saving the first picture.

- **Refresh** discovers newly saved pictures; reopening the page also refreshes it.
- The grid shows pictures without captions and fits the window width. Sort by newest
  or oldest; ordering uses the image file's modification time, which can change when
  saves are copied or restored.
- Click a tile, select **View photo**, or press Enter to enlarge the original image.
  The grid keeps thumbnails up to 800 pixels wide (larger at display scaling above
  200%) so tiles stay sharp as the window resizes; the viewer loads the source at full
  resolution. Click the enlarged picture or select **100%** to inspect its detail,
  with one image pixel per display pixel even on a scaled display. Scroll to explore,
  then click again or select **Fit to window** to see the whole picture. Ctrl+1 selects
  100%; Ctrl+0 fits to the window. Small source images are not enlarged artificially.
  Previous/Next and the arrow keys navigate the gallery. Source dimensions and the
  file's modification date are shown in the preview.
- **Export photo** saves a separate PNG or JPEG. PNG avoids another lossy encoding;
  JPEG is saved at quality 95. A date-based filename is suggested automatically.
  Exports cannot overwrite files inside the save folder.
- **Open save folder** opens the current profile's saved-game directory.

This is a local gallery; it does not download the online photo storefront or upload
anything. It reads `photo/` or `photos/` content directories under the active
`B13EBABEBABEBABE/4D5309C9/00000001` save. Standard images, extensionless images,
and JPEGs embedded after a file header are decoded by Qt. Where a named photo
folder contains both a preview and a larger image, the larger image is used.
Encrypted or otherwise unsupported files are reported as unreadable. The gallery
cannot repair pictures already saved with corrupted pixels by older game builds.

Discovery, image decoding and export run in background threads. Source files are
read only; exports use an atomic replacement after successful encoding. The decoder
limits files to 64 MB and decoded images to 40 million pixels. Tests use disposable
profiles and generated images; a new in-game photo save still needs an end-to-end
check (the local player profile had no photos, and the isolated game run ended
before saving one).

## Launcher interface

The sidebar has seven sections and **About** at its foot. A section of several pages shows
them as tabs under its title; the section button returns to the page shown last.

| Section | Pages (tabs) | What is there |
| --- | --- | --- |
| **Play** | | The banner, one big **Play** (with **Play in VR** when a VR app is found, and **Stop**), the game's messages, and four status rows: Game files, Game, DLC, Online. **Open log folder**, **Open launcher log folder**, Challenges and **Installation details** follow. |
| **Library** | Photos, Designs & vinyls, Saves | What the game saved: photo-mode pictures, designs and vinyl groups, save backups and the profile editor. |
| **Mods** | Mods, Texture packs, Radio | What changes the game's files or adds to them: mods and the FM4 cars, texture packs (HD pack, decal pack, compression), the custom radio stations. |
| **Online** | Online, Servers | **Online**: the server Play uses (changed in Servers) and every online option: Website account, Forza Live (Sign in to Xbox LIVE, room, invites, friends and chat keys), Horizon World, Shader prebuild, Co-op campaign, Challenges, Discord. **Servers**: the server list with players and ping, This PC, a server added by address, and Offline. |
| **Controls** | | The controller guide, bindings and calibration, DualSense and Xbox trigger effects. **Control settings** opens Settings > Controls. |
| **Settings** | Graphics, Display, Controls, Gameplay, Audio, VR, Profile, Advanced | Every other forza.toml key and the launcher's own options, with search (which finds the Online ones too and opens Online). |
| **Setup** | Game files, Updates & DLC, Build | Building the game, step by step. |
| **About** | | The notices, launcher updates, Add to Steam (Linux) and the project links. |

The Online settings were a Settings tab; they are now the first tab of the **Online** section, next
to Servers. Their keys and schema group ("Online") did not change: the Settings page still builds
them (`HOSTED` in `ui/settings_page.py`, shown by the window as page 12, `ONLINE_PAGE_ID`), saves
them and finds them in search, where a result opens Online > Online. The old Server card with four
radio buttons (Public server, This PC, My own server, Offline) repeated the Servers page: Servers now
has an **Offline** row as well, and Online > Online shows the server Play uses with a link to
Servers. Search also finds the Servers list and its address field.

Settings categories, and what moved between them: **Graphics** has the graphics card card,
Picture (Graphics API, resolution, upscaling, anti-aliasing), NVIDIA DLSS (Windows), AMD FSR 3,
World detail, HDR and Texture packs, which were the old Display tab's picture sections; **Display** keeps Frame rate,
Window, Camera and Ultrawide and HUD. **Gameplay** also shows the native pause menu (Menu),
which was under Advanced. The forza.toml keys, their values in `launcher.json` and their schema
groups did not change (`ui/settings_page.py`, `TABS` and `TAB_SECTIONS`, maps sections to
categories; a section no category names stays in its group's category). Racing wheel, Shifter
and clutch and Keyboard bindings start folded (**Show**) unless they are in use (Racing wheel
On, a shifter chosen, keyboard and mouse on). Settings few players change, such as the guest
refresh rate, the input backend, a wheel's raw axis numbers or the event lobby timings, sit
under **More options** in their card (`MORE_OPTIONS`).

Play keeps the launch action above the readiness checks. **Installation details**
expands to show game, save and executable paths; normal status rows stay short.
Setup's **Build log** is collapsed initially, continues recording while hidden,
and opens automatically on a build failure. Cancelling a build does not force it open.

Each tab switches to a separate page with its own scroll position; tabs are not scroll
shortcuts. Related settings are grouped in padded cards, with space between descriptions and
controls. File-location controls in Advanced and Profile sit below their descriptions. Setup's
**Build options** expands to show the parallel-job count and low-priority option. Starting a
setup task or reporting its failure reveals the corresponding tab. Setup checks compiler and
build-tool availability in the background, so opening it after browsing the gallery does not
block navigation while version probes run.

Graphics settings explain when DLSS/DLAA, FSR 3, ultrawide or HUD customization
controls an unavailable option. Buttons, tabs, expandable sections and switches have
visible keyboard focus. The Controls introduction reflects the existing keyboard
and mouse support.

**Look (`ui/theme.py`).** The launcher uses the website's look (forza.dyotson.dev,
`style.css`): an umber window (`#1c140f`, cards `#2a1f17`), sand and cream text
(`#f4ebdd`, `#f3dfbf`, help text `#c4ae92`), dusty orange (`#e8964f`) and rust (`#d0703f`)
accents, and the website's orange-to-rust gradient on Play. Headings use Bitter ExtraBold and
the navigation, tabs and buttons Saira Semi Condensed; body text stays on the system's UI font.
Both fonts are under the SIL Open Font License and ship in `launcher/forza_launcher/ui/fonts/`
with their licence texts (from github.com/google/fonts; Bitter is the weight-800 instance of its
variable font). Every text color passes WCAG AA (4.5:1) on the surface it is drawn on; input,
switch and check-box edges keep 3:1. The sidebar shows the logo mark (`docs/images/logo-mark.png`,
the website's); the window icon is `docs/images/logo.png` (Linux) or `forza.ico` (Windows).

Windows uses the ReImagined logo mark for the launcher, installer and game
executable. The shared `forza-recomp/resources/forza.ico` includes nine sizes from
16 to 256 pixels. PyInstaller embeds it in the launcher; CMake compiles a Windows
resource into both base-game and TU4 builds. Existing game executables receive
the icon on their next rebuild. See `forza-recomp/resources/README.md` to regenerate
the icon from the project artwork.
## Gamepad navigation

The SDL controller backend leaves process signal handling to the host. On Linux,
SIGTERM terminates the launcher normally instead of becoming a discarded SDL quit
event. A subprocess regression test checks this; the CI window smoke test also has
a forced-kill deadline and a five-minute step limit.

The launcher accepts SDL-mapped controllers on Windows and Linux, including the Steam
Deck's gamepad mode. Connect a controller, release its buttons/stick, and use:

| Control (Xbox / PlayStation) | Action |
| --- | --- |
| D-pad or left stick up/down | Previous/next control; scrolls it into view |
| Left/right | Change a list, dropdown, number or slider; otherwise move focus |
| A / cross | Activate the focused button/toggle, or open a dropdown |
| B / circle | Close a dropdown/dialog; otherwise return to Play |
| LB/RB / L1/R1 | Previous/next sidebar section (wraps) |

Play starts focused. An orange outline marks the focused control, skipping page containers
and descriptive labels. The focused action button carries an A badge; sidebar section buttons
stay text-only. **Settings > Controls** shows launcher button hints and connected,
paused, unavailable or disconnected status. When the separate controller-guide
feature is installed, the same card appears on its **Controls** page alongside
the diagrams and haptics. Settings resets preserve the status card.
Shoulders follow the sidebar's visible section order (each section opens on its first page).
Left/right switches a section's page tabs (Library, Mods) and the Settings and Setup category
tabs; search results support up/down, A to select and B to dismiss.
Dropdowns use up/down and A to select, B to close.
File pickers use Qt dialogs so controller focus works there too. Text entry still needs a
keyboard or the system's on-screen keyboard. Steam Input users should select a gamepad
layout; a mouse/keyboard layout continues to work through normal Qt input.

Input is ignored while the launcher is inactive or the game is running, and from a device
that the Controls page is binding or calibrating. Reconnects, returning to the launcher and
the end of a Bind require neutral input before another action. Directions repeat
after 400 ms, then every 120 ms; action buttons never repeat. The left stick has a roughly
49% dead zone. One controller is used at a time; unplugging it allows another to take over.
Unsupported/unmapped devices do not prevent mouse/keyboard use. Initialization or read
errors appear in Controls and in the launcher log.

PySDL2 and `pysdl2-dll` supply the controller mappings/native runtime, with no SDL video or
audio initialization. Both standalone bundles and the Flatpak include these dependencies.
The Flatpak already grants controller device access. `launcher/test_gamepad.py` covers
state/repeat logic, real SDL virtual-device mapping and hotplug, Qt focus and settings,
modal dialogs, game launch signals and input suspension. Run it on either OS with
`python -m unittest discover -s launcher -p 'test_gamepad.py' -v`; Qt tests use the offscreen
platform. Release CI also runs `launcher/packaging/gamepad_smoke.py` through each frozen
launcher to verify its bundled SDL library. These automated virtual-device checks do not
replace a physical-controller/Steam Deck acceptance check.

## Controller guide

**Controls** in the sidebar opens a controller reference with Xbox, DualSense,
and DualShock 4 artwork. Select a button, or Tab to it and press Space, to see its
driving action and the matching Xbox prompt. The **All driving controls** section
provides the same reference as text. The guide describes the default layout;
the in-game control layout and transmission settings can change some actions.

The controller selector changes the guide, not the game's bindings or prompts
(**Bindings**, below, changes the bindings).
**Control settings** opens Settings > Controls for the existing keyboard, mouse,
player, and racing-wheel options.

Artwork comes from [e7d/gamepad-viewer](https://github.com/e7d/gamepad-viewer),
with its MIT license and source revision included in
`launcher/forza_launcher/ui/controller_art/`. The images and button sprites are
bundled locally, so the guide works offline.

The **DualSense adaptive triggers** card saves throttle strength, brake
strength and overall intensity. Effects default off. The launcher starts the
loopback telemetry service only when either haptics option is enabled at game
launch. If both were off, enabling an effect during play requires a game restart;
Controls shows a restart hint. Once telemetry is running, effects can be changed
during play. It requires a rebuilt game with the FH1 telemetry bridge and a DualSense
or DualSense Edge. It assumes the default layout (LT brake, RT throttle). The
effects follow the car, not the trigger: the brake stiffens with the deceleration and
pulses with ABS or a locked wheel, the throttle buzzes on wheelspin and gets a light
wall at the rev limiter, and both shake on rough ground and in a crash. Status shows whether the
controller and driving telemetry are ready. See [telemetry.md](telemetry.md) for
the protocol, hardware limitations and validation.

**Xbox trigger vibration** adds separate LT brake and RT throttle vibration for
Xbox One/Series controllers. Enable it and adjust overall intensity or either
trigger independently. Normal body rumble continues through the game. Status
reports whether the current controller/driver exposes impulse triggers, or
whether the launcher and game need an update/rebuild. Xbox 360 controllers keep
their normal body rumble. The default game control layout is required for the
LT/RT effect mapping.

## Bindings and wheel calibration (#279)

The **Bindings** card on the Controls page binds a device's inputs by pressing them, as sim racing games do.
Pick a connected controller or wheel (a wheel is what the game's wheel driver takes for one: SDL's wheel type
or a wheel-like name, or any unmapped joystick with Racing wheel set to On). The live view shows a wheel's raw
axes (bars) and buttons (numbered lamps) and a controller's mapped sticks, triggers and buttons by name. Each
game action has a row with its current input, **Bind**, **Clear** and **Reset to default**; inputs bound twice
are listed. Bind takes the first input that changes from where it was when Bind was pressed (an input already
held counts only after it is released), gives up after 10 seconds, and Esc cancels it.

- **Wheels** read and write the wheel driver's own settings in their formats: `wheel_axes`, `wheel_buttons`,
  `wheel_shifter_buttons`, and the invert switches (from the direction the steering was turned and where a pedal
  rests). The rows show the driver's defaults, ported in `launcher/forza_launcher/bindings.py` (known Logitech
  wheels by USB id, the generic button map, the Driving Force Shifter). **Calibrate wheel** measures the
  steering axis and its direction (lock to lock), the rotation (a quarter turn), each pedal's axis, travel and
  invert switch, and sets the steering deadzone with a live preview computed like the driver
  (`steering_value`); details in [playing.md](playing.md#racing-wheels-issue-4). Pedals and a shifter on
  their own USB devices (#272) bind on the wheel's pedal and gear rows: while such a row waits, the editor
  watches every other connected device that is no game controller too (`bindings.merge`, `split`), and a press
  there writes `wheel_pedal_device` / `wheel_pedal_axes` or `wheel_shifter_device` /
  `wheel_shifter_device_buttons` (`set_aux_binding`). The device settings hold the device's USB ids
  (`device_ref`: `0483:0525`), not its name, since the game's SDL 3 can read another name than the launcher's
  SDL 2 for a Windows device (a Thrustmaster has none there, #562); opening Controls rewrites a device saved by
  its name (`migrate_device_settings`). The picker shows them as *pedals* or *shifter, read with
  the wheel* (`pick_aux`, the driver's choice ported with its table of known devices). On Linux, devices SDL 2
  leaves out (no X and Y axis) are read from evdev (`evdev_joysticks.py`).
- **Controllers** write `controller_remap` (SDK patch 0116), a profile per controller model by SDL GUID;
  format and supported pairs in [playing.md](playing.md#remapping-a-controller-279).
  Resetting all bindings, or the last custom binding, keeps an empty profile for the selected GUID when
  needed to block another saved profile for the same model or `*`. It restores the default inputs without
  deleting the other saved profiles. The SDK already treats an exact empty profile as the default mapping.

The editor reads SDL joysticks itself (`gamepad.Joysticks`, the same SDL as the navigation). While a Bind or a
calibration runs, gamepad navigation ignores that device and afterwards waits for it to be neutral, so the
presses that bind never move the launcher's focus. Changes go through the Settings page, which shows them,
saves and rewrites `forza.toml`. `launcher/test_bindings.py` covers the strings, defaults, conflicts,
detection and calibration math, and drives the editor with SDL virtual joysticks (a wheel and a gamepad);
`launcher/test_wheel_devices.py` the pedal and shifter devices (a wheel, pedals and a shifter).

## Verify your download

Each release carries a `SHA256SUMS` file with the SHA-256 checksum of every download (the Windows
installer, the Linux archive, the Flatpak bundle and the Windows file list). To check that your
download is the file the release published:

1. Download `SHA256SUMS` from the same release page as the installer.
2. Hash your download. On Windows, in a Command Prompt or PowerShell in the folder of the file:

   ```
   certutil -hashfile ForzaHorizon1-ReImagined-Setup-<version>.exe SHA256
   ```

   or, in PowerShell:

   ```
   Get-FileHash .\ForzaHorizon1-ReImagined-Setup-<version>.exe -Algorithm SHA256
   ```

3. Find the line for that file name in `SHA256SUMS` (any text editor) and compare the hashes. They must
   match letter for letter; capitals do not matter, and spaces that `certutil` may put between byte
   pairs are not part of the hash.

   On Linux: put both files in one folder and run `sha256sum --ignore-missing -c SHA256SUMS`.

A match shows your copy is complete and is the file GitHub Actions built and published from this
repository. It is not a code signature and not a malware scan. The installer and `forza-launcher.exe`
are not code-signed yet (SmartScreen may warn), and Windows Defender sometimes flags the launcher as
`Trojan:Win32/Bearfoos.A!ml` or `Trojan:Script/Wacatac.C!ml`, which are machine-learning guesses
(#482, #354, #409). If that happens after the hash matches, open **Windows Security > Virus & threat
protection > Protection history**, choose the detection and **Actions > Allow on device**. The installed
files are listed with their hashes in `windows-build-manifest.json` in the install folder.

## Launcher updates

**About > Launcher updates** checks the project's latest stable GitHub release and offers
the matching x86-64 Windows installer, Linux archive or Flatpak bundle. Packaged launchers
check once in the background each time they open; turn this off with **Check for updates
when the launcher opens**. An available update adds an **Update available** link in the
sidebar. Offline or rate-limited checks leave Play and Setup usable; retry under About.
Checking does not start a download. On packaged Linux launchers, **Update and restart**
downloads, verifies, installs and restarts in one action. Finish any task and close the game first.

Downloads go to `updates/<tag>/` under the launcher's configuration directory. The updater
requires the release asset's GitHub SHA-256 digest and expected size, writes a partial file,
and exposes the completed download only after both match. Failed or cancelled partial
downloads are removed; retry starts fresh. A verified completed download can be reused.
Assets without a digest, a matching platform, or a URL under this repository's release
are not offered; **Releases** remains available for manual installation. Only stable
three-part versions are compared; development versions are reported without recommending
an automatic upgrade or downgrade.

Installation behavior:

* **Windows:** after downloading, choose **Open installer**, close the launcher and follow
  the installer. Packaged launchers pass their current install directory, including custom locations.
* **Linux archive:** **Update and restart** rechecks the downloaded archive's size and digest,
  extracts it safely into a private sibling of the install folder, and checks the new executable's
  version. A detached helper waits for the current launcher to exit, keeps the old folder as
  `.forza-update-<id>/previous` beside the installation, and moves the new folder into the same
  location. It checks the executable again at that location, then restarts. A failed replacement
  or startup check restores and restarts the previous copy. The helper is copied outside the
  installed bundle, so moving that bundle does not remove the helper. It uses a file lock to
  exclude another updater and refuses a changed installation. The previous copy remains as
  a recovery backup; old backups may be removed once the new launcher works. The directory
  containing the launcher must be writable by the player. If the configured game, data or
  configuration folder is inside the launcher folder, move it using Setup before updating.
* **Flatpak:** **Download update** saves a size- and SHA-256-verified bundle under the system's
  Downloads folder, in `ForzaHorizon1-ReImagined/<tag>/`. The card shows the exact quoted
  command to install it for the current user: `flatpak install --user <bundle>`. Close the
  launcher, run that command in a host terminal, then reopen the launcher. **Open download
  folder** opens the folder through the desktop portal; the command remains visible if that
  fails. The manifest grants `xdg-download` write access for these bundles, while home
  remains read-only (removable drives under `/run/media` and `/media` are writable for a game
  and save folder there, #717). It grants no host-command permission, and the launcher never
  starts the Flatpak installer or restarts the sandbox itself. System installations can be
  managed manually through the host's software tools or terminal.

During installation, Play, Setup and Settings are disabled, and closing waits for the
installation to finish. Preparation/installation errors keep the existing window usable.
Archive helper logs go to `update-install.log` in the configuration
folder. On restart, About displays the installation result from `update-result.txt`; this
message is consumed once and is not overwritten by an immediate automatic release check.
An archive helper that cannot observe the old launcher exiting within 60 seconds aborts
without replacing it.

Settings, saves, DLC and the player's game files remain in their existing user folders.

The existing build ID logic marks the game stale when the launcher/project version changes;
use **Setup > Build** to rebuild from your files. Source checkouts expose manual release
checks and can open a downloaded package, but never replace the checkout or run Git commands.

Validation: `python3 -m unittest discover -s launcher -p 'test_*.py'` covers release selection,
version comparison, download integrity, cancellation/retries, real archive replacement and
rollback with fixture launchers, and the detached helper waiting for its parent to exit.
With PySide6 installed, offscreen UI tests cover automatic archive download/install,
restart, install failure recovery, shutdown during a request or installation, and Windows
installer invocation. Flatpak UI tests cover the Downloads destination, shell quoting,
manual instructions, failed integrity checks, and preservation of the instructions when
opening the folder fails. They assert that downloading and opening the folder never invokes
host commands, installs the bundle, or closes the launcher. The suite passes 72 tests with
9 game-fixture tests skipped. Native Windows installation still requires platform testing.

Linux archive platform validation used an isolated Ubuntu 24.04.4 x86_64 VM, X11/Xvfb and
real PyInstaller packages. Test releases used versions 0.1.0 and 0.1.1 with local HTTP release
metadata/downloads and normal integrity checks. The game itself was not included. Tests
verified a custom installation path with spaces, a visible replacement GUI, retained
settings/save data, and restoration of the previous working GUI after a deliberately broken
replacement. The current Flatpak manual-download flow is also exercised with local HTTP
fixtures and rendered Qt UI; no native Flatpak sandbox rerun has been performed since
removing host execution.

### Save manager

**Saves** manages the profile chosen in **Settings > Profile**: the local profile or the
launcher's copy of a Xenia profile. It never restores into the original Xenia content
folder. The displayed path is the managed save folder; an advanced `--user_data_root`
override is honoured too. Backups preserve the game's files without changing their
format. The entire `B13EBABEBABEBABE/4D5309C9/00000001` directory is included,
including photos stored there; DLC, caches and game files are excluded.

* **Create backup** accepts an optional name. Backups live under
  `<launcher data folder>/save-backups/<profile>/`, with a file list and SHA-256 hashes.
* **Back up before Play** is enabled by default. Play waits for the backup to finish;
  failures block launch with a visible error. An unchanged save reuses the latest valid
  backup. Keep between 1 and 50 automatic backups (default 10). Named backups and backups
  made before restores or edits are never automatically removed. Open the backup folder to
  manage those manually.
* **Restore selected** asks for confirmation, validates the full backup, stages it beside
  the current save, then backs up the current save before replacing it. A journal allows
  the launcher to recover an interrupted directory replacement on the next backup,
  restore or Play. Cancelling before replacement leaves the current save in place.

Scans, hashing and copies run in a cancellable background worker. The page blocks writes
while this launcher has a game running; an OS lock also excludes another launcher using
the same data folder. The lock stays held across game relaunches and until shutdown has
finished stopping the game. Games started directly or by older launchers do not take
this lock: close them before managing saves. Backup storage should also be copied to
another drive if protection against drive failure is needed.

Validation uses disposable fixtures only: hash corruption, missing/extra files, path
traversal, retention, cancellation, disk space failures, replacement rollback, journal
recovery, Xenia source isolation, Qt actions, worker shutdown and session locking.
`cd launcher && python -m unittest test_saves test_saves_ui -v` runs these checks;
link rejection is skipped on Windows when symlink creation is unavailable.

**Edit profile…** opens an offline editor for credits (0–999,999,999) and career XP
(0–2,147,483,647), plus a **Wristband** selector: Yellow, Green, Blue, Pink, Orange,
Purple and Gold. The selector uses the career database's IDs 0–6 and edits only
`Main/WristbandLevel`; it leaves XP separate and does not complete races, award cars,
finish the opening tutorial or change online rank. Level is shown for context. Changing
XP only changes the stored total. An unfamiliar saved wristband value is shown as an
unchanged current value and preserved unless a supported tier is selected.
The editor authenticates and decrypts `ForzaProfile/ForzaProfile` using the selected
profile's XUID and key tables read from the player's own `default.xex` in the game-files
folder. It supports the known disc and TU4 layouts, and both a profile the base game wrote and
a TU4 one (TU4 signs its converted profile with another key); no game key bytes are bundled or
written to the launcher config. First open may take a few seconds to decode the XEX.

**Back up and apply** verifies the loaded file has not changed, edits only the allowed
four-byte fields, re-encrypts and verifies the output, and creates a full **Before edit**
backup before atomically replacing the profile file. It preserves the opaque class-state
tail, AES padding, unused file capacity and companion files. Unknown layouts, incorrect
XUIDs, failed MACs, out-of-range values, active game locks and stale editor sessions are
rejected without replacing the save. The work uses the same cancellable worker and
shutdown handling as backups. Run `python -m unittest test_save_editor test_saves_ui -v`
for the synthetic codec, fault-injection and Qt checks; test fixtures contain no game keys
or game-generated saves.

The editor's **Unicorn cars** section offers **Add missing unicorns**.
It authenticates the selected save's garage database, keeps a full safety backup,
and adds only missing models from the seven-car unicorn list. Existing cars,
upgrades, tuning and the selected car are preserved. This is an offline edit;
no server or Message Center collection is needed. See [garage format and validation](unicorn_cars.md).

When the save has not finished the **1000 CLUB tutorial**, the editor shows that section
with **Mark the tutorial finished**. Signed in to Forza Live, a career past the point where
the 1000 CLUB opens starts that tutorial at every load and the car takes no input but START
until it is done (#237). Its progress is four step flags at the end of the profile's
`CCarChallengeState` entry (after the per-car challenge list); the action sets them, after
the usual full backup, and changes nothing else. A save without that entry, or one whose
entry does not parse exactly, shows nothing.

Format reference: [Pinyon Shift's FH1 save research](https://arcanite24.github.io/pinyon-shift/game-saves/).
The editor's profile-table addresses and XUID derivation were checked against the local
executables and a disposable game-generated profile. A no-edit round trip reproduced the
entire file byte for byte; changing credits preserved every other body byte and companion
file. A separate copy loaded in the native TU4 game, which then saved normally with
1,000,000 credits and 12,345 XP intact and exited cleanly. The original fixture and real
user save were not modified.

### Mods (#149)

**Mods** lists the folders in `<data folder>/mods` (name, version, author from each mod's
`mod.toml`, and its files), each with a tick to turn it on or off (Return, Enter or a controller's
A on the selected row too), **Move up** / **Move down** for the load order (a lower mod wins over
a higher one for the same file), **Add mod…**, **Remove**, **Refresh** and **Open mods folder**.
The details under the list name the files a mod shares with other mods and whose copy is used.

- **Add mod…** asks for a **Zip file…** or a **Folder…** (dropping either on the page does the
  same). `mods.install` finds the mod in it (a folder with `media/` or `mod.toml`, at the top or
  one folder down), unpacks or copies it into a hidden `.adding-*` folder in the mods folder,
  checks it with the game's rules (`read_mod`) and only then moves it into place; a mod of that
  name is replaced only after the player says yes (never while the game runs). A zip with an
  absolute path, `..`, a link, more than 200,000 files or more than 8 GB unpacked is refused.
  Errors are said under the list ("Not added: ...").
- **Remove** asks first and deletes the mod's folder (a linked folder: only the link); it is off
  while the game runs.
- With no mods the page says how to add one and links the guide; while the game runs, a notice
  says changes apply at its next start.
- A mod the game would skip shows "(will not load)" and why, by the game's rules
  (`mod_archive.h` ScanMod): no `media/` folder or no usable file (non-ASCII names, paths over 255
  characters and `.zip` files are left out), a file it cannot read, two names that differ only in
  case, a file over 64 MB that replaces no loose file in the game's `media` folder, a folder named
  `*` or with a `|`.

launcher.json keeps the order and the mods turned off (`"mods": {"order": [...], "disabled":
[...]}`); every start passes `--forza_mods_dir=<data folder>/mods --forza_mods=A|B` (`|` alone:
none), except an option that **Extra command-line flags** (**Settings > Advanced**) name, as
`--name=value` or `--name value` (the game refuses an option given twice). The player's guide is
[modding.md](modding.md). Tests: `launcher/test_mods.py`.

### Texture packs (#384)

**Texture packs** has the two switches of Settings > Graphics > Texture packs (the same settings,
through `SettingsPage.apply_external`), the list of packs and **Make HD pack**. The list is the
Mods page's (`FolderListPage` in `ui/mods_page.py`): the folders in `<data folder>/textures` but
`dump/` (`texture_packs.py`), ticked to use, **Move up** / **Move down** (a lower pack wins for the
same texture), **Add pack…** (zip, folder or a drop; only fingerprint-named textures and text files
are copied, checked as the game reads them, zip slip and size caps as for mods, a dump refused),
**Remove**, **Refresh**, **Open textures folder**, and the details of the selected pack (its
textures, size, graphics memory, the textures it shares with other packs and whose copy is used).
Texture files directly in `textures/` (milestone 1's layout) are the pack **Loose files (textures
folder)**: always first (every pack wins over it), passed as `.`, which can be turned off but not
moved or removed.
launcher.json keeps `"texture_packs": {"order": [...], "disabled": [...]}`; with Use texture packs
on, every start passes `--texture_packs=A|B` (SDK patch 0310; `|` alone: none) unless the extra
flags name `texture_packs` or `texture_pack_root`.

**Make HD pack** (`hd_pack.py`) upscales the player's dump into `textures/hd-pack` with
Real-ESRGAN's ncnn-Vulkan build, downloaded on first use from its official release (pinned in
`pins.py`, SHA-256 checked) into `<data folder>/tools`, in a background `Task` with progress,
**Cancel** and resume; closing the window asks first and stops it. The page shows the estimate
(textures, disk, graphics memory, time; each scale with its graphics memory) and warns on weak
graphics cards; 4x is offered only with at least 10 GB of dedicated graphics memory
(`Config.pc_specs`, else greyed out with the reason). The page names the upscaler's and the
model's licences with links. Details, the licences and
the measurements: [texture packs](texture_packs.md). Tests: `launcher/test_texture_packs.py`,
`launcher/test_hd_pack.py`.

**Make decal pack** (`decal_pack.py`, #659) reads the Paint Shop's decals from the game's own
`media/Livery.zip` (no dump), names each by the fingerprint the game gives it and upscales them
with the same upscaler and job (`hd_pack.upscale`) into `textures/decal-pack`, 2x (default) or
4x, in the same background `Task`. Details: [texture packs](texture_packs.md#make-decal-pack).
Tests: `launcher/test_decal_pack.py`.

## Toolchain (pinned in `launcher/forza_launcher/pins.py`, every archive checked by sha256)

* Linux: the system `clang` (>= 21; the project is tested with 23), `cmake` (>= 3.25) and
  `ninja` are used when present, else official LLVM 23.1.3, CMake 4.4.4 and ninja 1.13.2 are
  downloaded into `toolchain/` (LLVM is a 2 GB download; only the compiler, linker and archiver
  tools are unpacked, 0.8 GB, and the archive is deleted afterwards). The C++ standard library headers
  must come from the system (`g++`/`libstdc++` dev package: Debian `build-essential`, Fedora
  `gcc-c++`); the launcher tests a C++23 compile and says so if that fails.
* The SDK: the pinned ReXGlue nightly (`nightly-20261002-bd833a2a`, downloaded from the SDK's
  release) plus ReIGlue's plugin, runtime and codegen tool (the project's fork, built at its pinned commit) from
  `sdk-overlay/rexglue-overlay-<os>-amd64.tar.gz`, which CI builds and the installer carries.
  Redistribution: the SDK is BSD-3-Clause (copyright Tom Clay, portions Xenia); the binary
  form must reproduce the notice, which `licenses/rexglue-sdk.txt` in every package does.
  The project's own license is GPL-3.0-or-later (`licenses/ForzaHorizon1-ReImagined.txt`), with the
  additional permission for graphics, VR and platform components and the history of the earlier MIT
  releases in `licenses/NOTICE.txt` (the repository's `NOTICE.md`); the Forza Live server in the package is
  AGPL-3.0-or-later (`licenses/forza-live.txt`, copied from `server/LICENSE`).
  The overlay holds SDK code only, no game code.
* Windows (built once in the VM, see "Windows"): portable LLVM (clang-cl, lld-link), CMake, ninja downloaded the same
  way. The MSVC CRT and Windows SDK come from an installed Visual Studio 2022 (17.3+) / Build Tools
  (`vswhere`, `vcvars64.bat`; older ones are skipped, see "Round 5") if there is one, else **xwin** on the player's machine after the
  player ticks "I accept Microsoft's license" (the launcher links the license; nothing of
  Microsoft's is redistributed). The toolchain file is `tools/cmake/windows-native-clang-cl.cmake`.
  The Windows overlay is the whole SDK prefix (the SDK's prebuilt Windows plugin has no
  Vulkan backend), built by CI.
  **MSYS2 and Cygwin tools are never used (#100).** devkitPro and similar put an MSYS2 `usr\bin` on PATH; its
  `cmake` turns `C:\Users\x` into `/home/x/...` ("The source directory ... does not exist", build failed at 22%).
  `build_env.find_tool` walks PATH and skips cmake, ninja and clang-cl that sit under a `msys`/`msys2`/`msys64`/`cygwin*`
  folder, next to `msys-2.0.dll` or `cygwin1.dll`, or whose `--version` says MSYS/Cygwin; each skip is a line in the build
  log (`cmake: skipped ...`). Order after that: a native program on PATH, the launcher's downloaded tool, the one bundled
  with Visual Studio (`Common7\IDE\CommonExtensions\Microsoft\CMake`), then the download. The PATH given to cmake and
  ninja has the MSYS2/Cygwin folders removed too (logged as `PATH: left out ...`). Tests: `launcher/test_tools_msys.py`.

### HTTPS certificates (#68, #85)

Every download and the update check go through `fetch.urlopen`, which verifies certificates
(never switched off). On Windows and macOS it first lets the operating system verify the chain
(`truststore`, as pip does: Windows fetches a missing root certificate itself, which Python's own
read of the ROOT store does not trigger, the cause of "CERTIFICATE_VERIFY_FAILED: unable to get
local issuer certificate" on a fresh Windows 11 in #85); if that fails it tries Python's default
context with the distributions' CA files and `certifi`'s bundle. On Linux only that second context
is used (#68). If no context can verify, the error names the host and says it is usually antivirus
HTTPS scanning or a proxy that replaces certificates, and each failure is written to `launcher.log`.
`truststore` and `certifi` are in `launcher/requirements.txt` and bundled by the spec (certifi's
`cacert.pem` as data); the Flatpak module `certifi` pins the wheel by sha256. Tests:
`launcher/test_fetch.py` (context choice per platform, fallback, error text).

### Child process environment (#87)

PyInstaller puts the bundle's `_internal` folder on `LD_LIBRARY_PATH` (the player's own value is kept in
`LD_LIBRARY_PATH_ORIG`), and every program the launcher started inherited it: `rexglue` failed with
`libstdc++.so.6: version GLIBCXX_3.4.31 not found` against the bundled, older libstdc++ (Linux .tar.gz build).
`launcher/forza_launcher/cleanenv.py` returns the environment for any external program: the saved `*_ORIG` library
paths restored (or the variable removed, or only the bundle's entries stripped when none was saved), and the bundle's
`QT_PLUGIN_PATH`, `PYTHONHOME`, `SSL_CERT_FILE` and bootloader `_PYI_*` markers dropped. The build steps
(`builder.run`, `tool_env`, compiler and version probes, the `cmake -P` RPATH step, the `--run-script` tools),
the game and the local server (`game.Session`; the game's lib dirs are put in front of the player's own path),
the GPU queries, `git rev-parse` and the archive update helper all use it, and `ui.window.run` restores
`os.environ` itself so that Qt's `xdg-open` is clean too (the loader reads the path at process start, so this does
not affect what is loaded). Tests: `launcher/test_cleanenv.py` (frozen environments with the original set, unset and
empty, every launch path). `tools/prerelease.sh --only launcher` also runs `git` and a fake game under a library
path of broken `libz`/`libpcre2`/`libstdc++` and fails if either inherits it.

### When the game ends (#98)

The Play page polls the game. Exit 0 and 75 (the game restarting itself, #81) are not errors. A red "The game stopped
(exit code N)" box is only for crash-like exits: `game.exit_is_crash` says no when the player pressed Stop in the
launcher (`Session.stop_requested`), when Linux reports SIGINT/SIGTERM (-2, -15), and when the game's log ends with its
own shutdown lines ("Title terminated; hard-exiting process", "Execution complete").

Why a reporter on Windows saw "exit code 1" after closing the game: the game itself exits 0 when its window is closed
(SDK `ReXApp::OnClosing` ends with `std::_Exit(0)`; checked on Linux with SIGTERM: "Title terminated; hard-exiting
process", exit 0). The report's log has that closing line in its first two runs and in none of the later ones (the runs
the launcher started): they just stop, and exit code 1 is what Python's `Popen.terminate()` gives on Windows
(`TerminateProcess(handle, 1)`), which is what the Stop button (and quitting the launcher) did. The launcher therefore
now stops the game the way the close button does: on Windows it posts WM_CLOSE to the game's windows
(`game.request_window_close`), so the game shuts down, writes its log and exits 0, and ends it with `terminate()` only if
it is still there after 10 s; on Linux `terminate()` is SIGTERM, which SDL turns into the same close request. Not tested on
a real Windows machine (the code is exercised with the window call stubbed).

"Last errors in the log" skips lines the game writes in normal play (`game.HARMLESS_ERRORS`: `ffmpeg:` audio frames,
`Unimplemented XLIVEBASE/XAM message`, `ResolvePath(... device not found`, `[NtCreateFile] FAILED`, `STUB`), and shows
no list when nothing else is left. Tests: `launcher/test_game_exit.py`.

## Settings

`launcher/forza_launcher/schema.py` lists every key (about 70) with its group, type, default,
range and help text: Display (frame rate, vsync, fullscreen, window, resolution preset, render
scale, filtering, FSR/CAS, FXAA, ...), Controls (keyboard and mouse `mnk_*` with the steering ramp, 25 keybinds,
racing wheel `wheel_*` (mode, rotation, pedals, curves, buttons, force feedback and its centring, engine rumble and road rumble strengths, rumble style, clutch and shifter, pedal and shifter devices; [playing.md](playing.md#racing-wheels-issue-4)), controller bindings `controller_remap` (written by Controls, Bindings; [playing.md](playing.md#remapping-a-controller-279)),
deadzones, vibration, `FORZA_PAD_PER_USER`), Gameplay (the CPU drivers' rubber banding), Audio, Online (server mode, live room, Horizon
World switches), Profile (own save or a copy of a Xenia Canary save, gamertag, language),
Advanced (log, GPU, game/data folders, extra command-line flags, environment variables,
a free-form "other settings" box for any further `forza.toml` key, a live preview of the
generated `forza.toml` and command line). Keys that hold their default are not written.
Display > Picture > **Cockpit gauges always lit** (`cockpit_gauges_lit`, off by default, #183)
keeps the instrument backlight of your own car on by day too; the headlights and other cars
stay as the game draws them ([how](rendering.md#cockpit-gauges-lit-by-day-183)).
Audio > Car sounds > **Engine** and **Turbo and supercharger** (`car_engine_volume`,
`car_turbo_volume`, 0 to 200 %, 100 by default, #730) scale the game's own level of those
sound buses for every car: the engine voices, and the turbo spool, blow-off valve and
supercharger whine. Tyres, wind, transmission and the rest keep the game's mix
([how](playing.md#car-sound-levels-730)).
Audio > Radio > **Station changes** (`radio_station_switch`, Game by default, #401): Fast
uses a 0.15 s repeat guard and 0.25 s station fades; Instant uses a 0.075 s guard and
0.04 s fades. Both retarget to the newest accepted request and work with stock and custom
stations. Stream startup and mixing still add latency; TU4 custom test-tone output
crossed a fixed amplitude threshold after 0.216 and 0.250 s in two observations
([measurement and coverage limits](playing.md#running-the-game)).
Audio > Radio > **Keep radio during races** (`radio_keep_in_events`, off by default)
restores the chosen stock/custom station or Radio Off at qualified career/Rally
race-opening music cues. Earlier unqualified intro music retains its native path.
Restart after changing it. Story/post-race music and native dialogue, effects and
loading/finish fades retain their behavior
([scope](playing.md#running-the-game)).
Gameplay > CPU drivers > **Rubber banding** (`ai_rubber_banding`, Game by default, #433): Off
races every CPU driver at its own skill wherever you are, in single-player races and free roam;
online races keep the game's AI ([playing.md](playing.md#running-the-game)).
Gameplay > Map > **Fast Travel Anywhere** (`fast_travel_anywhere`, on by default, #424, Title
Update 4): fast travel from the map to any road for free, as the unlock once bought with Tokens
did. No Tokens are given and the other Token offers stay; off brings the game's offer back
([playing.md](playing.md#running-the-game)).
Gameplay > Startup > **Skip startup videos** (`skip_startup_movies`, off by default): the game
starts straight at the title screen, without the Microsoft Studios, Dolby and Playground Games
videos (about 45 s). The game boots as after a restart of itself; the festival and 1000 Club
videos inside the game still play. Applies on the next launch.

**Play in VR** (#333, experimental): the Play page shows a **Play in VR** button next to Play when an
OpenXR runtime is installed (`vr.detect()`: on Linux the active runtime manifest, incl. the host's
inside the Flatpak; on Windows the `HKLM\\SOFTWARE\\Khronos\\OpenXR\\1` `ActiveRuntime` value, read-only).
Play always starts flat. Play in VR starts with the VR values as launch arguments for that session only
(frame rate 72, scene MSAA off, render scale 2 or the chosen Sharpness, true stereo, the VR trace off,
the chosen mode and seat height; `--gpu_backend=vulkan` on Windows; inside the Flatpak the host's manifest
in `XR_RUNTIME_JSON`), so the saved flat settings stay as they are; an extra flag the player named wins.
The first time a checklist asks to start the VR app, connect and put on the headset, then press Play in VR
(**Don't show again**, `vr.checklist_hidden` in launcher.json). Settings has a **VR** tab: a status line
(runtime found, or links to WiVRn and SteamVR), **Mode** (Cockpit, recommended / Big screen; `vr_mode`),
**Sharpness** (Standard / Sharp / Sharpest; `vr_picture`), **Seat height (cm)** (`vr_seat_height_cm`),
**Swap eyes** (`vr_swap_eyes`, off; passed only when on), the recentre hint and an **Advanced** expander
listing what VR overrides. The four settings are launcher-only
(never written to forza.toml); an older config's `vr_mode` is just the chosen mode ([vr.md](vr.md)).
On Windows, Display also offers DLSS Quality, Balanced, Performance and DLAA,
with independent 1080p/1440p/4K output and automatic NVIDIA runtime setup. See [`native_dlss.md`](native_dlss.md).
On every platform and GPU the Upscaling filter also offers **AMD FSR 3 (temporal)**, with FSR 3
quality (Quality, Balanced, Performance, Native AA) and output (1080p, 1440p, 4K): Play adds
`--resolution`, `--resolution_scale`, `--fh1_output_width/height`, `--fh1_upscaler=fsr3_<mode>`
and keeps `--present_effect=fsr` as the presenter filter and fallback (`fsr3.py`; DLSS wins
when it is on). See [Rendering issues](rendering.md#temporal-upscaling-with-amd-fsr-3-51-2026-10-16).
Online: public (`forza-reimagined.playit.plus:56677`, written as `live_server` and `FORZA_SERVER`),
local (the launcher starts the Forza Live server inside its own executable, or reuses one
on port 21201, like `play.sh`), your own, or offline. **Online > Servers** (#587,
`server_list.py`, `ui/servers_page.py`) picks among them: the main server first, then the servers of
the main server's signed list (`/servers.json`, taken only when the list key the launcher ships
signed it and it has not expired), This PC, addresses added by hand, and Offline. It pings each server's
`/status` (players online, round trip, up or down) in a background thread when the page opens and
every 30 s while it stays open; a server that does not answer shows as down and stays listed. Using a
server sets the Online mode (`online.mode`, `online.custom_server` in launcher.json; Offline is mode
`offline`), and Online > Online shows it in its Server card. With "Online
features" off, only the page's "Check servers" button checks them. The Xenia save logic is `play.sh --xenia`
ported (copy once, `--user_data_root/--cache_root/--profile_xuid`, DLC folder shared).
The DLC is shared into the Xenia copy (`xenia-<XUID>/0000000000000000`) with a link, a junction
on Windows, checked at every Play (issues #65, #70): a link to another folder is relinked, a real
folder in its place gives its packages to the launcher's folder and is renamed
`0000000000000000.old`, and where no link can be made the packages are mirrored with hard links.
Without the DLC in the folder the game is started on, the game mounts none, and Horizon Rally
answers "XBOX LIVE MARKETPLACE ERROR: Failed to download required Xbox LIVE Marketplace data"
(reproduced 2026-10-15, TU4, Xenia save, link removed). On Windows, DLC that only
`Documents\forza` has (the game's own default folder) is copied to the launcher's folder when that
has none. Play warns, and the warning stays in view, when TU4 is chosen in Setup but the build has
no `update` folder, when DLC is installed or chosen but the game's folder has none, when a
package has no header, and, with TU4, when no package in the game's folder holds the Rally
Expansion Pack (`Media/1600.puboffer`): Horizon Rally then gives the same Marketplace error (#79).
`forza-launcher --dry-run` prints what Play would run; it matches `tools/play.sh`
(`--game_data_root`, `--log_file`, `FORZA_SERVER`, library path).
Defaults differ from the game's in two places: the first run sets `frame_rate = 60` and `present_effect = "fsr"` (AMD FSR 1; a launcher that already holds a choice keeps it; the old `fsr2` entry becomes `fsr`; `fsr3` is now the temporal upscaler).

**20 and 40 fps (#263).** The Frame rate list starts with 20, 30 and 40 fps; a typed rate under 60 becomes the
highest of those not above it (45 becomes 40), as the game runs only those under 60
(`schema.LOW_FRAME_RATES`, `docs/framerate.md`). The help text says what 20 is for: a PC that cannot hold 30
gets frames that each stay on a 60 Hz screen equally long. Tests: `launcher/test_frame_rate_control.py`.

**Match display (#215).** The first entry of the Frame rate list, "Match display (N Hz)", follows the refresh rate
of the monitor the game runs on (Settings > Display > Monitor; 0 and 1 are the primary display, 2 the second,
and so on). It is stored as `frame_rate = 0` in `launcher.json` and resolved by the launcher
(`forza_launcher/display.py`) whenever it writes `forza.toml`: Qt's `refreshRate()` of that screen, rounded and
clamped to 30 or 60 to 240, and never above what the PC-specs warnings accept (30 on a low CPU, 60 on a mid CPU or a
low/mid graphics card). If the rate cannot be read it uses 60 and says so in the launcher log. The game only ever
sees a number. Qt lists the primary screen first and the rest in the system's order; SDL may order three or more
monitors differently, in which case the label shows the rate of the monitor Qt calls that number. The in-game
Graphics menu saves the frame rate it was started with every time; if that is the resolved rate, Match display
stays, and any other rate chosen in the menu replaces it. Tests: `launcher/test_match_display.py`.

**Battery saver (#140, Linux).** Display > Frame rate > Battery saver: Off (the default), On, or "Auto on battery
(Steam Deck)". When it applies, Play starts the game at 30 fps and render scale 1 with AMD FSR 1 and Scene
anti-aliasing Off (FSR 3 becomes FSR 1, SMAA and FSR Native AA the game's own anti-aliasing) and passes
`--gpu_wait_blocking=true` (already the default). A frame rate of 20 or 40 stays: the player chose it for a
screen 30 does not divide (a Deck at 40 Hz). Only this start's `forza.toml` and command line change: `launcher.json`
keeps the player's own values, which the next start on mains power uses again, and the player's own extra flags
still come last. Auto applies on a Steam Deck (as the GPU detection finds it) whose battery runs it: no charger in
`/sys/class/power_supply` online (type Mains, USB, ...) and the system battery discharging; a controller's or a
mouse's battery (scope Device) does not count. The power source is read once, at Play; Auto on another PC shows that
it never applies there. The in-game Graphics menu saves every value it shows, so the values battery saver forced at
the last start (kept in `launcher.json` under `power`) are not taken from it as the player's choice. The shader
prebuild plans with the player's own settings. Tests: `launcher/test_power.py` (fake sysfs).

### Game language (#434)

Profile > Identity > **Game language** (`game_language`, a launcher setting never written to
`forza.toml`) sets the game's Language ID (`user_language`) and Country ID (`user_country`)
together. Automatic (the default) leaves both IDs as they are, so a saved Language ID or
Country ID keeps working; a choice overrides them when `forza.toml` is written
(`forza_launcher/languages.py`).

How the game picks its text: the Xbox dashboard has twelve languages, so the game combines two
settings. `XGetLanguage` (XC_LANGUAGE, 1 to 12) and `XGetLocale` (XC_COUNTRY mapped to one of 38
locales) index a table in the game (tag such as `hu-hu`); the tags are reduced to a short one and
the short one picks the string table `game:\media\stringtables\<folder>.zip`. Only English
(Language ID 1) lets the country choose among Hungarian, Czech, Dutch, Danish, Finnish,
Norwegian and Swedish; every other Language ID gives its one language, whatever the country (the
country then only tells Spanish Spain from Latin America and the Chinese regions apart). Chinese
(Language ID 8 or 10) opens `CHT` for every country, mainland China included; Slovak and Greek
countries with Language ID 1 are not tested.

| Game language | Table | Language ID | Country ID |
|---|---|---|---|
| English | `EN` | 1 | 103 (United States; any country without a language of its own) |
| English (United Kingdom) | `GB` | 1 | 35 (United Kingdom) |
| Japanese, German, French, Italian, Korean, Polish, Russian | `JP` `DE` `FR` `IT` `KO` `PL` `RU` | 2, 3, 4, 6, 7, 11, 12 | yours |
| Portuguese (Brazil) | `BR` | 9 | yours |
| Spanish (Spain) | `ES` | 5 | 31 (Spain; the Americas give Latin American) |
| Spanish (Latin America) | `MX` | 5 | 71 (Mexico) |
| Chinese (Traditional) | `CHT` | 8 | 101 (Taiwan) |
| Czech | `CZ` | 1 | 23 |
| Hungarian | `HU` | 1 | 42 |
| Dutch | `NL` | 1 | 74 |
| Danish | `DA` | 1 | 25 |
| Finnish | `FI` | 1 | 32 |
| Norwegian | `NB` | 1 | 75 |
| Swedish | `SV` | 1 | 90 |

The disc also holds `DEV` and `LOC` (development tables) and no table for Chinese (Simplified),
Slovak or Greek. The name list in the game also has the Russian voice sets `ERU`, `DRU`, `BRU`,
`ARU`, which are not string tables. The game's own two IDs, by hand: `user_language = 1` and
`user_country = 42` is Hungarian. Tested in the game: every row above was run and the folder the game opens
checked in its log; Hungarian, Czech and Dutch were also checked on screen (the text of the
missing-content message after the intro is in that language).

### GPU choice (#52)

Many glitches depend on the graphics card, so Settings > Graphics starts with **Graphics card**: NVIDIA, AMD, Intel,
Steam Deck, or "Other / not sure". The launcher detects the card once (first start; the answer is kept in
`launcher.json` under `gpu`) and preselects it; the player can change it at any time.

* **Detection** (`launcher/forza_launcher/gpu.py`, no Qt): Linux reads `/sys/class/drm/card*/device/vendor`
  (0x10de NVIDIA, 0x1002 AMD, 0x8086 Intel); the Steam Deck is the DMI board or product name `Jupiter` (LCD) or
  `Galileo` (OLED) from Valve and wins over its AMD APU; without those (#140) its APU: the graphics' PCI id
  (1002:163f Van Gogh, 1002:1435 Sephiroth) or the CPU's name (`AMD Custom APU 0405` or `0932`), and Steam's
  `SteamDeck=1` together with another "AMD Custom APU" (`gpu.steam_deck_reason`; Windows: the board, the CPU or
  the adapter `AMD Custom GPU 0405`). Windows asks PowerShell (`Get-CimInstance Win32_VideoController`
  and `Win32_BaseBoard`), falling back to `wmic`; the vendor comes from the adapter name or the `VEN_xxxx` of its
  PNP id; Microsoft Basic, remote and virtual adapters are ignored. With several cards (laptop iGPU + dGPU) the
  discrete one wins (NVIDIA, then AMD, then Intel). `FORZA_GPU=nvidia|amd|intel|deck` overrides.
* **Which card the game runs on** (#89): a laptop with an Intel UHD 630 and a GTX 1050 started the game on the Intel
  GPU (Vulkan 1.2, an old driver: device lost at the intro). The SDK already had `vulkan_device` / `d3d12_adapter`
  (an index, -1 = automatic), which the launcher cannot know, so the SDK patch `0054-gpu-vendor-and-discrete-preference`
  adds the `gpu_vendor` setting (`nvidia`, `amd`, `intel` or a PCI id such as `0x10de`; empty = automatic) and a better
  automatic choice. Vulkan (`src/ui/vulkan/vulkan_provider.cpp`) orders the physical devices by: the `gpu_vendor`
  match, the supported features (the existing `vulkan_prefer_*` cvars), then the device type (discrete, integrated,
  virtual, software), then the driver's order. Direct3D 12 (`src/ui/d3d12/d3d12_provider.cpp`) takes among the
  adapters that can create a feature level 11_0 device the `gpu_vendor` match, else the one with the most dedicated
  video memory (an iGPU has little of its own), else the lowest index. An index (`vulkan_device >= 0`,
  `d3d12_adapter >= 0` or -2 for WARP) still wins; a vendor with no such card logs a warning and chooses
  automatically. The chosen device is logged once (`Vulkan device in use: 1: NVIDIA GeForce GTX 1050 (vendor
  0x10DE, ..., discrete, API 1.3.x)`; Direct3D 12: `DXGI adapter in use: ...`). The launcher passes
  `--gpu_vendor=nvidia|amd|intel` for the card chosen in Settings (`game.gpu_vendor_arg`); "Other", "Steam Deck",
  or a `gpu_vendor` / `vulkan_device` / `d3d12_adapter` of the player's own (extra flags, other settings,
  the Vulkan device setting) pass nothing. The patch is number 0054 because PR #88 may take 0053.
* **Defaults**: a new launcher gets the vendor's preset as its first-run values (backend, render scale, upscaling
  filter, frame rate, shader switches). A launcher that already holds settings keeps every one: it only learns the
  vendor. Changing the vendor asks "Use the recommended settings for X now?" (No changes only the markings);
  the button "Use the recommended settings for this card" applies them again. Reset settings restores the preset.
* **PC specs** (#206, `launcher/forza_launcher/specs.py`, no Qt): the launcher also reads the CPU (name, physical cores,
  AVX2 as a stand-in for FMA3), the RAM and the card (name, video memory, integrated or not) and keeps them in
  `launcher.json` under `specs`; they are detected again when the graphics card changes or with "Check this PC again".
  Linux reads `/proc/cpuinfo`, `/proc/meminfo`, amdgpu's `mem_info_vram_total`, `nvidia-smi` and `lspci -mm`; Windows
  uses the same single PowerShell query (video memory from the display class registry key, as `AdapterRAM` stops at
  4 GB) and `IsProcessorFeaturePresent(40)`. `FORZA_SPECS="cpu_cores=4,avx2=0,ram=8,vram=4,integrated=0"` overrides.
  Two tiers: CPU (limits the frame rate): low = no AVX2, under 4 cores, under 8 GB RAM or a Steam Deck, mid = under
  6 cores, high = 6+ cores with AVX2; graphics (limits render scale, FSR 3, anti-aliasing): low = integrated or under
  4 GB, mid = 4 to 7 GB or a GTX / RX 400-500 / Vega-era card, high = 8 GB+ on a newer card. A new launcher gets 30 fps
  (low CPU) or 60 fps, and render scale 2 only with a high-end card, on top of the vendor preset (whose known problems
  always win). On an entry card (low graphics, or a GTX / RX 400-500-era card with 4 GB; not a Steam Deck) it also gets
  the 540p 3D scene (#640: a GTX 1650 Ti laptop is limited by its GPU at the 720p scene, 46.8 -> 54.3 fps in the
  benchmark, docs/perf.md). An existing launcher.json on an entry card gets it too, once (`migrated.scene_540`),
  with a notice on the Play page that says where to change it back (Settings > Graphics > 3D scene resolution);
  a player who sets 720p again keeps it. Only where 540p is drawn: FSR 3, DLSS, a separate anti-aliasing method and
  Ultrawide gameplay keep 720p. Settings that are heavy for the PC get an orange line with the numbers (render scale 2 renders 2560x1440
  ...). A launcher with existing settings is never changed silently: a banner on the Settings page offers "Use
  recommended" (only the settings that warn) or "Keep mine", and comes back only when the hardware changes.
  On a Steam Deck (#140) the banner offers the Deck preset itself (`gpu.DECK_SETTINGS`, below) whenever a setting
  differs from it (a frame rate of 20 or 40 counts as the preset's), "Use recommended" applies all of it, and the
  answer is kept per preset revision (`specs.DECK_OFFER`), so a Deck that answered before the preset had Scene
  anti-aliasing Off is asked once more. A new launcher on a Deck gets the whole preset. On a Deck the frame rate
  warns above 40 (not 30), and Scene anti-aliasing other than Off warns. Match display on a Deck picks a rate its
  screen divides, at most 40 (`display.under_cap`: 40 at 40, 80 or 120 Hz, 30 at 60 or 90 Hz); elsewhere a 40 Hz
  screen gets 40 and any other under 60 gets 30, as before. Tests: `launcher/test_steam_deck.py`.
* **Known problems** are never forbidden: the option keeps working, with an orange line under it giving the reason
  and the issue number while it holds the problem value, and the choice is marked ("Direct3D 12 (flickers on
  NVIDIA, #46)"). The explicit choice of the player always wins.
* **Old Intel graphics drivers (#672):** an Intel integrated GPU whose driver reports a Vulkan API below 1.3 can't run
  the Vulkan renderer (an HD 520 on Vulkan 1.2.203 closed before the title screen, #669; a UHD on 1.2.131 ran at about
  1 fps, #640). At each start the launcher asks the Vulkan driver in a process of its own
  (`forza-launcher --vulkan-devices`, `launcher/forza_launcher/vulkan_probe.py`) and, when the GPU the game would use
  is such an iGPU, Setup and Play say so: update the Intel driver from intel.com, or use Direct3D 12 ("Use Direct3D
  12" switches Settings > Graphics > Graphics API). A new launcher.json on Windows starts on Direct3D 12 there; an
  existing one is never switched by itself. ReGPU (experimental) is a Direct3D 12 renderer too, so with it chosen there
  is no warning either; it is never the advice, as it was not tested on Intel graphics (the Intel preset marks it
  untested). On Linux, which has no Direct3D 12, the warning asks to update Mesa. No
  warning when the version is unknown, and never from the Vulkan version on AMD (Vega iGPUs report 1.3.2xx on
  current drivers, #268). Tests: `launcher/test_intel_vulkan.py`.

| Card | Preset | Marked as a known problem |
|---|---|---|
| NVIDIA | Vulkan, render scale 1, AMD FSR 1, 60 fps, shader cache and background shader compilation on (scale 2 is the most you should need) | Direct3D 12 (Windows): flicker, green reflections on glass and paint (#46). Render scale 3 and above: flicker and coloured artifacts across the map (#47). Background compilation or the shader cache off: Vulkan stutter (#49, open: warm-cache stutter unmeasured). Traffic car glow in the distance (#48): no setting avoids it |
| AMD (Linux and Windows) | Vulkan, render scale 1, AMD FSR 1, 60 fps | none; render scale 3 and 4 are clean on Radeon (Vulkan/RADV, and Direct3D 12 under vkd3d-proton at 3) |
| Intel | Vulkan, render scale 1, AMD FSR 1, 30 fps (Direct3D 12 on a new Windows install whose iGPU driver is below Vulkan 1.3, above) | a driver below Vulkan 1.3 (#669, #640); Direct3D 12 (a different render target path on Intel), ReGPU and render scale 2 and up are marked as untested / slow |
| Steam Deck | Vulkan, render scale 1, AMD FSR 1, 30 fps, Scene anti-aliasing Off, Console shadow and texture detail, 3D scene at 720p, Letterbox on, shader cache and background shader compilation on; Battery saver (above) stays off, as on every PC ([steam_deck.md](steam_deck.md)) | render scale above 1 and a frame rate above 40: the Deck ran 17 to 25 fps (#45); 40 suits its screen at 40 Hz (LCD) or 80 Hz (OLED) |

Tests: `python3 -m unittest launcher/test_gpu.py` (detection from a fake sysfs and PowerShell / wmic output, presets
valid for the schema, warnings, the first-run / existing-settings / player-choice flows, the `--gpu_vendor` argument).

## Release and CI (`.github/workflows/`)

* `release.yml` (PRs to `main`, tags `v*`, manual): `launcher-tests` runs the Python unit
  and offscreen Qt tests on Windows and Linux without waiting for an SDK build; release
  publishing requires both test jobs to pass. `sdk-linux` (`tools/build_rexglue_sdk.sh` +
  `tools/package_sdk_overlay.sh`, cached on the patches), `sdk-windows` (clang-cl on a Windows
  runner), then `launcher-linux` (PyInstaller, `launcher/packaging/make_package.py` makes
  `ForzaHorizon1-ReImagined-linux-x86_64.tar.gz` with `install.sh`, `.desktop`, icon) and
  `launcher-windows` (PyInstaller, Inno Setup `launcher/packaging/installer.iss`: per-user
  install with Start menu and optional desktop shortcut, bundled VC++ 2015-2022 x64
  redistributable installed when missing, after a page that says so), `flatpak` (flatpak-builder in the Flathub image, see
  "Flatpak" below) and `publish` (tags only) creating the release and uploading all three.
  Before upload, `launcher/packaging/release_checksums.py` hashes the final asset bytes
  and writes `SHA256SUMS`, uploaded alongside them (#354). The manifest uses sorted,
  download-relative filenames and excludes itself; reruns replace it. SDK overlays are
  removed before hashing, as before upload. Checksums verify bytes against the release's
  manifest; they are not code signatures or a malware assessment. Code-signing and vendor
  submissions remain separate work. The version comes from the tag. Nothing here needs
  the game or generated code.
  The Windows job also records `windows-build-manifest.json` after staging the final
  payload and before compiling the installer. It lists each staged file's relative
  path, size and SHA-256, the source commit, public Actions run URL, Python version
  and packaging dependency versions. The installer includes it, and a copy named
  `ForzaHorizon1-ReImagined-windows-files-<version>.json` is published beside the
  installer and covered by `SHA256SUMS`. This lets maintainers identify an installed
  file even if antivirus has removed it. It covers the staged payload, not Inno's
  generated uninstaller or the separately bundled Microsoft redistributable. When
  the release is signed, it is written after the launcher is signed. The installer
  smoke test verifies that the installed launcher matches its recorded hash.
  Against Defender's machine-learning detections of `forza-launcher.exe` (#482, #354), the
  `launcher-windows` job builds PyInstaller's bootloader from source and gives both executables a
  Windows version resource. `PYINSTALLER_COMPILE_BOOTLOADER=1 pip install --no-binary pyinstaller -r
  launcher/requirements-build.txt` replaces the stock `run.exe` and `runw.exe`, which every PyInstaller
  program (malware included) shares, with ones compiled on the runner by PyInstaller's waf and the
  Visual Studio C++ tools; the version is still the one `requirements-build.txt` allows.
  `launcher/packaging/check_bootloader.py` fails the job when either file is still byte-identical to the
  PyPI wheel's (PyInstaller's source distribution ships the stock ones and keeps them without the
  variable). `launcher/packaging/windows_version_info.py` writes the version resource
  (`CompanyName`, `ProductName`, `FileDescription`, `FileVersion`, `ProductVersion`, `OriginalFilename`,
  copyright) from `forza_launcher/_version.py` when `forza-launcher.spec` runs, and a job step reads it
  back (`test_installer.ps1` reads both programs' resources from the installed files). `installer.iss` sets the installer's own (`VersionInfoVersion` from `/DVersionNumeric`, company,
  product, description, original file name). Linux is untouched: the spec only passes `version=` on
  Windows, and the Linux job and the Flatpak build use the stock PyInstaller wheel or none.
  Code signing (#354) goes through SignPath Foundation and is off until the repository variable
  `SIGNPATH_ORGANIZATION_ID` and the secret `SIGNPATH_API_TOKEN` exist (`SIGNPATH_PROJECT_SLUG` if the
  SignPath project is not `ForzaHorizon1-ReImagined`); it never runs on pull requests, so until then
  releases are built unsigned as before. When it is on, `launcher-windows` makes two signing requests
  with `signpath/github-action-submit-signing-request`, each on a GitHub artifact named `unsigned-*`
  that the `publish` job drops:
  1. after Package: the staged `forza-launcher.exe` and Inno Setup's uninstaller. Inno Setup can only
     embed a signature in the uninstaller (which Setup's temporary self-copies also run) while
     compiling, so `launcher/packaging/build_installer.ps1 -PrepareUninstaller` compiles once with
     `SignedUninstaller=yes` and no SignTool: Inno writes `uninst-<Inno Setup version>-<hash>.e32` to
     `signed-uninstaller/` and stops. The signed launcher goes back into the stage before the payload
     manifest is written, and the signed `.e32` into `signed-uninstaller/`;
  2. after the real compile (which finds the signed `.e32` and puts its signature into `unins000.exe`):
     the installer.
  The artifact configurations are `binaries` and `installer` in SignPath; reviewed copies are in
  `launcher/packaging/signpath/`. Tags use the `release-signing` policy (a maintainer approves each
  request in SignPath while the job waits, up to two hours); a run by hand uses `test-signing`.
  `launcher/packaging/verify_signature.ps1` checks every signed file (valid, signed by SignPath
  Foundation and timestamped; for test signing: signed, unchanged and timestamped), the signed launcher
  must still print its version, and `installer-test` checks the installer, the installed launcher and
  `unins000.exe` the same way (`test_installer.ps1 -Signature`). `launcher/test_code_signing.py`
  covers the wiring.
  The VC++ redistributable comes from a versioned Microsoft URL pinned with its SHA-256 in
  `launcher/forza_launcher/pins.py` (`vc-redist-x64`, fetched by `launcher/packaging/ci_fetch.py`), and
  the job fails unless `Get-AuthenticodeSignature` says it is validly signed by Microsoft. When Microsoft
  ships a newer one, follow `https://aka.ms/vs/17/release/vc_redist.x64.exe` to the URL it redirects to
  and put that URL and the file's SHA-256 in the pin (the URL's path names the hash; `test_vc_redist_pin.py`
  checks they agree).
* Privacy (#497, [`PRIVACY.md`](../PRIVACY.md)): the installer shows the policy (`InfoBeforeFile`,
  `licenses/PRIVACY.txt` from `make_package.py`), then an "Online features" page, ticked by default.
  It writes the choice to `install_options.json` in the install folder with a stamp of that run;
  `/ONLINE=no` turns it off for a silent install. `launcher/forza_launcher/online_features.py` applies
  each stamp once: off sets Server to Offline and turns off shader-list sharing, co-op campaign results,
  the update check and Discord activity; on keeps the settings (a new config has them on). Without an
  installer (Linux, Flatpak, a checkout), a new `launcher.json` gets the same question in a dialog
  (`ui/online_features.py`) before the update check runs; it is asked again until answered.
  *Reset settings* keeps those features off for a player who chose off. `test_installer.ps1` installs
  twice (default, then `/ONLINE=no`) and checks the file, and that the uninstaller removes it.
* The website (forza.dyotson.dev) is its own repository,
  [FH1-ReImagined/website](https://github.com/FH1-ReImagined/website), with its Pages workflow
  and `tools/preview_site.sh`. Screenshots it shows from `docs/images` are copied there.

## Developing

```sh
python3 -m venv launcher/.venv && launcher/.venv/bin/pip install -r launcher/requirements.txt -r launcher/requirements-build.txt
launcher/.venv/bin/python launcher/main.py            # the window (in a checkout it also finds this tree's own build and overlay)
launcher/.venv/bin/python launcher/main.py --build --root /path --jobs 4   # the same build without the window
cd launcher && .venv/bin/pyinstaller --noconfirm forza-launcher.spec    # the frozen app
```

Run the launcher suite from the repository root after installing `launcher/requirements.txt`:
`python -m unittest discover -s launcher -p 'test_*.py' -v`. Qt runs offscreen. The tests
use temporary data and fake child programs; the game-file acceptance tests skip when their
fixtures are absent, and the Linux archive/shell tests skip on Windows.

Windows baseline (#121): the suite previously had 10 failures and 5 errors. Most were
host assumptions in the tests: POSIX library paths, extensionless executable fixtures,
symlinks requiring privileges, or updater text and path spelling. The fixtures now use
native paths and executable names, explicitly select the platform behavior under test,
and exercise Windows directory junctions as well as the mirror fallback. Session cleanup
runs before temporary-directory cleanup. There was also a launcher bug: `Session.start`
kept the parent's `server.log` handle open. It now closes that copy immediately after
spawning the server (including on spawn failure); the child keeps its own logging handle.
Validated with the full 184-test suite on Windows/Python 3.12.10 (161 passed, 23 expected
skips) and Fedora WSL/Python 3.14.6 (175 passed, 9 missing-game-fixture skips), including
the offscreen Qt tests on both platforms.

## Memory check (#32)

A build peaks at a base plus a share per compile job: measured 1.5 GB at `-j3` (Windows), 1.8 to 2.0 GB at `-j4`
and 3.2 GB at `-j8` (Linux). `build_env.ram_need_gb(jobs)` is `0.8 GB + 0.35 GB * jobs` (RAM_BASE_GB,
RAM_PER_JOB_GB), a line just above those three points (1.85, 2.2, 3.6 GB). The game's own ~1.1 GB is not part of it:
it matters while playing, not while building.

* The Build page shows one memory row for the chosen "Parallel jobs" and updates it when the number changes:
  green when the free memory covers the estimate; yellow when it does not, with the job count that fits
  (`build_env.jobs_for_ram`); red only when the installed memory is under 2 GB (even `-j1` cannot work).
* Start build with too few free GB for the chosen jobs asks "Build with N jobs instead?" Yes lowers the spin box
  and builds, No keeps the choice, Cancel stops. Nothing refuses a build that has a chance.
* The default job count is the most that fits the free memory (and the cores, at most 16).
* An 8 GB machine with 5 GB free is green up to `-j12`; with 2.5 GB free `-j8` is yellow and suggests `-j4`.

## Measured (8 cores, 31 GB RAM, system clang 23.1.2; another game was running meanwhile)

Clean builds through the launcher code (fresh data root, the pinned SDK nightly downloaded,
codegen, configure, 346 ninja steps, `-j4`, low priority), wall time and peak memory of the
build's process group (sampled every 2 s):

| Build | Wall time | Peak memory |
| --- | --- | --- |
| source tree, system clang 23 | 4.6 min | 1.8 GB |
| the frozen app with the bundled overlay, system clang | 4.6 min | 1.8 GB |
| frozen toolchain download: LLVM 23.1.3 (2 GB), cmake, ninja, then the same build | 4.2 min after the download (about 6 min in all) | 1.8 GB |

On disk afterwards: build tree about 1 GB, SDK 1.6 GB, runtime 0.2 GB, the unpacked toolchain
1.1 GB (only the compiler driver, linker, archiver tools and `lib/clang` of LLVM's 12 GB), the
extracted game 7.6 GB. The binary built by the launcher booted to the title screen
("PRESS START") and held 59.6 fps with the `frame_rate = 60` the launcher wrote.

## Verified / not verified

Verified on Linux:

* Extraction of a real Games on Demand package (7.6 GB) with progress, `default.xex` checked
  first (base version accepted; `/etc/hostname`, a missing path and a non-package are refused
  with readable messages); 23 DLC packages listed by name and installed.
* The full clean build above, three ways; Play from the launcher (local Forza Live server
  started and stopped with the game, `/dev/shm` clean, no stray processes); cancel at step 60
  and resume (ninja continued with 287 of 346 steps left); a download interrupted at 20 MB
  resumed with Range and passed its sha256.
* `--dry-run` output equals `tools/play.sh` for the same settings (`--game_data_root`,
  `--log_file`, `FORZA_SERVER`, `LD_LIBRARY_PATH` order; `--xenia` gives `--user_data_root`,
  `--cache_root`, `--profile_xuid`; the Xenia save copy and DLC link were run).
* DLC from a Xenia content folder (#36, 2026-10-14, Linux): `tools/install_dlc.py` and the DLC card take
  a Xenia `content` folder, its `0000000000000000`, `4D5309C9` or `00000002` folder, a folder of package
  files, or both mixed. Xenia writes the same header (0x148 bytes of XCONTENT_AGGREGATE_DATA plus a
  little-endian license mask) as this tool; its masks come from the package's STFS licenses (0, 1, 2 or
  all bits in the 23 headers of a Xenia folder), so the copy sets the mask to all bits (the runtime reads
  bytes 0x148..0x14C, `ContentPackage::LoadPackageLicenseMask`). Tested with copies of the 23 installed
  packages in scratch user data roots: with `Headers/` (masks rewritten to 0, 1 and 2, plus an empty
  folder and a folder named like no package: both skipped and listed), without `Headers/` (headers
  generated, byte for byte the originals), only the `00000002` folder, the `4D5309C9` and
  `0000000000000000` folders, a mix of package files and folders, and package files (two synthetic STFS
  packages, as before); the Setup page (Qt offscreen) listed the names and the skipped folders, installed
  23 packages and showed "23 installed". Booting the TU4 build on each of the two Xenia-style
  copies (`FORZA_DLC_LOG=1`, START and A scripted into the main menu): the game probed 20 `DLCRoot`
  mounts and answered 252 `IsCarInstalled` queries, all "installed", none "not installed".
  The Horizon Rally menu entry was not looked at in this run.
* PyInstaller (the spec bundles tools, assets and the Forza Live server's stdlib imports),
  the frozen app starts, runs the local server (`--live-server`) and a whole build; `install.sh`.
* Title Update 4 (2026-10-14, source tree, system clang 23, `--jobs 4`, nice 10, SDK downloaded):
  `--build --title-update <the TU package zip>` applied the update (2 s), generated code from
  `forza_manifest_tu4.toml` pointed at `build/tu`, built 350 steps in 3.6 min (2.0 GB peak) and staged
  `runtime/update` (patched `default.xex`, both facades, `media.zip`); the runtime booted with
  `--update_data_root=runtime/update` to the main menu, which lists HORIZON RALLY. Rebuild detection:
  the same package reads "built", no package reads "stale: the title update was removed".
  A fresh build found one bug that the in-tree build hid: `src/main.cpp` included the base
  `generated/default/forza_init.h` (present in the checkout, absent in the launcher's tree); a TU4
  build now includes `generated_tu4/`.
* Workflows: `actionlint` clean, YAML loads. Landing page: Chromium at 1440x900 and 390x844
  (no console errors, no horizontal scroll, lightbox works).

Windows run time: see "Windows" below (installer, extraction, a whole build with xwin and Play without a GPU were run
in a VM on 2026-10-14). Not verified: a Visual Studio Build Tools machine, Windows 10, the `publish` job (tags
only), the TU4 step on Linux in the frozen app.

## Open

* **Title update and DLC.** Both are optional in the UI; TU4 is marked recommended. A TU4 profile
  save (converted by the game) is refused by the base build, so a player who has one needs the
  TU4 build. A suggestion for `tools/install_dlc.py` (not edited): a `progress` callback; the
  launcher counts bytes by wrapping `xcontent_extract.extract`.
* The Windows build path (on a player's machine), the installer and the `publish` job have never
  run. The CI jobs build them; the Windows SDK overlay has not been used to build the game.
* The Linux overlay is built on Ubuntu 22.04 (CI image `ubuntu-22.04`, glibc 2.35: the binaries
  need `GLIBC_2.35` and `GLIBCXX_3.4.32`, so any distro from 2022 on that has a g++ new enough
  for the game build runs it). 22.04 has neither C++23 headers nor CMake 3.25, so
  `launcher/packaging/ci_linux_sdk_setup.sh` takes g++-14 from the `ubuntu-toolchain-r/test` PPA
  and CMake, ninja and LLVM from the launcher's pinned downloads (`ci_fetch.py`); the SDK's
  extra `-dev` packages (`libx11-xcb-dev` was the missing one) are listed there. The same script
  was run in an `ubuntu:22.04` container: configure, build and `package_sdk_overlay.sh` pass
  (about 8 min on 8 cores).
* Windows CI: `.gitattributes` marks `patches/**` as `-text` and the job sets
  `core.autocrlf false` before checkout. With the runner's default autocrlf the patches get CRLF
  line endings and `git apply` fails on the very first one (`patch failed:
  spirv_translator_fetch.cpp:2030`), which was the first dry run's failure. The clang-cl build
  of the SDK prints millions of warning lines (GitHub then cannot serve the log), so the job
  keeps the output in a file and prints only failures.
  clang-cl reads the SDK's `-Wall` as `-Weverything` (millions of warnings, a 56 minute build)
  and ignores `-fno-char8_t` and `-ffp-model=strict` (the first broke `xboxkrnl_ob.cpp`, a `u8`
  literal passed as `std::string_view`), so the job rewrites those three lines of the SDK's top
  `CMakeLists.txt` to `-w`, `/Zc:char8_t-` and `/fp:strict` before configuring. The build then
  takes about 10 minutes. Hand-run artifacts (run 38572896631): Linux launcher package 79 MB
  (starts, `--version`, `--dry-run`), Windows installer 69 MB (Inno Setup 6.7, with the VC++
  redistributable), SDK overlays 7 MB (Linux) and 18 MB (Windows: runtime, Xenos plugin, codegen
  tool, headers, import libraries, licenses; no Microsoft files).
* Code signing of the Windows installer (SmartScreen will warn).

## Flatpak (Linux, issue #1): status and next steps

Files: `flatpak/` (manifest `io.github.dyotson.ForzaHorizon1ReImagined.yml`, `.desktop`, AppStream
`.metainfo.xml` (validates with `flatpak-builder-lint appstream`), `stage.py`, `rebuild.sh`,
`forza-reimagined.sh`; the CI job `flatpak` is in `.github/workflows/release.yml`). Local build: `flatpak/rebuild.sh` (needs
`flatpak install --user flathub org.flatpak.Builder org.freedesktop.Sdk//26.08
org.freedesktop.Sdk.Extension.llvm22//26.08`; big files go to `~/.cache/forza-flatpak/`).

Design: the app's runtime is `org.freedesktop.Sdk//26.08` itself (glibc 2.44, g++ 16 headers, cmake 4.4,
ninja, python 3.14 are there at run time), and `org.freedesktop.Sdk.Extension.llvm22` (clang 22.1.8,
accepted: the launcher needs >= 21) is declared as the app's extension with `autodownload`. On SteamOS
(the Steam Deck) the .tar.gz launcher has no C++ headers and points the player at this Flatpak (#135). The
launcher runs from source with the SDK's python; PySide6-Essentials 6.12 and shiboken6 are pinned
wheels in the manifest. `stage.py` copies what the launcher needs (launcher, tools, project sources,
server, the CI-built SDK overlay, licenses; no game code, data or generated code) into
`flatpak/stage/` (git-ignored). `paths.IN_FLATPAK` (FLATPAK_ID) adds the extension's `bin` to PATH and
switches Qt to its own file dialogs (the portal would hand out the one picked file, losing a Games on
Demand package's `<name>.data`). Permissions: wayland, fallback-x11, pulseaudio, network, `--device=all`,
read-only `home` and `/mnt`, writable `/run/media` and `/media` (removable drives such as an SD card, for the game and save folder, #717), plus writable `xdg-download` for verified launcher bundles (other disks: `flatpak override --user --filesystem=PATH:ro`, or without `:ro` for a game and save folder there; the launcher names that command when it cannot write a folder).
Saves, config, extracted game and build live in `~/.var/app/<id>/`. New CLI: `forza-launcher --extract PACKAGE`.

Tested (this host, Fedora Atomic, user installation): the app builds and installs; `--extract` of the
real GoD package (7.1 GB, about 15 s); `--build --title-update <TU4 zip> --jobs 8` inside the sandbox with
the SDK's cmake/ninja and clang 22.1 (no downloads except the 122 MB SDK nightly): 3.4 min wall, 3.1 GB
peak build memory, TU4 applied, 357 ninja steps; the game was started from the launcher's own launch plan
(Play code path) in the sandbox on Wayland/radv with the local Forza Live server child and a copy of a
Xenia save, but it stopped a second later for want of an audio backend (fixed, see below). Repo size of the app commit 98 MB (734 MB installed with the 496 MB LLVM extension).

Bundle test and game test (2026-10-14, CI run 38594452070, all five jobs green: sdk-linux,
sdk-windows, launcher-linux, launcher-windows, flatpak; `publish` is tag-only). The `flatpak` job is
in `release.yml` (flatpak-github-actions v6.8 pinned, freedesktop-26.08 image, reuses
`sdk-overlay-linux`, the SDK's LICENSE from the pinned commit) and `publish` needs it and uploads the
bundle with the installers. Bundle: `ForzaHorizon1-ReImagined-x86_64.flatpak`, 63 MB (app 259 MB
installed; the Sdk runtime is 1.8 GB installed, the llvm22 extension 0.5 GB).

* Installing the bundle on a clean user installation (`FLATPAK_USER_DIR` scratch, nothing from Flathub
  present): `flatpak install --user --bundle` adds the Flathub remote from the bundle's runtime repo and
  pulls the `org.freedesktop.Sdk//26.08` runtime and its GL/codec extensions by itself (23 s on a fast
  line). It does NOT pull `org.freedesktop.Sdk.Extension.llvm22` (autodownload only applies to installs
  from a remote). So the player runs `flatpak install flathub org.freedesktop.Sdk.Extension.llvm22//26.08`
  once. Without it the launcher still works: it falls back to the pinned 2 GB LLVM download, but now
  also shows the one command in Setup (a warning row, build_env.FLATPAK_LLVM_HINT) and in the build
  log. README and the landing page name the command.
* `--extract` of the 7.1 GB package: 5 s (page cache warm). `--build --title-update <TU4 zip> --jobs 8`:
  3.7 to 4.0 min, 3.1 to 3.2 GB peak build memory (the 3.2 GB is for `-j8`).
* FOUND AND FIXED: the first game start crashed in the guest (`KeBugCheckEx`, SIGTRAP) right after
  "SDL_InitSubSystem(SDL_INIT_AUDIO) failed: Audio target 'pulseaudio' not available". The CI-built
  SDK runtime (overlay, so the Flatpak AND the tar.gz) had SDL3 with no audio backend: the Ubuntu 22.04
  runner had no libpulse/ALSA dev packages when SDL3 was configured (the host-built runtime links
  libpulse, libasound, libdbus, fribidi, libthai through dlopen; the CI one had none of them).
  `launcher/packaging/ci_linux_sdk_setup.sh` now installs libpulse-dev, libasound2-dev, libdbus-1-dev,
  libfribidi-dev and libthai-dev; with the rebuilt overlay the game finds the PulseAudio socket of the
  sandbox (PipeWire host: "HyperX ... 6 ch, 48000 Hz") and runs. The Windows overlay was not affected.
* Play from the launcher's own Play button (the real window on Wayland, a scratch copy of the Xenia
  save through Settings > Profile, the local Forza Live server, nothing from `~/.local/share/forza`):
  title screen, START, main menu with SINGLE PLAYER / MULTIPLAYER / HORIZON RALLY, A, free roam in the
  save's Aventador, driven with scripted RT and left stick (FORZA_AUTOPRESS_FIFO) for 3.5 minutes at
  59.7 to 60 fps. Peak RSS of the game (VmHWM): 0.68 GB at the main menu, 1.0 GB entering free roam,
  1.13 GB after 3.5 minutes of driving. README and the landing page say "about 4 GB free" now with these
  numbers (game 1.1 GB; build 2 GB at -j4, 3.2 GB at -j8). The launcher's memory check follows them (#32, see
  "Memory check" below).
* The launcher window (not headless) runs under Qt's Wayland platform inside the sandbox (KDE Plasma 6,
  radv), with the Qt file dialogs; screenshots taken with spectacle on the host.
* Test recipe: `FLATPAK_USER_DIR=~/.cache/forza-flatpak/userdir` for a clean installation. The window
  can be driven from a script run with the sandbox's python (`flatpak run --command=sh APP -c
  'PYTHONPATH=/app/lib/python3*/site-packages python3 SCRIPT'`; the script and its output must live
  under the home folder, /tmp is private, and home is read-only except `~/.var/app/<id>`).
  The game inside the sandbox is invisible to the host's `ps` (own PID namespace): read
  `/proc/<pid>/status` from a process inside it, stop it with `flatpak kill APP`.

NOT done: the PySide6 size trim (the app is 259 MB installed, 63 MB bundled) and publishing to Flathub.
Flathub lint: the app id fails `appid-url-not-reachable` (the repo is `ForzaHorizon1-ReImagined`) and
`home:ro` needs a justification.

## Windows: real-VM test (2026-10-14, Windows 11 Pro 10.0.26200, 4 cores, 8 GB, no GPU)

VM: `dockurr/windows` in podman (KVM). To reach sshd from the host the container needs
`-e NETWORK=user -e USER_PORTS=22` (the default tap mode did not forward port 22: "connection reset");
the disk survives re-creating the container. Host memory matters: the container was OOM-killed once
when another process grew to 14 GB.

Results, release v0.1.1 `ForzaHorizon1-ReImagined-Setup-0.1.1.exe`:

* Silent install (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /LOG=...`): exit 0 in about
  10 s; `%LOCALAPPDATA%\Programs\ForzaHorizon1-ReImagined\forza-launcher.exe`, `project\`, `sdk-overlay\`
  present; Start menu and desktop shortcuts `Forza Horizon 1 - ReImagined.lnk`; uninstall registry entry; the
  VC++ redistributable step ran (v14.44 afterwards). The desktop-icon task is checked by default, so silent
  installs get it too. Silent uninstall (`unins000.exe /VERYSILENT`) removed files, shortcuts and the entry.
* Interactive install through the wizard (license, folder, tasks, install, finish "Start the launcher"):
  all pages fine, no abort (the colon fix of 0.1.1 holds on real Windows).
* Launcher: starts from the installer's Finish page; the window renders (Play, Setup); `--version` prints
  0.1.1; `--dry-run` without game files prints the readable "not set up" message and exits 1. In a window
  of the default 1040 px width the Setup page's content is clipped on the right on Windows (Segoe UI is
  wider than the Linux font, something in that page forces a minimum width); maximized it is fine. Not
  fixed yet.
* `launcher/packaging/test_installer.ps1 -Setup <exe> [-ExpectVersion X]` automates the above (install,
  assert files/shortcuts/registry, `--version`, `--dry-run`, uninstall, assert removed); it passed on the VM.

### Round 2 (same day, VM as above; issues #26, #28, #29, #30): what was found and fixed

VM access that worked: `ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 2222 tester@127.0.0.1`
(the host key changes when the container is re-created; without those options a script hangs on the
prompt). The Samba share (`\\host.lan\Data`) is slow to show new files to the VM (minutes of stale
directory listings): use it to move big files, and `scp` for scripts. For GUI tests a PowerShell loop
started by a scheduled task in the console session (`schtasks /create ... /ru tester /it`) ran scripts that
click (`SetCursorPos` + `mouse_event`, `SendKeys`) and take screenshots (`Graphics.CopyFromScreen`), which
`scp` brings back; the screen is 1280x800. Python 3.12 + PySide6 in the VM runs the launcher from a
`git archive` of the tree, so a fix is tested in a minute.

* **#29 (Extract game files stays at 0% and vanishes).** Extraction itself works on Windows: a real Games on
  Demand package (7.1 GB, 4 cores, a few minutes), the same package converted by iso2god 1.8.1 from the EU/Asia
  ISO, and the ISO itself (`tools/xiso_extract.py`) all extract in the GUI (source tree and the v0.1.1 frozen
  app) and with `--extract`. Root cause: the worker thread DID report its error, and the Setup page erased
  it. `SetupPage._finished` printed the message and then called `refresh()`, which, with no `default.xex`
  yet (the extraction deletes its `.part` folder on failure), blanked the message label and set the badge back to
  "Needed". Any refusal after the progress bar started (a `default.xex` that is not the base version, not enough free
  disk space, a disk error) looked like the bar disappearing. Reproduced by making `KNOWN_BASE` empty in the VM.
  Fix (e59faa1): failures are kept in `SetupPage.fail` until the next run, shown red, with a dialog and the
  path of the new `launcher.log` (`%APPDATA%\ForzaReImagined\launcher.log`, tracebacks included); `Task.run`
  catches `BaseException` (a `SystemExit` from a tool would have ended the thread without a signal), logs it and
  `sys.excepthook` shows a dialog for anything that reaches the top. The same wipe hit the build (`status3`) and
  the Play page (below). What the players' copies actually fail on is still unknown; the message now says
  (version, entry point, media id of their `default.xex`).
* **#30 (extracted folder, picked its `default.xex`, step 1 stays "Needed", no error).** A complete extracted
  folder (`default.xex` + both facade files) is accepted with "Use these files" or "Choose folder" (checked in
  the VM). A folder missing a file is refused with the file named; the same wipe made the message vanish when the
  player went on to fill the title update field (refresh). The message now says to choose the whole extracted game
  folder (the three files).
* **#29 / #30 again (v0.1.2: players still stuck at Setup; round 3).** Their `launcher.log` files showed the
  refusal the previous round made visible: `version 0.0.0.9 / 0.0.0.10 / 0.0.0.12, base the same, media id
  6368CD69 / 2DC7007B / 4000D145, entry point 0x82a6fbc0`, "it looks like a title update" (wrong: these are
  retail printings of the base game, not updates). The launcher accepted only `(version 7, entry point)`. The
  executables of these printings are the same build (docs/disc.md), so the rule is now by code, not by number:
  `forza_launcher/xexcheck.py` decodes the image (AES via `tools/apply_title_update.py`: openssl or
  `cryptography` when present, pure Python otherwise, 14 s here on the 23 MB image; runs in the extraction
  thread, in a task when "Use these files" is pressed, and the verdict is remembered per file in
  `xex_checks.json` next to `launcher.json`, so the window's frequent refreshes are free) and compares SHA-1
  prefixes of 64 KB pieces of every section (`[va, va+size)`, so padding between sections is ignored) with
  `forza_launcher/xex_reference.py` (hashes only; written by `tools/xex_reference.py extracted/game` from the
  supported files, 458 hashes for the three executables). Same for the two facade DLLs. What the verdict
  says: accepted with "This is a different printing of the game (version X, media id Y): its code is the same as
  the supported one"; refused as "A title update is already applied" (version != base: TU4 output is 0.0.4.12 on
  base 0.0.0.12), "a title update patch" (module flags), "damaged" (the image fails its own header's page
  digests) or "is not the supported code: it differs in: section .text (code) at 0x823f0000-0x823fffff ..."
  (any section, code or data, with the address ranges of the 64 KB pieces). `run_build` runs the same check,
  so a headless build cannot start from other code. Second bug in the same log: the player chose
  `<name>.data\Data0000`; `resolve_package` now maps a `DataNNNN` file (or the `.data` folder) to the package file
  beside the folder. A relative `--title-update` path to `--build` is made absolute (the title update tool ran in
  another directory).
  Tests: `python3 -m unittest launcher/test_xex.py` (header-rewritten copies of the supported files as
  0.0.0.9/10/11/12 are accepted; the TU4 output is refused as a title update; one changed byte in `.text`,
  `.data` or a facade's code is refused naming the section; a changed string in the padding after `.edata` is
  accepted; a damaged file is called damaged; `DataNNNN` / `.data` / package resolve to the package). Proof of
  the build: `--build --title-update <TU4 zip>` from a scratch root whose `default.xex` and facades have the
  header of 0.0.0.12 / media `4000D145`: accepted (message above), TU4 applied (370 of 371 page groups, the
  same page as in docs/dlc.md; the facades 61/61, 54/54), codegen and 359 ninja steps (5.2 min at `-j4`, 2.0 GB
  peak), then the game booted with a scratch profile and ran: first-run video setup, intro videos, new game, in
  the open world driving. Not verified, as no such file was at hand: the real 0.0.0.9, 0.0.0.10 and 0.0.0.12 executables;
  they are accepted only if their images are as the table above says (the fingerprint decides on the
  player's machine, and a different one is refused with section and addresses, which the player can post).
  A test trap met on the way: with `media/` and the other game folders as **symbolic links** into another copy,
  the game stops at "Disc Read Error" (`NtCreateFile ... game:\media\aiopenworld.zip -> 0xc000000f`, the file
  exists): the runtime does not follow a symlinked directory under `game:`. It is not the header (the same
  executables booted with the folders hard-linked); the launcher copies real files, so players are not affected,
  but a game folder made of symlinks (to point the launcher at a game elsewhere) does not work.
* **#26 (Setup clipped at 1040 px).** Reproduced (Segoe UI). Cause: the Windows-only "I accept Microsoft's license
  terms ..." `QCheckBox`, whose text cannot wrap, forced the scroll page's minimum width past the window.
  `pages.WrappedCheck` (a check box plus a wrapping label) fixes it; verified by screenshot at the default size.
* **#28 (`\usr\bin\true codegen`).** d33e2d4 verified: a Windows build from the CI installer of main
  (0.0.0-dev.649f0bc, frozen, Visual Studio absent so xwin) with TU4 + 23 DLC + the ISO as game files, through the
  window (Build, license ticked, `-j3`, low priority): LLVM 23.1.3 / CMake 4.4.4 / ninja downloaded, xwin splat,
  TU4 applied, 357 ninja steps, `forza.exe` linked and staged: **563 s wall (9.4 min, with the downloads) and a
  1.5 GB peak** (summed working set of the process tree, now measured on Windows through Toolhelp +
  `GetProcessMemoryInfo`; `builder._windows_tree_memory`). On disk: build 0.9 GB (after link), toolchain + xwin 1.7 GB,
  game 7.6 GB. The old preflight said "8 GB installed, 5 GB free (need about 8 GB)" as an error on this 8 GB VM and asked
  "Try anyway?"; the build did not run out of memory (fixed in #32: that machine now gets a green row).
* **Play without a GPU.** `forza.exe` starts, logs `Failed to create a Vulkan instance: ErrorIncompatibleDriver`
  and exits with code 1 within a second. The launcher said "The game is running." forever: `Play.show_message(warn)`
  was hidden by the `refresh()` that follows. Now (9949778) the Play page keeps a red box: exit code, a sentence
  for the usual cause (no Vulkan GPU), the last three error lines of `forza.log`, the log path.
* **Installer test in CI.** The `installer-test` job (windows-2022, `test_installer.ps1`) ran on the CI-built
  installer: every check passed, but the step exited 1 (the last native command, `--dry-run`, returned 1);
  `exit 0` added. `release.yml` run 38641731120 validates it (artifacts only).

### Round 4: #35 (`manifest 'build.ninja' still dirty after 100 tries`), found and fixed

Players on v0.1.2: `[0/100] Re-running CMake...` repeating, then ninja gives up. Mechanism (reproduced on Linux with
cmake 4.4.3 / ninja 1.13.2: `touch -d '+2 hours' inc.cmake` on a file the project includes): build.ninja's
regeneration rule depends on every CMake file the configure read; if one has a modification time newer than the
clock, CMake regenerates build.ninja (its mtime is "now", still older than that file), ninja checks again, and
after 100 rounds reports "perhaps system time is not set". Which file, on Windows:
**the installer stamped the installed files with the future.** Inno Setup stores the build machine's *local* time
by default (CI runners are UTC) and applies it as the installing machine's local time (no `TimeStampsInUTC`): a
file built at 21:40 UTC is installed "at 21:40 local", 3 h ahead for UTC-3, 7 h ahead for PDT. The toolchain file
`<install>\project\tools\cmake\windows-native-clang-cl.cmake` is passed to CMake straight from the install
folder (`-DCMAKE_TOOLCHAIN_FILE`) and is a regeneration input, so for the first hours after a release (until the
player's clock passes the stamp) every build on every machine west of UTC looped. Confirmed in the VM with the
correct clock (VM is PDT; it had been running 7 h fast, which also showed that an old clock era leaves future
times behind: `ninja -d explain -n` named, in turn, `rexglue_project.cmake`, `generated/default/dll_targets.cmake`
and CMake's own `Platform/WindowsPaths.cmake`, all stamped while the clock was wrong): with the v0.1.3 code and the
installed project, `ninja explain: output build.ninja older than most recent input
...\project\tools\cmake\windows-native-clang-cl.cmake (+6.5 h)`, both on a fresh tree and on one left by a
v0.1.1 build that stopped at 24% (#28). A stale state from an older launcher is not by itself a cause, but it
keeps whatever stamps it had.

Fixes (ad12ef1, c3078f6):

* `builder.sync_toolchain_file`: the Windows toolchain file is copied into `build/toolchain/` (a new file, so the
  installer's time never reaches CMake); `clamp_future_times` sets every file or folder with a modification time
  in the future to now, under the project copy, the SDK, the downloaded toolchain, xwin and that copy, before each
  configure (a wrong clock, an archive, an installer; it logs "N files had a modification time in the future").
* `configure_stamp`: the CMake build tree (`out/build/<os>`) records the launcher version, platform, SDK pin and
  overlay, compilers (path and source), and title-update choice in `.forza-configure-stamp`; a tree with another
  stamp, or none (made by an older launcher), is deleted and configured from scratch, not resumed.
* If ninja still prints "still dirty after 100 tries", `run_build` deletes the CMake build tree, clamps again and
  configures once more; if that fails too the message says to check the date, time and time zone, to press
  Resume build, and to delete the build folder if it comes back.
* `installer.iss`: `TimeStampsInUTC=yes`, so installed files keep real times.
* `forza-recomp/CMakeLists.txt`: `codegen_skipped.cmd` is written only when its content differs (a `file(WRITE)`
  every configure is a new time on a file the custom commands depend on; it is not a regeneration input and was not
  the cause).
* `launcher/test_build_tree.py` (clamp, stamp/reset), `python3 -m unittest launcher/test_build_tree.py`.

Verified in the Windows 11 VM (source tree of the launcher, `--build --base --jobs 4`, the installed project with its
installer times, correct clock unless noted; each ends with `forza.exe` linked and staged, 4.2-4.4 min, 1.7 GB peak):
(c) current code, fresh tree: no loop (control with the v0.1.3 code: loop, explained above); (b) clock set 5 h back
(downloaded toolchain, SDK, xwin all in the future): the control loops, the new code logs "14754 files had a
modification time in the future" and builds; (a) a v0.1.1 build stopped at 24% (the `\usr\bin\true` failure), then the new
code: "The earlier build was made by another version ... configuring from scratch", builds; the retry: with the
clamp disabled and a future-stamped toolchain copy, ninja loops, the launcher deletes the build tree, clamps and the
second attempt finishes; with the clamp disabled for good the message above is the result of the second failure.
Linux headless build (`--build --base --jobs 4`, scratch root, system clang 23.1.2 / cmake 4.4.3 / ninja 1.13.2): 350
steps, 5.6 min, 1.8 GB peak; a second run resumes the same tree (no "configuring from scratch"). The CI installer of
`release.yml` run 38654797202 (all jobs green, artifacts only): installed in the VM (PDT) at 23:39 UTC, the toolchain
file's modification time is 23:33:54 UTC, the build's own time (v0.1.3's installer gave +6.5 h); `TimeStampsInUTC`
works.

Still not tested: a Visual Studio Build Tools machine (the `vswhere`/`vcvars64.bat` path), Windows 10, a real GPU,
long user names or a data folder with spaces/non-ASCII characters, antivirus interfering.

The `installer-test` job in `release.yml` (it runs the script on the CI-built installer):

```yaml
  installer-test:
    needs: launcher-windows
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with: { name: launcher-windows, path: installer }
      - shell: pwsh
        run: |
          $exe = (Get-ChildItem installer\*Setup*.exe | Select-Object -First 1).FullName
          launcher\packaging\test_installer.ps1 -Setup $exe
```

### Round 5: #37 (Visual Studio 2017 picked) and #38 (access denied under Documents, Discussion #31)

**#37.** A player's log said `MSVC: Visual Studio at ...\2017\Professional`; every file then failed (`struct char8_t;` in
`<experimental/filesystem>`, `'bit' file not found`, `'span' file not found`): clang-cl compiles against the MSVC STL of
that install, 14.16, which has none of the C++23 the SDK headers and the project use. `vswhere -latest` had returned it
because no version was required. What the code needs (grep of the SDK's `include/` and `src/`): `<expected>` with
`std::expected`/`std::unexpected` (SDK headers, 5 + 3 uses), `std::byteswap`, `std::unreachable`, `std::popcount`,
`std::rotl`, `<span>`, `<bit>`, `std::jthread`/`stop_token`, `std::endian`; the project itself uses no further library
feature. The newest of these is `<expected>`: MSVC STL of Visual Studio 2022 **17.3 = toolset 14.33**. Rule
(`build_env.find_msvc`, constants `MIN_VS = (17, 3)`, `MIN_TOOLSET = (14, 33)`):

* `vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json -utf8` (without `-latest`)
  lists every installation, newest first; one older than 17.3 is skipped by its version without running anything;
* for the others `vcvars64.bat` is run and its environment checked by what is really installed, not by a number:
  `VCToolsVersion` >= 14.33, `<expected>`, `<span>`, `<bit>` and `windows.h` found on `INCLUDE` (a Visual Studio 2022
  can hold only the 2019 toolset 14.29, or no Windows SDK);
* the first that passes is used; otherwise the downloaded runtime (xwin, the license tick) is used as before.

The log says it, for example `MSVC: found ... 2022 ... ; ... 2017 ...`, `MSVC: skipping Visual Studio Professional 2017
15.9.36 at ...: too old: its C++ standard library (MSVC 14.1x) lacks <expected> ...`, `MSVC: using Visual Studio Build Tools
2022 17.14.5 at ... (toolset 14.44.35207)` or `MSVC: no installed Visual Studio is new enough; the Microsoft C++ runtime
and Windows SDK are downloaded instead`. The Setup page asks for the license unless this same selection finds a usable
install. Untested: a real Visual Studio 2022 (the VM has none; an old STL under clang 23 may have its own problems below
17.8, the version floor is the library's, not a tested one).
Tests: `python3 -m unittest launcher/test_msvc.py` (fake vswhere JSON: 2017/2019 skipped, newest compatible chosen, a
2022 with only toolset 14.29 skipped, missing header/SDK reasons, nothing installed). In the VM (a fake `vswhere.exe` from
C# printing JSON, fake `vcvars64.bat` files, real `cmd` run): 2017 alone, 2017 + 2022 with toolset 14.44, 2017 + 2022 with
14.29 give the logs above and the right choice; the real `vswhere.exe` 3.1.7 accepts the flags (`[]` without Visual Studio).

**#38.** `ninja: error: rebuilding 'build.ninja': GetFileAttributesEx(C:/Users/<name>/Documents/forza/sdk/.../SDL3Config.cmake):
GetLastError() = 5`: Windows denied access to the data folder, `Documents\forza` (v0.1.x default). Documents is synced by
OneDrive (Known Folder Move) on many machines and is a Controlled folder access / antivirus-guarded folder; a file ninja
cannot even stat keeps `build.ninja` dirty. Changes (`forza_launcher/datafolder.py`, `paths.py`, `builder.py`, Setup page):

* **Default.** Windows: `%LOCALAPPDATA%\ForzaReImagined` (not synced, not a protected folder). An install that already
  lives in `Documents\forza` (it has content and the new folder does not exist) keeps using it, so nothing changes under
  a working player. Linux and Flatpak are untouched (`~/.local/share/forza`, the Flatpak data dir).
* **Choose the folder (Discussion #31, issue #40).** Setup has a card "Where the game is built" (path, Change folder, Open);
  Settings > Advanced "Game data folder" is the same button and flow (it was a free text field that changed the folder
  with no check). A chosen folder that is not empty and is not one of ours gets a `ForzaReImagined`
  subfolder. It is checked: writable (a file is created, read and deleted), free space (about 15 GB, 18 with a toolchain
  download, less when the game is already extracted), OneDrive. Errors block, warnings ask.
* **Move, never delete.** Changing the folder offers to copy saves, profiles, DLC, shader cache, log and the built game
  (`runtime/`, `build/state.json`) and, separately, the extracted game files (7 GB; or leave them in the old folder, then
  `game_root` points there). The toolchain, SDK, downloads, xwin and build tree are not copied: they are made again (a
  blocked folder is exactly where they fail). The old folder is left as it is and the message says so. Copy is resumable
  (same-size files skipped) and cancellable.
* **Offer at start (Windows).** Once, when the folder in use is the v0.1.x `Documents\forza` and it is risky (inside OneDrive,
  not writable, or the last build was refused): a dialog proposes the new default; "Keep this folder" is remembered
  (`advanced.data_root_asked`).
* **OneDrive detection** (`datafolder.in_onedrive`): the folder is under a path in `OneDrive*` environment variables
  (OneDrive, OneDriveConsumer, OneDriveCommercial), a path component is named `OneDrive...`, or a folder on the way has the
  cloud attributes (recall on data access/open, offline, pinned, unpinned). Shown under the folder, logged at the start of a
  build ("Warning: ...").
* **Access denied.** Before a Windows build the folder is probed; during it, a failing command whose output has
  `GetLastError() = 5`, `Access is denied` or `Permission denied`, or a Python `PermissionError`, ends in `FolderBlocked` with
  a message naming OneDrive, antivirus and Controlled folder access, the folder, Windows' own words, and "choose another folder";
  the window offers to open the folder chooser, and remembers the folder as blocked (`advanced.blocked_root`, shown red on
  the card and used by the start offer).
* Bug met on the way: `accept_ms_license` and `build_jobs` were not keys of the config defaults, so `_merge` dropped them
  when the file was loaded (the license tick and job count did not survive a restart); they are keys now.
* Not changed (not the launcher's files): `tools/dist/play.bat`, README and the bug-report template still say
  `Documents\forza` for the zip's `play.bat`.

Tests: `python3 -m unittest launcher/test_datafolder.py` (OneDrive by environment, name and attribute; the access-denied
patterns; the message; probe; free space; defaults for a fresh and an existing install; folder choice; copy keeps the old
folder, skips rebuildable parts, resumes, cancels).

Verified in the Windows 11 VM (clock set right first: it was 7 h fast again; source tree of the launcher with the CI
installer's overlay, `--build --base --jobs 4 --accept-microsoft-license`, game files from the ISO, no `--root`):

* **Default folder.** `--extract` of the ISO went to `%LOCALAPPDATA%\ForzaReImagined\game` (7.0 GB, a few minutes); the build
  that followed ran there: LLVM/CMake/ninja downloaded, with the fake Visual Studio 2017 and a fake 2022 holding toolset 14.29
  both skipped (log lines above) and xwin used, 349 ninja steps, **7.1 min, 1.7 GB peak**, `forza.exe` linked and staged
  (so the xwin fallback still builds). The attribute check had a bug found here: `GetFileAttributesW` returns
  INVALID_FILE_ATTRIBUTES as -1 through ctypes' default `int`, which made every folder that did not exist yet look
  cloud-synced; fixed (unsigned result type). Then real folders: `%OneDrive%\forza` is flagged by the environment, a folder
  with `attrib +P` by its attribute, `Documents\forza` and the default are not.
* **Chosen folder.** The copy that "Change folder" runs (saves, runtime, state, and the 7.1 GB of game files, 6 s on the VM's
  disk), the config switched to `C:\fh2`, the old folder untouched, then a headless build with no `--root` built in `C:\fh2`
  (6.4 min, `runtime: C:\fh2\runtime`). The window flow (both questions, the check, the switch) was run against a Linux
  offscreen window with the dialogs answered by script; not clicked by hand on Windows.
* **Access denied.** Denying read on `SDL3Config.cmake` alone (`icacls /deny tester:(RA)`) did not make ninja fail (Windows
  grants attribute reads through the folder), so the player's exact ninja line was not reproduced by the ACL; the real
  `builder.run` was fed that line from `cmd` and ends in `FolderBlocked` with the message above. Denying read on the SDK's
  `cmake` folder (`icacls /deny tester:(OI)(CI)(R)`) made CMake say only "ReXGlue SDK not found": the build log shows
  that, and now `datafolder.read_problem` (listing sdk, toolchain, xwin and build, opening a few hundred files) finds the
  denied path and the build ends with the blocked-folder message naming `...\cmake\SDL3Config.cmake: Permission denied`.
  A data folder with write access denied stops before any work ("Cannot write to ..."). Trap: that deny ACE also removes
  READ_CONTROL, so even the owner's `icacls /remove` fails ("Access is denied"); `takeown /r` and `icacls /grant` repair it.
  Not tested: real OneDrive, Controlled folder access, antivirus.

`release.yml` (workflow_dispatch, artifacts only) run 38666192753 on the final commit: all jobs green (sdk-linux, sdk-windows,
launcher-linux, flatpak, launcher-windows, installer-test; publish skipped). No tag pushed, no release made. Left: a real
Visual Studio 2022 and a machine with OneDrive / Controlled folder access to confirm both fixes outside the VM.
