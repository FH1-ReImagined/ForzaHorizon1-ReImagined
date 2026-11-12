"""Opening a link in the player's browser, or a folder or file in their file manager (issue #815).

QDesktopServices.openUrl starts xdg-open (or the desktop's own opener) with the launcher's own
environment. In the frozen Linux launcher (PyInstaller) that environment points into _internal:
QT_PLUGIN_PATH and QML2_IMPORT_PATH at the bundle's Qt, and the bootloader's own variables, so a
Qt opener such as KDE's kde-open loads plugins of another Qt version and stops, and the browser
never opens while openUrl still answers True. There, open_url starts the openers itself with the
player's own environment (cleanenv.clean_environment): xdg-open, then gio open, then the desktop
portal (OpenURI, links only), and Qt's own way last. Elsewhere (Windows, the Flatpak, a checkout)
it is QDesktopServices.openUrl.

install() makes Qt's own openUrl, which labels with links and the web view use, take the same way.
"""
import shutil
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QDesktopServices

from . import cleanenv

WAIT = 2.0                  # seconds an opener gets to fail before it counts as started
SCHEMES = ("http", "https")  # the ones install() takes over (Qt opens local files itself)
PORTAL = ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
          "--object-path", "/org/freedesktop/portal/desktop",
          "--method", "org.freedesktop.portal.OpenURI.OpenURI"]


def external() -> bool:
    """The openers run with a clean environment: the frozen launcher on Linux."""
    return sys.platform.startswith("linux") and cleanenv.frozen()


def as_qurl(url) -> QUrl:
    if isinstance(url, QUrl):
        return url
    return QUrl(str(url))


def commands(url: QUrl) -> list:
    """The openers to try, in order, for a link or a local file."""
    if url.isLocalFile():
        target = url.toLocalFile()
        return [["xdg-open", target], ["gio", "open", target]]
    target = url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
    return [["xdg-open", target], ["gio", "open", target], PORTAL + ["", target, "@a{sv} {}"]]


def run_command(cmd, env, popen=subprocess.Popen, wait=WAIT) -> bool:
    """Whether the opener started: it ended well, or still runs after `wait` seconds (xdg-open
    without a desktop runs the browser itself until it closes)."""
    exe = shutil.which(cmd[0], path=env.get("PATH"))
    if not exe:
        return False
    try:
        proc = popen([exe] + list(cmd[1:]), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    try:
        return proc.wait(timeout=wait) == 0
    except subprocess.TimeoutExpired:
        threading.Thread(target=proc.wait, name="opener", daemon=True).start()   # no zombie left
        return True


def open_with_commands(url, env=None, run=None) -> bool:
    url = as_qurl(url)
    env = cleanenv.clean_environment() if env is None else env
    run = run or run_command
    return any(run(cmd, env) for cmd in commands(url))


_handler = None


def _qt_open(url: QUrl) -> bool:
    """Qt's own openUrl, past install()'s handler (whose answer Qt does not pass on)."""
    if _handler is None:
        return QDesktopServices.openUrl(url)
    for scheme in SCHEMES:
        QDesktopServices.unsetUrlHandler(scheme)
    try:
        return QDesktopServices.openUrl(url)
    finally:
        for scheme in SCHEMES:
            QDesktopServices.setUrlHandler(scheme, _handler, "open")


def open_url(url) -> bool:
    """Open a link (str or QUrl) or a local file or folder (QUrl.fromLocalFile); False when nothing
    could, so the caller shows the link or the path instead."""
    url = as_qurl(url)
    if external() and open_with_commands(url):
        return True
    return _qt_open(url)


class _Handler(QObject):
    @Slot(QUrl)
    def open(self, url):
        # inside a handler Qt's openUrl goes to the platform (it does not call the handler again)
        if not open_with_commands(url):
            QDesktopServices.openUrl(url)


def install() -> None:
    """In the frozen Linux launcher, Qt's openUrl of a link takes open_url's way too (once
    QApplication exists)."""
    global _handler
    if not external() or _handler is not None:
        return
    _handler = _Handler()
    for scheme in SCHEMES:
        QDesktopServices.setUrlHandler(scheme, _handler, "open")
