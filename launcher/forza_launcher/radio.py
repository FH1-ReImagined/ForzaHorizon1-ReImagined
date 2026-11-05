"""Player-built radio overlays. Independently authored for ReImagined.

Inputs are the player's FH1 files and local music. Original banks and music are
read-only; publication replaces only our own generated mod after a full build.
See docs/custom-radio.md for format evidence and remaining runtime qualification.
"""
import copy
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import mods, paths

MOD = "reimagined-radio"
SLOTS = ("Radio1", "Radio2", "Radio3")
TITLES = ("Horizon Bass Arena", "Horizon Pulse", "Horizon Rocks")
LOGOS = ("RS_BassArena", "RS_Pulse", "RS_2Eleven")
MAX_BANK = 0x7FFFFFFF
MARKER = ".reimagined-radio.json"
MAX_TAG = 100  # Characters in a custom song's artist or title (the stock names reach 47).
# The stock station logos in media/ui/textures/Horizon.zip, Hud/Radio/ (#678): texture,
# the slot it belongs to, and what it is. A custom logo replaces the slot's two textures.
STOCK_LOGOS = (("RS_BassArena", "Radio1", "station logo"), ("RS_Pulse", "Radio2", "station logo"),
               ("RS_2Eleven", "Radio3", "station logo"), ("Radio1", "Radio1", "station icon"),
               ("Radio2", "Radio2", "station icon"), ("Radio3", "Radio3", "station icon"),
               ("RS_Off", "", "radio off icon"))
# Leveling (#662). The stock station songs (R1-R3 in Radio_Music.fsb) measure a median
# -14.6 LUFS integrated, with true peaks at a median -4.4 dBTP and -2.8 dBTP at the 75th
# percentile. The game plays the radio at musicLevel 0.85 times the player's Radio volume
# (at most 1.0), adds the car and world sounds without a limiter, and the output stage
# clamps at full scale. A modern master (around -7 LUFS, peaks at 0 dBFS) leaves no room
# for that, so each song is turned down, or up, to the stock loudness with one linear
# gain: no compression. The gain stops where the song's true peak would pass the ceiling,
# so a quiet song with loud peaks is never boosted into clipping. The ceiling applies
# before encoding, half a decibel under -3 dBTP to leave room for the MP3 encoder.
LEVEL_TARGET = -14.5  # LUFS
LEVEL_CEILING = -3.5  # dBTP
LEVELING = {"target": LEVEL_TARGET, "ceiling": LEVEL_CEILING}


class RadioError(ValueError):
    pass


def read_radio_xml(data):
    # FH1's XML reader accepts bare '&' in artist names (e.g. Chase & Status).
    # Escape only those, preserving already escaped and numeric entities.
    data = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)", b"&amp;", data)
    # Another accepted native extension: an attribute name beginning with a digit.
    data = re.sub(rb"(?<=\s)3dPanLevel(?=\s*=)", b"_radio_3dPanLevel", data)
    try:
        return ET.ElementTree(ET.fromstring(data))
    except ET.ParseError as exc:
        raise RadioError(f"The game's radio XML could not be read: {exc}") from exc


def write_radio_xml(tree, path):
    data = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    Path(path).write_bytes(data.replace(b" _radio_3dPanLevel=", b" 3dPanLevel="))


def settings(cfg):
    value = cfg.d.get("custom_radio", {})
    if not isinstance(value, dict):
        raise RadioError("Radio settings are invalid. Restore launcher.json from its backup.")
    level = value.get("level", True)
    if not isinstance(level, bool):
        raise RadioError("Radio settings are invalid. Restore launcher.json from its backup.")
    out = {"version": 1, "ffmpeg": str(value.get("ffmpeg", "")), "level": level, "stations": {}}
    stations = value.get("stations", {})
    if not isinstance(stations, dict):
        raise RadioError("Radio station settings are invalid.")
    for slot, station in stations.items():
        if slot not in SLOTS or not isinstance(station, dict):
            raise RadioError("Unknown radio station in launcher.json.")
        name = str(station.get("name", "")).strip()
        mode = station.get("mode", "replace")
        songs = station.get("songs", [])
        if (not name or len(name) > 64 or any(ord(c) < 32 for c in name)
                or mode not in ("add", "replace") or not isinstance(songs, list)
                or len(songs) > 200 or any(not isinstance(s, str) for s in songs)):
            raise RadioError(f"{slot}: choose a name (1–64 characters), Add or Replace, and at most 200 songs.")
        out["stations"][slot] = {"name": name, "mode": mode, "songs": list(songs),
                                  "logo": str(station.get("logo", "")),
                                  "tags": _tags(slot, station.get("tags", {}), songs)}
    return out


def _tags(slot, value, songs):
    """Per-song artist and title for the HUD (#678): {path: {"artist", "title"}}.

    Empty fields use the defaults (the file name and the station name), and entries
    for songs no longer in the playlist are dropped. Configs without tags still load.
    """
    if not isinstance(value, dict):
        raise RadioError(f"{slot}: the song names are invalid. Restore launcher.json from its backup.")
    out = {}
    for song, tag in value.items():
        if not isinstance(tag, dict):
            raise RadioError(f"{slot}: the song names are invalid. Restore launcher.json from its backup.")
        clean = {}
        for field in ("artist", "title"):
            text = tag.get(field, "")
            if not isinstance(text, str):
                raise RadioError(f"{slot}: the song names are invalid. Restore launcher.json from its backup.")
            text = text.strip()
            if len(text) > MAX_TAG or any(ord(c) < 32 for c in text):
                raise RadioError(f"{slot}: a song's artist or title is longer than {MAX_TAG} characters "
                                 "or has a line break.")
            if text:
                clean[field] = text
        if clean and song in songs:
            out[song] = clean
    return out


def song_names(station, song):
    """(title, artist) the in-game radio HUD shows for a custom song."""
    tag = station.get("tags", {}).get(song, {})
    return tag.get("title") or Path(song).stem, tag.get("artist") or station["name"]


def ffprobe_path(ffmpeg=""):
    """FFprobe beside the chosen FFmpeg, else on PATH; None when there is none."""
    if ffmpeg:
        exe = Path(ffmpeg)
        sibling = exe.with_name("ffprobe" + exe.suffix)
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def read_tags(song, ffprobe):
    """The artist and title tags of a music file, read with FFprobe; {} when unavailable."""
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json",
             str(Path(song).resolve())], capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        probe = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if not isinstance(probe, dict):
        return {}
    # Container tags first (MP3, M4A), then stream tags (Ogg, Opus); keys differ in case.
    sources = [probe.get("format", {})] + list(probe.get("streams", []) or [])
    out = {}
    for source in sources:
        tags = source.get("tags", {}) if isinstance(source, dict) else {}
        if not isinstance(tags, dict):
            continue
        for key, text in tags.items():
            field = str(key).lower()
            if field in ("artist", "title") and field not in out and isinstance(text, str):
                text = " ".join(text.split())[:MAX_TAG]
                if text:
                    out[field] = text
    return out


@dataclass
class Bank:
    header: bytes
    samples: list
    padding: bytes
    data_offset: int
    data_size: int


def read_bank(path):
    """Read only the bounded FSB4 index, keeping every original byte and index."""
    path = Path(path)
    with path.open("rb") as f:
        header = f.read(48)
        if len(header) != 48 or header[:4] != b"FSB4":
            raise RadioError("Radio_Music.fsb is not a supported FSB4 bank. Check your extracted FH1 files.")
        count, index_size, data_size, version, flags = struct.unpack_from("<5I", header, 4)
        if (version != 0x40000 or flags != 0x40 or not 1 <= count <= 4096
                or not count * 80 <= index_size <= 16 * 1024**2
                or 48 + index_size + data_size != path.stat().st_size):
            raise RadioError("The radio bank has unsupported flags or inconsistent sizes; no files were changed.")
        samples, total = [], 0
        end = 48 + index_size
        for _ in range(count):
            start = f.tell()
            raw = f.read(2)
            size = struct.unpack("<H", raw)[0] if len(raw) == 2 else 0
            if size < 80 or start + size > end:
                raise RadioError("The radio bank sample index is truncated.")
            raw += f.read(size - 2)
            length = struct.unpack_from("<I", raw, 36)[0]
            if length % 16:
                raise RadioError("The radio bank uses an unsupported sample alignment.")
            total += length
            samples.append(raw)
        padding = f.read(end - f.tell())
        if total != data_size:
            raise RadioError("The radio bank's sample sizes do not match its audio data.")
        return Bank(header, samples, padding, end, data_size)


def sample_header(name, frames, size):
    """FSB4 MPEG1 Layer III, stereo 48 kHz; three native SYNC points in samples."""
    if not name.isascii() or len(name) > 29 or not 0 < frames < 0xFFFFFFFF:
        raise RadioError("Invalid generated radio sample identity or duration.")
    header = bytearray(868)
    struct.pack_into("<H", header, 0, len(header))
    header[2:2 + len(name)] = name.encode("ascii")
    struct.pack_into("<6I4H2fI", header, 32,
                     frames, size, 0, frames - 1, 0x90000200, 48000,
                     255, 128, 128, 2, 1.0, 10000.0, len(header))
    header[80:84] = b"SYNC"
    struct.pack_into("<I", header, 84, 3)
    for i, (position, label) in enumerate(((0, b"SongStart"), (0, b"EventStart"),
                                           (frames - 1, b"IdentStart"))):
        at = 88 + i * 260
        struct.pack_into("<I", header, at, position)
        header[at + 4:at + 4 + len(label)] = label
    return bytes(header)


def mp3_samples(path):
    """Validate our exact FFmpeg output: 160 kb/s, 48 kHz MPEG1 Layer III frames."""
    count = 0
    with Path(path).open("rb") as f:
        while raw := f.read(480):
            # Sync, MPEG1, Layer III, bitrate index 10, 48 kHz, no padding.
            if (len(raw) != 480 or raw[0] != 255 or raw[1] & 0xFE != 0xFA
                    or raw[2] & 0xFE != 0xA4 or raw[3] >> 6 == 3):
                raise RadioError("FFmpeg produced an unsupported MP3 stream. Use an FFmpeg build with libmp3lame.")
            count += 1
    if not count:
        raise RadioError("The selected song contains no audio.")
    return count * 1152


def _run(cmd, source, cancelled, verb="encode"):
    """Run FFmpeg with the cancel check and the five-minute limit; return its last log bytes."""
    # A file keeps stderr bounded in memory and avoids a pipe deadlock.
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 300
            while proc.poll() is None:
                if cancelled():
                    raise RadioError("Radio build cancelled. Your previous stations are unchanged.")
                if time.monotonic() > deadline:
                    raise RadioError(f"Encoding timed out: {source.name}. Try a shorter or different file.")
                time.sleep(.05)
            if proc.returncode:
                log.seek(0)
                raise RadioError(f"Could not {verb} {source.name}: " + log.read(4096).decode("utf-8", "replace"))
            log.seek(max(0, log.tell() - 4096))
            return log.read()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def _input(source, ffmpeg, verbosity):
    # Both passes see the same audio: first stream, at most 30 minutes, stereo 48 kHz.
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-nostats", "-v", verbosity,
            "-i", str(source.resolve()), "-map", "0:a:0", "-vn", "-t", "1800", "-ac", "2", "-ar", "48000"]


def measure(source, ffmpeg, cancelled=lambda: False):
    """First pass: integrated loudness (LUFS) and true peak (dBTP), EBU R128.

    FFmpeg's ebur128 filter is the meter loudnorm uses, without loudnorm's 192 kHz
    resampling: about a second per song instead of eight. metadata=1 moves its
    per-frame lines to the verbose level, so only the summary is logged.
    """
    source = Path(source)
    cmd = _input(source, ffmpeg, "info") + ["-af", "ebur128=peak=true:metadata=1", "-f", "null", "-"]
    log = _run(cmd, source, cancelled, "measure").decode("utf-8", "replace")
    summary = log[log.rfind("Summary:"):]
    loudness = re.search(r"\bI:\s*(-?(?:inf|[0-9.]+)) LUFS", summary)
    peak = re.search(r"\bPeak:\s*(-?(?:inf|[0-9.]+)) dBFS", summary)
    if "Summary:" not in log or not loudness or not peak:
        raise RadioError(f"Could not measure the loudness of {source.name}. "
                         "Turn off song leveling on the Radio page or use another FFmpeg build.")
    return float(loudness.group(1)), float(peak.group(1))


def level_gain(loudness, peak):
    """Linear gain in dB toward LEVEL_TARGET that keeps the true peak under LEVEL_CEILING."""
    if not math.isfinite(loudness) or loudness <= -70:
        return 0.0  # Silence or no measurable programme: leave it alone.
    gain = LEVEL_TARGET - loudness
    if math.isfinite(peak):
        gain = min(gain, LEVEL_CEILING - peak)
    return round(max(-40.0, min(40.0, gain)), 2)


def encode(source, output, ffmpeg, cancelled=lambda: False, level=True):
    source, output = Path(source), Path(output)
    if not source.is_file():
        raise RadioError(f"Song not found: {source}. Remove it from the playlist or choose its new location.")
    gain = [] if not level else ["-af", f"volume={level_gain(*measure(source, ffmpeg, cancelled)):.2f}dB"]
    cmd = _input(source, ffmpeg, "error") + gain + [
        "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", "160k",
        "-write_xing", "0", "-id3v2_version", "0", "-f", "mp3", str(output)]
    _run(cmd, source, cancelled)
    return mp3_samples(output)


def stale(cfg):
    """True when the published stations were built with other leveling than the current setting."""
    try:
        wanted = LEVELING if settings(cfg)["level"] else None
        marker = json.loads((mods.mods_dir(cfg) / MOD / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return not isinstance(marker, dict) or marker.get("format", 1) < 2 or marker.get("leveling") != wanted


def write_bank(source, output, additions, cancelled=lambda: False):
    bank = read_bank(source)
    added = sum(p.stat().st_size for _, p in additions)
    headers = b"".join(h for h, _ in additions)
    padding = bank.padding + bytes((-len(headers)) % 16)
    index_growth = len(headers) + len(padding) - len(bank.padding)
    if bank.data_offset + index_growth + bank.data_size + added > MAX_BANK:
        raise RadioError("The new radio bank exceeds the game's 2 GB limit. Remove some songs.")
    header = bytearray(bank.header)
    struct.pack_into("<3I", header, 4, len(bank.samples) + len(additions),
                     bank.data_offset - 48 + index_growth, bank.data_size + added)
    with Path(output).open("xb") as out, Path(source).open("rb") as original:
        out.write(header)
        for sample in bank.samples:
            out.write(sample)
        out.write(headers)
        out.write(padding)
        original.seek(bank.data_offset)
        while block := original.read(1024**2):
            if cancelled():
                raise RadioError("Radio build cancelled. Your previous stations are unchanged.")
            out.write(block)
        for _, song in additions:
            with song.open("rb") as audio:
                shutil.copyfileobj(audio, out, 1024**2)
    read_bank(output)


def patch_strings(data, replacements):
    """Rebuild LSB2 text section, preserving ids, keys, order and other sections."""
    if data[:4] != b"LSB2" or len(data) < 28:
        raise RadioError("Unsupported radio string table.")
    n = struct.unpack_from(">I", data, 16)[0]
    if not 2 <= n <= 16 or len(data) < 20 + n * 4:
        raise RadioError("Radio string table has an unsupported section directory.")
    offsets = struct.unpack_from(f">{n}I", data, 20)
    a, b = offsets[:2]
    if b == 0:  # The game's LOC placeholder has no identifier section.
        return data
    if not 20 + n * 4 <= a < b < len(data):
        raise RadioError("Radio string table offsets are invalid.")
    count = struct.unpack_from(">I", data, a + 4)[0]
    pool = a + 8 + 6 * (count + 1)
    if pool > b:
        raise RadioError("Radio string table entries are truncated.")
    ids, idcount = {}, struct.unpack_from(">I", data, b + 4)[0]
    idpool = b + 8 + idcount * 8
    if idpool > len(data):
        raise RadioError("Radio string table ids are truncated.")
    for i in range(idcount):
        key, _, offset = struct.unpack_from(">HHI", data, b + 8 + i * 8)
        start = idpool + offset
        end = data.find(b"\0", start)
        if start < idpool or end < 0:
            raise RadioError("Radio string identifier is invalid.")
        ids[data[start:end].decode("ascii")] = key
    by_key = {ids[k]: v for k, v in replacements.items() if k in ids}
    entries, text = bytearray(), bytearray()
    for i in range(count):
        key, offset = struct.unpack_from(">HI", data, a + 8 + i * 6)
        pos = pool + offset * 2
        end = pos
        while end + 1 < b and data[end:end + 2] != b"\0\0":
            end += 2
        if pos < pool or end + 1 >= b:
            raise RadioError("Radio string text is truncated.")
        value = by_key.get(key)
        raw = value.encode("utf-16-be") + b"\0\0" if value is not None else data[pos:end + 2]
        entries += struct.pack(">HI", key, len(text) // 2)
        text += raw
    sentinel = struct.unpack_from(">H", data, a + 8 + count * 6)[0]
    entries += struct.pack(">HI", sentinel, len(text) // 2)
    section = struct.pack(">II", 8 + len(entries) + len(text), count) + entries + text
    prefix = bytearray(data[:a])
    delta = a + len(section) - b
    for i, offset in enumerate(offsets):
        if offset >= b:
            struct.pack_into(">I", prefix, 20 + i * 4, offset + delta)
    return bytes(prefix) + section + data[b:]


def logo_xds(filename, width=210, height=100):
    """Encode a fitted RGBA logo using this repo's Xbox tile helper."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QImageReader, QPainter
    paths.ensure_tools_importable()
    from xdc_decode import tiled_offset
    reader = QImageReader(str(filename))
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 16 * 1024**2:
        raise RadioError("Logo is unreadable or exceeds 16 megapixels. Choose a smaller PNG.")
    image = reader.read()
    if image.isNull():
        raise RadioError("Logo could not be decoded. Choose a PNG image.")
    # The native HUD draws track text over the bottom of its 210x100 logo slot.
    # Keep custom opaque artwork above those labels, as the stock artwork does.
    art_height = 72 if (width, height) == (210, 100) else height
    image = image.scaled(width, art_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QImage(width, height, QImage.Format_RGBA8888)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawImage((width - image.width()) // 2, (art_height - image.height()) // 2, image)
    painter.end()
    raw = bytes(canvas.constBits())
    pitch = (width + 31) & ~31
    pixels = bytearray(pitch * ((height + 31) & ~31) * 4)
    for y in range(height):
        for x in range(width):
            at = y * canvas.bytesPerLine() + x * 4
            r, g, b, a = raw[at:at + 4]
            offset = tiled_offset(x, y, pitch)
            if offset + 4 > len(pixels):
                raise RadioError("Logo tile size exceeded its texture allocation.")
            pixels[offset:offset + 4] = bytes((a, r, g, b))
    # TextureFetch word 0 bits 22..30 store row pitch in 32-texel units
    # (SDK TextureInfo::Prepare: fetch.pitch << 5). It must match the tiler.
    fetch0 = (0x820000FE & ~(0x1FF << 22)) | ((pitch // 32) << 22)
    header = struct.pack(">13I", 3, 1, 0, 0, 0, 0xFFFF0000, 0xFFFF0000,
                         fetch0, 0x86, ((height - 1) << 13) | (width - 1), 0xC14, 0, 0xA00)
    return header + pixels


def _updated(cfg):
    """A reader that prefers the title update's media.zip member over the original file."""
    paths.ensure_tools_importable()
    from extract_map_points import read_member
    from . import game as game_module
    from .config import render_toml
    install, _ = game_module.find_game(cfg)
    update = game_module.update_data_root(cfg, install, render_toml(cfg)) / "media.zip" if install else None
    extra = game_module.split_args(cfg.advanced.get("extra_args", ""))
    for i, arg in enumerate(extra):
        if arg.startswith("--update_data_root="):
            update = Path(arg.split("=", 1)[1]) / "media.zip"
        elif arg == "--update_data_root" and i + 1 < len(extra):
            update = Path(extra[i + 1]) / "media.zip"
    def updated(rel, original):
        if update and update.is_file():
            data = read_member(update, rel)
            if data is not None:
                return data
        return original()
    return updated


def _tiled(x, y, width, log2):
    """Xenos 2D tiled byte offset of element (x, y); width in elements, 2**log2 bytes each."""
    macro = ((y >> 5) * (width >> 5)) << (log2 + 7)
    micro = ((y & 6) << 2) << log2
    base = macro + ((micro & ~0xF) << 1) + (micro & 0xF) + ((y & 8) << (3 + log2)) + ((y & 1) << 4)
    macro = (x >> 5) << (log2 + 7)
    micro = (x & 7) << log2
    offset = base + macro + ((micro & ~0xF) << 1) + (micro & 0xF)
    return (((offset & ~0x1FF) << 3) + ((offset & 0x1C0) << 2) + (offset & 0x3F)
            + ((y & 16) << 7) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6))


def _rgb565(value):
    r, g, b = value >> 11, (value >> 5) & 63, value & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _dxt_colors(block, four):
    c0, c1, bits = struct.unpack_from("<HHI", block)
    a, b = _rgb565(c0), _rgb565(c1)
    if four or c0 > c1:
        palette = [a + (255,), b + (255,), tuple((2 * p + q) // 3 for p, q in zip(a, b)) + (255,),
                   tuple((p + 2 * q) // 3 for p, q in zip(a, b)) + (255,)]
    else:  # DXT1's three-colour mode: index 3 is transparent black.
        palette = [a + (255,), b + (255,), tuple((p + q) // 2 for p, q in zip(a, b)) + (255,), (0, 0, 0, 0)]
    return [palette[(bits >> 2 * i) & 3] for i in range(16)]


def _dxt5_alpha(block):
    a0, a1 = block[0], block[1]
    bits = int.from_bytes(block[2:8], "little")
    if a0 > a1:
        palette = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        palette = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [palette[(bits >> 3 * i) & 7] for i in range(16)]


# Xenos texture formats the radio HUD uses: fetch format -> (name, alpha, block bytes).
# The *_AS_16_16_16_16 variants (51, 53) store the same blocks.
XDS_FORMATS = {6: ("A8R8G8B8 (uncompressed)", "8-bit", 0), 18: ("DXT1 (BC1)", "1-bit", 8),
               51: ("DXT1 (BC1)", "1-bit", 8), 20: ("DXT5 (BC3)", "8-bit", 16),
               53: ("DXT5 (BC3)", "8-bit", 16)}


def decode_xds(data):
    """Decode a tiled 2D HUD texture to (width, height, RGBA bytes, format name, alpha kind)."""
    if not 52 < len(data) <= 32 * 1024**2:
        raise RadioError("The logo texture is empty or too large.")
    words = struct.unpack_from(">13I", data)
    fetch0, fetch1, dims = words[7], words[8], words[9]
    fmt, endian = fetch1 & 0x3F, (fetch1 >> 6) & 3
    if fmt not in XDS_FORMATS or not fetch0 >> 31:
        raise RadioError(f"Unsupported logo texture format {fmt}.")
    name, alpha, block = XDS_FORMATS[fmt]
    width, height = (dims & 0x1FFF) + 1, ((dims >> 13) & 0x1FFF) + 1
    pitch = ((fetch0 >> 22) & 0x1FF) * 32
    if width * height > 4 * 1024**2 or pitch < width:
        raise RadioError("The logo texture has invalid dimensions.")
    pixels = bytearray(data[52:])
    if endian == 1:  # 8-in-16: swap the bytes of every 16-bit word.
        pixels[0::2], pixels[1::2] = pixels[1::2], pixels[0::2]
    rgba = bytearray(width * height * 4)
    if not block:
        for y in range(height):
            for x in range(width):
                at = _tiled(x, y, pitch, 2)
                if at + 4 > len(pixels):
                    raise RadioError("The logo texture is truncated.")
                a, r, g, b = pixels[at:at + 4]
                rgba[(y * width + x) * 4:(y * width + x + 1) * 4] = bytes((r, g, b, a))
        return width, height, bytes(rgba), name, alpha
    log2 = 3 if block == 8 else 4
    for by in range((height + 3) // 4):
        for bx in range((width + 3) // 4):
            at = _tiled(bx, by, pitch // 4, log2)
            if at + block > len(pixels):
                raise RadioError("The logo texture is truncated.")
            raw = pixels[at:at + block]
            if block == 8:
                texels = _dxt_colors(raw, False)
            else:
                texels = [c[:3] + (a,) for c, a in zip(_dxt_colors(raw[8:], True), _dxt5_alpha(raw))]
            for i, texel in enumerate(texels):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                if x < width and y < height:
                    rgba[(y * width + x) * 4:(y * width + x + 1) * 4] = bytes(texel)
    return width, height, bytes(rgba), name, alpha


def _art_bounds(width, height, rgba):
    """Bounding box of the visible (alpha > 0) pixels, or None when nothing is visible."""
    xs, ys = [], []
    for y in range(height):
        row = rgba[y * width * 4 + 3:(y + 1) * width * 4:4]
        visible = [x for x, a in enumerate(row) if a]
        if visible:
            xs += (visible[0], visible[-1])
            ys.append(y)
    if not ys:
        return None
    return {"x": min(xs), "y": ys[0], "width": max(xs) - min(xs) + 1, "height": ys[-1] - ys[0] + 1}


def export_stock_logos(cfg, folder):
    """Write the game's own station logos as PNGs plus stock-logos.json to folder (#678).

    They come from the player's local game files and go only to the folder chosen.
    Returns the number of logos written.
    """
    paths.ensure_tools_importable()
    from extract_map_points import read_member
    from xdc_decode import png_bytes
    textures = Path(cfg.game_files()) / "media/ui"
    archive = next((p for p in sorted(textures.glob("*/*.zip")) if p.parent.name.lower() == "textures"
                    and p.name.lower() == "horizon.zip"), None) if textures.is_dir() else None
    updated = _updated(cfg)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    notes = []
    for texture, slot, kind in STOCK_LOGOS:
        rel = f"Hud/Radio/{texture}.xds"
        data = updated(f"ui/textures/horizon/{rel}",
                       lambda: read_member(archive, rel) if archive else None)
        if data is None:
            continue
        width, height, rgba, fmt, alpha = decode_xds(data)
        (folder / f"{texture}.png").write_bytes(png_bytes(width, height, rgba))
        transparent = any(a < 255 for a in rgba[3::4])
        note = {"file": f"{texture}.png", "game_texture": f"media/ui/textures/Horizon.zip/{rel}",
                "kind": kind, "station": TITLES[SLOTS.index(slot)] if slot else "",
                "slot": slot, "width": width, "height": height, "format": fmt,
                "alpha": alpha if transparent else "opaque",
                "visible_area": _art_bounds(width, height, rgba)}
        if kind == "station logo":
            note["custom_logo"] = ("A custom PNG is scaled to fit 210x72 without cropping, centred, "
                                   "at the top of this 210x100 slot; the HUD draws the song's title "
                                   "and artist over the bottom 28 pixels.")
        elif kind == "station icon":
            note["custom_logo"] = "A custom PNG is also scaled to fit this 64x32 icon, centred."
        notes.append(note)
    if not notes:
        raise RadioError("The station logos were not found in your game files. "
                         "Check the game files folder on the Setup page.")
    (folder / "stock-logos.json").write_text(json.dumps({
        "about": "Forza Horizon's own radio station logos, exported from your game files for "
                 "reference. They are game assets: keep them to yourself.",
        "logos": notes}, indent=2) + "\n", encoding="utf-8")
    return len(notes)


def build(cfg, progress=lambda n, total, text: None, cancelled=lambda: False):
    """Build privately, then atomically switch the generated overlay while idle."""
    spec = settings(cfg)
    if not spec["stations"]:
        state = mods.state(cfg)
        previous = copy.deepcopy(cfg.d.get("mods"))
        cfg.d["mods"] = {"order": state["order"],
                         "disabled": list(dict.fromkeys(state["disabled"] + [MOD]))}
        try:
            cfg.save()
        except BaseException:
            if previous is None:
                cfg.d.pop("mods", None)
            else:
                cfg.d["mods"] = previous
            raise
        return "Original radio restored. Restart the game to hear it."
    ffmpeg = spec["ffmpeg"] or shutil.which("ffmpeg")
    if not ffmpeg or not Path(ffmpeg).is_file():
        raise RadioError("FFmpeg was not found. Install it, then use Locate FFmpeg on the Radio page.")
    game = Path(cfg.game_files())
    radio = game / "media/audio/radio"
    bank = read_bank(radio / "Radio_Music.fsb")
    updated = _updated(cfg)
    from extract_map_points import read_member
    system = read_radio_xml(updated("audio/radio/RadioSystem.xml", lambda: (radio / "RadioSystem.xml").read_bytes()))
    info = read_radio_xml(updated("audio/radio/RadioSoundbankInfo_Music.xml", lambda: (radio / "RadioSoundbankInfo_Music.xml").read_bytes()))
    target = mods.mods_dir(cfg) / MOD
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never take over a user-created folder with our reserved name.
    if target.is_symlink() or (target.exists() and not (target / MARKER).is_file()):
        raise RadioError(f"{target} already exists and was not generated by Radio. Move it before building.")
    backup = target.with_name("." + MOD + ".previous")
    if backup.exists():
        raise RadioError(f"A previous radio build remains at {backup}. Restore or move it before building again.")
    with tempfile.TemporaryDirectory(prefix=".radio-build-", dir=target.parent) as temp:
        work = Path(temp)
        staged = work / MOD
        audio = staged / "media/audio/radio"
        audio.mkdir(parents=True)
        additions, titles = [], {}
        total = sum(len(s["songs"]) for s in spec["stations"].values())
        done = 0
        for slot, station in spec["stations"].items():
            if not station["songs"]:
                raise RadioError(f"{station['name']}: add at least one song before building.")
            native = system.getroot().find(f"RadioStation[@name='{slot}']/Playlist")
            if native is None:
                raise RadioError(f"Your game files do not contain {slot}'s playlist.")
            if station["mode"] == "replace":
                for track in list(native):
                    native.remove(track)
            for song in station["songs"]:
                if cancelled():
                    raise RadioError("Radio build cancelled. Your previous stations are unchanged.")
                progress(done, total, f"Encoding {Path(song).name}")
                encoded = work / f"track-{done}.mp3"
                samples = encode(song, encoded, ffmpeg, cancelled, spec["level"])
                name = f"RI_{slot}_{done:04d}"
                additions.append((sample_header(name, samples, encoded.stat().st_size), encoded))
                title, artist = song_names(station, song)
                ET.SubElement(native, "MusicTrack", name=title, artist=artist,
                              fmodname=name, likelihoodDaytime="1", likelihoodMorning="1",
                              likelihoodEvening="1", likelihoodNight="1")
                entry = ET.SubElement(info.getroot(), "Bank", Name=name)
                duration = samples * 1000 // 48000
                ET.SubElement(entry, "SoundBankInfo", SoundBankIndex=str(len(bank.samples) + done),
                              OffsetEventStart="0", OffsetIdentStart=str(max(0, duration - 1)),
                              OffsetSongStart="0", OffsetStartNextTrack="0", FileLength=str(duration),
                              SyncpointIndexIdentStart="2", SyncpointStartNextTrack="-1")
                done += 1
            native.set("noRepeat", str(max(0, min(15, len(native) - 1))))
            i = SLOTS.index(slot)
            titles[f"IDS_RadioStation_Option_{i + 1}"] = station["name"]
            if station["logo"]:
                out = staged / f"media/ui/textures/horizon/Hud/Radio/{LOGOS[i]}.xds"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(logo_xds(station["logo"]))
                out.with_name(f"{slot}.xds").write_bytes(logo_xds(station["logo"], 64, 32))
        progress(done, total, "Writing radio bank")
        write_bank(radio / "Radio_Music.fsb", audio / "Radio_Music.fsb", additions, cancelled)
        write_radio_xml(system, audio / "RadioSystem.xml")
        write_radio_xml(info, audio / "RadioSoundbankInfo_Music.xml")
        for archive in sorted((game / "media/stringtables").glob("*.zip")):
            data = updated(f"stringtables/{archive.stem}/MyProfile.str",
                           lambda: read_member(archive, "MyProfile.str"))
            if data:
                out = staged / f"media/stringtables/{archive.stem}/MyProfile.str"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(patch_strings(data, titles))
        (staged / "mod.toml").write_text('name = "Custom radio"\nversion = "1"\n'
                                         'description = "Built locally from your game and music"\n', encoding="utf-8")
        (staged / MARKER).write_text(json.dumps({"format": 2, "settings": spec,
                                                 "leveling": LEVELING if spec["level"] else None}, ensure_ascii=False), encoding="utf-8")
        if cancelled():
            raise RadioError("Radio build cancelled. Your previous stations are unchanged.")
        if (Path(cfg.game_files()).resolve() != game.resolve()
                or mods.mods_dir(cfg).resolve() != target.parent.resolve()
                or settings(cfg) != spec):
            raise RadioError("Radio inputs or the data folder changed during the build. Nothing was published; build again.")
        old_state = copy.deepcopy(cfg.d.get("mods"))
        existed = target.exists()
        if existed:
            target.rename(backup)
        try:
            staged.rename(target)
            state = mods.state(cfg)
            cfg.d["mods"] = {"order": [f for f in state["order"] if f != MOD] + [MOD],
                             "disabled": [f for f in state["disabled"] if f != MOD]}
            cfg.save()
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            if existed:
                backup.rename(target)
            if old_state is None:
                cfg.d.pop("mods", None)
            else:
                cfg.d["mods"] = old_state
            raise
        cleanup_note = ""
        if existed:
            try:
                shutil.rmtree(backup)
            except OSError:
                # Publication succeeded. A hidden backup cannot become an active mod.
                cleanup_note = f" The old build could not be removed from {backup}; move it before rebuilding."
    return f"Built {len(spec['stations'])} station(s) with {done} custom song(s). Restart the game to hear them." + cleanup_note
