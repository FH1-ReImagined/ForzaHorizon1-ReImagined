// forza - VR through OpenXR; see xr.h and docs/vr.md (#333).
//
// Threads: Prepare and the Vulkan creation hooks run where the SDK sets up graphics; the
// session, its frame loop and the copies into the headset's swapchain run on the "Forza OpenXR"
// thread; the camera hooks call CockpitView/RememberPosedView on the main loop thread and
// OnRenderView/TagSwap on the render thread. The OpenXR thread shares the device's one graphics
// queue with the GPU plugin and the presenter: it takes the device's queue lock around every
// call the XR_KHR_vulkan_enable2 spec lists as using it (xrBeginFrame, xrEndFrame,
// xrAcquireSwapchainImage, xrReleaseSwapchainImage) and around its own submissions.

#include "xr.h"

#include "driving_gate.h"
#include "guest_memory.h"
#include "sdk_import.h"
#include "vr_head_pose_math.h"
#include "xr_frame_tags.h"
#include "xr_math.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/system/interfaces/graphics.h>
#include <rex/ui/keybinds.h>
#include <rex/ui/vulkan/provider.h>

#ifdef _WIN32
#include <windows.h>
#define XR_USE_PLATFORM_WIN32
#else
#include <dlfcn.h>
#include <pthread.h>
#include <time.h>
#define XR_USE_TIMESPEC
#endif
#define XR_NO_PROTOTYPES
#define XR_USE_GRAPHICS_API_VULKAN
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <typeinfo>
#include <vector>

REXCVAR_DEFINE_STRING(vr_mode, "off", "VR",
                      "VR headset through OpenXR: off, screen (the game on a virtual screen) or "
                      "cockpit (head-tracked in-car view, mono); needs an OpenXR runtime")
    .allowed({"off", "screen", "cockpit"})
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_DOUBLE(vr_seat_height_cm, -4.0, "VR",
                      "Cockpit mode: raises (positive) or lowers the eye from the driver's seat, cm "
                      "(-4: the game's driver's eye sits a few cm under the roof, and a head turned "
                      "toward a door or the rear window would leave the cabin through it)")
    .range(-50, 50);
REXCVAR_DEFINE_STRING(vr_recenter_key, "Home", "VR",
                      "Key that recentres the VR view on the head (both sticks held for a second "
                      "do the same)")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
// The eye's room around where it was recentred (xr_math.h HeadBox): an ellipse on the floor
// beside and behind it, eased toward its edge, and a dome over it; forward and down are free.
REXCVAR_DEFINE_DOUBLE(vr_head_back_limit_cm, 15.0, "VR",
                      "Cockpit: how far the eye may move back from where it was recentred, cm "
                      "(turning round moves the head back; 100: no limit)")
    .range(0, 100);
REXCVAR_DEFINE_DOUBLE(vr_head_side_limit_cm, 18.0, "VR",
                      "Cockpit: how far the eye may move sideways, cm (turning the head toward a "
                      "door moves it 8-12 cm aside; 100: no limit)")
    .range(0, 100);
REXCVAR_DEFINE_DOUBLE(vr_head_up_limit_cm, 6.0, "VR",
                      "Cockpit: how far the eye may move up while near where it was recentred, cm; "
                      "less the further it is aside or back, where the roof slopes down (100: no "
                      "limit)")
    .range(0, 100);
// Version 8: the fallback of a cockpit that only turns (3 degrees of freedom).
REXCVAR_DEFINE_BOOL(vr_head_position, true, "VR",
                    "Cockpit: the eye follows the head's position (leaning moves it inside the car); "
                    "off: it only turns, staying at the driver's seat (the whole view moves with a lean)");
REXCVAR_DEFINE_DOUBLE(vr_fov_margin, 5.0, "VR",
                      "Cockpit mode: degrees drawn beyond the headset's field of view on each side, "
                      "so reprojection between game frames does not show the image's edge")
    .range(0, 30)
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_INT32(vr_runtime_timeout, 20, "VR",
                     "Seconds to wait for the OpenXR runtime at start before playing flat")
    .range(1, 300)
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
// 2.2 m at 2.5 m: 47 degrees wide, 27 tall. The corners are 24 degrees from the middle, within
// an eye turn without turning the head, like a cinema screen from its recommended seats (THX:
// 36-40 degrees). Version 1's 3.2 m filled 65 degrees: menus and the HUD's corners needed head
// turns.
REXCVAR_DEFINE_DOUBLE(vr_screen_width, 2.2, "VR", "Virtual screen width, metres").range(0.5, 20);
REXCVAR_DEFINE_DOUBLE(vr_screen_distance, 2.5, "VR", "Virtual screen distance, metres")
    .range(0.5, 20);
// The HUD's own panel in the cockpit (needs ReIGlue 0393): 0.9 m wide 1 m ahead, its middle 14
// degrees below the eyes: 48 x 27 degrees, the HUD's 1280 pixels at 27 per degree; its top edge
// sits just under the horizon, the speedometer and map 24 degrees down in its corners.
REXCVAR_DEFINE_BOOL(vr_hud, true, "VR",
                    "Cockpit: the HUD on its own panel in front of the seat, below the eyes, instead "
                    "of in the corners of the head-tracked view")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_DOUBLE(vr_hud_width, 0.9, "VR", "Cockpit: the HUD panel's width, metres").range(0.2, 3);
REXCVAR_DEFINE_DOUBLE(vr_hud_distance, 1.0, "VR", "Cockpit: the HUD panel's distance, metres")
    .range(0.3, 5);
REXCVAR_DEFINE_DOUBLE(vr_hud_pitch, -14.0, "VR",
                      "Cockpit: the HUD panel's direction from the eyes, degrees (negative: below)")
    .range(-60, 30);
// Stereo. true (ReIGlue 0396, the default since version 5): the game's main view is drawn
// for each eye from the same frame, each from its own eye point (the frame's commands executed
// twice in the GPU emulation): real depth. depth (ReIGlue 0394, version 3): each eye made from the
// one picture and its depth pre-pass, shifted by the disparity of each pixel's distance, the
// holes beside near objects filled from the background ("full of artifacts" in the third
// headset test). off: both eyes see the same picture (version 2).
REXCVAR_DEFINE_STRING(vr_stereo, "true", "VR",
                      "Cockpit: true (each eye drawn from its own point: real depth, about half "
                      "again the GPU time), depth (each eye made from the picture and its depth: "
                      "approximate, filled edges beside near objects) or off (both eyes see the "
                      "same picture: no depth)")
    .allowed({"off", "depth", "true"})
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_BOOL(vr_stereo_eye_parts, true, "VR",
                    "True stereo: draw each eye's scene only across the part of the picture that eye "
                    "sees (about 70 %; less GPU time)")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_DOUBLE(vr_stereo_max_shift, 3.0, "VR",
                      "Depth stereo: the most a pixel moves for an eye, percent of the picture's "
                      "width (nearer than that is drawn at that depth)")
    .range(0.5, 10)
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
// ReIGlue 0395: contrast-adaptive sharpening of the cockpit's picture before the headset gets it
// (the desktop window's FSR/CAS present effect works on the window's picture only).
REXCVAR_DEFINE_DOUBLE(vr_sharpen, 0.5, "VR",
                      "Cockpit: sharpening of the head-tracked picture, 0 (off) to 1 (strongest)")
    .range(0, 1)
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
// Version 6: the headset at a refresh rate that shows every game frame equally long. WiVRn picks
// the highest rate under 100 Hz (90 on a Quest 3) unless set in its headset app; 72 fps at 90 Hz
// shows 4 frames of 5 for one refresh and the fifth for two, so the world outside judders, and the
// runtime composites and streams 90 pictures a second for 72.
REXCVAR_DEFINE_INT32(vr_refresh_rate, 0, "VR",
                     "The headset's refresh rate in Hz: 0 matches it to the game's frame rate (72 "
                     "fps: 72 Hz), -1 leaves it to the runtime and the headset's settings")
    .range(-1, 240)
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DECLARE(int32_t, frame_rate);  // src/main.cpp
// Version 6: the headset's frame waits briefly for a game frame that is about to be finished. The
// OpenXR thread takes the newest finished game frame at each headset frame; with the game at the
// headset's rate the two meet at a moment WiVRn's frame timing settles on, and on the real headset
// that was right where game frames finish: one just in time, the next a fraction of a millisecond
// late, so that frame was shown a headset frame later, its predecessor twice, and two frames were
// then ready at once (one dropped): 4 to 12 frames a second shown twice or not at all, and the
// game's part of the latency jumping between about 42 and 55 ms. Moving the game's frame timer did
// not help (tried: the frames still finished at the moment they were taken). Now a headset frame
// that finds no new game frame while one is due (one headset period after the last) waits up to
// this long for it.
REXCVAR_DEFINE_DOUBLE(vr_frame_wait, 4.0, "VR",
                      "Milliseconds the headset's frame may wait for a game frame that is about to be "
                      "finished, rather than show the last one again (0: never)")
    .range(0, 8)
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

REXCVAR_DEFINE_BOOL(vr_trace, false, "VR",
                    "Log the head pose, the game camera, the field of view drawn and the latency "
                    "about 10 times a second (lines \"VR trace:\")")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

namespace forza::xr {
namespace {

using rex::ui::vulkan::VulkanDevice;

// ReIGlue 0390 (src/ui/vulkan/vulkan_creation_hooks.h in the SDK) and 0391
// (src/ui/vulkan/vulkan_presenter.cpp): no public header, the same layouts here.
extern "C" {
struct RexVulkanCreationHooks {
  uint32_t size;
  void* user;
  VkResult (*create_instance)(void* user, PFN_vkGetInstanceProcAddr get_instance_proc_addr,
                              const VkInstanceCreateInfo* create_info, VkInstance* instance);
  VkPhysicalDevice (*physical_device)(void* user, VkInstance instance);
  VkResult (*create_device)(void* user, PFN_vkGetInstanceProcAddr get_instance_proc_addr,
                            VkPhysicalDevice physical_device, const VkDeviceCreateInfo* create_info,
                            VkDevice* device);
};
struct RexGuestOutputFrame {
  uint32_t size;
  uint32_t handle;
  uint64_t serial;
  uint32_t tag;
  uint32_t width;
  uint32_t height;
  VkImage image;
  VkImageView view;
  // ReIGlue 0393: the frame's VR layers, all A2B10G10R10 in SHADER_READ_ONLY_OPTIMAL like `image`
  // except the HUD (R8G8B8A8_UNORM, unpremultiplied colour and its coverage); held with the frame.
  struct Layers {
    uint32_t layers;  // kLayerScene, kLayerHud, kLayerStereo
    uint32_t scene_width, scene_height;
    VkImage scene;  // the scene before the HUD, the guest output's gamma and format
    uint32_t hud_width, hud_height;
    VkImage hud;  // the HUD alone at 1280 x 720
    uint32_t stereo_width, stereo_height;
    VkImage stereo;  // both eyes side by side (ReIGlue 0394)
  } vr;
  int64_t ready_ns;  // ReIGlue 0397: when the frame was published, steady clock (0: not told)
};
}
constexpr uint32_t kExportFrames = 1, kExportLayers = 2, kExportStereo = 4;  // rexui_SetGuestOutputExport
constexpr uint32_t kExportTrueStereo = 8;  // ReIGlue 0396
constexpr uint32_t kLayerScene = 1, kLayerHud = 2, kLayerStereo = 4;
using SetVulkanCreationHooksFn = void (*)(const RexVulkanCreationHooks*);
using SetPadsWithoutFocusFn = void (*)(int);  // ReIGlue 0392 (src/input/input_system.cpp)
using SetGuestOutputStereoFn = void (*)(float, float);  // ReIGlue 0393 (vulkan_presenter.cpp)
using SetGuestOutputTrueStereoFn = void (*)(float, float);  // ReIGlue 0396: eye distance, P00
using SetGuestOutputTrueStereoEyesFn = void (*)(const float*);  // ReIGlue 0396: each eye's part
using SetGuestOutputExportFn = void (*)(int);
using AcquireGuestOutputFrameFn = int (*)(RexGuestOutputFrame*, uint64_t);
using ReleaseGuestOutputFrameFn = void (*)(uint32_t);

#ifdef _WIN32
constexpr const char* kLoaderName = "openxr_loader.dll";
#else
constexpr const char* kLoaderName = "libopenxr_loader.so.1";
#endif

enum class Mode { kOff, kScreen, kCockpit };

struct Api {
  PFN_xrGetInstanceProcAddr GetInstanceProcAddr = nullptr;
  PFN_xrEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties = nullptr;
  PFN_xrCreateInstance CreateInstance = nullptr;
  PFN_xrDestroyInstance DestroyInstance = nullptr;
  PFN_xrResultToString ResultToString = nullptr;
  PFN_xrGetInstanceProperties GetInstanceProperties = nullptr;
  PFN_xrGetSystem GetSystem = nullptr;
  PFN_xrGetSystemProperties GetSystemProperties = nullptr;
  PFN_xrPollEvent PollEvent = nullptr;
  PFN_xrCreateSession CreateSession = nullptr;
  PFN_xrDestroySession DestroySession = nullptr;
  PFN_xrBeginSession BeginSession = nullptr;
  PFN_xrEndSession EndSession = nullptr;
  PFN_xrRequestExitSession RequestExitSession = nullptr;
  PFN_xrCreateReferenceSpace CreateReferenceSpace = nullptr;
  PFN_xrDestroySpace DestroySpace = nullptr;
  PFN_xrLocateSpace LocateSpace = nullptr;
  PFN_xrLocateViews LocateViews = nullptr;
  PFN_xrEnumerateViewConfigurationViews EnumerateViewConfigurationViews = nullptr;
  PFN_xrEnumerateEnvironmentBlendModes EnumerateEnvironmentBlendModes = nullptr;
  PFN_xrEnumerateSwapchainFormats EnumerateSwapchainFormats = nullptr;
  PFN_xrCreateSwapchain CreateSwapchain = nullptr;
  PFN_xrDestroySwapchain DestroySwapchain = nullptr;
  PFN_xrEnumerateSwapchainImages EnumerateSwapchainImages = nullptr;
  PFN_xrAcquireSwapchainImage AcquireSwapchainImage = nullptr;
  PFN_xrWaitSwapchainImage WaitSwapchainImage = nullptr;
  PFN_xrReleaseSwapchainImage ReleaseSwapchainImage = nullptr;
  PFN_xrWaitFrame WaitFrame = nullptr;
  PFN_xrBeginFrame BeginFrame = nullptr;
  PFN_xrEndFrame EndFrame = nullptr;
  PFN_xrGetVulkanGraphicsRequirements2KHR GetVulkanGraphicsRequirements2KHR = nullptr;
  PFN_xrCreateVulkanInstanceKHR CreateVulkanInstanceKHR = nullptr;
  PFN_xrCreateVulkanDeviceKHR CreateVulkanDeviceKHR = nullptr;
  PFN_xrGetVulkanGraphicsDevice2KHR GetVulkanGraphicsDevice2KHR = nullptr;
  // XR_FB_display_refresh_rate (optional; WiVRn, Meta's runtime): the headset's refresh rate.
  PFN_xrEnumerateDisplayRefreshRatesFB EnumerateDisplayRefreshRatesFB = nullptr;
  PFN_xrGetDisplayRefreshRateFB GetDisplayRefreshRateFB = nullptr;
  PFN_xrRequestDisplayRefreshRateFB RequestDisplayRefreshRateFB = nullptr;
#ifdef _WIN32
  PFN_xrConvertWin32PerformanceCounterToTimeKHR ConvertTime = nullptr;
#else
  PFN_xrConvertTimespecTimeToTimeKHR ConvertTime = nullptr;
#endif
};

// Device functions the SDK's table may not have (vkCmdBlitImage, vkCmdCopyImage), all loaded here.
struct Vk {
  PFN_vkCreateCommandPool CreateCommandPool = nullptr;
  PFN_vkDestroyCommandPool DestroyCommandPool = nullptr;
  PFN_vkResetCommandPool ResetCommandPool = nullptr;
  PFN_vkAllocateCommandBuffers AllocateCommandBuffers = nullptr;
  PFN_vkBeginCommandBuffer BeginCommandBuffer = nullptr;
  PFN_vkEndCommandBuffer EndCommandBuffer = nullptr;
  PFN_vkCmdPipelineBarrier CmdPipelineBarrier = nullptr;
  PFN_vkCmdBlitImage CmdBlitImage = nullptr;
  PFN_vkCmdCopyImage CmdCopyImage = nullptr;
  PFN_vkQueueSubmit QueueSubmit = nullptr;
  PFN_vkCreateFence CreateFence = nullptr;
  PFN_vkDestroyFence DestroyFence = nullptr;
  PFN_vkWaitForFences WaitForFences = nullptr;
  PFN_vkResetFences ResetFences = nullptr;
  PFN_vkGetFenceStatus GetFenceStatus = nullptr;
  PFN_vkCreateImage CreateImage = nullptr;
  PFN_vkDestroyImage DestroyImage = nullptr;
  PFN_vkGetImageMemoryRequirements GetImageMemoryRequirements = nullptr;
  PFN_vkAllocateMemory AllocateMemory = nullptr;
  PFN_vkFreeMemory FreeMemory = nullptr;
  PFN_vkBindImageMemory BindImageMemory = nullptr;
  PFN_vkGetPhysicalDeviceMemoryProperties GetPhysicalDeviceMemoryProperties = nullptr;
  PFN_vkGetPhysicalDeviceFormatProperties GetPhysicalDeviceFormatProperties = nullptr;
};

// One copy of a guest output into the headset's swapchain, in flight until its fence signals.
struct CopySlot {
  VkCommandPool pool = VK_NULL_HANDLE;
  VkCommandBuffer buffer = VK_NULL_HANDLE;
  VkFence fence = VK_NULL_HANDLE;
  uint32_t hold = 0;  // the guest output frame held until the copy is done
  bool busy = false;
};

// A swapchain the OpenXR thread copies game pictures into, with its staging image.
struct Target {
  XrSwapchain swapchain = XR_NULL_HANDLE;
  std::vector<VkImage> images;
  uint32_t width = 0, height = 0;
  uint32_t failed_width = 0, failed_height = 0;
  VkImage staging = VK_NULL_HANDLE;
  VkDeviceMemory staging_memory = VK_NULL_HANDLE;
  bool have_image = false;  // a copy was released into the swapchain
  XrExtent2Di shown_extent{};  // the picture's part of the swapchain image (smaller for the screen)
  bool image_acquired = false;  // a timed-out wait must resume this same acquisition
  uint32_t acquired_index = 0;
};

struct State {
  Mode mode = Mode::kOff;
#ifdef _WIN32
  HMODULE loader = nullptr;
#else
  void* loader = nullptr;
#endif
  Api xr;
  XrInstance instance = XR_NULL_HANDLE;
  XrSystemId system = XR_NULL_SYSTEM_ID;
  std::string runtime_name;
  bool prepared = false;
  bool started = false;

  // Vulkan created through the runtime (the creation hooks).
  VkInstance vk_instance = VK_NULL_HANDLE;
  VkPhysicalDevice vk_physical = VK_NULL_HANDLE;
  VkDevice vk_device = VK_NULL_HANDLE;
  bool vk_failed = false;
  PFN_vkGetInstanceProcAddr vk_get_instance_proc_addr = nullptr;

  SetGuestOutputExportFn set_export = nullptr;
  AcquireGuestOutputFrameFn acquire_frame = nullptr;
  ReleaseGuestOutputFrameFn release_frame = nullptr;
  SetPadsWithoutFocusFn set_pads_without_focus = nullptr;
  SetGuestOutputStereoFn set_stereo = nullptr;
  bool stereo = false;  // vr_stereo depth or true was asked for and the SDK can do it
  // ReIGlue 0396: true stereo; the export flags without it and whether they carry it now (only
  // while the cockpit is shown: menus and the screen get one picture).
  SetGuestOutputTrueStereoFn set_true_stereo = nullptr;
  SetGuestOutputTrueStereoEyesFn set_true_stereo_eyes = nullptr;
  float true_stereo_eye_x[4] = {0, 1, 0, 1};  // each eye's part of the picture handed to the SDK
  bool true_stereo = false;
  uint32_t export_flags = 0;
  bool export_true_stereo = false;
  int stereo_logged = -1;  // the cockpit's last logged picture: 1 each eye its own, 0 one
  float true_stereo_p00 = 0;  // the cockpit frustum's P00 handed to the SDK
  float ipd = 0.064f;  // the eyes' distance (xrLocateViews), handed to the SDK for the eyes
  bool picture_stereo = false;  // the picture swapchain holds both eyes side by side
  float picture_ipd = 0.064f;  // the eye distance those eyes were made with

  // The session (OpenXR thread; the spaces are also read by CockpitView under pose_mutex).
  VulkanDevice* device = nullptr;
  Vk vk;
  std::thread thread;
  std::atomic<bool> stop{false};
  std::atomic<bool> finished{false};  // the thread has left Run
  XrSession session = XR_NULL_HANDLE;
  XrSpace local = XR_NULL_HANDLE;
  XrSpace view = XR_NULL_HANDLE;
  XrSessionState state = XR_SESSION_STATE_UNKNOWN;
  std::atomic<bool> running{false};  // between xrBeginSession and xrEndSession; read by input polling
  int frame_failures = 0;  // xrWaitFrame/xrBeginFrame/xrEndFrame failures in a row
  XrEnvironmentBlendMode blend = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
  int64_t swapchain_format = 0;
  VkFormat staging_format = VK_FORMAT_UNDEFINED;  // the 8-bit UNORM twin of the sRGB format

  // The swapchain for the game's picture, sized as the guest output.
  Target picture;  // the game's picture: the screen, or the cockpit (the scene without the HUD)
  Target hud;  // the HUD alone (ReIGlue 0393), on its panel in the cockpit
  bool hud_shown = false;  // the last cockpit picture came with its HUD in `hud`
  bool layers_logged = false;
  CopySlot slots[3];
  uint32_t next_slot = 0;
  // Version 6: an empty submission after each xrEndFrame, so the next one can wait for the
  // runtime's previous frame without the queue lock (EndFrame).
  VkFence end_fence = VK_NULL_HANDLE;
  bool end_fence_pending = false;
  bool locked_end_frame = false;  // FORZA_VR_LOCKED_END_FRAME=1: version 5's way (A/B tests)
  uint64_t last_serial = 0;
  FrameTag shown_tag;  // the tag of the picture in the swapchain (id 0: untagged)
  bool linear_blit = false;  // the guest output's format can be filtered: the screen is downscaled

  // Cockpit state shared with the camera hooks.
  std::atomic<bool> cockpit_active{false};
  bool menu_up = false;  // the game is paused or in a menu (OpenXR thread): the screen shows it
  std::atomic<bool> recenter{true};
  std::mutex pose_mutex;  // the spaces while CockpitView locates, the origin, the frustum
  Pose origin;
  bool origin_set = false;
  CockpitFrustum frustum;  // what the game draws the cockpit with (vertical 0: not known yet)
  std::atomic<int64_t> wait_display_time{0}, wait_period{0}, wait_steady_ns{0};
  // Camera built to picture shown: the head pose is predicted that far ahead. A streamed headset
  // (WiVRn) shows a picture 70-95 ms after the game built its camera, so the cap is 100 ms.
  LatencyEstimate latency{40'000'000, 5'000'000, 100'000'000};  // OpenXR thread only
  std::atomic<int64_t> latency_ns{40'000'000};
  // The last cockpit frame's latency in two parts: camera built to the picture reaching the OpenXR
  // thread (the game's own pipeline), and from there to its display time (the runtime's).
  std::atomic<int64_t> last_game_ns{0}, last_runtime_ns{0};
  int64_t stat_game_ns = 0, stat_runtime_ns = 0;  // sums over the stats window
  uint32_t stat_latency_samples = 0;

  // vr_trace: what the camera hook saw last (main loop thread), logged by the OpenXR thread.
  std::atomic<bool> trace{false};
  std::atomic<uint32_t> trace_camera{0};
  std::atomic<const char*> trace_camera_name{nullptr};
  std::atomic<float> trace_look{0};
  std::atomic<bool> trace_posed{false};
  std::atomic<int64_t> trace_view_ns{0};  // steady clock of the last view built
  int64_t trace_next_ns = 0;

  std::mutex tags_mutex;
  TagRing<64> tags;
  ViewMatcher<8> views;
  SwapTagger swap_tagger;
  std::atomic<uint32_t> next_tag{1};

  // Frame timing (vr_frame_wait; ReIGlue 0397 tells when each game frame was published).
  int64_t frame_wait_ns = 0;  // the longest a headset frame waits for a game frame due; 0: never
  int64_t stat_slack_min = INT64_MAX, stat_slack_sum = 0, stat_waited_ns = 0;
  uint32_t stat_slack_n = 0, stat_skips = 0, stat_waits = 0, stat_waits_found = 0;
  // How evenly frames are taken (each headset frame) and published (each game frame): the spread
  // of the intervals, for the 10 s line.
  int64_t last_pickup_ns = 0, last_ready_ns = 0;
  double stat_pickup_sum = 0, stat_pickup_sq = 0, stat_ready_sum = 0, stat_ready_sq = 0;
  uint32_t stat_pickup_n = 0, stat_ready_n = 0;

  // Statistics for the periodic log line.
  std::atomic<uint32_t> stat_swaps{0}, stat_tagged_swaps{0}, stat_extra_views{0}, stat_repeat_views{0};
  uint32_t stat_frames = 0, stat_projection = 0, stat_quad = 0, stat_hud = 0, stat_copies = 0;
  int64_t stat_end_frame_max_ns = 0, stat_end_frame_ns = 0, stat_wait_frame_ns = 0, stat_end_wait_ns = 0;
  std::chrono::steady_clock::time_point stat_since;
};

State g;

int64_t SteadyNs() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

std::string ResultName(XrResult r) {
  if (g.xr.ResultToString && g.instance != XR_NULL_HANDLE) {
    char text[XR_MAX_RESULT_STRING_SIZE] = {};
    if (XR_SUCCEEDED(g.xr.ResultToString(g.instance, r, text))) return text;
  }
  switch (r) {
    case XR_ERROR_RUNTIME_UNAVAILABLE:
      return "XR_ERROR_RUNTIME_UNAVAILABLE";
    case XR_ERROR_RUNTIME_FAILURE:
      return "XR_ERROR_RUNTIME_FAILURE";
    case XR_ERROR_FORM_FACTOR_UNAVAILABLE:
      return "XR_ERROR_FORM_FACTOR_UNAVAILABLE";
    case XR_ERROR_EXTENSION_NOT_PRESENT:
      return "XR_ERROR_EXTENSION_NOT_PRESENT";
    case XR_ERROR_API_VERSION_UNSUPPORTED:
      return "XR_ERROR_API_VERSION_UNSUPPORTED";
    case XR_ERROR_INSTANCE_LOST:
      return "XR_ERROR_INSTANCE_LOST";
    default:
      return "XrResult " + std::to_string(int(r));
  }
}

void* LoaderSymbol(const char* name) {
#ifdef _WIN32
  return g.loader ? reinterpret_cast<void*>(GetProcAddress(g.loader, name)) : nullptr;
#else
  return g.loader ? dlsym(g.loader, name) : nullptr;
#endif
}

bool LoadLoader() {
#ifdef _WIN32
  g.loader = LoadLibraryA(kLoaderName);
#else
  g.loader = dlopen(kLoaderName, RTLD_NOW | RTLD_LOCAL);
#endif
  if (!g.loader) return false;
  g.xr.GetInstanceProcAddr =
      reinterpret_cast<PFN_xrGetInstanceProcAddr>(LoaderSymbol("xrGetInstanceProcAddr"));
  return g.xr.GetInstanceProcAddr != nullptr;
}

template <typename T>
bool Load(XrInstance instance, const char* name, T& out) {
  PFN_xrVoidFunction f = nullptr;
  if (XR_FAILED(g.xr.GetInstanceProcAddr(instance, name, &f)) || !f) return false;
  out = reinterpret_cast<T>(f);
  return true;
}

// Logs why VR is off and ends the instance Prepare made (only before the Vulkan hooks are
// registered). The loader stays loaded: a runtime may leave threads in its library.
void FlatBeforeHooks(const std::string& why) {
  REXLOG_WARN("VR: {} - playing flat", why);
  if (g.instance != XR_NULL_HANDLE && g.xr.DestroyInstance) g.xr.DestroyInstance(g.instance);
  g.instance = XR_NULL_HANDLE;
  g.mode = Mode::kOff;
}

// --- Vulkan creation through the runtime (ReIGlue 0390) ------------------------------------

VkResult HookCreateInstance(void*, PFN_vkGetInstanceProcAddr gipa, const VkInstanceCreateInfo* info,
                            VkInstance* out) {
  g.vk_get_instance_proc_addr = gipa;
  if (!g.vk_failed) {
    XrVulkanInstanceCreateInfoKHR create{XR_TYPE_VULKAN_INSTANCE_CREATE_INFO_KHR};
    create.systemId = g.system;
    create.pfnGetInstanceProcAddr = gipa;
    create.vulkanCreateInfo = info;
    VkResult vk = VK_SUCCESS;
    const XrResult r = g.xr.CreateVulkanInstanceKHR(g.instance, &create, out, &vk);
    if (XR_SUCCEEDED(r)) {
      if (vk == VK_SUCCESS) g.vk_instance = *out;
      return vk;  // a Vulkan error (a missing layer): the SDK retries through here
    }
    REXLOG_WARN("VR: xrCreateVulkanInstanceKHR failed ({}) - playing flat", ResultName(r));
    g.vk_failed = true;
  }
  auto create = reinterpret_cast<PFN_vkCreateInstance>(gipa(nullptr, "vkCreateInstance"));
  const VkResult vk = create ? create(info, nullptr, out) : VK_ERROR_INITIALIZATION_FAILED;
  if (vk == VK_SUCCESS) g.vk_instance = *out;
  return vk;
}

VkPhysicalDevice HookPhysicalDevice(void*, VkInstance instance) {
  if (g.vk_failed || instance != g.vk_instance) return VK_NULL_HANDLE;
  XrVulkanGraphicsDeviceGetInfoKHR info{XR_TYPE_VULKAN_GRAPHICS_DEVICE_GET_INFO_KHR};
  info.systemId = g.system;
  info.vulkanInstance = instance;
  VkPhysicalDevice physical = VK_NULL_HANDLE;
  const XrResult r = g.xr.GetVulkanGraphicsDevice2KHR(g.instance, &info, &physical);
  if (XR_FAILED(r) || physical == VK_NULL_HANDLE) {
    REXLOG_WARN("VR: xrGetVulkanGraphicsDevice2KHR failed ({}) - playing flat", ResultName(r));
    g.vk_failed = true;
    return VK_NULL_HANDLE;
  }
  g.vk_physical = physical;
  return physical;
}

VkResult HookCreateDevice(void*, PFN_vkGetInstanceProcAddr gipa, VkPhysicalDevice physical,
                          const VkDeviceCreateInfo* info, VkDevice* out) {
  if (!g.vk_failed && physical == g.vk_physical && physical != VK_NULL_HANDLE) {
    XrVulkanDeviceCreateInfoKHR create{XR_TYPE_VULKAN_DEVICE_CREATE_INFO_KHR};
    create.systemId = g.system;
    create.pfnGetInstanceProcAddr = gipa;
    create.vulkanPhysicalDevice = physical;
    create.vulkanCreateInfo = info;
    VkResult vk = VK_SUCCESS;
    const XrResult r = g.xr.CreateVulkanDeviceKHR(g.instance, &create, out, &vk);
    if (XR_SUCCEEDED(r) && vk == VK_SUCCESS) {
      g.vk_device = *out;
      return VK_SUCCESS;
    }
    REXLOG_WARN("VR: xrCreateVulkanDeviceKHR failed ({}, VkResult {}) - playing flat",
                ResultName(r), int(vk));
    g.vk_failed = true;
    if (XR_SUCCEEDED(r)) return vk;
  }
  auto create =
      reinterpret_cast<PFN_vkCreateDevice>(gipa(g.vk_instance, "vkCreateDevice"));
  return create ? create(physical, info, nullptr, out) : VK_ERROR_INITIALIZATION_FAILED;
}

// --- The session ------------------------------------------------------------------------------

XrTime NowXrTime() {
  if (g.xr.ConvertTime) {
    XrTime t = 0;
#ifdef _WIN32
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    if (XR_SUCCEEDED(g.xr.ConvertTime(g.instance, &counter, &t))) return t;
#else
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    if (XR_SUCCEEDED(g.xr.ConvertTime(g.instance, &ts, &t))) return t;
#endif
  }
  // Without the conversion extension: the last xrWaitFrame's display time is about two frames
  // after it returned.
  return g.wait_display_time.load() - 2 * g.wait_period.load() + (SteadyNs() - g.wait_steady_ns.load());
}

Pose ToPose(const XrPosef& p) {
  return {{p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w},
          {p.position.x, p.position.y, p.position.z}};
}
XrPosef ToXr(const Pose& p) {
  XrPosef out;
  out.orientation = {p.q.x, p.q.y, p.q.z, p.q.w};
  out.position = {p.p.x, p.p.y, p.p.z};
  return out;
}

// The head (VIEW space) in LOCAL at `time`; false without a tracked orientation.
bool LocateHead(XrTime time, Pose& out) {
  XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
  if (XR_FAILED(g.xr.LocateSpace(g.view, g.local, time, &location))) return false;
  if (!(location.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)) return false;
  out = ToPose(location.pose);
  if (!(location.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT)) out.p = {};
  return true;
}

bool LoadDeviceFunctions() {
  const auto& ifn = g.device->vulkan_instance()->functions();
  const VkDevice device = g.device->device();
  const VkInstance instance = g.device->vulkan_instance()->instance();
  bool ok = true;
#define FORZA_VK_DEVICE(name)                                                         \
  ok &= (g.vk.name = reinterpret_cast<PFN_vk##name>(ifn.vkGetDeviceProcAddr(device,   \
                                                                             "vk" #name))) != nullptr;
#define FORZA_VK_INSTANCE(name)                                                        \
  ok &= (g.vk.name = reinterpret_cast<PFN_vk##name>(ifn.vkGetInstanceProcAddr(instance, \
                                                                               "vk" #name))) != nullptr;
  FORZA_VK_DEVICE(CreateCommandPool)
  FORZA_VK_DEVICE(DestroyCommandPool)
  FORZA_VK_DEVICE(ResetCommandPool)
  FORZA_VK_DEVICE(AllocateCommandBuffers)
  FORZA_VK_DEVICE(BeginCommandBuffer)
  FORZA_VK_DEVICE(EndCommandBuffer)
  FORZA_VK_DEVICE(CmdPipelineBarrier)
  FORZA_VK_DEVICE(CmdBlitImage)
  FORZA_VK_DEVICE(CmdCopyImage)
  FORZA_VK_DEVICE(QueueSubmit)
  FORZA_VK_DEVICE(CreateFence)
  FORZA_VK_DEVICE(DestroyFence)
  FORZA_VK_DEVICE(WaitForFences)
  FORZA_VK_DEVICE(ResetFences)
  FORZA_VK_DEVICE(GetFenceStatus)
  FORZA_VK_DEVICE(CreateImage)
  FORZA_VK_DEVICE(DestroyImage)
  FORZA_VK_DEVICE(GetImageMemoryRequirements)
  FORZA_VK_DEVICE(AllocateMemory)
  FORZA_VK_DEVICE(FreeMemory)
  FORZA_VK_DEVICE(BindImageMemory)
  FORZA_VK_INSTANCE(GetPhysicalDeviceMemoryProperties)
  FORZA_VK_INSTANCE(GetPhysicalDeviceFormatProperties)
#undef FORZA_VK_DEVICE
#undef FORZA_VK_INSTANCE
  return ok;
}

bool CreateCopySlots() {
  const VkDevice device = g.device->device();
  for (CopySlot& slot : g.slots) {
    VkCommandPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pool_info.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
    pool_info.queueFamilyIndex = g.device->queue_family_graphics_compute();
    if (g.vk.CreateCommandPool(device, &pool_info, nullptr, &slot.pool) != VK_SUCCESS) return false;
    VkCommandBufferAllocateInfo alloc{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    alloc.commandPool = slot.pool;
    alloc.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    alloc.commandBufferCount = 1;
    if (g.vk.AllocateCommandBuffers(device, &alloc, &slot.buffer) != VK_SUCCESS) return false;
    VkFenceCreateInfo fence_info{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    if (g.vk.CreateFence(device, &fence_info, nullptr, &slot.fence) != VK_SUCCESS) return false;
  }
  VkFenceCreateInfo fence_info{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  if (g.vk.CreateFence(device, &fence_info, nullptr, &g.end_fence) != VK_SUCCESS) g.end_fence = VK_NULL_HANDLE;
  const char* locked = std::getenv("FORZA_VR_LOCKED_END_FRAME");
  g.locked_end_frame = locked && *locked == '1';
  return true;
}

// Waits (up to `timeout_ns`) for the empty submission made after the last xrEndFrame; true once
// it is done (or there is none).
bool FinishEndFence(uint64_t timeout_ns) {
  if (!g.end_fence_pending) return true;
  const VkDevice device = g.device->device();
  const VkResult waited = g.vk.WaitForFences(device, 1, &g.end_fence, VK_TRUE, timeout_ns);
  if (waited != VK_SUCCESS && waited != VK_ERROR_DEVICE_LOST) return false;
  if (waited == VK_ERROR_DEVICE_LOST || g.vk.ResetFences(device, 1, &g.end_fence) != VK_SUCCESS) g.stop = true;
  g.end_fence_pending = false;
  return true;
}

// Waits for a slot's copy (up to `timeout_ns`) and drops its hold on the guest output.
bool FinishSlot(CopySlot& slot, uint64_t timeout_ns) {
  if (!slot.busy) return true;
  const VkDevice device = g.device->device();
  const VkResult waited = g.vk.WaitForFences(device, 1, &slot.fence, VK_TRUE, timeout_ns);
  if (waited != VK_SUCCESS && waited != VK_ERROR_DEVICE_LOST) return false;
  // Vulkan defines device loss as completion for resource lifetime purposes.
  // It permits cleanup, but never another copy on this device.
  if (waited == VK_ERROR_DEVICE_LOST) g.stop = true;
  else if (g.vk.ResetFences(device, 1, &slot.fence) != VK_SUCCESS) g.stop = true;
  if (slot.hold) g.release_frame(slot.hold);
  slot.hold = 0;
  slot.busy = false;
  return true;
}
bool FinishAllSlots() {
  bool finished = true;
  for (CopySlot& slot : g.slots) finished &= FinishSlot(slot, 100'000'000);
  return finished;
}
// Copies whose fence has signalled.
void ReapSlots() {
  for (CopySlot& slot : g.slots) {
    if (slot.busy && g.vk.GetFenceStatus(g.device->device(), slot.fence) == VK_SUCCESS) {
      FinishSlot(slot, 0);
    }
  }
}

// Destroys a target's swapchain and staging image; only once no copy into them is in flight.
void DestroyTarget(Target& t) {
  const VkDevice device = g.device->device();
  if (t.swapchain != XR_NULL_HANDLE) g.xr.DestroySwapchain(t.swapchain);
  t.swapchain = XR_NULL_HANDLE;
  t.images.clear();
  t.image_acquired = false;
  if (t.staging != VK_NULL_HANDLE) g.vk.DestroyImage(device, t.staging, nullptr);
  if (t.staging_memory != VK_NULL_HANDLE) g.vk.FreeMemory(device, t.staging_memory, nullptr);
  t.staging = VK_NULL_HANDLE;
  t.staging_memory = VK_NULL_HANDLE;
  t.width = t.height = 0;
  t.have_image = false;
}

bool DestroySwapchain() {
  // A timeout is not completion. Retain the swapchains, staging images, pools,
  // fences and guest-image holds until every submitted copy has finished.
  if (!FinishAllSlots()) return false;
  DestroyTarget(g.picture);
  DestroyTarget(g.hud);
  return true;
}

// The cockpit view's size and its pixel density in the middle of the picture (the guest output's
// size: 1280 x 720 at the render scale, which may differ per axis), once both are known.
void LogCockpitView() {
  if (g.mode != Mode::kCockpit || !g.picture.width || !g.picture.height) return;
  CockpitFrustum f;
  {
    std::lock_guard lock(g.pose_mutex);
    f = g.frustum;
  }
  if (f.vertical <= 0) return;
  constexpr float kToDeg = 57.29578f;
  REXLOG_INFO("VR: the cockpit view is drawn {:.0f} deg wide x {:.0f} tall around an axis {:.1f} deg "
              "{} and {:.1f} {} of the head; at {}x{} that is {:.1f} x {:.1f} pixels per degree in the "
              "middle",
              2 * f.HalfHorizontal() * kToDeg, f.vertical * kToDeg, std::fabs(f.pitch) * kToDeg,
              f.pitch < 0 ? "down" : "up", std::fabs(f.yaw) * kToDeg, f.yaw < 0 ? "right" : "left",
              g.picture.width, g.picture.height,
              CentrePixelsPerDegree(float(g.picture.width), f.HalfHorizontal()),
              CentrePixelsPerDegree(float(g.picture.height), f.vertical / 2));
}

// A swapchain (8-bit sRGB) and its staging image: the source blitted to 8 bits, then copied bit
// for bit into the sRGB swapchain image (the sources already hold gamma-encoded values).
bool CreateTarget(Target& t, uint32_t width, uint32_t height, const char* what) {
  DestroyTarget(t);  // the caller made sure no copy into it is in flight
  XrSwapchainCreateInfo info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
  info.usageFlags = XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT | XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT;
  info.format = g.swapchain_format;
  info.sampleCount = 1;
  info.width = width;
  info.height = height;
  info.faceCount = 1;
  info.arraySize = 1;
  info.mipCount = 1;
  XrResult r;
  {
    const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
    r = g.xr.CreateSwapchain(g.session, &info, &t.swapchain);
  }
  if (XR_FAILED(r)) {
    REXLOG_WARN("VR: xrCreateSwapchain {}x{} for the {} failed ({})", width, height, what, ResultName(r));
    t.swapchain = XR_NULL_HANDLE;
    return false;
  }
  uint32_t count = 0;
  g.xr.EnumerateSwapchainImages(t.swapchain, 0, &count, nullptr);
  std::vector<XrSwapchainImageVulkan2KHR> images(count, {XR_TYPE_SWAPCHAIN_IMAGE_VULKAN2_KHR});
  if (XR_FAILED(g.xr.EnumerateSwapchainImages(
          t.swapchain, count, &count, reinterpret_cast<XrSwapchainImageBaseHeader*>(images.data())))) {
    return false;
  }
  for (const auto& image : images) t.images.push_back(image.image);

  const VkDevice device = g.device->device();
  VkImageCreateInfo image_info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
  image_info.imageType = VK_IMAGE_TYPE_2D;
  image_info.format = g.staging_format;
  image_info.extent = {width, height, 1};
  image_info.mipLevels = 1;
  image_info.arrayLayers = 1;
  image_info.samples = VK_SAMPLE_COUNT_1_BIT;
  image_info.tiling = VK_IMAGE_TILING_OPTIMAL;
  image_info.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
  image_info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
  if (g.vk.CreateImage(device, &image_info, nullptr, &t.staging) != VK_SUCCESS) return false;
  VkMemoryRequirements requirements;
  g.vk.GetImageMemoryRequirements(device, t.staging, &requirements);
  VkPhysicalDeviceMemoryProperties memory;
  g.vk.GetPhysicalDeviceMemoryProperties(g.device->physical_device(), &memory);
  uint32_t type = UINT32_MAX;
  for (uint32_t i = 0; i < memory.memoryTypeCount && type == UINT32_MAX; ++i) {
    if ((requirements.memoryTypeBits & (1u << i)) &&
        (memory.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
      type = i;
    }
  }
  VkMemoryAllocateInfo alloc{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
  alloc.allocationSize = requirements.size;
  alloc.memoryTypeIndex = type;
  if (type == UINT32_MAX || g.vk.AllocateMemory(device, &alloc, nullptr, &t.staging_memory) != VK_SUCCESS ||
      g.vk.BindImageMemory(device, t.staging, t.staging_memory, 0) != VK_SUCCESS) {
    REXLOG_WARN("VR: no memory for the {}x{} staging image of the {}", width, height, what);
    return false;
  }
  t.width = width;
  t.height = height;
  REXLOG_INFO("VR: {} swapchain {}x{}, format {}, {} images", what, width, height, g.swapchain_format,
              t.images.size());
  return true;
}

// The target at this size, made (again) if needed; false if that failed (tried again only for
// another size) or copies into the old one are still in flight (the session stops; its cleanup
// waits for them).
bool EnsureTarget(Target& t, uint32_t width, uint32_t height, const char* what) {
  if (width == t.width && height == t.height) return true;
  if (width == t.failed_width && height == t.failed_height) return false;
  if (!FinishAllSlots()) {
    g.stop = true;
    return false;
  }
  if (!CreateTarget(t, width, height, what)) {
    t.failed_width = width;
    t.failed_height = height;
    return false;
  }
  return true;
}

VkImageMemoryBarrier Barrier(VkImage image, VkAccessFlags src, VkAccessFlags dst, VkImageLayout from,
                             VkImageLayout to) {
  VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
  b.srcAccessMask = src;
  b.dstAccessMask = dst;
  b.oldLayout = from;
  b.newLayout = to;
  b.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
  b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
  b.image = image;
  b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  return b;
}

// Acquires the target's next swapchain image, or resumes an acquisition whose wait timed out, and
// waits for it (the queue lock around the acquire, as XR_KHR_vulkan_enable2 asks). XR_TIMEOUT_EXPIRED
// is a success code, but the runtime still owns the image: it is neither copied into nor released
// until a later wait succeeds. Other failures stop the session (its cleanup retires the image).
bool AcquireTarget(Target& t) {
  if (!t.image_acquired) {
    XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
    XrResult r;
    {
      const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
      r = g.xr.AcquireSwapchainImage(t.swapchain, &acquire, &t.acquired_index);
    }
    if (XR_FAILED(r)) return false;
    t.image_acquired = true;
  }
  XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
  wait.timeout = 100'000'000;
  const XrResult r = g.xr.WaitSwapchainImage(t.swapchain, &wait);
  if (r != XR_SUCCESS) {
    if (r != XR_TIMEOUT_EXPIRED) g.stop = true;
    return false;
  }
  return !g.stop;
}

// Hands an acquired image back to the runtime (the caller holds the queue lock); false if the
// runtime refused it.
bool ReleaseTarget(Target& t) {
  XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
  const XrResult r = g.xr.ReleaseSwapchainImage(t.swapchain, &release);
  if (XR_SUCCEEDED(r)) {
    t.image_acquired = false;
    return true;
  }
  REXLOG_WARN("VR: xrReleaseSwapchainImage failed ({})", ResultName(r));
  return false;
}

// Records `source` (shader-read-only, as the SDK hands its images over) blitted to out_width x
// out_height into the target's staging image and copied into its acquired swapchain image.
void RecordCopy(VkCommandBuffer cb, Target& t, VkImage source, uint32_t width, uint32_t height,
                uint32_t out_width, uint32_t out_height) {
  const VkImage target = t.images[t.acquired_index];
  // As the presenter's own guest output capture: the source leaves and returns to
  // SHADER_READ_ONLY in the fragment shader stage, which later writers wait for.
  VkImageMemoryBarrier to_copy[2] = {
      Barrier(source, VK_ACCESS_SHADER_READ_BIT, VK_ACCESS_TRANSFER_READ_BIT,
              VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL),
      Barrier(t.staging, 0, VK_ACCESS_TRANSFER_WRITE_BIT, VK_IMAGE_LAYOUT_UNDEFINED,
              VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL)};
  g.vk.CmdPipelineBarrier(cb, VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                          VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 2, to_copy);
  VkImageBlit blit{};
  blit.srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
  blit.srcOffsets[1] = {int32_t(width), int32_t(height), 1};
  blit.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
  blit.dstOffsets[1] = {int32_t(out_width), int32_t(out_height), 1};
  const bool scaled = out_width != width || out_height != height;
  g.vk.CmdBlitImage(cb, source, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, t.staging,
                    VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &blit,
                    scaled ? VK_FILTER_LINEAR : VK_FILTER_NEAREST);
  VkImageMemoryBarrier after_blit[3] = {
      Barrier(source, 0, VK_ACCESS_SHADER_READ_BIT, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
              VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL),
      Barrier(t.staging, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
              VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL),
      Barrier(target, 0, VK_ACCESS_TRANSFER_WRITE_BIT, VK_IMAGE_LAYOUT_UNDEFINED,
              VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL)};
  g.vk.CmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                          VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT, 0, 0,
                          nullptr, 0, nullptr, 3, after_blit);
  VkImageCopy copy{};
  copy.srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
  copy.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
  copy.extent = {out_width, out_height, 1};
  g.vk.CmdCopyImage(cb, t.staging, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, target,
                    VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
  // Swapchain images go back to the runtime in COLOR_ATTACHMENT_OPTIMAL (XR_KHR_vulkan_enable2).
  VkImageMemoryBarrier release_barrier =
      Barrier(target, VK_ACCESS_TRANSFER_WRITE_BIT,
              VK_ACCESS_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT |
                  VK_ACCESS_SHADER_READ_BIT,
              VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
  g.vk.CmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0,
                          nullptr, 0, nullptr, 1, &release_barrier);
}

// Copies a guest output into the next swapchain image and releases it to the runtime. The frame's
// hold is dropped when the copy's fence signals. For the screen a picture drawn at render scale 2
// or more is scaled down on the way with a linear filter to its 1280 x 720 (per axis, so a picture
// drawn at other scales across and up keeps its 16:9): the screen covers about 1,000 of the
// headset's pixels across, and the runtime samples its layers without mipmaps, so a full
// 2560-pixel picture would shimmer. For the cockpit, with the VR layers (ReIGlue 0393), the
// scene before the HUD goes to the picture and the HUD alone to its own swapchain.
bool CopyFrame(const RexGuestOutputFrame& frame, bool for_screen) {
  if (g.stop) return false;
  bool layers = !for_screen && frame.size >= sizeof(RexGuestOutputFrame) &&
                (frame.vr.layers & kLayerScene) && frame.vr.scene != VK_NULL_HANDLE &&
                frame.vr.scene_width == frame.width && frame.vr.scene_height == frame.height;
  bool with_hud = layers && (frame.vr.layers & kLayerHud) && frame.vr.hud != VK_NULL_HANDLE;
  // Without a HUD swapchain the cockpit shows the guest output, the HUD in it.
  if (with_hud && !EnsureTarget(g.hud, frame.vr.hud_width, frame.vr.hud_height, "HUD")) {
    if (g.stop) return false;
    layers = with_hud = false;
  }
  // ReIGlue 0394: both eyes side by side, made from the scene and its depth.
  const bool stereo = layers && g.stereo && (frame.vr.layers & kLayerStereo) &&
                      frame.vr.stereo != VK_NULL_HANDLE && frame.vr.stereo_width == 2 * frame.width &&
                      frame.vr.stereo_height == frame.height;
  const uint32_t picture_width = stereo ? frame.vr.stereo_width : frame.width;
  if (!EnsureTarget(g.picture, picture_width, frame.height, "picture")) return false;
  CopySlot& slot = g.slots[g.next_slot];
  if (!FinishSlot(slot, 100'000'000)) return false;
  if (g.stop) return false;

  if (!AcquireTarget(g.picture)) return false;
  // A HUD image still owned by the runtime (its wait timed out) misses this frame: the panel keeps
  // its last picture.
  const bool hud_acquired = with_hud && AcquireTarget(g.hud);
  if (g.stop) return false;

  const VkDevice device = g.device->device();
  g.vk.ResetCommandPool(device, slot.pool, 0);
  VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  g.vk.BeginCommandBuffer(slot.buffer, &begin);
  const uint32_t divide_x = for_screen && g.linear_blit ? std::max(1u, frame.width / 1280) : 1;
  const uint32_t divide_y = for_screen && g.linear_blit ? std::max(1u, frame.height / 720) : 1;
  const uint32_t out_width = std::max(1u, picture_width / divide_x);
  const uint32_t out_height = std::max(1u, frame.height / divide_y);
  RecordCopy(slot.buffer, g.picture, stereo ? frame.vr.stereo : layers ? frame.vr.scene : frame.image,
             picture_width, frame.height, out_width, out_height);
  if (hud_acquired) {
    RecordCopy(slot.buffer, g.hud, frame.vr.hud, frame.vr.hud_width, frame.vr.hud_height,
               frame.vr.hud_width, frame.vr.hud_height);
  }
  g.vk.EndCommandBuffer(slot.buffer);

  VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
  submit.commandBufferCount = 1;
  submit.pCommandBuffers = &slot.buffer;
  VkResult submitted;
  bool released, hud_released = true;
  {
    const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
    submitted = g.vk.QueueSubmit(queue.queue(), 1, &submit, slot.fence);
    if (submitted == VK_SUCCESS) {
      slot.hold = frame.handle;
      slot.busy = true;
      g.next_slot = (g.next_slot + 1) % 3;
    }
    released = ReleaseTarget(g.picture);
    if (hud_acquired) hud_released = ReleaseTarget(g.hud);
  }
  if (submitted != VK_SUCCESS) {
    REXLOG_WARN("VR: vkQueueSubmit of the copy failed ({})", int(submitted));
    g.stop = true;
    return false;
  }
  if (!released || !hud_released) {
    // The submission owns the guest frame: keep its hold until the fence completes even though an
    // image could not be handed to the compositor.
    if (!released) g.picture.have_image = false;
    if (!hud_released) g.hud.have_image = false;
    g.stop = true;
    return true;
  }
  g.picture.have_image = true;
  g.picture.shown_extent = {int32_t(out_width), int32_t(out_height)};
  if (g.true_stereo && layers && int(stereo) != g.stereo_logged) {
    g.stereo_logged = int(stereo);
    REXLOG_INFO("VR: {}", stereo ? "each eye gets its own picture" : "both eyes get one picture");
  }
  g.picture_stereo = stereo;
  g.picture_ipd = g.ipd;
  if (hud_acquired) {
    g.hud.have_image = true;
    g.hud.shown_extent = {int32_t(frame.vr.hud_width), int32_t(frame.vr.hud_height)};
  }
  // The HUD panel shows with the cockpit pictures that come without their HUD; a cockpit frame
  // without layers has the HUD in its picture.
  g.hud_shown = with_hud && g.hud.have_image;
  if (layers && !g.layers_logged) {
    g.layers_logged = true;
    REXLOG_INFO("VR: the cockpit gets the scene without the HUD ({}x{}){}{}", frame.width, frame.height,
                with_hud ? fmt::format("; the HUD is on its own panel ({}x{})", frame.vr.hud_width,
                                       frame.vr.hud_height)
                         : std::string(),
                stereo ? (g.true_stereo ? "; each eye drawn from its own point (true stereo)"
                                        : "; each eye its own picture (depth stereo)")
                       : "");
  }
  ++g.stat_copies;
  return true;
}

bool ChooseFormats() {
  uint32_t count = 0;
  g.xr.EnumerateSwapchainFormats(g.session, 0, &count, nullptr);
  std::vector<int64_t> formats(count);
  if (XR_FAILED(g.xr.EnumerateSwapchainFormats(g.session, count, &count, formats.data()))) return false;
  for (int64_t format : formats) {
    if (format == VK_FORMAT_R8G8B8A8_SRGB || format == VK_FORMAT_B8G8R8A8_SRGB) {
      g.swapchain_format = format;
      g.staging_format = format == VK_FORMAT_R8G8B8A8_SRGB ? VK_FORMAT_R8G8B8A8_UNORM
                                                           : VK_FORMAT_B8G8R8A8_UNORM;
      break;
    }
  }
  if (!g.swapchain_format) {
    // No 8-bit sRGB format: an 8-bit UNORM one, blitted to directly (the picture looks too dark).
    for (int64_t format : formats) {
      if (format == VK_FORMAT_R8G8B8A8_UNORM || format == VK_FORMAT_B8G8R8A8_UNORM) {
        g.swapchain_format = format;
        g.staging_format = VkFormat(format);
        REXLOG_WARN("VR: the runtime offers no 8-bit sRGB swapchain format; colours will be off");
        break;
      }
    }
  }
  if (!g.swapchain_format) {
    REXLOG_WARN("VR: the runtime offers no 8-bit RGBA swapchain format");
    return false;
  }
  VkFormatProperties source, staging;
  g.vk.GetPhysicalDeviceFormatProperties(g.device->physical_device(),
                                         VK_FORMAT_A2B10G10R10_UNORM_PACK32, &source);
  g.vk.GetPhysicalDeviceFormatProperties(g.device->physical_device(), g.staging_format, &staging);
  if (!(source.optimalTilingFeatures & VK_FORMAT_FEATURE_BLIT_SRC_BIT) ||
      !(staging.optimalTilingFeatures & VK_FORMAT_FEATURE_BLIT_DST_BIT)) {
    REXLOG_WARN("VR: the GPU cannot blit the game's picture to an 8-bit image");
    return false;
  }
  g.linear_blit = (source.optimalTilingFeatures & VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT) != 0;
  uint32_t modes = 0;
  g.xr.EnumerateEnvironmentBlendModes(g.instance, g.system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
                                      0, &modes, nullptr);
  std::vector<XrEnvironmentBlendMode> blend(modes);
  if (modes && XR_SUCCEEDED(g.xr.EnumerateEnvironmentBlendModes(
                   g.instance, g.system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, modes, &modes,
                   blend.data())) && modes) {
    g.blend = blend[0];
  }
  return true;
}

bool CreateSession() {
  XrGraphicsBindingVulkan2KHR binding{XR_TYPE_GRAPHICS_BINDING_VULKAN2_KHR};
  binding.instance = g.device->vulkan_instance()->instance();
  binding.physicalDevice = g.device->physical_device();
  binding.device = g.device->device();
  binding.queueFamilyIndex = g.device->queue_family_graphics_compute();
  binding.queueIndex = 0;
  XrSessionCreateInfo info{XR_TYPE_SESSION_CREATE_INFO};
  info.next = &binding;
  info.systemId = g.system;
  XrResult r;
  {
    const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
    r = g.xr.CreateSession(g.instance, &info, &g.session);
  }
  if (XR_FAILED(r)) {
    REXLOG_WARN("VR: xrCreateSession failed ({}) - playing flat", ResultName(r));
    g.session = XR_NULL_HANDLE;
    return false;
  }
  XrReferenceSpaceCreateInfo space{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
  space.poseInReferenceSpace.orientation.w = 1;
  space.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
  if (XR_FAILED(r = g.xr.CreateReferenceSpace(g.session, &space, &g.local))) {
    REXLOG_WARN("VR: no LOCAL space ({}) - playing flat", ResultName(r));
    return false;
  }
  space.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
  if (XR_FAILED(r = g.xr.CreateReferenceSpace(g.session, &space, &g.view))) {
    REXLOG_WARN("VR: no VIEW space ({}) - playing flat", ResultName(r));
    return false;
  }
  if (!ChooseFormats()) {
    REXLOG_WARN("VR: no usable swapchain format - playing flat");
    return false;
  }
  uint32_t count = 0;
  g.xr.EnumerateViewConfigurationViews(g.instance, g.system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
                                       0, &count, nullptr);
  std::vector<XrViewConfigurationView> views(count, {XR_TYPE_VIEW_CONFIGURATION_VIEW});
  if (count && XR_SUCCEEDED(g.xr.EnumerateViewConfigurationViews(
                   g.instance, g.system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, count, &count,
                   views.data()))) {
    REXLOG_INFO("VR: session created; the headset recommends {}x{} per eye",
                views[0].recommendedImageRectWidth, views[0].recommendedImageRectHeight);
  }
  return true;
}

// While a session runs the player looks at the headset and the desktop window usually has no
// focus: pads and wheels keep working then (ReIGlue 0392; the keyboard stays focus-bound).
void PadsWithoutFocus(bool on) {
  if (g.set_pads_without_focus) g.set_pads_without_focus(on ? 1 : 0);
}

bool DestroySession() {
  g.cockpit_active = false;
  PadsWithoutFocus(false);
  {
    std::lock_guard lock(g.pose_mutex);  // no CockpitView is locating now
    if (g.session != XR_NULL_HANDLE && !DestroySwapchain()) return false;
    if (g.view != XR_NULL_HANDLE) g.xr.DestroySpace(g.view);
    if (g.local != XR_NULL_HANDLE) g.xr.DestroySpace(g.local);
    g.view = g.local = XR_NULL_HANDLE;
  }
  if (g.session != XR_NULL_HANDLE) {
    const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
    g.xr.DestroySession(g.session);
  }
  g.session = XR_NULL_HANDLE;
  g.running = false;
  if (!FinishEndFence(100'000'000)) return false;
  if (g.end_fence) g.vk.DestroyFence(g.device->device(), g.end_fence, nullptr);
  g.end_fence = VK_NULL_HANDLE;
  if (g.set_export) g.set_export(0);
  g.export_true_stereo = false;
  for (CopySlot& slot : g.slots) {
    if (slot.fence) g.vk.DestroyFence(g.device->device(), slot.fence, nullptr);
    if (slot.pool) g.vk.DestroyCommandPool(g.device->device(), slot.pool, nullptr);
    slot = {};
  }
  return true;
}

void FinishSession() {
  bool warned = false;
  while (!DestroySession()) {
    if (!warned) REXLOG_WARN("VR: waiting for pending GPU copies before destroying session resources");
    warned = true;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  g.finished = true;
}

const char* StateName(XrSessionState s) {
  switch (s) {
    case XR_SESSION_STATE_IDLE: return "idle";
    case XR_SESSION_STATE_READY: return "ready";
    case XR_SESSION_STATE_SYNCHRONIZED: return "synchronized";
    case XR_SESSION_STATE_VISIBLE: return "visible";
    case XR_SESSION_STATE_FOCUSED: return "focused";
    case XR_SESSION_STATE_STOPPING: return "stopping";
    case XR_SESSION_STATE_LOSS_PENDING: return "loss pending";
    case XR_SESSION_STATE_EXITING: return "exiting";
    default: return "unknown";
  }
}

// Handles the runtime's events; false when the session is over (exiting or lost).
// XR_FB_display_refresh_rate: the rate ChooseRefreshRate gives for the game's frame rate.
void RequestRefreshRate() {
  if (!g.xr.EnumerateDisplayRefreshRatesFB || !g.xr.RequestDisplayRefreshRateFB) {
    REXLOG_INFO("VR: the runtime cannot set the headset's refresh rate (no XR_FB_display_refresh_rate)");
    return;
  }
  uint32_t count = 0;
  if (XR_FAILED(g.xr.EnumerateDisplayRefreshRatesFB(g.session, 0, &count, nullptr)) || !count) return;
  std::vector<float> rates(count);
  if (XR_FAILED(g.xr.EnumerateDisplayRefreshRatesFB(g.session, count, &count, rates.data()))) return;
  float current = 0;
  if (g.xr.GetDisplayRefreshRateFB) g.xr.GetDisplayRefreshRateFB(g.session, &current);
  std::string offered;
  for (float r : rates) offered += fmt::format("{}{:.0f}", offered.empty() ? "" : "/", r);
  const int requested = REXCVAR_GET(vr_refresh_rate);
  const float rate = ChooseRefreshRate(rates.data(), count, REXCVAR_GET(frame_rate), requested);
  if (rate <= 0) {
    REXLOG_INFO("VR: the headset runs at {:.0f} Hz ({} offered; vr_refresh_rate -1 leaves it)", current, offered);
    return;
  }
  if (std::fabs(rate - current) < 0.5f) {
    REXLOG_INFO("VR: the headset runs at {:.0f} Hz ({} offered)", current, offered);
    return;
  }
  const XrResult r = g.xr.RequestDisplayRefreshRateFB(g.session, rate);
  REXLOG_INFO("VR: asked the headset for {:.0f} Hz for {} {} ({} offered; it ran at {:.0f}){}", rate,
              requested > 0 ? requested : REXCVAR_GET(frame_rate), requested > 0 ? "Hz asked" : "fps",
              offered, current, XR_FAILED(r) ? " - refused (" + ResultName(r) + ")" : std::string());
}

bool PollEvents() {
  XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
  while (g.xr.PollEvent(g.instance, &event) == XR_SUCCESS) {
    switch (event.type) {
      case XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED: {
        const auto& changed = *reinterpret_cast<XrEventDataSessionStateChanged*>(&event);
        g.state = changed.state;
        REXLOG_INFO("VR: session {}", StateName(g.state));
        if (g.state == XR_SESSION_STATE_READY) {
          XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};
          begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
          const XrResult r = g.xr.BeginSession(g.session, &begin);
          if (XR_FAILED(r)) {
            REXLOG_WARN("VR: xrBeginSession failed ({}) - playing flat", ResultName(r));
            return false;
          }
          g.running = true;
          g.recenter = true;
          RequestRefreshRate();
          g.stat_since = std::chrono::steady_clock::now();
          PadsWithoutFocus(true);
        } else if (g.state == XR_SESSION_STATE_STOPPING) {
          g.cockpit_active = false;
          g.running = false;
          PadsWithoutFocus(false);
          g.xr.EndSession(g.session);
        } else if (g.state == XR_SESSION_STATE_EXITING || g.state == XR_SESSION_STATE_LOSS_PENDING) {
          return false;
        }
        break;
      }
      case XR_TYPE_EVENT_DATA_DISPLAY_REFRESH_RATE_CHANGED_FB: {
        const auto& changed = *reinterpret_cast<XrEventDataDisplayRefreshRateChangedFB*>(&event);
        REXLOG_INFO("VR: the headset's refresh rate changed from {:.0f} to {:.0f} Hz",
                    changed.fromDisplayRefreshRate, changed.toDisplayRefreshRate);
        break;
      }
      case XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING:
        REXLOG_WARN("VR: the OpenXR runtime is going away");
        return false;
      case XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING:
        REXLOG_INFO("VR: the runtime recentred its space");
        g.recenter = true;
        break;
      default:
        break;
    }
    event = {XR_TYPE_EVENT_DATA_BUFFER};
  }
  return true;
}

void LogStats() {
  const auto now = std::chrono::steady_clock::now();
  const double seconds = std::chrono::duration<double>(now - g.stat_since).count();
  if (seconds < 10) return;
  const uint32_t swaps = g.stat_swaps.exchange(0), tagged = g.stat_tagged_swaps.exchange(0);
  const uint32_t extra = g.stat_extra_views.exchange(0), repeat = g.stat_repeat_views.exchange(0);
  // Latency is measured on cockpit frames only (a tagged picture has the time its camera was
  // built): without any in this window there is nothing to report.
  std::string latency = "latency -";
  if (g.stat_latency_samples) {
    const double n = g.stat_latency_samples;
    latency = fmt::format("latency {:.1f} ms (game {:.1f} + runtime {:.1f}; predicting {:.1f})",
                          g.latency.Raw() / 1e6, g.stat_game_ns / n / 1e6,
                          g.stat_runtime_ns / n / 1e6, g.latency.Value() / 1e6);
  }
  if (g.stat_slack_n) {
    latency += fmt::format("; frames ready {:.1f} ms (least {:.1f}) before taken, {} passed over, "
                           "waited for {} ({} came, {:.1f} ms a wait)",
                           g.stat_slack_sum / double(g.stat_slack_n) / 1e6, g.stat_slack_min / 1e6,
                           g.stat_skips, g.stat_waits, g.stat_waits_found,
                           g.stat_waits ? g.stat_waited_ns / double(g.stat_waits) / 1e6 : 0.0);
  }
  auto spread = [](double sum, double sq, uint32_t n) {
    const double mean = sum / n;
    return fmt::format("{:.1f}+-{:.1f}", mean, std::sqrt(std::max(0.0, sq / n - mean * mean)));
  };
  if (g.stat_pickup_n && g.stat_ready_n) {
    latency += fmt::format("; taken every {} ms, published every {} ms",
                           spread(g.stat_pickup_sum, g.stat_pickup_sq, g.stat_pickup_n),
                           spread(g.stat_ready_sum, g.stat_ready_sq, g.stat_ready_n));
  }
  g.stat_pickup_sum = g.stat_pickup_sq = g.stat_ready_sum = g.stat_ready_sq = 0;
  g.stat_pickup_n = g.stat_ready_n = 0;
  g.stat_slack_min = INT64_MAX;
  g.stat_slack_sum = g.stat_waited_ns = 0;
  g.stat_slack_n = g.stat_skips = g.stat_waits = g.stat_waits_found = 0;
  REXLOG_INFO(
      "VR: {:.0f} Hz headset, {:.1f} game frames/s copied; shown as cockpit {} (HUD panel {}) / "
      "screen {} frames; swaps tagged {}/{} (dropped views {}, repeated {}); {}; xrWaitFrame {:.1f} ms, "
      "previous frame's GPU work {:.1f} ms, xrEndFrame {:.1f} ms (max {:.1f})",
      g.stat_frames / seconds, g.stat_copies / seconds, g.stat_projection, g.stat_hud, g.stat_quad,
      tagged, swaps, extra, repeat, latency, g.stat_wait_frame_ns / std::max(g.stat_frames, 1u) / 1e6,
      g.stat_end_wait_ns / std::max(g.stat_frames, 1u) / 1e6,
      g.stat_end_frame_ns / std::max(g.stat_frames, 1u) / 1e6, g.stat_end_frame_max_ns / 1e6);
  g.stat_frames = g.stat_projection = g.stat_quad = g.stat_hud = g.stat_copies = 0;
  g.stat_end_frame_max_ns = g.stat_end_frame_ns = g.stat_wait_frame_ns = g.stat_end_wait_ns = 0;
  g.stat_game_ns = g.stat_runtime_ns = 0;
  g.stat_latency_samples = 0;
  g.stat_since = now;
}

float StereoMaxShift() { return float(REXCVAR_GET(vr_stereo_max_shift)) / 100.0f; }
// ReIGlue 0396: the cockpit view's projection scale in x (the game's P00).
float TrueStereoP00(const CockpitFrustum& f) { return 1.0f / std::tan(f.HalfHorizontal()); }

HeadBox Box() {
  return {float(REXCVAR_GET(vr_head_side_limit_cm)) / 100.0f,
          float(REXCVAR_GET(vr_head_back_limit_cm)) / 100.0f,
          float(REXCVAR_GET(vr_head_up_limit_cm)) / 100.0f};
}

// The recentred head held in the eye's room, or only turned (vr_head_position off).
Pose HeldHead(const Pose& rel) {
  if (!REXCVAR_GET(vr_head_position)) return {rel.q, {0, 0, 0}};
  return LimitHead(rel, Box());
}

// The game's pause flag and menus (hooks/telemetry.cpp feeds the gate from the world update).
bool GameMenuUp() {
  switch (telemetry::Player().State()) {
    case telemetry::Drive::kPause:
    case telemetry::Drive::kRewind:
    case telemetry::Drive::kMenu:
    case telemetry::Drive::kLoading:
      return true;
    default:
      return false;
  }
}

// vr_trace, about 10 times a second: the head relative to the recentred pose (yaw left, pitch up,
// roll left in degrees; x right, y up, z forward in cm, as the game's camera takes them), the
// game camera and whether the head pose went into it, what the headset shows, the field of view
// the shown picture was drawn with, and the last cockpit frame's latency.
void Trace(XrTime display_time, Layer layer) {
  const int64_t now = SteadyNs();
  if (now < g.trace_next_ns) return;
  g.trace_next_ns = now + 100'000'000;
  std::string head = "head not tracked";
  {
    std::lock_guard lock(g.pose_mutex);
    Pose located;
    if (g.origin_set && LocateHead(display_time, located)) {
      const Pose rel = Relative(g.origin, located);
      float yaw, pitch, roll;
      YawPitchRoll(rel.q, yaw, pitch, roll);
      // + 0.0f: no "-0" in the log.
      head = fmt::format("head yaw {:.0f} pitch {:.0f} roll {:.0f} deg, x {:.1f} y {:.1f} z {:.1f} cm",
                         std::round(yaw * 57.2958f) + 0.0f, std::round(pitch * 57.2958f) + 0.0f,
                         std::round(roll * 57.2958f) + 0.0f, std::round(rel.p.x * 1000) / 10 + 0.0f,
                         std::round(rel.p.y * 1000) / 10 + 0.0f, std::round(-rel.p.z * 1000) / 10 + 0.0f);
      vr::HeadPose test;
      if (TestPoseOffset(test))
        head += fmt::format(" + test offset yaw {:.0f} pitch {:.0f} roll {:.0f}, x {:.1f} y {:.1f} z {:.1f} cm",
                            test.yaw, test.pitch, test.roll, test.x * 100, test.y * 100, test.z * 100);
      // How far from the head at display time the shown cockpit picture was drawn (the runtime
      // turns the picture to the head but cannot move it: this offset is what makes the cabin
      // swim; the eye box's hold, the prediction's miss over the latency, a test offset).
      if (layer == Layer::kProjection && g.shown_tag.id) {
        const Vec3 d{g.shown_tag.head.p.x - located.p.x, g.shown_tag.head.p.y - located.p.y,
                     g.shown_tag.head.p.z - located.p.z};
        head += fmt::format(" (picture drawn {:.1f} cm away)", std::round(std::sqrt(d.x * d.x + d.y * d.y + d.z * d.z) * 1000) / 10);
      }
      // Where the eye was held (the head box), when that is not where the head is.
      const Pose eye = HeldHead(rel);
      if (std::fabs(eye.p.x - rel.p.x) + std::fabs(eye.p.y - rel.p.y) + std::fabs(eye.p.z - rel.p.z) > 0.005f)
        head += fmt::format(" (eye held at x {:.1f} y {:.1f} z {:.1f} cm)", std::round(eye.p.x * 1000) / 10 + 0.0f,
                            std::round(eye.p.y * 1000) / 10 + 0.0f, std::round(-eye.p.z * 1000) / 10 + 0.0f);
    }
  }
  const int64_t view_age = now - g.trace_view_ns.load();
  std::string camera;
  if (!g.trace_view_ns.load() || view_age > 500'000'000) {
    camera = "no game view";
  } else {
    const char* name = g.trace_camera_name.load();
    camera = fmt::format("camera {} {:08X} look {:.0f} deg, {}", name ? name : "?", g.trace_camera.load(),
                         std::round(g.trace_look.load() * 57.2958f) + 0.0f,
                         g.trace_posed.load() ? "posed" : "not posed");
  }
  std::string shown = layer == Layer::kProjection ? "cockpit" : layer == Layer::kQuad ? "screen" : "nothing";
  if (g.menu_up && layer == Layer::kQuad) shown += " (paused or menu)";
  if (layer == Layer::kProjection && g.picture_stereo) shown += " stereo";
  if (layer == Layer::kProjection && g.hud_shown) shown += " with the HUD panel";
  if (layer == Layer::kProjection) {
    const CockpitFrustum& f = g.shown_tag.frustum;
    shown += fmt::format(" {:.0f}x{:.0f} deg pitch {:.1f}", 2 * f.HalfHorizontal() * 57.2958f,
                         f.vertical * 57.2958f, f.pitch * 57.2958f);
  }
  const int64_t game_ns = g.last_game_ns.load(), runtime_ns = g.last_runtime_ns.load();
  const std::string latency =
      game_ns || runtime_ns ? fmt::format("latency {:.0f} ms (game {:.0f} + runtime {:.0f})",
                                          (game_ns + runtime_ns) / 1e6, game_ns / 1e6, runtime_ns / 1e6)
                            : "latency -";
  REXLOG_INFO("VR trace: {}; {}; shows {}; {}", head, camera, shown, latency);
}

// One headset frame: wait, take the newest game picture, submit the layer.
// The newest game frame newer than the last one taken; with vr_frame_wait, when there is none yet
// but one is due (the game publishes one each headset frame and the last came about a period ago),
// waits up to vr_frame_wait for it, polling every quarter millisecond.
bool AcquireNewest(RexGuestOutputFrame& frame, int64_t period_ns) {
  if (g.acquire_frame(&frame, g.last_serial)) return true;
  if (g.frame_wait_ns <= 0 || FrameWaitOffTest() || !g.last_ready_ns || period_ns <= 0) return false;
  const int64_t start = SteadyNs();
  const int64_t due = g.last_ready_ns + period_ns;
  // Not due within the wait, or long overdue (the game is slower than the headset, loading).
  if (due - start > g.frame_wait_ns || start - due > period_ns / 2) return false;
  ++g.stat_waits;
  bool found = false;
  while (!g.stop && SteadyNs() - start < g.frame_wait_ns) {
    std::this_thread::sleep_for(std::chrono::microseconds(250));
    if (g.acquire_frame(&frame, g.last_serial)) {
      found = true;
      break;
    }
  }
  g.stat_waited_ns += SteadyNs() - start;
  g.stat_waits_found += found;
  return found;
}

bool Frame() {
  XrFrameWaitInfo wait{XR_TYPE_FRAME_WAIT_INFO};
  XrFrameState frame_state{XR_TYPE_FRAME_STATE};
  const int64_t wait_start = SteadyNs();
  XrResult r = g.xr.WaitFrame(g.session, &wait, &frame_state);
  g.stat_wait_frame_ns += SteadyNs() - wait_start;
  if (XR_FAILED(r)) {
    if (!g.frame_failures++) REXLOG_WARN("VR: xrWaitFrame failed ({})", ResultName(r));
    return r != XR_ERROR_SESSION_LOST && r != XR_ERROR_INSTANCE_LOST;
  }
  g.wait_display_time = frame_state.predictedDisplayTime;
  g.wait_period = frame_state.predictedDisplayPeriod;
  g.wait_steady_ns = SteadyNs();
  {
    const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
    XrFrameBeginInfo begin{XR_TYPE_FRAME_BEGIN_INFO};
    r = g.xr.BeginFrame(g.session, &begin);
  }
  if (XR_FAILED(r)) {
    if (!g.frame_failures++) REXLOG_WARN("VR: xrBeginFrame failed ({})", ResultName(r));
    return r != XR_ERROR_SESSION_LOST && r != XR_ERROR_INSTANCE_LOST;
  }
  ++g.stat_frames;

  // The eyes' fields of view and orientations relative to the head: what the cockpit view has
  // to cover.
  XrViewLocateInfo locate{XR_TYPE_VIEW_LOCATE_INFO};
  locate.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
  locate.displayTime = frame_state.predictedDisplayTime;
  locate.space = g.view;
  XrViewState view_state{XR_TYPE_VIEW_STATE};
  XrView eyes[2] = {{XR_TYPE_VIEW}, {XR_TYPE_VIEW}};
  uint32_t eye_count = 0;
  if (g.mode == Mode::kCockpit &&
      XR_SUCCEEDED(g.xr.LocateViews(g.session, &locate, &view_state, 2, &eye_count, eyes)) &&
      eye_count == 2) {
    const Fov fovs[2] = {
        {eyes[0].fov.angleLeft, eyes[0].fov.angleRight, eyes[0].fov.angleUp, eyes[0].fov.angleDown},
        {eyes[1].fov.angleLeft, eyes[1].fov.angleRight, eyes[1].fov.angleUp, eyes[1].fov.angleDown}};
    const bool oriented = view_state.viewStateFlags & XR_VIEW_STATE_ORIENTATION_VALID_BIT;
    if (g.stereo && (view_state.viewStateFlags & XR_VIEW_STATE_POSITION_VALID_BIT)) {
      const XrVector3f a = eyes[0].pose.position, b = eyes[1].pose.position;
      const float ipd = std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z));
      if (ipd > 0.04f && ipd < 0.09f && std::fabs(ipd - g.ipd) > 0.0005f) {
        g.ipd = ipd;
        if (g.true_stereo) {
          if (g.true_stereo_p00 > 0) g.set_true_stereo(ipd, g.true_stereo_p00);
        } else {
          g.set_stereo(ipd, StereoMaxShift());
        }
        REXLOG_INFO("VR: stereo with the headset's eye distance, {:.1f} mm", ipd * 1000);
      }
    }
    const Quat rotation[2] = {oriented ? ToPose(eyes[0].pose).q : Quat{},
                              oriented ? ToPose(eyes[1].pose).q : Quat{}};
    const float margin = float(REXCVAR_GET(vr_fov_margin)) * 3.14159265f / 180.0f;
    const CockpitFrustum f = CoverEyes(fovs, rotation, margin, 1.5f, 2.2f);
    bool changed;
    {
      std::lock_guard lock(g.pose_mutex);
      changed = std::fabs(f.vertical - g.frustum.vertical) > 0.005f ||
                std::fabs(f.aspect - g.frustum.aspect) > 0.005f ||
                std::fabs(f.pitch - g.frustum.pitch) > 0.005f || std::fabs(f.yaw - g.frustum.yaw) > 0.005f;
      if (changed) g.frustum = f;
    }
    // ReIGlue 0396: the eyes' offset in clip space needs the cockpit view's P00; each eye is drawn
    // across its part of the picture.
    if (g.true_stereo && f.vertical > 0 && std::fabs(TrueStereoP00(f) - g.true_stereo_p00) > 1e-4f) {
      g.true_stereo_p00 = TrueStereoP00(f);
      g.set_true_stereo(g.ipd, g.true_stereo_p00);
    }
    if (g.true_stereo && g.set_true_stereo_eyes && f.vertical > 0) {
      float eye_x[4];
      for (int i = 0; i < 2; ++i)
        EyePictureRange(fovs[i], rotation[i], f, margin, eye_x[2 * i], eye_x[2 * i + 1]);
      bool eyes_changed = false;
      for (int i = 0; i < 4; ++i) eyes_changed |= std::fabs(eye_x[i] - g.true_stereo_eye_x[i]) > 0.002f;
      if (eyes_changed) {
        std::copy(eye_x, eye_x + 4, g.true_stereo_eye_x);
        g.set_true_stereo_eyes(REXCVAR_GET(vr_stereo_eye_parts) ? eye_x : nullptr);
        REXLOG_INFO("VR: true stereo draws the left eye across {:.0f}-{:.0f} % of the picture, the "
                    "right eye across {:.0f}-{:.0f} %",
                    eye_x[0] * 100, eye_x[1] * 100, eye_x[2] * 100, eye_x[3] * 100);
      }
    }
    if (changed) {
      REXLOG_INFO("VR: eye fields of view L {:.0f}/{:.0f}/{:.0f}/{:.0f}, R {:.0f}/{:.0f}/{:.0f}/{:.0f} deg",
                  fovs[0].left * 57.2958f, fovs[0].right * 57.2958f, fovs[0].up * 57.2958f,
                  fovs[0].down * 57.2958f, fovs[1].left * 57.2958f, fovs[1].right * 57.2958f,
                  fovs[1].up * 57.2958f, fovs[1].down * 57.2958f);
      LogCockpitView();
    }
  }

  if (g.recenter.load()) {
    Pose head;
    if (LocateHead(frame_state.predictedDisplayTime, head)) {
      {
        std::lock_guard lock(g.pose_mutex);
        g.origin = RecenterOrigin(head);
        g.origin_set = true;
      }
      g.recenter = false;
      REXLOG_INFO("VR: recentred (head at {:.2f} {:.2f} {:.2f} m)", head.p.x, head.p.y, head.p.z);
    }
  }
  // While the game is paused or a menu is up (the pause and rewind menus, the start menu, loading)
  // the in-car view is not head-posed: the game draws its own in-car view under the menu and the
  // picture goes to the screen, where the menu can be read (version 2 drew it into the 123 degree
  // cockpit view, its text at the edges of the eyes).
  const bool menu = GameMenuUp();
  if (menu != g.menu_up) {
    g.menu_up = menu;
    if (g.mode == Mode::kCockpit)
      REXLOG_INFO("VR: {}", menu ? "the game is paused or in a menu: the picture is on the screen"
                                 : "the game runs again: the in-car view follows the head");
  }
  g.cockpit_active = !g.stop && g.mode == Mode::kCockpit && g.origin_set && !menu &&
                     (g.state == XR_SESSION_STATE_VISIBLE || g.state == XR_SESSION_STATE_FOCUSED);
  // ReIGlue 0396: the game draws each eye only while the cockpit is shown (the screen and the menus
  // get one picture, at the GPU cost of one).
  const bool want_true_stereo = g.cockpit_active && !StereoOffTest();
  if (g.true_stereo && want_true_stereo != g.export_true_stereo && g.set_export) {
    g.export_true_stereo = want_true_stereo;
    g.set_export(int(g.export_flags | (g.export_true_stereo ? kExportTrueStereo : 0)));
  }

  if (frame_state.shouldRender) {
    const int64_t pickup_ns = SteadyNs();
    if (g.last_pickup_ns) {
      const double d = (pickup_ns - g.last_pickup_ns) / 1e6;
      g.stat_pickup_sum += d;
      g.stat_pickup_sq += d * d;
      ++g.stat_pickup_n;
    }
    g.last_pickup_ns = pickup_ns;
    RexGuestOutputFrame frame{sizeof(RexGuestOutputFrame)};
    if (AcquireNewest(frame, frame_state.predictedDisplayPeriod)) {
      const uint64_t skipped =
          g.last_serial && frame.serial > g.last_serial + 1 ? frame.serial - g.last_serial - 1 : 0;
      g.last_serial = frame.serial;
      if (frame.size >= offsetof(RexGuestOutputFrame, ready_ns) + sizeof(int64_t) && frame.ready_ns) {
        if (g.last_ready_ns && !skipped) {
          const double d = (frame.ready_ns - g.last_ready_ns) / 1e6;
          g.stat_ready_sum += d;
          g.stat_ready_sq += d * d;
          ++g.stat_ready_n;
        }
        g.last_ready_ns = frame.ready_ns;
        const int64_t slack_ns = pickup_ns - frame.ready_ns;  // negative: waited for it
        g.stat_slack_min = std::min(g.stat_slack_min, slack_ns);
        g.stat_slack_sum += slack_ns;
        ++g.stat_slack_n;
      }
      g.stat_skips += uint32_t(skipped);
      FrameTag tag;
      {
        std::lock_guard lock(g.tags_mutex);
        if (const FrameTag* found = g.tags.Find(frame.tag)) tag = *found;
      }
      const bool cockpit = ChooseLayer(true, g.mode == Mode::kCockpit, tag.id ? &tag : nullptr,
                                       frame_state.predictedDisplayTime) == Layer::kProjection;
      if (CopyFrame(frame, !cockpit)) {
        if (tag.id && tag.id != g.shown_tag.id &&
            frame_state.predictedDisplayTime - tag.build_time <= kMaxTagAgeNs) {
          // Built to shown, and its two parts: built to here (the game's frames in flight, the
          // render thread, the GPU, this thread's pick-up) and here to the display time (the
          // runtime's compositor and, for WiVRn, encoding, the network and the headset).
          const int64_t now = NowXrTime();
          g.latency.Add(frame_state.predictedDisplayTime - tag.build_time);
          g.latency_ns = g.latency.Value();
          g.last_game_ns = now - tag.build_time;
          g.last_runtime_ns = frame_state.predictedDisplayTime - now;
          g.stat_game_ns += now - tag.build_time;
          g.stat_runtime_ns += frame_state.predictedDisplayTime - now;
          ++g.stat_latency_samples;
        }
        g.shown_tag = tag;
      } else {
        g.release_frame(frame.handle);
      }
    }
  }
  ReapSlots();

  XrCompositionLayerProjectionView views[2] = {{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW},
                                               {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}};
  XrCompositionLayerProjection projection{XR_TYPE_COMPOSITION_LAYER_PROJECTION};
  XrCompositionLayerQuad quad{XR_TYPE_COMPOSITION_LAYER_QUAD};
  XrCompositionLayerQuad hud{XR_TYPE_COMPOSITION_LAYER_QUAD};
  const XrCompositionLayerBaseHeader* layers[2] = {};
  uint32_t layer_count = 0;
  const XrRect2Di rect{{0, 0}, g.picture.shown_extent};
  const Layer layer = frame_state.shouldRender
                          ? ChooseLayer(g.picture.have_image, g.mode == Mode::kCockpit,
                                        g.shown_tag.id ? &g.shown_tag : nullptr,
                                        frame_state.predictedDisplayTime)
                          : Layer::kNone;
  if (layer == Layer::kProjection) {
    // Mono: both eyes get the picture as drawn from the head pose (turned to the drawn axis) and
    // the field of view it was rendered with.
    // With depth stereo (ReIGlue 0394) each eye gets its half of the picture, made for that eye
    // (the head's pose moved half the eye distance along its x axis).
    const Fov fov = g.shown_tag.frustum.ImageFov();
    for (int i = 0; i < 2; ++i) {
      XrCompositionLayerProjectionView& view = views[i];
      view.fov = {fov.left, fov.right, fov.up, fov.down};
      if (g.picture_stereo) {
        const int32_t half = rect.extent.width / 2;
        view.pose = ToXr(EyePose(g.shown_tag.head, i, g.picture_ipd));
        view.subImage = {g.picture.swapchain, {{i * half, 0}, {half, rect.extent.height}}, 0};
      } else {
        view.pose = ToXr(g.shown_tag.head);
        view.subImage = {g.picture.swapchain, rect, 0};
      }
    }
    projection.space = g.local;
    projection.viewCount = 2;
    projection.views = views;
    layers[layer_count++] = reinterpret_cast<const XrCompositionLayerBaseHeader*>(&projection);
    ++g.stat_projection;
    // The HUD on its panel, fixed in front of the seat below the eyes (LOCAL is car-fixed while
    // seated): drawn over the cockpit with its own coverage.
    if (g.hud_shown && g.hud.have_image) {
      Pose origin;
      {
        std::lock_guard lock(g.pose_mutex);
        origin = g.origin;
      }
      const float width = float(REXCVAR_GET(vr_hud_width));
      hud.layerFlags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT |
                       XR_COMPOSITION_LAYER_UNPREMULTIPLIED_ALPHA_BIT;
      hud.space = g.local;
      hud.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
      hud.subImage = {g.hud.swapchain, {{0, 0}, g.hud.shown_extent}, 0};
      hud.pose = ToXr(Compose(origin, PanelPose(float(REXCVAR_GET(vr_hud_distance)),
                                                float(REXCVAR_GET(vr_hud_pitch)) * 3.14159265f / 180.0f)));
      hud.size = {width, width * float(g.hud.shown_extent.height) / float(std::max(g.hud.shown_extent.width, 1))};
      layers[layer_count++] = reinterpret_cast<const XrCompositionLayerBaseHeader*>(&hud);
      ++g.stat_hud;
    }
  } else if (layer == Layer::kQuad) {
    Pose origin;
    {
      std::lock_guard lock(g.pose_mutex);
      origin = g.origin;
    }
    const float distance = float(REXCVAR_GET(vr_screen_distance));
    const float width = float(REXCVAR_GET(vr_screen_width));
    quad.space = g.local;
    quad.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
    quad.subImage = {g.picture.swapchain, rect, 0};
    quad.pose = ToXr(Compose(origin, {Quat{}, Vec3{0, 0, -distance}}));
    quad.size = {width, width * float(g.picture.shown_extent.height) /
                            float(std::max(g.picture.shown_extent.width, 1))};
    layers[layer_count++] = reinterpret_cast<const XrCompositionLayerBaseHeader*>(&quad);
    ++g.stat_quad;
  }
  XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};
  end.displayTime = frame_state.predictedDisplayTime;
  end.environmentBlendMode = g.blend;
  end.layerCount = layer_count;
  end.layers = layers;
  // Monado's compositor (WiVRn's) blocks in xrEndFrame until the GPU has finished the previous
  // frame's work (it waits for that frame's semaphore before taking the next). xrEndFrame must hold
  // the device's queue lock (the runtime submits on the game's queue in it), so for that whole wait
  // the game's command processor could not submit: with true stereo on the real headset the game
  // spent 14 ms of every 14 ms frame blocked there and the GPU idled (56 fps at 83 % busy). Wait for
  // the previous frame here, without the lock: the empty submission made after the last xrEndFrame
  // is done once everything the runtime submitted then is.
  if (!g.locked_end_frame) {
    const int64_t wait_start = SteadyNs();
    FinishEndFence(50'000'000);
    g.stat_end_wait_ns += SteadyNs() - wait_start;
  }
  const int64_t end_start = SteadyNs();
  {
    const auto queue = g.device->AcquireQueue(g.device->queue_family_graphics_compute(), 0);
    r = g.xr.EndFrame(g.session, &end);
    if (!g.locked_end_frame && g.end_fence != VK_NULL_HANDLE && !g.end_fence_pending) {
      VkSubmitInfo marker{VK_STRUCTURE_TYPE_SUBMIT_INFO};
      g.end_fence_pending = g.vk.QueueSubmit(queue.queue(), 1, &marker, g.end_fence) == VK_SUCCESS;
    }
  }
  g.stat_end_frame_max_ns = std::max(g.stat_end_frame_max_ns, SteadyNs() - end_start);
  g.stat_end_frame_ns += SteadyNs() - end_start;
  if (XR_FAILED(r)) {
    if (!g.frame_failures++) REXLOG_WARN("VR: xrEndFrame failed ({})", ResultName(r));
    return r != XR_ERROR_SESSION_LOST && r != XR_ERROR_INSTANCE_LOST;
  }
  g.frame_failures = 0;
  LogStats();
  if (g.trace) Trace(frame_state.predictedDisplayTime, layer);
  return true;
}

void Run() {
#if defined(__APPLE__)
  pthread_setname_np("Forza OpenXR");  // macOS names only the calling thread
#elif !defined(_WIN32)
  pthread_setname_np(pthread_self(), "Forza OpenXR");
#endif
  if (!LoadDeviceFunctions() || !CreateCopySlots()) {
    REXLOG_WARN("VR: Vulkan functions or command buffers missing - playing flat");
    FinishSession();
    return;
  }
  if (!CreateSession()) {
    FinishSession();
    return;
  }
  bool exit_requested = false;
  const int64_t never = INT64_MAX;
  int64_t exit_deadline = never;
  while (true) {
    if (g.stop && !exit_requested) {
      exit_requested = true;
      exit_deadline = SteadyNs() + 1'000'000'000;
      if (g.running) g.xr.RequestExitSession(g.session);
    }
    if (exit_requested && (!g.running || SteadyNs() > exit_deadline)) break;
    if (!PollEvents()) {
      if (!exit_requested) REXLOG_WARN("VR: the session ended ({}) - playing flat", StateName(g.state));
      break;
    }
    if (!g.running) {
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
      continue;
    }
    if (!Frame()) {
      REXLOG_WARN("VR: the session was lost - playing flat");
      break;
    }
    if (g.frame_failures > 100) {
      REXLOG_WARN("VR: the runtime keeps refusing frames - playing flat");
      break;
    }
  }
  FinishSession();
  REXLOG_INFO("VR: OpenXR thread stopped");
}

Mode RequestedMode() {
  const std::string mode = REXCVAR_GET(vr_mode);
  if (mode == "screen") return Mode::kScreen;
  if (mode == "cockpit") return Mode::kCockpit;
  return Mode::kOff;
}

int64_t pad_held_since = 0;  // the pad polling thread only
bool pad_fired = false;

// What the probe of the runtime found, handed from its thread to Prepare.
struct ProbeResult {
  std::mutex mutex;
  std::condition_variable done_cv;
  bool done = false;
  bool abandoned = false;  // Prepare stopped waiting: the probe cleans up after itself
  std::string error;  // empty: a headset is there
  Api xr;
  XrInstance instance = XR_NULL_HANDLE;
  XrSystemId system = XR_NULL_SYSTEM_ID;
  std::string runtime_name, system_name;
  uint32_t vendor = 0;
  XrVersion vulkan_min = 0, vulkan_max = 0;
};

// Instance, headset and Vulkan requirements. On its own thread: WiVRn's xrCreateInstance waits
// for a headset to connect, however long that takes.
std::string RunProbe(ProbeResult& p) {
  Api& xr = p.xr;
  uint32_t count = 0;
  XrResult r = xr.EnumerateInstanceExtensionProperties(nullptr, 0, &count, nullptr);
  if (XR_FAILED(r)) {
    return "no OpenXR runtime is installed or active (" + ResultName(r) +
           "): install WiVRn, Monado or SteamVR and start it before the game";
  }
  std::vector<XrExtensionProperties> extensions(count, {XR_TYPE_EXTENSION_PROPERTIES});
  xr.EnumerateInstanceExtensionProperties(nullptr, count, &count, extensions.data());
  bool vulkan2 = false, convert_time = false, refresh_rate = false;
#ifdef _WIN32
  const char* time_extension = XR_KHR_WIN32_CONVERT_PERFORMANCE_COUNTER_TIME_EXTENSION_NAME;
#else
  const char* time_extension = XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME;
#endif
  for (const auto& e : extensions) {
    vulkan2 |= std::strcmp(e.extensionName, XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME) == 0;
    convert_time |= std::strcmp(e.extensionName, time_extension) == 0;
    refresh_rate |= std::strcmp(e.extensionName, XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME) == 0;
  }
  if (!vulkan2) return "the OpenXR runtime has no XR_KHR_vulkan_enable2";
  std::vector<const char*> enabled{XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME};
  if (convert_time) enabled.push_back(time_extension);
  if (refresh_rate) enabled.push_back(XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME);
  XrInstanceCreateInfo create{XR_TYPE_INSTANCE_CREATE_INFO};
  std::strncpy(create.applicationInfo.applicationName, "Forza Horizon 1 ReImagined",
               XR_MAX_APPLICATION_NAME_SIZE - 1);
  create.applicationInfo.applicationVersion = 1;
  std::strncpy(create.applicationInfo.engineName, "ReXGlue", XR_MAX_ENGINE_NAME_SIZE - 1);
  create.applicationInfo.apiVersion = XR_API_VERSION_1_0;
  create.enabledExtensionCount = uint32_t(enabled.size());
  create.enabledExtensionNames = enabled.data();
  r = xr.CreateInstance(&create, &p.instance);
  if (XR_FAILED(r)) {
    p.instance = XR_NULL_HANDLE;
    return "the OpenXR runtime did not start (" + ResultName(r) +
           "): start its server (WiVRn, Monado, SteamVR) and connect the headset before the game";
  }
  bool ok = true;
#define FORZA_XR(name) ok &= Load(p.instance, "xr" #name, xr.name);
  FORZA_XR(DestroyInstance)
  FORZA_XR(ResultToString)
  FORZA_XR(GetInstanceProperties)
  FORZA_XR(GetSystem)
  FORZA_XR(GetSystemProperties)
  FORZA_XR(PollEvent)
  FORZA_XR(CreateSession)
  FORZA_XR(DestroySession)
  FORZA_XR(BeginSession)
  FORZA_XR(EndSession)
  FORZA_XR(RequestExitSession)
  FORZA_XR(CreateReferenceSpace)
  FORZA_XR(DestroySpace)
  FORZA_XR(LocateSpace)
  FORZA_XR(LocateViews)
  FORZA_XR(EnumerateViewConfigurationViews)
  FORZA_XR(EnumerateEnvironmentBlendModes)
  FORZA_XR(EnumerateSwapchainFormats)
  FORZA_XR(CreateSwapchain)
  FORZA_XR(DestroySwapchain)
  FORZA_XR(EnumerateSwapchainImages)
  FORZA_XR(AcquireSwapchainImage)
  FORZA_XR(WaitSwapchainImage)
  FORZA_XR(ReleaseSwapchainImage)
  FORZA_XR(WaitFrame)
  FORZA_XR(BeginFrame)
  FORZA_XR(EndFrame)
  FORZA_XR(GetVulkanGraphicsRequirements2KHR)
  FORZA_XR(CreateVulkanInstanceKHR)
  FORZA_XR(CreateVulkanDeviceKHR)
  FORZA_XR(GetVulkanGraphicsDevice2KHR)
#undef FORZA_XR
  if (!ok) return "the OpenXR runtime lacks core or Vulkan functions";
#ifdef _WIN32
  if (convert_time) Load(p.instance, "xrConvertWin32PerformanceCounterToTimeKHR", xr.ConvertTime);
#else
  if (convert_time) Load(p.instance, "xrConvertTimespecTimeToTimeKHR", xr.ConvertTime);
#endif
  if (refresh_rate &&
      !(Load(p.instance, "xrEnumerateDisplayRefreshRatesFB", xr.EnumerateDisplayRefreshRatesFB) &&
        Load(p.instance, "xrGetDisplayRefreshRateFB", xr.GetDisplayRefreshRateFB) &&
        Load(p.instance, "xrRequestDisplayRefreshRateFB", xr.RequestDisplayRefreshRateFB))) {
    xr.EnumerateDisplayRefreshRatesFB = nullptr;
    xr.GetDisplayRefreshRateFB = nullptr;
    xr.RequestDisplayRefreshRateFB = nullptr;
  }
  XrInstanceProperties properties{XR_TYPE_INSTANCE_PROPERTIES};
  xr.GetInstanceProperties(p.instance, &properties);
  p.runtime_name = properties.runtimeName;
  REXLOG_INFO("VR: OpenXR runtime {} {}.{}.{}", properties.runtimeName,
              XR_VERSION_MAJOR(properties.runtimeVersion), XR_VERSION_MINOR(properties.runtimeVersion),
              XR_VERSION_PATCH(properties.runtimeVersion));
  XrSystemGetInfo system{XR_TYPE_SYSTEM_GET_INFO};
  system.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
  r = xr.GetSystem(p.instance, &system, &p.system);
  if (XR_FAILED(r)) {
    return p.runtime_name + " has no headset (" + ResultName(r) +
           "): connect it before starting the game";
  }
  XrSystemProperties system_properties{XR_TYPE_SYSTEM_PROPERTIES};
  xr.GetSystemProperties(p.instance, p.system, &system_properties);
  p.system_name = system_properties.systemName;
  p.vendor = system_properties.vendorId;
  XrGraphicsRequirementsVulkan2KHR requirements{XR_TYPE_GRAPHICS_REQUIREMENTS_VULKAN2_KHR};
  r = xr.GetVulkanGraphicsRequirements2KHR(p.instance, p.system, &requirements);
  if (XR_FAILED(r)) return "xrGetVulkanGraphicsRequirements2KHR failed (" + ResultName(r) + ")";
  p.vulkan_min = requirements.minApiVersionSupported;
  p.vulkan_max = requirements.maxApiVersionSupported;
  return {};
}

void ProbeThread(std::shared_ptr<ProbeResult> p) {
  const auto start = std::chrono::steady_clock::now();
  std::string error = RunProbe(*p);
  std::lock_guard lock(p->mutex);
  p->error = std::move(error);
  p->done = true;
  if (p->abandoned) {
    REXLOG_WARN("VR: the OpenXR runtime answered after {:.0f} s, too late; the game stays flat",
                std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count());
    if (p->instance != XR_NULL_HANDLE && p->xr.DestroyInstance) p->xr.DestroyInstance(p->instance);
    p->instance = XR_NULL_HANDLE;
  }
  p->done_cv.notify_all();
}

}  // namespace

void Prepare() {
  if (g.prepared || g.mode != Mode::kOff) return;
  g.mode = RequestedMode();
  if (g.mode == Mode::kOff) return;
  REXLOG_INFO("VR: {} mode requested", g.mode == Mode::kCockpit ? "cockpit" : "screen");
  if (!LoadLoader()) {
    FlatBeforeHooks(std::string("no OpenXR loader (") + kLoaderName + ")");
    return;
  }
  if (!Load(XR_NULL_HANDLE, "xrEnumerateInstanceExtensionProperties",
            g.xr.EnumerateInstanceExtensionProperties) ||
      !Load(XR_NULL_HANDLE, "xrCreateInstance", g.xr.CreateInstance)) {
    FlatBeforeHooks("the OpenXR loader lacks its global functions");
    return;
  }
  auto probe = std::make_shared<ProbeResult>();
  probe->xr = g.xr;
  std::thread(ProbeThread, probe).detach();
  const int timeout = int(REXCVAR_GET(vr_runtime_timeout));
  {
    std::unique_lock lock(probe->mutex);
    if (!probe->done_cv.wait_for(lock, std::chrono::seconds(1), [&] { return probe->done; })) {
      REXLOG_INFO("VR: waiting up to {} s for the OpenXR runtime", timeout);
      probe->done_cv.wait_for(lock, std::chrono::seconds(std::max(timeout - 1, 0)),
                              [&] { return probe->done; });
    }
    if (!probe->done) {
      probe->abandoned = true;
      // The loader stays loaded: the probe's thread is still inside it.
      REXLOG_WARN("VR: the OpenXR runtime did not answer in {} s (WiVRn waits until the headset "
                  "connects: connect it before starting the game) - playing flat",
                  timeout);
      g.mode = Mode::kOff;
      return;
    }
  }
  g.xr = probe->xr;
  g.instance = probe->instance;
  g.system = probe->system;
  g.runtime_name = probe->runtime_name;
  if (!probe->error.empty()) {
    FlatBeforeHooks(probe->error);
    return;
  }
  auto set_hooks =
      reinterpret_cast<SetVulkanCreationHooksFn>(forza::SdkSymbol("rexui_SetVulkanCreationHooks"));
  if (!set_hooks || !forza::SdkSymbol("rexui_AcquireGuestOutputFrame")) {
    FlatBeforeHooks("this SDK build has no OpenXR support (ReIGlue 0390 and 0391)");
    return;
  }
  RexVulkanCreationHooks hooks{};
  hooks.size = sizeof(hooks);
  hooks.create_instance = HookCreateInstance;
  hooks.physical_device = HookPhysicalDevice;
  hooks.create_device = HookCreateDevice;
  set_hooks(&hooks);
  g.prepared = true;
  REXLOG_INFO("VR: headset {} (vendor {:04X}); Vulkan {}.{} to {}.{}; the runtime picks the GPU",
              probe->system_name, probe->vendor, XR_VERSION_MAJOR(probe->vulkan_min),
              XR_VERSION_MINOR(probe->vulkan_min), XR_VERSION_MAJOR(probe->vulkan_max),
              XR_VERSION_MINOR(probe->vulkan_max));
}

void Start(rex::system::IGraphicsSystem* graphics) {
  if (!g.prepared || g.started) return;
  rex::ui::GraphicsProvider* provider = graphics ? graphics->provider() : nullptr;
  if (!provider) return;  // not yet: called again once it exists
  g.started = true;
  if (!std::strstr(typeid(*provider).name(), "VulkanProvider")) {
    REXLOG_WARN("VR: needs the Vulkan renderer (gpu_backend=vulkan) - playing flat");
    return;
  }
  VulkanDevice* device = static_cast<rex::ui::vulkan::VulkanProvider*>(provider)->vulkan_device();
  if (g.vk_failed || !device || device->device() != g.vk_device) {
    REXLOG_WARN("VR: the Vulkan device was not created through the OpenXR runtime - playing flat");
    return;
  }
  g.set_export = reinterpret_cast<SetGuestOutputExportFn>(forza::SdkSymbol("rexui_SetGuestOutputExport"));
  g.acquire_frame =
      reinterpret_cast<AcquireGuestOutputFrameFn>(forza::SdkSymbol("rexui_AcquireGuestOutputFrame"));
  g.release_frame =
      reinterpret_cast<ReleaseGuestOutputFrameFn>(forza::SdkSymbol("rexui_ReleaseGuestOutputFrame"));
  if (!g.set_export || !g.acquire_frame || !g.release_frame) {
    REXLOG_WARN(
        "VR: this SDK build cannot hand the picture to OpenXR (ReIGlue 0391) - playing flat");
    return;
  }
  g.set_pads_without_focus =
      reinterpret_cast<SetPadsWithoutFocusFn>(forza::SdkSymbol("rexinput_SetPadsWithoutFocus"));
  if (!g.set_pads_without_focus) {
    REXLOG_WARN("VR: this SDK build drops pad input while the game window has no focus "
                "(ReIGlue 0392): click the game window before putting the headset on");
  }
  g.trace = REXCVAR_GET(vr_trace);
  g.device = device;
  // ReIGlue 0393's layers for the cockpit's HUD panel: the scene without the HUD and the HUD alone
  // (an SDK without it ignores the flag: the HUD stays in the picture).
  uint32_t flags = kExportFrames;
  if (g.mode == Mode::kCockpit && REXCVAR_GET(vr_hud)) flags |= kExportLayers;
  // ReIGlue 0394's depth stereo builds on the scene without the HUD, so it brings the HUD panel.
  g.set_stereo = reinterpret_cast<SetGuestOutputStereoFn>(forza::SdkSymbol("rexui_SetGuestOutputStereo"));
  if (g.mode == Mode::kCockpit && REXCVAR_GET(vr_stereo) == "depth") {
    if (g.set_stereo) {
      g.stereo = true;
      flags |= kExportLayers | kExportStereo;
      g.set_stereo(g.ipd, StereoMaxShift());
    } else {
      REXLOG_WARN("VR: this SDK build cannot make the eyes from the depth (ReIGlue 0394): mono");
    }
  }
  // ReIGlue 0396's true stereo also builds on the layers (the eyes come with the scene layer).
  g.set_true_stereo = reinterpret_cast<SetGuestOutputTrueStereoFn>(
      forza::SdkSymbol("rexui_SetGuestOutputTrueStereo"));
  g.set_true_stereo_eyes = reinterpret_cast<SetGuestOutputTrueStereoEyesFn>(
      forza::SdkSymbol("rexui_SetGuestOutputTrueStereoEyes"));
  if (g.mode == Mode::kCockpit && REXCVAR_GET(vr_stereo) == "true") {
    if (g.set_true_stereo) {
      g.stereo = g.true_stereo = true;
      flags |= kExportLayers;
      REXLOG_INFO("VR: true stereo: the game draws each eye from its own point");
    } else {
      REXLOG_WARN("VR: this SDK build cannot draw each eye (ReIGlue 0396): mono");
    }
  }
  // vr_frame_wait needs ReIGlue 0397's publish times (without them nothing waits).
  g.frame_wait_ns = int64_t(REXCVAR_GET(vr_frame_wait) * 1e6);
  // ReIGlue 0395: the scene layer sharpened (an SDK without it ignores this: no sharpening).
  if (g.mode == Mode::kCockpit && (flags & kExportLayers) && REXCVAR_GET(vr_sharpen) > 0) {
    using SetSharpenFn = void (*)(float);
    if (auto set_sharpen = reinterpret_cast<SetSharpenFn>(forza::SdkSymbol("rexui_SetGuestOutputSharpen"))) {
      set_sharpen(float(REXCVAR_GET(vr_sharpen)));
      REXLOG_INFO("VR: the cockpit's picture is sharpened ({:.2f}) for the headset", REXCVAR_GET(vr_sharpen));
    } else {
      REXLOG_WARN("VR: this SDK build cannot sharpen the cockpit's picture (ReIGlue 0395)");
    }
  }
  g.export_flags = flags;
  g.set_export(int(flags));
  if (const std::string key = REXCVAR_GET(vr_recenter_key); !key.empty()) {
    rex::ui::RegisterBind("bind_vr_recenter", key, "Recentre the VR view", [] { Recenter(); });
  }
  REXLOG_INFO("VR: starting the session on {} ({} to recentre, or hold both sticks)",
              device->properties().deviceName, REXCVAR_GET(vr_recenter_key));
  g.thread = std::thread(Run);
}

void Shutdown() {
  if (!g.thread.joinable()) return;
  g.stop = true;
  g.cockpit_active = false;
  // The thread requests exit when its current runtime call returns. This is an
  // app-close hook, before the SDK destroys the presenter and its Vulkan device.
  // Never detach: a runtime call may still resume and use those SDK resources.
  for (int i = 0; i < 300 && !g.finished; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(10));
  if (!g.finished) {
    REXLOG_WARN("VR: still waiting for OpenXR shutdown; a stalled runtime can prevent the game from closing safely");
  }
  g.thread.join();
  if (g.instance != XR_NULL_HANDLE) g.xr.DestroyInstance(g.instance);
  g.instance = XR_NULL_HANDLE;
}

bool CockpitActive() { return g.cockpit_active.load(std::memory_order_relaxed); }
bool SessionRunning() { return g.running.load(std::memory_order_relaxed); }

bool CockpitView(vr::HeadTransform& head, float& vertical_fov, float& aspect, uint32_t& tag) {
  if (!CockpitActive()) return false;
  FrameTag t;
  {
    std::lock_guard lock(g.pose_mutex);
    if (g.local == XR_NULL_HANDLE || !g.origin_set || g.frustum.vertical <= 0) return false;
    t.frustum = g.frustum;
    t.build_time = NowXrTime();
    t.target_time = t.build_time + g.latency_ns.load();
    Pose located;
    if (!LocateHead(t.target_time, located)) return false;
    // The camera looks along the frustum's axis: the head turned by its offset, in the game (the
    // recentred head) and in the layer (the head in LOCAL) alike, from the eye held in front of
    // the seat.
    const Pose real = Relative(g.origin, located);
    const Pose rel = HeldHead(real);
    t.head = TurnView(Compose(g.origin, rel), t.frustum);
    // A test offset turns and moves the game's camera only: the layer keeps the real head's pose,
    // so the headset shows the turned view straight ahead (as the head would see it turned).
    vr::HeadPose test;
    const Pose game = TestPoseOffset(test) ? HeldHead(AddOffset(real, FromHeadPose(test))) : rel;
    head = ToGameHead(TurnView(game, t.frustum), float(REXCVAR_GET(vr_seat_height_cm)) / 100.0f);
    t.game = head;
  }
  t.id = g.next_tag.fetch_add(1);
  if (!t.id) t.id = g.next_tag.fetch_add(1);
  {
    std::lock_guard lock(g.tags_mutex);
    g.tags.Put(t);
  }
  vertical_fov = t.frustum.vertical;
  aspect = t.frustum.aspect;
  tag = t.id;
  return true;
}

bool Tracing() { return g.trace.load(std::memory_order_relaxed); }

void NoteView(uint32_t camera_vtable, const char* camera_name, float look_angle, bool posed) {
  g.trace_camera.store(camera_vtable, std::memory_order_relaxed);
  g.trace_camera_name.store(camera_name, std::memory_order_relaxed);
  g.trace_look.store(look_angle, std::memory_order_relaxed);
  g.trace_posed.store(posed, std::memory_order_relaxed);
  g.trace_view_ns.store(SteadyNs(), std::memory_order_relaxed);
}

void RememberPosedView(uint32_t tag, const uint8_t matrix[64]) {
  std::lock_guard lock(g.tags_mutex);
  g.views.Remember(tag, matrix);
}

void OnRenderView(uint32_t camera, const uint8_t matrix[64]) {
  std::lock_guard lock(g.tags_mutex);
  g.swap_tagger.OnView(camera, g.views.Match(matrix));
}

bool RenderViewHead(vr::HeadTransform& head) {
  std::lock_guard lock(g.tags_mutex);
  const FrameTag* tag = g.tags.Find(g.swap_tagger.Current());
  if (!tag) return false;
  head = tag->game;
  return true;
}

void TagSwap(uint8_t* base, uint32_t packet) {
  if (!g.started || !g.device) return;
  uint32_t views = 0, tag;
  {
    std::lock_guard lock(g.tags_mutex);
    tag = g.swap_tagger.TakeForSwap(&views);
  }
  ++g.stat_swaps;
  if (views > 1) g.stat_extra_views += views - 1;
  if (!views && tag) ++g.stat_repeat_views;
  if (!tag || !packet) return;
  // The packet is big-endian guest memory.
  uint8_t* p = forza::GuestPtr(base, packet);
  uint32_t dwords[64];
  for (int i = 0; i < 64; ++i) {
    const uint8_t* q = p + 4 * i;
    dwords[i] = uint32_t(q[0]) << 24 | uint32_t(q[1]) << 16 | uint32_t(q[2]) << 8 | q[3];
  }
  constexpr uint32_t kSwapSignature = 'S' << 24 | 'W' << 16 | 'A' << 8 | 'P';
  if (!TagSwapPacket(dwords, kSwapSignature, tag)) {
    static std::atomic<bool> logged{false};
    if (!logged.exchange(true)) REXLOG_WARN("VR: unexpected swap packet layout; frames stay untagged");
    return;
  }
  for (size_t i : {kXeSwapHeaderIndex, kXeSwapTagIndex}) {
    uint8_t* q = p + 4 * i;
    q[0] = uint8_t(dwords[i] >> 24), q[1] = uint8_t(dwords[i] >> 16);
    q[2] = uint8_t(dwords[i] >> 8), q[3] = uint8_t(dwords[i]);
  }
  ++g.stat_tagged_swaps;
}

void Recenter() {
  if (g.started && g.device) {
    g.recenter = true;
    REXLOG_INFO("VR: recentre requested");
  }
}

void OnPad(uint16_t buttons) {
  if (!g.started || !g.device) return;
  constexpr uint16_t kSticks = 0x0040 | 0x0080;  // left and right thumbstick presses
  if ((buttons & kSticks) != kSticks) {
    pad_held_since = 0;
    pad_fired = false;
    return;
  }
  const int64_t now = SteadyNs();
  if (!pad_held_since) pad_held_since = now;
  if (!pad_fired && now - pad_held_since >= 1'000'000'000) {
    pad_fired = true;
    Recenter();
  }
}

}  // namespace forza::xr
