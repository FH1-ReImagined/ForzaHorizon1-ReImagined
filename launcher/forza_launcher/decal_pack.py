"""Make decal pack (#659, docs/texture_packs.md): the Paint Shop's decals upscaled on this PC into
the texture pack textures/decal-pack. Nothing of the game is downloaded or shared: the decals are
read from the player's own game files, the pack stays in their data folder.

The decals are media/Livery.zip's decals/*.carbin (490 files, 478 different pictures): each holds
one 256x256 (7 of them 256x128) 8:8:8:8 texture with its mip levels, tiled and big-endian as the
console loads it. Its fingerprint (the name a pack file must have) is computed here as the game's
texture packs compute it (SDK patch 0280, Packs::Fingerprint), so no dump is needed: every decal
gets its file, whether or not it was ever on screen. Upscaling is Make HD pack's
(hd_pack.upscale: Real-ESRGAN, colours matched, BC7 with mips), with two changes for decals: they
are colour art by definition, and their alpha (the logo's outline) goes through the model too, so
the outline is as sharp as the colour, with the colour under the transparent texels filled from
the edge (no white or dark fringe)."""
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import hd_pack, paths, texture_compress, texture_packs

SCALES = (2, 4)
# A large decal covers about 900 pixels of the 2x paint job (#659). The pack is preloaded (its
# pack.toml, _manifest): 178 MiB of graphics memory at 2x, 652 MiB at 4x, which looked only a
# little sharper at render scale 2.
DEFAULT_SCALE = 2
MEMBERS = "decals/"
# The decal's texture in a decal .carbin (all 490 alike): width, height, mip levels, the texture
# format word (8:8:8:8), then the largest level.
_WIDTH, _HEIGHT, _LEVELS, _FORMAT, _DATA = 0x143, 0x147, 0x14B, 0x14F, 0x157
_ARGB8 = 0x18287F86
# The texture as the game binds it (SDK pack.cpp Packs::Fingerprint): fingerprint version 1,
# k_8_8_8_8, k8in32, the pitch in 32-texel units, tiled, packed mips.
_FINGERPRINT_VERSION, _K_8_8_8_8, _K_8IN32 = 1, 6, 2
SOURCE_LIST = ".decals.tsv"   # in the pack: Livery.zip member, its CRC, fingerprint, size


def livery_zip(cfg) -> Path:
    return cfg.game_files() / "media" / "Livery.zip"


def pack_dir(cfg) -> Path:
    return texture_packs.textures_dir(cfg) / texture_packs.DECAL_PACK


def work_dir(cfg) -> Path:
    return cfg.data_home() / "tools" / "decal-pack-work"


# --- the decals --------------------------------------------------------------------------------

def _tiled_offsets(width: int, height: int) -> list:
    """For each run of 4 texels (16 bytes) of the untiled image, row by row, its byte offset in
    the console's tiled layout (XGAddress2DTiledOffset for 4-byte texels: 4 texels in a row are
    16 bytes in a row there too)."""
    aligned = (width + 31) & ~31
    out = []
    for y in range(height):
        for x in range(0, width, 4):
            macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << 9
            micro = ((x & 7) + ((y & 6) << 2)) << 2
            offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << 5) + ((y & 1) << 4)
            out.append(((offset & ~511) << 3) + ((offset & 448) << 2) + (offset & 63) + ((y & 16) << 7)
                       + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6))
    return out


_offsets = {}


def parse(data: bytes):
    """(width, height, the largest level's bytes as stored) of a decal .carbin, or None when it is
    not one (the archive's base.carbin, a file of another layout)."""
    if len(data) < _DATA:
        return None
    width, height, levels, fmt = struct.unpack_from(">IIII", data, _WIDTH)
    if (fmt != _ARGB8 or not levels or width % 32 or height % 32 or not 32 <= width <= 2048
            or not 32 <= height <= 2048 or len(data) < _DATA + width * height * 4):
        return None
    return width, height, data[_DATA:_DATA + width * height * 4]


def fingerprint(base: bytes, width: int, height: int) -> str:
    """The texture packs' fingerprint of a decal (SDK patch 0280, Packs::Fingerprint): XXH3 of the
    largest level as stored (a 32-texel multiple, so the tiled data as it is), then of the fields."""
    paths.ensure_tools_importable()
    from xxh3 import xxh3_64
    fields = struct.pack("<8IQ", _FINGERPRINT_VERSION, _K_8_8_8_8, _K_8IN32, width, height, width // 32,
                         1, 1, xxh3_64(base))
    return "%016x" % (xxh3_64(fields) or 1)


def untile(base: bytes, width: int, height: int) -> bytes:
    """The largest level as RGBA8 rows, in the channel order of the game's dump PNGs (stored
    A, R, G, B a texel; the dump's red is the stored B, as the game samples it)."""
    offsets = _offsets.get((width, height))
    if offsets is None:
        offsets = _offsets[(width, height)] = _tiled_offsets(width, height)
    linear = b"".join(base[o:o + 16] for o in offsets)
    out = bytearray(len(linear))
    out[0::4] = linear[3::4]
    out[1::4] = linear[2::4]
    out[2::4] = linear[1::4]
    out[3::4] = linear[0::4]
    return bytes(out)


@dataclass
class Member:
    name: str
    crc: int
    size: int


def members(cfg) -> list:
    """The decal files in the game's Livery.zip (from its directory only: cheap); [] without it."""
    try:
        with zipfile.ZipFile(livery_zip(cfg)) as z:
            return [Member(i.filename, i.CRC, i.file_size) for i in z.infolist()
                    if i.filename.lower().startswith(MEMBERS) and i.filename.lower().endswith(".carbin")
                    and i.file_size >= _DATA + 32 * 32 * 4]
    except (OSError, zipfile.BadZipFile):
        return []


def read_source_list(pack: Path) -> dict:
    """(member, crc) -> (fingerprint, width, height), from the pack's SOURCE_LIST."""
    out = {}
    try:
        lines = (pack / SOURCE_LIST).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        cols = line.split("\t")
        if len(cols) == 4 and texture_packs.FINGERPRINT.match(cols[2]):
            try:
                w, h = (int(v) for v in cols[3].split("x"))
                out[(cols[0], int(cols[1]))] = (cols[2], w, h)
            except ValueError:
                continue
    return out


def extract(cfg, out_dir: Path, cancelled=lambda: False, progress=lambda n, total: None) -> dict:
    """Decodes every decal of Livery.zip into out_dir/<fingerprint>.png; returns
    (member, crc) -> (fingerprint, width, height). About 10 s for the 490."""
    paths.ensure_tools_importable()
    import fxobj_shaders   # read_zip_member: the game's LZX (zip method 21), pure Python
    from PySide6.QtGui import QImage
    zip_path = livery_zip(cfg)
    found = {}
    with zipfile.ZipFile(zip_path) as z:
        infos = [i for i in z.infolist() if i.filename.lower().startswith(MEMBERS)
                 and i.filename.lower().endswith(".carbin")]
        for n, info in enumerate(infos):
            if cancelled():
                from .fetch import Cancelled
                raise Cancelled()
            progress(n, len(infos))
            parsed = parse(fxobj_shaders.read_zip_member(z, zip_path, info))
            if parsed is None:
                continue
            width, height, base = parsed
            fp = fingerprint(base, width, height)
            found[(info.filename, info.CRC)] = (fp, width, height)
            target = out_dir / f"{fp}.png"
            if not target.is_file():
                rgba = untile(base, width, height)
                image = QImage(rgba, width, height, width * 4, QImage.Format_RGBA8888)
                if not image.save(str(target), "PNG", 100):
                    raise OSError(f"could not write {target}")
    return found


# --- the plan and the job ----------------------------------------------------------------------

@dataclass
class Plan:
    scale: int
    decals: int = 0          # decal files in Livery.zip
    done: int = 0            # of them, already in the pack at this scale
    replaces: int = 0        # the scale of the pack it replaces (0: none)
    compressed: bool = False
    out_pixels: int = 0      # of the whole pack
    found: bool = False      # Livery.zip is there
    todo_pixels: int = 0     # source texels still to upscale

    @property
    def todo(self) -> int:
        return self.decals - self.done

    def disk_bytes(self) -> int:
        return texture_compress.gpu_bytes(self.out_pixels) if self.compressed else int(
            self.out_pixels * hd_pack.PNG_BYTES_PER_PIXEL)

    def gpu_bytes(self) -> int:
        """Graphics memory of the whole pack (preloaded): BC7 with mips, or RGBA8 from PNGs."""
        return texture_compress.gpu_bytes(self.out_pixels) if self.compressed else self.out_pixels * 4 * 4 // 3

    def seconds(self) -> float:
        # The decals' upscale is twice a world texture's (colour, then alpha) a source texel.
        return self.todo_pixels / 1e6 * hd_pack.SECONDS_PER_MEGAPIXEL * 2


def existing_scale(cfg):
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


def plan(cfg, scale: int, compressed=None) -> Plan:
    """What making the decal pack at `scale` does (from Livery.zip's directory and the pack's
    SOURCE_LIST: nothing is decoded). A pack at another scale is replaced as a whole."""
    p = Plan(scale=scale, compressed=texture_compress.available(cfg) if compressed is None else compressed)
    found = members(cfg)
    p.found = bool(found)
    pack = pack_dir(cfg)
    before = existing_scale(cfg)
    p.replaces = 0 if before in (None, scale) else before
    known = read_source_list(pack) if not p.replaces else {}
    seen = set()
    for m in found:
        p.decals += 1
        fp, w, h = known.get((m.name, m.crc), (None, 256, 256 if m.size > 200_000 else 128))
        if fp is not None and fp in seen:
            p.done += 1     # the same picture as another decal: one file for both
            continue
        p.out_pixels += w * h * scale * scale
        if fp is not None and ((pack / f"{fp}.dds").is_file() or (pack / f"{fp}.png").is_file()):
            p.done += 1
            seen.add(fp)
        else:
            p.todo_pixels += w * h
    return p


def _manifest(pack: Path, scale: int, compressed: bool):
    pack.mkdir(parents=True, exist_ok=True)
    how = " and compressed (BC7)" if compressed else ""
    (pack / texture_packs.MANIFEST).write_text(
        'name = "Decal pack (made on this PC)"\n'
        f'description = "The Paint Shop\'s decals from your game files, upscaled {scale}x by Real-ESRGAN '
        f'({hd_pack.MODEL}){how} on this PC. Made from the game\'s art: keep it to yourself."\n'
        f"scale = {scale}\nmodel = \"{hd_pack.MODEL}\"\nformat = \"{'dds' if compressed else 'png'}\"\n"
        # Read at start (SDK patch 0436): a car's decals are drawn into its paint job once, as the
        # car loads, and a replacement read only when its decal first loads comes a frame late.
        "preload = true\n",
        encoding="utf-8")


def make(cfg, scale: int, progress=lambda fraction, text: None, cancelled=lambda: False,
         log=lambda text: None) -> hd_pack.Result:
    """Decodes Livery.zip's decals, upscales the ones not in the pack yet into it and records
    their fingerprints (SOURCE_LIST). Resumes; a pack at another scale is replaced. Raises
    fetch.Cancelled when cancelled (the files made so far stay)."""
    if scale not in SCALES:
        raise ValueError(f"scale {scale}")
    hd_pack.verify(cfg)
    if not livery_zip(cfg).is_file():
        raise RuntimeError(f"The game's {livery_zip(cfg)} is missing: install the game in Setup first.")
    pack, work = pack_dir(cfg), work_dir(cfg)
    before = existing_scale(cfg)
    if before not in (None, scale):
        texture_packs.remove(cfg, texture_packs.DECAL_PACK)
    result = hd_pack.Result()
    result.compressed = texture_compress.available(cfg)
    start = time.monotonic()
    source = work / "source"
    source.mkdir(parents=True, exist_ok=True)
    try:
        found = extract(cfg, source, cancelled, lambda n, total: progress(
            0.0, f"Reading the decals: {n} of {total}"))
        _manifest(pack, scale, result.compressed)
        (pack / SOURCE_LIST).write_text("".join(
            f"{name}\t{crc}\t{fp}\t{w}x{h}\n" for (name, crc), (fp, w, h) in sorted(found.items())),
            encoding="utf-8")
        items, seen = [], set()
        for fp, w, h in sorted(found.values()):
            if fp in seen or (pack / f"{fp}.dds").is_file() or (pack / f"{fp}.png").is_file():
                continue
            seen.add(fp)
            items.append(hd_pack.Item(fp, w, h, w * scale, h * scale))
        log(f"Decal pack: {len(found)} decals ({len(set(v[0] for v in found.values()))} different), "
            f"{len(items)} to upscale {scale}x")
        # upscale() deletes its work folder when it ends: the decoded decals go with it.
        hd_pack.upscale(cfg, items, source, pack, work, result, start, progress, cancelled, decal=True)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    log(f"Decal pack: {result.made} made in {result.seconds:.0f} s ({dict(result.formats)}), "
        f"{len(result.failed)} failed")
    return result
