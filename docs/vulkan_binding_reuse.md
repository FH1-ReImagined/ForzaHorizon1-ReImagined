# Unchanged Vulkan texture/sampler bindings

Opt-in experiment originally based on main `54d5177`, now integrated with main
`da9bf35`. SDK patch 0094.
`FORZA_VK_BINDING_REUSE=1` enables sampler acquisition and texture-request reuse.
Default is off. Texture descriptor reuse has been removed.

## Foreground regression and descriptor removal, 2026-10-24

Foreground testing on integrated main found tiled/duplicated rendering with
reuse enabled. The accepted reuse-off capture was clean. Both runs used the
same binaries and settings, four distinct physical CPU cores, and fully audited
foreground AI and CPU windows. The reference-checking warm-up also rendered
incorrectly despite zero sampler/texture mismatches or Vulkan API errors.
Those checks therefore do not establish visual correctness.

The observed reuse-on slowdown is not an equal-quality performance comparison.
The older measurements below describe their original builds and cannot justify
enabling this integrated version by default. The PR remains draft and reuse
remains off pending measurements of the revised patch.

A foreground diagnostic disabled descriptor reuse alone while keeping sampler
and texture-request reuse enabled. Its final race capture was clean, with zero
incorrect-resolve warnings (the full reuse-on run had 26,561). Counters recorded
71,114,357 sampler hits and 43,219,868 texture-request hits, zero descriptor hits,
and no reference execution. Both audited foreground windows were 100% and the
four-core affinity remained correct. This stats-enabled control establishes a
correctness distinction; its timings are excluded from performance claims.
The patch now removes descriptor reuse entirely. The exact underlying descriptor
lifetime/binding failure is not established by this control.

The revised 119-patch stack applies with source whitespace checks enabled. Its
combined 120-patch stack with #179 produces the same tree in either order. All
nine Vulkan command-processor consumers were rebuilt for the changed class
layout and the private GPU DLL linked successfully. The production guard tests
and descriptor write integration guard pass; the latter rejects the old code.

A final foreground run of revision `6ac17d2` with reuse enabled and interval
counters, reference execution and API validation disabled completed the full
60-second AI interval. The final race capture was clean and incorrect-resolve
warnings remained zero. AI and CPU foreground fractions were both 100%, with the
audited CPU mask unchanged at 0, 2, 4, 6 and valid frame-clock correlation. This
is a single NVIDIA correctness check, not a revised performance comparison.
Default enablement still requires repeatable measurements of the revised patch.

## Reuse and invalidation

Sampler acquisition is remembered per shader stage and texture-cache instance.
Both shaders' stage states retain dirty bits for fetch constants written since
that stage was acquired. A hit requires the same shader, cache, submission and
anisotropy override, with no sampled fetch constant written. Fetch notifications
cover scalar and bulk writes through the existing invalidation path. Shader
identity includes shader-declared filter overrides. Device format capabilities
and maximum anisotropy remain fixed for a cache instance. Overflow retries always
run normal acquisition for both stages, preserving submission marking and the
existing sampler lifetime and eviction rules.

A texture request can skip only if every requested fetch is synchronized, no
texture has become outdated, the request is covered by the previous request in
the current submission, and no texture image usage changed. Texture image usage
transitions advance a cache-wide epoch. CPU writes and resolves retain existing
shared-memory watch invalidation; eviction and frame reset clear synchronized
bindings. A miss runs ordinary ownership selection, uploads, resource marking
and barriers. There is no new persistent texture-object memo.

Texture/sampler descriptors always use the normal allocation/write path and bind
the fresh sets. Ordered payload equality was insufficient to establish safe
descriptor reuse in foreground testing.
Pipeline-layout changes clear bound-set flags from the first incompatible set
onward. This prevents an unchanged pixel payload from retaining a stale bound
flag after a vertex-layout change. External graphics pipelines and native-HUD
cache switches keep their existing invalidations. Constants descriptors and
constant-buffer uploads are unchanged.

## Verification

`FORZA_VK_BINDING_VERIFY=1` executes the complete normal sampler and texture paths
on predicted hits. It compares sampler parameters and handles, texture binding
views, and usage epochs. A mismatch logs an error; that invocation has already
used the fresh normal path. `FORZA_VK_BINDING_STATS=1` emits interval counters
once per 300 frames. Neither diagnostic is enabled in scored measurements.

The CPU-only `tools/binding_reuse` test exercises production sampler and texture
request guards across shaders, caches, filtering policies, submissions, image
usage changes, invalidation, subset requests and all 32 mask bits. It also checks
the compatible descriptor-layout prefix. A source integration regression guard
keeps the texture descriptor write decisions outside the reuse experiment and
checks the normal write/bind calls remain present; it does not replace GPU
validation. Configure with `-DSDK_SOURCE=<patched SDK>`, build Release and run
CTest. The benchmark runner also has checked alternating modes, diagnostics and
resume compatibility using a fake executor without launching a game.

For CPU-constrained measurements, the runner audits the observed process CPU
mask. Windows SDK initialization restores the system mask, so an initial
post-launch affinity assignment alone is insufficient. The runner corrects
startup resets only before the startup settling deadline (and before AI or CPU
scoring begins). Later changes reject the run without reasserting affinity.
The manifest records that affinity was audited, each process audit sample
records the observed mask, and resume rejects older unaudited constrained runs.
Run `python tools/binding_reuse/test_affinity.py` (requires `psutil`) for the
startup-reset, scored-escape and read-only scored-audit regression fixtures.

`RECOMP_PERF_EPOCH` optionally writes the wall-clock epoch corresponding to the
frame CSV's first steady-clock event. Scored CPU/frame correlation requires this
marker; missing markers and wall-clock jumps reject timing. `--diagnostic-only`
runs excluded AI warm-ups and returns correctness/audit metadata without FPS or
CPU/frame metrics, including on hosts whose relative CSV has no marker. The
`test_frame_clock.py` and `test_diagnostic.py` fixtures cover current CSV/report
formats, timing-window boundaries, invalid clocks and unqualified diagnostics.
The optional production epoch writer passed a CPU-only isolated-function harness
with the marker disabled and enabled, including nine-decimal output and steady
clock alignment. The changed authored debug object compiles and the private
shared host relinks successfully. Foreground testing with that host passed the
clock and affinity audits, but exposed the rendering blocker described above.

## Overlap

Checked #179 at `600eb0e` and #157 at `911d55c`. Each applies with 0094 in either
order, producing identical SDK trees. The combined #179/#157/0094 series also
produces identical trees with 0094 before or after the PR patches. #179 changes constant descriptors; this
experiment changes texture/sampler acquisition and descriptors. Their feature
switches are independent. `FORZA_VK_BINDING_STATS` is a shared diagnostic switch:
a combined build enables both experiments' counters, without enabling either
optimization. #157 changes
renderer integration in the same command processor. Cache-instance checks reject
reuse after the scene/native-HUD cache switches, and existing external-pipeline
invalidations remain effective. The combined #179/#157/0094 SDK also builds successfully as Windows x64
Release (`rexgpu-xenos` and `rexruntime`). These source/build checks do not
establish the performance or runtime correctness of a combined feature build.

## Benchmark protocol and results

Windows x64 Release, i9-13900K / RTX 5060 Ti (617.14), 64 GB RAM.
The display was verified at 2560 x 1440 / 360 Hz. Both variants target 240 FPS
with VSync off and confirmed IMMEDIATE presentation. The same executable and
GPU/runtime DLLs run both modes, toggling only `FORZA_VK_BINDING_REUSE`.
GPU fence waits use the original yield policy. Sampler-parameter memo and dynamic
constant descriptors are disabled; quality settings are identical between modes.

Five alternating 720p pairs use independent copies of a frozen seed and the
native eight-car Recaro Rush / Montano Dirt Cross AI race. Each score follows
15 seconds of movement settling and covers 60 seconds. Process/thread CPU cost
uses a separate 30-second window beginning 10 seconds into that score. No CPU
affinity is forced. Verification, interval counters, API validation, stack
sampling and GPU telemetry are off in these scored runs. Every retained score
has 100% audited foreground focus, no detected competing game/build, and zero
new graphics-pipeline creation entries in its AI interval. Traffic and car
positions are not deterministic.

| 720p mean over five runs per mode | Baseline | Reuse | Change |
| --- | ---: | ---: | ---: |
| Average FPS | 93.8834 | 99.9896 | +6.50% |
| Process CPU ms/guest frame | 33.2951 | 31.5835 | -5.14% |
| GPU command-thread CPU ms/guest frame | 9.2405 | 8.7366 | -5.45% |
| 1% low FPS | 71.4737 | 75.4471 | +5.56% |
| p99 frame ms | 13.2997 | 12.5468 | -5.66% |
| Mean per-run worst frame ms | 17.3982 | 16.3234 | -6.18% |

| Pair | Order | FPS change | Process CPU ms/frame change | GPU command-thread CPU ms/frame change |
| --- | --- | ---: | ---: | ---: |
| 1 | baseline / candidate | +6.65% | -2.92% | -3.00% |
| 2 | candidate / baseline | +3.07% | -2.55% | -0.27% |
| 3 | baseline / candidate | +10.17% | -9.57% | -10.79% |
| 4 | candidate / baseline | +7.61% | -7.64% | -7.66% |
| 5 | baseline / candidate | +5.08% | -2.83% | -5.19% |

All five pairs favor reuse for FPS and process CPU cost per frame. These results
establish a benefit in this race and configuration, with the optimization kept
opt-in and local. They do not establish additive gains with #179 or performance
with #157's native passes enabled.

### Reference checks

720p: 4,514,704 verified sampler-stage hits and 2,799,856
verified texture-request hits, with zero mismatches and no reported Vulkan API
validation errors. Descriptor payloads are compared field by field on each hit.
The sampler-stage hit rate is 48.88% (including empty stages);
texture requests hit 60.63% and descriptor payloads hit
32.86%. Counter windows emitted during AI scoring can
straddle its boundaries; these rates describe the excluded diagnostic run.

1440p: 4,591,773 verified sampler-stage hits and 2,856,320
verified texture-request hits, with zero mismatches and no reported Vulkan API
validation errors. Descriptor payloads are compared field by field on each hit.
The sampler-stage hit rate is 48.94% (including empty stages);
texture requests hit 60.88% and descriptor payloads hit
33.23%. Counter windows emitted during AI scoring can
straddle its boundaries; these rates describe the excluded diagnostic run.

Standalone production-guard tests and runner CLI/resume checks pass. The
Windows SDK build, exact-source verification and combined #179/#157/0094 build
pass. Both PR heads were rechecked after timing. Canonical blank context lines
in the patch are retained; applying its added source with whitespace errors
enabled passes.

### Native-resolution diagnostic

One 2560 x 1440 pair follows the separate excluded 1440p verification run.
The scored modes have counters, reference checking and API validation disabled;
external GPU utilization is sampled at 1 Hz.

| Native diagnostic | Baseline | Reuse |
| --- | ---: | ---: |
| FPS | 57.9859 | 57.9516 |
| 1% low FPS | 40.2989 | 33.0958 |
| p99 frame ms | 18.7729 | 19.7750 |
| Process CPU ms/guest frame | 43.4810 | 44.2931 |
| GPU utilization during CPU window | 97.80% | 97.70% |

The native FPS difference is -0.06%. This is one pair,
not a repeatability claim. The GPU utilization and the full per-run metrics
should be considered alongside the stronger five-pair 720p result.

Native 1% lows change from 40.30 to 33.10 FPS, and p99
from 18.77 to 19.77 ms. The candidate's worse tails in this pair are a
limitation, not an established repeatable regression. Process CPU cost changes by
+1.87%. Default remains off.

One interrupted startup warm-up was excluded when another game launched. An
initial native candidate attempt was rejected by the foreground audit after a
late focus handoff and external window snapshot; it produced no retained
performance result. The native pair was restarted with startup-only activation
and capture. All rejected raw output is preserved separately.

The inspected captures from pairs 2 and 5 and both final native runs show no
obvious new rendering corruption. Traffic poses differ, so this is a visual
spot check, not pixel-equivalence proof.

### Provenance and limits

The common executable contains native AI instrumentation from
`ec7300244e369d2802b1112ecd02bc681cb8cf94` on game source `fc55901`; this is not a
fresh full-game build of main. The GPU/runtime DLLs are freshly rebuilt from
main `54d5177`'s 67 public SDK patches plus 0094, with no private SDK extras.
The frozen seed is hashed and verified unchanged. Binary hashes, exact SDK trees,
patch hash, all retained runs, excluded diagnostics and overlap proofs are in
[the portable results](benchmarks/vulkan-binding-reuse-2026-10-20.json). Raw logs,
captures and private builds remain under `build/binding-reuse`.

### Main integration, 2026-10-22

Integrated main `58bfc10`, including the Controls changes. Patch 0094 retains its
number and behavior. Its explicit binding-header include is inserted away from
the include context that main's shader-prebuild patch 0113 modifies.

All 92 SDK patches apply in order to pinned SDK `bd833a2`. The exact public
series passes an isolated Windows x64 Release Vulkan/D3D12 GPU-plugin and runtime
build. The production binding-reuse CPU guard test passes (1/1). GPU/gameplay
validation and benchmarks were not repeated; the preceding measurements retain
their original provenance. Current-branch runtime qualification, broader workload
coverage and repeated native-resolution frame-time checks remain outstanding.
Binding reuse stays off by default and the PR remains draft.

### Current-main integration, 2026-10-24

Signed merge `79b6faf` integrates main `da9bf35`; patch 0094's behavior and
default are unchanged. All 119 SDK patches apply in order with strict added-source
whitespace checks to pinned SDK `bd833a2`. All 1,370 public SDK source blobs match
the resulting tree `7d397a176ce27bedb24fbed9e6ff714a01d0bff1`. The isolated Windows
x64 Release Vulkan/D3D12 GPU-plugin and runtime build passes, as does the production
binding-reuse guard executable.

Patch 0092 from #179 also applies independently to this main. The combined 120-patch
series produces tree `3b2f41813315cc38897ce655e55c809475476efb` with either PR's
patch applied first, or with all patches in numerical order. This is source
compatibility evidence; combined enabled behavior has not been qualified.

Signed commit `1cc7541` adds the observed-affinity audit described above. Four
CPU-only regression fixtures, a native Windows child-process reset/escape check,
and alternating-mode/resume CLI checks pass. These changes validate the
measurement protocol, not a new performance result. The historical measurements
above retain their original source and hardware provenance.

The current-stack excluded reference/API diagnostic completed a valid AI-driven
eight-car race at 720p on the RTX 5060 Ti (driver 617.14), using four separate
P cores (logical CPUs 0, 2, 4, 6). It verified 8,706,849 sampler-stage hits and
5,278,876 texture-request hits, with zero mismatches and no Vulkan validation
errors. The layer emitted ten shader-output-not-consumed warning messages,
including its duplicate-suppression notice. All 384 process-audit samples retained the requested CPU
mask and recorded no competing build/game process. One post-run capture showed
no obvious new rendering corruption; this is a spot check, not pixel equivalence.

**This is correctness evidence only.** Foreground was false throughout after
Computer Use app approval timed out. The original runner also rejected timing
because this host lacked the optional epoch marker. No FPS or CPU/frame result
is published from this run. The host uses 82 current-main authored objects with
disclosed private controller/silent-audio/import fixtures and previously compiled
private generated objects. The copied source profile was unchanged. The
[portable correctness report](benchmarks/vulkan-binding-reuse-2026-10-24.json)
records exact source trees, binaries, audit results and limitations. Default
remains off; representative foreground and native-resolution scoring is pending.
