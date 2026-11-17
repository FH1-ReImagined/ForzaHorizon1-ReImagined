"""Tests for fm1_sound (FM1 engine sound as FH1 banks and tuning) and fm1_import's sound package.
Synthetic data only: no game file is needed."""
import array
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm1_sound  # noqa: E402


def xwb(waves):
    """An XACT wave bank, version 3: waves [(name, codec, channels, rate, data, loop_start, loop_length)]."""
    meta_size, name_size = 24, 64
    bank_off = 0x28
    meta_off = bank_off + 0x28
    names_off = meta_off + meta_size * len(waves)
    wave_off = names_off + name_size * len(waves)
    meta, names, body = b"", b"", b""
    for name, codec, ch, rate, data, ls, ll in waves:
        fmt = codec | (ch << 2) | (rate << 5)
        meta += struct.pack("<6I", 0, fmt, len(body), len(data), ls, ll)
        names += name.encode().ljust(name_size, b"\0")
        body += data
    head = b"WBND" + struct.pack("<I", 3) + struct.pack("<8I", bank_off, 0x28, meta_off, len(meta), names_off,
                                                          len(names), wave_off, len(body))
    head = head.ljust(bank_off, b"\0")
    bank = struct.pack("<II", 0x10000, len(waves)) + b"Test".ljust(16, b"\0") + struct.pack("<II", meta_size, name_size)
    return head + bank.ljust(0x28, b"\0") + meta + names + body


def adpcm_block(pred=0, index=0, nibbles=0x77):
    return struct.pack("<hBB", pred, index, 0) + bytes([nibbles]) * 32


class Cab:
    def __init__(self, files):
        self.files = {k.lower(): None for k in files}
        self._data = {k.lower(): v for k, v in files.items()}

    def read(self, name):
        return self._data[name.lower()]


def layer_ini(tracks):
    lines = ["VolOffsetMin -997", "AddUpgradeVolume true"]
    for i, (name, rmin, rs, rmax, vol) in enumerate(tracks):
        lines += ["Track%d\\RPMMin %d" % (i, rmin), "Track%d\\RPMSample %d" % (i, rs), "Track%d\\RPMMax %d" % (i, rmax),
                  "Track%d\\PitchMin 1" % i, "Track%d\\PitchSample 1.05" % i, "Track%d\\PitchMax 0.95" % i,
                  "Track%d\\VolumeAdjust %d" % (i, vol), "Track%d\\WaveName %s" % (i, name)]
    lines += ["Track%d\\RPMMin 1" % len(tracks), "Track%d\\WaveName \xcd\xcd\xcd\xcd" % len(tracks)]
    return "\r\n".join(lines).encode("latin-1")


ET = """<Engine>
  <EngineSettings audio_rpm_idle="869" audio_rpm_redline="7364" num_cylinders="4"/>
  <EngineAmbient>
    <Upgrade L0="X_EngAmb_HT.xml"/>
  </EngineAmbient>
  <EngineIntake>
    <Upgrade L0="X_Int_HT.xml" L1="X_Int_HT.xml" L2="X_Int_HT.xml" L3="X_Int_HT.xml"/>
  </EngineIntake>
  <Exhaust>
    <Upgrade L0="X_Exh_HT.xml" L1="X_Exh_HT.xml" L2="X_Exh2_HT.xml" L3="X_Exh2_HT.xml"/>
  </Exhaust>
</Engine>
"""
HT = """
<Soundbank name="X_%s.fsb">
  <%s>
    <Loop wavebankindex="0" rpm_min="500" rpm_sample="803" rpm_max="1066" volume="1.000000" pitch="1.000000" pitchMin="1.000000" pitchMax="1.000000"/>
    <Loop wavebankindex="1" rpm_min="816" rpm_sample="1079" rpm_max="20000" volume="0.800000" pitch="1.000000" pitchMin="1.000000" pitchMax="1.000000"/>
  </%s>
  <DSP>
    <Volume Marker="%s"/>
  </DSP>
</Soundbank>
"""


def harmonics(name):
    part = {"X_Int_HT.xml": ("Int", "EngineIntake"), "X_Exh_HT.xml": ("Exh", "ExhaustL"),
            "X_Exh2_HT.xml": ("Exh", "ExhaustL")}.get(name)
    return HT % (part[0], part[1], part[1], name) if part else None


def sound_cab():
    waves = [("EngA_01000", 1, 1, 22050, adpcm_block() * 4, 36, 72),
             ("EngA_03000", 1, 1, 22050, adpcm_block() * 4, 0, 0),
             ("Exh0_01000", 0, 1, 48000, struct.pack("<4h", 1, -2, 3, -4), 2, 4),
             ("Exh2_05000", 0, 1, 48000, struct.pack("<2h", 5, 6), 0, 4)]
    base = "engines\\car\\audio\\"
    files = {base + "lod1\\car.xwb": xwb(waves),
             base + "engineaudio.ini": b"PhysicsRPMMin 880\r\nPhysicsRPMMax 8000\r\nRPMMin 1020\r\nRPMMax 8160\r\n"}
    for lv in range(4):
        files[base + "lod1\\l%02d\\enginea.ini" % lv] = layer_ini([("EngA_03000", 2500, 3000, 9000, 2000),
                                                                   ("EngA_01000", 600, 1000, 2000, 1000)])
        exh = "Exh2_05000" if lv >= 2 else "Exh0_01000"
        files[base + "lod1\\l%02d\\exhaust.ini" % lv] = layer_ini([("Exh0_01000", 600, 1000, 4000, 2000),
                                                                   (exh, 4000, 5000, 9000, 2000)])
    return Cab(files)


class Decoding(unittest.TestCase):
    def test_xbox_adpcm(self):
        # nibble 7 at step index 0 (step 7): +0 +7 +3 +1 = 11, index 8 (step 16): +2 +16 +8 +4 = 30
        s = fm1_sound.decode_xbox_adpcm(adpcm_block())
        self.assertEqual(len(s), 64)
        self.assertEqual(list(s[:2]), [11, 41])
        neg = fm1_sound.decode_xbox_adpcm(adpcm_block(pred=100, nibbles=0xCC))
        self.assertEqual(list(neg[:2]), [93, 83])   # nibble 0xC: -(0 + 7) at step 7, then index 2 (step 9): -(1 + 9)

    def test_xbox_adpcm_clamps(self):
        s = fm1_sound.decode_xbox_adpcm(adpcm_block(pred=32767, index=88))
        self.assertEqual(max(s), 32767)

    def test_read_xwb(self):
        waves = fm1_sound.read_xwb(sound_cab().read("engines\\car\\audio\\lod1\\car.xwb"))
        self.assertEqual(sorted(waves), ["enga_01000", "enga_03000", "exh0_01000", "exh2_05000"])
        a = waves["enga_01000"]
        self.assertEqual((a.name, a.codec, a.channels, a.rate), ("EngA_01000", 1, 1, 22050))
        s, start, end = a.pcm()
        self.assertEqual((len(s), start, end), (256, 64, 192))    # 36-byte blocks of 64 samples
        p = waves["exh0_01000"]
        s, start, end = p.pcm()
        self.assertEqual((list(s), start, end), ([1, -2, 3, -4], 1, 3))

    def test_not_a_bank(self):
        with self.assertRaises(fm1_sound.Fm1SoundError):
            fm1_sound.read_xwb(b"RIFF" + bytes(64))

    def test_stereo_adpcm_refused(self):
        w = fm1_sound.Wave("x", 1, 2, 22050, adpcm_block() * 2, 0, 0)
        with self.assertRaises(fm1_sound.Fm1SoundError):
            w.pcm()


class Banks(unittest.TestCase):
    def test_fsb4(self):
        d = fm1_sound.fsb4([("Loop_A", array.array("h", [1, -2, 3]), 22050, 0, 3),
                            ("Loop_B", array.array("h", range(20)), 48000, 4, 20)])
        magic, n, shsize, dsize, ver, mode = struct.unpack_from("<4s5I", d, 0)
        self.assertEqual((magic, n, ver, mode), (b"FSB4", 2, 0x40000, fm1_sound.FSB_BIGENDIANPCM))
        self.assertEqual((48 + shsize) % 32, 0)
        self.assertEqual(len(d), 48 + shsize + dsize)
        size, = struct.unpack_from("<H", d, 48)
        name = d[50:80].split(b"\0")[0]
        length, comp, ls, le, smode, freq = struct.unpack_from("<6I", d, 80)
        self.assertEqual((size, name, length, comp, ls, le, freq), (80, b"Loop_A", 16, 32, 0, 2, 22050))
        self.assertEqual(smode, fm1_sound.MODE_LOOP_NORMAL | fm1_sound.MODE_16BITS | fm1_sound.MODE_MONO)
        data = d[48 + shsize:]
        self.assertEqual(struct.unpack(">3h", data[:6]), (1, -2, 3))      # big-endian PCM
        self.assertEqual(struct.unpack_from("<4I", d, 48 + 80 + 32), (32, 64, 4, 19))  # B: 32 samples padded

    def test_volume(self):
        self.assertEqual(fm1_sound.volume(2000), 1.0)
        self.assertAlmostEqual(fm1_sound.volume(0), 0.1)
        self.assertEqual(fm1_sound.volume(3000), 1.0)


class Tuning(unittest.TestCase):
    def test_tracks(self):
        waves = {"enga_01000": fm1_sound.Wave("EngA_01000", 0, 1, 1, b"", 0, 0)}
        ini = fm1_sound.read_ini(layer_ini([("EngA_01000", 600, 1000, 2000, 1500),
                                            ("Missing", 1, 2, 3, 4)]).decode("latin-1"))
        t = fm1_sound.tracks(ini, waves)
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["WaveName"], t[0]["RPMSample"], t[0]["VolumeAdjust"]), ("EngA_01000", 1000, 1500))

    def test_car_sound(self):
        s = fm1_sound.car_sound(sound_cab())
        self.assertEqual(s["rpm"], (1020, 8160))
        self.assertEqual(s["physics_rpm"], (880, 8000))
        self.assertEqual([t["WaveName"] for t in s["levels"][0]["Int"]], ["EngA_01000", "EngA_03000"])
        self.assertEqual([t["WaveName"] for t in s["levels"][3]["Exh"]], ["Exh0_01000", "Exh2_05000"])

    def test_no_sound(self):
        self.assertIsNone(fm1_sound.car_sound(Cab({"cars\\car\\car.carbin": b""})))

    def test_harmonic(self):
        t = HT % ("Exh", "ExhaustL", "ExhaustL", "keep")
        tr = [{"WaveName": "W", "RPMMin": 600, "RPMSample": 1000, "RPMMax": 2000, "PitchMin": 1, "PitchSample": 1.1,
               "PitchMax": 0.9, "VolumeAdjust": 2000}]
        out = fm1_sound.harmonic(t, "New.fsb", tr, {"W": 3})
        self.assertIn('<Soundbank name="New.fsb">', out)
        self.assertEqual(out.count("<Loop "), 1)
        self.assertIn('wavebankindex="3" rpm_min="600" rpm_sample="1000" rpm_max="2000" volume="1.000000" '
                      'pitch="1.100000" pitchMin="1.000000" pitchMax="0.900000"', out)
        self.assertIn('<Volume Marker="keep"/>', out)   # the template's effects stay

    def test_harmonic_without_loops(self):
        with self.assertRaises(fm1_sound.Fm1SoundError):
            fm1_sound.harmonic("<Soundbank name=\"x.fsb\">\n</Soundbank>", "y.fsb", [], {})

    def test_fh1_files(self):
        s = fm1_sound.car_sound(sound_cab())
        et, tunings, banks = fm1_sound.fh1_files(s, "TOY_Test_95", ET, harmonics)
        self.assertEqual(sorted(banks), ["TOY_Test_95_FM1_Exh.fsb", "TOY_Test_95_FM1_Int.fsb"])
        self.assertEqual(struct.unpack_from("<I", banks["TOY_Test_95_FM1_Exh.fsb"], 4)[0], 2)  # Exh0 + Exh2
        self.assertEqual(len(tunings), 8)
        self.assertIn('audio_rpm_idle="1020" audio_rpm_redline="8160"', et)
        self.assertIn('<Upgrade L0="X_EngAmb_HT.xml"/>', et)    # the ambient part stays FH1's
        self.assertIn('L2="TOY_Test_95_FM1_Exh_L2_HT.xml"', et)
        l2 = tunings["TOY_Test_95_FM1_Exh_L2_HT.xml"]
        self.assertIn('<Soundbank name="TOY_Test_95_FM1_Exh.fsb">', l2)
        self.assertIn('<Volume Marker="X_Exh2_HT.xml"/>', l2)  # level 2's own FH1 template
        self.assertIn('wavebankindex="1" rpm_min="4000" rpm_sample="5000"', l2)
        l0 = tunings["TOY_Test_95_FM1_Exh_L0_HT.xml"]
        self.assertIn('wavebankindex="0" rpm_min="4000" rpm_sample="5000"', l0)   # Exh0_01000 again at 5000

    def test_part_missing_at_a_level(self):
        s = fm1_sound.car_sound(sound_cab())
        s["levels"][0] = {"Int": s["levels"][0]["Int"]}         # no exhaust at stock
        et, tunings, banks = fm1_sound.fh1_files(s, "TOY_Test_95", ET, harmonics)
        self.assertIn("wavebankindex", tunings["TOY_Test_95_FM1_Exh_L0_HT.xml"])  # level 1's loops

    def test_placeholder_template(self):
        s = fm1_sound.car_sound(sound_cab())
        placeholder = '<Soundbank name="NA.fsb">\n  <EngineIntake>\n  </EngineIntake>\n</Soundbank>\n'
        read = {"X_Int_HT.xml": placeholder, fm1_sound.FALLBACK["Int"]: HT % ("Int", "EngineIntake", "EngineIntake", "fb"),
                "X_Exh_HT.xml": harmonics("X_Exh_HT.xml"), "X_Exh2_HT.xml": harmonics("X_Exh2_HT.xml")}.get
        et, tunings, banks = fm1_sound.fh1_files(s, "TOY_Test_95", ET, read)
        self.assertIn('<Volume Marker="fb"/>', tunings["TOY_Test_95_FM1_Int_L0_HT.xml"])

    def test_far_sound_only(self):
        cab = sound_cab()
        far = Cab({k.replace("\\lod1\\", "\\lod2\\"): cab.read(k) for k in cab.files})
        s = fm1_sound.car_sound(far)
        self.assertEqual(s["bank"], "engines\\car\\audio\\lod2\\car.xwb")
        self.assertEqual(len(s["levels"][0]["Exh"]), 2)

    def test_missing_template(self):
        s = fm1_sound.car_sound(sound_cab())
        with self.assertRaises(fm1_sound.Fm1SoundError):
            fm1_sound.fh1_files(s, "TOY_Test_95", ET, lambda n: None)


class Package(unittest.TestCase):
    def test_write_package(self):
        import fm1_import
        with tempfile.TemporaryDirectory() as td:
            pkg = os.path.join(td, "4D5309C9", "00000002", fm1_import.PACKAGE_NAME)
            files = {fm1_import.BANKS + "A_FM1_Exh.fsb": b"FSB4a", "media/db/gamedb.slt": b"db"}
            self.assertEqual(fm1_import.write_package(pkg, files), ["A_FM1_Exh.fsb"])
            dz = os.path.join(pkg, "Media", "DLCZips")
            with open(os.path.join(dz, fm1_import.BANKS_FOLDER, "A_FM1_Exh.fsb"), "rb") as f:
                self.assertEqual(f.read(), b"FSB4a")
            with open(os.path.join(dz, "zipmount.xml"), encoding="ascii") as f:
                mount = f.read()
            self.assertIn('Name="%s" Mount="game:\\Media\\audio\\cars\\Engines\\Soundbanks\\LOD1\\"'
                          % fm1_import.BANKS_FOLDER, mount)
            self.assertTrue(os.path.isfile(os.path.join(pkg, "Media", "%d.puboffer" % fm1_import.PACKAGE_OFFER)))
            header = os.path.join(td, "4D5309C9", "Headers", "00000002", fm1_import.PACKAGE_NAME + ".header")
            self.assertEqual(os.path.getsize(header), 0x14C)
            mod = os.path.join(td, "mod")
            fm1_import.write_mod(mod, files, {"names": ("A (FM1)", "A (FM1)"), "id": 1})
            self.assertTrue(os.path.isfile(os.path.join(mod, "media", "db", "gamedb.slt")))
            self.assertFalse(any("fsb" in f.lower() for _, _, fs in os.walk(mod) for f in fs))

    def test_no_banks_no_package(self):
        import fm1_import
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fm1_import.write_package(os.path.join(td, "p"), {"media/x": b""}), [])
            self.assertFalse(os.path.exists(os.path.join(td, "p")))


if __name__ == "__main__":
    unittest.main()
