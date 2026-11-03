#!/usr/bin/env python3
"""Install DLC packages (STFS marketplace content) where the runtime looks for them.

Usage: install_dlc.py [--user-data DIR] PACKAGE_OR_DIR [...]

Also takes DLC that is already extracted, in Xenia's layout: a Xenia `content` folder, its
0000000000000000, 4D5309C9 or 00000002 folder (a folder of package folders named like the package
files), alone or mixed with package files in one folder. Each package folder is copied as it is
(its name kept) with its header from ../Headers/00000002/<name>.header when present (license mask
set to all bits), else a generated one named from a table of known packages (or "DLC <name>").
Folders that do not look like DLC (no files inside, a name that is not a package name) are
skipped and reported.

Each package is extracted to DIR/0000000000000000/<title id>/00000002/<package name>/ and
gets a content header in DIR/0000000000000000/<title id>/Headers/00000002/<name>.header
(XCONTENT_AGGREGATE_DATA, 0x148 bytes, plus a license mask of 0xFFFFFFFF: all offers
unlocked). That is the layout of the ReXGlue SDK's ContentManager, the same as Xenia's
content folder. A directory argument installs every package in it. DIR defaults to
~/.local/share/forza (the game's user data root). Packages are only read.
"""
import argparse
import os
import re
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xcontent_extract  # noqa: E402

MARKETPLACE = 2
DEVICE_HDD = 1
TITLE_ID = 0x4D5309C9
HEADER_SIZE = 0x148
NAME_RE = re.compile(r"[0-9A-Fa-f]{8,42}")  # a package folder: its file name, a SHA-1 in hex + title id

# Display names of this game's packages by package name (names only), for DLC found extracted
# without its header.
KNOWN_NAMES = {
    "13E2064584A4BA7759BA105051C2199ABC5FA5554D": "Pre-Order Car Pack",
    "1E21F98F04DBD7B0C7A2274481E2A854EB2C8C414D": "Season Pass: 2006 Lamborghini Miura Concept",
    "210BB00587D95ACFD8CEDCA23B7E16AE2C59C3DB4D": "VIP Membership & Cars Pack",
    "3691DE1D9E8D54E492DF1CB7AB44527F12905EBE4D": "February Jalopnik Car Pack",
    "38776C61072464819DF6352F1615E96E812CE8734D": "LCE: Day1 DLC Pack",
    "4E78F9223A118CFBB8A25A3319890A9C68BD66E54D": "March Meguiar's Car Pack",
    "50C4BE8C00EBF08197B09C4936D1C66D394F39C84D": "2010 Ferrari 458 Italia",
    "64A1E7AA98F887E1D4017D4EA369F44F3B33CC934D": "April TopGear Car Pack",
    "6F6992766050D818245ADD408031E280FB5F4E634D": "Rally Expansion Pack",
    "74C7C406C55BBD8C6681B22D1CBFE07AA054060C4D": "2012 Aston Martin Virage",
    "8794D269B02C9C52EB95D1CC7AE365E9B06BD9414D": "2013 Ford Shelby GT500 - Rockstar Energy",
    "A5B171B88811F1785A9AFC1F956C768A97029C004D": "VIP Membership & Cars Pack",
    "AA8F507902C3D3D05E38F115C0170A4A2CC412C84D": "November Bondurant Car Pack",
    "B75175BED56077D233373795139AEB570A5CFEE24D": "October Car Pack",
    "BC6F8A24F1F38F719B9F09C002DDC1F3024AC2AB4D": "December IGN Car Pack",
    "C96D582D5F0E48E58765AC15FB950D09DA1680894D": "2011 Mercedes-Benz SLS AMG",
    "CCB95DD6E009F4F78DF207A1405A0E034CE9B7C34D": "2010 Volkswagen Golf R",
    "D4888FE9189BEF249567245E784F876794F0BDC04D": "2013 Ford Mustang Boss 302  - Pizza Hut Edition",
    "DA1027D3F195042E877B864AAD6C8B26E0B281C24D": "Honda Challenge Car Pack",
    "DFCA0F246444BEB5F96E33802B0C3D082B25003F4D": "January Recaro Car Pack",
    "ED6D95A929D78638F2E6A10466BD847875E3CC234D": "2010 Nissan 370Z",
    "EE8ECC936E550AE8C52031F6968068D2273165584D": "1000 Club Expansion Pack",
    "F7830DC6DB7073F2EF395A05A3C1227A278FC5B54D": "Season Pass",
}


def header_bytes(name, display_name, title_id):
    data = struct.pack(">II", DEVICE_HDD, MARKETPLACE)
    data += display_name.encode("utf-16-be")[:254].ljust(256, b"\0")
    data += name.encode("ascii")[:42].ljust(42, b"\0") + b"\0\0"
    data += b"\0" * 4  # alignment before the 64-bit XUID
    data += struct.pack(">QI", 0, title_id) + b"\0" * 4
    assert len(data) == 0x148
    return data + struct.pack("<I", 0xFFFFFFFF)  # the runtime reads it in host order


def install(path, user_data):
    """Extract one package. Returns [(file path, number of damaged 4 KB blocks)] of the files whose
    blocks do not match the package's own hashes ([] for an intact package or one skipped)."""
    pkg = xcontent_extract.open_package(path)
    try:
        return _install(pkg, path, user_data)
    finally:
        for f in getattr(pkg, "files", []):
            f.close()


def _install(pkg, path, user_data):
    h = pkg.hdr
    if h.content_type != MARKETPLACE:
        print(f"skipping {path}: content type 0x{h.content_type:X} is not marketplace content")
        return []
    name = os.path.basename(path)
    root = os.path.join(user_data, "0000000000000000", f"{h.title_id:08X}")
    dest = os.path.join(root, "00000002", name)
    total = 0
    damaged = []
    for entry in pkg.entries():
        bad = xcontent_extract.extract(pkg, entry, dest)
        if bad:
            damaged.append((entry.path, len(bad)))
        total += entry.size
    headers = os.path.join(root, "Headers", "00000002")
    os.makedirs(headers, exist_ok=True)
    with open(os.path.join(headers, name + ".header"), "wb") as f:
        f.write(header_bytes(name, h.display_name, h.title_id))
    print(f"{h.display_name}: {total} bytes -> {dest}")
    for p, n in damaged:
        print(f"WARNING: {name}: {p} has {n} damaged 4 KB block(s): the package file does not match its "
              f"own hashes (a bad download or copy), so that content will not work. Get the package again.")
    return damaged


def _child(p, name):
    """The child of folder p called `name`, ignoring case (a Windows copy), or None."""
    q = os.path.join(p, name)
    if os.path.isdir(q):
        return q
    try:
        for n in os.listdir(p):
            if n.lower() == name.lower() and os.path.isdir(os.path.join(p, n)):
                return os.path.join(p, n)
    except OSError:
        pass
    return None


def resolve_root(folder):
    """The folder that holds the package folders and files, given what the player picked: a Xenia
    `content` folder, its 0000000000000000 or 4D5309C9 folder, or the 00000002 folder itself."""
    p = folder
    for sub in ("0000000000000000", "4D5309C9", "00000002"):
        q = _child(p, sub)
        if q:
            p = q
    return p


def _has_files(p):
    return any(files for _r, _d, files in os.walk(p))


def header_for(name, src_header=None, display_name=None):
    """The header to write for package `name`: the one that came with it (license mask set to all
    bits) or a generated one."""
    if src_header and os.path.isfile(src_header):
        with open(src_header, "rb") as f:
            data = f.read(HEADER_SIZE)
        if len(data) == HEADER_SIZE:
            return data + struct.pack("<I", 0xFFFFFFFF)
    return header_bytes(name, display_name or KNOWN_NAMES.get(name.upper(), "DLC " + name), TITLE_ID)


def header_display_name(path):
    """The display name stored in a header file, or None."""
    try:
        with open(path, "rb") as f:
            data = f.read(HEADER_SIZE)
        if len(data) == HEADER_SIZE:
            return data[8:264].decode("utf-16-be", "ignore").split("\0")[0].strip() or None
    except OSError:
        pass
    return None


def scan_folder(folder):
    """What a DLC folder holds: (root, dirs, files, skipped). root is the resolved folder; dirs the
    extracted package folders [(path, name, display name, header path or None)]; files the
    candidate package files (not checked here); skipped [(name, reason)] the folders that do not
    look like DLC."""
    root = resolve_root(os.path.abspath(os.path.expanduser(folder)))
    if not os.path.isdir(root):
        raise FileNotFoundError(f"{root} is not a folder")
    hdir = _child(os.path.dirname(root), "Headers")
    hdir = _child(hdir, os.path.basename(root)) if hdir else None
    dirs, files, skipped = [], [], []
    for n in sorted(os.listdir(root)):
        p = os.path.join(root, n)
        if os.path.isfile(p):
            files.append(p)
        elif not os.path.isdir(p):
            continue
        elif not NAME_RE.fullmatch(n):
            skipped.append((n, "not a package name"))
        elif not _has_files(p):
            skipped.append((n, "no files inside"))
        else:
            h = os.path.join(hdir, n + ".header") if hdir else None
            h = h if h and os.path.isfile(h) else None
            dirs.append((p, n, (h and header_display_name(h)) or KNOWN_NAMES.get(n.upper(), "DLC " + n), h))
    return root, dirs, files, skipped


def folder_size(p):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(p) for f in fs)


def install_folder(src, name, src_header, user_data, copy=shutil.copy2, display_name=None):
    """Copy an extracted package folder as it is under the user data root, with its header."""
    root = os.path.join(user_data, "0000000000000000", f"{TITLE_ID:08X}")
    dest = os.path.join(root, "00000002", name)
    headers = os.path.join(root, "Headers", "00000002")
    if not (os.path.exists(dest) and os.path.samefile(src, dest)):
        part = dest + ".part"
        shutil.rmtree(part, ignore_errors=True)
        try:
            shutil.copytree(src, part, copy_function=copy)
        except BaseException:
            shutil.rmtree(part, ignore_errors=True)
            raise
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(part, dest)
    os.makedirs(headers, exist_ok=True)
    hp = os.path.join(headers, name + ".header")
    if not (src_header and os.path.exists(hp) and os.path.samefile(src_header, hp)):
        with open(hp, "wb") as f:
            f.write(header_for(name, src_header, display_name))
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packages", nargs="+")
    ap.add_argument("--user-data", default=os.path.expanduser("~/.local/share/forza"))
    a = ap.parse_args()
    for p in a.packages:
        if not os.path.isdir(p):
            install(p, a.user_data)
            continue
        _root, dirs, files, skipped = scan_folder(p)
        for f in files:
            try:
                install(f, a.user_data)
            except SystemExit as e:
                print(f"skipping {f}: {e}")
        for d, n, disp, h in dirs:
            print(f"{disp}: {folder_size(d)} bytes -> {install_folder(d, n, h, a.user_data, display_name=disp)}")
        for n, why in skipped:
            print(f"skipping {n}: {why}")


if __name__ == "__main__":
    main()
