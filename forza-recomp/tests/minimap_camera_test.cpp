// Actual native adapter with sparse synthetic guest memory and original-call
// stand-ins. No game assets, process, renderer, or native guest calls.
#include <rex/cvar.h>
#include <rex/memory/utils.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include "../src/platform/guest_memory.h"
#include "../src/platform/minimap_heading.h"
#include "../src/platform/xr.h"
#include <array>
#include <cstdlib>
#include <cstring>
namespace {
std::array<uint8_t, 0x20000> memory{};
bool active_vr = false;
unsigned reads = 0, calls = 0, protection_queries = 0;
float drawn_x = 0, drawn_z = 0;
void Check(bool ok) { if (!ok) std::abort(); }
constexpr uint32_t service = 0x101000, entries = 0x102000, record = 0x103000,
    map = 0x104000, camera = 0x105000, world = 0x107000;
}
namespace forza {
uint8_t* FixtureGuestPtr(uint8_t* base, uint32_t address) {
  ++reads;
  Check(base == memory.data());
  if (address == 0x833F0410) return base;
  Check(address >= 0x100000 && address < 0x120000);
  return base + address - 0x100000;
}
}
bool forza_minimap_camera = false, vr_head_pose_test = false;
namespace forza::xr { bool CockpitActive() { return active_vr; } }
namespace rex::memory {
bool FixtureProtect(const void*, size_t& region, PageAccess& access) {
  ++protection_queries;
  region = 4096; access = PageAccess(3); return true;
}
size_t FixturePageSize() { return 4096; }
}
namespace rex::runtime {
auto FixtureResolve(uint32_t address) -> decltype(ResolveIndirectFunction(address));
}
#undef REXCVAR_DEFINE_BOOL
#define REXCVAR_DEFINE_BOOL(...)
#undef REXCVAR_DECLARE
#define REXCVAR_DECLARE(type, name) extern type name
#undef REXCVAR_GET
#define REXCVAR_GET(name) name
#undef REXLOG_INFO
#define REXLOG_INFO(...) ((void)0)
#define GuestPtr FixtureGuestPtr
#define QueryProtect FixtureProtect
#define page_size FixturePageSize
#define ResolveIndirectFunction FixtureResolve
#define FORZA_NATIVE_PAUSE 1
#include "../src/hooks/minimap_camera.cpp"
#undef GuestPtr
#undef QueryProtect
#undef page_size
#undef ResolveIndirectFunction
namespace {
uint8_t* At(uint32_t address) { return forza::FixtureGuestPtr(memory.data(), address); }
void Store(uint32_t address, uint32_t value) {
  auto* p = At(address); p[0] = uint8_t(value >> 24); p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8); p[3] = uint8_t(value);
}
void Rebuild(PPCContext& ctx, uint8_t* base) {
  Check(base == memory.data()); ++calls;
  if (ctx.r3.u32 == camera) {
    drawn_x = forza::camera::ReadFloat(At(camera + 0x20));
    drawn_z = forza::camera::ReadFloat(At(camera + 0x28));
    std::fill_n(At(camera + 0x50), 256, uint8_t(0x55));
  } else Check(ctx.r3.u32 == map && ctx.r4.u32 == camera);
  ctx.r3.u64 = 0x12345678;
}
void Setup() {
  memory.fill(0); active_vr = false; calls = 0;
  forza::minimap::previous_record = 0;
  Store(0x833F0410, service); Store(service + 0x74, entries); Store(service + 0x78, entries + 4);
  Store(entries, record); Store(record + 0x20, map); Store(record + 0x24, camera);
  At(record + 0x1C)[0] = 1; Store(map + 4, 0x106000);
  Store(camera + 0x28, std::bit_cast<uint32_t>(1.f));
  At(world + 0x0D)[0] = 1; Store(world + 0x28, std::bit_cast<uint32_t>(1.f));
  forza::camera::camera_world.store(world);
  std::array<float, 16> east{1,0,1,0, 0,1,0,0, 0,0,0,0, 0,0,0,1};
  forza::minimap::heading.Publish(east, world, forza::minimap::NowMs());
}
}
namespace rex::runtime {
auto FixtureResolve(uint32_t address) -> decltype(ResolveIndirectFunction(address)) {
  Check(address == 0x82DA2388 || address == 0x82DA4A80); return Rebuild;
}
}
int main() {
  using forza::minimap::MapScope;
  Check(!forza::minimap::Memory(nullptr, service, 4));
  Check(!forza::minimap::Memory(memory.data(), 0xFFFFF, 4));
  Check(!forza::minimap::Memory(memory.data(), 0xF0000000, 0));
  Check(!forza::minimap::Memory(memory.data(), 0xEFFFFFFC, 8));
  Check(!forza::minimap::Memory(memory.data(), service, size_t(-1)));
  Setup(); PPCContext ctx{}; ctx.r1.u64 = 0x2222; ctx.r3.u64 = service;
  auto before_context = ctx;
  reads = 0;
  { MapScope scope(ctx, memory.data()); }
  Check(!reads && !calls); // disabled is inert
  forza_minimap_camera = true;
  std::array<uint8_t, 0x150> saved;
  std::copy_n(At(camera), saved.size(), saved.begin());
  { MapScope scope(ctx, memory.data()); Check(calls == 2 && drawn_x == 1 && drawn_z == 0); }
  Check(!std::memcmp(At(camera), saved.data(), saved.size()));
  Check(!std::memcmp(&ctx, &before_context, sizeof(ctx)));
  forza_minimap_camera = false; calls = 0;
  { MapScope scope(ctx, memory.data()); }
  Check(calls == 2 && drawn_x == 0 && drawn_z == 1); // toggle restores stock geometry
  calls = 0; { MapScope scope(ctx, memory.data()); } Check(!calls);
  for (unsigned reason = 0; reason < 6; ++reason) {
    Setup(); forza_minimap_camera = true;
    if (reason == 0) At(world + 0x0C)[0] = 1;
    if (reason == 1) active_vr = true;
    if (reason == 2) Store(record, 2); // pause-preview/full-map record
    if (reason == 3) ctx.r4.u64 = 1; // not phase zero
    if (reason == 4) Store(record + 0x24, 0xEFFFFFF0); // camera extent outside guest range
    if (reason == 5) forza::minimap::heading.Publish({}, world, forza::minimap::NowMs());
    { MapScope scope(ctx, memory.data()); } Check(!calls);
    ctx.r4.u64 = 0;
  }
  Setup(); forza_minimap_camera = true;
  { MapScope scope(ctx, memory.data()); At(camera + 0x50)[0] = 0x77; }
  Check(At(camera + 0x50)[0] == 0x77); // a newer native write wins
  Check(protection_queries == 0); // no OS memory-map queries in any adapter path
}
