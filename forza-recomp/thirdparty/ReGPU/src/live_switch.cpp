#include "live_switch.h"

#include <cstdlib>
#include <map>
#include <mutex>

#include <windows.h>

#include "log.h"

namespace
{
struct Registry
{
    std::mutex mutex;
    std::vector<LiveSwitch*> switches;
    std::map<std::string, bool> pending; // set before the switch first ran
};

Registry& Get()
{
    static Registry r;
    return r;
}

void SetEnv(const std::string& name, bool on)
{
    // getenv() readers of the same name (startup code, other copies) see it too.
    _putenv_s(name.c_str(), on ? "1" : "");
    SetEnvironmentVariableA(name.c_str(), on ? "1" : nullptr);
}
}

LiveSwitch::LiveSwitch(const char* name) : name_(name), value_(false)
{
    Registry& r = Get();
    std::lock_guard lock(r.mutex);
    const char* v = getenv(name);
    bool on = v != nullptr && *v != 0; // (an empty value is how the debug server clears one)
    if (auto it = r.pending.find(name); it != r.pending.end())
        on = it->second;
    value_.store(on, std::memory_order_relaxed);
    r.switches.push_back(this);
}

bool LiveSwitch::Set(const std::string& name, bool on)
{
    Registry& r = Get();
    std::lock_guard lock(r.mutex);
    SetEnv(name, on);
    bool found = false;
    for (LiveSwitch* s : r.switches)
        if (name == s->name_)
        {
            s->value_.store(on, std::memory_order_relaxed);
            found = true;
        }
    if (!found)
        r.pending[name] = on;
    Log("switch: %s %s%s", name.c_str(), on ? "on" : "off", found ? "" : " (not reached yet: kept for when it is)");
    return found;
}

std::vector<std::pair<std::string, bool>> LiveSwitch::List()
{
    Registry& r = Get();
    std::lock_guard lock(r.mutex);
    std::vector<std::pair<std::string, bool>> out;
    for (LiveSwitch* s : r.switches)
        out.emplace_back(s->name_, bool(*s));
    return out;
}
