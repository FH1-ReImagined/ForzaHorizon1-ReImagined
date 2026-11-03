// Apply pause-only mesh edges at FH1's final native transform boundary.
// tu_address_map: TU4 addresses
#include "../platform/guest_addr.h"
#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include "../platform/native_pause_edges.h"
#include "../platform/fh1_menu_components.h"
#include "../platform/guest_memory.h"
#include <rex/ppc/func.h>
#include <rex/runtime.h>
#include <array>
#include <atomic>
#include <bit>

extern "C" REX_FUNC(__imp__sub_82FE6238);
namespace forza::native_pause_edges {
namespace {
std::array<std::atomic<uint32_t>,9> meshes{};
std::atomic<uint32_t> active{0};
void Set(size_t slot,uint32_t instance){
  meshes[slot].store(instance,std::memory_order_release);
  if(instance)active.fetch_or(1u<<slot,std::memory_order_release);
  else active.fetch_and(~(1u<<slot),std::memory_order_release);
}
uint32_t Read(uint8_t* base,uint32_t address){
  const auto p=GuestPtr(base,address);
  return uint32_t(p[0])<<24|uint32_t(p[1])<<16|uint32_t(p[2])<<8|p[3];
}
void Write(uint8_t* base,uint32_t address,float value){
  auto bits=std::bit_cast<uint32_t>(value);auto p=GuestPtr(base,address);
  for(int i=3;i>=0;--i){p[i]=uint8_t(bits);bits>>=8;}
}
}
void MapMesh(uint32_t instance){Set(0,instance);}
void TabMeshes(size_t slot,uint32_t paper,uint32_t grain){
  if(slot<4){Set(1+slot*2,paper);Set(2+slot*2,grain);}
}
void ClearTabs(){for(size_t i=1;i<meshes.size();++i)Set(i,0);}
bool Apply(PPCContext& context,uint8_t* base){
  const auto mask=active.load(std::memory_order_acquire),instance=context.r4.u32;
  if(!mask||!instance)return false;
  bool found=false;
  for(size_t i=0;i<meshes.size();++i)
    if((mask&(1u<<i))&&meshes[i].load(std::memory_order_acquire)==instance){found=true;break;}
  if(!found||GuestPtr(base,instance+0xA)[0]!=2)return false;
  // Rebuild from authored properties every time: animation and repeated native
  // visits must neither erase the boundary nor accumulate its shear.
  auto call=context;
  call.r3.u64=instance+0x3C;call.r4.u64=instance+0xC;call.r5.u64=instance+0x18;
  call.r6.u64=instance+0x24;call.r7.u64=instance+0x30;
  call.r8.u64=GuestPtr(base,instance+0xF9)[0];call.r9.u64=GuestPtr(base,instance+0xF8)[0];
  rex::runtime::ResolveIndirectFunction(0x82FE4368)(call,base);
  for(unsigned column=0;column<3;++column){
    const auto address=instance+0x3C+column*16;
    Write(base,address,std::bit_cast<float>(Read(base,address))+
        fh1_menu_components::kMenuShear*std::bit_cast<float>(Read(base,address+4)));
  }
  auto flags=GuestPtr(base,instance+0xFB);
  *flags=(*flags&~0x20)|0x10; // Retain our local matrix; rebuild native world data.
  return true;
}
}
extern "C" REX_FUNC(sub_82FE6238){
  forza::native_pause_edges::Apply(ctx,base);
  __imp__sub_82FE6238(ctx,base);
}
#endif
