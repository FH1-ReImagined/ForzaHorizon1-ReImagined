// Native output: a window with a D3D12 flip-model swap chain. The guest's
// VdSwap hands a frame over; a render thread presents it without blocking
// the guest. Until the D3D hooks draw real content the frame is a clear
// whose shade pulses with the frame count, proving the path end to end.
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>

#include <d3d12.h>
#include <dxgi1_6.h>
#include <windows.h>
#include <wrl/client.h>

#include "log.h"
#include "render/presenter.h"
#include "settings.h"

using Microsoft::WRL::ComPtr;


namespace
{
constexpr UINT kBuffers = 3;
constexpr UINT kWidth = 1280, kHeight = 720;

struct Presenter
{
    HWND window = nullptr;
    ComPtr<ID3D12Device> device;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<IDXGISwapChain3> swapChain;
    ComPtr<ID3D12DescriptorHeap> rtvHeap;
    UINT rtvStride = 0;
    ComPtr<ID3D12Resource> backBuffers[kBuffers];
    ComPtr<ID3D12CommandAllocator> allocators[kBuffers];
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<ID3D12Fence> fence;
    UINT64 fenceValues[kBuffers] = {};
    UINT64 nextFence = 1;
    HANDLE fenceEvent = nullptr;

    std::mutex mutex;
    std::condition_variable cv;
    uint64_t pendingFrame = 0;
    std::vector<uint8_t> image; // latest submitted RGBA8, kWidth x kHeight
    ComPtr<ID3D12Resource> upload[kBuffers];
    uint64_t presentedFrame = 0;
    std::atomic<bool> ready{ false };
};

Presenter g_p;
}

// The size the game renders at (settings: resolution and forced aspect ratio).
void RenderSize(uint32_t& width, uint32_t& height)
{
    GetSettings().RenderSize(width, height);
}

namespace
{

LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_CLOSE)
    {
        Log("presenter: window closed, exiting");
        std::_Exit(0);
    }
    if (msg == WM_CHAR)
    {
        if (regpu::GetHost().onChar)
            regpu::GetHost().onChar(wchar_t(wp));
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

void Check(HRESULT hr, const char* what)
{
    if (FAILED(hr))
        Fatal("presenter: %s failed (%08lX)", what, hr);
}

std::mutex g_deviceMutex;
ComPtr<ID3D12Device> g_device;
ComPtr<ID3D12CommandQueue> g_queue;
std::atomic<bool> g_rendererActive{ false };

void EnsureDevice()
{
    std::lock_guard lock(g_deviceMutex);
    if (g_device)
        return;
    if (getenv("REGPU_D3D12_DEBUG"))
    {
        ComPtr<ID3D12Debug> debug;
        if (SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&debug))))
        {
            debug->EnableDebugLayer();
            Log("presenter: D3D12 debug layer on");
            // REGPU_D3D12_GBV=1 (with the debug layer): GPU-based validation and synchronized command
            // queue validation, which report resource state and barrier hazards as the GPU meets them
            // (slow). From the FM4 port.
            ComPtr<ID3D12Debug1> debug1;
            if (getenv("REGPU_D3D12_GBV") && SUCCEEDED(debug.As(&debug1)))
            {
                debug1->SetEnableGPUBasedValidation(TRUE);
                debug1->SetEnableSynchronizedCommandQueueValidation(TRUE);
                Log("presenter: D3D12 GPU-based validation on (REGPU_D3D12_GBV)");
            }
        }
        else
            Log("presenter: D3D12 debug layer not available (Graphics Tools not installed?)");
    }
    // REGPU_DRED=1: device-removed breadcrumbs and page-fault reporting, so a removed device logs the
    // command list and operation the GPU stopped at (renderer.cpp, LogDebugMessages). From the FM4 port.
    if (getenv("REGPU_DRED"))
    {
        ComPtr<ID3D12DeviceRemovedExtendedDataSettings> dred;
        if (SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&dred))))
        {
            dred->SetAutoBreadcrumbsEnablement(D3D12_DRED_ENABLEMENT_FORCED_ON);
            dred->SetPageFaultEnablement(D3D12_DRED_ENABLEMENT_FORCED_ON);
            Log("presenter: device-removed breadcrumbs and page-fault reporting on (REGPU_DRED)");
        }
    }
    Check(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&g_device)), "D3D12CreateDevice");
    D3D12_COMMAND_QUEUE_DESC qd{};
    qd.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    Check(g_device->CreateCommandQueue(&qd, IID_PPV_ARGS(&g_queue)), "CreateCommandQueue");
}

// A windowed game's client area at exactly `width` x `height` pixels (the output resolution), as a
// game's windowed mode does: not shrunk to fit the screen (the shrink gave 2369x1333 for 2560x1440
// on a 1440-row display, so the picture was never the size chosen). When it is bigger than the work
// area the window is placed at its top left, title bar on screen. Pixels are physical: the process is
// made per-monitor DPI aware before ReGPU opens a window (a host's window has its own awareness).
// Maximised or borderless (fullscreen) windows are left alone. Asynchronous: safe off the window's thread.
void SizeWindow(HWND hwnd, uint32_t width, uint32_t height)
{
    if (!hwnd || !width || !height || IsZoomed(hwnd))
        return;
    const DWORD style = DWORD(GetWindowLongW(hwnd, GWL_STYLE)), exStyle = DWORD(GetWindowLongW(hwnd, GWL_EXSTYLE));
    if (!(style & WS_CAPTION))
        return;
    RECT r{ 0, 0, LONG(width), LONG(height) };
    AdjustWindowRectExForDpi(&r, style, FALSE, exStyle, GetDpiForWindow(hwnd));
    const int w = r.right - r.left, h = r.bottom - r.top;
    MONITORINFO mi{ sizeof(mi) };
    GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &mi);
    RECT now{};
    GetWindowRect(hwnd, &now);
    // Kept on the work area as far as it fits; its top left (the title bar) always is.
    const int x = std::max<int>(mi.rcWork.left, std::min<int>(now.left, mi.rcWork.right - w));
    const int y = std::max<int>(mi.rcWork.top, std::min<int>(now.top, mi.rcWork.bottom - h));
    const bool own = GetWindowThreadProcessId(hwnd, nullptr) == GetCurrentThreadId();
    SetWindowPos(hwnd, nullptr, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | (own ? 0u : UINT(SWP_ASYNCWINDOWPOS)));
    Log("presenter: window client %ux%u", width, height);
}

void CreateWindowAndDevice()
{
    // Physical pixels for the window ReGPU opens (an unaware window at 125% scaling would be stretched).
    if (!GetSettings().hostWindow)
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    WNDCLASSW wc{};
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"regpu";
    wc.hbrBackground = HBRUSH(GetStockObject(BLACK_BRUSH));
    wc.hIcon = HICON(LoadImageW(wc.hInstance, MAKEINTRESOURCEW(1), IMAGE_ICON, GetSystemMetrics(SM_CXICON), GetSystemMetrics(SM_CYICON), LR_DEFAULTCOLOR));
    RegisterClassW(&wc);
    // The swap chain is the chosen resolution (the game's image is centred in it with black
    // bars when an aspect ratio is forced). Windowed: the client area is exactly that size
    // (SizeWindow). Fullscreen: a borderless window over the primary display. Either way the
    // swap chain stretches to the window.
    const Settings& settings = GetSettings();
    const uint32_t outW = settings.width, outH = settings.height;
    HWND surface = nullptr;
    if (settings.hostWindow)
    {
        // The host's window (Config::hostWindow): the swap chain goes straight onto it, and it
        // stretches with it. The host owns its messages (close, resize, fullscreen).
        g_p.window = HWND(settings.hostWindow);
        Log("presenter: presenting into the host's window %p", settings.hostWindow);
    }
    else if (settings.fullscreen)
    {
        // Borderless window over the whole display; its black background shows around the picture.
        // The picture (the swap chain) is a child window: the largest rectangle of the chosen
        // aspect ratio, centred. Other windows cannot cover the black bars this way.
        const int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
        g_p.window = CreateWindowExW(0, L"regpu", GetSettings().windowTitle.c_str(), WS_POPUP | WS_VISIBLE, 0, 0, sw, sh, nullptr, nullptr, wc.hInstance, nullptr);
        LONG w = sw, h = sh;
        if (int64_t(sw) * outH > int64_t(sh) * outW)
            w = LONG(int64_t(sh) * outW / outH);
        else
            h = LONG(int64_t(sw) * outH / outW);
        surface = CreateWindowExW(0, L"regpu", nullptr, WS_CHILD | WS_VISIBLE, (sw - w) / 2, (sh - h) / 2, w, h,
            g_p.window, nullptr, wc.hInstance, nullptr);
    }
    else
    {
        g_p.window = CreateWindowW(L"regpu", GetSettings().windowTitle.c_str(), WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
            CW_USEDEFAULT, CW_USEDEFAULT, nullptr, nullptr, wc.hInstance, nullptr);
        SetWindowPos(g_p.window, nullptr, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOZORDER); // (sized from the work area's corner)
        SizeWindow(g_p.window, outW, outH);
        ShowWindow(g_p.window, SW_SHOW);
    }

    ComPtr<IDXGIFactory4> factory;
    Check(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory)), "CreateDXGIFactory2");
    EnsureDevice();
    g_p.device = g_device;
    g_p.queue = g_queue;

    DXGI_SWAP_CHAIN_DESC1 sd{};
    sd.Width = outW;
    sd.Height = outH;
    sd.Scaling = DXGI_SCALING_STRETCH;
    sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = kBuffers;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    sd.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING;
    ComPtr<IDXGISwapChain1> sc1;
    Check(factory->CreateSwapChainForHwnd(g_p.queue.Get(), surface ? surface : g_p.window, &sd, nullptr, nullptr, &sc1), "CreateSwapChainForHwnd");
    Check(sc1.As(&g_p.swapChain), "IDXGISwapChain3");
    factory->MakeWindowAssociation(g_p.window, DXGI_MWA_NO_ALT_ENTER);

    D3D12_DESCRIPTOR_HEAP_DESC hd{};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    hd.NumDescriptors = kBuffers;
    Check(g_p.device->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_p.rtvHeap)), "CreateDescriptorHeap");
    g_p.rtvStride = g_p.device->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);
    for (UINT i = 0; i < kBuffers; i++)
    {
        Check(g_p.swapChain->GetBuffer(i, IID_PPV_ARGS(&g_p.backBuffers[i])), "GetBuffer");
        D3D12_CPU_DESCRIPTOR_HANDLE h = g_p.rtvHeap->GetCPUDescriptorHandleForHeapStart();
        h.ptr += SIZE_T(i) * g_p.rtvStride;
        g_p.device->CreateRenderTargetView(g_p.backBuffers[i].Get(), nullptr, h);
        Check(g_p.device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&g_p.allocators[i])), "CreateCommandAllocator");
    }
    Check(g_p.device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, g_p.allocators[0].Get(), nullptr, IID_PPV_ARGS(&g_p.list)),
        "CreateCommandList");
    g_p.list->Close();
    Check(g_p.device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&g_p.fence)), "CreateFence");
    g_p.fenceEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);

    D3D12_HEAP_PROPERTIES hp{};
    hp.Type = D3D12_HEAP_TYPE_UPLOAD;
    D3D12_RESOURCE_DESC rd{};
    rd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    rd.Width = UINT64(kWidth) * kHeight * 4;
    rd.Height = 1;
    rd.DepthOrArraySize = 1;
    rd.MipLevels = 1;
    rd.SampleDesc.Count = 1;
    rd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    for (UINT i = 0; i < kBuffers; i++)
        Check(g_p.device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_GENERIC_READ, nullptr,
                  IID_PPV_ARGS(&g_p.upload[i])),
            "CreateCommittedResource(upload)");
}

void Transition(ID3D12Resource* resource, D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after)
{
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = resource;
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = before;
    b.Transition.StateAfter = after;
    g_p.list->ResourceBarrier(1, &b);
}

void DrawFrame(uint64_t frame)
{
    const UINT i = g_p.swapChain->GetCurrentBackBufferIndex();
    if (g_p.fence->GetCompletedValue() < g_p.fenceValues[i])
    {
        g_p.fence->SetEventOnCompletion(g_p.fenceValues[i], g_p.fenceEvent);
        WaitForSingleObject(g_p.fenceEvent, INFINITE);
    }
    g_p.allocators[i]->Reset();
    g_p.list->Reset(g_p.allocators[i].Get(), nullptr);
    ID3D12Resource* backBuffer = g_p.backBuffers[i].Get();

    bool copied = false;
    {
        std::lock_guard lock(g_p.mutex);
        if (g_p.image.size() == size_t(kWidth) * kHeight * 4)
        {
            void* mapped = nullptr;
            g_p.upload[i]->Map(0, nullptr, &mapped);
            memcpy(mapped, g_p.image.data(), g_p.image.size());
            g_p.upload[i]->Unmap(0, nullptr);
            copied = true;
        }
    }

    if (copied)
    {
        Transition(backBuffer, D3D12_RESOURCE_STATE_PRESENT, D3D12_RESOURCE_STATE_COPY_DEST);
        D3D12_TEXTURE_COPY_LOCATION dst{};
        dst.pResource = backBuffer;
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        D3D12_TEXTURE_COPY_LOCATION src{};
        src.pResource = g_p.upload[i].Get();
        src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        src.PlacedFootprint.Footprint = { DXGI_FORMAT_R8G8B8A8_UNORM, kWidth, kHeight, 1, kWidth * 4 };
        g_p.list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        Transition(backBuffer, D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_PRESENT);
    }
    else
    {
        Transition(backBuffer, D3D12_RESOURCE_STATE_PRESENT, D3D12_RESOURCE_STATE_RENDER_TARGET);
        D3D12_CPU_DESCRIPTOR_HANDLE rtv = g_p.rtvHeap->GetCPUDescriptorHandleForHeapStart();
        rtv.ptr += SIZE_T(i) * g_p.rtvStride;
        const float pulse = 0.15f + 0.1f * float((frame % 120) < 60 ? frame % 60 : 60 - frame % 60) / 60.0f;
        const float color[4] = { 0.05f, pulse, 0.12f, 1.0f };
        g_p.list->ClearRenderTargetView(rtv, color, 0, nullptr);
        Transition(backBuffer, D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_PRESENT);
    }

    g_p.list->Close();
    ID3D12CommandList* lists[] = { g_p.list.Get() };
    g_p.queue->ExecuteCommandLists(1, lists);
    g_p.swapChain->Present(0, DXGI_PRESENT_ALLOW_TEARING);
    g_p.fenceValues[i] = g_p.nextFence++;
    g_p.queue->Signal(g_p.fence.Get(), g_p.fenceValues[i]);
}

void RenderThread()
{
    SetThreadDescription(GetCurrentThread(), L"presenter");
    CreateWindowAndDevice();
    g_p.ready = true;
    Log("presenter: window and D3D12 swap chain ready");
    for (;;)
    {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE))
        {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        uint64_t frame;
        {
            std::unique_lock lock(g_p.mutex);
            g_p.cv.wait_for(lock, std::chrono::milliseconds(16), [] { return g_p.pendingFrame != g_p.presentedFrame; });
            frame = g_p.pendingFrame;
            if (frame == g_p.presentedFrame || g_rendererActive)
                continue;
            g_p.presentedFrame = frame;
        }
        DrawFrame(frame);
    }
}
}

void PresenterInit()
{
    if (getenv("REGPU_HEADLESS"))
        return;
    std::thread(RenderThread).detach();
}

void PresenterSubmitFrame(uint64_t frame)
{
    {
        std::lock_guard lock(g_p.mutex);
        g_p.pendingFrame = frame;
    }
    g_p.cv.notify_one();
}

bool PresenterHasFocus()
{
    return !g_p.window || GetForegroundWindow() == g_p.window;
}

// The swap chain to a new output size (the renderer's ApplyOutputSize, the host GPU idle).
void PresenterResize(uint32_t width, uint32_t height)
{
    if (!g_p.swapChain)
        return;
    for (auto& b : g_p.backBuffers)
        b.Reset();
    if (FAILED(g_p.swapChain->ResizeBuffers(kBuffers, width, height, DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING)))
    {
        Log("presenter: swap chain resize to %ux%u failed", width, height);
        return;
    }
    for (UINT i = 0; i < kBuffers; i++)
    {
        Check(g_p.swapChain->GetBuffer(i, IID_PPV_ARGS(&g_p.backBuffers[i])), "GetBuffer");
        D3D12_CPU_DESCRIPTOR_HANDLE h = g_p.rtvHeap->GetCPUDescriptorHandleForHeapStart();
        h.ptr += SIZE_T(i) * g_p.rtvStride;
        g_p.device->CreateRenderTargetView(g_p.backBuffers[i].Get(), nullptr, h);
    }
    Log("presenter: swap chain %ux%u", width, height);
}

void regpu::SetWindowSize(uint32_t width, uint32_t height)
{
    SizeWindow(g_p.window, width, height);
}

void PresenterSubmitImage(uint32_t width, uint32_t height, std::vector<uint8_t> rgba)
{
    if (width != kWidth || height != kHeight)
        return;
    {
        std::lock_guard lock(g_p.mutex);
        g_p.image = std::move(rgba);
        g_p.pendingFrame++;
    }
    g_p.cv.notify_one();
}

ID3D12Device* RenderDevice()
{
    EnsureDevice();
    return g_device.Get();
}

ID3D12CommandQueue* RenderQueue()
{
    EnsureDevice();
    return g_queue.Get();
}

void PresenterUseRenderer()
{
    g_rendererActive = true;
}

bool PresenterRecordCopy(ID3D12GraphicsCommandList* list, ID3D12Resource* src, ID3D12Resource* overlay, uint32_t overlayW, uint32_t overlayH)
{
    if (!g_p.ready)
        return false;
    const UINT i = g_p.swapChain->GetCurrentBackBufferIndex();
    ID3D12Resource* backBuffer = g_p.backBuffers[i].Get();
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = backBuffer;
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_PRESENT;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    // The image is the render size; with a forced aspect ratio it is smaller than the back
    // buffer and goes in the middle, on black.
    const D3D12_RESOURCE_DESC sd = src->GetDesc(), bd = backBuffer->GetDesc();
    const UINT offsetX = UINT((bd.Width - std::min(sd.Width, bd.Width)) / 2), offsetY = (bd.Height - std::min(sd.Height, bd.Height)) / 2;
    if (offsetX || offsetY)
    {
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
        list->ResourceBarrier(1, &b);
        D3D12_CPU_DESCRIPTOR_HANDLE rtv = g_p.rtvHeap->GetCPUDescriptorHandleForHeapStart();
        rtv.ptr += SIZE_T(i) * g_p.rtvStride;
        const float black[4] = { 0, 0, 0, 1 };
        list->ClearRenderTargetView(rtv, black, 0, nullptr);
        b.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
        b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    }
    list->ResourceBarrier(1, &b);
    if (!offsetX && !offsetY && sd.Width == bd.Width && sd.Height == bd.Height)
        list->CopyResource(backBuffer, src);
    else
    {
        D3D12_TEXTURE_COPY_LOCATION dst{}, from{};
        dst.pResource = backBuffer;
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        from.pResource = src;
        from.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        const D3D12_BOX box{ 0, 0, 0, UINT(std::min(sd.Width, bd.Width)), std::min(sd.Height, bd.Height), 1 };
        list->CopyTextureRegion(&dst, offsetX, offsetY, 0, &from, &box);
    }
    if (overlay)
    {
        D3D12_TEXTURE_COPY_LOCATION dst{}, from{};
        dst.pResource = backBuffer;
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        from.pResource = overlay;
        from.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        list->CopyTextureRegion(&dst, offsetX + 24, offsetY + 24, 0, &from, nullptr);
    }
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_PRESENT;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    std::swap(b.Transition.StateBefore, b.Transition.StateAfter);
    list->ResourceBarrier(1, &b);
    return true;
}

void PresenterPresent()
{
    // Vsync: wait for the display's refresh; off: present at once (tearing allowed).
    if (g_p.ready)
    {
        if (GetSettings().vsync)
            g_p.swapChain->Present(1, 0);
        else
            g_p.swapChain->Present(0, DXGI_PRESENT_ALLOW_TEARING);
    }
}

uint32_t DisplayRefreshHz()
{
    DEVMODEW mode{};
    mode.dmSize = sizeof(mode);
    if (EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &mode) && mode.dmDisplayFrequency > 1)
        return mode.dmDisplayFrequency;
    return 60;
}
