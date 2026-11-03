"""Make HD pack (#384, docs/texture_packs.md): upscales the player's own texture dump on this PC
into the texture pack textures/hd-pack. Nothing of the game is downloaded or shared: the dump
is the player's, the pack stays in their data folder.

The upscaler is Real-ESRGAN's ncnn-Vulkan build (any Vulkan GPU, no Python or CUDA), from its
official release (the program MIT, the realesrgan-x4plus model BSD-3-Clause), downloaded on
demand, checked against its pinned SHA-256 and unpacked into <data>/tools. The model upscales 4x;
2x is its output halved (box filter), which keeps its detail.

Only colour textures go through it (index.tsv names each dumped texture's format): the
single- and two-channel ones (DXT5A/BC4 masks, DXN/BC5 normal maps, R8, RG8) and small ones are
skipped, and so are colour textures that look like normal maps or data maps (channels used as
masks: red and green blend maps, black and white). Alpha is kept apart: the
transparent texels' colour is filled from their neighbours before upscaling (so cutout edges do
not turn dark), and the alpha channel is scaled smoothly on its own. The output's colours are
matched to the original's (the model shifts them a little). Each texture is written as
hd-pack/<fingerprint>.dds, compressed by forza-texcompress (texture_compress.py: BC7, or BC1 where
it keeps the texture nearly as well, with the mip levels; a quarter of the graphics memory of a
PNG, #413), or as hd-pack/<fingerprint>.png when the game was built without it (the game then
makes the mips). Each goes through a temporary file, so a stopped run resumes where it stopped."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from array import array
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import cleanenv, fetch, pins, texture_compress, texture_packs

VERSION = "20220424"             # Real-ESRGAN v0.2.5.0's ncnn-Vulkan build
MODEL = "realesrgan-x4plus"
MODEL_SCALE = 4
TOOL_NAME = f"realesrgan-ncnn-vulkan-{VERSION}"
RELEASE_URL = "https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0"
PROGRAM_LICENCE_URL = "https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/blob/master/LICENSE"   # MIT
MODEL_LICENCE_URL = "https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE"                # BSD-3-Clause
# 4x is offered on cards with at least this much of their own memory: a compressed 4x pack takes
# about 1.7 GB of graphics memory (as much as an uncompressed 2x one), an uncompressed one 6.4 GB.
MIN_VRAM_GB_4X = 6
MIN_VRAM_GB_4X_PNG = 10
SCALES = (2, 4)
MAX_SIDE = 4096                  # an output side at most this long (a lower factor for big ones)
MIN_SIDE = 32                    # textures with a shorter side are left as they are
MIN_PIXELS = 64 * 64
SKIP_FORMATS = {"BC4": "masks", "BC5": "normal maps", "R8": "masks", "RG8": "two-channel"}
BATCH_PIXELS = 4 << 20           # source pixels per upscaler run (progress and cancel steps)
BATCH_MAX = 48
# Measured on a race's dump (docs/texture_packs.md): PNG bytes per output pixel (1.19 at 2x,
# 1.07 at 4x), and the whole job's time per source megapixel on a Radeon RX 9060 XT (544 s for
# 758 textures, 81 megapixels, at 2x; 590 s at 4x). Compressing runs on the CPU while the
# graphics card upscales the next batch (8 threads of a Ryzen 7 7800X3D: 29 s for the 2x pack,
# 116 s for the 4x one), so it adds little.
PNG_BYTES_PER_PIXEL = 1.15
SECONDS_PER_MEGAPIXEL = 7.0


def platform_key() -> str:
    if sys.platform == "win32":
        return "realesrgan-windows"
    if sys.platform == "darwin":
        return "realesrgan-macos"
    return "realesrgan-linux"


def _program_name() -> str:
    return "realesrgan-ncnn-vulkan.exe" if sys.platform == "win32" else "realesrgan-ncnn-vulkan"


def _wanted_files() -> list:
    """The archive's files the launcher keeps (not its demo pictures and video)."""
    files = [_program_name(), f"models/{MODEL}.bin", f"models/{MODEL}.param"]
    if sys.platform == "win32":
        files.append("vcomp140.dll")   # Microsoft's OpenMP runtime the program needs
    return files


def tool_dir(cfg) -> Path:
    return cfg.data_home() / "tools" / TOOL_NAME


def work_dir(cfg) -> Path:
    return cfg.data_home() / "tools" / "hd-pack-work"


def pack_dir(cfg) -> Path:
    return texture_packs.textures_dir(cfg) / texture_packs.HD_PACK


def dump_dir(cfg) -> Path:
    return texture_packs.textures_dir(cfg) / texture_packs.DUMP


# --- the upscaler ------------------------------------------------------------------------------

MANIFEST = "installed.json"


def installed(cfg) -> bool:
    """Whether the upscaler is unpacked (every file there with its recorded size)."""
    try:
        info = json.loads((tool_dir(cfg) / MANIFEST).read_text(encoding="utf-8"))
        files = info["files"]
        return info.get("archive") == pins.DOWNLOADS[platform_key()][1] and all(
            (tool_dir(cfg) / name).stat().st_size == f["size"] for name, f in files.items())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify(cfg):
    """Checks every file of the upscaler against the SHA-256 recorded when it was unpacked from
    the pinned archive; raises RuntimeError when one changed (it is not run then)."""
    try:
        info = json.loads((tool_dir(cfg) / MANIFEST).read_text(encoding="utf-8"))
        files = info["files"]
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError(f"The upscaler is not installed ({e}).") from None
    for name, f in files.items():
        if fetch.sha256_of(tool_dir(cfg) / name) != f["sha256"]:
            raise RuntimeError(f"The upscaler's {name} changed since it was downloaded: download it again.")


def install(cfg, progress=lambda d, t: None, cancelled=lambda: False) -> Path:
    """Downloads the pinned release archive (SHA-256 checked by fetch.download), unpacks the
    program and its model into tool_dir, and deletes the archive."""
    key = platform_key()
    cache = cfg.data_home() / "downloads"
    archive = fetch.download(key, cache, progress, cancelled)
    dest = tool_dir(cfg)
    staging = dest.with_name(dest.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    files = {}
    try:
        with zipfile.ZipFile(archive) as z:
            names = {i.filename: i for i in z.infolist()}
            for name in _wanted_files():
                info = names.get(name)
                if info is None:
                    raise RuntimeError(f"{archive.name} has no {name}")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with z.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        h.update(chunk)
                        out.write(chunk)
                files[name] = {"size": target.stat().st_size, "sha256": h.hexdigest()}
        if sys.platform != "win32":
            os.chmod(staging / _program_name(), 0o755)
        (staging / MANIFEST).write_text(json.dumps({
            "archive": pins.DOWNLOADS[key][1], "url": pins.DOWNLOADS[key][0], "files": files}, indent=1),
            encoding="utf-8")
        (staging / "SOURCE.txt").write_text(
            f"Real-ESRGAN ncnn-Vulkan {VERSION}, from {pins.DOWNLOADS[key][0]}\n"
            f"(release {RELEASE_URL}), SHA-256 {pins.DOWNLOADS[key][1]}.\n"
            "The program: MIT License, Copyright (c) 2021 Xintao Wang, and (c) 2019 nihui "
            "(https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan).\n"
            f"The model {MODEL}: BSD 3-Clause License, Copyright (c) 2021 Xintao Wang "
            "(https://github.com/xinntao/Real-ESRGAN).\n", encoding="utf-8")
        shutil.rmtree(dest, ignore_errors=True)
        staging.rename(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    try:
        archive.unlink()
    except OSError:
        pass
    return dest


def remove_tool(cfg):
    shutil.rmtree(tool_dir(cfg), ignore_errors=True)


# --- what to upscale ---------------------------------------------------------------------------

@dataclass
class Item:
    fingerprint: str
    width: int
    height: int
    out_width: int
    out_height: int


def min_vram_gb_4x(compressed: bool) -> int:
    return MIN_VRAM_GB_4X if compressed else MIN_VRAM_GB_4X_PNG


@dataclass
class Plan:
    scale: int
    todo: list = field(default_factory=list)       # Items still to make
    done: int = 0                                  # already in the pack
    skipped: Counter = field(default_factory=Counter)  # reason -> textures
    dumped: int = 0                                # textures in the dump
    out_pixels: int = 0                            # of todo and done
    replaces: int = 0                              # the scale of the pack it replaces (0: none)
    compressed: bool = False                       # written as BC7/BC1 DDS (else PNG)
    png_done: int = 0                              # of done, PNGs (compressed first when compressing)

    @property
    def count(self) -> int:
        return len(self.todo) + self.done

    def disk_bytes(self) -> int:
        if self.compressed:   # a DDS file is its graphics memory and a header
            return self.gpu_bytes()
        return int(self.out_pixels * PNG_BYTES_PER_PIXEL)

    def gpu_bytes(self) -> int:
        """Graphics memory of the whole pack: BC7/BC1 with mips as they are in the DDS files, or
        RGBA8 with mips, as the game uploads a PNG."""
        if self.compressed:
            return texture_compress.gpu_bytes(self.out_pixels)
        return self.out_pixels * 4 * 4 // 3

    def seconds(self) -> float:
        """A rough time for the textures still to make, on a mid-range card."""
        pixels = sum(i.width * i.height for i in self.todo)
        return pixels / 1e6 * SECONDS_PER_MEGAPIXEL


def read_index(dump: Path) -> dict:
    """index.tsv of a dump: fingerprint -> (game format, width, height, file format)."""
    out = {}
    try:
        lines = (dump / "index.tsv").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        cols = line.split("\t")
        if len(cols) < 5 or not texture_packs.FINGERPRINT.match(cols[0]):
            continue
        try:
            w, h = (int(v) for v in cols[2].lower().split("x"))
        except ValueError:
            continue
        out[cols[0].lower()] = (cols[1], w, h, cols[4].strip())
    return out


def out_size(width: int, height: int, scale: int):
    """The upscaled size: `scale` times, or a lower power of two when a side would pass
    MAX_SIDE; None when not even 2x fits."""
    while scale >= 2 and max(width, height) * scale > MAX_SIDE:
        scale //= 2
    return (width * scale, height * scale) if scale >= 2 else None


LEFT_OUT = ".left-out.tsv"   # in the pack: textures the job found not to be colour, and why


def read_left_out(pack: Path) -> dict:
    """fingerprint -> why the job left it out ("normal map", "data map")."""
    try:
        lines = (pack / LEFT_OUT).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return dict(line.split("\t", 1) for line in lines if "\t" in line)


def plan(cfg, scale: int, compressed=None) -> Plan:
    """What making the pack at `scale` does. A pack made at another scale is replaced as a whole:
    nothing of it counts as made, whether its files are DDS or PNG (Plan.replaces names its
    scale). `compressed`: whether it writes DDS files (default: whether this PC has the
    compressor); a PNG made at this scale then counts as made and is compressed first."""
    p = Plan(scale=scale, compressed=texture_compress.available(cfg) if compressed is None else compressed)
    dump, pack = dump_dir(cfg), pack_dir(cfg)
    index = read_index(dump)
    left_out = read_left_out(pack)
    before = existing_scale(cfg)
    same_scale = before in (None, scale)
    p.replaces = 0 if same_scale else before
    p.dumped = len(index)
    for fp, (game_format, w, h, file_format) in sorted(index.items()):
        if fp in left_out:
            p.skipped[left_out[fp] + "s"] += 1
            continue
        if file_format in SKIP_FORMATS or game_format == "k_DXN":
            p.skipped[SKIP_FORMATS.get(file_format, "normal maps")] += 1
            continue
        if min(w, h) < MIN_SIDE or w * h < MIN_PIXELS:
            p.skipped["small"] += 1
            continue
        size = out_size(w, h, scale)
        if size is None:
            p.skipped["already large"] += 1
            continue
        if not (dump / f"{fp}.png").is_file():
            p.skipped["without a .png in the dump"] += 1
            continue
        p.out_pixels += size[0] * size[1]
        if not same_scale:
            p.todo.append(Item(fp, w, h, *size))
        elif (pack / f"{fp}.dds").is_file():
            p.done += 1
        elif (pack / f"{fp}.png").is_file():
            p.done += 1
            p.png_done += 1
        else:
            p.todo.append(Item(fp, w, h, *size))
    return p


def existing_scale(cfg):
    """The scale the HD pack was made at (its pack.toml), or None."""
    try:
        text = (pack_dir(cfg) / texture_packs.MANIFEST).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("scale"):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


# --- the images (Qt's QImage: no other imaging library in the launcher) ------------------------

_HOLE = bytes([0] + [255] * 255)  # alpha byte -> 0 for a hole, 255 otherwise


def _qimage():
    from PySide6.QtGui import QImage
    return QImage


def _rgba(img):
    """An image as RGBA8888 bytes (rows are 4 * width bytes: no padding)."""
    QImage = _qimage()
    img = img.convertToFormat(QImage.Format_RGBA8888)
    return img, bytes(img.constBits())[: img.width() * img.height() * 4]


def _from_rgba(data, width, height):
    QImage = _qimage()
    return QImage(data, width, height, width * 4, QImage.Format_RGBA8888).copy()


def not_colour(rgba: bytes) -> str:
    """Why a texture's RGB is data, not colour ("" when it is colour), from up to 128x128 of its
    texels: "normal map" for a tangent-space normal map (red and green around the middle, blue
    high), "data map" when its channels are masks (most values at 0 or 255 and many texels pure
    red, green or yellow: the game's blend maps; a GAN would change the weights)."""
    n = len(rgba) // 4
    if not n:
        return ""
    r, g, b = (sum(rgba[c::4]) / n for c in range(3))
    if abs(r - 128) < 28 and abs(g - 128) < 28 and b > 190:
        return "normal map"
    step = max(1, n // 16384)
    texels = [rgba[i:i + 4] for i in range(0, len(rgba), 4 * step)]
    texels = [t for t in texels if t[3]] or texels
    saturated = sum(1 for t in texels if max(t[:3]) - min(t[:3]) > 150) / len(texels)
    extreme = sum(1 for t in texels for c in t[:3] if c < 16 or c > 239) / (3 * len(texels))
    if saturated > 0.25 and extreme > 0.6:
        return "data map"
    return ""


def _fill_holes(rgba: bytes, width: int, height: int) -> bytes:
    """The colour under transparent black texels (BC1's cut-out texels decode to black; most
    tools leave DXT5's black too) filled from the texels around them (push-pull over a mip
    pyramid, premultiplied), so the upscaler and the GPU's filtering do not darken the edges.
    Every other texel keeps its colour exactly; alpha becomes 255 (the upscaler gets RGB)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    QImage = _qimage()
    data = bytearray(rgba)
    if not rgba[3::4].count(0):  # no transparent texel: nothing to fill
        data[3::4] = b"\xff" * (width * height)
        return bytes(data)
    words = array("I")
    words.frombytes(rgba)
    coverage = bytes(0 if v == 0 else 255 for v in words)
    if coverage.count(0) in (0, len(coverage)):
        data[3::4] = b"\xff" * (width * height)
        return bytes(data)
    data[3::4] = coverage
    levels = [_from_rgba(bytes(data), width, height).convertToFormat(QImage.Format_ARGB32_Premultiplied)]
    while levels[-1].width() > 1 or levels[-1].height() > 1:
        last = levels[-1]
        levels.append(last.scaled(max(1, last.width() // 2), max(1, last.height() // 2),
                                  Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    fill = levels[-1]
    for level in reversed(levels[:-1]):
        up = fill.scaled(level.width(), level.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        fill = level.copy()
        painter = QPainter(fill)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationOver)
        painter.drawImage(0, 0, up)
        painter.end()
    # Un-premultiplied: the fill's colour whatever its coverage; a covered texel (alpha 255
    # through the pyramid) comes back exactly as it was.
    out = bytearray(_rgba(fill)[1])
    out[3::4] = b"\xff" * (width * height)
    return bytes(out)


def _stats(rgba: bytes, alpha=None, samples=65536):
    """Per channel (mean, standard deviation) of RGBA bytes, from up to `samples` texels spread
    over the image; with `alpha` (a byte per texel), only the texels it covers."""
    n = len(rgba) // 4
    step = max(1, n // samples)
    picked = [i for i in range(0, n, step) if alpha is None or alpha[i]] or list(range(0, n, step))
    out = []
    for c in range(3):
        counts = Counter(rgba[4 * i + c] for i in picked)
        mean = sum(v * k for v, k in counts.items()) / len(picked)
        var = sum(k * (v - mean) ** 2 for v, k in counts.items()) / len(picked)
        out.append((mean, var ** 0.5))
    return out


def match_colours(out: bytearray, source: bytes, out_small: bytes, alpha=None):
    """Gives `out` (RGBA bytes, in place) the per-channel mean and contrast of `source`, compared
    with `out_small` (the output scaled to the source's size) over the texels `alpha` covers (the
    filled holes would skew it): the model's slight colour shift undone, the new detail kept."""
    for c, ((m_src, s_src), (m_out, s_out)) in enumerate(zip(_stats(source, alpha), _stats(out_small, alpha))):
        gain = min(1.5, max(0.67, s_src / s_out)) if s_out > 1 else 1.0
        lut = bytes(max(0, min(255, round((v - m_out) * gain + m_src))) for v in range(256))
        out[c::4] = bytes(out[c::4]).translate(lut)


def prepare(source_png: Path, input_png: Path):
    """Reads a dumped texture; returns why it is left out ("normal map", "data map"), else its
    alpha (or None when it is opaque) and the RGB the upscaler gets, written to input_png."""
    QImage = _qimage()
    img = QImage(str(source_png))
    if img.isNull():
        raise OSError(f"{source_png.name} could not be read")
    img, rgba = _rgba(img)
    w, h = img.width(), img.height()
    why = not_colour(rgba)
    if why:
        return why
    alpha = rgba[3::4]
    opaque = alpha.count(255) == len(alpha)
    rgb = _fill_holes(rgba, w, h)
    if not _from_rgba(rgb, w, h).convertToFormat(QImage.Format_RGB888).save(str(input_png), "PNG", 100):
        raise OSError(f"could not write {input_png}")
    return {"alpha": None if opaque else alpha, "width": w, "height": h}


def finish(prepared: dict, input_png: Path, output_png: Path, item: Item, target: Path, raw: Path = None):
    """Turns the upscaler's output into the pack's file: its size (halved for 2x), the source's
    colours, the alpha (scaled smoothly), written as target (a PNG) through a temporary file; with
    `raw`, written there as RGBA8 for the compressor instead, and returns its texture_compress.Job
    (to write target, a .dds)."""
    from PySide6.QtCore import Qt
    QImage = _qimage()
    out = QImage(str(output_png))
    if out.isNull():
        raise OSError(f"the upscaler wrote no readable {output_png.name}")
    if (out.width(), out.height()) != (item.out_width, item.out_height):
        out = out.scaled(item.out_width, item.out_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out, data = _rgba(out)
    data = bytearray(data)
    alpha = prepared["alpha"]
    w, h = prepared["width"], prepared["height"]
    source = _rgba(QImage(str(input_png)))[1]
    small = _rgba(out.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))[1]
    match_colours(data, source, small, alpha)
    if alpha is not None:
        grey = bytearray(len(alpha) * 4)
        for c in range(3):
            grey[c::4] = alpha
        grey[3::4] = b"\xff" * len(alpha)
        scaled = _from_rgba(bytes(grey), prepared["width"], prepared["height"]).scaled(
            item.out_width, item.out_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        data[3::4] = _rgba(scaled)[1][0::4]
    if raw is not None:
        if alpha is None:
            data[3::4] = b"\xff" * (item.out_width * item.out_height)
        texture_compress.write_raw(raw, bytes(data))
        # Cut-outs and other alpha: BC7. Opaque: BC1 when it keeps the texture nearly as well.
        return texture_compress.Job("bc7" if alpha is not None else "auto", item.out_width,
                                    item.out_height, raw, target)
    image = _from_rgba(bytes(data), item.out_width, item.out_height)
    if alpha is None:
        image = image.convertToFormat(QImage.Format_RGB888)
    temp = target.with_name(f".{target.name}.tmp")
    if not image.save(str(temp), "PNG"):
        temp.unlink(missing_ok=True)
        raise OSError(f"could not write {temp}")
    os.replace(temp, target)


# --- the job -----------------------------------------------------------------------------------

def _manifest(cfg, scale, compressed=False):
    pack = pack_dir(cfg)
    pack.mkdir(parents=True, exist_ok=True)
    how = " and compressed (BC7/BC1)" if compressed else ""
    (pack / texture_packs.MANIFEST).write_text(
        'name = "HD pack (made on this PC)"\n'
        f'description = "Your texture dump upscaled {scale}x by Real-ESRGAN ({MODEL}){how} on this PC. '
        'Made from the game\'s art: keep it to yourself."\n'
        f"scale = {scale}\nmodel = \"{MODEL}\"\nformat = \"{'dds' if compressed else 'png'}\"\n",
        encoding="utf-8")


def _command(cfg, src: Path, dest: Path) -> list:
    exe = tool_dir(cfg) / _program_name()
    return [str(exe), "-i", str(src), "-o", str(dest), "-n", MODEL, "-s", str(MODEL_SCALE), "-f", "png"]


def _start(cfg, src: Path, dest: Path, messages: Path):
    cmd = _command(cfg, src, dest)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000 | 0x00004000  # no console window, below normal priority
    else:
        kw["start_new_session"] = True
    # Its messages (a line per tile) go to a file: a pipe nobody reads would fill and stop it.
    with open(messages, "wb") as err:
        return subprocess.Popen(cmd, cwd=str(tool_dir(cfg)), env=cleanenv.clean_environment(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, **kw)


def _stop(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _batches(items: list) -> list:
    out, batch, pixels = [], [], 0
    for item in items:
        batch.append(item)
        pixels += item.width * item.height
        if pixels >= BATCH_PIXELS or len(batch) >= BATCH_MAX:
            out.append(batch)
            batch, pixels = [], 0
    if batch:
        out.append(batch)
    return out


class Result:
    def __init__(self):
        self.made = 0
        self.left_out = Counter()   # "normal map" / "data map" -> textures
        self.failed = []
        self.seconds = 0.0
        self.compressed = False
        self.formats = Counter()    # "BC7" / "BC1" -> textures written so
        self.converted = 0          # PNGs made before, compressed


def make(cfg, scale: int, progress=lambda fraction, text: None, cancelled=lambda: False,
         log=lambda text: None) -> Result:
    """Upscales what plan() lists into the HD pack, in batches; resumes (textures already in the
    pack are skipped). Raises fetch.Cancelled when cancelled (the files made so far stay)."""
    if scale not in SCALES:
        raise ValueError(f"scale {scale}")
    verify(cfg)
    p = plan(cfg, scale)
    result = Result()
    result.compressed = compress = p.compressed
    start = time.monotonic()
    _manifest(cfg, scale, compress)
    pack, work = pack_dir(cfg), work_dir(cfg)
    if compress and p.png_done:
        # PNGs an earlier run made (or a build without the compressor): compressed first, so the
        # whole pack takes a quarter of the memory.
        log(f"HD pack: compressing the {p.png_done} textures made before as PNG")
        folder = texture_compress.compress_folder(
            cfg, pack, work / "compress", lambda n, t: progress(
                0.0, f"Compressing the textures made before: {n} of {t}"), cancelled, top_only=True)
        result.converted = folder.compressed
        result.failed += folder.failed
    total = len(p.todo)
    total_pixels = sum(i.width * i.height for i in p.todo) or 1
    done_pixels = 0
    batches = _batches(p.todo)
    log(f"HD pack: {total} textures to upscale {scale}x, {p.done} already made, "
        f"{sum(p.skipped.values())} skipped")

    def stage(n):
        return work / f"batch{n}"

    def prepare_batch(n, batch):
        shutil.rmtree(stage(n), ignore_errors=True)
        (stage(n) / "in").mkdir(parents=True)
        (stage(n) / "out").mkdir()
        (stage(n) / "raw").mkdir()
        ready = []
        for item in batch:
            if cancelled():
                raise fetch.Cancelled()
            try:
                info = prepare(dump_dir(cfg) / f"{item.fingerprint}.png", stage(n) / "in" / f"{item.fingerprint}.png")
            except OSError as e:
                result.failed.append(f"{item.fingerprint}: {e}")
                continue
            if isinstance(info, str):
                result.left_out[info] += 1
                with open(pack_dir(cfg) / LEFT_OUT, "a", encoding="utf-8") as f:
                    f.write(f"{item.fingerprint}\t{info}\n")
                continue
            ready.append((item, info))
        return ready

    def finish_batch(n, ready):
        nonlocal done_pixels
        jobs = []
        for item, info in ready:
            if cancelled():
                raise fetch.Cancelled()
            fp = item.fingerprint
            try:
                if compress:
                    jobs.append(finish(info, stage(n) / "in" / f"{fp}.png", stage(n) / "out" / f"{fp}.png",
                                       item, pack / f"{fp}.dds", raw=stage(n) / "raw" / f"{fp}.rgba"))
                else:
                    finish(info, stage(n) / "in" / f"{fp}.png", stage(n) / "out" / f"{fp}.png", item,
                           pack / f"{fp}.png")
                    # A DDS of another scale would be used before the new PNG.
                    (pack / f"{fp}.dds").unlink(missing_ok=True)
                    result.made += 1
            except OSError as e:
                result.failed.append(f"{fp}: {e}")
        if jobs:
            outcome = texture_compress.run(cfg, jobs, stage(n) / "compress", cancelled=cancelled)
            for job in jobs:
                done = outcome.done.get(str(job.output))
                if done is None:
                    result.failed.append(f"{job.output.stem}: {outcome.failed.get(str(job.output), 'not written')}")
                    continue
                job.output.with_suffix(".png").unlink(missing_ok=True)   # a PNG an older run left (any scale)
                result.made += 1
                result.formats[done.format] += 1
        shutil.rmtree(stage(n), ignore_errors=True)

    def report(n_done, pixels_done, text):
        elapsed = time.monotonic() - start
        fraction = pixels_done / total_pixels
        left = ""
        if fraction > 0.02 and elapsed > 5:
            remaining = elapsed / fraction * (1 - fraction)
            left = f", about {max(1, round(remaining / 60))} min left"
        progress(fraction, f"{text} {n_done} of {total}{left}")

    proc = None
    previous = None   # (batch number, ready list) waiting for its outputs to be finished
    try:
        for n, batch in enumerate(batches):
            report(result.made + sum(result.left_out.values()) + len(result.failed), done_pixels, "Upscaling")
            ready = prepare_batch(n, batch)
            proc = _start(cfg, stage(n) / "in", stage(n) / "out", stage(n) / "messages.txt") if ready else None
            # While the GPU works on this batch, the last one's outputs are finished.
            if previous is not None:
                finish_batch(*previous)
                done_pixels += sum(i.width * i.height for i, _ in previous[1])
                previous = None
            while proc is not None and proc.poll() is None:
                if cancelled():
                    raise fetch.Cancelled()
                finished = {f.stem for f in (stage(n) / "out").glob("*.png")}
                part = sum(i.width * i.height for i, _ in ready if i.fingerprint in finished)
                report(result.made + len(finished), done_pixels + part, "Upscaling")
                time.sleep(0.25)
            if proc is not None and proc.returncode != 0:
                err = [line for line in (stage(n) / "messages.txt").read_text("utf-8", "replace").splitlines()
                       if line.strip() and not line.strip().endswith("%")]
                raise RuntimeError("The upscaler stopped (exit code %s): %s" % (
                    proc.returncode, err[-1] if err else "no message"))
            proc = None
            done_pixels += sum(i.width * i.height for i in batch) - sum(i.width * i.height for i, _ in ready)
            previous = (n, ready)
        if previous is not None:
            finish_batch(*previous)
            done_pixels += sum(i.width * i.height for i, _ in previous[1])
    finally:
        _stop(proc)
        shutil.rmtree(work, ignore_errors=True)
    result.seconds = time.monotonic() - start
    progress(1.0, f"Made {result.made} of {total}")
    log(f"HD pack: {result.made} made in {result.seconds:.0f} s"
        + (f" ({dict(result.formats)}, {result.converted} PNGs compressed)" if compress else " (PNG)")
        + f", left out {dict(result.left_out)}, {len(result.failed)} failed")
    return result
