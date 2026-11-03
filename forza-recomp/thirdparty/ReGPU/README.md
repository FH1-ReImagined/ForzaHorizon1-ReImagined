# ReGPU

The Xbox 360 GPU (Xenos), on Direct3D 12, for native PC ports of Xbox 360 games made with
[XenonRecomp](https://github.com/hedge-dev/XenonRecomp).

ReGPU takes the GPU's half of a port off your hands. The recompiled game talks to it exactly as it
talked to the console: it writes command packets into a ring buffer and calls the kernel's `Vd*` video
functions. ReGPU runs the command processor, translates the Xenos shader microcode to HLSL, emulates
EDRAM, resolves, MSAA and the texture formats, presents the frames, and can upscale them (FSR 1,
optionally FSR 3 and DLSS).

It came out of a working port, Forza Motorsport 2, where it renders the whole game. Everything
specific to that game has been removed; what remains is the console's behaviour, plus hooks for the
workarounds a particular game needs.

## Requirements

- **XenonRecomp.** ReGPU is for XenonRecomp ports and builds against it: it uses XenonRecomp's
  `PPCContext` and calling convention to provide the console's `__imp__Vd*` video exports and to call
  the game's graphics interrupt. Your recompiled game links them like any other import. (Ports made
  some other way are not supported.)
- Windows 10/11, a Direct3D 12 GPU (feature level 12_0), clang-cl (as XenonRecomp's output needs),
  CMake 3.25+, Ninja, Python 3 (a build step embeds compiled shaders).
- The AMD FidelityFX SDK (MIT) for the FSR 1 shader headers. FSR 3 needs its signed runtime DLLs and
  DLSS needs NVIDIA's DLSS SDK; both are off by default.

## Building

ReGPU is a static library your port links. In your port's CMake:

```cmake
set(REGPU_XENONRECOMP "${CMAKE_SOURCE_DIR}/thirdparty/XenonRecomp")   # XenonRecomp checkout
set(REGPU_PPC_DIR     "${CMAKE_SOURCE_DIR}/out/ppc")                  # your game's XenonRecomp output (ppc_config.h)
set(REGPU_FFX         "${CMAKE_SOURCE_DIR}/thirdparty/fidelityfx/Kits/FidelityFX")
add_subdirectory(thirdparty/ReGPU)
target_link_libraries(game PRIVATE regpu)
```

Options: `REGPU_FSR3` (with the FidelityFX signed DLLs), `REGPU_DLSS` and `REGPU_DLSS_DIR`.

Ship Microsoft's `d3dcompiler_47.dll` (the Windows SDK's `Redist\D3D\x64`, redistributable) beside
your executable: ReGPU loads it from there first. Under Wine/Proton the system copy is Wine's own,
which can't compile the translated shaders.

To check that ReGPU builds on its own: `cmake --build build --target regpu_linkcheck`.

## Hooking it up

Everything is in [`include/regpu/regpu.h`](include/regpu/regpu.h). Before the game initialises
its graphics:

```cpp
#include <regpu/regpu.h>

regpu::Config& config = regpu::GetConfig();
config.width = 1920; config.height = 1080;
config.msaa = 4;
config.userDir = savesFolder / "regpu";      // shader and pipeline caches
config.windowTitle = L"My Game";

regpu::Host& host = regpu::GetHost();
host.base = guestMemoryBase;                 // the guest's 4 GB address space
host.alloc = [](bool physical, uint32_t size, uint32_t align) { return MyGuestAlloc(physical, size, align); };
host.createThread = [](const char* name, void (*body)(PPCContext&)) { MyCreateHostGuestThread(name, body); };
host.currentPcr = [] { return MyCurrentThread().pcr; };
host.guestCall = [](PPCContext& ctx, uint32_t address) { MyGuestCall(ctx, address); };
host.interruptLockAcquire = [](int cpu) { MyInterruptLock(cpu); };
host.interruptLockRelease = [] { MyInterruptUnlock(); };

regpu::Start();   // window, device, command processor
```

Your runtime already has these: they are the pieces any XenonRecomp port builds for its own threads
and memory. ReGPU never touches the rest of the kernel (files, threads, XAM, audio), which stays yours.

The renderer currently assumes the game draws at 1280x720, as most Xbox 360 games do (see
[`docs/xenos-notes.md`](docs/xenos-notes.md)).

### Game-specific fixes

When a game needs something the console did that isn't the GPU's job, or a workaround for one of
its shaders, use the optional hooks instead of changing ReGPU:

| Hook | For |
|---|---|
| `rewriteShader` | Edit a shader's translated HLSL before it compiles, and tag it |
| `onDraw` | Set four `float4` constants (`vuser`) for draws with tagged shaders |
| `resolveWriteback` | Copy a resolved image back to memory because the game reads it on the CPU (screenshots, thumbnails, focus readback) |
| `staticShaders`, `bootScreen` | Compile every shader in the game's files on first boot, with your own progress screen |
| `onChar`, `onSwap` | Typed text (cheat codes), a callback per frame |
| `logThreadStacks`, `currentStack`, `slowFrame`, `cpuUsage` | Diagnostics |
| `SetGameWidescreen(true)` | The game itself draws wider than 16:9, so ReGPU leaves the field of view alone |

## Debugging

Start the game with `REGPU_DEBUG=1` and drive it from outside with
[`tools/regpuctl.py`](tools/regpuctl.py): press buttons, take screenshots, capture a frame, edit
shaders live, flip `REGPU_*` switches. [`CLAUDE.md`](CLAUDE.md) is the playbook.
[`tools/xbdm.py`](tools/xbdm.py) and [`tools/xbox_fb.py`](tools/xbox_fb.py) take reference
screenshots and memory reads from a real (modded) console over XBDM.

## Credits and licences

- [XenonRecomp](https://github.com/hedge-dev/XenonRecomp) (MIT), which ReGPU builds against.
- [Xenia](https://github.com/xenia-project/xenia)'s research into the Xenos GPU informed this
  renderer. No Xenia code is included.
- AMD FidelityFX SDK (MIT); NVIDIA DLSS SDK (its own licence, only with `REGPU_DLSS`); Microsoft's
  `d3dcompiler_47.dll` (Windows SDK redistributable).

ReGPU is released under the [MIT licence](LICENSE).
