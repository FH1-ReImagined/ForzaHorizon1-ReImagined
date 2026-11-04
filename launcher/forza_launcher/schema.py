"""Every forza.toml key the launcher manages.

Each entry is a ReXGlue SDK cvar or one this project defines in forza-recomp/src (frame_rate,
live_server, the Horizon World switches, ...). A cvar can be set in forza.toml next to the game
binary or as --name=value on the command line; the launcher writes forza.toml. A key that
holds its default is not written, so the file stays small. `default` is what "unset" means
for the game (a few use 0/"" as "automatic").
"""
from dataclasses import dataclass, field
import math

from . import gamertag, languages, power
from .paths import IS_WINDOWS

GROUPS = ["Display", "VR", "Controls", "Gameplay", "Audio", "Online", "Profile", "Advanced"]
# The frame rates under 60 the game runs (forza-recomp/src/main.cpp): 20 and 40 divide 60 and
# 120 Hz screens, so every frame stays on screen for the same number of refreshes.
LOW_FRAME_RATES = (20, 30, 40)


@dataclass
class Setting:
    key: str
    label: str
    group: str
    kind: str  # bool, int, float, str, choice, combo, fps, keys
    default: object
    help: str = ""
    section: str = ""  # sub-heading inside the group
    choices: list = field(default_factory=list)  # [(value, label)], for choice / combo
    lo: float | None = None
    hi: float | None = None
    step: float = 1
    platform: str = ""  # "linux" or "windows": shown only there
    restart: bool = False
    max_length: int | None = None  # optional string input limit
    legacy: dict = field(default_factory=dict)  # value an older version offered -> the one it became

    def applies(self) -> bool:
        return not self.platform or (self.platform == "windows") == IS_WINDOWS


def _c(*values):
    return [(v, v) for v in values]


S = []


def add(*a, **k):
    S.append(Setting(*a, **k))


# --- Display ---------------------------------------------------------------------------
CAMERA_FOVS = (
    ("forza_chase_near_fov", "Chase near", 48.5),
    ("forza_chase_far_fov", "Chase far", 58),
    ("forza_cockpit_fov", "Cockpit", 62),
    ("forza_hood_fov", "Hood", 66),
    ("forza_bumper_fov", "Bumper", 53),
)
CAMERA_FOV_KEYS = {key for key, _, _ in CAMERA_FOVS}
for key, label, stock in CAMERA_FOVS:
    add(key, f"{label} base FOV (vertical degrees)", "Display", "choice", 0,
        f"Stock preserves the game's camera behavior (normally {stock} vertical degrees). "
        "Select a base FOV from 40 to 90 vertical degrees. The game's speed widening still "
        "applies, so the moving view can exceed the selected base. Camera transitions blend "
        "the adjustment. Ultrawide keeps its wider horizontal view. Disabled during VR head tracking.",
        "Camera", choices=[(0, f"Stock ({stock} degrees)")] +
        [(degrees, f"{degrees} degrees") for degrees in range(40, 91)])
add("frame_rate", "Frame rate", "Display", "fps", 30,
    "30 FPS is the game's default. You can raise it up to 240 FPS without changing the game's "
    "physics speed. Pick a rate or type your own. Match display uses the refresh rate of the monitor "
    "the game runs on (see Monitor), up to what this PC is likely to hold; if it cannot be read, 60 FPS. "
    "20 FPS: for PCs that can't hold 30, every frame stays on screen equally long on a 60 Hz screen "
    "(40 FPS does the same on a 120 Hz screen for PCs that can't hold 60, and on a Steam Deck with its "
    "screen at 40 Hz or 80 Hz; Match display picks 40 there).", "Frame rate")
add("forza_performance_overlay", "Performance overlay", "Display", "choice", "off",
    "Measured game FPS, optionally with a frame-time graph. Does not count UI redraws.",
    "Frame rate", choices=[("off", "Off"), ("fps", "FPS"), ("graph", "FPS + frame-time graph")])
add("battery_saver", "Battery saver", "Display", "choice", "off",
    "Starts the game at 30 FPS and render scale 1 with AMD FSR 1 (FSR 3 and SMAA off) and Scene "
    "anti-aliasing Off, the lightest settings, without changing yours: they apply again at the next "
    "start on mains power. A frame rate of 20 or 40 (for a screen at 40 Hz) stays. Auto applies it "
    "when a Steam Deck runs on its battery as the game starts.",
    "Frame rate", choices=list(power.MODES), platform="linux", restart=True)
add("vsync", "Vertical sync", "Display", "bool", True,
    "Wait for the display's refresh. Turn off only for testing.", "Frame rate")
add("video_mode_refresh_rate", "Guest refresh rate (Hz)", "Display", "float", 0.0,
    "0 picks it from the frame rate. Set it to override.", "Frame rate", lo=0, hi=480, step=1)
add("fullscreen", "Borderless fullscreen", "Display", "bool", True,
    "Start in borderless fullscreen (recommended).", "Window")
add("fullscreen_exclusive", "Exclusive fullscreen", "Display", "bool", False,
    "Start in exclusive fullscreen (switches the display mode).", "Window")
add("window_width", "Window width", "Display", "int", 0,
    "Logical pixels for windowed mode. 0 uses the default.", "Window", lo=0, hi=8192, step=10)
add("window_height", "Window height", "Display", "int", 0,
    "Logical pixels for windowed mode. 0 uses the default.", "Window", lo=0, hi=8192, step=10)
add("monitor", "Monitor", "Display", "int", 0,
    "0 default, 1 primary, 2 second monitor, ...", "Window", lo=0, hi=16)
add("video_driver", "Video driver", "Display", "choice", "",
    "SDL video driver. Automatic is right unless the window misbehaves.", "Window",
    choices=[("", "Automatic"), ("wayland", "Wayland"), ("x11", "X11")], platform="linux",
    restart=True)
add("forza_ultrawide", "Ultrawide gameplay", "Display", "bool", False,
    "Follow the window's aspect ratio with a wider horizontal view. Menus and map stay 16:9. "
    "Renders at the physical window resolution with proportional HUD. Render scale is automatic. "
    "Requires Vulkan and the patched SDK. Fractional heights, including 1080, render at the next "
    "720-line multiple and downsample to the window; this costs more GPU work than native 1080p. "
    "Restart after resizing to change render resolution.",
    "Ultrawide and HUD", restart=True)
add("forza_hud_edges", "Expand HUD to ultrawide edges", "Display", "bool", False,
    "Anchor side HUD elements toward the full window edges while keeping gauges and text proportional. "
    "Keeps the original screen insets. Experimental: long centered text and race overlays need more testing. "
    "Applies during gameplay; restart after changing.",
    "Ultrawide and HUD", restart=True)
add("forza_hud_layout", "Customize HUD layout", "Display", "bool", False,
    "Adjust the HUD safe area and position at 16:9 or ultrawide, without changing the camera. "
    "Applies on the next game launch.", "Ultrawide and HUD", restart=True)
add("forza_minimap_camera", "Camera-facing minimap", "Display", "bool", False,
    "Rotate the gameplay minimap with where the camera looks instead of the vehicle. "
    "Also available in the in-game Graphics settings. Requires Title Update 4; "
    "the full map and VR cockpit keep their native orientation.", "Ultrawide and HUD")
add("forza_hud_safe_width", "HUD safe width %", "Display", "float", 100.0,
    "Fit the centered 16:9 HUD within this width. The smaller safe-area percentage sets the "
    "uniform scale, keeping gauges and text proportional.", "Ultrawide and HUD", lo=50, hi=100, step=1)
add("forza_hud_safe_height", "HUD safe height %", "Display", "float", 100.0,
    "Fit the HUD within this height. Applies during gameplay.", "Ultrawide and HUD", lo=50, hi=100, step=1)
add("forza_hud_offset_x", "HUD horizontal position %", "Display", "float", 0.0,
    "Move the HUD as a group: negative left, positive right. Clamped to keep it on screen.",
    "Ultrawide and HUD", lo=-20, hi=20, step=1)
add("forza_hud_offset_y", "HUD vertical position %", "Display", "float", 0.0,
    "Move the HUD as a group: negative up, positive down. Clamped to keep it on screen.",
    "Ultrawide and HUD", lo=-20, hi=20, step=1)
add("resolution", "Resolution preset", "Display", "combo", "",
    "Guest video mode and window size in one: 720p, 1080p, 1440p, 4k or WxH. Empty keeps the "
    "defaults (1280x720 guest). The game still draws at 1280x720; Render scale sets the "
    "drawing resolution. With Ultrawide gameplay, the physical window size controls drawing; "
    "changing this preset does not change a borderless monitor's resolution.", "Picture",
    choices=[("", "Default"), ("720p", "720p"), ("1080p", "1080p"), ("1440p", "1440p"),
             ("4k", "4K")])
add("resolution_scale", "Render scale", "Display", "int", 1,
    "Draws at 1x to 8x the guest resolution. 1 with FSR 1 is the fast default; 2 is real extra detail "
    "for about 40% fewer fps; above 2 is experimental (flicker on NVIDIA, issue #47).", "Picture",
    lo=1, hi=8, restart=True)
add("scene_resolution", "3D scene resolution", "Display", "choice", "720",
    "For integrated graphics that can't hold the frame rate: 540p draws the 3D scene at 960x540 "
    "(about half the pixels) and scales it to 720p; the HUD, text and menus stay at 720p, and the "
    "upscaling filter (AMD FSR 1 is the sharpest) takes the picture to the window. It saves GPU time "
    "only: a PC limited by its CPU (Steam Deck) gets nothing. The scene is drawn in one pass with 2x "
    "MSAA (Scene anti-aliasing Off removes it). Not with AMD FSR 3, NVIDIA DLSS, a separate "
    "anti-aliasing method or Ultrawide gameplay, which keep 720p.", "Picture",
    choices=[("720", "720p (game)"), ("540", "540p")], restart=True)
add("anisotropic_override", "Anisotropic filtering", "Display", "choice", 3,
    "Texture sharpness at oblique angles.", "Picture",
    choices=[(-1, "Game default"), (0, "Off"), (1, "1x"), (2, "2x"), (3, "4x"), (4, "8x"),
             (5, "16x")])
add("present_effect", "Upscaling filter", "Display", "choice", "bilinear",
    "How the 720p picture is scaled to the window. AMD FSR 1 is the sharp, cheap choice (about the "
    "cost of bilinear); CAS only sharpens, up to 2x. AMD FSR 3 (temporal) reconstructs the scene "
    "from several jittered frames at the output resolution below, with the HUD drawn natively; "
    "any GPU (Vulkan).", "Picture",
    choices=[("bilinear", "Bilinear"), ("cas", "AMD CAS (sharpen)"), ("fsr", "AMD FSR 1"),
             ("fsr3", "AMD FSR 3 (temporal)")],
    restart=True, legacy={"fsr2": "fsr"})
add("fsr3_mode", "FSR 3 quality", "Display", "choice", "performance",
    "The scene's input size for AMD FSR 3: Quality 1/1.5 of the output per axis, Balanced 1/1.7, "
    "Performance 1/2 (720p for 1440p); never below the game's native 1280x720, "
    "so at a 1080p output Balanced and Performance also use 720p. Only with the AMD FSR 3 filter.",
    "AMD FSR 3", choices=[("quality", "Quality"), ("balanced", "Balanced"),
                          ("performance", "Performance")], restart=True)
add("fsr3_output_resolution", "FSR 3 output resolution", "Display", "choice", "1440p",
    "The reconstructed picture and native UI resolution for AMD FSR 3.", "AMD FSR 3",
    choices=[("1080p", "1920 x 1080"), ("1440p", "2560 x 1440"), ("4k", "3840 x 2160 (4K)")],
    restart=True)
if IS_WINDOWS:
    add("fh1_upscaler", "NVIDIA DLSS", "Display", "choice", "off",
        "Reconstruct the scene with NVIDIA's recommended input size for the selected mode, "
        "with integrated temporal anti-aliasing. Menus, HUD and car labels stay at output resolution. "
        "The scene uses an integer emulator scale, then resamples between scales; these modes may cost extra. "
        "Requires an RTX GPU; the NVIDIA runtime downloads automatically on first use.",
        "NVIDIA DLSS", choices=[("off", "Off"), ("quality", "Quality"), ("balanced", "Balanced"),
                                ("dlss", "Performance")],
        platform="windows", restart=True)
    add("fh1_output_resolution", "Output resolution", "Display", "choice", "1440p",
        "The reconstructed picture and native UI resolution. Restart the game after changing it.",
        "NVIDIA DLSS", choices=[("1080p", "1920 x 1080"), ("1440p", "2560 x 1440"),
                                ("4k", "3840 x 2160 (4K)")], platform="windows", restart=True)

add("forza_antialiasing", "Anti-aliasing", "Display", "choice", "game",
    "Game default preserves guest MSAA. SMAA 1x is spatial and needs no NVIDIA runtime. "
    "DLAA and FSR Native AA are temporal at output resolution. A separate method is available "
    "only with upscaling off; filtering occurs before the native HUD.", "Picture",
    choices=[("game", "Game default (MSAA)"), ("smaa", "SMAA 1x"),
             *([("dlaa", "NVIDIA DLAA")] if IS_WINDOWS else []),
             ("fsr3_native", "FSR Native AA (TAA)")], restart=True)
add("swap_post_effect", "Anti-aliasing post effect", "Display", "choice", "none",
    "FXAA on the final image.", "Picture",
    choices=[("none", "None"), ("fxaa", "FXAA"), ("fxaa_extreme", "FXAA extreme")], restart=True)
add("present_letterbox", "Letterbox other aspect ratios", "Display", "bool", True,
    "Keep 16:9 with black bars instead of stretching.", "Picture")
add("present_cas_additional_sharpness", "CAS extra sharpness", "Display", "float", 0.0,
    "0 to 1, for the CAS filter.", "Picture", lo=0, hi=1, step=0.05)
add("present_dither", "Output dithering", "Display", "bool", False,
    "Dither the final image to hide banding.", "Picture", restart=True)
add("scene_msaa", "Scene anti-aliasing (MSAA)", "Display", "choice", "game",
    "The game draws the 3D scene with 4x MSAA in three parts, which costs the most CPU time on "
    "4-core PCs and the Steam Deck. 2x draws it in two parts with 2x MSAA, as the game does for "
    "its largest races. Off draws it in one pass without MSAA: about a fifth less CPU time in "
    "the GPU emulation and less GPU time, with more jagged edges.", "Picture",
    choices=[("game", "4x (game)"), ("2x", "2x"), ("off", "Off (fastest)")], restart=True)
add("native_2x_msaa", "Native 2x MSAA implementation", "Display", "bool", True,
    "Implement guest-requested 2x targets using native GPU multisampling. This does not force MSAA or choose the game's sample count.", "Picture")
add("cockpit_gauges_lit", "Cockpit gauges always lit", "Display", "bool", False,
    "Keep your car's instrument backlight on by day too, as the game shows it at night, so the "
    "gauges are easy to read in cockpit view. The headlights stay as they are, and other "
    "players' cars are not changed.", "Picture")
add("motion_blur", "Motion blur", "Display", "bool", True,
    "Blur moving scenery and wheels while driving fast. Off eliminates speed blur trails "
    "while keeping tone mapping, bloom and vignette untouched. Useful for high refresh rates "
    "or players prone to motion sickness. Also in the in-game Graphics screen, where it "
    "applies live.", "Picture")
if IS_WINDOWS:
    add("gpu_backend", "Graphics API", "Display", "choice", "vulkan",
        "Vulkan includes this project's fixes and is recommended; Direct3D 12 is the SDK's own and "
        "flickers on some NVIDIA cards (greenish reflections, issue #46).", "Picture",
        choices=[("vulkan", "Vulkan"), ("d3d12", "Direct3D 12")], platform="windows",
        restart=True)

add("forza_pause_on_focus_loss", "Pause when the window loses focus", "Controls", "bool", False,
    "Open the game's pause menu when you switch away while driving. Resume manually after returning. "
    "Multiplayer continues running while its pause menu is open.", "Auto-pause", restart=True)
add("forza_pause_on_controller_disconnect", "Pause when the controller disconnects", "Controls", "bool", False,
    "Open the game's pause menu when the physical controller or wheel you were using disconnects. "
    "Keyboard-only play and unplugging an idle extra controller do not trigger it. Resume manually.",
    "Auto-pause", restart=True)

add("world_shadow_detail", "Shadow detail", "Display", "choice", "console",
    "How far away small objects (trees on a hillside, posts, rocks, bushes) still cast a shadow. "
    "Console is the game's own range; High casts them 3 times as far, Ultra 10 times. A few "
    "hundred more shadow draws a frame; see docs/graphics-detail.md for the measured cost.",
    "World detail",
    choices=[("console", "Console"), ("high", "High"), ("ultra", "Ultra")], restart=True)
add("world_texture_detail", "Distant texture detail", "Display", "choice", "console",
    "Sharper terrain, road and scenery textures in the distance (the track's texture LOD bias: "
    "Console 0, High -0.5, Ultra -1). Best with 8x or 16x anisotropic filtering; Ultra can "
    "shimmer slightly on fine patterns.", "World detail",
    choices=[("console", "Console"), ("high", "High"), ("ultra", "Ultra")], restart=True)
add("world_shadow_filter", "Shadow softness", "Display", "choice", "console",
    "Softer, smoother edges on the shadows of trees, cars and scenery: Soft filters each shadow "
    "over a 1.5 times wider area, Softer twice as wide with a wider blur. Measured on an RX 9060 XT: "
    "no measurable increase for Soft, about 0.5 ms per frame for Softer at 2560x1440; other GPUs may differ. "
    "Console is the game's own filter. Also in the "
    "in-game Graphics screen, where it applies without a restart.", "World detail",
    choices=[("console", "Console"), ("soft", "Soft"), ("softer", "Softer")])
add("world_ambient_occlusion", "Ambient occlusion", "Display", "choice", "off",
    "Darker sunlight where surfaces meet: under cars, along curbs and rails, at the foot of walls "
    "and in creases of the ground (screen-space ambient occlusion on the world's sunlight; the cars "
    "and trees keep their own shading). Low or High strength, the same cost: about 0.5 ms "
    "per frame at 2560x1440 on an RX 9060 XT, nothing measurable under a 60 fps cap; other GPUs "
    "may differ. Off is the game's lighting. Vulkan only. Also in the in-game Graphics screen, "
    "where it applies without a restart.", "World detail",
    choices=[("off", "Off"), ("low", "Low"), ("high", "High")])
# Texture packs (#384, docs/texture_packs.md): SDK patch 0280's cvars; the launcher passes
# --texture_pack_root=<data folder>/textures (game.py), the folder its button opens.
add("texture_dump", "Dump textures", "Display", "bool", False,
    "Save each texture the game loads, once, to textures/dump in the game's data folder, named "
    "by its fingerprint: a .dds and a .png to look at. A replacement must have the same name. "
    "Takes disk space (about 200 MB for the first race) and a little time as textures "
    "load. The files are the game's art: never share them.", "Texture packs",
    restart=True)
add("texture_replace", "Use texture packs", "Display", "bool", False,
    "Draw textures from the .dds and .png files in the textures folder (and folders inside it) "
    "instead of the game's: higher resolution, same aspect ratio, named like the dumped "
    "texture. Uses graphics memory, not the game's. Vulkan only.", "Texture packs",
    restart=True)
# scRGB requires an HDR-enabled desktop and a surface advertising the format.
add("present_hdr", "HDR output", "Display", "bool", False,
    "Expand SDR scene highlights into an scRGB output. Requires Vulkan, an HDR display and HDR "
    "enabled in the OS. Unsupported displays keep SDR. This does not restore native HDR detail.",
    "HDR", restart=True)
add("present_hdr_paper_white_nits", "Paper white (nits)", "Display", "float", 200.0,
    "Brightness of white HUD elements and ordinary white. Start at 200 and adjust for your room.",
    "HDR", lo=80, hi=1000, step=10, restart=True)
add("present_hdr_peak_nits", "Display peak (nits)", "Display", "float", 1000.0,
    "Set to your display's HDR peak brightness. At or below paper white, highlight expansion is off.",
    "HDR", lo=80, hi=10000, step=10, restart=True)
add("present_hdr_highlights", "Highlight strength", "Display", "float", 0.5,
    "0 keeps the scene at paper white; 1 expands bright scene pixels toward the selected peak. "
    "HUD coverage stays at paper white. Translucent overlays may suppress highlights beneath them.",
    "HDR", lo=0, hi=1, step=0.05, restart=True)
add("present_hdr_gamma", "HDR source gamma", "Display", "float", 2.2,
    "2.2 matches the game's SDR curve. 0 uses the piecewise sRGB curve.",
    "HDR", lo=0, hi=3, step=0.1, restart=True)
add("present_hdr_compare", "Compare SDR / HDR", "Display", "bool", False,
    "Left: SDR at paper white. Right: HDR. Both halves use the HDR output, with a white divider.",
    "HDR", restart=True)
add("present_hdr_test_pattern", "HDR calibration", "Display", "choice", 0,
    "Show a calibration strip over the picture. Turn off when finished. "
    "An SDR screenshot cannot show the physical brightness.", "HDR",
    choices=[(0, "Off"), (1, "Near black"), (2, "Paper white"), (3, "Display peak")], restart=True)

# VR through OpenXR (forza-recomp/src/platform/xr.h, docs/vr.md, #333). Launcher-only: the values
# go to the game as arguments of "Play in VR" (vr.py), so Play always starts flat.
add("vr_mode", "Mode", "VR", "choice", "cockpit",
    "Cockpit puts you in the driver's seat and turns the in-car camera with your head. "
    "Big screen shows the game on a screen in front of you, in any camera.", "VR (experimental)",
    choices=[("cockpit", "Cockpit (recommended)"), ("screen", "Big screen")])
add("vr_picture", "Sharpness", "VR", "choice", "game",
    "How sharp the cockpit looks. Higher is clearer but needs a stronger graphics card; "
    "if the headset stutters, go back a step.", "VR (experimental)",
    choices=[("game", "Standard"), ("sharp", "Sharp (needs a stronger graphics card)"),
             ("sharpest", "Sharpest (needs a much stronger graphics card)")])
add("vr_seat_height_cm", "Seat height (cm)", "VR", "float", -4.0,
    "Raises or lowers your eye in the cockpit. Lower it if your head pokes through the roof.",
    "VR (experimental)", lo=-30, hi=30, step=1)

# --- Controls --------------------------------------------------------------------------
add("mnk_mode", "Keyboard and mouse", "Controls", "bool", False,
    "Keys stand in for a controller (the bindings below).", "Keyboard")
add("mnk_mouse", "Mouse moves the right stick", "Controls", "bool", False,
    "Look around (and aim the Photo Mode camera) with the mouse; the pointer is captured "
    "while the game has focus. Off: the right stick comes from its keys only.", "Keyboard")
add("mnk_sensitivity", "Mouse sensitivity", "Controls", "float", 1.0,
    "1.0 turns the right stick halfway at 1000 pixels a second.", "Keyboard",
    lo=0.01, hi=10, step=0.1)
add("mnk_stick_ramp", "Steering ramp (seconds)", "Controls", "float", 0.2,
    "How long a held key takes to push the left stick (steering) from its first "
    "deflection to full lock; letting go takes half as long. 0 is all or nothing.",
    "Keyboard", lo=0, hi=2, step=0.05)
add("mnk_passthrough", "Raw keyboard to the game", "Controls", "bool", False,
    "For titles that read a USB keyboard. Not needed for Forza.", "Keyboard")
add("vibration", "Controller vibration", "Controls", "bool", True, "", "Controller")
add("guide_button", "Guide button pass-through", "Controls", "bool", False, "", "Controller")
add("left_stick_deadzone_percentage", "Left stick deadzone", "Controls", "float", 0.0,
    "Fraction of the stick's range, 0 to 1.", "Controller", lo=0, hi=1, step=0.01)
add("right_stick_deadzone_percentage", "Right stick deadzone", "Controls", "float", 0.0,
    "Fraction of the stick's range, 0 to 1.", "Controller", lo=0, hi=1, step=0.01)
add("input_backend", "Input backend", "Controls", "choice", "sdl",
    "SDL works with any pad; XInput is Windows only.", "Controller",
    choices=[("sdl", "SDL"), ("xinput", "XInput")])
add("hid_mappings_file", "Controller mapping file", "Controls", "str", "gamecontrollerdb.txt",
    "SDL game controller mappings.", "Controller")
# Controller remapping (patch 0116, #279): the Controls page's Bindings write it.
add("controller_remap", "Controller bindings", "Controls", "str", "",
    "Set by Bindings on the Controls page. Profiles separated by ';', each the controller's SDL "
    "GUID (or * for every controller) and target=source pairs, e.g. *:rt=a,a=none (throttle on "
    "A). Sources: a button, lt or rt, a stick axis (lx, -lx inverted) or one direction of it "
    "(ly+ is up), @0.3 for how far an axis presses a button. SDL backend only.", "Controller",
    restart=True)

# Racing wheels (patch 0074): an SDL joystick that is a wheel is presented as the Xbox 360 Wireless
# Racing Wheel. SDL backend only.
add("wheel_mode", "Racing wheel", "Controls", "choice", "auto",
    "Auto: a controller SDL reports as a wheel (or named like one) is used as the 360 racing "
    "wheel. On: every controller without a gamepad mapping counts as a wheel.", "Racing wheel",
    choices=[("auto", "Auto"), ("on", "On"), ("off", "Off")], restart=True)
# The primary wheel (patch 0134, #306).
add("wheel_device", "Wheel device", "Controls", "str", "",
    "The wheel the first player drives with, and the one pedals, a shifter or a handbrake on their "
    "own USB device are read with. Empty picks one with force feedback first (a wheel base before "
    "a vJoy or button box); a device name as Bindings lists it, SDL GUID or vid:pid picks that "
    "one, and makes it a wheel. Use as the wheel saves the vid:pid, which the game reads "
    "whatever name Windows gives the device.", "Racing wheel", restart=True)
add("wheel_rotation", "Wheel rotation (degrees)", "Controls", "int", 900,
    "Lock to lock, as set in the wheel's own driver.", "Racing wheel", lo=90, hi=2520, step=10)
add("wheel_lock", "Steering lock (degrees)", "Controls", "int", 900,
    "Wheel travel, lock to lock, that gives the game's full steering. Below the rotation makes "
    "the steering quicker.", "Racing wheel", lo=90, hi=2520, step=10)
add("wheel_deadzone", "Steering deadzone", "Controls", "float", 0.0,
    "Fraction of the wheel's range, 0 to 0.5.", "Racing wheel", lo=0, hi=0.5, step=0.01)
add("wheel_steer_offset", "Steering deadzone compensation", "Controls", "float", 0.05,
    "Only with full-range wheel steering off: any wheel movement beyond a hair already gives this "
    "fraction of the full steering (0 to 0.3), to cancel an inner steering deadzone. Full-range "
    "steering follows the wheel from the centre without it.", "Racing wheel",
    lo=0, hi=0.3, step=0.01)
add("wheel_invert_steering", "Invert steering", "Controls", "bool", False, "", "Racing wheel")
add("wheel_900_steering", "Full-range wheel steering", "Controls", "bool", True,
    "The game uses its full-range (900\u00b0) wheel steering. Off uses the Xbox 360 wheel's "
    "270\u00b0 mode, which filters the steering like a gamepad.", "Racing wheel", restart=True)
add("wheel_pedals", "Pedals", "Controls", "choice", "auto",
    "Combined: one axis, throttle one way and brake the other (G25 style).", "Racing wheel",
    choices=[("auto", "Separate (detected)"), ("separate", "Separate"), ("combined", "Combined")])
add("wheel_invert_throttle", "Invert throttle", "Controls", "bool", False,
    "Turn on when the throttle works backwards.", "Racing wheel")
add("wheel_invert_brake", "Invert brake", "Controls", "bool", False,
    "Turn on when the brake works backwards.", "Racing wheel")
add("wheel_throttle_curve", "Throttle curve", "Controls", "float", 1.0,
    "A gamma: 1 is linear, above 1 is softer at the start.", "Racing wheel",
    lo=0.2, hi=4, step=0.1)
add("wheel_brake_curve", "Brake curve", "Controls", "float", 1.0,
    "A gamma: 1 is linear, above 1 is softer at the start.", "Racing wheel",
    lo=0.2, hi=4, step=0.1)
add("wheel_axes", "Axis numbers", "Controls", "str", "",
    "Overrides, e.g. steer=0,throttle=2,brake=5,clutch=1. Empty uses the built-in profile of a "
    "known Logitech wheel (G25, G27, G29, G920, G923, Driving Force GT), else detects them from "
    "where each axis rests when the wheel is plugged in (pedals released). handbrake=3 reads an "
    "analog handbrake plugged into the wheel (99: none).", "Racing wheel")
add("wheel_buttons", "Button numbers", "Controls", "str", "",
    "Overrides, e.g. a=0,b=1,x=2,y=3,lb=5,rb=4,back=8,start=9,ls=11,rs=10,up=12,down=13,"
    "left=14,right=15. The hat is the d-pad unless a direction is set here (-1 turns it off). "
    "The G29, G920 and G923 have their own maps "
    "built in. A button the shifter uses never presses one of these.",
    "Racing wheel")
# Pedals on their own USB device (patch 0128, #272): read with the wheel.
add("wheel_pedal_device", "Pedal device", "Controls", "str", "",
    "Pedals on their own USB device, read with the wheel. Empty finds known pedals (Simagic, "
    "Fanatec, Thrustmaster T-LCM, Heusinkveld, Moza, Logitech) or a device named like pedals; a "
    "device name as Bindings lists it, or its vid:pid, picks one; none reads the wheel's own "
    "pedals. Binding a pedal row by pressing the pedal sets it (as vid:pid).", "Racing wheel")
add("wheel_pedal_axes", "Pedal device axes", "Controls", "str", "",
    "Axis numbers on the pedal device, e.g. throttle=0,brake=1,clutch=2. Empty uses known pedals' "
    "layout, else detects them from where each axis rests when the game starts (pedals "
    "released).", "Racing wheel")
# An analog handbrake (patch 0133, #303): the game's 360 wheel handbrake axis, next to the B button.
add("wheel_handbrake_device", "Handbrake device", "Controls", "str", "",
    "An analog handbrake on its own USB device, read with the wheel. Empty finds known handbrakes "
    "(Simagic TB-1 and TB-RS, Fanatec ClubSport Handbrake, Thrustmaster TSS) or a device named "
    "like one; a device name as Bindings lists it, or its vid:pid, picks one (also the pedal or "
    "shifter device); none reads none. Binding the Handbrake row by pulling the handbrake sets "
    "it (as vid:pid).",
    "Racing wheel")
add("wheel_handbrake_axis", "Handbrake device axis", "Controls", "int", 0,
    "The handbrake's axis number on the handbrake device.", "Racing wheel", lo=0, hi=99)
add("wheel_invert_handbrake", "Invert handbrake", "Controls", "bool", False,
    "Turn on when the analog handbrake works backwards.", "Racing wheel")
add("wheel_ffb", "Force feedback", "Controls", "bool", True,
    "The game's own wheel forces: the car's steering force, a centring spring that grows with "
    "speed, a light damper, and its rumble as a vibration. They fade out while you are not "
    "driving (pause menu, rewind, replays).",
    "Racing wheel", restart=True)
add("wheel_ffb_reverse", "Reverse force direction", "Controls", "bool", False,
    "Turn on if the wheel pulls away from the centre in corners instead of towards it (wheel "
    "drivers disagree about the direction).", "Racing wheel")
add("wheel_ffb_strength", "Force feedback strength (%)", "Controls", "int", 60,
    "0 to 100.", "Racing wheel", lo=0, hi=100, step=5)
# A minimum steering force (patch 0131, #303).
add("wheel_ffb_min_strength", "Minimum force (%)", "Controls", "int", 0,
    "Gives a light car some weight in the wheel: the game's steering forces weaker than this share "
    "of the full force are scaled up (smoothly, up to 4 times near the centre), stronger ones stay "
    "as they are. 0 is off. Try 10 to 30. The centring spring and the rumble do not change.",
    "Racing wheel", lo=0, hi=100, step=5)
add("wheel_ffb_spring_strength", "Centring strength (%)", "Controls", "int", 100,
    "The game's centring spring and damper, 0 to 100 percent of the force feedback strength. "
    "Lower it if small turns feel heavy at speed: the steering force and the road feel stay.",
    "Racing wheel", lo=0, hi=100, step=5)
add("wheel_rumble_engine_strength", "Engine rumble (%)", "Controls", "int", 50,
    "The vibration from the game's engine motor (its right motor), 0 to 100 percent of the force "
    "feedback strength. The game runs it at half or more whenever the car accelerates, so it "
    "starts at 50; lower it if the engine drowns out the road.",
    "Racing wheel", lo=0, hi=100, step=5)
add("wheel_rumble_road_strength", "Road rumble (%)", "Controls", "int", 80,
    "The vibration from the road surface, kerbs and impacts (the game's left motor), 0 to 100 "
    "percent of the force feedback strength.", "Racing wheel", lo=0, hi=100, step=5)
# How often the vibration effects are updated (ReIGlue 0403 follow-up, #467); read on every update.
add("wheel_vibration_rate", "Wheel vibration updates per second", "Controls", "int", 60,
    "Lower it if your wheel's vibration feels too strong. 15 to 120.", "Racing wheel",
    lo=15, hi=120, step=5)
add("wheel_rumble_style", "Rumble style", "Controls", "choice", "fine",
    "How the game's rumble plays on the wheel's motor. Fine: the engine and the road each as a "
    "light, quick vibration (about 70 and 40 Hz). Classic: one strong, slower vibration, as the "
    "Xbox 360 wheel's rim motors felt; it shakes a force feedback wheel hard. A wheel without "
    "room for the fine effects plays classic.", "Racing wheel",
    choices=[("fine", "Fine"), ("classic", "Classic")], restart=True)
# Clutch and shifter (patch 0099): the game reads the 360 wheel's analog clutch and H-pattern shifter.
add("wheel_clutch", "Clutch pedal", "Controls", "bool", True,
    "The clutch pedal works as the game's clutch (choose Manual with clutch in the game's options).",
    "Shifter and clutch")
add("wheel_invert_clutch", "Invert clutch", "Controls", "bool", False,
    "Turn on when the clutch works backwards.", "Shifter and clutch")
add("wheel_shifter", "Shifter", "Controls", "choice", "off",
    "H-pattern: the shifter's gear selects the gear (the game's manual transmission; no gear "
    "is neutral). Sequential: its up and down buttons shift like the paddles.",
    "Shifter and clutch",
    choices=[("off", "None"), ("h", "H-pattern"), ("sequential", "Sequential")], restart=True)
add("wheel_shifter_buttons", "Shifter buttons", "Controls", "str", "",
    "e.g. 1=12,2=13,3=14,4=15,5=16,6=17,r=18 (gears 1 to 7 and reverse) or up=..,down=.. "
    "(sequential). Empty uses the Logitech Driving Force Shifter on a G29, G920 or G923: gears "
    "on 12 to 17, reverse on 18 (G29, PlayStation G923) or 11 (G920, Xbox G923). These buttons "
    "press nothing else.",
    "Shifter and clutch", restart=True)
add("wheel_shifter_clutch", "Gears need the clutch", "Controls", "bool", False,
    "H-pattern: a gear engages only with the clutch pressed; without it the car stays in neutral.",
    "Shifter and clutch")
add("wheel_clutch_threshold", "Clutch threshold", "Controls", "float", 0.5,
    "How far the clutch must be pressed to engage a gear, 0.1 to 1.", "Shifter and clutch",
    lo=0.1, hi=1, step=0.05)
# H-pattern: the lever's slot, or gears on lever moves (patch 0132, #303).
add("wheel_shifter_follow_slot", "Gear follows the lever's slot", "Controls", "bool", True,
    "H-pattern. On (default, as on the Xbox): the game takes the gear the lever sits in whenever it "
    "sets the gear itself, at race and event starts and finishes. Off, for switching to the "
    "paddles: a gear engages only when the lever moves into a slot or out to neutral, so the "
    "paddles keep the gear they chose and a race start keeps the game's.", "Shifter and clutch")
# A shifter on its own USB device (patch 0128, #272): read with the wheel.
add("wheel_shifter_device", "Shifter device", "Controls", "str", "",
    "A shifter on its own USB device, read with the wheel. Empty finds known shifters (Simagic "
    "DS-8X, Fanatec ClubSport SQ, Thrustmaster TH8 RS, Moza HGP, Heusinkveld, Logitech) or a "
    "device named like one (a TH8A); a device name as Bindings lists it, or its vid:pid, picks "
    "one; none reads the wheel's own buttons. Binding a gear row by pressing it on the shifter "
    "sets it (as vid:pid).",
    "Shifter and clutch", restart=True)
add("wheel_shifter_device_buttons", "Shifter device buttons", "Controls", "str", "",
    "Button numbers on the shifter device, e.g. 1=0,2=1,3=2,4=3,5=4,6=5,r=6 or up=..,down=.. "
    "Empty uses a known shifter's layout.", "Shifter and clutch", restart=True)

KEYBINDS = [
    ("a", "A", "Semicolon,Space"), ("b", "B", "Quote,Backspace"), ("x", "X", "L"),
    ("y", "Y", "P"), ("left_trigger", "Left trigger", "Q,I"),
    ("right_trigger", "Right trigger", "E,O"), ("left_shoulder", "Left shoulder", "1"),
    ("right_shoulder", "Right shoulder", "3"), ("lstick_up", "Left stick up", "W"),
    ("lstick_down", "Left stick down", "S"), ("lstick_left", "Left stick left", "A"),
    ("lstick_right", "Left stick right", "D"), ("lstick_press", "Left stick press", "F"),
    ("rstick_up", "Right stick up", "Up"), ("rstick_down", "Right stick down", "Down"),
    ("rstick_left", "Right stick left", "Left"), ("rstick_right", "Right stick right", "Right"),
    ("rstick_press", "Right stick press", "K"), ("dpad_up", "D-pad up", "Shift+Up"),
    ("dpad_down", "D-pad down", "Shift+Down"), ("dpad_left", "D-pad left", "Shift+Left"),
    ("dpad_right", "D-pad right", "Shift+Right"), ("back", "Back", "Z,Tab"),
    ("start", "Start", "X,Return"), ("guide", "Guide", ""),
]
for _k, _label, _default in KEYBINDS:
    add("keybind_" + _k, _label, "Controls", "keys", _default,
        "Key names (A, Space, Return, Up, Semicolon, F5, Numpad1, LMB for the left mouse "
        "button, ...), comma separated for alternatives, Shift+Up for a combination.",
        "Keyboard bindings (used when keyboard and mouse is on)")

# --- Gameplay --------------------------------------------------------------------------
add("ai_rubber_banding", "Rubber banding", "Gameplay", "choice", "game",
    "Game: as the game shipped, a CPU driver ahead of you eases off (the weaker ones down to "
    "a fifth of their power) and one behind you brakes and corners better to catch up. Off: "
    "every CPU driver races at its own skill wherever you are, so the faster car pulls away "
    "on the straights and the better-handling one in the corners; the difficulty still sets "
    "how good they are. Single-player races and free roam only: online races and Co-op "
    "Festival rounds always play as shipped.",
    "CPU drivers", choices=[("game", "Game"), ("off", "Off")])

add("forza_car_tuning", "Native car tuning", "Gameplay", "bool", False,
    "Add Car Tuning to the stock pause menu for your owned car in Colorado free roam. "
    "Requires Settings > Online > Sign in to Xbox LIVE: off, including when playing solo while signed in to Forza Live. "
    "Supports named setups "
    "and tune file export/import. Requires FH1 Title Update 4 resources prepared "
    "by the launcher during a build. Applies on the next game launch.", "Car tuning", restart=True)

add("custom_race", "Custom Race", "Gameplay", "bool", False,
    "Replay a festival or street race you have already won with your own settings: press "
    "Select on the event's title card to choose the car class, laps (circuits), opponents "
    "(1-7) and time of day. Rewards are paid as usual. Offline only: requires Settings > "
    "Online > Sign in to Xbox LIVE: off. Title Update 4 only. Applies on the next game launch.",
    "Custom Race", restart=True)

add("skip_startup_movies", "Skip startup videos", "Gameplay", "bool", False,
    "Skips the videos at the start (Microsoft Studios, Dolby, Playground Games, about 45 s) "
    "and goes to the title screen. The in-game videos (the festival intro, the 1000 Club "
    "introduction) still play. Applies on the next game launch.", "Startup", restart=True)

# --- Audio -----------------------------------------------------------------------------
add("audio_mute", "Mute", "Audio", "bool", False, "", "Output")
add("audio_maxqframes", "Audio buffer (frames)", "Audio", "int", 8,
    "4 to 64. Lower is less delay but may crackle.", "Output", lo=4, hi=64)
add("radio_station_switch", "Station changes", "Audio", "choice", "game",
    "Game: original radio HUD delay and 0.5 s fades. Fast: 0.15 s repeat guard and 0.25 s fades. "
    "Instant: 0.075 s repeat guard and 0.04 s fades. Fast and Instant accept the latest station "
    "during a fade. Applies to stock and custom stations. Stream startup still adds latency.",
    "Radio", choices=[("game", "Game"), ("fast", "Fast"), ("instant", "Instant")])


add("radio_keep_in_events", "Keep radio during races", "Audio", "bool", False,
    "Keep your selected stock or custom station, including Radio Off, through qualified "
    "career and Rally race-opening music cues. Story cinematics, post-race music, dialogue "
    "and sound effects keep their native behavior. Loading/finish fades still apply.",
    "Radio", restart=True)

# --- Online ----------------------------------------------------------------------------
add("live_signin", "Sign in to Xbox LIVE", "Online", "bool", True,
    "Through the Forza Live server: friends and LIVE lobbies.", "Forza Live")
add("live_room", "Room", "Online", "str", "",
    "Only players in the same room on the server see each other. Empty is the default room.",
    "Forza Live")
add("live_accept_invites", "Show game invites", "Online", "bool", True,
    "Another player's invite asks you: hold Back + A (or Enter) to join. Off ignores them.",
    "Forza Live")
add("live_friends_key", "Friends screen key", "Online", "str", "F8",
    "Opens the friends screen (a controller: hold the left stick press, unless the "
    "controller shortcut below is off).", "Forza Live")
add("live_friends_pad_shortcut", "Controller friends shortcut", "Online", "bool", True,
    "Holding the left stick press opens the friends screen. Turn off if you hold the left stick "
    "while driving; the friends and chat keys still work.", "Forza Live")
add("live_chat_key", "Chat key", "Online", "str", "F9",
    "Opens the chat (a controller: the friends screen's Chat tab).", "Forza Live")
add("world_drop_in", "Horizon World drop-in", "Online", "bool", True,
    "The Horizon World playlist is a shared free roam that players jump into.",
    "Horizon World")
add("world_events", "Horizon World events", "Online", "bool", True,
    "The world's host takes the events the server starts.", "Horizon World")
add("world_challenges", "Head-to-head challenges", "Online", "bool", True,
    "Challenge another player's car in the world.", "Horizon World")
add("forza_online_photo", "Online photo", "Online", "bool", False,
    "ONLINE PHOTO in the pause menu of Horizon World free roam and online lobbies: the game's "
    "photo camera while the session keeps running. Other cars keep moving; yours takes no input "
    "until you leave. A photo is the frame on screen when you take it (no depth of field, no "
    "Big Shot) and is saved like any other photo. Title Update 4.", "Horizon World")
add("world_event_wait_seconds", "Event lobby wait (s)", "Online", "int", 30,
    "How long the wait lobby before an event lasts.", "Horizon World", lo=0, hi=600)
add("world_event_cpu_with_peers", "AI drivers in multi-player world events", "Online", "bool",
    True, "Horizon World events and the Co-op Festival's /event races (also alone on This PC); "
    "playlist and custom races have no AI drivers, as on the Xbox 360. Off: only a solo event "
    "gets them.", "Horizon World")
add("world_event_cpu_ready", "Do not wait for AI after events", "Online", "bool", True,
    "The results screen returns to the lobby without waiting for them.", "Horizon World")
add("world_event_ai_grid", "AI difficulty grid size (test)", "Online", "int", 0,
    "Test knob: 0 uses the event's grid.", "Horizon World", lo=0, hi=16)

# --- Profile ---------------------------------------------------------------------------
add("gamertag", "Gamertag", "Profile", "str", "",
    "Letters, digits and spaces only, up to 15 characters. Empty uses the one kept in live_identity.txt (your login name).",
    "Identity", max_length=15)
add("game_language", "Game language", "Profile", "choice", "",
    "The language of the game's menus and text. Automatic uses the Language ID and Country ID "
    "below (English, United States by default). Any other choice sets both for you; Hungarian, "
    "Czech, Dutch, Danish, Finnish, Norwegian and Swedish pick English with their country, as "
    "the game does. A language that has no Country ID of its own uses yours.", "Identity",
    choices=languages.CHOICES, restart=True)
add("user_language", "Language ID", "Profile", "int", 1,
    "Xbox language code: 1 English, 2 Japanese, 3 German, 4 French, 5 Spanish, 6 Italian, "
    "7 Korean, 8 Chinese (Traditional), 9 Portuguese, 10 Chinese (Simplified), 11 Polish, 12 Russian. "
    "Used when Game language is Automatic.",
    "Identity", lo=0, hi=20)
add("user_country", "Country ID", "Profile", "int", 103, "Xbox country code.", "Identity",
    lo=0, hi=300)
add("license_mask", "DLC license mask", "Profile", "int", 0,
    "Licence mask for activated content.", "Identity", lo=0, hi=2147483647)

# --- Advanced --------------------------------------------------------------------------
add("forza_native_pause", "Native pause menu (experimental)", "Advanced", "bool", False,
    "Use Home, Cars, Social and Settings pages. Requires FH1 Title Update 4 and menu resources "
    "prepared by the launcher during a build. The original pause menu remains available "
    "if these resources are missing. Applies on the next game launch.", "Menu", restart=True)
add("log_level", "Log level", "Advanced", "choice", "info",
    "Keep info unless a bug report asks for more: trace and debug write millions of lines and "
    "lower the frame rate a lot (#174).", "Log",
    choices=_c("trace", "debug", "info", "warn", "error", "critical", "off"))
add("log_noisy", "Noisy logging", "Advanced", "bool", False,
    "High-frequency log messages.", "Log")
add("log_flush_interval", "Log flush interval (s)", "Advanced", "int", 0,
    "0 flushes at exit only.", "Log", lo=0, hi=60, restart=True)
add("vulkan_device", "Vulkan device", "Advanced", "int", -1,
    "-1 picks the best GPU. Use another index on multi-GPU systems.", "GPU", lo=-1, hi=16,
    restart=True)
add("vulkan_allow_present_mode_immediate", "Allow tearing present mode", "Advanced", "bool",
    False, "No vsync wait. Spins the GPU; leave off.", "GPU")
add("d3d12_allow_variable_refresh_rate_and_tearing", "Allow tearing (Direct3D 12)", "Advanced", "bool",
    False, "Variable refresh rate (G-Sync / FreeSync) on Direct3D 12. Tears on a fixed-refresh "
    "display; leave off unless yours has VRR.", "GPU", platform="windows")
add("vulkan_allow_present_mode_mailbox", "Allow mailbox present mode", "Advanced", "bool",
    False, "Triple buffering.", "GPU")
add("vulkan_allow_present_mode_fifo_relaxed", "Allow relaxed FIFO", "Advanced", "bool", False,
    "", "GPU")
add("vulkan_allow_present_mode_fifo_latest_ready", "Use FIFO latest ready", "Advanced", "bool",
    True, "Vsync that shows the newest finished frame when the game draws slower than the "
    "refresh rate, if the driver offers it. Off: plain FIFO.", "GPU")
add("store_shaders", "Keep a shader cache", "Advanced", "bool", True,
    "Fewer stutters the second time you play.", "GPU")
add("vulkan_store_pipeline_cache", "Keep compiled Vulkan pipelines", "Advanced", "bool", True,
    "Reuse local GPU driver data when preparing cached shaders at startup. Requires the shader "
    "cache and Vulkan. New scenes may still compile shaders; driver changes rebuild the cache.", "GPU", restart=True)
add("async_shader_compilation", "Compile shaders in the background", "Advanced", "bool", True,
    "", "GPU")
add("gpu_wait_blocking", "Sleep while waiting for the GPU (testing)", "Advanced", "bool", False,
    "The render thread sleeps until the GPU is done instead of keeping a CPU core busy, which "
    "leaves more CPU and power to the game on laptops. On by default on Linux; not tested on "
    "Windows yet: turn it on to try it, and report any freeze (GitHub issue #45).", "GPU",
    platform="windows", restart=True)
add("present_safe_area_x", "Safe area width %", "Advanced", "int", 90, "", "GPU",
    lo=0, hi=100)
add("present_safe_area_y", "Safe area height %", "Advanced", "int", 90, "", "GPU",
    lo=0, hi=100)
add("video_mode_width", "Guest video width", "Advanced", "int", 1280,
    "Overridden by the resolution preset.", "GPU", lo=640, hi=4095, restart=True)
add("video_mode_height", "Guest video height", "Advanced", "int", 720,
    "Overridden by the resolution preset.", "GPU", lo=480, hi=4095, restart=True)

# Launcher only (never written to forza.toml): see shader_share.py, #276.
add("share_shader_list", "Share shader list", "Online", "bool", True,
    "After a game session, send the shader hashes and render settings from your pipeline cache to the "
    "server you play on (the public server or your own), once per entry. No game data, file paths or "
    "gamertag are sent, only pipeline descriptions: which shaders were used and how. The request carries "
    "your player ID and a signature made with your signing key (not the key itself) so that the server "
    "counts each player once. The server publishes "
    "the entries several players reported, and Prebuild shaders builds that list from your own game files, "
    "so new places and cars stop compiling shaders while you drive. Nothing is sent offline or to a "
    "server on this PC.", "Shader prebuild")
# Launcher only: see coop_grant.py, #309.
add("coop_results", "Co-op campaign results", "Online", "bool", True,
    "After a game session, ask the server you play on for your co-op campaign results and add the "
    "ones you earned to the save you played with. The request carries your player ID and a signature "
    "made with your signing key (not the key itself). "
    "Nothing is asked offline or of a server on this PC.", "Co-op campaign")
# Launcher only: see challenges.py, forza-live #1.
add("challenges", "Daily and weekly challenges", "Online", "bool", True,
    "Show the main server's daily and weekly challenges and your progress on the Play page. The "
    "launcher asks the main server for them when the Play page is open (at most every 5 minutes) "
    "and after a game session, only with Server set to Public server. The request carries your "
    "player ID and a signature made with your signing key (not the key itself).", "Challenges"),
# Discord status display is on by default; the "Online features" choice (PRIVACY.md) and this
# switch turn it off. The application ID is public maintainer setup, separated from the
# ordinary sharing switch. Never request a token or secret.
add("discord_presence", "Share activity on Discord", "Online", "bool", True,
    "Show your activity in the Discord desktop app: free roam, racing, shops and online lobbies. "
    "Only when the Discord desktop app runs; Discord's own activity privacy settings apply. "
    "Applies on the next game launch.", "Discord",
    restart=True)
add("discord_presence_details", "Share car, event and area", "Online", "bool", True,
    "Also show your car with its class and PI, the event you race in and the area of Colorado you "
    "drive in, with the names the game uses. Off: only the activity, as before. "
    "Applies on the next game launch.", "Discord",
    restart=True)
add("discord_application_id", "Discord application ID", "Advanced", "str", "1563015276583460934",
    "The public Application ID Discord shows as the game (the project's own, set by default). "
    "Change it only to use another Discord application; empty disables the connection. "
    "Never enter a bot token or client secret.", "Discord setup",
    restart=True)

BY_KEY = {s.key: s for s in S}


def for_group(group):
    return [s for s in S if s.group == group and s.applies()]


def defaults():
    return {s.key: s.default for s in S}


def coerce(setting, value):
    """Make a value (from JSON, TOML or a widget) fit the setting's type, or the default."""
    try:
        if setting.key == "gamertag":
            return gamertag.clean_name(value)
        if setting.kind == "bool":
            return bool(value)
        if setting.kind in ("int", "fps"):
            v = int(float(value))
            if setting.kind == "fps":    # 0 = Match display, resolved by the launcher at launch
                if v == 0:
                    return 0
                if v < 60:    # below 60 the game runs 20, 30 or 40: the highest not above v
                    return max([r for r in LOW_FRAME_RATES if r <= v], default=LOW_FRAME_RATES[0])
                return min(v, 240)
            lo = -(10 ** 9) if setting.lo is None else int(setting.lo)
            hi = 10 ** 9 if setting.hi is None else int(setting.hi)
            return max(lo, min(hi, v))
        if setting.kind == "float":
            v = float(value)
            if not math.isfinite(v):
                return setting.default
            lo = -1e9 if setting.lo is None else setting.lo
            hi = 1e9 if setting.hi is None else setting.hi
            return max(lo, min(hi, v))
        if setting.kind == "choice" and setting.choices:
            value = setting.legacy.get(value, value)
            values = [c[0] for c in setting.choices]
            if value in values:
                return value
            for c in values:
                if str(c) == str(value):
                    return c
            return setting.default
        text = str(value)
        return text[:setting.max_length] if setting.max_length is not None else text
    except (TypeError, ValueError):
        return setting.default
