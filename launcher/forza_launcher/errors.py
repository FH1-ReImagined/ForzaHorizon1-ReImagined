"""Actionable summaries without replacing the original exception or guessing its cause."""
import errno
import html
import socket
import ssl
from urllib.error import HTTPError, URLError


def title(operation):
    return {
        "build": "Game build failed",
        "extract game files": "Game extraction failed",
        "check game files": "Game file check failed",
        "install DLC": "DLC installation failed",
        "prebuild shaders": "Shader preparation failed",
        "move data folder": "Data folder move failed",
        "start the game": "Game could not start",
    }.get(operation, f"Could not complete: {operation}")


def dialog_text(text):
    """Keep raw tool output literal in Qt's static AutoText message boxes."""
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


def next_step(error, operation):
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        # SSL's errno is an SSL_ERROR_* value, not a POSIX errno (1 collides
        # with EPERM). Classify it before filesystem/memory error numbers.
        if isinstance(current, ssl.SSLCertVerificationError) or type(current).__name__ == "CertificateError":
            return ("Check this computer's date/time and trusted certificates, and whether an antivirus program "
                    "scans HTTPS traffic or a proxy replaces certificates (turn off its HTTPS scanning or add "
                    "the launcher as an exception). Keep certificate verification enabled; send the launcher log if it persists.")
        if isinstance(current, ssl.SSLError):
            return "The secure connection failed. Check your connection and server address; send the launcher log if it persists. Keep certificate verification enabled."
        winerror = getattr(current, "winerror", None)
        number = getattr(current, "errno", None)
        if winerror == 126:
            return "Windows could not load a required runtime component. Reinstall the current launcher and rebuild to restore its libraries; keep your original game files."
        if winerror == 193:
            return "The selected program is not a valid executable for this system. Check its platform/architecture and repair the launcher installation before rebuilding."
        if number == errno.ENOSPC or winerror == 112:
            return "Free space on the drive containing the selected data/output folder, then retry."
        if winerror in (32, 33):
            return "Close the program using the file named above, then retry."
        if isinstance(current, PermissionError) or number in (errno.EACCES, errno.EPERM):
            return "Check access to the file or folder named above. Choose a writable data folder in Setup if needed."
        if number == errno.ENOMEM or winerror in (8, 14, 1455):
            return ("Close memory-heavy applications and reduce the build job count before retrying."
                    if operation == "build" else "Close memory-heavy applications, then retry.")
        if isinstance(current, FileNotFoundError):
            return ("Check the missing path above. If the game executable is missing, open Setup and Build."
                    if operation == "start the game" else
                    "Check the missing path above. Restore the file or select its current location in Setup before retrying.")
        if isinstance(current, HTTPError):
            if current.code == 429:
                return "The download service is rate-limiting requests. Wait before retrying."
            if current.code == 404:
                return "The download is unavailable at that address. Check for a launcher update; send the log if the current release still fails."
            if current.code in (401, 403):
                return "The download service refused access. Check for a launcher update; send the resource address and launcher log if it persists."
            return f"The download service returned HTTP {current.code}. Check the connection and retry later; send the log if it persists."
        if isinstance(current, (TimeoutError, socket.timeout)):
            return "The operation timed out. Check your connection if a download was in progress, then retry."
        if isinstance(current, socket.gaierror):
            return "The server address could not be resolved. Check your connection and the configured server address, then retry."
        if isinstance(current, ConnectionError):
            return "Check your internet/server connection and the address shown above, then retry."
        if isinstance(current, URLError) and isinstance(current.reason, BaseException):
            current = current.reason
            continue
        current = current.__cause__ or current.__context__
    return "Follow any specific instructions above. If the cause is still unclear, send the launcher log with your version and the steps that failed."


def message(error, operation, log, stage=""):
    detail = str(error).strip() or type(error).__name__
    resource = getattr(error, "resource_name", "")
    host = getattr(error, "resource_host", "")
    if isinstance(resource, str) and resource and isinstance(host, str):
        detail += f"\nDownload: {resource} (host: {host})"
    extra = getattr(error, "detail", "")
    if isinstance(extra, str) and extra and extra not in detail:
        detail += "\n" + extra
    if len(detail) > 4000:
        detail = detail[:4000] + "\n[Full details are in the launcher/build log.]"
    step = f"\nStep: {stage}" if stage else ""
    return f"{title(operation)}{step}\n{detail}\n\nNext: {next_step(error, operation)}\nLauncher log: {log}"
