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
// The scale is read once. 4096 is not offered: the game's memory for render textures has no
// room for them (the first 4096x4096 texture is not made and the game stops on the missing
// livery). Memory: src/platform/livery_bake.h, docs/graphics-detail.md.
// Disc build: sub_82C92390, sub_82C9A2D8, sub_82C8B028, sub_82C8D750, sub_82C38828,
// sub_8252CC60, sub_82A40CB0 and sub_82C90178.
// tu_address_map: TU4 addresses

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/livery_bake.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>
#include <cstring>

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
#endif
extern "C" REX_FUNC(OriginalRenderSetupStep);
extern "C" REX_FUNC(OriginalCompressedSetupStep);
extern "C" REX_FUNC(OriginalMakeLayoutTexture);
extern "C" REX_FUNC(OriginalResetLayoutSize);
extern "C" REX_FUNC(OriginalMakeRenderTexture);
extern "C" REX_FUNC(OriginalMakeScratchTextures);
extern "C" REX_FUNC(OriginalTextureHeaderCore);
extern "C" REX_FUNC(OriginalBlitSection);

namespace {
namespace lb = forza::livery_bake;

// The setup's texture layout size (+60): 1024, or 2048 in the Paint Shop.
constexpr uint32_t kLayoutSizeOffset = 60;
// -1 in the Paint Shop.
constexpr uint32_t kPaintShopOffset = 236;
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

}  // namespace

// One step of a car livery's setup: makes at most one of LivRenderPristineN/DamageN (or 0).
extern "C" REX_FUNC(RenderSetupStep) {
  g_in_livery = Scaled(base, ctx.r3.u32);
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
  g_in_scratch = true;
  OriginalMakeScratchTextures(ctx, base);
  g_in_scratch = false;
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
