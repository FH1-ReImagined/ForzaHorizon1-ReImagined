"""Colors, fonts and the Qt style sheet: the website's look (forza.dyotson.dev, style.css), Colorado
at golden hour. A warm umber window, dusty orange and rust accents, sand and cream text.

Type: Bitter ExtraBold for headings and Saira Semi Condensed (SemiBold, Bold) for labels, buttons and
the navigation, both under the SIL Open Font License (ui/fonts/, with their OFL texts). They come from
Google Fonts' repository (github.com/google/fonts, ofl/bitter and ofl/sairasemicondensed); Bitter is
the weight-800 instance of its variable font (fontTools varLib.instancer). Body text stays on the
system's UI font. load_fonts() registers them once a QApplication exists, then stylesheet() names
the families Qt registered.

Every text color here passes WCAG AA (4.5:1) on the surfaces it is drawn on, and the borders of
inputs, switches and check boxes keep 3:1 (CONTROL)."""
from pathlib import Path

# The website's palette (style.css :root)
BG = "#1c140f"          # --bg, the page
BG_DEEP = "#140e0a"     # --bg-deep, the sidebar and inputs
PANEL = "#2a1f17"       # --surface, cards
PANEL2 = "#36281d"      # --surface-2, buttons, selected rows
BORDER = "#4d3a2a"      # --border, card edges and separators
TEXT = "#f4ebdd"        # --text
SOFT = "#e8dac5"        # --soft, leads
MUTED = "#c4ae92"       # --muted, help text (7.5:1 on cards)
CREAM = "#f3dfbf"       # --cream, titles
SAND = "#ead9bd"        # --sand
ORANGE = "#e8964f"      # --orange, dusty orange: accents, focus
RUST = "#d0703f"        # --pink (rust), the gradient's end
PINK = RUST             # the old name, still used by a few pages
INK = "#2a1a10"         # --ink, text on the orange gradient
GRAD_START = "#eba05a"  # --grad: linear-gradient(100deg, #eba05a, var(--pink))
NOTICE = "#faede8"      # .notice text
LINE = "#3e3228"        # --line (rgba(243, 223, 191, .1)) on a card, opaque: rounded corners stay even
# Launcher-only: the states the website does not have
CONTROL = "#8a6f55"     # input, switch and check box edges (3.4:1 on cards)
OK = "#9cc48a"          # sage: ready (8.2:1 on cards)
ERR = "#f08a73"         # light rust-red: a problem (6.6:1 on cards)
DISABLED = "#a08c76"    # disabled text
DISABLED_BG = "#241b14"

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FILES = ("Bitter-ExtraBold.ttf", "SairaSemiCondensed-SemiBold.ttf", "SairaSemiCondensed-Bold.ttf")
HEADLINE = "Bitter"                 # the families Qt registers, set again by load_fonts()
UI = "Saira SemiCondensed"
LOADED = []                         # the FONT_FILES Qt took

ARROW = (Path(__file__).resolve().parent / "down.svg").as_posix()
CHECK = (Path(__file__).resolve().parent / "check.svg").as_posix()


def load_fonts():
    """Register the bundled fonts with Qt (needs a QGuiApplication). Returns (headline, ui) family
    names; a missing or unreadable file keeps the default name, and Qt falls back to the system's."""
    global HEADLINE, UI
    from PySide6.QtGui import QFontDatabase
    for name in FONT_FILES:
        if name in LOADED:
            continue
        font_id = QFontDatabase.addApplicationFont(str(FONT_DIR / name))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if not families:
            continue
        LOADED.append(name)
        if name.startswith("Bitter"):
            HEADLINE = families[0]
        elif name.endswith("SemiBold.ttf"):
            UI = families[0]
    return HEADLINE, UI


def apply_palette(app):
    """Links in rich text (QLabel <a>) follow the palette: the website's orange, not Qt's blue."""
    from PySide6.QtGui import QColor, QPalette
    palette = app.palette()
    palette.setColor(QPalette.Link, QColor(ORANGE))
    palette.setColor(QPalette.LinkVisited, QColor(ORANGE))
    app.setPalette(palette)


def stylesheet(headline=None, ui=None):
    h = f'"{headline or HEADLINE}"'
    u = f'"{ui or UI}"'
    return f"""
* {{ font-size: 14px; }}
QWidget {{ background: transparent; color: {TEXT}; }}
QMainWindow, QDialog, #root {{ background: {BG}; }}
QDialog QLabel {{ color: {TEXT}; }}

/* ---- the sidebar: the logo, the sections, the launcher's version ---- */
#sidebar {{ background: {BG_DEEP}; border-right: 1px solid rgba(243, 223, 191, 0.10); }}
#brand {{ color: {CREAM}; font-family: {u}; font-size: 13px; font-weight: 700; letter-spacing: 1px; }}
#brandSub {{ color: {ORANGE}; font-family: {h}; font-size: 20px; font-weight: 800; }}
#navHeading {{ color: {MUTED}; font-family: {u}; font-size: 12px; font-weight: 700; letter-spacing: 2px;
  padding: 14px 22px 4px 22px; }}
QPushButton#nav {{ text-align: left; padding: 10px 18px 10px 19px; border: none; border-left: 3px solid transparent;
  color: #f0e3d2; font-family: {u}; font-size: 17px; font-weight: 600; letter-spacing: 0.5px;
  background: transparent; border-radius: 0; }}
QPushButton#nav:hover {{ color: {CREAM}; background: rgba(241, 227, 206, 0.06); }}
QPushButton#nav:checked {{ color: {CREAM}; border-left: 3px solid {ORANGE}; background: rgba(232, 150, 79, 0.13); }}
QPushButton#nav:focus {{ color: {CREAM}; background: rgba(232, 150, 79, 0.20); border-left: 3px solid {CREAM}; }}
QPushButton#nav:disabled {{ color: {DISABLED}; }}
QPushButton#navSmall {{ text-align: left; padding: 7px 18px 7px 19px; border: none; border-left: 3px solid transparent;
  color: {MUTED}; font-family: {u}; font-size: 15px; font-weight: 600; background: transparent; border-radius: 0; }}
QPushButton#navSmall:hover {{ color: {CREAM}; background: rgba(241, 227, 206, 0.06); }}
QPushButton#navSmall:checked {{ color: {CREAM}; border-left: 3px solid {ORANGE}; background: rgba(232, 150, 79, 0.13); }}
QPushButton#navSmall:focus {{ color: {CREAM}; background: rgba(232, 150, 79, 0.20); border-left: 3px solid {CREAM}; }}
#version {{ color: {MUTED}; font-size: 12px; }}

/* ---- the section header: its title and its pages ---- */
#sectionHeader {{ border-bottom: 1px solid rgba(243, 223, 191, 0.10); }}
#sectionTitle {{ font-family: {h}; font-size: 30px; font-weight: 800; color: {CREAM}; }}
QTabBar#subnav::tab {{ padding: 8px 2px 9px 2px; margin-right: 22px; color: #f0e3d2; background: transparent;
  font-family: {u}; font-size: 16px; font-weight: 600; letter-spacing: 0.5px;
  border: none; border-bottom: 3px solid transparent; border-radius: 0; }}
QTabBar#subnav::tab:selected {{ color: {CREAM}; border-bottom: 3px solid {ORANGE}; background: transparent; }}
QTabBar#subnav::tab:hover {{ color: {CREAM}; border-bottom: 3px solid {BORDER}; }}
QTabBar#subnav::tab:selected:hover {{ border-bottom: 3px solid {ORANGE}; }}
QTabBar#subnav::tab:selected:focus {{ color: {CREAM}; background: rgba(232, 150, 79, 0.16); border-bottom: 3px solid {CREAM}; }}

/* ---- type ---- */
#pageTitle {{ font-family: {h}; font-size: 26px; font-weight: 800; color: {CREAM}; }}
#pageLead {{ color: {SOFT}; font-size: 14px; }}
#cardTitle {{ font-family: {h}; font-size: 17px; font-weight: 800; color: {CREAM}; }}
#kicker, #section {{ color: {ORANGE}; font-family: {u}; font-size: 12px; font-weight: 700; letter-spacing: 2px; }}
#muted {{ color: {MUTED}; font-size: 13px; }}
#help {{ color: {MUTED}; font-size: 12px; }}
#reason {{ color: {ORANGE}; font-size: 12px; font-style: italic; }}
#ok {{ color: {OK}; }} #bad {{ color: {ERR}; }}
#settingLabel {{ font-weight: 600; color: {TEXT}; }}
#statusTitle {{ font-family: {u}; font-size: 15px; font-weight: 700; color: {CREAM}; letter-spacing: 0.5px; }}

/* ---- surfaces ---- */
#card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 16px; }}
#card QFrame#card {{ background: {PANEL2}; }}
#warn {{ background: #3a2219; border: 1px solid #7a4429;
  border-left: 3px solid {RUST}; border-radius: 12px; padding: 12px 14px; color: {NOTICE}; }}
#separator {{ background: {BORDER}; }}

/* ---- buttons: the website's ghost and primary .btn ---- */
QPushButton {{ background: #30251c; border: 2px solid {CONTROL}; border-radius: 12px;
  padding: 7px 18px; color: {CREAM}; font-family: {u}; font-size: 15px; font-weight: 700; letter-spacing: 0.4px; }}
QPushButton:hover {{ border-color: {ORANGE}; background: #43301f; }}
QPushButton:focus {{ border-color: {ORANGE}; background: #4a3421; }}
QPushButton:pressed {{ background: #5a3d25; }}
QPushButton:disabled {{ color: {DISABLED}; border-color: {LINE}; background: {DISABLED_BG}; }}
QPushButton#play {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0.3, stop:0 {GRAD_START}, stop:1 {RUST});
  color: {INK}; border: 2px solid transparent; border-radius: 16px; padding: 14px 60px;
  font-family: {u}; font-size: 24px; font-weight: 700; letter-spacing: 1px; }}
QPushButton#play:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0.3, stop:0 #f2b373, stop:1 #db8253); }}
QPushButton#play:focus {{ border: 2px solid {CREAM}; }}
QPushButton#play:disabled {{ background: {PANEL2}; color: {DISABLED}; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0.3, stop:0 {GRAD_START}, stop:1 {RUST});
  color: {INK}; border: 2px solid transparent; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0.3, stop:0 #f2b373, stop:1 #db8253); }}
QPushButton#primary:focus {{ border: 2px solid {CREAM}; }}
QPushButton#primary:disabled {{ background: {PANEL2}; color: {DISABLED}; }}
QPushButton#link {{ background: transparent; border: none; color: {ORANGE}; padding: 4px 0; text-align: left;
  font-family: {u}; font-size: 15px; font-weight: 700; }}
QPushButton#link:hover {{ color: {CREAM}; text-decoration: underline; }}
QPushButton#link:focus {{ color: {CREAM}; text-decoration: underline; }}
QToolButton#disclosure {{ background: transparent; border: 2px solid transparent; border-radius: 8px;
  padding: 5px 8px; color: {SOFT}; font-family: {u}; font-size: 15px; font-weight: 700; }}
QToolButton#disclosure:hover {{ color: {CREAM}; background: rgba(241, 227, 206, 0.06); }}
QToolButton#disclosure:focus {{ border-color: {ORANGE}; color: {CREAM}; }}

/* ---- inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
  background: {BG_DEEP}; border: 1px solid {CONTROL}; border-radius: 10px; padding: 8px 14px;
  selection-background-color: #8a4a26; selection-color: #ffffff; color: {TEXT}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {SAND}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
  border: 1px solid {ORANGE}; background: #1d140e; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
  color: {DISABLED}; background: {DISABLED_BG}; border-color: {BORDER}; }}
QSpinBox, QDoubleSpinBox {{ padding: 8px 0; }}
QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{
  background: transparent; border: none; border-radius: 0; padding: 0; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{ARROW}"); width: 12px; height: 8px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {CONTROL}; selection-background-color: #6a3f24;
  selection-color: {CREAM}; outline: none; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
  width: 0; border: none; }}
QProgressBar {{ background: {BG_DEEP}; border: 1px solid {BORDER}; border-radius: 8px; height: 16px; text-align: center;
  color: {TEXT}; font-size: 12px; }}
QProgressBar::chunk {{ border-radius: 7px;
  background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {GRAD_START}, stop:1 {RUST}); }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ORANGE}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {CREAM}; width: 16px; margin: -6px 0; border-radius: 8px; border: 2px solid {BG_DEEP}; }}
QSlider:focus::handle:horizontal {{ border: 2px solid {ORANGE}; }}

/* ---- tabs inside a page (Setup's stages, Settings' categories) ---- */
QTabBar::tab {{ padding: 6px 9px; margin-right: 2px; color: #f0e3d2; font-family: {u}; font-size: 15px; font-weight: 600;
  border: 2px solid transparent; border-radius: 10px; background: transparent; }}
QTabBar::tab:selected {{ color: {INK}; background: {SAND}; border-color: {SAND}; }}
QTabBar::tab:hover {{ color: {CREAM}; background: rgba(241, 227, 206, 0.08); }}
QTabBar::tab:selected:hover {{ color: {INK}; background: {CREAM}; }}
QTabBar::tab:selected:focus {{ border-color: {ORANGE}; }}
QTabBar::tab:focus {{ border-color: {ORANGE}; }}
QTabBar QToolButton {{ background: {PANEL2}; border: 1px solid {CONTROL}; border-radius: 6px; }}

/* ---- lists, tables, scrolling ---- */
QScrollArea {{ border: none; background: transparent; }}
QListWidget#settingsSearchResults {{ background: {PANEL2}; border: 1px solid {CONTROL};
  border-radius: 10px; padding: 3px; outline: none; }}
QListWidget#settingsSearchResults::item {{ padding: 6px 12px; border-radius: 6px; }}
QListWidget#settingsSearchResults::item:selected {{ background: #6a3f24; color: {CREAM}; }}
QListWidget#settingsSearchResults:focus::item:selected {{ border: 1px solid {ORANGE}; }}
QListView#catalogGrid {{ background: transparent; border: none; outline: none; }}
QListView#catalogGrid::item {{ padding: 6px; border: 1px solid transparent; border-radius: 10px; }}
QListView#catalogGrid::item:hover {{ background: {PANEL}; border-color: {BORDER}; }}
QListView#catalogGrid::item:selected {{ background: {PANEL2}; border-color: {ORANGE}; color: {CREAM}; }}
QListView#catalogGrid::item:focus {{ border-color: {ORANGE}; }}
QSplitter#catalogSplitter::handle:horizontal {{ width: 6px; background: {BORDER}; }}
QHeaderView::section {{ font-family: {u}; font-size: 14px; font-weight: 700; letter-spacing: 0.4px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ border-radius: 4px; min-height: 36px; margin: 0 2px;
  background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {ORANGE}, stop:1 {RUST}); }}
QScrollBar::handle:vertical:hover {{ background: {SAND}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ border-radius: 4px; min-width: 36px; margin: 2px 0; background: {CONTROL}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; width: 0; background: none; }}

/* ---- check boxes and radio buttons ---- */
QRadioButton, QCheckBox {{ spacing: 10px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 10px; border: 2px solid {CONTROL}; background: {BG_DEEP}; }}
QRadioButton::indicator:hover {{ border-color: {SAND}; }}
QRadioButton::indicator:checked {{ border-color: {ORANGE}; background: qradialgradient(cx:0.5,cy:0.5,radius:0.5,
  fx:0.5,fy:0.5, stop:0 {ORANGE}, stop:0.55 {ORANGE}, stop:0.6 {BG_DEEP}, stop:1 {BG_DEEP}); }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 2px solid {CONTROL}; background: {BG_DEEP}; }}
QCheckBox::indicator:hover {{ border-color: {SAND}; }}
QCheckBox::indicator:checked {{ background: {ORANGE}; border-color: {ORANGE}; image: url("{CHECK}"); }}
QCheckBox::indicator:checked:hover {{ border-color: {CREAM}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {DISABLED}; }}
QCheckBox::indicator:disabled {{ background: {DISABLED_BG}; border-color: {BORDER}; }}
QCheckBox::indicator:checked:disabled {{ background: {DISABLED}; border-color: {DISABLED}; }}
QCheckBox:focus::indicator, QRadioButton:focus::indicator {{ border-color: {CREAM}; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {ORANGE}; padding: 6px; }}
QMenu {{ background: {PANEL2}; border: 1px solid {CONTROL}; padding: 4px; }}
QMenu::item:selected {{ background: #6a3f24; color: {CREAM}; }}
"""


QSS = stylesheet()
