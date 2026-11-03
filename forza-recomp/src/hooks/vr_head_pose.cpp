// forza - the head pose on the in-car camera (#333, docs/vr.md): from the headset in the VR
// cockpit mode (src/platform/xr.h), or a test pose without a headset (vr_head_pose_test). Off
// unless one of them is on; normal play never reaches the code below that check.
//
// The camera system: each split-screen view (CSplitView, manager + 244 + 172 * n; the
// manager is *(*(*0x832DF024 + 4) + 4)) points at +8 to its player's camera director. The
// director holds the active camera at +20 (CCamDriver is the in-car view with the driver's
// hands, CCamCockpit is not used by the player), the camera's world position at +176, and
// the view matrix the game derived for this frame at +240 (row-vector, world to camera)
// with the vertical FOV at +304. Two functions read that matrix through its accessor
// sub_8284EF88, and nothing else calls the accessor:
//   sub_825A9D20 (view, ...)  hands the matrix, FOV, aspect and clip planes to the view's
//                             presentation camera (CSplitView + 40, a deferred
//                             IPresentationCamera: SetViewTransform is vtable +168) and the
//                             same set again to sub_82DBADC0 (the render-side view state);
//   sub_829A4860 (?, out)     builds view 0's frustum for something game-side
//                             (sub_823EAED0; posing it or not changes nothing visible).
// Both run on the main loop thread right after the director update (sub_82858288), from
// CSplitView::Update (sub_825AA1B0) and the world update (sub_823E3218).
// Both hooks put the posed matrix in place, run the original and put the game's matrix back
// (unless the game wrote a new one meanwhile), so the camera's own state never carries the
// pose and nothing drifts. The director's position/forward/up (+176/+208/+224) are left
// alone: whatever reads them sees the car's camera.
//
// Pose source (yaw pitch roll in degrees, x y z in metres in camera space: x right, y up,
// z forward; positive yaw looks left, pitch up, roll tilts the head left):
//   FORZA_VR_POSE="yaw,pitch,roll,x,y,z"   a fixed pose from the start (missing values 0)
//   FORZA_VR_POSE_FIFO=<path>             lines "yaw pitch roll x y z" (spaces or commas)
//                                          replace the pose as they arrive (Linux; mkfifo)
//   FORZA_VR_POSE_ALL=1                    every camera, not only the in-car ones
//   FORZA_VR_POSE_SKIP=view|frustum        leave one of the two readers unposed (to see
//                                          what each one feeds); a FIFO line "skip view",
//                                          "skip frustum" or "skip none" changes it
//   FORZA_VR_POSE_TRACE=1                  log the calling thread every 300th call
//   a FIFO line "exposure X"               the VR cockpit's exposure speed from then on (a test aid)
//   a FIFO line "cull 0" or "cull 1"       the VR cockpit's size cull scale off or on (a test aid)
//   a FIFO line "stereo 0" or "stereo 1"   VR true stereo off (one picture) or as set (a test aid)
//   a FIFO line "framewait 0" or "... 1"   vr_frame_wait off or as set (a test aid)
//   a FIFO line "recenter"                 recentre the VR view, as the Home key (a test aid: a headset
//                                          lying still can relocalise after its display wakes)
//   a FIFO line "parallax 0|1|2"           vr_cockpit_parallax off, on, or on with the game's own eye
//                                          position for the shading (a test aid; vr_cockpit_space.cpp)
//
// In the VR cockpit, vr_head_pose_test's pose (env or FIFO) is added to the headset's for the game's
// camera only (xr.cpp CockpitView): a scripted look-around through the real runtime with the
// headset still. FIFO test commands there: "cull 0|1" (vr_cull_scale), "exposure X"
// (vr_exposure_speed).
//
// VR cockpit mode: the view hook asks the OpenXR thread for the predicted head pose and the
// field of view to draw with (the headset's plus a margin, written into the director's FOV at
// +304 for both readers like the matrix) and remembers the posed matrix with a frame tag; the
// render-side camera's SetViewTransform (sub_823EB7A0, CPresentationCamera: the matrix lands at
// +16) finds the tag again on the render thread, and VdSwap puts it into the swap packet
// (debug/frame_dump.cpp) so the OpenXR thread knows which pose each picture was drawn with.
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/camera_fov.h"
#include "../platform/minimap_heading.h"
#include "../platform/vr_head_pose_math.h"
#include "../platform/world_detail.h"
#include "../platform/xr.h"
#include "../platform/xr_math.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
REXCVAR_DECLARE(bool, forza_minimap_camera);

#ifndef _WIN32
#include <fcntl.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

REXCVAR_DEFINE_BOOL(vr_head_pose_test, false, "VR",
                    "Experimental: apply a test head pose (FORZA_VR_POSE / FORZA_VR_POSE_FIFO) "
                    "to the in-car camera");
REXCVAR_DEFINE_DOUBLE(vr_near_plane_cm, 2.0, "VR",
                      "Cockpit: the near clipping plane of the head-tracked view, cm (the in-car "
                      "camera's own is 10 cm: a head moved toward the door or the roof cut them "
                      "open, which looked like another camera)")
    .range(0.5, 10);
// The eye adaptation measures the whole picture: with the cockpit's 123 x 109 degree view a head
// turn toward a bright window or the dark footwell re-exposed the whole cabin within a fraction of
// a second at 72 fps (docs/vr.md "Version 4"). 0.02: fifty times slower than the track's rate.
REXCVAR_DEFINE_DOUBLE(vr_exposure_speed, 0.02, "VR",
                      "Cockpit: how fast the exposure adapts while the head pose drives the camera, "
                      "as a factor of the game's own rate (1: the game's; smaller: looking at a "
                      "window or the footwell changes the cabin's brightness less)")
    .range(0.001, 1);
REXCVAR_DECLARE(bool, vr_cull_scale);  // src/hooks/vr_culling.cpp
REXCVAR_DEFINE_BOOL(vr_camera_effects, false, "VR",
                    "Cockpit: keep the game's camera physics (shake at speed, G-force lean and "
                    "tilt, the speed field of view) on top of the head pose");

namespace {
using forza::vr::HeadPose;

constexpr uint32_t kViewMatrix = 240;  // director: world-to-camera matrix, 4x4 floats
constexpr uint32_t kFov = 304;  // director: vertical field of view, radians
constexpr uint32_t kActiveCamera = 20;
constexpr uint32_t kRenderCameraMatrix = 16;  // CPresentationCamera: the view matrix
constexpr uint32_t kLookAngle = 280;  // in-car cameras: the right stick's look-around yaw, radians

// The VR cockpit's pose for the frame being built: set by the view hook, reused by the frustum
// hook of the same frame (both on the main loop thread).
struct XrView {
  forza::vr::HeadTransform head;
  float fov = 0;
  float aspect = 0;
  bool valid = false;
};
XrView xr_view;

// The projection aspect of the VR cockpit while a posed view is handed over on this thread (0:
// none): the viewport query (hooks/ultrawide_projection.cpp) answers with it inside the two
// readers, so the presentation camera's projection and the game-side frustum both get it.
thread_local float posed_aspect = 0;
// The near plane (metres) of the VR cockpit while a posed view is handed over on this thread (0:
// the camera's own): the hook on the director's near-plane accessor answers with it.
thread_local float posed_near = 0;
// The size-cull factor for the frames being built (vr_culling.cpp): the in-car camera's own
// vertical field of view (+304) over the posed cockpit view's, as tan^2 of the half-angles.
std::atomic<float> cull_scale{1.0f};
// The view/frustum hooks share the main-loop thread. Remember a modified cockpit view so a
// menu or lost session restores the track's own values even if no new valid view is built.
bool cockpit_adjusted = false;

std::mutex pose_mutex;
HeadPose pose;
std::atomic<bool> pose_set{false};
std::atomic<int> skip_hook{0};  // 0 none, 1 view, 2 frustum
std::atomic<float> exposure_speed_test{0};  // a FIFO's "exposure X" (0: vr_exposure_speed)
std::atomic<int> cull_test{0};  // a FIFO's "cull 1" (1) or "cull 0" (2); 0: vr_cull_scale
std::atomic<bool> stereo_off_test{false};  // a FIFO's "stereo 0" (until "stereo 1")
std::atomic<bool> frame_wait_off_test{false};  // a FIFO's "framewait 0" (until "framewait 1")
int SkipCode(const char* name) {
  return !name ? 0 : std::strcmp(name, "view") == 0 ? 1 : std::strcmp(name, "frustum") == 0 ? 2 : 0;
}

uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
bool Valid(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }

bool Parse(const char* text, HeadPose& out) {
  float v[6] = {};
  int n = 0;
  const char* p = text;
  while (n < 6 && *p) {
    char* end = nullptr;
    const float f = std::strtof(p, &end);
    if (end == p) {
      ++p;  // separator
      continue;
    }
    v[n++] = f;
    p = end;
  }
  if (!n) return false;
  out = HeadPose{v[0], v[1], v[2], v[3], v[4], v[5]};
  return true;
}

void SetPose(const HeadPose& p, const char* from) {
  {
    std::lock_guard lock(pose_mutex);
    pose = p;
  }
  pose_set = !p.IsIdentity();
  REXLOG_INFO("VR head pose ({}): yaw {:.1f} pitch {:.1f} roll {:.1f}, offset {:.3f} {:.3f} {:.3f} m",
              from, p.yaw, p.pitch, p.roll, p.x, p.y, p.z);
}

void ReadFifo(std::string path) {
#ifdef _WIN32
  REXLOG_WARN("FORZA_VR_POSE_FIFO is not supported on Windows ({})", path);
#else
  const int fd = open(path.c_str(), O_RDWR);  // O_RDWR: no EOF when a writer goes away
  if (fd < 0) {
    REXLOG_WARN("FORZA_VR_POSE_FIFO: cannot open {}", path);
    return;
  }
  std::string line;
  char c;
  while (read(fd, &c, 1) == 1) {
    if (c != '\n') {
      line += c;
      continue;
    }
    HeadPose p;
    if (line.rfind("cull ", 0) == 0) {  // test aid: "cull 0" / "cull 1" overrides vr_cull_scale
      cull_test = std::atoi(line.c_str() + 5) ? 1 : 2;
      REXLOG_INFO("VR head pose: cull scale {}", cull_test.load() == 1 ? "on" : "off");
    } else if (line.rfind("stereo ", 0) == 0) {  // test aid: VR true stereo off / back on
      stereo_off_test = std::atoi(line.c_str() + 7) == 0;
      REXLOG_INFO("VR head pose: stereo {}", stereo_off_test.load() ? "off (test)" : "as set");
    } else if (line.rfind("framewait ", 0) == 0) {  // test aid: vr_frame_wait off / back on
      frame_wait_off_test = std::atoi(line.c_str() + 10) == 0;
      REXLOG_INFO("VR head pose: frame wait {}", frame_wait_off_test.load() ? "off (test)" : "as set");
    } else if (line.rfind("parallax ", 0) == 0) {  // test aid: vr_cockpit_parallax off / on
      forza::xr::SetCockpitParallaxTest(std::atoi(line.c_str() + 9));
      REXLOG_INFO("VR head pose: cockpit parallax {}", std::atoi(line.c_str() + 9));
    } else if (line == "recenter") {  // test aid: as the recentre key
      forza::xr::Recenter();
      REXLOG_INFO("VR head pose: recentre");
    } else if (line.rfind("exposure ", 0) == 0) {  // test aid: vr_exposure_speed from now on
      exposure_speed_test = std::strtof(line.c_str() + 9, nullptr);
      REXLOG_INFO("VR head pose: exposure speed {}", exposure_speed_test.load());
    } else if (line.rfind("skip ", 0) == 0) {
      skip_hook = SkipCode(line.c_str() + 5);
      REXLOG_INFO("VR head pose: skip {}", line.c_str() + 5);
    } else if (Parse(line.c_str(), p)) {
      SetPose(p, "fifo");
    }
    line.clear();
  }
#endif
}

void InitOnce() {
  static std::once_flag once;
  std::call_once(once, [] {
    HeadPose p;
    if (const char* text = std::getenv("FORZA_VR_POSE"); text && Parse(text, p)) SetPose(p, "env");
    skip_hook = SkipCode(std::getenv("FORZA_VR_POSE_SKIP"));
    if (const char* fifo = std::getenv("FORZA_VR_POSE_FIFO"); fifo && *fifo)
      std::thread(ReadFifo, std::string(fifo)).detach();
    REXLOG_INFO("VR head pose test on: {}", std::getenv("FORZA_VR_POSE_ALL") ? "every camera"
                                                                             : "in-car cameras");
  });
}

bool InCarVtable(uint32_t vtable) {
  return vtable == FORZA_ADDR(0x82075DD4)     // Camera::CCamDriver (the cockpit view)
         || vtable == FORZA_ADDR(0x82075CD4);  // Camera::CCamCockpit
}

bool InCar(uint8_t* base, uint32_t camera, bool xr) {
  static const bool all = std::getenv("FORZA_VR_POSE_ALL") != nullptr;
  if (all && !xr) return true;
  return InCarVtable(Load32(base, camera));
}

// The camera classes a player meets (vtables from their RTTI), for vr_trace.
const char* CameraName(uint32_t vtable) {
  struct Known {
    uint32_t vtable;
    const char* name;
  };
  static const Known known[] = {
      {FORZA_ADDR(0x82075DD4), "CCamDriver"},     {FORZA_ADDR(0x82075CD4), "CCamCockpit"},
      {FORZA_ADDR(0x82075D54), "CCamHood"},       {FORZA_ADDR(0x82075E54), "CCamBumperHigh"},
      {FORZA_ADDR(0x820755AC), "CCamFollowLow"},  {FORZA_ADDR(0x8207562C), "CCamFollowHigh"},
      {FORZA_ADDR(0x8207552C), "CCamFollow"},     {FORZA_ADDR(0x82075FD4), "CCamRearView"},
      {FORZA_ADDR(0x82075ED4), "CCamWheel"},      {FORZA_ADDR(0x82075F54), "CCamSide"},
      {FORZA_ADDR(0x82075444), "CBlendCamera"},
  };
  for (const Known& k : known)
    if (k.vtable == vtable) return k.name;
  return "?";
}

float LoadFloat(uint8_t* base, uint32_t address) {
  const uint32_t bits = Load32(base, address);
  float f;
  std::memcpy(&f, &bits, 4);
  return f;
}

// Whether the head pose replaces the game's camera physics on `camera` this frame: the VR
// cockpit (unless vr_camera_effects), or the test pose while it is set.
bool PoseOwnsCamera(uint8_t* base, uint32_t camera) {
  if (!Valid(camera)) return false;
  if (forza::xr::CockpitActive()) return !REXCVAR_GET(vr_camera_effects) && InCar(base, camera, true);
  return REXCVAR_GET(vr_head_pose_test) && pose_set && InCar(base, camera, false);
}

// FORZA_VR_POSE_TRACE=1: which thread calls each hook, every 300th call.
void Trace(const char* hook, uint32_t director) {
  static const bool on = std::getenv("FORZA_VR_POSE_TRACE") != nullptr;
  if (!on) return;
  static std::atomic<uint32_t> calls{0};
  if (calls++ % 300) return;
#ifdef _WIN32
  const long tid = 0;
#else
  const long tid = long(syscall(SYS_gettid));
#endif
  REXLOG_INFO("VR head pose trace: {} director {:08X} thread {}", hook, director, tid);
}

void PutFloat(uint8_t* q, float f) {
  uint32_t bits;
  std::memcpy(&bits, &f, 4);
  q[0] = uint8_t(bits >> 24), q[1] = uint8_t(bits >> 16), q[2] = uint8_t(bits >> 8), q[3] = uint8_t(bits);
}

// Swaps the posed view matrix (and in the VR cockpit the field of view and the projection
// aspect) into the director for the length of one guest call.
class PosedView {
 public:
  PosedView(uint8_t* base, uint32_t director, const char* hook) {
    const bool xr = forza::xr::CockpitActive();
    const bool view_hook = std::strcmp(hook, "view") == 0;
    const bool trace = view_hook && forza::xr::Tracing();
    if (!xr && cockpit_adjusted) {
      forza::world_detail::ScaleExposureAdaptation(base, 1.0f);
      cull_scale = 1.0f;
      xr_view.valid = false;
      cockpit_adjusted = false;
    }
    if (!xr && !trace && !REXCVAR_GET(vr_head_pose_test)) return;
    uint32_t camera = 0;
    const bool posed = Pose(base, director, hook, xr, view_hook, camera);
    // The VR cockpit's slower eye adaptation, while the head pose drives the camera; the track's
    // own rate otherwise (other cameras, menus).
    if (xr && view_hook && Valid(director)) {
      const float test_speed = exposure_speed_test.load(std::memory_order_relaxed);
      const float speed = test_speed > 0 ? test_speed : float(REXCVAR_GET(vr_exposure_speed));
      forza::world_detail::ScaleExposureAdaptation(base, posed ? speed : 1.0f);
      cockpit_adjusted = posed;
      if (!posed) cull_scale = 1.0f;
    }
    if (trace && Valid(director)) {  // view 1 of single player has no director
      const uint32_t vtable = Valid(camera) ? Load32(base, camera) : 0;
      forza::xr::NoteView(vtable, Valid(camera) ? CameraName(vtable) : "none",
                          InCarVtable(vtable) ? LoadFloat(base, camera + kLookAngle) : 0.0f, posed);
    }
  }
  ~PosedView() {
    // The game may have computed the next view meanwhile (another thread): keep that one.
    if (matrix_ && std::memcmp(matrix_, posed_, sizeof(posed_)) == 0)
      std::memcpy(matrix_, original_, sizeof(original_));
    if (fov_ && std::memcmp(fov_, posed_fov_, 4) == 0) std::memcpy(fov_, original_fov_, 4);
    if (matrix_) {
      posed_aspect = previous_aspect_;
      posed_near = previous_near_;
    }
  }
  PosedView(const PosedView&) = delete;
  PosedView& operator=(const PosedView&) = delete;

 private:
  bool Pose(uint8_t* base, uint32_t director, const char* hook, bool xr, bool view_hook, uint32_t& camera) {
    if (xr && view_hook) xr_view.valid = false;
    forza::vr::HeadTransform head;
    float fov = 0, aspect = 0;
    uint32_t tag = 0;
    if (!Valid(director)) return false;
    camera = Load32(base, director + kActiveCamera);
    if (!xr) {
      if (!REXCVAR_GET(vr_head_pose_test)) return false;
      InitOnce();
      Trace(hook, director);
      if (skip_hook && skip_hook == SkipCode(hook)) return false;
      if (!pose_set) return false;
    }
    if (!Valid(camera) || !InCar(base, camera, xr)) return false;
    if (xr) {
      if (view_hook) {
        if (!forza::xr::CockpitView(head, fov, aspect, tag)) return false;
        xr_view = {head, fov, aspect, true};
      } else {
        if (!xr_view.valid) return false;
        head = xr_view.head;
        fov = xr_view.fov;
        aspect = xr_view.aspect;
      }
    } else {
      std::lock_guard lock(pose_mutex);
      head = forza::vr::ToTransform(pose);
    }
    matrix_ = forza::GuestPtr(base, director + kViewMatrix);
    std::memcpy(original_, matrix_, sizeof(original_));
    float view[16];
    for (int i = 0; i < 16; ++i) {
      const uint8_t* q = original_ + 4 * i;
      const uint32_t bits = uint32_t(q[0]) << 24 | uint32_t(q[1]) << 16 | uint32_t(q[2]) << 8 | q[3];
      std::memcpy(&view[i], &bits, 4);
    }
    forza::vr::ApplyHeadTransform(view, head);
    for (int i = 0; i < 16; ++i) PutFloat(posed_ + 4 * i, view[i]);
    if (tag) forza::xr::RememberPosedView(tag, posed_);
    std::memcpy(matrix_, posed_, sizeof(posed_));
    if (fov > 0) {
      fov_ = forza::GuestPtr(base, director + kFov);
      std::memcpy(original_fov_, fov_, 4);
      if (view_hook && aspect > 0) {
        // The cull record's k is 3 tan^2 of the vertical half-angle (measured: 5.896 at the
        // cockpit's 109 degrees, 3 for the cube map's 90), so the in-car camera's own k is this
        // fraction of the cockpit's.
        const float own = LoadFloat(base, director + kFov);
        if (own > 0.1f && own < 3.0f) cull_scale = forza::xr::CullScale(own, fov);
      }
      PutFloat(posed_fov_, fov);
      std::memcpy(fov_, posed_fov_, 4);
    }
    previous_aspect_ = posed_aspect;
    if (aspect > 0) posed_aspect = aspect;
    previous_near_ = posed_near;
    if (xr) posed_near = float(REXCVAR_GET(vr_near_plane_cm)) / 100.0f;
    static std::atomic<bool> logged{false};
    if (!logged.exchange(true))
      REXLOG_INFO("VR head pose: first posed view (director {:08X}, camera {:08X}, {})", director,
                  camera, xr ? "headset" : "test pose");
    return true;
  }

  uint8_t* matrix_ = nullptr;
  uint8_t original_[64];
  uint8_t posed_[64];
  uint8_t* fov_ = nullptr;
  uint8_t original_fov_[4];
  uint8_t posed_fov_[4];
  float previous_aspect_ = 0;
  float previous_near_ = 0;
};

// View 0's director, found as sub_829A4860 finds it.
uint32_t FirstViewDirector(uint8_t* base) {
  const uint32_t root = Load32(base, FORZA_ADDR(0x832DF024));
  if (!Valid(root)) return 0;
  const uint32_t holder = Load32(base, root + 4);
  if (!Valid(holder)) return 0;
  const uint32_t manager = Load32(base, holder + 4);
  if (!Valid(manager)) return 0;
  return Load32(base, manager + 244 + 8);
}

}  // namespace

namespace forza::xr {
float PosedViewAspect() { return posed_aspect; }
float CullScale() {
  const int test = cull_test.load(std::memory_order_relaxed);
  const bool on = test ? test == 1 : REXCVAR_GET(vr_cull_scale);
  return on ? cull_scale.load(std::memory_order_relaxed) : 1.0f;
}

bool StereoOffTest() { return stereo_off_test.load(std::memory_order_relaxed); }
bool FrameWaitOffTest() { return frame_wait_off_test.load(std::memory_order_relaxed); }

bool TestPoseOffset(HeadPose& offset) {
  if (!REXCVAR_GET(vr_head_pose_test)) return false;
  InitOnce();
  if (!pose_set) return false;
  std::lock_guard lock(pose_mutex);
  offset = pose;
  return true;
}
}  // namespace forza::xr

// The camera physics step: sub_82853DD0 (camera, view matrix in/out, FOV in/out, position out),
// called once a frame by the director's step sub_82855070 after the active camera built its own
// view (CCamDriver: the driver's eye on the car body, looking along the car turned by the
// look-around angle) and its field of view. It applies what the camera's CameraPhysics.xml
// layers computed this frame (camera + 48 ... + 112): car-space and camera-space yaw/pitch/roll
// offsets and impulses (G-force lean and tilt toward the corner, collision jolts, surface bumps,
// a 1.1 degree constant pitch), car- and camera-space position offsets (head bob, the noise
// layers that shake the view at speed) and a field-of-view offset (8 degrees wider at speed),
// then rebuilds the view matrix from them. Skipped while the head pose owns the camera: the
// view stays rigid with the car and only the head moves it, the field of view stays the
// camera's own (which the VR cockpit replaces anyway), and the layers keep running (their
// springs carry on, so switching back is smooth).
extern "C" REX_FUNC(__imp__sub_82853DD0);
extern "C" REX_FUNC(sub_82853DD0) {
  if (PoseOwnsCamera(base, ctx.r3.u32)) {
    static std::atomic<bool> logged{false};
    if (!logged.exchange(true)) REXLOG_INFO("VR: the in-car camera's camera physics are off while the head pose drives it");
    return;
  }
  __imp__sub_82853DD0(ctx, base);
}

// The director's near-plane accessor (director: the active camera's virtual function +32), which
// both readers of the view matrix call for the clip planes (sub_825A9D20's SetZClip and the
// render-side view state, the frustum). Inside a posed VR cockpit view it answers with
// vr_near_plane_cm when that is nearer: the in-car camera's 10 cm cut the roof and the door open
// when the head moved toward them (turned 70-90 degrees toward the driver's door the head moves
// 8-10 cm sideways and back, docs/vr.md "Version 3").
extern "C" REX_FUNC(__imp__sub_8284EF98);
extern "C" REX_FUNC(sub_8284EF98) {
  __imp__sub_8284EF98(ctx, base);
  if (posed_near > 0 && ctx.f1.f64 > posed_near) {
    static std::atomic<bool> logged{false};
    if (!logged.exchange(true))
      REXLOG_INFO("VR: the in-car view's near plane is {:.3f} m instead of the camera's {:.3f} m",
                  posed_near, ctx.f1.f64);
    ctx.f1.f64 = posed_near;
  }
}

// The view's presentation camera update (view = CSplitView: +8 director, +40 camera).
extern "C" REX_FUNC(__imp__sub_825A9D20);
extern "C" REX_FUNC(sub_825A9D20) {
  const uint32_t view = ctx.r3.u32;
  PosedView posed(base, Valid(view) ? Load32(base, view + 8) : 0, "view");
  // Capture the exact submitted gameplay view, including native look-around
  // and blends, on its owning thread. Never read that matrix from render.
  if (REXCVAR_GET(forza_minimap_camera) && !REXCVAR_GET(vr_head_pose_test) &&
      !forza::xr::CockpitActive() && Valid(view)) {
    const auto director = Load32(base, view + 8);
    if (Valid(director) && director == FirstViewDirector(base)) {
      std::array<float, 16> matrix;
      for (size_t i = 0; i < matrix.size(); ++i)
        matrix[i] = LoadFloat(base, director + kViewMatrix + uint32_t(i * 4));
      forza::minimap::heading.Publish(matrix,
          forza::camera::camera_world.load(std::memory_order_relaxed), forza::minimap::NowMs());
    }
  }
  __imp__sub_825A9D20(ctx, base);
}

// View 0's frustum for the game-side visibility.
extern "C" REX_FUNC(__imp__sub_829A4860);
extern "C" REX_FUNC(sub_829A4860) {
  const bool posing = REXCVAR_GET(vr_head_pose_test) || forza::xr::CockpitActive();
  PosedView posed(base, posing ? FirstViewDirector(base) : 0, "frustum");
  __imp__sub_829A4860(ctx, base);
}

// CPresentationCamera::SetViewTransform(camera, matrix by value) on the render thread: the
// matrix is stored at camera + 16. In the VR cockpit, a posed matrix names the frame's tag.
extern "C" REX_FUNC(__imp__sub_823EB7A0);
extern "C" REX_FUNC(sub_823EB7A0) {
  const uint32_t camera = ctx.r3.u32;
  __imp__sub_823EB7A0(ctx, base);
  if (forza::xr::CockpitActive() && Valid(camera))
    forza::xr::OnRenderView(camera, forza::GuestPtr(base, camera + kRenderCameraMatrix));
}
