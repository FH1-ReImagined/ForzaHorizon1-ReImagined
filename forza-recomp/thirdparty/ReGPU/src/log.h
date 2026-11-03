#pragma once
#include <cstdarg>
#include <cstdio>
#include <string>
#include <vector>

// Thread-safe log to stderr and regpu.log in the working folder (REGPU_LOG_DIR: another folder).
// REGPU_LOG_ASYNC=1: a writer thread does the writing, so a slow drive doesn't stall the caller.
void Log(const char* fmt, ...);
// Waits (up to 2 s) until queued lines are written: call before the process dies (a crash handler).
void LogFlush();
[[noreturn]] void Fatal(const char* fmt, ...);
// The last n lines (up to 2000 are kept), for the debug server.
std::vector<std::string> LogTail(size_t n);

#define LOG_ONCE(...)                \
    do                               \
    {                                \
        static bool logged_ = false; \
        if (!logged_)                \
        {                            \
            logged_ = true;          \
            Log(__VA_ARGS__);        \
        }                            \
    } while (0)
