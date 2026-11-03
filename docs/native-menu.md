# Original FH1 native menus

The native pause menu uses repository-authored pages, layout, navigation and
input handling. It requires the player's normal FH1 TU4 installation. No FH2
executable, extracted scene, texture, translation or generated include is used
by this build. The older FH2 experiment is documented in
[horizon2-menu-port.md](horizon2-menu-port.md) for reference. Its translated
runtime adapters and build configuration have been retired.

## Current scope and validation

The pause feature adds Home, Cars, Social and Settings, plus contextual Activity
and Horizon pages for native race, PR Stunt, Rally, 1000 Club and other available
actions. See [the stock context/action checklist](pause-menu-contexts.md) for
native routing and outstanding runtime validation. Car Tuning
comes from the standalone implementation merged in #363; this feature adds only
the parent visibility bridge needed to enter and return from the Cars tile.
Bucket List is excluded. The feature remains opt-in.

### Native quit dialog

In offline free roam, **Y Quit** opens FH1's native confirmation box. **A Quit to
main menu** and **B Cancel** keep their original buttons; **X Quit to desktop**
adds a game-window exit. Cancel restores the originating tab and selection.
Online Leave Session and End Free Roam keep their existing dialogs and behavior.

Desktop exit follows the original quit-to-title transition and waits for the
matching native profile serialization, successful prior I/O, synchronous content
flush and newly bound title screen. It then requests game-window closure on the
host UI thread. Save errors, incomplete I/O, allocation failures, cancellation
and changed ownership prevent desktop closure. It does not close the launcher
or inject another profile save. Native quit fixtures cover reference ownership,
allocation fallback, one original forward per result and save-completion gates.
Runtime qualification of these new choices is pending; earlier four-tab images
and quit traces establish only the revisions and behaviors attributed below.

### Review corrections - 2026-10-25

The launcher keeps the unmodified FH1 TU4 files in `runtime/update`. Standalone
car tuning uses `runtime/update-native`, prepared by the tuning-only tool without
changing the stock pause flow. The full pause menu uses `runtime/update-menu`,
which contains its donors, tuning aliases and offline reload flow. Both prepared
outputs are built independently from the pristine update.

Launch selects the full overlay only when effective `forza_native_pause` is on;
otherwise tuning selects its independent overlay, or both options off select
stock. Environment and command-line overrides retain runtime precedence, and an
explicit update-root argument remains authoritative. Missing full-menu resources
fall back to stock rather than mounting tuning-only resources as a full menu.
Turning options off and restarting restores the corresponding path without a
rebuild. Failed preparation removes only that preparer's stale output, keeping
the stock and other independent overlay intact. Build/runtime directory links
are allowed; links on individual publication targets are rejected.

The Cars page remains selectable while signed in and when individual actions
are unavailable. Its current-car panel is informational; each action retains
its own availability check. MY CARS and CHANGE CAR require known Colorado free
roam, virtual networking off and no current Live session. Capabilities,
activation and the native garage Open helper re-check this gate; a stock World
Map action cannot unlock it. Disabled car-change/tuning tiles identify the
offline requirement when networking is enabled.
Tuning uses the same offline gate. Changing cars records host-side intent scoped
to the pause owner. Only native Resume consumes it and sets the guest reload
byte. Other transitions, confirmed exits and teardown clear it. Resume also
re-checks network/session state and rejects a mismatched owner or stale guest flag.

Presence advertising again requires the stock USES_PRESENCE flag. A peer-network
flag alone does not advertise a joinable session; join-in-progress-disabled and
presence-join-disabled sessions are excluded. Session membership display remains
separate. Travel availability logs only when its state changes. The duplicated
macOS 720p entry is removed. macOS TU4 CI now compiles the feature-enabled host;
the focused workflow runs live-connection and native-car lifecycle tests and
tracks their headers, launcher staging, and the LZX decoder.

Focused synthetic checks and TU0/TU4 source compilation pass. The Windows test
environment cannot create directory symlinks/junctions, so those cases are
reported as skipped locally and remain enabled in Linux CI. The car adapter
harness compiles the actual Open helper and selection hooks with mocked native
calls, covering online state changes, deferred one-shot Resume, Map/Photo/
Horizon World/leave/settings cleanup, owner mismatch, no-op selection and stale
flags. These checks do not establish rendering or actual-game lifecycle behavior
for this revision; the production runtime evidence below predates these fixes.

### Current-main integration and production build - 2026-10-25

Integrated main `048b553` in signed merge `79c0669`, then main `a8288c0` in signed
merge `4f3b61e`. The social-session conflict retains main's host-migration QoS
metadata and cleanup together with the pause feature's member list and presence
refresh after guest-session deletion. Main's Co-op Festival playlist 20 and
invited-session detection remain combined.

All 90 authored Windows runtime sources compile without private instrumentation.
The matching SDK is built from pinned commit
`bd833a2ab69d305f99ddf57418fc38ffec8af93d` with all 121 current patches.
Independent reapplication produced tree `a8490b588cffa456e89e80811deed41de2ea9cda`,
matching all 1,369 source blobs; all 1,265 installed files matched their manifest.
The ordinary production link passes with untouched generated objects. Linked
object, library, source, resource and runtime hashes are recorded outside Git.
There are no private keyboard, audio, input or renderer changes in either final
validation host.

The earlier keyboard-import failure was caused by the private build script's
object order: it appended newer authored objects after `rexruntime.lib`, so the
linker loaded the SDK's keyboard imports before seeing the application overrides.
Putting all authored objects before import libraries, as the normal CMake target
does, resolves it. No symbol bridge, generated-object rewrite, weaker linker
option or production keyboard change is required. The prior baseline-link-blocker
assessment is superseded.

The later main merge adds Windows PerMonitorV2 DPI awareness. Its new `main.cpp`
and native resource were compiled and linked with the same SDK; the embedded
RT_MANIFEST type 24, ID 1, language 1033 and icon group were inspected in the
actual executable. The other 89 authored source files are byte-identical to the
complete validation run below. No pause, tuning, map or child-flow code changed.

Focused checks pass: 19 CTests, 62 launcher controls/menu tests, seven TU4 syntax
checks, 26 co-op grant tests, 53 campaign/ledger/lobby server tests, two DPI
manifest tests, 16 DLC integrity tests and six HD-pack tests. All four workflows
passed on `79c0669`; final-head checks are visible on #235. A transient Windows
`test_cleanenv` shared-subprocess mock count failure passed on one failed-job
rerun without changing an assertion or production source.

### Fresh actual runtime evidence - 2026-10-25

The pristine production host at `79c0669` passed an offline copied-profile
1280x720 Vulkan run, producing 22 actual game captures, each visually inspected:

- Home, Cars, Social and Settings, with shoulder navigation.
- All six Settings children: Difficulty, Graphics, Audio, Controls, HUD and Video;
  Back restored the originating Settings tile after each child.
- Cars to the merged Tune Car workspace, a READY native performance simulation,
  then Back restoring Cars with Tune Car selected. Stock/draft values matched;
  no tuning changes were applied.
- Full native map entry and return to Home; resume restored the gameplay HUD;
  reopening pause started on Home.

After integrating DPI-aware main, a separate pristine production run at
`4f3b61e` captured and visually verified all four tabs again at actual 1280x720.
These are the final-code four-tab images. The 22 child-flow captures remain
attributed to `79c0669`, rather than being represented as repeated after the DPI
merge. Fresh images are attached to #235 outside Git and identified by code head.

Both owned games closed normally with exit code 0. Elevated host process checks
found no remaining Forza process or owned child. All 12 seed-save file hashes and
108 historical source-save inventories remained unchanged. The production SDK's
audio-output fallback was available; no private silent-audio driver or keyboard
import wiring was used. No settings/tuning changes, public-server actions, friend
requests, messages or invitations were applied in these offline runs.

Online-session actions, paid Garage/Autoshow travel, actual car replacement,
extended lifetime, quit-to-title/reload, Linux runtime and other GPUs were not
repeated in this pass. The feature remains opt-in; those are broader validation
limits, not claims made by the fresh captures.

### Historical runtime evidence

The 2026-10-24 captures at `3e12c71` are historical. Their private screenshot
harness used keyboard symbol wiring and a silent-audio fixture. They do not
qualify the current production link and are kept distinct from the two pristine
2026-10-25 runs above.

## Structure

### Slanted map and tab boundaries

The Home map and pause tab papers use the same 7-degree edges as the cards.
Their mesh transforms are rebuilt at the final native transform boundary, scoped
to nine registered map/tab instances. Rebuilding from authored properties avoids
accumulating shear across animation frames. Registrations clear before their
presentations are freed. Other native UI instances retain their original path.
Tab labels use projected anchors without shearing the font. The map renders a
small horizontal margin and counter-transforms its texture coordinates, keeping
terrain, labels and player markers upright inside its slanted boundary. The car
photo is not registered for this transform.

A copied-profile offline 1280x720 run verified Home, Cars, Social and Settings,
shoulder navigation, full-map entry/return, settled tuning simulation and return
with Tune Car selected, and resume/reopen. No tuning changes were applied. The
new hook and affected sources pass seven TU4 syntax checks, 19 focused CTests,
62 launcher tests and two base/disabled-build stub tests. The private runtime
used private transform/keyboard aliases and a silent audio fixture at that early
checkpoint. The later uninstrumented production build and runtime validation in
the 2026-10-25 sections above supersede that limitation. Screenshots are attached
to the PR, outside the repo.

The pause strip has Home, Cars, Social and Settings. The four native
tab presentations scroll to keep the selected logical tab visible. Social uses a larger
Friends activity panel, Horizon World/Create Game actions, direct Requests and
Chat entries, and Session & Connection. Friends shows server-supplied counts and
up to three online friends with their actual presence; selecting it opens the
existing friends editor on Friends. Requests opens Requests directly, with
incoming/sent counts; Chat shows a read-only unread count and latest-line
preview before opening Chat. Reading the preview never marks messages read or
sends anything. These editor entries use the input thread's queued request
path, including its invite/keyboard ownership, rather than mutating overlay
state from the guest UI thread.

Offline, Friends explains the available features and opens setup help. Requests
and Chat also lead to help instead of becoming dead controls. Online play's
native gates still disable Horizon World/Create Game, with readable panels and
an explicit connection requirement. Waiting for server contact has its own
guidance; no disconnected friend counts or chat previews are displayed. The
connection strip shows actual server-room population only while connected.
An active local party/game session is identified separately; room population
is not presented as a count of players in that session. Disabled online-entry
tiles explain when Colorado single-player free roam is required.

Session & Connection keeps Social selected and shows actual XSession membership,
five players per page, with local/host labels and car/class/PI when published.
Selecting a longer player list advances to the next five. Server-room population
is separate from this list. Join a Friend opens the friends editor; a joinable
friend's menu offers Join Game and requires the existing local join confirmation
before broadcasting the native invite-accepted notification. The target is
revalidated for connection, session identity, privacy, blocked status and available
slots. Cancelling an explicit join does not mute that friend's later invites.
Invite Friends uses the existing picker with the current session's information.
Leave Session uses FH1's native exit confirmation. Both require a current session.
For a free-roam host this is explicitly named End Free Roam and explains that it
ends the round for everyone. Native Horizon World can start its next round after
returning to the lobby. Cancelling result 2/18 restores the composed page without
waiting for a parent rebind; confirming releases composed resources before the
native activity transition.

Connection & Help is available inside this view. It shows the player,
connection state and title-service endpoint when supplied by the server, plus
the launcher's actual Online server choices and troubleshooting instructions.
It does not pretend to reconnect or modify launcher settings from inside the
game. Career Statistics is a secondary action here, alongside Back to Social;
native Back restores the previous root selection. Statistics returns to the
help view and its selected action. Shoulder navigation leaves the help view and
continues through the normal tabs. Native custom lobbies retain their own invites.

Car presence is an optional versioned `FH1C` suffix after the existing slot/QoS
envelope. The sender preserves all native QoS bytes and omits car details when the
1024-byte server limit leaves insufficient space. Older clients remain compatible;
the session view says car details are unavailable when a peer supplies none.
Car model/name, class and PI come from the bound native car presentation, rather
than an estimated performance calculation. XSessionJoin/Leave maintain the member
XUID list independently of the server roster.

Gameplay sessions without USES_PRESENCE also count for the membership display:
FH1 deletes its presence party when entering a world or race. These peer sessions
are not advertised as joinable without USES_PRESENCE. Presence advertising also
rejects join-in-progress-disabled sessions. Unique member XUIDs supply the
displayed population (native slot counters can receive duplicate joins). The loaded
PlayerBar car supplies metadata, rather than the garage's cached single-player car.
Metadata is refreshed while pause is open and scoped to the current session ID;
a newly entered session omits old car details until its presentation is read.
Native exit uses action 15 for a host's End Free Roam, or model-gated action 17
with the bound `quit` callback at pause+0x1F8. Action 14 is Retire and must never
be labelled Leave Session: it leaves a guest waiting in the running round.
Action 5 opens the Message Center and is never used to exit.

## Session validation

Two private local clients
exercised Friends join confirmation/cancel/accept, a populated custom lobby,
race start and joining a running Horizon World. The session page showed both
players' actual loaded cars with class/PI and distinct host/local labels.
At 1280x720, guest Quit confirmation/cancel/accept returned from the running
World to a native Party Lobby; the populated invite picker opened/cancelled
without sending invitations. Testing Party Lobby Exit Multiplayer exposed stale
title-layout nodes in the graphics-menu hook. The root-list rebuild now discards
those references instead of restoring a destroyed scene. A fresh 720p regression
run entered multiplayer, accepted the native Party exit, and returned to playable
single-player with its normal HUD. The World-to-lobby and corrected lobby-to-game
transitions were verified in separate copied-save runs.

Presence joins to a running Horizon World bypass the local matchmaking search.
The world drop-in classifier therefore accepts an unknown playlist only when the
native Find Game mode and free-roam preset match; a known other playlist or preset
still fails closed. This preserves Jump In and any-class handling for invite joins.

## Native transitions

Online Free Roam enters the original multiplayer flow and opens Find Game's
playlist picker, where Horizon World can be selected. Create Game enters the
same flow and opens Create Custom Game's native lobby, including Setup Game,
car selection and invites. Both entry tiles require a connected server, the
original pause map gate, a multiplayer callback and Colorado single-player
free-roam activity. They do not start another multiplayer transition from an
online world or a race. Availability is rechecked when activated.

The pause's original `multiplayer` callback at +0x1E4 initializes the game mode,
session and resources; opening CPartyLobby directly skips that initialization
and is unsafe. A one-shot intent waits for the party's normal tick, native
host/choice gates and a published session to remain ready for 300 ms, then
dispatches the native Find Game/Create Custom Game choice. It expires after
30 seconds, clears on disconnect or Back, and is consumed before dispatch so
returning to the party does not reopen the child. Original lobby navigation,
confirmation dialogs, playlist contents and online game options remain native.
Exiting multiplayer follows FH1's normal mode transition rather than returning
directly to an overlaid single-player pause screen.

Home renders the original `fullscreen` map profile into a separate native map
record at the card's resolution. Terrain, roads, labels and the player's
heading marker come from FH1. Record0 and its gameplay camera remain untouched;
record1 remains available for the normal full-map screen. The preview follows
the native compressed-heap setup, waits for readiness, and submits texture I/O
under allocation group4. Using the current world's allocation group instead
caused the previous loading crash. Hide releases the preview record and profile
references, restores allocation routes, and releases any heap setup it owns.

At 1280x720, copied-profile checks covered terrain/roads/player-marker rendering,
Home to full map and back, Social to native Statistics and back, and resume with
the normal HUD restored. A loopback-only Forza Live server with an empty friends
database verified Friends entry, shoulder navigation to Requests and Add friend,
Chat entry, and closing back to Social. No requests or chat messages were sent.
The existing Friends editor uses the SDK overlay, which is not included in the
native game-render-target screenshots; its open/tab/close log events supplied
the integration evidence.

A later 1280x720 copied-profile pass on a loopback-only server verified Social's
counts/appearance, Free Roam directly opening the native playlist picker,
selecting Horizon World and reaching a playable online world, and Create Game
opening the native custom lobby. Setup Game displayed the original privacy,
permissions, game type, track and rules controls; Back returned to the lobby
and then the party without repeating the shortcut. No public server, friend
request, chat message or external invite was used. This was a single-client
entry-flow check; joining a friend's game and multi-client racing were not
retested. Model checks cover losing the server while an online tile is focused,
keeping Social selected, connection/help focus, secondary Statistics and all
root action navigation.

The Social usability pass was captured at 1280x720 both offline and against a
private loopback server with an empty friends list. Offline help, native
Statistics and return, root selection restoration, and shoulder navigation
were exercised. Stopping the owned local server verified the waiting/error
guidance, cleared stale social summaries and disabled online entry. Connected
logs verify direct Friends, Requests and Chat tab
entry/close; native render-target captures omit the SDK overlay itself. No
requests, messages or invites were sent. The test runtime used a private silent
audio backend because Windows had no default playback device; neither this
backend nor any SDK changes ship in the PR. Populated friend lists, nonzero
unread chat and multi-client/session controls still need a further live pass.

The combined WIP includes the native tuning workspace described in
[car-tuning.md](car-tuning.md). Cars exposes `car_tuning` through its native
availability gate, alongside Change Car, Garage and Autoshow. Tuning returns
through the pause page visibility API, preserving the selected Cars action.
Both consumers use the same TU4 widget helper; its optional donor layer offset
defaults to the existing pause behavior. Pause tabs now use the reusable
`fh1_menu_components::TabBar` renderer while the pause controller retains input
and capability ownership.

Pause papers, labels and decorative icons share a 7-degree shear with horizontal
top and bottom edges. The car photo retains its authored image scale and UVs;
its parent is zoomed equally on both axes. FH1 owns its animated vertical scale,
so independently changing the child X/Y values stretched the earlier preview.
The native thumbnail material and the player's cached paint remain in use. The
map preview is an upright rectangle inscribed inside the padded slanted panel;
its terrain, labels and player heading remain undistorted. The 720p composition fits within the
existing safe area. FH1's layer nodes are render boundaries, so the shared
matrix is composed onto their drawable children rather than the layer itself;
camera and light nodes retain their authored transforms.

The four tab donors now form a scrolling window over up to 64 logical pages.
Shoulder navigation keeps the current window until selection crosses its edge,
then reveals the next tab. Edge arrows indicate hidden pages; wraparound brings
the selected first or last page into view. The normal menu still has four pages.
A private seven-page staged build at 1280x720 verified both hidden-edge hints,
scrolling in both directions, and wraparound. The temporary extra pages are not
part of the shipped menu or its scene assets. Geometry and viewport tests cover
map containment, stable windows and bounded page capacity.

Neutral cards tint FH1's original `Horizon/DirtMasks/Panel01` painted paper
dark charcoal, with white headings and pale secondary text. Focused actions
use festival pink and white text. The rough painted edges remain visible;
no copied game textures are added to the repo. Decorative action icons use
faded white silhouettes for contrast against both charcoal and pink.

The ten action icons use Font Awesome Free 6.7.2 solid SVGs (car-side, wrench,
warehouse, store, user-group, earth-americas, gamepad, user-plus, comments and plug), CC BY 4.0. Their attribution and license are retained in
`tools/native_menu/icons` and shipped alongside the staged runtime. Native
256x256 XDS surfaces are staged from that directory; SVG sources are retained.
Developers can rebuild them with `python tools/native_menu_icons.py` (Pillow and
resvg-py are required only for rebuilding). The launcher ships prebuilt artwork;
players need no image conversion dependency. Archive preflight checks all 36
scene members plus these ten textures before activating the menu.

The Cars composition uses a larger full-width photo with five specification
columns underneath. Both menus bind real controller icons through the shared
native footer model API. The resource manifest now prepares the pause and tuning
aliases together; both runtime options remain independently disabled by default
in the launcher. Named setups support Save, Load, Rename, Delete, Export and
Import. Community is an unavailable Coming soon page with no action dispatch.

A combined 1280x720 Vulkan copied-profile session verified Home, the selectable
Tune Car tile, entry into the actual tuning workspace, in-game naming and a
saved Setup 1 entry in Load, file export, the unavailable Community page, Back
to Cars with Tune Car focused, and resume. All screenshots came from that same
combined binary and copied profile. No tuning values were applied in this pass.
The tuning worker's separate tests cover file import and persistence/readback;
those are not claimed as repeated combined-session checks. Header class/PI
binding uses the current loaded car's native badge controller; its manufacturer
logo comes from the database's standard IconPath resource. The legacy light
filename is not used because the corresponding texture is absent in FH1.
Both tuning and library use the same header identity. Platform, fallback and extended lifetime checks remain
limited as detailed below.

The shared tab bar uses compact spacing and angled FH1 paper. Pause labels
follow the composition's shear; tuning labels retain their own layout.
Selection commits immediately while the active paper moves for220ms;
interrupted motion continues from the displayed location, wrapping snaps, and
large elapsed times settle immediately. This visual state belongs to the shared
component and is preserved through tuning/library view snapshots. The pause
header retains its native player/car/credits bindings with separate paper
containers and no continuous header background. Only the dim backdrop bleeds outside the safe area, sized from the output
viewport so the donor texture's transparent perimeter is outside the frame.

The shared UI polish was checked at1280x720 in a copied offline profile:
Home/Cars header and slanted tabs, Tune Car entry, tuning/library manufacturer
logo and current B437 badge for the Lancer, named Setup1 under Load, shoulder
switches, Back and resume. Native screenshot sequences capture the active-paper
motion on both pause and library tabs; no synthetic interpolation was added.
The current class is cached until its native value changes, avoiding redundant
texture/text rebinding every second. This pass did not Apply any tuning values.

| Layer | Responsibility | Source |
| --- | --- | --- |
| Model/controller | Stable page and tile IDs, spatial navigation, per-page focus, action intent, input repeat and child suspension | `forza-recomp/src/platform/menu_model.h` |
| Default pages | Home, Cars and Settings, semantic actions, 1280×720 design rectangles and live availability | `forza-recomp/src/platform/native_pause_pages.h` |
| Native rendering | FH1 presentation lifetime, isolated visual components, text/material properties and native data bindings | `forza-recomp/src/platform/fh1_menu_widgets.h`, `forza-recomp/src/hooks/native_pause_scene.cpp` |
| Asset availability | Bounded archive-directory preflight before native scene requests | `forza-recomp/src/platform/native_pause_assets.h` |
| Game actions | Native FH1 child screens, offline garage, festival travel and recovery | `forza-recomp/src/platform/fh1_pause_actions.h` and the `fh1_*`/`offline_cars` adapters |
| Resource preparation | Verified FH1 donor aliases and the existing offline car reload flow | `tools/native_menu_scene.py`, `tools/native_menu/fh1_donors.json` |

The controller has no game, renderer or SDK dependencies. A page owns a vector
of tiles; each tile has an ID, label, rectangle, action, visibility and enabled
state. An empty action creates an informational panel. Updating these values
preserves valid selection by ID. Directional navigation uses the rectangles,
so it works with uneven tiles without a table of hand-coded neighbours.

The native adapter rechecks FH1 action gates before dispatch. Opening a child
suspends the controller and hides the parent presentation; returning requires
held buttons to be released before they can activate another action. FH1 keeps
ownership of its action model, child screens and callbacks.

## Preparing and building

TU4 builds compile the native implementation by default. Set
`FORZA_NATIVE_PAUSE=OFF` to exclude it; base-game builds keep the stock pause
menu. Remove old `FORZA_FH2_NAVIGATION_DIR` and `FORZA_FH2_GRID_EVENTS_DIR`
cache values when reusing a build directory. They are diagnosed before SDK
discovery or code generation.

Enable **Advanced → Menu → Native pause menu (experimental)** in the launcher,
then restart the game. The equivalent runtime switch is
`--forza_native_pause=true`. It remains opt-in while the new native composition
is undergoing broader platform and lifecycle validation.

LB/RB switch between Home, Cars, Social and Settings; directional input selects an
available tile, and A opens it. B/Start resume driving. Left-stick click requests
the native Reset Car Position action when it is available. Returning from a
child restores the previous page and focused tile, even when FH1 recreates the
pause screen. Resuming and then opening pause again starts on Home.

The launcher prepares resources from its freshly applied FH1 TU4 update during
installation. It publishes unmodified files to `runtime/update`, standalone tuning to
`runtime/update-native`, and the full pause overlay to `runtime/update-menu`.
Only the corresponding enabled option selects each prepared output on launch. Unsupported or modified input removes stale
prepared output, leaves stock available, and logs why preparation was unavailable.
Each overlay is published only after its copy is complete. Original input files
remain unchanged.

Before requesting donors, the runtime checks the mounted update archive for all
36 required, nonempty scene members. Missing, incomplete or unsupported archives
keep the stock pause immediately. This is necessary because FH1's native scene
request can stall on a missing resource before a UI timeout can run. The check
reads ZIP directory metadata and accepts native method-21 payloads without
decoding them; the preparation tool remains responsible for integrity checks.

For a direct developer build:

```sh
python tools/native_menu_scene.py /path/to/original/fh1-tu4 --output /path/to/new/private/menu-update
```

Point `--update_data_root` at that generated directory. The tool verifies the
supported original TU4 archive SHA-256 and executable title/version. It copies
complete FH1 scene triplets into distinct resource aliases without decoding or
changing their geometry. It also patches the owner's FH1 pause XML for the
offline car reload path, using the repository's Python LZX decoder. All output
contains game-derived data and stays in ignored private directories.

This is resource preparation, not a general scene-file serializer. Rendering
uses isolated native components from those donor presentations. No serialized
game scene is committed to the repository.

## Extending the menus

1. Add or modify `Page`/`Tile` descriptors. Keep IDs stable when changing their
   order or labels so focus survives dynamic refreshes.
2. Register an action and its availability/dispatch behavior in the native
   adapter. Layout must never override the game's permission checks.
3. Bind additional data separately from layout. The map adapter accepts an
   image rectangle; the car card keeps native names, units and performance data.
4. Validate the renderer's capacity before transferring ownership from stock
   controls. The first FH1 backend has six tile slots and four tab slots; the
   controller itself supports arbitrary counts. Expanding this backend requires
   additional donor aliases, matching slot/tab definitions, and memory/render
   validation. An unsupported layout must retain the stock menu.

New renderers can consume the same controller without carrying over FH1 guest
addresses. Changes to input repeat, focus memory or navigation therefore remain
independent of scene composition and game actions.

## Validation

The SDK-free model tests exercise arbitrary page counts, uneven geometry,
availability changes, spatial reachability of all default pages, stable focus,
child return, opposing inputs and repeat timing. Preparation tests use authored
synthetic data, including compressed XML; they cover deterministic output,
archive offsets, source preservation and malformed input. Configuration and
disabled-stub tests require neither game files nor translated includes.
Archive preflight tests cover every missing donor member, malformed directories,
empty/encrypted entries, native method-21 metadata and ZIP comments without game
files or a decompressor.

```sh
python -m unittest discover -s tools -p 'test_native*.py' -v
python -m unittest discover -s launcher -p 'test_native_menu*.py' -v
cmake -S forza-recomp/tests/menu-model -B build/menu-model
cmake --build build/menu-model --parallel 2
ctest --test-dir build/menu-model --output-on-failure
cmake -S forza-recomp/tests/pause-stubs -B build/pause-stubs
cmake --build build/pause-stubs --parallel 2
ctest --test-dir build/pause-stubs --output-on-failure
```

The initial private preparation audit preserved 917 original archive entries
byte-for-byte, intentionally replaced only the active `pause.xml` entry, and
verified all 36 alias payloads against their FH1 donors. All three executable
copies matched the source.

On 2026-10-22, a private Windows TU4 build compiled and linked all twelve affected
runtime translation units against the actual SDK with no FH2 inputs or flags.
Base-game and disabled-TU4 hook compilation also passed. The game ran offline on
copied saves at 1280×720, Vulkan, native render scale, on an NVIDIA GeForce
RTX 5060 Ti with driver 617.14. Visual checks covered all three pages, native
current-car data, player/credits/progress, map preview, and the corrected
Difficulty title. World Map, Change Car, Graphics, Audio and Difficulty opened
successfully; child returns, resume, and reopening pause behaved as described
above. No profile settings were changed in these menu checks.

The final binary also passed two archive-availability cases: opt-in with pristine
TU4 assets immediately retained usable stock pause and resumed; the staged
archive activated Home/Cars and resumed. The preflight was added after the first
missing-file run exposed a stall inside FH1's loader before the UI timeout could
execute. The corrected Difficulty heading was verified in a separate run.

The short session included three pause openings and several child screens. Whole
process private memory ranged from 2.97 to 2.99 GB, including screenshot and
resource warming; these observations do not establish the absence of leaks or a
stable long-session memory cost. Linux, other GPUs, races, Rally, multiplayer,
paid travel and extended repeated-open testing remain unvalidated.

Integration with main `04b7d31` preserved the current controls/remapping and
gamertag behavior. Fourteen SDK-free model/archive CTest cases, 59 focused
launcher/menu/bindings/gamertag tests and syntax checks of four enabled TU4 menu
hooks passed after that merge. The game captures above predate this integration;
no post-merge game session or full rebuilt SDK/game validation was performed.

### Current-main conflict and packaging repair (2026-10-22)

Integrated main `7f9a716` in an isolated checkout of combined checkpoint `3711096`.
The resolution retains both benchmark and pause/tuning includes, all catalog and
menu packaging tools, and current-main ASCII gamertag filtering alongside menu
setting-length/coercion behavior. Active menu/tuning worktrees were not changed.

Fixed Linux release tar naming: the native-resource loop had overwritten the
package name with `native_menu/fh1_donors.json`, so `--tar` attempted to write a
nonexistent nested archive path. A synthetic Linux-package regression checks the
release filename and all nested preparation resources.

Windows validation after integration: 618 launcher tests passed with 37 skips;
20 distribution/install tests and 23 resource-staging tests passed. All 14 menu
model/archive, three tuning/component/setup and two disc/TU4 disabled-hook CTests
passed. The actual graphics, pause, scene, map and two tuning hook sources compile
with TU4/native-pause enabled against the installed Windows SDK headers. No game
or GPU session, full game link, shared SDK installation or gameplay claim was made
in this conflict-repair pass. The draft/runtime-opt-in gates above remain.

The first CI rerun exposed a Windows-only test-path mismatch: the runner's TEMP
path used `RUNNER~1`, while the builder deliberately resolved the workspace to its
long spelling. The preparation mock now compares resolved paths, retaining its
workspace/TU containment assertions. A lexical-alias regression exercises that
normalization on every host; the full Windows suite was rerun successfully.

### Main integration after frame pacing (2026-10-23)

Merged main through `da8386c` in an isolated checkout. The networking conflict
retains the menu's receipt-only welcome timestamp and the suspend/resume and
rejoining state. Windows validation: 688 launcher tests (37 skips), 24 native-menu
tool tests, 20 menu/archive/disabled-hook/title/benchmark CTests, and the strict
component test passed. Networking, friends, graphics, pause, scene and map sources
compile for both disc and TU4 (native pause enabled for TU4). No post-merge game/GPU
run or full game link was performed; the existing draft/runtime-opt-in gates remain.

Social uses the same faded silhouette family as Cars on charcoal painted panels.
Friends uses a separate native quad, preserving the current-car manufacturer logo.
The connection strip displays a complete plug, pink while connected and dimmed
while offline; it reserves text padding and does not imply signal strength.

Social artwork validation (2026-10-23): native compilation/link, 24 staging tests,
17 model/archive/tuning CTests, 62 launcher checks and six TU4 syntax checks pass.
A private copied-save 1280x720 session verified offline Social, connection help,
Cars manufacturer-logo preservation and return to Social. It then hit a native
allocator write fault on resume (guest 0x00000005, LR 0x8242306C).
The following resume-lifetime fix addresses this failure. Source save hashes
were unchanged.

### Resume resource lifetime (2026-10-23)

Resume now releases the additional pause presentations before invoking FH1's
native done callback. Previously it only hid them; their deferred teardown
could overlap gameplay-interface texture loading in the front-end GPU heap.
The recorded write to guest address 5 is the allocator's deliberate failure
trap. Refresh/check the native done callback before cleanup, preserve the
original screen handle through cleanup, and rebuild composition if native
resume rejects the transition. Native destruction can safely run afterward
because composition state has already been cleared.

A private diagnostic 1280x720 run measured front-end GPU heap use falling from
60,578,432 to 56,363,288 bytes before callback dispatch (about 4 MiB released),
with no allocation-failure counters. The preceding implementation without
Social artwork also completed the same navigation/resume sequence; this is
limited heap headroom during overlapping resource lifetimes, not evidence that
every earlier resume necessarily fails.

Final hooks without diagnostic modifications passed six copied-save, offline
1280x720 pause/resume cycles: Social via help and Cars; Home with native map;
Cars; Settings; Social; and Social with Start-button resume. Every cycle
captured resumed gameplay/HUD, and the menu reopened after the final cycle.
There were no guest access violations and source save hashes were unchanged.
Native compile/link, 17 CTests, 62 launcher checks and six TU4 syntax checks
pass. UI tests used the private silent backend because Windows had no default
audio endpoint; it and diagnostics are not committed. The ordinary private
runtime was restored and all owned test games closed. Online and extended
session validation remain outside this focused check; retain draft/opt-in gates.
