// forza - dump presented frames from guest memory, independent of window focus.
//
// FORZA_FRAMEDUMP="55,65.5" dumps the front buffer handed to VdSwap at those
// seconds after the first swap, into $FORZA_FRAMEDUMP_DIR (default /tmp) as
// forza_frame_<secs>.bin plus a .json sidecar describing the layout. Convert
// with tools/frame_to_png.py. Run with --vulkan_readback_resolve=true so GPU
// resolves are written back to guest memory; otherwise the dump shows whatever
// the CPU last wrote there.
//
// FORZA_FRAMEDUMP_TRIGGER=<path> also dumps a frame whenever that file appears
// (it is deleted again), for stepping through menus with FORZA_AUTOPRESS_FIFO.
//
// FORZA_SCREENSHOT_TRIGGER=<path> saves the host's output image instead (what the
// window shows, at the draw resolution scale, e.g. --resolution_scale=2) as
// forza_shot_<secs>.ppm in the same directory whenever that file appears.
// FORZA_SCREENSHOT_BURST=N[:K] makes each trigger save N images, one every K guest
// frames (K defaults to 1), named with milliseconds, for measuring how fast something
// moves on screen.
//
// FORZA_FPSLOG=1 logs the guest frame rate (VdSwap calls) every 5 seconds.
//
// FORZA_INVALIDATE_AT="20" fires the physical-memory write callbacks over the
// whole 0xA0000000 mirror once at that time, forcing the GPU to drop and
// re-upload every watched texture (diagnoses stale GPU texture copies).
//
// FORZA_RDCAPTURE="25,90" asks RenderDoc (when the game runs under renderdoccmd)
// to capture a few frames at each of those times, without needing keyboard
// focus. Each capture is a separate .rdc file. FORZA_RDCAPTURE_TRIGGER=<path>
// also captures whenever that file appears (it is deleted again), so a scene
// can be caught by hand while watching periodic frame dumps; load times vary
// too much between runs for fixed times to land on the same shot.

#include "../platform/guest_memory.h"
#include "../platform/sdk_import.h"
#include "frame_stats.h"
#include "stall_watch.h"
#include "../hooks/frame_time.h"
#include "../platform/ultrawide.h"
#include "../platform/xr.h"

#include <rex/logging.h>
#include <rex/runtime.h>
#include <rex/system/interfaces/graphics.h>
// The SDK's runtime is built with the spatial output effects (SDK patch 0043), which changes
// the layout of ui::Presenter; this file calls into it, so it sees the same layout.
#ifndef REX_HAS_FIDELITYFX_SDK
#define REX_HAS_FIDELITYFX_SDK 1
#endif
#include <rex/ui/presenter.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>
#include <rex/thread/mutex.h>

#ifndef __APPLE__
#include <renderdoc/renderdoc_app.h>
#endif

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using forza::GuestPtr;

namespace {

using GuestFunc = void (*)(PPCContext&, uint8_t*);

// Physical memory is mirrored into the guest virtual space at 0xA0000000.
constexpr uint32_t kPhysicalMirror = 0xA0000000;

uint32_t LoadBE32(const uint8_t* p) {
  uint32_t v;
  std::memcpy(&v, p, 4);
  return __builtin_bswap32(v);
}

struct Schedule {
  std::vector<double> times;
  size_t next = 0;
  std::string dir = "/tmp";
};

// Parses "12,30.5,60" into sorted seconds.
std::vector<double> ParseTimes(const char* spec) {
  std::vector<double> times;
  std::string s(spec ? spec : "");
  size_t pos = 0;
  while (pos < s.size()) {
    size_t end = s.find(',', pos);
    if (end == std::string::npos) end = s.size();
    times.push_back(std::atof(s.substr(pos, end - pos).c_str()));
    pos = end + 1;
  }
  std::sort(times.begin(), times.end());
  return times;
}

Schedule& GetSchedule() {
  static Schedule schedule;
  static std::once_flag once;
  std::call_once(once, [] {
    if (const char* dir = std::getenv("FORZA_FRAMEDUMP_DIR")) schedule.dir = dir;
    if (const char* spec = std::getenv("FORZA_FRAMEDUMP")) {
      schedule.times = ParseTimes(spec);
      REXLOG_INFO("FORZA_FRAMEDUMP: {} dumps scheduled into {}", schedule.times.size(),
                  schedule.dir);
    }
  });
  return schedule;
}

void DumpFrontBuffer(uint8_t* base, uint32_t fetch_addr, double seconds, const std::string& dir) {
  // Texture fetch constant (6 big-endian dwords).
  const uint8_t* fetch = GuestPtr(base, fetch_addr);
  uint32_t dw0 = LoadBE32(fetch), dw1 = LoadBE32(fetch + 4), dw2 = LoadBE32(fetch + 8);
  uint32_t address = dw1 & 0xFFFFF000u;
  uint32_t format = dw1 & 0x3F;
  uint32_t width = (dw2 & 0x1FFF) + 1;
  uint32_t height = ((dw2 >> 13) & 0x1FFF) + 1;
  uint32_t pitch_texels = ((dw0 >> 22) & 0x1FF) << 5;
  bool tiled = (dw0 >> 31) & 1;
  uint32_t aligned_width = std::max(pitch_texels, (width + 31) & ~31u);
  uint32_t aligned_height = (height + 31) & ~31u;
  size_t bytes = size_t(aligned_width) * aligned_height * 4;

  char name[64];
  std::snprintf(name, sizeof(name), "forza_frame_%07.2f", seconds);
  std::string stem = dir + "/" + name;
  // The front buffer constant holds a virtual address in one of the physical
  // mirrors (0xE3044000 in Halo: Reach). Read it through that mapping: the 0xE0000000
  // mirror sits 4 KiB higher in physical memory than the 0xA0000000 one, so
  // reading the masked address via 0xA0000000 shifts the image by one tile.
  const uint8_t* src =
      address >= kPhysicalMirror ? GuestPtr(base, address) : GuestPtr(base, kPhysicalMirror + address);
  if (FILE* f = std::fopen((stem + ".bin").c_str(), "wb")) {
    std::fwrite(src, 1, bytes, f);
    std::fclose(f);
  }
  if (FILE* f = std::fopen((stem + ".json").c_str(), "w")) {
    std::fprintf(f,
                 "{\"width\": %u, \"height\": %u, \"aligned_width\": %u, \"aligned_height\": %u, "
                 "\"format\": %u, \"tiled\": %s, \"address\": %u, \"seconds\": %.2f}\n",
                 width, height, aligned_width, aligned_height, format, tiled ? "true" : "false",
                 address, seconds);
    std::fclose(f);
  }
  REXLOG_INFO("FORZA_FRAMEDUMP: t={:.2f}s {}x{} fmt={} tiled={} addr={:#x} -> {}.bin", seconds,
              width, height, format, tiled, address, stem);
}

void MaybeInvalidatePhysicalMemory(double now) {
  static const double at = [] {
    const char* v = std::getenv("FORZA_INVALIDATE_AT");
    return v ? std::atof(v) : -1.0;
  }();
  static bool done = false;
  if (done || at < 0 || now < at) return;
  done = true;
  auto* memory = rex::system::kernel_state()->memory();
  bool any = memory->TriggerPhysicalMemoryCallbacks(
      rex::thread::global_critical_region::AcquireDirect(), 0xA0000000u, 0x20000000u,
      /*is_write=*/true, /*unwatch_exact_range=*/false);
  REXLOG_INFO("FORZA_INVALIDATE_AT: physical write callbacks fired at t={:.2f}s (watched pages: {})",
              now, any);
}

// Captures explicitly with Start/EndFrameCapture spanning a few guest frames:
// TriggerCapture never produced a file with this runtime's presenter.
void MaybeTriggerRenderDocCapture(double now) {
#ifdef __APPLE__
  // RenderDoc does not support macOS. Guest dumps and host screenshots still work.
  static std::once_flag warning;
  std::call_once(warning, [] {
    if (std::getenv("FORZA_RDCAPTURE") || std::getenv("FORZA_RDCAPTURE_TRIGGER")) {
      REXLOG_WARN("FORZA_RDCAPTURE: RenderDoc capture is unavailable on macOS");
    }
  });
#else
  static const std::vector<double> times = ParseTimes(std::getenv("FORZA_RDCAPTURE"));
  static size_t next = 0;
  static int swaps = 0;  // swaps captured so far in the open capture, 0 when idle
  static RENDERDOC_API_1_0_0* api = nullptr;
  if (swaps > 0) {
    if (++swaps > 3) {
      uint32_t ok = api->EndFrameCapture(nullptr, nullptr);
      REXLOG_INFO("FORZA_RDCAPTURE: capture ended at t={:.2f}s (result {})", now, ok);
      swaps = 0;
    }
    return;
  }
  static const char* trigger = std::getenv("FORZA_RDCAPTURE_TRIGGER");
  static int polls = 0;
  bool due = next < times.size() && now >= times[next];
  while (next < times.size() && times[next] <= now) ++next;
  // The trigger file is polled every 10 swaps.
  if (!due && trigger && *trigger && ++polls % 10 == 0) {
    due = std::remove(trigger) == 0;
  }
  if (!due) return;
  if (!api) {
#ifdef _WIN32
    HMODULE lib = GetModuleHandleA("renderdoc.dll");
    auto get_api =
        lib ? reinterpret_cast<pRENDERDOC_GetAPI>(GetProcAddress(lib, "RENDERDOC_GetAPI")) : nullptr;
#else
    void* lib = dlopen("librenderdoc.so", RTLD_NOW | RTLD_NOLOAD);
    auto get_api =
        lib ? reinterpret_cast<pRENDERDOC_GetAPI>(dlsym(lib, "RENDERDOC_GetAPI")) : nullptr;
#endif
    if (!get_api || !get_api(eRENDERDOC_API_Version_1_0_0, reinterpret_cast<void**>(&api)) ||
        !api) {
      REXLOG_WARN("FORZA_RDCAPTURE: RenderDoc is not loaded (run under renderdoccmd capture)");
      api = nullptr;
      next = times.size();
      trigger = nullptr;
      return;
    }
  }
  api->StartFrameCapture(nullptr, nullptr);
  REXLOG_INFO("FORZA_RDCAPTURE: capture started at t={:.2f}s", now);
  swaps = 1;
#endif
}

// Saves the presenter's current guest output image (what the window shows before
// scaling to it, at the draw resolution scale) as a binary PPM. On its own thread:
// the capture waits for a GPU copy.
void CaptureHostOutput(double now, const std::string& dir, bool precise = false) {
  std::thread([now, dir, precise] {
    auto* runtime = rex::Runtime::instance();
    auto* graphics = runtime ? runtime->graphics_system() : nullptr;
    auto* presenter = graphics ? graphics->presenter() : nullptr;
    rex::ui::RawImage image;
    if (!presenter || !presenter->CaptureGuestOutput(image)) {
      REXLOG_WARN("FORZA_SCREENSHOT: no presenter output to capture");
      return;
    }
    char name[64];
    std::snprintf(name, sizeof(name), precise ? "/forza_shot_%08.3f.ppm" : "/forza_shot_%07.2f.ppm",
                  now);
    const std::string path = dir + name;
    FILE* f = std::fopen((path + ".tmp").c_str(), "wb");
    if (!f) return;
    std::fprintf(f, "P6\n%u %u\n255\n", image.width, image.height);
    std::vector<uint8_t> row(size_t(image.width) * 3);
    for (uint32_t y = 0; y < image.height; ++y) {
      const uint8_t* src = image.data.data() + y * image.stride;
      for (uint32_t x = 0; x < image.width; ++x) std::memcpy(&row[3 * x], src + 4 * x, 3);
      std::fwrite(row.data(), 1, row.size(), f);
    }
    std::fclose(f);
    std::rename((path + ".tmp").c_str(), path.c_str());
    REXLOG_INFO("FORZA_SCREENSHOT: {}x{} -> {}", image.width, image.height, path);
  }).detach();
}

}  // namespace

// void VdSwap(buffer_ptr, fetch_ptr, unk2, unk3, unk4, frontbuffer_ptr,
//             texture_format_ptr, color_space_ptr, width_ptr, height_ptr)
extern "C" REX_FUNC(__imp__VdSwap) {
  static GuestFunc sdk = forza::SdkImport("__imp__VdSwap");
  static const auto start = std::chrono::steady_clock::now();
  const uint32_t packet_addr = ctx.r3.u32;
  const uint32_t fetch_addr = ctx.r4.u32;

  const double elapsed =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  forza::perf::OnGuestFrame();
  forza::ultrawide::Frame();
  forza::frame_time::OnGuestSwap();
  forza::stall::OnGuestSwap();
  MaybeTriggerRenderDocCapture(elapsed);
  MaybeInvalidatePhysicalMemory(elapsed);
  static const bool fps_log = [] {
    const char* v = std::getenv("FORZA_FPSLOG");
    return v && *v && *v != '0';
  }();
  if (fps_log) {
    static double window_start = elapsed;
    static uint32_t swaps = 0;
    ++swaps;
    if (elapsed - window_start >= 5.0) {
      REXLOG_INFO("FORZA_FPSLOG: t={:.0f}s {:.1f} fps", elapsed, swaps / (elapsed - window_start));
      window_start = elapsed;
      swaps = 0;
    }
  }
  Schedule& schedule = GetSchedule();
  static const char* dump_trigger = std::getenv("FORZA_FRAMEDUMP_TRIGGER");
  static int dump_polls = 0;
  if (dump_trigger && *dump_trigger && fetch_addr && ++dump_polls % 10 == 0 &&
      std::remove(dump_trigger) == 0) {
    DumpFrontBuffer(base, fetch_addr, elapsed, schedule.dir);
  }
  static const char* shot_trigger = std::getenv("FORZA_SCREENSHOT_TRIGGER");
  static int shot_polls = 0;
  static int burst_n = 1, burst_every = 1, burst_left = 0, burst_frame = 0;
  static std::once_flag burst_once;
  std::call_once(burst_once, [] {
    if (const char* v = std::getenv("FORZA_SCREENSHOT_BURST")) {
      std::sscanf(v, "%d:%d", &burst_n, &burst_every);
      burst_n = std::max(1, burst_n);
      burst_every = std::max(1, burst_every);
    }
  });
  if (burst_left > 0) {
    if (++burst_frame % burst_every == 0) {
      CaptureHostOutput(elapsed, schedule.dir, true);
      --burst_left;
    }
  } else if (shot_trigger && *shot_trigger && ++shot_polls % 10 == 0 &&
             std::remove(shot_trigger) == 0) {
    CaptureHostOutput(elapsed, schedule.dir, burst_n > 1);
    burst_left = burst_n - 1;
    burst_frame = 0;
  }
  if (schedule.next < schedule.times.size() && fetch_addr) {
    double now = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    if (now >= schedule.times[schedule.next]) {
      DumpFrontBuffer(base, fetch_addr, now, schedule.dir);
      while (schedule.next < schedule.times.size() && schedule.times[schedule.next] <= now) {
        ++schedule.next;
      }
    }
  }
  if (sdk) sdk(ctx, base);
  // VR cockpit: the swap packet carries the tag of the head pose this frame was drawn with.
  forza::xr::TagSwap(base, packet_addr);
}
