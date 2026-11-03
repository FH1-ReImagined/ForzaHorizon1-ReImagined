"""Texture compression (#413, docs/texture_packs.md): texture pack pictures to BC7 or BC1 DDS files
with their mip levels, which the game uploads as they are. A PNG becomes RGBA8 in graphics memory
(4 bytes a texel, plus a third for the mips); BC7 takes 1 byte a texel, BC1 half a byte.

The compressor is forza-texcompress (forza-recomp/texcompress), built with the game on this PC
next to it (Rich Geldreich's bc7enc_rdo encoders, MIT, in forza-recomp/thirdparty/bc7enc_rdo):
nothing is downloaded. Make HD pack writes its textures through it (hd_pack.py), and Add pack can
compress a pack's PNGs (texture_packs.install). The launcher hands it RGBA8 files decoded with
Qt's QImage and a job list; it writes each DDS through a temporary file, so a stopped run leaves
no half-written texture."""
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import cleanenv, fetch, texture_packs

TOOL_VERSION = 1   # the job format forza-texcompress speaks (its --version)
SOURCE_URL = "https://github.com/richgel999/bc7enc_rdo"
LICENCE_URL = "https://github.com/richgel999/bc7enc_rdo/blob/master/LICENSE"   # MIT or public domain
MAX_THREADS = 8            # each holds a texture: up to 4096x4096 and its levels (about 110 MB)
BATCH_BYTES = 512 << 20    # RGBA8 files on disk at once while compressing a folder
# Graphics memory of a compressed pack, per texel of the largest levels (all levels: a third
# more). BC7 is 1 byte; measured on a race's dump upscaled 2x and 4x, the few textures that come
# out as BC1 (half a byte) change that by under 2%.
BYTES_PER_TEXEL = 1.0


def tool_name() -> str:
    return "forza-texcompress.exe" if sys.platform == "win32" else "forza-texcompress"


def tool_path(cfg) -> Path | None:
    """forza-texcompress next to the game the launcher built (FORZA_TEXCOMPRESS overrides)."""
    override = os.environ.get("FORZA_TEXCOMPRESS", "")
    if override:
        return Path(override) if Path(override).is_file() else None
    from . import game
    install, _looked = game.find_game(cfg)
    if install is None:
        return None
    path = install.dir / tool_name()
    return path if path.is_file() else None


_checked = {}   # (path, size, mtime) -> whether its --version is ours


def _command(path: Path, args: list) -> list:
    return [str(path)] + args


def available(cfg) -> bool:
    """Whether this PC has a forza-texcompress that speaks TOOL_VERSION (built with the game since
    #413; an older build has none, and packs are written as PNG)."""
    path = tool_path(cfg)
    if path is None:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _checked:
        try:
            out = subprocess.run(_command(path, ["--version"]), capture_output=True, text=True, timeout=20,
                                 env=cleanenv.clean_environment(), **_no_window())
            words = out.stdout.split()
            _checked[key] = (out.returncode == 0 and len(words) >= 2 and words[0] == "forza-texcompress"
                             and words[1].isdigit() and int(words[1]) >= TOOL_VERSION)
        except (OSError, subprocess.SubprocessError):
            _checked[key] = False
    return _checked[key]


def gpu_bytes(texels: int) -> int:
    """Graphics memory of compressed textures with `texels` in their largest levels."""
    return int(texels * BYTES_PER_TEXEL * 4 / 3)


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


@dataclass
class Job:
    choice: str        # "auto" (BC1 for an opaque texture BC1 keeps nearly as well, else BC7), "bc7", "bc1"
    width: int
    height: int
    raw: Path          # width * height RGBA8 texels, rows packed
    output: Path       # the .dds to write


@dataclass
class Done:
    format: str        # "BC1" or "BC7"
    psnr: float        # of the largest level against the picture, RGB, dB
    alpha_flips: int   # texels on the other side of half alpha than in the picture


@dataclass
class Outcome:
    done: dict = field(default_factory=dict)      # output path (str) -> Done
    failed: dict = field(default_factory=dict)    # output path (str) -> why


def write_raw(path: Path, rgba: bytes):
    with open(path, "wb") as f:
        f.write(rgba)


def threads() -> int:
    """The processors this launcher may use, one left for the rest, at most MAX_THREADS."""
    count = getattr(os, "process_cpu_count", os.cpu_count)() or 2
    return max(1, min(MAX_THREADS, count - 1))


def run(cfg, jobs: list, work: Path, progress=lambda done, total: None, cancelled=lambda: False) -> Outcome:
    """Compresses `jobs` with forza-texcompress (below normal priority); `work` is an empty folder
    for its job list and messages. Raises fetch.Cancelled (the textures finished so far stay) or
    RuntimeError when it could not run."""
    outcome = Outcome()
    if not jobs:
        return outcome
    path = tool_path(cfg)
    if path is None:
        raise RuntimeError("forza-texcompress is not there: build the game again (Setup > Build).")
    work.mkdir(parents=True, exist_ok=True)
    lines = []
    for job in jobs:
        for p in (job.raw, job.output):
            if "\t" in str(p) or "\n" in str(p):
                raise RuntimeError(f"a folder name with a tab or line break: {p}")
        lines.append(f"{job.choice}\t{job.width}\t{job.height}\t{job.raw}\t{job.output}\n")
    listing = work / "jobs.tsv"
    listing.write_text("".join(lines), encoding="utf-8")
    messages, errors = work / "messages.txt", work / "errors.txt"
    kw = {}
    if sys.platform != "win32":
        kw["start_new_session"] = True
    with open(messages, "wb") as out, open(errors, "wb") as err:
        proc = subprocess.Popen(_command(path, ["--threads", str(threads()), "--low-priority", str(listing)]),
                                cwd=str(work), env=cleanenv.clean_environment(), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, **_no_window(), **kw)
    seen = 0
    try:
        while True:
            finished = proc.poll() is not None
            text = messages.read_text("utf-8", "replace")
            # Only whole lines (the last may still be written).
            for line in text[:text.rfind("\n") + 1].splitlines()[seen:]:
                seen += 1
                cols = line.split("\t")
                if cols[0] == "ok" and len(cols) >= 6:
                    try:
                        outcome.done[cols[1]] = Done(cols[2], float(cols[4]), int(cols[5]))
                    except ValueError:
                        outcome.done[cols[1]] = Done(cols[2], 0.0, 0)
                elif cols[0] == "fail" and len(cols) >= 3:
                    outcome.failed[cols[1]] = cols[2]
                progress(len(outcome.done) + len(outcome.failed), len(jobs))
            if finished:
                break
            if cancelled():
                raise fetch.Cancelled()
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    if proc.returncode not in (0, 1):
        why = errors.read_text("utf-8", "replace").strip().splitlines()
        why = why[-1] if why else f"exit code {proc.returncode}"
        for job in jobs:
            outcome.failed.setdefault(str(job.output), f"the compressor stopped ({why})")
    for job in jobs:
        if str(job.output) not in outcome.done:
            outcome.failed.setdefault(str(job.output), "the compressor did not write it")
    return outcome


# --- a folder's PNGs ---------------------------------------------------------------------------

@dataclass
class FolderResult:
    compressed: int = 0
    failed: list = field(default_factory=list)     # "name: why"
    png_bytes: int = 0                             # graphics memory of the PNGs as RGBA8 with mips
    dds_bytes: int = 0                             # and of the DDS files made from them


def pngs(folder: Path, top_only=False) -> list:
    """The <fingerprint>.png files in `folder` (and the folders inside it) without a .dds of the
    same name beside them (the game uses that one)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [] if top_only else sorted(dirnames)
        names = {n.lower() for n in filenames}
        for name in sorted(filenames):
            stem, ext = os.path.splitext(name)
            if ext.lower() == ".png" and texture_packs.FINGERPRINT.match(stem) and \
                    f"{stem.lower()}.dds" not in names:
                found.append(Path(dirpath) / name)
    return found


def _rgba(path: Path):
    """A picture as (RGBA8 bytes, width, height), or None when Qt cannot read it."""
    from PySide6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format_RGBA8888)
    return bytes(img.constBits())[: img.width() * img.height() * 4], img.width(), img.height()


def compress_folder(cfg, folder: Path, work: Path, progress=lambda done, total: None,
                    cancelled=lambda: False, top_only=False) -> FolderResult:
    """Replaces the fingerprint-named PNGs in `folder` by DDS files (BC7, or BC1 where it keeps the
    texture nearly as well), in batches of BATCH_BYTES; a PNG is deleted only once its DDS is
    written. Raises fetch.Cancelled (what was done stays) or RuntimeError."""
    result = FolderResult()
    todo = pngs(folder, top_only)
    total = len(todo)
    shutil.rmtree(work, ignore_errors=True)
    try:
        index = 0
        while index < total:
            work.mkdir(parents=True, exist_ok=True)
            jobs, sources, size = [], {}, 0
            while index < total and (not jobs or size < BATCH_BYTES):
                if cancelled():
                    raise fetch.Cancelled()
                png = todo[index]
                index += 1
                picture = _rgba(png)
                if picture is None:
                    result.failed.append(f"{png.name}: not a picture Qt can read")
                    continue
                rgba, width, height = picture
                raw = work / f"{len(jobs):05d}.rgba"
                write_raw(raw, rgba)
                job = Job("auto", width, height, raw, png.with_suffix(".dds"))
                jobs.append(job)
                sources[str(job.output)] = (png, width * height)
                size += len(rgba)
            done_before = result.compressed + len(result.failed)
            outcome = run(cfg, jobs, work / "run", lambda n, _t: progress(done_before + n, total), cancelled)
            for output, (png, texels) in sources.items():
                if output in outcome.done:
                    try:
                        png.unlink()
                    except OSError as e:
                        result.failed.append(f"{png.name}: the PNG could not be deleted ({e})")
                        continue
                    result.compressed += 1
                    result.png_bytes += texels * 4 * 4 // 3
                    try:
                        result.dds_bytes += Path(output).stat().st_size
                    except OSError:
                        pass
                else:
                    result.failed.append(f"{png.name}: {outcome.failed.get(output, 'not written')}")
            shutil.rmtree(work, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    progress(total, total)
    return result
