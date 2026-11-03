#pragma once
#include <cstdint>

namespace forza::pause_car_access {
// A stock Map action is also present in Horizon World. Car changes require
// known Colorado free roam with neither a network nor an active session.
inline bool Allowed(bool known, uint32_t presence, uint32_t region,
                    bool network, bool session) {
  return known && presence == 1 && region == 0 && !network && !session;
}

class ReloadIntent {
public:
  void Changed(uint32_t owner) { owner_ = owner; pending_ = owner != 0; }
  void Clear() { owner_ = 0; pending_ = false; }
  void Bind(uint32_t owner) { if (owner != owner_) Clear(); }
  bool Resume(uint32_t owner, bool allowed) {
    const bool reload = pending_ && owner_ == owner && allowed;
    Clear();
    return reload;
  }
private:
  uint32_t owner_ = 0;
  bool pending_ = false;
};
}
