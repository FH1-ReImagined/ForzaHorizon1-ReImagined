#include "auto_pause.h"
#include "auto_pause_policy.h"
#include "driving_gate.h"
#include "guest_memory.h"
#include "sdk_import.h"
#include "xr.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/memory/utils.h>
#include <rex/ui/window.h>
#include <rex/ui/window_listener.h>
#include <atomic>
#include <algorithm>
#include <bit>
#include <cmath>
#include <mutex>

REXCVAR_DEFINE_BOOL(forza_pause_on_focus_loss, false, "Controls",
                    "Open the pause menu when the game loses focus; resume manually");
REXCVAR_DEFINE_BOOL(forza_pause_on_controller_disconnect, false, "Controls",
                    "Open the pause menu when the controller in use disconnects; resume manually");

namespace forza::auto_pause {
namespace {
std::atomic<uint32_t> focus_losses{0}, world{0};
std::mutex mutex;
Policy policy;
bool was_requesting = false;

bool CanPause(uint8_t* base) {
  if (telemetry::Player().State() != telemetry::Drive::kDriving) return false;
  const uint32_t address = world.load(std::memory_order_acquire);
  if (address < 0x100000 || address > 0xefffff00) return false;
  auto* p = GuestPtr(base, address);
  // The cached world address comes from the real world-update hook. Validate
  // both pages before looking at its current flags, rather than relying only
  // on the debounced gate (which can still say driving just after manual pause).
  for (size_t i = 0; i < 0x2c;) {
    size_t region = 0;
    rex::memory::PageAccess access{};
    if (!rex::memory::QueryProtect(p + i, region, access) ||
        !(int(access) & int(rex::memory::PageAccess::kReadOnly))) return false;
    const size_t page = rex::memory::page_size();
    i += std::min(size_t(0x2c) - i, page - (reinterpret_cast<uintptr_t>(p + i) % page));
  }
  const uint32_t scale = uint32_t(p[0x28]) << 24 | uint32_t(p[0x29]) << 16 |
                         uint32_t(p[0x2a]) << 8 | p[0x2b];
  const float speed = std::bit_cast<float>(scale);
  return !p[0x0c] && p[0x0d] && std::isfinite(speed) && speed > 0;
}
}

struct WindowObserver::Impl final : rex::ui::WindowListener {
  explicit Impl(rex::ui::Window* w) : window(w) { if (window) window->AddListener(this); }
  ~Impl() override { Detach(); }
  void OnLostFocus(rex::ui::UISetupEvent&) override {
    if (!xr::SessionRunning()) ++focus_losses;
  }
  void OnClosing(rex::ui::UIEvent&) override { Detach(); }
  void Detach() { if (window) window->RemoveListener(this); window = nullptr; }
  rex::ui::Window* window;
};
WindowObserver::WindowObserver(rex::ui::Window* window) : impl_(std::make_unique<Impl>(window)) {}
WindowObserver::~WindowObserver() = default;
void World(uint32_t address) { world.store(address, std::memory_order_release); }
bool Poll(uint8_t* base, uint16_t buttons, uint32_t user_mask) {
  using Sequence = uint32_t (*)(uint32_t);
  static const auto sequence = reinterpret_cast<Sequence>(SdkSymbol("ForzaInputDisconnectSequence"));
  const bool on_focus = REXCVAR_GET(forza_pause_on_focus_loss);
  const bool on_disconnect = REXCVAR_GET(forza_pause_on_controller_disconnect);
  if (on_disconnect && !sequence) {
    static std::once_flag warned;
    std::call_once(warned, [] { REXLOG_WARN("Auto-pause: controller disconnect detection requires an updated SDK build"); });
  }
  std::lock_guard lock(mutex);
  const bool request = policy.Poll(telemetry::DrivingNowMs(), focus_losses.load(),
      sequence ? std::optional<uint32_t>(sequence(user_mask)) : std::nullopt,
      on_focus, on_disconnect, (on_focus || on_disconnect) && CanPause(base), (buttons & 0x10) != 0,
      xr::SessionRunning());
  if (request && !was_requesting) REXLOG_INFO("Auto-pause: requesting the game's pause menu; resume manually");
  was_requesting = request;
  return request;
}
void Cancel() { std::lock_guard lock(mutex); policy.Cancel(); was_requesting = false; }
}
