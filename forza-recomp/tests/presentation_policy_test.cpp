#include "../src/platform/presentation_policy.h"
#include <cassert>
#include <initializer_list>
using forza::ultrawide::PresentationPolicy;
int main() {
  PresentationPolicy policy;
  assert(!policy.Advance(true));
  policy.Observe(true, false);  // Evidence before the first presentation.
  for (int i = 0; i < 30; ++i) assert(policy.Advance(true));
  assert(!policy.Advance(true));
  for (bool map_first : {false, true}) {
    policy = {};
    policy.Observe(!map_first, map_first);
    policy.Observe(map_first, !map_first);
    for (int i = 0; i < 3; ++i) assert(!policy.Advance(true));
    assert(!policy.Advance(true));  // Stale world must not reacquire.
    policy.Observe(true, false);
    assert(policy.Advance(true));
  }
  policy.Observe(false, true);
  for (int i = 0; i < 3; ++i) {
    policy.Observe(true, false);  // World rendering behind the map.
    assert(!policy.Advance(true));
  }
  assert(policy.Advance(true));
  assert(!policy.Advance(false));
  policy = {};  // Reinitialization cannot retain the old world's lease.
  assert(!policy.Advance(true));
}
