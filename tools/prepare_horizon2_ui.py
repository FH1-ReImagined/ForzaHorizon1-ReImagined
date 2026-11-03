#!/usr/bin/env python3
"""Prepare private FH2 Xbox 360 code and UI assets for the FH1 menu port.

The input GOD package is read only. All extracted media and translated game
code go into an ignored output directory. This does not install a game mod.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import xcontent_extract

TITLE_ID = 0x4D530AA4
# Layouts and function boundaries are verified only for this retail executable.
EXECUTABLE_SHA256 = "516b04d7c69313cc581062e44030af3636f689e7f09eed1a709e4319f031feb7"
MEMBERS = ("default.xex", "media/ui.zip", "media/ui/textures.zip", "media/ui/fonts.zip",
           "media/stringtables/en.zip")


def read_entry(package, entry):
    data = bytearray()
    for fragment, offset, length in entry.runs:
        stream = package.files[fragment]
        stream.seek(offset)
        chunk = stream.read(min(length, entry.size - len(data)))
        data.extend(chunk)
        if len(data) == entry.size:
            break
    if len(data) != entry.size:
        raise ValueError(f"Short read: {entry.path}")
    return bytes(data)


def prepare(package_path, output):
    package_path, output = Path(package_path).resolve(), Path(output).resolve()
    # A GOD package consists of this file plus its sibling .data directory.
    # Keep output outside that input directory so extraction cannot touch either.
    if output == package_path.parent or package_path.parent in output.parents:
        raise ValueError("Output must be outside the GOD package directory")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}; choose a new directory")
    package = xcontent_extract.open_package(str(package_path))
    try:
        if package.hdr.title_id != TITLE_ID:
            raise ValueError("Expected Forza Horizon 2 for Xbox 360 (Title ID 4D530AA4)")
        entries = {e.path.lower(): e for e in package.entries() if not e.is_dir}
        missing = set(MEMBERS) - entries.keys()
        if missing:
            raise ValueError(f"GOD package is missing: {', '.join(sorted(missing))}")
        executable = read_entry(package, entries["default.xex"])
        digest = hashlib.sha256(executable).hexdigest()
        if digest != EXECUTABLE_SHA256:
            raise ValueError(f"Unverified FH2 executable ({digest}); do not apply these address hints")
        output.mkdir(parents=True)
        provenance = {"title_id": f"{TITLE_ID:08X}", "media_id": f"{package.hdr.media_id:08X}",
                      "source": str(package_path), "files": {}}
        for member in MEMBERS:
            entry = entries[member]
            # Output paths come from our fixed allowlist, never package filenames.
            destination = output / member
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = executable if member == "default.xex" else read_entry(package, entry)
            destination.write_bytes(data)
            provenance["files"][member] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        shutil.copyfile(Path(__file__).parent / "horizon2_ui/tail_targets.toml", output / "tail_targets.toml")
        (output / "forza_manifest.toml").write_text(
            '[project]\nname = "horizon2_ui"\nsdk_version = "0.10.0"\ngame_root = "."\n\n'
            '[entrypoint]\nfile_path = "default.xex"\nout_directory_path = "generated"\n'
            'includes = ["tail_targets.toml"]\n', encoding="utf-8")
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        return provenance
    finally:
        for stream in package.files:
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("god", type=Path, help="GOD header file, alongside its .data directory")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "extracted/horizon2-ui")
    parser.add_argument("--codegen", type=Path, help="ReXGlue executable; optionally generate private C++ too")
    args = parser.parse_args()
    try:
        provenance = prepare(args.god, args.output)
        print(f"Prepared FH2 {provenance['title_id']} in {args.output.resolve()}")
        if args.codegen:
            subprocess.run([str(args.codegen.resolve()), "codegen",
                            str(args.output.resolve() / "forza_manifest.toml")], check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
