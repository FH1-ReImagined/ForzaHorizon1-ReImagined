#pragma once

#include "fh1_pause_actions.h"
#include "native_pause_pages.h"

#if FORZA_TU4
// Native result lifecycle decisions, shared by the live composition and the
// guest-memory regression fixture. Ownership/rendering stay with the scene.
namespace forza::native_pause_dialog {
using Outcome=menu::PauseDialogOutcome;
enum class Effect { Keep, Restore, Release };
struct Completion {
  Effect effect=Effect::Keep;
  std::optional<fh1_pause_actions::DialogRequest> next;
};

inline Outcome Before(const PPCContext& ctx,uint8_t* base,uint32_t screen,
                      const std::optional<fh1_pause_actions::DialogRequest>& request) {
  using namespace fh1_pause_actions;
  if (ctx.r3.u32!=screen || !request ||
      !MatchesDialog(base,screen,*request,ctx.r4.u32,ctx.r5.u32)) return Outcome::Ignored;
  // The common native handler owns information/difficulty/marketplace results.
  // Only activity confirmations require this adapter to inspect a choice.
  if (menu::NativePauseDialogReturns(request->id)) return Outcome::Cancel;
  if (!ctx.r6.u32 || detail::Read(base,ctx.r6.u32)!=0x82033168) return Outcome::Ignored;
  const auto choice=detail::Read(base,ctx.r6.u32+12);
  const auto outcome=menu::NativePauseDialogOutcome(request->id,choice);
  if (outcome==Outcome::Leave && !HasRequiredExitCallback(base,screen,request->id,choice))
    return Outcome::Cancel;
  return outcome;
}

inline Completion After(const PPCContext& ctx,uint8_t* base,uint32_t screen,
                        Outcome outcome,bool close_child) {
  using namespace fh1_pause_actions;
  if (outcome==Outcome::Ignored || !detail::IsPause(base,screen)) return {};
  if (outcome==Outcome::Restart) {
    // Native 827E5CA8 sets +468 only after accepting restart; its button
    // delegate runs after this result callback and consumes the flag.
    if (GuestPtr(base,screen+468)[0]) return {Effect::Release,{}};
    outcome=Outcome::Cancel;
  }
  if (const auto next=QueuedDialog(ctx,base,screen)) return {Effect::Keep,next};
  if (close_child && outcome==Outcome::Cancel) return {Effect::Restore,{}};
  // In particular Restart X6's full-screen delegate runs AFTER this callback.
  return {};
}
} // namespace forza::native_pause_dialog
#endif
