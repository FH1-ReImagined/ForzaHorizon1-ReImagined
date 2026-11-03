#pragma once
template<class... T> inline void RadioTestLog(T&&...) {}
#define REXLOG_INFO(...) RadioTestLog(__VA_ARGS__)
