#!/usr/bin/env python3
"""Flatten only native tile focus depth in a new private pause overlay.

The original focus/blur curves move tile meshes and labels 20 units toward
the perspective camera. Their scale remains one. Preserve focus events,
opacity, key times and scene IDs; replace only these curves' depth values.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib

from stage_horizon2_pause_scene import compressed_payload, digest, patch_archive
from fh1_pause_tile_theme import EXECUTABLES

# Serialized property-object IDs in Grid_Menu_01 and Grid_Menu_03 only.
# Each has exactly one 100 ms focus and one inverse blur curve. The baseline
# preserves native front/back ordering between paper, text and underline.
DEPTHS = {
    0xAE: 0, 0xB1: -1, 0xB4: -2, 0xB6: -2, 0xBC: 1, 0xD5: 1,
    0xDA: -1, 0xE3: 0, 0xFC: 0, 0x10C: -1, 0x115: 0, 0x12E: 0,
    0x1B3: -1, 0x1BC: 0, 0x1D5: 0, 0x23D: -1, 0x246: 0, 0x25F: 0,
    0x298: -1, 0x2A1: 0, 0x2BA: 0, 0x2CB: -1, 0x2D4: 0, 0x2ED: 0,
    0x473: -1, 0x47C: 1, 0x495: 0, 0x4CE: 0, 0x4D1: -1, 0x4D4: -2,
    0x4DB: 0, 0x4F4: -2, 0x4F5: 1, 0x4FA: 0, 0x4FD: -1, 0x500: -2,
    0x507: 0, 0x520: -2, 0x521: 1, 0x526: -1, 0x52F: 0, 0x54E: 0,
    0x5C3: -1, 0x5CC: 0, 0x5EB: 0,
}
Z_PROPERTY = 0x027E238B
SCENE = "ui/scenes/ui4/925_pause_menu.bgf"


def patch_bgf(data):
    output = bytearray(data)
    for target, depth in DEPTHS.items():
        marker = struct.pack(">III", target, Z_PROPERTY, 0x01000002)
        starts = []
        offset = 0
        while (offset := data.find(marker, offset)) >= 0:
            starts.append(offset + len(marker))
            offset += len(marker)
        if len(starts) != 2:
            raise ValueError(f"Expected one focus/blur pair for tile object {target:#x}")
        directions = []
        for start in starts:
            if start + 48 > len(data):
                raise ValueError("Truncated native tile depth curve")
            keys = [struct.unpack_from(">IfIfIf", data, start + k * 24) for k in range(2)]
            endpoints = (keys[0][1], keys[1][1])
            if (tuple(k[0] for k in keys) != (0, 100) or
                    any(k[2] or k[4] for k in keys) or
                    not all(math.isfinite(k[f]) for k in keys for f in (1, 3, 5)) or
                    endpoints not in ((depth, depth - 20), (depth - 20, depth))):
                raise ValueError(f"Unexpected tile depth keys for object {target:#x}")
            directions.append(endpoints)
            for k in range(2):
                for field in (4, 12, 20):
                    struct.pack_into(">f", output, start + k * 24 + field, depth)
        if len(set(directions)) != 2:
            raise ValueError(f"Missing inverse tile depth curve for object {target:#x}")
    return bytes(output)


def decode(info, payload, unxmem):
    if info.compress_type == zipfile.ZIP_STORED:
        result = payload
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        result = zlib.decompress(payload, -15)
    elif info.compress_type == 21:
        if not unxmem or not Path(unxmem).is_file():
            raise ValueError("XMem scene requires --unxmem")
        with tempfile.TemporaryDirectory(prefix="fh2-tile-depth-") as folder:
            source, output = Path(folder) / "scene.xmem", Path(folder) / "scene.bgf"
            source.write_bytes(payload)
            subprocess.run([str(Path(unxmem).resolve()), str(source), str(output),
                            str(info.file_size)], check=True, capture_output=True)
            result = output.read_bytes()
    else:
        raise ValueError("Unsupported private scene compression")
    if len(result) != info.file_size or zlib.crc32(result) != info.CRC:
        raise ValueError("Private scene failed size/CRC validation")
    return result


def stage_depth(overlay, output, unxmem=None):
    overlay, output = Path(overlay).resolve(), Path(output).resolve()
    if output == overlay or overlay in output.parents:
        raise ValueError("Output must be outside the input overlay")
    if output.exists():
        raise FileExistsError(output)
    if not (overlay / "default.xex").is_file():
        raise ValueError("Expected private pause overlay with default.xex")
    source = overlay / "media.zip"
    replacements = {}
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.filename.lower() == SCENE:
                payload = patch_bgf(decode(info, compressed_payload(archive, info), unxmem))
                replacement = copy.copy(info)
                replacement.compress_type = zipfile.ZIP_STORED
                replacement.file_size, replacement.CRC = len(payload), zlib.crc32(payload)
                replacements[info.filename.lower()] = (replacement, payload)
    if len(replacements) != 1:
        raise ValueError("Expected exactly one imported FH2 pause BGF")
    output.mkdir(parents=True)
    offsets = patch_archive(source, output / "media.zip", replacements)
    for name in EXECUTABLES:
        if (overlay / name).is_file():
            shutil.copyfile(overlay / name, output / name)
    previous = overlay / "scene-provenance.json"
    report = json.loads(previous.read_text()) if previous.exists() else {}
    report["fh2_tile_focus_depth"] = {
        "source_archive_sha256": digest(source),
        "target_objects": len(DEPTHS), "curves": len(DEPTHS) * 2,
        "description": "Preserved native focus with constant authored tile depth",
    }
    report.setdefault("payload_offsets", {}).update(offsets)
    report["output_archive_sha256"] = digest(output / "media.zip")
    (output / "scene-provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unxmem", type=Path)
    args = parser.parse_args()
    stage_depth(args.overlay, args.output, args.unxmem)
    print(f"Staged native tile focus depth in {args.output}")
