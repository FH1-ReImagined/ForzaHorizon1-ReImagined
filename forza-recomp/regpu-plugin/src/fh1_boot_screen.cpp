// The first-boot screen: while every game shader compiles, the game's own splash background and
// logo with a progress bar, instead of a window that looks hung (as the FM2 port's). The art is
// decoded from the player's media/ui/Textures.zip (.xds: a 52-byte header ending in the texture
// fetch constant, then the tiled texture data), so nothing is bundled.
#include "fh1_boot_screen.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <string>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <regpu/regpu.h>
#include <rex/logging.h>

#include "fh1_shaders.h"
#include "gpu/texture.h"
#include "png.h"

namespace glue2gpu
{
namespace
{
constexpr uint32_t kW = 1280, kH = 720;
constexpr const char* kZip = "media/ui/Textures.zip";
// FH1's own art in media/ui/textures/Horizon.zip (Textures.zip's splash and logos are Forza Motorsport 4's).
constexpr const char* kBackground = "media/ui/textures/Horizon.zip|Loading/BGLoadingImages/Image1.xds"; // the festival stage, 1280x720
constexpr const char* kLogo = "media/ui/textures/Horizon.zip|E3/E3LargeLogo.xds";                       // Forza Horizon, 1000x300 with alpha

struct Image
{
    uint32_t w = 0, h = 0;
    std::vector<uint8_t> rgba;
};

uint32_t Be32(const uint8_t* p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }

// spec: a member of media/ui/Textures.zip, or "<zip under the data root>|<member>".
bool LoadXds(const std::string& spec, Image& img)
{
    const size_t bar = spec.find('|');
    const std::string zip = bar == std::string::npos ? kZip : spec.substr(0, bar);
    const std::string member = bar == std::string::npos ? spec : spec.substr(bar + 1);
    std::vector<uint8_t> file;
    if (!ReadGameZipMember(zip, member, file) || file.size() < 52)
        return false;
    TextureFetch fetch{};
    for (int i = 0; i < 6; i++)
        fetch.dwords[i] = Be32(&file[28 + i * 4]);
    img.w = fetch.Width();
    img.h = fetch.Height();
    return DecodeTexture(fetch, file.data() + 52, img.rgba);
}

struct Assets
{
    std::once_flag once;
    bool ok = false;
    Image background, logo;
} g_assets;

bool EnsureAssets()
{
    std::call_once(g_assets.once, [] {
        g_assets.ok = LoadXds(kBackground, g_assets.background) && g_assets.background.w == kW && g_assets.background.h == kH;
        if (!LoadXds(kLogo, g_assets.logo))
            g_assets.logo = {};
        if (!g_assets.ok)
            REXLOG_WARN("boot screen: {} not found or not 1280x720; using ReGPU's plain overlay", kBackground);
    });
    return g_assets.ok;
}

// Draws src scaled to (dw x dh) at (dx, dy), alpha blended, bilinear.
void Blit(std::vector<uint8_t>& dst, const Image& src, int dx, int dy, int dw, int dh)
{
    for (int y = std::max(0, dy); y < std::min(int(kH), dy + dh); y++)
        for (int x = std::max(0, dx); x < std::min(int(kW), dx + dw); x++)
        {
            const float u = (x - dx + 0.5f) * src.w / dw - 0.5f, v = (y - dy + 0.5f) * src.h / dh - 0.5f;
            const int x0 = std::clamp(int(std::floor(u)), 0, int(src.w) - 1), y0 = std::clamp(int(std::floor(v)), 0, int(src.h) - 1);
            const int x1 = std::min(x0 + 1, int(src.w) - 1), y1 = std::min(y0 + 1, int(src.h) - 1);
            const float fx = std::clamp(u - x0, 0.0f, 1.0f), fy = std::clamp(v - y0, 0.0f, 1.0f);
            float c[4];
            for (int k = 0; k < 4; k++)
            {
                auto at = [&](int sx, int sy) { return float(src.rgba[(size_t(sy) * src.w + sx) * 4 + k]); };
                c[k] = (at(x0, y0) * (1 - fx) + at(x1, y0) * fx) * (1 - fy) + (at(x0, y1) * (1 - fx) + at(x1, y1) * fx) * fy;
            }
            const float a = c[3] / 255.0f;
            uint8_t* d = &dst[(size_t(y) * kW + x) * 4];
            for (int k = 0; k < 3; k++)
                d[k] = uint8_t(d[k] * (1 - a) + c[k] * a + 0.5f);
        }
}

void FillRect(std::vector<uint8_t>& dst, int x0, int y0, int x1, int y1, const uint8_t rgb[3], float alpha)
{
    for (int y = std::max(0, y0); y < std::min(int(kH), y1); y++)
        for (int x = std::max(0, x0); x < std::min(int(kW), x1); x++)
        {
            uint8_t* d = &dst[(size_t(y) * kW + x) * 4];
            for (int k = 0; k < 3; k++)
                d[k] = uint8_t(d[k] * (1 - alpha) + rgb[k] * alpha + 0.5f);
        }
}

// Text through GDI (grayscale antialiasing, used as coverage). align: 0 left, 1 centre, 2 right of x.
void DrawText(std::vector<uint8_t>& dst, const wchar_t* text, int x, int y, int height, int weight, int align, const uint8_t rgb[3])
{
    static std::mutex gdiMutex;
    std::lock_guard lock(gdiMutex);
    HDC dc = CreateCompatibleDC(nullptr);
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof(bi.bmiHeader);
    bi.bmiHeader.biWidth = int(kW);
    bi.bmiHeader.biHeight = -height * 2;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    HGDIOBJ oldBmp = SelectObject(dc, bmp);
    HFONT font = CreateFontW(-height, 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                             ANTIALIASED_QUALITY, DEFAULT_PITCH, L"Bahnschrift");
    HGDIOBJ oldFont = SelectObject(dc, font);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, RGB(255, 255, 255));
    SIZE size{};
    GetTextExtentPoint32W(dc, text, int(wcslen(text)), &size);
    const int left = align == 1 ? x - size.cx / 2 : align == 2 ? x - size.cx : x;
    TextOutW(dc, 0, 0, text, int(wcslen(text)));
    GdiFlush();
    const uint8_t* px = static_cast<const uint8_t*>(bits);
    for (int ty = 0; ty < height * 2; ty++)
        for (int tx = 0; tx < size.cx && tx < int(kW); tx++)
        {
            const int dxp = left + tx, dyp = y + ty;
            if (dxp < 0 || dxp >= int(kW) || dyp < 0 || dyp >= int(kH))
                continue;
            const float a = px[(size_t(ty) * kW + tx) * 4 + 1] / 255.0f;
            if (a <= 0)
                continue;
            uint8_t* d = &dst[(size_t(dyp) * kW + dxp) * 4];
            for (int k = 0; k < 3; k++)
                d[k] = uint8_t(d[k] * (1 - a) + rgb[k] * a + 0.5f);
        }
    SelectObject(dc, oldFont);
    DeleteObject(font);
    SelectObject(dc, oldBmp);
    DeleteObject(bmp);
    DeleteDC(dc);
}
} // namespace

bool BootScreenCompose(const wchar_t* title, uint32_t done, uint32_t total, std::vector<uint8_t>& rgba)
{
    if (!EnsureAssets())
        return false;
    rgba = g_assets.background.rgba;
    static const uint8_t white[3] = { 255, 255, 255 }, grey[3] = { 200, 200, 200 }, black[3] = { 0, 0, 0 };
    // The logo's Horizon pink along the bar, a little deeper towards its end.
    static const uint8_t orange[3] = { 255, 30, 110 }, pink[3] = { 220, 0, 80 };

    // A dark band behind the block, so the text reads over the stage.
    FillRect(rgba, 0, 150, int(kW), 600, black, 0.5f);
    // Logo, centred, its aspect kept; the whole block sits a little above the middle.
    int y = 172;
    if (!g_assets.logo.rgba.empty())
    {
        const int lw = 620, lh = int(float(lw) * g_assets.logo.h / g_assets.logo.w);
        Blit(rgba, g_assets.logo, int(kW - lw) / 2, y, lw, lh);
        y += lh + 18;
    }
    DrawText(rgba, title, int(kW) / 2, y, 40, FW_SEMIBOLD, 1, white);
    y += 70;

    // Progress bar with the percentage to its right.
    const float progress = total ? std::min(1.0f, float(done) / float(total)) : 0.0f;
    const int bx0 = 240, bx1 = 1000, by0 = y, by1 = y + 30;
    FillRect(rgba, bx0 - 2, by0 - 2, bx1 + 2, by1 + 2, white, 0.85f);
    FillRect(rgba, bx0, by0, bx1, by1, black, 0.75f);
    const int fill = bx0 + int((bx1 - bx0) * progress + 0.5f);
    for (int x = bx0 + 3; x < fill - 3; x++)
    {
        const float t = float(x - bx0) / float(bx1 - bx0);
        const uint8_t c[3] = { uint8_t(orange[0] + (pink[0] - orange[0]) * t), uint8_t(orange[1] + (pink[1] - orange[1]) * t),
                               uint8_t(orange[2] + (pink[2] - orange[2]) * t) };
        FillRect(rgba, x, by0 + 3, x + 1, by1 - 3, c, 1.0f);
    }
    wchar_t text[96];
    if (total)
    {
        swprintf(text, 96, L"%d%%", int(progress * 100.0f));
        DrawText(rgba, text, bx1 + 22, by0 - 4, 32, FW_SEMIBOLD, 0, white);
        swprintf(text, 96, L"%u / %u %ls · first start only", done, total, wcsstr(title, L"PIPELINES") ? L"pipelines" : L"game shaders");
    }
    else
        swprintf(text, 96, L"Searching the game files · first start only");
    DrawText(rgba, text, int(kW) / 2, by1 + 18, 22, FW_NORMAL, 1, grey);
    return true;
}

void BootScreenPreview()
{
    const auto dir = regpu::GetConfig().userDir / "bootscreen";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    // GLUE2GPU_BOOTSCREEN_PREVIEW=<name;name...>: those textures of media/ui/Textures.zip as well.
    std::vector<std::string> names = { kBackground, kLogo };
    for (std::string list = std::getenv("GLUE2GPU_BOOTSCREEN_PREVIEW"); !list.empty();)
    {
        const size_t semi = list.find(';');
        if (list.substr(0, semi).size() > 1)
            names.push_back(list.substr(0, semi));
        list = semi == std::string::npos ? "" : list.substr(semi + 1);
    }
    for (const std::string& name : names)
    {
        Image img;
        if (LoadXds(name, img))
        {
            std::string file = std::filesystem::path(name.substr(name.find('|') + 1)).stem().string();
            std::replace(file.begin(), file.end(), ' ', '_');
            WritePng(dir / (file + ".png"), img.w, img.h, img.rgba);
            REXLOG_INFO("boot screen preview: {} {}x{}", name, img.w, img.h);
        }
        else
            REXLOG_INFO("boot screen preview: {} not decoded", name);
    }
    std::vector<uint8_t> frame;
    if (BootScreenCompose(L"COMPILING SHADERS", 712, 1548, frame))
        WritePng(dir / "composed.png", kW, kH, frame);
    if (BootScreenCompose(L"PREPARING SHADERS", 0, 0, frame))
        WritePng(dir / "searching.png", kW, kH, frame);
}
} // namespace glue2gpu
