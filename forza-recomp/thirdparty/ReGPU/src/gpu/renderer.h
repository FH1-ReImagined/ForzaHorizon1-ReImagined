#pragma once
#include <cstdint>

// Native renderer, called on the command processor thread.
void RendererOnShaderLoad(uint32_t type, const uint32_t* microcodeBE, uint32_t dwords);
// The same with the used length and hash already known (Fnv of the used dwords ^ type).
void RendererSetShader(uint32_t type, const uint32_t* microcodeBE, uint32_t usedDwords, uint64_t hash);
void RendererOnDraw(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize);
// frontBuffer: physical address of the resolved image to show.
void RendererOnSwap(uint64_t frame, uint32_t frontBuffer);
// Lookahead hooks (see GpuLookahead): shader loads and draws the command processor has
// not executed yet, seen on a shadow copy of the registers.
void RendererPredictBegin();
void RendererPredictShader(uint32_t type, const uint32_t* microcodeBE, uint32_t dwords);
void RendererPredictDraw(uint32_t initiator);
// True when no shader/pipeline compile is running and none finished in the last 2 s
// (the shader tour waits for this between steps).
bool RendererCompileIdle();
// Flushes the pipeline library to disk (called when the tour finishes).
void RendererSaveCaches();
// From the port's crash handler: a crash inside the driver's pipeline compiler is logged and that pipeline is
// refused from the next start on (regpu-bad-pipelines.txt).
void RendererLogCrashContext();
// The display gamma table entry `index` (10:10:10 RGB), applied when presenting.
void RendererSetGammaEntry(uint32_t index, uint32_t rgb10);
// One entry of the display's piecewise-linear gamma ramp (DC_LUT_PWL_DATA: base, delta), 0-127, per channel.
void RendererSetGammaPwl(uint32_t index, uint32_t component, uint32_t data);
// Benchmark: frame times are recorded while on; the report summarises them.
void BenchmarkRecord(bool on);
void BenchmarkWriteReport(const char* path);
// True while REGPU_TRACE_DRAWS's frame is being recorded.
bool RendererTracing();

// Parallel draw translation (gpu.cpp cuts the draw stream between resolves into pieces).
#include <functional>
struct RendererTile
{
    int32_t originY = 0;
    int32_t rect[4] = { 0, 0, 1 << 14, 1 << 14 };
};
// Whether draws may be translated on workers now (game settled, no upscaler, ...).
bool RendererParallelReady();
// TileMerge for the draw workers: whether the current draw's scissor is the widened first tile, decided
// on the command processor as the draw is logged (the tile pass has moved on by the time a worker replays
// it). Returns 0 (not widened), 1 (widened to rect) or 2 (widened, and it is the first: the later tiles'
// rows are cleared before it, so it is drawn on the command processor in order).
uint32_t RendererTileWidenNow(int32_t rect[4]);
// On a worker, the answer RendererTileWidenNow gave for the draw being replayed (set false after it).
void RendererForceTileWiden(bool set, bool widened, const int32_t rect[4]);
uint32_t RendererDrawWorkers();
// The current draw is a resolve (EDRAM copy): a point the stream is not cut across.
bool RendererIsResolveDraw();
// Occlusion queries (EVENT_WRITE_ZPD): Begin starts counting the samples later draws pass;
// End stops and gives the count in console samples (of the same query in the last completed
// frame), false while there is none yet.
// Before a fence write: what has been recorded is submitted, and the host GPU fence value
// that marks its end comes back (0: nothing to wait for). The write itself waits for that
// value on another thread (gpu.cpp), so the command processor reads on.
uint64_t RendererFenceSubmit();
// REGPU_EARLY_FENCES=1: fences land when the command processor reaches them, as on the console
// (where the GPU is never far behind), instead of after the host GPU. The title can then rewrite
// memory the host GPU has yet to read, so guest ranges seen changing are copied, not read in place.
bool RendererEarlyFences();
// Blocks until the host GPU has reached a value from RendererFenceSubmit.
void RendererFenceWait(uint64_t value, void* event);
// The fence writer, after RendererFenceWait(value): resolve write-backs recorded before it land in guest memory.
void RendererFinishFencedWritebacks(uint64_t completed);
// Predicated tiling merged (see renderer.cpp, TileMerge): the window scissor of the current
// registers in screen space, and what was learned about a surface's tiles.
void RendererScreenScissor(int32_t rect[4]);
// A replayed tile pass seen on surface `surface` with this scissor, the first tile's being
// `first`: learns the tiles; true when the replay's draws can be dropped (the first tile
// already drew them all).
bool RendererTileReplay(uint32_t surface, const int32_t first[4], const int32_t replay[4]);
// Each draw on its way to the renderer, with its window scissor (screen space): true when it is
// a later tile's draw that the first tile drew (dropped). Keeps track of where the tile pass is.
// `replay`: the draw is in a buffer the command processor found to be a later tile's replay.
bool RendererTileDrop(uint32_t surface, const int32_t rect[4], bool resolve, uint32_t initiator, bool replay, uint32_t dmaBase, uint32_t dmaSize);
void RendererOcclusionBegin();
// The query's samples that passed and, as the console's Total, the samples its draws covered, as
// counted for the last finished query that wrote its counts to `address` (RB_SAMPLE_COUNT_ADDR).
bool RendererOcclusionEnd(uint32_t& samples, uint32_t& total, uint32_t address);
// Follows the tile state draws set (see DrawImpl), for the state at each piece's start.
void RendererTrackTile(RendererTile& tile);
void RendererSetTile(const RendererTile& tile);
// Runs translate(i), i in [0, n), on the draw workers (and this thread), each into its own
// command list, and executes them in order after everything recorded so far.
void RendererRunPieces(uint32_t n, const std::function<void(uint32_t)>& translate);
