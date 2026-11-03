"""What the PC can run: CPU, RAM and graphics card, the settings that suit it, and warnings (issue #206).

Players with mid-range PCs ended up on settings meant for high-end ones (a GTX 1660 SUPER at render scale 2:
2560x1440 at 99% GPU). The launcher detects the specs once (cached in launcher.json under `specs`), sorts the PC
into two tiers and

* gives a NEW launcher.json the recommended frame rate and render scale (on top of the vendor preset, which
  keeps the last word: a value the vendor's known problems reject is not applied),
* warns next to settings that are heavy for the PC, with the numbers filled in,
* offers a player with existing settings a one-time "use recommended / keep mine" choice; nothing changes silently.

No Qt in here. Every parser is a pure function over text, tested with fixtures (test_specs.py).
FORZA_SPECS="cpu_cores=4,avx2=0,ram=8,vram=4,integrated=0" overrides single values (testing, odd setups).
"""
import glob
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field, fields

from . import gpu
from .paths import IS_WINDOWS

LOW, MID, HIGH, UNKNOWN = "low", "mid", "high", "unknown"
GIB = 1024 ** 3
# The game's guest resolution: render scale n draws n times this (docs/launcher.md, Render scale)
GUEST_SIZE = (1280, 720)
RAM_LOW_GB = 7.0   # "8 GB" PCs report 7.0 to 7.9 GiB once the integrated GPU and firmware have taken theirs
DECK_NAME = "Steam Deck"   # gpu_name of a Steam Deck, whatever its graphics are called (gpu.steam_deck_reason)


@dataclass
class Specs:
    cpu_name: str = ""
    cpu_cores: int = 0          # physical cores; 0 = unknown
    cpu_threads: int = 0
    avx2: object = None         # True / False, None = unknown. AVX2 stands in for FMA3: every CPU with AVX2 has it
    ram_gb: float = 0.0         # 0 = unknown
    gpu_name: str = ""
    vram_gb: float = 0.0        # 0 = unknown
    integrated: object = None   # True / False, None = unknown
    source: str = ""

    def known(self) -> bool:
        return bool(self.cpu_name or self.cpu_cores or self.ram_gb or self.gpu_name or self.vram_gb)

    def signature(self) -> str:
        """Identifies this hardware; the one-time banner comes back only when it changes (or, on a Steam
        Deck, when its preset does: DECK_OFFER)."""
        sig = "|".join(str(x) for x in (self.cpu_name, self.cpu_cores, self.avx2, round(self.ram_gb),
                                         self.gpu_name, round(self.vram_gb, 1), self.integrated))
        return sig + "|" + DECK_OFFER if is_deck(self) else sig


def specs_from_dict(d) -> Specs:
    """The cached specs; a value of the wrong kind falls back to 'unknown'."""
    out = Specs()
    for f in fields(Specs):
        v = d.get(f.name) if isinstance(d, dict) else None
        default = getattr(out, f.name)
        if f.name in ("avx2", "integrated"):
            setattr(out, f.name, v if isinstance(v, bool) else None)
        elif isinstance(default, str) and isinstance(v, str):
            setattr(out, f.name, v)
        elif isinstance(default, (int, float)) and isinstance(v, (int, float)) and not isinstance(v, bool):
            setattr(out, f.name, type(default)(v))
    return out


# --- names -----------------------------------------------------------------------------------
def short_cpu(name: str) -> str:
    n = re.sub(r"\((?:R|TM|tm)\)", "", name or "")
    n = re.sub(r"\b(?:AMD|Intel|Core|Processor|CPU)\b|\b\d+-Core\b|@.*$|\bwith Radeon.*$", " ", n, flags=re.I)
    return " ".join(n.split()) or (name or "").strip()


def short_gpu(name: str) -> str:
    n = re.sub(r"\((?:R|TM|tm)\)", "", name or "")
    n = re.sub(r"\b(?:NVIDIA|AMD|Advanced Micro Devices, Inc\.|Intel|Corporation)\b", " ", n)
    n = re.sub(r"^\s*(?:\[|\])", "", n)
    return " ".join(n.split()) or (name or "").strip()


def _gb(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 0.15 else f"{value:.1f}"


# --- classification --------------------------------------------------------------------------
_INTEGRATED_NAME = re.compile(r"radeon(?:\(tm\))?\s+(?:graphics|\d{3}m)\b|\bvega\s*(?:3|6|7|8|10|11)\b|"
                              r"\buhd\b|\bhd graphics\b|\biris\b|arc(?:\(tm\))?\s+graphics\b|steam deck|"
                              r"\b(?:van gogh|rembrandt|phoenix|raphael|renoir|cezanne|barcelo|lucienne)\b", re.I)
_INTEL_DISCRETE = re.compile(r"\barc(?:\(tm\))?\s+(?:pro\s+)?[ab]\d{2,3}\b|\bdg[12]\b", re.I)
# Cards of the GTX / GCN era: slow whatever the memory they carry
_OLD_CARD = re.compile(r"\bgtx\b|\bgt\s?\d{3}\b|\brx\s?[45]\d{2}\b|\bvega\b|\br[579]\s?\d{3}\b|"
                       r"\bradeon\s+hd\b|\bhd\s?[5-7]\d{3}\b|\bfury\b|\btitan\b(?!\s+rtx)|\bgeforce\s+\d{3}", re.I)
_NEW_CARD = re.compile(r"\brtx\b|\brx\s?[6-9]\d{3}\b|\barc(?:\(tm\))?\s+(?:pro\s+)?[ab]\d{2,3}\b", re.I)


def is_integrated(vendor: str, name: str, vram_gb: float = 0.0):
    """True / False, or None when there is nothing to go on."""
    n = name or ""
    if vendor == gpu.DECK:
        return True
    if vendor == gpu.NVIDIA:
        return False
    if _INTEGRATED_NAME.search(n) and not _INTEL_DISCRETE.search(n) and not _NEW_CARD.search(n):
        return True
    if vendor == gpu.INTEL:
        return not _INTEL_DISCRETE.search(n)
    if vendor == gpu.AMD:
        if 0 < vram_gb <= 2 and not re.search(r"\brx\s?[4-9]\d{2,3}\b", n, re.I):
            return True   # an APU's carve-out of system memory
        if vram_gb or n:
            return False
    return None


# The Steam Deck preset's revision (#140): a Deck whose player answered the banner before it existed
# is asked once more.
DECK_OFFER = "deck-preset-2"


def is_deck(s: Specs) -> bool:
    return s.gpu_name == DECK_NAME


def cpu_tier(s: Specs) -> str:
    """Limits the frame rate (the game's simulation runs on the CPU).
    low: no AVX2, under 4 cores, under 8 GB RAM, or a Steam Deck. mid: under 6 cores. high: 6+ cores with AVX2."""
    if s.avx2 is False or 0 < s.cpu_cores < 4 or 0 < s.ram_gb < RAM_LOW_GB or is_deck(s):
        return LOW
    if s.cpu_cores <= 0:
        return UNKNOWN
    return MID if s.cpu_cores < 6 else HIGH


def gpu_tier(s: Specs) -> str:
    """Limits render scale, FSR 3 and heavier anti-aliasing.
    low: integrated, or under 4 GB of video memory. mid: 4 to 7 GB, or a GTX / RX 400-500 / Vega-era card
    whatever its memory. high: 8 GB or more on a newer card (RTX, RX 6000+, Arc). Without the memory size the
    name decides (never above mid, as the memory is not known); a name that says nothing is unknown."""
    name = s.gpu_name or ""
    if s.integrated is True or (s.integrated is None and _INTEGRATED_NAME.search(name)):
        return LOW
    if s.vram_gb > 0:
        if s.vram_gb < 3.5:
            return LOW
        if s.vram_gb < 7.5 or _OLD_CARD.search(name):
            return MID
        return HIGH
    if _OLD_CARD.search(name) or _NEW_CARD.search(name):
        return MID
    return UNKNOWN


def describe(s: Specs) -> str:
    """'Detected: Ryzen 5 7600X (6 cores), GeForce GTX 1660 SUPER (6 GB), 32 GB RAM: mid-range graphics, fast CPU'"""
    parts = []
    if s.cpu_name:
        parts.append(short_cpu(s.cpu_name) + (f" ({s.cpu_cores} cores)" if s.cpu_cores else ""))
    if s.gpu_name:
        parts.append(short_gpu(s.gpu_name) + (f" ({_gb(s.vram_gb)} GB)" if s.vram_gb else ""))
    if s.ram_gb:
        parts.append(f"{_gb(s.ram_gb)} GB RAM")
    if not parts:
        return "This PC: CPU, RAM and graphics card not detected."
    g, c = gpu_tier(s), cpu_tier(s)
    verdict = [x for x in ({LOW: "entry-level graphics", MID: "mid-range graphics", HIGH: "high-end graphics"}.get(g),
                           {LOW: "slow CPU", MID: "mid-range CPU", HIGH: "fast CPU"}.get(c)) if x]
    return "This PC: " + ", ".join(parts) + (": " + ", ".join(verdict) if verdict else "")


# --- Linux parsers -----------------------------------------------------------------------------
def parse_cpuinfo(text: str):
    """(model name, physical cores, threads, avx2) from /proc/cpuinfo."""
    name, threads, avx2 = "", 0, None
    cores, block = set(), {}
    for line in (text or "").splitlines() + [""]:
        if not line.strip():
            if block:
                if "processor" in block:
                    threads += 1
                if "core id" in block:
                    cores.add((block.get("physical id", "0"), block["core id"]))
                if "flags" in block:
                    avx2 = bool(avx2) or "avx2" in block["flags"].split()
                name = name or block.get("model name", "")
            block = {}
            continue
        key, _, value = line.partition(":")
        block.setdefault(key.strip(), value.strip())
    return name, len(cores) or threads, threads, avx2


def parse_meminfo(text: str) -> float:
    """MemTotal in GB (binary: 16 GB of RAM shows as 15.x), 0 when absent."""
    m = re.search(r"^MemTotal:\s*(\d+)\s*kB", text or "", re.M)
    return int(m.group(1)) * 1024 / GIB if m else 0.0


def parse_nvidia_smi(text: str):
    """[(name, VRAM in GB)] from `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`."""
    out = []
    for line in (text or "").splitlines():
        name, _, mem = line.rpartition(",")
        try:
            out.append((name.strip(), int(float(mem.strip())) / 1024))
        except ValueError:
            continue
    return out


def parse_lspci(text: str):
    """{PCI slot: name} for the display controllers in `lspci -mm`; the bracketed marketing name wins
    ('Navi 48 [Radeon RX 9070/9070 XT]' -> 'Radeon RX 9070/9070 XT')."""
    out = {}
    for line in (text or "").splitlines():
        try:
            cells = shlex.split(line)
        except ValueError:
            continue
        if len(cells) < 4 or not re.search(r"vga|3d controller|display controller", cells[1], re.I):
            continue
        m = re.search(r"\[([^\]]+)\]", cells[3])
        out[re.sub(r"^[0-9a-f]{4}:(?=\S+:)", "", cells[0])] = m.group(1) if m else cells[3]
    return out


@dataclass
class _Card:
    vendor: str
    name: str = ""
    vram_gb: float = 0.0
    integrated: object = None


def _read_int(path: str) -> int:
    try:
        return int(gpu._read(path))
    except ValueError:
        return 0


def linux_cards(sysfs: str = "/sys", lspci_text: str = "", smi_text: str = ""):
    """The graphics cards sysfs shows, with names (lspci), amdgpu video memory and nvidia-smi's names and sizes."""
    names = parse_lspci(lspci_text)
    smi = parse_nvidia_smi(smi_text)
    cards = []
    for card in sorted(glob.glob(f"{sysfs}/class/drm/card*")):
        if not re.fullmatch(r"card\d+", os.path.basename(card)):
            continue
        try:
            vendor = gpu.vendor_from_pci_id(int(gpu._read(f"{card}/device/vendor"), 16))
        except ValueError:
            continue
        if not vendor:
            continue
        slot = re.search(r"PCI_SLOT_NAME=(?:[0-9a-f]{4}:)?(\S+)", gpu._read(f"{card}/device/uevent"))
        name = names.get(slot.group(1), "") if slot else ""
        name = name or gpu._read(f"{card}/device/product_name")
        vram = _read_int(f"{card}/device/mem_info_vram_total") / GIB
        cards.append(_Card(vendor, name, vram))
    for c in cards:
        if c.vendor == gpu.NVIDIA and smi:
            c.name, c.vram_gb = max(smi, key=lambda x: x[1])
        if not c.name:
            c.name = gpu.LABELS[c.vendor] + " graphics"
        c.integrated = is_integrated(c.vendor, c.name, c.vram_gb)
    return cards


def best_card(cards):
    """The card the game would use: discrete before integrated, then more memory, then NVIDIA, AMD, Intel."""
    rank = {gpu.NVIDIA: 0, gpu.AMD: 1, gpu.INTEL: 2}
    return min(cards, key=lambda c: (c.integrated is True, -c.vram_gb, rank.get(c.vendor, 3)), default=None)


def detect_linux(sysfs: str = "/sys", proc: str = "/proc", lspci_text=None, smi_text=None) -> Specs:
    s = Specs(source="linux")
    s.cpu_name, s.cpu_cores, s.cpu_threads, s.avx2 = parse_cpuinfo(gpu._read(f"{proc}/cpuinfo"))
    s.ram_gb = parse_meminfo(gpu._read(f"{proc}/meminfo"))
    if not s.cpu_threads:
        s.cpu_threads = os.cpu_count() or 0
        s.cpu_cores = s.cpu_cores or s.cpu_threads
    deck = gpu.detect_linux(sysfs, proc).vendor == gpu.DECK
    cards = linux_cards(sysfs, _lspci() if lspci_text is None else lspci_text,
                        _nvidia_smi() if smi_text is None else smi_text)
    card = best_card(cards)
    if deck:
        s.gpu_name, s.integrated = DECK_NAME, True
        s.vram_gb = card.vram_gb if card else 0.0
    elif card:
        s.gpu_name, s.vram_gb, s.integrated = card.name, card.vram_gb, card.integrated
    return s


def _lspci() -> str:
    try:
        return gpu._run(["lspci", "-mm"], timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""


def _nvidia_smi() -> str:
    try:
        return gpu._run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=4)
    except (OSError, subprocess.SubprocessError):
        return ""


# --- Windows parsers ---------------------------------------------------------------------------
def _as_list(x):
    return x if isinstance(x, list) else [] if x is None else [x]


def _to_int(x) -> int:
    if isinstance(x, list):                       # a REG_BINARY value arrives as its bytes
        return int.from_bytes(bytes(b & 255 for b in x if isinstance(b, int)), "little")
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def parse_powershell(text: str) -> Specs:
    """Specs from the JSON gpu.PS_QUERY prints (cpus, ram, gpus, vram, board)."""
    s = Specs(source="powershell")
    try:
        data = json.loads(text)
    except ValueError:
        return s
    if not isinstance(data, dict):
        return s
    cpus = [c for c in _as_list(data.get("cpus")) if isinstance(c, dict)]
    if cpus:
        s.cpu_name = " ".join(str(cpus[0].get("Name") or "").split())
        s.cpu_cores = sum(_to_int(c.get("NumberOfCores")) for c in cpus)
        s.cpu_threads = sum(_to_int(c.get("NumberOfLogicalProcessors")) for c in cpus)
    s.ram_gb = _to_int(data.get("ram")) / GIB
    registry = {}
    for v in _as_list(data.get("vram")):
        if isinstance(v, dict) and v.get("Desc") and _to_int(v.get("Mem")):
            registry[str(v["Desc"]).strip().lower()] = _to_int(v["Mem"])
    cards = []
    for g in _as_list(data.get("gpus")):
        if not isinstance(g, dict):
            continue
        name = str(g.get("Name") or "").strip()
        text_ = " ".join(str(g.get(k) or "") for k in ("Name", "AdapterCompatibility", "PNPDeviceID"))
        vendor = gpu.vendor_from_text(text_)
        if not vendor:
            m = re.search(r"VEN_([0-9A-F]{4})", text_.upper())
            vendor = gpu.vendor_from_pci_id(int(m.group(1), 16)) if m else ""
        if not vendor or not name:
            continue
        vram = (registry.get(name.lower()) or _to_int(g.get("AdapterRAM"))) / GIB
        cards.append(_Card(vendor, name, vram, is_integrated(vendor, name, vram)))
    if (str(data.get("board") or "").strip().lower() in gpu.DECK_BOARDS or gpu.DECK_APU_NAME.search(s.cpu_name)
            or any(gpu.DECK_APU_NAME.search(c.name) for c in cards)):
        s.gpu_name, s.integrated = DECK_NAME, True
        s.vram_gb = max((c.vram_gb for c in cards), default=0.0)
        return s
    card = best_card(cards)
    if card:
        s.gpu_name, s.vram_gb, s.integrated = card.name, card.vram_gb, card.integrated
    return s


def windows_avx2():
    """PF_AVX2_INSTRUCTIONS_AVAILABLE (40)."""
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
    except Exception:
        return None


def detect_windows() -> Specs:
    s = Specs(source="powershell")
    try:
        s = parse_powershell(gpu.powershell_text())
    except (OSError, subprocess.SubprocessError):
        pass
    s.avx2 = windows_avx2()
    return s


# --- x86-64-v3 ---------------------------------------------------------------------------------------
# #220: the launcher compiles the game for it when the CPU has all of it (builder.cpu_target).
# /proc/cpuinfo names: pni = SSE3, abm = LZCNT, cx16 = CMPXCHG16B, lahf_lm = LAHF/SAHF. The kernel clears avx
# (and with it avx2, fma, f16c) when it does not save the AVX registers.
V3_CPUINFO_FLAGS = ("pni", "ssse3", "sse4_1", "sse4_2", "popcnt", "cx16", "lahf_lm",
                    "avx", "avx2", "bmi1", "bmi2", "fma", "f16c", "movbe", "abm")


def missing_v3_flags(cpuinfo_text: str):
    """The x86-64-v3 flags the first CPU of /proc/cpuinfo lacks ([] = none), or None without a flags line."""
    m = re.search(r"^flags\s*:(.*)$", cpuinfo_text or "", re.M)
    if not m:
        return None
    have = set(m.group(1).split())
    return [f for f in V3_CPUINFO_FLAGS if f not in have]


# Windows x64 machine code (Python cannot run CPUID): cpuid(leaf=rcx, subleaf=rdx, out=r8 -> eax ebx ecx edx)
# and xgetbv(index=rcx) -> rax. IsProcessorFeaturePresent knows AVX2 but not BMI, MOVBE, F16C or LZCNT.
_CPUID_CODE = bytes.fromhex("53 89c8 89d1 0fa2 418900 41895804 41894808 4189500c 5b c3".replace(" ", ""))
_XGETBV_CODE = bytes.fromhex("0f01d0 48c1e220 4809d0 c3".replace(" ", ""))


def v3_from_cpuid(regs, xgetbv) -> bool:
    """x86-64-v3 from CPUID: regs(leaf, subleaf) -> (eax, ebx, ecx, edx), xgetbv(0) -> XCR0. The same test as
    the game's own check at start (forza-recomp/src/platform/cpu_target.cpp)."""
    max_leaf = regs(0, 0)[0]
    ecx1 = regs(1, 0)[2] if max_leaf >= 1 else 0
    ebx7 = regs(7, 0)[1] if max_leaf >= 7 else 0
    ext = regs(0x80000001, 0)[2] if regs(0x80000000, 0)[0] >= 0x80000001 else 0
    need1 = [0, 9, 12, 13, 19, 20, 22, 23, 27, 28, 29]  # SSE3 SSSE3 FMA CX16 SSE4.1 SSE4.2 MOVBE POPCNT OSXSAVE AVX F16C
    if not all(ecx1 >> b & 1 for b in need1):
        return False
    if not all(ebx7 >> b & 1 for b in (3, 5, 8)) or not all(ext >> b & 1 for b in (0, 5)):  # BMI1 AVX2 BMI2; LAHF LZCNT
        return False
    return xgetbv(0) & 6 == 6   # the OS saves the SSE and AVX registers


def _windows_v3():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    size = 4096
    mem = k32.VirtualAlloc(None, size, 0x3000, 0x04)   # MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    if not mem:
        return None
    try:
        ctypes.memmove(mem, _CPUID_CODE, len(_CPUID_CODE))
        ctypes.memmove(mem + 64, _XGETBV_CODE, len(_XGETBV_CODE))
        old = wintypes.DWORD()
        if not k32.VirtualProtect(mem, size, 0x20, ctypes.byref(old)):   # PAGE_EXECUTE_READ
            return None
        cpuid = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32 * 4))(mem)
        xgetbv = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_uint32)(mem + 64)

        def regs(leaf, sub):
            out = (ctypes.c_uint32 * 4)()
            cpuid(leaf, sub, ctypes.byref(out))
            return tuple(out)
        return v3_from_cpuid(regs, xgetbv)
    finally:
        k32.VirtualFree(mem, 0, 0x8000)   # MEM_RELEASE


_V3 = []


def x86_64_v3():
    """Can this PC run code compiled for x86-64-v3 (AVX2, FMA, BMI1/2, F16C, LZCNT, MOVBE)? True / False, None
    when it cannot tell. Asked once per launcher run."""
    if not _V3:
        try:
            if IS_WINDOWS:
                _V3.append(_windows_v3())
            else:
                missing = missing_v3_flags(gpu._read("/proc/cpuinfo"))
                _V3.append(None if missing is None else not missing)
        except Exception:   # detection must never stop the launcher
            _V3.append(None)
    return _V3[0]


# --- override and entry points -------------------------------------------------------------------
_TRUE = ("1", "true", "yes", "on")


def parse_override(text: str) -> dict:
    """FORZA_SPECS='cpu_cores=4,avx2=0,ram=8,vram=4,integrated=0,threads=8,cpu=Name,gpu=Name' -> field values."""
    out = {}
    for item in (text or "").split(","):
        key, _, value = item.partition("=")
        key, value = key.strip().lower(), value.strip()
        try:
            if key in ("cpu_cores", "cores"):
                out["cpu_cores"] = int(value)
            elif key in ("cpu_threads", "threads"):
                out["cpu_threads"] = int(value)
            elif key in ("ram", "ram_gb"):
                out["ram_gb"] = float(value)
            elif key in ("vram", "vram_gb"):
                out["vram_gb"] = float(value)
            elif key in ("avx2", "integrated"):
                out[key] = value.lower() in _TRUE
            elif key in ("cpu", "cpu_name"):
                out["cpu_name"] = value
            elif key in ("gpu", "gpu_name"):
                out["gpu_name"] = value
        except ValueError:
            continue
    return out


def apply_override(s: Specs, text: str) -> Specs:
    values = parse_override(text)
    for k, v in values.items():
        setattr(s, k, v)
    if "vram_gb" in values and "integrated" not in values and s.integrated is None:
        s.integrated = False
    if values:
        s.source = "FORZA_SPECS"
    return s


def detect() -> Specs:
    """This PC's specs. Never raises."""
    try:
        s = detect_windows() if IS_WINDOWS else detect_linux()
    except Exception:  # detection must never stop the launcher
        s = Specs()
    try:
        return apply_override(s, os.environ.get("FORZA_SPECS", ""))
    except Exception:
        return s


def fingerprint() -> str:
    """A cheap look at the installed graphics cards (no subprocess), to notice a new GPU without detecting
    everything again at each start."""
    ids = []
    try:
        if IS_WINDOWS:
            import winreg
            key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as root:
                for i in range(32):
                    try:
                        with winreg.OpenKey(root, f"{i:04d}") as sub:
                            ids.append(str(winreg.QueryValueEx(sub, "DriverDesc")[0]))
                    except OSError:
                        continue
        else:
            for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
                if re.fullmatch(r"card\d+", os.path.basename(card)):
                    ids.append(gpu._read(f"{card}/device/vendor") + ":" + gpu._read(f"{card}/device/device"))
    except Exception:
        pass
    return "|".join(ids) + "|" + os.environ.get("FORZA_SPECS", "")


# --- recommendations and warnings ------------------------------------------------------------------
FPS_BY_CPU = {LOW: 30, MID: 60, HIGH: 60}      # never above 60 automatically
SCALE_BY_GPU = {LOW: 1, MID: 1, HIGH: 2}
# The most a Steam Deck is offered without a warning: 40 fps, one frame per refresh of its screen at 40 Hz
# (LCD) or two at 80 Hz (OLED). It holds it only in light scenes; 30 is its preset.
DECK_FPS = 40
DECK_FRAME_RATES = (20, 30, 40)    # the rates under 60 its screen's refresh rates divide


def deck_offer(s: Specs, settings: dict) -> dict:
    """On a Steam Deck, the values of its preset (gpu.DECK_SETTINGS) that `settings` (effective: Match display
    already a number) do not have; {} anywhere else. A frame rate of 20 or 40 (for a 40 Hz screen) counts as
    the preset's."""
    if not is_deck(s):
        return {}
    out = {}
    for key, value in gpu.DECK_SETTINGS.items():
        if key == "frame_rate" and _int(settings.get(key), 30) in DECK_FRAME_RATES:
            continue
        if settings.get(key, value) != value:
            out[key] = value
    return out


def banner_text(s: Specs) -> str:
    if is_deck(s):
        return ("Steam Deck: use its preset? 30 fps, render scale 1 with AMD FSR 1, Scene anti-aliasing Off and "
                "Console shadow and texture detail run it best (the CPU work of the GPU emulation is its limit).")
    return "Some settings are heavy for this PC."


@dataclass
class Warn:
    key: str            # the setting; '' = about the PC, not a setting
    text: str
    fix: dict = field(default_factory=dict)   # what "use recommended" sets for this warning


def _cpu_label(s: Specs) -> str:
    return short_cpu(s.cpu_name) + (f" ({s.cpu_cores} cores)" if s.cpu_cores else "") if s.cpu_name else "this CPU"


def _gpu_label(s: Specs) -> str:
    return short_gpu(s.gpu_name) + (f" ({_gb(s.vram_gb)} GB)" if s.vram_gb else "") if s.gpu_name else "this graphics card"


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def warnings(s: Specs, settings: dict) -> list:
    """[Warn] for the settings that are heavy for this PC."""
    out = []
    g, c = gpu_tier(s), cpu_tier(s)
    temporal = settings.get("present_effect") == "fsr3" or settings.get("fh1_upscaler", "off") != "off"
    scale, fps = _int(settings.get("resolution_scale"), 1), _int(settings.get("frame_rate"), 30)
    if g in (LOW, MID) and scale >= 2 and not temporal and not settings.get("forza_ultrawide"):
        size = f"{GUEST_SIZE[0] * scale}x{GUEST_SIZE[1] * scale}"
        out.append(Warn("resolution_scale", f"Render scale {scale} renders {size}. {_gpu_label(s)} usually can't "
                        "hold 60 fps there; try 1 with FSR 1.", {"resolution_scale": 1}))
    elif g == HIGH and scale >= 3 and not temporal and not settings.get("forza_ultrawide"):
        size = f"{GUEST_SIZE[0] * scale}x{GUEST_SIZE[1] * scale}"
        out.append(Warn("resolution_scale", f"Render scale {scale} renders {size}, which is heavy even for "
                        f"{_gpu_label(s)}; 2 is the most tested.", {"resolution_scale": 2}))
    limit = {LOW: 30, MID: 60}.get(c)
    if is_deck(s):
        if fps > DECK_FPS:
            out.append(Warn("frame_rate", f"{fps} fps is more than a Steam Deck holds: the CPU work of the "
                            "GPU emulation sets its frame rate. Use 30, or 40 with the screen at 40 Hz (LCD) "
                            "or 80 Hz (OLED).", {"frame_rate": 30}))
        if settings.get("scene_msaa", "game") != "off":
            out.append(Warn("scene_msaa", "On a Steam Deck the GPU emulation's CPU work sets the frame rate, and "
                            "most of it is the scene's three 4x MSAA passes. Off draws the scene in one pass: "
                            "about a fifth less of that work.", {"scene_msaa": "off"}))
    elif limit and fps > limit:
        out.append(Warn("frame_rate", f"{fps} fps needs a fast CPU: the game's simulation runs on the CPU. "
                        f"A {_cpu_label(s)} may not hold it in free roam; try {limit}.", {"frame_rate": limit}))
    if g in (LOW, MID) and fps > 60:
        out.append(Warn("frame_rate", f"Above 60 fps the graphics card is usually the limit on {_gpu_label(s)}, "
                        "even at render scale 1: the game's graphics are emulated and cost far more GPU time than "
                        "the resolution suggests.", {"frame_rate": 60}))
    if g == LOW and settings.get("present_effect") == "fsr3":
        out.append(Warn("present_effect", f"AMD FSR 3 is heavy for {_gpu_label(s)}; AMD FSR 1 costs far less.",
                        {"present_effect": "fsr"}))
    if g == LOW and settings.get("forza_antialiasing") in ("smaa", "fsr3_native"):
        out.append(Warn("forza_antialiasing", f"This anti-aliasing is an extra pass on every frame, and "
                        f"{_gpu_label(s)} has little to spare; the game's own is cheaper.",
                        {"forza_antialiasing": "game"}))
    if s.avx2 is False:
        out.append(Warn("", f"{_cpu_label(s)} is an older CPU without AVX2: expect a lower frame rate, "
                        "above all in free roam; 30 fps is the safe choice."))
    if 0 < s.ram_gb < RAM_LOW_GB:
        out.append(Warn("", f"This PC has {_gb(s.ram_gb)} GB of RAM; the game wants 8 GB or more."))
    return out


def recommended(s: Specs, settings: dict, vendor: str = "") -> dict:
    """The settings a new launcher.json should have for this PC, applied on top of the vendor's preset:
    only the values that differ from `settings`. The frame rate may only go below the vendor's; a value the
    vendor's known problems reject is left out."""
    g, c = gpu_tier(s), cpu_tier(s)
    want = {}
    if c in FPS_BY_CPU:
        want["frame_rate"] = min(FPS_BY_CPU[c], _int(settings.get("frame_rate"), 60))
    if g in SCALE_BY_GPU:
        want["resolution_scale"] = SCALE_BY_GPU[g]
        if g == LOW and settings.get("present_effect") == "fsr3":
            want["present_effect"] = "fsr"
    want.update(deck_offer(s, {**settings, **want}))
    out = {}
    for key, value in want.items():
        if settings.get(key) != value and not gpu.warnings(vendor, {key: value}):
            out[key] = value
    return out
