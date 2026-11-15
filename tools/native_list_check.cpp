// The checker behind tools/native_list_check.py: reads model command lists (the stream that
// script writes from a track archive's .rmb.bin members), counts what they hold, converts each
// with src/platform/native_list.h and proves the result against a plain PM4 interpreter
// (tests/native_list_reference.h). It prints counts only, as JSON: no word of a list, no name.
//   g++ -std=c++20 -O2 -I forza-recomp/src -I forza-recomp/tests tools/native_list_check.cpp
//   native_list_check FILE|-        (the stream; - is standard input)
// Every part of a list is a block of exactly its size, so a build with
// -fsanitize=address,undefined -D_GLIBCXX_SANITIZE_VECTOR reports a read past any of them.
//
// Stream (little-endian words): "NLC1", then per list: 1, pass group, patch table words, area C
// words, PM4 words, records, entries, flags (1 the header's end marker is there, 2 the list's
// trailer is), then the bytes as they are in the file: the 0xC8-byte header, the patch table,
// area C, the PM4 without its trailer, the group's records and entries (two words each).
#include <algorithm>
#include <chrono>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "native_list_reference.h"
#include "platform/native_list.h"
#include "platform/scene_tiling.h"

namespace nl = forza::native_list;
namespace ref = forza::native_list::reference;

namespace {

constexpr uint32_t kGroups = 21;

struct KeyHash {
  size_t operator()(const nl::Key& k) const { return size_t(k.w[0]) | size_t(k.w[1]) << 32; }
};
using KeySet = std::unordered_set<nl::Key, KeyHash>;

struct Mode {
  const char* name;
  uint64_t select, mask;
};
// Each tile's bin select, no tiling (the command processor's reset values), and a bin mask that
// excludes the bin: no predicated packet runs until a tile block sets the mask again.
constexpr Mode kModes[] = {
    {"tile0", 0x3, 0xFFFFFFFF},    {"tile1", 0xC, 0xFFFFFFFF},
    {"tile2", 0x30, 0xFFFFFFFF},   {"no-tiling", 0xFFFFFFFF, 0xFFFFFFFF},
    {"bin-masked-out", 0x3, 0x30},
};
constexpr uint32_t kModeCount = sizeof(kModes) / sizeof(kModes[0]);

struct Agreement {
  uint64_t runs = 0, all = 0, interpreter = 0, draw_count = 0, ops_state = 0, ops_draws = 0,
           ops_end = 0, view_state = 0, view_draws = 0, view_end = 0, draws = 0;
  void Add(const ref::Verdict& v) {
    ++runs;
    all += v.all();
    interpreter += v.interpreter;
    draw_count += v.draw_count;
    ops_state += v.ops_state;
    ops_draws += v.ops_draws;
    ops_end += v.ops_end;
    view_state += v.view_state;
    view_draws += v.view_draws;
    view_end += v.view_end;
    draws += v.draws;
  }
};

struct Json {
  std::string out;
  bool first = true;
  void Sep() {
    if (!first) out += ",";
    first = false;
  }
  void Key(const std::string& k) {
    Sep();
    out += "\"" + k + "\":";
  }
  void Num(const std::string& k, uint64_t v) {
    Key(k);
    out += std::to_string(v);
  }
  void Open(const std::string& k) {
    Key(k);
    out += "{";
    first = true;
  }
  void Close() {
    out += "}";
    first = false;
  }
  template <typename K>
  void Map(const std::string& k, const std::map<K, uint64_t>& m, bool hex = false) {
    Open(k);
    for (const auto& [key, value] : m) {
      char name[32];
      std::snprintf(name, sizeof(name), hex ? "%04X" : "%u", unsigned(key));
      Num(name, value);
    }
    Close();
  }
  void Agree(const std::string& k, const Agreement& a) {
    Open(k);
    Num("runs", a.runs);
    Num("all", a.all);
    Num("interpreter", a.interpreter);
    Num("draw_count", a.draw_count);
    Num("ops_state", a.ops_state);
    Num("ops_draws", a.ops_draws);
    Num("ops_end", a.ops_end);
    Num("view_state", a.view_state);
    Num("view_draws", a.view_draws);
    Num("view_end", a.view_end);
    Num("draws", a.draws);
    Close();
  }
};

struct Stats {
  // What the lists hold, converted or not.
  uint64_t lists = 0, dwords = 0, marker_missing = 0, trailer_missing = 0, bad_walks = 0;
  uint64_t group_lists[kGroups] = {}, group_dwords[kGroups] = {}, group_draws[kGroups] = {};
  uint64_t group_masks[kGroups][3] =
      {};  // all ones; FFFFFFF7 in the third mask of both sets; other
  std::map<uint32_t, uint64_t> resource, flags, segments, type3, type3_predicated, registers,
      one_register, record_types, entry_types, record_places, primitive, source_select;
  uint64_t packets[4] = {}, draws = 0, wide_indices = 0, draws_predicated = 0;
  uint64_t subcode_entries = 0, subcode_lists = 0, table_entries = 0, table_bad = 0;
  // The entries that name an IM_LOAD's address word, and those whose value word (what the fixup
  // adds the shader's address to) is not a multiple of 4: the key keeps that word's low two bits.
  uint64_t shader_entries = 0, shader_values_unaligned = 0;
  // Conversion.
  uint64_t converted = 0, convert_ns = 0, template_words = 0, pool_words = 0, ops = 0,
           deltas_total = 0;
  std::map<uint32_t, uint64_t> refused, slots;
  uint64_t group_refused[kGroups] = {};
  KeySet keys;
  uint64_t tiled = 0, tile_bound_agree = 0, tile_bound_packets = 0;
  // Lists scene_msaa unties for which Untiled() says "no" of the tiled words and "yes" of the
  // untiled ones, before and after the fixups.
  uint64_t untiled_told = 0;
  // Slots against the game's own lookup records.
  uint64_t record_places_checked = 0, record_places_agree = 0, slots_total = 0, slots_unnamed = 0;
  // Lists whose slots are the same without the buffer fixups (area C): the patch table names
  // every buffer too.
  uint64_t slots_without_area_c = 0;
  // The two views against the interpreter: [as in the file / every place written][mode], tiled
  // words and untiled.
  Agreement agree[2][kModeCount], agree_untiled[2][kModeCount];
  // The key.
  uint64_t key_fixed_up = 0, template_fixed_up = 0, template_flag = 0, key_untiled = 0,
           untiled_refused = 0, key_sees_pm4 = 0, key_sees_table = 0, key_sees_mask = 0;
  // The reduction: distinct per-draw parts over everything read.
  KeySet deltas, fetch_templates, exits, vertex_blobs, pixel_blobs;
  // Draws that load no vertex shader.
  uint64_t odd_lists = 0, odd_draws = 0, odd_one_register = 0, odd_index_offset_only = 0,
           odd_wide = 0, odd_first = 0, odd_no_fetch = 0, odd_carried = 0;
  uint64_t group_odd[kGroups] = {};
};

struct ListIn {
  uint32_t group = 0, flags = 0, records = 0, entries = 0;
  std::vector<uint8_t> header, table, area_c, pm4, record_words;
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

class Checker {
 public:
  void List(const ListIn& l) {
    Census(l);
    const nl::Input in = l.input();
    const auto start = std::chrono::steady_clock::now();
    const nl::Result result = converter_.Convert(in, &t_);
    s.convert_ns += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                 std::chrono::steady_clock::now() - start)
                                 .count());
    if (!result) {
      ++s.refused[uint32_t(result.refuse)];
      ++s.group_refused[l.group];
      return;
    }
    ++s.converted;
    s.template_words += t_.size();
    s.pool_words += t_[nl::kTPoolWords];
    s.ops += t_[nl::kTOpCount];
    s.deltas_total += t_[nl::kTDeltaCount];
    s.keys.insert(nl::TemplateKey(t_.data()));
    Records(l);
    Tiles(l);
    Reduction(l);
    Proof(l);
  }
  Stats s;

 private:
  // Counts over the raw list: nothing here uses the converter.
  void Census(const ListIn& l) {
    const uint32_t n = uint32_t(l.pm4.size() / 4), g = l.group;
    ++s.lists;
    s.dwords += n;
    ++s.group_lists[g];
    s.group_dwords[g] += n;
    s.marker_missing += !(l.flags & 1);
    s.trailer_missing += !(l.flags & 2);
    const uint8_t* h = l.header.data();
    ++s.resource[nl::Word(h, 0) & 0xF];
    ++s.flags[nl::Word(h, nl::kHeaderFlags / 4)];
    ++s.segments[nl::Word(h, nl::kHeaderSegments / 4 + 1)];
    bool ones = true, f7 = true;
    for (uint32_t i = 0; i < nl::kMaskWords; ++i) {
      const uint32_t w = nl::Word(h, nl::kHeaderMasks / 4 + i);
      ones = ones && w == 0xFFFFFFFFu;
      f7 = f7 && w == ((i == 5 || i == 15) ? 0xFFFFFFF7u : 0xFFFFFFFFu);
    }
    ++s.group_masks[g][ones ? 0 : f7 ? 1 : 2];
    for (uint32_t d = 0; d < n;) {
      const uint32_t header = nl::Word(l.pm4.data(), d);
      const uint32_t type = header >> 30, count = ((header >> 16) & 0x3FFF) + 1;
      ++s.packets[type];
      if (type == 2) {
        ++d;
        continue;
      }
      const uint32_t size = type == 1 ? 3 : 1 + count;
      if (d + size > n) {
        ++s.bad_walks;
        break;
      }
      if (type == 0) {
        const uint32_t first = header & 0x7FFF;
        if (header & 0x8000) {
          ++s.one_register[first];
          ++s.registers[first];
        } else {
          for (uint32_t r = first; r < first + count; ++r) ++s.registers[r];
        }
      } else if (type == 3) {
        const uint32_t opcode = (header >> 8) & 0x7F;
        ++s.type3[opcode];
        s.type3_predicated[opcode] += header & 1;
        if (opcode == nl::kPm4DrawIndx && count >= 2) {
          const uint32_t initiator = nl::Word(l.pm4.data(), d + 2);
          ++s.draws;
          ++s.group_draws[g];
          ++s.primitive[initiator & 0x3F];
          ++s.source_select[(initiator >> 6) & 3];
          s.wide_indices += (initiator >> 11) & 1;
          s.draws_predicated += header & 1;
        }
      }
      d += size;
    }
    // The tables, entry by entry: a place's length is in its bits 26-29.
    bool subcode = false;
    for (const std::vector<uint8_t>* table : {&l.table, &l.area_c}) {
      const uint32_t words = uint32_t(table->size() / 4);
      for (uint32_t i = 0; i < words;) {
        const uint32_t entry = nl::Word(table->data(), i), length = (entry >> 26) & 0xF;
        if (length != 1 && length != 2) {
          ++s.table_bad;
          break;
        }
        ++s.table_entries;
        if ((entry & 0x3F) >= 62) {
          ++s.subcode_entries;
          subcode = true;
        }
        const uint32_t dword = (entry >> 6) & 0xFFFFF;
        if (length == 2 && i + 1 < words && (entry & 0x3F) == 0 && dword >= 1 && dword < n &&
            (nl::Word(l.pm4.data(), dword - 1) & 0xFFFFFF00u) == 0xC0012700u) {
          ++s.shader_entries;
          s.shader_values_unaligned += (nl::Word(table->data(), i + 1) & 3) != 0;
        }
        i += length;
      }
    }
    s.subcode_lists += subcode;
    for (uint32_t r = 0; r < l.records + l.entries; ++r) {
      const uint32_t type = nl::Word(l.record_words.data(), r * 2) & 0xF;
      ++(r < l.records ? s.record_types : s.entry_types)[type];
    }
  }

  const uint32_t* Slot(uint32_t dword) const {
    const uint32_t* slots = t_.data() + t_[nl::kTSlots];
    uint32_t lo = 0, hi = t_[nl::kTSlotCount];
    while (lo < hi) {
      const uint32_t mid = (lo + hi) / 2;
      if (slots[mid * nl::kSlotWords + 1] < dword) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    return lo < t_[nl::kTSlotCount] && slots[lo * nl::kSlotWords + 1] == dword
               ? slots + lo * nl::kSlotWords
               : nullptr;
  }

  // The game finds a place through its records: a fixup type and where its chain starts (TU4
  // sub_82D19210's handlers). Each place a record names must be a slot of that type and length,
  // and every slot must be named by a record.
  void Records(const ListIn& l) {
    named_.assign(t_[nl::kTSlotCount], 0);
    const uint32_t* slots = t_.data() + t_[nl::kTSlots];
    for (uint32_t r = 0; r < l.records + l.entries; ++r) {
      const uint32_t type = nl::Word(l.record_words.data(), r * 2) & 0xF;
      const uint32_t where = nl::Word(l.record_words.data(), r * 2 + 1);
      const std::vector<uint8_t>& table = (where & 0x40000000u) ? l.table : l.area_c;
      const uint32_t words = uint32_t(table.size() / 4);
      for (uint32_t i = where & 0xFFFFFF; i < words;) {
        const uint32_t entry = nl::Word(table.data(), i);
        // Types 1, 2, 7-10 read a value word after each entry; 3, 4 and 6 none; 5 by the entry.
        const uint32_t length = type == 5                               ? (entry >> 26) & 0xF
                                : (type == 3 || type == 4 || type == 6) ? 1
                                                                        : 2;
        if (length == 0 || i + length > words) break;
        const uint32_t dword = (entry >> 6) & 0xFFFFF, sub = entry & 0x3F;
        const uint32_t value = length == 2 ? nl::Word(table.data(), i + 1) : 0;
        uint32_t target = dword, size = 1, slot_type = type;
        bool none = false;
        switch (type) {
          case 1:
            target = dword + sub;
            size = 2;
            break;
          case 2:
            target = dword + 2;
            break;
          case 3:
            size = 6;
            break;
          case 5:
            none = sub == nl::kSurfaceModifier;
            if (sub == nl::kSurfaceMarkSize) target = dword + 1;
            break;
          case 7:
          case 8:
            target = dword + (value & 3) * 4;
            size = (value & 0x70) / 4;
            slot_type = nl::kSlotConstants;
            break;
          default:
            break;
        }
        if (r < l.records) ++s.record_places[type];
        if (!none) {
          ++s.record_places_checked;
          const uint32_t* slot = Slot(target);
          if (slot && (slot[0] & 0xFF) == slot_type && slot[3] == size) {
            ++s.record_places_agree;
            named_[size_t(slot - slots) / nl::kSlotWords] = 1;
          }
        }
        if (!(entry & 0x40000000u)) break;
        i += length;
      }
    }
    ListIn bare = l;
    bare.area_c.clear();
    if (converter_.Convert(bare.input(), &other_) &&
        other_[nl::kTSlotCount] == t_[nl::kTSlotCount] &&
        std::equal(slots, slots + t_[nl::kTSlotCount] * nl::kSlotWords,
                   other_.data() + other_[nl::kTSlots])) {
      ++s.slots_without_area_c;
    }
    for (uint32_t i = 0; i < t_[nl::kTSlotCount]; ++i) {
      ++s.slots_total;
      ++s.slots[slots[i * nl::kSlotWords] & 0xFF];
      s.slots_unnamed += !named_[i];
    }
  }

  // The template's tile-bound packets against scene_msaa's own list of them.
  void Tiles(const ListIn& l) {
    if (!(t_[nl::kTFlags] & nl::kTemplateTiled)) return;
    ++s.tiled;
    const uint32_t n = uint32_t(l.pm4.size() / 4);
    host_.resize(n);
    for (uint32_t i = 0; i < n; ++i) host_[i] = nl::Word(l.pm4.data(), i);
    const std::vector<size_t> bound = forza::scene_tiling::TileBoundPackets(host_.data(), n);
    const uint32_t* mine = t_.data() + t_[nl::kTTileBound];
    bool same = bound.size() == t_[nl::kTTileBoundCount];
    for (size_t i = 0; same && i < bound.size(); ++i) {
      same = mine[i * 2] == bound[i] && mine[i * 2 + 1] == host_[bound[i]];
    }
    s.tile_bound_agree += same;
    s.tile_bound_packets += bound.size();
  }

  // The per-draw parts a pack of converted lists would share (the M4 design's reduction): the
  // state registers written since the previous draw, the fetch constants written with their
  // static words, the vertex constants, the static pixel constants, and per list the state
  // registers it leaves written. A place counts as its fixup type, not as a value.
  void Reduction(const ListIn& l) {
    const uint32_t* t = t_.data();
    const uint32_t* pool = t + t[nl::kTPool];
    slot_type_.assign(t[nl::kTPoolWords] + 1, 0);
    const uint32_t* slot = t + t[nl::kTSlots];
    for (uint32_t i = 0; i < t[nl::kTSlotCount]; ++i, slot += nl::kSlotWords) {
      for (uint32_t w = 0; w < slot[3]; ++w) slot_type_[slot[2] + w] = uint8_t(slot[0] & 0xFF);
    }
    std::map<uint32_t, std::pair<uint32_t, uint32_t>> exit;
    const uint32_t draws = nl::DrawCount(t);
    bool odd = false;
    for (uint32_t k = 0; k <= draws; ++k) {
      const uint32_t* r = nl::DrawRecord(t, k);
      std::map<uint32_t, std::pair<uint32_t, uint32_t>> delta;
      nl::Hasher fetch, vertex, pixel;
      bool vertex_shader = false, any_fetch = false;
      for (uint32_t o = r[nl::kDFirstOp]; o < r[nl::kDFirstOp] + r[nl::kDOpCount]; ++o) {
        const uint32_t* op = nl::Op(t, o);
        const uint32_t kind = op[0] & 0xFF;
        if (kind == nl::kOpShader && !(op[0] & nl::kOpPixel)) vertex_shader = true;
        if (kind != nl::kOpRegs || (op[0] & nl::kOpOneRegister)) continue;
        for (uint32_t i = 0; i < op[2]; ++i) {
          const uint32_t reg = op[1] + i, at = op[3] + i;
          const uint32_t place = slot_type_[at], value = pool[at];
          switch (nl::ClassOf(reg)) {
            case nl::RegClass::kState:
              delta[reg] = exit[reg] = {place, place ? place : value};
              break;
            case nl::RegClass::kFetch:
              any_fetch = true;
              fetch.Add(reg);
              fetch.Add(value);
              break;
            case nl::RegClass::kVertexConstant:
              vertex.Add(reg);
              vertex.Add(place);
              vertex.Add(value);
              break;
            case nl::RegClass::kPixelConstant:
              if (place) break;
              pixel.Add(reg);
              pixel.Add(value);
              break;
            default:
              break;
          }
        }
      }
      if (k == draws) break;  // the tail belongs to no draw
      nl::Hasher d;
      for (const auto& [reg, v] : delta) {
        d.Add(reg);
        d.Add(v.first);
        d.Add(v.second);
      }
      s.deltas.insert(d.Finish());
      s.fetch_templates.insert(fetch.Finish());
      s.vertex_blobs.insert(vertex.Finish());
      s.pixel_blobs.insert(pixel.Finish());
      if (!vertex_shader) {
        // A draw that loads no vertex shader: what it sets, and what it has from the draw before.
        ++s.odd_draws;
        odd = true;
        ++s.group_odd[l.group];
        s.odd_one_register += delta.size() == 1;
        s.odd_index_offset_only += delta.size() == 1 && delta.begin()->first == 0x2102;
        s.odd_wide += (r[nl::kDInitiator] >> 11) & 1;
        s.odd_first += k == 0;
        s.odd_no_fetch += !any_fetch;
        s.odd_carried += r[nl::kDVertexShader] != nl::kNone && r[nl::kDVertexFetch] != nl::kNone;
      }
    }
    s.odd_lists += odd;
    nl::Hasher e;
    for (const auto& [reg, v] : exit) {
      e.Add(reg);
      e.Add(v.first);
      e.Add(v.second);
    }
    s.exits.insert(e.Finish());
  }

  void Modes(const std::vector<uint8_t>& pm4, bool untiled, Agreement* to) {
    for (uint32_t m = 0; m < kModeCount; ++m) {
      nl::Run run;
      run.bin_select = kModes[m].select;
      run.bin_mask = kModes[m].mask;
      run.untiled = untiled;
      to[m].Add(harness_.Check(t_.data(), pm4.data(), uint32_t(pm4.size() / 4), run));
    }
  }

  void Proof(const ListIn& l) {
    const uint32_t* t = t_.data();
    const nl::Key key = nl::TemplateKey(t);
    const bool tiled = t[nl::kTFlags] & nl::kTemplateTiled;
    ListIn live = l;
    ref::Random random(s.lists);
    bool told = true;
    for (uint32_t variant = 0; variant < 2; ++variant) {
      if (variant) {
        // Every place written with a made-up value, as after the game's fixups.
        ref::FixUp(t, live.pm4.data(), &random);
        s.key_fixed_up += nl::LiveKey(t, live.input()) == key;
        s.template_fixed_up += converter_.Convert(live.input(), &other_) && other_ == t_;
        ref::StoreWord(live.header.data(), nl::kHeaderFlags / 4,
                       nl::Word(live.header.data(), nl::kHeaderFlags / 4) | nl::kFlagFixedUp);
        s.template_flag += converter_.Convert(live.input(), &other_) && other_ == t_;
      }
      Modes(live.pm4, false, s.agree[variant]);
      if (!tiled) continue;
      std::vector<uint8_t> untiled = live.pm4;  // a copy of exactly the list's size
      const bool changed = ref::Untile(untiled.data(), uint32_t(untiled.size() / 4)) != 0;
      // Of a live list a caller knows what Untiled() says: the untiled runs take that answer,
      // and it has to be what scene_msaa did.
      const bool says = nl::Untiled(t, untiled.data());
      told = told && changed && says && !nl::Untiled(t, live.pm4.data());
      Modes(untiled, says, s.agree_untiled[variant]);
      if (!variant) continue;
      nl::Input flat = live.input();
      flat.pm4 = untiled.data();
      s.key_untiled += nl::LiveKey(t, flat) == key;
      s.untiled_refused += converter_.Convert(flat, &other_).refuse == nl::Refuse::kUntiled;
    }
    s.untiled_told += tiled && told;
    // The key must see a change of any static word: a PM4 word outside every slot, a table word,
    // a mask.
    const uint32_t* slot = t + t[nl::kTSlots];
    const uint32_t* slots_end = slot + t[nl::kTSlotCount] * nl::kSlotWords;
    const uint32_t n = uint32_t(live.pm4.size() / 4);
    uint32_t pick = uint32_t(random.Next() % n), d = pick;
    do {
      const uint32_t* covering = nullptr;
      for (const uint32_t* q = slot; q != slots_end; q += nl::kSlotWords) {
        if (d >= q[1] && d < q[1] + q[3]) covering = q;
      }
      if (!covering) break;
      d = (d + 1) % n;
    } while (d != pick);
    ListIn changed = live;
    ref::StoreWord(changed.pm4.data(), d, nl::Word(changed.pm4.data(), d) ^ 0x00010000u);
    s.key_sees_pm4 += nl::LiveKey(t, changed.input()) != key;
    changed = live;
    if (!changed.table.empty()) {
      const uint32_t w = uint32_t(random.Next() % (changed.table.size() / 4));
      ref::StoreWord(changed.table.data(), w, nl::Word(changed.table.data(), w) ^ 0x40);
    }
    s.key_sees_table += changed.table.empty() || nl::LiveKey(t, changed.input()) != key;
    changed = live;
    changed.header[nl::kHeaderMasks + random.Next() % (nl::kMaskWords * 4)] ^= 0x10;
    s.key_sees_mask += nl::LiveKey(t, changed.input()) != key;
  }

  nl::Converter converter_;
  ref::Harness harness_;
  std::vector<uint32_t> t_, other_, host_;
  std::vector<uint8_t> named_, slot_type_;
};

bool Read(std::FILE* f, void* to, size_t bytes) { return std::fread(to, 1, bytes, f) == bytes; }
// The stream's next `bytes` as a list's part: a vector made for it, of exactly that size. With
// no spare capacity behind the part, a sanitizer build sees a read past its end.
bool ReadPart(std::FILE* f, size_t bytes, std::vector<uint8_t>* to) {
  std::vector<uint8_t> part(bytes);
  if (bytes && !Read(f, part.data(), bytes)) return false;
  *to = std::move(part);
  return true;
}
bool ReadWords(std::FILE* f, uint32_t* to, size_t count) {
  uint8_t raw[4];
  for (size_t i = 0; i < count; ++i) {
    if (!Read(f, raw, 4)) return false;
    to[i] =
        uint32_t(raw[0]) | uint32_t(raw[1]) << 8 | uint32_t(raw[2]) << 16 | uint32_t(raw[3]) << 24;
  }
  return true;
}

void GroupArray(Json& j, const char* name, const uint64_t* values) {
  j.Open(name);
  for (uint32_t g = 0; g < kGroups; ++g) {
    if (values[g]) j.Num(std::to_string(g), values[g]);
  }
  j.Close();
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "usage: native_list_check FILE|-\n");
    return 2;
  }
  std::FILE* f = stdin;
  if (std::strcmp(argv[1], "-") != 0) {
    f = std::fopen(argv[1], "rb");
  } else {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
#endif
  }
  uint32_t magic = 0;
  if (!f || !ReadWords(f, &magic, 1) || magic != 0x31434C4Eu) {
    std::fprintf(stderr, "native_list_check: not a list stream\n");
    return 2;
  }
  Checker checker;
  ListIn l;
  for (;;) {
    uint32_t head[8];
    if (!ReadWords(f, head, 1)) break;
    if (head[0] != 1 || !ReadWords(f, head + 1, 7) || head[1] >= kGroups || head[2] > (1u << 24) ||
        head[3] > (1u << 24) || head[4] > (1u << 24) || head[5] > (1u << 20) ||
        head[6] > (1u << 20)) {
      std::fprintf(stderr, "native_list_check: damaged stream\n");
      return 2;
    }
    l.group = head[1];
    l.records = head[5];
    l.entries = head[6];
    l.flags = head[7];
    if (!ReadPart(f, nl::kHeaderBytes, &l.header) || !ReadPart(f, size_t(head[2]) * 4, &l.table) ||
        !ReadPart(f, size_t(head[3]) * 4, &l.area_c) || !ReadPart(f, size_t(head[4]) * 4, &l.pm4) ||
        !ReadPart(f, (size_t(head[5]) + head[6]) * 8, &l.record_words)) {
      std::fprintf(stderr, "native_list_check: stream cut off\n");
      return 2;
    }
    checker.List(l);
  }

  const Stats& s = checker.s;
  Json j;
  j.out = "{";
  j.Open("census");
  j.Num("lists", s.lists);
  j.Num("dwords", s.dwords);
  j.Num("marker_missing", s.marker_missing);
  j.Num("trailer_missing", s.trailer_missing);
  j.Num("bad_walks", s.bad_walks);
  GroupArray(j, "group_lists", s.group_lists);
  GroupArray(j, "group_dwords", s.group_dwords);
  GroupArray(j, "group_draws", s.group_draws);
  uint64_t masks[3][kGroups] = {};
  for (uint32_t g = 0; g < kGroups; ++g) {
    for (uint32_t c = 0; c < 3; ++c) masks[c][g] = s.group_masks[g][c];
  }
  GroupArray(j, "group_masks_all_ones", masks[0]);
  GroupArray(j, "group_masks_f7", masks[1]);
  GroupArray(j, "group_masks_other", masks[2]);
  j.Map("resource_type", s.resource);
  j.Map("flags", s.flags, true);
  j.Map("segments", s.segments);
  j.Num("type0", s.packets[0]);
  j.Num("type1", s.packets[1]);
  j.Num("type2", s.packets[2]);
  j.Num("type3", s.packets[3]);
  j.Map("type3_opcodes", s.type3, true);
  j.Map("type3_predicated", s.type3_predicated, true);
  j.Map("registers", s.registers, true);
  j.Map("one_register_packets", s.one_register, true);
  j.Num("draws", s.draws);
  j.Num("draws_predicated", s.draws_predicated);
  j.Num("draws_32bit_indices", s.wide_indices);
  j.Map("primitive_types", s.primitive);
  j.Map("source_select", s.source_select);
  j.Map("record_types", s.record_types);
  j.Map("entry_types", s.entry_types);
  j.Num("table_entries", s.table_entries);
  j.Num("table_bad", s.table_bad);
  j.Num("subcode_62_63_entries", s.subcode_entries);
  j.Num("subcode_62_63_lists", s.subcode_lists);
  j.Num("shader_address_entries", s.shader_entries);
  j.Num("shader_address_values_unaligned", s.shader_values_unaligned);
  j.Close();

  j.Open("conversion");
  j.Num("converted", s.converted);
  j.Open("refused");
  for (const auto& [reason, count] : s.refused) j.Num(nl::RefuseName(nl::Refuse(reason)), count);
  j.Close();
  GroupArray(j, "group_refused", s.group_refused);
  j.Num("distinct_keys", s.keys.size());
  j.Num("convert_ns", s.convert_ns);
  j.Num("template_words", s.template_words);
  j.Num("pool_words", s.pool_words);
  j.Num("ops", s.ops);
  j.Num("delta_runs", s.deltas_total);
  j.Num("slots", s.slots_total);
  j.Map("slots_by_type", s.slots);
  j.Map("record_places_by_type", s.record_places);
  j.Num("record_places_checked", s.record_places_checked);
  j.Num("record_places_agree", s.record_places_agree);
  j.Num("slots_no_record_names", s.slots_unnamed);
  j.Num("same_slots_without_area_c", s.slots_without_area_c);
  j.Num("tiled", s.tiled);
  j.Num("tile_bound_agree", s.tile_bound_agree);
  j.Num("tile_bound_packets", s.tile_bound_packets);
  j.Num("untiled_told", s.untiled_told);
  j.Close();

  j.Open("proof");
  static const char* const variants[2] = {"as_extracted", "fixed_up"};
  for (uint32_t v = 0; v < 2; ++v) {
    for (uint32_t m = 0; m < kModeCount; ++m) {
      j.Agree(std::string(variants[v]) + "/" + kModes[m].name, s.agree[v][m]);
    }
    for (uint32_t m = 0; m < kModeCount; ++m) {
      j.Agree(std::string(variants[v]) + "/untiled/" + kModes[m].name, s.agree_untiled[v][m]);
    }
  }
  j.Close();

  j.Open("key");
  j.Num("same_after_fixups", s.key_fixed_up);
  j.Num("same_template_after_fixups", s.template_fixed_up);
  j.Num("same_template_with_0x2000", s.template_flag);
  j.Num("same_untiled", s.key_untiled);
  j.Num("untiled_refused_as_input", s.untiled_refused);
  j.Num("sees_changed_pm4_word", s.key_sees_pm4);
  j.Num("sees_changed_table_word", s.key_sees_table);
  j.Num("sees_changed_mask", s.key_sees_mask);
  j.Close();

  j.Open("reduction");
  j.Num("register_deltas", s.deltas.size());
  j.Num("fetch_templates", s.fetch_templates.size());
  j.Num("exit_sets", s.exits.size());
  j.Num("vertex_constant_blobs", s.vertex_blobs.size());
  j.Num("pixel_constant_blobs", s.pixel_blobs.size());
  j.Close();

  j.Open("draws_without_vertex_shader");
  j.Num("draws", s.odd_draws);
  j.Num("lists", s.odd_lists);
  GroupArray(j, "by_group", s.group_odd);
  j.Num("one_state_register", s.odd_one_register);
  j.Num("only_vgt_indx_offset", s.odd_index_offset_only);
  j.Num("no_fetch_constant", s.odd_no_fetch);
  j.Num("32bit_indices", s.odd_wide);
  j.Num("first_draw_of_list", s.odd_first);
  j.Num("shader_and_vertex_buffer_carried", s.odd_carried);
  j.Close();
  j.out += "}\n";
  std::fputs(j.out.c_str(), stdout);
  return 0;
}
