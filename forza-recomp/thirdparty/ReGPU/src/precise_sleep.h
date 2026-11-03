#pragma once
// Sleeping to a deadline more exactly than std::this_thread::sleep_until (Sleep's 1 ms
// granularity overshot by up to a millisecond: uneven vblanks and frame pacing). A
// high-resolution waitable timer sleeps to just before the deadline; with spinUs, the rest is
// spun out (for the frame limiter and vblanks; guest sleeps don't spin).
#include <windows.h>

#include <chrono>
#include <thread>

inline void PreciseSleepUntil(std::chrono::steady_clock::time_point deadline, int64_t spinUs = 0)
{
    using namespace std::chrono;
    thread_local HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    auto left = duration_cast<microseconds>(deadline - steady_clock::now()).count();
    if (left <= 0)
        return;
    if (!timer)
    {
        std::this_thread::sleep_until(deadline);
        return;
    }
    if (left > spinUs)
    {
        LARGE_INTEGER due;
        due.QuadPart = -int64_t(left - spinUs) * 10; // relative, 100 ns units
        if (SetWaitableTimerEx(timer, &due, 0, nullptr, nullptr, nullptr, 0))
            WaitForSingleObject(timer, INFINITE);
        else
            std::this_thread::sleep_until(deadline - microseconds(spinUs));
    }
    while (steady_clock::now() < deadline)
        YieldProcessor();
}
