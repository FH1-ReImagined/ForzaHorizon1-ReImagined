// forza - the crash in races (#83): the loader thread released a resource's data object
// that was already freed.
//
// sub_82C222C8 (TU4 sub_82CE2A38) is the cacheable resources' load step (vtable slot 22 of
// CCacheableResource, CTextureResource, CTrackModelResource and the other track and texture
// resources). The loader thread (sub_82F0CF00 -> sub_82F0BEE0 -> sub_82F0B690 ->
// sub_82F0B2D0) calls it; it runs the resource's own load (slot 15), then takes the data
// object at resource+0x40, clears the field and calls the object's Release (vtable +12).
// The unload of a resource (slot 16, e.g. sub_82CE1E30 / sub_82CE3000 in TU4) takes and
// releases the same field the same way, without a lock. In the player's log the object
// at +0x40 had a vtable pointer of 0x0AF048AC (its memory reused), and the Release read
// faulted.
//
// The load and unload hooks (hints/resource_data_release.toml) replace every
// matching take in these resource vtables: the field is exchanged
// with 0 in one atomic step, so two threads cannot both take the same object, and an
// object whose vtable is not in the executable's image is logged with the resource's class
// and dropped instead of released (it is gone already; calling into it faulted).

#include "../platform/guest_memory.h"
#include "resource_data_take.h"

#include <rex/image_info.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

extern const rex::PPCImageInfo PPCImageConfig;

static_assert(offsetof(PPCContext, r3) == 0, "hooks recover ctx from &ctx.r3");

namespace {

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

bool Readable(uint32_t address, uint32_t size) {
  auto* memory = rex::system::kernel_state()->memory();
  auto* heap = memory->LookupHeap(address);
  uint32_t protect = 0;
  return heap && heap->QueryProtect(address, &protect) &&
         (protect & rex::memory::kMemoryProtectRead) &&
         heap->QueryProtect(address + size - 1, &protect) &&
         (protect & rex::memory::kMemoryProtectRead);
}

bool InImage(uint32_t a) {
  return a - PPCImageConfig.image_base < PPCImageConfig.image_size;
}

bool InCode(uint32_t a) { return a - PPCImageConfig.code_base < PPCImageConfig.code_size; }

// A live object: readable, its vtable in the image, the vtable's Release in the code.
bool LiveObject(uint8_t* base, uint32_t object) {
  if ((object & 3) || !Readable(object, 4)) return false;
  const uint32_t vtable = Load32(forza::GuestPtr(base, object));
  if ((vtable & 3) || !InImage(vtable) || !InImage(vtable + 15)) return false;
  return InCode(Load32(forza::GuestPtr(base, vtable + 12)));
}

// The class name from MSVC RTTI (vtable[-1] -> complete object locator, +12 -> type
// descriptor, +8 -> ".?AVName@@"), or "?".
std::string ClassName(uint8_t* base, uint32_t object) {
  if ((object & 3) || !Readable(object, 4)) return "?";
  const uint32_t vtable = Load32(forza::GuestPtr(base, object));
  if (!InImage(vtable - 4) || !InImage(vtable)) return "?";
  const uint32_t locator = Load32(forza::GuestPtr(base, vtable - 4));
  if (!InImage(locator) || !InImage(locator + 15)) return "?";
  const uint32_t type = Load32(forza::GuestPtr(base, locator + 12));
  if (!InImage(type) || !InImage(type + 8 + 80)) return "?";
  std::string name;
  for (const uint8_t* p = forza::GuestPtr(base, type + 8); *p && name.size() < 80; ++p) {
    name += *p >= 32 && *p < 127 ? char(*p) : '?';
  }
  return name;
}

std::atomic<int> reports{0};

uint32_t TakeResourceData(uint32_t resource) {
  uint8_t* base = rex::system::kernel_state()->memory()->virtual_membase();
  auto* field = reinterpret_cast<uint32_t*>(forza::GuestPtr(base, resource + 0x40));
  uint32_t data = forza::resource::TakeData(*field);
  if (data && !LiveObject(base, data)) {
    const int n = reports.fetch_add(1);
    if (n < 10) {
      const bool readable = !(data & 3) && Readable(data, 16);
      REXLOG_ERROR("RESOURCE: {} {:08X} (vtable {:08X}) held a freed data object {:08X} at +0x40 "
                   "(words {:08X} {:08X} {:08X} {:08X}); not released (#83)",
                   ClassName(base, resource), resource,
                   Readable(resource, 4) ? Load32(forza::GuestPtr(base, resource)) : 0, data,
                   readable ? Load32(forza::GuestPtr(base, data)) : 0,
                   readable ? Load32(forza::GuestPtr(base, data + 4)) : 0,
                   readable ? Load32(forza::GuestPtr(base, data + 8)) : 0,
                   readable ? Load32(forza::GuestPtr(base, data + 12)) : 0);
    }
    data = 0;
  }
  return data;
}

// Unload replaces lwz r3,64(r3); li rN,0; cmplwi cr6,r3,0; stw rN,64(rSaved).
// The preceding mr already saved the resource in r11/r31. Keep that register,
// reproduce the zero register and CR6, then resume at the original conditional
// branch. Skipping the store matters too: it must not clear newly published data.
template <PPCRegister PPCContext::* Zero>
void UnloadResourceData(PPCRegister& r3) {
  PPCContext& ctx = *reinterpret_cast<PPCContext*>(&r3);
  ctx.r3.u64 = TakeResourceData(ctx.r3.u32);
  (ctx.*Zero).u64 = 0;
  ctx.cr6.compare<uint32_t>(ctx.r3.u32, 0, ctx.xer);
}

}  // namespace

// Load's take also saves its return value in r31 and zeros r10. Resume at r11's
// original null check; neither the virtual Release nor the load result changes.
void ForzaTakeResourceData_82C222F0(PPCRegister& r3) {
  PPCContext& ctx = *reinterpret_cast<PPCContext*>(&r3);
  ctx.r31.u64 = ctx.r3.u64;
  ctx.r10.u64 = 0;
  ctx.r11.u64 = TakeResourceData(ctx.r30.u32);
}

void ForzaUnloadResourceDataR10(PPCRegister& r3) { UnloadResourceData<&PPCContext::r10>(r3); }
void ForzaUnloadResourceDataR11(PPCRegister& r3) { UnloadResourceData<&PPCContext::r11>(r3); }
void ForzaUnloadResourceDataR29(PPCRegister& r3) { UnloadResourceData<&PPCContext::r29>(r3); }
void ForzaUnloadResourceDataR30(PPCRegister& r3) { UnloadResourceData<&PPCContext::r30>(r3); }
