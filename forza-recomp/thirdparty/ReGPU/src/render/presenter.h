#pragma once
#include <cstdint>
#include <vector>

// Window + D3D12 swap chain (REGPU_HEADLESS=1 disables it).
void PresenterInit();
// The size the game renders at (settings.h: resolution and forced aspect ratio). The swap
// chain is the resolution; the image is centred in it and the swap chain stretches to the
// window.
void RenderSize(uint32_t& width, uint32_t& height);
// The primary display's refresh rate (the frame cap with vsync on).
uint32_t DisplayRefreshHz();
// Called from VdSwap: the guest finished a frame.
void PresenterSubmitFrame(uint64_t frame);
// Shows a CPU-side RGBA8 image (scaled 1:1 into the 1280x720 back buffer).
void PresenterSubmitImage(uint32_t width, uint32_t height, std::vector<uint8_t> rgba);
// Keyboard input goes to the game only while the window has focus.
bool PresenterHasFocus();

struct ID3D12Device;
struct ID3D12CommandQueue;
struct ID3D12GraphicsCommandList;
struct ID3D12Resource;

// Shared D3D12 device and queue (created on first use, also when headless).
ID3D12Device* RenderDevice();
ID3D12CommandQueue* RenderQueue();
// Records copying src (COPY_SOURCE, RGBA8 at RenderSize()) into the current back
// buffer; returns false when there is no window. Call PresenterPresent()
// after executing the list.
// overlay (COPY_SOURCE RGBA8, overlayW x overlayH) is copied over the top-left corner when given.
bool PresenterRecordCopy(ID3D12GraphicsCommandList* list, ID3D12Resource* src, ID3D12Resource* overlay = nullptr, uint32_t overlayW = 0, uint32_t overlayH = 0);
void PresenterPresent();
// The swap chain to a new output size (host GPU idle; renderer.cpp ApplyOutputSize).
void PresenterResize(uint32_t width, uint32_t height);
// Called by the renderer: the presenter stops drawing its own frames.
void PresenterUseRenderer();
