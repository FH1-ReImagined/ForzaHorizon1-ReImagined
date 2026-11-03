#!/usr/bin/env python3
"""dlc_manifest.py OUT.json PACKAGE FOLDER [PACKAGE FOLDER ...]: the known-good files of DLC packages.

A maintainer tool. For each STFS package file and the folder it was extracted to (a Xenia
content folder or the launcher's), every 4 KB block of every file in the folder is checked
against the SHA-1 that the package's own hash table holds for it (the last block of a file
with the package's stored padding, as STFS hashes it). A file whose blocks all match is
intact, whatever the package copy's own blocks hold: a damaged package copy (#86, #422)
can still vouch for a good folder. OUT.json (launcher/forza_launcher/data/dlc-4D5309C9.json,
read by forza_launcher/dlccheck.py) gets, per package name, every file's size and SHA-1;
a package with a file that fails, or is missing, is left out and reported. Only hashes and
paths are written, no package content. Packages already in OUT.json are kept unless given
again, and so is its hand-written `known_damage` (regions that copies going around have wrong,
with the SHA-1 of the right bytes and the `ZIPREAD` CRC-32 of a bad stream; docs/dlc.md).
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xcontent_extract  # noqa: E402

BLOCK = 0x1000


def verify(pkg, entry, path):
    """(size, sha1 hex) of the folder's file if every block matches the package's table, else a
    reason string."""
    try:
        f = open(path, "rb")
    except OSError as e:
        return f"cannot read: {e}"
    with f:
        if os.fstat(f.fileno()).st_size != entry.size:
            return f"size {os.fstat(f.fileno()).st_size}, the package says {entry.size}"
        whole = hashlib.sha1()
        bad = []
        for i, b in enumerate(entry.blocks):
            data = f.read(BLOCK)
            whole.update(data)
            stored = data
            if len(data) < BLOCK:
                # STFS hashes the stored block with its padding: take the padding from the package.
                pf = pkg.files[0]
                pf.seek(pkg.block_to_offset(b) + len(data))
                stored = data + pf.read(BLOCK - len(data))
            if hashlib.sha1(stored).digest() != pkg.block_sha1(b):
                bad.append(i)
        if bad:
            return f"{len(bad)} block(s) do not match the package's hashes (first {bad[0]})"
        return entry.size, whole.hexdigest()


def manifest_for(package, folder):
    pkg = xcontent_extract.open_package(package)
    if not isinstance(pkg, xcontent_extract.Stfs):
        raise SystemExit(f"{package}: not an STFS package")
    files, problems = {}, []
    for e in pkg.entries():
        if e.is_dir:
            continue
        r = verify(pkg, e, os.path.join(folder, *e.path.split("/")))
        if isinstance(r, str):
            problems.append(f"{e.path}: {r}")
        else:
            files[e.path] = list(r)
    for f in pkg.files:
        f.close()
    return files, problems


def main(argv):
    if len(argv) < 4 or len(argv) % 2:
        raise SystemExit(__doc__)
    out = argv[1]
    data = {"packages": {}}
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
    ok = True
    for package, folder in zip(argv[2::2], argv[3::2]):
        name = os.path.basename(package).upper()
        files, problems = manifest_for(package, folder)
        if problems:
            ok = False
            print(f"{name}: left out, {len(problems)} file(s) fail:", *problems[:10], sep="\n  ")
            continue
        data["packages"][name] = dict(sorted(files.items()))
        print(f"{name}: {len(files)} files verified")
    write(out, data)
    return 0 if ok else 1


def write(out, data):
    """One file per line, so a change shows as a readable diff."""
    lines = []
    for key in sorted(data):
        if key != "packages":
            lines.append(f"{json.dumps(key)}:{json.dumps(data[key], sort_keys=True)}")
    pkgs = []
    for name, files in sorted(data["packages"].items()):
        rows = ",\n".join(f"{json.dumps(p)}:{json.dumps(v)}" for p, v in sorted(files.items()))
        pkgs.append(f"{json.dumps(name)}:{{\n{rows}}}")
    lines.append('"packages":{\n' + ",\n".join(pkgs) + "}")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("{\n" + ",\n".join(lines) + "}\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
