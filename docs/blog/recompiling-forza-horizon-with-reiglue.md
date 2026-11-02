# Recompiling Forza Horizon with ReIGlue

Forza Horizon came out on the Xbox 360 in 2012. [Forza Horizon 1: ReImagined](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined) runs it as a native x86-64 program on Windows and Linux. A launcher reads the player's own copy of the game, translates its PowerPC executable to C++, and compiles that C++ on the player's PC. No game binary is downloaded.

The toolchain is [ReIGlue](https://github.com/FH1-ReImagined/ReIGlue), the project's fork of the [ReXGlue SDK](https://github.com/rexglue/rexglue-sdk). The repository contains no game code and no game data. Players bring their own copy: a disc image, a Games on Demand package or an extracted folder. The project is not affiliated with, endorsed by or sponsored by Microsoft, Xbox Game Studios, Turn 10 or Playground Games. Forza, Forza Horizon, Xbox and Xbox LIVE are trademarks of Microsoft.

## Static recompilation in one page

An emulator interprets or JIT-compiles the console's PowerPC instructions while the game runs. ReIGlue, like the upstream ReXGlue SDK it is forked from, generates C++ from them ahead of time instead.

ReIGlue's code generator reads a manifest (the fork keeps upstream's command name, `rexglue codegen`). This project's manifest names `default.xex` and the two DLLs the game loads at run time (media playback and Kinect speech). The generator then works in phases: imports and the function table, a scan into code and data, function discovery, gap fill, merge, and a final check that every call target resolves.

Each guest function becomes one C++ function. It takes a context struct that holds the PowerPC registers and a pointer to guest memory. The console is big-endian, so loads and stores swap bytes:

```cpp
// Illustrative, written for this post (not taken from the game): two PowerPC
// instructions and the kind of C++ the generator emits for them.
//   lwz r3, 8(r4)    ->
ctx.r3.u64 = REX_LOAD_U32(ctx.r4.u32 + 8);   // read guest memory at r4+8, byte-swapped
//   add r3, r3, r5   ->
ctx.r3.u64 = ctx.r3.u64 + ctx.r5.u64;
```

Indirect calls (virtual methods, callbacks) look up their target in a table from guest addresses to host functions. On the first codegen run, `default.xex` came out as 76,788 functions and 248 MB of C++.

The other half is ReIGlue's runtime, linked into the program: the kernel and XAM (Xbox 360 system library) functions the game imports (threads, files, memory, content packages), XMA audio and input (SDL or XInput, keyboard and mouse, wheels). ReIGlue's GPU plugin emulates the Xenos GPU on Vulkan or Direct3D 12. ReXGlue derives the runtime and the GPU emulation from Xenia, and many of ReIGlue's changes port later Xenia fixes into them. The CPU code is native and compiled ahead of time; the GPU is still emulated while the game runs. On the project's test PC (Ryzen 7 7800X3D, Radeon RX 9060 XT), that GPU emulation is what limits races above 60 fps.

### ReXGlue and ReIGlue

ReXGlue was started by Tom Clay and is developed by the ReXGlue contributors under the BSD 3-Clause license. It builds on Xenia, and its credits name XenonRecomp and rexdex's recompiler. This project would not exist without it.

ReIGlue is a ReXGlue nightly (0.10.0.24, commit `bd833a2`) with one commit per change on top. It exists because upstream ReXGlue's Xenos GPU emulation lacked many of Xenia's fixes. The first turned up in RecompiledHaloReach, this project's predecessor by the same author: a texture-fetch exponent bias read from the wrong word of the fetch constant, which made textures 256 times too dark. The upstream report ([rexglue/rexglue-sdk#456](https://github.com/rexglue/rexglue-sdk/issues/456)) was closed without taking GPU changes, so the fixes became a patch series, and then the fork. In the fork, each change's commit message says what it changes and why. ReIGlue keeps upstream's history and follows new ReXGlue nightlies by rebasing the series onto them; a change upstream has taken is dropped from it. ReIGlue is not an official ReXGlue project.

At the revision the game pins, ReIGlue's commit list (`docs/reiglue/commits.md`) has 152 numbered changes. Each keeps the number it had in the project's old patch series, which is why numbers such as 0411 appear below.

- **111 general.** GPU accuracy (mostly Xenia fixes), performance, input, runtime, platform and codegen. These are written so any ReXGlue game, or upstream, can take them as they are. The dot-product fix below was reported upstream as [rexglue/rexglue-sdk#491](https://github.com/rexglue/rexglue-sdk/issues/491).
- **37 Forza-specific.** These depend on this game's shaders, render targets or controls: the scene/HUD split that DLSS, FSR 3 and SMAA build on, plus ultrawide, HDR, texture packs, VR and wheel layouts.

The game pins an exact ReIGlue commit (`REIGLUE_COMMIT` in `tools/build_rexglue_sdk.sh`; a tag, if given, must resolve to that commit), and each launcher release carries the ReIGlue build that CI made from its pin. ReIGlue's release tags are never moved. ReXGlue's license, copyright notices and history are kept unchanged; ReIGlue adds a NOTICE file that credits ReXGlue, Xenia and the other BSD-licensed code it adapts.

## From the player's files to a native build

**Reading the copy.** The project's own Python tools read a disc image (recognised by its file-system signature, not its extension) or a Games on Demand package. The launcher checks free disk space and extracts into a temporary folder, so a failed or cancelled run never leaves half a game. The docs record seconds for a disc image on an SSD, and about 15 s for a 7.1 GB package in one test.

**Checking it.** `default.xex` is extracted first and checked before the long copy. The launcher refuses, with the reason, another game, a title-update patch or an executable that already has an update applied. The base game was sold in several printings whose executables are the same build. The one the project's addresses were written for is accepted from its header alone. Any other is decoded and hashed in 64 KB pieces, section by section, and compared with a table of SHA-1 hash prefixes (the table holds hashes only, no game bytes). It is accepted only if every section, code and data, is identical: codegen reads the image, the runtime loads the player's file, and the hints and hooks are addresses in it. The two DLLs are checked the same way.

**Title Update 4 and DLC** are optional. The launcher applies the player's update to their executable itself (about 2 s), and the build then uses a separate manifest and hints for the update's addresses.

**Toolchain.** On Linux the launcher uses the system's clang (21 or newer), CMake and Ninja, or downloads pinned versions checked by SHA-256. On Windows it uses clang-cl, with the Microsoft C runtime and Windows SDK from an installed Visual Studio, or from xwin after the player accepts Microsoft's license. The SDK is ReIGlue at the pinned commit, built by the project's CI and carried by the installer. On Linux the launcher downloads the upstream ReXGlue nightly that ReIGlue is based on and installs ReIGlue's GPU plugin, runtime and code generator over it; the nightly supplies the rest of the SDK's files. On Windows the installer carries the whole SDK, built from ReIGlue.

**Generating and compiling.** Codegen runs on the player's executables ("a minute or two", the launcher says), then CMake and Ninja build, with progress, cancel and resume. On a CPU with AVX2, FMA, BMI2 and MOVBE the generated code targets x86-64-v3; on any other it targets the SSE4.1 baseline that ReIGlue keeps from ReXGlue.

One clean build took 4.6 minutes at 4 jobs, with 346 Ninja steps and a peak of 1.8 GB of memory, including the download of the upstream ReXGlue nightly and codegen. That was an 8-core PC with 31 GB of RAM and clang 23.1.2, with another game running at the same time. The launcher rebuilds only when the project sources, the launcher version, the ReIGlue pin, the title update or the CPU target changes.

**Shader prebuild** is optional (Vulkan) and is covered below.

## What hand-written code adds

Generated and hand-written code never share a file. Generated C++ goes to `forza-recomp/generated/` (`generated_tu4/` for Title Update 4). It is git-ignored and never published. The project's code is in `forza-recomp/src/`. TOML hints in `forza-recomp/hints/` give the generator function starts it misses, library routines to replace with native ones, and "mid-asm" hooks that call C++ at one instruction.

**Function overrides** use the linker. The generator emits every guest function as `__imp__sub_XXXXXXXX`, with a weak alias `sub_XXXXXXXX` that all generated calls go to. A strong definition in the project's code replaces the alias at link time and can still call the original:

```cpp
// Illustrative, written for this post; the project's frame-time hooks have this shape.
extern "C" REX_FUNC(__imp__sub_XXXXXXXX);    // the generated original
extern "C" REX_FUNC(sub_XXXXXXXX) {          // the replacement every caller now reaches
  __imp__sub_XXXXXXXX(ctx, base);            // run the original...
  ctx.f1.f64 = Adjust(ctx.f1.f64);           // ...then change its floating-point result
}
```

**Kernel and XAM imports** are overridden by defining the import's `__imp__` symbol in the project's code. That definition takes precedence over the runtime library's, and the original stays reachable: through `dlsym` on Linux (where the override wins by symbol interposition), and as an export of `rexruntime.dll` on Windows. From the project's own code:

```cpp
// forza-recomp/src/kernel/xam_nui.cpp: report "no Kinect" the way Xenia does.
extern "C" REX_FUNC(__imp__XamNuiGetDeviceStatus) {
  static GuestFunc sdk = forza::SdkImport("__imp__XamNuiGetDeviceStatus");
  if (sdk) sdk(ctx, base);  // zeroes the status (not connected)
  ctx.r3.u64 = kNuiNotConnected;
}
```

Hooks are written against the disc's executable. For Title Update 4, `tools/tu_address_map.py` maps their addresses and marks any hooked function the update changed for review.

Some of the hooks:

- **Ultrawide.** A hook wraps the viewport aspect query, which feeds both projection and culling, so a wider camera does not cull the newly visible edges. Together with ReIGlue's native ultrawide rendering, a 3440x1440 window is drawn at 3440x1440 and presented 1:1.
- **Graphics options in the game.** On Title Update 4, a Graphics page in the original pause menu, built from the game's own layout and font, applies changes without a restart.
- **Wheels.** Any wheel SDL detects is presented as the Xbox 360 Wireless Racing Wheel, so the game uses its own wheel layout and options and sends its own force feedback. Hand-written code reads the game's simulation state each frame, and ReIGlue's wheel driver uses it to fade that force feedback out in menus, rewinds and replays. Logitech G29 and G923 owners tested this on Windows.
- **Frame rates above 30**, below.

Online play also runs through hand-written kernel and XAM code that talks to the project's Forza Live server; that is a [separate post](online-back.md).

## Hard problems

**Finding every function.** In `default.xex` the project added 220 entry points that are reached only indirectly (vtables, callback tables, C runtime initializers), plus 357 "orphan" functions that are reached only through pointers the game builds while it runs, so no static scan sees them. The first boot died in a DLL's initializer with the runtime's error "Call to invalid or unregistered function". Tools that read the XEX image found the missing ones. The game's fiber routines save registers and return into another fiber's link register, which cannot work when the host stack does not move, so hints map them to the native fibers in ReIGlue's runtime (inherited from ReXGlue).

**Vector math precision.** Free roam crashed in about half of all loads, while world collision was being set up. The collision code (a GJK distance query) starts from an estimate whose squared length overflows. On the Xbox 360's VMX128 unit an overflowing dot product returns NaN, so the loop's exit test fails and the query runs. Xenia emulates this. The SSE instruction that upstream ReXGlue's code generator emitted returned infinity, the test passed, and the query returned before adding its first point. ReIGlue commit 0025 redoes every non-finite vmsum3fp128/vmsum4fp128 result the way Xenia does, at all 2,883 of these dot products in the game. Before the fix, 2 of 3 test loads faulted; after it, 10 of 10 ran free roam. Other VMX128 differences are open in [#478](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/478).

**Timing above 30 fps.** The game's frame limiter releases the main loop on every second vertical blank, which gives 30 fps at 60 Hz. The `frame_rate` setting runs the emulated vblank at twice the target, and ReIGlue (change 0037) raised upstream ReXGlue's 240 Hz cap on the emulated refresh rate to 480 Hz. Motion was still uneven, because the game times frames with a millisecond tick: at 144 fps it measured 5 to 9 ms for frames 6.94 ms apart. A hook now times each frame from the guest tick clock, aligned to the vblank that released it: the car's movement per frame varied by 2.8% at 144 fps with it, and by 16.5% with the millisecond timer. The physics step is variable, so the simulation keeps real time: 0-100 km/h took 2.38-2.48 s at 60, 120 and 144 fps.

**GPU resolve and readback.** Xenos draws into EDRAM and "resolves" the results into main memory. In the emulation that data stays on the host GPU, so a resolve the game reads with the CPU must be copied back; without that, car thumbnails were noise. Copying back every resolve cost 16% of the frame rate in a race at 144 fps and 21% in free roam. The default now copies a resolve only when its key is new, which costs nothing measurable. Photos still saved wrong ([#54](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/54)): the photo's memory range takes a depth resolve every frame, so its key was never new. ReIGlue 0048 adds the source and format to the key. For photo mode's large tiled picture (the "Big Shot"), ReIGlue 0411 adds a setting that makes resolves of one memory range wait for the GPU and be copied back. A hook points it at the game's two front buffers only while a Big Shot is being made, so other frames don't pay for it ([#552](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/552)).

**Shaders.** The GPU emulation translates shader microcode to SPIR-V and builds a Vulkan pipeline the first time something is drawn, and that stutters. The prebuild ships no shader code, only a list of pipelines (hashes and decoded render state), and rebuilds the shaders from the player's files. Vertex shaders were the hard part: the console's Direct3D runtime patches each one when it is bound, so no stored hash matches the shader as it sits in the files. The list records the changed fields per instruction (indices and vertex layout values, no instruction code), and the launcher rebuilds the exact shader and checks its hash; 97.3% of the pipeline descriptions stored in test sessions could be rebuilt. In one four-minute session (Radeon RX 9060 XT, RADV, empty caches), the game created 593 pipelines during play, with 6 hitches and 19 stutters; after a 4.3 s prebuild, the counts were 76, 0 and 2.

**The emulated GPU's own cost.** The GPU command thread limited races. One cause was in upstream ReXGlue's runtime: each time the game wrote to memory the GPU had already uploaded (thousands of times a second), the runtime re-read all of `/proc/self/maps` while holding a global lock the GPU thread also needs. ReIGlue 0028 asks the kernel with one `PROCMAP_QUERY` ioctl instead (Linux 6.11+). That runtime change and four GPU-plugin changes (ReIGlue 0028-0032) together took a festival race with 7 AI cars at `frame_rate` 60 from 41-42 to 59.7 fps, with the same game binary; either half alone reached about 50 fps. Three later changes (ReIGlue 0036, 0038 and 0039) took the same race at `frame_rate` 144 from 72.8 to 97.2 fps, in an A/B run on the test PC where only the GPU plugin changed. The README's table, which shows 70.1 fps for this race at 144, was measured before those three changes.

## Why build on the player's PC

The main reason is legal. The generated C++ is translated game code, so no prebuilt game is ever shipped: a release holds the launcher, the project's sources, the server software for Forza Live (the project's own online server, not a Microsoft service) and ReIGlue's BSD-licensed SDK pieces, which contain SDK code only. In the words of the launcher's build module, "Nothing built here is ever uploaded or shipped."

There is a practical gain too: the game is compiled for the CPU it runs on. With the x86-64-v3 target, the generated code has 6.9% fewer instructions, and the audio threads used 17% less CPU in the project's measurements. If a v3 build is moved to a CPU without those features, a guard that runs before any generated code lists what is missing and says "Rebuild the game on this PC".

## Limits and what's next

- **Low-end hardware.** Players report about 15–20 fps in free roam on a Steam Deck, and 15–25 fps on 4-core laptops and integrated GPUs; the README advises staying at 30 fps there. Work is open ([#45](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/45), [#263](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/263)), including splitting the GPU command thread in two ([#474](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/474)).
- **NVIDIA.** Direct3D 12 flickers ([#46](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/46)), and render scale 3 shows artifacts ([#47](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/47)). Several crash reports are open.
- **macOS** is a source-build experiment. **VR** is experimental and has been tested on a Meta Quest 3 on Linux.
- **Title Update 4** players and base-game players do not meet online.

The roadmap starts with stability on every PC. Multiplayer and modding come next, and in the long run, readable C++ for the game's systems.

## Links

- Project: <https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined>
- ReIGlue, the project's fork of ReXGlue: <https://github.com/FH1-ReImagined/ReIGlue>
- Upstream ReXGlue SDK: <https://github.com/rexglue/rexglue-sdk>
- Website: <https://forza.dyotson.dev>
- Discord: <https://discord.gg/yu7zScdcWw>
