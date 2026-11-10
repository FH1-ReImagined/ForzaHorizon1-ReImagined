#pragma once
#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>

namespace forza::telemetry {
// "The player is driving" (#224), shared by the trigger effects and anything else that must
// stop in menus, pause and replays (wheel force feedback, #256). The car stays in the game's
// vehicle list in menus (garage, showroom), so a local car and fresh input do not tell a menu
// from the road. The game's own rumble does: it sets the pad's motors (XamInputSetState) while
// the car runs, and not in menus or pause. Quiet moments (an idle engine on smooth tarmac) are
// bridged by a hold. Read it with forza::telemetry::IsDriving().
constexpr uint64_t kDrivingHoldMs = 3000;
struct DrivingGate {
  std::atomic<uint64_t> rumble_ms{0};  // last time the game asked for a non-zero rumble; 0 = never
  void Rumble(uint16_t left, uint16_t right, uint64_t now) {
    if (left || right) rumble_ms.store(now ? now : 1, std::memory_order_relaxed);
  }
  bool Driving(uint64_t now) const {
    const uint64_t last = rumble_ms.load(std::memory_order_relaxed);
    return last && now - last < kDrivingHoldMs;
  }
  bool Seen() const { return rumble_ms.load(std::memory_order_relaxed) != 0; }
};
inline uint64_t DrivingNowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline DrivingGate& Gate() { static DrivingGate gate; return gate; }
// Thread safe; no telemetry bridge needed. Fed by the XamInputSetState hook.
inline bool IsDriving() { return Gate().Driving(DrivingNowMs()); }

// "The player drives" for the racing wheel (#256, #304), from the game's own simulation state,
// which the world update hook (hooks/telemetry.cpp) reads once a frame: the world object (the main
// loop's game + 4, the same layout in TU4) has a pause flag (+0x0C: the pause and rewind menus,
// replays, the second after Resume from here), a time scale (+0x28: -1 while a rewind plays the car
// backwards, its physics still stepping) and a started flag (+0x0D: clear while it loads); the
// physics substep hook tells whether the car's physics stepped (sub_82D4DE30). The pad polls tell
// menus: the game polls every frame, also where no world is updated (main menu, event menus).
// Counted in frames, never in elapsed time alone: a hitch is one long frame (a shader compile, a
// collision's physics burst, a window losing focus, streaming), and it is not a pause. The physics
// gate before this one (#256) ended a run after 250 ms without a substep, so every hitch that long
// faded the forces out and back in over a second (#304).
// A state counts after kGateFrames frames in a row; driving also after kDrivingSettleMs, which
// leaves out most of the burst of physics when a replay starts. A frame where the physics did not
// step (a frame of 0.1 ms) counts for nothing, unless kIdleFrames of them last kIdleMs.
enum class Drive : uint8_t { kUnknown, kDriving, kPause, kRewind, kLoading, kMenu, kStopped };
inline const char* DriveName(Drive d) {
  switch (d) {
    case Drive::kDriving: return "driving";
    case Drive::kPause: return "pause";
    case Drive::kRewind: return "rewind";
    case Drive::kLoading: return "loading";
    case Drive::kMenu: return "menu or loading";
    case Drive::kStopped: return "the car's physics stopped";
    default: return "unknown";
  }
}
struct WorldFrame {
  bool paused = false;     // the pause flag (+0x0C)
  bool rewinding = false;  // the time scale (+0x28) is below 0
  bool loaded = true;      // the started flag (+0x0D)
  bool stepped = false;    // the car's physics stepped during this update
};
constexpr int kGateFrames = 3;
constexpr uint64_t kDrivingSettleMs = 500;
constexpr int kIdleFrames = 30;
constexpr uint64_t kIdleMs = 500;
constexpr int kMenuPolls = 24;  // pad polls without a world update (the game polls 4 a frame)
constexpr uint64_t kMenuMs = 300;
class PlayerGate {
 public:
  // One world update. Called on the game's main loop thread, as the polls are; locked anyway.
  void Frame(uint64_t now, const WorldFrame& f) {
    std::lock_guard lock(mutex_);
    polls_ = 0;
    last_frame_ms_ = now ? now : 1;
    Drive d;
    if (f.rewinding) {
      d = Drive::kRewind;
      rewound_ = true;
    } else if (f.paused) {
      d = rewound_ ? Drive::kRewind : Drive::kPause;  // the rewind menu, or the pause menu
    } else if (!f.loaded) {
      d = Drive::kLoading;
    } else if (f.stepped) {
      d = Drive::kDriving;
    } else {
      if (idle_frames_++ == 0) idle_since_ = now;
      if (idle_frames_ < kIdleFrames || now - idle_since_ < kIdleMs) return;
      d = Drive::kStopped;
    }
    if (d != Drive::kStopped) idle_frames_ = 0;
    if (d == candidate_) {
      candidate_frames_++;
    } else {
      candidate_ = d;
      candidate_frames_ = 1;
      candidate_since_ = now;
    }
    if (d == state_ || candidate_frames_ < kGateFrames) return;
    if (d == Drive::kDriving && now - candidate_since_ < kDrivingSettleMs) return;
    state_ = d;
    if (d == Drive::kDriving) rewound_ = false;
  }
  // One pad poll.
  void Poll(uint64_t now) {
    std::lock_guard lock(mutex_);
    if (!last_frame_ms_) last_frame_ms_ = now ? now : 1;  // counted from the first poll
    if (state_ == Drive::kMenu || ++polls_ < kMenuPolls || now - last_frame_ms_ < kMenuMs) return;
    state_ = candidate_ = Drive::kMenu;
    candidate_frames_ = 0;
    rewound_ = false;
    idle_frames_ = 0;
  }
  Drive State() {
    std::lock_guard lock(mutex_);
    return state_;
  }

 private:
  std::mutex mutex_;
  Drive state_ = Drive::kUnknown;
  Drive candidate_ = Drive::kUnknown;
  int candidate_frames_ = 0;
  uint64_t candidate_since_ = 0;
  int idle_frames_ = 0;
  uint64_t idle_since_ = 0;
  bool rewound_ = false;  // a rewind played since the player last drove: its menu is a rewind
  int polls_ = 0;
  uint64_t last_frame_ms_ = 0;
};
inline PlayerGate& Player() { static PlayerGate gate; return gate; }

// The DualSense and Xbox trigger effects' gate with the physics bridge (#692). PlayerGate alone
// misses the pause menu online: an online world does not pause (no pause flag, the car's physics
// keep stepping), so in online free roam the triggers stayed on in the pause menu and its pages
// (#224 again from 0.2.5, when the launcher turned the physics bridge on and this gate replaced
// the rumble gate). The game stops its rumble in every menu, online too, so a game that rumbles
// must also have rumbled within kDrivingHoldMs. A game that never rumbled (vibration off in its
// options, a racing wheel) is judged by PlayerGate alone.
enum class Triggers : uint8_t { kOn, kNotDriving, kNoRumble };
inline Triggers TriggerGate(Drive state, const DrivingGate& rumble, uint64_t now) {
  if (state != Drive::kDriving) return Triggers::kNotDriving;
  if (rumble.Seen() && !rumble.Driving(now)) return Triggers::kNoRumble;
  return Triggers::kOn;
}
}
