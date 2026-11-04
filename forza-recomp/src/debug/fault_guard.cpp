// forza - stop on a fault nothing handles instead of re-running it forever.
//
// The runtime's signal handler offers a fault to its handlers (MMIO ranges, GPU
// write watches); when none takes it, it returns and the faulting instruction runs
// again. A null read in recompiled code then faults forever, and the runtime logs
// "Unhandled guest access violation" each time: 4 MB of log a second (a 260 MB log in
// one minute when a copied save was loaded). A trap (__builtin_trap, e.g. a switch case
// out of range) loops the same way.
//
// This handler is installed after the runtime's, so it only sees faults nobody took.
// It logs the fault once with the host call stack as module+offset (resolve with
// `addr2line -f -e forza-recomp/out/build/linux/forza 0xOFFSET`; frames in sub_XXXXXXXX
// are the recompiled guest functions; on Windows the pc and the frames are forza.exe+offset
// followed by the guest function, sub_XXXXXXXX+0xN, from the recompiled functions' table:
// a player's forza.exe is built on their machine and its layout follows their codegen
// history, #83) and exits with status 134. No core dump: one of a
// process mapping 5+ GB of guest memory takes minutes and gigabytes. FORZA_FAULT=wait
// parks the thread instead, so gdb can attach (gdb -p PID, then `thread apply all bt`).
//
// LogGuestThreads (called when the window closes) logs every guest thread's last call site
// and call chain, so the log of a game that hangs without a fault shows where it waits; the
// stall watchdog (src/debug/stall_watch.cpp) logs the same for the main loop and render
// threads during a long frame.

#include "../platform/guest_memory.h"

#include <rex/exception_handler.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/thread_state.h>
#include <rex/system/xmemory.h>
#include <rex/system/xthread.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#include <execinfo.h>
#endif
#include <csignal>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <set>
#include <string>
#include <thread>
#include <vector>

namespace forza {

namespace {

constexpr uint64_t kGuestVirtualBase = 0x100000000ull;  // Linux: guest X is host base + X

bool Wait() {
  const char* v = std::getenv("FORZA_FAULT");
  return v && std::strcmp(v, "wait") == 0;
}

// The recompiled functions' host addresses, sorted, with their guest addresses
// (PPCFuncMappings), for naming the guest function a host pc is in. Needed on Windows: the
// executable has no symbols, and which generated file holds a function depends on the
// player's codegen history (codegen.partition.json keeps every earlier assignment), so
// forza.exe+offset does not map onto another build (#83). Only the main run of entries
// counts: hooks that replace a function live in src/, among other host code.
struct HostFunc {
  uint64_t host;
  uint32_t guest;
};
std::vector<HostFunc> g_host_funcs;

void BuildHostFuncs() {
  for (const PPCFuncMapping* m = PPCFuncMappings; m->host; ++m) {
    g_host_funcs.push_back({reinterpret_cast<uint64_t>(m->host), uint32_t(m->guest)});
  }
  std::sort(g_host_funcs.begin(), g_host_funcs.end(),
            [](const HostFunc& a, const HostFunc& b) { return a.host < b.host; });
  // The generated code is one contiguous run; split at gaps over 1 MB, keep the longest.
  size_t best_begin = 0, best_end = 0;
  for (size_t begin = 0, i = 1; i <= g_host_funcs.size(); ++i) {
    if (i == g_host_funcs.size() || g_host_funcs[i].host - g_host_funcs[i - 1].host > 0x100000) {
      if (i - begin > best_end - best_begin) best_begin = begin, best_end = i;
      begin = i;
    }
  }
  g_host_funcs.erase(g_host_funcs.begin() + best_end, g_host_funcs.end());
  g_host_funcs.erase(g_host_funcs.begin(), g_host_funcs.begin() + best_begin);
}

// " sub_XXXXXXXX+0xN" when pc is in a recompiled function (the start of the function at or
// below it; a function clang inlined into its caller shows as the caller), else "".
std::string GuestFunc(uint64_t pc) {
  if (g_host_funcs.empty() || pc < g_host_funcs.front().host ||
      pc - g_host_funcs.back().host > 0x40000) {
    return {};
  }
  auto it = std::upper_bound(g_host_funcs.begin(), g_host_funcs.end(), pc,
                             [](uint64_t p, const HostFunc& f) { return p < f.host; });
  --it;
  return fmt::format(" sub_{:08X}+0x{:X}", it->guest, pc - it->host);
}

// "module+0xOFFSET" for a host code address (Windows: the image the address is in, so a
// player's crash log maps onto the released forza.exe; elsewhere the plain address).
std::string HostAddr(uint64_t pc) {
#ifdef _WIN32
  HMODULE module = nullptr;
  wchar_t path[MAX_PATH];
  if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                             GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                         reinterpret_cast<LPCWSTR>(pc), &module) &&
      GetModuleFileNameW(module, path, MAX_PATH)) {
    const wchar_t* name = std::wcsrchr(path, L'\\');
    name = name ? name + 1 : path;
    std::string narrow;
    for (; *name; ++name) narrow += *name < 128 ? char(*name) : '?';
    return fmt::format("{}+0x{:X}{}", narrow, pc - reinterpret_cast<uint64_t>(module),
                       GuestFunc(pc));
  }
#endif
  return fmt::format("0x{:X}{}", pc, GuestFunc(pc));
}

void LogStack() {
#ifdef _WIN32
  void* frames[48];
  const USHORT n = CaptureStackBackTrace(0, 48, frames, nullptr);
  for (USHORT i = 0; i < n; ++i) {
    REXLOG_ERROR("  #{} {}", i, HostAddr(reinterpret_cast<uint64_t>(frames[i])));
  }
#else
  void* frames[48];
  const int n = backtrace(frames, 48);
  for (int i = 0; i < n; ++i) {
    Dl_info info{};
    if (dladdr(frames[i], &info) && info.dli_fname) {
      const char* name = std::strrchr(info.dli_fname, '/');
      REXLOG_ERROR("  #{} {}+0x{:X}{}{}", i, name ? name + 1 : info.dli_fname,
                   reinterpret_cast<uintptr_t>(frames[i]) -
                       reinterpret_cast<uintptr_t>(info.dli_fbase),
                   info.dli_sname ? " " : "", info.dli_sname ? info.dli_sname : "");
    } else {
      REXLOG_ERROR("  #{} {}", i, frames[i]);
    }
  }
#endif
}

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

// Whether the guest word at `address` can be read: its page is allocated and readable.
// A thread that has exited (or one whose context still holds an old r1) can leave a
// stack pointer into a released stack; reading it faulted (#79: closing the window
// logged "read of guest 0x708E0003" from LogGuestThreads and aborted).
bool GuestReadable(uint32_t address) {
  auto* kernel = rex::system::kernel_state();
  if (!kernel || !kernel->memory()) return false;
  auto* heap = kernel->memory()->LookupHeap(address);
  uint32_t protect = 0;
  return heap && heap->QueryProtect(address, &protect) &&
         (protect & rex::memory::kMemoryProtectRead) &&
         heap->QueryProtect(address + 3, &protect) &&
         (protect & rex::memory::kMemoryProtectRead);
}

// Return addresses along a guest stack's back chain (saved LR at back chain - 8), from
// the frame at r1 outwards. Stops at the first link that does not look like one, or
// that points at memory that is not readable.
std::string GuestChain(uint8_t* base, uint32_t frame, int max_frames) {
  std::string chain;
  for (int i = 0; i < max_frames && frame; ++i) {
    if (frame < 0x40000000 || frame >= 0x80000000) break;  // guest stacks (0x7xxxxxxx) only
    if ((frame & 3) || !GuestReadable(frame)) break;
    const uint32_t parent = Load32(forza::GuestPtr(base, frame));
    if (parent <= frame || parent - frame > 0x100000 || (parent & 7)) break;
    if (!GuestReadable(parent - 8)) break;
    const uint32_t lr = Load32(forza::GuestPtr(base, parent - 8));
    if (lr < 0x82000000 || lr >= 0x90000000) break;
    chain += fmt::format(" {:08X}", lr);
    frame = parent;
  }
  return chain;
}

// The guest registers of the faulting thread (as last stored in its PPCContext: values
// the recompiled code keeps in host registers may be newer) and its guest call chain
// from the stack back-chain (saved LR at back chain - 8).
void LogGuestState() {
  if (!rex::system::XThread::IsInThread()) return;
  auto* thread = rex::system::XThread::GetCurrentThread();
  auto* state = thread ? thread->thread_state() : nullptr;
  PPCContext* ctx = state ? state->context() : nullptr;
  auto* kernel = rex::system::kernel_state();
  if (!ctx || !kernel) return;
  uint8_t* base = kernel->memory()->virtual_membase();
  std::string regs;
  // PPCContext keeps r3 first (r3, r0, r1, r2, r4, ...): index by name. Until #83 the dump
  // walked from r0, so its r3..r31 were r4..r31 and lr, and r3 was missing.
  const PPCRegister* gpr[32] = {
      &ctx->r0,  &ctx->r1,  &ctx->r2,  &ctx->r3,  &ctx->r4,  &ctx->r5,  &ctx->r6,  &ctx->r7,
      &ctx->r8,  &ctx->r9,  &ctx->r10, &ctx->r11, &ctx->r12, &ctx->r13, &ctx->r14, &ctx->r15,
      &ctx->r16, &ctx->r17, &ctx->r18, &ctx->r19, &ctx->r20, &ctx->r21, &ctx->r22, &ctx->r23,
      &ctx->r24, &ctx->r25, &ctx->r26, &ctx->r27, &ctx->r28, &ctx->r29, &ctx->r30, &ctx->r31};
  for (int i = 0; i < 32; ++i) {
    regs += fmt::format("{}r{}={:08X}", i % 8 ? " " : (i ? "\n    " : "    "), i, gpr[i]->u32);
  }
  REXLOG_ERROR("Guest thread {:08X} registers (lr={:08X} ctr={:08X}):\n{}",
               thread->handle(), uint32_t(ctx->lr), ctx->ctr.u32, regs);
  REXLOG_ERROR("Guest call chain (return addresses):{}", GuestChain(base, ctx->r1.u32, 24));
}

bool OnUnhandledFault(rex::arch::Exception* ex, void* /*data*/) {
  using Code = rex::arch::Exception::Code;
  if (ex->code() == Code::kAccessViolation) {
    const uint64_t addr = ex->fault_address();
    const bool guest = addr >= kGuestVirtualBase && addr < kGuestVirtualBase + 0x100000000ull;
    REXLOG_CRITICAL("Unhandled {} fault at host 0x{:X}{} (pc {})",
                    ex->access_violation_operation() ==
                            rex::arch::Exception::AccessViolationOperation::kWrite
                        ? "write"
                        : "read",
                    addr, guest ? fmt::format(" = guest 0x{:08X}", uint32_t(addr)) : "",
                    HostAddr(ex->pc()));
  } else {
    REXLOG_CRITICAL("Unhandled illegal instruction (trap) at pc {}", HostAddr(ex->pc()));
  }
  // The guest state first: unwinding the host stack can fault again (an indirect call to a
  // data address leaves a frame the unwinder cannot read), and then nothing more is logged.
  LogGuestState();
  if (auto logger = rex::GetLogger()) logger->flush();
  LogStack();
  if (Wait()) {
    REXLOG_CRITICAL("FORZA_FAULT=wait: thread parked for a debugger");
    for (;;) std::this_thread::sleep_for(std::chrono::hours(1));
  }
  std::_Exit(134);
}

// A REX_FATAL (e.g. "Call to invalid or unregistered function at guest address 0", a
// call through a null or freed object's vtable) logs one line and aborts: log where the
// guest was, too (its LR is the indirect call's return address).
void OnAbort(int sig) {
  std::signal(sig, SIG_DFL);
  REXLOG_CRITICAL("Aborted (signal {})", sig);
  LogStack();
  LogGuestState();
  if (auto logger = rex::GetLogger()) logger->flush();
  std::_Exit(134);
}

}  // namespace

// One guest thread as last stored in its PPCContext: its handle, start address, the guest
// return address of its last call (lr: the call site of a kernel wait it is blocked in) and
// its call chain (a thread running guest code without calls may be further on). Read
// without stopping the thread, so a running thread's chain can be cut short. "" when the
// thread has no context.
std::string DescribeGuestThread(rex::system::XThread& thread) {
  auto* kernel = rex::system::kernel_state();
  auto* state = thread.thread_state();
  PPCContext* ctx = state ? state->context() : nullptr;
  if (!kernel || !ctx) return {};
  std::string host;  // the [tNNNN] of its log lines (Windows thread ids; not on Linux)
#ifdef _WIN32
  if (auto* t = thread.thread()) host = fmt::format(" t{}", t->system_id());
#endif
  return fmt::format("thread {:08X}{}{} start {:08X} lr {:08X} chain{}", thread.handle(), host,
                     thread.main_thread() ? " (main)" : "",
                     thread.creation_params()->start_address, uint32_t(ctx->lr),
                     GuestChain(kernel->memory()->virtual_membase(), ctx->r1.u32, 16));
}

// Where every guest thread is when the player closes the window (#76): a game that loads
// forever logs nothing else, so this is what a hang report shows. One line per thread
// (DescribeGuestThread).
void LogGuestThreads(const char* why) {
  auto* kernel = rex::system::kernel_state();
  if (!kernel || !kernel->object_table()) return;
  auto threads = kernel->object_table()->GetObjectsByType<rex::system::XThread>();
  REXLOG_INFO("FORZA: guest threads {}:", why);
  std::set<rex::system::XThread*> seen;  // a thread is listed once per handle
  for (auto& thread : threads) {
    if (!thread || !thread->is_guest_thread() || !seen.insert(thread.get()).second) continue;
    const std::string line = DescribeGuestThread(*thread);
    if (!line.empty()) REXLOG_INFO("  {}", line);
  }
}

void InstallFaultGuard() {
  BuildHostFuncs();
  rex::arch::ExceptionHandler::Install(OnUnhandledFault, nullptr);
  std::signal(SIGABRT, OnAbort);
}

}  // namespace forza
