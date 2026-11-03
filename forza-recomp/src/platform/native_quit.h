#pragma once
#include <cstdint>
#include <functional>
struct PPCContext;
namespace forza::native_quit {
// Native pause owns the dialog. App owns the UI-thread close callback.
void InstallHost(std::function<void(uint64_t)> request);
void Cancel();
bool Consume(uint64_t generation);
bool Open(PPCContext&,uint8_t*,uint32_t screen,uint32_t owner);
bool HasDialog();
bool Available(uint8_t*,uint32_t screen);
bool Choice(PPCContext&,uint8_t*);
uint32_t MainMenuResult(PPCContext&,uint8_t*,uint32_t original);
void ReleaseResult(PPCContext&,uint8_t*,uint32_t result);
// Forward every native result once; desktop uses a separately owned Main Menu
// result only for the duration of the original handler.
void WithChoice(PPCContext&,uint8_t*,const std::function<void()>& handler);
void Title(PPCContext&,uint8_t*,uint32_t screen);
}
