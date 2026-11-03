"""Remove the original five-second RB translation from the private scene.

Only GROUP_RB's X default and the two matching curve records are changed.
Lengths, key times, object identifiers and all other properties are preserved.
"""
import copy
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile
import zlib


def unique(data, marker):
    if data.count(marker) != 1:
        raise ValueError("Unexpected FH2 right-bumper scene record")
    return data.index(marker)


def patch_bsg(data):
    output = bytearray(data)
    # Render object 0x4A belongs to FBF node 0xD8, GROUP_RB.
    marker = struct.pack(">III", 0x4A, 0xD8, 0x01000000)
    position = unique(output, marker) + len(marker)
    if position + 4 > len(output) or struct.unpack_from(">f", output, position)[0] != -400.0:
        raise ValueError("Unexpected GROUP_RB initial X")
    struct.pack_into(">f", output, position, -0.5)
    return bytes(output)


def patch_bgf(data):
    output = bytearray(data)
    # GROUP_RB hash, property-object index and FBF node, followed by five
    # scalar properties. The initial duration remains 5000 ms; only X changes.
    marker = (struct.pack(">III", 0xE252B3F0, 0x90, 0xD8) + b"\x01\x01" +
              struct.pack(">IIII", 5, 0x083BF5F8, 5000, 0x1A7C230D))
    position = unique(output, marker) + len(marker)
    if position + 4 > len(output) or struct.unpack_from(">f", output, position)[0] != -400.0:
        raise ValueError("Unexpected GROUP_RB property X")
    struct.pack_into(">f", output, position, -0.5)

    # The curve target is one-based (0x91), followed by the X property id and
    # six cubic keys. Each key has three (time, value) pairs: point/controls.
    marker = struct.pack(">III", 0x91, 0x027C230D, 0x01000006)
    if output.count(marker) != 2:
        raise ValueError("Expected two GROUP_RB X curves")
    start = 0
    for _ in range(2):
        start = output.index(marker, start) + len(marker)
        if start + 6 * 24 > len(output):
            raise ValueError("Truncated GROUP_RB X curve")
        for key in range(6):
            offset = start + key * 24
            time, value, in_time, in_value, out_time, out_value = \
                struct.unpack_from(">IfIfIf", output, offset)
            if (time != key * 1000 or in_time != 0 or out_time != 0 or
                    not all(math.isfinite(v) for v in (value, in_value, out_value)) or
                    (key == 0 and value != -400.0) or
                    (key == 5 and value != -0.5)):
                raise ValueError("Unexpected GROUP_RB X key")
            for field in (4, 12, 20):
                struct.pack_into(">f", output, offset + field, -0.5)
        start += 6 * 24
    return bytes(output)


def replacement(info, payload, suffix, unxmem):
    if info.compress_type == 0:
        data = payload
    elif info.compress_type == 8:
        data = zlib.decompress(payload, -15)
    elif info.compress_type == 21:
        if not unxmem or not Path(unxmem).is_file():
            raise ValueError("FH2 shoulder layout requires --unxmem")
        with tempfile.TemporaryDirectory(prefix="fh2-pause-tabs-") as folder:
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
    data = {"bgf": patch_bgf, "bsg": patch_bsg}[suffix](data)
    result = copy.copy(info)
    result.compress_type = zipfile.ZIP_STORED
    result.file_size, result.CRC = len(data), zlib.crc32(data)
    return result, data
