#pragma once
// ReGPU: the Xbox 360 GPU (Xenos) for XenonRecomp ports, on Direct3D 12.
//
// The game fills in Config and Host before calling Start, and implements nothing else of the GPU:
// ReGPU provides the console's Vd* video exports (PPC_FUNC __imp__Vd...), runs the command processor
// and the graphics interrupt, translates shaders, emulates EDRAM and resolves, and presents the frames.
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

struct PPCContext; // XenonRecomp (ppc_context.h)

namespace regpu
{
// Display and quality settings. Read at start and, for most fields, again each frame.
struct Config
{
    uint32_t width = 1280, height = 720; // output resolution
    uint32_t aspectW = 0, aspectH = 0;   // forced aspect ratio (0 = from the resolution)
    bool fullscreen = false;             // borderless fullscreen on the primary display
    bool vsync = true;
    uint32_t maxFps = 60;                // frame cap
    bool enhance = false;                // enhanced lighting post pass (experimental)
    uint32_t msaa = 1;                   // samples per pixel: 1 (off), 2, 4 or 8
    // Upscaling: the 3D scene renders smaller and is upscaled before post-processing and the
    // HUD, which stay at full resolution. Fsr3 and Dlss need REGPU_FSR3 / REGPU_DLSS builds.
    enum class Upscaler : uint32_t { Off, Fsr1, Fsr3, Dlss };
    Upscaler upscaler = Upscaler::Off;
    uint32_t quality = 1;   // 0 native (anti-aliasing only), 1 quality, 2 balanced, 3 performance, 4 ultra performance
    float sharpness = 0.5f; // 0-1
    std::filesystem::path userDir = "."; // shader and pipeline caches, dumps
    std::wstring windowTitle = L"ReGPU";
    // A window the host already owns (an HWND): ReGPU presents into it instead of opening its own.
    // The host then handles closing, resizing and fullscreen; windowTitle and fullscreen are unused.
    void* hostWindow = nullptr;
    // Upload ring per frame (constants, vertices, textures), in MB; 0 is ReGPU's default (64 with
    // direct fetch). About 11 KB a draw goes to constants alone: titles with thousands of draws need
    // more, or their last draws are dropped. REGPU_RING_MB overrides it.
    uint32_t uploadRingMB = 0;
    // Old CPUs: renderer optimisations that help fast PCs but cost slow ones are off (the staged draw
    // constants: an FX-8350 lost time copying them).
    bool lowPerformance = false;

    // The scene's resolution as a fraction of the output (1 when upscaling is off).
    float SceneScale() const;
    // The size the game renders at: the resolution, narrowed or shortened to the forced aspect
    // ratio (the rest of the screen is black bars).
    void RenderSize(uint32_t& w, uint32_t& h) const;
};

// A shader found in the game's files or memory: its microcode as in guest memory (big endian).
struct ScannedShader
{
    bool pixel;
    std::vector<uint32_t> code;
};

// A resolve (an EDRAM copy to memory), as offered to Host::resolveWriteback.
struct ResolveCopy
{
    uint32_t base;          // physical address of the image (its top-left; tiles land in one image)
    uint32_t width, height; // the whole image, in game pixels
    uint32_t format;        // the Xenos colour format it is stored as (6: 8888)
    bool depth;             // a depth copy (written as the console stores D24FS8 in 32-bit words)
    uint32_t samples;       // the EDRAM surface's MSAA: 1, 2 or 4
    int32_t top, bottom;    // the rows this resolve covers (a tile of the image)
};

// Resolved images stay on the GPU; one the game reads on the CPU must also be written to memory.
struct Writeback
{
    enum class Mode { None, Now, Deferred };
    // Now: written before the game continues (a stall). Deferred: written when this frame's slot comes
    // round again, a frame or two late, without a stall (for something read every frame).
    Mode mode = Mode::None;
    // Deferred only: the part of the image to write (game pixels); the default is all of it.
    uint32_t x0 = 0, y0 = 0, x1 = ~0u, y1 = ~0u;
};

// What ReGPU needs from the game's runtime. Required unless marked optional.
struct Host
{
    // The guest's 4 GB address space: guest address A is at base + A.
    uint8_t* base = nullptr;
    // Optional: the file mapping of the 512 MB physical memory, if the game maps it from one
    // (D3D12 then reads vertex buffers in place instead of copying them).
    void* physicalSection = nullptr;
    // Guest memory: physical (GPU-visible) or virtual, at least `align` aligned. Returns the guest address.
    uint32_t (*alloc)(bool physical, uint32_t size, uint32_t align) = nullptr;
    // Runs body on a new thread that can call guest code (a host-created guest thread).
    void (*createThread)(const char* name, void (*body)(PPCContext&)) = nullptr;
    // The calling guest thread's processor control region (KPCR) guest address.
    uint32_t (*currentPcr)() = nullptr;
    // Calls guest code at address with ctx's registers (the graphics interrupt callback).
    void (*guestCall)(PPCContext& ctx, uint32_t address) = nullptr;
    // The guest's interrupt lock for one CPU: interrupt callbacks run holding it.
    void (*interruptLockAcquire)(int cpu) = nullptr;
    void (*interruptLockRelease)() = nullptr;

    // Optional: guest thread CPU use since the last call (" t3:85% t7:40%"), for the perf log.
    std::string (*cpuUsage)(double wallSeconds) = nullptr;
    // Optional: every shader in the game's files, for compiling them all ahead of time on first boot.
    std::vector<ScannedShader> (*staticShaders)() = nullptr;
    // Optional: whether staticShaders' list is already saved (a fast call: no search).
    bool (*staticShadersSaved)() = nullptr;
    // Optional: the title's shipped pipeline list, as pipelines.manifest text ("P vs ps blend depth
    // stencilMaskMode flags [biasScale biasOffset]" in hex, one a line: hashes and render state, no game
    // data), typically merged from players' manifests. Those not built yet are built once, after the static
    // shaders, with the boot screen up; those needing a shader the game makes at run time are built in the
    // background once it exists. Built ones join pipelines.manifest. REGPU_NO_SEED_PIPELINES=1 skips it.
    const char* (*pipelineSeed)() = nullptr;
    // Optional: a 1280x720 RGBA8 screen for the first-boot shader compile (title, progress done/total;
    // total 0 while searching). False: ReGPU's plain progress overlay is shown instead.
    bool (*bootScreen)(const wchar_t* title, uint32_t done, uint32_t total, std::vector<uint8_t>& rgba) = nullptr;
    // Optional: the game's own fix to a shader, made to its translated HLSL before it compiles (a
    // game-specific workaround: the translation stays generic). `hash` identifies the shader (the
    // debug tools print it); patched copies the title makes of it reuse the result. Returns a tag of
    // the game's choosing (0: none); draws using a tagged shader go to onDraw.
    uint32_t (*rewriteShader)(uint64_t hash, std::string& hlsl, bool pixel) = nullptr;
    // Optional: which resolves to write back to memory, and how. Without it, the 8888 colour resolves
    // whose size is listed in REGPU_RESOLVE_WRITEBACK ("WxH,WxH") are written at once.
    Writeback (*resolveWriteback)(const ResolveCopy& copy) = nullptr;
    // Optional: a draw with a tagged shader (rewriteShader), before it is recorded. `c` is the vertex
    // shader's constants (as the title set them); `user` is four float4 the rewritten shaders can read
    // as vuser[0..3] (zero unless set here).
    void (*onDraw)(uint64_t vs, uint64_t ps, uint32_t vsTag, uint32_t psTag, const float c[256][4], float user[4][4]) = nullptr;
    // Optional: a draw with a tagged shader, before it is recorded: true clears the bound colour target
    // over the draw's scissor to `colour` first. For pixels a title leaves unwritten and reads back, which
    // on the console hold whatever another view of the same EDRAM left there (a depth surface, say), and
    // here hold the colour surface's previous contents.
    bool (*clearBeforeDraw)(uint64_t vs, uint64_t ps, uint32_t vsTag, uint32_t psTag, float colour[4]) = nullptr;
    // Optional: each frame the game finishes (VdSwap), on the renderer's thread.
    void (*onSwap)(uint64_t frame) = nullptr;
    // Optional: a character typed into the game window (WM_CHAR), e.g. for cheat codes.
    void (*onChar)(wchar_t c) = nullptr;
    // Optional diagnostics: log every host thread's stack (a wait that looks hung); the calling
    // thread's stack as text (a crash); a frame that took `ms` (a spike profiler's dump).
    void (*logThreadStacks)(const char* why) = nullptr;
    std::string (*currentStack)(int maxFrames) = nullptr;
    void (*slowFrame)(double ms) = nullptr;
};

Config& GetConfig();
Host& GetHost();

// Opens the window and the D3D12 device, and starts the command processor. Call once, after filling
// in Config and Host, before the game calls VdInitializeRingBuffer.
void Start();
// The debug server's controller (REGPU_DEBUG=1; tools/regpuctl.py press/hold/stick/trigger): the game
// merges it into player 1's pad where it reads XInput (XInputGetState / XamInputGetState). False when
// nothing is held. Buttons use the XInput bits.
struct DebugPad
{
    uint16_t buttons = 0;
    uint8_t leftTrigger = 0, rightTrigger = 0;
    int16_t lx = 0, ly = 0, rx = 0, ry = 0;
    bool sticks = false, triggers = false; // replace the pad's sticks / triggers instead of adding to them
};
bool DebugPadState(DebugPad& pad);

// Whether the game window has keyboard focus (read keyboard input only then).
bool WindowHasFocus();
// Logs the command processor's state (ring pointers, what it waits on, interrupts, swaps): for a
// watchdog when the game looks hung.
void LogState();
// Call from the game's crash handler, on the crashing thread: if it crashed inside the graphics driver's
// pipeline compiler (some drivers fault on a few pipelines), the pipeline is logged and refused from the
// next start on (regpu-bad-pipelines.txt in the working directory), so its draws are dropped instead.
// It also writes out the log lines still queued (REGPU_LOG_ASYNC).
void LogCrashContext();

// The game draws its 3D view wider than 16:9 itself (a widescreen patch): ReGPU then leaves its
// field of view alone instead of widening it (Hor+) at wide resolutions.
void SetGameWidescreen(bool on);
// Call when the game has stopped drawing (the window is closing): flushes the shader and pipeline caches.
void SaveCaches();
// The output (drawing) resolution while the game runs: Config's width and height at start. Taken at
// the start of the next frame (the host GPU idle; surfaces, upscaler and swap chain rebuilt). The
// upscaler and its quality (Config::upscaler, quality) are read every frame and change without it.
void SetOutputSize(uint32_t width, uint32_t height);
// Windowed: the game window's client area to exactly width x height pixels (a resolution change
// from a host's options screen; ReGPU's own window opens at Config's width and height this way).
// Not shrunk to fit the screen; kept with its title bar on it. Maximised or fullscreen: unchanged.
// Works on Config::hostWindow too.
void SetWindowSize(uint32_t width, uint32_t height);
// Changes the frame cap and vsync while the game runs (a host's options screen); Config's maxFps
// and vsync are the starting values.
void SetFrameLimit(uint32_t maxFps, bool vsync);
// Texture filtering while the game runs: -1 the fetch's own anisotropy (the default), 0 none, else
// that many samples at most (2-16) for every wrapping linear fetch. Applies to the 2D pixel fetches of
// REGPU_LEAN_FETCH (its sampler table); others keep their samplers.
void SetAnisotropy(int maxAnisotropy);
} // namespace regpu
