// Compile with the runtime SDK and each FORZA_TITLE_UPDATE value. The native SQL
// helper is stubbed; the production hook, guest byte order and context are real.
#include "../src/hooks/garage_capacity.cpp"
#include <array>
#include <cassert>

namespace {
uint32_t stored = 0;
uint32_t calls = 0;
}

extern "C" REX_FUNC(GarageCount) {
  assert(ctx.r3.u32 == 32);
  ++calls;
  ctx.r3.u64 = stored;
  ctx.r31.u64 = 0xdeadbeef;
  ctx.r4.u64 = 0xfeedface;
  // Changing the descriptor during COUNT must not alter the earlier snapshot.
  base[32 + 75] = 0;
}

int main() {
  std::array<uint8_t, 128> memory{};
  auto write = [&](uint32_t offset, uint32_t value) {
    for (int i = 0; i < 4; ++i) memory[32 + offset + i] = uint8_t(value >> (24 - 8 * i));
  };
  PPCContext ctx{};
  for (uint32_t count : {549u, 550u, 551u, 998u, 999u, 1000u, 1001u, UINT32_MAX}) {
    for (uint32_t pending : {0u, 1u, 2u, 1000u}) {
      write(68, 0x1000);
      write(72, 0x1000 + pending * 8);
      write(76, 0x4000);
      ctx.r3.u64 = 32;
      ctx.r4.u64 = 0x12345678;
      ctx.r31.u64 = 0x87654321;
      stored = count;
      const auto before = calls;
      GarageFull(ctx, memory.data());
      assert(calls == before + 1);
      assert(ctx.r3.u64 == (uint64_t(count) + pending >= 1000 ? 1u : 0u));
      assert(ctx.r4.u64 == 0x12345678);
      assert(ctx.r31.u64 == 0x87654321);
    }
  }
}
