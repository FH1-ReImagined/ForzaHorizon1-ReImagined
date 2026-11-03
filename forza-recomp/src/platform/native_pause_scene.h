#pragma once
#include <cstdint>
struct PPCContext;

// FH1-native composition. The original pause owns its model and callbacks;
// ownership transfers only after every required donor passes validation.
namespace forza::native_pause_scene {
bool Owns(uint32_t screen);
bool Input(PPCContext&, uint8_t* base);
bool Page(PPCContext&, uint8_t* base, uint32_t screen, bool show);
void Destroy(PPCContext&, uint8_t* base, uint32_t screen);
void AfterBind(PPCContext&, uint8_t* base, uint32_t screen);
void Tick(PPCContext&, uint8_t* base, uint32_t screen);
void PartyTick(PPCContext&, uint8_t* base, uint32_t screen);
void PartyInput(PPCContext&, uint8_t* base, uint32_t screen, uint32_t event);
}
