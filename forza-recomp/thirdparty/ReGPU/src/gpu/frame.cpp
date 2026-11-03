// Frame capture from the command stream. Collects, per frame, the shaders
// loaded and the textures bound at draw time. At frame end a worker decodes
// the frame's textures and lays them out for the window: the first view of
// real game content, until translated shaders draw the frame itself.
#include <algorithm>
#include <condition_variable>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "gpu/regs.h"
#include "gpu/ucode.h"
#include "gpu/renderer.h"
#include "gpu/texture.h"
#include "kernel/runtime.h"
#include "live_switch.h"
#include "log.h"
#include "png.h"
#include "render/presenter.h"

namespace
{
uint64_t Fnv1a(const uint32_t* data, uint32_t dwords)
{
    uint64_t h = 0xCBF29CE484222325ull;
    const auto* bytes = reinterpret_cast<const uint8_t*>(data);
    for (uint32_t i = 0; i < dwords * 4; i++)
        h = (h ^ bytes[i]) * 0x100000001B3ull;
    return h;
}

std::unordered_set<uint64_t> g_shaders;

// The title uploads its shaders again for nearly every draw. Each distinct upload is
// parsed and hashed once; repeats are found by a fast word hash and confirmed by
// comparing the words.
struct ShaderUpload
{
    std::vector<uint32_t> words; // as uploaded (big-endian)
    uint32_t used = 0;           // ucode::UsedDwords
    uint64_t hash = 0;           // Fnv1a of the used dwords ^ type
};
// Two generations: when the current one fills, the older one is dropped and the current
// one becomes old (lookups promote what is still used). Clearing everything at once made
// every upload miss for a while and stalled the command processor for ~20 ms.
std::unordered_map<uint64_t, ShaderUpload> g_uploads, g_uploadsOld;
// The same uploads by guest address (to their key): a repeat from the same place costs one
// compare. A fixed direct-mapped table: the title patches shaders at ever new addresses, and
// a growing map's rehashes stalled the command processor.
struct UploadAt
{
    uint64_t at = ~0ull, key = 0;
    // The record itself, found at rotation `rotation`: alive until the second rotation after (records
    // move to the old map at the next and are freed at the one after), so a repeat load skips both
    // map lookups (~1% of the command processor) while it is.
    ShaderUpload* upload = nullptr;
    uint64_t rotation = 0;
};
uint64_t g_rotations = 0;
UploadAt g_uploadsAt[16384];
UploadAt& UploadSlot(uint64_t at) { return g_uploadsAt[((at * 0x9E3779B97F4A7C15ull) >> 50) & 16383]; }
constexpr size_t kUploadGeneration = 8192;

ShaderUpload* FindUpload(uint64_t key)
{
    if (auto it = g_uploads.find(key); it != g_uploads.end())
        return &it->second;
    auto old = g_uploadsOld.find(key);
    if (old == g_uploadsOld.end())
        return nullptr;
    auto node = g_uploadsOld.extract(old);
    return &g_uploads.insert(std::move(node)).position->second;
}

void RotateUploads()
{
    if (g_uploads.size() < kUploadGeneration)
        return;
    g_uploadsOld.clear();
    std::swap(g_uploads, g_uploadsOld);
    g_rotations++;
    g_uploads.reserve(kUploadGeneration * 2);
}

uint64_t FastKey(const uint32_t* p, uint32_t dwords, uint32_t type)
{
    uint64_t h = 0x9E3779B97F4A7C15ull ^ (uint64_t(dwords) << 8) ^ type;
    uint32_t i = 0;
    for (; i + 2 <= dwords; i += 2)
    {
        uint64_t w;
        memcpy(&w, p + i, 8);
        h = (h ^ w) * 0xFF51AFD7ED558CCDull;
        h ^= h >> 32;
    }
    if (i < dwords)
        h = (h ^ p[i]) * 0xFF51AFD7ED558CCDull;
    return h ^ (h >> 29);
}
const bool g_dumpShaders = getenv("REGPU_DUMP_SHADERS") != nullptr;

struct TextureKey
{
    uint32_t address, format, width, height;
    bool operator<(const TextureKey& o) const
    {
        return std::tie(address, format, width, height) < std::tie(o.address, o.format, o.width, o.height);
    }
};

std::map<TextureKey, TextureFetch> g_frameTextures; // command processor thread only
uint32_t g_frameDraws = 0;

// Hand-off to the compositor thread.
std::mutex g_mutex;
std::condition_variable g_cv;
std::vector<TextureFetch> g_pending;
bool g_hasPending = false;

constexpr uint32_t kCanvasW = 1280, kCanvasH = 720;

void Blit(std::vector<uint8_t>& canvas, const std::vector<uint8_t>& image, uint32_t w, uint32_t h, uint32_t x0, uint32_t y0,
    uint32_t cellW, uint32_t cellH)
{
    // Fit the image in the cell, nearest-neighbour, over a checkerboard so
    // alpha is visible.
    const float scale = std::min(float(cellW) / w, float(cellH) / h);
    const uint32_t dw = std::max(1u, uint32_t(w * scale)), dh = std::max(1u, uint32_t(h * scale));
    for (uint32_t y = 0; y < dh && y0 + y < kCanvasH; y++)
    {
        for (uint32_t x = 0; x < dw && x0 + x < kCanvasW; x++)
        {
            const uint8_t* s = &image[(size_t(std::min(h - 1, uint32_t(y / scale))) * w + std::min(w - 1, uint32_t(x / scale))) * 4];
            uint8_t* d = &canvas[(size_t(y0 + y) * kCanvasW + x0 + x) * 4];
            const uint8_t bg = (((x >> 3) + (y >> 3)) & 1) ? 90 : 60;
            for (int c = 0; c < 3; c++)
                d[c] = uint8_t((s[c] * s[3] + bg * (255 - s[3])) / 255);
            d[3] = 255;
        }
    }
}

void CompositorLoop()
{
    for (;;)
    {
        std::vector<TextureFetch> textures;
        {
            std::unique_lock lock(g_mutex);
            g_cv.wait(lock, [] { return g_hasPending; });
            textures.swap(g_pending);
            g_hasPending = false;
        }
        // Largest first; up to 12 cells.
        std::sort(textures.begin(), textures.end(), [](const TextureFetch& a, const TextureFetch& b) {
            return a.Width() * a.Height() > b.Width() * b.Height();
        });
        if (textures.size() > 12)
            textures.resize(12);
        std::vector<uint8_t> canvas(size_t(kCanvasW) * kCanvasH * 4, 20);
        const uint32_t cols = 4, rows = 3, cellW = kCanvasW / cols, cellH = kCanvasH / rows;
        std::vector<uint8_t> image;
        for (size_t i = 0; i < textures.size(); i++)
        {
            if (DecodeTexture(textures[i], image))
                Blit(canvas, image, textures[i].Width(), textures[i].Height(), uint32_t(i % cols) * cellW + 2,
                    uint32_t(i / cols) * cellH + 2, cellW - 4, cellH - 4);
        }
        static const uint64_t dumpFrame = getenv("REGPU_DUMP_FRAME") ? strtoull(getenv("REGPU_DUMP_FRAME"), nullptr, 10) : 0;
        static uint64_t composed = 0;
        if (++composed == dumpFrame)
        {
            WritePng("frame.png", kCanvasW, kCanvasH, canvas);
            Log("gpu: wrote frame.png");
        }
        PresenterSubmitImage(kCanvasW, kCanvasH, std::move(canvas));
    }
}
}

const bool g_textureView = getenv("REGPU_TEXVIEW") != nullptr;

void FrameOnShaderLoad(ShaderType type, const uint32_t* microcodeBE, uint32_t dwords, uint32_t guestAddress)
{
    if (!dwords || dwords > 0x10000)
        return;
    const uint64_t at = uint64_t(guestAddress) << 32 ^ uint64_t(dwords) << 2 ^ uint32_t(type);
    if (g_uploads.bucket_count() < kUploadGeneration * 2)
        g_uploads.reserve(kUploadGeneration * 2);
    UploadAt& slot = UploadSlot(at);
    if (slot.at == at)
    {
        ShaderUpload* u = slot.upload && g_rotations <= slot.rotation + 1 ? slot.upload : FindUpload(slot.key);
        if (u && u != slot.upload)
            slot.upload = u, slot.rotation = g_rotations;
        if (u && u->words.size() == dwords && memcmp(u->words.data(), microcodeBE, dwords * 4) == 0)
        {
            RendererSetShader(uint32_t(type), microcodeBE, u->used, u->hash);
            return;
        }
    }
    const uint64_t key = FastKey(microcodeBE, dwords, uint32_t(type));
    if (const ShaderUpload* u = FindUpload(key); u && u->words.size() == dwords && memcmp(u->words.data(), microcodeBE, dwords * 4) == 0)
    {
        RendererSetShader(uint32_t(type), microcodeBE, u->used, u->hash);
        slot = { at, key, const_cast<ShaderUpload*>(u), g_rotations };
        return; // seen before: already logged and dumped
    }
    const uint32_t raw = dwords;
    dwords = ucode::UsedDwords(microcodeBE, dwords);
    const uint64_t hash = Fnv1a(microcodeBE, dwords) ^ uint64_t(type);
    RendererSetShader(uint32_t(type), microcodeBE, dwords, hash);
    RotateUploads();
    ShaderUpload& upload = g_uploads[key] = ShaderUpload{ std::vector<uint32_t>(microcodeBE, microcodeBE + raw), dwords, hash };
    slot = { at, key, &upload, g_rotations };
    if (!g_shaders.insert(hash).second)
        return;
    if (g_shaders.size() % 50 == 1)
        Log("gpu: %zu distinct shaders seen", g_shaders.size());
    static LiveSwitch traceAddr("REGPU_TRACE_SHADER_ADDR");
    static LiveSwitch hexAddr("REGPU_HEXDUMP_SHADERS");
    if (hexAddr && guestAddress >= 0x80000000u)
    {
        static int dumped = 0;
        if (dumped++ < 6)
        {
            const uint8_t* b = GuestPtr<uint8_t>(guestAddress - 96);
            char line[400];
            for (int row = 0; row < 14; row++)
            {
                int n = 0;
                for (int i = 0; i < 16; i++)
                    n += snprintf(line + n, sizeof(line) - n, "%02X ", b[row * 16 + i]);
                Log("hex %08X: %s", guestAddress - 96 + row * 16, line);
            }
            Log("hex --- (shader starts at %08X)", guestAddress);
        }
    }
    if (traceAddr)
        Log("shader: %s %016llx at %08X, %u dwords (first dwords %08X %08X)", type == ShaderType::Vertex ? "vs" : "ps", (unsigned long long)hash,
            guestAddress, dwords, __builtin_bswap32(microcodeBE[0]), __builtin_bswap32(microcodeBE[1]));
    if (g_dumpShaders)
    {
        const auto dir = RegpuUserDir() / "shaders";
        std::filesystem::create_directories(dir);
        char name[64];
        snprintf(name, sizeof(name), "%s_%016llx.bin", type == ShaderType::Vertex ? "vs" : "ps", (unsigned long long)hash);
        std::ofstream(dir / name, std::ios::binary).write(reinterpret_cast<const char*>(microcodeBE), dwords * 4);
    }
}

void FrameOnDraw(uint32_t initiator, uint32_t dmaBase, uint32_t dmaSize)
{
    g_frameDraws++;
    if (!g_textureView)
    {
        RendererOnDraw(initiator, dmaBase, dmaSize);
        return;
    }
    for (uint32_t slot = 0; slot < 32; slot++)
    {
        const TextureFetch f = ReadTextureFetch(slot);
        if (f.Type() != 2 || f.Dimension() != 1 || !TextureFormatSupported(f.Format()) || f.Width() < 16 || f.Height() < 16)
            continue;
        g_frameTextures.emplace(TextureKey{ f.Address(), f.Format(), f.Width(), f.Height() }, f);
    }
}

void FrameOnSwap(uint64_t frame, uint32_t frontBuffer)
{
    if (!g_textureView)
    {
        RendererOnSwap(frame, frontBuffer);
        g_frameDraws = 0;
        return;
    }
    static std::once_flag started;
    std::call_once(started, [] { std::thread(CompositorLoop).detach(); });
    {
        std::lock_guard lock(g_mutex);
        g_pending.clear();
        for (auto& [key, fetch] : g_frameTextures)
            g_pending.push_back(fetch);
        g_hasPending = true;
    }
    g_cv.notify_one();
    if (frame % 600 == 0)
        Log("gpu: frame %llu: %u draws, %zu textures bound", (unsigned long long)frame, g_frameDraws, g_frameTextures.size());
    g_frameTextures.clear();
    g_frameDraws = 0;
}
