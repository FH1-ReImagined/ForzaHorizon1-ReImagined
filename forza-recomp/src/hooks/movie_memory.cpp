// forza - a movie that finds no room in the streaming heap takes physical memory (#262).
//
// Quitting to the title screen (Pause > Quit) tears the session down, and the main menu's
// background movie (1280x720) starts at once. The render thread opens it (sub_82E6C108,
// TU4 sub_82F2DE98) with the movie allocator (0x834B49F8, sub_82E5E9F8) pushed as its
// XMemAlloc heap, and XMediaFacade's video decoder asks for its buffers through XMemAlloc.
// The allocator (sub_82E5B7C8, TU4 sub_82F1D3F0) takes each block of 64 KB and more from heap
// 0x20, "streamingGPU" (88,832 KB, src/platform/game_heap.h), and XMemAlloc (sub_82CC1B10,
// TU4 sub_82D83590) writes the size to address 6 on purpose when it gets nothing. That was the
// crash: the decoder's 2,826,496-byte frame buffer (XMediaFacade sub_88159030 -> sub_8815E468
// -> sub_8815B9F8), on the render thread, 0.42 s into a quit from the festival.
//
// The streamed world fills heap 0x20 in free roam (55 MB on the test save); the quit frees it
// while the menu starts: on the test PC 56 -> 9 MB within 20 ms of the quit, the rest within
// 160 ms, and the movie opened 90 ms after the last free. The game counts on that order. On a
// slower PC (the player's: a laptop iGPU with frames of 200-500 ms, the festival's fuller
// world) the movie can open before the world's blocks are gone, and the heap had no 2.7 MB
// in one piece.
//
// Now a movie block that heap 0x20 cannot give comes from physical memory: XPhysicalAllocEx
// (sub_82A6F2D8, TU4 sub_82B2C348) with the protection and alignment the allocator gives its
// own physical blocks: physical like the heap's, so the GPU can read the movie's planes from
// them. XMemFree (sub_82CC1DA0, TU4 sub_82D83820) and XMemSize (sub_82CC1ED8, TU4
// sub_82D83958) know these blocks, which the game would take for its general heap's (the size
// word before the block), and free them with XPhysicalFree (sub_82CBB348, TU4 sub_82D7CE38).
// Each one is logged with the heap's state, so a recurrence shows in forza.log. When physical
// memory has none either, the game stops as before, with that logged.
// FORZA_MOVIE_FALLBACK=0 switches this off; =always takes every such block from physical
// memory (tests this path).
#include "../platform/fixed_gpu_heap.h"
#include "../platform/game_heap.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/image_info.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

extern const rex::PPCImageInfo PPCImageConfig;

extern "C" REX_FUNC(__imp__sub_82E5B7C8);
extern "C" REX_FUNC(__imp__sub_82CC1DA0);
extern "C" REX_FUNC(__imp__sub_82CC1ED8);
extern "C" REX_FUNC(sub_82A6F2D8);
extern "C" REX_FUNC(sub_82CBB348);

namespace {

namespace heap = forza::game_heap;

constexpr uint32_t kStreamingHeap = 0x20;
constexpr uint32_t kSmallBlock = 0x10000;  // smaller blocks come from the general heap

enum class Mode { kOff, kOnFailure, kAlways };

Mode GetMode() {
  static const Mode mode = [] {
    const char* v = std::getenv("FORZA_MOVIE_FALLBACK");
    if (v && std::strcmp(v, "0") == 0) return Mode::kOff;
    if (v && std::strcmp(v, "always") == 0) return Mode::kAlways;
    return Mode::kOnFailure;
  }();
  return mode;
}

heap::FallbackBlocks& Blocks() {
  static heap::FallbackBlocks blocks;
  return blocks;
}

bool Readable(uint32_t address, uint32_t size) {
  // the executable's image (the heap table lives in its data): its heap answers no queries
  if (address - PPCImageConfig.image_base < PPCImageConfig.image_size &&
      address + size - 1 - PPCImageConfig.image_base < PPCImageConfig.image_size)
    return true;
  auto* kernel = rex::system::kernel_state();
  if (!kernel || !kernel->memory()) return false;
  auto* guest_heap = kernel->memory()->LookupHeap(address);
  uint32_t protect = 0;
  return guest_heap && guest_heap->QueryProtect(address, &protect) &&
         (protect & rex::memory::kMemoryProtectRead) &&
         guest_heap->QueryProtect(address + size - 1, &protect) &&
         (protect & rex::memory::kMemoryProtectRead);
}

bool Read32(uint8_t* base, uint32_t address, uint32_t* value) {
  if (!Readable(address, 4)) return false;
  const uint8_t* p = forza::GuestPtr(base, address);
  *value = uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
  return true;
}

// Heap 0x20's state, read without its lock (the numbers can be a moment old).
std::string StreamingHeap(uint8_t* base) {
  const uint32_t address = heap::HeapOfCategory(FORZA_ADDR(0x832AAC24), kStreamingHeap);
  const heap::Census c = heap::Count(
      address, [base](uint32_t a, uint32_t* v) { return Read32(base, a, v); },
      [base](uint32_t a, uint8_t* v) {
        if (!Readable(a, 1)) return false;
        *v = *forza::GuestPtr(base, a);
        return true;
      });
  std::string name;
  uint32_t text = 0;
  if (Read32(base, address + heap::kName, &text))
    for (uint32_t i = 0; i < 32 && Readable(text + i, 1); ++i) {
      const char ch = char(*forza::GuestPtr(base, text + i));
      if (ch < 32 || ch > 126) break;
      name += ch;
    }
  return fmt::format("heap 0x20 ({}): {} of {} KB in use, {} KB free in {} blocks, the largest "
                     "{} KB{}",
                     name.empty() ? "?" : name, c.used / 1024, c.size / 1024, c.free_bytes / 1024,
                     c.free_blocks, c.largest_free / 1024, c.complete ? "" : " (not all read)");
}

}  // namespace

// The movie allocator's Alloc(this, size, attributes) (vtable 0x8224B768 slot 0).
extern "C" REX_FUNC(sub_82E5B7C8) {
  const uint32_t size = ctx.r4.u32, attributes = ctx.r5.u32;
  const uint64_t lr = ctx.lr;
  const Mode mode = GetMode();
  // A physical block has its own fallback in the allocator (sub_82E5B240).
  const bool eligible = mode != Mode::kOff && size >= kSmallBlock && !heap::IsPhysical(attributes);
  if (!eligible || mode != Mode::kAlways) {
    __imp__sub_82E5B7C8(ctx, base);
    if (ctx.r3.u32 || !eligible) return;
  }
  const std::string state = StreamingHeap(base);
  ctx.r3.u64 = size;
  ctx.r4.u64 = 0xFFFFFFFF;  // anywhere
  ctx.r5.u64 = heap::PhysicalAlignment(attributes);
  ctx.r6.u64 = heap::PhysicalProtect(attributes);
  sub_82A6F2D8(ctx, base);
  ctx.lr = lr;
  const uint32_t block = ctx.r3.u32;
  if (!block) {
    REXLOG_ERROR("FORZA: a movie needs {} bytes (attributes {:08X}): none in the streaming heap "
                 "nor in physical memory, the game stops (#262); {}",
                 size, attributes, state);
    return;
  }
  if (heap::ZeroInitialize(attributes)) std::memset(forza::GuestPtr(base, block), 0, size);
  Blocks().Add(block, size);
  if (mode == Mode::kAlways)
    REXLOG_INFO("FORZA: movie block of {} bytes from physical memory at {:08X}", size, block);
  else
    REXLOG_WARN("FORZA: a movie needs {} bytes (attributes {:08X}) and the streaming heap has "
                "no room: taken from physical memory at {:08X} (#262); {}",
                size, attributes, block, state);
}

// XMemFree(address, attributes).
extern "C" REX_FUNC(sub_82CC1DA0) {
  if (forza::fixed_gpu_heap::Free(ctx.r3.u32)) return;  // #857, fixed_gpu_heap.cpp
  if (Blocks().Take(ctx.r3.u32)) {
    REXLOG_INFO("FORZA: movie block at {:08X} back to physical memory", ctx.r3.u32);
    const uint64_t lr = ctx.lr;
    sub_82CBB348(ctx, base);  // XPhysicalFree(address)
    ctx.lr = lr;
    return;
  }
  __imp__sub_82CC1DA0(ctx, base);
}

// XMemSize(address, attributes).
extern "C" REX_FUNC(sub_82CC1ED8) {
  if (const uint32_t size = forza::fixed_gpu_heap::Size(ctx.r3.u32)) {  // #857
    ctx.r3.u64 = size;
    return;
  }
  if (const uint32_t size = Blocks().Size(ctx.r3.u32)) {
    ctx.r3.u64 = size;
    return;
  }
  __imp__sub_82CC1ED8(ctx, base);
}
