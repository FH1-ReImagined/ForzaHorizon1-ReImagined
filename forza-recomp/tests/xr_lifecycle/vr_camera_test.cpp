// Actual camera hook: flat/menu/session-loss transitions, without guest code or a headset.
#include "../../src/hooks/vr_head_pose.cpp"
#include <cstdio>
#include <vector>
bool vr_cull_scale = true;
bool forza_minimap_camera = false;
bool active = false;
float exposure = 1;
int exposure_writes = 0;
namespace forza::xr {
bool CockpitActive() { return active; }
bool Tracing() { return false; }
bool CockpitView(vr::HeadTransform&, float& fov, float& aspect, uint32_t& tag) {
  fov = 1.9f; aspect = 1.2f; tag = 1; return true;
}
void RememberPosedView(uint32_t, const uint8_t*) {}
void OnRenderView(uint32_t, const uint8_t*) {}
void NoteView(uint32_t, const char*, float, bool) {}
void Recenter() {}
void SetCockpitParallaxTest(int) {}
}
namespace forza::world_detail {
void ScaleExposureAdaptation(uint8_t*, float scale) { exposure = scale; ++exposure_writes; }
}
extern "C" {
REX_FUNC(__imp__sub_82853DD0) {}
REX_FUNC(__imp__sub_8284EF98) {}
REX_FUNC(__imp__sub_825A9D20) {}
REX_FUNC(__imp__sub_829A4860) {}
REX_FUNC(__imp__sub_823EB7A0) {}
}
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "camera FAIL line %d: %s (exposure=%g, cull=%g)\n", __LINE__, #x, exposure, cull_scale.load()); return 1; } } while (false)
void Store32(uint8_t* p, uint32_t value) {
  p[0] = uint8_t(value >> 24); p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8); p[3] = uint8_t(value);
}
int main() {
  std::vector<uint8_t> memory(0x120000);
  auto* base = memory.data();
  constexpr uint32_t director = 0x100000, camera = 0x110000;
  Store32(base + director + kActiveCamera, camera);
  Store32(base + camera, FORZA_ADDR(0x82075DD4));
  PutFloat(base + director + kFov, 1.0821f);
  { PosedView view(base, director, "view"); }
  CHECK(exposure_writes == 0 && cull_scale == 1);
  // Cockpit -> menu/lost session -> cockpit again. Frustum calls must not clear the pose.
  for (int transition = 0; transition < 2; ++transition) {
    active = true;
    { PosedView view(base, director, "view"); }
    CHECK(exposure == float(vr_exposure_speed) && cull_scale < 1);
    { PosedView view(base, director, "frustum"); }
    CHECK(exposure == float(vr_exposure_speed) && cull_scale < 1);
    active = false;
    // The frustum may be the first hook after a lost session, without a valid director.
    { PosedView view(base, transition ? 0 : director, transition ? "frustum" : "view"); }
    CHECK(exposure == 1 && cull_scale == 1);
    CHECK(!xr_view.valid);
    const int writes = exposure_writes;
    { PosedView view(base, director, "view"); }
    CHECK(exposure_writes == writes);
  }
  // A different camera within a running session restores the normal track rate too.
  active = true;
  { PosedView view(base, director, "view"); }
  Store32(base + camera, 0);
  { PosedView view(base, director, "view"); }
  CHECK(exposure == 1 && cull_scale == 1);
  std::puts("camera transition cases pass");
}
