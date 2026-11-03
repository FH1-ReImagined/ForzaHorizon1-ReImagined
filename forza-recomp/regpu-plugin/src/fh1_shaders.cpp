// Finds every shader in FH1's files before the game asks for one (ReGPU compiles them all on the
// first boot, with the boot screen up, instead of one by one as the game first draws with them).
//
// The game keeps its compiled shaders as Xbox 360 D3D shader blobs: in the effect files
// (media/shaders/**/*.fxobj, shaders/track/*.fxobj inside media/tracks/*/bin.zip, newer car effects in
// the title update's media.zip) and in the executable (the engine's own). A blob starts with
// 0x102A1101 (vertex) or 0x102A1100 (pixel), and its header leads to the microcode, so nothing around
// it needs parsing (FH1R's tools/fxobj_shaders.py). Most zip members are method 21 (XMemCompress LZX,
// 32 KB blocks of one stream), decoded with ReXGlue's lzx_decompress. Only the used dwords of each
// shader are kept, as ReGPU hashes what the game loads (ucode::UsedDwords). The list is saved, so
// later boots skip the search; adding the title update or DLC (other files or sizes) searches again.
// FX-8350 first boot: 1548 shaders (1092 in the effect files, 456 more in the executable), compiled in
// 284 s. GLUE2GPU_SHADER_SCAN=deep: every zip member as well (found nothing more in FH1 TU4).
#include "fh1_shaders.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_set>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <rex/logging.h>
#include <rex/system/lzx.h>

#include "gpu/ucode.h"

namespace glue2gpu
{
namespace
{
std::filesystem::path g_game, g_update, g_saveDir;
const uint8_t* g_guestBase = nullptr;

uint32_t Be32(const uint8_t* p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }
uint16_t Le16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }
uint32_t Le32(const uint8_t* p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }
uint64_t Le64(const uint8_t* p) { return uint64_t(Le32(p)) | uint64_t(Le32(p + 4)) << 32; }

uint64_t Fnv(const void* data, size_t n, uint64_t h = 0xCBF29CE484222325ull)
{
    const auto* p = static_cast<const uint8_t*>(data);
    for (size_t i = 0; i < n; i++)
        h = (h ^ p[i]) * 0x100000001B3ull;
    return h;
}

std::string Lower(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(), [](char c) { return char(tolower(uint8_t(c))); });
    return s;
}

// ---- Zips ----------------------------------------------------------------------------

struct ZipEntry
{
    std::string name;
    uint16_t method = 0;
    uint64_t compressed = 0, size = 0, offset = 0;
};

bool ReadAt(std::ifstream& f, uint64_t at, void* out, size_t n)
{
    f.clear();
    f.seekg(std::streamoff(at));
    return bool(f.read(static_cast<char*>(out), std::streamsize(n)));
}

// The central directory (with zip64 sizes and offsets where a field is full).
bool ZipIndex(const std::filesystem::path& path, std::vector<ZipEntry>& out)
{
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f)
        return false;
    const uint64_t fileSize = uint64_t(f.tellg());
    const uint64_t tail = std::min<uint64_t>(fileSize, 65557);
    std::vector<uint8_t> end(tail);
    if (!ReadAt(f, fileSize - tail, end.data(), tail))
        return false;
    size_t eocd = SIZE_MAX;
    for (size_t i = tail >= 22 ? tail - 22 : 0;; i--)
    {
        if (Le32(&end[i]) == 0x06054B50)
        {
            eocd = i;
            break;
        }
        if (i == 0)
            break;
    }
    if (eocd == SIZE_MAX)
        return false;
    uint64_t count = Le16(&end[eocd + 10]), cdSize = Le32(&end[eocd + 12]), cdOffset = Le32(&end[eocd + 16]);
    if ((count == 0xFFFF || cdOffset == 0xFFFFFFFF) && eocd >= 20 && Le32(&end[eocd - 20]) == 0x07064B50)
    {
        uint8_t z64[56];
        if (!ReadAt(f, Le64(&end[eocd - 20 + 8]), z64, sizeof(z64)) || Le32(z64) != 0x06064B50)
            return false;
        count = Le64(z64 + 32);
        cdSize = Le64(z64 + 40);
        cdOffset = Le64(z64 + 48);
    }
    std::vector<uint8_t> cd(cdSize);
    if (!ReadAt(f, cdOffset, cd.data(), cd.size()))
        return false;
    for (size_t p = 0, i = 0; i < count && p + 46 <= cd.size() && Le32(&cd[p]) == 0x02014B50; i++)
    {
        const uint16_t nameLen = Le16(&cd[p + 28]), extraLen = Le16(&cd[p + 30]), commentLen = Le16(&cd[p + 32]);
        if (p + 46 + nameLen + extraLen > cd.size())
            break;
        ZipEntry e;
        e.method = Le16(&cd[p + 10]);
        e.compressed = Le32(&cd[p + 20]);
        e.size = Le32(&cd[p + 24]);
        e.offset = Le32(&cd[p + 42]);
        e.name.assign(reinterpret_cast<const char*>(&cd[p + 46]), nameLen);
        // Zip64 extra field: the full-width values of the fields that are 0xFFFFFFFF, in order.
        for (size_t x = p + 46 + nameLen; x + 4 <= p + 46 + nameLen + extraLen;)
        {
            const uint16_t id = Le16(&cd[x]), len = Le16(&cd[x + 2]);
            if (id == 1)
            {
                size_t v = x + 4;
                if (e.size == 0xFFFFFFFF && v + 8 <= x + 4 + len)
                    e.size = Le64(&cd[v]), v += 8;
                if (e.compressed == 0xFFFFFFFF && v + 8 <= x + 4 + len)
                    e.compressed = Le64(&cd[v]), v += 8;
                if (e.offset == 0xFFFFFFFF && v + 8 <= x + 4 + len)
                    e.offset = Le64(&cd[v]);
            }
            x += 4 + len;
        }
        out.push_back(std::move(e));
        p += 46 + nameLen + extraLen + commentLen;
    }
    return true;
}

// A member's bytes: stored, or method 21. (The track archives have no local headers: the central
// directory's offset points at the data itself.)
bool ZipRead(std::ifstream& f, const ZipEntry& e, std::vector<uint8_t>& out)
{
    if ((e.method != 0 && e.method != 21) || e.size > (512u << 20))
        return false;
    uint64_t at = e.offset;
    uint8_t local[30];
    if (!ReadAt(f, at, local, sizeof(local)))
        return false;
    if (Le32(local) == 0x04034B50)
        at += 30 + Le16(local + 26) + Le16(local + 28);
    std::vector<uint8_t> raw(e.compressed);
    if (!ReadAt(f, at, raw.data(), raw.size()))
        return false;
    if (e.method == 0)
    {
        out = std::move(raw);
        return true;
    }
    // Blocks: FF, u16 uncompressed size, u16 compressed size, data; or u16 compressed size, data (a
    // whole 32 KB block). The data of all blocks is one LZX stream with a 128 KB window.
    std::vector<uint8_t> stream;
    stream.reserve(raw.size());
    for (size_t p = 0; p < raw.size();)
    {
        uint32_t comp;
        if (raw[p] == 0xFF)
        {
            if (p + 5 > raw.size())
                break;
            comp = (uint32_t(raw[p + 3]) << 8) | raw[p + 4];
            p += 5;
        }
        else
        {
            if (p + 2 > raw.size())
                break;
            comp = (uint32_t(raw[p]) << 8) | raw[p + 1];
            p += 2;
        }
        if (comp == 0 || p + comp > raw.size())
            break;
        stream.insert(stream.end(), raw.begin() + p, raw.begin() + p + comp);
        p += comp;
    }
    out.assign(e.size, 0);
    return lzx_decompress(stream.data(), stream.size(), out.data(), out.size(), 1u << 17, nullptr, 0) == 0;
}

// ---- Blobs ---------------------------------------------------------------------------

struct Found
{
    std::mutex mutex;
    std::unordered_set<uint64_t> seen;
    std::vector<regpu::ScannedShader> shaders;
    std::atomic<size_t> blobs{ 0 };
};

// Every D3D shader blob in the bytes. Header (big-endian): magic, virtual size, physical size, debug
// info offset (0x24 or 0), constant table, definitions, shader description; the description starts
// with the microcode's offset and size in the physical part, which follows the virtual part.
void FindBlobs(const uint8_t* data, size_t size, Found& found)
{
    for (size_t pos = 0; pos + 0x24 <= size;)
    {
        const void* hit = memchr(data + pos, 0x10, size - pos - 0x23);
        if (!hit)
            return;
        const size_t start = size_t(static_cast<const uint8_t*>(hit) - data);
        pos = start + 1;
        const uint32_t magic = Be32(data + start);
        if (magic != 0x102A1101 && magic != 0x102A1100)
            continue;
        const uint32_t vsize = Be32(data + start + 4), psize = Be32(data + start + 8), dbg = Be32(data + start + 12),
                       desc = Be32(data + start + 24);
        if ((dbg != 0 && dbg != 0x24) || desc < 0x24 || uint64_t(desc) + 8 > vsize || start + uint64_t(vsize) + psize > size)
            continue;
        const uint32_t poff = Be32(data + start + desc), bytes = Be32(data + start + desc + 4);
        if (!bytes || bytes % 4 || poff % 4 || uint64_t(poff) + bytes > psize || bytes > 0x40000)
            continue;
        const bool pixel = magic == 0x102A1100;
        const auto* code = reinterpret_cast<const uint32_t*>(data + start + vsize + poff);
        const uint32_t used = ucode::UsedDwords(code, bytes / 4);
        found.blobs++;
        const uint64_t hash = Fnv(code, size_t(used) * 4) ^ (pixel ? 1 : 0);
        pos = start + vsize + psize;
        std::lock_guard lock(found.mutex);
        if (!found.seen.insert(hash).second)
            continue;
        regpu::ScannedShader s{ pixel, std::vector<uint32_t>(used) };
        memcpy(s.code.data(), code, size_t(used) * 4);
        found.shaders.push_back(std::move(s));
    }
}

// ---- Sources -------------------------------------------------------------------------

// Every zip member, not only the effect files: on the FX the same 1092 shaders in the files, in 52 s
// instead of 6.5 s, so it's off unless asked for.
bool Deep() { return std::getenv("GLUE2GPU_SHADER_SCAN") && Lower(std::getenv("GLUE2GPU_SHADER_SCAN")) == "deep"; }

// Members that are never shaders: textures, audio, video, strings, the world's geometry streams.
bool Skipped(const std::string& lowerName)
{
    static const char* const kSkip[] = { ".xds", ".fsb", ".wmv", ".str", ".tga", ".bix", ".pvsz", ".soundscape", ".txt", ".html", ".xml" };
    for (const char* ext : kSkip)
        if (lowerName.size() >= strlen(ext) && lowerName.compare(lowerName.size() - strlen(ext), strlen(ext), ext) == 0)
            return true;
    return false;
}

std::vector<std::filesystem::path> SourceFiles()
{
    std::vector<std::filesystem::path> files;
    std::error_code ec;
    for (const std::filesystem::path& root : { g_game, g_update })
    {
        if (root.empty())
            continue;
        for (auto it = std::filesystem::recursive_directory_iterator(root, ec); !ec && it != std::filesystem::recursive_directory_iterator(); it.increment(ec))
        {
            if (!it->is_regular_file(ec))
                continue;
            const std::string ext = Lower(it->path().extension().string());
            if (ext == ".zip" || ext == ".fxobj" || ext == ".xex")
                files.push_back(it->path());
        }
    }
    std::sort(files.begin(), files.end());
    return files;
}

constexpr uint32_t kMagic = 0x53534846; // 'FHSS'
constexpr uint32_t kVersion = 1;

// Which files (names and sizes) the saved list was made from, and how deep the search was.
uint32_t Signature(const std::vector<std::filesystem::path>& files)
{
    uint64_t h = Fnv(Deep() ? "deep" : "efxe", 4); // (efxe: effects and the executable)
    for (const auto& f : files)
    {
        std::error_code ec;
        const std::string name = f.filename().string();
        const uint64_t size = std::filesystem::file_size(f, ec);
        h = Fnv(name.data(), name.size(), h);
        h = Fnv(&size, sizeof(size), h);
    }
    return uint32_t(h ^ (h >> 32));
}

std::filesystem::path SavedPath() { return g_saveDir / "fh1_static_shaders.bin"; }

bool ReadHeader(std::ifstream& in, uint32_t head[4])
{
    return in.read(reinterpret_cast<char*>(head), 16) && head[0] == kMagic && head[1] == kVersion;
}

bool LoadSaved(std::vector<regpu::ScannedShader>& out, uint32_t signature)
{
    std::ifstream in(SavedPath(), std::ios::binary);
    uint32_t head[4];
    if (!ReadHeader(in, head) || head[3] != signature)
        return false;
    out.resize(head[2]);
    for (auto& s : out)
    {
        uint32_t meta[2];
        if (!in.read(reinterpret_cast<char*>(meta), sizeof(meta)) || meta[1] > 0x10000)
            return false;
        s.pixel = meta[0] != 0;
        s.code.resize(meta[1]);
        if (!in.read(reinterpret_cast<char*>(s.code.data()), std::streamsize(size_t(meta[1]) * 4)))
            return false;
    }
    return true;
}

void Save(const std::vector<regpu::ScannedShader>& shaders, uint32_t signature)
{
    std::error_code ec;
    std::filesystem::create_directories(g_saveDir, ec);
    std::ofstream out(SavedPath(), std::ios::binary);
    const uint32_t head[4] = { kMagic, kVersion, uint32_t(shaders.size()), signature };
    out.write(reinterpret_cast<const char*>(head), sizeof(head));
    for (const auto& s : shaders)
    {
        const uint32_t meta[2] = { s.pixel ? 1u : 0u, uint32_t(s.code.size()) };
        out.write(reinterpret_cast<const char*>(meta), sizeof(meta));
        out.write(reinterpret_cast<const char*>(s.code.data()), std::streamsize(s.code.size() * 4));
    }
}

// The loaded executable image (the engine's shaders), region by readable region.
void ScanImage(Found& found)
{
    if (!g_guestBase)
        return;
    const uint8_t* begin = g_guestBase + 0x82000000u;
    const uint8_t* end = begin + 0x04000000u;
    for (const uint8_t* p = begin; p < end;)
    {
        MEMORY_BASIC_INFORMATION mbi{};
        if (!VirtualQuery(p, &mbi, sizeof(mbi)))
            break;
        const uint8_t* regionEnd = std::min(end, static_cast<const uint8_t*>(mbi.BaseAddress) + mbi.RegionSize);
        const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE;
        if (mbi.State == MEM_COMMIT && (mbi.Protect & readable) && !(mbi.Protect & PAGE_GUARD))
            FindBlobs(p, size_t(regionEnd - p), found);
        p = regionEnd;
    }
}
} // namespace

void SetShaderSources(const std::filesystem::path& game, const std::filesystem::path& update, const std::filesystem::path& saveDir,
                      const uint8_t* guestBase)
{
    g_game = game;
    g_update = update;
    g_saveDir = saveDir;
    g_guestBase = guestBase;
}

bool ReadGameZipMember(const std::string& zip, const std::string& member, std::vector<uint8_t>& out)
{
    const std::filesystem::path path = g_game / zip;
    std::vector<ZipEntry> entries;
    if (!ZipIndex(path, entries))
        return false;
    const std::string want = Lower(member);
    for (const ZipEntry& e : entries)
        if (Lower(e.name) == want)
        {
            std::ifstream f(path, std::ios::binary);
            return ZipRead(f, e, out);
        }
    return false;
}

bool StaticShadersSaved()
{
    std::ifstream in(SavedPath(), std::ios::binary);
    uint32_t head[4];
    return ReadHeader(in, head) && head[3] == Signature(SourceFiles());
}

std::vector<regpu::ScannedShader> StaticShaders()
{
    std::vector<regpu::ScannedShader> result;
    const std::vector<std::filesystem::path> files = SourceFiles();
    const uint32_t signature = Signature(files);
    if (LoadSaved(result, signature))
    {
        REXLOG_INFO("fh1 shaders: {} loaded from {}", result.size(), SavedPath().string());
        return result;
    }
    const auto start = std::chrono::steady_clock::now();
    const bool deep = Deep();
    // The work: each zip member to search, and each loose file (members of the 2 GB track archive
    // spread over the threads, not one file per thread).
    struct Item
    {
        size_t file;
        ZipEntry entry; // name empty: the whole file
    };
    std::vector<Item> items;
    for (size_t i = 0; i < files.size(); i++)
    {
        const std::string ext = Lower(files[i].extension().string());
        if (ext == ".fxobj")
            items.push_back({ i, {} });
        else if (ext == ".zip")
        {
            std::vector<ZipEntry> entries;
            if (!ZipIndex(files[i], entries))
            {
                REXLOG_WARN("fh1 shaders: {} is not a zip this reads", files[i].string());
                continue;
            }
            for (ZipEntry& e : entries)
            {
                const std::string name = Lower(e.name);
                const bool effect = name.size() > 6 && name.compare(name.size() - 6, 6, ".fxobj") == 0;
                if (e.size && (effect || (deep && !Skipped(name))))
                    items.push_back({ i, std::move(e) });
            }
        }
    }
    // Biggest first, so the last thread isn't left with the one large member.
    std::sort(items.begin(), items.end(), [](const Item& a, const Item& b) { return a.entry.size > b.entry.size; });
    Found found;
    std::atomic<size_t> next{ 0 }, failed{ 0 };
    std::atomic<uint64_t> bytesSearched{ 0 };
    std::vector<std::thread> workers;
    const unsigned n = std::max(2u, std::thread::hardware_concurrency());
    for (unsigned t = 0; t < n; t++)
        workers.emplace_back([&] {
            // Below the game's threads (its audio among them): this takes every core for a while.
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
            size_t openFile = SIZE_MAX;
            std::ifstream f;
            std::vector<uint8_t> data;
            for (size_t i; (i = next++) < items.size();)
            {
                const Item& item = items[i];
                if (item.file != openFile)
                {
                    f = std::ifstream(files[item.file], std::ios::binary);
                    openFile = item.file;
                }
                if (item.entry.name.empty())
                {
                    f.seekg(0, std::ios::end);
                    data.resize(size_t(f.tellg()));
                    if (!ReadAt(f, 0, data.data(), data.size()))
                        continue;
                }
                else if (!ZipRead(f, item.entry, data))
                {
                    failed++;
                    continue;
                }
                bytesSearched += data.size();
                FindBlobs(data.data(), data.size(), found);
            }
        });
    for (auto& w : workers)
        w.join();
    const size_t fromFiles = found.shaders.size();
    ScanImage(found);
    result = std::move(found.shaders);
    Save(result, signature);
    size_t pixel = 0;
    for (const auto& s : result)
        pixel += s.pixel;
    REXLOG_INFO("fh1 shaders: {} found ({} vertex, {} pixel; {} in the files, {} more in the executable) from {} blobs, {} MB in {} members "
                "and files ({} unreadable) in {:.1f} s",
                result.size(), result.size() - pixel, pixel, fromFiles, result.size() - fromFiles, found.blobs.load(), bytesSearched.load() >> 20,
                items.size(), failed.load(), std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count());
    return result;
}
} // namespace glue2gpu
