#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace forza::social_presence {
// FH1 retires the presence party when it creates a peer gameplay session.
inline bool InteractiveSession(uint32_t flags, uint64_t id) {
  return id && (flags & (0x2u | 0x20u));
}
inline uint32_t JoinState(uint32_t flags) {
  // Advertising requires the stock USES_PRESENCE bit. A peer-network session
  // alone (including Find Game's 0x2D) is not an invitation to join.
  if (!(flags & 0x2u) || (flags & (0x200u | 0x400u))) return 0;
  return 0x13u | ((flags & 0x800u) ? 0x100u : 0);
}
struct Car {
  uint32_t model = 0;
  uint16_t pi = 0;
  uint8_t car_class = 0;
  std::string name;
  bool operator==(const Car&) const = default;
};
inline void Append(std::vector<uint8_t>& extra, const Car& car) {
  // Preserve complete native QoS data. Omit optional car details when the
  // existing presence envelope leaves no room under the server's 1024 limit.
  if (!car.model || !car.pi || car.pi > 9999 || car.car_class > 10 || car.name.empty() ||
      car.name.size() > 120 || extra.size()+13+car.name.size()>1024) return;
  extra.insert(extra.end(), {'F','H','1','C',1});
  for (int shift=24;shift>=0;shift-=8) extra.push_back(uint8_t(car.model>>shift));
  extra.push_back(car.car_class);
  extra.push_back(uint8_t(car.pi>>8)); extra.push_back(uint8_t(car.pi));
  extra.push_back(uint8_t(car.name.size()));
  extra.insert(extra.end(),car.name.begin(),car.name.end());
}
inline std::optional<Car> Read(const std::vector<uint8_t>& extra) {
  if(extra.size()<6)return {};
  const size_t qos=size_t(extra[4])<<8|extra[5];
  if(qos>extra.size()-6)return {};
  const size_t start=6+qos;
  static constexpr std::array<uint8_t,5> magic{'F','H','1','C',1};
  if(extra.size()-start<13 || !std::equal(magic.begin(),magic.end(),extra.begin()+start))return {};
  const auto* p=extra.data()+start;
  const size_t size=p[12];
  if(!size||size>120||size>extra.size()-start-13)return {};
  Car car{uint32_t(p[5])<<24|uint32_t(p[6])<<16|uint32_t(p[7])<<8|p[8],
          uint16_t(uint16_t(p[10])<<8|p[11]),p[9],std::string(p+13,p+13+size)};
  if(!car.model||!car.pi||car.pi>9999||car.car_class>10)return {};
  // Names come from peers; keep them inside one HUD/menu line.
  for(auto& c:car.name)if(uint8_t(c)<32||uint8_t(c)==127)c=' ';
  return car;
}
inline const char* ClassName(uint8_t value) {
  static constexpr const char* names[]{"F","E","D","C","B","A","S","R3","R2","R1","X"};
  return value<11?names[value]:"?";
}
inline bool Joinable(uint32_t state,uint64_t session,bool is_friend,bool blocked,bool already_joined) {
  return session && (state&0x13)==0x13 && (!(state&0x100)||is_friend) && !blocked && !already_joined;
}
}
