#!/usr/bin/env python3
"""Isolate original FH2 page-navigation code from private ReXGlue output.

Only function symbols are renamed. The instruction translation is retained,
including FH2's disabled-tab checks and end-of-bar behavior. The result is
derived game code: keep the output under extracted/, never commit it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from prepare_horizon2_ui import EXECUTABLE_SHA256

FUNCTIONS = {"sub_826C00F0": "fh2_pause_next_page", "sub_826C0258": "fh2_pause_previous_page"}
ALLOWED_MACROS = {"REX_FUNC_PROLOGUE", "REX_LOAD_U32", "REX_STORE_U32", "REX_CALL_INDIRECT_FUNC"}


def extract(generated, output):
    generated, output = Path(generated).resolve(), Path(output).resolve()
    executable = generated.parent / "default.xex"
    if hashlib.sha256(executable.read_bytes()).hexdigest() != EXECUTABLE_SHA256:
        raise ValueError("Unverified source executable; navigation addresses are version-specific")
    found = {}
    for source in sorted(generated.glob("horizon2_ui_recomp.*.cpp")):
        text = source.read_text(encoding="utf-8")
        for original, renamed in FUNCTIONS.items():
            start = text.find(f"DEFINE_REX_FUNC({original}) {{")
            if start < 0:
                continue
            if original in found:
                raise ValueError(f"Duplicate generated function: {original}")
            end = text.find("\nDEFINE_REX_FUNC(", start + 1)
            body = text[start:end if end >= 0 else None].strip()
            if not body.endswith("}"):
                raise ValueError(f"Incomplete generated function: {original}")
            # These two routines only use the tab datasource's virtual methods.
            # Reject any newly introduced direct dependency instead of stubbing it.
            if re.search(r"\bsub_[0-9A-F]+\(ctx", body):
                raise ValueError(f"Unexpected direct call in {original}")
            macros = set(re.findall(r"\b(REX_[A-Z_0-9]+)\(", body))
            if macros - ALLOWED_MACROS:
                raise ValueError(f"Unreviewed generated macros in {original}: {macros - ALLOWED_MACROS}")
            found[original] = (body.replace(f"DEFINE_REX_FUNC({original})", f"DEFINE_REX_FUNC({renamed})"),
                               hashlib.sha256(body.encode()).hexdigest())
    if found.keys() != FUNCTIONS.keys():
        raise ValueError(f"Missing generated functions: {set(FUNCTIONS) - found.keys()}")
    output.mkdir(parents=True, exist_ok=False)
    (output / "pause_navigation.inc").write_text(
        "// Private translated FH2 code, generated from the owner's executable.\n"
        "// Do not commit or redistribute this file.\n\n" +
        "\n\n".join(found[name][0] for name in FUNCTIONS) + "\n", encoding="utf-8")
    report = {"executable_sha256": EXECUTABLE_SHA256, "transform": "function-symbol rename only",
              "functions": {name: {"symbol": renamed, "translation_sha256": found[name][1]}
                            for name, renamed in FUNCTIONS.items()}}
    (output / "navigation-provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = extract(args.generated, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Isolated {len(report['functions'])} original FH2 routines in {args.output}")


if __name__ == "__main__":
    main()
