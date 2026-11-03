"""Apply bounded, same-width FH1 font edits to the owner's FH2 scene.

No scene data is distributed. Keeping every record length unchanged preserves
the presentation's serialized indices and offsets.
"""
import copy
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile
import zlib


def patch_fbf(data):
    output = bytearray(data)
    # Font sizes and text widths come from FH1's 925_PAUSE_MENU, except the
    # right-aligned credits retain FH2's box to preserve the outer header edge.
    # BGF also supplies live font overrides; changing only this FBF schema
    # leaves the FH2 car/credit text in horizon_e when the scene loads.
    for name, old_font, new_font, size, old_width, new_width in (
            (b"TEXT_CAR", b"horizon_e", b"horizon_d", 14, 222.0, 238.0),
            (b"TEXT_CR", b"horizon_e", b"horizon_d", 14, 9.0, 9.0),
            (b"TEXT_PI", b"horizon_d", b"horizon_d", 16, 24.0, 37.0),
            (b"Next_Level", b"horizon_d", b"horizon_d", 14, 44.0, 62.0),
            (b"Points", b"horizon_d", b"horizon_d", 14, 74.0, 99.0)):
        marker = struct.pack(">I", len(name)) + name + b"\0"
        if output.count(marker) != 1:
            raise ValueError(f"Expected one FH2 header text record {name!r}")
        start = output.index(marker) + 4
        font = start + len(name) + 24
        if output[font:font + len(old_font)] != old_font:
            raise ValueError(f"Unexpected header font record {name!r}")
        if struct.unpack_from(">f", output, font + 42)[0] != old_width:
            raise ValueError(f"Unexpected header text width {name!r}")
        output[font:font + len(old_font)] = new_font
        struct.pack_into(">I", output, font + len(old_font), size)
        struct.pack_into(">f", output, font + 42, new_width)
    return bytes(output)


def patch_bgf(data):
    output = bytearray(data)
    marker = b"WRISTBAND\0"
    positions = []
    start = 0
    while (start := output.find(marker, start)) >= 0:
        positions.append(start)
        start += len(marker)
    if len(positions) != 3:
        raise ValueError("Expected three FH2 wristband contracts")
    for start in positions:
        font = start + 112
        if output[font - 4:font + 9] != b"\0\0\0\x09horizon_d":
            raise ValueError("Missing wristband number-font contract")
        output[font:font + 9] = b"horizon_b"
    # Only the PLAYER_BAR region: other FH2 menu titles intentionally retain
    # their own font. These are same-length BGF string values, so every string
    # index and byte offset remains unchanged.
    start_marker = b"\0\0\0\x0aPLAYER_BAR"
    end_marker = b"\0\0\0\x04onUp"
    if output.count(start_marker) != 1:
        raise ValueError("Expected one FH2 player-bar contract")
    start = output.index(start_marker)
    end = output.find(end_marker, start)
    if end < 0:
        raise ValueError("Missing FH2 player-bar boundary")
    region = output[start:end]
    font_marker = b"\0\0\0\x09horizon_e"
    if region.count(font_marker) != 3:
        raise ValueError("Unexpected FH2 player-bar font overrides")
    output[start:end] = region.replace(font_marker, b"\0\0\0\x09horizon_d")
    return bytes(output)


def replacement(info, payload, suffix, unxmem):
    if info.compress_type == 0:
        data = payload
    elif info.compress_type == 8:
        data = zlib.decompress(payload, -15)
    elif info.compress_type == 21:
        if not unxmem or not Path(unxmem).is_file():
            raise ValueError("FH1 header fonts require --unxmem")
        with tempfile.TemporaryDirectory(prefix="fh1-pause-header-") as folder:
            packed, unpacked = Path(folder) / "scene.xmem", Path(folder) / "scene.bin"
            packed.write_bytes(payload)
            result = subprocess.run([str(Path(unxmem).resolve()), str(packed),
                                     str(unpacked), str(info.file_size)],
                                    capture_output=True, check=False)
            if result.returncode or not unpacked.is_file():
                raise ValueError("Failed to decode private pause scene")
            data = unpacked.read_bytes()
    else:
        raise ValueError("Unsupported private scene compression")
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        raise ValueError("Private pause scene failed size/CRC validation")
    data = patch_fbf(data) if suffix == "fbf" else patch_bgf(data)
    result = copy.copy(info)
    result.compress_type = zipfile.ZIP_STORED
    result.file_size, result.CRC = len(data), zlib.crc32(data)
    return result, data
