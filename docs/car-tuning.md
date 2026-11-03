# Native local car tuning

The current executable feature is an experimental local tuning form covering
the nine categories below through an original FH1 native component workspace,
garage, physics and save operations. It needs
only the player's FH1 files. `forza_car_tuning` defaults to false. The base-game
build supplies inert stubs; the native addresses below are for Title Update 4.

The SDK-independent field schema describes 35 possible controls; the adapter
grants access only when the current car's native upgrade gates and ranges allow
it. Copied-profile runtime checks cover representative edits across all nine
categories on an upgraded RWD Mustang; remaining fields/drivetrains and direct
non-pressure physics still need qualification. This implementation is not yet
qualified for normal saves. Named setup files and parameter-only file sharing are described below. An online community
browser or public publishing service is not implemented; its native tab is
unavailable and marked Coming soon.

## Standalone integration

This feature is independent of the tabbed pause-menu work in #235. Enable
**Settings > Gameplay > Native car tuning** in the launcher and restart the game.
After updating the launcher, rebuild once with your own Title Update 4 selected
to prepare the tuning resources automatically. The stock FH1 pause list offers **Car Tuning** above
Graphics and Quit only for the paused, primary-user, offline Colorado free-roam
context with an owned current car. The existing native availability checks run
again on entry and Apply. Missing donor aliases keep the stock pause screen
available. Discard/Back and teardown restore its native list and focus through
the existing FH1 refresh callback.

Set **Settings > Online > Sign in to Xbox LIVE: off** before launching. The current gate also
excludes solo free roam while signed in to Forza Live: network-enabled mode is
broader than active multiplayer participation. This conservative restriction
remains in place because current presence and session records do not reliably
distinguish solo play from joining or leaving a live session. Supporting signed-in
solo play needs a reliable session lifecycle snapshot and transition tests;
removing the network check alone would not preserve the live-session boundary.

The build retains the unmodified title update in `runtime/update` and prepares
only the tuning donor aliases in a separate `runtime/update-native`. Turning the
switch off selects the stock update again, without rebuilding or changing its
bytes. An explicit `--update_data_root` remains authoritative; tuning CLI and
`REX_FORZA_CAR_TUNING` overrides follow the runtime's precedence. Failed or
unsupported preparation falls back to the stock update and reports the reason
in the build log. No new pause menu, Change Car overlay, or other game's files
are required.

If tuning is enabled but its prepared resources are unavailable,
Play shows a warning and an **Open Setup → Build** action. Select Title Update 4
and build again to prepare the resources. The warning does not block ordinary play.
After a preparation failure, the warning shows the saved reason and its next step
even after restarting the launcher. It distinguishes no TU4 selection, missing
launcher tools, an unsupported original archive and file-access problems. Repeating
Build alone does not repair a missing tool or unsupported input.

The build log records the preparation error, a diagnostic code and the tool's
bounded output. Archive mismatches include the expected and actual SHA-256 and
the checked file path; a mismatch means unsupported input, not proof of corruption.
The last result is stored in `runtime/native-tuning-status.json`. A successful
preparation replaces the previous failure. Older installations without this file
still show the generic warning and direct players who already rebuilt to the
preparation error in their build log. Original game/update inputs remain unchanged.

The isolation starts on current main and carries tuning from the combined
`d877a127cc301c9bcc23e06f64d5427c9e0519de` checkpoint, including its native footer,
class/PI comparison, manufacturer logo, painted panels, simulation status and
tab-transition fixes. Shared components are limited to the tuning workspace,
car identity/logo layout and FH1 widget primitives. No tabbed pause controller,
Social page or Bucket List is required. Named setup paths and the `.fh1tune`
schema are unchanged.

CPU validation of this standalone source covers the tuning model, saved setup
and file library, shared components and authored donor-directory fixtures.
The adapter, tuning workspace, stock-menu hooks and offline activity observer
also compile for disc and TU4 with the installed SDK. Earlier runtime evidence
below belongs to the predecessor tuning/combined builds: it does not qualify
this isolated entry point. Before release, validate its stock entry and focus
return, Apply and save/restart, named library and export/import in a separate
copied-profile session. Keep the runtime option off by default until those gates
are complete.

## Source comparison

Read-only inspection of the locally supplied FH2 Xbox 360 package (title
`4D530AA4`, media `7C7779D1`) found no tuning action in any XML in its gamemodes
archive and no tuning component scene in its 819-entry UI archive. Its workshop
exposes auto upgrades, custom upgrades and owned upgrades. Its executable retains
tuning classes and database fields, as FH1 does. Retained code/string names are
not evidence of an exposed player feature. These findings concern this Xbox 360
build; they do not establish parity with the separate Xbox One game.

The supplied FM4 game package (title `4D530910`, media `33E7B39F`) includes a real
`540_I_TUN_CHANGE_SETUP` scene. Its component inventory contains 29 sliders and six
balance controls across nine categories; the executable category dispatcher at
`826041B0` calls the corresponding nine builders. `NewProfile_Career_Garage` has
35 tuning columns. Its content-install package has media ID `6225E9B1`.

All extracts, decoded assets, disassembly and original binaries remain private
outside Git. The code in this change is original interface/model/UI work; it does
not copy game instructions, serialized UI, text tables or textures.

## Local categories and FH1 counterparts

The maximum count includes eight forward gears and AWD differential controls;
the installed transmission and drivetrain can expose fewer. These are verified
FH1 native interfaces, not functions imported from FM4.

| Category | Controls | FH1 TU4 getters/ranges | Native prerequisite |
| --- | --- | --- | --- |
| Tires | Front/rear cold pressure (2) | `82656EC8`, `82656EF0`, `82656F10` | No upgrade gate in retained category builder |
| Gearing | Final drive, gears 1–8 (9) | `82656F30`, `82656F68`, `82656F88` | `826564B8` final drive; `826564D8` gears; actual gear count |
| Alignment | Front/rear camber/toe, caster (5) | `82656FB0`, `82657040`–`826570E0` | `826564F8` suspension |
| Anti-roll bars | Front/rear (2) | `82656048`, `82656080`, `826560A0` | `82656518`, `82656538` independently |
| Springs | Front/rear stiffness and height (4) | `826560C0`, `82656150`–`826561C0` | `826564F8`; non-fixed car-specific bounds |
| Damping | Front/rear rebound and bump (4) | `826561E0`, `82656240`–`826562A0` | `826564F8`; non-fixed car-specific bounds |
| Aero | Front/rear downforce (2) | `826562C0`, `826562F8`, `82656318` | `82656558`, `82656578`; non-fixed bounds |
| Brakes | Balance and pressure (2) | `82656338`, `82656378`, `82656390` | `82656598`; direct getter supplies displayed percentage |
| Differential | Driven-axle acceleration/deceleration, AWD center (5) | `826563A8`, `826563E0`, `82656440`–`826564A0` | `826565B8`, `826565D8`, `82656438`; drivetrain and individual field gates |

FH1's retained builders are `82850250`, `828504E0`, `828509A8`, `82850BF8`,
`82850E58`, `828512B0`, `828515A0`, `828518A8` and `82851BA0` respectively. The FM4
builders invoke equivalent model interfaces and part-level checks. FM4 upgrade
descriptions corroborate sport transmission final drive, race transmission gear
ratios, race suspension/brakes/bars/aero and sport/race differential distinctions.
The adapter must call actual native predicates rather than infer availability
from an upgrade's name or price. Differential deceleration/center and stock race
cars particularly require runtime qualification.

All nine categories have retained FH1 counterparts and are bound by this adapter.
The temporary native model supplies each field's endpoints by sampling its
getter at normalized zero and one. `82656010` / `82DF6C48` resolves stock defaults
into that model while saving and restoring the live definition's tune payload.
Fixed or invalid ranges are unavailable. No range is invented from an upgrade
name. Native physics and save tests are still required for each category.
Physics settings also contain handbrake, ABS, stability/traction and fuel entries;
these are not among the 35 visible FM4 controls and are not exposed here.

## Editing and save contract

The tune payload is 47 words (`0xBC` bytes). Values are normalized to `[0,1]`, with
`-1` selecting the stock default. Thirty-five words represent the exposed local
fields. Gear 1 starts at payload `+0x9C`; pressure is at `+0x30`/`+0x5C`.
Opaque words and untouched stock sentinels are preserved exactly. Viewing the
form must not normalize the saved payload.

`car_tuning_fields.h` describes field identity and offsets without runtime or SDK
dependencies. `EditSession` accepts a capability schema, rejects unavailable
fields, handles increasing/decreasing display directions, and keeps all edits
host-local. Cancel restores the original bits. Reset writes the stock sentinel.
Rear acceleration on a sport differential links the rear deceleration word,
matching `82846474` / `82846570`; that peer is not independently editable. Race
differentials expose both fields independently. Other unavailable and opaque
words remain unchanged. Gears must remain in descending ratio order (equality is
allowed); the form rejects a crossing edit and asks the player to adjust its
neighbor first. Reset All restores the whole editable setup atomically.
Before Apply the adapter rechecks owned ID, model ID, live-car address, definition
pointer, all 45 setup part IDs, the original payload and loaded limits. The parts
block's `0xB4`-byte value semantics are established by the native initializer
`82C9E6D8` and comparator `82C9E700`.

Pressure bounds come from loaded settings `8359FD18`/`8359FD1C`; the native stock
pressure comes from `82145560`. They are global physics bounds, not individually
invented per-car limits. The retail settings observed are 15–55 PSI and stock
30 PSI, but the editor reads the runtime values.

The native model is allocated/constructed at `0x940` bytes / `8271D6E8`, initialized
for the live car by `8265CA18`, and destroyed with `82658570` then `8242D1A0`.
Initialization resolves stock sentinels, so Apply restores the exact original
garage payload plus explicit edits to model `+0xC0` before calling `82659028`.
That operation writes the garage through `825A8518`, updates live setup, resolves
physics through `82DF6968`, refreshes the live physics view and requests normal
profile saving through `82583918`/`82520EA0`.

The adapter confirms garage payload, live-car payload and all four cold tire
pressures after the call. The wheel pressure interface is established by
`82DF6510` (`+0x1DC0`, wheel stride `0x410`). A mismatch closes the edit session;
it never retries or rolls back a potentially completed native write. This
readback is not proof of a successful disk save. A copied-save restart test is
still required.

The form calls FH1's preference-aware formatter `82D6FBD0`, materializes the
returned string through vtable `+0x1C`, copies bounded UTF-16 text, and releases
the reference with `824CEAB0`. Unit families are pressure 18, ratio/anti-roll/
damping 0, alignment 20, spring 25, height 8, force 28 and percent 1. Inputs are
raw native getter values (including radians for alignment). Unit selection uses
`82D6DD00`, and `825D4AC8` converts a visible step back to native units. This
honors FH1's mixed unit profiles rather than assuming one imperial/metric flag.
Choices are bounded to roughly 1,000 steps per field; open-time latency and
allocation behavior need runtime measurement. Brake balance displays the direct
getter; the old slider's two orientation inversions must not be repeated here.

## UI integration and lifetime

Public integration is `car_tuning_menu::Available(ctx, base, pause_screen)` and
`Open(ctx, base, pause_screen)`. Host action ID is `0x7115` (`car_tuning`). Access
requires the paused, primary-user, offline Colorado free-roam context and a
matching owned car. Activity observations occur before the Live sign-in gate.

The tuning workspace composes independently loaded FH1 garage paper, tape and
text nodes through `fh1_menu_widgets.h` and `native_tuning_components.h`. The
nine horizontal tabs show the active and locked categories. Only the selected
category's available rows appear, with graphical ranges, a saved marker, and
separate saved/draft values formatted in the player's native units. Left/right
changes the selected setting; LB/RB changes category. Seven standalone native
performance results occupy aligned saved/draft columns, with calculating and
unavailable states. Contextual help and Apply Setup / Discard / Reset All have
separate anchors. Reset All affects every editable category, not just the tab.
The underlying stock screen retains native dispatcher and datasource ownership;
its presentation and the parent pause presentation are suppressed only after
the dedicated component donors validate, then restored on teardown.

The launcher's build runs `tools/native_tuning_assets.py` to prepare 22 independent
aliases of the player's pristine FH1 garage triplet. For a development build,
`tools/native_tuning_assets.py ORIGINAL_FH1_TU4 --output NEW_PRIVATE_DIRECTORY`
remains available.
It checks the original archive hash and TU4 executable identity, copies opaque
compressed scene payloads without modification, and records provenance. It does
not patch pause flow or embed game resources in source control. The launcher
selects `runtime/update-native` for standalone tuning. If the full native pause
menu is enabled, it selects `runtime/update-menu`, which also contains the tuning
aliases. With both options off, it selects the unmodified `runtime/update`. Before requesting any donor, native
preflight requires all 66 alias members and rejects unsupported directory,
encryption and compression layouts. Native binding validates required nodes
and isolates original controllers. `fh1_menu_components.h` supplies reusable stable-ID tabs, independent focus and
activation, wrapping bumper selection, locked explanations and a variable-count
viewport, plus a shared header layout. The workspace owns directional field
selection and value stepping with release arming and bounded repeat; bumpers
select tabs. Hidden native spinners are lifetime-owned data, not input authority.
The header binds FH1's localized manufacturer/model fields. Both the workspace
and named library also retain the
native `CLASS_SM` badge/`TEXT_PI` binding in the shared header, beside a CURRENT
CLASS label. This reads the current loaded car through the PlayerBar's native
`825E6518` / `825E6620` class/PI getters and the original class-badge renderer;
it is refreshed once per second and rebound on reopen. FH1 supplies the class
image/color and numeric display-PI conversion. No draft PI is invented, and
these values are not compatibility gates or serialized into shared setup files.
A missing/mismatched current-car readout is shown as unavailable. The full-screen
paper lies below the original native blur/footer layer; the single metric paper and foreground lie above it.
The original footer controller renders the actual controller atlas icons and
owns label spacing. Native context/layer sorting (`82F1F2B0`) establishes draw
order; changing presentation-registry order is insufficient. No missing `COMPONENT_TUNING_MENU` resource,
other game's assets, ImGui or browser overlay is used.

Stock sentinels remain distinct from explicit values, and Reset All restores
stock on every editable field. Generated choices align to the native unit
profile's display precision, retaining exact endpoints and existing saved values.
This avoids half-digit rounding changes when the native save path normalizes
edited floats during reload. Opening input must be released before edits or Apply. The
component composition is still experimental and requires visual/lifecycle
qualification along with the per-category native physics checks below.

Four existing options hooks route only matching tuning screens: bind
`827303E8`, notify `82739190`, teardown `827142C0`, and input `82736B08`.
Apply and its screen transition run inside the native input dispatcher; the
frame tick has no native transition-job context. `graphics_menu::Tick` handles
release arming, category/reset input and bounded preview advancement. Saving keeps input
locked and uses a detached edit-session copy plus a screen generation token so
native callbacks cannot mutate freed/replacement UI state.

Shared integration edits are confined to `graphics_menu.cpp`, `live.h`,
`live_xmsg.cpp` and the source list in `forza-recomp/CMakeLists.txt`.

## Named setups and file sharing

Press X / Setups in the native workspace. The shared tab component exposes Save,
Load, Rename, Delete, Export and Import. The unavailable Community tab reads
Coming soon; it has no Select action, filesystem operation or online dispatch.
Save and Rename use an original FH1
controller character grid; directions select a character, A enters it, Y removes
it, DONE commits the name and B cancels. Names are 1-32 bounded ASCII characters;
letters, numbers, internal spaces, apostrophes, hyphens and underscores are
accepted, with a letter/number at each end. Delete requires a second explicit A.
No SDK ImGui keyboard, host settings overlay or browser is used.

Documents live under the runtime's `user_data_root/tunes/library`. Export writes
a new `.fh1tune` file in `tunes/exports`; share that parameter file yourself.
The recipient places it, retaining its generated filename, in `tunes/inbox` and
selects Import. The game does not publish or contact a server. The existing
ForzaLive photo/club content services have no standalone named-tune contract;
community search, author authentication, cloud storage and share codes are
planned later; the native Community browser is marked Coming soon. This route
works offline and adds no opt-out
network behavior. The containing feature remains off by default.

The original `FH1TUNE 1` text format is limited to 8 KiB. It carries a display
name, model ID, all 45 installed part IDs, a native schema compatibility ID and
only the exposed field IDs and their raw normalized float bits, including stock
sentinels. It carries no owned/profile identity, opaque tune words, live memory
addresses, display-unit assumptions, game assets, livery or executable data.
The schema ID covers the native range endpoints, stock defaults and linked-field
semantics, independently of selected display units. Unsupported versions,
unknown/duplicate fields, NaN/infinity, out-of-range values, illegal names and
malformed/trailing input are rejected. LF and CRLF are accepted. Files and root
folders that are symlinks are refused; generated IDs, rather than display names,
form paths. Each folder exposes at most 256 valid documents, bounded reads and
a bounded directory scan. Metadata is written completely to a temporary file
before rename; changing a name publishes new metadata before removing the old
file. Existing metadata is not truncated on a failed write.

Loading and importing require the same model, exact installed parts, matching
schema and exact editable-field coverage. The editor first validates the entire
candidate, including final gear ordering, then merges only permitted fields into
the receiver's existing draft. Its original/opaque words remain intact, and
sport differential coupling uses the receiver's native schema. Failure leaves
the draft unchanged. Import saves a local library document only after validation.
No load/import calls the native garage Apply operation. Preview remains cloned;
the player must choose Apply explicitly, through the existing identity/parts/
payload rechecks and native profile save path.

Copied-profile native checks exercised controller naming, save, changed-draft
load, rename, export, valid import, visible rejection of a wrong-model document,
confirmed delete, Discard and library persistence through actual process restart.
Save/load and import/reject/delete left per-test profile hashes unchanged.
An applied loaded pressure setup passed full garage/live/cold readback and
persisted through restarting the same copied profile; all 29 available native
display strings and stock flags matched. The original seed stayed unchanged.
The SDK-independent setup test additionally covers wrong parts/schema,
unknown/duplicate/unavailable fields, malformed/oversized files, NaN/range checks,
unsafe names/IDs, opaque preservation, sport coupling, atomic gear rejection,
CRLF portability and library save/rename/delete. No public upload was tested.

## Native performance preview

The retained performance-screen initializer `8265CE80` constructs an owned,
separate car definition and physics model (native state 3). `82DFA980` supplies
its standalone evaluator, and `82656E58` advances that evaluator with the native
1/120-second host-time budget. The adapter resolves the draft through
`82DF6968` on the clone and restarts only that evaluator. It never invokes the
garage Apply operation, refreshes live physics, or requests saving for preview.
The owned definition must differ from the live definition. Owned/model identity,
all installed part IDs, garage payload, live payload and live definition tune
payload must remain unchanged throughout preview. Superseded and closing forms
release the evaluator and clone on the guest thread.

The seven result pairs and native display families match `82733718` and
`82725A40`: 0-60 and 0-100 MPH time (seconds, family 3), top speed (m/s, family
13), 60/100 MPH braking distance (meters, family 9), and lateral G at 60/120 MPH
(family 0). The speed labels and results use the player's actual native unit
preferences. These are physics estimates rather than ratings or measured road
tests. Each pair carries calculating, unavailable, or completed status; no
fabricated fallback values are shown. A saved baseline and unsaved draft are
distinguished explicitly.

A private copied-profile probe completed all seven metrics on the stock Lancer
and confirmed unchanged garage/live/definition payloads, identity, parts and disk
save hashes. The first probe recalculated the current payload on the clone.
Changed-pressure draft simulation also passed: native braking results changed
while the guard confirmed unchanged garage/live/definition tune payloads.
Both completed-preview Discard and Discard while calculation was running left
the copied profile's per-test disk hashes unchanged, and released the cloned
model. Ordinary startup autosaves are excluded by taking the disk baseline
after entering the test context. The original seed hashes remained unchanged.
These tests first qualified the pressure preview path on this copied stock
Lancer. Later upgraded-Mustang checks below exercise all nine categories on
the cloned evaluator; neither test establishes real-world performance accuracy.

## Validation

The standalone C++20 test target is `forza-recomp/tests/car-tuning`. It exercises
sentinel and opaque-word preservation, range errors, cancel/reset, concurrent
writers, changed car/definition/parts, unavailable fields, reversed display
directions, sport-differential coupling, ordered gears and capability changes.
Targeted SDK compilation has passed TU4 and
base for the adapter, form and shared hook/accessor files.

A copied offline TU4 profile runtime check on 2026-10-22 passed pressure
Cancel/reopen, Reset, unchanged Apply, and edited Apply (front 15.0 PSI, rear
15.1 PSI). Native readback confirmed the complete garage/live payload and all
four cold pressures. The copied profile was written to disk, the process was
closed, and restarting the same profile preserved both displayed values. The
original seed hashes remained unchanged.
After adding preview lifetime handling, a second edited Apply (front 15.2 PSI,
rear 15.3 PSI) again matched native garage/live/four-cold-pressure readbacks,
wrote the copied profile, and persisted through a full process restart.
After replacing the visible options form with the dedicated component workspace,
a third edited Apply (front 15.4 PSI, rear 15.5 PSI) matched garage/live/four-cold-
pressure readbacks, wrote the copied profile, closed cleanly, and persisted after
restarting that same copied profile with `--resume`. Unchanged Apply, reopen,
Reset-to-stock preview, locked-category navigation and Discard also passed with
the new component lifetime. Per-test copied-profile and original-seed hashes
remained unchanged on Discard. Actual 1280x720 captures show saved/draft values,
graphical ranges, native estimates, selected/locked tabs and contextual help.
Native metadata preflight accepted the prepared archive and rejected pristine
missing-alias, missing-member and unsupported-codec inputs before any guest load.
These remain private test artifacts outside Git.

The stock Lancer exposed only pressure. A second test selected the existing
owned 1970 Ford Mustang Boss 429 (owned ID 2, model 404) through FH1's native
garage flow. Legitimate native purchases on the copied profile installed race
front/rear aero, transmission, brakes, spring/dampers and both anti-roll bars,
plus a sport differential. The discounted total was 33,028 Cr, leaving 4,752 Cr
on the copy. No parts, money or payloads were fabricated; the seed stayed exact.

The native gates exposed 29 controls across all nine categories: two pressures;
final drive and six gears; five alignment fields; two bars; four spring/height;
four damping; two aero; two brakes; and one sport rear differential control.
The RWD car exposed no front/center differential, seventh/eighth gear or
independent sport rear deceleration control. Seven-row navigation, held input,
field/tab wrapping and rejection of a crossing gear ratio passed.

A representative edit in each of the nine categories passed cloned preview,
Apply, complete 47-word garage/live readback and four cold-pressure checks,
copied-profile disk save, process closure and restart of that same profile.
After correcting a spring choice-grid rounding midpoint, all 29 native formatted
values and stock flags matched the pre-save draft after restart. All 37 untouched
payload words remained bit-exact; the sport rear acceleration/deceleration pair
persisted together at 55%. A few edited normalized floats changed by small native
round-trip amounts, while their corrected native display values stayed exact.
Reset All followed by Discard, reopen and unchanged Apply left the per-test copied
profile and original seed hashes unchanged. Native preview guards confirmed
unchanged identity, installed parts and garage/live/definition payloads; clones
were released on replacement and close. Final native 1280x720 captures show the
continuous metric paper, readable blurred/dim background, FH1 header/car identity,
controller atlas icons and populated multi-row categories.

This qualifies representative category storage and live setup payloads on that
car, not every accessible field or independently measured spring/damper/brake
physics. Unqualified cases include race differential independent controls,
FWD/AWD-specific gates, unavailable seventh/eighth gears, other unit profiles and
resolutions, car/part changes while the form is open, and teardown during an
active save. Keep the feature off by default.

Runtime testing also corrected two integration defects: the native host object
exists offline (access now checks the actual runtime network gate), and native
screen closure from the frame tick lacked the UI transition job context.

Further qualification must continue on a copied offline profile. Exercise the
remaining controls and drivetrain/part combinations, state changes while the
form is open, and teardown during an active save. Independently inspect actual
non-pressure physics behavior rather than inferring it from saved payloads or
standalone estimates. Use only real owned cars and native purchases, preserving
unrelated owned cars, installed upgrades and opaque tune data. Record each
unexercised case explicitly and retain the default-off setting.
