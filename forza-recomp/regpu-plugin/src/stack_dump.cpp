// Host::logThreadStacks: every thread's host stack, for ReGPU's hang reports.
#include "stack_dump.h"

#include <cstdio>
#include <mutex>
#include <string>
#include <vector>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dbghelp.h>
#include <tlhelp32.h>

#include <rex/logging.h>

namespace glue2gpu
{
namespace
{
constexpr int kMaxFrames = 32;

struct Raw
{
    DWORD tid = 0;
    int frames = 0;
    DWORD64 pc[kMaxFrames]{};
};

// Unwinds a suspended thread from its context. Nothing here allocates or takes a lock the
// suspended thread might hold.
void Unwind(CONTEXT ctx, Raw& out)
{
    for (out.frames = 0; out.frames < kMaxFrames && ctx.Rip; out.frames++)
    {
        out.pc[out.frames] = ctx.Rip;
        DWORD64 imageBase = 0;
        PRUNTIME_FUNCTION fn = RtlLookupFunctionEntry(ctx.Rip, &imageBase, nullptr);
        if (!fn)
        {
            // A leaf function: the return address is on top of the stack.
            ctx.Rip = *reinterpret_cast<DWORD64*>(ctx.Rsp);
            ctx.Rsp += 8;
            continue;
        }
        void* handlerData = nullptr;
        DWORD64 establisher = 0;
        RtlVirtualUnwind(UNW_FLAG_NHANDLER, imageBase, ctx.Rip, fn, &ctx, &handlerData, &establisher, nullptr);
    }
}

std::string Describe(DWORD64 pc)
{
    HANDLE process = GetCurrentProcess();
    char buffer[sizeof(SYMBOL_INFO) + 256] = {};
    auto* symbol = reinterpret_cast<SYMBOL_INFO*>(buffer);
    symbol->SizeOfStruct = sizeof(SYMBOL_INFO);
    symbol->MaxNameLen = 255;
    char module[MAX_PATH] = "?";
    HMODULE handle = nullptr;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCSTR>(pc), &handle))
    {
        GetModuleFileNameA(handle, module, sizeof(module));
        if (const char* slash = strrchr(module, '\\'))
            memmove(module, slash + 1, strlen(slash));
    }
    char line[512];
    DWORD64 displacement = 0;
    if (SymFromAddr(process, pc, &displacement, symbol))
    {
        IMAGEHLP_LINE64 source = { sizeof(source) };
        DWORD column = 0;
        if (SymGetLineFromAddr64(process, pc, &column, &source))
            snprintf(line, sizeof(line), "%s!%s+0x%llx (%s:%lu)", module, symbol->Name, (unsigned long long)displacement,
                     source.FileName, source.LineNumber);
        else
            snprintf(line, sizeof(line), "%s!%s+0x%llx", module, symbol->Name, (unsigned long long)displacement);
    }
    else
        snprintf(line, sizeof(line), "%s+0x%llx", module, (unsigned long long)(pc - reinterpret_cast<DWORD64>(handle)));
    return line;
}

std::string ThreadName(DWORD tid)
{
    std::string name;
    if (HANDLE h = OpenThread(THREAD_QUERY_LIMITED_INFORMATION, FALSE, tid))
    {
        PWSTR wide = nullptr;
        if (SUCCEEDED(GetThreadDescription(h, &wide)) && wide)
        {
            char narrow[128] = {};
            WideCharToMultiByte(CP_UTF8, 0, wide, -1, narrow, sizeof(narrow) - 1, nullptr, nullptr);
            name = narrow;
            LocalFree(wide);
        }
        CloseHandle(h);
    }
    return name;
}
} // namespace

void LogThreadStacks(const char* why)
{
    static std::mutex mutex;
    std::lock_guard lock(mutex);

    // Collect first (allocating only while every thread runs), then suspend one at a time.
    std::vector<DWORD> tids;
    const DWORD pid = GetCurrentProcessId(), self = GetCurrentThreadId();
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te = { sizeof(te) };
    for (BOOL ok = Thread32First(snapshot, &te); ok; ok = Thread32Next(snapshot, &te))
        if (te.th32OwnerProcessID == pid && te.th32ThreadID != self)
            tids.push_back(te.th32ThreadID);
    CloseHandle(snapshot);

    std::vector<Raw> raws(tids.size());
    for (size_t i = 0; i < tids.size(); i++)
    {
        raws[i].tid = tids[i];
        HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT, FALSE, tids[i]);
        if (!h)
            continue;
        if (SuspendThread(h) != DWORD(-1))
        {
            CONTEXT ctx = {};
            ctx.ContextFlags = CONTEXT_FULL;
            if (GetThreadContext(h, &ctx))
                Unwind(ctx, raws[i]);
            ResumeThread(h);
        }
        CloseHandle(h);
    }

    static std::once_flag symbols;
    std::call_once(symbols, [] {
        SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_LOAD_LINES);
        SymInitialize(GetCurrentProcess(), nullptr, TRUE);
    });

    // Written to a file beside the executable too: the log may be the thing that's stuck.
    std::FILE* file = std::fopen("regpu_stacks.txt", "a");
    auto emit = [&](const std::string& s) {
        REXLOG_ERROR("{}", s);
        if (file)
            std::fprintf(file, "%s\n", s.c_str());
    };
    emit(std::string("regpu: thread stacks (") + why + "), " + std::to_string(raws.size()) + " threads");
    for (const Raw& r : raws)
    {
        if (!r.frames)
            continue;
        emit("  thread " + std::to_string(r.tid) + " " + ThreadName(r.tid));
        for (int f = 0; f < r.frames; f++)
            emit("    " + Describe(r.pc[f]));
    }
    if (file)
        std::fclose(file);
}
} // namespace glue2gpu
