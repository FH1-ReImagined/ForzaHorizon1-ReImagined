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

// A model of the HUD map camera (82DA2388): tilted by `pitch` from looking straight down
// (heading at the top of the map), pulled back `height` along its view axis from the ground
// point, so the ground point is at the centre of the map.
struct MapCamera { float pitch, fov, aspect, height; };
struct V3 { float x, y, z; };
struct Screen { float x, y; };
V3 Mad(V3 a, V3 b, float s) { return {a.x + s * b.x, a.y + s * b.y, a.z + s * b.z}; }
float Dot(V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
struct Basis { V3 right, up, view, eye; };
Basis Frame(const MapCamera& c, forza::minimap::Ground pivot, forza::minimap::Heading heading) {
  const V3 forward{heading.x, 0, heading.z}, right{heading.z, 0, -heading.x};
  const float s = std::sin(c.pitch), k = std::cos(c.pitch);
  const V3 view{s * forward.x, -k, s * forward.z}, up{k * forward.x, s, k * forward.z};
  return {right, up, view, Mad({pivot.x, 0, pivot.z}, view, -c.height)};
}
Screen Project(const MapCamera& c, const Basis& b, V3 point) {
  const V3 d = Mad(point, b.eye, -1);
  const float t = std::tan(c.fov / 2), z = Dot(d, b.view);
  return {Dot(d, b.right) / (z * t * c.aspect), Dot(d, b.up) / (z * t)};
}
// 82DA0FE8: the ground point under a screen point.
V3 Unproject(const MapCamera& c, const Basis& b, Screen at) {
  const float t = std::tan(c.fov / 2);
  const V3 ray = Mad(Mad(b.view, b.right, at.x * t * c.aspect), b.up, at.y * t);
  return Mad(b.eye, ray, -b.eye.y / ray.y);
}
bool Lands(Screen drawn, Screen at) { return std::hypot(drawn.x - at.x, drawn.y - at.y) < 0.001f; }
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

  // #816: the map turned with the camera keeps the car where the stock map draws it.
  Check(!Flat(0, 0) && !Flat(std::numeric_limits<float>::quiet_NaN(), 1));
  Check(!MeasureLookAhead({std::numeric_limits<float>::infinity(), 0}, {0, 0}, {0, 1}));
  // In-game TU4 values first: 70 degree pitch, 55 degree fov, 500 m, car 0.3 below the centre.
  // The map's aspect follows the HUD rectangle (scale, 16:9, ultrawide); fov/pitch/height
  // follow speed.
  for (const MapCamera camera : {MapCamera{1.222f, 0.960f, 1.0f, 500}, MapCamera{1.222f, 0.960f, 16.f / 9, 500},
                                 MapCamera{1.222f, 0.960f, 21.f / 9, 500}, MapCamera{1.222f, 0.960f, 32.f / 9, 500},
                                 MapCamera{0.9f, 0.7f, 1.4f, 250}, MapCamera{0.f, 1.2f, 1.0f, 800}}) {
    for (const Screen spot : {Screen{0, -0.3f}, Screen{0.2f, -0.45f}}) {
      for (const Heading car_heading : {Heading{0.985f, -0.175f}, Heading{0, 1}, Heading{-1, 0}}) {
        const Ground car{87.68f, -3921.24f};
        const V3 car3{car.x, 0, car.z};
        // 82584FC0: build the camera on the car, find the ground under the car's screen
        // spot and move the ground point by the difference, so the car lands on that spot.
        const auto native = *Flat(car_heading.x, car_heading.z);
        const auto built = Frame(camera, car, native);
        const auto under = Unproject(camera, built, spot);
        const Ground pivot{2 * car.x - under.x, 2 * car.z - under.z};
        Check(Lands(Project(camera, Frame(camera, pivot, native), car3), spot));
        // The car read back from the rows of the camera built on it (82DA2388 world matrix).
        const std::array<float, 16> world{built.right.x, built.right.y, built.right.z, 0,
                                          built.up.x, built.up.y, built.up.z, 0,
                                          built.view.x, built.view.y, built.view.z, 0,
                                          built.eye.x, built.eye.y, built.eye.z, 1};
        const auto read = CarFromMapCamera(world, camera.height);
        Check(read && std::hypot(read->x - car.x, read->z - car.z) < 0.01f);
        const auto offset = MeasureLookAhead(pivot, *read, native);
        Check(offset && (spot.x != 0 || std::fabs(offset->right) < 0.01f));
        const auto same = TurnPivot(pivot, native, native, *offset);
        Check(std::hypot(same.x - pivot.x, same.z - pivot.z) < 0.01f);
        for (int degrees = -180; degrees <= 180; degrees += 15) {
          const float a = float(degrees) * 3.14159265358979323846f / 180;
          const Heading look{native.x * std::cos(a) - native.z * std::sin(a),
                             native.x * std::sin(a) + native.z * std::cos(a)};
          const auto turned = TurnPivot(pivot, native, look, *offset);
          Check(Lands(Project(camera, Frame(camera, turned, look), car3), spot));
          // The bug: turning only the heading swings the car around the old ground point.
          const auto old = Project(camera, Frame(camera, pivot, look), car3);
          if (std::abs(degrees) >= 90 && camera.pitch > 0) Check(!Lands(old, spot));
        }
      }
    }
  }
  LookAheadMailbox mailbox;
  Check(!mailbox.Read(0x1000));
  mailbox.Publish(0x1000, {159.76f, 0.5f});
  const auto read = mailbox.Read(0x1000);
  Check(read && read->forward == 159.76f && read->right == 0.5f);
  Check(!mailbox.Read(0x2000) && !mailbox.Read(0));
}
