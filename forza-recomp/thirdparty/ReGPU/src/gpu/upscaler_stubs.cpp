// Builds without the DLSS or FidelityFX SDK (REGPU_DLSS / REGPU_FSR3 off): those upscalers report
// unavailable and the renderer falls back as it does on hardware without them.
#include "gpu/fsr3.h"

#ifndef REGPU_DLSS
bool DlssInit(ID3D12Device*) { return false; }
bool DlssEvaluate(ID3D12GraphicsCommandList*, const DlssSetup&, const DlssFrame&) { return false; }
#endif
#ifndef REGPU_FSR3
bool Fsr3Evaluate(ID3D12Device*, ID3D12GraphicsCommandList*, const DlssSetup&, const DlssFrame&, float) { return false; }
#endif
