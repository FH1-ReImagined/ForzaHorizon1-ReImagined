# Pinyon Shift implementation replacement

This is an implementation and provenance record, not a declaration that the
project is free of Pinyon Shift code. The BSD notice and packaging remain because
the HDR implementation, SDK dependency and possible reused source still require
review and replacement. Three obsolete archive copies have been removed. The BSD-3-Clause terms require preserving the copyright,
conditions and disclaimer in source distributions and reproducing them with
binary distributions; they do not require relicensing this project's own code.

## Repository inventory, 2026-10-27

| Repository | Audited main commit | Tracked paths | Local branch heads |
| --- | --- | ---: | ---: |
| ForzaHorizon1-ReImagined | `52d5784af194328802a98d43e0c633265c922645` | 1038 | 180 |
| ReIGlue | `7d17ea51a51861cfef4244afc06a908bf1fd74a2` | 1409 | 4 |
| forza-live | `526b8bb57906` | 57 | 3 |
| website | `9e73f29acd97` | 29 | 1 |
| .github | `f901dcdfd21a` | 1 | 1 |

The organization lists these five repositories. Searches cover tracked files,
submodule entries and every local branch head for `pinyon`, `shiftglue` and
`arcanite24`. The last three repositories have no matches. A negative text search
does not establish independent authorship; provenance and diffs are also needed.
The game's server submodule points to this organization's forza-live repository.
Old local feature branches still contain the inherited implementation. This work
does not rewrite published history or overwrite anyone's active checkout.

## Replaced here

| File | Replacement and evidence |
| --- | --- |
| `forza-recomp/src/platform/presentation_policy.h` | New countdown state machine. World evidence can acquire for two presentations, retain for thirty; map evidence suppresses three presentations. It accepts evidence before frame zero and resets on initialization. |
| `forza-recomp/src/platform/ultrawide.cpp` | New synchronized session owns dimensions, projection and state. Host callbacks run outside the lock. Existing cvar names remain the configuration interface. |
| `forza-recomp/src/platform/ultrawide_math.h` | Geometry derived from a 720-line presentation canvas and a bounded HUD rectangle. Nonfinite hot-reload values select neutral defaults. |
| `forza-recomp/src/hooks/ultrawide_projection.cpp` | New guest getter adapter uses the established viewport fields and caller addresses, and preserves the VR eye-aspect override. Addresses and memory layout are interface facts. |
| `forza-recomp/tests/presentation_policy_test.cpp` | Acquisition/expiry, map priority in both callback orders, background rendering, disable and reset. |
| `forza-recomp/tests/ultrawide_math_test.cpp` | Existing pixel-shape, safe-area, anchor and backing-height checks; nonfinite input cases added. |
| `forza-recomp/src/hooks/ai_catchup.cpp`, `src/platform/ai_catchup.h` | Remove the config-field neutralization policy. Isolate comparison-query results at five verified call sites instead, with precise stale live-state cleanup. See `docs/ai-comparison-policy.md`; runtime qualification remains outstanding. |
| `tools/check_hdr_capture.py`, `tools/test_hdr_capture.py` | Verifier and independent known-answer tests for the new `diffuse-exp-v1` SDK curve/grid. Refuses old captures rather than applying the new model to them. |
| `launcher/forza_launcher/save_editor.py`, `launcher/test_save_editor.py` | Iterative prefix walker replaces recursive traversal and the unexplained depth-8/name-256 policy. Byte-derived count feasibility, explicit object/path budgets and sibling duplicate detection preserve scalar offsets and opaque tail bytes. Synthetic contracts include 1,100 nested sections and 300-byte names. |

These replacements were written from the existing interfaces and documented
behavior. This is not a claim of a segregated clean-room process: the previous
implementations were inspected during the provenance audit.

## Removed archives and remaining covered or unresolved material

| Location | Status |
| --- | --- |
| `patches/rexglue-sdk/0063-native-ultrawide-and-hud.patch` | Removed from the obsolete archive. It is not consumed by current builds; historical Git revisions retain the original notices. |
| `patches/rexglue-sdk/0082-spirv-apple-color-shader-contraction.patch` | Removed from the obsolete archive. Live ReIGlue replacement uses a completed-module pass. |
| `patches/rexglue-sdk/0091-hdr-scrgb-presenter.patch` | Removed from the obsolete archive, including its embedded generated SPIR-V. The live SDK still contains remaining covered integration. |
| ReIGlue `src/ui/vulkan/vulkan_presenter.cpp`, `include/rex/ui/vulkan/presenter.h` | Separate SDK PR #5 replaces image ownership, format selection, composition/output pass construction and coverage/output recording. Stable interface declarations, cache invalidation and shutdown calls remain for final provenance review. The game pin still uses the prior implementation. |
| ReIGlue `src/ui/shaders/hdr_output.ps.glsl`, `hdr_ui_mask.ps.glsl` and their `vulkan_spirv/*` headers | Replacement shaders and regenerated binaries exist on the separate ReIGlue branch; the pinned SDK still carries the adaptation. |
| ReIGlue `src/graphics/shaders/hdr_hud_mask.cs.glsl` and generated header | Review found the first replacement retained the original threshold/dilation policy. A subsequent per-pixel compositing lower-bound design replaces it; the pinned SDK still carries the adaptation. |
| ReIGlue Vulkan command processor and render-target cache HDR paths | SDK PR #5 replaces compute construction, descriptor recording and image allocations, including reused VR allocation/sharpening helpers. Checkpoint `8c97dd5` replaces `CopyHdrScene` with a typed, validated exact copy and tracked image transitions; source review accepted the substantive replacement. The capture decision still implements the inherited policy. Capture timing, source selection and rendering remain unresolved. |
| `forza-recomp/src/hooks/ai_catchup.cpp`, `src/platform/ai_catchup.h` | Review found substantive carryover in the original ordered config neutralization despite different hooks and traces. That policy is removed in the new query-isolation draft; historical research credit and notices remain. Config offsets retained for tracing are game-layout facts, not a clearance claim. |
| `docs/launcher.md`, `launcher/forza_launcher/save_editor.py`, `saves.py` | Research citation retained. Initial commit `ad75b8b` records the save manager/editor. A later audit also inspected upstream `tools/fh1-profile.py`; its depth/name limits were shared with the first parser. The subsequent iterative design removes those policies. Save management's staging/journal/hash flow differs from upstream. The crypto/key-derivation provenance audit is separate and is not cleared by parser tests or by absence of matching lines. |
| macOS tuning/performance comparison documents | Historical measurements and citations retained. They are not runtime dependencies. |

The source commits named by the HDR documentation are ShiftGlue `36275a6`,
`2c82d4d` and `159464d`; its macOS reference is `524c6fb`. The local reference
checkout preserves those revisions. The Pinyon game repository carries the BSD
3-Clause notice; ShiftGlue retains ReXGlue/Xenia BSD notices as well. All remain
intact where their material remains.

The experimental ReGPU checkout is pinned at
`bdd133abb56824eef2cd59d2864f2f051c59d207`. Its actual license is MIT, copyright
2026 Scott. The active backend integration (`feature/regpu-backend`, `21b6372`
at audit start) preserves ReGPU/FidelityFX notices and the ReXGlue/Xenia BSD
notice for its adapted float24 conversion. It is not evidence of a Pinyon
dependency. The backend owner reported no Pinyon imports; their active edits
were left untouched.

## Validation and limits

Windows clang 23.1.3, C++20, `-Wall -Wextra -Werror`: presentation policy and
ultrawide geometry tests pass. Both changed production translation units pass
syntax checks against the existing r2 SDK headers. No game run, GPU image
comparison, full link or macOS runtime validation has been performed for this
replacement. The SDK's own contract tests and syntax checks are recorded in its
corresponding replacement branch. No game save or qualified SDK binary changed.

The subsequent AI replacement passes its contract tests and production syntax
checks with both `FORZA_TITLE_UPDATE=0` and `4`. The new HDR verifier passes four
known-answer tests with NumPy 2.3.5. No linked game or race run has qualified the
new AI mechanism, and no new HDR capture has qualified the redesigned curve.

The iterative save parser passes all 21 synthetic save-editor tests, including
authentication, no-edit byte identity, field-only mutations, stale-session/backup
failure rejection, deep nesting, oversized names, resource budgets, duplicates
and every truncated prefix. No real save was opened or changed for this check.

## Replacement checkpoints

The game branch is PR #493. The companion ReIGlue branch is PR #5, source
checkpoint `7bb18e3`. The SDK has eight passing standalone contract tests:
HUD geometry, shader contraction, HDR color, parameter ABI, format selection,
generated shader bindings, scene capture policy and injected compute-allocation
failures. Both updated production translation units pass Windows syntax checks.
This checkpoint also linked and passed DLL loader/ABI checks. Independent review
then identified substantive carryover in the curve, calibration, coverage,
capture and AI policies. The subsequent HDR redesign and AI query-isolation
mechanism change those policies and require new qualification; the inherited
scene trigger remains unresolved. The later exact-copy replacement passed its
private build and loader/ABI checks at `8c97dd5`, without GPU qualification.
The new SDK design is recorded in
`docs/reiglue/independent-hdr-design.md` in PR #5. No existing notices are removed.

The source comparison is evidence about particular implementations, not an
inference from absence of a project name. Research references remain useful and
are not removed to conceal provenance. Historical branches, published binaries
and the current SDK pin retain their original source and required notices.
The active ReGPU branch also inherits its game's existing presentation code;
its newly added backend was the part checked for additional Pinyon imports.

On 2026-10-28, work resumed at unchanged remote heads `3d18dd6` (game #493)
and `6ef569d` (ReIGlue #5). The next SDK source commit, `342686e`, replaces the
hot-toggle predicate with a request/commit protocol: one setting snapshot drives
connection attempts; a fully successful connection acknowledges that request
even for SDR fallback; the next changed request triggers capability selection.
Existing retirement, thread exclusion and reconnect failure handling remain
dependencies. Derivation and the bounded private build plan are recorded in the
SDK design document. The presenter passes syntax checking; request, format and
exact-copy contracts pass. No new DLL link or game/GPU run occurred. The game SDK
pin, required notices and remaining capture/integration/crypto limitations are
unchanged. Runtime qualification still requires a separate resource grant.

The subsequent separately granted private SDK validation rebuilt four production
units at `342686ee76dd5758e691a57f84ce9e0964441ef7` and linked both DLLs in a
single serial job, including loader/ABI checks, in 27.063 seconds. All 319
previously recorded input/output hashes matched before and after. Two objects
without historical hash anchors were rebuilt; the four reused replacement
objects matched their recorded hashes. Both DLLs loaded, ABI 1 was returned,
all 135 GPU imports resolved against 11,280 runtime exports, and invalid requests
were rejected without graphics creation. Exact object/DLL hashes and step results
are in the SDK's `docs/reiglue/validation/2026-10-28-hdr-request.json`; its
`capture-witness-plan.md` records the next evidence requirements and current
instrumentation gaps. CPU was released. No game/GPU run occurred, and the SDK pin
and runtime/provenance gates remain unchanged.

One subsequent granted HDR-off SDK smoke session closed after 183.563 seconds
under an owned Job/watchdog. A prior-main compatible host reached the opening
Viper/Flynt world sequence with scene/HUD, paused and resumed through normal UI.
The source seed did not reach the intended garage/ordinary free-roam, so those
cases remain open. The host excludes this PR's new AI/presentation mechanisms;
the run does not qualify them, HDR-on/VR, hot-toggle or capture acquisition.
The SDK records exact hashes and limits in
`docs/reiglue/validation/2026-10-28-hdr-off-smoke.json`. Source profile/cache,
shared game data and private runtime inputs were unchanged; Job and full game
process enumeration were empty on closure. Required notices and SDK pin remain.

On 2026-10-29, companion SDK source `e6c1711` adds a narrow connection-status
replacement after the request policy. Surface capability remains private during
construction; one atomic observation is published only after all connection
resources succeed, and retirement clears both capability and active status.
Independent review, the new status/existing request contracts and presenter
syntax checking pass. Normal CMake registration retains Release assertions and
portable thread linkage; a private negative control fails its retirement assertion.
The SDK records source evidence in
`docs/reiglue/validation/2026-10-29-hdr-status-source.json`.

All fourteen affected production units then compiled privately without PCH, and
exports regenerated. The first 64.344-second window stopped on a driver parser
error before linking; its receipt and object hashes remain preserved. After parser
regressions and hash checks, a separately granted 2.797-second link-only window
passed both private DLL links and loader/ABI checks without recompilation. All
135 imports resolved against 11,280 exports, ABI 1 and invalid-request rejection
passed, and initial HDR status was `false/false`. All 347 protected hashes matched.
Exact evidence is in `docs/reiglue/validation/2026-10-29-hdr-status-build.json`.
No game/GPU run qualifies these changes. Connection transitions, HDR/VR, this PR's
AI/presentation, scene acquisition and the other provenance gates remain open.
The game SDK pin, shared binaries and all required notices remain unchanged.
