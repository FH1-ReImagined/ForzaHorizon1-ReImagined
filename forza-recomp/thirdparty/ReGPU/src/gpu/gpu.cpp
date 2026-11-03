// Phase 1 GPU: a command-processor stand-in. It consumes the ring buffer the
// title's D3D writes, performs the packets that make the CPU side progress
// (memory writes, fences, interrupts, waits) and skips everything that would
// draw. Vblank interrupts run at 60 Hz. Phase 2 replaces drawing with a
// native renderer hooked at the D3D level.
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <algorithm>
#include <atomic>
#include <map>
#include <unordered_map>
#include <deque>
#include <chrono>
#include <thread>

#include <intrin.h>
#include <windows.h>

#include "render/presenter.h"
#include "live_switch.h"
#include "settings.h"
#include "kernel/imports.h"
#include "kernel/thread.h"
#include "regpu/driver.h"

using Clock = std::chrono::steady_clock;
#include "gpu/regs.h"
#include "render/presenter.h"
#include "gpu/renderer.h"
#include "precise_sleep.h"

extern HANDLE g_profiledWorker; // renderer.cpp

namespace
{
constexpr uint32_t CP_RB_RPTR = 0x01C4;
constexpr uint32_t CP_RB_WPTR = 0x01C5;
constexpr uint32_t SCRATCH_REG0 = 0x0578;
constexpr uint32_t COHER_STATUS_HOST = 0x0A31;
constexpr uint32_t RBBM_STATUS = 0x05D0;

uint32_t Reg(uint32_t index)
{
    uint32_t v;
    memcpy(&v, gpureg::LiveFile() + index * 4, 4);
    return __builtin_bswap32(v);
}
void SetReg(uint32_t index, uint32_t value)
{
    value = __builtin_bswap32(value);
    memcpy(gpureg::LiveFile() + index * 4, &value, 4);
}

// Physical addresses as the GPU sees them map onto our physical heap.
uint32_t PhysToGuest(uint32_t phys) { return 0xA0000000 + (phys & 0x1FFFFFFF); }

void PresentSwap(uint64_t frame, uint32_t frontBuffer); // command processor thread
void PaceSwap(uint64_t frame);                           // the thread that swaps

std::atomic<int64_t> g_vblankUs{ 0 }; // vblank period set by the host (0: ReGPU's own)

struct Gpu
{
    std::atomic<uint32_t> ringBase{ 0 };   // guest address
    std::atomic<uint32_t> ringDwords{ 0 };
    std::atomic<uint32_t> rptrWriteback{ 0 };
    std::atomic<uint32_t> interruptCallback{ 0 };
    std::atomic<uint32_t> interruptData{ 0 };
    uint32_t rptr = 0;
    // VdInitializeRingBuffer can replace the ring while the command processor is running the old
    // one (a title handing the GPU from one module's D3D device to another's). ringMutex orders
    // that against the CP's read pointer updates; the generation tells the CP its ring is gone.
    std::mutex ringMutex;
    uint32_t ringGeneration = 0;

    bool interruptThread = false;
    std::mutex interruptMutex;
    std::condition_variable interruptCv;
    std::vector<std::pair<uint32_t, uint32_t>> pendingInterrupts; // source, cpu

    std::atomic<uint64_t> swaps{ 0 };
    std::atomic<uint32_t> lastPacket{ 0 };
    std::atomic<uint32_t> waitingOn{ 0 };
    std::atomic<uint32_t> waitingRef{ 0 }, waitingInfo{ 0 };
    std::atomic<uint32_t> interruptsRaised{ 0 };
    std::atomic<uint64_t> draws{ 0 };
    // Physical addresses of VdSwap's blocks not yet reached by the command
    // processor; reaching one ends the frame for the renderer.
    std::mutex swapMutex;
    struct PendingSwap
    {
        uint32_t block;      // physical address of VdSwap's 64 dwords
        uint64_t frame;
        uint32_t frontBuffer; // physical address of the front buffer
    };
    std::deque<PendingSwap> pendingSwaps;
    // The NOP blocks of the pending swaps, readable without the lock: the command processor
    // checks every NOP against them (most NOPs are padding and match nothing).
    std::atomic<uint32_t> swapBlocks[16]{};
    std::atomic<uint32_t> swapBlockNext{ 0 };
    // A bit per (address >> 2) & 255 of those blocks, set before them: a padding NOP is turned away
    // with one load instead of sixteen (NOPs were ~4% of the command processor in a race).
    std::atomic<uint64_t> swapFilter[4]{};
    // Predicated tiling: type-3 packets with the predicate bit run only
    // when (binSelect & binMask) != 0. D3D replays each frame per tile and
    // predicates its once-per-frame packets (swap callbacks, fences).
    bool dropDraws = false; // this draw is a later tile's, which the first tile drew (TileMerge)
    bool inReplay = false;  // running a buffer that replays a tile pass for a later tile (TileMerge)
    uint64_t droppedDraws = 0;
    uint64_t binMask = ~0ull;
    uint64_t binSelect = ~0ull;
    std::atomic<uint64_t> skippedPredicated{ 0 };
    std::atomic<uint64_t> waits{ 0 }, slowWaits{ 0 }, waitMicros{ 0 };
    std::atomic<uint32_t> interruptsHandled{ 0 };
};

Gpu g_gpu;
bool g_trace = getenv("REGPU_GPUTRACE") != nullptr;

void RaiseInterrupt(uint32_t source, uint32_t cpu)
{
    {
        std::lock_guard lock(g_gpu.interruptMutex);
        g_gpu.pendingInterrupts.push_back({ source, cpu });
    }
    g_gpu.interruptCv.notify_one();
}

// ---- Draw worker ---------------------------------------------------------------------
// The command processor (this file's CommandProcessorLoop) parses the command stream and
// keeps the guest's view (registers, read pointer, waits) current; draws are translated on
// a worker thread. Everything the renderer needs goes through an ordered log: register
// writes (the worker keeps its own register file), shader loads (with their microcode),
// draws, swaps, and the memory writes the title treats as fences, so those land only after
// the draws before them. Waits, conditional writes and interrupts first let the worker
// catch up. Opt-in with REGPU_DRAW_THREAD=1: the worker alone still does nearly all the work
// (translation, not parsing, is the cost), but the parsing then overlaps it: FH1 free roam on a
// 5800X, 98-100 -> 106-108 fps once its main and render threads overlapped.
namespace work
{
enum : uint32_t { kPad, kRegs, kShader, kDraw, kSwap, kWriteBack, kStore };
constexpr uint64_t kWords = 16ull << 20; // 64 MB
std::vector<uint32_t> g_ring(kWords);
alignas(64) std::atomic<uint64_t> g_head{ 0 }; // written by the command processor
alignas(64) std::atomic<uint64_t> g_tail{ 0 }; // written by the worker
std::vector<uint8_t> g_file(0x8000 * 4);       // the worker's register file (registers up to 0x7FFF)
// Read when the command processor starts (a host may set REGPU_DRAW_THREAD before regpu::Start).
bool g_enabled = false;
std::atomic<bool> g_started{ false };

// Space for a record of `words` payload words; returns where to write it.
uint32_t* Begin(uint32_t type, uint32_t words)
{
    const uint64_t need = uint64_t(words) + 1;
    uint64_t head = g_head.load(std::memory_order_relaxed);
    if ((head % kWords) + need > kWords) // does not fit before the end: pad to it
    {
        const uint64_t pad = kWords - head % kWords;
        while (head + pad - g_tail.load(std::memory_order_acquire) > kWords)
            g_tail.wait(g_tail.load(std::memory_order_acquire), std::memory_order_acquire);
        g_ring[head % kWords] = (kPad << 24) | uint32_t(pad - 1);
        head += pad;
        g_head.store(head, std::memory_order_release);
        g_head.notify_one();
    }
    while (head + need - g_tail.load(std::memory_order_acquire) > kWords)
    {
        const uint64_t tail = g_tail.load(std::memory_order_acquire);
        if (head + need - tail > kWords)
            g_tail.wait(tail, std::memory_order_acquire);
    }
    uint32_t* p = &g_ring[head % kWords];
    p[0] = (type << 24) | words;
    return p + 1;
}

void End(uint32_t words)
{
    const uint64_t head = g_head.load(std::memory_order_relaxed) + words + 1;
    g_head.store(head, std::memory_order_release);
    g_head.notify_one();
}

// Waits until the worker has done everything logged so far.
std::atomic<uint64_t> g_drains{ 0 }, g_drainWaits{ 0 };
void Drain()
{
    if (!g_started.load(std::memory_order_acquire))
        return;
    g_drains++;
    if (g_tail.load(std::memory_order_acquire) != g_head.load(std::memory_order_relaxed))
        g_drainWaits++;
    const uint64_t head = g_head.load(std::memory_order_relaxed);
    for (uint64_t tail; (tail = g_tail.load(std::memory_order_acquire)) != head;)
        g_tail.wait(tail, std::memory_order_acquire);
}

bool Active() { return g_enabled && g_started.load(std::memory_order_relaxed); }

void LogRegs(uint32_t index, const uint32_t* valuesBE, uint32_t count)
{
    uint32_t* p = Begin(kRegs, count + 1);
    p[0] = index;
    memcpy(p + 1, valuesBE, size_t(count) * 4);
    End(count + 1);
}
}

void WriteBackNow(uint32_t address, uint32_t value);
namespace PendingWrites
{
void Add(uint64_t fence, uint32_t address, uint32_t value, bool raw = false);
void Drain();
}
void OrderedStore(uint32_t address, uint32_t value);

// ---- Parallel draw segments ------------------------------------------------------------
// Between resolves (and fences, swaps, waits, interrupts) the draws of a frame are
// independent: the command processor logs them with the register writes and shader loads
// between them, plus a snapshot of the registers, shaders and tile state every 128 draws.
// At the segment's end, it is cut at snapshots into pieces of about equal draw counts that
// are translated on the draw workers into their own command lists, executed in order
// (RendererRunPieces). REGPU_DRAW_WORKERS=0 turns it off.
namespace seg
{
enum : uint32_t { kRegs = 1, kShader, kDraw };
constexpr uint32_t kSnapEvery = 128;
struct Snapshot
{
    size_t logPos = 0;
    uint32_t drawsBefore = 0;
    std::vector<uint8_t> regs;
    std::vector<uint32_t> vs, ps;
    uint32_t vsAddress = 0, psAddress = 0;
    RendererTile tile;
};
bool g_open = false;
std::vector<uint32_t> g_log;
std::vector<Snapshot> g_snaps;
size_t g_snapCount = 0; // used entries of g_snaps (kept allocated)
uint32_t g_draws = 0;
// The command processor's view: the shaders last loaded and the tile state.
std::vector<uint32_t> g_vs, g_ps;
uint32_t g_vsAddress = 0, g_psAddress = 0;
RendererTile g_tile;

void TakeSnapshot()
{
    if (g_snapCount == g_snaps.size())
        g_snaps.emplace_back();
    Snapshot& s = g_snaps[g_snapCount++];
    s.logPos = g_log.size();
    s.drawsBefore = g_draws;
    s.regs.resize(0x8000 * 4); // (type-0 packets address registers up to 0x7FFF)
    memcpy(s.regs.data(), gpureg::LiveFile(), s.regs.size());
    s.vs = g_vs;
    s.ps = g_ps;
    s.vsAddress = g_vsAddress;
    s.psAddress = g_psAddress;
    s.tile = g_tile;
}

void Append(uint32_t type, const uint32_t* a, uint32_t n, const uint32_t* b = nullptr, uint32_t nb = 0)
{
    g_log.push_back(type << 24 | (n + nb));
    g_log.insert(g_log.end(), a, a + n);
    if (nb)
        g_log.insert(g_log.end(), b, b + nb);
}

// Replays snapshot `first`'s records up to `endPos` on this thread (a worker or the CP).
void Replay(size_t first, size_t endPos)
{
    const Snapshot& snap = g_snaps[first];
    thread_local std::vector<uint8_t> file;
    file = snap.regs;
    uint8_t* const saved = gpureg::t_file;
    gpureg::t_file = file.data();
    RendererSetTile(snap.tile);
    if (!snap.vs.empty())
        FrameOnShaderLoad(ShaderType::Vertex, snap.vs.data(), uint32_t(snap.vs.size()), snap.vsAddress);
    if (!snap.ps.empty())
        FrameOnShaderLoad(ShaderType::Pixel, snap.ps.data(), uint32_t(snap.ps.size()), snap.psAddress);
    for (size_t at = snap.logPos; at < endPos;)
    {
        const uint32_t header = g_log[at];
        const uint32_t type = header >> 24, n = header & 0xFFFFFF;
        const uint32_t* a = &g_log[at + 1];
        switch (type)
        {
        case kRegs:
            memcpy(file.data() + size_t(a[0]) * 4, a + 1, size_t(n - 1) * 4);
            break;
        case kShader:
            FrameOnShaderLoad(ShaderType(a[0]), a + 3, a[2], a[1]);
            break;
        case kDraw:
        {
            const int32_t rect[4] = { int32_t(a[4]), int32_t(a[5]), int32_t(a[6]), int32_t(a[7]) };
            RendererForceTileWiden(true, a[3] != 0, rect);
            FrameOnDraw(a[0], a[1], a[2]);
            RendererForceTileWiden(false, false, nullptr);
            break;
        }
        }
        at += size_t(n) + 1;
    }
    gpureg::t_file = saved;
}

// Segments too small to go parallel are drawn straight away, not logged: logging one costs a snapshot of
// the whole register file (128 KB) and its replay another copy, and PGR4 cuts ~45 segments a frame of
// which two have enough draws to share out. Which ones are small comes from the last frame (a segment
// is known by its place in the frame); a wrong guess only costs that segment's parallelism for a frame.
// REGPU_LOG_ALL_SEGMENTS=1: log every segment, as before (A/B).
std::vector<uint32_t> g_sizes, g_lastSizes; // draws of each segment this frame / the last
uint32_t g_index = 0;                        // the segment the next draw belongs to
uint32_t g_directDraws = 0;                  // draws of the current segment drawn straight away
constexpr uint32_t kParallelDraws = 2 * 96;  // fewer than this never make two pieces (Flush)

// Whether the current segment's draws go straight to the renderer.
bool Direct()
{
    static LiveSwitch logAll("REGPU_LOG_ALL_SEGMENTS");
    return !g_open && !logAll && g_index < g_lastSizes.size() && g_lastSizes[g_index] < kParallelDraws;
}

// The current segment ends (logged or drawn straight away).
void EndSegment(uint32_t draws)
{
    if (!draws)
        return;
    if (g_sizes.size() <= g_index)
        g_sizes.resize(g_index + 1);
    g_sizes[g_index++] = draws;
}

// A frame ends: its segment sizes are the guide for the next.
void EndFrame()
{
    if (static LiveSwitch trace("REGPU_TRACE_SEGMENTS"); trace)
    {
        static uint32_t frames = 0;
        if (frames++ % 300 == 0)
        {
            std::string line;
            for (uint32_t n : g_sizes)
                line += " " + std::to_string(n);
            Log("segments:%s", line.c_str());
        }
    }
    g_lastSizes.swap(g_sizes);
    g_sizes.clear();
    g_index = 0;
}

// Translates the open segment (on the workers if it is large enough).
void Flush()
{
    EndSegment(g_open ? g_draws : g_directDraws);
    g_directDraws = 0;
    if (!g_open)
        return;
    g_open = false;
    const size_t end = g_log.size();
    // REGPU_DRAW_PIECES=n ("env REGPU_DRAW_PIECES n" while running): at most n pieces a segment (more than
    // the workers: they take the next piece as they finish one, evening out pieces of uneven cost).
    static uint32_t flushes = 0, pieceCap = 0;
    if (flushes++ % 256 == 0)
        pieceCap = getenv("REGPU_DRAW_PIECES") ? uint32_t(atoi(getenv("REGPU_DRAW_PIECES"))) : 0;
    const uint32_t workers = pieceCap ? pieceCap : RendererDrawWorkers() + 1;
    const uint32_t pieces = std::min<uint32_t>(uint32_t(g_snapCount), std::min(workers, g_draws / 96));
    {
        static uint64_t segments = 0, parallel = 0, draws = 0, parallelDraws = 0;
        static auto last = std::chrono::steady_clock::now();
        segments++;
        draws += g_draws;
        if (pieces > 1)
        {
            parallel++;
            parallelDraws += g_draws;
        }
        static LiveSwitch perf("REGPU_PERF");
        if (perf && std::chrono::steady_clock::now() - last > std::chrono::seconds(2))
        {
            last = std::chrono::steady_clock::now();
            Log("perf: segments %llu (%llu parallel), draws %llu (%llu parallel) in 2 s", (unsigned long long)segments,
                (unsigned long long)parallel, (unsigned long long)draws, (unsigned long long)parallelDraws);
            segments = parallel = draws = parallelDraws = 0;
        }
    }
    if (pieces <= 1)
        Replay(0, end);
    else
    {
        // Snapshot boundaries closest to equal shares of the draws.
        std::vector<size_t> starts;
        for (uint32_t k = 0; k < pieces; k++)
        {
            const uint32_t want = uint32_t(uint64_t(g_draws) * k / pieces);
            size_t best = 0;
            for (size_t i = 0; i < g_snapCount; i++)
                if (g_snaps[i].drawsBefore <= want)
                    best = i;
            if (starts.empty() || best != starts.back())
                starts.push_back(best);
        }
        RendererRunPieces(uint32_t(starts.size()), [&](uint32_t i) {
            Replay(starts[i], i + 1 < starts.size() ? g_snaps[starts[i + 1]].logPos : end);
        });
        // This thread's own draws continue with the shaders the stream has now.
        if (!g_vs.empty())
            FrameOnShaderLoad(ShaderType::Vertex, g_vs.data(), uint32_t(g_vs.size()), g_vsAddress);
        if (!g_ps.empty())
            FrameOnShaderLoad(ShaderType::Pixel, g_ps.data(), uint32_t(g_ps.size()), g_psAddress);
    }
    g_log.clear();
    g_snapCount = 0;
    g_draws = 0;
}

bool Enabled()
{
    static const bool on = RendererDrawWorkers() > 0 && !getenv("REGPU_DRAW_THREAD");
    return on;
}
}

void SubmitShaderLoad(ShaderType type, const uint32_t* microcodeBE, uint32_t dwords, uint32_t guestAddress)
{
    if (seg::Enabled())
    {
        dwords = std::min<uint32_t>(dwords, 0x10000);
        auto& code = type == ShaderType::Vertex ? seg::g_vs : seg::g_ps;
        code.assign(microcodeBE, microcodeBE + dwords);
        (type == ShaderType::Vertex ? seg::g_vsAddress : seg::g_psAddress) = guestAddress;
        if (seg::g_open)
        {
            const uint32_t head[3] = { uint32_t(type), guestAddress, dwords };
            seg::Append(seg::kShader, head, 3, microcodeBE, dwords);
            return;
        }
    }
    if (!work::Active())
    {
        FrameOnShaderLoad(type, microcodeBE, dwords, guestAddress);
        return;
    }
    dwords = std::min<uint32_t>(dwords, 0x10000);
    uint32_t* p = work::Begin(work::kShader, dwords + 3);
    p[0] = uint32_t(type);
    p[1] = guestAddress;
    p[2] = dwords;
    memcpy(p + 3, microcodeBE, size_t(dwords) * 4);
    work::End(dwords + 3);
}

void SubmitDraw(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    // A later tile's draw that the first tile drew for all of them (TileMerge).
    {
        int32_t rect[4];
        RendererScreenScissor(rect);
        g_gpu.dropDraws = !work::Active() && RendererTileDrop(gpureg::Get(0x2000 /* RB_SURFACE_INFO */), rect, RendererIsResolveDraw(), initiator, g_gpu.inReplay, dmaBase, dmaSize);
    }
    if (g_gpu.dropDraws)
    {
        g_gpu.droppedDraws++;
        return;
    }
    if (seg::Enabled())
    {
        if (RendererIsResolveDraw() || !RendererParallelReady())
        {
            seg::Flush();
            FrameOnDraw(initiator, dmaBase, dmaSize);
            return;
        }
        // TileMerge's widened first tile, decided now (the tile pass moves on before the workers replay it);
        // the first widened draw clears the later tiles' rows first, so it runs here in order.
        int32_t widenedRect[4];
        const uint32_t widen = RendererTileWidenNow(widenedRect);
        if (widen == 2)
        {
            seg::Flush();
            FrameOnDraw(initiator, dmaBase, dmaSize);
            return;
        }
        if (seg::Direct())
        {
            seg::g_directDraws++;
            FrameOnDraw(initiator, dmaBase, dmaSize);
            RendererTrackTile(seg::g_tile);
            return;
        }
        if (!seg::g_open || seg::g_draws % seg::kSnapEvery == 0)
        {
            seg::g_open = true;
            seg::TakeSnapshot();
        }
        const uint32_t a[8] = { initiator, dmaBase, dmaSize, widen, uint32_t(widenedRect[0]), uint32_t(widenedRect[1]), uint32_t(widenedRect[2]),
                                uint32_t(widenedRect[3]) };
        seg::Append(seg::kDraw, a, 8);
        seg::g_draws++;
        RendererTrackTile(seg::g_tile);
        return;
    }
    if (!work::Active())
    {
        FrameOnDraw(initiator, dmaBase, dmaSize);
        return;
    }
    uint32_t* p = work::Begin(work::kDraw, 3);
    p[0] = initiator;
    p[1] = dmaBase;
    p[2] = dmaSize;
    work::End(3);
}

void WorkerLoop()
{
    SetThreadDescription(GetCurrentThread(), L"gpu draw worker");
    // the port's sampler (PGR4_SAMPLE_THREAD=w) follows this thread
    if (!g_profiledWorker)
        DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_profiledWorker, 0, FALSE, DUPLICATE_SAME_ACCESS);
    gpureg::t_file = work::g_file.data();
    for (;;)
    {
        uint64_t tail = work::g_tail.load(std::memory_order_relaxed);
        uint64_t head;
        while ((head = work::g_head.load(std::memory_order_acquire)) == tail)
            work::g_head.wait(head, std::memory_order_acquire);
        // Everything published so far, then let the command processor know.
        while (tail != head)
        {
            const uint32_t* p = &work::g_ring[tail % work::kWords];
            const uint32_t type = p[0] >> 24, words = p[0] & 0xFFFFFF;
            const uint32_t* a = p + 1;
            switch (type)
            {
            case work::kRegs:
                memcpy(work::g_file.data() + a[0] * 4, a + 1, size_t(words - 1) * 4);
                break;
            case work::kShader:
                FrameOnShaderLoad(ShaderType(a[0]), a + 3, a[2], a[1]);
                break;
            case work::kDraw:
                FrameOnDraw(a[0], a[1], a[2]);
                break;
            case work::kSwap:
                FrameOnSwap(uint64_t(a[0]) | uint64_t(a[1]) << 32, a[2]);
                break;
            case work::kWriteBack:
                PendingWrites::Add(RendererFenceSubmit(), a[0], a[1]); // after the host GPU, as on the command processor
                break;
            case work::kStore:
                OrderedStore(a[0], a[1]);
                break;
            default:
                break;
            }
            tail += uint64_t(words) + 1;
            // Publish now and then (the command processor waits on this when the log is full
            // or it must catch up): every record would cost a wake-up each.
            if (type == work::kWriteBack || type == work::kStore || type == work::kSwap || (tail & 0x3FFF) < words + 1)
            {
                work::g_tail.store(tail, std::memory_order_release);
                work::g_tail.notify_all();
            }
        }
        work::g_tail.store(tail, std::memory_order_release);
        work::g_tail.notify_all();
    }
}

thread_local PPCContext* t_cpContext = nullptr;

void DispatchInterrupt(PPCContext& ctx, uint32_t source, uint32_t cpu)
{
    const uint32_t callback = g_gpu.interruptCallback;
    if (!callback)
        return;
    // The handler acknowledges by clearing the bit of the CPU it runs on,
    // read from the PCR; it runs with that CPU's interrupts disabled.
    const regpu::Host& host = regpu::GetHost();
    *GuestPtr<uint8_t>(host.currentPcr() + kpcr::CurrentCpu) = uint8_t(cpu);
    host.interruptLockAcquire(int(cpu));
    ctx.msr &= ~0x8000ull;
    ctx.r3.u64 = source;
    ctx.r4.u64 = g_gpu.interruptData;
    host.guestCall(ctx, callback);
    ctx.msr |= 0x8000;
    host.interruptLockRelease();
}

// Vblank callbacks run on a guest thread of their own, like the console's
// DPC; command-stream interrupts run on the command processor thread.
void InterruptLoop(PPCContext& ctx)
{
    for (;;)
    {
        std::vector<std::pair<uint32_t, uint32_t>> sources;
        {
            std::unique_lock lock(g_gpu.interruptMutex);
            g_gpu.interruptCv.wait(lock, [] { return !g_gpu.pendingInterrupts.empty(); });
            sources.swap(g_gpu.pendingInterrupts);
        }
        for (auto [source, cpu] : sources)
            DispatchInterrupt(ctx, source, cpu);
    }
}

// GPU memory accesses carry an endian mode in the low two address bits:
// 0 none, 1 8-in-16, 2 8-in-32, 3 16-in-32.
uint32_t GpuSwap(uint32_t value, uint32_t endian)
{
    switch (endian & 3)
    {
    case 1: return ((value & 0x00FF00FF) << 8) | ((value >> 8) & 0x00FF00FF);
    case 2: return __builtin_bswap32(value);
    case 3: return (value << 16) | (value >> 16);
    default: return value;
    }
}

void WriteBackNow(uint32_t address, uint32_t value)
{
    *GuestPtr<uint32_t>(PhysToGuest(address & ~3u)) = GpuSwap(value, address);
}

// Fence writes waiting for the host GPU to reach the draws before them, written in order by
// their own thread.
namespace PendingWrites
{
struct Write
{
    uint64_t fence;
    uint32_t address, value;
    bool raw; // a plain big-endian store (a scratch register write-back), not a fence write
};
std::mutex g_mutex;
std::condition_variable g_cv, g_done;
std::deque<Write> g_queue;

void Main()
{
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    std::unique_lock lock(g_mutex);
    for (;;)
    {
        g_cv.wait(lock, [] { return !g_queue.empty(); });
        const Write w = g_queue.front();
        lock.unlock();
        RendererFenceWait(w.fence, event);
        RendererFinishFencedWritebacks(w.fence); // resolve write-backs before the fence
        if (w.raw)
            StoreBE32(w.address, w.value);
        else
            WriteBackNow(w.address, w.value);
        lock.lock();
        g_queue.pop_front(); // after the write: Add sees it pending until then, keeping the order
        g_done.notify_all();
    }
}

void Add(uint64_t fence, uint32_t address, uint32_t value, bool raw)
{
    std::lock_guard lock(g_mutex);
    if (!fence && g_queue.empty())
    {
        if (raw)
            StoreBE32(address, value);
        else
            WriteBackNow(address, value);
        return;
    }
    static std::once_flag started;
    std::call_once(started, [] { std::thread(Main).detach(); });
    if (!fence)
        fence = g_queue.back().fence;
    g_queue.push_back({ fence, address, value, raw });
    g_cv.notify_one();
}

// Blocks until every queued write has landed.
void Drain()
{
    std::unique_lock lock(g_mutex);
    g_done.wait(lock, [] { return g_queue.empty(); });
}
}

// Completion signals other than fence writes keep their order behind the fence writes still waiting
// for the host GPU: a scratch register write-back lands after them (OrderedStore), and an interrupt
// packet waits for them (the title may read a fence in its handler). Acting at once, a title could
// see a later signal before an earlier fence and reuse memory the GPU still reads (from the FM4
// port). REGPU_NO_FENCE_ORDER=1: they act at once, as before (A/B).
LiveSwitch g_noFenceOrder("REGPU_NO_FENCE_ORDER");

void OrderedStore(uint32_t address, uint32_t value)
{
    if (!g_noFenceOrder)
        PendingWrites::Add(0, address, value, true);
    else
        StoreBE32(address, value);
}

// Memory writes from the command stream (fences): after the draws before them.
void WriteBack(uint32_t address, uint32_t value)
{
    seg::Flush(); // a fence: after the draws before it
    if (work::Active())
    {
        uint32_t* p = work::Begin(work::kWriteBack, 2);
        p[0] = address;
        p[1] = value;
        work::End(2);
        return;
    }
    // With early fences (RendererEarlyFences) the fence lands now; the work before it is still
    // submitted.
    const uint64_t fence = RendererFenceSubmit();
    PendingWrites::Add(RendererEarlyFences() ? 0 : fence, address, value);
}

uint32_t ReadBack(uint32_t address)
{
    return GpuSwap(*GuestPtr<uint32_t>(PhysToGuest(address & ~3u)), address);
}

bool WaitCompare(uint32_t function, uint32_t value, uint32_t reference)
{
    switch (function)
    {
    case 0: return false;
    case 1: return value < reference;
    case 2: return value <= reference;
    case 3: return value == reference;
    case 4: return value != reference;
    case 5: return value >= reference;
    case 6: return value > reference;
    default: return true;
    }
}

void ExecuteBuffer(uint32_t address, uint32_t dwords);

// Where the packet being executed ends in each buffer being run (innermost last), so
// the lookahead can continue from there.
struct BufferCursor
{
    uint32_t next, end; // guest addresses: next packet, end of the buffer
};
thread_local std::vector<BufferCursor> t_cursors;
uint32_t g_ringNext = 0;      // ring index after the packet being executed
std::atomic<bool> g_inRing{ false };

uint32_t PacketLength(uint32_t header)
{
    switch (header >> 30)
    {
    case 0:
    case 3: return ((header >> 16) & 0x3FFF) + 2;
    case 1: return 3;
    default: return 1;
    }
}

constexpr uint32_t SCRATCH_UMSK = 0x01DC;
constexpr uint32_t SCRATCH_ADDR = 0x01DD;


// Register writes from the command stream. Scratch registers with their
// mask bit set are mirrored to memory: D3D's fences rely on it.
// The display's gamma table (DC_LUT_*): the title writes the entry index, then the entry
// (10:10:10 RGB). The presenter applies it, as the display hardware does.
// In PWL mode (DC_LUT_RW_MODE bit 0) the title writes the piecewise-linear ramp instead: 128
// entries of base and delta, each as red, green, blue writes (those the write-enable mask, blue
// green red, allows), the index stepping after blue. As Xenia does.
constexpr uint32_t DC_LUT_RW_MODE = 0x1921, DC_LUT_RW_INDEX = 0x1922, DC_LUT_PWL_DATA = 0x1924, DC_LUT_30_COLOR = 0x1925,
                   DC_LUT_WRITE_EN_MASK = 0x1927;
uint32_t g_lutIndex = 0, g_lutComponent = 0;

void WriteLut(uint32_t index, uint32_t value)
{
    if (index == DC_LUT_RW_INDEX)
    {
        g_lutIndex = value & 0xFF;
        g_lutComponent = 0;
    }
    else if (index == DC_LUT_30_COLOR)
        RendererSetGammaEntry(g_lutIndex++ & 0xFF, value);
    else if (index == DC_LUT_PWL_DATA && (Reg(DC_LUT_RW_MODE) & 1))
    {
        if ((Reg(DC_LUT_WRITE_EN_MASK) >> (2 - g_lutComponent)) & 1)
            RendererSetGammaPwl(g_lutIndex & 0x7F, g_lutComponent, value);
        if (++g_lutComponent == 3)
        {
            g_lutComponent = 0;
            g_lutIndex = (g_lutIndex & 0x80) | ((g_lutIndex + 1) & 0x7F);
        }
    }
}

void WriteRegister(uint32_t index, uint32_t value)
{
    SetReg(index, value);
    WriteLut(index, value);
    if (seg::g_open)
    {
        const uint32_t be[2] = { index, __builtin_bswap32(value) };
        seg::Append(seg::kRegs, be, 2);
    }
    if (work::Active())
    {
        const uint32_t be = __builtin_bswap32(value);
        work::LogRegs(index, &be, 1);
    }
    if (index >= SCRATCH_REG0 && index < SCRATCH_REG0 + 8)
    {
        const uint32_t n = index - SCRATCH_REG0;
        if ((Reg(SCRATCH_UMSK) >> n) & 1)
        {
            const uint32_t address = PhysToGuest(Reg(SCRATCH_ADDR) + n * 4);
            seg::Flush();
            if (work::Active())
            {
                uint32_t* p = work::Begin(work::kStore, 2);
                p[0] = address;
                p[1] = value;
                work::End(2);
            }
            else
                OrderedStore(address, value);
        }
    }
}

// Bulk register writes from big-endian guest data (constant loads, register runs): the
// register file is big-endian too, so a run clear of the scratch registers is a copy.
void WriteRegisters(uint32_t index, uint32_t source, uint32_t count)
{
    const bool lut = index <= DC_LUT_30_COLOR && index + count > DC_LUT_RW_INDEX;
    if (!lut && (index + count <= SCRATCH_REG0 || index >= SCRATCH_REG0 + 8))
    {
        memcpy(gpureg::LiveFile() + index * 4, GuestPtr<uint8_t>(source), size_t(count) * 4);
        if (seg::g_open)
            seg::Append(seg::kRegs, &index, 1, GuestPtr<uint32_t>(source), count);
        if (work::Active())
            work::LogRegs(index, GuestPtr<uint32_t>(source), count);
        return;
    }
    for (uint32_t i = 0; i < count; i++)
        WriteRegister(index + i, LoadBE32(source + i * 4));
}

// (At namespace scope: a function-local static checks its initialisation guard on every packet.)
const int g_gpuSlow = getenv("REGPU_GPUSLOW") ? atoi(getenv("REGPU_GPUSLOW")) : 0;
LiveSwitch g_tracePredication("REGPU_TRACE_PRED");

// Executes one packet at ptr; returns its size in dwords.
uint32_t ExecutePacket(uint32_t ptr)
{
    const uint32_t header = LoadBE32(ptr);
    g_gpu.lastPacket.store(ptr, std::memory_order_relaxed); // diagnostics only: no fence per packet
    for (int i = 0; i < g_gpuSlow; i++)
        _mm_pause();
    switch (header >> 30)
    {
    case 0: // type 0: register writes
    {
        const uint32_t count = ((header >> 16) & 0x3FFF) + 1;
        const uint32_t base = header & 0x7FFF;
        const bool oneReg = header & 0x8000;
        if (oneReg)
            for (uint32_t i = 0; i < count; i++)
                WriteRegister(base, LoadBE32(ptr + 4 + i * 4));
        else
            WriteRegisters(base, ptr + 4, count);
        return 1 + count;
    }
    case 1: // type 1: two register writes
        WriteRegister(header & 0x7FF, LoadBE32(ptr + 4));
        WriteRegister((header >> 11) & 0x7FF, LoadBE32(ptr + 8));
        return 3;
    case 2: // type 2: nop (VdSwap fills its block with these)
    {
        // (Guest and physical addresses share their low bits.)
        if (const uint32_t h = (ptr >> 2) & 255; !((g_gpu.swapFilter[h >> 6].load(std::memory_order_acquire) >> (h & 63)) & 1))
            return 1;
        bool candidate = false;
        for (const auto& b : g_gpu.swapBlocks)
            candidate |= b.load(std::memory_order_acquire) == (ptr & 0x1FFFFFFF);
        if (!candidate)
            return 1;
        std::lock_guard lock(g_gpu.swapMutex);
        for (size_t i = 0; i < g_gpu.pendingSwaps.size(); i++)
        {
            if (g_gpu.pendingSwaps[i].block == (ptr & 0x1FFFFFFF))
            {
                const auto swap = g_gpu.pendingSwaps[i];
                g_gpu.pendingSwaps.erase(g_gpu.pendingSwaps.begin(), g_gpu.pendingSwaps.begin() + i + 1);
                PresentSwap(swap.frame, swap.frontBuffer);
                break;
            }
        }
        return 1;
    }
    default:
        break;
    }

    // Type 3.
    const uint32_t count = ((header >> 16) & 0x3FFF) + 1;
    const uint32_t opcode = (header >> 8) & 0x7F;
    const uint32_t data = ptr + 4;
    auto arg = [&](uint32_t i) { return LoadBE32(data + i * 4); };
    LiveSwitch& tracePredication = g_tracePredication;
    static std::atomic<int> predLogged{ 0 };
    if (tracePredication && (header & 1) && (g_gpu.binSelect != 0xFFFFFFFFull || g_gpu.binMask != 0xFFFFFFFFull) && predLogged++ < (getenv("REGPU_TRACE_PRED_MAX") ? atoi(getenv("REGPU_TRACE_PRED_MAX")) : 400))
        Log("pred: op %02X mask %016llX select %016llX -> %s", opcode, (unsigned long long)g_gpu.binMask, (unsigned long long)g_gpu.binSelect,
            (g_gpu.binSelect & g_gpu.binMask) ? "run" : "skip");
    if (tracePredication && opcode == 0x2D && (header & 1) && (g_gpu.binSelect & g_gpu.binMask) && predLogged < 100000000)
        Log("pred: set constant %08X: %08X %08X %08X (count %u)", arg(0), count > 1 ? arg(1) : 0, count > 2 ? arg(2) : 0, count > 3 ? arg(3) : 0, count);
    if (RendererTracing() && opcode != 0x10)
        Log("pm4: op %02X count %u%s", opcode, count, (header & 1) && !(g_gpu.binSelect & g_gpu.binMask) ? " (predicated off)" : "");
    if ((header & 1) && !(g_gpu.binSelect & g_gpu.binMask))
    {
        g_gpu.skippedPredicated++;
        return 1 + count;
    }
    if (g_trace && opcode != 0x10)
        Log("gpu: %08X op %02X count %u: %08X %08X %08X %08X", ptr, opcode, count, arg(0), count > 1 ? arg(1) : 0,
            count > 2 ? arg(2) : 0, count > 3 ? arg(3) : 0);
    switch (opcode)
    {
    case 0x64: // XE_SWAP: 'SWAP', front buffer physical address, width, height
    {
        // Not a Xenos packet: Xenia's VdSwap, and ReXGlue's after it, writes it into the swap's
        // block instead of NOPs. ReGPU's own VdSwap never does, so XenonRecomp ports don't see it.
        if (count >= 2 && arg(0) == 0x53574150)
        {
            const uint64_t n = ++g_gpu.swaps;
            PresentSwap(n, arg(1) & 0x1FFFF000);
            // The guest's VdSwap returned long ago; pacing the command processor here holds the
            // title back once the ring fills, which caps it the same way.
            PaceSwap(n);
        }
        break;
    }
    case 0x3F: // INDIRECT_BUFFER
    case 0x37: // INDIRECT_BUFFER_PFD
        // REGPU_TRACE_IB=1: the indirect buffers run (address, dwords, window offset, scissor), 400 of them.
        if (static LiveSwitch traceIb("REGPU_TRACE_IB"); traceIb)
        {
            static int logged = 0;
            if (logged++ < 400)
                Log("ib: %08X %u dwords, window offset %08X scissor %08X %08X, swaps %llu", arg(0), arg(1) & 0xFFFFF, gpureg::Get(0x2080),
                    gpureg::Get(0x2081), gpureg::Get(0x2082), (unsigned long long)g_gpu.swaps.load());
        }
        {
            // Predicated tiling merged (renderer.cpp, TileMerge): a buffer run again this frame
            // with another window offset is a replay of a tile pass. Its packets still run
            // (fences, interrupts); its draws are dropped once the first tile draws them all.
            struct Run
            {
                uint32_t windowOffset, surface;
                int32_t rect[4];
            };
            static std::unordered_map<uint64_t, Run> runs;
            static uint64_t runsSwap = ~0ull;
            if (runsSwap != g_gpu.swaps.load())
            {
                runs.clear();
                runsSwap = g_gpu.swaps.load();
            }
            const uint64_t key = uint64_t(arg(0)) << 32 | (arg(1) & 0xFFFFF);
            Run now{ gpureg::Get(0x2080 /* PA_SC_WINDOW_OFFSET */), gpureg::Get(0x2000 /* RB_SURFACE_INFO */), {} };
            RendererScreenScissor(now.rect);
            bool replay = false;
            // (The first tile sits at the origin: window offset 0; the others are offset.)
            // Not with the draw thread yet: merged there, the second tile's area came out unrendered.
            if (const auto [it, inserted] = runs.try_emplace(key, now); !inserted && it->second.windowOffset == 0 && now.windowOffset != 0 &&
                it->second.surface == now.surface && !work::Active())
                replay = RendererTileReplay(now.surface, it->second.rect, now.rect);
            // (Its draws are dropped in SubmitDraw up to the tile's first resolve: they include the
            // tile's own clears, whose scissor is not the tile's, and which would wipe what the
            // first tile drew there.)
            const bool outer = g_gpu.inReplay;
            g_gpu.inReplay = outer || replay;
            ExecuteBuffer(PhysToGuest(arg(0) & ~3u), arg(1) & 0xFFFFF);
            g_gpu.inReplay = outer;
        }
        break;
    case 0x3D: // MEM_WRITE: address, values...
        for (uint32_t i = 1; i < count; i++)
            WriteBack(arg(0) + (i - 1) * 4, arg(i));
        break;
    case 0x58: // EVENT_WRITE_SHD: initiator, address, value
    {
        uint32_t value = arg(2);
        if ((arg(0) >> 31) & 1) // write a counter instead of the value
            value = uint32_t(g_gpu.swaps.load());
        WriteBack(arg(1), value);
        break;
    }
    case 0x59: // EVENT_WRITE_CFL
        break;
    case 0x5A: // EVENT_WRITE_EXT: screen extents of the previous draws.
    {
        // D3D's predicated tiling turns these into the bins each draw
        // touches. Report the whole screen: every draw may touch every tile.
        const uint16_t extents[6] = { 0, 8192 >> 3, 0, 8192 >> 3, 0, 1 };
        const uint32_t address = GpuPhysToGuest(arg(1) & ~3u);
        for (int i = 0; i < 6; i++)
            *GuestPtr<uint16_t>(address + i * 2) = __builtin_bswap16(extents[i]);
        break;
    }
    case 0x5B: // EVENT_WRITE_ZPD: occlusion query sample counts.
    {
        // At a query's end D3D has written 0xFFFFFEED to the counters;
        // answer with a fixed number of visible samples.
        const uint32_t counts = GpuPhysToGuest(gpureg::Get(0x2325) & ~3u); // RB_SAMPLE_COUNT_ADDR
        auto* c = GuestPtr<uint32_t>(counts);
        const uint32_t finished = __builtin_bswap32(0xFFFFFEED);
        // Layout (little-endian dwords): Total A/B, ZFail A/B, ZPass A/B, StencilFail A/B.
        const bool end = (c[4] == finished && c[5] == finished) || (c[2] == finished && c[3] == finished);
        static int logged = 0;
        static LiveSwitch traceZpd("REGPU_TRACE_ZPD"); // (a getenv per query was ~1% of the command processor)
        if (logged < 12 && traceZpd)
        {
            logged++;
            Log("zpd: %08X %08X %08X %08X %08X %08X %08X %08X%s", c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], end ? " (end)" : "");
        }
        // Real counts when draws are recorded here in stream order (not with the draw thread
        // or workers, whose lists the query would not wrap): the sun flare fades behind trees.
        static LiveSwitch fake("REGPU_FAKE_OCCLUSION"); // A/B: the old fixed answer
        const bool real = !fake && !work::Active() && !seg::Enabled();
        uint32_t samples = 1000, total = 1000;
        if (real && !end)
            RendererOcclusionBegin();
        else if (real && !RendererOcclusionEnd(samples, total, counts))
            samples = total = 1000; // no result yet (the first frames): fully visible
        memset(c, 0, 8 * 4);
        if (end)
        {
            // Total: every sample the query's draws covered; ZPass: those that passed. With both equal a
            // light flare (PGR4's brake lights) could only be fully on or 0/0, and flickered as its few
            // pixels passed or failed.
            c[4] = samples; // ZPass_A
            c[0] = total;   // Total_A
            // REGPU_TRACE_ZPD_RESULTS=1: the answers given (40 after it is switched on).
            static LiveSwitch traceResults("REGPU_TRACE_ZPD_RESULTS");
            static int resultsLogged = 0;
            if (!traceResults)
                resultsLogged = 0;
            else if (resultsLogged++ < 40)
                Log("zpd result: zpass %u total %u", samples, total);
        }
        break;
    }
    case 0x45: // COND_WRITE: wait info, poll addr, ref, mask, write addr, write data
    {
        if (arg(0) & 0x10) // polls memory a fence of the open segment may change
            seg::Flush();
        const uint32_t info = arg(0);
        if (info & 0x10) // a memory poll may read a fence the worker writes
            work::Drain();
        const uint32_t value = (info & 0x10) ? ReadBack(arg(1)) : Reg(arg(1));
        if (WaitCompare(info & 7, value & arg(3), arg(2)))
        {
            if (info & 0x100)
                WriteBack(arg(4), arg(5));
            else
                WriteRegister(arg(4), arg(5));
        }
        break;
    }
    case 0x3C: // WAIT_REG_MEM: info, poll addr, ref, mask, interval
    {
        const uint32_t info = arg(0);
        // A memory poll that does not hold yet may wait for a fence of the open segment.
        if ((info & 0x10) && !WaitCompare(info & 7, ReadBack(arg(1)) & arg(3), arg(2)))
            seg::Flush();
        // A memory poll may wait for a fence the worker writes: let it catch up, unless the
        // condition already holds (most waits are for values the title set).
        if ((info & 0x10) && !WaitCompare(info & 7, ReadBack(arg(1)) & arg(3), arg(2)))
            work::Drain();
        g_gpu.waitingOn = arg(1);
        g_gpu.waitingRef = arg(2);
        g_gpu.waitingInfo = info;
        // Usually satisfied within microseconds by an interrupt handler:
        // spin, then yield, then sleep.
        const auto start = Clock::now();
        static const int spinMs = getenv("REGPU_CP_SPIN_MS") ? atoi(getenv("REGPU_CP_SPIN_MS")) : 100;
        for (int tries = 0;; tries++)
        {
            const uint32_t value = (info & 0x10) ? ReadBack(arg(1)) : Reg(arg(1));
            if (WaitCompare(info & 7, value & arg(3), arg(2)))
                break;
            const auto waited = Clock::now() - start;
            if (waited > std::chrono::seconds(2))
            {
                LOG_ONCE("gpu: WAIT_REG_MEM on %08X never satisfied; continuing", arg(1));
                break;
            }
            if (tries < 200)
                _mm_pause();
            else if (waited < std::chrono::milliseconds(spinMs))
                SwitchToThread();
            else
                Sleep(1);
        }
        g_gpu.waitingOn = 0;
        {
            const auto us = std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - start).count();
            g_gpu.waits++;
            g_gpu.waitMicros += us;
            static LiveSwitch perf("REGPU_PERF");
            if (perf)
            {
                // Time per wait target, logged every 2 s.
                static std::map<std::pair<uint32_t, uint32_t>, uint64_t> byTarget;
                static auto lastLog = Clock::now();
                byTarget[{ arg(1) | ((info & 0x10) ? 0x80000000u : 0), info & 7 }] += uint64_t(us);
                if (Clock::now() - lastLog > std::chrono::seconds(2))
                {
                    lastLog = Clock::now();
                    for (auto& [key, total] : byTarget)
                        if (total > 20000)
                            Log("perf: CP waited %llu ms on %s %08X (fn %u)", (unsigned long long)(total / 1000), (key.first >> 31) ? "mem" : "reg",
                                key.first & 0x7FFFFFFF, key.second);
                    byTarget.clear();
                }
            }
            if (us > 1000)
                g_gpu.slowWaits++;
        }
        break;
    }
    case 0x21: // REG_RMW: reg, and mask, or mask
    {
        const uint32_t reg = arg(0) & 0x1FFF;
        WriteRegister(reg, (Reg(reg) & arg(1)) | arg(2));
        break;
    }
    case 0x54: // INTERRUPT: cpu mask
        seg::Flush();
        work::Drain(); // after the draws before it, like the hardware
        if (!g_noFenceOrder)
            PendingWrites::Drain(); // and after the fence writes before it
        for (uint32_t cpu = 0; cpu < 6; cpu++)
        {
            if ((arg(0) >> cpu) & 1)
            {
                // Synchronous, like the hardware: the handler runs on this
                // thread before the command processor reads on.
                g_gpu.interruptsRaised++;
                DispatchInterrupt(*t_cpContext, 1, cpu);
                g_gpu.interruptsHandled++;
            }
        }
        break;
    case 0x60: // SET_BIN_MASK_LO
        g_gpu.binMask = (g_gpu.binMask & 0xFFFFFFFF00000000ull) | arg(0);
        break;
    case 0x61: // SET_BIN_MASK_HI
        g_gpu.binMask = (g_gpu.binMask & 0xFFFFFFFFull) | (uint64_t(arg(0)) << 32);
        break;
    case 0x62: // SET_BIN_SELECT_LO
        g_gpu.binSelect = (g_gpu.binSelect & 0xFFFFFFFF00000000ull) | arg(0);
        break;
    case 0x63: // SET_BIN_SELECT_HI
        g_gpu.binSelect = (g_gpu.binSelect & 0xFFFFFFFFull) | (uint64_t(arg(0)) << 32);
        break;
    case 0x50: // SET_BIN_MASK (64-bit)
        g_gpu.binMask = (uint64_t(arg(0)) << 32) | arg(1);
        break;
    case 0x51: // SET_BIN_SELECT (64-bit)
        g_gpu.binSelect = (uint64_t(arg(0)) << 32) | arg(1);
        break;
    case 0x2D: // SET_CONSTANT: offset/type, values
    case 0x55: // SET_CONSTANT2
    {
        uint32_t index;
        if (opcode == 0x55)
            index = arg(0) & 0xFFFF;
        else
        {
            static const uint32_t bases[] = { gpureg::AluConstants, gpureg::FetchConstants, gpureg::BoolConstants, gpureg::LoopConstants, 0x2000 };
            const uint32_t type = (arg(0) >> 16) & 0xFF;
            index = (arg(0) & 0x7FF) + (type < 5 ? bases[type] : 0);
        }
        WriteRegisters(index, data + 4, count - 1);
        break;
    }
    case 0x2F: // LOAD_ALU_CONSTANT: address, offset/type, size (dwords)
    {
        static const uint32_t bases[] = { gpureg::AluConstants, gpureg::FetchConstants, gpureg::BoolConstants, gpureg::LoopConstants, 0x2000 };
        const uint32_t source = GpuPhysToGuest(arg(0) & 0x3FFFFFFF);
        const uint32_t type = (arg(1) >> 16) & 0xFF;
        const uint32_t index = (arg(1) & 0x7FF) + (type < 5 ? bases[type] : 0);
        const uint32_t size = arg(2) & 0xFFF;
        WriteRegisters(index, source, size);
        break;
    }
    case 0x27: // IM_LOAD: type|address, start/size
    {
        const uint32_t address = GpuPhysToGuest(arg(0) & ~3u);
        const uint32_t dwords = arg(1) & 0xFFFF;
        SubmitShaderLoad(ShaderType(arg(0) & 3), GuestPtr<uint32_t>(address), dwords, address);
        break;
    }
    case 0x2B: // IM_LOAD_IMMEDIATE: type, start/size, microcode
    {
        const uint32_t dwords = std::min(arg(1) & 0xFFFF, count - 2);
        SubmitShaderLoad(ShaderType(arg(0) & 3), GuestPtr<uint32_t>(data + 8), dwords, data + 8);
        break;
    }
    case 0x22: // DRAW_INDX: viz query, initiator, [index base, index size]
        g_gpu.draws++;
        SubmitDraw(arg(1), count > 2 ? arg(2) : 0, count > 3 ? arg(3) : 0);
        break;
    case 0x36: // DRAW_INDX_2: initiator, [inline indices]
        g_gpu.draws++;
        SubmitDraw(arg(0), 0, 0);
        break;
    case 0x46: // EVENT_WRITE
    case 0x48: // ME_INIT
    case 0x10: // NOP
    default:
        // Draws, state, constants: nothing to do until the native renderer.
        break;
    }
    return 1 + count;
}

void ExecuteBuffer(uint32_t address, uint32_t dwords)
{
    // One thread-local lookup per buffer, not per packet.
    auto& cursors = t_cursors;
    const size_t level = cursors.size();
    cursors.push_back({ address, address + dwords * 4 });
    for (uint32_t done = 0; done < dwords;)
    {
        const uint32_t ptr = address + done * 4;
        cursors[level].next = ptr + PacketLength(LoadBE32(ptr)) * 4;
        done += ExecutePacket(ptr);
    }
    cursors.pop_back();
}

// ---- Lookahead ---------------------------------------------------------------
struct LookState
{
    int packets = 0;
};

void ShadowWrite(uint32_t index, uint32_t value)
{
    if (index >= 0x2000 && index < 0x5000)
        SetReg(index, value);
}

void LookaheadBuffer(uint32_t address, uint32_t dwords, int depth, LookState& st)
{
    for (uint32_t done = 0; done < dwords && st.packets < 400000;)
    {
        const uint32_t ptr = address + done * 4;
        const uint32_t header = LoadBE32(ptr);
        const uint32_t length = PacketLength(header);
        done += length;
        st.packets++;
        switch (header >> 30)
        {
        case 0:
        {
            const uint32_t count = ((header >> 16) & 0x3FFF) + 1;
            const uint32_t base = header & 0x7FFF;
            const bool oneReg = header & 0x8000;
            for (uint32_t i = 0; i < count; i++)
                ShadowWrite(oneReg ? base : base + i, LoadBE32(ptr + 4 + i * 4));
            continue;
        }
        case 1:
            ShadowWrite(header & 0x7FF, LoadBE32(ptr + 4));
            ShadowWrite((header >> 11) & 0x7FF, LoadBE32(ptr + 8));
            continue;
        case 2:
            continue;
        default:
            break;
        }
        const uint32_t count = ((header >> 16) & 0x3FFF) + 1;
        const uint32_t opcode = (header >> 8) & 0x7F;
        const uint32_t data = ptr + 4;
        auto arg = [&](uint32_t i) { return LoadBE32(data + i * 4); };
        if ((header & 1) && !(g_gpu.binSelect & g_gpu.binMask))
            continue;
        switch (opcode)
        {
        case 0x3F:
        case 0x37:
            if (depth < 4)
                LookaheadBuffer(PhysToGuest(arg(0) & ~3u), arg(1) & 0xFFFFF, depth + 1, st);
            break;
        case 0x2D:
        case 0x55:
        {
            uint32_t index;
            if (opcode == 0x55)
                index = arg(0) & 0xFFFF;
            else
            {
                static const uint32_t bases[] = { gpureg::AluConstants, gpureg::FetchConstants, gpureg::BoolConstants, gpureg::LoopConstants, 0x2000 };
                const uint32_t type = (arg(0) >> 16) & 0xFF;
                index = (arg(0) & 0x7FF) + (type < 5 ? bases[type] : 0);
            }
            for (uint32_t i = 1; i < count; i++)
                ShadowWrite(index + i - 1, arg(i));
            break;
        }
        case 0x21:
            ShadowWrite(arg(0) & 0x1FFF, (Reg(arg(0) & 0x1FFF) & arg(1)) | arg(2));
            break;
        case 0x2F:
        {
            static const uint32_t bases[] = { gpureg::AluConstants, gpureg::FetchConstants, gpureg::BoolConstants, gpureg::LoopConstants, 0x2000 };
            const uint32_t source = GpuPhysToGuest(arg(0) & 0x3FFFFFFF);
            const uint32_t type = (arg(1) >> 16) & 0xFF;
            const uint32_t index = (arg(1) & 0x7FF) + (type < 5 ? bases[type] : 0);
            const uint32_t size = arg(2) & 0xFFF;
            for (uint32_t i = 0; i < size; i++)
                ShadowWrite(index + i, LoadBE32(source + i * 4));
            break;
        }
        case 0x27:
        {
            const uint32_t addr = GpuPhysToGuest(arg(0) & ~3u);
            const uint32_t dwords2 = arg(1) & 0xFFFF;
            if (dwords2 && dwords2 <= 0x10000)
                RendererPredictShader(arg(0) & 3, GuestPtr<uint32_t>(addr), dwords2);
            break;
        }
        case 0x2B:
        {
            const uint32_t dwords2 = std::min(arg(1) & 0xFFFF, count - 2);
            if (dwords2 && dwords2 <= 0x10000)
                RendererPredictShader(arg(0) & 3, GuestPtr<uint32_t>(data + 8), dwords2);
            break;
        }
        case 0x22:
            RendererPredictDraw(arg(1));
            break;
        case 0x36:
            RendererPredictDraw(arg(0));
            break;
        case 0x60: g_gpu.binMask = (g_gpu.binMask & 0xFFFFFFFF00000000ull) | arg(0); break;
        case 0x61: g_gpu.binMask = (g_gpu.binMask & 0xFFFFFFFFull) | (uint64_t(arg(0)) << 32); break;
        case 0x62: g_gpu.binSelect = (g_gpu.binSelect & 0xFFFFFFFF00000000ull) | arg(0); break;
        case 0x63: g_gpu.binSelect = (g_gpu.binSelect & 0xFFFFFFFFull) | (uint64_t(arg(0)) << 32); break;
        case 0x50: g_gpu.binMask = (uint64_t(arg(0)) << 32) | arg(1); break;
        case 0x51: g_gpu.binSelect = (uint64_t(arg(0)) << 32) | arg(1); break;
        default:
            break;
        }
    }
}
}

void GpuLookahead()
{
    static LiveSwitch off("REGPU_NO_LOOKAHEAD");
    if (off || work::Active() || !t_cpContext) // (only on the command processor thread: its stream and registers)
        return;
    constexpr uint32_t kFirst = 0x2000, kCount = 0x3000;
    std::vector<uint32_t> saved(kCount);
    memcpy(saved.data(), gpureg::LiveFile() + kFirst * 4, kCount * 4);
    const uint64_t savedMask = g_gpu.binMask, savedSelect = g_gpu.binSelect;
    RendererPredictBegin();
    LookState st;
    // The rest of every buffer being executed, innermost first.
    for (size_t i = t_cursors.size(); i-- > 0;)
    {
        const BufferCursor c = t_cursors[i];
        if (c.next < c.end)
            LookaheadBuffer(c.next, (c.end - c.next) / 4, 1, st);
    }
    // Then the ring from after the current packet to the write pointer, copied
    // contiguously (it may wrap).
    const uint32_t base = g_gpu.ringBase, size = g_gpu.ringDwords;
    if (base && size)
    {
        const uint32_t wptr = Reg(CP_RB_WPTR) % size;
        uint32_t start = g_ringNext;
        const uint32_t queued = (wptr + size - start) % size;
        static const uint32_t scratch = RegpuGuestAlloc(Heap::Virtual, 0x400000 * 4);
        const uint32_t n = std::min<uint32_t>(queued, 0x400000);
        for (uint32_t i = 0; i < n; i++)
            StoreBE32(scratch + i * 4, LoadBE32(base + ((start + i) % size) * 4));
        LookaheadBuffer(scratch, n, 0, st);
    }
    memcpy(gpureg::LiveFile() + kFirst * 4, saved.data(), kCount * 4);
    g_gpu.binMask = savedMask;
    g_gpu.binSelect = savedSelect;
}

namespace
{
void CommandProcessorLoop(PPCContext& ctx)
{
    t_cpContext = &ctx;
    work::g_enabled = getenv("REGPU_DRAW_THREAD") != nullptr;
    if (work::g_enabled)
    {
        memcpy(work::g_file.data(), gpureg::LiveFile(), work::g_file.size());
        std::thread worker(WorkerLoop);
        static HANDLE workerHandle = nullptr;
        DuplicateHandle(GetCurrentProcess(), worker.native_handle(), GetCurrentProcess(), &workerHandle, 0, FALSE, DUPLICATE_SAME_ACCESS);
        worker.detach();
        work::g_started.store(true, std::memory_order_release);
        Log("gpu: draw worker thread started");
        std::thread([] {
            for (;;)
            {
                Sleep(2000);
                if (getenv("REGPU_PERF"))
                {
                    static uint64_t lastBusy = 0;
                    FILETIME c, e, k, u;
                    uint64_t busy = 0;
                    if (workerHandle && GetThreadTimes(workerHandle, &c, &e, &k, &u))
                        busy = (uint64_t(k.dwHighDateTime) << 32 | k.dwLowDateTime) + (uint64_t(u.dwHighDateTime) << 32 | u.dwLowDateTime);
                    Log("perf: draw worker: %.0f%% busy, %llu drains (%llu waited) in 2 s", (busy - lastBusy) / 1e7 / 2.0 * 100.0,
                        (unsigned long long)work::g_drains.exchange(0), (unsigned long long)work::g_drainWaits.exchange(0));
                    lastBusy = busy;
                }
            }
        }).detach();
    }
    for (;;)
    {
        uint32_t base, size, generation;
        {
            std::lock_guard lock(g_gpu.ringMutex);
            base = g_gpu.ringBase;
            size = g_gpu.ringDwords;
            generation = g_gpu.ringGeneration;
        }
        if (!base || !size)
        {
            Sleep(1);
            continue;
        }
        const uint32_t wptr = Reg(CP_RB_WPTR) % size;
        // REGPU_PERF: how long the CP waits on an empty ring for the title rather than working. It
        // spins, so its CPU use reads 100% either way; a CP that is idle part of each frame is not
        // what limits the frame rate.
        static bool perf = getenv("REGPU_PERF") != nullptr;
        static std::chrono::steady_clock::time_point idleSince, logSince = std::chrono::steady_clock::now();
        static double idleSeconds = 0;
        static bool idle = false;
        if (g_gpu.rptr == wptr)
        {
            seg::Flush(); // nothing more queued: translate what there is
            if (perf && !idle)
            {
                idle = true;
                idleSince = std::chrono::steady_clock::now();
            }
            Sleep(0);
            continue;
        }
        if (perf)
        {
            const auto now = std::chrono::steady_clock::now();
            if (idle)
                idleSeconds += std::chrono::duration<double>(now - idleSince).count();
            idle = false;
            if (const double window = std::chrono::duration<double>(now - logSince).count(); window >= 2.0)
            {
                Log("perf: command processor idle %.0f%% of the time (waiting on the title to queue work)", idleSeconds / window * 100.0);
                idleSeconds = 0;
                logSince = now;
            }
        }
        bool replaced = false;
        while (!replaced && g_gpu.rptr != wptr)
        {

            // A packet may wrap past the end of the ring: run it from a
            // contiguous copy.
            const uint32_t header = LoadBE32(base + g_gpu.rptr * 4);
            uint32_t length = 1;
            switch (header >> 30)
            {
            case 0:
            case 3: length = ((header >> 16) & 0x3FFF) + 2; break;
            case 1: length = 3; break;
            default: break;
            }
            g_ringNext = (g_gpu.rptr + length) % size;
            uint32_t used;
            if (g_gpu.rptr + length <= size)
                used = ExecutePacket(base + g_gpu.rptr * 4);
            else
            {
                static const uint32_t scratch = RegpuGuestAlloc(Heap::Virtual, 0x4002 * 4);
                for (uint32_t i = 0; i < length; i++)
                    StoreBE32(scratch + i * 4, LoadBE32(base + ((g_gpu.rptr + i) % size) * 4));
                used = ExecutePacket(scratch);
            }
            std::lock_guard lock(g_gpu.ringMutex);
            if (g_gpu.ringGeneration != generation)
            {
                // The ring was replaced during this packet: the new one starts at 0, and this
                // ring's position must not land in it.
                replaced = true;
                break;
            }
            g_gpu.rptr = (g_gpu.rptr + used) % size;
            // Publish progress packet by packet: the title waits for ring
            // space by polling this, and only publishing once the ring is
            // drained made it and the CP take turns instead of overlapping.
            SetReg(CP_RB_RPTR, g_gpu.rptr);
            if (const uint32_t wb = g_gpu.rptrWriteback)
                StoreBE32(wb, g_gpu.rptr);
        }
    }
}

// Frames per second the game may present: max FPS, or the display's refresh rate if lower
// and vsync is on.
// Follows the settings as they change (regpu::SetFrameLimit: a host's options screen).
uint32_t FrameCap()
{
    static const uint32_t refresh = DisplayRefreshHz();
    const Settings& s = GetSettings();
    uint32_t c = s.maxFps;
    if (s.vsync)
        c = std::min(c, refresh);
    c = std::max(c, 30u);
    static std::atomic<uint32_t> logged{ 0 };
    if (logged.exchange(c) != c)
        Log("gpu: frame cap %u fps (max %u, vsync %s)", c, s.maxFps, s.vsync ? "on" : "off");
    return c;
}

void VblankLoop()
{
    SetThreadDescription(GetCurrentThread(), L"gpu vblank");
    auto next = Clock::now();
    for (;;)
    {
        // The title presents on vblanks: every one in gameplay, often every second one in menus. It
        // keeps game time by the clock, not by frames, so vblanks run at twice the frame cap
        // (120-720 Hz) and the limiter in VdSwap sets the frame rate for both.
        static const char* fixedHz = getenv("REGPU_VBLANK_HZ");
        const int hz = fixedHz ? std::clamp(atoi(fixedHz), 30, 1000) : std::clamp(int(FrameCap()) * 2, 120, 720);
        const int64_t defaultUs = int64_t(1000000 / hz);
        // A host that owns the video mode (driver::SetRefreshRate) sets the rate instead.
        const int64_t hostUs = g_vblankUs.load(std::memory_order_relaxed);
        next += std::chrono::microseconds(hostUs ? hostUs : defaultUs);
        static LiveSwitch coarse("REGPU_COARSE_SLEEP"); // A/B: the old 1 ms sleeps
        if (coarse)
            std::this_thread::sleep_until(next);
        else
            PreciseSleepUntil(next, 200);
        RaiseInterrupt(0, 2);
    }
}
}

void GpuInit()
{
    SetReg(RBBM_STATUS, 0); // idle
    SetReg(COHER_STATUS_HOST, 0);
    // Display registers D3D reads: vblank always pending in the interrupt
    // status (the vblank handler checks it), 720p viewport and counters.
    SetReg(0x1951, 1);          // D1MODE interrupt status: vblank
    SetReg(0x194C, 0x000002D0); // D1MODE_V_COUNTER
    SetReg(0x1961, 0x050002D0); // D1MODE_VIEWPORT_SIZE 1280x720
    SetReg(0x0F01, 0x0000200E); // RB_BC_CONTROL
    std::thread(VblankLoop).detach();
}

void GpuStartInterruptThread()
{
    if (g_gpu.interruptThread)
        return;
    g_gpu.interruptThread = true;
    regpu::GetHost().createThread("gpu vblank irq", InterruptLoop);
    regpu::GetHost().createThread("gpu command processor", CommandProcessorLoop);
}

uint64_t GpuSwapCount() { return g_gpu.swaps; }

void GpuLogState()
{
    const uint32_t w = g_gpu.waitingOn;
    if (g_gpu.rptrWriteback)
        Log("  gpu: rptr writeback reads %u", LoadBE32(g_gpu.rptrWriteback));
    Log("  gpu: rptr %u wptr %u last packet %08X waiting on %08X (= %08X, want fn %u ref %08X) swaps %llu", g_gpu.rptr, Reg(CP_RB_WPTR),
        g_gpu.lastPacket.load(), w, w ? ReadBack(w) : 0, g_gpu.waitingInfo & 7, g_gpu.waitingRef.load(), (unsigned long long)g_gpu.swaps.load());
    Log("  gpu: predicated packets skipped %llu", (unsigned long long)g_gpu.skippedPredicated.load());
    Log("  gpu: waits %llu (%llu over 1 ms, %.1f s total)", (unsigned long long)g_gpu.waits.load(), (unsigned long long)g_gpu.slowWaits.load(),
        g_gpu.waitMicros / 1e6);
    Log("  gpu: interrupts raised %u handled %u, draws %llu", g_gpu.interruptsRaised.load(), g_gpu.interruptsHandled.load(),
        (unsigned long long)g_gpu.draws.load());
    if (const uint32_t device = g_gpu.interruptData)
    {
        const uint32_t g = LoadBE32(device + 0x2A10);
        Log("  gpu: device %08X retire block %08X: %08X %08X %08X %08X", device, g, LoadBE32(g), LoadBE32(g + 4), LoadBE32(g + 8), LoadBE32(g + 12));
    }
}

// Swaps -------------------------------------------------------------------

namespace
{
// The command processor reached a swap: the frame so far goes to the screen.
void PresentSwap(uint64_t frame, uint32_t frontBuffer)
{
    seg::Flush();
    seg::EndFrame();
    seg::g_tile = RendererTile{};
    if (work::Active())
    {
        uint32_t* p = work::Begin(work::kSwap, 3);
        p[0] = uint32_t(frame);
        p[1] = uint32_t(frame >> 32);
        p[2] = frontBuffer;
        work::End(3);
    }
    else
        FrameOnSwap(frame, frontBuffer);
}

// Per swap, on the thread that swaps (the title's in VdSwap, or the command processor's for
// XE_SWAP): the hang watchdog and the frame limiter.
void PaceSwap(uint64_t frame)
{
    if (frame == 1 || (frame & (frame - 1)) == 0 || frame % 60 == 0)
        Log("gpu: swap #%llu", (unsigned long long)frame);
    // Watchdog: no swap for 10 s once the title has started presenting logs every thread's stack
    // (once per stall), so a lockup says where each thread is waiting.
    static std::atomic<uint64_t> lastSwap{ 0 };
    lastSwap.store(frame, std::memory_order_relaxed);
    static std::once_flag watchdog;
    std::call_once(watchdog, [] {
        std::thread([] {
            uint64_t seen = 0;
            int still = 0;
            bool logged = false;
            for (;;)
            {
                Sleep(1000);
                const uint64_t now = lastSwap.load(std::memory_order_relaxed);
                still = now == seen ? still + 1 : 0;
                if (now != seen)
                    logged = false;
                seen = now;
                // (Only once the title presents steadily: the first swaps are followed by loading and
                // a shader cache rebuild, minutes without a swap.)
                if (still >= 10 && now >= 600 && !logged && regpu::GetHost().logThreadStacks)
                {
                    logged = true;
                    Log("hang: no VdSwap for 10 s (last #%llu)", (unsigned long long)now);
                    regpu::GetHost().logThreadStacks("no swap for 10 s");
                }
            }
        }).detach();
    });
    // Frame limiter (settings: max FPS, the display's refresh with vsync): the title's render
    // thread waits for its next frame slot. Vblanks run faster than the cap, so menus (which
    // present every second vblank) and gameplay (every vblank) both reach it.
    const int64_t minFrameUs = int64_t(1000000 / FrameCap());
    if (minFrameUs)
    {
        static auto next = Clock::now();
        const auto now = Clock::now();
        static LiveSwitch coarse("REGPU_COARSE_SLEEP");
        if (next > now)
        {
            if (coarse)
                std::this_thread::sleep_until(next);
            else
                PreciseSleepUntil(next, 300);
        }
        next = std::max(next, now) + std::chrono::microseconds(minFrameUs);
    }
}
}

void regpu::SetFrameLimit(uint32_t maxFps, bool vsync)
{
    Settings& s = GetSettings();
    s.maxFps = maxFps;
    s.vsync = vsync;
}

// Driver entry points (include/regpu/driver.h) ------------------------------

void regpu::driver::SetInterruptCallback(uint32_t callback, uint32_t userData)
{
    g_gpu.interruptCallback = callback;
    g_gpu.interruptData = userData;
    Log("gpu: interrupt callback %08X data %08X", callback, userData);
    GpuStartInterruptThread();
}

void regpu::driver::InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2)
{
    // (physical address, log2 of size in qwords? - size = 1 << (log2 + 3) bytes)
    // A new ring starts empty: both pointers back to 0, as the kernel resets the CP. Without the
    // write pointer reset, the CP would run the new ring's stale contents up to the old one's.
    {
        std::lock_guard lock(g_gpu.ringMutex);
        g_gpu.ringBase = PhysToGuest(ptr);
        g_gpu.ringDwords = (1u << (sizeLog2 + 3)) / 4;
        g_gpu.rptr = 0;
        g_gpu.ringGeneration++;
        SetReg(CP_RB_WPTR, 0);
        SetReg(CP_RB_RPTR, 0);
    }
    Log("gpu: ring buffer at %08X, %u dwords", g_gpu.ringBase.load(), g_gpu.ringDwords.load());
}

void regpu::driver::EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockSizeLog2)
{
    g_gpu.rptrWriteback = PhysToGuest(ptr & 0x1FFFFFFF);
    Log("gpu: rptr writeback at %08X (from %08X), block log2 %u", g_gpu.rptrWriteback.load(), ptr, blockSizeLog2);
}

void regpu::driver::SetSwitch(const char* name, bool on)
{
    LiveSwitch::Set(name, on);
}

void regpu::driver::SetRefreshRate(double hz)
{
    const int64_t us = hz > 0 ? int64_t(1000000 / std::clamp(hz, 24.0, 1000.0)) : 0;
    if (g_vblankUs.exchange(us) != us)
        Log("gpu: vblank %.2f Hz (0: ReGPU's own)", us ? 1000000.0 / us : 0.0);
}

// A guest access is a plain load or store, as it is when the file is guest memory: side effects
// (the gamma table) come from packets, which go through WriteRegister above.
uint32_t regpu::driver::ReadRegister(uint32_t index)
{
    return Reg(index & 0x7FFF);
}

void regpu::driver::WriteRegister(uint32_t index, uint32_t value)
{
    index &= 0x7FFF;
    SetReg(index, value);
    // The display's gamma table, which a title can write directly (FH1 does, after its brightness
    // calibration): without it the presenter showed the identity ramp and the image's tones were
    // off against the console's. (The rest of WriteRegister records into the command stream: not
    // from a guest thread.)
    WriteLut(index, value);
}

#if REGPU_GUEST_EXPORTS
// Imports ------------------------------------------------------------------

GUEST_EXPORT(VdInitializeEngines)
{
    Return(ctx, 1);
}

GUEST_EXPORT(VdShutdownEngines) {}

GUEST_EXPORT(VdSetGraphicsInterruptCallback)
{
    regpu::driver::SetInterruptCallback(ctx.r3.u32, ctx.r4.u32);
}

GUEST_EXPORT(VdInitializeRingBuffer)
{
    regpu::driver::InitializeRingBuffer(ctx.r3.u32, ctx.r4.u32);
}

GUEST_EXPORT(VdEnableRingBufferRPtrWriteBack)
{
    // (address, block log2)
    regpu::driver::EnableReadPointerWriteBack(ctx.r3.u32, ctx.r4.u32);
}

GUEST_EXPORT(VdSetSystemCommandBufferGpuIdentifierAddress) {}

GUEST_EXPORT(VdGetSystemCommandBuffer)
{
    // (buffer*, gpuIdentifier*)
    static uint32_t buffer = RegpuGuestAlloc(Heap::Physical, 0x10000, 0x1000);
    StoreIfNonNull(ctx.r3.u32, buffer);
    StoreIfNonNull(ctx.r4.u32, 0xBEEF0000);
}

namespace
{
// X_VIDEO_MODE.
void WriteVideoMode(uint32_t mode)
{
    memset(GuestPtr(mode), 0, 0x30);
    StoreBE32(mode + 0x00, 1280); // display width
    StoreBE32(mode + 0x04, 720);  // display height
    StoreBE32(mode + 0x08, 0);    // interlaced
    StoreBE32(mode + 0x0C, 1);    // widescreen
    StoreBE32(mode + 0x10, 1);    // high definition
    const float refresh = 60.0f;
    uint32_t bits;
    memcpy(&bits, &refresh, 4);
    StoreBE32(mode + 0x14, bits);
    StoreBE32(mode + 0x18, 1);    // NTSC-M standard
    StoreBE32(mode + 0x1C, 0x4A);
    StoreBE32(mode + 0x20, 0x01);
}
}

GUEST_EXPORT(VdQueryVideoMode)
{
    WriteVideoMode(ctx.r3.u32);
}

GUEST_EXPORT(XGetVideoMode)
{
    WriteVideoMode(ctx.r3.u32);
}

GUEST_EXPORT(VdQueryVideoFlags)
{
    Return(ctx, 0x7); // widescreen | 1280 | hi-def
}

GUEST_EXPORT(VdGetCurrentDisplayGamma)
{
    // (type*, power*)
    StoreIfNonNull(ctx.r3.u32, 2);
    const float power = 2.22222233f;
    uint32_t bits;
    memcpy(&bits, &power, 4);
    StoreIfNonNull(ctx.r4.u32, bits);
}

GUEST_EXPORT(VdGetCurrentDisplayInformation)
{
    // X_DISPLAY_INFO (0x58 bytes); the title reads the sizes.
    const uint32_t info = ctx.r3.u32;
    memset(GuestPtr(info), 0, 0x58);
    auto set16 = [&](uint32_t off, uint16_t v) { *GuestPtr<uint16_t>(info + off) = __builtin_bswap16(v); };
    set16(0x00, 1280); // front buffer width
    set16(0x02, 720);  // front buffer height
    *GuestPtr<uint8_t>(info + 0x04) = 0;
    *GuestPtr<uint8_t>(info + 0x05) = 0;
    StoreBE32(info + 0x14, 320);  // horizontal pixels-per-inch-ish fields
    set16(0x40, 1280); // display width
    set16(0x42, 720);  // display height
    StoreBE32(info + 0x50, 60);   // refresh rate
}

GUEST_EXPORT(VdSetDisplayMode)
{
    Return(ctx, 0);
}

GUEST_EXPORT(VdPersistDisplay)
{
    StoreIfNonNull(ctx.r4.u32, 0);
    Return(ctx, 0);
}

GUEST_EXPORT(VdRetrainEDRAM) { Return(ctx, 0); }
GUEST_EXPORT(VdRetrainEDRAMWorker) { Return(ctx, 0); }
GUEST_EXPORT(VdIsHSIOTrainingSucceeded) { Return(ctx, 1); }
GUEST_EXPORT(VdEnableDisableClockGating) {}
GUEST_EXPORT(VdCallGraphicsNotificationRoutines) { Return(ctx, 0); }

GUEST_EXPORT(VdInitializeScalerCommandBuffer)
{
    // Last argument is the destination; fill it with type-2 NOPs.
    const uint32_t dest = StackArg(ctx, 8);
    if (dest)
    {
        for (uint32_t i = 0; i < 0x1CC / 4; i++)
            StoreBE32(dest + i * 4, 0x80000000);
    }
    Return(ctx, 0x1CC / 4);
}

GUEST_EXPORT(VdSwap)
{
    // (buffer (64 dwords in the ring), fetch constant, ..., frontbuffer*, format*, colorspace*, width*, height*)
    const uint32_t buffer = ctx.r3.u32;
    // The 64 dwords reserved for the swap become type-2 NOPs: D3D walks
    // retired command memory on the CPU and reads other headers as links.
    for (uint32_t i = 0; i < 64; i++)
        StoreBE32(buffer + i * 4, 0x80000000);
    const uint64_t n = ++g_gpu.swaps;
    {
        std::lock_guard lock(g_gpu.swapMutex);
        // r4: the front buffer's texture fetch constant (6 dwords).
        const uint32_t frontBuffer = ctx.r4.u32 ? (LoadBE32(ctx.r4.u32 + 4) & 0x1FFFF000) : 0;
        g_gpu.swapBlocks[g_gpu.swapBlockNext++ % 16].store(buffer & 0x1FFFFFFF, std::memory_order_release);
        uint64_t filter[4] = {};
        for (const auto& b : g_gpu.swapBlocks)
        {
            const uint32_t h = (b.load(std::memory_order_relaxed) >> 2) & 255;
            filter[h >> 6] |= 1ull << (h & 63);
        }
        for (int i = 0; i < 4; i++)
            g_gpu.swapFilter[i].store(filter[i], std::memory_order_release);
        g_gpu.pendingSwaps.push_back({ buffer & 0x1FFFFFFF, n, frontBuffer });
        if (g_gpu.pendingSwaps.size() > 16)
            g_gpu.pendingSwaps.pop_front();
    }
    PaceSwap(n);
    Return(ctx, 0);
}
#endif // REGPU_GUEST_EXPORTS
