# Android (ARM64) port: feasibility study and plan

Issue: [#788](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/788).
Study dated 2026-11-11, decisions added 2026-11-12. **Nothing is ported yet.** There is no
Android build to download, and none is promised.

This page is the reference for anyone working on the port: what was found, what was decided,
and the order of the work. It covers the CPU and runtime, the GPU, the performance budget, and
the app shell, distribution and testing. Every claim was checked a second time; claims that
are only estimates, or have no source, are marked as such or listed under
[open questions](#8-open-questions).

Labels:
- [code]: read in the source. Paths without a prefix are in ReIGlue (our ReXGlue fork), read
  at 5408d20, the version pinned in `tools/build_rexglue_sdk.sh`. Line numbers drift; search
  for the named function if they no longer match.
- [measured]: run on a project PC.
- [source]: an external page, linked.
- [estimate]: our extrapolation.

Sizes: XS under 1 day, S 1-3 days, M 1-2 weeks, L 3+ weeks. All sizes are estimates.

## Contents

1. [Verdict](#1-verdict)
2. [Decisions (2026-11-12)](#2-decisions-2026-11-12)
3. [CPU side](#3-cpu-side)
4. [GPU side](#4-gpu-side)
5. [Performance budget](#5-performance-budget)
6. [App shell, distribution, testing](#6-app-shell-distribution-testing)
7. [Path](#7-path)
8. [Open questions](#8-open-questions)

## 1. Verdict

Not yet as a product. Go for a staged bring-up that stops at a measured run on a real
Snapdragon device.

Nothing found so far makes an Android port impossible:
- The generated code already ran on ARM64: about 60 guest fps on an Apple M4 Pro.
- It compiles for aarch64 Linux and against Bionic, Android's C library.
- It needs no JIT.
- Adreno 7xx/8xx meets every Vulkan requirement with no renderer change.

What keeps it from a go:
1. **Performance is unmeasured and probably marginal.** By our estimates only a 2025-26
   flagship (Snapdragon 8 Elite class) might hold a 30 fps cap at the 540p scene. The device is
   likely GPU- and bandwidth-bound, the opposite of the Steam Deck. This is why the target is
   high-end phones and Android handhelds with active cooling (decision 4).
2. **Mali is rejected at device creation today.** Decision 2 takes Mali out of scope, so this
   no longer blocks the plan; the facts are kept in [section 4](#if-mali-ever-becomes-a-target).
3. **No prebuilt game is ever shipped.** On the PC the launcher builds the game from the
   player's own copy. Decision 1 keeps that rule on Android by building on the player's PC, so
   the phone needs no compiler. The on-device compile that the study first assumed is kept
   below as a rejected option.

The first three steps of the [path](#7-path) are cheap and useful even if Android never ships:
an ARM64 Linux build with tests in CI, the 16 KB page bug fix, and an emulator bring-up. Decide
on an Android product only after a real Snapdragon device has run the opening drive and free
roam with numbers.

## 2. Decisions (2026-11-12)

The owner answered the four step-0 questions:

1. **Where the game compiles: on the player's PC.** The PC launcher builds the APK from the
   player's own copy of the game, and the player installs that APK on the phone. There is no
   compiler on the phone. Consequences:
   - The rule "no prebuilt game is ever shipped" holds exactly as on the PC: game code is only
     ever made on the player's own machine.
   - The game library sits in the APK's native library folder, so the Android 14+ rules on
     loading code from app storage, and the question of whether Google Play counts on-device
     compiling as downloaded code, no longer apply.
   - The phone needs no LLVM toolchain, no build service and no build tree. Peak storage on
     the phone drops from about 20 GB to the game files (see
     [Game files](#game-files)).
   - The player needs a PC, and the PC launcher gains an Android target (Android NDK
     cross-build, APK packaging and signing, install over USB or wireless debugging, or a copied
     APK file).
   - Every game update means a rebuild on the PC and a reinstall, signed with the same key.
2. **GPU: Snapdragon (Adreno) only.** Mali (MediaTek Dimensity, Google Tensor, older Exynos),
   Xclipse (newer Exynos) and PowerVR are not targets. The app should refuse them with a clear
   message instead of trying fallbacks.
3. **Signing: SignPath, if it covers Android.** The project was accepted into SignPath's
   open-source program in November 2026. See [Signing](#signing) for what SignPath can and
   cannot sign under decision 1.
4. **Target devices: powerful phones and Android handhelds.** Mainly Android gaming handhelds
   (AYN Thor and Odin class, AYANEO) and gaming phones (RedMagic), which have active cooling.
   Ordinary phones are not a target. By the estimates in
   [section 5](#5-performance-budget), only Snapdragon 8 Elite-class chips are likely to hold
   30 fps; 8 Gen 2 and 8 Gen 3 devices (for example the AYN Thor and Odin 2) are a stretch
   goal until measured.

## 3. CPU side

### What already works

**ARM64 has run.** Our macOS ARM64 build ran the TU4 opening drive at 59.97 guest fps on an M4
Pro at 720p ([macos.md](macos.md), PR #169) [measured]. That validates the generated code, simde
on NEON and the FPCR handling on ARM64.

It does not validate the Linux or Android paths:
- macOS maps memory with MapViewsMac (`src/system/xmemory.cpp:428-471`).
- It gets the 0xE0000000 offset from a Mac-only branch.
- Its fault handler reads the Darwin mcontext (`src/core/exception_handler_posix.cpp:114-131`).
- It has no realtime signals (`src/core/threading_posix.cpp:119`).

So the Linux ARM64 memory-view, signal and thread-suspend paths have never run on real
hardware.

**Compile probes pass** [measured].
- aarch64-linux-gnu (clang 23, a Debian trixie arm64 sysroot, `-march=armv8.2-a -fsyntax-only`):
  0 errors on 12 SDK files, the generated `forza_recomp.0/.10/.89`, `forza-recomp/src/main.cpp`,
  and FFmpeg's `wmaprodec.c` and `random_seed.c`.
- NDK r29 (API 31): the generated code compiles against the current SDK source headers.
- The 0.10.0.24 nightly SDK headers fail at `rex/chrono/chrono.h:131,154` on
  `clock_time_conversion`. The source tree already has the guard that fixes this.

**The guest memory layout works on aarch64 Linux** under qemu-user with 4 KB pages [measured],
with a small mapping test:
- The views map at base 0x100000000.
- The A0000000 and E0000000 physical aliases read back correctly.
- A write-watch fault arrives as SIGSEGV with ESR 0x9200004f (WnR=1), and execution resumes.

**x86-only code is guarded** [code].
- `fpscr.h:53-85` uses `mrs`/`msr fpcr` on aarch64.
- The intrinsics, the MMIO decode (`mmio_handler.cpp:267, 438-521`) and the Linux sigcontext
  walk (`exception_handler_posix.cpp:133-160`) all have ARM64 branches.
- The only unguarded x86 code is `tests/guest_fma/check.cpp:63-77`.

**The memory model fits ARM64** [code].
- `lwsync` becomes an acq_rel fence; `sync` and `eieio` become seq_cst.
- `lwarx`/`stwcx` becomes a load plus `compare_exchange_strong(acq_rel)` inside the global lock
  (generated `forza_recomp.10.cpp:20525-20539`).

**No JIT is needed.** All guest code is compiled ahead of time; MAP_JIT is used only on macOS
(`memory_posix.cpp:339-343`) [code].

**FFmpeg** selects `config_android_aarch64.h` on Android (`config.h:19-25`) and compiles with the
NDK [measured].

**Upstream ReXGlue builds linux-arm64** green, at v0.10.1 (2026-11-06) and in its 2026-11-10
nightly [measured]. On our fork:
- `build-linux-aarch64.yaml` runs only on `v*` tags and has never run, and the fork's Nightly is
  disabled.
- Our `macos.yml` did build the patched fork for arm64. It was green through 2026-11-06, then
  failed 6 times in `fm4_track.cpp` (`REX_KERNEL_MEMORY` and `Load32` undeclared). That looks
  like an SDK version mismatch, not an ARM problem.
- Since #714 `macos.yml` runs only on manual dispatch, so ARM64 support is already going stale.

**Code size** [estimate]: one translation unit is 236 KB of aarch64 code against 321 KB on x86.
At that ratio the whole game is about 58 MB, under the 128 MB direct-branch range. This holds
only if that one unit is representative.

### Bugs and gaps

1. **The 16 KB/64 KB page bug** (real, read in code).
   - The runtime sizes the view granularity from `getpagesize()` (`xmemory.cpp:59-66`,
     `memory_posix.cpp:120-125`). With 16 KB pages it maps the 0xE0000000 view 4 KB short and
     gives that heap `host_address_offset=0x1000` (`xmemory.cpp:2026-2031`).
   - The compiled code adds 0x1000 back only when `REX_PLATFORM_MAC && ARM64`
     (`include/rex/system/xmemory.h:52-59`, `resources/templates/codegen/pch_h.inja:136-137`,
     `test/ppc_config_h.inja:81`, generated `forza_pch.h:128-132`).
   - Result: on any Linux or Android kernel with 16 or 64 KB pages, every guest access at
     0xE0000000 and above lands 4 KB off. That includes the x86_64 Android 16 KB emulator images,
     so a fix gated on ARM64 alone is not enough.
   - Android is moving to 16 KB pages; Google Play requires 16 KB support for updates of apps
     targeting Android 15+ from 2027-02-01
     (https://developer.android.com/guide/practices/page-sizes). We do not ship on Play, but the
     devices will have 16 KB kernels either way.

2. **Linux MapViews can replace an existing mapping.** It maps at `1<<n` with plain `MAP_FIXED`
   and reserves nothing first (`memory_posix.cpp:563-574`, `xmemory.cpp:274-286`). The comment
   there says otherwise. This is a silent overwrite risk, more likely inside an Android app
   process. `MAP_FIXED_NOREPLACE` is available in the NDK.

3. **The fibers use ucontext, which Bionic does not have.** `getcontext`, `makecontext` and
   `swapcontext` are missing (NDK probe errors at `fiber_posix.cpp:32,47,55,68`). Every XThread
   converts to a fiber (`xthread.cpp:651`), so this blocks Android outright.

4. **Robust mutexes.** `PTHREAD_MUTEX_ROBUST` and `pthread_mutex_consistent` are not in Bionic
   (`threading_posix.cpp:300,329,426`). `REX_PLATFORM_LINUX` is also 1 on Android
   (`platform.h:34-36`), so this code is compiled in.

5. **The leftover Xenia Android code does not build.**
   - `threading_posix.cpp:48` includes `rex/main_android.h`, which does not exist.
   - `rex::GetAndroidApiLevel` is called (`memory_posix.cpp:100`, `threading_posix.cpp:74`) but
     neither declared nor defined.
   - `InitializeAndroidSystemForApplicationContext` and `ShutdownAndroidSystem`
     (`system.h:23-24`), and `IsAndroidContentUri` and `OpenAndroidContentFileDescriptor`
     (`filesystem.h:132-133`), are declared but never defined.
   - The ASharedMemory path (`memory_posix.cpp:491-516`) is reusable. The Linux path uses
     `shm_open` (`:537`), which Bionic lacks.

6. **The SIGSEGV handler never forwards faults it does not claim.** It returns
   (`exception_handler_posix.cpp:262-420`), and `original_sigsegv_handler_` is used only on
   uninstall (`:478`). A real crash then re-executes the faulting instruction in a loop: it hangs
   instead of crashing, and on Android debuggerd writes no tombstone. ART chains SIGSEGV through
   libsigchain
   (https://android.googlesource.com/platform/art/+/323dfbf34f/runtime/fault_handler.cc), so our
   handler must pass on faults that are not ours.

7. **The SDL CMake** forces X11, Wayland, ALSA, PulseAudio and PipeWire on under
   `UNIX AND NOT APPLE` (`thirdparty/CMakeLists.txt:291-302`), which includes Android. SDL3
   itself supports Android (https://wiki.libsdl.org/SDL3/README-android).

8. **The game CMake** hardcodes the linux-amd64 SDK overlay paths
   (`forza-recomp/CMakeLists.txt:39-47`, this repository). The x86-64-v3 target and
   `guest_fma/check.cpp` are x86-only.

### Not verified yet

- The 182 PPC instruction tests and the FMA cases have never run on ARM64.
- Write-watch faults raised by exclusive or CAS instructions (LDAXR/STLXR, or LSE CASAL with
  armv8.2-a).
- The 132 `memory_order_relaxed` sites have not been audited.
- The `lwarx` emulation uses a plain non-volatile load (generated `forza_recomp.10.cpp:20532`),
  so not every guest access is volatile. This is probably harmless because a CAS follows, but it
  is unproven.

### CI

GitHub's `ubuntu-24.04-arm` runners are free for public repositories, and both repositories are
public
(https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/).
The changelog states 4 vCPU but gives no RAM figure. That is enough for the SDK and its tests. A
full game build there is untested.

## 4. GPU side

### Requirements

**Always required:** `independentBlend`, `fragmentStoresAndAtomics` and
`vertexPipelineStoresAndAtomics`. The checks are in `vulkan_device.cpp:112-155` and
`command_processor.cpp:1838-1850`. The `vulkan_require_*_stores_and_atomics` cvars say
"Deprecated and ignored" (`vulkan_device.cpp:41-48`).

**Required only while their cvar is on** (default on, except on macOS; `vulkan_device.cpp:51-58`):
- `geometryShader`. The fallback was validated on the M4 after patch 0080 ([macos.md](macos.md)).
- `fillModeNonSolid`. Polygon modes fall back to solid fill (`pipeline_cache.cpp:2332-2366`).

**FH1 uses no memexport** [measured]. We parsed 1,551 unique guest shaders (729 vertex, 822
pixel) from 55 shader stores; none has a kMemory alloc. The parser uses the layout in
`ucode.h:469-487` with big-endian decoding, which a second check confirmed. The translator marks
shared memory NonWritable when memexport is unused
(`src/graphics/pipeline/shader/spirv_translator.cpp:510-512`). So the store requirements are SDK
policy, not a need of the game. Caveat: the stores do not cover a full play-through.

**Not used:** float64, int64, float16 in shaders, subgroups, descriptor indexing, mesh shaders,
ray tracing and StorageImageWriteWithoutFormat [measured, spirv-dis over 113 modules].
- SampleRateShading needs `sampleRateShading`, which every phone GPU checked has.
- Limits: at most 4 descriptor sets, 128-byte push constants (not checked struct by struct), and
  `maxStorageBufferRange` of 128 MiB or more.

### Feature table

Source: gpuinfo.org reports, re-checked by hand: 52998 G715, 53119 G925, 53287 Adreno 740, 53295
Adreno 750, 53220 Adreno 830, 51383 Turnip a740, 53283 Xclipse 940, 52896 PanVK G610.
n/c = not checked. Only the Adreno column matters under decision 2; the rest is kept for
reference.

| Feature (what happens without it) | Adreno (Qualcomm driver) | Turnip | Mali (Arm driver) | Xclipse | PowerVR C/D | PanVK |
|---|---|---|---|---|---|---|
| `vertexPipelineStoresAndAtomics` (device rejected) | yes | yes | **no** (about 100% of devices) | yes | yes | **no** |
| `fragmentStoresAndAtomics` (device rejected) | yes | yes | yes | yes | yes | n/c |
| `geometryShader` (while its cvar is on; the fallback expands primitives in the vertex shader) | yes | yes | yes | yes | yes | **no** |
| `fillModeNonSolid` (while its cvar is on; solid fill) | yes | yes | **no** | yes | yes (BXM: no) | n/c |
| BC formats (decoded in compute at 2-8x the memory; texture packs fail with "this GPU cannot sample its format", `texture_cache.cpp:3969`) | yes (4% of 7xx lack it) | yes | **no** | BC1-3 only | no BC7 | yes |
| `VK_EXT_shader_stencil_export` (stencil copied in 8 draws, `render_target_cache.cpp:2736`) | yes | yes | **no** | yes | **no** | n/c |
| `VK_EXT_conditional_rendering` (the saving of SDK patch 0461 is lost) | depends on the driver (740 @512.676: no) | n/c | **no** (G720/G925) | n/c | n/c | n/c |
| `sparseResidencyBuffer` (shared memory is a full 512 MiB buffer; the scaled resolve buffer is 512 MiB x scale²) | flag set, untested | yes | **no** | n/c | n/c | yes |
| Clip and cull distance (user clip planes dropped, vertex kill lost; `pipeline_cache.cpp:1562-1592`) | yes | yes | **no** ([source](https://yuzu-mirror.github.io/entry/yuzu-progress-report-dec-2023)) | n/c | n/c | n/c |
| D24S8 (fallback D32S8) | yes | yes | yes | no | n/c | n/c |
| `maxMemoryAllocationSize` | 1 GiB | | 8 GiB | 2 GiB | | |

- 16-bit render targets and UINT transfers (else FSI or failure, `render_target_cache.cpp:312-442`):
  yes on every family checked.
- Fragment shader interlock: not needed, since host render targets are the default and are
  required for native resolution (`command_processor.cpp:1882-1887`). It appears only on new
  Qualcomm drivers (830 @512.891, 725 @512.863).

Android-wide coverage (https://vulkan.gpuinfo.org/listfeaturescore10.php?platform=android):
`vertexPipelineStoresAndAtomics` 55.6%, `fillModeNonSolid` 54.1%, `geometryShader` 94.0%,
`textureCompressionBC` 37.0%.

### What it means

**Adreno 7xx/8xx with the Qualcomm driver is the target** (decision 2). Open point: the 1 GiB
`maxMemoryAllocationSize`. If sparse binding is missing or broken, the scale-2 resolve buffer
(2 GiB) cannot be allocated, and native resolution above 720p needs scale 2 or more.

**Turnip is not the system driver on any shipping device.** Using it means loading a custom
driver (libadrenotools style).
- On Adreno 8xx, upstream Turnip runs in sysmem mode only, with no tile binning
  (https://www.phoronix.com/news/Mesa-26.0-Adreno-Gen-8-Graphics,
  https://newreleases.io/project/github/The412Banner/Banners-Turnip/release/v26.2.0-20260623-r9).
- The Dragon Age: Origins Android port measured Turnip about 16 ms a frame slower and dropped it
  (https://github.com/mabencs/Dragon-Age-Origins-Recomp-Android-Duo).
- So Turnip is a fallback, not the plan.

**The Android surface code is missing.**
- `vulkan_presenter.cpp:41-43` includes `rex/ui/surface_android.h`, and `:952-955` uses
  `AndroidNativeWindowSurface`. Neither exists; `include/rex/ui` has only the gnulinux, mac and
  win surfaces.
- `WindowSDL::CreateSurfaceImpl` (`window_sdl.cpp:442-490`) handles only Win32, Metal, Wayland
  and XCB.
- This is an S work item.

### If Mali ever becomes a target

Not the chosen path (decision 2). Kept so the work is not redone. Mali would need, in order:
1. A small SDK patch: honor the store cvars and turn the per-draw memexport check into
   skip-and-warn. First check the "VS converted to compute" stage flag
   (`command_processor.cpp:1679-1683`).
2. The fill-mode cvar turned off.
3. Then it runs on fallbacks:
   - BC decoded in compute. The texture limit is not raised for decoded textures (TODO at
     `texture_cache.cpp:2835`), so expect eviction at 384 MB.
   - Stencil copied in 8 draws.
   - No sparse binding.
   - No clip planes. Whether FH1 uses them is unknown.
   - No texture packs.

Arm ships Android Mali with BCn disabled
(https://community.arm.com/support-forums/f/mobile-graphics-and-gaming-forum/54575/support-for-bcn-texture-formats-on-future-mali-gpus).
The Sonic Unleashed Android port transcodes BC to ETC2 on the CPU
(https://github.com/SansNope/UnleashedRecomp-Android); that is the existing recipe.

**Xclipse and PowerVR C/D** pass the hard checks. Their gaps all have fallbacks, but nothing has
been tested, and they are out of scope too.

## 5. Performance budget

### Our numbers [measured]

**Ryzen 7 7800X3D CPU**
- All threads: 15.9-19.2 ms a frame at 60 fps; 19.3-20.9 ms on the Deck preset at 30 fps.
- Per thread: command processor (CP) 3.9-6.3 ms, render thread 2.7-3.7 ms, Vulkan submission
  1.0-1.55 ms.
- The audio mixer is a fixed 110-127 ms of CPU a second ([perf.md](perf.md)).

**CP cost per draw, unsplit** ([perf.md](perf.md))
- 7800X3D: 1.2-1.3 µs.
- Recent CPUs in older logs: 1.5-1.6 µs.
- Zen 3: 2.2-2.9 µs.
- 4-core Zen 1/2: 3.6-3.8 µs.
- i5-10300H: 3.6-4.4 µs.
- BC-250: 4.5 µs (#174).
- Ryzen 7730U: 6.1 µs.

The split CP costs more total CPU: 8.67 ms a frame against 6.00 unsplit, +45%.

The 4-5x spread in per-draw cost mixes code versions, metrics and power states, so it does not
support a CPU scaling law. The 7730U was at a 30 fps cap with the CP 38% busy.

**GPU**
- RX 9060 XT at 720p with the game's MSAA: 5.7 ms uncapped, 8.0 ms at 30 fps clocks. With
  `scene_msaa` off: 4.9 and 6.9 ms.
- GTX 1650 Ti at the 540p scene: 17.7 ms. That is draws 7.8, render-target transfers 5.3 (41 a
  frame), texture loads 2.3, resolves 1.7 and uploads 0.5 ms. SDK patch 0461 brings it to
  16.0 ms. At render scale 2: 52.1 down to 41.8 ms.

**Steam Deck (0.1.30)**
- 19.7 fps in the benchmark, 1% low 10.9.
- Free roam drops to 13 fps (#559).
- GPU 50-74% busy at 700-950 MHz.
- No Deck data since 0.1.30.

**M4 Pro**
- 59.3-66.5 fps at 720p, 26.7-30.2 fps at 1440p.
- October code, with other load on the machine. The GPU core count and the render-target path
  are not recorded.

**Memory (Linux)**
- RSS 1,035 MB (PSS 781).
- VRAM 1.1 GB: render targets 494-540 MB, textures 186-211 MB growing to the 384 MB limit.
- GTT 59-79 MB.

### Phones and handhelds [source]

**CPU (Geekbench 6 single-core)**
- Snapdragon 8 Elite: 3,033-3,242 (https://beebom.com/snapdragon-8-elite-benchmarks).
- Dimensity 9400: 2,618-3,010
  (https://beebom.com/snapdragon-8-elite-vs-dimensity-9400-benchmark-comparison/amp/).
- Steam Deck OLED: 1,348
  (https://www.notebookcheck.net/AMD-Steam-Deck-OLED-APU-Processor-Benchmarks-and-Specs.799065.0.html).
- 8 Elite Gen 5 and Dimensity 9500: 3,471-4,007, partly from reference designs.

**GPU**
- Adreno 830: about 3.4 TFLOPS, 77-85 GB/s shared with the CPU, Steel Nomad Light median 2,404
  (https://www.notebookcheck.net/Qualcomm-Adreno-830-Benchmarks-and-Specs.908507.0.html).
- Adreno 840: about +21% over that, measured on a reference design (https://heise.de/-10669926).
- Steam Deck: 88 GB/s. We found no Deck Steel Nomad Light score, so every "x a Deck" ratio rests
  on a derived score of about 1,500-1,600. On that basis the 830 is about 1.5x a Deck and the 840
  about 1.8x [estimate].
- M4 Pro: 273 GB/s (https://www.macrumors.com/2024/10/29/apple-announces-m4-pro-chip).

**Throttling** (sustained share of peak in the Wild Life Extreme stress loop, under full load; a
game capped at 30 fps throttles differently)
- 8 Elite in a well-cooled phone: 83%.
- Galaxy S25 Ultra: about 48-59%
  (https://benchmarks.ul.com/hardware/phone/Samsung+Galaxy+S25+Ultra+review,
  https://www.notebookcheck.net/Google-Pixel-10-Pro-XL-and-Tensor-G5-deliver-class-leading-stability-in-GPU-test.1097994.0.html).
- Dimensity 9400: 39.5-58%.

Handhelds and gaming phones with a fan (decision 4) should sit at the top of this range, but no
sustained figure for one was collected [estimate].

### Estimate for today's code [estimate, wide error bars]

**CPU.** The Deck preset issues 2,700-3,400 draws a frame. At a hand-picked 1.6-2.4 µs a draw,
with the unexplained 2x "Deck gap" on top, the CP takes 5.6-13 ms a frame. That should be fine at
30 fps on a 2025-26 flagship and marginal on 2023-24 chips. Mobile drivers' per-draw CPU cost is
not modelled.

**GPU.** Three estimates, which do not agree:
1. Scaled from the Deck (x1.5 peak, with a guessed tile penalty): about 16 ms at 720p.
2. Scaled from the M4 Pro: about 32 ms.
3. Scaled by bandwidth from the 1650 Ti profile: transfers, resolves and texture loads are 9.3 of
   its 17.7 ms. At 192 GB/s against 77-85 GB/s, that part alone becomes 20-24 ms, for a 540p
   frame of about 28-32 ms peak.

Estimates 2 and 3 are the more physical ones. The tile penalty is not captured by any of them:
- about 41 transfers, about 100 resolves and the uploads in each frame each end a render pass;
- geometry-shader pipelines (`pipeline_cache.cpp:2242-2303`) are slow on tile-based GPUs.

**fps on an 8 Elite-class device** (`scene_msaa` off, FSR 1)
- 540p: about 25-35 fps peak, less sustained.
- 720p: about 20-30 fps.
- 8 Elite Gen 5: about 20% more.
- 8 Gen 2 (AYN Thor and Odin 2 class) or Tensor G4: under 20.

A 30 fps cap at 540p is plausible only on a well-cooled 2025-26 flagship, and 60 fps is not
plausible.

**Memory.** About 2.0-2.5 GB of unified memory, 2.3-2.8 GB late in a session. This assumes BC
stays compressed (true on Adreno) and the driver adds 0.1-0.3 GB; the driver share is not
checked. It fits 12-16 GB devices, is tight on 8 GB (Quest 3), and does not fit 6 GB. Android has
no hard per-app limit; lmkd kills apps under memory pressure
(https://source.android.com/docs/core/perf/lmkd).

### Perf work that matters, in order

1. **Transfers and resolves on tile-based GPUs.**
   - Count render passes with `fh1_gpu_profile` on Adreno.
   - Use DONT_CARE or `VK_EXT_load_store_op_none` for EDRAM contents that are dead.
   - Evaluate framebuffer fetch.
   - Evaluate the existing FSI path (`render_target_path_vulkan=fsi`,
     `render_target_cache.cpp:61, 224-233`), which removes ownership transfers. Only the newest
     Qualcomm drivers expose the interlock it needs.
   - Size render targets to the rows the game draws (estimate: 540 down to 250 MB).
2. **An Android preset:** 540p, `scene_msaa` off, 30 fps, `texture_cache_memory_limit_soft=192`,
   render scale 1.
3. **Power.** CPU time saved is GPU clock kept.
   - Cut the remaining wakeups: render_idle about 9,800 a second, GPU VSync about 950, XMA about
     1,900 ([perf.md](perf.md)).
   - The split CP turns on at 6+ physical cores (`forza-recomp/src/main.cpp`) and costs +45% CP
     CPU. Measure it both ways at a 30 fps cap with SoC power before defaulting it on.
4. **CP per draw.**
   - Descriptor reuse (SDK patches 0442-0445) and #768.
   - Make the `thread_cores.cpp` heuristics count big cores via `cpu_capacity`.
   - ADPF hints.

## 6. App shell, distribution, testing

### Where the game compiles

The game is never shipped prebuilt; the launcher builds it on the player's machine
([launcher.md](launcher.md)). There were three options:

| | A. Build on the phone | B. Build on the player's PC (**chosen**) | C. Ship prebuilt game code |
|---|---|---|---|
| Rule "no prebuilt game" | Kept: code made on the player's device | Kept: code made on the player's own PC | Broken. Rejected. |
| Needs a PC | No | Yes; the PC launcher gains an Android target | No |
| APK holds | Shell, SDK runtime, ReIGlue codegen, clang/lld | Everything, built on the player's PC | Everything |
| Work | Biggest: an Android toolchain and a build service | Medium: NDK cross-build and APK packaging in the launcher | n/a |

**B, the chosen path.** The PC launcher already generates and compiles the game. For Android it
also:
- downloads the Android NDK (r29+, for LLVM 21+; see below) and cross-compiles the generated
  code and the runtime for arm64-v8a;
- packages the game library with the shell (the Kotlin launcher activity, SDL's Java glue and
  resources) into an APK;
- signs the APK (see [Signing](#signing));
- installs it over USB or wireless debugging (adb), or saves the APK file for the player to copy
  and open on the device;
- copies the game files to the device (see [Game files](#game-files)).

Build time on the PC should be close to today's build (about 4.6 minutes at 4 jobs on an 8-core
PC, about 2 GB RAM), since the work is the same compile for a different target [estimate]. The
APK holds a game library of about 100-200 MB [estimate: the x86-64 Linux binary is 157-164 MB
with debug info]. Because the library is installed in the APK's native library folder, nothing
is loaded from writable storage.

Work items for B:
- An SDK ABI check so the launcher rebuilds only when the generated code or the SDK changes, and
  a per-file object cache, as on the PC.
- The same signing key on every rebuild, or Android refuses the update and the player has to
  uninstall, which deletes the app's data. Saves should live where an uninstall cannot reach
  them, or be backed up by the PC launcher.
- The NDK download size and its licence terms for redistribution through the launcher are not
  checked.

**A, build on the phone (not the chosen path).** Kept for reference. Prior art is the Dragon
Age: Origins port (https://github.com/mabencs/Dragon-Age-Origins-Recomp-Android-Duo):
- It packages clang+lld (LLVM 19.1.7) and its recompiler as `libllvm.so` and `libxenonrecomp.so`
  and runs them with ProcessBuilder.
- It builds in a foreground service in its own `:gamecode` process with 6 jobs.
- The build takes under 9 minutes on a Snapdragon 8 Gen 2 (AYN Thor).
- Its APK is 75.8 MB and holds no game code.
- It targets SDK 33. That avoids Android 14's rule that loaded code must be read-only and its
  requirement that foreground services declare a type
  (https://developer.android.com/about/versions/14/behavior-changes-14).

The same port also has the B loop for developers: build on the PC with the NDK and push the
library to the device. Problems the on-device recipe would have had for us:
- **Compiler version.** We need clang 21+ for C++23. Dragon Age uses LLVM 19, and the vendored
  SDL Android project pins NDK 28.2. NDK r29 uses clang-r563880c
  (https://newreleases.io/project/github/android/ndk/release/r29); that this is LLVM 21 is our
  estimate. This also applies to B, on the PC.
- **Build time and RAM on a phone are unknown.** The 308 MB of generated C++ (303 .cpp files plus
  125 project files) is big; our estimate for an 8 Gen 2/3 device was 15-30 minutes with
  throttling.
- **Peak storage of about 20 GB, transient.** ISO 8.74 GB, extracted game 7.2 GB, build tree and
  SDK about 2.6 GB, the toolchain, DLC 1.9 GB and texture packs 1.5 GB [measured; the total is an
  estimate].
- **Loading a library written at run time.** At targetSdk 34+ the file must be made read-only
  before loading; nobody had tested that.

**C, prebuilt game code in an APK** (the Sonic Unleashed port's model) is ruled out.

### Shell

**Launcher on the device.** A small Kotlin/Compose activity for settings and status, plus SDL3's
SDLActivity running the game in its own process.
- SDL3 3.4.14 is vendored with its Android Java glue (compileSdk and targetSdk 35, NDK 28.2,
  arm64-v8a).
- `getMainSharedObject()` and `loadLibraries()` can be overridden (`SDLActivity.java:268-310`);
  under B the game library is a normal packaged library, so the defaults may do.

**Modules.** The facade modules and the Xenos plugin are loaded with `dlopen` by bare name
(`CMakeLists.txt:360-389`). On Android they need to be packaged libraries, absolute paths, or
statically linked.

**Setup tools.** Setup is five pure-Python tools of about 1,841 lines (`xiso_extract`,
`xcontent_extract`, `apply_title_update`, `lzx_delta`, `install_dlc`), plus the launcher's
installer logic. Under B they keep running on the PC, unchanged. Porting them to the device
(Kotlin, or Chaquopy, https://github.com/chaquo/chaquopy) is needed only for a later PC-free
mode; AES in `apply_title_update.py` would then need the cryptography wheel, since Android has
no openssl binary.

### Game files

The game needs POSIX paths (`runtime.cpp:31,46-52`), so the files live in app storage, not
behind the Storage Access Framework. That is 2,446 files; the biggest is 2.94 GB, under the FAT32
limit [measured]. The extracted game is 7.2 GB, plus 1.9 GB for DLC and any texture packs.

Under B the PC already has the extracted game, so the launcher copies it to the device's
app-specific storage over adb, or the device imports a copied folder. Which of these works on
current Android versions (Android 11+ restricts other apps' access to `Android/data`) is not
checked.

### Input, audio, lifecycle, display

**Input.**
- SDL gamepads (the built-in controls of handhelds appear as a gamepad), plus a touch overlay
  feeding an SDL virtual joystick for phones without a controller.
- DualSense trigger effects move into the game (`SDL_SendGamepadEffect`).
- SDL's HIDAPI covers USB, and Bluetooth only for the Steam Controller.
- Discord, Steam, VR and wheels are out of scope for v1.

**Audio.** AAudio through SDL.

**Lifecycle.**
- When the app is paused, SDL blocks only its own event loop, and the ANativeWindow is destroyed
  (SDL's `README-android.md`).
- The shell must map the background event to the existing auto-pause (`auto_pause_policy.h`),
  `GraphicsSystem::Pause` and `AudioSystem::Pause`, and a presenter disconnect. On the way back it
  reconnects through the #221 path.
- The cached-apps freezer uses the cgroup v2 freezer, not SIGSTOP. It exists from Android 11,
  and on 14+ it freezes an app about 10 s after it is cached
  (https://source.android.com/docs/core/perf/cached-apps-freezer).
- For timing, the freezer looks like the SIGSTOP case `resume.cpp` already handles
  ([online_vision.md](online_vision.md)). Verify with `am freeze`.
- Copy the Dragon Age port's "autosave when put away" option.

**Display.** From Android 15, the game draws under the notch; `SDL_GetWindowSafeArea()` gives the
usable area.

### Distribution

Under B there is no game APK to publish. What the project ships is the PC launcher with its
Android target, through the usual GitHub Releases with `SHA256SUMS`. The APK is made by each
player.

**Google Play: no.** The APK holds code built from the player's own copy, a self-updater is not
allowed there, and the Forza trademark is a risk. aX360e is on Play
(https://www.androidauthority.com/ax360e-xbox-360-emulator-3625517/), for reference.

**F-Droid: no.** It requires FLOSS code built on its own servers
(https://f-droid.org/docs/Inclusion_Policy), which cannot include the game.

### Signing

SignPath's `create-apk-signature` supports v1-v3 embedded and v4 detached APK signatures on the
Open Source plan (https://docs.signpath.io/artifact-configuration/reference). Decision 3 is to
use SignPath if it covers Android. Under decision 1 the split is:
- **What SignPath can sign:** what the project builds in CI and ships: the PC launcher (already
  planned), and any prebuilt APK the project publishes, such as a capability probe or a shell
  without game code.
- **What it cannot sign:** the player's APK. It holds game code built on the player's PC, and
  sending it to a signing service would upload that code to a third party, which the project
  does not do. The launcher would create a signing key on the player's PC, keep it with the
  player's settings, and sign every rebuild with it (decided 2026-11-12).

To ask SignPath: whether the Open Source plan covers an Android key, and who holds it. A key in
their HSM cannot be lost, but the identity of anything signed with it then depends on SignPath.

**Developer verification.** Google enforces it since 2026-09-30 in Brazil, Indonesia, Singapore
and Thailand, and worldwide in 2027. Hobbyists may share with up to 20 devices with no fee
(https://developer.android.com/developer-verification). Whether it applies to an APK a player
builds and installs on their own device with their own key, over adb or as a file, is not
checked, and decides how the install step works.

### Testing

**A desktop Linux PC** (RX 9060 XT, 8 threads, 30 GB RAM, KVM).
- An x86_64 Android build on the emulator or Waydroid can test the shell, the app lifecycle, file
  copy and the CPU and runtime paths.
- The 16 KB x86_64 emulator image would catch the page bug
  (https://developer.android.com/guide/practices/page-sizes).
- Neither has been run yet.
- Both expose the PC's GPU features, BC included, so emulator rendering results say nothing
  about phones.

**ARM64 images on x86** have no acceleration
(https://developer.android.com/studio/run/emulator-acceleration).

**Quest 3.** A real ARM64 device with an Adreno 740, the same GPU as the AYN Thor (the Dragon Age
port's test device).
- It has 8 GB RAM.
- It is untested whether a Vulkan game runs in a Horizon OS panel.
- It works for bring-up, but its cooling is not a handheld's.
- A Snapdragon 8 Elite-class handheld or gaming phone is still needed for the performance
  verdict.

**16 KB pages on hardware.** Pixel phones have the developer option "Boot with 16KB page size"
(a Tensor/Mali phone, so only for the CPU and memory paths), or Asahi Linux on Apple Silicon.

**Device farms.** Only for a Vulkan capability probe APK that contains no game: running the game
there would upload compiled game code and 7 GB of game data to a third party. Firebase Game Loop
has a 3-minute default timeout (https://firebase.google.com/docs/test-lab/android/game-loop).
AWS Device Farm allows 4 GB uploads and 150-minute sessions
(https://docs.aws.amazon.com/devicefarm/latest/developerguide/limits.html).

**Test tools.** `render_test`, `live_shot` and `autodrive` use an input FIFO and `/proc/pid/mem`
(`tools/render_test.py`). On Android they need:
- intent extras for settings;
- `run-as` for files;
- a debug-only localhost socket reached through `adb forward`.

## 7. Path

Sizes are estimates.

0. **Decisions (XS). Done 2026-11-12**, see [section 2](#2-decisions-2026-11-12): build on the
   player's PC; Snapdragon/Adreno only; SignPath if it covers Android; powerful phones and
   Android handhelds.

1. **aarch64 Linux in CI (1.5-2.5 weeks).**
   - Fix the 16 KB/64 KB page bug, gated on the target and not only the CPU arch:
     - change `xmemory.h:52`, `pch_h.inja:136`, `ppc_config_h.inja:81` and
       `ViewOffsetGranularity`;
     - regenerate the code;
     - add a unit test that forces 16 KB.
   - Use `MAP_FIXED_NOREPLACE` in MapViews.
   - Add a linux-arm64 platform to the game CMake.
   - Run the ReIGlue build-linux-aarch64 job on PRs, with tests and powerpc binutils.
   - Fix whatever the PPC and FMA tests turn up on NEON.
   - Exit: the PR job is green on `ubuntu-24.04-arm` with ctest, all 182 PPC tests and the FMA
     cases; host sources and sample generated files compile for aarch64; the 16 KB test passes.

2. **Android NDK build (2-3 weeks).**
   - Replace the ucontext fibers.
   - Turn off robust mutexes on Android.
   - Remove the dead main_android/GetAndroidApiLevel glue and replace `shm_open`.
   - Add `surface_android.h/.cpp` and an Android branch in WindowSDL.
   - Chain the SIGSEGV handler through libsigchain.
   - Add `NOT ANDROID` to the SDL CMake.
   - Package the modules as libraries or link them statically; build forza as a shared library.
   - Use NDK r29+ / LLVM 21+.
   - Exit: `libforza.so` links for arm64-v8a and x86_64-android.

3. **Emulator bring-up (M, 1-2 weeks).**
   - A minimal SDLActivity APK, built and installed from the PC, as players will do.
   - Map the lifecycle events.
   - Add a debug socket.
   - Exit: on the x86_64 emulator or Waydroid, including the 16 KB image, it boots to the menu and
     drives the opening scene, and survives Home, screen off, rotation and `am freeze`/unfreeze.
     GPU results do not count.

4. **First real Snapdragon device (M, 1-2 weeks). This is the go/no-go point.**
   - Run on the Quest 3 (Adreno 740), then on a Snapdragon 8 Elite-class handheld or gaming phone.
   - Run a Vulkan probe APK.
   - Test 16 KB pages on a Pixel with the 16 KB toggle (CPU and memory paths only).
   - Exit: the opening drive and 10+ minutes of free roam render correctly. Collect
     `fh1_gpu_profile`, the frame cost CSV, per-thread CPU, temperatures and clocks, and
     render-pass counts.
   - Then decide whether to continue or stop. Stop or shelve if sustained free roam is well under
     20 fps at 540p.

5. **Tile-GPU and power perf pass (L, 4-8 weeks).**
   - Render-pass churn and load/store ops.
   - The FSI path where the driver exposes it.
   - Render targets sized to the rows the game draws.
   - An Android preset: 540p, `scene_msaa` off, 30 fps, 192 MB texture limit.
   - Measure the split command processor both ways under the 30 fps cap with SoC power.
   - Cut wakeups; place threads on big cores; ADPF hints.
   - Exit: the playable bar (step 8) holds on the reference device.

6. **Other GPUs: not planned** (decision 2). The app refuses non-Adreno GPUs with a clear message.
   The Mali work, if it is ever wanted, is in
   [section 4](#if-mali-ever-becomes-a-target) (M-L): honor the ignored store-feature cvars,
   transcode BC to ETC2 or ASTC, emulate clip planes if FH1 uses them, and make texture packs work
   without BC.

7. **Product: the PC launcher's Android target and the device shell (L, about 4-7 weeks).**
   - PC launcher: NDK download, cross-build for arm64-v8a with an incremental object cache, APK
     packaging, a per-player signing key kept on the PC, install over USB or wireless debugging
     or as a file, copying the game files, TU4 and DLC.
   - Device shell: settings, space checks, crash reports, touch overlay, and a message for
     unsupported GPUs.
   - SignPath for what the project ships; a CI check that no published artifact holds game files.
   - Exit: a clean device and a PC with the launcher go from ISO to playing; after a game or
     launcher update, the PC rebuilds incrementally and reinstalls over the old APK with saves
     kept.

8. **Playable bar before any announcement.**
   - On the reference device, a 30 fps cap holds in free roam at the Android preset for 20+
     minutes sustained, with 1% lows reported.
   - No crashes or lost GPU device over several sessions.
   - The lifecycle survives backgrounding.
   - The PC build and the install complete in a time we report.
   - Only then a public test build or announcement.

Rough total to a public test build: about 4-5 months of one developer, about half of it in steps
5 and 7 [estimate; it was 4-6 months with the on-device compile].

## 8. Open questions

1. Real device fps. The three GPU estimates are about 2x apart. The Deck Steel Nomad Light score,
   the tile penalty and the "Deck gap" on big.LITTLE are unsourced.
2. The CPU cost per draw in mobile drivers, at 3,000-4,200 draws a frame.
3. Whether Qualcomm's sparse binding works. Scale 2 depends on it, under the 1 GiB allocation
   limit.
4. Whether FH1 uses user clip planes (`ucp_ena`). Matters only for non-Adreno GPUs.
5. Whether there is memexport in parts of the game our shader stores do not cover.
6. The size of the game library and of the APK (estimated 100-200 MB), and the PC build time for
   arm64-v8a.
7. How the game files get to the device: adb into app-specific storage, or an import from a
   copied folder, on Android 11 to 16.
8. The relaxed-atomics audit, and write-watch faults from exclusive or CAS instructions on ARM64
   Linux.
9. The 58 MB aarch64 code size, extrapolated from one translation unit.
10. Device memory with the driver's own use; the OS's share of RAM is unsourced.
11. Throttling at a 30 fps cap, as opposed to stress loops, on a handheld with a fan.
12. Quest 3: a Vulkan game in a panel, and whether 8 GB holds the game.
13. Whether Waydroid or gfxstream runs a Vulkan build on a desktop AMD GPU.
14. Whether SignPath covers an Android key, and who holds it.
15. Whether Android developer verification applies to an APK the player builds and installs on
    their own device.
16. The NDK's download size and whether the launcher may fetch it for the player.
17. gpuinfo data that was not saved: Mali 53055/52703/52767, Xclipse 49029/53128/52499, the
    Adreno 725 interlock, and the per-family percentages. Re-run them (Adreno first).
18. The M4 Pro anchor: which render-target path it used, and its GPU core count.
19. Whether a full game build fits a 4 vCPU hosted ARM64 runner, or needs a self-hosted machine.
