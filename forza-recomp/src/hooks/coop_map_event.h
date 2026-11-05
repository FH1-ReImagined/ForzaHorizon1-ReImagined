// forza - the Co-op Festival's events on the map and at their start (#309): see
// coop_map_event.cpp. The event card's hooks live in custom_race.cpp and ask here first.
#pragma once

#include <cstdint>

#include <rex/ppc/context.h>

namespace forza::coop_map_event {
// After the card's bind (CCareerEventDescription, TU4): `event` is the card's event id,
// `career_entry` whether its A enters the race (the sign-up at the event's start; the map's
// Details card has none, its A sets a route).
void CardBound(PPCContext& ctx, uint8_t* base, uint32_t card, uint32_t event, bool career_entry);
// The card's input (r4 the input event): true when it was ours (the caller returns 1).
bool CardInput(PPCContext& ctx, uint8_t* base, uint32_t card, uint32_t input);
// The card's destructor.
void CardDestroyed(uint32_t card);
}  // namespace forza::coop_map_event
