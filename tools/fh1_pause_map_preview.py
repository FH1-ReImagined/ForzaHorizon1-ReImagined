#!/usr/bin/env python3
"""Add a private alias of FH1's original map scene for Home's map preview."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import zipfile

from stage_horizon2_pause_scene import compressed_payload, digest, patch_archive

SOURCE = "ui/scenes/ui4/h_map"
TARGET = "ui/scenes/ui4/fh1_pause_map"
EXECUTABLES = ("default.xex", "XMediaFacade_default.xex", "SpeechFacade_default.xex")


def stage(overlay, output):
    overlay, output = Path(overlay).resolve(), Path(output).resolve()
    if output == overlay or overlay in output.parents:
        raise ValueError("Output must be outside the source overlay")
    if output.exists():
        raise FileExistsError(output)
    source = overlay / "media.zip"
    if not (overlay / "default.xex").is_file():
        raise ValueError("Expected a private FH1 update overlay")
    replacements = {}
    with zipfile.ZipFile(source) as archive:
        entries = {i.filename.lower(): i for i in archive.infolist()}
        for suffix in ("bgf", "bsg", "fbf"):
            info = entries[f"{SOURCE}.{suffix}"]
            target = f"{TARGET}.{suffix}"
            if target in entries:
                raise ValueError("Map preview alias is already staged")
            replacements[target] = (copy.copy(info), compressed_payload(archive, info))
    output.mkdir(parents=True)
    offsets = patch_archive(source, output / "media.zip", replacements)
    for name in EXECUTABLES:
        if (overlay / name).is_file():
            shutil.copyfile(overlay / name, output / name)
    provenance = overlay / "scene-provenance.json"
    report = json.loads(provenance.read_text(encoding="utf-8")) if provenance.exists() else {}
    report["native_home_map_preview"] = {"source": SOURCE, "alias": TARGET,
        "source_archive_sha256": digest(source)}
    report.setdefault("payload_offsets", {}).update(offsets)
    report["output_archive_sha256"] = digest(output / "media.zip")
    (output / "scene-provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args.overlay, args.output)
    print(f"Staged native Home map preview in {args.output}")
