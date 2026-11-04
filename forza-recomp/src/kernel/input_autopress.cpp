// forza - controller routing (every pad drives player 1) and scripted input for tests.
//
// Routing: the runtime signs in one profile, on user 0, and gives each SDL pad its own
// user slot in connection order. With virtual pads around (Steam Input, Sunshine), the
// pad in your hands is often slot 1 or 2: Forza then sees START from an unsigned user and
// asks it to sign in, which nothing can answer, and the title screen never goes away. So
// every pad is merged into user 0 (buttons combined, the strongest stick and trigger
// wins) and users 1-3 report no controller. FORZA_PAD_PER_USER=1 restores the SDK's
// one-slot-per-pad behaviour. Vibration for user 0 goes to every pad.
//
// FORZA_AUTOPRESS="45:A,50:START,52.5:DOWN,60:LSUP:3" presses each input at
// the given number of seconds after the first XamInputGetState call, for 0.3 s
// or for the optional third field in seconds. Buttons: A B X Y START BACK UP
// DOWN LEFT RIGHT LB RB LS RS. Full deflection of a stick: LSUP LSDOWN LSLEFT
// LSRIGHT RSUP RSDOWN RSLEFT RSRIGHT. Triggers: LT RT. When the variable is
// unset the hook only forwards to the SDK implementation. Input is applied to
// user 0 even if no physical controller is connected.
//
// FORZA_AUTOPRESS_FIFO=<path> also reads "INPUT[:hold]" lines from that FIFO
// (create it with mkfifo) while the game runs and presses each one as it
// arrives ("PADn:INPUT" presses it on the pad in SDK slot n, before routing), so menus can be stepped through while watching frame dumps
// (FORZA_FRAMEDUMP_TRIGGER). In either mode the game sees a connected, centred
// controller between presses instead of a disconnected one. "SCENE:<name>" lines label
// the frame statistics (RECOMP_PERF, src/debug/frame_stats.cpp); "PROF:START" and
// "PROF:STOP" run the CPU sampler (src/debug/cpu_profile.cpp); "SAY:<text>" says <text> in
// World chat and "SAYC:<text>" in Club chat (live_friends.cpp; the Co-op Festival's /event and
// the /club commands in scripted tests).

#include "input_caps.h"
#include "live.h"

#include "../platform/guest_memory.h"
#include "../platform/benchmark.h"
#include "../platform/auto_pause.h"

#include "../platform/telemetry.h"
#include "../platform/wheel_bridge.h"
#include "../platform/xr.h"
#include "../platform/sdk_import.h"
#include "../debug/cpu_profile.h"
#include "../debug/frame_stats.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#endif

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using forza::GuestPtr;

namespace forza {
uint16_t WorldChallengePad(uint16_t buttons);  // hooks/world_challenge.cpp
uint16_t LiveInvitePad(uint16_t buttons, int left_y, bool& block_all);  // kernel/live_invites.cpp
uint16_t LiveFriendsPad(uint16_t buttons, int left_x, int left_y, bool& block_all);  // live_friends.cpp
uint16_t KeyboardUiPad(uint16_t buttons, int left_x, int left_y, bool& block_all);  // xam_keyboard.cpp
uint16_t CustomRacePad(uint16_t buttons);  // hooks/custom_race.cpp
}

namespace {

using GuestFunc = void (*)(PPCContext&, uint8_t*);

struct Press {
  uint32_t slot;  // SDK input slot the press is injected into (FIFO "PADn:" prefix)
  double at_seconds;
  double hold_seconds;
  uint16_t buttons;
  // Stick axes (left x/y, right x/y) and triggers (left, right); 0 = untouched.
  int16_t sticks[4];
  uint8_t triggers[2];
};

constexpr double kDefaultHoldSeconds = 0.3;
constexpr uint32_t kErrorSuccess = 0;

uint16_t ButtonMask(const std::string& name) {
  static const struct {
    const char* name;
    uint16_t mask;
  } kButtons[] = {
      {"UP", 0x0001},    {"DOWN", 0x0002}, {"LEFT", 0x0004}, {"RIGHT", 0x0008},
      {"START", 0x0010}, {"BACK", 0x0020}, {"LB", 0x0100},   {"RB", 0x0200},
      {"LS", 0x0040},    {"RS", 0x0080},
      {"A", 0x1000},     {"B", 0x2000},    {"X", 0x4000},    {"Y", 0x8000},
  };
  for (const auto& b : kButtons) {
    if (name == b.name) return b.mask;
  }
  return 0;
}

// Fills the stick/trigger fields of `press` for an analog input name.
bool ApplyAnalog(const std::string& name, Press& press) {
  static const struct {
    const char* name;
    int axis;  // 0 lx, 1 ly, 2 rx, 3 ry
    int16_t value;
  } kSticks[] = {
      {"LSUP", 1, 32767},  {"LSDOWN", 1, -32768}, {"LSLEFT", 0, -32768}, {"LSRIGHT", 0, 32767},
      {"RSUP", 3, 32767},  {"RSDOWN", 3, -32768}, {"RSLEFT", 2, -32768}, {"RSRIGHT", 2, 32767},
  };
  for (const auto& s : kSticks) {
    if (name == s.name) {
      press.sticks[s.axis] = s.value;
      return true;
    }
  }
  if (name == "LT" || name == "RT") {
    press.triggers[name == "LT" ? 0 : 1] = 255;
    return true;
  }
  return false;
}

std::vector<Press> ParseSchedule(const char* spec) {
  std::vector<Press> presses;
  std::string s(spec);
  size_t pos = 0;
  while (pos < s.size()) {
    size_t end = s.find(',', pos);
    if (end == std::string::npos) end = s.size();
    std::string item = s.substr(pos, end - pos);
    pos = end + 1;
    size_t colon = item.find(':');
    if (colon == std::string::npos) continue;
    size_t colon2 = item.find(':', colon + 1);
    std::string name = item.substr(colon + 1, colon2 == std::string::npos ? std::string::npos
                                                                         : colon2 - colon - 1);
    Press press{};
    press.at_seconds = std::atof(item.substr(0, colon).c_str());
    press.hold_seconds = colon2 == std::string::npos
                             ? kDefaultHoldSeconds
                             : std::atof(item.substr(colon2 + 1).c_str());
    press.buttons = ButtonMask(name);
    if (press.buttons || ApplyAnalog(name, press)) presses.push_back(press);
  }
  return presses;
}

std::mutex schedule_mutex;

// Reads "INPUT[:hold]" lines from the FIFO and schedules each one right away.
void ReadFifo(std::string path, std::vector<Press>* schedule,
              std::chrono::steady_clock::time_point start) {
#ifdef _WIN32
  // A POSIX FIFO; scripted test sessions (tools/live_session.sh) run on Linux.
  (void)schedule;
  (void)start;
  REXLOG_WARN("FORZA_AUTOPRESS_FIFO is not supported on Windows ({})", path);
  return;
#else
  // O_RDWR keeps the FIFO open (no EOF) when the writer goes away.
  int fd = open(path.c_str(), O_RDWR);
  if (fd < 0) {
    REXLOG_WARN("FORZA_AUTOPRESS_FIFO: cannot open {}", path);
    return;
  }
  std::string line;
  char c;
  while (read(fd, &c, 1) == 1) {
    if (c != '\n') {
      line += c;
      continue;
    }
    double now = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    if (line == "PROF:START" || line == "PROF:STOP") {  // debug/cpu_profile.cpp
      if (line == "PROF:START") {
        forza::cpuprof::Start();
      } else {
        forza::cpuprof::Stop();
      }
      line.clear();
      continue;
    }
    if (line.rfind("SCENE:", 0) == 0) {  // frame statistics label (debug/frame_stats.cpp)
      forza::perf::SetScene(line.substr(6));
      line.clear();
      continue;
    }
    if (line.rfind("SAY:", 0) == 0) {  // a World chat line, as typed (live_friends.cpp)
      forza::LiveChatSay(2, line.substr(4));
      REXLOG_INFO("FORZA_AUTOPRESS_FIFO: t={:.2f}s {}", now, line);
      line.clear();
      continue;
    }
    if (line.rfind("SAYC:", 0) == 0) {  // a Club chat line, as typed (live_friends.cpp)
      forza::LiveChatSay(4, line.substr(5));
      REXLOG_INFO("FORZA_AUTOPRESS_FIFO: t={:.2f}s {}", now, line);
      line.clear();
      continue;
    }
    uint32_t slot = 0;
    if (line.size() > 5 && line.rfind("PAD", 0) == 0 && line[3] >= '0' && line[3] <= '3' &&
        line[4] == ':') {  // "PAD1:START": a press on the pad in slot 1 (routing tests)
      slot = uint32_t(line[3] - '0');
      line = line.substr(5);
    }
    std::vector<Press> parsed = ParseSchedule(("0:" + line).c_str());
    for (Press& press : parsed) {
      press.slot = slot;
      press.at_seconds = now;
      std::lock_guard<std::mutex> lock(schedule_mutex);
      schedule->push_back(press);
    }
    REXLOG_INFO("FORZA_AUTOPRESS_FIFO: t={:.2f}s {}", now, line);
    line.clear();
  }
#endif
}

GuestFunc Sdk(const char* name) { return forza::SdkImport(name); }

constexpr uint32_t kErrorDeviceNotConnected = 0x48F;
constexpr uint32_t kFlagAnyUser = 1u << 30;
constexpr uint32_t kSlots = 4;

bool PadPerUser() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_PAD_PER_USER");
    return v && *v && *v != '0';
  }();
  return on;
}

std::vector<Press> schedule;
std::chrono::steady_clock::time_point start;
bool scripted = false;

// FORZA_INPUT_RECORD=<file> logs player 1's pad on every change ("t buttons lt rt lx ly rx ry");
// FORZA_INPUT_REPLAY=<file> plays such a log back on slot 0 in place of the hardware.
struct Sample {
  double t;
  uint16_t buttons;
  uint8_t triggers[2];
  int16_t sticks[4];
};
FILE* record_file = nullptr;
std::vector<Sample> replay;

double Now() { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); }

void LoadReplay(const char* path) {
  FILE* f = std::fopen(path, "r");
  if (!f) {
    REXLOG_WARN("FORZA_INPUT_REPLAY: cannot open {}", path);
    return;
  }
  Sample s{};
  unsigned b, lt, rt;
  int lx, ly, rx, ry;
  while (std::fscanf(f, "%lf %x %u %u %d %d %d %d", &s.t, &b, &lt, &rt, &lx, &ly, &rx, &ry) == 8) {
    s.buttons = uint16_t(b);
    s.triggers[0] = uint8_t(lt);
    s.triggers[1] = uint8_t(rt);
    s.sticks[0] = int16_t(lx), s.sticks[1] = int16_t(ly), s.sticks[2] = int16_t(rx), s.sticks[3] = int16_t(ry);
    replay.push_back(s);
  }
  std::fclose(f);
  REXLOG_INFO("FORZA_INPUT_REPLAY: {} samples from {}", replay.size(), path);
}

void InitScripting() {
  static std::once_flag once;
  std::call_once(once, [] {
    start = std::chrono::steady_clock::now();
    if (const char* spec = std::getenv("FORZA_AUTOPRESS")) {
      schedule = ParseSchedule(spec);
      scripted = !schedule.empty();
      REXLOG_INFO("FORZA_AUTOPRESS: {} scheduled presses", schedule.size());
    }
    if (const char* fifo = std::getenv("FORZA_AUTOPRESS_FIFO"); fifo && *fifo) {
      scripted = true;
      std::thread(ReadFifo, std::string(fifo), &schedule, start).detach();
    }
    if (const char* path = std::getenv("FORZA_INPUT_REPLAY"); path && *path) {
      LoadReplay(path);
      scripted |= !replay.empty();
    }
    if (const char* path = std::getenv("FORZA_INPUT_RECORD"); path && *path) {
      record_file = std::fopen(path, "w");
      REXLOG_INFO("FORZA_INPUT_RECORD: {} {}", path, record_file ? "open" : "cannot open");
    }
    if (!PadPerUser()) REXLOG_INFO("Input: every pad drives user 0 (FORZA_PAD_PER_USER=1 to split)");
  });
}

// X_INPUT_STATE (16 bytes): be32 packet; be16 buttons; u8 lt, rt; be16 lx, ly, rx, ry.
struct PadState {
  uint16_t buttons = 0;
  uint8_t triggers[2] = {};
  int16_t sticks[4] = {};
};

PadState Decode(const uint8_t* p) {
  PadState s;
  s.buttons = uint16_t(p[4] << 8 | p[5]);
  s.triggers[0] = p[6];
  s.triggers[1] = p[7];
  for (int i = 0; i < 4; ++i) s.sticks[i] = int16_t(p[8 + 2 * i] << 8 | p[9 + 2 * i]);
  return s;
}

void Encode(const PadState& s, uint32_t packet, uint8_t* p) {
  p[0] = uint8_t(packet >> 24);
  p[1] = uint8_t(packet >> 16);
  p[2] = uint8_t(packet >> 8);
  p[3] = uint8_t(packet);
  p[4] = uint8_t(s.buttons >> 8);
  p[5] = uint8_t(s.buttons);
  p[6] = s.triggers[0];
  p[7] = s.triggers[1];
  for (int i = 0; i < 4; ++i) {
    p[8 + 2 * i] = uint8_t(uint16_t(s.sticks[i]) >> 8);
    p[9 + 2 * i] = uint8_t(s.sticks[i]);
  }
}

// Scripted presses active now for `slot`; true if any.
bool Scripted(uint32_t slot, PadState& s) {
  if (!scripted) return false;
  const double now =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  bool any = false;
  std::lock_guard<std::mutex> lock(schedule_mutex);
  for (const Press& p : schedule) {
    if (p.slot != slot || now < p.at_seconds || now >= p.at_seconds + p.hold_seconds) continue;
    any = true;
    s.buttons |= p.buttons;
    for (int i = 0; i < 4; ++i) {
      if (p.sticks[i]) s.sticks[i] = p.sticks[i];
    }
    for (int i = 0; i < 2; ++i) {
      if (p.triggers[i]) s.triggers[i] = p.triggers[i];
    }
  }
  return any;
}

// One SDK slot's pad (plus the scripted presses for it). Calls the SDK with this slot in
// r3 and the game's buffer as scratch; false when nothing is connected there. A scripted
// run presents a connected, centred pad in slot 0 even without hardware (a disconnected
// pad pauses gameplay).
bool ReadSlot(GuestFunc sdk, PPCContext& ctx, uint8_t* base, uint32_t slot, uint32_t flags,
              uint32_t buffer, PadState& out) {
  const uint64_t r3 = ctx.r3.u64, r4 = ctx.r4.u64, r5 = ctx.r5.u64;
  ctx.r3.u64 = slot;
  ctx.r4.u64 = flags & ~kFlagAnyUser;
  ctx.r5.u64 = buffer;
  if (sdk) sdk(ctx, base);
  bool connected = sdk && ctx.r3.u32 == kErrorSuccess;
  ctx.r3.u64 = r3;
  ctx.r4.u64 = r4;
  ctx.r5.u64 = r5;
  out = connected && buffer ? Decode(GuestPtr(base, buffer)) : PadState{};
  // (Once the recording has ended the pad, and the debug server's presses, take over again.)
  if (slot == 0 && !replay.empty() && Now() <= replay.back().t + 0.5) {
    const double now = Now();
    auto it = std::upper_bound(replay.begin(), replay.end(), now,
                               [](double t, const Sample& s) { return t < s.t; });
    out = PadState{};
    if (it != replay.begin()) {
      const Sample& s = *(it - 1);
      out.buttons = s.buttons;
      std::memcpy(out.triggers, s.triggers, 2);
      std::memcpy(out.sticks, s.sticks, sizeof(out.sticks));
    }
  }
  const bool pressed = Scripted(slot, out);
  return connected || pressed || (scripted && slot == 0);
}

// Merges `in` into `acc`: buttons combined, the larger trigger and the stick pushed
// furthest win.
void Merge(PadState& acc, const PadState& in) {
  acc.buttons |= in.buttons;
  for (int i = 0; i < 2; ++i) acc.triggers[i] = std::max(acc.triggers[i], in.triggers[i]);
  for (int stick = 0; stick < 2; ++stick) {
    const int x = in.sticks[2 * stick], y = in.sticks[2 * stick + 1];
    const int ax = acc.sticks[2 * stick], ay = acc.sticks[2 * stick + 1];
    if (x * x + y * y > ax * ax + ay * ay) {
      acc.sticks[2 * stick] = int16_t(x);
      acc.sticks[2 * stick + 1] = int16_t(y);
    }
  }
}

bool RoutedUser(uint32_t user_index, uint32_t flags, uint32_t& user) {
  user = ((user_index & 0xFF) == 0xFF || (flags & kFlagAnyUser)) ? 0 : user_index;
  return user < kSlots;
}

}  // namespace

// DWORD XamInputGetState(DWORD user_index, DWORD flags, X_INPUT_STATE* state)
namespace forza::autopilot {
bool Drive(uint8_t* base, int16_t& steer, uint8_t& brake, uint8_t& throttle);
}

extern "C" REX_FUNC(__imp__XamInputGetState) {
  static GuestFunc sdk = Sdk("__imp__XamInputGetState");
  InitScripting();
  forza::wheel::UpdateDriving();  // the wheel's forces fade while the player is not driving (#256)
  const uint32_t flags = ctx.r4.u32, buffer = ctx.r5.u32;
  uint32_t user;
  if (!RoutedUser(ctx.r3.u32, flags, user)) {
    if (sdk) sdk(ctx, base);
    return;
  }
  PadState state;
  bool connected;
  if (PadPerUser()) {
    connected = ReadSlot(sdk, ctx, base, user, flags, buffer, state);
  } else if (user != 0) {
    connected = false;  // every pad is player 1
  } else {
    connected = false;
    for (uint32_t slot = 0; slot < kSlots; ++slot) {
      PadState pad;
      if (ReadSlot(sdk, ctx, base, slot, flags, buffer, pad)) {
        connected = true;
        Merge(state, pad);
      }
    }
  }
  if (user == 0 && record_file && connected) {
    static PadState last;
    static bool any = false;
    if (!any || std::memcmp(&last, &state, sizeof(state)) != 0) {
      any = true;
      last = state;
      std::fprintf(record_file, "%.4f %04x %u %u %d %d %d %d\n", Now(), state.buttons,
                   state.triggers[0], state.triggers[1], state.sticks[0], state.sticks[1],
                   state.sticks[2], state.sticks[3]);
      std::fflush(record_file);
    }
  }
  if (user == 0 && connected) {  // the dev autopilot (src/debug/autopilot.cpp)
    int16_t steer;
    uint8_t lt, rt;
    if (forza::autopilot::Drive(base, steer, lt, rt)) {
      state.sticks[0] = steer;
      state.triggers[0] = lt;
      state.triggers[1] = rt;
    }
  }
  if (user == 0 && forza::benchmark::WantsPad()) connected = true;
  const bool auto_pause = user == 0 && forza::auto_pause::Poll(
      base, state.buttons, PadPerUser() ? 1u : 15u);
  if (auto_pause) connected = true;
  if (!connected) {
    if (user == 0) forza::telemetry::Input(false, 0, 0, 0, 0);
    ctx.r3.u64 = kErrorDeviceNotConnected;
    return;
  }
  // The game's text prompts (kernel/xam_keyboard.cpp), the invite picker and prompt
  // (kernel/live_invites.cpp), the friends screen (kernel/live_friends.cpp) and head-to-head
  // challenges (hooks/world_challenge.cpp) watch player 1's pad and hide what they use from
  // the game.
  if (user == 0) {
    bool block_all = false;
    state.buttons &= uint16_t(
        ~forza::KeyboardUiPad(state.buttons, state.sticks[0], state.sticks[1], block_all));
    if (!block_all) {
      state.buttons &= uint16_t(~forza::LiveInvitePad(state.buttons, state.sticks[1], block_all));
    }
    if (!block_all) {  // the friends screen (kernel/live_friends.cpp)
      state.buttons &= uint16_t(
          ~forza::LiveFriendsPad(state.buttons, state.sticks[0], state.sticks[1], block_all));
    }
    if (block_all) state = PadState{};
    state.buttons &= uint16_t(~forza::WorldChallengePad(state.buttons));
    // SELECT on a won event's title card, and Custom Race's A for the card; nothing of it
    // while a prompt above holds the pad.
    if (!block_all) state.buttons = forza::CustomRacePad(state.buttons);
    if (forza::benchmark::WantsPad()) {
      bool capture = false;
      const auto buttons = forza::benchmark::Pad(state.buttons, capture);
      if (capture) state = PadState{};
      state.buttons = buttons;
    }
    if (auto_pause) {
      if (!block_all && !forza::benchmark::WantsPad()) {
        state = PadState{};
        state.buttons = 0x10;  // START: let FH1 open its own pause menu.
      } else {
        forza::auto_pause::Cancel();
      }
    }
  }
  if (user == 0) forza::xr::OnPad(state.buttons);  // VR: both sticks held recentre
  if (user == 0) forza::telemetry::Input(true, state.triggers[0], state.triggers[1], state.buttons, state.sticks[0]);
  if (buffer) {
    // The packet number changes whenever the state does.
    static std::mutex packet_mutex;
    static uint32_t packets[kSlots] = {};
    static uint8_t last[kSlots][16] = {};
    uint8_t bytes[16];
    std::lock_guard<std::mutex> lock(packet_mutex);
    Encode(state, packets[user], bytes);
    if (std::memcmp(bytes + 4, last[user] + 4, 12) != 0) {
      ++packets[user];
      Encode(state, packets[user], bytes);
      std::memcpy(last[user], bytes, 16);
    }
    std::memcpy(GuestPtr(base, buffer), bytes, 16);
  }
  ctx.r3.u64 = kErrorSuccess;
}

namespace {
// Capabilities of user 0: the first connected slot's (a scripted run always has one).
void Capabilities(GuestFunc sdk, PPCContext& ctx, uint8_t* base, forza::input_caps::Args args) {
  InitScripting();
  uint32_t user;
  if (!RoutedUser(args.user.u32, args.flags.u32, user) || PadPerUser()) {
    if (sdk) sdk(ctx, base);
    return;
  }
  if (user != 0) {
    ctx.r3.u64 = kErrorDeviceNotConnected;
    return;
  }
  if (sdk && forza::input_caps::FirstSlot(sdk, ctx, base, args, kSlots)) return;
  if (scripted || forza::benchmark::WantsPad()) {
    // A scripted run without hardware: the SDK's answer for slot 0 (its capabilities
    // struct is filled even when nothing is connected), reported as connected.
    if (sdk) sdk(ctx, base);
    ctx.r3.u64 = kErrorSuccess;
    return;
  }
  ctx.r3.u64 = kErrorDeviceNotConnected;
}
}  // namespace

// DWORD XamInputGetCapabilities(DWORD user_index, DWORD flags, X_INPUT_CAPABILITIES* caps)
extern "C" REX_FUNC(__imp__XamInputGetCapabilities) {
  static GuestFunc sdk = Sdk("__imp__XamInputGetCapabilities");
  Capabilities(sdk, ctx, base, forza::input_caps::CapabilitiesArgs(ctx));
}

// DWORD XamInputGetCapabilitiesEx(DWORD unk, DWORD user_index, DWORD flags, caps*)
extern "C" REX_FUNC(__imp__XamInputGetCapabilitiesEx) {
  static GuestFunc sdk = Sdk("__imp__XamInputGetCapabilitiesEx");
  Capabilities(sdk, ctx, base, forza::input_caps::CapabilitiesExArgs(ctx));
}

// DWORD XamInputSetState(DWORD user_index, DWORD unk, X_INPUT_VIBRATION* vibration)
extern "C" REX_FUNC(__imp__XamInputSetState) {
  static GuestFunc sdk = Sdk("__imp__XamInputSetState");
  uint32_t user;
  // Player one's rumble is how the trigger effects tell driving from a menu (#224).
  if (ctx.r5.u32 && RoutedUser(ctx.r3.u32, 0, user) && user == 0) {
    uint16_t motors[2];  // X_INPUT_VIBRATION, big endian: left, right
    std::memcpy(motors, GuestPtr(base, ctx.r5.u32), sizeof(motors));
    forza::telemetry::Rumble(__builtin_bswap16(motors[0]), __builtin_bswap16(motors[1]));
  }
  if (PadPerUser() || !RoutedUser(ctx.r3.u32, 0, user) || !sdk) {
    if (sdk) sdk(ctx, base);
    return;
  }
  if (user != 0) {
    ctx.r3.u64 = kErrorDeviceNotConnected;
    return;
  }
  // Rumble on every pad: whichever one is in the player's hands.
  const uint64_t saved = ctx.r3.u64;
  bool any = false;
  for (uint32_t slot = 0; slot < kSlots; ++slot) {
    ctx.r3.u64 = slot;
    sdk(ctx, base);
    any |= ctx.r3.u32 == kErrorSuccess;
    ctx.r3.u64 = saved;
  }
  ctx.r3.u64 = any || scripted ? kErrorSuccess : kErrorDeviceNotConnected;
}
