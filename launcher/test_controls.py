"""The controller guide loads its real artwork and translates the game's prompts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PySide6 is not installed')
class ControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.ui.controls_page import ControlsPage
        self.page = ControlsPage()
        self.page.resize(640, 700)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_every_controller_loads_and_its_targets_align_with_visible_artwork(self):
        for index in range(self.page.family.count()):
            self.page.family.setCurrentIndex(index)
            self.app.processEvents()
            diagram = self.page.diagram
            image, regions = diagram.artwork[diagram.family]
            self.assertFalse(image.isNull())
            image = image.toImage()
            for key, region in regions.items():
                center = region.center()
                self.assertGreater(image.pixelColor(int(center.x()), int(center.y())).alpha(), 0, key)
                self.assertTrue(diagram.rect().contains(diagram.buttons[key].geometry()), key)

    def test_clicking_a_playstation_button_preserves_the_equivalent_xbox_prompt(self):
        self.page.family.setCurrentIndex(1)
        self.page.diagram.buttons['b'].click()
        self.assertEqual(self.page.action.text(), 'Back / shift up')
        self.assertIn('Circle', self.page.prompt.text())
        self.assertIn('Xbox prompt: B', self.page.prompt.text())
        self.page.family.setCurrentIndex(0)
        self.assertEqual(self.page.action.text(), 'Back / shift up')
        self.assertTrue(self.page.diagram.buttons['b'].isChecked())
        self.assertIn('Xbox prompt: B', self.page.prompt.text())

    def test_keyboard_activation_and_playstation_menu_button_names(self):
        self.page.family.setCurrentIndex(1)
        self.page.diagram.buttons['back'].setFocus()
        QTest.keyClick(self.page.diagram.buttons['back'], Qt.Key_Space)
        self.assertEqual(self.page.action.text(), 'Open map')
        self.assertIn('Create', self.page.prompt.text())
        self.page.family.setCurrentIndex(2)
        self.assertIn('Share', self.page.prompt.text())
        self.assertIn('Xbox prompt: Back', self.page.prompt.text())
        self.assertIn('Square', self.page.diagram.buttons['x'].accessibleName())

    def test_control_settings_link_opens_the_existing_controls_category(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = Config(root / 'launcher.json')
            cfg.loaded = True
            cfg.gpu['checked'] = True
            cfg.advanced['data_root'] = str(root)
            cfg.d['check_updates'] = False
            with patch('forza_launcher.paths.config_dir', return_value=root), \
                    patch('forza_launcher.game.find_game', return_value=(None, [])):
                window = MainWindow(cfg)
                try:
                    window.show_page(5)
                    button = next(b for b in window.controls.findChildren(QPushButton)
                                  if b.text() == 'Control settings')
                    button.click()
                    self.assertIs(window.stack.currentWidget(), window.settings)
                    self.assertEqual(window.settings.tabs.currentIndex(), window.settings.tab_index('Controls'))
                    for old_id, page in ((0, window.play), (1, window.setup), (3, window.about), (4, window.gallery)):
                        self.assertIs(window.stack.widget(old_id), page)
                finally:
                    window.close()
                    while window.setup._probes:
                        self.app.processEvents()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
