#include "../src/platform/ultrawide_math.h"
#include <cassert>
#include <cmath>
#include <utility>
#include <limits>
using namespace forza::ultrawide;
static bool Near(double a, double b) { return std::abs(a-b) < 1e-6; }
int main() {
  assert(NativeSizeSupported(3440, 1440));
  assert(NativeSizeSupported(5120, 1440));
  assert(NativeSizeSupported(1720, 720));
  assert(NativeSizeSupported(5120, 2160));
  assert(NativeSizeSupported(2560, 1080));
  assert(NativeSizeSupported(1920, 1080));
  assert(NativeSizeSupported(3440, 1439));
  assert(!NativeSizeSupported(0, 1440));
  assert(!NativeSizeSupported(3440, 0));
  assert(!NativeSizeSupported(1280 * 7 + 1, 1440));
  assert(!NativeSizeSupported(3440, 720 * 8));
  for (uint32_t height = 1; height <= 5040; ++height) {
    const auto drawing = DrawingHeight(height);
    assert(drawing >= height && drawing - height < 720);
    assert(drawing % 720 == 0 && drawing <= 5040);
    assert(RenderScale(height, 720, 1) * 720 == drawing);
  }
  assert(DrawingHeight(1080) == 1440);
  assert(DrawingHeight(1440) == 1440);
  assert(DrawingHeight(2160) == 2160);
  // Presentation scales the whole backing, not a crop. A square HUD widget
  // remains square after the unequal drawing-to-window scale at 1080 lines.
  for (const auto dimensions : {std::pair{2560u,1080u}, std::pair{3840u,1080u},
                                std::pair{3440u,1440u}}) {
    const auto layout = ComputeLayout(dimensions.first, dimensions.second, 100, 100, 0, 0);
    const double drawn_width = 100.0 / 1280 * layout.hud_scale_x * dimensions.first;
    const double drawn_height = 100.0 / 720 * layout.hud_scale_y * DrawingHeight(dimensions.second);
    assert(Near(drawn_width, drawn_height * dimensions.second / DrawingHeight(dimensions.second)));
  }
  auto native = ComputeLayout(2560,1440,100,100,0,0);
  assert(Near(native.aspect_scale,1) && Near(native.hud_scale_x,1));
  auto wide = ComputeLayout(3440,1440,100,100,0,0);
  assert(Near(wide.aspect_scale,3440.0/2560));
  // A centered HUD spans the original 2560 pixels, with circular gauges intact.
  assert(Near(wide.hud_scale_x*3440,2560));
  for (const auto dimensions : {std::pair{2560u,1440u}, std::pair{3440u,1440u},
                                std::pair{5120u,1440u}}) {
    const auto layout = ComputeLayout(dimensions.first,dimensions.second,90,95,5,4);
    // A square guest widget must be square in final output pixels.
    assert(Near(100.0/1280*layout.hud_scale_x*dimensions.first,
                100.0/720*layout.hud_scale_y*dimensions.second));
  }
  auto edges = ComputeLayout(3440,1440,100,100,0,0,true);
  // The original 16:9 canvas extends 440 native pixels toward each side.
  assert(Near(edges.hud_edge_shift*3440/2,440));
  assert(Near(edges.hud_scale_x,wide.hud_scale_x));
  assert(Near(edges.hud_scale_y,wide.hud_scale_y));
  auto edge_clamped = ComputeLayout(3440,1440,100,100,20,20,true);
  assert(Near(edge_clamped.hud_offset_x,0) && Near(edge_clamped.hud_offset_y,0));
  auto edge_safe = ComputeLayout(3440,1440,90,90,20,0,true);
  assert(Near(edge_safe.hud_offset_x,.1));
  auto native_edges = ComputeLayout(2560,1440,100,100,0,0,true);
  assert(Near(native_edges.hud_edge_shift,0));
  auto safe = ComputeLayout(3440,1440,90,95,5,4);
  assert(Near(safe.hud_scale_y,.9) && Near(safe.hud_scale_x*3440,2304));
  assert(Near(safe.hud_offset_x,.1) && Near(safe.hud_offset_y,.08));
  auto clamped = ComputeLayout(5120,1440,50,50,1000,-1000);
  assert(clamped.hud_scale_x + clamped.hud_offset_x <= 1.0);
  assert(clamped.hud_scale_y - clamped.hud_offset_y <= 1.0);
  auto invalid = ComputeLayout(3440,1440,std::numeric_limits<double>::quiet_NaN(),
      std::numeric_limits<double>::infinity(),std::numeric_limits<double>::infinity(),
      std::numeric_limits<double>::quiet_NaN());
  assert(Near(invalid.hud_scale_x,wide.hud_scale_x));
  assert(Near(invalid.hud_offset_x,0) && Near(invalid.hud_offset_y,0));
  auto empty = ComputeLayout(0,0,100,100,0,0);
  assert(Near(empty.aspect_scale,1));
  assert(RenderScale(3440,1280,2)==3 && RenderScale(1440,720,2)==2);
  assert(RenderScale(5120,1280,2)==4 && RenderScale(3840,1280,4)==4);
  assert(RenderScale(16384,1280,1)==7);
}
