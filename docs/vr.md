# Playing in VR

Experimental: tested on a Meta Quest 3 with WiVRn on Linux. SteamVR and Meta Quest Link on Windows are untested.

1. Start your VR app (WiVRn on Linux; SteamVR, Meta Quest Link or Virtual Desktop on Windows) and connect the headset.
2. Put the headset on.
3. In the launcher, press **Play in VR** (it appears next to Play once a VR app is installed). Play alone starts the
   normal flat game.

Settings > VR has the options: **Mode** (Cockpit, recommended, or Big screen), **Sharpness** (Standard, Sharp,
Sharpest: sharper needs a stronger graphics card) and **Seat height**. Recentre with the Home key, or hold both
sticks pressed for a second. Play in VR sets what the headset needs (72 frames a second, no scene anti-aliasing,
a higher render scale) for that session only; your normal settings are not changed.

Runtime detection follows the pinned OpenXR loader: a nonempty `XR_RUNTIME_JSON` takes precedence
over the Windows registry or Linux defaults, including when that explicit file is missing (no
fallback to another runtime). Native Linux installs use `XDG_CONFIG_HOME`, `XDG_CONFIG_DIRS`, then
`/etc`; each directory prefers `active_runtime.<architecture>.json` over `active_runtime.json`.
The Flatpak uses the host configuration instead of the sandbox's application configuration.

To make it sharper in WiVRn: stream HEVC or AV1 at 10-bit, raise the bitrate (50 Mbit/s an eye worked on a Quest 3),
and raise the resolution in the WiVRn app on the headset.

# VR: feasibility study and head-tracking prototype (#333)

Goal: sit in the car with a headset (OpenXR: SteamVR, Quest Link, Monado), as in Assetto
Corsa. This is step 1: where the game's camera can take a head pose, what stereo would take
and cost, where the HUD and menus are drawn, and a head-tracking prototype without a
headset. Addresses are the disc executable's, with Title Update 4's in brackets.

## Summary

- **The camera is easy to reach.** Each player view (a `CSplitView`) has a *camera
  director* that holds the active camera and, per frame, the finished view matrix
  (director + 0xF0, world to camera). Exactly two functions read that matrix: the one that
  hands it to the renderer's presentation camera and the one that builds the game-side view
  frustum. Putting a head pose into that matrix for the duration of those two calls turns
  the view inside the car with everything else (shadows, mirrors, culling) following, and
  leaves the camera's own state untouched. The prototype does exactly that.
- **The in-car view is `Camera::CCamDriver`**, not `CCamCockpit` (which the player never
  uses): driver's eye 0.21 m above and 0.51 m behind the steering wheel, 62 degree vertical
  FOV, near plane 0.1 m (`media/camera.zip` > `CameraSettings.ini`, `Driver\...`).
- **Drawing the scene twice has an engine path, but it costs CPU where there is none to
  spare.** The engine keeps Forza Motorsport 4's two-player split-screen: the world
  update loops over two `CSplitView`s, each with its own director, presentation camera and
  viewport, and a split-screen render scenario cuts crowd, particles, reflections and MSAA
  tiling to afford it. Whether FH1's menus still reach it is not established. A second eye would add the scene's share of the command processor and the
  render thread (the scene is about two thirds of the ~3,000 host draws per frame, measured
  with RenderDoc), roughly +5 to +8 ms of CPU per frame on a 5 GHz Zen 4, on the two threads
  every frame waits for; simulation, physics, audio and the HUD stay single. Fine as a
  correct-stereo reference on fast PCs, not as the product. A lead: the scene is drawn in
  three predicated EDRAM tiles, so the emulated GPU processes it three times; a non-tiled
  scene would save more command-processor time than a second eye costs.
- **The HUD is already separable:** the SDK (patch 0046) finds the frame's scene/UI split
  from the front buffer's formats and can draw the UI into its own target, which a
  cockpit-fixed or head-locked panel needs.
- **Built: an OpenXR session with a virtual screen and a head-tracked mono cockpit**
  (`vr_mode`, launcher Display > VR (experimental) > VR headset (experimental)); [what it
  does, how, and the test steps for a Quest 3 with WiVRn](#openxr-session-and-cockpit-milestones-1-and-2).
  Version 2, after the first headset test: camera physics off in the cockpit, the field of view
  fitted to the eyes, pads without focus, a smaller screen, the trace
  ([what changed](#version-2-after-the-first-headset-test)). Version 3, after the second: the eye
  kept in the cabin, menus on the screen, the HUD on its own panel, depth stereo
  ([version 3](#version-3-after-the-second-headset-test)). Version 4, after the third: the eye
  free to move aside and back, slow eye adaptation, the size cull as on a monitor (cars no longer
  pop in close ahead), the headset's picture sharpened, depth stereo off by default, a plan for
  true stereo, the headset's own picture recorded and an emulator closer to it
  ([version 4](#version-4-after-the-third-headset-test)). Version 5: true stereo, the scene drawn
  for each eye from its own point in the GPU emulation (72 fps on the RX 9060 XT with the scene's
  MSAA off; [version 5](#version-5-true-stereo)). Version 6: true stereo at 72 fps on the real
  headset ([version 6](#version-6-true-stereo-on-the-real-headset)). Version 7: no jump of the
  cabin when looking at a side window ([version 7](#version-7-the-cabin-no-longer-jumps)). Version 8:
  the cabin stays put in the car when the head moves, and its light with it: the game drew the car's
  interior from the in-car camera's own point while the eye was within 10 cm of it
  ([version 8](#version-8-the-cabin-stays-in-the-car)).
- **Recommended plan** (with the integration research, merged below): (a) an OpenXR session
  with a virtual screen, then the head-tracked mono cockpit (the prototype's hook fed by
  the headset, the HUD on a quad, the game at its own rate under the runtime's
  reprojection); (b) stereo by warping that view to both eyes with the depth the DLSS/FSR 3
  path already decodes, with the split-screen view as a correct-stereo reference, and true
  stereo inside the GPU emulation last; (c) comfort (camera effects, motion blur and depth
  of field off, recenter, IPD) and the UI on panels. Sizes and order below.

## The camera system

### Where the view is computed

Per frame, on the main loop's thread (`FORZA_VR_POSE_TRACE=1` logged the push on one thread,
and the frustum's chain starts in the same world update):

```
world update                      sub_823E3218 (TU4 824131A8)
  for each of 2 split views       manager + 244 + 172 * i   (i < 2, a fixed loop)
    CSplitView::Update            sub_825AA1B0 (826223B0)
      director update             sub_82858288 (82908710): active camera -> view matrix
      push to the renderer        sub_825A9D20 (82621F20)
      ...
  game-side visibility            sub_829A7448 (82A5B920) -> sub_829A6E18 -> sub_829A5598
                                  -> sub_829A4860 (82A58DA0): view 0's frustum
                                     (sub_823EAED0 builds it from matrix, FOV, aspect, clip)
```

The manager is `*(*(*0x832DF024 + 4) + 4)` (TU4 `0x833CAAE4`), the same object whose
+0x80/+0x84 is the vehicle list (`tools/autodrive.py`).

**`CSplitView`** (vtable `0x82029F38` in TU4): +8 the camera director, +12 the viewport
(x, y, width, height as integers: 0, 0, 1280, 720; then min/max Z), +40 the view's
presentation camera (a deferred `IPresentationCamera`). In single player view 1's director
is null.

**The camera director** (no RTTI; read from guest memory in free roam and in a race):

| Offset | What |
| --- | --- |
| +0 | the `Forza2::CPlayer` |
| +20 | the active camera (a `Camera::ICamera`) |
| +24 ... +116 | the player's cameras: FollowLow, FollowHigh, Driver, Hood, BumperHigh, three Free, FreeTargetCar, FixedFree, Wheel, Side, Ortho, four TSC (track-side), 321, PreRaceTrack, FreeTrack, PhotoMode, RearView, Blend, NoCar |
| +176 | camera position in the world (x, y, z, 1) |
| +208, +224 | camera forward and up in the world |
| +240 | **the view matrix**: 4x4 floats, world to camera, Direct3D row-vector convention, left-handed camera space (x right, y up, z forward). Rows 0-2 hold the camera's right, up and forward axes as columns, row 3 is `-position . axis` for each axis |
| +304 | vertical field of view in radians (1.0821 = 62 degrees in the cockpit, 1.0123 = 58 chase) |

`sub_8284EF88` (`828FF258`) returns director + 240 and `sub_8284EF90` the FOV; near and far
come from the active camera's virtual functions at +32 and +36. The only callers of the
matrix accessor are `sub_825A9D20` (twice) and `sub_829A4860`.

**The cameras.** The car-relative ones (Cockpit, Hood, Driver, BumperHigh, RearView) share
their update `sub_8285C8C8` (`8290CD80`); Wheel and Side have their own. vtables (RTTI):
CCamDriver `0x82075DD4` (`0x820860FC`), CCamCockpit `0x82075CD4` (`0x82085FFC`), CCamHood
`0x82075D54`, CCamRearView `0x82075FD4`. The right stick's look-around is a yaw angle at
camera + 280; `sub_82851518` turns the car's forward axis by it. With that look turned 90
degrees the game draws the view without the car (no door, no pillar), so the game's own
look-around is not a head pose: it changes what is drawn.

`RB` changes the camera (FollowHigh -> Driver -> ...); `BACK` opens the map.

### Where the matrices end up

`sub_825A9D20` (view, flag) calls the view's presentation camera (a
`CPresentationCameraDeferred`, game thread) with:

| vtable offset | Call | Argument |
| --- | --- | --- |
| +168 | SetViewTransform (stub `sub_82DB9AE8`) | the director's view matrix, by value |
| +56 | SetFOV | FOV x a constant |
| +68 | SetAspectRatio | `sub_823E22C8` (the viewport query `hooks/ultrawide_projection.cpp` widens) |
| +196 | SetZClip | near, far |
| +80, +44, +236, ... | further setters with values from the active camera (its virtual functions +76, +80, ...), not identified | |

and then hands the same set (matrix, FOV, aspect, near, far, a vector from the director and
two flags from the active camera) to `sub_82DBADC0`. The deferred stubs queue each call (80 bytes for a matrix) for the render
thread (`sub_8261E178`), where `CPresentationCamera::SetViewTransform` (`sub_823EB7A0`)
stores the matrix at camera + 16 and sets the dirty flag at + 464; the projection
(SetProjectionTransform, +172, `sub_82D8C820`) goes to + 80. The renderer builds the
per-object constants from there on the CPU: the car shaders take `matWVP`, `matWV`, `matVP`,
`matVInv` (`media/shaders/Cars/*.fxobj`), the engine's world shaders `CamViewProjMat`,
`CamProjMat`, `PCamViewProjMat` (strings in `default.xex`). So the GPU never sees the camera
alone: per object it gets world-view-projection products computed by the render thread.

### Where to apply a head pose

A head pose is a rotation R and an offset t of the eye in the camera's own space. With the
row-vector view matrix V, the posed view is `V * inverse(H)`, `H = [R 0; t 1]`
(`src/platform/vr_head_pose_math.h`). The cleanest point is the director's matrix between
the director update and its two readers:

- the renderer gets the posed view, so the main view, its culling and the shadows follow
  (seen in the screenshots), and so does everything else the render thread derives from the
  presentation camera, such as the motion-blur reprojection;
- the game-side frustum follows too (`sub_829A4860`), so game-side visibility uses the same
  view;
- the camera's own state (look-around angle, springs, smoothing) never sees the pose, so
  nothing integrates it and nothing drifts, and switching cameras or pausing is unaffected.

In the car frame: the cockpit camera is rigidly attached to the car (plus the game's camera
shake and G-force head movement, `CameraPhysics.xml` > `CameraSpaceYPROffset` /
`CameraSpaceXYZOffset`), so camera space is the car frame up to those effects. A headset's
pose relative to its calibrated seated origin goes in as R and t directly; the effects
should be off in VR (stage c).

For stereo the same point takes the eye offset: t.x = -IPD/2 for the left eye, +IPD/2 for
the right (the second view would be the second split-screen view, or the GPU emulation's
second eye). The projection must become the headset's asymmetric per-eye frustum; the
presentation camera has a full projection matrix setter (+172) besides SetFOV/aspect, but
which one the renderer prefers is not checked yet.

## Head-pose prototype

`src/hooks/vr_head_pose.cpp`, off by default (cvar `vr_head_pose_test`, category VR).
With the cvar off the hooks call the original right away; normal play is unchanged.

```
--vr_head_pose_test=true
FORZA_VR_POSE="yaw,pitch,roll,x,y,z"   fixed pose from the start (degrees, metres)
FORZA_VR_POSE_FIFO=<path>             mkfifo it; each line "yaw pitch roll x y z" replaces
                                      the pose ("skip view|frustum|none" for A/B tests)
FORZA_VR_POSE_ALL=1                   every camera, not only the in-car ones
FORZA_VR_POSE_TRACE=1                 log the calling thread every 300 calls
```

Positive yaw turns the head left, positive pitch looks up, positive roll tilts the head
left; x is right, y up, z forward in metres. The pose applies while the active camera is
CCamDriver or CCamCockpit. Both readers of the matrix are hooked (`sub_825A9D20` and
`sub_829A4860`): each writes the posed matrix into the director, runs the original and
writes the game's matrix back unless the game already wrote a new one. The math has a
standalone test (`forza-recomp/tests/vr_head_pose_math_test.cpp`: yaw, pitch, roll,
offset and their order).

Example (a test session as in `tools/live_session.sh`, scratch save and data dirs):

```sh
mkfifo /tmp/pose.fifo
FORZA_VR_POSE_FIFO=/tmp/pose.fifo tools/live_session.sh DIR 600 --vr_head_pose_test=true ...
echo "60 0 0 0 0 0" > /tmp/pose.fifo     # look left
echo "0 30 0 0 0 0" > /tmp/pose.fifo     # look up
echo "15 -5 10 0.2 0.05 0.05" > /tmp/pose.fifo   # lean right and tilt
```

### Results (TU4, Linux, RX 9060 XT, 720p, 30 fps)

Three runs on a scratch copy of the save: free roam standing at the spawn (straight, 60
degrees left and right, 30 up, 150 back, a lean of 0.25 m right with yaw/pitch/roll), and the
GoPro Millennia Masters festival race driven by `tools/autodrive.py` at 90-140 km/h in the
cockpit view (straight, left, right, up, lean, 75 left and right, 160 back), poses sent
through the FIFO while driving. Screenshots stay out of Git.

Works:

- The view turns and moves inside the car: door, pillars, roof lining, seats, the driver's
  arms and hands, the dashboard and the gauges are all drawn from the new eye point, at any
  angle tried. The game's own look-around (right stick) drops the car at 90 degrees; the
  posed view keeps it.
- Nothing visibly culled or popping at 60-160 degrees while driving: houses, trees, the
  crowd, guard rails, other cars, the shadows. The render side culls with the presentation
  camera, which gets the posed matrix.
- The rear-view and wing mirrors keep working (they have their own camera, CCamRearView).
- 30 fps held, no fault in the log; the camera's state is never modified, so switching
  cameras and the race intro behave as before.

Off, or to handle later:

- The HUD stays fixed on the screen (expected: it is a 2D overlay, see below).
- Only arms and hands exist: looking down or back shows the seat without a body.
- The mirrors show the unposed mirror image (their camera does not follow the head;
  physically the image should shift with the eye).
- The game's motion blur reprojects with the previous frame's camera, so quick head turns
  would smear; in VR it has to be off.
- The game-side frustum (`sub_829A4860`) shows no visible effect: at a near standstill,
  shots at 75, -75 and 160 degrees with that reader posed and unposed (`skip frustum`) differ
  no more than two shots with the same setting (mean pixel difference 30.4 against a 31.5
  noise floor, 4.7 against 6.0, 6.8 against 7.3), while `skip view` alone shows the unposed
  forward view. It feeds something the screenshots do not show (spawning, streaming
  priority or sound are candidates); posing it is harmless and keeps it consistent.
- Not tried: a full lap with continuous head motion, night (headlights), rain, other cars'
  interiors, the photo mode and replays (FORZA_VR_POSE_ALL applies to every camera).

## OpenXR session and cockpit (milestones 1 and 2)

Built on top of the study (plan step (a)1 and the head-tracking part of (a)2). Code:
`src/platform/xr.{h,cpp}` (loader, instance, session, the OpenXR thread),
`src/platform/xr_math.h` (poses and fields of view), `src/platform/xr_frame_tags.h` (frame
pairing), the camera hooks in `src/hooks/vr_head_pose.cpp` (and the viewport query's in
`src/hooks/ultrawide_projection.cpp`), ReIGlue commits 0390, 0391 and 0392
([the SDK side](#the-sdk-side-reiglue-0390-0397)), the launcher's `forza_launcher/vr.py`. Version 2
(below, [after the first headset test](#version-2-after-the-first-headset-test)) changed the cockpit's
field of view, the camera physics, pad focus, the screen size and the latency estimate. The OpenXR headers
are vendored (`thirdparty/openxr`, OpenXR-SDK 1.1.62, Apache-2.0 or MIT); the loader is opened at
run time, never linked.

### What it does

| Setting (cvar) | Default | What |
| --- | --- | --- |
| VR headset (experimental) (`vr_mode`) | `off` | `off`: nothing VR runs, no OpenXR library is loaded. `screen`: virtual screen. `cockpit`: virtual screen plus the head-tracked in-car view |
| Seat height (cm) (`vr_seat_height_cm`) | -4 | Cockpit: raises or lowers the eye from the driver's position (version 4: 4 cm below it, [why](#what-moves-when-looking-around-1)) |
| `vr_recenter_key` | `Home` | Recentres the view (both sticks pressed for a second do the same) |
| `vr_fov_margin` | 5 | Degrees drawn beyond the headset's field of view on each side |
| `vr_camera_effects` | false | Cockpit: keep the game's camera physics (shake, G-force lean and tilt, speed field of view) on top of the head pose |
| `vr_cockpit_parallax` | true | Cockpit: the car's interior drawn from where the eye really is ([version 8](#version-8-the-cabin-stays-in-the-car)); off: as the game does, from the in-car camera's point while the eye is within 10 cm of it |
| `vr_head_side_limit_cm`, `vr_head_back_limit_cm`, `vr_head_up_limit_cm` | 18, 15, 6 | Cockpit: the eye's room round where it was recentred, an ellipse on the floor and a dome over it ([version 4](#what-moves-when-looking-around-1); back since version 8) |
| `vr_head_position` | true | Cockpit: the eye follows the head's position; off: it only turns (3 degrees of freedom, the seat height kept) |
| `vr_exposure_speed` | 0.02 | Cockpit: how fast the exposure adapts while the head pose drives the camera, a factor of the game's rate |
| `vr_cull_scale` | true | Cockpit: the object size cull as for the in-car camera's own field of view |
| `vr_sharpen` | 0.5 | Cockpit: sharpening of the headset's picture, 0 to 1 (ReIGlue 0395) |
| `vr_stereo` | off | Cockpit: `depth` makes each eye from the picture and its depth (version 3; ReIGlue 0394) |
| `vr_hud` | true | Cockpit: the HUD on its own panel in front of the seat (ReIGlue 0393) |
| `vr_screen_width`, `vr_screen_distance` | 2.2, 2.5 | The virtual screen in metres (47 degrees wide; version 1: 3.2 m, 65 degrees) |
| `vr_runtime_timeout` | 20 | Seconds to wait for the runtime at start |
| `vr_refresh_rate` | 0 | The headset's refresh rate: 0 matches it to the game's frame rate (72 fps: 72 Hz; [version 6](#72-hz)), a number asks for that rate, -1 leaves it to the runtime |
| `vr_frame_wait` | 4 | Milliseconds a headset frame may wait for a game frame that is due ([version 6](#every-game-frame-shown-once)) |
| `vr_trace` | false | Logs the head pose, the game camera, the field of view and the latency about 10 times a second ([the trace](#the-trace-vr_trace)) |

- **Virtual screen** (both modes): the newest game picture on an OpenXR quad layer 2.2 m wide
  (height from the picture's aspect) 2.5 m in front of the recentred head, at its height, in
  `LOCAL` space, redrawn at the headset's rate whatever the game's frame rate. A picture drawn
  at render scale 2 or more is halved (or more) with a linear filter on the way: the runtime
  samples layers without mipmaps, and the screen is about 1,000 headset pixels wide. The desktop
  window keeps showing the game as before (it is the mirror).
- **Cockpit:** while the active camera is the in-car one (`CCamDriver`, or `CCamCockpit`), the
  camera hook takes the head pose from the headset instead of `FORZA_VR_POSE`: rotation and
  position relative to the recentred head plus the seat height, held in the eye's room
  (`vr_head_side_limit_cm` and the others). The game draws exactly the union of both eyes' fields of view plus the
  margin: around an axis pitched to the middle of the eyes' up and down angles, with a
  projection aspect of its own (Quest 3: 123 x 109 degrees, the axis 5.5 degrees down), the
  vertical field of view written into the director's FOV (+304) and the aspect returned by the
  viewport query, for both readers, like the matrix. The game's camera physics are off for that
  camera. That picture goes to a projection layer with the pose and field of view it was drawn
  with, the same image for both eyes (mono, no depth); the runtime reprojects head motion
  between game frames. Other cameras, menus, loading and cut scenes show on the virtual
  screen.
- **HUD:** it stays in the game's picture. In the cockpit picture the speedometer and map sit in
  its corners, 48-59 degrees to the side: each at the outer edge of one eye only ([version
  2](#hud-and-menus-c)); the car's own gauges are in the cockpit (`cockpit_gauges_lit` keeps them
  lit). A HUD on its own quad needs the scene/HUD split per frame (SDK patch B of the plan): not
  done here.
- **Recentre:** at the session's first tracked frame, with `vr_recenter_key` (Home), both sticks
  held for a second, and when the runtime recentres its own space (Quest: hold the Meta button).
  It keeps the heading only, so the horizon stays level.
- **Failures** log one line that starts with `VR:` and ends with `- playing flat`: no loader
  (`libopenxr_loader.so.1` / `openxr_loader.dll`), no runtime installed or active, the runtime
  did not start (its server not running), no answer within `vr_runtime_timeout` (WiVRn's
  `xrCreateInstance` waits until a headset connects, so the probe runs on its own thread), no
  headset, a runtime without `XR_KHR_vulkan_enable2`, an SDK without ReIGlue 0390/0391, a
  renderer other than Vulkan, a Vulkan device not created through the runtime, a failed session,
  a lost session (the game goes on flat from there).
- **Pads without focus:** while the session runs, pads and wheels keep working when the desktop
  window has no focus (ReIGlue 0392); the keyboard still needs the focus.
- Not done: stereo, the HUD quad, motion blur and depth of field (still on: no clean switch
  found yet, below), the guest vblank locked to the headset (patch C), D3D12.

### How it works

1. `ForzaApp::OnPreSetup`, before the GPU plugin creates the Vulkan provider: `xr::Prepare` opens
   the loader and, on a thread it waits for up to `vr_runtime_timeout`, creates the instance
   (`XR_KHR_vulkan_enable2`, `XR_KHR_convert_timespec_time` or the Win32 counterpart), finds the
   head-mounted display and reads its Vulkan requirements. Then it registers the Vulkan creation
   hooks (ReIGlue 0390): the runtime creates the `VkInstance` (`xrCreateVulkanInstanceKHR`), picks
   the physical device (`xrGetVulkanGraphicsDevice2KHR`) and creates the `VkDevice`
   (`xrCreateVulkanDeviceKHR`), each with the extensions it needs on top of the SDK's.
2. `OnCreateDialogs` (the provider exists): `xr::Start` checks that the device is the one the
   runtime made, turns on the guest output export (ReIGlue 0391) and starts the "Forza OpenXR"
   thread: session on graphics queue 0 of the game's device, `LOCAL` and `VIEW` spaces, an 8-bit
   sRGB swapchain the size of the guest output, then the frame loop: `xrWaitFrame`,
   `xrBeginFrame`, the newest guest output if there is a new one, `xrEndFrame`. The device's queue
   lock (`AcquireQueue`) is held around `xrBeginFrame`, `xrEndFrame`, swapchain acquire and
   release and the copy's submission, the calls `XR_KHR_vulkan_enable2` says use the queue.
3. The copy: the guest output (A2B10G10R10, gamma-encoded) is blitted to an 8-bit UNORM image and
   copied bit for bit into the sRGB swapchain image (a blit into sRGB would encode the gamma a
   second time). Barriers as the presenter's own guest output capture: the guest output leaves and
   returns to `SHADER_READ_ONLY` in the fragment shader stage, so a later refresh of the same
   image waits for the copy (same queue). The frame is held (ReIGlue 0391) until the copy's fence.
4. Frame pairing (`xr_frame_tags.h`): the view hook (main loop thread) asks for the head pose
   predicted for now plus the measured latency (camera built to picture shown, an average of 5 to
   100 ms), poses the matrix and remembers its 64 bytes with a new tag. On the render thread the
   hook on `CPresentationCamera::SetViewTransform` (`sub_823EB7A0`, TU4 `sub_8241B860`; the matrix
   lands at camera + 16) finds the tag by the bytes; VdSwap writes the frame's tag into the swap
   packet (one more dword in `PM4_XE_SWAP`, which a stock command processor skips); the command
   processor hands it to the presenter with the guest output (ReIGlue 0391); the OpenXR thread looks
   the pose and field of view up. The render thread also swaps frames without a new view (it draws
   the camera's last view again), so the tag stays with the render-side camera until it gets an
   unposed matrix (another camera). A tag more than 300 ms old at display time means the game
   stopped building in-car views: the picture goes back on the screen.
5. Every 10 s a line `VR: N Hz headset, N game frames/s copied; shown as cockpit N / screen N
   frames; swaps tagged N/N (dropped views N, repeated N); latency N ms (game N + runtime N;
   predicting N); xrEndFrame max N ms`. Dropped views: the render thread skipped a main loop
   frame; repeated: it drew the last one again. Latency is measured on cockpit frames only
   (`latency -` without any).

### Copy ownership and shutdown

Leaving a posed cockpit for a menu or a lost session restores the loaded track's own exposure
adaptation rate and normal size-cull factor on the next camera hook, before its flat-path early
return. The frustum hook can restore them without a valid director or a new view. Ordinary flat
play never applies or restores those cockpit adjustments.

A swapchain wait timeout does not make an image writable. The bridge keeps that
acquisition and retries its wait on the next frame, without copying or releasing
the image until the wait succeeds. A session error stops new copies. Submitted
copies retain their guest-image holds until their fences complete, including when
releasing the image to OpenXR fails.

Resize and session cleanup retain all resources while a copy fence times out;
cleanup retries rather than destroying an in-use image, command pool or fence.
Vulkan device loss permits resource cleanup under the Vulkan lifetime rules and
stops further copies.

App close joins the OpenXR worker before the SDK destroys its presenter and
Vulkan device. The worker is never detached with a raw SDK device pointer.
OpenXR calls such as `xrWaitFrame`, `xrEndFrame` and `xrCreateSession` have no
application-controlled cancellation timeout: a runtime stalled inside one of
them can prevent safe shutdown. The three-second warning is diagnostic, not a
shutdown deadline. This does not terminate the process or discard unsaved data.

The actual bridge can be checked with mock SDK/OpenXR/Vulkan calls, without a
runtime or headset:

```sh
cmake -S forza-recomp/tests/xr_lifecycle -B out/xr-lifecycle \
  -DFORZA_VULKAN_INCLUDE_DIR=/path/to/vulkan-headers/include
cmake --build out/xr-lifecycle
ctest --test-dir out/xr-lifecycle --output-on-failure
```

These fixtures cover timeout/retry ownership, fence-deferred destruction,
session/device loss, failed submission/release, stop and join-before-teardown.
They also compile the actual camera hook for base and TU4, checking cockpit-to-flat restoration,
frustum-first transitions without a director, repeated transitions and the untouched flat baseline.
They do not qualify a physical headset or comfort.

### Tested without a headset (Linux, TU4, RX 9060 XT, RADV)

- **VR off:** no OpenXR library mapped, no `VR:` line, frame rate as before (the off paths are
  one relaxed atomic load per camera call, swap and guest output refresh).
- **No runtime** (`vr_mode=cockpit`, no `active_runtime.json`): `VR: no OpenXR runtime is
  installed or active (XR_ERROR_RUNTIME_UNAVAILABLE) ... - playing flat`, the loader unloaded.
- **WiVRn selected (`XR_RUNTIME_JSON` = the Flatpak's `openxr_wivrn.json`), server not running:**
  WiVRn's client cannot connect to `$XDG_RUNTIME_DIR/wivrn/comp_ipc`; `xrCreateInstance` fails:
  `VR: the OpenXR runtime did not start (XR_ERROR_RUNTIME_UNAVAILABLE) ... - playing flat`.
- **WiVRn server running (user space: `flatpak run --command=wivrn-server io.github.wivrn.wivrn
  --no-manage-active-runtime --no-publish-service`), no headset:** WiVRn's `xrCreateInstance`
  blocks until a headset connects (the first build hung at start). Now: `VR: waiting up to 20 s
  for the OpenXR runtime`, then `VR: the OpenXR runtime did not answer in 20 s (WiVRn waits until
  the headset connects ...) - playing flat`; the game starts flat and exits cleanly.
- **A mock OpenXR runtime** (a scratch test library, not in the repository: `XR_KHR_vulkan_enable2`
  that adds instance and device extensions as Monado does, a 90 Hz frame clock, a head that
  turns 25 degrees left and right every 6 s, checks of every call and layer, dumps of the shown
  image): screen mode put a 3.2 x 1.8 m quad at 2.5 m with the game's picture (colours as on the
  desktop, so the gamma is right); in cockpit mode (free roam, the in-car view) the window showed
  the view turning with the simulated head inside the car at 114 x 140 degrees, the runtime got
  projection layers with that field of view, all swaps were tagged in 10 s windows spent in the
  car (281/281, 278/278), the submitted pose was within 0.1 degree of the head at display (the
  mock's prediction is exact; a real runtime's is not), switching to the hood camera went back to
  the screen, both sticks recentred, a session loss and no headset fell back to flat, and closing
  the game ended the session (`xrRequestExitSession`, stopping, idle, exiting, destroyed). The
  mock's checks found no error. The game held its 28-30 fps; `xrEndFrame max` read about 230 ms
  there because the mock waits for the GPU while it dumps a picture every 3 s.
- **Packaging:** the loader built as `tools/build_rexglue_sdk.sh` builds it (OpenXR-SDK 1.1.62)
  works with the game; in a freedesktop 26.08 Flatpak sandbox with the manifest's new permissions
  that loader found the WiVRn Flatpak's runtime and connected to its server (`xrCreateInstance`
  waiting for the headset); without `xdg-run/wivrn` the connection fails.
- **Unit tests:** `tests/xr_math_test.cpp` (OpenXR to game axes against the test pose's yaw,
  pitch and roll, recentring, seat height, the covering field of view, the latency estimate),
  `tests/xr_frame_tags_test.cpp` (tags, matrix matching, the swap tagger, layer choice, the swap
  packet), `tests/vr_head_pose_math_test.cpp`; all three run in CI.
- **Windows:** the game's and ReIGlue's changed sources compile for Windows (syntax
  check with the cross toolchain); not run.

Only the headset can tell: WiVRn creating the Vulkan instance and device for the game's device
and its session starting; the screen's size and sharpness; the colours; smoothness at the
headset's rate with the game at 30 fps; whether holding the queue lock in `xrEndFrame` stalls the
game (`xrEndFrame max` in the 10 s line); how far the head prediction is off and whether the
5 degree margin hides the picture's edge when the head turns; the HUD's place; comfort.

### The SDK side (ReIGlue 0390-0397)

The SDK changes VR needs are commits 0390-0397 of
[ReIGlue](https://github.com/FH1-ReImagined/ReIGlue), the project's fork of the ReXGlue SDK, from
tag `fh1-0.10.0.24-r2` on (included in the revision pinned by `tools/build_rexglue_sdk.sh`). Each commit's message
says what it changes and why (the same text as their rows in [sdk_patches.md](sdk_patches.md));
ReIGlue's `docs/reiglue/commits.md` lists them as Forza-specific, area VR. The game looks them up at
run time (C exports of the runtime and the GPU plugin) and plays flat, or without the missing part,
when they are not there.

| # | What |
| --- | --- |
| 0390 | Vulkan created through the OpenXR runtime: instance, physical device and device hooks |
| 0391 | The guest output for a second consumer, with a frame tag from the swap packet |
| 0392 | Pads and wheels without window focus |
| 0393 | The scene before the HUD and the HUD alone, with each frame |
| 0394 | Both eyes from the scene and its depth (depth stereo) |
| 0395 | The headset's picture sharpened (contrast-adaptive) |
| 0396 | True stereo: the scene's commands executed once per eye |
| 0397 | When each guest output frame was published |

### Packaging

- **Linux package and Flatpak:** `tools/build_rexglue_sdk.sh` builds `libopenxr_loader.so.1` from
  OpenXR-SDK 1.1.62 (pinned SHA-256) into the overlay's `lib/`, `tools/package_sdk_overlay.sh`
  packs it, and the launcher copies it next to the game (`builder.stage_runtime`;
  `tools/package_linux.sh` for a hand-made package). The game's `LD_LIBRARY_PATH` holds the game
  folder, so it is found first; without it the system's loader is used (Fedora `openxr-libs`,
  Ubuntu/Debian `libopenxr-loader1`, Arch `openxr`). The freedesktop 26.08 runtime has none.
- **Flatpak permissions** (manifest): `xdg-config/openxr:ro` (the active runtime manifest),
  `/var/lib/flatpak/app/io.github.wivrn.wivrn:ro` (the runtime library of a system-wide WiVRn
  Flatpak; a user install is under the home folder, already readable), `xdg-run/wivrn:ro` and
  `xdg-run/monado_comp_ipc` (the compositor sockets). Inside the sandbox `XDG_CONFIG_HOME` is the
  app's own folder, so the launcher sets `XR_RUNTIME_JSON` to the host's
  `~/.config/openxr/1/active_runtime.json`. Runtimes installed on the host in `/usr` (Monado,
  WiVRn from distribution packages) cannot be loaded in the sandbox (its `/usr` is the runtime's);
  SteamVR's Linux runtime lives under the home folder but was not tried.
- **Windows:** no runtime installs the loader; `openxr_loader.dll` must sit next to `forza.exe`.
  The release workflow's sdk-windows job (and `tools/build_windows.sh`) puts it into the
  overlay's `bin/` from `openxr_loader_windows-1.1.62.zip` of the KhronosGroup/OpenXR-SDK-Source
  release (pinned in `launcher/forza_launcher/pins.py` as `openxr-loader-windows`, SHA-256
  `800ec772e2f9448a26ab9f579f4914d984346dd9d0d7c007841abe21d2c8ff2f`, `x64/bin/openxr_loader.dll`,
  Apache-2.0, its licence in `share/licenses/openxr-loader/`), and the launcher copies every DLL
  of that `bin/` next to `forza.exe`, so the installer's build carries it. The game build's
  `licenses/openxr-sdk.txt` covers it. The launcher forces `--gpu_backend=vulkan` when the
  setting is on. Built in CI only: not run with a headset (SteamVR, Meta Quest Link, Virtual
  Desktop) yet.

## Testing on a Quest 3 with WiVRn

1. **Build** this branch: the SDK overlay from ReIGlue `fh1-0.10.0.24-r2`, which has 0390-0397
   (`tools/build_rexglue_sdk.sh`, which also builds the OpenXR loader) and the game as usual, or
   let the launcher build it. `libopenxr_loader.so.1` is also installed on this PC
   (`/usr/lib64`), so the loader is there either way.
2. **Start WiVRn** on the PC (the WiVRn app from the Flatpak `io.github.wivrn.wivrn`), put the
   Quest 3 on, open WiVRn on the Quest and connect to the PC. Wait until the Quest shows that it
   is connected and waiting for an application. While it runs, WiVRn makes itself the active
   OpenXR runtime (`~/.config/openxr/1/active_runtime.json`). Connect first, then start the
   game: WiVRn makes the game wait until a headset is there (20 s, then the game starts flat).
3. **Launcher:** Settings > Display > VR (experimental) > **VR headset (experimental)**:
   **Virtual screen**. Press Play. For the trace, add `--vr_trace=true` to the extra arguments.
4. **What you should see:** the game in its window on the desktop as always, and in the headset a
   screen 2.2 m wide 2.5 m in front of you at eye height (47 degrees: the whole picture in view
   without turning the head) with the game on it, smooth when you move your head. Press **Home**
   on the keyboard, or hold **both sticks pressed** for a second, to bring the screen in front of
   you again. The pad works while you wear the headset, whichever window has the focus.
5. **Cockpit:** close the game, set **VR headset (experimental)** to **Cockpit**, Play, sit as
   you would drive. Load into free roam, press **Home** (or hold both sticks) once while looking
   straight ahead, then switch to the in-car view (**RB** cycles the cameras; the one with the
   driver's hands on the wheel). You should be inside the car: turning and moving your head
   looks around the interior (door, pillars, mirrors, the roof); the world outside updates at
   the game's frame rate while the head motion stays smooth. The car's interior stays put at any
   speed: no shake, no lean in corners, no zoom at speed. Both eyes see the same picture (depth
   stereo is an option, `vr_stereo=depth`), so the cockpit looks large and flat. Turned round you
   see your seat from the front (the driver has no body), from at most 15 cm behind where you
   recentred. The HUD is on its own panel below the windscreen (version 3); paused or in a menu,
   the picture goes to the screen. Other cameras and the map are on the screen too. **Seat height
   (cm)** moves the eye up or down (default 4 cm below the game's driver's eye); recentre after
   changing it. Try a drive.
6. **Send back** `forza.log` (launcher: Play page > Open log folder; by default
   `~/.local/share/forza/forza.log`), or at least `grep -n "VR\|Vulkan device\|Input: pads\|rror"
   forza.log`. The useful lines: `VR: OpenXR runtime WiVRn ...`, `VR: headset ...`, `Vulkan
   device chosen by the creation hook (OpenXR runtime): ...`, `VR: session created ...`, `Input:
   pads and wheels work while the window has no focus`, `VR: session focused`, `VR: eye fields of
   view ...`, `VR: the cockpit view is drawn N deg wide x N tall ... N x N pixels per degree in
   the middle`, `VR: swapchain ...`, `VR: the in-car camera's camera physics are off ...`, the
   `VR: N Hz headset, ...` line every 10 s (in the car: cockpit frames, swaps tagged N/N, latency
   with its game and runtime parts, xrEndFrame max), the `VR trace:` lines, and a `... - playing
   flat` line if VR gave up. If the session did not start, WiVRn's own log (the WiVRn app) too.
   And what you saw: the screen's size and sharpness; the cockpit's sharpness; stutter; whether
   anything still moves the view besides your head; what you see when you look back; black edges
   when turning the head quickly; where the HUD was.

## Version 2: after the first headset test

The first test (Quest 3 over WiVRn, cockpit, render scale 2, 72 fps, 25 October 2026) worked
first time but was not playable. What was reported, what caused it and what changed:

| Report | Cause | Change |
| --- | --- | --- |
| h. Camera shake at high speed, very nauseating | The in-car camera's `CameraPhysics.xml` layers (`DriverCam`, 51 layers): Simplex and Perlin noise on the camera's yaw, pitch, roll and position driven by `SpeedMPH`, collision jolts, surface bumps | Camera physics off for the head-posed camera (below) |
| i. The camera "changes angles a lot" | The same layers: G-force lean and tilt (`Gs_Lateral` turns the view 0.35 degrees per g toward the corner and rolls it 1.3, `Gs_Longitudinal` pitches it and moves the eye), slip, a constant 1.1 degree pitch, and 8 degrees more field of view at speed. All of it on top of the head pose | Same |
| a. The pad stops working once in the headset | The SDK gives a neutral pad while the window lacks focus (`ReXApp`'s input active callback); the desktop window loses it when the player looks at the headset (`Window lost focus` in the log) | ReIGlue 0392: pads and wheels work without focus while the session runs; the keyboard stays focus-bound |
| b. Very blurry | The cockpit view was 120 degrees tall, so 144 wide at 16:9, for eyes that see 108 x 99 together: 69 % of the picture's pixels were outside both eyes, 7.3 pixels per degree in the middle | The view covers exactly the eyes plus the 5 degree margin: 123 x 109 degrees around an axis 5.5 degrees down, with its own projection aspect: 12.0 x 9.0 pixels per degree in the middle at render scale 2, 62 % of the pixels inside the eyes (the rest is the margin) |
| b. Cockpit latency 70-95 ms against 40 on the screen | The 40 was never measured: the estimate starts at 40 ms and only cockpit frames (tagged with the time their camera was built) measure it. The 70-95 ms is the game's own pipeline plus WiVRn's; ours was the prediction, capped at 50 ms, 30 ms short | Predicted as measured up to 100 ms; the latency is split into the game's and the runtime's part in the 10 s line and the trace; `latency -` without cockpit frames |
| d. Looking back about 180 degrees "changes the position" | Turning round moves the head back (the eyes pivot round the neck, the shoulders turn). The driver's seat is about 20-25 cm behind the game's eye point (the test save's Koenigsegg): from about 15 cm back the near plane cuts through the seat and the view lands inside it, which looks like another camera. The camera itself stays `CCamDriver`, posed (trace) | The eye stops 8 cm behind where it was recentred (`vr_head_back_limit_cm`) |
| c. HUD outside the view and giant; menus giant on the screen | The screen was 3.2 m at 2.5 m: 65 degrees | 2.2 m: 47 degrees; the HUD in the cockpit: below |

### Camera physics off in the VR cockpit (h, i)

Each frame the director's step (`sub_82855070`, TU4 `82905418`, from the director update
`sub_82858288`, TU4 `82908710`) has the active camera build its own view (vtable +24: for
`CCamDriver` (TU4 `82901D58`) a look-at from the driver's eye on the car body, along the car's
forward turned by the look-around angle, with the car's up), its field of view (+28) and its
position (+40), then calls `sub_82853DD0` (TU4 `82904110`) with the camera, the view matrix, the
FOV and the position. That function applies what the camera's physics layers computed this frame
(camera + 48 ... + 112, written by the camera's update): yaw/pitch/roll in camera and car space,
car-space position offsets along the car's axes, a forward offset, the FOV offset, and rebuilds
the matrix (`sub_828FD278` in TU4, a look-at from the new position, forward and up).

The hook on `sub_82853DD0` returns at once while the head pose owns the camera: the VR cockpit
with an in-car camera (`CCamDriver`, `CCamCockpit`), unless `vr_camera_effects`; or the test
pose (`vr_head_pose_test` with a pose set). The view stays the camera's own, rigid with the car
body (which still pitches and rolls on its suspension, as a real seat does); the FOV is the
camera's own (62 degrees, which the VR cockpit replaces); the layers keep running, so switching
back is smooth. Other cameras keep their effects.

Checked with the emulated headset (`scratchpad/xremu`, Monado with a scripted head): the same
drive (reverse out of the outpost's parking, accelerate to about 80 km/h, into the fence), 12
game pictures 0.3 s apart, the dashboard band (rows 70-95 %, below the windscreen) matched
between consecutive pictures by shifting it: with `vr_camera_effects=true` (version 1's
behaviour) it moved by up to 36 x 12 pixels (about 3 x 1.3 degrees) from one picture to the next,
with the default by 0 in all 11 pairs, through the crash too.

### The cockpit's field of view (b)

`CoverEyes` (`xr_math.h`) takes both eyes' fields of view and orientations relative to the head
(`xrLocateViews` in `VIEW` space, so canted displays are handled) and returns the drawn frustum:
the axis at the middle of the eyes' extreme angles (Quest 3: 0 degrees across, (44 - 55) / 2 =
5.5 degrees down), and the half-angles that cover every corner of both eyes' frusta seen from
that axis, plus `vr_fov_margin`. Pitching the axis widens the corners a little (Quest 3: 56.7
instead of 54 degrees across), but it drops 11 degrees of height: on balance more pixels per
degree on both axes than a straight axis (13.4 x 7.3).

The game then draws with the head turned to that axis (the camera hook composes the turn into
the head transform), the vertical field of view in the director's +304 and the aspect from the
viewport query (`sub_823E22C8`, whose hook returns it while a posed view is handed over: the
presentation camera's `SetAspectRatio` and the game-side frustum both ask it inside the two
readers). The picture keeps its pixels (2560 x 1440 at render scale 2), so they are not square:
123 degrees across 2560 pixels, 109 down 1440. The projection layer gets the turned head pose
and the frustum's symmetric field of view; the runtime maps the picture onto it.

| | Version 1 | Version 2 |
| --- | --- | --- |
| Drawn (Quest 3 eyes L -54/40/44/-55, R -40/54/44/-55, margin 5) | 144 x 120 degrees, straight | 123 x 109 degrees, axis 5.5 down |
| Pixels per degree in the middle, render scale 2 | 7.3 x 7.3 | 12.0 x 9.0 |
| Pixels inside either eye | 31 % | 62 % (90 % without the margin) |

The headset itself has about 16 pixels per degree in the middle at the recommended 2064 x 2162
per eye: render scale 3 would come closer (18 x 13.5), at the GPU cost of 2.25 times the pixels.
The ultrawide machinery ([ultrawide.md](ultrawide.md)) can also draw at another raster aspect,
but only wider than 16:9, Vulkan only, and from the desktop window's size: the aspect query is
the part that fits here. In the emulated headset with the same eyes the layer reads `fov_deg l
-61.74 r 61.74 u 54.50 d -54.50`, pitched 5.5 degrees down (`quat_xyzw -0.048 0 0 0.999`), and
the eye views match the game's picture at every pose of the standard sequence (straight, yaw
45/90/180, pitch, roll, leans).

### Pads without window focus (a)

ReIGlue 0392 adds `rex::input::SetPadsWithoutFocus` (C export `rexinput_SetPadsWithoutFocus`).
The OpenXR thread turns it on when the session begins (`Input: pads and wheels work while the
window has no focus`) and off when it stops or is destroyed (`... need the window's focus
again`). It also sets SDL's background joystick events hint (SDL ignores presses while no window
has keyboard focus; the wheel driver sets the same hint when `wheel_mode` is on, the default).
The keyboard and mouse driver checks the focus itself, so typing in another window does not
drive the car. Without it the game logs that the pad needs the focus (click the game
window before putting the headset on). Not checked with a real pad in a headset session: the
emulator runs drive through the test FIFO, which does not go through the SDK's drivers.

### Latency (b)

The `latency` of the 10 s line is the time from the camera hook building a posed view to the
display time of the headset frame that first shows it. Measured in the emulator at 30 fps: about
88 ms of game (the camera is built at the start of a main loop frame, the render thread draws it
during the next one, then the GPU and the swap; plus up to one headset frame until the OpenXR
thread takes the picture) and 10-11 ms of runtime (Monado, no network). At 72 fps the game's part
should be near 40 ms; WiVRn adds encoding, the network and decoding. So the 70-95 ms of the
first test is mostly not ours. What was ours: the pose was predicted at most 50 ms ahead, so the
picture was drawn for a head 20-45 ms older than when it was shown, and the runtime reprojected
the difference (rotation only: head translation in that time made the cockpit swim). The cap is
100 ms now. Next, if needed: re-pose the view on the render thread when it takes the matrix
(`sub_823EB7A0`), about one game frame later, which would also need the render-side view state
(`sub_82DBADC0`) re-posed.

### Looking back (d)

The emulated head turned 90, 135, 170, 180 and 190 degrees, over each shoulder (150 degrees and
15 cm aside, 10 cm back), and at 180 degrees moved 0, 5, 10, 15 and 20 cm back. The camera stayed
`CCamDriver`, posed, with the game's look-around angle at 0 (trace). At 180 degrees the view is
the driver's seat seen from the front, filling the view (there is no body); 15 cm back the near
plane (0.1 m) cut through the seat, 20 cm back the view was inside it (dark, the rear bulkhead):
another position, as reported. With the 8 cm limit the views at 15 and 20 cm back are the 8 cm
view. Sideways and upward moves are not limited (at 180 degrees, 15 cm aside or 10 cm up shows the
seat's side or top). The trace shows the head's position, so a real look back shows how far it
went.

### HUD and menus (c)

The screen is 2.2 m wide at 2.5 m: 47 x 27 degrees, the corners 24 degrees from the middle, within
an eye turn without turning the head, about what a cinema seat gives (THX recommends 36-40
degrees). The menus, drawn for 1280 x 720, get 27 of their pixels per degree, a little more than
the headset shows in the middle (about 16 at WiVRn's recommended size, 25 for the panel), so text
is not magnified into blur; the copy for the screen is halved from render scale 2 so that the
runtime's unfiltered sampling does not shimmer. Version 1's 65 degrees needed head turns to read
the corners.

In the cockpit the HUD is drawn into the picture at its usual places. With the new field of view
the map spans 48-59 degrees left and the speedometer 48-59 degrees right of the view's middle,
in the lower corners: each at the outer edge of one eye only (the left eye sees to 54 degrees left,
the right eye to 54 right), mostly in the margin and following the head. The narrower view brings
them nearer (version 1: 60-72 degrees, outside both eyes) but not into a readable place. The
game's HUD options can hide them; the car's own gauges are readable in the cockpit. A HUD quad
of its own needs the scene/HUD split per frame (not done).

### The trace (`vr_trace`)

`--vr_trace=true` (or `vr_trace = true` in `forza.toml`) logs from the OpenXR thread about 10
times a second:

```
VR trace: head yaw 180 pitch 0 roll 0 deg, x 0.0 y 0.0 z -15.0 cm; camera CCamDriver 820860FC
look 0 deg, posed; shows cockpit 123x109 deg pitch -5.5; latency 99 ms (game 88 + runtime 11)
```

(one line in the log; about 8 MB an hour). The head relative to the recentred pose at the frame's display time: yaw
left, pitch up, roll left in degrees, x right, y up, z forward in cm (the game's axes; z -15 is
15 cm back). The game's active camera (class from its vtable, or `?`; `no game view` when the
camera hook has not run for 0.5 s: menus, loading) with its look-around angle (the right stick)
and whether the head pose went into it. What the headset shows (cockpit with the frustum drawn,
or screen) and the last cockpit frame's latency in its two parts.

### Not done in version 2

- **Motion blur and depth of field** stay on. The render scenario files (`renderscenarios.zip`,
  e.g. `Horizon_Race.xml`) switch them (`MotionBlur`, `DepthOfField`), read through the game's
  reflection classes (setting ids 4 and 3, vtables at `0x8223C1CC` and `0x8223C1F8` in the disc
  executable); where the renderer reads the value was not found yet. Motion blur reprojects with
  the previous frame's camera, so quick head turns smear the world.
- Stereo, a HUD quad, late latching on the render thread, Windows with a headset.

### Tested (version 2, Linux, TU4, RX 9060 XT)

- **Emulated headset** (`scratchpad/xremu`: Monado 25.1 with a scripted head, the first test's
  eye fields of view, 1032 x 1081 pixels per eye, 90 Hz; its timing is not real): cockpit mode at
  render scale 2 with the trace on: the standard pose sequence (straight, yaw 45/90/180, pitch 30
  up and down, roll 20, leans 15 cm, forward 10, up 5), the look-back sequences above, the drive
  with and without camera effects, the chase and hood cameras on the screen (`quad_size 2.200
  1.237`, the picture halved to 1280 x 720 on the way). Clean session start and end.
- **VR off** on scratch data: no `VR` line, no OpenXR library mapped, the in-car view as before,
  clean exit.
- **Unit tests:** `xr_math_test` (the covering frustum for the Quest 3's eyes, canted and lopsided
  eyes, caps, the turned view in OpenXR and in the game, yaw/pitch/roll, 180 and -180 and q/-q,
  the back limit, the latency cap), `xr_frame_tags_test`, `vr_head_pose_math_test`, the launcher's
  `test_vr.py`.

## Version 3: after the second headset test

The second test (Quest 3 over WiVRn at 90 Hz, cockpit, render scale 2, 72 fps, 25 October 2026, with
the trace) confirmed that the shake and the lean were gone. What was still wrong, what caused it and
what changed:

| Report | Cause | Change |
| --- | --- | --- |
| 1. Turning the head about 90 degrees to see a mirror or a window "changes to another camera", very nauseating | Not a camera switch: the trace has `CCamDriver`, posed, look 0 throughout. The eyes turn round the neck: at 60-80 degrees the trace has the head 6-12 cm aside and 7-10 cm back. In the test car the driver's eye is a few centimetres under the roof, which slopes down to the sides and to the back: 7 cm aside and 8 cm back toward the driver's door (or 10 cm up) the eye leaves the cabin through the roof, the roof and the pillar vanish and the view looks like an outside camera's | The eye is held in half an ellipsoid round where it was recentred (10 cm aside, 8 back, 6 up; forward and down free), eased toward its surface; the head-tracked view's near plane is 2 cm instead of the camera's 10 |
| 2. The pause menu, speed trap notices and skill points cannot be read in the cockpit | Menus and the HUD were drawn into the 123 x 109 degree cockpit picture: the pause menu runs off the eyes, the HUD sits at their outer edges | Paused or in a menu, the in-car view is not head-posed and the picture is on the screen; while driving the HUD is on its own panel in front of the seat (ReIGlue 0393) |
| 3. Hard to see what is ahead; sharper on a monitor | The 123 x 109 degree view gets 12.0 x 9.0 pixels per degree at render scale 2 (a monitor's 62 degree in-car view about twice that) | The launcher's VR picture preset draws more rows than columns (below); measured on the RX 9060 XT, only 2 x 2 holds 72 fps |
| 4. No depth | Both eyes got the same picture | Each eye made from the picture and its depth (ReIGlue 0394, `vr_stereo`; off by default since version 4) |

### The eye stays in the cabin (1)

Reproduced in the emulated headset (`scratchpad/xremu`, the user's eye fields of view, the test
save's Koenigsegg, standing at the outpost) with the trace's poses: turned 70 degrees right (toward the
driver's door: the car is right-hand drive) with the head 9 cm aside and 9 cm back, the roof over the
door was cut away and the sky showed through; at 90 degrees, 10 cm aside and 10 back, the whole roof was
gone (a black bar, the roof's edge, with the sky above it). A sweep at 90 degrees right and 8 cm back:
3 and 5 cm aside kept the roof, 7 cm lost part of it, 10 cm all of it; 10 cm aside without moving back
kept it, and so did 5 cm up while 10 cm up cut it. The near plane is not the cause: at 2 cm the same
poses showed the same holes (the eye is above the roof's inside, not just near it). The trace had
`CCamDriver`, posed, look 0 at every step.

- **The head box** (`xr_math.h` `LimitHead`): the eye's offset from where it was recentred, with the
  sideways, backward and upward parts scaled by the half-axes 10, 8 and 6 cm (`vr_head_side_limit_cm`,
  `vr_head_back_limit_cm`, `vr_head_up_limit_cm`), has a radius; up to 0.8 of the surface it is
  unchanged, beyond that it is eased (a tanh knee) so it approaches the surface without a hard stop, the
  three parts scaled alike (the direction is kept). Forward (toward the wheel) and down are free. Every
  point on the surface was checked to keep the roof (90 degrees right with 7.1 aside and 5.7 back, 8.7
  and 4, 5 and 6.9; 60 and 120 degrees; 6 aside and 3.6 up). Version 2's 8 cm back limit is the same
  half-axis, now eased.
- **The near plane**: the hook on the director's near-plane accessor (`sub_8284EF98`, TU4
  `sub_828FF268`) answers 2 cm (`vr_near_plane_cm`) inside a posed view's hand-over, for both readers
  of the matrix. The game's depth is reversed and floating point (the shadow passes decode view z =
  0.02 / depth), so the closer plane costs no precision. It keeps door cards, the wheel and the mirror
  housing whole when the head leans close to them.
- The trace now also prints where the eye was held: `head ... x 7.9 y 0.0 z -8.4 cm (eye held at x 6.0
  y 0.0 z -6.4 cm)`.

The user's poses on version 2 and 3 side by side: `shots/item1_side_v2_v3.png` (scratchpad).

### Menus on the screen, the HUD on a panel (2)

**Menus.** The world update's pause flag (`hooks/telemetry.cpp`, the wheel's `PlayerGate`: the pause
and rewind menus, the map, the start menu, loading) turns the cockpit off for as long as it lasts: the
camera hook does not pose the in-car view, the game draws its own 62 degree view under the menu, and the
picture goes to the virtual screen (2.2 m at 2.5 m, 47 degrees) where the menu reads as on a monitor.
Back in the game the cockpit returns within the gate's three frames. Log: `VR: the game is paused or in a
menu: the picture is on the screen` / `VR: the game runs again: the in-car view follows the head`; the
trace says `shows screen (paused or menu)`.

**The HUD's own panel (ReIGlue 0393).** The scene/HUD split per frame, as planned (patch B): the HDR
path already copies the front buffer just before its first HUD draw (`CaptureHdrScene`, the 1280-pitch
`2_10_10_10` composite before the first `2_10_10_10_AS_10_10_10_10` draw); with the VR flag it does so
with HDR off too. In the swap refresher, after the guest output's own gamma pass, the same gamma pipeline
runs again from that copy into the frame's scene image (so the ramp, fades and the channel order match the
guest output), and a compute pass recovers the HUD alone from the two pictures: the game blends the HUD
over the scene (frame = scene x (1 - a) + colour x a), and for each pixel the least coverage a that explains
the change with a colour in [0, 1] is taken (a dark translucent backdrop comes out black at its own
opacity, white text white, with no copy of the scene behind it), then box-filtered in premultiplied
colour down to the HUD's own 1280 x 720. The OpenXR thread copies the scene into the cockpit's swapchain
and the HUD into its own, and submits the HUD as a quad over the projection layer
(`XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT | UNPREMULTIPLIED_ALPHA_BIT`), fixed in front of the
recentred seat: 0.9 m wide, 1 m ahead, its middle 14 degrees below the eyes (`vr_hud_width`,
`vr_hud_distance`, `vr_hud_pitch`): 48 x 27 degrees, the top edge at the horizon, notices and skill
points in its upper middle, the speedometer and map in its lower corners about 21 degrees aside and
24 down. `vr_hud=false` keeps version 2's HUD in the picture.

### The picture's sharpness (3)

The cockpit view is 123 x 109 degrees drawn into the game's 16:9 picture: at render scale 2 (2560 x
1440) that is 12.0 pixels per degree across and 9.0 down in the middle, while the headset shows
about 16. The launcher's **VR picture** (Display > VR) draws the cockpit at a draw scale per axis,
more upward than across, so its pixels are about square in degrees: Sharp (2 x 3, 2560 x 2160, 12 x
13) and Sharpest (3 x 4, 3840 x 2880, 18 x 18); "As the Display settings" (the default) keeps Render
scale. GPU time a frame on the RX 9060 XT at the second test's settings (free roam in the cockpit,
standing; `scratchpad/s333v3/bench.sh`, the game's DRM engine time over 20 s, the benchmark lock
held):

| Draw scale | VR cockpit | Flat |
| --- | --- | --- |
| 2 x 2 (2560 x 1440) | 11.7 ms, 72 fps | 11.6 ms, 72 fps |
| 2 x 3 (2560 x 2160) | 17.9 ms, 55 fps | 16.5 ms, 60 fps |
| 3 x 4 (3840 x 2880) | 34.0 ms, 29 fps | 31.5 ms, 32 fps |

Only 2 x 2 holds 72 fps on this card (84 % of the 13.9 ms frame); 2 x 3 needs a faster one. Without
MSAA 2 x 3 is no cheaper (18.1 ms): the cost is the pixels' shading.

### Depth stereo (4)

ReIGlue 0394 ([the SDK side](#the-sdk-side-reiglue-0390-0397)): the main view's depth pre-pass, decoded to view
distance at the frame's first sun shadow pass, moves each scene pixel by its disparity for each eye
(half the headset's eye distance times the focal length over the distance, capped at
`vr_stereo_max_shift`, 3 % of the width), the nearest winning; the holes beside near objects are
filled from the farther side. The projection layer gets one half per eye, each at its eye's pose.
It costs 0.3 ms of GPU a frame at 2 x 2 (11.69 against 11.39 ms mono). The third test switched it
off ("full of artifacts"); version 4 makes it off by default (below).

## Version 4: after the third headset test

The third test (Quest 3 over WiVRn at 90 Hz, cockpit, render scale 2, 72 fps, depth stereo off, 26
October 2026, with the trace): the HUD panel works, the flick of version 2 ("another camera") is
gone, smoother; "a lot more to go":

| Report | Cause | Change |
| --- | --- | --- |
| 1. Looking around the car, the camera moves and the lighting of the whole car moves | The lighting: the game's eye adaptation measures the whole picture, so with the cockpit's wide view a turn toward a bright window or the dark footwell re-exposes the cabin, at 72 fps within a fraction of a second (measured: a 45 degree turn makes the cabin 28 % brighter or 17 % darker in about a second). The camera: version 3's eye box held the eye 2-4 cm short of the head at every look toward a door or a mirror, so the cabin moved with the head | Exposure adapts 50 times slower in the head-tracked view (`vr_exposure_speed` 0.02: the same turn changes it by about a tenth); the eye is 4 cm lower (`vr_seat_height_cm` -4) and follows the head 1:1 aside and back (eased only beyond 15 and 13 cm), only how far it may go up is limited near the roof's slopes |
| 2. Cars pop in right in front; draw distance bad | The renderer's object size cull uses the view's field of view: the cockpit's 109 degrees made every object 2.3 times smaller on the picture than the in-car camera's 62, so cars, signs and posts were dropped at 1/2.3 of their distance (41 items a frame standing at the festival, 9 with the fix), and small objects' shadows too | The cull runs with the in-car camera's own factor in the VR cockpit (`vr_cull_scale`): a drivatar's car 60-80 m away behind the guard rail, missing before, is drawn |
| 2. Still blurry | Mostly the stream: WiVRn encoded each eye at 1472 x 1536 (0.71 of the headset's 2064 x 2162 on each axis) with H.264 8-bit at 24.6 Mbit/s (its server log); the game's picture gives 12.0 x 9.0 pixels per degree and nothing sharpens it for the headset | The headset's picture is sharpened (ReIGlue 0395, `vr_sharpen` 0.5, no measurable GPU time); WiVRn settings below |
| 3. Depth stereo full of artifacts | The depth warp's filled edges and its wrong depth for glass, particles and the HUD-less transparencies | Off by default (`vr_stereo` off; `depth` stays an option); true stereo: evaluated, plan below |
| 4. The emulator does not look like the headset | `scratchpad/xremu` had 1000 x 1080 eyes, no video stream and no lens | `scratchpad/xremu2` (the Quest 3's eyes at full size, captures at full size, a WiVRn stream and lens model), `scratchpad/questcap` (records the headset's own picture over USB) |

New settings (cvars; the launcher's Seat height default follows):

| cvar | Default | What |
| --- | --- | --- |
| `vr_seat_height_cm` | -4 (was 0) | The eye 4 cm below the game's driver's eye, which sits just under the roof |
| `vr_head_side_limit_cm`, `vr_head_back_limit_cm`, `vr_head_up_limit_cm` | 18, 15, 6 (were 10, 8, 6) | The eye's room: an ellipse on the floor, a dome over it (below) |
| `vr_exposure_speed` | 0.02 | How fast the exposure adapts while the head pose drives the camera, a factor of the game's own rate |
| `vr_cull_scale` | true | The size cull of the main and shadow views as for the in-car camera's field of view |
| `vr_sharpen` | 0.5 | Contrast-adaptive sharpening of the headset's picture, 0 to 1 (ReIGlue 0395) |
| `vr_stereo` | off (was depth) | `depth`: version 3's depth stereo |

### What moves when looking around (1)

**The trace.** The test's 3,453 head-tracked samples (`VR trace`, about 6 minutes in the car) show the
looks the user made: turned 45-70 degrees the head was 7-12.5 cm aside and 3.5-10.4 cm back
(the eyes pivot round the neck), up to 5 cm up when looking up. Version 3's ellipsoid (10 aside, 8
back, 6 up, eased from 0.8 of the way out) held the eye 2-4 cm short at every one of them (turned
62 degrees left: the head at x -9.9, z -6.2 cm, the eye held at x -7.8, z -4.9): the picture is
drawn from where the eye is held and the runtime only turns it to the head, so the near cabin (the
door at 40 cm, the pillar, the mirror) slid with the head by 3-6 degrees. Over the whole trace version 3 held the eye by more than 0.5 cm
in 5.9 % of the samples; version 4's box in 2.1 % (only where the head went up while aside or back),
with no hold at all at the 95th percentile.

**The roof.** In the emulator with the test save's Lamborghini Aventador (left-hand drive), the
trace's extreme poses without any limit: 66 degrees left looking up 19 degrees with the head 5.4 cm
aside, 3.8 up and 10.2 back cut the roof open (the sky through it); so did 90 degrees left with 10
aside, 5 up and 8 back even with the eye 4 cm lower. Aside and back without going up kept the roof
at 90 degrees either way (10 aside, 10 back), and turned round 12 cm back. So the up part is what
leaves the cabin. Version 4's box (`xr_math.h` `LimitHead`): the eye's sideways and backward offset
is free inside an ellipse 18 x 15 cm (eased from 0.85 of the way out; 15 cm back is where the seat
begins) and how far it may go up shrinks from 6 cm over the middle to nothing at the ellipse's edge
(a dome), so a head that goes up near the doors or the rear window is held low while turns are
1:1; with the 4 cm lower eye all six extreme poses keep the roof
(`shots/extreme_v4/sheet.png`, scratchpad `s333v4`; version 3's box against none:
`shots/user_box_vs_nobox.png`). The trace now also prints `picture drawn N cm away`: how far from
the head at display time the shown picture was drawn (the eye box's hold, the head prediction's
miss over the 80-90 ms latency, a test offset), the part the runtime cannot correct.

**The lighting.** Pure head turns in the emulator, the car standing at the festival, the game's
pictures compared through the rotation (`s333v4/re/rotcmp.py`: B's pixels looked up in A through the
turn, the cabin's lower half): straight against turned 45 degrees right and left and back, about a
second each (`seq/alt.txt`), at three adaptation speeds switched live (a FIFO test command):

| Exposure speed | Turned right 45 (toward the passenger seat) | Turned left 45 (the driver's window) |
| --- | --- | --- |
| 1 (the game's) | 1.28, 1.27 times as bright | 0.83, 0.82 |
| 0.05 | 1.11, 1.12 | 0.91, 0.98 |
| 0.01 | 1.09, 1.11 | 0.95, 0.98 |

With the game's rate the whole cabin re-exposes within the second the head is turned; held 8 s it
is fully adapted either way. The track's `ToneMapeDelay` (TrackSettings.xml `0.075`, track settings
struct +416, read every frame) is that rate: version 4 scales it while the head pose drives the camera
(`world_detail.cpp` `ScaleExposureAdaptation`) and puts the track's own back for other cameras and
menus. 0.02 (about 15 s to adapt instead of a fraction of a second) leaves a 45 degree glance about a tenth brighter or darker, as
the eye would; what is left at 0.01 (about 9 %) is the method's noise and the HUD in the compared
area. No other view-dependent lighting showed up: with adaptation slowed, the cabin's shading did
not change with the turn beyond that (shadows, reflections and specular are drawn from the same eye
point, which a pure turn does not move).

### Draw distance (2)

**The size cull.** The view setup (TU4 `sub_82E91710`) gives each view a cull record (camera
position +0, a factor k +16, the track's screen-area distance D +20 and D squared +24), and the size
test (`sub_8243FD70`, TU4 `sub_8246FE78`) keeps an item while k x distance^2 <= D^2 x size
([graphics-detail.md](graphics-detail.md)). Logged in the cockpit (`FORZA_VR_CULL_STATS=1`, a test
aid of `hooks/vr_culling.cpp`): k is 5.896 for the main view (D 350) and the shadow view (D 600 at
Ultra shadow detail), 3 for the cube map's faces (D 70) and 0.052 for the mirror (D 8): k = 3 tan^2
of the view's vertical half-angle (54.5 degrees: 1.965 x 3; the cube map's 45: 3). The in-car
camera's 62 degrees give 1.083, so in the VR cockpit an item was dropped at 1/sqrt(5.4) = 1/2.3 of
the distance it has on a monitor. Standing at the festival in 5 s: the main view dropped 14,760 items
(41 a frame); with the in-car camera's k 3,240 (9 a frame, the wider view also holds more items); the
shadow view 5,400 and 0. In the game's pictures switched between the two at the same spot
(`shots/cull7`, scratchpad `s333v4`): with the fix a drivatar's car (its name tag "RAY") on the road
behind the guard rail 60-80 m ahead and a post are drawn, without it they are not.

`hooks/vr_culling.cpp` hooks the size test: in the VR cockpit, for the records whose D is the track's
mainScene or shadow caster distance, an item the game dropped is tested again with k times tan^2 of
the in-car camera's vertical half-angle over the cockpit's (0.184; the camera hook computes it from
the director's own FOV at +304 before it writes the cockpit's), the same angular size as on a
monitor. Cube map and mirror are unchanged; other cameras, menus and flat play take the game's path
(one relaxed load). It draws about 100 more items a frame (2,755 draws against 2,658) for no
measurable GPU time (11.21 against 11.23 ms a frame).

**The other field-of-view readers** (issue #436: the director's FOV accessor `sub_8284EF90`, TU4
`828FF260`, has 7 callers). The two that the head pose already covers since version 1 (the
presentation camera's update `sub_825A9D20`, twice, and the game-side frustum `sub_829A4860`) get the
cockpit's FOV and aspect. The others read the camera's own 62 degrees: the split view's update
`sub_825AA358` (TU4 `82622558`) posts a camera message (id 7 to the object at TU4 `833CAAD4`: near,
far, position +320, forward +208, up +224, FOV, a constant, ...), the world update `sub_823E3218`,
and `sub_824A7E80` and `sub_82494E00` (TU4 `82514DB8`, `824C4EA8`; car and post-effect setup).
They carry the car's unposed axes, so they do not follow the head either way; nothing in the tests
pointed at them (the car above was the size cull). The renderer's car LOD distances
(`renderscenarios.zip` `CarLODTransDist` 15/25/50 m, traffic 22-100 m) are distances, not screen sizes.

### Sharpness (2)

Where the blur comes from, in the order the picture goes through (Quest 3 eyes L -54/40/44/-55,
R -40/54/44/-55 degrees):

| Stage | Size | Pixels per degree in the middle |
| --- | --- | --- |
| The game's cockpit picture, render scale 2 | 2560 x 1440 over 123 x 109 degrees | 12.0 across, 9.0 down |
| WiVRn's encoder, per eye (the user's sessions, its server log) | 1472 x 1536 | 11.6 across, 11.2 down |
| The headset's panel, per eye | 2064 x 2208 | 16.3, 16.1 (before the lens) |

and the stream is H.264 8-bit at 24.6 Mbit/s an eye at 90 Hz: 34 KB a frame for 2.3 million pixels.
So more game pixels across bring little until WiVRn streams more of them; down, the game's 9.0 is
the weakest.

What version 4 changes on the PC side, without GPU cost that matters:

- **Sharpening for the headset** (ReIGlue 0395, [the SDK side](#the-sdk-side-reiglue-0390-0397)): the desktop
  window's FSR/CAS present effect never reached the headset (the cockpit gets ReIGlue 0393's scene
  layer). A contrast-adaptive sharpening pass (after AMD FidelityFX CAS) runs on the scene layer
  after the HUD is recovered and before stereo and the hand-over, `vr_sharpen` 0.5. It costs
  no measurable GPU time (11.21 ms a frame with it, 11.23 without, at 2 x 2 in the cockpit).
- **No double resample on our side**: the cockpit picture is copied 1:1 into the swapchain (the
  runtime samples it once into its eye images); the screen is halved only for the virtual screen.
- **Not changed**: a texture LOD bias for the VR view (the game picks mips for its own picture's
  pixels, which is right for what is drawn; the user already runs Distant texture detail High),
  anisotropic filtering (the user runs 16x), the swapchain size (it is the picture's).

**WiVRn settings** (in the WiVRn app on the headset and the dashboard on the PC), from the
simulation below and the encoder's arithmetic:

1. **Refresh rate 72 Hz** with the game at 72 fps: at 90 Hz each game frame stays one or two headset
   frames in turn (5 to 4), so the world outside judders while the cabin, reprojected, does not.
2. **Bit rate** as high as the link holds: 24.6 Mbit/s an eye is far below what a Quest 3 decodes;
   100-200 Mbit/s in all on a 5 or 6 GHz router in the same room (or WiVRn over USB, which this PC
   can use now that the headset has USB debugging).
3. **Codec H.265 or AV1, 10-bit**: the RX 9060 XT encodes both in hardware (VA-API) and the Quest 3
   decodes both; at the same bit rate they keep about twice H.264's detail, and 10-bit removes the
   banding of the dark cabin and the sky.
4. **Resolution**: the stream is 0.71 of the headset's size on each axis: raise WiVRn's resolution
   scale toward 1 (or use foveated encoding, which keeps the middle at full size for fewer bits).

What the stream does to an eye (the emulator's left eye at the panel's 2064 x 2208, straight ahead
in the car; `headset_view.py` with this PC's VA-API encoder, a 4-pixel-a-frame pan; SSIM against
the eye view, 1 = identical; `shots/doc8/wivrn_sim.png`, scratchpad `s333v4`):

| Stream per eye | SSIM |
| --- | --- |
| 1472 x 1536, H.264 8-bit, 24.6 Mbit/s, 90 Hz (as tested) | 0.968 |
| 1472 x 1536, H.265 10-bit, 50 Mbit/s | 0.972 |
| 2064 x 2208, H.265 10-bit, 75 Mbit/s, 72 Hz | 0.987 |
| 2064 x 2208, AV1 10-bit, 75 Mbit/s, 72 Hz (the encoder used a quarter of it) | 0.990 |

The encoder size costs most (the wheel's grain, the gauges' numbers); the codec and bit rate then
decide how much survives motion. The sharpening's own effect on the eye view: the edge energy of
the dashboard's middle is 41 % higher at 0.5 than at 0 (`shots/sharpen_cmp.png`).

### Stereo (3)

Depth stereo is off by default (`vr_stereo` off): the user found it "full of artifacts", which is
what the method gives where the depth is wrong or missing (glass, particles, the holes beside near
objects filled from the background). It stays as `vr_stereo=depth`.

**True stereo, evaluated; not built this round.** What it costs, measured on this PC (RX 9060 XT,
the cockpit at 2 x 2, 72 fps, standing at the festival; `s333v4/bench.sh`, the benchmark lock held):
the v4 cockpit takes 11.2 ms of GPU a frame (81 % of the 13.9 ms at 72 fps; 71.9 fps) and the GPU emulation's command processor 4.5-4.9 ms of CPU a frame for 2,660-2,920 draws (four runs: v4, without the cull scale, without sharpening, without both: GPU 11.21-11.23 ms in all). The ways to draw the world from two eye points:

| Method | CPU | GPU | Verdict |
| --- | --- | --- | --- |
| The engine's second split view (FM4's split-screen: `CSplitView` 1, its own director and presentation camera) with the other eye's offset | The scene's share of the command processor and the render thread twice: about +3 ms of command processor a frame (two thirds of 4.6 ms and of 2,750 draws) and +1.5 ms of render thread, on the threads every frame waits for | About 2x the scene (its draws and pixels), shadows once | Not at 72 fps on this CPU; whether FH1 can make view 1 without a second player is not established |
| Alternate eyes on alternate frames | 1x | 1x | No: each eye at 36 Hz, the scenery moves 0.4 m at 100 km/h between the eyes' frames (false depth) |
| Per-eye replay inside the GPU emulation (the scene's draws recorded once, executed twice into per-eye render targets with a per-eye clip-space shift) | About 1x (+ the second submission's recording) | Per-eye frusta at the same pixels per degree need 1541 x 1231 per eye, 3.8 million pixels in all against today's 3.7: the pixels cost about the same, the vertex work and the draws' fixed cost double | The way to go: about 1.3-1.5x the scene's GPU time, which 2 x 2 at 72 fps has room for only with MSAA off |

The plan for the per-eye replay (SDK work, commits after 0395):

1. **The scene's range**: the main scene's draws are the ones ReIGlue 0394's depth capture and the
   DLSS path already identify (`z_enable`, `zfunc` not Always, not window-space, the 1280-pitch
   colour target; the first draw after the depth pre-pass resolve at the frame's first sun shadow
   pass, `Fh1VrDepthBeforeDraw`), up to the resolve of the main colour target before
   post-processing. Shadow passes, the cube map and the mirrors before it stay single.
2. **Two sets of host render targets** for that range (the scene's EDRAM targets at the draw scale;
   predicated tiling off for the range, as the split-screen scenario's `TilingScenario 4`), the
   command processor executing the range's recorded Vulkan commands once per eye.
3. **The eye's offset** in the translated vertex shaders: an epilogue `pos.x += -P00 * dx` (dx half
   the eye distance, P00 from the cockpit's projection that the camera hook knows) plus each eye's
   asymmetric frustum as an NDC scale and offset, from a push constant; the shader's cache key is
   unchanged. Full-screen and window-space draws get no offset.
4. **Post-processing once per eye** from each eye's resolve (bloom, tone mapping; their constants
   are the same), the HUD once (ReIGlue 0393 already separates it), then each eye to its half of the
   projection layer, as ReIGlue 0394's halves.
5. **The camera hook** draws the union of the eyes as today (the cull, the LODs and the shadows for
   both), so the game-side state stays single.

Risks: readbacks or memexport inside the range (they must happen once), screen-space passes inside
it that rebuild positions from depth (the motion-blur velocity pass: per eye or off), and the
command processor's resolves and EDRAM bookkeeping at render scale.

### The headset's own picture (4a)

`scratchpad/questcap` (not in the repository): Google's platform-tools r37.0.1 (adb) and scrcpy
5.0.1 in a private prefix, checked against Google's SDK manifest and pinned SHA-256s and the scrcpy
release's signed checksums; adb on its own server port (5038) with its own key, so it never takes
the headset from WiVRn's own adb. `capture.sh check|record|stop|shot|frames|trace` (its README):
`adb exec-out screencap` gives the panels' picture, both eyes lens-shaped at 4128 x 2208, exactly
what the eyes see through the lenses (stream, reprojection and all); `record` keeps the display
awake (`com.oculus.vrpowermanager prox_close`, restored with `automation_disable` at the end and on
Ctrl-C), names frames by host time and finds each one's nearest `VR trace` line. Captures can show
the room (passthrough) outside the game: they stay local and out of commits and issues.

**This round on the real headset** (allowed from 02:00): over adb the WiVRn client was started on the
Quest 3 (on the desk, display kept awake), and the game ran against the system WiVRn server
(`XR_RUNTIME_JSON` = the Flatpak's manifest, `s333v4/run_game.sh --wivrn`): `VR: OpenXR runtime
WiVRn 'v26.9'`, session focused, frames submitted at 90 Hz (WiVRn's encoder again 1472 x 1536 and
24.6 Mbit/s an eye). But the headset reported "the room is too dark to track your movements; turn on
a light" and gave no head pose (`head not tracked`), so the cockpit never started and the lenses
showed only that dialog; nothing else was captured. With a light on, `s333v4/hs_verify.sh` runs the
check in one go: the look-around (straight, 45 left, 60 right leaning, 90 left, 90 left up, 180,
down 30) as a test offset on top of the still headset (`vr_head_pose_test` with
`FORZA_VR_POSE_FIFO`: the game's camera turns, the projection layer keeps the real head, so the
turned view shows straight ahead in the lenses), the size cull off and on, the exposure at the
game's speed and at version 4's, each as the lenses show it and as the game drew it.

### The emulator closer to a Quest 3 (4b)

`scratchpad/xremu2` (the original `xremu` untouched; its downloads and build sysroot reused):

- `start.sh --profile quest3` (default): the eyes the user's Quest 3 reports through WiVRn (54 out,
  40 in, 44 up, 55 down degrees) at the panels' 2064 x 2208 pixels each, 90 Hz, the recommended
  swapchain size at 100 %; `--profile wivrn`: the same eyes at WiVRn's encoder size (1472 x 1536),
  the picture WiVRn's own compositor (Monado's, as this one) renders before encoding.
- Its own Monado build with patch 0004: eye captures at the eyes' full size (Monado's mirror was
  fixed at 1080 rows).
- `headset_view.py EYE.png`: what WiVRn's stream makes of an eye capture: scaled to the encoder size,
  a 30-frame clip panning 4 pixels a frame encoded with the same VA-API encoder WiVRn uses on this
  PC at the codec, bit depth and bit rate given (default the user's: H.264 8-bit 24.6 Mbit/s at 90
  Hz), the middle frame decoded and scaled to the panel; `--lens` adds the panel's lens-shaped
  picture as a screencap shows it (a radial model, approximate until fitted to a real capture);
  `NAME_cmp.png` puts the middle of the input and of the result side by side.

### Tested (version 4, Linux, TU4, RX 9060 XT)

- **Emulated headset** (`scratchpad/xremu2`, Quest 3 eyes at 2064 x 2208, 90 Hz; render scale 2,
  72 fps, the user's settings, the test save's Aventador at the festival): the third test's poses
  and the extreme ones with version 3's box, without a box, and with version 4's (roof kept in all
  six); straight and turned 45 degrees at three exposure speeds (table above); the size cull off
  and on (counts and pictures above); sharpening 0 and 0.5; WiVRn's stream simulated at four
  settings. The trace shows the test offset and `picture drawn N cm away`.
- **Timing** (benchmark lock held, `s333v4/bench_results.txt`): GPU 11.2 ms a frame at 2 x 2 in
  the cockpit with or without the cull scale and the sharpening (version 3: 11.4 ms mono, 11.7 with
  depth stereo).
- **Real headset**: the session on WiVRn started and focused; no head tracking in the dark room
  (above).
- **Unit tests**: `xr_math_test` (version 4's box: the third test's looks 1:1 within 3 mm, up held near
  the edges, continuity sideways and up; the test offset; the cull factor 0.184 for 62 against 109
  degrees), `world_detail_test` (the exposure factor), `xr_frame_tags_test`,
  `vr_head_pose_math_test`, the launcher's `test_vr.py` (the seat's new default).
- **Not done**: the headset check with a light on (`hs_verify.sh`), true stereo (plan above; built in
  [version 5](#version-5-true-stereo)), the lens model fitted to a real screencap.

## Version 5: true stereo

The third test asked for depth without version 3's artifacts. Version 5 draws the scene for each eye
from that eye's own point, from the same frame (no eye a frame late): the near cockpit moves between
the eyes and the scenery does not, with nothing filled in or guessed. `vr_stereo` is now `true` by
default; `depth` (version 3's) and `off` (one picture, version 4) stay.

| | Version 4 (`off`) | Version 3 (`depth`) | Version 5 (`true`) |
| --- | --- | --- | --- |
| What each eye gets | The same picture | The one picture moved by each pixel's depth, holes filled from the background | The scene drawn again from the eye's point |
| Near parts of the cockpit | Flat | Right where the depth is right; smeared edges, glass and particles wrong | Right everywhere |
| GPU a frame (RX 9060 XT, render scale 2, standing at the festival) | 11.3 ms | 11.7 ms | 13.2 ms (scene MSAA off), 14.7 ms (2x MSAA) |
| 72 fps | Yes | Yes | With the scene's MSAA off |

### How it works (ReIGlue 0396)

**Per-eye replay inside the GPU emulation**, version 4's plan, built at the command level instead
of the Vulkan level. The command processor executes the guest's PM4 commands for a frame once, as
usual (the left eye), and keeps a copy of every packet from the scene's start to the first HUD draw;
before that HUD draw it puts the register file (and the bound shaders and predication bins) back as
they were at the scene's start and executes the copy again (the right eye), then puts back the HUD
draw's state and continues. Because the second pass goes through the command processor's normal
path, its render target ownership, texture loads, resolves and barriers stay consistent; the CPU
side of the game, the render thread and the guest's command buffers run once.

- **Where the segment starts and ends** (`src/graphics/fh1_vr_stereo.h`, from a traced frame,
  `FORZA_GPU_FRAME_TRACE`): the cockpit frame is the HUD's render-to-texture, the four sun shadow
  cascades, the 1280 x 720 view's depth pre-pass and the sun's shadow mask on it, the rear-view
  mirror, sometimes a face of the reflection cube, then the scene in two predicated EDRAM tiles (2x
  MSAA; one tile without MSAA), post-processing, the minimap, the composite into the front buffer and
  the HUD. The segment is the scene's clear (a window-space rectangle writing depth, 640 x 4x MSAA)
  and its first draw (colour and reversed depth on that depth buffer) up to the first
  `2_10_10_10_AS_10_10_10_10` draw into the front buffer after the composite (ReIGlue 0393's
  pre-HUD point). Everything before it is drawn once.
- **The eye's offset**: the scene's 3D draws (depth-tested on the scene's depth buffer, a projected
  position, the 16:9 viewport) take `P00 x d` from the clip-space x of every vertex, `d` half the
  headset's eye distance, in a new system constant the translated vertex shaders subtract after the
  guest's own shader (no shader cache key changes: it is zero for every other draw). The game draws
  the main view mirrored across (its shadow passes' `c0.x` is `-P00`; post-processing turns the
  picture back), measured and then read from those constants each frame: the left eye takes
  `+P00 x d`.
- **What stays the middle's**: the depth pre-pass, the sun's shadow mask and the occlusion are
  drawn once from the middle. The scene's shaders find them at the screen position they compute
  from the guest's position, which never sees the offset, so each eye's scene reads the shadow at
  the right surface. (Offsetting them too put every surface's shadow lookup 30 pixels off in the
  right eye: the whole wheel lit, `shots/before_mask_centred.png`.)
- **Memory the frame reuses**: the second pass must see what the first saw. Its uploads of guest
  memory are logged with their data during the first pass and repeated in order (a ring of per-object
  data refilled within the frame otherwise gives the second pass later contents); other uploads are
  skipped (the CPU may already be writing the next frame); `LOAD_ALU_CONSTANT` packets are logged as
  the register values they loaded; a texture is loaded again only from memory the second pass itself
  resolved (the frame reuses the shadow cascades' memory for the shadow mask and post-processing);
  fences, interrupts, waits, occlusion queries and scratch-register writes are not repeated.
- **Each eye's part of the picture**: the cockpit picture covers both eyes (123 x 109 degrees). Each
  eye is drawn only across the columns its field of view and the 5 degree margin reach (a Quest 3:
  the left eye 0-80 %, the right 20-100 %; `xr_math.h` `EyePictureRange`, `vr_stereo_eye_parts`).
- **The layers**: the left eye's composite is copied before the second pass; at the swap it goes
  through the same gamma ramp and sharpening as the scene layer, and both eyes go side by side in
  ReIGlue 0394's stereo layer; the projection layer gives each eye its half at its eye's pose (version
  3's path). The HUD panel and the virtual screen are unchanged (the HUD is recovered from the right
  eye's picture). The game asks for true stereo only while the cockpit is shown, so menus and the
  screen cost one picture.

### Cost (RX 9060 XT, Ryzen 7 7800X3D, the cockpit at render scale 2, the benchmark lock held)

`s333v5/bench_ab.sh` (standing at the festival) and `bench_drive.sh` (throttle held down the road):
one session each, true stereo switched off and on every 15 s (8 s driving) with the test FIFO's
`stereo 0|1`, the game's GPU time from DRM fdinfo and the command processor's from
`fh1_frame_cost_csv`:

| Setting | Mono: fps, GPU, command processor | True stereo: fps, GPU, command processor |
| --- | --- | --- |
| The user's (2x MSAA, ultra shadows, FSR present) | 72, 11.3 ms, 4.8 ms | 67, 14.7 ms, 7.5 ms |
| Scene MSAA off | 72, 11.0 ms, 3.7 ms | **71.8, 13.2 ms (95 %), 5.8 ms** |
| Scene MSAA off, driving | 72, 11.3 ms, 3.2 ms | **72, 13.3 ms (96 %), 5.2 ms** |
| 2x MSAA, shadows high | 72, 11.4 ms | 66, 14.9 ms |
| 2x MSAA, no present effect | 72, 11.3 ms | 67, 14.6 ms |

The second eye costs 2.2 ms of GPU without MSAA and 3.4 ms with 2x MSAA, where the scene is drawn in
two EDRAM tiles (each eye's vertex work and draws twice); drawing each eye only across its part of
the picture saved 0.3 ms (15.1 to 14.8). On the way: the first version replayed from the shadow
cascades (20.0 ms, 49 fps). The command processor's extra 2-3 ms are well inside the frame. **So
true stereo holds 72 fps on this card with the scene's MSAA off** (`vr_test_v5.sh` sets it), with
about half a millisecond to spare; with 2x MSAA it runs at 67 fps (two runs of each, `runs/final-*`).

### Tested (version 5, emulated Quest 3)

- **Parallax** (`scratchpad/xremu2`, Quest 3 eyes at 2064 x 2208, the test save's Aventador at the
  festival): between the two eye views the far trees and road are 501-502 pixels apart (the eyes'
  lopsided fields of view: 499 for a point at infinity), the gauges 587 (0.7 m away: 84 pixels of
  disparity expected, 86 measured), the wheel's rim 656 (0.4 m). In the game's own picture (the right
  eye) against true stereo switched off at the same moment: the gauges 32 pixels left, the trees 0,
  the lighting and shadows the same (`shots/mono_vs_right.png`); with the eye offset set to 0
  (`FORZA_VR_STEREO_SHIFT_SCALE=0`) the right eye's pass reproduces the picture.
- **Look-around** (`poses.py run standard`: straight, yaw 45/90 both ways, 180, pitch 30 up and
  down, roll 20, leans 15 cm right and left, 10 cm forward, 5 cm up; `shots/std/lr_sheet.png`):
  both eyes consistent at every pose, no unrendered columns in any eye view; driving
  (`shots/drive/lr_sheet.png`) and pause and resume (the screen gets one picture, the cockpit both
  again within a frame).
- **Real headset**: the session on WiVRn started; in the dark room the Quest reports no head
  tracking (`head not tracked`), so nothing was captured. `s333v5/hs_verify_v5.sh` runs the check in
  one go with a light on (the look-around, stereo off and on, the GPU time).
- **Unit tests**: `tools/test_vr_stereo.cpp` (the segment's start and end on the traced frame's
  draws, which draws take the offset, the offsets for 63 mm, the eyes' columns in the mirrored
  view, the packets the second pass leaves out, LOAD_ALU_CONSTANT as registers; in CI with the SDK
  tests), `xr_math_test` (each eye's part of the picture), `xr_lifecycle_test`.

### Limits

- **The desktop window** shows the right eye with the left fifth of the picture grey (not drawn for
  that eye).
- **Frames without the scene's structure** (no HUD draw after the composite, a render path that
  clears the scene differently) are drawn once: the left eye's picture goes to both eyes for that
  frame. The log counts them (`VR stereo: right eye replayed ... (N frames, M dropped)`).
- **Effects drawn from the middle**: the rear-view mirror's picture, the shadow mask, the occlusion
  and the sky (no depth test) are the same in both eyes; specular highlights use the middle's view
  direction. At 3 cm none of these is visible as a depth error.
- **Only the RX 9060 XT measured**; a slower GPU needs a lower render scale, and the command
  processor's +2-3 ms matter on a slow CPU.
- **Test aids**: `FORZA_GPU_FRAME_TRACE=<file>` (one frame's packets and draws when `<file>.trigger`
  appears), `FORZA_VR_STEREO_SHIFT_SCALE` (scales the eye offset; 0 for an A/B of the two passes), the
  test FIFO's `stereo 0|1` (true stereo off and back on).

New settings (cvars):

| cvar | Default | What |
| --- | --- | --- |
| `vr_stereo` | true (was off) | `true`: each eye drawn from its own point; `depth`: version 3's; `off`: one picture |
| `vr_stereo_eye_parts` | true | True stereo draws each eye only across the part of the picture it sees |

## Version 6: true stereo on the real headset

Version 5's first check on the real headset (Quest 3 over WiVRn on Wi-Fi, this PC's RX 9060 XT,
render scale 2, 72 fps, scene MSAA off, a still headset on the desk with a light on; the agents run
it, `scratchpad/s333v6/hs_verify_v6.sh`) showed true stereo right in the lenses, but:

| | Version 5 | Version 6 |
| --- | --- | --- |
| Game frame rate, true stereo | 53 fps | 71-72 fps (standing and driving) |
| The headset's refresh rate | 90 Hz | 72 Hz (the game asks for it) |
| Game frames shown, a second | 53 | 68-71 of 72 |
| Latency, head pose to the lenses | 117-126 ms (game 50-52 + WiVRn 66-69) | 88-112 ms (game 37-50 + WiVRn 46-64) |
| Key frames the headset asked for (a lost or late frame) | about 85 a minute | 1-2 a minute |
| WiVRn's stream | H.264 8-bit, 24.6 Mbit/s an eye | HEVC 10-bit, 50 Mbit/s an eye |

The game's GPU time with true stereo is 13.1-13.3 ms a frame (95 % of the 72 fps budget; one
picture: 10.7-11.0 ms), WiVRn's compositing 3-4 % of the GPU, its encoder a quarter of the video
engine (`status.txt` of each run: fps, the game's and WiVRn's GPU time from DRM fdinfo, the GPU's busy
percentage, the stream's Mbit/s, the headset's own frame statistics from its log).

### The game waited for WiVRn while holding the GPU queue

The cause of the 53 fps was not the GPU being too slow: the GPU was 81 % busy. Monado's compositor
(the one WiVRn runs) blocks in `xrEndFrame` until the GPU has finished the previous frame's work: it
waits for that frame's semaphore before it takes the next. The game calls `xrEndFrame` holding the
device's queue lock, as `XR_KHR_vulkan_enable2` asks (the runtime submits to the game's queue in it),
and the game has one graphics queue for everything, so while the OpenXR thread waited, the GPU
emulation could not submit: with true stereo `xrEndFrame` took 14 ms of every 14 ms frame (the new
`xrWaitFrame`/`xrEndFrame` times in the 10 s line), the GPU ran dry between frames, frames came late,
WiVRn's own frames came late (the headset showed up to 14 old frames a second and asked for new key
frames), and its latency estimate grew.

Version 6 waits for the previous frame first, without the lock: an empty submission with a fence
after each `xrEndFrame` marks everything the runtime submitted in it, and the next frame waits for
that fence before it takes the lock (`xr.cpp` `FinishEndFence`). `xrEndFrame` now takes 0.1 ms. On
the same run, before and after: 56 to 71 fps, the game's GPU busy 83 to 93 %, latency 134-153 ms to
71-90 ms, 60 key frames asked for in 93 s to 2. `FORZA_VR_LOCKED_END_FRAME=1` brings back version
5's way (a test aid).

### 72 Hz

WiVRn runs a Quest 3 at the highest rate under 100 Hz, 90 Hz, unless its headset app is set to another.
With the game at 72 fps that shows four frames of five for one refresh and the fifth for two: the world
outside judders while the reprojected cabin does not, and WiVRn composites and streams 90 pictures a
second instead of 72. The game now asks the runtime for the refresh rate that shows every frame the
same number of times (`XR_FB_display_refresh_rate`, which WiVRn and Meta's runtime have):
`vr_refresh_rate` 0 (the default) picks the lowest offered rate that is a whole multiple of the game's
frame rate (72 fps: 72 Hz; 60 fps: 120; 30 fps: 90), else the lowest above it; a number asks for that
rate; -1 leaves it to the runtime. The log says `VR: asked the headset for 72 Hz for 72 fps
(72/80/90/120 offered; it ran at 90)` and `VR: the headset's refresh rate changed from 90 to 72 Hz`.
WiVRn passes the request on to the headset whatever its app's refresh rate setting is; the headset
stays at that rate until it next connects.

### Every game frame shown once

At 72 fps on a 72 Hz headset each headset frame should show a new game frame. With the fixes above
it showed 56-62 a second: the OpenXR thread takes the newest finished frame when WiVRn wakes it, and
both run every 13.9 ms (± 0.5 ms, measured), but the moment WiVRn wakes it settled right where game
frames finish: one just in time, the next a fraction of a millisecond late (shown a frame later, the
one before it twice, then two ready at once and one dropped). The game's part of the latency jumped
between about 42 and 55 ms. Moving the game's own frame timer did not help (tried: 20 ms earlier in
10 s, and the frames still finished at the moment they were taken), likely because Monado times an
application's wake-up from when its previous frame's GPU work finished, which here is the game's.

Now, when the headset's frame finds no new game frame although one is due (the last one came about a
headset frame ago), it waits for it, at most `vr_frame_wait` (4 ms; it needs to know when each frame
was published, ReIGlue 0397). Measured, four 20 s windows on and off in the same session: 68-71
frames shown a second against 56-62, frames passed over 15-35 in 10 s against 100-163; about 17-43
headset frames a second waited, 2 ms on average, and 95 % found their frame. The latency did not
change. The 10 s line shows it: `frames ready N ms (least N) before taken, N passed over, waited for N
(N came, N ms a wait); taken every 13.9+-0.5 ms, published every 13.9+-0.5 ms`.

### WiVRn's settings

On the PC (`~/.var/app/io.github.wivrn.wivrn/config/wivrn/config.json` for the Flatpak, read again at
each headset connection; the previous one is kept as `config.json.bak-20261026`):

```json
{
  "bit-depth": 10,
  "encoder": [
    {"encoder": "vaapi", "codec": "h265", "options": {"b": "50000000"}},
    {"encoder": "vaapi", "codec": "h265", "options": {"b": "50000000"}},
    {"encoder": "vaapi", "codec": "h265"}
  ],
  ...
}
```

- **Codec HEVC (H.265), 10-bit.** The RX 9060 XT encodes HEVC and AV1 in hardware (VA-API), and the
  Quest 3 decodes both. Lens captures of the same view at 50 Mbit/s an eye: the trees and the road
  through the windscreen visibly softer with H.264 than with HEVC or AV1 (a detail measure of the
  left eye's middle, scratchpad `s333v6/tools/sharp.py`: 3.5 for H.264, 3.7-4.3 for HEVC and 4.3 for
  AV1; it varies with the game's light between runs); AV1 no better than HEVC for twice the encoder
  time (half the video engine against a quarter). 10-bit keeps the dark cabin and the sky from banding. The latency was the
  same for all three within the runs' spread.
- **Bit rate 50 Mbit/s an eye** (95-117 Mbit/s sent in all), from the 24.6 the headset app's default
  50 Mbit/s total gave. The headset is on 5 GHz Wi-Fi 6 at 1200 Mbit/s link, the PC on wired gigabit
  to the same router: no more lost frames than at 25 Mbit/s an eye (1-2 key frames asked for a minute;
  25 Mbit/s an eye gave a little less detail, 4.1). The bit rate is normally set in the
  headset app (Settings, Streaming, Bitrate); WiVRn's server cannot set it, so here the VA-API encoder's
  own option overrides it (`"options": {"b": ...}`: WiVRn hands those to FFmpeg when it opens the
  encoder, which takes its bit rate then). The third encoder is the passthrough's alpha, left alone.
- **USB**: WiVRn can stream over the USB cable (adb, TCP only); it streams over Wi-Fi here and that
  works, so it stays on Wi-Fi.
- **The encoded size**, 1472 x 1536 an eye, comes from the headset app: its render resolution times
  100 % less its "Foveated encoding" percentage (which keeps more of the middle; set to 30 % here, its
  lowest without the app's extended settings, against a default of 50 %). Neither can be set from the
  PC.

**For players** (WiVRn's headset app and `config.json`): the refresh rate needs nothing now (the game
asks for 72 Hz); codec HEVC (or AV1) with 10-bit on, if the GPU encodes them in hardware (WiVRn's
server log lists the encoders it set up, as `vaapi (h265 10-bit)`); the bit rate as high as the link
holds without the headset losing frames (100 Mbit/s on 5 GHz Wi-Fi in the same room worked here;
WiVRn's dashboard can also connect over the USB cable); foveated encoding at its lowest (30 %).

### What did not help

- **A render scale under 2 across** (the ultrawide path's fractional width, `forza_native_width=2240`,
  1.75 x 2): 12.7 ms against 13.3, but true stereo's eyes are made at the full 2560 and the picture
  then is 2240, so they were not handed over (one picture for both eyes). An eighth fewer pixels saved
  0.6 ms: the second eye's cost is mostly its 1,300 draws and their vertices, not its pixels. Not used.
- **Cheaper settings** (shadows high, bilinear desktop window, 8x anisotropic filtering): true stereo
  13.27 ms against 13.3; one picture 10.66 against 11.0. Not used.

So the GPU has about 0.6 ms to spare at 72 fps with true stereo on this card (95 % busy, the whole
GPU 97-99 % with WiVRn); the headset showed 0-8 old frames in 20 s in most measurements.

### Tested (version 6, real Quest 3)

`scratchpad/s333v6/hs_verify_v6.sh` (with `POSES=1 DRIVE=1 RECONNECT=1` for the final run, about
4 minutes): the look-around (straight, 45 left, 60 right leaning, 90 left, 180, down 30), driving for
20 s and one picture against true stereo, each with the lenses captured; all show the game. Final:
stereo 71.2 fps standing (13.3 ms), 72.0 driving (13.0 ms), one picture 72.0 (11.0 ms); 68-72 frames
shown a second; 2 key frames asked for in 162 s; latency 100-112 ms that run. Unit tests:
`xr_math_test` (the refresh rate chosen for 72, 60, 30, 90, 144 and 75 fps on a Quest 3's rates and
on 80/90/120/144), `xr_lifecycle_test` (the new fence and marker submission with the mocks).

New settings (cvars):

| cvar | Default | What |
| --- | --- | --- |
| `vr_refresh_rate` | 0 | The headset's refresh rate: 0 matches it to the game's frame rate, a number asks for that rate, -1 leaves it to the runtime |
| `vr_frame_wait` | 4 | Milliseconds the headset's frame may wait for a game frame that is due, rather than show the last one again (0: never) |

Test aids: the test FIFO's `framewait 0|1` (`vr_frame_wait` off and back), `recenter` (as the Home
key: a headset lying still can relocalise after its display wakes, once by 134 degrees and 57 cm),
`FORZA_VR_LOCKED_END_FRAME=1`.

## Version 7: the cabin no longer jumps

The fourth headset test (version 6, Quest 3 over WiVRn, 72 fps, true stereo, 26 October 2026, with
the trace): "much better", but turning the head about 90 degrees to a side window or into the car
"flicks", as in version 2. The trace has `CCamDriver`, posed, throughout; the head 9-12 cm aside and
4-9 cm back at 60-73 degrees, the eye held by version 4's box in none of the samples.

**Reproduced.** The trace's head path from 12:37:05.9 to 12:37:16.4 replayed step by step in the
emulated headset (`scratchpad/xremu2`, the test save's Aventador): looking left at 54 degrees with
the head 7.8 cm aside, 2.2 down and 4.1 back the whole cabin jumps between two steps and the roof
over the door is gone (the sky through it), and comes back when the head returns; looking right the
headliner changes as abruptly. The same path through the test-pose FIFO on the real headset
(`scratchpad/svrflick/hs_flick.sh`, the display recorded at 72 fps): the jump in one frame, 42-47
(mean difference of the left eye between frames) where the head turn itself gives up to 28, twice
each pass.

**The cause.** Swept 1 cm (then 2.5 mm) at a time in every direction, the jump happens where the
view is 10.0 cm from the in-car camera's own point (9.9-10.1 cm aside left and right, forward, down
and back), the same in both eyes and with one picture (`vr_stereo off`), and with the near plane at
0.5 cm as at 2. Inside that sphere the game draws the player's car following the view: the cabin
looks the same with the head 5 cm aside, 4 cm down or 8 cm back, and the seat height (`vr_seat_height_cm`
0 against -4) does not show in it. Past 10 cm it draws the car where it really is, so the cabin jumps
by the whole 10 cm at once and parts of the roof are not drawn. The game's camera (the director's
position and matrix) and the head pose are continuous through it, and the frame's draws are the
same (`FORZA_GPU_FRAME_TRACE`): the switch is the renderer's, on the view's distance from the
camera. Versions 1 and 2 met it looking back and aside ("another camera"); version 3's box held
the eye 10 x 8 x 6 cm round the game's eye point, inside the sphere for nearly every look; version 4
lowered the eye 4 cm and let it move 18 cm aside and 15 back, so a look at a side window (the head
9-12 cm aside and 4-9 back, 11-14 cm from the camera's point) crossed it every time. Versions 5 and 6
did not change it; the user had not tested 4 and 5.

**The change.** The eye is held within 9 cm of the in-car camera's point (`vr_head_reach_cm`, the
seat height included), its offset eased toward that sphere from 0.85 of it (7.7 cm) as version 3's
box was, the direction kept (`xr_math.h` `LimitHead`). Version 4's box (`vr_head_side_limit_cm`,
`vr_head_back_limit_cm`, `vr_head_up_limit_cm`) is gone. Inside the sphere the hold does not show in
the cabin (it follows the view anyway); the world outside moves a few centimetres less. True stereo
is untouched: each eye's 3.2 cm is added in the vertex shader, after the game's decision.

- **Not the near plane, not the field of view.** A smaller near plane or a "smaller camera" would not
  help: nothing is clipped, the game moves the car once the view is more than 10 cm from where the
  in-car camera is. Version 3's finding that the near plane was not the cause stands.
- **Not a fade.** Fading to dark where the eye would leave the cabin (common in VR) is not needed:
  with the hold the view never gets there, and a fade at every look at a mirror would be worse.
- **Head translation in the cabin.** Inside the sphere the cabin follows the view, so leaning
  does not move the eye in the cabin (so it was in every version); only the turn of the head
  and each eye's offset do. Drawing the car where it is relative to the posed view (what the game
  does past 10 cm, with the cockpit's own parts) would need the renderer's switch found and changed:
  a lead for later.

**Tested (version 7).** The same replayed path, emulated: the roof stays at every step; at the two
steps where the cabin jumped the left eye changes by 21 and 30 (version 6: 41 and 44, the head turn
alone up to 39), and the top of the left eye never fills with sky (bright share 0.55 at most against
0.93). On the real headset
(`hs_flick.sh`, two passes): no jump (largest change between frames 21.7 against 47.4), the roof
always there (`svrflick/shots/headset_before_after.png`: before, the roof gone in one frame;
after, the same moment). True stereo afterwards: 71.8 fps, 13.4 ms of GPU a frame, 72 Hz, 68-69
frames shown a second. Unit tests: `xr_math_test` (the side-window looks of the trace held within
9 cm, every direction, the direction kept, continuous, a low seat never pushed out).

New settings (cvars):

| cvar | Default | What |
| --- | --- | --- |
| `vr_head_reach_cm` | 9 | How far the eye may move from the in-car camera's own point, the seat height included (the game redraws the car past 10 cm; 100: no limit) |

Removed: `vr_head_side_limit_cm`, `vr_head_back_limit_cm`, `vr_head_up_limit_cm`.

## Version 8: the cabin stays in the car

The fifth headset test (version 7, Quest 3, 26 October 2026): "the whole car and lighting moves when I
move my head, which is headache and nausea inducing". Reported since version 3 ("looking around the car,
the camera moves and the lighting with the whole car moves"); versions 4 and 7 treated other causes.

**The cause, in plain words.** The game draws the inside of your own car in a pass of its own, in a
coordinate system whose origin is the in-car camera's point (the driver's eye the game uses). In that
pass it takes the camera's distance from that point, and when it is under 10 cm it uses zero: the
cabin is drawn as if your eye were exactly at the driver's eye point. On a monitor this keeps the
dashboard still while the camera shakes. In a headset it means: lean 6 cm to the side and the
dashboard, the wheel, the pillars and the mirrors are drawn exactly where they were, so the whole car
moves with your head while the world outside does not; and as the light and the shadows on the cabin
are computed from where your eye really is, they slide over it. Past 10 cm the game uses the real
distance, and the cabin jumped by those 10 cm: the "flick" of versions 2 and 6. Version 7 kept the eye
within 9 cm, so the jump was gone but the cabin followed every head movement.

**Where.** The cabin pass is base `sub_82DCF1C0` (Title Update 4 `sub_82E91040`), with the renderer
and the view constants (r4). It saves the camera's world position (constants +192) at +208, converts
it to fixed point with 18 fraction bits (`vcfpsxws128`), subtracts the car render object's in-car
camera point (+1872, the same format; the object from `sub_82447400`), takes the squared length
(`vmsum3fp128`) and compares it with the constant 0.01 at `0x82000CCC` (shared with about 250
functions); below it the offset becomes zero. It writes the offset as the camera's position (+192),
as the translation of the camera-to-world matrix (+128, row 3 at +176) and inverts that into the view
(+0), draws the cabin (`sub_823F37D0`, then twice `sub_82447CA8`, each with the constants in r5) and
puts the constants back. Found by dumping the cockpit model's vertex constants at its draw (the view's
translation was 0 with the head 0 or 6 cm aside and 14.6 cm at 14 cm, the car's own matrix the same
in all three), then the view constants and the stack frames that held them, and the callers of the
camera's `IsCameraInCockpit`.

**The change** (`hooks/vr_cockpit_space.cpp`, the maths in `platform/vr_cockpit_space.h`). In the VR
cockpit, before each of the pass's three cabin draws, the camera's position, its matrix's translation
and the view are written from the head's own offset: the head transform the frame's camera was posed
with (kept with the frame's tag, `xr::RenderViewHead`) turned into the car's axes. That is exactly the
eye's place in the car and nothing else: the game's (camera - point) also carries where the camera and
the car's point differ when the cabin is drawn (a few millimetres while driving), which the game's snap
hid and which would make the cabin tremble. Without a tag (no posed view) the game's own offset is
used, without the 10 cm test. The game puts its own constants back after the draws.
`vr_cockpit_parallax` (on) turns it off; the FIFO test aid `parallax 0|1|2` (2: the cabin moves but
is shaded with the game's own eye position, to tell sheen from light that slides).

- **The eye's room.** With the cabin drawn from the real eye, the 10 cm sphere no longer matters, and
  version 7's 9 cm hold (`vr_head_reach_cm`) is gone. What remains is the cabin's own shape: the
  interior is modelled for the driver's eye, and an eye that goes up near the doors or the rear window
  leaves it through the roof. So version 4's room is back (an ellipse 18 cm aside and 15 cm back on the
  floor, a dome 6 cm high over it; forward and down free; `vr_head_side_limit_cm` and the others), which
  version 4 checked at the user's extreme poses with the roof intact.
- **The cabin's near plane.** The pass builds its projection with the car's own near plane (the car
  render object's virtual +456, `CockpitNearPlane` in the car's data: 10 cm for the test save's
  Aventador), not the camera's that version 3 changed. With the eye moved toward the driver's door the
  headliner over the door and the pillar came within 10 cm and were cut away: the "roof over the door
  opens" of versions 3 to 7 (version 3 and 7 found the world's near plane was not it). The cabin pass
  now takes `vr_near_plane_cm` (2 cm) too.
- **The seat height** now really lowers the eye in the cabin (it did not show inside 10 cm before).
- **A fallback:** `vr_head_position=false` keeps the eye at the seat and only turns it (3 degrees of
  freedom): leaning moves the whole view, cabin and world together.

**Measured** (`tools/vr_comfort_check.py`, numpy and Pillow; the emulated Quest 3 and the real one).
Each pose is compared with a straight-ahead picture taken just before it (the day goes on); the
tests and their rules are in the script's header. Emulated Quest 3 (`scratchpad/xremu2`), the test
save's Aventador standing at the festival, the user's settings (render scale 2, 72 fps, true stereo,
the scene's MSAA off), version 7 = 170e8d8, version 8 = this branch merged with 4d6f2e3:

| Test | Version 7 | Version 8 |
| --- | --- | --- |
| 1 parallax: the cabin's shift against what its depth predicts (1 = fixed to the car, 0 = follows the head), head 6 cm right / back / up | 0.00 / 0.00 / 0.00 (predicted 80 / 62 / 79 px at half size, measured 0): FAIL | 1.00 / 0.98 / 0.96 (error 1-5 px): PASS |
| 2 light on the cabin, 6 cm moves: lighting-only change beyond the metric's floor (limit 20 points) | +43 / +57 / +43 points: FAIL | +12 / +8 / +11: pass |
| 2 light, head turns about the eye (limit 5 points) | +4 to +46 | +1.4 to +2.5 for four of six turns; +6 (30 degrees left), +29 (45 degrees right), and the lean right +38 |
| 2 control: the game's own in-car camera turned the same way, VR off (test pose, flat 62 degree view) | | +0.2 to +3.8 points for 30-45 degree turns |
| 2 overall brightness (exposure, limit 5 %) | within 4.6 % | within 4.2 % |
| 3 no jump: the user's version 6 head path in steps of 1 cm / 3 degrees, a step changing the picture 1.75 times as much as its neighbours | 2 flagged steps (1.9 times): a car passing the side window, not the cabin (the 9 cm hold kept it from the snap) | none (at most 1.64 times): PASS. Control, `parallax 0` (the game's snap with version 4's room, as version 6): a jump at every crossing of 10 cm (1.85-2.16 times): FAIL, as it should |
| 4 driving, head still: the cabin's shift | median 0.26 px: PASS | median 0.13 px, 73 % of the cabin within 1 px: PASS |
| 5 frame rate (emulator: its compositor and the captures cost frames; the headset is the measure) | 62-68 | 62-67 |

The light: the 6 cm moves of version 7 change it by 4 to 6 times the metric's floor (the cabin stays put
in the picture while its light moves); version 8's change is that of the game's own camera moving 6 cm
outside its 10 cm snap (+14 to +19, measured in version 7): view-dependent sheen on the leather and glare
near the windows. The turns: a pure turn of the game's own camera barely changes the cabin's light (the control). The
VR turns that exceed the limit also move the game's camera (the head turned about the eye moves its centre
2 cm; the lean 6 cm), and their changes sit on glossy parts (the wheel's spokes and chrome, the vanity
mirror) in the low dawn sun of the test save: the user looked at those poses and judged it real lighting
(sheen and side light that change with where the eye is), not a VR fault.

Unit tests: `vr_cockpit_space_test` (the fixed point, the 10 cm edge, the view), `xr_math_test` (version
4's room again).

## HUD and menus

**Where.** The game's UI system (`ForzaUI` / UI4: `CHud` and its controls, the menus'
screens) draws on the render thread after the scene's post-processing, straight into the
front buffer: screen-space quads with the HUD/font shader families, no depth test. In the
prototype's screenshots the minimap, speedometer, lap/position boards and the event
banners stay where they are on the screen whatever the head does.

**How the SDK already separates it.** Patch 0046 (`VulkanCommandProcessor::IssueDraw`)
finds each frame's scene/UI boundary from the front buffer (1280 pitch, no MSAA): the
tonemapped scene is composited into it as `k_2_10_10_10`, and the first draw into the same
buffer as `k_2_10_10_10_AS_10_10_10_10` starts the UI (`fh1_scene_composited_`,
`Fh1SelectNativeHud`). With DLSS/FSR 3 the UI already goes to its own render targets at its
own scale, seeded with the scene. Patch 0063 also recognizes the HUD/font shader families
and moves whole HUD draws (the ultrawide edge anchors).

**For VR.** Seed the UI target with transparent black instead of the scene and hand it to
the compositor as a quad layer: head-locked (or a fixed panel in front of the seat) for
menus, fixed in the car below the windscreen for the HUD. To check: elements that darken or
blend with what is under them (translucent backdrops) need the alpha to come out right on
a cleared target. Projected labels (rival names, route markers) were not in
the screenshots' frames; whether the scene pass or the HUD pass draws them is open (if the
HUD pass, they need a per-eye position or a depth in stereo). Menus over a
paused scene (pause, map) and the 3D menus with their own UI cameras (garage, showroom,
`CUI4CarTargetCamera`) work unchanged on a flat virtual screen (stage a); the cockpit
gauges are part of the car model and are already in the car.

## Stereo: drawing the scene twice

**The engine has a second view.** The world update runs a fixed loop over two
`CSplitView`s, each with its own director, presentation camera and viewport, and the
executable and media still carry Forza Motorsport 4's two-player split-screen:
`ForzaUI::CSplitscreenLobby`, `CHudSplitscreen`, `CPauseSplitscreen`, a Kinect split-screen
join flow, `Splitscreen.str` (player 2 can "Play as Guest", no second profile needed), the
split-screen render scenario below, and split-screen cockpit offsets in the car data
(`SplitscreenCamCockpitOffsetX/Y/Z`, `Driver\SplitscreenDefaultOffsetFromSteeringWheel*`).
FH1 was not sold with split-screen; whether its menus still reach it is not established. So "the game draws the scene twice"
does not need a new engine path: the second split view is one. What it would take for
stereo: give view 1 the same player's camera with the other eye's offset (the prototype's
hook point, t.x = +-IPD/2), and its own full-size eye target instead of half of the
screen. Unknown: whether a split view can exist without a second player and car, and
whether the split-screen renderer can be pointed at two full-size targets.

**What the console cut to afford it.** `media/renderscenarios.zip` > `Splitscreen_Race.xml`
against `8-Car_Race.xml` / `Horizon_Race.xml`: tiling scenario 4
(`SplitScreen_NonTiling2xMSAA`: 2x MSAA in one pass instead of 4x MSAA in three predicated
tiles), no crowd (`CrowdLoad 0`), a quarter of the particles, no dynamic environment-map
updates (`EnvMapFrequencyScale 0`), shadow maps only in the cockpit view
(`SkipShadowMapUnlessCockpit`), a cheaper car shader (`shaders_splitscreen`), and no motion
blur entry. Those are the per-view costs; shadows and the reflection cube would be shared
between eyes anyway.

**What runs per scene draw, and what a second one costs** (estimates from the per-thread
measurements in [perf.md](perf.md) > "CPU time per frame" and "Low-end CPUs": TU4, 30 fps,
Ryzen 7 7800X3D at 5 GHz; not measured with two views):

| Per frame at 30 fps | Free roam | Race | Doubles with a second eye? |
| --- | --- | --- | --- |
| GPU command processor (PM4 parsing, register writes, `UpdateBindings`, texture and render target lookups, Vulkan recording) | 5.0 ms | 6.7-8.4 ms | its scene share, yes |
| Render thread (`sub_8261E178`): culling, per-object constants (`matWVP` ...), D3D command building | ~2.3 ms own work (3.5-4.1 ms with its waits) | ~2.3 ms (4.1-4.7) | its scene share, yes |
| Vulkan submission thread (driver) | 1.2 ms | 1.5 ms | its scene share, yes |
| Main loop (simulation, physics, AI) | 2.3 ms | 2.7 ms | no |
| Audio mixer and decoders | 3.5 ms | 4.4 ms | no |
| GPU (RX 9060 XT, 720p) | 7.9 ms | 8.3 ms | scene and post, yes; shadows, cube map, HUD, no |

**The scene's share, measured.** A RenderDoc capture of free roam (TU4, RADV, 720p, hood
camera, standing at the spawn; `renderdoccmd` with `FORZA_RDCAPTURE_TRIGGER`, draws grouped by
their render targets) holds about 3,000 host draws per guest frame:

| Pass | Draws per frame | Targets |
| --- | --- | --- |
| Main scene, 4x MSAA in three EDRAM tiles | 1,969 (about 660 per tile) | 1280x512 4x MSAA colour and depth |
| Shadow cascades, depth only | 320-335 | 1040x2528 and 520x1264 depth |
| Another depth-only pass (not identified) | 388 | 1280x2048 depth |
| Post-processing, bloom, quarter-resolution passes | about 250 | 1280x2048 and 640x1024 colour |
| Reflection cube update (not every frame) | about 15 | 320x4096 and 160x4096 |
| Front buffer, HUD and the rest | the remainder | |

So the scene is about two thirds of the draws the command processor turns into Vulkan, and
a second eye in the same tiled mode adds about 66% to that thread: 5-8.4 ms becomes about
8.3-14 ms per frame. With the render thread's and the submission thread's scene shares, a
second eye adds roughly +5 to +8 ms of CPU per frame here, on the two threads every frame
waits for. That still holds 30 fps on this CPU, halves the ceiling above 60, and puts 30 fps
out of reach on a Steam Deck-class CPU (command processor 2-2.5 times slower). The
integration research below reaches the same verdict; it estimates +10-13 ms by doubling both
threads whole.

**A lead that changes the sums.** The game builds the scene's command buffer once and the
GPU replays it per tile (predicated tiling), so the emulated GPU processes the scene three
times while the host render targets need it once. The split-screen scenario's non-tiled
mode (`TilingScenario 4`, `SplitScreen_NonTiling2xMSAA`) draws the scene in one pass. If a
non-tiled scene (or skipping the redundant tiles in the SDK) works for the main view, it
would cut the command processor's scene work to about a third, which is more than a second
eye costs, and would help slow CPUs in flat play too (#45). Not tried. A two-player
split-screen race measured against a one-player race would replace the estimates above with
numbers (open question).

**Alternatives** (details in the research section): rendering one wider mono view and
warping it to both eyes with the depth the DLSS/FSR 3 path already decodes (CPU flat,
stereo approximate), or producing both eyes inside the GPU emulation (multiview, or replaying
the frame's command stream per eye; CPU about flat, GPU about 2x, a large SDK job). For the
latter: the per-object constants are products the render thread computed (`matWVP`), so a
host-side per-eye change has to be applied in clip space (`P_eye * T_eye * P^-1`, or the
`x += -P00 * dx` shift), not by swapping a view constant. Drawing alternate eyes on
alternate frames is out: at 30 fps each eye would update at 15 Hz while the car moves
almost a metre per frame.

## Integration and stereo options (research)

From a separate research run (OpenXR, runtimes, how other projects put flat games in VR); nothing in it was built. Checked against the camera and render-loop findings above; corrections are marked **Checked:**, and its open questions are answered [below](#answers-to-the-research-questions).

Status: this is research only. Nothing has been built or run. CPU, GPU and pixel figures are estimates taken from [perf.md](perf.md), [framerate.md](framerate.md) and simple geometry. The few measured numbers are marked as measured. [Open questions](#open-questions-for-the-camera-and-render-loop-work) lists what the camera and render-loop work had to answer; the answers follow this section.

### Summary

- **The game must never draw the scene twice.** In a 30 fps race on a 5 GHz Zen 4 core, each frame costs about 4.7 ms of render-thread work and 6.7-7.1 ms of the emulated GPU's command processor (measured, [perf.md](perf.md)). **Checked:** about 2.3 ms of the render thread's 4.7 ms is the game's own work; the rest is waiting for the GPU ([perf.md](perf.md) > Low-end CPUs), so a second eye adds the scene's share of about 2.3 + 7 ms, not 12 ms (estimate in [Stereo](#stereo-drawing-the-scene-twice)).
  - Every frame waits on the command processor, and it is the limit above 60 fps ([framerate.md](framerate.md)).
  - A second guest scene submission doubles both threads. That cuts a fast desktop's race ceiling roughly in half and puts 20-22 fps machines far below 30.
- **The game keeps its own frame rate, and the OpenXR runtime drives the headset.**
  - A separate XR thread resubmits the newest finished game frame at 72-120 Hz. Each frame is tagged with the head pose it was rendered with.
  - The runtime reprojects head rotation. The game is never blocked by `xrWaitFrame`.
- **Build order:**
  1. OpenXR plumbing and a virtual screen.
  2. A head-tracked mono cockpit, with the HUD on a quad layer.
  3. Stereo by depth reprojection of that mono view.
  4. True stereo inside the GPU emulation: Vulkan multiview or per-eye replay. Stage 3 stays as the fallback for weaker GPUs.

  Stages 1-3 keep the CPU cost at flat. Stage 4 keeps it close to flat, but it is a large GPU-emulation job.
- **Not built:** alternate-eye rendering, a wide render cropped per eye, and the game drawing twice.
- **Vulkan only at first.** Every PC runtime that #333 targets supports `XR_KHR_vulkan_enable2`. D3D12 is a later port.
- **PCs that run the game at about 20-22 fps get the virtual screen.** No precedent treats head-tracked 3D at 20 fps as comfortable.

### What this game imposes

- **CPU.** See above. Anything that makes the guest submit the scene twice doubles the two threads that limit the frame.
- **Frame rate.**
  - The default is 30 fps; 20, 40 and 60+ are available.
  - The frame rate is the guest vblank divided by a divider. Dividers 2 and 3 exist today (`frame_pacing::VblanksPerFrame`). 20 and 40 fps keep real-time speed (#263).
  - In VR the guest vblank should become the headset refresh rate, which needs new dividers and rates. See [Pacing](#pacing-against-the-headset).
- **Output path.**
  - `VulkanCommandProcessor::IssueSwap` calls `Presenter::RefreshGuestOutput`, which writes into a 3-image mailbox. The images are A2B10G10R10 and stay in `SHADER_READ_ONLY_OPTIMAL`.
  - The mailbox is built for several consumers, and the presenter is one of them.
- **Existing scene/HUD splits.** There are two, and neither gives a clean HUD-only image with real alpha:
  - The HDR path (`CaptureHdrScene`) takes a snapshot of the scene before the first HUD draw. It finds HUD coverage by comparing the final picture with that snapshot, and the result goes into the guest output's 2-bit alpha.
    - It runs only while HDR is active.
    - It triggers only on the 1280-pitch transition from `2_10_10_10` to `2_10_10_10_AS_10_10_10_10`.
    - It is "a visual difference mask, not game-provided HUD geometry" ([hdr.md](hdr.md)).
  - The native DLSS path draws the HUD, menus and labels into their own raster at their own resolution. That raster is seeded with the upscaled scene ([native_dlss.md](native_dlss.md)).
- **Depth and motion vectors already exist.** The DLSS/FSR 3 work (SDK patches 0046/0057/0058/0072, `fh1_dlss.cpp`) provides:
  - The packed float24 depth decode (view z = B/(1-A-depth) from the `PPMotionBlurDepthHorizonVel` constants).
  - Camera and per-object motion vectors. The player's car uses stencil 15.
  - A main-scene draw filter: `z_enable`, `zfunc != Always`, not window-space, `surface_pitch == 1280`. The pitch test has to be generalized for VR sizes.
  - A per-draw `ndc_offset`.
- **Field of view.**
  - `hooks/ultrawide_projection.cpp` already widens the projection and the culling together.
  - Non-16:9 drawing works with a padded integer EDRAM stride, but only for wider images. A taller-than-16:9 image, such as a near-square eye, is untested ([ultrawide.md](ultrawide.md)).
- **ABI.**
  - The game is compiled against the unpatched nightly SDK headers. So any VR API that crosses the SDK boundary has to be `extern "C"` functions that the game finds with `forza::SdkSymbol`, like `ForzaWheelPlayerDriving` and `rexui_SetDialogContinuousRepaint`.
  - Patches must not change the layout of `VulkanDevice` or `Presenter`, because the game inlines their accessors.
- **Vulkan queue.**
  - The device has one graphics+compute queue. The presenter, DLSS and the "Vulkan Submission" thread from patch 0038 all share it through `AcquireQueue(...)`'s `recursive_mutex`.

### Recommended staged approach

#### Stage 1: OpenXR session and a virtual screen

- **What:**
  - SDK patch A, so the runtime chooses the GPU and adds its Vulkan extensions.
  - The XR thread.
  - A `LOCAL`-space quad, about 3.2 x 1.8 m at 2.5 m, showing the guest output.
  - The desktop window as a mirror.
  - Packaging: the loader, Flatpak permissions and a launcher toggle.
- **Needs from the reverse engineering:** nothing.
- **Cost:** CPU 1x. GPU about 1x at flat resolution. It works on every PC, including the 20-22 fps ones.
- **Value:** on its own this stage is limited, because Virtual Desktop and SteamVR's theater mode already give players a virtual screen. It is the base for everything later, and it stays the mode for menus, loading, movies, pause, photo mode and slow CPUs.
- **Done when:**
  - The session starts and stops cleanly on SteamVR (Windows and Linux), Meta Link, VDXR and Monado/WiVRn.
  - Any failure falls back to flat.
  - Flat play with VR off shows no change in frame time.

#### Stage 2: Head-tracked mono cockpit

- **What:**
  - The camera hook reads the head pose from the XR thread's pose slot: rotation plus seated translation.
  - Projection and culling are widened to the eye FOV plus a margin of about 10° for reprojection edges, as RPCS3 VR does. The drawing size is near-square.
  - SDK patch B exports the pre-HUD scene and the HUD every frame.
  - SDK patch C phase-locks the guest vblank to the headset.
  - Frame tags link each GPU frame to its pose.
  - A projection layer shows the same image in both views from the centre pose, and the HUD goes on a quad.
  - Comfort switches: shake, G-force head motion, motion blur and depth of field off.
- **What the player sees:**
  - No stereo. Rotation-only reprojection shows the image as if at infinity, so the cockpit looks oversized and flat.
  - This is a milestone more than a product. It may still be worth offering as an option on 30 fps machines.
  - In a seated cockpit, `LOCAL` space acts as car-fixed. The interior reprojects almost perfectly at headset rate, and the outside world steps at the game's rate.
- **Cheap experiment in this stage:** submit the decoded depth with `XR_KHR_composition_layer_depth` and the centre pose. Then check whether Meta's ASW 2.0 positional timewarp gives usable parallax "for free". This is unverified, and Meta only at best.
- **Needs from the reverse engineering:** the camera hook point, a widenable frustum that drives culling, near-square or off-axis projection, and frame-skip tracking. **Checked:** the hook point and the culling are done in the prototype (both readers of the view matrix are posed; render culling follows); widening goes through the same FOV/aspect the ultrawide hook already scales; off-axis projection and frame tags are still open.
- **Cost:** CPU 1x. GPU about 1.1-1.2x a mono view at headset resolution, because of the margin.

#### Stage 3: Stereo by depth reprojection

- **What:** render the centre view, covering the union of both eye frusta. Warp it to each eye by ±IPD/2 using the decoded depth, and fill disocclusions from the farther side.
  - Symmetric warping makes holes half the size of warping one eye into the other.
  - A temporal cache can help on the rigid cockpit.
- **Per-eye shift from the centre view** (32 mm half-IPD, about 15-25 px per degree on current headsets):

  | Distance | What is there | Shift per eye |
  |---|---|---|
  | 0.5 m | Steering wheel | 3.7°, 55-90 px |
  | 0.8 m | Dash, A-pillar | 2.3°, 35-57 px |
  | 1.2 m | Mirror | 1.5°, 23-38 px |
  | 5 m | Car ahead | 0.37°, 5-9 px |
  | 20 m | Scenery | 1-2 px |
  | 50 m and beyond | Far scenery | under 1 px |

- **What the player sees:** the world is close to correct. Each eye gets a 30-55 px filled band beside the wheel and A-pillars, which is exactly where the driver looks.
- **Risks:**
  - Glass that writes depth would flatten the world onto the windshield.
  - Motion blur, depth of field and MSAA edges misalign colour and depth. Turn blur off in the final combine but keep the velocity pass, because it produces the motion vectors.
  - Particles, lens flares and transparent surfaces take the wrong depth.
- **Later:** run the warp on the XR thread every headset frame with the newest pose. That is our own positional timewarp, and it works on every runtime, not only Meta's.
- **Precedents:** CryEngine 3's screen-space stereo (Crysis 2), the vorpX Z-buffer mode, and HindsightVR's Xenia fallback.
- **Cost:** CPU 1x. GPU about 1.15-1.3x (a wider centre view plus a warp pass under 1 ms).

#### Stage 4: True stereo inside the GPU emulation

The guest submits each frame once, and the Vulkan backend produces both eyes. Precedents: Dolphin, HindsightVR on xemu and Xenia, the RPCS3 VR fork, openRBRVR 2.0 and 3D Vision Automatic. openRBRVR's notes say multiview "reduces CPU load significantly", and RPCS3 VR moved to it as well.

**Variants:**
- **Multiview.** One draw writes two layers, and `gl_ViewIndex` picks the eye. Every main-scene render target, every resolve, and every texture sampled from a resolve becomes a 2-layer array.
- **Per-eye replay.** Replay the frame's recorded deferred command stream once per eye with a per-eye push constant.
  - Estimated cost: an extra 1.2-1.5 ms on the Vulkan submission thread.
  - It only works cleanly if the main scene and its post-processing fit in one submission. A mid-frame readback or memexport wait breaks it.

**Eye offset**, added as an epilogue to the translated vertex shader. HindsightVR's approach of the same SPIR-V for both eyes plus a push constant leaves the shader-cache key unchanged.
- **Clip-space shift.** `pos.x += -P00*dx` before the divide by w, plus the per-eye asymmetric frustum as an NDC scale and offset. `ndc_offset` is already applied per draw. This needs no knowledge of the game's matrices. Dolphin and 3D Vision use the related form `x += sep*(w - convergence)`.
- **Full reprojection.** `M_eye = P_eye * T_eye * P^-1`. This needs the game's projection matrix P. Keep the game's NDC z unchanged.

**Rules from the precedents:**
- Shadow, light-space and reflection passes stay mono.
- HUD draws and flat full-screen passes get no offset.
- Full-screen passes that rebuild position from depth need a per-eye inverse projection.
- Scissor rectangles are per eye.
- Readbacks and memexport happen once.
- HindsightVR's Xenia layered resolve was disabled at render scale above 1, so scaled resolves need care.

**Cost:** CPU about 1x. GPU about 1.7-2x, because shadows and cube maps are drawn once.

**Later optimization:** hybrid near-field stereo, as in Oculus's hybrid mono rendering. True stereo for the player car (stencil 15), reprojection beyond about 10 m. Its complexity is close to full stereo.

Stage 3 stays as the fallback for weaker GPUs.

#### Not building

- **Alternate-eye rendering** (UEVR AFR, Luke Ross AER):
  - At 30, 40 or 45 fps, each eye updates at 15, 20 or 22.5 Hz.
  - At 100 km/h the car moves 0.93 m per 30 fps frame. A roadside object 3 m to the side and 10 m ahead changes bearing by 1.6° between the two eyes' images, against a true binocular disparity of about 0.35°. The result is false depth or a double image, and rotation reprojection does not fix translation.
  - UEVR's documentation says its AFR mode "causes eye desyncs and usually nausea". Luke Ross says ghosting "becomes worse as the game frame rate gets lower".
- **Wide render cropped per eye:** every object sits on one depth plane. It costs about as much as stage 3 and is no better than mono.
- **Game draws the scene twice:** it doubles the render thread and the command processor, about +10-13 ms per frame at 5 GHz. It is only useful as a correctness reference on fast CPUs, and only if the engine has a clean extra-view entry point. **Checked:** it has one, the second split-screen view (left in from Forza Motorsport 4's split-screen); the scene's share of the two threads is about +5-8 ms per frame here. The verdict for slow CPUs stands.

#### Which PCs get what

| CPU class (flat fps) | Recommended VR mode | Game rate on the headset |
|---|---|---|
| Steam Deck class, 4-core laptops (20-22) | Virtual screen (stage 1) | Any; the quad is redrawn at headset rate |
| Holds 30 solid | Stages 2-3 | 30 on 90 Hz (1/3) or 120 Hz (1/4); 24 on 72 Hz is marginal |
| Holds 45-60+ | Stages 2-4 | Half the headset rate: 36/40/45/60 on 72/80/90/120 Hz |

- Frame synthesis (SteamVR Motion Smoothing, Meta ASW, Virtual Desktop SSW) is designed around exactly half rate. Motion Smoothing scales down to 2-3 synthetic frames per real one.
- How Meta ASW behaves below half rate is not confirmed here.
- **GPU check (rough, from a measurement taken while other games also loaded the GPU):**
  - On the RX 9060 XT, 1440p at render scale 2 (3.7 Mpx) ran 42 fps against a 60 target.
  - Two 1440x1440 eyes are 4.1 Mpx, so stage 4 would land around 36-40 fps on that card.
  - Stage 3 at about 2.3 Mpx leaves headroom.
  - A 1440-wide eye uses a 2560 EDRAM stride: wasted storage, not wasted shading.
  - This needs a real benchmark.

### Comparison of stereo methods

CPU is relative to flat at the same game frame rate. GPU is relative to one mono view at headset resolution; flat 720p is not a useful baseline. All numbers are estimates.

| Method | CPU cost | GPU cost | Comfort | Quality | Complexity | Verdict |
|---|---|---|---|---|---|---|
| Virtual screen (quad layer) | 1x | about 1x at flat resolution | Best: the room is a stable reference | 2D, not immersive | Low | Stage 1; menus, slow CPUs |
| Head-tracked mono | 1x | about 1.1-1.2x (margin) | No stereo; the cockpit looks oversized | Flat image, correct culling if widened | Low-medium (camera hook) | Stage 2 |
| Mono plus depth layer, runtime positional timewarp | 1x | about 1x | Unknown | Unknown; Meta only, untested | Low once depth is exported | Experiment in stage 2 |
| Depth reprojection to two eyes | 1x | about 1.15-1.3x | Good for the world; edge artifacts can strain the eyes | 30-55 px filled bands at the wheel and pillars; transparency wrong | Low-medium (inputs exist) | Stage 3, then the fallback |
| Multiview in the GPU emulation | about 1x (plus layered target and resolve bookkeeping) | about 1.7-2x | Correct stereo | Near native; some screen-space effects from the centre eye | Very high | Stage 4 |
| Per-eye replay of the recorded command stream | about 1x, plus 1.2-1.5 ms on the submission thread | about 1.7-2x | Correct stereo | Near native | Very high; needs one submission per frame | Stage 4 alternative |
| Host re-translates every draw twice | Guest 1x; command processor 1.3-2x | about 2x | Correct stereo | Near native | High | Avoid: doubles the high-fps limit |
| Wide render cropped per eye | 1x | about 1.1-1.2x | No depth; one plane | Looks like a cardboard photo | Low | Do not build |
| Alternate-eye rendering | 1x | 1x (plus optical flow if AER v2-like) | Poor in a moving car | Ghosting, false depth | Medium | Do not build |
| Game draws the scene twice | About 2x the scene's share of the render thread and command processor | about 2x | Correct, but at a much lower frame rate | Best | Medium-high: the second split-screen view exists | Reference only (fast CPUs) |

In every method the runtime corrects head rotation only. Scenery outside the windows still steps at the game's frame rate. The car-fixed cockpit acts as a rest frame, which helps against vection.

### OpenXR integration sketch

OpenXR is a second consumer of each finished game frame, running on its own thread, not a new `Presenter`. A game-side "Forza XR" thread:
- owns the `XrInstance` and `XrSession`;
- runs `xrWaitFrame`, `xrBeginFrame` and `xrEndFrame`;
- owns the per-eye and quad swapchains.

#### Who owns what

- **Game repo** (logic stays out of the patch stack):
  - `forza-recomp/src/platform/xr_session.{h,cpp}`: instance, system, session, spaces, events.
  - `xr_frame_loop.cpp`: the XR thread.
  - `xr_pose.h`: the pose seqlock and frame tags.
  - `xr_math.h`: pose and FOV maths, unit-tested like `tests/frame_pacing_test.cpp`.
  - `thirdparty/OpenXR-SDK` for the loader (Apache-2.0).
- **SDK patches A, B and C** below, all exposed as `extern "C"` functions.

#### Startup and shutdown

1. **Early setup with `--xr`**, in `ForzaApp::OnPostInitLogging` or at the top of `OnPreSetup` before `LoadGpuPlugin`:
   - Call `xrCreateInstance` with `XR_KHR_vulkan_enable2` and `XR_KHR_composition_layer_depth`. Add `XR_KHR_convert_timespec_time` on Linux or `XR_KHR_win32_convert_performance_counter_time` on Windows.
   - Call `xrGetSystem(HMD)`, then `xrGetVulkanGraphicsRequirements2KHR`. The spec requires that call before `xrCreateSession`.
   - Register patch A's hooks.
   - If any step fails, log it and run flat.
2. **The SDK creates the instance, physical device and device through the runtime** (patch A).
3. **In `ForzaApp::OnCreateDialogs`**, where `InstallVulkanHooks` already runs and the provider exists:
   - Create the session with `XrGraphicsBindingVulkan2KHR{instance, physical_device, device, queue_family_graphics_compute(), queue_index}`.
   - Create the `LOCAL` and `VIEW` spaces and the swapchains.
   - Start the XR thread.
4. **On close**, in `OnWindowCloseRequested`/`OnShutdown`: call `xrRequestExitSession`, then destroy everything. The SDK hard-exits right after close, so this has to happen there.

#### SDK patch A: Vulkan creation through the runtime (`librexruntime`)

The runtime has to pick the physical device and add its own instance and device extensions. That happens inside `VulkanInstance::Create`, `VulkanProvider::Create` and `VulkanDevice::CreateIfSupported`. The existing `vulkan_hooks.cpp` only swaps function pointers after the device exists, which is too late.

```c
struct RexVulkanCreationHooks {
  void* user;
  VkResult (*create_instance)(void*, PFN_vkGetInstanceProcAddr, const VkInstanceCreateInfo*, VkInstance*);
  VkPhysicalDevice (*physical_device)(void*, VkInstance);          // xrGetVulkanGraphicsDevice2KHR
  VkResult (*create_device)(void*, PFN_vkGetInstanceProcAddr, VkPhysicalDevice,
                            const VkDeviceCreateInfo*, VkDevice*);  // xrCreateVulkanDeviceKHR
};
extern "C" void rexui_SetVulkanCreationHooks(const RexVulkanCreationHooks*);
```

- **Instance:** both `vkCreateInstance` call sites (the first attempt and the retry without layers) go through `xrCreateVulkanInstanceKHR`.
- **Physical device:** when the hook returns a device, try only that one, and log if the `vulkan_device` or `gpu_vendor` settings disagreed. If `CreateIfSupported` rejects it for missing emulation features, VR fails and the game runs flat.
- **Device:** `vkCreateDevice` goes through `xrCreateVulkanDeviceKHR`.
  - Extensions the runtime adds do not appear in `extensions_`, which is harmless.
  - Monado wants the external memory, semaphore and fence extensions.
- **Optional:** request a second graphics queue when `queueCount >= 2` and give index 1 to XR. NVIDIA exposes several; RADV exposes one.
  - Otherwise share queue 0 and take `AcquireQueue(family, 0)` around `xrBeginFrame`, `xrEndFrame`, `xrAcquireSwapchainImage` and `xrReleaseSwapchainImage`. The spec lists exactly these as needing external synchronization of the queue.
  - `queue_families_[i].queues` is already a vector, so a second queue does not change the layout.

#### SDK patch B: per-swap eye and HUD export (`librexgpu-xenos`)

- **Changes in `IssueSwap` when VR is on:**
  - Run `CaptureHdrScene` and the HUD-coverage pass whether or not HDR is on, and generalize the `hdr_scenes_` ring.
  - In the same deferred stream, copy the pre-HUD scene and the final output with HUD coverage into a ring of 3-4 VR slots owned by the plugin. Being in the same stream keeps queue order, so no cross-thread GPU sync is needed.
  - Tag each slot with `swap_index`, the count of `IssueSwap` calls, counted before the async-placeholder skip.

  ```c
  struct RexVrFrame { uint64_t swap_index; VkImage scene, output; VkImageView scene_view, output_view;
                      uint32_t width, height; uint32_t slot; };
  extern "C" void rexgpu_VrEnable(int on);
  extern "C" int  rexgpu_VrAcquireLatest(RexVrFrame* out); // newest slot whose submission fence completed
  extern "C" void rexgpu_VrRelease(uint32_t slot);         // XR copy finished (its own fence signalled)
  ```

- **The XR thread only takes completed frames.** It never stalls, never shows a half-written image, and does not depend on how patch 0038 orders the refresher's work against the tracking fence.
- **Stage 4 extends this export** with a second layer per slot.

#### SDK patch C: vblank phase lock (`graphics_system.cpp`, the "GPU VSync" worker)

```c
extern "C" void rexgpu_SetVblankPhase(uint64_t guest_tick);
```

- **What it does:** the XR thread converts `predictedDisplayTime` to the guest tick with the timespec/QPC conversion extension. The worker then nudges `last_frame_time` like a PLL, so guest vblanks lock to the display.
- **Why it is needed:** without it, the SDK's 1 ms timer beats against the headset clock. Frames then alternate between 2 and 4 display periods, the same problem `PresentPacer` fixes for monitors (#263).

#### Frame tags: the pose a frame was rendered with

- **Writing the pose.** After `xrWaitFrame`, the XR thread writes head and eye poses in `LOCAL`, plus each eye's `XrFovf`, into a seqlock.
  - It uses `xrLocateViews` at `predictedDisplayTime + latency`.
  - The latency is a measured EMA from game-frame start to display, capped at about 30-50 ms.
- **The camera hook** reads that slot when it builds the view. It stores `FrameTag{eye_pose[2], fov[2], eye_mask, target_time}` for the main frame it belongs to.
  - The render pass (`sub_8259F3E8`, counter at render object `+0x948`) can skip main frames, so the tag must follow the frame the render thread actually draws.
- **At VdSwap** (next to `frame_time::OnGuestSwap`), the game pushes the tag with its swap count. VdSwap and `IssueSwap` are 1:1, so `RexVrFrame::swap_index` pairs each GPU frame with its tag.
- **Pose rule from the spec:** each projection view carries the pose and FOV the image was rendered with, not the newest pose. The runtime maps the difference, which is what makes its reprojection correct.

#### Swapchains and format conversion

- **Eyes:** one swapchain per eye, not a 2-layer array, so a single eye can be updated and the other resubmitted.
  - Size: the game's scene raster, not the recommended size; the runtime scales.
  - Format: `R8G8B8A8_SRGB`/`B8G8R8A8_SRGB` from `xrEnumerateSwapchainFormats`, created with `MUTABLE_FORMAT | COLOR_ATTACHMENT (| STORAGE)`.
- **Conversion:** one small pass samples the A2B10G10R10 source and writes the already gamma-encoded values through a **UNORM view** of the sRGB image. A `vkCmdBlitImage` into sRGB would encode twice.
  - The image is then moved to `COLOR_ATTACHMENT_OPTIMAL` before `xrReleaseSwapchainImage`, as the spec requires.
- **Resubmitting old frames is fine.** `xrEndFrame` uses the most recently released image of each swapchain. A game frame shown for 3 headset frames needs no new acquire or copy.

#### The XR thread loop

```
loop: xrPollEvent -> READY: xrBeginSession(STEREO) / STOPPING: xrEndSession / LOSS_PENDING: flat
  xrWaitFrame(&fs)                                   // only this thread blocks on the headset
  write pose slot (xrLocateViews(LOCAL, fs.predictedDisplayTime + latency))
  [queue lock] xrBeginFrame
  f = pacer.Pick(rexgpu_VrAcquireLatest, fs.predictedDisplayTime)
  if f is new && fs.shouldRender: acquire eye/HUD images, record convert pass, submit;
                                  [queue lock] xrReleaseSwapchainImage; rexgpu_VrRelease(previous)
  layers = driving ? { Projection(LOCAL, views[i] = {tag.eye_pose[i], tag.fov[i], eye[i]}, depth optional),
                       Quad HUD }
                   : { Quad cinema }
  [queue lock] xrEndFrame(fs.predictedDisplayTime, layers)
```

- The pacer picks a frame by its display slot, not simply the newest complete one. It holds frame N+1 until its slot, reusing `PresentPacer`'s adaptive-percentile offset from `frame_pacing.h`.

#### Pacing against the headset

- **The game never blocks on `xrWaitFrame`.** Instead, the guest vblank becomes the headset rate, phase-locked by patch C, and the divider is headset Hz / game fps.

  | Headset | Half rate (fast CPUs) | Lower rates |
  |---|---|---|
  | 72 Hz | 36 | 24 |
  | 80 Hz | 40 | 20 |
  | 90 Hz | 45 | 30 |
  | 120 Hz | 60 | 40, 30 |

- **New work:** dividers 2 and 3 exist. A divider of 4 and the 24, 36 and 45 fps rates are new, and real-time simulation speed at those rates needs checking.
- **The desktop window becomes a mirror:**
  - `QueuePresent`'s `PresentDelayNs` returns 0.
  - The mirror uses MAILBOX or IMMEDIATE. In `kGuestOutputThreadImmediately` mode the command-processor thread presents, so a 60 Hz FIFO monitor would throttle emulation.

#### Depth and frame synthesis

- **Depth layer:** `XR_KHR_composition_layer_depth` is supported by every runtime in the Khronos inventory.
  - The source is the depth that the DLSS/FSR 3 path already reconstructs. It needs near/far values and reversed-Z handling.
  - Meta's ASW 2.0 uses depth for positional timewarp.
- **App motion vectors** (`XR_EXT_frame_synthesis`, `XR_FB_space_warp`):
  - The inventory shows support only for SteamVR on Steam Frame among PC runtimes. It is not on Link or SteamVR for other headsets.
  - Its ratification status is unclear. Re-check before relying on it.

#### Packaging

**Checked** (and partly done): [Packaging](#packaging) under the OpenXR session.

- **Loader:** ship `openxr_loader` (`.dll`, `libopenxr_loader.so.1`).
- **Flatpak:** add the following, which match WiVRn's instructions for Flatpak apps:
  - `--filesystem=xdg-run/monado_comp_ipc`
  - `--filesystem=xdg-run/wivrn:ro`
  - `--filesystem=xdg-config/openxr:ro`
  - `--filesystem=xdg-config/openvr:ro`
  - A system-wide `/etc/xdg/openxr` runtime is not visible inside the sandbox.
  - Whether host-installed runtime libraries load inside the sandbox is open.
- **Launcher:** the VR toggle sets `--xr`, forces Vulkan, and picks a frame rate for the headset's refresh rate.

#### D3D12 (later, Windows)

- **Adapter:** pick the one whose `AdapterLuid` matches `XrGraphicsRequirementsD3D12KHR::adapterLuid`, and create the device at `max(11_0, minFeatureLevel)`.
- **Binding:** `XrGraphicsBindingD3D12KHR{device, direct queue}`. Images go back to `RENDER_TARGET` state at release.
- **Main cost:** re-implementing patch B. The D3D12 plugin has no pre-HUD snapshot or scene/HUD split.
- **Coverage without it:** WMR is the only listed PC runtime without Vulkan support, and Windows 11 24H2 removed it. VDXR reaches Vulkan through its interop layer.

### HUD, menus and overlays

- **When not driving** (`forza::telemetry::IsDriving()` is false): menus, loading, movies, pause, photo mode and the map go on the cinema quad. Every serious precedent does this, including RPCS3 VR, HindsightVR, ETS2 and openRBRVR.
- **While driving:**
  - The eyes get the pre-HUD scene.
  - The HUD goes on a `LOCAL` quad about 1.2 m ahead, below the eye line, using `BLEND_TEXTURE_SOURCE_ALPHA | UNPREMULTIPLIED_ALPHA`, drawn after the projection layer.
  - Distance and size should be user-adjustable.
  - A HUD baked into the eye image would follow the head, sit in the periphery of a wide eye FOV, and conflict with cockpit depth in stereo.
- **Use quads, not cylinders.** SteamVR has no cylinder layers.
- **Extracting the HUD is the open problem.** Neither existing split gives HUD-only RGBA. Options:
  1. **Difference mask between the final picture and the snapshot** (version 1):
     - Write it to a separate dilated 8-bit image; the guest output's 2-bit alpha is too coarse.
     - Opaque elements work.
     - Translucent panels carry a frozen copy of the scene behind them, at the wrong depth and updating at the game rate.
  2. **Redirect HUD draws** (everything after the scene-to-HUD transition) into a cleared transparent target. This is clean if the game's HUD blending writes usable alpha, which is unknown.
- **HUD layout:** the HUD is laid out for the eye raster's aspect ratio. Patch 0063's HUD edge anchors can pull elements inward.
- **World-space markers** (GPS arrows, checkpoints, name tags): if the 3D pass draws them, they stay correct in the scene. If they are screen-space overlays, they land on the HUD quad at the wrong depth. 3D Vision placed such elements at the depth under them.
- **Host ImGui overlays** (toasts, performance overlay, graphics menu): an optional third quad, through the SDK's detached `AppUIDrawContext`.
- **Comfort settings**, all from precedents:
  - Camera shake, G-force head motion, look-to-apex, motion blur and depth of field off.
  - Recenter and seat-position controls.
  - Configurable IPD and world scale.

### Open questions for the camera and render-loop work

| # | Question | Decides |
|---|---|---|
| 1 | Where are the cockpit camera's view and projection built? Is there a guest function or object to hook, and which vertex-shader constants carry them (axes, handedness)? | Stage 2 hook; P for `P_eye*T_eye*P^-1` in stage 4 |
| 2 | Does one frustum drive culling, LOD, shadow-cascade fitting and streaming? Can it be widened like the ultrawide hook, to the eye FOV plus about 10°? | Culling holes in stage 2 (RPCS3 VR, HindsightVR and openRBRVR all had to widen it) |
| 3 | Can the game render a near-square, asymmetric (off-axis) frustum? Is the projection built from FOV and aspect, or can a matrix be injected? | Eye raster size; stage 2 |
| 4 | Is the cockpit drawn with its own projection, near plane or depth partition? | Depth decode in stage 3; clip-shift size in stage 4 |
| 5 | How many world units make a metre, and where is the driver's eye point in each car? | IPD, world scale, seat position |
| 6 | Where do shake, G-force head motion, look-to-apex and depth of field come from, and can they be switched off? | Comfort |
| 7 | Is the camera used outside rendering (audio listener, streaming, AI)? Is it safe to change it per frame with the head pose? | Stage 2 |
| 8 | Is FM4-style Kinect head-tracking code present in FH1? `xam_nui.cpp` says so; public sources show only voice GPS. | A ready camera input whose culling already follows the head |
| 9 | Where should a frame tag attach so it follows the frame the render thread actually draws (`+0x948` skip counter)? Are VdSwap and `IssueSwap` 1:1 in every mode? | Frame tags |
| 10 | How many frames separate building the camera from the swap? | Pose prediction and the latency cap |
| 11 | Can the render thread redraw the last frame with a new camera without running the simulation? | Decoupling head rate from game rate; otherwise only GPU command replay (Dolphin VR's opcode replay) remains |
| 12 | Is the renderer forward or deferred? Which resolves feed later passes (post-processing, bloom, reflections, mirror, minimap), and which full-screen passes rebuild position from depth? | How many textures need two layers in stage 4; per-eye inverse projections |
| 13 | Do the main scene and its post-processing fit in one deferred submission per frame, or do `readback_resolve` or memexport waits split them? | Per-eye replay or multiview in stage 4 |
| 14 | Does cockpit glass write depth before the depth resolve that the velocity pass reads? | Whether stage 3 is viable |
| 15 | Does `CaptureHdrScene`'s trigger fire in cockpit view, in every race and menu mode, and at non-1280 and near-square rasters? | Patch B's eye source |
| 16 | Do HUD draws form an identifiable range after the transition, and does their blending write usable alpha? Are world-space markers drawn in the scene pass or the HUD pass? | HUD extraction method |
| 17 | Is there a clean extra-view path, such as the rear-view mirror? | Only for judging the game-draws-twice reference |

**Outside the reverse engineering (tests and checks):**
- **Patch 0038 ordering.** Does the refresher's work reach the queue before the presenter paints? The XR path avoids depending on it, but the existing presenter path deserves a check.
- **Queue sharing.** On RADV there is only one graphics queue. Does holding the shared queue lock across SteamVR or Meta `xrEndFrame` stall the submission thread?
- **New frame rates.** Do the game's simulation and pacing hold real-time speed at 24, 36 and 45 fps?
- **Runtime support for depth and motion vectors.** Which PC runtimes accept app depth and motion vectors today: Link ASW 2.0, VDXR, SteamVR? How well do Monado, WiVRn and ALVR reproject? Monado lists 3DoF timewarp; spacewarp is reported as in development.
- **Flatpak.** Do host-installed Monado or WiVRn runtime libraries load inside the sandbox? WiVRn's Flatpak build may be the only practical path.
- **GPU cost.** What is the real cost at headset resolution under the integer EDRAM stride? This needs a benchmark.
- **Player tolerance.** Do players accept the world stepping at 30-45 fps with runtime synthesis? Only a headset test can answer.

### Licences

| Project | Licence | Reuse here (GPL-3.0-or-later) |
|---|---|---|
| HindsightVR | GPLv2+ | Code can be used under GPLv3; keep its notices |
| RPCS3 VR fork | GPLv2 | Ideas only (GPLv2-only is not compatible with GPLv3) |
| Dolphin | GPLv2+ | Code can be used under GPLv3; keep its notices |
| PPSSPP | GPL | Check the version first (2 only: ideas only) |
| openRBRVR | MPL-2.0 | File-level copyleft; MPL-2.0 files can be combined with GPLv3 code, keep their notices |
| UEVR | No licence id reported by GitHub | Check before reusing |
| OpenXR-SDK loader | Apache-2.0 | Ship (Apache-2.0 is compatible with GPLv3) |

### Sources

OpenXR spec and runtimes:
- https://registry.khronos.org/OpenXR/specs/1.1/html/xrspec.html (`XR_KHR_vulkan_enable2` initialization, concurrency and swapchain layout; `xrWaitFrame`/`xrEndFrame`; `XrCompositionLayerProjectionView`; D3D12 resource state; `XR_EXT_frame_synthesis`)
- https://registry.khronos.org/OpenXR/specs/1.1/man/html/XR_KHR_vulkan_enable2.html
- https://registry.khronos.org/OpenXR/specs/1.1/man/html/xrEndFrame.html
- https://registry.khronos.org/OpenXR/specs/1.1/man/html/XrCompositionLayerSpaceWarpInfoFB.html
- https://github.khronos.org/OpenXR-Inventory/runtime_extension_support.html
- https://developer.android.com/develop/xr/openxr/extensions/XR_EXT_frame_synthesis
- https://monado.pages.freedesktop.org/monado/vulkan-extensions.html
- https://fosdem.org/2026/events/attachments/T38AKM-monado-and-beyond/slides/266219/monado_an_tfzl8ak.pdf (found only through a search snippet; the link did not resolve)
- https://github.com/mbucchia/VirtualDesktop-OpenXR
- https://github.com/WiVRn/WiVRn
- https://community.khronos.org/t/display-overlay-without-submitting-frame-possible/108397
- https://forums.developer.nvidia.com/t/vkwaitforfences-does-not-return-in-a-timely-manner-with-sync-fd/373147
- https://www.pcworld.com/article/2490448/windows-11s-2024-update-finally-abandons-this-once-promising-feature.html

Reprojection and frame synthesis:
- https://developers.meta.com/horizon/blog/developer-guide-to-asw-20/
- https://developers.meta.com/vr/documentation/native/pc/asynchronous-spacewarp/
- https://www.meta.com/blog/introducing-asw-2-point-0-better-accuracy-lower-latency/
- https://developers.meta.com/horizon/documentation/native/android/mobile-asw/
- https://roadtovr.com/steamvr-motion-smoothing-asw-alex-vlachos/
- https://roadtovr.com/steamvr-update-brings-motion-smoothing-easier-keybinding/
- https://bit-tech.net/news/valve-launches-steamvr-motion-smoothing-beta/1
- https://uploadvr.com/reprojection-explained/
- https://uploadvr.com/virtual-desktop-synchronous-spacewarp/
- https://developers.meta.com/vr/blog/hybrid-mono-rendering-in-ue4-and-unity/
- https://www.lgdv.tf.fau.de/files/2019/07/HybridVR-reduced.pdf

Flat games in VR:
- UEVR: https://github.com/praydog/UEVR, http://docs.uevr.io/usage/overview.html, https://github.com/praydog/UEVR/blob/master/src/mods/vr/FFakeStereoRenderingHook.cpp, https://github.com/hatrd/UEVR
- Luke Ross R.E.A.L.: https://uploadvr.com/rdr2-vr-mod-not-soon/, https://www.patreon.com/realvr/posts/aer-v2-152398605, https://mixed-news.com/en/real-vr-mod-dlss-ray-reconstruction/, https://mixed-news.com/en/kingdom-come-deliverance-ii-vr-mod/
- vorpX: https://www.vorpx.com/more-headtracking-z-buffer-vs-geometry-3d/, https://www.vorpx.com/features/, https://thelatenightsession.com/2019/08/03/2d-games-in-3d-via-vr-virtual-desktop-and-vorpx/
- NVIDIA 3D Vision Automatic: https://archive.docs.nvidia.com/gameworks/content/technologies/desktop/nv3dva_background.htm, https://archive.docs.nvidia.com/gameworks/content/technologies/desktop/nv3dva_stereoscopic_issues.htm
- Dolphin: https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/VideoCommon/GeometryShaderGen.cpp, https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/VideoCommon/GeometryShaderManager.cpp, https://dolphin-emu.org/blog/2015/05/13/a-second-perspective/, https://forums.dolphin-emu.org/Thread-stereoscopic-3d, https://github.com/CarlKenner/dolphin/blob/VR-Hydra/Source/Core/VideoCommon/VR.cpp, https://forums.dolphin-emu.org/Thread-oculus-rift-support, https://www.generationamiga.com/2026/07/06/dolphinxr-v0-3-improves-gamecube-and-wii-vr-emulation-with-major-metroid-prime-fixes/
- PPSSPP: https://github.com/hrydgard/ppsspp/blob/master/Common/VR/PPSSPPVR.cpp, https://www.ppsspp.org/docs/reference/vr-apk
- CitraVR: https://mixed-news.com/en/meta-quest-citravr-nintendo-3ds-emulator/
- RPCS3 and the VR fork: https://dsogaming.com/?p=136128, https://github.com/hookmanuk/rpcs3, https://github.com/hookmanuk/rpcs3/releases, https://github.com/hookmanuk/rpcs3/blob/openxr/RPCS3_VR_FORK_STARTING_POINT.md, https://github.com/hookmanuk/rpcs3/blob/openxr/vr-games.md, https://github.com/hookmanuk/rpcs3/blob/openxr/bin/vr_profiles/BCUS98114.json
- PenguinScreen2: https://www.generationamiga.com/2026/08/16/penguinscreen2-adds-vr-head-tracking-to-ace-combat-5-and-more-ps2-games/
- HindsightVR: https://gitlab.com/aknumbers/hindsightvr, https://gitlab.com/aknumbers/hindsightvr/-/blob/dev/flow.md, https://gitlab.com/aknumbers/hindsightvr/-/blob/dev/docs/adapters/xenia/halo3.md, https://gitlab.com/aknumbers/hindsightvr/-/blob/dev/docs/methods/emulator-render-path.md
- BotW BetterVR: https://github.com/Crementif/BotW-BetterVR, https://roadtovr.com/zelda-breath-of-the-wild-vr-mod/
- openRBRVR: https://github.com/Detegr/openRBRVR, https://github.com/Detegr/openRBRVR/blob/master/FAQ.md, https://github.com/Detegr/openRBRVR/blob/master/src/RBR.cpp, https://github.com/Detegr/openRBRVR/blob/master/src/Dx.cpp, https://github.com/Detegr/openRBRVR/releases
- Euro Truck Simulator 2: https://www.engadget.com/2013-10-24-euro-truck-simulator-2-shifts-gears-to-add-oculus-rift-support.html, https://wiki.vronlinux.org/docs/games/ets2-ats/
- MSFS: https://forums.flightsimulator.com/t/why-does-the-rdrthread-double-in-vr/766129
- Star Wars: Squadrons: https://www.ea.com/studios/motive/news/the-making-of-vr-part-2
- Single-pass stereo: https://developer.nvidia.com/vrworks/graphics/vrsli, https://docs.unity3d.com/2019.1/Documentation/Manual/SinglePassInstancing.html, https://saschawillems.de/blog/2018/06/08/multiview-rendering-in-vulkan-using-vk_khr_multiview/, https://docs.kdab.com/kdgpu/unstable/multiview.html
- CryEngine 3 and depth-based stereo: https://mcvuk.com/key-release-the-3d-powered-cryengine-3-2/, https://www.tomshardware.com/reviews/tridef-stereoscopic-3d-gaming%2C3019-10.html, https://www.ee.cityu.edu.hk/~lmpo/publications/2012_ICASSP_Depth.pdf
- Comfort and rest frames: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9762595/, https://www.faac.com/blog/2021/02/17/simulator-sickness-design-implications-of-the-rest-frame-hypothesis/, https://paper.nweon.com/15302
- Forza Kinect: https://www.engadget.com/2012-06-06-forza-horizon-to-feature-voice-activated-kinect-gps.html, https://www.autoguide.com/auto-news/2011/06/forza-motorsport-4-features-head-tracking-via-kinect-video.html

This repo:
- Docs: [perf.md](perf.md), [framerate.md](framerate.md), [rendering.md](rendering.md), [native_dlss.md](native_dlss.md), [hdr.md](hdr.md), [ultrawide.md](ultrawide.md), [sdk_patches.md](sdk_patches.md)
- Game code: [vulkan_hooks.cpp](../forza-recomp/src/platform/vulkan_hooks.cpp), [frame_pacing.h](../forza-recomp/src/platform/frame_pacing.h), [frame_time.cpp](../forza-recomp/src/hooks/frame_time.cpp), [ultrawide_projection.cpp](../forza-recomp/src/hooks/ultrawide_projection.cpp), [xam_nui.cpp](../forza-recomp/src/kernel/xam_nui.cpp)
- SDK patches: [0057](../patches/rexglue-sdk/0057-dlss-scene-jitter-coverage.patch), [0058](../patches/rexglue-sdk/0058-dlss-reprojection-and-tile-jitter.patch)
- Flatpak manifest: [the Flatpak manifest](../flatpak/io.github.dyotson.ForzaHorizon1ReImagined.yml)
- SDK sources, in the checkout the build uses: `src/ui/vulkan/vulkan_instance.cpp`, `vulkan_provider.cpp`, `vulkan_device.cpp`, `vulkan_presenter.cpp`, `src/ui/presenter.cpp`, `src/graphics/vulkan/command_processor.cpp` (`IssueSwap`, `CaptureHdrScene`, `EndSubmission`), `src/graphics/graphics_system.cpp` (GPU VSync worker), `src/ui/d3d12/d3d12_provider.cpp`, `include/rex/graphics/vulkan/deferred_command_buffer.h`

## Answers to the research questions

Numbers refer to the table above.

1. **View and projection.** The view is built by the director update (`sub_82858288`) into
   director + 240 (row-vector, left-handed, x right, y up, z forward). `sub_825A9D20` hands
   it to the presentation camera with SetViewTransform, and the projection's inputs with
   SetFOV, SetAspectRatio and SetZClip; the projection itself is built on the render side.
   The hook point is the prototype's. The GPU gets per-object products the render thread
   computes (`matWVP`, `matWV`, `matVP`, `matVInv` in the car shaders, `CamViewProjMat`,
   `CamProjMat`, `PCamViewProjMat` in the engine's), not a view constant; their register
   numbers per shader are not mapped. P can be rebuilt from FOV, aspect and clip planes, or
   read from the render-side `CPresentationCamera` + 80.
2. **One frustum?** The view matrix has two readers: the presentation camera (render-side
   culling, shadows, mirrors) and the game-side frustum `sub_829A4860`. Both follow the
   prototype's pose; nothing was missing at 60-160 degrees, and posing the game-side one
   or not makes no visible difference. Both take their aspect from `sub_823E22C8`, which
   the ultrawide hook already widens, and the FOV from director + 304, so a margin for
   reprojection is a scale in the same hook. Level of detail is distance-based
   (`CarLODTransDist` etc. in the render scenarios) and barely moves with a head offset.
3. **Off-axis, near-square.** By default the projection is symmetric, from FOV and aspect.
   The interface also has SetProjectionTransform (+172, a full matrix; used by the UI's
   scene cameras, `sub_82DBDE80`) and `InitPerspectiveObliqueCamera`; whether the main view
   honours an injected matrix is not tested. Taller-than-16:9 rasters are untested.
4. **Cockpit projection.** One near/far pair per camera; the in-car camera's near plane is
   0.1 m (`Driver\CamNearClip`), FOV 62 degrees. No separate interior camera or pass in
   the camera code; a renderer-side depth partition is not checked.
5. **Units, eye point.** Metres: the chase camera sits 5.1 m from the car's position, and
   `CameraSettings.ini` gives offsets in metres. The eye is
   `Driver\DefaultOffsetFromSteeringWheel` (0, 0.21, -0.51) m from the steering wheel, plus
   the car's `CamCockpitOffsetX/Y/Z`; the result is the director's position (+176) each
   frame.
6. **Shake, G-forces, depth of field.** Camera physics layers in `CameraPhysics.xml` map
   input channels (an enum at `0x82074100`: `Gs_Lateral`, `Gs_Longitudinal`, collisions,
   `Slip`, `Speed`, `RPM`, `SurfaceBumpOffset_*`, `ShoulderYaw/Roll`, `HeadHomeOffset*`,
   `HeadTracking*`, ...) through curves and springs to `CarSpace`/`CameraSpace` XYZ/YPR
   offsets. `CameraPhysicsSansEffects.xml` is the same without effects, most likely what the
   player's Camera Effects option (Default / Vignette / Off) selects: not verified. Depth of
   field and motion blur are render-scenario switches (`DepthOfField`, `MotionBlur`).
7. **Camera outside rendering.** The prototype poses only the matrix; the director's
   position, forward and up (+176/+208/+224) stay the car's, so whatever reads those (the
   sound listener has `lockListenerToCamFOV`; distances) sees the unposed camera. Changing
   the pose every frame is safe: nothing integrates it, the matrix is put back after each
   reader, and 15 pose changes and 21 reader switches during a race left no trace in the log.
8. **Kinect head tracking.** Present: the option `IDS_OptionHeadTracking` (On/Off),
   `ForzaProfileHeadTrackingSettings`, `ForzaUI::CHeadTrackingSetup`, `CNatalCameraManager`,
   and camera-physics input channels 27-31 (`HeadTrackingX/Y/Z/Yaw/Pitch`). In FH1's
   `CameraPhysics.xml` their layers are disabled (`Enabled="0"`) and amplify through curves
   and springs (25 degrees of head yaw become about 9 degrees of camera yaw): made for a TV.
   VR keeps the direct matrix pose.
9. **Frame tags.** The camera is built and handed over in the world update on the main loop
   thread (`sub_823E3218` > `sub_825AA1B0` > `sub_825A9D20`); the render thread draws it
   later from the deferred queue. The tag belongs in the `sub_825A9D20` hook: the pose that
   frame used. VdSwap against `IssueSwap` is not checked.
10. **Camera to swap.** Not measured. At least the main loop to render thread hand-over (one
   frame of queue); stamping a pose and finding it at VdSwap would measure it.
11. **Redraw with a new camera.** No entry point found. The render-side presentation camera
   holds the matrix (+16, dirty flag +464), but re-running the render thread's frame without
   a new frame from the main loop is not a path the game has.
12. **Renderer.** From [rendering.md](rendering.md): the scene is 4x MSAA in three EDRAM
   tiles (predicated tiling), post-processing runs once at the end, the velocity pass reads
   the resolved depth and stencil, mirrors are extra camera views (CCamRearView). A free-roam
   frame is about 3,000 host draws: the scene's three tiles 1,969, shadow cascades about
   330, another depth-only pass 388, post-processing about 250 (table in
   [Stereo](#stereo-drawing-the-scene-twice)). Forward or deferred is not determined here.
13. **One submission per frame?** Not checked.
14. **Glass and depth.** Not checked.
15. **`CaptureHdrScene` in the cockpit.** Not checked directly; the same front-buffer
   transition separated scene and HUD in every cockpit screenshot's frame.
16. **HUD range and alpha.** The range is identifiable (everything after the transition);
   whether its blending writes usable alpha is not checked. No world-space markers were in
   the screenshots' frames.
17. **Extra-view path.** Yes: the second split-screen view, and the mirror cameras.

## Recommended plan

The research's four stages, mapped to the three asked for. Sizes are rough: S is days,
M one to two weeks, L a month or more of focused work.

**(a) Head tracking on a flat virtual screen**

1. OpenXR session and a virtual screen (research stage 1): SDK patch A (Vulkan created
   through the runtime), the XR thread, a quad showing the guest output, the window as a
   mirror, loader and Flatpak permissions, a launcher toggle. **M.** Works on every PC,
   and stays the mode for menus, loading, movies and slow CPUs.
2. Head-tracked mono cockpit (research stage 2): the prototype's hook reading the XR pose
   slot instead of the FIFO (**S**, the hook point and culling are done), FOV/aspect widened
   to the eye FOV plus a margin through the same hook (**S**), the pre-HUD scene and the HUD
   exported per swap (patch B, **M**), guest vblank phase-locked to the headset (patch C,
   **S-M**), frame tags (**S**), the HUD on a quad. Gate: is a mono cockpit at 30 fps with
   runtime reprojection comfortable enough to ship as an option?

**(b) Stereo**

3. Depth reprojection of the mono view to two eyes (research stage 3), using the DLSS/FSR 3
   depth decode. **M.** CPU flat. First check that cockpit glass does not write depth.
4. Before or beside it, a cheap reference: put the second split-screen view to use as the
   other eye on a fast PC (**S-M** to find out, larger if the split-screen renderer needs a
   second player). Gives correct stereo to compare stage 3 against and measures what the
   game drawing twice really costs.
5. True stereo inside the GPU emulation (research stage 4: multiview or per-eye replay).
   **L.** Only after 3 has shipped and its artifacts are known.
6. Worth trying early because it pays for any of these and helps flat play: drawing the
   main scene without predicated tiling (one pass instead of three for the command
   processor). **S** to try, **M** if it needs SDK work.

**(c) Comfort and UI**

7. Camera effects off (shake, G-force head motion: the effects-free camera physics), motion
   blur and depth of field off, the game's look-around and look-back disabled while
   tracked, recenter and seat position, IPD and world scale. **S-M.**
8. HUD on a panel (cleared UI target with alpha, placement), menus and pause on the cinema
   quad, the player's missing body (an option to hide the arms, or accept it). **M.**

Order: 1, 2, then 7 with it (comfort is needed as soon as the head moves the camera), 3, 8,
and 4 and 6 in parallel with 3. 5 last.

## Open questions

- Can the main view run non-tiled (the split-screen `TilingScenario 4`), or can the SDK
  skip the redundant predicated tiles with host render targets? That would cut the command
  processor's scene work to about a third.
- What a second view really costs: a two-player split-screen race against a one-player race,
  CPU per thread and GPU per frame, if FH1's menus still reach split-screen (player 2 can be a
  guest; the scripted input needs a persistent second pad, `FORZA_PAD_PER_USER=1`).
- Can a split-screen view exist without a second player and car, and can its target be a
  full-size eye image?
- Does the main view take an injected off-axis projection (SetProjectionTransform, or
  `InitPerspectiveObliqueCamera`), and does a near-square raster work with the EDRAM tiling?
- What does the game-side frustum (`sub_829A4860` > `sub_829A7448`) decide? Nothing visible
  changed with it posed or not.
- How many frames lie between the camera's hand-over and the swap?
- Does the HUD's blending leave usable alpha on a cleared target?
- Which camera physics file does the Camera Effects option pick, and does it remove all of
  the in-car motion?
- The research's own list (runtime depth support, queue sharing, new frame rates, Flatpak,
  GPU cost at headset resolution) still applies.
