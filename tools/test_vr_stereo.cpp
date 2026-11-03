// VR true stereo's decisions (ReIGlue 0396, #333): src/graphics/fh1_vr_stereo.h of the ReIGlue
// source. The draws are the cockpit frame's as FORZA_GPU_FRAME_TRACE recorded them (TU4, free
// roam, render scale 2, 2x MSAA).
// Build with the ReIGlue source's src/graphics on the include path:
//   c++ -std=c++17 -I ~/rexglue-sdk-src-forza/sdk/src/graphics tools/test_vr_stereo.cpp
#include "fh1_vr_stereo.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace s = rex::graphics::fh1_vr_stereo;

#define CHECK(x)                                               \
  do {                                                         \
    if (!(x)) {                                                \
      std::fprintf(stderr, "vr stereo: FAIL %d: %s\n", __LINE__, #x); \
      std::exit(1);                                            \
    }                                                          \
  } while (false)

namespace {

// A clear: a window-space rectangle writing depth everywhere (dctl 0x8777), 640 at 4x MSAA.
s::DrawState Clear(uint32_t depth_base) {
  s::DrawState d;
  d.mode = s::kModeDepthOnly;
  d.pitch = 640;
  d.msaa = s::kMsaa4X;
  d.depth_base = depth_base;
  d.z_enable = d.z_write = true;
  d.zfunc = s::kCompareAlways;
  d.window_space = true;
  d.primitive = s::kPrimitiveRectangleList;
  d.viewport_x_scale = 640;
  d.viewport_y_scale = -360;
  return d;
}
// A 3D draw of the 1280 x 720 view (vte 0x43F).
s::DrawState View(uint32_t mode, uint32_t msaa, uint32_t depth_base, bool z_write) {
  s::DrawState d;
  d.mode = mode;
  d.pitch = 1280;
  d.msaa = msaa;
  d.depth_base = depth_base;
  d.z_enable = true;
  d.z_write = z_write;
  d.zfunc = s::kCompareGreaterEqual;
  d.projected = true;
  d.primitive = 4;
  d.viewport_x_scale = 640;
  d.viewport_y_scale = -360;
  d.viewport_x_offset = 640;
  return d;
}
s::DrawState FrontBuffer(uint32_t format, uint32_t base) {
  s::DrawState d;
  d.mode = s::kModeColorDepth;
  d.pitch = 1280;
  d.msaa = s::kMsaa1X;
  d.color_format = format;
  d.color_base = base;
  d.depth_base = 0x2D0;
  d.primitive = s::kPrimitiveRectangleList;
  d.viewport_x_scale = 640;
  d.viewport_y_scale = -180;
  return d;
}

void Segment() {
  s::Tracker t;
  // The HUD's render-to-texture clear (1280 across) is a candidate, its next draw is not a start.
  CHECK(s::Classify(Clear(0x2D0), false, t) == s::Draw::kClear);
  CHECK(s::Classify(View(s::kModeColorDepth, s::kMsaa1X, 0x2D0, false), false, t) !=
        s::Draw::kSegmentStart);  // no depth write: not the scene's first draw
  // The sun's shadow cascades: 1040 across, not a main-view clear.
  s::DrawState cascade_clear = Clear(0x2D0);
  cascade_clear.pitch = 520;
  CHECK(s::Classify(cascade_clear, false, t) == s::Draw::kOther);
  // The depth pre-pass after its clear draws depth only: not the start.
  CHECK(s::Classify(Clear(0x000), false, t) == s::Draw::kClear);
  CHECK(t.depth_base == 0x000);
  CHECK(s::Classify(View(s::kModeDepthOnly, s::kMsaa1X, 0x000, true), false, t) == s::Draw::kOther);
  // The scene: its clear (depth 0x400) and its first draw (colour, 2x MSAA, reversed depth).
  CHECK(s::Classify(Clear(0x400), false, t) == s::Draw::kClear);
  CHECK(s::Classify(View(s::kModeColorDepth, s::kMsaa2X, 0x400, true), false, t) ==
        s::Draw::kSegmentStart);
  // While recording: the composite into the front buffer, then the first HUD draw ends it.
  CHECK(s::Classify(View(s::kModeColorDepth, s::kMsaa2X, 0x400, true), true, t) == s::Draw::kOther);
  CHECK(s::Classify(FrontBuffer(s::kFormat_2_10_10_10_AS_10_10_10_10, 0), true, t) ==
        s::Draw::kOther);  // before any composite
  CHECK(s::Classify(FrontBuffer(s::kFormat_2_10_10_10, 0), true, t) == s::Draw::kOther);
  CHECK(t.composite_seen && t.composite_base == 0);
  CHECK(s::Classify(FrontBuffer(s::kFormat_2_10_10_10_AS_10_10_10_10, 0x10), true, t) ==
        s::Draw::kOther);  // another target
  CHECK(s::Classify(FrontBuffer(s::kFormat_2_10_10_10_AS_10_10_10_10, 0), true, t) ==
        s::Draw::kSegmentEnd);
  // Resolves (copy mode) are never classified.
  s::DrawState copy = Clear(0x400);
  copy.mode = 6;
  CHECK(s::Classify(copy, false, t) == s::Draw::kOther);
}

void Offsets() {
  s::Tracker t;
  t.depth_base = 0x400;
  // The scene's opaque and transparent 3D draws.
  CHECK(s::TakesOffset(View(s::kModeColorDepth, s::kMsaa2X, 0x400, true), t));
  CHECK(s::TakesOffset(View(s::kModeColorDepth, s::kMsaa2X, 0x400, false), t));
  // The depth pre-pass and the shadow mask (the pre-pass's depth buffer) stay the middle's.
  CHECK(!s::TakesOffset(View(s::kModeDepthOnly, s::kMsaa1X, 0x000, true), t));
  // A full-screen depth write (zfunc always), a window-space draw, post-processing (no depth test).
  s::DrawState always = View(s::kModeDepthOnly, s::kMsaa2X, 0x400, true);
  always.zfunc = s::kCompareAlways;
  CHECK(!s::TakesOffset(always, t));
  s::DrawState flat = View(s::kModeColorDepth, s::kMsaa2X, 0x400, false);
  flat.projected = false;
  CHECK(!s::TakesOffset(flat, t));
  s::DrawState post = View(s::kModeColorDepth, s::kMsaa1X, 0x400, false);
  post.z_enable = false;
  CHECK(!s::TakesOffset(post, t));
  // The rear-view mirror's viewport (384 x 128) and the reflection cube's (256 x 256).
  s::DrawState mirror = View(s::kModeColorDepth, s::kMsaa2X, 0x400, true);
  mirror.viewport_x_scale = 192;
  mirror.viewport_y_scale = -64;
  CHECK(!s::TakesOffset(mirror, t));
  s::DrawState cube = mirror;
  cube.viewport_x_scale = 128;
  cube.viewport_y_scale = -128;
  CHECK(!s::TakesOffset(cube, t));

  // The cockpit's P00 0.5376 (123 degrees across), 63 mm between the eyes: 0.0169 in clip space.
  float o[2];
  s::EyeOffsets(0.5376f, 0.063f, true, o);
  CHECK(std::fabs(o[0] - 0.016934f) < 1e-5f && std::fabs(o[1] + 0.016934f) < 1e-5f);
  s::EyeOffsets(0.5376f, 0.063f, false, o);
  CHECK(o[0] < 0 && o[1] > 0);
  // The near cockpit at 0.7 m moves 0.0169 / 0.7 of the half-width, about 31 pixels at 2560 across
  // (measured: 32), the scenery at 100 m a third of a pixel.
  CHECK(std::fabs(0.016934f / 0.7f * 1280 - 31) < 1);
  CHECK(0.016934f / 100.0f * 1280 < 0.25f);
}

void Columns() {
  // A Quest 3's left eye sees 0 to 80 % of the cockpit picture (its field of view and the margin),
  // the right eye 20 to 100 %. Mirrored: the render target's columns are the other way round.
  uint32_t c[2];
  // (Rounded outward: a column partly seen is drawn.)
  s::EyeColumns(0.0f, 0.8f, true, 1280, c);
  CHECK((c[0] == 255 || c[0] == 256) && c[1] == 1280);
  s::EyeColumns(0.2f, 1.0f, true, 1280, c);
  CHECK(c[0] == 0 && (c[1] == 1024 || c[1] == 1025));
  s::EyeColumns(0.2f, 1.0f, false, 1280, c);
  CHECK((c[0] == 255 || c[0] == 256) && c[1] == 1280);
  s::EyeColumns(-0.5f, 2.0f, true, 1280, c);  // clamped
  CHECK(c[0] == 0 && c[1] == 1280);
  // A tile's scissor limited to the eye's columns.
  uint32_t offset = 0, extent = 1280;
  const uint32_t left[2] = {256, 1280};
  s::ClipColumns(offset, extent, left);
  CHECK(offset == 256 && extent == 1024);
  offset = 0;
  extent = 100;
  s::ClipColumns(offset, extent, left);
  CHECK(extent == 0);
}

void Packets() {
  // The second pass leaves out what talks to the CPU, and indirect buffers (their packets are
  // logged one by one); keeps constants, registers, shaders, draws and the bin masks.
  for (uint32_t op : {0x3Fu, 0x37u, 0x54u, 0x64u, 0x3Cu, 0x3Du, 0x58u, 0x5Bu, 0x23u})
    CHECK(s::SkipsOpcode(op));
  for (uint32_t op : {0x22u, 0x36u, 0x2Du, 0x2Fu, 0x27u, 0x2Bu, 0x60u, 0x62u, 0x46u})
    CHECK(!s::SkipsOpcode(op));
  CHECK(s::PacketWords(0x00031234u) == 5);  // type 0, 4 registers
  CHECK(s::PacketWords(0x40000000u) == 3);  // type 1
  CHECK(s::PacketWords(0x80000000u) == 0);  // type 2
  CHECK(s::PacketWords(0xC0022200u) == 4);  // DRAW_INDX with 3 words
  // LOAD_ALU_CONSTANT of 8 fetch constants from 6: registers 0x4806 on, as a type-0 header.
  uint32_t first = 0;
  CHECK(s::LoadedConstantsHeader((1u << 16) | 6, 8, 0x5003, &first) == ((7u << 16) | 0x4806));
  CHECK(first == 0x4806);
  CHECK(s::LoadedConstantsHeader((0u << 16) | 0x3FC, 32, 0x5003, &first) == ((31u << 16) | 0x43FC));
  CHECK(s::LoadedConstantsHeader((5u << 16), 4, 0x5003, &first) == 0);  // no such type
  CHECK(s::LoadedConstantsHeader((4u << 16) | 0x7FF, 0x3000, 0x5003, &first) == 0);  // past the end
}

}  // namespace

int main() {
  Segment();
  Offsets();
  Columns();
  Packets();
  std::puts("vr stereo: ok");
  return 0;
}
