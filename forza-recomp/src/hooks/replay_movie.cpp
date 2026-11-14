// forza - a replay movie reads every frame back from a texture (#880, docs/rendering.md).
// tu_address_map: TU4 addresses
//
// MAKE MOVIE in a replay (the film button of the replay controls, signed in) encodes a WMV in the
// game and uploads it. While it runs the game steps the replay 1/30 s a frame, however long the
// frame takes, and draws every frame twice: the replay for the movie, then the PLEASE WAIT
// screen the player sees. After the first, the render thread (CRenderThread, TU4 addresses; the
// disc build's are below) copies the picture for the encoder in sub_82600710: it draws the
// frame's front buffer into a 640x360 (WEB) or 1280x720 (LARGE) X8R8G8B8 texture whose memory
// is the renderer's scratch block (the address at 0x833D4728 + 28), resolves into it, locks it
// and untiles it into the capture's CPU buffer. The game thread's CExportWMVComponent hands
// that buffer to the encoder (CWMVVideoCodec, sub_82F40310) with the frame's time. sub_82606930
// starts the capture on the render thread and sub_826006E0 ends it.
//
// readback_resolve = some (the default) copies a resolve back to guest memory only when its
// key is new. The texture is the same memory every frame, so the CPU saw its first copy for the
// whole movie: every frame the encoder got was that one picture. And that picture was black:
// the draw into the texture uses a pipeline of its own, which async_shader_compilation was
// still building, so the first frame's draw was skipped. The encoder writes a frame only when
// it encodes to more than one byte, so the file held just its key frames (one every 122
// frames), all black, and the sound.
//
// While a capture runs, the texture is in readback_resolve_wait_range (ReIGlue 0411): its
// resolves wait for the GPU and are copied back, as with readback_resolve = full. Only that
// resolve pays, one a frame while the movie is made. FORZA_MOVIE_READBACK=0 switches it off.
// And the frames are drawn with their real pipelines (thumbnail_sync.cpp, FORZA_MOVIE_SYNC=0
// switches that off): a movie is rendered once, like a photo.
//
// FORZA_MOVIE_TRACE=1 logs what the capture read for every frame (a hash and the mean of the
// texture's bytes), for telling a movie of one picture from a movie of the replay.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/readback_wait.h"
#include "../platform/replay_movie.h"
#include "thumbnail_sync.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

#if FORZA_TU4
#define MovieCaptureStart sub_82606930
#define OriginalMovieCaptureStart __imp__sub_82606930
#define MovieCaptureEnd sub_826006E0
#define OriginalMovieCaptureEnd __imp__sub_826006E0
#define MovieCaptureFrame sub_82600710
#define OriginalMovieCaptureFrame __imp__sub_82600710
constexpr uint32_t kScratchBlock = 0x833D4728 + 28;
#else
#define MovieCaptureStart sub_8258FF58
#define OriginalMovieCaptureStart __imp__sub_8258FF58
#define MovieCaptureEnd sub_8258A128
#define OriginalMovieCaptureEnd __imp__sub_8258A128
#define MovieCaptureFrame sub_8258A158
#define OriginalMovieCaptureFrame __imp__sub_8258A158
constexpr uint32_t kScratchBlock = 0x832E6B18 + 28;
#endif

namespace {

uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
}

bool EnvIs(const char* name, const char* value) {
  const char* v = std::getenv(name);
  return v && std::strcmp(v, value) == 0;
}

bool Enabled() {
  static const bool on = !EnvIs("FORZA_MOVIE_READBACK", "0");
  return on;
}

bool Traced() {
  static const bool on = EnvIs("FORZA_MOVIE_TRACE", "1");
  return on;
}

// The render thread's own state; the trace's counters are atomics only to be safe.
std::atomic<uint64_t> g_range{0};
std::atomic<uint32_t> g_frames{0}, g_changes{0};
std::atomic<uint64_t> g_last_hash{0};

}  // namespace

// void CRenderThread::StartCapture(type, user, buffer, width, height), on the render thread.
extern "C" REX_FUNC(OriginalMovieCaptureStart);
extern "C" REX_FUNC(MovieCaptureStart) {
  const uint32_t width = ctx.r7.u32, height = ctx.r8.u32;
  // Before the first frame's draws reach the GPU: its picture is drawn with a new pipeline.
  forza::thumbnail_sync::HoldForReplayMovie();
  OriginalMovieCaptureStart(ctx, base);
  const uint64_t range =
      forza::replay_movie::TextureRange(Load32(base, kScratchBlock), width, height);
  g_frames = 0;
  g_changes = 0;
  g_last_hash = 0;
  g_range = range;
  if (!range) {
    REXLOG_WARN("Replay movie: no capture texture for {}x{}; the movie may be one picture", width,
                height);
    return;
  }
  if (!Enabled()) {
    REXLOG_INFO("Replay movie: {}x{}, its texture ({:08X}, {} bytes) is not copied back "
                "(FORZA_MOVIE_READBACK=0)",
                width, height, uint32_t(range >> 32), uint32_t(range));
    return;
  }
  if (!forza::readback_wait::Request(forza::readback_wait::Owner::kReplayMovie, range)) {
    REXLOG_WARN("Replay movie: the SDK has no readback_resolve_wait_range; the movie may be one "
                "picture with readback_resolve = some");
    return;
  }
  REXLOG_INFO("Replay movie: {}x{}, its texture ({:08X}, {} bytes) is copied back for every frame",
              width, height, uint32_t(range >> 32), uint32_t(range));
}

// void CRenderThread::EndCapture(), on the render thread.
extern "C" REX_FUNC(OriginalMovieCaptureEnd);
extern "C" REX_FUNC(MovieCaptureEnd) {
  OriginalMovieCaptureEnd(ctx, base);
  if (!g_range.exchange(0)) return;
  forza::readback_wait::Request(forza::readback_wait::Owner::kReplayMovie, 0);
  REXLOG_INFO("Replay movie: capture ended after {} frames{}", g_frames.load(),
              Traced() ? fmt::format(" ({} of them a new picture)", g_changes.load())
                       : std::string());
}

// void CRenderThread::CaptureFrame(): one frame into the capture's buffer.
extern "C" REX_FUNC(OriginalMovieCaptureFrame);
extern "C" REX_FUNC(MovieCaptureFrame) {
  forza::thumbnail_sync::HoldForReplayMovie();
  OriginalMovieCaptureFrame(ctx, base);
  const uint32_t frame = g_frames.fetch_add(1);
  if (!Traced()) return;
  const uint64_t range = g_range;
  if (!range) return;
  // What the capture just read: the texture's bytes as they are in guest memory.
  const uint8_t* p = forza::GuestPtr(base, 0xA0000000u | uint32_t(range >> 32));
  const uint32_t size = uint32_t(range);
  uint64_t hash = 1469598103934665603ull, sum = 0;
  for (uint32_t i = 0; i < size; ++i) {
    hash = (hash ^ p[i]) * 1099511628211ull;
    sum += p[i];
  }
  const bool changed = hash != g_last_hash.exchange(hash);
  if (changed) ++g_changes;
  REXLOG_INFO("Replay movie: frame {} read {:016X}, mean byte {:.2f}{}", frame, hash,
              double(sum) / size, changed ? "" : " (the same as the frame before)");
}
