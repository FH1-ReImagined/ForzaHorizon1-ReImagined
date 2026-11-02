// forza - photo mode's Big Shot reads each tile back from a front buffer (#552,
// docs/rendering.md).
// tu_address_map: TU4 addresses
//
// BIG SHOT : 8.3 MEGAPIXEL renders the photo as 3x3 tiles of 1280x720, each over 96 frames.
// BigShotStart (called from the renderer after the confirmation) sets up the tile state, the
// object of sub_8248B278 + 148: its 3840x2160 buffer at +352, and +88 while the tiles go to
// that buffer. On the last frame of each tile the game locks that frame's front buffer (one of
// the renderer's two D3D textures, kFrontBuffers) and copies it into the buffer on the CPU
// (sub_825F3CE8, called from sub_825F4418). The BMP made of the nine tiles is what "upload to
// ForzaMotorsport.net" sends. BigShotEnd resets the state after the ninth tile.
//
// readback_resolve = some (the default) copies a resolve back to guest memory only when its
// key is new. The game resolves both front buffers every frame, so they were copied back once,
// during the first loading screen, and all nine tiles were that loading screen. While a Big
// Shot runs, the SDK's readback_resolve_wait_range (ReIGlue 0411) holds the two front buffers:
// their resolves wait for the GPU and are copied back, as with readback_resolve = full. Other
// resolves and other frames are not affected. FORZA_BIGSHOT_READBACK=0 switches it off.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

#if FORZA_TU4
#define BigShotStart sub_82614850
#define OriginalBigShotStart __imp__sub_82614850
#define BigShotEnd sub_82CF45C8
#define OriginalBigShotEnd __imp__sub_82CF45C8
constexpr uint32_t kTileState = 0x83539F20 + 148;
constexpr uint32_t kFrontBuffers = 0x833D4728 + 2600;
#else
#define BigShotStart sub_8259D848
#define OriginalBigShotStart __imp__sub_8259D848
#define BigShotEnd sub_82C33CA0
#define OriginalBigShotEnd __imp__sub_82C33CA0
constexpr uint32_t kTileState = 0x834448E0 + 148;
constexpr uint32_t kFrontBuffers = 0x832E6B18 + 2600;
#endif

namespace {

constexpr uint32_t kTextureSize = 52;  // a D3DTexture: 7 words, then its fetch constant
// The range is cleared if the end is never seen (nine tiles of 96 frames take 15 s at 60 fps).
constexpr auto kLimit = std::chrono::minutes(10);

uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
}

bool Enabled() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_BIGSHOT_READBACK");
    return !(v && std::strcmp(v, "0") == 0);
  }();
  return on;
}

std::mutex g_mutex;
uint64_t g_generation = 0;  // Protected by g_mutex, as is the setting.
bool g_set = false;

bool SetRange(uint64_t range) {
  return rex::cvar::SetFlagByName("readback_resolve_wait_range", std::to_string(range));
}

// The physical range of both front buffers (each from its fetch constant: base address in
// word 1, 32 bits a pixel, width and height in word 2), as one range.
uint64_t FrontBufferRange(uint8_t* base) {
  uint32_t start = UINT32_MAX, end = 0;
  for (uint32_t i = 0; i < 2; ++i) {
    const uint32_t fetch = kFrontBuffers + i * kTextureSize + 28;
    const uint32_t address = Load32(base, fetch + 4) & 0x1FFFF000;
    const uint32_t size = Load32(base, fetch + 8);
    const uint32_t width = (size & 0x1FFF) + 1, height = ((size >> 13) & 0x1FFF) + 1;
    if (!address) return 0;
    start = std::min(start, address);
    end = std::max(end, address + ((width + 31) & ~31u) * ((height + 31) & ~31u) * 4);
  }
  return uint64_t(start) << 32 | (end - start);
}

}  // namespace

extern "C" REX_FUNC(OriginalBigShotStart);
extern "C" REX_FUNC(BigShotStart) {
  OriginalBigShotStart(ctx, base);
  // +88 is set only when the tiles go to the CPU buffer (+352).
  if (!Enabled() || !*forza::GuestPtr(base, kTileState + 88) || !Load32(base, kTileState + 352)) {
    return;
  }
  const uint64_t range = FrontBufferRange(base);
  if (!range) return;
  uint64_t generation;
  {
    std::lock_guard lock(g_mutex);
    if (!SetRange(range)) {
      REXLOG_WARN("Big Shot: the SDK has no readback_resolve_wait_range; its tiles may come out "
                  "as an old picture with readback_resolve = some");
      return;
    }
    g_set = true;
    generation = ++g_generation;
  }
  REXLOG_INFO("Big Shot: the front buffers ({:08X}, {} bytes) are copied back for its tiles",
              uint32_t(range >> 32), uint32_t(range));
  std::thread([generation] {
    std::this_thread::sleep_for(kLimit);
    std::lock_guard lock(g_mutex);
    if (g_set && g_generation == generation) {
      SetRange(0);
      g_set = false;
      REXLOG_WARN("Big Shot: no end after {} minutes; front buffer readback back to normal",
                  std::chrono::duration_cast<std::chrono::minutes>(kLimit).count());
    }
  }).detach();
}

extern "C" REX_FUNC(OriginalBigShotEnd);
extern "C" REX_FUNC(BigShotEnd) {
  OriginalBigShotEnd(ctx, base);
  std::lock_guard lock(g_mutex);
  if (!g_set) return;
  SetRange(0);
  g_set = false;
  REXLOG_INFO("Big Shot: done, front buffer readback back to normal");
}
