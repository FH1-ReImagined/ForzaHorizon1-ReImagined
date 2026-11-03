#pragma once
#include <cstdint>

namespace forza::world_session {
// An invite can enter Find Game without a local matchmaking search. In that
// case the native mode/preset identify the world; a known other playlist must
// still fail closed.
inline bool IsWorld(uint32_t playlist,uint32_t mode,uint8_t preset) {
  return (!playlist||playlist==3||playlist==20)&&mode==7&&preset==7;
}
}
