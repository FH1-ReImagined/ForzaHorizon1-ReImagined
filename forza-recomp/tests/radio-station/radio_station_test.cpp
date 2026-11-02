// Executes the production wrappers. Native callees are a deterministic contract
// fixture, not FMOD or an audible-output test; runtime listening remains required.
#include "platform/guest_addr.h"
#include <rex/ppc/func.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#if FORZA_TU4
#define Allowed sub_8260D7D0
#define OriginalAllowed __imp__sub_8260D7D0
#define Change sub_82C99A98
#define OriginalChange __imp__sub_82C99A98
#define Fade sub_82C99B98
#define OriginalFade __imp__sub_82C99B98
#else
#define Allowed sub_82596958
#define OriginalAllowed __imp__sub_82596958
#define Change sub_82BDA720
#define OriginalChange __imp__sub_82BDA720
#define Fade sub_82BDA820
#define OriginalFade __imp__sub_82BDA820
#endif
extern std::string radio_station_switch;
extern "C" REX_FUNC(Allowed);
extern "C" REX_FUNC(Change);
extern "C" REX_FUNC(Fade);

namespace {
constexpr uint32_t player = 0x100, radio = 0x400;
std::array<uint8_t, 4096> memory{};
bool hud = false;
int admissions = 0, requests = 0, releases = 0;
std::vector<uint32_t> starts;
uint32_t Read(uint32_t a) {
  uint32_t n; std::memcpy(&n, memory.data() + a, 4); return __builtin_bswap32(n);
}
void Write(uint32_t a, uint32_t n) {
  n = __builtin_bswap32(n); std::memcpy(memory.data() + a, &n, 4);
}
float Float(uint32_t a) { auto n = Read(a); float f; std::memcpy(&f, &n, 4); return f; }
void Float(uint32_t a, float f) { uint32_t n; std::memcpy(&n, &f, 4); Write(a, n); }
void Reset() {
  memory.fill(0); starts.clear(); hud = false;
  Write(radio + 0xA0, 1); Write(radio + 0x228, 1);
  Float(radio + 0x160, 1); Float(radio + 0x220, 2); Float(radio + 0x224, 4);
}
bool Press() { PPCContext c; c.r3.u64 = player; Allowed(c, memory.data()); return c.r3.u64 != 0; }
void Request(uint32_t station, bool user = true, bool force = false) {
  PPCContext c; c.r3.u64 = radio; c.r4.u64 = station;
  c.r5.u64 = user; c.r6.u64 = force; c.r7.u64 = 0xAB;
  Change(c, memory.data());
}
void Tick(float dt) {
  const auto flag = memory[radio + 0x164]; const auto rate = Read(radio + 0x224);
  PPCContext c; c.r3.u64 = radio; c.f1.f64 = dt; Fade(c, memory.data());
  assert(memory[radio + 0x164] == flag && Read(radio + 0x224) == rate);
  assert(Float(radio + 0x220) == 2);  // normal/event rate never touched
}
void Settle() { for (int i = 0; i < 200 && Read(radio + 0x22C); ++i) Tick(0.01f); }
}

extern "C" REX_FUNC(OriginalAllowed) {
  assert(base == memory.data() && ctx.r3.u32 == player); ++admissions;
  const bool ok = !hud && Float(player + 0xA4) == 0;
  if (ok) Float(player + 0xA4, 1);
  ctx.r3.u64 = ok;
}
extern "C" REX_FUNC(OriginalChange) {
  assert(base == memory.data() && ctx.r3.u32 == radio && ctx.r7.u32 == 0xAB);
  ++requests; ++releases;  // original owns and releases its name argument
  const auto state = Read(radio + 0x22C), target = ctx.r4.u32;
  if ((state == 0 || state == 3) && (!Read(radio + 0x7C) || ctx.r6.u32) &&
      target != Read(radio + 0xA0)) {
    Write(radio + 0x228, target); memory[radio + 0x21C] = uint8_t(ctx.r5.u32);
    Write(radio + 0x22C, 1);
  }
  ctx.r3.u64 = 0xDEAD;  // wrapper must retain its own this pointer
}
extern "C" REX_FUNC(OriginalFade) {
  assert(base == memory.data() && ctx.r3.u32 == radio);
  const auto rate = Float(radio + (memory[radio + 0x164] ? 0x224 : 0x220));
  const float delta = rate * float(ctx.f1.f64);
  if (Read(radio + 0x22C) == 1) {
    Float(radio + 0x160, std::max(0.f, Float(radio + 0x160) - delta));
    if (Float(radio + 0x160) == 0) {
      Write(radio + 0xA0, Read(radio + 0x228)); starts.push_back(Read(radio + 0xA0));
      Write(radio + 0x22C, 3);
    }
  }
  if (Read(radio + 0x22C) == 3) {
    Float(radio + 0x160, std::min(1.f, Float(radio + 0x160) + delta));
    if (Float(radio + 0x160) == 1) Write(radio + 0x22C, 0);
  }
  ctx.r3.u64 = 0xBEEF;
}

int main(int argc, char** argv) {
  assert(argc == 2); radio_station_switch = argv[1];
  const bool quicker = radio_station_switch == "fast" || radio_station_switch == "instant";
  const bool instant = radio_station_switch == "instant";
  Reset(); assert(Press());
  assert(Float(player + 0xA4) == (instant ? .075f : quicker ? .15f : 1.f));
  assert(!Press());  // held/repeated admission remains bounded
  Float(player + 0xA4, 0); hud = true; assert(Press() == quicker);
  assert(admissions == 3);  // original HUD lifetime/refcount work always executes

  Reset(); Request(2); Tick(.01f);
  const auto wanted = instant ? .75f : quicker ? .96f : .98f;
  assert(std::abs(Float(radio + 0x160) - wanted) < .00001f);
  Settle(); assert((starts == std::vector<uint32_t>{2}));

  Reset(); Request(2, false); Tick(.01f);
  assert(std::abs(Float(radio + 0x160) - .98f) < .00001f);  // scripted fades unchanged

  Reset(); Request(2); Tick(.01f); Request(3); Settle();
  assert((starts == std::vector<uint32_t>{quicker ? 3u : 2u}));
  if (quicker) {
    Reset(); Request(2); Tick(.01f); Request(1); Settle();
    assert(starts.empty() && Read(radio + 0xA0) == 1);  // reverse back to current
    Reset(); Request(2); while (Read(radio + 0x22C) == 1) Tick(.01f);
    const auto level = Float(radio + 0x160); Request(0);  // native Off slot stand-in
    assert(Float(radio + 0x160) == level && Read(radio + 0x22C) == 1);
    Settle(); assert((starts == std::vector<uint32_t>{2, 0}));
    Request(3); Settle(); assert(Read(radio + 0xA0) == 3);  // leave Off normally
    Reset(); Request(2); Tick(.01f); Write(radio + 0x7C, 1); Request(3);
    assert(Read(radio + 0x22C) == 1 && Read(radio + 0x228) == 2);
    Request(3, true, true); Settle(); assert(Read(radio + 0xA0) == 3);
  }
  assert(requests == releases);
  std::cout << "Production radio wrapper checks passed: " << argv[1] << '\n';
}
