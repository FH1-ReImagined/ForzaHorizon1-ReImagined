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
    const uint32_t f = d.Record(t::Kind::kDraw, 9, 300, 14, 0x29DFull, 0x147Bull);
    d.SetDetails(f, car);
    t::Details with_work = car;
    with_work.work.render_passes = 1;
    with_work.work.render_pass_ends = 1;
    with_work.work.draws = 3;
    with_work.work.barriers = 2;
    with_work.work.uploads = 1;
    with_work.setup_marked = true;
    CHECK(d.next() == f + 1);
    const uint32_t u = d.Record(t::Kind::kDraw, 9, 300, 5065, 0x8228ull, 0x66A4ull);
    CHECK(u == f + 1);
    d.SetDetails(u, with_work);
    r = t::DescribeDetails(d, f, 0, true, true);
    std::printf("%s\n", r.c_str());
    CHECK(Has(r, "GPU breadcrumbs, unfinished #2 (draw): VS modification 000000400000003F, "
                 "PS modification 000400000016003F, render pass 0x0000038C (1x, depth D24FS8, "
                 "color 0 2_10_10_10_FLOAT)"));
    CHECK(Has(r, "recorded since #1, before the draw command: 1 render pass, 1 render pass end, "
                 "3 draws, 2 barriers, 1 hoisted upload point"));
    // The marker before #2 still holds an older draw's number: the work was not finished.
    CHECK(Has(r, "the GPU did not finish that work: it hung in it (or in the draw, if the draw "
                 "ran alongside it)"));
    CHECK(!Has(r, "submission"));  // #1 and #2 are in the same one
    CHECK(!Has(r, "last finished in"));  // the same render pass
    r = t::DescribeDetails(d, f, f, true, true);
    CHECK(Has(r, "did not finish that work"));
    r = t::DescribeDetails(d, f, u, true, true);
    std::printf("%s\n", r.c_str());
    CHECK(Has(r, "the GPU finished that work: it hung in the draw itself"));
    // Without gpu_device_lost_setup_breadcrumbs (the default) nothing of this is recorded: the
    // line names the setting.
    r = t::DescribeDetails(d, f, u, false, true);
    std::printf("%s\n", r.c_str());
    CHECK(Has(r, "GPU breadcrumbs, unfinished #2 (draw): whether the GPU hung in it or in the work "
                 "recorded since #1 (a render pass"));
    CHECK(Has(r, "is logged with gpu_device_lost_setup_breadcrumbs"));
    CHECK(!Has(r, "modification") && !Has(r, "hung in the draw itself"));
    // VK_NV_device_diagnostic_checkpoints: no marker between the work and the draw, and the
    // checkpoints do not say exactly what finished: no verdict, also with nothing in between.
    {
      static t::Trail o;
      const uint32_t of = o.Record(t::Kind::kDraw, 9, 300, 14, 0x29DFull, 0x147Bull);
      o.SetDetails(of, car);
      t::Details unmarked = with_work;
      unmarked.setup_marked = false;
      o.SetDetails(o.Record(t::Kind::kDraw, 9, 300, 5065, 0x8228ull, 0x66A4ull), unmarked);
      r = t::DescribeDetails(o, of, 0, true, false);
      CHECK(Has(r, "1 hoisted upload point; in that work or in the draw (no verdict with "
                   "VK_NV_device_diagnostic_checkpoints)"));
      CHECK(!Has(r, "finish that work") && !Has(r, "hung"));
      t::Details nothing = car;
      o.SetDetails(o.Record(t::Kind::kDraw, 9, 300, 3, 1, 2), nothing);
      r = t::DescribeDetails(o, of + 1, 0, true, false);
      CHECK(Has(r, "before the draw command: nothing; in that work or in the draw (no verdict"));
    }
    // #N and #N+1 in different submissions: the presenter's work on the queue between them is
    // not counted, so "nothing else was recorded" and "did not finish that work" are not said.
    {
      static t::Trail x;
      const uint32_t x1 = x.Record(t::Kind::kDraw, 20, 400, 3, 1, 2);
      x.SetDetails(x1, car);
      const uint32_t x2 = x.Record(t::Kind::kDraw, 21, 401, 3, 1, 2);
      x.SetDetails(x2, car);  // nothing recorded, not marked
      r = t::DescribeDetails(x, x1, 0, true, true);
      std::printf("%s\n", r.c_str());
      CHECK(Has(r, "#1 is in submission 20, #2 in submission 21"));
      CHECK(Has(r, "in the draw or in the other work on the queue between the two submissions"));
      CHECK(!Has(r, "hung in the draw itself"));
      const uint32_t x3 = x.Record(t::Kind::kDraw, 22, 402, 3, 1, 2);
      x.SetDetails(x3, with_work);
      r = t::DescribeDetails(x, x2, x1, true, true);
      CHECK(Has(r, "did not finish that work or the other work on the queue between the two "
                   "submissions"));
      // The marker before the draw proves that everything before it on the queue finished.
      r = t::DescribeDetails(x, x2, x3, true, true);
      CHECK(Has(r, "the GPU finished that work: it hung in the draw itself"));
      // Nothing finished yet: "since the start of the trail", and no verdict from nothing.
      r = t::DescribeDetails(x, 0, 0, true, true);
      CHECK(Has(r, "unfinished #1 (draw)") && Has(r, "recorded since the start of the trail"));
      CHECK(Has(r, "the last finished entry is no longer in the trail"));
      CHECK(!Has(r, "hung in the draw itself"));
      r = t::DescribeDetails(x, 0, 0, false, true);
      CHECK(Has(r, "in the work recorded since the start of the trail"));
    }
    // Nothing recorded between the two: the draw itself.
    t::Details bare = car;
    bare.render_pass_key = 2;  // 4x, no attachments
    const uint32_t v = d.Record(t::Kind::kDraw, 9, 300, 3, 1, 0);
    d.SetDetails(v, bare);
    r = t::DescribeDetails(d, u, 0, true, true);
    CHECK(Has(r, "render pass 0x00000002 (4x, no attachments)"));
    CHECK(Has(r, "recorded since #2, before the draw command: nothing; nothing else was recorded "
                 "since #2: it hung in the draw itself"));
    CHECK(Has(r, "last finished in render pass 0x0000038C"));
    // Several color targets, D24S8 and the sample count the key cannot hold.
    CHECK(t::DescribeRenderPass(3u | (0x1Fu << 2) | (0u << 7) | (7u << 8) | (6u << 12) |
                                (15u << 16) | (2u << 20)) ==
          "render pass 0x002F677F (?x, depth D24S8, color 0 16_16_16_16_FLOAT, color 1 16_16_FLOAT, "
          "color 2 32_32_FLOAT, color 3 2_10_10_10)");
    // The test hook's wait (FORZA_TEST_GPU_HANG_AT_FRAME) is work before the next draw.
    t::Details hang = car;
    hang.work.render_pass_ends = 1;
    hang.work.test_hangs = 1;
    hang.work.render_passes = 1;
    hang.setup_marked = true;
    const uint32_t h = d.Record(t::Kind::kDraw, 9, 300, 3, 1, 2);
    d.SetDetails(h, hang);
    r = t::DescribeDetails(d, v, u, true, true);
    CHECK(Has(r, "unfinished #4 (draw)") && Has(r, "1 test hang") &&
          Has(r, "did not finish that work"));
    // A resolve names its own work and no verdict.
    t::Details resolve;
    resolve.work.dispatches = 1;
    resolve.work.upscales = 2;
    const uint32_t s = d.Record(t::Kind::kResolve, 9, 300, 0, 0, 0);
    d.SetDetails(s, resolve);
    r = t::DescribeDetails(d, h, 0, true, true);
    CHECK(Has(r, "unfinished #5 (resolve): recorded since #4, with it: 1 dispatch, 2 upscaling "
                 "passes"));
    CHECK(!Has(r, "hung") && !Has(r, "last finished in"));  // no render pass for a resolve
    r = t::DescribeDetails(d, h, 0, false, true);
    CHECK(Has(r, "unfinished #5 (resolve): whether the GPU hung in it"));
    // Nothing when every draw finished, for entries without details, or for details of a number
    // that the trail no longer holds.
    CHECK(t::DescribeDetails(d, d.last_recorded(), 0, true, true).empty());
    CHECK(t::DescribeDetails(d, d.last_recorded(), 0, false, true).empty());
    CHECK(t::DescribeDetails(trail, 0, 0, true, true).empty());
    static t::Trail w;
    const uint32_t w1 = w.Record(t::Kind::kDraw, 1, 1, 3, 1, 2);
    w.SetDetails(w1, car);
    for (uint32_t i = 0; i < t::Trail::kEntries; ++i) {
      w.Record(t::Kind::kDraw, 2, 2, 3, 1, 2);
    }
    CHECK(w.FindDetails(w1) == nullptr);
    CHECK(w.FindDetails(w1 + t::Trail::kEntries) == nullptr);  // that slot's details are w1's
    CHECK(t::DescribeDetails(w, w1 + t::Trail::kEntries - 1, 0, true, true).empty());
  }

  std::printf("device lost trail: OK\n");
  return 0;
}
