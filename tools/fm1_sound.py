"""Forza Motorsport (2005) engine sound for an FM1 car imported into FH1 (#930).

FM1 keeps a car's engine sound in its cab, under engines/<engine>/audio/:
  engineaudio.ini           PhysicsRPMMin/Max (the engine's idle and limiter) and RPMMin/Max (the
                            same in the rpm the recordings are tuned in)
  lod1/<engine>.xwb         the recordings: an XACT 1 wave bank (version 3), Xbox ADPCM
  lod1/l00..l03/<layer>.ini one file per upgrade level (stock, then the three exhaust/intake levels)
                            and layer: enginea (the engine, heard as FH1's intake), exhaust, and
                            trans, turbo, supercharger, exhaustpop, whose waves FM1 shares
                            between cars (not in the car's bank: FH1's own are kept)
A layer file lists Track<n>\\RPMMin, RPMSample, RPMMax, PitchMin, PitchSample, PitchMax,
VolumeAdjust (millibels, 2000 = full) and WaveName: a loop recorded at RPMSample, cross-faded
between RPMMin and RPMMax. Unused tracks hold filler bytes instead of a name.

FH1 has the same model: an engine's EngineTuning (<MediaName>_ET.xml) names a HarmonicTuning file
per part and upgrade level (EngineIntake, Exhaust, EngineAmbient; L0-L3), and that file names its
bank (<Soundbank name="x.fsb">) and lists its loops (<Loop wavebankindex rpm_min rpm_sample
rpm_max volume pitch pitchMin pitchMax/>), with the part's effects (DSP, burbles) after them.
FH1's banks are FMOD FSB4 files of XMA; the banks written here hold 16-bit PCM, which FMOD plays
without a hardware decoder.

The game reads a bank only as a loose file next to a DLC package's zip (docs/modding/fm4-cars.md,
#776), so the banks go into a package; the tuning files go into the mod.

Plain Python: no numpy.
"""
import array
import re
import struct
import sys

XWB_MAGIC = b"WBND"
LAYERS = (("enginea", "EngineIntake", "Int"), ("exhaust", "Exhaust", "Exh"))
# FH1 tunings to start from where the matched engine's is a placeholder without loops (NA.xml):
# the 1995 MR2 Turbo's intake and the SR20 exhaust FH1 gives it
FALLBACK = {"Int": "4T3_MR2_Int_O398_HT.xml", "Exh": "4T2_SR20_Exh_O398_HT.xml"}
LEVELS = 4
ADPCM_BLOCK = 36            # one channel: s16 sample, u8 step index, u8 pad, 32 bytes of nibbles
ADPCM_SAMPLES = 64
STEPS = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
         80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
         544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499,
         2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
         12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767)
INDEX = (-1, -1, -1, -1, 2, 4, 6, 8)
# FSB4 (FMOD Ex): header flag for big-endian PCM; sample modes
FSB_BIGENDIANPCM = 0x8
MODE_LOOP_NORMAL, MODE_16BITS, MODE_MONO = 0x2, 0x10, 0x20


class Fm1SoundError(ValueError):
    pass


class Wave:
    def __init__(self, name, codec, channels, rate, data, loop_start, loop_length):
        self.name, self.codec, self.channels, self.rate = name, codec, channels, rate
        self.data, self.loop_start, self.loop_length = data, loop_start, loop_length

    def pcm(self):
        """(int16 samples, loop start, loop end): loop points in samples, the end exclusive."""
        if self.codec == 0:
            s = array.array("h", self.data[:len(self.data) // 2 * 2])
            if sys.byteorder == "big":
                s.byteswap()
            per = 2 * self.channels
            return s, self.loop_start // per, (self.loop_start + self.loop_length) // per
        if self.codec != 1:
            raise Fm1SoundError("%s: wave format %d is not PCM or Xbox ADPCM" % (self.name, self.codec))
        if self.channels != 1:
            raise Fm1SoundError("%s: %d channels (engine loops are mono)" % (self.name, self.channels))
        s = decode_xbox_adpcm(self.data)
        a = self.loop_start // ADPCM_BLOCK * ADPCM_SAMPLES
        b = (self.loop_start + self.loop_length) // ADPCM_BLOCK * ADPCM_SAMPLES
        return s, a, min(b, len(s)) if self.loop_length else len(s)


def read_xwb(data):
    """{name: Wave} of an XACT wave bank, version 3 (FM1's): segments bank data, entry metadata,
    entry names and wave data; each entry: flags/duration, format (codec 2 bits, channels 3, rate
    26), play offset and length, loop start and length (bytes)."""
    if data[:4] != XWB_MAGIC:
        raise Fm1SoundError("not an XACT wave bank")
    version, = struct.unpack_from("<I", data, 4)
    if version != 3:
        raise Fm1SoundError("wave bank version %d (FM1's are 3)" % version)
    bank_off, _, meta_off, _, names_off, _, wave_off, _ = struct.unpack_from("<8I", data, 8)
    flags, count = struct.unpack_from("<II", data, bank_off)
    meta_size, name_size = struct.unpack_from("<II", data, bank_off + 24)
    if flags & 0x20000:
        raise Fm1SoundError("compact wave bank")
    waves = {}
    for i in range(count):
        _, fmt, off, length, ls, ll = struct.unpack_from("<6I", data, meta_off + meta_size * i)
        p = names_off + name_size * i
        name = data[p:p + name_size].split(b"\0")[0].decode("latin-1")
        codec, channels, rate = fmt & 3, (fmt >> 2) & 7, (fmt >> 5) & 0x3FFFFFF
        waves[name.lower()] = Wave(name, codec, channels, rate, data[wave_off + off:wave_off + off + length], ls, ll)
    return waves


def decode_xbox_adpcm(data):
    """Mono Xbox ADPCM (IMA, 36-byte blocks of 64 samples; the header sample starts the block and
    is not played) -> array('h')."""
    out = array.array("h")
    for b in range(0, len(data) - ADPCM_BLOCK + 1, ADPCM_BLOCK):
        pred, index = struct.unpack_from("<hB", data, b)
        index = min(max(index, 0), 88)
        for byte in data[b + 4:b + ADPCM_BLOCK]:
            for nib in (byte & 15, byte >> 4):
                step = STEPS[index]
                diff = step >> 3
                if nib & 4:
                    diff += step
                if nib & 2:
                    diff += step >> 1
                if nib & 1:
                    diff += step >> 2
                pred = max(-32768, min(32767, pred - diff if nib & 8 else pred + diff))
                index = min(max(index + INDEX[nib & 7], 0), 88)
                out.append(pred)
    return out


def fsb4(waves):
    """An FMOD FSB4 bank of mono 16-bit big-endian PCM loops: waves [(name, samples, rate, loop
    start, loop end)]. Every sample's data is padded to 32 bytes (FSB4 aligns them)."""
    heads, body = [], []
    for name, samples, rate, a, b in waves:
        s = array.array("h", samples)
        while len(s) % 16:
            s.append(0)
        if sys.byteorder == "little":
            s.byteswap()
        raw = s.tobytes()
        nm = name.encode("ascii")[:29]
        heads.append(struct.pack("<H30s6IHhHHff", 80, nm, len(s), len(raw), a, max(a, b - 1),
                                 MODE_LOOP_NORMAL | MODE_16BITS | MODE_MONO, rate, 255, 128, 128, 1,
                                 1.0, 10000.0) + struct.pack("<IHh", 0, 0, 0))
        body.append(raw)
    shdr = b"".join(heads)
    shdr += b"\0" * (-(48 + len(shdr)) % 32)
    data = b"".join(body)
    return (struct.pack("<4s5I", b"FSB4", len(waves), len(shdr), len(data), 0x40000, FSB_BIGENDIANPCM)
            + b"\0" * 24 + shdr + data)


def read_ini(text):
    """FM1's 'Key value' lines -> {key: value}; values that are filler bytes become ''."""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if parts:
            v = parts[1].strip() if len(parts) > 1 else ""
            out[parts[0]] = v if re.fullmatch(r"[\x20-\x7e]*", v) else ""
    return out


def tracks(ini, waves):
    """The layer's loops in rpm order: dicts of FM1's Track fields, those whose wave the bank has."""
    out = []
    for n in range(64):
        k = "Track%d\\" % n
        if k + "WaveName" not in ini:
            break
        name = ini[k + "WaveName"]
        if not name or name.lower() not in waves:
            continue
        t = {f: float(ini.get(k + f, 0)) for f in ("RPMMin", "RPMSample", "RPMMax", "PitchMin", "PitchSample",
                                                     "PitchMax", "VolumeAdjust")}
        t["WaveName"] = waves[name.lower()].name
        out.append(t)
    return sorted(out, key=lambda t: t["RPMSample"])


def car_sound(cab):
    """FM1's engine sound in a car's cab (fm1_cars.Cab): {"rpm": (audio idle, audio limiter),
    "physics_rpm": (idle, limiter), "waves": {name: Wave}, "levels": [{part: [tracks]}] * 4,
    "bank": its path in the cab}, or None when the cab has none. The near sound (lod1) where the
    cab has its bank and settings, else the far one (lod2: some cabs hold only that, the F50's)."""
    for lod in ("lod1", "lod2"):
        banks = sorted(n for n in cab.files if re.fullmatch(r"engines\\[^\\]+\\audio\\%s\\[^\\]+\.xwb" % lod, n))
        if not banks:
            continue
        base = banks[0].rsplit("\\%s\\" % lod, 1)[0]
        waves = read_xwb(cab.read(banks[0]))
        levels = []
        for lv in range(LEVELS):
            parts = {}
            for fm1, _, part in LAYERS:
                name = "%s\\%s\\l%02d\\%s.ini" % (base, lod, lv, fm1)
                if name in cab.files:
                    parts[part] = tracks(read_ini(cab.read(name).decode("latin-1")), waves)
            if lv and not any(parts.values()):
                parts = levels[-1]
            levels.append(parts)
        if not any(levels[0].values()):
            continue
        ini = base + "\\engineaudio.ini"
        eng = read_ini(cab.read(ini).decode("latin-1")) if ini in cab.files else {}
        rpm = lambda a, b: (float(eng[a]), float(eng[b])) if a in eng and b in eng else None  # noqa: E731
        return {"rpm": rpm("RPMMin", "RPMMax"), "physics_rpm": rpm("PhysicsRPMMin", "PhysicsRPMMax"),
                "waves": waves, "levels": levels, "bank": banks[0]}
    return None


def volume(mb):
    """FM1's VolumeAdjust (millibels, 2000 = full) -> FH1's linear loop volume."""
    return min(1.0, 10 ** ((mb - 2000.0) / 2000.0))


def loops_xml(tracks_, index, indent):
    return "".join('%s<Loop wavebankindex="%d" rpm_min="%d" rpm_sample="%d" rpm_max="%d" volume="%.6f" '
                   'pitch="%.6f" pitchMin="%.6f" pitchMax="%.6f"/>\n'
                   % (indent, index[t["WaveName"]], round(t["RPMMin"]), round(t["RPMSample"]), round(t["RPMMax"]),
                      volume(t["VolumeAdjust"]), t["PitchSample"], t["PitchMin"], t["PitchMax"]) for t in tracks_)


LOOPS = re.compile(r"(<(\w+)>[ \t]*\r?\n)((?:[ \t]*<Loop [^>]*/>[ \t]*\r?\n)+)([ \t]*</\2>)")


def harmonic(template, bank, tracks_, index):
    """FH1's harmonic tuning template with FM1's bank and loops in place of its own."""
    t = re.sub(r'<Soundbank name="[^"]*"', '<Soundbank name="%s"' % bank, template, count=1)
    m = LOOPS.search(t)
    if not m:
        raise Fm1SoundError("the harmonic tuning template has no loop list")
    indent = re.match(r"[ \t]*", m.group(3)).group(0)
    return t[:m.start(3)] + loops_xml(tracks_, index, indent) + t[m.end(3):]


def fh1_files(sound, media, et, read_harmonic):
    """FM1's sound as FH1 files for the car whose engine sound is <media>_ET.xml:
    et: FH1's EngineTuning to start from (the text, already renamed for media);
    read_harmonic(name): an FH1 HarmonicTuning file's text, or None.
    -> (the new EngineTuning text, {harmonic tuning file name: text}, {bank file name: bytes})."""
    banks, tunings = {}, {}
    for _, element, part in LAYERS:
        used = []
        for lv in sound["levels"]:
            for t in lv.get(part, []):
                if t["WaveName"] not in used:
                    used.append(t["WaveName"])
        if not used:
            continue
        bank = "%s_FM1_%s.fsb" % (media, part)
        index = {n: i for i, n in enumerate(used)}
        waves = []
        for n in used:
            w = sound["waves"][n.lower()]
            s, a, b = w.pcm()
            waves.append((w.name, s, w.rate, a, b))
        banks[bank] = fsb4(waves)
        m = re.search(r"<%s>\s*<Upgrade ([^>]*)/>" % element, et)
        if not m:
            raise Fm1SoundError("the EngineTuning has no %s" % element)
        attrs = dict(re.findall(r'(L\d)="([^"]*)"', m.group(1)))
        names = {}
        for lv in range(LEVELS):
            src = attrs.get("L%d" % lv) or attrs.get("L0")
            template = read_harmonic(src) if src else None
            if template is None or not LOOPS.search(template):
                # FH1's placeholder (NA.xml, NA_Int_HT.xml: no sound) or a file it lacks: one of
                # its own tunings of the same part
                template = read_harmonic(FALLBACK[part])
            if template is None:
                raise Fm1SoundError("FH1 has neither the harmonic tuning %s nor %s" % (src, FALLBACK[part]))
            name = "%s_FM1_%s_L%d_HT.xml" % (media, part, lv)
            loops = sound["levels"][lv].get(part) or next(lvl[part] for lvl in sound["levels"] if lvl.get(part))
            tunings[name] = harmonic(template, bank, loops, index)
            names["L%d" % lv] = name
        et = et[:m.start(1)] + " ".join('%s="%s"' % (k, v) for k, v in names.items()) + et[m.end(1):]
    if sound.get("rpm"):
        idle, top = sound["rpm"]
        et = re.sub(r'audio_rpm_idle="[^"]*"', 'audio_rpm_idle="%d"' % round(idle), et, count=1)
        et = re.sub(r'audio_rpm_redline="[^"]*"', 'audio_rpm_redline="%d"' % round(top), et, count=1)
    return et, tunings, banks


def main(argv):
    import argparse
    import os
    import wave
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fm1_cars
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("fm1", help="FM1's disc folder")
    ap.add_argument("car", help="FM1 car name (its cab: media/cabs/car_<car>.cab)")
    ap.add_argument("--wav", help="write each wave of the car's bank as a .wav into this folder")
    a = ap.parse_args(argv)
    cab, _ = fm1_cars.load_car(a.fm1, a.car)
    s = car_sound(cab)
    if s is None:
        print("%s: no engine sound in its cab" % a.car)
        return 1
    print("bank", s["bank"], "audio rpm", s["rpm"], "physics rpm", s["physics_rpm"])
    for lv, parts in enumerate(s["levels"]):
        for part, ts in parts.items():
            print("L%d %s: %s" % (lv, part, " ".join("%s@%d" % (t["WaveName"], t["RPMSample"]) for t in ts)))
    if a.wav:
        os.makedirs(a.wav, exist_ok=True)
        for w in s["waves"].values():
            samples, _, _ = w.pcm()
            if sys.byteorder == "big":
                samples.byteswap()
            with wave.open(os.path.join(a.wav, w.name + ".wav"), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(w.rate)
                f.writeframes(samples.tobytes())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
