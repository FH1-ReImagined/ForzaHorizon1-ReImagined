"""Small reusable widgets."""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QPen
from PySide6.QtWidgets import (QAbstractButton, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from .. import paths
from . import theme


class SpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt's spin-box editor does not inherit the outer stylesheet's padding.
        self.lineEdit().setTextMargins(14, 0, 14, 0)
        self.setMinimumWidth(80)


class DoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lineEdit().setTextMargins(14, 0, 14, 0)
        self.setMinimumWidth(100)


class Switch(QAbstractButton):
    """An on/off toggle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        # On: the orange track; off: a dark track with a CONTROL edge (3:1 against the card).
        track = QColor(theme.ORANGE if on else theme.BG_DEEP)
        edge = QColor(theme.ORANGE if on else theme.CONTROL)
        if not enabled:
            track = QColor(theme.DISABLED if on else theme.DISABLED_BG)
            edge = QColor(theme.DISABLED if on else theme.BORDER)
        p.setPen(QPen(edge, 1.5))
        p.setBrush(track)
        p.drawRoundedRect(QRectF(1.5, 3.5, 43, 19), 9.5, 9.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.INK if on and enabled else theme.SAND if enabled else theme.DISABLED))
        x = 27 if on else 5
        p.drawEllipse(QRectF(x, 6.5, 14, 14) if not on else QRectF(x - 1, 5.5, 16, 16))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(theme.CREAM), 1.5))
            p.drawRoundedRect(QRectF(0.75, 0.75, 44.5, 24.5), 12, 12)


class Disclosure(QWidget):
    """Secondary information stays available without filling the main page."""
    def __init__(self, title):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = QToolButton()
        self.toggle.setObjectName("disclosure")
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setFocusPolicy(Qt.StrongFocus)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.body.hide()
        self.toggle.toggled.connect(self._toggled)
        layout.addWidget(self.toggle, 0, Qt.AlignLeft)
        layout.addWidget(self.body)

    def _toggled(self, expanded):
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.body.setVisible(expanded)

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)


def card(layout_cls=QVBoxLayout, margins=(20, 18, 20, 18), spacing=10):
    f = QFrame()
    f.setObjectName("card")
    lay = layout_cls(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f, lay


def label(text, name="", wrap=True, parent=None):
    lb = QLabel(text, parent)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


class Hero(QWidget):
    """The project's banner (docs/images/social-preview.png), cropped to its content."""

    CROP_Y, CROP_H = 104, 432  # rows of the 1280x640 banner that hold the artwork

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pix = QPixmap(str(paths.asset("social-preview.png")))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * self.CROP_H / 1280)

    def sizeHint(self):
        return QSize(760, self.heightForWidth(760))

    def resizeEvent(self, e):
        self.setFixedHeight(self.heightForWidth(self.width()))
        super().resizeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 16, 16)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(theme.BG))
        if not self.pix.isNull():
            p.drawPixmap(QRectF(self.rect()), self.pix, QRectF(0, self.CROP_Y, 1280, self.CROP_H))
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)


class StatusRow(QWidget):
    """A colored dot, a title and a detail line."""

    def __init__(self, title):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(12)
        self.dot = QLabel("\u25cf")
        self.dot.setFixedWidth(14)
        lay.addWidget(self.dot, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("statusTitle")
        self.detail = label("", "muted")
        col.addWidget(self.title)
        col.addWidget(self.detail)
        lay.addLayout(col, 1)

    def set(self, ok, detail, title=None):
        color = {True: theme.OK, False: theme.ERR, None: theme.ORANGE}[ok]
        self.dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self.detail.setText(detail)
        if title:
            self.title.setText(title)
