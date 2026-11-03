#pragma once
#include <cstdint>
#include <optional>

namespace forza::auto_pause {
// Edge-triggered requests, never an automatic resume. The caller additionally
// checks the live world's pause flag before exposing START to the game.
class Policy {
 public:
  bool Poll(uint64_t now, uint32_t focus, std::optional<uint32_t> disconnect,
            bool on_focus, bool on_disconnect, bool driving, bool start_held,
            bool xr_session_running = false) {
    // Consume focus events and cancel a pending focus pulse while VR owns focus. Physical
    // disconnect detection remains active, including when VR uses a flat virtual screen.
    on_focus = on_focus && !xr_session_running;
    const bool lost_focus = focus_ && *focus_ != focus;
    const bool lost_pad = disconnect_ && disconnect && *disconnect_ != *disconnect;
    focus_ = focus;
    disconnect_ = disconnect;
    reasons_ &= uint8_t((on_focus ? 1 : 0) | (on_disconnect ? 2 : 0));
    if (!reasons_) until_ = 0;
    if (!driving || start_held || (!on_focus && !on_disconnect)) {
      Cancel();
      return false;
    }
    const uint8_t reason = uint8_t((on_focus && lost_focus ? 1 : 0) |
                                   (on_disconnect && lost_pad ? 2 : 0));
    if (reason) { until_ = now + 150; reasons_ |= reason; }
    return until_ && now < until_;
  }
  void Cancel() { until_ = 0; reasons_ = 0; }

 private:
  std::optional<uint32_t> focus_, disconnect_;
  uint64_t until_ = 0;
  uint8_t reasons_ = 0;
};
}  // namespace forza::auto_pause
