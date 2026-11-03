#include "../src/platform/native_quit_flow.h"
#include <cassert>
#include <cstdint>
#include <iostream>
using forza::native_quit::Flow;
namespace {
constexpr uint32_t screen=100,owner=1,manager=200,parameter=300,profile=400,volume=500;
uint64_t Start(Flow& f,uint32_t choice=2){
  const auto generation=f.Begin(screen,owner,manager);assert(generation);
  assert(f.Parameter(parameter,true));assert(f.Choose(screen,parameter,choice)==(choice==2));return generation;
}
uint64_t Save(Flow& f){
  const auto gen=Start(f);f.Unload(0);assert(f.Manager(manager,-1,1)==gen);
  assert(f.Profile(gen,profile,volume,0x82026A8C,true));assert(f.Commit(gen,profile,volume,1));return gen;
}
void Finish(Flow& f,uint64_t gen){f.CommitEnd(gen,volume,0);f.ProfileEnd(gen,profile,volume,true);f.ManagerEnd(gen);}
void NoClose(Flow& f,uint64_t gen){assert(!f.Title(owner,manager,true));assert(!f.Consume(gen));}
}
int main(){
 {Flow f;const auto g=Save(f);assert(!f.Title(owner,manager,true));f.Prior(g,volume,0);f.Flush(g,volume,true,0);Finish(f,g);assert(f.Title(owner,manager,true)==g);assert(f.Consume(g));assert(!f.Consume(g));}
 for(auto choice:{0u,1u,3u,0xffffffffu}){Flow f;auto g=Start(f,choice);NoClose(f,g);}
 {Flow f;const auto g=f.Begin(screen,owner,manager);f.Parameter(parameter,false);assert(!f.Choose(screen,parameter,2));NoClose(f,g);}
 for(auto error:{996u,997u,5u,4318u}){Flow f;auto g=Save(f);f.Prior(g,volume,error);f.Flush(g,volume,true,0);Finish(f,g);NoClose(f,g);}
 {Flow f;auto g=Save(f);f.Prior(g,volume,996);f.Prior(g,volume,0);f.Flush(g,volume,true,0);Finish(f,g);assert(f.Title(owner,manager,true)==g);}
 for(bool async:{false,true}){Flow f;auto g=Save(f);f.Prior(g,volume,0);f.Flush(g,volume,!async,5);Finish(f,g);NoClose(f,g);}
 {Flow f;auto g=Save(f);f.Prior(g,volume,0);f.Flush(g,volume,false,0);Finish(f,g);NoClose(f,g);}
 for(auto destination:{1u,2u,3u,0xffffffffu}){Flow f;auto g=Start(f);f.Unload(destination);assert(!f.Manager(manager,-1,1));NoClose(f,g);}
 for(auto identity:{0u,1u,2u}){Flow f;auto g=Start(f);f.Unload(0);assert(f.Manager(manager,-1,1)==g);assert(!f.Profile(g,identity==2?0:profile,volume,identity?0x82026A8C:0,identity!=1));NoClose(f,g);}
 {Flow f;auto g=Start(f);f.Unload(0);assert(!f.Manager(manager+1,-1,1));NoClose(f,g);}
 {Flow f;auto g=Save(f);f.Prior(g,volume,0);f.Flush(g,volume,true,0);f.CommitEnd(g,volume,0);f.ProfileEnd(g,profile+1,volume,true);f.ManagerEnd(g);NoClose(f,g);}
 {Flow f;auto g=Save(f);f.Prior(g,volume,0);f.Flush(g,volume,true,0);Finish(f,g);assert(!f.Title(owner+1,manager,true));assert(!f.Consume(g));}
 {Flow f;auto g=Save(f);f.Prior(g,volume,0);f.Flush(g,volume,true,0);Finish(f,g);assert(f.Title(owner,manager,true)==g);f.Cancel();assert(!f.Consume(g));}
 {Flow f;auto old=Save(f);f.Cancel();auto current=Start(f);assert(current!=old);f.Prior(old,volume,0);f.Flush(old,volume,true,0);Finish(f,old);NoClose(f,current);}
 std::cout<<"Native quit: complete save/unload proof, A/B cancellation, pending/error/lifetime/foreign/stale and one-shot close passed\n";
}
