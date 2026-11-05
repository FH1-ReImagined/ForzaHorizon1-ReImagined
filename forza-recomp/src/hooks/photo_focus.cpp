// forza - photo mode's FOCUS reads the world depth target on the CPU (#665, docs/rendering.md).
// tu_address_map: TU4 addresses
//
// FOCUS (X in photo mode's camera) picks what the photo holds still: the object under the
// reticle stays sharp and the rest smears with the shutter speed. The game reads the world depth
// target on the CPU for it, as X is handled ("WorldDepth" in the renderer's render target table,
// 0x833D4728: 1280x720, 3,768,320 bytes at physical 0x1DAC5000), and it resolves that target
// every frame. readback_resolve = some (the default) copies the resolve back to guest memory only
// when its key is new, so FOCUS read the depth of an old frame: on the car it kept the scenery
// sharp, and once focused elsewhere it never came back to the car (any GPU vendor).
//
// While photo mode's camera screen is up (sub_827DBE70 every frame, its state +0x748 is 0;
// 1 in Effects Mode, 3 from the shutter while the photo is shown), the world depth
// target is in readback_resolve_wait_range: its resolves wait for the GPU and are copied back,
// as with readback_resolve = full. Turning it on as X is pressed is too late (the depth is read
// from what is already in guest memory), so it stays on in the camera. It is released in other
// states and 250 ms after the screen's last frame. FORZA_PHOTO_FOCUS_READBACK=0 switches it off.
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/online_photo.h"
#include "../platform/readback_wait.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

#if FORZA_TU4
#define CameraScreen sub_827DBE70
#define OriginalCameraScreen __imp__sub_827DBE70
constexpr uint32_t kRenderTargets = 0x833D4728;
#else
#define CameraScreen sub_8274BF00
#define OriginalCameraScreen __imp__sub_8274BF00
constexpr uint32_t kRenderTargets = 0x832E6B18;
#endif

namespace {

using Clock = std::chrono::steady_clock;
constexpr auto kGone = std::chrono::milliseconds(250);  // no camera screen frame for this long
constexpr uint32_t kTableBytes = 0xA28;  // the render target entries, up to the front buffers

uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
}

bool Enabled() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_PHOTO_FOCUS_READBACK");
    return !(v && std::strcmp(v, "0") == 0);
  }();
  return on;
}

// The render target table's "WorldDepth" entry: its name, then its size and address.
uint64_t WorldDepthRange(uint8_t* base) {
  static constexpr char kName[] = "WorldDepth";
  for (uint32_t at = kRenderTargets; at < kRenderTargets + kTableBytes; at += 4) {
    const uint32_t name = Load32(base, at);
    if (name < 0x82000000 || name >= 0x83000000) continue;  // the image's strings
    if (std::memcmp(forza::GuestPtr(base, name), kName, sizeof(kName)) != 0) continue;
    const uint32_t size = Load32(base, at + 4), address = Load32(base, at + 8) & 0x1FFFFFFF;
    if (!address || size < 0x100000 || size > 0x1000000) return 0;
    return forza::readback_wait::Pack(address, size);
  }
  return 0;
}

std::mutex g_mutex;  // the state below
bool g_held = false;
uint64_t g_generation = 0;
Clock::time_point g_last_frame;

void Release(const std::string& why) {  // g_mutex held
  if (!g_held) return;
  forza::readback_wait::Request(forza::readback_wait::Owner::kPhotoFocus, 0);
  g_held = false;
  ++g_generation;
  REXLOG_INFO("Photo focus: world depth readback back to normal ({})", why);
}

void Hold(uint8_t* base) {  // g_mutex held
  g_last_frame = Clock::now();
  if (g_held) return;
  static uint64_t range = 0;
  static bool warned = false;
  if (!range) range = WorldDepthRange(base);
  if (!range) {
    if (!warned) REXLOG_WARN("Photo focus: no WorldDepth render target; FOCUS may pick the wrong object");
    warned = true;
    return;
  }
  if (!forza::readback_wait::Request(forza::readback_wait::Owner::kPhotoFocus, range)) {
    if (!warned) REXLOG_WARN("Photo focus: the SDK has no readback_resolve_wait_range; FOCUS may pick the wrong object");
    warned = true;
    return;
  }
  g_held = true;
  const uint64_t generation = ++g_generation;
  REXLOG_INFO("Photo focus: world depth ({:08X}, {} bytes) copied back while the camera is up",
              uint32_t(range >> 32), uint32_t(range));
  // Leaving photo mode ends the screen's frames without a call here to say so.
  std::thread([generation] {
    for (;;) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      std::lock_guard lock(g_mutex);
      if (!g_held || g_generation != generation) return;
      if (Clock::now() - g_last_frame > kGone) {
        Release("photo mode's camera closed");
        return;
      }
    }
  }).detach();
}

}  // namespace

// Photo mode's camera screen, every frame. Its state (+0x748) is 0 in the camera, 1 in Effects
// Mode and 3 from the shutter while the photo is shown (PREPARING PHOTO, SAVE, a Big Shot).
extern "C" REX_FUNC(OriginalCameraScreen);
extern "C" REX_FUNC(CameraScreen) {
  if (ctx.r3.u32) {
    const uint32_t state = Load32(base, ctx.r3.u32 + 0x748);
    forza::online_photo::OnCameraScreen(state);
    if (Enabled()) {
      std::lock_guard lock(g_mutex);
      if (state == 0) {
        Hold(base);
      } else if (g_held) {
        Release("camera screen state " + std::to_string(state));
      }
    }
  }
  OriginalCameraScreen(ctx, base);
}
