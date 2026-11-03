#pragma once
#include <string>
namespace fmt {template<class...T>std::string format(const char*,T&&...){return {};}}
#define REXLOG_INFO(...) ((void)0)
#define REXLOG_WARN(...) ((void)0)
