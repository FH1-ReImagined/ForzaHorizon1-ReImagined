// Native renderer M4, third step (src/platform/native_list_registry.h): the game's side of the
// model list registry, without guest memory: which command buffers are bound and when one is
// bound again, the lists an unload drops, one template id per key, the template ring that waits
// for the plugin's acknowledgement, the state words, the copy of an untiled list with its
// headers put back. And the fixture the GPU plugin's test runs (tests/native_list_fixture.h,
// the same file as ReIGlue's tests/fh1_native_list_fixture.h): two lists made up here and the
// templates this converter makes of them; the test fails if the converter makes others.
//   g++ -std=c++20 -O2 -I forza-recomp/src forza-recomp/tests/native_list_registry_test.cpp
//   ./a.out --fixture > forza-recomp/tests/native_list_fixture.h   (after a change of the format)
#include "platform/native_list_registry.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "native_list_fixture.h"

namespace nl = forza::native_list;

#define CHECK(x)                                                                      \
  do {                                                                                \
    if (!(x)) {                                                                       \
      std::fprintf(stderr, "native list registry: FAIL line %d: %s\n", __LINE__, #x); \
      std::exit(1);                                                                   \
    }                                                                                 \
  } while (false)

namespace {

// A list being written (host order): packets, then the patch table's places.
struct List {
  std::vector<uint32_t> pm4, table;
  uint32_t flags = 0x86;
  uint32_t at() const { return uint32_t(pm4.size()); }
  uint32_t Regs(uint32_t first, std::vector<uint32_t> values) {
    pm4.push_back(uint32_t(values.size() - 1) << 16 | first);
    pm4.insert(pm4.end(), values.begin(), values.end());
    return at() - uint32_t(values.size());
  }
  uint32_t Type3(uint32_t opcode, std::vector<uint32_t> values, bool predicated = false) {
    pm4.push_back(0xC0000000u | uint32_t(values.size() - 1) << 16 | opcode << 8 |
                  (predicated ? 1 : 0));
    pm4.insert(pm4.end(), values.begin(), values.end());
    return at() - uint32_t(values.size());
  }
  // A place of one fixup: (dword, sub-code, value word or -1).
  void Place(uint32_t dword, uint32_t sub, int64_t value = -1) {
    table.push_back(0x80000000u | (value >= 0 ? 2u : 1u) << 26 | dword << 6 | sub);
    if (value >= 0) table.push_back(uint32_t(value));
  }
  void TileBlock(uint32_t seed) {
    Type3(nl::kPm4SetBinMaskHi, {0});
    for (uint32_t tile = 0; tile < nl::kTiles; ++tile) {
      Type3(nl::kPm4SetBinMaskLo, {nl::kTileMasks[tile]});
      Type3(0x2D, {nl::kTileWindowTarget, 0x7000 + tile + seed, 0x8000 + tile, 0x9000 + tile},
            true);
    }
    Type3(nl::kPm4SetBinMaskLo, {0xFFFFFFFFu});
    Type3(nl::kPm4SetBinMaskHi, {0});
  }
  // One draw: vertex constants, both shaders (one with a literal block), state, a texture, the
  // vertex buffer, the draw.
  void Draw(uint32_t seed, bool predicated) {
    pm4.push_back(0x80000000u);  // padding
    Regs(0x4000, {seed, seed + 1, seed + 2, seed + 3, 0x3F800000u, 0, 0, seed + 7});
    Place(Type3(nl::kPm4LoadAluConstant, {0xEEEEEEE0u, 0x3F0, nl::kLiteralWords}), 1, 0);
    Place(Type3(nl::kPm4ImLoad, {0xEEEEEEE0u, 20 + seed}), 0, 0x40);
    Place(Type3(nl::kPm4ImLoad, {0xEEEEEEE1u, 30 + seed}), 0, 0x80);
    Regs(0x2200, {0x00700736u, seed, 0x20, 0});
    Regs(0x2180, {seed, 0x10000});
    Place(Regs(nl::kRegFetch, {0xEEEEEEE0u, 1, 2, 3, 4, 5}), 0);
    Place(Regs(nl::kRegVertexFetch, {6, 7, 8, 9, 0xEEEEEEE0u, 0xEEEEEEE1u}), 4, 0);
    Regs(0x2102, {0});
    Place(Type3(nl::kPm4DrawIndx, {0, (40 + seed) << 16 | 4, 0xEEEEEEE0u, 0x40000000u | 40},
                predicated),
          0, 0);
  }
  std::vector<uint32_t> Header() const {
    std::vector<uint32_t> h(nl::kHeaderBytes / 4, 0);
    h[0] = 0x00400009;
    for (uint32_t i = 0; i < nl::kMaskWords; ++i) h[nl::kHeaderMasks / 4 + i] = 0xFFFFFFFFu;
    h[nl::kHeaderFlags / 4] = flags;
    h[nl::kHeaderPatchWords / 4] = uint32_t(table.size());
    h[nl::kHeaderSegments / 4 + 1] = 1;
    h[nl::kHeaderSegments / 4 + 2] = 0x81000000u | at();
    return h;
  }
};

// The depth pre-pass's shape (one draw, not predicated) and the scene pass's (tile blocks, a
// tile-bound render target write, two predicated draws).
List DepthList() {
  List l;
  l.Draw(1, false);
  l.Regs(0x57F, {7});
  return l;
}
List SceneList() {
  List l;
  l.flags = 0x886;
  l.TileBlock(0);
  l.Regs(0x2000, {0x14020500u, 0x00000500u, 0x00000500u});  // tile-bound
  l.Regs(0x2301, {0xC003});                                   // tile-bound
  l.Draw(2, true);
  l.TileBlock(16);
  l.Draw(3, true);
  l.TileBlock(32);
  return l;
}

std::vector<uint8_t> Guest(const std::vector<uint32_t>& words) {
  std::vector<uint8_t> b(words.size() * 4);
  for (size_t i = 0; i < words.size(); ++i) {
    const uint32_t g = __builtin_bswap32(words[i]);
    std::memcpy(b.data() + i * 4, &g, 4);
  }
  return b;
}

struct Converted {
  std::vector<uint32_t> header, table, pm4, tmpl;
};
Converted Convert(const List& l) {
  Converted c;
  c.header = l.Header();
  c.table = l.table;
  c.pm4 = l.pm4;
  const auto h = Guest(c.header), t = Guest(c.table), p = Guest(c.pm4);
  nl::Input in;
  in.header = h.data();
  in.patch_table = t.data();
  in.patch_words = uint32_t(c.table.size());
  in.pm4 = p.data();
  in.pm4_words = uint32_t(c.pm4.size());
  const nl::Result r = nl::Convert(in, &c.tmpl);
  if (!r) std::fprintf(stderr, "refused: %s at %u\n", nl::RefuseName(r.refuse), r.at);
  CHECK(r);
  return c;
}

void PrintArray(const char* name, const std::vector<uint32_t>& v) {
  std::printf("constexpr uint32_t %s[] = {", name);
  for (size_t i = 0; i < v.size(); ++i) std::printf("%s0x%08X,", i % 8 ? " " : "\n    ", v[i]);
  std::printf("\n};\n");
}

void WriteFixture() {
  std::printf(
      "// Native renderer M4: two model lists made up by tests/native_list_registry_test.cpp (the\n"
      "// depth pre-pass's shape, and the scene pass's with its tile blocks) and the templates the\n"
      "// game's converter (src/platform/native_list.h) makes of them, host order. The game's test\n"
      "// checks that its converter still makes these; ReIGlue's tests/fh1_native_list_test.cpp\n"
      "// (the same file as its tests/fh1_native_list_fixture.h) runs the GPU plugin's readers and\n"
      "// compare on them. Written by `native_list_registry_test --fixture`; do not edit.\n"
      "#pragma once\n\n#include <cstdint>\n\nnamespace native_list_fixture {\n\n");
  const struct {
    const char* name;
    List list;
  } lists[] = {{"kDepth", DepthList()}, {"kScene", SceneList()}};
  for (const auto& [name, list] : lists) {
    const Converted c = Convert(list);
    PrintArray((std::string(name) + "Header").c_str(), c.header);
    PrintArray((std::string(name) + "PatchTable").c_str(), c.table);
    PrintArray((std::string(name) + "Pm4").c_str(), c.pm4);
    PrintArray((std::string(name) + "Template").c_str(), c.tmpl);
    std::printf("\n");
  }
  std::printf("}  // namespace native_list_fixture\n");
}

template <size_t N>
bool Same(const std::vector<uint32_t>& v, const uint32_t (&a)[N]) {
  return v.size() == N && std::memcmp(v.data(), a, sizeof(a)) == 0;
}

void TestFixture() {
  namespace fx = native_list_fixture;
  const Converted depth = Convert(DepthList()), scene = Convert(SceneList());
  CHECK(Same(depth.header, fx::kDepthHeader) && Same(depth.table, fx::kDepthPatchTable) &&
        Same(depth.pm4, fx::kDepthPm4));
  CHECK(Same(depth.tmpl, fx::kDepthTemplate));
  CHECK(Same(scene.header, fx::kSceneHeader) && Same(scene.table, fx::kScenePatchTable) &&
        Same(scene.pm4, fx::kScenePm4));
  CHECK(Same(scene.tmpl, fx::kSceneTemplate));
  CHECK(scene.tmpl[nl::kTFlags] & nl::kTemplateTiled);
  CHECK(scene.tmpl[nl::kTTileBoundCount] == 3 * 3 + 2);  // three blocks' windows, two writes
  // Untiled as scene_msaa unties it: the same key, the template's mode, and refused as input;
  // retiled from the saved headers it is the list again.
  std::vector<uint32_t> untiled = scene.pm4;
  std::vector<std::pair<uint32_t, uint32_t>> saved;
  const uint32_t address = 0xA1234000u;
  for (size_t i : forza::scene_tiling::TileBoundPackets(untiled.data(), untiled.size())) {
    saved.emplace_back(address + uint32_t(i) * 4, untiled[i]);
    untiled[i] = forza::scene_tiling::NopPacket(untiled[i]);
  }
  CHECK(saved.size() == 11);
  std::vector<uint8_t> h = Guest(scene.header), t = Guest(scene.table), p = Guest(untiled);
  nl::Input in;
  in.header = h.data();
  in.patch_table = t.data();
  in.patch_words = uint32_t(scene.table.size());
  in.pm4 = p.data();
  in.pm4_words = uint32_t(untiled.size());
  CHECK(nl::LiveKey(scene.tmpl.data(), in) == nl::TemplateKey(scene.tmpl.data()));
  CHECK(nl::Untiled(scene.tmpl.data(), p.data()));
  std::vector<uint32_t> refused;
  CHECK(nl::Convert(in, &refused).refuse == nl::Refuse::kUntiled);
  CHECK(nl::Retile(p.data(), uint32_t(untiled.size()), address, saved));
  CHECK(p == Guest(scene.pm4));
  CHECK(!nl::Retile(p.data(), uint32_t(untiled.size()), address, saved));  // not NOPs now
  CHECK(!nl::Retile(p.data(), uint32_t(untiled.size()), address, {}));
  // A saved address outside the list.
  std::vector<uint8_t> q = Guest(untiled);
  CHECK(!nl::Retile(q.data(), uint32_t(untiled.size()), address + 4, saved));
  CHECK(!nl::Retile(q.data(), uint32_t(untiled.size()), address,
                    {{address + uint32_t(untiled.size()) * 4, 0xC0001000u}}));
}

void TestFlags() {
  // The loader's flags in memory against the files' (docs "M4, second step").
  CHECK(nl::FilesFormFlags(0x181) == 0x86);
  CHECK(nl::FilesFormFlags(0x2181) == 0x86);
  CHECK(nl::FilesFormFlags(0x981) == 0x886);
  CHECK(nl::FilesFormFlags(0x2981) == 0x886);
}

void TestLists() {
  nl::Lists lists;
  nl::Bound old;
  CHECK(lists.Find(0x40001000, 0x17100000, 300, 40, &old) == nl::Lists::Look::kFresh);
  nl::Bound b;
  b.phys = 0x17100000;
  b.words = 300;
  b.patch_words = 40;
  b.slot = 5;
  b.template_id = 1;
  lists.Set(0x40001000, b);
  CHECK(lists.Find(0x40001000, 0x17100000, 300, 40, &old) == nl::Lists::Look::kCurrent);
  // The object reused for another list: another segment, length or patch table.
  CHECK(lists.Find(0x40001000, 0x17200000, 300, 40, &old) == nl::Lists::Look::kReused);
  CHECK(old.phys == 0x17100000 && old.slot == 5);
  CHECK(lists.Find(0x40001000, 0x17100000, 301, 40, &old) == nl::Lists::Look::kReused);
  CHECK(lists.Find(0x40001000, 0x17100000, 300, 41, &old) == nl::Lists::Look::kReused);
  // Unload: by the headers' range, by the PM4's, neither.
  nl::Bound c = b;
  c.phys = 0x17300000;
  lists.Set(0x40002000, c);
  nl::Bound d = b;
  d.phys = 0x17400000;
  lists.Set(0x40003000, d);
  uint32_t by_header = 0, by_address = 0;
  auto gone = lists.Unload(0x40000F00, 0x200, 0x18000000, 0x1000, &by_header, &by_address);
  CHECK(gone.size() == 1 && gone[0].phys == 0x17100000 && by_header == 1 && by_address == 0);
  gone = lists.Unload(0x50000000, 0x100, 0x17300000, 0x40, &by_header, &by_address);
  CHECK(gone.size() == 1 && gone[0].phys == 0x17300000 && by_address == 1);
  gone = lists.Unload(0x50000000, 0x100, 0x17300000, 0x40);
  CHECK(gone.empty() && lists.size() == 1);
  // A range that ends where a list starts does not hold it.
  gone = lists.Unload(0x40002000, 0x1000, 0x17000000, 0x400000);
  CHECK(gone.empty());
  CHECK(lists.Get(0x40003000) && !lists.Get(0x40001000));
  CHECK(lists.Erase(0x40003000, &old) && old.phys == 0x17400000 && lists.size() == 0);
}

void TestTemplates() {
  nl::Templates t;
  nl::Key a, b;
  a.w[0] = 1;
  b.w[0] = 2;
  bool collision = false;
  const uint32_t ia = t.Id(a, 0x111, 100, &collision);
  CHECK(ia && !collision && !t.Sent(a));
  CHECK(t.Id(a, 0x111, 100, &collision) == ia && !collision);
  t.MarkSent(a);
  CHECK(t.Sent(a) && !t.Sent(b));
  const uint32_t ib = t.Id(b, 0x222, 100, &collision);
  CHECK(ib && ib != ia);
  // The same key with other words: refused, the first stays.
  CHECK(t.Id(a, 0x112, 100, &collision) == 0 && collision);
  CHECK(t.Id(a, 0x111, 101, &collision) == 0 && collision);
  CHECK(t.Id(a, 0x111, 100, &collision) == ia && !collision && t.size() == 2);
}

void TestRing() {
  nl::DefineRing ring(1024);
  uint32_t at = 0, seq = 0;
  CHECK(ring.Reserve(100, 0, &at, &seq) && at == 0 && seq == 1);    // 128 bytes
  CHECK(ring.Reserve(500, 0, &at, &seq) && at == 128 && seq == 2);  // 512
  CHECK(ring.Reserve(300, 0, &at, &seq) && at == 640 && seq == 3);  // 320: up to 960
  CHECK(!ring.Reserve(100, 0, &at, &seq));                          // 64 left, nothing acked
  CHECK(!ring.Reserve(2000, 3, &at, &seq));                         // never fits
  CHECK(!ring.Fits(2000) && !ring.Fits(1025) && !ring.Fits(0) && ring.Fits(1024) &&
        ring.Fits(1));
  // The plugin read the first: its 128 bytes at the start are free again.
  CHECK(ring.Reserve(100, 1, &at, &seq) && at == 0 && seq == 4);
  CHECK(!ring.Reserve(64, 1, &at, &seq));  // wrapped: 0-128 used, 128 is the oldest
  CHECK(ring.Reserve(64, 2, &at, &seq) && at == 128 && seq == 5);
  CHECK(ring.outstanding() == 3);
  // Everything acknowledged: the ring starts over.
  CHECK(ring.Reserve(1024, 5, &at, &seq) && at == 0 && ring.outstanding() == 1);
  CHECK(!ring.Reserve(0, 6, &at, &seq));
}

void TestSlots() {
  nl::Slots slots(2);
  uint32_t a = 9, b = 9, c = 9;
  CHECK(slots.Take(&a) && slots.Take(&b) && a != b && !slots.Take(&c));
  slots.Give(a);
  CHECK(slots.Take(&c) && c == a && slots.used() == 2);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc > 1 && std::strcmp(argv[1], "--fixture") == 0) {
    WriteFixture();
    return 0;
  }
  TestFlags();
  TestFixture();
  TestLists();
  TestTemplates();
  TestRing();
  TestSlots();
  std::printf("native list registry: all tests passed\n");
  return 0;
}
