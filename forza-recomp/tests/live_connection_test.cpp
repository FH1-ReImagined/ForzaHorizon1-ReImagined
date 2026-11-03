#include "kernel/live_connection.h"

#include <cstdlib>
#include <iostream>

namespace {
void Check(bool condition, const char* description) {
  if (!condition) {
    std::cerr << description << '\n';
    std::exit(1);
  }
}
}

int main() {
  using namespace std::chrono;
  using forza::live_connection::Clock;
  using forza::live_connection::IsFresh;
  const Clock::time_point welcome(seconds(100));
  Check(!IsFresh(false, 7, welcome, welcome), "Disabled network must be offline");
  Check(!IsFresh(true, 0, welcome, welcome), "Missing registration must be offline");
  Check(!IsFresh(true, 7, {}, welcome), "Configuration alone must be offline");
  Check(IsFresh(true, 7, welcome, welcome), "Welcome establishes contact");
  Check(IsFresh(true, 7, welcome, welcome + seconds(20)), "20-second boundary is live");
  Check(!IsFresh(true, 7, welcome, welcome + seconds(20) + nanoseconds(1)),
        "Contact expires after 20 seconds");
  Check(!IsFresh(true, 7, welcome, welcome + seconds(80)),
        "Retry intervals cannot revive a stale receipt");
  Check(IsFresh(true, 7, welcome + seconds(80), welcome + seconds(81)),
        "New welcome restores contact");
  Check(!IsFresh(true, 7, welcome, welcome - seconds(1)),
        "Future receipt must not appear live");
  std::cout << "Live connection: 9 checks passed\n";
}
