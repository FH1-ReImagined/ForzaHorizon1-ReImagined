"""The launcher's layout: sidebar sections and their page tabs, Settings categories (every setting
still has its row and its search result), folded cards and "More options", the bundled fonts and
logo in the packages, and the palette's contrast (WCAG AA)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from forza_launcher import schema  # noqa: E402


def _luminance(color):
    c = [int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class PaletteTests(unittest.TestCase):
    def test_text_passes_wcag_aa_on_every_surface(self):
        from forza_launcher.ui import theme
        for surface in (theme.BG, theme.BG_DEEP, theme.PANEL, theme.PANEL2):
            for name in ("TEXT", "SOFT", "MUTED", "CREAM", "ORANGE", "OK", "ERR"):
                with self.subTest(text=name, surface=surface):
                    self.assertGreaterEqual(contrast(getattr(theme, name), surface), 4.5)
        self.assertGreaterEqual(contrast(theme.INK, theme.GRAD_START), 4.5)   # Play's label
        self.assertGreaterEqual(contrast(theme.INK, theme.RUST), 4.5)
        self.assertGreaterEqual(contrast(theme.INK, theme.SAND), 4.5)         # the selected tab
        self.assertGreaterEqual(contrast(theme.DISABLED, theme.DISABLED_BG), 4.5)

    def test_control_edges_keep_three_to_one(self):
        from forza_launcher.ui import theme
        for surface in (theme.BG, theme.BG_DEEP, theme.PANEL):
            self.assertGreaterEqual(contrast(theme.CONTROL, surface), 3.0)
        self.assertGreaterEqual(contrast(theme.ORANGE, theme.PANEL2), 3.0)   # focus edge

    def test_palette_is_the_websites(self):
        from forza_launcher.ui import theme
        for name, value in (("BG", "#1c140f"), ("PANEL", "#2a1f17"), ("TEXT", "#f4ebdd"),
                            ("MUTED", "#c4ae92"), ("CREAM", "#f3dfbf"), ("SAND", "#ead9bd"),
                            ("ORANGE", "#e8964f"), ("RUST", "#d0703f")):
            self.assertEqual(getattr(theme, name), value)


class PackagingTests(unittest.TestCase):
    FONTS = ("Bitter-ExtraBold.ttf", "SairaSemiCondensed-SemiBold.ttf", "SairaSemiCondensed-Bold.ttf",
             "OFL-Bitter.txt", "OFL-SairaSemiCondensed.txt")

    def test_fonts_and_licences_are_in_the_launcher(self):
        folder = HERE / "forza_launcher" / "ui" / "fonts"
        for name in self.FONTS:
            self.assertTrue((folder / name).is_file(), name)
        for name in ("OFL-Bitter.txt", "OFL-SairaSemiCondensed.txt"):
            self.assertIn("SIL Open Font License", (folder / name).read_text(encoding="utf-8"))

    def test_pyinstaller_and_flatpak_take_the_fonts_and_the_logo_mark(self):
        spec = (HERE / "forza-launcher.spec").read_text(encoding="utf-8")
        self.assertIn('"forza_launcher" / "ui" / "fonts"), "forza_launcher/ui/fonts"', spec)
        self.assertIn('images / "logo-mark.png"', spec)
        stage = (REPO / "flatpak" / "stage.py").read_text(encoding="utf-8")
        self.assertRegex(stage, r'ASSETS = \([^)]*"logo-mark.png"')
        self.assertIn('copytree(REPO / "launcher" / "forza_launcher"', stage)   # fonts come with the package
        self.assertTrue((REPO / "docs" / "images" / "logo-mark.png").is_file())

    def test_notice_lists_the_fonts(self):
        notice = (REPO / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("launcher/forza_launcher/ui/fonts/", notice)
        self.assertIn("SIL Open Font License", notice)


class CategoryTests(unittest.TestCase):
    def test_every_section_is_placed_on_linux_and_windows(self):
        # Windows has its own settings (Graphics API, NVIDIA DLSS): the schema is made per platform.
        import subprocess
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); import forza_launcher.paths as p; "
            "p.IS_WINDOWS = sys.argv[2] == 'win'; from forza_launcher import schema; "
            "from forza_launcher.ui import settings_page as sp; "
            "placed = {sec for secs in sp.TAB_SECTIONS.values() for sec in secs}; "
            "print('\\n'.join(f'{s.group}|{s.section}|{sp.tab_of(s)}' for s in schema.S if s.applies() "
            "and (s.group, s.section) not in placed))")
        for platform in ("linux", "win"):
            with self.subTest(platform=platform):
                out = subprocess.run([sys.executable, "-c", script, str(HERE), platform], capture_output=True,
                                     text=True, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, check=True)
                left = [line.split("|") for line in out.stdout.splitlines() if line]
                # Display's and Controls' sections are all placed by hand; others stay in their group.
                self.assertEqual([x for x in left if x[0] in ("Display", "Controls")], [])
                for group, _section, tab in left:
                    self.assertEqual(group, tab)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch("forza_launcher.paths.config_dir", return_value=root),
                        patch("forza_launcher.datafolder.game_default_data_home", return_value=root / "legacy")]
        for p in self.patches:
            p.start()
        cfg = Config(root / "launcher.json")
        cfg.advanced["data_root"] = str(root / "data")
        cfg.gpu["checked"] = True
        cfg.loaded = True
        cfg.d["check_updates"] = False
        self.win = MainWindow(cfg)
        self.win.resize(1040, 760)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_sections_hold_every_page_once(self):
        from forza_launcher.ui.window import SECTIONS
        ids = [pid for _name, pages, _small in SECTIONS for _title, pid in pages]
        self.assertEqual(sorted(ids), sorted(self.win._pages))
        self.assertEqual(len(self.win.navs.buttons()), len(SECTIONS))
        self.assertLessEqual(len([s for s in SECTIONS if not s[2]]), 7)

    def test_every_page_shows_its_section_title_button_and_tab(self):
        from forza_launcher.ui.window import SECTIONS
        for name, pages, _small in SECTIONS:
            for title, pid in pages:
                with self.subTest(page=title):
                    self.win.show_page(pid)
                    self.assertIs(self.win.stack.currentWidget(), self.win._pages[pid])
                    self.assertTrue(self.win.nav_button(pid).isChecked())
                    self.assertEqual(self.win.nav_button(pid).text(), name)
                    if pid != 0:
                        self.assertEqual(self.win.section_title.text(), name)
                        self.assertTrue(self.win.section_title.isVisible())
                    self.assertEqual(self.win.subnav.isVisible(), len(pages) > 1)
                    if len(pages) > 1:
                        self.assertEqual(self.win.subnav.tabData(self.win.subnav.currentIndex()), pid)

    def test_section_button_returns_to_the_page_shown_last(self):
        from forza_launcher.ui.window import RADIO_PAGE_ID
        self.win.show_page(RADIO_PAGE_ID)
        self.win.show_page(0)
        self.win.nav_button(RADIO_PAGE_ID).click()
        self.assertIs(self.win.stack.currentWidget(), self.win.radio)

    def test_page_tabs_switch_pages_and_radio_lock_keeps_only_radio(self):
        from forza_launcher.ui.window import MODS_PAGE_ID, RADIO_PAGE_ID, TEXTURE_PACKS_PAGE_ID
        self.win.show_page(MODS_PAGE_ID)
        tabs = self.win.subnav
        tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.tabData(i) == TEXTURE_PACKS_PAGE_ID))
        self.assertIs(self.win.stack.currentWidget(), self.win.texture_packs)
        self.win.show_page(RADIO_PAGE_ID)
        self.win.lock_radio_navigation(True)
        try:
            for i in range(tabs.count()):
                self.assertEqual(tabs.isTabEnabled(i), tabs.tabData(i) == RADIO_PAGE_ID)
            for button in self.win.navs.buttons():
                self.assertEqual(button.isEnabled(), button is self.win.nav_button(RADIO_PAGE_ID))
        finally:
            self.win.lock_radio_navigation(False)
        self.assertTrue(all(tabs.isTabEnabled(i) for i in range(tabs.count())))

    def test_page_titles_give_way_to_the_section_header(self):
        from PySide6.QtWidgets import QLabel
        for page in self.win._pages.values():
            for title in page.findChildren(QLabel, "pageTitle"):
                self.assertTrue(title.isHidden(), title.text())

    def test_every_setting_has_a_row_a_category_and_a_search_result(self):
        from forza_launcher.ui.settings_page import TABS, tab_of
        page = self.win.settings
        self.assertEqual([page.tabs.tabText(i) for i in range(page.tabs.count())], TABS)
        found = {t.widget: t for t in page.search.targets}
        for s in schema.S:
            if not s.applies():
                continue
            with self.subTest(key=s.key):
                self.assertIn(s.key, page.rows)
                row = page.rows[s.key]
                self.assertIn(tab_of(s), TABS)
                scroll = page.stack.widget(page.tab_index(tab_of(s)))
                self.assertTrue(scroll.widget().isAncestorOf(row))
                self.assertEqual(found[row].group, tab_of(s))

    def test_display_settings_split_into_graphics_and_display(self):
        from forza_launcher.ui.settings_page import tab_of
        for key in ("resolution_scale", "world_shadow_detail", "present_hdr", "texture_replace", "motion_blur"):
            self.assertEqual(tab_of(schema.BY_KEY[key]), "Graphics")
        for key in ("frame_rate", "fullscreen", "forza_cockpit_fov", "forza_ultrawide"):
            self.assertEqual(tab_of(schema.BY_KEY[key]), "Display")
        self.assertEqual(tab_of(schema.BY_KEY["forza_native_pause"]), "Gameplay")
        self.assertEqual(schema.BY_KEY["forza_native_pause"].group, "Advanced")   # its key and group stay

    def test_search_opens_a_folded_card_and_more_options(self):
        page = self.win.settings
        self.win.show_page(2)
        for key in ("wheel_axes", "audio_maxqframes", "keybind_a"):
            with self.subTest(key=key):
                row = page.rows[key]
                self.assertFalse(row.isVisible())
                target = next(t for t in page.search.targets if t.widget is row)
                page._show_search_result(target)
                self.app.processEvents()
                self.app.processEvents()
                self.assertTrue(row.isVisible())

    def test_folded_cards_open_when_in_use(self):
        self.win.cfg.settings["wheel_mode"] = "on"
        self.win.settings.rebuild()
        self.win.show_page(2)
        self.win.settings.show_tab("Controls")
        self.app.processEvents()
        self.assertTrue(self.win.settings.rows["wheel_rotation"].isVisible())
        self.assertFalse(self.win.settings.rows["keybind_a"].isVisible())

    def test_bundled_fonts_register(self):
        from forza_launcher.ui import theme
        headline, ui = theme.load_fonts()
        self.assertTrue(headline.startswith("Bitter"), headline)
        self.assertTrue(re.match(r"Saira ?Semi ?Condensed", ui), ui)
        self.assertIn(f'"{headline}"', theme.stylesheet())


if __name__ == "__main__":
    unittest.main()
