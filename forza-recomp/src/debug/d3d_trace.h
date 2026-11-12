// D3D trace (FORZA_D3D_TRACE=ON builds only, TU4, Linux; docs/native-renderer.md): counts the
// calls of the Xbox D3D library's functions and tags the command-buffer dwords each one writes,
// for the GPU plugin's PM4 trace. Off (the default), this header defines nothing that costs.
//
// A game hook on a function of d3d_trace_hooks.inc (kind kChained) starts with
//   FORZA_D3D_TRACE_SCOPE(TheHookedName);
// TheHookedName may be the file's #define of the sub_ name.
#pragma once

#if FORZA_D3D_TRACE

#include <rex/ppc/context.h>

#include <cstdint>

namespace forza::d3d_trace {

enum : uint8_t {
#define D3D_TRACE_HOOK(id, function, kind, role) kId_##function = id,
#include "d3d_trace_hooks.inc"
#undef D3D_TRACE_HOOK
};

void Enter(uint32_t id, bool device, PPCContext& ctx, uint8_t* base);
void Leave(uint8_t* base);

class Scope {
 public:
  Scope(uint32_t id, PPCContext& ctx, uint8_t* base) : base_(base) { Enter(id, false, ctx, base); }
  ~Scope() { Leave(base_); }
  Scope(const Scope&) = delete;
  Scope& operator=(const Scope&) = delete;

 private:
  uint8_t* base_;
};

}  // namespace forza::d3d_trace

#define FORZA_D3D_TRACE_SCOPE_(function) \
  ::forza::d3d_trace::Scope forza_d3d_trace_scope_(::forza::d3d_trace::kId_##function, ctx, base)
#define FORZA_D3D_TRACE_SCOPE(function) FORZA_D3D_TRACE_SCOPE_(function)

#else

#define FORZA_D3D_TRACE_SCOPE(function) static_cast<void>(0)

#endif
