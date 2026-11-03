#include "../src/platform/minimap_heading.h"
#include "../src/platform/vr_head_pose_math.h"
#include <cstdlib>
#include <limits>
#include <thread>

namespace {
void Check(bool ok) { if (!ok) std::abort(); }
bool Near(float a, float b) { return std::fabs(a - b) < 0.0001f; }
std::array<float, 16> Identity() {
  return {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 12, 4, -30, 1};
}
}
int main() {
  using namespace forza::minimap;
  auto view = Identity();
  auto h = CameraHeading(view);
  Check(h && Near(h->x, 0) && Near(h->z, 1));
  // Native look-around, including the -180/+180 boundary, not car velocity.
  for (float yaw : {-180.f, -179.f, -90.f, 0.f, 90.f, 179.f, 180.f}) {
    view = Identity();
    forza::vr::HeadPose pose; pose.yaw = yaw; pose.pitch = 30; pose.roll = 15;
    forza::vr::ApplyHeadPose(view.data(), pose);
    h = CameraHeading(view);
    Check(h && Near(std::hypot(h->x, h->z), 1));
    constexpr float radians = 3.14159265358979323846f / 180;
    Check(Near(h->x, -std::sin(yaw * radians)) && Near(h->z, std::cos(yaw * radians)));
    auto shifted = view;
    shifted[12] += 10000; shifted[13] -= 500; shifted[14] -= 9000;
    const auto other = CameraHeading(shifted);
    Check(other && Near(other->x, h->x) && Near(other->z, h->z));
  }
  view = Identity(); view[2] = 0; view[10] = 0;
  Check(!CameraHeading(view)); // Looking vertically has no horizontal heading.
  view = Identity(); view[6] = std::numeric_limits<float>::quiet_NaN();
  Check(!CameraHeading(view));
  view = Identity(); view[2] = std::numeric_limits<float>::infinity();
  Check(!CameraHeading(view));

  HeadingMailbox box;
  Check(!box.Read(7, 100));
  box.Publish(Identity(), 7, 100);
  Check(box.Read(7, 100) && box.Read(7, 350));
  Check(!box.Read(7, 351) && !box.Read(8, 100) && !box.Read(7, 99));
  box.Publish(view, 7, 101);
  Check(!box.Read(7, 101));

  auto east = Identity(); east[2] = 1; east[10] = 0;
  std::atomic<bool> done{false};
  std::thread writer([&] {
    for (unsigned i = 0; i < 100000; ++i) {
      box.Publish(Identity(), 7, 200);
      box.Publish(east, 8, 200);
    }
    done.store(true);
  });
  while (!done.load()) {
    if (const auto a = box.Read(7, 200)) Check(Near(a->x, 0) && Near(a->z, 1));
    if (const auto b = box.Read(8, 200)) Check(Near(b->x, 1) && Near(b->z, 0));
  }
  writer.join();
}
