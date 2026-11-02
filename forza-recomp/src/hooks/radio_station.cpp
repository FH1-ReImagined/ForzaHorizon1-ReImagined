// forza - quicker radio station changes (#401).
//
// A press of the radio buttons (d-pad left/right, actions RadioLeft/RadioRight) goes to
// sub_824FABD8 (TU4 sub_82566078), which asks sub_82596958 (TU4 sub_8260D7D0) whether the
// station may change. That says no while the player's radio cooldown (+0xA4, a float the
// player's update counts down by the frame time) runs, and while the radio HUD (a
// CHudStatusRadio: the station logo and track name) is up. A change sets the cooldown to
// 1 s; the HUD comes up with the new station, about 0.6 s after the press, and stays
// about 2.5 s, so the next change can come 3 to 3.5 s after the first. The station index
// goes to CAudioManagerDeferred, then RadioSystem::CRadioSystem. The native normal
// fades are 0.5 s each; the user rate is 0.25 s but its selector (+0x164) is not
// set by this path. Historical stock and custom station logs on 2026-10-28 show
// 11-14 ms admission-to-audio-request, 508-560 ms admission-to-new-stream state,
// and 960-1007 ms until the fade settles. Those state logs do not isolate stream
// load/decode time or measure the first audible sample. The original drops
// requests while fading out and switches immediately during fade-in.
//
// radio_station_switch=fast keeps the existing 150 ms repeat guard and 250 ms ramps.
// instant uses a 75 ms repeat guard and 40 ms station-only ramps. Both accept the
// newest request during a ramp and retain the native zero-volume stream handoff.
// The short repeat guard also bounds bindings configured to repeat while held.
// No global level, ducking, speech, or event fade is changed. Stock and custom
// stations use this same native path. Actual output latency includes the audio
// queue, stream startup and mixer; "instant" does not mean zero latency.
// game (the default) preserves the original behavior. FORZA_RADIOLOG=1 logs
// admission, station requests and native fade-state changes.

// tu_address_map: TU4 addresses
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

#if FORZA_TU4
#define RadioChangeAllowed sub_8260D7D0
#define OriginalRadioChangeAllowed __imp__sub_8260D7D0
#define ChangeStation sub_82C99A98
#define OriginalChangeStation __imp__sub_82C99A98
#define UpdateStationFade sub_82C99B98
#define OriginalUpdateStationFade __imp__sub_82C99B98
#else
#define RadioChangeAllowed sub_82596958
#define OriginalRadioChangeAllowed __imp__sub_82596958
#define ChangeStation sub_82BDA720
#define OriginalChangeStation __imp__sub_82BDA720
#define UpdateStationFade sub_82BDA820
#define OriginalUpdateStationFade __imp__sub_82BDA820
#endif

REXCVAR_DEFINE_STRING(radio_station_switch, "game", "Audio",
                      "Radio station changes: game, fast (150 ms repeat guard, 250 ms ramps), "
                      "or instant (75 ms repeat guard, 40 ms ramps)");

// RadioChangeAllowed(player): bool. Says yes when the cooldown is 0 and no radio HUD is up,
// and then sets the cooldown to 1 s.
extern "C" REX_FUNC(OriginalRadioChangeAllowed);
// ChangeStation(radio, name, user, force, ?): starts the fade to a station (user: a player's
// change). Ignored unless the radio is idle or fading in.
extern "C" REX_FUNC(OriginalChangeStation);
// UpdateStationFade(radio, f1 = frame time): the station change's fade, once a frame.
extern "C" REX_FUNC(OriginalUpdateStationFade);

namespace {

// Player (the radio press handler's object).
constexpr uint32_t kCooldown = 0xA4;  // float, seconds
// CRadioSystem
constexpr uint32_t kFadeLevel = 0x160;     // float, 0..1
constexpr uint32_t kUserFadeFlag = 0x164;  // byte: fade at the user rate (+0x224), else +0x220
constexpr uint32_t kUserFadeRate = 0x224;  // float, reciprocal seconds
constexpr uint32_t kStationLock = 0x7C;   // nonzero rejects unforced changes
constexpr uint32_t kChangeUser = 0x21C;    // byte: the pending change is a player's
constexpr uint32_t kFadeState = 0x22C;     // int
constexpr uint32_t kIdle = 0, kFadingOut = 1, kFadingIn = 3;

constexpr float kFastCooldown = 0.15f;
constexpr float kInstantCooldown = 0.075f;
constexpr float kInstantFadeRate = 1.0f / 0.04f;

enum class SwitchMode { Game, Fast, Instant };
SwitchMode Mode() {
  static const SwitchMode mode = [] {
    const std::string value = REXCVAR_GET(radio_station_switch);
    if (value == "instant") return SwitchMode::Instant;
    if (value == "fast") return SwitchMode::Fast;
    return SwitchMode::Game;
  }();
  return mode;
}
bool Quicker() { return Mode() != SwitchMode::Game; }

bool Log() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_RADIOLOG");
    return v && *v && *v != '0';
  }();
  return on;
}

double Seconds() {
  static const auto start = std::chrono::steady_clock::now();
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

uint32_t Load32(uint8_t* base, uint32_t address) {
  uint32_t v;
  std::memcpy(&v, forza::GuestPtr(base, address), 4);
  return __builtin_bswap32(v);
}
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  value = __builtin_bswap32(value);
  std::memcpy(forza::GuestPtr(base, address), &value, 4);
}
float LoadFloat(uint8_t* base, uint32_t address) {
  const uint32_t v = Load32(base, address);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}
void StoreFloat(uint8_t* base, uint32_t address, float value) {
  uint32_t v;
  std::memcpy(&v, &value, 4);
  Store32(base, address, v);
}

}  // namespace

extern "C" REX_FUNC(RadioChangeAllowed) {
  const uint32_t player = ctx.r3.u32;
  const float cooldown = LoadFloat(base, player + kCooldown);
  OriginalRadioChangeAllowed(ctx, base);
  bool allowed = (ctx.r3.u32 & 0xFF) != 0;
  const bool hud = !allowed && cooldown <= 0.0f;  // only the radio HUD said no
  if (Quicker()) {
    if (hud) allowed = true;
    if (allowed) {
      StoreFloat(base, player + kCooldown,
                 Mode() == SwitchMode::Instant ? kInstantCooldown : kFastCooldown);
      ctx.r3.u64 = 1;
    }
  }
  if (Log())
    REXLOG_INFO("RADIOLOG {:.3f} press: {}{}", Seconds(), allowed ? "station changes" : "ignored",
                allowed ? "" : (hud ? " (radio HUD up)" : " (cooldown)"));
}

extern "C" REX_FUNC(ChangeStation) {
  const uint32_t radio = ctx.r3.u32;
  const bool user = (ctx.r5.u32 & 0xFF) != 0;
  const uint32_t state = Load32(base, radio + kFadeState);
  // The game takes a change only while idle or fading in, and fading in it switches at once.
  // Quicker modes: seen as idle, the change fades out from the current level to the newest station.
  const bool locked = Load32(base, radio + kStationLock) != 0 && (ctx.r6.u32 & 0xFF) == 0;
  const bool retarget = Quicker() && user && !locked &&
                        (state == kFadingOut || state == kFadingIn);
  if (retarget) Store32(base, radio + kFadeState, kIdle);
  OriginalChangeStation(ctx, base);
  uint32_t now = Load32(base, radio + kFadeState);
  // Nothing new to load (the station that plays, or one the radio refuses): fade (back) in.
  if (retarget && now == kIdle) Store32(base, radio + kFadeState, now = kFadingIn);
  if (Log())
    REXLOG_INFO("RADIOLOG {:.3f} change station (user {}): fade state {} -> {}, level {:.2f}", Seconds(),
                int(user), state, now, LoadFloat(base, radio + kFadeLevel));
}

extern "C" REX_FUNC(UpdateStationFade) {
  const uint32_t radio = ctx.r3.u32;
  uint8_t* user_rate = forza::GuestPtr(base, radio + kUserFadeFlag);
  const uint8_t saved = *user_rate;
  const uint32_t state = Log() ? Load32(base, radio + kFadeState) : 0;
  const bool player_change = Quicker() && *forza::GuestPtr(base, radio + kChangeUser);
  const uint32_t saved_rate = Load32(base, radio + kUserFadeRate);
  if (player_change) {
    *user_rate = 1;
    if (Mode() == SwitchMode::Instant)
      StoreFloat(base, radio + kUserFadeRate, kInstantFadeRate);
  }
  OriginalUpdateStationFade(ctx, base);
  *user_rate = saved;
  if (player_change && Mode() == SwitchMode::Instant)
    Store32(base, radio + kUserFadeRate, saved_rate);
  if (Log()) {
    const uint32_t now = Load32(base, radio + kFadeState);
    if (now != state)
      REXLOG_INFO("RADIOLOG {:.3f} fade state {} -> {}{}", Seconds(), state, now,
                  state == kFadingOut ? " (new station started)" : "");
  }
}
