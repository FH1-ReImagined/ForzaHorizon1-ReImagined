# Driving camera field of view

The launcher Display > Camera section and TU4 native Graphics screen expose
separate base FOV choices for chase near, chase far, cockpit, hood and bumper.
Every choice defaults to Stock (0), which leaves the original camera behavior
untouched. Explicit choices are 40–90 vertical degrees. Save applies and persists;
Back discards unsaved edits. The launcher imports the native settings file before
its next launch; macOS also writes the external TOML through the existing path.

These are **base** values. The game still adds its CameraPhysics effects, including
speed widening, so the moving FOV can exceed the selected base. The existing
`forza_cockpit_fov` key keeps its degree values: 90 still selects a 90-degree base,
not a 90-degree offset. No migration or reinterpretation of saved cockpit values
is needed. The other keys are `forza_chase_near_fov`, `forza_chase_far_fov`,
`forza_hood_fov` and `forza_bumper_fov`.

The hook reads the original camera's base FOV and adds only the difference between
that base and the selected one to the game's finished FOV. It preserves user-owned
camera settings when Stock is selected and never edits game archives or data.
Stock CameraSettings.ini bases are 48.5, 58, 62, 66 and 53 degrees respectively.
CameraPhysics.xml adds up to 22 degrees for the chase cameras, 8 for Driver
(starting above 140 mph), 10 for hood and 23 for bumper. These values explain why
parked screenshots alone cannot validate preservation of dynamic FOV.

## Scope and transitions

Only CCamFollowLow, CCamFollowHigh, CCamDriver, CCamHood and CCamBumperHigh are
eligible leaves. Other classes, including CCamCockpit, RearView/mirrors, Wheel,
Side, free/photo/menu cameras and the base CCamFollow class, contribute zero.
CBlendCamera interpolates the endpoint adjustments with the same t used by its
original FOV getter. Entering or leaving a selected camera therefore does not
switch its whole adjustment on or off at the final frame. Nested blends are
bounded to eight levels and 31 visited nodes, with cycle, pointer, numeric and
layout checks; malformed graphs retain the original FOV.

The current world's existing pause/replay flag, loaded flag and time scale
exclude paused, replay, rewind and loading worlds. These are the same fields used
by the existing wheel-driving gate, read directly without its debounce delay.
All adjustments are bypassed while vr_head_pose_test is enabled, even for an
identity pose, or while the real OpenXR cockpit session is active
(`forza::xr::CockpitActive()`). The headset retains ownership of its projection.
Virtual-screen mode keeps the ordinary camera settings. This guard is covered by
the production-hook test; real-headset compatibility of this PR remains untested.

Camera position, near/far planes and aspect are untouched. Ultrawide retains its
existing aspect query. Mirrors retain their own unmodified rear-view camera.
The half-FOV flags used by the original class getters are respected too.

## Runtime ownership and hook

The accessor hook is `0x8284EF90` in TU0 and `0x828FF260` in TU4. Both owned
executables independently confirm the leaf reads one float from director +304
and returns. No guest camera or director bytes are written by this hook.

The director update `0x82905418` (TU4) calls the active camera's +28 virtual
getter, stores the base FOV at +304, then calls `0x82904110`, which adds the
CameraPhysics FOV contribution. The accessor hook calls the original exactly
once and adjusts its returned float. The adjustment cannot accumulate across
frames or across readers.

There are seven direct accessor call sites across six TU4 functions:

| Function | Calls | Consumer |
| --- | --- | --- |
| 0x82621F20 | 2 | Presentation FOV setter and complete render-state camera update |
| 0x82A58DA0 | 1 | Game-side visibility frustum |
| 0x824131A8 | 1 | World update |
| 0x82622558 | 1 | Split-view camera setup |
| 0x82514DB8 | 1 | Car/post-effect setup |
| 0x824C4EA8 | 1 | Further camera setup |

All seven now use the same accessor adjustment, without a caller-address filter.
The earlier two temporary scopes covered three of these calls and missed four.

`FORZA_FOV_TRACE=1` enables bounded diagnostic samples of caller, director,
active camera kind, blend t, unmodified FOV and returned FOV. It records initial
calls, sparse longer-run samples and up to 4,096 blend samples. It is off by
default and exists to qualify actual transitions and dynamic FOV in test runs.

| Camera | TU0 vtable | TU4 vtable | Base storage |
| --- | --- | --- | --- |
| FollowLow | 0x820755AC | 0x820858D4 | degrees at *(camera+192)+28; half flag +196 |
| FollowHigh | 0x8207562C | 0x82085954 | same |
| Driver | 0x82075DD4 | 0x820860FC | radians +272; half flag +304 |
| Hood | 0x82075D54 | 0x8208607C | same |
| BumperHigh | 0x82075E54 | 0x8208617C | same |
| CBlendCamera | 0x82075444 | 0x8208576C | from +192, to +196, t +200 |

Vtable/getter layouts were independently checked from the owned executables and
TU4 generated source, following the maintainer's notes on issue #436. No game
code or data is included in the repository changes.

## Revision validation

The standalone guest-memory test covers every allowed base on all five classes,
stock/invalid values, source-derived speed additions, full 101-step transitions
in both directions, nested blends, split-screen scaling, invalid pointers/fields,
cycles, recursion/node budgets, world guards and nonaccumulation without writes.
A second test compiles the actual production hook with minimal SDK interfaces
for both TU0 and TU4: all seven caller return PCs receive the same corrected
value, the original is called exactly once, and guest bytes and other context
fields stay unchanged. These tests run in the existing Windows/Linux/macOS CI
workflow. Real SDK TU0/TU4 syntax checks remain separate from those test stubs.

Local revised checks passed: both standalone hook variants with /W4 /WX,
real-SDK syntax checks for the affected hooks on TU0/TU4, and 27 focused launcher
checks covering all five independent settings and the unchanged cockpit degree
contract. All 84 authored Windows host sources compiled and linked normally
against immutable TU4 generated objects. The installed SDK's 1,265-file SHA-256
manifest and 121-patch manifest matched.

**Revised in-game validation is pending a reserved game slot.** The retained
checkpoint dde9469 had actual Stock/40/90 parked cockpit, native Save/Back/restart,
rewind/replay smoke and identity-head-pose captures on an RTX 5060 Ti. Those
captures do not validate this new accessor, dynamic speed or blend path. The revised path and five-row native layout still need runtime qualification.
The PR is available for source review; that does not establish runtime or
headset acceptance.
All prior runs used offline copied profiles, hard limits and unchanged source
profile hashes. Captures, game data, caches and build products remain outside Git.

## Current main integration (2026-10-26)

Integrated main `b560528`, including ReIGlue r2 and the merged OpenXR feature.
The native screen retains shadow softness and ambient occlusion alongside all
five camera controls. The real OpenXR cockpit guard prevents the FOV accessor
from adjusting the headset projection, independently of the test-pose flag.
TU0/TU4 production-hook interface tests and standalone math tests pass; 32
focused launcher tests pass, including one combined native-settings import of
all five FOV preferences with shadow and ambient-occlusion settings. These are
source/interface checks, not current-SDK host or revised in-game qualification.
The earlier 121-patch SDK build above belongs to the previous checkpoint.
The accessor/blend/speed and native-layout runtime gate remains open.

## Windows runtime check (2026-10-27)

On revision `71434e9`, a fresh Windows host linked against ReIGlue r2
`317216e8` reached TU4 free roam using a separate copy of a legitimate profile.
Actual 720p captures show all five native FOV controls and parked cockpit views
at Stock (62 degrees), 40 and 90. Native Save wrote the selected preference;
the production accessor trace returned 40 and 90 from the original 62-degree
value. This run observed five caller return addresses, not all seven.

The help paragraph clipped its final instruction at 720p. It is now shorter,
preserving the range, speed, transition, VR and Save/Back explanations; the
revised paragraph still needs an actual native-screen check.

The driving attempt stopped against a garage barrier, so it does not qualify
speed widening. Near/far blends, process-restart persistence and a physical
headset remain unqualified. The game returned normally to the title screen and
then exited with code 0. The observer reported a read failure during process
shutdown; the owned job was empty and the original profile hashes were unchanged.
These captures qualify the revision above, not the subsequent text change.

The benchmark restart snapshot now includes all five camera preferences. Its
previous graphics whitelist omitted them, so changing to the benchmark's
independent profile could lose a native-menu FOV selection. A regression executes
the production snapshot writer and restore reader: five distinct bases round-trip,
Stock replaces prior explicit bases, and an unrelated flag stays untouched. It
fails on the old writer and passes with the five-key whitelist fix. The affected
Windows source compiles and the production host links; actual game restart
qualification remains separate.
