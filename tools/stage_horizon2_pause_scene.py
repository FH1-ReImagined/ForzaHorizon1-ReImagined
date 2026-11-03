#!/usr/bin/env python3
"""Stage the owner's original FH2 pause scene in a private FH1 update copy.

This stages assets for controller-port development, not a finished menu mod.
The stock FH1 pause controller cannot drive this scene. Input packages stay
read-only; the new update directory must not exist. Keep all output private.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zipfile

from prepare_horizon2_ui import EXECUTABLE_SHA256
import offline_cars_flow
import fh1_pause_header
import fh2_pause_tabs

SCENE = "Scenes/ui4/Anth_925_PAUSE_MENU_FIRSTPASS"
TARGET = "ui/scenes/ui4/925_pause_menu"
SUFFIXES = ("bgf", "bsg", "fbf")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compressed_payload(archive, info):
    if info.flag_bits & 1 or info.compress_type not in (0, 8, 21):
        raise ValueError(f"Unsupported scene compression or encryption: {info.filename}")
    archive.fp.seek(info.header_offset)
    header = archive.fp.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise ValueError(f"Invalid local ZIP header: {info.filename}")
    name_size, extra_size = struct.unpack_from("<HH", header, 26)
    archive.fp.seek(name_size + extra_size, 1)
    payload = archive.fp.read(info.compress_size)
    if len(payload) != info.compress_size:
        raise ValueError(f"Truncated scene: {info.filename}")
    return payload


def append_compressed(archive, name, source, payload):
    # Forza uses extra field 0x1123 as an absolute payload offset. A normal ZIP
    # writer omits it, resulting in bad scene reads even for unchanged FH1 files.
    # Include its own eight-byte local extra field when computing the offset.
    info = zipfile.ZipInfo(name, date_time=source.date_time)
    data_offset = archive.fp.tell() + 30 + len(name.encode("ascii")) + 8
    info.extra = struct.pack("<HHI", 0x1123, 4, data_offset)
    info.external_attr = source.external_attr
    # zipfile cannot encode method 21. Write the already-compressed bytes as a
    # temporary stored record, then restore the source codec/CRC/size metadata
    # in both headers. The owner's compressed scene bytes are never translated.
    archive.writestr(info, payload, compress_type=zipfile.ZIP_STORED)
    end = archive.fp.tell()
    info.compress_type = source.compress_type
    info.CRC = source.CRC
    info.file_size = source.file_size
    archive.fp.seek(info.header_offset + 8)
    archive.fp.write(struct.pack("<H", info.compress_type))
    archive.fp.seek(info.header_offset + 14)
    archive.fp.write(struct.pack("<III", info.CRC, info.compress_size, info.file_size))
    archive.fp.seek(end)
    return data_offset


def patch_archive(source_zip, destination, replacements):
    if Path(destination).exists():
        raise FileExistsError(f"Output archive already exists: {destination}")
    shutil.copyfile(source_zip, destination)
    with zipfile.ZipFile(destination, "a") as archive:
        names = {name.lower() for name in replacements}
        archive.filelist = [i for i in archive.filelist if i.filename.lower() not in names]
        archive.NameToInfo = {i.filename: i for i in archive.filelist}
        offsets = {}
        for name, (info, payload) in replacements.items():
            offsets[name] = append_compressed(archive, name, info, payload)
    return offsets


def stage(prepared, update, output, *, offline_cars=False, fh1_header=False,
          garage_header=False, unxmem=None):
    prepared, update, output = (Path(p).resolve() for p in (prepared, update, output))
    if output == update or update in output.parents:
        raise ValueError("Output must be outside the source update directory")
    if output == prepared or output == prepared / "media" or prepared / "media" in output.parents:
        raise ValueError("Output must not replace the prepared source files")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if digest(prepared / "default.xex") != EXECUTABLE_SHA256:
        raise ValueError("Unverified FH2 source executable")
    provenance = json.loads((prepared / "provenance.json").read_text(encoding="utf-8"))
    ui_zip = prepared / "media/ui.zip"
    if digest(ui_zip) != provenance["files"]["media/ui.zip"]["sha256"]:
        raise ValueError("Prepared FH2 UI archive does not match its provenance")
    textures_zip = prepared / "media/ui/textures.zip"
    if digest(textures_zip) != provenance["files"]["media/ui/textures.zip"]["sha256"]:
        raise ValueError("Prepared FH2 texture archive does not match its provenance")
    if not (update / "default.xex").is_file() or not (update / "media.zip").is_file():
        raise ValueError("Expected an extracted FH1 title-update directory")
    replacements = {}
    with zipfile.ZipFile(ui_zip) as archive:
        entries = {i.filename.lower(): i for i in archive.infolist()}
        for suffix in SUFFIXES:
            source_name = f"{SCENE}.{suffix}".lower()
            if source_name not in entries:
                raise ValueError(f"Missing original FH2 scene: {source_name}")
            info = entries[source_name]
            replacements[f"{TARGET}.{suffix}"] = (copy.copy(info), compressed_payload(archive, info))
            if fh1_header and suffix in ("bgf", "fbf"):
                replacements[f"{TARGET}.{suffix}"] = fh1_pause_header.replacement(
                    *replacements[f"{TARGET}.{suffix}"], suffix, unxmem)
            if suffix in ("bgf", "bsg"):
                replacements[f"{TARGET}.{suffix}"] = fh2_pause_tabs.replacement(
                    *replacements[f"{TARGET}.{suffix}"], suffix, unxmem)
    texture_names = json.loads((Path(__file__).parent / "horizon2_ui/pause_scene_textures.json").read_text())
    with zipfile.ZipFile(textures_zip) as archive:
        entries = {i.filename.lower(): i for i in archive.infolist()}
        for name in texture_names:
            if not name.lower().startswith("ui4/anthem/") or ".." in name.split("/"):
                raise ValueError(f"Invalid scene texture manifest entry: {name}")
            info = entries[name.lower()]
            replacements[f"ui/textures/{name.lower()}"] = (copy.copy(info), compressed_payload(archive, info))
    if offline_cars:
        replacements[offline_cars_flow.ENTRY] = offline_cars_flow.replacement(
            update / "media.zip", unxmem, compressed_payload)
    if garage_header:
        # A distinct resource name gives pause its own presentation instance.
        # The real garage retains its original resource and all its controls.
        with zipfile.ZipFile(update / "media.zip") as archive:
            entries = {i.filename.lower(): i for i in archive.infolist()}
            for suffix in SUFFIXES:
                source = f"ui/scenes/ui4/108_c_buy_carselect.{suffix}"
                if source not in entries:
                    raise ValueError(f"Missing native garage scene: {source}")
                info = entries[source]
                replacements[f"ui/scenes/ui4/fh1_pause_player_bar.{suffix}"] = (
                    copy.copy(info), compressed_payload(archive, info))
    output.mkdir(parents=True)
    offsets = patch_archive(update / "media.zip", output / "media.zip", replacements)
    for name in ("default.xex", "XMediaFacade_default.xex", "SpeechFacade_default.xex"):
        if (update / name).is_file():
            shutil.copyfile(update / name, output / name)
    report = {"status": "scene assets only; controller integration required",
              "fh2_executable_sha256": EXECUTABLE_SHA256,
              "fh2_ui_sha256": provenance["files"]["media/ui.zip"]["sha256"],
              "fh2_textures_sha256": provenance["files"]["media/ui/textures.zip"]["sha256"],
              "texture_count": len(texture_names),
              "fh1_update_archive_sha256": digest(update / "media.zip"),
              "output_archive_sha256": digest(output / "media.zip"),
              "scene": SCENE, "payload_offsets": offsets,
              "offline_cars_reload_flow": offline_cars,
              "fh1_header_fonts": fh1_header,
              "native_garage_header": garage_header,
              "fixed_right_bumper_intro": True}
    (output / "scene-provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path, help="Output of prepare_horizon2_ui.py")
    parser.add_argument("update", type=Path, help="Existing FH1 TU4 update directory")
    parser.add_argument("--output", type=Path, required=True, help="New private update directory")
    parser.add_argument("--offline-cars", action="store_true",
                        help="Include the TU4 pause-safe garage reload flow")
    parser.add_argument("--unxmem", type=Path,
                        help="XMem decoder executable for the private scene and optional Cars flow")
    parser.add_argument("--fh1-header", action="store_true",
                        help="Use FH1 header fonts in the private scene (requires --unxmem)")
    parser.add_argument("--garage-header", action="store_true",
                        help="Copy the unchanged FH1 garage presentation under a private alias")
    args = parser.parse_args()
    try:
        stage(args.prepared, args.update, args.output,
              offline_cars=args.offline_cars, fh1_header=args.fh1_header,
              garage_header=args.garage_header, unxmem=args.unxmem)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")
    print(f"Staged original FH2 scene assets in {args.output}; controller integration is required")


if __name__ == "__main__":
    main()
