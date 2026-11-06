// rexgpu-regpu.dll: loaded by ReXGlue's LoadGpuPlugin("regpu") (--gpu_plugin=regpu).
#include <string_view>

#include <rex/system/gpu_plugin.h>

#include "regpu_graphics_system.h"

extern "C" REX_GPU_PLUGIN_EXPORT uint32_t rex_gpu_abi_version(void)
{
    return rex::system::kGpuPluginAbiVersion;
}

extern "C" REX_GPU_PLUGIN_EXPORT rex::system::IGraphicsSystem* rex_gpu_create(
    uint32_t abi_version, const rex::system::GpuCreateInfo* info)
{
    if (abi_version != rex::system::kGpuPluginAbiVersion || !info ||
        info->struct_size < sizeof(rex::system::GpuCreateInfo))
        return nullptr;
    // ReGPU is Direct3D 12 only. FH1 passes its gpu_backend setting here on Windows.
    std::string_view backend = info->backend ? info->backend : "any";
    if (backend != "any" && backend != "d3d12")
        return nullptr;
    return new glue2gpu::ReGpuGraphicsSystem();
}
