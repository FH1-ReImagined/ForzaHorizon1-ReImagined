#pragma once
#include <cstdint>

namespace forza::telemetry {
// Raw, post-overlay player-one inputs. These are not remapped game actions.
void Input(bool connected, uint8_t left, uint8_t right, uint16_t buttons, int16_t steer);
// The game's rumble request for player one (XamInputSetState); tells driving from a menu.
void Rumble(uint16_t left, uint16_t right);
void Physics(uint8_t* base);
// The player's car in the world (x east, z as the track files store it), when one is loaded.
bool LocalCarPosition(uint8_t* base, float& x, float& z);
}
