// forza - a world past 8,192 m.
//
// The game keeps camera-relative positions as 32-bit fixed point with 18 fraction bits
// (Vec4Fixed<18> in the FH2 prototype's symbols, where it is Vec4Fixed<17>): 4 micrometre
// steps and +-8,192 m, saturating. Colorado fits. A converted world that reaches further
// (FH2's is 10.8 km east of its origin, Fast & Furious's 8.5 km south) stops there: the chase
// camera no longer follows the car, and the zones round a start past the limit never stream.
//
// Every conversion to or from the type is one vector instruction with the bit count in it
// (83 in Title Update 4: the cameras, CMultiCam::UpdateViewAudioData, CGame::Update, the
// cars, the render thread, ...). hints_tu4/wide_world.toml puts a hook before each
// (tools/wide_world_hooks.py finds them in the image and checks the list). With the switch
// off, the default, a hook returns false and the game's instruction runs unchanged. With
// it on, the hook converts with 17 bits (8 micrometre steps, +-16,384 m) and the instruction
// is jumped over.
//
// The switch is read once, before the game runs: values of both scales must never meet.
// FORZA_WIDE_WORLD=1 turns it on; fm4_track_mount.cpp gives it that default for a mounted
// world whose folder has a wide_world.txt. Positions sent to other players are not looked
// at: a wide world is for players who all run it.

#include "wide_world.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/intrinsics.h>

#include <cstdlib>
#include <cstring>

namespace {

bool wide = false;

constexpr float kToFixed = 131072.0f;          // 2^17
constexpr float kToFloat = 1.0f / 131072.0f;

}  // namespace

namespace forza::wide_world {

void Configure() {
  const char* value = std::getenv("FORZA_WIDE_WORLD");
  wide = value && *value && std::strcmp(value, "0") != 0;
  if (wide)
    REXLOG_INFO("Wide world: positions kept with 17 fraction bits (+-16,384 m) instead of 18");
}

bool Enabled() { return wide; }

}  // namespace forza::wide_world

// vcfpsxws128 / vctsxs vD, vB, 18: float to saturated fixed point.
bool ForzaWideWorldToFixed(PPCFPSCRRegister& fpscr, PPCVRegister& to, PPCVRegister& from) {
  if (!wide) [[likely]] return false;
  fpscr.enableFlushMode();
  simde_mm_store_si128(
      (simde__m128i*)to.s32,
      rex::ppc::simde_mm_vctsxs(simde_mm_mul_ps(simde_mm_load_ps(from.f32), simde_mm_set1_ps(kToFixed))));
  return true;
}

// The same with one register (the generated declaration names a hook's parameters after its
// registers, so a conversion in place cannot pass its register twice).
bool ForzaWideWorldToFixedInPlace(PPCFPSCRRegister& fpscr, PPCVRegister& v) {
  return ForzaWideWorldToFixed(fpscr, v, v);
}

bool ForzaWideWorldToFloat(PPCFPSCRRegister& fpscr, PPCVRegister& to, PPCVRegister& from);
bool ForzaWideWorldToFloatInPlace(PPCFPSCRRegister& fpscr, PPCVRegister& v) {
  return ForzaWideWorldToFloat(fpscr, v, v);
}

// vcsxwfp128 / vcfsx vD, vB, 18: fixed point to float.
bool ForzaWideWorldToFloat(PPCFPSCRRegister& fpscr, PPCVRegister& to, PPCVRegister& from) {
  if (!wide) [[likely]] return false;
  fpscr.enableFlushMode();
  simde_mm_store_ps(to.f32, simde_mm_mul_ps(simde_mm_cvtepi32_ps(simde_mm_load_si128((simde__m128i*)from.u32)),
                                            simde_mm_set1_ps(kToFloat)));
  return true;
}
