"""Stable GitHub releases and verified update downloads; never writes the installed app."""
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import GITHUB_URL, __version__, fetch

API_URL = "https://api.github.com/repos/FH1-ReImagined/ForzaHorizon1-ReImagined/releases/latest"
TIMEOUT = 15


class UpdateError(Exception):
    pass


def version_key(value):
    """Only stable three-part release versions. Development builds are not comparable."""
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[0-9A-Za-z.-]+)?", value)
    return tuple(map(int, match.groups())) if match else None


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    digest: str
    size: int


@dataclass(frozen=True)
class Release:
    tag: str
    assets: tuple

    @property
    def url(self):
        return GITHUB_URL + "/releases/tag/" + urllib.parse.quote(self.tag, safe="")

    def newer_than(self, current=__version__):
        key = version_key(current)
        return key is not None and version_key(self.tag) > key

    def asset_for(self, system, flatpak=False, machine="x86_64"):
        if machine.lower() not in ("x86_64", "amd64"):
            return None
        if system == "win32":
            name = f"ForzaHorizon1-ReImagined-Setup-{self.tag.removeprefix('v')}.exe"
        elif system.startswith("linux"):
            name = ("ForzaHorizon1-ReImagined-x86_64.flatpak" if flatpak else
                    "ForzaHorizon1-ReImagined-linux-x86_64.tar.gz")
        else:
            return None
        return next((a for a in self.assets if a.name == name), None)


def parse_release(data):
    if not isinstance(data, dict):
        raise UpdateError("GitHub returned an invalid release. Try checking again later.")
    tag = data.get("tag_name", "")
    if not isinstance(tag, str) or version_key(tag) is None or data.get("draft") or data.get("prerelease"):
        raise UpdateError("The latest release is not a supported stable version. See Releases for details.")
    items = data.get("assets", [])
    if not isinstance(items, list):
        raise UpdateError("GitHub returned an invalid asset list. Try checking again later.")
    assets = []
    prefix = GITHUB_URL + "/releases/download/" + urllib.parse.quote(tag, safe="") + "/"
    for item in items:
        if not isinstance(item, dict):
            continue
        name, url, digest, size = (item.get(k) for k in ("name", "browser_download_url", "digest", "size"))
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+", name)
                or url != prefix + urllib.parse.quote(name, safe="")
                or not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)
                or type(size) is not int or size <= 0):
            continue  # Never download an unverified asset or one outside this repository/tag.
        assets.append(Asset(name, url, digest[7:].lower(), size))
    return Release(tag, tuple(assets))


def check():
    request = urllib.request.Request(API_URL, headers={
        "User-Agent": "forza-reimagined-launcher/" + __version__,
        "Accept": "application/vnd.github+json",
    })
    try:
        with fetch.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise UpdateError("GitHub returned too much release data. Try again later.")
        return parse_release(json.loads(raw))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published release was found. Try again later.") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub's request limit was reached. Try again later or open Releases.") from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code}. Try again later.") from exc
    except fetch.CertificateError as exc:
        raise UpdateError(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise UpdateError("Could not check for updates. Check your connection and try again.") from exc


def download(asset: Asset, directory: Path, progress=lambda d, t: None, cancelled=lambda: False):
    """Use a partial file, check size and SHA-256, then atomically expose the completed file."""
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / asset.name
    if cancelled():
        raise fetch.Cancelled()
    if dest.is_file() and dest.stat().st_size == asset.size and fetch.sha256_of(dest) == asset.digest:
        return dest
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(asset.url, headers={"User-Agent": "forza-reimagined-launcher"})
    try:
        digest = hashlib.sha256()
        done = 0
        with fetch.urlopen(request, timeout=TIMEOUT) as response, part.open("wb") as output:
            while True:
                if cancelled():
                    raise fetch.Cancelled()
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                done += len(chunk)
                if done > asset.size:
                    raise UpdateError("The update's size does not match the release. Check again and retry.")
                output.write(chunk)
                digest.update(chunk)
                progress(done, asset.size)
        if cancelled():
            raise fetch.Cancelled()
        if done != asset.size or digest.hexdigest() != asset.digest:
            raise UpdateError("The update failed its integrity check. Check again and download a fresh copy.")
        part.replace(dest)
        return dest
    except OSError as exc:
        raise UpdateError(f"Could not download the update: {exc}. Try again.") from exc
    finally:
        part.unlink(missing_ok=True)
