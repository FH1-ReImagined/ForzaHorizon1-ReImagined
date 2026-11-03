# Camera-facing minimap

In the game's **Graphics** settings, set **Minimap orientation** to **Camera**.
The gameplay minimap follows where you look, including looking sideways or behind
the car. **Vehicle (stock)** keeps FH1's original behavior. Save keeps the choice;
Back discards changes. The launcher also has **Display > Camera-facing minimap**.

This is opt-in and requires Title Update 4. It works independently of the new pause
menu. The full map and pause-menu map preview retain their normal orientation.
VR cockpit/head-pose mode, loading, rewind and paused gameplay use the native map.
The player's arrow and GPS route still use the original map geometry.

The main-loop view submission publishes a bounded heading snapshot no older than 250 ms.
Only the gameplay HUD map record borrows it at the native map-render pass. The
map camera's heading and matrices are restored afterward; no vehicle direction or
gameplay camera is changed. Disabling the setting rebuilds the native geometry.

Validation so far: heading math/concurrent snapshot tests, native adapter fixtures,
launcher persistence tests, touched-TU compilation and a linked private production
host. An actual offline TU4 run selected Camera in the stock Graphics page, saved
the preference, and captured a stationary car looking right with the minimap
rotated. The saved native setting passed the production launcher import and fresh
configuration reload. A fresh offline TU4 process then loaded the saved Camera
choice without an orientation command-line override, followed a look-right input,
and opened and closed the full map before resuming gameplay. Full-map and preview
exclusion also pass the native adapter fixtures; the new pause-menu preview was
not separately exercised in that stock-pause runtime check. Hardware coverage is
limited to the Windows/Vulkan test host; other rendering platforms remain untested.

The native HUD hook validates guest address ranges without querying OS page
protections. This avoids per-frame `/proc/self/maps` scans on Linux kernels
without `PROCMAP_QUERY`; adapter tests require zero protection queries.
