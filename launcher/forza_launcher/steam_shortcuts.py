"""Add one Linux launcher shortcut while Steam is closed (#508).

No Steam account credentials, API, network, or client shutdown. Existing VDF
records stay byte-for-byte intact. Inspired by Pinyon Shift; see NOTICE.md.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

from . import paths

NAME = "Forza Horizon 1: ReImagined"
MAX_VDF = 16 * 1024 * 1024


class SteamError(Exception):
    pass


@dataclass(frozen=True)
class Account:
    root: Path
    user: str

    @property
    def config(self) -> Path:
        return self.root / "userdata" / self.user / "config"

    @property
    def label(self) -> str:
        kind = "Flatpak Steam" if self.flatpak else "Steam"
        return f"{kind} · account {self.user} · {self.root}"

    @property
    def flatpak(self) -> bool:
        return "com.valvesoftware.Steam" in self.root.parts


def accounts(home: Path | None = None) -> list[Account]:
    home = home or Path.home()
    roots = (home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam")
    found, seen = [], set()
    for candidate in roots:
        root = candidate.resolve()
        if root in seen:
            continue
        seen.add(root)
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for user in sorted(userdata.iterdir()):
            if user.name.isascii() and user.name.isdigit() and int(user.name) and (user / "config").is_dir():
                found.append(Account(root, user.name))
    return found


def require_closed(in_flatpak: bool | None = None) -> None:
    """Inspect native host processes. Fail closed inside Flatpak.

    pgrep returns 1 only for no match; 2/3, a missing utility, timeout or denied
    host access cannot establish that Steam is closed. Recheck before commit.
    Neither absence of a PID file nor absence of a D-Bus name proves that a
    host Steam process is absent from an isolated sandbox.
    """
    in_flatpak = paths.IN_FLATPAK if in_flatpak is None else in_flatpak
    if in_flatpak:
        raise SteamError("Automatic Steam setup cannot verify host processes from this Flatpak. "
                         "Use Games > Add a Non-Steam Game in Steam; see the Steam Deck guide.")
    command = ["pgrep", "-x", "steam|steamwebhelper"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SteamError("Could not check whether Steam is closed. See the Steam Deck guide for manual setup.") from exc
    if result.returncode == 0:
        raise SteamError("Exit Steam completely, then try Add to Steam again. Keep Steam closed until it finishes.")
    if result.returncode != 1:
        raise SteamError("Could not verify that Steam is closed. See the Steam Deck guide for manual setup.")


@dataclass(frozen=True)
class Command:
    exe: str
    start_dir: str
    options: str = ""

    @property
    def appid(self) -> int:
        return zlib.crc32((self.exe + NAME).encode("utf-8")) | 0x80000000


def _quote(value: str) -> str:
    # Steam's shortcut command parsing is not a shell quoting contract. Reject
    # ambiguous paths rather than create a command with changed meaning.
    if any(c in value for c in '\x00\r\n"`$\\'):
        raise SteamError("This launcher path cannot be represented safely in a Steam shortcut. Move it to a simpler path.")
    return '"' + value + '"'


def launcher_command() -> Command:
    if paths.IN_FLATPAK:
        raise SteamError("Use Steam's Games > Add a Non-Steam Game menu for the launcher Flatpak.")
    if not paths.FROZEN:
        raise SteamError("Add to Steam is available in the installed Linux launcher. For a source checkout, use Steam's manual Add a Non-Steam Game option.")
    exe = Path(sys.executable).resolve()
    return Command(_quote(str(exe)), _quote(str(exe.parent)))


@dataclass
class Entry:
    kind: int
    key: bytes
    value: bytes | list
    end: int


def parse(data: bytes) -> list[Entry]:
    """Bounded binary KeyValues parser. Unknown/invalid types are never rewritten."""
    if len(data) > MAX_VDF:
        raise SteamError("Steam's shortcuts file is too large to edit safely.")
    pos = 0

    def string():
        nonlocal pos
        start = pos
        end = data.find(b"\0", pos)
        if end >= 0:
            pos = end + 1
            return data[start:end]
        raise SteamError("Steam's shortcuts file has an unterminated string.")

    def group(depth):
        nonlocal pos
        if depth > 32:
            raise SteamError("Steam's shortcuts file is nested too deeply.")
        entries = []
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 8:
                return entries
            key = string()
            if kind == 0:
                value = group(depth + 1)
            elif kind == 1:
                value = string()
            elif kind in (2, 3, 4, 6, 7, 10):
                size = 8 if kind in (7, 10) else 4
                value = data[pos:pos + size]
                pos += size
                if len(value) != size:
                    raise SteamError("Steam's shortcuts file is truncated.")
            else:
                raise SteamError(f"Unsupported Steam shortcuts field type {kind}; the file was left unchanged.")
            entries.append(Entry(kind, key, value, pos))
        raise SteamError("Steam's shortcuts file is missing an end marker.")

    out = group(0)
    if pos != len(data):
        raise SteamError("Steam's shortcuts file has unexpected trailing data.")
    return out


def _field(entries, name, kind):
    matches = [e for e in entries if e.key.lower() == name.lower()]
    if len(matches) > 1 or (matches and matches[0].kind != kind):
        raise SteamError("Steam's shortcuts file contains ambiguous fields; it was left unchanged.")
    return matches[0].value if matches else None


def _string(key: str, value: str) -> bytes:
    return b"\1" + key.encode() + b"\0" + value.encode("utf-8") + b"\0"


def _integer(key: str, value: int) -> bytes:
    return b"\2" + key.encode() + b"\0" + struct.pack("<I", value)


def append_shortcut(data: bytes, command: Command, icon: str) -> bytes | None:
    """None means an existing matching shortcut; never replace user customizations."""
    roots = parse(data)
    matches = [e for e in roots if e.key == b"shortcuts"]
    if len(matches) != 1 or matches[0].kind != 0:
        raise SteamError("Steam's shortcuts file has no unambiguous shortcuts object.")
    root = matches[0]
    used = set()
    duplicate = False
    for entry in root.value:
        if entry.kind != 0 or not entry.key.isdigit() or entry.key in used:
            raise SteamError("Steam's shortcut rows are invalid or duplicated.")
        used.add(entry.key)
        name = _field(entry.value, b"appname", 1)
        exe = _field(entry.value, b"exe", 1)
        options = _field(entry.value, b"LaunchOptions", 1) or b""
        appid = _field(entry.value, b"appid", 2)
        same_command = exe == command.exe.encode() and options == command.options.encode()
        if name == NAME.encode() or same_command:
            duplicate = True
        elif appid == struct.pack("<I", command.appid):
            raise SteamError("Another Steam shortcut uses this app ID. No changes were made.")
    if duplicate:
        return None
    index = 0
    while str(index).encode() in used:
        index += 1
    row = b"\0" + str(index).encode() + b"\0" + _integer("appid", command.appid)
    for key, value in (("AppName", NAME), ("Exe", command.exe), ("StartDir", command.start_dir),
                       ("icon", icon), ("ShortcutPath", ""), ("LaunchOptions", command.options)):
        row += _string(key, value)
    for key, value in (("IsHidden", 0), ("AllowDesktopConfig", 1), ("AllowOverlay", 1),
                       ("OpenVR", 0), ("Devkit", 0), ("DevkitOverrideAppID", 0), ("LastPlayTime", 0)):
        row += _integer(key, value)
    row += _string("DevkitGameID", "") + _string("FlatpakAppID", "")
    row += b"\0tags\0" + _string("0", "Forza ReImagined") + b"\x08\x08"
    return data[:root.end-1] + row + data[root.end-1:]


def _read_file(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_VDF:
        raise SteamError(f"Cannot safely edit {path}: expected a regular, unlinked file under 16 MiB.")
    return path.read_bytes()


@contextmanager
def _lock(config: Path):
    path = config / ".forza-shortcut.lock"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SteamError("Another Add to Steam operation is active, or its lock remains from an interrupted run. See the Steam Deck guide.") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def _stage(config: Path, data: bytes, mode=0o600) -> Path:
    fd, name = tempfile.mkstemp(prefix=".forza-shortcut-", dir=config)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def add(account: Account, command: Command, artwork: dict[str, bytes], *, closed_check=None) -> str:
    """Transaction: stage/fsync, exclusive backup/art, recheck, atomic VDF commit.

    Existing art is preserved. On failure before the commit, remove only art
    created by this operation that still has our exact contents. Steam does not
    honor our lock: the user must keep it closed; checks narrow that race.
    """
    check = closed_check or require_closed
    config = account.config
    if account.flatpak:
        raise SteamError("Flatpak Steam needs manual setup for applications outside its sandbox. "
                         "See the Steam Deck guide. No Steam files were changed.")
    if not account.user.isascii() or not account.user.isdigit() or not int(account.user):
        raise SteamError("Invalid Steam account directory.")
    for part in (account.root / "userdata", config.parent, config):
        if part.is_symlink() or not part.is_dir():
            raise SteamError("Steam's account configuration is missing or redirected. Use Steam's manual setup.")
    check()
    with _lock(config):
        dest = config / "shortcuts.vdf"
        original = _read_file(dest)
        grid = config / "grid"
        if grid.is_symlink() or (grid.exists() and not grid.is_dir()):
            raise SteamError("Steam's artwork folder is redirected or invalid.")
        icon = grid / f"{command.appid}_icon.png"
        content = append_shortcut(original if original is not None else b"\0shortcuts\0\x08\x08", command, str(icon))
        if content is None:
            return "A matching Steam shortcut already exists. Its settings and artwork were left unchanged."
        staged, created = [], []
        committed = False
        backup = None
        try:
            mode = stat.S_IMODE(dest.stat().st_mode) if original is not None else 0o600
            replacement = _stage(config, content, mode)
            staged.append(replacement)
            if original is not None:
                saved = _stage(config, original, mode)
                staged.append(saved)
                backup = config / ("shortcuts.vdf.forza-backup-" + uuid.uuid4().hex)
                os.link(saved, backup)  # exclusive, complete backup; never overwrite one
            grid.mkdir(exist_ok=True)
            for suffix, data in artwork.items():
                if suffix not in (".png", "_hero.png", "_logo.png", "_icon.png"):
                    raise SteamError("Invalid shortcut artwork name.")
                target = grid / f"{command.appid}{suffix}"
                if target.exists() or target.is_symlink():
                    continue
                art = _stage(config, data)
                staged.append(art)
                try:
                    os.link(art, target)
                except FileExistsError:
                    continue  # another writer's artwork takes precedence
                created.append((target, data))
            check()
            if _read_file(dest) != original:
                raise SteamError("Steam's shortcuts changed during setup. Try again after Steam has exited.")
            os.replace(replacement, dest)
            committed = True
        finally:
            if not committed:
                for target, data in created:
                    try:
                        if not target.is_symlink() and target.read_bytes() == data:
                            target.unlink()
                    except OSError:
                        pass
            for temporary in staged:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        message = "Added to Steam. Open Steam and find Forza Horizon 1: ReImagined under Non-Steam."
        return message + (f" Backup: {backup}" if backup else "")


def bundled_artwork() -> dict[str, bytes]:
    logo = paths.asset("logo.png").read_bytes()
    banner = paths.asset("social-preview.png").read_bytes()
    return {".png": banner, "_hero.png": banner, "_logo.png": logo, "_icon.png": logo}
