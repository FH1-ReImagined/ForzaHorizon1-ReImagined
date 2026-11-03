"""Downloading and unpacking the pinned archives."""
import hashlib
import os
import shutil
import ssl
import stat
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import pins


class Cancelled(Exception):
    pass


# CA bundles of the common distributions. A frozen launcher's OpenSSL looks where the build
# machine keeps them (Ubuntu), which Fedora, Bazzite, SteamOS or Arch don't have (#68).
_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian, Ubuntu, Arch, SteamOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora, Bazzite, RHEL
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/etc/ssl/cert.pem",                                  # Alpine, macOS
)
_CONTEXTS = None


class CertificateError(OSError):
    """The server's certificate could not be verified by any of the trust stores (#85)."""


def _default_context() -> ssl.SSLContext:
    """Python's default context plus the CA files of the distributions and certifi's bundle."""
    ctx = ssl.create_default_context()
    for ca in (os.environ.get("SSL_CERT_FILE"),) + _CA_FILES:
        if ca and os.path.isfile(ca):
            try:
                ctx.load_verify_locations(cafile=ca)
            except (OSError, ssl.SSLError):
                pass
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def _truststore_context():
    """A context that lets the operating system verify the chain (Windows fetches a missing root
    certificate itself while doing so; Python's own read of the ROOT store does not). None where
    truststore is not installed."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return None


def contexts() -> list:
    """The verifying contexts to try, in order. Verification is never turned off.
    Windows and macOS: the operating system's verification (truststore), then the default context
    with certifi. Elsewhere only the default context (#68 made it find the distribution's CAs)."""
    global _CONTEXTS
    if _CONTEXTS is None:
        found = []
        if sys.platform in ("win32", "darwin"):
            ts = _truststore_context()
            if ts is not None:
                found.append(ts)
        found.append(_default_context())
        _CONTEXTS = found
    return _CONTEXTS


def ssl_context() -> ssl.SSLContext:
    """The first (preferred) verifying context."""
    return contexts()[0]


def is_certificate_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def urlopen(req, timeout=60):
    """urllib.request.urlopen with ssl_context(); when the certificate cannot be verified, the next
    context is tried, and when none can, a CertificateError that names the host."""
    ctxs = contexts()
    last = None
    for i, ctx in enumerate(ctxs):
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ssl.SSLError) as e:
            if not is_certificate_error(e):
                raise
            last = e
            try:
                from . import paths
                paths.log_error(f"certificate verification failed for {_host(req)} (context {i + 1} of {len(ctxs)})", e)
            except Exception:
                pass
    host = _host(req)
    raise CertificateError(
        f"The certificate of {host} could not be verified ({getattr(last, 'reason', last)}). "
        "Check this computer's date/time and trusted certificates, and whether an antivirus program "
        "scans HTTPS traffic or a proxy replaces certificates (turn off its HTTPS scanning or add "
        "the launcher as an exception). Certificate verification remains enabled. Details are in launcher.log.") from last


def _host(req) -> str:
    url = req if isinstance(req, str) else req.full_url
    return urllib.parse.urlsplit(url).hostname or url


def sha256_of(path: Path, progress=None):
    h = hashlib.sha256()
    size = path.stat().st_size
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    return h.hexdigest()


def download(name: str, cache: Path, progress=lambda d, t: None, cancelled=lambda: False) -> Path:
    """Download a pinned archive into cache (kept: a second run needs no download); returns its
    path after checking the sha256. Resumes a partial file."""
    url, digest, _kind = pins.DOWNLOADS[name]
    cache.mkdir(parents=True, exist_ok=True)
    final = cache / url.rsplit("/", 1)[1]
    if final.is_file() and sha256_of(final) == digest:
        return final
    part = final.with_name(final.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "forza-reimagined-launcher"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    try:
        resp = urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # the partial file is already complete
            resp, have = None, have
        else:
            e.resource_name, e.resource_host = final.name, _host(req)
            raise
    except OSError as e:
        e.resource_name, e.resource_host = final.name, _host(req)
        raise
    if resp is not None:
        if resp.status == 200 and have:  # the server ignored Range: start over
            have = 0
        total = have + int(resp.headers.get("Content-Length") or 0)
        mode = "ab" if have else "wb"
        with open(part, mode) as f:
            done = have
            while chunk := resp.read(1 << 20):
                if cancelled():
                    raise Cancelled()
                f.write(chunk)
                done += len(chunk)
                progress(done, total)
    actual_digest = sha256_of(part)
    if actual_digest != digest:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{final.name}: the download does not match its pinned checksum. "
                           f"Expected SHA-256 {digest}; actual SHA-256 {actual_digest}. "
                           f"The invalid partial file {part} was deleted; retry the download. "
                           "Other cached downloads were kept.")
    os.replace(part, final)
    return final


def _safe(dest: Path, name: str) -> Path:
    target = (dest / name).resolve()
    if not str(target).startswith(str(dest.resolve())):
        raise RuntimeError(f"unsafe path in archive: {name}")
    return target


def extract_zip(archive: Path, dest: Path, strip=0, progress=lambda d, t: None, cancelled=lambda: False):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        for i, info in enumerate(infos):
            if cancelled():
                raise Cancelled()
            parts = Path(info.filename).parts[strip:]
            if not parts:
                continue
            target = _safe(dest, str(Path(*parts)))
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif stat.S_ISLNK(mode):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                os.symlink(z.read(info).decode(), target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                if mode & 0o111:
                    os.chmod(target, (mode & 0o777) | 0o600)
            if i % 50 == 0:
                progress(i, len(infos))
    progress(len(infos), len(infos))


def extract_tar(archive: Path, dest: Path, strip=0, only=None, progress=lambda d, t: None,
                cancelled=lambda: False):
    """Unpack a tar(.gz/.xz). only: a predicate on the stripped member name (skip what is not needed)."""
    dest.mkdir(parents=True, exist_ok=True)
    total = archive.stat().st_size
    links = []
    with open(archive, "rb") as raw, tarfile.open(fileobj=raw, mode="r|*") as tar:
        for member in tar:
            if cancelled():
                raise Cancelled()
            parts = Path(member.name).parts[strip:]
            if not parts:
                continue
            name = str(Path(*parts))
            if only and not only(name):
                continue
            member.name = name
            if member.issym() or member.islnk():
                # Links are made at the end (_make_link). tarfile's own fallback for a link it
                # cannot create (a data folder on exFAT/FAT or some NTFS and network mounts,
                # Windows without the symlink privilege) loads the rest of the archive to find
                # the target; in this stream every file after that then fails with "seeking
                # backwards is not allowed" (#200).
                links.append((name, member.linkname, member.islnk()))
                continue
            tar.extract(member, dest, filter="tar")
            progress(raw.tell(), total)
    pending = []
    for name, linkname, hard in links:
        if hard:  # a hard link names its target from the archive root
            target = Path(linkname).parts[strip:]
            if not target:
                continue
            linkname = str(Path(*target))
        pending.append((name, linkname, hard))
    # A link can point to another link listed after it (bin/clang-cl -> clang -> clang-23): when
    # they become copies, repeat until a round makes no more.
    while pending:
        left = [link for link in pending if not _make_link(dest, *link)]
        if len(left) == len(pending):
            break
        pending = left
    progress(total, total)


def _make_link(dest: Path, name: str, linkname: str, hard: bool) -> bool:
    """A link of an unpacked archive: a symlink (or hard link) where the file system has
    them, else a copy of its target. False when it has to be a copy and the target is not
    there (yet); a target that was not unpacked (skipped) is left out."""
    root = dest.resolve()  # /home can itself be a symlink (/var/home on Fedora Atomic, Bazzite)
    path = _safe(root, str(Path(name).parent)) / Path(name).name
    try:
        source = _safe(root, os.path.relpath((root / linkname) if hard else (path.parent / linkname), root))
    except (RuntimeError, ValueError):
        return True  # points outside the unpacked folder
    if path.is_dir() and not path.is_symlink():
        return True  # the archive put real files there
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        path.unlink()
    try:
        if hard:
            os.link(source, path)
        else:
            os.symlink(linkname, path)
        return True
    except OSError:
        pass
    if source.is_file():
        shutil.copy2(source, path)
    elif source.is_dir():
        shutil.copytree(source, path, symlinks=False)
    else:
        return False
    return True


def unpack(name: str, archive: Path, dest: Path, strip=0, only=None, progress=lambda d, t: None,
           cancelled=lambda: False):
    kind = pins.DOWNLOADS[name][2]
    if kind == "zip":
        extract_zip(archive, dest, strip, progress, cancelled)
    else:
        extract_tar(archive, dest, strip, only, progress, cancelled)
