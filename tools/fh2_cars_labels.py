#!/usr/bin/env python3
"""Retarget authored Grid03 label anchors in a new private pause overlay."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import struct
import zipfile
import zlib

from fh1_pause_tile_theme import EXECUTABLES
from fh2_pause_tile_depth import decode
from stage_horizon2_pause_scene import compressed_payload, digest, patch_archive

SCENE = "ui/scenes/ui4/925_pause_menu.bgf"
X, Y = 0x027C230D, 0x027D234C
# Native property object, raw-name hash, parent object, FBF/render node, title.
LABELS = (
    (0x4D4, 0x1DCD6A86, 0x4CC, 0x746, True),
    (0x4F4, 0x9283A2E5, 0x4CC, 0x77A, False),
    (0x500, 0x1DCD6A86, 0x4F8, 0x78B, True),
    (0x520, 0x9283A2E5, 0x4F8, 0x7BF, False),
)


def unique(data, marker):
    if data.count(marker) != 1:
        raise ValueError("Expected one original Grid03 label record")
    return data.index(marker)


def patch_bgf(data):
    output = bytearray(data)
    for obj, name, parent, node, title in LABELS:
        marker = struct.pack(">III", name, parent, node) + bytes((4, 1)) + struct.pack(">I", 48)
        start = unique(data, marker) + len(marker)
        if start + 48 * 8 > len(data):
            raise ValueError("Truncated native label properties")
        for prop, base_value, old_value, target in (
                (X, -152.0 if title else -163.0, -163.0, -162.0),
                (Y, 70.0 if title else -73.0, 71.0 if title else -73.0,
                 38.0 if title else -38.0)):
            offsets = [start + i * 8 for i in range(48)
                       if struct.unpack_from(">I", data, start + i * 8)[0] == (prop | (3 << 27))]
            if len(offsets) != 1 or struct.unpack_from(">f", data, offsets[0] + 4)[0] != base_value:
                raise ValueError("Unexpected original label position property")
            struct.pack_into(">f", output, offsets[0] + 4, target)
            curve = struct.pack(">III", obj, prop, 0x01000002)
            positions = []
            offset = 0
            while (offset := data.find(curve, offset)) >= 0:
                positions.append(offset + len(curve))
                offset += len(curve)
            if len(positions) != 2:
                raise ValueError("Expected exactly one native focus/blur pair")
            for position in positions:
                if position + 48 > len(data):
                    raise ValueError("Truncated native label curve")
                keys = [struct.unpack_from(">IfIfIf", data, position + k * 24) for k in range(2)]
                if (tuple(k[0] for k in keys) != (0, 100) or
                        any(k[1] != old_value or k[2] or k[4] for k in keys) or
                        any(k[j] not in (old_value, -1.0) for k in keys for j in (3, 5))):
                    raise ValueError("Unexpected original native label curve")
                for k, key in enumerate(keys):
                    for index, field in ((1, 4), (3, 12), (5, 20)):
                        if key[index] == old_value:
                            struct.pack_into(">f", output, position + k * 24 + field, target)
    return bytes(output)


def stage_labels(overlay, output, unxmem=None):
    overlay, output = Path(overlay).resolve(), Path(output).resolve()
    if output == overlay or overlay in output.parents:
        raise ValueError("Output must be outside the input overlay")
    if output.exists():
        raise FileExistsError(output)
    source = overlay / "media.zip"
    if not (overlay / "default.xex").is_file():
        raise ValueError("Expected a private pause overlay")
    replacements = {}
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.filename.lower() == SCENE:
                payload = patch_bgf(decode(info, compressed_payload(archive, info), unxmem))
                replacement = copy.copy(info)
                replacement.compress_type = zipfile.ZIP_STORED
                replacement.file_size, replacement.CRC = len(payload), zlib.crc32(payload)
                replacements[SCENE] = (replacement, payload)
    if len(replacements) != 1:
        raise ValueError("Expected exactly one imported pause BGF")
    output.mkdir(parents=True)
    offsets = patch_archive(source, output / "media.zip", replacements)
    for name in EXECUTABLES:
        if (overlay / name).is_file():
            shutil.copyfile(overlay / name, output / name)
    provenance = overlay / "scene-provenance.json"
    report = json.loads(provenance.read_text(encoding="utf-8")) if provenance.exists() else {}
    report["fh2_cars_label_anchors"] = {
        "source_archive_sha256": digest(source), "labels": 4, "curves": 16,
        "title_xy": [-162, 38], "caption_xy": [-162, -38],
        "description": "Retargeted native position properties and focus/blur XY tracks; preserved times and other curves",
    }
    report.setdefault("payload_offsets", {}).update(offsets)
    report["output_archive_sha256"] = digest(output / "media.zip")
    (output / "scene-provenance.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unxmem", type=Path)
    args = parser.parse_args()
    stage_labels(args.overlay, args.output, args.unxmem)
    print(f"Staged Cars label curves in {args.output}")
