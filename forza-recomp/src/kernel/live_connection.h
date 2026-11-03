#pragma once

#include <chrono>
#include <cstdint>

namespace forza::live_connection {

using Clock = std::chrono::steady_clock;

inline bool IsFresh(bool enabled, uint32_t player_id,
                    Clock::time_point received, Clock::time_point now) {
  return enabled && player_id && received != Clock::time_point{} &&
         now >= received && now - received <= std::chrono::seconds(20);
}

}  // namespace forza::live_connection
