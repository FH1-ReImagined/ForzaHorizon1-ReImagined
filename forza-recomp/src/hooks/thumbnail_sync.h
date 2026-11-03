// forza - renders made once are drawn with real pipelines (src/hooks/thumbnail_sync.cpp).

#pragma once

namespace forza::thumbnail_sync {

// A photo mode frame (src/hooks/shadow_filter.cpp): holds sync_pipeline_creation as the car
// picture and livery renders do, until 8 s after the last one (#167).
void HoldForPhotoMode();

}  // namespace forza::thumbnail_sync
