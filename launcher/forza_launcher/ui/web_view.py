"""The launcher's own window onto the website's account page (Online > Online > Website account).

The player signs in to their forza.dyotson.dev account there, or makes one, with Discord, GitHub or
an emailed code (the same accounts as in a browser), and clicks Link on the page; the launcher's
dialog (ui/web_account.py) then asks them to confirm the account. Nothing about accounts changes:
this is the site's own page in a Qt WebEngine view instead of the player's browser.

What the launcher allows the view:
  - the main frame shows only the site and the Discord and GitHub sign-in pages it sends the player
    to (decide() below). Every other address, and every new window, opens in the player's browser.
    A link on the site to Discord or GitHub (an invite, the source code) opens there too.
  - no scripts, no QWebChannel and no cookie access from the launcher: it never reads the pages.
  - no certificate exceptions, no downloads, no devtools, and every page permission is denied.
    Passkeys and security keys work (webauthn()).
  - its own profile in the launcher's data folder (storage_dir()), shared with nothing else. It
    keeps the website's sign-in, like a browser does; forget() deletes it ("Sign out of the website
    in the launcher").

Qt WebEngine is optional: create() returns None where it is missing (a checkout with PySide6
Essentials only, Windows on Arm) or FORZA_NO_WEB_VIEW is set, and the dialog uses the player's
browser instead. It is imported only when the window opens, so a player who never signs in loads
none of it."""
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import paths

# Where the site's provider sign-in sends the player (allauth's Discord and GitHub apps).
PROVIDER_HOSTS = ("discord.com", "github.com")
PROFILE_NAME = "web-account"
FORGET_MARKER = "web-account.forget"   # a profile that could not be deleted at once goes at the next open

VIEW, BROWSER, REFUSE = "view", "browser", "refuse"


def origin(url):
    """('https', 'forza.dyotson.dev', 443) for a URL string; ports are filled in."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    return scheme, (parts.hostname or "").lower(), port or {"https": 443, "http": 80}.get(scheme)


def decide(url, main_frame=True, link_click=False, current="", site="https://forza.dyotson.dev"):
    """Where a navigation goes: VIEW (in the launcher's view), BROWSER (the player's browser, and
    not in the view) or REFUSE. `current` is the address the view shows, `site` the website's
    origin."""
    scheme, host, port = origin(url)
    if not main_frame:
        # frames inside a page (the providers' captchas): secure ones only, never a local file
        if scheme in ("https", "blob") or url in ("about:blank", "about:srcdoc"):
            return VIEW
        return REFUSE
    if url == "about:blank":
        return VIEW
    if (scheme, host, port) == origin(site):
        return VIEW
    if scheme == "https" and host in PROVIDER_HOSTS and port == 443:
        # the site's sign-in form and the providers' own pages stay; the site's plain links to
        # Discord or GitHub (an invite, the source code) are for the browser
        if link_click and origin(current)[:2] != ("https", host):
            return BROWSER
        return VIEW
    if scheme == "https" and host:
        return BROWSER
    if scheme in ("http", "mailto") and link_click:
        return BROWSER     # what the player clicked, in the program they picked for it
    return REFUSE


def storage_dir():
    """The view's profile: cookies, the site's storage and the cache (paths.data_home())."""
    return paths.data_home() / PROFILE_NAME


def has_profile(storage=None):
    storage = Path(storage or storage_dir())
    return storage.is_dir() and not (storage.parent / FORGET_MARKER).exists()


def forget(storage=None):
    """Delete the view's profile: the launcher's window is then signed out of the website, Discord
    and GitHub. A profile still in use (on Windows a file can stay open a moment after the window
    closed) is deleted when the window next opens."""
    storage = Path(storage or storage_dir())
    marker = storage.parent / FORGET_MARKER
    shutil.rmtree(storage, ignore_errors=True)
    if storage.exists():
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("delete the web-account folder before using it\n", encoding="utf-8")
        except OSError:
            pass
    else:
        marker.unlink(missing_ok=True)
    return not storage.exists()


def _forget_pending(storage):
    marker = storage.parent / FORGET_MARKER
    if marker.exists():
        shutil.rmtree(storage, ignore_errors=True)
        if not storage.exists():
            marker.unlink(missing_ok=True)


def available():
    """Qt WebEngine can be imported (and is not turned off)."""
    if os.environ.get("FORZA_NO_WEB_VIEW"):
        return False
    try:
        import PySide6.QtWebEngineCore  # noqa: F401
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        return False
    return True


def create(site, parent=None, storage=None):
    """A SignInView, or None where Qt WebEngine is missing or does not start."""
    if not available():
        return None
    try:
        view = _classes()(site, parent, storage)
    except Exception as e:  # a broken Qt WebEngine install: the browser works instead
        paths.log_error("website account window", e)
        return None
    global _quit_hooked
    if not _quit_hooked:
        from PySide6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(_release_all)
        _quit_hooked = True
    return view


def _release_all():
    """At the launcher's exit: no page or profile may outlive the QApplication (Qt WebEngine
    crashes then)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    for view in list(_live):
        view.close_down()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def open_in_browser(url):
    return QDesktopServices.openUrl(QUrl(url) if isinstance(url, str) else url)


_built = None
_closing = []   # views, pages and profiles whose deletion Qt has not run yet
_live = []      # open SignInViews
_quit_hooked = False


def _classes():
    """SignInView, defined on first use so that importing this module loads no Qt WebEngine."""
    global _built
    if _built is not None:
        return _built
    from PySide6.QtCore import Qt
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    Page_ = QWebEnginePage

    class Page(QWebEnginePage):
        """The navigation allow-list (decide()), new windows to the browser, nothing granted."""

        def __init__(self, profile, site, parent=None):
            super().__init__(profile, parent)
            self.site = site
            self.newWindowRequested.connect(self._new_window)
            self.certificateError.connect(lambda error: error.rejectCertificate())
            if hasattr(self, "permissionRequested"):          # Qt 6.8+
                self.permissionRequested.connect(lambda permission: permission.deny())
            else:
                self.featurePermissionRequested.connect(
                    lambda url, feature: self.setFeaturePermission(url, feature, Page_.PermissionPolicy.PermissionDeniedByUser))
            if hasattr(self, "fileSystemAccessRequested"):
                self.fileSystemAccessRequested.connect(lambda request: request.reject())
            if hasattr(self, "desktopMediaRequested"):
                self.desktopMediaRequested.connect(lambda request: request.cancel())
            self.registerProtocolHandlerRequested.connect(lambda request: request.reject())
            self.fullScreenRequested.connect(lambda request: request.reject())
            self.selectClientCertificate.connect(lambda selection: selection.selectNone())

        def acceptNavigationRequest(self, url, kind, main_frame):
            text = url.toString()
            where = decide(text, main_frame, kind == Page_.NavigationType.NavigationTypeLinkClicked,
                           self.url().toString(), self.site)
            if where == BROWSER:
                open_in_browser(url)
            return where == VIEW

        def _new_window(self, request):
            # target=_blank and window.open: the player's browser, for any secure address
            url = request.requestedUrl()
            if url.scheme() in ("https", "http", "mailto"):
                open_in_browser(url)

    class SignInView(QWidget):
        """The address line (with Back and Reload) over the page. `failed` when the page's process
        ends, `notice` for a security key's prompts."""
        failed = Signal(str)
        notice = Signal(str)

        def __init__(self, site, parent=None, storage=None):
            super().__init__(parent)
            self.site = site
            self.storage = Path(storage or storage_dir())
            _forget_pending(self.storage)
            self.storage.mkdir(parents=True, exist_ok=True)
            self._webauth = None
            self.profile = self._profile()
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            bar = QHBoxLayout()
            bar.setSpacing(8)
            self.back = QPushButton("Back")
            self.reload = QPushButton("Reload")
            self.address = QLineEdit()
            self.address.setReadOnly(True)
            self.address.setFocusPolicy(Qt.ClickFocus)
            self.address.setToolTip("The page shown below. The launcher shows only forza.dyotson.dev and "
                                    "Discord's and GitHub's sign-in pages here; other links open your browser.")
            for b in (self.back, self.reload):
                b.setAutoDefault(False)
            bar.addWidget(self.back)
            bar.addWidget(self.address, 1)
            bar.addWidget(self.reload)
            lay.addLayout(bar)
            self.view = QWebEngineView(self)
            self.page = Page(self.profile, site)
            self.view.setPage(self.page)
            self.view.setMinimumHeight(320)
            self.view.setContextMenuPolicy(Qt.CustomContextMenu)
            self.view.customContextMenuRequested.connect(self._menu)
            lay.addWidget(self.view, 1)
            self.back.clicked.connect(self.view.back)
            self.reload.clicked.connect(self.view.reload)
            self.view.urlChanged.connect(self._shown)
            self.page.renderProcessTerminated.connect(self._ended)
            if hasattr(self.page, "webAuthUxRequested"):      # Qt 6.7+
                self.page.webAuthUxRequested.connect(self._webauth_started)
            self._shown(QUrl())
            _live.append(self)

        def _profile(self):
            Policy = QWebEngineProfile.PersistentPermissionsPolicy
            cache = str(self.storage / "cache")
            try:
                from PySide6.QtWebEngineCore import QWebEngineProfileBuilder   # Qt 6.9+
                builder = QWebEngineProfileBuilder()
                builder.setPersistentStoragePath(str(self.storage))
                builder.setCachePath(cache)
                builder.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
                builder.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
                builder.setPersistentPermissionsPolicy(Policy.AskEveryTime)
                profile = builder.createProfile(PROFILE_NAME)
            except ImportError:
                profile = QWebEngineProfile(PROFILE_NAME)
                profile.setPersistentStoragePath(str(self.storage))
                profile.setCachePath(cache)
                if hasattr(profile, "setPersistentPermissionsPolicy"):
                    profile.setPersistentPermissionsPolicy(Policy.AskEveryTime)
            profile.downloadRequested.connect(lambda download: download.cancel())
            s, A = profile.settings(), QWebEngineSettings.WebAttribute
            for name, on in (("JavascriptCanAccessClipboard", False), ("JavascriptCanPaste", False),
                             ("LocalContentCanAccessRemoteUrls", False), ("LocalContentCanAccessFileUrls", False),
                             ("AllowRunningInsecureContent", False), ("AllowGeolocationOnInsecureOrigins", False),
                             ("PluginsEnabled", False), ("PdfViewerEnabled", False), ("ScreenCaptureEnabled", False),
                             ("FullScreenSupportEnabled", False), ("NavigateOnDropEnabled", False),
                             ("HyperlinkAuditingEnabled", False), ("JavascriptCanOpenWindows", True)):
                if hasattr(A, name):
                    s.setAttribute(getattr(A, name), on)
            return profile

        def load(self, url):
            self.view.load(QUrl(url))

        def url(self):
            return self.view.url().toString()

        def _shown(self, url):
            """The page's origin (scheme, host and a port other than the usual one)."""
            port = url.port()
            self.address.setText(f"{url.scheme()}://{url.host()}" + (f":{port}" if port != -1 else "")
                                 if url.host() else "")
            self.back.setEnabled(self.view.history().canGoBack())

        def _menu(self, pos):
            """The page's menu with only editing and Back/Forward/Reload (no source, saving or new
            windows)."""
            keep = {self.page.action(getattr(Page_.WebAction, n)) for n in (
                "Back", "Forward", "Reload", "Undo", "Redo", "Cut", "Copy", "Paste", "SelectAll")}
            menu = self.view.createStandardContextMenu()
            for action in menu.actions():
                if action not in keep and not action.isSeparator():
                    menu.removeAction(action)
            if any(not a.isSeparator() for a in menu.actions()):
                menu.exec(self.view.mapToGlobal(pos))
            menu.deleteLater()

        def _ended(self, status, code):
            if status != Page_.RenderProcessTerminationStatus.NormalTerminationStatus:
                self.failed.emit("The launcher's window stopped.")

        # passkeys and security keys: the page asks, the launcher shows Chromium's prompts
        def _webauth_started(self, request):
            self._webauth = request
            request.stateChanged.connect(lambda state, r=request: self._webauth_state(r, state))
            self._webauth_state(request, request.state())

        def _webauth_state(self, request, state):
            S = type(request).WebAuthUxState
            if state == S.SelectAccount:
                names = list(request.userNames())
                name, ok = QInputDialog.getItem(self, "Passkey", f"Sign in to {request.relyingPartyId()} as:",
                                                names, 0, False)
                request.setSelectedAccount(name) if ok and name else request.cancel()
            elif state == S.CollectPin:
                pin, ok = QInputDialog.getText(self, "Security key", "Your security key's PIN:",
                                               QLineEdit.EchoMode.Password)
                request.setPin(pin) if ok and pin else request.cancel()
            elif state == S.FinishTokenCollection:
                self.notice.emit("Touch your security key.")
            elif state == S.RequestFailed:
                self.notice.emit("The security key or passkey did not work. Try again, or sign in another way.")
                request.cancel()
            elif state in (S.Cancelled, S.Completed):
                self.notice.emit("")
                self._webauth = None

        def close_down(self):
            """End the page, then the profile, so that Qt WebEngine's processes end too."""
            page, profile = getattr(self, "page", None), getattr(self, "profile", None)
            if page is None:
                return
            self.page = self.profile = None
            self._webauth = None
            if self in _live:
                _live.remove(self)
            self.view.stop()
            # kept until Qt deleted them: posted deletions run in order, the page before its profile
            held = (self.view, page, profile)
            _closing.append(held)
            profile.destroyed.connect(lambda *_: held in _closing and _closing.remove(held))
            for thing in held:
                thing.deleteLater()

    _built = SignInView
    return _built
