// forza - VR through OpenXR (docs/vr.md, #333): a session on the headset next to the desktop
// window, which keeps showing the game.
//
// vr_mode = off (default): nothing here runs and no OpenXR library is loaded.
// vr_mode = screen: the game's picture on a virtual screen (an OpenXR quad layer, 2.2 x 1.24 m
//   at 2.5 m in front of the seat: 47 degrees wide), redrawn at the headset's rate.
// vr_mode = cockpit: as screen, but in the in-car view the head pose turns and moves the camera
//   inside the car (hooks/vr_head_pose.cpp) and the picture becomes a projection layer drawn
//   for the pose it was rendered with (the runtime reprojects head motion between game frames).
//   The game draws exactly the eyes' fields of view plus a margin (an axis pitched to their
//   middle, and a projection aspect that need not be the picture's), its camera physics (shake,
//   G-force lean, speed field of view) off. Mono: both eyes see the same image. Other cameras,
//   menus and loading stay on the screen. Pads keep working while the desktop window has no
//   focus (ReIGlue 0392).
//
// The OpenXR runtime creates the Vulkan instance and device and picks the GPU (ReIGlue 0390);
// the newest guest output reaches this thread with the tag of the frame it shows (ReIGlue
// 0391). Any failure (no loader, no runtime, no headset, a lost session) logs one line that
// starts with "VR:" and the game goes on flat.
#pragma once

#include <cstdint>

namespace rex::system {
class IGraphicsSystem;
}
namespace forza::vr {
struct HeadTransform;
struct HeadPose;
}

namespace forza::xr {

// OnPreSetup, before the GPU plugin creates its Vulkan provider: loads the loader, creates the
// instance, finds the headset and registers the Vulkan creation hooks.
void Prepare();
// Once the graphics provider exists: starts the session and its thread if the Vulkan device was
// created through the runtime. Safe to call more than once.
void Start(rex::system::IGraphicsSystem* graphics);
// Ends the session and joins the thread (window close and shutdown).
void Shutdown();

// Cockpit mode with a running session: the camera hook takes its pose from the headset.
bool CockpitActive();
// Any running OpenXR session, including the virtual screen and menus (input focus policy).
bool SessionRunning();
// Main loop thread, for each in-car view built: the head transform (recentred, with the seat
// offset, turned to the drawn axis), the vertical field of view and the projection aspect to draw
// with, and a new frame tag. False: no pose.
bool CockpitView(vr::HeadTransform& head, float& vertical_fov, float& aspect, uint32_t& tag);
// Main loop thread: the posed view matrix (64 guest bytes) that carries `tag`.
void RememberPosedView(uint32_t tag, const uint8_t matrix[64]);
// Render thread: a view matrix reached the render-side camera (CPresentationCamera at guest
// address `camera`).
void OnRenderView(uint32_t camera, const uint8_t matrix[64]);
// Render thread: the head transform the view it draws now was posed with (false: not a posed view).
bool RenderViewHead(vr::HeadTransform& head);
// Render thread, at VdSwap after the SDK wrote the swap packet at guest address `packet`:
// puts the tag of the frame being swapped into it.
void TagSwap(uint8_t* base, uint32_t packet);

// vr_trace: main loop thread, each view the game builds while a session runs: the active camera
// (its vtable and a static name, "?" when unknown; for the in-car camera also its look-around
// angle, radians) and whether the head pose went into it. The OpenXR thread logs it with the head
// pose about 10 times a second.
bool Tracing();
void NoteView(uint32_t camera_vtable, const char* camera_name, float look_angle, bool posed);

// Camera hooks (hooks/vr_head_pose.cpp): the projection aspect the VR cockpit draws with while a
// posed view is handed over on the calling thread, else 0. The viewport query's hook
// (hooks/ultrawide_projection.cpp) answers with it.
float PosedViewAspect();

// Camera hooks (hooks/vr_head_pose.cpp): the test pose (vr_head_pose_test with FORZA_VR_POSE or
// FORZA_VR_POSE_FIFO) while one is set; in the VR cockpit it is added to the headset's pose for
// the game's camera only (the projection layer keeps the real head), so a scripted look-around
// shows in the headset as if the head had turned. False: none.
bool TestPoseOffset(vr::HeadPose& offset);
// Camera hooks: a test FIFO's "stereo 0" (VR true stereo off until "stereo 1"; a test aid).
bool StereoOffTest();
// Camera hooks: a test FIFO's "framewait 0" (vr_frame_wait off until "framewait 1"; a test aid).
bool FrameWaitOffTest();
// hooks/vr_cockpit_space.cpp: a FIFO's "parallax 0|1|2" overrides vr_cockpit_parallax (a test aid; 2: the
// cabin drawn from the eye but shaded with the game's own eye position).
void SetCockpitParallaxTest(int on);

// Camera hooks: the factor for the size cull of the views drawn from the camera (hooks/
// vr_culling.cpp) while posed VR cockpit views are drawn: tan^2 of the in-car camera's own
// vertical half-angle over the cockpit's (below 1), else 1.
float CullScale();

// Recentres the cockpit view and the screen on where the head is now.
void Recenter();
// Player 1's pad buttons, every poll: both sticks pressed for a second recentre.
void OnPad(uint16_t buttons);

}  // namespace forza::xr
