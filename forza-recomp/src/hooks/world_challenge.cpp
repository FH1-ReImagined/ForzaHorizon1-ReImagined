// forza - head-to-head challenges between players in the Horizon World
// (docs/online_vision.md, phase 3; the server side is server/forza_live/competition/challenge.py).
//
// In the world's free roam a player drives up behind another player and presses X. The
// server finds the car close ahead of it in the same world and asks that player, who
// accepts with X or declines with B within 10 s. On accept both race to a landmark a few
// km away (picked by the server) from a 3 s countdown, inside the free roam: nobody leaves
// the world and the other players are not disturbed. The first car within the finish
// radius wins. The prompts, the countdown, the distance and direction to the finish and
// the result use the game's native HUD notification control (challenge_hud.cpp).
//
// Client side:
// - Every frame of the world's free roam (WorldChallengeFrame, called from the
//   CIsFreeRoamSessionOver update that world_events.cpp hooks) reads the cars on this
//   console, sends its own car's position, heading and speed to the server four times a
//   second, looks for another player's car close ahead, and checks the finish while racing.
// - The pad (WorldChallengePad, from XamInputGetState in input_autopress.cpp): with another
//   player's car close ahead (the hint "X: challenge the driver ahead" shows), X sends a
//   challenge and is kept from the game (X opens the leaderboards in free roam);
//   otherwise the game gets it. While a prompt shows, X accepts and B declines (both kept
//   from the game).
// - The server's messages (LiveChallenge, network thread) publish native HUD text.
//
// The car: CStoreRestorePlayerCarPosition (sub_82983A90) reads the player's car as
// root = *(0x832DF024), list = *(*(root + 4) + 4), slot = sub_8248BAE8(list) (the
// active vehicle of the list's vector at +0x80), car = *(*slot + 0x2D90), and the
// position is the car's vfunc 0x13C (a vector, y up); ReadCars reads the same.
//
// world_challenges (on) turns it off; FORZA_CHALLENGE_TRACE=1 logs the car position.

#include "../kernel/live.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/challenge_hud.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

REXCVAR_DEFINE_BOOL(world_challenges, true, "Network/Forza Live",
                    "Horizon World: head-to-head challenges (X behind another player's car)");

namespace {
constexpr uint32_t kCarRoot = FORZA_ADDR(0x832DF024);
constexpr uint16_t kButtonB = 0x2000, kButtonX = 0x4000;
// CHALLENGE ops (server/forza_live/competition/challenge.py).
constexpr uint8_t kPos = 1, kRequest = 2, kAnswer = 3, kFinish = 4, kLeft = 5;
constexpr uint8_t kNotice = 16, kPrompt = 17, kStart = 18, kEnd = 19, kConfig = 20;
constexpr char kTitle[] = "HEAD-TO-HEAD";

using Clock = std::chrono::steady_clock;
int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch())
      .count();
}

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
uint16_t Load16(const uint8_t* p) { return uint16_t(p[0] << 8 | p[1]); }
float LoadF(const uint8_t* p) {
  const uint32_t v = Load32(p);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}
void Put32(std::vector<uint8_t>& b, uint32_t v) {
  for (int s = 24; s >= 0; s -= 8) b.push_back(uint8_t(v >> s));
}
void PutF(std::vector<uint8_t>& b, float f) {
  uint32_t v;
  std::memcpy(&v, &f, 4);
  Put32(b, v);
}

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_CHALLENGE_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

bool Plausible(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }

// The cars on this console: the list's vehicles are every car of the world or race, each
// with its kind at +0x2DD0 (0 this console's player, 1 a CPU driver this console runs, 2
// another console's player, 3 its CPU driver; seen in a race with two consoles and 6 CPU
// drivers; sub_8248BAE8 picks the first of kind 0). The car's vfunc 0x13C returns the
// position at car - 0xA20 (lvx; x, y, z, y up); its up vector is at car - 0xA50 and its
// forward vector at car - 0xA40 (it matched the direction the car had moved in). `own`
// gets this console's player's car (false when there is none), `others` the other
// consoles' players' cars.
struct Car {
  float p[3];
  float forward[2];  // x, z, normalized; 0, 0 when the car points straight up or down
};
bool ReadCars(uint8_t* base, Car& own, std::vector<Car>* others) {
  const uint32_t root = Load32(forza::GuestPtr(base, kCarRoot));
  if (!Plausible(root)) return false;
  const uint32_t holder = Load32(forza::GuestPtr(base, root + 4));
  if (!Plausible(holder)) return false;
  const uint32_t list = Load32(forza::GuestPtr(base, holder + 4));
  if (!Plausible(list)) return false;
  const uint32_t first = Load32(forza::GuestPtr(base, list + 0x80));
  const uint32_t end = Load32(forza::GuestPtr(base, list + 0x84));
  if (!Plausible(first) || end < first || end - first > 4 * 64) return false;
  bool found = false;
  for (uint32_t slot = first; slot < end; slot += 4) {
    const uint32_t vehicle = Load32(forza::GuestPtr(base, slot));
    if (!Plausible(vehicle)) continue;
    const uint32_t kind = Load32(forza::GuestPtr(base, vehicle + 0x2DD0));
    if (kind != 0 && (kind != 2 || !others)) continue;
    const uint32_t car = Load32(forza::GuestPtr(base, vehicle + 0x2D90));
    if (!Plausible(car)) continue;
    const uint8_t* p = forza::GuestPtr(base, car - 0xA20);
    Car c;
    for (int i = 0; i < 3; ++i) c.p[i] = LoadF(p + 4 * i);
    const uint8_t* f = forza::GuestPtr(base, car - 0xA40);
    const float fx = LoadF(f), fz = LoadF(f + 8), fl = std::sqrt(fx * fx + fz * fz);
    c.forward[0] = fl > 0.2f && fl < 2.0f ? fx / fl : 0.0f;
    c.forward[1] = fl > 0.2f && fl < 2.0f ? fz / fl : 0.0f;
    if (!std::isfinite(c.p[0]) || !std::isfinite(c.p[1]) || !std::isfinite(c.p[2]) ||
        std::fabs(c.p[0]) > 1e5f || std::fabs(c.p[2]) > 1e5f) {
      continue;
    }
    if (kind == 0 && !found) {
      own = c;
      found = true;
    } else if (kind == 2) {
      others->push_back(c);
    }
  }
  return found;
}

// The game's sat nav. CSetSatNavDestinationPos (sub_829423B0) and the map's waypoint
// (0x826E04E8) do it as: components = *sub_825F6FB0() (= *(*(*(0x832DF024) + 4) + 0x4C)),
// node = sub_82CDC5A0(components[*(0x832FF090)], position in v1) (the road network's node
// for a position), sub_8299FB40(components[*(0x832FF054)], node) (the sat nav's
// destination; node 0 clears it). A challenge borrows this route and restores it.
// The tutorial can lock the destination; preserve that lock while borrowing it.
// tu_address_map: TU4 addresses
extern "C" REX_FUNC(__imp__sub_82CDC5A0);
extern "C" REX_FUNC(__imp__sub_8299FB40);
struct NavigationLease {
  uint32_t manager = 0, destination = 0;
  uint8_t locked = 0;
} navigation;
bool SatNav(PPCContext& ctx, uint8_t* base, bool set, float x, float y, float z, bool quiet = false) {
  const uint32_t root = Load32(forza::GuestPtr(base, kCarRoot));
  if (!Plausible(root)) return false;
  const uint32_t holder = Load32(forza::GuestPtr(base, root + 4));
  if (!Plausible(holder)) return false;
  const uint32_t components = Load32(forza::GuestPtr(base, holder + 0x4C));
  if (!Plausible(components)) return false;
  const uint32_t table = Load32(forza::GuestPtr(base, components));
  if (!Plausible(table)) return false;
  const uint32_t roads = Load32(forza::GuestPtr(
      base, table + 4 * Load32(forza::GuestPtr(base, FORZA_ADDR(0x832FF090)))));
  const uint32_t satnav = Load32(forza::GuestPtr(
      base, table + 4 * Load32(forza::GuestPtr(base, FORZA_ADDR(0x832FF054)))));
  if (!Plausible(roads) || !Plausible(satnav)) return false;
  // A different free roam owns different components. Never dereference the old one.
  if (navigation.manager && navigation.manager != satnav) navigation = {};
  if (!set && !navigation.manager) return true;
  PPCContext call = ctx;
  call.r1.u64 = ctx.r1.u32 - 0x400;
  uint32_t node = navigation.destination;
  if (set) {
    call.r3.u64 = roads;
    call.v1.f32[3] = x;  // lvx byte-reverses the vector: x is the top lane
    call.v1.f32[2] = y;
    call.v1.f32[1] = z;
    call.v1.f32[0] = 1.0f;
    __imp__sub_82CDC5A0(call, base);
    node = call.r3.u32;
    if (!Plausible(node)) return false;
  }
  auto* locked = forza::GuestPtr(base, satnav + 0x2FC);
  const NavigationLease previous = navigation.manager ? navigation : NavigationLease{
      satnav, Load32(forza::GuestPtr(base, satnav + 0x240)), *locked};
  *locked = 0;
  // Same tracker reset used by the game's CLockSatNavDestination when unlocking.
  call.r1.u64 = ctx.r1.u32 - 0x400;
  call.r3.u64 = satnav;
  rex::runtime::ResolveIndirectFunction(FORZA_TU4 ? 0x82A4C660 : 0x829980E8)(call, base);
  call.r1.u64 = ctx.r1.u32 - 0x400;
  call.r3.u64 = satnav;
  call.r4.u64 = node;
  __imp__sub_8299FB40(call, base);
  const bool accepted = call.r3.u8 != 0;
  *locked = accepted && set ? 1 : previous.locked;
  if (accepted) {
    navigation = set ? previous : NavigationLease{};
    if (!quiet)
      REXLOG_INFO("CHALLENGE: GPS {} (road node {:08X})",
                  set ? "route to the finish" : "previous route restored", node);
  }
  return accepted;
}

// Client state. Game thread (frame, pad) and network thread (messages): one mutex.
std::mutex mutex;
enum class State { kIdle, kWaiting, kPrompt, kRacing };
State state = State::kIdle;
uint32_t challenge_id = 0;
std::string opponent, destination;
float finish_x = 0, finish_y = 0, finish_z = 0, finish_radius = 0;
// The game's GPS route: borrow it on START and restore it at the end.
// The HUD update also drains cleanup when the free-roam frame hook stops.
enum class Gps { kNone, kSet, kShown, kClear };
Gps gps = Gps::kNone;
int64_t next_gps_attempt = 0;
int64_t deadline = 0;      // prompt: answer by; waiting: give up at; racing: the start time
bool finished = false;
int64_t last_frame = 0;    // the last frame of the world's free roam
bool in_world = false;     // we told the server we drive in the world
int64_t last_pos_sent = 0, last_hud = 0, last_request = 0;
float pos[3] = {}, heading[2] = {0, 1}, speed = 0;
int64_t pos_at = 0;
bool have_pos = false;
// The server's rule for a challenge (CONFIG): another player's car within `range` m,
// ahead of ours unless any_direction. X challenges only then; otherwise the game gets it.
float range = 40;
bool any_direction = false;
bool target_ahead = false;  // another player's car is there now
bool x_taken = false;       // the X held now is ours (hidden from the game)

int64_t hud_busy_until = 0;  // a message shows that the hint must not replace

void Send(std::vector<uint8_t> body) { forza::LiveSendChallenge(body); }

// Publish a native HUD message for `seconds`. Needs the mutex; guest UI calls
// are deferred to the native control's own update thread.
void Show(const std::string& title, const std::string& body, double seconds) {
  hud_busy_until = NowMs() + int64_t(seconds * 1000);
  forza::ChallengeHudShow(title, body, seconds);
}

std::string Distance(float metres) {
  char buf[32];
  if (metres >= 1000) std::snprintf(buf, sizeof(buf), "%.1f km", metres / 1000);
  else std::snprintf(buf, sizeof(buf), "%.0f m", metres);
  return buf;
}

// Where the finish is, relative to the car's heading (x right of z, y up).
const char* Direction(float dx, float dz) {
  const float ahead = dx * heading[0] + dz * heading[1];
  const float right = dx * heading[1] - dz * heading[0];
  const float angle = std::atan2(right, ahead) * 180.0f / 3.14159265f;
  static const char* const kNames[] = {"behind", "behind, left", "left", "ahead, left", "ahead",
                                       "ahead, right", "right", "behind, right", "behind"};
  return kNames[int(std::lround((angle + 180.0f) / 45.0f))];
}

// Needs the mutex.
void RaceHud(int64_t now) {
  if (now < deadline) {
    const int64_t left = (deadline - now + 999) / 1000;
    forza::ChallengeHudShow(kTitle, "vs " + opponent + ": race to " + destination + "\n" +
                               std::to_string(left) + "...", 2, false);
    return;
  }
  const float dx = finish_x - pos[0], dz = finish_z - pos[2];
  const float dist = std::sqrt(dx * dx + dz * dz);
  std::string body = (now - deadline < 2000 ? "GO!  " : "") + std::string("vs ") + opponent +
                     ": race to " + destination + "\n" + Distance(dist) + ", " +
                     Direction(dx, dz);
  static int64_t traced = 0;
  const bool log = Trace() && now - traced >= 3000;
  if (log) traced = now;
  forza::ChallengeHudShow(kTitle, body, 3, log);
}

void LeaveWorld() {
  if (!in_world) return;
  in_world = false;
  forza::ChallengeHudClear();
  if (gps != Gps::kNone) gps = Gps::kClear;
  Send({kLeft});
  REXLOG_INFO("CHALLENGE: out of the world's free roam");
  if (state != State::kIdle) {
    state = State::kIdle;
    Show(kTitle, "You left the free roam: challenge over", 4);
  }
}
}  // namespace

namespace forza {

// Continue deferred cleanup even when the world's frame hook has stopped.
void WorldChallengeHudTick(PPCContext& ctx, uint8_t* base) {
  std::lock_guard<std::mutex> lock(mutex);
  if (in_world && (!REXCVAR_GET(world_challenges) || NowMs() - last_frame > 2000)) LeaveWorld();
  if (gps == Gps::kClear && SatNav(ctx, base, false, 0, 0, 0)) gps = Gps::kNone;
}

// Every frame of the Horizon World's free roam (world_events.cpp, sub_825ADB30).
void WorldChallengeFrame(PPCContext& ctx, uint8_t* base) {
  if (!REXCVAR_GET(world_challenges)) return;
  const int64_t now = NowMs();
  std::lock_guard<std::mutex> lock(mutex);
  last_frame = now;
  if (now - pos_at < 100) return;
  Car own;
  std::vector<Car> others;
  if (!ReadCars(base, own, &others)) return;
  const float* p = own.p;
  if (have_pos) {
    const float dx = p[0] - pos[0], dz = p[2] - pos[2];
    const float moved = std::sqrt(dx * dx + dz * dz), dt = float(now - pos_at) / 1000.0f;
    if (dt > 0 && dt < 1) speed = moved / dt;
    if (own.forward[0] == 0 && own.forward[1] == 0 && moved > 0.3f && moved < 200) {
      heading[0] = dx / moved;  // no forward vector: the direction it moved in
      heading[1] = dz / moved;
    }
  }
  if (own.forward[0] != 0 || own.forward[1] != 0) {
    heading[0] = own.forward[0];
    heading[1] = own.forward[1];
  }
  std::memcpy(pos, p, sizeof(pos));
  pos_at = now;
  // Another player's car close ahead: X challenges it (the server decides who).
  const bool was_ahead = target_ahead;
  target_ahead = false;
  for (const Car& c : others) {
    const float dx = c.p[0] - pos[0], dz = c.p[2] - pos[2];
    const float dist = std::sqrt(dx * dx + dz * dz);
    if (dist > range) continue;
    if (any_direction || dist < 0.1f || (dx * heading[0] + dz * heading[1]) / dist >= 0.5f) {
      target_ahead = true;
      break;
    }
  }
  if (target_ahead && state == State::kIdle && now >= hud_busy_until && now - last_hud >= 1000) {
    last_hud = now;
    forza::ChallengeHudShow(kTitle, "Driver ahead", 1.5, !was_ahead, challenge_hud::Actions::kChallenge);
  }
  if (!have_pos && Trace()) REXLOG_INFO("CHALLENGE: car at {:.1f} {:.1f} {:.1f}", p[0], p[1], p[2]);
  have_pos = true;
  in_world = true;
  if (now - last_pos_sent >= 250) {
    last_pos_sent = now;
    std::vector<uint8_t> body{kPos};
    for (float v : {pos[0], pos[1], pos[2], heading[0], heading[1], speed}) PutF(body, v);
    Send(body);
    static int64_t traced = 0;
    if (Trace() && now - traced > 5000) {
      traced = now;
      REXLOG_INFO("CHALLENGE: car at {:.1f} {:.1f} {:.1f} heading {:.2f} {:.2f} {:.1f} m/s",
                  pos[0], pos[1], pos[2], heading[0], heading[1], speed);
    }
  }
  if (gps == Gps::kSet && now >= next_gps_attempt) {
    next_gps_attempt = now + 500;
    if (SatNav(ctx, base, true, finish_x, finish_y, finish_z)) gps = Gps::kShown;
  } else if (gps == Gps::kClear && SatNav(ctx, base, false, 0, 0, 0)) {
    gps = Gps::kNone;
  }
  if (state == State::kRacing) {
    if (!finished && now >= deadline) {
      const float dx = finish_x - pos[0], dz = finish_z - pos[2];
      if (dx * dx + dz * dz <= finish_radius * finish_radius) {
        finished = true;
        std::vector<uint8_t> body{kFinish};
        Put32(body, challenge_id);
        Put32(body, uint32_t(now - deadline));
        Send(body);
        REXLOG_INFO("CHALLENGE {}: at the finish after {} ms", challenge_id, now - deadline);
        Show(kTitle, "Finish! Waiting for the result...", 10);
      }
    }
    if (!finished && now - last_hud >= 200) {
      last_hud = now;
      RaceHud(now);
    }
  }
}

// The pad of player 1 (input_autopress.cpp, every XamInputGetState): returns the buttons
// to hide from the game.
uint16_t WorldChallengePad(uint16_t buttons) {
  if (!REXCVAR_GET(world_challenges)) return 0;
  static uint16_t previous = 0;
  const uint16_t pressed = buttons & ~previous;
  previous = buttons;
  const int64_t now = NowMs();
  std::lock_guard<std::mutex> lock(mutex);
  if (in_world && now - last_frame > 2000) LeaveWorld();  // out of the free roam
  if (!in_world) return 0;
  switch (state) {
    case State::kIdle:
      if ((pressed & kButtonX) && target_ahead && now - last_request > 1500) {
        last_request = now;
        x_taken = true;
        Send({kRequest});
        REXLOG_INFO("CHALLENGE: X pressed: asking the server for the car ahead");
      }
      if (!(buttons & kButtonX)) x_taken = false;
      return x_taken ? kButtonX : 0;
    case State::kPrompt: {
      if (now >= deadline) {
        state = State::kIdle;
        Show(kTitle, "Challenge expired", 3);
        return 0;
      }
      if (pressed & (kButtonX | kButtonB)) {
        const bool accept = pressed & kButtonX;
        std::vector<uint8_t> body{kAnswer};
        Put32(body, challenge_id);
        body.push_back(accept ? 1 : 0);
        Send(body);
        REXLOG_INFO("CHALLENGE {}: {}", challenge_id, accept ? "accepted" : "declined");
        state = accept ? State::kWaiting : State::kIdle;
        deadline = now + 5000;
        Show(kTitle, accept ? "Accepted! Get ready..." : "Declined", accept ? 5 : 3);
      } else if (now - last_hud >= 250) {
        last_hud = now;
        forza::ChallengeHudShow(kTitle, opponent + " challenges you to a race\n(" +
                                   std::to_string((deadline - now + 999) / 1000) + ")",
                       2, false, challenge_hud::Actions::kAnswer);
      }
      return kButtonX | kButtonB;
    }
    case State::kWaiting:
      if (now >= deadline) state = State::kIdle;
      if (!(buttons & kButtonX)) x_taken = false;
      return x_taken ? kButtonX : 0;
    case State::kRacing:
      return 0;
  }
  return 0;
}

// CHALLENGE from the server (net.cpp, network thread); see server/forza_live/competition/challenge.py.
void LiveChallenge(const uint8_t* p, size_t n) {
  if (n < 1) return;
  const int64_t now = NowMs();
  std::lock_guard<std::mutex> lock(mutex);
  auto text8 = [&](size_t& at) {
    if (at >= n || at + 1 + p[at] > n) return std::string();
    std::string s(reinterpret_cast<const char*>(p + at + 1), p[at]);
    at += 1 + p[at];
    return s;
  };
  auto text16 = [&](size_t at) {
    if (at + 2 > n) return std::string();
    const size_t len = std::min<size_t>(Load16(p + at), n - at - 2);
    return std::string(reinterpret_cast<const char*>(p + at + 2), len);
  };
  auto split = [](const std::string& s, std::string& title, std::string& body) {
    const size_t nl = s.find('\n');
    title = nl == std::string::npos ? std::string(kTitle) : s.substr(0, nl);
    body = nl == std::string::npos ? s : s.substr(nl + 1);
  };
  switch (p[0]) {
    case kNotice: {
      if (n < 4) return;
      std::string title, body;
      split(text16(2), title, body);
      if (state == State::kIdle || state == State::kWaiting) {
        if (body.find("Challenge sent") != std::string::npos) {
          state = State::kWaiting;
          deadline = now + 1000 * p[1] + 2000;
        }
        Show(title, body, p[1]);
      }
      return;
    }
    case kPrompt: {
      if (n < 7) return;
      size_t at = 6;
      challenge_id = Load32(p + 1);
      deadline = now + 1000 * p[5];
      opponent = text8(at);
      state = State::kPrompt;
      last_hud = 0;
      REXLOG_INFO("CHALLENGE {}: {} challenges us", challenge_id, opponent);
      forza::ChallengeHudShow(kTitle, opponent + " challenges you to a race\n(" +
                                 std::to_string(p[5]) + ")",
                     2, true, challenge_hud::Actions::kAnswer);
      return;
    }
    case kStart: {
      if (n < 23) return;
      challenge_id = Load32(p + 1);
      deadline = now + Load16(p + 5);
      finish_x = LoadF(p + 7);
      finish_y = LoadF(p + 11);
      finish_z = LoadF(p + 15);
      finish_radius = LoadF(p + 19);
      gps = Gps::kSet;
      next_gps_attempt = 0;
      size_t at = 23;
      opponent = text8(at);
      destination = text8(at);
      state = State::kRacing;
      finished = false;
      last_hud = 0;
      REXLOG_INFO("CHALLENGE {}: race {} to {} ({:.0f}, {:.0f}, radius {:.0f}); we are at {:.0f} {:.0f}",
                  challenge_id, opponent, destination, finish_x, finish_z, finish_radius, pos[0],
                  pos[2]);
      Show(kTitle, "vs " + opponent + ": race to " + destination, 3);
      return;
    }
    case kEnd: {
      if (n < 6) return;
      const uint8_t outcome = p[5];
      static const char* const kOutcome[] = {"", "YOU WIN", "YOU LOSE", "NO WINNER", kTitle};
      REXLOG_INFO("CHALLENGE {}: over, outcome {}", Load32(p + 1), outcome);
      gps = Gps::kClear;
      state = State::kIdle;
      Show(outcome <= 4 ? kOutcome[outcome] : kTitle, text16(6), 6);
      return;
    }
    case kConfig:
      if (n < 6) return;
      range = LoadF(p + 1);
      any_direction = p[5] != 0;
      REXLOG_INFO("CHALLENGE: the server's rule: a car within {:.0f} m{}", range,
                  any_direction ? "" : ", ahead");
      return;
    default:
      return;
  }
}

// The lap guide of an imported FM4 circuit (fm4_lap_timer.cpp): the sat-nav's destination
// becomes the road node nearest (x, y, z), as a challenge's finish does. Called from the world
// update, every few hundred metres of the lap; false while the game has no road network or
// sat-nav yet. A circuit plays offline, so no challenge borrows the sat-nav there.
bool SatNavGuide(PPCContext& ctx, uint8_t* base, float x, float y, float z) {
  std::lock_guard<std::mutex> lock(mutex);
  return SatNav(ctx, base, true, x, y, z, true);
}

}  // namespace forza
