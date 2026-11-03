#pragma once
#include <cstdint>
#include <vector>

namespace glue2gpu
{
// The first-boot shader compile screen as 1280x720 RGBA8 (Host::bootScreen): the game's own splash
// background and logo with a progress bar. False when the game's UI textures can't be read (ReGPU's
// plain overlay is shown then). total 0: still searching.
bool BootScreenCompose(const wchar_t* title, uint32_t done, uint32_t total, std::vector<uint8_t>& rgba);
// GLUE2GPU_BOOTSCREEN_PREVIEW=1: writes the candidate textures and a composed screen to
// <cache>/regpu/bootscreen/*.png.
void BootScreenPreview();
} // namespace glue2gpu
