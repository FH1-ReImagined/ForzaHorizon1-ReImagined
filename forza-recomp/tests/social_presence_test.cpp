#include "../src/platform/social_presence.h"
#include "../src/platform/world_session.h"
#include <cassert>

int main(){
  assert(forza::world_session::IsWorld(3,7,7));
  assert(forza::world_session::IsWorld(20,7,7)); // Co-op campaign's world playlist.
  assert(!forza::world_session::IsWorld(20,7,1)); // Campaign races keep race rules.
  assert(forza::world_session::IsWorld(0,7,7)); // Invite, no local search.
  assert(!forza::world_session::IsWorld(2,7,7));
  assert(!forza::world_session::IsWorld(0,7,1));
  assert(!forza::world_session::IsWorld(0,10,7));
  using namespace forza::social_presence;
  assert(!InteractiveSession(0x702,0)); // Offline placeholder.
  assert(InteractiveSession(0x823,42)); // Native presence party.
  assert(InteractiveSession(0x2D,42)); // Horizon World gameplay replaces the party.
  assert(InteractiveSession(0x2C,42)); // Joined gameplay session.
  assert(!InteractiveSession(0x4,42)); // Stats-only session is not a playable party.
  assert(JoinState(0x23)==0x13);
  assert(JoinState(0x823)==0x113);
  assert(!JoinState(0x2D) && !JoinState(0x2C));
  assert(!JoinState(0x223) && !JoinState(0x22D));
  assert(!JoinState(0x423) && !JoinState(0xC23)); // No join in progress.
  assert(!JoinState(0xA23) && !JoinState(0xE23)); // Both disabled/private.
  const Car car{123,437,4,"Lancer Evolution X GSR"};
  std::vector<uint8_t> extra{8,0,2,0,0,3,1,2,3};Append(extra,car);
  assert(Read(extra)&&*Read(extra)==car);
  for(size_t size=0;size<extra.size();++size){auto broken=extra;broken.resize(size);assert(!Read(broken));}
  {auto broken=extra;broken[4]=255;broken[5]=255;assert(!Read(broken));}
  {auto broken=extra;broken[13]=2;assert(!Read(broken));}
  {auto dirty=car;dirty.name="Driver\nCar";std::vector<uint8_t> bytes(6);Append(bytes,dirty);assert(Read(bytes)->name=="Driver Car");}
  assert(!Read(std::vector<uint8_t>(6)));assert(std::string(ClassName(4))=="B");
  {std::vector<uint8_t> bytes(1006);const auto before=bytes;Append(bytes,car);assert(bytes==before);}
  assert(Joinable(0x13,42,false,false,false));
  assert(!Joinable(0x13,0,true,false,false));
  assert(!Joinable(0x12,42,true,false,false));
  assert(!Joinable(0x113,42,false,false,false));
  assert(Joinable(0x113,42,true,false,false));
  assert(!Joinable(0x13,42,true,true,false));
  assert(!Joinable(0x13,42,true,false,true));
}
