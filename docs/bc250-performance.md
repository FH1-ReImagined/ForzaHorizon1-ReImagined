# BC-250: guarded Linux guest FMA investigation

## Scope and evidence

Current main includes #233's guarded `FORZA_CPU_TARGET=x86-64-v3` path, which the
launcher selects on supported CPUs. This PR retains the narrower Linux-only
`FORZA_GUEST_FMA` option for manual comparisons with `FORZA_CPU_TARGET=baseline`;
it is not the launcher's CPU-target implementation. BC-250 runtime validation is
still pending for both paths.

The reported BC-250 configuration uses CachyOS, Mesa 26.2.4, 40 unlocked GPU
CUs and six CPU cores. Reports for ReImagined 0.1.17 and 0.1.23 describe roughly
30–40 fps even at internal scales 1 and 2. We have no BC-250 trace or local
BC-250 hardware. Aggregate CPU/GPU utilization below 100% does not exclude a
serial CPU bottleneck or synchronization wait. The unusually large reported
log warrants checking logging overhead before attributing the issue to guest math.

The ordinary resolution setting selects the guest video/window mode;
`resolution_scale` controls scene raster size. Record actual raster/output
dimensions, frame cap, GPU busy time and per-thread CPU cost before selecting
the next optimization. This option is an experiment, not a verified BC-250 fix.

## Experiment: hardware FMA in generated code

`FORZA_GUEST_FMA=ON` adds `-mfma -ffp-contract=off` to the generated main
module and generated facade targets, including their precompiled headers.
The host/bootstrap and SDK retain their normal baseline. This is initially
limited to Linux x86-64 with Clang/GCC and defaults **off**.

- Explicit `std::fma`/`std::fmaf` can lower to hardware instructions.
- Separate multiply/add expressions must stay separate; no fast-math or
  global CPU-native tuning is added.
- An ELF preinitializer in the baseline host checks both AVX and FMA
  through compiler CPU detection, including OS AVX state support, and the
  SSE4.2/POPCNT features also permitted by Clang's `-mfma` flag. It runs
  before SDK shared-library and generated static initializers, prints a rebuild instruction
  and exits on an incompatible machine.
- This is a build-time choice, not a `forza.toml` graphics setting. Do not
  distribute an FMA build as the portable release or enable it by default
  until game and affected-hardware validation are complete.

After configuring a local game build with the normal SDK and title-update
options, change only this option for an A/B/A comparison:

```sh
cmake -S forza-recomp -B /path/to/game-build -DFORZA_GUEST_FMA=OFF
cmake --build /path/to/game-build --parallel 4
# Measure baseline; retain its logs outside the source tree.
cmake -S forza-recomp -B /path/to/game-build -DFORZA_GUEST_FMA=ON
cmake --build /path/to/game-build --parallel 4
# Measure FMA; then repeat OFF to check run-to-run drift.
```

Use the same generated code, patched SDK, title update, copied save, route,
internal/output resolution, frame cap, AA, upscaler and warm shader caches.
Record average fps, frame-time p95/p99, per-thread CPU time, CPU/GPU clocks,
temperature and GPU busy time. Take at least three runs per variant and
record contention. Do not use an active personal save or public server.
`RECOMP_PERF=1`, `FORZA_FRAMETIME_LOG=1`, and the existing
`tools/perf_scenario.sh` / `tools/cpuprof_report.py` can supply frame and CPU
evidence. Follow [the profiling guide](perf.md) for their prerequisites.

### Reproducible arithmetic check (no game data)

```sh
cmake -S forza-recomp/tests/guest_fma -B /tmp/forza-fma-check -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=clang++ -DFORZA_GUEST_FMA=ON
cmake --build /tmp/forza-fma-check
ctest --test-dir /tmp/forza-fma-check -V
```

Repeat in a separate directory with the option OFF. The test compares explicit
fused operations and separate multiply/add for float/double across four rounding
modes, all four FTZ/DAZ combinations, special values and seeded random bit
patterns (4,334,912 comparisons). Non-NaNs must match bit for bit, including
signed zero; NaN payload selection and floating-point exception flags are not
verified. A default-priority initializer also exercises guest math before main,
after the early CPU guard.

On an i9-13900K under Fedora WSL, Clang 22.1.8, both variants passed; the
optimized check also passed with GCC 16.2.1. Assembly
inspection shows FMA instructions in the optimized explicit operations, separate
multiply/add instructions in the nonfused operations, and no AVX/FMA instructions
in the guard. Fault injection with gdb (clear compiler CPU feature bits at guard
entry) exits with status 1 before the generated-code initializer is called.
This does not replace an actual unsupported-CPU/OS test.

After the game builds finished, seven alternating-order runs of 10 million
iterations gave a small serial difference (8.29 ms baseline, 7.77 ms FMA).
Four independent accumulators fell from 57.23 to 7.51 ms. These synthetic loops demonstrate that
call/ABI overhead can matter when work overlaps; they predict neither the
audio thread's improvement nor game fps. BC-250 runtime validation is pending.

### Game build and packaging checks

- Both disc (`FORZA_TITLE_UPDATE=0`) and TU4 (`4`) compile and link with the
  option ON, using SDK `0.10.0.24-dev.gbd833a2` and Clang 22.1.8.
- The full TU4 executable also rejects injected missing CPU features in its
  early guard. The guard's object contains no AVX/FMA instructions.
- Unsupported-platform configure is rejected; the portable arithmetic check
  passes with the option OFF.
- Launcher/Flatpak source enumeration includes the new CMake/host files and
  excludes private generated code.

Game boot, driving, graphics and audio have not been validated for this build.
An attempted `--help` smoke check aborts in SDK CLI/path setup in both an
existing baseline binary and this executable; it is not counted as a boot pass.

### Main integration validation, 2026-10-22

Merged main through `211e862`, retaining its Discord host source and CPU-target module.
The follow-up merge after #286 preserves main's corrected swap-wait notes;
the FMA/CPU-target code is unchanged from `6836e19`. All eight arithmetic
combinations, the V3 feature test and four missing-feature startup checks passed again.
The guest-FMA guard now uses `.preinit_array` too: an ordinary constructor can
run after SDK shared-library initializers that call guest-exported inline code.
Unsupported hosts use `_Exit` so rejection cannot enter teardown callbacks.

The arithmetic fixture now includes both production CMake modules and guards.
Clang 22.1.8 and GCC 16.2.1 on Fedora WSL pass all four combinations of
`FORZA_CPU_TARGET=baseline|x86-64-v3` and `FORZA_GUEST_FMA=OFF|ON` (eight
builds, 4,334,912 comparisons each). Configure each combination in a separate
build directory using the arithmetic-check command above, adding
`-DFORZA_CPU_TARGET=<target>` and selecting `clang++` or `g++`.
Compile-command checks confirm guest-only optimization flags and baseline host
and reference flags. Disassembly contains no AVX/FMA instructions in the guard;
enabled executables contain the preinitialization section.

GDB fault injection after `__builtin_cpu_init` individually clears the detected
AVX, FMA, SSE4.2 or POPCNT feature. Each run prints the rebuild instruction and
exits with status 1 before either guest math or an `LD_PRELOAD` test library's
constructor executes. The supported-host control reaches that constructor and
passes arithmetic. This simulates missing features; it is not a test of an
actual unsupported CPU/OS. The production V3 feature checker also passes its
synthetic missing-CPUID/XCR0 tests.

Production-module configure probes confirm default/OFF behavior, both facade
targets' flags, unchanged host flags, and rejection of Windows, Darwin, ARM,
unsupported compilers and a missing generated-code target when FMA is ON.
Focused Linux launcher CPU-target, hardware-spec and build-tree tests pass
(44 tests: 43 passed, one Windows-only test skipped).

No full game build, game/GPU benchmark, BC-250 execution, driving or audio
validation was performed in this integration pass. Earlier game-build and
synthetic timing evidence above is historical and is not an FPS prediction.

## Next experiments, in order

1. Obtain the updated BC-250 baseline and a per-thread profile. Establish
   actual raster dimensions and whether the limiting work is simulation,
   command processing, audio, GPU execution or a synchronization wait.
2. Compare the FMA build on that device; listen for audio problems, test driving
   and menus on disc and TU4, and check frame times rather than only averages.
3. If command processing dominates, prioritize descriptor/binding work and
   the existing native-renderer effort. If guest CPU work dominates, evaluate
   register/stack/call optimizations with differential correctness checks.
4. Test code-model changes and topology-aware thread placement independently.
   Keep experiments reversible and reject changes whose improvement is within
   repeated-run noise.

## Current-main review preparation (26 October)

Integrated main `b560528` without changing the FMA implementation or CPU-target
selection. Fresh Fedora WSL/GCC 16.2.1 configure, compile, link and arithmetic
tests pass for all four baseline/v3 and FMA OFF/ON combinations. The existing
Clang FMA-enabled fixture also rebuilds and passes. Each arithmetic test covers
4,334,912 cases with all four rounding modes and FTZ/DAZ combinations; NaN
payloads and floating-point exception flags remain outside this comparison.

A Linux CI job now builds and runs the same four combinations using the
production CMake modules and startup guards, without game files or an SDK.
It is an arithmetic/build check, not an FPS benchmark or a BC-250 acceptance run.
The earlier missing-feature injection and disassembly results were not repeated.

The bounded, manually enabled experiment is ready for source review. It stays
off by default. Current-head game boot, driving, graphics/audio behavior and
BC-250 performance remain unqualified; the proposed device experiments above
remain necessary before making a performance claim or changing defaults.
