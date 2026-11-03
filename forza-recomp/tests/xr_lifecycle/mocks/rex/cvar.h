#pragma once
#include <string>
#include <initializer_list>
namespace rex::cvar {enum class Lifecycle{kInitOnly};}
struct MockCvar{MockCvar range(double,double){return{};} MockCvar allowed(std::initializer_list<const char*>){return{};} MockCvar lifecycle(rex::cvar::Lifecycle){return{};}};
#define REXCVAR_DEFINE_STRING(n,v,...) inline std::string n=v; inline MockCvar reg_##n=MockCvar{}
#define REXCVAR_DEFINE_DOUBLE(n,v,...) inline double n=v; inline MockCvar reg_##n=MockCvar{}
#define REXCVAR_DEFINE_INT32(n,v,...) inline int n=v; inline MockCvar reg_##n=MockCvar{}
#define REXCVAR_DEFINE_BOOL(n,v,...) inline bool n=v; inline MockCvar reg_##n=MockCvar{}
#define REXCVAR_DECLARE(t,n) extern t n
#define REXCVAR_GET(n) n
