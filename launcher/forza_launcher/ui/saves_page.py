"""Save management with all directory scans and file copies off the GUI thread."""
from copy import deepcopy
from datetime import datetime
import struct

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import paths, save_editor, saves
from ..fetch import Cancelled
from ..installer import InstallError
from .pages import open_folder, scroll_page
from .widgets import Switch, card, label
from .workers import Task
from . import theme


class FolderLabel(QLabel):
    def set_folder(self, folder):
        self.setToolTip(str(folder))
        self.setMinimumWidth(0)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.toolTip(), Qt.ElideMiddle, max(1, self.width())))

    def resizeEvent(self, event):
        self._elide()
        super().resizeEvent(event)


class SavesPage(QWidget):
    def __init__(self, cfg, window):
        super().__init__()
        self.cfg, self.window = cfg, window
        self.task = None
        self.closing = False
        self.profile = None
        self.entries = []
        self._profile_config = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc, lay = scroll_page('Saves', 'Keep a restore point for your next drive.')
        outer.addWidget(sc)

        c, cl = card(spacing=14)
        self.profile_label = label('Selected profile', 'cardTitle')
        row = QHBoxLayout()
        row.setSpacing(20)
        row.addWidget(self.profile_label, 1)
        self.edit_button = QPushButton('Edit profile…')
        self.edit_button.clicked.connect(self.edit_profile)
        row.addWidget(self.edit_button)
        self.location = FolderLabel()
        self.location.setObjectName('muted')
        self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addLayout(row)
        cl.addWidget(self.location)
        cl.addWidget(label('Uses the profile selected in Settings. Xenia backups use the launcher’s copy; '
                           'your original Xenia save stays untouched.', 'muted'))
        lay.addWidget(c)

        c, cl = card(spacing=14)
        row = QHBoxLayout()
        row.setSpacing(20)
        text = QVBoxLayout()
        text.setSpacing(5)
        text.addWidget(label('Back up before Play', 'cardTitle'))
        text.addWidget(label('A new automatic backup is kept when your save changes.', 'muted'))
        row.addLayout(text, 1)
        self.automatic = Switch()
        self.automatic.setAccessibleName('Back up before Play')
        self.automatic.setFocusPolicy(Qt.StrongFocus)
        self.automatic.setChecked(cfg.d['save_manager']['automatic'])
        row.addWidget(self.automatic)
        cl.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(label('Automatic backups to keep', wrap=False))
        self.retention = QSpinBox()
        self.retention.setRange(1, 50)
        self.retention.setValue(cfg.d['save_manager']['keep_automatic'])
        self.retention.setAccessibleName('Automatic backups to keep')
        self.retention.setMinimumWidth(90)
        self.retention.setMinimumHeight(36)
        row.addWidget(self.retention)
        row.addStretch()
        cl.addLayout(row)
        cl.addWidget(label('Named backups and backups made before a restore or edit are always kept.', 'muted'))
        lay.addWidget(c)
        self.automatic.toggled.connect(self._preferences)
        self.retention.valueChanged.connect(self._preferences)

        c, cl = card(spacing=16)
        cl.addWidget(label('Your backups', 'cardTitle'))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.name = QLineEdit()
        self.name.setPlaceholderText('Backup name (optional)')
        self.name.setAccessibleName('Backup name')
        self.name.setMaxLength(80)
        self.name.setMinimumHeight(40)
        self.name.setTextMargins(10, 3, 10, 3)
        row.addWidget(self.name, 1)
        self.create_button = QPushButton('Create backup')
        self.create_button.setMinimumHeight(40)
        self.create_button.clicked.connect(self.create_backup)
        self.name.returnPressed.connect(self.create_backup)
        row.addWidget(self.create_button)
        cl.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Name', 'Created', 'Type', 'Size'])
        self.table.setAccessibleName('Save backups')
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(210)
        self.table.setStyleSheet(f'''
            QTableWidget {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER};
                           gridline-color: {theme.BORDER}; selection-background-color: {theme.BORDER}; }}
            QTableWidget::item {{ padding: 8px; }}
            QTableWidget::item:selected {{ background: {theme.BORDER}; color: {theme.CREAM}; }}
            QHeaderView::section {{ padding: 8px; background: {theme.PANEL2}; border: none;
                                    border-bottom: 1px solid {theme.BORDER}; color: {theme.MUTED}; }}
        ''')
        self.table.itemSelectionChanged.connect(self._enable)
        cl.addWidget(self.table)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.restore_button = QPushButton('Restore selected')
        self.restore_button.clicked.connect(self.restore_selected)
        row.addWidget(self.restore_button)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        row.addStretch()
        self.folder_button = QPushButton('Open backup folder')
        self.folder_button.clicked.connect(self.open_backups)
        row.addWidget(self.folder_button)
        cl.addLayout(row)
        self.message = label('Open Saves to load your backups.', 'muted')
        cl.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.hide()
        cl.addWidget(self.progress)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        cl.addWidget(self.cancel_button, alignment=Qt.AlignLeft)
        lay.addWidget(c)
        lay.addWidget(label('Close games started outside this launcher before creating or restoring a backup.', 'muted'))
        lay.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._enable)
        self.timer.start()
        self._enable()

    def busy(self):
        return self.task is not None

    def _preferences(self):
        self.cfg.d['save_manager'] = dict(automatic=self.automatic.isChecked(),
                                        keep_automatic=self.retention.value())
        try:
            self.cfg.save()
        except OSError as exc:
            self.message.setText(f'Could not save backup preferences: {exc}')

    def _enable(self):
        changed = self._profile_config != (self.cfg.profile, self.cfg.advanced)
        idle = not self.busy() and not self.window.running() and not self.window._busy
        self.create_button.setEnabled(idle and self.profile is not None and not changed)
        self.edit_button.setEnabled(idle and self.profile is not None and not changed)
        self.restore_button.setEnabled(idle and not changed and self.table.currentRow() >= 0)
        self.refresh_button.setEnabled(not self.busy())
        self.folder_button.setEnabled(not self.busy() and self.profile is not None and not changed)
        self.name.setEnabled(idle)
        self.automatic.setEnabled(not self.busy())
        self.retention.setEnabled(not self.busy() and self.automatic.isChecked())
        if changed and self.profile is not None and not self.busy():
            self.message.setText('Profile settings changed. Refresh to load this profile’s backups.')

    def _run(self, title, fn, complete, abort_on_cancel=False, failed=None):
        if self.busy() or self.closing:
            return
        box = {}
        self.message.setText(title)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)

        def run(task):
            try:
                box['value'] = fn(task)
            except saves.Cancelled as exc:
                raise Cancelled() from exc
            except (saves.SaveError, OSError, ValueError) as exc:
                raise InstallError(str(exc)) from exc
            return ''

        self.task = Task(run, title)
        self._result = (False, 'The save operation did not finish.')
        self.task.done.connect(self._done)
        self.task.progress.connect(self._progress)

        def finished():
            task, self.task = self.task, None
            task.deleteLater()
            self.progress.hide()
            self.cancel_button.hide()
            self._enable()
            self.window.play.refresh()
            if self.closing:
                self.closing = False
                self.window.close()
                return
            ok, message = self._result
            if abort_on_cancel and task.cancelled():
                ok, message = False, 'Launch cancelled.'
            if ok:
                complete(box.get('value'))
            else:
                self.message.setText(message)
                self.window.play.show_message(message, True, keep=True)
                if failed is not None:
                    failed(message)

        self.task.finished.connect(finished)
        self._enable()
        self.window.play.refresh()
        self.task.start()

    def _done(self, ok, message):
        self._result = (ok, message)

    def _progress(self, fraction, _detail):
        self.progress.setRange(0, 100)
        self.progress.setValue(int(fraction * 100))

    def _copy_progress(self, task):
        return lambda done, total: task.progress.emit(done / total if total else 0, '')

    def cancel(self):
        if self.task:
            self.task.cancel()
            self.cancel_button.setEnabled(False)
            self.message.setText('Cancelling safely…')

    def shutdown(self):
        if not self.busy():
            return True
        self.closing = True
        self.cancel()
        return False

    def refresh(self):
        if self.busy():
            return
        cfg = deepcopy(self.cfg)
        signature = deepcopy((cfg.profile, cfg.advanced))

        def scan(task):
            profile = saves.profile_for(cfg)
            entries, errors = saves.snapshots(profile)
            return profile, entries, errors

        def loaded(value):
            self.profile, self.entries, errors = value
            self._profile_config = signature
            self.profile_label.setText(self.profile.label)
            self.location.set_folder(self.profile.folder)
            self.table.setRowCount(len(self.entries))
            for row, snap in enumerate(self.entries):
                date = datetime.fromisoformat(snap.created).astimezone().strftime('%d %b %Y, %H:%M')
                kind = {'manual': 'Named', 'automatic': 'Automatic', 'before-restore': 'Before restore',
                        'before-edit': 'Before edit'}[snap.kind]
                size = f'{snap.size / 2**20:.1f} MB' if snap.size >= 2**20 else f'{max(1, snap.size / 1024):.0f} KB'
                for col, text in enumerate((snap.label, date, kind, size)):
                    item = QTableWidgetItem(text)
                    item.setToolTip(snap.label if col == 0 else text)
                    self.table.setItem(row, col, item)
            self.message.setText((f'{len(self.entries)} backup(s).' if self.entries else
                                  'No backups yet. Create one here or press Play to back up automatically.') +
                                 (f' {errors} unreadable backup(s) skipped.' if errors else ''))
            self._enable()

        self._run('Loading backups…', scan, loaded)

    def create_backup(self):
        if not self.create_button.isEnabled():
            return
        profile, name = self.profile, self.name.text()

        def complete(snapshot):
            if snapshot is None:
                self.message.setText('No save files yet. Play the game and save your progress first.')
            else:
                self.name.clear()
                self.refresh()

        self._run('Creating backup…', lambda t: saves.create(profile, name, cancelled=t.cancelled,
                  progress=self._copy_progress(t)), complete)

    def restore_selected(self):
        row = self.table.currentRow()
        if not self.restore_button.isEnabled() or row < 0:
            return
        snapshot, profile = self.entries[row], self.profile
        if QMessageBox.question(self, 'Restore backup', f'Restore “{snapshot.label}” to {profile.label}?\n\n'
                                'Your current save will be backed up before it is replaced.',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._run('Restoring backup…', lambda t: saves.restore(profile, snapshot, cancelled=t.cancelled,
                  progress=self._copy_progress(t)), lambda _: self.refresh())

    def open_backups(self):
        if self.profile is not None and self.folder_button.isEnabled():
            try:
                open_folder(self.profile.backups)
            except OSError as exc:
                self.message.setText(str(exc))

    def edit_profile(self):
        if not self.edit_button.isEnabled():
            return
        from .save_editor import ProfileEditor
        self.editor = ProfileEditor(self)
        self.editor.finished.connect(lambda _: self.refresh())
        self.editor.show()
        self.editor.reload()

    def before_launch(self, plan, launch):
        profile = plan.save_profile
        if profile is None:
            launch()
            return
        # a start on an FM4 circuit (experimental, #648) always backs the save up first
        automatic = self.cfg.d['save_manager']['automatic'] or bool(getattr(plan, 'fm4_track', ''))
        keep = self.cfg.d['save_manager']['keep_automatic']
        # New profiles need neither a directory scan nor a backup worker.
        if not profile.folder.exists() and not (profile.folder.parent / '.save-manager-restore.json').exists():
            launch()
            return

        def prepare(task):
            if automatic:
                saves.create(profile, kind='automatic', keep=keep, cancelled=task.cancelled,
                             progress=self._copy_progress(task))
            else:
                saves.recover(profile)
            # the FM4 circuits' states out of the profile before it overflows (#850)
            try:
                return save_editor.trim_circuit_states(
                    self.cfg, profile, getattr(plan, 'fm4_track', '') or '', task.cancelled,
                    self._copy_progress(task), keep_automatic=keep)
            except saves.Cancelled:
                raise
            except (saves.SaveError, OSError, ValueError, struct.error) as exc:
                paths.log_error('check the save profile before the start', exc)
                return None

        def complete(trim):
            note = trim.note() if trim is not None else ''
            if note:  # before the start, so a start error still shows over it
                self.message.setText(note)
                self.window.play.show_message(note, True, keep=True)
            launch()

        self._run('Backing up saves…' if automatic else 'Checking saves…', prepare, complete,
                  abort_on_cancel=True)
