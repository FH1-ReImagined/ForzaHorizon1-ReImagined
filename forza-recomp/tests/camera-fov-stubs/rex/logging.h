#pragma once
template <class... Args>
inline void FovTestLog(const char*, Args&&...) {}
#define REXLOG_INFO(...) FovTestLog(__VA_ARGS__)
