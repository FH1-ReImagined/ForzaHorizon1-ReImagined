"""Browse and preserve local design catalogs without changing the source save."""
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QBuffer, QIODevice, QModelIndex, QSize,
                            QSortFilterProxyModel, QStandardPaths, QThread, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QProgressBar, QPushButton, QScrollArea,
                               QSplitter, QStackedWidget, QVBoxLayout, QWidget, QMessageBox)

from .. import TITLE_ID, browser, paths, ugc
from . import theme
from .widgets import label

CATALOG_PAGE_ID = 7  # Preserve main: Gallery 4, Controls 5, Saves 6.
MAX_PREVIEWS = 16 * 1024**2


def read_previews(source, task):
    """Decode thumbnails off the GUI thread; keep only bounded, scaled PNGs."""
    catalog = ugc.read_catalog(source, task.isInterruptionRequested)
    paths.ensure_tools_importable()
    import xdc_decode
    previews, errors, total = {}, {}, 0
    for i, item in enumerate(catalog.items):
        if task.isInterruptionRequested():
            raise ugc.CatalogError('Opening cancelled.')
        task.status.emit(f'Reading previews… {i+1} of {len(catalog.items)}')
        raw = item.files.get('thumb.xdc')
        if not raw:
            errors[item.path] = 'This item has no saved preview. Its original files can still be exported.'
            continue
        if total >= MAX_PREVIEWS:
            errors[item.path] = 'Preview memory limit reached. The original files can still be exported.'
            continue
        try:
            width, height, rgba = xdc_decode.decode(raw)
            image = QImage(rgba, width, height, width*4, QImage.Format_RGBA8888)
            image = image.scaled(480, 270, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, 'PNG'):
                raise ValueError('Could not decode the saved preview.')
            png = bytes(buffer.data())
            if total+len(png) > MAX_PREVIEWS:
                errors[item.path] = 'Preview memory limit reached. The original files can still be exported.'
                continue
            previews[item.path] = png
            total += len(png)
        except ValueError as exc:
            errors[item.path] = f'Preview unavailable: {exc} The original files can still be exported.'
    if task.isInterruptionRequested():
        raise ugc.CatalogError('Opening cancelled.')
    return catalog, previews, errors


class CatalogTask(QThread):
    result = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            result = self.fn(self)
            # A completed export is reported even if Cancel arrived after the atomic finish.
            self.result.emit(result)
        except (ugc.CatalogError, OSError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            log = paths.log_error('design catalog', exc)
            self.failed.emit(f'Could not finish the catalog operation. Try again. Details: {log}')


class CatalogModel(QAbstractListModel):
    ItemRole = Qt.UserRole+1
    SearchRole = Qt.UserRole+2

    def __init__(self, parent):
        super().__init__(parent)
        self.catalog = None
        self.previews = {}
        self.icons = OrderedDict()

    def replace(self, catalog, previews):
        self.beginResetModel()
        self.catalog, self.previews = catalog, previews
        self.icons.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.catalog is None else len(self.catalog.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self.rowCount():
            return None
        item = self.catalog.items[index.row()]
        if role == Qt.DisplayRole:
            return item.title
        if role == Qt.ToolTipRole:
            return f'{item.title}\nCreator: {", ".join(item.creators) or "Not recorded"}'
        if role == self.ItemRole:
            return item
        if role == self.SearchRole:
            return ' '.join((item.title, *item.creators, str(item.model_id or ''))).casefold()
        if role == Qt.DecorationRole:
            row = index.row()
            if row not in self.icons:
                pix = QPixmap()
                if item.path in self.previews:
                    pix.loadFromData(self.previews[item.path], 'PNG')
                    pix = pix.scaled(176, 99, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if pix.isNull():
                    pix = QPixmap(176, 99)
                    pix.fill(QColor(theme.PANEL2))
                    painter = QPainter(pix)
                    painter.setPen(QColor(theme.MUTED))
                    painter.drawText(pix.rect(), Qt.AlignCenter, 'No saved preview')
                    painter.end()
                self.icons[row] = QIcon(pix)
                if len(self.icons) > 128:
                    self.icons.popitem(last=False)
            self.icons.move_to_end(row)
            return self.icons[row]
        return None


class CatalogFilter(QSortFilterProxyModel):
    query = ''
    kind = ''

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        item = index.data(CatalogModel.ItemRole)
        return ((not self.kind or item.kind == self.kind) and
                self.query in index.data(CatalogModel.SearchRole))


class CatalogsPage(QWidget):
    def __init__(self, cfg, window):
        super().__init__()
        self.cfg, self.window = cfg, window
        self.task = None
        self.closing = False
        self.catalog = None
        self.preview_errors = {}
        self.exported = None
        self.preview_pix = QPixmap()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(12)
        outer.addWidget(label('Designs & vinyls', 'pageTitle'))
        outer.addWidget(label('Browse saved designs and vinyl groups. Import an FH1 design or preserve an archival copy.', 'pageLead'))

        actions = QHBoxLayout()
        self.open_package = QPushButton('Open save package…')
        self.open_folder = QPushButton('Open catalog folder…')
        self.export = QPushButton('Export catalog…')
        self.export.setToolTip('Export all original files, creator details and PNG previews to a new folder.')
        self.open_package.clicked.connect(self.choose_package)
        self.open_folder.clicked.connect(self.choose_folder)
        self.export.clicked.connect(self.choose_export)
        actions.addWidget(self.open_package)
        actions.addWidget(self.open_folder)
        actions.addStretch()
        actions.addWidget(self.export)
        outer.addLayout(actions)
        self.source = label('No catalog open', 'muted')
        self.source.setTextFormat(Qt.PlainText)
        self.source.setMinimumWidth(0)
        outer.addWidget(self.source)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search designs, creators or car IDs')
        self.search.setAccessibleName('Search designs, creators or car IDs')
        self.kind = QComboBox()
        self.kind.setAccessibleName('Content type')
        for text, value in [('All content', ''), ('Designs', 'livery'), ('Vinyl groups', 'vinyl-group')]:
            self.kind.addItem(text, value)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.kind)
        outer.addLayout(filters)
        self.count = label('', 'muted')
        outer.addWidget(self.count)

        self.content = QStackedWidget()
        self.empty = label('Keep your old designs\n\nOpen a LivCatalog or LivGroups save package, or its extracted folder. Your original save stays untouched.', 'muted')
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setContentsMargins(28, 16, 28, 16)
        self.content.addWidget(self.empty)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName('catalogSplitter')
        self.model = CatalogModel(self)
        self.proxy = CatalogFilter(self)
        self.proxy.setSourceModel(self.model)
        self.grid = QListView()
        self.grid.setObjectName('catalogGrid')
        self.grid.setAccessibleName('Saved designs and vinyl groups')
        self.grid.setModel(self.proxy)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setIconSize(QSize(176, 99))
        self.grid.setGridSize(QSize(188, 144))
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setMinimumWidth(200)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.selectionModel().currentChanged.connect(self.selection_changed)
        self.splitter.addWidget(self.grid)
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(16, 0, 4, 4)
        dl.setSpacing(12)
        self.preview = label('', 'muted')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(140)
        self.preview.setMinimumWidth(0)
        dl.addWidget(self.preview)
        self.title = label('', 'cardTitle')
        self.title.setTextFormat(Qt.PlainText)
        dl.addWidget(self.title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.fields = {}
        for name in ('Game', 'Type', 'Creator', 'Car ID', 'Declared layers', 'Storefront'):
            value = label('', 'muted')
            value.setTextFormat(Qt.PlainText)
            value.setMinimumWidth(0)
            form.addRow(label(name, 'muted'), value)
            self.fields[name] = value
        self.fields['Declared layers'].setToolTip('Reported by the saved filename; the layer data has not been interpreted.')
        dl.addLayout(form)
        self.preview_note = label('', 'help')
        self.preview_note.setTextFormat(Qt.PlainText)
        dl.addWidget(self.preview_note)
        self.import_button = QPushButton('Import design…')
        self.import_button.setToolTip('Import the selected FH1 design into your active local profile, after backing up its catalog.')
        self.import_button.clicked.connect(self.choose_import)
        dl.addStretch()
        details = QScrollArea()
        details.setWidgetResizable(True)
        details.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        details.setWidget(detail)
        details.setMinimumWidth(240)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(details, 1)
        right_layout.addWidget(self.import_button)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([480, 260])
        self.splitter.splitterMoved.connect(lambda *_: self._fit_preview())
        self.content.addWidget(self.splitter)
        outer.addWidget(self.content, 1)
        outer.addWidget(label('Import adds a design to your active profile’s saved designs. Apply it to the matching car in-game. Vinyl groups and other Forza games can be exported only.', 'help'))
        self.message = label('', 'muted')
        self.message.setTextFormat(Qt.PlainText)
        outer.addWidget(self.message)
        progress = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setAccessibleName('Catalog operation progress')
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.cancel_task)
        self.open_export = QPushButton('Open export folder')
        self.open_export.clicked.connect(self.show_export)
        progress.addWidget(self.bar, 1)
        progress.addWidget(self.cancel)
        progress.addWidget(self.open_export)
        outer.addLayout(progress)
        self.search.textChanged.connect(self.filter_changed)
        self.kind.currentIndexChanged.connect(self.filter_changed)
        self._controls()

    def busy(self):
        return self.task is not None

    def _controls(self):
        busy = self.busy()
        self.open_package.setEnabled(not busy)
        self.open_folder.setEnabled(not busy)
        self.export.setEnabled(not busy and self.catalog is not None and bool(self.catalog.items))
        self.bar.setVisible(busy)
        self.cancel.setVisible(busy)
        self.cancel.setEnabled(busy)
        self.open_export.setVisible(not busy and self.exported is not None)
        item = self.grid.currentIndex().data(CatalogModel.ItemRole)
        self.import_button.setEnabled(not busy and self.catalog is not None and
            self.catalog.title_id == TITLE_ID and bool(self.catalog.index) and
            self.catalog.profile_id is not None and item is not None and item.kind == 'livery')

    def choose_package(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open a LivCatalog or LivGroups save', str(self.cfg.data_home()), 'All files (*)')
        if path:
            self.open_source(Path(path))

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Open an extracted LivCatalog or LivGroups folder', str(self.cfg.data_home()))
        if path:
            self.open_source(Path(path))

    def open_source(self, path):
        if self.busy():
            return
        if self.window.running():
            self._error('Close the game before opening a catalog, then try again.')
            return
        self._start(lambda task: read_previews(path, task), self._loaded, 'Reading original files…')

    def _loaded(self, result):
        self.catalog, previews, self.preview_errors = result
        self.exported = None
        self.model.replace(self.catalog, previews)
        self.source.setText(self.catalog.source.name)
        self.source.setToolTip(str(self.catalog.source))
        self.search.clear()
        self.kind.setCurrentIndex(0)
        self.content.setCurrentWidget(self.splitter)
        self.filter_changed()
        self.message.setText('Original files are ready to preserve.' if self.catalog.items else 'No designs or vinyl groups were found. Choose a LivCatalog or LivGroups save.')

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().strip().casefold()
        self.proxy.kind = self.kind.currentData()
        self.proxy.invalidate()
        count = self.proxy.rowCount()
        total = self.model.rowCount()
        self.count.setText(f'{count} of {total} items' if self.catalog else '')
        self.grid.setCurrentIndex(self.proxy.index(0, 0))
        self.selection_changed(self.grid.currentIndex())
        self.grid.setToolTip('No matches. Try another search or choose All content.' if total and not count else '')

    def selection_changed(self, index, *_):
        item = index.data(CatalogModel.ItemRole) if index.isValid() else None
        self.preview_pix = QPixmap()
        for value in self.fields.values():
            value.clear()
        self.preview.clear()
        self.preview_note.clear()
        self.title.setText(item.title if item else 'No matching items')
        self._controls()
        if not item:
            self.preview.setText('Try another search or choose All content.')
            return
        raw = self.model.previews.get(item.path)
        if raw:
            self.preview_pix.loadFromData(raw, 'PNG')
            self._fit_preview()
        else:
            self.preview.setText('Preview unavailable')
        self.preview.setAccessibleName(f'Preview of {item.title}')
        values = (self.catalog.game, 'Design' if item.kind == 'livery' else 'Vinyl group',
                  ', '.join(item.creators) or 'Not recorded',
                  str(item.model_id) if item.model_id is not None else 'Not recorded',
                  str(item.declared_layers) if item.declared_layers is not None else 'Not recorded',
                  'Record preserved' if item.storefront else 'Not recorded')
        for field, value in zip(self.fields.values(), values):
            field.setText(value)
        self.preview_note.setText(self.preview_errors.get(item.path, ''))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_preview()

    def _fit_preview(self):
        if not self.preview_pix.isNull():
            self.preview.setPixmap(self.preview_pix.scaled(max(1, self.preview.width()), 160,
                                                          Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def choose_export(self):
        if self.busy() or not self.catalog or not self.catalog.items:
            return
        parent = QFileDialog.getExistingDirectory(self, 'Choose a location for the archival copy',
                    QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
        if parent:
            name = 'Forza-catalog-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            self.export_to(Path(parent)/name)

    def export_to(self, destination):
        if self.busy() or not self.catalog or not self.catalog.items:
            return
        catalog = self.catalog
        def work(task):
            return ugc.export_catalog(catalog, destination, task.isInterruptionRequested,
                lambda done, total: task.status.emit(f'Exporting originals… {done} of {total}'))
        self._start(work, self._exported, 'Exporting original files and previews…')

    def _exported(self, manifest):
        self.exported = manifest.parent
        self.open_export.setText('Open export folder')
        self.message.setText(f'Archival copy saved to {self.exported}')

    def choose_import(self):
        from .. import catalog_import
        if self.busy():
            return
        if self.window.running() or self.window.setup.busy():
            self._error('Close the game and finish Setup before importing a design.')
            return
        item = self.grid.currentIndex().data(CatalogModel.ItemRole)
        if item is None:
            return
        try:
            self.window.settings.flush()
            target = catalog_import.target_for(self.cfg)
        except (ValueError, OSError) as exc:
            self._error(str(exc))
            return
        text = (f'Import “{item.title}” by {", ".join(item.creators) or "Unknown creator"}?\n\n'
                f'Destination: {target.folder}\nOffline profile: {target.identity.hex().upper()}\n\n'
                'The existing design catalog will be backed up first. Creator attribution and locks '
                'are preserved. Your career and garage will not be edited. Apply the design in-game.')
        box = QMessageBox(QMessageBox.Question, 'Import design', text,
                          QMessageBox.Cancel | QMessageBox.Yes, self)
        box.setTextFormat(Qt.PlainText)
        box.setDefaultButton(QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText('Back up and import')
        if box.exec() == QMessageBox.Yes:
            self.import_to(target, item.path, self.cfg.game_files()/'default.xex')

    def import_to(self, target, item_path, executable):
        from .. import catalog_import
        if self.busy() or self.window.running() or self.window.setup.busy():
            self._error('Close the game and finish Setup before importing a design.')
            return
        catalog = self.catalog
        def work(task):
            try:
                return catalog_import.import_design(catalog, item_path, target, executable,
                    task.isInterruptionRequested, task.status.emit)
            except (ValueError, OSError) as exc:
                raise ugc.CatalogError(str(exc)) from exc
        self._start(work, self._imported, 'Authenticating the design and destination…')

    def _imported(self, backup):
        self.exported = backup
        self.open_export.setText('Open catalog backup')
        self.message.setText(f'Design imported. Apply it to the matching car in-game. Catalog backup: {backup}')

    def show_export(self):
        if self.exported and not browser.open_url(QUrl.fromLocalFile(str(self.exported))):
            self._error(f'Could not open the folder. Open it manually: {self.exported}')

    def _error(self, message):
        self.message.setText(message)
        self.message.setStyleSheet(f'color: {theme.ERR};')

    def _start(self, fn, callback, message):
        self.message.setStyleSheet('')
        self.message.setText(message)
        self.task = CatalogTask(fn, self)
        self.task.result.connect(callback)
        self.task.failed.connect(self._error)
        self.task.status.connect(self.message.setText)
        self.task.finished.connect(self._finished)
        self._controls()
        self.task.start()

    def cancel_task(self):
        if self.task:
            self.task.requestInterruption()
            self.cancel.setEnabled(False)
            self.message.setText('Stopping the catalog operation…')

    def _finished(self):
        task, self.task = self.task, None
        task.deleteLater()
        self._controls()
        if self.closing:
            self.closing = False
            QTimer.singleShot(0, self.window.close)
