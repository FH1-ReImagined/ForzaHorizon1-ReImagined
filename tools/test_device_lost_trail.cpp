// Which draw the GPU was on when the device was lost (ReIGlue 0438, #551), and what was
// recorded for it before the draw command (ReIGlue 1100, #913):
// src/graphics/vulkan/fh1_device_lost_trail.h of the patched SDK source. Build with the patched
// SDK source's src/graphics/vulkan on the include path:
//   c++ -std=c++17 -I ~/rexglue-sdk-src-forza/sdk/src/graphics/vulkan
//       tools/test_device_lost_trail.cpp -o test_device_lost_trail
#include "fh1_device_lost_trail.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace t = rex::graphics::vulkan::fh1_device_lost_trail;

#define CHECK(x)                                                    \
  do {                                                              \
    if (!(x)) {                                                     \
      std::fprintf(stderr, "device lost trail: FAIL: %s\n", #x);   \
      std::exit(1);                                                 \
    }                                                               \
  } while (false)

static bool Has(const std::string& s, const char* part) { return s.find(part) != std::string::npos; }

int main() {
  static t::Trail trail;  // 16384 entries: not on the stack
  // Submission 7: three draws; submission 8: a resolve and two draws.
  const uint32_t a = trail.Record(t::Kind::kDraw, 7, 100, 36, 0x1E6883FCCDE1F688ull, 0xA4A965C189287B99ull);
  const uint32_t b = trail.Record(t::Kind::kDraw, 7, 100, 6, 0x2C53E1A563484076ull, 0);
  const uint32_t c = trail.Record(t::Kind::kDraw, 7, 100, 3, 0x1111ull, 0x2222ull);
  const uint32_t d = trail.Record(t::Kind::kResolve, 8, 100, 0, 0, 0);
  const uint32_t e = trail.Record(t::Kind::kDraw, 8, 100, 99, 0x3333ull, 0x4444ull);
  trail.Record(t::Kind::kDraw, 8, 100, 12, 0x5555ull, 0x6666ull);
  CHECK(a == 1 && b == 2 && c == 3 && d == 4 && e == 5);
  CHECK(trail.last_recorded() == 6);
  CHECK(trail.Find(0) == nullptr);
  CHECK(trail.Find(2)->vertex_shader == 0x2C53E1A563484076ull);
  CHECK(trail.FindSubmission(7)->first == 1 && trail.FindSubmission(7)->last == 3);
  CHECK(trail.FindSubmission(8)->entries == 3);
  // The index counts the submission's draws and resolves as recorded (1-based).
  CHECK(trail.Find(4)->index_in_submission == 1 && trail.Find(6)->index_in_submission == 3);
  CHECK(trail.FindSubmission(9) == nullptr);

  // The GPU issued draw 5 and finished the resolve (4): stuck on draw 5.
  std::string r = t::Describe(trail, "VK_AMD_buffer_marker", 5, 4);
  std::printf("%s\n", r.c_str());
  CHECK(Has(r, "GPU breadcrumbs (VK_AMD_buffer_marker): last finished #4 (resolve 1 of 3 in submission 8"));
  CHECK(Has(r, "unfinished: #5 (draw 2 of 3 in submission 8, frame 100, VS 0000000000003333, PS 0000000000004444, 99 vertices)"));
  CHECK(Has(r, "(1 issued but not finished)"));
  CHECK(Has(r, "submission 8 holds #4-#6"));
  CHECK(Has(r, "6 recorded in all"));

  // A depth-only draw names no pixel shader.
  r = t::Describe(trail, "VK_NV_device_diagnostic_checkpoints", 0, 1);
  CHECK(Has(r, "unfinished: #2 (draw 2 of 3 in submission 7, frame 100, VS 2C53E1A563484076, PS none, 6 vertices)"));
  CHECK(!Has(r, "last issued"));

  // Everything finished: the loss came after the guest's draws.
  r = t::Describe(trail, "VK_AMD_buffer_marker", 6, 6);
  CHECK(Has(r, "every recorded draw finished"));

  // Nothing written yet.
  r = t::Describe(trail, "VK_AMD_buffer_marker", 0, 0);
  CHECK(Has(r, "last finished none") && Has(r, "unfinished: #1 (draw 1 of 3 in submission 7"));

  // Old numbers fall out of the trail once it wrapped.
  for (uint32_t i = 0; i < t::Trail::kEntries; ++i) {
    trail.Record(t::Kind::kDraw, 100 + i / 1000, 200, 3, 1, 2);
  }
  CHECK(trail.Find(3) == nullptr);
  r = t::Describe(trail, "VK_AMD_buffer_marker", 0, 3);
  CHECK(Has(r, "last finished #3 (no longer in the trail)"));
  const uint32_t last = trail.last_recorded();
  CHECK(trail.Find(last) && trail.Find(last)->submission == 100 + (t::Trail::kEntries - 1) / 1000);

  // ReIGlue 1100 (#913): the unfinished draw's modifications, render pass and the work recorded
  // before its draw command, and whether the GPU finished that work.
  {
    static t::Trail d;
    t::Details car;
    car.vertex_shader_modification = 0x000000400000003Full;
    car.pixel_shader_modification = 0x000400000016003Full;
    // 1x, depth D24FS8 and color 0 used (0b00011 << 2), depth format bit 7, color 0 format 3.
    car.render_pass_key = (3u << 2) | (1u << 7) | (3u << 8);
    car.filled = true;
    const uint32_t f = d.Record(t::Kind::kDraw, 9, 300, 14, 0x29DFull, 0x147Bull, car);
    t::Details with_work = car;
    with_work.work.render_passes = 1;
    with_work.work.draws = 3;
    with_work.work.barriers = 2;
    with_work.setup_marked = true;
    CHECK(d.next() == f + 1);
    const uint32_t u = d.Record(t::Kind::kDraw, 9, 300, 5065, 0x8228ull, 0x66A4ull, with_work);
    r = t::DescribeDetails(d, f, 0);
    std::printf("%s\n", r.c_str());
    CHECK(Has(r, "GPU breadcrumbs, unfinished #2 (draw): VS modification 000000400000003F, "
                 "PS modification 000400000016003F, render pass 0x0000038C (1x, depth D24FS8, "
                 "color 0 2_10_10_10_FLOAT)"));
    CHECK(Has(r, "recorded before the draw command: 1 render pass, 3 draws, 2 barriers"));
    CHECK(Has(r, "did not finish that work"));
    CHECK(!Has(r, "last finished in"));  // the same render pass
    r = t::DescribeDetails(d, f, u);
    CHECK(Has(r, "the GPU finished that work: it hung in the draw itself"));
    // Nothing recorded between the two: the draw itself.
    t::Details bare = car;
    bare.render_pass_key = 2;  // 4x, no attachments
    const uint32_t v = d.Record(t::Kind::kDraw, 9, 300, 3, 1, 0, bare);
    r = t::DescribeDetails(d, u, 0);
    CHECK(Has(r, "render pass 0x00000002 (4x, no attachments)"));
    CHECK(Has(r, "nothing else was recorded after #2: it hung in the draw itself"));
    CHECK(Has(r, "last finished in render pass 0x0000038C"));
    // A resolve names its own work; nothing when every draw finished or for entries without
    // details (the older tests' entries above).
    t::Details resolve;
    resolve.work.dispatches = 1;
    resolve.filled = true;
    d.Record(t::Kind::kResolve, 9, 300, 0, 0, 0, resolve);
    r = t::DescribeDetails(d, v, 0);
    CHECK(Has(r, "unfinished #4 (resolve): recorded with it: 1 dispatch"));
    CHECK(t::DescribeDetails(d, d.last_recorded(), 0).empty());
    CHECK(t::DescribeDetails(trail, 0, 0).empty());
  }

  std::printf("device lost trail: OK\n");
  return 0;
}
