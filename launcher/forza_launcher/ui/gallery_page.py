"""Local photo gallery, with cancellable background discovery and image decoding."""
from datetime import datetime
from math import ceil
from pathlib import Path

from PySide6.QtCore import QEvent, QThread, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QListView, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import browser, gallery, paths
from . import theme


def text_label(text="", name=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


class GalleryTask(QThread):
    photo = Signal(object, object)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work

    def run(self):
        try:
            result = self.work(self)
            if not self.isInterruptionRequested():
                self.result.emit(result)
        except Exception as exc:
            log = paths.log_error("Photo gallery", exc)
            self.error.emit(f"{exc}\nDetails: {log}")


class PhotoGrid(QListWidget):
    """Fit complete rows of pictures to the available width, without captions."""
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout_photos()

    def layout_photos(self):
        width = max(1, self.viewport().width())
        columns = max(1, width // 220)
        # QListView also reserves spacing at the row edges. Without this room,
        # an exact-width row wraps its last thumbnail onto the next line.
        cell = max(1, (width - 2 * self.spacing()) // columns)
        picture = max(1, cell - 28)
        height = round(picture * 9 / 16)
        self.setIconSize(QSize(picture, height))
        self.setGridSize(QSize(cell, height + 28))
        for i in range(self.count()):
            self.item(i).setSizeHint(QSize(max(1, cell - 12), height + 16))


class PhotoCanvas(QLabel):
    clicked = Signal(object)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(event.position().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)


class PhotoPreview(QDialog):
    def __init__(self, photos, index, profile, parent):
        super().__init__(parent)
        self.photos, self.index, self.profile = photos, index, profile
        self.task = None
        self.closing = False
        self.image = None
        self.source_pixmap = None
        self.actual_size = False
        self.resize(1000, 720)
        self.setMinimumSize(600, 440)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        self.heading = text_label(name="cardTitle")
        layout.addWidget(self.heading)
        self.canvas = PhotoCanvas()
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setMinimumSize(1, 1)
        self.canvas.setCursor(Qt.PointingHandCursor)
        self.canvas.clicked.connect(self.toggle_zoom)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidget(self.canvas)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)
        self.details = text_label(name="muted")
        layout.addWidget(self.details)
        self.message = text_label(name="muted")
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.zoom = QPushButton("100%")
        self.export = QPushButton("Export photo…")
        close = QPushButton("Close")
        self.previous.clicked.connect(lambda: self.navigate(-1))
        self.next.clicked.connect(lambda: self.navigate(1))
        self.zoom.clicked.connect(lambda: self.toggle_zoom())
        self.export.clicked.connect(self.export_current)
        close.clicked.connect(self.reject)
        for button in (self.previous, self.next, self.zoom, self.export):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.navigate(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.navigate(1))
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.set_actual_size(True))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.set_actual_size(False))
        self.set_actual_size(False)
        self.load()

    def start(self, work, receive):
        self.task = GalleryTask(work, self)
        self.task.result.connect(receive)
        self.task.error.connect(self.message.setText)
        self.task.finished.connect(self.finished_task)
        self.controls(False)
        self.task.start()

    def controls(self, ready):
        self.previous.setEnabled(ready and self.index > 0)
        self.next.setEnabled(ready and self.index + 1 < len(self.photos))
        self.export.setEnabled(ready and self.image is not None)
        self.zoom.setEnabled(ready and self.image is not None)

    def finished_task(self):
        self.task.deleteLater()
        self.task = None
        self.controls(True)
        if self.closing:
            super().reject()

    def load(self):
        photo = self.photos[self.index]
        self.image = None
        self.source_pixmap = None
        self.canvas.clear()
        self.canvas.resize(1, 1)
        self.set_actual_size(False)
        self.setWindowTitle("Photo gallery")
        self.heading.setText(f"Photo {self.index + 1} of {len(self.photos)}")
        self.details.setText(f"Source: {photo.width} × {photo.height}  ·  File modified "
                             + datetime.fromtimestamp(photo.modified).strftime("%d %b %Y, %H:%M"))
        self.message.setText("Loading photo…")
        self.start(lambda task: gallery.read_photo(photo.path)[0], self.loaded)

    def loaded(self, image):
        self.image = image
        self.source_pixmap = QPixmap.fromImage(image)
        self.message.setText("")
        self.paint_photo()

    def paint_photo(self):
        if self.source_pixmap is None:
            return
        dpr = self.scroll.viewport().devicePixelRatioF()
        pixmap = QPixmap(self.source_pixmap)
        if not self.actual_size:
            bounds = QSize(max(1, round(self.scroll.viewport().width() * dpr)),
                           max(1, round(self.scroll.viewport().height() * dpr)))
            # Never enlarge a small source and present interpolation as detail.
            pixmap = pixmap.scaled(bounds.boundedTo(pixmap.size()),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        # At 100%, one source pixel maps to one display pixel, including HiDPI.
        pixmap.setDevicePixelRatio(dpr)
        self.canvas.setPixmap(pixmap)
        self.canvas.resize(pixmap.deviceIndependentSize().toSize())

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self.paint_photo()
        return super().eventFilter(watched, event)

    def set_actual_size(self, enabled):
        self.actual_size = enabled
        self.zoom.setText("Fit to window" if enabled else "100%")
        hint = ("Click to fit the photo to the window. Scroll to explore."
                if enabled else "Click to view at 100% (one image pixel per display pixel).")
        self.canvas.setToolTip(hint)
        self.zoom.setToolTip("Fit to window (Ctrl+0)" if enabled else "Actual size (Ctrl+1)")
        policy = Qt.ScrollBarAsNeeded if enabled else Qt.ScrollBarAlwaysOff
        self.scroll.setHorizontalScrollBarPolicy(policy)
        self.scroll.setVerticalScrollBarPolicy(policy)
        self.paint_photo()

    def toggle_zoom(self, point=None):
        if self.task is not None or self.image is None:
            return
        # Keep the clicked part of a fitted image in view when enlarging it.
        x = point.x() / max(1, self.canvas.width()) if point is not None else .5
        y = point.y() / max(1, self.canvas.height()) if point is not None else .5
        self.set_actual_size(not self.actual_size)
        if self.actual_size:
            self.scroll.horizontalScrollBar().setValue(round(x * self.canvas.width() - self.scroll.viewport().width() / 2))
            self.scroll.verticalScrollBar().setValue(round(y * self.canvas.height() - self.scroll.viewport().height() / 2))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.paint_photo()

    def navigate(self, direction):
        index = self.index + direction
        if self.task is None and 0 <= index < len(self.photos):
            self.index = index
            self.load()

    def export_current(self):
        if self.task is not None or self.image is None:
            return
        name, selected = QFileDialog.getSaveFileName(
            self, "Export photo", datetime.fromtimestamp(self.photos[self.index].modified).strftime(
                "Forza-photo-%Y%m%d-%H%M%S.png"),
            "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not name:
            return
        destination = Path(name)
        if not destination.suffix:
            destination = destination.with_suffix(".jpg" if selected.startswith("JPEG") else ".png")
            if destination.exists() and QMessageBox.question(
                    self, "Replace exported photo?", f"{destination.name} already exists. Replace it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        image = self.image
        self.message.setText("Exporting photo…")
        self.start(lambda task: gallery.export_photo(image, destination, self.profile),
                   lambda _: self.message.setText(f"Exported to {destination}"))

    def reject(self):
        if self.task is not None:
            self.closing = True
            self.task.requestInterruption()
            self.message.setText("Finishing photo task…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.task is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class GalleryPage(QWidget):
    idle = Signal()

    def __init__(self, cfg, window):
        super().__init__()
        self.cfg, self.window = cfg, window
        self.task = None
        self.profile = None
        self.entries = []
        self.pending_refresh = False
        self.stopping = False
        self.preview = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(text_label("Photo gallery", "pageTitle"), 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        heading.addWidget(self.refresh_button)
        outer.addLayout(heading)
        outer.addWidget(text_label("Your saved Photo Mode pictures, ready to view and export.", "pageLead"))
        controls = QHBoxLayout()
        self.location = text_label(name="muted")
        controls.addWidget(self.location, 1)
        self.sort = QComboBox()
        self.sort.addItems(("Newest first", "Oldest first"))
        self.sort.setAccessibleName("Sort photos")
        self.sort.setToolTip("Order by the saved image file's modification time.")
        self.sort.currentIndexChanged.connect(self.rebuild)
        controls.addWidget(self.sort)
        outer.addLayout(controls)
        self.message = text_label(name="muted")
        outer.addWidget(self.message)
        self.grid = PhotoGrid()
        self.grid.setAccessibleName("Saved photos. Press Enter to view a photo.")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setSpacing(6)
        self.grid.setWordWrap(False)
        self.grid.setTextElideMode(Qt.ElideRight)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.grid.setStyleSheet(f"QListWidget {{ background: transparent; border: none; }} "
            f"QListWidget::item {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 10px; padding: 6px; }} "
            f"QListWidget::item:selected {{ border: 2px solid {theme.ORANGE}; background: {theme.PANEL2}; }}")
        self.grid.itemActivated.connect(self.open_photo)
        self.grid.itemClicked.connect(self.open_photo)
        self.grid.itemSelectionChanged.connect(self.selection_changed)
        outer.addWidget(self.grid, 1)
        self.empty = text_label("No saved photos yet.\n\nIn the game, open Photo Mode, take a picture, "
                                "then choose Save. Return here and press Refresh.", "pageLead")
        self.empty.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.empty, 1)
        bottom = QHBoxLayout()
        self.view = QPushButton("View photo")
        self.view.setEnabled(False)
        self.view.clicked.connect(lambda: self.open_photo(self.grid.currentItem()))
        self.folder_button = QPushButton("Open save folder")
        self.folder_button.clicked.connect(self.open_folder)
        self.folder_button.setEnabled(False)
        bottom.addWidget(self.view)
        bottom.addStretch()
        bottom.addWidget(self.folder_button)
        outer.addLayout(bottom)
        self.grid.hide()

    def event(self, event):
        result = super().event(event)
        if (event.type() == QEvent.DevicePixelRatioChange and hasattr(self, "grid")
                and not self.stopping):
            self.refresh()
        return result

    def refresh(self):
        if self.stopping:
            return
        if self.task is not None:
            self.pending_refresh = True
            self.task.requestInterruption()
            return
        self.entries.clear()
        self.grid.clear()
        self.view.setEnabled(False)
        self.folder_button.setEnabled(False)
        try:
            self.profile = gallery.profile_folder(self.cfg)
        except (OSError, ValueError) as exc:
            self.profile = None
            self.location.setText("")
            self.message.setText(str(exc))
            self.empty.setText("Select a profile to browse its saved photos.")
            self.empty.show()
            self.grid.hide()
            return
        self.location.setText("Imported Xenia profile" if self.cfg.profile["mode"] == "xenia" else "Current profile")
        self.location.setToolTip(str(self.profile))
        self.folder_button.setToolTip(str(self.profile))
        self.message.setText("Looking for saved photos…")
        self.empty.setText("Loading your photo gallery…")
        self.empty.show()
        self.grid.hide()
        self.refresh_button.setEnabled(False)
        profile = self.profile
        # Tiles stay below 400 logical pixels at every window width. Keep enough
        # detail for their largest size, including HiDPI, without caching originals.
        # Two samples per logical pixel also give smooth downsampling at 100% scale.
        scale = max(2.0, self.grid.devicePixelRatioF())
        thumbnail_size = (ceil(400 * scale), ceil(225 * scale))

        def scan(task):
            skipped = 0
            for directory, files in gallery.photo_groups(profile, task.isInterruptionRequested):
                if task.isInterruptionRequested():
                    break
                result = gallery.load_group(directory, files, task.isInterruptionRequested,
                                            thumbnail_size=thumbnail_size)
                if result:
                    task.photo.emit(*result)
                else:
                    skipped += 1
            return skipped

        self.task = GalleryTask(scan, self)
        self.task.photo.connect(self.add_photo)
        self.task.result.connect(self.scanned)
        self.task.error.connect(self.failed)
        self.task.finished.connect(self.finished_task)
        self.task.start()

    def add_photo(self, photo, image):
        if not self.pending_refresh and not self.stopping:
            self.entries.append((photo, QIcon(QPixmap.fromImage(image))))

    def scanned(self, skipped):
        self.rebuild()
        count = len(self.entries)
        self.message.setText(f"{count} saved photo{'s' if count != 1 else ''}" +
            (f" · {skipped} folder(s) had no readable image. They may still be saving or use an unsupported format." if skipped else ""))
        self.folder_button.setEnabled(self.profile.is_dir())

    def failed(self, message):
        self.rebuild()
        self.message.setText(message)
        if not self.entries:
            self.empty.setText("The photo folder could not be read. Check your data folder in Setup and try Refresh.")

    def finished_task(self):
        self.task.deleteLater()
        self.task = None
        self.refresh_button.setEnabled(True)
        if self.pending_refresh and not self.stopping:
            self.pending_refresh = False
            self.refresh()
        else:
            self.idle.emit()

    def rebuild(self, *_):
        selected = self.grid.currentItem()
        selected_path = selected.data(Qt.UserRole).path if selected else None
        order = self.sort.currentIndex()
        self.entries.sort(key=lambda e: ((-1 if order == 0 else 1) * e[0].modified, str(e[0].path)))
        self.grid.clear()
        for index, (photo, icon) in enumerate(self.entries):
            item = QListWidgetItem(icon, "")
            item.setData(Qt.UserRole, photo)
            description = f"Photo {index + 1} · {photo.width} × {photo.height}\nFile modified " + \
                datetime.fromtimestamp(photo.modified).strftime("%d %b %Y, %H:%M")
            item.setToolTip(description)
            item.setData(Qt.AccessibleTextRole, description)
            self.grid.addItem(item)
            if photo.path == selected_path:
                self.grid.setCurrentItem(item)
        self.grid.layout_photos()
        visible = self.grid.count()
        self.grid.setVisible(bool(visible))
        self.empty.setVisible(not visible)
        self.empty.setText("No saved photos yet.\n\nIn the game, open Photo Mode, take a picture, then choose Save. "
                           "Return here and press Refresh.")
        self.selection_changed()

    def selection_changed(self):
        item = self.grid.currentItem()
        self.view.setEnabled(item is not None and not item.isHidden())

    def open_photo(self, item):
        if item is None or item.isHidden() or self.preview is not None:
            return
        photos = [self.grid.item(i).data(Qt.UserRole) for i in range(self.grid.count())
                  if not self.grid.item(i).isHidden()]
        self.preview = PhotoPreview(photos, photos.index(item.data(Qt.UserRole)), self.profile, self)
        self.preview.finished.connect(self.preview_closed)
        self.preview.setModal(True)
        self.preview.show()

    def preview_closed(self):
        self.preview.deleteLater()
        self.preview = None
        self.idle.emit()

    def open_folder(self):
        if self.profile and self.profile.is_dir():
            if not browser.open_url(QUrl.fromLocalFile(str(self.profile))):
                self.message.setText("The save folder could not be opened. Its path is shown below.")

    def shutdown(self):
        """Defer window destruction until workers finish, including preview/export."""
        self.stopping = True
        self.pending_refresh = False
        if self.task:
            self.task.requestInterruption()
        if self.preview:
            self.preview.reject()
        return self.task is None and self.preview is None
