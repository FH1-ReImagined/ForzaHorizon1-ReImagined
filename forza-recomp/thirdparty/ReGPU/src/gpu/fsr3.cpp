// AMD FSR 3 upscaling (FidelityFX API, MIT): the same inputs as DLSS (see dlss.h). Needs
// amd_fidelityfx_loader_dx12.dll and amd_fidelityfx_upscaler_dx12.dll next to the
// executable (copied there by the build); runs on any D3D12 GPU.
#include "gpu/fsr3.h"

#include <ffx_api.h>
#include <ffx_upscale.h>
#include <dx12/ffx_api_dx12.h>

#include "log.h"

namespace
{
struct Fsr3
{
    bool failed = false;
    ffxContext context = nullptr;
    DlssSetup setup{};
} g_fsr3;
}

bool Fsr3Evaluate(ID3D12Device* device, ID3D12GraphicsCommandList* list, const DlssSetup& setup, const DlssFrame& frame, float sharpness)
{
    if (g_fsr3.failed)
        return false;
    if (!g_fsr3.context || !(setup == g_fsr3.setup))
    {
        if (g_fsr3.context)
        {
            ffxDestroyContext(&g_fsr3.context, nullptr);
            g_fsr3.context = nullptr;
        }
        ffxCreateBackendDX12Desc backend{};
        backend.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_BACKEND_DX12;
        backend.device = device;
        ffxCreateContextDescUpscale create{};
        create.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
        create.header.pNext = &backend.header;
        create.maxRenderSize = { setup.renderWidth, setup.renderHeight };
        create.maxUpscaleSize = { setup.outputWidth, setup.outputHeight };
        create.flags = FFX_UPSCALE_ENABLE_AUTO_EXPOSURE | (setup.depthInverted ? FFX_UPSCALE_ENABLE_DEPTH_INVERTED : 0) |
            (setup.hdr ? FFX_UPSCALE_ENABLE_HIGH_DYNAMIC_RANGE : 0);
        const ffxReturnCode_t r = ffxCreateContext(&g_fsr3.context, &create.header, nullptr);
        if (r != FFX_API_RETURN_OK)
        {
            Log("fsr3: create failed (%d): amd_fidelityfx_*_dx12.dll missing?", int(r));
            g_fsr3.context = nullptr;
            g_fsr3.failed = true;
            return false;
        }
        g_fsr3.setup = setup;
        Log("fsr3: %ux%u -> %ux%u%s", setup.renderWidth, setup.renderHeight, setup.outputWidth, setup.outputHeight, setup.hdr ? ", HDR" : "");
    }
    ffxDispatchDescUpscale d{};
    d.header.type = FFX_API_DISPATCH_DESC_TYPE_UPSCALE;
    d.commandList = list;
    d.color = ffxApiGetResourceDX12(frame.color, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    d.depth = ffxApiGetResourceDX12(frame.depth, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    d.motionVectors = ffxApiGetResourceDX12(frame.motion, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    d.output = ffxApiGetResourceDX12(frame.output, FFX_API_RESOURCE_STATE_UNORDERED_ACCESS);
    d.jitterOffset = { frame.jitterX, frame.jitterY };
    d.motionVectorScale = { frame.motionScaleX, frame.motionScaleY };
    d.renderSize = { setup.renderWidth, setup.renderHeight };
    d.upscaleSize = { setup.outputWidth, setup.outputHeight };
    d.enableSharpening = sharpness > 0.0f;
    d.sharpness = sharpness;
    d.frameTimeDelta = frame.frameTimeMs;
    d.preExposure = 1.0f;
    d.reset = frame.reset;
    // The game's camera is unknown here; typical values (used for depth-based heuristics).
    d.cameraNear = 0.1f;
    d.cameraFar = 10000.0f;
    d.cameraFovAngleVertical = 1.0f;
    d.viewSpaceToMetersFactor = 1.0f;
    const ffxReturnCode_t r = ffxDispatch(&g_fsr3.context, &d.header);
    if (r != FFX_API_RETURN_OK)
    {
        static int logged = 0;
        if (logged++ < 3)
            Log("fsr3: dispatch failed (%d)", int(r));
        return false;
    }
    return true;
}
