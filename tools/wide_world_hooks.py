#!/usr/bin/env python3
"""The hooks that let a world reach past 8,192 m (forza-recomp/src/hooks/wide_world.cpp): finds
every fixed-point position conversion in a player-owned Title Update 4 executable and checks
(or writes) forza-recomp/hints_tu4/wide_world.toml against it. Read-only on the image.

Usage: wide_world_hooks.py --tu4 PATH [--write] [--hints PATH]
Requires openssl on PATH, as tools/xex_image.py does.

The game keeps camera-relative positions as 32-bit fixed point with 18 fraction bits
(Vec4Fixed<18>: 4 micrometre steps, +-8,192 m, saturating). Each conversion is one vector
instruction with the bit count in it: float to fixed vcfpsxws128 / vctsxs, fixed to float
vcsxwfp128 / vcfsx. A hook before each lets the runtime do it with 17 bits instead.
"""
import argparse
from pathlib import Path
import struct
import tomllib

import xex_image

BITS = 18
TO_FIXED, TO_FLOAT = "ForzaWideWorldToFixed", "ForzaWideWorldToFloat"
HEADER = """\
# A world past 8,192 m (src/hooks/wide_world.cpp; written and checked by
# tools/wide_world_hooks.py). The game keeps camera-relative positions as 32-bit fixed point
# with 18 fraction bits (Vec4Fixed<18>): +-8,192 m, saturating. Every conversion to or from
# it is one of these vector instructions (float to fixed: vcfpsxws128, vctsxs; fixed to
# float: vcsxwfp128, vcfsx; 18 in each). The hook before each returns false and the
# instruction runs as it is, unless the wide world switch is on: then the hook converts with
# 17 bits (+-16,384 m) and the instruction is jumped over.
"""


def conversions(base, image, bits=BITS):
    """[(address, hook name, destination register, source register)] of the image's code."""
    found = []
    for _name, start, end, executable in xex_image.sections(base, image):
        if not executable:
            continue
        for address in range(start, end - 3, 4):
            word = struct.unpack_from(">I", image, address - base)[0]
            if (word >> 16) & 31 != bits:
                continue
            op = word >> 26
            if op == 6 and (word & 0x7F0) in (0x230, 0x2B0):      # VMX128: 128 registers
                name = TO_FIXED if (word & 0x7F0) == 0x230 else TO_FLOAT
                to, source = (word >> 21) & 31 | ((word >> 2) & 3) << 5, (word >> 11) & 31 | (word & 3) << 5
            elif op == 4 and (word & 0x7FF) in (970, 842):         # vctsxs, vcfsx
                name = TO_FIXED if (word & 0x7FF) == 970 else TO_FLOAT
                to, source = (word >> 21) & 31, (word >> 11) & 31
            else:
                continue
            found.append((address, name, to, source))
    return found


def hook_name(name, to, source):
    """The generated declaration names a hook's parameters after its registers, so a
    conversion in place (one register) has a hook of its own with one vector parameter."""
    return name + ("InPlace" if to == source else "")


def registers(to, source):
    return ["fpscr", f"v{to}"] + ([] if to == source else [f"v{source}"])


def toml_list(items):
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"


def hints_text(found):
    out = [HEADER]
    for address, name, to, source in found:
        out.append(f'[[midasm_hook]]\naddress = 0x{address:08X}\nname = "{hook_name(name, to, source)}"\n'
                   f"registers = {toml_list(registers(to, source))}\n"
                   f"jump_address_on_true = 0x{address + 4:08X}\n")
    return "\n".join(out)


def verify(base, image, hints):
    """Every conversion has its hook and no hook is anywhere else; returns how many."""
    found = conversions(base, image)
    hooks = hints.get("midasm_hook", [])
    by_address = {hook["address"]: hook for hook in hooks}
    if len(by_address) != len(hooks):
        raise ValueError("Duplicate wide world hook addresses")
    for address, name, to, source in found:
        hook = by_address.pop(address, None)
        want = dict(address=address, name=hook_name(name, to, source), registers=registers(to, source),
                    jump_address_on_true=address + 4)
        if hook != want:
            raise ValueError(f"Missing or different hook at {address:08X}: wanted {want}, found {hook}")
    if by_address:
        raise ValueError("Hooks where the image has no such conversion: "
                         + ", ".join(f"{a:08X}" for a in sorted(by_address)))
    if not found:
        raise ValueError("No fixed-point conversions found; check the executable version")
    return len(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tu4", type=Path, required=True, help="Title Update 4's default.xex")
    parser.add_argument("--hints", type=Path, default=Path(__file__).resolve().parents[1]
                        / "forza-recomp" / "hints_tu4" / "wide_world.toml")
    parser.add_argument("--write", action="store_true", help="write the hints file instead of checking it")
    args = parser.parse_args()
    base, image = xex_image.load_image(args.tu4)
    if args.write:
        found = conversions(base, image)
        args.hints.write_text(hints_text(found), encoding="utf-8", newline="\n")
        print(f"{args.hints}: {len(found)} hooks written")
    count = verify(base, image, tomllib.loads(args.hints.read_text(encoding="utf-8")))
    print(f"TU4: all {count} fixed-point position conversions have their hook")


if __name__ == "__main__":
    main()
