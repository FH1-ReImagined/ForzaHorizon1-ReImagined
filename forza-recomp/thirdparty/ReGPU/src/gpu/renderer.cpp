// Native D3D12 renderer driven by the command stream (phase 2, first cut).
//
// Runs on the command processor thread. For each draw it translates the
// active Xenos shaders to HLSL (cached), uploads the referenced vertex data
// and an expanded index list into a per-frame upload ring, decodes the
// bound textures (cached by content), and draws into one 1280x720 colour
// target with depth. Resolves and other non-colour EDRAM passes are
// skipped; render-target configuration (EDRAM surfaces, MSAA, tiles) comes
// next.

#include <algorithm>
#include <bit>
#include <immintrin.h>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <functional>
#include <fstream>
#include <memory>
#include <mutex>
#include <thread>
#include <chrono>
#include <cmath>
#include <map>
#include <tuple>
#include <set>
#include <shared_mutex>
#include <unordered_set>
#include <unordered_map>

#include <d3d12.h>
#include <dxgi1_4.h>
#include <d3dcompiler.h>
#include <windows.h>
#include <wrl/client.h>

#include "gpu/hlsl.h"
#include "gpu/dlss.h"
#include "gpu/fsr3.h"
#include "gpu/recorded_list.h"
#include "fsr1_cs.h"
#include <DirectXPackedVector.h>
#include "live_switch.h"
#include "settings.h"
#include "gpu/regs.h"
#include "gpu/texture.h"
#include "gpu/ucode.h"
#include "kernel/runtime.h"
#include "kernel/thread.h"
#include "debug.h"
#include "log.h"
#include "png.h"
#include "render/presenter.h"
#include "gpu/renderer.h"
#include "gpu/shader_scan.h"
#include "gpu/resolve_pack.h"

using Microsoft::WRL::ComPtr;


std::atomic<bool> g_gameWidescreen{ false }; // regpu::SetGameWidescreen

namespace
{
constexpr UINT kWidth = 1280, kHeight = 720;
// Output size (RenderSize: REGPU_RESOLUTION or REGPU_RENDER_SCALE). Everything else
// (registers, scissors, resolve and clear rectangles, shader constants) stays in the game's
// own pixels; only the D3D calls scale. Vertically that is output height / 720. A pass at
// the game's full 1280-pixel width (surface pitch 1280: the main view and its full-screen
// post-processing) scales horizontally to the full output width, wider than 16:9 on an
// ultrawide; every other pass (reflection faces, shadow maps, reduced-size post passes)
// scales uniformly, so square targets stay square. Draws, clears and resolves of a pass
// all read the same pitch, so they agree.
// (Changed while the game runs by regpu::SetOutputSize, applied between frames: ApplyOutputSize.)
std::atomic<uint32_t> g_outputWidth{ 0 }, g_outputHeight{ 0 };
void LoadOutputSize()
{
    uint32_t w, h;
    RenderSize(w, h);
    g_outputWidth = w;
    g_outputHeight = h;
}
uint32_t OutputWidth()
{
    if (!g_outputWidth.load(std::memory_order_relaxed))
        LoadOutputSize();
    return g_outputWidth.load(std::memory_order_relaxed);
}
uint32_t OutputHeight()
{
    if (!g_outputHeight.load(std::memory_order_relaxed))
        LoadOutputSize();
    return g_outputHeight.load(std::memory_order_relaxed);
}
// Upscaling: the 3D scene of a frame renders at this fraction of the output (1 otherwise);
// after the scene is upscaled (UpscaleScene), the rest of the frame renders at full size.
float g_sceneScale = 1.0f;
// The scene of this frame is still being drawn and is upscaled when it is done (any
// upscaler; with a temporal one, also at native scale for anti-aliasing).
bool g_scenePhase = false;
// A temporal upscaler this frame: the scene's main view draws with motion vectors and jitter.
bool g_temporal = false;
struct Upscale
{
    bool tried = false, ready = false;
    ComPtr<ID3D12RootSignature> root;
    ComPtr<ID3D12PipelineState> easu, rcas, depthCopy, gamma, enhance;
    ComPtr<ID3D12Resource> dlaaOut; // DLAA output (scene size), copied back into the scene image
    ComPtr<ID3D12Resource> depth; // temporal: the scene's depth as R32 (render size, top left)
    ComPtr<ID3D12Resource> temp; // EASU output (RGBA16F)
    uint32_t tempWidth = 0, tempHeight = 0;
    bool switchedThisFrame = false;
    uint64_t retryFrame = 0; // after a frame with no scene to upscale (menus): native until then
    // Temporal upscalers: the motion target (RG16F, output size; UV offsets to last frame),
    // this frame's jitter (pixels and NDC) and the scene's render size.
    ComPtr<ID3D12Resource> motion;
    ComPtr<ID3D12DescriptorHeap> motionRtvHeap;
    D3D12_CPU_DESCRIPTOR_HANDLE motionRtv{};
    float jitterPx[2] = {}, jitterNdc[2] = {};
    uint32_t renderWidth = 0, renderHeight = 0;
    bool reset = true; // no usable history (first frame, after menus)
    // Each draw's vertex constants, kept for the same draw in the next frame (keyed by
    // shaders, vertex and index data and occurrence).
    struct DrawConstants
    {
        uint32_t frame = 0;                    // the temporal frame that last drew it
        D3D12_GPU_VIRTUAL_ADDRESS address[2]{}; // its vertex constants in that frame's ring, by frame parity
    };
    std::unordered_map<uint64_t, DrawConstants> constants;
    std::unordered_map<uint64_t, uint32_t> occurrences;
    uint32_t constantsFrame = 0;
} g_upscale;

// A pass taller than the screen (a 1024x1024 render target, drawn 720 rows at a time) is scaled as a whole
// by kHeight / its height so it fits the surface, and the vertex shader divides by its height.
// It is found by its window scissor (WindowScissor) and recognised by its surface info.
uint32_t g_tallInfo = 0, g_tallHeight = 0, g_tallKey = ~0u;
std::atomic<bool> g_tallSeen{ false }; // REGPU_CAPTURE_TALL: a tall pass was drawn
// A surface wider than the screen (a 2048x2048 composite target, drawn in 256-row
// tiles) is a square pass of its pitch less the tile padding (2080 -> 2048, 1040 -> 1024).
// The MSAA layout each EDRAM surface is drawn in (its last draw that is not a texture-less
// rectangle), by colour base and row width in samples: views of the same EDRAM with another
// MSAA mode (clears) are mapped onto it. Samples per pixel: 1x 1x1, 2x 1x2, 4x 2x2.
std::atomic<uint32_t> g_nativeMsaa[0x1000]; // key << 2 | MSAA mode
uint32_t MsaaAcross(uint32_t msaa) { return msaa == 2 ? 2u : 1u; }
uint32_t MsaaDown(uint32_t msaa) { return msaa ? 2u : 1u; }
uint32_t LayoutKey(uint32_t surface)
{
    return (gpureg::Get(0x2001 /* RB_COLOR_INFO */) & 0xFFF) | (surface & 0x3FFF) * MsaaAcross((surface >> 16) & 3) << 12;
}
std::atomic<uint32_t>& NativeSlot(uint32_t key) { return g_nativeMsaa[(key ^ key >> 12) & 0xFFF]; }
std::atomic<uint32_t> g_nativeMsaaGeneration{ 0 }; // bumped when an entry changes (BigPassSize's memo)
void SetNativeMsaa(uint32_t key, uint32_t msaa)
{
    // Read first: written at every draw, the slot's cache line bounced between the draw workers.
    std::atomic<uint32_t>& slot = NativeSlot(key);
    if (slot.load(std::memory_order_relaxed) != (key << 2 | msaa) && slot.exchange(key << 2 | msaa, std::memory_order_relaxed) != (key << 2 | msaa))
        g_nativeMsaaGeneration.fetch_add(1, std::memory_order_relaxed);
}
// The same, for depth surfaces: the MSAA layout each depth EDRAM base and row width (in samples)
// is drawn in. A title can reset a 1x pass's stencil through a 4x view that only shares the depth
// base (FH1's sun-shadow mask: a 640x360 4x rectangle over the 1280x720 mask pass, colour base
// another). Keyed apart from the colour table.
std::atomic<uint32_t> g_nativeDepthMsaa[0x1000]; // key << 2 | MSAA mode
uint32_t DepthLayoutKey(uint32_t surface)
{
    return (gpureg::Get(0x2002 /* RB_DEPTH_INFO */) & 0xFFF) | (surface & 0x3FFF) * MsaaAcross((surface >> 16) & 3) << 12;
}
std::atomic<uint32_t>& NativeDepthSlot(uint32_t key) { return g_nativeDepthMsaa[(key ^ key >> 12) & 0xFFF]; }
// The depth surface's MSAA layout when the current view is another one of it (also of a 1x
// surface), else -1.
int ForeignNativeDepthMsaa()
{
    const uint32_t surface = gpureg::Get(0x2000 /* RB_SURFACE_INFO */), key = DepthLayoutKey(surface);
    const uint32_t native = NativeDepthSlot(key).load(std::memory_order_relaxed);
    return native >> 2 == key && (native & 3) != ((surface >> 16) & 3) ? int(native & 3) : -1;
}
// The surface's MSAA layout when the current view is another one of an MSAA surface, else -1.
int ForeignNativeMsaa()
{
    static LiveSwitch off("REGPU_NO_FOREIGN_MSAA"); // A/B
    if (off)
        return -1;
    const uint32_t surface = gpureg::Get(0x2000 /* RB_SURFACE_INFO */), key = LayoutKey(surface);
    const uint32_t native = NativeSlot(key).load(std::memory_order_relaxed);
    return native >> 2 == key && (native & 3) != ((surface >> 16) & 3) && (native & 3) != 0 ? int(native & 3) : -1;
}

uint32_t BigPassSizeUncached()
{
    uint32_t pitch = gpureg::Get(0x2000 /* RB_SURFACE_INFO */) & 0x3FFF;
    // A view of a big MSAA surface in another MSAA mode belongs to the same pass.
    if (const int native = ForeignNativeMsaa(); native >= 0)
        pitch = pitch * MsaaAcross((gpureg::Get(0x2000) >> 16) & 3) / MsaaAcross(uint32_t(native));
    static LiveSwitch off("REGPU_NO_BIG_PASS");
    return !off && pitch > kWidth ? std::min<uint32_t>(2048, pitch * 64 / 65) : 0;
}
// Asked several times per draw (PassWidth, PassHeight, ScaleX, ScaleY): the answer only changes
// with the surface registers or the layout table.
uint32_t BigPassSize()
{
    struct Memo { uint32_t surface = ~0u, color = ~0u, generation = ~0u, result = 0; };
    thread_local Memo memo;
    const uint32_t surface = gpureg::Get(0x2000), color = gpureg::Get(0x2001) & 0xFFF, generation = g_nativeMsaaGeneration.load(std::memory_order_relaxed);
    if (surface != memo.surface || color != memo.color || generation != memo.generation)
        memo = { surface, color, generation, BigPassSizeUncached() };
    return memo.result;
}
uint32_t PassWidth() { const uint32_t big = BigPassSize(); return big ? big : kWidth; }
// Another MSAA view of the tall pass's EDRAM: a title can clear the stencil of its 1x
// 1024x1024 target through a 4x view (480x512, pitch 520) while a layer is being moved.
bool TallView()
{
    static LiveSwitch off("REGPU_NO_TALL_VIEW"); // A/B
    const uint32_t surface = gpureg::Get(0x2000 /* RB_SURFACE_INFO */);
    return !off && g_tallHeight && surface != g_tallInfo && ((surface ^ g_tallInfo) >> 16 & 3) && LayoutKey(surface) == g_tallKey;
}
uint32_t PassHeight()
{
    if (const uint32_t big = BigPassSize())
        return big;
    const uint32_t surface = gpureg::Get(0x2000 /* RB_SURFACE_INFO */);
    return g_tallHeight && (surface == g_tallInfo || TallView()) ? g_tallHeight : kHeight;
}
// Guest rows the host EDRAM surfaces hold at full scale. A 1024-row pass (a 1024x1024 shadow
// atlas drawn as one tall pass) used to be squeezed into the 720 rows of the output: at a 720p
// render size FH1's four shadow cascades had 70% of the console's resolution (720x720 for its
// 1024x1024 atlas). REGPU_SURFACE_ROWS=720 squeezes as before (A/B).
uint32_t SurfaceRows()
{
    static const uint32_t rows = [] {
        const char* v = getenv("REGPU_SURFACE_ROWS");
        return v ? std::clamp<uint32_t>(uint32_t(atoi(v)), kHeight, 2048) : 1024u;
    }();
    return rows;
}
// The whole-number scale a tall pass is drawn at (IntegerTallScale): the output's scale rounded up.
// The host surfaces are made large enough for it (at 1080p, 2x: as large as 1440p's).
uint32_t TallScaleRoom() { return std::max<uint32_t>(1, (OutputHeight() + kHeight - 1) / kHeight); }
uint32_t SurfaceWidth() { return std::max(OutputWidth(), TallScaleRoom() * kWidth); }
uint32_t SurfaceHeight()
{
    return std::max(uint32_t((uint64_t(OutputHeight()) * SurfaceRows() + kHeight - 1) / kHeight), TallScaleRoom() * SurfaceRows());
}

// A tall pass (a shadow atlas) is drawn at a whole number of host texels per guest texel: titles move
// a shadow projection in steps of one guest texel to hold it still, and at a fractional scale (1.5 at
// 1080p) each step put the host texel grid at another phase, so alpha-tested edges (FH1's tree
// leaves) rasterised differently every frame and their shadows shimmered, more while moving. Rounded
// up when the surfaces hold it (rounded down to 1x at 1080p the near shadows still shimmered a little
// and distant ones more, at the console's own resolution), else down.
// REGPU_FRACTIONAL_TALL_SCALE=1: the output's scale as before (A/B).
float IntegerTallScale(float passScale)
{
    static LiveSwitch fractional("REGPU_FRACTIONAL_TALL_SCALE");
    if (fractional || BigPassSize() || PassHeight() <= kHeight)
        return passScale;
    const float total = float(OutputHeight()) / float(kHeight) * g_sceneScale * passScale;
    if (total <= 1.0f)
        return passScale;
    const uint32_t pitch = gpureg::Get(0x2000 /* RB_SURFACE_INFO */) & 0x3FFF;
    const float up = std::ceil(total - 1e-3f);
    const bool fits = up * float(PassHeight()) <= float(SurfaceHeight()) && up * float(pitch) <= float(SurfaceWidth());
    return passScale * (fits ? up : std::floor(total + 1e-3f)) / total;
}

float PassScale()
{
    // (Asked for every scaled coordinate: memoised on what it depends on.)
    struct Memo { uint32_t surface = ~0u, color = ~0u, generation = ~0u, tallInfo = ~0u, tallHeight = ~0u, output = ~0u; float scene = -1; float result = 1; };
    thread_local Memo memo;
    const uint32_t surface = gpureg::Get(0x2000), color = gpureg::Get(0x2001) & 0xFFF, generation = g_nativeMsaaGeneration.load(std::memory_order_relaxed);
    if (surface != memo.surface || color != memo.color || generation != memo.generation || g_tallInfo != memo.tallInfo || g_tallHeight != memo.tallHeight ||
        OutputHeight() != memo.output || g_sceneScale != memo.scene)
        memo = { surface, color, generation, g_tallInfo, g_tallHeight, OutputHeight(), g_sceneScale,
            IntegerTallScale(std::min(float(kWidth) / float(PassWidth()), float(SurfaceRows()) / float(PassHeight()))) };
    return memo.result;
}
float ScaleY() { return float(OutputHeight()) / float(kHeight) * g_sceneScale * PassScale(); }
float ScaleWide() { return float(OutputWidth()) / float(kWidth) * g_sceneScale * PassScale(); }
bool WidePass() { return (gpureg::Get(0x2000 /* RB_SURFACE_INFO */) & 0x3FFF) == kWidth; }
float ScaleX() { return WidePass() ? ScaleWide() : ScaleY(); }
LONG HostX(LONG v) { return LONG(std::lround(double(v) * ScaleX())); }
LONG HostY(LONG v) { return LONG(std::lround(double(v) * ScaleY())); }
D3D12_RECT ToHost(const D3D12_RECT& r)
{
    return { HostX(r.left), HostY(r.top), HostX(r.right), HostY(r.bottom) };
}

// Wider than 16:9: the output is `wide` times as wide as 16:9 for its height. The view
// extends horizontally by `extent` (1 + (wide - 1) x REGPU_WIDE_FOV%): 100 is full Hor+ (the
// most side view, but rectilinear stretch at the edges grows with a wide camera); 0
// keeps the 16:9 horizontal view and zooms in vertically instead.
float OutputWide() { return float(OutputWidth()) * kHeight / (float(OutputHeight()) * kWidth); }
float ViewExtent()
{
    static const float widePercent = [] {
        const char* v = getenv("REGPU_WIDE_FOV");
        return v ? std::clamp(float(atof(v)), 0.0f, 100.0f) : 100.0f;
    }();
    return 1.0f + (std::max(OutputWide(), 1.0f) - 1.0f) * widePercent / 100.0f;
}
constexpr UINT kFrames = 3;
// The per-frame upload ring. Without guest memory mapped for the GPU (drivers without
// ID3D12Device13, e.g. older Windows 10 builds) every vertex buffer is copied into it: the
// menus alone then need well over 64 MB a frame, and draws past the end were dropped (black
// bands and missing letters). REGPU_RING_MB overrides.
UINT64 kRingSize = 64ull << 20;
// A frame that runs out of ring asks for twice as much: each slot rebuilds its ring at that size
// at its next start (its GPU work done), so only that one frame drops draws.
constexpr UINT64 kRingMax = 2048ull << 20;
std::atomic<UINT64> g_ringWant{ 0 };
UINT64 g_ringPeak = 0;
constexpr UINT kDescriptorsPerFrame = 8192; // per frame: ring + guest memory views, then resolve-blit views
constexpr UINT kBindless = 1u << 16;      // permanent texture descriptors (0 null 2D, 1 null cube)
constexpr UINT kBindlessBase = kDescriptorsPerFrame * 3;
// The EDRAM colour target: half floats hold the title's HDR (7e3) range.
constexpr DXGI_FORMAT kColorFormat = DXGI_FORMAT_R16G16B16A16_FLOAT;
constexpr UINT kMaxSurfaces = 64; // EDRAM bases with host surfaces
// Samples per pixel of the scene surfaces (anti-aliasing, Settings::msaa). The blits that
// read them use multisampled shaders when this is above 1.
uint32_t g_msaa = 1;
constexpr uint32_t RB_SURFACE_INFO = 0x2000, RB_COLOR_INFO = 0x2001, RB_DEPTH_INFO = 0x2002;
// Buffers that change (UI quads, particles) are all under 3 KB (REGPU_TRACE_MIDSIZE); REGPU_DIRECT_FETCH_KB
// overrides. (16 KB while fences were written at record time; since they wait for the host GPU, 4 KB.)
constexpr uint32_t kDirectFetchBytes = 4 * 1024;
constexpr uint32_t kEarlyFenceDirectFetchBytes = 16 * 1024; // with RendererEarlyFences

// Registers.
constexpr uint32_t RB_STENCILREFMASK = 0x210D;
constexpr uint32_t RB_COLOR_MASK = 0x2104, RB_ALPHA_REF = 0x210E, RB_DEPTHCONTROL = 0x2200, RB_BLENDCONTROL0 = 0x2201;
constexpr uint32_t RB_COLORCONTROL = 0x2202, PA_CL_VTE_CNTL = 0x2206, RB_MODECONTROL = 0x2208, VGT_INDX_OFFSET = 0x2102;
constexpr uint32_t VGT_MULTI_PRIM_IB_RESET_INDX = 0x2103;
constexpr uint32_t PA_CL_VPORT_XSCALE = 0x210F, PA_CL_VPORT_XOFFSET = 0x2110, PA_CL_VPORT_YSCALE = 0x2111, PA_CL_VPORT_YOFFSET = 0x2112;
constexpr uint32_t PA_CL_VPORT_ZSCALE = 0x2113, PA_CL_VPORT_ZOFFSET = 0x2114;
constexpr uint32_t PA_SC_WINDOW_OFFSET = 0x2080, PA_SC_WINDOW_SCISSOR_TL = 0x2081, PA_SC_WINDOW_SCISSOR_BR = 0x2082;
constexpr uint32_t PA_SU_SC_MODE_CNTL = 0x2205;
constexpr uint32_t RB_COPY_CONTROL = 0x2318, RB_COPY_DEST_BASE = 0x2319, RB_COPY_DEST_PITCH = 0x231A, RB_COPY_DEST_INFO = 0x231B;
constexpr uint32_t SQ_PROGRAM_CNTL = 0x2180, SQ_CONTEXT_MISC = 0x2181, PA_SU_POINT_SIZE = 0x2280, PA_SU_POINT_MINMAX = 0x2281;
constexpr uint32_t RB_DEPTH_CLEAR = 0x231D, RB_COLOR_CLEAR = 0x231E, RB_COLOR_CLEAR_LO = 0x231F;

void LogDebugMessages();

void Check(HRESULT hr, const char* what)
{
    if (FAILED(hr))
    {
        LogDebugMessages();
        Fatal("renderer: %s failed (%08lX)", what, hr);
    }
}

float RegFloat(uint32_t index)
{
    const uint32_t v = gpureg::Get(index);
    float f;
    memcpy(&f, &v, 4);
    return f;
}

struct CompiledShader
{
    enum State { Pending, Ready, Failed };
    std::atomic<int> state{ Pending }; // set by the compile worker
    uint64_t hash = 0, cacheKey = 0;
    bool pixel = false;
    std::vector<uint32_t> ucodeBE; // saved to the shader cache once a pipeline uses it
    ComPtr<ID3DBlob> code;
    TranslatedShader info;
    const CompiledShader* hlslSource = nullptr; // canonical variants: the shader whose code (and HLSL) this reuses
    mutable const CompiledShader* motion = nullptr; // its motion-vector variant, once looked up
    uint32_t tag = 0; // the game's mark for this shader (Host::rewriteShader), passed to Host::onDraw
    bool IsReady() const { return state.load(std::memory_order_acquire) == Ready; }
};

struct CachedTexture
{
    ComPtr<ID3D12Resource> resource;
    uint64_t contentHash = 0;
    uint64_t checkedFrame = ~0ull; // content already validated in this frame
    uint32_t unchanged = 0;        // checks in a row that found the same content
    uint32_t changes = 0;          // times the content changed after the first upload
    bool everLive = false;         // cubes: the title rendered (some of) its faces at least once
    uint64_t lastLiveFrame = 0;    // cubes: the renderer frame of the last put-together with rendered faces
    uint32_t width = 0, height = 0;
    DXGI_FORMAT format = DXGI_FORMAT_R8G8B8A8_UNORM; // cubes: matches their faces
    bool native = false; // block-compressed on the GPU: the shader applies the fetch swizzle
};

// A resolve destination backed by a host texture: fetches of this guest
// address read the copy of the render target instead of guest memory.
struct ResolvedTexture
{
    ComPtr<ID3D12Resource> resource;
    uint32_t width = 0, height = 0;          // game pixels
    uint32_t hostWidth = 0, hostHeight = 0;  // the texture (see ScaleX/ScaleY)
    uint64_t frame = ~0ull;                  // renderer frame of the last resolve into it
    uint64_t serial = 0;                     // stamp of the last resolve into it (g_resolveSerial)
    DXGI_FORMAT format = DXGI_FORMAT_R8G8B8A8_UNORM; // R32_FLOAT for depth resolves
    bool swapped = false; // resolved with the copy swap bit: guest memory has red and blue exchanged
    // Depth resolves: the guest's D24S8 words as RGBA8 (stencil, then depth's low to high bytes),
    // for titles that fetch the depth image with a colour format to read its stencil (see BlitResolve).
    ComPtr<ID3D12Resource> packed;
    uint32_t packedWidth = 0, packedHeight = 0;
    // Guest memory under the image when it was resolved (our resolves don't write it): if the title
    // later puts something else there (a CPU-made texture), the resolve is stale (see ResolvedFor).
    uint64_t memHash = 0, memChecked = 0;
    bool stale = false;
    uint32_t guestBpp = 4; // bytes per pixel of the guest image: the hash covers only its memory
    uint32_t guestFormat = 0; // the copy's destination format (RB_COPY_DEST_INFO bits 7-12)
    // The title resolves differently sized images (bloom chain) to one address within a
    // frame: keep earlier sizes here and swap them in rather than recreating them.
    std::vector<ResolvedTexture> spares;
};

// REGPU_DUMP_RESOLVES readback of one resolved texture.
struct ResolveDump
{
    ComPtr<ID3D12Resource> buffer;
    uint32_t base = 0;
    int variant = 0;
    UINT width = 0, height = 0, pitch = 0;
    DXGI_FORMAT format = DXGI_FORMAT_UNKNOWN;
    float minDepth = 0, maxDepth = 0;
    uint64_t midCount = 0;
};

struct FrameResources
{
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12Resource> ring;
    uint8_t* ringCpu = nullptr;
    UINT64 ringSize = 0; // this slot's ring (grows, see RingAlloc)
    std::atomic<UINT64> ringUsed{ 0 }; // draw workers allocate concurrently
    // Guest ranges already uploaded this frame: (address << 32 | bytes) -> (offset, sample hash).
    std::unordered_map<uint64_t, std::pair<UINT64, uint64_t>> uploads;
    UINT descriptorBase = 0;
    UINT descriptorsUsed = 0;
    UINT64 fence = 0;
    std::vector<ComPtr<ID3D12Resource>> garbage; // released once this frame's work completes
    bool timed = false; // REGPU_PERF: GPU timestamps were written for this frame
    // Extra command lists of this frame (parallel draw pieces, main list continuations).
    std::vector<std::pair<ComPtr<ID3D12CommandAllocator>, ComPtr<ID3D12GraphicsCommandList>>> pool;
    size_t poolUsed = 0;
};

// The pipeline this thread is handing to the driver compiler, named by RendererLogCrashContext if the driver faults.
struct CompilingNow { uint64_t vs, ps; bool active; };
thread_local CompilingNow g_compiling{};

// Pipelines whose compile crashed the driver (FM4 port: AMD's amdxc64 faults on a few): written by
// RendererLogCrashContext (the port's crash handler, through regpu::LogCrashContext) to regpu-bad-pipelines.txt
// and refused on the next start, so a draw using one is dropped instead of killing the game.
const char* const kBadPipelinesFile = "regpu-bad-pipelines.txt";
bool PipelineIsPoisoned(uint64_t vs, uint64_t ps)
{
    static const std::set<std::pair<uint64_t, uint64_t>> bad = [] {
        std::set<std::pair<uint64_t, uint64_t>> out;
        if (FILE* f = fopen(kBadPipelinesFile, "r"))
        {
            unsigned long long v, p;
            while (fscanf(f, "%llx %llx", &v, &p) == 2)
                out.insert({ v, p });
            fclose(f);
        }
        if (!out.empty())
            Log("renderer: %zu pipelines refused (they crashed the driver's compiler before: %s)", out.size(), kBadPipelinesFile);
        return out;
    }();
    return bad.count({ vs, ps }) != 0;
}

struct PipelineKey
{
    uint64_t vs, ps;
    uint32_t blend, depth, stencilMaskMode, flags;
    uint32_t biasScale = 0, biasOffset = 0; // polygon offset (float bits), 0 when off
    bool operator==(const PipelineKey&) const = default;
};
// A polygon offset (float bits) rounded to 4 mantissa bits (within 3%): pipelines keyed by it. PGR4
// moves one pass's offset a little every frame (1.5e-4 to 2.3e-4), and each value was a new pipeline,
// built while the frame waited: 22,000 of the FX's 23,000 cached pipelines were that one shader pair.
// REGPU_EXACT_DEPTH_BIAS=1: exact, as before.
uint32_t RoundedBias(uint32_t bits)
{
    static const bool exact = getenv("REGPU_EXACT_DEPTH_BIAS") != nullptr;
    return exact || (bits & 0x7F800000) == 0x7F800000 ? bits : (bits + (1u << 18)) & ~((1u << 19) - 1);
}

// The polygon offset's constant part as the console applies it: an absolute amount added to depth
// (Xenia's shader-depth route, halved for D24FS8 as Xenia does), from the vertex shader. D3D12's depth
// bias on a float depth buffer is relative instead (scaled by 2^(exponent of the primitive's depth -
// 23)): at the small reverse-Z depths of a car a few metres away it came out ~64 times too small, and
// PGR4's headlight flare occlusion quads (offset toward the camera, GEQUAL against the restored scene
// depth) never passed, so the flare stayed off and flashed on only when a query had no result yet.
// 0 without polygon offset, or with REGPU_NO_POLY_OFFSET / REGPU_FIXED_POLY_OFFSET (A/B).
float PolygonOffsetDepth()
{
    static LiveSwitch noPolyOffset("REGPU_NO_POLY_OFFSET");
    static LiveSwitch fixedOffset("REGPU_FIXED_POLY_OFFSET");
    const uint32_t sc = gpureg::Get(PA_SU_SC_MODE_CNTL);
    if (noPolyOffset || fixedOffset || !((gpureg::Get(RB_DEPTHCONTROL) >> 1) & 1) || !((sc >> 11) & 3))
        return 0.0f;
    float offset = RegFloat((sc >> 11) & 1 ? 0x2381 : 0x2383);
    if ((gpureg::Get(RB_DEPTH_INFO) >> 16) & 1) // D24FS8
        offset *= 0.5f;
    return offset;
}

struct PipelineKeyHash
{
    size_t operator()(const PipelineKey& k) const
    {
        uint64_t h = k.vs * 0x9E3779B97F4A7C15ull ^ k.ps;
        h ^= (uint64_t(k.blend) << 32 | k.depth) * 0xC2B2AE3D27D4EB4Full;
        h ^= (uint64_t(k.stencilMaskMode) << 32 | k.flags) * 0x165667B19E3779F9ull;
        h ^= (uint64_t(k.biasScale) << 32 | k.biasOffset) * 0x9E3779B97F4A7C15ull;
        return size_t(h ^ (h >> 29));
    }
};

struct BoundTexture
{
    ID3D12Resource* texture;
    DXGI_FORMAT format;
    bool cube;
};

// A pipeline state being (or already) built. The driver compiles the translated
// shaders to GPU code inside CreateGraphicsPipelineState, which takes 40-700 ms for
// the larger ones: that is done on worker threads and draws wait until it is ready.
struct PipelineEntry
{
    ComPtr<ID3D12PipelineState> pso;
    std::atomic<int> state{ 0 }; // 0 building, 1 ready, 2 failed
    // The build, run once by whoever claims it first: a pool thread, or a draw that must wait for it
    // (WaitForPipeline), so that draw doesn't wait behind every build queued before it.
    std::function<void()> build;
    std::atomic<bool> claimed{ false };
    void Build()
    {
        if (!claimed.exchange(true, std::memory_order_acq_rel))
            build();
    }
};

struct Renderer
{
    ID3D12Device* device = nullptr;
    ID3D12CommandQueue* queue = nullptr;
    ComPtr<ID3D12GraphicsCommandList> realList;
    RecordedList recorder; // the main thread's command list (see recorded_list.h)
    ComPtr<ID3D12Heap> physicalHeap;     // the guest's physical memory section, mapped for the GPU
    ComPtr<ID3D12Resource> physical;     // 512 MB raw buffer over it (null if unsupported)
    ComPtr<ID3D12Fence> fence;
    HANDLE fenceEvent = nullptr;
    UINT64 nextFence = 1;
    FrameResources frames[kFrames];
    UINT frameIndex = 0;
    bool listOpen = false;

    ComPtr<ID3D12RootSignature> rootSignature;
    ComPtr<ID3D12DescriptorHeap> srvHeap;     // shader visible
    ComPtr<ID3D12DescriptorHeap> samplerHeap; // REGPU_LEAN_FETCH: s0-s7 again as a table (space1, root parameter 4)
    ComPtr<ID3D12DescriptorHeap> stagingHeap; // CPU: null texture SRV
    UINT srvStride = 0;
    ComPtr<ID3D12DescriptorHeap> rtvHeap, dsvHeap;
    ComPtr<ID3D12DescriptorHeap> blitRtvHeap;             // one RTV, rewritten per depth resolve
    ComPtr<ID3D12RootSignature> blitRootSignature;
    ComPtr<ID3D12RootSignature> motionRootSignature; // root signature + b2 (motion-vector pipelines)
    ComPtr<ID3D12PipelineState> depthBlit;                // depth buffer -> R32_FLOAT
    ComPtr<ID3D12PipelineState> depthPackBlit;            // depth and stencil -> the guest's D24S8 words as RGBA8
    ComPtr<ID3D12PipelineState> colorBlit8, colorBlit16;  // colour -> 8888 / RGBA16F, scaled
    ComPtr<ID3D12PipelineState> depthBlitMs, colorBlitMs8, colorBlitMs16; // the same from multisampled surfaces
    // EDRAM surfaces: one host colour and one host depth target per EDRAM base
    // (RB_COLOR_INFO / RB_DEPTH_INFO bits 0-11), so passes that the title
    // places at different EDRAM offsets do not overwrite each other.
    struct Surface
    {
        ComPtr<ID3D12Resource> resource;
        D3D12_RESOURCE_STATES state = D3D12_RESOURCE_STATE_RENDER_TARGET;
        D3D12_CPU_DESCRIPTOR_HANDLE view{};
    };
    std::unordered_map<uint32_t, Surface> colorSurfaces, depthSurfaces;
    ComPtr<ID3D12Resource> readback;

    std::unordered_map<uint64_t, std::unique_ptr<CompiledShader>> shaders;
    std::unordered_map<PipelineKey, std::shared_ptr<PipelineEntry>, PipelineKeyHash> pipelines;
    // Keyed by address, format, width and height (TextureKey): looked up for every bound
    // texture of every draw.
    std::unordered_map<uint64_t, CachedTexture> textures;
    std::unordered_map<uint64_t, CachedTexture> cubes;
    std::unordered_map<uint64_t, CachedTexture> volumes; // 3D textures
    std::unordered_map<uint64_t, CachedTexture> mipChains; // resolved textures with their mips, by base and shape
    // Bindless texture descriptors.
    struct BindlessEntry
    {
        UINT index;
        ComPtr<ID3D12Resource> resource; // kept alive while the descriptor may be used
        uint64_t lastFrame;
    };
    std::unordered_map<uint64_t, BindlessEntry> bindless; // by resource pointer, format, cube
    std::vector<UINT> bindlessFree;
    UINT bindlessNext = 3; // 0-2: the null 2D, cube and 3D views
    std::unordered_map<uint32_t, ResolvedTexture> resolved; // by physical address

    uint64_t frame = 0;
    std::atomic<uint32_t> drawsThisFrame{ 0 }, skippedThisFrame{ 0 };
    bool ok = false;
};

Renderer g_r;
std::atomic<uint32_t> g_lastFrameDraws{ 0 }; // the debug server's status

// REGPU_LEAN_FETCH's sampler table (Init): the eight samplers in banks. A change of texture filtering
// (regpu::SetAnisotropy) is written into the next bank and the draws switch to it, so frames still in
// flight keep reading the one they were recorded with.
constexpr UINT kSamplerBanks = 4;
D3D12_SAMPLER_DESC g_samplerBase[8]{};
UINT g_samplerStride = 0;
std::atomic<UINT> g_samplerBank{ 0 };
std::atomic<int> g_anisoRequest{ -1 }, g_anisoApplied{ -1 };
std::mutex g_samplerMutex;

void WriteSamplerBank(UINT bank, int aniso)
{
    for (UINT i = 0; i < 8; i++)
    {
        D3D12_SAMPLER_DESC s = g_samplerBase[i];
        // The wrapping linear samplers (s0 and the anisotropic s4-s7): surface textures. The clamped
        // and point ones (shadow maps, render targets, lookups) stay as they are.
        const bool surface = i == 0 || i >= 4;
        if (surface && aniso == 0)
        {
            s.Filter = D3D12_FILTER_MIN_MAG_MIP_LINEAR;
            s.MaxAnisotropy = 0;
        }
        else if (surface && aniso > 0)
        {
            s.Filter = aniso > 1 ? D3D12_FILTER_ANISOTROPIC : D3D12_FILTER_MIN_MAG_MIP_LINEAR;
            s.MaxAnisotropy = UINT(std::clamp(aniso, 1, 16));
        }
        D3D12_CPU_DESCRIPTOR_HANDLE h = g_r.samplerHeap->GetCPUDescriptorHandleForHeapStart();
        h.ptr += size_t(bank * 8 + i) * g_samplerStride;
        g_r.device->CreateSampler(&s, h);
    }
}

// The sampler table for the next draw, after a filtering change if one is pending (draw thread).
D3D12_GPU_DESCRIPTOR_HANDLE SamplerTable()
{
    if (g_anisoRequest.load(std::memory_order_relaxed) != g_anisoApplied.load(std::memory_order_acquire))
    {
        std::lock_guard lock(g_samplerMutex); // (draw workers may get here together)
        if (const int want = g_anisoRequest.load(); want != g_anisoApplied.load())
        {
            const UINT bank = (g_samplerBank.load() + 1) % kSamplerBanks;
            WriteSamplerBank(bank, want);
            g_samplerBank.store(bank);
            g_anisoApplied.store(want, std::memory_order_release);
            Log("renderer: texture filtering %d (sampler bank %u)", want, bank);
        }
    }
    D3D12_GPU_DESCRIPTOR_HANDLE h = g_r.samplerHeap->GetGPUDescriptorHandleForHeapStart();
    h.ptr += uint64_t(g_samplerBank.load()) * 8 * g_samplerStride;
    return h;
}

std::atomic<uint64_t> g_outputRequest{ 0 }; // regpu::SetOutputSize: width << 32 | height, 0 none (ApplyOutputSize)
}

void regpu::SetAnisotropy(int maxAnisotropy)
{
    g_anisoRequest.store(maxAnisotropy, std::memory_order_relaxed);
}

void regpu::SetOutputSize(uint32_t width, uint32_t height)
{
    if (width && height)
        g_outputRequest.store(uint64_t(width) << 32 | height, std::memory_order_relaxed);
}
uint32_t RendererLastFrameDraws() { return g_lastFrameDraws.load(std::memory_order_relaxed); }
namespace
{

// Shared caches used by draws on several threads (see DrawContext).
std::recursive_mutex g_shaderMutex;  // shaders, pipelines, the compiler's maps
std::recursive_mutex g_textureMutex; // textures, cubes, mip chains, bindless, texture memory, index arena
uint32_t g_zeroedEdramBase = ~0u; // EDRAM base a depth clear to zero has just emptied (BindSurfaces)
std::mutex g_surfaceMutex;           // EDRAM surfaces
// Parallel segments: texture uploads and copies a worker makes go to this list, executed
// before the segment's draws (another worker's earlier draws may use the same texture).
RecordedList* g_prepList = nullptr;

// What a thread recording draws keeps for itself: its command list, the bound surfaces and
// shaders. The command processor thread uses the main context (the frame's list); draw
// workers each have their own (see the parallel draw segments in gpu.cpp).
struct DrawContext
{
    RecordedList* list = nullptr;
    uint32_t colorBase = ~0u, depthBase = ~0u;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv{}, dsv{};
    // The bound surfaces.
    ComPtr<ID3D12Resource> color, depth;
    D3D12_RESOURCE_STATES colorState = D3D12_RESOURCE_STATE_RENDER_TARGET;
    // Active shaders: the last microcode loaded per stage.
    std::vector<uint32_t> vsCode, psCode;
    uint64_t vsHash = 0, psHash = 0;
    bool worker = false; // a draw worker: frame-level work (begin, resolves, swaps) is not its job
    uint32_t draws = 0;  // a worker's draws, added to drawsThisFrame once at the end of its piece
};
DrawContext g_mainContext;
thread_local DrawContext* t_context = nullptr;
inline DrawContext& C() { return t_context ? *t_context : g_mainContext; }

// Holds the texture lock and, on a draw worker, records into the segment's prep list.
struct TextureScope
{
    std::lock_guard<std::recursive_mutex> lock{ g_textureMutex };
    RecordedList* saved = nullptr;
    TextureScope()
    {
        if (C().worker && g_prepList)
        {
            saved = C().list;
            C().list = g_prepList;
        }
    }
    ~TextureScope()
    {
        if (saved)
            C().list = saved;
    }
};

// With REGPU_DRED: where the GPU was when the device went away. Each command list still in flight
// logs its breadcrumb count and the last operation it completed, with the operations around it; a
// page fault logs its address and the allocations at or recently freed from it. From the FM4 port.
void LogDeviceRemovedData()
{
    ComPtr<ID3D12DeviceRemovedExtendedData> dred;
    if (!g_r.device || FAILED(g_r.device->QueryInterface(IID_PPV_ARGS(&dred))))
        return;
    static const char* kOps[] = { "SetMarker", "BeginEvent", "EndEvent", "DrawInstanced", "DrawIndexedInstanced", "ExecuteIndirect",
        "Dispatch", "CopyBufferRegion", "CopyTextureRegion", "CopyResource", "CopyTiles", "ResolveSubresource", "ClearRenderTargetView",
        "ClearUnorderedAccessView", "ClearDepthStencilView", "ResourceBarrier", "ExecuteBundle", "Present", "ResolveQueryData",
        "BeginSubmission", "EndSubmission" };
    const auto op = [&](D3D12_AUTO_BREADCRUMB_OP o) { return UINT(o) < std::size(kOps) ? kOps[o] : "other"; };
    D3D12_DRED_AUTO_BREADCRUMBS_OUTPUT crumbs{};
    if (SUCCEEDED(dred->GetAutoBreadcrumbsOutput(&crumbs)))
    {
        int listed = 0;
        for (const D3D12_AUTO_BREADCRUMB_NODE* n = crumbs.pHeadAutoBreadcrumbNode; n && listed < 16; n = n->pNext)
        {
            const UINT done = n->pLastBreadcrumbValue ? *n->pLastBreadcrumbValue : 0;
            if (done >= n->BreadcrumbCount)
                continue; // finished
            listed++;
            Log("renderer: dred: list %p (%s) on queue %p: %u of %u operations done", static_cast<const void*>(n->pCommandList),
                n->pCommandListDebugNameA ? n->pCommandListDebugNameA : "unnamed", static_cast<const void*>(n->pCommandQueue), done, n->BreadcrumbCount);
            const UINT from = done > 4 ? done - 4 : 0, to = std::min(n->BreadcrumbCount, done + 4);
            for (UINT i = from; i < to; i++)
                Log("renderer: dred:   %s%u %s", i == done ? "> " : "  ", i, op(n->pCommandHistory[i]));
        }
        if (!listed)
            Log("renderer: dred: no command list was in flight");
    }
    D3D12_DRED_PAGE_FAULT_OUTPUT fault{};
    if (SUCCEEDED(dred->GetPageFaultAllocationOutput(&fault)) && fault.PageFaultVA)
    {
        Log("renderer: dred: page fault at GPU VA %016llX", (unsigned long long)fault.PageFaultVA);
        for (const D3D12_DRED_ALLOCATION_NODE* a = fault.pHeadExistingAllocationNode; a; a = a->pNext)
            Log("renderer: dred:   existing allocation %s (type %d)", a->ObjectNameA ? a->ObjectNameA : "unnamed", int(a->AllocationType));
        for (const D3D12_DRED_ALLOCATION_NODE* a = fault.pHeadRecentFreedAllocationNode; a; a = a->pNext)
            Log("renderer: dred:   recently freed %s (type %d)", a->ObjectNameA ? a->ObjectNameA : "unnamed", int(a->AllocationType));
    }
    else
        Log("renderer: dred: no page fault recorded");
}

void LogDebugMessages()
{
    if (g_r.device)
        Log("renderer: device removed reason %08lX", g_r.device->GetDeviceRemovedReason());
    if (getenv("REGPU_DRED"))
        LogDeviceRemovedData();
    ComPtr<ID3D12InfoQueue> queue;
    if (!g_r.device || FAILED(g_r.device->QueryInterface(IID_PPV_ARGS(&queue))))
        return;
    // The first messages name the cause; the rest are often its echo (calls on a list that failed to close).
    const UINT64 n = queue->GetNumStoredMessages();
    for (UINT64 i = 0; i < n; i++)
    {
        if (i == 15 && n > 20)
            i = n - 5;
        SIZE_T size = 0;
        queue->GetMessage(i, nullptr, &size);
        std::vector<char> buffer(size);
        auto* message = reinterpret_cast<D3D12_MESSAGE*>(buffer.data());
        queue->GetMessage(i, message, &size);
        Log("d3d12: %s", message->pDescription);
    }
    queue->ClearStoredMessages();
}
double g_pipelineMs = 0; // REGPU_PERF: time spent creating pipeline states this second
uint32_t g_pipelinesCreated = 0;
uint32_t g_skippedPipeline = 0;
const char* g_waitWhat = "?";
// The current draw samples the full-screen image the title resolved this frame (the
// post-processing composite): its flat geometry is a screen layer, not HUD (see vwide.w).
thread_local bool g_drawSamplesResolve = false;
// REGPU_PERF: GPU time per frame from timestamps at the start and end of each frame's list.
ComPtr<ID3D12QueryHeap> g_timestamps;

// REGPU_GPU_PASSES=1: GPU time per pass (between render target switches and resolves),
// summed by pass and logged every 2 s, to find where GPU time goes.
// REGPU_GPU_SHADERS=1 also marks every draw, labelled by its shaders (approximate: the GPU overlaps draws).
const bool g_gpuShaders = getenv("REGPU_GPU_SHADERS") != nullptr;
const bool g_gpuPasses = getenv("REGPU_GPU_PASSES") != nullptr || g_gpuShaders;
constexpr UINT kPassMarks = 4096;
ComPtr<ID3D12QueryHeap> g_passQueries;
ComPtr<ID3D12Resource> g_passReadback;
struct PassMarkInfo
{
    char label[48];
    uint32_t draws; // draws in the pass this mark starts (filled at the next mark)
};
std::vector<PassMarkInfo> g_passMarks[3];
uint32_t g_passDraws = 0;

// Occlusion queries (EVENT_WRITE_ZPD: the sun flare's visibility). Each guest query becomes a
// D3D12 one; the guest gets the result of the same query (by order in the frame) from the
// last frame that has completed, so the command processor never waits for the GPU.
constexpr UINT kOcclusionPerFrame = 512; // D3D queries per frame slot (a guest query is one or more)
ComPtr<ID3D12QueryHeap> g_occlusionHeap;
ComPtr<ID3D12Resource> g_occlusionReadback;
// A guest query's D3D queries: its depth-tested samples (main) and, for each draw in it, the same draw
// with the depth test always passing (coverage: the console's Total).
struct OcclusionParts
{
    UINT order = 0;
    std::vector<UINT> main, coverage;
    // Which query it is: the guest memory its counts go to (RB_SAMPLE_COUNT_ADDR at its end: the game's
    // query object), and its place among this frame's queries there. A guest query gets the result of the
    // last finished one with the same address, not the same place in the frame: PGR4 queries its light
    // flares in alternating sets, and results come from whichever frame finished last (1-3 back), so by
    // order a headlight's query was answered with another lamp's result (the flare flickered at random).
    uint64_t kind = 0;
    UINT kindOrder = 0;
};
std::vector<OcclusionParts> g_occlusionPending[3];  // per frame slot
std::vector<int64_t> g_occlusionResult;              // by order in the frame: host samples, -1 none yet
std::vector<int64_t> g_occlusionCoverage;            // by order in the frame: host samples covered, -1 none
std::map<std::pair<uint64_t, UINT>, std::pair<int64_t, int64_t>> g_occlusionByKind; // address, place: passed, covered (-1 none)
std::unordered_map<uint64_t, UINT> g_occlusionKindCount; // this frame's queries so far, by address
OcclusionParts g_occlusionCurrent;
uint64_t g_occlusionFrame = ~0ull;
UINT g_occlusionCount = 0, g_occlusionIndex = 0, g_occlusionOrder = 0, g_occlusionOrders = 0; // D3D queries used, open one, guest order, guest queries
bool g_occlusionOpen = false;
std::map<std::string, std::pair<double, uint64_t>> g_passTotals; // ms, draws
uint64_t g_passFrames = 0;
ComPtr<ID3D12Resource> g_timestampReadback;
double g_gpuFrameMs = 0, g_gpuFrameMaxMs = 0;
uint32_t g_gpuFrames = 0;
uint32_t g_waitBlend = 0;
std::map<std::string, uint64_t> g_created; // REGPU_PERF: committed resources created, by kind
// REGPU_PERF: what happened during the current frame, for the slow-frame log.
struct FrameEvents
{
    double waitMs = 0;          // CP thread waiting on shader/pipeline builds
    uint32_t waits = 0, pipelines = 0, created = 0;
    double section[8] = {};     // draw path sections (see Section), ms
    double worstDrawMs = 0;     // the single slowest draw
} g_frameEvents;
void CountCreate(const char* what)
{
    g_created[what]++;
    g_frameEvents.created++;
}

const bool g_dumpHlsl = getenv("REGPU_DUMP_HLSL") != nullptr;

// Texture memory: textures are placed in large heaps instead of each getting a committed
// resource (its own heap). Creating a committed resource costs the driver milliseconds,
// which showed as hitches whenever new textures streamed in on the road ahead. Blocks are
// 64 KB multiples, reused by size once the GPU is done with the texture that held them.
struct TexturePool
{
    static constexpr uint64_t kHeapBytes = 128ull << 20;
    struct Block
    {
        ID3D12Heap* heap = nullptr;
        uint64_t offset = 0, size = 0;
    };
    std::vector<ComPtr<ID3D12Heap>> heaps;
    uint64_t used = kHeapBytes; // in heaps.back()
    std::unordered_map<uint64_t, std::vector<Block>> free; // by size
    std::unordered_map<ID3D12Resource*, Block> live;
    std::deque<std::pair<uint64_t, Block>> retired; // frame retired, block
} g_texturePool;

// A texture in the pool (or committed, if it does not fit a heap). Starts in `state`.
ComPtr<ID3D12Resource> CreateTexture(const D3D12_RESOURCE_DESC& desc, D3D12_RESOURCE_STATES state, const char* kind)
{
    static LiveSwitch committed("REGPU_COMMITTED_TEXTURES"); // A/B
    TexturePool& pool = g_texturePool;
    const D3D12_RESOURCE_ALLOCATION_INFO info = g_r.device->GetResourceAllocationInfo(0, 1, &desc);
    const uint64_t size = (info.SizeInBytes + 0xFFFF) & ~0xFFFFull;
    ComPtr<ID3D12Resource> r;
    if (!committed && size <= TexturePool::kHeapBytes && info.Alignment <= 0x10000)
    {
        TexturePool::Block block;
        auto& list = pool.free[size];
        if (!list.empty())
        {
            block = list.back();
            list.pop_back();
        }
        else
        {
            if (pool.used + size > TexturePool::kHeapBytes)
            {
                D3D12_HEAP_DESC hd{};
                hd.SizeInBytes = TexturePool::kHeapBytes;
                hd.Properties.Type = D3D12_HEAP_TYPE_DEFAULT;
                hd.Alignment = 0x10000;
                hd.Flags = D3D12_HEAP_FLAG_ALLOW_ONLY_NON_RT_DS_TEXTURES;
                ComPtr<ID3D12Heap> heap;
                Check(g_r.device->CreateHeap(&hd, IID_PPV_ARGS(&heap)), "CreateHeap(textures)");
                CountCreate("texture heap");
                pool.heaps.push_back(heap);
                pool.used = 0;
            }
            block = { pool.heaps.back().Get(), pool.used, size };
            pool.used += size;
        }
        if (SUCCEEDED(g_r.device->CreatePlacedResource(block.heap, block.offset, &desc, state, nullptr, IID_PPV_ARGS(&r))))
        {
            pool.live[r.Get()] = block;
            return r;
        }
        pool.free[size].push_back(block);
    }
    D3D12_HEAP_PROPERTIES hp{};
    hp.Type = D3D12_HEAP_TYPE_DEFAULT;
    Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &desc, state, nullptr,
              IID_PPV_ARGS(&r)),
        kind);
    CountCreate(kind);
    return r;
}

FrameResources& Frame();

// Hands a texture to the frame's garbage; its pool block is reused once no frame in flight
// can still read it.
void RetireTexture(ComPtr<ID3D12Resource>&& r)
{
    if (!r)
        return;
    if (auto it = g_texturePool.live.find(r.Get()); it != g_texturePool.live.end())
    {
        g_texturePool.retired.push_back({ g_r.frame, it->second });
        g_texturePool.live.erase(it);
    }
    Frame().garbage.push_back(std::move(r));
}

void ReclaimTextureBlocks()
{
    auto& retired = g_texturePool.retired;
    while (!retired.empty() && retired.front().first + kFrames + 2 < g_r.frame)
    {
        g_texturePool.free[retired.front().second.size].push_back(retired.front().second);
        retired.pop_front();
    }
}

// Bumped by every resolve: the per-slot texture bindings below are only reused while no
// resolve has changed what an address maps to.
uint64_t g_resolveEpoch = 0;
uint64_t g_resolveSerial = 0; // counts resolves: what a mip chain was assembled from

// The constants a texture fetch slot produced for the previous draw: draws usually bind
// the same fetch constants as their neighbours, and a repeat within the frame (same fetch,
// no resolve since) gives the same binding.
struct SlotBinding
{
    uint32_t fetch[6] = {};
    uint64_t frame = ~0ull, epoch = ~0ull;
    uint32_t tinfo[4] = {};
    float tbias[4] = {};
    bool samplesResolve = false;
};
// A cache valid for one frame: an open-addressed table whose slots carry the frame they were filled
// in; a slot of another frame is empty, so a new frame starts with an empty table without clearing
// it. (A std::unordered_map, a node and a cache miss per lookup, was ~3% of the command processor
// across the index, upload, texture and bindless caches.) Grows when a frame fills half of it.
template <class V>
struct FrameMap
{
    struct Slot
    {
        uint64_t key = 0;
        uint64_t frame = ~0ull;
        V value{};
    };
    std::vector<Slot> slots = std::vector<Slot>(4096);
    size_t used = 0;          // slots filled this frame
    uint64_t usedFrame = ~0ull;
    static size_t Hash(uint64_t key)
    {
        key ^= key >> 31;
        key *= 0x9E3779B97F4A7C15ull;
        return size_t(key ^ (key >> 29));
    }
    V* Find(uint64_t key, uint64_t frame)
    {
        const size_t mask = slots.size() - 1;
        for (size_t i = Hash(key) & mask;; i = (i + 1) & mask)
        {
            Slot& s = slots[i];
            if (s.frame != frame)
                return nullptr;
            if (s.key == key)
                return &s.value;
        }
    }
    void Put(uint64_t key, uint64_t frame, const V& value)
    {
        if (usedFrame != frame)
        {
            usedFrame = frame;
            used = 0;
        }
        if ((used + 1) * 2 > slots.size())
            Grow(frame);
        const size_t mask = slots.size() - 1;
        for (size_t i = Hash(key) & mask;; i = (i + 1) & mask)
        {
            Slot& s = slots[i];
            if (s.frame != frame)
            {
                s = { key, frame, value };
                used++;
                return;
            }
            if (s.key == key)
            {
                s.value = value;
                return;
            }
        }
    }
    void Grow(uint64_t frame)
    {
        std::vector<Slot> old(slots.size() * 2);
        old.swap(slots);
        used = 0;
        const size_t mask = slots.size() - 1;
        for (const Slot& o : old)
            if (o.frame == frame)
                for (size_t i = Hash(o.key) & mask;; i = (i + 1) & mask)
                    if (slots[i].frame != frame)
                    {
                        slots[i] = o;
                        used++;
                        break;
                    }
    }
};

// A FrameMap the draw workers share (REGPU_DRAW_WORKERS), behind each thread's own: what one thread
// looked up this frame, the others find under a read lock instead of the texture mutex. (Each worker's
// own cache starts the frame empty, so without it every worker took the texture mutex for every
// texture, index buffer and bindless slot once a frame: over half of a worker's busy time in PGR4.)
template <class V>
struct SharedFrameMap
{
    FrameMap<V> map;
    std::shared_mutex mutex;
    bool Find(uint64_t key, uint64_t frame, V& out)
    {
        std::shared_lock lock(mutex);
        if (const V* v = map.Find(key, frame))
        {
            out = *v;
            return true;
        }
        return false;
    }
    void Put(uint64_t key, uint64_t frame, const V& value)
    {
        std::unique_lock lock(mutex);
        map.Put(key, frame, value);
    }
};

// Bindings by fetch constant: a texture bound by many draws (in any slot) is looked up once
// per frame and resolve epoch (textures are still checked once per frame for changes).
// (A direct-mapped table: an entry holds the fetch words it was made for, so a collision only
// recomputes. The hash map it replaced was 2-3% of the command processor.)
constexpr uint32_t kBindingSlots = 8192;
thread_local std::unique_ptr<SlotBinding[]> g_bindings;
thread_local uint64_t g_bindingsFrame = ~0ull, g_bindingsEpoch = ~0ull;

// The frame to trace (draws and resolves logged, the image and resolves written out):
// REGPU_TRACE_DRAWS=<frame>, or the one after F12 is pressed in the game window.
std::atomic<uint64_t> g_hotkeyFrame{ 0 };
// (Read once at startup: a function-local static checks its initialisation guard on every call, and
// RendererTracing asks several times a draw.)
const uint64_t g_traceDrawsEnv = getenv("REGPU_TRACE_DRAWS") ? strtoull(getenv("REGPU_TRACE_DRAWS"), nullptr, 10) : 0;
uint64_t TraceFrame()
{
    const uint64_t hot = g_hotkeyFrame.load(std::memory_order_relaxed);
    return hot ? hot : g_traceDrawsEnv;
}

// Why GetTexture last found nothing to bind (the draw then samples the white fallback): shown in
// the F12 capture (out/capture/textures.txt).
thread_local char g_textureFailure[96] = "";
void NoteTextureFailure(const char* why, const TextureFetch& fetch)
{
    snprintf(g_textureFailure, sizeof(g_textureFailure), "%s (type %u dim %u format %u)", why, fetch.Type(), fetch.Dimension(), fetch.Format());
}

// Per-draw timing (several clock reads per draw) only for REGPU_PERF.
const bool g_perfTiming = getenv("REGPU_PERF") != nullptr;

// The last draw state set on the command list: draws skip setting what has not changed
// (each D3D12 call costs driver time, and most draws share their neighbours' state).
// Invalidated wherever the list is reset or the root signature changes.
struct DrawState
{
    D3D12_VIEWPORT viewport{ -1, -1, -1, -1, -1, -1 };
    D3D12_RECT scissor{ -1, -1, -1, -1 };
    uint32_t stencilRef = ~0u;
    float blendFactor[4] = { -1, -1, -1, -1 };
    ID3D12PipelineState* pso = nullptr;
    D3D12_GPU_DESCRIPTOR_HANDLE table2{}, table3{}, samplers{};
    D3D_PRIMITIVE_TOPOLOGY topology = D3D_PRIMITIVE_TOPOLOGY_UNDEFINED;
    bool motionTargets = false; // the motion target is bound as the second render target
    bool motionRoot = false;    // the motion root signature is set
    D3D12_INDEX_BUFFER_VIEW indexBuffer{};
    void Invalidate() { *this = DrawState{}; }
};
thread_local DrawState g_drawState;

// Texture cache key: address (4 KB aligned), format (6 bits), width and height (13 bits each).
uint64_t TextureKey(uint32_t address, uint32_t format, uint32_t w, uint32_t h)
{
    return uint64_t(address >> 12) << 32 | uint64_t(format & 0x3F) << 26 | uint64_t((w - 1) & 0x1FFF) << 13 | ((h - 1) & 0x1FFF);
}

uint64_t Fnv(const void* data, size_t bytes, uint64_t h = 0xCBF29CE484222325ull)
{
    const auto* p = static_cast<const uint8_t*>(data);
    for (size_t i = 0; i < bytes; i++)
        h = (h ^ p[i]) * 0x100000001B3ull;
    return h;
}

// FullHash with AVX2: 64 bytes a step in two 256-bit accumulators (each 32-bit half of the data, mixed
// with a key, multiplied into the other half: XXH3's accumulate). FH1 hashes ~16 MB of vertex and index
// data a frame (buffers reused within the frame are checked again before each draw); the scalar lanes
// took ~1 ms of it.
__attribute__((target("avx2"))) uint64_t FullHashAvx2(const uint8_t* data, size_t bytes)
{
    const __m256i k0 = _mm256_set_epi64x(0x1CAD21F72C81017Cull, 0xBE4BA423396CFEB8ull, 0x7C01812CF721AD1Cull, 0xDB979083E96DD4DEull);
    const __m256i k1 = _mm256_set_epi64x(0x1F67B3B7A4A44072ull, 0x78E5C0CC4EE679CBull, 0xD8ACDEA946EF1938ull, 0x3F349CE33F76FAA8ull);
    __m256i a = _mm256_set1_epi64x(int64_t(0x9E3779B97F4A7C15ull ^ bytes)), b = _mm256_set1_epi64x(int64_t(0xC2B2AE3D27D4EB4Full));
    size_t i = 0;
    for (; i + 64 <= bytes; i += 64)
    {
        const __m256i d0 = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(data + i));
        const __m256i d1 = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(data + i + 32));
        const __m256i x0 = _mm256_xor_si256(d0, k0), x1 = _mm256_xor_si256(d1, k1);
        a = _mm256_add_epi64(a, _mm256_add_epi64(_mm256_mul_epu32(x0, _mm256_srli_epi64(x0, 32)), _mm256_shuffle_epi32(d0, 0x4E)));
        b = _mm256_add_epi64(b, _mm256_add_epi64(_mm256_mul_epu32(x1, _mm256_srli_epi64(x1, 32)), _mm256_shuffle_epi32(d1, 0x4E)));
    }
    alignas(32) uint64_t lanes[8];
    _mm256_store_si256(reinterpret_cast<__m256i*>(lanes), a);
    _mm256_store_si256(reinterpret_cast<__m256i*>(lanes + 4), b);
    uint64_t h = 0xCBF29CE484222325ull;
    for (uint64_t v : lanes)
        h = (h ^ v) * 0xFF51AFD7ED558CCDull, h ^= h >> 29;
    for (; i < bytes; i++)
        h = (h ^ data[i]) * 0x100000001B3ull;
    h = (h ^ (h >> 33)) * 0xC4CEB9FE1A85EC53ull;
    return h ^ (h >> 33);
}

// FullHashAvx2's accumulate in 128-bit halves, for CPUs without AVX2 (FX-8350: its 64-bit multiplies
// are slow, the scalar lanes below were ~13% of the draw thread).
uint64_t FullHashSse2(const uint8_t* data, size_t bytes)
{
    const __m128i k0 = _mm_set_epi64x(0x7C01812CF721AD1Cull, 0xDB979083E96DD4DEull), k1 = _mm_set_epi64x(0x1CAD21F72C81017Cull, 0xBE4BA423396CFEB8ull);
    const __m128i k2 = _mm_set_epi64x(0xD8ACDEA946EF1938ull, 0x3F349CE33F76FAA8ull), k3 = _mm_set_epi64x(0x1F67B3B7A4A44072ull, 0x78E5C0CC4EE679CBull);
    __m128i a0 = _mm_set1_epi64x(int64_t(0x9E3779B97F4A7C15ull ^ bytes)), a1 = a0;
    __m128i b0 = _mm_set1_epi64x(int64_t(0xC2B2AE3D27D4EB4Full)), b1 = b0;
    const auto acc = [](__m128i& s, __m128i d, __m128i k) {
        const __m128i x = _mm_xor_si128(d, k);
        s = _mm_add_epi64(s, _mm_add_epi64(_mm_mul_epu32(x, _mm_srli_epi64(x, 32)), _mm_shuffle_epi32(d, 0x4E)));
    };
    size_t i = 0;
    for (; i + 64 <= bytes; i += 64)
    {
        acc(a0, _mm_loadu_si128(reinterpret_cast<const __m128i*>(data + i)), k0);
        acc(a1, _mm_loadu_si128(reinterpret_cast<const __m128i*>(data + i + 16)), k1);
        acc(b0, _mm_loadu_si128(reinterpret_cast<const __m128i*>(data + i + 32)), k2);
        acc(b1, _mm_loadu_si128(reinterpret_cast<const __m128i*>(data + i + 48)), k3);
    }
    alignas(16) uint64_t lanes[8];
    _mm_store_si128(reinterpret_cast<__m128i*>(lanes), a0);
    _mm_store_si128(reinterpret_cast<__m128i*>(lanes + 2), a1);
    _mm_store_si128(reinterpret_cast<__m128i*>(lanes + 4), b0);
    _mm_store_si128(reinterpret_cast<__m128i*>(lanes + 6), b1);
    uint64_t h = 0xCBF29CE484222325ull;
    for (uint64_t v : lanes)
        h = (h ^ v) * 0xFF51AFD7ED558CCDull, h ^= h >> 29;
    for (; i < bytes; i++)
        h = (h ^ data[i]) * 0x100000001B3ull;
    h = (h ^ (h >> 33)) * 0xC4CEB9FE1A85EC53ull;
    return h ^ (h >> 33);
}

// Every byte of the data, four independent 64-bit lanes at a time (a dword-by-dword chain waits on each
// multiply). For data whose every byte matters and that is checked every frame: index buffers.
// (AVX2 where the CPU has it; REGPU_SCALAR_HASH=1: the scalar lanes.)
uint64_t FullHash(const uint8_t* data, size_t bytes)
{
    static const bool avx2 = IsProcessorFeaturePresent(40 /* PF_AVX2_INSTRUCTIONS_AVAILABLE */) && !getenv("REGPU_SCALAR_HASH");
    if (avx2 && bytes >= 256)
        return FullHashAvx2(data, bytes);
    static LiveSwitch scalar("REGPU_SCALAR_HASH_LIVE"); // the scalar lanes on a CPU without AVX2 (A/B)
    if (!avx2 && bytes >= 256 && !scalar)
        return FullHashSse2(data, bytes);
    uint64_t a = 0xCBF29CE484222325ull ^ bytes, b = 0x9E3779B97F4A7C15ull, c = 0xC2B2AE3D27D4EB4Full, d = 0x165667B19E3779F9ull;
    size_t i = 0;
    for (; i + 32 <= bytes; i += 32)
    {
        uint64_t v[4];
        memcpy(v, data + i, 32);
        a = (a ^ v[0]) * 0xFF51AFD7ED558CCDull;
        b = (b ^ v[1]) * 0xC4CEB9FE1A85EC53ull;
        c = (c ^ v[2]) * 0xFF51AFD7ED558CCDull;
        d = (d ^ v[3]) * 0xC4CEB9FE1A85EC53ull;
        a ^= a >> 29; b ^= b >> 31; c ^= c >> 29; d ^= d >> 31;
    }
    for (; i < bytes; i++)
        a = (a ^ data[i]) * 0x100000001B3ull;
    uint64_t h = a ^ (b * 0x9E3779B97F4A7C15ull) ^ (c * 0xC2B2AE3D27D4EB4Full) ^ (d * 0x165667B19E3779F9ull);
    h = (h ^ (h >> 33)) * 0xFF51AFD7ED558CCDull;
    return h ^ (h >> 33);
}

// Sampled hash for large texture sources: every 64th dword plus the ends.
// (Word-wise: it runs for every small buffer of every draw.)
uint64_t ContentHash(const uint8_t* data, size_t bytes, size_t stride = 256)
{
    auto mix = [](uint64_t h, uint64_t v) {
        h = (h ^ v) * 0xFF51AFD7ED558CCDull;
        return h ^ (h >> 32);
    };
    static LiveSwitch fullHash("REGPU_FULL_CONTENT_HASH"); // A/B: every dword (finds stale cached copies)
    if (fullHash)
        stride = 4;
    uint64_t h = mix(0xCBF29CE484222325ull, bytes);
    for (size_t i = 0; i + 4 <= bytes; i += stride)
    {
        uint32_t v;
        memcpy(&v, data + i, 4);
        h = mix(h, v);
    }
    if (bytes >= 64)
        for (size_t i = bytes - 64; i < bytes; i += 8)
        {
            uint64_t v;
            memcpy(&v, data + i, 8);
            h = mix(h, v);
        }
    return h;
}

// Texture checks hashed ahead on worker threads: each frame starts by hashing the textures the
// last one checked, and the draw takes that hash when 32 spot reads (taken by the worker before
// its hash) still match memory, else hashes there as before. Hashing every texture in use on the
// command processor took ~5 ms a frame (PGR4); the staggered checks that avoided it showed
// streamed textures late. REGPU_NO_TEXTURE_PREHASH hashes on the command processor (A/B).
namespace prehash
{
struct Job
{
    uint64_t key = 0;
    const uint8_t* source = nullptr;
    size_t bytes = 0, stride = 0;
    uint64_t spots = 0, hash = 0;
    std::atomic<uint32_t> state{ 0 }; // 0 waiting, 1 being hashed, 2 done
};
std::unique_ptr<Job[]> g_jobs;
uint32_t g_count = 0;
std::unordered_map<uint64_t, uint32_t> g_index; // key -> job, this frame (command processor only)
struct Pending
{
    uint64_t key;
    const uint8_t* source;
    size_t bytes, stride;
};
std::vector<Pending> g_next; // the textures this frame checks (for the next)
std::mutex g_nextMutex;      // g_next (the draw workers push too)
std::atomic<uint32_t> g_cursor{ 0 }, g_busy{ 0 };
std::atomic<uint64_t> g_generation{ 0 };
std::mutex g_mutex;
std::condition_variable g_cv;

uint64_t Spots(const uint8_t* data, size_t bytes)
{
    uint64_t h = bytes;
    for (uint32_t i = 0; i < 32 && bytes >= 8; i++)
    {
        uint64_t v;
        memcpy(&v, data + (bytes - 8) * i / 31, 8);
        h = (h ^ v) * 0x100000001B3ull;
    }
    return h;
}

uint64_t ContentHashFwd(const uint8_t* data, size_t bytes, size_t stride) { return ContentHash(data, bytes, stride); }

void Work(Job& job)
{
    uint32_t expected = 0;
    if (!job.state.compare_exchange_strong(expected, 1))
        return;
    job.spots = Spots(job.source, job.bytes);
    job.hash = ContentHashFwd(job.source, job.bytes, job.stride);
    job.state.store(2, std::memory_order_release);
}

void Worker()
{
    uint64_t seen = 0;
    for (;;)
    {
        {
            std::unique_lock lock(g_mutex);
            g_cv.wait(lock, [&] { return g_generation.load() != seen; });
            seen = g_generation.load();
            g_busy++;
        }
        for (uint32_t i; (i = g_cursor.fetch_add(1)) < g_count;)
            Work(g_jobs[i]);
        g_busy--;
    }
}

bool Enabled()
{
    static LiveSwitch off("REGPU_NO_TEXTURE_PREHASH");
    return !off;
}

// At the start of a frame: last frame's textures go to the workers.
void StartFrame()
{
    static std::once_flag started;
    std::call_once(started, [] {
        for (int i = 0; i < 3; i++)
            std::thread(Worker).detach();
    });
    // The workers stop taking jobs and finish the one in hand before the jobs are replaced.
    g_cursor.store(~0u >> 1);
    while (g_busy.load())
        _mm_pause();
    g_index.clear();
    g_count = Enabled() ? uint32_t(g_next.size()) : 0;
    g_jobs.reset(g_count ? new Job[g_count] : nullptr);
    for (uint32_t i = 0; i < g_count; i++)
    {
        Job& j = g_jobs[i];
        j.key = g_next[i].key;
        j.source = g_next[i].source;
        j.bytes = g_next[i].bytes;
        j.stride = g_next[i].stride;
        g_index[j.key] = i;
    }
    g_next.clear();
    {
        std::lock_guard lock(g_mutex);
        g_cursor.store(0);
        g_generation++;
    }
    g_cv.notify_all();
}

LiveSwitch g_noAhead("REGPU_NO_TEXTURE_AHEAD");

// A texture that skips its check this frame and is due next frame, queued for the workers. Hash
// queues the textures checked this frame, but with staggered checks (every 4th or 16th frame by
// key) those are not next frame's: the command processor was hashing most textures itself (~5% of
// its time in FM2's race). REGPU_NO_TEXTURE_AHEAD=1: not queued (A/B).
// The draw workers (REGPU_DRAW_WORKERS) use the hashes too: they run only inside the command processor's
// segments, never during StartFrame, so g_index and the jobs are fixed while they read them; their pushes to
// g_next lock. (They hashed every texture themselves before, under the texture mutex: a worker's draws cost
// three times the command processor's in PGR4.)
void Push(const Pending& p)
{
    std::lock_guard lock(g_nextMutex);
    g_next.push_back(p);
}

void Ahead(uint64_t key, const uint8_t* source, size_t bytes, size_t stride)
{
    if (Enabled() && !g_noAhead)
        Push({ key, source, bytes, stride });
}

// The texture's hash, from the workers when they have it and memory still matches.
uint64_t Hash(uint64_t key, const uint8_t* source, size_t bytes, size_t stride)
{
    if (!Enabled())
        return ContentHashFwd(source, bytes, stride);
    Push({ key, source, bytes, stride });
    if (const auto it = g_index.find(key); it != g_index.end())
    {
        Job& job = g_jobs[it->second];
        if (job.source == source && job.bytes == bytes && job.stride == stride)
        {
            Work(job); // not reached yet: hashed here (and spot-checked, harmlessly)
            while (job.state.load(std::memory_order_acquire) != 2)
                _mm_pause();
            if (Spots(source, bytes) == job.spots)
                return job.hash;
        }
    }
    return ContentHashFwd(source, bytes, stride);
}
}

D3D12_CPU_DESCRIPTOR_HANDLE Cpu(ID3D12DescriptorHeap* heap, UINT index, UINT stride)
{
    D3D12_CPU_DESCRIPTOR_HANDLE h = heap->GetCPUDescriptorHandleForHeapStart();
    h.ptr += SIZE_T(index) * stride;
    return h;
}

D3D12_GPU_DESCRIPTOR_HANDLE Gpu(ID3D12DescriptorHeap* heap, UINT index, UINT stride)
{
    D3D12_GPU_DESCRIPTOR_HANDLE h = heap->GetGPUDescriptorHandleForHeapStart();
    h.ptr += UINT64(index) * stride;
    return h;
}

ComPtr<ID3D12Resource> CreateBuffer(UINT64 size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES state)
{
    D3D12_HEAP_PROPERTIES hp{};
    hp.Type = heap;
    D3D12_RESOURCE_DESC rd{};
    rd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    rd.Width = size;
    rd.Height = 1;
    rd.DepthOrArraySize = 1;
    rd.MipLevels = 1;
    rd.SampleDesc.Count = 1;
    rd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    ComPtr<ID3D12Resource> r;
    Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, state, nullptr, IID_PPV_ARGS(&r)), "CreateCommittedResource(buffer)"); CountCreate("buffer");
    return r;
}

// Whether the GPU sees guest memory through the placed buffer over it. A driver can open the heap
// and still not alias it (vkd3d-proton on RADV: the calls succeed and the GPU reads zeros, so
// in-place vertex fetches draw nothing): a value written by the CPU is copied out by the GPU and
// compared. The block is ours (Host::alloc), not memory the title may be using.
bool PhysicalHeapAliasesGuest(ID3D12Device* d, ID3D12Resource* physical)
{
    const uint32_t address = RegpuGuestAlloc(Heap::Physical, 4096, 4096);
    if (!address)
        return true; // nothing to test with: trust the mapping, as before
    uint32_t pattern[4];
    for (uint32_t i = 0; i < 4; i++)
        pattern[i] = 0x9E3779B9u * (i + 1) ^ uint32_t(GetTickCount64()) ^ address;
    memcpy(GuestPtr<uint8_t>(address), pattern, sizeof(pattern));

    ComPtr<ID3D12Resource> readback = CreateBuffer(256, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<ID3D12Fence> fence;
    if (FAILED(d->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator))) ||
        FAILED(d->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator.Get(), nullptr, IID_PPV_ARGS(&list))) ||
        FAILED(d->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence))))
        return true;
    list->CopyBufferRegion(readback.Get(), 0, physical, address & 0x1FFFFFFF, sizeof(pattern));
    list->Close();
    ID3D12CommandList* lists[] = { list.Get() };
    g_r.queue->ExecuteCommandLists(1, lists);
    const HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    g_r.queue->Signal(fence.Get(), 1);
    fence->SetEventOnCompletion(1, event);
    WaitForSingleObject(event, 5000);
    CloseHandle(event);
    if (fence->GetCompletedValue() < 1)
        return false; // the copy never finished: don't read through this heap
    uint32_t seen[4] = {};
    void* mapped = nullptr;
    const D3D12_RANGE range{ 0, sizeof(seen) };
    if (SUCCEEDED(readback->Map(0, &range, &mapped)))
    {
        memcpy(seen, mapped, sizeof(seen));
        readback->Unmap(0, nullptr);
    }
    if (memcmp(seen, pattern, sizeof(pattern)) == 0)
        return true;
    Log("renderer: guest memory self-test at %08X wrote %08X %08X, the GPU read %08X %08X", address, pattern[0], pattern[1], seen[0], seen[1]);
    return false;
}

// Depth resolves: a depth-stencil buffer can only be copied whole, so the
// resolve rectangle is drawn into an R32_FLOAT texture by a tiny pass that
// reads the depth buffer at the same pixel.
void InitDepthBlit()
{
    auto* d = g_r.device;
    // Anti-aliasing: the highest sample count the setting asks for that the device supports
    // for both the colour and depth formats (1 when none).
    g_msaa = 1;
    for (uint32_t want = GetSettings().msaa; want > 1; want /= 2)
    {
        D3D12_FEATURE_DATA_MULTISAMPLE_QUALITY_LEVELS colour{ kColorFormat, want }, depth{ DXGI_FORMAT_D32_FLOAT_S8X24_UINT, want };
        if (SUCCEEDED(d->CheckFeatureSupport(D3D12_FEATURE_MULTISAMPLE_QUALITY_LEVELS, &colour, sizeof colour)) && colour.NumQualityLevels &&
            SUCCEEDED(d->CheckFeatureSupport(D3D12_FEATURE_MULTISAMPLE_QUALITY_LEVELS, &depth, sizeof depth)) && depth.NumQualityLevels)
        {
            g_msaa = want;
            break;
        }
    }
    Log("renderer: anti-aliasing %ux (setting %u)", g_msaa, GetSettings().msaa);
    D3D12_DESCRIPTOR_RANGE range{};
    range.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SRV;
    range.NumDescriptors = 2; // (t1: the depth pack's stencil plane; the other blits read t0 only)
    D3D12_ROOT_PARAMETER params[2]{};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[0].DescriptorTable.NumDescriptorRanges = 1;
    params[0].DescriptorTable.pDescriptorRanges = &range;
    params[0].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[1].Constants.Num32BitValues = 6;
    params[1].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    D3D12_ROOT_SIGNATURE_DESC rs{};
    rs.NumParameters = 2;
    rs.pParameters = params;
    ComPtr<ID3DBlob> blob, error;
    if (FAILED(D3D12SerializeRootSignature(&rs, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &error)))
        Fatal("renderer: blit root signature: %s", error ? static_cast<const char*>(error->GetBufferPointer()) : "?");
    Check(d->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&g_r.blitRootSignature)),
        "CreateRootSignature(blit)");

    static const char kSource[] = R"(
Texture2D<float> depthTex : register(t0);
Texture2D<float4> colorTex : register(t0);
cbuffer Blit : register(b0) { float scale; uint swapRB; uint clampDest; float destMin; float destMax; uint toGamma; };
// Xenia's LinearToPWLGamma: what a k_8_8_8_8_GAMMA surface holds in memory.
float LinearToPWLGamma(float l)
{
    l = saturate(l);
    float scale, offset;
    if (l >= 128.0 / 1023.0)
    {
        if (l >= 512.0 / 1023.0) { scale = 1023.0 / 8.0; offset = 128.0 / 255.0; }
        else { scale = 1023.0 / 4.0; offset = 64.0 / 255.0; }
    }
    else
    {
        if (l >= 64.0 / 1023.0) { scale = 1023.0 / 2.0; offset = 32.0 / 255.0; }
        else { scale = 1023.0; offset = 0.0; }
    }
    return trunc(l * scale) * (1.0 / 255.0) + offset;
}
float4 vs(uint id : SV_VertexID) : SV_Position
{
    float2 uv = float2((id << 1) & 2, id & 2);
    return float4(uv * float2(2, -2) + float2(-1, 1), 0, 1);
}
float ps(float4 pos : SV_Position) : SV_Target
{
    return depthTex.Load(int3(int2(pos.xy), 0));
}
Texture2D<uint2> stencilTex : register(t1);
// 20e4 (D24FS8): 4-bit exponent biased by 15, 20-bit mantissa, [0, 2).
uint Encode20e4(float v)
{
    v = clamp(v, 0.0, 1.9999999);
    if (v < exp2(-14.0))
        return uint(v * exp2(14.0) * 1048576.0);
    int e = int(floor(log2(v)));
    float m = v / exp2(float(e)) - 1.0;
    return (uint(e + 15) << 20) | min(uint(m * 1048576.0), 0xFFFFFu);
}
float4 psPack(float4 pos : SV_Position) : SV_Target
{
    float z = depthTex.Load(int3(int2(pos.xy), 0));
    uint st = stencilTex.Load(int3(int2(pos.xy), 0)).g;
    uint d = swapRB != 0 ? Encode20e4(z) : uint(round(saturate(z) * 16777215.0));
    return float4(float(st & 0xFFu), float(d & 0xFFu), float((d >> 8) & 0xFFu), float(d >> 16)) / 255.0;
}
float4 psColor(float4 pos : SV_Position) : SV_Target
{
    float4 c = colorTex.Load(int3(int2(pos.xy), 0)) * scale;
    if (clampDest) c = clamp(c, destMin, destMax);
    if (toGamma & 1u) c.rgb = float3(LinearToPWLGamma(c.r), LinearToPWLGamma(c.g), LinearToPWLGamma(c.b));
    if (toGamma & 2u) c.a = round(saturate(c.a) * 3.0) / 3.0; // a 2-bit alpha destination (k_2_10_10_10)
    return swapRB ? c.bgra : c;
}
)";
    ComPtr<ID3DBlob> vs, ps;
    Check(D3DCompile(kSource, sizeof(kSource) - 1, nullptr, nullptr, nullptr, "vs", "vs_5_0", 0, 0, &vs, &error), "D3DCompile(blit vs)");
    Check(D3DCompile(kSource, sizeof(kSource) - 1, nullptr, nullptr, nullptr, "ps", "ps_5_0", 0, 0, &ps, &error), "D3DCompile(blit ps)");
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd{};
    pd.pRootSignature = g_r.blitRootSignature.Get();
    pd.VS = { vs->GetBufferPointer(), vs->GetBufferSize() };
    pd.PS = { ps->GetBufferPointer(), ps->GetBufferSize() };
    pd.BlendState.RenderTarget[0].RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    pd.SampleMask = UINT_MAX;
    pd.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    pd.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    static LiveSwitch noCull("REGPU_NO_CULL");
    if (noCull)
        pd.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    pd.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pd.NumRenderTargets = 1;
    pd.RTVFormats[0] = DXGI_FORMAT_R32_FLOAT;
    pd.SampleDesc.Count = 1;
    Check(d->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&g_r.depthBlit)), "CreateGraphicsPipelineState(depth blit)");
    {
        ComPtr<ID3DBlob> psPack;
        Check(D3DCompile(kSource, sizeof(kSource) - 1, nullptr, nullptr, nullptr, "psPack", "ps_5_0", 0, 0, &psPack, &error), "D3DCompile(blit psPack)");
        D3D12_GRAPHICS_PIPELINE_STATE_DESC pack = pd;
        pack.PS = { psPack->GetBufferPointer(), psPack->GetBufferSize() };
        pack.RTVFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
        Check(d->CreateGraphicsPipelineState(&pack, IID_PPV_ARGS(&g_r.depthPackBlit)), "CreateGraphicsPipelineState(depth pack blit)");
    }
    ComPtr<ID3DBlob> psColor;
    Check(D3DCompile(kSource, sizeof(kSource) - 1, nullptr, nullptr, nullptr, "psColor", "ps_5_0", 0, 0, &psColor, &error), "D3DCompile(blit psColor)");
    pd.PS = { psColor->GetBufferPointer(), psColor->GetBufferSize() };
    pd.RTVFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
    Check(d->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&g_r.colorBlit8)), "CreateGraphicsPipelineState(colour blit 8)");
    pd.RTVFormats[0] = DXGI_FORMAT_R16G16B16A16_FLOAT;
    Check(d->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&g_r.colorBlit16)), "CreateGraphicsPipelineState(colour blit 16)");
    if (g_msaa > 1)
    {
        // The same passes reading a multisampled surface: depth takes sample 0, colour averages all samples.
        const std::string msSource = "#define SAMPLES " + std::to_string(g_msaa) + R"(
Texture2DMS<float, SAMPLES> depthMs : register(t0);
Texture2DMS<float4, SAMPLES> colorMs : register(t0);
cbuffer Blit : register(b0) { float scale; uint swapRB; uint clampDest; float destMin; float destMax; uint toGamma; };
// Xenia's LinearToPWLGamma: what a k_8_8_8_8_GAMMA surface holds in memory.
float LinearToPWLGamma(float l)
{
    l = saturate(l);
    float scale, offset;
    if (l >= 128.0 / 1023.0)
    {
        if (l >= 512.0 / 1023.0) { scale = 1023.0 / 8.0; offset = 128.0 / 255.0; }
        else { scale = 1023.0 / 4.0; offset = 64.0 / 255.0; }
    }
    else
    {
        if (l >= 64.0 / 1023.0) { scale = 1023.0 / 2.0; offset = 32.0 / 255.0; }
        else { scale = 1023.0; offset = 0.0; }
    }
    return trunc(l * scale) * (1.0 / 255.0) + offset;
}
float4 vs(uint id : SV_VertexID) : SV_Position
{
    float2 uv = float2((id << 1) & 2, id & 2);
    return float4(uv * float2(2, -2) + float2(-1, 1), 0, 1);
}
float ps(float4 pos : SV_Position) : SV_Target
{
    return depthMs.Load(int2(pos.xy), 0);
}
float4 psColor(float4 pos : SV_Position) : SV_Target
{
    float4 c = 0;
    [unroll] for (int i = 0; i < SAMPLES; i++)
        c += colorMs.Load(int2(pos.xy), i);
    c = c / SAMPLES * scale;
    if (clampDest) c = clamp(c, destMin, destMax);
    if (toGamma & 1u) c.rgb = float3(LinearToPWLGamma(c.r), LinearToPWLGamma(c.g), LinearToPWLGamma(c.b));
    if (toGamma & 2u) c.a = round(saturate(c.a) * 3.0) / 3.0; // a 2-bit alpha destination (k_2_10_10_10)
    return swapRB ? c.bgra : c;
}
)";
        ComPtr<ID3DBlob> msVs, msPs, msColor;
        Check(D3DCompile(msSource.data(), msSource.size(), nullptr, nullptr, nullptr, "vs", "vs_5_0", 0, 0, &msVs, &error), "D3DCompile(blit ms vs)");
        Check(D3DCompile(msSource.data(), msSource.size(), nullptr, nullptr, nullptr, "ps", "ps_5_0", 0, 0, &msPs, &error), "D3DCompile(blit ms depth)");
        Check(D3DCompile(msSource.data(), msSource.size(), nullptr, nullptr, nullptr, "psColor", "ps_5_0", 0, 0, &msColor, &error), "D3DCompile(blit ms colour)");
        pd.VS = { msVs->GetBufferPointer(), msVs->GetBufferSize() };
        pd.PS = { msPs->GetBufferPointer(), msPs->GetBufferSize() };
        pd.RTVFormats[0] = DXGI_FORMAT_R32_FLOAT;
        Check(d->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&g_r.depthBlitMs)), "CreateGraphicsPipelineState(depth blit ms)");
        pd.PS = { msColor->GetBufferPointer(), msColor->GetBufferSize() };
        pd.RTVFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
        Check(d->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&g_r.colorBlitMs8)), "CreateGraphicsPipelineState(colour blit ms 8)");
        pd.RTVFormats[0] = DXGI_FORMAT_R16G16B16A16_FLOAT;
        Check(d->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&g_r.colorBlitMs16)), "CreateGraphicsPipelineState(colour blit ms 16)");
    }
}

void InitPipelineLibrary(ID3D12Device* device);

// Guest physical memory for the GPU, in place: vertex fetches read it without copies. Called at
// init and, when that fails, again at a few frames after (FrameBegin): under ReXGlue the heap calls
// can fail with E_INVALIDARG at init and succeed later (FH1 TU4 on one PC: about half its runs on
// the copy path, 7 ms a frame of vertex copies). REGPU_NO_MAP_RETRY=1: init only.
void MapGuestMemory(ID3D12Device* d)
{
    // A title can make a page of physical memory no-access (FH1 TU4 guards the page after its
    // minimap geometry ring), and the heap calls refuse a view with such a page (E_INVALIDARG): on
    // one PC the guard was there before the renderer started and every run took the copy path. The
    // GPU doesn't see CPU protection, so those pages are made read-write (as the rest of the view) while the heap is opened and
    // restored after. REGPU_NO_MAP_UNGUARD=1: as found.
    std::vector<std::pair<std::pair<void*, size_t>, DWORD>> guarded;
    if (!getenv("REGPU_NO_MAP_UNGUARD"))
    {
        uint8_t* at = GuestPtr<uint8_t>(0xA0000000);
        uint8_t* const end = at + 0x20000000;
        MEMORY_BASIC_INFORMATION mbi{};
        while (at < end && VirtualQuery(at, &mbi, sizeof(mbi)))
        {
            const size_t size = std::min<size_t>(mbi.RegionSize - (at - static_cast<uint8_t*>(mbi.BaseAddress)), end - at);
            DWORD old = 0;
            if (mbi.State == MEM_COMMIT && ((mbi.Protect & PAGE_NOACCESS) || (mbi.Protect & PAGE_GUARD)) && VirtualProtect(at, size, PAGE_READWRITE, &old))
                guarded.push_back({ { at, size }, old });
            at += size;
        }
        if (!guarded.empty())
            Log("renderer: %zu no-access range(s) in guest physical memory made readable while the heap is opened", guarded.size());
    }
    struct Restore
    {
        std::vector<std::pair<std::pair<void*, size_t>, DWORD>>& ranges;
        ~Restore()
        {
            for (const auto& [range, old] : ranges)
            {
                DWORD unused = 0;
                VirtualProtect(range.first, range.second, old, &unused);
            }
        }
    } restore{ guarded };
    ComPtr<ID3D12Device13> d13;
    HRESULT hr = getenv("REGPU_FORCE_DEVICE3") ? E_NOINTERFACE : d->QueryInterface(IID_PPV_ARGS(&d13)); // (REGPU_FORCE_DEVICE3: test the fallback)
    if (SUCCEEDED(hr))
        hr = d13->OpenExistingHeapFromFileMapping(RegpuPhysicalSection(), IID_PPV_ARGS(&g_r.physicalHeap));
    if (FAILED(hr) && d13)
    {
        Log("renderer: OpenExistingHeapFromFileMapping failed %08X; trying the view address", unsigned(hr));
        hr = d13->OpenExistingHeapFromAddress1(GuestPtr(0xA0000000), 0x20000000, IID_PPV_ARGS(&g_r.physicalHeap));
}
// Older runtimes (Windows 10, older drivers) have no ID3D12Device13, but ID3D12Device3 (Windows 10
// 1709+) opens a heap over the whole allocation an address is in: the 512 MB view of guest
// physical memory at 0xA0000000 is exactly that. The self-test below checks the GPU sees it.
// (From the FM2 port: 211 fps against 150 on the copy path there.) Also when ID3D12Device13's calls
// fail: under ReXGlue on one PC both returned E_INVALIDARG while this one works (FH1 on the copy
// path: 7 ms a frame of vertex copies, 63 fps against stock's 96).
if (FAILED(hr))
{
    if (d13)
        Log("renderer: mapping guest memory: OpenExistingHeapFromAddress1 failed %08X; trying ID3D12Device3", unsigned(hr));
    ComPtr<ID3D12Device3> d3;
    if (SUCCEEDED(hr = d->QueryInterface(IID_PPV_ARGS(&d3))))
        hr = d3->OpenExistingHeapFromAddress(GuestPtr(0xA0000000), IID_PPV_ARGS(&g_r.physicalHeap));
    if (SUCCEEDED(hr))
        Log("renderer: guest memory opened through ID3D12Device3, %llu MB", (unsigned long long)(g_r.physicalHeap->GetDesc().SizeInBytes >> 20));
    else
        Log("renderer: mapping guest memory: ID3D12Device3 failed %08X", unsigned(hr));
}
if (SUCCEEDED(hr))
{
    D3D12_RESOURCE_DESC bd{};
    bd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    bd.Width = g_r.physicalHeap->GetDesc().SizeInBytes;
    bd.Height = 1;
    bd.DepthOrArraySize = 1;
    bd.MipLevels = 1;
    bd.SampleDesc.Count = 1;
    bd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    bd.Flags = D3D12_RESOURCE_FLAG_ALLOW_CROSS_ADAPTER;
    if (FAILED(hr = d->CreatePlacedResource(g_r.physicalHeap.Get(), 0, &bd, D3D12_RESOURCE_STATE_COMMON, nullptr, IID_PPV_ARGS(&g_r.physical))))
    {
        Log("renderer: placing the guest memory buffer failed %08X", unsigned(hr));
        g_r.physical.Reset();
    }
}
// The calls can succeed on a driver that doesn't alias the memory (vkd3d-proton on RADV): test it.
// REGPU_NO_HEAP_SELFTEST=1 trusts the mapping (A/B).
if (g_r.physical && !getenv("REGPU_NO_HEAP_SELFTEST") && !PhysicalHeapAliasesGuest(d, g_r.physical.Get()))
{
    Log("renderer: the GPU does not see guest memory through the mapped heap; vertex data is copied instead");
    g_r.physical.Reset();
    g_r.physicalHeap.Reset();
}
Log("renderer: guest memory %s", g_r.physical ? "mapped for the GPU (zero-copy vertex fetch)" : "not mappable; vertex data is copied");
// For a port's launcher ("Reduced performance" warning): what this PC actually got.
if (FILE* f = _wfopen((regpu::GetConfig().userDir / "zero_copy.txt").c_str(), L"w"))
{
    fputs(g_r.physical ? "1" : "0", f);
    fclose(f);
}
}

// REGPU_ANISO_FROM_FETCH: anisotropic filtering as each fetch asks (see Init's samplers). Read when the
// root signature is built, and by each texture binding.
bool AnisoFromFetch()
{
    static LiveSwitch fromFetch("REGPU_ANISO_FROM_FETCH");
    return fromFetch;
}

void Init()
{
    g_r.device = RenderDevice();
    g_r.queue = RenderQueue();
    // REGPU_D3D12_STACKS=1 (with REGPU_D3D12_DEBUG): each debug-layer error is logged when it
    // happens, with the stack of the call that made it (the first 3 of each kind).
    if (getenv("REGPU_D3D12_STACKS"))
    {
        ComPtr<ID3D12InfoQueue1> queue;
        DWORD cookie = 0;
        if (SUCCEEDED(g_r.device->QueryInterface(IID_PPV_ARGS(&queue))))
            queue->RegisterMessageCallback(
                [](D3D12_MESSAGE_CATEGORY, D3D12_MESSAGE_SEVERITY severity, D3D12_MESSAGE_ID id, LPCSTR description, void*) {
                    if (severity > D3D12_MESSAGE_SEVERITY_ERROR)
                        return;
                    static std::mutex m;
                    static std::unordered_map<int, int> seen;
                    std::lock_guard lock(m);
                    if (seen[int(id)]++ >= 3)
                        return;
                    const auto stack = regpu::GetHost().currentStack;
                    Log("d3d12 error %d: %s\n  at %s", int(id), description, stack ? stack(16).c_str() : "?");
                },
                D3D12_MESSAGE_CALLBACK_FLAG_NONE, nullptr, &cookie);
        else
            Log("renderer: no ID3D12InfoQueue1 (REGPU_D3D12_STACKS needs the debug layer)");
    }
    auto* d = g_r.device;

    // Root signature: b0, b1 root CBVs; one table t0-t32; four static samplers.
    D3D12_DESCRIPTOR_RANGE range{};
    range.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SRV;
    range.NumDescriptors = 2; // t0 upload ring, t1 guest memory
    D3D12_DESCRIPTOR_RANGE bindless[3]{};
    for (UINT i = 0; i < 3; i++)
    {
        // 2D textures in space1, cubes in space2, 3D in space3, over the same descriptors.
        bindless[i].RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SRV;
        bindless[i].NumDescriptors = kBindless;
        bindless[i].RegisterSpace = 1 + i;
        bindless[i].OffsetInDescriptorsFromTableStart = 0;
    }
    D3D12_ROOT_PARAMETER params[6]{};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    params[0].Descriptor.ShaderRegister = 0;
    params[0].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    params[1].Descriptor.ShaderRegister = 1;
    params[1].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    params[2].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[2].DescriptorTable.NumDescriptorRanges = 1;
    params[2].DescriptorTable.pDescriptorRanges = &range;
    params[2].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    params[3].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[3].DescriptorTable.NumDescriptorRanges = 3;
    params[3].DescriptorTable.pDescriptorRanges = bindless;
    params[3].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    // s0-s3 linear wrap, linear clamp, point clamp, point wrap; s4-s7 linear wrap at 2x, 4x, 8x, 16x
    // anisotropy (REGPU_ANISO_FROM_FETCH, below).
    D3D12_STATIC_SAMPLER_DESC samplers[8]{};
    const D3D12_FILTER filters[4] = { D3D12_FILTER_MIN_MAG_MIP_LINEAR, D3D12_FILTER_MIN_MAG_MIP_LINEAR, D3D12_FILTER_MIN_MAG_MIP_POINT,
        D3D12_FILTER_MIN_MAG_MIP_POINT };
    const D3D12_TEXTURE_ADDRESS_MODE modes[4] = { D3D12_TEXTURE_ADDRESS_MODE_WRAP, D3D12_TEXTURE_ADDRESS_MODE_CLAMP,
        D3D12_TEXTURE_ADDRESS_MODE_CLAMP, D3D12_TEXTURE_ADDRESS_MODE_WRAP };
    for (UINT i = 0; i < 8; i++)
    {
        samplers[i].Filter = filters[i < 4 ? i : 0];
        // Wrapping linear filtering (surface textures: road, ground) is anisotropic: plain
        // trilinear picks blurred levels at grazing angles (blotchy asphalt). Not the clamped
        // sampler: it also reads shadow maps and other render targets, where averaging depth
        // over a long footprint breaks the shadows (black patches, glitchy object shadows).
        // REGPU_ANISO_FROM_FETCH: s0 stays trilinear and a fetch gets the anisotropy it asks for (dword 3
        // bits 25-27), as the console and Xenia give it; forced 16x made FH1's distant ground about 20%
        // sharper than stock's (fine-detail measure, FX).
        static LiveSwitch noAniso("REGPU_NO_ANISO"); // A/B
        if (i >= 4)
        {
            samplers[i].Filter = D3D12_FILTER_ANISOTROPIC;
            samplers[i].MaxAnisotropy = 2u << (i - 4);
        }
        else if (filters[i] == D3D12_FILTER_MIN_MAG_MIP_LINEAR && modes[i] == D3D12_TEXTURE_ADDRESS_MODE_WRAP && !noAniso && !AnisoFromFetch())
        {
            samplers[i].Filter = D3D12_FILTER_ANISOTROPIC;
            samplers[i].MaxAnisotropy = 16;
        }
        samplers[i].AddressU = samplers[i].AddressV = samplers[i].AddressW = i >= 4 ? D3D12_TEXTURE_ADDRESS_MODE_WRAP : modes[i];
        samplers[i].MaxLOD = D3D12_FLOAT32_MAX;
        samplers[i].ShaderRegister = i;
        samplers[i].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    }
    // REGPU_LEAN_FETCH (hlsl.cpp): the same eight samplers as a table in space1, which a shader can
    // index (static samplers can't back a dynamically indexed array).
    D3D12_DESCRIPTOR_RANGE samplerRange{};
    samplerRange.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SAMPLER;
    samplerRange.NumDescriptors = 8;
    samplerRange.RegisterSpace = 1;
    D3D12_ROOT_SIGNATURE_DESC rs{};
    rs.NumParameters = 4;
    rs.pParameters = params;
    rs.NumStaticSamplers = 8;
    rs.pStaticSamplers = samplers;
    if (LeanFetch())
    {
        params[4].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
        params[4].DescriptorTable.NumDescriptorRanges = 1;
        params[4].DescriptorTable.pDescriptorRanges = &samplerRange;
        params[4].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
        rs.NumParameters = 5;
        D3D12_DESCRIPTOR_HEAP_DESC sh{};
        sh.Type = D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER;
        sh.NumDescriptors = 8 * kSamplerBanks;
        sh.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
        Check(d->CreateDescriptorHeap(&sh, IID_PPV_ARGS(&g_r.samplerHeap)), "CreateDescriptorHeap(sampler)");
        g_samplerStride = d->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER);
        for (UINT i = 0; i < 8; i++)
        {
            D3D12_SAMPLER_DESC& s = g_samplerBase[i];
            s.Filter = samplers[i].Filter;
            s.AddressU = samplers[i].AddressU;
            s.AddressV = samplers[i].AddressV;
            s.AddressW = samplers[i].AddressW;
            s.MaxAnisotropy = samplers[i].MaxAnisotropy;
            s.ComparisonFunc = samplers[i].ComparisonFunc;
            s.MaxLOD = samplers[i].MaxLOD;
        }
        WriteSamplerBank(0, -1);
    }
    ComPtr<ID3DBlob> blob, error;
    if (FAILED(D3D12SerializeRootSignature(&rs, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &error)))
        Fatal("renderer: root signature: %s", error ? static_cast<const char*>(error->GetBufferPointer()) : "?");
    Check(d->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&g_r.rootSignature)), "CreateRootSignature");
    // Motion-vector pipelines: the same plus b2, last frame's vertex constants of the draw.
    params[rs.NumParameters].ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    params[rs.NumParameters].Descriptor.ShaderRegister = 2;
    params[rs.NumParameters].ShaderVisibility = D3D12_SHADER_VISIBILITY_VERTEX;
    rs.NumParameters++;
    blob.Reset();
    error.Reset();
    if (FAILED(D3D12SerializeRootSignature(&rs, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &error)))
        Fatal("renderer: motion root signature: %s", error ? static_cast<const char*>(error->GetBufferPointer()) : "?");
    Check(d->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&g_r.motionRootSignature)),
        "CreateRootSignature(motion)");

    D3D12_DESCRIPTOR_HEAP_DESC hd{};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
    hd.NumDescriptors = kBindlessBase + kBindless;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    Check(d->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_r.srvHeap)), "CreateDescriptorHeap(srv)");
    g_r.srvStride = d->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    hd.NumDescriptors = 2 + 2 * kFrames; // null 2D, null cube, per frame: ring and guest-memory raw views
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_NONE;
    Check(d->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_r.stagingHeap)), "CreateDescriptorHeap(staging)");
    hd.NumDescriptors = 1;
    D3D12_SHADER_RESOURCE_VIEW_DESC nullSrv{};
    nullSrv.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    nullSrv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    nullSrv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    nullSrv.Texture2D.MipLevels = 1;
    d->CreateShaderResourceView(nullptr, &nullSrv, Cpu(g_r.stagingHeap.Get(), 0, g_r.srvStride));
    nullSrv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURECUBE;
    nullSrv.TextureCube.MipLevels = 1;
    d->CreateShaderResourceView(nullptr, &nullSrv, Cpu(g_r.stagingHeap.Get(), 1, g_r.srvStride));
    d->CopyDescriptorsSimple(2, Cpu(g_r.srvHeap.Get(), kBindlessBase, g_r.srvStride), Cpu(g_r.stagingHeap.Get(), 0, g_r.srvStride),
        D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    nullSrv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE3D;
    nullSrv.Texture3D.MipLevels = 1;
    d->CreateShaderResourceView(nullptr, &nullSrv, Cpu(g_r.srvHeap.Get(), kBindlessBase + 2, g_r.srvStride));

    InitPipelineLibrary(d);
    MapGuestMemory(d);

    // Views for the EDRAM surfaces (created on first use).
    hd.NumDescriptors = kMaxSurfaces;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    Check(d->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_r.rtvHeap)), "CreateDescriptorHeap(rtv)");
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_DSV;
    Check(d->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_r.dsvHeap)), "CreateDescriptorHeap(dsv)");
    hd.NumDescriptors = 1;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    Check(d->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_r.blitRtvHeap)), "CreateDescriptorHeap(blit rtv)");
    InitDepthBlit();
    g_r.readback = CreateBuffer(UINT64((OutputWidth() * 4 + 255) & ~255u) * OutputHeight(), D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);

    kRingSize = (getenv("REGPU_RING_MB") ? std::clamp<UINT64>(UINT64(atoi(getenv("REGPU_RING_MB"))), 16, 2048)
        // (128 MB with the zero-copy mapping, not 64: new vertex buffers are copied for their first 16 frames,
        // and the frame a world loads in filled 64 MB and dropped draws until the ring had grown.)
        : regpu::GetConfig().uploadRingMB ? std::clamp<UINT64>(regpu::GetConfig().uploadRingMB, 16, 2048) : g_r.physical && !getenv("REGPU_NO_DIRECT_FETCH") ? 128 : 384) << 20;
    Log("renderer: upload ring %llu MB per frame", (unsigned long long)(kRingSize >> 20));
    for (UINT i = 0; i < kFrames; i++)
    {
        FrameResources& f = g_r.frames[i];
        Check(d->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&f.allocator)), "CreateCommandAllocator");
        f.ring = CreateBuffer(kRingSize, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
        f.ring->Map(0, nullptr, reinterpret_cast<void**>(&f.ringCpu));
        f.ringSize = kRingSize;
        f.descriptorBase = i * kDescriptorsPerFrame;
    }
    Check(d->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, g_r.frames[0].allocator.Get(), nullptr, IID_PPV_ARGS(&g_r.realList)),
        "CreateCommandList");
    g_r.realList->Close();
    g_r.recorder.Init(g_r.realList.Get());
    g_mainContext.list = &g_r.recorder;
    Check(d->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&g_r.fence)), "CreateFence");
    g_r.fenceEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    PresenterUseRenderer();
    g_r.ok = true;
    Log("renderer: D3D12 renderer ready");
}

double g_gpuWaitSeconds = 0; // CP thread blocked on the host GPU (frame resources)

FrameResources& Frame() { return g_r.frames[g_r.frameIndex]; }

// A new output resolution (regpu::SetOutputSize), taken at the start of a frame with the host GPU idle:
// the EDRAM surfaces (made at the output size on first use) are dropped and come back at the new size,
// as do the frame readback, the upscaler's images and the swap chain. Resolved images take their new
// size at their next resolve. The guest redraws every surface it uses each frame.
void ApplyOutputSize()
{
    const uint64_t request = g_outputRequest.exchange(0);
    if (!request || !g_r.ok)
        return;
    Settings& st = GetSettings();
    const uint32_t w = uint32_t(request >> 32), h = uint32_t(request);
    if (w == st.width && h == st.height)
        return;
    // Every frame in flight may still use the old surfaces.
    if (const UINT64 last = g_r.nextFence - 1; g_r.fence->GetCompletedValue() < last)
    {
        g_r.fence->SetEventOnCompletion(last, g_r.fenceEvent);
        WaitForSingleObject(g_r.fenceEvent, INFINITE);
    }
    st.width = w;
    st.height = h;
    LoadOutputSize();
    {
        // Released with this frame, not now: until the frame start rebinds (SelectSurfaces), commands
        // still name the old surfaces' views (releasing them here crashed the replay thread).
        std::lock_guard surfaceLock(g_surfaceMutex);
        for (auto* map : { &g_r.colorSurfaces, &g_r.depthSurfaces })
        {
            for (auto& [base, s] : *map)
                Frame().garbage.push_back(std::move(s.resource));
            map->clear();
        }
    }
    C().colorBase = C().depthBase = ~0u; // nothing bound: the frame start selects the new surfaces
    C().color.Reset();
    C().depth.Reset();
    g_r.readback = CreateBuffer(UINT64((OutputWidth() * 4 + 255) & ~255u) * OutputHeight(), D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    g_upscale.depth.Reset();
    g_upscale.motion.Reset();
    g_upscale.dlaaOut.Reset();
    g_upscale.renderWidth = g_upscale.renderHeight = 0;
    g_upscale.reset = true;
    PresenterResize(OutputWidth(), OutputHeight());
    Log("renderer: output %ux%u (live)", OutputWidth(), OutputHeight());
}

void TransitionColor(D3D12_RESOURCE_STATES to);

// Gets (creating on first use) the host surface for an EDRAM base.
bool TemporalInit();
void BeginTemporalFrame();

Renderer::Surface& GetSurface(bool depth, uint32_t base)
{
    std::lock_guard surfaceLock(g_surfaceMutex);
    auto& map = depth ? g_r.depthSurfaces : g_r.colorSurfaces;
    if (auto it = map.find(base); it != map.end())
        return it->second;
    if (map.size() >= kMaxSurfaces)
        return map.begin()->second; // out of views: share one (should not happen)
    Renderer::Surface& s = map[base];
    D3D12_HEAP_PROPERTIES hp{};
    hp.Type = D3D12_HEAP_TYPE_DEFAULT;
    D3D12_RESOURCE_DESC td{};
    td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width = SurfaceWidth();
    td.Height = SurfaceHeight();
    td.DepthOrArraySize = 1;
    td.MipLevels = 1;
    td.SampleDesc.Count = g_msaa;
    D3D12_CLEAR_VALUE cv{};
    const UINT index = UINT(map.size() - 1);
    if (depth)
    {
        td.Format = DXGI_FORMAT_R32G8X24_TYPELESS; // float depth (the title uses D24FS8, reverse-Z); typeless for resolves
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL;
        cv.Format = DXGI_FORMAT_D32_FLOAT_S8X24_UINT;
        cv.DepthStencil.Depth = 1.0f;
        Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_DEPTH_WRITE, &cv,
                  IID_PPV_ARGS(&s.resource)),
            "CreateCommittedResource(depth surface)"); CountCreate("depth surface");
        s.state = D3D12_RESOURCE_STATE_DEPTH_WRITE;
        s.view = g_r.dsvHeap->GetCPUDescriptorHandleForHeapStart();
        s.view.ptr += SIZE_T(index) * g_r.device->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_DSV);
        D3D12_DEPTH_STENCIL_VIEW_DESC dsvDesc{};
        dsvDesc.Format = DXGI_FORMAT_D32_FLOAT_S8X24_UINT;
        dsvDesc.ViewDimension = g_msaa > 1 ? D3D12_DSV_DIMENSION_TEXTURE2DMS : D3D12_DSV_DIMENSION_TEXTURE2D;
        C().list->CreateDepthStencilView(g_r.device, s.resource.Get(), dsvDesc, s.view); // in order with the recorded commands
    }
    else
    {
        td.Format = kColorFormat;
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
        cv.Format = td.Format;
        Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_RENDER_TARGET, &cv,
                  IID_PPV_ARGS(&s.resource)),
            "CreateCommittedResource(colour surface)"); CountCreate("colour surface");
        s.view = g_r.rtvHeap->GetCPUDescriptorHandleForHeapStart();
        s.view.ptr += SIZE_T(index) * g_r.device->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);
        C().list->CreateRenderTargetView(g_r.device, s.resource.Get(), s.view); // in order with the recorded commands
    }
    Log("renderer: EDRAM %s surface at base %03X", depth ? "depth" : "colour", base);
    return s;
}

// Binds the surfaces for the current RB_COLOR_INFO / RB_DEPTH_INFO bases
// (the list must be open).
FrameResources& Frame();

void PassMark(const char* fmt, uint32_t a, uint32_t b)
{
    if (!g_gpuPasses || !g_passQueries || !C().list)
        return;
    const UINT frameIndex = UINT(&Frame() - g_r.frames);
    auto& marks = g_passMarks[frameIndex];
    if (marks.size() >= kPassMarks)
        return;
    if (!marks.empty())
        marks.back().draws = g_passDraws;
    g_passDraws = 0;
    PassMarkInfo m{};
    snprintf(m.label, sizeof(m.label), fmt, a, b);
    C().list->EndQuery(g_passQueries.Get(), D3D12_QUERY_TYPE_TIMESTAMP, frameIndex * kPassMarks + UINT(marks.size()));
    marks.push_back(m);
}

void SelectSurfaces(bool force)
{
    // A colour surface is keyed by its EDRAM layout: its base and its row width in samples (pitch
    // times 2 for a 4x view, see LayoutKey). A narrower 1x surface at the same base lays its tiles
    // out differently and is another surface; a 4x view of half the pitch covers the same samples
    // and is the same one (FH1's photo mode reaches its 1280x720 1x scene through a 4x 640x360
    // view; keyed by pitch it missed the scene, and the scene stayed hazy). REGPU_SURFACE_BY_BASE=1:
    // by base alone; REGPU_SURFACE_BY_PITCH=1: by base and pitch (A/B).
    static LiveSwitch byBase("REGPU_SURFACE_BY_BASE"), byPitch("REGPU_SURFACE_BY_PITCH");
    const uint32_t surfaceInfo = gpureg::Get(RB_SURFACE_INFO);
    const uint32_t rowSamples = (surfaceInfo & 0x3FFF) * (byPitch ? 1u : MsaaAcross((surfaceInfo >> 16) & 3));
    const uint32_t colorBase = (gpureg::Get(RB_COLOR_INFO) & 0xFFF) | (byBase ? 0u : rowSamples << 12),
                   depthBase = gpureg::Get(RB_DEPTH_INFO) & 0xFFF;
    if (!force && colorBase == C().colorBase && depthBase == C().depthBase)
        return;
    if (C().colorBase != ~0u && !C().worker) // (workers leave surfaces as render targets)
        if (auto it = g_r.colorSurfaces.find(C().colorBase); it != g_r.colorSurfaces.end()) // (not a new empty one)
            it->second.state = C().colorState;
    Renderer::Surface& c = GetSurface(false, colorBase);
    Renderer::Surface& z = GetSurface(true, depthBase);
    C().color = c.resource;
    C().colorState = c.state;
    C().rtv = c.view;
    C().depth = z.resource;
    C().dsv = z.view;
    C().colorBase = colorBase;
    C().depthBase = depthBase;
    PassMark("pass colour %03X depth %03X", colorBase, depthBase);
    TransitionColor(D3D12_RESOURCE_STATE_RENDER_TARGET);
    if (g_zeroedEdramBase == (colorBase & 0xFFF)) // a depth clear zeroed these tiles (see the resolve)
    {
        g_zeroedEdramBase = ~0u;
        const float zero[4]{};
        C().list->ClearRenderTargetView(C().rtv, zero, 0, nullptr);
    }
    C().list->OMSetRenderTargets(1, &C().rtv, FALSE, &C().dsv);
    g_drawState.motionTargets = false;
}

// Bindless texture descriptors: one per (resource, format, dimension), kept
// while the texture is in use. Entries unused for a few frames (replaced or
// dropped textures) go back to the free list; their resources are released
// once the GPU is done with the frame that last used them.
UINT BindlessIndexLocked(ID3D12Resource* resource, DXGI_FORMAT format, uint32_t kind);
// The per-draw functions' switches, at namespace scope: as function-local statics each one checked
// its initialisation guard on every draw (dozens a draw).
LiveSwitch g_sw_REGPU_ALL_CONSTANTS("REGPU_ALL_CONSTANTS");
LiveSwitch g_sw_REGPU_ASYNC_SHADERS("REGPU_ASYNC_SHADERS");
LiveSwitch g_sw_REGPU_CONSTANTS_BY_COUNT("REGPU_CONSTANTS_BY_COUNT");
LiveSwitch g_sw_REGPU_CONSTANTS_IN_RING("REGPU_CONSTANTS_IN_RING");
LiveSwitch g_sw_REGPU_DEPTH_ALPHATEST_ALL("REGPU_DEPTH_ALPHATEST_ALL");
LiveSwitch g_sw_REGPU_DEPTH_ALPHATEST_EMPTY("REGPU_DEPTH_ALPHATEST_EMPTY");
LiveSwitch g_sw_REGPU_DEPTH_ALPHATEST_VS("REGPU_DEPTH_ALPHATEST_VS");
LiveSwitch g_sw_REGPU_DUMP_TEXTURES("REGPU_DUMP_TEXTURES");
LiveSwitch g_sw_REGPU_FULL_CONSTANT_BLOCKS("REGPU_FULL_CONSTANT_BLOCKS");
LiveSwitch g_sw_REGPU_NO_DEPTH_ALPHATEST("REGPU_NO_DEPTH_ALPHATEST");
LiveSwitch g_sw_REGPU_NO_DIRECT_FETCH("REGPU_NO_DIRECT_FETCH");
LiveSwitch g_sw_REGPU_NO_DIRECT_INDEX("REGPU_NO_DIRECT_INDEX");
LiveSwitch g_sw_REGPU_NO_FETCH_BOUNDS("REGPU_NO_FETCH_BOUNDS");
LiveSwitch g_sw_REGPU_NO_GPU_SWIZZLE("REGPU_NO_GPU_SWIZZLE");
LiveSwitch g_sw_REGPU_NO_HUD_SQUEEZE("REGPU_NO_HUD_SQUEEZE");
LiveSwitch g_sw_REGPU_NO_INDEXED("REGPU_NO_INDEXED");
LiveSwitch g_sw_REGPU_NO_STRIP_CUT("REGPU_NO_STRIP_CUT");
LiveSwitch g_sw_REGPU_NO_KILL_SKIP("REGPU_NO_KILL_SKIP");
LiveSwitch g_sw_REGPU_NO_LIVE_CUBE("REGPU_NO_LIVE_CUBE");
LiveSwitch g_sw_REGPU_NO_MIPS("REGPU_NO_MIPS");
LiveSwitch g_sw_REGPU_NO_MSAA_SCALE("REGPU_NO_MSAA_SCALE");
LiveSwitch g_sw_REGPU_NO_NATIVE_BC("REGPU_NO_NATIVE_BC");
LiveSwitch g_sw_REGPU_NO_RECT_SNAP("REGPU_NO_RECT_SNAP");
LiveSwitch g_sw_REGPU_NO_RENDER("REGPU_NO_RENDER");
LiveSwitch g_sw_REGPU_NO_SHADER_SCAN("REGPU_NO_SHADER_SCAN");
LiveSwitch g_sw_REGPU_NO_UNORM_CLAMP("REGPU_NO_UNORM_CLAMP");
LiveSwitch g_sw_REGPU_OLD_CLAMP("REGPU_OLD_CLAMP");
LiveSwitch g_sw_REGPU_OLD_CLEAR_SHIFT("REGPU_OLD_CLEAR_SHIFT");
LiveSwitch g_sw_REGPU_TEXTURE_CHECK_EVERY4("REGPU_TEXTURE_CHECK_EVERY4");
LiveSwitch g_sw_REGPU_TEXTURE_CHECK_STAGGER("REGPU_TEXTURE_CHECK_STAGGER");
LiveSwitch g_sw_REGPU_TRACE_CONSTANTS_USED("REGPU_TRACE_CONSTANTS_USED");
LiveSwitch g_sw_REGPU_TRACE_MIDSIZE("REGPU_TRACE_MIDSIZE");
LiveSwitch g_sw_REGPU_TRACE_TEXCHANGE("REGPU_TRACE_TEXCHANGE");
LiveSwitch g_sw_REGPU_TRACE_TILES("REGPU_TRACE_TILES");
LiveSwitch g_sw_REGPU_TRACE_TILE_WIDEN("REGPU_TRACE_TILE_WIDEN");

// Each thread remembers this frame's descriptors (eviction only takes ones unused for frames).
// kind: 0 2D, 1 cube, 2 3D.
UINT BindlessIndex(ID3D12Resource* resource, DXGI_FORMAT format, uint32_t kind)
{
    const uint64_t key = uint64_t(reinterpret_cast<uintptr_t>(resource)) << 8 ^ uint64_t(format) << 2 ^ uint64_t(kind);
    thread_local FrameMap<UINT> known;
    static SharedFrameMap<UINT> shared;
    if (const UINT* index = known.Find(key, g_r.frame))
        return *index;
    if (UINT index; C().worker && shared.Find(key, g_r.frame, index))
    {
        known.Put(key, g_r.frame, index);
        return index;
    }
    std::lock_guard textureLock(g_textureMutex);
    const UINT index = BindlessIndexLocked(resource, format, kind);
    known.Put(key, g_r.frame, index);
    if (RendererDrawWorkers())
        shared.Put(key, g_r.frame, index);
    return index;
}

UINT BindlessIndexLocked(ID3D12Resource* resource, DXGI_FORMAT format, uint32_t kind)
{
    const uint64_t key = uint64_t(reinterpret_cast<uintptr_t>(resource)) << 8 ^ uint64_t(format) << 2 ^ uint64_t(kind);
    auto it = g_r.bindless.find(key);
    if (it != g_r.bindless.end())
    {
        it->second.lastFrame = g_r.frame;
        return it->second.index;
    }
    UINT index;
    if (!g_r.bindlessFree.empty())
    {
        index = g_r.bindlessFree.back();
        g_r.bindlessFree.pop_back();
    }
    else if (g_r.bindlessNext < kBindless)
        index = g_r.bindlessNext++;
    else
        return kind; // full: draw with a null texture
    D3D12_SHADER_RESOURCE_VIEW_DESC srv{};
    srv.Format = format;
    srv.Shader4ComponentMapping = format == DXGI_FORMAT_BC4_UNORM
        ? D3D12_ENCODE_SHADER_4_COMPONENT_MAPPING(0, 0, 0, 0) // DXT5A: its one channel in all four
        : D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    if (kind == 1)
    {
        srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURECUBE;
        srv.TextureCube.MipLevels = UINT(-1);
    }
    else if (kind == 2)
    {
        srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE3D;
        srv.Texture3D.MipLevels = UINT(-1);
    }
    else
    {
        srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
        srv.Texture2D.MipLevels = UINT(-1); // all levels (resolved mip chains)
    }
    g_r.device->CreateShaderResourceView(resource, &srv, Cpu(g_r.srvHeap.Get(), kBindlessBase + index, g_r.srvStride));
    g_r.bindless[key] = { index, resource, g_r.frame };
    return index;
}

void EvictBindless()
{
    if (g_r.frame % 16 != 0)
        return;
    for (auto it = g_r.bindless.begin(); it != g_r.bindless.end();)
    {
        if (it->second.lastFrame + kFrames + 2 < g_r.frame)
        {
            Frame().garbage.push_back(std::move(it->second.resource));
            g_r.bindlessFree.push_back(it->second.index);
            it = g_r.bindless.erase(it);
        }
        else
            ++it;
    }
}

// Waits for the queue fence to reach a value, on an event of the calling thread: one shared event let two waiting
// threads steal each other's wake-up, and the loser sat out its whole timeout (FM4 port: 2 s stalls in a race).
void WaitFenceValue(UINT64 value, DWORD timeoutMs)
{
    thread_local HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (g_r.fence->GetCompletedValue() >= value)
        return;
    g_r.fence->SetEventOnCompletion(value, event);
    if (WaitForSingleObject(event, timeoutMs) == WAIT_TIMEOUT && timeoutMs != INFINITE)
        Log("perf: a fence wait timed out after %lu ms (value %llu, completed %llu)", timeoutMs, (unsigned long long)value,
            (unsigned long long)g_r.fence->GetCompletedValue());
}

void BeginFrameIfNeeded()
{
    if (g_r.listOpen)
        return;
    FrameResources& f = Frame();
    if (g_r.fence->GetCompletedValue() < f.fence)
    {
        const auto w0 = std::chrono::steady_clock::now();
        WaitFenceValue(f.fence, INFINITE);
        g_gpuWaitSeconds += std::chrono::duration<double>(std::chrono::steady_clock::now() - w0).count();
    }
    f.garbage.clear();
    ApplyOutputSize(); // (a new resolution: between frames)
    void FinishDeferredWritebacks(UINT frameIndex);
    FinishDeferredWritebacks(UINT(&f - g_r.frames)); // this slot's previous frame has completed
    static LiveSwitch perf("REGPU_PERF");
    const UINT frameIndex = UINT(&f - g_r.frames);
    if (!g_occlusionPending[frameIndex].empty())
    {
        // This slot's previous frame has completed: keep its occlusion results.
        uint64_t* counts = nullptr;
        const D3D12_RANGE range{ size_t(frameIndex) * kOcclusionPerFrame * 8, size_t(frameIndex + 1) * kOcclusionPerFrame * 8 };
        if (SUCCEEDED(g_occlusionReadback->Map(0, &range, reinterpret_cast<void**>(&counts))))
        {
            for (const OcclusionParts& q : g_occlusionPending[frameIndex])
            {
                if (g_occlusionResult.size() <= q.order)
                {
                    g_occlusionResult.resize(q.order + 1, -1);
                    g_occlusionCoverage.resize(q.order + 1, -1);
                }
                int64_t passed = 0, covered = 0;
                for (UINT i : q.main)
                    passed += int64_t(counts[i]);
                for (UINT i : q.coverage)
                    covered += int64_t(counts[i]);
                g_occlusionResult[q.order] = passed;
                g_occlusionCoverage[q.order] = q.coverage.empty() ? -1 : covered;
                g_occlusionByKind[{ q.kind, q.kindOrder }] = { passed, q.coverage.empty() ? -1 : covered };
            }
            const D3D12_RANGE none{ 0, 0 };
            g_occlusionReadback->Unmap(0, &none);
        }
        g_occlusionPending[frameIndex].clear();
    }
    if (perf && !g_timestamps)
    {
        D3D12_QUERY_HEAP_DESC qd{};
        qd.Type = D3D12_QUERY_HEAP_TYPE_TIMESTAMP;
        qd.Count = 2 * kFrames;
        g_r.device->CreateQueryHeap(&qd, IID_PPV_ARGS(&g_timestamps));
        g_timestampReadback = CreateBuffer(8 * 2 * kFrames, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    }
    if (g_gpuPasses && !g_passQueries)
    {
        D3D12_QUERY_HEAP_DESC qd{};
        qd.Type = D3D12_QUERY_HEAP_TYPE_TIMESTAMP;
        qd.Count = kPassMarks * kFrames;
        g_r.device->CreateQueryHeap(&qd, IID_PPV_ARGS(&g_passQueries));
        g_passReadback = CreateBuffer(8ull * kPassMarks * kFrames, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    }
    if (g_gpuPasses && g_passMarks[frameIndex].size() > 1)
    {
        // This slot's previous frame has completed: add up its passes.
        auto& marks = g_passMarks[frameIndex];
        uint64_t* t = nullptr;
        const D3D12_RANGE range{ size_t(frameIndex) * kPassMarks * 8, size_t(frameIndex + 1) * kPassMarks * 8 };
        if (SUCCEEDED(g_passReadback->Map(0, &range, reinterpret_cast<void**>(&t))))
        {
            UINT64 frequency = 1;
            g_r.queue->GetTimestampFrequency(&frequency);
            const uint64_t* ts = t + size_t(frameIndex) * kPassMarks;
            for (size_t i = 0; i + 1 < marks.size(); i++)
            {
                if (ts[i + 1] < ts[i])
                    continue; // marks from lists that ran out of order
                auto& total = g_passTotals[marks[i].label];
                total.first += double(ts[i + 1] - ts[i]) * 1000.0 / double(frequency);
                total.second += marks[i].draws;
            }
            const D3D12_RANGE none{ 0, 0 };
            g_passReadback->Unmap(0, &none);
            g_passFrames++;
        }
        marks.clear();
        static auto lastLog = std::chrono::steady_clock::now();
        if (std::chrono::steady_clock::now() - lastLog > std::chrono::seconds(2) && g_passFrames)
        {
            lastLog = std::chrono::steady_clock::now();
            std::vector<std::pair<double, std::string>> top;
            double sum = 0;
            for (auto& [label, total] : g_passTotals)
            {
                top.push_back({ total.first, label });
                sum += total.first;
            }
            std::sort(top.rbegin(), top.rend());
            Log("gpu passes: %.2f ms/frame over %llu frames", sum / g_passFrames, (unsigned long long)g_passFrames);
            for (size_t i = 0; i < top.size() && i < (g_gpuShaders ? 30u : 14u); i++)
                Log("gpu passes: %6.3f ms  %5.0f draws  %s", top[i].first / g_passFrames, double(g_passTotals[top[i].second].second) / g_passFrames,
                    top[i].second.c_str());
            g_passTotals.clear();
            g_passFrames = 0;
        }
    }
    g_passMarks[frameIndex].clear();
    if (perf && f.timed && g_timestampReadback)
    {
        // This slot's previous frame has completed (waited for above).
        uint64_t* t = nullptr;
        const D3D12_RANGE range{ size_t(frameIndex) * 16, size_t(frameIndex) * 16 + 16 };
        if (SUCCEEDED(g_timestampReadback->Map(0, &range, reinterpret_cast<void**>(&t))))
        {
            UINT64 frequency = 1;
            g_r.queue->GetTimestampFrequency(&frequency);
            const double ms = double(t[frameIndex * 2 + 1] - t[frameIndex * 2]) * 1000.0 / double(frequency);
            const D3D12_RANGE none{ 0, 0 };
            g_timestampReadback->Unmap(0, &none);
            if (ms > 0 && ms < 1000)
            {
                g_gpuFrameMs += ms;
                g_gpuFrameMaxMs = std::max(g_gpuFrameMaxMs, ms);
                g_gpuFrames++;
            }
        }
        f.timed = false;
    }
    f.allocator->Reset();
    f.poolUsed = 0;
    g_r.recorder.Rebind(g_r.realList.Get()); // (a pool list may have continued last frame)
    C().list->Reset(f.allocator.Get(), nullptr);
    // This frame's scene phase and scale (see UpscaleScene).
    {
        const Settings& st = GetSettings();
        const bool temporalUpscaler = st.upscaler == Settings::Upscaler::Dlss || st.upscaler == Settings::Upscaler::Fsr3;
        const float scale = st.SceneScale();
        g_upscale.switchedThisFrame = false;
        g_scenePhase = st.upscaler != Settings::Upscaler::Off && (temporalUpscaler || scale < 1.0f) && g_r.frame >= g_upscale.retryFrame &&
            (!temporalUpscaler || TemporalInit());
        g_temporal = g_scenePhase && temporalUpscaler;
        g_sceneScale = g_scenePhase ? scale : 1.0f;
        if (g_temporal)
            BeginTemporalFrame();
    }
    PassMark("frame start %u%u", 0, 0);
    if (perf && g_timestamps)
        C().list->EndQuery(g_timestamps.Get(), D3D12_QUERY_TYPE_TIMESTAMP, frameIndex * 2);
    if (const UINT64 used = f.ringUsed.load(); used > g_ringPeak)
    {
        g_ringPeak = used;
        if (used > f.ringSize / 2)
            Log("renderer: upload ring peak %llu MB of %llu", (unsigned long long)(used >> 20), (unsigned long long)(f.ringSize >> 20));
    }
    if (const UINT64 want = g_ringWant.load(); want > f.ringSize)
    {
        f.garbage.push_back(f.ring); // the old ring may still be referenced by views until this frame's end
        f.ring = CreateBuffer(want, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
        f.ring->Map(0, nullptr, reinterpret_cast<void**>(&f.ringCpu));
        f.ringSize = want;
        Log("renderer: upload ring grown to %llu MB", (unsigned long long)(want >> 20));
    }
    f.ringUsed = 0;
    f.uploads.clear();
    // Guest memory not mapped at init: try again a few times (see MapGuestMemory).
    if (!g_r.physical && (g_r.frame == 30 || g_r.frame == 120 || g_r.frame == 600 || g_r.frame == 2400) && !getenv("REGPU_NO_MAP_RETRY"))
        MapGuestMemory(g_r.device);
    // Per-frame table: t0 this frame's ring, t1 guest physical memory.
    {
        D3D12_SHADER_RESOURCE_VIEW_DESC raw{};
        raw.Format = DXGI_FORMAT_R32_TYPELESS;
        raw.ViewDimension = D3D12_SRV_DIMENSION_BUFFER;
        raw.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
        raw.Buffer.NumElements = UINT(f.ringSize / 4);
        raw.Buffer.Flags = D3D12_BUFFER_SRV_FLAG_RAW;
        g_r.device->CreateShaderResourceView(f.ring.Get(), &raw, Cpu(g_r.srvHeap.Get(), f.descriptorBase, g_r.srvStride));
        if (g_r.physical)
        {
            raw.Buffer.NumElements = UINT(g_r.physical->GetDesc().Width / 4);
            g_r.device->CreateShaderResourceView(g_r.physical.Get(), &raw, Cpu(g_r.srvHeap.Get(), f.descriptorBase + 1, g_r.srvStride));
        }
        else
            g_r.device->CreateShaderResourceView(f.ring.Get(), &raw, Cpu(g_r.srvHeap.Get(), f.descriptorBase + 1, g_r.srvStride));
    }
    f.descriptorsUsed = 2;
    EvictBindless();
    ReclaimTextureBlocks();
    ID3D12DescriptorHeap* heaps[] = { g_r.srvHeap.Get(), g_r.samplerHeap.Get() };
    C().list->SetDescriptorHeaps(g_r.samplerHeap ? 2 : 1, heaps);
    C().list->SetGraphicsRootSignature(g_r.rootSignature.Get());
    g_drawState.Invalidate();
    SelectSurfaces(true);
    C().list->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    g_r.listOpen = true;
}

// Allocates from the frame's upload ring; returns the offset (or ~0).
UINT64 RingAlloc(UINT64 bytes, UINT64 alignment = 256)
{
    FrameResources& f = Frame();
    UINT64 used = f.ringUsed.load(std::memory_order_relaxed), offset;
    for (;;)
    {
        offset = (used + alignment - 1) & ~(alignment - 1);
        if (offset + bytes > f.ringSize || f.ringUsed.compare_exchange_weak(used, offset + bytes, std::memory_order_relaxed))
            break;
    }
    if (offset + bytes > f.ringSize)
    {
        for (UINT64 want = g_ringWant.load(), grown = std::min(std::max(f.ringSize * 2, offset + bytes), kRingMax);
             want < grown && !g_ringWant.compare_exchange_weak(want, grown);)
            ;
        static UINT64 lastFull = 0;
        static int fullLogs = 0;
        if ((RendererTracing() || fullLogs < 20) && lastFull != g_r.frame && ++fullLogs)
        {
            lastFull = g_r.frame;
            Log("ring full: %llu bytes requested, %llu used, %llu uploads cached", (unsigned long long)bytes, (unsigned long long)f.ringUsed.load(), (unsigned long long)f.uploads.size());
        }
        return ~0ull;
    }
    return offset;
}

// A debug variable the debug server's "env" can set live, read once per frame: getenv on every draw
// (REGPU_SKIP_SHADERS, REGPU_POLY_OFFSET_SCALE) cost FH1 half its frame rate on this PC.
struct FrameEnv
{
    const char* name;
    std::string value;
    uint64_t frame = ~0ull;
    bool set = false;
    const char* Get()
    {
        if (frame != g_r.frame)
        {
            frame = g_r.frame;
            const char* v = getenv(name);
            set = v && *v;
            if (set)
                value = v;
        }
        return set ? value.c_str() : nullptr;
    }
};

// A large vertex buffer the title rewrites from frame to frame (PGR4's crowd bone palettes):
// read in place, the title's next frame can overwrite it before the host GPU runs this one
// (its fences are signalled when recorded), so it is copied while it keeps changing. Checked
// once per frame with the sampled hash. REGPU_DIRECT_DYNAMIC keeps them in place (A/B).
bool DynamicBuffer(uint32_t address, uint32_t bytes)
{
    static LiveSwitch keepDirect("REGPU_DIRECT_DYNAMIC");
    if (keepDirect)
        return false;
    struct Seen
    {
        uint64_t hash = 0, frame = ~0ull, dynamicUntil = 0;
    };
    // The answer holds for the rest of the frame once a buffer is checked in it: each thread keeps this
    // frame's answers (a map lookup and a lock per vertex fetch per draw otherwise).
    const uint64_t key = uint64_t(address) << 32 | bytes;
    thread_local FrameMap<bool> answered;
    if (const bool* a = answered.Find(key, g_r.frame))
        return *a;
    static std::unordered_map<uint64_t, Seen> seen;
    static std::mutex mutex; // the draw workers (REGPU_DRAW_WORKERS) check buffers too
    std::lock_guard lock(mutex);
    Seen& s = seen[key];
    // Hashing is the cost: a buffer that stopped changing (static geometry, most of them) is
    // checked every 16th frame, one that changes every frame.
    const bool due = s.frame == ~0ull || g_r.frame <= s.dynamicUntil || g_r.frame >= s.frame + 16;
    if (s.frame != g_r.frame && due)
    {
        const uint64_t hash = ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(address)), bytes);
        // A buffer seen changing once stays copied: one written in bursts (FH1's skid marks, appended
        // while the car slides) went back to being read in place 120 frames after its last change and
        // checked every 16th frame, so the next burst was read while the title wrote it (rare exploded
        // vertices on the marks). REGPU_DYNAMIC_EXPIRE=1: back to in place after 120 quiet frames, as before.
        static LiveSwitch expire("REGPU_DYNAMIC_EXPIRE");
        if (s.frame != ~0ull && hash != s.hash)
            s.dynamicUntil = expire ? g_r.frame + 120 : ~0ull;
        // A buffer seen for the first time is copied until it has stayed the same for 16 frames: with
        // early fences the title may rewrite it before the host GPU reads it, and ring-style buffers (FH1's
        // minimap blocks, skid marks, particles) keep appearing at new addresses and sizes, so read in place
        // at first sight they exploded when the GPU ran behind (busy scenes). REGPU_NEW_BUFFERS_IN_PLACE=1:
        // read in place until seen changing, as before.
        static LiveSwitch newInPlace("REGPU_NEW_BUFFERS_IN_PLACE");
        if (s.frame == ~0ull && !newInPlace)
            s.dynamicUntil = g_r.frame + 16;
        s.hash = hash;
        s.frame = g_r.frame;
        // REGPU_TRACE_DYNAMIC=1: the buffers being copied, every 600 frames.
        static LiveSwitch trace("REGPU_TRACE_DYNAMIC");
        static uint64_t lastLog = 0;
        if (trace && g_r.frame >= lastLog + 600)
        {
            lastLog = g_r.frame;
            uint32_t n = 0, total = 0;
            uint64_t bytesTotal = 0;
            for (const auto& [key, e] : seen)
            {
                total++;
                if (g_r.frame <= e.dynamicUntil)
                {
                    n++;
                    bytesTotal += uint32_t(key);
                    if (n <= 12)
                        Log("dynamic: buffer %08X %u bytes", uint32_t(key >> 32), uint32_t(key));
                }
            }
            Log("dynamic: %u of %u large buffers copied, %llu KB", n, total, (unsigned long long)(bytesTotal >> 10));
        }
    }
    const bool dynamic = g_r.frame <= s.dynamicUntil;
    if (s.frame == g_r.frame)
        answered.Put(key, g_r.frame, dynamic);
    return dynamic;
}

// Whether guest memory can be read. A XenonRecomp port commits all of it; a host that protects
// unallocated guest pages (ReXGlue: no access) faults on a fetch that runs past an allocation,
// which the GPU reading the same memory in place would see as zeros. (Its fault handler runs
// before any of ours, so the range is checked first.) VirtualQuery is slow on a large mapped view
// (it walks the view to find the region: answered per page and frame it cost FH1 100 ms a frame),
// so answers are kept per 64 KB page: a readable page for 240 frames (staggered), one that is not
// for 30. Protection the host changes in between is not seen until then.
bool GuestReadable(const uint8_t* p, size_t bytes)
{
    struct Page
    {
        uint64_t until;
        bool ok;
    };
    thread_local std::unordered_map<uintptr_t, Page> pages;
    const uintptr_t first = uintptr_t(p) >> 16, last = (uintptr_t(p) + std::max<size_t>(bytes, 1) - 1) >> 16;
    for (uintptr_t page = first; page <= last; page++)
    {
        auto it = pages.find(page);
        if (it == pages.end() || it->second.until <= g_r.frame)
        {
            MEMORY_BASIC_INFORMATION mbi{};
            const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE;
            const void* at = reinterpret_cast<const void*>(page << 16);
            bool ok = VirtualQuery(at, &mbi, sizeof(mbi)) && mbi.State == MEM_COMMIT && (mbi.Protect & readable) && !(mbi.Protect & PAGE_GUARD);
            // The region may end inside the page: a run of readable regions until the page's end.
            for (uintptr_t next = uintptr_t(mbi.BaseAddress) + mbi.RegionSize; ok && next < ((page + 1) << 16);
                 next = uintptr_t(mbi.BaseAddress) + mbi.RegionSize)
                ok = VirtualQuery(reinterpret_cast<const void*>(next), &mbi, sizeof(mbi)) && mbi.State == MEM_COMMIT &&
                     (mbi.Protect & readable) && !(mbi.Protect & PAGE_GUARD);
            it = pages.insert_or_assign(page, Page{ g_r.frame + (ok ? 240 + (page * 2654435761u) % 64 : 30), ok }).first;
        }
        if (!it->second.ok)
            return false;
    }
    return true;
}

// REGPU_PERF: UploadGuest's bytes hashed and copied (logged with the frame stats).
struct UploadStats
{
    std::atomic<uint64_t> hashed{ 0 }, copied{ 0 }, hits{ 0 }, copies{ 0 }, sampledHits{ 0 };
} g_uploadStats;

// Copies a guest range into the ring once per frame; draws sharing a vertex
// buffer reuse the copy. A sampled hash catches ranges rewritten mid-frame.
UINT64 UploadGuest(uint32_t guest, uint32_t bytes)
{
    FrameResources& f = Frame();
    const uint8_t* src = GuestPtr<uint8_t>(guest);
    // A range that isn't all readable guest memory (see GuestReadable) is uploaded as its readable
    // start and zeros past it. A title's buffer can run past its allocation into a page the host
    // never committed (FH1's minimap geometry ring: its last 48 KB block ends 4 KB past it); zeroing
    // the whole range dropped what that block drew (the route and the player arrow, every few
    // frames). Not cached: rare, and the hash would read past the readable part.
    // REGPU_UPLOAD_ZERO_UNREADABLE=1: all zeros, as before.
    auto zeros = [&] {
        static LiveSwitch allZeros("REGPU_UPLOAD_ZERO_UNREADABLE");
        size_t readable = 0;
        while (!allZeros && readable < bytes)
        {
            MEMORY_BASIC_INFORMATION mbi{};
            const DWORD ok = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE;
            if (!VirtualQuery(src + readable, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT || !(mbi.Protect & ok) || (mbi.Protect & PAGE_GUARD))
                break;
            readable = std::min<size_t>(bytes, uintptr_t(mbi.BaseAddress) + mbi.RegionSize - uintptr_t(src));
        }
        static std::atomic<uint32_t> logged{ 0 };
        if (logged++ < 8)
            Log("upload: %08X (%u bytes) is not all readable guest memory: %zu bytes copied, zeros after", guest, bytes, readable);
        const UINT64 offset = RingAlloc(std::max<uint32_t>(bytes, 16) + 16, 16);
        if (offset != ~0ull)
        {
            memcpy(f.ringCpu + offset, src, readable);
            memset(f.ringCpu + offset + readable, 0, bytes - readable);
        }
        return offset;
    };
    // Small buffers are often rewritten between draws (UI quads), so hash all
    // of them; large ones (static geometry) are sampled.
    uint64_t sample = bytes;
    static const uint32_t fullHashBytes = getenv("REGPU_UPLOAD_FULL_HASH_KB") ? uint32_t(atoi(getenv("REGPU_UPLOAD_FULL_HASH_KB"))) * 1024u : 16384u;
    // (Only on the copy path: with the zero-copy heap the large ranges, the ones that run past
    // allocations, are read in place by the GPU, and the check would cost every upload.)
    if (!g_r.physical && !GuestReadable(src, bytes))
        return zeros();
    // Large ranges reach this copy path, with guest memory mapped for the GPU, only when they were seen
    // changing (DynamicBuffer): hash them all, not 16 samples. Sampled, a range rewritten between two
    // draws of a frame (FH1's skid marks once they pass 16 KB: segments rewritten as the car slides)
    // kept the first draw's copy, and later draws put marks at old positions. REGPU_UPLOAD_SAMPLED=1:
    // 16 samples, as before. (Without the mapping every large buffer is copied: sampled there.)
    static LiveSwitch uploadSampled("REGPU_UPLOAD_SAMPLED");
    const uint64_t key = uint64_t(guest) << 32 | bytes;
    // Each recording thread keeps its own cache (cleared when the frame changes).
    thread_local FrameMap<std::pair<UINT64, uint64_t>> uploads;
    // A range never seen changing, hashed for 600 frames, is checked in full at its first use in a frame
    // and later in the frame by the sampled hash. FH1 free roam reuses one 32 KB vertex range 352 times a
    // frame; hashed in full each time, the uploads were 8% of the frame time on a paused frame. A range
    // seen changing once (a sampled check that finds a change counts) is checked in full at every use
    // for good, as the skid marks need: so only a range's first change ever, made between two draws of a
    // frame where the samples miss it, could be read stale, and only for the rest of that frame (a
    // skid mark buffer idle since the load, at the first slide). Opt-in for that:
    // REGPU_UPLOAD_STABLE_REUSE=1 (FH1: +5% on a paused frame, +1-2% driving, perf timing on).
    struct Stable
    {
        uint64_t full = 0, sampled = 0, since = 0, sampledFrame = ~0ull;
        bool changed = false;
    };
    thread_local std::unordered_map<uint64_t, Stable> stable;
    static LiveSwitch stableReuse("REGPU_UPLOAD_STABLE_REUSE");
    const bool trackStable = stableReuse && !uploadSampled;
    const auto* known = uploads.Find(key, g_r.frame);
    if (known && trackStable)
        if (auto it = stable.find(key); it != stable.end() && it->second.sampledFrame == g_r.frame && !it->second.changed &&
            g_r.frame >= it->second.since + 600)
        {
            if (ContentHash(src, bytes) == it->second.sampled)
            {
                if (g_perfTiming) g_uploadStats.hits++;
                if (g_perfTiming) g_uploadStats.sampledHits++;
                return known->first;
            }
            it->second.changed = true;
        }
    if (bytes > fullHashBytes && g_r.physical && !uploadSampled)
        sample = FullHash(src, bytes);
    else if (bytes <= fullHashBytes)
        sample = uploadSampled ? ContentHash(src, bytes) : FullHash(src, bytes); // (ContentHash's default stride is 256 bytes)
    else
        for (uint32_t i = 0; i < 16 && bytes >= 8; i++)
        {
            uint64_t v;
            memcpy(&v, src + (uint64_t(bytes - 8) * i / 15), 8);
            sample = (sample ^ v) * 0x100000001B3ull;
        }
    if (trackStable)
    {
        // Ring-style ranges keep appearing at new addresses: past 64K ranges, those never seen changing
        // are dropped (and start their 600 frames again); the ones that changed stay marked.
        if (stable.size() > (1u << 16))
            std::erase_if(stable, [](const auto& e) { return !e.second.changed; });
        Stable& s = stable[key];
        if (!s.since)
            s.since = g_r.frame;
        else if (s.full != sample)
            s.changed = true;
        s.full = sample;
        if (s.sampledFrame != g_r.frame || !known || known->second != sample)
        {
            s.sampled = ContentHash(src, bytes);
            s.sampledFrame = g_r.frame;
        }
    }
    if (g_perfTiming) g_uploadStats.hashed += bytes;
    // REGPU_TRACE_UPLOADS=1: the ranges hashed most (bytes and uses a frame), every 240 frames. FH1 free
    // roam: one 32 KB vertex range used by 352 draws a frame is 40% of the ~26 MB.
    if (static const bool traceUploads = getenv("REGPU_TRACE_UPLOADS") != nullptr; traceUploads)
    {
        static std::mutex m;
        static std::unordered_map<uint64_t, std::pair<uint64_t, uint64_t>> byKey; // hashed bytes, uses
        static uint64_t lastFrame = 0;
        std::lock_guard lock(m);
        auto& e = byKey[key];
        e.first += bytes;
        e.second++;
        if (g_r.frame >= lastFrame + 240)
        {
            std::vector<std::pair<uint64_t, uint64_t>> v;
            for (auto& [k, x] : byKey)
                v.push_back({ x.first, k });
            std::sort(v.rbegin(), v.rend());
            for (size_t i = 0; i < v.size() && i < 10; i++)
                Log("uploads: %08X %u bytes: %.2f MB hashed a frame, %.1f uses a frame", uint32_t(v[i].second >> 32), uint32_t(v[i].second),
                    v[i].first / 240.0 / 1048576.0, byKey[v[i].second].second / 240.0);
            byKey.clear();
            lastFrame = g_r.frame;
        }
    }
    if (known && known->second == sample)
    {
        if (g_perfTiming) g_uploadStats.hits++;
        return known->first;
    }
    if (g_perfTiming) g_uploadStats.copies++;
    if (g_perfTiming) g_uploadStats.copied += bytes;
    if (RendererTracing() && bytes > (16u << 10))
        Log("upload: %08X %u bytes (ring %llu)", guest, bytes, (unsigned long long)f.ringUsed.load());
    const UINT64 offset = RingAlloc(std::max<uint32_t>(bytes, 16) + 16, 16);
    if (offset == ~0ull)
        return offset;
    memcpy(f.ringCpu + offset, src, bytes);
    uploads.Put(key, g_r.frame, { offset, sample });
    return offset;
}

// Index buffers for indexed draws: the guest's big-endian indices, swapped once into an
// arena on the GPU and checked for changes once per frame (sampled, like textures). Real
// indexed draws let the GPU reuse transformed vertices; the vertex shader then gets the
// index as its vertex ID ("auto" index mode, see hlsl.cpp).
struct IndexArena
{
    static constexpr uint64_t kBytes = 128ull << 20;
    ComPtr<ID3D12Resource> buffer;
    uint64_t used = 0;
    std::unordered_map<uint32_t, std::vector<uint64_t>> free; // offsets by size class (log2)
    struct Entry
    {
        uint64_t offset = 0, hash = 0, checkedFrame = ~0ull;
        uint32_t sizeClass = 0;
        D3D12_GPU_VIRTUAL_ADDRESS staged = 0; // this frame's copy in the upload ring (frame stagedFrame)
        uint64_t stagedFrame = ~0ull;
        bool changing = false; // seen changing: checked at every draw, not once a frame
    };
    std::unordered_map<uint64_t, Entry> entries;
    bool full = false;
    // Copies into the arena, made at the end of the frame in one batch (a barrier
    // on the arena mid-frame would drain the GPU each time new geometry streams in).
    struct Copy
    {
        ID3D12Resource* ring;
        uint64_t from, to, bytes;
    };
    std::vector<Copy> pending;
} g_indexArena;

// This frame's index uploads, copied into the arena once its draws are recorded.
void FlushIndexCopies()
{
    IndexArena& arena = g_indexArena;
    if (arena.pending.empty())
        return;
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = arena.buffer.Get();
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_INDEX_BUFFER;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    C().list->ResourceBarrier(1, &b);
    for (const auto& c : arena.pending)
        C().list->CopyBufferRegion(arena.buffer.Get(), c.to, c.ring, c.from, c.bytes);
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    C().list->ResourceBarrier(1, &b);
    arena.pending.clear();
}

// The view of the guest index buffer at physical address `base` (count indices), swapped
// into the arena; false when it cannot be cached (the draw fetches indices itself).
bool GetIndexBufferLocked(uint32_t base, uint32_t count, bool is32, uint32_t endian, D3D12_INDEX_BUFFER_VIEW& view, uint64_t hash);
thread_local bool g_indexChanging = false; // set by GetIndexBufferLocked: the entry is checked at every draw
// Each thread remembers this frame's index buffers (checked once per frame), except ones seen changing.
bool GetIndexBuffer(uint32_t base, uint32_t count, bool is32, uint32_t endian, D3D12_INDEX_BUFFER_VIEW& view)
{
    thread_local FrameMap<D3D12_INDEX_BUFFER_VIEW> known;
    const uint64_t key = uint64_t(base) << 32 ^ uint64_t(count) << 3 ^ uint64_t(is32) << 2 ^ endian;
    static SharedFrameMap<D3D12_INDEX_BUFFER_VIEW> shared;
    if (const auto* v = known.Find(key, g_r.frame))
    {
        view = *v;
        return true;
    }
    if (C().worker && shared.Find(key, g_r.frame, view))
    {
        known.Put(key, g_r.frame, view);
        return true;
    }
    // The indices are hashed before the lock: the draw workers otherwise queued on the texture mutex behind
    // one another's hashing of the index buffers seen changing (checked at every draw).
    const uint32_t bytes = count * (is32 ? 4 : 2);
    if (!bytes || bytes > (16u << 20))
        return false;
    static LiveSwitch sampledIndexHash("REGPU_INDEX_SAMPLED_HASH");
    const uint8_t* src = GuestPtr<uint8_t>(GpuPhysToGuest(base));
    const uint64_t hash = sampledIndexHash ? ContentHash(src, bytes) : FullHash(src, bytes);
    std::lock_guard textureLock(g_textureMutex);
    g_indexChanging = false;
    if (!GetIndexBufferLocked(base, count, is32, endian, view, hash))
        return false;
    if (!g_indexChanging)
    {
        known.Put(key, g_r.frame, view);
        if (RendererDrawWorkers())
            shared.Put(key, g_r.frame, view);
    }
    return true;
}

bool GetIndexBufferLocked(uint32_t base, uint32_t count, bool is32, uint32_t endian, D3D12_INDEX_BUFFER_VIEW& view, uint64_t hash)
{
    IndexArena& arena = g_indexArena;
    const uint32_t bytes = count * (is32 ? 4 : 2);
    if (!bytes || bytes > (16u << 20))
        return false;
    if (arena.full && arena.entries.size())
    {
        // Start over: copies in this frame run after the frames in flight are done.
        arena.entries.clear();
        arena.free.clear();
        arena.used = 0;
        arena.full = false;
    }
    if (!arena.buffer)
    {
        arena.buffer = CreateBuffer(IndexArena::kBytes, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_INDEX_BUFFER);
        if (!arena.buffer)
            return false;
    }
    const uint64_t key = uint64_t(base) << 32 ^ uint64_t(bytes) << 3 ^ uint64_t(is32) << 2 ^ endian;
    auto [it, inserted] = arena.entries.try_emplace(key);
    IndexArena::Entry& e = it->second;
    if (inserted)
    {
        uint32_t sizeClass = 8; // 256 bytes
        while ((1ull << sizeClass) < bytes)
            sizeClass++;
        auto& list = arena.free[sizeClass];
        if (!list.empty())
        {
            e.offset = list.back();
            list.pop_back();
        }
        else if (arena.used + (1ull << sizeClass) <= IndexArena::kBytes)
        {
            e.offset = arena.used;
            arena.used += 1ull << sizeClass;
        }
        else
        {
            arena.entries.erase(it);
            arena.full = true;
            return false;
        }
        e.sizeClass = sizeClass;
    }
    view = { arena.buffer->GetGPUVirtualAddress() + e.offset, bytes, is32 ? DXGI_FORMAT_R32_UINT : DXGI_FORMAT_R16_UINT };
    // An index buffer seen changing is checked at every draw: one rewritten between two draws of a frame
    // (FH1's skid marks, many on screen) otherwise drew later draws with the earlier indices, triangles
    // stretched to far-off vertices. REGPU_INDEX_CHECK_ONCE=1: once a frame for all, as before.
    static LiveSwitch checkOnce("REGPU_INDEX_CHECK_ONCE");
    g_indexChanging = e.changing && !checkOnce;
    if (e.checkedFrame == g_r.frame && (!e.changing || checkOnce))
    {
        if (e.stagedFrame == g_r.frame)
            view.BufferLocation = e.staged;
        return true;
    }
    e.checkedFrame = g_r.frame;
    const uint8_t* src = GuestPtr<uint8_t>(GpuPhysToGuest(base));
    // (hash: every index, not a sample, made by GetIndexBuffer before the lock: a game that streams new
    // geometry into the same address and size can change only words between the samples, and the old
    // indices drew on (FM2 port, report #4: black wedges of track). REGPU_INDEX_SAMPLED_HASH=1 samples
    // every 256 bytes as before (A/B).)
    if (!inserted && hash == e.hash)
    {
        if (e.stagedFrame == g_r.frame)
            view.BufferLocation = e.staged;
        return true;
    }
    if (!inserted)
    {
        e.changing = true;
        g_indexChanging = !checkOnce;
    }
    // Swap into the upload ring (as the vertex shader would read them), then copy.
    const UINT64 offset = RingAlloc((bytes + 15) & ~15u, 16);
    if (offset == ~0ull)
        return false;
    uint8_t* dst = Frame().ringCpu + offset;
    if (is32)
        for (uint32_t i = 0; i < count; i++)
        {
            uint32_t v;
            memcpy(&v, src + i * 4, 4);
            if (endian == 2)
                v = __builtin_bswap32(v);
            else if (endian == 1)
                v = ((v & 0x00FF00FFu) << 8) | ((v >> 8) & 0x00FF00FFu);
            memcpy(dst + i * 4, &v, 4);
        }
    else
        for (uint32_t i = 0; i < count; i++)
        {
            uint16_t v;
            memcpy(&v, src + i * 2, 2);
            if (endian == 1 || endian == 2)
                v = __builtin_bswap16(v);
            memcpy(dst + i * 2, &v, 2);
        }
    // This frame draws from the upload ring; the arena gets the copy at the end of the frame.
    e.staged = Frame().ring->GetGPUVirtualAddress() + offset;
    e.stagedFrame = g_r.frame;
    view.BufferLocation = e.staged;
    arena.pending.push_back({ Frame().ring.Get(), offset, e.offset, bytes });
    e.hash = hash;
    return true;
}

// Compile activity, for the on-screen overlay and REGPU_PERF.
struct CompileStats
{
    std::atomic<int> busy{ 0 };            // jobs (shader compiles, pipeline builds) in flight
    std::atomic<uint32_t> shadersBuilt{ 0 }, shadersCached{ 0 }, pipelinesBuilt{ 0 }, pipelinesLoaded{ 0 };
    std::atomic<uint64_t> lastColdMs{ 0 };  // steady-clock ms of the last real compile
    std::atomic<uint32_t> warmTotal{ 0 }, warmDone{ 0 };
    std::atomic<uint32_t> staticDone{ 0 }, staticTotal{ 0 }; // first boot: static shaders compiled / to compile
    std::mutex mutex;
    std::string current;                    // what the oldest in-flight job is
    std::chrono::steady_clock::time_point currentSince;
    void Begin(const std::string& what)
    {
        std::lock_guard lock(mutex);
        if (busy.load() == 0)
            currentSince = std::chrono::steady_clock::now();
        current = what;
        busy++;
    }
    void End(bool cold)
    {
        busy--;
        if (cold)
            lastColdMs = uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
    }
} g_stats;

// Everything the title has ever bound is recorded (shader microcode as files in
// shadercache/, pipeline states in pipelines.manifest) so later runs rebuild all of
// it in the background at boot instead of stalling the first time a level needs it.
// Cache files written off the command processor thread: opening a file can take
// milliseconds (more with a virus scanner), which showed as frame spikes whenever a level
// met a new pipeline.
struct DiskWriter
{
    std::mutex mutex;
    std::condition_variable cv, idle;
    std::deque<std::function<void()>> queue;
    bool busy = false;

    void Push(std::function<void()> job)
    {
        static std::once_flag started;
        std::call_once(started, [this] {
            std::thread([this] {
                SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
                for (;;)
                {
                    std::function<void()> next;
                    {
                        std::unique_lock lock(mutex);
                        cv.wait(lock, [this] { return !queue.empty(); });
                        next = std::move(queue.front());
                        queue.pop_front();
                        busy = true;
                    }
                    next();
                    std::lock_guard lock(mutex);
                    busy = false;
                    if (queue.empty())
                        idle.notify_all();
                }
            }).detach();
        });
        {
            std::lock_guard lock(mutex);
            queue.push_back(std::move(job));
        }
        cv.notify_one();
    }
    // Waits until everything queued is written (at exit).
    void Flush()
    {
        std::unique_lock lock(mutex);
        idle.wait_for(lock, std::chrono::seconds(5), [this] { return queue.empty() && !busy; });
    }
} g_diskWriter;

// Vertex shaders as the title binds them: the D3D layer patches each shipped vertex shader's fetch
// instructions (data format, stride, offset) for the vertex declaration it is drawn with, so the shaders the
// game draws with never match the shipped ones (Host::staticShaders) and can't be compiled at the first boot.
// A runtime vertex shader is recorded as its shipped shader plus the 3-dword instructions that differ
// ("V shipped runtime group:w0.w1.w2 ..." in pipelines.manifest, uploaded with players' logs); the first boot
// rebuilds and compiles every variant listed in the seed (Host::pipelineSeed). Only hashes and those patched
// words are kept: the code itself comes from the player's own copy of the game.
namespace VertexPatch
{
std::mutex g_mutex;
std::unordered_map<size_t, std::vector<std::pair<uint64_t, std::vector<uint32_t>>>> g_byLength; // shipped vertex shaders
std::unordered_map<uint64_t, std::pair<size_t, size_t>> g_byHash;                               // hash -> length, index

void AddShipped(uint64_t hash, const std::vector<uint32_t>& code)
{
    std::lock_guard lock(g_mutex);
    if (g_byHash.count(hash))
        return;
    auto& list = g_byLength[code.size()];
    g_byHash[hash] = { code.size(), list.size() };
    list.push_back({ hash, code });
}

// The manifest line for a runtime vertex shader, or empty when no shipped shader of its length differs from
// it in a few whole instructions only (the fetches).
std::string Line(uint64_t hash, const std::vector<uint32_t>& code)
{
    std::lock_guard lock(g_mutex);
    const auto it = g_byLength.find(code.size());
    if (it == g_byLength.end())
        return {};
    const std::pair<uint64_t, std::vector<uint32_t>>* best = nullptr;
    std::vector<size_t> bestGroups;
    for (const auto& base : it->second)
    {
        std::vector<size_t> groups;
        for (size_t i = 0; i < code.size() && groups.size() <= 32; i++)
            if (code[i] != base.second[i] && (groups.empty() || groups.back() != i / 3))
                groups.push_back(i / 3);
        if (groups.size() <= 32 && (!best || groups.size() < bestGroups.size()))
        {
            best = &base;
            bestGroups = std::move(groups);
        }
    }
    if (!best || bestGroups.empty())
        return {};
    char buf[64];
    snprintf(buf, sizeof(buf), "V %llx %llx", (unsigned long long)best->first, (unsigned long long)hash);
    std::string line = buf;
    for (size_t g : bestGroups)
    {
        uint32_t w[3] = {};
        for (size_t k = 0; k < 3; k++)
            if (g * 3 + k < code.size())
                w[k] = code[g * 3 + k];
        snprintf(buf, sizeof(buf), " %zx:%x.%x.%x", g, w[0], w[1], w[2]);
        line += buf;
    }
    return line;
}

// A "V" line's runtime shader rebuilt from its shipped one; false if the line doesn't fit (the hash checks it).
bool Rebuild(const char* line, uint64_t& hash, std::vector<uint32_t>& code)
{
    unsigned long long shipped = 0, runtime = 0;
    int used = 0;
    if (sscanf(line, "V %llx %llx%n", &shipped, &runtime, &used) < 2 || !used)
        return false;
    {
        std::lock_guard lock(g_mutex);
        const auto it = g_byHash.find(shipped);
        if (it == g_byHash.end())
            return false;
        code = g_byLength[it->second.first][it->second.second].second;
    }
    for (const char* p = line + used; *p;)
    {
        unsigned long long g;
        unsigned w0, w1, w2;
        int n = 0;
        if (sscanf(p, " %llx:%x.%x.%x%n", &g, &w0, &w1, &w2, &n) < 4 || !n)
            break;
        p += n;
        const uint32_t words[3] = { w0, w1, w2 };
        for (size_t k = 0; k < 3; k++)
            if (g * 3 + k < code.size())
                code[g * 3 + k] = words[k];
    }
    hash = Fnv(code.data(), code.size() * 4);
    return hash == runtime;
}
}

struct Manifest
{
    std::mutex mutex;
    std::unordered_set<PipelineKey, PipelineKeyHash> known;
    std::vector<PipelineKey> pending; // loaded at start, not built yet
    size_t next = 0;
    std::unordered_set<uint64_t> shaderFiles;
    std::unordered_set<uint64_t> patched; // runtime vertex shaders with a "V" line already

    static std::filesystem::path Path() { return RegpuUserDir() / "pipelines.manifest"; }
    static std::filesystem::path UcodePath(bool pixel, uint64_t hash)
    {
        char name[48];
        snprintf(name, sizeof(name), "%s_%016llx.ucode", pixel ? "ps" : "vs", (unsigned long long)hash);
        return RegpuUserDir() / "shadercache" / name;
    }
    void Load()
    {
        std::ifstream in(Path());
        std::string line;
        while (std::getline(in, line))
        {
            if (unsigned long long shipped, runtime; sscanf(line.c_str(), "V %llx %llx", &shipped, &runtime) == 2)
            {
                patched.insert(runtime);
                continue;
            }
            PipelineKey k{};
            unsigned long long vs, ps;
            // (Older manifests end at the flags: no polygon offset.)
            if (sscanf(line.c_str(), "P %llx %llx %x %x %x %x %x %x", &vs, &ps, &k.blend, &k.depth, &k.stencilMaskMode, &k.flags, &k.biasScale, &k.biasOffset) >= 6)
            {
                k.vs = vs;
                k.ps = ps;
                k.biasOffset = RoundedBias(k.biasOffset);
                if (known.insert(k).second)
                    pending.push_back(k);
            }
        }
        g_stats.warmTotal = uint32_t(pending.size());
        Log("renderer: manifest lists %zu pipelines to rebuild", pending.size());
    }
    void RecordPipeline(const PipelineKey& k)
    {
        std::lock_guard lock(mutex);
        if (!known.insert(k).second)
            return;
        g_diskWriter.Push([k] {
            std::ofstream(Path(), std::ios::app) << "P " << std::hex << k.vs << ' ' << k.ps << ' ' << k.blend << ' ' << k.depth << ' '
                                                  << k.stencilMaskMode << ' ' << k.flags << ' ' << k.biasScale << ' ' << k.biasOffset << '\n';
        });
    }
    void RecordShader(bool pixel, uint64_t hash, const std::vector<uint32_t>& codeBE)
    {
        std::lock_guard lock(mutex);
        if (codeBE.empty() || !shaderFiles.insert(hash).second)
            return;
        const bool patch = !pixel && patched.insert(hash).second;
        g_diskWriter.Push([path = UcodePath(pixel, hash), code = codeBE, patch, hash] {
            std::error_code ec;
            if (!std::filesystem::exists(path, ec))
                std::ofstream(path, std::ios::binary).write(reinterpret_cast<const char*>(code.data()), std::streamsize(code.size() * 4));
            if (patch)
                if (const std::string line = VertexPatch::Line(hash, code); !line.empty())
                    std::ofstream(Path(), std::ios::app) << line << '\n';
        });
    }
    static bool LoadShader(bool pixel, uint64_t hash, std::vector<uint32_t>& out)
    {
        std::ifstream in(UcodePath(pixel, hash), std::ios::binary | std::ios::ate);
        if (!in)
            return false;
        out.resize(size_t(in.tellg()) / 4);
        in.seekg(0);
        in.read(reinterpret_cast<char*>(out.data()), std::streamsize(out.size() * 4));
        return !out.empty();
    }
} g_manifest;

// Shader compilation runs on worker threads so the command processor never
// stalls on fxc: a draw whose shaders are still compiling is skipped. The
// bytecode is cached on disk by HLSL hash, so later runs start warm.
// Compiles that something waits for skip fxc's optimiser (a large shader can take seconds with
// it). REGPU_OPTIMIZE_SHADERS=1 also builds each shader optimised on a background thread and
// stores it beside the unoptimised one (<key>.o.dxbc); later runs load that. The running
// session keeps the bytecode its pipelines were built from.
const bool g_optimizeShaders = getenv("REGPU_OPTIMIZE_SHADERS") != nullptr;

std::filesystem::path ShaderCachePath(uint64_t key, bool optimized = false)
{
    char name[40];
    snprintf(name, sizeof(name), optimized ? "%016llx.o.dxbc" : "%016llx.dxbc", (unsigned long long)key);
    return RegpuUserDir() / "shadercache" / name;
}

// REGPU_OPTIMIZE_SHADERS: optimised builds, one at a time at the lowest priority.
struct ShaderOptimizer
{
    struct Job
    {
        uint64_t key;
        bool pixel;
        std::string hlsl;
    };
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<Job> jobs;
    std::unordered_set<uint64_t> queued;
    std::atomic<uint32_t> built{ 0 };

    void Push(uint64_t key, bool pixel, const std::string& hlsl)
    {
        {
            std::lock_guard lock(mutex);
            if (!queued.insert(key).second)
                return;
            jobs.push_back({ key, pixel, hlsl });
        }
        static std::once_flag started;
        // REGPU_OPTIMIZE_THREADS=n: n builders (default 1, at the lowest priority).
        std::call_once(started, [this] {
            const int n = getenv("REGPU_OPTIMIZE_THREADS") ? std::clamp(atoi(getenv("REGPU_OPTIMIZE_THREADS")), 1, 16) : 1;
            for (int i = 0; i < n; i++)
                std::thread([this] { Run(); }).detach();
        });
        cv.notify_one();
    }

    void Run()
    {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_LOWEST);
        for (;;)
        {
            Job job;
            {
                std::unique_lock lock(mutex);
                cv.wait(lock, [this] { return !jobs.empty(); });
                job = std::move(jobs.front());
                jobs.pop_front();
            }
            const D3D_SHADER_MACRO vertexStage[] = { { "REGPU_VERTEX_STAGE", "1" }, { nullptr, nullptr } };
            ComPtr<ID3DBlob> code, errors;
            if (FAILED(D3DCompile(job.hlsl.data(), job.hlsl.size(), nullptr, job.pixel ? nullptr : vertexStage, nullptr, "main",
                    job.pixel ? "ps_5_1" : "vs_5_1", D3DCOMPILE_OPTIMIZATION_LEVEL1 | D3DCOMPILE_ENABLE_UNBOUNDED_DESCRIPTOR_TABLES, 0,
                    &code, &errors)))
                continue; // the unoptimised build stays
            // Written under a temporary name: a run that reads it never sees half a file.
            const auto path = ShaderCachePath(job.key, true);
            auto tmp = path;
            tmp += ".tmp";
            std::ofstream(tmp, std::ios::binary).write(static_cast<const char*>(code->GetBufferPointer()), std::streamsize(code->GetBufferSize()));
            std::error_code ec;
            std::filesystem::rename(tmp, path, ec);
            if (const uint32_t n = ++built; n == 1 || n % 100 == 0)
                Log("renderer: %u shaders optimised in the background", n);
        }
    }
} g_optimizer;

// The stored bytecode for cs.cacheKey, optimised first; an unoptimised one is queued for its
// optimised build.
bool LoadCachedBytecode(CompiledShader& cs)
{
    for (const bool optimized : { true, false })
    {
        if (optimized && !g_optimizeShaders)
            continue;
        std::ifstream in{ ShaderCachePath(cs.cacheKey, optimized), std::ios::binary | std::ios::ate };
        if (!in)
            continue;
        const auto size = size_t(in.tellg());
        if (!size || FAILED(D3DCreateBlob(size, &cs.code)))
            continue;
        in.seekg(0);
        in.read(static_cast<char*>(cs.code->GetBufferPointer()), std::streamsize(size));
        if (!optimized && g_optimizeShaders)
            g_optimizer.Push(cs.cacheKey, cs.pixel, cs.info.hlsl);
        return true;
    }
    return false;
}

struct ShaderCompiler
{
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<CompiledShader*> queue;
    std::deque<CompiledShader*> background; // static shaders: compiled when nothing urgent waits
    std::vector<std::thread> workers;
    std::atomic<int> jobs{ 0 };   // urgent, queued or compiling (holds wait for these)
    std::atomic<int> waiting{ 0 }; // background, queued or parked
    // Once the game runs, background pushes (memory-scan finds: mostly stale copies the
    // game never draws) are parked instead: translated, compiled only if a draw promotes them.
    std::atomic<bool> park{ false };
    std::unordered_set<const CompiledShader*> parked;

    void Start()
    {
        std::filesystem::create_directories(RegpuUserDir() / "shadercache");
        // All but one core: the first boot compiles the whole game behind the overlay.
        const unsigned n = std::max(2u, std::thread::hardware_concurrency() - 1);
        for (unsigned i = 0; i < n; i++)
            workers.emplace_back([this] { Run(); });
        for (auto& w : workers)
            w.detach();
    }

    void Push(CompiledShader* cs, bool later = false)
    {
        {
            std::lock_guard lock(mutex);
            if (later)
            {
                waiting++;
                if (park.load())
                    parked.insert(cs);
                else
                    background.push_back(cs);
            }
            else
            {
                jobs++;
                queue.push_back(cs);
            }
        }
        cv.notify_one();
    }

    // Moves a background shader something now needs to the front of the urgent queue.
    void Promote(const CompiledShader& cs)
    {
        if (cs.state.load(std::memory_order_relaxed) != CompiledShader::Pending || !waiting.load(std::memory_order_relaxed))
            return;
        std::lock_guard lock(mutex);
        bool found = false;
        if (parked.erase(&cs))
            found = true;
        else if (auto it = std::find(background.begin(), background.end(), &cs); it != background.end())
        {
            background.erase(it);
            found = true;
        }
        if (found)
        {
            waiting--;
            jobs++;
            queue.push_front(const_cast<CompiledShader*>(&cs));
        }
    }

    void Run()
    {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
        for (;;)
        {
            CompiledShader* cs;
            bool urgent;
            {
                std::unique_lock lock(mutex);
                cv.wait(lock, [this] { return !queue.empty() || !background.empty(); });
                urgent = !queue.empty();
                auto& from = urgent ? queue : background;
                cs = from.front();
                from.pop_front();
                if (!urgent)
                    waiting--;
            }
            if (urgent)
            {
                char label[64];
                snprintf(label, sizeof(label), "%s %08llx", cs->pixel ? "pixel shader" : "vertex shader", (unsigned long long)(cs->hash >> 32));
                g_stats.Begin(label);
                Compile(*cs);
                g_stats.End(true);
                jobs--;
            }
            else
                Compile(*cs);
        }
    }

    static void Compile(CompiledShader& cs)
    {
        ComPtr<ID3DBlob> errors;
        // Runtime compiles skip optimisation: fxc can take minutes on the
        // larger translated shaders.
        const auto start = std::chrono::steady_clock::now();
        // The translated source picks its sampling form per stage (see hlsl.cpp: a vertex
        // shader cannot sample with an implicit LOD).
        const D3D_SHADER_MACRO vertexStage[] = { { "REGPU_VERTEX_STAGE", "1" }, { nullptr, nullptr } };
        const HRESULT hr = D3DCompile(cs.info.hlsl.data(), cs.info.hlsl.size(), nullptr, cs.pixel ? nullptr : vertexStage, nullptr, "main",
            cs.pixel ? "ps_5_1" : "vs_5_1", D3DCOMPILE_SKIP_OPTIMIZATION | D3DCOMPILE_ENABLE_UNBOUNDED_DESCRIPTOR_TABLES, 0, &cs.code, &errors);
        const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        if (FAILED(hr))
        {
            Log("renderer: %s %016llx failed to compile:\n%s", cs.pixel ? "ps" : "vs", (unsigned long long)cs.hash,
                errors ? static_cast<const char*>(errors->GetBufferPointer()) : "?");
            cs.state.store(CompiledShader::Failed, std::memory_order_release);
            return;
        }
        g_stats.shadersBuilt++;
        if (ms > 200)
            Log("renderer: %s %016llx compiled in %.0f ms (background)", cs.pixel ? "ps" : "vs", (unsigned long long)cs.hash, ms);
        std::ofstream(ShaderCachePath(cs.cacheKey), std::ios::binary)
            .write(static_cast<const char*>(cs.code->GetBufferPointer()), std::streamsize(cs.code->GetBufferSize()));
        if (g_optimizeShaders)
            g_optimizer.Push(cs.cacheKey, cs.pixel, cs.info.hlsl);
        cs.state.store(CompiledShader::Ready, std::memory_order_release);
    }
} g_compiler;

// Shaders by canonical key: the microcode with the vertex fetch fields the title patches at
// load masked out. A patched copy of a translated shader shares its HLSL and bytecode and
// only needs its own fetch layout (TranslateVertexFetchLayout), not a full translation
// (which took up to ~18 ms on the command processor for a large shader).
std::unordered_map<uint64_t, const CompiledShader*> g_canonical;
// Bytecode already loaded, by HLSL hash: a shader whose HLSL matches one in memory doesn't
// read the shader cache from disk again.
std::unordered_map<uint64_t, ComPtr<ID3DBlob>> g_bytecode;

uint64_t CanonicalKey(const ucode::Shader& parsed)
{
    std::vector<uint32_t> w = parsed.words;
    for (const ucode::Cf& cf : parsed.cf)
    {
        if (cf.IsExec())
            for (uint32_t k = 0; k < cf.count(); k++)
            {
                const uint32_t index = cf.address() + k;
                if (!((cf.sequence() >> (2 * k)) & 1) || (index + 1) * 3 > w.size() || (w[index * 3] & 0x1F) != 0)
                    continue;
                w[index * 3] &= ~0x1FF00000u;   // fetch constant
                w[index * 3 + 1] &= 0x80000000u; // swizzle, format, sign, rounding, exponent, mini
                w[index * 3 + 2] &= 0x80000000u; // stride, offset
            }
        if (cf.IsEnd())
            break;
    }
    return Fnv(w.data(), w.size() * 4, 0x5A17);
}

// background: queued behind everything a draw needs (the static shader set).
// Live pixel shader edits (the debug server's "ps HASH EXPR"): draws with that shader use an alias hash
// instead, translated afresh with its output replaced, so they get their own pipelines and the original
// shader is untouched (shaders are never removed). A new edit gets a new alias; clearing it goes back.
std::mutex g_psEditMutex;
std::unordered_map<uint64_t, uint64_t> g_psEditAlias; // shader -> alias
std::unordered_map<uint64_t, std::string> g_psEditExpr; // alias -> the expression it returns
std::atomic<bool> g_psEdits{ false };
std::unordered_set<uint64_t> g_psEditEver; // every alias made (kept out of the pipeline manifest)
// The debug server's drawstats: draws per (vs, ps) over one frame.
std::atomic<int> g_drawStats{ 0 }; // 1 armed for the next frame, 2 counting, 0 off
std::mutex g_drawStatsMutex;
std::map<std::pair<uint64_t, uint64_t>, uint32_t> g_drawStatsCounts;
bool IsPsEditAlias(uint64_t hash)
{
    std::lock_guard lock(g_psEditMutex);
    return g_psEditEver.count(hash) != 0;
}
uint64_t PsEditAlias(uint64_t hash)
{
    if (!g_psEdits.load(std::memory_order_relaxed))
        return hash;
    std::lock_guard lock(g_psEditMutex);
    auto it = g_psEditAlias.find(hash);
    return it == g_psEditAlias.end() ? hash : it->second;
}

const CompiledShader& GetShaderLocked(uint64_t hash, const std::vector<uint32_t>& codeBE, bool pixel, bool background);
// Shaders are never removed: each thread remembers the ones it looked up (no lock per draw).
const CompiledShader& GetShader(uint64_t hash, const std::vector<uint32_t>& codeBE, bool pixel, bool background = false)
{
    thread_local std::unordered_map<uint64_t, const CompiledShader*> known;
    // Neighbouring draws mostly use the same shaders: a small direct-mapped table in front of the map
    // (its lookups were ~0.7% of the command processor).
    struct Recent
    {
        uint64_t hash = 0;
        const CompiledShader* cs = nullptr;
    };
    thread_local Recent recent[64];
    Recent& r = recent[(hash ^ (hash >> 17)) & 63];
    const CompiledShader* found = r.hash == hash ? r.cs : nullptr;
    if (!found)
        if (auto it = known.find(hash); it != known.end())
            found = it->second;
    if (found)
    {
        r = { hash, found };
        // First seen as a background find (a memory scan), it may still be parked: a draw
        // that needs it must promote it, or its wait never ends.
        if (!background)
            g_compiler.Promote(*found);
        return *found;
    }
    std::lock_guard shaderLock(g_shaderMutex);
    const CompiledShader& cs = GetShaderLocked(hash, codeBE, pixel, background);
    known.emplace(hash, &cs);
    r = { hash, &cs };
    return cs;
}

const CompiledShader& GetShaderLocked(uint64_t hash, const std::vector<uint32_t>& codeBE, bool pixel, bool background)
{
    auto it = g_r.shaders.find(hash);
    if (it != g_r.shaders.end())
    {
        if (!background)
            g_compiler.Promote(*it->second);
        return *it->second;
    }
    static std::once_flag started;
    std::call_once(started, [] { g_compiler.Start(); });
    auto& slot = g_r.shaders[hash];
    slot = std::make_unique<CompiledShader>();
    CompiledShader& cs = *slot;
    cs.hash = hash;
    cs.pixel = pixel;
    // Not saved yet: the memory scan also finds stale copies in freed blocks (heap links
    // over the first words), so only shaders a pipeline uses go into the cache.
    cs.ucodeBE = codeBE;
    const auto getStart = std::chrono::steady_clock::now();
    static LiveSwitch perfLog("REGPU_PERF");
    const char* path = "full";
    struct LogSlow
    {
        std::chrono::steady_clock::time_point start;
        const CompiledShader& cs;
        const char*& path;
        ~LogSlow()
        {
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
            if (perfLog && ms > 5.0)
                Log("perf: GetShader %s %016llx took %.1f ms (%s, %zu dwords)", cs.pixel ? "ps" : "vs", (unsigned long long)cs.hash, ms, path,
                    cs.ucodeBE.size());
        }
    } logSlow{ getStart, cs, path };
    const ucode::Shader parsed = ucode::Parse(codeBE.data(), uint32_t(codeBE.size()), pixel);
    static LiveSwitch noCanonical("REGPU_NO_CANONICAL");
    // Baked vertex fetch puts each copy's layout in its code, so copies can't share one compile.
    const uint64_t canonical = pixel || noCanonical || BakedVertexFetch() || IsPsEditAlias(hash) ? 0 : CanonicalKey(parsed);
    if (canonical)
        path = g_canonical.count(canonical) ? (g_canonical[canonical]->IsReady() ? "full, layout fallback" : "full, base not ready") : "full, new canonical";
    if (canonical)
        if (auto c = g_canonical.find(canonical); c != g_canonical.end() && c->second->IsReady())
        {
            const CompiledShader& base = *c->second;
            TranslatedShader layout;
            if (TranslateVertexFetchLayout(parsed, layout))
            {
                cs.info.ok = true;
                cs.info.usedTextureSlots = base.info.usedTextureSlots;
                cs.info.memExport = base.info.memExport;
                cs.info.constantsUsed = base.info.constantsUsed; // same ALU code
                cs.info.constantMask = base.info.constantMask;
                cs.info.interpolatorsWritten = base.info.interpolatorsWritten;
                cs.info.vertexFetchLayout = std::move(layout.vertexFetchLayout);
                cs.info.vertexFetchConstants = std::move(layout.vertexFetchConstants);
                cs.cacheKey = base.cacheKey;
                cs.code = base.code;
                cs.hlslSource = &base;
                cs.tag = base.tag;
                cs.state = CompiledShader::Ready;
                g_stats.shadersCached++;
                path = "variant";
                return cs;
            }
        }
    cs.info = TranslateShader(parsed);
    // Only a translation whose fetches all read vfl can stand in for its patched copies; one
    // with baked fetch fields would hand them the wrong layout.
    if (canonical && cs.info.ok)
        if (TranslatedShader check; TranslateVertexFetchLayout(parsed, check))
            g_canonical.emplace(canonical, &cs);
    if (!cs.info.ok)
    {
        Log("renderer: %s %016llx not translated: %s", pixel ? "ps" : "vs", (unsigned long long)hash, cs.info.error.c_str());
        // REGPU_DUMP_SHADERS: keep the microcode too, so a missing instruction can be looked
        // at with tools/xenos_dis (the on-load dump only covers shaders that get this far).
        static const bool dumpShaders = getenv("REGPU_DUMP_SHADERS") != nullptr;
        if (dumpShaders)
        {
            const auto dir = RegpuUserDir() / "shaders";
            std::filesystem::create_directories(dir);
            char name[64];
            snprintf(name, sizeof(name), "fail_%s_%016llx.bin", pixel ? "ps" : "vs", (unsigned long long)hash);
            std::ofstream(dir / name, std::ios::binary)
                .write(reinterpret_cast<const char*>(cs.ucodeBE.data()), std::streamsize(cs.ucodeBE.size() * 4));
        }
        cs.state = CompiledShader::Failed;
        return cs;
    }
    // The game's own fixes to this shader (regpu.h Host::rewriteShader).
    if (const auto rewrite = regpu::GetHost().rewriteShader)
        cs.tag = rewrite(hash, cs.info.hlsl, pixel);
    if (g_dumpHlsl)
    {
        std::filesystem::create_directories("out/hlsl");
        char name[64];
        snprintf(name, sizeof(name), "out/hlsl/%s_%016llx.hlsl", pixel ? "ps" : "vs", (unsigned long long)hash);
        if (FILE* f = fopen(name, "wb"))
        {
            fwrite(cs.info.hlsl.data(), 1, cs.info.hlsl.size(), f);
            fclose(f);
        }
    }
    // Debug: REGPU_DEBUG_VS=<hash>:<statement> appends a statement to that vertex
    // shader's guest program (it can read r[] and write o[]).
    if (const char* dbg = getenv("REGPU_DEBUG_VS"); dbg && !pixel && strtoull(dbg, nullptr, 16) == hash)
        if (const char* stmt = strchr(dbg, ':'))
        {
            // "<hash>:@<block>:<statement>" inserts at the start of control-flow block <block>.
            std::string s = stmt + 1, marker = "}\nvoid GuestVS(uint vindex, out float4 opos, out float4 o[16])\n";
            if (s[0] == '@')
            {
                const size_t colon = s.find(':');
                marker = "        if (cfpc == " + s.substr(1, colon - 1) + "u)\n        {\n";
                s = s.substr(colon + 1);
            }
            if (size_t at = cs.info.hlsl.find(marker); at != std::string::npos)
                cs.info.hlsl.insert(marker[0] == '}' ? at : at + marker.size(), "            " + s + "\n");
        }
    // Debug: REGPU_TINT_PS=h1,h2,... paints those pixel shaders in distinct flat
    // colours (red, green, blue, yellow, magenta, cyan, white, orange, ...).
    if (const char* list = getenv("REGPU_TINT_PS"); list && pixel)
    {
        static const char* kTints[] = { "1,0,0", "0,1,0", "0,0,1", "1,1,0", "1,0,1", "0,1,1", "1,1,1", "1,0.5,0", "0.5,0,1", "0,0.5,0.3",
            "0.5,0.5,0.5", "0.3,0.15,0" };
        int n = 0;
        for (const char* s = list; *s && n < 12; n++)
        {
            char* end;
            const uint64_t h = strtoull(s, &end, 16);
            if (h == hash)
                if (size_t at = cs.info.hlsl.rfind("return oC[0];"); at != std::string::npos)
                    cs.info.hlsl.replace(at, 13, std::string("return float4(") + kTints[n] + ", 1);");
            s = *end == ',' ? end + 1 : end;
            if (end == s && *end != ',')
                break;
        }
    }
    // Debug: REGPU_DEBUG_PS=<hash>:<expr> replaces that pixel shader's output, e.g.
    // "a62c809452fcc71b:float4(input.o[4].xyz, 1)".
    // REGPU_DEBUG_PS_ALL=<expr> does the same for every pixel shader.
    if (const char* all = getenv("REGPU_DEBUG_PS_ALL"); all && pixel)
        if (size_t at = cs.info.hlsl.rfind("return oC[0];"); at != std::string::npos)
            cs.info.hlsl.replace(at, 13, std::string("return ") + all + ";");
    if (const char* dbg = getenv("REGPU_DEBUG_PS"); dbg && pixel && strtoull(dbg, nullptr, 16) == hash)
        if (const char* expr = strchr(dbg, ':'))
            if (size_t at = cs.info.hlsl.rfind("return oC[0];"); at != std::string::npos)
                cs.info.hlsl.replace(at, 13, std::string("return ") + (expr + 1) + ";");
    if (!pixel && g_psEdits.load(std::memory_order_relaxed))
    {
        std::string stmt;
        {
            std::lock_guard lock(g_psEditMutex);
            if (auto e = g_psEditExpr.find(hash); e != g_psEditExpr.end())
                stmt = e->second;
        }
        const std::string marker = "}\nvoid GuestVS(uint vindex, out float4 opos, out float4 o[16])\n";
        // "@TEXT|STATEMENT" runs it after the first line holding TEXT instead of at the end.
        size_t at = std::string::npos;
        if (!stmt.empty() && stmt[0] == '@')
        {
            const size_t bar = stmt.find('|');
            if (bar != std::string::npos)
                if (size_t hit = cs.info.hlsl.find(stmt.substr(1, bar - 1)); hit != std::string::npos)
                    at = cs.info.hlsl.find('\n', hit) + 1;
            stmt = bar == std::string::npos ? "" : stmt.substr(bar + 1);
        }
        else if (!stmt.empty())
            at = cs.info.hlsl.find(marker);
        if (!stmt.empty() && at != std::string::npos && at != 0)
        {
            cs.info.hlsl.insert(at, "            " + stmt + "\n");
            Log("debug: vs alias %016llx runs %s", (unsigned long long)hash, stmt.c_str());
        }
        else if (!stmt.empty())
            Log("debug: vs alias %016llx: the anchor wasn't found", (unsigned long long)hash);
    }
    if (pixel && g_psEdits.load(std::memory_order_relaxed))
    {
        std::string expr;
        {
            std::lock_guard lock(g_psEditMutex);
            if (auto e = g_psEditExpr.find(hash); e != g_psEditExpr.end())
                expr = e->second;
        }
        // "@TEXT|STATEMENT" runs STATEMENT after the first line holding TEXT, as for vertex shaders
        // (to read a value mid-shader: "@cfpc = 9u;|oC0 = float4(r1.xyz, 1); cfpc = 0xFFFFu;").
        if (!expr.empty() && expr[0] == '@')
        {
            const size_t bar = expr.find('|');
            size_t at = std::string::npos;
            if (bar != std::string::npos)
                if (size_t hit = cs.info.hlsl.find(expr.substr(1, bar - 1)); hit != std::string::npos)
                    at = cs.info.hlsl.find('\n', hit) + 1;
            if (at != std::string::npos && at != 0)
            {
                cs.info.hlsl.insert(at, expr.substr(bar + 1) + "\n");
                Log("debug: ps alias %016llx runs %s", (unsigned long long)hash, expr.c_str());
            }
            else
                Log("debug: ps alias %016llx: the anchor wasn't found", (unsigned long long)hash);
        }
        else if (!expr.empty())
        {
            if (size_t at = cs.info.hlsl.rfind("return oC[0];"); at != std::string::npos)
                cs.info.hlsl.replace(at, 13, "return " + expr + ";");
            Log("debug: ps alias %016llx returns %s", (unsigned long long)hash, expr.c_str());
        }
    }
    cs.cacheKey = Fnv(cs.info.hlsl.data(), cs.info.hlsl.size(), pixel ? 1 : 2);
    if (auto b = g_bytecode.find(cs.cacheKey); b != g_bytecode.end())
    {
        cs.code = b->second;
        cs.state = CompiledShader::Ready;
        g_stats.shadersCached++;
        return cs;
    }
    if (LoadCachedBytecode(cs))
    {
        cs.state = CompiledShader::Ready;
        g_bytecode.emplace(cs.cacheKey, cs.code);
        g_stats.shadersCached++;
        return cs;
    }
    g_compiler.Push(&cs, background);
    return cs;
}

// Motion-vector variant of a translated shader (temporal upscalers). Built by rewriting the
// generated HLSL, so default shaders (and their caches) stay as they are:
// - the vertex constants gain last frame's c[] (vcPrev, see DrawImpl) and the frame's
//   jitter (vjitter, NDC units);
// - the vertex shader runs the guest program a second time with vcPrev (GuestVSPrev), puts
//   both clip positions in two more interpolants and jitters only the rasterized position;
// - the pixel shader also writes the motion to SV_Target1 as a UV offset (previous minus
//   current, in the 0..1 range of the target).
// Empty when the shader's text is not in the expected shape.
std::string MotionVariant(const std::string& hlsl, bool pixel)
{
    std::string h = hlsl;
    auto replace = [&](const std::string& from, const std::string& to) {
        const size_t at = h.find(from);
        if (at == std::string::npos)
            return false;
        h.replace(at, from.size(), to);
        return true;
    };
    if (!replace("    float4 vwide;\n};", "    float4 vwide;\n    float4 vjitter;\n};\ncbuffer VSPrevious : register(b2)\n{\n    float4 vcPrev[256];\n};") ||
        !replace("    float2 pcoord : TEXCOORD16; // point sprite coordinate\n",
            "    float2 pcoord : TEXCOORD16; // point sprite coordinate\n    float4 mcur : TEXCOORD17;\n    float4 mprev : TEXCOORD18;\n"))
        return {};
    if (pixel)
    {
        if (!replace("float4 main(VSOut input) : SV_Target0\n{", "struct PSMotionOut { float4 c : SV_Target0; float2 mv : SV_Target1; };\n"
                                                                    "PSMotionOut main(VSOut input)\n{"))
            return {};
        const size_t at = h.rfind("    return oC[0];\n}");
        if (at == std::string::npos)
            return {};
        h.replace(at, std::string("    return oC[0];\n}").size(),
            "    PSMotionOut motionOut;\n"
            "    motionOut.c = oC[0];\n"
            "    float2 cur = input.mcur.xy / input.mcur.w, prev = input.mprev.xy / input.mprev.w;\n"
            "    motionOut.mv = (prev - cur) * float2(0.5, -0.5);\n"
            "    return motionOut;\n}");
        return h;
    }
    // Vertex shader: GuestVSPrev, a copy of the guest program reading last frame's constants.
    const std::string sig = "void GuestVS(uint vindex, out float4 opos, out float4 o[16], out float4 opts)\n{";
    const size_t start = h.find(sig);
    const size_t end = h.find("}\nvoid GuestVS(uint vindex, out float4 opos, out float4 o[16])\n", start);
    if (start == std::string::npos || end == std::string::npos)
        return {};
    std::string prev = h.substr(start, end + 2 - start);
    prev.replace(0, std::string("void GuestVS(").size(), "void GuestVSPrev(");
    for (size_t at = 0; (at = prev.find("vc[", at)) != std::string::npos; at += 7)
        prev.replace(at, 3, "vcPrev[");
    h.insert(end + 2, prev);
    // The viewport transform (from the target size to the clip position) as a function.
    const std::string from = "    float2 size = float2(vinfo.zw);\n";
    const std::string last = "    r.pos = float4(ndc * w, zc, w);\n";
    const size_t a = h.find(from), b = h.find(last);
    const size_t mainAt = h.find("VSOut main(uint id : SV_VertexID)\n{");
    if (a == std::string::npos || b == std::string::npos || mainAt == std::string::npos || b < a || a < mainAt)
        return {};
    std::string body = h.substr(a, b - a);
    for (size_t at = 0; (at = body.find("r.pos", at)) != std::string::npos; at += 3)
        body.replace(at, 5, "pos");
    const std::string fn = "float4 ToClip(float4 pos, float2 pointOffset)\n{\n" + body + "    return float4(ndc * w, zc, w);\n}\n";
    h.replace(a, b + last.size() - a,
        "    r.pos = ToClip(r.pos, pointOffset);\n"
        "    r.mcur = r.pos;\n"
        "    r.mprev = r.pos;\n"
        "    if (!isPoint && !rect)\n"
        "    {\n"
        "        float4 q, qo[16], qpts;\n"
        "        GuestVSPrev(e, q, qo, qpts);\n"
        "        r.mprev = ToClip(q, pointOffset);\n"
        "    }\n"
        "    r.pos.xy += vjitter.xy * r.pos.w;\n");
    h.insert(mainAt, fn);
    // VSOut members the original code never sets.
    return h;
}

// The motion-vector variant of a shader (compiled on first use; nullptr if it cannot be made).
std::unordered_map<uint64_t, std::unique_ptr<CompiledShader>> g_motionShaders;
const CompiledShader* MotionShaderLookup(const CompiledShader& base);
const CompiledShader* MotionShader(const CompiledShader& base)
{
    if (base.motion && base.motion->state.load(std::memory_order_acquire) != CompiledShader::Pending)
        return base.motion->state.load(std::memory_order_acquire) == CompiledShader::Failed ? nullptr : base.motion;
    const CompiledShader* m = MotionShaderLookup(base);
    base.motion = m;
    return m;
}

const CompiledShader* MotionShaderLookup(const CompiledShader& base)
{
    const CompiledShader& source = base.hlslSource ? *base.hlslSource : base;
    auto [it, inserted] = g_motionShaders.try_emplace(source.cacheKey);
    if (!inserted)
        return it->second->state.load(std::memory_order_acquire) == CompiledShader::Failed ? nullptr : it->second.get();
    it->second = std::make_unique<CompiledShader>();
    CompiledShader& cs = *it->second;
    cs.hash = source.hash;
    cs.pixel = source.pixel;
    cs.info = source.info;
    cs.info.hlsl = MotionVariant(source.info.hlsl, source.pixel);
    if (cs.info.hlsl.empty())
    {
        LOG_ONCE("upscale: no motion-vector variant for %s %016llx", source.pixel ? "ps" : "vs", (unsigned long long)source.hash);
        cs.state = CompiledShader::Failed;
        return nullptr;
    }
    cs.cacheKey = Fnv(cs.info.hlsl.data(), cs.info.hlsl.size(), source.pixel ? 1 : 2);
    if (auto b = g_bytecode.find(cs.cacheKey); b != g_bytecode.end())
    {
        cs.code = b->second;
        cs.state = CompiledShader::Ready;
        return &cs;
    }
    if (LoadCachedBytecode(cs))
    {
        cs.state = CompiledShader::Ready;
        g_bytecode.emplace(cs.cacheKey, cs.code);
        return &cs;
    }
    g_compiler.Push(&cs, false);
    return &cs;
}

D3D12_BLEND Blend(uint32_t f)
{
    switch (f)
    {
    case 0: return D3D12_BLEND_ZERO;
    case 1: return D3D12_BLEND_ONE;
    case 4: return D3D12_BLEND_SRC_COLOR;
    case 5: return D3D12_BLEND_INV_SRC_COLOR;
    case 6: return D3D12_BLEND_SRC_ALPHA;
    case 7: return D3D12_BLEND_INV_SRC_ALPHA;
    case 8: return D3D12_BLEND_DEST_COLOR;
    case 9: return D3D12_BLEND_INV_DEST_COLOR;
    case 10: return D3D12_BLEND_DEST_ALPHA;
    case 11: return D3D12_BLEND_INV_DEST_ALPHA;
    case 12: return D3D12_BLEND_BLEND_FACTOR;
    case 14: return D3D12_BLEND_ALPHA_FACTOR;
    case 13: return D3D12_BLEND_INV_BLEND_FACTOR;
    case 15: return D3D12_BLEND_INV_ALPHA_FACTOR;
    case 16: return D3D12_BLEND_SRC_ALPHA_SAT;
    default: return D3D12_BLEND_ONE;
    }
}

D3D12_BLEND AlphaBlend(uint32_t f)
{
    // Colour factors are invalid for alpha in D3D12: use their alpha forms.
    switch (f)
    {
    case 4: return D3D12_BLEND_SRC_ALPHA;
    case 5: return D3D12_BLEND_INV_SRC_ALPHA;
    case 8: return D3D12_BLEND_DEST_ALPHA;
    case 9: return D3D12_BLEND_INV_DEST_ALPHA;
    default: return Blend(f);
    }
}

D3D12_BLEND_OP BlendOp(uint32_t op)
{
    switch (op)
    {
    case 1: return D3D12_BLEND_OP_SUBTRACT;
    case 2: return D3D12_BLEND_OP_MIN;
    case 3: return D3D12_BLEND_OP_MAX;
    case 4: return D3D12_BLEND_OP_REV_SUBTRACT;
    default: return D3D12_BLEND_OP_ADD;
    }
}

// Persistent pipeline library: compiled GPU code survives between runs
// (Config::userDir/pipelines.bin; keyed by the driver, so a driver update just misses).
struct PipelineLibrary
{
    ComPtr<ID3D12PipelineLibrary> library;
    std::atomic<int> stored{ 0 };
    std::mutex saveMutex;
    // Stores and loads share the library; serializing takes it alone. Serialize running
    // during a StorePipeline is not something the runtime promises to handle, and a cold
    // load with many pipelines building at once locked up (Mugello).
    std::shared_mutex access;
    std::chrono::steady_clock::time_point lastSave = std::chrono::steady_clock::now();
    std::vector<char> loaded;
    // Compaction: entries are never removed, so every translator or driver change leaves the old ones
    // behind (FM2 port: 574 MB read at every boot). Every pipeline this session loads or builds also
    // goes into `fresh`; once that holds every pipeline the manifest lists and is at least 10% smaller,
    // it replaces the old library and is what gets saved. REGPU_NO_LIBRARY_COMPACT=1 keeps the old one.
    ComPtr<ID3D12PipelineLibrary> fresh;
    std::atomic<size_t> freshCount{ 0 };

    static std::filesystem::path Path() { return RegpuUserDir() / "pipelines.bin"; }

    void Init(ID3D12Device* device)
    {
        ComPtr<ID3D12Device1> d1;
        if (FAILED(device->QueryInterface(IID_PPV_ARGS(&d1))))
            return;
        if (std::ifstream in{ Path(), std::ios::binary | std::ios::ate })
        {
            loaded.resize(size_t(in.tellg()));
            in.seekg(0);
            in.read(loaded.data(), std::streamsize(loaded.size()));
        }
        if (FAILED(d1->CreatePipelineLibrary(loaded.data(), loaded.size(), IID_PPV_ARGS(&library))))
        {
            loaded.clear();
            if (FAILED(d1->CreatePipelineLibrary(nullptr, 0, IID_PPV_ARGS(&library))))
                library.Reset();
        }
        Log("renderer: pipeline library %s (%zu bytes loaded)", library ? "ready" : "unavailable", loaded.size());
        if (library && !loaded.empty() && !getenv("REGPU_NO_LIBRARY_COMPACT") && FAILED(d1->CreatePipelineLibrary(nullptr, 0, IID_PPV_ARGS(&fresh))))
            fresh.Reset();
    }

    // The shaders' compiled code is part of the name: the game's shader hashes alone stay the same
    // when the translation changes (a translator fix, REGPU_DYNAMIC_VFETCH), and a stale entry under
    // the name then failed to load and blocked storing the new one, rebuilding it every launch
    // (FM2 port: 181-496 of 498 pipelines rebuilt each launch, 0 after).
    static std::wstring Name(const PipelineKey& k, const D3D12_GRAPHICS_PIPELINE_STATE_DESC& pd)
    {
        uint64_t code = Fnv(pd.VS.pShaderBytecode, pd.VS.BytecodeLength, 3) * 0x9E3779B97F4A7C15ull ^
            (pd.PS.pShaderBytecode ? Fnv(pd.PS.pShaderBytecode, pd.PS.BytecodeLength, 4) : 0);
        // The fixed-function state too: an entry only loads with the description it was stored with, so
        // when the renderer's state for a key changed (depth bias, formats, sample count) the old entry
        // kept the name and blocked the new one (FM2 port: 2723 of 3031 rebuilt every launch).
        // REGPU_PIPELINE_NAME_SHADERS_ONLY=1 names by the shaders alone, as before.
        static const bool shadersOnly = getenv("REGPU_PIPELINE_NAME_SHADERS_ONLY") != nullptr;
        if (!shadersOnly)
        {
            code = (code ^ Fnv(&pd.BlendState, sizeof(pd.BlendState), 5)) * 0x9E3779B97F4A7C15ull;
            code = (code ^ Fnv(&pd.RasterizerState, sizeof(pd.RasterizerState), 6)) * 0x9E3779B97F4A7C15ull;
            code = (code ^ Fnv(&pd.DepthStencilState, sizeof(pd.DepthStencilState), 7)) * 0x9E3779B97F4A7C15ull;
            code = (code ^ Fnv(pd.RTVFormats, sizeof(pd.RTVFormats), 8)) * 0x9E3779B97F4A7C15ull;
            const uint32_t misc[] = { pd.SampleMask, uint32_t(pd.IBStripCutValue), uint32_t(pd.PrimitiveTopologyType), pd.NumRenderTargets,
                uint32_t(pd.DSVFormat), pd.SampleDesc.Count, pd.SampleDesc.Quality, pd.InputLayout.NumElements };
            code = (code ^ Fnv(misc, sizeof(misc), 9)) * 0x9E3779B97F4A7C15ull;
        }
        wchar_t name[128];
        swprintf(name, 128, L"%016llx_%016llx_%08x_%08x_%08x_%08x_%016llx", (unsigned long long)k.vs, (unsigned long long)k.ps, k.blend, k.depth,
            k.stencilMaskMode, k.flags, (unsigned long long)code);
        return name;
    }

    HRESULT CreateOrLoad(ID3D12Device* device, const D3D12_GRAPHICS_PIPELINE_STATE_DESC& pd, const PipelineKey& key, ComPtr<ID3D12PipelineState>& pso, bool* hit)
    {
        *hit = false;
        if (PipelineIsPoisoned(key.vs, key.ps))
            return E_FAIL; // (see kBadPipelinesFile)
        if (library)
        {
            const std::wstring name = Name(key, pd);
            {
                std::shared_lock lock(access);
                *hit = SUCCEEDED(library->LoadGraphicsPipeline(name.c_str(), &pd, IID_PPV_ARGS(&pso)));
            }
            if (*hit)
            {
                KeepFresh(name, pso.Get());
                return S_OK;
            }
            // The slow part (the driver compiling) runs outside the lock.
            g_compiling = { key.vs, key.ps, true };
            const HRESULT hr = device->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&pso));
            g_compiling.active = false;
            if (SUCCEEDED(hr))
            {
                std::shared_lock lock(access);
                if (SUCCEEDED(library->StorePipeline(name.c_str(), pso.Get())))
                    stored++;
            }
            if (SUCCEEDED(hr))
                KeepFresh(name, pso.Get());
            return hr;
        }
        g_compiling = { key.vs, key.ps, true };
        const HRESULT hr = device->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&pso));
        g_compiling.active = false;
        return hr;
    }

    void KeepFresh(const std::wstring& name, ID3D12PipelineState* pso)
    {
        if (!fresh)
            return;
        {
            std::shared_lock lock(access); // (stores share the library, as in CreateOrLoad)
            if (!fresh)
                return;
            if (SUCCEEDED(fresh->StorePipeline(name.c_str(), pso)))
                freshCount++;
        }
        size_t listed;
        {
            std::lock_guard manifestLock(g_manifest.mutex);
            listed = g_manifest.known.size();
        }
        if (!listed || freshCount < listed)
            return;
        bool swap = false;
        {
            std::unique_lock lock(access);
            // Only when it saves something: else the old library stays and is not written again.
            if (fresh && fresh->GetSerializedSize() * 10 < loaded.size() * 9)
            {
                library = fresh;
                swap = true;
            }
            fresh.Reset();
        }
        if (swap)
        {
            Log("renderer: pipeline library compacted to this session's %zu pipelines; saved at the next save", freshCount.load());
            stored++;
        }
    }

    // Writes the library out when new pipelines were stored (at most every 10 s). Never on
    // the calling thread: serializing and writing the whole library (tens to hundreds of MB)
    // there stalled the command processor for one frame every 10 s of gameplay. force (exit)
    // writes synchronously.
    std::atomic<bool> saving{ false };

    void MaybeSave(bool force = false)
    {
        if (!library || !stored.load())
            return;
        if (force)
        {
            SaveNow();
            return;
        }
        if (std::chrono::steady_clock::now() - lastSave < std::chrono::seconds(10) || saving.exchange(true))
            return;
        lastSave = std::chrono::steady_clock::now();
        std::thread([this] {
            // Not lower: it holds the library alone while serializing, and starving it then
            // would stall every pipeline build.
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
            SaveNow();
            saving = false;
        }).detach();
    }

    void SaveNow()
    {
        std::lock_guard lock(saveMutex);
        const int pending = stored.exchange(0);
        if (!pending)
            return;
        const auto start = std::chrono::steady_clock::now();
        std::vector<char> blob;
        {
            std::unique_lock lock(access);
            blob.resize(library->GetSerializedSize());
            if (blob.empty() || FAILED(library->Serialize(blob.data(), blob.size())))
            {
                stored += pending;
                return;
            }
        }
        const auto serialized = std::chrono::steady_clock::now();
        const auto tmp = Path().string() + ".tmp";
        { std::ofstream(tmp, std::ios::binary).write(blob.data(), std::streamsize(blob.size())); }
        std::error_code ec;
        std::filesystem::rename(tmp, Path(), ec);
        if (ec)
            std::filesystem::remove(tmp, ec);
        if (getenv("REGPU_PERF"))
            Log("perf: pipeline library saved in the background (%.0f MB): serialize %.0f ms, write %.0f ms", blob.size() / 1048576.0,
                std::chrono::duration<double, std::milli>(serialized - start).count(),
                std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - serialized).count());
    }
} g_pipelineLibrary;

void InitPipelineLibrary(ID3D12Device* device)
{
    g_pipelineLibrary.Init(device);
    g_manifest.Load();
    ShaderScanStart();
}

struct PipelinePool
{
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<std::function<void()>> queue;

    PipelinePool()
    {
        const unsigned n = std::clamp(std::thread::hardware_concurrency() * 3 / 4, 2u, 12u);
        for (unsigned i = 0; i < n; i++)
            std::thread([this] { Run(); }).detach();
    }
    void Push(std::function<void()> job)
    {
        {
            std::lock_guard lock(mutex);
            queue.push_back(std::move(job));
        }
        cv.notify_one();
    }
    void Run()
    {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
        for (;;)
        {
            std::function<void()> job;
            {
                std::unique_lock lock(mutex);
                cv.wait(lock, [this] { return !queue.empty(); });
                job = std::move(queue.front());
                queue.pop_front();
            }
            job();
        }
    }
};

std::shared_ptr<PipelineEntry> EnsurePipeline(const CompiledShader& vs, const CompiledShader& ps, const PipelineKey& key)
{
    if (auto it = g_r.pipelines.find(key); it != g_r.pipelines.end())
        return it->second;
    const uint32_t blend = key.blend, depth = key.depth;
    const uint32_t mask = (key.stencilMaskMode >> 24) & 0xF, mode = (key.stencilMaskMode >> 28) & 7, stencil = key.stencilMaskMode & 0xFFFFFF;
    const bool alphaToMask = key.flags & 1, cullable = (key.flags >> 1) & 1;
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd{};
    pd.pRootSignature = g_r.rootSignature.Get();
    pd.VS = { vs.code->GetBufferPointer(), vs.code->GetBufferSize() };
    pd.PS = { ps.code->GetBufferPointer(), ps.code->GetBufferSize() };
    auto& rt = pd.BlendState.RenderTarget[0];
    const uint32_t srcC = blend & 31, opC = (blend >> 5) & 7, dstC = (blend >> 8) & 31;
    const uint32_t srcA = (blend >> 16) & 31, opA = (blend >> 21) & 7, dstA = (blend >> 24) & 31;
    rt.BlendEnable = !(srcC == 1 && dstC == 0 && opC == 0 && srcA == 1 && dstA == 0 && opA == 0);
    rt.SrcBlend = Blend(srcC);
    rt.DestBlend = Blend(dstC);
    rt.BlendOp = BlendOp(opC);
    rt.SrcBlendAlpha = AlphaBlend(srcA);
    rt.DestBlendAlpha = AlphaBlend(dstA);
    rt.BlendOpAlpha = BlendOp(opA);
    rt.RenderTargetWriteMask = UINT8(mask);
    // (One sample: dithered in the pixel shader instead, see DrawImpl, unless REGPU_NO_ALPHA_DITHER.)
    pd.BlendState.AlphaToCoverageEnable = alphaToMask && (g_msaa > 1 || getenv("REGPU_NO_ALPHA_DITHER"));
    pd.SampleMask = UINT_MAX;
    pd.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    pd.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    pd.RasterizerState.DepthClipEnable = (key.flags >> 2) & 1 ? FALSE : TRUE;
    if (key.biasScale | key.biasOffset)
    {
        float scale, offset;
        memcpy(&scale, &key.biasScale, 4);
        memcpy(&offset, &key.biasOffset, 4);
        // As Xenia: the scale is in 1/16ths of the slope; the offset in depth units, taken as 2^-24
        // each (a 32-bit float depth's step between 0.5 and 1).
        pd.RasterizerState.SlopeScaledDepthBias = scale / 16.0f;
        pd.RasterizerState.DepthBias = INT(std::clamp(double(offset) * double(1 << 24), -2147483647.0, 2147483647.0));
    }
    pd.DepthStencilState.DepthEnable = (depth >> 1) & 1;
    pd.DepthStencilState.DepthWriteMask = ((depth >> 2) & 1) ? D3D12_DEPTH_WRITE_MASK_ALL : D3D12_DEPTH_WRITE_MASK_ZERO;
    pd.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC(((depth >> 4) & 7) + 1);
    // Stencil: Xenos ops (the D3D9 order) 0 keep, 1 zero, 2 replace, 3 incr clamp, 4 decr clamp,
    // 5 invert, 6 incr wrap, 7 decr wrap. Back faces use their own ops when enabled.
    static const D3D12_STENCIL_OP kOps[8] = { D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_ZERO, D3D12_STENCIL_OP_REPLACE, D3D12_STENCIL_OP_INCR_SAT,
        D3D12_STENCIL_OP_DECR_SAT, D3D12_STENCIL_OP_INVERT, D3D12_STENCIL_OP_INCR, D3D12_STENCIL_OP_DECR };
    static LiveSwitch noStencil("REGPU_NO_STENCIL");
    if ((depth & 1) && !noStencil)
    {
        auto& ds = pd.DepthStencilState;
        ds.StencilEnable = TRUE;
        ds.StencilReadMask = UINT8(stencil >> 8);
        ds.StencilWriteMask = UINT8(stencil >> 16);
        auto faceOps = [&](uint32_t shift) {
            D3D12_DEPTH_STENCILOP_DESC op{};
            op.StencilFunc = D3D12_COMPARISON_FUNC(((depth >> shift) & 7) + 1);
            op.StencilFailOp = kOps[(depth >> (shift + 3)) & 7];
            op.StencilPassOp = kOps[(depth >> (shift + 6)) & 7];
            op.StencilDepthFailOp = kOps[(depth >> (shift + 9)) & 7];
            return op;
        };
        ds.FrontFace = faceOps(8);
        // Rectangles and point sprites aren't polygons to the Xenos: they have no back face (the facing
        // test applies to triangles only), so their host triangles take the front ops too. A rectangle
        // list drawn clockwise with two-sided stencil (a depth reset under a windscreen: NOTEQUAL front,
        // ALWAYS back) otherwise applied the back ops and reset the whole screen. From the FM4 port.
        // REGPU_RECT_BACK_STENCIL=1: the facing decides (A/B; read when a pipeline is built).
        static LiveSwitch rectBackStencil("REGPU_RECT_BACK_STENCIL");
        ds.BackFace = (depth >> 7) & 1 && (cullable || rectBackStencil) ? faceOps(20) : ds.FrontFace;
    }
    // Culling: bit 0 front, bit 1 back; bit 2 set means clockwise faces are front.
    static LiveSwitch flip("REGPU_CULL_FLIP");
    pd.RasterizerState.FrontCounterClockwise = ((mode & 4) == 0) != flip;
    // Only real triangle primitives keep the guest winding; generated geometry
    // (rectangle corners, point sprites) is never culled.
    static LiveSwitch noCull("REGPU_NO_CULL"); // A/B
    pd.RasterizerState.CullMode = !cullable || noCull ? D3D12_CULL_MODE_NONE
        : (mode & 3) == 1 ? D3D12_CULL_MODE_FRONT : (mode & 3) == 2 ? D3D12_CULL_MODE_BACK : D3D12_CULL_MODE_NONE;
    pd.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pd.IBStripCutValue = key.flags & 0x100 ? D3D12_INDEX_BUFFER_STRIP_CUT_VALUE_0xFFFF : D3D12_INDEX_BUFFER_STRIP_CUT_VALUE_DISABLED;
    pd.NumRenderTargets = 1;
    pd.RTVFormats[0] = kColorFormat;
    pd.DSVFormat = DXGI_FORMAT_D32_FLOAT_S8X24_UINT;
    pd.SampleDesc.Count = g_msaa;
    const bool motion = key.flags & 4; // motion-vector variants: a second target for the motion
    if (motion)
    {
        pd.pRootSignature = g_r.motionRootSignature.Get();
        pd.NumRenderTargets = 2;
        pd.RTVFormats[1] = DXGI_FORMAT_R16G16_FLOAT;
        pd.BlendState.IndependentBlendEnable = TRUE;
        auto& mv = pd.BlendState.RenderTarget[1];
        mv = D3D12_RENDER_TARGET_BLEND_DESC{};
        // Blended draws (glass, smoke) keep the motion of what is behind them.
        mv.RenderTargetWriteMask = rt.BlendEnable ? 0 : D3D12_COLOR_WRITE_ENABLE_RED | D3D12_COLOR_WRITE_ENABLE_GREEN;
    }
    auto entry = std::make_shared<PipelineEntry>();
    g_r.pipelines[key] = entry;
    if (!motion && !IsPsEditAlias(key.ps) && !IsPsEditAlias(key.vs)) // the manifest rebuilds pipelines from the base shaders (not live edits)
    {
        g_manifest.RecordShader(false, key.vs, vs.ucodeBE);
        g_manifest.RecordShader(true, key.ps, ps.ucodeBE);
        g_manifest.RecordPipeline(key);
    }
    static PipelinePool pool;
    char label[64];
    snprintf(label, sizeof(label), "pipeline %08llx/%08llx", (unsigned long long)(key.vs >> 32), (unsigned long long)(key.ps >> 32));
    g_stats.Begin(label);
    // (A plain pointer back to the entry: pipelines are never removed.)
    entry->build =[e = entry.get(), pd, vsKeep = vs.code, psKeep = ps.code, key]() mutable {
        PipelineEntry* entry = e;
        const auto createStart = std::chrono::steady_clock::now();
        bool hit = false;
        const HRESULT hr = g_pipelineLibrary.CreateOrLoad(g_r.device, pd, key, entry->pso, &hit);
        const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - createStart).count();
        if (ms > 30.0 && getenv("REGPU_PERF"))
            Log("perf: pipeline %016llx/%016llx took %.1f ms to create (background)", (unsigned long long)key.vs, (unsigned long long)key.ps, ms);
        if (FAILED(hr))
            Log("renderer: pipeline %016llx/%016llx failed", (unsigned long long)key.vs, (unsigned long long)key.ps);
        (hit ? g_stats.pipelinesLoaded : g_stats.pipelinesBuilt)++;
        entry->state.store(FAILED(hr) ? 2 : 1, std::memory_order_release);
        g_stats.End(!hit);
    };
    pool.Push([entry] { entry->Build(); });
    g_pipelinesCreated++;
    g_frameEvents.pipelines++;
    return entry;
}

void WaitHoldingImpl(const std::function<bool()>& done);

// Waits for the build (draws are never dropped: a missing pipeline used to leave
// shadow passes undrawn). Cold builds can take 100+ ms; the overlay says so.
// `force`: wait even with REGPU_ASYNC_PIPELINES (draws a pass cannot lose, see SkippableDraw).
ID3D12PipelineState* WaitForPipeline(const std::shared_ptr<PipelineEntry>& entry, bool force = false)
{
    static LiveSwitch async("REGPU_ASYNC_PIPELINES");
    if (async && !force && entry->state.load(std::memory_order_acquire) == 0)
        return nullptr;
    // Not started yet: built here, now. Queued behind the pool's other builds, a draw that must wait
    // for its pipeline waited for all of them: on the FX (a cold cache entering FH1's world) over 20 s,
    // and the title never queued work again. REGPU_NO_INLINE_PIPELINE=1: wait in the queue, as before.
    if (static LiveSwitch noInline("REGPU_NO_INLINE_PIPELINE"); !noInline)
        entry->Build();
    WaitHoldingImpl([&] { return entry->state.load(std::memory_order_acquire) != 0; });
    return entry->state.load() == 1 ? entry->pso.Get() : nullptr;
}

PipelineKey MakePipelineKey(uint64_t vsHash, uint64_t psHash, bool depthOnly, bool cullable)
{
    const uint32_t blend = gpureg::Get(RB_BLENDCONTROL0);
    uint32_t depth = gpureg::Get(RB_DEPTHCONTROL);
    // Debug: REGPU_NO_ZTEST=a-b turns the depth test off for draws a..b-1 of the REGPU_DUMP_FRAME frame.
    static uint32_t noZFrom = 0, noZTo = 0;
    static const bool noZParsed = [] { if (const char* s = getenv("REGPU_NO_ZTEST")) sscanf(s, "%u-%u", &noZFrom, &noZTo); return true; }();
    static const uint64_t noZFrame = getenv("REGPU_DUMP_FRAME") ? strtoull(getenv("REGPU_DUMP_FRAME"), nullptr, 10) : 0;
    (void)noZParsed;
    if (noZTo && g_r.frame + 1 == noZFrame && g_r.drawsThisFrame >= noZFrom && g_r.drawsThisFrame < noZTo)
        depth &= ~(getenv("REGPU_NO_ZWRITE") ? 4u : 2u);
    static LiveSwitch zFuncAlways("REGPU_ZFUNC_ALWAYS");
    if (zFuncAlways)
        depth |= 0x70; // debug: depth func ALWAYS for every draw
    const uint32_t mask = depthOnly ? 0 : gpureg::Get(RB_COLOR_MASK) & 0xF;
    // Stencil masks (not the reference, which is dynamic) and culling are pipeline state.
    const uint32_t stencil = gpureg::Get(RB_STENCILREFMASK) & 0xFFFF00, mode = gpureg::Get(PA_SU_SC_MODE_CNTL) & 7;
    // Alpha-to-mask (RB_COLORCONTROL bit 4): cut-out foliage and fences.
    const bool alphaToMask = (gpureg::Get(RB_COLORCONTROL) >> 4) & 1;
    // CLIP_DISABLE (PA_CL_CLIP_CNTL bit 16): no near/far clipping, the depth clamped instead, as
    // Xenia does. FH1's sky dome lies on the far plane: with depth clipping, fragments a little
    // past it were dropped as the camera moved, and the holes showed the clear colour (grey shapes
    // in the sky). REGPU_DEPTH_CLIP_ALWAYS=1: clipped as before (A/B).
    static LiveSwitch depthClipAlways("REGPU_DEPTH_CLIP_ALWAYS");
    const bool noDepthClip = !depthClipAlways && ((gpureg::Get(0x2204 /* PA_CL_CLIP_CNTL */) >> 16) & 1);
    // Polygon offset (PA_SU_SC_MODE_CNTL bits 11 front, 12 back; PA_SU_POLY_OFFSET_FRONT/BACK_SCALE,
    // _OFFSET at 0x2380-0x2383): FH1's shadow maps are drawn with a slope scale of 16 (in 1/16ths) and
    // an offset of 5e-5. Without it lit surfaces shadowed themselves (shadow acne): the parked car's
    // rear and roof came out 15% darker than stock, mottled. Front values for both faces (FH1 sets
    // them equal). REGPU_NO_POLY_OFFSET=1: none (A/B).
    static LiveSwitch noPolyOffset("REGPU_NO_POLY_OFFSET");
    const uint32_t sc = gpureg::Get(PA_SU_SC_MODE_CNTL);
    const bool polyOffset = !noPolyOffset && ((depth >> 1) & 1) && ((sc >> 11) & 3);
    const uint32_t biasScale = polyOffset ? gpureg::Get((sc >> 11) & 1 ? 0x2380 : 0x2382) : 0;
    // The constant part goes to the vertex shader (PolygonOffsetDepth), unless REGPU_FIXED_POLY_OFFSET=1.
    static LiveSwitch fixedOffset("REGPU_FIXED_POLY_OFFSET");
    uint32_t biasOffset = polyOffset && fixedOffset ? RoundedBias(gpureg::Get((sc >> 11) & 1 ? 0x2381 : 0x2383)) : 0;
    // REGPU_POLY_OFFSET_SCALE=<f>: the constant offset times f (debug; read per draw, part of the key).
    thread_local FrameEnv scaleEnv{ "REGPU_POLY_OFFSET_SCALE" };
    if (const char* m = scaleEnv.Get(); m && biasOffset)
    {
        float v;
        memcpy(&v, &biasOffset, 4);
        v *= float(atof(m));
        memcpy(&biasOffset, &v, 4);
    }
    PipelineKey key{ vsHash, psHash, blend, depth, stencil | (mask << 24) | (mode << 28),
        uint32_t(alphaToMask) | (uint32_t(cullable) << 1) | (uint32_t(noDepthClip) << 2) };
    key.biasScale = biasScale;
    key.biasOffset = biasOffset;
    return key;
}

// Alpha-blended colour draws (smoke, sparks, glows) are skippable for the moment it
// takes to build their pipeline; opaque and depth-only draws (shadow maps, geometry)
// are not: dropping those leaves holes or a black ground.
std::chrono::steady_clock::time_point g_lastLongHold{}; // end of the last hold of 2 s or more

// Once a loading hold has finished and the game is running (4 s later), new content that
// shows up first-time (new objects, effects) builds in the background and the
// draw is skipped for the half second that takes, instead of freezing every frame.
bool GameSettled()
{
    static LiveSwitch never("REGPU_NO_SETTLED_SKIP");
    return !never && g_lastLongHold.time_since_epoch().count() != 0 && std::chrono::steady_clock::now() - g_lastLongHold > std::chrono::seconds(4);
}

// Set by the draw: a rectangle list without textures (a clear, a stencil reset, a mask fill).
thread_local bool g_plainRectDraw = false;
bool PlainRectMustDraw()
{
    static LiveSwitch skipPlainRects("REGPU_SKIP_PLAIN_RECTS"); // 1: skippable like the rest, as before
    return g_plainRectDraw && !skipPlainRects;
}

bool SkippableDraw(bool depthOnly, uint32_t blend)
{
    static LiveSwitch noSkip("REGPU_NO_SKIP_BLENDED");
    if (noSkip || depthOnly)
        return false;
    // Not these: the pipelines are tiny and a dropped clear or reset leaves the last frame's stencil or colour
    // for later passes to draw through (FH1's minimap came out as white blobs for a moment when the world
    // loaded: 27 such draws were dropped while their pipelines built, the first frame after the load).
    // REGPU_SKIP_PLAIN_RECTS=1: skippable like the rest, as before.
    if (PlainRectMustDraw())
        return false;
    const bool opaque = (blend & 0xFF1F1F1F) == 0x00010001 || (blend & 0x1F) == 1 && ((blend >> 8) & 0x1F) == 0;
    return !opaque || GameSettled();
}

ID3D12PipelineState* GetPipeline(const CompiledShader& vs, const CompiledShader& ps, bool depthOnly, bool cullable, bool motion = false, bool stripCut = false,
    bool coverage = false)
{
    PipelineKey key = MakePipelineKey(C().vsHash, C().psHash, depthOnly, cullable);
    if (coverage)
    {
        // An occlusion query's coverage draw: the depth test always passes, nothing is written.
        key.depth = (key.depth & ~0x77u) | 0x72u;
        key.stencilMaskMode &= ~(0xFu << 24);
        key.flags |= 0x200;
    }
    if (motion)
        key.flags |= 4;
    if (stripCut)
        key.flags |= 0x100; // strips restart at index 0xFFFF (DrawImpl)
    thread_local std::unordered_map<PipelineKey, ID3D12PipelineState*, PipelineKeyHash> ready; // (pipelines are never removed)
    // Runs of draws share a pipeline: the last one's answer first, before the map.
    thread_local PipelineKey lastKey{};
    thread_local ID3D12PipelineState* lastPso = nullptr;
    if (lastPso && memcmp(&key, &lastKey, sizeof(key)) == 0)
        return lastPso;
    if (auto r = ready.find(key); r != ready.end())
    {
        lastKey = key;
        lastPso = r->second;
        return r->second;
    }
    std::shared_ptr<PipelineEntry> entry;
    {
        const auto lockStart = g_perfTiming ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
        std::lock_guard shaderLock(g_shaderMutex);
        if (g_perfTiming)
            if (const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - lockStart).count(); ms > 2.0)
                Log("perf: GetPipeline waited %.1f ms for the shader lock", ms);
        auto it = g_r.pipelines.find(key);
        if (it != g_r.pipelines.end() && it->second->state.load(std::memory_order_acquire) == 1)
        {
            ready.emplace(key, it->second->pso.Get());
            return it->second->pso.Get();
        }
        entry = EnsurePipeline(vs, ps, key);
    }
    if (SkippableDraw(depthOnly, key.blend) && entry->state.load(std::memory_order_acquire) == 0)
    {
        g_skippedPipeline++;
        return nullptr; // builds in the background; later frames draw it
    }
    g_waitWhat = depthOnly ? "pipeline (depth only)" : "pipeline";
    g_waitBlend = key.blend;
    const auto waitStart = g_perfTiming ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    ID3D12PipelineState* pso = WaitForPipeline(entry, PlainRectMustDraw());
    if (g_perfTiming)
        if (const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - waitStart).count(); ms > 2.0)
            Log("perf: GetPipeline waited %.1f ms for pipeline %016llx/%016llx (state was %d)", ms, (unsigned long long)key.vs, (unsigned long long)key.ps, 0);
    return pso;
}

// Cube textures: six faces, each a padded 2D slice. Faces the title rendered
// and resolved (dynamic reflections) are copied from their host textures every
// frame; static faces are decoded once and re-decoded when their data changes.
// A 3D texture, decoded on the CPU (DecodeTexture3D) and uploaded as RGBA8 when its contents change.
ID3D12Resource* GetVolumeTexture(const TextureFetch& fetch, DXGI_FORMAT& format)
{
    TextureScope textureScope;
    static LiveSwitch noVolumes("REGPU_NO_3D_TEXTURES"); // A/B: the white fallback
    const uint32_t w = fetch.Width(), h = fetch.Height(), d = fetch.Depth();
    if (noVolumes || w > 2048 || h > 2048 || d > 256 || !TextureFormatSupported(fetch.Format()))
    {
        NoteTextureFailure("3D texture not supported", fetch);
        return nullptr;
    }
    CachedTexture& cached = g_r.volumes[TextureKey(fetch.Address(), fetch.Format(), w, h) ^ (uint64_t(d) << 52)];
    if (cached.resource && cached.checkedFrame == g_r.frame)
    {
        format = cached.format;
        return cached.resource.Get();
    }
    cached.checkedFrame = g_r.frame;
    format = DXGI_FORMAT_R8G8B8A8_UNORM;
    const uint8_t* source = GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address()));
    const uint64_t hash = ContentHash(source, TextureSourceSize(fetch)) ^ (uint64_t(fetch.Swizzle()) << 8) ^ (uint64_t(fetch.Endian()) << 44);
    if (cached.resource && cached.contentHash == hash)
        return cached.resource.Get();
    if (!cached.resource || cached.width != w || cached.height != h || cached.resource->GetDesc().DepthOrArraySize != d)
    {
        D3D12_HEAP_PROPERTIES hp{};
        hp.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC td{};
        td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE3D;
        td.Width = w;
        td.Height = h;
        td.DepthOrArraySize = UINT16(d);
        td.MipLevels = 1;
        td.SampleDesc.Count = 1;
        td.Format = format;
        if (cached.resource)
            Frame().garbage.push_back(std::move(cached.resource));
        Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                  D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, nullptr,
                  IID_PPV_ARGS(&cached.resource)),
            "CreateCommittedResource(3D)"); CountCreate("3D texture");
        cached.width = w;
        cached.height = h;
        cached.format = format;
    }
    std::vector<uint8_t> rgba;
    if (!DecodeTexture3D(fetch, rgba))
        return nullptr;
    const UINT rowPitch = (w * 4 + 255) & ~255u;
    const UINT64 offset = RingAlloc(UINT64(rowPitch) * h * d, 512);
    if (offset == ~0ull)
    {
        NoteTextureFailure("upload ring full (3D)", fetch);
        return nullptr;
    }
    for (uint32_t z = 0; z < d; z++)
        for (uint32_t y = 0; y < h; y++)
            memcpy(Frame().ringCpu + offset + (UINT64(z) * h + y) * rowPitch, &rgba[(size_t(z) * h + y) * w * 4], size_t(w) * 4);
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = cached.resource.Get();
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    C().list->ResourceBarrier(1, &b);
    D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
    dst.pResource = cached.resource.Get();
    dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    src.pResource = Frame().ring.Get();
    src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    src.PlacedFootprint.Offset = offset;
    src.PlacedFootprint.Footprint = { format, w, h, d, rowPitch };
    C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    C().list->ResourceBarrier(1, &b);
    cached.contentHash = hash;
    return cached.resource.Get();
}

ResolvedTexture* ResolveCurrent(uint32_t base);

ID3D12Resource* GetCubeTexture(const TextureFetch& fetch, DXGI_FORMAT& format)
{
    TextureScope textureScope;
    const uint32_t w = fetch.Width(), h = fetch.Height();
    if (w != h || w > 2048)
        return nullptr;
    const uint32_t stride = TextureFaceStride(fetch);
    CachedTexture& cached = g_r.cubes[TextureKey(fetch.Address(), fetch.Format(), w, h)];
    if (cached.resource && cached.checkedFrame == g_r.frame)
    {
        format = cached.format;
        return cached.resource.Get();
    }
    cached.checkedFrame = g_r.frame;

    // Faces the title resolved into?
    const ResolvedTexture* faces[6]{};
    bool resolved = false;
    for (uint32_t i = 0; i < 6; i++)
    {
        // (Through the stale check: a face address the title reused for a texture of its own.)
        const ResolvedTexture* r = ResolveCurrent(fetch.Address() + i * stride);
        if (r && r->format != DXGI_FORMAT_R32_FLOAT && r->width >= w && r->height >= h)
        {
            faces[i] = r;
            resolved = true;
        }
    }
    DXGI_FORMAT want = DXGI_FORMAT_R8G8B8A8_UNORM;
    for (uint32_t i = 0; i < 6 && resolved; i++)
        if (faces[i])
        {
            want = faces[i]->format;
            break;
        }
    if (resolved)
    {
        cached.everLive = true;
        cached.lastLiveFrame = g_r.frame;
    }
    // A cube the title renders that has no live face for a moment (its resolves late, stale or replaced) was read from
    // guest memory, which holds no picture of it: the paint reflected an empty cube (FM4 port: purple, no reflections).
    // The last live picture stays for up to 180 frames instead. REGPU_NO_CUBE_KEEP=1 (live): the old behaviour (A/B).
    static LiveSwitch noKeep("REGPU_NO_CUBE_KEEP");
    if (!resolved && cached.resource && cached.everLive && !noKeep && g_r.frame - cached.lastLiveFrame < 180)
    {
        format = cached.format;
        return cached.resource.Get();
    }
    // Faces the title rendered are at the render scale; the cube matches them (their own host
    // size: the current pass's scale differs when the cube is first fetched in another pass,
    // which rebuilt the cube mid-level, a 4-5 ms draw).
    uint32_t cw = w, ch = h;
    for (uint32_t i = 0; i < 6 && resolved; i++)
        if (faces[i] && faces[i]->format == want && faces[i]->hostWidth && faces[i]->width)
        {
            cw = uint32_t(std::lround(double(w) * faces[i]->hostWidth / faces[i]->width));
            ch = uint32_t(std::lround(double(h) * faces[i]->hostHeight / faces[i]->height));
            break;
        }
    // Cube maps read from memory keep their mip levels (each level holds all six faces, padded as a
    // 2D level is; the packed tail of 16x16 and below is not read). Resolved cubes have one level.
    static LiveSwitch noCubeMips("REGPU_NO_CUBE_MIPS"); // A/B
    UINT16 mips = 1;
    if (!resolved && !noCubeMips && fetch.MipAddress() && fetch.Tiled() && !(w & (w - 1)))
        for (uint32_t l = 1; l <= fetch.MipMaxLevel() && (w >> l) > 16; l++)
            mips++;
    if (!cached.resource || cached.width != cw || cached.height != ch || cached.format != want || cached.resource->GetDesc().MipLevels != mips)
    {
        D3D12_HEAP_PROPERTIES hp{};
        hp.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC td{};
        td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        td.Width = cw;
        td.Height = ch;
        td.DepthOrArraySize = 6;
        td.MipLevels = mips;
        td.SampleDesc.Count = 1;
        td.Format = want;
        if (cached.resource)
            Frame().garbage.push_back(std::move(cached.resource));
        Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                  D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, nullptr,
                  IID_PPV_ARGS(&cached.resource)),
            "CreateCommittedResource(cube)"); CountCreate("cube");
        cached.width = cw;
        cached.height = ch;
        cached.format = want;
        cached.contentHash = 0;
    }
    format = want;

    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = cached.resource.Get();
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;

    if (resolved)
    {
        D3D12_RESOURCE_BARRIER fb[6]{};
        UINT n = 0;
        for (uint32_t i = 0; i < 6; i++)
        {
            if (!faces[i] || faces[i]->format != want)
                continue;
            fb[n].Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
            fb[n].Transition.pResource = faces[i]->resource.Get();
            fb[n].Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
            fb[n].Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
            n++;
        }
        C().list->ResourceBarrier(1, &b);
        if (n)
            C().list->ResourceBarrier(n, fb);
        // REGPU_CUBE_Y_SWAP=1: the title's +Y and -Y faces trade places. FH1's live car reflections
        // came out with them exchanged against Xenia (whose face order and cube coordinates match
        // ours; the faces' own contents differ), and the glass reflected the sunlit ground: green after
        // the shader squares it. Why our resolves of those two faces differ is not known yet; opt-in
        // until it is.
        static LiveSwitch swapY("REGPU_CUBE_Y_SWAP");
        for (uint32_t i = 0; i < 6; i++)
        {
            if (!faces[i] || faces[i]->format != want)
                continue;
            D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
            dst.pResource = cached.resource.Get();
            dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
            dst.SubresourceIndex = (swapY && (i == 2 || i == 3) ? 5 - i : i) * mips;
            src.pResource = faces[i]->resource.Get();
            src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
            const D3D12_BOX box{ 0, 0, 0, std::min(cw, faces[i]->hostWidth), std::min(ch, faces[i]->hostHeight), 1 };
            C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, &box);
        }
        for (UINT k = 0; k < n; k++)
            std::swap(fb[k].Transition.StateBefore, fb[k].Transition.StateAfter);
        if (n)
            C().list->ResourceBarrier(n, fb);
    }
    else
    {
        // Static: hash all six faces, decode on change.
        const uint8_t* source = GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address()));
        uint64_t hash = ContentHash(source, size_t(stride) * 6) ^ (uint64_t(fetch.Swizzle()) << 8) ^ (uint64_t(fetch.Endian()) << 44) ^ (uint64_t(mips) << 52);
        // Each level's face address: level 0 at the base, levels 1+ from the mip address, every level
        // holding its six faces one after another.
        uint32_t levelBase[16]{}, levelStride[16]{};
        levelBase[0] = fetch.Address();
        levelStride[0] = stride;
        for (UINT16 l = 1, address = 0; l < mips; l++)
        {
            TextureFetch level = fetch;
            level.dwords[0] &= ~(0x1FFu << 22);
            level.dwords[2] = (level.dwords[2] & ~0x3FFFFFFu) | ((w >> l) - 1) | (((h >> l) - 1) << 13);
            levelBase[l] = (l == 1 ? fetch.MipAddress() : levelBase[l - 1] + levelStride[l - 1] * 6);
            levelStride[l] = TextureFaceStride(level);
            (void)address;
        }
        if (mips > 1)
            hash ^= ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(levelBase[1])), size_t(levelBase[mips - 1] - levelBase[1]) + size_t(levelStride[mips - 1]) * 6) * 31;
        if (cached.contentHash == hash)
            return cached.resource.Get();
        const UINT rowPitch = (w * 4 + 255) & ~255u;
        const UINT64 faceBytes = (UINT64(rowPitch) * h + 511) & ~511ull;
        const UINT64 offset = RingAlloc(faceBytes * 6 * 2, 512); // all levels fit in twice level 0
        if (offset == ~0ull)
            return nullptr;
        C().list->ResourceBarrier(1, &b);
        UINT64 at = offset;
        for (uint32_t i = 0; i < 6; i++)
            for (UINT16 l = 0; l < mips; l++)
        {
            const uint32_t lw = std::max(w >> l, 1u), lh = std::max(h >> l, 1u);
            TextureFetch face = fetch;
            face.dwords[1] = (face.dwords[1] & 0xFFF) | ((levelBase[l] + i * levelStride[l]) & 0xFFFFF000);
            face.dwords[5] = (face.dwords[5] & ~(3u << 9)) | (1u << 9); // decode as 2D
            if (l)
            {
                face.dwords[0] &= ~(0x1FFu << 22); // pitch: the level's own width
                face.dwords[2] = (face.dwords[2] & ~0x3FFFFFFu) | (lw - 1) | ((lh - 1) << 13);
            }
            std::vector<uint8_t> rgba;
            if (!DecodeTexture(face, rgba))
                rgba.assign(size_t(lw) * lh * 4, 0);
            if (l)
            {
                const UINT lPitch = (lw * 4 + 255) & ~255u;
                for (uint32_t y = 0; y < lh; y++)
                    memcpy(Frame().ringCpu + at + UINT64(y) * lPitch, &rgba[size_t(y) * lw * 4], size_t(lw) * 4);
                D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
                dst.pResource = cached.resource.Get();
                dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
                dst.SubresourceIndex = l + i * mips;
                src.pResource = Frame().ring.Get();
                src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
                src.PlacedFootprint.Offset = at;
                src.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, lw, lh, 1, lPitch };
                C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
                at += (UINT64(lPitch) * lh + 511) & ~511ull;
                continue;
            }
            if (getenv("REGPU_DUMP_CUBES"))
            {
                char name[80];
                std::filesystem::create_directories("out/cube");
                snprintf(name, sizeof(name), "out/cube/%08X_face%u_stride%X.png", fetch.Address(), i, stride);
                WritePng(name, w, h, rgba);
            }
            for (uint32_t y = 0; y < h; y++)
                memcpy(Frame().ringCpu + offset + faceBytes * i + UINT64(y) * rowPitch, &rgba[size_t(y) * w * 4], size_t(w) * 4);
            D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
            dst.pResource = cached.resource.Get();
            dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
            dst.SubresourceIndex = i * mips;
            src.pResource = Frame().ring.Get();
            src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
            src.PlacedFootprint.Offset = at;
            src.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, w, h, 1, rowPitch };
            C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        }
        cached.contentHash = hash;
    }
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    C().list->ResourceBarrier(1, &b);
    return cached.resource.Get();
}

// A render target the title resolved together with its mip chain (it
// downsamples and resolves each level itself): levels 1+ follow each other
// from the fetch constant's mip address, each padded to 32x32 texels and
// 4 KB. Assembled into one mipmapped host texture every frame it is used.
ID3D12Resource* ResolvedMipChain(const TextureFetch& fetch, const ResolvedTexture& level0)
{
    TextureScope textureScope;
    const uint32_t w = std::min(fetch.Width(), level0.width), h = std::min(fetch.Height(), level0.height);
    const UINT bpp = level0.format == DXGI_FORMAT_R8G8B8A8_UNORM ? 4 : 8;
    std::vector<const ResolvedTexture*> levels{ &level0 };
    uint32_t address = fetch.MipAddress();
    for (uint32_t i = 1; i <= fetch.MipMaxLevel() && (w >> i || h >> i); i++)
    {
        const uint32_t lw = std::max(w >> i, 1u), lh = std::max(h >> i, 1u);
        const ResolvedTexture* r = ResolveCurrent(address);
        if (!r || r->format != level0.format || r->width < lw || r->height < lh)
            break;
        levels.push_back(r);
        const uint32_t bytes = ((lw + 31) & ~31u) * ((lh + 31) & ~31u) * bpp;
        address += std::max((bytes + 4095) & ~4095u, 4096u);
    }
    if (levels.size() < 2)
        return nullptr;
    // The levels are host textures (scaled with the render resolution): the chain is too.
    // (At game size it held each level's top-left corner only: one title's level-wide
    // shadow map read from the wrong place, black patches on the road.)
    const uint32_t hw = std::max(uint32_t(uint64_t(level0.hostWidth) * w / std::max(level0.width, 1u)), 1u);
    const uint32_t hh = std::max(uint32_t(uint64_t(level0.hostHeight) * h / std::max(level0.height, 1u)), 1u);

    const UINT count = UINT(levels.size());
    // One per shape too: a base fetched with two sizes or level counts in a frame recreated its chain
    // at each change (PGR4's races: 160 textures created a second).
    auto& chain = g_r.mipChains[uint64_t(fetch.Address()) << 32 ^ (uint64_t(hw) << 18 ^ uint64_t(hh) << 4 ^ count) * 0x9E3779B1u ^ uint64_t(level0.format)];
    // Assembled again whenever one of its levels has been resolved since, not once per frame: a fetch
    // reads memory as it is at the draw (one title blurs a shadow in two transposed passes, both
    // resolved to level 0, and the chain built after the first was kept: the shadow came out
    // sideways). From the FM4 port. REGPU_MIP_CHAIN_PER_FRAME=1: once per frame (A/B).
    static LiveSwitch perFrame("REGPU_MIP_CHAIN_PER_FRAME");
    uint64_t sources = count;
    for (const ResolvedTexture* level : levels)
        sources = (sources ^ level->serial) * 0x100000001B3ull;
    if (chain.resource && (perFrame ? chain.checkedFrame == g_r.frame : chain.contentHash == sources))
        return chain.resource.Get();
    chain.checkedFrame = g_r.frame;
    chain.contentHash = sources;
    if (!chain.resource || chain.width != hw || chain.height != hh || chain.format != level0.format ||
        chain.resource->GetDesc().MipLevels != count)
    {
        D3D12_HEAP_PROPERTIES hp{};
        hp.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC td{};
        td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        td.Width = hw;
        td.Height = hh;
        td.DepthOrArraySize = 1;
        td.MipLevels = UINT16(count);
        td.SampleDesc.Count = 1;
        td.Format = level0.format;
        if (chain.resource)
            Frame().garbage.push_back(std::move(chain.resource));
        Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                  D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, nullptr,
                  IID_PPV_ARGS(&chain.resource)),
            "CreateCommittedResource(mip chain)"); CountCreate("mip chain");
        chain.width = hw;
        chain.height = hh;
        chain.format = level0.format;
    }
    std::vector<D3D12_RESOURCE_BARRIER> b(count + 1);
    for (UINT i = 0; i <= count; i++)
    {
        b[i].Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b[i].Transition.pResource = i < count ? levels[i]->resource.Get() : chain.resource.Get();
        b[i].Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b[i].Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
        b[i].Transition.StateAfter = i < count ? D3D12_RESOURCE_STATE_COPY_SOURCE : D3D12_RESOURCE_STATE_COPY_DEST;
    }
    // Several levels can share one host texture only if the title resolved
    // them to the same address, which it does not; still, transition each once.
    C().list->ResourceBarrier(UINT(b.size()), b.data());
    for (UINT i = 0; i < count; i++)
    {
        D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
        dst.pResource = chain.resource.Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        dst.SubresourceIndex = i;
        src.pResource = levels[i]->resource.Get();
        src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        const D3D12_BOX box{ 0, 0, 0, std::min(std::max(hw >> i, 1u), levels[i]->hostWidth), std::min(std::max(hh >> i, 1u), levels[i]->hostHeight), 1 };
        C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, &box);
    }
    for (auto& x : b)
        std::swap(x.Transition.StateBefore, x.Transition.StateAfter);
    C().list->ResourceBarrier(UINT(b.size()), b.data());
    return chain.resource.Get();
}

// The resolved texture a fetch reads, if any. Resolves are never block compressed,
// so a compressed fetch at an address we resolved to earlier is a different image the
// title has since loaded there (menu icons reusing a shadow image's memory).
// A hash of the guest memory a resolved image covers (sampled: it only has to notice new contents).
// (Only the image's own bytes: an 8-bit shadow image hashed as 32-bit took in the memory after it,
// which changed with unrelated game state, and the shadow fell back to that memory as noise.)
uint64_t GuestImageHash(uint32_t base, uint32_t width, uint32_t height, uint32_t bpp)
{
    const size_t bytes = std::min<size_t>(size_t(width) * height * bpp, 4u << 20);
    return ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(base)), std::max<size_t>(bytes, 64), 256);
}

// Records what guest memory holds under a resolved image (after a resolve, or after we write it back).
void NoteResolvedMemory(uint32_t base, ResolvedTexture& r)
{
    r.memHash = GuestImageHash(base, r.width, r.height, r.guestBpp);
    r.memChecked = g_r.frame;
    r.stale = false;
}

// The resolve at a base when it still holds what is in memory there, else nullptr (see ResolvedFor).
ResolvedTexture* ResolveCurrent(uint32_t base)
{
    auto it = g_r.resolved.find(base & 0x1FFFFFFF);
    if (it == g_r.resolved.end())
        return nullptr;
    // A resolve not repeated lately whose memory has changed since: the title reused the address for a
    // texture of its own (menu icons that sat on an old menu resolve showed as white boxes).
    // Checked every 32 frames; a new resolve there makes it current again.
    static LiveSwitch noStaleCheck("REGPU_NO_STALE_RESOLVES"); // A/B
    ResolvedTexture& r = it->second;
    if (!noStaleCheck && r.frame + 8 < g_r.frame && r.frame != ~0ull)
    {
        if (!r.stale && g_r.frame - r.memChecked >= 32)
        {
            r.memChecked = g_r.frame;
            if (GuestImageHash(it->first, r.width, r.height, r.guestBpp) != r.memHash)
            {
                r.stale = true;
                Log("renderer: resolve at %08X (%ux%u) is stale: the memory was reused", it->first, r.width, r.height);
            }
        }
        if (r.stale)
            return nullptr;
    }
    return &r;
}

const ResolvedTexture* ResolvedFor(const TextureFetch& fetch)
{
    const char* name = TextureFormatName(fetch.Format());
    if (name && (strncmp(name, "DXT", 3) == 0 || strncmp(name, "DXN", 3) == 0 || strncmp(name, "CTX", 3) == 0))
        return nullptr;
    return ResolveCurrent(fetch.Address());
}

// The guest bytes a texture check hashes, and how densely.
void TextureHashSpan(const TextureFetch& fetch, uint32_t w, uint32_t h, const uint8_t*& source, size_t& bytes, size_t& stride)
{
    source = GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address()));
    // The whole base level: the old guess (pitch * height / 2) covered only the first half of a
    // one-byte-per-texel texture (DXT5), so a thumbnail filled in later went unnoticed.
    bytes = std::max<size_t>({ size_t(std::max(fetch.Pitch(), w)) * h / 2, TextureSourceSize(fetch), 64 });
    // Only what changes the decoded image: sampler state (clamp, filter) varies per draw.
    // Small textures (icons, thumbnails) are hashed densely, big ones sampled: a change confined
    // to a few dwords must not slip between the samples.
    stride = bytes <= (64u << 10) ? 4 : bytes <= (512u << 10) ? 64 : 256;
}

// Decodes (if changed) and uploads a texture; returns its resource.
ID3D12Resource* GetTextureLocked(const TextureFetch& fetch, DXGI_FORMAT& format, bool& native);
// Textures are checked and uploaded once per frame; each thread remembers what it got this
// frame, so repeated bindings take no lock.
ID3D12Resource* GetTexture(const TextureFetch& fetch, DXGI_FORMAT& format, bool& native)
{
    struct Known
    {
        ID3D12Resource* resource;
        DXGI_FORMAT format;
        bool native;
    };
    thread_local FrameMap<Known> known;
    // (Resolved textures can change within a frame: not cached.)
    const bool cacheable = !ResolvedFor(fetch) && !RendererTracing();
    const uint64_t key = TextureKey(fetch.Address(), fetch.Format(), fetch.Width(), fetch.Height()) ^ uint64_t(fetch.dwords[0]) << 7 ^
        uint64_t(fetch.dwords[3] & 0xFFF) << 41 ^ uint64_t(fetch.dwords[1] & 0xC0) << 50;
    static SharedFrameMap<Known> shared;
    if (cacheable)
    {
        if (const Known* k = known.Find(key, g_r.frame))
        {
            format = k->format;
            native = k->native;
            return k->resource;
        }
        if (Known k; C().worker && shared.Find(key, g_r.frame, k))
        {
            known.Put(key, g_r.frame, k);
            format = k.format;
            native = k.native;
            return k.resource;
        }
    }
    ID3D12Resource* r = GetTextureLocked(fetch, format, native);
    if (cacheable && r)
    {
        known.Put(key, g_r.frame, { r, format, native });
        if (RendererDrawWorkers())
            shared.Put(key, g_r.frame, { r, format, native });
    }
    return r;
}

ID3D12Resource* GetTextureLocked(const TextureFetch& fetch, DXGI_FORMAT& format, bool& native)
{
    TextureScope textureScope;
    native = false;
    format = DXGI_FORMAT_R8G8B8A8_UNORM;
    if (const ResolvedTexture* resolvedTexture = ResolvedFor(fetch))
    {
        // The full-screen scene image rendered this frame: the draw is a screen layer (the
        // post-processing composite, a grid of rectangles). HUD elements sample only atlases
        // and reduced-size buffers (a 320x180 blur of the scene behind their panels).
        // (By the size the draw fetches it at: the HUD samples the scene image too, at 320x180.)
        if (resolvedTexture->frame == g_r.frame && fetch.Width() >= kWidth)
            g_drawSamplesResolve = true;
        // A colour-format fetch of a depth resolve reads the guest's D24S8 words (BlitResolve's pack).
        if (resolvedTexture->format == DXGI_FORMAT_R32_FLOAT && resolvedTexture->packed && fetch.Format() == 6 /* k_8_8_8_8 */)
        {
            format = DXGI_FORMAT_R8G8B8A8_UNORM;
            return resolvedTexture->packed.Get();
        }
        format = resolvedTexture->format;
        if (fetch.MipMaxLevel() > 0 && fetch.MipAddress())
            if (ID3D12Resource* chain = ResolvedMipChain(fetch, *resolvedTexture))
                return chain;
        return resolvedTexture->resource.Get();
    }
    if (fetch.Type() != 2 || fetch.Dimension() != 1 || !TextureFormatSupported(fetch.Format()))
    {
        NoteTextureFailure("not a supported 2D texture", fetch);
        return nullptr;
    }
    const uint32_t w = fetch.Width(), h = fetch.Height();
    auto& cached = g_r.textures[TextureKey(fetch.Address(), fetch.Format(), w, h)];
    LiveSwitch &dumpAll = g_sw_REGPU_DUMP_TEXTURES;
    const bool forceDecode = dumpAll && RendererTracing();
    if (cached.resource && cached.checkedFrame == g_r.frame && !forceDecode)
    {
        format = cached.format;
        native = cached.native;
        return cached.resource.Get();
    }
    // A texture unchanged for many checks (most of them: loaded once) is checked every 4th
    // frame (staggered), not every frame: hashing all textures in use cost ~0.5 ms a frame.
    // Ones that change (video, render-to-texture on the CPU) never get here.
    // Every frame by default: titles that stream textures reuse their memory (PGR4's scenery), and
    // a texture rechecked every 4th or 16th frame showed the previous one there for a few frames.
    // REGPU_TEXTURE_CHECK_STAGGER=1 brings the staggered checks back (A/B; faster).
    LiveSwitch &stagger = g_sw_REGPU_TEXTURE_CHECK_STAGGER;
    const bool checkAlways = !stagger;
    const uint64_t textureKey = TextureKey(fetch.Address(), fetch.Format(), w, h);
    // Unchanged for 300 checks (a few seconds): every 16th frame (a CPU-side update shows up at most
    // ~0.1 s late). REGPU_TEXTURE_CHECK_EVERY4=1 keeps every 4th (A/B).
    LiveSwitch &every4 = g_sw_REGPU_TEXTURE_CHECK_EVERY4;
    const uint64_t checkMask = !every4 && cached.unchanged >= 300 ? 15 : 3;
    // Textures that keep changing (8 times or more) aren't staggered. A paused video's planes sit unchanged
    // long enough to be, each on its own frames, and when it played again its brightness and colour came
    // from different frames for a frame or two (FM2 port, report #15). REGPU_STAGGER_CHANGING=1: as before.
    static LiveSwitch noChangingCheck("REGPU_STAGGER_CHANGING");
    if (cached.resource && !checkAlways && !forceDecode && cached.unchanged >= 30 &&
        (cached.changes < 8 || noChangingCheck) && ((g_r.frame + (textureKey >> 32)) & checkMask) != 0)
    {
        if (((g_r.frame + 1 + (textureKey >> 32)) & checkMask) == 0) // due next frame: hashed ahead
        {
            const uint8_t* source;
            size_t bytes, stride;
            TextureHashSpan(fetch, w, h, source, bytes, stride);
            prehash::Ahead(textureKey, source, bytes, stride);
        }
        cached.checkedFrame = g_r.frame;
        format = cached.format;
        native = cached.native;
        return cached.resource.Get();
    }
    cached.checkedFrame = g_r.frame;
    // DXT formats stay compressed on the GPU (BC1-BC4): no decode, a quarter to an eighth of
    // the upload. D3D12 needs block-aligned sizes; others are decoded to RGBA8.
    LiveSwitch &noNativeBC = g_sw_REGPU_NO_NATIVE_BC;
    const uint32_t kind = TextureBCKind(fetch.Format());
    const uint32_t bc = !noNativeBC && ((w % 4 == 0 && h % 4 == 0) || kind == 6) && !forceDecode ? kind : 0;
    const uint8_t* source;
    size_t sourceBytes, hashStride;
    TextureHashSpan(fetch, w, h, source, sourceBytes, hashStride);
    const uint64_t hash = prehash::Hash(textureKey, source, sourceBytes, hashStride) ^ (bc ? 0 : uint64_t(fetch.Swizzle()) << 8) ^ (uint64_t(fetch.Pitch()) << 24) ^
        (uint64_t(fetch.Tiled()) << 40) ^ (uint64_t(fetch.Endian()) << 44);
    if (cached.resource && cached.contentHash == hash && !forceDecode)
    {
        cached.unchanged++;
        format = cached.format;
        native = cached.native;
        return cached.resource.Get();
    }
    // REGPU_TRACE_TEXCHANGE=1: textures whose contents changed (address, frame, checks unchanged before).
    if (LiveSwitch &traceChange = g_sw_REGPU_TRACE_TEXCHANGE; traceChange && cached.resource)
        Log("texchange: %08X %s %ux%u frame %llu after %u unchanged", fetch.Address(), TextureFormatName(fetch.Format()), w, h,
            (unsigned long long)g_r.frame, cached.unchanged);
    const auto uploadStart = g_perfTiming ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    const bool wasNew = !cached.resource;
    cached.unchanged = 0;
    if (!wasNew)
        cached.changes++;
    struct UploadLog
    {
        std::chrono::steady_clock::time_point start;
        const TextureFetch& fetch;
        bool isNew;
        ~UploadLog()
        {
            if (!g_perfTiming)
                return;
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
            if (ms > 2.0)
                Log("perf: texture %08X %s %ux%u %s took %.1f ms", fetch.Address(), TextureFormatName(fetch.Format()), fetch.Width(), fetch.Height(),
                    isNew ? "new" : "changed", ms);
        }
    } uploadLog{ uploadStart, fetch, wasNew };
    // The title's own mip levels (distant surfaces sample the smaller, averaged ones: without
    // them the road and grass shimmer and look grainy). Down to the packed tail.
    LiveSwitch &noMips = g_sw_REGPU_NO_MIPS; // A/B
    std::vector<TextureFetch> levels{ fetch };
    for (uint32_t l = 1; !noMips && !forceDecode; l++)
    {
        TextureFetch level;
        if (!TextureMipLevel(fetch, l, level))
            break;
        levels.push_back(level);
    }
    const UINT16 mipCount = UINT16(levels.size());
    if (bc)
    {
        static const DXGI_FORMAT bcFormats[] = { DXGI_FORMAT_UNKNOWN, DXGI_FORMAT_BC1_UNORM, DXGI_FORMAT_BC2_UNORM, DXGI_FORMAT_BC3_UNORM,
            DXGI_FORMAT_BC4_UNORM, DXGI_FORMAT_BC5_UNORM, DXGI_FORMAT_R16G16B16A16_FLOAT };
        const DXGI_FORMAT bcFormat = bcFormats[bc];
        // Kind 6 (16_16_16_16_FLOAT) is uncompressed: blocks of one texel, 8 bytes.
        const uint32_t blockBytes = bc == 1 || bc == 4 || bc == 6 ? 8 : 16, bs = bc == 6 ? 1 : 4;
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        if (!cached.resource || cached.width != w || cached.height != h || cached.format != bcFormat || cached.resource->GetDesc().MipLevels != mipCount)
        {
            D3D12_HEAP_PROPERTIES hp{};
            hp.Type = D3D12_HEAP_TYPE_DEFAULT;
            D3D12_RESOURCE_DESC td{};
            td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
            td.Width = w;
            td.Height = h;
            td.DepthOrArraySize = 1;
            td.MipLevels = mipCount;
            td.SampleDesc.Count = 1;
            td.Format = bcFormat;
            RetireTexture(std::move(cached.resource));
            cached.resource = CreateTexture(td, D3D12_RESOURCE_STATE_COPY_DEST, "texture");
            cached.width = w;
            cached.height = h;
            cached.format = bcFormat;
        }
        else
        {
            b.Transition.pResource = cached.resource.Get();
            b.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
            b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
            C().list->ResourceBarrier(1, &b);
        }
        for (UINT16 l = 0; l < mipCount; l++)
        {
            const uint32_t lw = levels[l].Width(), lh = levels[l].Height();
            const UINT rowPitch = (((lw + bs - 1) / bs) * blockBytes + 255) & ~255u;
            const UINT64 offset = RingAlloc(UINT64(rowPitch) * ((lh + bs - 1) / bs), 512);
            if (offset == ~0ull)
            {
                NoteTextureFailure("upload ring full (compressed)", fetch);
                return nullptr;
            }
            CopyTextureBlocks(levels[l], Frame().ringCpu + offset, rowPitch);
            D3D12_TEXTURE_COPY_LOCATION dst{};
            dst.pResource = cached.resource.Get();
            dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
            dst.SubresourceIndex = l;
            D3D12_TEXTURE_COPY_LOCATION src{};
            src.pResource = Frame().ring.Get();
            src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
            src.PlacedFootprint.Offset = offset;
            src.PlacedFootprint.Footprint = { bcFormat, (lw + bs - 1) / bs * bs, (lh + bs - 1) / bs * bs, 1, rowPitch };
            C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        }
        b.Transition.pResource = cached.resource.Get();
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
        C().list->ResourceBarrier(1, &b);
        cached.contentHash = hash;
        cached.native = true;
        format = bcFormat;
        native = true;
        return cached.resource.Get();
    }

    std::vector<uint8_t> rgba;
    if (!DecodeTexture(fetch, rgba))
    {
        NoteTextureFailure("decode failed", fetch);
        return nullptr;
    }
    if (RendererTracing())
        Log("texture upload: %08X %s %ux%u (%s)", fetch.Address(), TextureFormatName(fetch.Format()), w, h, cached.resource ? "changed" : "new");
    static const uint32_t dumpAddress = getenv("REGPU_DUMP_TEXTURE") ? uint32_t(strtoul(getenv("REGPU_DUMP_TEXTURE"), nullptr, 16)) : 0;
    if (RendererTracing() && getenv("REGPU_DUMP_TEXTURES"))
    {
        char name[64];
        snprintf(name, sizeof(name), "out/tex/%08X_%s_%ux%u.png", fetch.Address(), TextureFormatName(fetch.Format()), w, h);
        std::filesystem::create_directories("out/tex");
        WritePng(name, w, h, rgba);
    }
    if (dumpAddress && fetch.Address() == dumpAddress)
    {
        WritePng("texture.png", w, h, rgba);
        Log("renderer: wrote texture.png (%s %ux%u, endian %u, tiled %u, pitch %u)", TextureFormatName(fetch.Format()), w, h, fetch.Endian(),
            fetch.Tiled(), fetch.Pitch());
    }
    if (!cached.resource || cached.width != w || cached.height != h || cached.format != DXGI_FORMAT_R8G8B8A8_UNORM ||
        cached.resource->GetDesc().MipLevels != mipCount)
    {
        cached.format = DXGI_FORMAT_R8G8B8A8_UNORM;
        cached.native = false;
        D3D12_HEAP_PROPERTIES hp{};
        hp.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC td{};
        td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        td.Width = w;
        td.Height = h;
        td.DepthOrArraySize = 1;
        td.MipLevels = mipCount;
        td.SampleDesc.Count = 1;
        td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        RetireTexture(std::move(cached.resource));
        cached.resource = CreateTexture(td, D3D12_RESOURCE_STATE_COPY_DEST, "texture");
        cached.width = w;
        cached.height = h;
    }
    else
    {
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = cached.resource.Get();
        b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
        C().list->ResourceBarrier(1, &b);
    }
    for (UINT16 l = 0; l < mipCount; l++)
    {
        if (l > 0 && !DecodeTexture(levels[l], rgba))
        {
            NoteTextureFailure("mip decode failed", fetch);
            return nullptr;
        }
        const uint32_t lw = levels[l].Width(), lh = levels[l].Height();
        const UINT rowPitch = (lw * 4 + 255) & ~255u;
        const UINT64 offset = RingAlloc(UINT64(rowPitch) * lh, 512);
        if (offset == ~0ull)
        {
            NoteTextureFailure("upload ring full", fetch);
            return nullptr;
        }
        for (uint32_t y = 0; y < lh; y++)
            memcpy(Frame().ringCpu + offset + UINT64(y) * rowPitch, &rgba[size_t(y) * lw * 4], size_t(lw) * 4);
        D3D12_TEXTURE_COPY_LOCATION dst{};
        dst.pResource = cached.resource.Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        dst.SubresourceIndex = l;
        D3D12_TEXTURE_COPY_LOCATION src{};
        src.pResource = Frame().ring.Get();
        src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        src.PlacedFootprint.Offset = offset;
        src.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, lw, lh, 1, rowPitch };
        C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    }
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = cached.resource.Get();
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    C().list->ResourceBarrier(1, &b);
    cached.contentHash = hash;
    return cached.resource.Get();
}

// F12 capture: every distinct texture a draw of the traced frame binds goes into
// out/capture/textures.txt with its decoded image in out/capture/tex/, and a texture that could not
// be bound (the draw samples the white fallback) is named in out/play.log with the reason.
void CaptureTexture(const TextureFetch& fetch, uint32_t slot, bool cube, bool bound, uint32_t index, bool native, const char* why)
{
    static uint64_t captureFrame = 0;
    static std::set<uint64_t> seen;
    static std::ofstream summary;
    const uint64_t frame = TraceFrame();
    if (frame != captureFrame)
    {
        captureFrame = frame;
        seen.clear();
        std::filesystem::create_directories("out/capture/tex");
        std::filesystem::remove_all("out/capture/mem");
        summary.close();
        summary.open("out/capture/textures.txt");
        summary << "frame " << frame << " (draw numbers are those of the 'draw #N' lines in out/play.log)\n";
        summary << "draw slot address format size pitch tiled swizzle: status [image]\n";
    }
    const bool resolved = ResolvedFor(fetch) != nullptr;
    if (!bound && !cube)
        Log("    texture t%u %08X %s %ux%u is not bound: the draw samples white (%s)", slot, fetch.Address(), TextureFormatName(fetch.Format()), fetch.Width(),
            fetch.Height(), why[0] ? why : "unknown");
    const uint64_t key = (uint64_t(fetch.Address()) << 24) ^ (uint64_t(fetch.Format()) << 16) ^ (uint64_t(fetch.Width()) << 4) ^ fetch.Height() ^
        (uint64_t(cube) << 62);
    if (!seen.insert(key).second)
        return;
    char status[160];
    if (!bound)
        snprintf(status, sizeof(status), "WHITE FALLBACK: %s", why[0] ? why : "unknown");
    else if (resolved)
        snprintf(status, sizeof(status), "a resolved render target (out/res)");
    else
        snprintf(status, sizeof(status), "bound%s, bindless %u", native ? " (compressed on the GPU)" : "", index);
    std::string image;
    std::vector<uint8_t> rgba;
    if (!cube && !resolved && fetch.Type() == 2 && fetch.Dimension() == 1 && TextureFormatSupported(fetch.Format()) && DecodeTexture(fetch, rgba))
    {
        char name[160];
        snprintf(name, sizeof(name), "out/capture/tex/%08X_%s_%ux%u.png", fetch.Address(), TextureFormatName(fetch.Format()), fetch.Width(), fetch.Height());
        if (WritePng(name, fetch.Width(), fetch.Height(), rgba))
            image = name;
    }
    // The raw bytes of the base level as the game left them (out/capture/mem/), to look at a texture the
    // decode shows as noise: wrong layout, or not written yet.
    if (!cube && !resolved && fetch.Type() == 2)
    {
        const size_t bytes = std::min<size_t>(TextureSourceSize(fetch), 16u << 20);
        if (bytes)
        {
            std::filesystem::create_directories("out/capture/mem");
            char name[160];
            snprintf(name, sizeof(name), "out/capture/mem/%08X_%s_%ux%u.bin", fetch.Address(), TextureFormatName(fetch.Format()), fetch.Width(), fetch.Height());
            std::ofstream raw(name, std::ios::binary);
            raw.write(reinterpret_cast<const char*>(GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address()))), std::streamsize(bytes));
        }
    }
    char line[400];
    snprintf(line, sizeof(line), "%u t%u %08X %s %ux%u pitch %u %s sw%03X: %s %s%s\n", g_r.drawsThisFrame.load(), slot, fetch.Address(),
        TextureFormatName(fetch.Format()), fetch.Width(), fetch.Height(), fetch.Pitch(), fetch.Tiled() ? "tiled" : "linear", fetch.Swizzle(), status,
        cube ? "(cube map) " : "", image.c_str());
    summary << line;
    summary.flush();
}

uint32_t ReadIndex(uint32_t base, uint32_t i, bool is32, uint32_t endian)
{
    if (is32)
    {
        uint32_t v = *GuestPtr<uint32_t>(base + i * 4);
        if (endian == 2)
            v = __builtin_bswap32(v);
        else if (endian == 1)
            v = ((v & 0x00FF00FF) << 8) | ((v >> 8) & 0x00FF00FF);
        return v;
    }
    uint16_t v = *GuestPtr<uint16_t>(base + i * 2);
    if (endian == 1 || endian == 2)
        v = __builtin_bswap16(v);
    return v;
}

// Expanded host vertex list: guest indices, or rectangle markers
// (bit 31 | role << 29 | list position of the rectangle's first vertex).
bool BuildIndexList(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize, std::vector<uint32_t>& out)
{
    const uint32_t prim = initiator & 0x3F, source = (initiator >> 6) & 3, count = initiator >> 16;
    const bool is32 = (initiator >> 11) & 1;
    const uint32_t offset = gpureg::Get(VGT_INDX_OFFSET);
    // Reused between draws (no allocation per draw).
    thread_local std::vector<uint32_t> g;
    thread_local std::vector<uint8_t> cut;
    g.resize(count);
    cut.clear();
    // Primitive restart: a raw index equal to the reset value ends the strip/fan.
    const bool restart = (gpureg::Get(PA_SU_SC_MODE_CNTL) >> 21) & 1;
    const uint32_t resetIndex = gpureg::Get(VGT_MULTI_PRIM_IB_RESET_INDX) & (is32 ? 0xFFFFFF : 0xFFFF);
    if (source == 0) // DMA
    {
        const uint32_t base = GpuPhysToGuest(dmaBase & ~(is32 ? 3u : 1u));
        const uint32_t endian = dmaSize >> 30;
        const uint32_t available = (dmaSize & 0xFFFFFF) /* in indices, as Xenia (num_words) */;
        for (uint32_t i = 0; i < count && i < available; i++)
        {
            const uint32_t raw = ReadIndex(base, i, is32, endian);
            if (restart && (raw & (is32 ? 0xFFFFFF : 0xFFFF)) == resetIndex)
            {
                cut.resize(count);
                cut[i] = 1;
            }
            g[i] = raw + offset;
        }
        // A draw whose count runs past the index buffer (DMA size) reads zero indices there, as the
        // indexed path's buffer view does: the vector is reused between draws, so those entries kept
        // an earlier draw's indices (stray triangles). From the FM4 port. REGPU_STALE_INDICES=1: as before (A/B).
        static LiveSwitch staleIndices("REGPU_STALE_INDICES");
        if (count > available && !staleIndices)
            for (uint32_t i = available; i < count; i++)
                g[i] = offset;
    }
    else if (source == 2) // auto
    {
        for (uint32_t i = 0; i < count; i++)
            g[i] = i + offset;
    }
    else
        return false;

    out.clear();
    switch (prim)
    {
    case 1: // point list: a quad per point, corner in bits 29-30
    {
        out.resize(size_t(count) * 6);
        uint32_t* o = out.data();
        for (uint32_t i = 0; i < count; i++)
            for (uint32_t corner : { 0u, 1u, 2u, 2u, 1u, 3u })
                *o++ = (corner << 29) | (g[i] & 0x1FFFFFFF);
        return true;
    }
    case 4: // triangle list
        out.assign(g.begin(), g.begin() + (count / 3) * 3);
        return true;
    case 6: // triangle strip(s), split at restart indices
    case 5: // triangle fan(s)
    {
        out.resize(count >= 3 ? size_t(count - 2) * 3 : 0); // upper bound
        uint32_t* o = out.data();
        for (uint32_t start = 0; start < count;)
        {
            uint32_t end = start;
            if (cut.empty())
                end = count;
            else
                while (end < count && !cut[end])
                    end++;
            for (uint32_t i = start; i + 2 < end; i++)
            {
                const uint32_t k = i - start;
                if (prim == 5)
                    o[0] = g[start], o[1] = g[i + 1], o[2] = g[i + 2];
                else if (k & 1)
                    o[0] = g[i + 1], o[1] = g[i], o[2] = g[i + 2];
                else
                    o[0] = g[i], o[1] = g[i + 1], o[2] = g[i + 2];
                o += 3;
            }
            start = end + 1;
        }
        out.resize(size_t(o - out.data()));
        return true;
    }
    case 13: // quad list
    {
        out.resize(size_t(count / 4) * 6);
        uint32_t* o = out.data();
        for (uint32_t i = 0; i + 3 < count; i += 4, o += 6)
            o[0] = g[i], o[1] = g[i + 1], o[2] = g[i + 2], o[3] = g[i], o[4] = g[i + 2], o[5] = g[i + 3];
        return true;
    }
    case 8: // rectangle list: first triangle as given, second from the
            // hypotenuse ends and the computed fourth corner
        for (uint32_t i = 0; i + 2 < count; i += 3)
        {
            const uint32_t pos = uint32_t(out.size());
            out.insert(out.end(), { g[i], g[i + 1], g[i + 2] });
            // The Xenos draws each rectangle as one primitive with the given triangle's facing. The second
            // triangle is ordered (first hypotenuse end, fourth corner, second end), which keeps that
            // winding: the other order faced the other way, so a draw with separate back-face stencil ops
            // applied them to half of every rectangle. From the FM4 port. REGPU_OLD_RECT_WINDING=1 (read
            // at start): the old order (A/B).
            static const bool oldWinding = getenv("REGPU_OLD_RECT_WINDING") != nullptr;
            for (uint32_t role : oldWinding ? std::initializer_list<uint32_t>{ 1u, 2u, 0u } : std::initializer_list<uint32_t>{ 1u, 0u, 2u })
                out.push_back(0x80000000u | (role << 29) | pos);
        }
        return true;
    default:
        return false;
    }
}
}

namespace
{
// Expanded index lists (strips with restarts, fans, points, rectangles), kept across frames
// and keyed by the draw's index parameters and a sampled hash of the guest indices (like
// index buffers); each is copied into the ring once per frame.
struct IndexListEntry
{
    bool valid = false, ok = false;
    uint64_t sample = 0;
    std::vector<uint32_t> list;
    uint64_t frame = ~0ull; // the frame `offset` belongs to
    UINT64 offset = 0;
};

IndexListEntry* CachedIndexList(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    const uint32_t source = (initiator >> 6) & 3, count = initiator >> 16;
    const bool is32 = (initiator >> 11) & 1;
    uint64_t key = 0xCBF29CE484222325ull;
    for (uint32_t v : { initiator, dmaBase, dmaSize, gpureg::Get(VGT_INDX_OFFSET), gpureg::Get(PA_SU_SC_MODE_CNTL) & (1u << 21),
             gpureg::Get(VGT_MULTI_PRIM_IB_RESET_INDX) })
        key = (key ^ v) * 0x100000001B3ull;
    uint64_t sample = 0;
    if (source == 0)
    {
        const uint32_t available = (dmaSize & 0xFFFFFF) /* in indices, as Xenia (num_words) */;
        const uint32_t bytes = std::min(count, available) * (is32 ? 4 : 2);
        // Every index (as GetIndexBuffer): sampled every 256 bytes, indices rewritten between the samples
        // kept the old expanded list (stretched triangles). REGPU_INDEX_SAMPLED_HASH=1: sampled.
        static LiveSwitch sampledIndexHash("REGPU_INDEX_SAMPLED_HASH");
        const uint8_t* p = GuestPtr<uint8_t>(GpuPhysToGuest(dmaBase & ~(is32 ? 3u : 1u)));
        sample = sampledIndexHash ? ContentHash(p, bytes) : FullHash(p, bytes);
    }
    thread_local std::unordered_map<uint64_t, IndexListEntry> cache;
    if (cache.size() > 8192)
        cache.clear();
    IndexListEntry& e = cache[key];
    if (!e.valid || e.sample != sample)
    {
        e.valid = true;
        e.sample = sample;
        e.frame = ~0ull;
        e.ok = BuildIndexList(initiator, dmaBase, dmaSize, e.list);
    }
    return e.ok ? &e : nullptr;
}

// Origin (screen rows) and screen rectangle of the tile pass being replayed.
struct TileState
{
    int originY = 0;
    D3D12_RECT rect{ 0, 0, 1 << 14, 1 << 14 };
    uint32_t surface = ~0u; // RB_SURFACE_INFO of the tiled pass (~0: any)
};
thread_local TileState g_tile;
static LiveSwitch g_noForeignTile("REGPU_NO_FOREIGN_TILE"); // A/B for the paint-editor tile fix

// Tiling: each tile pass is shifted into EDRAM by the window offset and
// clipped to the tile. We render in screen space, so draws (and resolves)
// stay unshifted and are clipped to the tile's screen rectangle.
// Predicated tiling, merged. A title rendering into EDRAM too small for its scene (PGR4: 1280x720
// with MSAA) replays the same command buffers once per tile, each clipped to its tile: every draw
// was recorded two or three times (PGR4: 3270 of 8100 draws a frame were the second tile). We
// render the whole surface at once, so the first tile can draw the scene for all of them and the
// others' draws are dropped. The command processor finds replays (gpu.cpp: a buffer run again in
// a frame with another window offset) and learns each surface's tiles here. From the next frame
// on, up to its first resolve the first tile's scissor is widened to all the tiles, and the other
// tiles drop their draws up to their own first resolve. Resolves stay per tile (each copies its
// tile to its own address), and what follows a tile's first resolve (passes that sample it) is
// drawn tile by tile as before.
// A resolve keeps its clear inside the first tile (ReGPU otherwise extends a full-width clear down
// the surface for the next tile, which wiped what the first tile drew there).
// A later tile drops only draws the first tile drew (by DrawKey): tiles can differ.
// On by default; REGPU_NO_TILE_MERGE=1 draws every tile (A/B). Checked pixel for pixel on PGR4's
// race (paused) and by eye on its menus. Not with the draw thread (gpu.cpp). Debug:
// REGPU_TRACE_TILE_PHASE, REGPU_TRACE_TILE_WIDEN, REGPU_TILE_MERGE_KEEP, REGPU_TILE_MERGE_DROP_RESOLVES,
// out/tile_keep.txt "from to" (later-tile draws to keep, for bisecting).
namespace TileMerge
{
struct Entry
{
    uint32_t surface = 0;
    int32_t first[4]{}, all[4]{};
    int32_t later[4][4]{}; // the other tiles' rectangles
    uint32_t laterCount = 0;
    uint64_t learnedFrame = 0;
};
struct Table
{
    std::vector<Entry> entries;
};
std::atomic<const Table*> g_table{ nullptr };
std::mutex g_mutex;

bool Enabled()
{
    static LiveSwitch off("REGPU_NO_TILE_MERGE");
    return !off;
}

// What a draw draws: its primitive, indices and the shaders' first vertex buffer and textures
// (fetch constants), so a later tile's copy of a first-tile draw can be recognised.
uint64_t DrawKey(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    uint64_t h = 0xCBF29CE484222325ull;
    auto mix = [&](uint32_t v) { h = (h ^ v) * 0x100000001B3ull; };
    mix(initiator);
    mix(dmaBase);
    mix(dmaSize);
    for (uint32_t r = 0x4800; r < 0x4800 + 8 * 6; r++) // fetch constants of the first 8 slots
        mix(gpureg::Get(r));
    mix(gpureg::Get(0x2180)); // SQ_PROGRAM_CNTL
    return h;
}

// Keys counted (the first tile's draws, by DrawKey): an open-addressing table, so the ~1500 a frame of
// the race's first tile cost no allocation each (an unordered_multiset did).
struct KeyCounts
{
    std::vector<std::pair<uint64_t, uint32_t>> slots; // key (0: empty), count
    size_t used = 0;
    static size_t Hash(uint64_t k) { return size_t((k ^ (k >> 29)) * 0x9E3779B97F4A7C15ull >> 20); }
    void Add(uint64_t key)
    {
        key |= 1; // (0 marks an empty slot)
        if ((used + 1) * 2 > slots.size())
        {
            std::vector<std::pair<uint64_t, uint32_t>> old(std::max<size_t>(slots.size() * 2, 1024));
            old.swap(slots);
            used = 0;
            for (const auto& o : old)
                if (o.first)
                    for (size_t i = Hash(o.first) & (slots.size() - 1);; i = (i + 1) & (slots.size() - 1))
                        if (!slots[i].first)
                        {
                            slots[i] = o;
                            used++;
                            break;
                        }
        }
        for (size_t i = Hash(key) & (slots.size() - 1);; i = (i + 1) & (slots.size() - 1))
        {
            if (slots[i].first == key)
            {
                slots[i].second++;
                return;
            }
            if (!slots[i].first)
            {
                slots[i] = { key, 1 };
                used++;
                return;
            }
        }
    }
    // One of the key, taken away; false if there is none left.
    bool Take(uint64_t key)
    {
        key |= 1;
        if (slots.empty())
            return false;
        for (size_t i = Hash(key) & (slots.size() - 1);; i = (i + 1) & (slots.size() - 1))
        {
            if (!slots[i].first)
                return false;
            if (slots[i].first == key)
            {
                if (!slots[i].second)
                    return false;
                slots[i].second--;
                return true;
            }
        }
    }
};

// Where the current tile pass is (the command processor's thread): the surface, whether the
// first tile has resolved, and which later tiles have.
struct Phase
{
    uint32_t surface = 0;
    bool firstResolved = false, inLater = false;
    bool laterResolved[4]{};
    int current = -1; // the tile running: 0 the first, 1.. the later ones
    // The first tile's draws (before its first resolve), by what they draw: a later tile drops only
    // these. Tiles can differ (PGR4's race: the second tile had one draw more, a road marking).
    KeyCounts drawn;
    uint32_t counts[4]{}; // REGPU_TRACE_TILES: first tile before / after its first resolve, later tiles the same
    uint32_t bases[2]{};  // REGPU_TRACE_TILES: colour | depth EDRAM bases of the first and the later tiles' last draws
    bool laterCleared = false; // the later tiles' rows were readied for the widened first tile (LaterClear)
};
Phase g_phase;
thread_local bool t_widened = false; // WindowScissor's last answer was widened
// A replayed draw's answer, decided on the command processor when it was logged (RendererTileWidenNow)
thread_local bool t_forced = false, t_forcedWidened = false;
thread_local int32_t t_forcedRect[4]{};
bool g_mergedResolve = false;        // the resolve about to run is a merged first tile's (its clear stays in the tile)
// Unmerged, a later tile draws over what the first tile's clearing resolve left in the EDRAM; merged,
// the widened first tile draws the later tiles' rows over whatever the end of the last frame left
// there (post-processing). So before its first widened draw, those rows get that clear (from the last
// frame's resolve). PGR4's race: its scene leaves alpha alone and the motion blur weights its samples
// by it; over the last frame's leftovers the blur stopped at the tile line.
// REGPU_NO_TILE_MERGE_LATER_CLEAR=1: not cleared (A/B).
struct LaterClear
{
    bool color = false, depth = false, depthFloat = false;
    uint32_t colorHi = 0, colorLo = 0, depthValue = 0, colorFormat = 0;
    int32_t pending[4]{}; // rows to clear before the next draw (bottom <= top: none)
};
LaterClear g_laterClear;
// Surfaces whose later tiles clear with rectangles sized to the tile before resolving (PGR4's
// menus): the first tile's scissor does not widen those, so the scene behind is not merged there.
std::set<uint32_t> g_unsafe;

// The first tile's scissor widened to all the tiles, when this is a learned first tile before its
// first resolve.
bool Widen(uint32_t surface, int32_t r[4])
{
    const Table* t = g_table.load(std::memory_order_acquire);
    if (!t || !Enabled() || (g_phase.surface == surface && g_phase.firstResolved) || g_unsafe.count(surface))
        return false;
    for (const Entry& e : t->entries)
        if (e.surface == surface && e.first[0] == r[0] && e.first[1] == r[1] && e.first[2] == r[2] && e.first[3] == r[3])
        {
            static LiveSwitch noLaterClear("REGPU_NO_TILE_MERGE_LATER_CLEAR");
            if (g_phase.surface == surface && !g_phase.laterCleared && !noLaterClear && (g_laterClear.color || g_laterClear.depth))
            {
                g_phase.laterCleared = true;
                const int32_t rows[4]{ e.all[0], e.first[3], e.all[2], e.all[3] };
                memcpy(g_laterClear.pending, rows, sizeof(rows));
            }
            memcpy(r, e.all, sizeof(e.all));
            return true;
        }
    return false;
}
}

// The window scissor of the registers, in screen space, before clamping to the pass.
void ScreenScissor(int32_t r[4])
{
    const uint32_t offsetReg = gpureg::Get(PA_SC_WINDOW_OFFSET);
    const int32_t wx = int32_t(offsetReg << 17) >> 17, wy = int32_t((offsetReg >> 16) << 17) >> 17;
    const bool vertexOffset = (gpureg::Get(PA_SU_SC_MODE_CNTL) >> 16) & 1;
    const uint32_t tl = gpureg::Get(PA_SC_WINDOW_SCISSOR_TL), br = gpureg::Get(PA_SC_WINDOW_SCISSOR_BR);
    r[0] = int32_t(tl & 0x3FFF), r[1] = int32_t((tl >> 16) & 0x3FFF), r[2] = int32_t(br & 0x3FFF), r[3] = int32_t((br >> 16) & 0x3FFF);
    if (!(tl >> 31))
    {
        r[0] += wx; r[2] += wx; r[1] += wy; r[3] += wy;
    }
    if (vertexOffset)
    {
        r[0] -= wx; r[2] -= wx; r[1] -= wy; r[3] -= wy;
    }
}

D3D12_RECT WindowScissor()
{
    const uint32_t offsetReg = gpureg::Get(PA_SC_WINDOW_OFFSET);
    const int32_t wx = int32_t(offsetReg << 17) >> 17, wy = int32_t((offsetReg >> 16) << 17) >> 17;
    const bool vertexOffset = (gpureg::Get(PA_SU_SC_MODE_CNTL) >> 16) & 1;
    const uint32_t tl = gpureg::Get(PA_SC_WINDOW_SCISSOR_TL), br = gpureg::Get(PA_SC_WINDOW_SCISSOR_BR);
    int32_t x0 = int32_t(tl & 0x3FFF), y0 = int32_t((tl >> 16) & 0x3FFF), x1 = int32_t(br & 0x3FFF), y1 = int32_t((br >> 16) & 0x3FFF);
    if (!(tl >> 31))
    {
        x0 += wx; x1 += wx; y0 += wy; y1 += wy; // to EDRAM space
    }
    if (vertexOffset)
    {
        x0 -= wx; x1 -= wx; y0 -= wy; y1 -= wy; // back to screen space
    }
    {
        int32_t r[4] = { x0, y0, x1, y1 };
        if (TileMerge::t_forced)
        {
            TileMerge::t_widened = TileMerge::t_forcedWidened;
            memcpy(r, TileMerge::t_forcedRect, sizeof(r));
        }
        else
            TileMerge::t_widened = TileMerge::Widen(gpureg::Get(0x2000 /* RB_SURFACE_INFO */), r);
        if (TileMerge::t_widened)
            x0 = r[0], y0 = r[1], x1 = r[2], y1 = r[3];
    }
    // Taller than the screen on a pass narrower than it: a tall render target (see PassHeight).
    const uint32_t surfaceInfo = gpureg::Get(0x2000 /* RB_SURFACE_INFO */);
    static LiveSwitch noTall("REGPU_NO_TALL_PASS");
    if (!noTall && y1 > int32_t(kHeight) && y1 <= 2048 && (surfaceInfo & 0x3FFF) < kWidth)
    {
        if (g_tallInfo != surfaceInfo || g_tallHeight != uint32_t(y1))
            Log("tall pass: scissor y %d..%d x %d..%d window offset %d,%d vertexOffset %d surface %08X", y0, y1, x0, x1, wx, wy, int(vertexOffset), surfaceInfo);
        g_tallInfo = surfaceInfo;
        g_tallHeight = uint32_t(y1);
        g_tallKey = LayoutKey(surfaceInfo);
        // REGPU_CAPTURE_TALL=1: trace this very frame from here on (its dump is taken at the swap).
        static LiveSwitch captureTall("REGPU_CAPTURE_TALL");
        static bool traced = false;
        if (captureTall && !traced)
        {
            traced = true;
            g_hotkeyFrame = g_r.frame + 1;
            g_tallSeen = true;
        }
    }
    const LONG maxY = LONG(PassHeight());
    const LONG maxX = LONG(PassWidth());
    return { std::clamp<LONG>(x0, 0, maxX), std::clamp<LONG>(y0, 0, maxY), std::clamp<LONG>(x1, 0, maxX), std::clamp<LONG>(y1, 0, maxY) };
}

void TransitionColor(D3D12_RESOURCE_STATES to)
{
    if (C().colorState == to)
        return;
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = C().color.Get();
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = C().colorState;
    b.Transition.StateAfter = to;
    C().list->ResourceBarrier(1, &b);
    C().colorState = to;
}

// Draws the resolve rectangle of the colour (or depth) buffer into the
// destination's host texture at the same pixels, converting the format and
// scaling colour by the resolve's exponent bias.
// Makes `dest` the variant of that size and format (an earlier one from its spares, or new).
void EnsureResolved(ResolvedTexture& dest, uint32_t width, uint32_t height, uint32_t hostWidth, uint32_t hostHeight, DXGI_FORMAT format)
{
    auto swapVariant = [](ResolvedTexture& a, ResolvedTexture& b) {
        std::swap(a.resource, b.resource);
        std::swap(a.width, b.width);
        std::swap(a.height, b.height);
        std::swap(a.hostWidth, b.hostWidth);
        std::swap(a.hostHeight, b.hostHeight);
        std::swap(a.frame, b.frame);
        std::swap(a.serial, b.serial);
        std::swap(a.format, b.format);
        std::swap(a.swapped, b.swapped);
    };
    if (!dest.resource || dest.width != width || dest.height != height || dest.hostWidth != hostWidth || dest.hostHeight != hostHeight ||
        dest.format != format)
    {
        bool found = false;
        for (ResolvedTexture& spare : dest.spares)
            if (spare.resource && spare.width == width && spare.height == height && spare.hostWidth == hostWidth &&
                spare.hostHeight == hostHeight && spare.format == format)
            {
                swapVariant(dest, spare);
                found = true;
                break;
            }
        if (!found)
        {
            if (dest.resource)
            {
                // The displaced variant stays available; bound the pool.
                if (dest.spares.size() >= 6)
                {
                    Frame().garbage.push_back(std::move(dest.spares.front().resource));
                    dest.spares.erase(dest.spares.begin());
                }
                dest.spares.emplace_back();
                swapVariant(dest, dest.spares.back());
            }
            D3D12_HEAP_PROPERTIES hp{};
            hp.Type = D3D12_HEAP_TYPE_DEFAULT;
            D3D12_RESOURCE_DESC td{};
            td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
            td.Width = hostWidth;
            td.Height = hostHeight;
            td.DepthOrArraySize = 1;
            td.MipLevels = 1;
            td.SampleDesc.Count = 1;
            td.Format = format;
            td.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
            // Colour images can also be an upscaler's output (written by compute).
            if (format == DXGI_FORMAT_R8G8B8A8_UNORM || format == DXGI_FORMAT_R16G16B16A16_FLOAT)
                td.Flags |= D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
            Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                      D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, nullptr,
                      IID_PPV_ARGS(&dest.resource)),
                "CreateCommittedResource(resolve)");
            CountCreate("resolve");
            dest.width = width;
            dest.height = height;
            dest.hostWidth = hostWidth;
            dest.hostHeight = hostHeight;
            dest.format = format;
            dest.swapped = false;
        }
    }
}

// destRange: the destination's fixed-point range after the scale (the 16-bit integer range for the
// 16-bit fixed formats); null none.
void BlitResolve(bool depth, uint32_t base, uint32_t width, uint32_t height, DXGI_FORMAT format, const D3D12_RECT& rect, float scale, bool swapRB = false,
    const float* destRange = nullptr, uint32_t toGamma = 0)
{
    // REGPU_FREEZE_RESOLVES=<hex base>[,<hex base>...]: those images keep what they hold, later resolves
    // to them are skipped (debug: does a flicker come from an image's redraws?). Read every resolve, so
    // the debug server's "env" sets it live.
    thread_local FrameEnv freezeEnv{ "REGPU_FREEZE_RESOLVES" };
    if (const char* freeze = freezeEnv.Get())
        if (auto it = g_r.resolved.find(base); it != g_r.resolved.end() && it->second.resource)
            for (const char* p = freeze; *p;)
            {
                char* end = nullptr;
                if (uint32_t(strtoul(p, &end, 16)) == base)
                {
                    it->second.frame = g_r.frame;
                    return;
                }
                p = *end ? end + 1 : end;
            }
    ResolvedTexture& dest = g_r.resolved[base];
    // Rounded down, not to nearest: only the rows (and columns) whose pixel centres the draws covered hold
    // the image. At a scale that leaves a fraction (1080p is 1.5x 720p), PGR4's exposure chain ends in a
    // 32x1 image: 1.5 rows became a 2-row texture whose second row was never drawn, the single row it
    // samples blended in garbage, and the exposure flickered and ran away (washed-out menus, black races).
    // REGPU_ROUND_RESOLVE_SIZE=1: to nearest, as before (A/B).
    static LiveSwitch roundSize("REGPU_ROUND_RESOLVE_SIZE");
    const uint32_t hostWidth = roundSize ? uint32_t(HostX(LONG(width))) : std::max(1u, uint32_t(double(width) * ScaleX() + 1e-3));
    const uint32_t hostHeight = roundSize ? uint32_t(HostY(LONG(height))) : std::max(1u, uint32_t(double(height) * ScaleY() + 1e-3));
    EnsureResolved(dest, width, height, hostWidth, hostHeight, format);
    if (RendererTracing())
        Log("resolve: -> %08X %ux%u as %ux%u host (scale %.3f x %.3f, texture %ux%u)", base, width, height, hostWidth, hostHeight, ScaleX(), ScaleY(),
            dest.hostWidth, dest.hostHeight);
    // What memory holds under the image, for the stale check (ResolveCurrent): once per frame, as tiles
    // land in the same image. An image resolved every frame (shadow maps, bloom) only needs it every
    // 8th: the check waits until a resolve has not been repeated for 8 frames, so its baseline is
    // never older than that. Hashing each of PGR4's 31 resolves a frame took 0.9 ms on an FX-8350.
    // (Write-backs record our own writes as they happen.) REGPU_RESOLVE_BASELINE_EVERY_FRAME=1: every frame.
    static LiveSwitch baselineEveryFrame("REGPU_RESOLVE_BASELINE_EVERY_FRAME");
    // (Resolved within the last 4 frames counts: images refreshed every second or third frame, like
    // reflection faces, were hashed on every resolve.)
    const bool continuing = dest.frame != ~0ull && dest.frame + 4 >= g_r.frame && g_r.frame - dest.memChecked < 8 && !dest.stale;
    if (dest.frame != g_r.frame && (baselineEveryFrame || !continuing))
        NoteResolvedMemory(base, dest);
    dest.frame = g_r.frame;
    dest.serial = ++g_resolveSerial;
    const D3D12_RECT sr{ rect.left, rect.top, std::min<LONG>(rect.right, width), std::min<LONG>(rect.bottom, height) };
    FrameResources& f = Frame();
    if (sr.right <= sr.left || sr.bottom <= sr.top || f.descriptorsUsed >= kDescriptorsPerFrame)
        return;

    const UINT slot = f.descriptorBase + f.descriptorsUsed++;
    D3D12_SHADER_RESOURCE_VIEW_DESC srv{};
    srv.Format = depth ? DXGI_FORMAT_R32_FLOAT_X8X24_TYPELESS : kColorFormat;
    srv.ViewDimension = g_msaa > 1 ? D3D12_SRV_DIMENSION_TEXTURE2DMS : D3D12_SRV_DIMENSION_TEXTURE2D;
    srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    srv.Texture2D.MipLevels = 1;
    g_r.device->CreateShaderResourceView(depth ? C().depth.Get() : C().color.Get(), &srv, Cpu(g_r.srvHeap.Get(), slot, g_r.srvStride));
    const auto rtv = g_r.blitRtvHeap->GetCPUDescriptorHandleForHeapStart();
    C().list->CreateRenderTargetView(g_r.device, dest.resource.Get(), rtv); // in order with the recorded commands

    D3D12_RESOURCE_BARRIER b[2]{};
    UINT barriers = 1;
    b[0].Type = b[1].Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b[0].Transition.pResource = dest.resource.Get();
    b[0].Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    b[0].Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    if (depth)
    {
        b[1].Transition.pResource = C().depth.Get();
        b[1].Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b[1].Transition.StateBefore = D3D12_RESOURCE_STATE_DEPTH_WRITE;
        b[1].Transition.StateAfter = D3D12_RESOURCE_STATE_DEPTH_READ | D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
        barriers = 2;
    }
    else
        TransitionColor(D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    C().list->ResourceBarrier(barriers, b);

    const bool ms = g_msaa > 1;
    ID3D12PipelineState* pso = depth ? (ms ? g_r.depthBlitMs.Get() : g_r.depthBlit.Get())
        : format == DXGI_FORMAT_R8G8B8A8_UNORM ? (ms ? g_r.colorBlitMs8.Get() : g_r.colorBlit8.Get())
        : (ms ? g_r.colorBlitMs16.Get() : g_r.colorBlit16.Get());
    C().list->OMSetRenderTargets(1, &rtv, FALSE, nullptr);
    C().list->SetGraphicsRootSignature(g_r.blitRootSignature.Get());
    C().list->SetPipelineState(pso);
    C().list->SetGraphicsRootDescriptorTable(0, Gpu(g_r.srvHeap.Get(), slot, g_r.srvStride));
    const uint32_t blitConstants[6] = { std::bit_cast<uint32_t>(scale), uint32_t(swapRB), uint32_t(destRange != nullptr),
        std::bit_cast<uint32_t>(destRange ? destRange[0] : 0.0f), std::bit_cast<uint32_t>(destRange ? destRange[1] : 0.0f), uint32_t(toGamma) };
    C().list->SetGraphicsRoot32BitConstants(1, 6, blitConstants, 0);
    // Width and height stay in game pixels (the size the title knows); the texture and the
    // rectangle are scaled.
    const D3D12_VIEWPORT vp{ 0, 0, float(hostWidth), float(hostHeight), 0, 1 };
    const D3D12_RECT hostRect = ToHost(sr);
    C().list->RSSetViewports(1, &vp);
    C().list->RSSetScissorRects(1, &hostRect);
    C().list->DrawInstanced(3, 1, 0, 0);
    // Depth resolves also keep the guest's D24S8 words (RGBA8: stencil, depth low to high byte), for
    // fetches of the depth image with a colour format: FH1's speed blur reads the stencil that way to
    // pick each pixel's reprojection, and with depth alone every pixel took object 0's and the whole
    // scene blurred. REGPU_NO_DEPTH_PACK=1: depth only, as before.
    static LiveSwitch noDepthPack("REGPU_NO_DEPTH_PACK");
    if (depth && !ms && !noDepthPack && f.descriptorsUsed + 2 <= kDescriptorsPerFrame)
    {
        if (!dest.packed || dest.packedWidth != hostWidth || dest.packedHeight != hostHeight)
        {
            if (dest.packed)
                Frame().garbage.push_back(std::move(dest.packed));
            D3D12_HEAP_PROPERTIES hp{};
            hp.Type = D3D12_HEAP_TYPE_DEFAULT;
            D3D12_RESOURCE_DESC td{};
            td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
            td.Width = hostWidth;
            td.Height = hostHeight;
            td.DepthOrArraySize = 1;
            td.MipLevels = 1;
            td.SampleDesc.Count = 1;
            td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            td.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
            if (SUCCEEDED(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                    D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, nullptr, IID_PPV_ARGS(&dest.packed))))
            {
                CountCreate("depth pack");
                dest.packedWidth = hostWidth;
                dest.packedHeight = hostHeight;
            }
        }
        if (dest.packed)
        {
            const UINT pslot = f.descriptorBase + f.descriptorsUsed;
            f.descriptorsUsed += 2;
            D3D12_SHADER_RESOURCE_VIEW_DESC dv = srv;
            g_r.device->CreateShaderResourceView(C().depth.Get(), &dv, Cpu(g_r.srvHeap.Get(), pslot, g_r.srvStride));
            dv.Format = DXGI_FORMAT_X32_TYPELESS_G8X24_UINT;
            dv.Texture2D.PlaneSlice = 1;
            g_r.device->CreateShaderResourceView(C().depth.Get(), &dv, Cpu(g_r.srvHeap.Get(), pslot + 1, g_r.srvStride));
            D3D12_RESOURCE_BARRIER pb{};
            pb.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
            pb.Transition.pResource = dest.packed.Get();
            pb.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
            pb.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
            C().list->ResourceBarrier(1, &pb);
            C().list->CreateRenderTargetView(g_r.device, dest.packed.Get(), rtv);
            C().list->OMSetRenderTargets(1, &rtv, FALSE, nullptr);
            C().list->SetPipelineState(g_r.depthPackBlit.Get());
            C().list->SetGraphicsRootDescriptorTable(0, Gpu(g_r.srvHeap.Get(), pslot, g_r.srvStride));
            const uint32_t packConstants[5] = { 0, uint32_t((gpureg::Get(RB_DEPTH_INFO) >> 16) & 1), 0, 0, 0 };
            C().list->SetGraphicsRoot32BitConstants(1, 5, packConstants, 0);
            C().list->DrawInstanced(3, 1, 0, 0);
            std::swap(pb.Transition.StateBefore, pb.Transition.StateAfter);
            C().list->ResourceBarrier(1, &pb);
        }
    }

    std::swap(b[0].Transition.StateBefore, b[0].Transition.StateAfter);
    std::swap(b[1].Transition.StateBefore, b[1].Transition.StateAfter);
    C().list->ResourceBarrier(barriers, b);
    TransitionColor(D3D12_RESOURCE_STATE_RENDER_TARGET);
    // Back to the main targets and root signature.
    C().list->OMSetRenderTargets(1, &C().rtv, FALSE, &C().dsv);
    C().list->SetGraphicsRootSignature(g_r.rootSignature.Get());
    g_drawState.Invalidate();
}

// EDRAM resolve: copy a rectangle of the render target to the destination
// (as a host texture), then optionally clear colour and depth.
// ---- Upscaling ---------------------------------------------------------------------------
// Temporal upscalers (DLSS, FSR 3): the motion target, jitter, and the evaluation.
bool InitUpscale();
bool TemporalInit()
{
    if (!InitUpscale())
        return false;
    if (!g_upscale.motion)
    {
        D3D12_HEAP_PROPERTIES hp{};
        hp.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC td{};
        td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        td.Width = OutputWidth();
        td.Height = OutputHeight();
        td.DepthOrArraySize = 1;
        td.MipLevels = 1;
        td.SampleDesc.Count = 1;
        td.Format = DXGI_FORMAT_R16G16_FLOAT;
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
        D3D12_CLEAR_VALUE cv{};
        cv.Format = td.Format;
        if (FAILED(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_RENDER_TARGET, &cv,
                IID_PPV_ARGS(&g_upscale.motion))))
            return false;
        D3D12_DESCRIPTOR_HEAP_DESC hd{};
        hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
        hd.NumDescriptors = 1;
        if (FAILED(g_r.device->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_upscale.motionRtvHeap))))
            return false;
        g_upscale.motionRtv = g_upscale.motionRtvHeap->GetCPUDescriptorHandleForHeapStart();
        g_r.device->CreateRenderTargetView(g_upscale.motion.Get(), nullptr, g_upscale.motionRtv);
        td.Format = DXGI_FORMAT_R32_FLOAT;
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
        if (FAILED(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, nullptr,
                IID_PPV_ARGS(&g_upscale.depth))))
            return false;
    }
    return true;
}

// A tile of the scene was resolved (its depth may be cleared next): keep its depth.
void CopySceneDepth(const D3D12_RECT& gameRect)
{
    const D3D12_RECT r = ToHost(gameRect);
    FrameResources& f = Frame();
    if (!g_upscale.depth || !C().depth || r.right <= r.left || r.bottom <= r.top || f.descriptorsUsed + 2 > kDescriptorsPerFrame)
        return;
    const UINT slots = f.descriptorBase + f.descriptorsUsed;
    f.descriptorsUsed += 2;
    D3D12_SHADER_RESOURCE_VIEW_DESC srv{};
    srv.Format = DXGI_FORMAT_R32_FLOAT_X8X24_TYPELESS;
    srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    srv.Texture2D.MipLevels = 1;
    g_r.device->CreateShaderResourceView(C().depth.Get(), &srv, Cpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    D3D12_UNORDERED_ACCESS_VIEW_DESC uav{};
    uav.Format = DXGI_FORMAT_R32_FLOAT;
    uav.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
    g_r.device->CreateUnorderedAccessView(g_upscale.depth.Get(), nullptr, &uav, Cpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = C().depth.Get();
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_DEPTH_WRITE;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    C().list->ResourceBarrier(1, &b);
    uint32_t c[24] = {};
    c[0] = uint32_t(r.left);
    c[1] = uint32_t(r.top);
    c[20] = uint32_t(r.right - r.left);
    c[21] = uint32_t(r.bottom - r.top);
    C().list->SetComputeRootSignature(g_upscale.root.Get());
    C().list->SetComputeRoot32BitConstants(0, 24, c, 0);
    C().list->SetPipelineState(g_upscale.depthCopy.Get());
    C().list->SetComputeRootDescriptorTable(1, Gpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    C().list->SetComputeRootDescriptorTable(2, Gpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));
    C().list->Dispatch((c[20] + 7) / 8, (c[21] + 7) / 8, 1);
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    C().list->ResourceBarrier(1, &b);
    g_drawState.Invalidate(); // the compute pipeline replaced the draw's
}

float Halton(uint32_t index, uint32_t base)
{
    float f = 1.0f, r = 0.0f;
    for (; index; index /= base)
    {
        f /= float(base);
        r += f * float(index % base);
    }
    return r;
}

// Start of a temporal frame: jitter, a cleared motion target, the next constants map.
void BeginTemporalFrame()
{
    g_upscale.renderWidth = uint32_t(std::lround(OutputWidth() * double(g_sceneScale)));
    g_upscale.renderHeight = uint32_t(std::lround(OutputHeight() * double(g_sceneScale)));
    // Phases: 8 per pixel of upscale area (DLSS guidance), at most 64.
    const double ratio = double(OutputWidth()) / std::max(1u, g_upscale.renderWidth);
    const uint32_t phases = std::clamp(uint32_t(std::ceil(8.0 * ratio * ratio)), 8u, 64u);
    const uint32_t index = uint32_t(g_r.frame % phases) + 1;
    static LiveSwitch noJitter("REGPU_NO_JITTER");
    g_upscale.jitterPx[0] = noJitter ? 0.0f : Halton(index, 2) - 0.5f;
    g_upscale.jitterPx[1] = noJitter ? 0.0f : Halton(index, 3) - 0.5f;
    g_upscale.jitterNdc[0] = 2.0f * g_upscale.jitterPx[0] / float(std::max(1u, g_upscale.renderWidth));
    g_upscale.jitterNdc[1] = -2.0f * g_upscale.jitterPx[1] / float(std::max(1u, g_upscale.renderHeight));
    g_upscale.constantsFrame++;
    g_upscale.occurrences.clear();
    if (g_upscale.constantsFrame % 600 == 0) // draws not seen for a while
        std::erase_if(g_upscale.constants, [](const auto& e) { return e.second.frame + 2 < g_upscale.constantsFrame; });
    const float zero[4] = {};
    C().list->ClearRenderTargetView(g_upscale.motionRtv, zero, 0, nullptr);
}

void UpscaleResolved(uint32_t base);

// DLSS or FSR 3 on the scene image at `base`: colour (render size), depth and motion (top left of
// output-sized textures); the upscaled image replaces it like UpscaleResolved.
bool EvaluateTemporal(uint32_t base, bool dlss)
{
    if (dlss && !DlssInit(g_r.device))
        return false;
    auto it = g_r.resolved.find(base);
    if (it == g_r.resolved.end() || !it->second.resource)
        return false;
    ResolvedTexture& dest = it->second;
    if (dest.format != DXGI_FORMAT_R8G8B8A8_UNORM && dest.format != DXGI_FORMAT_R16G16B16A16_FLOAT)
        return false;
    const ComPtr<ID3D12Resource> color = dest.resource;
    const uint32_t inWidth = dest.hostWidth, inHeight = dest.hostHeight;
    const float savedScale = g_sceneScale;
    g_sceneScale = 1.0f;
    const uint32_t outWidth = uint32_t(HostX(LONG(dest.width))), outHeight = uint32_t(HostY(LONG(dest.height)));
    g_sceneScale = savedScale;
    if (outWidth < inWidth || outHeight < inHeight)
        return false;
    const uint64_t frame = dest.frame;
    const DXGI_FORMAT format = dest.format;
    const bool swapped = dest.swapped;
    // DLAA (native scale): the output must be another texture; the pool keys variants on
    // size, so a same-size output gets its own texture, copied back after.
    const bool dlaa = outWidth == inWidth && outHeight == inHeight;
    if (dlaa)
    {
        if (!g_upscale.dlaaOut || g_upscale.dlaaOut->GetDesc().Width != outWidth || g_upscale.dlaaOut->GetDesc().Height != outHeight ||
            g_upscale.dlaaOut->GetDesc().Format != format)
        {
            if (g_upscale.dlaaOut)
                Frame().garbage.push_back(std::move(g_upscale.dlaaOut));
            D3D12_HEAP_PROPERTIES hp{};
            hp.Type = D3D12_HEAP_TYPE_DEFAULT;
            D3D12_RESOURCE_DESC td = color->GetDesc();
            td.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
            if (FAILED(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td,
                    D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, nullptr, IID_PPV_ARGS(&g_upscale.dlaaOut))))
                return false;
        }
    }
    else
        EnsureResolved(dest, dest.width, dest.height, outWidth, outHeight, format);
    dest.frame = frame;
    dest.swapped = swapped;

    DlssSetup setup;
    setup.renderWidth = inWidth;
    setup.renderHeight = inHeight;
    setup.outputWidth = outWidth;
    setup.outputHeight = outHeight;
    setup.quality = GetSettings().quality;
    setup.hdr = format == DXGI_FORMAT_R16G16B16A16_FLOAT;
    static auto last = std::chrono::steady_clock::now();
    const auto now = std::chrono::steady_clock::now();
    DlssFrame in{};
    in.color = color.Get();
    in.depth = g_upscale.depth.Get();
    in.motion = g_upscale.motion.Get();
    in.output = dlaa ? g_upscale.dlaaOut.Get() : dest.resource.Get();
    static const float jitterSign = getenv("REGPU_DLSS_JITTER_FLIP") ? -1.0f : 1.0f;
    in.jitterX = g_upscale.jitterPx[0] * jitterSign;
    in.jitterY = g_upscale.jitterPx[1] * jitterSign;
    in.motionScaleX = float(inWidth);
    in.motionScaleY = float(inHeight);
    in.frameTimeMs = float(std::chrono::duration<double, std::milli>(now - last).count());
    in.reset = g_upscale.reset;
    last = now;

    auto barrier = [](ID3D12Resource* r, D3D12_RESOURCE_STATES from, D3D12_RESOURCE_STATES to) {
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = r;
        b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b.Transition.StateBefore = from;
        b.Transition.StateAfter = to;
        C().list->ResourceBarrier(1, &b);
    };
    const D3D12_RESOURCE_STATES read = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    barrier(g_upscale.depth.Get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    barrier(g_upscale.motion.Get(), D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    barrier(in.output, read, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    // NGX records into the real command list (after everything recorded so far).
    const bool ok = dlss ? DlssEvaluate(C().list->Real(), setup, in)
                         : Fsr3Evaluate(g_r.device, C().list->Real(), setup, in, GetSettings().sharpness);
    barrier(in.output, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, read);
    if (dlaa && ok)
    {
        barrier(in.output, read, D3D12_RESOURCE_STATE_COPY_SOURCE);
        barrier(color.Get(), read, D3D12_RESOURCE_STATE_COPY_DEST);
        C().list->Real()->CopyResource(color.Get(), in.output);
        barrier(color.Get(), D3D12_RESOURCE_STATE_COPY_DEST, read);
        barrier(in.output, D3D12_RESOURCE_STATE_COPY_SOURCE, read);
    }
    barrier(g_upscale.motion.Get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_RENDER_TARGET);
    barrier(g_upscale.depth.Get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    // NGX may have changed the list's heaps, root signatures and pipeline: set ours again.
    ID3D12DescriptorHeap* heaps[] = { g_r.srvHeap.Get(), g_r.samplerHeap.Get() };
    C().list->SetDescriptorHeaps(g_r.samplerHeap ? 2 : 1, heaps);
    C().list->SetGraphicsRootSignature(g_r.rootSignature.Get());
    C().list->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    g_drawState.Invalidate();
    SelectSurfaces(true);
    if (!ok && !dlaa) // keep the image: show the small one upscaled by FSR 1 instead
    {
        dest.resource = color;
        dest.hostWidth = inWidth;
        dest.hostHeight = inHeight;
        return false;
    }
    return true;
}

// The temporal upscale of the scene image at `base` (colour, depth, motion, jitter); FSR 1
// where the chosen upscaler is not available.
void TemporalEvaluate(uint32_t base)
{
    const bool done = EvaluateTemporal(base, GetSettings().upscaler == Settings::Upscaler::Dlss);
    if (!done)
        UpscaleResolved(base);
    g_upscale.reset = false;
}

// The scene (everything up to the full-screen resolve of the main view) renders at
// g_sceneScale; that resolved image is upscaled to the output size (FSR 1: EASU, then RCAS
// sharpening) and post-processing and the HUD render at full size from it.

bool InitUpscale()
{
    if (g_upscale.tried)
        return g_upscale.ready;
    g_upscale.tried = true;
    D3D12_DESCRIPTOR_RANGE srv{}, uav{};
    srv.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SRV;
    srv.NumDescriptors = 1;
    uav.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_UAV;
    uav.NumDescriptors = 1;
    D3D12_ROOT_PARAMETER params[3]{};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[0].Constants.Num32BitValues = 24;
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[1].DescriptorTable.NumDescriptorRanges = 1;
    params[1].DescriptorTable.pDescriptorRanges = &srv;
    params[2].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[2].DescriptorTable.NumDescriptorRanges = 1;
    params[2].DescriptorTable.pDescriptorRanges = &uav;
    D3D12_STATIC_SAMPLER_DESC sampler{};
    sampler.Filter = D3D12_FILTER_MIN_MAG_MIP_LINEAR;
    sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    sampler.MaxLOD = D3D12_FLOAT32_MAX;
    sampler.ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    D3D12_ROOT_SIGNATURE_DESC rs{};
    rs.NumParameters = 3;
    rs.pParameters = params;
    rs.NumStaticSamplers = 1;
    rs.pStaticSamplers = &sampler;
    ComPtr<ID3DBlob> blob, error;
    if (FAILED(D3D12SerializeRootSignature(&rs, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &error)) ||
        FAILED(g_r.device->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&g_upscale.root))))
    {
        Log("upscale: root signature failed: %s", error ? static_cast<const char*>(error->GetBufferPointer()) : "?");
        return false;
    }
    D3D12_COMPUTE_PIPELINE_STATE_DESC pd{};
    pd.pRootSignature = g_upscale.root.Get();
    pd.CS = { kEasuMain, sizeof(kEasuMain) };
    if (FAILED(g_r.device->CreateComputePipelineState(&pd, IID_PPV_ARGS(&g_upscale.easu))))
        return false;
    pd.CS = { kRcasMain, sizeof(kRcasMain) };
    if (FAILED(g_r.device->CreateComputePipelineState(&pd, IID_PPV_ARGS(&g_upscale.rcas))))
        return false;
    pd.CS = { kDepthCopyMain, sizeof(kDepthCopyMain) };
    if (FAILED(g_r.device->CreateComputePipelineState(&pd, IID_PPV_ARGS(&g_upscale.depthCopy))))
        return false;
    pd.CS = { kGammaMain, sizeof(kGammaMain) };
    if (FAILED(g_r.device->CreateComputePipelineState(&pd, IID_PPV_ARGS(&g_upscale.gamma))))
        return false;
    pd.CS = { kEnhanceMain, sizeof(kEnhanceMain) };
    if (FAILED(g_r.device->CreateComputePipelineState(&pd, IID_PPV_ARGS(&g_upscale.enhance))))
        return false;
    g_upscale.ready = true;
    Log("upscale: FSR 1 ready (scene at %.0f%% of %ux%u)", GetSettings().SceneScale() * 100.0f, OutputWidth(), OutputHeight());
    return true;
}

// ---- Display gamma ---------------------------------------------------------------------
// The console's display maps the front buffer through a 256-entry table the title uploads
// (DC_LUT_30_COLOR), often not the identity (one title's darkens by about x^1.15, with a darker
// toe: without it the picture was ~20% brighter than the console's). REGPU_NO_GAMMA=1 shows the front buffer as is.
std::atomic<uint32_t> g_gammaTable[256];
std::atomic<bool> g_gammaSet{ false };
// The piecewise-linear ramp (DC_LUT_PWL_DATA), which the display uses for a k_2_10_10_10 front
// buffer (as Xenia decides, after D3D's InitializePresentationParameters: the table for 8_8_8_8,
// PWL for 2_10_10_10). FH1 presents 2_10_10_10 with a PWL ramp that darkens the low end (1/16 to
// 0.017); shown through its identity table instead, shadows were too bright against stock
// (festival stand wall 21.5 vs 16.6, now 16.8).
std::atomic<uint32_t> g_gammaPwl[128][3];
std::atomic<bool> g_gammaPwlSet{ false };
ComPtr<ID3D12Resource> g_gammaOut;
ComPtr<ID3D12Resource> g_enhanceOut; // enhanced lighting output (Settings::enhance)

// Returns the graded copy of `image` (in COPY_SOURCE state), or null to show it as is. `pwl`: the
// front buffer is k_2_10_10_10, so the display uses the PWL ramp. REGPU_NO_GAMMA_PWL=1: always the table.
ID3D12Resource* GradeFrontBuffer(ID3D12Resource* image, bool pwl)
{
    static LiveSwitch off("REGPU_NO_GAMMA"), noPwl("REGPU_NO_GAMMA_PWL");
    pwl = pwl && !noPwl && g_gammaPwlSet.load(std::memory_order_relaxed);
    if (off || (!pwl && !g_gammaSet.load(std::memory_order_relaxed)) || !InitUpscale())
        return nullptr;
    // 17 points of the ramp (green; the title writes equal channels), at x = k/16.
    float points[17];
    bool identity = true;
    for (uint32_t k = 0; k <= 16; k++)
    {
        if (pwl)
        {
            // A 10-bit value: its top 7 bits pick the entry, its low 3 step along the delta
            // (base and delta are 16-bit, bits 0-5 zero).
            const uint32_t v = std::min(k * 1023u / 16u, 1023u), e = g_gammaPwl[v >> 3][1].load(std::memory_order_relaxed);
            points[k] = float(std::min((e & 0xFFFF) + (((e >> 16) * (v & 7)) >> 3), 0xFFFFu)) / 65535.0f;
        }
        else
        {
            const float at = k * 255.0f / 16.0f;
            const uint32_t i = std::min(uint32_t(at), 254u);
            const float t = at - float(i);
            const float a = float((g_gammaTable[i].load(std::memory_order_relaxed) >> 10) & 0x3FF) / 1023.0f;
            const float b = float((g_gammaTable[i + 1].load(std::memory_order_relaxed) >> 10) & 0x3FF) / 1023.0f;
            points[k] = a + (b - a) * t;
        }
        identity &= std::fabs(points[k] - k / 16.0f) < 0.002f;
    }
    static std::atomic<uint64_t> loggedHash{ 0 };
    static std::atomic<int> logs{ 0 }; // (titles fade the ramp in: the first few changes)
    uint64_t h = pwl ? 1 : 2;
    for (float pt : points)
        h = (h ^ uint32_t(pt * 65535.0f)) * 0x100000001B3ull;
    if (loggedHash.exchange(h) != h && logs++ < 4)
        Log("gamma: display ramp (%s): %.3f %.3f %.3f %.3f %.3f ... %.3f", pwl ? "PWL" : "table", points[0], points[1], points[2], points[4], points[8], points[16]);
    if (identity)
        return nullptr;
    const D3D12_RESOURCE_DESC desc = image->GetDesc();
    FrameResources& f = Frame();
    if (f.descriptorsUsed + 2 > kDescriptorsPerFrame)
        return nullptr;
    if (!g_gammaOut || g_gammaOut->GetDesc().Width != desc.Width || g_gammaOut->GetDesc().Height != desc.Height)
    {
        D3D12_RESOURCE_DESC td = desc;
        td.MipLevels = 1;
        td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
        RetireTexture(std::move(g_gammaOut));
        g_gammaOut = CreateTexture(td, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, "gamma");
    }
    const UINT slots = f.descriptorBase + f.descriptorsUsed;
    f.descriptorsUsed += 2;
    D3D12_SHADER_RESOURCE_VIEW_DESC srv{};
    srv.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    srv.Texture2D.MipLevels = 1;
    g_r.device->CreateShaderResourceView(image, &srv, Cpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    D3D12_UNORDERED_ACCESS_VIEW_DESC uav{};
    uav.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    uav.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
    g_r.device->CreateUnorderedAccessView(g_gammaOut.Get(), nullptr, &uav, Cpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));
    uint32_t c[24] = {};
    memcpy(c, points, sizeof(points));
    c[20] = uint32_t(desc.Width);
    c[21] = desc.Height;
    C().list->SetComputeRootSignature(g_upscale.root.Get());
    C().list->SetComputeRoot32BitConstants(0, 24, c, 0);
    C().list->SetPipelineState(g_upscale.gamma.Get());
    C().list->SetComputeRootDescriptorTable(1, Gpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    C().list->SetComputeRootDescriptorTable(2, Gpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));
    C().list->Dispatch(UINT((desc.Width + 7) / 8), (desc.Height + 7) / 8, 1);
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = g_gammaOut.Get();
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_UNORDERED_ACCESS;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    C().list->ResourceBarrier(1, &b);
    g_drawState.Invalidate(); // the compute pipeline replaced the draw's
    return g_gammaOut.Get();
}

// Enhanced lighting (Settings::enhance): the frame through the local-contrast pass. `image` is
// in COPY_SOURCE and so is the result; returns null when the option is off.
ID3D12Resource* EnhanceFrontBuffer(ID3D12Resource* image)
{
    if (!GetSettings().enhance || !image || !InitUpscale())
        return nullptr;
    const D3D12_RESOURCE_DESC desc = image->GetDesc();
    FrameResources& f = Frame();
    if (f.descriptorsUsed + 2 > kDescriptorsPerFrame)
        return nullptr;
    if (!g_enhanceOut || g_enhanceOut->GetDesc().Width != desc.Width || g_enhanceOut->GetDesc().Height != desc.Height)
    {
        D3D12_RESOURCE_DESC td = desc;
        td.MipLevels = 1;
        td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
        RetireTexture(std::move(g_enhanceOut));
        g_enhanceOut = CreateTexture(td, D3D12_RESOURCE_STATE_COPY_SOURCE, "enhance");
    }
    const UINT slots = f.descriptorBase + f.descriptorsUsed;
    f.descriptorsUsed += 2;
    D3D12_SHADER_RESOURCE_VIEW_DESC srv{};
    srv.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    srv.Texture2D.MipLevels = 1;
    g_r.device->CreateShaderResourceView(image, &srv, Cpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    D3D12_UNORDERED_ACCESS_VIEW_DESC uav{};
    uav.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    uav.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
    g_r.device->CreateUnorderedAccessView(g_enhanceOut.Get(), nullptr, &uav, Cpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));

    D3D12_RESOURCE_BARRIER b[2]{};
    for (auto& x : b)
        x.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b[0].Transition.pResource = image;
    b[0].Transition.Subresource = 0;
    b[0].Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
    b[0].Transition.StateAfter = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    b[1].Transition.pResource = g_enhanceOut.Get();
    b[1].Transition.Subresource = 0;
    b[1].Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
    b[1].Transition.StateAfter = D3D12_RESOURCE_STATE_UNORDERED_ACCESS;
    C().list->ResourceBarrier(2, b);

    // The strength (0.35) rides in the rcasCon slot of the constants (index 16), as float bits.
    const float strength = 0.35f;
    uint32_t c[24] = {};
    c[16] = std::bit_cast<uint32_t>(strength);
    c[20] = uint32_t(desc.Width);
    c[21] = desc.Height;
    C().list->SetComputeRootSignature(g_upscale.root.Get());
    C().list->SetComputeRoot32BitConstants(0, 24, c, 0);
    C().list->SetPipelineState(g_upscale.enhance.Get());
    C().list->SetComputeRootDescriptorTable(1, Gpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    C().list->SetComputeRootDescriptorTable(2, Gpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));
    C().list->Dispatch(UINT((desc.Width + 7) / 8), (desc.Height + 7) / 8, 1);

    b[0].Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    b[0].Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    b[1].Transition.StateBefore = D3D12_RESOURCE_STATE_UNORDERED_ACCESS;
    b[1].Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    C().list->ResourceBarrier(2, b);
    g_drawState.Invalidate(); // the compute pipeline replaced the draw's
    return g_enhanceOut.Get();
}

// Upscales the resolved image at `base` (rendered at the scene scale) to the output size in
// place: the resolve keeps the small image among its spares and shows the upscaled one.
void UpscaleResolved(uint32_t base)
{
    auto it = g_r.resolved.find(base);
    if (it == g_r.resolved.end() || !it->second.resource || !InitUpscale())
        return;
    ResolvedTexture& dest = it->second;
    if (dest.format != DXGI_FORMAT_R8G8B8A8_UNORM && dest.format != DXGI_FORMAT_R16G16B16A16_FLOAT)
        return;
    const ComPtr<ID3D12Resource> source = dest.resource;
    const uint32_t inWidth = dest.hostWidth, inHeight = dest.hostHeight;
    const float savedScale = g_sceneScale;
    g_sceneScale = 1.0f;
    const uint32_t outWidth = uint32_t(HostX(LONG(dest.width))), outHeight = uint32_t(HostY(LONG(dest.height)));
    g_sceneScale = savedScale;
    if (outWidth <= inWidth || outHeight <= inHeight)
        return;
    const uint64_t frame = dest.frame;
    const DXGI_FORMAT format = dest.format;
    const bool swapped = dest.swapped;
    EnsureResolved(dest, dest.width, dest.height, outWidth, outHeight, format);
    dest.frame = frame;
    dest.swapped = swapped;
    if (!g_upscale.temp || g_upscale.tempWidth != outWidth || g_upscale.tempHeight != outHeight)
    {
        if (g_upscale.temp)
            Frame().garbage.push_back(std::move(g_upscale.temp));
        D3D12_HEAP_PROPERTIES hp{};
        hp.Type = D3D12_HEAP_TYPE_DEFAULT;
        D3D12_RESOURCE_DESC td{};
        td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        td.Width = outWidth;
        td.Height = outHeight;
        td.DepthOrArraySize = 1;
        td.MipLevels = 1;
        td.SampleDesc.Count = 1;
        td.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
        td.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
        Check(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, nullptr,
                  IID_PPV_ARGS(&g_upscale.temp)),
            "CreateCommittedResource(upscale)");
        g_upscale.tempWidth = outWidth;
        g_upscale.tempHeight = outHeight;
    }
    FrameResources& f = Frame();
    if (f.descriptorsUsed + 4 > kDescriptorsPerFrame)
        return;
    const UINT slots = f.descriptorBase + f.descriptorsUsed;
    f.descriptorsUsed += 4;
    auto srvOf = [&](ID3D12Resource* r, DXGI_FORMAT fmt, UINT slot) {
        D3D12_SHADER_RESOURCE_VIEW_DESC v{};
        v.Format = fmt;
        v.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
        v.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
        v.Texture2D.MipLevels = 1;
        g_r.device->CreateShaderResourceView(r, &v, Cpu(g_r.srvHeap.Get(), slot, g_r.srvStride));
    };
    auto uavOf = [&](ID3D12Resource* r, DXGI_FORMAT fmt, UINT slot) {
        D3D12_UNORDERED_ACCESS_VIEW_DESC v{};
        v.Format = fmt;
        v.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
        g_r.device->CreateUnorderedAccessView(r, nullptr, &v, Cpu(g_r.srvHeap.Get(), slot, g_r.srvStride));
    };
    srvOf(source.Get(), format, slots);
    uavOf(g_upscale.temp.Get(), DXGI_FORMAT_R16G16B16A16_FLOAT, slots + 1);
    srvOf(g_upscale.temp.Get(), DXGI_FORMAT_R16G16B16A16_FLOAT, slots + 2);
    uavOf(dest.resource.Get(), format, slots + 3);

    // FSR 1 constants (FsrEasuCon / FsrRcasCon): the whole source texture is the input.
    auto bits = [](float v) { return std::bit_cast<uint32_t>(v); };
    uint32_t c[24] = {};
    const float iw = float(inWidth), ih = float(inHeight), ow = float(outWidth), oh = float(outHeight);
    c[0] = bits(iw / ow); c[1] = bits(ih / oh); c[2] = bits(0.5f * iw / ow - 0.5f); c[3] = bits(0.5f * ih / oh - 0.5f);
    c[4] = bits(1.0f / iw); c[5] = bits(1.0f / ih); c[6] = bits(1.0f / iw); c[7] = bits(-1.0f / ih);
    c[8] = bits(-1.0f / iw); c[9] = bits(2.0f / ih); c[10] = bits(1.0f / iw); c[11] = bits(2.0f / ih);
    c[12] = bits(0.0f); c[13] = bits(4.0f / ih);
    // RCAS: sharpness in stops (0 sharpest); the setting's 1 is sharpest.
    const float sharp = std::exp2(-2.0f * (1.0f - GetSettings().sharpness));
    c[16] = bits(sharp);
    const uint16_t h = DirectX::PackedVector::XMConvertFloatToHalf(sharp);
    c[17] = uint32_t(h) | uint32_t(h) << 16;
    c[20] = outWidth;
    c[21] = outHeight;

    auto barrier = [](ID3D12Resource* r, D3D12_RESOURCE_STATES from, D3D12_RESOURCE_STATES to) {
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = r;
        b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b.Transition.StateBefore = from;
        b.Transition.StateAfter = to;
        C().list->ResourceBarrier(1, &b);
    };
    const D3D12_RESOURCE_STATES read = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    const UINT gx = (outWidth + 15) / 16, gy = (outHeight + 15) / 16;
    C().list->SetComputeRootSignature(g_upscale.root.Get());
    C().list->SetComputeRoot32BitConstants(0, 24, c, 0);
    C().list->SetPipelineState(g_upscale.easu.Get());
    C().list->SetComputeRootDescriptorTable(1, Gpu(g_r.srvHeap.Get(), slots, g_r.srvStride));
    C().list->SetComputeRootDescriptorTable(2, Gpu(g_r.srvHeap.Get(), slots + 1, g_r.srvStride));
    C().list->Dispatch(gx, gy, 1);
    barrier(g_upscale.temp.Get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, read);
    barrier(dest.resource.Get(), read, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    C().list->SetPipelineState(g_upscale.rcas.Get());
    C().list->SetComputeRootDescriptorTable(1, Gpu(g_r.srvHeap.Get(), slots + 2, g_r.srvStride));
    C().list->SetComputeRootDescriptorTable(2, Gpu(g_r.srvHeap.Get(), slots + 3, g_r.srvStride));
    C().list->Dispatch(gx, gy, 1);
    barrier(dest.resource.Get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, read);
    barrier(g_upscale.temp.Get(), read, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    g_drawState.Invalidate(); // the compute pipeline replaced the draw's
}

// The scene is complete (its full-screen image resolved): upscale it, and render the rest of
// the frame (post-processing, HUD) at full size.
void UpscaleScene(uint32_t base)
{
    if (g_temporal)
        TemporalEvaluate(base);
    else
        UpscaleResolved(base);
    g_sceneScale = 1.0f;
    g_scenePhase = false;
    g_upscale.switchedThisFrame = true;
}

// Copies the render target resolved to `base` into guest memory, tiled, for the title to read.
void WriteResolveToGuest(uint32_t base, uint32_t width, uint32_t height, uint32_t info);
void QueueDeferredWriteback(uint32_t base, uint32_t width, uint32_t height, uint32_t info, bool depth, const D3D12_RECT& rect,
    const regpu::Writeback& request);

// REGPU_WRITEBACK_8888_ONLY=1: resolve write-backs only for 8_8_8_8 colour (and depth), as before (A/B).
LiveSwitch g_writeback8888Only("REGPU_WRITEBACK_8888_ONLY");

// Xenos 7e3 float (3-bit exponent, 7-bit mantissa, no sign) in bits 0-9.
float Float7e3(uint32_t f)
{
    f &= 0x3FF;
    uint32_t mantissa = f & 0x7F, exponent = f >> 7;
    if (!f)
        return 0.0f;
    if (!exponent)
    {
        int shift = 0;
        while (!(mantissa & 0x80))
            mantissa <<= 1, shift++;
        exponent = uint32_t(1 - (shift - 1));
        mantissa &= 0x7F;
        return std::ldexp(1.0f + mantissa / 128.0f, int(exponent) - 4);
    }
    return std::ldexp(1.0f + mantissa / 128.0f, int(exponent) - 3);
}

// The resolve clear value is the EDRAM's own bits for the colour surface's format (red in the low
// bits, as Xenia packs them): RB_COLOR_CLEAR for 32 bits a sample, RB_COLOR_CLEAR_LO the lower half
// of 64-bit formats. Read as ARGB 8888 for every format, a k_2_10_10_10_FLOAT surface cleared to
// C8521485 came out purple (FH1's sky, where the scene leaves the clear showing).
void ClearColor(uint32_t format, uint32_t lo, uint32_t hi, float out[4])
{
    auto half = [](uint32_t h) {
        const uint32_t e = (h >> 10) & 0x1F, m = h & 0x3FF;
        const float v = e ? std::ldexp(1.0f + m / 1024.0f, int(e) - 15) : std::ldexp(m / 1024.0f, -14);
        return (h & 0x8000) ? -v : v;
    };
    auto fixed16 = [](uint32_t v) { return float(int16_t(v & 0xFFFF)) * (32.0f / 32767.0f); }; // -32...32
    auto f32 = [](uint32_t v) { float f; memcpy(&f, &v, 4); return f; };
    switch (format)
    {
    case 2:  // k_2_10_10_10
    case 10: // k_2_10_10_10_AS_10_10_10_10
        out[0] = (hi & 0x3FF) / 1023.0f, out[1] = ((hi >> 10) & 0x3FF) / 1023.0f, out[2] = ((hi >> 20) & 0x3FF) / 1023.0f, out[3] = (hi >> 30) / 3.0f;
        return;
    case 3:  // k_2_10_10_10_FLOAT
    case 12: // k_2_10_10_10_FLOAT_AS_16_16_16_16
        out[0] = Float7e3(hi), out[1] = Float7e3(hi >> 10), out[2] = Float7e3(hi >> 20), out[3] = (hi >> 30) / 3.0f;
        return;
    case 4: // k_16_16
        out[0] = fixed16(hi), out[1] = fixed16(hi >> 16), out[2] = 0.0f, out[3] = 1.0f;
        return;
    case 5: // k_16_16_16_16
        out[0] = fixed16(lo), out[1] = fixed16(lo >> 16), out[2] = fixed16(hi), out[3] = fixed16(hi >> 16);
        return;
    case 6: // k_16_16_FLOAT
        out[0] = half(hi), out[1] = half(hi >> 16), out[2] = 0.0f, out[3] = 1.0f;
        return;
    case 7: // k_16_16_16_16_FLOAT
        out[0] = half(lo), out[1] = half(lo >> 16), out[2] = half(hi), out[3] = half(hi >> 16);
        return;
    case 14: // k_32_FLOAT
        out[0] = f32(hi), out[1] = out[2] = 0.0f, out[3] = 1.0f;
        return;
    case 15: // k_32_32_FLOAT
        out[0] = f32(lo), out[1] = f32(hi), out[2] = 0.0f, out[3] = 1.0f;
        return;
    default: // k_8_8_8_8, k_8_8_8_8_GAMMA
        out[0] = (hi & 0xFF) / 255.0f, out[1] = ((hi >> 8) & 0xFF) / 255.0f, out[2] = ((hi >> 16) & 0xFF) / 255.0f, out[3] = (hi >> 24) / 255.0f;
        return;
    }
}

void Resolve()
{
    g_resolveEpoch++; // texture bindings may now map to a new resolve
    if (!g_r.ok)
        Init();
    BeginFrameIfNeeded();
    SelectSurfaces(false);
    PassMark("resolve to %08X (colour %03X)", gpureg::Get(RB_COPY_DEST_BASE), C().colorBase);
    const uint32_t control = gpureg::Get(RB_COPY_CONTROL);
    const uint32_t command = (control >> 20) & 3;

    // The rectangle: three float2 vertices in vertex fetch constant 0.
    const uint32_t d0 = gpureg::Get(gpureg::FetchConstants), d1 = gpureg::Get(gpureg::FetchConstants + 1);
    const uint32_t vertices = GpuPhysToGuest(d0 & 0xFFFFFFFC);
    float x0 = 1e9f, y0 = 1e9f, x1 = -1e9f, y1 = -1e9f;
    for (int i = 0; i < 3; i++)
    {
        uint32_t xv = *GuestPtr<uint32_t>(vertices + i * 8), yv = *GuestPtr<uint32_t>(vertices + i * 8 + 4);
        if ((d1 & 3) == 2)
        {
            xv = __builtin_bswap32(xv);
            yv = __builtin_bswap32(yv);
        }
        float x, y;
        memcpy(&x, &xv, 4);
        memcpy(&y, &yv, 4);
        x0 = std::min(x0, x);
        y0 = std::min(y0, y);
        x1 = std::max(x1, x);
        y1 = std::max(y1, y);
    }
    // Clamped to the tile, as the hardware does: in a tile pass the
    // resolves for the other tiles become empty.
    const D3D12_RECT tile = WindowScissor();
    D3D12_RECT rect{ std::clamp<LONG>(LONG(std::lround(x0)), tile.left, tile.right), std::clamp<LONG>(LONG(std::lround(y0)), tile.top, tile.bottom),
        std::clamp<LONG>(LONG(std::lround(x1)), tile.left, tile.right), std::clamp<LONG>(LONG(std::lround(y1)), tile.top, tile.bottom) };
    if (rect.right <= rect.left || rect.bottom <= rect.top)
        return;

    const uint32_t sourceSelect = control & 7;
    const uint64_t traceFrame = TraceFrame();
    // REGPU_TRACE_RESOLVES=1: every resolve (not just those of the traced frame), with its time.
    static LiveSwitch traceAllResolves("REGPU_TRACE_RESOLVES");
    // REGPU_TRACE_RESOLVES_MIN=<width>: only resolves at least that wide (less slowdown).
    static const long traceMinWidth = getenv("REGPU_TRACE_RESOLVES_MIN") ? atol(getenv("REGPU_TRACE_RESOLVES_MIN")) : 0;
    if ((traceAllResolves && rect.right - rect.left >= traceMinWidth) || (traceFrame && g_r.frame + 1 == traceFrame))
        Log("resolve: control %08X dest %08X pitch %08X info %08X rect %ld,%ld-%ld,%ld clear %08X depth clear %08X (color info %08X depth info %08X surface %08X)", control,
            gpureg::Get(RB_COPY_DEST_BASE), gpureg::Get(RB_COPY_DEST_PITCH), gpureg::Get(RB_COPY_DEST_INFO), rect.left, rect.top, rect.right, rect.bottom,
            gpureg::Get(RB_COLOR_CLEAR), gpureg::Get(RB_DEPTH_CLEAR), gpureg::Get(RB_COLOR_INFO), gpureg::Get(RB_DEPTH_INFO), gpureg::Get(RB_SURFACE_INFO));
    // REGPU_CAPTURE_ON_RESOLVE=<dest hex>[,<skip>]: trace the frame after the skip-th resolve to that address
    // (passes that run only now and then, like an editor's preview).
    if (static const char* onResolve = getenv("REGPU_CAPTURE_ON_RESOLVE"); onResolve)
    {
        static const uint32_t dest = uint32_t(strtoul(onResolve, nullptr, 16));
        static int skip = strchr(onResolve, ',') ? atoi(strchr(onResolve, ',') + 1) : 0;
        if ((gpureg::Get(RB_COPY_DEST_BASE) & 0x1FFFFFFF) == dest && skip-- == 0)
        {
            g_hotkeyFrame = g_r.frame + 2;
            Log("capture: resolve to %08X seen, tracing frame %llu", dest, (unsigned long long)(g_r.frame + 2));
        }
    }
    if (command != 3 && sourceSelect <= 4)
    {
        // Each tile resolves to the destination row it starts at; key the
        // host texture by the image's own base so the tiles land together.
        const uint32_t pitchReg = gpureg::Get(RB_COPY_DEST_PITCH);
        const uint32_t pitch = pitchReg & 0x3FFF;
        const uint32_t info = gpureg::Get(RB_COPY_DEST_INFO);
        const bool depth = sourceSelect == 4;
        const uint32_t destFormat = (info >> 7) & 0x3F;
        // Guest bytes per pixel (Xenos formats: k_8 2, 16-bit 3-5/10/15/24/30, 64-bit 26/32/34/37, 128-bit 35/38).
        const uint32_t guestBpp = depth ? 4
            : destFormat == 2 ? 1
            : (destFormat >= 3 && destFormat <= 5) || destFormat == 10 || destFormat == 15 || destFormat == 24 || destFormat == 30 ? 2
            : destFormat == 26 || destFormat == 32 || destFormat == 34 || destFormat == 37 ? 8
            : destFormat == 35 || destFormat == 38 ? 16 : 4;
        // The tile's rows are pitch * guestBpp bytes each (PGR4's 2xMSAA HDR scene, k_16_16_16_16_FLOAT,
        // put its lower tile in a separate image at 4 bytes a pixel: the bottom third came out black).
        static LiveSwitch noTileBpp("REGPU_NO_TILE_BPP"); // A/B: rows of 4 bytes a pixel, as before
        // REGPU_RESOLVE_IMAGE_BASE=1: when the declared height covers a partial-width rectangle, the destination
        // base is the image's own and the rectangle its place in it (FH1's shadow atlas: one 1024x1024
        // cascade quadrant per resolve, at rows 512-1023 of the image at the base). As tiles, those
        // landed 512 rows early, at an address nothing fetches, and the shadow lookups read empty
        // quadrants: no sun shadows. Opt-in: a tall pass's tiles may declare such heights too.
        static LiveSwitch imageBase("REGPU_RESOLVE_IMAGE_BASE");
        const uint32_t declaredHeight = (pitchReg >> 16) & 0x3FFF;
        // (Only for a rectangle narrower than the image: full-width rows are a tall pass's tiles, which
        // FH1's scene resolves are too, at the tile's own row.)
        const bool placedInImage = imageBase && rect.top > 0 && uint32_t(rect.bottom) <= declaredHeight && uint32_t(rect.right - rect.left) < pitch;
        const uint32_t base = placedInImage ? gpureg::Get(RB_COPY_DEST_BASE) & 0x1FFFFFFF
                                            : ((gpureg::Get(RB_COPY_DEST_BASE) & 0x1FFFFFFF) - uint32_t(rect.top) * pitch * (noTileBpp ? 4 : guestBpp)) & 0x1FFFFFFF;
        const uint32_t width = std::clamp<uint32_t>(pitch, 1, std::max<uint32_t>(kWidth, BigPassSize()));
        // Taller than the screen is possible: a 1024x1024 target drawn as tall passes is resolved
        // 720 rows at a time and sampled as 1024 rows. The texture keeps all its rows (clamped to
        // kHeight it was stretched 1.4x vertically when sampled).
        constexpr uint32_t kMaxResolveHeight = 2048;
        uint32_t height = std::clamp<uint32_t>(placedInImage ? declaredHeight : declaredHeight + uint32_t(rect.top), 1, kMaxResolveHeight);
        // A later tile into an image that already holds its rows keeps the image: some copies give
        // the full image's rows for every tile (720, then 976 and 1232 with the tile's offset), and
        // each "taller" copy recreated the texture, dropping the tiles before it (the lower two
        // thirds of the image came out black). It grows only when the rows don't fit.
        static LiveSwitch noKeep("REGPU_NO_HEIGHT_KEEP");
        if (auto it = g_r.resolved.find(base); !noKeep && it != g_r.resolved.end() && it->second.resource && rect.top > 0 &&
                                                uint32_t(rect.bottom) <= it->second.height && it->second.height < height)
            height = it->second.height;
        // Destination: 8888 stays 8-bit (clamped, as the hardware does); other
        // formats keep the HDR range. The exponent bias scales colour copies.
        // k_2_10_10_10 (7) is a fixed-point front-buffer format too (PGR4 presents one); it gets the same
        // presentable 8-bit host image, losing its low two bits. Only unscaled, though: a copy with an
        // exponent bias stores HDR scaled down (FH1's reflections at 1/16, sampled back with the fetch's
        // exponent adjust), and 8 bits of that came out as a colour cast (green car glass); those keep
        // the HDR range. REGPU_NO_1010102_AS_8888 keeps every one HDR.
        static LiveSwitch no1010102("REGPU_NO_1010102_AS_8888");
        const int bias = int32_t(info << 10) >> 26; // bits 16-21, signed
        const DXGI_FORMAT format = depth ? DXGI_FORMAT_R32_FLOAT
            : destFormat == 6 || (destFormat == 7 && bias == 0 && !no1010102) ? DXGI_FORMAT_R8G8B8A8_UNORM : DXGI_FORMAT_R16G16B16A16_FLOAT;
        // Bit 24 (copy swap): the destination stores red and blue exchanged; the
        // fetch swizzle swaps them back when the title samples it.
        const bool swapRB = !depth && ((info >> 24) & 1);
        g_r.resolved[base].guestBpp = guestBpp;
        // 16-bit fixed destinations (k_16, k_16_16, k_16_16_16_16: formats 24-26) hold integers: the copy converts
        // each value (after the exponent bias) to a 16-bit integer and saturates, signed or unsigned by the copy's
        // number format (RB_COPY_DEST_INFO bits 13-15: 1 snorm, 3 sint signed). Our texture keeps the value as a
        // float in the same units, so it is held to that range here (from the FM4 port).
        // REGPU_NO_FIXED16_RESOLVE_CLAMP=1 leaves them unclamped (A/B).
        static LiveSwitch noFixed16Clamp("REGPU_NO_FIXED16_RESOLVE_CLAMP");
        const uint32_t numberFormat = (info >> 13) & 7;
        static constexpr float kSigned16[2] = { -32768.0f, 32767.0f }, kUnsigned16[2] = { 0.0f, 65535.0f };
        // A fixed-point 2_10_10_10 destination kept as half float (biased copies, above) saturates
        // like the hardware's: without it an HDR copy scaled down kept values past 1 (FH1's reflection
        // cube, at 1/16: the sun's highlights came back 16 times too bright on the car glass).
        static constexpr float kUnorm[2] = { 0.0f, 1.0f };
        const float* destRange = !depth && !noFixed16Clamp && destFormat >= 24 && destFormat <= 26
            ? (numberFormat == 1 || numberFormat == 3 ? kSigned16 : kUnsigned16)
            : !depth && destFormat == 7 && format == DXGI_FORMAT_R16G16B16A16_FLOAT ? kUnorm : nullptr;
        // A 7e3 surface (colour formats 3, 12) holds 0..31.875: additive blends past that saturate in
        // the EDRAM, but sum on in our half-float target, so the copy is held to it (scaled with the
        // exponent bias). Unclamped, PGR4's menu crowd flashes (a pile of additive sprites) fed its
        // bloom values far past the console's: a white-cyan glare over the crowd.
        // REGPU_NO_7E3_RESOLVE_CLAMP=1: unclamped.
        static LiveSwitch no7e3Clamp("REGPU_NO_7E3_RESOLVE_CLAMP");
        float k7e3[2] = { 0.0f, 31.875f * std::ldexp(1.0f, bias) };
        if (const uint32_t colorFormat = (gpureg::Get(RB_COLOR_INFO) >> 16) & 0xF;
            !destRange && !depth && !no7e3Clamp && format == DXGI_FORMAT_R16G16B16A16_FLOAT && (colorFormat == 3 || colorFormat == 12))
            destRange = k7e3;
        // A k_8_8_8_8_GAMMA surface (colour format 1) holds gamma in memory: our surfaces keep linear,
        // so the copy encodes it, as the EDRAM does, for the gamma-signed fetches that read it back
        // and for the front buffer.
        // (Pairs with REGPU_TEXTURE_GAMMA: opt-in with it, REGPU_RESOLVE_GAMMA=1.)
        static LiveSwitch resolveGamma("REGPU_RESOLVE_GAMMA");
        const bool toGamma = !depth && resolveGamma && ((gpureg::Get(RB_COLOR_INFO) >> 16) & 0xF) == 1;
        // A k_2_10_10_10 destination (7) holds 2 bits of alpha; our texture keeps 8 or 16. Titles test
        // that alpha for equality (FH1's final composite picks scene samples whose alpha matches the
        // centre's), so the copy rounds it as the hardware's does. REGPU_NO_RESOLVE_ALPHA2=1: as before.
        static LiveSwitch noAlpha2("REGPU_NO_RESOLVE_ALPHA2");
        const uint32_t blitFlags = (toGamma ? 1u : 0u) | (!depth && destFormat == 7 && !noAlpha2 ? 2u : 0u);
        BlitResolve(depth, base, width, height, format, rect, depth ? 1.0f : std::ldexp(1.0f, bias), false, destRange, blitFlags);
        // Temporal upscaling: each tile of the main view keeps its depth before it is cleared.
        if (g_temporal && g_scenePhase && !depth && width >= kWidth && rect.left == 0 && rect.right >= LONG(kWidth))
            CopySceneDepth(rect);
        // The main view's full image (its last tile reaches the bottom): the scene is done.
        if (g_scenePhase && !depth && width >= kWidth && rect.left == 0 && rect.right >= LONG(kWidth) && rect.bottom >= LONG(kHeight))
            UpscaleScene(base);
        g_r.resolved[base].swapped = swapRB;
        g_r.resolved[base].guestFormat = depth ? 0u : destFormat;
        // Images the game reads on the CPU are also written to memory (regpu.h Host::resolveWriteback;
        // without it, REGPU_RESOLVE_WRITEBACK's "WxH,WxH" sizes of 8888 colour, at once).
        regpu::Writeback request;
        if (const auto ask = regpu::GetHost().resolveWriteback)
        {
            const uint32_t msaa = (gpureg::Get(RB_SURFACE_INFO) >> 16) & 3;
            request = ask({ base, width, height, destFormat, depth, msaa == 2 ? 4u : msaa == 1 ? 2u : 1u, int32_t(rect.top), int32_t(rect.bottom) });
        }
        else
        {
            static const std::vector<std::pair<uint32_t, uint32_t>> sizes = [] {
                std::vector<std::pair<uint32_t, uint32_t>> v;
                for (const char* p = getenv("REGPU_RESOLVE_WRITEBACK"); p && *p;)
                {
                    uint32_t w = 0, h = 0;
                    if (sscanf(p, "%ux%u", &w, &h) == 2)
                        v.emplace_back(w, h);
                    p = strchr(p, ',');
                    if (p)
                        p++;
                }
                return v;
            }();
            if (!depth && (destFormat == 6 || !g_writeback8888Only) && rect.left == 0 && rect.top == 0 &&
                std::find(sizes.begin(), sizes.end(), std::pair<uint32_t, uint32_t>(width, height)) != sizes.end())
                request.mode = regpu::Writeback::Mode::Now;
        }
        // REGPU_RESOLVE_WRITEBACK_MAX=<pixels>: every smaller resolve the game didn't ask for is also
        // written, deferred (the console writes every resolve to memory; small ones, like an
        // average-luminance target, are what a title reads back on the CPU).
        static const uint64_t writebackMax = getenv("REGPU_RESOLVE_WRITEBACK_MAX") ? strtoull(getenv("REGPU_RESOLVE_WRITEBACK_MAX"), nullptr, 10) : 0;
        if (request.mode == regpu::Writeback::Mode::None && uint64_t(width) * height <= writebackMax)
            request.mode = regpu::Writeback::Mode::Deferred;
        // Any destination format the copy can write (from the FM4 port; 8888 only before).
        const bool anyFormat = destFormat == 6 || !g_writeback8888Only;
        if (request.mode == regpu::Writeback::Mode::Now && !depth && anyFormat)
            WriteResolveToGuest(base, width, height, info);
        else if (request.mode == regpu::Writeback::Mode::Deferred && (depth || anyFormat))
            QueueDeferredWriteback(base, width, height, info, depth, rect, request);
    }
    // Clears after a tile's resolve prepare the EDRAM for the next tile, which
    // we draw lower on the same host surface: a full-width clear covers every
    // row from the tile down.
    D3D12_RECT clearRect = rect;
    const uint32_t surfacePitch = gpureg::Get(RB_SURFACE_INFO) & 0x3FFF;
    static LiveSwitch noClearExtend("REGPU_NO_CLEAR_EXTEND");
    // (Not in a merged tile pass (TileMerge): the later tiles' rows are drawn already.)
    const bool merged = std::exchange(TileMerge::g_mergedResolve, false);
    // A merged first tile's clearing resolve readies the EDRAM for the later tiles: kept for their rows
    // (TileMerge::LaterClear).
    if (merged)
    {
        TileMerge::LaterClear& lc = TileMerge::g_laterClear;
        lc.color = (control >> 8) & 1, lc.depth = (control >> 9) & 1;
        lc.colorHi = gpureg::Get(RB_COLOR_CLEAR), lc.colorLo = gpureg::Get(RB_COLOR_CLEAR_LO), lc.depthValue = gpureg::Get(RB_DEPTH_CLEAR);
        lc.colorFormat = (gpureg::Get(RB_COLOR_INFO) >> 16) & 0xF, lc.depthFloat = (gpureg::Get(RB_DEPTH_INFO) >> 16) & 1;
    }
    if (!noClearExtend && !merged && surfacePitch && uint32_t(rect.right - rect.left) >= std::min<uint32_t>(surfacePitch, kWidth) && rect.left == 0)
        clearRect.bottom = PassHeight();
    if ((control >> 8) & 1) // colour clear (ARGB)
    {
        const uint32_t c = gpureg::Get(RB_COLOR_CLEAR);
        float color[4];
        static LiveSwitch oldClear("REGPU_OLD_CLEAR_COLOR"); // A/B: every clear read as ARGB 8888
        if (oldClear)
        {
            color[0] = ((c >> 16) & 0xFF) / 255.0f, color[1] = ((c >> 8) & 0xFF) / 255.0f, color[2] = (c & 0xFF) / 255.0f, color[3] = (c >> 24) / 255.0f;
        }
        else
            ClearColor((gpureg::Get(RB_COLOR_INFO) >> 16) & 0xF, gpureg::Get(RB_COLOR_CLEAR_LO), c, color);
        TransitionColor(D3D12_RESOURCE_STATE_RENDER_TARGET);
        const D3D12_RECT hostClear = ToHost(clearRect);
        static LiveSwitch traceClears("REGPU_TRACE_CLEARS");
        if (traceClears)
            Log("resolve clear: %08X rect %ld,%ld-%ld,%ld host %ld,%ld-%ld,%ld colour base %03X surface %08X worker %d", c, clearRect.left, clearRect.top, clearRect.right,
                clearRect.bottom, hostClear.left, hostClear.top, hostClear.right, hostClear.bottom, C().colorBase, gpureg::Get(RB_SURFACE_INFO), int(C().worker));
        C().list->ClearRenderTargetView(C().rtv, color, 1, &hostClear);
    }
    if ((control >> 9) & 1) // depth/stencil clear: depth 24 bits | stencil 8
    {
        const uint32_t v = gpureg::Get(RB_DEPTH_CLEAR), d = v >> 8;
        float depth = float(d) / 16777215.0f;
        if ((gpureg::Get(RB_DEPTH_INFO) >> 16) & 1)
        {
            // D24FS8: 20e4 float (4-bit exponent, bias 15, 20-bit mantissa).
            const uint32_t e = d >> 20, m = d & 0xFFFFF;
            depth = e ? std::ldexp(1.0f + float(m) / 1048576.0f, int(e) - 15) : std::ldexp(float(m) / 1048576.0f, -14);
            depth = std::clamp(depth, 0.0f, 1.0f);
        }
        const D3D12_RECT hostClear = ToHost(clearRect);
        C().list->ClearDepthStencilView(C().dsv, D3D12_CLEAR_FLAG_DEPTH | D3D12_CLEAR_FLAG_STENCIL, depth, UINT8(v & 0xFF), 1, &hostClear);
        // The EDRAM is one memory: a colour surface at the depth buffer's base is the same tiles, and a
        // whole-surface clear to zero leaves them zero (transparent black). The next colour surface
        // bound at that base starts cleared (BindSurfaces). PGR4's minimap is drawn at its
        // post-processing depth buffer's base without a clear of its own: the frame's last depth clear
        // is what empties it, and without that its old route lines piled up (a red bar).
        // REGPU_NO_ALIASED_DEPTH_CLEAR=1: colour surfaces keep their contents (A/B).
        static LiveSwitch noAliasedClear("REGPU_NO_ALIASED_DEPTH_CLEAR");
        if (v == 0 && !noAliasedClear && clearRect.left == 0 && clearRect.top == 0 && clearRect.bottom >= LONG(PassHeight()))
            g_zeroedEdramBase = C().depthBase;
    }
}
}

namespace
{
// ---- Lookahead: pipelines the command stream will need, found by GpuLookahead.
struct LookItem
{
    PipelineKey key;
    std::shared_ptr<std::vector<uint32_t>> vsCode, psCode; // only for shaders not translated yet
    std::shared_ptr<PipelineEntry> entry; // set once the build has been started
    bool dropped = false;
};
std::vector<LookItem> g_look;
std::unordered_set<PipelineKey, PipelineKeyHash> g_lookKeys;
uint64_t g_predVs = 0, g_predPs = 0;
const uint32_t *g_predVsCode = nullptr, *g_predPsCode = nullptr;
// Microcode read from guest memory, copied when the prediction is made: the pointer can
// point into memory the guest rewrites before the predicted draw is looked at, and then the
// bytes would no longer match the hash taken at the same moment.
std::shared_ptr<std::vector<uint32_t>> g_predVsOwn, g_predPsOwn;
uint32_t g_predVsDwords = 0, g_predPsDwords = 0;
size_t g_lookNext = 0;
uint32_t g_lookStarted = 0, g_lookFinished = 0;

// Starts what can be started (translating shaders as needed, queueing pipeline builds);
// returns true while anything is unfinished.
bool AdvanceLookahead(double budgetMs)
{
    const auto start = std::chrono::steady_clock::now();
    bool pending = false;
    for (size_t i = 0; i < g_look.size(); i++)
    {
        LookItem& it = g_look[i];
        if (it.dropped)
            continue;
        if (!it.entry)
        {
            if (std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count() > budgetMs)
            {
                pending = true;
                continue;
            }
            const CompiledShader* vs = nullptr;
            const CompiledShader* ps = nullptr;
            if (auto f = g_r.shaders.find(it.key.vs); f != g_r.shaders.end())
                g_compiler.Promote(*(vs = f->second.get()));
            else
                vs = it.vsCode ? &GetShader(it.key.vs, *it.vsCode, false) : nullptr;
            if (auto f = g_r.shaders.find(it.key.ps); f != g_r.shaders.end())
                g_compiler.Promote(*(ps = f->second.get()));
            else
                ps = it.psCode ? &GetShader(it.key.ps, *it.psCode, true) : nullptr;
            if (!vs || !ps)
            {
                it.dropped = true;
                g_lookFinished++;
                continue;
            }
            if (vs->state.load() == CompiledShader::Failed || ps->state.load() == CompiledShader::Failed)
            {
                it.dropped = true;
                g_lookFinished++;
                continue;
            }
            if (vs->state.load() == CompiledShader::Pending || ps->state.load() == CompiledShader::Pending)
            {
                pending = true;
                continue;
            }
            it.entry = EnsurePipeline(*vs, *ps, it.key);
            g_lookStarted++;
        }
        if (it.entry && it.entry->state.load(std::memory_order_acquire) == 0)
            pending = true;
    }
    // Finished items leave the list so it stays short.
    size_t keep = 0;
    for (size_t i = 0; i < g_look.size(); i++)
    {
        const bool finished = g_look[i].dropped || (g_look[i].entry && g_look[i].entry->state.load(std::memory_order_acquire) != 0);
        if (finished && !g_look[i].dropped)
            g_lookFinished++;
        if (!finished)
            g_look[keep++] = std::move(g_look[i]);
    }
    g_look.resize(keep);
    return pending || !g_look.empty();
}

// Shaders the memory scan found (by renderer hash), to see how much of a level it covers.
std::unordered_set<uint64_t> g_scanKnown;
uint32_t g_scanPrefound = 0, g_scanMissed = 0;

// Hands shaders the scanner found to the compile queue (translation runs here, on the
// command processor thread, so the budget keeps it from eating a frame).
void DrainScan(double budgetMs, std::vector<const CompiledShader*>* queued = nullptr)
{
    const auto start = std::chrono::steady_clock::now();
    std::vector<ScannedShader> found;
    while (true)
    {
        found.clear();
        if (!ShaderScanTake(found, 8))
            return;
        for (ScannedShader& s : found)
        {
            // The scanner accepts a candidate on control flow alone, and some of what it finds in
            // the mesh files is not a shader (ucode::Plausible: an opcode the ISA doesn't define,
            // or instructions past the end). Warming one costs a background compile and logs a
            // translation failure for a hash no draw will ever use.
            if (!ucode::Plausible(ucode::Parse(s.code.data(), uint32_t(s.code.size()), s.pixel)))
                continue;
            const uint64_t hash = Fnv(s.code.data(), s.code.size() * 4) ^ (s.pixel ? 1 : 0);
            g_scanKnown.insert(hash);
            // Speculative (and partly stale memory): compiled when nothing urgent waits,
            // promoted if a draw turns out to need it.
            const CompiledShader& cs = GetShader(hash, s.code, s.pixel, true);
            if (queued && cs.state.load() == CompiledShader::Pending)
                queued->push_back(&cs);
        }
        if (budgetMs > 0 && std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count() > budgetMs)
            return;
    }
}

// ---- Hold: while the command processor waits for a compile it keeps presenting the
// last frame with the overlay on top, so the screen doesn't just freeze.
struct Hold
{
    bool active = false;
    ComPtr<ID3D12Resource> image; // last frame shown (kept alive here)
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<ID3D12Resource> upload;
    uint8_t* uploadCpu = nullptr;
    std::chrono::steady_clock::time_point lastPresent{};
} g_hold;

void HoldPresent();
void SubmitLists(UINT n, ID3D12CommandList* const* lists);

// Spins until done() is true. Presents the overlay roughly 8 times a second once the wait
// has lasted 150 ms.


void WaitHoldingImpl(const std::function<bool()>& done)
{
    if (done())
        return;
    const auto waitStart = std::chrono::steady_clock::now();
    struct CountWait
    {
        std::chrono::steady_clock::time_point start;
        ~CountWait()
        {
            g_frameEvents.waitMs += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
            g_frameEvents.waits++;
        }
    } countWait{ waitStart };
    // A stall: look at everything queued behind this draw and build it all at once
    // (in parallel), then wait for the whole set, not just this draw's pipeline.
    static auto lastLook = std::chrono::steady_clock::time_point{};
    const auto start = std::chrono::steady_clock::now();
    bool announced = false;
    // Not once the game runs: the scan alone takes ~18 ms and the wait is usually shorter.
    if (!g_compiler.park.load())
    {
        ShaderScanNow(std::max(2u, std::thread::hardware_concurrency() / 2));
        DrainScan(0);
    }
    for (;;)
    {
        const auto now = std::chrono::steady_clock::now();
        if (now - lastLook > std::chrono::milliseconds(400))
        {
            lastLook = now;
            GpuLookahead();
        }
        const bool lookPending = AdvanceLookahead(4.0);
        if (done() && !lookPending && g_compiler.jobs.load() == 0)
            break;
        if (now - start > std::chrono::seconds(180))
            break;
        // Nothing legitimate takes this long: say what is still pending and where every
        // thread is, once per wait.
        static std::chrono::steady_clock::time_point reported{};
        if (now - start > std::chrono::seconds(20) && reported != start)
        {
            reported = start;
            int queued = 0, background = 0, parked = 0;
            {
                std::lock_guard lock(g_compiler.mutex);
                queued = int(g_compiler.queue.size());
                background = int(g_compiler.background.size());
                parked = int(g_compiler.parked.size());
            }
            Log("hang: waiting %.0f s on %s: done %d, lookahead pending %d (%zu items), compile jobs %d (queued %d, background %d, parked %d), "
                "pipeline builds in flight %d",
                std::chrono::duration<double>(now - start).count(), g_waitWhat, int(done()), int(lookPending), g_look.size(),
                g_compiler.jobs.load(), queued, background, parked, g_stats.busy.load());
            if (regpu::GetHost().logThreadStacks)
                regpu::GetHost().logThreadStacks("renderer wait over 20 s");
        }
        if (now - start > std::chrono::milliseconds(150))
        {
            g_hold.active = true;
            announced = true;
            DrainScan(0);
            if (now - g_hold.lastPresent > std::chrono::milliseconds(33))
                HoldPresent();
        }
        Sleep(1);
    }
    if (announced)
        g_hold.active = false;
    if (std::chrono::steady_clock::now() - start > std::chrono::seconds(2))
        g_lastLongHold = std::chrono::steady_clock::now();
    if (getenv("REGPU_PERF"))
    {
        const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        if (ms > 20)
            Log("perf: stall of %.0f ms on %s (blend %08X, lookahead started %u, finished %u)", ms, g_waitWhat, g_waitBlend, g_lookStarted, g_lookFinished);
    }
}

// Lookahead hooks, called from GpuLookahead on shadow register state.
void PredictBeginImpl()
{
    g_predVs = C().vsHash;
    g_predPs = C().psHash;
    g_predVsCode = C().vsCode.data();
    g_predPsCode = C().psCode.data();
    g_predVsOwn.reset(); // the command list's own copy stays valid
    g_predPsOwn.reset();
    g_predVsDwords = uint32_t(C().vsCode.size());
    g_predPsDwords = uint32_t(C().psCode.size());
}

std::vector<PipelineKey> g_seedLater; // seed pipelines waiting for a shader the game makes at run time

// Seed pipelines (Host::pipelineSeed) whose shaders the game has made since the boot: started in the
// background, checked about once a second.
void SeedStep()
{
    static auto last = std::chrono::steady_clock::time_point{};
    if (g_seedLater.empty() || std::chrono::steady_clock::now() - last < std::chrono::seconds(1))
        return;
    last = std::chrono::steady_clock::now();
    size_t started = 0;
    std::erase_if(g_seedLater, [&](const PipelineKey& key) {
        if (g_r.pipelines.count(key))
            return true;
        const auto vs = g_r.shaders.find(key.vs), ps = g_r.shaders.find(key.ps);
        if (vs == g_r.shaders.end() || ps == g_r.shaders.end() || !vs->second->IsReady() || !ps->second->IsReady())
            return false;
        EnsurePipeline(*vs->second, *ps->second, key);
        started++;
        return true;
    });
    if (started)
        Log("seed pipelines: %zu started now their shaders exist (%zu still waiting)", started, g_seedLater.size());
}

// ---- Warm-up: rebuilds every shader/pipeline recorded in the manifest in the
// background (on this thread a few ms per frame) while the game boots and sits in menus.
void WarmupStep()
{
    DrainScan(3.0);
    SeedStep();
    static LiveSwitch off("REGPU_NO_WARMUP");
    if (off || g_manifest.next >= g_manifest.pending.size())
        return;
    const auto start = std::chrono::steady_clock::now();
    int skipped = 0;
    while (g_manifest.next < g_manifest.pending.size() && g_stats.busy.load() < 12)
    {
        if (std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count() > 4.0)
            break;
        const PipelineKey key = g_manifest.pending[g_manifest.next];
        if (g_r.pipelines.count(key))
        {
            g_manifest.next++;
            g_stats.warmDone++;
            continue;
        }
        std::vector<uint32_t> vsCode, psCode;
        if (!Manifest::LoadShader(false, key.vs, vsCode) || !Manifest::LoadShader(true, key.ps, psCode))
        {
            g_manifest.next++;
            g_stats.warmDone++;
            continue;
        }
        const CompiledShader& vs = GetShader(key.vs, vsCode, false);
        const CompiledShader& ps = GetShader(key.ps, psCode, true);
        if (vs.state.load() == CompiledShader::Pending || ps.state.load() == CompiledShader::Pending)
        {
            // Still compiling: look further ahead instead of waiting on it.
            std::rotate(g_manifest.pending.begin() + g_manifest.next, g_manifest.pending.begin() + g_manifest.next + 1, g_manifest.pending.end());
            if (++skipped > 64)
                break;
            continue;
        }
        g_manifest.next++;
        g_stats.warmDone++;
        if (vs.IsReady() && ps.IsReady())
            EnsurePipeline(vs, ps, key);
    }
}

// ---- Overlay: a small panel in the corner while shaders are compiling.
constexpr UINT kOverlayW = 520, kOverlayH = 140;
struct Overlay
{
    ComPtr<ID3D12Resource> texture;
    D3D12_RESOURCE_STATES state = D3D12_RESOURCE_STATE_COPY_DEST;
    std::chrono::steady_clock::time_point lastRender{};
    std::string lastText;
    bool visible = false;
} g_overlay;

bool OverlayWanted(std::vector<std::string>& lines)
{
    static LiveSwitch disabled("REGPU_NO_OVERLAY");
    if (disabled)
        return false;
    const uint64_t nowMs = uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
    const bool recentCold = g_stats.lastColdMs.load() && nowMs - g_stats.lastColdMs.load() < 4000;
    const bool warming = g_stats.warmTotal.load() && g_stats.warmDone.load() < g_stats.warmTotal.load();
    if (g_stats.busy.load() == 0 && !recentCold && !warming && !g_hold.active)
        return false;
    char l[160];
    lines.push_back(g_hold.active ? "Preparing shaders - please wait" : "Shader compiler");
    if (g_stats.busy.load() > 0)
    {
        std::lock_guard lock(g_stats.mutex);
        const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - g_stats.currentSince).count();
        snprintf(l, sizeof(l), "Compiling %s  (%.1f s, %d in progress)", g_stats.current.c_str(), sec, g_stats.busy.load());
        lines.push_back(l);
    }
    else
        lines.push_back("Idle");
    snprintf(l, sizeof(l), "Shaders    %u compiled, %u from cache", g_stats.shadersBuilt.load(), g_stats.shadersCached.load());
    lines.push_back(l);
    snprintf(l, sizeof(l), "Pipelines  %u built, %u from cache", g_stats.pipelinesBuilt.load(), g_stats.pipelinesLoaded.load());
    lines.push_back(l);
    if (g_hold.active)
    {
        snprintf(l, sizeof(l), "This scene  %u pipelines found, %u done, %zu waiting", g_lookStarted + uint32_t(g_look.size()), g_lookFinished, g_look.size());
        lines.push_back(l);
    }
    if (g_stats.warmTotal.load())
    {
        snprintf(l, sizeof(l), "Warm-up    %u / %u pipelines", std::min(g_stats.warmDone.load(), g_stats.warmTotal.load()), g_stats.warmTotal.load());
        lines.push_back(l);
    }
    if (g_stats.staticTotal.load())
    {
        snprintf(l, sizeof(l), "First boot %u / %u game shaders compiled", std::min(g_stats.staticDone.load(), g_stats.staticTotal.load()), g_stats.staticTotal.load());
        lines.push_back(l);
    }
    return true;
}

// Renders the lines with GDI into RGBA pixels.
void RenderOverlayPixels(const std::vector<std::string>& lines, std::vector<uint32_t>& pixels)
{
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof(bi.bmiHeader);
    bi.bmiHeader.biWidth = kOverlayW;
    bi.bmiHeader.biHeight = -int(kOverlayH);
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    HDC dc = CreateCompatibleDC(nullptr);
    void* bits = nullptr;
    HBITMAP bitmap = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    HGDIOBJ oldBitmap = SelectObject(dc, bitmap);
    RECT all{ 0, 0, LONG(kOverlayW), LONG(kOverlayH) };
    HBRUSH bg = CreateSolidBrush(RGB(14, 16, 22)), border = CreateSolidBrush(RGB(255, 140, 0));
    FillRect(dc, &all, border);
    RECT inner{ 2, 2, LONG(kOverlayW) - 2, LONG(kOverlayH) - 2 };
    FillRect(dc, &inner, bg);
    HFONT font = CreateFontW(19, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        FIXED_PITCH | FF_MODERN, L"Consolas");
    HGDIOBJ oldFont = SelectObject(dc, font);
    SetBkMode(dc, TRANSPARENT);
    for (size_t i = 0; i < lines.size(); i++)
    {
        SetTextColor(dc, i == 0 ? RGB(255, 170, 40) : RGB(235, 235, 235));
        TextOutA(dc, 12, 8 + int(i) * 22, lines[i].c_str(), int(lines[i].size()));
    }
    GdiFlush();
    pixels.resize(size_t(kOverlayW) * kOverlayH);
    const uint32_t* src = static_cast<const uint32_t*>(bits);
    for (size_t i = 0; i < pixels.size(); i++)
    {
        const uint32_t p = src[i]; // 0x00RRGGBB
        pixels[i] = 0xFF000000u | ((p & 0xFF) << 16) | (p & 0xFF00) | ((p >> 16) & 0xFF);
    }
    SelectObject(dc, oldFont);
    SelectObject(dc, oldBitmap);
    DeleteObject(font);
    DeleteObject(bg);
    DeleteObject(border);
    DeleteObject(bitmap);
    DeleteDC(dc);
}

// Records the overlay upload (when its text changed) and returns the texture to
// copy onto the back buffer, or null when nothing is showing.
struct OverlayUpload
{
    ID3D12Resource* resource = nullptr;
    uint8_t* cpu = nullptr;
    UINT64 offset = 0;
};

// upload(bytes) returns staging space (resource + CPU pointer + offset) or a null resource.
ID3D12Resource* PrepareOverlayOn(ID3D12GraphicsCommandList* list, const std::function<OverlayUpload(UINT64)>& upload, bool force)
{
    std::vector<std::string> lines;
    g_overlay.visible = OverlayWanted(lines);
    if (!g_overlay.visible)
        return nullptr;
    std::string text;
    for (auto& l : lines)
        text += l + "\n";
    const auto now = std::chrono::steady_clock::now();
    const bool stale = !g_overlay.texture || (text != g_overlay.lastText && (force || now - g_overlay.lastRender > std::chrono::milliseconds(200)));
    if (stale)
    {
        if (!g_overlay.texture)
        {
            D3D12_HEAP_PROPERTIES hp{};
            hp.Type = D3D12_HEAP_TYPE_DEFAULT;
            D3D12_RESOURCE_DESC td{};
            td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
            td.Width = kOverlayW;
            td.Height = kOverlayH;
            td.DepthOrArraySize = 1;
            td.MipLevels = 1;
            td.SampleDesc.Count = 1;
            td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            if (FAILED(g_r.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_COPY_DEST, nullptr,
                    IID_PPV_ARGS(&g_overlay.texture))))
                return nullptr;
            g_overlay.state = D3D12_RESOURCE_STATE_COPY_DEST;
        }
        std::vector<uint32_t> pixels;
        RenderOverlayPixels(lines, pixels);
        static LiveSwitch dumpOverlay("REGPU_OVERLAY_DUMP");
        if (dumpOverlay)
        {
            std::vector<uint8_t> rgba(pixels.size() * 4);
            memcpy(rgba.data(), pixels.data(), rgba.size());
            WritePng("overlay.png", kOverlayW, kOverlayH, rgba);
        }
        const UINT pitch = kOverlayW * 4;
        const UINT alignedPitch = (pitch + 255) & ~255u;
        const OverlayUpload staging = upload(UINT64(alignedPitch) * kOverlayH);
        if (!staging.resource)
            return nullptr;
        for (UINT y = 0; y < kOverlayH; y++)
            memcpy(staging.cpu + staging.offset + UINT64(y) * alignedPitch, pixels.data() + size_t(y) * kOverlayW, pitch);
        if (g_overlay.state != D3D12_RESOURCE_STATE_COPY_DEST)
        {
            D3D12_RESOURCE_BARRIER b{};
            b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
            b.Transition.pResource = g_overlay.texture.Get();
            b.Transition.Subresource = 0;
            b.Transition.StateBefore = g_overlay.state;
            b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
            list->ResourceBarrier(1, &b);
        }
        D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
        dst.pResource = g_overlay.texture.Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        src.pResource = staging.resource;
        src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        src.PlacedFootprint.Offset = staging.offset;
        src.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, kOverlayW, kOverlayH, 1, alignedPitch };
        list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = g_overlay.texture.Get();
        b.Transition.Subresource = 0;
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
        list->ResourceBarrier(1, &b);
        g_overlay.state = D3D12_RESOURCE_STATE_COPY_SOURCE;
        g_overlay.lastText = text;
        g_overlay.lastRender = now;
    }
    return g_overlay.state == D3D12_RESOURCE_STATE_COPY_SOURCE ? g_overlay.texture.Get() : nullptr;
}

// The frame path stages through the frame's upload ring.
ID3D12Resource* PrepareOverlay()
{
    return PrepareOverlayOn(C().list->Real(), [](UINT64 bytes) {
        OverlayUpload u;
        const UINT64 offset = RingAlloc(bytes, 512);
        if (offset == ~0ull)
            return u;
        u.resource = Frame().ring.Get();
        u.cpu = Frame().ringCpu;
        u.offset = offset;
        return u;
    }, false);
}

// Presents the last frame again with the overlay on top, on a separate command list
// (the frame's own list is mid-recording while the command processor waits).
void HoldPresent()
{
    g_hold.lastPresent = std::chrono::steady_clock::now();
    if (!g_hold.image || !g_r.device)
        return;
    if (!g_hold.list)
    {
        if (FAILED(g_r.device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&g_hold.allocator))) ||
            FAILED(g_r.device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, g_hold.allocator.Get(), nullptr, IID_PPV_ARGS(&g_hold.list))))
            return;
        g_hold.list->Close();
        const UINT64 bytes = UINT64((kOverlayW * 4 + 255) & ~255u) * kOverlayH + 1024;
        g_hold.upload = CreateBuffer(bytes, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
        void* mapped = nullptr;
        g_hold.upload->Map(0, nullptr, &mapped);
        g_hold.uploadCpu = static_cast<uint8_t*>(mapped);
    }
    // Everything the main queue still runs must be done before this list reuses its allocator.
    g_hold.allocator->Reset();
    g_hold.list->Reset(g_hold.allocator.Get(), nullptr);
    ID3D12Resource* overlay = PrepareOverlayOn(g_hold.list.Get(), [](UINT64) {
        OverlayUpload u;
        u.resource = g_hold.upload.Get();
        u.cpu = g_hold.uploadCpu;
        return u;
    }, true);
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = g_hold.image.Get();
    b.Transition.Subresource = 0;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    g_hold.list->ResourceBarrier(1, &b);
    const bool ok = PresenterRecordCopy(g_hold.list.Get(), g_hold.image.Get(), overlay, kOverlayW, kOverlayH);
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    g_hold.list->ResourceBarrier(1, &b);
    g_hold.list->Close();
    if (!ok)
        return;
    ID3D12CommandList* lists[] = { g_hold.list.Get() };
    g_r.recorder.Real(); // (after what the replay stream still queues)
    SubmitLists(1, lists);
    PresenterPresent();
    // Wait for it: the allocator and upload buffer are reused at the next hold.
    const UINT64 value = g_r.nextFence++;
    g_r.queue->Signal(g_r.fence.Get(), value);
    if (g_r.fence->GetCompletedValue() < value)
    {
        WaitFenceValue(value, 500);
    }
}
}

// Puts a full 1280x720 RGBA8 frame (the swap chain size) on screen: the first-boot screen,
// shown before the game has drawn anything that HoldPresent could repeat.
void BootPresent(const std::vector<uint8_t>& rgba)
{
    constexpr uint32_t gw = 1280, gh = 720;
    const uint32_t w = OutputWidth(), h = OutputHeight(); // the swap chain size
    static ComPtr<ID3D12Resource> texture, upload;
    static uint8_t* uploadCpu = nullptr;
    if (!g_r.device || rgba.size() != size_t(gw) * gh * 4)
        return;
    if (!g_hold.list)
    {
        if (FAILED(g_r.device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&g_hold.allocator))) ||
            FAILED(g_r.device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, g_hold.allocator.Get(), nullptr, IID_PPV_ARGS(&g_hold.list))))
            return;
        g_hold.list->Close();
        const UINT64 bytes = UINT64((kOverlayW * 4 + 255) & ~255u) * kOverlayH + 1024;
        g_hold.upload = CreateBuffer(bytes, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
        void* mapped = nullptr;
        g_hold.upload->Map(0, nullptr, &mapped);
        g_hold.uploadCpu = static_cast<uint8_t*>(mapped);
    }
    if (!texture)
    {
        D3D12_HEAP_PROPERTIES heap{ D3D12_HEAP_TYPE_DEFAULT };
        D3D12_RESOURCE_DESC desc{};
        desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        desc.Width = w;
        desc.Height = h;
        desc.DepthOrArraySize = 1;
        desc.MipLevels = 1;
        desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        desc.SampleDesc.Count = 1;
        if (FAILED(g_r.device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&texture))))
            return;
        upload = CreateBuffer(UINT64((w * 4 + 255) & ~255u) * h, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
        void* mapped = nullptr;
        upload->Map(0, nullptr, &mapped);
        uploadCpu = static_cast<uint8_t*>(mapped);
    }
    // Nearest-neighbour to the output size, centred at 16:9 on wider outputs with the
    // background's edge columns carried out to the sides.
    const uint32_t pitch = (w * 4 + 255) & ~255u;
    const uint32_t contentW = std::min(w, h * gw / gh), left = (w - contentW) / 2;
    for (uint32_t y = 0; y < h; y++)
    {
        const uint32_t* in = reinterpret_cast<const uint32_t*>(rgba.data()) + size_t(y * gh / h) * gw;
        uint32_t* out = reinterpret_cast<uint32_t*>(uploadCpu + size_t(y) * pitch);
        for (uint32_t x = 0; x < w; x++)
        {
            const uint32_t cx = x < left ? 0 : std::min(contentW - 1, x - left);
            out[x] = in[size_t(cx) * gw / contentW];
        }
    }
    g_hold.allocator->Reset();
    g_hold.list->Reset(g_hold.allocator.Get(), nullptr);
    D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
    dst.pResource = texture.Get();
    dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    src.pResource = upload.Get();
    src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    src.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, w, h, 1, (w * 4 + 255) & ~255u };
    g_hold.list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = texture.Get();
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    g_hold.list->ResourceBarrier(1, &b);
    const bool ok = PresenterRecordCopy(g_hold.list.Get(), texture.Get());
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    g_hold.list->ResourceBarrier(1, &b);
    g_hold.list->Close();
    if (!ok)
        return;
    ID3D12CommandList* lists[] = { g_hold.list.Get() };
    g_r.recorder.Real(); // (after what the replay stream still queues)
    SubmitLists(1, lists);
    PresenterPresent();
    const UINT64 value = g_r.nextFence++;
    g_r.queue->Signal(g_r.fence.Get(), value);
    if (g_r.fence->GetCompletedValue() < value)
    {
        WaitFenceValue(value, 500);
    }
}

void RendererPredictBegin()
{
    PredictBeginImpl();
}

void RendererPredictShader(uint32_t type, const uint32_t* microcodeBE, uint32_t dwords)
{
    dwords = ucode::UsedDwords(microcodeBE, dwords);
    auto& hash = type == 0 ? g_predVs : g_predPs;
    auto& code = type == 0 ? g_predVsCode : g_predPsCode;
    auto& own = type == 0 ? g_predVsOwn : g_predPsOwn;
    auto& count = type == 0 ? g_predVsDwords : g_predPsDwords;
    hash = 0;
    count = 0;
    own.reset();
    // The signal can name a shader the command processor has not loaded yet, so the
    // microcode may still be uninitialised or already reused: only predict one that decodes.
    if (!dwords || !ucode::Plausible(ucode::Parse(microcodeBE, dwords, type != 0)))
        return;
    hash = Fnv(microcodeBE, dwords * 4) ^ type;
    own = std::make_shared<std::vector<uint32_t>>(microcodeBE, microcodeBE + dwords);
    code = own->data();
    count = dwords;
}

void RendererPredictDraw(uint32_t initiator)
{
    const uint32_t edramMode = gpureg::Get(RB_MODECONTROL) & 7;
    if ((edramMode != 4 && edramMode != 5) || !g_predVs || !g_predPs || !g_predVsDwords || !g_predPsDwords)
        return;
    const uint32_t prim = initiator & 0x3F;
    const PipelineKey key = MakePipelineKey(g_predVs, g_predPs, edramMode == 5, prim != 8 && prim != 1);
    if (g_r.pipelines.count(key) || !g_lookKeys.insert(key).second)
        return;
    LookItem item{ key, nullptr, nullptr, nullptr, false };
    if (!g_r.shaders.count(g_predVs))
        item.vsCode = g_predVsOwn ? g_predVsOwn : std::make_shared<std::vector<uint32_t>>(g_predVsCode, g_predVsCode + g_predVsDwords);
    if (!g_r.shaders.count(g_predPs))
        item.psCode = g_predPsOwn ? g_predPsOwn : std::make_shared<std::vector<uint32_t>>(g_predPsCode, g_predPsCode + g_predPsDwords);
    g_look.push_back(std::move(item));
}

void RendererLogCrashContext()
{
    if (!g_compiling.active)
    {
        LogFlush();
        return;
    }
    Log("crash: this thread was inside the driver compiling pipeline vs %016llx ps %016llx; it will be skipped on the next start",
        (unsigned long long)g_compiling.vs, (unsigned long long)g_compiling.ps);
    if (FILE* f = fopen(kBadPipelinesFile, "a"))
    {
        fprintf(f, "%016llx %016llx\n", (unsigned long long)g_compiling.vs, (unsigned long long)g_compiling.ps);
        fclose(f);
    }
    LogFlush();
}

bool RendererCompileIdle()
{
    const uint64_t nowMs = uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
    const bool warming = (g_stats.warmTotal.load() && g_stats.warmDone.load() < g_stats.warmTotal.load()) || g_stats.staticTotal.load();
    return g_stats.busy.load() == 0 && !warming && nowMs - g_stats.lastColdMs.load() > 2000;
}

void RendererSaveCaches()
{
    g_diskWriter.Flush();
    g_pipelineLibrary.MaybeSave(true);
}

void RendererSetShader(uint32_t type, const uint32_t* microcodeBE, uint32_t usedDwords, uint64_t hash)
{
    std::vector<uint32_t>& code = type == 0 ? C().vsCode : C().psCode;
    uint64_t& current = type == 0 ? C().vsHash : C().psHash;
    // Titles reload the bound shader before most draws: the same hash is the same used microcode, so
    // the copy is already there (FH1: ~2% of the draw thread on an FX-8350). REGPU_NO_SAME_SHADER_SKIP=1: copy anyway.
    static LiveSwitch noSkip("REGPU_NO_SAME_SHADER_SKIP");
    if (current == hash && code.size() == usedDwords && !noSkip)
        return;
    code.assign(microcodeBE, microcodeBE + usedDwords);
    current = hash;
}

void RendererOnShaderLoad(uint32_t type, const uint32_t* microcodeBE, uint32_t dwords)
{
    std::vector<uint32_t>& code = type == 0 ? C().vsCode : C().psCode;
    code.assign(microcodeBE, microcodeBE + ucode::UsedDwords(microcodeBE, dwords));
    (type == 0 ? C().vsHash : C().psHash) = Fnv(code.data(), code.size() * 4) ^ type;
}

namespace
{
// REGPU_LOG_SKIPS=1: every frame that dropped draws logs how many and why (first 600 such frames), with the
// shaders of the first few: tells whether a glitch at load (a half-drawn minimap) is missing draws.
LiveSwitch g_sw_REGPU_LOG_SKIPS("REGPU_LOG_SKIPS");
struct SkipLog
{
    std::mutex mutex;
    uint64_t frame = ~0ull;
    std::map<std::string, uint32_t> reasons;
    std::vector<std::string> samples;
    uint32_t frames = 0;
    void Flush()
    {
        if (reasons.empty())
            return;
        if (frames++ < 600)
        {
            std::string line;
            for (const auto& [why, n] : reasons)
                line += why + " x" + std::to_string(n) + "; ";
            for (const auto& s : samples)
                line += s + "; ";
            Log("skips: frame %llu: %s", (unsigned long long)frame, line.c_str());
        }
        reasons.clear();
        samples.clear();
    }
} g_skipLog;

void TraceSkip(const char* why, uint32_t initiator)
{
    if (g_sw_REGPU_LOG_SKIPS)
    {
        std::lock_guard lock(g_skipLog.mutex);
        if (g_skipLog.frame != g_r.frame)
        {
            g_skipLog.Flush();
            g_skipLog.frame = g_r.frame;
        }
        g_skipLog.reasons[why]++;
        if (g_skipLog.samples.size() < 4)
        {
            char buf[96];
            snprintf(buf, sizeof(buf), "%s vs %08llx ps %08llx", why, (unsigned long long)(C().vsHash >> 32), (unsigned long long)(C().psHash >> 32));
            g_skipLog.samples.push_back(buf);
        }
    }
    const uint64_t traceFrame = TraceFrame();
    if (traceFrame && g_r.frame + 1 == traceFrame)
        Log("skip draw (%s): prim %u count %u mode %u vs %016llx ps %016llx", why, initiator & 0x3F, initiator >> 16,
            gpureg::Get(RB_MODECONTROL) & 7, (unsigned long long)C().vsHash, (unsigned long long)C().psHash);
}
}

namespace
{
// Performance counters (REGPU_PERF=1 logs them once a second).
struct Perf
{
    std::chrono::steady_clock::time_point windowStart = std::chrono::steady_clock::now();
    double drawSeconds = 0, swapSeconds = 0;
    double section[8] = {};
    uint64_t tablesBuilt = 0, tablesReused = 0;
    std::chrono::steady_clock::time_point mark;
    uint64_t frames = 0, draws = 0, slowFrames = 0;
    double maxFrame = 0;
} g_perf;
void DrawImpl(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize);
}


// ---- Parallel draw pieces --------------------------------------------------------------
namespace
{
std::mutex g_poolMutex;

ID3D12GraphicsCommandList* AcquireList()
{
    FrameResources& f = Frame();
    std::lock_guard lock(g_poolMutex);
    if (f.poolUsed == f.pool.size())
    {
        auto& e = f.pool.emplace_back();
        Check(g_r.device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&e.first)), "CreateCommandAllocator(piece)");
        Check(g_r.device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, e.first.Get(), nullptr, IID_PPV_ARGS(&e.second)),
            "CreateCommandList(piece)");
    }
    else
    {
        auto& e = f.pool[f.poolUsed];
        e.first->Reset();
        e.second->Reset(e.first.Get(), nullptr);
    }
    return f.pool[f.poolUsed++].second.Get();
}

// The state every draw list starts with.
void ListPrologue(RecordedList& list)
{
    ID3D12DescriptorHeap* heaps[] = { g_r.srvHeap.Get(), g_r.samplerHeap.Get() };
    list.SetDescriptorHeaps(g_r.samplerHeap ? 2 : 1, heaps);
    list.SetGraphicsRootSignature(g_r.rootSignature.Get());
    list.IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
}

// Command lists closed but not yet submitted, in order. Parallel segments end the main list and add
// their pieces several dozen times a frame; submitted one segment at a time, each submission left the
// GPU idle a moment (FM2: GPU time per frame 5.0 -> 6.5 ms with 3 draw workers). They are queued here
// and go with the next real submission (the frame's, a fence's, or one that must reach the GPU now).
// REGPU_NO_DEFER_LISTS=1: at once.
std::mutex g_pendingMutex;
std::vector<ID3D12CommandList*> g_pendingLists;

// Submits the queued lists, then these.
void SubmitLists(UINT n, ID3D12CommandList* const* lists)
{
    std::lock_guard lock(g_pendingMutex);
    g_pendingLists.insert(g_pendingLists.end(), lists, lists + n);
    if (!g_pendingLists.empty())
        g_r.queue->ExecuteCommandLists(UINT(g_pendingLists.size()), g_pendingLists.data());
    g_pendingLists.clear();
}

// Queues lists for the next submission (or submits them, with REGPU_NO_DEFER_LISTS).
void QueueLists(UINT n, ID3D12CommandList* const* lists)
{
    static const bool now = getenv("REGPU_NO_DEFER_LISTS") != nullptr;
    if (now)
        return SubmitLists(n, lists);
    std::lock_guard lock(g_pendingMutex);
    g_pendingLists.insert(g_pendingLists.end(), lists, lists + n);
}

// Ends the main list so far (queued for submission) and continues the frame in a fresh one.
void EndOcclusionQuery();
void EndOcclusionPart();

void SplitMainList()
{
    // A D3D query must end in the list it began in: an open guest query ends its part here and goes on
    // in the new list. (Ending the guest query, as before, left the guest's own end without a result:
    // answered as fully visible, with the periodic submits a query here and there every frame.)
    const bool queryOpen = g_occlusionOpen;
    if (queryOpen)
        EndOcclusionPart();
    ID3D12GraphicsCommandList* done = g_r.recorder.Real();
    done->Close();
    ID3D12CommandList* lists[] = { done };
    QueueLists(1, lists);
    g_r.recorder.Rebind(AcquireList());
    ListPrologue(g_r.recorder);
    g_drawState.Invalidate();
    SelectSurfaces(true);
    if (queryOpen)
    {
        if (g_occlusionCount + 1 <= kOcclusionPerFrame)
        {
            g_occlusionIndex = g_r.frameIndex * kOcclusionPerFrame + g_occlusionCount++;
            g_occlusionCurrent.main.push_back(g_occlusionIndex);
            C().list->BeginQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex);
        }
        else
        {
            g_occlusionOpen = false; // out of D3D queries: what was counted so far is its result
            g_occlusionPending[g_r.frameIndex].push_back(g_occlusionCurrent);
        }
    }
}

// SplitMainList without waiting for the replay thread: the closed part is queued (and with submit, the
// queue submitted) by the replay thread when it gets there (RecordedList::HandOff). Lists queued by the
// command processor itself go through the stream too (QueueInStream), so the order holds; whatever
// queues directly drains first (Real(), WaitForQueueIdle).
void QueueClosed(ID3D12GraphicsCommandList* closed, void*)
{
    closed->Close();
    ID3D12CommandList* lists[] = { closed };
    QueueLists(1, lists);
}
void QueueClosedAndSubmit(ID3D12GraphicsCommandList* closed, void* ctx)
{
    QueueClosed(closed, ctx);
    SubmitLists(0, nullptr);
}
void SplitMainListAsync(bool submit)
{
    static LiveSwitch sync("REGPU_SYNC_SPLITS"); // (A/B: wait for the replay as before)
    if (sync)
    {
        SplitMainList();
        if (submit)
            SubmitLists(0, nullptr);
        return;
    }
    const bool queryOpen = g_occlusionOpen;
    if (queryOpen)
        EndOcclusionPart();
    g_r.recorder.HandOff(AcquireList(), submit ? QueueClosedAndSubmit : QueueClosed, nullptr);
    ListPrologue(g_r.recorder);
    g_drawState.Invalidate();
    SelectSurfaces(true);
    if (queryOpen)
    {
        if (g_occlusionCount + 1 <= kOcclusionPerFrame)
        {
            g_occlusionIndex = g_r.frameIndex * kOcclusionPerFrame + g_occlusionCount++;
            g_occlusionCurrent.main.push_back(g_occlusionIndex);
            C().list->BeginQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex);
        }
        else
        {
            g_occlusionOpen = false;
            g_occlusionPending[g_r.frameIndex].push_back(g_occlusionCurrent);
        }
    }
}
// Lists queued at this point of the main stream (the draw workers' pieces).
void QueueInStream(std::vector<ID3D12CommandList*> lists)
{
    static LiveSwitch sync("REGPU_SYNC_SPLITS");
    if (sync)
        return QueueLists(UINT(lists.size()), lists.data());
    auto* held = new std::vector<ID3D12CommandList*>(std::move(lists));
    g_r.recorder.HandOff(nullptr, [](ID3D12GraphicsCommandList*, void* ctx) {
        auto* v = static_cast<std::vector<ID3D12CommandList*>*>(ctx);
        QueueLists(UINT(v->size()), v->data());
        delete v;
    }, held);
}

// Waits until everything submitted to the queue so far has finished.
void WaitForQueueIdle()
{
    g_r.recorder.Real(); // (hand-offs still in the replay stream queue their lists first)
    SubmitLists(0, nullptr); // (queued lists first: the signal follows them)
    const UINT64 value = g_r.nextFence++;
    g_r.queue->Signal(g_r.fence.Get(), value);
    if (g_r.fence->GetCompletedValue() < value)
    {
        WaitFenceValue(value, 2000);
    }
}

// Ends the open query's current D3D part (the guest query stays open).
void EndOcclusionPart()
{
    C().list->EndQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex);
    C().list->ResolveQueryData(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex, 1, g_occlusionReadback.Get(), UINT64(g_occlusionIndex) * 8);
}

void EndOcclusionQuery()
{
    g_occlusionOpen = false;
    C().list->EndQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex);
    C().list->ResolveQueryData(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex, 1, g_occlusionReadback.Get(), UINT64(g_occlusionIndex) * 8);
    g_occlusionPending[g_r.frameIndex].push_back(g_occlusionCurrent);
}

// Deferred write-backs (regpu.h Writeback::Mode::Deferred): the requested part of a resolve is copied to
// a readback buffer with its frame, and written to guest memory when that frame slot comes round again,
// a frame or two late and without a stall (waiting for each one ran a game reading its picture every
// frame at a third of its speed). Depth goes out as the console stores a D24FS8 copy: tiled 32-bit
// words, the 20e4 float depth in the top 24 bits; colour as tiled A8R8G8B8.
struct DeferredWriteback
{
    ComPtr<ID3D12Resource> buffer;
    UINT pitch = 0, hostW = 0, hostH = 0;
    DXGI_FORMAT hostFormat = DXGI_FORMAT_R8G8B8A8_UNORM;
    uint32_t base = 0, width = 0, height = 0, info = 0, x0 = 0, x1 = 0, y0 = 0, y1 = 0;
    bool depth = false;
    // Fence-ordered write-back (FenceWritebacks): generation << 2 | state (0 waiting, 1 being written,
    // 2 written); the generation changes each time the entry is reused, so a late claim cannot take
    // the next use's entry.
    std::atomic<uint64_t> state{ 0 };
};
// A deque: entries keep their address while the fence writer thread may hold them.
std::deque<DeferredWriteback> g_deferredWritebacks[kFrames];
size_t g_deferredUsed[kFrames] = {};
size_t g_deferredFenced[kFrames] = {}; // entries already handed to the fence writer

// With fences written after the host GPU (the fence writer thread, gpu.cpp), each deferred write-back
// is written to guest memory just before the first fence after it: a title that reads a small resolved
// image once it sees the next fence (as on the console) finds it there, and the command processor never
// waits. Entries not written by then land at their frame slot's next start, as before. From the FM4
// port. REGPU_NO_FENCE_WRITEBACKS=1: only at the frame slot (A/B).
bool FenceWritebacks()
{
    static const bool on = !getenv("REGPU_NO_FENCE_WRITEBACKS");
    return on;
}
struct FencedWriteback
{
    DeferredWriteback* w;
    uint64_t fence, generation;
    UINT slot;
};
std::mutex g_fencedMutex;
std::deque<FencedWriteback> g_fenced;

uint32_t EncodeDepth20e4(float v)
{
    v = std::clamp(v, 0.0f, 1.0f);
    if (v < std::ldexp(1.0f, -14))
        return std::min<uint32_t>(uint32_t(std::lround(std::ldexp(double(v), 34))), 0xFFFFF); // denormal: m / 2^20 * 2^-14
    int e = 0;
    (void)std::frexp(double(v), &e); // v = f * 2^e, f in [0.5, 1)
    const int exponent = std::clamp(e - 1 + 15, 1, 15);
    const double mantissa = std::ldexp(double(v), 15 - exponent) - 1.0;
    return uint32_t(exponent) << 20 | std::min<uint32_t>(uint32_t(std::lround(mantissa * 1048576.0)), 0xFFFFF);
}

// Writes the game pixels [x0, x1) x [y0, y1) of a resolve to guest memory as the GPU stores its
// destination: tiled (32-texel aligned pitch), in the destination format (RB_COPY_DEST_INFO
// bits 7-12) with the copy's red/blue swap and endian mode (gpu/resolve_pack). `pixels` is the
// host texture read back (RGBA8, RGBA16F, or R32F for depth), hostW x hostH texels `pitch` bytes
// apart, covering the game pixels at the host's render scale. Each game pixel is the average of
// the texels it covers for colour, the one at its centre for depth (where the console's pixel
// sampled; REGPU_WRITEBACK_POINT takes that for colour too). Depth goes out as the console stores
// a D24FS8 copy: 32-bit words, the 20e4 float depth in the top 24 bits. (From the FM4 port: 8888
// only before, and the top-left texel of each scaled pixel.)
void WriteResolvePixels(const uint8_t* pixels, UINT pitch, DXGI_FORMAT hostFormat, UINT hostW, UINT hostH, uint32_t base, uint32_t width,
    uint32_t info, bool depth, uint32_t x0, uint32_t x1, uint32_t y0, uint32_t y1)
{
    static LiveSwitch pointColor("REGPU_WRITEBACK_POINT");
    const uint32_t endian = info & 7, destFormat = depth ? 6 : (info >> 7) & 0x3F;
    const bool swapRB = !depth && ((info >> 24) & 1);
    uint32_t log2Bytes = 2;
    if (!depth && !regpu::resolve::DestLog2Bytes(destFormat, log2Bytes))
        return;
    const uint32_t bytes = 1u << log2Bytes, pitchTexels = (width + 31) & ~31u;
    const UINT hostBpp = hostFormat == DXGI_FORMAT_R16G16B16A16_FLOAT ? 8 : 4;
    const bool point = depth || pointColor;
    uint8_t* guest = GuestPtr<uint8_t>(GpuPhysToGuest(base));
    // The host texels [first, last) that game pixel i of n covers when the n pixels span `host`
    // texels: at least one, and only the one at its centre when point sampling.
    const auto span = [point](uint32_t i, uint32_t n, UINT host, UINT& first, UINT& last) {
        if (point)
        {
            first = std::min<UINT>(UINT(uint64_t(2 * i + 1) * host / (2 * uint64_t(n))), host - 1);
            last = first + 1;
            return;
        }
        first = std::min<UINT>(UINT(uint64_t(i) * host / n), host - 1);
        last = std::clamp<UINT>(UINT(uint64_t(i + 1) * host / n), first + 1, host);
    };
    for (uint32_t y = y0; y < y1; y++)
    {
        UINT hy0, hy1;
        span(y - y0, y1 - y0, hostH, hy0, hy1);
        for (uint32_t x = x0; x < x1; x++)
        {
            UINT hx0, hx1;
            span(x - x0, x1 - x0, hostW, hx0, hx1);
            uint32_t words[4] = {};
            if (depth)
                words[0] = EncodeDepth20e4(*reinterpret_cast<const float*>(pixels + size_t(hy0) * pitch + size_t(hx0) * 4)) << 8;
            else
            {
                float c[4] = {};
                for (UINT hy = hy0; hy < hy1; hy++)
                    for (UINT hx = hx0; hx < hx1; hx++)
                    {
                        const uint8_t* px = pixels + size_t(hy) * pitch + size_t(hx) * hostBpp;
                        for (int k = 0; k < 4; k++)
                        {
                            // (Halves only from an 8-byte texel: read from an RGBA8 one, the last texel's
                            // read ran 4 bytes past the buffer, a fault where it ends on a page: the FX's GTX 970.)
                            if (hostBpp == 8)
                            {
                                uint16_t h;
                                memcpy(&h, px + k * 2, 2);
                                c[k] += regpu::resolve::HalfToFloat(h);
                            }
                            else
                                c[k] += float(px[k]) / 255.0f;
                        }
                    }
                const float n = float((hx1 - hx0) * (hy1 - hy0));
                for (float& v : c)
                    v /= n;
                if (swapRB)
                    std::swap(c[0], c[2]);
                regpu::resolve::PackPixel(destFormat, c, words);
            }
            const size_t at = TextureTiledOffset(x, y, pitchTexels, log2Bytes);
            const uint8_t* b = reinterpret_cast<const uint8_t*>(words);
            for (uint32_t k = 0; k < bytes; k++)
                guest[regpu::resolve::SwappedByte(at + k, endian)] = b[k];
        }
    }
}

// Writes one entry's pixels if this caller claims it (state waiting, generation as expected).
bool ClaimAndWrite(DeferredWriteback& w, uint64_t generation)
{
    uint64_t expected = generation << 2;
    if (!w.state.compare_exchange_strong(expected, (generation << 2) | 1))
        return false;
    const uint8_t* pixels = nullptr;
    if (SUCCEEDED(w.buffer->Map(0, nullptr, reinterpret_cast<void**>(const_cast<uint8_t**>(&pixels)))) && pixels)
    {
        WriteResolvePixels(pixels, w.pitch, w.hostFormat, w.hostW, w.hostH, w.base, w.width, w.info, w.depth, w.x0, w.x1, w.y0, w.y1);
        const D3D12_RANGE none{ 0, 0 };
        w.buffer->Unmap(0, &none);
    }
    w.state = (generation << 2) | 2;
    return true;
}

void FinishDeferredWritebacks(UINT frameIndex)
{
    {
        // This slot's entries leave the fence writer's list: they are finished here.
        std::lock_guard lock(g_fencedMutex);
        std::erase_if(g_fenced, [&](const FencedWriteback& f) { return f.slot == frameIndex; });
    }
    g_deferredFenced[frameIndex] = 0;
    for (size_t i = 0; i < g_deferredUsed[frameIndex]; i++)
    {
        DeferredWriteback& w = g_deferredWritebacks[frameIndex][i];
        // Written by the fence writer already (or being written: wait for it), else written here.
        const uint64_t generation = w.state.load() >> 2;
        if (!ClaimAndWrite(w, generation))
            while ((w.state.load() & 3) == 1)
                std::this_thread::yield();
        if (auto it = g_r.resolved.find(w.base); it != g_r.resolved.end())
            NoteResolvedMemory(w.base, it->second); // our own write: not a reuse of the memory
    }
    g_deferredUsed[frameIndex] = 0;
}

// Queues the rows of this resolve (rect) inside the requested region for a deferred write-back.
void QueueDeferredWriteback(uint32_t base, uint32_t width, uint32_t height, uint32_t info, bool depth, const D3D12_RECT& rect,
    const regpu::Writeback& request)
{
    const auto it = g_r.resolved.find(base);
    if (it == g_r.resolved.end() || !it->second.resource ||
        (depth ? it->second.format != DXGI_FORMAT_R32_FLOAT
               : it->second.format != DXGI_FORMAT_R8G8B8A8_UNORM && it->second.format != DXGI_FORMAT_R16G16B16A16_FLOAT))
        return;
    const uint32_t x0 = std::min(request.x0, width), x1 = std::min(request.x1, width);
    const uint32_t y0 = std::max<uint32_t>(std::min(request.y0, height), uint32_t(std::max<LONG>(rect.top, 0)));
    const uint32_t y1 = std::min<uint32_t>(std::min(request.y1, height), uint32_t(std::max<LONG>(rect.bottom, 0)));
    if (x1 <= x0 || y1 <= y0)
        return;
    ResolvedTexture& r = it->second;
    auto& slot = g_deferredWritebacks[g_r.frameIndex];
    if (g_deferredUsed[g_r.frameIndex] == slot.size())
        slot.emplace_back();
    DeferredWriteback& w = slot[g_deferredUsed[g_r.frameIndex]++];
    w.state = ((w.state.load() >> 2) + 1) << 2; // a new use: next generation, waiting
    const UINT hostX0 = UINT(uint64_t(x0) * r.hostWidth / width), hostY0 = UINT(uint64_t(y0) * r.hostHeight / height);
    w.hostW = std::max<UINT>(1, std::min<UINT>(UINT(uint64_t(x1) * r.hostWidth / width), r.hostWidth) - hostX0);
    w.hostH = std::max<UINT>(1, std::min<UINT>(UINT(uint64_t(y1) * r.hostHeight / height), r.hostHeight) - hostY0);
    w.hostFormat = r.format;
    w.pitch = (w.hostW * (r.format == DXGI_FORMAT_R16G16B16A16_FLOAT ? 8 : 4) + 255) & ~255u;
    const UINT64 bytes = UINT64(w.pitch) * w.hostH;
    if (!w.buffer || w.buffer->GetDesc().Width < bytes)
        w.buffer = CreateBuffer(std::max<UINT64>(bytes, 1u << 20), D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    D3D12_RESOURCE_BARRIER bar{};
    bar.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    bar.Transition.pResource = r.resource.Get();
    bar.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    bar.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    C().list->ResourceBarrier(1, &bar);
    D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
    dst.pResource = w.buffer.Get();
    dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    dst.PlacedFootprint.Footprint = { r.format, w.hostW, w.hostH, 1, w.pitch };
    src.pResource = r.resource.Get();
    src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    const D3D12_BOX box{ hostX0, hostY0, 0, hostX0 + w.hostW, hostY0 + w.hostH, 1 };
    C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, &box);
    std::swap(bar.Transition.StateBefore, bar.Transition.StateAfter);
    C().list->ResourceBarrier(1, &bar);
    w.base = base;
    w.width = width;
    w.height = height;
    w.info = info;
    w.depth = depth;
    w.x0 = x0, w.x1 = x1, w.y0 = y0, w.y1 = y1;
}

// Before a fence write from the command stream: what has been recorded is submitted and a host
// fence marks its end, so the title only sees the fence once the draws before it have really read
// their data. Written at record time, it reused vertex and index buffers the GPU had yet to read
// (PGR4: torn geometry spiking into the sky for a frame, crowd poses snapping). The command
// processor used to wait for the GPU here, which kept the CPU and GPU from ever overlapping
// (PGR4 ran at 25 fps); the write now waits on its own thread (gpu.cpp).
// REGPU_EARLY_FENCES writes them at record time again (A/B).
uint64_t FenceSubmitImpl()
{
    static LiveSwitch early("REGPU_EARLY_FENCES");
    if (!g_r.ok)
        return 0;
    // With record-time fences, a fence that follows a small resolve write-back of this frame still goes
    // after the host GPU (and the write-back), on the fence writer: the title finds the image in memory
    // once it sees that fence. Record-time fences behind it queue after it on the writer, so values stay
    // in order. REGPU_NO_WRITEBACK_FENCES=1: every fence at record time (the write-backs land later).
    if (early)
    {
        static const bool writebackFences = !getenv("REGPU_NO_WRITEBACK_FENCES");
        if (!writebackFences || !FenceWritebacks() || g_deferredUsed[g_r.frameIndex] <= g_deferredFenced[g_r.frameIndex])
            return 0;
    }
    // Nothing recorded since the last one: the same point in the queue.
    static uint64_t lastFrame = ~0ull, lastValue = 0;
    static uint32_t lastDraws = ~0u;
    const uint32_t draws = g_r.drawsThisFrame.load();
    const UINT slot = g_r.frameIndex;
    const bool newWritebacks = FenceWritebacks() && g_deferredUsed[slot] > g_deferredFenced[slot];
    if (lastFrame == g_r.frame && lastDraws == draws && !newWritebacks)
        return lastValue;
    lastFrame = g_r.frame;
    lastDraws = draws;
    BeginFrameIfNeeded();
    SplitMainList();
    SubmitLists(0, nullptr); // (the signal must follow this fence's draws)
    lastValue = g_r.nextFence++;
    g_r.queue->Signal(g_r.fence.Get(), lastValue);
    if (FenceWritebacks())
    {
        // The write-backs recorded before this fence: the fence writer lands them before the fence.
        std::lock_guard lock(g_fencedMutex);
        for (size_t i = g_deferredFenced[slot]; i < g_deferredUsed[slot]; i++)
        {
            DeferredWriteback& w = g_deferredWritebacks[slot][i];
            g_fenced.push_back({ &w, lastValue, w.state.load() >> 2, slot });
        }
        g_deferredFenced[slot] = g_deferredUsed[slot];
    }
    return lastValue;
}

void WriteResolveToGuest(uint32_t base, uint32_t width, uint32_t height, uint32_t info)
{
    const auto it = g_r.resolved.find(base);
    if (it == g_r.resolved.end() || !it->second.resource || !width || !height ||
        (it->second.format != DXGI_FORMAT_R8G8B8A8_UNORM && it->second.format != DXGI_FORMAT_R16G16B16A16_FLOAT))
        return;
    ResolvedTexture& r = it->second;
    // Run what has been recorded (the resolve's copy included) and let the GPU finish it.
    BeginFrameIfNeeded();
    SplitMainList();
    WaitForQueueIdle();
    const UINT pitch = (r.hostWidth * (r.format == DXGI_FORMAT_R16G16B16A16_FLOAT ? 8 : 4) + 255) & ~255u;
    ComPtr<ID3D12Resource> buffer = CreateBuffer(UINT64(pitch) * r.hostHeight, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    ID3D12GraphicsCommandList* list = AcquireList();
    D3D12_RESOURCE_BARRIER bar{};
    bar.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    bar.Transition.pResource = r.resource.Get();
    bar.Transition.Subresource = 0;
    bar.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
    bar.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    list->ResourceBarrier(1, &bar);
    D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
    dst.pResource = buffer.Get();
    dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    dst.PlacedFootprint.Footprint = { r.format, r.hostWidth, r.hostHeight, 1, pitch };
    src.pResource = r.resource.Get();
    src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    std::swap(bar.Transition.StateBefore, bar.Transition.StateAfter);
    list->ResourceBarrier(1, &bar);
    list->Close();
    ID3D12CommandList* lists[] = { list };
    SubmitLists(1, lists);
    WaitForQueueIdle();
    uint8_t* pixels = nullptr;
    buffer->Map(0, nullptr, reinterpret_cast<void**>(&pixels));
    if (!pixels)
        return;
    WriteResolvePixels(pixels, pitch, r.format, r.hostWidth, r.hostHeight, base, width, info, false, 0, width, 0, height);
    buffer->Unmap(0, nullptr);
    NoteResolvedMemory(base, r); // our own write: not a reuse of the memory
    Log("resolve: wrote %ux%u back to guest memory at %08X (format %u, endian %u, swap %u)", width, height, base, (info >> 7) & 0x3F, info & 7,
        (info >> 24) & 1);
}

struct Workers
{
    bool started = false;
    std::mutex mutex;
    std::condition_variable start, done;
    uint64_t generation = 0;
    const std::function<void(uint32_t)>* job = nullptr;
    uint32_t count = 0;
    std::atomic<uint32_t> next{ 0 }, finished{ 0 };
    std::vector<ID3D12GraphicsCommandList*> lists;
    std::vector<uint64_t> pieceNs; // REGPU_TRACE_PIECES
    std::vector<uint32_t> pieceThread;
} g_workers;

// One piece on this thread, with a worker context of its own.
std::atomic<uint64_t> g_pieceNs{ 0 }, g_segmentNs{ 0 }, g_pieceCount{ 0 }, g_segmentCount{ 0 };
void TranslatePiece(DrawContext& ctx, uint32_t i)
{
    const auto t0 = std::chrono::steady_clock::now();
    ID3D12GraphicsCommandList* real = AcquireList();
    RecordedList recorder;
    recorder.Init(real, true);
    ctx = DrawContext{};
    ctx.list = &recorder;
    ctx.worker = true;
    DrawContext* saved = t_context;
    t_context = &ctx;
    ListPrologue(recorder);
    g_drawState.Invalidate();
    (*g_workers.job)(i);
    g_r.drawsThisFrame += ctx.draws;
    t_context = saved;
    const auto t1 = std::chrono::steady_clock::now();
    real->Close();
    g_workers.lists[i] = real;
    g_workers.pieceNs[i] = uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count());
    g_workers.pieceThread[i] = GetCurrentThreadId();
    g_pieceNs += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - t0).count());
    g_pieceCount++;
}

}
HANDLE g_profiledWorker = nullptr; // REGPU_PROFILE=w
namespace
{
void WorkerMain()
{
    SetThreadDescription(GetCurrentThread(), L"gpu draw piece worker");
    if (!g_profiledWorker)
        DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_profiledWorker, 0, FALSE, DUPLICATE_SAME_ACCESS);
    DrawContext ctx;
    uint64_t seen = 0;
    for (;;)
    {
        {
            std::unique_lock lock(g_workers.mutex);
            g_workers.start.wait(lock, [&] { return g_workers.generation != seen; });
            seen = g_workers.generation;
        }
        for (uint32_t i; (i = g_workers.next.fetch_add(1)) < g_workers.count;)
        {
            TranslatePiece(ctx, i);
            if (g_workers.finished.fetch_add(1) + 1 == g_workers.count)
            {
                std::lock_guard lock(g_workers.mutex);
                g_workers.done.notify_all();
            }
        }
    }
}
}

// Exceptions escaping a thread end the process silently: say where.
void InstallTerminateLog()
{
    static std::once_flag once;
    std::call_once(once, [] {
        std::set_terminate([] {
            std::string what = "?";
            if (auto e = std::current_exception())
                try { std::rethrow_exception(e); } catch (const std::exception& x) { what = x.what(); } catch (...) {}
            Log("CRASH: std::terminate (%s): %s", what.c_str(), regpu::GetHost().currentStack ? regpu::GetHost().currentStack(24).c_str() : "");
            fflush(nullptr);
            std::abort();
        });
    });
}

uint32_t RendererDrawWorkers()
{
    InstallTerminateLog();
    static const uint32_t n = [] {
        const char* v = getenv("REGPU_DRAW_WORKERS");
        const uint32_t hw = std::max(2u, std::thread::hardware_concurrency());
        (void)hw;
        // Off by default: measured slower than the serial path so far (per-draw cost roughly
        // doubles across threads and the command processor waits for each segment).
        return v ? std::min<uint32_t>(uint32_t(atoi(v)), hw) : 0u;
    }();
    return n;
}

bool RendererParallelReady()
{
    // REGPU_NO_DRAW_WORKERS: the pool stays, draws go serial (A/B live, "switch REGPU_NO_DRAW_WORKERS 1")
    static LiveSwitch off("REGPU_NO_DRAW_WORKERS");
    return RendererDrawWorkers() > 0 && !off && g_r.ok && GameSettled() && GetSettings().upscaler == Settings::Upscaler::Off && !RendererTracing();
}

bool RendererIsResolveDraw()
{
    return (gpureg::Get(RB_MODECONTROL) & 7) == 6;
}

uint64_t RendererFenceSubmit() { return FenceSubmitImpl(); }
bool RendererEarlyFences()
{
    static const bool on = getenv("REGPU_EARLY_FENCES") != nullptr;
    return on;
}

void RendererScreenScissor(int32_t rect[4]) { ScreenScissor(rect); }

bool RendererTileDrop(uint32_t surface, const int32_t rect[4], bool resolve, uint32_t initiator, bool replay, uint32_t dmaBase, uint32_t dmaSize)
{
    static LiveSwitch tracePhase("REGPU_TRACE_TILE_PHASE"); // the tile pass's resolves and draw counts
    TileMerge::Phase& p = TileMerge::g_phase;
    if (tracePhase && resolve)
        Log("tile resolve: surface %08X (pass %08X) scissor %d,%d-%d,%d replay %d current %d first resolved %d later resolved %d, draws %u %u %u %u", surface,
            p.surface, rect[0], rect[1], rect[2], rect[3], int(replay), p.current, int(p.firstResolved), int(p.laterResolved[0]), p.counts[0], p.counts[1],
            p.counts[2], p.counts[3]);
    const TileMerge::Table* t = TileMerge::g_table.load(std::memory_order_acquire);
    if (!t || !TileMerge::Enabled() || TileMerge::g_unsafe.count(surface))
        return false;
    if (false)
        Log("tile resolve: scissor %d,%d-%d,%d replay %d current %d first resolved %d later resolved %d, draws %u %u %u %u", rect[0], rect[1], rect[2], rect[3],
            int(replay), p.current, int(p.firstResolved), int(p.laterResolved[0]), p.counts[0], p.counts[1], p.counts[2], p.counts[3]);
    for (const TileMerge::Entry& e : t->entries)
    {
        if (e.surface != surface || e.learnedFrame >= g_r.frame)
            continue;
        if (memcmp(e.first, rect, sizeof(e.first)) == 0)
        {
            // The first tile: a new pass when we come from a later tile (or another surface).
            if (p.surface != surface || p.inLater)
            {
                if (tracePhase && p.surface)
                    Log("tile pass %08X: first tile %u + %u draws (before/after its first resolve), later %u + %u; bases %08X / %08X", p.surface, p.counts[0],
                        p.counts[1], p.counts[2], p.counts[3], p.bases[0], p.bases[1]);
                p = { surface };
            }
            p.current = 0;
            p.counts[p.firstResolved ? 1 : 0]++;
            if (!p.firstResolved && !resolve)
                p.drawn.Add(TileMerge::DrawKey(initiator, dmaBase, dmaSize));
            p.bases[0] = (gpureg::Get(RB_COLOR_INFO) & 0xFFF) << 16 | (gpureg::Get(RB_DEPTH_INFO) & 0xFFF);
            if (resolve)
            {
                // (Before the renderer sees it: the resolve itself is not widened, and its clear
                // must not reach the later tiles' rows, which this tile has drawn already.)
                TileMerge::g_mergedResolve = true; // (every one: PGR4 ends a tile with a copy, then a clearing resolve)
                p.firstResolved = true;
            }
            return false;
        }
        for (uint32_t i = 0; i < e.laterCount; i++)
            if (memcmp(e.later[i], rect, sizeof(e.later[i])) == 0)
            {
                if (p.surface != surface)
                    return false; // not seen its first tile: draw as is
                p.inLater = true;
                p.current = int(i) + 1;
                p.counts[p.laterResolved[i] ? 3 : 2]++;
                p.bases[1] = (gpureg::Get(RB_COLOR_INFO) & 0xFFF) << 16 | (gpureg::Get(RB_DEPTH_INFO) & 0xFFF);
                if (resolve)
                {
                    static LiveSwitch dropResolves("REGPU_TILE_MERGE_DROP_RESOLVES"); // debug
                    if (dropResolves)
                        return true;
                    p.laterResolved[i] = true;
                    return false;
                }
                if (!p.laterResolved[i] && (initiator & 0x3F) == 8) // a rectangle list: a clear of the tile
                {
                    TileMerge::g_unsafe.insert(surface);
                    Log("tile merge: surface %08X clears its tiles with rectangles, not merged", surface);
                    return false;
                }
                static LiveSwitch keep("REGPU_TILE_MERGE_KEEP"); // debug: widen the first tile, keep the others' draws
                // Debug (bisecting a draw that must stay): out/tile_keep.txt "from to" keeps the later
                // tile's draws with those numbers (re-read every 60 frames).
                static uint32_t keepFrom = 0, keepTo = 0;
                static uint64_t keepRead = 0;
                if (g_r.frame >= keepRead + 60)
                {
                    keepRead = g_r.frame;
                    keepFrom = keepTo = 0;
                    if (FILE* f = fopen("out/tile_keep.txt", "r"))
                    {
                        if (fscanf(f, "%u %u", &keepFrom, &keepTo) != 2)
                            keepFrom = keepTo = 0;
                        fclose(f);
                    }
                }
                const uint32_t number = p.counts[2] - 1;
                if (number >= keepFrom && number < keepTo)
                    return false;
                if (p.laterResolved[i] || keep)
                    return false;
                // A draw with the stencil test on runs in its own tile: the stencil its widened first-tile
                // twin wrote in this tile's rows doesn't last to the draws here that test it (FH1's sky is
                // drawn where its stencil marks allow: with those dropped, the sky went grey for a frame
                // now and then while the camera moved). REGPU_TILE_MERGE_DROP_STENCIL=1: dropped too (A/B).
                static LiveSwitch dropStencil("REGPU_TILE_MERGE_DROP_STENCIL");
                if (!dropStencil && (gpureg::Get(RB_DEPTHCONTROL) & 1))
                    return false;
                // Drawn by the first tile: dropped (each first-tile draw stands for one later one).
                if (!p.drawn.Take(TileMerge::DrawKey(initiator, dmaBase, dmaSize)))
                    return false;
                static LiveSwitch traceDrops("REGPU_TRACE_TILE_DROPS"); // debug: one frame's dropped draws every 300
                if (traceDrops && g_r.frame % 300 == 0)
                    Log("tile drop: frame %llu #%u tile %d vs %016llx ps %016llx prim %u depthctl %08X colour %03X depth %03X", (unsigned long long)g_r.frame, number, int(i) + 1,
                        (unsigned long long)C().vsHash, (unsigned long long)C().psHash, initiator & 0x3F, gpureg::Get(RB_DEPTHCONTROL), gpureg::Get(RB_COLOR_INFO) & 0xFFF,
                        gpureg::Get(RB_DEPTH_INFO) & 0xFFF);
                return true;
            }
    }
    // A resolve with a scissor of its own during the pass (PGR4's menus resolve the scene mid-pass
    // and composite from it): it ends the merged part of the tile running.
    if (resolve && p.surface == surface && p.current >= 0)
    {
        if (p.current == 0)
            p.firstResolved = true;
        else
            p.laterResolved[p.current - 1] = true;
        return false;
    }
    // (Draws with a scissor of their own stay: D3D gives each tile the draw's scissor cut to the
    // tile, so a decal across the tiles' border reached the first tile cut short.)
    return false;
}

bool RendererTileReplay(uint32_t surface, const int32_t first[4], const int32_t replay[4])
{
    if (!TileMerge::Enabled())
        return false;
    const uint64_t now = g_r.frame;
    std::lock_guard lock(TileMerge::g_mutex);
    const TileMerge::Table* old = TileMerge::g_table.load(std::memory_order_acquire);
    TileMerge::Entry* found = nullptr;
    if (old)
        for (const auto& x : old->entries)
            if (x.surface == surface && memcmp(x.first, first, sizeof(x.first)) == 0)
                found = const_cast<TileMerge::Entry*>(&x);
    // Learned in an earlier frame and covering this replay: its draws were drawn by the first tile.
    if (found && found->learnedFrame < now && replay[0] >= found->all[0] && replay[1] >= found->all[1] && replay[2] <= found->all[2] &&
        replay[3] <= found->all[3])
        return true;
    auto* t = new TileMerge::Table(old ? *old : TileMerge::Table{});
    TileMerge::Entry* e = nullptr;
    for (auto& x : t->entries)
        if (x.surface == surface && memcmp(x.first, first, sizeof(x.first)) == 0)
            e = &x;
    if (!e)
    {
        if (t->entries.size() >= 16)
        {
            delete t;
            return false;
        }
        e = &t->entries.emplace_back();
        e->surface = surface;
        memcpy(e->first, first, sizeof(e->first));
        memcpy(e->all, first, sizeof(e->all));
    }
    bool known = false;
    for (uint32_t i = 0; i < e->laterCount; i++)
        known = known || memcmp(e->later[i], replay, sizeof(e->later[i])) == 0;
    if (!known && e->laterCount < 4)
        memcpy(e->later[e->laterCount++], replay, sizeof(e->later[0]));
    e->all[0] = std::min(e->all[0], replay[0]);
    e->all[1] = std::min(e->all[1], replay[1]);
    e->all[2] = std::max(e->all[2], replay[2]);
    e->all[3] = std::max(e->all[3], replay[3]);
    e->learnedFrame = now;
    Log("tile merge: surface %08X first tile %d,%d-%d,%d, all tiles %d,%d-%d,%d", surface, first[0], first[1], first[2], first[3], e->all[0], e->all[1],
        e->all[2], e->all[3]);
    TileMerge::g_table.store(t, std::memory_order_release); // (the old table is kept: readers may hold it)
    return false;
}

// The fence writer thread, once the host GPU has reached `completed`: the write-backs handed over
// with a fence value up to it are written to guest memory (before the fence itself).
void RendererFinishFencedWritebacks(uint64_t completed)
{
    for (;;)
    {
        FencedWriteback f;
        {
            std::lock_guard lock(g_fencedMutex);
            if (g_fenced.empty() || g_fenced.front().fence > completed)
                return;
            f = g_fenced.front();
            g_fenced.pop_front();
        }
        ClaimAndWrite(*f.w, f.generation);
    }
}

void RendererFenceWait(uint64_t value, void* event)
{
    if (g_r.fence->GetCompletedValue() >= value)
        return;
    g_r.fence->SetEventOnCompletion(value, HANDLE(event));
    WaitForSingleObject(HANDLE(event), 2000);
}

void RendererOcclusionBegin()
{
    if (!g_r.ok)
        Init();
    BeginFrameIfNeeded();
    if (g_occlusionFrame != g_r.frame)
    {
        g_occlusionFrame = g_r.frame;
        g_occlusionCount = 0;
        g_occlusionOrders = 0;
        g_occlusionKindCount.clear();
    }
    if (g_occlusionOpen || g_occlusionCount + 4 > kOcclusionPerFrame)
        return;
    if (!g_occlusionHeap)
    {
        D3D12_QUERY_HEAP_DESC qd{};
        qd.Type = D3D12_QUERY_HEAP_TYPE_OCCLUSION;
        qd.Count = kOcclusionPerFrame * kFrames;
        g_r.device->CreateQueryHeap(&qd, IID_PPV_ARGS(&g_occlusionHeap));
        g_occlusionReadback = CreateBuffer(8ull * kOcclusionPerFrame * kFrames, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
        if (!g_occlusionHeap || !g_occlusionReadback)
            return;
    }
    g_occlusionOrder = g_occlusionOrders++;
    g_occlusionCurrent = {};
    g_occlusionCurrent.order = g_occlusionOrder;
    g_occlusionIndex = g_r.frameIndex * kOcclusionPerFrame + g_occlusionCount++;
    g_occlusionCurrent.main.push_back(g_occlusionIndex);
    C().list->BeginQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex);
    g_occlusionOpen = true;
    if (const uint64_t t = TraceFrame(); t && g_r.frame + 1 == t)
        Log("occlusion query %u begins (draw #%u next)", g_occlusionOrder, g_r.drawsThisFrame.load() + 1);
}

bool OcclusionEndImpl(uint32_t& samples, uint32_t& total, uint32_t address);
namespace
{
namespace Unstable
{
bool On();
extern uint32_t g_logged;
}
}

// REGPU_TRACE_UNSTABLE: a query's answer that turns from hidden to visible or back between frames
// (no result yet counts as visible: the caller answers 1000 samples).
bool RendererOcclusionEnd(uint32_t& samples, uint32_t& total, uint32_t address)
{
    const UINT order = g_occlusionOrder;
    const bool open = g_occlusionOpen;
    const bool known = OcclusionEndImpl(samples, total, address);
    if (open && Unstable::On())
    {
        // By order in the frame and frame parity: -1 no result yet, 0 hidden, 1 visible.
        static std::vector<int> lastByParity[2];
        static std::vector<uint64_t> frameByParity[2];
        auto& last = lastByParity[g_r.frame & 1];
        auto& lastFrame = frameByParity[g_r.frame & 1];
        if (last.size() <= order)
        {
            last.resize(order + 1, -2);
            lastFrame.resize(order + 1, 0);
        }
        const int now = known ? (samples > 0 ? 1 : 0) : -1;
        if (last[order] != -2 && last[order] != now && lastFrame[order] + 2 == g_r.frame && Unstable::g_logged < 400)
        {
            Unstable::g_logged++;
            Log("unstable: frame %llu occlusion query %u: %s -> %s (%u samples)", (unsigned long long)g_r.frame, order,
                last[order] < 0 ? "no result (visible)" : last[order] ? "visible" : "hidden",
                now < 0 ? "no result (visible)" : now ? "visible" : "hidden", known ? samples : 0);
        }
        last[order] = now;
        lastFrame[order] = g_r.frame;
    }
    return known;
}

bool OcclusionEndImpl(uint32_t& samples, uint32_t& total, uint32_t address)
{
    if (!g_occlusionOpen)
        return false;
    const UINT order = g_occlusionOrder;
    g_occlusionCurrent.kind = uint64_t(address) << 1 | 1;
    g_occlusionCurrent.kindOrder = g_occlusionKindCount[g_occlusionCurrent.kind]++;
    const std::pair<uint64_t, UINT> kind{ g_occlusionCurrent.kind, g_occlusionCurrent.kindOrder };
    EndOcclusionQuery();
    // REGPU_OCCLUSION_BY_ORDER=1: answered by order in the frame, as before (A/B).
    static LiveSwitch byOrder("REGPU_OCCLUSION_BY_ORDER");
    int64_t passed = -1, covered = -1;
    if (byOrder)
    {
        if (order < g_occlusionResult.size())
            passed = g_occlusionResult[order], covered = g_occlusionCoverage[order];
    }
    else if (auto it = g_occlusionByKind.find(kind); it != g_occlusionByKind.end())
        std::tie(passed, covered) = it->second;
    if (const uint64_t t = TraceFrame(); t && g_r.frame + 1 == t)
        Log("occlusion query %u ends: kind %016llx #%u, answer passed %lld covered %lld (host samples)", order, (unsigned long long)kind.first,
            kind.second, (long long)passed, (long long)covered);
    if (passed < 0)
        return false;
    // Host samples to the console's: the output is ScaleX x ScaleY host pixels per game pixel
    // with g_msaa samples each; the guest surface has 1, 2 or 4 samples per pixel.
    const uint32_t guestMsaa = (gpureg::Get(RB_SURFACE_INFO) >> 16) & 3;
    const double perHost = double(1u << guestMsaa) / (double(ScaleX()) * double(ScaleY()) * double(g_msaa));
    samples = uint32_t(std::min(double(passed) * perHost + 0.5, 4294967295.0));
    // Total: the samples the query's draws covered, depth test aside (their coverage draws); without
    // one (REGPU_NO_OCCLUSION_COVERAGE, or none yet), the passed count as before.
    total = covered >= 0 ? std::max(samples, uint32_t(std::min(double(covered) * perHost + 0.5, 4294967295.0))) : samples;
    return true;
}

void RendererTrackTile(RendererTile& tile)
{
    const uint32_t mode = gpureg::Get(RB_MODECONTROL) & 7;
    if (mode != 4 && mode != 5)
        return;
    const D3D12_RECT scissor = WindowScissor();
    const uint32_t offsetReg = gpureg::Get(PA_SC_WINDOW_OFFSET);
    const int32_t wy = ((gpureg::Get(PA_SU_SC_MODE_CNTL) >> 16) & 1) ? int32_t((offsetReg >> 16) << 17) >> 17 : 0;
    const bool full = scissor.left <= 0 && scissor.top <= 0 && scissor.right >= LONG(PassWidth()) && scissor.bottom >= LONG(PassHeight());
    if (!full)
    {
        tile.originY = -wy;
        tile.rect[0] = scissor.left;
        tile.rect[1] = scissor.top;
        tile.rect[2] = scissor.right;
        tile.rect[3] = scissor.bottom;
    }
}

uint32_t RendererTileWidenNow(int32_t rect[4])
{
    // The scissor WindowScissor starts from (EDRAM space, then back to screen space with the vertex offset)
    const uint32_t offsetReg = gpureg::Get(PA_SC_WINDOW_OFFSET);
    const int32_t wx = int32_t(offsetReg << 17) >> 17, wy = int32_t((offsetReg >> 16) << 17) >> 17;
    const bool vertexOffset = (gpureg::Get(PA_SU_SC_MODE_CNTL) >> 16) & 1;
    const uint32_t tl = gpureg::Get(PA_SC_WINDOW_SCISSOR_TL), br = gpureg::Get(PA_SC_WINDOW_SCISSOR_BR);
    rect[0] = int32_t(tl & 0x3FFF), rect[1] = int32_t((tl >> 16) & 0x3FFF), rect[2] = int32_t(br & 0x3FFF), rect[3] = int32_t((br >> 16) & 0x3FFF);
    if (!(tl >> 31))
    {
        rect[0] += wx; rect[2] += wx; rect[1] += wy; rect[3] += wy;
    }
    if (vertexOffset)
    {
        rect[0] -= wx; rect[2] -= wx; rect[1] -= wy; rect[3] -= wy;
    }
    const bool cleared = TileMerge::g_phase.laterCleared;
    if (!TileMerge::Widen(gpureg::Get(0x2000 /* RB_SURFACE_INFO */), rect))
        return 0;
    return !cleared && TileMerge::g_phase.laterCleared ? 2 : 1;
}

void RendererForceTileWiden(bool set, bool widened, const int32_t rect[4])
{
    TileMerge::t_forced = set;
    TileMerge::t_forcedWidened = widened;
    if (set && rect)
        memcpy(TileMerge::t_forcedRect, rect, sizeof(TileMerge::t_forcedRect));
}

void RendererSetTile(const RendererTile& tile)
{
    g_tile.originY = tile.originY;
    g_tile.rect = { tile.rect[0], tile.rect[1], tile.rect[2], tile.rect[3] };
}

void RendererRunPieces(uint32_t n, const std::function<void(uint32_t)>& translate)
{
    if (!n)
        return;
    const auto segmentStart = std::chrono::steady_clock::now();
    struct Done
    {
        std::chrono::steady_clock::time_point start;
        ~Done()
        {
            g_segmentNs += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count());
            g_segmentCount++;
            static auto last = std::chrono::steady_clock::now();
            if (getenv("REGPU_PERF") && std::chrono::steady_clock::now() - last > std::chrono::seconds(2))
            {
                last = std::chrono::steady_clock::now();
                Log("perf: parallel segments %llu: %.1f ms wall, %.1f ms in %llu pieces (%.2fx)", (unsigned long long)g_segmentCount.load(),
                    g_segmentNs / 1e6, g_pieceNs / 1e6, (unsigned long long)g_pieceCount.load(), double(g_pieceNs) / std::max<uint64_t>(1, g_segmentNs));
                g_segmentNs = g_pieceNs = g_pieceCount = g_segmentCount = 0;
            }
        }
    } done{ segmentStart };
    BeginFrameIfNeeded();
    SplitMainListAsync(false);
    // Texture uploads of the pieces, executed before them.
    ID3D12GraphicsCommandList* prepReal = AcquireList();
    RecordedList prep;
    prep.Init(prepReal, true);
    g_prepList = &prep;
    {
        std::lock_guard lock(g_workers.mutex);
        if (!g_workers.started)
        {
            g_workers.started = true;
            // 16 MB stacks like the command processor's: shader translation and the HLSL
            // compiler can run on these threads, and they need far more than 1 MB.
            for (uint32_t i = 0; i < RendererDrawWorkers(); i++)
                if (HANDLE h = CreateThread(nullptr, 16u << 20, [](void*) -> DWORD { WorkerMain(); return 0; }, nullptr, STACK_SIZE_PARAM_IS_A_RESERVATION, nullptr))
                    CloseHandle(h);
        }
        g_workers.job = &translate;
        g_workers.count = n;
        g_workers.next = 0;
        g_workers.finished = 0;
        g_workers.lists.assign(n, nullptr);
        g_workers.pieceNs.assign(n, 0);
        g_workers.pieceThread.assign(n, 0);
        g_workers.generation++;
    }
    g_workers.start.notify_all();
    // This thread takes pieces too.
    static DrawContext ownContext;
    for (uint32_t i; (i = g_workers.next.fetch_add(1)) < n;)
    {
        TranslatePiece(ownContext, i);
        g_workers.finished.fetch_add(1);
    }
    {
        std::unique_lock lock(g_workers.mutex);
        g_workers.done.wait(lock, [] { return g_workers.finished.load() >= g_workers.count; });
    }
    if (static LiveSwitch trace("REGPU_TRACE_PIECES"); trace)
    {
        static uint32_t logged = 0;
        if (logged++ % 50 == 0)
        {
            std::string line;
            for (uint32_t i = 0; i < n; i++)
                line += " " + std::to_string(g_workers.pieceNs[i] / 1000) + "us@" + std::to_string(g_workers.pieceThread[i] % 10000);
            Log("pieces: wall %lldus, cp %u:%s", (long long)std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - segmentStart).count(),
                GetCurrentThreadId() % 10000, line.c_str());
        }
    }
    g_prepList = nullptr;
    prepReal->Close();
    std::vector<ID3D12CommandList*> lists;
    lists.push_back(prepReal);
    for (ID3D12GraphicsCommandList* l : g_workers.lists)
        lists.push_back(l);
    QueueInStream(std::move(lists));
}

void RendererOnDraw(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    if (g_gpuShaders)
        PassMark("vs %08X ps %08X", uint32_t(C().vsHash), uint32_t(C().psHash));
    if (!g_perfTiming)
    {
        DrawImpl(initiator, dmaBase, dmaSize);
        g_perf.draws++;
        g_passDraws++;
        return;
    }
    const auto t0 = std::chrono::steady_clock::now();
    DrawImpl(initiator, dmaBase, dmaSize);
    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    g_perf.drawSeconds += seconds;
    g_frameEvents.worstDrawMs = std::max(g_frameEvents.worstDrawMs, seconds * 1000.0);
    g_perf.draws++;
    g_passDraws++;
}

namespace
{
void Section(int i)
{
    if (!g_perfTiming)
        return;
    const auto now = std::chrono::steady_clock::now();
    if (i > 0)
    {
        const double seconds = std::chrono::duration<double>(now - g_perf.mark).count();
        g_perf.section[i - 1] += seconds;
        g_frameEvents.section[i - 1] += seconds * 1000.0;
    }
    g_perf.mark = now;
}

// REGPU_TRACE_UNSTABLE=1: draws whose inputs are the same as in the frame before but for which the
// renderer decided differently (where the vertices came from, the index list, the textures bound,
// the pipeline, the scissor, a merged tile). In a paused game every frame is the same, so anything
// logged is a decision that flips by itself: the flickering geometry hunt (a tower or a wall of vents
// in PGR4's sky for a frame). Also logs a change in the number of draws.
namespace Unstable
{
enum Part : uint32_t { kVertices, kIndices, kTextures, kPipeline, kScissor, kTile, kVertexData, kIndexData, kParts };
const char* const kPartNames[kParts] = { "vertices", "indices", "textures", "pipeline", "scissor", "tile merge",
    "vertex data (the game rewrote it)", "index data (the game rewrote it)" };
struct Draw
{
    uint64_t input = 0;
    uint64_t part[kParts]{};
    uint64_t vsHash = 0, psHash = 0;
    uint32_t initiator = 0, firstVertexFetch = 0, dmaBase = 0, dmaSize = 0;
};
// The two frames before (titles alternate work between frames: reflection faces, PGR4's paused
// race has 4390 and 4358 draws in turn), so a frame is compared with the one two back.
std::vector<Draw> g_older, g_previous, g_current;
uint64_t g_frame = ~0ull;
uint32_t g_logged = 0;

bool On()
{
    static LiveSwitch on("REGPU_TRACE_UNSTABLE");
    // A fresh log budget each time it is switched on.
    static bool was = false;
    if (on && !was)
    {
        g_logged = 0;
        g_older.clear();
        g_previous.clear();
        g_current.clear();
        g_frame = ~0ull;
    }
    was = on;
    return on;
}

uint64_t Mix(uint64_t h, uint64_t v) { return (h ^ v) * 0x100000001B3ull; }

// What the draw is given: its packet and every register a draw reads (shaders, fetch and ALU
// constants, render state).
uint64_t InputKey(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize, uint64_t vsHash, uint64_t psHash)
{
    uint64_t h = Mix(Mix(Mix(Mix(Mix(0xCBF29CE484222325ull, initiator), dmaBase), dmaSize), vsHash), psHash);
    for (uint32_t r = 0x2000; r < 0x2400; r++) // render state
        if (r != 0x2080) // (PA_SC_WINDOW_OFFSET: a merged tile's differs by design)
            h = Mix(h, gpureg::Get(r));
    for (uint32_t r = 0x4000; r < 0x4980; r++) // ALU and fetch constants
        h = Mix(h, gpureg::Get(r));
    return h;
}

// At each draw: a new frame compares the last one with the one before.
void Begin()
{
    if (g_frame == g_r.frame)
        return;
    if (g_frame != ~0ull)
    {
        const std::vector<Draw>& base = g_older; // two frames back
        if (!base.empty() && base.size() != g_current.size() && g_logged < 400)
        {
            g_logged++;
            Log("unstable: frame %llu has %zu draws, two frames before %zu", (unsigned long long)g_frame, g_current.size(), base.size());
        }
        else if (!base.empty() && base.size() == g_current.size())
        {
            uint32_t inFrame = 0;
            for (size_t i = 0; i < g_current.size() && g_logged < 400; i++)
            {
                const Draw &a = base[i], &b = g_current[i];
                if (a.input != b.input)
                    continue; // given something else: not ours
                for (uint32_t k = 0; k < kParts; k++)
                    if (a.part[k] != b.part[k] && inFrame++ < 12)
                    {
                        g_logged++;
                        Log("unstable: frame %llu draw %zu (vs %016llx ps %016llx, initiator %08X, vertex fetch %08X %08X, indices %08X %08X): same input, %s differ (%016llx -> %016llx)",
                            (unsigned long long)g_frame, i, (unsigned long long)b.vsHash, (unsigned long long)b.psHash, b.initiator, b.firstVertexFetch,
                            gpureg::Get(gpureg::FetchConstants + 1), b.dmaBase, b.dmaSize, kPartNames[k], (unsigned long long)a.part[k], (unsigned long long)b.part[k]);
                    }
            }
        }
    }
    std::swap(g_older, g_previous);
    std::swap(g_previous, g_current);
    g_current.clear();
    g_frame = g_r.frame;
}
}

// REGPU_PERF: the constants section split further (Sub(k) ends part k), logged with the rest.
double g_sub[8] = {};
std::chrono::steady_clock::time_point g_subMark;
void Sub(int k)
{
    if (!g_perfTiming)
        return;
    const auto now = std::chrono::steady_clock::now();
    if (k >= 0)
        g_sub[k] += std::chrono::duration<double>(now - g_subMark).count();
    g_subMark = now;
}

// Debug (finding which draw paints something): out/draw_skip.txt "from to [info]" skips this frame's
// draws with those numbers (counted from 0 each frame, re-read every 30 frames); "info N" logs
// draw N's packet and registers once. With REGPU_DEBUG only.
bool DebugSkipDraw(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    static const bool enabled = getenv("REGPU_DEBUG") != nullptr;
    if (!enabled)
        return false;
    static uint32_t from = 0, to = 0, info = ~0u;
    static uint64_t infoVs = 0; // "infovs HASH": the first draw of that vertex shader
    static uint64_t readFrame = 0, infoFrame = 0;
    if (g_r.frame >= readFrame + 30)
    {
        readFrame = g_r.frame;
        from = to = 0;
        info = ~0u;
        if (FILE* f = fopen("out/draw_skip.txt", "r"))
        {
            char word[16] = {};
            if (fscanf(f, "%u %u", &from, &to) != 2)
                from = to = 0;
            char hex[32] = {};
            if (fscanf(f, "%15s %31s", word, hex) != 2)
                info = ~0u;
            else if (!strcmp(word, "info"))
                info = uint32_t(atoi(hex));
            else if (!strcmp(word, "infovs"))
                info = 0xFFFFFFFEu, infoVs = strtoull(hex, nullptr, 16);
            else
                info = ~0u;
            fclose(f);
        }
    }
    const uint32_t n = g_r.drawsThisFrame.load();
    if ((n == info || (info == 0xFFFFFFFEu && C().vsHash == infoVs)) && infoFrame + 60 < g_r.frame)
    {
        infoFrame = g_r.frame;
        Log("draw info %u: initiator %08X index base %08X size %08X vs %016llx ps %016llx", n, initiator, dmaBase, dmaSize, (unsigned long long)C().vsHash,
            (unsigned long long)C().psHash);
        for (uint32_t k = 0; k < 8; k++)
            Log("  vertex fetch %u: %08X %08X", k, gpureg::Get(gpureg::FetchConstants + k * 2), gpureg::Get(gpureg::FetchConstants + k * 2 + 1));
        for (uint32_t slot = 0; slot < 32; slot++)
        {
            const TextureFetch f = ReadTextureFetch(slot);
            if (f.Type() == 2)
                Log("  texture fetch %u: %08X %08X %08X %08X %08X %08X (dimension %u, %ux%u, format %u, filter mag %u min %u, address %08X)", slot, f.dwords[0], f.dwords[1],
                    f.dwords[2], f.dwords[3], f.dwords[4], f.dwords[5], f.Dimension(), f.Width(), f.Height(), f.Format(), (f.dwords[3] >> 19) & 3,
                    (f.dwords[3] >> 21) & 3, f.Address());
        }
        Log("  surface %08X colour %08X depth %08X mode %08X window offset %08X scissor %08X %08X", gpureg::Get(RB_SURFACE_INFO), gpureg::Get(RB_COLOR_INFO),
            gpureg::Get(RB_DEPTH_INFO), gpureg::Get(RB_MODECONTROL), gpureg::Get(PA_SC_WINDOW_OFFSET), gpureg::Get(PA_SC_WINDOW_SCISSOR_TL),
            gpureg::Get(PA_SC_WINDOW_SCISSOR_BR));
    }
    // (Never a resolve: skipped, the scene copies stop updating and the screen keeps the last picture.)
    if (n >= from && n < to && (gpureg::Get(RB_MODECONTROL) & 7) != 6)
    {
        g_r.drawsThisFrame++; // (keeps the numbering of the draws after it)
        return true;
    }
    return false;
}

void DrawImpl(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    Section(0);
    if (DebugSkipDraw(initiator, dmaBase, dmaSize))
        return;
    g_drawSamplesResolve = false;
    LiveSwitch &noRender = g_sw_REGPU_NO_RENDER;
    if (noRender)
        return;
    // A draw with PA_SC_VIZ_QUERY.kill_pix_post_hi_z (bit 7) is viz query survey geometry: it only feeds
    // the hierarchical-Z visibility test of its query ID, and the hardware kills its pixels after hi-Z,
    // so it writes no colour, depth or stencil and no ZPD report counts it. Nothing here predicates
    // draws on viz queries, so a survey has nothing to feed and isn't drawn (titles may draw them with
    // debug shaders: solid boxes). From the FM4 port. REGPU_VIZ_QUERY_DRAWS=1 draws them (A/B).
    static LiveSwitch vizQueryDraws("REGPU_VIZ_QUERY_DRAWS");
    if (!vizQueryDraws && (gpureg::Get(0x2293 /* PA_SC_VIZ_QUERY */) >> 7 & 1))
    {
        TraceSkip("viz query survey", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    if (!g_r.ok)
        Init();
    const uint32_t edramMode = gpureg::Get(RB_MODECONTROL) & 7;
    if (edramMode == 6)
    {
        Resolve();
        return;
    }
    // Colour+depth (4) and depth-only (5) passes draw.
    // REGPU_SKIP_SHADERS=<hex hash>[,...]: draws whose vertex or pixel shader is listed are dropped
    // (debug: which draw makes an artefact). Read per draw, so the debug server's "env" sets it live.
    thread_local FrameEnv skipEnv{ "REGPU_SKIP_SHADERS" };
    if (const char* skip = skipEnv.Get())
        for (const char* p = skip; *p;)
        {
            char* end = nullptr;
            const uint64_t h = strtoull(p, &end, 16);
            if (end == p)
                break;
            if (h == C().vsHash || h == C().psHash)
            {
                TraceSkip("REGPU_SKIP_SHADERS", initiator);
                g_r.skippedThisFrame++;
                return;
            }
            p = *end ? end + 1 : end;
        }
    if ((edramMode != 4 && edramMode != 5) || C().vsCode.empty() || C().psCode.empty())
    {
        TraceSkip("edram mode", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    {
        // A draw reached a shader we have not seen: a new level or object has loaded. Before the
        // static shader set existed this scanned guest memory for all of its shader objects
        // right away; now that set covers the game, and the 512 MB scan (~18 ms on this
        // thread) only showed as a frame spike, so once the game runs the low-priority
        // background scan is enough.
        const bool unseen = g_r.shaders.find(C().vsHash) == g_r.shaders.end() || g_r.shaders.find(C().psHash) == g_r.shaders.end();
        static auto lastScan = std::chrono::steady_clock::time_point{};
        LiveSwitch &noScan = g_sw_REGPU_NO_SHADER_SCAN;
        if (unseen && !noScan)
        {
            if (g_scanKnown.count(C().vsHash) || g_scanKnown.count(C().psHash))
                g_scanPrefound++;
            else
                g_scanMissed++;
            if (!g_compiler.park.load() && std::chrono::steady_clock::now() - lastScan > std::chrono::milliseconds(300))
            {
                const auto t0 = std::chrono::steady_clock::now();
                ShaderScanNow(std::max(2u, std::thread::hardware_concurrency() / 2));
                lastScan = std::chrono::steady_clock::now();
                DrainScan(0);
                if (getenv("REGPU_PERF"))
                    Log("perf: shader scan took %.0f ms, %llu shaders found in total", std::chrono::duration<double, std::milli>(lastScan - t0).count(),
                        (unsigned long long)ShaderScanTotal());
            }
        }
    }
    // A live edit of this pixel shader (PsEditAlias): this draw uses the alias, the context keeps the real hash.
    struct PsAlias
    {
        uint64_t real, realVs;
        PsAlias() : real(C().psHash), realVs(C().vsHash) { C().psHash = PsEditAlias(real); C().vsHash = PsEditAlias(realVs); }
        ~PsAlias() { C().psHash = real; C().vsHash = realVs; }
    } psAlias;
    const CompiledShader& vs = GetShader(C().vsHash, C().vsCode, false);
    if (g_drawStats.load(std::memory_order_relaxed) == 2) // the debug server's drawstats: every draw of a frame
    {
        std::lock_guard lock(g_drawStatsMutex);
        g_drawStatsCounts[{ psAlias.realVs, psAlias.real }]++;
    }
    const CompiledShader& ps = GetShader(C().psHash, C().psCode, true);
    Section(1);
    LiveSwitch &asyncShaders = g_sw_REGPU_ASYNC_SHADERS;
    const bool shadersPending = vs.state.load(std::memory_order_acquire) == CompiledShader::Pending || ps.state.load(std::memory_order_acquire) == CompiledShader::Pending;
    if (shadersPending && SkippableDraw(edramMode == 5, gpureg::Get(RB_BLENDCONTROL0)))
    {
        g_skippedPipeline++;
        return; // compiling in the background
    }
    g_waitWhat = "shader";
    g_waitBlend = gpureg::Get(RB_BLENDCONTROL0);
    if (!asyncShaders)
        WaitHoldingImpl([&] {
            return vs.state.load(std::memory_order_acquire) != CompiledShader::Pending && ps.state.load(std::memory_order_acquire) != CompiledShader::Pending;
        });
    if (!vs.IsReady() || !ps.IsReady())
    {
        TraceSkip(vs.state == CompiledShader::Failed || ps.state == CompiledShader::Failed ? "shader" : "compiling", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    // A fetch from vertex fetch constant 96 or above (index field past the 96 slots) has no
    // vertex data. With the layout baked in, such shaders failed to compile and their draws
    // were skipped; read from vfl they compile and draw garbage (black shapes on Mugello).
    for (uint32_t k : vs.info.vertexFetchConstants)
        if (k >= 96)
        {
            TraceSkip("invalid vertex fetch constant", initiator);
            g_r.skippedThisFrame++;
            return;
        }
    // Triangle lists and restart-free strips read their indices on the GPU;
    // everything else is expanded here.
    const uint32_t prim = initiator & 0x3F, indexSource = (initiator >> 6) & 3, indexCount = initiator >> 16;
    const bool restartOn = (gpureg::Get(PA_SU_SC_MODE_CNTL) >> 21) & 1;
    LiveSwitch &noDirectIndex = g_sw_REGPU_NO_DIRECT_INDEX;
    // A draw asking for more indices than its index buffer holds reads zeros past its end (as the
    // Xenos does): the CPU path bounds the reads (BuildIndexList), the GPU's direct reads would not.
    const bool indexOverrun = indexSource == 0 && indexCount > (dmaSize & 0xFFFFFF);
    // With early fences, indices read in place must not be ones the title rewrites (the copied
    // index paths are safe: they read guest memory at record time).
    // (Rect lists only: triangle lists and strips read their indices through the index arena, which
    // copies them; refusing those sent every draw of a rewritten index buffer to the CPU's index
    // expansion, 7 ms a frame in FH1 TU4.)
    // Small index buffers are always copied, whatever the primitive: direct indices are read by the
    // vertex shader when the host GPU runs, frames after the command processor saw them. FH1 writes the
    // same 36 indices of each shadow cascade box (a rect list) every frame, clearing and refilling the
    // memory in place; a content check sees nothing change (and called it rewritten on some frames, not
    // others), and whenever the GPU ran a few frames behind (2560x1440 and up, another game on the GPU) it
    // read the cleared indices: every other frame the cascade drew degenerate triangles and the sun
    // shadows flickered (RenderDoc, two frames: same vertices, indices all zero). Copying a few hundred
    // bytes costs nothing; large ones (static meshes) stay in place unless seen changing.
    // REGPU_DIRECT_DYNAMIC_INDICES=1: as before, in place unless an early-fence rect list changed (A/B).
    static LiveSwitch directDynamicIndices("REGPU_DIRECT_DYNAMIC_INDICES");
    const uint32_t indexBytes = (dmaSize & 0xFFFFFF) * (((initiator >> 11) & 1) ? 4u : 2u);
    constexpr uint32_t kCopiedIndexBytes = 16u << 10;
    const bool indexRewritten = indexSource == 0 &&
        (directDynamicIndices ? prim == 13 && RendererEarlyFences() && DynamicBuffer(dmaBase & 0x1FFFFFFF, indexBytes)
                              : (prim == 4 || prim == 6 || prim == 13) &&
                                    (indexBytes < kCopiedIndexBytes || ((prim != 13 || RendererEarlyFences()) && DynamicBuffer(dmaBase & 0x1FFFFFFF, indexBytes))));
    // A strip from a 16-bit index buffer restarting at 0xFFFF is drawn indexed, the GPU restarting it
    // (the pipeline's strip cut value), as Xenia does. Expanded on the CPU, these were copied into the
    // ring every frame: 7 MB a frame in PGR4's races (4% of the command processor on an FX-8350).
    // REGPU_NO_STRIP_CUT=1: expanded as before (A/B).
    const bool stripCut = prim == 6 && restartOn && indexSource == 0 && !((initiator >> 11) & 1) &&
        (gpureg::Get(VGT_MULTI_PRIM_IB_RESET_INDX) & 0xFFFF) == 0xFFFF && !g_sw_REGPU_NO_STRIP_CUT && !g_sw_REGPU_NO_INDEXED;
    const bool directIndex = g_r.physical && !noDirectIndex && !indexOverrun && !indexRewritten && (indexSource == 0 || indexSource == 2) &&
        (prim == 4 || prim == 13 || (prim == 6 && (!restartOn || stripCut)));
    const uint32_t directVertices = prim == 4 ? indexCount / 3 * 3 : prim == 13 ? indexCount / 4 * 6 : (indexCount >= 3 ? (indexCount - 2) * 3 : 0);
    IndexListEntry* indexEntry = directIndex ? nullptr : CachedIndexList(initiator, dmaBase, dmaSize);
    static const std::vector<uint32_t> noList;
    const std::vector<uint32_t>& list = indexEntry ? indexEntry->list : noList;
    if (directIndex ? directVertices == 0 : (!indexEntry || list.empty()))
    {
        TraceSkip("primitive", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    Section(2);
    {
        // Debug bisection: REGPU_SKIP_DRAWS=a-b drops draws a..b-1 of the REGPU_DUMP_FRAME frame.
        static uint32_t skipFrom = 0, skipTo = 0;
        static const bool parsed = [] { if (const char* s = getenv("REGPU_SKIP_DRAWS")) sscanf(s, "%u-%u", &skipFrom, &skipTo); return true; }();
        static const uint64_t envDumpFrame = getenv("REGPU_DUMP_FRAME") ? strtoull(getenv("REGPU_DUMP_FRAME"), nullptr, 10) : 0;
        const uint64_t dumpFrame = g_hotkeyFrame.load(std::memory_order_relaxed) ? g_hotkeyFrame.load(std::memory_order_relaxed) : envDumpFrame; // (F12)
        (void)parsed;
        // REGPU_SKIP_VS=h1,h2,...: drop those vertex shaders in the frame after the dump
        // (written to frame2.png), for an A/B pair under the same camera.
        static const std::vector<uint64_t> skipVs = [] {
            std::vector<uint64_t> v;
            for (const char* s = getenv("REGPU_SKIP_VS"); s && *s;)
            {
                char* end;
                v.push_back(strtoull(s, &end, 16));
                s = *end == ',' ? end + 1 : end;
                if (end == s && *end != ',') break;
            }
            return v;
        }();
        const bool skipThis = g_r.frame == dumpFrame && std::find(skipVs.begin(), skipVs.end(), C().vsHash) != skipVs.end();
        // REGPU_SWEEP="start,step,count" (with REGPU_DUMP_FRAME or F12 as the first frame): frame
        // dumpFrame+i drops draws [start+i*step, start+(i+1)*step) and is written to
        // out/sweep/<i>.png, so one run compares many skips on near-identical frames.
        static uint32_t sweepStart = 0, sweepStep = 0, sweepCount = 0;
        static const bool sweepParsed = [] { if (const char* e = getenv("REGPU_SWEEP")) sscanf(e, "%u,%u,%u", &sweepStart, &sweepStep, &sweepCount); return true; }();
        (void)sweepParsed;
        bool sweepSkip = false;
        if (sweepCount && dumpFrame && g_r.frame + 1 >= dumpFrame && g_r.frame + 1 < dumpFrame + sweepCount)
        {
            const uint32_t i = uint32_t(g_r.frame + 1 - dumpFrame);
            sweepSkip = g_r.drawsThisFrame >= sweepStart + i * sweepStep && g_r.drawsThisFrame < sweepStart + (i + 1) * sweepStep;
            // REGPU_SWEEP_ONLY=1: the opposite: drop every draw in the whole swept range except this frame's chunk.
            if (getenv("REGPU_SWEEP_ONLY"))
                sweepSkip = g_r.drawsThisFrame >= sweepStart && g_r.drawsThisFrame < sweepStart + sweepCount * sweepStep && !sweepSkip;
        }
        if (skipThis || sweepSkip || (skipTo && g_r.frame + 1 == dumpFrame && g_r.drawsThisFrame >= skipFrom && g_r.drawsThisFrame < skipTo))
        {
            g_r.drawsThisFrame++;
            return;
        }
    }
    BeginFrameIfNeeded();
    SelectSurfaces(false);
    // Temporal upscaling: the main view's draws in the scene also write motion vectors (their
    // shaders' motion variants, once compiled; until then the draw has no motion).
    const bool cullable = (initiator & 0x3F) != 8 && (initiator & 0x3F) != 1;
    const bool wantMotion = g_temporal && g_scenePhase && edramMode == 4 && WidePass();
    g_plainRectDraw = (initiator & 0x3F) == 8 && !ps.info.usedTextureSlots && !vs.info.usedTextureSlots;
    const CompiledShader* mvs = wantMotion ? MotionShader(vs) : nullptr;
    const CompiledShader* mps = wantMotion ? MotionShader(ps) : nullptr;
    bool motion = mvs && mps && mvs->IsReady() && mps->IsReady();
    D3D12_GPU_VIRTUAL_ADDRESS motionPrevious = 0; // last frame's vertex constants of this draw (b2)
    ID3D12PipelineState* pso = motion ? GetPipeline(*mvs, *mps, false, cullable, true, stripCut && directIndex) : nullptr;
    if (!pso)
    {
        motion = false;
        pso = GetPipeline(vs, ps, edramMode == 5, cullable, false, stripCut && directIndex);
    }
    if (!pso)
    {
        // A shader that failed to translate or compile: the draw is lost (logged once per pair).
        if (vs.state.load() == CompiledShader::Failed || ps.state.load() == CompiledShader::Failed)
        {
            static std::mutex failedMutex;
            static std::set<std::pair<uint64_t, uint64_t>> failedSeen;
            std::lock_guard failedLock(failedMutex);
            if (failedSeen.insert({ vs.hash, ps.hash }).second)
                Log("renderer: draw dropped, vs %016llx%s ps %016llx%s", (unsigned long long)vs.hash, vs.state.load() == CompiledShader::Failed ? " (failed)" : "",
                    (unsigned long long)ps.hash, ps.state.load() == CompiledShader::Failed ? " (failed)" : "");
        }
        TraceSkip("pipeline", initiator);
        g_skippedPipeline++;
        return;
    }
    FrameResources& f = Frame();

    Section(3);
    Sub(-1);
    // Index list.
    UINT64 listOffset = 0;
    if (!directIndex)
    {
        if (indexEntry->frame != g_r.frame)
        {
            indexEntry->offset = RingAlloc(list.size() * 4, 16);
            if (indexEntry->offset != ~0ull)
            {
                memcpy(f.ringCpu + indexEntry->offset, list.data(), list.size() * 4);
                indexEntry->frame = g_r.frame;
            }
        }
        listOffset = indexEntry->offset;
    }
    if (listOffset == ~0ull)
    {
        TraceSkip("ring full", initiator);
        g_r.skippedThisFrame++;
        return;
    }

    // Vertex constants: c[256], bools, fetch table, info.
    struct VSConstants
    {
        float c[256][4];
        uint32_t b[8];
        uint32_t l[32]; // loop constants
        uint32_t vf[96][4];
        uint32_t info[4];
        float point[4];
        float vscale[4], voffset[4];
        uint32_t vte[4];
        uint32_t vfl[32][4]; // vertex fetch layout of the bound shader
        float vuser[4][4];   // the game's per-draw constants (Host::onDraw; hlsl.cpp vuser), zero by default
        float vdepth[4];     // x: the polygon offset's constant part (hlsl.cpp vdepth)
        float vwide[4];      // x: Hor+ factor (see hlsl.cpp)
        float vjitter[4];     // motion-vector variants: this frame's jitter (NDC)
    };
    struct PSConstants
    {
        float c[256][4];
        uint32_t b[8];
        uint32_t l[32]; // loop constants
        uint32_t info[4];
        float info2[4];
        uint32_t tinfo[32][4];
        float tbias[32][4];
    };
    const UINT64 vsOffset = RingAlloc(sizeof(VSConstants)), psOffset = RingAlloc(sizeof(PSConstants));
    if (vsOffset == ~0ull || psOffset == ~0ull)
    {
        TraceSkip("ring full", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    // The float constants go straight to the ring (only those the shaders read); the rest of each
    // block is built in a cached copy and written in one go before the draw is bound: the ring is
    // write-combined upload memory, where scattered small writes (vc and pc in turn) cost ~5 ms a
    // frame and reading back is slower still.
    auto* vcRing = reinterpret_cast<VSConstants*>(f.ringCpu + vsOffset);
    auto* pcRing = reinterpret_cast<PSConstants*>(f.ringCpu + psOffset);
    thread_local VSConstants vcStage;
    thread_local PSConstants pcStage;
    // Not in low performance mode (Config::lowPerformance): on an old CPU (FM2 on an FX-8350) the
    // block copies cost more than the scattered writes they replace. REGPU_CONSTANTS_IN_RING=1 too.
    LiveSwitch &inRingSwitch = g_sw_REGPU_CONSTANTS_IN_RING;
    const bool inRing = inRingSwitch || regpu::GetConfig().lowPerformance;
    auto* vc = inRing ? vcRing : &vcStage;
    auto* pc = inRing ? pcRing : &pcStage;
    const bool hooked = regpu::GetHost().onDraw && (vs.tag || ps.tag); // onDraw reads and writes vc->c
    {
        // Byte-swapped block copy of both constant banks (1024 dwords each).
        const __m128i swap = _mm_setr_epi8(3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12);
        // Only the constants each shader reads (the rest of the block is never read).
        const auto* src = reinterpret_cast<const __m128i*>(gpureg::File() + gpureg::AluConstants * 4);
        auto* dv = reinterpret_cast<__m128i*>(hooked && !inRing ? vc->c : vcRing->c);
        auto* dp = reinterpret_cast<__m128i*>(pcRing->c);
        LiveSwitch &allConstants = g_sw_REGPU_ALL_CONSTANTS; // A/B
        const uint32_t nv = allConstants ? 256 : std::min(vs.info.constantsUsed, 256u), np = allConstants ? 256 : std::min(ps.info.constantsUsed, 256u);
        if (LiveSwitch &traceUsed = g_sw_REGPU_TRACE_CONSTANTS_USED; traceUsed)
        {
            static uint64_t sv = 0, sp = 0, n = 0, lastFrame = 0;
            sv += nv, sp += np, n++;
            if (g_r.frame >= lastFrame + 120)
            {
                lastFrame = g_r.frame;
                Log("constants used: vs %.1f ps %.1f per draw (%llu draws)", double(sv) / n, double(sp) / n, (unsigned long long)n);
                sv = sp = n = 0;
            }
        }
        // Only the constants each shader reads (by its mask; the game's onDraw hook may read any).
        // REGPU_CONSTANTS_BY_COUNT copies all up to the highest one again (A/B).
        LiveSwitch &byCount = g_sw_REGPU_CONSTANTS_BY_COUNT;
        auto copy = [&](__m128i* dst, const __m128i* from, const std::array<uint64_t, 4>& mask, uint32_t n, bool all) {
            if (all || byCount)
            {
                for (uint32_t i = 0; i < n; i++)
                    _mm_storeu_si128(dst + i, _mm_shuffle_epi8(_mm_loadu_si128(from + i), swap));
                return;
            }
            for (uint32_t w = 0; w < 4; w++)
                for (uint64_t bits = mask[w]; bits; bits &= bits - 1)
                {
                    const uint32_t i = w * 64 + uint32_t(_tzcnt_u64(bits));
                    _mm_storeu_si128(dst + i, _mm_shuffle_epi8(_mm_loadu_si128(from + i), swap));
                }
        };
        copy(dv, src, vs.info.constantMask, nv, allConstants || hooked);
        copy(dp, src + 256, ps.info.constantMask, np, allConstants || hooked);
        if (motion)
        {
            // This draw's constants for the next frame, and last frame's for its motion. The
            // draw is known by its shaders, vertex and index data and how often it came up.
            uint64_t key = C().vsHash * 0x9E3779B97F4A7C15ull ^ C().psHash ^ (uint64_t(dmaBase) << 20) ^ initiator;
            for (uint32_t k : vs.info.vertexFetchConstants)
            {
                key = (key ^ gpureg::Get(gpureg::FetchConstants + k * 2)) * 0xFF51AFD7ED558CCDull;
                break; // the first vertex buffer is enough to tell draws apart
            }
            key ^= uint64_t(g_upscale.occurrences[key]++) * 0xC2B2AE3D27D4EB4Full;
            // Last frame's constants are still in last frame's ring (it is reused only after
            // kFrames): the draw reads them there (b2).
            auto& entry = g_upscale.constants[key];
            const uint32_t parity = g_upscale.constantsFrame & 1;
            const D3D12_GPU_VIRTUAL_ADDRESS now = f.ring->GetGPUVirtualAddress() + vsOffset;
            const bool known = entry.frame + 1 == g_upscale.constantsFrame && !g_upscale.reset;
            motionPrevious = known ? entry.address[parity ^ 1] : now;
            entry.address[parity] = now;
            entry.frame = g_upscale.constantsFrame;
            vc->vjitter[0] = g_upscale.jitterNdc[0];
            vc->vjitter[1] = g_upscale.jitterNdc[1];
            vc->vjitter[2] = vc->vjitter[3] = 0;
        }
    }
    Sub(0); // index list, ring allocations, float constants
    for (uint32_t i = 0; i < 8; i++)
        vc->b[i] = pc->b[i] = gpureg::Get(gpureg::BoolConstants + i);
    for (uint32_t i = 0; i < 32; i++)
        vc->l[i] = pc->l[i] = gpureg::Get(gpureg::LoopConstants + i);
    // Debug: REGPU_FORCE_BOOL=<index>:<0|1> forces one boolean constant for every draw.
    if (static const char* force = getenv("REGPU_FORCE_BOOL"); force)
    {
        static uint32_t index = 0, value = 0;
        static const bool parsed = sscanf(force, "%u:%u", &index, &value) == 2 && index < 256;
        if (parsed)
        {
            const uint32_t bit = 1u << (index & 31);
            vc->b[index >> 5] = pc->b[index >> 5] = value ? (pc->b[index >> 5] | bit) : (pc->b[index >> 5] & ~bit);
        }
    }
    Sub(1); // bools, loops
    // (Only the entries the shader fetches are set, each in full below: zeroing all 96 was 1.5 KB a draw,
    // ~6 MB a frame of stores in FH1.)
    // vf[k].w: the buffer's size, past which a fetch reads 0 (as the Xenos does). REGPU_NO_FETCH_BOUNDS=1
    // reads past it (A/B).
    LiveSwitch &noBounds = g_sw_REGPU_NO_FETCH_BOUNDS;
    for (uint32_t k : vs.info.vertexFetchConstants)
    {
        const uint32_t d0 = gpureg::Get(gpureg::FetchConstants + k * 2), d1 = gpureg::Get(gpureg::FetchConstants + k * 2 + 1);
        const uint32_t address = d0 & 0xFFFFFFFC, bytes = ((d1 >> 2) & 0xFFFFFF) * 4;
        vc->vf[k][0] = vc->vf[k][1] = vc->vf[k][2] = vc->vf[k][3] = 0;
        // Large buffers (static geometry) are read in place; small ones may be
        // rewritten before the GPU runs this frame, so they are copied.
        LiveSwitch &noDirect = g_sw_REGPU_NO_DIRECT_FETCH;
        static const uint32_t directBytes = getenv("REGPU_DIRECT_FETCH_KB") ? uint32_t(atoi(getenv("REGPU_DIRECT_FETCH_KB"))) * 1024u
                                          : RendererEarlyFences() ? kEarlyFenceDirectFetchBytes : kDirectFetchBytes;
        if (g_r.physical && !noDirect && bytes >= directBytes && !DynamicBuffer(address, bytes))
        {
            vc->vf[k][0] = address & 0x1FFFFFFF;
            vc->vf[k][1] = d1 & 3;
            vc->vf[k][2] = 1;
            vc->vf[k][3] = noBounds ? ~0u : bytes / 4;
            continue;
        }
        // REGPU_TRACE_MIDSIZE=1: log copied buffers of 16-64 KB that change within a frame (they must stay copied)
        LiveSwitch &traceMid = g_sw_REGPU_TRACE_MIDSIZE;
        static const uint32_t traceMinBytes = traceMid ? uint32_t(atoi(getenv("REGPU_TRACE_MIDSIZE"))) * 1024u : 0;
        if (traceMid && bytes >= traceMinBytes)
        {
            static std::unordered_map<uint32_t, std::pair<uint64_t, uint64_t>> seen; // address -> frame, hash
            static uint32_t changed = 0, total = 0;
            const uint64_t h = ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(address)), bytes);
            auto& e = seen[address];
            total++;
            if (e.first == g_r.frame && e.second != h && changed++ < 40)
                Log("midsize: %08X %u bytes changed within frame %llu (vs %016llx)", address, bytes, (unsigned long long)g_r.frame, (unsigned long long)C().vsHash);
            static uint32_t across = 0;
            if (e.first != g_r.frame && e.first + 4 > g_r.frame && e.second != h && across++ < 60)
                Log("midsize: %08X %u bytes changed since frame %llu (vs %016llx ps %016llx)", address, bytes, (unsigned long long)e.first, (unsigned long long)C().vsHash, (unsigned long long)C().psHash);
            e = { g_r.frame, h };
        }
        const UINT64 dataOffset = UploadGuest(GpuPhysToGuest(address), bytes);
        if (dataOffset == ~0ull)
        {
            TraceSkip("ring full", initiator);
            g_r.skippedThisFrame++;
            return;
        }
        vc->vf[k][0] = uint32_t(dataOffset);
        vc->vf[k][1] = d1 & 3;
        vc->vf[k][3] = noBounds ? ~0u : bytes / 4;
    }
    Sub(2); // vertex fetches (uploads)
    // Perspective geometry in a wide pass (see ViewExtent): the game's camera already projects
    // at the wider aspect once hooks/camera_aspect.cpp widened it, so only the vertical zoom
    // for REGPU_WIDE_FOV < 100 remains; otherwise the view is narrowed here (Hor+ without the
    // game's culling knowing).
    {
        const float wide = WidePass() ? OutputWide() : 1.0f;
        const float extent = WidePass() ? ViewExtent() : 1.0f;
        const bool gameWidened = g_gameWidescreen.load(std::memory_order_relaxed);
        vc->vwide[0] = gameWidened ? 1.0f : 1.0f / extent;
        vc->vwide[1] = wide / extent;
        LiveSwitch &noSqueeze = g_sw_REGPU_NO_HUD_SQUEEZE; // A/B: 2D stretched with the view
        vc->vwide[2] = noSqueeze ? 1.0f : 1.0f / wide;
        vc->vwide[3] = 1; // set after the textures are bound (below)
    }
    if (!vs.info.vertexFetchLayout.empty())
        memcpy(vc->vfl, vs.info.vertexFetchLayout.data(), vs.info.vertexFetchLayout.size() * sizeof(vc->vfl[0]));
    vc->info[0] = uint32_t(listOffset);
    vc->info[1] = (initiator & 0x3F) == 1 ? 2 : 0;
    vc->vdepth[0] = PolygonOffsetDepth();
    vc->vdepth[1] = vc->vdepth[2] = vc->vdepth[3] = 0;
    // Viewport transform in the vertex shader (see hlsl.cpp).
    vc->vscale[0] = RegFloat(PA_CL_VPORT_XSCALE);
    vc->vscale[1] = RegFloat(PA_CL_VPORT_YSCALE);
    vc->vscale[2] = RegFloat(PA_CL_VPORT_ZSCALE);
    vc->vscale[3] = 0;
    vc->voffset[3] = 0;
    // The title clears its (non-MSAA) shadow depth maps through views of the same EDRAM
    // with MSAA on: a pixel of a 4x surface covers 2x2 samples of the linear layout we
    // render in, a pixel of a 2x surface 1x2. Scale those clear rectangles to match.
    LiveSwitch &noMsaaScale = g_sw_REGPU_NO_MSAA_SCALE;
    if (!noMsaaScale && (initiator & 0x3F) == 8 && (gpureg::Get(RB_COLOR_MASK) & 0xF) == 0 && (gpureg::Get(RB_SURFACE_INFO) & 0x3FFF) < 1000)
    {
        const uint32_t msaa = (gpureg::Get(RB_SURFACE_INFO) >> 16) & 3;
        if (msaa == 2)
            vc->vscale[3] = vc->voffset[3] = 2.0f;
        else if (msaa == 1)
        {
            vc->vscale[3] = 1.0f;
            vc->voffset[3] = 2.0f;
        }
    }
    // The same trick between two MSAA views (a title can clear a 2x composite
    // through a 4x view, 1000 pixels of it plus a 48-pixel 2x strip): texture-less rectangles
    // are scaled from their view's layout to the surface's (see g_nativeMsaa).
    else if (!noMsaaScale)
    {
        const uint32_t surface = gpureg::Get(RB_SURFACE_INFO), msaa = (surface >> 16) & 3;
        static LiveSwitch noDepthViewScale("REGPU_NO_DEPTH_VIEW_SCALE"); // A/B for the depth-layout case below
        if ((initiator & 0x3F) != 8 || ps.info.usedTextureSlots || vs.info.usedTextureSlots)
        {
            SetNativeMsaa(LayoutKey(surface), msaa);
            if (gpureg::Get(0x2200 /* RB_DEPTHCONTROL */) & 7) // the draw uses the depth surface
            {
                const uint32_t key = DepthLayoutKey(surface);
                NativeDepthSlot(key).store(key << 2 | msaa, std::memory_order_relaxed);
            }
        }
        else if (TallView())
        {
            const uint32_t native = (g_tallInfo >> 16) & 3;
            vc->vscale[3] = float(MsaaAcross(msaa)) / float(MsaaAcross(native));
            vc->voffset[3] = float(MsaaDown(msaa)) / float(MsaaDown(native));
        }
        else if (const int native = ForeignNativeMsaa(); native >= 0)
        {
            vc->vscale[3] = float(MsaaAcross(msaa)) / float(MsaaAcross(uint32_t(native)));
            vc->voffset[3] = float(MsaaDown(msaa)) / float(MsaaDown(uint32_t(native)));
        }
        // A rectangle through another MSAA view of a depth surface (stencil resets): scaled from the
        // view's layout to the depth surface's. Without it FH1's 4x stencil reset covered a quarter
        // of the sun-shadow mask, and the far cascades never reached the rest: weak sun shadows.
        // REGPU_NO_DEPTH_VIEW_SCALE=1: not scaled, as before.
        else if (const int nativeDepth = noDepthViewScale ? -1 : ForeignNativeDepthMsaa();
                 nativeDepth >= 0 && (gpureg::Get(0x2200 /* RB_DEPTHCONTROL */) & 7))
        {
            vc->vscale[3] = float(MsaaAcross(msaa)) / float(MsaaAcross(uint32_t(nativeDepth)));
            vc->voffset[3] = float(MsaaDown(msaa)) / float(MsaaDown(uint32_t(nativeDepth)));
        }
    }
    vc->voffset[0] = RegFloat(PA_CL_VPORT_XOFFSET);
    vc->voffset[1] = RegFloat(PA_CL_VPORT_YOFFSET);
    vc->voffset[2] = RegFloat(PA_CL_VPORT_ZOFFSET);
    vc->vte[0] = gpureg::Get(PA_CL_VTE_CNTL);
    vc->vte[1] = vc->vte[2] = vc->vte[3] = 0;
    // Flags for the screen-space position code (hlsl.cpp), in bits the register doesn't use:
    // bit 31 REGPU_OLD_CLEAR_SHIFT (A/B: half-pixel shift before the MSAA-view scale); bit 30 snaps a
    // texture-less screen-space draw's edges to whole guest pixels on an upscaled surface. The console
    // covers a guest pixel by its centre; scaled, the half-pixel shift is more than half a host pixel,
    // and a rectangle with integer edges (a shadow map's clear) lost its first row and column.
    // REGPU_NO_RECT_SNAP=1: no snap.
    LiveSwitch &oldClearShift = g_sw_REGPU_OLD_CLEAR_SHIFT, &noRectSnap = g_sw_REGPU_NO_RECT_SNAP;
    if (oldClearShift)
        vc->vte[0] |= 1u << 31;
    // REGPU_OLD_DEPTH_W=1: depth divided by w and multiplied back, as before (hlsl.cpp, bit 29).
    if (static LiveSwitch oldDepthW("REGPU_OLD_DEPTH_W"); oldDepthW)
        vc->vte[0] |= 1u << 29;
    if (!noRectSnap && !ps.info.usedTextureSlots && !vs.info.usedTextureSlots && (ScaleX() != 1.0f || ScaleY() != 1.0f))
        vc->vte[0] |= 1u << 30;
    // The same snap for a texture-less rectangle through another MSAA view (vscale.w, voffset.w > 1):
    // the half-pixel shift added after the view scale put the far edge of FH1's 4x clear of its
    // 1024x1024 shadow maps on the last row's centres, so row 1023 kept 0 (always shadowed) and drew
    // a thin dark line on the ground where the car's shadow box ends. Snapped, the rectangle covers
    // the view pixels whose centres it covers. REGPU_NO_VIEW_RECT_SNAP=1: shifted as before (A/B).
    if (static LiveSwitch noViewRectSnap("REGPU_NO_VIEW_RECT_SNAP"); !noViewRectSnap && !oldClearShift && !ps.info.usedTextureSlots &&
        !vs.info.usedTextureSlots && vc->vscale[3] >= 1.0f && vc->voffset[3] >= 1.0f && (vc->vscale[3] > 1.0f || vc->voffset[3] > 1.0f))
        vc->vte[0] |= 1u << 30;
    // Viewport-transformed draws with D3D9 pixel centres (PA_SU_VTX_CNTL bit 0 clear) are shifted half a
    // pixel, as the screen-space ones are (hlsl.cpp, bit 28). Unshifted, FH1's image sat 0.6-0.7 px off
    // stock's (HUD, banners) and its screen-space shadow lookups landed between mask texels: the shadows
    // shimmered with the idle camera (FX, real-time capture: 22-30 changes a second at the user's
    // example against 12.7 shifted and stock's 16). REGPU_NO_3D_HALF_PIXEL=1: unshifted (A/B).
    if (static LiveSwitch no3dHalfPixel("REGPU_NO_3D_HALF_PIXEL"); !no3dHalfPixel && !(gpureg::Get(0x2302 /* PA_SU_VTX_CNTL */) & 1))
        vc->vte[0] |= 1u << 28;
    Sub(3); // viewport, MSAA scale
    // Triangle lists and restart-free strips from an index buffer: a real indexed draw.
    LiveSwitch &noIndexed = g_sw_REGPU_NO_INDEXED;
    D3D12_INDEX_BUFFER_VIEW indexView{};
    uint32_t indexedCount = 0;
    if (directIndex && !noIndexed && indexSource == 0 && (prim == 4 || prim == 6))
    {
        const bool is32 = (initiator >> 11) & 1;
        const uint32_t available = dmaSize & 0xFFFFFF;
        const uint32_t count = std::min(indexCount, available);
        indexedCount = prim == 4 ? count / 3 * 3 : count;
        if (indexedCount < 3 || !GetIndexBuffer(dmaBase & 0x1FFFFFFF & ~(is32 ? 3u : 1u), count, is32, dmaSize >> 30, indexView))
            indexedCount = 0;
    }
    if (stripCut && directIndex && !indexedCount)
    {
        // (The shader's own index fetch can't restart a strip.)
        TraceSkip("strip cut", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    if (indexedCount)
    {
        vc->vte[1] = 1u | 8u; // the vertex ID is the index
        vc->vte[3] = gpureg::Get(VGT_INDX_OFFSET);
    }
    else if (directIndex)
    {
        const bool is32 = (initiator >> 11) & 1;
        vc->vte[1] = 1u | (prim == 6 ? 2u : 0u) | (prim == 13 ? 64u : 0u) | (is32 ? 4u : 0u) | (indexSource == 2 ? 8u : 0u) | ((dmaSize >> 30) << 4);
        vc->vte[2] = dmaBase & 0x1FFFFFFF & ~(is32 ? 3u : 1u);
        vc->vte[3] = gpureg::Get(VGT_INDX_OFFSET);
    }
    // The window scissor marks a tall pass (PassHeight); this draw may be its first, so check before the
    // target size is taken (else the vertices are placed for 720 rows in a taller viewport: a one-off 1024-row
    // downsample came out stretched 1.42x).
    Sub(4); // index buffer
    (void)WindowScissor();
    vc->info[2] = PassWidth();
    vc->info[3] = PassHeight();
    // Point sizes are 12.4 fixed-point radii.
    const uint32_t pointSize = gpureg::Get(PA_SU_POINT_SIZE), pointMinMax = gpureg::Get(PA_SU_POINT_MINMAX);
    vc->point[0] = float(pointSize >> 16) / 8.0f;
    vc->point[1] = float(pointSize & 0xFFFF) / 8.0f;
    vc->point[2] = float(pointMinMax & 0xFFFF) / 8.0f;
    vc->point[3] = float(pointMinMax >> 16) / 8.0f;
    // The game's per-draw constants for shaders it rewrote (regpu.h Host::onDraw).
    memset(vc->vuser, 0, sizeof(vc->vuser));
    if (const auto onDraw = regpu::GetHost().onDraw; onDraw && (vs.tag || ps.tag))
        onDraw(C().vsHash, C().psHash, vs.tag, ps.tag, vc->c, vc->vuser);
    float clearColour[4] = {};
    const auto clearBeforeDraw = regpu::GetHost().clearBeforeDraw;
    const bool clearBefore = clearBeforeDraw && (vs.tag || ps.tag) && clearBeforeDraw(C().vsHash, C().psHash, vs.tag, ps.tag, clearColour);
    pc->info[1] = (gpureg::Get(SQ_PROGRAM_CNTL) >> 18) & 1 ? (gpureg::Get(SQ_CONTEXT_MISC) >> 8) & 0xFF : 0xFF;

    const uint32_t colorControl = gpureg::Get(RB_COLORCONTROL);
    // Depth-only passes (EDRAM mode 5) keep the alpha test for draws that sample a texture: a
    // shadow map can have cut-out geometry that otherwise lands in it as solid blocks (jagged
    // shadow under the object). A texture-less draw has no cut-out to make: Top Spin 4 clears its
    // shadow map's depth with a quad whose colour, and so alpha, is 0, and the console writes it
    // (alpha-tested, the clear was lost and the stale depth shadowed the map's whole square).
    // Only a texture the pixel shader samples makes a cut-out (FM2: the race's sun shadow map lost
    // its casters and clears to a pass-through alpha of 0 while the vertex shader sampled one).
    // A slot whose fetch constant isn't a texture (type 0: the game unbound it) has no cut-out
    // either: it reads 0, and the test would drop the whole caster (FH1: the car body sampled an
    // unbound slot 0 and was missing from the near sun-shadow cascade, so the car cast no shadow).
    // REGPU_NO_DEPTH_ALPHATEST=1: no alpha test in depth-only passes; REGPU_DEPTH_ALPHATEST_ALL=1:
    // every draw tested (before); REGPU_DEPTH_ALPHATEST_VS=1: a vertex-shader texture counts too;
    // REGPU_DEPTH_ALPHATEST_EMPTY=1: an unbound slot counts too.
    LiveSwitch &noDepthAlphaTest = g_sw_REGPU_NO_DEPTH_ALPHATEST, &testAll = g_sw_REGPU_DEPTH_ALPHATEST_ALL, &testVs = g_sw_REGPU_DEPTH_ALPHATEST_VS;
    const auto boundSlots = [&](uint32_t slots) {
        if (edramMode != 5 || g_sw_REGPU_DEPTH_ALPHATEST_EMPTY)
            return slots;
        uint32_t bound = 0;
        for (uint32_t m = slots; m; m &= m - 1)
            if (ReadTextureFetch(uint32_t(__builtin_ctz(m))).Type() == 2)
                bound |= m & -m;
        return bound;
    };
    const bool depthAlphaTest = !noDepthAlphaTest && (testAll || boundSlots(ps.info.usedTextureSlots) || (testVs && boundSlots(vs.info.usedTextureSlots)));
    pc->info[0] = (colorControl >> 3) & 1 && (edramMode != 5 || depthAlphaTest) ? (colorControl & 7) : 7;
    pc->info2[0] = RegFloat(RB_ALPHA_REF);
    pc->info2[1] = 1.0f / ScaleX();
    pc->info2[2] = 1.0f / ScaleY();
    // Our colour target is half float: a 7e3 surface (formats 3 and 12, the title's HDR scene)
    // saturates at 31.875, so its output is clamped to that, as the hardware does (the bloom
    // reads those values).
    // A fixed-point surface (k_8_8_8_8, its _GAMMA, k_2_10_10_10, 0 1 2 10) takes the output clamped
    // to 0..1 before blending (-1: saturate). Unclamped, a multiply blend brightened where the output
    // was a little over 1 (Top Spin 4's contact-shadow decals: light squares under every foot).
    // REGPU_NO_UNORM_CLAMP=1: not clamped.
    LiveSwitch &noUnormClamp = g_sw_REGPU_NO_UNORM_CLAMP;
    const uint32_t colorFormat = (gpureg::Get(RB_COLOR_INFO) >> 16) & 0xF;
    const bool unorm = colorFormat == 0 || colorFormat == 1 || colorFormat == 2 || colorFormat == 10;
    pc->info2[3] = colorFormat == 3 || colorFormat == 12 ? 31.875f : unorm && !noUnormClamp ? -1.0f : 0.0f;

    Sub(5); // the rest
    Section(4);
    // Textures are bindless: each one has a permanent descriptor in the heap's
    // bindless region, and the draw passes its index in the constants.
    const uint32_t usedSlots = ps.info.usedTextureSlots | vs.info.usedTextureSlots;
    // Only the slots up to the highest used one reach the ring (below), unless whole blocks are copied:
    // clearing all 32 was 1 KB a draw (3.5% of the command processor in PGR4's races on an FX-8350).
    {
        const size_t clear = g_sw_REGPU_FULL_CONSTANT_BLOCKS ? 32 : usedSlots ? 32 - uint32_t(__builtin_clz(usedSlots)) : 0;
        memset(pc->tinfo, 0, clear * sizeof(pc->tinfo[0]));
        memset(pc->tbias, 0, clear * sizeof(pc->tbias[0]));
    }
    if (g_bindingsFrame != g_r.frame || g_bindingsEpoch != g_resolveEpoch)
    {
        if (!g_bindings)
            g_bindings = std::make_unique<SlotBinding[]>(kBindingSlots); // (entries carry their frame and epoch)
        g_bindingsFrame = g_r.frame;
        g_bindingsEpoch = g_resolveEpoch;
    }
    for (uint32_t m = usedSlots; m; m &= m - 1)
    {
        const uint32_t slot = uint32_t(__builtin_ctz(m));
        const TextureFetch fetch = ReadTextureFetch(slot);
        uint64_t bindingKey = 0xCBF29CE484222325ull;
        for (uint32_t d : fetch.dwords)
            bindingKey = (bindingKey ^ d) * 0x100000001B3ull;
        SlotBinding& memo = g_bindings[(bindingKey ^ bindingKey >> 29) & (kBindingSlots - 1)];
        static const bool noSlotMemo = getenv("REGPU_NO_SLOT_MEMO") != nullptr || getenv("REGPU_DUMP_PS_TEXTURES") != nullptr;
        if (!noSlotMemo && memo.frame == g_r.frame && memo.epoch == g_resolveEpoch && memcmp(memo.fetch, fetch.dwords, sizeof(memo.fetch)) == 0)
        {
            memcpy(pc->tinfo[slot], memo.tinfo, sizeof(memo.tinfo));
            memcpy(pc->tbias[slot], memo.tbias, sizeof(memo.tbias));
            g_drawSamplesResolve |= memo.samplesResolve;
            continue;
        }
        const bool samplesBefore = g_drawSamplesResolve;
        g_drawSamplesResolve = false;
        const bool cube = fetch.Dimension() == 3;
        static const uint64_t dumpPs = getenv("REGPU_DUMP_PS_TEXTURES") ? strtoull(getenv("REGPU_DUMP_PS_TEXTURES"), nullptr, 16) : 0;
        if (dumpPs && C().psHash == dumpPs && !cube)
        {
            static std::set<uint32_t> dumped;
            std::vector<uint8_t> rgba;
            if (dumped.insert(fetch.Address()).second && DecodeTexture(fetch, rgba))
            {
                char name[80];
                std::filesystem::create_directories("out/tex");
                snprintf(name, sizeof(name), "out/tex/t%u_%08X_%s_%ux%u_sw%03X.png", slot, fetch.Address(), TextureFormatName(fetch.Format()), fetch.Width(),
                    fetch.Height(), fetch.Swizzle());
                WritePng(name, fetch.Width(), fetch.Height(), rgba);
            }
        }
        DXGI_FORMAT format = DXGI_FORMAT_R8G8B8A8_UNORM;
        bool native = false;
        g_textureFailure[0] = 0;
        const bool volume = fetch.Dimension() == 2;
        ID3D12Resource* texture = cube ? GetCubeTexture(fetch, format) : volume ? GetVolumeTexture(fetch, format) : GetTexture(fetch, format, native);
        // Debug: REGPU_NO_LIVE_CUBE=1 binds the fallback for cubes the title rendered (live reflections).
        if (LiveSwitch &noLiveCube = g_sw_REGPU_NO_LIVE_CUBE; noLiveCube && cube && ResolvedFor(fetch))
            texture = nullptr;
        const uint32_t kind = cube ? 1u : volume ? 2u : 0u;
        const uint32_t index = texture ? BindlessIndex(texture, format, kind) : kind;
        if (RendererTracing())
            CaptureTexture(fetch, slot, cube, texture != nullptr, index, native, g_textureFailure);
        // w: dimension (1 2D, 3 cube); bit 31 + bits 8-19: the fetch swizzle, applied in the
        // shader for textures that live on the GPU (resolves). Decoded ones are swizzled already.
        LiveSwitch &noGpuSwizzle = g_sw_REGPU_NO_GPU_SWIZZLE;
        const ResolvedTexture* resolvedPtr = ResolvedFor(fetch);
        const bool onGpu = (!noGpuSwizzle && fetch.Type() == 2 && resolvedPtr != nullptr) || native;
        // Our resolved textures keep red and blue in place; when the title
        // resolved with the swap bit, its fetch swizzle expects them exchanged.
        uint32_t swizzle = fetch.Swizzle();
        if (onGpu && resolvedPtr && resolvedPtr->swapped)
            for (uint32_t c = 0; c < 4; c++)
            {
                const uint32_t sel = (swizzle >> (3 * c)) & 7;
                if (sel == 0 || sel == 2)
                    swizzle ^= (sel ^ (2 - sel)) << (3 * c);
            }
        // Built here and written once: the constants live in upload memory, which is slow to read back.
        uint32_t ti[4]{};
        float tb[4]{};
        ti[3] = (cube ? 3 : volume ? 2 : 1) | (onGpu ? (1u << 31) | (swizzle << 8) : 0);
        // Clamp X and Y (dword 0 bits 10-12, 13-15): 0 wrap, else clamp. Filter: dword 3 bits
        // 19-20 (mag): 0 point. With only one axis clamped (kerbs: clamped across, repeating
        // along) the wrapping sampler is used and the shader clamps that axis (bits 18, 19).
        const uint32_t clampX = (fetch.dwords[0] >> 10) & 7, clampY = (fetch.dwords[0] >> 13) & 7;
        LiveSwitch &oldClamp = g_sw_REGPU_OLD_CLAMP; // A/B: X's mode for both axes (before the kerb fix)
        const bool clamp = oldClamp ? clampX != 0 : clampX != 0 && clampY != 0;
        const bool point = ((fetch.dwords[3] >> 19) & 3) == 0;
        ti[0] = (point ? (clamp ? 2 : 3) : (clamp ? 1 : 0)) | ((uint32_t(fetch.ExpAdjust()) & 0x3F) << 8);
        // Gamma-signed components (fetch dword 0 bits 2-9, two bits a texel component; 3 = gamma): the
        // shader converts them to linear (bits 24-27, in the order TFetchRaw returns: the texel's for
        // textures swizzled in the shader, the swizzled order for decoded ones). Ignoring them left
        // FH1's colour maps (most of its textures) in gamma. But with it FH1 came out far darker than
        // stock ReXGlue (Xenia, which converts these too): something else in the chain differs, not
        // found yet. Opt-in until then: REGPU_TEXTURE_GAMMA=1.
        // (=2: only textures decoded here, =3: only those on the GPU (resolves, native block formats).)
        static const int textureGamma = getenv("REGPU_TEXTURE_GAMMA") ? atoi(getenv("REGPU_TEXTURE_GAMMA")) : 0;
        if (textureGamma == 1 || (textureGamma == 2 && !onGpu) || (textureGamma == 3 && onGpu) || (textureGamma == 4 && native) ||
            (textureGamma == 5 && resolvedPtr))
        {
            uint32_t gammaMask = 0;
            for (uint32_t c = 0; c < 4; c++)
            {
                const uint32_t sel = onGpu ? c : (fetch.Swizzle() >> (3 * c)) & 7;
                if (sel < 4 && ((fetch.dwords[0] >> (2 + 2 * sel)) & 3) == 3)
                    gammaMask |= 1u << c;
            }
            ti[0] |= gammaMask << 24;
        }
        if (!clamp && !oldClamp)
            ti[0] |= (clampX ? 1u << 18 : 0u) | (clampY ? 1u << 19 : 0u);
        // The mirroring modes (1 mirrored repeat; 3, 5, 7 mirrored once, then clamped): the shader folds
        // the coordinate (bits 28 U, 29 V; 30, 31 once). Taken as plain clamps, FH1's wrong-way sign came
        // out blank: its arrow is the mirror image of an atlas icon, fetched at u -0.835..-0.668 with U
        // mirrored, and clamped to the transparent edge texel. REGPU_NO_MIRROR=1: as clamps (A/B).
        if (static LiveSwitch noMirror("REGPU_NO_MIRROR"); !noMirror)
        {
            const auto mirror = [](uint32_t mode, uint32_t bit, uint32_t onceBit) {
                return mode == 1 ? bit : (mode == 3 || mode == 5 || mode == 7) ? bit | onceBit : 0u;
            };
            ti[0] |= mirror(clampX, 1u << 28, 1u << 30) | mirror(clampY, 1u << 29, 1u << 31);
        }
        // Anisotropy the fetch asks for (dword 3 bits 25-27: 2 = 2x .. 5 = 16x), for the linear wrapping
        // sampler: bits 20-22 pick s4-s7 (hlsl.cpp). Only with REGPU_ANISO_FROM_FETCH (else s0 is 16x).
        if (const uint32_t aniso = (fetch.dwords[3] >> 25) & 7; AnisoFromFetch() && !point && !clamp && aniso >= 2 && aniso <= 5)
            ti[0] |= (aniso - 1) << 20;
        // Mirrored modes (1 mirrored repeat; 3, 5, 7 mirror once, then clamp): the shader folds the
        // coordinate first (bits 28, 29 repeat; 30, 31 once), and the axis is still clamped after the
        // fold (wrapped, the filter blends the opposite edge in). Clamped alone, a mirrored half
        // smeared the edge column across (FM2 port: the telemetry's car diagram, as Xenia shows it).
        static LiveSwitch noMirror("REGPU_NO_TEXTURE_MIRROR"); // A/B: mirrored modes clamped only
        if (!oldClamp && !noMirror)
            ti[0] |= (clampX == 1 ? 1u << 28 : 0u) | (clampY == 1 ? 1u << 29 : 0u) | (clampX >= 3 && (clampX & 1) ? 1u << 30 : 0u) |
                (clampY >= 3 && (clampY & 1) ? 1u << 31 : 0u);
        // Clamp to border on both axes (modes 6, 7): the shader returns the border colour
        // outside the image (dword 5 bits 0-1: 1 white, else black).
        if (clampX >= 6 && clampY >= 6)
            ti[0] |= (1u << 16) | ((fetch.dwords[5] & 3) == 1 ? 1u << 17 : 0u);
        // Below the output resolution (a temporal upscaler's scene) the mips are biased sharper by log2 of the
        // scale (-0.58 at 67%), as upscalers ask, so the upscaled image keeps the output's texture detail
        // (from the FM4 port). REGPU_NO_LOD_BIAS=1 (live): the fetch's bias alone.
        static LiveSwitch noLodBias("REGPU_NO_LOD_BIAS");
        tb[0] = fetch.LodBias() + (g_sceneScale < 1.0f && !noLodBias ? std::log2(g_sceneScale) : 0.0f);
        memcpy(&tb[1], &index, 4);
        // A resolved texture is as wide as the pitch it was resolved with; the fetch
        // constant may declare a narrower image inside it (e.g. 163 of 192 columns):
        // the shader scales UVs so 0..1 spans the declared image, not the padding.
        tb[2] = tb[3] = 1.0f;
        if (onGpu && resolvedPtr && !cube && resolvedPtr->width && resolvedPtr->height && resolvedPtr->resource)
        {
            tb[2] = std::min(1.0f, float(fetch.Width()) / float(resolvedPtr->width));
            tb[3] = std::min(1.0f, float(fetch.Height()) / float(resolvedPtr->height));
        }
        ti[1] = fetch.Width();
        ti[2] = fetch.Height();
        memcpy(memo.fetch, fetch.dwords, sizeof(memo.fetch));
        memo.frame = g_r.frame;
        memo.epoch = g_resolveEpoch;
        memcpy(memo.tinfo, ti, sizeof(memo.tinfo));
        memcpy(memo.tbias, tb, sizeof(memo.tbias));
        memcpy(pc->tinfo[slot], ti, sizeof(ti));
        memcpy(pc->tbias[slot], tb, sizeof(tb));
        memo.samplesResolve = g_drawSamplesResolve;
        g_drawSamplesResolve |= samplesBefore;
    }
    pc->info[2] = 0;
    // Colour exponent bias (RB_COLOR_INFO bits 20-25, signed): the hardware scales the colour output by
    // 2^bias before it is stored and blended, in every format; the 16_16 and 16_16_16_16 targets
    // (formats 4, 5) hold -32..32 (pinfo.z bit 3). A title drawing a 16_16 shadow map with bias 5,
    // resolved with copy bias 10 and fetched with exponent -15, got every value 32x too small (from
    // the FM4 port). REGPU_NO_COLOR_EXP_BIAS=1: unscaled (A/B).
    {
        static LiveSwitch noColorExpBias("REGPU_NO_COLOR_EXP_BIAS");
        const uint32_t colorInfo = gpureg::Get(RB_COLOR_INFO);
        pc->info[3] = noColorExpBias ? 0u : uint32_t(int32_t(colorInfo << 6) >> 26);
        const uint32_t format = (colorInfo >> 16) & 0xF;
        if (!noColorExpBias && (format == 4 || format == 5))
            pc->info[2] |= 8u;
        // A k_2_10_10_10_FLOAT surface (formats 3, 12) keeps 2 bits of alpha (see the resolve's
        // REGPU_NO_RESOLVE_ALPHA2); the draw's output is rounded as the EDRAM stores it. (Blending
        // with the stored alpha still reads ours, unrounded.) REGPU_NO_ALPHA2=1: as before.
        // Not when the colour blend reads the source alpha (factors 6, 7, 16): the blender takes the
        // shader's alpha at full precision, only the stored value has 2 bits. Rounded, PGR4's menu
        // ground (blended over the tyre smoke by its alpha) cut the smoke into hard grey steps.
        static LiveSwitch noAlpha2("REGPU_NO_ALPHA2");
        const uint32_t blend = gpureg::Get(RB_BLENDCONTROL0), srcFactor = blend & 0x1F, dstFactor = (blend >> 8) & 0x1F;
        auto readsSrcAlpha = [](uint32_t f) { return f == 6 || f == 7 || f == 16; };
        if (!noAlpha2 && (format == 3 || format == 12) && !readsSrcAlpha(srcFactor) && !readsSrcAlpha(dstFactor))
            pc->info[2] |= 32u;
    }
    // A vertex shader that allocates interpolators but exports none (depth-only shaders end with an
    // empty export) leaves the pixel shader's inputs to whatever the hardware's parameter cache
    // held: titles rely on cut-out kills there passing (Top Spin 4's player shadows: a texture
    // looked up at the unwritten coordinate, read as 0 here, cut whole bodies out of the shadow map).
    // In depth-only passes such kills are skipped (bit 2). REGPU_NO_KILL_SKIP=1: kept.
    if (LiveSwitch &noKillSkip = g_sw_REGPU_NO_KILL_SKIP; !noKillSkip && edramMode == 5 && !vs.info.interpolatorsWritten)
        pc->info[2] |= 4u;
    // Alpha to mask (RB_COLORCONTROL bit 4) on a single-sample target: as Xenia, a 2x2 dither, each
    // pixel keeping its colour where alpha >= 1 - offset/4, the offsets RB_COLORCONTROL bits 24-31
    // (pinfo.z bit 6, offsets in bits 8-15). D3D's coverage cut everything at alpha 0.5: PGR4's menu
    // tyre smoke came out as hard-edged grey shapes. REGPU_NO_ALPHA_DITHER=1: D3D's coverage.
    if (static LiveSwitch noAlphaDither("REGPU_NO_ALPHA_DITHER"); !noAlphaDither && g_msaa == 1 && edramMode != 5 && (colorControl >> 4) & 1)
        pc->info[2] |= 64u | (colorControl >> 24) << 8;
    // The pixel position register (PsParamGen) holds the pixel's integer position, D3D9's convention
    // (Xenia: the host pixel rounded down, then the render scale undone); the host pixel's centre was
    // half a host pixel past it. Screen-space lookups that add half a texel then read past the pixel's
    // own texel (at 1.5, FH1's shadow passes' depth fetches one row apart could fall on one host row).
    // Bit 16 (bit 6 is the alpha dither). REGPU_OLD_PIXEL_POS=1: the centre (A/B).
    if (static LiveSwitch oldPixelPos("REGPU_OLD_PIXEL_POS"); !oldPixelPos)
        pc->info[2] |= 1u << 16;
    // HUD and menus sample ordinary textures; full-screen layers sample resolved targets
    // and must keep spanning the width (squeezing them warped the image outside 16:9).
    vc->vwide[3] = g_drawSamplesResolve ? 0.0f : 1.0f;

    Section(5);
    // Viewport. The viewport transform happens in the vertex shader; D3D maps 1:1.
    // Tiled passes shift each draw into EDRAM by the window offset. We render in screen
    // space, so tiled draws stay unshifted. The title's rectangle draws (depth resets)
    // reset the window offset to zero and use tile-relative coordinates instead:
    // those land in the current tile, so shift them by the tile's origin.
    D3D12_RECT scissor = WindowScissor();
    float shiftY = 0;
    {
        const uint32_t offsetReg = gpureg::Get(PA_SC_WINDOW_OFFSET);
        const int32_t wy = ((gpureg::Get(PA_SU_SC_MODE_CNTL) >> 16) & 1) ? int32_t((offsetReg >> 16) << 17) >> 17 : 0;
        const bool full = scissor.left <= 0 && scissor.top <= 0 && scissor.right >= LONG(PassWidth()) && scissor.bottom >= LONG(PassHeight());
        // REGPU_TRACE_TILES=1: draws per frame in tile passes (by tile rectangle) and full ones.
        if (LiveSwitch &traceTiles = g_sw_REGPU_TRACE_TILES; traceTiles)
        {
            static std::map<std::tuple<LONG, LONG, LONG, LONG>, uint32_t> byRect;
            static uint32_t fullDraws = 0;
            static uint64_t frame = 0;
            if (frame != g_r.frame)
            {
                if (frame && g_r.frame % 60 == 0)
                {
                    std::string text;
                    for (const auto& [r, n] : byRect)
                        text += " [" + std::to_string(std::get<0>(r)) + "," + std::to_string(std::get<1>(r)) + "-" + std::to_string(std::get<2>(r)) + "," +
                            std::to_string(std::get<3>(r)) + "]=" + std::to_string(n);
                    Log("tiles: %u full-pass draws;%s", fullDraws, text.c_str());
                }
                byRect.clear();
                fullDraws = 0;
                frame = g_r.frame;
            }
            if (full)
                fullDraws++;
            else
                byRect[{ scissor.left, scissor.top, scissor.right, scissor.bottom }]++;
        }
        if (!full || TileMerge::t_widened)
        {
            // (A merged first tile is the tile pass now: its draws are not shifted into an older tile.)
            g_tile.originY = -wy;
            g_tile.rect = scissor;
            g_tile.surface = gpureg::Get(RB_SURFACE_INFO);
        }
        // Only draws on the tiled pass's own surface are tile-relative: a photo or replay mode can draw its
        // depth-of-field composite once, full-screen, into another view of the same EDRAM after
        // the 4x scene's three tiles, and clipped to the last tile it covered only the bottom
        // third (the rest of the picture was black).
        // Another MSAA view of the tiled surface itself (stencil clears of a composite) is still
        // tile-relative.
        else if (g_tile.originY != 0 && (g_tile.surface == ~0u || g_tile.surface == gpureg::Get(RB_SURFACE_INFO) || (!g_noForeignTile && ForeignNativeMsaa() >= 0)))
        {
            shiftY = float(g_tile.originY + wy);
            scissor.left = std::max(scissor.left, g_tile.rect.left);
            scissor.top = std::max(scissor.top, g_tile.rect.top);
            scissor.right = std::min(scissor.right, g_tile.rect.right);
            scissor.bottom = std::min(scissor.bottom, g_tile.rect.bottom);
        }
    }
    const D3D12_VIEWPORT vp{ 0, shiftY * ScaleY(), float(PassWidth()) * ScaleX(), float(PassHeight()) * ScaleY(), 0, 1 };
    if (scissor.right <= scissor.left || scissor.bottom <= scissor.top)
    {
        TraceSkip("empty scissor", initiator);
        g_r.skippedThisFrame++;
        return;
    }
    D3D12_RECT hostScissor = ToHost(scissor);
    if (TileMerge::LaterClear& lc = TileMerge::g_laterClear; lc.pending[3] > lc.pending[1])
    {
        const D3D12_RECT rows = ToHost(D3D12_RECT{ lc.pending[0], lc.pending[1], lc.pending[2], lc.pending[3] });
        lc.pending[3] = lc.pending[1];
        if (lc.color)
        {
            float color[4];
            ClearColor(lc.colorFormat, lc.colorLo, lc.colorHi, color);
            TransitionColor(D3D12_RESOURCE_STATE_RENDER_TARGET);
            C().list->ClearRenderTargetView(C().rtv, color, 1, &rows);
        }
        if (lc.depth)
        {
            const uint32_t d = lc.depthValue >> 8;
            float depth = float(d) / 16777215.0f;
            if (lc.depthFloat)
            {
                const uint32_t ex = d >> 20, m = d & 0xFFFFF;
                depth = std::clamp(ex ? std::ldexp(1.0f + float(m) / 1048576.0f, int(ex) - 15) : std::ldexp(float(m) / 1048576.0f, -14), 0.0f, 1.0f);
            }
            C().list->ClearDepthStencilView(C().dsv, D3D12_CLEAR_FLAG_DEPTH | D3D12_CLEAR_FLAG_STENCIL, depth, UINT8(lc.depthValue & 0xFF), 1, &rows);
        }
    }
    if (LiveSwitch &traceWidened = g_sw_REGPU_TRACE_TILE_WIDEN; traceWidened && TileMerge::t_widened)
    {
        static uint64_t logged = 0;
        if (logged != g_r.frame)
        {
            logged = g_r.frame;
            Log("tile merge draw: scissor %ld,%ld-%ld,%ld host %ld,%ld-%ld,%ld viewport y %g h %g shift %g pass %ux%u", scissor.left, scissor.top, scissor.right,
                scissor.bottom, hostScissor.left, hostScissor.top, hostScissor.right, hostScissor.bottom, vp.TopLeftY, vp.Height, shiftY, PassWidth(), PassHeight());
        }
    }
    // A scissor narrower than the screen in a wide pass clips 2D content (a minimap),
    // which the vertex shader keeps in the centred 16:9 area: map it the same way. Not for
    // screen layers (post-processing done in halves would leave a seam).
    if (WidePass() && ScaleWide() != ScaleY() && !g_drawSamplesResolve && (scissor.left > 0 || scissor.right < LONG(kWidth)))
    {
        const double centre = kWidth * ScaleWide() / 2.0, s = ScaleY();
        hostScissor.left = LONG(std::lround(centre + (scissor.left - double(kWidth) / 2) * s));
        hostScissor.right = LONG(std::lround(centre + (scissor.right - double(kWidth) / 2) * s));
    }
    DrawState& ds = g_drawState;
    if (memcmp(&ds.viewport, &vp, sizeof(vp)) != 0)
    {
        ds.viewport = vp;
        C().list->RSSetViewports(1, &vp);
    }
    if (memcmp(&ds.scissor, &hostScissor, sizeof(hostScissor)) != 0)
    {
        ds.scissor = hostScissor;
        C().list->RSSetScissorRects(1, &hostScissor);
    }
    if (const uint32_t ref = gpureg::Get(RB_STENCILREFMASK) & 0xFF; ref != ds.stencilRef)
    {
        ds.stencilRef = ref;
        C().list->OMSetStencilRef(ref);
    }
    // The game asked for the colour target to be cleared before this draw (Host::clearBeforeDraw).
    if (clearBefore)
        C().list->ClearRenderTargetView(C().rtv, clearColour, 1, &hostScissor);
    {
        // RB_BLEND_RED..ALPHA: the constant colour for blend factors 12-15.
        const float factor[4] = { RegFloat(0x2105), RegFloat(0x2106), RegFloat(0x2107), RegFloat(0x2108) };
        if (memcmp(ds.blendFactor, factor, sizeof(factor)) != 0)
        {
            memcpy(ds.blendFactor, factor, sizeof(factor));
            C().list->OMSetBlendFactor(factor);
        }
    }
    if (motion != ds.motionRoot)
    {
        // Motion pipelines have their own root signature; switching clears the root arguments.
        ds.motionRoot = motion;
        C().list->SetGraphicsRootSignature(motion ? g_r.motionRootSignature.Get() : g_r.rootSignature.Get());
        ds.table2 = ds.table3 = D3D12_GPU_DESCRIPTOR_HANDLE{};
    }
    if (motion != ds.motionTargets)
    {
        ds.motionTargets = motion;
        const D3D12_CPU_DESCRIPTOR_HANDLE targets[2] = { C().rtv, g_upscale.motionRtv };
        C().list->OMSetRenderTargets(motion ? 2 : 1, targets, FALSE, &C().dsv);
    }
    if (pso != ds.pso)
    {
        ds.pso = pso;
        const auto t0 = g_perfTiming ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
        C().list->SetPipelineState(pso);
        if (g_perfTiming)
        {
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            if (ms > 1.0)
                Log("perf: SetPipelineState took %.1f ms (vs %016llx ps %016llx)", ms, (unsigned long long)C().vsHash, (unsigned long long)C().psHash);
        }
    }
    if (Unstable::On())
    {
        using namespace Unstable;
        Begin();
        Draw d;
        d.vsHash = C().vsHash;
        d.psHash = C().psHash;
        d.initiator = initiator;
        d.firstVertexFetch = gpureg::Get(gpureg::FetchConstants);
        d.dmaBase = dmaBase;
        d.dmaSize = dmaSize;
        d.input = InputKey(initiator, dmaBase, dmaSize, d.vsHash, d.psHash);
        uint64_t h = 0xCBF29CE484222325ull;
        for (uint32_t k : vs.info.vertexFetchConstants)
        {
            // In place: its address. Copied: that it was (the ring offset moves every frame) and
            // what was copied.
            const uint32_t d1 = gpureg::Get(gpureg::FetchConstants + k * 2 + 1), bytes = ((d1 >> 2) & 0xFFFFFF) * 4;
            const uint32_t address = gpureg::Get(gpureg::FetchConstants + k * 2) & 0xFFFFFFFC;
            h = Mix(Mix(h, k), vc->vf[k][2]);
            h = Mix(h, vc->vf[k][2] ? vc->vf[k][0] : ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(address)), std::min<uint32_t>(bytes, 1u << 20), 64));
        }
        d.part[kVertices] = h;
        d.part[kIndices] = Mix(Mix(Mix(Mix(0, indexedCount), indexView.BufferLocation), uint64_t(reinterpret_cast<uintptr_t>(indexEntry))),
            indexEntry ? indexEntry->offset : 0);
        h = 0;
        for (uint32_t m = ps.info.usedTextureSlots | vs.info.usedTextureSlots; m; m &= m - 1)
        {
            const uint32_t slot = uint32_t(__builtin_ctz(m));
            uint64_t t[4];
            memcpy(t, pc->tinfo[slot], 16);
            memcpy(t + 2, pc->tbias[slot], 16);
            for (uint64_t v : t)
                h = Mix(h, v);
        }
        d.part[kTextures] = h;
        d.part[kPipeline] = uint64_t(reinterpret_cast<uintptr_t>(pso));
        d.part[kScissor] = Mix(Mix(Mix(Mix(0, uint64_t(hostScissor.left)), uint64_t(hostScissor.top)), uint64_t(hostScissor.right)), uint64_t(hostScissor.bottom)) ^
            uint64_t(std::bit_cast<uint32_t>(vp.TopLeftY)) << 32;
        d.part[kTile] = TileMerge::t_widened;
        // The data itself, every dword (a paused game rewriting geometry it draws).
        h = 0;
        for (uint32_t k : vs.info.vertexFetchConstants)
        {
            const uint32_t d1 = gpureg::Get(gpureg::FetchConstants + k * 2 + 1), bytes = ((d1 >> 2) & 0xFFFFFF) * 4;
            const uint32_t address = gpureg::Get(gpureg::FetchConstants + k * 2) & 0xFFFFFFFC;
            h = Mix(h, ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(address)), std::min<uint32_t>(bytes, 4u << 20), 4));
        }
        d.part[kVertexData] = h;
        {
            const bool is32 = (initiator >> 11) & 1;
            const uint32_t count = dmaSize & 0xFFFFFF;
            d.part[kIndexData] = dmaBase && count ? ContentHash(GuestPtr<uint8_t>(GpuPhysToGuest(dmaBase & 0x1FFFFFFF & ~(is32 ? 3u : 1u))),
                                                        std::min<uint32_t>(count * (is32 ? 4 : 2), 4u << 20), 4)
                                                  : 0;
        }
        g_current.push_back(d);
    }
    if (!inRing)
    {
        // Only the vertex fetch and texture entries this draw's shaders read: the tables are sized for
        // 96 fetch constants and 32 slots, a draw uses a few, and the rest was ~3 KB of padding per draw
        // (17 MB a frame of upload writes in PGR4's races). The ring bytes skipped are never read.
        // REGPU_FULL_CONSTANT_BLOCKS=1 copies the whole blocks (A/B).
        LiveSwitch &fullBlocks = g_sw_REGPU_FULL_CONSTANT_BLOCKS;
        const size_t vsFrom = hooked ? 0 : offsetof(VSConstants, b), psFrom = offsetof(PSConstants, b);
        auto put = [](void* ring, const void* stage, size_t from, size_t to) {
            if (to > from)
                memcpy(static_cast<uint8_t*>(ring) + from, static_cast<const uint8_t*>(stage) + from, to - from);
        };
        if (fullBlocks)
        {
            put(vcRing, vc, vsFrom, sizeof(VSConstants));
            put(pcRing, pc, psFrom, sizeof(PSConstants));
        }
        else
        {
            uint32_t fetches = 0;
            for (uint32_t k : vs.info.vertexFetchConstants)
                fetches = std::max(fetches, k + 1);
            const size_t layouts = std::min<size_t>(vs.info.vertexFetchLayout.size(), 32);
            const uint32_t slots = usedSlots ? 32 - uint32_t(__builtin_clz(usedSlots)) : 0;
            put(vcRing, vc, vsFrom, offsetof(VSConstants, vf));
            put(vcRing, vc, offsetof(VSConstants, vf), offsetof(VSConstants, vf) + fetches * sizeof(vc->vf[0]));
            put(vcRing, vc, offsetof(VSConstants, info), offsetof(VSConstants, vfl));
            put(vcRing, vc, offsetof(VSConstants, vfl), offsetof(VSConstants, vfl) + layouts * sizeof(vc->vfl[0]));
            put(vcRing, vc, offsetof(VSConstants, vuser), sizeof(VSConstants));
            put(pcRing, pc, psFrom, offsetof(PSConstants, tinfo));
            put(pcRing, pc, offsetof(PSConstants, tinfo), offsetof(PSConstants, tinfo) + slots * sizeof(pc->tinfo[0]));
            put(pcRing, pc, offsetof(PSConstants, tbias), offsetof(PSConstants, tbias) + slots * sizeof(pc->tbias[0]));
        }
    }
    C().list->SetGraphicsRootConstantBufferView(0, f.ring->GetGPUVirtualAddress() + vsOffset);
    C().list->SetGraphicsRootConstantBufferView(1, f.ring->GetGPUVirtualAddress() + psOffset);
    if (motion)
        C().list->SetGraphicsRootConstantBufferView(LeanFetch() ? 5 : 4, motionPrevious);
    if (const D3D12_GPU_DESCRIPTOR_HANDLE t2 = Gpu(g_r.srvHeap.Get(), f.descriptorBase, g_r.srvStride); t2.ptr != ds.table2.ptr)
    {
        ds.table2 = t2;
        C().list->SetGraphicsRootDescriptorTable(2, t2);
    }
    if (const D3D12_GPU_DESCRIPTOR_HANDLE t3 = Gpu(g_r.srvHeap.Get(), kBindlessBase, g_r.srvStride); t3.ptr != ds.table3.ptr)
    {
        ds.table3 = t3;
        C().list->SetGraphicsRootDescriptorTable(3, t3);
        ds.samplers = {}; // set again with it (after a root signature switch)
    }
    if (g_r.samplerHeap)
        if (const D3D12_GPU_DESCRIPTOR_HANDLE s = SamplerTable(); s.ptr != ds.samplers.ptr)
        {
            ds.samplers = s;
            C().list->SetGraphicsRootDescriptorTable(4, s);
        }
    // REGPU_CAPTURE_COMPOSITE=<n>: trace (as F12) the n-th frame that draws into the 2048-wide
    // surface (pitch field 0x820: a 2048-wide composite), from its first such draw on.
    if (static const int compositeN = getenv("REGPU_CAPTURE_COMPOSITE") ? atoi(getenv("REGPU_CAPTURE_COMPOSITE")) : 0;
        compositeN && (gpureg::Get(RB_SURFACE_INFO) & 0x3FFF) == 0x820)
    {
        static uint64_t lastFrame = ~0ull;
        static int seen = 0;
        if (g_r.frame != lastFrame && ++seen == compositeN)
        {
            g_hotkeyFrame = g_r.frame + 1;
            Log("capture: composite pass %d, tracing frame %llu", seen, (unsigned long long)(g_r.frame + 1));
        }
        lastFrame = g_r.frame;
    }
    const uint64_t traceFrame = TraceFrame();
    if (traceFrame && g_r.frame + 1 == traceFrame)
    {
        std::string tex;
        for (uint32_t slot = 0; slot < 32; slot++)
        {
            if ((((ps.info.usedTextureSlots | vs.info.usedTextureSlots) >> slot) & 1) == 0)
                continue;
            const TextureFetch t = ReadTextureFetch(slot);
            char e[160];
            snprintf(e, sizeof(e), " %st%u=%s %ux%u@%08X sw%03X sg%02X d%u [%08X %08X %08X %08X %08X %08X]", ((ps.info.usedTextureSlots >> slot) & 1) ? "" : "vs:", slot, TextureFormatName(t.Format()), t.Width(), t.Height(), t.Address(), t.Swizzle(),
                (t.dwords[0] >> 2) & 0xFF, t.Dimension(), t.dwords[0], t.dwords[1], t.dwords[2], t.dwords[3], t.dwords[4], t.dwords[5]);
            tex += e;
        }
        Log("    indices (initiator %08X dma %08X offset %u): %u %u %u %u %u %u ... %u", initiator, dmaBase, gpureg::Get(VGT_INDX_OFFSET),
            list.size() > 0 ? list[0] : 0, list.size() > 1 ? list[1] : 0, list.size() > 2 ? list[2] : 0, list.size() > 3 ? list[3] : 0,
            list.size() > 4 ? list[4] : 0, list.size() > 5 ? list[5] : 0, list.empty() ? 0 : list.back());
        Log("draw #%u vs %016llx ps %016llx prim %u n %zu blend %08X mask %X scissor %ld,%ld-%ld,%ld vte %03X wide %d layer %d pitch %u%s%s", g_r.drawsThisFrame.load(),
            (unsigned long long)C().vsHash, (unsigned long long)C().psHash, initiator & 0x3F, list.size(), gpureg::Get(RB_BLENDCONTROL0),
            gpureg::Get(RB_COLOR_MASK) & 15, scissor.left, scissor.top, scissor.right, scissor.bottom, gpureg::Get(PA_CL_VTE_CNTL) & 0x3FF,
            int(WidePass()), int(g_drawSamplesResolve), gpureg::Get(RB_SURFACE_INFO) & 0x3FFF,
            g_occlusionOpen ? " [in occlusion query]" : GetShader(C().vsHash, C().vsCode, false).info.memExport ? " [memexport]" : "", tex.c_str());
        Log("    host: scissor %ld,%ld-%ld,%ld viewport %g,%g %gx%g depth %p colour %p (bases %03X %03X)", hostScissor.left, hostScissor.top,
            hostScissor.right, hostScissor.bottom, vp.TopLeftX, vp.TopLeftY, vp.Width, vp.Height, static_cast<void*>(C().depth.Get()),
            static_cast<void*>(C().color.Get()), C().depthBase, C().colorBase);
        uint32_t bools[8];
        for (int i = 0; i < 8; i++)
            bools[i] = gpureg::Get(gpureg::BoolConstants + i);
        Log("    bools %08X %08X %08X %08X %08X %08X %08X %08X", bools[0], bools[1], bools[2], bools[3], bools[4], bools[5], bools[6], bools[7]);
        if (getenv("REGPU_TRACE_VC"))
            for (uint32_t i = 0; i < 256; i++)
            {
                // The vertex shader's constants as the guest set them (the register file), the ones it reads
                // marked: the ring holds only those, and a hooked draw's are elsewhere.
                float c[4];
                for (int j = 0; j < 4; j++)
                {
                    const uint32_t bits = gpureg::Get(gpureg::AluConstants + i * 4 + j);
                    memcpy(&c[j], &bits, 4);
                }
                const bool read = ((vs.info.constantMask[i >> 6] >> (i & 63)) & 1) != 0;
                if (read || c[0] != 0 || c[1] != 0 || c[2] != 0 || c[3] != 0)
                    Log("    vc%u = %g %g %g %g%s", i, c[0], c[1], c[2], c[3], read ? " (read)" : "");
            }
        if (vs.hlslSource && vs.hlslSource != &vs)
            Log("    vs is a variant of %016llx (its code, this shader's fetch layout)", (unsigned long long)vs.hlslSource->hash);
        // The shader's fetches (VFetchK k): fetch constant, format, signed, normalised, stride, offset, exponent.
        for (size_t k = 0; k < vs.info.vertexFetchLayout.size(); k++)
        {
            const auto& l = vs.info.vertexFetchLayout[k];
            Log("    fetch %zu: vf%u format %u%s%s stride %u offset %u exp %d", k, l[0] & 0x7F, (l[0] >> 8) & 0x3F, (l[0] & 0x4000) ? " signed" : "",
                    (l[0] & 0x8000) ? " norm" : "", l[1], l[2], int(l[3] << 10) >> 26);
        }
        for (uint32_t k : vs.info.vertexFetchConstants)
        {
            const uint32_t d0 = gpureg::Get(gpureg::FetchConstants + k * 2), d1 = gpureg::Get(gpureg::FetchConstants + k * 2 + 1);
            const uint32_t* v = GuestPtr<uint32_t>(GpuPhysToGuest(d0 & 0xFFFFFFFC));
            Log("    vf%u: %08X size %u endian %u: %08X %08X %08X %08X %08X %08X %08X %08X", k, d0 & 0xFFFFFFFC, ((d1 >> 2) & 0xFFFFFF) * 4, d1 & 3,
                v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]);
            if ((initiator & 0x3F) == 8)
            {
                // Rectangle lists: print the corners as floats (big-endian vertex data).
                std::string corners;
                for (uint32_t i = 0; i < 3 && (i * 7 + 1) * 4 < ((d1 >> 2) & 0xFFFFFF) * 4; i++)
                {
                    uint32_t xb = __builtin_bswap32(v[i * 7]), yb = __builtin_bswap32(v[i * 7 + 1]);
                    float x, y;
                    memcpy(&x, &xb, 4);
                    memcpy(&y, &yb, 4);
                    char t[48];
                    snprintf(t, sizeof(t), " (%g,%g)", x, y);
                    corners += t;
                }
                Log("    rect corners:%s", corners.c_str());
            }
            std::string nz;
            for (uint32_t i = 0; i * 8 + 5 < ((d1 >> 2) & 0xFFFFFF) && i < 24; i++)
            {
                char t[16];
                snprintf(t, sizeof(t), " %08X", v[i * 8 + 5]);
                nz += t;
            }
            Log("    vf%u normal z per vertex:%s", k, nz.c_str());
            // REGPU_TRACE_VSTAT=<dword>,<stride>: min/max/mean of that big-endian float over the
            // vertices this draw indexes (e.g. 3,15: one vertex stream of a model).
            if (const char* e = getenv("REGPU_TRACE_VSTAT"))
            {
                uint32_t off = 0, stride = 0;
                const uint32_t dwordsInBuffer = (d1 >> 2) & 0xFFFFFF;
                if (sscanf(e, "%u,%u", &off, &stride) == 2 && stride && off < stride && !list.empty())
                {
                    double lo = 1e30, hi = -1e30, sum = 0;
                    uint32_t n = 0, above = 0;
                    for (uint32_t index : list)
                    {
                        const uint64_t at = uint64_t(index) * stride + off;
                        if (at >= dwordsInBuffer)
                            continue;
                        const uint32_t b = __builtin_bswap32(v[at]);
                        float f;
                        memcpy(&f, &b, 4);
                        lo = std::min<double>(lo, f);
                        hi = std::max<double>(hi, f);
                        sum += f;
                        n++;
                        above += f > 0.5f;
                    }
                    if (n)
                        Log("    vf%u dword %u of %u: min %g max %g mean %g, %u of %u above 0.5", k, off, stride, lo, hi, sum / n, above, n);
                    // and the first indices with that value and the vertex's first dword
                    std::string first;
                    for (size_t j = 0; j < list.size() && j < 48; j++)
                    {
                        const uint64_t at = uint64_t(list[j]) * stride;
                        if (at + off >= dwordsInBuffer)
                        {
                            first += " " + std::to_string(list[j]) + ":-";
                            continue;
                        }
                        const uint32_t b0 = __builtin_bswap32(v[at + off]), p0 = __builtin_bswap32(v[at]);
                        float f, x;
                        memcpy(&f, &b0, 4);
                        memcpy(&x, &p0, 4);
                        char t[48];
                        snprintf(t, sizeof(t), " %u:%g@%g", list[j], f, x);
                        first += t;
                    }
                    Log("    vf%u first indices (index:value@dword0):%s", k, first.c_str());
                }
            }
        }
        Log("    scissors: generic %08X-%08X screen %08X-%08X window %08X-%08X offset %08X vport %g,%g +%g,%g", gpureg::Get(0x2090), gpureg::Get(0x2091),
            gpureg::Get(0x200E), gpureg::Get(0x200F), gpureg::Get(PA_SC_WINDOW_SCISSOR_TL), gpureg::Get(PA_SC_WINDOW_SCISSOR_BR), gpureg::Get(PA_SC_WINDOW_OFFSET),
            RegFloat(PA_CL_VPORT_XOFFSET), RegFloat(PA_CL_VPORT_YOFFSET), RegFloat(PA_CL_VPORT_XSCALE), RegFloat(PA_CL_VPORT_YSCALE));
        Log("    vport z scale %g offset %g, vte %08X, clip %08X, depth clamp/zrange regs %08X %08X", RegFloat(PA_CL_VPORT_ZSCALE), RegFloat(PA_CL_VPORT_ZOFFSET), gpureg::Get(0x2206), gpureg::Get(0x2204), gpureg::Get(0x2280), gpureg::Get(0x2281));
        Log("    alpha ref %g (pinfo.x %u) edram mode %u colormask %X, pixel position into r%d (SQ_PROGRAM_CNTL %08X SQ_CONTEXT_MISC %08X)", RegFloat(RB_ALPHA_REF),
            pc->info[0], edramMode, gpureg::Get(RB_COLOR_MASK), pc->info[1] == 0xFF ? -1 : int(pc->info[1]), gpureg::Get(SQ_PROGRAM_CNTL), gpureg::Get(SQ_CONTEXT_MISC));
        Log("    surface %08X color %08X depth %08X depthctl %08X stencil %08X colorctl %08X mode %X", gpureg::Get(0x2000), gpureg::Get(0x2001), gpureg::Get(0x2002),
            gpureg::Get(RB_DEPTHCONTROL), gpureg::Get(RB_STENCILREFMASK), gpureg::Get(RB_COLORCONTROL), gpureg::Get(PA_SU_SC_MODE_CNTL));
        for (uint32_t c = 0; c < 256; c++)
            if (c == 0 || c == 1 || c == 5 || c == 6 || c == 7 || c == 14 || c == 22 || c == 254 || c == 255 || ((ps.info.constantMask[c >> 6] >> (c & 63)) & 1))
                Log("    pc%u = %g %g %g %g", c, pcRing->c[c][0], pcRing->c[c][1], pcRing->c[c][2], pcRing->c[c][3]); // (and every one the pixel shader reads)
    }
    // A draw inside an occlusion query is also drawn with the depth test always passing, in a query of
    // its own: the samples it covers, the console's Total (a game fades a light flare by the share that
    // passed). From pgr4-native's occlusion pairs. REGPU_NO_OCCLUSION_COVERAGE=1: not drawn (Total = ZPass).
    static LiveSwitch noCoverage("REGPU_NO_OCCLUSION_COVERAGE");
    ID3D12PipelineState* coveragePso = g_occlusionOpen && !noCoverage && !motion && g_occlusionCount + 2 <= kOcclusionPerFrame
        ? GetPipeline(vs, ps, edramMode == 5, cullable, false, stripCut && directIndex, true) : nullptr;
    {
        const auto t0 = g_perfTiming ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
        const D3D_PRIMITIVE_TOPOLOGY topology = indexedCount && prim == 6 ? D3D_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP : D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST;
        if (topology != g_drawState.topology)
        {
            g_drawState.topology = topology;
            C().list->IASetPrimitiveTopology(topology);
        }
        if (indexedCount && memcmp(&indexView, &g_drawState.indexBuffer, sizeof(indexView)) != 0)
        {
            g_drawState.indexBuffer = indexView;
            C().list->IASetIndexBuffer(&indexView);
        }
        auto issue = [&] {
            if (indexedCount)
                C().list->DrawIndexedInstanced(indexedCount, 1, 0, 0, 0);
            else
                C().list->DrawInstanced(directIndex ? directVertices : UINT(list.size()), 1, 0, 0);
        };
        if (coveragePso)
        {
            // The open query's part so far ends; the coverage draw gets a query of its own; the query
            // goes on in a new part (queries never overlap).
            EndOcclusionPart();
            const UINT covered = g_r.frameIndex * kOcclusionPerFrame + g_occlusionCount++;
            C().list->BeginQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, covered);
            C().list->SetPipelineState(coveragePso);
            issue();
            C().list->EndQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, covered);
            C().list->ResolveQueryData(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, covered, 1, g_occlusionReadback.Get(), UINT64(covered) * 8);
            g_occlusionCurrent.coverage.push_back(covered);
            C().list->SetPipelineState(pso);
            g_occlusionIndex = g_r.frameIndex * kOcclusionPerFrame + g_occlusionCount++;
            g_occlusionCurrent.main.push_back(g_occlusionIndex);
            C().list->BeginQuery(g_occlusionHeap.Get(), D3D12_QUERY_TYPE_OCCLUSION, g_occlusionIndex);
        }
        issue();
        if (g_perfTiming)
        {
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            if (ms > 1.0)
                Log("perf: DrawInstanced took %.1f ms (vs %016llx ps %016llx)", ms, (unsigned long long)C().vsHash, (unsigned long long)C().psHash);
        }
    }
    Section(6);
    if (C().worker)
        C().draws++;
    else
        g_r.drawsThisFrame++;
    // REGPU_SUBMIT_DRAWS=<n>: submit the frame so far every n draws (never inside an occlusion query), so
    // the GPU starts on it while the rest is recorded (pgr4-native). Off by default: FM2's race, already
    // GPU-bound, lost 4-17% at n = 128-512. It may help a game whose GPU waits on recording.
    static const uint32_t submitEvery = getenv("REGPU_SUBMIT_DRAWS") ? uint32_t(atoi(getenv("REGPU_SUBMIT_DRAWS"))) : 0;
    static uint32_t sinceSubmit = 0;
    if (submitEvery && !C().worker && ++sinceSubmit >= submitEvery && !g_occlusionOpen)
    {
        sinceSubmit = 0;
        SplitMainListAsync(true);
    }
}

}

// ---- Benchmark (benchmark.cmd) ------------------------------------------------------
// Frame times while recording (a timed run), and the report written at the end.
std::atomic<bool> g_benchRecording{ false };
std::mutex g_benchMutex;
std::vector<float> g_benchFrames; // ms

void BenchmarkRecord(bool on)
{
    g_benchRecording.store(on, std::memory_order_relaxed);
}

void BenchmarkWriteReport(const char* path)
{
    std::vector<float> frames;
    {
        std::lock_guard lock(g_benchMutex);
        frames = g_benchFrames;
    }
    std::string cpu = "?", gpu = "?";
    {
        char name[256];
        DWORD size = sizeof(name);
        if (RegGetValueA(HKEY_LOCAL_MACHINE, "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", "ProcessorNameString", RRF_RT_REG_SZ, nullptr,
                name, &size) == ERROR_SUCCESS)
            cpu = name;
        while (!cpu.empty() && cpu.back() == ' ')
            cpu.pop_back();
    }
    uint32_t cores = 0;
    {
        DWORD bytes = 0;
        GetLogicalProcessorInformationEx(RelationProcessorCore, nullptr, &bytes);
        std::vector<uint8_t> buffer(bytes);
        if (bytes && GetLogicalProcessorInformationEx(RelationProcessorCore, reinterpret_cast<SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX*>(buffer.data()), &bytes))
            for (DWORD at = 0; at < bytes; at += reinterpret_cast<SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX*>(buffer.data() + at)->Size)
                cores++;
    }
    MEMORYSTATUSEX memory{ sizeof(memory) };
    GlobalMemoryStatusEx(&memory);
    if (g_r.device)
    {
        ComPtr<IDXGIFactory4> factory;
        ComPtr<IDXGIAdapter1> adapter;
        DXGI_ADAPTER_DESC1 desc{};
        if (SUCCEEDED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))) && SUCCEEDED(factory->EnumAdapterByLuid(g_r.device->GetAdapterLuid(), IID_PPV_ARGS(&adapter))) &&
            SUCCEEDED(adapter->GetDesc1(&desc)))
        {
            char name[128];
            WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1, name, sizeof(name), nullptr, nullptr);
            gpu = std::string(name) + " (" + std::to_string(desc.DedicatedVideoMemory >> 20) + " MB)";
        }
    }
    const Settings& st = GetSettings();
    static const char* upscalers[] = { "off", "FSR 1", "FSR 3", "DLSS" };
    static const char* qualities[] = { "native (AA)", "quality", "balanced", "performance", "ultra performance" };
    std::string upscaler = upscalers[std::min<uint32_t>(uint32_t(st.upscaler), 3)];
    if (st.upscaler != Settings::Upscaler::Off)
        upscaler += std::string(", ") + qualities[std::min<uint32_t>(st.quality, 4)];

    FILE* out = fopen(path, "w");
    if (!out)
        return;
    fprintf(out, "ReGPU benchmark\n\n");
    fprintf(out, "CPU:        %s (%u cores, %u threads)\n", cpu.c_str(), cores, GetActiveProcessorCount(ALL_PROCESSOR_GROUPS));
    fprintf(out, "GPU:        %s\n", gpu.c_str());
    fprintf(out, "RAM:        %.0f GB\n", memory.ullTotalPhys / 1073741824.0);
    fprintf(out, "Resolution: %ux%u%s\n", OutputWidth(), OutputHeight(), st.fullscreen ? " fullscreen" : " windowed");
    fprintf(out, "Upscaler:   %s\n\n", upscaler.c_str());
    if (frames.size() < 100)
    {
        fprintf(out, "Not enough frames recorded (%zu): the run ended too early. Start the game once first\n"
                     "(the first start compiles shaders), then run the benchmark again.\n", frames.size());
        fclose(out);
        return;
    }
    double total = 0;
    for (float f : frames)
        total += f;
    std::vector<float> sorted = frames;
    std::sort(sorted.begin(), sorted.end());
    // Lows: the average frame rate over the slowest 1% / 0.1% of frames.
    auto low = [&](double fraction) {
        const size_t n = std::max<size_t>(1, size_t(sorted.size() * fraction));
        double sum = 0;
        for (size_t i = sorted.size() - n; i < sorted.size(); i++)
            sum += sorted[i];
        return 1000.0 * double(n) / sum;
    };
    fprintf(out, "Average:    %.1f fps (%zu frames in %.1f s)\n", 1000.0 * double(frames.size()) / total, frames.size(), total / 1000.0);
    fprintf(out, "1%% low:     %.1f fps\n", low(0.01));
    fprintf(out, "0.1%% low:   %.1f fps\n", low(0.001));
    fprintf(out, "Slowest:    %.1f ms\n", double(sorted.back()));
    // Pacing: how much consecutive frames differ (0 for perfectly even frames).
    double jitter = 0;
    for (size_t i = 1; i < frames.size(); i++)
        jitter += std::fabs(double(frames[i]) - double(frames[i - 1]));
    fprintf(out, "Pacing:     %.2f ms average change between frames, median frame %.2f ms\n", jitter / double(frames.size() - 1),
        double(sorted[sorted.size() / 2]));
    // Timeline: average and slowest frame per 5 s (does the first stretch run slower?).
    fprintf(out, "Timeline:  ");
    double windowMs = 0, windowMax = 0;
    size_t windowFrames = 0;
    for (float f : frames)
    {
        windowMs += f;
        windowMax = std::max(windowMax, double(f));
        windowFrames++;
        if (windowMs >= 5000.0)
        {
            fprintf(out, " %.0f fps (max %.1f ms) |", 1000.0 * double(windowFrames) / windowMs, windowMax);
            windowMs = windowMax = 0;
            windowFrames = 0;
        }
    }
    fprintf(out, "\n");
    fclose(out);
    Log("benchmark: report written to %s", path);
}

void RendererOnSwapImpl(uint64_t frameNumber, uint32_t frontBuffer);
void RendererOnSwap(uint64_t frameNumber, uint32_t frontBuffer)
{
    const auto t0 = std::chrono::steady_clock::now();
    RendererOnSwapImpl(frameNumber, frontBuffer);
    const auto now = std::chrono::steady_clock::now();
    g_perf.swapSeconds += std::chrono::duration<double>(now - t0).count();
    g_perf.frames++;
    static LiveSwitch perf("REGPU_PERF");
    static auto lastSwap = now;
    static double frameDraw = 0, lastDrawTotal = 0;
    {
        const double dt = std::chrono::duration<double>(now - lastSwap).count() * 1000.0;
        lastSwap = now;
        if (g_benchRecording.load(std::memory_order_relaxed))
        {
            std::lock_guard lock(g_benchMutex);
            g_benchFrames.push_back(float(dt));
        }
        g_perf.maxFrame = std::max(g_perf.maxFrame, dt);
        if (dt > 20.0)
            g_perf.slowFrames++;
        const double drawNow = g_perf.drawSeconds * 1000.0;
        static double drawBefore = 0;
        const double frameDrawMs = drawNow >= drawBefore ? drawNow - drawBefore : drawNow;
        drawBefore = drawNow;
        static const double slowMs = getenv("REGPU_SLOW_MS") ? atof(getenv("REGPU_SLOW_MS")) : 25.0; // slow-frame log threshold
        if (perf && dt > slowMs)
        {
            if (regpu::GetHost().slowFrame)
                regpu::GetHost().slowFrame(dt + 2.0);
        }
        if (perf && dt > slowMs)
            Log("perf: slow frame %llu: %.1f ms | draw %.1f ms (worst draw %.1f; shaders %.1f indices %.1f pipeline %.1f constants %.1f "
                "textures %.1f submit %.1f), waited %.1f ms on %u builds, %u pipelines started, %u resources created",
                (unsigned long long)frameNumber, dt, frameDrawMs, g_frameEvents.worstDrawMs, g_frameEvents.section[0], g_frameEvents.section[1],
                g_frameEvents.section[2], g_frameEvents.section[3], g_frameEvents.section[4], g_frameEvents.section[5], g_frameEvents.waitMs,
                g_frameEvents.waits, g_frameEvents.pipelines, g_frameEvents.created);
        g_frameEvents = {};
    }
    const double window = std::chrono::duration<double>(now - g_perf.windowStart).count();
    if (window >= 1.0)
    {
        if (perf && g_perf.frames)
            Log("perf: %.1f fps, %.2f ms/frame | renderer draw %.2f ms, swap %.2f ms per frame | %.0f draws/frame", g_perf.frames / window,
                window * 1000.0 / g_perf.frames, g_perf.drawSeconds * 1000.0 / g_perf.frames, g_perf.swapSeconds * 1000.0 / g_perf.frames,
                double(g_perf.draws) / g_perf.frames);
        if (perf && g_perf.frames)
            Log("perf:   shaders %.2f | indices %.2f | pipeline %.2f | constants %.2f | textures %.2f | submit %.2f ms/frame",
                g_perf.section[0] * 1000.0 / g_perf.frames, g_perf.section[1] * 1000.0 / g_perf.frames, g_perf.section[2] * 1000.0 / g_perf.frames,
                g_perf.section[3] * 1000.0 / g_perf.frames, g_perf.section[4] * 1000.0 / g_perf.frames, g_perf.section[5] * 1000.0 / g_perf.frames);
        if (perf && g_perf.frames)
        {
            Log("perf:   constants: lists/floats %.2f | bools %.2f | fetches %.2f | viewport %.2f | indices %.2f | rest %.2f ms/frame",
                g_sub[0] * 1000.0 / g_perf.frames, g_sub[1] * 1000.0 / g_perf.frames, g_sub[2] * 1000.0 / g_perf.frames, g_sub[3] * 1000.0 / g_perf.frames,
                g_sub[4] * 1000.0 / g_perf.frames, g_sub[5] * 1000.0 / g_perf.frames);
            for (double& v : g_sub)
                v = 0;
        }
        if (perf)
            Log("perf:   cpu%s | slowest frame %.1f ms, %llu over 20 ms", RegpuGuestCpuUsage(window).c_str(), g_perf.maxFrame,
                (unsigned long long)g_perf.slowFrames);
        if (perf && g_perf.frames)
            Log("perf:   waiting on host GPU %.2f ms/frame | GPU time %.2f ms/frame (worst %.1f)", g_gpuWaitSeconds * 1000.0 / g_perf.frames,
                g_gpuFrames ? g_gpuFrameMs / g_gpuFrames : 0.0, g_gpuFrameMaxMs);
        g_gpuFrameMs = g_gpuFrameMaxMs = 0;
        g_gpuFrames = 0;
        if (perf && g_perf.frames)
            Log("perf:   descriptor tables built %.0f, reused %.0f per frame", double(g_perf.tablesBuilt) / g_perf.frames, double(g_perf.tablesReused) / g_perf.frames);
        if (perf && g_perf.frames)
            Log("perf:   uploads: %.1f MB hashed, %.1f MB copied (%.0f copies, %.0f reused, %.0f of them by samples) per frame",
                double(g_uploadStats.hashed.exchange(0)) / g_perf.frames / 1048576.0, double(g_uploadStats.copied.exchange(0)) / g_perf.frames / 1048576.0,
                double(g_uploadStats.copies.exchange(0)) / g_perf.frames, double(g_uploadStats.hits.exchange(0)) / g_perf.frames,
                double(g_uploadStats.sampledHits.exchange(0)) / g_perf.frames);
        if (perf && g_perf.frames)
        {
            // Order of RecordedList::Op.
            static const char* const names[] = { "barrier", "copy", "rootsig", "table", "cbv", "root32", "targets", "pipeline", "viewport",
                "scissor", "stencilref", "blendfactor", "topology", "heaps", "draw", "endquery", "resolvequery", "clearrtv", "cleardsv",
                "creatertv", "createdsv", "drawindexed", "indexbuffer", "copybuffer", "csrootsig", "cstable", "csroot32", "dispatch", "beginquery" };
            std::string s;
            for (size_t i = 0; i < std::size(names); i++)
                if (const uint64_t n = g_recordedOps[i].exchange(0); n >= g_perf.frames)
                    s += " " + std::string(names[i]) + " " + std::to_string(n / g_perf.frames);
            Log("perf:   recorded per frame:%s | %llu barriers", s.c_str(), (unsigned long long)(g_recordedBarriers.exchange(0) / g_perf.frames));
            // REGPU_TRACE_BARRIERS=1: barriers per frame by call site (module offset, for llvm-symbolizer) and
            // transition (before -> after state, or UAV/aliasing).
            static const bool traceBarriers = getenv("REGPU_TRACE_BARRIERS") != nullptr;
            static std::mutex barrierMutex;
            static std::map<std::tuple<uintptr_t, uint32_t, uint32_t>, uint64_t> barrierSites;
            if (traceBarriers && !g_barrierSites)
                g_barrierSites = [](void* caller, UINT n, const D3D12_RESOURCE_BARRIER* b) {
                    HMODULE self = nullptr;
                    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                        reinterpret_cast<LPCWSTR>(&InitDepthBlit), &self);
                    const uintptr_t at = uintptr_t(caller) - uintptr_t(self);
                    std::lock_guard lock(barrierMutex);
                    for (UINT i = 0; i < n; i++)
                    {
                        const bool transition = b[i].Type == D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
                        barrierSites[{ at, transition ? uint32_t(b[i].Transition.StateBefore) : 0xFFFF0000u | b[i].Type,
                            transition ? uint32_t(b[i].Transition.StateAfter) : 0 }]++;
                    }
                };
            if (traceBarriers)
            {
                std::lock_guard lock(barrierMutex);
                std::vector<std::pair<uint64_t, std::tuple<uintptr_t, uint32_t, uint32_t>>> v;
                for (auto& [k, n] : barrierSites)
                    v.push_back({ n, k });
                std::sort(v.rbegin(), v.rend());
                for (size_t i = 0; i < v.size() && i < 12; i++)
                    Log("perf:   barriers %llu/frame at +%llx: %x -> %x", (unsigned long long)(v[i].first / g_perf.frames),
                        (unsigned long long)std::get<0>(v[i].second), std::get<1>(v[i].second), std::get<2>(v[i].second));
                barrierSites.clear();
            }
        }
        if (perf && !g_created.empty())
        {
            std::string s;
            for (auto& [what, n] : g_created)
                s += " " + what + ":" + std::to_string(n);
            Log("perf:   resources created this second:%s", s.c_str());
        }
        if (perf && g_pipelinesCreated)
            Log("perf:   pipelines started %u", g_pipelinesCreated);
        if (perf && (g_scanPrefound || g_scanMissed))
            Log("perf:   new shaders at draw time: %u already found by scan, %u not", g_scanPrefound, g_scanMissed);
        g_scanPrefound = g_scanMissed = 0;
        if (perf && g_skippedPipeline)
            Log("perf:   draws skipped waiting for pipelines: %u", g_skippedPipeline);
        g_skippedPipeline = 0;
        g_pipelineMs = 0;
        g_pipelinesCreated = 0;
        g_pipelineLibrary.MaybeSave();
        g_created.clear();
        g_gpuWaitSeconds = 0;
        g_perf = Perf{};
    }
}

// ---- Precompile (test build): before the game goes on, compile every stored shader and
// build every stored pipeline, with the overlay up. The stored set is whatever earlier
// runs recorded (shadercache/*.ucode and pipelines.manifest), so the more the game has
// been played, the more is covered. REGPU_NO_PRECOMPILE=1 turns it off.
bool g_precompileDone = false;

// The title's shipped pipeline list (Host::pipelineSeed): those not built yet are built once, on the
// first boot after the static shaders, so a first race doesn't stop for each new one. Only those whose
// shaders are compiled by then can be; the rest wait in g_seedLater for the game to make their shader.
void BuildSeedPipelines()
{
    const regpu::Host& host = regpu::GetHost();
    if (!host.pipelineSeed || getenv("REGPU_NO_SEED_PIPELINES"))
        return;
    const auto start = std::chrono::steady_clock::now();
    std::vector<std::pair<PipelineKey, std::pair<const CompiledShader*, const CompiledShader*>>> todo;
    size_t listed = 0, known = 0, missing = 0;
    std::unordered_set<uint64_t> missingShaders, seen;
    for (const char* p = host.pipelineSeed(); p && *p;)
    {
        const char* end = strchr(p, '\n');
        const std::string line(p, end ? end : p + strlen(p));
        p = end ? end + 1 : p + line.size();
        PipelineKey key{};
        unsigned long long vs, ps;
        if (sscanf(line.c_str(), "P %llx %llx %x %x %x %x %x %x", &vs, &ps, &key.blend, &key.depth, &key.stencilMaskMode, &key.flags,
                   &key.biasScale, &key.biasOffset) < 6)
            continue;
        key.vs = vs;
        key.ps = ps;
        key.biasOffset = RoundedBias(key.biasOffset);
        if (!seen.insert(PipelineKeyHash{}(key)).second)
            continue;
        listed++;
        if (g_manifest.known.count(key) || g_r.pipelines.count(key))
        {
            known++;
            continue;
        }
        const auto vsIt = g_r.shaders.find(key.vs), psIt = g_r.shaders.find(key.ps);
        if (vsIt == g_r.shaders.end() || psIt == g_r.shaders.end())
        {
            missing++;
            missingShaders.insert(vsIt == g_r.shaders.end() ? key.vs : key.ps);
            g_seedLater.push_back(key);
            continue;
        }
        todo.push_back({ key, { vsIt->second.get(), psIt->second.get() } });
    }
    Log("seed pipelines: %zu listed, %zu already built, %zu to build, %zu need %zu shaders the game makes at run time", listed, known,
        todo.size(), missing, missingShaders.size());
    if (todo.empty())
        return;
    std::vector<std::shared_ptr<PipelineEntry>> building;
    std::vector<uint8_t> frame;
    size_t next = 0, failed = 0;
    auto lastShown = std::chrono::steady_clock::time_point{};
    for (;;)
    {
        // A few at a time: the pool works through them while the screen updates.
        while (next < todo.size() &&
               building.size() - size_t(std::count_if(building.begin(), building.end(), [](const auto& e) { return e->state.load() != 0; })) < 32)
        {
            const auto& [key, shaders] = todo[next++];
            const auto [vs, ps] = shaders;
            while (vs->state.load() == CompiledShader::Pending || ps->state.load() == CompiledShader::Pending)
                Sleep(1);
            if (vs->IsReady() && ps->IsReady())
                building.push_back(EnsurePipeline(*vs, *ps, key));
            else
                failed++;
        }
        const size_t done = size_t(std::count_if(building.begin(), building.end(), [](const auto& e) { return e->state.load() != 0; }));
        if (next >= todo.size() && done == building.size())
            break;
        const auto now = std::chrono::steady_clock::now();
        if (now - lastShown > std::chrono::milliseconds(100))
        {
            lastShown = now;
            if (host.bootScreen && host.bootScreen(L"BUILDING PIPELINES", uint32_t(done + failed), uint32_t(todo.size()), frame))
                BootPresent(frame);
            else
            {
                g_hold.active = true;
                HoldPresent();
            }
        }
        Sleep(5);
    }
    g_hold.active = false;
    g_lastLongHold = std::chrono::steady_clock::now();
    Log("seed pipelines: %zu built in %.1f s (%zu skipped: a shader didn't compile)", building.size(),
        std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(), failed);
}

void PrecompileAll()
{
    if (g_precompileDone)
        return;
    g_precompileDone = true;
    // From here on the game runs: speculative finds wait for a draw that needs them.
    struct ParkAfter
    {
        ~ParkAfter() { g_compiler.park = true; }
    } parkAfter;
    if (getenv("REGPU_NO_PRECOMPILE"))
        return;
    const auto start = std::chrono::steady_clock::now();
    // Only the shaders stored pipelines use: older caches also hold scan finds that never drew.
    std::vector<PipelineKey> keys = g_manifest.pending;
    std::vector<std::pair<bool, uint64_t>> shaders;
    {
        std::set<std::pair<bool, uint64_t>> used;
        for (const PipelineKey& k : keys)
            for (const auto s : { std::pair{ false, k.vs }, std::pair{ true, k.ps } })
                if (used.insert(s).second)
                    shaders.push_back(s);
    }
    g_stats.warmTotal = uint32_t(keys.size());
    g_stats.warmDone = 0;
    Log("precompile: %zu shaders and %zu pipelines stored", shaders.size(), keys.size());
    size_t nextShader = 0, nextKey = 0;
    size_t shadersInstalled = 0, pipelinesStarted = 0;
    auto lastPresent = std::chrono::steady_clock::time_point{};
    for (;;)
    {
        // Phase 1: load and translate shaders a batch at a time (compiles run on worker threads).
        for (int batch = 0; batch < 64 && nextShader < shaders.size(); batch++, nextShader++)
        {
            const auto [pixel, hash] = shaders[nextShader];
            if (g_r.shaders.count(hash))
                continue;
            std::vector<uint32_t> code;
            if (Manifest::LoadShader(pixel, hash, code))
            {
                GetShader(hash, code, pixel);
                shadersInstalled++;
            }
        }
        // Phase 2: start pipelines whose shaders are ready.
        for (int batch = 0; batch < 256 && nextKey < keys.size(); batch++)
        {
            const PipelineKey key = keys[nextKey];
            const CompiledShader* vs = nullptr;
            const CompiledShader* ps = nullptr;
            if (auto f = g_r.shaders.find(key.vs); f != g_r.shaders.end())
                vs = f->second.get();
            if (auto f = g_r.shaders.find(key.ps); f != g_r.shaders.end())
                ps = f->second.get();
            if (!vs || !ps)
            {
                // Shader not loaded yet: load it now (translation queues the compile).
                std::vector<uint32_t> code;
                if (!vs && Manifest::LoadShader(false, key.vs, code))
                    vs = &GetShader(key.vs, code, false);
                code.clear();
                if (!ps && Manifest::LoadShader(true, key.ps, code))
                    ps = &GetShader(key.ps, code, true);
            }
            // A shader not stored (a static one from Host::staticShaders, installed after this): skip the
            // pipeline. Waiting for it, as before, waited for good (FH1 on the FX: a black screen).
            if (vs && ps && (vs->state.load() == CompiledShader::Pending || ps->state.load() == CompiledShader::Pending))
                break; // wait for this one to finish compiling before going on
            if (vs && ps && vs->state.load() == CompiledShader::Ready && ps->state.load() == CompiledShader::Ready)
            {
                if (!g_r.pipelines.count(key))
                {
                    EnsurePipeline(*vs, *ps, key);
                    pipelinesStarted++;
                }
            }
            nextKey++;
            g_stats.warmDone++;
        }
        // Done when every shader is loaded and every pipeline is built.
        size_t building = 0;
        for (auto& [k, e] : g_r.pipelines)
            if (e->state.load() == 0)
                building++;
        const bool allSent = nextShader >= shaders.size() && nextKey >= keys.size();
        if (allSent && g_compiler.jobs.load() == 0 && building == 0)
            break;
        const auto now = std::chrono::steady_clock::now();
        if (now - lastPresent > std::chrono::milliseconds(33))
        {
            lastPresent = now;
            // Past a moment the stored set's shaders are compiling (a first boot after a translator
            // change rebuilds them all, minutes on a large cache): the host's boot screen with this
            // phase's progress, not a held black frame.
            const regpu::Host& host = regpu::GetHost();
            std::vector<uint8_t> frame;
            if (now - start > std::chrono::seconds(2) && host.bootScreen &&
                host.bootScreen(L"COMPILING SHADERS", uint32_t(nextKey), uint32_t(keys.size()), frame))
                BootPresent(frame);
            else
            {
                g_hold.active = true;
                HoldPresent();
            }
        }
        Sleep(1);
    }
    g_hold.active = false;
    // A warm start never has a long loading hold, so without this settled skipping
    // never arms and each first-time effect stalls for its compile.
    if (!keys.empty())
        g_lastLongHold = std::chrono::steady_clock::now();
    Log("precompile: done in %.1f s (%zu shaders loaded, %zu pipelines started)",
        std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(), shadersInstalled, pipelinesStarted);
    // The shipped pipeline list last, once the static shaders below are compiled (whichever way this returns).
    struct SeedAfter
    {
        ~SeedAfter() { BuildSeedPipelines(); }
    } seedAfter;

    // Every shader in the game data (Host::staticShaders) goes to the compile queue as well. Compiles
    // run on the background workers and the game doesn't wait for them; cached bytecode loads
    // straight away, so only the first boot pays (REGPU_NO_STATIC_SHADERS=1 skips this).
    const regpu::Host& host = regpu::GetHost();
    if (getenv("REGPU_NO_STATIC_SHADERS") || !host.staticShaders)
        return;
    auto BootScreenCompose = [&](const wchar_t* title, uint32_t done, uint32_t total, std::vector<uint8_t>& rgba) {
        return host.bootScreen && host.bootScreen(title, done, total, rgba);
    };
    const auto staticStart = std::chrono::steady_clock::now();
    size_t queued = 0;
    std::vector<const CompiledShader*> compiling;
    // The first boot's search of the game files takes a few seconds: say so on screen.
    std::vector<uint8_t> bootFrame;
    if (!(host.staticShadersSaved && host.staticShadersSaved()) && BootScreenCompose(L"PREPARING SHADERS", 0, 0, bootFrame))
        BootPresent(bootFrame);
    for (const regpu::ScannedShader& s : host.staticShaders())
    {
        // The scanner accepts a candidate on control flow alone, and a few of what it finds in
        // the mesh files is not a shader (ucode::Plausible). Translating one fails on an opcode
        // the ISA doesn't define, after paying a translation on every boot.
        if (!ucode::Plausible(ucode::Parse(s.code.data(), uint32_t(s.code.size()), s.pixel)))
            continue;
        const uint64_t hash = Fnv(s.code.data(), s.code.size() * 4) ^ (s.pixel ? 1 : 0);
        if (!s.pixel)
            VertexPatch::AddShipped(hash, s.code);
        if (g_r.shaders.count(hash))
            continue;
        g_scanKnown.insert(hash);
        const CompiledShader& cs = GetShader(hash, s.code, s.pixel, true);
        if (cs.state.load() == CompiledShader::Pending)
            compiling.push_back(&cs);
        queued++;
    }
    // The vertex shaders the game makes from them (VertexPatch), as the seed lists them.
    size_t variants = 0, variantLines = 0, fitting = 0;
    if (host.pipelineSeed && !getenv("REGPU_NO_SEED_PIPELINES"))
        for (const char* p = host.pipelineSeed(); p && *p;)
        {
            const char* end = strchr(p, '\n');
            const std::string line(p, end ? end : p + strlen(p));
            p = end ? end + 1 : p + line.size();
            if (line.rfind("V ", 0) != 0)
                continue;
            variantLines++;
            uint64_t hash;
            std::vector<uint32_t> code;
            if (!VertexPatch::Rebuild(line.c_str(), hash, code))
                continue;
            fitting++;
            if (g_r.shaders.count(hash))
                continue;
            const CompiledShader& cs = GetShader(hash, code, false, true);
            if (cs.state.load() == CompiledShader::Pending)
                compiling.push_back(&cs);
            queued++;
            variants++;
        }
    if (variantLines)
        Log("static shaders: vertex shader variants in the seed: %zu, %zu rebuilt from the shipped shaders, %zu new to compile", variantLines, fitting,
            variants);
    // Compiling them while the game runs halves the frame rate for minutes, so the first
    // boot waits here with the overlay up; later boots find the bytecode cached and
    // queue nothing.
    const int toCompile = int(compiling.size());
    Log("static shaders: %zu new, %d to compile (prepared in %.1f s)", queued, toCompile,
        std::chrono::duration<double>(std::chrono::steady_clock::now() - staticStart).count());
    if (!toCompile)
        return;
    g_stats.staticTotal = uint32_t(toCompile);
    g_stats.staticDone = 0;
    const auto compileStart = std::chrono::steady_clock::now();
    auto lastShown = std::chrono::steady_clock::time_point{}, lastScan = std::chrono::steady_clock::now();
    for (size_t next = 0;;)
    {
        while (next < compiling.size() && compiling[next]->state.load() != CompiledShader::Pending)
            next++;
        if (next == compiling.size())
            break;
        const auto now = std::chrono::steady_clock::now();
        // The game keeps running meanwhile and builds the shaders it makes at runtime (the
        // title's among them): compile those in the same wait.
        if (now - lastScan > std::chrono::milliseconds(1500))
        {
            lastScan = now;
            ShaderScanNow(2);
            const size_t before = compiling.size();
            DrainScan(0, &compiling);
            g_stats.staticTotal += uint32_t(compiling.size() - before);
        }
        if (now - lastShown > std::chrono::milliseconds(100))
        {
            lastShown = now;
            g_stats.staticDone = uint32_t(std::count_if(compiling.begin(), compiling.end(),
                [](const CompiledShader* cs) { return cs->state.load() != CompiledShader::Pending; }));
            if (BootScreenCompose(L"COMPILING SHADERS", g_stats.staticDone.load(), g_stats.staticTotal.load(), bootFrame))
                BootPresent(bootFrame);
            else
            {
                g_hold.active = true;
                HoldPresent();
            }
        }
        Sleep(5);
    }
    g_hold.active = false;
    g_stats.staticTotal = 0;
    g_lastLongHold = std::chrono::steady_clock::now();
    Log("static shaders: %zu compiled in %.1f s (%zu found in memory meanwhile)", compiling.size(), std::chrono::duration<double>(std::chrono::steady_clock::now() - compileStart).count(), compiling.size() - size_t(toCompile));
}

void RendererOnSwapImpl(uint64_t frameNumber, uint32_t frontBuffer)
{
    if (regpu::GetHost().onSwap)
        regpu::GetHost().onSwap(g_r.frame);
    if (!g_r.ok)
        return;
    PrecompileAll();
    WarmupStep();
    BeginFrameIfNeeded();
    // A frame that rendered small but never resolved a scene (a menu): upscale what it shows
    // and stay at full size for a while (menus are drawn sharp at full size).
    if (g_scenePhase && !g_upscale.switchedThisFrame)
    {
        UpscaleResolved(frontBuffer);
        g_sceneScale = 1.0f;
        g_scenePhase = false;
        g_upscale.retryFrame = g_r.frame + 120;
        g_upscale.reset = true; // the next scene has no usable history
    }
    // Show the front buffer the title resolved to; fall back to the render
    // target if it was never resolved.
    ID3D12Resource* image = C().color.Get();
    D3D12_RESOURCE_STATES imageState = D3D12_RESOURCE_STATE_COPY_SOURCE;
    auto it = g_r.resolved.find(frontBuffer);
    const bool fromResolve = it != g_r.resolved.end() && it->second.width == kWidth && it->second.height == kHeight &&
        it->second.format == DXGI_FORMAT_R8G8B8A8_UNORM;
    ID3D12Resource* graded = nullptr; // the front buffer through the display's gamma table
    if (fromResolve)
    {
        image = it->second.resource.Get();
        graded = GradeFrontBuffer(image, it->second.guestFormat == 7);
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = image;
        b.Transition.Subresource = 0;
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
        C().list->ResourceBarrier(1, &b);
    }
    // The render target is half float; without an 8-bit resolve there is
    // nothing presentable this frame.
    if (fromResolve)
        g_hold.image = it->second.resource;
    ID3D12Resource* overlay = fromResolve ? PrepareOverlay() : nullptr;
    ID3D12Resource* shown = graded ? graded : image;
    if (fromResolve)
        if (ID3D12Resource* enhanced = EnhanceFrontBuffer(shown))
            shown = enhanced;
    const bool presenting = fromResolve && PresenterRecordCopy(C().list->Real(), shown, overlay, kOverlayW, kOverlayH);
    {
        const uint64_t traceFrame = TraceFrame();
        if (traceFrame && g_r.frame + 1 == traceFrame)
            Log("swap: front buffer %08X, %s", frontBuffer, fromResolve ? "resolved" : "not resolved: showing the render target");
    }

    static uint64_t dumpFrame = getenv("REGPU_DUMP_FRAME") ? strtoull(getenv("REGPU_DUMP_FRAME"), nullptr, 10) : 0;
    // A capture asked for some other way (regpuctl capture: RendererRequestCapture) dumps that frame too.
    if (const uint64_t hot = g_hotkeyFrame.load(std::memory_order_relaxed); hot > dumpFrame && hot > g_r.frame)
        dumpFrame = hot;
    {
        // F12 in the game window: capture the next frame (frame.png, the resolves in out\res,
        // and the frame's draws in out/play.log) to report rendering bugs.
        static bool wasDown = false;
        const bool down = (GetAsyncKeyState(VK_F12) & 0x8000) != 0;
        DWORD pid = 0;
        GetWindowThreadProcessId(GetForegroundWindow(), &pid);
        if (down && !wasDown && pid == GetCurrentProcessId())
        {
            g_hotkeyFrame = g_r.frame + 2;
            dumpFrame = g_r.frame + 2;
            Log("capture: F12 pressed, tracing frame %llu", (unsigned long long)dumpFrame);
        }
        wasDown = down;
    }
    // REGPU_CAPTURE_TALL=1: the same capture as F12, once a pass taller than the screen was drawn.
    {
        static LiveSwitch captureTall("REGPU_CAPTURE_TALL");
        static bool capturedTall = false;
        if (captureTall && !capturedTall && g_tallSeen)
        {
            capturedTall = true;
            dumpFrame = g_r.frame + 1; // the frame the tall pass was drawn in (already being traced)
            Log("capture: tall pass seen, dumping frame %llu", (unsigned long long)dumpFrame);
        }
    }
    // REGPU_CAPTURE_AT=<seconds>: the same capture as F12, that long after the start (for replays).
    {
        static const double captureAt = getenv("REGPU_CAPTURE_AT") ? atof(getenv("REGPU_CAPTURE_AT")) : 0;
        static const auto captureStart = std::chrono::steady_clock::now();
        static bool captured = false;
        if (captureAt > 0 && !captured && std::chrono::duration<double>(std::chrono::steady_clock::now() - captureStart).count() >= captureAt)
        {
            captured = true;
            g_hotkeyFrame = g_r.frame + 2;
            dumpFrame = g_r.frame + 2;
            Log("capture: REGPU_CAPTURE_AT reached, tracing frame %llu", (unsigned long long)dumpFrame);
        }
    }
    ++g_r.frame;
    prehash::StartFrame();
    DebugOnSwap(g_r.frame);
    if (int st = g_drawStats.load(); st) // drawstats: arm at one swap, stop at the next
        g_drawStats = st == 1 ? 2 : 0;
    // The debug server's "shot": this frame (when it has a picture), to the file it names.
    std::string shotPath;
    const bool shot = fromResolve && DebugShotPending(shotPath);
    if (shot)
        dumpFrame = g_r.frame;
    // REGPU_DUMP_AT=<seconds>: dump the first resolved frame after that long (comparable
    // screenshots whatever the frame rate).
    {
        static const double dumpAt = getenv("REGPU_DUMP_AT") ? atof(getenv("REGPU_DUMP_AT")) : 0;
        static const auto start = std::chrono::steady_clock::now();
        if (dumpAt > 0 && !dumpFrame && fromResolve && std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count() >= dumpAt)
            dumpFrame = g_r.frame;
    }
    g_tile = TileState{};
    static LiveSwitch dumpSecond("REGPU_SKIP_VS");
    static const uint64_t dumpEvery = getenv("REGPU_DUMP_EVERY") ? strtoull(getenv("REGPU_DUMP_EVERY"), nullptr, 10) : 0;
    static uint32_t sweepCountForDump = [] { uint32_t a = 0, b = 0, c = 0; if (const char* e = getenv("REGPU_SWEEP")) sscanf(e, "%u,%u,%u", &a, &b, &c); return c; }();
    const bool sweepDump = fromResolve && sweepCountForDump && dumpFrame && g_r.frame >= dumpFrame && g_r.frame < dumpFrame + sweepCountForDump;
    const bool dump = sweepDump || (fromResolve && ((dumpFrame && (g_r.frame == dumpFrame || (dumpSecond && g_r.frame == dumpFrame + 1))) || (dumpEvery && g_r.frame % dumpEvery == 0)));
    if (dump)
    {
        D3D12_TEXTURE_COPY_LOCATION dst{};
        dst.pResource = g_r.readback.Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        dst.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, OutputWidth(), OutputHeight(), 1, (OutputWidth() * 4 + 255) & ~255u };
        D3D12_TEXTURE_COPY_LOCATION src{};
        src.pResource = shown;
        src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    }
    // REGPU_DUMP_RESOLVES=1 (with REGPU_DUMP_FRAME): also write every resolved texture to out/res.
    static std::vector<ResolveDump> resolveDumps;
    resolveDumps.clear();
    if (dump && (getenv("REGPU_DUMP_RESOLVES") || (g_hotkeyFrame.load() && g_r.frame == dumpFrame)))
    {
        auto add = [&](uint32_t base, ResolvedTexture& r, int variant) {
            if (!r.resource)
                return;
            ResolveDump d;
            d.base = base;
            d.variant = variant;
            d.width = r.hostWidth; // the texture's own size
            d.height = r.hostHeight;
            d.format = r.format;
            const UINT bpp = r.format == DXGI_FORMAT_R16G16B16A16_FLOAT ? 8 : 4;
            d.pitch = (d.width * bpp + 255) & ~255u;
            d.buffer = CreateBuffer(UINT64(d.pitch) * d.height, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
            D3D12_RESOURCE_BARRIER bar{};
            bar.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
            bar.Transition.pResource = r.resource.Get();
            bar.Transition.Subresource = 0;
            bar.Transition.StateBefore = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
            bar.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
            C().list->ResourceBarrier(1, &bar);
            D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
            dst.pResource = d.buffer.Get();
            dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
            dst.PlacedFootprint.Footprint = { r.format, d.width, d.height, 1, d.pitch };
            src.pResource = r.resource.Get();
            src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
            C().list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
            std::swap(bar.Transition.StateBefore, bar.Transition.StateAfter);
            C().list->ResourceBarrier(1, &bar);
            resolveDumps.push_back(std::move(d));
        };
        for (auto& [base, r] : g_r.resolved)
        {
            add(base, r, 0);
            int v = 1;
            for (ResolvedTexture& spare : r.spares)
                add(base, spare, v++);
        }
    }
    if (fromResolve)
    {
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = image;
        b.Transition.Subresource = 0;
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE | D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE;
        C().list->ResourceBarrier(1, &b);
        if (graded)
        {
            b.Transition.pResource = graded;
            b.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
            b.Transition.StateAfter = D3D12_RESOURCE_STATE_UNORDERED_ACCESS;
            C().list->ResourceBarrier(1, &b);
        }
    }

    FlushIndexCopies(); // this frame's index uploads into the arena, after its draws
    if (g_gpuPasses && g_passQueries && !g_passMarks[UINT(&Frame() - g_r.frames)].empty())
    {
        const UINT frameIndex = UINT(&Frame() - g_r.frames);
        PassMark("frame end %u%u", 0, 0);
        const UINT n = UINT(g_passMarks[frameIndex].size());
        C().list->ResolveQueryData(g_passQueries.Get(), D3D12_QUERY_TYPE_TIMESTAMP, frameIndex * kPassMarks, n, g_passReadback.Get(),
            UINT64(frameIndex) * kPassMarks * 8);
    }
    if (g_timestamps)
    {
        const UINT frameIndex = UINT(&Frame() - g_r.frames);
        C().list->EndQuery(g_timestamps.Get(), D3D12_QUERY_TYPE_TIMESTAMP, frameIndex * 2 + 1);
        C().list->ResolveQueryData(g_timestamps.Get(), D3D12_QUERY_TYPE_TIMESTAMP, frameIndex * 2, 2, g_timestampReadback.Get(), UINT64(frameIndex) * 16);
        Frame().timed = true;
    }
    // A query must end in the list it began in: one still open at the swap (begun before it,
    // ended after) made Close fail and removed the device. Its end then answers fully visible.
    if (g_occlusionOpen)
        EndOcclusionQuery();
    C().list->Close();
    ID3D12CommandList* lists[] = { C().list->Real() };
    SubmitLists(1, lists);
    if (presenting)
        PresenterPresent();
    Frame().fence = g_r.nextFence++;
    g_r.queue->Signal(g_r.fence.Get(), Frame().fence);
    g_r.listOpen = false;

    if (dump)
    {
        WaitFenceValue(Frame().fence, INFINITE);
        for (ResolveDump& d : resolveDumps)
        {
            uint8_t* mapped = nullptr;
            d.buffer->Map(0, nullptr, reinterpret_cast<void**>(&mapped));
            std::vector<uint8_t> out(size_t(d.width) * d.height * 4);
            // Depth: the values between the clear values (0, 1) stretched over the grey range, so a
            // map of depths close together (a shadow map) shows its shapes.
            float lo = 1.0f, hi = 0.0f;
            if (d.format == DXGI_FORMAT_R32_FLOAT)
                for (UINT y = 0; y < d.height; y++)
                    for (UINT x = 0; x < d.width; x++)
                        if (const float f = reinterpret_cast<const float*>(mapped + size_t(y) * d.pitch)[x]; f > 0.0f && f < 1.0f)
                            lo = std::min(lo, f), hi = std::max(hi, f);
            for (UINT y = 0; y < d.height; y++)
                for (UINT x = 0; x < d.width; x++)
                {
                    uint8_t* o = &out[(size_t(y) * d.width + x) * 4];
                    if (d.format == DXGI_FORMAT_R16G16B16A16_FLOAT)
                    {
                        const uint16_t* h = reinterpret_cast<const uint16_t*>(mapped + size_t(y) * d.pitch) + x * 4;
                        for (int c = 0; c < 4; c++)
                        {
                            const uint32_t e = (h[c] >> 10) & 31, m = h[c] & 1023;
                            float f = e == 0 ? m / 1024.0f * 6.1035e-5f : e == 31 ? 65504.0f : (1.0f + m / 1024.0f) * std::ldexp(1.0f, int(e) - 15);
                            if (h[c] & 0x8000)
                                f = -f;
                            o[c] = uint8_t(std::clamp(f, 0.0f, 1.0f) * 255.0f + 0.5f);
                        }
                    }
                    else if (d.format == DXGI_FORMAT_R32_FLOAT)
                    {
                        const float f = reinterpret_cast<const float*>(mapped + size_t(y) * d.pitch)[x];
                        if (x == 0 && y == 0)
                            d.minDepth = d.maxDepth = f;
                        d.minDepth = std::min(d.minDepth, f);
                        d.maxDepth = std::max(d.maxDepth, f);
                        if (f > 0.0f && f < 1.0f)
                            d.midCount++;
                        const float g = f <= 0.0f ? 0.0f : f >= 1.0f ? 1.0f : hi > lo ? 0.1f + 0.8f * (f - lo) / (hi - lo) : 0.5f;
                        o[0] = o[1] = o[2] = uint8_t(g * 255.0f + 0.5f);
                        o[3] = 255;
                    }
                    else
                        memcpy(o, mapped + size_t(y) * d.pitch + x * 4, 4);
                    o[3] = 255;
                }
            d.buffer->Unmap(0, nullptr);
            if (d.format == DXGI_FORMAT_R32_FLOAT)
                Log("resolve dump: depth %08X %ux%u min %g max %g strictly-inside %llu (%g to %g, drawn stretched)", d.base, d.width, d.height,
                    d.minDepth, d.maxDepth, (unsigned long long)d.midCount, lo, hi);
            std::filesystem::create_directories("out/res");
            char name[96];
            snprintf(name, sizeof(name), "out/res/%08X_%ux%u_v%d.png", d.base, d.width, d.height, d.variant);
            WritePng(name, d.width, d.height, out);
        }
        resolveDumps.clear();
        uint8_t* data = nullptr;
        g_r.readback->Map(0, nullptr, reinterpret_cast<void**>(&data));
        // Written at the render size; the region probes below work on a 1280x720 copy.
        const uint32_t dw = OutputWidth(), dh = OutputHeight(), dpitch = (dw * 4 + 255) & ~255u;
        std::vector<uint8_t> full(size_t(dw) * dh * 4);
        for (uint32_t y = 0; y < dh; y++)
            memcpy(&full[size_t(y) * dw * 4], data + size_t(y) * dpitch, size_t(dw) * 4);
        g_r.readback->Unmap(0, nullptr);
        for (size_t i = 3; i < full.size(); i += 4)
            full[i] = 255;
        std::vector<uint8_t> rgba(size_t(kWidth) * kHeight * 4);
        for (uint32_t y = 0; y < kHeight; y++)
            for (uint32_t x = 0; x < kWidth; x++)
                memcpy(&rgba[(size_t(y) * kWidth + x) * 4], &full[(size_t(y) * dh / kHeight * dw + size_t(x) * dw / kWidth) * 4], 4);
        static const uint64_t every = getenv("REGPU_DUMP_EVERY") ? strtoull(getenv("REGPU_DUMP_EVERY"), nullptr, 10) : 0;
        // REGPU_REGION_HASH="x,y,w,h" (with REGPU_DUMP_EVERY): instead of a PNG, log a hash of the bright
        // pixels in that part of the output, to see how often it changes (text, animations).
        static uint32_t rx = 0, ry = 0, rw = 0, rh = 0;
        static const bool regionHash = [] {
            const char* e = getenv("REGPU_REGION_HASH");
            return e && sscanf(e, "%u,%u,%u,%u", &rx, &ry, &rw, &rh) == 4;
        }();
        if (regionHash && every && g_r.frame % every == 0)
        {
            uint64_t h = 0xCBF29CE484222325ull;
            for (uint32_t y = ry; y < ry + rh && y < dh; y++)
                for (uint32_t x = rx; x < rx + rw && x < dw; x++)
                {
                    const uint8_t* px = &full[(size_t(y) * dw + x) * 4];
                    h = (h ^ (std::max({ px[0], px[1], px[2] }) > 90 ? 1u : 0u)) * 0x100000001B3ull;
                }
            Log("region: frame %llu hash %08X", (unsigned long long)g_r.frame, uint32_t(h ^ (h >> 32)));
        }
        else if (every && g_r.frame % every == 0 && g_r.frame != dumpFrame)
        {
            char name[64];
            std::filesystem::create_directories("out/frames");
            snprintf(name, sizeof(name), "out/frames/%05llu.png", (unsigned long long)g_r.frame);
            WritePng(name, dw, dh, full);
        }
        else if (sweepDump && getenv("REGPU_SWEEP_STAT"))
        {
            // REGPU_SWEEP_STAT="x,y,w,h": log the region's mean colour instead of writing a PNG.
            uint32_t x0 = 0, y0 = 0, w = 1, h = 1;
            sscanf(getenv("REGPU_SWEEP_STAT"), "%u,%u,%u,%u", &x0, &y0, &w, &h);
            uint64_t sum[3] = {};
            for (uint32_t y = y0; y < y0 + h && y < kHeight; y++)
                for (uint32_t x = x0; x < x0 + w && x < kWidth; x++)
                    for (int c = 0; c < 3; c++)
                        sum[c] += rgba[(size_t(y) * kWidth + x) * 4 + c];
            Log("sweep %llu: %llu %llu %llu", (unsigned long long)(g_r.frame - dumpFrame), (unsigned long long)(sum[0] / (w * h)),
                (unsigned long long)(sum[1] / (w * h)), (unsigned long long)(sum[2] / (w * h)));
        }
        else if (sweepDump)
        {
            std::filesystem::create_directories("out/sweep");
            char name[64];
            snprintf(name, sizeof(name), "out/sweep/%03llu.png", (unsigned long long)(g_r.frame - dumpFrame));
            WritePng(name, dw, dh, full);
        }
        else if (shot && g_r.frame == dumpFrame)
        {
            std::error_code ec;
            if (const auto dir = std::filesystem::path(shotPath).parent_path(); !dir.empty())
                std::filesystem::create_directories(dir, ec);
            // A ".raw" path (debug "shots ... raw"): 640x360 RGBA, nearest, no encoding, so a burst
            // barely slows the game.
            if (shotPath.size() > 4 && shotPath.compare(shotPath.size() - 4, 4, ".raw") == 0)
            {
                std::vector<uint8_t> thumb(640 * 360 * 4);
                for (uint32_t y = 0; y < 360; y++)
                    for (uint32_t x = 0; x < 640; x++)
                        memcpy(&thumb[(size_t(y) * 640 + x) * 4], &full[(size_t(y) * dh / 360 * dw + size_t(x) * dw / 640) * 4], 4);
                if (FILE* fp = fopen(shotPath.c_str(), "wb"))
                {
                    fwrite(thumb.data(), 1, thumb.size(), fp);
                    fclose(fp);
                }
            }
            else
                WritePng(shotPath.c_str(), dw, dh, full);
            DebugShotDone(shotPath);
        }
        else
            WritePng(g_r.frame == dumpFrame ? "frame.png" : "frame2.png", dw, dh, full);
        Log("renderer: wrote frame.png (frame %llu: %u draws, %u skipped)", (unsigned long long)frameNumber, g_r.drawsThisFrame.load(),
            g_r.skippedThisFrame.load());
    }
    if (frameNumber % 600 == 0)
        Log("renderer: frame %llu: %u draws, %u skipped, %zu shaders, %zu pipelines, %zu textures", (unsigned long long)frameNumber,
            g_r.drawsThisFrame.load(), g_r.skippedThisFrame.load(), g_r.shaders.size(), g_r.pipelines.size(), g_r.textures.size());
    g_lastFrameDraws.store(g_r.drawsThisFrame.load(), std::memory_order_relaxed);
    g_r.drawsThisFrame = g_r.skippedThisFrame = 0;
    g_r.frameIndex = (g_r.frameIndex + 1) % kFrames;
}

bool RendererTracing()
{
    const uint64_t traceFrame = TraceFrame();
    return traceFrame && g_r.frame + 1 == traceFrame;
}

// The camera aspect the game is given (hooks/camera_aspect.cpp): 16/9 widened by the view
// extent, so its projection and frustum culling cover the wider view.
float GameAspect()
{
    return 16.0f / 9.0f * ViewExtent();
}

void RendererSetGammaEntry(uint32_t index, uint32_t rgb10)
{
    if (index < 256)
    {
        g_gammaTable[index].store(rgb10, std::memory_order_relaxed);
        g_gammaSet.store(true, std::memory_order_relaxed);
    }
}

void RendererSetGammaPwl(uint32_t index, uint32_t component, uint32_t data)
{
    if (index < 128 && component < 3)
    {
        g_gammaPwl[index][component].store(data, std::memory_order_relaxed);
        g_gammaPwlSet.store(true, std::memory_order_relaxed);
    }
}

// The debug server's "capture": what F12 does.
// The next whole frame, as F12 does: frame + 1 is the frame already being drawn, whose trace
// started part-way through (the first tiles of a tiled pass were missing).
void RendererRequestCapture() { g_hotkeyFrame = g_r.frame + 2; }

// The debug server's "ps HASH [EXPR]": an empty expression drops the edit. Returns the alias (0 if dropped).
uint64_t RendererEditPixelShader(uint64_t hash, const std::string& expr)
{
    static std::atomic<uint64_t> serial{ 0 };
    std::lock_guard lock(g_psEditMutex);
    if (auto it = g_psEditAlias.find(hash); it != g_psEditAlias.end())
    {
        g_psEditExpr.erase(it->second);
        g_psEditAlias.erase(it);
    }
    uint64_t alias = 0;
    if (!expr.empty())
    {
        alias = hash ^ (0x9E3779B97F4A7C15ull * ++serial) ^ 0xD3B6000000000000ull;
        g_psEditAlias[hash] = alias;
        g_psEditExpr[alias] = expr;
        g_psEditEver.insert(alias);
    }
    g_psEdits = !g_psEditAlias.empty();
    return alias;
}

// The debug server's "hlsl HASH": the shader's translated source ("" if it hasn't been seen).
std::string RendererShaderHlsl(uint64_t hash)
{
    std::lock_guard lock(g_shaderMutex);
    auto it = g_r.shaders.find(hash);
    if (it == g_r.shaders.end())
        return {};
    const CompiledShader* cs = it->second.get();
    return cs->hlslSource ? cs->hlslSource->info.hlsl : cs->info.hlsl;
}

// Whether a live edit's alias is drawing: its shader compiled and a pipeline with it built.
bool RendererPsEditReady(uint64_t alias)
{
    std::lock_guard lock(g_shaderMutex);
    auto it = g_r.shaders.find(alias);
    if (it == g_r.shaders.end() || !it->second->IsReady())
        return false;
    for (const auto& [key, entry] : g_r.pipelines)
        if ((key.ps == alias || key.vs == alias) && entry->state.load(std::memory_order_acquire) == 1)
            return true;
    return false;
}

// drawstats: counts the next whole frame's draws by shader pair (blocks until it's done).
std::string RendererDrawStats()
{
    {
        std::lock_guard lock(g_drawStatsMutex);
        g_drawStatsCounts.clear();
    }
    g_drawStats = 1;
    for (int i = 0; i < 400 && g_drawStats.load() != 0; i++)
        Sleep(5);
    std::lock_guard lock(g_drawStatsMutex);
    std::string out;
    char buf[96];
    for (const auto& [k, n] : g_drawStatsCounts)
    {
        snprintf(buf, sizeof(buf), "%s{\"vs\":\"%016llx\",\"ps\":\"%016llx\",\"draws\":%u}", out.empty() ? "" : ",", (unsigned long long)k.first,
            (unsigned long long)k.second, n);
        out += buf;
    }
    return out;
}

void regpu::SetGameWidescreen(bool on) { g_gameWidescreen.store(on, std::memory_order_relaxed); }
