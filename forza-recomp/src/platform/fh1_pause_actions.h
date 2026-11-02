#pragma once

#include "guest_addr.h"
#include <cstdint>
#include <initializer_list>
#include <optional>
#include <rex/ppc/context.h>

#if FORZA_TU4
#include "guest_memory.h"
#include <rex/ppc/func.h>
#endif

// TU4's pause actions remain owned by the original FH1 screen. The imported
// scene supplies controls, but must not put FH2 objects in the FH1 screen fields.
namespace forza::fh1_pause_actions {
inline constexpr uint32_t kMap = 2;
inline constexpr uint32_t kResume = 3;
inline constexpr uint32_t kMyProfile = 12;

#if FORZA_TU4
namespace detail {
inline uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 |
         uint32_t(p[2]) << 8 | p[3];
}
inline void Write(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = GuestPtr(base, address);
  for (int i = 3; i >= 0; --i) { p[i] = uint8_t(value); value >>= 8; }
}
class Native {
public:
  Native(const PPCContext& context, uint8_t* memory)
      : context_(context), base_(memory) {}
  uint32_t Call(uint32_t address, std::initializer_list<uint32_t> args) {
    unsigned index = 0;
    for (const auto value : args) switch (index++) {
      case 0: context_.r3.u64 = value; break;
      case 1: context_.r4.u64 = value; break;
      case 2: context_.r5.u64 = value; break;
      case 3: context_.r6.u64 = value; break;
    }
    rex::runtime::ResolveIndirectFunction(address)(context_, base_);
    return context_.r3.u32;
  }
private:
  PPCContext context_;
  uint8_t* base_;
};
inline bool IsPause(uint8_t* base, uint32_t screen) {
  return base && screen && Read(base, screen) == 0x82061F94;
}
struct GuardObservation {
  uint8_t* base;
  uint32_t screen, action;
  bool rejected = false;
};
inline thread_local GuardObservation* guard_observation = nullptr;
struct ObserveGuard {
  GuardObservation* previous;
  explicit ObserveGuard(GuardObservation& observation)
      : previous(guard_observation) { guard_observation = &observation; }
  ~ObserveGuard() { guard_observation = previous; }
};
} // namespace detail

// Only the scoped native dispatch observes this result. The original guard
// still runs once and retains all content/license prompts and side effects.
inline void ObserveClubGuard(uint8_t* base, uint32_t screen, bool accepted) {
  auto* observation = detail::guard_observation;
  if (observation && observation->base == base && observation->screen == screen &&
      observation->action == 19) observation->rejected = !accepted;
}

struct DialogRequest {
  uint32_t id = 0, params = 0;
};
inline bool ChildQueued(uint8_t* base, uint32_t screen) {
  // 82F240C8 sets +264 only after accepting a native child request and
  // transfers its owned parameters to +160. The later child retains them.
  return detail::IsPause(base, screen) && GuestPtr(base, screen + 264)[0] != 0;
}
inline std::optional<DialogRequest> QueuedDialog(const PPCContext& context,
                                                uint8_t* base, uint32_t screen) {
  if (!ChildQueued(base, screen)) return {};
  const auto params = detail::Read(base, screen + 160);
  if (!params) return {};
  const auto table = detail::Read(base, params);
  const auto getter = table ? detail::Read(base, table + 4) : 0;
  if (!getter) return {};
  detail::Native native(context, base);
  // This is the same native ID getter used by 82F28AD0 when returning a child.
  return DialogRequest{native.Call(getter, {params}), params};
}
inline bool MatchesDialog(uint8_t* base, uint32_t screen, const DialogRequest& request,
                          uint32_t id, uint32_t child) {
  return detail::IsPause(base, screen) && child && request.params && id == request.id &&
      detail::Read(base, child + 156) == screen &&
      detail::Read(base, child + 164) == request.params;
}
inline bool HasRequiredExitCallback(uint8_t* base, uint32_t screen,
                                    uint32_t dialog, uint32_t choice) {
  if (!detail::IsPause(base, screen)) return false;
  // These 827E5CA8 branches close without a transition when their callback
  // has disappeared since the confirmation opened. Other accepted branches
  // perform their own activity work before (or without) the done callback.
  uint32_t offset=0;
  switch (dialog) {
    case 2: offset=choice==6?508:504;break;
    case 3: offset=484;break;
    case 17: offset=512;break;
    case 23: offset=488;break;
    default: return true;
  }
  return detail::Read(base,screen+offset)!=0;
}

struct Row {
  uint32_t address = 0, text = 0;
  bool enabled = false;
};
// Read the native display-order vector, including its enabled flag. A numeric
// ID alone is not authorization: the builders own event, DLC and party gates.
inline std::optional<Row> FindRow(uint8_t* base, uint32_t screen, uint32_t action) {
  if (!detail::IsPause(base, screen) || action > 19) return {};
  const auto begin = detail::Read(base, screen + 0x19C);
  const auto end = detail::Read(base, screen + 0x1A0);
  if (!begin || end < begin || end - begin > 1024 || (end - begin) % 4) return {};
  std::optional<Row> result;
  for (auto entry = begin; entry < end; entry += 4) {
    const auto row = detail::Read(base, entry);
    if (!row || detail::Read(base, row + 8) != action) continue;
    if (result) return {}; // An ambiguous model must not dispatch an action.
    result = Row{row, detail::Read(base, row + 12), GuestPtr(base, row + 20)[0] != 0};
  }
  return result;
}
inline bool Available(uint8_t* base, uint32_t screen, uint32_t action) {
  const auto row = FindRow(base, screen, action);
  return row && row->enabled;
}

// Call once from the imported scene's replacement for 827DFB18, on the guest UI
// thread with its live PPC context. Both model builders finish by calling
// 827D1618: the imported scene MUST intercept that call and skip its original
// list binding. The model and callback references are still destroyed by the
// original 827D6248. This does not construct or bind a replacement list.
inline bool InitModel(const PPCContext& context, uint8_t* base, uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827D6348, {screen}); // Existing footer; tolerates no footer.
  const auto game = native.Call(0x824A94B8, {});
  if (!game) return false;
  const bool rally = detail::Read(base, game + 0x944) == 3;
  native.Call(rally ? 0x827D65A0 : 0x827DE1B0, {screen});
  // The original engine event/hash is runtime initialized. This call publishes
  // the pause engine event; it does not initialize the action callbacks below.
  native.Call(0x82F1EC20, {screen, detail::Read(base, 0x833F1DF8)});
  // Preserve the timer binding used by the original tick, even when the node
  // does not exist in the imported scene. The constructor owns this binding.
  native.Call(0x82F21888, {screen, 0x82051D90, screen + 0x1B8});
  native.Call(0x82F3A490, {screen + 0x1B8, 0x820063C4});
  native.Call(0x827DABD0, {screen});
  return true;
}

// Dispatch only native IDs. Availability must come from the original +0x174
// model (and its enabled state), not from this numeric range. In particular,
// Map=2 runs the +0x1E0 callback or native screen 0xA0 fallback; MyProfile=12
// enters native screen 0xB2. The dispatcher ignores r5 and reads action from r6.
// Custom Graphics/Difficulty IDs belong to the host adapter, not this helper.
inline bool Dispatch(const PPCContext& context, uint8_t* base, uint32_t screen,
                     uint32_t action) {
  if (!Available(base, screen, action)) return false;
  detail::Native native(context, base);
  native.Call(0x827DABD0, {screen});
  detail::GuardObservation observation{base, screen, action};
  detail::ObserveGuard scope(observation);
  native.Call(0x827EBEE0, {screen, 2, 0, action});
  // Club can reject without a prompt (missing resource/availability). A
  // rejection with an accepted child is still a native prompt to return from.
  return !observation.rejected || ChildQueued(base, screen);
}

// Original COptions selection branches. These branches use only the common
// screen+0x10C owner field and set the options selection at game-state+0xDCC;
// they do not inspect COptions controls. Native manager vtable+0x4C receives
// (manager, owner, screenID, 0, -1, 0), without allocating child parameters.
// Controls: choice 0 -> screen 0x3D, factory 8273D5E8/CControllerLayout.
// Audio: choice 3 -> screen 0x0C, factory 8273D5A0/CAudioOptionsBasic.
// Preserve the originating Settings tab before calling: these are native
// manager screen transitions, rather than the Graphics child-page overlay.
inline bool OpenControls(const PPCContext& context, uint8_t* base,
                         uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827D92D0, {screen, 2, 0, 0});
  return true;
}
inline bool OpenAudio(const PPCContext& context, uint8_t* base, uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827D92D0, {screen, 2, 0, 3});
  return true;
}

// Native COptions choice 2 -> HUD screen 0x5F; choice 4 -> Video screen
// 0x164. Both use the same owner/return path as Audio and Controls above.
inline bool OpenHud(const PPCContext& context, uint8_t* base, uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827D92D0, {screen, 2, 0, 2});
  return true;
}
inline bool OpenVideo(const PPCContext& context, uint8_t* base, uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827D92D0, {screen, 2, 0, 4});
  return true;
}

// FH1's progress/statistics view is CStatisticsUI, screen 0x14A using the
// original H_STATISTICS scene (factory 82719600 -> constructor 82843AA0).
// CMyProfileMenu choice 1 enters it directly, with no child parameters. That
// branch of 82789718 reads only common screen+0x10C and records its menu choice
// in game-state+0xD74; it never accesses CMyProfileMenu's list controls. Native
// categories, data binding and Back remain owned by CStatisticsUI. The caller
// must retain the originating tile and hide the imported grid, as for Audio.
inline bool OpenProgress(const PPCContext& context, uint8_t* base,
                         uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x82789718, {screen, 2, 0, 1});
  return true; // Transition requested; native UI manager owns the child.
}

// The pause's "multiplayer" callback enters the original game-mode flow.
// Opening CPartyLobby directly skips its session/resource initialization.
inline bool OpenMultiplayer(const PPCContext& context, uint8_t* base,
                            uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827DABD0, {screen});
  const auto callback = detail::Read(base, screen + 0x1E4);
  if (!callback) return false;
  native.Call(0x82EAA148, {callback});
  return true;
}

// Native Back/Start branch of 827DADC8, without its unrecognized-event fallback
// into screen+0x170. The +0x1DC "done" callback resumes through FH1's own flow.
// The adapter must own input and prevent this while a child screen is active.
inline bool Resume(const PPCContext& context, uint8_t* base, uint32_t screen) {
  if (!detail::IsPause(base, screen)) return false;
  detail::Native native(context, base);
  native.Call(0x827DABD0, {screen});
  if (!detail::Read(base, screen + 0x1DC)) return false;
  const auto table = detail::Read(base, screen);
  native.Call(detail::Read(base, table + 0x78),
              {screen, detail::Read(base, 0x835AA9A8)});
  const auto done = detail::Read(base, screen + 0x1DC);
  if (done) native.Call(0x82EAA148, {done});
  return true;
}

// Optional event matcher for the existing FH1 input callback. `event` is its
// r4 event object, not a numeric button ID. Call this before forwarding input to
// the imported controls. Match only native Back=0x60 and Start=0x68; other
// inputs must never fall through to original 827DADC8 with a missing list.
inline bool BackInput(const PPCContext& context, uint8_t* base, uint32_t screen,
                      uint32_t event) {
  if (!detail::IsPause(base, screen) || !event) return false;
  detail::Native native(context, base);
  detail::Write(base, screen + 0x1D0, event);
  for (const auto button : {0x60u, 0x68u}) {
    // This is the predicate used by screen vtable+0xA8 (827464B8), without a
    // temporary guest array. Its return is narrowed to a byte in native code.
    if (native.Call(0x82CBC110, {event, 0xA, button, 0}) & 0xFFu) {
      Resume(context, base, screen);
      return true; // Consume recognized input even when no done callback exists.
    }
  }
  return false;
}
#else
inline bool InitModel(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool Dispatch(const PPCContext&, uint8_t*, uint32_t, uint32_t) { return false; }
inline bool OpenControls(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool OpenAudio(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool OpenHud(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool OpenVideo(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool OpenProgress(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool OpenMultiplayer(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool Resume(const PPCContext&, uint8_t*, uint32_t) { return false; }
inline bool BackInput(const PPCContext&, uint8_t*, uint32_t, uint32_t) { return false; }
#endif

// Imported-scene teardown integration:
// - Bypass original 827D1578 (vtable+0x10): it unconditionally removes the old
//   list listener via screen+0x170. Unregister imported control listeners there.
// - Keep 827D6248: it releases +0x174 model, +0x1AC/+0x1B8 bindings and
//   +0x1DC..+0x204 callback references, and has no +0x170 dereference.
// - Keep 827DE0D0 tick: it refreshes callbacks, advances native screen state,
//   and updates the timer binding. It also has no list dereference.
// - 827DAB28 tolerates a missing list and still initializes +0x1D8/+0x208.
} // namespace forza::fh1_pause_actions
