"""Controller reference diagrams; family selection changes the guide, not game bindings."""
from PySide6.QtCore import QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget, QCheckBox)
from ..haptics.settings import normalize
from ..haptics.service import supported, supports_xbox
from .. import schema

from . import theme
from .bindings_editor import BindingsEditor
from .controller_artwork import compose
from .pages import scroll_page
from .widgets import Disclosure, SpinBox, card, label


FAMILIES = (("xbox", "Xbox"), ("dualsense", "DualSense"), ("dualshock", "DualShock 4"))
XBOX = {
    "left_trigger": "LT", "right_trigger": "RT", "left_shoulder": "LB", "right_shoulder": "RB",
    "a": "A", "b": "B", "x": "X", "y": "Y", "back": "Back", "start": "Start",
    "left_stick": "LS", "right_stick": "RS", "dpad": "D-pad",
}
PLAYSTATION = dict(XBOX, left_trigger="L2", right_trigger="R2", left_shoulder="L1", right_shoulder="R1",
                   a="×", b="○", x="□", y="△", left_stick="L3", right_stick="R3", start="Options")
FACE_NAMES = {"a": "Cross", "b": "Circle", "x": "Square", "y": "Triangle"}
ACTIONS = {
    "right_trigger": ("Accelerate", "Hold to accelerate. The trigger gives you analog throttle control."),
    "left_trigger": ("Brake / reverse", "Hold to brake. From a stop, hold to reverse."),
    "left_stick": ("Steer", "Move left or right to steer. Also navigates menus and the map. "
                   "Hold the stick press to open the in-game friends screen."),
    "right_stick": ("Look around", "Move to look around. Press the stick to look behind you. "
                    "In Photo Mode, move it to control the camera."),
    "a": ("Handbrake / select", "Apply the handbrake while driving. Select in menus; take a picture in Photo Mode."),
    "b": ("Back / shift up", "Go back in menus or resume driving. With manual gears, shift up."),
    "x": ("Enter event / shift down", "Sign up for an event with automatic gears. With manual gears, shift down. "
          "In Photo Mode, adjust focus."),
    "y": ("Rewind", "Rewind when enabled in the game's Difficulty settings. In Photo Mode, open effects."),
    "right_shoulder": ("Change camera", "Cycle through the driving camera views."),
    "left_shoulder": ("Left bumper", "Its action depends on the controller layout and transmission options "
                      "selected in the game's settings."),
    "back": ("Open map", "Open the map. With manual gears, this button is also used to enter an event."),
    "start": ("Pause", "Open the pause menu. Also continues past the title screen."),
    "dpad": ("Radio", "Use the directional pad to control the radio. It also navigates menus."),
}


def button_labels(family):
    if family == "xbox":
        return dict(XBOX)
    return dict(PLAYSTATION, back="Create" if family == "dualsense" else "Share")


def button_name(family, key):
    if family != "xbox" and key in FACE_NAMES:
        return FACE_NAMES[key]
    return button_labels(family)[key]


class ControllerDiagram(QWidget):
    """Licensed controller artwork with transparent, keyboard-accessible hit regions."""
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.family = 'xbox'
        self.artwork = {}
        self.setAccessibleName('Controller diagram. Tab between buttons to read their actions.')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(300)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key in ACTIONS:
            button = QToolButton(self)
            button.setObjectName('controllerButton')
            button.setCheckable(True)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                'QToolButton { background: transparent; border: 2px solid transparent; border-radius: 6px; }'
                f'QToolButton:hover, QToolButton:focus, QToolButton:checked {{ border-color: {theme.ORANGE}; }}')
            button.clicked.connect(lambda _checked=False, key=key: self.selected.emit(key))
            self.group.addButton(button)
            self.buttons[key] = button
        self.buttons['right_trigger'].setChecked(True)
        self.set_family('xbox')

    def sizeHint(self):
        return QSize(640, 300)

    def set_family(self, family):
        self.family = family
        if family not in self.artwork:
            self.artwork[family] = compose(family)
        for key, button in self.buttons.items():
            prompt = button_name(family, key)
            title, description = ACTIONS[key]
            button.setAccessibleName(f'{prompt}: {title}')
            button.setToolTip(f'{prompt} · Xbox {XBOX[key]}\n{description}')
        self.position_buttons()
        self.update()

    def image_rect(self):
        image, _ = self.artwork[self.family]
        scale = min(self.width() / image.width(), (self.height()-16) / image.height())
        width, height = image.width()*scale, image.height()*scale
        return QRectF((self.width()-width)/2, 8, width, height), scale

    def position_buttons(self):
        target, scale = self.image_rect()
        _, regions = self.artwork[self.family]
        for key, region in regions.items():
            x = target.x() + region.center().x()*scale
            y = target.y() + region.center().y()*scale
            width, height = max(24, region.width()*scale), max(24, region.height()*scale)
            self.buttons[key].setGeometry(round(x-width/2), round(y-height/2), round(width), round(height))

    def resizeEvent(self, event):
        self.setFixedHeight(min(300, max(220, round(self.width() * .42))))
        self.position_buttons()
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        image, _ = self.artwork[self.family]
        target, _ = self.image_rect()
        painter.drawPixmap(target, image, QRectF(image.rect()))


class ControlsPage(QWidget):
    settings_requested = Signal()
    haptics_changed = Signal(dict)

    def __init__(self, cfg=None, window=None):
        super().__init__()
        self.cfg, self.window = cfg, window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page('Controls', 'Choose your controller to translate the game’s Xbox button prompts.')
        outer.addWidget(scroll)
        frame, content = card(margins=(24, 20, 24, 20), spacing=14)
        header = QHBoxLayout()
        header.addWidget(label('Controller layout', 'cardTitle'), 1)
        self.family = QComboBox()
        self.family.setAccessibleName('Controller type')
        for key, title in FAMILIES:
            self.family.addItem(title, key)
        self.family.setMinimumWidth(180)
        header.addWidget(self.family)
        content.addLayout(header)
        self.diagram = ControllerDiagram()
        content.addWidget(self.diagram)
        content.addWidget(label('Select a button for its action. You can also Tab between buttons and press Space.', 'help'))
        layout.addWidget(frame)
        frame, content = card(margins=(24, 20, 24, 20), spacing=8)
        self.action = label('', 'cardTitle')
        self.prompt = label('', 'help')
        self.description = label('', 'muted')
        content.addWidget(self.action)
        content.addWidget(self.prompt)
        content.addWidget(self.description)
        layout.addWidget(frame)
        self.key = 'right_trigger'
        self.diagram.selected.connect(self.select_button)
        self.family.currentIndexChanged.connect(self.family_changed)
        self.all_controls = Disclosure('All driving controls')
        self.legend = label('', 'muted')
        self.all_controls.content.addWidget(self.legend)
        credit = QLabel('Controller artwork: <a href="https://github.com/e7d/gamepad-viewer">Gamepad Viewer</a> · MIT license')
        credit.setObjectName('help')
        credit.setOpenExternalLinks(True)
        self.all_controls.content.addWidget(credit)
        layout.addWidget(self.all_controls)
        layout.addWidget(label('Default driving layout. Actions can change with the layout and transmission chosen in the game. '
                               'Selecting a controller here changes this guide; the game still shows Xbox prompts.', 'help'))
        settings = QPushButton('Control settings')
        settings.clicked.connect(self.settings_requested)
        layout.addWidget(settings, 0, Qt.AlignLeft)
        self._local_settings = schema.defaults()
        frame, content = card(margins=(24, 20, 24, 20), spacing=12)
        self.bindings = BindingsEditor(self.current_settings, self.apply_settings)
        self.bindings.settings_requested.connect(self.settings_requested)
        content.addWidget(self.bindings)
        layout.addWidget(frame)
        if cfg is not None:
            self.add_haptics(layout)
        layout.addStretch(1)
        self.family_changed()

    def current_settings(self):
        return self.cfg.settings if self.cfg is not None else self._local_settings

    def apply_settings(self, changes):
        """Store bindings: through the Settings page when there is one (it updates its controls,
        saves and writes forza.toml), else straight into the config."""
        settings_page = getattr(self.window, 'settings', None)
        if settings_page is not None and hasattr(settings_page, 'apply_external'):
            settings_page.apply_external(changes)
            return
        target = self.current_settings()
        for key, value in changes.items():
            target[key] = schema.coerce(schema.BY_KEY[key], value)
        if self.cfg is not None:
            self.cfg.save()

    def captures(self, instance):
        """Launcher navigation ignores a device while it is being bound or calibrated."""
        return self.bindings.captures(instance)

    def showEvent(self, event):
        super().showEvent(event)
        self.bindings.start()

    def hideEvent(self, event):
        self.bindings.stop()
        super().hideEvent(event)

    def add_haptics(self, layout):
        frame, content = card(margins=(24, 20, 24, 20), spacing=16)
        content.addWidget(label('DualSense adaptive triggers', 'cardTitle'))
        content.addWidget(label('DualSense and DualSense Edge · USB or Bluetooth. '
                                'Xbox and DualShock 4 do not have adaptive triggers.', 'help'))
        prefs = normalize(self.cfg.d.get('haptics'))
        self.haptics_enabled = QCheckBox('Enable adaptive triggers while driving')
        self.haptics_enabled.setChecked(prefs['enabled'])
        content.addWidget(self.haptics_enabled)
        self.haptics_fields = {}
        for key, title, maximum, suffix in [('intensity', 'Overall intensity', 100, '%'),
                                           ('throttle_strength', 'Throttle resistance · R2', 8, ''),
                                           ('brake_strength', 'Brake resistance · L2', 8, '')]:
            row = QHBoxLayout()
            row.setSpacing(24)
            row.addWidget(label(title, 'muted'), 1)
            field = SpinBox()
            field.setRange(0, maximum)
            field.setSpecialValueText('Off')
            field.setSuffix(suffix)
            field.setValue(prefs[key])
            field.setMinimumWidth(100)
            field.setAccessibleName(title)
            row.addWidget(field)
            content.addLayout(row)
            self.haptics_fields[key] = field
            field.valueChanged.connect(self.save_haptics)
        self.haptics_status = label('', 'help')
        content.addWidget(self.haptics_status)
        content.addWidget(label('Uses the default driving layout: LT brakes and RT accelerates. '
                                'Set resistance to Off if you use a different trigger assignment. '
                                'Triggers release when telemetry stops. RPM, tire slip, ABS and road effects '
                                'are not available in this first FH1 bridge.', 'help'))
        credit = QLabel(f'Trigger transport: <a style="color:{theme.ORANGE}" '
                        'href="https://github.com/haritha99ch/HorizonHaptics">HorizonHaptics</a> · MIT license')
        credit.setObjectName('help')
        credit.setOpenExternalLinks(True)
        content.addWidget(credit)
        layout.addWidget(frame)
        self.haptics_enabled.toggled.connect(self.save_haptics)
        frame, content = card(margins=(24, 20, 24, 20), spacing=16)
        content.addWidget(label('Xbox trigger vibration', 'cardTitle'))
        content.addWidget(label('Xbox One and Xbox Series controllers with impulse triggers. '
                                'Availability depends on the connection and driver; Xbox 360 controllers '
                                'keep standard game rumble.', 'help'))
        self.xbox_enabled = QCheckBox('Enable trigger vibration while driving')
        self.xbox_enabled.setChecked(prefs['xbox_enabled'])
        content.addWidget(self.xbox_enabled)
        self.xbox_fields = {}
        for key, title in [('xbox_intensity', 'Overall intensity'),
                           ('xbox_throttle', 'Throttle vibration · RT'), ('xbox_brake', 'Brake vibration · LT')]:
            row = QHBoxLayout()
            row.setSpacing(24)
            row.addWidget(label(title, 'muted'), 1)
            field = SpinBox()
            field.setRange(0, 100)
            field.setSuffix('%')
            field.setSpecialValueText('Off')
            field.setValue(prefs[key])
            field.setMinimumWidth(100)
            field.setAccessibleName('Xbox '+title)
            row.addWidget(field)
            content.addLayout(row)
            self.xbox_fields[key] = field
            field.valueChanged.connect(self.save_haptics)
        self.xbox_status = label('', 'help')
        content.addWidget(self.xbox_status)
        content.addWidget(label('Uses LT brake and RT throttle in the default driving layout. '
                                'Normal game rumble stays independent. Trigger vibration stops '
                                'when driving telemetry stops. Updated game builds use engine RPM, '
                                'ABS and tire slip; older builds use trigger pressure.', 'help'))
        layout.addWidget(frame)
        self.xbox_enabled.toggled.connect(self.save_haptics)
        self.haptics_timer = QTimer(self)
        self.haptics_timer.timeout.connect(self.refresh_haptics)
        self.haptics_timer.start(500)
        self.refresh_haptics()

    def save_haptics(self, *_):
        prefs = {'enabled': self.haptics_enabled.isChecked(),
                 'xbox_enabled': self.xbox_enabled.isChecked(),
                 **{key: field.value() for key, field in self.haptics_fields.items()},
                 **{key: field.value() for key, field in self.xbox_fields.items()}}
        self.cfg.d['haptics'] = normalize(prefs)
        self.cfg.save()
        self.haptics_changed.emit(self.cfg.d['haptics'])
        self.refresh_haptics()

    def refresh_haptics(self):
        enabled = self.haptics_enabled.isChecked()
        for field in self.haptics_fields.values():
            field.setEnabled(enabled)
        session = getattr(self.window, 'session', None)
        service = getattr(session, 'haptics', None)
        if service:
            status = service.status
        elif not enabled:
            status = 'Adaptive triggers off'
        elif session and supported(session.plan.cwd):
            status = 'Saved · restart the game to enable adaptive triggers'
        elif session:
            status = 'This game build has no FH1 telemetry bridge. Rebuild the game to enable adaptive triggers.'
        else:
            status = 'Saved · starts with a game build that includes the FH1 telemetry bridge'
        self.haptics_status.setText(status)
        xbox_enabled = self.xbox_enabled.isChecked()
        for field in self.xbox_fields.values():
            field.setEnabled(xbox_enabled)
        if service:
            xbox_status = service.xbox_status
        elif not xbox_enabled:
            xbox_status = 'Xbox trigger vibration off'
        elif session and supports_xbox(session.plan.cwd):
            xbox_status = 'Saved · restart the game to enable Xbox trigger vibration'
        elif session:
            xbox_status = 'Rebuild the game to add Xbox trigger vibration support'
        else:
            xbox_status = 'Saved · starts with a game build that includes Xbox trigger vibration support'
        self.xbox_status.setText(xbox_status)

    def family_changed(self, *_):
        family = self.family.currentData()
        self.diagram.set_family(family)
        self.select_button(self.key)
        self.legend.setText('\n\n'.join(f'{button_name(family, key)}  ·  {title}'
                                        for key, (title, _) in ACTIONS.items()))

    def select_button(self, key):
        self.key = key
        self.diagram.buttons[key].setChecked(True)
        title, description = ACTIONS[key]
        family = self.family.currentData()
        self.action.setText(title)
        self.prompt.setText(f'{button_name(family, key)}  ·  Xbox prompt: {XBOX[key]}')
        self.description.setText(description)
