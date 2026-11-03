# Resource load/unload ownership (#83)

The racing crash in [#83](https://github.com/Dyotson/ForzaHorizon1-ReImagined/issues/83)
read a freed resource-data object's vtable on the loader thread. The existing
guard exchanges `resource+0x40` with zero during the cacheable resource load
step, then checks the returned object's vtable before releasing it.

That exchange alone does not make a competing unload safe. The disc and TU4
unload implementations still used separate load and clear instructions:

| Order | Unload | Load |
| --- | --- | --- |
| 1 | Reads pointer A from `resource+0x40` | |
| 2 | | Exchanges the field with zero, obtains A |
| 3 | | Releases A |
| 4 | Clears the field, releases A again | |

The existing vtable guard on the load side cannot reject A in this schedule:
it was still live when load acquired it. This is a demonstrable ownership gap;
it does not establish that this exact schedule caused the original player's
crash, whose first releasing thread was not captured.

The unload hooks now exchange the same field through the same helper. On each
executable, 19 unload implementations are reached through vtables sharing the
cacheable load step at slot 22 (disc `82C222C8`, TU4 `82CE2A38`). The hooks replace
the four instructions from the pointer load through the field clear. They
reproduce r3, the zero register (r10/r11/r29/r30), and CR6; preserve the saved
resource register (r11/r31); and resume at the original null branch. The
resource-specific cleanup and virtual Release remain in generated code.
The load hook retains its r31 return-value snapshot and r10/r11 effects.

Both sides use `std::atomic_ref<uint32_t>` on the aligned guest storage, with
big-endian conversion after acquisition. No delayed ordinary clear remains in
the executed hook range. Existing invalid-object diagnostics and the ten-report
limit apply on both paths. Other resource lifetime/publication bugs remain
possible; this does not add a general resource lock or lifetime tracking.

## Validation

The SDK-independent ownership regression models the old double-take schedule,
checks null/endian cases and publication between takes, and races two contenders
for 10,000 distinct pointers. It passes on Windows (clang-cl) and Linux
(Clang/GCC). The real hook compiles against both installed SDKs.
Address/undefined-behavior sanitizer checks also pass. Fresh code generation
succeeds for disc and TU4; all 20 affected load/unload functions per version
contain the expected hook call and resume label, and those generated functions
pass Windows clang-cl syntax checks. This is not a full game link or boot test.

```sh
g++ -std=c++20 -O2 -pthread forza-recomp/tests/resource_data_take_test.cpp -o /tmp/resource-data-test
/tmp/resource-data-test
python -m unittest discover -s tools -p 'test_resource_data_hooks.py' -v
```

The image auditor reads player-owned executables without writing image data:

```sh
python tools/verify_resource_data_hooks.py --disc /path/to/disc/default.xex --tu4 /path/to/tu4/default.xex
```

It discovers the unloads from the vtables, verifies the displaced instructions
and saved/zero registers, and checks hook names, register arguments, and resume
addresses. The existing main hints fail on the first missing unload hook; the
updated hints cover all 19 on disc and all 19 on TU4. Synthetic image tests
exercise missing hooks, incompatible instructions/registers/resume addresses,
duplicate/extra hints, and unsupported images in CI. The ownership regression
also runs in Linux CI; no private game files are needed there.

On 21 October, a private development TU4 host combined these hooks with #184,
including all 20 generated load/unload hook sites. A copied Golf R save with
official DLC completed native FR26 entry (VW Dub Club), race start, normal
retirement and return to free roam. All 185 streamed reads completed and their
queued headers matched relocation. This was offline Windows/NVIDIA testing;
native event entry bypassed career progression and the executable reused
private generated objects rather than rebuilding every current source file.
It did not reproduce the public online AMD report or establish #232 coverage.

The 22 October integration retains #184's atomic destination publication and
cache pin gate alongside these atomic load/unload takes. Both ownership
regressions and the synthetic hook audit remain enabled in release CI.
No new game test or runtime soak was performed for that integration.
#182 and the later `82DDA118` streamed-block failure remain unconfirmed.

## Current-main review preparation (26 October)

The branch now integrates main `b560528` (including the ReIGlue SDK migration,
VR and garage-capacity changes). The atomic ownership implementation and hook
addresses are unchanged. Both actual hook sources compile for disc and TU4
against the canonical ReIGlue headers. Both ownership regressions pass, including
10,000 competing takes, 10,000 reader/two-writer races and 10,000
publication/unload races. All six synthetic hook-audit tests pass; a read-only
audit of the original disc and TU4 images independently confirms all 19 unload
sites per executable. The PR diff against current main passes whitespace checks.

This prepares the bounded ownership correction for source review. It is not
current-head gameplay qualification: no full host rebuild, game boot, runtime
soak, online AMD reproduction or new sanitizer run was performed in this pass.
The historical runtime and sanitizer evidence above retains its original scope.
Do not close #83, #182 or #232 as reproduced and resolved on this evidence alone.
