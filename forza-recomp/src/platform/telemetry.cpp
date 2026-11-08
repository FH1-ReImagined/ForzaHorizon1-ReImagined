// Opt-in, loopback-only FH1 telemetry. See docs/telemetry.md for the wire format.
#ifdef _WIN32
#include <winsock2.h>
#else
#include <sys/socket.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <unistd.h>
#include <dlfcn.h>
#endif
#include "telemetry.h"
#include "driving_gate.h"
#include "xbox_haptics.h"
#include "guest_addr.h"
#include "guest_memory.h"
#include "car_physics.h"
#include <rex/logging.h>
#include <rex/memory/utils.h>
#include <array>
#include <algorithm>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <mutex>

namespace forza::telemetry {
namespace {
using Clock = std::chrono::steady_clock;
uint64_t Now() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count();
}
struct Bridge {
  bool enabled = false;
  bool physics = false;
#ifdef _WIN32
  SOCKET socket = INVALID_SOCKET;
#else
  int socket = -1;
#endif
  sockaddr_in destination{};
  uint64_t token = 0, input_time = 0, last_time = 0;
  uint32_t sequence = 0, car = 0;
  uint8_t left = 0, right = 0;
  uint16_t buttons = 0;
  int16_t steer = 0;
  std::array<float, 3> position{}, velocity{};
  bool have_velocity = false;
  struct Sent { uint32_t sequence = 0; uint64_t time = 0; };
  std::array<Sent, 4> sent{};
  bool xbox_seen = false;
  uint32_t xbox_sequence = 0;
  int logged_active = -1;  // the last logged trigger state (#692): -1 none yet
  uint32_t user_mask = 15;
  std::mutex mutex;
  Bridge() {
    const char* port = std::getenv("FORZA_TELEMETRY_PORT");
    const char* nonce = std::getenv("FORZA_TELEMETRY_TOKEN");
    if (!port || !nonce || std::strlen(nonce) != 16) return;
    for (const char* c = nonce; *c; ++c)
      if (!(*c >= '0' && *c <= '9') && !(*c >= 'a' && *c <= 'f') && !(*c >= 'A' && *c <= 'F')) return;
    char* end = nullptr;
    const long n = std::strtol(port, &end, 10);
    if (*end || n < 1 || n > 65535) return;
    token = std::strtoull(nonce, &end, 16);
    if (*end || !token) return;
#ifdef _WIN32
    WSADATA data;
    if (WSAStartup(MAKEWORD(2, 2), &data)) return;
#endif
    socket = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
#ifdef _WIN32
    if (socket == INVALID_SOCKET) return;
    u_long nonblocking = 1;
    if (ioctlsocket(socket, FIONBIO, &nonblocking)) return;
#else
    if (socket < 0 || fcntl(socket, F_SETFL, O_NONBLOCK) < 0) return;
#endif
    destination.sin_family = AF_INET;
    destination.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    destination.sin_port = htons(uint16_t(n));
    enabled = true;
    const char* physics_env = std::getenv("FORZA_TELEMETRY_PHYSICS");
    physics = physics_env && std::strcmp(physics_env, "1") == 0;
    const char* per_user = std::getenv("FORZA_PAD_PER_USER");
    if (per_user && std::strcmp(per_user, "1") == 0) user_mask = 1;
  }
};
Bridge& State() { static Bridge bridge; return bridge; }

// Reject uncommitted/stale guest pointers before dereferencing, including reads
// crossing host page boundaries. GuestPtr also handles Windows physical mappings.
bool Read(uint8_t* base, uint32_t address, void* out, size_t size) {
  if (address < 0x00100000 || uint64_t(address) + size > 0xF0000000ull) return false;
  auto* p = GuestPtr(base, address);
  for (size_t i = 0; i < size;) {
    size_t region = 0;
    rex::memory::PageAccess access;
    if (!rex::memory::QueryProtect(p + i, region, access) ||
        !(int(access) & int(rex::memory::PageAccess::kReadOnly))) return false;
    const size_t page = rex::memory::page_size();
    i += std::min(size - i, page - (reinterpret_cast<uintptr_t>(p + i) % page));
  }
  std::memcpy(out, p, size);
  return true;
}
bool Word(uint8_t* base, uint32_t address, uint32_t& value) {
  uint32_t raw;
  if (!Read(base, address, &raw, 4)) return false;
  value = std::byteswap(raw);
  return true;
}
bool Vector(uint8_t* base, uint32_t address, std::array<float, 3>& value) {
  for (int i = 0; i < 3; ++i) {
    uint32_t raw;
    if (!Word(base, address + i * 4, raw)) return false;
    value[i] = std::bit_cast<float>(raw);
    if (!std::isfinite(value[i]) || std::abs(value[i]) > 1000000) return false;
  }
  return true;
}
bool LocalCar(uint8_t* base, uint32_t& id, std::array<float, 3>& position,
              std::array<float, 3>& forward) {
  uint32_t root, holder, list, first, end;
  if (!Word(base, FORZA_ADDR(0x832DF024), root) || !Word(base, root + 4, holder) ||
      !Word(base, holder + 4, list) || !Word(base, list + 0x80, first) ||
      !Word(base, list + 0x84, end) || first > end || end - first > 256 || (end-first)%4) return false;
  for (uint32_t slot = first; slot < end; slot += 4) {
    uint32_t vehicle, kind, car;
    if (!Word(base, slot, vehicle) || vehicle > 0xEFFF0000 ||
        !Word(base, vehicle + 0x2DD0, kind) || kind != 0 ||
        !Word(base, vehicle + 0x2D90, car) || car < 0x100A50) continue;
    if (!Vector(base, car - 0xA20, position) || !Vector(base, car - 0xA40, forward)) continue;
    float norm = 0;
    for (float f : forward) norm += f*f;
    if (norm < .8f || norm > 1.2f) continue;
    id = car;
    return true;
  }
  return false;
}
template<size_t N, class T> void Put(std::array<uint8_t, N>& data, size_t& offset, T value) {
  static_assert(std::endian::native == std::endian::little);
  std::memcpy(data.data() + offset, &value, sizeof(value));
  offset += sizeof(value);
}
int XboxRumble(uint32_t users, uint16_t left, uint16_t right) {
  using Function = int (*)(uint32_t, uint16_t, uint16_t, uint32_t);
  static Function function = [] {
#ifdef _WIN32
    auto module = GetModuleHandleW(L"rexruntime.dll");
    return module ? reinterpret_cast<Function>(GetProcAddress(module, "ForzaInputTriggerRumble")) : nullptr;
#else
    return reinterpret_cast<Function>(dlsym(RTLD_DEFAULT, "ForzaInputTriggerRumble"));
#endif
  }();
  return function ? function(users, left, right, 150) : -2;
}
void ReceiveXbox(Bridge& s, uint64_t now, bool active) {
  XboxCommand latest{};
  bool received = false;
  // Bound work on the simulation thread. Expired/replayed commands never arm a
  // trigger: the echoed telemetry sequence must have been sent within 150 ms.
  for (int i = 0; i < 64; ++i) {
    std::array<uint8_t, 64> data;
    sockaddr_in peer{};
#ifdef _WIN32
    int peer_size = sizeof(peer);
#else
    socklen_t peer_size = sizeof(peer);
#endif
    const int size = int(recvfrom(s.socket, reinterpret_cast<char*>(data.data()), int(data.size()), 0,
                                  reinterpret_cast<sockaddr*>(&peer), &peer_size));
    if (size < 0) break;
    XboxCommand command;
    if (peer.sin_addr.s_addr != htonl(INADDR_LOOPBACK) || peer.sin_port != s.destination.sin_port ||
        !DecodeXbox(data.data(), size_t(size), s.token, command) ||
        (s.xbox_seen && !After(command.sequence, s.xbox_sequence))) continue;
    bool fresh = false;
    for (const auto& sample : s.sent)
      if (sample.time && sample.sequence == command.sample && now-sample.time <= 150) fresh = true;
    if (!fresh) continue;
    s.xbox_sequence = command.sequence; s.xbox_seen = true;
    latest = command; received = true;
  }
  if (!received) return;
  const int32_t result = XboxRumble(s.user_mask, active ? latest.left : 0, active ? latest.right : 0);
  std::array<uint8_t, 24> ack{};
  std::memcpy(ack.data(), "FH1A\1\0\30\0", 8);
  std::memcpy(ack.data()+8, &s.token, 8);
  std::memcpy(ack.data()+16, &latest.sequence, 4);
  std::memcpy(ack.data()+20, &result, 4);
  sendto(s.socket, reinterpret_cast<const char*>(ack.data()), int(ack.size()), 0,
         reinterpret_cast<sockaddr*>(&s.destination), sizeof(s.destination));
}
}
void Input(bool connected, uint8_t left, uint8_t right, uint16_t buttons, int16_t steer) {
  auto& s = State();
  if (!s.enabled) return;
  std::lock_guard lock(s.mutex);
  s.input_time = connected ? Now() : 0;
  s.left = left; s.right = right; s.buttons = buttons; s.steer = steer;
}
bool LocalCarPosition(uint8_t* base, float& x, float& z) {
  uint32_t car;
  std::array<float, 3> position{}, forward{};
  if (!LocalCar(base, car, position, forward)) return false;
  x = position[0];
  z = position[2];
  return true;
}
bool LocalCarPose(uint8_t* base, float position_out[3], float forward_out[3]) {
  uint32_t car;
  std::array<float, 3> position{}, forward{};
  if (!LocalCar(base, car, position, forward)) return false;
  for (int i = 0; i < 3; ++i) {
    position_out[i] = position[i];
    forward_out[i] = forward[i];
  }
  return true;
}
bool LocalCarPhysics(uint8_t* base, CarPhysicsView& out) {
  uint32_t car;
  std::array<float, 3> position{}, forward{};
  if (!LocalCar(base, car, position, forward)) return false;
  const CarPhysics p = ReadCarPhysics(
      car, FORZA_TU4 != 0, [base](uint32_t a, void* d, size_t n) { return Read(base, a, d, n); });
  if (!p.valid) return false;
  out.rpm = p.rpm;
  out.speed = p.speed;
  out.gear = p.gear;
  for (int i = 0; i < 5; ++i) out.controls[i] = p.controls[i];
  for (int i = 0; i < 4; ++i) {
    out.slip[i] = p.slip[i];
    out.slip_angle[i] = p.slip_angle[i];
    out.wheel_speed[i] = p.wheel_speed[i];
    out.suspension[i] = p.suspension[i];
    out.contact[i] = p.contact[i];
  }
  return true;
}
void Rumble(uint16_t left, uint16_t right) {
  Gate().Rumble(left, right, Now());  // always: the wheel reads it without the bridge
}
void Physics(uint8_t* base) {
  auto& s = State();
  if (!s.enabled) return;
  // Never wait behind another sampler/input call on a guest simulation thread.
  std::unique_lock lock(s.mutex, std::try_to_lock);
  if (!lock.owns_lock()) return;
  const uint64_t now = Now();
  if (now - s.last_time < 16) return;
  uint32_t car = 0, flags = 0;
  std::array<float, 3> position{}, forward{}, velocity{}, acceleration{};
  const bool valid = LocalCar(base, car, position, forward);
  const CarPhysics physics = valid && s.physics
      ? ReadCarPhysics(car, FORZA_TU4 != 0,
                       [base](uint32_t a, void* p, size_t n) { return Read(base, a, p, n); })
      : CarPhysics{};
  if (valid) flags |= 1;
  if (s.input_time && now - s.input_time < 250) flags |= 2;
  const float dt = float(now - s.last_time) / 1000;
  if (valid && car == s.car && dt > 0 && dt <= .25f) {
    float speed2 = 0;
    for (int i = 0; i < 3; ++i) { velocity[i] = (position[i]-s.position[i])/dt; speed2 += velocity[i]*velocity[i]; }
    if (speed2 < 200*200) {
      flags |= 4;
      if (s.have_velocity) {
        // Local X/Z derived from horizontal heading; Y remains world-up.
        std::array<float, 3> a;
        for (int i = 0; i < 3; ++i) a[i] = (velocity[i]-s.velocity[i])/dt;
        const float norm = std::hypot(forward[0], forward[2]);
        if (norm > .1f) {
          acceleration = {(a[0]*forward[2]-a[2]*forward[0])/norm, a[1],
                          (a[0]*forward[0]+a[2]*forward[2])/norm};
          flags |= 8;
        }
      }
    } else velocity = {};
  }
  s.car = car; s.position = position; s.velocity = velocity;
  s.have_velocity = (flags & 4) != 0; s.last_time = now;
  std::array<uint8_t, 140> packet{};
  const uint16_t packet_size = s.physics ? 140 : 96;
  std::memcpy(packet.data(), "FH1T", 4);
  size_t offset = 4;
  Put(packet, offset, uint16_t(s.physics ? 2 : 1)); Put(packet, offset, packet_size);
  const uint32_t sequence = s.sequence++;
  s.sent[sequence % s.sent.size()] = {sequence, now};
  if (physics.valid) flags |= 16;
  Put(packet, offset, s.token); Put(packet, offset, sequence); Put(packet, offset, flags);
  Put(packet, offset, now); Put(packet, offset, car);
  // Driving: a local car, fresh input, the game's simulation running and its rumble too; not in
  // menus, also not in the online pause menu, where the world keeps running (#692).
  const Drive drive = Player().State();
  const Triggers gate = s.physics ? TriggerGate(drive, Gate(), now)
                                  : (Gate().Driving(now) ? Triggers::kOn : Triggers::kNoRumble);
  const bool driving = valid && (flags & 2) && gate == Triggers::kOn;
  if (int(driving) != s.logged_active) {
    s.logged_active = int(driving);
    if (driving)
      REXLOG_INFO("Telemetry: trigger effects on (driving)");
    else if (!valid || !(flags & 2))
      REXLOG_INFO("Telemetry: trigger effects off (no local car or no fresh pad input)");
    else if (gate == Triggers::kNoRumble)
      REXLOG_INFO("Telemetry: trigger effects off (the game's rumble stopped: a menu)");
    else
      REXLOG_INFO("Telemetry: trigger effects off ({})", DriveName(drive));
  }
  Put(packet, offset, uint32_t(driving));
  for (auto v : {position, velocity, acceleration, forward}) for (float f : v) Put(packet, offset, f);
  Put(packet, offset, s.left); Put(packet, offset, s.right);
  Put(packet, offset, uint8_t(0)); Put(packet, offset, int8_t(s.steer / 256));
  Put(packet, offset, s.buttons); Put(packet, offset, uint16_t(0));
  if (s.physics) {
    for (float value : {physics.rpm, physics.redline, physics.speed, physics.controls[1],
                        physics.controls[0], physics.controls[4]}) Put(packet, offset, value);
    const float direction = physics.gear == 0 ? -1.0f : 1.0f;
    for (float slip : physics.slip) Put(packet, offset, direction * slip);
    Put(packet, offset, uint32_t(physics.abs));
  }
  sendto(s.socket, reinterpret_cast<const char*>(packet.data()), packet_size, 0,
         reinterpret_cast<sockaddr*>(&s.destination), sizeof(s.destination));
  ReceiveXbox(s, now, driving);
}
}
