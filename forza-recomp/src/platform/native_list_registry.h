// forza - native renderer M4 (docs/native-renderer.md, "M4, third step"): the game's side of the
// model list registry, without guest memory, for tests/native_list_registry_test.cpp.
//
// With native_lists=shadow (src/hooks/native_lists.cpp) a model list is converted when it first
// runs (src/platform/native_list.h), its template is sent to the GPU plugin once, and the list's
// physical address is bound to the template, all by host packets in the D3D device's command
// buffer just before the RunCommandBuffer call (ReIGlue 0650: kOpListDefine, kOpListBind,
// kOpListDrop; the game's copy of their layout is below, keep it ReIGlue's
// rex/graphics/fh1_host_packet.h). This header holds what decides which packets go out:
// - Lists: per command buffer the list it was bound with (physical address, dwords, patch table
//   words), so a command buffer object reused for another list after an unload is bound again;
//   the lists an unloaded model's memory held, to drop;
// - Templates: one id per template key (lists with the same static words share one), with a
//   check of the template's words against the first one with that key;
// - DefineRing: where in a ring of guest memory a template goes, reused only once the plugin has
//   said (its acknowledgement word) that it read what was there;
// - Slots: the state words (one per bound list: its generation, odd while a fixup writes it).
#pragma once

#include <cstdint>
#include <cstring>
#include <deque>
#include <unordered_map>
#include <utility>
#include <vector>

#include "platform/native_list.h"

namespace forza::native_list {

// ReIGlue 0650 (rex/graphics/fh1_host_packet.h).
constexpr uint32_t kMagicHost = 0x4648314Eu;  // "FH1N", a host packet's first word
constexpr uint32_t kOpListDefine = 0x20, kOpListBind = 0x21, kOpListDrop = 0x22;
constexpr uint32_t kListDefineWords = 8, kListBindWords = 12, kListDropWords = 4;
constexpr uint32_t kListRefused = 0xFFFFFFFFu;
constexpr uint32_t kListGroupNone = 0xFF;
constexpr uint32_t kListRetiled = 1;
// Refusal codes beyond the converter's (Refuse): the game's own reasons not to convert.
constexpr uint32_t kRefuseRetile = uint32_t(Refuse::kCount);      // untiled, saved headers wrong
constexpr uint32_t kRefuseNoSlot = uint32_t(Refuse::kCount) + 1;  // no state word left
constexpr uint32_t kRefuseCollision = uint32_t(Refuse::kCount) + 2;  // a key with other words
constexpr uint32_t kRefuseTooLarge = uint32_t(Refuse::kCount) + 3;   // larger than the whole ring

// The hash a define carries: the first word of the 128-bit hash of the template's words.
inline uint32_t TemplateHash(const uint32_t* words, size_t count) {
  Hasher h;
  for (size_t i = 0; i < count; ++i) h.Add(words[i]);
  return h.Finish().w[0];
}

struct Bound {
  uint32_t phys = 0, words = 0, patch_words = 0, slot = kNone, template_id = kListRefused;
};

class Lists {
 public:
  enum class Look { kCurrent, kFresh, kReused };
  // Command buffer cb runs now with its segment at phys, `words` long, and a patch table of
  // `patch_words`: kCurrent if it is bound with exactly that, kReused if it was bound with
  // another list (`*old` is that binding), kFresh if it is not bound.
  Look Find(uint32_t cb, uint32_t phys, uint32_t words, uint32_t patch_words, Bound* old) const {
    auto it = bound_.find(cb);
    if (it == bound_.end()) return Look::kFresh;
    const Bound& b = it->second;
    if (b.phys == phys && b.words == words && b.patch_words == patch_words) return Look::kCurrent;
    *old = b;
    return Look::kReused;
  }
  void Set(uint32_t cb, const Bound& b) { bound_[cb] = b; }
  bool Erase(uint32_t cb, Bound* old) {
    auto it = bound_.find(cb);
    if (it == bound_.end()) return false;
    *old = it->second;
    bound_.erase(it);
    return true;
  }
  const Bound* Get(uint32_t cb) const {
    auto it = bound_.find(cb);
    return it == bound_.end() ? nullptr : &it->second;
  }
  // A model's memory is freed: its headers (command buffers) in [a, a + a_bytes), its PM4 in the
  // physical range [b, b + b_bytes). Every list in either is removed and returned, to drop.
  std::vector<Bound> Unload(uint32_t a, uint32_t a_bytes, uint32_t b, uint32_t b_bytes,
                            uint32_t* by_header = nullptr, uint32_t* by_address = nullptr) {
    std::vector<Bound> out;
    for (auto it = bound_.begin(); it != bound_.end();) {
      const bool header = it->first - a < a_bytes;
      const bool address = it->second.phys - b < b_bytes;
      if (header || address) {
        if (by_header && header) ++*by_header;
        if (by_address && address && !header) ++*by_address;
        out.push_back(it->second);
        it = bound_.erase(it);
      } else {
        ++it;
      }
    }
    return out;
  }
  size_t size() const { return bound_.size(); }

 private:
  std::unordered_map<uint32_t, Bound> bound_;
};

class Templates {
 public:
  // The id of the template with this key (lists with the same static words share it), or 0 if
  // the key came before with other template words (a hash collision, never seen: `*collision`).
  uint32_t Id(const Key& key, uint32_t words_hash, uint32_t words, bool* collision) {
    *collision = false;
    auto [it, fresh] = by_key_.try_emplace(key, Entry{next_id_, words_hash, words, false});
    if (fresh) return next_id_++;
    if (it->second.hash != words_hash || it->second.words != words) {
      *collision = true;
      return 0;
    }
    return it->second.id;
  }
  // Whether the template has been sent (its define written), and that it has.
  bool Sent(const Key& key) const {
    auto it = by_key_.find(key);
    return it != by_key_.end() && it->second.sent;
  }
  void MarkSent(const Key& key) {
    auto it = by_key_.find(key);
    if (it != by_key_.end()) it->second.sent = true;
  }
  size_t size() const { return by_key_.size(); }

 private:
  struct KeyHash {
    size_t operator()(const Key& k) const { return size_t(k.w[0]) | size_t(k.w[1]) << 32; }
  };
  struct Entry {
    uint32_t id, hash, words;
    bool sent;
  };
  std::unordered_map<Key, Entry, KeyHash> by_key_;
  uint32_t next_id_ = 1;
};

// A ring of `capacity` bytes for template words: Reserve finds room for one, after letting go of
// every block whose sequence number the plugin has acknowledged (`acked`, the last it read).
class DefineRing {
 public:
  static constexpr uint32_t kAlign = 64;
  explicit DefineRing(uint32_t capacity) : capacity_(capacity) {}
  // Whether a template of `bytes` can ever go in (one that cannot is refused, not retried).
  bool Fits(uint32_t bytes) const {
    return bytes && ((uint64_t(bytes) + kAlign - 1) & ~uint64_t(kAlign - 1)) <= capacity_;
  }
  bool Reserve(uint32_t bytes, uint32_t acked, uint32_t* offset, uint32_t* sequence) {
    bytes = (bytes + kAlign - 1) & ~(kAlign - 1);
    if (!bytes || bytes > capacity_) return false;
    while (!live_.empty() && int32_t(acked - live_.front().sequence) >= 0) live_.pop_front();
    uint32_t at;
    if (live_.empty()) {
      at = 0;
    } else {
      const uint32_t tail = live_.front().offset;
      const uint32_t head = live_.back().offset + live_.back().bytes;
      const bool wrapped = live_.back().offset < tail;
      if (wrapped) {
        if (head + bytes > tail) return false;
        at = head;
      } else if (head + bytes <= capacity_) {
        at = head;
      } else if (bytes <= tail) {
        at = 0;
      } else {
        return false;
      }
    }
    live_.push_back({next_sequence_, at, bytes});
    *offset = at;
    *sequence = next_sequence_++;
    return true;
  }
  size_t outstanding() const { return live_.size(); }

 private:
  struct Block {
    uint32_t sequence, offset, bytes;
  };
  std::deque<Block> live_;
  uint32_t capacity_;
  uint32_t next_sequence_ = 1;
};

// State words: `count` of them, handed out and given back.
class Slots {
 public:
  explicit Slots(uint32_t count) : count_(count) {}
  bool Take(uint32_t* slot) {
    if (!free_.empty()) {
      *slot = free_.back();
      free_.pop_back();
      return true;
    }
    if (next_ >= count_) return false;
    *slot = next_++;
    return true;
  }
  void Give(uint32_t slot) { free_.push_back(slot); }
  uint32_t used() const { return next_ - uint32_t(free_.size()); }

 private:
  std::vector<uint32_t> free_;
  uint32_t count_, next_ = 0;
};

// A list that scene_msaa has untiled (its tile-bound packet headers made NOPs), put back from the
// headers src/hooks/scene_tiling.cpp kept: `saved` holds (guest address, original header) pairs,
// `pm4` the list's words (guest order) as copied from guest address `pm4_address`. False if a
// saved word is not the NOP of its header (then the copy is not the list as recorded).
inline bool Retile(uint8_t* pm4, uint32_t pm4_words, uint32_t pm4_address,
                   const std::vector<std::pair<uint32_t, uint32_t>>& saved) {
  if (saved.empty()) return false;
  for (const auto& [address, header] : saved) {
    const uint32_t index = (address - pm4_address) / 4;
    if (address < pm4_address || (address - pm4_address) % 4 || index >= pm4_words ||
        Word(pm4, index) != scene_tiling::NopPacket(header)) {
      return false;
    }
  }
  for (const auto& [address, header] : saved) {
    const uint32_t index = (address - pm4_address) / 4;
    const uint32_t g = __builtin_bswap32(header);
    std::memcpy(pm4 + size_t(index) * 4, &g, 4);
  }
  return true;
}

}  // namespace forza::native_list
