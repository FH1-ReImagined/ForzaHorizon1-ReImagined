// List trace (FORZA_LIST_TRACE=ON builds only, TU4, Linux; docs/native-renderer.md "M4"): what the
// game's RunCommandBuffer calls run, for the native renderer's model list work. Off (the default),
// this header defines nothing that costs: the hooks' lines below compile to nothing.
//
//   FORZA_LIST_TRACE_CONTEXT(kind, pass, layer);  the pass group a draw function is drawing
//   FORZA_LIST_TRACE_RECORDED_LIST();             in the hook of sub_824467A8 (RunRecordedList)
//   FORZA_LIST_TRACE_RUN();                       in the hook of sub_824469B0 (RunCommandBuffer)
//
// The two RUN lines read ctx and base of the hook they are in, before the original runs.
#pragma once

#include <cstdint>

#if FORZA_LIST_TRACE

#include <rex/ppc/context.h>

namespace forza::list_trace {

enum class Context : uint8_t { kNone, kTrack, kProcedural, kCar };

void RecordedList(PPCContext& ctx, uint8_t* base);
void Run(PPCContext& ctx, uint8_t* base);

struct ContextState {
  Context kind = Context::kNone;
  uint32_t pass = 0, layer = 0;
};
ContextState& CurrentContext();

class ContextScope {
 public:
  ContextScope(Context kind, uint32_t pass, uint32_t layer) : saved_(CurrentContext()) {
    CurrentContext() = ContextState{kind, pass, layer};
  }
  ~ContextScope() { CurrentContext() = saved_; }
  ContextScope(const ContextScope&) = delete;
  ContextScope& operator=(const ContextScope&) = delete;

 private:
  ContextState saved_;
};

}  // namespace forza::list_trace

#define FORZA_LIST_TRACE_CONTEXT(kind, pass, layer)                                       \
  ::forza::list_trace::ContextScope forza_list_trace_context_(                            \
      ::forza::list_trace::Context::kind, uint32_t(pass), uint32_t(layer))
#define FORZA_LIST_TRACE_RECORDED_LIST() ::forza::list_trace::RecordedList(ctx, base)
#define FORZA_LIST_TRACE_RUN() ::forza::list_trace::Run(ctx, base)

#else

#define FORZA_LIST_TRACE_CONTEXT(kind, pass, layer) static_cast<void>(0)
#define FORZA_LIST_TRACE_RECORDED_LIST() static_cast<void>(0)
#define FORZA_LIST_TRACE_RUN() static_cast<void>(0)

#endif
