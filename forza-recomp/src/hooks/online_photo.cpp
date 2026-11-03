// forza - online photo (#576, src/platform/online_photo.h, docs/playing.md).
// tu_address_map: TU4 addresses
//
// The entry. The pause builder (sub_827DE1B0) reads the session's game mode type (sub_82F12410
// on the game services object, sub_82BB4240, sub_824B75E8) and adds Photo Mode only outside the
// online types, when sub_8274B828 and the settings byte +0x94D allow it (+0x957 overrides the
// first). WantsPauseRow repeats that test for the online types; graphics_menu.cpp's hook on
// the list bind (sub_827D1618) adds the row with the builder's own action id, so the pause
// screen's dispatcher (sub_827EBEE0, action 11) runs the game's photo mode flow, online too.
//
// The picture. Photo mode asks the renderer for an accumulation (sub_825F3F88: samples, mode):
// one sample while its camera moves, the whole sample list (96) while it holds still, and the
// list at the shutter (mode 1); "PREPARING PHOTO" waits until that is done. Online the world
// keeps running, and the camera follows the car: every frame a camera update
// (sub_82707F58 -> sub_82704838) restarts the accumulation, so the shutter's never finished
// and the game waited forever. Here every request is one sample (the photo is the current
// frame: no depth of field, nothing smears), and from the shutter until photo mode's camera
// asks again the camera updates are not sent, as nothing sends them in a paused game.
//
// The saved file. SAVE composes the photo, resolves it (1280x720 and its 416x240 thumbnail)
// and encodes it on the CPU. With readback_resolve = some a resolve is copied back to guest
// memory only when its key is new; online those keys are resolved every frame, so the saved
// photo and thumbnail were black. From the shutter until the camera is back, every resolve
// waits for the GPU and is copied back (ReIGlue 0411's readback_resolve_wait_range over the
// whole of physical memory, as readback_resolve = full does).
//
// Big Shot: its nine tiles are rendered one after the other, which a running world would tear
// apart. Online, photo mode's check for it (sub_827E4130) answers no, so RB stays STANDARD.
#include "../platform/online_photo.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>

REXCVAR_DEFINE_BOOL(forza_online_photo, false, "Interface",
                    "Photo mode in online free roam and lobbies: one frame, the session keeps running");

namespace forza::online_photo {
namespace {
std::atomic<bool> active{false};
std::atomic<bool> in_photo{false};  // photo mode has asked for an accumulation since the pause menu

uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

class Native {
 public:
  Native(const PPCContext& ctx, uint8_t* base) : ctx_(ctx), base_(base) {}
  uint32_t Call(uint32_t function, uint32_t r3 = 0) {
    ctx_.r3.u64 = r3;
    rex::runtime::ResolveIndirectFunction(function)(ctx_, base_);
    return ctx_.r3.u32;
  }

 private:
  PPCContext ctx_;
  uint8_t* base_;
};

// The session's game mode type, 0 without a session.
uint32_t SessionType(const PPCContext& ctx, uint8_t* base) {
  Native native(ctx, base);
  const auto services = Read(base, 0x833CAAE4);
  if (!services) return 0;
  const auto session = native.Call(0x82F12410, services);
  if (!session) return 0;
  const auto holder = native.Call(0x82BB4240, session);
  if (!holder || !Read(base, holder)) return 0;
  return native.Call(0x824B75E8, Read(base, holder));
}

// From the shutter until photo mode's camera is back (or the pause menu is built again).
std::mutex shutter_mutex;
Shutter shutter;
bool readback_set = false;
std::chrono::steady_clock::time_point shutter_time;
constexpr uint64_t kAllPhysical = 0x20000000;  // start 0, length 512 MB

void Readback(bool on) {  // shutter_mutex held
  if (on == readback_set) return;
  if (!rex::cvar::SetFlagByName("readback_resolve_wait_range",
                                on ? std::to_string(kAllPhysical) : std::string("0"))) {
    if (on) REXLOG_WARN("Online photo: the SDK has no readback_resolve_wait_range; a saved photo may be black");
    return;
  }
  readback_set = on;
}
}  // namespace

bool Enabled() { return REXCVAR_GET(forza_online_photo); }

bool WantsPauseRow(const PPCContext& ctx, uint8_t* base, uint32_t screen) {
  // Only the pause screen (its vtable), never the title's or another list.
  if (!base || !screen || Read(base, screen) != 0x82061F94) return false;
  {
    std::lock_guard lock(shutter_mutex);
    shutter.Reset();
    Readback(false);
  }
  in_photo = false;
  const uint32_t type = Enabled() ? SessionType(ctx, base) : 0;
  if (!IsOnlineType(type)) {
    active = false;
    return false;
  }
  Native native(ctx, base);
  const auto settings = native.Call(0x824A94B8);
  const bool event_gate = native.Call(0x8274B828) & 0xFF;
  const bool allowed = settings && (!event_gate || GuestPtr(base, settings)[0x957]) &&
                       !GuestPtr(base, settings)[0x94D];
  active = allowed;
  if (!allowed) REXLOG_INFO("Online photo: not offered here (session type {})", type);
  return allowed;
}

bool Active() { return active.load(); }

}  // namespace forza::online_photo

#if FORZA_TU4
extern "C" REX_FUNC(__imp__sub_825F3F88);
extern "C" REX_FUNC(sub_825F3F88) {
  using namespace forza::online_photo;
  // From photo mode's first request (by return address: the camera updates, sub_82704838; the
  // shutter, sub_827D69F8; its camera screen, sub_827DBE70; its start, sub_827DBDE8) until the
  // pause menu is back, every request is taken as photo mode's. Before that, another screen's
  // (sub_827356E8, a car view) is left alone.
  const uint32_t from = uint32_t(ctx.lr);
  const bool camera = from == 0x82704878;
  if (Active() && (camera || from == 0x827D6A6C || from == 0x827DC3A4 || from == 0x827DC3C4 ||
                   from == 0x827DBE50))
    in_photo = true;
  if (!Active() || !in_photo) {
    __imp__sub_825F3F88(ctx, base);
    return;
  }
  const uint32_t mode = ctx.r5.u32;
  Accumulation request;
  {
    std::lock_guard lock(shutter_mutex);
    const bool was_held = shutter.Held();
    request = shutter.Request(ctx.r4.u32, mode, camera);
    if (mode == 1) {
      shutter_time = std::chrono::steady_clock::now();
      Readback(true);
      REXLOG_INFO("Online photo: shutter, 1 sample (photo mode asked for {})", int32_t(ctx.r4.u32));
    } else if (was_held && !shutter.Held()) {
      Readback(false);
      REXLOG_INFO("Online photo: back in the camera after {:.1f} s",
                  std::chrono::duration<double>(std::chrono::steady_clock::now() - shutter_time).count());
    }
  }
  if (!request.send) return;
  ctx.r4.u64 = request.samples;
  __imp__sub_825F3F88(ctx, base);
}

// Photo mode's camera screen: may RB switch to BIG SHOT (vtable +0xE0).
extern "C" REX_FUNC(__imp__sub_827E4130);
extern "C" REX_FUNC(sub_827E4130) {
  if (forza::online_photo::Active()) {
    ctx.r3.u64 = 0;
    return;
  }
  __imp__sub_827E4130(ctx, base);
}

// Photo mode's camera screen, every frame. Its state (+0x748) is 3 from the shutter while the
// photo is shown (PREPARING PHOTO, SAVE, the upload question) and 0 back in the camera.
extern "C" REX_FUNC(__imp__sub_827DBE70);
extern "C" REX_FUNC(sub_827DBE70) {
  using namespace forza::online_photo;
  if (Active() && ctx.r3.u32) {
    const auto* p = forza::GuestPtr(base, ctx.r3.u32 + 0x748);
    const uint32_t state = uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
    if (state == 0) {
      std::lock_guard lock(shutter_mutex);
      if (shutter.Held()) {
        shutter.Reset();
        Readback(false);
        REXLOG_INFO("Online photo: back in the camera after {:.1f} s",
                    std::chrono::duration<double>(std::chrono::steady_clock::now() - shutter_time).count());
      }
    }
  }
  __imp__sub_827DBE70(ctx, base);
}
#endif
