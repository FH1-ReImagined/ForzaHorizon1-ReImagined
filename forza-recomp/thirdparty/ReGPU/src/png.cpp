#include "png.h"

#include <fstream>

namespace
{
uint32_t Crc(const uint8_t* data, size_t n, uint32_t crc = 0xFFFFFFFF)
{
    for (size_t i = 0; i < n; i++)
    {
        crc ^= data[i];
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1)));
    }
    return crc;
}

void Be32(std::vector<uint8_t>& v, uint32_t x)
{
    for (int s = 24; s >= 0; s -= 8)
        v.push_back(uint8_t(x >> s));
}

void Chunk(std::ofstream& f, const char* type, const std::vector<uint8_t>& data)
{
    std::vector<uint8_t> out;
    Be32(out, uint32_t(data.size()));
    out.insert(out.end(), type, type + 4);
    out.insert(out.end(), data.begin(), data.end());
    Be32(out, ~Crc(out.data() + 4, out.size() - 4));
    f.write(reinterpret_cast<const char*>(out.data()), out.size());
}
}

bool WritePng(const std::filesystem::path& path, uint32_t width, uint32_t height, const std::vector<uint8_t>& rgba)
{
    std::ofstream f(path, std::ios::binary);
    if (!f)
        return false;
    f.write("\x89PNG\r\n\x1a\n", 8);
    std::vector<uint8_t> ihdr;
    Be32(ihdr, width);
    Be32(ihdr, height);
    ihdr.insert(ihdr.end(), { 8, 6, 0, 0, 0 });
    Chunk(f, "IHDR", ihdr);

    // zlib stream of stored deflate blocks.
    std::vector<uint8_t> raw;
    for (uint32_t y = 0; y < height; y++)
    {
        raw.push_back(0);
        raw.insert(raw.end(), rgba.begin() + size_t(y) * width * 4, rgba.begin() + size_t(y + 1) * width * 4);
    }
    std::vector<uint8_t> z = { 0x78, 0x01 };
    for (size_t pos = 0; pos < raw.size();)
    {
        const size_t n = std::min<size_t>(65535, raw.size() - pos);
        z.push_back(pos + n == raw.size() ? 1 : 0);
        z.push_back(uint8_t(n));
        z.push_back(uint8_t(n >> 8));
        z.push_back(uint8_t(~n));
        z.push_back(uint8_t(~n >> 8));
        z.insert(z.end(), raw.begin() + pos, raw.begin() + pos + n);
        pos += n;
    }
    uint32_t a = 1, b = 0;
    for (uint8_t c : raw)
    {
        a = (a + c) % 65521;
        b = (b + a) % 65521;
    }
    Be32(z, (b << 16) | a);
    Chunk(f, "IDAT", z);
    Chunk(f, "IEND", {});
    return bool(f);
}
