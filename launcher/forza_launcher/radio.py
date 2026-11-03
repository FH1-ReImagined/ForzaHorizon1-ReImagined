"""Player-built radio overlays. Independently authored for ReImagined.

Inputs are the player's FH1 files and local music. Original banks and music are
read-only; publication replaces only our own generated mod after a full build.
See docs/custom-radio.md for format evidence and remaining runtime qualification.
"""
import copy
import json
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
    out = {"version": 1, "ffmpeg": str(value.get("ffmpeg", "")), "stations": {}}
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
                                  "logo": str(station.get("logo", ""))}
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


def encode(source, output, ffmpeg, cancelled=lambda: False):
    source, output = Path(source), Path(output)
    if not source.is_file():
        raise RadioError(f"Song not found: {source}. Remove it from the playlist or choose its new location.")
    cmd = [str(ffmpeg), "-nostdin", "-v", "error", "-i", str(source.resolve()),
           "-map", "0:a:0", "-vn", "-map_metadata", "-1", "-t", "1800",
           "-ac", "2", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "160k",
           "-write_xing", "0", "-id3v2_version", "0", "-f", "mp3", str(output)]
    # A file keeps stderr bounded in memory and avoids a pipe deadlock.
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errors,
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
                errors.seek(0)
                raise RadioError(f"Could not encode {source.name}: " + errors.read(4096).decode("utf-8", "replace"))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    return mp3_samples(output)


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
    system = read_radio_xml(updated("audio/radio/RadioSystem.xml", lambda: (radio / "RadioSystem.xml").read_bytes()))
    info = read_radio_xml(updated("audio/radio/RadioSoundbankInfo_Music.xml", lambda: (radio / "RadioSoundbankInfo_Music.xml").read_bytes()))
    target = mods.mods_dir(cfg) / MOD
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never take over a user-created folder with our reserved name.
    marker = ".reimagined-radio.json"
    if target.is_symlink() or (target.exists() and not (target / marker).is_file()):
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
                samples = encode(song, encoded, ffmpeg, cancelled)
                name = f"RI_{slot}_{done:04d}"
                additions.append((sample_header(name, samples, encoded.stat().st_size), encoded))
                ET.SubElement(native, "MusicTrack", name=Path(song).stem, artist=station["name"],
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
        (staged / marker).write_text(json.dumps({"format": 1, "settings": spec}, ensure_ascii=False), encoding="utf-8")
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
