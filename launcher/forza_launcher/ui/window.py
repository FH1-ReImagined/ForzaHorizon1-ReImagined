"""Play, gallery, catalogs, setup, settings, controls and saves."""
import sys
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import (APP_NAME, __version__, builder, cleanenv, coop_grant, crash_report, errors, game, paths, dlss,
               online_features, shader_share)
from ..config import Config, import_toml, render_toml
from . import theme
from .pages import AboutPage, PlayPage, SetupPage
from .settings_page import SettingsPage
from .gallery_page import GalleryPage
from .catalogs import CatalogsPage, CATALOG_PAGE_ID
from .controls_page import ControlsPage
from .saves_page import SavesPage
from .mods_page import ModsPage
from .texture_packs_page import TexturePacksPage
from .radio_page import RadioPage
from .widgets import label

MODS_PAGE_ID = 8
TEXTURE_PACKS_PAGE_ID = 9
RADIO_PAGE_ID = 10


class VrChecklist(QDialog):
    """The first "Play in VR": three steps, and "Don't show again"."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Play in VR")
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        warning = label("VR is experimental: it is playable, but expect rough edges. "
                        "Take a break if you feel motion sickness.", "warn")
        lay.addWidget(warning)
        lay.addWidget(QLabel("Before you start:"))
        for text in ("1. Start your VR app (WiVRn, SteamVR, Meta Quest Link or Virtual Desktop) "
                     "and connect the headset.",
                     "2. Put the headset on.",
                     "3. Press Play in VR."):
            step = QLabel(text)
            step.setWordWrap(True)
            lay.addWidget(step)
        self.dont_show = QCheckBox("Don't show again")
        lay.addWidget(self.dont_show)
        buttons = QDialogButtonBox()
        go = buttons.addButton("Play in VR", QDialogButtonBox.AcceptRole)
        go.setDefault(True)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        if not cfg.gpu.get("vendor") and not cfg.gpu.get("checked"):
            cfg.detect_gpu()   # once: preselects the GPU's choice in Settings > Display (#52)
            try:
                cfg.save()
            except OSError:
                pass
        if cfg.detect_specs():   # once, and again when the graphics card changed (#206)
            try:
                cfg.save()
            except OSError:
                pass
        self.session = None
        self.session_plan = None
        self._last_start_error = None
        self._dlss_session_status = None
        self._shader_warmup_status = None
        self._busy = False
        self._close_after_game = False
        self._dlss_task = None
        self._dlss_prepared = None
        self._dlss_result = (False, "")
        self._close_after_dlss = False
        self._crash_probe = None
        self._crash_report = None
        self._report_context = None
        self._crash_dialog = None
        self._close_after_game = False
        self.setWindowTitle(APP_NAME)
        icon = paths.asset("forza.ico" if sys.platform == "win32" else "logo.png")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1040, 760)
        self.setMinimumSize(900, 620)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 22, 0, 16)
        sl.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(18, 0, 12, 14)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setFixedSize(44, 44)
        logo.setAlignment(Qt.AlignCenter)
        pm = QPixmap(str(paths.asset("logo.png")))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo, 0, Qt.AlignVCenter)
        wordmark = QWidget()
        col = QVBoxLayout(wordmark)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        a = QLabel("Forza Horizon 1")
        a.setObjectName("brand")
        b = QLabel("ReImagined")
        b.setObjectName("brandSub")
        col.addWidget(a)
        col.addWidget(b)
        brand.addWidget(wordmark, 1, Qt.AlignVCenter)
        sl.addLayout(brand)

        self.stack = QStackedWidget()
        self.play = PlayPage(cfg, self)
        self.setup = SetupPage(cfg, self)
        self.settings = SettingsPage(cfg, self)
        self.about = AboutPage(cfg, self)
        self.gallery = GalleryPage(cfg, self)
        self.controls = ControlsPage(cfg, self)
        self.controls.haptics_changed.connect(self.update_haptics)
        self.controls.settings_requested.connect(self.show_control_settings)
        self._close_after_gallery = False
        self.gallery.idle.connect(self._gallery_idle)
        self.catalogs = CatalogsPage(cfg, self)
        self.saves = SavesPage(cfg, self)
        self.mods = ModsPage(cfg, self)
        self.texture_packs = TexturePacksPage(cfg, self)
        self.radio = RadioPage(cfg, self)
        self._close_after_radio = False
        self.radio.idle.connect(lambda: QTimer.singleShot(0, self.close) if self._close_after_radio else None)
        self.navs = QButtonGroup(self)
        self._pages = {0: self.play, 1: self.setup, 2: self.settings, 3: self.about,
                       4: self.gallery, 5: self.controls, 6: self.saves,
                       CATALOG_PAGE_ID: self.catalogs, MODS_PAGE_ID: self.mods,
                       TEXTURE_PACKS_PAGE_ID: self.texture_packs, RADIO_PAGE_ID: self.radio}
        # In id order, so a page's stack index is its id.
        for page in self._pages.values():
            self.stack.addWidget(page)
        for i, name in ((0, "Play"), (4, "Photo gallery"),
                        (CATALOG_PAGE_ID, "Designs && vinyls"), (1, "Setup"),
                        (5, "Controls"), (2, "Settings"), (6, "Saves"), (MODS_PAGE_ID, "Mods"),
                        (TEXTURE_PACKS_PAGE_ID, "Texture packs"), (RADIO_PAGE_ID, "Radio"), (3, "About")):
            btn = QPushButton(name)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.navs.addButton(btn, i)
            sl.addWidget(btn)
        self.navs.idClicked.connect(self.show_page)
        sl.addStretch(1)
        self.version = QLabel(f"Launcher {__version__}")
        self.version.setObjectName("version")
        self.version.setContentsMargins(20, 0, 10, 0)
        self.version.setWordWrap(True)
        self.update_notice = QPushButton("Update available")
        self.update_notice.setObjectName("link")
        self.update_notice.setStyleSheet("margin-left: 20px; margin-right: 10px;")
        self.update_notice.hide()
        self.update_notice.clicked.connect(lambda: self.show_page(3))
        self.about.updates.available.connect(lambda tag: self.update_notice.setVisible(bool(tag)))
        sl.addWidget(self.update_notice)
        sl.addWidget(self.version)
        h.addWidget(side)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 24, 20, 0)
        bl.addWidget(self.stack, 1)
        self.footer = QLabel("")
        self.footer.setObjectName("muted")
        self.footer.setWordWrap(True)
        self.footer.hide()
        bl.addWidget(self.footer)
        h.addWidget(body, 1)

        self.play.goto_setup.connect(lambda: self.show_page(1))
        self.play.play_requested.connect(lambda: self.start_game(False))
        self.play.play_vr_requested.connect(self.play_in_vr)
        self.setup.changed.connect(self.refresh_all)
        self.settings.changed.connect(self.play.refresh)
        self.poll = QTimer(self)
        self.poll.setInterval(500)
        self.poll.timeout.connect(self._poll)
        self._coop_notes = []    # what the co-op results thread applied or keeps waiting (#309)
        self._coop_thread = None
        self._coop_timer = QTimer(self)
        self._coop_timer.setInterval(500)
        self._coop_timer.timeout.connect(self._show_coop_notes)
        ask_online = self._first_run()
        self.show_page(0)
        self.refresh_all()
        if sys.platform == "win32":
            QTimer.singleShot(600, self.setup.offer_move)
        update_result = self.show_update_result()
        self._update_check_due = not paths.in_checkout() and not update_result
        if ask_online:   # the update check waits for the answer
            QTimer.singleShot(0, self.ask_online_features)
        else:
            self._start_update_check()

    def _start_update_check(self):
        if self._update_check_due and self.cfg.d["check_updates"]:
            QTimer.singleShot(1500, self.about.updates.check)
        self._update_check_due = False

    def ask_online_features(self):
        """The first-run "Online features" question (#497) where no installer asked it."""
        from .online_features import OnlineFeaturesDialog
        dialog = OnlineFeaturesDialog(self)
        dialog.exec()
        online_features.answer(self.cfg, dialog.online.isChecked())
        self.settings.rebuild()
        self.settings.flush()
        self.about.updates.automatic.setChecked(bool(self.cfg.d["check_updates"]))
        self._start_update_check()

    def show_update_result(self):
        from .. import linux_update
        result = linux_update.consume_result()
        if result:
            self.about.updates.message.setText(result.partition(": ")[2] or result)
            self.show_page(3)
        return bool(result)

    # ---- navigation -------------------------------------------------------------------
    def show_page(self, i):
        self.stack.setCurrentWidget(self._pages[i])
        self.navs.button(i).setChecked(True)
        self.navs.button(i).setFocus(Qt.OtherFocusReason)
        if i == 0:
            self.play.refresh()
        elif i == 1:
            self.setup.refresh()
        elif i == 3:
            self.about.refresh()
        elif i == 4:
            self.gallery.refresh()
        elif self._pages[i] is self.saves:
            self.saves.refresh()
        elif self._pages[i] is self.mods:
            self.mods.refresh()
        elif self._pages[i] is self.texture_packs:
            self.texture_packs.refresh()
        elif self._pages[i] is self.radio:
            self.radio.refresh()
        self.status("")

    def refresh_all(self):
        self.play.refresh()
        self.about.refresh()
        install, _ = game.find_game(self.cfg)
        self.version.setText(f"Launcher {__version__}\nGame: {game.game_version(install)}")
        self.write_toml()

    def show_control_settings(self):
        from .. import schema
        self.settings.tabs.setCurrentIndex(schema.GROUPS.index('Controls'))
        self.show_page(2)

    def status(self, text, error=False):
        self.footer.setText(text)
        self.footer.setVisible(bool(text))
        self.footer.setStyleSheet(f"color: {theme.ERR if error else theme.MUTED};")

    def set_busy(self, busy):
        self._busy = busy

    def radio_busy(self):
        return hasattr(self, "radio") and self.radio.busy()

    def lock_radio_navigation(self, locked):
        # Keeps folder changes, installs and other publishers out of a radio build.
        for button in self.navs.buttons():
            button.setEnabled(not locked or self.navs.id(button) == RADIO_PAGE_ID)

    def closeEvent(self, e):
        if self.radio.busy():
            self._close_after_radio = True
            self.radio.cancel()
            self.status("Stopping the radio build before closing…")
            e.ignore()
            return
        if self.catalogs.busy():
            self.catalogs.closing = True
            self.catalogs.cancel_task()
            self.status("Stopping the catalog operation before closing…")
            e.ignore()
            return
        if not self.saves.shutdown():
            e.ignore()
            return
        if self.about.updates.busy():
            if self.about.updates.installing:
                self.status("Installing the update. The launcher will restart when it's ready…")
                e.ignore()
                return
            self.about.updates.closing = True
            self.about.updates.cancel_task()
            self.status("Stopping the update request before closing…")
            e.ignore()
            return
        if self._dlss_task is not None and self._dlss_task.isRunning():
            self._close_after_dlss = True
            self._dlss_task.cancel()
            self.status("Cancelling the DLSS download…")
            e.ignore()
            return
        if self.setup.busy():
            if QMessageBox.question(self, "Quit", "A task is still running. Stop it and quit? "
                                    "A build resumes where it stopped.") != QMessageBox.Yes:
                e.ignore()
                return
            self.setup.task.cancel()
            self.setup.task.wait(15000)
        if self.texture_packs.busy():
            what = ("Make HD pack is still running. Stop it and quit? It continues where it stopped next time."
                    if self.texture_packs.hd_busy() else
                    "A texture pack is being compressed. Stop it and quit? What is compressed stays.")
            if QMessageBox.question(self, "Quit", what) != QMessageBox.Yes:
                e.ignore()
                return
            self.texture_packs.shutdown()
        if not self.gallery.shutdown():
            self._close_after_gallery = True
            self.status("Finishing photo tasks before closing…")
            e.ignore()
            return
        if self.running():
            self._close_after_game = True
            self.stop_game()
            self.status("Waiting for the game to close…")
            e.ignore()
            return
        # Installation flushed settings before handing off. Avoid writing the old config
        # over migrations made by a newly started Flatpak launcher.
        service = getattr(self.session, 'haptics', None)
        if service and service.running:
            service.stop()
            self.status('Releasing adaptive triggers…')
            QTimer.singleShot(50, self.close)
            e.ignore()
            return
        if not self.about.updates.restarting:
            self.settings.flush()
        super().closeEvent(e)

    def _gallery_idle(self):
        if self._close_after_gallery:
            QTimer.singleShot(0, self.close)

    # ---- first run ---------------------------------------------------------------------
    def _first_run(self):
        """A new launcher.json: adopt an existing forza.toml. Then the "Online features" choice of the
        installer (after the import, so an imported server cannot undo it). Returns True when the
        first-run question is still to ask."""
        new = not self.cfg.loaded
        if new:
            install, _ = game.find_game(self.cfg)
            toml = install.dir / "forza.toml" if install else None
            if toml and toml.is_file():
                try:
                    import_toml(self.cfg, toml.read_text(encoding="utf-8"))
                except OSError:
                    pass
        applied = online_features.take_installer_choice(self.cfg, new)
        ask = online_features.question_due(self.cfg, new)
        if applied:
            # These pages were created before the installer choice was applied.
            self.settings.rebuild()
            self.about.updates.automatic.setChecked(bool(self.cfg.d["check_updates"]))
        if new or applied:
            try:
                self.cfg.save()
            except OSError:
                pass
        return ask

    # ---- forza.toml ---------------------------------------------------------------------
    def write_toml(self):
        install, _ = game.find_game(self.cfg)
        if install is None:
            return
        try:
            from ..config import write_toml
            write_toml(self.cfg, install.dir / "forza.toml")
        except OSError as e:
            self.status(f"Could not write forza.toml in {install.dir}: {e}", True)

    def preview_text(self):
        install, looked = game.find_game(self.cfg)
        if install is None:
            return render_toml(self.cfg) + "\n# (the game is not built yet)"
        try:
            plan = game.build_plan(self.cfg, install, write=False)
        except Exception as e:
            return render_toml(self.cfg) + f"\n# {e}"
        lines = [f"# {plan.toml_path}", plan.toml_text.rstrip(), "", "# environment"]
        lines += [f"{k}={v}" for k, v in sorted(plan.env.items()) if k != "LD_LIBRARY_PATH"]
        lines += [f"unset {k}" for k in plan.unset]
        lines += ["", "# command", plan.command_line()]
        if plan.server_argv:
            lines += ["", "# local server", " ".join(plan.server_argv)]
        return "\n".join(lines)

    def reset_settings(self):
        if QMessageBox.question(self, "Reset settings", "Reset every setting on the Settings page to its default?") \
                != QMessageBox.Yes:
            return
        from .. import config, gpu, schema, specs
        self.cfg.d["settings"] = {**schema.defaults(), **config.FIRST_RUN}
        gpu.apply_preset(self.cfg.settings, self.cfg.gpu_vendor())
        self.cfg.settings.update(specs.recommended(self.cfg.pc_specs(), self.cfg.settings, self.cfg.gpu_vendor()))
        self.cfg.d["online"] = {"mode": "public", "custom_server": ""}
        self.cfg.d["profile"] = {"mode": "own", "xenia_content": "", "xenia_xuid": ""}
        for k in ("extra_args", "extra_env", "extras_toml"):
            self.cfg.advanced[k] = ""
        online_features.keep_off_after_reset(self.cfg)
        self.settings.rebuild()
        self.settings.flush()

    # ---- playing -----------------------------------------------------------------------
    def running(self):
        return self.session is not None and self.session.poll() is None

    def play_in_vr(self):
        """Play in VR: the first time, the checklist; then a start with the VR values."""
        if not self.cfg.d["vr"].get("checklist_hidden"):
            dialog = VrChecklist(self)
            if not dialog.exec():
                return
            if dialog.dont_show.isChecked():
                self.cfg.d["vr"]["checklist_hidden"] = True
                try:
                    self.cfg.save()
                except OSError:
                    pass
        self.start_game(True)

    def start_game(self, vr=None):
        if self.radio.busy():
            self.play.show_message("Wait for the radio build to finish before starting the game.", True)
            return
        if vr is not None:   # the DLSS download starts the game again without asking: keep the choice
            self._vr_start = vr
        if self.catalogs.busy():
            self.play.show_message('Wait for the catalog operation to finish before starting the game.', True)
            return
        if (self.running() or self._dlss_task is not None or self._crash_probe is not None
                or self.saves.busy() or self._busy):
            return
        install, looked = game.find_game(self.cfg)
        if install is None:
            self.play.show_message(game.missing_message(looked), True)
            return
        too_old = builder.cpu_blocker(self.cfg.data_home(), install.dir)
        if too_old:
            self.play.show_message(too_old, True)
            return
        ok, msg = __import__("forza_launcher.installer", fromlist=["x"]).check_game_folder(self.cfg.game_files())
        if not ok:
            self.play.show_message(msg, True)
            return
        self.settings.flush()
        preparation = (self.cfg.data_home(), install.dir, self.cfg.settings.get("fh1_upscaler"))
        if (dlss.enabled(self.cfg) or dlss.menu_runtime_enabled(self.cfg)) and self._dlss_prepared != preparation:
            self._prepare_dlss(install.dir)
            return
        self._dlss_prepared = None
        try:
            plan = game.build_plan(self.cfg, install, write=True, log=lambda t: self.status(t),
                                   vr=getattr(self, "_vr_start", False))
        except (RuntimeError, OSError) as e:
            self._start_failed(e)
            return
        self.saves.before_launch(plan, lambda: self._start_plan(plan))

    def _start_failed(self, error):
        if str(error) != self._last_start_error:
            self._last_start_error = str(error)
            paths.log_error("start the game", error)
        self.play.warn.setTextFormat(Qt.PlainText)
        self.play.show_message(errors.message(error, "start the game", paths.launcher_log()), True, keep=True)

    def _start_plan(self, plan):
        try:
            context = crash_report.launch_context(self.cfg, plan)
            session = game.Session(plan)
            session.start()
        except (RuntimeError, OSError, ValueError) as e:
            self._start_failed(e)
            return
        self._last_start_error = None
        self.session = session
        self.session_plan = plan
        self._session_started = time.time()  # co-op results earned now belong to this save
        self._report_context = context
        self._crash_report = None
        self.play.crash_report.hide()
        self._dlss_session_status = None
        self._shader_warmup_status = None
        self.play.set_shader_warmup(None)
        self.play.clear_message()
        if plan.notes:   # what the player set up but the game will not find (TU4, DLC): keep it in view
            self.play.show_message("The game is running. " + " ".join(plan.notes), True, keep=True)
        else:
            self.play.show_message("The game is running.")
        self.poll.start()
        self.play.refresh()

    def _prepare_dlss(self, renderer_folder=None):
        from .workers import Task
        from types import SimpleNamespace
        from ..installer import InstallError
        from ..fetch import Cancelled

        # Settings remain editable while the worker runs. Pin this download to
        # the data folder selected when Play was pressed.
        home = self.cfg.data_home()
        vendor = self.cfg.gpu_vendor()
        # prepare() checks the graphics card too (#107): pin it with the folder.
        download_cfg = SimpleNamespace(data_home=lambda: home, gpu_vendor=lambda: vendor)

        preparation = (home, renderer_folder, self.cfg.settings.get("fh1_upscaler"))

        def run(task):
            try:
                if renderer_folder is not None:
                    dlss.check_renderer(renderer_folder)
                dlss.prepare(download_cfg,
                             lambda done, total: task.progress.emit(done / total if total else 0,
                                                                   "Downloading NVIDIA DLSS"),
                             task.cancelled)
                if task.cancelled():
                    raise Cancelled()
            except (OSError, RuntimeError) as error:
                raise InstallError(str(error)) from error
            return "NVIDIA DLSS is ready."

        task = Task(run, "set up NVIDIA DLSS")
        self._dlss_task = task
        self._dlss_preparing = preparation
        self._dlss_result = (False, "DLSS setup did not finish.")
        self.play.play.setEnabled(False)
        self.play.show_message("Checking NVIDIA DLSS. First use downloads about 56 MB.")
        task.progress.connect(lambda fraction, _detail: self.status(
            f"Downloading NVIDIA DLSS… {fraction:.0%}"))
        task.done.connect(self._dlss_download_done)
        task.finished.connect(self._dlss_download_finished)
        task.start()
        self.play.refresh()

    def _dlss_download_done(self, ok, message):
        self._dlss_result = (ok, message)

    def _dlss_download_finished(self):
        task, self._dlss_task = self._dlss_task, None
        task.deleteLater()
        self.play.play.setEnabled(True)
        self.play.refresh()
        if self._close_after_dlss:
            self.close()
            return
        ok, message = self._dlss_result
        self.status(message, not ok)
        if ok:
            self._dlss_prepared = self._dlss_preparing
            self.start_game()
        else:
            if not dlss.enabled(self.cfg) and dlss.menu_runtime_enabled(self.cfg):
                # DLSS is optional in a native session. A failed download must
                # not prevent playing offline with native rendering.
                self._dlss_prepared = self._dlss_preparing
                self.start_game()
                self.play.show_message(f"Native rendering is available. Could not set up NVIDIA DLSS: {message}",
                                       True, keep=True)
            else:
                self._dlss_prepared = None
                self.play.show_message(f"Could not set up NVIDIA DLSS: {message}", True, keep=True)

    def stop_game(self):
        if self.saves.busy():
            self.saves.cancel()
            return
        if self._dlss_task is not None:
            self._dlss_task.cancel()
            self.status("Cancelling the DLSS download…")
            return
        if self.session is not None:
            self.session.stop()

    def _show_coop_notes(self):
        """When the co-op results thread is done: what it added to the career, and the results
        that wait (an event this career has not unlocked), under the Play page's last message."""
        if self._coop_thread is not None and self._coop_thread.is_alive():
            return
        self._coop_timer.stop()
        self._coop_thread = None
        notes, self._coop_notes = self._coop_notes, []
        if notes and self.session is None:
            current = self.play.msg.text()
            self.play.show_message("\n".join(([current] if current else []) + notes))

    def update_haptics(self, settings):
        if self.session:
            self.session.update_haptics(settings)

    def _poll(self):
        if self.session is None:
            self.poll.stop()
            return
        code = self.session.poll()
        if code is None:
            monitor = getattr(self.session, "shader_warmup", None)
            warmup = monitor.poll() if monitor is not None else None
            if warmup != self._shader_warmup_status:
                self._shader_warmup_status = warmup
                self.play.set_shader_warmup(warmup)
            status = dlss.session_status(self.session_plan)
            if status and status != self._dlss_session_status:
                self._dlss_session_status = status
                error, message = status
                self.play.show_message(message, error, keep=error)
            return
        if code == game.RELAUNCH_EXIT_CODE and not self._close_after_game:
            try:
                if self.session.relaunch():
                    return
            except (OSError, ValueError) as e:
                paths.log_error("start the game again", e)
        self.poll.stop()
        stopped = getattr(self.session, "stop_requested", False) is True
        self.session.cleanup()
        self.session = None
        try:  # shader lists from play (#276): in the background, silent, never holds up Play
            shader_share.share_in_background(self.cfg, self.session_plan)
        except Exception as e:
            paths.log_error("start the shader list upload", e)
        try:  # co-op campaign results (#309): the player's own festival event results to its save
            profile = getattr(self.session_plan, "save_profile", None)
            coop_grant.record_session(self.cfg, profile, getattr(self, "_session_started", 0),
                                      time.time())
            notes = self._coop_notes = []
            self._coop_thread = coop_grant.after_session_in_background(
                self.cfg, profile, lambda line: (print(line), notes.append(line)))
            if self._coop_thread is not None:
                self._coop_timer.start()
        except Exception as e:
            paths.log_error("start applying co-op results", e)
        self._shader_warmup_status = None
        self.play.set_shader_warmup(None)
        plan = self.session_plan
        self.play.clear_message()
        if not game.exit_is_crash(plan, code, stopped):
            self.play.show_message("The game closed.")
        else:
            self.play.show_message(game.crash_reason(plan, code), True, keep=True)
            if code != game.CPU_EXIT_CODE:
                self._prepare_crash_report(plan, code)
        self.play.refresh()

        if self._close_after_game:
            self.close()

    def _prepare_crash_report(self, plan, code):
        from .workers import Probe
        context = self._report_context or crash_report.launch_context(self.cfg, plan)
        probe = Probe(lambda: crash_report.collect(plan, code, context), "read crash report", self)
        self._crash_probe = probe
        probe.result.connect(self._crash_report_ready)
        probe.start()

    def _crash_report_ready(self, report):
        probe, self._crash_probe = self._crash_probe, None
        if probe is not None:
            probe.deleteLater()
        self._crash_report = report
        self.play.crash_report.setVisible(report is not None)
        if report is None:
            self.status(f"Could not prepare the crash report. Details: {paths.launcher_log()}", True)
        self.play.refresh()

    def review_crash_report(self):
        if self._crash_report is None:
            return
        if self._crash_dialog is not None:
            self._crash_dialog.raise_()
            self._crash_dialog.activateWindow()
            return
        from .crash_report import CrashReportDialog
        dialog = CrashReportDialog(self._crash_report, self)
        self._crash_dialog = dialog
        dialog.finished.connect(self._crash_report_closed)
        dialog.open()

    def _crash_report_closed(self, _result):
        dialog, self._crash_dialog = self._crash_dialog, None
        if dialog is not None:
            self._crash_report = crash_report.CrashReport(dialog.title.text(), dialog.body.toPlainText(),
                                                        dialog.report.log_path)
            dialog.deleteLater()


def run(argv):
    cleanenv.restore_process_environment()   # programs Qt starts (xdg-open) must not inherit _internal
    from PySide6.QtCore import Qt as _Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(_Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0]] + argv)
    app.setApplicationName("forza-reimagined-launcher")
    app.setDesktopFileName(paths.FLATPAK_ID or "forza-reimagined")
    # Qt file dialogs participate in controller navigation. In Flatpak they also keep
    # the real paths needed for a Games on Demand package and its adjacent .data folder.
    app.setAttribute(_Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app.setStyleSheet(theme.QSS)

    def report(exc_type, exc, tb):
        """Any exception that reaches the top (a slot, a thread): log it and say so, never vanish."""
        exc.__traceback__ = tb
        log = paths.log_error(f"unhandled {exc_type.__name__}", exc)
        if threading.current_thread() is not threading.main_thread():
            return   # a dialog must come from the GUI thread; the task wrappers report their own failures
        try:
            QMessageBox.critical(None, "Unexpected error", f"{exc_type.__name__}: {exc}\n\nDetails: {log}")
        except Exception:
            pass
    sys.excepthook = report
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback)
    win = MainWindow(Config())
    from .gamepad import Navigation
    navigation = Navigation(win)
    app.aboutToQuit.connect(navigation.close)
    win.show()
    win.play.play.setFocus()
    QTimer.singleShot(0, navigation.start)
    return app.exec()
