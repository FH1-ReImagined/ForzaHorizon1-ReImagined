#include "native_quit.h"
#include "guest_addr.h"
#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include "native_quit_flow.h"
#include "fh1_menu_widgets.h"
#include "fh1_pause_actions.h"
#include "../kernel/live.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <mutex>

extern "C" REX_FUNC(__imp__sub_827EA788);
extern "C" REX_FUNC(__imp__sub_82681628);
extern "C" REX_FUNC(__imp__sub_827041C8);
extern "C" REX_FUNC(__imp__sub_82583868);
extern "C" REX_FUNC(__imp__sub_82D228C8);
extern "C" REX_FUNC(__imp__sub_82B2BD00);
extern "C" REX_FUNC(__imp__sub_82B2AFD0);
extern "C" REX_FUNC(__imp__sub_82A3DB58);

namespace forza::native_quit {
namespace {
using namespace fh1_menu_widgets;
// CSaveProfile (829F4B80): lis r11,-31939; lwz r3,-21812(r11).
constexpr uint32_t kSaveManager=0x833CAACC;
std::mutex mutex;
Flow flow;
std::function<void(uint64_t)> host;
thread_local bool constructing=false;
thread_local uint64_t manager_generation=0,profile_generation=0,commit_generation=0;
thread_local uint32_t profile_token=0,volume_token=0,commit_volume=0;
template<class T> struct Scoped {
  T& slot;T previous;
  Scoped(T& target,T value):slot(target),previous(target){slot=value;}
  ~Scoped(){slot=previous;}
};
bool Enabled(){return rex::cvar::GetFlagByName("forza_native_pause")=="true";}
bool Offline(){return !NetworkOn()&&!LiveCurrentSession();}
// Use the native vector's copy constructors/destructor; its 44-byte entries
// contain owned string references and synchronization state, never memcpy them.
bool ThreeChoices(Native& ui,uint32_t begin,uint32_t end,uint32_t& vector) {
  if(!ui||!begin||end<begin||end-begin!=88||Read(ui.base,begin)!=0||
     Read(ui.base,begin+4)!=0||Read(ui.base,begin+44)!=1||Read(ui.base,begin+48)!=1)return false;
  vector=ui.scratch+640;
  for(unsigned i=0;i<12;i+=4)Write(ui.base,vector+i,0);
  ui.Call(0x82807240,{vector,3});
  const auto storage=Read(ui.base,vector),capacity=Read(ui.base,vector+8);
  if(!storage||capacity<storage||capacity-storage<132)return false;
  constexpr const char* labels[]={"QUIT TO MAIN MENU","CANCEL","QUIT TO DESKTOP"};
  for(unsigned i=0;i<3;++i){
    ui.Call(0x82808F48,{vector,begin+(i==1?44:0)});
    const auto first=Read(ui.base,vector),last=Read(ui.base,vector+4);
    if(!first||last<first||last-first!=(i+1)*44)return false;
    const auto button=first+i*44;
    if(!ui.TextRef(labels[i]))return false;
    ui.Call(0x824CEAB0,{button+8});Write(ui.base,button+8,0);
    ui.Call(0x82471820,{button+8,ui.scratch});
    ui.Call(0x824CEAB0,{ui.scratch});
    // Original A=0 / B=1 remain intact. X=2 is the new desktop choice.
    Write(ui.base,button,i);Write(ui.base,button+4,i);
  }
  return true;
}
}
void InstallHost(std::function<void(uint64_t)> request){std::lock_guard lock(mutex);flow.Cancel();host=std::move(request);}
void Cancel(){std::lock_guard lock(mutex);flow.Cancel();}
bool Consume(uint64_t generation){std::lock_guard lock(mutex);return Enabled()&&flow.Consume(generation);}
bool Available(uint8_t* base,uint32_t screen){
  if(!Enabled()||!Offline()||!fh1_pause_actions::detail::IsPause(base,screen)||!Read(base,screen+0x1f8)||!Read(base,kSaveManager))return false;
  std::lock_guard lock(mutex);return bool(host);
}
bool HasDialog(){std::lock_guard lock(mutex);return flow.HasDialog();}
bool Open(PPCContext& ctx,uint8_t* base,uint32_t screen,uint32_t owner){
  if(!Enabled()||!Offline()||!fh1_pause_actions::detail::IsPause(base,screen)||!Read(base,screen+0x1f8))return false;
  {std::lock_guard lock(mutex);if(!host||!flow.Begin(screen,owner,Read(base,kSaveManager)))return false;}
  Scoped scope(constructing,true);
  ctx.r3.u32=screen;
  __imp__sub_827EA788(ctx,base);
  return HasDialog();
}
bool Choice(PPCContext& ctx,uint8_t* base){
  // 82F28AD0 forwards a distinct 16-byte stack Result, not the 40-byte
  // MessageBoxParams. r5 is the live child popup: its parent and retained
  // Params identify the owned request without retaining a stack pointer.
  if(!ctx.r5.u32||!ctx.r6.u32||ctx.r4.u32!=2||
     Read(base,ctx.r6.u32)!=0x82033168||Read(base,ctx.r5.u32+156)!=ctx.r3.u32)return false;
  std::lock_guard lock(mutex);
  if(!Enabled()||!Offline()){flow.Cancel();return false;}
  return flow.Choose(ctx.r3.u32,Read(base,ctx.r5.u32+164),Read(base,ctx.r6.u32+12));
}
uint32_t MainMenuResult(PPCContext& ctx,uint8_t* base,uint32_t original){
  if(!original||Read(base,original)!=0x82033168)return 0;
  const auto object=REX_KERNEL_MEMORY()->SystemHeapAlloc(16);
  if(!object)return 0;
  // Native 827F2B90 initializes these four POD words; 827F2C98 owns no
  // references. Preserve every field and map only the selection to Main Menu.
  for(unsigned offset=0;offset<16;offset+=4)Write(base,object+offset,Read(base,original+offset));
  Write(base,object+12,0);
  return object;
}
void ReleaseResult(PPCContext&,uint8_t*,uint32_t result){
  if(result)REX_KERNEL_MEMORY()->SystemHeapFree(result);
}
void WithChoice(PPCContext& ctx,uint8_t* base,const std::function<void()>& handler){
  const auto original=ctx.r6.u64;
  const bool desktop=Choice(ctx,base);
  const auto mapped=desktop?MainMenuResult(ctx,base,uint32_t(original)):0;
  if(mapped)ctx.r6.u64=mapped;
  else if(desktop)Cancel();
  handler();
  if(mapped)ReleaseResult(ctx,base,mapped);
  if(desktop)ctx.r6.u64=original;
}
void Title(PPCContext&,uint8_t* base,uint32_t screen){
  std::function<void(uint64_t)> request;std::optional<uint64_t> generation;
  {std::lock_guard lock(mutex);
   if(!Enabled()||!Offline()){flow.Cancel();return;}
   generation=flow.Title(Read(base,screen+0x10c),Read(base,kSaveManager),
       Read(base,screen+0x260)==12&&Read(base,screen+0x174)!=0);
   if(generation)request=host;
  }
  if(generation&&request){REXLOG_INFO("Native quit: save and unload completed; requesting game window close");request(*generation);}
}
}

extern "C" REX_FUNC(sub_82681628){
  using namespace forza::native_quit;
  if(!constructing||ctx.r4.u32!=2){__imp__sub_82681628(ctx,base);return;}
  Native ui(ctx,base);uint32_t vector=0;
  const auto original_begin=ctx.r7.u32,original_end=ctx.r8.u32,original_body=ctx.r6.u32;
  bool prepared=ThreeChoices(ui,original_begin,original_end,vector);
  bool body=false;
  if(prepared){body=ui.TextRef("Choose where to quit.")!=0;prepared=body;}
  if(prepared){ctx.r7.u32=Read(base,vector);ctx.r8.u32=Read(base,vector+4);ctx.r6.u32=ui.scratch;}
  __imp__sub_82681628(ctx,base);
  const auto parameter=ctx.r3.u32;
  if(body)ui.Call(0x824CEAB0,{ui.scratch});
  if(vector)ui.Call(0x8267F2F0,{vector});
  // The original constructor has copied the references into its own object.
  ctx.r7.u32=original_begin;ctx.r8.u32=original_end;ctx.r6.u32=original_body;
  {std::lock_guard lock(mutex);flow.Parameter(parameter,prepared);}
}
extern "C" REX_FUNC(sub_827041C8){
  using namespace forza::native_quit;
  uint64_t generation;
  {std::lock_guard lock(mutex);generation=flow.Manager(ctx.r3.u32,ctx.r4.s32,ctx.r5.u32);}
  Scoped scope(manager_generation,generation);
  __imp__sub_827041C8(ctx,base);
  if(generation){std::lock_guard lock(mutex);flow.ManagerEnd(generation);}
}
extern "C" REX_FUNC(sub_82583868){
  using namespace forza::native_quit;
  if(!manager_generation){__imp__sub_82583868(ctx,base);return;}
  const auto profile=ctx.r3.u32,volume=profile?Read(base,profile+28):0;
  bool track=false;
  if(manager_generation){std::lock_guard lock(mutex);track=flow.Profile(manager_generation,profile,volume,volume?Read(base,volume):0,volume&&forza::GuestPtr(base,volume)[80]!=0);}
  Scoped generation_scope(profile_generation,track?manager_generation:0);
  Scoped profile_scope(profile_token,profile),volume_scope(volume_token,volume);
  __imp__sub_82583868(ctx,base);
  if(track){std::lock_guard lock(mutex);flow.ProfileEnd(profile_generation,profile,volume,ctx.r3.u32!=0);}
}
extern "C" REX_FUNC(sub_82D228C8){
  using namespace forza::native_quit;
  if(!profile_generation){__imp__sub_82D228C8(ctx,base);return;}
  const auto volume=ctx.r3.u32;bool track=false;
  if(profile_generation){std::lock_guard lock(mutex);track=flow.Commit(profile_generation,profile_token,volume,ctx.r4.u32);}
  Scoped generation_scope(commit_generation,track?profile_generation:0);
  Scoped volume_scope(commit_volume,volume);
  __imp__sub_82D228C8(ctx,base);
  if(track){std::lock_guard lock(mutex);flow.CommitEnd(commit_generation,volume,ctx.r3.u32);}
}
extern "C" REX_FUNC(sub_82B2BD00){
  using namespace forza::native_quit;
  __imp__sub_82B2BD00(ctx,base);
  if(commit_generation){std::lock_guard lock(mutex);flow.Prior(commit_generation,commit_volume,ctx.r3.u32);}
}
extern "C" REX_FUNC(sub_82B2AFD0){
  using namespace forza::native_quit;
  const bool synchronous=ctx.r4.u32==0;
  __imp__sub_82B2AFD0(ctx,base);
  if(commit_generation){std::lock_guard lock(mutex);flow.Flush(commit_generation,commit_volume,synchronous,ctx.r3.u32);}
}
extern "C" REX_FUNC(sub_82A3DB58){
  using namespace forza::native_quit;
  {std::lock_guard lock(mutex);flow.Unload(ctx.r3.u32&&Read(base,ctx.r3.u32)==0x8209CC80?Read(base,ctx.r3.u32+132):0xffffffffu);}
  __imp__sub_82A3DB58(ctx,base);
}
#else
namespace forza::native_quit {
void InstallHost(std::function<void(uint64_t)>){ }
void Cancel(){ }
bool Consume(uint64_t){return false;}
bool Open(PPCContext&,uint8_t*,uint32_t,uint32_t){return false;}
bool Available(uint8_t*,uint32_t){return false;}
bool HasDialog(){return false;}
bool Choice(PPCContext&,uint8_t*){return false;}
uint32_t MainMenuResult(PPCContext&,uint8_t*,uint32_t){return 0;}
void ReleaseResult(PPCContext&,uint8_t*,uint32_t){ }
void WithChoice(PPCContext&,uint8_t*,const std::function<void()>& handler){handler();}
void Title(PPCContext&,uint8_t*,uint32_t){ }
}
#endif
