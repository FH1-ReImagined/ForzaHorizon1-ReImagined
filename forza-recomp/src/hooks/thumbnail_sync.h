// forza - renders made once are drawn with real pipelines (src/hooks/thumbnail_sync.cpp).

#pragma once

namespace forza::thumbnail_sync {

// A photo mode frame (src/hooks/shadow_filter.cpp): holds sync_pipeline_creation as the car
// picture and livery renders do, until 8 s after the last one (#167).
void HoldForPhotoMode();

// A frame of a replay movie (src/hooks/replay_movie.cpp): the same hold from the capture's start
// to 8 s after its last frame (#880).
void HoldForReplayMovie();

}  // namespace forza::thumbnail_sync
