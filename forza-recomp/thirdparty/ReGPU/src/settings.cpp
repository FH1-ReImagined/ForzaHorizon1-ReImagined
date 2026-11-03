// Config and Host storage, and Start (include/regpu/regpu.h).
#include "settings.h"

#include <algorithm>

#include "debug.h"
#include "memory.h"
#include "gpu/renderer.h"

void PresenterInit();
bool PresenterHasFocus();
void GpuLogState();
void GpuInit();

namespace regpu
{
Config& GetConfig()
{
    static Config config;
    return config;
}

Host& GetHost()
{
    static Host host;
    return host;
}

void Start()
{
    g_base = GetHost().base;
    DebugServerStart(); // REGPU_DEBUG=1: tools/regpuctl.py drives the running game
    PresenterInit();
    GpuInit();
}

void Config::RenderSize(uint32_t& w, uint32_t& h) const
{
    w = width;
    h = height;
    // The forced aspect, or the resolution's own; never narrower than 16:9 (narrower views
    // are not supported yet: such resolutions get 16:9 with bars).
    uint64_t aw = aspectW, ah = aspectH;
    if (!aw || !ah)
        aw = width, ah = height;
    if (aw * 9 < ah * 16)
        aw = 16, ah = 9;
    // Fit that aspect inside the resolution.
    if (uint64_t(width) * ah > uint64_t(height) * aw)
        w = uint32_t(uint64_t(height) * aw / ah) & ~1u;
    else
        h = uint32_t(uint64_t(width) * ah / aw) & ~1u;
}

float Config::SceneScale() const
{
    if (upscaler == Upscaler::Off)
        return 1.0f;
    // The usual presets (native, 1.5x, 1.7x, 2x, 3x per axis).
    static const float scales[] = { 1.0f, 1.0f / 1.5f, 1.0f / 1.7f, 0.5f, 1.0f / 3.0f };
    return scales[std::min<uint32_t>(quality, 4)];
}
} // namespace regpu

void regpu::SaveCaches() { RendererSaveCaches(); }

bool regpu::WindowHasFocus() { return PresenterHasFocus(); }
void regpu::LogState() { GpuLogState(); }
void regpu::LogCrashContext() { RendererLogCrashContext(); }
