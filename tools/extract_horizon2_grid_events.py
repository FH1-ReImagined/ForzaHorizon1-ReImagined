#!/usr/bin/env python3
"""Isolate FH2's original grid page and button-event routines into a private include.

Derived instruction translations stay under extracted/, alongside the verified
owner-provided executable. The adapter supplies only formatting and event output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from prepare_horizon2_ui import EXECUTABLE_SHA256

FUNCTIONS = {
    "sub_83000500": "fh2_grid_hash",
    "sub_82964000": "fh2_grid_immediate",
    "sub_82964050": "fh2_grid_transition",
    "sub_82699460": "fh2_button_focus",
    "sub_826994A0": "fh2_button_blur",
    "sub_826994C0": "fh2_button_select",
    "sub_826994E0": "fh2_button_unselect",
}
BOUNDARIES = {"sub_824F46E0": "GridFormat", "sub_83001A88": "GridEmit"}
MACROS = {"REX_FUNC_PROLOGUE", "REX_LOAD_U8", "REX_LOAD_U32", "REX_LOAD_U64",
          "REX_STORE_U32", "REX_STORE_U64"}


def extract(generated, output):
    generated, output = Path(generated).resolve(), Path(output).resolve()
    if "extracted" not in output.parts:
        raise ValueError("Derived grid code must remain under an extracted/ directory")
    if hashlib.sha256((generated.parent / "default.xex").read_bytes()).hexdigest() != EXECUTABLE_SHA256:
        raise ValueError("Unverified FH2 executable; grid addresses are version-specific")
    found = {}
    for source in sorted(generated.glob("horizon2_ui_recomp.*.cpp")):
        source_text = source.read_text(encoding="utf-8")
        for name in FUNCTIONS:
            marker = f"DEFINE_REX_FUNC({name}) {{"
            start = source_text.find(marker)
            if start < 0:
                continue
            if name in found or source_text.find(marker, start + len(marker)) >= 0:
                raise ValueError(f"Duplicate function: {name}")
            end = source_text.find("\nDEFINE_REX_FUNC(", start + 1)
            body = source_text[start:end if end >= 0 else None].strip()
            if not body.endswith("}"):
                raise ValueError(f"Incomplete function: {name}")
            calls = set(re.findall(r"\b(sub_[0-9A-F]+)\s*\(\s*ctx\b", body))
            if calls - (FUNCTIONS.keys() | BOUNDARIES.keys()):
                raise ValueError(f"Unadapted calls: {calls}")
            macros = set(re.findall(r"\b(REX_[A-Z_0-9]+)\s*\(", body))
            if macros - MACROS:
                raise ValueError(f"Unreviewed macros: {macros - MACROS}")
            digest = hashlib.sha256(body.encode()).hexdigest()
            for original, replacement in {**FUNCTIONS, **BOUNDARIES}.items():
                body = re.sub(rf"\b{original}\b", replacement, body)
            found[name] = (body, digest)
    if found.keys() != FUNCTIONS.keys():
        raise ValueError(f"Missing functions: {FUNCTIONS.keys() - found.keys()}")
    output.mkdir(parents=True, exist_ok=False)
    (output / "grid_events.inc").write_text(
        "// Private translated FH2 code. Do not commit or redistribute.\n\n" +
        "\n\n".join(found[name][0] for name in FUNCTIONS) + "\n", encoding="utf-8")
    report = {"executable_sha256": EXECUTABLE_SHA256,
              "transform": "function-symbol rename only; formatting and emission adapted",
              "functions": {name: {"symbol": renamed, "translation_sha256": found[name][1]}
                            for name, renamed in FUNCTIONS.items()}, "boundaries": BOUNDARIES}
    (output / "grid-provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = extract(args.generated, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Isolated {len(report['functions'])} FH2 grid routines in {args.output}")
