"""Whose file dialogs the launcher opens (#884).

The launcher's own (Qt's) by default, everywhere, for two reasons: they take part in controller
navigation (ui/gamepad.py moves Qt's focus, and a dialog of the system is not Qt's), and they are
the same on Windows, on Linux and in the Flatpak.

Settings > Advanced > Launcher > "Use the system's file dialogs" (Config.advanced
"system_file_dialogs", off by default) leaves Qt free to open the system's instead: Windows' own,
and on Linux the desktop's where Qt has one for it. Where it has none, Qt opens its own as before.
Only file dialogs change: the launcher's messages and questions are Qt's on both systems.

The Flatpak always keeps the launcher's own, whatever the setting says. The system's dialog there
is the portal's, which gives the sandbox the one file picked, so a Games on Demand package would
lose the <name>.data folder next to it; the Flatpak has read access to the home folder and
removable drives instead, and Qt's dialog shows the real paths.
"""
from PySide6.QtCore import QCoreApplication, Qt

from .. import paths

KEY = "system_file_dialogs"
LABEL = "Use the system's file dialogs"
HELP = ("Browse, Add and Save open your system's file dialog in place of the launcher's own. "
        "Off (recommended): the launcher's own, which a controller can move in and which is the same "
        "on every system. The system's needs the mouse or the keyboard. On a Linux desktop that "
        "gives the launcher none, the launcher's own stays.")
FLATPAK_REASON = ("Not available in the Flatpak: the system's dialog gives the sandbox only the file you "
                  "pick, and a Games on Demand package needs the .data folder next to it.")


def available() -> bool:
    return not paths.IN_FLATPAK


def system(cfg) -> bool:
    """The player asked for the system's file dialogs, and they can be used here."""
    return bool(cfg.advanced.get(KEY)) and available()


def apply(cfg) -> None:
    """Sets whose file dialogs open from now on (the next one opened): at start, and when the
    setting changes."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, not system(cfg))
