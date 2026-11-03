"""Settings search navigates existing controls without changing configuration."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class SettingsSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.settings_page import SettingsPage
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.temp.name) / "launcher.json")
        self.cfg.advanced["data_root"] = self.temp.name
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        self.window = Mock()
        self.window.preview_text.return_value = "game.exe"
        self.page = SettingsPage(self.cfg, self.window)
        self.page.resize(1000, 720)
        self.page.show()
        self.app.processEvents()
        self.search = self.page.search
        # Initial radio selection schedules the page's existing startup save.
        # Measure search separately so slow CI rendering cannot flush that timer mid-test.
        self.page._save_timer.stop()
        self.window.reset_mock()

    def tearDown(self):
        self.page._save_timer.stop()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def query(self, text):
        self.search.edit.setText(text)
        self.app.processEvents()
        return [self.search.results.item(i).data(Qt.UserRole)
                for i in range(self.search.results.count())]

    def test_key_search_jumps_across_categories_without_changing_settings(self):
        from forza_launcher import schema
        from forza_launcher.ui import theme
        self.page.setStyleSheet(theme.QSS)
        self.page.resize(680, 720)
        before = copy.deepcopy(self.cfg.d)
        target = self.query("mnk_sensitivity")[0]
        row = self.page.rows["mnk_sensitivity"]
        self.assertIs(target.widget, row)
        self.assertFalse(row.control.isEnabled())
        QTest.keyClick(self.search.edit, Qt.Key_Return)
        self.assertEqual(self.page.tabs.currentIndex(), schema.GROUPS.index("Controls"))
        self.assertTrue(self.search.results.isHidden())
        self.app.processEvents()
        scroll = self.page.stack.currentWidget()
        right = row.control.mapTo(scroll.viewport(), row.control.rect().topRight())
        self.assertLessEqual(right.x(), scroll.viewport().width())
        self.assertEqual(scroll.horizontalScrollBar().value(), 0)
        self.assertEqual(self.cfg.d, before)
        self.assertFalse(row.control.isEnabled())
        self.window.write_toml.assert_not_called()

    def test_native_tuning_search_opens_gameplay_beside_rubber_banding(self):
        from forza_launcher import schema
        before = copy.deepcopy(self.cfg.d)
        target = self.query("Native car tuning")[0]
        self.assertEqual(target.group, "Gameplay")
        self.assertEqual(schema.BY_KEY["ai_rubber_banding"].group, target.group)
        self.assertIs(target.widget, self.page.rows["forza_car_tuning"])
        QTest.keyClick(self.search.edit, Qt.Key_Return)
        self.assertEqual(self.page.tabs.tabText(self.page.tabs.currentIndex()), "Gameplay")
        self.assertEqual(self.cfg.d, before)
        self.window.write_toml.assert_not_called()

    def test_metadata_search_includes_help_choices_and_all_query_words(self):
        self.assertTrue(self.query("default physics"))
        self.assertIn("Frame rate", [t.label for t in self.query("FRAME_RATE")])
        self.assertTrue(self.query("borderless fullscreen"))
        self.assertFalse(self.query("borderless impossibleword"))
        self.assertEqual(self.search.count.text(), "No settings found")
        self.assertTrue(self.search.results.isHidden())

    def test_launcher_fields_are_searchable_without_indexing_private_values(self):
        self.cfg.online["custom_server"] = "private-host.example"
        self.assertIs(self.query("custom_server")[0].focus, self.page.custom)
        self.assertIs(self.query("Xenia content folder")[0].focus, self.page.xcontent)
        self.assertIs(self.query("extra_env")[0].focus, self.page.envs)
        self.assertTrue(self.query("controller routing"))
        self.assertFalse(self.query("private-host.example"))

    def test_keyboard_select_focus_and_escape(self):
        self.query("frame_rate")
        QTest.keyClick(self.search.edit, Qt.Key_Down)
        self.assertTrue(self.search.results.hasFocus())
        QTest.keyClick(self.search.results, Qt.Key_Return)
        self.app.processEvents()
        self.assertTrue(self.page.rows["frame_rate"].control.hasFocus())
        QTest.keyClick(self.page.rows["frame_rate"].control, Qt.Key_F, Qt.ControlModifier)
        self.app.processEvents()
        self.assertTrue(self.search.edit.hasFocus())
        self.assertFalse(self.search.results.isHidden())
        QTest.keyClick(self.search.edit, Qt.Key_Escape)
        self.assertEqual(self.search.edit.text(), "")
        self.assertTrue(self.search.results.isHidden())
        self.assertTrue(self.search.count.isHidden())

    def test_hidden_gpu_rows_excluded_and_rebuild_replaces_targets(self):
        self.assertEqual(bool(self.query("fh1_output_resolution")),
                         "fh1_output_resolution" in self.page.rows)
        old = self.query("mnk_sensitivity")[0].widget
        self.cfg.gpu["vendor"] = "amd"
        self.page.rebuild()
        self.app.processEvents()
        fresh = self.query("mnk_sensitivity")[0].widget
        self.assertIsNot(fresh, old)
        self.assertIs(fresh, self.page.rows["mnk_sensitivity"])
        self.assertFalse(self.query("fh1_output_resolution"))
        self.query("")
        self.assertEqual(self.search.results.count(), 0)

    def test_deep_result_scrolls_into_view_after_category_layout(self):
        from forza_launcher import schema
        self.query("extra_env")
        target = self.search.results.item(0).data(Qt.UserRole)
        QTest.keyClick(self.search.edit, Qt.Key_Return)
        self.app.processEvents()
        self.app.processEvents()
        scroll = self.page.stack.widget(schema.GROUPS.index("Advanced"))
        top = target.widget.mapTo(scroll.viewport(), target.widget.rect().topLeft())
        self.assertGreaterEqual(top.y(), 0)
        self.assertLess(top.y(), scroll.viewport().height() - target.widget.height())
        self.assertLess(self.search.height(), 90)

    def test_results_overlay_tabs_without_moving_content_and_dismiss_outside(self):
        tabs = self.page.tabs.geometry()
        content = self.page.stack.geometry()
        self.query("resolution")
        self.assertEqual(self.page.tabs.geometry(), tabs)
        self.assertEqual(self.page.stack.geometry(), content)
        self.assertTrue(self.search.results.geometry().intersects(tabs))
        point = self.search.results.geometry().center()
        front = self.page.childAt(point)
        self.assertTrue(front is self.search.results or self.search.results.isAncestorOf(front))
        self.page.resize(850, 650)
        self.app.processEvents()
        self.assertEqual(self.search.results.width(), self.search.width())
        QTest.mouseClick(self.search.results.viewport(), Qt.LeftButton,
                         pos=self.search.results.visualItemRect(self.search.results.item(0)).center())
        self.app.processEvents()
        self.assertTrue(self.search.results.isHidden())
        self.query("resolution")
        QTest.mouseClick(self.page, Qt.LeftButton, pos=QPoint(840, 640))
        self.assertTrue(self.search.results.isHidden())


if __name__ == "__main__":
    unittest.main()
