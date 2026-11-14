// Native renderer M4 (src/platform/native_list.h): the model list converter, on lists made up
// here in the shapes the game's passes have (no game data): the depth and shadow passes, the two
// 256x256 passes, the scene pass with its tile blocks, the same list untiled, a draw that sets
// nothing, and every refusal. Each list runs beside a plain PM4 interpreter
// (native_list_reference.h) for every tile's bin select, without tiling and with a bin mask that
// lets no predicated packet through. Every part of a list handed to the converter ends at a page
// that cannot be read (Part), so a read past a part stops the test. An optimizing compiler drops
// a read whose value goes unused, so the test is also to be run unoptimized (CI does both):
//   g++ -std=c++20 -O2 -I forza-recomp/src forza-recomp/tests/native_list_test.cpp
//   g++ -std=c++20 -O0 -g -fsanitize=address,undefined -D_GLIBCXX_SANITIZE_VECTOR ... (the same)
#include "platform/native_list.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <set>
#include <string_view>
#include <utility>
#include <vector>

#if defined(__unix__) || defined(__APPLE__)
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#define NATIVE_LIST_TEST_FENCE 1
#endif

#include "native_list_reference.h"

namespace nl = forza::native_list;
namespace ref = forza::native_list::reference;

#define CHECK(x)                                                             \
  do {                                                                       \
    if (!(x)) {                                                              \
      std::fprintf(stderr, "native list: FAIL line %d: %s\n", __LINE__, #x); \
      std::exit(1);                                                          \
    }                                                                        \
  } while (false)

namespace {

constexpr uint32_t kPlaceholder = 0xEEEEEEE0u;  // what a place holds before any fixup

std::vector<uint32_t> Values(uint32_t count, uint32_t seed) {
  std::vector<uint32_t> v(count);
  for (uint32_t i = 0; i < count; ++i) v[i] = seed * 0x01000193u + i * 0x9E3779B1u + 0x1000;
  return v;
}

// A table entry: the list dword, the sub-code, and a value word if it has one.
struct Entry {
  uint32_t dword, sub;
  int64_t value = -1;
};

// A part of a list (its header, a table, its PM4) as the converter gets it: exactly its bytes,
// and nothing readable after them. The bytes end where a page without access begins, so a read
// one word past the part ends the test with or without a sanitizer; an empty part's address is
// that page. Without mmap the part is a block of exactly its size, which a sanitizer watches.
class Part {
 public:
  Part() : Part(0) {}
  explicit Part(size_t size) : size_(size) {
#ifdef NATIVE_LIST_TEST_FENCE
    const size_t page = size_t(sysconf(_SC_PAGESIZE));
    const size_t body = (size + page - 1) / page * page;
    mapped_ = body + page;
    void* map = mmap(nullptr, mapped_, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    CHECK(map != MAP_FAILED);
    map_ = static_cast<uint8_t*>(map);
    CHECK(mprotect(map_ + body, page, PROT_NONE) == 0);
    data_ = map_ + body - size;
#else
    map_ = data_ = new uint8_t[size]();
#endif
  }
  Part(const Part& other) : Part(other.size_) {
    if (size_) std::memcpy(data_, other.data_, size_);
  }
  Part& operator=(const Part& other) {
    Part copy(other);
    std::swap(map_, copy.map_);
    std::swap(mapped_, copy.mapped_);
    std::swap(data_, copy.data_);
    std::swap(size_, copy.size_);
    return *this;
  }
  ~Part() {
#ifdef NATIVE_LIST_TEST_FENCE
    munmap(map_, mapped_);
#else
    delete[] map_;
#endif
  }
  uint8_t* data() { return data_; }
  const uint8_t* data() const { return data_; }
  size_t size() const { return size_; }
  uint8_t& operator[](size_t i) { return data_[i]; }
  bool operator==(const Part& other) const {
    return size_ == other.size_ && (size_ == 0 || std::memcmp(data_, other.data_, size_) == 0);
  }
  bool operator!=(const Part& other) const { return !(*this == other); }

 private:
  uint8_t* map_ = nullptr;
  uint8_t* data_ = nullptr;
  size_t mapped_ = 0, size_ = 0;
};

Part Guest(const std::vector<uint32_t>& words) {
  Part bytes(words.size() * 4);
  for (size_t i = 0; i < words.size(); ++i) ref::StoreWord(bytes.data(), i, words[i]);
  return bytes;
}

// The fence itself: a process that reads the byte after a part does not get past it.
bool FenceHolds(size_t size) {
#ifdef NATIVE_LIST_TEST_FENCE
  const Part part(size);
  const pid_t child = fork();
  CHECK(child >= 0);
  if (child == 0) {
    close(STDERR_FILENO);  // a sanitizer build would report the fault at length
    volatile uint8_t byte = part.data()[part.size()];
    (void)byte;
    _exit(0);
  }
  int status = 0;
  CHECK(waitpid(child, &status, 0) == child);
  return !(WIFEXITED(status) && WEXITSTATUS(status) == 0);
#else
  (void)size;
  return true;
#endif
}

struct Built {
  Part header, table, area_c, pm4;
  nl::Input input() const {
    nl::Input in;
    in.header = header.data();
    in.patch_table = table.data();
    in.patch_words = uint32_t(table.size() / 4);
    in.area_c = area_c.data();
    in.area_c_words = uint32_t(area_c.size() / 4);
    in.pm4 = pm4.data();
    in.pm4_words = uint32_t(pm4.size() / 4);
    return in;
  }
};

// A list being written: packets, then the places of its two tables.
struct List {
  std::vector<uint32_t> pm4, table, area_c;
  uint32_t resource = 0x00400009, flags = 0x86;
  uint32_t segment_next = 0, segment_count = 1;
  int32_t segment_words = -1, patch_words = -1;  // the header's, when they are to differ

  uint32_t at() const { return uint32_t(pm4.size()); }
  // Each returns the dword of the packet's first value.
  uint32_t Regs(uint32_t first, const std::vector<uint32_t>& values) {
    pm4.push_back(uint32_t(values.size() - 1) << 16 | first);
    pm4.insert(pm4.end(), values.begin(), values.end());
    return at() - uint32_t(values.size());
  }
  uint32_t Mark(const std::vector<uint32_t>& values) {
    return Regs(0x8000 | nl::kRegScratch6, values);
  }
  void Pad(uint32_t count) { pm4.insert(pm4.end(), count, 0x80000000u); }
  uint32_t Type3(uint32_t opcode, const std::vector<uint32_t>& values, bool predicated = false) {
    pm4.push_back(0xC0000000u | uint32_t(values.size() - 1) << 16 | opcode << 8 |
                  (predicated ? 1 : 0));
    pm4.insert(pm4.end(), values.begin(), values.end());
    return at() - uint32_t(values.size());
  }
  uint32_t Shader(bool pixel, uint32_t size) {
    return Type3(nl::kPm4ImLoad, {kPlaceholder | pixel, size});
  }
  uint32_t Literals(uint32_t target) {
    return Type3(nl::kPm4LoadAluConstant, {kPlaceholder, target, nl::kLiteralWords});
  }
  uint32_t Draw(uint32_t initiator, uint32_t size_word, bool predicated = true) {
    return Type3(nl::kPm4DrawIndx, {0, initiator, kPlaceholder, size_word}, predicated);
  }
  // The three tiles' windows: made-up offsets and scissors.
  void TileBlock(uint32_t seed = 0) {
    Type3(nl::kPm4SetBinMaskHi, {0});
    for (uint32_t tile = 0; tile < nl::kTiles; ++tile) {
      Type3(nl::kPm4SetBinMaskLo, {nl::kTileMasks[tile]});
      Type3(0x2D, {nl::kTileWindowTarget, 0x7000 + tile + seed, 0x8000 + tile, 0x9000 + tile},
            true);
    }
    Type3(nl::kPm4SetBinMaskLo, {0xFFFFFFFFu});
    Type3(nl::kPm4SetBinMaskHi, {0});
  }

  static void Add(std::vector<uint32_t>* to, const Entry& e, bool more, bool patch_table) {
    to->push_back((patch_table ? 0x80000000u : 0) | (more ? 0x40000000u : 0) |
                  (e.value >= 0 ? 2u : 1u) << 26 | e.dword << 6 | e.sub);
    if (e.value >= 0) to->push_back(uint32_t(e.value));
  }
  // One fixup's places in the patch table.
  void Chain(const std::vector<Entry>& entries) {
    for (size_t i = 0; i < entries.size(); ++i) {
      Add(&table, entries[i], i + 1 < entries.size(), true);
    }
  }
  // A vertex or index buffer's place: in the patch table and in area C.
  void Buffer(const Entry& e) {
    Add(&table, e, false, true);
    Add(&area_c, e, false, false);
  }

  // The table's entries that name a dword below `words`; each chain still ends.
  static std::vector<uint32_t> Below(const std::vector<uint32_t>& table, uint32_t words) {
    std::vector<uint32_t> out;
    size_t last = SIZE_MAX;  // the chain's last entry kept
    for (size_t i = 0; i < table.size();) {
      const uint32_t entry = table[i], length = (entry >> 26) & 0xF;
      if (((entry >> 6) & 0xFFFFF) < words) {
        last = out.size();
        out.insert(out.end(), table.begin() + i, table.begin() + i + length);
      }
      if (!(entry & 0x40000000u)) {
        if (last != SIZE_MAX) out[last] &= ~0x40000000u;
        last = SIZE_MAX;
      }
      i += length;
    }
    return out;
  }
  // The list's first `words` words, with the places that are in them.
  List Prefix(uint32_t words) const {
    List l = *this;
    l.pm4.resize(words);
    l.table = Below(table, words);
    l.area_c = Below(area_c, words);
    return l;
  }

  Built Build() const {
    std::vector<uint32_t> header(nl::kHeaderBytes / 4, 0);
    header[nl::kHeaderResource / 4] = resource;
    for (uint32_t i = 0; i < nl::kMaskWords; ++i) {
      header[nl::kHeaderMasks / 4 + i] = 0xFFFF0000u + i;
    }
    header[nl::kHeaderFlags / 4] = flags;
    header[nl::kHeaderPatchWords / 4] =
        patch_words >= 0 ? uint32_t(patch_words) : uint32_t(table.size());
    header[nl::kHeaderSegments / 4] = segment_next;
    header[nl::kHeaderSegments / 4 + 1] = segment_count;
    header[nl::kHeaderSegments / 4 + 2] =
        0x81000000u | (segment_words >= 0 ? uint32_t(segment_words) : uint32_t(pm4.size()));
    Built b;
    b.header = Guest(header);
    b.table = Guest(table);
    b.area_c = Guest(area_c);
    b.pm4 = Guest(pm4);
    return b;
  }
};

constexpr uint32_t kStrip = 6, kTriangles = 4;
uint32_t Initiator(uint32_t primitive, uint32_t indices, bool wide = false) {
  return indices << 16 | (wide ? 0x800u : 0) | primitive;
}

// One draw of a depth-only pass (passes 0 and 6): vertex constants, the vertex shader, state,
// a texture, the vertex buffer, the draw.
void DepthDraw(List& l, uint32_t seed) {
  l.Pad(3);
  l.Regs(0x4000, Values(16, seed));
  l.Mark({0x00010011, seed, 0, 0});
  l.Chain({{l.Shader(false, 20 + seed), 0, 0x40}});
  l.Regs(0x2200, Values(4, seed + 1));
  l.Regs(0x2205, Values(1, seed + 2));
  l.Regs(0x2208, Values(4, seed + 3));
  l.Regs(0x2180, Values(2, seed + 4));
  l.Regs(0x2103, Values(1, seed + 5));
  l.Regs(0x210C, Values(2, seed + 6));
  l.Chain({{l.Regs(nl::kRegFetch, Values(6, seed + 7)), 0}});
  l.Pad(1);
  l.Buffer({l.Regs(nl::kRegVertexFetch, Values(6, seed + 8)), 4, 0});
  l.Regs(0x5000, {0, 0x00025000, 0});
  l.Regs(0x2102, {0});
  l.Buffer({l.Draw(Initiator(kStrip, 30 + seed), 0x40000000u | (30 + seed)), 0, 0});
  l.Regs(nl::kRegScratch7, {l.at() - 5});
}

List DepthList(bool second_draw_sets_nothing = false) {
  List l;
  DepthDraw(l, 1);
  if (second_draw_sets_nothing) {
    // The rest of a model too large for one draw: only the index offset changes.
    l.Regs(0x2102, {31});
    l.Buffer({l.Draw(Initiator(kStrip, 9, true), 0x80000000u | 9), 0, 0});
  } else {
    DepthDraw(l, 2);
  }
  l.Regs(nl::kRegWindowOffset, {0, 0, 0x02D00500});
  return l;
}

// One draw of a colour pass (passes 1, 2 and 4): both shaders with their literal constants, the
// pixel constants with the block a fixup fills, the render target and the viewport.
void ColourDraw(List& l, uint32_t seed, std::vector<Entry>* surfaces,
                std::vector<Entry>* viewport) {
  l.Pad(3);
  l.Regs(0x4000, Values(32, seed));
  l.Pad(3);
  l.Regs(0x4400, Values(32, seed + 1));
  const uint32_t block = l.Regs(0x45D0, Values(48, seed + 2));
  // c116 alone, then c120-c123 and c124-c127; c117-c119 stay the file's.
  l.Chain({{block, 0, 0x10}, {block + 16, 0, 0x40}, {block + 32, 0, 0x440}});
  l.Mark({0x00010012, seed, 0, 0});
  std::vector<Entry> pixel = {{l.Literals(0x7F0), 1, 0}};
  pixel.push_back({l.Shader(true, 40 + seed), 0, 0x40});
  l.Chain(pixel);
  std::vector<Entry> vertex = {{l.Literals(0x3F0), 1, 0}};
  l.Mark({0x00010011, seed, 0, 0});
  vertex.push_back({l.Shader(false, 50 + seed), 0, 0x40});
  l.Chain(vertex);
  surfaces->push_back({l.Regs(0x2200, Values(4, seed + 3)) + 3, nl::kSurfaceHiZ});
  l.Regs(0x2205, Values(1, seed + 4));
  l.Regs(0x2209, Values(3, seed + 5));
  l.Regs(0x2180, Values(3, seed + 6));
  if (viewport) {
    const uint32_t target = l.Regs(0x2000, Values(3, seed + 7));
    surfaces->push_back({target, nl::kSurfaceInfo});
    surfaces->push_back({target + 1, nl::kSurfaceColorInfo});
    surfaces->push_back({target + 2, nl::kSurfaceDepthInfo});
    l.Regs(0x2103, Values(2, seed + 8));
    const uint32_t scale = l.Regs(0x210F, Values(6, seed + 9));
    for (uint32_t i = 0; i < 6; ++i) viewport->push_back({scale + i, i});
  }
  l.Regs(0x210C, Values(2, seed + 10));
  const uint32_t textures = l.Regs(nl::kRegFetch + 5 * 6, Values(18, seed + 11));
  l.Chain({{textures, 0}});
  l.Chain({{textures + 12, 0}});  // fetch constant 6 has no fixup: its words are the file's
  l.Pad(1);
  l.Buffer({l.Regs(nl::kRegVertexFetch, Values(6, seed + 12)), 4, 0});
  l.Regs(0x5000, {0, 0x00025000, 0});
  if (viewport) surfaces->push_back({l.Regs(0x2301, Values(1, seed + 13)), nl::kSurfaceAaConfig});
  l.Regs(0x2380, Values(4, seed + 14));
  l.Regs(0x2102, {0});
  l.Buffer({l.Draw(Initiator(seed & 1 ? kStrip : kTriangles, 60 + seed), 0x40000000u | (60 + seed)),
            0, 0});
  l.Regs(nl::kRegScratch7, {l.at() - 5});
}

// The two surface marks every colour pass starts with: the size word is a fixup's.
void SurfaceMarks(List& l, std::vector<Entry>* surfaces) {
  surfaces->push_back({l.Mark({0x00010017, 0, 0x00AA00BB}) + 1, nl::kSurfaceMarkSize, 0});
  surfaces->push_back({l.Mark({0x00010017, 4, 0x00AA00BB}) + 1, nl::kSurfaceMarkSize, 4});
}

// A 256x256 pass (passes 1 and 2): the scissor set, the marks, the scissor again, the draws,
// the scissor restored.
List SmallTargetList() {
  List l;
  std::vector<Entry> surfaces, viewport, scissor;
  const auto window = [&] {
    const uint32_t at = l.Regs(nl::kRegWindowOffset, {0, 0, 0x01000100});
    scissor.push_back({at + 1, 0});
    scissor.push_back({at + 2, 1});
  };
  window();
  SurfaceMarks(l, &surfaces);
  window();
  ColourDraw(l, 3, &surfaces, &viewport);
  ColourDraw(l, 4, &surfaces, nullptr);
  window();
  l.Chain(surfaces);
  l.Chain(viewport);
  l.Chain(scissor);
  return l;
}

// The scene pass (pass 4): a tile block, the marks, the block again, the draws, the block once
// more. Its surface chain starts with the modifier entry, which names dword 0 and writes nothing.
// `padding` words in front make it a list that starts otherwise (no model has one).
List SceneListAfter(uint32_t padding) {
  List l;
  l.flags = 0x886;
  std::vector<Entry> surfaces = {{0, nl::kSurfaceModifier}}, viewport;
  l.Pad(padding);
  l.TileBlock();
  SurfaceMarks(l, &surfaces);
  l.TileBlock();
  ColourDraw(l, 5, &surfaces, &viewport);
  ColourDraw(l, 6, &surfaces, nullptr);
  l.TileBlock();
  l.Chain(surfaces);
  l.Chain(viewport);
  return l;
}
List SceneList() { return SceneListAfter(0); }

struct Mode {
  uint64_t select, mask;
};
// Each tile, no tiling (the command processor's reset values), and a bin mask without the bin.
constexpr Mode kModes[] = {{0x3, 0xFFFFFFFF},
                           {0xC, 0xFFFFFFFF},
                           {0x30, 0xFFFFFFFF},
                           {0xFFFFFFFF, 0xFFFFFFFF},
                           {0x3, 0x30}};

std::vector<uint32_t> Converted(const Built& b) {
  std::vector<uint32_t> t;
  const nl::Result result = nl::Convert(b.input(), &t);
  if (!result) {
    std::fprintf(stderr, "native list: refused: %s at %u\n", nl::RefuseName(result.refuse),
                 result.at);
  }
  CHECK(result);
  CHECK(t.size() == t[nl::kTWords] && t[nl::kTMagic] == nl::kMagic);
  return t;
}

// Every mode against the interpreter; the list as it is, then with every place written.
void AllModes(const std::vector<uint32_t>& t, const Built& b, bool untiled = false) {
  ref::Harness harness;
  for (const Mode& mode : kModes) {
    nl::Run run;
    run.bin_select = mode.select;
    run.bin_mask = mode.mask;
    run.untiled = untiled;
    const ref::Verdict v = harness.Check(t.data(), b.pm4.data(), uint32_t(b.pm4.size() / 4), run);
    CHECK(v.interpreter && v.draw_count);
    CHECK(v.ops_state && v.ops_draws && v.ops_end);
    CHECK(v.view_state && v.view_draws && v.view_end);
    CHECK(v.draws == nl::DrawCount(t.data()));
  }
}

uint32_t Slots(const std::vector<uint32_t>& t, uint32_t type) {
  uint32_t n = 0;
  for (uint32_t s = 0; s < t[nl::kTSlotCount]; ++s) {
    n += (t[t[nl::kTSlots] + s * nl::kSlotWords] & 0xFF) == type;
  }
  return n;
}

// A converted list: the template is the same whatever the places hold and whether the fixed-up
// flag is set, its key is the live list's, and it agrees with the interpreter in every mode.
std::vector<uint32_t> Proven(const List& list) {
  Built b = list.Build();
  const std::vector<uint32_t> t = Converted(b);
  CHECK(nl::LiveKey(t.data(), b.input()) == nl::TemplateKey(t.data()));
  AllModes(t, b);
  ref::Random random(7);
  for (int round = 0; round < 3; ++round) {
    ref::FixUp(t.data(), b.pm4.data(), &random);
    CHECK(nl::LiveKey(t.data(), b.input()) == nl::TemplateKey(t.data()));
    CHECK(Converted(b) == t);
    AllModes(t, b);
  }
  List flagged = list;
  flagged.flags |= nl::kFlagFixedUp;
  CHECK(Converted(flagged.Build()) == t);
  // No bit of a slot's words is in the key, but for the low two of a microcode address: the
  // shader's type.
  Built other = b;
  for (uint32_t s = 0; s < t[nl::kTSlotCount]; ++s) {
    const uint32_t* slot = &t[t[nl::kTSlots] + s * nl::kSlotWords];
    const uint32_t type = slot[0] & 0xFF;
    const bool microcode =
        (type == nl::kSlotVertexShader || type == nl::kSlotPixelShader) && (slot[0] >> 8) == 0;
    for (uint32_t d = slot[1]; d < slot[1] + slot[3]; ++d) {
      const uint32_t w = nl::Word(other.pm4.data(), d);
      for (uint32_t bit = 0; bit < 32; ++bit) {
        ref::StoreWord(other.pm4.data(), d, w ^ (1u << bit));
        const bool same = nl::LiveKey(t.data(), other.input()) == nl::TemplateKey(t.data());
        CHECK(same == !(microcode && bit < 2));
      }
      ref::StoreWord(other.pm4.data(), d, w);
    }
  }
  // Any static word changes the key: a register value, a table word, a mask, a flag.
  for (uint32_t d = 0; d < other.pm4.size() / 4; ++d) {
    const uint32_t w = nl::Word(other.pm4.data(), d);
    if (w >> 30 == 0 && (w & 0x7FFF) == 0x2205) {
      ref::StoreWord(other.pm4.data(), d + 1, nl::Word(other.pm4.data(), d + 1) ^ 0x100);
      break;
    }
  }
  CHECK(nl::LiveKey(t.data(), other.input()) != nl::TemplateKey(t.data()));
  other = b;
  ref::StoreWord(other.table.data(), 1, nl::Word(other.table.data(), 1) ^ 0x40);
  CHECK(nl::LiveKey(t.data(), other.input()) != nl::TemplateKey(t.data()));
  other = b;
  ref::StoreWord(other.area_c.data(), 1, nl::Word(other.area_c.data(), 1) ^ 0x40);
  CHECK(nl::LiveKey(t.data(), other.input()) != nl::TemplateKey(t.data()));
  other = b;
  other.header[nl::kHeaderMasks + 5] ^= 1;
  CHECK(nl::LiveKey(t.data(), other.input()) != nl::TemplateKey(t.data()));
  other = b;
  other.header[nl::kHeaderFlags + 3] ^= 4;
  CHECK(nl::LiveKey(t.data(), other.input()) != nl::TemplateKey(t.data()));
  return t;
}

// Whatever the converter takes must agree with the interpreter, not only the lists the game
// has: `base` with a few words of its PM4 and its tables changed at random, over and over.
// Returns how many of the changed lists converted.
uint32_t Mutations(const List& base, uint32_t rounds, uint64_t seed) {
  ref::Random random(seed);
  ref::Harness harness;
  std::vector<uint32_t> t;
  uint32_t converted = 0;
  for (uint32_t round = 0; round < rounds; ++round) {
    List l = base;
    for (uint32_t change = random.Next() % 3; change < 3; ++change) {
      std::vector<uint32_t>& words = random.Next() % 4   ? l.pm4
                                     : random.Next() % 2 ? l.table
                                                         : l.area_c;
      uint32_t& word = words[random.Next() % words.size()];
      switch (random.Next() % 4) {
        case 0:  // another word of the list: a plausible header or value
          word = l.pm4[random.Next() % l.pm4.size()];
          break;
        case 1:  // the predicate bit, the one-register bit, a register next door
          word ^= 1u << (random.Next() % 16);
          break;
        default:
          word ^= 1u << (random.Next() % 32);
          break;
      }
    }
    Built b = l.Build();
    if (!nl::Convert(b.input(), &t)) continue;
    ++converted;
    for (int fixed = 0; fixed < 2; ++fixed) {
      if (fixed) ref::FixUp(t.data(), b.pm4.data(), &random);
      CHECK(nl::LiveKey(t.data(), b.input()) == nl::TemplateKey(t.data()));
      for (const Mode& mode : kModes) {
        nl::Run run;
        run.bin_select = mode.select;
        run.bin_mask = mode.mask;
        CHECK(harness.Check(t.data(), b.pm4.data(), uint32_t(l.pm4.size()), run).all());
      }
      // The lists scene_msaa unties are the tiled templates, no other. Of a live list a caller
      // knows what Untiled() says: that has to be what scene_msaa did to it.
      const bool tiled = (t[nl::kTFlags] & nl::kTemplateTiled) != 0;
      CHECK(tiled == ref::SceneMsaaUnties(l.flags, b.pm4.data(), uint32_t(l.pm4.size())));
      CHECK(!nl::Untiled(t.data(), b.pm4.data()));
      if (!tiled) continue;
      Built untiled = b;
      const bool changed = ref::Untile(untiled.pm4.data(), uint32_t(l.pm4.size())) != 0;
      CHECK(changed && nl::Untiled(t.data(), untiled.pm4.data()));
      CHECK(nl::LiveKey(t.data(), untiled.input()) == nl::TemplateKey(t.data()));
      nl::Run flat;
      flat.untiled = nl::Untiled(t.data(), untiled.pm4.data());
      CHECK(harness.Check(t.data(), untiled.pm4.data(), uint32_t(l.pm4.size()), flat).all());
    }
  }
  return converted;
}

nl::Refuse Refused(
    const std::function<void(List&)>& change,
    const std::function<List()>& base = [] { return DepthList(); }) {
  List l = base();
  change(l);
  const Built b = l.Build();
  std::vector<uint32_t> t = {1};
  const nl::Result result = nl::Convert(b.input(), &t);
  CHECK(bool(result) == !t.empty());
  return result.refuse;
}

}  // namespace

int main() {
  using R = nl::Refuse;

  // The parts' fence works here: whatever reads past a part, an empty one too, is stopped.
  CHECK(FenceHolds(0) && FenceHolds(4) && FenceHolds(nl::kHeaderBytes));

  // Passes 0 and 6: two draws, the window scissor restored after them.
  {
    const List list = DepthList();
    const std::vector<uint32_t> t = Proven(list);
    CHECK(nl::DrawCount(t.data()) == 2 && t[nl::kTFlags] == 0 && t[nl::kTTileBoundCount] == 0);
    CHECK(t[nl::kTCbFlags] == 0x86 && t[nl::kTListWords] == list.pm4.size());
    CHECK(Slots(t, nl::kSlotVertexBuffer) == 2 && Slots(t, nl::kSlotIndexBuffer) == 2);
    CHECK(Slots(t, nl::kSlotTexture) == 2 && Slots(t, nl::kSlotVertexShader) == 2);
    CHECK(t[nl::kTSlotCount] == 8);  // each buffer once, though both tables name it
    // The masks travel with the template.
    CHECK(t[t[nl::kTMasks]] == 0xFFFF0000u && t[t[nl::kTMasks] + 19] == 0xFFFF0013u);
    // Every draw is predicated: with a bin mask that excludes the bin none runs, so none writes
    // its own registers; without tiling both do, and the last one's stay.
    const Built b = list.Build();
    ref::Harness harness;
    nl::Run run;
    run.bin_select = 0x3;
    run.bin_mask = 0x30;
    CHECK(harness.Check(t.data(), b.pm4.data(), uint32_t(list.pm4.size()), run).all());
    CHECK(harness.interpreted().Unwritten(nl::kRegDmaBase, 3));
    CHECK(harness.Check(t.data(), b.pm4.data(), uint32_t(list.pm4.size()), nl::Run()).all());
    const uint32_t last[3] = {kPlaceholder, 0x40000000u | 32, Initiator(kStrip, 32)};
    CHECK(harness.interpreted().Holds(nl::kRegDmaBase, 3, last));
    // The second draw has its own shader, texture and vertex buffer.
    std::vector<uint32_t> pool(t[nl::kTPoolWords]);
    nl::Resolve(t.data(), b.pm4.data(), pool.data());
    const nl::DrawView first = nl::Draw(t.data(), pool.data(), 0),
                       second = nl::Draw(t.data(), pool.data(), 1);
    CHECK(first.shader[0] && !first.shader[1] && second.shader[0] && !second.shader[1]);
    CHECK(first.shader_size[0] == 21 && second.shader_size[0] == 22);
    CHECK(first.vertex_fetch && second.vertex_fetch && first.vertex_fetch != second.vertex_fetch);
    CHECK(first.texture[0] && second.texture[0] && !first.texture[1] && !first.tiles);
    CHECK(first.predicated && first.initiator == Initiator(kStrip, 31) &&
          first.index_address == kPlaceholder);
    // A draw that is not predicated runs whatever the bins say.
    List plain = list;
    for (uint32_t& w : plain.pm4) {
      if (w == 0xC0032201u) w = 0xC0032200u;
    }
    const Built pb = plain.Build();
    const std::vector<uint32_t> pt = Converted(pb);
    CHECK(pt != t);
    AllModes(pt, pb);
    CHECK(harness.Check(pt.data(), pb.pm4.data(), uint32_t(plain.pm4.size()), run).all());
    CHECK(harness.interpreted().Holds(nl::kRegDmaBase, 3, last));
    // A shader load's stage is the low bits of its address word. With bit 0 set the first load
    // is a pixel shader's: another list, with another key and another template.
    Built staged = list.Build();
    uint32_t load = 0;
    while (nl::Word(staged.pm4.data(), load) != 0xC0012700u) ++load;
    CHECK(nl::Word(staged.pm4.data(), load + 1) == kPlaceholder);
    ref::StoreWord(staged.pm4.data(), load + 1, kPlaceholder | 1);
    CHECK(nl::LiveKey(t.data(), staged.input()) != nl::TemplateKey(t.data()));
    const std::vector<uint32_t> st = Converted(staged);
    CHECK(nl::TemplateKey(st.data()) != nl::TemplateKey(t.data()));
    CHECK(Slots(st, nl::kSlotVertexShader) == 1 && Slots(st, nl::kSlotPixelShader) == 1);
    AllModes(st, staged);
    std::vector<uint32_t> staged_pool(st[nl::kTPoolWords]);
    nl::Resolve(st.data(), staged.pm4.data(), staged_pool.data());
    const nl::DrawView staged_first = nl::Draw(st.data(), staged_pool.data(), 0),
                       staged_second = nl::Draw(st.data(), staged_pool.data(), 1);
    CHECK(!staged_first.shader[0] && staged_first.shader[1]);
    CHECK(staged_first.shader_address[1] == (kPlaceholder | 1) &&
          staged_first.shader_size[1] == 21);
    CHECK(staged_second.shader[0] && staged_second.shader[1]);  // its own, and the first's
    // The same word with bit 1 is no shader the list format has.
    ref::StoreWord(staged.pm4.data(), load + 1, kPlaceholder | 2);
    CHECK(nl::LiveKey(t.data(), staged.input()) != nl::TemplateKey(t.data()));
    std::vector<uint32_t> none;
    CHECK(nl::Convert(staged.input(), &none).refuse == R::kShader);
  }

  // A draw that sets nothing but the index offset: it keeps the previous draw's shader, texture
  // and vertex buffer.
  {
    const List list = DepthList(true);
    const std::vector<uint32_t> t = Proven(list);
    CHECK(nl::DrawCount(t.data()) == 2);
    const uint32_t* r = nl::DrawRecord(t.data(), 1);
    CHECK(r[nl::kDDeltaCount] == 2);  // the draw's mark of the one before, and the index offset
    const Built b = list.Build();
    std::vector<uint32_t> pool(t[nl::kTPoolWords]);
    nl::Resolve(t.data(), b.pm4.data(), pool.data());
    const nl::DrawView first = nl::Draw(t.data(), pool.data(), 0),
                       second = nl::Draw(t.data(), pool.data(), 1);
    CHECK(second.shader[0] && second.shader_address[0] == first.shader_address[0]);
    CHECK(second.shader_size[0] == first.shader_size[0] && !second.shader[1]);
    CHECK(second.vertex_fetch == first.vertex_fetch && second.texture[0] == first.texture[0]);
    CHECK(second.initiator == Initiator(kStrip, 9, true) && second.size_word == (0x80000000u | 9));
    CHECK(second.packet > first.packet && nl::Word(b.pm4.data(), second.packet) == 0xC0032201u);
  }

  // Passes 1 and 2: the render target, viewport and scissor are places.
  {
    const List list = SmallTargetList();
    const std::vector<uint32_t> t = Proven(list);
    CHECK(nl::DrawCount(t.data()) == 2 && t[nl::kTFlags] == 0);
    CHECK(Slots(t, nl::kSlotViewport) == 6 && Slots(t, nl::kSlotScissor) == 6);
    CHECK(Slots(t, nl::kSlotSurface) == 2 + 2 + 3 + 1);  // mark sizes, HiZ, the target, AA
    CHECK(Slots(t, nl::kSlotConstants) == 6 && Slots(t, nl::kSlotTexture) == 4);
    CHECK(Slots(t, nl::kSlotVertexShader) == 4 && Slots(t, nl::kSlotPixelShader) == 4);
    const Built b = list.Build();
    std::vector<uint32_t> pool(t[nl::kTPoolWords]);
    nl::Resolve(t.data(), b.pm4.data(), pool.data());
    const nl::DrawView second = nl::Draw(t.data(), pool.data(), 1);
    CHECK(second.shader[0] && second.shader[1] && second.shader_size[1] == 44);
    CHECK(second.texture[5] && second.texture[6] && second.texture[7] && !second.texture[0]);
    // Fetch constant 6 has no place: its words are in the template; 5's are not.
    CHECK(second.texture[6][0] == Values(18, 15)[6]);
    const uint32_t* stat = t.data() + t[nl::kTPool] + (second.texture[5] - pool.data());
    CHECK(stat[0] == 0 && stat[5] == 0 && stat[6] == Values(18, 15)[6]);
  }

  // Pass 4, tiled and untiled.
  {
    const List list = SceneList();
    const std::vector<uint32_t> t = Proven(list);
    CHECK(nl::DrawCount(t.data()) == 2 && t[nl::kTFlags] == nl::kTemplateTiled);
    CHECK(t[nl::kTCbFlags] == 0x886);
    Built b = list.Build();
    const uint32_t words = uint32_t(list.pm4.size());
    // The tile-bound packets are scene_msaa's: nine window writes, the render target, AA.
    const std::vector<size_t> bound =
        forza::scene_tiling::TileBoundPackets(list.pm4.data(), list.pm4.size());
    CHECK(bound.size() == 11 && t[nl::kTTileBoundCount] == 11);
    for (size_t i = 0; i < bound.size(); ++i) {
      CHECK(t[t[nl::kTTileBound] + i * 2] == bound[i]);
      CHECK(t[t[nl::kTTileBound] + i * 2 + 1] == list.pm4[bound[i]]);
    }
    // Tiled: each bin select gets its tile's window; without tiling all three run and the last
    // stays. The bin mask after the list is the tile block's.
    ref::Harness harness;
    const uint32_t tile1[3] = {0x7001, 0x8001, 0x9001}, tile2[3] = {0x7002, 0x8002, 0x9002};
    nl::Run run;
    run.bin_select = 0xC;
    CHECK(harness.Check(t.data(), b.pm4.data(), words, run).all());
    CHECK(harness.interpreted().Holds(nl::kRegWindowOffset, 3, tile1));
    CHECK(harness.interpreted().bin_mask() == nl::kBinMaskAfterTiles);
    CHECK(!harness.interpreted().Unwritten(0x2000, 3) &&
          !harness.interpreted().Unwritten(0x2301, 1));
    CHECK(harness.Check(t.data(), b.pm4.data(), words, nl::Run()).all());
    CHECK(harness.interpreted().Holds(nl::kRegWindowOffset, 3, tile2));
    // The tile block puts the bin mask back, so the draws run even if it excluded the bin.
    run.bin_select = 0x3;
    run.bin_mask = 0x30;
    CHECK(harness.Check(t.data(), b.pm4.data(), words, run).all());
    CHECK(!harness.interpreted().Unwritten(nl::kRegDmaBase, 3));
    CHECK(!nl::Untiled(t.data(), b.pm4.data()));
    // A view that drops the tile block no longer agrees: the comparison sees it.
    std::vector<uint32_t> broken = t;
    broken[t[nl::kTDraws] + nl::kDTile] = nl::kNone;
    const ref::Verdict bad = harness.Check(broken.data(), b.pm4.data(), words, nl::Run());
    CHECK(!bad.view_state && bad.ops_state && bad.ops_end);
    broken = t;
    broken[t[nl::kTPool] + 40] ^= 1;  // a static register word
    CHECK(!harness.Check(broken.data(), b.pm4.data(), words, nl::Run()).all());

    // Untiled: the same template and the same key; the list is refused as input in this form.
    ref::Random random(11);
    ref::FixUp(t.data(), b.pm4.data(), &random);
    Built untiled = b;
    CHECK(ref::Untile(untiled.pm4.data(), words) == 11);
    CHECK(untiled.pm4 != b.pm4);
    CHECK(nl::Untiled(t.data(), untiled.pm4.data()));
    CHECK(nl::LiveKey(t.data(), untiled.input()) == nl::TemplateKey(t.data()));
    std::vector<uint32_t> none;
    CHECK(nl::Convert(untiled.input(), &none).refuse == R::kUntiled && none.empty());
    AllModes(t, untiled, true);
    nl::Run flat;
    flat.untiled = true;
    CHECK(harness.Check(t.data(), untiled.pm4.data(), words, flat).all());
    // No window, render target or AA write is left; the rest of the list is.
    CHECK(harness.interpreted().Unwritten(nl::kRegWindowOffset, 3));
    CHECK(harness.interpreted().Unwritten(0x2000, 3) && harness.interpreted().Unwritten(0x2301, 1));
    CHECK(!harness.interpreted().Unwritten(0x210F, 6) &&
          !harness.interpreted().Unwritten(nl::kRegDmaBase, 3));
    CHECK(harness.interpreted().bin_mask() == nl::kBinMaskAfterTiles);
    // Run as tiled, the untiled words do not agree (and the other way round).
    CHECK(!harness.Check(t.data(), untiled.pm4.data(), words, nl::Run()).all());
    CHECK(!harness.Check(t.data(), b.pm4.data(), words, flat).all());
    // Half untiled (the game writes word 4 last): the key still holds.
    Built half = b;
    ref::StoreWord(half.pm4.data(), bound[5], forza::scene_tiling::NopPacket(list.pm4[bound[5]]));
    CHECK(nl::LiveKey(t.data(), half.input()) == nl::TemplateKey(t.data()));
    CHECK(!nl::Untiled(t.data(), half.pm4.data()));
    // Another NOP in a header's place is a changed list.
    ref::StoreWord(half.pm4.data(), bound[5], 0xC0001000u);
    CHECK(nl::LiveKey(t.data(), half.input()) != nl::TemplateKey(t.data()));

    // The same packets without the header's tile flag: nothing scene_msaa would untile.
    List unflagged = list;
    unflagged.flags = 0x86;
    const Built ub = unflagged.Build();
    const std::vector<uint32_t> ut = Converted(ub);
    CHECK(ut[nl::kTFlags] == 0 && ut[nl::kTTileBoundCount] == 0);
    CHECK(!ref::SceneMsaaUnties(unflagged.flags, ub.pm4.data(), words));
    CHECK(ref::SceneMsaaUnties(list.flags, b.pm4.data(), words));
    AllModes(ut, ub);
  }

  // A list recorded for the tiles that does not start with a tile block. Its word 4 is a value
  // of the first packet that reads as a tile's window write: scene_msaa, which goes by that
  // word's bits, would turn the render target write into a NOP and leave word 4, so nothing
  // could tell the list's two forms apart. Such a list is refused.
  {
    const auto value_at_word_4 = [] {
      List l;
      l.flags = 0x886;
      std::vector<uint32_t> constants = Values(16, 1);
      constants[3] = nl::kTileWindowHeader;
      l.Regs(0x4000, constants);
      l.Regs(0x2000, Values(3, 2));
      return l;
    };
    const List list = value_at_word_4();
    Built b = list.Build();
    const uint32_t words = uint32_t(list.pm4.size());
    CHECK(nl::Word(b.pm4.data(), 4) == nl::kTileWindowHeader);
    CHECK(ref::SceneMsaaUnties(list.flags, b.pm4.data(), words));
    std::vector<uint32_t> t = {1};
    const nl::Result result = nl::Convert(b.input(), &t);
    CHECK(result.refuse == R::kTileBlock && result.at == 0 && t.empty());
    // What scene_msaa would do to it: one packet's header, and not word 4.
    CHECK(ref::Untile(b.pm4.data(), words) == 1);
    CHECK(nl::Word(b.pm4.data(), 4) == nl::kTileWindowHeader);
    // Whatever word 4 holds: a list with the tile flag starts with a tile block or is refused.
    CHECK(Refused([](List& l) { l.pm4[4] = 0x3F800000; }, value_at_word_4) == R::kTileBlock);
    CHECK(Refused([](List&) {}, [] { return SceneListAfter(1); }) == R::kTileBlock);
    CHECK(Refused([](List&) {}, [] { return SceneListAfter(27); }) == R::kTileBlock);
    // Without the flag scene_msaa leaves such a list alone, and it converts as any other.
    List unflagged = list;
    unflagged.flags = 0x86;
    const Built ub = unflagged.Build();
    const std::vector<uint32_t> ut = Converted(ub);
    CHECK(ut[nl::kTFlags] == 0 && ut[nl::kTTileBoundCount] == 0);
    CHECK(!ref::SceneMsaaUnties(unflagged.flags, ub.pm4.data(), words));
    AllModes(ut, ub);
    CHECK(Refused([](List& l) { l.flags = 0x86; }, [] { return SceneListAfter(1); }) == R::kNone);
  }

  // Refusals. Each names what is outside the format; every reason is reached.
  std::set<R> reached;
  const auto refused = [&reached](
                           R reason, const std::function<void(List&)>& change,
                           const std::function<List()>& base = [] { return DepthList(); }) {
    const R got = Refused(change, base);
    if (got != reason) {
      std::fprintf(stderr, "native list: expected %s, got %s\n", nl::RefuseName(reason),
                   nl::RefuseName(got));
    }
    reached.insert(got);
    return got == reason;
  };
  CHECK(Refused([](List&) {}) == R::kNone);
  CHECK(refused(R::kResourceType, [](List& l) { l.resource = 0x0040000A; }));
  CHECK(refused(R::kFlags, [](List& l) { l.flags |= 0x40; }));
  CHECK(refused(R::kFlags, [](List& l) { l.flags |= 0x20; }));
  CHECK(Refused([](List& l) { l.flags |= nl::kFlagFixedUp; }) == R::kNone);
  CHECK(refused(R::kListSize, [](List& l) { l.pm4.clear(); }));
  // A place names its dword with 20 bits: a list of that many words converts, one more does not.
  const auto padding_only = [](uint32_t words) {
    return [words](List& l) {
      l.pm4.assign(words, 0x80000000u);
      l.table.clear();
      l.area_c.clear();
    };
  };
  CHECK(Refused(padding_only(nl::kMaxListWords)) == R::kNone);
  CHECK(refused(R::kListSize, padding_only(nl::kMaxListWords + 1)));
  CHECK(refused(R::kSegments, [](List& l) { l.segment_count = 2; }));
  CHECK(refused(R::kSegments, [](List& l) { l.segment_next = 0x40001000; }));
  CHECK(refused(R::kSegments, [](List& l) { l.segment_words = int32_t(l.pm4.size()) - 1; }));
  CHECK(refused(R::kPatchTable, [](List& l) { l.patch_words = int32_t(l.table.size()) + 1; }));
  // The tables.
  CHECK(refused(R::kPlaceEntry, [](List& l) { l.table.push_back(0x80000000u | 3u << 26); }));
  CHECK(refused(R::kPlaceEntry, [](List& l) { l.table.push_back(0x80000000u); }));
  CHECK(refused(R::kPlaceEntry, [](List& l) { l.table.push_back(0x88000000u); }));  // no value word
  CHECK(refused(R::kPlaceEntry,
                [](List& l) { l.table[l.table.size() - 2] |= 0x40000000u; }));  // no next
  CHECK(refused(R::kPlaceSubCode, [](List& l) { l.Chain({{l.Regs(0x2200, {1, 2}), 62}}); }));
  CHECK(refused(R::kPlaceSubCode, [](List& l) { l.area_c[0] |= 63; }));
  CHECK(refused(R::kPlaceField, [](List& l) { l.Chain({{l.Regs(0x2200, {1, 2}), 0}}); }));
  CHECK(refused(R::kPlaceField, [](List& l) { l.Chain({{l.at() + 100, 0}}); }));
  CHECK(refused(R::kPlaceField,
                [](List& l) { l.Chain({{l.Regs(nl::kRegFetch, Values(6, 1)) + 1, 0}}); }));
  CHECK(refused(R::kPlaceField, [](List& l) { l.Chain({{l.Regs(0x210F, Values(6, 1)), 1}}); }));
  CHECK(refused(R::kPlaceField, [](List& l) {
    l.Chain({{l.at(), 0}});
    l.Pad(1);
  }));  // padding
  CHECK(refused(R::kPlaceField, [](List& l) { l.Chain({{l.Draw(6, 1) + 1, 0, 0}}); }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // a vertex buffer pair outside fetch constant 31
    l.Buffer({l.Regs(nl::kRegFetch, Values(6, 1)), 4, 0});
  }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // a texture's place on fetch constant 31
    l.Chain({{l.Regs(nl::kRegVertexFetch, Values(6, 1)), 0}});
  }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // a constant block of no words
    l.Chain({{l.Regs(0x45D0, Values(48, 1)), 0, 0x00}});
  }));
  CHECK(Refused([](List& l) { l.Chain({{l.Regs(0x45D0, Values(48, 1)), 0, 0x10}}); }) == R::kNone);
  // A surface sub-code belongs to one register, and so does each corner of the scissor.
  {
    const std::pair<uint32_t, uint32_t> surfaces[] = {{0x2000, nl::kSurfaceInfo},
                                                      {0x2001, nl::kSurfaceColorInfo},
                                                      {0x2002, nl::kSurfaceDepthInfo},
                                                      {0x2301, nl::kSurfaceAaConfig},
                                                      {0x2203, nl::kSurfaceHiZ}};
    for (const auto& on : surfaces) {
      for (const auto& of : surfaces) {
        const uint32_t reg = on.first, sub = of.second;
        const R got = Refused([reg, sub](List& l) {
          // 0x2000-0x2002 and 0x2200-0x2203 as the lists write them; the place on `reg`.
          const uint32_t first = reg == 0x2203 ? 0x2200 : reg == 0x2301 ? 0x2301 : 0x2000;
          const uint32_t count = first == 0x2200 ? 4 : first == 0x2301 ? 1 : 3;
          l.Chain({{l.Regs(first, Values(count, 1)) + (reg - first), sub}});
        });
        CHECK(got == (sub == on.second ? R::kNone : R::kPlaceField));
      }
    }
    for (uint32_t reg = nl::kRegWindowOffset; reg <= nl::kRegWindowOffset + 2; ++reg) {
      for (uint32_t sub = 0; sub < 2; ++sub) {
        const R got = Refused([reg, sub](List& l) {
          l.Chain({{l.Regs(nl::kRegWindowOffset, {0, 0, 0}) + (reg - nl::kRegWindowOffset), sub}});
        });
        CHECK(got == (reg == nl::kRegWindowOffset + 1 + sub ? R::kNone : R::kPlaceField));
      }
    }
  }
  CHECK(refused(R::kPlaceField, [](List& l) {  // a constant block in c0-c7
    l.Chain({{l.Regs(0x4400, Values(16, 1)), 0, 0x40}});
  }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // a constant block past its packet
    l.Chain({{l.Regs(0x45F0, Values(16, 1)) + 4, 0, 0x40}});
  }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // the mark's size word is its last
    l.Chain({{l.Mark({0x00010017, 0, 1, 2}) + 1, nl::kSurfaceMarkSize, 0}});
  }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // one chain, two kinds of place
    l.Chain({{l.Regs(nl::kRegFetch, Values(6, 1)), 0}, {l.Regs(0x210F, Values(6, 1)), 0}});
  }));
  CHECK(refused(R::kPlaceField, [](List& l) {  // the modifier belongs to a surface chain
    l.Chain({{0, nl::kSurfaceModifier}, {l.Regs(0x210F, Values(6, 1)), 0}});
  }));
  CHECK(Refused([](List& l) {
          l.Chain({{0, nl::kSurfaceModifier}, {l.Regs(0x2000, Values(3, 1)), nl::kSurfaceInfo}});
        }) == R::kNone);
  CHECK(refused(R::kPlaceConflict, [](List& l) {
    const uint32_t block = l.Regs(0x45D0, Values(48, 1));
    l.Chain({{block, 0, 0x10}, {block, 0, 0x20}});
  }));
  CHECK(refused(R::kPlaceConflict, [](List& l) {
    const uint32_t block = l.Regs(0x45D0, Values(48, 1));
    l.Chain({{block + 4, 0, 0x40}, {block, 0, 0x20}});
  }));
  // The packets.
  CHECK(refused(R::kPacketType1,
                [](List& l) { l.pm4.insert(l.pm4.end(), {0x40000000u | 0x200, 1, 2}); }));
  CHECK(refused(R::kOpcode,
                [](List& l) { l.Type3(0x36, {Initiator(kStrip, 3) | 0x80}); }));  // DRAW_INDX_2
  CHECK(refused(R::kOpcode, [](List& l) { l.Type3(0x55, {0x2080, 0, 0, 0}); }));  // SET_CONSTANT2
  CHECK(refused(R::kOpcode, [](List& l) {
    l.pm4.push_back(0xC0002700u | 0x80);
    l.pm4.push_back(0);
  }));
  CHECK(refused(R::kUntiled, [](List& l) { l.Type3(0x10, {nl::kTileWindowTarget, 0, 0, 0}); }));
  CHECK(refused(R::kPacketCut,
                [](List& l) { l.pm4.insert(l.pm4.end(), {4u << 16 | 0x2200, 1, 2}); }));
  CHECK(refused(R::kPacketCut, [](List& l) { l.pm4.push_back(0xC0032201u); }));
  CHECK(refused(R::kRegister, [](List& l) { l.Regs(0x2204, {1}); }));
  CHECK(refused(R::kRegister, [](List& l) { l.Regs(0x2200, {1, 2, 3, 4, 5}); }));
  CHECK(refused(R::kRegister, [](List& l) { l.Regs(0x4030, Values(4, 1)); }));  // VS c12
  CHECK(refused(R::kRegister, [](List& l) { l.Regs(0x4420, Values(4, 1)); }));  // PS c8
  CHECK(
      refused(R::kRegister, [](List& l) { l.Regs(nl::kRegFetch + 48, Values(6, 1)); }));  // fetch 8
  CHECK(
      refused(R::kRegister, [](List& l) { l.Regs(0x47F0, Values(16, 1)); }));  // literals by packet
  CHECK(refused(R::kOneRegister, [](List& l) { l.Regs(0x8000 | 0x2200, {1, 2}); }));
  CHECK(Refused([](List& l) { l.Regs(0x8000 | nl::kRegScratch7, {1, 2}); }) == R::kNone);
  CHECK(refused(R::kFetchRange, [](List& l) { l.Regs(nl::kRegFetch + 1, Values(6, 1)); }));
  CHECK(refused(R::kFetchRange, [](List& l) { l.Regs(nl::kRegFetch, Values(5, 1)); }));
  CHECK(refused(R::kFetchRange, [](List& l) { l.Regs(nl::kRegVertexFetch + 2, Values(2, 1)); }));
  CHECK(refused(R::kPredicate, [](List& l) { l.Type3(nl::kPm4ImLoad, {kPlaceholder, 9}, true); }));
  CHECK(refused(R::kPredicate, [](List& l) {
    l.Type3(nl::kPm4LoadAluConstant, {kPlaceholder, 0x3F0, 16}, true);
  }));
  CHECK(refused(R::kTileBlock, [](List& l) { l.Type3(nl::kPm4SetBinMaskLo, {0xFFFFFFFFu}); }));
  CHECK(refused(R::kTileBlock,
                [](List& l) { l.Type3(0x2D, {nl::kTileWindowTarget, 0, 0, 0}, true); }));
  CHECK(refused(R::kTileBlock,
                [](List& l) { l.Type3(nl::kPm4SetBinMaskHi, {0}); }));      // cut by the end
  CHECK(refused(R::kTileBlock, [](List& l) { l.pm4[3] = 5; }, SceneList));  // another bin mask
  CHECK(refused(
      R::kTileBlock, [](List& l) { l.pm4[4] &= ~1u; }, SceneList));  // window not predicated
  CHECK(refused(
      R::kTileBlock, [](List& l) { l.pm4[5] = 0x00040081; }, SceneList));  // another register
  CHECK(refused(
      R::kTileBlock, [](List& l) { l.pm4[24] = 0x0000FFFF; }, SceneList));  // another last mask
  CHECK(refused(R::kTileBlock, [](List& l) { l.pm4[0] |= 1; }, SceneList));
  CHECK(
      refused(R::kWindowMix, [](List& l) { l.Regs(nl::kRegWindowOffset, {0, 0, 0}); }, SceneList));
  CHECK(
      refused(R::kWindowMix, [](List& l) { l.TileBlock(); }));  // the depth list writes the window
  CHECK(refused(R::kDraw,
                [](List& l) { l.Type3(nl::kPm4DrawIndx, {0, 6, kPlaceholder, 1, 0}, true); }));
  CHECK(
      refused(R::kDraw, [](List& l) { l.Type3(nl::kPm4DrawIndx, {1, 6, kPlaceholder, 1}, true); }));
  CHECK(refused(R::kDraw, [](List& l) {
    l.Type3(nl::kPm4DrawIndx, {0, 6 | 2 << 6, kPlaceholder, 1}, true);
  }));
  CHECK(refused(R::kDraw,
                [](List& l) { l.Type3(nl::kPm4DrawIndx, {0, 6 | 2 << 6}, true); }));  // auto index
  CHECK(refused(R::kDrawPlace, [](List& l) { l.Draw(Initiator(kStrip, 3), 3); }));
  CHECK(refused(R::kShader, [](List& l) { l.Type3(nl::kPm4ImLoad, {kPlaceholder | 2, 9}); }));
  CHECK(refused(R::kShader, [](List& l) { l.Type3(nl::kPm4ImLoad, {kPlaceholder, 0}); }));
  CHECK(refused(R::kShader, [](List& l) { l.Type3(nl::kPm4ImLoad, {kPlaceholder, 0x10009}); }));
  CHECK(refused(R::kShader, [](List& l) { l.Type3(nl::kPm4ImLoad, {kPlaceholder, 9, 0}); }));
  // One word, and the list ends with it: the size word that is not there must not be read.
  CHECK(refused(R::kShader, [](List& l) { l.Type3(nl::kPm4ImLoad, {kPlaceholder}); }));
  CHECK(refused(R::kShaderPlace, [](List& l) { l.Shader(true, 9); }));
  CHECK(refused(R::kLiterals,
                [](List& l) { l.Type3(nl::kPm4LoadAluConstant, {kPlaceholder, 0x100, 16}); }));
  CHECK(refused(R::kLiterals,
                [](List& l) { l.Type3(nl::kPm4LoadAluConstant, {kPlaceholder, 0x3F0, 8}); }));
  CHECK(refused(R::kLiterals, [](List& l) {  // fetch constants from memory
    l.Type3(nl::kPm4LoadAluConstant, {kPlaceholder, 0x00010000 | 0x3F0, 16});
  }));
  CHECK(refused(R::kLiterals,
                [](List& l) { l.Type3(nl::kPm4LoadAluConstant, {kPlaceholder, 0x3F0}); }));
  CHECK(refused(R::kLiteralsPlace, [](List& l) { l.Literals(0x720); }));
  // A place of the wrong shape for where it lands: a shader's address is a two-word entry.
  CHECK(refused(R::kPlaceField, [](List& l) { l.Chain({{l.Shader(false, 9), 0}}); }));
  CHECK(refused(R::kPlaceField, [](List& l) { l.Chain({{l.Literals(0x320), 0, 0}}); }));
  for (uint32_t r = 1; r < uint32_t(R::kCount); ++r) {
    if (!reached.count(R(r))) {
      std::fprintf(stderr, "native list: no test refuses with %s\n", nl::RefuseName(R(r)));
    }
    CHECK(reached.count(R(r)));
  }
  CHECK(std::string_view(nl::RefuseName(R::kLiteralsPlace)) == "literals-place");
  CHECK(std::string_view(nl::RefuseName(R::kWindowMix)) == "window-mix");

  // Nothing is read past a list's end (the parts' fence stops what tries). A list that ends
  // with a packet of any kind a list can hold, or a NOP, cut to one word, two, ... five: each is
  // refused, as none has a place.
  {
    const std::pair<uint32_t, std::vector<uint32_t>> packets[] = {
        {nl::kPm4DrawIndx, {0, Initiator(kStrip, 3), kPlaceholder, 3, 0}},
        {nl::kPm4ImLoad, {kPlaceholder, 9, 0, 0, 0}},
        {nl::kPm4LoadAluConstant, {kPlaceholder, 0x3F0, nl::kLiteralWords, 0, 0}},
        {nl::kPm4SetBinMaskLo, {nl::kTileMasks[0], 0, 0, 0, 0}},
        {nl::kPm4SetBinMaskHi, {0, 0, 0, 0, 0}},
        {forza::scene_tiling::kPm4SetConstant, {nl::kTileWindowTarget, 0, 0, 0, 0}},
        {forza::scene_tiling::kPm4Nop, {0, 0, 0, 0, 0}},
    };
    for (const auto& packet : packets) {
      const uint32_t opcode = packet.first;
      for (uint32_t words = 1; words <= packet.second.size(); ++words) {
        const std::vector<uint32_t> values(packet.second.begin(), packet.second.begin() + words);
        for (const bool predicated : {false, true}) {
          CHECK(Refused([&](List& l) { l.Type3(opcode, values, predicated); }) != R::kNone);
        }
      }
    }
    // Every prefix of every list here, with the places that are in it: most are refused (a
    // packet cut, a draw or a shader load without its place); the ones that convert agree with
    // the interpreter like any list.
    uint32_t prefixes = 0, converted = 0;
    for (const List& whole : {DepthList(), DepthList(true), SmallTargetList(), SceneList()}) {
      for (uint32_t words = 0; words < whole.pm4.size(); ++words, ++prefixes) {
        const List cut = whole.Prefix(words);
        const Built b = cut.Build();
        std::vector<uint32_t> t;
        if (!nl::Convert(b.input(), &t)) continue;
        ++converted;
        CHECK(nl::LiveKey(t.data(), b.input()) == nl::TemplateKey(t.data()));
        AllModes(t, b);
        if (!(t[nl::kTFlags] & nl::kTemplateTiled)) continue;
        Built untiled = b;
        CHECK(ref::Untile(untiled.pm4.data(), words) >= nl::kTiles);
        CHECK(nl::Untiled(t.data(), untiled.pm4.data()) && !nl::Untiled(t.data(), b.pm4.data()));
        AllModes(t, untiled, true);
      }
    }
    CHECK(converted > 100 && converted < prefixes);
  }

  // Changed lists: most are refused; the ones that convert agree like the rest.
  {
    const uint32_t rounds = 3000;
    const uint32_t depth = Mutations(DepthList(true), rounds, 1);
    const uint32_t small = Mutations(SmallTargetList(), rounds, 2);
    const uint32_t scene = Mutations(SceneList(), rounds, 3);
    CHECK(depth > rounds / 20 && small > rounds / 20 && scene > rounds / 20);
    CHECK(depth < rounds && small < rounds && scene < rounds);
  }

  // The key: four words of the stream's hash; the count is part of it.
  {
    nl::Hasher a, b, c;
    for (uint32_t i = 0; i < 9; ++i) a.Add(i);
    for (uint32_t i = 0; i < 9; ++i) b.Add(i);
    for (uint32_t i = 0; i < 9; ++i) c.Add(i);
    for (uint32_t i = 0; i < 3; ++i) c.Add(0);  // the same blocks, three more words
    const nl::Key ka = a.Finish(), kb = b.Finish(), kc = c.Finish();
    CHECK(ka == kb && ka != kc);
    CHECK((ka.w[0] | ka.w[1] | ka.w[2] | ka.w[3]) != 0);
  }

  std::puts("native list: ok");
  return 0;
}
