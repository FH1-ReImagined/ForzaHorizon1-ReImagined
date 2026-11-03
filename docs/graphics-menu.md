# In-game graphics settings

On Title Update 4, open **Graphics** above **Quit** in the original pause menu,
or below **Horizon Rally** on the main menu before gameplay. The screen uses
the original difficulty-screen layout, font, highlight and controller input.
The disc build retains its original menu. No game assets are distributed.

Use Up/Down to select a row and Left/Right to edit. **A / Save** applies and
saves all edits together without restarting. **B / Back** discards unsaved
edits. Spinner changes alone do not apply or save anything. Input is blocked
briefly while a saved renderer change applies. Failure restores the preceding
selections, displays the error and does not persist the failed request.

Controls appear in this order: quality preset, output resolution, upscaling,
anti-aliasing, MSAA scene resolution, texture filtering, shadow softness, ambient
occlusion, frame limit, VSync, and ultrawide HUD placement. Irrelevant rows dim immediately without requiring
hover or selection, and restore when relevant. Inactive values are retained.
The game's font cache is updated separately from native row input state.

Base FOV controls for chase near, chase far, cockpit, hood and bumper appear
before HUD placement. Stock preserves each camera's game settings, or choose
40–90 vertical degrees. The game's speed widening still applies, so the moving
FOV can exceed that base. Transitions blend the adjustment continuously. Save
applies without restarting; Back discards. VR head tracking bypasses these
controls. See [Driving camera field of view](cockpit-fov.md) for scope and evidence.

Ultrawide shows the physical window output and automatic drawing size. Its
rendering controls are read-only because changing them requires a launcher
restart; frame pacing, filtering and HUD controls still save independently.
Fractional heights such as 1080 use a 1440-line backing that is downsampled to
the window. Default/720p and custom launcher output modes display their current
value instead of selecting 1080p visually. Windows live rendering controls at
those modes unlock after choosing a supported 1080p/1440p/4K output.

MSAA scene resolution is independent of output/HUD resolution: at 1440p output,
1x renders a 1280x720 scene with a 2560x1440 HUD; 2x renders both at 2560x1440.
Native SMAA, DLAA and FSR Native AA use output-sized input. Upscaling chooses
its own lower scene resolution and supplies anti-aliasing. See
[Scene anti-aliasing](antialiasing.md). HUD placement requires HUD layout or
ultrawide support; runtime resolution changes currently support standard 16:9
output on Vulkan with host render targets. Other paths report an error.

Quality presets bundle reconstruction and filtering, preserving output/HUD
resolution, HUD placement, frame cap and VSync. Low uses Performance/2x filtering;
Medium uses Balanced/8x; High uses Quality/16x. Auto selects native at 1080p and
Quality above it, according to available reconstruction features. It is not a
benchmark. Without reconstruction, Low uses a 1x scene; the other presets use
output-sized scene scaling. These presets do not change model, shadow or
reflection quality. Shadow softness (Console, Soft, Softer) applies on Save; see
[Shadow softness](graphics-detail.md#shadow-softness-380). Ambient occlusion (Off, Low, High)
also applies on Save (Vulkan; dimmed on Direct3D 12); see
[Ambient occlusion](graphics-detail.md#ambient-occlusion-380). Manual image-quality edits return the preset to Custom.

The renderer applies one complete request between frames, waits for GPU work,
and rebuilds affected caches together. Failure restores the previous state.
Frame-cap changes reset timing history and update the guest vblank worker.
On Windows/NVIDIA/Vulkan, the launcher prepares its pinned runtime before a
native session too, allowing DLSS to be enabled later. Download failure still
permits native play. Saved choices are written atomically to
`graphics-settings.json`; the launcher validates and imports them at startup
and before launching again. Unrelated configuration and profile data remain.

The native macOS app also updates its external `forza.toml` on Save, since it
starts without the Python launcher. Mac-only choices include native 720p output
and MetalFX Temporal/Spatial reconstruction at 1440p or 4K output. Unsupported
MetalFX output sizes are rejected before rebuilding the renderer. At 720p,
presets retain a native 1x scene and adjust filtering.

Validation: the combined Windows TU4 build was tested on an RTX 5060 Ti using
disposable copied saves at 2560x1440 with a 120 FPS cap. MSAA 720p scene/native
1440p HUD, MSAA 1440p, SMAA 1440p and DLSS changes were exercised. Changing a
spinner wrote no settings or renderer request until Save. Switching upscaling
on/off disabled and restored AA/scene-scale rows without hovering them. Earlier
checks covered main-menu entry, presets, runtime failure rollback and restart
persistence. The split menu's import tests and standalone preset test cover
persistence, rejected requests, file-save failure and capability fallbacks.

## Performance overlay

The final menu row offers Off, FPS, and FPS + frame-time graph. It applies only
when saved and does not change quality presets. The launcher exposes the same
choice. It counts guest VdSwap intervals, not simulation ticks or UI repaints.
FPS uses a rolling second; the graph retains the last 240 intervals and expands
its range to include long hitches. Stale samples expire when frames stop.

White text appears at the top right without a background, border or input focus.
The optional graph is transparent. It uses an installed bold font (Arial Bold
on Windows and macOS or DejaVu Sans Condensed Bold on Linux), falling back to the SDK font.
It does not use the game's proprietary vector font. SDK patch 0078 allows this
passive dialog to draw with game frames without driving continuous presenter
redraws; interactive dialogs keep their existing input/animation repainting.

Validation: standalone tests cover steady rates, hitches, stale samples, ring
wraparound and invalid samples. Launcher tests cover persistence of all three
modes and invalid values. Earlier native-1440p gameplay checks compared the
overlay with the independent frame log; these are smoke tests, not benchmarks.
