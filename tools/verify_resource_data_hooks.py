#!/usr/bin/env python3
"""Audit #83 hook coverage against player-owned disc/TU4 XEX images, read-only.

Usage: verify_resource_data_hooks.py --disc PATH --tu4 PATH [--hints-root DIR]
Requires openssl on PATH, as tools/xex_image.py does. No image data is written.
"""
import argparse
from pathlib import Path
import struct
import tomllib

import xex_image


def unload_takes(base, image, load_step):
    """Find slot-16 unloads in vtables sharing slot-22's cacheable load step."""
    functions = set()
    offset = 0
    while (offset := image.find(struct.pack(">I", load_step), offset)) >= 0:
        if offset >= 88 and offset % 4 == 0:
            functions.add(struct.unpack_from(">I", image, offset - 24)[0])
        offset += 4
    takes = {}
    for function in functions:
        offset = function - base
        if offset < 0 or offset + 80 > len(image):
            continue
        words = struct.unpack_from(">20I", image, offset)
        # RTTI/other references to the load function can resemble vtable slots.
        # Only accept the unload's exact resource+0x40 take/release prologue.
        if 0x80630040 not in words:  # lwz r3,64(r3)
            continue
        index = words.index(0x80630040)
        if not 1 <= index <= 15:
            raise ValueError(f"Unexpected take position in {function:08X}")
        li, compare, store, branch = words[index + 1:index + 5]
        zero, saved = (li >> 21) & 31, (store >> 16) & 31
        if (zero not in (10, 11, 29, 30) or saved not in (11, 31) or
                li != (0x38000000 | (zero << 21)) or compare != 0x2B030000 or
                store != (0x90000040 | (zero << 21) | (saved << 16)) or
                words[index - 1] != (0x7C000378 | (3 << 21) | (saved << 16) | (3 << 11)) or
                (branch & 0xFFFF0000) != 0x419A0000):
            raise ValueError(f"Unexpected unload instructions in {function:08X}")
        takes[function + index * 4] = zero
    if not takes:
        raise ValueError("No supported resource unloads found; check the executable version")
    return takes


def verify(base, image, load_step, hints):
    takes = unload_takes(base, image, load_step)
    hooks = hints["midasm_hook"]
    by_address = {hook["address"]: hook for hook in hooks}
    if len(by_address) != len(hooks):
        raise ValueError("Duplicate resource hook addresses")
    for address, zero in sorted(takes.items()):
        hook = by_address.get(address)
        if (hook is None or hook["name"] != f"ForzaUnloadResourceDataR{zero}" or
                hook["registers"] != ["r3"] or hook["jump_address"] != address + 16):
            raise ValueError(f"Missing or incompatible atomic unload hook at {address:08X} (zero r{zero})")
    unload_hooks = {h["address"] for h in hooks if h["name"].startswith("ForzaUnloadResourceData")}
    if unload_hooks != takes.keys():
        raise ValueError("Unload hints include an address outside the verified vtable implementations")
    return len(takes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disc", type=Path, required=True)
    parser.add_argument("--tu4", type=Path, required=True)
    parser.add_argument("--hints-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "forza-recomp")
    args = parser.parse_args()
    for name, path, folder, step in (("disc", args.disc, "hints", 0x82C222C8),
                                     ("TU4", args.tu4, "hints_tu4", 0x82CE2A38)):
        base, image = xex_image.load_image(path)
        hints = tomllib.loads((args.hints_root / folder / "resource_data_release.toml").read_text())
        count = verify(base, image, step, hints)
        print(f"{name}: all {count} resource unload takes have compatible atomic hooks")


if __name__ == "__main__":
    main()
