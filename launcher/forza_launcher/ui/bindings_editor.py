"""Press-to-bind controls (#279): pick a device, watch its inputs live, and bind each game
action by pressing the button or moving the axis for it."""
import dataclasses
import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget)

from .. import bindings, paths
from ..paths import IS_WINDOWS
from . import theme
from .widgets import label

BIND_TIMEOUT = 10.0  # seconds without an input before a Bind gives up
HATS = {1: "up", 2: "right", 4: "down", 8: "left", 3: "up right", 6: "down right", 12: "down left",
        9: "up left"}
SONY = 0x054C
PLAYSTATION_NAMES = {"a": "Cross", "b": "Circle", "x": "Square", "y": "Triangle", "lb": "L1",
                     "rb": "R1", "lt": "L2", "rt": "R2", "ls": "L3", "rs": "R3", "back": "Create",
                     "start": "Options", "guide": "PS", "misc1": "Mute"}
PAD_AXES = ("Left stick X", "Left stick Y", "Right stick X", "Right stick Y", "LT", "RT")
LAMP_NAMES = {"back": "Back", "guide": "Guide", "start": "Start", "up": "Up", "down": "Down",
              "left": "Left", "right": "Right", "misc1": "Misc", "paddle1": "P1", "paddle2": "P2",
              "paddle3": "P3", "paddle4": "P4", "touchpad": "Pad"}


class InputView(QWidget):
    """The selected device's axes as bars and its buttons as lamps that light while pressed.

    A wheel that reads pedals, a shifter or a handbrake on another device graphs those too
    (`inputs`, from bindings.live_inputs): Throttle, Brake, Clutch, Handbrake and the shifter."""
    ROW, LAMP, GAP = 20, 26, 6

    def __init__(self):
        super().__init__()
        self.snap = None
        self.names = None  # a controller: its mapped inputs, by name ({button: label})
        self.inputs = None  # a wheel plus its pedal, shifter and handbrake devices (LiveInputs)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Live view of the selected device's axes and buttons")
        self.setMinimumHeight(40)

    def set_snapshot(self, snap, names=None, inputs=None):
        old = self._shape()
        self.snap, self.names, self.inputs = snap, names, inputs
        if self._shape() != old:
            self.setFixedHeight(self.height_for(self.width()))
        self.update()

    def _shape(self):
        if self.inputs is not None:
            return ("live", len(self.inputs.axes), len(self.inputs.buttons), len(self.inputs.hats),
                    self.lamp_width(), self.axis_gutter())
        if not self.snap:
            return None
        if self.names is not None:
            return ("pad", len(self.names))
        return ("raw", len(self.snap.axes), len(self.snap.buttons), len(self.snap.hats))

    def lamp_width(self):
        if self.names is not None and self.inputs is None:
            return 48
        if self.inputs is not None and self.inputs.buttons:
            text = max(self.fontMetrics().horizontalAdvance(label_)
                       for label_, _down in self.inputs.buttons)
            if text + 16 > self.LAMP:
                return text + 16
        return self.LAMP

    def axis_gutter(self):
        if self.inputs is None:
            return 104 if self.names is not None else 64
        if not self.inputs.axes:
            return 64
        text = max(self.fontMetrics().horizontalAdvance(label_) for label_, _raw in self.inputs.axes)
        return max(64, text + 12)

    def lamps_per_row(self, width):
        return max(1, (width + self.GAP) // (self.lamp_width() + self.GAP))

    def counts(self):
        if self.inputs is not None:
            return len(self.inputs.axes), len(self.inputs.buttons), len(self.inputs.hats)
        if self.names is not None:
            return len(PAD_AXES), len(self.names), 0
        return len(self.snap.axes), len(self.snap.buttons), len(self.snap.hats)

    def height_for(self, width):
        if not self.snap and self.inputs is None:
            return 40
        axes, n, hats = self.counts()
        rows = (n + self.lamps_per_row(width) - 1) // self.lamps_per_row(width) if n else 0
        return (axes * self.ROW + (12 if n else 0) + rows * (self.LAMP + self.GAP)
                + (self.ROW + 6 if hats else 0) + 4)

    def resizeEvent(self, event):
        self.setFixedHeight(self.height_for(self.width()))
        super().resizeEvent(event)

    def sizeHint(self):
        return QSize(480, self.height_for(480))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.snap and self.inputs is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, "No device selected.")
            return
        y = 0
        w = self.width()
        if self.inputs is not None:
            axes = [(name, (bindings.norm(raw) + 1) / 2, str(raw))
                    for name, raw in self.inputs.axes]
            lamps = list(self.inputs.buttons)
            hats = self.inputs.hats
        elif self.names is not None:
            pad = self.snap.pad_axes or (0.0,) * 6
            axes = [(name, (v + 1) / 2 if i < 4 else v, f"{v:+.2f}" if i < 4 else f"{v:.2f}")
                    for i, (name, v) in enumerate(zip(PAD_AXES, pad))]
            lamps = [(label_, name in self.snap.pad_buttons) for name, label_ in self.names.items()]
            hats = ()
        else:
            axes = [(f"Axis {n}", (bindings.norm(raw) + 1) / 2, str(raw))
                    for n, raw in enumerate(self.snap.axes)]
            lamps = [(str(n), down) for n, down in enumerate(self.snap.buttons)]
            hats = self.snap.hats
        left = self.axis_gutter()
        for name, frac, text in axes:
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y, left - 4, self.ROW), Qt.AlignLeft | Qt.AlignVCenter, name)
            track = QRectF(left, y + 5, max(10, w - left - 64), self.ROW - 10)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BORDER))
            p.drawRoundedRect(track, 4, 4)
            p.setBrush(QColor(theme.ORANGE))
            p.drawRoundedRect(QRectF(track.x(), track.y(), track.width() * frac, track.height()), 4, 4)
            p.setPen(QPen(QColor(theme.TEXT), 1))
            mid = track.x() + track.width() / 2
            p.drawLine(int(mid), int(track.y()) - 2, int(mid), int(track.bottom()) + 2)
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(w - 60, y, 60, self.ROW), Qt.AlignRight | Qt.AlignVCenter, text)
            y += self.ROW
        if lamps:
            y += 12
            per = self.lamps_per_row(w)
            lw = self.lamp_width()
            for n, (text, down) in enumerate(lamps):
                r, c = divmod(n, per)
                rect = QRectF(c * (lw + self.GAP), y + r * (self.LAMP + self.GAP), lw, self.LAMP)
                p.setPen(QPen(QColor(theme.ORANGE if down else theme.BORDER), 2))
                p.setBrush(QColor(theme.ORANGE) if down else Qt.NoBrush)
                p.drawRoundedRect(rect, self.LAMP / 2, self.LAMP / 2)
                p.setPen(QColor(theme.BG if down else theme.MUTED))
                p.drawText(rect, Qt.AlignCenter, text)
            y += ((len(lamps) + per - 1) // per) * (self.LAMP + self.GAP)
        if hats:
            y += 6
            text = "   ".join(f"Hat {n}: {HATS.get(v, 'centred')}" for n, v in enumerate(hats))
            p.setPen(QColor(theme.ORANGE if any(hats) else theme.MUTED))
            p.drawText(QRectF(0, y, w, self.ROW), Qt.AlignLeft | Qt.AlignVCenter, text)


class BindingRow(QWidget):
    def __init__(self, action, bind, clear, reset):
        super().__init__()
        self.action = action
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        self.title = QLabel(action.label)
        self.title.setMinimumWidth(150)
        self.title.setWordWrap(True)
        lay.addWidget(self.title, 3)
        self.value = QLabel("")
        self.value.setObjectName("muted")
        self.value.setMinimumWidth(120)
        self.value.setWordWrap(True)
        lay.addWidget(self.value, 3)
        self.bind = QPushButton("Bind")
        self.bind.setAccessibleName(f"Bind {action.label}")
        self.bind.clicked.connect(lambda: bind(self))
        self.clear = QPushButton("Clear")
        self.clear.setAccessibleName(f"Clear {action.label}")
        self.clear.clicked.connect(lambda: clear(self))
        self.reset = QPushButton("Reset to default")
        self.reset.setAccessibleName(f"Reset {action.label} to default")
        self.reset.clicked.connect(lambda: reset(self))
        self.bind.setMinimumWidth(90)
        for b in (self.bind, self.clear, self.reset):
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b)

    def show_value(self, text, conflict=False):
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {theme.ORANGE};" if conflict else "")

class SteeringPreview(QWidget):
    """The steering the game would get: -1 (full left) to 1 (full right)."""
    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Steering sent to the game")

    def set_value(self, value):
        self.value = value
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0, 4, self.width(), self.height() - 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(track, 4, 4)
        mid = track.width() / 2
        width = mid * abs(self.value)
        p.setBrush(QColor(theme.ORANGE))
        x = mid if self.value >= 0 else mid - width
        p.drawRoundedRect(QRectF(x, track.y(), width, track.height()), 3, 3)
        p.setPen(QPen(QColor(theme.TEXT), 1))
        p.drawLine(int(mid), 0, int(mid), self.height())


class CalibrationPanel(QWidget):
    """Wheel calibration, one step at a time (bindings.Calibration does the work)."""
    finished = Signal(dict)  # the settings to apply; {} when cancelled

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.cal = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.title = label("", "cardTitle")
        lay.addWidget(self.title)
        self.prompt = label("", "")
        lay.addWidget(self.prompt)
        self.live = label("", "muted")
        lay.addWidget(self.live)
        self.results = label("", "")
        lay.addWidget(self.results)
        self.warnings = label("", "")
        self.warnings.setStyleSheet(f"color: {theme.ORANGE};")
        lay.addWidget(self.warnings)
        self.deadzone_box = QWidget()
        dz = QVBoxLayout(self.deadzone_box)
        dz.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Steering deadzone"), 1)
        self.deadzone_value = QLabel("")
        row.addWidget(self.deadzone_value)
        dz.addLayout(row)
        self.deadzone = QSlider(Qt.Horizontal)
        self.deadzone.setRange(0, 50)
        self.deadzone.setAccessibleName("Steering deadzone")
        self.deadzone.valueChanged.connect(lambda _v: self.update_preview())
        dz.addWidget(self.deadzone)
        dz.addWidget(label("Turn the wheel: the bar is the steering the game gets.", "help"))
        self.preview = SteeringPreview()
        dz.addWidget(self.preview)
        self.preview_text = label("", "muted")
        dz.addWidget(self.preview_text)
        lay.addWidget(self.deadzone_box)
        buttons = QHBoxLayout()
        self.back = QPushButton("Back")
        self.back.clicked.connect(self.go_back)
        self.skip = QPushButton("Skip")
        self.skip.clicked.connect(self.go_skip)
        self.next = QPushButton("Next")
        self.next.clicked.connect(self.go_next)
        self.apply = QPushButton("Apply")
        self.apply.clicked.connect(self.go_apply)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(lambda: self.finished.emit({}))
        for b in (self.back, self.skip, self.next, self.apply, self.cancel):
            b.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addLayout(buttons)

    def begin(self, known, parts=None):
        self.cal = bindings.Calibration(known, parts)
        self.deadzone.setValue(round(float(self.settings().get("wheel_deadzone", 0.0)) * 100))
        self.show_step()

    def show_step(self):
        cal = self.cal
        n = len(cal.STEPS)
        self.title.setText(f"Wheel calibration \u00b7 step {cal.index + 1} of {n}")
        self.prompt.setText(cal.PROMPTS[cal.step])
        final = cal.done
        self.back.setEnabled(cal.index > 0)
        self.skip.setVisible(not final and cal.step != "centre")
        self.next.setVisible(not final)
        self.apply.setVisible(final)
        self.deadzone_box.setVisible(final)
        self.live.setVisible(not final)
        if final:
            lines, warnings = cal.results(self.settings())
            self.results.setText("\n".join(lines) or "Nothing measured: only the deadzone changes.")
            self.warnings.setText("\n".join(warnings))
        self.results.setVisible(final)
        self.warnings.setVisible(final and bool(self.warnings.text()))
        self.update_preview()

    def feed(self, snap):
        if self.cal is None:
            return
        text = self.cal.feed(snap)
        self.live.setText(text)
        self.next.setEnabled(self.cal.can_advance())
        if self.cal.done:
            self.update_preview()

    def settings_after(self):
        merged = dict(self.settings())
        merged.update(self.cal.changes(self.settings()))
        return merged

    def update_preview(self):
        dz = self.deadzone.value() / 100
        self.deadzone_value.setText(f"{dz:.2f}")
        cal = self.cal
        if cal is None or not cal.done:
            return
        s = self.settings_after()
        axis = bindings.parse_kv(s.get("wheel_axes", "")).get("steer", 0)
        snap = cal.last
        raw = snap.axes[axis] if snap is not None and 0 <= axis < len(snap.axes) else 0
        value = bindings.steering_value(raw, int(s.get("wheel_rotation", 900)),
                                        int(s.get("wheel_lock", 900)), dz,
                                        bool(s.get("wheel_invert_steering", False)))
        self.preview.set_value(value)
        self.preview_text.setText(f"Axis {axis}: {raw} \u2192 steering {value:+.2f}")

    def go_next(self):
        if self.cal.next():
            self.show_step()

    def go_skip(self):
        if self.cal.skip():
            self.show_step()

    def go_back(self):
        self.cal.back()
        self.show_step()

    def go_apply(self):
        self.finished.emit(self.cal.changes(self.settings(), self.deadzone.value() / 100))


class BindingsEditor(QWidget):
    """The Bindings card's content. `apply(changes)` stores settings ({key: value})."""
    settings_requested = Signal()

    def __init__(self, settings, apply):
        super().__init__()
        self.settings, self.apply = settings, apply
        self.backend = None
        self.devices = []
        self.device = None
        self.snap = None
        self.binding = None  # (row, Detector, deadline)
        self.bind_parts = ([], [])  # the devices a Bind watches and their snapshots at its start
        self.capture = False  # a calibration holds the device too
        self.cal_parts = []  # the devices a calibration watches (the wheel first)
        self.rows = []
        self.last_scan = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Bindings", "cardTitle"), 1)
        self.picker = QComboBox()
        self.picker.setAccessibleName("Device to bind")
        self.picker.setMinimumWidth(260)
        self.picker.currentIndexChanged.connect(self.device_chosen)
        head.addWidget(self.picker)
        # #306: which device is the wheel (the first player's; wheel_device).
        self.use_wheel = QPushButton("Use as the wheel")
        self.use_wheel.clicked.connect(self.toggle_wheel_device)
        self.use_wheel.hide()
        head.addWidget(self.use_wheel)
        lay.addLayout(head)
        self.intro = label("", "help")
        lay.addWidget(self.intro)
        self.view = InputView()
        lay.addWidget(self.view)
        self.status = label("", "help")
        self.status.setStyleSheet(f"color: {theme.CREAM};")
        lay.addWidget(self.status)
        self.conflicts = label("", "")
        self.conflicts.setStyleSheet(f"color: {theme.ORANGE};")
        self.conflicts.hide()
        lay.addWidget(self.conflicts)
        self.calibration = CalibrationPanel(settings)
        self.calibration.finished.connect(self.end_calibration)
        self.calibration.hide()
        lay.addWidget(self.calibration)
        self.table_box = QWidget()
        self.table = QGridLayout(self.table_box)
        self.table.setContentsMargins(0, 0, 0, 0)
        self.table.setVerticalSpacing(4)
        lay.addWidget(self.table_box)
        tools = QHBoxLayout()
        self.reset_all = QPushButton("Reset all to default")
        self.reset_all.clicked.connect(self.reset_everything)
        self.reset_all.hide()
        tools.addWidget(self.reset_all)
        self.calibrate = QPushButton("Calibrate wheel")
        self.calibrate.clicked.connect(self.begin_calibration)
        self.calibrate.hide()
        tools.addWidget(self.calibrate)
        advanced = QPushButton("Text settings (advanced)")
        advanced.setObjectName("link")
        advanced.setCursor(Qt.PointingHandCursor)
        advanced.clicked.connect(self.settings_requested)
        tools.addWidget(advanced)
        tools.addStretch(1)
        lay.addLayout(tools)
        cancel = QShortcut(QKeySequence(Qt.Key_Escape), self)
        cancel.setContext(Qt.WidgetWithChildrenShortcut)
        cancel.activated.connect(lambda: self.cancel("Binding cancelled."))
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.poll)
        self.show_device()

    # --- device -------------------------------------------------------------------------------
    def start(self, backend=None):
        if self.timer.isActive():
            return
        if backend is not None:
            self.backend = backend
        if self.backend is None:
            try:
                from ..gamepad import Joysticks
                self.backend = Joysticks()
            except (ImportError, OSError, RuntimeError) as exc:
                paths.log_error("open joysticks for bindings", exc)
                self.intro.setText("Controllers and wheels cannot be read here (SDL is unavailable).")
                return
        self.scan(force=True)
        self.timer.start()

    def stop(self):
        self.cancel("")
        if self.capture:
            self.end_calibration({})
        self.timer.stop()
        if self.backend is not None:
            self.backend.close()
            self.backend = None
        self.device = None
        self.snap = None

    def captures(self, instance):
        """Whether launcher navigation must ignore this device (a Bind or calibration runs)."""
        return (self.binding is not None or self.capture) and self.device is not None \
            and self.device.instance == instance

    def roles(self):
        """instance -> "pedals", "shifter", "handbrake" or "other" for the devices read with a
        wheel (#272, #303). A pedal or shifter device that is also the handbrake device keeps its
        role."""
        settings = self.settings()
        pick = bindings.pick_aux(self.devices, settings)
        out = {self.devices[i].instance: "other" for i in pick.other}
        handbrake = bindings.pick_handbrake(self.devices, settings)
        if handbrake is not None:
            out[self.devices[handbrake].instance] = "handbrake"
        for role in ("pedals", "shifter"):
            i = getattr(pick, role)
            if i is not None:
                out[self.devices[i].instance] = role
        return out

    def kind_of(self, device):
        mode = self.settings().get("wheel_mode", "auto")
        if mode != "off":
            if bindings.wheel_device_match(self.settings(), device):
                return "wheel"  # wheel_device makes it the wheel (#306)
            role = self.roles().get(device.instance)
            if role:
                return role
        if mode != "off" and (bindings.looks_like_wheel(device.name, device.vid, device.sdl_wheel,
                                                        device.pid)
                              or (mode == "on" and not device.gamepad)):
            return "wheel"
        return "controller" if device.gamepad else "unused"

    KINDS = {"wheel": "wheel", "controller": "controller", "unused": "not used by the game",
             "pedals": "pedals, read with the wheel", "shifter": "shifter, read with the wheel",
             "handbrake": "handbrake, read with the wheel", "other": "not used by the game",
             "wheel2": "wheel, a second controller"}

    def primary(self):
        """The instance of the wheel the first player drives with (bindings.primary_wheel)."""
        i = bindings.primary_wheel(self.devices, self.settings())
        return self.devices[i].instance if i is not None else None

    def kind_label(self, device, primary):
        kind = self.kind_of(device)
        if kind == "wheel" and device.instance != primary:
            kind = "wheel2"
        return f"{device.name} ({self.KINDS[kind]})"

    def label_picker(self):
        primary = self.primary()
        for i, d in enumerate(self.devices):
            if i < self.picker.count():
                self.picker.setItemText(i, self.kind_label(d, primary))
        self.update_wheel_choice(primary)

    def update_wheel_choice(self, primary):
        """"Use as the wheel" for a device that is not the wheel; on the one wheel_device names,
        "Choose the wheel automatically" (#306)."""
        d = self.device
        named = d is not None and bindings.wheel_device_match(self.settings(), d) > 0
        if named:
            self.use_wheel.setText("Choose the wheel automatically")
        else:
            self.use_wheel.setText("Use as the wheel")
        self.use_wheel.setVisible(d is not None and self.binding is None and not self.capture and (
            named or (d.instance != primary and self.kind_of(d) != "controller")))

    def toggle_wheel_device(self):
        d = self.device
        if d is None:
            return
        named = bindings.wheel_device_match(self.settings(), d) > 0
        self.apply({"wheel_device": "" if named else bindings.device_ref(d, self.devices)})
        self.status.setText("The wheel is chosen automatically again." if named else
                            f"{d.name} is the wheel: the first player drives with it, and pedals, "
                            "a shifter or a handbrake on their own USB device are read with it. "
                            "This applies the next time the game starts.")
        self.show_device()

    def extras(self):
        """The other devices a wheel's pedal and gear rows can be bound on: every connected
        device that is no game controller."""
        if self.device is None or self.kind() != "wheel":
            return []
        return [d for d in self.devices if d.instance != self.device.instance and not d.gamepad]

    def watch_extras(self):
        if self.backend is not None and hasattr(self.backend, "watch"):
            self.backend.watch([d.instance for d in self.extras()])

    def aux(self):
        """{"pedals": AuxInfo, "shifter": AuxInfo, "handbrake": AuxInfo} of the connected pedal,
        shifter and handbrake devices (the handbrake may be the pedal or shifter device)."""
        out = {}
        if self.kind() != "wheel":
            return out
        def info(d):
            axes, buttons, _ = self.backend.extra_counts(d.instance) \
                if self.backend is not None and hasattr(self.backend, "extra_counts") else (0, 0, 0)
            return bindings.AuxInfo(d.name, d.vid, d.pid, axes, buttons)
        roles = self.roles()
        for d in self.devices:
            role = roles.get(d.instance)
            if role in ("pedals", "shifter"):
                out[role] = info(d)
        handbrake = bindings.pick_handbrake(self.devices, self.settings())
        if handbrake is not None and self.devices[handbrake].instance != self.device.instance:
            out["handbrake"] = info(self.devices[handbrake])
        return out

    def live_for(self, snap):
        """The live view of the selected wheel, including a pedal, shifter or handbrake device the
        game reads with it (#306). None keeps the device's own axes and buttons."""
        if self.kind() != "wheel" or self.device is None or self.backend is None:
            return None
        devices = {}
        pick = bindings.pick_aux(self.devices, self.settings())
        for role in ("pedals", "shifter"):
            i = getattr(pick, role)
            if i is not None:
                devices[role] = self.devices[i]
        i = bindings.pick_handbrake(self.devices, self.settings())
        if i is not None:
            devices["handbrake"] = self.devices[i]
        if not devices:
            return None
        cache = {}

        def read(d):
            if d.instance == self.device.instance:
                return snap
            if d.instance not in cache:
                got = self.backend.read_extra(d.instance) if hasattr(self.backend, "read_extra") else None
                cache[d.instance] = got if got is not None else bindings.Snapshot()
            return cache[d.instance]

        aux, snaps = {}, {}
        for role, d in devices.items():
            got = read(d)
            if d.instance == self.device.instance:
                counts = (len(snap.axes), len(snap.buttons))
            elif hasattr(self.backend, "extra_counts"):
                counts = self.backend.extra_counts(d.instance)[:2]
            else:
                counts = (0, 0)
            aux[role] = bindings.AuxInfo(d.name, d.vid, d.pid,
                                         max(counts[0], len(got.axes)), max(counts[1], len(got.buttons)))
            snaps[role] = got
        return bindings.live_inputs(self.settings(), snap, aux, snaps)

    def parts(self):
        """The wheel and its extras as merged snapshots lay them out (bindings.merge)."""
        axes, buttons, _ = self.backend.counts
        out = [bindings.Part(self.device.instance, self.device.name, axes, buttons,
                             bindings.device_ref(self.device, self.devices))]
        for d in self.extras():
            a, b, _ = self.backend.extra_counts(d.instance) if hasattr(self.backend, "extra_counts") \
                else (0, 0, 0)
            if a or b:
                out.append(bindings.Part(d.instance, d.name, a, b,
                                         bindings.device_ref(d, self.devices)))
        return out

    def read_parts(self, parts, snap, fallback=None):
        """One merged snapshot of `parts`: the wheel's `snap`, then each extra as read now (an
        unplugged one as in `fallback`, or at rest)."""
        snaps = [snap]
        for i, part in enumerate(parts[1:], 1):
            got = self.backend.read_extra(part.instance) if hasattr(self.backend, "read_extra") else None
            if got is None:
                got = fallback[i] if fallback else bindings.Snapshot()
            snaps.append(got)
        return bindings.merge(parts, snaps), snaps

    def scan(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_scan < 1.0:
            return
        self.last_scan = now
        devices = self.backend.devices()
        if [d.instance for d in devices] == [d.instance for d in self.devices] and not force:
            return
        current = self.device.instance if self.device else None
        self.devices = devices
        # A device saved by its name (an earlier launcher) is saved by its USB ids from now on: the
        # game may read another name for it (#562).
        moved = bindings.migrate_device_settings(self.settings(), devices)
        if moved:
            self.apply(moved)
        self.picker.blockSignals(True)
        self.picker.clear()
        primary = self.primary()
        for d in devices:
            self.picker.addItem(self.kind_label(d, primary), d.instance)
        if not devices:
            self.picker.addItem("No controller or wheel connected", None)
        index = self.picker.findData(current) if current is not None else -1
        if index < 0 and devices:  # the wheel first: it is what most people bind
            index = bindings.primary_wheel(devices, self.settings())
            if index is None:
                index = next((i for i, d in enumerate(devices) if self.kind_of(d) == "wheel"), 0)
        self.picker.setCurrentIndex(max(0, index))
        self.picker.blockSignals(False)
        self.device_chosen()
        self.watch_extras()  # a pedal or shifter device plugged in or out
        self.refresh()

    def device_chosen(self, *_):
        instance = self.picker.currentData()
        device = next((d for d in self.devices if d.instance == instance), None)
        if device is not None and self.device is not None and device.instance == self.device.instance:
            return
        self.cancel("")
        if self.capture:
            self.end_calibration({})
        self.device = device
        self.snap = None
        if device is not None and self.backend is not None and not self.backend.open(device.instance):
            self.device = None
        self.watch_extras()
        self.show_device()

    def wheel(self):
        d = self.device
        axes, buttons, hats = self.backend.counts if self.backend is not None and d else (0, 0, 0)
        return bindings.WheelInfo(d.vid if d else 0, d.pid if d else 0, axes, buttons, hats, IS_WINDOWS)

    def known_wheel(self):
        w = self.wheel()
        # Pedals that rest at the maximum (Logitech); a Thrustmaster's rest is read (#308).
        return bool(bindings.known_logitech(w.vid, w.pid)) and w.num_axes > 3

    # --- rows ---------------------------------------------------------------------------------
    def kind(self):
        return self.kind_of(self.device) if self.device is not None else None

    def actions(self):
        return {"wheel": bindings.WHEEL_ACTIONS,
                "controller": bindings.CONTROLLER_ACTIONS}.get(self.kind(), [])

    def names(self):
        """Button names for this controller's prompts (PlayStation pads have their own)."""
        return PLAYSTATION_NAMES if self.device is not None and self.device.vid == SONY else {}

    def rules(self):
        return bindings.controller_rules(self.settings().get("controller_remap", ""), self.device.guid)

    def lamp_names(self):
        names = {**bindings.BUTTON_NAMES, **LAMP_NAMES, **self.names()}
        return {n: names[n] for n in bindings.PAD_BUTTONS + bindings.EXTRA_BUTTONS}

    def show_device(self):
        while self.table.count():
            widget = self.table.takeAt(0).widget()
            if widget is not None:
                widget.hide()  # gone now, not when the event loop gets to deleting it
                widget.setParent(None)
                widget.deleteLater()
        self.rows = []
        kind = self.kind()
        self.calibrate.setVisible(kind == "wheel")
        self.reset_all.setVisible(kind in ("wheel", "controller"))
        if kind == "wheel":
            name = bindings.known_wheel(self.device.vid, self.device.pid)
            self.intro.setText(
                "Bind a row, then press the button or move the axis you want for it. "
                + (f"A {name} has its own defaults. " if name else
                   "Rows without a binding use what the game detects when it starts (keep the "
                   "pedals released then). ")
                + "Pedals, a shifter or a handbrake on their own USB device bind here too: press "
                  "them when a pedal, gear or handbrake row asks. The handbrake row takes a button "
                  "and an axis, both at once. "
                + "Changes apply the next time the game starts.")
        elif kind in ("pedals", "shifter"):
            what = "These pedals are" if kind == "pedals" else "This shifter is"
            self.intro.setText(
                f"{what} read together with the wheel: choose the wheel above and bind its "
                f"{'pedal' if kind == 'pedals' else 'gear'} rows by pressing "
                f"{'them' if kind == 'pedals' else 'it'}. Its inputs show below.")
        elif kind == "controller":
            self.intro.setText(
                "Bind a row, then press the button, pull the trigger or move the stick you want for "
                "it: a button can work a trigger (a full press) and a trigger or stick direction a "
                "button. These bindings belong to this controller model, so another controller "
                "keeps its own. Changes apply the next time the game starts (SDL input backend).")
        elif kind == "handbrake":
            self.intro.setText(
                "This handbrake is read together with the wheel: choose the wheel above and bind "
                "its Handbrake row by pulling it. Its inputs show below.")
        elif kind == "other":
            self.intro.setText("The game does not use this device (a handbrake): it is read neither "
                               "as a wheel nor as its pedals or shifter.")
        elif kind == "unused":
            self.intro.setText("The game does not use this device: it is neither a controller SDL "
                               "knows nor a wheel. To use it as a wheel, set Racing wheel to On in "
                               "Control settings.")
        else:
            self.intro.setText("Connect a controller or wheel to bind its inputs.")
        section = None
        r = 0
        for action in self.actions():
            if action.section != section:
                section = action.section
                head = label(self.section_title(section), "muted")
                head.setStyleSheet("font-weight: 600; padding-top: 8px;")
                self.table.addWidget(head, r, 0)
                r += 1
            row = BindingRow(action, self.begin, self.clear, self.reset)
            self.rows.append(row)
            self.table.addWidget(row, r, 0)
            r += 1
        self.refresh()

    def section_title(self, section):
        if section == "Shifter":
            mode = self.settings().get("wheel_shifter", "off")
            used = {"h": "gears used (H-pattern)", "sequential": "up and down used (Sequential)"}
            return f"Shifter · {used.get(mode, 'off: choose H-pattern or Sequential in Control settings')}"
        return section

    def binding_text(self, action):
        if self.kind() == "controller":
            source, default = bindings.controller_binding(self.rules(), action)
            text = bindings.source_text(source, self.names())
            return text + (" (default)" if default else "")
        return bindings.wheel_binding_text(action, self.settings(), self.wheel(), self.aux())

    def conflict_list(self):
        if self.kind() == "controller":
            return bindings.controller_conflicts(self.rules(), names=self.names())
        return bindings.wheel_conflicts(self.settings(), self.wheel(), self.aux())

    def label_of(self, action):
        if self.kind() == "wheel":
            return bindings.wheel_action_label(action, self.settings())
        return action.label

    def editable(self, action):
        return self.kind() != "wheel" or bindings.wheel_action_editable(action, self.settings())

    def refresh(self):
        self.label_picker()
        conflicts = self.conflict_list()
        involved = {label_ for _, labels in conflicts for label_ in labels}
        for row in self.rows:
            title = self.label_of(row.action)
            row.title.setText(title)
            row.show_value(self.binding_text(row.action), title in involved)
            busy = self.binding is not None
            editable = self.editable(row.action)
            row.bind.setText("Cancel" if busy and self.binding[0] is row else "Bind")
            row.bind.setEnabled(editable and (not busy or self.binding[0] is row))
            row.clear.setEnabled(editable and not busy)
            row.reset.setEnabled(editable and not busy)
            tip = "" if editable else ("Combined pedals: the throttle row binds the axis for both. "
                                       "Set Pedals to Separate in Control settings to bind the "
                                       "brake on its own.")
            for widget in (row.bind, row.clear, row.reset, row.value):
                widget.setToolTip(tip)
        if conflicts:
            self.conflicts.setText("Bound twice: " + "; ".join(
                f"{what}: {', '.join(labels)}" for what, labels in conflicts))
        self.conflicts.setVisible(bool(conflicts))

    # --- binding ------------------------------------------------------------------------------
    def prompt(self, action):
        if self.kind() == "controller":
            if action.kind == "stick":
                way = "right" if action.field in ("lx", "rx") else "up"
                return f"Move the stick for {action.label} to the {way}"
            return f"Press the button, trigger or stick direction for {action.label}"
        if action.key == "steer":
            return "Turn the wheel to the right"
        if bindings.is_handbrake_row(action):
            return "Pull the handbrake or press the button for it"
        if action.kind == "axis":
            return f"Press the {action.label.lower()} pedal"
        return f"Press the button for {action.label}"

    def begin(self, row):
        if not self.editable(row.action):
            return
        if self.binding is not None:
            if self.binding[0] is row:
                self.cancel("Binding cancelled.")
            return
        if self.snap is None:
            self.status.setText("Select a connected device first.")
            return
        controller = self.kind() == "controller"
        kind = "steer" if row.action.key == "steer" and not controller else row.action.kind
        handbrake = not controller and bindings.is_handbrake_row(row.action)
        if handbrake:
            kind = "any"  # a button or an axis (#303)
        parts, snaps = [], [self.snap]
        baseline = self.snap
        if not controller and (bindings.is_pedal_row(row.action) or bindings.is_gear_row(row.action)
                               or handbrake):
            # Pedals, gears and a handbrake may be on their own USB device (#272, #303).
            parts = self.parts()
            baseline, snaps = self.read_parts(parts, self.snap)
        self.binding = (row, bindings.Detector(baseline, kind, controller),
                        time.monotonic() + BIND_TIMEOUT)
        self.bind_parts = (parts, snaps)
        self.status.setText(f"{self.prompt(row.action)} now (press a button or move an axis). "
                            "Esc cancels.")
        self.refresh()

    def cancel(self, message):
        if self.binding is None:
            return
        self.binding = None
        self.status.setText(message)
        self.refresh()

    def finish(self, row, found, parts=()):
        self.binding = None
        label_ = self.label_of(row.action)
        where = ref = ""
        if found.kind in ("axis", "button") and len(parts) > 1:
            part, number = bindings.split(parts, found.kind, found.number)
            found = dataclasses.replace(found, number=number)
            if part:
                where, ref = parts[part].name, parts[part].setting
        if where and found.kind == "button" and bindings.is_handbrake_row(row.action):
            # The driver reads only the wheel's buttons; on a handbrake device, its axis.
            self.status.setText(f"{label_}: buttons on {where} are not read. Pull the handbrake "
                                "(an axis) or press a button on the wheel.")
            self.refresh()
            return
        self.apply(self.changes_for(row.action, found, where, ref))
        what = bindings.source_text(found.source, self.names()) if found.source else found.describe()
        self.status.setText(f"{label_}: {what}{' on ' + where if where else ''}.")
        self.refresh()

    def remap_changes(self, action, source):
        text = self.settings().get("controller_remap", "")
        return {"controller_remap": bindings.set_controller_binding(text, self.device.guid, action, source)}

    def changes_for(self, action, found, device="", ref=""):
        """The settings that bind `action` to `found`; `device` is the name of the pedal, shifter or
        handbrake device it was found on, `ref` what the settings save for it (its USB ids)."""
        settings = self.settings()
        ref = ref or device
        if found.kind == "source":
            return self.remap_changes(action, found.source)
        if bindings.is_handbrake_row(action):
            changes = bindings.set_handbrake_binding(settings, found.kind, found.number, ref)
            if found.kind == "axis":
                full = 32767 if found.direction > 0 else -32768
                invert, _ = bindings.pedal_calibration(found.rest, full, False)
                changes["wheel_invert_handbrake"] = invert
            return changes
        if device:
            changes = bindings.set_aux_binding(settings, action, ref, found.number, device)
        else:
            changes = bindings.set_row_on_wheel(settings, action, found.number, self.aux())
        if found.kind == "axis":
            if action.key == "steer":
                changes["wheel_invert_steering"] = found.direction < 0
            elif action.key == "throttle" and bindings.combined_pedals(settings):
                # Combined: the driver takes the axis's low side as throttle unless inverted.
                changes["wheel_invert_throttle"] = found.direction > 0
            else:
                full = 32767 if found.direction > 0 else -32768
                invert, _ = bindings.pedal_calibration(found.rest, full,
                                                       self.known_wheel() and not device)
                changes[f"wheel_invert_{action.key}"] = invert
        return changes

    def clear(self, row):
        if not self.editable(row.action):
            return
        label_ = self.label_of(row.action)
        settings = self.settings()
        if self.kind() == "controller":
            changes = self.remap_changes(row.action, None)
        elif bindings.is_handbrake_row(row.action):
            changes = bindings.clear_handbrake(settings)
        elif bindings.row_device(row.action, settings, self.aux()) is not None:
            changes = bindings.set_aux_binding(settings, row.action, None, None)
        else:
            changes = bindings.set_row_on_wheel(settings, row.action, None, self.aux())
        self.apply(changes)
        self.status.setText(f"{label_}: not bound.")
        self.refresh()

    def reset(self, row):
        if not self.editable(row.action):
            return
        self.apply(self.remap_changes(row.action, "default") if self.kind() == "controller"
                   else bindings.reset_row(self.settings(), row.action))
        self.status.setText(f"{self.label_of(row.action)}: back to its default.")
        self.refresh()

    def reset_everything(self):
        if self.binding is not None or self.device is None:
            return
        if self.kind() == "controller":
            text = self.settings().get("controller_remap", "")
            profiles = bindings.parse_remap(text)
            # Removing a profile would reactivate its model or '*' fallback. An empty own
            # profile restores defaults while keeping the other saved profiles intact.
            profiles[self.device.guid.lower()] = {}
            self.apply({"controller_remap": bindings.format_remap(profiles)})
        else:
            self.apply(dict(bindings.RESET_ALL_WHEEL))
        self.status.setText("All bindings are back to their defaults.")
        self.refresh()

    # --- calibration --------------------------------------------------------------------------
    def begin_calibration(self):
        if self.device is None or self.capture:
            return
        self.cancel("")
        self.capture = True
        self.table_box.hide()
        self.conflicts.hide()
        self.calibrate.setEnabled(False)
        self.status.setText("")
        self.cal_parts = self.parts()  # pedals on their own USB device too (#272)
        self.calibration.begin(self.known_wheel(), self.cal_parts)
        self.calibration.show()
        if self.snap is not None:
            self.calibration.feed(self.read_parts(self.cal_parts, self.snap)[0])

    def end_calibration(self, changes):
        self.capture = False
        self.calibration.hide()
        self.table_box.show()
        self.calibrate.setEnabled(True)
        if changes:
            self.apply(changes)
            self.status.setText("Calibration applied.")
        self.refresh()

    def poll(self):
        if self.backend is None:
            return
        self.scan()
        if self.device is None:
            self.view.set_snapshot(None)
            return
        snap = self.backend.read()
        if snap is None:  # unplugged
            self.cancel("The device was disconnected.")
            if self.capture:
                self.end_calibration({})
            self.device = None
            self.scan(force=True)
            return
        self.snap = snap
        self.view.set_snapshot(snap, self.lamp_names() if self.kind() == "controller" else None,
                               self.live_for(snap))
        if self.capture:
            self.calibration.feed(self.read_parts(self.cal_parts, snap)[0] if self.cal_parts else snap)
        if self.binding is not None:
            row, detector, deadline = self.binding
            parts, baseline = self.bind_parts
            merged = self.read_parts(parts, snap, baseline)[0] if parts else snap
            found = detector.feed(merged)
            if found is not None:
                self.finish(row, found, parts)
            elif time.monotonic() > deadline:
                self.cancel("Nothing was pressed: binding cancelled.")
