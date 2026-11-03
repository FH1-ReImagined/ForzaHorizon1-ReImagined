import copy
import os
import struct
import sys
import tempfile
import unittest
import shutil
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import radio
from forza_launcher.config import Config


def bank_file(path, size=480):
    header = bytearray(48)
    header[:4] = b"FSB4"
    sample = radio.sample_header("original", 1152, size)
    padding = (-(48 + len(sample))) % 16
    struct.pack_into("<5I", header, 4, 1, len(sample) + padding, size, 0x40000, 0x40)
    path.write_bytes(header + sample + bytes(padding) + bytes(size))


def string_file():
    text = "ORIGINAL\0".encode("utf-16-be")
    key = 41
    section = struct.pack(">IIHIHI", 8 + 12 + len(text), 1, key, 0, 0xFFFF, len(text) // 2) + text
    ids = struct.pack(">IIHHI", 16 + len(b"IDS_RadioStation_Option_1\0"), 1, key, 0, 0) + b"IDS_RadioStation_Option_1\0"
    return b"LSB2" + struct.pack(">6I", 1, 0, 28, 2, 28, 28 + len(section)) + section + ids


class RadioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_original_bank_bytes_and_indexes_survive_append(self):
        source, dest, audio = [self.root / n for n in ("source.fsb", "built.fsb", "song.mp3")]
        bank_file(source)
        before = source.read_bytes()
        audio.write_bytes(bytes([19]) * 480)
        radio.write_bank(source, dest, [(radio.sample_header("custom", 1152, 480), audio)])
        old, new = radio.read_bank(source), radio.read_bank(dest)
        self.assertEqual(len(new.samples), 2)
        self.assertEqual(new.data_offset % 16, 0)
        self.assertEqual(new.samples[0], old.samples[0])
        self.assertEqual(dest.read_bytes()[new.data_offset:new.data_offset + old.data_size], before[old.data_offset:])
        self.assertEqual(dest.read_bytes()[-480:], audio.read_bytes())
        self.assertEqual(source.read_bytes(), before)

    def test_no_additions_round_trip_is_byte_identical(self):
        source, dest = self.root / "source.fsb", self.root / "built.fsb"
        bank_file(source)
        radio.write_bank(source, dest, [])
        self.assertEqual(source.read_bytes(), dest.read_bytes())

    def test_truncated_or_wrong_format_rejected(self):
        path = self.root / "bank.fsb"
        bank_file(path)
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(radio.RadioError):
            radio.read_bank(path)
        path.write_bytes(b"FSB5" + bytes(44))
        with self.assertRaises(radio.RadioError):
            radio.read_bank(path)

    def test_cancel_during_bank_write_preserves_source(self):
        source = self.root / "source.fsb"
        bank_file(source)
        before = source.read_bytes()
        with self.assertRaises(radio.RadioError):
            radio.write_bank(source, self.root / "out.fsb", [], lambda: True)
        self.assertEqual(source.read_bytes(), before)

    def test_mp3_frame_validation_rejects_truncation_wrong_rate_and_mono(self):
        p = self.root / "song.mp3"
        frame = b"\xff\xfb\xa4\x00" + bytes(476)
        p.write_bytes(frame * 2)
        self.assertEqual(radio.mp3_samples(p), 2304)
        for bad in (frame[:-1], b"ID3" + frame, frame[:2] + b"\xa0" + frame[3:],
                    frame[:3] + b"\xc0" + frame[4:]):
            p.write_bytes(bad)
            with self.assertRaises(radio.RadioError):
                radio.mp3_samples(p)

    def test_strings_preserve_ids_and_support_unicode_and_supplementary_characters(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from extract_map_points import StringTable
        data = string_file()
        text = "夜 Drive 🚙"
        result = radio.patch_strings(data, {"IDS_RadioStation_Option_1": text})
        self.assertEqual(StringTable(result).by_id("IDS_RadioStation_Option_1"), text)
        original_id_offset = struct.unpack_from(">I", data, 24)[0]
        new_id_offset = struct.unpack_from(">I", result, 24)[0]
        self.assertEqual(result[new_id_offset:], data[original_id_offset:])
        self.assertEqual(radio.patch_strings(data, {}), data)

    def test_custom_station_config_survives_restart(self):
        cfg = Config(self.root / "launcher.json")
        cfg.d["custom_radio"]["stations"]["Radio2"] = {
            "name": "Night Drive", "songs": ["song.wav"], "logo": "logo.png", "mode": "add"}
        cfg.save()
        loaded = Config(cfg.path)
        self.assertEqual(radio.settings(loaded), radio.settings(cfg))

    def test_invalid_station_slot_mode_and_control_characters_rejected(self):
        cfg = Config(self.root / "launcher.json")
        for slot, name, mode in (("Radio4", "Name", "add"), ("Radio1", "Name", "oops"),
                                 ("Radio1", "Bad\nName", "replace")):
            cfg.d["custom_radio"]["stations"] = {slot: {"name": name, "songs": [], "mode": mode}}
            with self.assertRaises(radio.RadioError):
                radio.settings(cfg)

    def test_restore_all_disables_only_generated_mod(self):
        cfg = Config(self.root / "launcher.json")
        cfg.d["mods"] = {"order": ["other", radio.MOD], "disabled": ["old"]}
        result = radio.build(cfg)
        self.assertIn("Original radio restored", result)
        self.assertEqual(cfg.d["mods"]["order"], ["other", radio.MOD])
        self.assertEqual(cfg.d["mods"]["disabled"], ["old", radio.MOD])

    def test_failed_encoder_does_not_publish_or_change_mod_order(self):
        cfg = Config(self.root / "launcher.json")
        cfg.d["custom_radio"] = {"ffmpeg": "missing-encoder", "stations": {
            "Radio1": {"name": "Test", "mode": "replace", "songs": ["missing.mp3"]}}}
        before = copy.deepcopy(cfg.d)
        with self.assertRaises(radio.RadioError):
            radio.build(cfg)
        self.assertEqual(cfg.d, before)
        self.assertFalse((self.root / "mods" / radio.MOD).exists())

    def test_logo_pixels_survive_tile_round_trip(self):
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        image = QImage(210, 100, QImage.Format_RGBA8888)
        image.fill(QColor(23, 67, 149, 211))
        p = self.root / "logo.png"
        image.save(str(p))
        xds = radio.logo_xds(p)
        from xdc_decode import tiled_offset
        self.assertEqual(struct.unpack_from(">I", xds, 32)[0] & 63, 6)
        self.assertEqual(struct.unpack_from(">I", xds, 36)[0], (99 << 13) | 209)
        pitch = ((struct.unpack_from(">I", xds, 28)[0] >> 22) & 0x1FF) * 32
        self.assertEqual(pitch, 224)
        for x, y in ((105, 10), (105, 53)):
            offset = 52 + tiled_offset(x, y, pitch)
            a, r, g, b = xds[offset:offset + 4]
            self.assertEqual((a, r, g, b), (211, 23, 67, 149))
        for x in (0, 105, 209):
            self.assertEqual(xds[52 + tiled_offset(x, 99, pitch)], 0)
        small = radio.logo_xds(p, 64, 32)
        self.assertEqual(((struct.unpack_from(">I", small, 28)[0] >> 22) & 0x1FF) * 32, 64)

    def test_native_xml_extensions_survive_output(self):
        data = b'<RadioSystem><RadioStation name="Radio1" 3dPanLevel="1"><MusicTrack artist="Chase &amp; Status & Guest"/></RadioStation></RadioSystem>'
        tree = radio.read_radio_xml(data)
        p = self.root / "radio.xml"
        radio.write_radio_xml(tree, p)
        self.assertIn(b'3dPanLevel="1"', p.read_bytes())
        self.assertNotIn(b'_radio_3dPanLevel', p.read_bytes())
        parsed = radio.read_radio_xml(p.read_bytes())
        self.assertEqual(parsed.getroot()[0][0].get("artist"), "Chase & Status & Guest")

    def test_actual_page_persists_playlist_edits_and_station_switch(self):
        from PySide6.QtWidgets import QApplication
        from forza_launcher.ui.radio_page import RadioPage
        app = QApplication.instance() or QApplication([])
        cfg = Config(self.root / "launcher.json")
        class Window:
            def running(self):
                return False
        page = RadioPage(cfg, Window())
        page.name.setText("Night Drive")
        page._add(["a.wav", "b.wav", "a.wav"])
        page.mode.setCurrentIndex(1)
        page.slot.setCurrentIndex(1)
        self.assertEqual(page.songs.count(), 0)
        page.slot.setCurrentIndex(0)
        self.assertEqual(page.name.text(), "Night Drive")
        self.assertEqual(page.songs.count(), 2)
        self.assertEqual(page.mode.currentData(), "add")
        saved = radio.settings(Config(cfg.path))["stations"]["Radio1"]
        self.assertEqual(saved["songs"], ["a.wav", "b.wav"])
        self.assertEqual(saved["mode"], "add")
        page._restore()
        self.assertNotIn("Radio1", radio.settings(Config(cfg.path))["stations"])
        page.close()
        # close() hides a top-level QWidget; destroy it while QApplication lives.
        import shiboken6
        shiboken6.delete(page)
        app.processEvents()

    def _build_fixture(self):
        game = self.root / "game"
        audio = game / "media/audio/radio"
        audio.mkdir(parents=True)
        bank_file(audio / "Radio_Music.fsb")
        (audio / "RadioSystem.xml").write_text('<RadioSystem><RadioStation name="Radio1"><Playlist><MusicTrack name="Stock"/></Playlist></RadioStation></RadioSystem>')
        (audio / "RadioSoundbankInfo_Music.xml").write_text('<SoundBanks><Bank Name="original"><SoundBankInfo SoundBankIndex="0"/></Bank></SoundBanks>')
        (game / "media/stringtables").mkdir()
        encoder = self.root / "encoder"
        encoder.touch()
        song = self.root / "song.wav"
        song.touch()
        cfg = Config(self.root / "launcher.json")
        cfg.d["game_root"] = str(game)
        cfg.d["advanced"]["data_root"] = str(self.root / "data")
        cfg.d["custom_radio"] = {"ffmpeg": str(encoder), "stations": {
            "Radio1": {"name": "Test", "mode": "replace", "songs": [str(song)]}}}
        cfg.save()
        def encoded(source, output, ffmpeg, cancelled):
            output.write_bytes(b"\xff\xfb\xa4\x00" + bytes(476))
            return 1152
        return cfg, encoded

    def test_backup_cleanup_failure_cannot_enable_old_radio(self):
        cfg, encoded = self._build_fixture()
        with patch.object(radio, "encode", side_effect=encoded):
            radio.build(cfg)
            real_remove = shutil.rmtree
            def remove(path, *args, **kwargs):
                if Path(path).name == "." + radio.MOD + ".previous":
                    raise PermissionError("simulated cleanup failure")
                return real_remove(path, *args, **kwargs)
            with patch.object(radio.shutil, "rmtree", side_effect=remove):
                result = radio.build(cfg)
        self.assertIn("Built 1 station", result)
        self.assertIn("old build could not be removed", result)
        self.assertEqual([m.folder for m in radio.mods.enabled(cfg)], [radio.MOD])
        cfg.d["custom_radio"]["stations"] = {}
        radio.build(cfg)
        self.assertEqual(radio.mods.enabled(cfg), [])

    def test_failed_config_publication_restores_old_generated_bank(self):
        cfg, encoded = self._build_fixture()
        with patch.object(radio, "encode", side_effect=encoded):
            radio.build(cfg)
            target = radio.mods.mods_dir(cfg) / radio.MOD
            old = (target / ".reimagined-radio.json").read_bytes()
            cfg.d["custom_radio"]["stations"]["Radio1"]["name"] = "Changed"
            with patch.object(cfg, "save", side_effect=PermissionError("config write denied")):
                with self.assertRaises(PermissionError):
                    radio.build(cfg)
        self.assertEqual((target / ".reimagined-radio.json").read_bytes(), old)
        self.assertFalse(target.with_name("." + radio.MOD + ".previous").exists())


if __name__ == "__main__":
    unittest.main()
