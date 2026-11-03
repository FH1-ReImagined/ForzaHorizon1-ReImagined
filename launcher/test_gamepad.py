"""Controller state, real SDL hotplug/mapping, and Qt navigation without game data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher.gamepad import Actions, SDLController, State


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.actions = Actions()
        self.actions.update(State(1), 0)

    def test_buttons_do_not_repeat(self):
        for button in ("accept", "back", "next", "previous"):
            state = State(1, frozenset({button}))
            self.assertEqual(self.actions.update(state, 1), [button])
            self.assertEqual(self.actions.update(state, 10), [])
            self.actions.update(State(1), 11)

    def test_direction_repeat_and_release(self):
        down = State(1, frozenset({"down"}))
        self.assertEqual(self.actions.update(down, 1), ["down"])
        self.assertEqual(self.actions.update(down, 1.3), [])
        self.assertEqual(self.actions.update(down, 1.4), ["down"])
        self.assertEqual(self.actions.update(down, 1.53), ["down"])
        self.assertEqual(self.actions.update(State(1), 2), [])
        self.assertEqual(self.actions.update(down, 2.01), ["down"])

    def test_stick_deadzone_and_dominant_axis(self):
        self.assertEqual(self.actions.update(State(1, x=3000, y=-8000), 1), [])
        self.assertEqual(self.actions.update(State(1, x=18000, y=-25000), 2), ["up"])
        self.assertEqual(self.actions.update(State(1, x=-32000, y=10000), 3), ["left"])

    def test_opposing_directions_cancel(self):
        self.assertEqual(self.actions.update(State(1, frozenset({"up", "down"})), 1), [])

    def test_reconnect_and_resume_require_neutral(self):
        held = State(2, frozenset({"accept"}))
        for reset in (lambda: self.actions.update(None, 1),
                      lambda: self.actions.update(held, 1, enabled=False),
                      lambda: self.actions.update(State(3), 1)):
            reset()
            self.assertEqual(self.actions.update(held, 2), [])
            self.assertEqual(self.actions.update(held, 3), [])
            self.actions.update(State(2), 4)
            self.assertEqual(self.actions.update(held, 5), ["accept"])

    def test_back_or_page_change_cannot_also_activate(self):
        self.assertEqual(self.actions.update(State(1, frozenset({"next", "accept"})), 1), ["next"])


@unittest.skipUnless(sys.platform == "linux", "POSIX termination behavior")
class SignalTests(unittest.TestCase):
    def test_controller_initialization_preserves_sigterm_termination(self):
        import signal
        import subprocess
        code = (
            "import os, signal, time; "
            "from forza_launcher.gamepad import SDLController; "
            "backend = SDLController(); "
            "os.kill(os.getpid(), signal.SIGTERM); time.sleep(30)"
        )
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent))
        env.pop("SDL_NO_SIGNAL_HANDLERS", None)
        result = subprocess.run([sys.executable, "-c", code], env=env,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, -signal.SIGTERM, result.stderr.decode())


class SDLTests(unittest.TestCase):
    """Use SDL's virtual device, exercising the shipped native library on each OS."""
    def setUp(self):
        import sdl2
        self.s = sdl2
        self.backend = SDLController()
        self.addCleanup(self.backend.close)
        self.index = sdl2.SDL_JoystickAttachVirtual(sdl2.SDL_JOYSTICK_TYPE_GAMECONTROLLER, 6, 21, 0)
        self.assertGreaterEqual(self.index, 0, sdl2.SDL_GetError())
        self.addCleanup(lambda: sdl2.SDL_JoystickDetachVirtual(self.index))
        self.joystick = sdl2.SDL_JoystickOpen(self.index)
        self.assertTrue(self.joystick)
        self.addCleanup(lambda: sdl2.SDL_JoystickClose(self.joystick))
        # A real controller may be attached to the developer's machine: explicitly select our fixture.
        self.backend.controller = sdl2.SDL_GameControllerOpen(self.index)
        self.assertTrue(self.backend.controller, sdl2.SDL_GetError())

    def test_native_mapping_axes_and_buttons(self):
        s = self.s
        for name, button in (("accept", s.SDL_CONTROLLER_BUTTON_A),
                             ("back", s.SDL_CONTROLLER_BUTTON_B),
                             ("next", s.SDL_CONTROLLER_BUTTON_RIGHTSHOULDER),
                             ("previous", s.SDL_CONTROLLER_BUTTON_LEFTSHOULDER),
                             ("up", s.SDL_CONTROLLER_BUTTON_DPAD_UP),
                             ("down", s.SDL_CONTROLLER_BUTTON_DPAD_DOWN),
                             ("left", s.SDL_CONTROLLER_BUTTON_DPAD_LEFT),
                             ("right", s.SDL_CONTROLLER_BUTTON_DPAD_RIGHT)):
            self.assertEqual(s.SDL_JoystickSetVirtualButton(self.joystick, button, 1), 0)
            self.assertIn(name, self.backend.read().buttons)
            s.SDL_JoystickSetVirtualButton(self.joystick, button, 0)
        s.SDL_JoystickSetVirtualAxis(self.joystick, s.SDL_CONTROLLER_AXIS_LEFTX, -24000)
        s.SDL_JoystickSetVirtualAxis(self.joystick, s.SDL_CONTROLLER_AXIS_LEFTY, 29000)
        state = self.backend.read()
        self.assertEqual((state.x, state.y), (-24000, 29000))

    def test_hotplug_and_cleanup(self):
        first = self.backend.read().device
        self.s.SDL_JoystickDetachVirtual(self.index)
        state = self.backend.read()
        self.assertTrue(state is None or state.device != first)
        self.index = self.s.SDL_JoystickAttachVirtual(self.s.SDL_JOYSTICK_TYPE_GAMECONTROLLER, 6, 21, 0)
        self.assertGreaterEqual(self.index, 0)
        # If another physical device was selected, release it so this fixture can be selected.
        if self.backend.controller:
            self.s.SDL_GameControllerClose(self.backend.controller)
        self.backend.controller = self.s.SDL_GameControllerOpen(self.index)
        self.assertNotEqual(self.backend.read().device, first)
        self.backend.close()
        self.backend.close()
        self.assertFalse(self.backend.initialized)


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        from forza_launcher.ui.gamepad import Navigation
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        # The offscreen platform cannot reliably report OS activation (especially Windows).
        p = patch.object(QApplication, "applicationState", return_value=Qt.ApplicationActive)
        p.start()
        self.addCleanup(p.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name in ("forza_launcher.ui.pages.SetupPage.refresh",
                     "forza_launcher.ui.pages.SetupPage.offer_move"):
            p = patch(name)
            p.start()
            self.addCleanup(p.stop)
        p = patch("forza_launcher.paths.config_dir", return_value=Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)
        cfg = Config(Path(self.temp.name) / "launcher.json")
        cfg.loaded = True
        cfg.gpu["checked"] = True
        cfg.d["advanced"]["data_root"] = self.temp.name
        cfg.d["check_updates"] = False
        self.win = MainWindow(cfg)
        self.addCleanup(self.win.close)
        self.backend = Mock()
        self.backend.read.return_value = State(1)
        self.nav = Navigation(self.win, self.backend)
        self.addCleanup(self.nav.close)
        self.addCleanup(self.wait_for_gallery)
        self.win.show()
        self.win.activateWindow()
        self.app.processEvents()
        self.nav.focus(self.win.play.play)

    def wait_for_gallery(self):
        # A test that passes the photo gallery starts its folder scan (a QThread child of the
        # page): let it end before the window can go, or a running QThread is destroyed.
        import time
        self.win.gallery.shutdown()
        deadline = time.monotonic() + 10
        while self.win.gallery.task is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)

    def press(self, button):
        self.nav.dispatch(button, self.win)
        self.app.processEvents()

    def test_accept_uses_existing_play_action(self):
        # No real game is started: only the existing Play signal is observed.
        self.win.play.play_requested.disconnect()
        started = Mock()
        self.win.play.play_requested.connect(started)
        self.win.play.mode = "play"
        self.press("accept")
        started.assert_called_once_with()

    def test_native_virtual_controller_reaches_play_signal(self):
        import sdl2 as s
        native = SDLController()
        self.addCleanup(native.close)
        index = s.SDL_JoystickAttachVirtual(s.SDL_JOYSTICK_TYPE_GAMECONTROLLER, 6, 21, 0)
        self.assertGreaterEqual(index, 0)
        self.addCleanup(lambda: s.SDL_JoystickDetachVirtual(index))
        joystick = s.SDL_JoystickOpen(index)
        self.addCleanup(lambda: s.SDL_JoystickClose(joystick))
        native.controller = s.SDL_GameControllerOpen(index)
        self.assertTrue(native.controller)
        self.nav.backend = native
        self.win.play.play_requested.disconnect()
        started = Mock()
        self.win.play.play_requested.connect(started)
        self.win.play.mode = "play"
        self.nav.poll()  # neutral
        s.SDL_JoystickSetVirtualButton(joystick, s.SDL_CONTROLLER_BUTTON_A, 1)
        self.nav.poll()
        self.app.processEvents()
        self.nav.poll()  # a held button must not start the game twice
        self.app.processEvents()
        started.assert_called_once_with()

    def test_unconfigured_play_opens_setup(self):
        self.press("accept")
        self.assertEqual(self.win.stack.currentIndex(), 1)

    def test_shoulders_wrap_and_back_returns_to_play(self):
        self.press("previous")
        self.assertEqual(self.win.stack.currentIndex(), 3)
        self.press("next")
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.press("next")
        self.assertIs(self.win.stack.currentWidget(), self.win.gallery)
        self.press("back")
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.assertIs(self.app.focusWidget(), self.win.play.play)
        self.assertTrue(self.win.isVisible())

    def test_shoulders_visit_every_page_in_sidebar_order(self):
        # Designs (id 7) and Mods (id 8) included: the stack index of a page is its id.
        buttons = sorted(self.win.navs.buttons(), key=lambda b: b.parentWidget().layout().indexOf(b))
        pages = [self.win._pages[self.win.navs.id(b)] for b in buttons]
        for page_id, page in self.win._pages.items():
            self.assertEqual(self.win.stack.indexOf(page), page_id)
        seen = [self.win.stack.currentWidget()]
        for _ in pages[1:]:
            self.press("next")
            seen.append(self.win.stack.currentWidget())
        self.assertEqual(seen, pages)
        self.press("next")
        self.assertIs(self.win.stack.currentWidget(), pages[0])
        backwards = []
        for _ in pages:
            self.press("previous")
            backwards.append(self.win.stack.currentWidget())
        self.assertEqual(backwards, pages[::-1])

    def test_mods_page_works_with_a_controller(self):
        from forza_launcher import mods
        from forza_launcher.ui.window import MODS_PAGE_ID
        folder = mods.mods_dir(self.win.cfg) / "Pad" / "media"
        folder.mkdir(parents=True)
        (folder / "a.xml").write_text("a")
        self.win.show_page(MODS_PAGE_ID)
        page = self.win.mods
        page.refresh()
        self.app.processEvents()
        # Down walks the page's controls: Add mod..., the list, the order buttons.
        reached = set()
        self.nav.focus(page.add_button)
        for _ in range(12):
            reached.add(self.app.focusWidget())
            self.press("down")
        self.assertTrue({page.add_button, page.table, page.refresh_button} <= reached)
        with patch.object(page, "add_dialog") as dialog:
            self.nav.focus(page.add_button)
            self.press("accept")
        dialog.assert_called_once()
        # A on the selected mod turns it off and on.
        self.nav.focus(page.table)
        page.table.selectRow(0)
        self.press("accept")
        self.assertEqual(self.win.cfg.d["mods"]["disabled"], ["Pad"])
        self.press("accept")
        self.assertEqual(self.win.cfg.d["mods"]["disabled"], [])

    def test_texture_packs_page_works_with_a_controller(self):
        from forza_launcher import texture_packs
        from forza_launcher.ui.window import TEXTURE_PACKS_PAGE_ID
        folder = texture_packs.textures_dir(self.win.cfg) / "Pad"
        folder.mkdir(parents=True)
        (folder / "0123456789abcdef.png").write_bytes(
            b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\0\x08\0\0\0\x08\x08\x06\0\0\0" + b"\0" * 16)
        self.win.show_page(TEXTURE_PACKS_PAGE_ID)
        page = self.win.texture_packs
        self.app.processEvents()
        # Down walks the page's controls: the switches, Add pack..., the list, Make HD pack.
        reached = set()
        self.nav.focus(page.switches["texture_replace"])
        for _ in range(16):
            reached.add(self.app.focusWidget())
            self.press("down")
        self.assertTrue({page.switches["texture_dump"], page.add_button, page.table, page.scale} <= reached)
        # A on the selected pack turns it off and on.
        self.nav.focus(page.table)
        page.table.selectRow(0)
        self.press("accept")
        self.assertEqual(self.win.cfg.d["texture_packs"]["disabled"], ["Pad"])
        self.press("accept")
        self.assertEqual(self.win.cfg.d["texture_packs"]["disabled"], [])

    def test_focus_skips_disabled_hidden_controls_and_scrolls(self):
        from PySide6.QtWidgets import QScrollArea, QPushButton
        self.win.show_page(2)
        disabled = self.win.settings.rows["frame_rate"].control
        disabled.setEnabled(False)
        self.nav.focus(self.win.navs.button(2))
        visited = set()
        for _ in range(120):
            self.press("down")
            target = self.app.focusWidget()
            self.assertTrue(target.isVisible())
            self.assertTrue(target.isEnabled())
            self.assertIsNot(target, disabled)
            self.assertTrue(self.nav.is_control(self.nav.target(self.win)))
            visited.add(target)
            if any(s.verticalScrollBar().value() > 0 for s in self.win.settings.findChildren(QScrollArea)):
                break
        else:
            self.fail("Moving focus never scrolled the Settings page")
        self.assertGreater(len(visited), 2)

    def test_page_containers_and_labels_never_get_a_focus_outline(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QScrollArea
        scroll = self.win.play.findChild(QScrollArea)
        scroll.setFocusPolicy(Qt.StrongFocus)
        self.nav.focus(scroll)
        self.nav.update_ring(self.win)
        self.assertFalse(self.nav.ring.isVisible())
        self.press("down")
        self.assertTrue(self.nav.is_control(self.nav.target(self.win)))
        label = self.win.play.findChild(QLabel)
        self.assertFalse(self.nav.is_control(label))

    def test_action_badge_follows_focus_and_restores_existing_icon(self):
        from PySide6.QtWidgets import QStyle
        button = self.win.play.play
        original = self.win.style().standardIcon(QStyle.SP_MediaPlay)
        button.setIcon(original)
        size = button.iconSize()
        self.nav.poll()
        self.assertEqual(button.icon().cacheKey(), self.nav.accept_icon.cacheKey())
        for tab in self.win.navs.buttons():
            self.nav.focus(tab)
            self.nav.poll()
            self.assertTrue(tab.icon().isNull())
        self.assertEqual(button.icon().cacheKey(), original.cacheKey())
        self.assertEqual(button.iconSize(), size)
        self.nav.focus(button)
        self.nav.poll()
        self.assertEqual(button.icon().cacheKey(), self.nav.accept_icon.cacheKey())
        self.backend.read.return_value = None
        self.nav.poll()
        self.assertEqual(button.icon().cacheKey(), original.cacheKey())

    def test_connection_status_lives_in_controls_and_sidebar_fits_at_minimum_size(self):
        self.win.resize(900, 620)
        self.nav.poll()
        from forza_launcher import schema
        controls = getattr(self.win, "controls", None)
        self.win.show_page(self.win.stack.indexOf(controls) if controls is not None else 2)
        if controls is None:
            self.win.settings.tabs.setCurrentIndex(schema.GROUPS.index("Controls"))
            controls = self.win.settings.stack.currentWidget()
        self.app.processEvents()
        guide = self.win.controller_guide
        self.assertTrue(controls.isAncestorOf(guide))
        self.assertTrue(guide.isVisible())
        self.assertEqual(self.win.height(), 620)
        for button in self.win.navs.buttons():
            self.assertLess(button.geometry().bottom(), self.win.version.geometry().top())

    def test_controller_status_survives_settings_rebuild(self):
        from forza_launcher import schema
        guide = self.win.controller_guide
        self.win.settings.rebuild()
        self.app.processEvents()
        self.nav.poll()
        self.assertIs(self.win.controller_guide, guide)
        self.assertEqual(guide.status.text(), "Connected")
        scroll = self.win.settings.stack.widget(schema.GROUPS.index("Controls"))
        controls = getattr(self.win, "controls", scroll)
        self.assertTrue(controls.isAncestorOf(guide))

    def test_controller_status_can_attach_to_optional_controls_page(self):
        from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
        page = QWidget(self.win)
        layout = QVBoxLayout(page)
        scroll = QScrollArea()
        inner = QWidget()
        QVBoxLayout(inner)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        self.win.controls = page
        self.nav.attach_guide()
        self.assertTrue(page.isAncestorOf(self.win.controller_guide))

    def test_switch_combo_spin_slider_and_settings_groups(self):
        from PySide6.QtWidgets import QComboBox, QSpinBox, QSlider, QVBoxLayout, QWidget
        from PySide6.QtCore import Qt
        from forza_launcher.ui.widgets import Switch
        page = QWidget()
        layout = QVBoxLayout(page)
        switch, combo, spin, slider = Switch(), QComboBox(), QSpinBox(), QSlider(Qt.Horizontal)
        combo.addItems(["one", "two", "three"])
        for widget in (switch, combo, spin, slider):
            layout.addWidget(widget)
        self.win.stack.addWidget(page)
        self.win.stack.setCurrentWidget(page)
        self.nav.focus(switch)
        self.press("accept")
        self.assertTrue(switch.isChecked())
        for widget in (combo, spin, slider):
            self.nav.focus(widget)
            self.press("right")
        self.assertEqual(combo.currentIndex(), 1)
        self.assertEqual(spin.value(), 1)
        self.assertEqual(slider.value(), 1)
        self.nav.focus(spin)
        self.press("down")
        self.assertIs(self.app.focusWidget(), slider)
        self.win.show_page(2)
        self.nav.focus(self.win.settings.tabs)
        self.press("right")
        self.assertEqual(self.win.settings.tabs.currentIndex(), 1)

    def test_search_overlay_can_be_selected_or_dismissed_without_leaving_settings(self):
        self.win.show_page(2)
        search = self.win.settings.search
        self.nav.focus(search.edit)
        search.edit.setText("frame_rate")
        self.press("down")
        self.assertIs(self.nav.target(self.win), search.results)
        self.press("accept")
        self.assertTrue(search.results.isHidden())
        self.assertIs(self.nav.target(self.win), self.win.settings.rows["frame_rate"].control)
        self.nav.focus(search.edit)
        search.refresh()
        self.press("back")
        self.assertTrue(search.results.isHidden())
        self.assertIs(self.win.stack.currentWidget(), self.win.settings)

    def test_combo_popup_select_and_cancel(self):
        from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget
        page = QWidget()
        combo = QComboBox()
        combo.addItems(["one", "two", "three"])
        QVBoxLayout(page).addWidget(combo)
        self.win.stack.addWidget(page)
        self.win.stack.setCurrentWidget(page)
        self.nav.focus(combo)
        old = combo.currentIndex()
        self.press("accept")
        self.assertIsNotNone(self.app.activePopupWidget())
        self.press("down" if old == 0 else "up")
        self.press("accept")
        self.assertNotEqual(combo.currentIndex(), old)
        self.press("accept")
        self.press("back")
        self.assertIsNone(self.app.activePopupWidget())

    def test_modal_back_does_not_activate_underlying_window(self):
        from PySide6.QtWidgets import QDialog, QPushButton, QVBoxLayout
        dialog = QDialog(self.win)
        dialog.setModal(True)
        from shiboken6 import isValid
        self.addCleanup(lambda: dialog.close() if isValid(dialog) else None)
        button = QPushButton("Continue", dialog)
        QVBoxLayout(dialog).addWidget(button)
        clicked = Mock()
        button.clicked.connect(clicked)
        dialog.show()
        self.app.processEvents()
        self.assertIs(self.nav.active_scope(), dialog)
        self.nav.focus(button)
        self.nav.dispatch("next", dialog)
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.nav.dispatch("accept", dialog)
        clicked.assert_called_once()
        self.nav.update_ring(dialog)
        self.nav.dispatch("back", dialog)
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.nav.update_ring(self.win)

    def test_inactive_and_running_suppress_input_but_build_can_be_cancelled(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        with patch.object(QApplication, "applicationState", return_value=Qt.ApplicationInactive):
            self.assertIsNone(self.nav.active_scope())
        with patch.object(self.win, "running", return_value=True):
            self.assertIsNone(self.nav.active_scope())
        self.win._busy = True
        self.assertIs(self.nav.active_scope(), self.win)
        self.win._busy = False
        self.win._dlss_task = object()
        self.assertIs(self.nav.active_scope(), self.win)
        self.win._dlss_task = None

    def test_timer_keeps_polling_inside_a_controller_opened_modal(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QDialog
        dialog = QDialog(self.win)
        loop = QEventLoop()
        dialog.finished.connect(loop.quit)
        timed_out = []
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        def timeout():
            timed_out.append(True)
            dialog.reject()
            loop.quit()
        watchdog.timeout.connect(timeout)
        self.win.play.play.clicked.disconnect()
        self.win.play.play.clicked.connect(dialog.exec)
        samples = iter([State(1), State(1, frozenset({"accept"})), State(1),
                        State(1, frozenset({"back"}))])
        self.backend.read.side_effect = lambda: next(samples, State(1))
        self.nav.start()
        watchdog.start(2000)
        loop.exec()
        watchdog.stop()
        self.nav.timer.stop()
        self.assertFalse(timed_out, "Controller polling stopped inside the dialog event loop")
        self.assertFalse(dialog.isVisible())

    def test_poll_resume_release_and_visible_focus(self):
        self.nav.poll()  # neutral arms navigation
        self.backend.read.return_value = State(1, frozenset({"next"}))
        with patch.object(self.nav, "active_scope", return_value=None):
            self.nav.poll()
        self.nav.poll()
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.backend.read.return_value = State(1)
        self.nav.poll()
        self.backend.read.return_value = State(1, frozenset({"next"}))
        self.nav.poll()
        self.app.processEvents()
        self.nav.poll()
        self.assertIs(self.win.stack.currentWidget(), self.win.gallery)
        self.assertTrue(self.nav.ring.isVisible())
        self.assertFalse(self.win.controller_guide.isHidden())
        self.assertEqual(self.win.controller_guide.status.text(), "Connected")
        self.backend.read.return_value = None
        self.nav.poll()
        self.assertFalse(self.nav.ring.isVisible())
        self.assertTrue(self.win.controller_guide.controls.isHidden())

    def test_backend_failure_keeps_launcher_usable(self):
        with patch("forza_launcher.ui.gamepad.paths.log_error"):
            self.backend.read.side_effect = RuntimeError("device disappeared")
            self.nav.poll()
        self.backend.close.assert_called_once()
        self.assertTrue(self.win.play.play.isEnabled())
        self.assertIn("Unavailable", self.win.controller_guide.status.text())


if __name__ == "__main__":
    unittest.main()
