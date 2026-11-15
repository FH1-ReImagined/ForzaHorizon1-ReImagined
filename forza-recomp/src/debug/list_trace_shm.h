// List trace (FORZA_LIST_TRACE): the shared memory between this game's list trace
// (list_trace.cpp) and the GPU plugin's command processor (ReIGlue built with
// REXGLUE_FH1_LIST_TRACE, patch 0640: fh1_list_trace.cpp). Both sit in the game's process; the
// segment is /dev/shm/fh1list-<pid>, created by whichever side maps it first; the plugin removes
// the name at its first report block.
//
// The game registers every buffer a RunCommandBuffer call names (the command buffer's segments,
// by physical address) with what it knows of it: the kind of command buffer, the pass group the
// game was drawing, the command buffer's flags. The command processor looks each indirect buffer
// it runs up by its address, times it and counts it against that class. The game's own counters
// (calls by device, caller, flags, the live header checks) go into a table of keyed counters that
// the plugin reports as deltas.
//
// ReIGlue has its own copy of this file (src/graphics/fh1_list_trace_shm.h): the two must stay the
// same. kVersion changes with the layout; the plugin ignores a segment of another version.
#pragma once

#include <cstdint>

namespace fh1list {

constexpr uint32_t kMagic = 0x464C5401;  // written by the game once its hooks run
constexpr uint32_t kVersion = 1;
constexpr uint32_t kListBits = 16;
constexpr uint32_t kLists = 1u << kListBits;
constexpr uint32_t kCounterBits = 16;
constexpr uint32_t kCounters = 1u << kCounterBits;
constexpr uint32_t kProbe = 64;

// What a registered buffer is (List::cls).
enum : uint8_t {
  kClassNone,      // not registered (D3D's own segments, buffers no RunCommandBuffer names)
  kClassModel,     // a model's list from the game's files: type 9, the files' trailer after it
  kClassRecorded,  // another command buffer of type 9 (recorded by D3D while the game runs)
  kClassType10,    // a command buffer of type 10
  kClassOther,     // any other type
  kClassCount,
};
constexpr uint8_t kGroupNone = 0xFF;  // List::group: pass * 3 + layer, or none

// One registered buffer. The game owns everything up to `calls`, the plugin everything after.
struct List {
  uint32_t phys;  // key: the buffer's physical address, 0 = empty slot (set first, by CAS)
  uint32_t words;
  uint32_t cb;                         // the command buffer that named it last
  uint32_t flags;                      // that command buffer's flags (+108) when it was registered
  uint8_t cls, group, context, ready;  // ready: 1 once the fields above are written
  uint32_t binds;                      // registrations with another command buffer or length
  uint64_t calls;                      // RunCommandBuffer calls that named it (every device)
  uint64_t runs;                       // times the command processor ran it
  uint64_t skipped;                    // INDIRECT_BUFFER packets to it that were predicated off
  uint64_t ticks;                      // decode time inside it (runs without a recorder sync)
  uint64_t hash;                       // its words when the plugin last looked (recorded class)
  uint64_t changes;                    // times those words had changed
};

struct Counter {
  uint64_t key;  // 0 = empty
  uint64_t count;
};

struct Shm {
  uint32_t magic;
  uint32_t version;
  uint64_t size;  // sizeof(Shm)
  List lists[kLists];
  Counter counters[kCounters];
};

inline uint32_t Slot(uint64_t key, uint32_t bits) {
  return uint32_t((key * 0x9E3779B97F4A7C15ull) >> (64 - bits));
}

// The slot of the buffer at physical address phys, or nullptr (read side: no insertion).
inline List* Find(Shm* s, uint32_t phys) {
  if (!phys)
    return nullptr;
  const uint32_t h = Slot(phys, kListBits);
  for (uint32_t i = 0; i < kProbe; ++i) {
    List& e = s->lists[(h + i) & (kLists - 1)];
    const uint32_t p = __atomic_load_n(&e.phys, __ATOMIC_ACQUIRE);
    if (p == phys)
      return __atomic_load_n(&e.ready, __ATOMIC_ACQUIRE) ? &e : nullptr;
    if (!p)
      return nullptr;
  }
  return nullptr;
}

}  // namespace fh1list
