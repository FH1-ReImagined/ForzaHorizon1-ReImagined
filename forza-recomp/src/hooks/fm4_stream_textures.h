#pragma once
// A converted FM4 circuit's streamed textures (#824, src/hooks/fm4_track.cpp): the memory the
// hook gives a texture's data when the game's own heap for it is full, and which failed
// allocation is a streamed texture's. No game, no SDK: tests/fm4_stream_textures_test.cpp.

#include <cstdint>
#include <deque>
#include <iterator>
#include <map>
#include <unordered_map>

namespace forza::fm4_stream_textures {

// The return address of the texture load's allocation (TU4 sub_82CEBE10 calls sub_82423000 at
// 0x82CEBF90 for the whole of a texture's _0x<id>.bix file). Only that caller's failed
// allocations are taken over: the heap's other users keep the game's own behaviour.
constexpr uint32_t kTextureLoadReturn = 0x82CEBF94;
constexpr bool IsTextureLoad(uint32_t return_address) {
  return return_address == kTextureLoadReturn;
}

constexpr uint32_t kPage = 4096;
// Guest memory is taken a chunk at a time and kept; at most kMaxChunks of them. A lap of Le
// Mans held 28 MB at most, of Suzuka 16 MB.
constexpr uint32_t kChunkBytes = 8u << 20;
constexpr uint32_t kMaxChunks = 8;
// A block given back is not handed out again for this long: the emulated GPU's thread draws
// the frames already queued after the game's thread has let their textures go, and for those
// frames the texels must still be there (as they are in the game's own heap until the block is
// used again). It is not what keeps other textures' texels out of a block: see Arena.
constexpr uint64_t kReuseDelayMs = 2000;
// A chunk the guest's heap had no room for is not asked for again for this long. Every failed
// texture load would ask (hundreds a second at a busy place), each with error lines in the log.
constexpr uint64_t kChunkRetryMs = 1000;

constexpr uint32_t Rounded(uint32_t size) { return (size + kPage - 1) & ~(kPage - 1); }

// Blocks of whole pages cut from the chunks, first fit, neighbours joined when they come back.
// The chunks stay allocated for the whole session, which is what keeps the texels right: the
// emulated GPU is told of every write to memory that stays allocated, also when a block is
// used again at once. Memory released to the guest's heap is another matter (fm4_track.cpp):
// a frame still queued can read it once more, the GPU side then holds its copy as up to date
// with nothing watching the pages, and what is written there after the next allocation is
// never seen. Not thread safe (the hook locks).
class Arena {
 public:
  // A chunk of guest memory to cut blocks from (the hook allocates it).
  void AddChunk(uint32_t address, uint32_t bytes) {
    ++chunks_;
    capacity_ += bytes;
    refused_ = false;
    Free(address, bytes);
  }
  // Whether the hook may ask the guest's heap for another chunk now: fewer than kMaxChunks,
  // and no request was refused in the last kChunkRetryMs.
  bool MayAddChunk(uint64_t now_ms) const { return chunks_ < kMaxChunks && now_ms >= ask_again_ms_; }
  // The guest's heap had no chunk to give. True the first time since a chunk was last added:
  // the hook logs it once.
  bool ChunkRefused(uint64_t now_ms) {
    ask_again_ms_ = now_ms + kChunkRetryMs;
    const bool first = !refused_;
    refused_ = true;
    return first;
  }
  // A block of at least `bytes`, or 0 when no free run is long enough (the hook then adds a
  // chunk, or the allocation fails as it did). `now_ms` is any clock that does not go back.
  uint32_t Take(uint32_t bytes, uint64_t now_ms) {
    Settle(now_ms);
    // So near 4 GB that rounding up to whole pages would wrap to a block of nothing.
    if (bytes > UINT32_MAX - (kPage - 1)) return 0;
    bytes = Rounded(bytes ? bytes : 1);
    for (auto it = free_.begin(); it != free_.end(); ++it) {
      if (it->second < bytes) continue;
      const uint32_t address = it->first, rest = it->second - bytes;
      free_.erase(it);
      if (rest) free_.emplace(address + bytes, rest);
      used_.emplace(address, bytes);
      held_ += bytes;
      if (held_ > peak_) peak_ = held_;
      return address;
    }
    return 0;
  }
  // A block given back; false when the address is not a block of this arena in use.
  bool Give(uint32_t address, uint64_t now_ms) {
    auto it = used_.find(address);
    if (it == used_.end()) return false;
    cooling_.push_back({address, it->second, now_ms + kReuseDelayMs});
    held_ -= it->second;
    used_.erase(it);
    return true;
  }
  uint32_t chunks() const { return chunks_; }
  uint64_t capacity() const { return capacity_; }
  uint32_t blocks() const { return uint32_t(used_.size()); }  // in use
  uint64_t held() const { return held_; }                      // bytes in use
  uint64_t peak() const { return peak_; }

 private:
  struct Cooling {
    uint32_t address, bytes;
    uint64_t ready_ms;
  };
  // The blocks whose delay is over become free runs.
  void Settle(uint64_t now_ms) {
    while (!cooling_.empty() && cooling_.front().ready_ms <= now_ms) {
      Free(cooling_.front().address, cooling_.front().bytes);
      cooling_.pop_front();
    }
  }
  void Free(uint32_t address, uint32_t bytes) {
    auto next = free_.lower_bound(address);
    if (next != free_.begin()) {
      auto before = std::prev(next);
      if (before->first + before->second == address) {
        address = before->first;
        bytes += before->second;
        free_.erase(before);
      }
    }
    if (next != free_.end() && address + bytes == next->first) {
      bytes += next->second;
      free_.erase(next);
    }
    free_.emplace(address, bytes);
  }

  std::map<uint32_t, uint32_t> free_;            // address -> bytes, never touching each other
  std::unordered_map<uint32_t, uint32_t> used_;  // address -> bytes
  std::deque<Cooling> cooling_;                  // given back, in the order they came
  uint64_t capacity_ = 0, held_ = 0, peak_ = 0;
  uint64_t ask_again_ms_ = 0;  // no chunk request before this
  uint32_t chunks_ = 0;
  bool refused_ = false;  // the last chunk request was refused
};

}  // namespace forza::fm4_stream_textures
