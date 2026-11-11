"""CI helper (ci-toolchain-mirror.yml): keep the project's GitHub copy of the toolchain PPA's
Ubuntu 22.04 packages, which ci_linux_sdk_setup.sh installs g++-14 from before it asks Launchpad.

  ci_toolchain_mirror.py status REPO TAG          exit 0 when the release already holds every pinned
                                                  file, 10 when it lacks some, 1 when one differs
  ci_toolchain_mirror.py fetch OUT                download the packages in ci_toolchain_debs.txt and their
                                                  source packages, checked against the PPA's signed index
  ci_toolchain_mirror.py upload OUT REPO TAG      add OUT's files to the release REPO/TAG

fetch trusts nothing but the PPA's signing key: it checks InRelease's signature with gpgv, the
package and source indexes against InRelease, and every file against those indexes. Every file must
also match ci_toolchain_sha256sums.txt where that lists it (a different hash stops the run); files
it doesn't list yet are written to OUT/UNPINNED, for a pull request that adds them.

upload never replaces a release file with different bytes: it stops and names the file instead.

The source packages are there for the licenses: GCC and its runtime libraries are GPL-3.0 (the
runtime libraries with the GCC Runtime Library Exception) and LGPL-2.1 (libquadmath), whose object
code may be passed on only with its corresponding source, kept next to it.

GitHub doesn't keep + and ~ in release file names, so the copy spells them plus and tilde.
"""
import argparse
import hashlib
import json
import lzma
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "ci_toolchain_debs.txt"
PINS = HERE / "ci_toolchain_sha256sums.txt"
# The PPA's signing key (Launchpad's RSA 4096 key for ~ubuntu-toolchain-r/test). The old 1024-bit
# key also signs InRelease; only this one counts.
PPA_KEY_FINGERPRINT = "C8EC952E2A0E1FBDC5090F6A2C277A0A352154E5"
KEY_FILE = HERE / "ci_toolchain_ppa_key.asc"
KEY_URLS = (
    f"https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x{PPA_KEY_FINGERPRINT}",
)
PPA_HOSTS = (
    "https://ppa.launchpadcontent.net/ubuntu-toolchain-r/test/ubuntu",
    "http://ppa.launchpad.net/ubuntu-toolchain-r/test/ubuntu",
)
SUITE = "jammy"
ARCH = "amd64"
SUMS = "SHA256SUMS"
INCOMPLETE = 10
NOTES = """\
Ubuntu 22.04 (jammy) packages of GCC 14 and the GCC 16 runtime libraries, copied unchanged from \
the ubuntu-toolchain-r/test PPA (https://launchpad.net/~ubuntu-toolchain-r/+archive/ubuntu/test) \
for the project's CI, which builds on Ubuntu 22.04 and needs g++-14's C++23 headers. \
This is not a release of the game or the launcher.

Every file was checked against the PPA's signed index before it was added; SHA256SUMS lists them all \
and launcher/packaging/ci_toolchain_sha256sums.txt in the repository pins the same hashes.

Licenses: GCC is free software under the GNU General Public License version 3; its runtime \
libraries (libgcc, libstdc++, libgomp, libatomic, libitm) add the GCC Runtime Library Exception, \
libquadmath is under the GNU Lesser General Public License 2.1 and the sanitizer runtimes under \
their LLVM/MIT terms. Each package carries its license in /usr/share/doc/<package>/copyright.

Corresponding source: this release also holds the complete Debian source packages these binaries \
were built from (.dsc, .orig.tar.* and .debian.tar.* files). GitHub doesn't keep + and ~ in file \
names, so they are spelled "plus" and "tilde" here: rename the files back (for example \
gcc-14_14.3.0-12ubuntu1tilde22tildeppa2.dsc to gcc-14_14.3.0-12ubuntu1~22~ppa2.dsc) and unpack with \
dpkg-source -x <file>.dsc.
"""


def asset_name(name):
    """The release file name for a Debian file name; ci_linux_sdk_setup.sh spells it the same way."""
    asset = name.replace("+", "plus").replace("~", "tilde")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", asset) or asset.startswith("."):
        raise SystemExit(f"{name}: no safe release file name for it")
    return asset


def manifest_entries(text):
    """(package, version) for each line of ci_toolchain_debs.txt."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        package, sep, version = line.partition("_")
        if not sep or not version:
            raise SystemExit(f"ci_toolchain_debs.txt: {line!r} is not package_version")
        entries.append((package, version))
    return entries


def parse_sums(text):
    """{file name: sha256} from sha256sum-style lines; "#" lines are comments."""
    sums = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise SystemExit(f"line {number}: {line!r} is not '<sha256>  <file>'")
        name = parts[1].lstrip("*")
        if sums.get(name, parts[0]) != parts[0]:
            raise SystemExit(f"line {number}: {name} is listed twice with different hashes")
        sums[name] = parts[0]
    return sums


def format_sums(sums):
    return "".join(f"{sums[name]}  {name}\n" for name in sorted(sums))


def deb822(text):
    """The stanzas of a Debian control-style file as dicts (continuation lines joined with \\n)."""
    stanzas, current, key = [], {}, None
    for line in text.splitlines():
        if not line.strip():
            if current:
                stanzas.append(current)
            current, key = {}, None
        elif line[0] in " \t":
            if key is None:
                raise ValueError(f"continuation line without a field: {line!r}")
            current[key] += "\n" + line.strip()
        else:
            key, _, value = line.partition(":")
            current[key] = value.strip()
    if current:
        stanzas.append(current)
    return stanzas


def checksum_lines(field):
    """[(sha256, size, name)] from a Checksums-Sha256 / SHA256 field."""
    rows = []
    for line in field.splitlines():
        parts = line.split()
        if len(parts) == 3:
            rows.append((parts[0], int(parts[1]), parts[2]))
    return rows


def resolve(entries, packages_text, sources_text):
    """The files to copy, {debian file name: (pool path, sha256, size)}, for the manifest's packages
    and the source packages they were built from. Stops with every manifest line the index lacks."""
    packages = {}
    for stanza in deb822(packages_text):
        packages.setdefault((stanza.get("Package"), stanza.get("Version")), stanza)
    sources = {}
    for stanza in deb822(sources_text):
        sources[(stanza.get("Package"), stanza.get("Version"))] = stanza
    files, missing, wanted_sources = {}, [], []
    for package, version in entries:
        stanza = packages.get((package, version))
        if stanza is None or stanza.get("Architecture") not in (ARCH, "all"):
            now = sorted(v for (p, v) in packages if p == package)
            missing.append(f"{package}_{version}: not in the PPA's index"
                           + (f" (it has {', '.join(now)})" if now else ""))
            continue
        filename = stanza["Filename"]
        expected = f"{package}_{version}_{ARCH}.deb"
        if filename.rsplit("/", 1)[-1] != expected:
            raise SystemExit(f"{package}: the index names {filename}, not {expected}")
        files[expected] = (filename, stanza["SHA256"], int(stanza["Size"]))
        source = stanza.get("Source", package)
        match = re.fullmatch(r"(\S+)(?:\s+\((\S+)\))?", source)
        source_package, source_version = match.group(1), match.group(2) or version
        if (source_package, source_version) not in wanted_sources:
            wanted_sources.append((source_package, source_version))
    for source_package, source_version in wanted_sources:
        stanza = sources.get((source_package, source_version))
        if stanza is None:
            missing.append(f"source {source_package} {source_version}: not in the PPA's index")
            continue
        for sha256, size, name in checksum_lines(stanza["Checksums-Sha256"]):
            files[name] = (f"{stanza['Directory']}/{name}", sha256, size)
    if missing:
        raise SystemExit("The PPA's index lacks what ci_toolchain_debs.txt lists; update that list "
                         "(and ci_toolchain_sha256sums.txt) to the PPA's current versions:\n  "
                         + "\n  ".join(missing))
    return files


def release_hashes(inrelease_text):
    """{path: (sha256, size)} from the SHA256 section of a verified InRelease."""
    stanza = deb822(inrelease_text)[0]
    return {name: (sha256, size) for sha256, size, name in checksum_lines(stanza.get("SHA256", ""))}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, dest, attempts=3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ci-toolchain-mirror"})
            with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
                while block := response.read(1 << 20):
                    out.write(block)
            return
        except (urllib.error.URLError, OSError) as e:
            last = e
            if attempt < attempts:
                time.sleep(attempt * 10)
    raise OSError(f"{url}: {last}")


def fetch_checked(path, sha256, size, dest):
    """Download a PPA file from the first host that serves the expected bytes."""
    errors = []
    for host in PPA_HOSTS:
        try:
            download(f"{host}/{path}", dest)
        except OSError as e:
            errors.append(str(e))
            continue
        actual = sha256_file(dest)
        if actual == sha256 and dest.stat().st_size == size:
            return
        errors.append(f"{host}/{path}: sha256 {actual}, the signed index says {sha256}")
    dest.unlink(missing_ok=True)
    raise SystemExit("Could not download a verified copy:\n  " + "\n  ".join(errors))


def signing_key(work):
    """A gpgv keyring holding the PPA's key, after checking its fingerprint."""
    armored = work / "key.asc"
    if KEY_FILE.exists():
        armored.write_bytes(KEY_FILE.read_bytes())
    else:
        for url in KEY_URLS:
            try:
                download(url, armored)
                break
            except OSError as e:
                print(f"{e}", file=sys.stderr)
        else:
            raise SystemExit("Could not download the PPA's signing key")
    shown = subprocess.run(["gpg", "--batch", "--show-keys", "--with-colons", str(armored)],
                           capture_output=True, text=True, check=True).stdout
    fingerprints = [line.split(":")[9] for line in shown.splitlines() if line.startswith("fpr:")]
    if PPA_KEY_FINGERPRINT not in fingerprints:
        raise SystemExit(f"The downloaded key is not {PPA_KEY_FINGERPRINT}: {fingerprints}")
    keyring = work / "key.gpg"
    with open(armored, "rb") as src, open(keyring, "wb") as out:
        subprocess.run(["gpg", "--batch", "--dearmor"], stdin=src, stdout=out, check=True)
    return keyring


def verified_inrelease(work, keyring):
    """The signed text of InRelease (gpgv's output, so unsigned text around it can't count)."""
    errors = []
    for host in PPA_HOSTS:
        raw = work / "InRelease"
        try:
            download(f"{host}/dists/{SUITE}/InRelease", raw)
        except OSError as e:
            errors.append(str(e))
            continue
        signed = work / "InRelease.signed"
        signed.unlink(missing_ok=True)
        result = subprocess.run(["gpgv", "--status-fd", "1", "--keyring", str(keyring),
                                 "--output", str(signed), str(raw)], capture_output=True, text=True)
        valid = [line.split() for line in result.stdout.splitlines()
                 if line.startswith("[GNUPG:] VALIDSIG ")]
        if result.returncode == 0 and any(PPA_KEY_FINGERPRINT in (row[2], row[-1]) for row in valid):
            return signed.read_text()
        errors.append(f"{host}: InRelease is not signed by {PPA_KEY_FINGERPRINT}: {result.stderr.strip()}")
    raise SystemExit("No verified InRelease:\n  " + "\n  ".join(errors))


def index(work, hashes, path):
    if path not in hashes:
        raise SystemExit(f"InRelease does not list {path}")
    sha256, size = hashes[path]
    dest = work / path.replace("/", "_")
    fetch_checked(f"dists/{SUITE}/{path}", sha256, size, dest)
    return lzma.decompress(dest.read_bytes()).decode()


def compare_pins(found, pins):
    """(conflicts, unpinned) between {asset: sha256} and the checked-in pins."""
    conflicts = [f"{name}: {sha256}, ci_toolchain_sha256sums.txt says {pins[name]}"
                 for name, sha256 in sorted(found.items()) if name in pins and pins[name] != sha256]
    unpinned = {name: sha256 for name, sha256 in found.items() if name not in pins}
    return conflicts, unpinned


def cmd_fetch(out):
    out.mkdir(parents=True, exist_ok=True)
    entries = manifest_entries(MANIFEST.read_text())
    pins = parse_sums(PINS.read_text()) if PINS.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        hashes = release_hashes(verified_inrelease(work, signing_key(work)))
        files = resolve(entries, index(work, hashes, f"main/binary-{ARCH}/Packages.xz"),
                        index(work, hashes, "main/source/Sources.xz"))
    found = {asset_name(name): sha256 for name, (_, sha256, _) in files.items()}
    conflicts, unpinned = compare_pins(found, pins)
    if conflicts:
        raise SystemExit("The PPA's signed index and ci_toolchain_sha256sums.txt disagree; "
                         "nothing was downloaded:\n  " + "\n  ".join(conflicts))
    for name, (path, sha256, size) in sorted(files.items()):
        print(f"{name} ({size} bytes)", flush=True)
        fetch_checked(path, sha256, size, out / asset_name(name))
    (out / SUMS).write_text(format_sums(found))
    (out / "UNPINNED").write_text(format_sums(unpinned))
    print(f"{len(files)} files verified against the PPA's signed index"
          + (f"; {len(unpinned)} not in ci_toolchain_sha256sums.txt yet" if unpinned else ""))


def gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def release_assets(repo, tag):
    """{name: asset} of the release, or None when there is no such release."""
    result = gh("api", f"repos/{repo}/releases/tags/{tag}", check=False)
    if result.returncode != 0:
        if "Not Found" in result.stdout + result.stderr:
            return None, None
        raise SystemExit(f"gh api failed: {result.stderr.strip()}")
    release = json.loads(result.stdout)
    return release, {asset["name"]: asset for asset in release.get("assets", [])}


def asset_sha256(repo, tag, asset, work):
    """An asset's sha256: GitHub's digest when it has one, else the downloaded file's."""
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest[len("sha256:"):]
    gh("release", "download", tag, "--repo", repo, "--pattern", asset["name"], "--dir", str(work),
       "--clobber")
    return sha256_file(work / asset["name"])


def cmd_status(repo, tag):
    """0 when the release holds every pinned file with the pinned bytes and every manifest package."""
    pins = parse_sums(PINS.read_text()) if PINS.exists() else {}
    debs = [asset_name(f"{p}_{v}_{ARCH}.deb") for p, v in manifest_entries(MANIFEST.read_text())]
    lacking = [name for name in debs if name not in pins]
    if lacking:
        print(f"ci_toolchain_sha256sums.txt has no hash for {', '.join(lacking)}")
        return INCOMPLETE
    _, assets = release_assets(repo, tag)
    if assets is None:
        print(f"{repo} has no release {tag} yet")
        return INCOMPLETE
    with tempfile.TemporaryDirectory() as tmp:
        for name, sha256 in sorted(pins.items()):
            if name not in assets:
                print(f"The release lacks {name}")
                return INCOMPLETE
            actual = asset_sha256(repo, tag, assets[name], Path(tmp))
            if actual != sha256:
                raise SystemExit(f"The release's {name} has sha256 {actual}, "
                                 f"ci_toolchain_sha256sums.txt says {sha256}")
    print(f"The release holds all {len(pins)} pinned files")
    return 0


def cmd_upload(out, repo, tag, target):
    found = parse_sums((out / SUMS).read_text())
    for name, sha256 in found.items():
        if sha256_file(out / name) != sha256:
            raise SystemExit(f"{out / name} changed since fetch")
    release, assets = release_assets(repo, tag)
    if release is None:
        notes = out.parent / "toolchain-release-notes.md"
        notes.write_text(NOTES)
        args = ["release", "create", tag, "--repo", repo, "--prerelease", "--latest=false",
                "--title", "CI toolchain: GCC 14 for Ubuntu 22.04 (not a game release)",
                "--notes-file", str(notes)]
        if target:
            args += ["--target", target]
        gh(*args)
        release, assets = release_assets(repo, tag)
    if not release.get("prerelease"):
        raise SystemExit(f"{tag} is not a pre-release; it must never be the latest release")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        conflicts, new = [], []
        for name, sha256 in sorted(found.items()):
            if name not in assets:
                new.append(name)
                continue
            actual = asset_sha256(repo, tag, assets[name], work)
            if actual != sha256:
                conflicts.append(f"{name}: the release has {actual}, the PPA's copy is {sha256}")
        # The release's SHA256SUMS lists every file it holds, older package versions too.
        listed = {}
        if SUMS in assets:
            gh("release", "download", tag, "--repo", repo, "--pattern", SUMS, "--dir", str(work),
               "--clobber")
            listed = parse_sums((work / SUMS).read_text())
        conflicts += [f"{SUMS}: {name} is listed as {listed[name]}, the PPA's copy is {sha256}"
                      for name, sha256 in sorted(found.items())
                      if name in listed and listed[name] != sha256]
        if conflicts:
            raise SystemExit(f"{repo} {tag} already holds different bytes under these names; "
                             "nothing was uploaded:\n  " + "\n  ".join(conflicts))
        for name in new:
            print(f"Uploading {name}", flush=True)
            gh("release", "upload", tag, str(out / name), "--repo", repo)
        merged = dict(listed, **found)
        if merged != listed or SUMS not in assets:
            sums = work / "upload" / SUMS
            sums.parent.mkdir()
            sums.write_text(format_sums(merged))
            print(f"{SUMS}: {len(merged) - len(listed)} new entries")
            gh("release", "upload", tag, str(sums), "--repo", repo, "--clobber")
    _, assets = release_assets(repo, tag)
    for name in new:
        if name not in assets:
            raise SystemExit(f"{name} is not in the release after the upload (renamed by GitHub?)")
        digest = assets[name].get("digest") or ""
        if digest.startswith("sha256:") and digest != "sha256:" + found[name]:
            raise SystemExit(f"{name}: GitHub reports {digest} after the upload")
    print(f"{len(new)} files added, {len(found) - len(new)} were already there")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("repo")
    p.add_argument("tag")
    p = sub.add_parser("fetch")
    p.add_argument("out", type=Path)
    p = sub.add_parser("upload")
    p.add_argument("out", type=Path)
    p.add_argument("repo")
    p.add_argument("tag")
    p.add_argument("--target", default=os.environ.get("GITHUB_SHA", ""))
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return cmd_status(a.repo, a.tag)
    if a.cmd == "fetch":
        cmd_fetch(a.out)
    else:
        cmd_upload(a.out, a.repo, a.tag, a.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
