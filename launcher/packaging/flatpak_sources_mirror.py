"""CI helper (flatpak-sources-mirror.yml): keep the project's GitHub copy of every file the Flatpak
manifest downloads, so the release workflow's flatpak job doesn't depend on any one outside host.

  flatpak_sources_mirror.py check                 every downloaded source of the manifest pins a
                                                  sha256 and names its copy in the mirror release as
                                                  its mirror-urls entry (launcher tests run this)
  flatpak_sources_mirror.py status REPO TAG       exit 0 when the release already holds every
                                                  source with its pinned bytes, 10 when it lacks
                                                  some, 1 when one differs
  flatpak_sources_mirror.py fetch OUT             download every source from its own url and check
                                                  it against the manifest's sha256
  flatpak_sources_mirror.py upload OUT REPO TAG   add OUT's files to the release REPO/TAG

flatpak-builder downloads a source from its url and, only when that fails (a host that is down or
times out, or bytes that don't match the sha256), from its mirror-urls in order. Either way it
checks the manifest's sha256, so the copy can't change what is built.

The copies live in the pre-release ci-flatpak-sources (never the latest release), one file per
source under the file name of its url. Files are only ever added: a file the release already holds
with other bytes stops the run and is named in its log. A source the manifest no longer lists stays
in the release, for the older commits that still name it.
"""
import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ci_toolchain_mirror as common  # noqa: E402  (same folder: gh, release lookups, sha256)

MANIFEST = HERE.parent.parent / "flatpak" / "io.github.dyotson.ForzaHorizon1ReImagined.yml"
REPO = "FH1-ReImagined/ForzaHorizon1-ReImagined"
TAG = "ci-flatpak-sources"
MIRROR_BASE = f"https://github.com/{REPO}/releases/download/{TAG}/"
SUMS = common.SUMS
INCOMPLETE = common.INCOMPLETE
NOTES = """\
Unchanged copies of the files the Linux Flatpak's manifest \
(flatpak/io.github.dyotson.ForzaHorizon1ReImagined.yml) downloads while it is built: MIT Kerberos's \
source tarball and the Python wheels of PySide6, shiboken6, PySDL2, pysdl2-dll and certifi. \
The project's CI builds the Flatpak from their own hosts and uses these copies only when a host can't \
be reached. This is not a release of the game or the launcher.

Every file was downloaded from the url the manifest names and checked against the sha256 it pins \
before it was added; SHA256SUMS lists them all. flatpak-builder checks the same sha256 again when it \
uses a copy.

Licenses: each file carries its own. MIT Kerberos is under the MIT license (NOTICE in the tarball); \
PySide6 and shiboken6 (Qt for Python, with Qt 6 inside) are under the GNU Lesser General Public \
License version 3, and the Qt Project publishes their source at https://download.qt.io/; PySDL2 is \
public domain (CC0); pysdl2-dll carries SDL2 under the zlib license; certifi is under the Mozilla \
Public License 2.0.
"""


def parse_sources(text):
    """[{"type", "url", "sha256", "mirror-urls", "line"}] for each source of the manifest that has
    a url. Reads the manifest's own style of YAML (block lists, "key: value", flow lists in []).

    Every item of every "sources:" list is a source, whatever key it starts with. A "url:" line
    anywhere in the file that isn't a source's own url stops the run, so a source the parser
    can't read is never skipped without a word."""
    sources, current, list_key = [], None, None
    sources_indent = item_indent = key_indent = None
    own_url_lines, nested_lines = set(), set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if sources_indent is not None and (
                indent < sources_indent or (indent == sources_indent and not stripped.startswith("- "))):
            sources_indent = item_indent = key_indent = current = list_key = None
        if sources_indent is None:
            if re.fullmatch(r"(- +)?sources:", stripped):
                sources_indent = indent + (len(stripped) - len(stripped.lstrip("- ")))
            continue
        if stripped.startswith("- ") and (item_indent is None or indent == item_indent):
            item_indent, list_key = indent, None
            body = stripped[2:].strip()
            key_indent = indent + (len(stripped) - len(body))
            current = None
            if body.startswith("{"):
                raise SystemExit(f"line {number}: write the source as a block mapping, one key a line")
            if not re.fullmatch(r"[A-Za-z0-9_-]+:.*", body):
                continue  # a string item: a separate file of sources, which this check can't see into
            current = {"line": number, "mirror-urls": []}
            sources.append(current)
            stripped, indent = body, key_indent
        if current is None:
            continue
        if list_key and (indent > key_indent or (indent == key_indent and stripped.startswith("- "))):
            # a list item of the key above (mirror-urls), or a line of a nested mapping
            # (x-checker-data and the like) that this check doesn't need
            nested_lines.add(number)
            if list_key and stripped.startswith("- "):
                current[list_key].append(stripped[2:].strip().strip("'\""))
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+): *(.*)", stripped)
        if not match or indent != key_indent:
            raise SystemExit(f"line {number}: can't read this line of a source")
        key, value = match.group(1), match.group(2).strip()
        list_key = None
        if key == "mirror-urls" and value and not (value.startswith("[") and value.endswith("]")):
            raise SystemExit(f"line {number}: mirror-urls must be a list")
        if key == "url":
            if not value:
                raise SystemExit(f"line {number}: write the url on the same line as url:")
            own_url_lines.add(number)
        if value.startswith("[") and value.endswith("]"):
            current[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            current[key] = value.strip("'\"")
        else:
            current[key], list_key = [], key
    for number, raw in enumerate(text.splitlines(), 1):
        if (re.match(r" *(- +)?url:", raw) and number not in own_url_lines
                and number not in nested_lines):
            raise SystemExit(f"line {number}: a url that isn't read as a source's own url")
    return [source for source in sources if source.get("url")]


def file_name(url):
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith("."):
        raise SystemExit(f"{url}: no safe release file name for it")
    return name


def mirror_url(url):
    return MIRROR_BASE + file_name(url)


def problems(sources):
    """What check reports: a source without a pinned sha256 or without its mirror copy's url."""
    found, names = [], {}
    for source in sources:
        where = f"line {source['line']} ({source['url']})"
        if source.get("type") not in ("archive", "file"):
            found.append(f"{where}: a {source.get('type', 'untyped')} source can't use mirror-urls; "
                         "use an archive or file source")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
            found.append(f"{where}: no sha256 pinned")
        try:
            name = file_name(source["url"])
        except SystemExit as e:
            found.append(str(e))
            continue
        other = names.setdefault(name, source)
        if other is not source and other.get("sha256") != source.get("sha256"):
            found.append(f"{where}: another source with other bytes has the same file name {name}")
        if source["mirror-urls"] != [mirror_url(source["url"])]:
            found.append(f"{where}: needs\n        mirror-urls:\n          - {mirror_url(source['url'])}")
    if not sources:
        found.append("the manifest has no downloaded sources (the parser didn't find them)")
    return found


def pinned(sources):
    """{release file name: sha256} for the manifest's sources."""
    return {file_name(source["url"]): source["sha256"] for source in sources}


def cmd_check(manifest):
    sources = parse_sources(manifest.read_text(encoding="utf-8"))
    found = problems(sources)
    if found:
        print(f"{manifest.name}:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    for source in sources:
        print(f"{file_name(source['url'])}  {urlsplit(source['url']).hostname}")
    print(f"{len(sources)} downloaded sources, each with its sha256 and its copy in {TAG}")
    return 0


def cmd_status(repo, tag, manifest):
    pins = pinned(parse_sources(manifest.read_text(encoding="utf-8")))
    _, assets = common.release_assets(repo, tag)
    if assets is None:
        print(f"{repo} has no release {tag} yet")
        return INCOMPLETE
    lacking = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, sha256 in sorted(pins.items()):
            if name not in assets:
                lacking.append(name)
                continue
            actual = common.asset_sha256(repo, tag, assets[name], Path(tmp))
            if actual != sha256:
                raise SystemExit(f"The release's {name} has sha256 {actual}, the manifest pins {sha256}")
    if lacking:
        print("The release lacks " + ", ".join(lacking))
        return INCOMPLETE
    print(f"The release holds all {len(pins)} sources of the manifest")
    return 0


def cmd_fetch(out, manifest):
    out.mkdir(parents=True, exist_ok=True)
    sources = parse_sources(manifest.read_text(encoding="utf-8"))
    found = problems(sources)
    if found:
        raise SystemExit("Fix the manifest first (flatpak_sources_mirror.py check):\n  " + "\n  ".join(found))
    for name, url, sha256 in sorted((file_name(s["url"]), s["url"], s["sha256"]) for s in sources):
        dest = out / name
        if dest.exists() and common.sha256_file(dest) == sha256:
            continue
        print(f"{url}", flush=True)
        common.download(url, dest)
        actual = common.sha256_file(dest)
        if actual != sha256:
            dest.unlink()
            raise SystemExit(f"{url}: sha256 {actual}, the manifest pins {sha256}; nothing is uploaded")
    (out / SUMS).write_text(common.format_sums(pinned(sources)))
    print(f"{len(sources)} files checked against the manifest's sha256")


def cmd_upload(out, repo, tag, target):
    found = common.parse_sums((out / SUMS).read_text())
    for name, sha256 in found.items():
        if common.sha256_file(out / name) != sha256:
            raise SystemExit(f"{out / name} changed since fetch")
    release, assets = common.release_assets(repo, tag)
    if release is None:
        notes = out.parent / "flatpak-sources-release-notes.md"
        notes.write_text(NOTES)
        args = ["release", "create", tag, "--repo", repo, "--prerelease", "--latest=false",
                "--title", "CI: the Flatpak's downloaded sources (not a game release)",
                "--notes-file", str(notes)]
        if target:
            args += ["--target", target]
        common.gh(*args)
        release, assets = common.release_assets(repo, tag)
    if not release.get("prerelease"):
        raise SystemExit(f"{tag} is not a pre-release; it must never be the latest release")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        conflicts, new = [], []
        for name, sha256 in sorted(found.items()):
            if name not in assets:
                new.append(name)
                continue
            actual = common.asset_sha256(repo, tag, assets[name], work)
            if actual != sha256:
                conflicts.append(f"{name}: the release has {actual}, the manifest pins {sha256}")
        # The release's SHA256SUMS lists every file it holds, the ones older manifests named too.
        listed = {}
        if SUMS in assets:
            common.gh("release", "download", tag, "--repo", repo, "--pattern", SUMS,
                      "--dir", str(work), "--clobber")
            listed = common.parse_sums((work / SUMS).read_text())
        conflicts += [f"{SUMS}: {name} is listed as {listed[name]}, the manifest pins {sha256}"
                      for name, sha256 in sorted(found.items())
                      if name in listed and listed[name] != sha256]
        if conflicts:
            raise SystemExit(f"{repo} {tag} already holds different bytes under these names; "
                             "nothing was uploaded:\n  " + "\n  ".join(conflicts))
        for name in new:
            print(f"Uploading {name}", flush=True)
            common.gh("release", "upload", tag, str(out / name), "--repo", repo)
        merged = dict(listed, **found)
        if merged != listed or SUMS not in assets:
            sums = work / "upload" / SUMS
            sums.parent.mkdir()
            sums.write_text(common.format_sums(merged))
            common.gh("release", "upload", tag, str(sums), "--repo", repo, "--clobber")
    _, assets = common.release_assets(repo, tag)
    for name in new:
        if name not in assets:
            raise SystemExit(f"{name} is not in the release after the upload (renamed by GitHub?)")
        digest = assets[name].get("digest") or ""
        if digest.startswith("sha256:") and digest != "sha256:" + found[name]:
            raise SystemExit(f"{name}: GitHub reports {digest} after the upload")
    print(f"{len(new)} files added, {len(found) - len(new)} were already there")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
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
    if a.cmd == "check":
        return cmd_check(a.manifest)
    if a.cmd == "status":
        return cmd_status(a.repo, a.tag, a.manifest)
    if a.cmd == "fetch":
        cmd_fetch(a.out, a.manifest)
    else:
        cmd_upload(a.out, a.repo, a.tag, a.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
