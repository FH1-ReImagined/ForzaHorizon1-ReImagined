// The live debug server: one client at a time on 127.0.0.1, a line per command, a line of JSON back.
// See tools/regpuctl.py for the commands. Input is merged into player 1's pad, screenshots are taken by the
// renderer at its next swap, and the waits block this thread only.
#include "debug.h"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "live_switch.h"
#include "log.h"
#include "memory.h"
#include "settings.h"
#if __has_include("C:/Program Files/RenderDoc/renderdoc_app.h")
#include "C:/Program Files/RenderDoc/renderdoc_app.h"
#define REGPU_HAVE_RENDERDOC 1
#endif

#pragma comment(lib, "ws2_32.lib")

void RendererRequestCapture(); // renderer.cpp: F12
uint64_t RendererEditPixelShader(uint64_t hash, const std::string& expr);
std::string RendererShaderHlsl(uint64_t hash);
bool RendererPsEditReady(uint64_t alias);
std::string RendererDrawStats();
uint32_t RendererLastFrameDraws();

namespace
{
using Clock = std::chrono::steady_clock;

std::mutex g_mutex;
std::condition_variable g_swapped;
uint64_t g_frame = 0; // the renderer's frame number (it can start again from 0)
uint64_t g_swaps = 0; // swaps seen, for the waits
std::deque<Clock::time_point> g_swapTimes; // the last second's swaps (fps)
std::vector<float> g_frameMs; // frame times (ms) since the last "frametimes"
Clock::time_point g_lastSwap{};

// Input: buttons held until released, and timed presses/sticks/triggers.
struct Timed
{
    Clock::time_point until;
    DebugPad pad;
};
DebugPad g_held;
std::vector<Timed> g_timed;
std::atomic<bool> g_inputActive{ false };

std::string g_shotPath;
std::string g_burstDir, g_burstExt = ".png"; // shots N DIR [raw]
uint32_t g_burstLeft = 0, g_burstTotal = 0;
uint64_t g_shotsDone = 0;

const struct { const char* name; uint16_t bit; } kButtons[] = {
    { "UP", 0x0001 }, { "DOWN", 0x0002 }, { "LEFT", 0x0004 }, { "RIGHT", 0x0008 }, { "START", 0x0010 }, { "BACK", 0x0020 },
    { "LS", 0x0040 }, { "RS", 0x0080 }, { "LB", 0x0100 }, { "RB", 0x0200 }, { "A", 0x1000 }, { "B", 0x2000 }, { "X", 0x4000 },
    { "Y", 0x8000 },
};

uint16_t ButtonBits(const std::string& names)
{
    uint16_t bits = 0;
    std::stringstream ss(names);
    for (std::string n; std::getline(ss, n, '+');)
    {
        for (char& c : n)
            c = char(toupper(uint8_t(c)));
        for (auto& b : kButtons)
            if (n == b.name)
                bits |= b.bit;
    }
    return bits;
}

std::string Escape(const std::string& s)
{
    std::string out;
    for (char c : s)
    {
        if (c == '"' || c == '\\')
            out += '\\', out += c;
        else if (c == '\n')
            out += "\\n";
        else if (uint8_t(c) < 32)
            out += ' ';
        else
            out += c;
    }
    return out;
}

std::string Ok(const std::string& fields = "") { return "{\"ok\":true" + (fields.empty() ? "" : "," + fields) + "}"; }
std::string Error(const std::string& why) { return "{\"ok\":false,\"error\":\"" + Escape(why) + "\"}"; }

// Waits for n more swaps (or the timeout); the frame reached.
uint64_t WaitFrames(uint64_t n, double timeoutSeconds)
{
    std::unique_lock lock(g_mutex);
    const uint64_t target = g_swaps + n;
    g_swapped.wait_for(lock, std::chrono::duration<double>(timeoutSeconds), [&] { return g_swaps >= target; });
    return g_frame;
}

bool Readable(uint32_t address, uint32_t size)
{
    MEMORY_BASIC_INFORMATION mi{};
    if (!VirtualQuery(g_base + address, &mi, sizeof(mi)) || mi.State != MEM_COMMIT || (mi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
        return false;
    return uint64_t(address) + size <= uint64_t(static_cast<uint8_t*>(mi.BaseAddress) - g_base) + mi.RegionSize;
}

std::string Handle(const std::string& line)
{
    std::stringstream in(line);
    std::string cmd;
    in >> cmd;
    for (char& c : cmd)
        c = char(tolower(uint8_t(c)));

    if (cmd == "ping")
        return Ok();
    if (cmd == "status")
    {
        std::lock_guard lock(g_mutex);
        const auto now = Clock::now();
        while (!g_swapTimes.empty() && now - g_swapTimes.front() > std::chrono::seconds(1))
            g_swapTimes.pop_front();
        char buf[256];
        snprintf(buf, sizeof(buf), "\"frame\":%llu,\"fps\":%zu,\"draws\":%u", (unsigned long long)g_frame, g_swapTimes.size(), RendererLastFrameDraws());
        return Ok(buf);
    }
    // frametimes: frame-time statistics since the last call (average, percentiles, worst, frames over 20/33 ms)
    if (cmd == "frametimes")
    {
        std::vector<float> v;
        {
            std::lock_guard lock(g_mutex);
            v.swap(g_frameMs);
        }
        if (v.empty())
            return Error("no frames");
        double sum = 0;
        for (float x : v)
            sum += x;
        std::vector<float> sorted = v;
        std::sort(sorted.begin(), sorted.end());
        auto pct = [&](double q) { return sorted[std::min(sorted.size() - 1, size_t(q * double(sorted.size())))]; };
        size_t over20 = 0, over33 = 0;
        for (float x : v)
        {
            over20 += x > 20.0f;
            over33 += x > 33.4f;
        }
        char buf[256];
        snprintf(buf, sizeof(buf), "\"frames\":%zu,\"avg\":%.3f,\"p50\":%.3f,\"p99\":%.3f,\"p999\":%.3f,\"max\":%.3f,\"over20\":%zu,\"over33\":%zu",
            v.size(), sum / double(v.size()), pct(0.5), pct(0.99), pct(0.999), sorted.back(), over20, over33);
        return Ok(buf);
    }
    // press A [ms]   press A+B 200   (held for ms, default 120, then released)
    if (cmd == "press" || cmd == "hold" || cmd == "release")
    {
        std::string names;
        in >> names;
        const uint16_t bits = names.empty() || names == "all" ? 0xFFFF : ButtonBits(names);
        if (!bits)
            return Error("unknown button: " + names);
        std::lock_guard lock(g_mutex);
        if (cmd == "hold")
            g_held.buttons |= bits;
        else if (cmd == "release")
        {
            g_held.buttons &= ~bits;
            if (names.empty() || names == "all")
                g_held = {}, g_timed.clear();
        }
        else
        {
            int ms = 120;
            in >> ms;
            Timed t{ Clock::now() + std::chrono::milliseconds(ms), {} };
            t.pad.buttons = bits;
            g_timed.push_back(t);
        }
        g_inputActive = true;
        return Ok();
    }
    // stick L x y [ms]  (-1..1; without ms it stays until "stick L 0 0")
    // trigger L|R v [ms] (0..1)
    if (cmd == "stick" || cmd == "trigger")
    {
        std::string side;
        double x = 0, y = 0;
        int ms = -1;
        in >> side;
        if (cmd == "stick")
            in >> x >> y >> ms;
        else
            in >> x >> ms;
        const bool left = !side.empty() && toupper(uint8_t(side[0])) == 'L';
        DebugPad p;
        if (cmd == "stick")
        {
            p.sticks = true;
            const int16_t sx = int16_t(std::clamp(x, -1.0, 1.0) * 32767), sy = int16_t(std::clamp(y, -1.0, 1.0) * 32767);
            (left ? p.lx : p.rx) = sx;
            (left ? p.ly : p.ry) = sy;
        }
        else
        {
            p.triggers = true;
            (left ? p.leftTrigger : p.rightTrigger) = uint8_t(std::clamp(x, 0.0, 1.0) * 255);
        }
        std::lock_guard lock(g_mutex);
        if (ms >= 0)
            g_timed.push_back({ Clock::now() + std::chrono::milliseconds(ms), p });
        else if (cmd == "stick")
        {
            g_held.sticks = true;
            (left ? g_held.lx : g_held.rx) = p.lx | p.rx;
            (left ? g_held.ly : g_held.ry) = p.ly | p.ry;
        }
        else
        {
            g_held.triggers = true;
            (left ? g_held.leftTrigger : g_held.rightTrigger) = p.leftTrigger | p.rightTrigger;
        }
        g_inputActive = true;
        return Ok();
    }
    // wait <frames> | wait <seconds>s
    if (cmd == "wait")
    {
        std::string arg;
        in >> arg;
        if (!arg.empty() && arg.back() == 's')
        {
            std::this_thread::sleep_for(std::chrono::duration<double>(atof(arg.c_str())));
            std::lock_guard lock(g_mutex);
            return Ok("\"frame\":" + std::to_string(g_frame));
        }
        const uint64_t n = arg.empty() ? 1 : strtoull(arg.c_str(), nullptr, 10);
        return Ok("\"frame\":" + std::to_string(WaitFrames(n, 60)));
    }
    // shot [path]: the next frame as a PNG (default out/live.png)
    if (cmd == "shot")
    {
        std::string path;
        std::getline(in, path);
        path.erase(0, path.find_first_not_of(' '));
        if (path.empty())
            path = "out/live.png";
        std::unique_lock lock(g_mutex);
        const uint64_t before = g_shotsDone;
        g_shotPath = path;
        if (!g_swapped.wait_for(lock, std::chrono::seconds(15), [&] { return g_shotsDone != before; }))
        {
            g_shotPath.clear();
            return Error("no frame came (the game may be loading or paused)");
        }
        return Ok("\"path\":\"" + Escape(path) + "\",\"frame\":" + std::to_string(g_frame));
    }
    // shots N DIR [raw]: N consecutive frames as DIR/s0.png ... (flicker between frames); raw: small .raw
    if (cmd == "shots")
    {
        uint32_t n = 0;
        std::string dir;
        in >> n;
        std::getline(in, dir);
        dir.erase(0, dir.find_first_not_of(' '));
        // "raw" after the directory: 640x360 RGBA files that barely slow the game (renderer.cpp).
        const bool raw = dir.size() > 4 && dir.compare(dir.size() - 4, 4, " raw") == 0;
        if (raw)
            dir.resize(dir.size() - 4);
        if (!n || n > 120 || dir.empty())
            return Error("shots N DIR [raw] (N up to 120)");
        std::error_code ec; // (the throwing overload, uncaught on this thread, ended the game)
        std::filesystem::create_directories(dir, ec);
        if (ec)
            return Error("can't make " + dir + ": " + ec.message());
        std::unique_lock lock(g_mutex);
        const uint64_t before = g_shotsDone;
        g_burstDir = dir;
        g_burstExt = raw ? ".raw" : ".png";
        g_burstTotal = g_burstLeft = n;
        g_shotPath = dir + "/s0" + g_burstExt;
        if (!g_swapped.wait_for(lock, std::chrono::seconds(60), [&] { return g_shotsDone - before >= n; }))
        {
            g_shotPath.clear();
            g_burstLeft = 0;
            return Error("not all frames came");
        }
        return Ok("\"frames\":" + std::to_string(n));
    }
    // output W H: the drawing resolution while the game runs (regpu::SetOutputSize)
    if (cmd == "output")
    {
        uint32_t w = 0, h = 0;
        in >> w >> h;
        if (!w || !h)
            return Error("output WIDTH HEIGHT");
        regpu::SetOutputSize(w, h);
        WaitFrames(3, 5);
        return Ok("\"output\":\"" + std::to_string(w) + "x" + std::to_string(h) + "\"");
    }
    // rdcapture: a RenderDoc capture of the next frame (the game started under RenderDoc, e.g. with
    // regpuctl's REGPU_WRAP). From the FM2 port.
    if (cmd == "rdcapture")
    {
#ifdef REGPU_HAVE_RENDERDOC
        HMODULE rd = GetModuleHandleA("renderdoc.dll");
        if (!rd)
            return Error("not running under RenderDoc");
        auto getApi = reinterpret_cast<pRENDERDOC_GetAPI>(GetProcAddress(rd, "RENDERDOC_GetAPI"));
        RENDERDOC_API_1_1_2* api = nullptr;
        if (!getApi || !getApi(eRENDERDOC_API_Version_1_1_2, reinterpret_cast<void**>(&api)) || !api)
            return Error("RENDERDOC_GetAPI failed");
        int frames = 1;
        in >> frames;
        if (frames > 1)
            api->TriggerMultiFrameCapture(static_cast<uint32_t>(frames));
        else
            api->TriggerCapture();
        return Ok("\"note\":\"captured at the next present\"");
#else
        return Error("built without RenderDoc's renderdoc_app.h");
#endif
    }
    if (cmd == "capture")
    {
        RendererRequestCapture();
        return Ok("\"note\":\"traced into out\\\\capture and play log at the next frame\"");
    }
    // log [n]: the last n log lines
    if (cmd == "log")
    {
        int n = 40;
        in >> n;
        std::string out;
        for (const auto& l : LogTail(size_t(std::clamp(n, 1, 2000))))
            out += (out.empty() ? "\"" : ",\"") + Escape(l) + "\"";
        return Ok("\"lines\":[" + out + "]");
    }
    // peek <hex address> <bytes>   poke <hex address> <hex bytes>   (guest addresses, raw big-endian memory)
    if (cmd == "peek" || cmd == "poke")
    {
        std::string a, data;
        in >> a >> data;
        const uint32_t address = uint32_t(strtoul(a.c_str(), nullptr, 16));
        if (cmd == "peek")
        {
            const uint32_t n = std::min<uint32_t>(data.empty() ? 64 : uint32_t(strtoul(data.c_str(), nullptr, 0)), 4096);
            if (!Readable(address, n))
                return Error("not mapped");
            std::string hex;
            char b[3];
            for (uint32_t i = 0; i < n; i++)
                snprintf(b, sizeof(b), "%02X", GuestPtr<uint8_t>(address)[i]), hex += b;
            return Ok("\"hex\":\"" + hex + "\"");
        }
        if (data.size() % 2 || !Readable(address, uint32_t(data.size() / 2)))
            return Error("bad data or not mapped");
        for (size_t i = 0; i < data.size(); i += 2)
            GuestPtr<uint8_t>(address)[i / 2] = uint8_t(strtoul(data.substr(i, 2).c_str(), nullptr, 16));
        return Ok();
    }
    // ps HASH EXPR: that pixel shader returns EXPR (HLSL over its registers, e.g. float4(r[4].xyz, 1))
    // ps HASH tint R,G,B: a flat colour   ps HASH: back to the original
    // vs HASH STATEMENT: that vertex shader also runs STATEMENT at its end (HLSL over r[], o[], vc[]), e.g.
    // o[0].zw = r[4].xy; (and read it back with a ps edit)   vs HASH: back to the original
    if (cmd == "ps" || cmd == "vs")
    {
        std::string h, rest;
        in >> h;
        std::getline(in, rest);
        rest.erase(0, rest.find_first_not_of(' '));
        const uint64_t hash = strtoull(h.c_str(), nullptr, 16);
        if (!hash)
            return Error("ps HASH [EXPR | tint R,G,B]");
        if (cmd == "ps" && rest.rfind("tint ", 0) == 0)
            rest = "float4(" + rest.substr(5) + ", 1)";
        const uint64_t alias = RendererEditPixelShader(hash, rest);
        // Its compile takes a second or so and its draws are skipped meanwhile: answer once it draws
        // (and a few frames later, so the next shot has it everywhere).
        bool ready = !alias;
        const auto deadline = Clock::now() + std::chrono::seconds(30); // vertex shaders take ~5 s
        while (!ready && Clock::now() < deadline)
        {
            WaitFrames(1, 0.1);
            ready = RendererPsEditReady(alias);
        }
        if (ready)
            WaitFrames(3, 2);
        char buf[96];
        snprintf(buf, sizeof(buf), "\"alias\":\"%016llx\",\"ready\":%s", (unsigned long long)alias, ready ? "true" : "false");
        return Ok(buf);
    }
    // hlsl HASH [path]: the shader's translated HLSL, written to path (default out/hlsl/<hash>.hlsl)
    if (cmd == "hlsl")
    {
        std::string h, path;
        in >> h >> path;
        const std::string src = RendererShaderHlsl(strtoull(h.c_str(), nullptr, 16));
        if (src.empty())
            return Error("no such shader (not drawn yet?)");
        if (path.empty())
            path = "out/hlsl/" + h + ".hlsl";
        CreateDirectoryA("out", nullptr);
        CreateDirectoryA("out/hlsl", nullptr);
        FILE* f = fopen(path.c_str(), "wb");
        if (!f)
            return Error("can't write " + path);
        fwrite(src.data(), 1, src.size(), f);
        fclose(f);
        return Ok("\"path\":\"" + Escape(path) + "\",\"bytes\":" + std::to_string(src.size()));
    }
    // switches: the on/off REGPU_* switches reached so far   switch NAME 0|1: flip one (live_switch.h)
    if (cmd == "switches")
    {
        std::string out;
        for (const auto& [name, on] : LiveSwitch::List())
            out += (out.empty() ? "\"" : ",\"") + Escape(name) + "\":" + (on ? "true" : "false");
        return Ok("\"switches\":{" + out + "}");
    }
    if (cmd == "switch")
    {
        std::string name, value;
        in >> name >> value;
        if ((name.rfind("REGPU_", 0) != 0 && name.rfind("PGR4_", 0) != 0) || (value != "0" && value != "1" && value != "on" && value != "off"))
            return Error("switch REGPU_NAME|PGR4_NAME 0|1");
        const bool on = value == "1" || value == "on";
        const bool known = LiveSwitch::Set(name, on);
        return Ok(std::string("\"name\":\"") + Escape(name) + "\",\"on\":" + (on ? "true" : "false") +
                  ",\"reached\":" + (known ? "true" : "false"));
    }
    // drawstats: the next frame's draws per shader pair
    if (cmd == "drawstats")
        return Ok("\"pairs\":[" + RendererDrawStats() + "]");
    // type TEXT: as if typed in the game window (WM_CHAR: Host::onChar, e.g. cheat codes)
    if (cmd == "type")
    {
        std::string text;
        std::getline(in, text);
        text.erase(0, text.find_first_not_of(' '));
        if (HWND w = FindWindowW(L"regpu", nullptr))
            for (char c : text)
                PostMessageW(w, WM_CHAR, WPARAM(uint8_t(c)), 0);
        return Ok();
    }
    // find TEXT: guest addresses holding TEXT as ASCII, UTF-16 big- or little-endian (the first 64)
    if (cmd == "find")
    {
        std::string text;
        std::getline(in, text);
        text.erase(0, text.find_first_not_of(' '));
        if (text.empty())
            return Error("find TEXT");
        std::vector<std::pair<std::string, std::string>> needles = { { "ascii", text }, { "utf16be", "" }, { "utf16le", "" } };
        for (char c : text)
        {
            needles[1].second += '\0', needles[1].second += c;
            needles[2].second += c, needles[2].second += '\0';
        }
        std::string out;
        int found = 0;
        for (uint64_t a = 0x10000; a < 0x100000000ull && found < 64;)
        {
            MEMORY_BASIC_INFORMATION mi{};
            if (!VirtualQuery(g_base + a, &mi, sizeof(mi)))
                break;
            const uint64_t start = uint64_t(static_cast<uint8_t*>(mi.BaseAddress) - g_base), end = start + mi.RegionSize;
            if (mi.State == MEM_COMMIT && !(mi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
            {
                const std::string_view region(reinterpret_cast<const char*>(g_base + start), size_t(end - start));
                for (const auto& [kind, n] : needles)
                    for (size_t at = region.find(n); at != std::string_view::npos && found < 64; at = region.find(n, at + 1), found++)
                    {
                        char buf[48];
                        snprintf(buf, sizeof(buf), "%s{\"address\":\"%08llX\",\"as\":\"%s\"}", out.empty() ? "" : ",", (unsigned long long)(start + at), kind.c_str());
                        out += buf;
                    }
            }
            a = end;
        }
        return Ok("\"hits\":[" + out + "]");
    }
    // env NAME [value]: set (or clear) an environment variable, for the switches that take a value (read once
    // at their first use, so mostly before that happens).
    if (cmd == "env")
    {
        std::string name, value;
        in >> name;
        std::getline(in, value);
        value.erase(0, value.find_first_not_of(' '));
        _putenv_s(name.c_str(), value.c_str());
        SetEnvironmentVariableA(name.c_str(), value.empty() ? nullptr : value.c_str());
        return Ok();
    }
    if (cmd == "quit")
    {
        Log("debug: quit requested");
        fflush(nullptr);
        std::thread([] {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            ExitProcess(0);
        }).detach();
        return Ok();
    }
    return Error("unknown command: " + cmd);
}

void Serve(SOCKET client)
{
    std::string pending;
    char buf[4096];
    for (;;)
    {
        const int n = recv(client, buf, sizeof(buf), 0);
        if (n <= 0)
            break;
        pending.append(buf, size_t(n));
        for (size_t nl; (nl = pending.find('\n')) != std::string::npos;)
        {
            std::string line = pending.substr(0, nl);
            pending.erase(0, nl + 1);
            if (!line.empty() && line.back() == '\r')
                line.pop_back();
            if (line.empty())
                continue;
            const std::string reply = Handle(line) + "\n";
            send(client, reply.data(), int(reply.size()), 0);
        }
    }
    closesocket(client);
}
}

void DebugServerStart()
{
    const char* on = getenv("REGPU_DEBUG");
    if (!on || !*on || *on == '0')
        return;
    const int port = getenv("REGPU_DEBUG_PORT") ? atoi(getenv("REGPU_DEBUG_PORT")) : 7341;
    std::thread([port] {
        SetThreadDescription(GetCurrentThread(), L"debug server");
        WSADATA wsa;
        WSAStartup(MAKEWORD(2, 2), &wsa);
        SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(uint16_t(port));
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) || listen(listener, 4))
        {
            Log("debug: can't listen on 127.0.0.1:%d", port);
            return;
        }
        Log("debug: listening on 127.0.0.1:%d (tools/regpuctl.py)", port);
        for (;;)
        {
            SOCKET client = accept(listener, nullptr, nullptr);
            if (client == INVALID_SOCKET)
                break;
            std::thread(Serve, client).detach();
        }
    }).detach();
}

bool regpu::DebugPadState(DebugPad& pad)
{
    if (!g_inputActive.load(std::memory_order_relaxed))
        return false;
    std::lock_guard lock(g_mutex);
    const auto now = Clock::now();
    std::erase_if(g_timed, [&](const Timed& t) { return now >= t.until; });
    pad = g_held;
    for (const auto& t : g_timed)
    {
        pad.buttons |= t.pad.buttons;
        if (t.pad.sticks)
        {
            pad.sticks = true;
            if (t.pad.lx || t.pad.ly) pad.lx = t.pad.lx, pad.ly = t.pad.ly;
            if (t.pad.rx || t.pad.ry) pad.rx = t.pad.rx, pad.ry = t.pad.ry;
        }
        if (t.pad.triggers)
        {
            pad.triggers = true;
            pad.leftTrigger = std::max(pad.leftTrigger, t.pad.leftTrigger);
            pad.rightTrigger = std::max(pad.rightTrigger, t.pad.rightTrigger);
        }
    }
    const bool any = pad.buttons || pad.sticks || pad.triggers;
    if (!any && g_timed.empty())
        g_inputActive = g_held.buttons || g_held.sticks || g_held.triggers;
    return any;
}

void DebugOnSwap(uint64_t frame)
{
    {
        std::lock_guard lock(g_mutex);
        g_frame = frame;
        g_swaps++;
        {
            const auto now = Clock::now();
            if (g_lastSwap != Clock::time_point{} && g_frameMs.size() < 200000)
                g_frameMs.push_back(std::chrono::duration<float, std::milli>(now - g_lastSwap).count());
            g_lastSwap = now;
        }
        g_swapTimes.push_back(Clock::now());
        if (g_swapTimes.size() > 2000)
            g_swapTimes.pop_front();
    }
    g_swapped.notify_all();
}

bool DebugShotPending(std::string& path)
{
    std::lock_guard lock(g_mutex);
    if (g_shotPath.empty())
        return false;
    path = g_shotPath;
    return true;
}

void DebugShotDone(const std::string& path)
{
    {
        std::lock_guard lock(g_mutex);
        if (g_shotPath == path)
        {
            g_shotPath.clear();
            // shots N DIR: the next frame's file, until N are written.
            if (g_burstLeft && --g_burstLeft)
                g_shotPath = g_burstDir + "/s" + std::to_string(g_burstTotal - g_burstLeft) + g_burstExt;
        }
        g_shotsDone++;
    }
    g_swapped.notify_all();
}
