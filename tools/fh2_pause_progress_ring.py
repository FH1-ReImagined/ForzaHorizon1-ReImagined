#!/usr/bin/env python3
"""Move the native RoadtripProgress component to the earlier shared grid group.

Only private scene copies are changed. Native ring geometry, images, object
identifiers and PROGRESS animation remain intact for CProgressBar to seek.
"""
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

SCENE = "ui/scenes/ui4/925_pause_menu"
NODE_PATH = "parent.Layer.Grid_Menu.Group.Grid_Menu_01.Group.RoadtripProgress"
BGF_PARENT = 0xAB
BGF_RING = 0x180
BSG_PARENT = 87
BSG_RING = 182
STATIC_ENDTIME = 0x7FFFFFFF
OLD_SLIDE_TIMING = struct.pack(">IBIIIII", BGF_RING, 1, 2,
                               0x083BF5F8, 4988, 0x0CFDD4C0, 4000)


def unique(data, marker):
    if data.count(marker) != 1:
        raise ValueError("Unexpected native progress-ring scene record")
    return data.index(marker)


def patch_bgf(data):
    output = bytearray(data)
    # Parent must already exist when the sequential native loader links it.
    # A forward reference to Button4 (0x23B) dropped this entire 20-node tree.
    if BGF_PARENT >= BGF_RING:
        raise ValueError("Progress ring requires an earlier serialized parent")
    unique(data, struct.pack(">III", 0xCC12FB41, 0xA3, 0xF1) + b"\x01\x01")
    # RoadtripProgress node0x255, parent InfoGraphics object0x135. The shared
    # Grid01 Group object0xAB avoids MyCars' visibility and focus ancestry.
    marker = struct.pack(">III", 0x804B9B4E, 0x135, 0x255) + b"\x07\x05\x02"
    position = unique(data, marker) + 4
    struct.pack_into(">I", output, position, BGF_PARENT)
    # Old Infographics_Set1 Slide1 explicitly delays this object until 4000ms.
    # Reparenting does not remove that slide override. Its lifetime is on the
    # shared grid's continuing timeline, not its own 1000ms PROGRESS timeline.
    # Make the component static for the native signed time range. Page Show
    # owns visibility; all PROGRESS curves and other overrides stay intact.
    timing = unique(data, OLD_SLIDE_TIMING)
    struct.pack_into(">I", output, timing + 13, STATIC_ENDTIME)
    struct.pack_into(">I", output, timing + 21, 0)
    return bytes(output)


def patch_bsg(data):
    output = bytearray(data)
    # The render graph uses record indices rather than BGF property IDs.
    # Ring182 is parented by Infographics149; destination shared Group is87.
    count = struct.unpack_from(">I", data, 0x24)[0] if len(data) >= 0x30 else 0
    if BSG_PARENT >= BSG_RING:
        raise ValueError("Progress ring requires an earlier render parent")
    if count <= BSG_RING or len(data) < 0x30 + (BSG_RING + 1) * 96:
        raise ValueError("Truncated native progress-ring render graph")
    target = 0x30 + BSG_PARENT * 96
    if struct.unpack_from(">III", data, target) != (86, 0xF1, 0x01000000):
        raise ValueError("Unexpected native Festival Progress destination")
    position = 0x30 + BSG_RING * 96
    if struct.unpack_from(">III", data, position) != (149, 0x255, 0x01000000):
        raise ValueError("Unexpected native RoadtripProgress ancestry")
    struct.pack_into(">I", output, position, BSG_PARENT)
    return bytes(output)


def stage_ring(overlay, output, unxmem=None):
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
            name = info.filename.lower()
            if name in (SCENE + ".bgf", SCENE + ".bsg"):
                original = decode(info, compressed_payload(archive, info), unxmem)
                payload = (patch_bgf if name.endswith(".bgf") else patch_bsg)(original)
                replacement = copy.copy(info)
                replacement.compress_type = zipfile.ZIP_STORED
                replacement.file_size, replacement.CRC = len(payload), zlib.crc32(payload)
                replacements[name] = (replacement, payload)
    if len(replacements) != 2:
        raise ValueError("Expected imported FH2 pause BGF and BSG")
    output.mkdir(parents=True)
    offsets = patch_archive(source, output / "media.zip", replacements)
    for name in EXECUTABLES:
        if (overlay / name).is_file():
            shutil.copyfile(overlay / name, output / name)
    previous = overlay / "scene-provenance.json"
    report = json.loads(previous.read_text()) if previous.exists() else {}
    report["fh2_native_progress_ring"] = {
        "source_archive_sha256": digest(source), "node_path": NODE_PATH,
        "node_id": "0x255", "controller_type": "PROGRESS_BAR",
        "bgf_parent": hex(BGF_PARENT), "bsg_parent": BSG_PARENT,
        "old_slide_override": {"endtime": STATIC_ENDTIME, "timeoffset": 0},
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
    stage_ring(args.overlay, args.output, args.unxmem)
    print(f"Staged native progress ring in {args.output}")
