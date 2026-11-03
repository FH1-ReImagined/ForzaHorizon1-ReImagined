#pragma once
#include <cstdint>

#include <d3d12.h>

// NVIDIA DLSS / DLAA (dlss.cpp).
struct DlssSetup
{
    uint32_t renderWidth = 0, renderHeight = 0, outputWidth = 0, outputHeight = 0;
    uint32_t quality = 1; // settings.h: 0 native (DLAA) .. 4 ultra performance
    bool hdr = false, depthInverted = true;
    bool operator==(const DlssSetup&) const = default;
};

struct DlssFrame
{
    // Inputs in NON_PIXEL_SHADER_RESOURCE state, the output in UNORDERED_ACCESS.
    ID3D12Resource *color, *depth, *motion, *output;
    float jitterX, jitterY;           // render pixels
    float motionScaleX, motionScaleY; // motion texture values to render pixels
    float frameTimeMs;
    bool reset;
};

// False without an RTX GPU, driver support or nvngx_dlss.dll.
bool DlssInit(ID3D12Device* device);
// Records the upscale into `list` (the real command list).
bool DlssEvaluate(ID3D12GraphicsCommandList* list, const DlssSetup& setup, const DlssFrame& frame);
