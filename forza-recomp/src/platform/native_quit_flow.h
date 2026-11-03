#pragma once
#include <cstdint>
#include <optional>

namespace forza::native_quit {
// Addresses are identity tokens only. The adapter reads an object only while
// its original native call owns its lifetime; this model never dereferences one.
class Flow {
 public:
  enum class Stage { Idle, Dialog, Unload, Manager, Profile, Commit, ProfileExit,
                     ManagerExit, Title, Queued };
  void Cancel() { ++generation_; stage_=Stage::Idle; }
  uint64_t Begin(uint32_t screen,uint32_t owner,uint32_t manager) {
    Cancel();
    if(!screen||!owner||!manager)return 0;
    screen_=screen;owner_=owner;manager_=manager;parameter_=0;stage_=Stage::Dialog;
    return generation_;
  }
  bool Parameter(uint32_t parameter,bool three_choices) {
    if(stage_!=Stage::Dialog||!parameter)return false;
    parameter_=parameter;three_choices_=three_choices;return true;
  }
  bool HasDialog() const {return stage_==Stage::Dialog&&parameter_;}
  bool Choose(uint32_t screen,uint32_t parameter,uint32_t choice) {
    if(stage_!=Stage::Dialog||screen!=screen_||parameter!=parameter_)return false;
    if(choice!=2||!three_choices_){Cancel();return false;}
    stage_=Stage::Unload;return true;
  }
  void Unload(uint32_t destination) {
    if(stage_==Stage::Idle||stage_==Stage::Dialog)return;
    if(stage_!=Stage::Unload||destination!=0){Cancel();return;}
    stage_=Stage::Manager;
  }
  uint64_t Manager(uint32_t manager,int32_t user,uint32_t mode) {
    if(stage_!=Stage::Manager)return 0;
    if(manager!=manager_||user!=-1||mode!=1){Cancel();return 0;}
    stage_=Stage::Profile;return generation_;
  }
  bool Profile(uint64_t generation,uint32_t profile,uint32_t volume,
               uint32_t table,bool open) {
    if(generation!=generation_||stage_!=Stage::Profile)return false;
    if(!profile||!volume||table!=0x82026A8C||!open){Cancel();return false;}
    profile_=profile;volume_=volume;stage_=Stage::Commit;committing_=false;
    return true;
  }
  bool Commit(uint64_t generation,uint32_t profile,uint32_t volume,uint32_t mode) {
    if(generation!=generation_||stage_!=Stage::Commit)return false;
    if(profile!=profile_||volume!=volume_||mode!=1||committing_){Cancel();return false;}
    committing_=true;prior_=flush_=0xffffffffu;return true;
  }
  void Prior(uint64_t generation,uint32_t volume,uint32_t status) {
    if(!InCommit(generation,volume))return;
    prior_=status;
    if(status!=0&&status!=996&&status!=997)Cancel();
  }
  void Flush(uint64_t generation,uint32_t volume,bool synchronous,uint32_t status) {
    if(!InCommit(generation,volume))return;
    if(!synchronous||prior_!=0||status!=0){Cancel();return;}
    flush_=status;
  }
  void CommitEnd(uint64_t generation,uint32_t volume,uint32_t status) {
    if(!InCommit(generation,volume))return;
    if(status!=0||prior_!=0||flush_!=0){Cancel();return;}
    stage_=Stage::ProfileExit;
  }
  void ProfileEnd(uint64_t generation,uint32_t profile,uint32_t volume,bool saved) {
    if(generation!=generation_||stage_==Stage::Idle)return;
    if(stage_!=Stage::ProfileExit||profile!=profile_||volume!=volume_||!saved){Cancel();return;}
    stage_=Stage::ManagerExit;
  }
  void ManagerEnd(uint64_t generation) {
    if(generation!=generation_||stage_==Stage::Idle)return;
    if(stage_!=Stage::ManagerExit){Cancel();return;}
    stage_=Stage::Title;
  }
  std::optional<uint64_t> Title(uint32_t owner,uint32_t manager,bool native_ready) {
    if(stage_!=Stage::Title)return {};
    if(owner!=owner_||manager!=manager_||!native_ready){Cancel();return {};}
    stage_=Stage::Queued;return generation_;
  }
  bool Consume(uint64_t generation) {
    if(generation!=generation_||stage_!=Stage::Queued)return false;
    Cancel();return true;
  }
  Stage stage() const{return stage_;}
  uint64_t generation() const{return generation_;}
 private:
  bool InCommit(uint64_t generation,uint32_t volume) const {
    return generation==generation_&&stage_==Stage::Commit&&committing_&&volume==volume_;
  }
  uint64_t generation_=0;
  Stage stage_=Stage::Idle;
  uint32_t screen_=0,owner_=0,manager_=0,parameter_=0,profile_=0,volume_=0;
  uint32_t prior_=0xffffffffu,flush_=0xffffffffu;
  bool three_choices_=false,committing_=false;
};
}
