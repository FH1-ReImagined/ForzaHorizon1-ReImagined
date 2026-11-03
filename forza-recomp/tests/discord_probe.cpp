#include "../src/platform/discord_model.h"
#include <discord_rpc.h>
#include <cassert>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <thread>

using namespace forza::discord;
int main(int argc, char** argv) {
  assert(!ValidApplicationId(""));
  assert(!ValidApplicationId("secret-token"));
  assert(!ValidApplicationId("123"));
  assert(!ValidApplicationId("18446744073709551616"));
  assert(ValidApplicationId("123456789012345678"));
  Model model;
  assert(model.Format().details == "At the main menu");
  assert(!model.Context(0x10000039, 500));  // online rank must not leak into the status
  model.Context(0x8001, 1);
  assert(model.Format().details == "Exploring Colorado");
  model.Context(0x8001, 2);
  assert(model.Format().details == "Racing in a festival event");
  model.Context(0x16, 1);
  assert(model.Format().details == "Racing in Horizon Rally");
  model.Context(0x8001, 14);
  model.count = 3; model.capacity = 8;
  assert(model.Format().state == "Multiplayer | 3/8 players");
  model.Context(0x8001, 15); model.Context(0x12, 4);
  assert(model.Format().details == "Exploring Colorado online");
  model.count = 99;
  assert(model.Format().count == 0);
  for (uint32_t mode = 0; mode <= 16; ++mode) {
    model.Context(0x8001, mode);
    assert(!model.Format().details.empty());
  }
  // Details (#527): the game's names for the car, class/PI, event and zone.
  {
    Model d;
    d.game = {"1962 Ferrari 250 GTO", "S", 701, "Oakley Blitz", "Red Rock"};
    d.Context(0x8001, 1);
    assert(d.Format().details == "Exploring Colorado");  // discord_presence_details off
    assert(d.Format().state == "Free roam");
    d.show_details = true;
    assert(d.Format().details == "Exploring Colorado \xC2\xB7 Red Rock");
    assert(d.Format().state == "1962 Ferrari 250 GTO \xC2\xB7 S 701");
    d.Context(0x15, 1);  // Horizon Rally's free roam: no zone
    assert(d.Format().details == "Preparing for Horizon Rally");
    d.Context(0x15, 0);
    d.Context(0x8001, 2);
    assert(d.Format().details == "Racing in a festival event \xC2\xB7 Oakley Blitz");
    d.Context(0x8001, 6);
    assert(d.Format().details == "Challenging a rival \xC2\xB7 Oakley Blitz");
    d.Context(0x8001, 5);  // a PR stunt is no event
    assert(d.Format().details == "Taking on a PR stunt");
    assert(d.Format().state == "1962 Ferrari 250 GTO \xC2\xB7 S 701");
    for (uint32_t menu : {0u, 8u, 9u, 10u, 11u, 12u, 13u}) {  // menus keep their wording
      Model plain;
      plain.Context(0x8001, menu);
      d.Context(0x8001, menu);
      assert(d.Format() == plain.Format());
    }
    d.Context(0x8001, 14); d.count = 3; d.capacity = 8;  // the lobby: no car
    assert(d.Format().state == "Multiplayer | 3/8 players");
    d.Context(0x8001, 15); d.Context(0x12, 4);
    assert(d.Format().details == "Exploring Colorado online \xC2\xB7 Red Rock");
    assert(d.Format().state == "1962 Ferrari 250 GTO \xC2\xB7 S 701 | 3/8 players");
    d.Context(0x12, 0);
    assert(d.Format().details == "Racing online");
    d.game.pi = 0;  // class without a PI: the car only
    assert(d.Format().state == "1962 Ferrari 250 GTO | 3/8 players");
    d.game.car.clear();  // unknown car: the old state, never a placeholder
    assert(d.Format().state == "Multiplayer | 3/8 players");
    // Discord's 128-character limit, cut between UTF-8 sequences; the count stays.
    std::string long_name;
    for (int i = 0; i < 100; ++i) long_name += "\xC3\xA9";  // e-acute, 2 bytes
    d.game.car = long_name;
    const auto fitted = d.Format().state;
    assert(fitted.size() <= 128);
    assert(fitted.ends_with("... | 3/8 players"));
    for (size_t i = 0; i + 17 < fitted.size(); i += 2)
      assert(static_cast<unsigned char>(fitted[i]) == 0xC3);
    d.game.event = std::string(300, 'x');
    d.Context(0x8001, 2);
    assert(d.Format().details.size() == 128 && d.Format().details.ends_with("..."));
    assert(Fit("short") == "short");
  }
  if (argc == 1) { std::puts("Discord model PASS"); return 0; }
  // A local Discord-compatible IPC fixture exercises the actual pinned client.
  DiscordEventHandlers handlers{};
  std::atomic<bool> fixture_saw_activity{false};
  // Private fixture acknowledges receipt via an inbound event; finish only after
  // it has observed the required activity, including the resend after reconnect.
  static std::atomic<bool>* completed;
  completed = &fixture_saw_activity;
  handlers.joinGame = [](const char*) { completed->store(true); };
  handlers.ready = [](const DiscordUser*) { std::puts("READY"); std::fflush(stdout); };
  handlers.errored = [](int, const char*) { std::puts("ERROR"); std::fflush(stdout); };
  Discord_Initialize(argv[1], &handlers, 0, nullptr);
  model.Context(0x8001, 1);
  auto activity = model.Format();
  DiscordRichPresence presence{};
  presence.details = activity.details.c_str(); presence.state = activity.state.c_str();
  presence.startTimestamp = 1000;
  Discord_UpdatePresence(&presence);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(12);
  while (!fixture_saw_activity && std::chrono::steady_clock::now() < deadline) {
    Discord_RunCallbacks(); std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  std::fputs("IPC fixture polling complete\n", stderr);
  Discord_ClearPresence();
  std::fputs("IPC fixture clear queued\n", stderr);
  std::this_thread::sleep_for(std::chrono::milliseconds(200));
  Discord_Shutdown();
  std::fputs("IPC fixture shutdown complete\n", stderr);
  assert(fixture_saw_activity && "fixture did not acknowledge the expected activity");
  std::puts("Discord IPC PASS");
}
