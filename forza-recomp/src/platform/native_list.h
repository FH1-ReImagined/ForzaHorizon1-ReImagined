// forza - native renderer M4 (docs/native-renderer.md, "M4: the model lists"): the converter for
// the command lists a track model carries. A model holds, per pass group, one precompiled D3D
// command buffer: a 0xC8-byte header, a patch table, PM4, and a table of buffer fixups ("area
// C"). The PM4 is small and regular (register writes, padding and six type-3 opcodes), and every
// word the game changes after loading is named by the two tables. Convert() turns one list into
// a template: what the list does, in a form that needs no PM4 decode.
//
// A template is one block of words (host order) with two views of the same list:
// - the ops, in list order: register runs, tile blocks, shader loads, literal-constant loads and
//   draws. Replaying them writes what the list's packets write, in the same order (ReplayOps);
// - the draws: for draw k its arguments, its predicate bit, the shaders, vertex buffer and
//   textures bound when it runs (carried forward from an earlier draw when it sets none), and
//   the registers written since the previous draw, sorted and with only the last write of each.
//   Folding those gives the list's own state at draw k (StateAtDraw).
// tools/native_list_check.py proves the two equal, and equal to a plain PM4 interpreter, on every
// list of the game's files.
//
// Places are slots, never values. A word a fixup writes (a buffer or shader address, a texture's
// fetch constant, the viewport, the surfaces, a constant block) is not in the template: its slot
// says where the word is in the live list, and Resolve() reads it from there. So a template is a
// function of the list's static words alone, and its key (128 bits over those words) is the same
// before and after any fixup. The game's own lookup records (fixup type and slot) are not input:
// the kind of a place follows from the field it lands on.
//
// Convert() refuses, with a named reason, everything outside what the game's files hold: see
// Refuse. A list that scene_msaa has untiled (src/hooks/scene_tiling.cpp: the tile-bound packet
// headers turned into NOPs) is refused as input, convert it from its original words; the
// template then serves both forms (Run::untiled, LiveKey).
//
// Pure functions over copies of the list's parts, for tests/native_list_test.cpp.
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

#include "platform/scene_tiling.h"

namespace forza::native_list {

constexpr uint32_t kMagic = 0x4648314Cu;  // "FH1L"
constexpr uint32_t kVersion = 1;
constexpr uint32_t kNone = 0xFFFFFFFFu;

// The command buffer header (guest order), as RunCommandBuffer and the fixup functions read it.
constexpr uint32_t kHeaderBytes = 0xC8;
constexpr uint32_t kHeaderResource = 0x00;  // low 4 bits: 9 (10 is the other kind of buffer)
constexpr uint32_t kHeaderMasks = 0x18;     // five 64-bit masks inherited, five left untouched
constexpr uint32_t kMaskWords = 20;
constexpr uint32_t kHeaderFlags = 0x6C;
constexpr uint32_t kHeaderPatchWords = 0x7C;
constexpr uint32_t kHeaderSegments = 0xB8;  // next, count, then (words, address) per segment
constexpr uint32_t kFlagsKnown = 0x886;     // 0x800: recorded with the tile rectangles
constexpr uint32_t kFlagTiles = 0x800;
constexpr uint32_t kFlagFixedUp = 0x2000;  // set by the texture and surface fixups at run time
// A place addresses its dword with 20 bits.
constexpr uint32_t kMaxListWords = 1u << 20;

constexpr uint32_t kPm4DrawIndx = 0x22, kPm4ImLoad = 0x27, kPm4LoadAluConstant = 0x2F,
                   kPm4SetBinMaskLo = 0x60, kPm4SetBinMaskHi = 0x61;
// Registers a draw packet writes itself.
constexpr uint32_t kRegDmaBase = 0x21FA, kRegDmaSize = 0x21FB, kRegDrawInitiator = 0x21FC;
constexpr uint32_t kRegWindowOffset = 0x2080;  // with the window scissor: 0x2080-0x2082
constexpr uint32_t kRegFetch = 0x4800, kRegVertexFetch = 0x48BA;  // fetch constant 31
constexpr uint32_t kRegScratch6 = 0x57E, kRegScratch7 = 0x57F;

// A tile block, the same 27 words in every list recorded for the three tiles: bin mask HI 0,
// then per tile its bin mask (LO) and a predicated SET_CONSTANT of the window offset and scissor
// (0x2080-0x2082), then bin mask LO FFFFFFFF, HI 0.
constexpr uint32_t kTileBlockWords = 27, kTiles = 3;
constexpr uint32_t kTileMasks[kTiles] = {0x3, 0xC, 0x30};
constexpr uint32_t kTileWindowHeader = 0xC0032D01u, kTileWindowTarget = 0x00040080u;
constexpr uint64_t kBinMaskAfterTiles = 0xFFFFFFFFull;

enum class RegClass : uint8_t { kNone, kState, kVertexConstant, kPixelConstant, kFetch, kMark };

// The registers a list writes with type-0 packets: 34 state registers, VS c0-c11, PS c0-c7 and
// c116-c127, fetch constants 0-7 (textures) and 31 (the vertex buffer), and D3D's own marks
// (SCRATCH_REG6/7 and SHADER_CONSTANT_FLUSH_FETCH_0-2), which are not GPU state.
constexpr RegClass ClassOf(uint32_t r) {
  if ((r >= 0x2000 && r <= 0x2002) || (r >= 0x2080 && r <= 0x2082) ||
      (r >= 0x2102 && r <= 0x2104) || r == 0x210C || r == 0x210D || (r >= 0x210F && r <= 0x2114) ||
      (r >= 0x2180 && r <= 0x2182) || (r >= 0x2200 && r <= 0x2203) || r == 0x2205 ||
      (r >= 0x2208 && r <= 0x220B) || r == 0x2301 || (r >= 0x2380 && r <= 0x2383)) {
    return RegClass::kState;
  }
  if (r >= 0x4000 && r < 0x4030) return RegClass::kVertexConstant;
  if ((r >= 0x4400 && r < 0x4420) || (r >= 0x45D0 && r < 0x4600)) return RegClass::kPixelConstant;
  if ((r >= kRegFetch && r < kRegFetch + 8 * 6) ||
      (r >= kRegVertexFetch && r < kRegVertexFetch + 6)) {
    return RegClass::kFetch;
  }
  if (r == kRegScratch6 || r == kRegScratch7 || (r >= 0x5000 && r <= 0x5002)) {
    return RegClass::kMark;
  }
  return RegClass::kNone;
}
constexpr uint32_t CountState() {
  uint32_t n = 0;
  for (uint32_t r = 0x2000; r < 0x2400; ++r) n += ClassOf(r) == RegClass::kState;
  return n;
}
static_assert(CountState() == 34);
// A packet that writes one of the registers scene_msaa's untiling removes (scene_tiling.h,
// LayoutRegister) can write nothing else, so untiling a list removes every write of them.
constexpr bool LayoutRunsWhole() {
  for (uint32_t r = 0x2000; r < 0x2400; ++r) {
    if (ClassOf(r) == RegClass::kState && ClassOf(r + 1) == RegClass::kState &&
        scene_tiling::LayoutRegister(r) != scene_tiling::LayoutRegister(r + 1)) {
      return false;
    }
  }
  return true;
}
static_assert(LayoutRunsWhole());

// A shader's literal constants, loaded from memory by LOAD_ALU_CONSTANT: c200-c203 or c252-c255
// of the vertex (0x4000) or pixel (0x4400) constants, 16 words.
constexpr uint32_t kLiteralWords = 16;
constexpr bool LiteralBlock(uint32_t index, uint32_t words) {
  return words == kLiteralWords &&
         (index == 0x320 || index == 0x3F0 || index == 0x720 || index == 0x7F0);
}

// Why a list is not converted (it then runs as PM4, as today).
enum class Refuse : uint32_t {
  kNone,
  kResourceType,   // the header's resource word is not a command buffer of type 9
  kFlags,          // flags outside 0x886 | 0x2000
  kSegments,       // not one segment of the list's length
  kPatchTable,     // the patch table's length is not the header's
  kListSize,       // empty, or longer than a place can address
  kPlaceEntry,     // a table entry of unknown length, or a chain cut off
  kPlaceSubCode,   // a place with sub-code 62 or 63 (the table at cb+112)
  kPlaceField,     // a place that lands on no known field
  kPlaceConflict,  // two different places on one dword
  kPacketType1,
  kOpcode,         // a type-3 opcode outside the six, or header bits no list has
  kUntiled,        // a NOP packet: the list is in its untiled form
  kPacketCut,      // a packet runs past the list's end
  kRegister,       // a register outside ClassOf
  kOneRegister,    // a one-register packet that is not a mark
  kFetchRange,     // fetch constants not written whole
  kPredicate,      // the predicate bit on a packet other than a draw or a tile's window
  kTileBlock,      // a bin mask or SET_CONSTANT that is not part of a tile block, or a list
                   // recorded for the tiles (kFlagTiles) that does not start with one
  kWindowMix,      // tile blocks and a type-0 write of the window registers in one list
  kDraw,           // a draw that is not DRAW_INDX from an index buffer, 4 words
  kDrawPlace,      // a draw whose index address is not a place
  kShader,         // a shader load of another form
  kShaderPlace,    // a shader load whose address is not a place
  kLiterals,       // a constant load from memory of another form
  kLiteralsPlace,  // a constant load whose address is not a place
  kCount,
};
constexpr const char* RefuseName(Refuse r) {
  constexpr const char* names[] = {
      "none",           "resource-type", "flags",         "segments",     "patch-table",
      "list-size",      "place-entry",   "place-subcode", "place-field",  "place-conflict",
      "packet-type-1",  "opcode",        "untiled",       "packet-cut",   "register",
      "one-register",   "fetch-range",   "predicate",     "tile-block",   "window-mix",
      "draw",           "draw-place",    "shader",        "shader-place", "literals",
      "literals-place",
  };
  static_assert(sizeof(names) / sizeof(names[0]) == size_t(Refuse::kCount));
  return r < Refuse::kCount ? names[size_t(r)] : "?";
}
struct Result {
  Refuse refuse = Refuse::kNone;
  uint32_t at = 0;  // the list dword, or the table word, the reason is about
  explicit operator bool() const { return refuse == Refuse::kNone; }
};

// One list's parts, in guest byte order as they are in the file and in guest memory.
struct Input {
  const uint8_t* header = nullptr;       // kHeaderBytes
  const uint8_t* patch_table = nullptr;  // the header's +0x7C words
  uint32_t patch_words = 0;
  const uint8_t* area_c = nullptr;
  uint32_t area_c_words = 0;
  const uint8_t* pm4 = nullptr;  // the segment's words (not the two-word trailer)
  uint32_t pm4_words = 0;
};
inline uint32_t Word(const uint8_t* guest, size_t index) {
  uint32_t v;
  std::memcpy(&v, guest + index * 4, 4);
  return __builtin_bswap32(v);
}

// ---- The template -----------------------------------------------------------------------------

// Header words. A section is its offset in the template, then its count.
enum : uint32_t {
  kTMagic,
  kTVersion,
  // The whole template's words.
  kTWords,
  // 4 words.
  kTKey,
  // The list's PM4 words.
  kTListWords = kTKey + 4,
  // The header's flags without kFlagFixedUp.
  kTCbFlags,
  // kTemplateTiled.
  kTFlags,
  // kOpWords each.
  kTOps,
  kTOpCount,
  // kDrawWords each; one more record than the count, for the list's tail.
  kTDraws,
  kTDrawCount,
  // kDeltaWords each.
  kTDeltas,
  kTDeltaCount,
  // kSlotWords each, by list dword.
  kTSlots,
  kTSlotCount,
  // The ops' static words; a slot's words are 0 here.
  kTPool,
  kTPoolWords,
  // 2 words each, by list dword: where a tile-bound packet's header is, and the header.
  kTTileBound,
  kTTileBoundCount,
  // kMaskWords: the header's ten masks.
  kTMasks,
  kTHeaderWords,
};
// The list is what scene_msaa unties: recorded for the tiles and starting with a tile block (its
// word 4 the first tile's window write). Only such a template has tile-bound packets, and
// Run::untiled applies to it alone.
constexpr uint32_t kTemplateTiled = 1;

// An op: kind in the low byte of word 0, flags above it, then three words.
constexpr uint32_t kOpWords = 4;
enum : uint32_t {
  // A type-0 packet: the first register, the count, the pool offset of its words.
  kOpRegs = 1,
  // A tile block: the pool offset of its 12 words (per tile the bin mask, the window offset, the
  // scissor's two corners), the list dword it starts at.
  kOpTile = 2,
  // IM_LOAD: the pool offset of the address word, the size word.
  kOpShader = 3,
  // LOAD_ALU_CONSTANT: the first register, the count, the pool offset of the address word.
  kOpLiterals = 4,
  // DRAW_INDX: the draw's record.
  kOpDraw = 5,
};
constexpr uint32_t kOpOneRegister = 0x100;  // kOpRegs: every word to the one register (a mark)
constexpr uint32_t kOpTileBound = 0x200;    // kOpRegs: a packet scene_msaa turns into a NOP
constexpr uint32_t kOpPixel = 0x400;        // kOpShader: the pixel shader (else the vertex shader)

// A draw's record. The record after the last draw describes the list's tail: the writes after
// that draw.
enum : uint32_t {
  kDFlags,
  // VGT_DRAW_INITIATOR: the primitive type, the index size and count.
  kDInitiator,
  // The pool offset of the index address (a slot).
  kDIndexPool,
  // VGT_DMA_SIZE.
  kDSizeWord,
  // The list dword of the draw packet's header.
  kDPacket,
  // The ops since the previous draw, this draw's own the last of them.
  kDFirstOp,
  kDOpCount,
  // The registers those ops write, reduced: the first run and the count.
  kDDelta,
  kDDeltaCount,
  // The bound shaders: the pool offset of the address word (kNone: the list loaded none so far)
  // and the size word.
  kDVertexShader,
  kDVertexShaderSize,
  kDPixelShader,
  kDPixelShaderSize,
  // The pool offset of fetch constant 31's six words, or kNone.
  kDVertexFetch,
  // 8 words: the pool offset of fetch constant 0-7's six words, or kNone.
  kDTexture,
  // The last tile block before the draw (an op), or kNone.
  kDTile = kDTexture + 8,
  kDrawWords,
};
constexpr uint32_t kDrawPredicated = 1;  // the packet's predicate bit: runs if bin select & mask
constexpr uint32_t kDrawTail = 2;

// A run of registers in a draw's delta: first register, count, the op that wrote them last and
// the first word's offset in that op's words (kOpRegs) or in its block of memory (kOpLiterals).
constexpr uint32_t kDeltaWords = 4;

// A slot: type | argument << 8, list dword, pool offset, words. The types are the game's fixup
// types.
constexpr uint32_t kSlotWords = 4;
enum : uint32_t {
  kSlotVertexBuffer = 1,  // 2 words of fetch constant 31; argument: which pair (0-2)
  kSlotIndexBuffer = 2,   // a draw's index address
  kSlotTexture = 3,       // 6 words; argument: the fetch constant
  kSlotViewport = 4,      // argument: 0-5, PA_CL_VPORT_XSCALE..ZOFFSET
  kSlotSurface = 5,       // argument: the sub-code (kSurface*)
  kSlotScissor = 6,       // argument: 0 window scissor TL, 1 BR
  kSlotConstants = 8,     // a block of pixel constants; argument: the first register - 0x4000
  kSlotVertexShader = 9,  // argument: 0 the microcode's address, 1 a literal block's
  kSlotPixelShader = 10,
};
constexpr uint32_t kSurfaceInfo = 0, kSurfaceDepthInfo = 1, kSurfaceColorInfo = 2,
                   kSurfaceAaConfig = 6, kSurfaceHiZ = 7, kSurfaceMarkSize = 8,
                   kSurfaceModifier = 9;  // no write: it changes how the chain's formats are read

struct Key {
  uint32_t w[4] = {};
  bool operator==(const Key& o) const { return std::memcmp(w, o.w, sizeof(w)) == 0; }
  bool operator!=(const Key& o) const { return !(*this == o); }
};

// 128 bits over a stream of words: the block mixing of MurmurHash3's x64 variant (Austin
// Appleby, public domain), four words a block, the word count in the final mix.
class Hasher {
 public:
  void Add(uint32_t v) {
    buffer_[fill_++] = v;
    ++count_;
    if (fill_ == 4) Block();
  }
  Key Finish() {
    if (fill_) {
      while (fill_ < 4) buffer_[fill_++] = 0;
      Block();
    }
    h1_ ^= count_;
    h2_ ^= count_;
    h1_ += h2_;
    h2_ += h1_;
    h1_ = Mix(h1_);
    h2_ = Mix(h2_);
    h1_ += h2_;
    h2_ += h1_;
    Key k;
    k.w[0] = uint32_t(h1_);
    k.w[1] = uint32_t(h1_ >> 32);
    k.w[2] = uint32_t(h2_);
    k.w[3] = uint32_t(h2_ >> 32);
    return k;
  }

 private:
  static uint64_t Rotl(uint64_t x, int r) { return (x << r) | (x >> (64 - r)); }
  static uint64_t Mix(uint64_t k) {
    k ^= k >> 33;
    k *= 0xFF51AFD7ED558CCDull;
    k ^= k >> 33;
    k *= 0xC4CEB9FE1A85EC53ull;
    k ^= k >> 33;
    return k;
  }
  void Block() {
    constexpr uint64_t c1 = 0x87C37B91114253D5ull, c2 = 0x4CF5AD432745937Full;
    uint64_t k1 = buffer_[0] | uint64_t(buffer_[1]) << 32;
    uint64_t k2 = buffer_[2] | uint64_t(buffer_[3]) << 32;
    k1 *= c1;
    k1 = Rotl(k1, 31);
    k1 *= c2;
    h1_ ^= k1;
    h1_ = Rotl(h1_, 27);
    h1_ += h2_;
    h1_ = h1_ * 5 + 0x52DCE729;
    k2 *= c2;
    k2 = Rotl(k2, 33);
    k2 *= c1;
    h2_ ^= k2;
    h2_ = Rotl(h2_, 31);
    h2_ += h1_;
    h2_ = h2_ * 5 + 0x38495AB5;
    fill_ = 0;
  }
  uint64_t h1_ = 0, h2_ = 0, count_ = 0;
  uint32_t buffer_[4] = {};
  uint32_t fill_ = 0;
};

inline Key TemplateKey(const uint32_t* t) {
  Key k;
  std::memcpy(k.w, t + kTKey, sizeof(k.w));
  return k;
}
inline uint32_t DrawCount(const uint32_t* t) { return t[kTDrawCount]; }
inline const uint32_t* DrawRecord(const uint32_t* t, uint32_t k) {
  return t + t[kTDraws] + k * kDrawWords;
}
inline const uint32_t* Op(const uint32_t* t, uint32_t op) { return t + t[kTOps] + op * kOpWords; }

// The key of a list as it is now, by template t's slots and tile-bound packets: the header's
// flags without kFlagFixedUp and its ten masks, the patch table, area C, and the PM4 with every
// slot's words as zero and every tile-bound header as recorded, whether the live word is that
// header or its NOP. One thing of a slot is taken as static and stays in: the low two bits of a
// microcode address, the shader's type. The game's two shader fixups (TU4 sub_82AA8750 and
// sub_82AA87D8) write the entry's value word plus the shader's address, with bit 0 set in the
// address for a pixel shader's microcode: the bits are the file's as long as both are multiples
// of 4. A live word with other bits has another key, and its list then runs as PM4.
inline Key LiveKey(const uint32_t* t, const Input& in) {
  Hasher h;
  h.Add(kVersion);
  h.Add(Word(in.header, kHeaderFlags / 4) & ~kFlagFixedUp);
  for (uint32_t i = 0; i < kMaskWords; ++i) h.Add(Word(in.header, kHeaderMasks / 4 + i));
  h.Add(in.patch_words);
  for (uint32_t i = 0; i < in.patch_words; ++i) h.Add(Word(in.patch_table, i));
  h.Add(in.area_c_words);
  for (uint32_t i = 0; i < in.area_c_words; ++i) h.Add(Word(in.area_c, i));
  h.Add(in.pm4_words);
  const uint32_t* slot = t + t[kTSlots];
  const uint32_t* slots_end = slot + t[kTSlotCount] * kSlotWords;
  const uint32_t* bound = t + t[kTTileBound];
  const uint32_t* bound_end = bound + t[kTTileBoundCount] * 2;
  for (uint32_t d = 0; d < in.pm4_words;) {
    if (slot != slots_end && slot[1] == d) {
      const uint32_t type = slot[0] & 0xFF, argument = slot[0] >> 8;
      const bool microcode =
          (type == kSlotVertexShader || type == kSlotPixelShader) && argument == 0;
      for (uint32_t i = 0; i < slot[3] && d < in.pm4_words; ++i, ++d) {
        h.Add(microcode ? Word(in.pm4, d) & 3 : 0);
      }
      slot += kSlotWords;
      continue;
    }
    uint32_t w = Word(in.pm4, d);
    if (bound != bound_end && bound[0] == d) {
      if (w == scene_tiling::NopPacket(bound[1])) w = bound[1];
      bound += 2;
    }
    h.Add(w);
    ++d;
  }
  return h.Finish();
}

// Whether the template's list is untiled now: word 4 of a list recorded for the tiles, the
// first tile's window write, is a NOP exactly while its tile-bound packets are.
inline bool Untiled(const uint32_t* t, const uint8_t* pm4) {
  if (!(t[kTFlags] & kTemplateTiled)) return false;
  const uint32_t w = Word(pm4, 4);
  return w >> 30 == 3 && ((w >> 8) & 0x7F) == scene_tiling::kPm4Nop;
}

// The template's pool with every slot read from the live list (host order): `pm4` is the
// template's list, t[kTListWords] words, and `pool` has t[kTPoolWords] words. To be done again
// when a fixup has touched the list.
inline void Resolve(const uint32_t* t, const uint8_t* pm4, uint32_t* pool) {
  if (t[kTPoolWords]) std::memcpy(pool, t + t[kTPool], t[kTPoolWords] * sizeof(uint32_t));
  const uint32_t* slot = t + t[kTSlots];
  for (uint32_t s = 0; s < t[kTSlotCount]; ++s, slot += kSlotWords) {
    for (uint32_t i = 0; i < slot[3]; ++i) pool[slot[2] + i] = Word(pm4, slot[1] + i);
  }
}

// How a list runs: the command processor's bin select and bin mask when it starts, and whether
// the list is untiled (the tile blocks' window writes and the tile-bound register runs are NOPs).
struct Run {
  uint64_t bin_select = 0xFFFFFFFFull;
  uint64_t bin_mask = 0xFFFFFFFFull;  // kept current by ReplayOps
  bool untiled = false;
};
struct DrawCall {
  uint32_t draw = 0;
  bool predicated = false;
  bool executed = false;  // not predicated, or bin select & bin mask
  uint32_t initiator = 0, index_address = 0, size_word = 0;
};

// A tile block's writes for this bin select: each tile's window under that tile's bin mask.
template <typename Sink>
void ApplyTile(const uint32_t* pool_words, const Run& run, Sink& sink) {
  if (run.untiled) return;
  for (uint32_t tile = 0; tile < kTiles; ++tile) {
    if (run.bin_select & pool_words[tile * 4]) {
      sink.Registers(kRegWindowOffset, 3, pool_words + tile * 4 + 1);
    }
  }
}

// View 1: ops [first, first + count) in list order, over a resolved pool. The sink gets what the
// packets do:
//   Registers(first, count, values)            host-order values to consecutive registers
//   Literals(first, count, address, offset)    registers loaded from guest memory, from word
//                                              `offset` of the block at `address`
//   Shader(pixel, address, size_word)          IM_LOAD
//   BinMask(mask)                              the bin mask a tile block leaves
//   Draw(call)                                 a draw packet; an executed one also writes the
//                                              three draw registers, which is the sink's work
template <typename Sink>
void ReplayOps(const uint32_t* t, const uint32_t* pool, uint32_t first, uint32_t count, Run& run,
               Sink& sink) {
  const uint32_t* op = Op(t, first);
  for (uint32_t i = 0; i < count; ++i, op += kOpWords) {
    switch (op[0] & 0xFF) {
      case kOpRegs:
        if (run.untiled && (op[0] & kOpTileBound)) break;
        if (op[0] & kOpOneRegister) {
          sink.Registers(op[1], 1, pool + op[3] + op[2] - 1);
        } else {
          sink.Registers(op[1], op[2], pool + op[3]);
        }
        break;
      case kOpTile:
        ApplyTile(pool + op[1], run, sink);
        run.bin_mask = kBinMaskAfterTiles;
        sink.BinMask(run.bin_mask);
        break;
      case kOpShader:
        sink.Shader((op[0] & kOpPixel) != 0, pool[op[1]], op[2]);
        break;
      case kOpLiterals:
        sink.Literals(op[1], op[2], pool[op[3]], 0);
        break;
      case kOpDraw: {
        const uint32_t* r = DrawRecord(t, op[1]);
        DrawCall call;
        call.draw = op[1];
        call.predicated = (r[kDFlags] & kDrawPredicated) != 0;
        call.executed = !call.predicated || (run.bin_select & run.bin_mask) != 0;
        call.initiator = r[kDInitiator];
        call.index_address = pool[r[kDIndexPool]];
        call.size_word = r[kDSizeWord];
        sink.Draw(call);
        break;
      }
      default:
        break;
    }
  }
}

// View 2: draw k as a native renderer takes it, with what is bound when it runs.
struct DrawView {
  bool predicated = false;
  uint32_t initiator = 0, index_address = 0, size_word = 0;
  uint32_t packet = 0;  // list dword of the packet
  // [0] the vertex shader, [1] the pixel shader: the live address word and the size word.
  bool shader[2] = {};
  uint32_t shader_address[2] = {}, shader_size[2] = {};
  const uint32_t* vertex_fetch = nullptr;  // fetch constant 31's six words, or null
  const uint32_t* texture[8] = {};         // fetch constants 0-7, null where the list set none
  bool tiles = false;                      // a tile block came before it
};
inline DrawView Draw(const uint32_t* t, const uint32_t* pool, uint32_t k) {
  const uint32_t* r = DrawRecord(t, k);
  DrawView v;
  v.predicated = (r[kDFlags] & kDrawPredicated) != 0;
  if (!(r[kDFlags] & kDrawTail)) {
    v.initiator = r[kDInitiator];
    v.index_address = pool[r[kDIndexPool]];
    v.size_word = r[kDSizeWord];
    v.packet = r[kDPacket];
  }
  for (uint32_t stage = 0; stage < 2; ++stage) {
    const uint32_t at = r[stage ? kDPixelShader : kDVertexShader];
    if (at == kNone) continue;
    v.shader[stage] = true;
    v.shader_address[stage] = pool[at];
    v.shader_size[stage] = r[stage ? kDPixelShaderSize : kDVertexShaderSize];
  }
  if (r[kDVertexFetch] != kNone) v.vertex_fetch = pool + r[kDVertexFetch];
  for (uint32_t i = 0; i < 8; ++i) {
    if (r[kDTexture + i] != kNone) v.texture[i] = pool + r[kDTexture + i];
  }
  v.tiles = r[kDTile] != kNone;
  return v;
}

// View 2: everything the list itself has set when draw k starts (k == DrawCount: when the list
// ends), from the draw records alone: the deltas of draws 0..k, the three registers of the last
// draw that ran, the tile block in effect, the bound shaders. The sink is ReplayOps's, without
// Draw.
template <typename Sink>
void StateAtDraw(const uint32_t* t, const uint32_t* pool, uint32_t k, const Run& run, Sink& sink) {
  const uint32_t* deltas = t + t[kTDeltas];
  uint32_t tile = kNone;
  for (uint32_t j = 0; j <= k; ++j) {
    const uint32_t* r = DrawRecord(t, j);
    const uint32_t* e = deltas + r[kDDelta] * kDeltaWords;
    for (uint32_t i = 0; i < r[kDDeltaCount]; ++i, e += kDeltaWords) {
      const uint32_t* op = Op(t, e[2]);
      if ((op[0] & 0xFF) == kOpLiterals) {
        sink.Literals(e[0], e[1], pool[op[3]], e[3]);
      } else if (!(run.untiled && (op[0] & kOpTileBound))) {
        sink.Registers(e[0], e[1], pool + op[3] + e[3]);
      }
    }
    tile = r[kDTile];
    if (j == k) break;
    const uint64_t mask = tile != kNone ? kBinMaskAfterTiles : run.bin_mask;
    if (!(r[kDFlags] & kDrawPredicated) || (run.bin_select & mask) != 0) {
      const uint32_t own[3] = {pool[r[kDIndexPool]], r[kDSizeWord], r[kDInitiator]};
      sink.Registers(kRegDmaBase, 3, own);
    }
  }
  if (tile != kNone) {
    ApplyTile(pool + Op(t, tile)[1], run, sink);
    sink.BinMask(kBinMaskAfterTiles);
  }
  const uint32_t* r = DrawRecord(t, k);
  for (uint32_t pixel = 0; pixel < 2; ++pixel) {
    const uint32_t at = r[pixel ? kDPixelShader : kDVertexShader];
    if (at == kNone) continue;
    sink.Shader(pixel != 0, pool[at], r[pixel ? kDPixelShaderSize : kDVertexShaderSize]);
  }
}

// ---- The converter ----------------------------------------------------------------------------

// Converts lists one after another; it only holds working memory between calls.
class Converter {
 public:
  // Writes the list's template to `out`, or refuses and leaves it empty.
  Result Convert(const Input& in, std::vector<uint32_t>* out) {
    out->clear();
    const Result result = Build(in);
    if (!result) return result;
    Emit(in, out);
    return result;
  }

 private:
  // What a list dword is, for a place that lands on it.
  enum : uint8_t { kOther, kRegsWord, kDrawWord, kShaderWord, kLiteralsWord };
  struct Place {
    uint32_t type, argument, dword, words, pool;
  };

  static Result No(Refuse r, uint32_t at) {
    Result result;
    result.refuse = r;
    result.at = at;
    return result;
  }
  uint32_t AddOp(uint32_t word0, uint32_t a, uint32_t b, uint32_t c, uint32_t dword) {
    const uint32_t op = uint32_t(ops_.size() / kOpWords);
    ops_.insert(ops_.end(), {word0, a, b, c});
    op_dword_.push_back(dword);
    return op;
  }
  Result Build(const Input& in) {
    if ((Word(in.header, kHeaderResource / 4) & 0xF) != 9) return No(Refuse::kResourceType, 0);
    cb_flags_ = Word(in.header, kHeaderFlags / 4) & ~kFlagFixedUp;
    if (cb_flags_ & ~kFlagsKnown) return No(Refuse::kFlags, 0);
    if (in.pm4_words == 0 || in.pm4_words > kMaxListWords) return No(Refuse::kListSize, 0);
    if (Word(in.header, kHeaderSegments / 4) != 0 ||
        Word(in.header, kHeaderSegments / 4 + 1) != 1 ||
        (Word(in.header, kHeaderSegments / 4 + 2) & 0xFFFFFF) != in.pm4_words) {
      return No(Refuse::kSegments, 0);
    }
    if (Word(in.header, kHeaderPatchWords / 4) != in.patch_words) return No(Refuse::kPatchTable, 0);

    ops_.clear();
    op_dword_.clear();
    draws_.clear();
    deltas_.clear();
    pool_.clear();
    places_.clear();
    bound_.clear();
    kind_.assign(in.pm4_words, kOther);
    info_.assign(in.pm4_words, 0);
    place_of_.assign(in.pm4_words, kNone);
    if (Result r = Walk(in); !r) return r;
    if (Result r = Table(in, in.patch_table, in.patch_words); !r) return r;
    if (Result r = Table(in, in.area_c, in.area_c_words); !r) return r;
    if (Result r = Check(in); !r) return r;
    Records();
    return {};
  }

  // The PM4, once: the ops, their static words, and what each dword is.
  Result Walk(const Input& in) {
    const uint32_t n = in.pm4_words;
    tile_blocks_ = 0;
    window_write_ = kNone;
    starts_with_tiles_ = false;
    for (uint32_t d = 0; d < n;) {
      const uint32_t header = Word(in.pm4, d);
      const uint32_t type = header >> 30;
      if (type == 2) {
        ++d;
        continue;
      }
      if (type == 1) return No(Refuse::kPacketType1, d);
      const uint32_t count = ((header >> 16) & 0x3FFF) + 1;
      if (count > n - d - 1) return No(Refuse::kPacketCut, d);
      if (type == 0) {
        const uint32_t first = header & 0x7FFF;
        const bool one = (header & 0x8000) != 0;
        if (one) {
          if (first != kRegScratch6 && first != kRegScratch7) return No(Refuse::kOneRegister, d);
        } else {
          for (uint32_t r = first; r < first + count; ++r) {
            if (ClassOf(r) == RegClass::kNone) return No(Refuse::kRegister, d);
          }
          if (ClassOf(first) == RegClass::kFetch) {
            const uint32_t base = first >= kRegVertexFetch ? kRegVertexFetch : kRegFetch;
            if ((first - base) % 6 || count % 6) return No(Refuse::kFetchRange, d);
          }
          if (first <= kRegWindowOffset + 2 && first + count > kRegWindowOffset &&
              window_write_ == kNone) {
            window_write_ = d;
          }
        }
        const uint32_t op = AddOp(kOpRegs | (one ? kOpOneRegister : 0), first, count,
                                  uint32_t(pool_.size()), d + 1);
        for (uint32_t i = 0; i < count; ++i) {
          pool_.push_back(Word(in.pm4, d + 1 + i));
          kind_[d + 1 + i] = kRegsWord;
          info_[d + 1 + i] = op;
        }
        d += 1 + count;
        continue;
      }
      const uint32_t opcode = (header >> 8) & 0x7F;
      if (header & 0x80FE) return No(Refuse::kOpcode, d);
      const bool predicated = header & 1;
      switch (opcode) {
        case kPm4DrawIndx: {
          // The viz query word, the initiator (source select 0: indices from memory), the index
          // address and the size word.
          if (count != 4 || Word(in.pm4, d + 1) != 0 || ((Word(in.pm4, d + 2) >> 6) & 3) != 0) {
            return No(Refuse::kDraw, d);
          }
          const uint32_t draw = uint32_t(draws_.size() / kDrawWords);
          draws_.resize(draws_.size() + kDrawWords, 0);
          uint32_t* r = &draws_[draw * kDrawWords];
          r[kDFlags] = predicated ? kDrawPredicated : 0;
          r[kDInitiator] = Word(in.pm4, d + 2);
          r[kDIndexPool] = uint32_t(pool_.size());
          r[kDSizeWord] = Word(in.pm4, d + 4);
          r[kDPacket] = d;
          pool_.push_back(Word(in.pm4, d + 3));
          AddOp(kOpDraw, draw, 0, 0, d + 1);
          for (uint32_t i = 0; i < count; ++i) {
            kind_[d + 1 + i] = kDrawWord;
            info_[d + 1 + i] = draw;
          }
          break;
        }
        case kPm4ImLoad: {
          if (predicated) return No(Refuse::kPredicate, d);
          // Two words, and only then are both the list's: the packet may be its last.
          if (count != 2) return No(Refuse::kShader, d);
          const uint32_t address = Word(in.pm4, d + 1), size = Word(in.pm4, d + 2);
          // Vertex (0) or pixel (1) in the address word's low bits; the size in words, from 0.
          if ((address & 3) > 1 || size == 0 || size >> 16) return No(Refuse::kShader, d);
          const uint32_t op = AddOp(kOpShader | ((address & 3) ? kOpPixel : 0),
                                    uint32_t(pool_.size()), size, 0, d + 1);
          pool_.push_back(address);
          kind_[d + 1] = kShaderWord;
          info_[d + 1] = op;
          break;
        }
        case kPm4LoadAluConstant: {
          if (predicated) return No(Refuse::kPredicate, d);
          if (count != 3) return No(Refuse::kLiterals, d);
          // The address, type 0 (ALU constants) with the first constant word, the word count.
          const uint32_t target = Word(in.pm4, d + 2), words = Word(in.pm4, d + 3);
          if (target >> 11 || !LiteralBlock(target, words)) return No(Refuse::kLiterals, d);
          const uint32_t op =
              AddOp(kOpLiterals, 0x4000 + target, words, uint32_t(pool_.size()), d + 1);
          pool_.push_back(Word(in.pm4, d + 1));
          kind_[d + 1] = kLiteralsWord;
          info_[d + 1] = op;
          break;
        }
        case kPm4SetBinMaskHi: {
          if (predicated || d + kTileBlockWords > n) return No(Refuse::kTileBlock, d);
          uint32_t expect[kTileBlockWords] = {};
          uint32_t e = 0;
          expect[e++] = 0xC0006100u;
          expect[e++] = 0;
          for (uint32_t tile = 0; tile < kTiles; ++tile) {
            expect[e++] = 0xC0006000u;
            expect[e++] = kTileMasks[tile];
            expect[e++] = kTileWindowHeader;
            expect[e++] = kTileWindowTarget;
            e += 3;  // the window offset and the scissor
          }
          expect[e++] = 0xC0006000u;
          expect[e++] = 0xFFFFFFFFu;
          expect[e++] = 0xC0006100u;
          expect[e++] = 0;
          const uint32_t pool = uint32_t(pool_.size());
          for (uint32_t i = 0; i < kTileBlockWords; ++i) {
            const uint32_t w = Word(in.pm4, d + i);
            const bool value = i >= 6 && i < 23 && (i - 6) % 7 < 3;
            if (!value && w != expect[i]) {
              const bool nop =
                  expect[i] == kTileWindowHeader && w == scene_tiling::NopPacket(expect[i]);
              return No(nop ? Refuse::kUntiled : Refuse::kTileBlock, d + i);
            }
            if (i >= 2 && i < 23 && (i - 2) % 7 == 1) pool_.push_back(w);  // the tile's bin mask
            if (value) pool_.push_back(w);
          }
          AddOp(kOpTile, pool, d, 0, d);
          if (d == 0) starts_with_tiles_ = true;
          for (uint32_t tile = 0; tile < kTiles; ++tile) {
            bound_.push_back(uint64_t(d + 4 + tile * 7) << 32 | kTileWindowHeader);
          }
          ++tile_blocks_;
          d += kTileBlockWords;
          continue;
        }
        case kPm4SetBinMaskLo:
        case scene_tiling::kPm4SetConstant:
          return No(Refuse::kTileBlock, d);
        case scene_tiling::kPm4Nop:
          return No(Refuse::kUntiled, d);
        default:
          return No(Refuse::kOpcode, d);
      }
      d += 1 + count;
    }
    // A list recorded for the tiles is one scene_msaa unties (src/hooks/scene_tiling.cpp,
    // UntileList), and it starts with a tile block: its word 4 is the first tile's window write.
    // scene_msaa goes by the bits of that word and Untiled() by its NOP, whatever the word is.
    // So a list with the flag that starts otherwise is refused: its word 4 could be a value of
    // another packet that reads as the header (scene_msaa would untie the list's other packets
    // and leave that word, and Untiled() could not tell the two forms apart), or a place, whose
    // value a template must not depend on.
    tiled_ = (cb_flags_ & kFlagTiles) != 0;
    if (tiled_ && !starts_with_tiles_) return No(Refuse::kTileBlock, 0);
    return {};
  }

  // One of the two tables: every entry a place, its kind by the field it lands on.
  Result Table(const Input& in, const uint8_t* table, uint32_t words) {
    uint32_t chain = 0;  // the fixup type of the chain so far
    bool modifier = false;
    for (uint32_t i = 0; i < words;) {
      const uint32_t entry = Word(table, i);
      const uint32_t length = (entry >> 26) & 0xF;
      const bool more = (entry >> 30) & 1;
      if ((length != 1 && length != 2) || i + length > words || (more && i + length >= words)) {
        return No(Refuse::kPlaceEntry, i);
      }
      const uint32_t sub = entry & 0x3F, dword = (entry >> 6) & 0xFFFFF;
      const uint32_t value = length == 2 ? Word(table, i + 1) : 0;
      if (sub >= 62) return No(Refuse::kPlaceSubCode, i);
      if (dword >= in.pm4_words) return No(Refuse::kPlaceField, i);
      if (length == 1 && sub == kSurfaceModifier) {
        modifier = true;
      } else {
        Place p{};
        if (!Land(dword, sub, length, value, &p)) return No(Refuse::kPlaceField, i);
        // A chain is one fixup, so one type (a shader's: its microcode and its literal blocks).
        if (chain && chain != p.type) return No(Refuse::kPlaceField, i);
        chain = p.type;
        if (Result r = AddPlace(in, p, i); !r) return r;
      }
      if (!more) {
        if (modifier && chain != kSlotSurface) return No(Refuse::kPlaceField, i);
        chain = 0;
        modifier = false;
      }
      i += length;
    }
    return {};
  }

  bool Land(uint32_t dword, uint32_t sub, uint32_t length, uint32_t value, Place* p) {
    const uint8_t kind = kind_[dword];
    if (kind == kOther) return false;
    if (kind == kDrawWord) {
      // The entry names the packet's first word; the index address is two on.
      const uint32_t* r = &draws_[size_t(info_[dword]) * kDrawWords];
      if (length != 2 || sub != 0 || dword != r[kDPacket] + 1) return false;
      p->type = kSlotIndexBuffer;
      p->argument = 0;
      p->dword = dword + 2;
      p->words = 1;
      p->pool = r[kDIndexPool];
      return true;
    }
    const uint32_t* op = &ops_[size_t(info_[dword]) * kOpWords];
    switch (kind) {
      case kRegsWord: {
        const uint32_t index = dword - op_dword_[info_[dword]];
        const uint32_t count = op[2];
        p->pool = op[3] + index;
        p->dword = dword;
        p->words = 1;
        p->argument = sub;
        if (op[0] & kOpOneRegister) {
          // The surface's size, the last word of a three-word SCRATCH_REG6 mark; the entry's
          // value says which surface.
          if (op[1] != kRegScratch6 || length != 2 || sub != kSurfaceMarkSize ||
              index + 2 != count) {
            return false;
          }
          p->type = kSlotSurface;
          p->dword = dword + 1;
          p->pool += 1;
          return true;
        }
        const uint32_t reg = op[1] + index;
        switch (ClassOf(reg)) {
          case RegClass::kFetch:
            if (length == 1) {
              // A texture: a whole fetch constant, 0-7.
              if (reg >= kRegVertexFetch || (reg - kRegFetch) % 6 || sub != 0 ||
                  index + 6 > count) {
                return false;
              }
              p->type = kSlotTexture;
              p->argument = (reg - kRegFetch) / 6;
              p->words = 6;
              return true;
            }
            // The vertex buffer: an address and size pair of fetch constant 31, `sub` words on.
            if (reg + sub < kRegVertexFetch || (reg + sub - kRegVertexFetch) % 2 ||
                reg + sub + 2 > kRegVertexFetch + 6 || index + sub + 2 > count) {
              return false;
            }
            p->type = kSlotVertexBuffer;
            p->argument = (reg + sub - kRegVertexFetch) / 2;
            p->dword = dword + sub;
            p->pool += sub;
            p->words = 2;
            return true;
          case RegClass::kState:
            if (length != 1) return false;
            if (reg >= 0x210F && reg <= 0x2114 && sub == reg - 0x210F) {
              p->type = kSlotViewport;
            } else if ((reg == 0x2000 && sub == kSurfaceInfo) ||
                       (reg == 0x2001 && sub == kSurfaceColorInfo) ||
                       (reg == 0x2002 && sub == kSurfaceDepthInfo) ||
                       (reg == 0x2301 && sub == kSurfaceAaConfig) ||
                       (reg == 0x2203 && sub == kSurfaceHiZ)) {
              p->type = kSlotSurface;
            } else if ((reg == 0x2081 && sub == 0) || (reg == 0x2082 && sub == 1)) {
              p->type = kSlotScissor;
            } else {
              return false;
            }
            return true;
          case RegClass::kPixelConstant: {
            // A constant block: value & 0x70 bytes, (value & 3) constants on, all in c116-c127.
            const uint32_t skip = (value & 3) * 4, block = (value & 0x70) / 4;
            if (length != 2 || sub != 0 || block == 0 || reg + skip < 0x45D0 ||
                index + skip + block > count) {
              return false;
            }
            p->type = kSlotConstants;
            p->argument = reg + skip - 0x4000;
            p->dword = dword + skip;
            p->pool += skip;
            p->words = block;
            return true;
          }
          default:
            return false;
        }
      }
      case kShaderWord:
        if (length != 2 || sub != 0) return false;
        p->type = (op[0] & kOpPixel) ? kSlotPixelShader : kSlotVertexShader;
        p->argument = 0;
        p->dword = dword;
        p->words = 1;
        p->pool = op[1];
        return true;
      case kLiteralsWord:
        if (length != 2 || sub != 1) return false;
        p->type = op[1] >= 0x4400 ? kSlotPixelShader : kSlotVertexShader;
        p->argument = 1;
        p->dword = dword;
        p->words = 1;
        p->pool = op[3];
        return true;
      default:
        return false;
    }
  }

  Result AddPlace(const Input& in, const Place& p, uint32_t at) {
    if (p.dword + p.words > in.pm4_words) return No(Refuse::kPlaceField, at);
    const uint32_t existing = place_of_[p.dword];
    if (existing != kNone) {
      // The vertex and index buffers are in both tables.
      const Place& q = places_[existing];
      const bool same =
          q.type == p.type && q.argument == p.argument && q.dword == p.dword && q.words == p.words;
      return same ? Result() : No(Refuse::kPlaceConflict, at);
    }
    for (uint32_t i = 0; i < p.words; ++i) {
      if (place_of_[p.dword + i] != kNone) return No(Refuse::kPlaceConflict, at);
    }
    for (uint32_t i = 0; i < p.words; ++i) {
      place_of_[p.dword + i] = uint32_t(places_.size());
      pool_[p.pool + i] = 0;
    }
    places_.push_back(p);
    return {};
  }

  // What needs the places: every address a place, and the tile-bound packets.
  Result Check(const Input& in) {
    for (uint32_t op = 0; op < ops_.size() / kOpWords; ++op) {
      uint32_t* o = &ops_[size_t(op) * kOpWords];
      const uint32_t dword = op_dword_[op];
      switch (o[0] & 0xFF) {
        case kOpDraw:
          if (place_of_[dword + 2] == kNone) return No(Refuse::kDrawPlace, dword - 1);
          break;
        case kOpShader:
          if (place_of_[dword] == kNone) return No(Refuse::kShaderPlace, dword - 1);
          break;
        case kOpLiterals:
          if (place_of_[dword] == kNone) return No(Refuse::kLiteralsPlace, dword - 1);
          break;
        case kOpRegs: {
          // The packets scene_tiling.h's TileBoundPackets names (LayoutRunsWhole: the first
          // register decides for the packet).
          if (tiled_ && !(o[0] & kOpOneRegister) && scene_tiling::LayoutRegister(o[1])) {
            o[0] |= kOpTileBound;
            bound_.push_back(uint64_t(dword - 1) << 32 | Word(in.pm4, dword - 1));
          }
          break;
        }
        default:
          break;
      }
    }
    // A tile block's window and a plain write of it in one list would need their order kept
    // per draw.
    if (tile_blocks_ && window_write_ != kNone) return No(Refuse::kWindowMix, window_write_);
    if (!tiled_) bound_.clear();
    std::sort(bound_.begin(), bound_.end());  // by list dword, as LiveKey walks them
    return {};
  }

  // The draw records: per draw the ops since the previous one, their registers reduced to the
  // last write of each, and what is bound.
  void Records() {
    const uint32_t draws = uint32_t(draws_.size() / kDrawWords);
    draws_.resize(draws_.size() + kDrawWords, 0);  // the tail
    draws_[size_t(draws) * kDrawWords + kDFlags] = kDrawTail;
    if (last_.empty()) last_.assign(0x8000, kNone);
    uint32_t bound[kDrawWords];
    for (uint32_t& b : bound) b = kNone;
    bound[kDVertexShaderSize] = bound[kDPixelShaderSize] = 0;
    uint32_t first_op = 0, draw = 0;
    writes_.clear();
    const uint32_t op_count = uint32_t(ops_.size() / kOpWords);
    for (uint32_t op = 0; op <= op_count; ++op) {
      const uint32_t* o = op < op_count ? &ops_[size_t(op) * kOpWords] : nullptr;
      const uint32_t kind = o ? o[0] & 0xFF : uint32_t(kOpDraw);
      if (kind == kOpRegs) {
        if (o[0] & kOpOneRegister) {
          Write(o[1], op, o[2] - 1);
        } else {
          for (uint32_t i = 0; i < o[2]; ++i) Write(o[1] + i, op, i);
          if (ClassOf(o[1]) == RegClass::kFetch) {
            for (uint32_t i = 0; i < o[2]; i += 6) {
              const uint32_t reg = o[1] + i;
              bound[reg >= kRegVertexFetch ? uint32_t(kDVertexFetch)
                                           : kDTexture + (reg - kRegFetch) / 6] = o[3] + i;
            }
          }
        }
      } else if (kind == kOpLiterals) {
        for (uint32_t i = 0; i < o[2]; ++i) Write(o[1] + i, op, i);
      } else if (kind == kOpShader) {
        const bool pixel = (o[0] & kOpPixel) != 0;
        bound[pixel ? kDPixelShader : kDVertexShader] = o[1];
        bound[pixel ? kDPixelShaderSize : kDVertexShaderSize] = o[2];
      } else if (kind == kOpTile) {
        bound[kDTile] = op;
      } else {  // a draw, or the list's end
        uint32_t* r = &draws_[size_t(draw) * kDrawWords];
        r[kDFirstOp] = first_op;
        r[kDOpCount] = (o ? op + 1 : op) - first_op;
        r[kDDelta] = uint32_t(deltas_.size() / kDeltaWords);
        Reduce();
        r[kDDeltaCount] = uint32_t(deltas_.size() / kDeltaWords) - r[kDDelta];
        for (uint32_t i = kDVertexShader; i < kDrawWords; ++i) r[i] = bound[i];
        first_op = op + 1;
        ++draw;
      }
    }
  }
  void Write(uint32_t reg, uint32_t op, uint32_t offset) {
    if (last_[reg] == kNone) touched_.push_back(uint16_t(reg));
    last_[reg] = uint32_t(writes_.size() / 2);
    writes_.push_back(op);
    writes_.push_back(offset);
  }
  // The writes since the previous draw as runs of registers, each with its last writer.
  void Reduce() {
    std::sort(touched_.begin(), touched_.end());
    for (size_t i = 0; i < touched_.size();) {
      const uint32_t reg = touched_[i];
      const uint32_t op = writes_[size_t(last_[reg]) * 2],
                     offset = writes_[size_t(last_[reg]) * 2 + 1];
      const bool one = (ops_[size_t(op) * kOpWords] & kOpOneRegister) != 0;
      uint32_t count = 1;
      while (!one && i + count < touched_.size() && touched_[i + count] == reg + count &&
             writes_[size_t(last_[reg + count]) * 2] == op &&
             writes_[size_t(last_[reg + count]) * 2 + 1] == offset + count) {
        ++count;
      }
      deltas_.insert(deltas_.end(), {reg, count, op, offset});
      i += count;
    }
    for (uint16_t reg : touched_) last_[reg] = kNone;
    touched_.clear();
    writes_.clear();
  }

  void Emit(const Input& in, std::vector<uint32_t>* out) {
    std::vector<uint32_t>& t = *out;
    t.assign(kTHeaderWords, 0);
    t[kTMagic] = kMagic;
    t[kTVersion] = kVersion;
    t[kTListWords] = in.pm4_words;
    t[kTCbFlags] = cb_flags_;
    t[kTFlags] = tiled_ ? kTemplateTiled : 0;
    const auto section = [&t](uint32_t offset_word, const std::vector<uint32_t>& words,
                              uint32_t each) {
      t[offset_word] = uint32_t(t.size());
      t[offset_word + 1] = uint32_t(words.size() / each);
      t.insert(t.end(), words.begin(), words.end());
    };
    section(kTOps, ops_, kOpWords);
    section(kTDraws, draws_, kDrawWords);
    t[kTDrawCount] -= 1;  // the tail's record is not a draw
    section(kTDeltas, deltas_, kDeltaWords);
    std::sort(places_.begin(), places_.end(),
              [](const Place& a, const Place& b) { return a.dword < b.dword; });
    t[kTSlots] = uint32_t(t.size());
    t[kTSlotCount] = uint32_t(places_.size());
    for (const Place& p : places_) {
      t.insert(t.end(), {p.type | p.argument << 8, p.dword, p.pool, p.words});
    }
    section(kTPool, pool_, 1);
    t[kTTileBound] = uint32_t(t.size());
    t[kTTileBoundCount] = uint32_t(bound_.size());
    for (uint64_t b : bound_) t.insert(t.end(), {uint32_t(b >> 32), uint32_t(b)});
    t[kTMasks] = uint32_t(t.size());
    for (uint32_t i = 0; i < kMaskWords; ++i) t.push_back(Word(in.header, kHeaderMasks / 4 + i));
    t[kTWords] = uint32_t(t.size());
    const Key key = LiveKey(t.data(), in);
    std::memcpy(&t[kTKey], key.w, sizeof(key.w));
  }

  uint32_t cb_flags_ = 0, tile_blocks_ = 0, window_write_ = kNone;
  bool tiled_ = false, starts_with_tiles_ = false;
  std::vector<uint32_t> ops_, op_dword_, draws_, deltas_, pool_, info_, place_of_, last_, writes_;
  std::vector<uint64_t> bound_;  // list dword << 32 | header
  std::vector<uint8_t> kind_;
  std::vector<uint16_t> touched_;
  std::vector<Place> places_;
};

inline Result Convert(const Input& in, std::vector<uint32_t>* out) {
  Converter converter;
  return converter.Convert(in, out);
}

}  // namespace forza::native_list
