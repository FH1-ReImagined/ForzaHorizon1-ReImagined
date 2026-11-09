#pragma once
#include <filesystem>
#include <cstdint>
#include <string_view>
struct PPCContext;

namespace forza::graphics_menu {
void Initialize(const std::filesystem::path& data_root);
void Tick(PPCContext& ctx, uint8_t* base);
}

namespace forza {
// vulkan_present_on_submission_thread's default for this upscaler (fh1_upscaler's value): on,
// except with a VR headset, on macOS and with NVIDIA DLSS (src/main.cpp). A value from forza.toml
// or the command line is kept.
void ApplyPresentOnSubmissionDefault(std::string_view upscaler);
}
