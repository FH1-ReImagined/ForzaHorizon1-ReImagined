#pragma once

#include <cstdint>

namespace forza {
void PatchMotionBlurStencilThreshold();
void SetMotionBlurEnabled(bool enabled);
bool MotionBlurEnabled();
}  // namespace forza
