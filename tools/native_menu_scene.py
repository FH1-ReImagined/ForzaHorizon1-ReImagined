#!/usr/bin/env python3
"""Prepare private FH1 donor aliases for an original native menu.

This is resource preparation, not a BGF/BSG/FBF scene compiler. The
runtime owns layout, text, input and controller isolation. Whole scene triplets
are copied without decoding or changing geometry, fonts, textures or animation.
Ten attributed monochrome action textures are staged separately.
The existing FH1 pause flow is patched to support native offline car changes.
Only a recognized original FH1 TU4 archive is accepted; an old modified overlay
is not an input. No other game's files or preparation modules are required.

    python tools/native_menu_scene.py FH1_TU4 --output NEW_PRIVATE_DIRECTORY

The versioned JSON manifest contains ``schema: 1`` and a ``scenes`` list of
``{source, alias}`` stems under ui/scenes/ui4. Aliases start with native_menu_.
Adding a donor does not add runtime integration: callers must still isolate its
controllers and manage its native resource lifetime. Generated files contain
the user's game data and belong outside Git, under a private build/extracted
directory. Source files are opened read-only and output must be new.
"""
import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

import lzx_delta
import offline_cars_flow


TITLE_ID = 0x4D5309C9
TU4_VERSION = 0x0000040C
ORIGINAL_ARCHIVE_SHA256 = "dfb31d11bf00153cfc5f3634681a41ea7e2a877d470230a148721ba3ccd5bab3"
DEFAULT_MANIFEST = Path(__file__).parent / "native_menu" / "fh1_donors.json"
ACTION_ICONS = ("change_car", "tune_car", "garage", "autoshow", "social_friends", "social_world", "social_game", "social_requests", "social_chat", "social_connection")


def append_action_icons(archive):
    """Stage attributed SVG-derived art, not borrowed game textures."""
    records = []
    for name in ACTION_ICONS:
        data = (DEFAULT_MANIFEST.parent / "icons" / (name + ".xds")).read_bytes()
        if len(data) != 52 + 256 * 256 * 4 or data[:8] != struct.pack(">II", 3, 1):
            raise ValueError(f"Invalid menu artwork: {name}")
        entry = f"ui/textures/ui4/native_menu/{name}.xds"
        if entry in archive.namelist():
            raise ValueError(f"Menu artwork already exists: {entry}")
        info = zipfile.ZipInfo(entry)
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = info.compress_size = len(data)
        info.CRC = zlib.crc32(data)
        append_compressed(archive, entry, info, data)
        records.append({"entry": entry, "sha256": hashlib.sha256(data).hexdigest()})
    return records
SUFFIXES = ("bgf", "bsg", "fbf")
EXECUTABLES = ("default.xex", "XMediaFacade_default.xex", "SpeechFacade_default.xex")
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SCENES = 64
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_FLOW_BYTES = 4 * 1024 * 1024
_SCENE = re.compile(r"ui/scenes/ui4/[a-z0-9_]{1,80}\Z")
_ALIAS = re.compile(r"ui/scenes/ui4/native_menu_[a-z0-9_]{1,68}\Z")


@dataclass(frozen=True)
class SceneAlias:
    source: str
    alias: str


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def parse_manifest(data):
    """Validate an original alias declaration; return canonical alias order."""
    if not isinstance(data, (str, bytes)) or len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest must be UTF-8 JSON of at most 64 KiB")
    try:
        document = json.loads(data, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ValueError(f"Invalid alias manifest JSON: {error}") from error
    if not isinstance(document, dict) or set(document) != {"schema", "scenes"}:
        raise ValueError("Manifest requires only schema and scenes")
    if type(document["schema"]) is not int or document["schema"] != 1:
        raise ValueError("Unsupported alias manifest schema; expected 1")
    scenes = document["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES:
        raise ValueError(f"Manifest requires 1 to {MAX_SCENES} scenes")
    result, names = [], set()
    for scene in scenes:
        if not isinstance(scene, dict) or set(scene) != {"source", "alias"}:
            raise ValueError("Each scene requires only source and alias")
        source, alias = scene["source"], scene["alias"]
        if not isinstance(source, str) or not _SCENE.fullmatch(source):
            raise ValueError("Scene source must be a lowercase ui/scenes/ui4 name without an extension")
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            raise ValueError("Scene alias must be a lowercase ui/scenes/ui4/native_menu_ name")
        if source == alias or alias in names:
            raise ValueError(f"Duplicate or self-referencing scene alias: {alias}")
        names.add(alias)
        result.append(SceneAlias(source, alias))
    if names.intersection(scene.source for scene in result):
        raise ValueError("Aliases cannot be used as donor sources")
    return tuple(sorted(result, key=lambda scene: scene.alias))


def manifest_bytes(scenes):
    """Serialize validated declarations deterministically, without local paths."""
    document = {"schema": 1, "scenes": [
        {"source": scene.source, "alias": scene.alias} for scene in scenes]}
    encoded = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # Keep the public serializer from accepting unchecked caller-created data.
    canonical = parse_manifest(encoded)
    if tuple(scenes) != canonical:
        return manifest_bytes(canonical)
    return encoded


def verify_executable(path):
    """Read the bounded XEX execution-info header, without loading game code."""
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(24)
        if len(header) != 24 or header[:4] != b"XEX2":
            raise ValueError("Expected FH1 TU4 default.xex (XEX2)")
        header_size = struct.unpack_from(">I", header, 8)[0]
        count = struct.unpack_from(">I", header, 20)[0]
        if not 1 <= count <= 1024 or not 24 + count * 8 <= header_size <= min(size, 1024 * 1024):
            raise ValueError("Invalid XEX optional-header bounds")
        options = stream.read(count * 8)
        execution = []
        for offset in range(0, len(options), 8):
            key, value = struct.unpack_from(">II", options, offset)
            if key == 0x40006:
                execution.append(value)
        if len(execution) != 1 or not 24 + count * 8 <= execution[0] <= header_size - 24:
            raise ValueError("Expected one bounded XEX execution-info header")
        stream.seek(execution[0])
        information = stream.read(24)
        if len(information) != 24:
            raise ValueError("Truncated XEX execution-info header")
        version = struct.unpack_from(">I", information, 4)[0]
        title = struct.unpack_from(">I", information, 12)[0]
        if title != TITLE_ID or version != TU4_VERSION:
            raise ValueError("Expected original Forza Horizon 1 TU4 default.xex (title 4D5309C9, version 0x40C)")


def compressed_payload(archive, info):
    """Read a checked raw ZIP payload, including the game's opaque method 21."""
    if info.flag_bits & ~0x808 or info.compress_type not in (0, 8, 21):
        raise ValueError(f"Unsupported scene encryption, ZIP flags or codec: {info.filename}")
    if not 0 < info.compress_size <= MAX_PAYLOAD_BYTES or not 0 < info.file_size <= MAX_PAYLOAD_BYTES:
        raise ValueError(f"Invalid or oversized scene payload: {info.filename}")
    archive.fp.seek(info.header_offset)
    header = archive.fp.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise ValueError(f"Invalid local ZIP header: {info.filename}")
    flags, codec = struct.unpack_from("<HH", header, 6)
    crc, compressed_size, size = struct.unpack_from("<III", header, 14)
    name_size, extra_size = struct.unpack_from("<HH", header, 26)
    name, extra = archive.fp.read(name_size), archive.fp.read(extra_size)
    encoding = "utf-8" if flags & 0x800 else "cp437"
    if (flags != info.flag_bits or codec != info.compress_type or
            name != info.orig_filename.encode(encoding) or len(extra) != extra_size):
        raise ValueError(f"Local/central ZIP header mismatch: {info.filename}")
    if not flags & 8 and (crc, compressed_size, size) != (info.CRC, info.compress_size, info.file_size):
        raise ValueError(f"Local/central ZIP sizes or CRC mismatch: {info.filename}")
    payload_offset = archive.fp.tell()
    cursor, absolute_offsets = 0, []
    while cursor < len(extra):
        if cursor + 4 > len(extra):
            raise ValueError(f"Truncated ZIP extra field: {info.filename}")
        tag, length = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        if cursor + length > len(extra):
            raise ValueError(f"Truncated ZIP extra value: {info.filename}")
        if tag == 0x1123:
            if length != 4:
                raise ValueError(f"Invalid Forza payload offset field: {info.filename}")
            absolute_offsets.append(struct.unpack_from("<I", extra, cursor)[0])
        cursor += length
    if absolute_offsets and absolute_offsets != [payload_offset]:
        raise ValueError(f"Incorrect Forza payload offset: {info.filename}")
    if payload_offset + info.compress_size > archive.start_dir:
        raise ValueError(f"Scene payload overlaps the ZIP directory: {info.filename}")
    payload = archive.fp.read(info.compress_size)
    if len(payload) != info.compress_size:
        raise ValueError(f"Truncated scene payload: {info.filename}")
    return payload


def append_compressed(archive, name, source, payload):
    """Append an alias with the native absolute data-offset extra field.

    Python cannot encode ZIP method 21. It writes these already-compressed bytes
    as a temporary stored member; both local and central metadata are then set
    to the original codec, uncompressed size and CRC. No payload is recompressed.
    """
    if source.compress_size != len(payload) or not payload:
        raise ValueError("Compressed alias payload size does not match its source")
    offset = archive.start_dir + 30 + len(name.encode("ascii")) + 8
    if offset + len(payload) > 0xFFFFFFFF:
        raise ValueError("Forza scene aliases require a 32-bit ZIP payload offset")
    info = zipfile.ZipInfo(name, date_time=source.date_time)
    info.extra = struct.pack("<HHI", 0x1123, 4, offset)
    archive.writestr(info, payload, compress_type=zipfile.ZIP_STORED)
    end = archive.fp.tell()
    info.compress_type, info.CRC, info.file_size = source.compress_type, source.CRC, source.file_size
    info.create_system, info.external_attr, info.internal_attr = source.create_system, source.external_attr, source.internal_attr
    archive.fp.seek(info.header_offset + 8)
    archive.fp.write(struct.pack("<H", info.compress_type))
    archive.fp.seek(info.header_offset + 14)
    archive.fp.write(struct.pack("<III", info.CRC, info.compress_size, info.file_size))
    archive.fp.seek(end)
    return offset


def decode_flow(info, payload):
    """Decode a bounded pause-flow member and verify its exact size and CRC.

    XMem framing follows fxobj_shaders.read_zip_member; the same repository LZX
    decoder works without an external executable. Unlike that scan helper, this
    preparation path rejects incomplete framing and checks the ZIP CRC. Intel
    E8 translation is unsupported by lzx_delta; the recognized TU4 XML does not
    use it. Scene aliases themselves remain opaque compressed data.
    """
    if not 0 < info.file_size <= MAX_FLOW_BYTES or len(payload) != info.compress_size:
        raise ValueError("Invalid or oversized FH1 pause-flow payload")
    try:
        if info.compress_type == zipfile.ZIP_STORED:
            data = payload
        elif info.compress_type == zipfile.ZIP_DEFLATED:
            decoder = zlib.decompressobj(-15)
            data = decoder.decompress(payload, info.file_size + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError("Incomplete or oversized deflated pause flow")
        elif info.compress_type == 21:
            stream, cursor, expected_size = bytearray(), 0, 0
            while cursor < len(payload):
                # The original TU4 member ends in a zero-length marker and
                # zero padding. It is not part of the LZX bitstream.
                remaining = payload[cursor:]
                if 2 <= len(remaining) <= 5 and not any(remaining):
                    cursor = len(payload)
                    break
                header_size = 5 if payload[cursor] == 0xFF else 2
                if cursor + header_size > len(payload):
                    raise ValueError("Truncated XMem pause-flow block header")
                if header_size == 5:
                    size, packed = struct.unpack_from(">HH", payload, cursor + 1)
                else:
                    size, packed = 32768, struct.unpack_from(">H", payload, cursor)[0]
                cursor += header_size
                if not 0 < size <= 32768 or not packed or cursor + packed > len(payload):
                    raise ValueError("Invalid XMem pause-flow block bounds")
                expected_size += size
                if expected_size > info.file_size:
                    raise ValueError("XMem pause-flow blocks exceed the declared size")
                stream.extend(payload[cursor:cursor + packed])
                cursor += packed
            if expected_size != info.file_size:
                raise ValueError("XMem pause-flow block sizes do not match the ZIP member")
            data = lzx_delta.decompress(bytes(stream), info.file_size, 1 << 17)
        else:
            raise ValueError("Unsupported FH1 pause-flow compression")
    except (zlib.error, lzx_delta.LzxError, struct.error, IndexError) as error:
        raise ValueError(f"Could not decode FH1 pause flow: {error}") from error
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        raise ValueError("FH1 pause flow failed size/CRC validation")
    return bytes(data)


def stage(update, output, manifest=DEFAULT_MANIFEST):
    """Create a new private overlay; return deterministic provenance metadata."""
    update, output, manifest = (Path(path).resolve() for path in (update, output, manifest))
    if output == update or update in output.parents or output in update.parents:
        raise ValueError("Output must be outside the source update directory")
    if output == manifest or output in manifest.parents:
        raise ValueError("Output must not contain the source manifest")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if not output.parent.is_dir():
        raise ValueError("Output parent directory must already exist")
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest exceeds 64 KiB")
    scenes = parse_manifest(manifest.read_bytes())
    source = update / "media.zip"
    verify_executable(update / "default.xex")
    source_digest = digest(source)
    if source_digest != ORIGINAL_ARCHIVE_SHA256:
        raise ValueError(
            "Unsupported or modified FH1 TU4 media.zip. Use the original extracted "
            "FH1 title-update 4 directory, not a previously staged menu overlay. "
            f"Expected SHA256 {ORIGINAL_ARCHIVE_SHA256}; found {source_digest}.")

    replacements = []
    with zipfile.ZipFile(source) as archive:
        entries = {}
        for info in archive.infolist():
            name = info.filename.lower()
            if name in entries:
                raise ValueError(f"Ambiguous case-insensitive ZIP entry: {name}")
            entries[name] = info
        if len(entries) + len(scenes) * len(SUFFIXES) >= 0xFFFF:
            raise ValueError("Too many ZIP entries for a native scene overlay")
        for scene in scenes:
            for suffix in SUFFIXES:
                donor, alias = f"{scene.source}.{suffix}", f"{scene.alias}.{suffix}"
                if donor not in entries:
                    raise ValueError(f"Original FH1 scene triplet is incomplete: missing {donor}")
                if alias in entries:
                    raise ValueError(f"Scene alias already exists: {alias}")
                info = entries[donor]
                replacements.append((alias, info, compressed_payload(archive, info)))
        if offline_cars_flow.ENTRY not in entries:
            raise ValueError("Original FH1 update is missing gamemodes/pause.xml")
        original_flow = entries[offline_cars_flow.ENTRY]
        flow = decode_flow(original_flow, compressed_payload(archive, original_flow))
        try:
            patched_flow = offline_cars_flow.patch_pause_xml(flow)
        except ET.ParseError as error:
            raise ValueError(f"Invalid FH1 pause-flow XML: {error}") from error
        flow_info = copy.copy(original_flow)
        flow_info.compress_type = zipfile.ZIP_STORED
        flow_info.file_size = flow_info.compress_size = len(patched_flow)
        flow_info.CRC = zlib.crc32(patched_flow)
        flow_report = {
            "entry": offline_cars_flow.ENTRY,
            "source_sha256": hashlib.sha256(flow).hexdigest(),
            "output_sha256": hashlib.sha256(patched_flow).hexdigest(),
            "source_compression": original_flow.compress_type,
            "output_compression": zipfile.ZIP_STORED,
        }

    # Complete and verify the private copy before making the result visible.
    with tempfile.TemporaryDirectory(prefix=".native-menu-", dir=output.parent) as folder:
        staged = Path(folder) / "overlay"
        staged.mkdir()
        destination = staged / "media.zip"
        shutil.copyfile(source, destination)
        if digest(destination) != source_digest:
            raise ValueError("Source archive changed during preparation; retry with a stable original copy")
        records = []
        with zipfile.ZipFile(destination, "a", allowZip64=False) as archive:
            # Retain the original local data and offsets, replacing only this
            # member's central-directory entry. There is one active pause.xml.
            archive.filelist = [info for info in archive.filelist
                                if info.filename.lower() != offline_cars_flow.ENTRY]
            archive.NameToInfo = {info.filename: info for info in archive.filelist}
            for alias, info, payload in replacements:
                offset = append_compressed(archive, alias, info, payload)
                records.append({
                    "alias": alias, "source": info.filename,
                    "compression": info.compress_type, "size": info.file_size,
                    "crc32": f"{info.CRC:08x}", "payload_offset": offset,
                    "compressed_sha256": hashlib.sha256(payload).hexdigest(),
                })
            flow_report["payload_offset"] = append_compressed(
                archive, offline_cars_flow.ENTRY, flow_info, patched_flow)
            artwork = append_action_icons(archive)
        executables = {}
        for name in EXECUTABLES:
            executable = update / name
            if executable.is_file():
                before = digest(executable)
                shutil.copyfile(executable, staged / name)
                if digest(staged / name) != before:
                    raise ValueError(f"Source executable changed during preparation: {name}")
                executables[name] = before
        report = {
            "schema": 1, "backend": "fh1-native-menu-resources",
            "status": "FH1 donor aliases and offline car flow; runtime layout and integration required",
            "source_archive_sha256": source_digest,
            "output_archive_sha256": digest(destination),
            "manifest_sha256": hashlib.sha256(manifest_bytes(scenes)).hexdigest(),
            "executables": executables, "entries": records,
            "offline_cars_flow": flow_report,
            "action_icons": artwork,
        }
        (staged / "native-menu-provenance.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        (staged / "native-menu-icons-license.txt").write_text(
            (DEFAULT_MANIFEST.parent / "icons/ATTRIBUTION.md").read_text(encoding="utf-8")+"\n"+
            (DEFAULT_MANIFEST.parent / "icons/FONT-AWESOME-LICENSE.txt").read_text(encoding="utf-8"), encoding="utf-8")
        if output.exists():
            raise FileExistsError(f"Output already exists: {output}")
        staged.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("update", type=Path, help="Original extracted FH1 TU4 directory")
    parser.add_argument("--output", type=Path, required=True, help="New private overlay directory")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="Original JSON scene-alias declaration")
    args = parser.parse_args()
    try:
        stage(args.update, args.output, args.manifest)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")
    print(f"Prepared FH1 donor aliases and offline car flow in {args.output}; runtime integration is separate")


if __name__ == "__main__":
    main()
