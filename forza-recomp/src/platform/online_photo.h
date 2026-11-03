// forza - online photo (#576): photo mode in online sessions, one frame at the shutter.
//
// The pause menu leaves Photo Mode (action 11) out in the online session types (6, 13 and 18:
// Horizon World free roam, lobbies and online events), because the game cannot pause there
// (sub_82584790), and photo mode's picture is many renders of the scene averaged (its
// anti-aliasing and depth of field). With forza_online_photo the pause list gets the entry
// back in those sessions, photo mode's camera and SAVE run as they are, and the picture is a
// single render of the current frame, so the running world does not smear it.
// src/hooks/online_photo.cpp has the details; docs/playing.md the player's side.
#pragma once

#include <cstdint>

#include <rex/ppc/context.h>

namespace forza::online_photo {

// The game's online session types, as the pause builder and the pause check test them.
constexpr bool IsOnlineType(uint32_t type) { return type == 6 || type == 13 || type == 18; }

// Where the online entry goes in the pause list: before the first of the rows the builder
// adds after Photo Mode offline (5, 8, 12), or at the end. ids: the rows' actions in order.
template <typename Ids>
size_t RowPosition(const Ids& ids) {
  size_t i = 0;
  for (const auto id : ids) {
    if (id == 5 || id == 8 || id == 12) return i;
    ++i;
  }
  return i;
}

// What an accumulation request (sub_825F3F88) becomes in an online photo. samples: the
// request's count (0xFFFFFFFF: the whole sample list); mode 1 is the shutter, the others
// photo mode's preview; camera: the request comes from a camera update (sub_82704838).
struct Accumulation {
  bool send = true;       // pass the request on
  uint32_t samples = 0;   // with this count
};
class Shutter {
 public:
  Accumulation Request(uint32_t samples, uint32_t mode, bool camera) {
    if (mode == 1) {
      held_ = true;  // the shutter: the photo is on screen until the camera is back
    } else if (!camera) {
      held_ = false;  // photo mode's own preview: back in the camera
    } else if (held_) {
      return {false, samples};  // the world's camera update would restart the photo
    }
    return {true, samples > 1 ? 1u : samples};
  }
  bool Held() const { return held_; }
  void Reset() { held_ = false; }

 private:
  bool held_ = false;
};

bool Enabled();
// Whether the pause screen's list should get the online entry; records whether the current
// pause menu is an online one (Active). Guest UI thread.
bool WantsPauseRow(const PPCContext& ctx, uint8_t* base, uint32_t screen);
// The last pause menu was built in an online session with the entry added.
bool Active();

}  // namespace forza::online_photo
