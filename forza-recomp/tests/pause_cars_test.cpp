// Exercise the production Open gate and selection hooks with a sparse guest
// memory fixture and mocked native calls. No game files, SDK, save or graphics.
#include "../src/platform/offline_cars.h"
#include <array>
#include <cassert>
#include <map>
#include <string_view>
#include <vector>

namespace {
std::map<uint32_t,std::array<uint8_t,4096>> pages;
bool known = true, network = false, session = false;
uint32_t presence = 1, region = 0, current_car = 7, selected_car = 8;
uint32_t dispatch_address = 0;
std::vector<uint32_t> calls;
constexpr uint32_t parent = 0x10000, owner = 0x20000, manager = 0x30000;
constexpr uint32_t table = 0x40000, open = 0x50000, child = 0x60000;
constexpr uint32_t reload = 0x833F6CA4;
void Word(uint32_t address, uint32_t value) {
  auto* p = TestGuestPtr(address);
  for (int i = 3; i >= 0; --i) { p[i] = uint8_t(value); value >>= 8; }
}
void Dispatch(PPCContext& ctx,uint8_t*) {
  switch (dispatch_address) {
    case 0x824A3770: ctx.r3.u64 = manager; break;
    case open:
      assert(ctx.r3.u32 == manager && ctx.r4.u32 == owner);
      assert(ctx.r5.u32 == forza::offline_cars::kMyCarsScreen);
      break;
    case 0x82753318: ctx.r3.u64 = 0x70000; break;
    case 0x825979B8: ctx.r3.u64 = current_car; break;
    case 0x824CEAB0: Word(ctx.r3.u32,0); break;
    default: assert(false);
  }
}
void Reset() {
  pages.clear(); calls.clear();
  known = true; network = session = false; presence = 1; region = 0;
  current_car = 7; selected_car = 8;
  forza::offline_cars::pending = false;
  forza::offline_cars::active_screen = 0;
  forza::offline_cars::reload_intent.Clear();
  Word(parent,0x82061F94); Word(parent+0x10C,owner);
  Word(manager,table); Word(table+0x4C,open);
  Word(child,0x8205F254); Word(child+0x10C,owner);
}
}
uint8_t* TestGuestPtr(uint32_t address) { return pages[address>>12].data()+(address&4095); }
namespace forza {
bool NetworkOn() { return network; }
bool LiveLocalActivity(uint32_t& p,uint32_t& r) { p=presence;r=region;return known; }
std::optional<LiveSessionView> LiveCurrentSession() {
  return session ? std::optional<LiveSessionView>{LiveSessionView{}} : std::nullopt;
}
}
namespace rex::runtime {
GuestFunction ResolveIndirectFunction(uint32_t address) {
  calls.push_back(address); dispatch_address=address; return Dispatch;
}
}
extern "C" REX_FUNC(__imp__sub_827683D0) { (void)ctx;(void)base; }
extern "C" REX_FUNC(__imp__sub_827C3620) { (void)ctx;(void)base; }
extern "C" REX_FUNC(__imp__sub_82789108) { (void)ctx;(void)base;current_car=selected_car; }
extern "C" REX_FUNC(sub_827683D0);
extern "C" REX_FUNC(sub_827C3620);
extern "C" REX_FUNC(sub_82789108);

int main() {
  using namespace forza::offline_cars;
  uint8_t memory = 0;
  PPCContext ctx;
  // Opening must re-check live state even after a previously available tile.
  for (int gate=0;gate<5;++gate) {
    Reset(); assert(Available(&memory,parent));
    switch(gate) {
      case 0: known=false;break;
      case 1: presence=2;break;
      case 2: region=1;break;
      case 3: network=true;break;
      case 4: session=true;break;
    }
    assert(!Available(&memory,parent));
    assert(!Open(ctx,&memory,parent)); assert(calls.empty() && !pending);
  }
  Reset(); assert(!Open(ctx,&memory,0)); assert(!Open(ctx,nullptr,parent));
  // Selection hooks leave the guest reload byte untouched until Resume.
  auto Select = [&] {
    assert(Open(ctx,&memory,parent));
    ctx.r3.u64=child; sub_827683D0(ctx,&memory);
    assert(active_screen==child && !pending);
    ctx.r3.u64=child; sub_82789108(ctx,&memory);
    assert(TestGuestPtr(reload)[0]==0);
    ctx.r3.u64=child; sub_827C3620(ctx,&memory); // Back to pause.
    assert(!active_screen);
  };
  Reset(); Select(); PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==1);
  PrepareResume(&memory,parent); assert(TestGuestPtr(reload)[0]==0); // Consumed once.
  using A = forza::menu::PauseAction;
  for (auto exit : {A::WorldMap,A::PhotoMode,A::FreeRoam,A::CreateGame,
                    A::LeaveSession,A::Graphics,A::Audio}) {
    Reset(); Select();
    BeforeTransition(&memory,exit); // Same boundary used by Activate.
    PrepareResume(&memory,parent); assert(TestGuestPtr(reload)[0]==0);
  }
  Reset(); Select(); BeforeTransition(&memory,A::ChangeCar); PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==1); // Revisiting the selector retains this visit's intent.
  Reset(); Select(); network=true; PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==0); // Network state changed after selection.
  Reset(); Select(); session=true; PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==0);
  Reset(); Select(); Word(parent+0x10C,owner+1); PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==0); // Another pause context cannot consume it.
  Reset(); selected_car=current_car; Select(); PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==0); // No-op selection.
  Reset(); TestGuestPtr(reload)[0]=1; PrepareResume(&memory,parent);
  assert(TestGuestPtr(reload)[0]==0); // A stale stock flag is not our intent.
}
