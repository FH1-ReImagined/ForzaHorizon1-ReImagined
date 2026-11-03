// forza - ambient occlusion option (docs/graphics-detail.md, #380).
//
// world_ambient_occlusion darkens the sunlight where the world's surfaces meet (under the cars,
// along curbs and rails, in creases of the ground). The GPU plugin does the work (SDK patch
// 0350, src/graphics/vulkan/fh1_world_ao.cpp): it reads this setting by name, follows its
// changes (the in-game Graphics screen sets it live) and multiplies a screen-space occlusion
// term into the world's sun shadow mask. Off, the default, is the game's image.

#include <rex/cvar.h>

REXCVAR_DEFINE_STRING(world_ambient_occlusion, "off", "Display",
                      "Ambient occlusion on the world's sunlight: off (the game's), low, high");
