#!/usr/bin/env python3
"""Copy a private pause overlay and reuse FH1's original painted panel texture.

No texture is decoded, painted, resized or recompressed. Original game archives
and the input overlay remain read-only. The output directory must be new.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from stage_horizon2_pause_scene import compressed_payload, digest, patch_archive

SOURCE = "DirtMasks/Panel01.xds"
TARGET = "ui/textures/ui4/anthem/pausemenu_whtbox.xds"
EXECUTABLES = ("default.xex", "XMediaFacade_default.xex", "SpeechFacade_default.xex")


def stage_theme(overlay, horizon_textures, output):
    overlay, horizon_textures, output = (
        Path(p).resolve() for p in (overlay, horizon_textures, output))
    if output == overlay or overlay in output.parents:
        raise ValueError("Output must be outside the input overlay directory")
    if output == horizon_textures or output in horizon_textures.parents:
        raise ValueError("Output must not contain the original texture archive")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    archive_path = overlay / "media.zip"
    if not (overlay / "default.xex").is_file():
        raise ValueError("Expected a private pause overlay with default.xex")
    with zipfile.ZipFile(archive_path) as archive:
        if TARGET not in {i.filename.lower() for i in archive.infolist()}:
            raise ValueError("Input overlay does not contain the imported pause panel texture")
    with zipfile.ZipFile(horizon_textures) as archive:
        entries = {i.filename.lower(): i for i in archive.infolist()}
        if SOURCE.lower() not in entries:
            raise ValueError(f"Original FH1 texture missing: {SOURCE}")
        info = copy.copy(entries[SOURCE.lower()])
        payload = compressed_payload(archive, info)
    # Both White_BGMaterial and Orange_HLMaterial use this image with normalized
    # UVs. Panel01's native white/alpha swizzle retains their runtime tint.
    # ButtonGrad is a footer/help gradient and deliberately stays unchanged.
    output.mkdir(parents=True)
    offsets = patch_archive(archive_path, output / "media.zip", {TARGET: (info, payload)})
    for name in EXECUTABLES:
        if (overlay / name).is_file():
            shutil.copyfile(overlay / name, output / name)
    report = {
        "status": "original FH1 texture aliased in a private pause overlay",
        "source_overlay_archive_sha256": digest(archive_path),
        "source_texture_archive_sha256": digest(horizon_textures),
        "source_texture": info.filename,
        "target_texture": TARGET,
        "compressed_texture_sha256": hashlib.sha256(payload).hexdigest(),
        "source_compression_method": info.compress_type,
        "source_uncompressed_size": info.file_size,
        "source_crc32": f"{info.CRC:08x}",
        "payload_offsets": offsets,
        "output_archive_sha256": digest(output / "media.zip"),
    }
    previous = overlay / "scene-provenance.json"
    provenance = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else {}
    provenance["fh1_tile_theme"] = report
    provenance["output_archive_sha256"] = report["output_archive_sha256"]
    provenance.setdefault("payload_offsets", {}).update(offsets)
    (output / "scene-provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path, help="Existing private pause update directory")
    parser.add_argument("horizon_textures", type=Path, help="Original FH1 media/ui/textures/Horizon.zip")
    parser.add_argument("--output", type=Path, required=True, help="New private update directory")
    args = parser.parse_args()
    try:
        stage_theme(args.overlay, args.horizon_textures, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")
    print(f"Staged original FH1 painted panel in {args.output}")


if __name__ == "__main__":
    main()
