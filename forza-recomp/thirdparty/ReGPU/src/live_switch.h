#pragma once
// An on/off REGPU_* switch that the debug server can flip while the game runs ("switch NAME 0|1",
// tools/regpuctl.py). It starts from the environment variable (set = on), like the getenv() checks it
// replaces, and reading it is one relaxed atomic load. Declare it static where it's used:
//     static LiveSwitch noFoo("REGPU_NO_FOO");
//     if (!noFoo) ...
// A switch registers itself the first time its line runs; a value set before that is kept for it. Some
// switches only matter at startup or feed caches, so flipping them shows from the next frame or not at all.
#include <atomic>
#include <string>
#include <utility>
#include <vector>

class LiveSwitch
{
public:
    explicit LiveSwitch(const char* name);
    LiveSwitch(const LiveSwitch&) = delete;
    LiveSwitch& operator=(const LiveSwitch&) = delete;
    operator bool() const { return value_.load(std::memory_order_relaxed); }
    const char* Name() const { return name_; }

    // The debug server's side: false if the name was never a switch (it's then kept for when one registers).
    static bool Set(const std::string& name, bool on);
    static std::vector<std::pair<std::string, bool>> List(); // the switches registered so far

private:
    const char* name_;
    std::atomic<bool> value_;
};
