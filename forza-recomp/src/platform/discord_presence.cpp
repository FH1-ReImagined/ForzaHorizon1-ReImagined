#include "discord_presence.h"
#include "discord_model.h"
#include <discord_rpc.h>
#include <rex/cvar.h>
#include <rex/logging.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <ctime>
#include <mutex>
#include <thread>

REXCVAR_DEFINE_BOOL(discord_presence, true, "Social",
                    "Share the current activity with the local Discord desktop client")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_BOOL(discord_presence_details, true, "Social",
                    "Also share the car with its class and PI, the event and the area of Colorado "
                    "(names the game shows; off: only the activity)")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_STRING(discord_application_id, "1563015276583460934", "Social",
                      "Public Discord application ID (not a bot token or client secret)")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

namespace forza::discord {
namespace {
using Clock = std::chrono::steady_clock;
struct Service {
  std::atomic<bool> enabled{false};
  std::atomic<bool> details{false};
  std::mutex lifecycle;
  std::mutex mutex;
  std::condition_variable wake;
  Model model;
  bool stop = false;
  std::thread worker;
  ~Service() { Stop(); }
  void Stop() {
    std::lock_guard lifetime_lock(lifecycle);
    enabled = false;
    { std::lock_guard lock(mutex); stop = true; }
    wake.notify_all();
    if (worker.joinable()) worker.join();
  }
};
Service& State() { static Service service; return service; }

void Run(std::string id) {
  auto& service = State();
  DiscordEventHandlers handlers{};
  handlers.ready = [](const DiscordUser*) { REXLOG_INFO("DISCORD: Rich Presence connected"); };
  handlers.disconnected = [](int code, const char*) { REXLOG_INFO("DISCORD: disconnected ({})", code); };
  handlers.errored = [](int code, const char*) { REXLOG_WARN("DISCORD: Rich Presence error ({})", code); };
  // No protocol registration or join secrets: status display only. Discord owns its
  // reconnecting I/O thread; game hooks only copy small, non-identifying state.
  Discord_Initialize(id.c_str(), &handlers, 0, nullptr);
  const int64_t started = int64_t(std::time(nullptr));
  Activity previous;
  auto next_update = Clock::time_point{};
  std::unique_lock lock(service.mutex);
  while (!service.stop) {
    auto activity = service.model.Format();
    lock.unlock();
    Discord_RunCallbacks();
    const auto now = Clock::now();
    if (now >= next_update && activity != previous) {
      DiscordRichPresence presence{};
      presence.details = activity.details.c_str();
      presence.state = activity.state.c_str();
      presence.startTimestamp = started;
      Discord_UpdatePresence(&presence);
      previous = activity;
      next_update = now + std::chrono::seconds(5);
      REXLOG_INFO("DISCORD: activity updated: {} / {}", activity.details, activity.state);
    }
    lock.lock();
    service.wake.wait_for(lock, std::chrono::milliseconds(250), [&] { return service.stop; });
  }
  lock.unlock();
  Discord_ClearPresence();
  Discord_Shutdown();
}
}  // namespace

void Initialize() {
  if (!REXCVAR_GET(discord_presence)) return;
  const auto id = REXCVAR_GET(discord_application_id);
  if (!ValidApplicationId(id)) {
    REXLOG_WARN("DISCORD: Rich Presence needs a valid public application ID; continuing without it");
    return;
  }
  auto& service = State();
  std::lock_guard lifetime_lock(service.lifecycle);
  if (service.stop) return;
  if (service.worker.joinable()) return;
  {
    std::lock_guard lock(service.mutex);
    service.model.show_details = REXCVAR_GET(discord_presence_details);
  }
  service.details = REXCVAR_GET(discord_presence_details);
  service.enabled = true;
  try {
    service.worker = std::thread(Run, id);
  } catch (const std::system_error&) {
    service.enabled = false;
    REXLOG_WARN("DISCORD: could not start the optional Rich Presence worker");
  }
}
void Shutdown() { State().Stop(); }
void Context(uint32_t id, uint32_t value) {
  auto& service = State();
  if (!service.enabled.load(std::memory_order_relaxed)) return;
  std::lock_guard lock(service.mutex);
  service.model.Context(id, value);
}
void Party(uint32_t count, uint32_t capacity) {
  auto& service = State();
  if (!service.enabled.load(std::memory_order_relaxed)) return;
  std::lock_guard lock(service.mutex);
  service.model.count = count;
  service.model.capacity = capacity;
}
bool Wanted() {
  auto& service = State();
  return service.enabled.load(std::memory_order_relaxed) && service.details.load(std::memory_order_relaxed);
}
void Details(const GameDetails& details) {
  auto& service = State();
  if (!Wanted()) return;
  std::lock_guard lock(service.mutex);
  service.model.game = details;
}
}  // namespace forza::discord
