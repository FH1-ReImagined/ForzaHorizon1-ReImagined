
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
std::vector<std::function<void()>> timers;
struct FakeTimer { template<class F> FakeTimer(F f) { timers.emplace_back(f); } void detach() {} };
std::atomic<bool> sync_on{false};
std::mutex gate_mutex;
std::condition_variable gate_cv;
bool block_expiry=false,expiry_entered=false,release_expiry=false,renew_entered=false,renew_done=false;
void SetFlag(const char* value) {
  const bool on=std::string(value)=="true";
  if (!on && block_expiry) {
    std::unique_lock lock(gate_mutex);expiry_entered=true;gate_cv.notify_all();
    if (!gate_cv.wait_for(lock,std::chrono::seconds(1),[]{return release_expiry;})) std::abort();
  }
  sync_on=on;
}
namespace rex::cvar {
struct FlagEntry { std::string name; void(*setter)(const char*); };
std::vector<FlagEntry>& GetRegistry() { static std::vector<FlagEntry> r{{"sync_pipeline_creation",SetFlag}};return r; }
}
int64_t now_ms=1000;
auto FakeNow() { return std::chrono::steady_clock::time_point(std::chrono::milliseconds(now_ms)); }
#define REXLOG_INFO(...) ((void)0)
#define REX_FUNC(name) void name(int& ctx, uint8_t* base)
uint8_t* expected_base = nullptr;
extern "C" REX_FUNC(__imp__sub_82546460) {
  if (base != expected_base) std::abort();
  ++ctx;
}
extern "C" REX_FUNC(__imp__sub_82C8FE60) {
  if (base != expected_base) std::abort();
  ++ctx;
}
namespace {
#include "actual_thumbnail_sync.inc"
void require(bool pass,const char* text) { if (!pass) {std::cerr<<"ASSERTION FAILED: "<<text<<'\n';std::exit(1);} }
int main(int argc,char**argv) {
  const std::string mode=argc>1?argv[1]:"race";
  if(mode=="race") {
    HoldSync();auto expiry=timers.front();block_expiry=true;
    std::thread old_timer(expiry);
    {std::unique_lock lock(gate_mutex);require(gate_cv.wait_for(lock,std::chrono::seconds(1),[]{return expiry_entered;}),"old timer reached actual setter");}
    std::thread renewal([]{
      {std::lock_guard lock(gate_mutex);renew_entered=true;gate_cv.notify_all();}
      HoldSync();
      {std::lock_guard lock(gate_mutex);renew_done=true;gate_cv.notify_all();}
    });
    {std::unique_lock lock(gate_mutex);
      require(gate_cv.wait_for(lock,std::chrono::seconds(1),[]{return renew_entered;}),"renewal thread entered");
      gate_cv.wait_for(lock,std::chrono::milliseconds(100),[]{return renew_done;});
      release_expiry=true;gate_cv.notify_all();
    }
    old_timer.join();renewal.join();
    require(sync_on,"expired old timer must not clear a newer sync hold");
  } else if(mode=="expiry") {
    HoldSync();HoldSync();timers[0]();require(sync_on,"stale timer cannot clear renewed hold");
    timers[1]();require(!sync_on,"latest timer expires hold");
  } else if(mode=="photo") {
    forza::thumbnail_sync::HoldForPhotoMode();require(sync_on&&timers.size()==1,"first photo holds sync");
    now_ms=1999;for(int i=0;i<60;++i)forza::thumbnail_sync::HoldForPhotoMode();
    require(timers.size()==1,"photo renewals throttled below one second");
    now_ms=2000;forza::thumbnail_sync::HoldForPhotoMode();require(timers.size()==2,"renew at one second");
    timers[0]();require(sync_on,"prior photo timer preserves renewed window");
    timers[1]();require(!sync_on,"last photo timer expires window");
  } else if(mode=="native") {
    uint8_t base[4]{};
    expected_base=base;
    int ctx=7;
    sub_82546460(ctx,base);
    sub_82C8FE60(ctx,base);
    require(ctx==9 && sync_on && timers.size()==2,"both hooks forward original context/base and hold sync");
  } else if(mode=="disabled") {
    #ifdef _WIN32
    _putenv_s("FORZA_PHOTO_SYNC","0");
#else
    setenv("FORZA_PHOTO_SYNC","0",1);
#endif
    forza::thumbnail_sync::HoldForPhotoMode();
    require(!sync_on&&timers.empty(),"FORZA_PHOTO_SYNC=0 leaves sync unchanged");
  }
  std::cout<<"PASS "<<mode<<'\n';
}
