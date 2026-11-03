#pragma once
// For hosts whose kernel already implements the Vd* video exports (ReXGlue): the GPU services
// those exports reach. A XenonRecomp port doesn't need this; ReGPU provides the exports itself
// (REGPU_GUEST_EXPORTS).
#include <cstdint>

namespace regpu::driver
{
// VdInitializeRingBuffer: ptr is the ring's physical address, its size 1 << (sizeLog2 + 3) bytes.
void InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2);
// VdEnableRingBufferRPtrWriteBack: ptr is the physical address the read pointer is copied to.
void EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockSizeLog2);
// VdSetGraphicsInterruptCallback. Starts ReGPU's interrupt and command processor threads.
void SetInterruptCallback(uint32_t callback, uint32_t userData);

// The guest's vblank rate, for a host whose VdQueryVideoMode reports its own refresh rate: the
// title paces itself by it. 0 returns to ReGPU's own (twice the frame cap, 120-720 Hz).
void SetRefreshRate(double hz);

// Sets a REGPU_* live switch, for a host that needs one on by default. Unlike setting the
// environment variable, this also reaches switches read when the library loads.
void SetSwitch(const char* name, bool on);

// The register file (guest 0x7FC80000 + index * 4), for the host's MMIO handler. Without
// REGPU_GUEST_EXPORTS the registers live in host memory, so guest accesses must come here.
uint32_t ReadRegister(uint32_t index);
void WriteRegister(uint32_t index, uint32_t value);
} // namespace regpu::driver
