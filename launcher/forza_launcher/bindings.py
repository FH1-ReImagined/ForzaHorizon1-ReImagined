"""Press-to-bind controls (#279): the bindings the Controls page edits, free of Qt and SDL.

Wheels keep the SDK wheel driver's settings and formats (patches 0074, 0099, 0101):
`wheel_axes` ("steer=0,throttle=2,...", and "handbrake=3" for an analog handbrake, patch 0133),
`wheel_buttons` ("a=0,b=1,..."), `wheel_shifter_buttons` ("1=12,...,r=18" or "up=..,down=.."),
plus the rotation, deadzone and invert switches. A field the
string leaves out keeps the driver's default, which this module ports (known Logitech wheels by
vendor:product) so the editor can show it.

Controllers use `controller_remap` (patch 0116): profiles separated by ';', each
"GUID:target=source,..." where GUID is SDL's joystick GUID (or '*' for every controller without a
profile of its own). A target is a pad control of the game's XInput state (a b x y lb rb back start
guide ls rs up down left right lt rt lx ly rx ry); a target left out keeps its own input. Sources:
a button (the targets' buttons and misc1 paddle1-4 touchpad), a trigger (lt rt), a stick axis
(lx ly rx ry, '-lx' inverted) or one direction of it (lx+ is right, ly+ is up), or none. '@0.3' after
an axis sets how far it must move to press a button (default 0.5). A button bound to a trigger
presses it fully. The SDK's parser (src/input/sdl/gamepad_remap.h) follows the same rules.
"""
from dataclasses import dataclass
import re

# --- key=value strings -------------------------------------------------------------------------
_ATOI = re.compile(r"\s*([+-]?\d+)")


def parse_kv(text):
    """"a=0,b=1" to {"a": 0, "b": 1}, as the SDK's ParseKv: keys lower case without spaces, the
    value read like atoi (0 when it is not a number); items without '=' are skipped."""
    out = {}
    for item in str(text or "").split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = "".join(key.split()).lower()
        m = _ATOI.match(value)
        out[key] = int(m.group(1)) if m else 0
    return out


def format_kv(values, order=()):
    """{"steer": 0, "throttle": 2} to "steer=0,throttle=2": known keys in `order`, others after."""
    keys = [k for k in order if k in values] + sorted(k for k in values if k not in order)
    return ",".join(f"{k}={int(values[k])}" for k in keys)


# --- actions -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Action:
    key: str      # row id
    label: str
    kind: str     # wheel: axis, button; controller: button, trigger, stick
    setting: str  # the forza.toml key it is stored in
    field: str    # its key inside that setting's string
    section: str


# The game takes a wheel for the Xbox 360 wheel and applies its wheel layout 0 (gamedb.slt
# WheelLayouts): the bumpers shift, A is the clutch button, B the handbrake, X Activate, Y Rewind,
# Back the camera.
_W = "wheel_buttons"
WHEEL_ACTIONS = [
    Action("steer", "Steering", "axis", "wheel_axes", "steer", "Wheel and pedals"),
    Action("throttle", "Throttle", "axis", "wheel_axes", "throttle", "Wheel and pedals"),
    Action("brake", "Brake", "axis", "wheel_axes", "brake", "Wheel and pedals"),
    Action("clutch", "Clutch pedal", "axis", "wheel_axes", "clutch", "Wheel and pedals"),
    Action("rb", "Shift up (RB, right paddle)", "button", _W, "rb", "Buttons"),
    Action("lb", "Shift down (LB, left paddle)", "button", _W, "lb", "Buttons"),
    Action("a", "Clutch button (A)", "button", _W, "a", "Buttons"),
    # A button (B) and an analog axis, both at once (#303): see the handbrake section below.
    Action("b", "Handbrake (B, or an axis)", "button", _W, "b", "Buttons"),
    Action("x", "Activate / enter event (X)", "button", _W, "x", "Buttons"),
    Action("y", "Rewind (Y)", "button", _W, "y", "Buttons"),
    Action("back", "Change camera (Back)", "button", _W, "back", "Buttons"),
    Action("start", "Pause (Start)", "button", _W, "start", "Buttons"),
    Action("rs", "Right stick press (RS)", "button", _W, "rs", "Buttons"),
    Action("ls", "Left stick press (LS)", "button", _W, "ls", "Buttons"),
    Action("up", "D-pad up", "button", _W, "up", "Buttons"),
    Action("down", "D-pad down", "button", _W, "down", "Buttons"),
    Action("left", "D-pad left", "button", _W, "left", "Buttons"),
    Action("right", "D-pad right", "button", _W, "right", "Buttons"),
    *[Action(f"gear{g}", f"Gear {g}", "button", "wheel_shifter_buttons", str(g), "Shifter")
      for g in range(1, 8)],
    Action("gearr", "Reverse gear", "button", "wheel_shifter_buttons", "r", "Shifter"),
    Action("seq_up", "Sequential shift up", "button", "wheel_shifter_buttons", "up", "Shifter"),
    Action("seq_down", "Sequential shift down", "button", "wheel_shifter_buttons", "down", "Shifter"),
]

_C = "controller_remap"
CONTROLLER_ACTIONS = [
    Action("lx", "Steer", "stick", _C, "lx", "Driving"),
    Action("rt", "Accelerate", "trigger", _C, "rt", "Driving"),
    Action("lt", "Brake / reverse", "trigger", _C, "lt", "Driving"),
    Action("a", "Handbrake / select", "button", _C, "a", "Driving"),
    Action("b", "Back / shift up", "button", _C, "b", "Driving"),
    Action("x", "Enter event / shift down", "button", _C, "x", "Driving"),
    Action("y", "Rewind", "button", _C, "y", "Driving"),
    Action("rb", "Change camera", "button", _C, "rb", "Driving"),
    Action("lb", "Left bumper", "button", _C, "lb", "Driving"),
    Action("rs", "Look back (right stick press)", "button", _C, "rs", "Driving"),
    Action("back", "Open map", "button", _C, "back", "Menus and camera"),
    Action("start", "Pause", "button", _C, "start", "Menus and camera"),
    Action("ls", "Left stick press (friends)", "button", _C, "ls", "Menus and camera"),
    Action("ly", "Left stick up / down (menus)", "stick", _C, "ly", "Menus and camera"),
    Action("rx", "Look left / right", "stick", _C, "rx", "Menus and camera"),
    Action("ry", "Look up / down", "stick", _C, "ry", "Menus and camera"),
    Action("up", "D-pad up (radio)", "button", _C, "up", "Menus and camera"),
    Action("down", "D-pad down (radio)", "button", _C, "down", "Menus and camera"),
    Action("left", "D-pad left (radio)", "button", _C, "left", "Menus and camera"),
    Action("right", "D-pad right (radio)", "button", _C, "right", "Menus and camera"),
]

AXIS_ORDER = ("steer", "throttle", "brake", "clutch", "handbrake")
BUTTON_ORDER = ("a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs", "up", "down", "left", "right")
SHIFTER_ORDER = ("1", "2", "3", "4", "5", "6", "7", "r", "up", "down")
# An axis number past every wheel's axes: the driver reads no axis for it. Negative numbers mean
# "the default" to the driver (the profile or detection), so they cannot unbind an axis.
NO_AXIS = 99

# --- wheel defaults, as the driver has them --------------------------------------------------
_LOGITECH = 0x046D
KNOWN_WHEELS = {0xC24F: "Logitech G29", 0xC262: "Logitech G920", 0xC266: "Logitech G923",
                0xC26E: "Logitech G923 (Xbox)", 0xC29A: "Logitech Driving Force GT",
                0xC29B: "Logitech G27", 0xC299: "Logitech G25"}
_PLAYSTATION = {0xC24F, 0xC266, 0xC267}
_XBOX = {0xC262, 0xC26E, 0xC26D}
WHEEL_WORDS = ("wheel", "racing", "driving force", "g25", "g27", "g29", "g920", "g923", "t300",
               "t150", "t248", "t-gt", "tx racing", "fanatec", "moza", "simagic", "csl", "podium",
               "ts-xw")


# Thrustmaster wheels (patch 0135, #308): the SDK's kThrustmasterModels (src/input/wheel/
# wheel_thrustmaster.h; test_wheel_thrustmaster.py checks they match), from Project Motor Racing's
# device maps (Pitwall PMR/shared/inputDevices, commit 01acf41). The axes hold only with the axis
# count the map assumes; where the pedals rest is read from the wheel.
@dataclass(frozen=True)
class ThrustmasterModel:
    pid: int
    name: str
    axes: int
    steer: int
    throttle: int
    brake: int
    clutch: int
    a: int
    b: int
    x: int
    y: int
    lb: int
    rb: int
    back: int
    start: int
    ls: int
    rs: int


_THRUSTMASTER = 0x044F
THRUSTMASTER_MODELS = [
    ThrustmasterModel(0xB67F, "Thrustmaster TMX", 4, 0, 2, 1, 3, 4, 8, 2, 9, 0, 1, 6, 7, 5, 10),
    ThrustmasterModel(0xB669, "Thrustmaster TX", 4, 0, 2, 1, 3, 4, 8, 2, 9, 0, 1, 3, 5, -1, -1),
    ThrustmasterModel(0xB692, "Thrustmaster TS-XW", 4, 0, 2, 1, 3, 12, 8, 2, 9, 0, 1, 6, 7, -1, -1),
    ThrustmasterModel(0xB689, "Thrustmaster TS-PC Racer", 4, 0, 2, 1, 3, 12, 8, 2, 9, 0, 1, 6, 7,
                      -1, -1),
    ThrustmasterModel(0xB66E, "Thrustmaster T300 RS", 4, 0, 2, 1, 3, 5, 4, 3, 2, 0, 1, 6, 7, 10, 11),
    ThrustmasterModel(0xB677, "Thrustmaster T150", 4, 0, 2, 1, 3, 5, 4, 3, 2, 0, 1, 6, 7, 10, 11),
    ThrustmasterModel(0xB684, "Thrustmaster T-GT", 8, 0, 5, 1, 6, 5, 4, 3, 2, 0, 1, 6, 7, 10, 11),
]


def thrustmaster(vid, pid):
    if vid != _THRUSTMASTER:
        return None
    return next((m for m in THRUSTMASTER_MODELS if m.pid == pid), None)


def known_logitech(vid, pid):
    """A Logitech wheel with a built-in profile: fixed axes, pedals that rest at the maximum."""
    return KNOWN_WHEELS.get(pid) if vid == _LOGITECH else None


def known_wheel(vid, pid):
    m = thrustmaster(vid, pid)
    return known_logitech(vid, pid) or (m.name if m else None)


def looks_like_wheel(name, vid=0, sdl_wheel=False, pid=0):
    """The driver's wheel_mode = auto test (IsWheelDevice): Microsoft pads never are; a known
    Thrustmaster always is (a TMX has no name on Windows: "44F B67F")."""
    if vid == 0x045E:
        return False
    return bool(sdl_wheel) or any(w in (name or "").lower() for w in WHEEL_WORDS) or \
        thrustmaster(vid, pid) is not None


def default_axes(vid, pid, windows, num_axes=None):
    """A known wheel's fixed axes; {} for others (the driver detects them when it opens the wheel).
    A Thrustmaster's hold only with the axis count its map has (num_axes, when known)."""
    m = thrustmaster(vid, pid)
    if m:
        if num_axes and num_axes != m.axes:
            return {}
        return {"steer": m.steer, "throttle": m.throttle, "brake": m.brake, "clutch": m.clutch}
    if not known_logitech(vid, pid):
        return {}
    if windows:
        return {"steer": 0, "throttle": 1, "brake": 2, "clutch": 3}
    return {"steer": 0, "clutch": 1, "throttle": 2, "brake": 3}


def default_buttons(vid, pid):
    m = {"a": 0, "b": 1, "x": 2, "y": 3, "rb": 4, "lb": 5, "back": 8, "start": 9, "rs": 10,
         "ls": 11, "up": -1, "down": -1, "left": -1, "right": -1}
    t = thrustmaster(vid, pid)
    if t:
        m.update({k: getattr(t, k) for k in ("a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs")})
    elif vid == _LOGITECH and pid in _PLAYSTATION:
        m.update(b=2, x=1)
    elif vid == _LOGITECH and pid in _XBOX:
        m.update(start=6, back=7, rs=8, ls=9)
    return m


def default_shifter(vid, pid):
    m = {k: -1 for k in SHIFTER_ORDER}
    if vid == _LOGITECH and (pid in _PLAYSTATION or pid in _XBOX):
        m.update({str(g): 11 + g for g in range(1, 7)})
        m["r"] = 18 if pid in _PLAYSTATION else 11
    return m


@dataclass(frozen=True)
class WheelInfo:
    vid: int = 0
    pid: int = 0
    num_axes: int = 0
    num_buttons: int = 0
    num_hats: int = 0
    windows: bool = False


def wheel_binding(action, settings, wheel):
    """(number or None, is_default) for a wheel row. None: no input (or one detected at start)."""
    own = parse_kv(settings.get(action.setting, "")).get(action.field)
    if action.setting == "wheel_axes":
        if own is not None and own >= 0:
            return (None if own >= NO_AXIS else own), False
        return default_axes(wheel.vid, wheel.pid, wheel.windows, wheel.num_axes).get(action.field), True
    if action.setting == "wheel_buttons":
        if own is not None:
            return (own if own >= 0 else None), False
        n = default_buttons(wheel.vid, wheel.pid)[action.field]
        return (n if n >= 0 else None), True
    if own is not None:
        return (own if own >= 0 else None), False
    n = default_shifter(wheel.vid, wheel.pid)[action.field]
    return (n if n >= 0 else None), True


def combined_pedals(settings):
    """One axis for both pedals: the driver reads throttle and brake from the `throttle` axis, one
    on each side, and ignores `brake`."""
    return settings.get("wheel_pedals") == "combined"


def wheel_action_label(action, settings):
    if action.key == "throttle" and combined_pedals(settings):
        return "Throttle and brake (combined pedals)"
    return action.label


def wheel_action_editable(action, settings):
    """With combined pedals the brake is the throttle axis's other side: it has no binding of its
    own to bind or clear (the throttle row does both)."""
    return not (action.key == "brake" and combined_pedals(settings))


def set_wheel_binding(settings, action, number):
    """The settings changes ({key: new string}) that bind `action` to `number`; None clears it and
    "default" removes the field so the driver's default applies again."""
    values = parse_kv(settings.get(action.setting, ""))
    if number == "default":
        values.pop(action.field, None)
    elif number is None:
        values[action.field] = NO_AXIS if action.kind == "axis" else -1
    else:
        values[action.field] = int(number)
    order = {"wheel_axes": AXIS_ORDER, "wheel_buttons": BUTTON_ORDER}.get(action.setting, SHIFTER_ORDER)
    return {action.setting: format_kv(values, order)}


def wheel_conflicts(settings, wheel, aux=None):
    """[(input text, [row labels])] for every axis or button of one device that more than one row
    uses. Shifter rows count only for the shifter mode that reads them, the brake not with combined
    pedals."""
    shifter = settings.get("wheel_shifter", "off")
    used = {}
    for action in WHEEL_ACTIONS:
        if not wheel_action_editable(action, settings):
            continue
        if action.setting == "wheel_shifter_buttons":
            sequential = action.field in ("up", "down")
            if shifter == "off" or (shifter == "h") == sequential:
                continue
        if is_handbrake_row(action):
            axis, device, _ = handbrake_axis(settings, aux)
            if axis is not None:
                where = f" on {device.name}" if device else ""
                used.setdefault((where, "axis", axis), []).append(wheel_action_label(action, settings))
        device = row_device(action, settings, aux)
        n, _ = aux_binding(action, settings, device) if device else \
            wheel_binding(action, settings, wheel)
        if n is None:
            continue
        where = f" on {device.name}" if device else ""
        used.setdefault((where, action.kind, n), []).append(wheel_action_label(action, settings))
    return [(f"{'Axis' if kind == 'axis' else 'Button'} {n}{where}", labels)
            for (where, kind, n), labels in sorted(used.items()) if len(labels) > 1]


# --- pedals and shifters on their own USB device (#272, patch 0128) ---------------------------
# The driver reads a pedal set and a shifter on their own USB devices with the first wheel:
# `wheel_pedal_device` / `wheel_shifter_device` pick them ("" finds a known one or one named like
# one, "none" reads the wheel's own, anything else names one: its name, SDL GUID or vid:pid), with
# `wheel_pedal_axes` ("throttle=2,brake=1,clutch=0") and `wheel_shifter_device_buttons`
# ("1=0,...,r=8"). A pedal set in wheel_pedal_axes reads the pedal device, one set in wheel_axes
# the wheel, others the pedal device while one is connected. While a shifter device is connected
# every gear comes from it. This ports src/input/wheel/wheel_devices.h.
PEDAL_KEYS = ("throttle", "brake", "clutch")


@dataclass(frozen=True)
class AuxModel:
    vid: int
    pid: int
    role: str  # "pedals", "shifter", "other" (a handbrake: no wheel of its own)
    name: str
    axes: int = 0  # pedals: the layout below holds with exactly this many axes
    throttle: int = -1
    brake: int = -1
    clutch: int = -1
    first_gear: int = -1  # shifter: button of gear 1, the next gears on the next buttons
    gears: int = 0
    reverse: int = -1
    up: int = -1
    down: int = -1


_P, _S, _O = "pedals", "shifter", "other"
# The SDK's kAuxModels (test_wheel_devices.py checks they match). Sources: docs/sdk_patches.md 0128.
AUX_MODELS = [
    AuxModel(0x0483, 0x0525, _P, "Simagic P1000", 3, 2, 1, 0),
    AuxModel(0x0483, 0x0524, _P, "Simagic P2000", 3, 2, 1, 0),
    AuxModel(0x3670, 0x0902, _P, "Simagic P2000 (haptic)", 3, 2, 1, 0),
    AuxModel(0x3670, 0x0903, _P, "Simagic P500"),
    AuxModel(0x3670, 0x0905, _P, "Simagic P700"),
    AuxModel(0x0483, 0x0531, _S, "Simagic DS-8X", first_gear=0, gears=7, reverse=8, up=10, down=9),
    AuxModel(0x0483, 0x0530, _O, "Simagic TB-1 handbrake"),
    AuxModel(0x3670, 0x0A04, _O, "Simagic TB-RS handbrake"),
    AuxModel(0x0EB7, 0x6204, _P, "Fanatec CSL Elite Pedals LC", 3, 0, 1, 2),
    AuxModel(0x0EB7, 0x6205, _P, "Fanatec CSL Pedals"),
    AuxModel(0x0EB7, 0x6206, _P, "Fanatec CSL Elite Pedals LC V2", 3, 0, 1, 2),
    AuxModel(0x0EB7, 0x183B, _P, "Fanatec ClubSport Pedals V3", 3, 0, 1, 2),
    AuxModel(0x0EB7, 0x1A92, _S, "Fanatec ClubSport Shifter SQ", first_gear=1, gears=7, reverse=0,
             up=9, down=8),
    AuxModel(0x0EB7, 0x1A93, _O, "Fanatec ClubSport Handbrake"),
    AuxModel(0x044F, 0xB371, _P, "Thrustmaster T-LCM", 3, 0, 2, 1),
    AuxModel(0x044F, 0xB660, _S, "Thrustmaster TH8 RS", first_gear=0, gears=7, reverse=7, up=9,
             down=8),
    AuxModel(0x044F, 0xB695, _O, "Thrustmaster TSS handbrake"),
    AuxModel(0x046D, 0xC26A, _P, "Logitech G PRO Racing Pedals", 3, 0, 1, 2),
    AuxModel(0x046D, 0xC277, _P, "Logitech RS Pedals"),
    AuxModel(0x046D, 0xC278, _S, "Logitech RS Shifter and Handbrake"),
    AuxModel(0x046D, 0xC273, _S, "Logitech Driving Force Shifter (USB adapter)", first_gear=0,
             gears=6, reverse=6),
    AuxModel(0x30B7, 0x1001, _P, "Heusinkveld Sprint", 3, 0, 1, 2),
    AuxModel(0x30B7, 0x1003, _P, "Heusinkveld Ultimate", 3, 0, 1, 2),
    AuxModel(0x30B7, 0x1004, _P, "Heusinkveld Race Center", 3, 0, 1, 2),
    AuxModel(0x10C4, 0x8B02, _P, "Heusinkveld Pedals (DC)", 3, 0, 1, 2),
    AuxModel(0xA020, 0x3142, _S, "Heusinkveld Sequential Shifter"),
    AuxModel(0x30B7, 0x1005, _S, "Heusinkveld Shifter"),
    AuxModel(0x30B7, 0x1006, _S, "Heusinkveld Shifter"),
    AuxModel(0x346E, 0x0003, _P, "Moza SR-P", 3, 0, 1, 2),
    AuxModel(0x346E, 0x0001, _P, "Moza CRP", 3, 0, 1, 2),
    AuxModel(0x346E, 0x0008, _P, "Moza mBooster", 3, 0, 1, 2),
    AuxModel(0x346E, 0x0013, _P, "Moza mBooster", 3, 0, 1, 2),
    AuxModel(0x346E, 0x001E, _S, "Moza HGP", first_gear=5, gears=7, reverse=4),
    AuxModel(0x346E, 0x0023, _S, "Moza SGP"),
]


def find_aux_model(vid, pid):
    return next((m for m in AUX_MODELS if m.vid == vid and m.pid == pid), None)


def _squash(text):
    return " ".join(str(text or "").lower().split())


def aux_role_by_name(name):
    """What a device's name says it is (the driver's AuxRoleByName); None: nothing."""
    n = _squash(name)
    if "wheel" in n:
        return None
    if "pedal" in n:
        return _P
    if any(w in n for w in ("shifter", "th8a", "th8 rs", "gear shift")):
        return _S
    if "handbrake" in n or "hand brake" in n:
        return _O
    return None


def detect_aux_role(vid, pid, name):
    m = find_aux_model(vid, pid)
    return m.role if m else aux_role_by_name(name)


def device_choice(setting):
    s = _squash(setting)
    if s in ("", "auto"):
        return "auto"
    return "none" if s in ("none", "off", "wheel") else "named"


def device_match(setting, device):
    """2: the setting is the device's name, GUID or vid:pid; 1: part of its name; 0: neither."""
    s = _squash(setting)
    if not s:
        return 0
    # SDL2's vJoy name differs from SDL3's Windows HID product name (#306).
    # Preserve existing saved bindings, restricted to the device's reported USB id.
    if s == "vjoy device" and (device.vid, device.pid) == (0x1234, 0xBEAD):
        return 2
    if s in (_squash(device.name), _squash(device.guid), f"{device.vid:04x}:{device.pid:04x}"):
        return 2
    return 1 if s in _squash(device.name) else 0


# --- how the settings name a device (#562) ---------------------------------------------------
# The launcher's SDL 2 and the game's SDL 3 can read different names for the same Windows device: a
# Thrustmaster wheel or pedal set is "Thrustmaster TX Racing Wheel" here (the product name Windows
# registers) and "44F B669" in the game, which gets no name for it. The device settings
# (`wheel_device` and the pedal, shifter and handbrake devices) are therefore saved by the device's
# USB ids, "0483:0525", which both read the same and the driver matches exactly; a device without
# ids, or one that shares them with another listed device, is saved by its name. A setting saved by
# name (a hand-written one, or by an earlier launcher) keeps working where the names agree; Bindings
# rewrites one that names a connected device exactly (migrate_device_settings), and the driver
# takes a nameless known device for a name that matches nothing.
DEVICE_KEYS = ("wheel_device", "wheel_pedal_device", "wheel_shifter_device",
               "wheel_handbrake_device")
_USB_ID = re.compile(r"^([0-9a-f]{4}):([0-9a-f]{4})$")


def usb_id(text):
    """(vid, pid) for a setting written as "0483:0525", else None."""
    m = _USB_ID.match(_squash(text))
    return (int(m[1], 16), int(m[2], 16)) if m else None


def device_ref(device, devices=()):
    """What the device settings save for `device` (a DeviceInfo): its USB ids, or its name when it
    has none or another of `devices` has the same."""
    if device.vid or device.pid:
        shared = any(d.instance != device.instance and (d.vid, d.pid) == (device.vid, device.pid)
                     for d in devices)
        if not shared:
            return f"{device.vid:04x}:{device.pid:04x}"
    return device.name


def known_device_name(vid, pid):
    """The name of a device the launcher knows by its USB ids, else None."""
    m = find_aux_model(vid, pid)
    return m.name if m else known_wheel(vid, pid)


def device_label(setting, fallback=""):
    """A device setting as the Controls page names the device while it is not connected: a name
    stays, "0483:0525" becomes the known model's name."""
    text = str(setting or "").strip()
    ids = usb_id(text)
    if not ids:
        return text or fallback
    return known_device_name(*ids) or f"USB device {text.lower()}"


def migrate_device_settings(settings, devices):
    """{setting: "vid:pid"} for the device settings that name a connected device by exactly its
    name: the same device, saved so that the game finds it whatever it calls it. Partial names,
    GUIDs, ids and names no connected device has stay as they are."""
    out = {}
    for key in DEVICE_KEYS:
        value = settings.get(key, "")
        if device_choice(value) != "named" or usb_id(value):
            continue
        refs = {device_ref(d, devices) for d in devices if _squash(d.name) == _squash(value)}
        if len(refs) == 1:
            ref = refs.pop()
            if _squash(ref) != _squash(value):
                out[key] = ref
    return out


@dataclass(frozen=True)
class AuxPick:
    pedals: int | None = None   # index into the device list
    shifter: int | None = None
    other: tuple = ()

    def is_aux(self, i):
        return i in (self.pedals, self.shifter) or i in self.other


def pick_aux(devices, settings):
    """The pedal device and the shifter device among `devices` (DeviceInfo, SDL order), as the
    driver picks them (PickAuxDevices)."""
    def named(setting):
        best, score = None, 0
        for i, d in enumerate(devices):
            m = device_match(setting, d)
            s = m * 2 + (0 if d.sdl_wheel else 1) if m else 0
            if s > score:
                best, score = i, s
        return best
    pedal_setting = settings.get("wheel_pedal_device", "")
    shifter_setting = settings.get("wheel_shifter_device", "")
    pc, sc = device_choice(pedal_setting), device_choice(shifter_setting)
    pedals = named(pedal_setting) if pc == "named" else None
    shifter = named(shifter_setting) if sc == "named" else None
    other = []
    for i, d in enumerate(devices):
        role = detect_aux_role(d.vid, d.pid, d.name)
        if role == _P and pc == "auto" and pedals is None:
            pedals = i
        elif role == _S and sc == "auto" and shifter is None:
            shifter = i
        elif role and i not in (pedals, shifter):
            other.append(i)
    return AuxPick(pedals, shifter, tuple(other))


# --- which device is the wheel (#306, patch 0134) -------------------------------------------
# Every wheel the driver takes is a controller of its own; the first is the first player's and the
# one pedal, shifter and handbrake devices are read with. `wheel_device` names it (a name, GUID or
# vid:pid; it then is a wheel even when it does not look like one); otherwise the driver puts wheels
# with force feedback first, then those SDL lists as wheels, then SDL's order. A pedal, shifter or
# handbrake device is no wheel unless wheel_device names it. This ports
# src/input/wheel/wheel_primary.h; the launcher does not see force feedback without opening every
# device, so `ffb` is optional (the game's log names the wheel it took: "Wheel: ... is the wheel").
def wheel_device_match(settings, device):
    setting = settings.get("wheel_device", "")
    return device_match(setting, device) if device_choice(setting) == "named" else 0


def wheel_like(device, settings):
    """The driver's IsWheelDevice for one device, without the pedal and shifter settings."""
    mode = settings.get("wheel_mode", "auto")
    if mode == "off":
        return False
    return looks_like_wheel(device.name, device.vid, device.sdl_wheel, device.pid) or \
        (mode == "on" and not device.gamepad) or wheel_device_match(settings, device) > 0


def wheel_order(devices, settings, ffb=None):
    """Indices of the devices the driver takes as wheels, the wheel first (WheelOrder)."""
    if settings.get("wheel_mode", "auto") == "off":
        return []
    pick = pick_aux(devices, settings)
    handbrake = pick_handbrake(devices, settings)
    ffb = ffb or {}
    out = [i for i, d in enumerate(devices)
           if wheel_device_match(settings, d) or
           (wheel_like(d, settings) and not pick.is_aux(i) and i != handbrake)]
    def key(i):
        d = devices[i]
        return (wheel_device_match(settings, d) * 4 + (2 if ffb.get(d.instance) else 0)
                + (1 if d.sdl_wheel else 0))
    return sorted(out, key=lambda i: -key(i))


def primary_wheel(devices, settings, ffb=None):
    """The index of the wheel the first player drives with, or None."""
    order = wheel_order(devices, settings, ffb)
    return order[0] if order else None


@dataclass(frozen=True)
class AuxInfo:
    """A connected pedal or shifter device as the bindings show it."""
    name: str
    vid: int = 0
    pid: int = 0
    num_axes: int = 0
    num_buttons: int = 0


def model_pedal_axis(info, field):
    """A known pedal set's axis for `field` when it has the axes its layout assumes, else None."""
    m = find_aux_model(info.vid, info.pid) if info else None
    if not m or m.role != _P or not m.axes or info.num_axes != m.axes:
        return None
    return {"throttle": m.throttle, "brake": m.brake, "clutch": m.clutch}[field]


def model_shifter(info):
    """A known shifter's buttons, as default_shifter gives a wheel's."""
    out = {k: -1 for k in SHIFTER_ORDER}
    m = find_aux_model(info.vid, info.pid) if info else None
    if m and m.role == _S and m.first_gear >= 0:
        for g in range(1, min(m.gears, 7) + 1):
            out[str(g)] = m.first_gear + g - 1
        out.update(r=m.reverse, up=m.up, down=m.down)
    return out


def is_pedal_row(action):
    return action.setting == "wheel_axes" and action.field in PEDAL_KEYS


def is_gear_row(action):
    return action.setting == "wheel_shifter_buttons"


def row_device(action, settings, aux):
    """The device a wheel row reads: None for the wheel, else aux["pedals"] / aux["shifter"] (an
    AuxInfo, or one named after the setting while that device is not connected)."""
    aux = aux or {}
    if is_pedal_row(action):
        own = parse_kv(settings.get("wheel_pedal_axes", "")).get(action.field, -1)
        wheel = parse_kv(settings.get("wheel_axes", "")).get(action.field, -1)
        if own >= 0:
            return aux.get("pedals") or AuxInfo(device_label(settings.get("wheel_pedal_device"),
                                                             "the pedal device"))
        if wheel < 0 and aux.get("pedals"):
            return aux["pedals"]
    elif is_gear_row(action) and aux.get("shifter"):
        return aux["shifter"]
    return None


def aux_binding(action, settings, device):
    """(number or None, is_default) for a row read on a pedal or shifter device."""
    if is_pedal_row(action):
        own = parse_kv(settings.get("wheel_pedal_axes", "")).get(action.field, -1)
        if own >= 0:
            return (None if own >= NO_AXIS else own), False
        return model_pedal_axis(device, action.field), True
    own = parse_kv(settings.get("wheel_shifter_device_buttons", "")).get(action.field)
    if own is not None:
        return (own if own >= 0 else None), False
    n = model_shifter(device)[action.field]
    return (n if n >= 0 else None), True


def wheel_binding_text(action, settings, wheel, aux=None):
    if is_handbrake_row(action):
        return handbrake_text(settings, wheel, aux)
    if not wheel_action_editable(action, settings):
        return "The throttle axis's other side (combined pedals)"
    device = row_device(action, settings, aux)
    if device is not None:
        n, default = aux_binding(action, settings, device)
    else:
        n, default = wheel_binding(action, settings, wheel)
    if action.kind == "axis":
        if n is None:
            text = "Detected at start" if default else "Not bound"
        else:
            text = f"Axis {n}"
    elif n is None:
        # A d-pad direction set in wheel_buttons replaces the hat's (patch 0127): only the
        # default reads the hat, and -1 turns the direction off.
        text = "Hat (d-pad)" if action.field in ("up", "down", "left", "right") and default \
            and wheel.num_hats and device is None else "Not bound"
    else:
        text = f"Button {n}"
    if device is not None:
        text += f" on {device.name}"
    return text + (" (default)" if default and n is not None else "")


def set_aux_binding(settings, action, device_name, number, alias=""):
    """The settings changes that bind a pedal or gear row on its own device `device_name` (what the
    settings save for it: device_ref; `alias` is the device's name, which a setting made by name
    also means): it becomes the pedal or shifter device, and bindings made on another one are
    dropped. None clears the row there, "default" goes back to the device's default."""
    pedal = is_pedal_row(action)
    device_key = "wheel_pedal_device" if pedal else "wheel_shifter_device"
    key = "wheel_pedal_axes" if pedal else "wheel_shifter_device_buttons"
    values = parse_kv(settings.get(key, ""))
    out = {}
    same = {_squash(device_name), _squash(alias)} - {""}
    if device_name is not None and _squash(settings.get(device_key, "")) not in same:
        values = {}  # those were another device's numbers
        out[device_key] = device_name
    if number == "default":
        values.pop(action.field, None)
    elif number is None:
        values[action.field] = NO_AXIS if pedal else -1
    else:
        values[action.field] = int(number)
    out[key] = format_kv(values, PEDAL_KEYS if pedal else SHIFTER_ORDER)
    if pedal:  # the wheel's own number for it would only confuse
        axes = parse_kv(settings.get("wheel_axes", ""))
        if action.field in axes:
            axes.pop(action.field)
            out["wheel_axes"] = format_kv(axes, AXIS_ORDER)
    return out


def set_row_on_wheel(settings, action, number, aux=None):
    """Binding (or clearing, or resetting) a row on the wheel itself. A pedal row leaves the pedal
    device; a gear row while a shifter device is connected reads the whole shifter from the wheel
    again (wheel_shifter_device = none)."""
    out = set_wheel_binding(settings, action, number)
    if is_pedal_row(action):
        values = parse_kv(settings.get("wheel_pedal_axes", ""))
        if action.field in values:
            values.pop(action.field)
            out["wheel_pedal_axes"] = format_kv(values, PEDAL_KEYS)
    elif is_gear_row(action) and (aux or {}).get("shifter") and number != "default":
        out["wheel_shifter_device"] = "none"
    return out


def reset_row(settings, action):
    """A row back to the driver's default: off the wheel's and the device's settings."""
    if is_handbrake_row(action):
        return reset_handbrake(settings)
    out = set_wheel_binding(settings, action, "default")
    other = {"wheel_axes": "wheel_pedal_axes",
             "wheel_shifter_buttons": "wheel_shifter_device_buttons"}.get(action.setting)
    if other and (is_pedal_row(action) or is_gear_row(action)):
        values = parse_kv(settings.get(other, ""))
        if action.field in values:
            values.pop(action.field)
            out[other] = format_kv(values, PEDAL_KEYS if other == "wheel_pedal_axes" else SHIFTER_ORDER)
    return out


RESET_ALL_WHEEL = {"wheel_axes": "", "wheel_buttons": "", "wheel_shifter_buttons": "",
                   "wheel_pedal_device": "", "wheel_pedal_axes": "", "wheel_shifter_device": "",
                   "wheel_shifter_device_buttons": "", "wheel_handbrake_device": "",
                   "wheel_handbrake_axis": 0, "wheel_invert_handbrake": False}


# --- the handbrake: a button and an analog axis (#303, patch 0133) ----------------------------
# FH1's wheel layouts bind the Handbrake to B and to the left stick up and down, the Xbox 360
# wheel's analog handbrake, and the game takes the stronger of them. The driver sends an axis
# there: one on the wheel (`wheel_axes` handbrake=N, a handbrake plugged into the wheel base) or on
# a handbrake device (`wheel_handbrake_device`: "" finds a known handbrake or one named like one, a
# name, GUID or vid:pid picks one, which may also be the pedal or shifter device, "none" none; its
# axis `wheel_handbrake_axis`). One set in wheel_axes wins; 99 there is "not bound" and turns a
# handbrake device off too. The Handbrake row binds the button and the axis: binding one keeps the
# other. This ports src/input/wheel/wheel_handbrake.h.
def is_handbrake_row(action):
    return action.setting == "wheel_buttons" and action.field == "b"


def pick_handbrake(devices, settings):
    """The index of the handbrake device among `devices` (DeviceInfo, SDL order), as the driver
    picks it (PickHandbrakeDevice); None for none."""
    setting = settings.get("wheel_handbrake_device", "")
    choice = device_choice(setting)
    if choice == "none":
        return None
    if choice == "named":
        best, score = None, 0
        for i, d in enumerate(devices):
            m = device_match(setting, d)
            s = m * 2 + (0 if d.sdl_wheel else 1) if m else 0
            if s > score:
                best, score = i, s
        return best
    return next((i for i, d in enumerate(devices) if detect_aux_role(d.vid, d.pid, d.name) == _O), None)


def handbrake_axis(settings, aux=None):
    """(axis number or None, the device it is on (an AuxInfo) or None for the wheel, is_default)."""
    own = parse_kv(settings.get("wheel_axes", "")).get("handbrake", -1)
    if own >= 0:
        return (None if own >= NO_AXIS else own), None, False
    device = (aux or {}).get("handbrake")
    if device is None:
        return None, None, True
    try:
        axis = int(settings.get("wheel_handbrake_axis", 0) or 0)
    except (TypeError, ValueError):
        axis = 0
    named = device_choice(settings.get("wheel_handbrake_device", "")) == "named"
    return (axis if 0 <= axis < NO_AXIS else None), device, not named and axis == 0


def handbrake_text(settings, wheel, aux=None):
    """The Handbrake row's value: its button and its axis, e.g. "Button 1 (default) + Axis 0 on
    SIMAGIC TB-RS Handbrake"."""
    button, default = wheel_binding(next(a for a in WHEEL_ACTIONS if is_handbrake_row(a)),
                                    settings, wheel)
    out = []
    if button is not None:
        out.append(f"Button {button}" + (" (default)" if default else ""))
    axis, device, axis_default = handbrake_axis(settings, aux)
    if axis is not None:
        out.append(f"Axis {axis}" + (f" on {device.name}" if device else "")
                   + (" (default)" if axis_default else ""))
    return " + ".join(out) or "Not bound"


def set_handbrake_binding(settings, kind, number, device_name=""):
    """The settings changes binding the Handbrake row's button (kind "button", on the wheel) or its
    axis (kind "axis", on the wheel, or on the device `device_name`, which becomes the handbrake
    device); the other input stays."""
    if kind == "button":
        return set_wheel_binding(settings, next(a for a in WHEEL_ACTIONS if is_handbrake_row(a)),
                                 number)
    axes = parse_kv(settings.get("wheel_axes", ""))
    out = {}
    if device_name:
        axes.pop("handbrake", None)
        out["wheel_handbrake_device"] = device_name
        out["wheel_handbrake_axis"] = int(number)
    else:
        axes["handbrake"] = int(number)
    out["wheel_axes"] = format_kv(axes, AXIS_ORDER)
    return out


def clear_handbrake(settings):
    """No handbrake input at all: no button, and the axis "not bound" (a handbrake device too)."""
    out = set_handbrake_binding(settings, "button", None)
    axes = parse_kv(settings.get("wheel_axes", ""))
    axes["handbrake"] = NO_AXIS
    out["wheel_axes"] = format_kv(axes, AXIS_ORDER)
    return out


def reset_handbrake(settings):
    """The Handbrake row back to the driver's defaults: the wheel's B button and a handbrake device
    found by itself."""
    out = set_handbrake_binding(settings, "button", "default")
    axes = parse_kv(settings.get("wheel_axes", ""))
    axes.pop("handbrake", None)
    out["wheel_axes"] = format_kv(axes, AXIS_ORDER)
    out.update(wheel_handbrake_device="", wheel_handbrake_axis=0, wheel_invert_handbrake=False)
    return out


@dataclass(frozen=True)
class Part:
    """One device in a merged snapshot: its axes and buttons follow the previous parts'. `ref` is
    what the device settings save for it (device_ref); empty: its name."""
    instance: int
    name: str
    axes: int
    buttons: int
    ref: str = ""

    @property
    def setting(self):
        return self.ref or self.name

    def matches(self, setting):
        """Whether a device setting already names this device (by ids or by name)."""
        return bool(_squash(setting)) and _squash(setting) in (_squash(self.setting), _squash(self.name))


def merge(parts, snaps):
    """One Snapshot of several devices (each part's snap, in order); hats are the first's."""
    axes, buttons = [], []
    for part, snap in zip(parts, snaps):
        axes += list(snap.axes[:part.axes]) + [0] * max(0, part.axes - len(snap.axes))
        buttons += list(snap.buttons[:part.buttons]) + [False] * max(0, part.buttons - len(snap.buttons))
    return Snapshot(tuple(axes), tuple(buttons), snaps[0].hats if snaps else ())


def split(parts, kind, number):
    """(part index, the number on that device) for a merged axis or button number."""
    for i, part in enumerate(parts):
        count = part.axes if kind == "axis" else part.buttons
        if number < count:
            return i, number
        number -= count
    return None, None


# --- the live view (#306) ---------------------------------------------------------------------
# The Controls page graphs one device. Pedals, a shifter or a handbrake on their own device are
# read with the wheel (the same choice as the game: pick_aux, pick_handbrake, PlanPedals), and a
# pedal moving has to show on that row or it looks unbound. With none of those devices contributing,
# the view stays the wheel's own axes and buttons.
_LIVE_AXIS = {"throttle": "Throttle", "brake": "Brake", "clutch": "Clutch"}
_LIVE_GEAR = {**{str(g): str(g) for g in range(1, 8)}, "r": "R", "up": "Up", "down": "Down"}
_GEAR_KEY = {**{str(g): f"gear{g}" for g in range(1, 8)}, "r": "gearr", "up": "seq_up",
             "down": "seq_down"}
_LIVE_FIELDS = {"h": [str(g) for g in range(1, 8)] + ["r"], "sequential": ["up", "down"]}


def _snap_axis(snap, number):
    if snap is None or number is None or number < 0 or number >= len(snap.axes):
        return 0
    return snap.axes[number]


def _snap_button(snap, number):
    if snap is None or number is None or number < 0 or number >= len(snap.buttons):
        return False
    return bool(snap.buttons[number])


def _pedal_from_device(settings, field):
    """Whether the game reads this pedal from the pedal device while one is connected (PlanPedals):
    a wheel_pedal_axes number, otherwise the device unless wheel_axes gives the pedal."""
    pedal = parse_kv(settings.get("wheel_pedal_axes", "")).get(field, -1)
    wheel = parse_kv(settings.get("wheel_axes", "")).get(field, -1)
    return pedal >= 0 or wheel < 0


@dataclass(frozen=True)
class LiveInputs:
    """Axis bars `(label, raw)` and button lamps `(label, down)` for a wheel that also reads a
    pedal, shifter or handbrake device. The wheel's own axes and buttons come first; hats are the
    wheel's."""
    axes: tuple
    buttons: tuple
    hats: tuple = ()


def live_inputs(settings, wheel_snap, aux=None, snaps=None):
    """The Controls page's live view of a wheel, with its auxiliary devices combined in.

    `aux` is {"pedals": AuxInfo, "shifter": AuxInfo, "handbrake": AuxInfo} for the devices the
    game reads with the wheel (leave a role out when it is not connected). `snaps` is the same
    keys to Snapshots. None when nothing is read from those devices, so the view stays the wheel's
    own axes and buttons. Otherwise those, then Throttle, Brake, Clutch, Handbrake and the
    shifter's gears, from the device the game uses for each."""
    aux, snaps = aux or {}, snaps or {}
    if wheel_snap is None:
        wheel_snap = Snapshot()
    actions = {a.key: a for a in WHEEL_ACTIONS}
    axes = [(f"Axis {n}", raw) for n, raw in enumerate(wheel_snap.axes)]
    buttons = [(str(n), bool(down)) for n, down in enumerate(wheel_snap.buttons)]
    added = False

    device = aux.get("pedals")
    if device is not None:
        snap = snaps.get("pedals")
        combined = combined_pedals(settings)
        named, unmapped = [], False
        for field in PEDAL_KEYS:
            if combined and field == "brake":
                continue  # combined pedals: brake is the throttle axis's other side
            if not _pedal_from_device(settings, field):
                continue
            number, default = aux_binding(actions[field], settings, device)
            if number is not None:
                named.append((_LIVE_AXIS[field], number))
            elif default:
                unmapped = True  # the game detects this pedal when it starts
        if unmapped:
            labels = {}
            for label, number in named:
                labels.setdefault(number, label)
            count = len(snap.axes) if snap is not None else 0
            if named:
                count = max(count, max(number for _, number in named) + 1)
            for i in range(count):
                axes.append((labels.get(i, f"Axis {i}"), _snap_axis(snap, i)))
            added = added or count > 0
        elif named:
            axes.extend((label, _snap_axis(snap, number)) for label, number in named)
            added = True

    if "handbrake" in aux:
        axis, where, _ = handbrake_axis(settings, aux)
        if where is not None and axis is not None:
            axes.append(("Handbrake", _snap_axis(snaps.get("handbrake"), axis)))
            added = True

    device = aux.get("shifter")
    fields = _LIVE_FIELDS.get(settings.get("wheel_shifter", "off"))
    if device is not None and fields:
        snap = snaps.get("shifter")
        model = find_aux_model(device.vid, device.pid)
        known = model is not None and model.role == _S and model.first_gear >= 0
        mapped = False
        for field in fields:
            number, _default = aux_binding(actions[_GEAR_KEY[field]], settings, device)
            if number is None:
                continue
            buttons.append((_LIVE_GEAR[field], _snap_button(snap, number)))
            mapped = True
        if mapped:
            added = True
        elif not known and snap is not None and snap.buttons:
            # No gear map yet: show the shifter's buttons so a press is visible.
            buttons.extend((str(i), bool(down)) for i, down in enumerate(snap.buttons))
            added = True

    if not added:
        return None
    return LiveInputs(tuple(axes), tuple(buttons), tuple(wheel_snap.hats))


# --- controller remap --------------------------------------------------------------------------
PAD_BUTTONS =("a", "b", "x", "y", "back", "guide", "start", "ls", "rs", "lb", "rb", "up", "down",
               "left", "right")
EXTRA_BUTTONS = ("misc1", "paddle1", "paddle2", "paddle3", "paddle4", "touchpad")
STICK_AXES = ("lx", "ly", "rx", "ry")
TRIGGERS = ("lt", "rt")
TARGETS = PAD_BUTTONS + TRIGGERS + STICK_AXES
DEFAULT_THRESHOLD = 0.5
_SOURCE = re.compile(r"^(-?)([a-z0-9]+)([+-]?)(?:@([0-9.]+))?$")


@dataclass(frozen=True)
class Source:
    """One input of the pad: a button, a whole axis (sign -1 inverts it) or one direction of a
    stick axis (half +1 or -1); threshold: how far an axis must move to press a button."""
    name: str
    half: int = 0
    invert: bool = False
    threshold: float = DEFAULT_THRESHOLD

    @property
    def is_button(self):
        return self.name in PAD_BUTTONS or self.name in EXTRA_BUTTONS

    @property
    def token(self):
        if self.name == "none":
            return "none"
        t = ("-" if self.invert else "") + self.name + {1: "+", -1: "-"}.get(self.half, "")
        if not self.is_button and abs(self.threshold - DEFAULT_THRESHOLD) > 1e-6:
            t += f"@{self.threshold:g}"
        return t

    def directions(self):
        """The physical input and the directions of it this source reads, for conflicts."""
        if self.is_button or self.name in TRIGGERS:
            return self.name, {1}
        return self.name, ({self.half} if self.half else {1, -1})


NONE = Source("none")


def parse_source(token):
    m = _SOURCE.match("".join((token or "").split()).lower())
    if not m:
        return None
    invert, name, half, threshold = m.groups()
    if name == "none" or name in PAD_BUTTONS or name in EXTRA_BUTTONS:
        if invert or half or threshold:
            return None
        return NONE if name == "none" else Source(name)
    if name not in STICK_AXES and name not in TRIGGERS:
        return None
    if name in TRIGGERS and (invert or half):
        return None
    if invert and half:
        return None
    t = DEFAULT_THRESHOLD
    if threshold:
        try:
            t = min(0.95, max(0.05, float(threshold)))
        except ValueError:
            return None
    return Source(name, {"+": 1, "-": -1}.get(half, 0), bool(invert), t)


def accepts(target, source):
    """Whether the SDK applies `source` to `target` (unsupported pairs are ignored there)."""
    if source is NONE or source.name == "none":
        return True
    if target in STICK_AXES:
        return source.name in STICK_AXES and not source.half
    if target in TRIGGERS:
        return source.is_button or source.name in TRIGGERS or (source.name in STICK_AXES and not source.invert)
    return target in PAD_BUTTONS and (source.is_button or not source.invert)


def parse_rules(text):
    """"rt=a,a=none" to {"rt": Source("a"), "a": NONE}; unknown or unsupported pairs are dropped."""
    rules = {}
    for item in (text or "").split(","):
        if "=" not in item:
            continue
        target, token = ("".join(p.split()).lower() for p in item.split("=", 1))
        source = parse_source(token)
        if target in TARGETS and source is not None and accepts(target, source):
            rules[target] = source
    return rules


def format_rules(rules):
    return ",".join(f"{t}={rules[t].token}" for t in TARGETS if t in rules)


def parse_remap(text):
    """{profile key: rules}; a profile key is a GUID (lower case) or '*'."""
    profiles = {}
    for part in (text or "").split(";"):
        if ":" not in part:
            continue
        key, rules = part.split(":", 1)
        key = key.strip().lower()
        if key == "*" or re.fullmatch(r"[0-9a-f]{32}", key):
            profiles[key] = parse_rules(rules)
    return profiles


def format_remap(profiles):
    """'*' comes last. Keep empty profiles that block a same-model or '*' fallback."""
    keys = sorted(k for k in profiles if k != "*") + (["*"] if "*" in profiles else [])
    mapped_models = {guid_model(k) for k in keys if k != "*" and profiles[k]} - {None}
    keep = [k for k in keys if profiles[k] or
            (k != "*" and (profiles.get("*") or guid_model(k) in mapped_models))]
    return ";".join(f"{k}:{format_rules(profiles[k])}" for k in keep)


def guid_model(guid):
    """(vendor, product) from an SDL GUID that carries them (bytes 4-5 and 8-9, 6-7 and 10-11 zero),
    else None (a GUID built from the name)."""
    try:
        b = bytes.fromhex(guid)
    except (TypeError, ValueError):
        return None
    if len(b) != 16 or b[6:8] != b"\0\0" or b[10:12] != b"\0\0":
        return None
    vid, pid = int.from_bytes(b[4:6], "little"), int.from_bytes(b[8:10], "little")
    return (vid, pid) if vid else None


def profile_key(profiles, guid):
    """The profile a controller uses: its own GUID, else one of the same model (vendor and product,
    whichever connection or driver SDL reports), else '*'. None when there is none."""
    guid = (guid or "").lower()
    if guid in profiles:
        return guid
    model = guid_model(guid)
    if model:
        for key in profiles:
            if key != "*" and guid_model(key) == model:
                return key
    return "*" if "*" in profiles else None


def controller_rules(text, guid):
    profiles = parse_remap(text)
    key = profile_key(profiles, guid)
    return dict(profiles[key]) if key else {}


def controller_binding(rules, action):
    """The source that drives `action` (its own input when the profile does not bind it)."""
    if action.field in rules:
        return rules[action.field], False
    return Source(action.field), True


def set_controller_binding(text, guid, action, source):
    """The new controller_remap string with `action` bound to `source` in this controller's own
    profile (made from the profile it used so far); source None clears, "default" resets."""
    profiles = parse_remap(text)
    key = (guid or "*").lower()
    if key not in profiles:
        used = profile_key(profiles, guid)
        profiles[key] = dict(profiles[used]) if used else {}
    rules = profiles[key]
    if source == "default":
        rules.pop(action.field, None)
    else:
        rules[action.field] = NONE if source is None else source
    return format_remap(profiles)


AXIS_NAMES = {"lx": "Left stick X", "ly": "Left stick Y", "rx": "Right stick X", "ry": "Right stick Y",
              "lt": "LT", "rt": "RT"}
HALF_NAMES = {("lx", 1): "Left stick right", ("lx", -1): "Left stick left",
              ("ly", 1): "Left stick up", ("ly", -1): "Left stick down",
              ("rx", 1): "Right stick right", ("rx", -1): "Right stick left",
              ("ry", 1): "Right stick up", ("ry", -1): "Right stick down"}
BUTTON_NAMES = {"a": "A", "b": "B", "x": "X", "y": "Y", "back": "Back", "guide": "Guide",
                "start": "Start", "ls": "LS", "rs": "RS", "lb": "LB", "rb": "RB", "up": "D-pad up",
                "down": "D-pad down", "left": "D-pad left", "right": "D-pad right", "misc1": "Share",
                "paddle1": "Paddle 1", "paddle2": "Paddle 2", "paddle3": "Paddle 3",
                "paddle4": "Paddle 4", "touchpad": "Touchpad"}


def source_text(source, names=None):
    """A player-facing name; `names` replaces button names (PlayStation prompts)."""
    names = {**BUTTON_NAMES, **(names or {})}
    if source.name == "none":
        return "Not bound"
    if source.is_button:
        return names.get(source.name, source.name)
    if source.half:
        text = HALF_NAMES[(source.name, source.half)]
    else:
        text = names.get(source.name, AXIS_NAMES[source.name])
        if source.invert:
            text += " (inverted)"
    if abs(source.threshold - DEFAULT_THRESHOLD) > 1e-6:
        text += f" past {round(source.threshold * 100)}%"
    return text


# Without a remap the SDK's driver also presses Y, B, X and A with the Elite paddles.
DEFAULT_EXTRA = {"y": "paddle1", "b": "paddle2", "x": "paddle3", "a": "paddle4"}


def controller_conflicts(rules, actions=CONTROLLER_ACTIONS, names=None):
    """[(input text, [row labels])] for physical inputs more than one row reads (a whole stick axis
    overlaps either of its directions)."""
    used = []
    for action in actions:
        source, default = controller_binding(rules, action)
        if source.name == "none":
            continue
        used.append((action, source))
        if default and action.field in DEFAULT_EXTRA:
            used.append((action, Source(DEFAULT_EXTRA[action.field])))
    out = []
    seen = set()
    for i, (action, source) in enumerate(used):
        if i in seen:
            continue
        name, dirs = source.directions()
        group = [action.label]
        for j in range(i + 1, len(used)):
            other_name, other_dirs = used[j][1].directions()
            if other_name == name and dirs & other_dirs:
                seen.add(j)
                if used[j][0].label not in group:
                    group.append(used[j][0].label)
        if len(group) > 1:
            out.append((source_text(Source(name), names) if source.is_button or name in TRIGGERS
                        else AXIS_NAMES[name], group))
    return out


# --- detection ---------------------------------------------------------------------------------
def norm(raw):
    """An SDL axis value as the wheel driver reads it (Norm): -1 to 1."""
    return raw / 32768.0 if raw < 0 else raw / 32767.0


@dataclass(frozen=True)
class Snapshot:
    """A joystick's raw state: axes -32768..32767, buttons, hats (SDL hat bits). For a controller
    with a gamepad mapping, `pad` has the mapped inputs too: buttons by name and the axes
    lx ly rx ry (up and right positive) and lt rt (0 to 1)."""
    axes: tuple = ()
    buttons: tuple = ()
    hats: tuple = ()
    pad_buttons: frozenset = frozenset()
    pad_axes: tuple = ()  # (lx, ly, rx, ry, lt, rt)


@dataclass(frozen=True)
class Detection:
    kind: str           # "axis" or "button" (wheel); "source" (controller)
    number: int = -1    # axis or button number
    direction: int = 0  # axis: +1 moved up from its rest, -1 down
    rest: int = 0       # axis: where it rested when binding started
    source: Source | None = None

    def describe(self):
        if self.kind == "button":
            return f"Button {self.number}"
        if self.kind == "axis":
            return (f"Axis {self.number}, {'up' if self.direction > 0 else 'down'} from "
                    f"{rest_text(self.rest)}")
        return source_text(self.source)


def rest_text(raw):
    v = norm(raw)
    return "the maximum" if v > .8 else "the minimum" if v < -.8 else "the centre" \
        if abs(v) < .2 else f"{round((v + 1) * 50)}%"


class Detector:
    """Waits for the first input that changes from where it was when binding started. kind: a
    wheel row's kind ("axis", "button"; "steer" takes a smaller turn; "any" a button or an axis,
    the handbrake) or, with controller, a
    controller target's ("button", "trigger", "stick"; the mapped gamepad inputs).
    Inputs held at the start count only after they were released."""
    def __init__(self, baseline, kind, controller=False, threshold=None):
        self.base = baseline
        self.kind = kind
        self.controller = controller
        self.threshold = threshold if threshold is not None else (
            0.15 if kind == "steer" else 0.5)
        self.released = set()

    def feed(self, snap):
        if self.controller:
            return self._pad(snap)
        if self.kind in ("button", "any"):
            for n, down in enumerate(snap.buttons):
                held = n < len(self.base.buttons) and self.base.buttons[n]
                if held and not down:
                    self.released.add(n)
                if down and (not held or n in self.released):
                    return Detection("button", n)
        if self.kind in ("axis", "steer", "any"):
            for n, raw in enumerate(snap.axes):
                if n >= len(self.base.axes):
                    break
                delta = norm(raw) - norm(self.base.axes[n])
                if abs(delta) >= self.threshold:
                    return Detection("axis", n, 1 if delta > 0 else -1, self.base.axes[n])
        return None

    def _pad(self, snap):
        base_axes = self.base.pad_axes or (0.0,) * 6
        names = ("lx", "ly", "rx", "ry", "lt", "rt")
        if self.kind != "stick":
            for name in PAD_BUTTONS + EXTRA_BUTTONS:
                down = name in snap.pad_buttons
                held = name in self.base.pad_buttons
                if held and not down:
                    self.released.add(name)
                if down and (not held or name in self.released) and name != "guide":
                    return Detection("source", source=Source(name))
        for i, name in enumerate(names):
            if i >= len(snap.pad_axes):
                break
            delta = snap.pad_axes[i] - base_axes[i]
            if abs(delta) < self.threshold:
                continue
            sign = 1 if delta > 0 else -1
            if name in TRIGGERS:
                if self.kind != "stick" and sign > 0:
                    return Detection("source", source=Source(name))
                continue
            if self.kind == "stick":
                # The row asks for right or up: the other way binds the axis inverted.
                return Detection("source", source=Source(name, invert=sign < 0))
            return Detection("source", source=Source(name, half=sign))
        return None


# --- pedals -------------------------------------------------------------------------------
def pedal_value(raw, open_raw, invert, known):
    """How far a pedal is pressed (0 to 1), as the wheel driver computes it (`pressed`) once the
    pedal was seen at rest: open_raw is where the axis sat when the game opened the wheel."""
    v = norm(raw)
    r = 1.0 if known else norm(open_raw)
    high_rest = r > 0.0
    if invert:
        high_rest = not high_rest
    rest = (1.0 if high_rest else -1.0) if (invert or known) else r
    if high_rest:
        p = (rest - v) / (rest + 1.0) if rest + 1.0 else 0.0
    else:
        p = (v - rest) / (1.0 - rest) if 1.0 - rest else 0.0
    d = 0.02
    return min(1.0, max(0.0, (p - d) / (1.0 - d)))


def pedal_calibration(rest_raw, full_raw, known):
    """(invert, travel) for a pedal seen at rest and fully pressed. invert is the switch under which
    the driver reads the rest as released and the full press as pressed; travel is the share of
    the axis the pedal covers (a pedal that stops short never reaches a full press)."""
    def error(invert):
        return pedal_value(rest_raw, rest_raw, invert, known) + \
            (1.0 - pedal_value(full_raw, rest_raw, invert, known))
    invert = error(True) + 1e-6 < error(False)
    travel = abs(norm(full_raw) - norm(rest_raw)) / 2.0
    return invert, min(1.0, travel)


# --- calibration -------------------------------------------------------------------------------
def steering_value(raw, rotation, lock, deadzone, invert):
    """The steering the driver sends (-1 to 1) for an axis value: scaled by rotation / lock,
    inverted, then the deadzone (the steps of ComputeStateLocked)."""
    s = norm(raw) * (rotation / max(1, lock)) * (-1.0 if invert else 1.0)
    a = abs(s)
    if a <= deadzone or deadzone >= 1:
        return 0.0
    out = min(1.0, (a - deadzone) / (1.0 - deadzone))
    return out if s > 0 else -out


def rotation_from_turn(raw, centre_raw=0, degrees=90):
    """The wheel's rotation (lock to lock, degrees) from the axis read with the wheel turned
    `degrees` from the centre: the axis spans the whole rotation, so one side of it is half.
    Rounded to the setting's 10 degree steps; None when the wheel was not turned."""
    moved = abs(norm(raw) - norm(centre_raw))
    if moved < 0.02:
        return None
    return int(max(90, min(2520, round(2 * degrees / moved / 10) * 10)))


def steering_calibration(left_raw, right_raw):
    """(invert, travel) from the axis at full left lock and at full right lock."""
    left, right = norm(left_raw), norm(right_raw)
    return left > right, min(1.0, abs(right - left) / 2.0)


def busiest_axis(baseline, snap, exclude=()):
    """(axis, distance) of the axis furthest from where it was in `baseline`."""
    best, best_delta = None, 0.0
    for n, raw in enumerate(snap.axes):
        if n in exclude or n >= len(baseline.axes):
            continue
        delta = abs(norm(raw) - norm(baseline.axes[n]))
        if delta > best_delta:
            best, best_delta = n, delta
    return best, best_delta


class Calibration:
    """Wheel calibration, step by step: centre (wheel centred, pedals released), full left and full
    right lock (the steering axis, its direction and travel), a quarter turn (the rotation), then
    each pedal pressed fully (its axis, travel and invert switch). Any step but the first can be
    skipped, which keeps what the settings have for it. `known`: a wheel with a built-in profile
    (its pedals are taken to rest at the maximum). `parts`: the wheel and the devices merged after
    it in the snapshots fed (merge), so the pedals can be found on a pedal device (#272); the
    steering is the wheel's."""
    STEPS = ("centre", "left", "right", "quarter", "throttle", "brake", "clutch", "finish")
    PROMPTS = {
        "centre": "Centre the wheel and release every pedal, then press Next.",
        "left": "Turn the wheel all the way to the left, to its lock, and hold it there.",
        "right": "Now turn it all the way to the right, to its lock, and hold it there.",
        "quarter": "Turn the wheel a quarter turn to the right from the centre: the top of the "
                   "rim points right (90°). Hold it there.",
        "throttle": "Press the throttle pedal all the way down and release it.",
        "brake": "Press the brake pedal all the way down and release it. "
                 "One pedal axis for both (combined pedals)? Press the brake side.",
        "clutch": "Press the clutch pedal all the way down and release it (Skip without one).",
        "finish": "Check the steering deadzone, then Apply.",
    }
    MIN_MOVE = {"left": 0.3, "right": 0.3, "quarter": 0.02, "throttle": 0.3, "brake": 0.3, "clutch": 0.3}

    def __init__(self, known=False, parts=None):
        self.known = known
        self.parts = list(parts or [])
        self.index = 0
        self.base = None
        self.steer = None   # axis
        self.left = self.right = None  # raw values at the locks
        self.quarter = None
        self.pedals = {}    # name -> (axis, rest, full)
        self.peak = None    # (axis, raw furthest from the centre snapshot) in a pedal step
        self.far = {}       # axis -> (distance, raw) furthest from the centre in this step
        self.last = None

    @property
    def step(self):
        return self.STEPS[self.index]

    def steer_exclude(self):
        """Axes the steering is not looked for on: the pedals found, the other devices' axes."""
        out = {p[0] for p in self.pedals.values()}
        if len(self.parts) > 1:
            out |= set(range(self.parts[0].axes, sum(p.axes for p in self.parts)))
        return out

    def where(self, axis):
        """(part index, axis on that device); (0, axis) without parts."""
        if not self.parts:
            return 0, axis
        return split(self.parts, "axis", axis)

    def axis_text(self, axis):
        part, n = self.where(axis)
        return f"axis {n}" + (f" on {self.parts[part].name}" if part else "")

    @property
    def done(self):
        return self.step == "finish"

    def exclude(self):
        used = {self.steer} | {p[0] for n, p in self.pedals.items()}
        if self.step == "brake" and "throttle" in self.pedals:
            used.discard(self.pedals["throttle"][0])  # combined pedals share the axis
        return used - {None}

    def feed(self, snap):
        """Track the step's input; returns a line describing what is seen."""
        self.last = snap
        step = self.step
        if self.base is None or step in ("centre", "finish"):
            return ""
        if step in ("left", "right", "quarter"):
            axis = self.steer
            if axis is None:
                axis, _ = busiest_axis(self.base, snap, self.steer_exclude())
            if axis is None or axis >= len(snap.axes):
                return ""
            return f"Steering: axis {axis} at {round(norm(snap.axes[axis]) * 100)}%"
        excluded = self.exclude()
        for n, raw in enumerate(snap.axes):
            if n in excluded or n >= len(self.base.axes):
                continue
            moved = norm(raw) - norm(self.base.axes[n])
            key = (n, 1 if moved > 0 else -1)
            if abs(moved) > self.far.get(key, (0.0, 0))[0]:
                self.far[key] = (abs(moved), raw)
        t = self.pedals.get("throttle")
        # Only the other side of the throttle's axis can be a brake (combined pedals).
        taken = (t[0], 1 if norm(t[2]) > norm(t[1]) else -1) if step == "brake" and t else None
        best = max((k for k in self.far if k != taken), key=lambda k: self.far[k][0], default=None)
        if best is not None and self.far[best][0] >= self.MIN_MOVE[step] / 2:
            self.peak = (best[0], self.far[best][1])
        if self.peak is None:
            return "Waiting for a pedal…"
        axis, raw = self.peak
        travel = abs(norm(raw) - norm(self.base.axes[axis])) / 2
        return f"{step.capitalize()}: {self.axis_text(axis)}, pressed {round(travel * 100)}% of its travel"

    def can_advance(self):
        step = self.step
        if step == "centre":
            return self.last is not None
        if step == "finish":
            return False
        if step in ("left", "right", "quarter"):
            if self.last is None or self.base is None:
                return False
            axis = self.steer
            if axis is None:
                axis, delta = busiest_axis(self.base, self.last, self.steer_exclude())
            else:
                delta = abs(norm(self.last.axes[axis]) - norm(self.base.axes[axis]))
            return axis is not None and delta >= self.MIN_MOVE[step]
        return self.peak is not None and \
            abs(norm(self.peak[1]) - norm(self.base.axes[self.peak[0]])) >= self.MIN_MOVE[step]

    def next(self):
        """Record the step from what was fed, then go on."""
        if not self.can_advance():
            return False
        step, snap = self.step, self.last
        if step == "centre":
            self.base = snap
        elif step in ("left", "right", "quarter"):
            if self.steer is None:
                self.steer, _ = busiest_axis(self.base, snap, self.steer_exclude())
            setattr(self, step, snap.axes[self.steer])
        else:
            axis, raw = self.peak
            self.pedals[step] = (axis, self.base.axes[axis], raw)
        self.peak, self.far = None, {}
        self.index += 1
        return True

    def skip(self):
        if self.step in ("centre", "finish"):
            return False
        self.peak, self.far = None, {}
        self.index += 1
        return True

    def back(self):
        if self.index == 0:
            return
        self.index -= 1
        self.peak, self.far = None, {}
        step = self.step
        if step in ("left", "right", "quarter"):
            setattr(self, step, None)
            if step == "left":
                self.steer = None
        elif step in self.pedals:
            del self.pedals[step]
        elif step == "centre":
            self.base = None

    def combined(self):
        """Throttle and brake were found on one axis, in opposite directions."""
        t, br = self.pedals.get("throttle"), self.pedals.get("brake")
        return bool(t and br and t[0] == br[0] and (norm(t[2]) > norm(t[1])) != (norm(br[2]) > norm(br[1])))

    def rotation(self):
        if self.quarter is None or self.base is None or self.steer is None:
            return None
        return rotation_from_turn(self.quarter, self.base.axes[self.steer])

    def results(self, settings):
        """(lines, warnings) to show before Apply."""
        lines, warnings = [], []
        if self.steer is not None and self.left is not None and self.right is not None:
            invert, travel = steering_calibration(self.left, self.right)
            lines.append(f"Steering: axis {self.steer}{', inverted' if invert else ''}")
            if travel < 0.9:
                warnings.append(f"The wheel reaches only {int(travel * 100)}% of its axis at full "
                                "lock: check the rotation set in the wheel's own software.")
        rotation = self.rotation()
        if rotation:
            lines.append(f"Rotation: {rotation}° lock to lock")
        for name in ("throttle", "brake", "clutch"):
            if name not in self.pedals:
                continue
            axis, rest, full = self.pedals[name]
            if self.combined() and name == "brake":
                lines.append(f"Brake: the other side of {self.axis_text(axis)} (combined pedals)")
                continue
            invert, travel = pedal_calibration(rest, full, self.known and self.where(axis)[0] == 0)
            lines.append(f"{name.capitalize()}: {self.axis_text(axis)}, rests at {rest_text(rest)}"
                         f"{', inverted' if invert else ''}")
            if travel < 0.9 and not (self.combined() and name == "throttle"):
                warnings.append(f"The {name} reaches only {int(travel * 100)}% of its axis, so a "
                                f"full press gives {int(travel * 100)}% {name}: check the pedal's "
                                "range in the wheel's own software.")
        devices = {self.where(p[0])[0] for p in self.pedals.values()} - {0}
        if len(devices) > 1:
            warnings.append("The pedals were found on more than one device: the game reads one "
                            "pedal device, the first one's.")
        if (not self.known or devices) and self.pedals:
            warnings.append("Keep the pedals released while the game starts: it reads where they "
                            "rest then.")
        return lines, warnings

    def changes(self, settings, deadzone=None):
        """The settings Apply writes. Steps that were skipped leave theirs alone."""
        out = {}
        axes = parse_kv(settings.get("wheel_axes", ""))
        if self.steer is not None and self.left is not None and self.right is not None:
            axes["steer"] = self.steer
            out["wheel_invert_steering"], _ = steering_calibration(self.left, self.right)
        rotation = self.rotation()
        if rotation:
            out["wheel_rotation"] = rotation
            if int(settings.get("wheel_lock", 900)) > rotation:
                out["wheel_lock"] = rotation  # full lock must still give full steering
        # Each pedal on the wheel (wheel_axes) or on the pedal device (wheel_pedal_axes, #272): the
        # first other device a pedal was found on becomes the pedal device.
        pedal_axes = parse_kv(settings.get("wheel_pedal_axes", ""))
        old_device = settings.get("wheel_pedal_device", "")
        device = None  # the Part the pedals were found on

        def place(name, axis):
            nonlocal device, pedal_axes
            part, n = self.where(axis)
            if part == 0:
                axes[name] = n
                pedal_axes.pop(name, None)
                return True
            if device is None:
                device = self.parts[part]
                if not device.matches(old_device):
                    pedal_axes = {}
            if self.parts[part] != device:
                return False
            pedal_axes[name] = n
            axes.pop(name, None)
            return True

        def known(axis):
            return self.known and self.where(axis)[0] == 0

        if self.combined():
            axis, rest, full = self.pedals["throttle"]
            place("throttle", axis)
            axes.pop("brake", None)
            pedal_axes.pop("brake", None)
            out["wheel_pedals"] = "combined"
            # Combined: the driver takes the axis below its centre as throttle unless inverted.
            out["wheel_invert_throttle"] = norm(full) > norm(rest)
        else:
            for name in ("throttle", "brake"):
                if name in self.pedals:
                    axis, rest, full = self.pedals[name]
                    if place(name, axis):
                        out[f"wheel_invert_{name}"], _ = pedal_calibration(rest, full, known(axis))
            if "throttle" in self.pedals and "brake" in self.pedals and \
                    settings.get("wheel_pedals") == "combined":
                out["wheel_pedals"] = "auto"
        if "clutch" in self.pedals:
            axis, rest, full = self.pedals["clutch"]
            if place("clutch", axis):
                out["wheel_invert_clutch"], _ = pedal_calibration(rest, full, known(axis))
        if axes != parse_kv(settings.get("wheel_axes", "")):
            out["wheel_axes"] = format_kv(axes, AXIS_ORDER)
        if device is not None and not device.matches(old_device):
            out["wheel_pedal_device"] = device.setting
        if pedal_axes != parse_kv(settings.get("wheel_pedal_axes", "")):
            out["wheel_pedal_axes"] = format_kv(pedal_axes, PEDAL_KEYS)
        if deadzone is not None:
            out["wheel_deadzone"] = round(float(deadzone), 2)
        return out
