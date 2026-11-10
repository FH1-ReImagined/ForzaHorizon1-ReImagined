// forza - livery bake scale: sharper vinyls and decals on cars in free roam and races
// (livery_bake_scale, #659, docs/graphics-detail.md "Paint job detail").
//
// A car's paint job is drawn once, when the car loads, through a chain of render textures, all
// made with sub_82CF9058 (TU4 addresses; r4 width, r5 height, r6 format, r7 name, r9 levels):
//   LivInt-<n>-0..4: each side's layers (sizes from the car, 640x150 to 1882x724 on the
//     Aventador);
//   LivRenderTexture (sub_82D4BD60): the sides put together in the car's texture layout,
//     1024x1024 (2048 in the Paint Shop, where the setup's +236 is -1); the size is also kept
//     at +60 of the setup, which the draws into it read (sub_82D4E748 sets it again). Each
//     side goes to a rectangle given in pixels of the 1024x1024 layout (sub_82D511B8);
//   LivRenderPristineN / LivRenderDamageN (sub_82D53420): that picture without and with
//     damage, 1024x1024 with 6 mip levels, 8:8:8:8 (sub_82D54030 draws them with
//     sub_82D50040, in strips that fit EDRAM, then each mip from the one above);
//   LivPristineN / LivDamageN (sub_82D5B3D0): the same compressed to DXT5, 1024x1024 with 6
//     levels: the textures the car is drawn with. The compression (sub_825AC118, then
//     sub_82CFAB18 per level) goes through two scratch textures, 1024x1024 linear 8:8:8:8 and
//     DXT5, that sub_8259A318 makes for it (sub_82AFE400 gives their headers and sizes) and
//     frees after; they must be as large as the level they hold.
// (The "0" variants, 2048x2048, are made only when the setup's flags ask for them; never seen.)
// The Paint Shop instead draws the edited side into a 2048x2048 texture every frame, so small
// decals there have twice the texels they get on the car.
//
// livery_bake_scale = 2 makes every 1024x1024 texture of that chain 2048x2048 (one more mip
// level where it has mips, so the smallest stays 32x32), the +60 size, the sides' rectangles
// (for the blit) and the compression's scratch textures with it; the draws, strips, mips and
// the compression follow the sizes. Only a driving car's paint job (mode 6 or 8) is scaled:
// the Paint Shop's editor has its own 2048x2048 layout (+236 -1), and photo mode's paint job
// (mode 7, 8:8:8:8 final textures) has no room at 2048 (see Scaled below).
// Photo mode's paint job draws no sides: it loads the layout its driving paint job saved once
// drawn (sub_82D4E7D0, compressed, the layout's size squared x 4 bytes; sub_82D4E9F0 reads as
// many bytes as its own layout holds). At 2x that save is 2048x2048: read into the 1024x1024
// layout, a quarter of it came out cut into bands and shifted, the decals gone or in the wrong
// place (#680). The copy keeps the game's 1024x1024 layout (a 2048x2048 one does not always fit
// the memory photo mode has; the photo's final textures are 1024x1024 either way): the hook
// reads the whole save and halves it into that layout (lb::HalveTiled32), 2x2 texels to one.
// The CPU compresses the render copies' levels from guest memory, which the GPU's resolves
// write. With readback_resolve = some a resolve is copied back only when its key is new, and at
// 2x the damage copy's was not: LivDamageN came out the same as LivPristineN, so the car showed
// no scratches when driving (#680; with readback_resolve = full it has them). While a paint job
// is drawn at 2x, from its first render copy until 2 s after its last compression, all of
// physical memory is in readback_resolve_wait_range (src/platform/readback_wait.h).
// The scale is read once. 4096 is not offered: the game's memory for render textures has no
// room for them (the first 4096x4096 texture is not made and the game stops on the missing
// livery). Memory: src/platform/livery_bake.h, docs/graphics-detail.md.
// Disc build: sub_82C92390, sub_82C9A2D8, sub_82C8B028, sub_82C8D750, sub_82C38828,
// sub_8252CC60, sub_82A40CB0, sub_82C90178, sub_82C8D7D8, sub_82C8D9F8, sub_82C7D618 and
// sub_8253C9D0.
// tu_address_map: TU4 addresses

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/livery_bake.h"
#include "../platform/readback_wait.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <vector>

REXCVAR_DEFINE_INT32(livery_bake_scale, 1, "Display",
                     "Resolution of the textures each car's paint job (vinyls and decals) is drawn "
                     "into for driving: 1 (the game's 1024x1024) or 2 (2048x2048: "
                     "sharper small decals, more memory per car with a paint job)");

#if FORZA_TU4
#define RenderSetupStep sub_82D53420
#define OriginalRenderSetupStep __imp__sub_82D53420
#define CompressedSetupStep sub_82D5B3D0
#define OriginalCompressedSetupStep __imp__sub_82D5B3D0
#define MakeLayoutTexture sub_82D4BD60
#define OriginalMakeLayoutTexture __imp__sub_82D4BD60
#define ResetLayoutSize sub_82D4E748
#define OriginalResetLayoutSize __imp__sub_82D4E748
#define MakeRenderTexture sub_82CF9058
#define OriginalMakeRenderTexture __imp__sub_82CF9058
#define MakeScratchTextures sub_8259A318
#define OriginalMakeScratchTextures __imp__sub_8259A318
#define TextureHeaderCore sub_82AFE400
#define OriginalTextureHeaderCore __imp__sub_82AFE400
#define BlitSection sub_82D511B8
#define OriginalBlitSection __imp__sub_82D511B8
#define SaveLayout sub_82D4E7D0
#define OriginalSaveLayout __imp__sub_82D4E7D0
#define LoadLayout sub_82D4E9F0
#define OriginalLoadLayout __imp__sub_82D4E9F0
#define StreamRead sub_82D3E878
#define OriginalStreamRead __imp__sub_82D3E878
#define CompressTexture sub_825AC118
#define OriginalCompressTexture __imp__sub_825AC118
#else
#define RenderSetupStep sub_82C92390
#define OriginalRenderSetupStep __imp__sub_82C92390
#define CompressedSetupStep sub_82C9A2D8
#define OriginalCompressedSetupStep __imp__sub_82C9A2D8
#define MakeLayoutTexture sub_82C8B028
#define OriginalMakeLayoutTexture __imp__sub_82C8B028
#define ResetLayoutSize sub_82C8D750
#define OriginalResetLayoutSize __imp__sub_82C8D750
#define MakeRenderTexture sub_82C38828
#define OriginalMakeRenderTexture __imp__sub_82C38828
#define MakeScratchTextures sub_8252CC60
#define OriginalMakeScratchTextures __imp__sub_8252CC60
#define TextureHeaderCore sub_82A40CB0
#define OriginalTextureHeaderCore __imp__sub_82A40CB0
#define BlitSection sub_82C90178
#define OriginalBlitSection __imp__sub_82C90178
#define SaveLayout sub_82C8D7D8
#define OriginalSaveLayout __imp__sub_82C8D7D8
#define LoadLayout sub_82C8D9F8
#define OriginalLoadLayout __imp__sub_82C8D9F8
#define StreamRead sub_82C7D618
#define OriginalStreamRead __imp__sub_82C7D618
#define CompressTexture sub_8253C9D0
#define OriginalCompressTexture __imp__sub_8253C9D0
#endif
extern "C" REX_FUNC(OriginalRenderSetupStep);
extern "C" REX_FUNC(OriginalCompressedSetupStep);
extern "C" REX_FUNC(OriginalMakeLayoutTexture);
extern "C" REX_FUNC(OriginalResetLayoutSize);
extern "C" REX_FUNC(OriginalMakeRenderTexture);
extern "C" REX_FUNC(OriginalMakeScratchTextures);
extern "C" REX_FUNC(OriginalTextureHeaderCore);
extern "C" REX_FUNC(OriginalBlitSection);
extern "C" REX_FUNC(OriginalSaveLayout);
extern "C" REX_FUNC(OriginalLoadLayout);
extern "C" REX_FUNC(OriginalStreamRead);
extern "C" REX_FUNC(OriginalCompressTexture);

namespace {
namespace lb = forza::livery_bake;

// The setup's texture layout size (+60): 1024, or 2048 in the Paint Shop.
constexpr uint32_t kLayoutSizeOffset = 60;
// -1 in the Paint Shop, 1 a car's paint job, 2 photo mode's copy of it (lb::kCopy).
constexpr uint32_t kPaintShopOffset = 236;
// A copy's source: the object holding the saved layout (its data at +8).
constexpr uint32_t kSourceOffset = 12;
// What the paint job is drawn for (sub_82D5B3D0 picks its final textures by it).
constexpr uint32_t kModeOffset = 232;
// Each side's rectangle in the layout (in the paint job the blit gets): 4 floats a side.
constexpr uint32_t kSideRectOffset = 284;
constexpr uint32_t kSides = 5;

// Set while a step of a paint job outside the Paint Shop makes its textures.
thread_local bool g_in_livery = false;
thread_local bool g_in_scratch = false;
// The last layout made or reset is not scaled (the Paint Shop's editor, photo mode): the sides'
// blits into it and the compression's scratch textures are left as they are until the next
// layout of a scaled paint job.
std::atomic<bool> g_unscaled_layout{false};

std::atomic<uint32_t> g_logged{0};

// Set while photo mode's copy loads a layout saved at twice its own size: the save's size.
thread_local uint32_t g_halve_from = 0;

// The layouts driving paint jobs saved: the saved data's address and the size it was saved at.
std::mutex g_saved_mutex;
std::unordered_map<uint32_t, uint32_t> g_saved;  // guarded by g_saved_mutex

uint32_t SavedSize(uint32_t data) {
  std::lock_guard lock(g_saved_mutex);
  const auto it = g_saved.find(data);
  return it == g_saved.end() ? 0 : it->second;
}

// All of physical memory in readback_resolve_wait_range while a paint job is drawn at the
// scale: from its first render copy until kHoldAfter after the last of its steps.
using Clock = std::chrono::steady_clock;
constexpr uint64_t kAllPhysical = 0x20000000;  // start 0, length 512 MB
constexpr auto kHoldAfter = std::chrono::seconds(2);
std::mutex g_hold_mutex;  // the three below
bool g_held = false;
uint64_t g_hold_generation = 0;
Clock::time_point g_hold_last;

// Starts the hold (start) or keeps a running one going.
void HoldReadback(bool start) {
  std::lock_guard lock(g_hold_mutex);
  if (!g_held && !start) return;
  g_hold_last = Clock::now();
  if (g_held) return;
  if (!forza::readback_wait::Request(forza::readback_wait::Owner::kLiveryBake, kAllPhysical)) {
    static bool warned = false;
    if (!warned) REXLOG_WARN("livery_bake_scale: the SDK has no readback_resolve_wait_range; cars may show no scratches");
    warned = true;
    return;
  }
  g_held = true;
  const uint64_t generation = ++g_hold_generation;
  std::thread([generation] {
    for (;;) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      std::lock_guard lock(g_hold_mutex);
      if (!g_held || g_hold_generation != generation) return;
      if (Clock::now() - g_hold_last > kHoldAfter) {
        forza::readback_wait::Request(forza::readback_wait::Owner::kLiveryBake, 0);
        g_held = false;
        return;
      }
    }
  }).detach();
}

uint32_t Scale() {
  static const uint32_t scale = [] {
    const uint32_t s = lb::ParseScale(REXCVAR_GET(livery_bake_scale));
    if (s > 1) REXLOG_INFO("livery_bake_scale: paint jobs drawn at {}x{}", 1024 * s, 1024 * s);
    return s;
  }();
  return scale;
}

bool Active() { return Scale() > 1 && !g_unscaled_layout.load(std::memory_order_relaxed); }

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = forza::GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}

// Whether a paint job (setup) is drawn at the scale: not the Paint Shop's (whose +236 is -1),
// and one whose final textures are the DXT5 pair (its mode at +232 is 6 or 8, a car driving).
// Other modes keep the game's size: photo mode's paint job (mode 7) ends in two 8:8:8:8
// textures instead (the jump table of sub_82D5B3D0: modes 2, 5, 7 and 9), and at 2048x2048 it
// did not fit the game's memory for render textures next to the car's own (the texture was not
// made and Take Photo crashed).
bool Scaled(uint8_t* base, uint32_t setup) {
  if (!setup || Load32(base, setup + kPaintShopOffset) == 0xFFFFFFFFu) return false;
  const uint32_t mode = Load32(base, setup + kModeOffset);
  return mode == 6 || mode == 8;
}

// The layout size follows its texture: scaled where the game picked 1024.
void ScaleLayoutSize(uint8_t* base, uint32_t setup) {
  const uint32_t scale = Scale();
  if (scale <= 1 || !Scaled(base, setup)) return;
  const uint32_t at = setup + kLayoutSizeOffset;
  if (Load32(base, at) == lb::kGameSize) Store32(base, at, lb::ScaledSize(lb::kGameSize, scale));
}

// The size the layout photo mode's copy of a paint job loads was saved at (0 for any other paint
// job, or a save it has no size for).
uint32_t CopySavedSize(uint8_t* base, uint32_t setup) {
  if (!setup || Load32(base, setup + kPaintShopOffset) != lb::kCopy) return 0;
  const uint32_t source = Load32(base, setup + kSourceOffset);
  const uint32_t data = source >= 0x10000 ? Load32(base, source + 8) : 0;
  return data ? SavedSize(data) : 0;
}

}  // namespace

// One step of a car livery's setup: makes at most one of LivRenderPristineN/DamageN (or 0).
extern "C" REX_FUNC(RenderSetupStep) {
  g_in_livery = Scaled(base, ctx.r3.u32);
  if (g_in_livery && Scale() > 1) HoldReadback(true);
  OriginalRenderSetupStep(ctx, base);
  g_in_livery = false;
}

// One step of the compressed side: makes LivPristineN/DamageN (or 0). It runs before the
// layout texture of the same paint job is made.
extern "C" REX_FUNC(CompressedSetupStep) {
  g_in_livery = Scaled(base, ctx.r3.u32);
  OriginalCompressedSetupStep(ctx, base);
  g_in_livery = false;
}

// Makes LivRenderTexture and sets the layout size (+60) the draws into it use.
extern "C" REX_FUNC(MakeLayoutTexture) {
  const uint32_t setup = ctx.r3.u32;
  const bool scaled = Scaled(base, setup);
  if (Scale() > 1) {
    static std::atomic<uint32_t> logged{0};
    if (logged.fetch_add(1, std::memory_order_relaxed) < 16) {
      REXLOG_INFO("livery_bake_scale: paint job mode {}{}: {}", Load32(base, setup + kModeOffset),
                  Load32(base, setup + kPaintShopOffset) == 0xFFFFFFFFu ? " (Paint Shop)" : "",
                  scaled ? "scaled" : "the game's size");
    }
  }
  g_unscaled_layout = !scaled;
  g_in_livery = scaled;
  OriginalMakeLayoutTexture(ctx, base);
  g_in_livery = false;
  if (scaled) ScaleLayoutSize(base, setup);
}

// Photo mode's copy loads its layout from the save (r3 the paint job; one read of its layout's
// size squared x 4 bytes, StreamRead below).
extern "C" REX_FUNC(LoadLayout) {
  const uint32_t setup = ctx.r3.u32;
  const uint32_t own = Load32(base, setup + kLayoutSizeOffset);
  const uint32_t saved = CopySavedSize(base, setup);
  g_halve_from = lb::HalvesSave(Scale(), own, saved) ? saved : 0;
  if (g_halve_from) {
    static std::atomic<uint32_t> logged{0};
    if (logged.fetch_add(1, std::memory_order_relaxed) < 4) {
      REXLOG_INFO("livery_bake_scale: photo mode's paint job: the {}x{} save halved into its {}x{} "
                  "layout", saved, saved, own, own);
    }
  }
  OriginalLoadLayout(ctx, base);
  g_halve_from = 0;
}

// Reads r5 bytes of a stream (r3) to r4. For a copy whose save is twice its layout's size, the
// whole save is read in four parts of the layout's size (through the layout's memory) and halved
// into the layout.
extern "C" REX_FUNC(StreamRead) {
  const uint32_t from = g_halve_from;
  const uint32_t to = from / 2;
  const uint32_t bytes = to * to * 4;
  if (!from || ctx.r5.u32 != bytes || ctx.r4.u32 < 0x10000) {
    OriginalStreamRead(ctx, base);
    return;
  }
  g_halve_from = 0;  // the load's one read
  const uint32_t stream = ctx.r3.u32, dst = ctx.r4.u32;
  std::vector<uint8_t> save(size_t(from) * from * 4);
  for (uint32_t part = 0; part < 4; ++part) {
    ctx.r3.u64 = stream;
    ctx.r4.u64 = dst;
    ctx.r5.u64 = bytes;
    OriginalStreamRead(ctx, base);
    std::memcpy(save.data() + size_t(part) * bytes, forza::GuestPtr(base, dst), bytes);
  }
  lb::HalveTiled32(save.data(), from, forza::GuestPtr(base, dst));
}

// Sets the layout size again (1024, or 2048 in the Paint Shop).
extern "C" REX_FUNC(ResetLayoutSize) {
  const uint32_t setup = ctx.r3.u32;
  OriginalResetLayoutSize(ctx, base);
  if (Scaled(base, setup)) {
    ScaleLayoutSize(base, setup);
  } else {
    g_unscaled_layout = true;
  }
}

// Saves a driving paint job's layout for photo mode's copy (r3 where the saved data goes, r4 the
// layout texture, r5 its size): the size is kept with the data's address.
extern "C" REX_FUNC(SaveLayout) {
  const uint32_t out = ctx.r3.u32, size = ctx.r5.u32;
  OriginalSaveLayout(ctx, base);
  const uint32_t saved = out >= 0x10000 ? Load32(base, out) : 0;
  const uint32_t data = saved >= 0x10000 ? Load32(base, saved + 8) : 0;
  if (!data) return;
  std::lock_guard lock(g_saved_mutex);
  if (g_saved.size() >= 64) g_saved.clear();  // one per paint job drawn; never grows unbounded
  g_saved[data] = size;
}

// Makes a render texture: r3 the slot, r4 width, r5 height, r6 format, r7 name, r9 levels.
extern "C" REX_FUNC(MakeRenderTexture) {
  if (g_in_livery && Scale() > 1 && lb::IsLiveryFormat(ctx.r6.u32)) {
    const uint32_t scale = Scale();
    const lb::Texture game{ctx.r4.u32, ctx.r5.u32, ctx.r9.u32};
    const lb::Texture t = lb::Scaled(game, scale);
    if (t.width != game.width) {
      ctx.r4.u64 = t.width;
      ctx.r5.u64 = t.height;
      ctx.r9.u64 = t.levels;
      if (g_logged.fetch_add(1, std::memory_order_relaxed) < 8) {
        REXLOG_INFO("livery_bake_scale {}: livery texture {:08X} {}x{} ({} levels) -> {}x{} ({} "
                    "levels, {:.1f} MB)",
                    scale, ctx.r6.u32, game.width, game.height, game.levels, t.width, t.height,
                    t.levels, double(lb::Bytes(t, ctx.r6.u32)) / (1024.0 * 1024.0));
      }
    }
  }
  OriginalMakeRenderTexture(ctx, base);
}

// Makes the compression's two 1024x1024 scratch textures (once, until the compression frees
// them): their headers and memory sizes come from sub_82AFE400.
extern "C" REX_FUNC(MakeScratchTextures) {
  HoldReadback(false);
  g_in_scratch = true;
  OriginalMakeScratchTextures(ctx, base);
  g_in_scratch = false;
}

// Compresses one render copy into its DXT5 texture, on the CPU from guest memory.
extern "C" REX_FUNC(CompressTexture) {
  HoldReadback(false);
  OriginalCompressTexture(ctx, base);
  HoldReadback(false);
}

// A texture header: r3 width, r4 height, r5 depth, r6 levels; returns the memory size.
extern "C" REX_FUNC(TextureHeaderCore) {
  if (g_in_scratch && Active() && ctx.r3.u32 == lb::kGameSize && ctx.r4.u32 == lb::kGameSize) {
    ctx.r3.u64 = lb::ScaledSize(lb::kGameSize, Scale());
    ctx.r4.u64 = lb::ScaledSize(lb::kGameSize, Scale());
  }
  OriginalTextureHeaderCore(ctx, base);
}

// Puts one side (r4) of the paint job into the layout texture (r3 the paint job; r7 set for
// the Paint Shop's highlighted side, which it places at twice its rectangle in the Paint Shop's
// 2048x2048 layout). Each side's place is a rectangle of 4 floats at r3 + 284 + 16 x side in
// pixels of the game's 1024x1024 layout (left, right, top, bottom: 70, 1011, 476, 838 for the
// Aventador's left side); in a scaled layout it is scaled for the call.
extern "C" REX_FUNC(BlitSection) {
  const uint32_t scale = Scale();
  const bool scaled = Active() && ctx.r7.u8 == 0 && ctx.r4.u32 < kSides;
  float saved[4] = {};
  const uint32_t rect = ctx.r3.u32 + kSideRectOffset + 16 * ctx.r4.u32;
  if (scaled) {
    for (int i = 0; i < 4; ++i) {
      uint32_t bits = Load32(base, rect + 4 * i);
      std::memcpy(&saved[i], &bits, 4);
      const float v = saved[i] * float(scale);
      std::memcpy(&bits, &v, 4);
      Store32(base, rect + 4 * i, bits);
    }
  }
  OriginalBlitSection(ctx, base);
  if (scaled) {
    for (int i = 0; i < 4; ++i) {
      uint32_t bits;
      std::memcpy(&bits, &saved[i], 4);
      Store32(base, rect + 4 * i, bits);
    }
  }
}
