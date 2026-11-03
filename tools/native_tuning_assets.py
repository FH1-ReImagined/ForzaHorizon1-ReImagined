#!/usr/bin/env python3
"""Stage private FH1-only donor aliases for the original tuning components.

Input must be the pristine extracted TU4. Output must be new and outside Git.
Opaque compressed scene triplets are copied unchanged. No flow, geometry,
texture or executable code is patched; runtime owns layout and input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import zipfile

TITLE_ID=0x4D5309C9
TU4_VERSION=0x0000040C
ORIGINAL_ARCHIVE_SHA256="dfb31d11bf00153cfc5f3634681a41ea7e2a877d470230a148721ba3ccd5bab3"
MAX_PAYLOAD_BYTES=64*1024*1024

class PreparationError(ValueError):
    def __init__(self, code, reason, action, detail=""):
        super().__init__(reason)
        self.diagnostic = dict(code=code, reason=reason, action=action, detail=detail)


def error_diagnostic(error):
    if isinstance(error, PreparationError):
        return error.diagnostic
    if isinstance(error, PermissionError):
        code, reason, action = ("access_denied", "Tuning preparation could not read or write a file.",
                                "Check the file access error in the build log and the selected data folder's permissions.")
    elif isinstance(error, FileNotFoundError):
        code, reason, action = ("missing_file", "A required Title Update 4 file is missing.",
                                "Check the missing path in the build log and select your original TU4 package in Setup.")
    elif isinstance(error, (KeyError, zipfile.BadZipFile)):
        code, reason, action = ("invalid_archive", "The TU4 archive is unreadable or lacks a required tuning scene.",
                                "Select your original supported TU4 package in Setup. Send the build log if it still fails.")
    else:
        code, reason, action = ("preparation_failed", "Tuning resource preparation failed.",
                                "Send the build log, including the error below; another rebuild alone may not fix it.")
    return dict(code=code, reason=reason, action=action, detail=str(error)[:4000])
def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

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
            raise PreparationError("wrong_title_update", "The selected executable is not FH1 Title Update 4.",
                                   "Select your original FH1 Title Update 4 package in Setup, then Build.",
                                   f"Expected title 4D5309C9/version 0000040C; found {title:08X}/{version:08X}.")

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

def stage(update,output):
    update,output=Path(update).resolve(),Path(output).resolve()
    if output.exists(): raise FileExistsError(output)
    if update==output or update in output.parents: raise ValueError("Output must be separate from original inputs")
    try:
        verify_executable(update/"default.xex")
    except PreparationError:
        raise
    except ValueError as error:
        raise PreparationError("invalid_executable", "The TU4 executable header is unreadable or unsupported.",
                               "Select your original supported FH1 Title Update 4 package in Setup. Send the build log if it still fails.",
                               str(error)) from error
    source=update/"media.zip"
    actual_digest = digest(source)
    if actual_digest != ORIGINAL_ARCHIVE_SHA256:
        raise PreparationError("unsupported_archive", "TU4 media.zip does not match the supported original archive.",
                               "Check that Setup uses an unmodified supported TU4 package. Send the build log if yours is original.",
                               f"File: {source}; expected SHA-256 {ORIGINAL_ARCHIVE_SHA256}; actual {actual_digest}.")
    stem="ui/scenes/ui4/108_c_buy_carselect"
    with zipfile.ZipFile(source) as archive:
        names={info.filename.lower().replace(chr(92),"/"):info for info in archive.infolist()}
        triplet=[(names[stem+"."+extension],extension) for extension in ("bgf","bsg","fbf")]
        payloads=[(info,extension,compressed_payload(archive,info)) for info,extension in triplet]
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".native-tuning-",dir=output.parent) as directory:
        staged=Path(directory)/"overlay";staged.mkdir()
        shutil.copyfile(source,staged/"media.zip")
        records=[]
        with zipfile.ZipFile(staged/"media.zip","a",allowZip64=False) as archive:
            for i in range(22):
                for info,extension,payload in payloads:
                    alias=f"ui/scenes/ui4/native_menu_tuning_{i}.{extension}"
                    append_compressed(archive,alias,info,payload)
                    records.append({"alias":alias,"source":info.filename,"compressed_sha256":hashlib.sha256(payload).hexdigest()})
        for name in ("default.xex","XMediaFacade_default.xex","SpeechFacade_default.xex"):
            if (update/name).is_file(): shutil.copyfile(update/name,staged/name)
        if digest(source)!=ORIGINAL_ARCHIVE_SHA256: raise ValueError("Source changed during staging")
        (staged/"native-tuning-provenance.json").write_text(json.dumps({"schema":1,"source_archive_sha256":ORIGINAL_ARCHIVE_SHA256,"entries":records},indent=2)+"\n")
        staged.rename(output)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("update",type=Path);parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    try: stage(args.update,args.output)
    except (OSError,ValueError,KeyError,zipfile.BadZipFile) as error:
        diagnostic = error_diagnostic(error)
        parser.exit(1,"NATIVE_TUNING_ERROR "+json.dumps(diagnostic, ensure_ascii=True)+"\n")
    print(f"Prepared private native tuning donors in {args.output}")
