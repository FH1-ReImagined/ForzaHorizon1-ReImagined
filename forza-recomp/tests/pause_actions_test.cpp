// Run the actual native action adapter against a synthetic native row vector.
// No translations, game data, SDK or rendering are needed.
#include "../src/platform/fh1_pause_actions.h"
#include "../src/platform/native_pause_dialog.h"
#include <array>
#include <cassert>
#include <cstring>
#include <map>
#include <vector>

namespace {
std::map<uint32_t,std::array<uint8_t,4096>> pages;
std::vector<uint32_t> calls;
uint32_t selected=0,dispatch_address=0;
constexpr uint32_t screen=0x10000,entries=0x20000,row=0x30000;
constexpr uint32_t params=0x50000,table=0x60000,getter=0x70000,child=0x80000;
constexpr uint32_t next_params=0xB0000;
bool observe_guard=false,accept_guard=false,queue_child=false;
uint32_t observed_screen=screen;
void Word(uint32_t address,uint32_t value) {
  auto* p=TestGuestPtr(address);
  for(int i=3;i>=0;--i){p[i]=uint8_t(value);value>>=8;}
}
void Native(PPCContext& ctx,uint8_t* base) {
  if (dispatch_address==getter) {
    assert(ctx.r3.u32==params || ctx.r3.u32==next_params);
    ctx.r3.u32=forza::fh1_pause_actions::detail::Read(base,ctx.r3.u32+4);
    return;
  }
  assert(ctx.r3.u32==screen);
  if(dispatch_address==0x827EBEE0) {
    assert(ctx.r4.u32==2 && ctx.r5.u32==0 && ctx.r6.u32==selected);
    if (queue_child) TestGuestPtr(screen+264)[0]=1;
    if (observe_guard) forza::fh1_pause_actions::ObserveClubGuard(base,observed_screen,accept_guard);
  } else assert(dispatch_address==0x827DABD0);
  ctx.r3.u64=0xAAAAAAAA;ctx.r4.u64=0xBBBBBBBB;ctx.r6.u64=0xCCCCCCCC;
}
void Setup(uint32_t id) {
  pages.clear();calls.clear();selected=id;
  observe_guard=accept_guard=queue_child=false;observed_screen=screen;
  Word(screen,0x82061F94);Word(screen+0x19C,entries);Word(screen+0x1A0,entries+4);
  Word(entries,row);Word(row+8,id);Word(row+12,0x40000);TestGuestPtr(row+20)[0]=1;
}
}
uint8_t* TestGuestPtr(uint32_t address){return pages[address>>12].data()+(address&4095);}
namespace rex::runtime {
GuestFunction ResolveIndirectFunction(uint32_t address) {
  dispatch_address=address;calls.push_back(address);return Native;
}
}
int main() {
  using namespace forza::fh1_pause_actions;
  uint8_t memory=0;PPCContext ctx;
  ctx.r3.u64=1;ctx.r4.u64=2;ctx.r5.u64=3;ctx.r6.u64=4;
  const auto original=ctx;
  for(uint32_t id=0;id<20;++id) {
    Setup(id);const auto before=pages;
    assert(Available(&memory,screen,id));
    const auto found=FindRow(&memory,screen,id);
    assert(found && found->address==row && found->text==0x40000 && found->enabled);
    assert(Dispatch(ctx,&memory,screen,id));
    assert((calls==std::vector<uint32_t>{0x827DABD0,0x827EBEE0}));
    assert(std::memcmp(&ctx,&original,sizeof(ctx))==0 && pages==before);
    // Enabled on display, disabled before activation: no native calls.
    calls.clear();TestGuestPtr(row+20)[0]=0;
    assert(!Dispatch(ctx,&memory,screen,id) && calls.empty());
    TestGuestPtr(row+20)[0]=1;Word(screen+0x1A0,entries);
    assert(!Dispatch(ctx,&memory,screen,id) && calls.empty());
  }
  for(int bad=0;bad<6;++bad) {
    Setup(14);
    switch(bad) {
      case 0: Word(screen,0);break;
      case 1: Word(screen+0x19C,0);break;
      case 2: Word(screen+0x1A0,entries-4);break;
      case 3: Word(screen+0x1A0,entries+3);break;
      case 4: Word(screen+0x1A0,entries+1028);break;
      case 5: Word(screen+0x1A0,entries+8);Word(entries+4,row);break;
    }
    assert(!Dispatch(ctx,&memory,screen,14) && calls.empty());
  }
  Setup(14);
  assert(!Dispatch(ctx,nullptr,screen,14) && !Dispatch(ctx,&memory,0,14));
  assert(!Dispatch(ctx,&memory,screen,20) && !Dispatch(ctx,&memory,screen,0x7000));
  assert(calls.empty());

  // The native Club guard may deny without creating any child. Its failure
  // must not be mistaken for a successful transition or leak into later calls.
  Setup(19);observe_guard=true;
  assert(!Dispatch(ctx,&memory,screen,19));
  assert((calls==std::vector<uint32_t>{0x827DABD0,0x827EBEE0}));
  assert(std::memcmp(&ctx,&original,sizeof(ctx))==0);
  queue_child=true;
  assert(Dispatch(ctx,&memory,screen,19)); // Native eligibility prompt queued.
  queue_child=false;TestGuestPtr(screen+264)[0]=0;accept_guard=true;
  assert(Dispatch(ctx,&memory,screen,19));
  accept_guard=false;observed_screen=screen+4;
  assert(Dispatch(ctx,&memory,screen,19)); // A different parent cannot veto it.
  Setup(7);observe_guard=true;
  assert(Dispatch(ctx,&memory,screen,7)); // Nor a different native action.
  ObserveClubGuard(&memory,screen,false); // Outside a dispatch has no effect.
  Setup(19);assert(Dispatch(ctx,&memory,screen,19));

  // Capture accepted parameters through the native virtual ID getter; zero
  // is valid. Results require the same retained params AND parent identity.
  Setup(0);Word(screen+160,params);Word(params,table);Word(table+4,getter);
  Word(child+156,screen);Word(child+164,params);
  assert(!QueuedDialog(ctx,&memory,screen));
  TestGuestPtr(screen+264)[0]=1;
  for (uint32_t id : {0u,3u,5u,16u,20u,21u,22u,23u,100007u,100011u,100012u,100013u,100014u}) {
    Word(params+4,id);calls.clear();const auto before=pages;
    const auto request=QueuedDialog(ctx,&memory,screen);
    assert(request && request->id==id && request->params==params);
    assert((calls==std::vector<uint32_t>{getter}));
    assert(std::memcmp(&ctx,&original,sizeof(ctx))==0 && pages==before);
    assert(MatchesDialog(&memory,screen,*request,id,child));
    assert(!MatchesDialog(&memory,screen,*request,id+1,child));
    assert(!MatchesDialog(&memory,screen,*request,id,0));
    Word(child+156,screen+4);assert(!MatchesDialog(&memory,screen,*request,id,child));
    Word(child+156,screen);Word(child+164,params+4);
    assert(!MatchesDialog(&memory,screen,*request,id,child));Word(child+164,params);
  }
  Word(table+4,0);assert(!QueuedDialog(ctx,&memory,screen));
  Word(params,0);assert(!QueuedDialog(ctx,&memory,screen));
  Word(screen+160,0);assert(!QueuedDialog(ctx,&memory,screen));
  assert(!QueuedDialog(ctx,nullptr,screen));

  // Callback removal after the prompt opened must not release the menu for a
  // native no-op. Confirm the actual pause fields, including party-leader X.
  for (const auto& item : std::array<std::array<uint32_t,3>,5>{{
      {2,0,504},{2,6,508},{3,0,484},{17,0,512},{23,0,488}}}) {
    Setup(17);
    assert(!HasRequiredExitCallback(&memory,screen,item[0],item[1]));
    Word(screen+item[2],0x90000);
    assert(HasRequiredExitCallback(&memory,screen,item[0],item[1]));
    Word(screen+item[2],0);
    assert(!HasRequiredExitCallback(&memory,screen,item[0],item[1]));
  }
  for (uint32_t id : {0u,1u,18u,19u}) assert(HasRequiredExitCallback(&memory,screen,id,0));
  assert(!HasRequiredExitCallback(nullptr,screen,2,0));

  // Exercise the production Before/After lifecycle using native callback
  // arguments, not just action-to-result tables. The original handler owns
  // changes made between Before and After, including replacement requests.
  namespace lifecycle=forza::native_pause_dialog;
  using Outcome=lifecycle::Outcome;using Effect=lifecycle::Effect;
  constexpr uint32_t result=0xA0000;
  auto prepare=[&](uint32_t id,uint32_t choice) {
    Setup(0);Word(params,table);Word(table+4,getter);Word(params+4,id);
    Word(child+156,screen);Word(child+164,params);
    Word(result,0x82033168);Word(result+12,choice);
    ctx.r3.u32=screen;ctx.r4.u32=id;ctx.r5.u32=child;ctx.r6.u32=result;
    return std::optional<DialogRequest>{{id,params}};
  };
  for (uint32_t id : {0u,1u,2u,3u,17u,18u,19u,23u}) {
    const auto request=prepare(id,1);
    const auto before=pages;
    const auto outcome=lifecycle::Before(ctx,&memory,screen,request);
    assert(outcome==Outcome::Cancel && pages==before);
    assert(lifecycle::After(ctx,&memory,screen,outcome,false).effect==Effect::Keep);
    assert(lifecycle::After(ctx,&memory,screen,outcome,true).effect==Effect::Restore);
  }
  for (uint32_t id : {16u,20u,21u,22u,100005u,100007u,100011u,100012u,100013u,100014u}) {
    const auto request=prepare(id,0);ctx.r6.u32=0;
    const auto outcome=lifecycle::Before(ctx,&memory,screen,request);
    assert(outcome==Outcome::Cancel); // No host read of a popup choice.
    assert(lifecycle::After(ctx,&memory,screen,outcome,true).effect==Effect::Restore);
  }
  auto request=prepare(0,0);
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Leave);
  Word(child+164,params+4);
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Ignored);
  Word(child+164,params);Word(child+156,screen+4);
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Ignored);
  Word(child+156,screen);ctx.r4.u32=1;
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Ignored);
  ctx.r4.u32=0;Word(result,0);
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Ignored);
  assert(lifecycle::After(ctx,&memory,screen,Outcome::Ignored,true).effect==Effect::Keep);
  request=prepare(3,0);
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Cancel);
  Word(screen+484,0x90000);
  assert(lifecycle::Before(ctx,&memory,screen,request)==Outcome::Leave);

  request=prepare(5,0);
  auto outcome=lifecycle::Before(ctx,&memory,screen,request);assert(outcome==Outcome::Restart);
  assert(lifecycle::After(ctx,&memory,screen,outcome,true).effect==Effect::Restore);
  TestGuestPtr(screen+468)[0]=1; // Original handler accepted; delegate has not run.
  assert(lifecycle::After(ctx,&memory,screen,outcome,false).effect==Effect::Release);
  request=prepare(5,6);
  outcome=lifecycle::Before(ctx,&memory,screen,request);assert(outcome==Outcome::Child);
  assert(lifecycle::After(ctx,&memory,screen,outcome,true).effect==Effect::Keep);

  request=prepare(100011,0);
  outcome=lifecycle::Before(ctx,&memory,screen,request);
  // Common handler queues its eligibility error before closing this prompt.
  Word(screen+160,next_params);Word(next_params,table);Word(next_params+4,100005);
  TestGuestPtr(screen+264)[0]=1;
  const auto followup=lifecycle::After(ctx,&memory,screen,outcome,true);
  assert(followup.effect==Effect::Keep && followup.next && followup.next->id==100005 &&
      followup.next->params==next_params);
  assert(lifecycle::Before(ctx,&memory,screen,followup.next)==Outcome::Ignored);
  calls.clear();assert(!lifecycle::After(ctx,&memory,screen,Outcome::Ignored,true).next);
  assert(calls.empty());
  Word(child+164,next_params);
  TestGuestPtr(screen+264)[0]=0;ctx.r4.u32=100005;
  outcome=lifecycle::Before(ctx,&memory,screen,followup.next);
  assert(outcome==Outcome::Cancel);
  assert(lifecycle::After(ctx,&memory,screen,outcome,true).effect==Effect::Restore);
}
