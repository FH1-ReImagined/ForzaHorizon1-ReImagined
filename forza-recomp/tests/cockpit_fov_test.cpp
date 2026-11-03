#include "../src/platform/camera_fov.h"
#include <cassert>
#include <limits>

using namespace forza::camera;

namespace {
void Write32(uint8_t* p, uint32_t bits) {
  p[0] = uint8_t(bits >> 24); p[1] = uint8_t(bits >> 16);
  p[2] = uint8_t(bits >> 8); p[3] = uint8_t(bits);
}
void WriteFloat(uint8_t* p, float value) {
  uint32_t bits;
  std::memcpy(&bits, &value, 4);
  Write32(p, bits);
}
void Near(float a, float b) { assert(std::fabs(a - b) < 2e-6f); }

struct Guest {
  std::array<uint8_t, 0x8000> bytes{};
  Vtables tables;
  uint32_t next = 0x100, config = 0x6000;
  mutable unsigned reads = 0;
  const uint8_t* operator()(uint32_t address, size_t count) const {
    ++reads;
    if (address >= bytes.size() || count > bytes.size() - address) return nullptr;
    return bytes.data() + address;
  }
  void Word(uint32_t address, uint32_t value) { Write32(bytes.data() + address, value); }
  void Float(uint32_t address, float value) { WriteFloat(bytes.data() + address, value); }
  uint32_t Leaf(Kind kind, float degrees, bool split = false) {
    const auto address = next;
    next += 0x200;
    uint32_t table = 0xDEADBEEF;
    switch (kind) {
      case Kind::kChaseNear: table = tables.chase_near; break;
      case Kind::kChaseFar: table = tables.chase_far; break;
      case Kind::kCockpit: table = tables.cockpit; break;
      case Kind::kHood: table = tables.hood; break;
      case Kind::kBumper: table = tables.bumper; break;
      case Kind::kBlend: table = tables.blend; break;
      default: break;
    }
    Word(address, table);
    if (kind == Kind::kChaseNear || kind == Kind::kChaseFar) {
      Word(address + 192, config);
      Float(config + 28, degrees);
      config += 0x40;
      bytes[address + 196] = split;
    } else {
      Float(address + 272, Radians(degrees));
      bytes[address + 304] = split;
    }
    return address;
  }
  uint32_t Blend(uint32_t from, uint32_t to, float t) {
    const auto address = Leaf(Kind::kBlend, 0);
    Word(address + 192, from); Word(address + 196, to); Float(address + 200, t);
    return address;
  }
  float Apply(float original, uint32_t camera, Settings settings,
              bool live = true, bool vr = false) const {
    return AdjustedFov(original, camera, settings,
        [&](uint32_t address) { return ReadNode(address, *this, tables); }, live, vr);
  }
};

void Check(const Vtables& tables) {
  Guest guest{{}, tables};
  const auto near = guest.Leaf(Kind::kChaseNear, 48.5f);
  const auto far = guest.Leaf(Kind::kChaseFar, 58);
  const auto cockpit = guest.Leaf(Kind::kCockpit, 62);
  const auto hood = guest.Leaf(Kind::kHood, 66);
  const auto bumper = guest.Leaf(Kind::kBumper, 53);
  const auto excluded = guest.Leaf(Kind::kOther, 0);
  const auto before = guest.bytes;
  const Settings selected{80, 80, 80, 80, 80};
  // Source-audited TU4 call sites: presentation twice, game frustum, world,
  // split-view, car/post effects, and the other camera setup. Every reader sees
  // the same finished value; the stored input is never changed or accumulated.
  const std::array<uint32_t, 7> callers{0x82621F9C, 0x82622320, 0x82A58E28,
      0x824135B0, 0x8262260C, 0x825156B0, 0x824C5084};
  for (const auto caller : callers) {
    assert(caller);
    Near(guest.Apply(Radians(70), cockpit, selected), Radians(88)); // 62 + speed 8; base 80 + 8.
    assert(guest.bytes == before);
  }
  const std::array<uint32_t, 5> cameras{near, far, cockpit, hood, bumper};
  const std::array<float, 5> bases{48.5f, 58, 62, 66, 53};
  const std::array<float, 5> widening{22, 22, 8, 10, 23};
  for (size_t i = 0; i < cameras.size(); ++i) {
    for (int degrees = 40; degrees <= 90; ++degrees) {
      const Settings choice{degrees, degrees, degrees, degrees, degrees};
      Near(guest.Apply(Radians(bases[i] + widening[i]), cameras[i], choice),
           Radians(float(degrees) + widening[i]));
    }
    const float original = Radians(bases[i] + widening[i]);
    Near(guest.Apply(original, cameras[i], {}), original);
    for (int invalid : {-1, 1, 39, 91, 999})
      Near(guest.Apply(original, cameras[i], {invalid, invalid, invalid, invalid, invalid}), original);
  }
  // A cockpit preference leaves other cameras alone, including all unknown
  // vtables (photo/free/menu/rear-view/wheel), regardless of shared getter code.
  for (const auto camera : {near, far, hood, bumper, excluded})
    Near(guest.Apply(Radians(58), camera, {0, 0, 80}), Radians(58));
  guest.reads = 0;
  Near(guest.Apply(Radians(62), cockpit, {}), Radians(62));
  assert(guest.reads == 0);
  Near(guest.Apply(Radians(70), cockpit, selected, false), Radians(70));
  Near(guest.Apply(Radians(70), cockpit, selected, true, true), Radians(70));
  assert(guest.reads == 0);

  // Full transition sweeps in both directions: no jump when the active class
  // becomes or ceases to be Driver. Keep the game's additional +3-degree effect.
  const auto blend = guest.Blend(far, cockpit, 0);
  for (int step = 0; step <= 100; ++step) {
    const float t = float(step) / 100;
    guest.Float(blend + 200, t);
    const float stock = std::fma(62.0f - 58.0f, t, 58.0f) + 3;
    Near(guest.Apply(Radians(stock), blend, {0, 0, 80}), Radians(61 + 22 * t));
    guest.Word(blend + 192, cockpit); guest.Word(blend + 196, far);
    const float reverse = std::fma(58.0f - 62.0f, t, 62.0f) + 3;
    Near(guest.Apply(Radians(reverse), blend, {0, 0, 80}), Radians(83 - 22 * t));
    guest.Word(blend + 192, far); guest.Word(blend + 196, cockpit);
  }
  guest.Float(blend + 200, 0.25f);
  const auto nested = guest.Blend(far, blend, 0.5f);
  Near(guest.Apply(Radians(60), nested, {0, 70, 80}), Radians(72.75f));
  // Inner Far->Driver adjustment at .25 is 13.5; outer Far->inner at .5 is 12.75.

  // The same half-FOV rule as each class getter; CameraPhysics is still added
  // independently by the game after that getter.
  const auto split_driver = guest.Leaf(Kind::kCockpit, 62, true);
  const auto split_chase = guest.Leaf(Kind::kChaseNear, 48.5f, true);
  Near(guest.Apply(Radians(31 + 8), split_driver, selected), Radians(40 + 8));
  Near(guest.Apply(Radians(24.25f + 22), split_chase, selected), Radians(40 + 22));

  const float original = Radians(65);
  guest.Word(blend + 192, blend); // Cycle.
  Near(guest.Apply(original, blend, selected), original);
  guest.Word(blend + 192, far);
  guest.Word(blend + 196, 0); // Null endpoint.
  Near(guest.Apply(original, blend, selected), original);
  guest.Word(blend + 196, cockpit);
  for (float invalid : {-0.1f, 1.1f, std::numeric_limits<float>::quiet_NaN()}) {
    guest.Float(blend + 200, invalid);
    Near(guest.Apply(original, blend, selected), original);
  }
  uint32_t deep = cockpit;
  for (int i = 0; i < 10; ++i) deep = guest.Blend(far, deep, 0.5f);
  Near(guest.Apply(original, deep, selected), original);
  uint32_t repeated = cockpit;
  for (int i = 0; i < 6; ++i) repeated = guest.Blend(repeated, repeated, 0.5f);
  unsigned nodes = 0;
  const auto counted = [&](uint32_t address) {
    ++nodes;
    return ReadNode(address, guest, tables);
  };
  Near(AdjustedFov(original, repeated, selected, counted), original);
  assert(nodes <= 31); // Bounded work even for a wide repeated DAG without cycles.
  Near(guest.Apply(original, 0xFFFFFFF0, selected), original);
  guest.Float(cockpit + 272, std::numeric_limits<float>::quiet_NaN());
  Near(guest.Apply(original, cockpit, selected), original);
  guest.Word(near + 192, 0xFFFFFFFC); // Chase setting pointer overflow.
  Near(guest.Apply(original, near, selected), original);
}
}  // namespace

int main() {
  std::array<uint8_t, 0x2C> world{};
  assert(!LiveCameraWorld(nullptr) && !LiveCameraWorld(world.data()));
  world[0x0D] = 1; WriteFloat(world.data() + 0x28, 1);
  assert(LiveCameraWorld(world.data()));
  world[0x0C] = 1; assert(!LiveCameraWorld(world.data()));
  world[0x0C] = 0; WriteFloat(world.data() + 0x28, -1);
  assert(!LiveCameraWorld(world.data()));
  WriteFloat(world.data() + 0x28, 0); assert(!LiveCameraWorld(world.data()));
  WriteFloat(world.data() + 0x28, std::numeric_limits<float>::infinity());
  assert(!LiveCameraWorld(world.data()));
  assert(ValidBaseFov(0) && ValidBaseFov(40) && ValidBaseFov(90));
  assert(!ValidBaseFov(39) && !ValidBaseFov(91));
  Check({0x820755AC, 0x8207562C, 0x82075DD4, 0x82075D54, 0x82075E54, 0x82075444});
  Check({0x820858D4, 0x82085954, 0x820860FC, 0x8208607C, 0x8208617C, 0x8208576C});
}
