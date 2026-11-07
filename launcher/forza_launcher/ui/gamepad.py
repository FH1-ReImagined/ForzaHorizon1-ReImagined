"""Controller navigation through Qt's focus chain and the launcher's existing actions."""
import time
from shiboken6 import isValid

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSlider,
                               QAbstractSpinBox, QApplication, QComboBox, QDialog,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                               QTabBar, QTextEdit, QWidget)

from .. import gamepad, paths
from . import theme
from .controller_widgets import ControllerGuide, button_icon


class FocusRing(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.ORANGE), 3))
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 7, 7)


class Navigation(QObject):
    def __init__(self, window, backend=None):
        super().__init__(window)
        self.window = window
        self.window.controller_guide = ControllerGuide()
        self.attach_guide()
        self.window.settings.rebuilt.connect(self.attach_guide)
        self.backend = backend
        self.actions = gamepad.Actions()
        self.used = False
        self.ring = FocusRing(window)
        self.ring.hide()
        self.decorated_button = None
        self.accept_icon = button_icon("A")
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.poll)

    def attach_guide(self):
        # Work independently of the optional Controls-diagrams PR. Once it is
        # present, the same status card belongs in its scrollable Controls page.
        controls = getattr(self.window, "controls", None)
        scroll = (controls.findChild(QScrollArea) if controls is not None else
                  self.window.settings.stack.widget(self.window.settings.tab_index("Controls")))
        scroll.widget().layout().insertWidget(2 if controls is not None else 0,
                                               self.window.controller_guide)

    def start(self):
        try:
            if self.backend is None:
                self.backend = gamepad.SDLController()
        except (ImportError, OSError, RuntimeError) as exc:
            paths.log_error("initialize launcher gamepad", exc)
            self.window.controller_guide.set_state(error="Unavailable. Keyboard and mouse still work.")
            return
        self.timer.start()

    def close(self):
        self.timer.stop()
        if isValid(self.ring):
            self.ring.hide()
        self.update_action_icon(None)
        if self.backend is not None:
            self.backend.close()
            self.backend = None

    def active_scope(self):
        if (QApplication.applicationState() != Qt.ApplicationActive
                or self.window.running()):
            return None
        scope = QApplication.activeModalWidget() or QApplication.activeWindow()
        owner = scope
        # QWidget.isAncestorOf stops at top-level windows, including owned dialogs.
        while owner:
            if owner is self.window:
                return scope
            owner = owner.parentWidget()
        return None

    def poll(self):
        try:
            state = self.backend.read()
        except (OSError, RuntimeError) as exc:
            paths.log_error("read launcher gamepad", exc)
            self.close()
            self.window.controller_guide.set_state(error="Unavailable. Keyboard and mouse still work.")
            return
        scope = self.active_scope()
        self.window.controller_guide.set_state(connected=state is not None, paused=scope is None)
        self.update_action_icon(self.target(scope) if state and scope else None)
        # A Bind or a calibration on the Controls page owns the device: its presses are bindings.
        # Disabled, Actions also waits for a neutral pad before it acts again.
        controls = getattr(self.window, "controls", None)
        captured = bool(state and controls is not None and hasattr(controls, "captures")
                        and controls.captures(state.device))
        for action in self.actions.update(state, time.monotonic(), scope is not None and not captured):
            self.used = True
            # A button can enter QDialog.exec(). Deliver outside this timer callback so
            # the timer keeps polling inside that nested event loop (including B to cancel).
            QTimer.singleShot(0, lambda a=action, s=scope: self.deliver(a, s))
        self.update_ring(scope if state and self.used else None)

    def deliver(self, action, scope):
        if self.active_scope() is scope:
            self.dispatch(action, scope)

    @staticmethod
    def key(widget, key, modifiers=Qt.NoModifier):
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.sendEvent(widget, QKeyEvent(kind, key, modifiers))

    @staticmethod
    def target(scope):
        focus = QApplication.focusWidget()
        if focus and (focus is scope or scope.isAncestorOf(focus)):
            # Spin boxes and editable combos delegate focus to their line edit.
            if isinstance(focus.parentWidget(), (QAbstractSpinBox, QComboBox)):
                return focus.parentWidget()
            return focus
        return None

    def focus(self, widget):
        widget.setFocus(Qt.TabFocusReason)
        parent = widget.parentWidget()
        while parent:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 20, 20)
            parent = parent.parentWidget()

    @staticmethod
    def is_control(widget):
        # Qt puts scroll areas and selectable labels in the tab chain too. They
        # are page scaffolding, not controller actions, and must never get a ring.
        if isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)):
            return not widget.isReadOnly()
        return isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox,
                                   QAbstractSlider, QAbstractItemView, QTabBar))

    def move(self, scope, forward):
        current = self.target(scope) or scope
        candidate = current
        while True:
            candidate = candidate.nextInFocusChain() if forward else candidate.previousInFocusChain()
            if (candidate is not scope and scope.isAncestorOf(candidate)
                    and self.is_control(candidate)
                    and candidate.isVisible() and candidate.isEnabled()
                    and not isinstance(candidate.parentWidget(), (QAbstractSpinBox, QComboBox))
                    and candidate.focusPolicy() & Qt.TabFocus):
                self.focus(candidate)
                return
            if candidate is current:
                return

    def dispatch(self, action, scope):
        popup = QApplication.activePopupWidget()
        if popup:
            keys = {"accept": Qt.Key_Return, "back": Qt.Key_Escape,
                    "up": Qt.Key_Up, "down": Qt.Key_Down,
                    "left": Qt.Key_Left, "right": Qt.Key_Right}
            if action in keys:
                self.key(QApplication.focusWidget() or popup, keys[action])
            return
        search = self.window.settings.search
        if scope is self.window and search.results.isVisible():
            if action == "back":
                search.dismiss()
                self.focus(search.edit)
                return
            if action == "accept":
                search._choose_current()
                return
            if action in ("up", "down"):
                if self.target(scope) is not search.results:
                    self.focus(search.results)
                else:
                    self.key(search.results, Qt.Key_Up if action == "up" else Qt.Key_Down)
                return
        if action in ("previous", "next"):
            if scope is self.window:
                buttons = sorted(self.window.navs.buttons(),
                                 key=lambda b: b.parentWidget().layout().indexOf(b))
                current = self.window.current_nav_button()
                index = buttons.index(current) if current in buttons else 0
                button = buttons[(index + (1 if action == "next" else -1)) % len(buttons)]
                self.window.show_page(self.window.navs.id(button))
                self.focus(button)
            return
        if action == "back":
            if isinstance(scope, QDialog):
                self.key(scope, Qt.Key_Escape)
            elif scope is self.window:
                self.window.show_page(0)
                self.focus(self.window.play.play)
            return
        target = self.target(scope)
        if target is None or not self.is_control(target):
            if scope is self.window and self.window.stack.currentIndex() == 0:
                self.focus(self.window.play.play)
            else:
                self.move(scope, True)
            target = self.target(scope)
        if (target is None or not self.is_control(target)
                or not target.isEnabled() or not target.isVisible()):
            return
        if action in ("up", "down"):
            self.move(scope, action == "down")
        elif action in ("left", "right"):
            if isinstance(target, QTabBar):
                self.key(target, Qt.Key_Right if action == "right" else Qt.Key_Left)
            elif isinstance(target, QComboBox):
                self.key(target, Qt.Key_Down if action == "right" else Qt.Key_Up)
            elif isinstance(target, QAbstractItemView):
                self.key(target, Qt.Key_Down if action == "right" else Qt.Key_Up)
            elif isinstance(target, QAbstractSpinBox):
                self.key(target, Qt.Key_Down if action == "left" else Qt.Key_Up)
            elif isinstance(target, QAbstractSlider):
                self.key(target, Qt.Key_Right if action == "right" else Qt.Key_Left)
            else:
                self.move(scope, action == "right")
        elif action == "accept":
            if isinstance(target, QAbstractButton):
                target.click()
            elif isinstance(target, QComboBox):
                target.showPopup()
            else:
                self.key(target, Qt.Key_Return)

    def update_action_icon(self, target):
        if (not isinstance(target, QPushButton) or target in self.window.navs.buttons()
                or not target.isEnabled() or not target.isVisible()):
            target = None
        if self.decorated_button:
            previous, icon, size = self.decorated_button
            if previous is target:
                return
            if isValid(previous):
                previous.setIcon(icon)
                previous.setIconSize(size)
            self.decorated_button = None
        if target:
            self.decorated_button = target, target.icon(), target.iconSize()
            target.setIcon(self.accept_icon)
            target.setIconSize(QSize(24, 24))

    def update_ring(self, scope):
        if not isValid(self.ring):
            self.ring = FocusRing(self.window)
        target = self.target(scope) if scope else None
        if (target is None or not self.is_control(target) or not target.isEnabled()
                or not target.isVisible() or QApplication.activePopupWidget()):
            self.ring.hide()
            return
        if self.ring.parentWidget() is not scope:
            self.ring.setParent(scope)
        origin, rect = target, target.rect()
        if isinstance(target, QAbstractItemView):
            if not target.currentIndex().isValid():
                self.ring.hide()
                return
            origin, rect = target.viewport(), target.visualRect(target.currentIndex())
        self.ring.setGeometry(rect.translated(origin.mapTo(scope, origin.rect().topLeft()))
                              .adjusted(-3, -3, 3, 3))
        self.ring.raise_()
        self.ring.show()
