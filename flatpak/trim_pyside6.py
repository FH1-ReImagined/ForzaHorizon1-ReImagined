#!/usr/bin/env python3
"""Deletes the parts of the PySide6 Addons and WebEngine wheels the launcher never loads (Flatpak
build, after pip). Qt WebEngine (the website sign-in window, launcher/forza_launcher/ui/web_view.py)
needs Addons only for a few libraries; the rest of Addons (3D, multimedia, charts, ...), Qt
WebEngine's QML side, its devtools and every locale but en-US are about 150 MB the launcher does
not use. The PyInstaller builds need none of this: they collect only what is imported, and the
spec drops the same locales and devtools.

  trim_pyside6.py <site-packages>

Only files the two wheels' RECORDs list are deleted, and never one that Essentials' or shiboken6's
RECORD lists too: the wheels share PySide6/__init__.py, _config.py, _git_pyside_version.py and
py.typed, and without __init__.py PySide6 would turn into a namespace package. So Essentials (the
rest of the launcher's Qt) is never touched."""
import csv
import sys
from pathlib import Path

# What of Addons libQt6WebEngineCore, libQt6WebEngineWidgets and QtWebEngineProcess link (ldd; the
# rest they link, Qml and Quick included, is in Essentials), and the one Python module
# `import PySide6.QtWebEngineWidgets` loads from Addons.
ADDONS_KEEP = {"PySide6/Qt/lib/libQt6Positioning.so.6", "PySide6/Qt/lib/libQt6WebChannel.so.6",
               "PySide6/QtWebChannel.abi3.so", "PySide6/QtWebChannel.pyi"}
WEBENGINE_LOCALES = "PySide6/Qt/translations/qtwebengine_locales/"
WEBENGINE_DROP = ("PySide6/Qt/resources/qtwebengine_devtools_resources.pak",
                  "PySide6/Qt/lib/libQt6WebEngineQuick", "PySide6/QtWebEngineQuick.", "PySide6/Qt/qml/")


def recorded(site, dist):
    """The files a wheel installed, from its RECORD (paths relative to site-packages)."""
    for info in site.glob(dist + "-*.dist-info"):
        with open(info / "RECORD", newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if row and ".dist-info/" not in row[0]:
                    yield row[0]


# The wheels whose files stay whole, shared ones included.
SHARED_WITH = ("pyside6_essentials", "shiboken6")


def drops(site):
    keep = set(ADDONS_KEEP)
    for dist in SHARED_WITH:
        owned = set(recorded(site, dist))
        if not owned:
            sys.exit(f"{site}: no {dist} RECORD, so the files it shares with Addons are unknown")
        keep |= owned
    for path in recorded(site, "pyside6_addons"):
        if path not in keep:
            yield path
    for path in recorded(site, "pyside6_webengine"):
        if path in keep:
            continue
        if path.startswith(WEBENGINE_LOCALES) and not path.endswith("/en-US.pak"):
            yield path
        elif path.startswith(WEBENGINE_DROP):
            yield path


def trim(site):
    site = Path(site)
    freed = 0
    for rel in drops(site):
        path = site / rel
        if path.is_file() or path.is_symlink():
            freed += path.lstat().st_size
            path.unlink()
    for folder in sorted((p for p in (site / "PySide6").rglob("*") if p.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    return freed


if __name__ == "__main__":
    for site in sys.argv[1:] or sys.exit(__doc__):
        print(f"{site}: {trim(site) / 1e6:.0f} MB of PySide6 the launcher does not load removed")
