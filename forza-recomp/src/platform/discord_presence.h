// Optional local Discord Rich Presence. No network services or profile identifiers.
#pragma once
#include <cstdint>

namespace forza::discord {
struct GameDetails;
void Initialize();
void Shutdown();
// XGI's primary-user context updates, also observed while playing offline.
void Context(uint32_t id, uint32_t value);
// Session-local member counts, never the server's global online population.
void Party(uint32_t count, uint32_t capacity);
// The car, class/PI, event and place (discord_model.h GameDetails): only names the game
// shows. Wanted() is false when sharing or discord_presence_details is off.
bool Wanted();
void Details(const GameDetails& details);
}  // namespace forza::discord
