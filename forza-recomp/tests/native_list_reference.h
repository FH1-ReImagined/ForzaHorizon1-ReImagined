// The yardstick for the model list converter (src/platform/native_list.h): a plain PM4
// interpreter that knows nothing of templates, a register file that remembers what a list wrote,
// and the comparison of the converter's two views with the interpreter, draw by draw. Used by
// tests/native_list_test.cpp on synthetic lists and by tools/native_list_check.cpp on the lists
// of a player's own game files.
#pragma once

#include <cstdint>
#include <cstring>
#include <memory>
#include <vector>

#include "platform/native_list.h"
#include "platform/scene_tiling.h"

namespace forza::native_list::reference {

// The registers a list wrote since Reset, each with where its value came from, the bound
// shaders and the bin mask. It is the sink of ReplayOps and StateAtDraw, and the interpreter's.
class Machine {
 public:
  static constexpr uint32_t kRegisters = 0x8000;
  enum : uint8_t { kUnwritten, kValue, kMemory };

  Machine() : value_(kRegisters), offset_(kRegisters), origin_(kRegisters, kUnwritten) {}
  void Reset(uint64_t bin_mask) {
    for (uint16_t reg : touched_) origin_[reg] = kUnwritten;
    touched_.clear();
    bin_mask_ = bin_mask;
    shader_[0] = shader_[1] = false;
    draws_ = 0;
  }

  void Registers(uint32_t first, uint32_t count, const uint32_t* values) {
    for (uint32_t i = 0; i < count; ++i) Set(first + i, kValue, values[i], 0);
  }
  // Word `offset + i` of the block of memory at `address` (the address word as it is in the list).
  void Literals(uint32_t first, uint32_t count, uint32_t address, uint32_t offset) {
    for (uint32_t i = 0; i < count; ++i) Set(first + i, kMemory, address, offset + i);
  }
  void Shader(bool pixel, uint32_t address, uint32_t size_word) {
    shader_[pixel] = true;
    shader_address_[pixel] = address;
    shader_size_[pixel] = size_word;
  }
  void BinMask(uint64_t mask) { bin_mask_ = mask; }
  void Draw(const DrawCall& call) {
    last_ = call;
    ++draws_;
    if (!call.executed) return;
    // As the command processor's draw packet does: the initiator, then the index address and size.
    Set(kRegDrawInitiator, kValue, call.initiator, 0);
    Set(kRegDmaBase, kValue, call.index_address, 0);
    Set(kRegDmaSize, kValue, call.size_word, 0);
  }

  bool operator==(const Machine& o) const {
    if (touched_.size() != o.touched_.size() || bin_mask_ != o.bin_mask_) return false;
    for (uint32_t s = 0; s < 2; ++s) {
      if (shader_[s] != o.shader_[s]) return false;
      if (shader_[s] &&
          (shader_address_[s] != o.shader_address_[s] || shader_size_[s] != o.shader_size_[s])) {
        return false;
      }
    }
    for (uint16_t reg : touched_) {
      if (origin_[reg] != o.origin_[reg] || value_[reg] != o.value_[reg]) return false;
      if (origin_[reg] == kMemory && offset_[reg] != o.offset_[reg]) return false;
    }
    return true;
  }
  bool operator!=(const Machine& o) const { return !(*this == o); }

  uint8_t origin(uint32_t reg) const { return origin_[reg]; }
  uint32_t value(uint32_t reg) const { return value_[reg]; }
  // Whether registers [first, first + count) hold exactly `values`, written as values.
  bool Holds(uint32_t first, uint32_t count, const uint32_t* values) const {
    for (uint32_t i = 0; i < count; ++i) {
      if (origin_[first + i] != kValue || value_[first + i] != values[i]) return false;
    }
    return true;
  }
  bool Unwritten(uint32_t first, uint32_t count) const {
    for (uint32_t i = 0; i < count; ++i) {
      if (origin_[first + i] != kUnwritten) return false;
    }
    return true;
  }
  uint64_t bin_mask() const { return bin_mask_; }
  bool shader(bool pixel) const { return shader_[pixel]; }
  uint32_t shader_address(bool pixel) const { return shader_address_[pixel]; }
  uint32_t shader_size(bool pixel) const { return shader_size_[pixel]; }
  const DrawCall& last_draw() const { return last_; }
  uint32_t draws() const { return draws_; }
  size_t written() const { return touched_.size(); }

 private:
  void Set(uint32_t reg, uint8_t origin, uint32_t value, uint32_t offset) {
    reg &= kRegisters - 1;
    if (origin_[reg] == kUnwritten) touched_.push_back(uint16_t(reg));
    origin_[reg] = origin;
    value_[reg] = value;
    offset_[reg] = offset;
  }
  std::vector<uint32_t> value_, offset_;
  std::vector<uint8_t> origin_;
  std::vector<uint16_t> touched_;
  uint64_t bin_mask_ = 0;
  bool shader_[2] = {};
  uint32_t shader_address_[2] = {}, shader_size_[2] = {};
  DrawCall last_;
  uint32_t draws_ = 0;
};

// PM4 as the SDK's command processor executes it (ReIGlue, command_processor.cpp): type-0
// register ranges (bit 15: every word to one register), type-1 register pairs, type-2 padding,
// and of type 3 the packets a list can hold plus NOP. A type-3 packet with the predicate bit runs
// only if bin select & bin mask is not zero. It stops before each draw packet so the state can
// be looked at, and at the first packet it does not know (ok() is then false).
class Interpreter {
 public:
  Interpreter(const uint8_t* pm4, uint32_t words, uint64_t bin_select, Machine* machine)
      : pm4_(pm4), words_(words), bin_select_(bin_select), machine_(machine) {}

  // Runs up to the next draw packet and describes it; false at the list's end.
  bool NextDraw(DrawCall* call) {
    while (at_ < words_ && ok_) {
      const uint32_t header = Word(pm4_, at_);
      const uint32_t count = ((header >> 16) & 0x3FFF) + 1;
      switch (header >> 30) {
        case 0: {
          if (at_ + 1 + count > words_) return Fail();
          const uint32_t base = header & 0x7FFF;
          for (uint32_t i = 0; i < count; ++i) {
            const uint32_t value = Word(pm4_, at_ + 1 + i);
            machine_->Registers((header & 0x8000) ? base : base + i, 1, &value);
          }
          at_ += 1 + count;
          break;
        }
        case 1: {
          if (at_ + 3 > words_) return Fail();
          const uint32_t a = Word(pm4_, at_ + 1), b = Word(pm4_, at_ + 2);
          machine_->Registers(header & 0x7FF, 1, &a);
          machine_->Registers((header >> 11) & 0x7FF, 1, &b);
          at_ += 3;
          break;
        }
        case 2:
          ++at_;
          break;
        default: {
          if (at_ + 1 + count > words_) return Fail();
          const uint32_t opcode = (header >> 8) & 0x7F;
          const bool predicated = header & 1;
          const bool run = !predicated || (bin_select_ & machine_->bin_mask()) != 0;
          if (opcode == 0x22) {  // DRAW_INDX: viz query, initiator, index address, size
            if (count < 2) return Fail();
            const uint32_t initiator = Word(pm4_, at_ + 2);
            if (((initiator >> 6) & 3) != 0 || count < 4) return Fail();
            call->draw = machine_->draws();
            call->predicated = predicated;
            call->executed = run;
            call->initiator = initiator;
            call->index_address = Word(pm4_, at_ + 3);
            call->size_word = Word(pm4_, at_ + 4);
            pending_ = *call;
            at_ += 1 + count;
            return true;
          }
          if (run && !Type3(opcode, count)) return Fail();
          at_ += 1 + count;
          break;
        }
      }
    }
    return false;
  }
  // The draw NextDraw stopped at.
  void ExecuteDraw() { machine_->Draw(pending_); }
  bool ok() const { return ok_; }

 private:
  bool Fail() {
    ok_ = false;
    return false;
  }
  bool Type3(uint32_t opcode, uint32_t count) {
    const uint32_t first = Word(pm4_, at_ + 1);
    switch (opcode) {
      case 0x10:  // NOP
        return true;
      case 0x60:  // SET_BIN_MASK_LO
        machine_->BinMask((machine_->bin_mask() & 0xFFFFFFFF00000000ull) | first);
        return true;
      case 0x61:  // SET_BIN_MASK_HI
        machine_->BinMask((machine_->bin_mask() & 0xFFFFFFFFull) | uint64_t(first) << 32);
        return true;
      case 0x2D: {  // SET_CONSTANT: the kind and first index, then the values
        static constexpr uint32_t bases[5] = {0x4000, 0x4800, 0x4900, 0x4908, 0x2000};
        const uint32_t kind = (first >> 16) & 0xFF;
        if (kind > 4) return false;
        for (uint32_t i = 0; i + 1 < count; ++i) {
          const uint32_t value = Word(pm4_, at_ + 2 + i);
          machine_->Registers(bases[kind] + (first & 0x7FF) + i, 1, &value);
        }
        return true;
      }
      case 0x2F: {  // LOAD_ALU_CONSTANT: address, kind and first index, words
        if (count < 3) return false;
        const uint32_t target = Word(pm4_, at_ + 2);
        if (((target >> 16) & 0xFF) != 0) return false;
        machine_->Literals(0x4000 + (target & 0x7FF), Word(pm4_, at_ + 3) & 0xFFF, first, 0);
        return true;
      }
      case 0x27: {  // IM_LOAD: address with the shader type in its low bits, size
        if (count < 2 || (first & 3) > 1) return false;
        machine_->Shader((first & 3) == 1, first, Word(pm4_, at_ + 2));
        return true;
      }
      default:
        return false;
    }
  }

  const uint8_t* pm4_;
  uint32_t words_;
  uint64_t bin_select_;
  Machine* machine_;
  uint32_t at_ = 0;
  bool ok_ = true;
  DrawCall pending_;
};

// What agreed when a template ran beside the interpreter over one list in one mode.
struct Verdict {
  bool interpreter = true;  // the interpreter knew every packet
  bool draw_count = true;   // as many draw packets as the template has draws
  bool ops_state = true;    // view 1: the state before every draw
  bool ops_draws = true;    // view 1: every draw's arguments and whether it ran
  bool ops_end = true;      // view 1: the state after the list
  bool view_state = true;   // view 2: the state before every draw
  bool view_draws = true;   // view 2: every draw's arguments and what is bound
  bool view_end = true;     // view 2: the state after the list
  uint32_t draws = 0;
  bool all() const {
    return interpreter && draw_count && ops_state && ops_draws && ops_end && view_state &&
           view_draws && view_end;
  }
};

class Harness {
 public:
  // The list `pm4` (the live words: fixed up or not, untiled or not) by template t, for one bin
  // select and starting bin mask. `run.untiled` must say whether the words are untiled.
  Verdict Check(const uint32_t* t, const uint8_t* pm4, uint32_t words, const Run& run) {
    Verdict v;
    // A block of exactly the pool's size, new for each run: a sanitizer build then sees a view
    // that reads a word past it.
    const std::unique_ptr<uint32_t[]> resolved(new uint32_t[t[kTPoolWords]]);
    Resolve(t, pm4, resolved.get());
    const uint32_t* pool = resolved.get();
    interpreted_.Reset(run.bin_mask);
    replayed_.Reset(run.bin_mask);
    Interpreter interpreter(pm4, words, run.bin_select, &interpreted_);
    Run replay = run;
    const uint32_t draws = DrawCount(t);
    for (uint32_t k = 0; k < draws; ++k) {
      DrawCall call;
      if (!interpreter.NextDraw(&call)) {
        v.draw_count = false;
        break;
      }
      ++v.draws;
      const uint32_t* r = DrawRecord(t, k);
      ReplayOps(t, pool, r[kDFirstOp], r[kDOpCount] - 1, replay, replayed_);
      if (interpreted_ != replayed_) v.ops_state = false;
      folded_.Reset(run.bin_mask);
      StateAtDraw(t, pool, k, run, folded_);
      if (interpreted_ != folded_) v.view_state = false;
      if (!Bound(Draw(t, pool, k), call)) v.view_draws = false;
      interpreter.ExecuteDraw();
      ReplayOps(t, pool, r[kDFirstOp] + r[kDOpCount] - 1, 1, replay, replayed_);
      const DrawCall& mine = replayed_.last_draw();
      if (replayed_.draws() != k + 1 || mine.draw != k || mine.predicated != call.predicated ||
          mine.executed != call.executed || mine.initiator != call.initiator ||
          mine.index_address != call.index_address || mine.size_word != call.size_word) {
        v.ops_draws = false;
      }
    }
    DrawCall extra;
    if (v.draw_count && interpreter.NextDraw(&extra)) v.draw_count = false;
    v.interpreter = interpreter.ok();
    if (v.draw_count) {
      const uint32_t* tail = DrawRecord(t, draws);
      ReplayOps(t, pool, tail[kDFirstOp], tail[kDOpCount], replay, replayed_);
      if (interpreted_ != replayed_ || replay.bin_mask != interpreted_.bin_mask()) {
        v.ops_end = false;
      }
      folded_.Reset(run.bin_mask);
      StateAtDraw(t, pool, draws, run, folded_);
      if (interpreted_ != folded_) v.view_end = false;
    } else {
      v.ops_end = v.view_end = false;
    }
    return v;
  }
  // The interpreter's state where the last Check ended: after the list.
  const Machine& interpreted() const { return interpreted_; }

 private:
  // Draw k's record against the interpreter at that draw: the arguments, and that what the
  // record says is bound is what the registers hold.
  bool Bound(const DrawView& view, const DrawCall& call) const {
    if (view.predicated != call.predicated || view.initiator != call.initiator ||
        view.index_address != call.index_address || view.size_word != call.size_word) {
      return false;
    }
    for (uint32_t s = 0; s < 2; ++s) {
      if (view.shader[s] != interpreted_.shader(s)) return false;
      if (view.shader[s] && (view.shader_address[s] != interpreted_.shader_address(s) ||
                             view.shader_size[s] != interpreted_.shader_size(s))) {
        return false;
      }
    }
    if (view.vertex_fetch ? !interpreted_.Holds(kRegVertexFetch, 6, view.vertex_fetch)
                          : !interpreted_.Unwritten(kRegVertexFetch, 6)) {
      return false;
    }
    for (uint32_t i = 0; i < 8; ++i) {
      if (view.texture[i] ? !interpreted_.Holds(kRegFetch + i * 6, 6, view.texture[i])
                          : !interpreted_.Unwritten(kRegFetch + i * 6, 6)) {
        return false;
      }
    }
    return true;
  }

  Machine interpreted_, replayed_, folded_;
};

// A small generator for made-up fixup values (xorshift64*).
class Random {
 public:
  explicit Random(uint64_t seed) : state_(seed * 0x9E3779B97F4A7C15ull + 1) {}
  uint32_t Next() {
    state_ ^= state_ >> 12;
    state_ ^= state_ << 25;
    state_ ^= state_ >> 27;
    return uint32_t((state_ * 0x2545F4914F6CDD1Dull) >> 32);
  }

 private:
  uint64_t state_;
};

inline void StoreWord(uint8_t* guest, size_t index, uint32_t value) {
  const uint32_t v = __builtin_bswap32(value);
  std::memcpy(guest + index * 4, &v, 4);
}

// Writes made-up values into every slot of the list, as the game's fixups would write real ones.
// A microcode address keeps its low two bits (the shader type), as it does in the game.
inline void FixUp(const uint32_t* t, uint8_t* pm4, Random* random) {
  const uint32_t* slot = t + t[kTSlots];
  for (uint32_t s = 0; s < t[kTSlotCount]; ++s, slot += kSlotWords) {
    const uint32_t type = slot[0] & 0xFF;
    const bool microcode =
        (type == kSlotVertexShader || type == kSlotPixelShader) && (slot[0] >> 8) == 0;
    for (uint32_t i = 0; i < slot[3]; ++i) {
      uint32_t value = random->Next();
      if (microcode) value = (value & ~3u) | (Word(pm4, slot[1] + i) & 3);
      StoreWord(pm4, slot[1] + i, value);
    }
  }
}

// Whether scene_msaa unties a list with these header flags (src/hooks/scene_tiling.cpp,
// UntileList): one recorded for the tiles whose word 4 reads as a SET_CONSTANT packet. It asks
// nothing else of the word, so neither does this.
inline bool SceneMsaaUnties(uint32_t cb_flags, const uint8_t* pm4, uint32_t words) {
  if (!(cb_flags & kFlagTiles) || words <= 4) return false;
  const uint32_t fifth = Word(pm4, 4);
  return fifth >> 30 == 3 && ((fifth >> 8) & 0x7F) == scene_tiling::kPm4SetConstant;
}

// Untiles a list the way scene_msaa does (src/hooks/scene_tiling.cpp, UntileList): every
// tile-bound packet's header becomes a NOP of the same length. Returns how many.
inline size_t Untile(uint8_t* pm4, uint32_t words) {
  std::vector<uint32_t> host(words);
  for (uint32_t i = 0; i < words; ++i) host[i] = Word(pm4, i);
  const std::vector<size_t> bound = scene_tiling::TileBoundPackets(host.data(), host.size());
  for (size_t i : bound) StoreWord(pm4, i, scene_tiling::NopPacket(host[i]));
  return bound.size();
}

}  // namespace forza::native_list::reference
