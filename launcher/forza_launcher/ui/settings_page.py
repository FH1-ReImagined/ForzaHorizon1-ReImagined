"""The Settings page: every forza.toml key plus the launcher's own options, in groups."""
from pathlib import Path

from PySide6.QtCore import QPoint, QRegularExpression, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIntValidator, QRegularExpressionValidator
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QTabBar, QPlainTextEdit,
                               QMessageBox, QPushButton, QRadioButton, QScrollArea, QSlider, QStackedWidget,
                               QToolTip, QVBoxLayout, QWidget)

from .. import PUBLIC_SERVER, display, dlss, fsr3, game, gamertag, gpu, power, schema, specs, vr
from ..config import Config, render_toml, validate_extras
from . import theme
from .settings_search import SettingsSearch
from .widgets import Disclosure, DoubleSpinBox, SpinBox, Switch, card, label

NO_WHEEL = Qt.StrongFocus


class _Wheelless:
    """Spin boxes and combos must not change when the page is scrolled over them."""

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class Combo(_Wheelless, QComboBox):
    def __init__(self):
        super().__init__()
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(18)


class Spin(_Wheelless, SpinBox):
    pass


class DSpin(_Wheelless, DoubleSpinBox):
    pass


DEPENDS = {  # key -> the bool setting that must be on for it to matter
    "mnk_mouse": "mnk_mode", "mnk_sensitivity": "mnk_mode", "mnk_passthrough": "mnk_mode",
    "mnk_stick_ramp": "mnk_mode",
    "discord_presence_details": "discord_presence",
}


class HudSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, low, high, value):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(low), int(high))
        self.number = SpinBox()
        self.number.setRange(int(low), int(high))
        self.number.setSuffix(" %")
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.number)
        self.setMinimumWidth(270)
        self.slider.setValue(int(value))
        self.number.setValue(int(value))
        self.slider.valueChanged.connect(self._changed)
        self.number.valueChanged.connect(self.slider.setValue)

    def _changed(self, value):
        self.number.blockSignals(True)
        self.number.setValue(value)
        self.number.blockSignals(False)
        self.valueChanged.emit(float(value))

    def setValue(self, value):
        self.slider.setValue(int(value))


def make_control(s: schema.Setting, value, on_change, note=None, monitor=0):
    """A widget for the setting, calling on_change(value) when the player changes it. note(value)
    may return a reason that is appended to a choice's label (a GPU's known problem, #52)."""
    if s.kind == "float" and s.key.startswith("forza_hud_"):
        w = HudSlider(s.lo, s.hi, value)
        w.slider.setAccessibleName(s.label)
        w.number.setAccessibleName(s.label + " value")
        w.valueChanged.connect(on_change)
        return w
    if s.kind == "bool":
        w = Switch()
        w.setChecked(bool(value))
        w.toggled.connect(on_change)
        return w
    if s.kind == "int":
        w = Spin()
        w.setRange(int(s.lo if s.lo is not None else -10 ** 9), int(s.hi if s.hi is not None else 10 ** 9))
        w.setSingleStep(int(s.step))
        w.setValue(int(value))
        w.setMinimumWidth(130)
        w.valueChanged.connect(on_change)
        return w
    if s.kind == "float":
        w = DSpin()
        w.setRange(s.lo if s.lo is not None else -1e9, s.hi if s.hi is not None else 1e9)
        w.setDecimals(2)
        w.setSingleStep(s.step)
        w.setValue(float(value))
        w.setMinimumWidth(130)
        w.valueChanged.connect(on_change)
        return w
    if s.kind == "choice":
        w = Combo()
        for v, text in s.choices:
            extra = note(v) if note else ""
            w.addItem(f"{text} ({extra})" if extra else str(text), v)
        i = w.findData(value)
        w.setCurrentIndex(max(i, 0))
        w.setMinimumWidth(190)
        w.currentIndexChanged.connect(lambda _i, w=w: on_change(w.currentData()))
        return w
    if s.kind == "combo":
        w = Combo()
        w.setEditable(True)
        for v, text in s.choices:
            w.addItem(str(text), v)
        i = w.findData(value)
        if i >= 0:
            w.setCurrentIndex(i)
        else:
            w.setEditText(str(value))
        w.setMinimumWidth(190)
        w.lineEdit().editingFinished.connect(lambda w=w: on_change(
            w.itemData(w.currentIndex()) if w.currentIndex() >= 0 and w.currentText() == w.itemText(w.currentIndex())
            else w.currentText().strip()))
        w.currentIndexChanged.connect(lambda i, w=w: on_change(w.itemData(i)) if i >= 0 else None)
        return w
    if s.kind == "fps":
        w = Combo()
        w.setEditable(True)
        w.addItem(display.label(monitor), 0)
        for v in (*schema.LOW_FRAME_RATES, 60, 75, 90, 100, 120, 144, 165, 180, 200, 240):
            w.addItem(f"{v} fps", v)
        w.lineEdit().setValidator(QRegularExpressionValidator(QRegularExpression(r"[1-9]\d{0,2}(?: fps)?")))
        w.setToolTip("Pick a rate or type your own (20, 30, 40, or 60 to 240), such as your display's "
                     "refresh rate.")
        i = w.findData(int(value))
        if i >= 0:
            w.setCurrentIndex(i)
        else:
            w.setEditText(f"{value} fps")
        w.setMinimumWidth(130)

        def changed(_=None, w=w):
            if w.currentIndex() == 0 and w.currentText() == w.itemText(0):
                on_change(0)    # Match display
                return
            digits = "".join(c for c in w.currentText() if c.isdigit())
            if digits:
                rate = schema.coerce(s, int(digits))
                w.setEditText(f"{rate} fps")
                on_change(rate)
        w.currentIndexChanged.connect(changed)
        w.lineEdit().editingFinished.connect(changed)
        return w
    w = QLineEdit(str(value))  # str, keys
    if s.max_length is not None:
        w.setMaxLength(s.max_length)
    w.setMinimumWidth(240)
    if s.key == "gamertag":
        # Limits while typing too: 15 characters, and anything but ASCII letters, digits and
        # spaces is dropped (accents transliterated) as it is typed or pasted.
        w.setMaxLength(gamertag.MAX_LENGTH)

        def gamertag_typed(text, w=w):
            kept = gamertag.filter_typing(text)
            if kept != text:
                pos = w.cursorPosition() - (len(text) - len(kept))
                w.setText(kept)
                w.setCursorPosition(max(0, min(pos, len(kept))))
                QToolTip.showText(w.mapToGlobal(w.rect().bottomLeft()), gamertag.RULE, w, w.rect(), 4000)
        w.textEdited.connect(gamertag_typed)

        def gamertag_done(w=w):
            cleaned, message = gamertag.check_name(w.text())
            if message:
                w.setText(cleaned)
                QToolTip.showText(w.mapToGlobal(w.rect().bottomLeft()), message, w, w.rect(), 8000)
            on_change(cleaned)
        w.editingFinished.connect(gamertag_done)
        return w
    w.editingFinished.connect(lambda w=w: on_change(schema.coerce(s, w.text().strip())))
    return w


class SettingRow(QWidget):
    def __init__(self, s: schema.Setting, cfg: Config, changed):
        super().__init__()
        self.s = s
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 14, 0, 14)
        lay.setSpacing(32)
        col = QVBoxLayout()
        col.setSpacing(8)
        title = QLabel(s.label)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 600;")
        col.addWidget(title)
        if s.help:
            help_text = label(s.help, "help")
            # Long button-map examples must not make every row wider than the viewport.
            help_text.setMinimumWidth(1)
            col.addWidget(help_text)
        self.warning = label("", "")
        self.warning.setStyleSheet(f"color: {theme.ORANGE}; font-size: 12px;")
        self.warning.hide()
        col.addWidget(self.warning)
        self.dependency = label("", "reason")
        self.dependency.hide()
        col.addWidget(self.dependency)
        lay.addLayout(col, 1)
        vendor = cfg.gpu_vendor()
        self.control = make_control(s, cfg.settings.get(s.key, s.default),
                                    lambda v, s=s: changed(s.key, v),
                                    note=lambda v, s=s: gpu.choice_note(vendor, s.key, v),
                                    monitor=cfg.settings.get("monitor", 0))
        tip = f"{s.key}" + (f" (default {s.default!r})" if s.default != "" else "")
        self.control.setToolTip(tip)
        self.control.setAccessibleName(s.label)
        title.setToolTip(tip)
        lay.addWidget(self.control, 0, Qt.AlignRight | Qt.AlignVCenter)

    def set_warning(self, text):
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def set_available(self, available, reason=""):
        self.control.setEnabled(available)
        self.dependency.setText(reason if not available else "")
        self.dependency.setVisible(not available and bool(reason))


def hline():
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.BORDER};")
    return f


def section_card(lay, title, index=None):
    frame, content = card(margins=(24, 20, 24, 20), spacing=16)
    content.addWidget(label(title, "cardTitle"))
    if index is None:
        lay.addWidget(frame)
    else:
        lay.insertWidget(index, frame)
    return content


class SettingsPage(QWidget):
    changed = Signal()
    rebuilt = Signal()

    def __init__(self, cfg: Config, window):
        super().__init__()
        self.cfg = cfg
        self.window = window
        self.rows = {}
        self.section_contents = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self.flush)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        self.search = SettingsSearch(self)
        self.search.chosen.connect(self._show_search_result)
        root.addWidget(self.search)
        self._specs_banner(root)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setAccessibleName("Settings categories")
        self.stack = QStackedWidget()
        root.addWidget(self.tabs)
        root.addWidget(self.stack, 1)
        for g in schema.GROUPS:
            self.tabs.addTab(g)
            self.stack.addWidget(self._build_group(g))
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.tabs.currentChanged.connect(self.search.dismiss)
        self.tabs.setCurrentIndex(0)
        self._apply_dependencies()

    # --- plumbing -------------------------------------------------------------------
    def _show_search_result(self, target):
        index = schema.GROUPS.index(target.group)
        self.tabs.setCurrentIndex(index)
        # Let the newly shown category finish its wrapped-text layout before scrolling.
        QTimer.singleShot(0, self, lambda: self._focus_search_target(target))

    def _focus_search_target(self, target):
        if not any(item is target for item in self.search.targets):
            return  # Reset/GPU changes may have rebuilt the controls while queued.
        scroll = self.stack.currentWidget()
        if self.tabs.tabText(self.tabs.currentIndex()) != target.group:
            return
        scroll.widget().layout().activate()
        if target.focus.isEnabled():
            target.focus.setFocus(Qt.ShortcutFocusReason)
        scroll.verticalScrollBar().setValue(target.widget.mapTo(scroll.widget(), QPoint()).y() - 32)
        # QScrollArea may pan a wide row sideways even with its horizontal bar hidden.
        scroll.horizontalScrollBar().setValue(0)

    def set_value(self, key, value):
        self.cfg.settings[key] = schema.coerce(schema.BY_KEY[key], value)
        if key in ("fh1_upscaler", "fh1_output_resolution", "forza_antialiasing") and dlss.enabled(self.cfg):
            resolution, _, _, _ = dlss.output_settings(self.cfg)
            for dep, fixed in (("gpu_backend", "vulkan"), ("resolution", resolution)):
                self.cfg.settings[dep] = fixed
                row = self.rows.get(dep)
                if row:
                    control = row.control
                    control.blockSignals(True)
                    control.setCurrentIndex(control.findData(fixed))
                    control.blockSignals(False)
        # Scene reconstruction already includes AA. Keep the selectors exclusive.
        if key in ("fh1_upscaler", "present_effect") and value not in ("off", "bilinear", "fsr", "cas"):
            self.cfg.settings["forza_antialiasing"] = "game"
            row = self.rows.get("forza_antialiasing")
            if row:
                row.control.blockSignals(True)
                row.control.setCurrentIndex(row.control.findData("game"))
                row.control.blockSignals(False)
        if key == "forza_ultrawide" and value and "gpu_backend" in self.rows:
            self.cfg.settings["gpu_backend"] = "vulkan"
            control = self.rows["gpu_backend"].control
            control.blockSignals(True)
            control.setCurrentIndex(control.findData("vulkan"))
            control.blockSignals(False)
        scene_aa = self.cfg.settings.get("forza_antialiasing", "game") != "game"
        scaling = self.cfg.settings.get("fh1_upscaler", "off") != "off" or self.cfg.settings.get("present_effect") == "fsr3"
        if scene_aa or scaling:
            self.cfg.settings["swap_post_effect"] = "none"
            row = self.rows.get("swap_post_effect")
            if row:
                row.control.blockSignals(True)
                row.control.setCurrentIndex(row.control.findData("none"))
                row.control.blockSignals(False)
        if key == "monitor" and "frame_rate" in self.rows:    # Match display names the new monitor's rate
            control = self.rows["frame_rate"].control
            selected = control.currentIndex() == 0
            control.setItemText(0, display.label(value))
            if selected:
                control.setCurrentIndex(0)
                control.setEditText(control.itemText(0))
        self._apply_dependencies()
        self.touched()

    def apply_external(self, changes):
        """Values set elsewhere (the Controls page's bindings): store them, show them in their
        controls and save."""
        for key, value in changes.items():
            s = schema.BY_KEY[key]
            value = schema.coerce(s, value)
            self.cfg.settings[key] = value
            row = self.rows.get(key)
            if row is None:
                continue
            control = row.control
            control.blockSignals(True)
            if isinstance(control, QLineEdit):
                control.setText(str(value))
            elif hasattr(control, "setChecked"):
                control.setChecked(bool(value))
            elif hasattr(control, "findData") and s.kind == "choice":
                control.setCurrentIndex(max(0, control.findData(value)))
            elif hasattr(control, "setValue"):
                control.setValue(value)
            control.blockSignals(False)
        self._apply_dependencies()
        self.touched()

    def touched(self):
        self._save_timer.start()
        self.refresh_preview()

    def flush(self):
        """Save launcher.json and write forza.toml."""
        self._save_timer.stop()
        try:
            self.cfg.save()
        except OSError as e:
            self.window.status(f"Could not save launcher settings: {e}", True)
        self.window.write_toml()
        self.changed.emit()

    def _apply_dependencies(self):
        discord = self.rows.get("discord_presence")
        if discord:
            identifier = str(self.cfg.settings.get("discord_application_id", ""))
            valid = (17 <= len(identifier) <= 20 and identifier.isascii() and identifier.isdigit()
                     and not identifier.startswith("0") and int(identifier) <= 2 ** 64 - 1)
            discord.warning.setText("Discord sharing needs a valid public Application ID. "
                                    "Add it under Advanced > Discord setup.")
            discord.warning.setVisible(bool(self.cfg.settings.get("discord_presence")) and not valid)
        active = dlss.enabled(self.cfg)
        temporal = fsr3.enabled(self.cfg)
        ultrawide = bool(self.cfg.settings.get("forza_ultrawide"))
        states = {}
        for key in ("fh1_upscaler", "fh1_output_resolution"):
            if key in self.rows:
                self.rows[key].setVisible(self.cfg.gpu_vendor() == gpu.NVIDIA)
        states["fh1_output_resolution"] = (active, "Choose a DLSS or DLAA mode to set its output resolution.")
        for key in ("gpu_backend", "resolution", "resolution_scale", "present_effect"):
            states[key] = (not active, "Controlled by NVIDIA DLSS / DLAA. Turn it off to change this setting.")
        for key in ("fsr3_mode", "fsr3_output_resolution"):
            states[key] = (temporal, "Select AMD FSR 3 as the upscaling filter to use this setting.")
        for key in ("resolution", "resolution_scale"):
            if temporal:
                states[key] = (False, "Controlled by AMD FSR 3. Use the FSR 3 output resolution setting.")
        for key in ("gpu_backend", "resolution_scale"):
            if temporal:
                states[key] = (False, "Controlled by AMD FSR 3. Choose another upscaling filter to change this setting.")
            elif ultrawide and not active:
                states[key] = (False, "Controlled by Ultrawide gameplay. Turn it off to change this setting.")
        for key in ("forza_hud_safe_width", "forza_hud_safe_height", "forza_hud_offset_x", "forza_hud_offset_y"):
            states[key] = (bool(self.cfg.settings.get("forza_hud_layout")), "Enable Customize HUD layout to adjust this setting.")
        shifter = self.cfg.settings.get("wheel_shifter", "off")
        states["wheel_invert_clutch"] = (bool(self.cfg.settings.get("wheel_clutch", True)),
                                         "Turn on Clutch pedal to adjust this setting.")
        states["wheel_shifter_buttons"] = (shifter != "off", "Choose a shifter to set its buttons.")
        for key in ("wheel_shifter_device", "wheel_shifter_device_buttons"):
            states[key] = (shifter != "off", "Choose a shifter to set its device.")
        states["wheel_pedal_axes"] = (
            str(self.cfg.settings.get("wheel_pedal_device", "")).strip().lower() not in ("none", "off", "wheel"),
            "Set Pedal device to a device (or empty) to set its axes.")
        states["wheel_shifter_clutch"] = (shifter == "h", "Choose the H-pattern shifter to use this setting.")
        states["wheel_shifter_follow_slot"] = (shifter == "h",
                                               "Choose the H-pattern shifter to use this setting.")
        states["wheel_handbrake_axis"] = (
            str(self.cfg.settings.get("wheel_handbrake_device", "")).strip().lower() not in ("none", "off", "wheel"),
            "Set Handbrake device to a device (or empty) to set its axis.")
        states["wheel_clutch_threshold"] = (
            shifter == "h" and bool(self.cfg.settings.get("wheel_shifter_clutch")),
            "Turn on Gears need the clutch (H-pattern shifter) to adjust this setting.")

        hdr_supported = self.cfg.settings.get("gpu_backend", "vulkan") == "vulkan"
        states["present_hdr"] = (hdr_supported, "HDR output requires Vulkan.")
        states["texture_replace"] = (hdr_supported, "Texture packs require Vulkan.")
        for key in self.rows:
            if key.startswith("present_hdr_"):
                states[key] = (hdr_supported and bool(self.cfg.settings.get("present_hdr")),
                               "Enable HDR output with Vulkan to adjust this setting.")

        for key, dep in DEPENDS.items():
            states[key] = (bool(self.cfg.settings.get(dep)), "")
        scaling = self.cfg.settings.get("fh1_upscaler", "off") != "off" or self.cfg.settings.get("present_effect") == "fsr3"
        supported = self.cfg.settings.get("gpu_backend", "vulkan") == "vulkan" and not ultrawide
        aa_reason = ("Upscaling includes anti-aliasing; turn it off to choose a separate method." if scaling else
                     "Separate scene AA currently requires Vulkan and standard 16:9 output." if not supported else "")
        states["forza_antialiasing"] = (not scaling and supported, aa_reason)
        aa_row = self.rows.get("forza_antialiasing")
        if aa_row:
            model = aa_row.control.model()
            for index in range(aa_row.control.count()):
                item = model.item(index)
                if item:
                    item.setEnabled(aa_row.control.itemData(index) != "dlaa" or self.cfg.gpu_vendor() == gpu.NVIDIA)
            aa_row.control.setToolTip(aa_reason or schema.BY_KEY["forza_antialiasing"].help)
        states["swap_post_effect"] = (
            not active and not temporal and self.cfg.settings.get("forza_antialiasing", "game") == "game",
            "Disable upscaling and select Game MSAA to use this setting.")
        scene_aa = (self.cfg.settings.get("gpu_backend", "vulkan") == "vulkan" and
                    self.cfg.settings.get("forza_antialiasing", "game") != "game")
        scene_reason = "Native AA renders at output resolution; this setting only affects Game MSAA."
        if scene_aa:
            states["resolution_scale"] = (False, scene_reason)
        if active or temporal or ultrawide or scene_aa:
            states["scene_resolution"] = (False, "NVIDIA DLSS, AMD FSR 3, a separate anti-aliasing method and "
                                                 "Ultrawide gameplay keep the 3D scene at 720p or more.")
        if "resolution_scale" in self.rows:
            self.rows["resolution_scale"].control.setToolTip(
                scene_reason if scene_aa else schema.BY_KEY["resolution_scale"].help)
        states["vulkan_store_pipeline_cache"] = (
            bool(self.cfg.settings.get("store_shaders")) and
            self.cfg.settings.get("gpu_backend", "vulkan") == "vulkan",
            "Enable shader storage with Vulkan to keep compiled pipelines.")
        for s in schema.S:
            if s.key.startswith("keybind_"):
                states[s.key] = (bool(self.cfg.settings.get("mnk_mode")), "")
        for key, (available, reason) in states.items():
            if key in self.rows:
                self.rows[key].set_available(available, reason)
        for row in self.rows.values():
            enabled = row.control.isEnabled()
            if row.control.property("dependencyEnabled") != enabled:
                row.control.setProperty("dependencyEnabled", enabled)
                effect = row.graphicsEffect()
                if effect is None and not enabled:
                    effect = QGraphicsOpacityEffect(row)
                    row.setGraphicsEffect(effect)
                if effect is not None:
                    effect.setOpacity(1.0 if enabled else 0.55)
                row.update()
                for widget in [row.control, *row.control.findChildren(QWidget)]:
                    style = widget.style()
                    style.unpolish(widget)
                    style.polish(widget)
                    widget.update()
        self._refresh_gpu_warnings()

    def _rows(self, lay, group, skip=()):
        section = None
        for s in schema.for_group(group):
            if s.key in skip:
                continue
            if s.section != section:
                section = s.section
                content = section_card(lay, section)
                content.setSpacing(0)
                self.section_contents[section] = content
            else:
                content.addWidget(hline())
            row = SettingRow(s, self.cfg, self.set_value)
            self.rows[s.key] = row
            content.addWidget(row)
            self.search.add_setting(s, row)

    def _page(self, title, lead):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 14, 20)
        lay.setSpacing(20)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        t.setStyleSheet("font-size: 22px;")
        lay.addWidget(t)
        if lead:
            lay.addWidget(label(lead, "pageLead"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        return scroll, lay

    # --- groups ---------------------------------------------------------------------
    def _build_group(self, g):
        leads = {
            "Display": "Window, frame rate and picture.",
            "VR": "Experimental: playable, but expect rough edges. Play with a VR headset: "
                  "press Play in VR on the Play page.",
            "Controls": "Use a controller, or enable keyboard and mouse and customize your bindings below.",
            "Gameplay": "How the game plays in single player. Online games always play as shipped.",
            "Audio": "",
            "Online": "Xbox LIVE is gone, so the game signs in to a Forza Live server instead.",
            "Profile": "Your saves live in the game's data folder. You can also continue a Xenia Canary save.",
            "Advanced": "For testing and troubleshooting. Everything here is also a forza.toml key or a command-line flag.",
        }
        scroll, lay = self._page(g, leads[g])
        if g == "Display":
            self._gpu_block(lay)
        if g == "VR":
            self._vr_status_block(lay)
        if g == "Online":
            self._online_block(lay)
        if g == "Profile":
            self._profile_block(lay)
        self._rows(lay, g)
        if g == "VR":
            self._vr_extras_block(lay)
        if g == "Display":
            self._texture_pack_block()
        if g == "Controls":
            lay.addWidget(label("Everything on this page is a forza.toml key; the keyboard bindings "
                                "use the SDK's key names.", "help"))
        if g == "Controls":
            self._pad_block(lay)
        if g == "Advanced":
            self._advanced_block(lay)
        lay.addStretch(1)
        return scroll

    # --- VR (vr.py) -----------------------------------------------------------------
    def _vr_status_block(self, lay):
        runtime = vr.detect()
        self.vr_status = label("", "")
        self.vr_status.setTextFormat(Qt.RichText)
        self.vr_status.setOpenExternalLinks(True)
        self.vr_status.setTextInteractionFlags(Qt.TextBrowserInteraction)
        if runtime is not None:
            self.vr_status.setText(f"\u2713 {runtime.name} found")
        else:
            self.vr_status.setText(
                "No VR app found: install "
                f"<a href=\"{vr.WIVRN_URL}\">WiVRn</a> (Linux) or "
                f"<a href=\"{vr.STEAMVR_URL}\">SteamVR</a> / Meta Quest Link (Windows).")
        frame, content = card(margins=(24, 16, 24, 16), spacing=6)
        content.addWidget(self.vr_status)
        lay.addWidget(frame)

    def _vr_extras_block(self, lay):
        content = self.section_contents.get("VR (experimental)")
        if content is None:
            return
        content.addWidget(hline())
        content.addWidget(label("Recentre: Home key, or hold both sticks for a second.", "help"))
        advanced = Disclosure("Advanced: what VR changes")
        advanced.content.addWidget(label(
            "Play in VR sets these for that session only; your normal settings are not changed.", "help"))
        for name, value, _key in vr.SESSION_OVERRIDES:
            advanced.content.addWidget(label(f"{name}: {value}", "help"))
        self.vr_advanced = advanced
        content.addWidget(advanced)

    # --- texture packs (#384) -------------------------------------------------------
    def _texture_pack_block(self):
        content = self.section_contents.get("Texture packs")
        if content is None:
            return
        content.addWidget(hline())
        row = QHBoxLayout()
        row.setContentsMargins(0, 14, 0, 0)
        folder = game.texture_pack_folder(self.cfg)
        self.texture_folder_button = QPushButton("Open textures folder")
        self.texture_folder_button.setToolTip(str(folder))
        self.texture_folder_button.clicked.connect(self._open_texture_folder)
        row.addWidget(self.texture_folder_button)
        self.texture_page_button = QPushButton("Texture packs page…")
        self.texture_page_button.setToolTip("Add, order and turn packs on or off, and make an HD pack "
                                            "from your dump.")
        self.texture_page_button.clicked.connect(self._show_texture_packs)
        row.addWidget(self.texture_page_button)
        row.addStretch(1)
        content.addLayout(row)

    def _show_texture_packs(self):
        from .window import TEXTURE_PACKS_PAGE_ID
        show = getattr(self.window, "show_page", None)
        if show is not None:
            self.flush()
            show(TEXTURE_PACKS_PAGE_ID)

    def _open_texture_folder(self):
        folder = game.texture_pack_folder(self.cfg)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Texture packs", f"Could not create {folder}: {e}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --- graphics card (#52) --------------------------------------------------------
    def _gpu_block(self, lay):
        lay = section_card(lay, "Graphics card")
        vendor = self.cfg.gpu_vendor()
        self.gpu_combo = Combo()
        for v in gpu.VENDORS:
            self.gpu_combo.addItem(gpu.label(v), v)
        self.gpu_combo.addItem(gpu.label(""), "")
        self.gpu_combo.setCurrentIndex(max(self.gpu_combo.findData(vendor), 0))
        self.gpu_combo.setMinimumWidth(190)
        row = QHBoxLayout()
        row.setSpacing(32)
        col = QVBoxLayout()
        col.setSpacing(8)
        t = QLabel("Your graphics card")
        t.setStyleSheet("font-weight: 600;")
        col.addWidget(t)
        self.gpu_summary = label("", "help")
        col.addWidget(self.gpu_summary)
        row.addLayout(col, 1)
        row.addWidget(self.gpu_combo, 0, Qt.AlignRight | Qt.AlignVCenter)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 6, 0, 6)
        lay.addWidget(w)
        self.gpu_notes = label("", "help")
        lay.addWidget(self.gpu_notes)
        self.specs_line = label("", "help")
        lay.addWidget(self.specs_line)
        self.gpu_apply = QPushButton("Use the recommended settings for this card")
        self.gpu_apply.clicked.connect(lambda: self._gpu_chosen(self.gpu_combo.currentData(), True))
        lay.addWidget(self.gpu_apply, 0, Qt.AlignLeft)
        self.gpu_combo.activated.connect(lambda _i: self._gpu_chosen(self.gpu_combo.currentData(), False))
        recheck = QPushButton("Check this PC again")
        recheck.clicked.connect(self._recheck_specs)
        lay.addWidget(recheck, 0, Qt.AlignLeft)
        self._gpu_texts()
        self.search.add("Your graphics card", "Display", "Graphics card", w, self.gpu_combo,
                        "GPU vendor NVIDIA AMD Intel recommended settings")

    def _gpu_texts(self):
        vendor = self.cfg.gpu_vendor()
        p = gpu.preset(vendor)
        detected = self.cfg.gpu.get("detected", "")
        found = (f"Detected: {self.cfg.gpu.get('name') or gpu.label(detected)}. " if detected else "Not detected. ")
        self.gpu_summary.setText(found + "Known problems of a card are marked on the options below; "
                                 "your own choices are never changed behind your back.")
        lines = ([f"Recommended: {p.summary}"] if p else
                 ["No recommendation for this card: the launcher's general defaults apply."])
        for text, issue in (p.notes if p else []):
            lines.append(f"{text}{gpu.issue_text(issue)}")
        pc = self.cfg.pc_specs()
        self.specs_line.setText(specs.describe(pc))
        lines += [w.text for w in specs.warnings(pc, {}) if not w.key]    # about the PC, not a setting
        self.gpu_notes.setText("\n".join(lines))
        self.gpu_apply.setEnabled(p is not None)

    def _gpu_chosen(self, vendor, force_apply):
        p = gpu.preset(vendor)
        apply_ = force_apply
        if not force_apply and p is not None:
            names = [schema.BY_KEY[k].label for k in gpu.settings_for(vendor)
                     if k in schema.BY_KEY and schema.BY_KEY[k].applies()]
            apply_ = QMessageBox.question(
                self, "Graphics card", f"Use the recommended settings for {p.label} now?\n\n{p.summary}\n\n"
                f"This changes {', '.join(names)}; every other setting stays as it is.") == QMessageBox.Yes
        self.cfg.set_gpu_vendor(vendor, apply_)
        if self.cfg.settings.get("fh1_upscaler", "off") != "off":
            self.set_value("fh1_upscaler", self.cfg.settings["fh1_upscaler"])
        self.touched()
        self.rebuild()
        self.window.status(f"Graphics card: {gpu.label(vendor)}" +
                           (" (recommended settings applied)" if apply_ else ""), False)

    def _refresh_gpu_warnings(self):
        vendor = self.cfg.gpu_vendor()
        found = {}
        warning_settings = self.cfg.effective_settings()
        if (dlss.enabled(self.cfg) and warning_settings.get("fh1_upscaler", "off") in ("quality", "balanced", "dlss") and
                warning_settings.get("fh1_output_resolution") == "4k"):
            # The 3x allocation belongs to the native UI; these modes render
            # the scene at 2x, so the ordinary 3x-scene warning does not apply.
            warning_settings["resolution_scale"] = 2
        for key, text, issue in gpu.warnings(vendor, warning_settings):
            found.setdefault(key, []).append(text + gpu.issue_text(issue))
        vendor_keys = set(found)    # the vendor's own warning on a setting says it already
        for w in specs.warnings(self.cfg.pc_specs(), warning_settings):
            if w.key and w.key not in vendor_keys:
                found.setdefault(w.key, []).append(w.text)
        saver = power.setting_warning(self.cfg.settings.get("battery_saver", power.OFF))
        if saver:
            found.setdefault("battery_saver", []).append(saver)
        for key, row in self.rows.items():
            if key == "discord_presence":
                continue  # its setup warning is managed by _apply_dependencies
            row.set_warning(" ".join(found.get(key, [])))
        self._refresh_banner()

    # --- settings heavy for this PC (#206) --------------------------------------------
    def _specs_banner(self, root):
        self.banner = QFrame()
        self.banner.setObjectName("specsBanner")
        self.banner.setStyleSheet(f"#specsBanner {{ border: 1px solid {theme.ORANGE}; border-radius: 8px; }}")
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        self.banner_text = label("Some settings are heavy for this PC.", "")
        row.addWidget(self.banner_text, 1)
        use = QPushButton("Use recommended")
        use.clicked.connect(self._use_recommended)
        keep = QPushButton("Keep mine")
        keep.clicked.connect(self._keep_mine)
        row.addWidget(use)
        row.addWidget(keep)
        self.banner.hide()
        root.addWidget(self.banner)

    def _refresh_banner(self):
        due = self.cfg.specs_banner_due()
        if due:
            self.banner_text.setText(self.cfg.specs_banner_text())
        self.banner.setVisible(due)

    def _use_recommended(self):
        changed = self.cfg.use_recommended()
        self.touched()
        self.rebuild()
        self.window.status("Recommended settings applied: " + ", ".join(
            schema.BY_KEY[k].label for k in changed) if changed else "Nothing to change", False)

    def _keep_mine(self):
        self.cfg.dismiss_specs_banner()
        self.touched()
        self._refresh_banner()

    def _recheck_specs(self):
        self.cfg.detect_specs(force=True)
        self.touched()
        self.rebuild()
        self.window.status("Checked this PC again", False)

    def _pad_block(self, lay):
        lay = section_card(lay, "Players", 2)
        row = QHBoxLayout()
        row.setSpacing(32)
        sw = Switch()
        sw.setChecked(bool(self.cfg.advanced.get("pad_per_user")))
        sw.toggled.connect(lambda v: self._adv("pad_per_user", v))
        col = QVBoxLayout()
        col.setSpacing(8)
        t = QLabel("Each controller is its own player")
        t.setStyleSheet("font-weight: 600;")
        col.addWidget(t)
        col.addWidget(label("Off (recommended): every controller drives player 1, so START on any pad "
                            "gets past the title screen. Sets FORZA_PAD_PER_USER=1 when on.", "help"))
        row.addLayout(col, 1)
        row.addWidget(sw)
        w = QWidget()
        w.setLayout(row)
        lay.addWidget(w)

        self.search.add("Each controller is its own player", "Controls", "Players", w, sw,
                        "pad_per_user FORZA_PAD_PER_USER controller routing player 1")

    def _adv(self, key, value):
        self.cfg.advanced[key] = value
        self.touched()

    # Online
    def _online_block(self, lay):
        lay = section_card(lay, "Server")
        self.mode_group = QButtonGroup(self)
        modes = [
            ("public", f"Public server", f"{PUBLIC_SERVER}: the shared online world, leaderboards and "
                                          "rivals. You forward no ports."),
            ("local", "This PC", "Starts a Forza Live server just for this session (127.0.0.1:21200). "
                                  "Online features work, but only you are in it."),
            ("custom", "My own server", "A server you or a friend host (forza-live's docs/hosting.md on GitHub)."),
            ("offline", "Offline", "No Xbox LIVE and no server."),
        ]
        self.radios = {}
        for key, title, text in modes:
            rb = QRadioButton(title)
            rb.setStyleSheet("font-weight: 600;")
            self.mode_group.addButton(rb)
            self.radios[key] = rb
            lay.addWidget(rb)
            hint = label(text, "help")
            hint.setContentsMargins(26, 0, 0, 6)
            lay.addWidget(hint)
            rb.toggled.connect(lambda on, k=key: on and self._set_mode(k))
        self.custom = QLineEdit(self.cfg.online.get("custom_server", ""))
        self.custom.setPlaceholderText("host[:port], for example example.org:21200")
        self.custom.editingFinished.connect(self._custom_changed)
        lay.addWidget(self.custom)
        self.radios[self.cfg.online["mode"]].setChecked(True)
        self.custom.setEnabled(self.cfg.online["mode"] == "custom")
        self.search.add("Online server", "Online", "Server", self.radios["public"],
                        keywords="public local offline Xbox LIVE Forza Live")
        self.search.add("Custom server address", "Online", "Server", self.custom,
                        keywords="host port custom_server")

    def _set_mode(self, mode):
        self.cfg.online["mode"] = mode
        self.custom.setEnabled(mode == "custom")
        self.touched()

    def _custom_changed(self):
        self.cfg.online["custom_server"] = self.custom.text().strip()
        self.touched()

    # Profile
    def _profile_block(self, lay):
        lay = section_card(lay, "Save")
        self.own = QRadioButton("My own profile")
        self.own.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.own)
        h = label(f"Saves in {self.cfg.data_home() / game.LOCAL_PROFILE / game.TITLE_ID}", "help")
        h.setContentsMargins(26, 0, 0, 6)
        lay.addWidget(h)
        self.xenia = QRadioButton("Continue a Xenia Canary save")
        self.xenia.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.xenia)
        h = label("Copies your Xenia profile's Forza Horizon save once and plays the copy, signed in "
                  "with that profile's XUID. The Xenia original is never modified.", "help")
        h.setContentsMargins(26, 0, 0, 6)
        lay.addWidget(h)
        form = QWidget()
        f = QVBoxLayout(form)
        f.setContentsMargins(0, 8, 0, 0)
        f.setSpacing(16)
        f.addWidget(QLabel("Xenia content folder"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.xcontent = QLineEdit(self.cfg.profile.get("xenia_content", ""))
        self.xcontent.setPlaceholderText(str(game.default_xenia_content()))
        b = QPushButton("Browse")
        b.clicked.connect(self._browse_xenia)
        row.addWidget(self.xcontent, 1)
        row.addWidget(b)
        f.addLayout(row)
        f.addWidget(QLabel("Profile (XUID)"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.xuid = Combo()
        self.xuid.setEditable(True)
        self.xuid.setMinimumWidth(240)
        row.addWidget(self.xuid, 1)
        scan = QPushButton("Find profiles")
        scan.clicked.connect(self._scan_xenia)
        row.addWidget(scan)
        f.addLayout(row)
        self.recopy = QPushButton("Copy the save again on the next start")
        self.recopy.setCheckable(True)
        f.addWidget(self.recopy)
        self.xenia_note = label("", "help")
        f.addWidget(self.xenia_note)
        lay.addWidget(form)
        self.xform = form
        (self.xenia if self.cfg.profile["mode"] == "xenia" else self.own).setChecked(True)
        self.xform.setEnabled(self.cfg.profile["mode"] == "xenia")
        self.xenia.toggled.connect(self._profile_mode)
        self.xcontent.editingFinished.connect(self._xenia_fields)
        self.xuid.lineEdit().editingFinished.connect(self._xenia_fields)
        self.xuid.setEditText(self.cfg.profile.get("xenia_xuid", ""))
        self._scan_xenia(quiet=True)
        self.search.add("Save profile", "Profile", "Save", self.xenia,
                        keywords="own Xenia Canary import continue save")
        self.search.add("Xenia content folder", "Profile", "Save", self.xcontent)
        self.search.add("Profile (XUID)", "Profile", "Save", self.xuid,
                        keywords="Xenia account")
        self.search.add("Copy the save again on the next start", "Profile", "Save", self.recopy,
                        keywords="Xenia recopy import")

    def _profile_mode(self, on):
        self.cfg.profile["mode"] = "xenia" if on else "own"
        self.xform.setEnabled(on)
        self.touched()

    def _browse_xenia(self):
        d = QFileDialog.getExistingDirectory(self, "Xenia content folder",
                                             self.xcontent.text() or str(game.default_xenia_content().parent))
        if d:
            self.xcontent.setText(d)
            self._xenia_fields()
            self._scan_xenia()

    def _xenia_fields(self):
        self.cfg.profile["xenia_content"] = self.xcontent.text().strip()
        self.cfg.profile["xenia_xuid"] = self.xuid.currentText().strip().upper()
        self.touched()

    def _scan_xenia(self, quiet=False):
        content = Path(self.xcontent.text().strip() or game.default_xenia_content())
        found = game.xenia_candidates(content)
        keep = self.xuid.currentText()
        self.xuid.clear()
        self.xuid.addItems(found)
        self.xuid.setEditText(keep or (found[0] if len(found) == 1 else ""))
        if found:
            self.xenia_note.setText(f"Found {len(found)} profile(s) with a Forza Horizon save in {content}.")
        else:
            self.xenia_note.setText(f"No Forza Horizon save found in {content}.")
        self._xenia_fields() if not quiet else None

    # Advanced
    def _advanced_block(self, lay):
        adv = self.cfg.advanced
        launcher = section_card(lay, "Launcher", 2)

        def line(title, help_, key, placeholder="", browse=False):
            w = QWidget()
            body = QVBoxLayout(w)
            body.setContentsMargins(0, 8, 0, 8)
            body.setSpacing(14)
            h = QHBoxLayout()
            h.setSpacing(12)
            col = QVBoxLayout()
            col.setSpacing(8)
            t = QLabel(title)
            t.setStyleSheet("font-weight: 600;")
            col.addWidget(t)
            col.addWidget(label(help_, "help"))
            body.addLayout(col)
            body.addLayout(h)
            e = QLineEdit(adv.get(key, ""))
            e.setPlaceholderText(placeholder)
            e.setMinimumWidth(280)
            self.search.add(title, "Advanced", "Launcher", w, e, key + " " + help_)
            if key == "data_root":
                # the same checked flow as Setup > Where the game is built: free space, OneDrive,
                # and the choice to copy saves and files (nothing is deleted)
                e.setText(str(self.cfg.data_home()))
                e.setReadOnly(True)
                b = QPushButton("Change folder")

                def change(_=False, e=e):
                    self.window.setup.change_folder()
                    e.setText(str(self.cfg.data_home()))
                b.clicked.connect(change)
                h.addWidget(e)
                h.addWidget(b)
                return w
            e.editingFinished.connect(lambda e=e: self._adv(key, e.text().strip()))
            h.addWidget(e)
            if browse:
                b = QPushButton("Browse")
                b.clicked.connect(lambda _=False, e=e, t=title: self._browse_into(e, t, key))
                h.addWidget(b)
            return w

        items = [
            line("Game program folder", "Where the game's executable is. Empty: the game the launcher built (the runtime folder in the game data folder).",
                 "game_dir", "automatic", True),
            line("Game data folder", "Saves, DLC, shader cache, extracted game files, the build and the log. "
                 "Changing it checks the folder (writable, free space, OneDrive) and offers to copy your saves and "
                 "files; the old folder is never deleted. Same as Setup > Where the game is built.", "data_root", "automatic", True),
            line("Extra command-line flags", "Added to the game's command line, for example "
                 "--resolution_scale=2.", "extra_args", "--flag=value"),
        ]
        for w in items:
            launcher.addWidget(w)
        page = lay
        lay = section_card(page, "Other settings")
        lay.addWidget(label("Any other forza.toml key, one per line (key = value). They are written "
                            "to forza.toml as they are.", "help"))
        self.extras = QPlainTextEdit(adv.get("extras_toml", ""))
        self.extras.setPlaceholderText('vulkan_validation_enabled = true')
        self.extras.setFixedHeight(90)
        self.extras_err = label("", "bad")
        self.extras.textChanged.connect(self._extras_changed)
        lay.addWidget(self.extras)
        lay.addWidget(self.extras_err)
        lay = section_card(page, "Environment variables")
        lay.addWidget(label("NAME=value, one per line, set for the game (for example FORZA_NETTRACE=1).", "help"))
        self.envs = QPlainTextEdit(adv.get("extra_env", ""))
        self.envs.setFixedHeight(70)
        self.envs.textChanged.connect(lambda: self._adv("extra_env", self.envs.toPlainText()))
        lay.addWidget(self.envs)
        lay = section_card(page, "What Play runs")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(170)
        self.preview.setStyleSheet("font-family: monospace; font-size: 12px;")
        lay.addWidget(self.preview)
        reset = QPushButton("Reset all settings to defaults")
        reset.clicked.connect(self.window.reset_settings)
        page.addWidget(reset, 0, Qt.AlignLeft)
        self.search.add("Other settings", "Advanced", "Other settings", self.extras,
                        keywords="extras_toml forza.toml configuration keys")
        self.search.add("Environment variables", "Advanced", "Environment variables", self.envs,
                        keywords="extra_env FORZA_NETTRACE NAME=value")
        self.search.add("What Play runs", "Advanced", "What Play runs", self.preview,
                        keywords="command line preview executable")
        self.search.add("Reset all settings to defaults", "Advanced", "Launcher", reset)
        self.refresh_preview()

    def _browse_into(self, edit, title, key):
        d = QFileDialog.getExistingDirectory(self, title, edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
            self._adv(key, d)

    def _extras_changed(self):
        text = self.extras.toPlainText()
        err = validate_extras(text)
        self.extras_err.setText(("Not valid TOML yet: " + err) if err else "")
        if not err:
            self._adv("extras_toml", text)

    def refresh_preview(self):
        if not hasattr(self, "preview"):
            return
        self.preview.setPlainText(self.window.preview_text())

    def rebuild(self):
        """After a reset: recreate the pages from the config."""
        self.rows.clear()
        self.search.clear_targets()
        cur = self.tabs.currentIndex()
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        for g in schema.GROUPS:
            self.stack.addWidget(self._build_group(g))
        self.stack.setCurrentIndex(cur)
        self._apply_dependencies()
        self.search.refresh()
        self.rebuilt.emit()
