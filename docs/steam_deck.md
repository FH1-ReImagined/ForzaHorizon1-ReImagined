# Steam Deck

How to install and set up the game on a Steam Deck, and what to expect (#140, #45). We have no Deck:
what is marked *untested* was checked on a desktop PC only (below); reports from Deck owners are welcome
in [#45](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/45).

## Install: the Flatpak

On SteamOS use the Flatpak launcher. The `.tar.gz` launcher can't build the game there: SteamOS has no
C++ headers, and the launcher says so (#135). The Flatpak brings its own compiler.

1. Switch to **Desktop Mode** (Steam button, **Power**, **Switch to Desktop**).
2. Download `ForzaHorizon1-ReImagined-x86_64.flatpak` from the
   [latest release](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/releases/latest).
3. Install it. Either open the file in Dolphin (Discover opens it; choose **Install**), or in Konsole:

   ```sh
   flatpak install --user ~/Downloads/ForzaHorizon1-ReImagined-x86_64.flatpak
   ```

   It pulls the freedesktop SDK runtime from Flathub. Then install the compiler extension once (a
   `.flatpak` file does not bring it; without it the launcher downloads a 2 GB compiler instead and
   tells you so):

   ```sh
   flatpak install flathub org.freedesktop.Sdk.Extension.llvm22//26.08
   ```

4. Open Steam in Desktop Mode, **Games > Add a Non-Steam Game to My Library**, choose
   **Forza Horizon 1: ReImagined**, then **Add Selected Programs**. The launcher's
   **About > Add to Steam manually** button links to these instructions. Back in Game Mode the
   launcher is in the Library under **Non-Steam**.
5. Start it, then in the launcher: **Setup** (your game files, Title Update 4 and DLC), **Build** (10 to 15
   minutes on a Deck; it can sit near 18% for several minutes, #135: let it run), **Prebuild shaders**, **Play**.

The launcher updates itself (**About**, **Launcher updates**). Its data lives in
`~/.var/app/io.github.dyotson.ForzaHorizon1ReImagined/`.

## Add to Steam

The native Linux packaged launcher offers **About > Add to Steam** (#508). It adds one shortcut
with the project's bundled banner and logo to native Steam. It detects Steam account directories
and asks which account to use when there is more than one. Sign in to Steam once first so its account
directory exists. Run the launcher from the desktop menu after exiting Steam.

The launcher checks for Steam and its web helper on the host before preparing files and immediately
before replacing the account's config/shortcuts.vdf. It never stops or starts Steam. Steam does not
honor the launcher's lock, so keep Steam closed throughout the operation. If the check is unavailable,
denied or times out, setup refuses to write.

Existing shortcut records stay byte-for-byte intact. A matching name or launch command leaves the
existing shortcut and its artwork untouched, including a shortcut for an older installation: update
that path in Steam manually. Unknown binary field types, ambiguous records, redirected configuration
folders and app-ID collisions are refused. Artwork already present is preserved.

Before changing an existing file, setup saves a complete, uniquely named
shortcuts.vdf.forza-backup-ID beside it. It stages the replacement on the same filesystem, then
atomically replaces shortcuts.vdf. A failure before replacement preserves the original and rolls
back newly added artwork. Backups remain until you remove them. To restore one, exit Steam and the
launcher, save a copy of the current file, then copy the chosen backup over shortcuts.vdf.
If a crash leaves .forza-shortcut.lock, close both applications and inspect the files/backup before
removing that lock and retrying. An existing lock is never automatically removed.

**The launcher Flatpak uses manual setup.** Its isolated process list cannot establish whether
host Steam is closed. Missing PID files or D-Bus names do not establish this either. Reading host
processes through flatpak-spawn would require broad host-command permission, which the launcher
does not grant. Steam userdata also remains read-only. The button opens this guide without attempting
a write. See the [Flatpak command reference](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-spawn).

**Flatpak Steam is detected but automatic edits are refused.** Launching a host program from its
sandbox requires separate integration; writing a host executable path alone would produce a broken
shortcut. Use the Steam Flatpak's own supported manual setup for external applications. We do not
change Steam's permissions. Actual Flatpak Steam launch acceptance remains untested.

Manual setup for the launcher Flatpak with SteamOS's native Steam, source checkouts or nonstandard
installations: in Desktop Mode,
open Steam, **Games > Add a Non-Steam Game to My Library**, choose **Forza Horizon 1: ReImagined**,
then **Add Selected Programs**. Add custom artwork through Steam if desired. Do not force Proton
compatibility for this Linux launcher.

### Validation

Binary-file transactions and failures, account selection, process-check failures and Qt task shutdown
are tested with synthetic fixtures. Actual Steam library display, SteamOS/Deck launch, and hardware
clock reporting still need a Deck owner to verify; no real Steam files or
clock settings were changed during development.

## Manual GPU Clock

On a detected Deck, Play checks the APU's read-only power_dpm_force_performance_level and
pp_dpm_sclk files. When the driver reports manual, it shows a reminder to check
**... > Performance > Manual GPU Clock** if performance is low. Disable an unintended low limit
there, then return to Play. The launcher never changes clock settings.

A low sampled clock while idle does not establish a low configured limit. The warning depends on
manual mode, not the sampled MHz, and does not claim Steam selected that mode: another utility can
also select it. Missing files and other GPUs produce no warning. The check refreshes when Play
refreshes, at most once every five seconds. These meanings follow the kernel's
[AMDGPU power interfaces](https://docs.kernel.org/gpu/amdgpu/thermal.html#power-dpm-force-performance-level).

## Settings

The launcher detects a Deck (the DMI board name `Jupiter` or `Galileo` from Valve, or else the Deck's APU;
Steam's `SteamDeck=1` is only a hint) and starts a new install on the **Steam Deck** preset. An install that
already had settings gets a one-time offer at the top of **Settings**: **Use recommended** or **Keep mine**.
The preset (Settings, Display):

| Setting | Value | Why |
| --- | --- | --- |
| Frame rate | 30 | the Deck seldom holds more; see below for 40 |
| Render scale | 1 | the game's own 1280x720 |
| Upscaling filter | AMD FSR 1 | costs nothing on the Deck's screen (1:1, below); sharp when docked |
| Scene anti-aliasing (MSAA) | Off | the scene in one pass instead of three: about a fifth less CPU work in the GPU emulation, the Deck's limit ([perf.md](perf.md#one-pass-scene-scene_msaa-45-2026-10-23)) |
| Shadow detail, Distant texture detail, Car detail | Console | the game's own |
| 3D scene resolution | 720p | 540p saves GPU time only, and the Deck is limited by its CPU |
| Letterbox other aspect ratios | on | see Screen |

A warning shows next to a frame rate above 40 and a Scene anti-aliasing other than Off.

**Screen.** The game draws 16:9. On the Deck's 1280x800 screen it is shown pixel for pixel (1280x720) with a
40-pixel black bar above and below; Letterbox off stretches it 11% taller instead. The game's ultrawide mode
does nothing for a screen narrower than 16:9. Checked in gamescope (the Deck's compositor) at 1280x800.

**40 fps.** A frame rate the screen's refresh rate divides keeps every frame on screen equally long: 30 at
60 Hz, 40 at 40 Hz (LCD) or 80 Hz (OLED), 20 at 40 or 60 Hz. 30 on a 40 Hz screen judders. For 40, set the
refresh rate in Steam's **Performance** menu (**...** button) and **Frame rate** 40 in the launcher, or
**Match display**: on a Deck it picks 40 at 40, 80 or 120 Hz and 30 at 60 or 90 Hz. Leave Steam's own
**Framerate limit** off (the game caps itself) or at the same rate. The Deck holds 40 only in light scenes;
30 at 60 Hz is the steadier choice. Tested on a desktop PC in a headless gamescope of 1280x800 at 40 Hz: at
`frame_rate = 40`, 98-99% of the frames reached the screen one refresh (25 ms) apart in free roam, and Qt in
gamescope reports the refresh rate Match display reads (39.9 Hz at 40, 59.8 at 60, 80.0 at 80).

**Battery saver** (Settings, Display, Frame rate): *Auto on battery* starts the game on the lightest settings
(30 fps, render scale 1, AMD FSR 1, Scene anti-aliasing Off) whenever the Deck runs on its battery, and your
own settings come back at the next start on mains power, so a docked setup at a higher render scale stays
yours. A frame rate of 20 or 40, chosen for a 40 Hz screen, stays. The power source is read when the game
starts.

## What to expect

The Deck is limited by its CPU: the GPU emulation's command processor turns the Xbox 360's GPU commands into
Vulkan work on one thread, and on four Zen 2 cores that thread sets the frame rate
([perf.md](perf.md#steam-deck-and-4-core-cpus-the-built-in-benchmark-45-2026-10-23)). Reported (#45): 17 to
30 fps in free roam depending on the version and the place, 10 to 15 near traffic, about 8 in an 8-car race
(0.1.27), and 19.7 fps in the built-in benchmark (0.1.30); all before Scene anti-aliasing Off. Render scale 2
and 60 fps only make it slower. Cutscene audio that does not match the picture comes with the low frame rate (not reproduced here). The first session in a place stutters while shaders are built:
**Prebuild shaders** in Setup and a session or two on a warm cache help. For a report, `forza.log` has a
`GPU minute` line a minute and a `GPU slow frames` line for each slow stretch, with the cause; Steam's
performance overlay at level 4 shows the CPU and GPU load.

**Experimental switches (off by default, not yet tried on a Deck).** Two options of the GPU emulation aim at the thread the
frame waits for: `--vulkan_present_on_submission_thread=true` (the swap no longer waits for the frame's replay; +15 %
fps uncapped on four desktop cores with the Deck preset, nothing at the 30 fps cap) and `--linux_thread_cores=auto`
(Pinyon Shift's thread placement for a Deck's four cores with SMT; it did not help on four cores without SMT). To try
them, add them under Settings, Advanced, extra arguments, run the built-in benchmark with and without and report the
numbers in [#559](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/559); the measurements and what is
still untested are in [perf.md](perf.md#steam-deck-ideas-from-pinyon-shift-559-476-475-474).

## Controls

The Deck's controls are an Xbox pad for the game and the launcher (Steam Input; pick a **Gamepad** layout
for the non-Steam game if the launcher does not react). In the launcher: D-pad or left stick to move, A to
press, B back, L1/R1 for the pages; text fields need the keyboard (**Steam** + **X**). In the game the pad
drives player 1. The back buttons (L4, L5, R4, R5) reach the game only as the buttons Steam Input's layout
gives them; the launcher's **Controls** page can then move the game's actions between the pad's buttons
([playing.md](playing.md#remapping-a-controller-279)).

## Suspend

Press the power button to sleep; the game carries on when the Deck wakes:

- A sleep stops the clock the game runs on, so it sees no gap: races, timers and the frame rate carry on
  where they were.
- Signed in to Forza Live, the game signs in again at once and keeps its player id, so a session you host
  keeps its address. The server forgets a silent player after 30 s, so other players see you leave meanwhile,
  as on a console that lost its connection. `forza.log` has a `FORZA_RESUME` line for each sleep.
- Sound plays on the system's default output, which SDL follows when it changes (headphones, a dock).

Tested on a desktop PC by stopping the game (`kill -STOP`, then `-CONT`) for 90 s in free roam and 150 s in
the pause menu, signed in to a local server, in a 1280x800 gamescope: the same player id both times (the
server logged `back #1313`), the car drove on, and the frames were back at 33 ms within three frames.
Untested on a Deck: a real sleep (the GPU, Wi-Fi and the controls coming back), the sound after a sleep, and
SteamOS's own power and refresh-rate menus.
