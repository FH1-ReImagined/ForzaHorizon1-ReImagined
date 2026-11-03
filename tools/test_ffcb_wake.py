"""Build actual FFCB hooks against synthetic event/context boundaries, without game data.

python tools/test_ffcb_wake.py --compiler clang++ --output <build-directory>
The model checks hook routing and event consumption; it does not qualify native GPU execution.
"""
import argparse
import os
from pathlib import Path
import subprocess

PREAMBLE = r'''
#include <cstdint>
#include <atomic>
#include <chrono>
#include <thread>
#include <cstdio>
union Reg { uint64_t u64=0; uint32_t u32; };
struct PPCContext { Reg r1,r3,r4,r31; uint64_t lr=0; };
#define REX_FUNC(name) void name(PPCContext& ctx, uint8_t* base)
#define REXLOG_INFO(...) ((void)0)
namespace forza { uint8_t* GuestPtr(uint8_t* b,uint32_t a) { return b+a; } }
'''
TESTS = r'''
uint8_t memory[65536]{};
bool signaled[12]{};
int failures=0, raw_calls=0, original_calls=0, native_consumes=0, wake_calls=0, taken=0;
int forced_status=0, depth=0;
bool worker=true, set_race=false, nested=false, throwing=false;
constexpr uint32_t object=0x1000, wakeptr=0x6000, wakehandle=0x4321;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %d: %s\n",__LINE__,#c); ++failures; } } while(false)
void Store32(uint32_t a,uint32_t v) { for(int i=3;i>=0;--i) { memory[a+i]=uint8_t(v);v>>=8; } }
void Init(uint32_t r) {
  Store32(r+0x75c,0); Store32(r+0x764,wakeptr); Store32(wakeptr,wakehandle);
  for(int i=0;i<12;++i) Store32(r+0x470+60*i+28,0x100+i);
}
PPCContext Context(uint32_t r=object) { PPCContext c;c.r1.u64=0xf000;c.r3.u64=r;c.r31.u64=0x12345678;return c; }
extern "C" REX_FUNC(RawWait) {
  ++raw_calls; CHECK(ctx.r4.u32==2); CHECK(ctx.r1.u32==0xec00);
  int slot=int(ctx.r3.u32)-0x100; CHECK(slot>=0 && slot<12);
  if (forced_status) { ctx.r3.u64=uint32_t(forced_status); return; }
  if(signaled[slot]) { signaled[slot]=false;++taken;ctx.r3.u64=0; }
  else {
    std::this_thread::sleep_for(std::chrono::milliseconds(2));ctx.r3.u64=0x102;
    if(set_race) { signaled[slot]=true;set_race=false; }
  }
  ctx.r31.u64=0xbad; // Raw-call volatile/context changes must not leak into caller.
}
extern "C" REX_FUNC(OriginalSlotWait) {
  ++original_calls; int slot=int(Load32(base,ctx.r3.u32))-0x100;
  if(slot>=0 && slot<12 && signaled[slot]) { signaled[slot]=false;++taken;ctx.r3.u64=1; }
  else ctx.r3.u64=0; // Stand-in for an empty native wait; never hang the test.
}
extern "C" REX_FUNC(SetEventHandle) {
  ++wake_calls;CHECK(ctx.r3.u32==wakehandle);CHECK(ctx.r1.u32==0xec00);
  if(worker) signaled[Load32(base,consuming->object+0x75c)]=true;
}
extern "C" REX_FUNC(OriginalFfcbConsume) {
  ++native_consumes; const uint32_t r=ctx.r3.u32;
  if(throwing) throw 7;
  if(nested && depth==0) {
    ++depth;auto inner=Context(object+0x2000);FfcbConsume(inner,base);--depth;
    CHECK(consuming && consuming->object==r);
  }
  auto slot=Load32(base,r+0x75c);
  ctx.r3.u64=r+0x470+60*slot+28;ctx.r4.u64=0xffffffff;ctx.lr=kSlotWaitReturn;
  SlotWait(ctx,base);
  CHECK(ctx.r31.u32==0x12345678);CHECK(ctx.r1.u32==0xf000);
  Store32(r+0x75c,(slot+1)%12);
}
int main(int argc,char** argv) {
  const char* mode=argc>1?argv[1]:"missing";Init(object);Init(object+0x2000);
  auto c=Context();
  if(!std::strcmp(mode,"ready")) {
    signaled[0]=true;FfcbConsume(c,memory);CHECK(c.r3.u32==1 && taken==1 && wake_calls==0 && raw_calls==1);
  } else if(!std::strcmp(mode,"missing") || !std::strcmp(mode,"set-race")) {
    set_race=!std::strcmp(mode,"set-race");FfcbConsume(c,memory);
    CHECK(c.r3.u32==1 && taken==1 && wake_calls==1 && raw_calls==2 && original_calls==0);
  } else if(!std::strcmp(mode,"error") || !std::strcmp(mode,"other-status")) {
    forced_status=!std::strcmp(mode,"error")?-1:0x101;FfcbConsume(c,memory);
    CHECK(c.r3.u32==0 && taken==0 && wake_calls==0 && raw_calls==1 && original_calls==0);
  } else if(!std::strcmp(mode,"wrap-reuse")) {
    for(int i=0;i<25;++i) { signaled[i%12]=true;c=Context();FfcbConsume(c,memory);CHECK(c.r3.u32==1); }
    CHECK(Load32(memory,object+0x75c)==1 && taken==25 && wake_calls==0);
    // Discard old pending slots, reinitialize at the identical address, then wake.
    for(bool& state:signaled) state=false;Init(object);c=Context();FfcbConsume(c,memory);
    CHECK(c.r3.u32==1 && wake_calls==1 && taken==26);
  } else if(!std::strcmp(mode,"nested")) {
    nested=true;FfcbConsume(c,memory);CHECK(c.r3.u32==1 && native_consumes==2 && taken==2 && wake_calls==2);
  } else if(!std::strcmp(mode,"throw")) {
    throwing=true;try { FfcbConsume(c,memory);CHECK(false); } catch(int) {}
    CHECK(!consuming);throwing=false;
    c.r3.u64=object+0x470+28;c.r4.u64=0xffffffff;c.lr=kSlotWaitReturn;
    SlotWait(c,memory);CHECK(original_calls==1 && raw_calls==0);
  } else if(!std::strcmp(mode,"unrelated")) {
    ConsumeScope scope(object,object+0x470+28);
    for(int route=0;route<3;++route) {
      c=Context();c.r3.u64=scope.event;c.r4.u64=0xffffffff;c.lr=kSlotWaitReturn;
      if(route==0) ++c.lr;if(route==1) c.r3.u64+=60;if(route==2) c.r4.u64=7;
      SlotWait(c,memory);
    }
    CHECK(original_calls==3 && raw_calls==0 && wake_calls==0);
  } else if(!std::strcmp(mode,"disabled")) {
    signaled[0]=true;FfcbConsume(c,memory);
    CHECK(native_consumes==1 && original_calls==1 && raw_calls==0 && wake_calls==0 && taken==1);
  } else if(!std::strcmp(mode,"deadline")) {
    worker=false;auto start=std::chrono::steady_clock::now();FfcbConsume(c,memory);
    auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
    CHECK(ms>=2000 && ms<3000 && wake_calls>0 && original_calls==1 && taken==0);
  }
  CHECK(!consuming || !std::strcmp(mode,"unrelated"));
  std::printf("%s: %s\n",mode,failures?"FAIL":"PASS");return failures?2:0;
}
'''
MODES = ('ready', 'missing', 'set-race', 'error', 'other-status', 'wrap-reuse',
         'nested', 'throw', 'unrelated', 'disabled', 'deadline')

def fixture(source):
    source = '\n'.join(line for line in source.splitlines()
                       if not line.startswith('#include "../platform/')
                       and not line.startswith('#include <rex/'))
    return PREAMBLE + source + TESTS

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',default='clang++')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    source=(Path(__file__).resolve().parents[1]/'forza-recomp/src/hooks/ffcb_wake.cpp').read_text()
    cpp=args.output/'ffcb_fixture.cpp';cpp.write_text(fixture(source))
    for tu in (0,1):
        exe=args.output/f'ffcb_fixture_{tu}.exe'
        subprocess.run([args.compiler,'-std=c++20','-O1','-pthread',f'-DFORZA_TU4={tu}',str(cpp),'-o',str(exe)],check=True)
        for mode in MODES:
            env=dict(os.environ,FORZA_FFCB_WAKE='0' if mode=='disabled' else '1')
            subprocess.run([str(exe),mode],env=env,check=True,timeout=5)

if __name__=='__main__':
    main()
