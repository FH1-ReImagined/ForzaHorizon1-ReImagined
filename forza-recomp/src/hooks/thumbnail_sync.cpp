// forza - a car's picture, its livery and photo mode are rendered with real pipelines (#74, #77,
// #167, docs/rendering.md).
//
// The game renders a car's picture once (the purchase, Car Select, the garage), reads the
// resolve back on the CPU and saves it in the profile (ForzaProfile/Thumbnails). With
// async_shader_compilation the SDK skips a draw whose pipeline is still being created, so
// with an empty pipeline cache (a new install's first purchase) the car's draws were lost
// and the saved picture was nearly black (23-33 KB: only the narrow side view, no car).
//
// sub_82546460 starts the picture (it calls the save, sub_82546168, and returns before the
// GPU reaches the render: the command processor runs behind the guest, by 270 ms in a cold
// run). So the window is by time, not by the call: from the call the SDK setting
// sync_pipeline_creation (patch 0051) makes the command processor create or wait for the
// real pipeline of each draw, for kWindow. Other draws in that time wait for their pipeline
// too, as with async_shader_compilation=false; with a warm cache nothing waits.
// FORZA_THUMB_SYNC=0 switches the hook off.
//
// The livery (paint, vinyls and decals) is drawn once into the car's 1024x1024 textures when
// the car is loaded, and the same skipped draws left those textures without some of their
// layers for as long as the car stayed loaded (#77: with an empty cache the BMW M3 GTR at
// the festival lost the white of its left front wing). sub_82C8FE60 sets up each livery
// element render (render scenario "RenderLiveryElements" or "...Highlight"; called 10 times
// within 1 ms at the festival load), and holds the same window. FORZA_LIVERY_SYNC=0 switches
// that hook off.
//
// Photo mode (#167): the photo is composed, resolved and read back on the CPU at SAVE. Since
// ReIGlue 0364 the pipelines a frame meets are built in the background across frames, and a
// draw whose pipeline is not ready yet is skipped in every frame until it is: a photo taken
// soon after entering photo mode on a slow driver could miss them. Photo mode draws the
// first shadow cascade with the high-quality filter (ApplyShadows technique 8,
// src/hooks/shadow_filter.cpp), which play never uses; while it does, the same window holds
// (the frame end then waits for every pipeline in flight, as before 0364).
// FORZA_PHOTO_SYNC=0 switches that off.

#include "thumbnail_sync.h"

#include "../platform/guest_addr.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>

namespace {

constexpr auto kWindow = std::chrono::seconds(8);

bool EnvOn(const char* name) {
  const char* v = std::getenv(name);
  return !(v && std::strcmp(v, "0") == 0);
}

void SetSync(bool on) {
  for (rex::cvar::FlagEntry& entry : rex::cvar::GetRegistry()) {
    if (entry.name == "sync_pipeline_creation") {
      entry.setter(on ? "true" : "false");
      return;
    }
  }
}

std::mutex g_sync_mutex;
uint64_t g_generation = 0;  // Protected with the corresponding flag update.

// Sets the SDK setting now and clears it kWindow after the last call.
void HoldSync() {
  uint64_t generation;
  {
    std::lock_guard lock(g_sync_mutex);
    generation = ++g_generation;
    SetSync(true);
  }
  std::thread([generation] {
    std::this_thread::sleep_for(kWindow);
    // An expired timer must not clear a hold renewed between its check and flag write.
    std::lock_guard lock(g_sync_mutex);
    if (g_generation == generation) SetSync(false);
  }).detach();
}

}  // namespace

namespace forza::thumbnail_sync {

void HoldForPhotoMode() {
  static const bool on = EnvOn("FORZA_PHOTO_SYNC");
  if (!on) return;
  // Called for every photo mode frame: renew the window at most once a second.
  static std::atomic<int64_t> last_ms{INT64_MIN / 2};
  const int64_t now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                             std::chrono::steady_clock::now().time_since_epoch())
                             .count();
  int64_t last = last_ms.load(std::memory_order_relaxed);
  if (now_ms - last < 1000 || !last_ms.compare_exchange_strong(last, now_ms)) return;
  if (now_ms - last > std::chrono::milliseconds(kWindow).count()) {
    REXLOG_INFO("Pipeline sync: photo mode, the pipelines are built before its frames end");
  }
  HoldSync();
}

}  // namespace forza::thumbnail_sync

extern "C" REX_FUNC(__imp__sub_82546460);
extern "C" REX_FUNC(sub_82546460) {
  static const bool on = EnvOn("FORZA_THUMB_SYNC");
  if (on) HoldSync();
  __imp__sub_82546460(ctx, base);
}

extern "C" REX_FUNC(__imp__sub_82C8FE60);
extern "C" REX_FUNC(sub_82C8FE60) {
  static const bool on = EnvOn("FORZA_LIVERY_SYNC");
  if (on) HoldSync();
  __imp__sub_82C8FE60(ctx, base);
}
