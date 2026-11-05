// forza - the owners of readback_resolve_wait_range (src/platform/readback_wait.h).
#include "readback_wait.h"

#include <rex/cvar.h>

#include <mutex>
#include <string>

namespace forza::readback_wait {
namespace {
std::mutex mutex;
Ranges ranges;
uint64_t applied = 0;
}  // namespace

bool Request(Owner owner, uint64_t range) {
  std::lock_guard lock(mutex);
  const uint64_t cover = ranges.Set(owner, range);
  if (cover == applied) return true;
  if (!rex::cvar::SetFlagByName("readback_resolve_wait_range", std::to_string(cover))) {
    return false;
  }
  applied = cover;
  return true;
}

}  // namespace forza::readback_wait
