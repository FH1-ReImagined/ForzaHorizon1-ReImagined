"""Catalog browser behavior with synthetic saves and real Qt workers, offline."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ugc import thumbnail, package
from forza_launcher import ugc
try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PySide6 is not installed')
class CatalogUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_patch = patch('forza_launcher.paths.config_dir', return_value=self.root)
        self.config_patch.start()
        cfg = Config(self.root/'launcher.json')
        cfg.loaded = True
        cfg.gpu['checked'] = True
        cfg.d['check_updates'] = False
        cfg.advanced['data_root'] = str(self.root/'data')
        self.win = MainWindow(cfg)
        self.page = self.win.catalogs
        self.source = self.root/'LivCatalog'
        item = self.source/'1293'/'Fixture design'
        item.mkdir(parents=True)
        (item/'0002_layers_1293.liv').write_bytes(b'opaque original')
        (item/'Original Creator.owner').touch()
        (item/'thumb.xdc').write_bytes(thumbnail()[0])
        group = self.source/'Vinyl fixture'
        group.mkdir()
        (group/'0004_layers.lvg').write_bytes(b'opaque vinyl')

    def tearDown(self):
        if self.page.busy():
            self.page.cancel_task()
            self.wait_done()
        # Showing the photo gallery starts its folder scan (a QThread child of the page), and
        # the window defers its close until the scan ends. Deleting the window before that
        # destroys a running QThread, which aborts the process (seen on the Windows runner).
        self.win.gallery.shutdown()
        deadline = time.monotonic()+10
        while self.win.gallery.task is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.win.gallery.task, 'Gallery scan did not finish')
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.config_patch.stop()
        self.temp.cleanup()

    def wait_done(self):
        deadline = time.monotonic()+5
        while self.page.busy() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertFalse(self.page.busy(), 'Catalog worker did not finish')

    def load(self):
        self.page.open_source(self.source)
        self.assertFalse(self.page.export.isEnabled())
        self.wait_done()
        self.assertEqual(self.page.model.rowCount(), 2)

    def test_navigation_keeps_gallery_and_about_routes(self):
        from forza_launcher.ui.catalogs import CATALOG_PAGE_ID
        # Library in the sidebar, then its Designs & vinyls tab.
        self.win.nav_button(CATALOG_PAGE_ID).click()
        self.assertIs(self.win.stack.currentWidget(), self.win.gallery)
        tabs = self.win.subnav
        tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.tabData(i) == CATALOG_PAGE_ID))
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.assertTrue(self.win.nav_button(CATALOG_PAGE_ID).isChecked())
        self.assertEqual(self.win.section_title.text(), "Library")
        self.win.show_page(3)
        self.assertIs(self.win.stack.currentWidget(), self.win.about)
        self.assertEqual(self.win.stack.currentIndex(), 3)
        self.win.show_page(4)
        self.assertIs(self.win.stack.currentWidget(), self.win.gallery)

    def test_load_search_filter_and_missing_preview(self):
        self.load()
        self.page.search.setText('ORIGINAL CREATOR')
        self.assertEqual(self.page.proxy.rowCount(), 1)
        self.assertEqual(self.page.fields['Car ID'].text(), '1293')
        self.assertFalse(self.page.preview.pixmap().isNull())
        self.assertEqual(self.page.fields['Creator'].text(), 'Original Creator')
        self.page.kind.setCurrentIndex(2)
        self.assertEqual(self.page.proxy.rowCount(), 0)
        self.assertEqual(self.page.title.text(), 'No matching items')
        self.page.search.clear()
        self.assertEqual(self.page.proxy.rowCount(), 1)
        self.assertIn('no saved preview', self.page.preview_note.text())
        self.assertTrue(self.page.export.isEnabled())  # export always preserves the whole catalog

    def test_export_snapshot_preserves_source_and_opens_folder(self):
        from forza_launcher.browser import QDesktopServices
        before = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        self.load()
        # Source edits after inspection cannot silently change the preserved snapshot.
        payload = next(self.source.rglob('*.liv'))
        payload.write_bytes(b'later source revision')
        destination = self.root/'export'
        self.page.export_to(destination)
        self.wait_done()
        manifest = json.loads((destination/'manifest.json').read_text())
        self.assertFalse(manifest['installable'])
        self.assertEqual(len(manifest['items']), 2)
        self.assertEqual(next(destination.rglob('*.liv')).read_bytes(), before[payload])
        self.assertEqual(payload.read_bytes(), b'later source revision')
        for path, raw in before.items():
            if path != payload:
                self.assertEqual(path.read_bytes(), raw)
        with patch.object(QDesktopServices, 'openUrl', return_value=True) as opened:
            self.page.open_export.click()
        self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), destination)
        with patch.object(QDesktopServices, 'openUrl', return_value=False):
            self.page.open_export.click()
        self.assertIn(str(destination), self.page.message.text())

    def test_bad_preview_and_failed_open_retain_originals(self):
        next(self.source.rglob('thumb.xdc')).write_bytes(b'invalid preview')
        self.load()
        self.page.search.setText('1293')
        self.assertIn('Preview unavailable', self.page.preview_note.text())
        old = self.page.catalog
        self.page.open_source(self.root/'missing')
        self.wait_done()
        self.assertIs(self.page.catalog, old)
        self.assertTrue(self.page.export.isEnabled())
        self.page.export_to(self.source)  # never overwrites even an existing source directory
        self.wait_done()
        self.assertTrue(next(self.source.rglob('*.liv')).exists())
        self.assertIsNone(self.page.exported)

    def test_package_metadata_and_plain_text_titles(self):
        path = self.root/'save'
        path.write_bytes(package())
        self.page.open_source(path)
        self.wait_done()
        self.assertEqual(self.page.fields['Game'].text(), 'Forza Horizon')
        catalog = ugc.Catalog(path, None, None, (
            ugc.Item('safe', '<b>Literal name</b>', 'livery', None, None, (), False, {}),))
        self.page._loaded((catalog, {}, {}))
        self.assertEqual(self.page.title.textFormat(), Qt.PlainText)
        self.assertEqual(self.page.title.text(), '<b>Literal name</b>')

    def test_game_lock_and_empty_catalog(self):
        with patch.object(self.win, 'running', return_value=True):
            self.page.open_source(self.source)
        self.assertFalse(self.page.busy())
        self.assertIn('Close the game', self.page.message.text())
        empty = self.root/'empty'
        empty.mkdir()
        self.page.open_source(empty)
        self.wait_done()
        self.assertFalse(self.page.export.isEnabled())
        self.assertIn('No designs', self.page.message.text())

    def test_close_cancels_and_waits_for_worker(self):
        self.win.show()
        started = threading.Event()
        def work(task):
            started.set()
            while not task.isInterruptionRequested():
                time.sleep(.005)
            raise ugc.CatalogError('Cancelled')
        self.page._start(work, lambda result: None, 'Working')
        self.assertTrue(started.wait(1))
        self.win.close()
        self.assertTrue(self.win.isVisible())
        self.wait_done()
        self.app.processEvents()
        self.assertFalse(self.win.isVisible())

    def test_keyboard_search_and_selection_at_minimum_window_size(self):
        from PySide6.QtTest import QTest
        from forza_launcher.ui.catalogs import CATALOG_PAGE_ID
        self.load()
        self.win.resize(900, 620)
        self.win.show_page(CATALOG_PAGE_ID)
        self.win.show()
        self.app.processEvents()
        self.page.search.setFocus()
        QTest.keyClicks(self.page.search, 'original creator')
        self.assertEqual(self.page.proxy.rowCount(), 1)
        self.page.search.clear()
        self.page.grid.setFocus()
        QTest.keyClick(self.page.grid, Qt.Key_End)
        self.assertEqual(self.page.grid.currentIndex().row(), 1)
        self.assertEqual(self.page.fields['Type'].text(), 'Vinyl group')
        self.assertEqual(self.win.width(), 900)
        self.assertEqual(self.win.height(), 620)
        self.assertGreaterEqual(self.page.splitter.sizes()[1], 240)

    def test_declining_other_task_quit_does_not_leave_close_pending(self):
        self.win.show()
        def work(task):
            while not task.isInterruptionRequested():
                time.sleep(.005)
        with patch.object(self.win.setup, 'busy', return_value=True), \
                patch.object(QMessageBox, 'question', return_value=QMessageBox.No) as question:
            self.page._start(work, lambda result: None, 'Working')
            self.win.close()
            self.wait_done()
            self.assertTrue(self.win.isVisible())

            self.assertFalse(self.page.closing)
            self.page._start(lambda task: None, lambda result: None, 'Working')
            self.wait_done()
            self.assertEqual(question.call_count, 1)
            self.assertTrue(self.win.isVisible())

    def test_import_confirmation_and_worker_use_the_selected_snapshot(self):
        from dataclasses import replace
        from forza_launcher import catalog_import as ci
        self.load()
        self.assertFalse(self.page.import_button.isEnabled())  # extracted folders lack verified identity
        self.page.catalog = replace(self.page.catalog, title_id='4D5309C9', profile_id=bytes(8), index=b'index')
        self.page.search.setText('1293')
        self.assertTrue(self.page.import_button.isEnabled())
        target = ci.Target(self.root,self.root/'profile',bytes.fromhex('B13EBABEBABEBABE'))
        backup=self.root/'backup'
        with patch.object(ci,'target_for',return_value=target), \
                patch.object(ci,'import_design',return_value=backup) as imported, \
                patch.object(QMessageBox,'exec',return_value=QMessageBox.Yes):
            self.page.import_button.click()
            self.wait_done()
        self.assertIs(imported.call_args.args[0],self.page.catalog)
        self.assertIn('Design imported',self.page.message.text())
        self.assertEqual(self.page.exported,backup)
        self.assertEqual(self.page.open_export.text(),'Open catalog backup')
        with patch.object(ci,'target_for',return_value=target), \
                patch.object(ci,'import_design') as imported, \
                patch.object(QMessageBox,'exec',return_value=QMessageBox.Cancel):
            self.page.import_button.click()
        imported.assert_not_called()

    def test_catalog_task_blocks_play_and_running_game_blocks_import(self):
        self.load()
        with patch.object(self.win,'running',return_value=True), \
                patch('forza_launcher.catalog_import.import_design') as imported:
            self.page.import_to(None,'unused',None)
        imported.assert_not_called()
        self.assertIn('Close the game',self.page.message.text())
        self.page._start(lambda task: None,lambda result:None,'Working')
        with patch('forza_launcher.game.find_game') as find:
            self.win.start_game()
        find.assert_not_called()
        self.wait_done()


if __name__ == '__main__':
    unittest.main()
