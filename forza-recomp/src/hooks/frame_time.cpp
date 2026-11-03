// forza - frame pacing and frame time at rates other than the console's 30 (docs/framerate.md).
//
// Pacing. The game's vblank callback (sub_8257E888, which ends in sub_8247C678) counts guest
// vblanks and sets the main loop's event every second one while the developer option
// mainthread30fps (options object 0x832E29A0 +0x95B, on by default) is set, and on every
// vblank when it is off (with frontend30fps, +0x95A, off as it is by default): Forza
// Motorsport 4's 60 fps setup. Above 30 the divider of two stays and the guest vblank runs
// at twice the frame rate while that fits under the SDK's limit (480 Hz with SDK patch 0037,
// else 240: 144 fps then runs a 144 Hz vblank with mainthread30fps off). A fast vblank
// matters: the emulated GPU's swap waits end on a vblank (SDK patch 0030), so the render
// thread's frames come in whole vblanks. 20 and 40 fps (#263) run the 60 and 120 Hz vblank
// of the screens they divide, with a divider of three (the game's counter sees two of every
// three vblanks): a frame that misses its vblank comes one screen refresh late, not 1.5.
// Their presents are paced too ("Present pacing", below).
//
// Frame time. The main loop (sub_823ED888) takes its frame time from the kernel's
// millisecond tick, so above 60 fps each frame's dt is whole milliseconds, and the frames
// start on vblanks the SDK's VSync thread fires at its next 1 ms check: at 144 fps the game
// saw 5 to 9 ms for frames 6.94 ms apart (0.1 ms when two fell in one millisecond), and
// motion judders, though the sum stays right. At rates other than 30 the main loop's call of
// sub_8247C168 (which picks the plain "dt = ms * 0.001" path when it returns true) is
// answered true with f31, the loop's dt register, set from the guest tick clock instead.
// Each frame gets a timestamp: the
// nominal time of the vblank that released it when it started within 2 ms of that vblank
// (on target), else the time it started (a late frame); dt is the difference of two
// timestamps. So frames that keep up get exactly the vblank period, late frames their
// real duration, and the sum is real time, as before. The game's own rules stay where
// they matter: more than 4 s (a load) still counts as one 60 Hz frame, and dt is at least
// 0.1 ms. FORZA_FRAME_TIME=game keeps the millisecond frame time (for comparisons);
// FORZA_FRAMETIME_LOG=1 logs the dt statistics every 10 s, FORZA_FRAMETIME_CSV each frame.

#include "frame_time.h"
#include "../debug/stall_watch.h"
#include "../platform/graphics_menu.h"
#include "../platform/benchmark.h"
#include "../platform/frame_pacing.h"

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/chrono/clock.h>
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/ui/flags.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

REXCVAR_DECLARE(int32_t, frame_rate);  // src/main.cpp

namespace {

constexpr uint32_t kOptions = FORZA_ADDR(0x832E29A0);           // sub_82479E88's static object
constexpr uint32_t kOptionsGuard = FORZA_ADDR(0x832E4A70);      // its construction guard (bit 0)
constexpr uint32_t kMainThread30Fps = kOptions + 0x95B;
constexpr uint32_t kFrameTimeCallReturn = FORZA_ADDR(0x823EDB08);  // sub_823ED888's call of sub_8247C168

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

int EnvInt(const char* name, int fallback) {
  const char* v = std::getenv(name);
  return v && *v ? std::atoi(v) : fallback;
}

bool PreciseFrameTime() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_FRAME_TIME");
    return !(v && std::strcmp(v, "game") == 0);
  }();
  return on;
}

bool FrameTimeLog() {
  static const bool on = EnvInt("FORZA_FRAMETIME_LOG", 0) != 0;
  return on;
}

// The highest guest refresh rate the SDK runs: the video_mode_refresh_rate setting's range,
// which VdQueryVideoMode clamps to as well (240 Hz, or 480 with SDK patch 0037).
double MaxVblankHz() {
  static const double hz = [] {
    for (const rex::cvar::FlagEntry& entry : rex::cvar::GetRegistry()) {
      if (entry.name == "video_mode_refresh_rate" && entry.constraints.max.has_value()) {
        return *entry.constraints.max;
      }
    }
    return 240.0;
  }();
  return hz;
}

double GuestVblankHz() {
  return std::clamp(REXCVAR_GET(video_mode_refresh_rate), 24.0, MaxVblankHz());
}

uint64_t VblankIntervalTicks() {
  // As the SDK's VSync thread computes it (graphics_system.cpp, VdQueryVideoMode's float), at
  // the current rate: the in-game graphics menu changes it while the game runs.
  const double hz = double(float(GuestVblankHz()));
  return std::max<uint64_t>(1, uint64_t(double(rex::chrono::Clock::guest_tick_frequency()) / hz));
}

// The last guest vblank: its count, when the callback ran, and the nominal time of that
// vblank. The SDK's VSync thread fires vblank j at the first 1 ms check at or after
// start + j * interval, so a callback is never early: the nominal schedule is kept at the
// earliest time consistent with every callback seen.
struct VblankState {
  std::atomic<uint64_t> seq{0};  // odd while being written (one writer: the VSync thread)
  std::atomic<uint64_t> count{0}, actual{0}, nominal{0};
} vblank;

void OnVblank() {
  constexpr auto kRelaxed = std::memory_order_relaxed;
  const uint64_t now = rex::chrono::Clock::QueryGuestTickCount();
  const uint64_t interval = VblankIntervalTicks();
  const uint64_t count = vblank.count.load(kRelaxed);
  uint64_t nominal = vblank.nominal.load(kRelaxed);
  if (count == 0) {
    nominal = now;
  } else {
    nominal += interval;
    if (now < nominal) {
      nominal = now;
    } else if (now - nominal >= interval) {
      nominal += (now - nominal) / interval * interval;  // vblanks we did not see
    }
  }
  vblank.seq.fetch_add(1, std::memory_order_acq_rel);
  vblank.count.store(count + 1, kRelaxed);
  vblank.actual.store(now, kRelaxed);
  vblank.nominal.store(nominal, kRelaxed);
  vblank.seq.fetch_add(1, std::memory_order_release);
}

void ReadVblank(uint64_t& count, uint64_t& actual, uint64_t& nominal) {
  for (;;) {
    const uint64_t s = vblank.seq.load(std::memory_order_acquire);
    if (s & 1) continue;
    count = vblank.count.load(std::memory_order_relaxed);
    actual = vblank.actual.load(std::memory_order_relaxed);
    nominal = vblank.nominal.load(std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_acquire);
    if (vblank.seq.load(std::memory_order_relaxed) == s) return;
  }
}

// Render sync. Away from 30 fps the main loop runs at the target rate whatever the render thread
// makes, and the render thread draws the newest main frame it finds. When the render thread
// falls behind (a race at 240 fps renders about 63 frames a second here), the main loop runs
// three or four frames for each one drawn, and now and then the drawn frame has no HUD (#78:
// the whole HUD missing for a frame, about one frame in 50), and the simulated time between
// two drawn frames jumps by whole main frames (motion judders). While the render thread's
// frames take more than 1.15 target frames (a moving average over about 16 swaps), each main
// frame waits until the render thread has swapped since the previous one (at most 50 ms),
// so the game runs at the rate it draws. The frame's timestamp is taken before the wait, so
// dt still adds up to real time. When the render thread keeps up, nothing waits.
// The wait also ends when the render thread finds no main frame to draw (its loop,
// sub_8259F3E8, leaves its frame count alone and returns): it is waiting for this main frame
// then, and no swap can come before the main loop goes on. A frame it skips is not swapped
// (with mainthread30fps off it draws every second one), so waiting for a swap ran into the
// 50 ms limit on every such frame; the swaps then came about every 55 ms, which kept the
// average high and the waits going: about 18 fps until a restart (#187).
// FORZA_RENDER_SYNC=0 turns it off.
constexpr uint32_t kRenderFrameCount = 0x948;  // render object: main frames it has taken

struct RenderState {
  std::mutex mutex;
  std::condition_variable swapped;
  uint64_t swaps = 0, last_tick = 0;
  double interval_ema = 0;  // ticks between swaps
  std::atomic<uint64_t> waiting{0};  // the main loop's wait in progress (0: none)
  uint64_t idle = 0;                 // the wait in which the render loop found nothing to draw
} render;

// Present pacing (20 and 40 fps, PresentDelayNs). Where in its frame a guest frame reaches the
// host present varies: the emulated GPU's swap ends on a guest vblank (SDK patch 0030), the
// SDK's VSync thread fires a vblank up to about 1 ms after its time (it checks every 1 ms),
// and a frame that is ready a little later swaps a vblank later. In a 20 fps race the
// presents came 0.4 to 2.1 ms after the nominal time of the vblank their swap ended on, now
// and then a vblank later; at 40 fps one frame in twenty swapped a 120 Hz vblank early.
// Against the screen, whose refresh drifts slowly against the guest clock, that spread is a
// phase window: while it straddles a refresh deadline, frames alternate between 2 and 4
// refreshes of a 60 Hz screen instead of 3. So the first present of each guest frame waits
// until a fixed time after the start of the frame period it falls in (the latest on-target
// frame start, plus whole frames): about the 98th percentile of those times
// (src/platform/frame_pacing.h). A later present is not held. FORZA_PRESENT_PACING=0 turns
// it off.
std::atomic<uint64_t> guest_swaps{0};

struct PaceState {
  std::mutex mutex;
  uint64_t seen_swaps = 0;
  forza::frame_pacing::PresentPacer pacer;
} pace;
std::atomic<uint64_t> last_release{0};  // nominal start of the latest on-target frame (ticks)

bool PresentPacingEnabled() {
  static const bool on = EnvInt("FORZA_PRESENT_PACING", 1) != 0;
  return on;
}

struct SyncStats {
  uint64_t waits = 0, timeouts = 0;
  double wait_ms = 0;
} sync_stats;

bool RenderSyncEnabled() {
  static const bool on = EnvInt("FORZA_RENDER_SYNC", 1) != 0;
  return on;
}

void WaitForRender() {
  static uint64_t seen = 0;
  const uint64_t freq = rex::chrono::Clock::guest_tick_frequency();
  const uint64_t start = rex::chrono::Clock::QueryGuestTickCount();
  const double target_ticks = forza::frame_time::TargetMs() * 1e-3 * double(freq);
  std::unique_lock<std::mutex> lock(render.mutex);
  const bool behind = render.interval_ema > 1.15 * target_ticks &&
                      start < render.last_tick + freq / 10;  // swaps are coming (100 ms)
  if (behind && render.swaps == seen) {
    static uint64_t waits = 0;
    const uint64_t id = ++waits;
    render.waiting.store(id, std::memory_order_release);
    const bool progressed = render.swapped.wait_for(
        lock, std::chrono::milliseconds(50),
        [&] { return render.swaps != seen || render.idle == id; });
    render.waiting.store(0, std::memory_order_relaxed);
    sync_stats.waits += 1;
    sync_stats.timeouts += !progressed;
    sync_stats.wait_ms +=
        double(rex::chrono::Clock::QueryGuestTickCount() - start) * 1000.0 / double(freq);
  }
  seen = render.swaps;
}

struct Stats {
  uint64_t frames = 0, paced = 0, game_kept = 0;
  double sum = 0, sum2 = 0, min = 1e9, max = 0;
  double game_sum = 0, game_sum2 = 0;
  uint64_t window_start = 0;
};

void LogStats(Stats& s, uint64_t now) {
  const uint64_t freq = rex::chrono::Clock::guest_tick_frequency();
  if (!s.window_start) s.window_start = now;
  if (now - s.window_start < 10 * freq || !s.frames) return;
  const double n = double(s.frames), mean = s.sum / n, game_mean = s.game_sum / n;
  REXLOG_INFO(
      "FRAMETIME: frames={} on_target={:.1f}% dt mean={:.3f} sd={:.3f} min={:.3f} max={:.3f} ms; "
      "game's ms dt mean={:.3f} sd={:.3f}; kept game's={}; render sync waits={} ({:.0f} ms), timeouts={}",
      s.frames, 100.0 * s.paced / n, mean, std::sqrt(std::max(0.0, s.sum2 / n - mean * mean)),
      s.min, s.max, game_mean,
      std::sqrt(std::max(0.0, s.game_sum2 / n - game_mean * game_mean)), s.game_kept,
      sync_stats.waits, sync_stats.wait_ms, sync_stats.timeouts);
  sync_stats = SyncStats{};
  s = Stats{};
  s.window_start = now;
}

// FORZA_FRAMETIME_CSV=<path>: one line per frame, "t,dt,game_ms,on_target,x,z" (ms since
// the first frame, our dt in ms, the game's whole-ms frame time, our car's position as
// tools/autodrive.py reads it), to check how motion follows dt.
std::FILE* FrameCsv() {
  static std::FILE* f = [] {
    const char* path = std::getenv("FORZA_FRAMETIME_CSV");
    return path && *path ? std::fopen(path, "w") : nullptr;
  }();
  return f;
}

bool GuestAddr(uint32_t a) { return a >= 0x00100000 && a < 0xF0000000; }

bool OwnCarPosition(uint8_t* base, float& x, float& z) {
  auto u32 = [base](uint32_t a) { return Load32(forza::GuestPtr(base, a)); };
  const uint32_t root = u32(FORZA_ADDR(0x832DF024));
  if (!GuestAddr(root) || !GuestAddr(u32(root + 4))) return false;
  const uint32_t list = u32(u32(root + 4) + 4);
  if (!GuestAddr(list)) return false;
  const uint32_t first = u32(list + 0x80), end = u32(list + 0x84);
  if (!GuestAddr(first) || end < first || end - first > 256) return false;
  for (uint32_t slot = first; slot < end; slot += 4) {
    const uint32_t vehicle = u32(slot);
    if (!GuestAddr(vehicle) || u32(vehicle + 0x2DD0) != 0) continue;
    const uint32_t car = u32(vehicle + 0x2D90);
    if (!GuestAddr(car)) continue;
    uint32_t xb = u32(car - 0xA20), zb = u32(car - 0xA20 + 8);
    std::memcpy(&x, &xb, 4);
    std::memcpy(&z, &zb, 4);
    return std::isfinite(x) && std::isfinite(z);
  }
  return false;
}

// Returns false to leave the game's frame time.
bool FrameTime(uint8_t* base, uint32_t game_ms, double& dt_out) {
  static uint64_t prev = 0, seen_count = 0;
  static Stats stats;
  static int last_rate = 0;
  const int rate = REXCVAR_GET(frame_rate);
  if (rate != last_rate) { prev = 0; seen_count = 0; last_rate = rate; }
  const uint64_t now = rex::chrono::Clock::QueryGuestTickCount();
  const uint64_t freq = rex::chrono::Clock::guest_tick_frequency();
  uint64_t count, actual, nominal;
  ReadVblank(count, actual, nominal);
  const uint64_t window = std::min(freq / 500, VblankIntervalTicks() / 2);  // 2 ms
  const bool paced = count != seen_count && now >= actual && now - actual <= window;
  seen_count = count;
  uint64_t stamp = paced ? nominal : now;
  if (paced) last_release.store(stamp, std::memory_order_release);
  if (!prev) {
    prev = stamp;
    return false;
  }
  const uint64_t min_step = freq / 10000;  // 0.1 ms, the game's minimum dt
  if (stamp < prev + min_step) stamp = prev + min_step;
  const double dt = double(stamp - prev) / double(freq);
  prev = stamp;
  if (FrameTimeLog()) {
    const double ms = dt * 1000.0;
    stats.frames += 1;
    stats.paced += paced;
    stats.sum += ms;
    stats.sum2 += ms * ms;
    stats.min = std::min(stats.min, ms);
    stats.max = std::max(stats.max, ms);
    stats.game_sum += game_ms;
    stats.game_sum2 += double(game_ms) * game_ms;
    if (dt > 4.0) stats.game_kept += 1;
    LogStats(stats, now);
  }
  if (std::FILE* csv = FrameCsv()) {
    static uint64_t first = stamp;
    float x = 0, z = 0;
    const bool car = OwnCarPosition(base, x, z);
    std::fprintf(csv, "%.3f,%.4f,%u,%d,%.3f,%.3f\n", double(stamp - first) * 1000.0 / double(freq),
                 dt * 1000.0, game_ms, int(paced), car ? x : 0.0f, car ? z : 0.0f);
  }
  if (dt > 4.0) return false;  // the game's rule: a long pause counts as one 60 Hz frame
  dt_out = dt;
  return true;
}

}  // namespace

namespace forza::frame_time {

bool ConsoleRate() { return REXCVAR_GET(frame_rate) == 30; }

void ResetRenderTiming() {
  std::lock_guard lock(render.mutex);
  render.last_tick = 0;
  render.interval_ema = 0;
}

bool EvenRate() {
  const int fps = REXCVAR_GET(frame_rate);
  return fps == 20 || fps == 40;
}

int VblanksPerFrame() {
  static const int forced = EnvInt("FORZA_VBLANK_DIVIDER", 0);  // for comparisons
  return frame_pacing::VblanksPerFrame(REXCVAR_GET(frame_rate), MaxVblankHz(), forced);
}

int64_t PresentDelayNs() {
  if (!EvenRate() || !PresentPacingEnabled()) return 0;
  const uint64_t swaps = guest_swaps.load(std::memory_order_acquire);
  std::lock_guard lock(pace.mutex);
  if (swaps == pace.seen_swaps) return 0;  // no new guest frame: a repaint of the UI
  pace.seen_swaps = swaps;
  const uint64_t release = last_release.load(std::memory_order_acquire);
  const uint64_t now = rex::chrono::Clock::QueryGuestTickCount();
  const uint64_t period = VblankIntervalTicks() * uint64_t(VblanksPerFrame());
  if (!release || now < release) return 0;
  const double ms = double(rex::chrono::Clock::guest_tick_frequency()) / 1000.0;
  const double wait = pace.pacer.Delay(double((now - release) % period), double(period), ms);
  return int64_t(wait * 1e6 / ms);
}

void OnGuestSwap() {
  if (ConsoleRate()) return;
  guest_swaps.fetch_add(1, std::memory_order_release);
  const uint64_t now = rex::chrono::Clock::QueryGuestTickCount();
  {
    std::lock_guard<std::mutex> lock(render.mutex);
    if (render.last_tick) {
      const double interval = double(now - render.last_tick);
      render.interval_ema =
          render.interval_ema ? render.interval_ema + (interval - render.interval_ema) / 16 : interval;
    }
    render.last_tick = now;
    render.swaps += 1;
  }
  render.swapped.notify_all();
}

int VblankHz() { return REXCVAR_GET(frame_rate) * VblanksPerFrame(); }

double TargetMs() { return 1000.0 * VblanksPerFrame() / GuestVblankHz(); }

}  // namespace forza::frame_time

// The vblank callback's counter (sub_8257E888 tail-calls it with the vblank data stored).
extern "C" REX_FUNC(__imp__sub_8247C678);
extern "C" REX_FUNC(sub_8247C678) {
  if (!forza::frame_time::ConsoleRate()) {
    OnVblank();
    const int divider = forza::frame_time::VblanksPerFrame();
    // One vblank per frame needs the game's divider off; two or three need it on.
    // Restore both states: an earlier one-vblank rate or the option parser may have
    // disabled it. With it left off, showing two of every three vblanks doubles 20/40 fps.
    if (Load32(forza::GuestPtr(base, kOptionsGuard)) & 1) {
      *forza::GuestPtr(base, kMainThread30Fps) = uint8_t(divider != 1);
    }
    // A divider of three (20 and 40 fps): the counter, with mainthread30fps on, releases the
    // main loop at every second vblank it sees; it sees two of every three, so whatever its
    // count was, the releases come every third vblank.
    static forza::frame_pacing::ThirdVblank third;
    if (divider == 3 && !third.Forward()) return;
  }
  __imp__sub_8247C678(ctx, base);
}

// One pass of the render thread's loop (r3: the render object): it takes the next main frame
// and draws it, or skips it, or finds none and sleeps 0 ms. A pass that began while a main
// frame waits (render sync) and took no frame ends that wait.
extern "C" REX_FUNC(__imp__sub_8259F3E8);
extern "C" REX_FUNC(sub_8259F3E8) {
  const uint64_t wait = render.waiting.load(std::memory_order_acquire);
  const uint32_t frames = ctx.r3.u32 + kRenderFrameCount;
  const uint32_t before = wait ? Load32(forza::GuestPtr(base, frames)) : 0;
  __imp__sub_8259F3E8(ctx, base);
  if (!wait || Load32(forza::GuestPtr(base, frames)) != before) return;
  {
    std::lock_guard<std::mutex> lock(render.mutex);
    render.idle = wait;
  }
  render.swapped.notify_all();
}

// sub_823ED888 calls this right after the frame time in ms (r30) and dt = ms * 0.001 (f31);
// true makes the loop store f31 as the frame's dt (game +0x1C0) and pass it on.
extern "C" REX_FUNC(__imp__sub_8247C168);
extern "C" REX_FUNC(sub_8247C168) {
  const uint32_t lr = uint32_t(ctx.lr);
  const uint32_t game_ms = ctx.r30.u32;
  __imp__sub_8247C168(ctx, base);
  if (lr != kFrameTimeCallReturn) return;
  forza::stall::OnMainFrame();
  forza::graphics_menu::Tick(ctx, base);
  forza::benchmark::Tick(ctx, base);
  if (forza::frame_time::ConsoleRate()) return;
  double dt;
  const bool timed = FrameTime(base, game_ms, dt);
  if (RenderSyncEnabled()) WaitForRender();
  if (!timed || !PreciseFrameTime()) return;
  ctx.f31.f64 = double(float(dt));
  ctx.r3.u64 = 1;
}
