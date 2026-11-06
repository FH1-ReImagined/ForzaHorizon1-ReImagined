#pragma once
// Every shader in FH1's files, for ReGPU's first-boot compile (Host::staticShaders).
#include <cstdint>
#include <filesystem>
#include <vector>

#include <regpu/regpu.h>

namespace glue2gpu
{
// Where to search (the game's and the title update's data roots), where to keep the list, and the
// guest memory base (the executable image is searched too). Call before regpu::Start.
void SetShaderSources(const std::filesystem::path& game, const std::filesystem::path& update, const std::filesystem::path& saveDir,
                      const uint8_t* guestBase);
std::vector<regpu::ScannedShader> StaticShaders();
bool StaticShadersSaved();
// A member of one of the game's zips (stored or LZX), by its path under the game's data root
// ("media/ui/Textures.zip") and its name in the zip, case-insensitively.
bool ReadGameZipMember(const std::string& zip, const std::string& member, std::vector<uint8_t>& out);
} // namespace glue2gpu
