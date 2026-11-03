// A normal build must link and call the optional pause APIs without game/SDK code.
#include "../src/platform/fh1_map_preview.h"
#include "../src/platform/native_pause_scene.h"
#include <array>
#include <cassert>

// Disabled implementations must never inspect the guest context or memory.
struct PPCContext {};

int main() {
  PPCContext context;
  std::array<uint8_t, 16> memory{};
  memory.fill(0xA5);
  const auto before = memory;
  for (uint32_t screen : {0u, 1u, 0x827D1618u}) {
    assert(!forza::native_pause_scene::Owns(screen));
    assert(!forza::native_pause_scene::Input(context, memory.data()));
    assert(!forza::native_pause_scene::Page(context, memory.data(), screen, true));
    forza::native_pause_scene::AfterBind(context, memory.data(), screen);
    forza::native_pause_scene::Tick(context, memory.data(), screen);
    forza::native_pause_scene::PartyTick(context, memory.data(), screen);
    forza::native_pause_scene::PartyInput(context, memory.data(), screen, 1);
    forza::native_pause_scene::Destroy(context, memory.data(), screen);
    for (bool visible : {false, true}) {
      forza::fh1_map_preview::Update(context, memory.data(), screen, visible);
    }
    forza::fh1_map_preview::Release(context, memory.data());
    assert(!forza::fh1_map_preview::Ready());
    assert(!forza::fh1_map_preview::SetBounds({64, 180, 500, 250}, false));
    assert(memory == before);
  }
}
