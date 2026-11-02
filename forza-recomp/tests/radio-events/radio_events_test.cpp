// Contract fixture executes authored wrappers; native callees are modeled, not FMOD.
#include <rex/ppc/func.h>
#include "hooks/radio_event_bindings.h"
using namespace forza::radio_event_native;
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
extern bool radio_keep_in_events;
#define DECL(n) extern "C" REX_FUNC(n)
DECL(PrepareCutscene); DECL(GenericFallback); DECL(StopCutscene); DECL(EventRequestCopy); DECL(RaceCutsceneLookup); DECL(CutsceneLookup); DECL(CutsceneMusicTrigger);
DECL(StartEventMusic); DECL(QueueEventMusic); DECL(MarkScriptedStation);
namespace {
std::vector<uint8_t> mem(64*1024*1024);
struct Trace { uint32_t record; char kind; uint32_t flag; };
std::vector<uint32_t> native_records; std::vector<Trace> queue_trace,marker_trace;
constexpr uint32_t flow=0x80001000,settings=0x80002000,player=0x80003000,audio=0x80004000,vt=0x80005000;
constexpr uint32_t rec=0x80006000,timeline=0x80007000,canonical=0x80008000,key=0x80009000,name=0x8000A000;
constexpr uint32_t worker=0x8000E000,radio=0x8000F000,radio_vt=0x80010000;
constexpr uint32_t cuts=0x8000B000,incoming=0x8000C000,other=0x8000D000;
int select_calls=0;
int originals=0,free_calls=0,event_calls=0,markers=0,outer_releases=0,queue_releases=0,side_effects=0;
int lookup_path=0; uint32_t track_result=0,variant_result=0;
bool other_stop_after_lookup=false,stop_after_lookup=false,qualified_reprepare_after_lookup=false,reprepare_after_lookup=false,rebind_in_helper=false;
uint32_t marker_flag=0,marker_station=0,selected=3,first_result=rec,last_result=rec;
uint64_t music_caller=kMusicReturn,queue_caller=kQueueReturn,marker_caller=kMarkerReturn;
bool native_flag=false; [[maybe_unused]] bool invalidate_in_flag=false; int flag_calls=0;
bool nested_helper=false,wrong_receiver=false,wrong_audio=false,nested_unbound=false,nested_eligible=false,nested_lookup=false,invalidate_before_queue=false,transition_in_queue=false;
uint32_t Read(uint32_t a){auto*p=mem.data()+(a&0x7fffffff);return uint32_t(p[0])<<24|uint32_t(p[1])<<16|uint32_t(p[2])<<8|p[3];}
void Write(uint32_t a,uint32_t x){auto*p=mem.data()+(a&0x7fffffff);for(int i=3;i>=0;--i){p[i]=uint8_t(x);x>>=8;}}
void Str(uint32_t a,const std::string&s){auto d=s.size()>=16?a+0x200:a;std::memcpy(mem.data()+(d&0x7fffffff),s.c_str(),s.size()+1);Write(a+16,uint32_t(s.size()));Write(a+20,s.size()>=16?uint32_t(s.size()):15);if(s.size()>=16)Write(a,d);}
void Copy(uint32_t type=3){Write(incoming+44,type);PPCContext c;c.r3.u64=flow+8;c.r4.u64=incoming;EventRequestCopy(c,mem.data());}
void Record(uint32_t r,const std::string&k){Str(r+4,k);Write(r+80,3);Write(r+84,3);Write(r+192,timeline+(r-rec));Write(timeline+(r-rec)+4,r);}
void Reset(){select_calls=0;lookup_path=0;track_result=variant_result=0;other_stop_after_lookup=stop_after_lookup=qualified_reprepare_after_lookup=reprepare_after_lookup=rebind_in_helper=false;native_flag=false;invalidate_in_flag=false;flag_calls=0;native_records.clear();queue_trace.clear();marker_trace.clear();Copy();std::fill(mem.begin(),mem.end(),0);Write(kManager,flow);Write(kSettings,settings);Write(kPlayer,player);Write(kAudioManager,audio);Write(settings+4,3);Write(flow+52,3);Write(audio,vt);Write(audio+12,worker);Write(worker+52,radio);Write(radio,radio_vt);Write(radio_vt+kRadioFreeRoamSlot,kRadioFreeRoam);Write(radio+124,1);Write(radio+164,1);Write(radio+168,3);Write(radio+172,1);Write(radio+176,4);Write(vt+kSelectStationSlot,kSelectStationQueue);Write(vt+kFreeRoamSlot,kFreeRoamQueue);Record(rec,"prerace_intro_region_race");Record(other,"story_cinematic");Str(canonical,"prerace_intro");Str(key,"prerace_intro_region_race");Copy();first_result=last_result=rec;originals=free_calls=event_calls=markers=outer_releases=queue_releases=side_effects=0;marker_flag=marker_station=0;selected=1;music_caller=kMusicReturn;queue_caller=kQueueReturn;marker_caller=kMarkerReturn;nested_helper=wrong_receiver=wrong_audio=nested_unbound=nested_eligible=nested_lookup=invalidate_before_queue=transition_in_queue=false;}
void Prepare(uint64_t caller=kPrepareReturn){PPCContext c;c.lr=caller;c.r3.u64=cuts;c.r4.u64=canonical;PrepareCutscene(c,mem.data());}
void Music(uint32_t r=rec){PPCContext c;c.r3.u64=r;CutsceneMusicTrigger(c,mem.data());}
void ExpectOriginal(){assert(originals==1&&free_calls==0&&event_calls==1&&marker_flag==1&&marker_station==2&&outer_releases==1&&queue_releases==1);}
}
extern "C" REX_FUNC(OriginalEventRequestCopy){assert(base==mem.data());Write(ctx.r3.u32+44,Read(ctx.r4.u32+44));}
extern "C" REX_FUNC(OriginalPrepareCutscene){
 if(ctx.lr==kPrepareReturn) RaceCutsceneLookup(ctx,base);
 else { ctx.lr=0x1234;ctx.r3.u64=cuts;ctx.r4.u64=key;CutsceneLookup(ctx,base); }
}
extern "C" REX_FUNC(OriginalStopCutscene){assert(base==mem.data());Write(ctx.r3.u32+84,0);Write(ctx.r3.u32+80,0);}
extern "C" REX_FUNC(OriginalRaceCutsceneLookup){
 if(nested_lookup){nested_lookup=false;auto save=ctx;auto first=first_result,last=last_result;Str(canonical,"story_cinematic");Str(key,"story_cinematic_key");first_result=last_result=other;PPCContext inner=ctx;RaceCutsceneLookup(inner,base);ctx=save;first_result=first;last_result=last;Str(canonical,"prerace_intro");Str(key,"prerace_intro_region_race");}
 ctx.lr=kQualifiedReturn;if(wrong_receiver)ctx.r3.u64=cuts+0x100;ctx.r4.u64=key;CutsceneLookup(ctx,base);
 if(lookup_path>0&&!ctx.r3.u32){ctx.lr=kTrackReturn;ctx.r3.u64=cuts;Str(key,"prerace_intro_track");ctx.r4.u64=key;CutsceneLookup(ctx,base);}
 if(lookup_path>1&&!ctx.r3.u32){ctx.lr=kFallbackReturn;ctx.r3.u64=cuts;ctx.r4.u64=canonical;GenericFallback(ctx,base);}
 if(other_stop_after_lookup){other_stop_after_lookup=false;PPCContext stop;stop.r3.u64=other;StopCutscene(stop,base);}
 if(stop_after_lookup){stop_after_lookup=false;PPCContext stop;stop.r3.u64=rec;StopCutscene(stop,base);Record(rec,"prerace_intro_region_race");}
 if(qualified_reprepare_after_lookup){qualified_reprepare_after_lookup=false;Prepare();}
 if(reprepare_after_lookup){reprepare_after_lookup=false;Prepare(0x1234);}
 ctx.r3.u64=last_result;
}
extern "C" REX_FUNC(OriginalGenericFallback){
 ctx.lr=kVariantReturn;ctx.r3.u64=cuts;Str(key,"prerace_intro_variant");ctx.r4.u64=key;CutsceneLookup(ctx,base);
 if(lookup_path>2&&!ctx.r3.u32){ctx.lr=0x1234;ctx.r3.u64=cuts;CutsceneLookup(ctx,base);ctx.r3.u64=last_result;}
}
extern "C" REX_FUNC(OriginalCutsceneLookup){(void)base;ctx.r3.u64=ctx.lr==kQualifiedReturn?first_result:ctx.lr==kTrackReturn?track_result:ctx.lr==kVariantReturn?variant_result:last_result;}
extern "C" REX_FUNC(OriginalCutsceneMusicTrigger){
 const auto r=ctx.r3.u32; native_records.push_back(r);
 if(r==rec&&nested_unbound){nested_unbound=false;Music(other);}
 if(r==rec&&nested_eligible){nested_eligible=false;Music(other);}
 ctx.lr=music_caller;ctx.r3.u64=player;ctx.r4.u64=name;StartEventMusic(ctx,base);native_records.pop_back();
}
extern "C" REX_FUNC(OriginalStartEventMusic){
 ++originals;++side_effects;
 Write(radio+124,0); // Original helper queues native station unlock before its music command.
 if(rebind_in_helper){rebind_in_helper=false;Prepare();Music();}
 if(nested_helper){nested_helper=false;auto saved=ctx;PPCContext inner;inner.lr=0x1234;inner.r3.u64=player;StartEventMusic(inner,base);ctx=saved;}
 if(wrong_audio)Write(kAudioManager,audio+0x100);
 if(invalidate_before_queue){invalidate_before_queue=false;Copy();}
 ctx.lr=queue_caller;ctx.r3.u64=audio;ctx.r4.u64=name;QueueEventMusic(ctx,base);
 ctx.lr=marker_caller;ctx.r3.u64=player;ctx.r4.u64=1;ctx.r5.u64=2;MarkScriptedStation(ctx,base);
 ++outer_releases;
}
extern "C" REX_FUNC(OriginalQueueEventMusic){assert(base==mem.data()&&ctx.r4.u32==name);++event_calls;++queue_releases;queue_trace.push_back({native_records.back(),'E',0});}
extern "C" REX_FUNC(OriginalMarkScriptedStation){assert(base==mem.data()&&ctx.r3.u32==player);++markers;marker_flag=ctx.r4.u32;marker_station=ctx.r5.u32;marker_trace.push_back({native_records.back(),'M',marker_flag});}
extern "C" REX_FUNC(QueueFreeRoam){assert(base==mem.data()&&ctx.r3.u32==audio&&ctx.f1.f64==1.0&&ctx.r5.u32==0&&ctx.r6.u32==uint32_t(kTu4&&(native_flag||Read(radio+172)==3)));assert(kTu4?flag_calls>0:flag_calls==0);++free_calls;queue_trace.push_back({native_records.back(),'F',0});if(transition_in_queue){transition_in_queue=false;Copy(17);Write(settings+4,17);}if(Read(radio+172)!=3||(kTu4&&ctx.r6.u32))Write(radio+164,Read(radio+172));assert(Read(radio+172)==selected);}
extern "C" REX_FUNC(QueueSelectStation){assert(base==mem.data()&&ctx.r3.u32==audio&&ctx.r4.u32==3&&ctx.r5.u32==0&&ctx.r6.u32==1);assert(Read(radio+124)==0);++select_calls;Write(radio+164,3);assert(Read(radio+172)==selected);}
extern "C" REX_FUNC(DestroyOwnedString){assert(base==mem.data()&&ctx.r3.u32==name&&ctx.r4.u32==1&&ctx.r5.u32==0);++queue_releases;}
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
extern "C" REX_FUNC(FreeRoamFlag){assert(base==mem.data());++flag_calls;ctx.r3.u64=native_flag?0xAB01:0;ctx.r4.u64=0xBAD;ctx.r5.u64=0xBAD;ctx.f1.f64=99;if(invalidate_in_flag){invalidate_in_flag=false;Copy();}}
#endif
int main(int argc,char**argv){
 radio_keep_in_events=argc>1&&std::string(argv[1])=="on";
 if(!radio_keep_in_events){Reset();Prepare();Music();ExpectOriginal();std::cout<<"default original passed\n";return 0;}
 int cases=0;
 Reset();selected=3;Write(radio+172,3);Write(radio+164,4);Prepare();Music();assert(free_calls==1&&select_calls==(kTu4?0:1)&&Read(radio+164)==3&&Read(radio+172)==3);++cases;
 Reset();Write(radio_vt+kRadioFreeRoamSlot,0);Prepare();Music();ExpectOriginal();++cases;
 Reset();Write(radio+168,0);Prepare();Music();ExpectOriginal();++cases;
 if(!kTu4){Reset();selected=3;Write(radio+172,3);Write(vt+kSelectStationSlot,0);Prepare();Music();ExpectOriginal();++cases;}

 Reset();first_result=0;lookup_path=1;track_result=rec;Record(rec,"prerace_intro_track");Prepare();Music();assert(free_calls==1&&event_calls==0);++cases;
 Reset();first_result=0;lookup_path=2;variant_result=rec;Record(rec,"prerace_intro_variant");Prepare();Music();assert(free_calls==1&&event_calls==0);++cases;
 Reset();first_result=0;lookup_path=3;Prepare();Music();ExpectOriginal();++cases;
 Reset();first_result=0;lookup_path=2;variant_result=rec;last_result=other;Record(rec,"prerace_intro_variant");Prepare();Music();ExpectOriginal();++cases;
 Reset();Prepare();Prepare(0x1234);Music();ExpectOriginal();++cases;
 Reset();Prepare();PPCContext stop;stop.r3.u64=rec;StopCutscene(stop,mem.data());Record(rec,"prerace_intro_region_race");Music();ExpectOriginal();++cases;
 Reset();other_stop_after_lookup=true;Prepare();Music();assert(free_calls==1&&event_calls==0);++cases;
 Reset();stop_after_lookup=true;Prepare();Music();ExpectOriginal();++cases;
 Reset();qualified_reprepare_after_lookup=true;Prepare();Music();ExpectOriginal();++cases;
 Reset();reprepare_after_lookup=true;Prepare();Music();ExpectOriginal();++cases;
 Reset();Prepare();rebind_in_helper=true;Music();assert(queue_trace.size()==2&&queue_trace[0].kind=='F'&&queue_trace[1].kind=='E');assert(marker_trace[0].flag==0&&marker_trace[1].flag==1);++cases;

 Reset();native_flag=true;Prepare();Music();assert(free_calls==1);++cases;
 if(kTu4){Reset();Prepare();invalidate_in_flag=true;Music();ExpectOriginal();++cases;}
 for(uint32_t type:{0u,1u,6u,8u,12u,17u,18u,19u,99u}){Reset();Write(settings+4,type);Copy(type);Prepare();Music();ExpectOriginal();++cases;}
 Reset();Write(settings+4,20);Copy(20);Prepare();Music();if(kTu4)assert(free_calls==1&&event_calls==0);else ExpectOriginal();++cases;
 Reset();Prepare();Music();assert(free_calls==1&&event_calls==0&&marker_flag==0&&marker_station==uint32_t(-1)&&queue_releases==1&&outer_releases==1&&side_effects==1);++cases;
 Reset();first_result=0;Prepare();Music();ExpectOriginal();++cases;
 Reset();last_result=other;Prepare();Music();ExpectOriginal();++cases;
 Reset();Prepare(0x1234);Music();ExpectOriginal();++cases;
 Reset();Str(canonical,"prerace_intro_story");Prepare();Music();ExpectOriginal();++cases;
 Reset();Write(flow+52,17);Prepare();Write(flow+52,3);Music();ExpectOriginal();++cases;
 Reset();Prepare();Copy();Music();ExpectOriginal();++cases;
 for(uint32_t phase:{0u,1u,2u}){Reset();Prepare();Write(rec+80,phase);Music();ExpectOriginal();++cases;}
 Reset();Prepare();Write(rec+84,0);Music();ExpectOriginal();++cases;
 Reset();Prepare();Write(timeline+4,other);Music();ExpectOriginal();++cases;
 Reset();Prepare();Str(rec+4,"story_cinematic");Music();ExpectOriginal();++cases;
 Reset();Prepare();music_caller=0x1234;Music();ExpectOriginal();++cases;
 Reset();Prepare();queue_caller=0x1234;Music();ExpectOriginal();++cases;
 Reset();Prepare();marker_caller=0x1234;Music();assert(free_calls==1&&marker_flag==1&&marker_station==2);++cases;
 Reset();Prepare();Write(vt+kFreeRoamSlot,0);Music();ExpectOriginal();++cases;
 Reset();Prepare();invalidate_before_queue=true;Music();ExpectOriginal();++cases;
 Reset();Prepare();transition_in_queue=true;Music();assert(free_calls==1&&event_calls==0&&marker_flag==0&&marker_station==uint32_t(-1));Music();assert(event_calls==1);++cases;
 Reset();Prepare();nested_unbound=true;Music();assert(queue_trace.size()==2&&queue_trace[0].record==other&&queue_trace[0].kind=='E'&&queue_trace[1].record==rec&&queue_trace[1].kind=='F');assert(marker_trace[0].record==other&&marker_trace[0].flag==1&&marker_trace[1].record==rec&&marker_trace[1].flag==0);assert(free_calls==1&&event_calls==1&&markers==2&&queue_releases==2&&outer_releases==2&&marker_flag==0);++cases;
 Reset();Prepare();Str(canonical,"prerace_countdown");Str(key,"prerace_countdown_region_race");Record(other,"prerace_countdown_region_race");first_result=last_result=other;Prepare();nested_eligible=true;Music();assert(queue_trace.size()==2&&queue_trace[0].record==other&&queue_trace[0].kind=='F'&&queue_trace[1].record==rec&&queue_trace[1].kind=='F');assert(marker_trace[0].flag==0&&marker_trace[1].flag==0);assert(free_calls==2&&event_calls==0&&markers==2&&queue_releases==2);++cases;
 Reset();nested_lookup=true;Prepare();Music();ExpectOriginal();++cases;
 Reset();Prepare();nested_helper=true;Music();assert(free_calls==1&&event_calls==1&&markers==2&&queue_releases==2&&outer_releases==2&&marker_flag==0);++cases;
 Reset();wrong_receiver=true;Prepare();Music();ExpectOriginal();++cases;
 Reset();Prepare();wrong_audio=true;Music();ExpectOriginal();++cases;
 Reset();Prepare();Write(rec+84,2);Music();ExpectOriginal();++cases;
 std::cout<<cases<<" enabled adversarial cases passed\n";
}
