#pragma once
#include <cstdint>
#include <filesystem>
#include <vector>

// Writes RGBA8 as an uncompressed PNG (debug captures).
bool WritePng(const std::filesystem::path& path, uint32_t width, uint32_t height, const std::vector<uint8_t>& rgba);
