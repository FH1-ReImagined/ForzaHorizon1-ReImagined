// Actual adapter with synthetic native allocation/reference/call boundaries.
// No game files, save writes, desktop window or GPU are used.
#include <rex/system/kernel_state.h>
#include "../src/platform/guest_memory.h"
#include <array>
#include <cassert>
#include <map>
#include <string>
#include <vector>
namespace {
std::array<uint8_t,1<<20> memory{};
struct Heap {
  bool fail=false;uint32_t next=0x10000;unsigned allocations=0,frees=0,attempts=0,fail_at=0;
  uint32_t SystemHeapAlloc(size_t size){if(fail||++attempts==fail_at)return 0;const auto p=next;next+=uint32_t((size+15)&~15);assert(next<memory.size());++allocations;return p;}
  void SystemHeapFree(uint32_t){++frees;}
} heap;
}
namespace forza {
inline uint8_t* FixtureGuestPtr(uint8_t* base,uint32_t address){
  // One read-only global manager slot used by the adapter, in this fixture.
  if(address==0x833CAACC)return base+0xe000; // CSaveProfile's lis/lwz target.
  if(address==0x82036984)return base+0xf000;
  assert(address<memory.size());
  return base+address;
}
}
#undef REX_KERNEL_MEMORY
#define REX_KERNEL_MEMORY() (&heap)
#define GuestPtr FixtureGuestPtr
#include "../src/platform/native_quit.cpp"
#undef GuestPtr

REXCVAR_DEFINE_BOOL(forza_native_pause,false,"Interface","Fixture option");
namespace {
using forza::fh1_menu_widgets::Read;
using forza::fh1_menu_widgets::Write;
constexpr uint32_t screen=0x100,parameter=0x1000,source=0x2000,profile=0x4000,volume=0x5000,unload=0x6000,result=0x7400,popup=0x7800;
std::map<uint32_t,std::u16string> text;
std::map<uint32_t,int> refs;
std::vector<uint32_t> captured_refs;
std::map<uint32_t,std::vector<uint32_t>> boxes;
std::vector<uint32_t> captured_ids,captured_buttons;
std::vector<std::u16string> captured_labels;
bool online=false,reserve_fail=false;
unsigned constructors=0,vector_destroyed=0;
uint32_t prior_status=0,flush_status=0;
void Ref(uint32_t object,int change){if(!object)return;refs[object]+=change;assert(refs[object]>=0);}
void DestroyBox(){for(auto& [box,objects]:boxes)for(auto object:objects)Ref(object,-1);boxes.clear();captured_refs.clear();for(auto [object,count]:refs)assert(count==((object==0x8000||object==0x8010)?1:0));}
void Setup(){
  DestroyBox();memory.fill(0);heap={};text.clear();refs.clear();captured_ids.clear();captured_buttons.clear();captured_labels.clear();constructors=vector_destroyed=0;reserve_fail=online=false;prior_status=flush_status=0;
  Write(memory.data(),screen,0x82061F94);Write(memory.data(),screen+0x10c,1);Write(memory.data(),screen+0x1f8,0x1234);Write(memory.data(),0xe000,0x3000);
  Write(memory.data(),profile+28,volume);Write(memory.data(),volume,0x82026A8C);memory[volume+80]=1;Write(memory.data(),unload,0x8209CC80);
  Write(memory.data(),0xf000,0xdead);
  Write(memory.data(),result,0x82033168);Write(memory.data(),popup+156,screen);Write(memory.data(),popup+164,parameter);
  for(unsigned i=0;i<2;++i){Write(memory.data(),source+i*44,i);Write(memory.data(),source+i*44+4,i);Write(memory.data(),source+i*44+8,0x8000+i*16);text[0x8000+i*16]=i?u"CANCEL":u"EXIT SINGLE PLAYER";refs[0x8000+i*16]=1;}
  forza::native_quit::Cancel();
}
void ExpectReferences(){for(auto object:captured_refs)assert(refs[object]>0);assert(refs[0x8000]>=1&&refs[0x8010]>=1);}
REX_FUNC(Allocate){ctx.r3.u32=heap.SystemHeapAlloc(ctx.r3.u32);}
REX_FUNC(MakeText){auto& out=text[ctx.r3.u32];for(uint32_t i=0;i<ctx.r5.u32;++i)out.push_back(char16_t(memory[ctx.r4.u32+i*2])<<8|memory[ctx.r4.u32+i*2+1]);}
REX_FUNC(CopyRef){const auto object=Read(base,ctx.r4.u32);Write(base,ctx.r3.u32,object);Ref(object,1);}
REX_FUNC(OwnRef){Write(base,ctx.r3.u32,ctx.r4.u32);Ref(ctx.r4.u32,1);}
REX_FUNC(FreeRef){Ref(Read(base,ctx.r3.u32),-1);Write(base,ctx.r3.u32,0);}
REX_FUNC(Reserve){if(reserve_fail)return;auto storage=heap.SystemHeapAlloc(ctx.r4.u32*44);Write(base,ctx.r3.u32,storage);Write(base,ctx.r3.u32+4,storage);Write(base,ctx.r3.u32+8,storage+ctx.r4.u32*44);}
REX_FUNC(Append){auto dest=Read(base,ctx.r3.u32+4);assert(dest+44<=Read(base,ctx.r3.u32+8));std::copy_n(base+ctx.r4.u32,44,base+dest);Ref(Read(base,dest+8),1);Write(base,ctx.r3.u32+4,dest+44);}
REX_FUNC(DestroyVector){++vector_destroyed;for(auto p=Read(base,ctx.r3.u32);p<Read(base,ctx.r3.u32+4);p+=44)Ref(Read(base,p+8),-1);for(unsigned i=0;i<12;i+=4)Write(base,ctx.r3.u32+i,0);}
REX_FUNC(DestroyResult){assert(ctx.r4.u32==1);auto found=boxes.find(ctx.r3.u32);assert(found!=boxes.end());for(auto object:found->second)Ref(object,-1);boxes.erase(found);heap.SystemHeapFree(ctx.r3.u32);}
}
namespace forza {
bool NetworkOn(){return online;}
std::optional<LiveSessionView> LiveCurrentSession(){return {};}
}
namespace rex::runtime {
PPCFunc* ResolveIndirectFunction(uint32_t address){
 switch(address){
  case 0x824265C8:return Allocate;case 0x82D6DE20:return MakeText;
  case 0x82555B90:return OwnRef;case 0x82471820:return CopyRef;case 0x824CEAB0:return FreeRef;
  case 0x82807240:return Reserve;case 0x82808F48:return Append;case 0x8267F2F0:return DestroyVector;
  case 0xdead:return DestroyResult;
  default:assert(false&&"unexpected native operation");return nullptr;
 }
}
}
extern "C" REX_FUNC(__imp__sub_82681628){
 ++constructors;
 const auto object=ctx.r3.u32;auto& owned=boxes[object];assert(owned.empty());
 Write(base,object,0x82036984);Write(base,object+4,ctx.r4.u32);Write(base,object+8,0);Write(base,object+12,0);
 captured_ids.clear();captured_buttons.clear();captured_labels.clear();captured_refs.clear();
 // Model the native constructor's reference copies and independent vector.
 const auto storage=heap.next;heap.next+=ctx.r8.u32-ctx.r7.u32;
 Write(base,object+24,storage);Write(base,object+28,storage+ctx.r8.u32-ctx.r7.u32);
 Write(base,object+32,Read(base,object+28));
 std::copy(base+ctx.r7.u32,base+ctx.r8.u32,base+storage);
 for(auto p=ctx.r7.u32;p<ctx.r8.u32;p+=44){
  captured_ids.push_back(Read(base,p));captured_buttons.push_back(Read(base,p+4));
  const auto ref=Read(base,p+8);captured_labels.push_back(text[ref]);captured_refs.push_back(ref);owned.push_back(ref);Ref(ref,1);
 }
 for(auto [slot,source]:{std::pair{16u,ctx.r5.u32},std::pair{20u,ctx.r6.u32}}){const auto ref=Read(base,source);Write(base,object+slot,ref);if(ref){captured_refs.push_back(ref);owned.push_back(ref);Ref(ref,1);}}
 ctx.r3.u32=object;
}
extern "C" REX_FUNC(__imp__sub_827EA788){ctx.r3.u32=parameter;ctx.r4.u32=2;ctx.r5.u32=0x9000;ctx.r6.u32=0x9010;ctx.r7.u32=source;ctx.r8.u32=source+88;sub_82681628(ctx,base);}
extern "C" REX_FUNC(__imp__sub_827041C8){ctx.r3.u32=profile;ctx.r4.u32=ctx.r5.u32=1;sub_82583868(ctx,base);}
extern "C" REX_FUNC(__imp__sub_82583868){ctx.r3.u32=volume;ctx.r4.u32=1;sub_82D228C8(ctx,base);ctx.r3.u32=1;}
extern "C" REX_FUNC(__imp__sub_82D228C8){ctx.r3.u32=volume+96;sub_82B2BD00(ctx,base);ctx.r4.u32=0;sub_82B2AFD0(ctx,base);}
extern "C" REX_FUNC(__imp__sub_82B2BD00){ctx.r3.u32=prior_status;}
extern "C" REX_FUNC(__imp__sub_82B2AFD0){ctx.r3.u32=flush_status;}
extern "C" REX_FUNC(__imp__sub_82A3DB58){}

int main(){
 using namespace forza::native_quit;PPCContext ctx{};auto* base=memory.data();unsigned requests=0;uint64_t requested=0;
 auto install=[&]{InstallHost([&](uint64_t g){++requests;requested=g;});};
 auto open=[&]{ctx.r3.u32=screen;assert(Open(ctx,base,screen,1));assert(constructors==1);ExpectReferences();};
 Setup();install();assert(!Open(ctx,base,screen,1)&&constructors==0);assert(rex::cvar::SetFlagByName("forza_native_pause","true"));
 online=true;assert(!Open(ctx,base,screen,1)&&constructors==0);online=false;
 for(auto choice:{0u,1u,2u}){
  Setup();install();open();assert((captured_ids==std::vector<uint32_t>{0,1,2}));assert((captured_buttons==std::vector<uint32_t>{0,1,2}));assert((captured_labels==std::vector<std::u16string>{u"QUIT TO MAIN MENU",u"CANCEL",u"QUIT TO DESKTOP"}));assert(vector_destroyed==1);ExpectReferences();
  ctx.r3.u32=screen;ctx.r4.u32=2;ctx.r5.u32=popup;ctx.r6.u32=result;Write(base,result+12,choice);assert(Choice(ctx,base)==(choice==2));assert(requests==0);
  if(choice==2){const auto saved=ctx;const auto clone=MainMenuResult(ctx,base,result);assert(clone&&clone!=result);assert(constructors==1);assert(Read(base,clone+12)==0&&Read(base,result+12)==2);for(unsigned offset:{0u,4u,8u})assert(Read(base,clone+offset)==Read(base,result+offset));assert(ctx.r3.u64==saved.r3.u64&&ctx.r6.u64==saved.r6.u64&&ctx.r7.u64==saved.r7.u64&&ctx.r8.u64==saved.r8.u64);const auto freed=heap.frees;ReleaseResult(ctx,base,clone);assert(heap.frees==freed+1);assert(ctx.r6.u64==saved.r6.u64);heap.fail=true;assert(!MainMenuResult(ctx,base,result));assert(Read(base,result+12)==2);heap.fail=false;}
 }
 for(auto fail:{1u,2u}){Setup();install();if(fail==1)heap.fail=true;else reserve_fail=true;open();assert(captured_ids.size()==2);ctx.r3.u32=screen;ctx.r4.u32=2;ctx.r5.u32=popup;ctx.r6.u32=result;Write(base,result+12,2);assert(!Choice(ctx,base));}
 // Every partial native TextRef/vector construction failure must release the
 // references already copied and retain the original two-choice dialog.
 for(unsigned fail=1;fail<=6;++fail){Setup();install();heap.fail_at=fail;open();assert(captured_ids.size()==2);DestroyBox();}
 for(auto choice:{0u,1u,2u,99u})for(bool allocation_failure:{false,true}){
  Setup();install();open();ctx.r3.u32=screen;ctx.r4.u32=2;ctx.r5.u32=popup;ctx.r6.u32=result;ctx.r7.u64=0x12345678;ctx.r8.u64=0xabcdef01;Write(base,result+12,choice);heap.fail=allocation_failure;
  unsigned forwards=0;WithChoice(ctx,base,[&]{++forwards;assert(ctx.r3.u32==screen&&ctx.r4.u32==2&&ctx.r7.u64==0x12345678&&ctx.r8.u64==0xabcdef01);if(choice==2&&!allocation_failure){assert(ctx.r6.u32!=result&&Read(base,ctx.r6.u32+12)==0);}else assert(ctx.r6.u32==result&&Read(base,result+12)==choice);ctx.r3.u64=0x1122334455667788;});
  assert(forwards==1&&ctx.r6.u32==result&&Read(base,result+12)==choice&&ctx.r3.u64==0x1122334455667788);assert(boxes.size()==1);
 }
 // A Result has no Params identity. Only its live popup's native parent and
 // retained Params establish lineage; foreign same-event results stay intact.
 for(unsigned foreign=0;foreign<7;++foreign){
  Setup();install();open();ctx.r3.u32=foreign==0?screen+4:screen;ctx.r4.u32=foreign==1?18:2;ctx.r5.u32=popup;ctx.r6.u32=foreign==2?0x7000:foreign==5?parameter:result;
  if(foreign==3)Write(base,popup+156,screen+4);
  if(foreign==4)Write(base,popup+164,parameter+40);
  if(foreign==6)Cancel();
  Write(base,ctx.r6.u32+12,2);const auto pointer=ctx.r6.u32;unsigned forwards=0;
  WithChoice(ctx,base,[&]{++forwards;assert(ctx.r6.u32==pointer);});assert(forwards==1&&constructors==1);
 }
 // Fully initialize the native 16-byte POD and preserve both auxiliary words.
 Setup();install();open();ctx.r3.u32=screen;ctx.r4.u32=2;ctx.r5.u32=popup;ctx.r6.u32=result;
 Write(base,result+4,0x12345678);Write(base,result+8,0x87654321);Write(base,result+12,2);
 ctx.r6.u64=0x1234000000000000ull|result;const auto full_result_register=ctx.r6.u64;
 WithChoice(ctx,base,[&]{assert(ctx.r6.u32!=result&&ctx.r6.u64==ctx.r6.u32);assert(Read(base,ctx.r6.u32)==0x82033168);assert(Read(base,ctx.r6.u32+4)==0x12345678);assert(Read(base,ctx.r6.u32+8)==0x87654321);assert(Read(base,ctx.r6.u32+12)==0);ctx.r6.u64=0xabcdef1234567890ull;});
 assert(ctx.r6.u64==full_result_register&&Read(base,result+12)==2);
 for(auto status:{0u,996u,997u,5u,4318u}){
  Setup();install();open();ctx.r3.u32=screen;ctx.r4.u32=2;ctx.r5.u32=popup;ctx.r6.u32=result;Write(base,result+12,2);assert(Choice(ctx,base));prior_status=status;
  ctx.r3.u32=unload;sub_82A3DB58(ctx,base);ctx.r3.u32=0x3000;ctx.r4.s32=-1;ctx.r5.u32=1;sub_827041C8(ctx,base);
  const auto before=requests;Write(base,0xa000+0x10c,1);Write(base,0xa000+0x260,12);Write(base,0xa000+0x174,0xb000);Title(ctx,base,0xa000);assert(requests==before+(status==0));
  if(status==0){assert(Consume(requested));assert(!Consume(requested));}
 }
 DestroyBox();assert(refs[0x8000]==1&&refs[0x8010]==1);InstallHost({});
}
