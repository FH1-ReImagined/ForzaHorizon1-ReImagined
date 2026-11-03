"""Damaged DLC that the package check cannot see (#86, #327, #422).

Install DLC checks a package file (STFS) against the SHA-1 it holds for every 4 KB block
(tools/xcontent_extract.py). An extracted package folder (Xenia's content folder) carries no such
table, so it used to be copied as it was: a Rally Expansion Pack with 12 wrong blocks in
Media/DLCZips/ColoradoDirt_pri_65.zip, a copy that goes around, then crashed Kettle Hills stage 1
halfway, with no warning anywhere.

data/dlc-4D5309C9.json has the size and SHA-1 of every file of the packages whose intact files are
known (tools/dlc_manifest.py writes it from folders that match their packages' own hash tables),
and the regions of files known to be damaged in copies that go around, with the SHA-1 of the right
bytes. Install DLC compares every file it copies from a folder with that list (`compare`, the
SHA-1 taken while copying: no second read). Play checks what is installed (`installed_problems`):
every known file there with its size, and the known damaged regions (a 48 KB read), which also
finds a damaged pack installed before this check existed. Packages not in the list are not checked.
"""
import functools
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "data" / "dlc-4D5309C9.json"
DLC_FOLDER = Path("0000000000000000") / "4D5309C9" / "00000002"


def _manifest():
    """({package: {path lower: (path, size, sha1)}}, {package: {path: [region, ...]}}); empty when
    the list is missing or unreadable (then nothing is checked)."""
    return _load(str(MANIFEST))


@functools.lru_cache(maxsize=None)
def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}, {}
    files = {name.upper(): {p.lower(): (p, int(size), sha1) for p, (size, sha1) in entries.items()}
             for name, entries in data.get("packages", {}).items()}
    damage = {name.upper(): entries for name, entries in data.get("known_damage", {}).items()}
    return files, damage


def display_name(name) -> str:
    """The pack's name (tools/install_dlc.py's table), else the package name."""
    from . import paths
    paths.ensure_tools_importable()
    try:
        import install_dlc
    except ImportError:
        return name
    return install_dlc.KNOWN_NAMES.get(name.upper(), name)


def copy_file(src, dst) -> str:
    """shutil.copy2 that also returns the SHA-1 of the bytes it copied."""
    digest = hashlib.sha1()
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            chunk = fi.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            fo.write(chunk)
    shutil.copystat(src, dst)
    return digest.hexdigest()


def _summary(paths, limit=3):
    return ", ".join(paths[:limit]) + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def compare(name, digests) -> list:
    """What is wrong with an extracted package against the known-good list, as sentences: digests
    maps every file copied (its path in the package, '/' or os.sep separated) to (size, sha1 hex).
    [] when the package is intact or not in the list."""
    files = _manifest()[0].get(name.upper())
    if not files:
        return []
    got = {p.replace(os.sep, "/").lower(): v for p, v in digests.items()}
    damaged, missing = [], []
    for key, (path, size, sha1) in files.items():
        have = got.get(key)
        if have is None:
            missing.append(path)
        elif (int(have[0]), have[1]) != (size, sha1):
            damaged.append(path)
    out = []
    if damaged:
        out.append(f"{_summary(damaged)} differ{'s' if len(damaged) == 1 else ''} from an intact copy")
    if missing:
        out.append(f"{len(missing)} file(s) are missing ({_summary(missing)})")
    return out


def _index(folder: Path):
    """{path lower: (real path, size)} of the files under folder (a copy made on Windows may differ
    in case from the package's names)."""
    out = {}
    for dirpath, _dirs, names in os.walk(folder, followlinks=True):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            out[os.path.relpath(p, folder).replace(os.sep, "/").lower()] = (p, size)
    return out


def _region_ok(path, region) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(int(region["offset"]))
            data = f.read(int(region["size"]))
    except OSError:
        return True     # unreadable now (locked, offline drive): no claim either way
    return hashlib.sha1(data).hexdigest() == region["sha1"]


def installed_problems(root) -> list:
    """[(package name, problem, effect or "")] for the packages installed under the user data root
    `root` that are in the list: files missing or of another size than the intact ones (a partial
    or damaged copy), and the regions known to be damaged in copies that go around."""
    files, damage = _manifest()
    base = Path(root) / DLC_FOLDER
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for name in names:
        expected = files.get(name.upper())
        if not expected or not (base / name).is_dir():
            continue
        have = _index(base / name)
        wrong = [path for key, (path, size, _sha1) in expected.items()
                 if have.get(key, (None, None))[1] != size]
        if wrong:
            out.append((name, f"{_summary(wrong)} {'is' if len(wrong) == 1 else 'are'} missing or "
                              "incomplete", ""))
        for path, regions in damage.get(name.upper(), {}).items():
            if path in wrong or path.lower() not in have:
                continue
            for region in regions:
                if not _region_ok(have[path.lower()][0], region):
                    out.append((name, f"{path} has the damaged bytes of a bad copy that goes around",
                                region.get("effect", "")))
                    break
    return out


_ZIPREAD = re.compile(r"ZIPREAD: stream [0-9A-Fa-f]+ \(method \d+, (\d+) of (\d+) bytes out[^\n]*the read ends short")
_ZIPREAD_CRC = re.compile(r"ZIPREAD: stream [0-9A-Fa-f]+ input at [0-9A-Fa-f]+, CRC-32 ([0-9a-f]{8})")


def short_read_summary(tail):
    """For a crash report: the game's log says a compressed file's data ran out before its end
    (`ZIPREAD`, src/hooks/compressed_stream_stall.cpp), which a damaged file does; the loader then
    goes on with a broken resource and the game crashes soon after (#86, #422). Names the pack when
    the bytes' CRC-32 is one of a known bad copy. None when the log has no such line."""
    text = "\n".join(tail)
    reads = _ZIPREAD.findall(text)
    if not reads:
        return None
    got, size = reads[-1]
    known_bad = None
    crcs = set(_ZIPREAD_CRC.findall(text))
    for name, entries in _manifest()[1].items():
        for path, regions in entries.items():
            if any(r.get("zipread_crc") in crcs for r in regions):
                known_bad = (display_name(name), path)
    lines = [f"Right before the crash the game read a compressed file whose data ends early ({got} of "
             f"{size} bytes, `ZIPREAD` in the log): a damaged game or DLC file."]
    if known_bad:
        lines.append(f"Its bytes are those of a known damaged copy of the {known_bad[0]} "
                     f"(`{known_bad[1]}`): install the pack again from an intact copy of its package file "
                     "(Setup, step 2, checks every block).")
    else:
        lines.append("Install the DLC again from its package files (Setup, step 2, checks every block) "
                     "and check the game files.")
    return "\n".join(lines)
