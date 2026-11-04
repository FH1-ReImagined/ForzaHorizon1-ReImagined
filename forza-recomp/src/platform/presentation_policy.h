// Copyright (c) 2026 Forza Horizon 1: ReImagined contributors.
// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>

namespace forza::ultrawide {
// Evidence expires after a bounded number of presentations. Map evidence wins
// over a world drawn behind the map. Counts avoid a sentinel for frame zero.
class PresentationPolicy {
 public:
  void Observe(bool gameplay, bool map) {
    if (gameplay) { acquisition_ = 2; retention_ = 30; }
    if (map) protected_ = 3;
  }
  bool Advance(bool eligible) {
    active_ = eligible && !protected_ &&
              (active_ ? retention_ != 0 : acquisition_ != 0);
    Tick(acquisition_); Tick(retention_); Tick(protected_);
    return active_;
  }
  bool active() const { return active_; }
 private:
  static void Tick(uint8_t& remaining) { if (remaining) --remaining; }
  uint8_t acquisition_ = 0, retention_ = 0, protected_ = 0;
  bool active_ = false;
};
}  // namespace forza::ultrawide
