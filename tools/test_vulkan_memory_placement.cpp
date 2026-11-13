// Where the Vulkan backend puts new memory on integrated GPUs (patch 0138, #268): the policy
// in src/graphics/vulkan/fh1_memory_placement.h of the patched SDK source.
// Build with the patched SDK source's src/graphics/vulkan on the include path:
//   c++ -std=c++17 -I ~/rexglue-sdk-src-forza/sdk/src/graphics/vulkan
//       tools/test_vulkan_memory_placement.cpp -o test_vulkan_memory_placement
#include "fh1_memory_placement.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <initializer_list>

namespace p = rex::graphics::vulkan::fh1_memory_placement;

#define CHECK(x)                                                    \
  do {                                                              \
    if (!(x)) {                                                     \
      std::fprintf(stderr, "memory placement: FAIL: %s\n", #x);    \
      std::exit(1);                                                 \
    }                                                               \
  } while (false)

namespace {

constexpr uint64_t kMB = uint64_t(1) << 20;
constexpr uint64_t kGB = uint64_t(1) << 30;
constexpr uint32_t DL = p::kPropertyDeviceLocal, HV = p::kPropertyHostVisible,
                   HC = p::kPropertyHostCoherent, CACHED = p::kPropertyHostCached;
constexpr uint32_t kAmdCoherent = 0x40, kAmdUncached = 0x80, kLazy = 0x10;

struct Builder {
  p::MemoryInfo m;
  explicit Builder(uint32_t device_type) { m.device_type = device_type; }
  Builder& Heap(uint64_t size, bool device_local) {
    m.heap_size[m.heap_count] = size;
    m.heap_flags[m.heap_count] = device_local ? p::kHeapDeviceLocal : 0;
    ++m.heap_count;
    return *this;
  }
  Builder& Type(uint32_t heap, uint32_t flags) {
    m.type_heap[m.type_count] = heap;
    m.type_flags[m.type_count] = flags;
    ++m.type_count;
    return *this;
  }
};

// A Vega APU (Ryzen 5000/7000U, device 0x1638, 0x164C or 0x15E7 as in #268) on AMD's Windows
// legacy driver (Vulkan 1.3.260, vulkan.gpuinfo.org): the 512 MB carve-out split into a 256 MB
// device-local heap and a 256 MB host-visible device-local heap, budgets 95% of each (486 MB in
// all, #268's log).
p::MemoryInfo VegaApuWindows() {
  return Builder(p::kDeviceTypeIntegrated)
      .Heap(256 * kMB, true)
      .Heap(7 * kGB, false)
      .Heap(256 * kMB, true)
      .Type(0, DL)
      .Type(1, HV | HC)
      .Type(2, DL | HV | HC)
      .Type(1, HV | HC | CACHED)
      .Type(0, DL | kAmdCoherent | kAmdUncached)
      .Type(1, HV | HC | kAmdCoherent | kAmdUncached)
      .m;
}

// One 512 MB device-local heap holding both device-local types.
p::MemoryInfo OneHeapApu() {
  return Builder(p::kDeviceTypeIntegrated)
      .Heap(512 * kMB, true)
      .Heap(14 * kGB, false)
      .Type(0, DL)
      .Type(1, HV | HC)
      .Type(0, DL | HV | HC)
      .Type(1, HV | HC | CACHED)
      .m;
}

// A Steam Deck on RADV: the shared memory reported as two thirds device-local, one third GTT.
p::MemoryInfo RadvApu() {
  return Builder(p::kDeviceTypeIntegrated)
      .Heap(8 * kGB + 512 * kMB, true)
      .Heap(4 * kGB + 256 * kMB, false)
      .Type(0, DL)
      .Type(1, HV | HC)
      .Type(0, DL | HV | HC)
      .Type(1, HV | HC | CACHED)
      .m;
}

// This test machine: RADV, RX 9060 XT (the system heap first, resizable BAR).
p::MemoryInfo RadvDiscrete() {
  return Builder(p::kDeviceTypeDiscrete)
      .Heap(15 * kGB, false)
      .Heap(15 * kGB, true)
      .Type(1, DL)
      .Type(1, DL)
      .Type(0, HV | HC)
      .Type(1, DL | HV | HC)
      .Type(1, DL | HV | HC)
      .Type(0, HV | HC | CACHED)
      .Type(0, HV | HC | CACHED)
      .m;
}

p::HeapUse Use3(uint64_t u0, uint64_t b0, uint64_t u1, uint64_t b1, uint64_t u2, uint64_t b2) {
  p::HeapUse u;
  u.usage[0] = u0, u.budget[0] = b0;
  u.usage[1] = u1, u.budget[1] = b1;
  u.usage[2] = u2, u.budget[2] = b2;
  return u;
}

}  // namespace

int main() {
  // The layouts.
  {
    const p::Layout l = p::Describe(VegaApuWindows());
    CHECK(l.integrated);
    CHECK(l.device_local_types == 0b101);  // not the AMD device-coherent type 4
    CHECK(l.device_local_heaps == 0b101);
    CHECK(l.device_local_bytes == 512 * kMB);
    CHECK(l.system_heap == 1);
    CHECK(l.system_types == 0b10);  // write-combined: not the device-coherent type 5
    CHECK(l.system_types_cached == 0b1000);
    // Two device-local tiers: the plain heap, then the host-visible one.
    CHECK(l.tier_count == 2);
    CHECK(l.tier_types[0] == 0b1 && l.tier_heap[0] == 0);
    CHECK(l.tier_types[1] == 0b100 && l.tier_heap[1] == 2);
  }
  {
    const p::Layout l = p::Describe(OneHeapApu());
    CHECK(l.integrated && l.device_local_heaps == 0b1);
    CHECK(l.tier_count == 2 && l.tier_types[0] == 0b1 && l.tier_types[1] == 0b100);
    CHECK(l.tier_heap[0] == 0 && l.tier_heap[1] == 0);
  }
  {
    const p::Layout l = p::Describe(RadvDiscrete());
    CHECK(!l.integrated);  // discrete GPUs keep everything device-local
    CHECK(l.can_move());   // (the test cap still can move memory there)
    CHECK(l.system_heap == 0);
    CHECK(l.device_local_heaps == 0b10);
    CHECK(l.system_types == 0b100);
    CHECK(l.system_types_cached == 0b1100000);
    CHECK(l.device_local_types == 0b11011);
    CHECK(l.tier_count == 2 && l.tier_types[0] == 0b11 && l.tier_types[1] == 0b11000);
  }
  // RADV's APUs (Steam Deck, BC-250, Linux laptops): the device-local heap is not small next
  // to the system heap, so nothing changes there, the render target trim included.
  CHECK(!p::Describe(RadvApu()).integrated);
  // A discrete GPU with little VRAM: still discrete.
  CHECK(!p::Describe(Builder(p::kDeviceTypeDiscrete)
                         .Heap(2 * kGB, true)
                         .Heap(32 * kGB, false)
                         .Type(0, DL)
                         .Type(1, HV | HC)
                         .m)
             .integrated);
  // An integrated GPU with a single (device-local) heap, as Intel's: nothing to move to.
  {
    const p::Layout l = p::Describe(Builder(p::kDeviceTypeIntegrated)
                                        .Heap(16 * kGB, true)
                                        .Type(0, DL)
                                        .Type(0, DL | HV | HC)
                                        .Type(0, DL | HV | HC | CACHED)
                                        .m);
    CHECK(!l.integrated && !l.can_move());
  }
  // A carve-out counts as small up to half the system heap: 1 GB of 8 GB yes, 2 GB of 3.5 GB
  // no; another device type likewise; a CPU device never.
  {
    Builder small(p::kDeviceTypeIntegrated);
    small.Heap(1 * kGB, true).Heap(8 * kGB, false).Type(0, DL).Type(1, HV | HC);
    CHECK(p::Describe(small.m).integrated);
    Builder large(p::kDeviceTypeIntegrated);
    large.Heap(2 * kGB, true).Heap(3 * kGB + 512 * kMB, false).Type(0, DL).Type(1, HV | HC);
    CHECK(!p::Describe(large.m).integrated);
    Builder other(0);
    other.Heap(1 * kGB, true).Heap(8 * kGB, false).Type(0, DL).Type(1, HV | HC);
    CHECK(p::Describe(other.m).integrated);
    Builder cpu(p::kDeviceTypeCpu);
    cpu.Heap(1 * kGB, true).Heap(8 * kGB, false).Type(0, DL).Type(1, HV | HC);
    CHECK(!p::Describe(cpu.m).integrated);
  }
  // A system heap whose only type is lazily allocated is no place to move to.
  CHECK(!p::Describe(Builder(p::kDeviceTypeIntegrated)
                         .Heap(512 * kMB, true)
                         .Heap(8 * kGB, false)
                         .Type(0, DL)
                         .Type(1, kLazy)
                         .m)
             .can_move());
  // The largest of two system heaps.
  {
    const p::Layout l = p::Describe(Builder(p::kDeviceTypeIntegrated)
                                        .Heap(256 * kMB, false)
                                        .Heap(512 * kMB, true)
                                        .Heap(8 * kGB, false)
                                        .Type(1, DL)
                                        .Type(0, HV | HC)
                                        .Type(2, HV | HC)
                                        .m);
    CHECK(l.system_heap == 2 && l.system_types == 0b100);
  }

  // The budget the code sees: the driver's, or at most the test cap.
  CHECK(p::CappedBudget(15 * kGB, 0) == 15 * kGB);
  CHECK(p::CappedBudget(15 * kGB, 486) == 486 * kMB);
  CHECK(p::CappedBudget(300 * kMB, 486) == 300 * kMB);
  CHECK(p::CappedBudget(0, 486) == 486 * kMB);
  // Per heap: the device-local budgets scaled down to add up to the cap; the system heap kept.
  {
    const p::Layout l = p::Describe(VegaApuWindows());
    p::HeapUse u = Use3(0, 243 * kMB, 0, 6 * kGB, 0, 243 * kMB);
    p::ApplyCap(l, u, 300);
    CHECK(u.budget[0] == 150 * kMB && u.budget[2] == 150 * kMB && u.budget[1] == 6 * kGB);
    u = Use3(0, 243 * kMB, 0, 6 * kGB, 0, 243 * kMB);
    p::ApplyCap(l, u, 600);  // above the driver's: unchanged
    CHECK(u.budget[0] == 243 * kMB && u.budget[2] == 243 * kMB);
    u = Use3(0, 0, 0, 0, 0, 0);  // no budget reported: the cap by heap size
    p::ApplyCap(l, u, 486);
    CHECK(u.budget[0] == 243 * kMB && u.budget[2] == 243 * kMB && u.budget[1] == 0);
    const p::Layout r = p::Describe(RadvDiscrete());
    p::HeapUse v;
    v.budget[0] = 15 * kGB, v.budget[1] = 15 * kGB;
    p::ApplyCap(r, v, 486);
    CHECK(v.budget[1] == 486 * kMB && v.budget[0] == 15 * kGB);
  }

  // VMA's new blocks: an eighth of a heap up to 1 GB, else 256 MB.
  CHECK(p::AllocatorBlockBytes(256 * kMB) == 32 * kMB);
  CHECK(p::AllocatorBlockBytes(512 * kMB) == 64 * kMB);
  CHECK(p::AllocatorBlockBytes(1 * kGB) == 128 * kMB);
  CHECK(p::AllocatorBlockBytes(15 * kGB) == 256 * kMB);
  CHECK(p::AllocatorBlockBytes(0) == 256 * kMB);

  // Shares of a heap's budget: 80 % is 388.8 MB of 486 MB, 95 % 461.7 MB, 50 % 243 MB; the
  // defaults (#268, a device lost with the carve-out 94 % full) keep textures out of
  // device-local memory and the rest at half the budget.
  {
    const uint64_t budget = 486 * kMB;
    CHECK(p::Fits(80, 300 * kMB, 64 * kMB, budget));
    CHECK(!p::Fits(80, 330 * kMB, 64 * kMB, budget));
    CHECK(p::Fits(95, 330 * kMB, 64 * kMB, budget));
    CHECK(p::Fits(95, 440 * kMB, 16 * kMB, budget));
    CHECK(!p::Fits(95, 450 * kMB, 16 * kMB, budget));
    CHECK(p::Fits(80, 100 * kGB, 1 * kGB, 0));  // no budget known
    CHECK(p::kTexturePercent == 0 && p::kHotPercent == 50);
    CHECK(!p::Fits(p::kTexturePercent, 0, 1, budget));
    CHECK(p::Fits(p::kHotPercent, 200 * kMB, 43 * kMB, budget));
    CHECK(!p::Fits(p::kHotPercent, 200 * kMB, 44 * kMB, budget));
  }

  // Each device-local heap against its own budget (the review case: heap 0 full, heap 2
  // empty, 250 of 486 MB pooled, which pooled figures would call 51 %).
  {
    const p::Layout l = p::Describe(VegaApuWindows());
    const p::HeapUse u = Use3(240 * kMB, 243 * kMB, 900 * kMB, 6 * kGB, 10 * kMB, 243 * kMB);
    CHECK(p::FittingHeaps(l, 95, u, 16 * kMB, false) == 0b100);
    // A texture counts VMA's 32 MB block of a 256 MB heap: 10 + 32 MB fits 80 % of 243 MB.
    CHECK(p::FittingHeaps(l, 80, u, 1 * kMB, true) == 0b100);
    CHECK(p::FittingHeaps(l, 0, u, 1 * kMB, true) == 0);
    CHECK(!p::HeapOverBudget(l, u));
    CHECK(p::HeapOverBudget(l, Use3(243 * kMB, 243 * kMB, 0, 6 * kGB, 0, 243 * kMB)));
    // The order: the host-visible device-local heap, system memory, then heap 0.
    p::Order o = p::Choose(l, 0xFF, 0b100);
    CHECK(!o.system_first && o.count == 4);
    CHECK(o.masks[0] == 0b100 && o.masks[1] == 0b10 && o.masks[2] == 0b1000 && o.masks[3] == 0b1);
    // Both full: system memory first, then the device-local tiers in their order.
    o = p::Choose(l, 0xFF, 0);
    CHECK(o.system_first && o.count == 4);
    CHECK(o.masks[0] == 0b10 && o.masks[1] == 0b1000 && o.masks[2] == 0b1 && o.masks[3] == 0b100);
    // Both with room: plain device-local first.
    o = p::Choose(l, 0xFF, 0b101);
    CHECK(o.masks[0] == 0b1 && o.masks[1] == 0b100 && o.masks[2] == 0b10);
    // An image that takes only device-local types stays there.
    o = p::Choose(l, 0b101, 0);
    CHECK(!o.system_first && o.count == 2 && o.masks[0] == 0b1 && o.masks[1] == 0b100);
    // One that takes only the cached system type and type 0: that, then device-local.
    o = p::Choose(l, 0b1001, 0);
    CHECK(o.system_first && o.count == 2 && o.masks[0] == 0b1000 && o.masks[1] == 0b1);
    // The AMD-specific types are never chosen.
    o = p::Choose(l, 0b110000, 0);
    CHECK(o.count == 0);
    CHECK(p::LowestType(0b1000) == 3 && p::LowestType(0) == UINT32_MAX);
  }

  // A session on the Vega APU with the default shares: textures, render targets and guest
  // memory pages arrive; textures all go to system memory, each device-local heap stays within
  // half its budget (plus one allocation), the host-visible heap is used, and everything is
  // placed somewhere.
  {
    const p::Layout l = p::Describe(VegaApuWindows());
    p::HeapUse u = Use3(60 * kMB, 243 * kMB, 0, 6 * kGB, 4 * kMB, 243 * kMB);  // the driver's own
    uint64_t texture_room[p::kMaxHeaps] = {};  // free room in VMA's blocks per heap
    uint32_t moved_textures = 0, moved_hot = 0;
    bool used_heap2 = false;
    for (int i = 0; i < 600; ++i) {
      const bool texture = i % 3 != 0;
      const uint64_t size = texture ? 6 * kMB : (i % 2 ? 16 * kMB : 4 * kMB);
      const uint32_t percent = texture ? p::kTexturePercent : p::kHotPercent;
      const p::Order o = p::Choose(l, 0xFF, p::FittingHeaps(l, percent, u, size, texture));
      CHECK(o.count > 0);
      const uint32_t type = p::LowestType(o.masks[0]);
      const uint32_t heap = type == 0 ? 0 : type == 2 ? 2 : 1;
      if (heap != 1) {
        if (texture) {
          if (texture_room[heap] < size) {  // VMA makes a block
            const uint64_t block = p::AllocatorBlockBytes(l.heap_bytes[heap]);
            u.usage[heap] += block;
            texture_room[heap] += block;
          }
          texture_room[heap] -= size;
          CHECK(false);  // textures stay out of device-local memory
        } else {
          u.usage[heap] += size;
        }
        used_heap2 |= heap == 2;
      } else {
        u.usage[1] += size;
        ++(texture ? moved_textures : moved_hot);
      }
      for (uint32_t h : {0u, 2u}) {
        CHECK(u.usage[h] * 100 <= u.budget[h] * p::kHotPercent + 16 * kMB * 100);
      }
    }
    CHECK(moved_textures == 400 && moved_hot > 0 && used_heap2);
    CHECK(!p::HeapOverBudget(l, u));
    CHECK(u.usage[0] + u.usage[2] <= 243 * kMB + 16 * kMB);
  }
  // The upload rings' host-visible device-local types (#858, vulkan_rebar_uploads).
  {
    // This machine with resizable BAR: the two DL|HV|HC types of the 15 GB video memory heap.
    CHECK(p::DeviceLocalUploadTypes(RadvDiscrete()) == 0b11000);
    CHECK(p::LowestType(p::DeviceLocalUploadTypes(RadvDiscrete())) == 3);
    // RADV without resizable BAR: the 256 MB window is a heap of its own: system memory.
    const p::MemoryInfo no_rebar = Builder(p::kDeviceTypeDiscrete)
                                       .Heap(15 * kGB, false)
                                       .Heap(15 * kGB + 768 * kMB, true)
                                       .Heap(256 * kMB, true)
                                       .Type(1, DL)
                                       .Type(0, HV | HC)
                                       .Type(2, DL | HV | HC)
                                       .Type(0, HV | HC | CACHED)
                                       .m;
    CHECK(p::DeviceLocalUploadTypes(no_rebar) == 0);
    // NVIDIA with resizable BAR (the type on the video memory heap), a host-cached and an AMD
    // device-coherent device-local type, which are never chosen, and one that is not coherent.
    const p::MemoryInfo nvidia = Builder(p::kDeviceTypeDiscrete)
                                     .Heap(8 * kGB, true)
                                     .Heap(16 * kGB, false)
                                     .Type(0, DL)
                                     .Type(1, HV | HC)
                                     .Type(0, DL | HV | HC | CACHED)
                                     .Type(0, DL | HV | HC | kAmdCoherent)
                                     .Type(0, DL | HV)
                                     .Type(0, DL | HV | HC)
                                     .m;
    CHECK(p::DeviceLocalUploadTypes(nvidia) == 0b100000);
    // Integrated GPUs keep their rings in system memory, whatever their heaps.
    CHECK(p::DeviceLocalUploadTypes(RadvApu()) == 0);
    CHECK(p::DeviceLocalUploadTypes(OneHeapApu()) == 0);
    CHECK(p::DeviceLocalUploadTypes(VegaApuWindows()) == 0);
  }
  std::puts("memory placement: ok");
  return 0;
}
