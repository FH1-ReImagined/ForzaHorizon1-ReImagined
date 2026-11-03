# FH2 Xbox 360 pause menu in FH1 TU4

This document describes the earlier private prototype. The current build uses
the [original FH1 native menu](native-menu.md), with repository-authored
navigation and FH1-only resource preparation. It does not build the translated
FH2 adapters or load their scenes. Their runtime code and configuration were
retired; use commit `86256a1661d4a94d183875958ba70999c1b79091` in a separate
checkout to reproduce this historical prototype. The research/preparation tools
remain available, but are not launcher or game-runtime dependencies.

The private scene build runs the owner's original FH2 Xbox 360 pause scene in
FH1 with Home, Cars and Settings. Extracted FH2 navigation, page-event and
button-state functions drive the controls; authored adapters bind FH1 models,
resources and native actions. This integrates selected original routines rather
than replacing FH1's screen manager. Original executables, archives, saves and
translated game instructions remain private and ignored.

## Current controls and pages

LB/RB navigate the three tabs and stop at their endpoints. Disabled pages follow
FH1's free-roam Map availability. Directional input selects available tiles;
A activates them. B/Start resume driving. Left-stick click requests Reset Car
Position from the footer on any tab when native recovery is available. Native
child screens own their input,
and existing held input must release before the parent consumes it again.

| Page | Actions |
| --- | --- |
| Home | World Map, Settings, My Cars, Festival Progress, Photo Mode, My Profile |
| Cars | Change Car, Go to Garage, Go to Autoshow |
| Settings | Difficulty, Graphics, Audio, Controls, HUD, Video |

Settings replaces the former Home Resume tile; B/Start remain resume shortcuts.
Difficulty remains in Settings. Reset Car Position is a global footer shortcut. Graphics opens the recomp panel. The other five categories use FH1's
original child screens; Video contains the native brightness/subtitle options.

Home and Settings use authored Grid_Menu_01. Home maps six logical tiles
to native slots 0, 1, 2, 4, 5, 6, hiding the old Reset slot 3. My Cars copies
Grid_Menu_04's native tall tile transforms, including labels, paper, underline
and shadows. Settings restores the captured original geometry. Cars uses
Grid_Menu_03: a 550×370 current-car panel on the left and three 360×112 action
tiles on the right. The informational panel is skipped by selection. Unused
buttons stay hidden. Layout
page events run only when geometry changes. Animated FOCUS/BLUR events restore
the current selection after native initialization/SHOW: immediate FOCUSED/BLURRED
leave prior first-button focus active in retained presentations. Fresh return
selection is normalized after destination availability refresh, preventing an
empty cache from resetting Garage/Autoshow to Change Car.

## Home map preview

World Map is a Home tile; there is no separate Map tab. Selecting it still
opens FH1's full native map, and Back restores Home. The preview samples the
existing HUD map target and record0 viewport. The 360×172 tile reserves a 42px heading above a 360×130 map body, keeping
roads clear of the title. The body uses an aspect-preserving crop around the
native player position and retains FH1's transparent road-map style.
It acquires no full-screen profile and owns no gameplay camera.

The crop reads the existing local-player map entity, flattens world Y to zero
as native icon symbolizer 82DA77FC does, and projects with the current map-camera
matrix after native material-pass preparation. The render-phase point is
published atomically for the next UI crop; the earlier Tick camera has a
different temporary projection. It clamps to the rendered viewport while keeping the native marker and
padding inside the map body. The preview expands tan(FOV/2) threefold at the
original HUD camera distance and angle. This shows three times
the world span and naturally shrinks native markers to one third of their
previous screen size (about 21px for the player in the 720p tile). The scoped
render override restores FOV and all cached camera matrices after the
pass. Icon profiles, sizes, offsets, geometry ownership and entity positions
remain native.

The private H_MAP alias supplies the textured Tip.Bar.Bar/Material1504 quad
and caption. Its original untextured custom-render rectangle is hidden after
placement. Borrowed controller dispatch is disabled after initialization;
the native ownership map remains intact. The caption uses Home's title font.

The paused HUD releases its icon atlas resources. The helper retains the
existing record0 minimap profile, invokes the original minimap load callback
only if unloaded, and waits for native readiness and both player-arrow texture
references. On hiding it unloads only a load that the preview acquired, then
releases its profile reference. No full-screen terrain textures are loaded.

A scoped native material-pass hook validates the published record against the
current service and rebuilds its map-camera matrices and transient geometry
immediately before phase0 draws it. Preview camera overrides last only for that pass. Other phases and inactive calls pass
through unchanged. Hiding clears the atomic request before tab changes,
resume or native child entry; destruction releases the donor before its parent.
Original Home labels remain visible until the target and resources are ready.

Prepare fresh private outputs from the progress overlay:

```powershell
python tools/fh1_pause_map_preview.py PROGRESS_OVERLAY --output MAP_OVERLAY
python tools/fh2_cars_labels.py MAP_OVERLAY --output FINAL_OVERLAY --unxmem UNXMEM_EXE
```

The map aliases preserve all original compressed payloads. The second tool
changes only identified Cars label anchors/tracks in the imported pause BGF.
The local preview launcher uses `scene-update-fh1-map-cars2`.

## Festival Progress

Home's progress tile shows overall game completion from the exact native
Statistics General getter: 8269E150(USER0 profile, 1), whose f1 return is in
percentage units. Profile lookup mirrors 8284CC88: 825EC700 -> facade.v28 ->
player.v58. Game readiness, database/state pointers, player/profile and finite
results are checked before use. This includes the native additional completion
counts and is independent of wristband XP, already shown in the header.

The pink ring uses native completion / 100. The centered white label displays
one decimal place (the copied fixture matches Statistics at 1.4%); 100% has no
redundant decimal. Longer percentage labels use smaller text to fit the ring.
The caption reads GAME COMPLETION / VIEW STATISTICS. Values refresh on binding
and child return, then every 300 visible Home frames because the getter queries
career data.

The native FH2 RoadtripProgress component retains its circular meshes, textures
and original PROGRESS animation. Its own CProgressBar controller selects manual
mode and seeks the normalized FH1 completion fraction. A scoped update hook
prevents the donor's unrelated roadtrip variable from replacing that value.
Existing native Button_Text2 supplies the completion caption; the ring has
its original centered number field. Other pages clear reused text fields.

Selecting Festival Progress opens FH1's native Statistics screen (0x14A,
H_STATISTICS), using the original CMyProfileMenu route at 82789718 with choice 2.
General, Discovery, Records, Races and Skills retain their native navigation.
Back returns to the same Festival Progress tile. FH1's General category reports
the same overall game completion shown by the tile.

The private ring tool reparents the component to the earlier shared Grid01
Group. Both BGF and BSG parents must precede the component during sequential
loading; attaching it to the later Festival Progress button drops the subtree.
The old Infographics_Set1 slide override for this component is also changed
from timeoffset 4000/endtime 4988 to 0/INT_MAX. The Home parent timeline
continues beyond 4988 during navigation, so its lifetime must cover the full
page rather than the donor's short placement interval. Tab visibility controls
the ring; its own normalized PROGRESS curves remain unchanged.
Only the private archive copy is changed; original game files remain intact.
The progress overlay is the input to the private Home map-preview overlay.

The final smaa-completion1-720 run verifies overall completion at 1.4%,
matching native Statistics General (raw getter 1.3513514%). The wristband/XP
caption is removed. The selected tile stays unchanged after ten seconds;
Statistics opens and Back returns to the same tile. Cars and Settings hide the
ring. All seven captures are 1280x720 at native scene scale 1. The build and
whitespace check pass. The unchanged private ring overlay previously passed
three asset-tool tests and an audit preserving all 958 unrelated compressed
payloads and all three executables byte-for-byte.

```powershell
python tools/fh2_pause_progress_ring.py STYLED_OVERLAY --output PROGRESS_OVERLAY
```

## Cars action text

The two direct Grid03 action tiles contain native focus/blur position tracks
for their titles and captions. Those tracks restore the original taller-card
anchors after runtime styling. `fh2_cars_labels.py` changes only the four
identified label records and their matching X/Y tracks in a new private
overlay: X is -162, title Y is 38 and caption Y is -38. Native timing, focus
behavior, other axes and unrelated payloads remain unchanged. Its strict
record/value checks reject a different source scene before creating output.

## Current-car panel

Cars reuses the private FH1 garage donor's CAR_STATS subtree. It reads the
current owned-car model without changing the garage selection, binds the
localized manufacturer/model names through the native PlayerBar query, and
uses the saved owned-car thumbnail with the native stock fallback. The native
specifications model supplies power, torque, weight, weight distribution and
displacement. No invented performance ratings or placeholder badges are shown.
The subtree and PlayerBar retain their original controller ownership; only
borrowed dispatch lists are filtered. Temporary native references are released,
and the panel refreshes on binding/child return and periodically while visible.

The current-car card uses the full width: the enlarged native photo and name
paper sit on the left, with five specification rows alongside on the right.
The old footer backing is hidden so it does not leave a partial strip below the
repositioned rows. Row anchors are narrowed and vertically spaced
without stretching the native fonts. The photo, labels and values retain their
original artwork, rotation, controller ownership and data bindings. A native
1280x720 capture confirms the photo/name padding, all five rows and their values
fit inside the current-car card.

## Change Car

Change Car opens TU4 CMyCars screen 0xAF. Base-game screen 0xA1 is different:
TU4 0xA1 is CMarketplaceCarSelect and incorrectly introduces an online gate.
The standalone adapter scopes native cancel/selection callback changes to its
own instance, loads the local garage and restores the originating Cars tile.

The private --offline-cars overlay adds the owner's original garage reload flow
so selecting a different car updates the physical world vehicle on resume.
Only the native garage-reload flag triggers this flow. A copied-save test
swapped the Mitsubishi Lancer for a Volkswagen Corrado VR6, verified C400 in the
native header and observed the Corrado driving in the world.

## Garage and Autoshow travel

fh1_festival_travel resolves workshop_02 / autoshow_01 through native activity
lookup, finds the matching available CMapEntityActivity and uses its authored
icon point to position the native map cursor. It waits for FH1's map update to
produce the destination transform and fee before invoking original confirmation
827C81C0 or native affordability failure 827C8470.

The game retains unlock checks, affordability, payment, loading, teleport,
orientation and restoration. The shortcut does not fabricate coordinates or
activate locked activities. Cancelling leaves the native map focused on the
destination; closing it restores the originating Cars tile. Accepting arrives
at the original entrance and the normal X prompt enters the location. The
request clears before a modal and on map exit; unrelated map use stays native.

Copied-save 720p runs verified both confirmations, cancellation returns, actual
world arrivals and X entry into Dak's Garage's upgrade menu. Both native fees
were 0 Cr in this fixture. Paid/insufficient-credit paths remain delegated to
original functions and were not exercised by those runs. Paintshop is a
separate FH1 destination; the Garage shortcut is for upgrades.

## Reset Car Position

The native footer rebuilds its A/B model and appends HELP_BUTTON glyph 14
(left-stick click) with Reset Car Position, only while native recovery is
available. Availability refreshes while pause is visible and on child return.
The existing pressed-edge/held-input rules apply across all three tabs.

fh1_car_recovery resolves USER0 using 825EC700 -> facade virtual +28 -> player
virtual +44, matching original CHandOfGodControl. It checks the original gates
in 825280E0, including NoHandOfGodReset, ownership and active/queued recovery,
then sets only the one-shot car+3C34 request and resumes through the native
pause callback. It never calls Restart Mission/Event or resets all cars.

Original recovery performs fade, safe-road placement, upright orientation and
collision/physics restoration. Scoped observational hooks report acceptance and
execution without changing native behavior. A copied-save test displaced the
car, observed state 0 to 1 and original reset 825217E8 executing, then verified
the car stopped upright on the road and pause reopened normally.
The footer-specific `smaa-footer-reset2-720` run verified the joined header,
six-tile Home layout, restored Settings geometry, and the LS prompt on Cars.
A timed LS click while Cars was open queued recovery; native state changed
0 to 1 and original position/orientation reset executed two seconds later.
The car was upright at 0 mph, and Home reopened with one focused tile and
one reset prompt. All five captures are 1280 by 720.

## Native FH1 header and footer

The --garage-header stage aliases original TU4 108_C_BUY_CARSELECT bgf/bsg/fbf
members as FH1_PAUSE_PLAYER_BAR. Their compressed payloads are unchanged. A
separate resource name avoids altering the cached real Garage presentation.
The runtime retains the native resource, constructs a native scene owner and
registers its renderer. Only retained header controllers update; normal native
presentation ownership/destruction remains intact.

The left two rows preserve original wristband/progress and car/class artwork,
fonts and digit sizes. The original BottomRow credits text, paper and stacker
move to the right second row. The donor's unused Timer/TimerGroup/Time text and
native white paper provide the right first row for the active username. Their
unrelated siblings/controllers remain excluded. Serialized runtime node hashes,
not FBF mesh labels, identify the reused paper node. Its original timer texture
had a 0.926 horizontal UV offset; clearing that offset through the native image
matrix updater exposes the complete paper strip while preserving the Y flip.
Native text box width/height are normalized units, unlike pixel font sizes.

The username paper is 260 by 36 pixels at 720p. A 220-pixel text interior
uses native normalized box dimensions and fitting, with an 18px maximum font,
so wide names shrink while keeping space inside the torn edges. The timer
label originally had texttype 0, which bypasses native fitting; the username
sets bounded texttype 1, shrink mode 2 and single-line word wrapping off.
Credits sit immediately below, touching the username paper while retaining
internal text padding. The launcher limits typed/pasted gamertags to 15 characters
and applies the same cap when loading settings and exporting game configuration.
Empty names retain the existing identity fallback.
The 720p wide-name probe (`smaa-padding3-720`) displays all fifteen W glyphs
on both initial open and reopen. Native metrics report width 218.285 pixels
and fitted size 13, leaving about 18 pixels of visible paper on each side.
The normal fifteen-letter ABC probe (`smaa-padding4-normal-720`) fits at
size 17 with width 191.534 pixels and about 32 pixels of horizontal padding.
The native fitter respects both horizontal and vertical padding.

The active gamertag comes through hooked XamUserGetName for user 0. The imported
FH2 bar contributes one native text field for a single bottom-right ONLINE
status line aligned with footer prompts. Confirmed connectivity requires a
validated server welcome within 20 seconds, using a receipt-only steady-clock
timestamp; configuration or stale registration is insufficient. Host roles are
omitted because the native host flag also exists in a single-player world.
Offline hides the status without moving username or credits.

PositionIn, OpacityIn and ScaleIn target the donor presentation and dirty queue.
Constant transforms may omit scalar properties, so verified instance-cache
fallbacks dirty the native transform and renderer rather than guessing fields.
Resource registration drains only donor work. Native event flush and renderer
preparation occur before frame-begin clears dirty lists. Drawing stays in the
original UI graphics pass immediately after pause, above its blur.

Native child transitions and resume release the donor through normal scene and
resource APIs. Returning or reopening loads a fresh donor; no header remains
while driving or over native child screens. A 720p run verified original B437
and C400 badges around a physical car swap.

## FH1 tile theme

The standalone tool creates a new private overlay:

```powershell
python tools/fh1_pause_tile_theme.py EXISTING_OVERLAY FH1_HORIZON_ZIP --output NEW_OVERLAY
```

It aliases original FH1 DirtMasks/Panel01.xds over the imported
ui/textures/ui4/anthem/pausemenu_whtbox.xds without decoding/repainting/reencoding.
Native XMem bytes, compression, all other assets and input archives remain
unchanged. Existing mesh/UVs provide painted brush edges on fills and pink
selection marks. White text uses warm charcoal with burgundy focus fill.

Opaque Locked_Tile backings are hidden on every adapted page; otherwise their
grey gradient rectangles show through the brush edges. Photo/Profile's opaque
images are hidden and their existing White_BG backings explicitly enabled,
because those authored defaults are zero opacity. This gives all tiles the
same paper treatment. The native footer gradient remains unchanged.


Selected tiles use the pink paper background alone. Orange_HL bottom lines
are hidden after native focus updates, preventing selection animations from
restoring them on Home, Cars or Settings. The four smaa-no-underline1-720
captures verify initial Home, selected Progress, Cars and selected Controls
without the bottom line, retaining the pink selection background and gauge.

Tiles use common 22px horizon_e titles and 11px horizon_b navigation captions,
with 18-unit inset anchors and normalized fitting boxes. Profile and Festival
Progress have intentional two-line titles. The progress caption uses 11px text beside the circular gauge. Individual native ShadowCorner/ShadowStraight leaves are hidden;
hiding their parent alone allowed focus events to restore the raised frame.

FH2's focus zoom is a perspective-depth animation, not parent scale. Run the
depth tool after the texture theme to create a second fresh private overlay:

```powershell
python tools/fh2_pause_tile_depth.py THEMED_OVERLAY --output STYLED_OVERLAY
```

It validates 90 paired 100ms position.z curves on 45 specific Grid 01/03 objects
and replaces only their plus/minus-20 focus-depth values with their authored
baseline. IDs, times, opacity, native FOCUS/BLUR events and entry/exit movements
remain intact. The actual archive audit preserved all 959 other compressed
entries and all executable bytes. The styled overlay is the input to the private progress-ring tool.

Tabs reuse the FH1 italic title font at 20px, warm charcoal fills and pink
selection. Hidden divider/notification visuals retain their native ownership.
The native stacker measures the new text and owns horizontal placement. Static
FBF labels can lack a live horzalign property, so CenterLabels uses the verified
native mode-0/mode-2 background records to center them without accumulating
width offsets. The RB anchor uses a 24-unit gap, removing the old extra 12-unit
right gutter. Structural tests cover those modes, binding validation, ownership
and repeated layout updates.
The stacker's text-placement flag remains enabled: disabling it skips the
text setter entirely. Authored anchors compensate the actual alignment queried
through the native text binding (left, center or right).

The six valid `smaa-style2-720` captures verify Home, selected tall My Cars,
Map, Cars, selected Controls and reopened Home with stable tile bounds and
wrapped titles. The final `smaa-style4-tabs-final-720` Home/Settings captures
verify centered italic tabs: all four native text/background X coordinates
match locally and in world space, with measured 17px horizontal padding.
Both use the new styled overlay at 1280x720/native scale 1.

## Private preparation and validation

### Main integration, 2026-10-22

The prototype is integrated with main `3390ef0`. The integration pass ran no
game, GPU benchmark, real save or public server. Existing screenshots and native
720p gameplay checks below predate this integration (`c7165e8` and earlier).

Current CPU validation covers 32 affected-hook compilations: base disabled,
TU4 disabled, TU4 navigation-only and TU4 scene-enabled. The five private native
navigation/grid/layout/connection CTest cases pass, as do two SDK-free base/TU4
disabled-API executables. Ten configure-only cases verify default-off behavior,
valid private-input combinations, missing translations, and early rejection
before SDK lookup or codegen. The 38 synthetic preparation tests and 67 focused
launcher graphics/settings/exit/relaunch tests pass.

The complete launcher suite ran 498 tests with 34 skips. Its unchanged symlink
test has one failure and one subtest error on this Windows host without symlink
privileges (WinError 1314); the affected fetch implementation and test match main.
The new `pause-menu-tests` workflow runs synthetic preparation/configuration
checks and both disabled-API executables without SDK or game inputs.

Post-integration runtime validation remains required: stock pause/title Graphics
and Back; an opted-in binary with stock scene assets; actual-scene map, Garage
and Graphics child return; resume; and quit-to-title/reload with donor teardown
before title-movie loading. Main's title/movie, HDR and driving-gate code is
retained, but a static review and hook compilation do not establish those flows.
Full release builds and Linux/macOS gameplay remain unvalidated; the draft's
race/rally, split-screen, multiplayer, profile-state and paid-travel limits remain.

Prepare the owner's FH2 executable/assets and extract navigation/events using
prepare_horizon2_ui.py, extract_horizon2_navigation.py and
extract_horizon2_grid_events.py. Stage with stage_horizon2_pause_scene.py,
--offline-cars --fh1-header --garage-header and a local --unxmem decoder.
Apply the tile-theme tool, then the tile-depth tool and progress-ring tool, creating fresh overlays.
Keep all prepared data,
extracted instructions, overlays and copied profiles under ignored paths.

Visual validation is strictly 1280x720 at native scene scale 1. Perfect this
baseline before trying another output resolution. Current native checks cover
three-tab navigation, Home preview/full-map/Back/resume transitions, current-car
thumbnail/specifications and all three Cars focus positions. Earlier checks cover all six Settings children, native Map/Garage, selected-tile return,
physical car changes, travel accept/cancel, recovery, resume/reopen and online
receipt expiry. This does not establish race/rally/split-screen/multiplayer
coverage, every possible profile, or paid travel behavior.

The latest 720p captures confirm the preview heading stays clear of roads, the
complete native player marker fits with padding, and the current-car card and
action labels remain inside their panels. A seven-capture navigation check
covered each Cars action focus, Settings, full Map and Back; a later lifecycle
check also confirmed the driving HUD after resume.

The FOV-only zoom follow-up passed six 720p captures: Home, Cars, native full
Map, Back, resumed driving HUD and reopened Home. The native player arrow is
visible at the smaller scale, and normal full-map/HUD scale restores.

Synthetic asset checks:

```powershell
python tools/test_horizon2_ui.py
python tools/test_horizon2_grid_events.py
python tools/test_offline_cars_flow.py
python tools/test_fh1_pause_header.py
python tools/test_fh2_pause_tabs.py
python tools/test_fh1_pause_tile_theme.py
python tools/test_fh2_pause_tile_depth.py
python tools/test_fh2_pause_progress_ring.py
python tools/test_fh1_pause_map_preview.py
python tools/test_fh2_cars_labels.py
```

Original navigation/events/focus and tab-layout C++ harnesses exercise their
adapters independently of gameplay. The live-connection CPU harness checks
receipt freshness, expiry, retry behavior and recovery. Asset checks verify
read-only sources, codec/CRC preservation, payload offsets and scoped aliases.
Only authored adapters, tools, tests and documentation belong in the repository;
original game data and derived translations must not be redistributed.

## Other Horizon menu references

FH2 Xbox 360's original scene supplies the tab strip, page layouts, brush-backed
cards and native controller bindings used here. Its authored Grid 04 includes a
tall narrow My Cars tile; using that geometry fills the former Reset tile space
without stretching text or adding a duplicate destination.

[FH3 pause screenshot](https://medium.com/parliament-perspective/learn-better-ux-ui-from-video-games-b26c5aa645d5)
shows top-level category tabs, a grid of destinations, player/car/credits in the
header, and left-stick Reset Car Position in the footer beside Select/Back/Help.
[Microsoft's FH4 pause screenshot](https://learn.microsoft.com/ja-jp/xbox/images/gaming-accessibility/forza-home-diabled.png)
likewise keeps Select, Back and left-stick Reset Car Position together below the
main tiles. This supports treating recovery as a global action rather than a
Home destination. The adaptation keeps FH1's native thumbstick glyph and footer
stacker, uses the existing held-input release rules, and hides recovery when
FH1's original gates reject it. No newer-game textures or fonts are needed.
