"""Search existing settings controls without duplicating their state."""
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget)


@dataclass
class SearchTarget:
    label: str
    group: str
    section: str
    widget: QWidget
    focus: QWidget
    keywords: str

    def rank(self, query):
        title = self.label.casefold()
        text = " ".join((title, self.group, self.section, self.keywords)).casefold()
        if not all(word in text for word in query.split()):
            return None
        return 0 if query == title else 1 if title.startswith(query) else 2 if query in title else 3


class SettingsSearch(QWidget):
    chosen = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.targets = []
        self._watching_outside = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search settings… (Ctrl+F)")
        self.edit.setAccessibleName("Search settings")
        self.edit.setClearButtonEnabled(True)
        self.count = QLabel()
        bar.addWidget(self.edit, 1)
        bar.addWidget(self.count)
        layout.addLayout(bar)
        # A sibling overlay stays above the category tabs without taking layout space.
        self.results = QListWidget(parent)
        self.results.setObjectName("settingsSearchResults")
        self.results.setAccessibleName("Settings search results")
        self.results.setWordWrap(True)
        self.results.hide()
        self.count.hide()
        self.edit.textChanged.connect(self.refresh)
        self.edit.returnPressed.connect(self._choose_current)
        self.results.itemClicked.connect(self._choose)
        self.installEventFilter(self)
        self.edit.installEventFilter(self)
        self.results.installEventFilter(self)
        self.shortcut = QShortcut(QKeySequence.Find, parent)
        self.shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut.activated.connect(self.focus_search)

    def add(self, label, group, section, widget, focus=None, keywords=""):
        focus = focus if focus is not None else widget
        if not focus.accessibleName():
            focus.setAccessibleName(label)
        self.targets.append(SearchTarget(label, group, section, widget, focus, keywords))

    def add_setting(self, setting, row, group=None):
        """group: the Settings category the row shows in (default: the setting's schema group)."""
        choices = " ".join(str(value) + " " + str(label) for value, label in setting.choices)
        self.add(setting.label, group or setting.group, setting.section, row, row.control,
                 " ".join((setting.key, setting.help, choices)))

    def clear_targets(self):
        self.results.clear()
        self.targets.clear()
        self.dismiss()

    def refresh(self):
        query = self.edit.text().strip().casefold()
        self.results.clear()
        if not query:
            self.count.hide()
            self.dismiss()
            return
        matches = []
        for index, target in enumerate(self.targets):
            rank = target.rank(query)
            if rank is not None and not target.widget.isHidden():
                matches.append((rank, index, target))
        for _, _, target in sorted(matches):
            item = QListWidgetItem(f"{target.label}\n{target.group} · {target.section}")
            item.setData(Qt.UserRole, target)
            item.setSizeHint(QSize(0, 54))
            item.setToolTip(target.label + "\n" + target.keywords)
            self.results.addItem(item)
        total = len(matches)
        self.count.setText(f"{total} setting{'s' if total != 1 else ''} found" if total else "No settings found")
        self.count.show()
        if total:
            self._show_results()
            self.results.setCurrentRow(0)
        else:
            self.dismiss()

    def _show_results(self):
        self.results.show()
        self._position_results()
        self.results.raise_()
        if not self._watching_outside:
            QApplication.instance().installEventFilter(self)
            self._watching_outside = True

    def _position_results(self):
        if self.results.isHidden():
            return
        origin = self.mapTo(self.parentWidget(), QPoint(0, self.height() + 8))
        height = min(min(self.results.count(), 4) * 54 + 8,
                     max(0, self.parentWidget().height() - origin.y() - 8))
        self.results.setGeometry(origin.x(), origin.y(), self.width(), height)

    def dismiss(self):
        self.results.hide()
        if self._watching_outside:
            QApplication.instance().removeEventFilter(self)
            self._watching_outside = False

    def focus_search(self):
        self.edit.setFocus(Qt.ShortcutFocusReason)
        self.edit.selectAll()
        self.refresh()

    def _choose_current(self):
        if not self.results.isHidden() and self.results.currentItem():
            self._choose(self.results.currentItem())

    def _choose(self, item):
        target = item.data(Qt.UserRole)
        self.dismiss()
        self.chosen.emit(target)

    def eventFilter(self, watched, event):
        if watched is self and event.type() == QEvent.Hide:
            self.dismiss()
        if watched is self and event.type() in (QEvent.Move, QEvent.Resize):
            self._position_results()
        if self.results.isVisible() and event.type() in (QEvent.MouseButtonPress, QEvent.FocusIn):
            if isinstance(watched, QWidget) and not (
                    watched is self or self.isAncestorOf(watched) or
                    watched is self.results or self.results.isAncestorOf(watched)):
                self.dismiss()
        if watched in (self.edit, self.results) and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.edit.clear()
                self.edit.setFocus()
                return True
            if watched is self.edit and event.key() == Qt.Key_Down and self.results.count():
                self._show_results()
                self.results.setFocus()
                return True
            if watched is self.results and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._choose_current()
                return True
        return super().eventFilter(watched, event)
