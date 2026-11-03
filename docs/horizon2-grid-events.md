# Original FH2 grid and button events

Historical prototype: its runtime adapters and private test targets were retired
in favor of the [original FH1 native menu](native-menu.md). The implementation
described below is preserved at commit
`86256a1661d4a94d183875958ba70999c1b79091` for a separate research checkout.

The pause scene uses the original FH2 grid page-event and button-state routines
through `fh2_grid_events.h`. Seven translated functions execute in a bounded
1 KiB arena: the event hash, immediate page, page transition, focus, blur,
select and unselect routines. Formatting and event emission are explicit host
boundaries. Their result is an event hash; no FH2 object or vtable is installed
in FH1 guest memory.

## Private extraction

After preparing and generating the owner-provided executable, run:

```powershell
python tools/extract_horizon2_grid_events.py extracted/horizon2-ui/generated --output extracted/horizon2-ui/grid-events
```

Use a fresh output directory. The extractor checks the executable SHA-256,
requires output beneath an `extracted` directory, rejects unexpected calls or
macros, and rejects duplicate or missing functions. It only renames function
symbols, including the two host boundaries. `grid-provenance.json` records each
original translation's SHA-256. Both generated outputs remain private and must
not be committed or redistributed.

The executable check identifies the supported game build; the recorded
translation hashes describe the supplied generator output. They do not replace
trust in the preparation and code-generation tools.

## Standalone checks

Run the synthetic extraction checks without game files:

```powershell
python tools/test_horizon2_grid_events.py
```

The C++ harness needs C++23, installed SDK headers (including their SIMD
dependencies), and the private include. It does not link or launch the game:

```powershell
cmake -S forza-recomp/tests/horizon2-grid -B extracted/horizon2-ui/grid-test-build -G Ninja -DCMAKE_BUILD_TYPE=Release -DREXSDK_INCLUDE_DIR="PATH/TO/SDK/include" -DFH2_GRID_EVENTS_DIR="ABSOLUTE/PATH/TO/extracted/horizon2-ui/grid-events"
cmake --build extracted/horizon2-ui/grid-test-build
ctest --test-dir extracted/horizon2-ui/grid-test-build --output-on-failure
```

Run from the compiler's normal development environment. A Windows SDK toolchain
file can be passed with `-DCMAKE_TOOLCHAIN_FILE=...`. Assertions remain enabled
in Release. The SSE4.1 option is limited to Clang/GCC on x86 hosts.

The harness covers 28 original event cases (four immediate pages, 16 page
transitions and eight button-state combinations), rejects invalid pages, and
checks stack, return-address and nonvolatile-register preservation. It also
checks contract ancestry, parent-cycle termination, focus changes, teardown,
and the native material-color call's binding bytes and arguments. The color
test checks the adapter boundary; it does not execute the renderer.

A clean Windows clang-cl 23.1.3/Ninja Release configure, build and CTest run
passed on 2026-10-20 using the installed SDK and a fresh extraction. Regenerated
include/provenance files matched the existing private artifacts byte for byte;
all executable and generated-source files retained their pre-check hashes.
Other host platforms have not been built in this check.

## Native scene binding

`fh2_focus_binding.h` locates the nearest eligible factory-captured button
contract beneath a raw button node by following its parent at `+0x0C`. Supply
only button contracts from the current scene engine. Focus changes emit the
original blur/focus events through the caller's native event bridge. Forget
the stored focus on destruction; do not send events to destroyed nodes.

The actual `ANTHEM_PAUSE_MENU_BUTTON` and `PAUSE_MENU_BUTTON` vtables both use
the extracted virtual focus, blur, select and unselect methods. Their immediate
events are `FOCUSED`, `BLURRED`, `SELECTED` and `UNSELECTED`; animated variants
are `FOCUS`, `BLUR`, `SELECT` and `UNSELECT`.

The native TU4 packed material setter is `82F37C40`, taking a 12-byte binding
`{820307A4, material node, engine}` and `0xRRGGBB`. It updates diffuse channels
in 0–255 units and dirties the render object. The orange highlight material is
`Orange_HL.Material294`; target the material, not its parent group. Opacity is
separate and uses **0–100**: the renderer multiplies the scene property by 0.01.

The earlier native list-controller discovery bridge was not used by the scene
port and was removed. Its speculative layout adaptations are not part of the
production API or the validation claims above.

## Three-tab shoulder layout

`fh2_tab_layout.h` adapts the native TU4 `SUPER_STACKER` that the original scene
already creates. Its main tab stack has six triples of text, notification and
divider bindings, followed by RB. Hiding unused tabs does not remove
those bindings from layout, so RB retains the six-tab position.

After native initialization and label binding, `UseThreeTabs` verifies the
controller vtable and the RB/third-divider/sixth-divider node identities. It
moves RB immediately after the first three triples and limits relative background
sizing to three tabs. `UseFourTabs` remains available for its original layout.
After text measurement, `AnchorRightBumper` converts the active divider and RB parent to their shared ancestor's coordinate space. It
sets the RB authored coordinate after subtracting the stacker's text-width
delta. Their authored local coordinates cannot be compared directly because
they have different parents. The common ancestor's intro animation is excluded
from this calculation. The original `828D1E08` update then places the controls;
no per-frame transform correction is installed.

The records own their element bindings. Compaction swaps complete records so
the native destructor, which visits all 20 slots regardless of active count,
still destroys every binding exactly once. The additional standalone test
checks ownership preservation, parent-space conversion, independence from the
common ancestor's animation, invalid targets/nonfinite coordinates and
idempotence. Both standalone CTest targets pass.

The original child `GROUP_RB` also has a five-second X animation from -400 to
-0.5, independent of the native stacker. That caused first-open drift even
when the outer component was anchored correctly. `fh2_pause_tabs.py` applies a
bounded, same-size private source edit: it sets this node's BSG/BGF defaults
and both matching six-key X curves to -0.5, including their control values.
Key times, node identities, all other properties and record lengths remain
unchanged. Staging applies this edit automatically and records it in provenance.
The runtime anchor runs once after the initial text layout; no settling-frame
loop or continuous position override is needed. Run the synthetic checks with
`python tools/test_fh2_pause_tabs.py`.

The 2026-10-21 combined runtime check captured initial Home, tab changes,
native settings returns, physical car replacement and pause reopening. RB had
the same pixel bounds on first open and reopening, and identical image pixels
across the intervening menu pages. The staged BSG default and both BGF curves
were also independently read back and verified at -0.5.
