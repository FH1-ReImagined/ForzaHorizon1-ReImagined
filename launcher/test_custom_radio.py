import copy
import json
import os
import re
import subprocess
import time
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
        def encoded(source, output, ffmpeg, cancelled, level=True):
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

    # --- Leveling to the game's loudness (#662) ---

    def test_level_gain_targets_game_loudness_under_the_peak_ceiling(self):
        # A modern master is turned down to the stock radio loudness.
        self.assertEqual(radio.level_gain(-7.0, 0.8), radio.LEVEL_TARGET + 7.0)
        # A quiet, dynamic song is raised only while its true peak stays under the ceiling.
        self.assertEqual(radio.level_gain(-24.0, -14.0), radio.LEVEL_TARGET + 24.0)
        self.assertEqual(radio.level_gain(-20.9, -3.2), round(radio.LEVEL_CEILING + 3.2, 2))
        self.assertLessEqual(radio.level_gain(-20.9, -3.2), 0)
        # Silence is left alone.
        self.assertEqual(radio.level_gain(-70.0, float("-inf")), 0.0)
        self.assertEqual(radio.level_gain(float("-inf"), float("-inf")), 0.0)

    def _encode_commands(self, level, summary):
        song = self.root / "song.flac"
        song.touch()
        commands = []
        def run(cmd, source, cancelled, verb="encode"):
            commands.append(cmd)
            return summary if "-f" in cmd and cmd[cmd.index("-f") + 1] == "null" else b""
        with patch.object(radio, "_run", side_effect=run), patch.object(radio, "mp3_samples", return_value=1152):
            self.assertEqual(radio.encode(song, self.root / "out.mp3", "ffmpeg", level=level), 1152)
        return commands

    SUMMARY = (b"[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:          -7.0 LUFS\n"
               b"    Threshold: -17.0 LUFS\n\n  True peak:\n    Peak:        0.8 dBFS\n")

    def test_leveling_on_measures_then_applies_one_linear_gain(self):
        measure, encode = self._encode_commands(True, self.SUMMARY)
        self.assertIn("ebur128=peak=true:metadata=1", measure)
        self.assertEqual(encode[encode.index("-af") + 1], f"volume={radio.LEVEL_TARGET + 7.0:.2f}dB")
        self.assertEqual(encode[-1], str(self.root / "out.mp3"))
        # Both passes read the same audio: first stream, 30 minutes, stereo 48 kHz.
        for cmd in (measure, encode):
            self.assertEqual(cmd[cmd.index("-t") + 1], "1800")
            self.assertEqual(cmd[cmd.index("-map") + 1], "0:a:0")

    def test_leveling_off_keeps_the_original_level(self):
        (encode,) = self._encode_commands(False, self.SUMMARY)
        self.assertNotIn("-af", encode)
        self.assertIn("libmp3lame", encode)

    def test_unreadable_measurement_is_an_error(self):
        with self.assertRaises(radio.RadioError):
            self._encode_commands(True, b"no summary here")

    def test_cancel_stops_a_running_pass(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        start = time.monotonic()
        with self.assertRaisesRegex(radio.RadioError, "cancelled"):
            radio._run(cmd, Path("song.flac"), lambda: time.monotonic() - start > .2)
        self.assertLess(time.monotonic() - start, 10)

    def test_level_setting_defaults_on_survives_restart_and_rejects_non_bool(self):
        cfg = Config(self.root / "launcher.json")
        self.assertTrue(radio.settings(cfg)["level"])
        cfg.d["custom_radio"]["level"] = False
        cfg.save()
        self.assertFalse(radio.settings(Config(cfg.path))["level"])
        cfg.d["custom_radio"]["level"] = "yes"
        with self.assertRaises(radio.RadioError):
            radio.settings(cfg)

    def test_stations_built_before_leveling_need_one_rebuild(self):
        cfg, encoded = self._build_fixture()
        marker = radio.mods.mods_dir(cfg) / radio.MOD / radio.MARKER
        self.assertFalse(radio.stale(cfg))  # nothing built yet
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"format": 1, "settings": radio.settings(cfg)}))
        self.assertTrue(radio.stale(cfg))
        levels = []
        def record(source, output, ffmpeg, cancelled, level=True):
            levels.append(level)
            return encoded(source, output, ffmpeg, cancelled, level)
        with patch.object(radio, "encode", side_effect=record):
            radio.build(cfg)
            self.assertFalse(radio.stale(cfg))
            self.assertEqual(json.loads(marker.read_text())["leveling"], radio.LEVELING)
            cfg.d["custom_radio"]["level"] = False
            self.assertTrue(radio.stale(cfg))
            radio.build(cfg)
        self.assertFalse(radio.stale(cfg))
        self.assertIsNone(json.loads(marker.read_text())["leveling"])
        self.assertEqual(levels, [True, False])

    def test_page_checkbox_saves_leveling(self):
        from PySide6.QtWidgets import QApplication
        from forza_launcher.ui.radio_page import RadioPage
        app = QApplication.instance() or QApplication([])
        cfg = Config(self.root / "launcher.json")
        class Window:
            def running(self):
                return False
        page = RadioPage(cfg, Window())
        self.assertTrue(page.level.isChecked())
        page.level.setChecked(False)
        self.assertFalse(radio.settings(Config(cfg.path))["level"])
        page.close()
        import shiboken6
        shiboken6.delete(page)
        app.processEvents()

    # --- Song artist and title (#678) ---

    def test_configs_without_song_names_still_load(self):
        cfg = Config(self.root / "launcher.json")
        cfg.d["custom_radio"]["stations"]["Radio1"] = {"name": "Old", "songs": ["a.wav"], "mode": "add"}
        station = radio.settings(cfg)["stations"]["Radio1"]
        self.assertEqual(station["tags"], {})
        self.assertEqual(radio.song_names(station, "dir/a.wav"), ("a", "Old"))

    def test_song_names_survive_restart_and_drop_removed_songs(self):
        cfg = Config(self.root / "launcher.json")
        cfg.d["custom_radio"]["stations"]["Radio2"] = {
            "name": "Night Drive", "songs": ["a.wav", "b.wav"], "mode": "replace",
            "tags": {"a.wav": {"artist": " Chase & Status ", "title": "Blind Faith"},
                     "b.wav": {"title": "Only Title", "artist": ""},
                     "gone.wav": {"artist": "Removed"}}}
        cfg.d["custom_radio"] = radio.settings(cfg)
        cfg.save()
        station = radio.settings(Config(cfg.path))["stations"]["Radio2"]
        self.assertEqual(station["tags"], {"a.wav": {"artist": "Chase & Status", "title": "Blind Faith"},
                                           "b.wav": {"title": "Only Title"}})
        self.assertEqual(radio.song_names(station, "b.wav"), ("Only Title", "Night Drive"))

    def test_invalid_song_names_rejected(self):
        cfg = Config(self.root / "launcher.json")
        for tags in ([], {"a.wav": "x"}, {"a.wav": {"artist": 3}},
                     {"a.wav": {"title": "Line\nbreak"}}, {"a.wav": {"artist": "x" * (radio.MAX_TAG + 1)}}):
            cfg.d["custom_radio"]["stations"] = {"Radio1": {"name": "N", "songs": ["a.wav"], "tags": tags}}
            with self.assertRaises(radio.RadioError):
                radio.settings(cfg)

    def test_build_writes_edited_artist_and_title_for_the_hud(self):
        cfg, encoded = self._build_fixture()
        other = self.root / "other.wav"
        other.touch()
        station = cfg.d["custom_radio"]["stations"]["Radio1"]
        song = station["songs"][0]
        station["songs"].append(str(other))
        station["tags"] = {song: {"artist": "Rusko & Friends", "title": "Everyday (VIP)"}}
        with patch.object(radio, "encode", side_effect=encoded):
            radio.build(cfg)
        built = radio.mods.mods_dir(cfg) / radio.MOD / "media/audio/radio/RadioSystem.xml"
        tracks = radio.read_radio_xml(built.read_bytes()).getroot().findall(".//MusicTrack")
        self.assertEqual([(t.get("name"), t.get("artist")) for t in tracks],
                         [("Everyday (VIP)", "Rusko & Friends"), ("other", "Test")])
        self.assertIn(b'artist="Rusko &amp; Friends"', built.read_bytes())

    def test_read_tags_from_ffprobe_json(self):
        probe = {"format": {"tags": {"ARTIST": "Format  Artist"}},
                 "streams": [{"tags": {"title": "Stream Title", "artist": "Ignored"}}]}
        result = subprocess.CompletedProcess([], 0, json.dumps(probe).encode(), b"")
        with patch.object(radio.subprocess, "run", return_value=result) as run:
            self.assertEqual(radio.read_tags(self.root / "a.ogg", "ffprobe"),
                             {"artist": "Format Artist", "title": "Stream Title"})
        self.assertEqual(run.call_args[0][0][0], "ffprobe")
        with patch.object(radio.subprocess, "run", side_effect=OSError("missing")):
            self.assertEqual(radio.read_tags(self.root / "a.ogg", "ffprobe"), {})
        self.assertEqual(radio.read_tags(self.root / "a.ogg", None), {})

    def test_ffprobe_is_found_beside_the_chosen_ffmpeg(self):
        ffmpeg, ffprobe = self.root / "ffmpeg.exe", self.root / "ffprobe.exe"
        ffmpeg.touch()
        ffprobe.touch()
        self.assertEqual(radio.ffprobe_path(str(ffmpeg)), str(ffprobe))

    def test_page_edits_song_names_prefilled_from_tags(self):
        from PySide6.QtWidgets import QApplication, QDialog, QLineEdit
        from forza_launcher.ui import radio_page
        app = QApplication.instance() or QApplication([])
        cfg = Config(self.root / "launcher.json")
        class Window:
            def running(self):
                return False
        page = radio_page.RadioPage(cfg, Window())
        page.name.setText("Night Drive")
        page._add(["/music/a.flac", "/music/b.flac"])
        seen = []
        def answer():
            artist, title = page._edit_dialog.findChildren(QLineEdit)
            seen.append((artist.text(), title.text()))
            title.setText("  Edited   Title ")
            return QDialog.Accepted
        with patch.object(radio, "read_tags", return_value={"artist": "Tag Artist", "title": "Tag Title"}), \
                patch.object(radio_page.QDialog, "exec", side_effect=answer):
            page._edit(page.songs.item(0))
        self.assertEqual(seen, [("Tag Artist", "Tag Title")])
        saved = radio.settings(Config(cfg.path))["stations"]["Radio1"]
        self.assertEqual(saved["songs"], ["/music/a.flac", "/music/b.flac"])
        self.assertEqual(saved["tags"], {"/music/a.flac": {"artist": "Tag Artist", "title": "Edited Title"}})
        self.assertIn("Tag Artist – Edited Title", page.songs.item(0).text())
        self.assertEqual(page.songs.item(1).text(), "/music/b.flac")
        # Stored names win over the file's tags; empty fields go back to the defaults.
        with patch.object(radio, "read_tags", side_effect=AssertionError("not probed")), \
                patch.object(radio_page.QDialog, "exec", side_effect=lambda: (
                    seen.append(tuple(f.text() for f in page._edit_dialog.findChildren(QLineEdit))),
                    [f.clear() for f in page._edit_dialog.findChildren(QLineEdit)],
                    QDialog.Accepted)[-1]):
            page._edit(page.songs.item(0))
        self.assertEqual(seen[-1], ("Tag Artist", "Edited Title"))
        self.assertEqual(radio.settings(Config(cfg.path))["stations"]["Radio1"]["tags"], {})
        page._set_tags("/music/b.flac", "Someone", "")
        page.songs.item(1).setSelected(True)
        page._remove()
        self.assertEqual(radio.settings(Config(cfg.path))["stations"]["Radio1"]["tags"], {})
        page.close()
        import shiboken6
        shiboken6.delete(page)
        app.processEvents()

    # --- Exporting the stock logos (#678) ---

    def test_custom_logo_texture_decodes_back_to_its_pixels(self):
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        image = QImage(210, 72, QImage.Format_RGBA8888)
        image.fill(QColor(23, 67, 149, 211))
        p = self.root / "logo.png"
        image.save(str(p))
        width, height, rgba, fmt, alpha = radio.decode_xds(radio.logo_xds(p))
        self.assertEqual((width, height, alpha), (210, 100, "8-bit"))
        self.assertIn("uncompressed", fmt)
        self.assertEqual(rgba[(10 * 210 + 105) * 4:(10 * 210 + 106) * 4], bytes((23, 67, 149, 211)))
        self.assertEqual(rgba[(99 * 210 + 105) * 4 + 3], 0)
        self.assertEqual(radio._art_bounds(width, height, rgba), {"x": 0, "y": 0, "width": 210, "height": 72})

    def test_dxt1_and_dxt5_logos_decode_from_tiled_blocks(self):
        # DXT5: red at 200 alpha in block (0, 0), opaque white in block (9, 1).
        red = bytes((200, 0)) + bytes(6) + struct.pack("<HHI", 0xF800, 0, 0)
        white = bytes((255, 255)) + bytes(6) + struct.pack("<HHI", 0xFFFF, 0, 0)
        width, height, rgba, fmt, alpha = radio.decode_xds(
            dxt_texture(53, 16, 40, 8, {(0, 0): red, (9, 1): white}))
        self.assertEqual((width, height, fmt, alpha), (40, 8, "DXT5 (BC3)", "8-bit"))
        self.assertEqual(rgba[:4], bytes((255, 0, 0, 200)))
        at = (5 * 40 + 37) * 4
        self.assertEqual(rgba[at:at + 4], bytes((255, 255, 255, 255)))
        self.assertEqual(rgba[(5 * 40) * 4 + 3], 0)  # An empty block is transparent.
        # DXT1 three-colour mode (c0 < c1): index 0 is blue, index 3 transparent.
        blue = struct.pack("<HHI", 0x001F, 0x07E0, 0b1100)
        width, height, rgba, fmt, alpha = radio.decode_xds(dxt_texture(51, 8, 64, 32, {(3, 2): blue}))
        self.assertEqual((width, height, fmt, alpha), (64, 32, "DXT1 (BC1)", "1-bit"))
        at = (8 * 64 + 12) * 4
        self.assertEqual(rgba[at:at + 8], bytes((0, 0, 255, 255, 0, 0, 0, 0)))
        with self.assertRaises(radio.RadioError):
            radio.decode_xds(dxt_texture(53, 16, 40, 8, {})[:52 + 64])

    def test_export_writes_png_and_notes_to_the_chosen_folder_only(self):
        import zipfile
        game = self.root / "game"
        textures = game / "media/ui/textures"
        textures.mkdir(parents=True)
        red = bytes((128, 128)) + bytes(6) + struct.pack("<HHI", 0xF800, 0, 0)
        with zipfile.ZipFile(textures / "Horizon.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Hud/Radio/RS_Pulse.xds", dxt_texture(53, 16, 210, 100, {(5, 6): red}))
            zf.writestr("Hud/Radio/Radio2.xds", dxt_texture(51, 8, 64, 32, {}))
        before = (textures / "Horizon.zip").read_bytes()
        cfg = Config(self.root / "launcher.json")
        cfg.d["game_root"] = str(game)
        cfg.d["advanced"]["data_root"] = str(self.root / "data")
        out = self.root / "export"
        self.assertEqual(radio.export_stock_logos(cfg, out), 2)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["RS_Pulse.png", "Radio2.png", "stock-logos.json"])
        notes = {n["file"]: n for n in json.loads((out / "stock-logos.json").read_text())["logos"]}
        pulse = notes["RS_Pulse.png"]
        self.assertEqual((pulse["width"], pulse["height"], pulse["format"], pulse["alpha"], pulse["station"]),
                         (210, 100, "DXT5 (BC3)", "8-bit", "Horizon Pulse"))
        self.assertEqual(pulse["visible_area"], {"x": 20, "y": 24, "width": 4, "height": 4})
        self.assertIn("210x72", pulse["custom_logo"])
        self.assertEqual((notes["Radio2.png"]["alpha"], notes["Radio2.png"]["kind"]), ("opaque", "station icon"))
        from PySide6.QtGui import QImage
        png = QImage(str(out / "RS_Pulse.png"))
        self.assertEqual((png.width(), png.height()), (210, 100))
        self.assertEqual(png.pixelColor(21, 25).getRgb(), (255, 0, 0, 128))
        self.assertEqual((textures / "Horizon.zip").read_bytes(), before)
        self.assertFalse((self.root / "data").exists() and any((self.root / "data").rglob("*.png")))
        (textures / "Horizon.zip").unlink()
        with self.assertRaises(radio.RadioError):
            radio.export_stock_logos(cfg, self.root / "empty")


def dxt_texture(fmt, block, width, height, blocks):
    """A synthetic tiled, 8-in-16 swapped DXT texture: {(block x, block y): block bytes}."""
    pitch = (width + 127) & ~127
    rows = (height + 127) & ~127
    log2 = 3 if block == 8 else 4
    pixels = bytearray(pitch // 4 * rows // 4 * block)
    for (bx, by), raw in blocks.items():
        at = radio._tiled(bx, by, pitch // 4, log2)
        pixels[at:at + block] = raw
    pixels[0::2], pixels[1::2] = pixels[1::2], pixels[0::2]
    fetch0 = 0x800000FE | ((pitch // 32) << 22)
    header = struct.pack(">13I", 3, 1, 0, 0, 0, 0xFFFF0000, 0xFFFF0000, fetch0, 0x40 | fmt,
                         ((height - 1) << 13) | (width - 1), 0xD10, 0, 0xA00)
    return header + bytes(pixels)


def _ffmpeg_with_lame():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return ffmpeg if "libmp3lame" in out else None


@unittest.skipUnless(_ffmpeg_with_lame(), "FFmpeg with libmp3lame is not installed")
class RadioLevelingFFmpegTests(unittest.TestCase):
    """Real encodes of generated test audio; no music or game audio is used."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ffmpeg = _ffmpeg_with_lame()

    def tearDown(self):
        self.temp.cleanup()

    def _make(self, name, graph):
        path = self.root / name
        subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-filter_complex", graph,
                        "-c:a", "pcm_s16le", str(path)], check=True, timeout=120)
        return path

    def _loud(self):
        # Limited pink noise like a modern master: about -7 LUFS, sample peaks at 0 dBFS.
        return self._make("loud.wav", "anoisesrc=color=pink:sample_rate=48000:duration=20:seed=7:amplitude=0.5,"
                          "aformat=channel_layouts=stereo,volume=14dB,alimiter=limit=1:level=false:attack=1:release=20")

    def test_loud_master_is_turned_down_to_the_game_loudness(self):
        song = self._loud()
        loudness, peak = radio.measure(song, self.ffmpeg)
        self.assertGreater(loudness, -8.5)
        self.assertGreater(peak, -0.5)
        out = self.root / "on.mp3"
        radio.encode(song, out, self.ffmpeg, level=True)
        loudness, peak = radio.measure(out, self.ffmpeg)
        self.assertAlmostEqual(loudness, radio.LEVEL_TARGET, delta=1.5)
        self.assertLessEqual(peak, radio.LEVEL_CEILING + .5)

    def test_leveling_off_keeps_the_loud_master(self):
        out = self.root / "off.mp3"
        radio.encode(self._loud(), out, self.ffmpeg, level=False)
        loudness, peak = radio.measure(out, self.ffmpeg)
        self.assertGreater(loudness, -9)
        self.assertGreater(peak, -1)

    def test_quiet_song_with_loud_peaks_is_not_boosted_into_clipping(self):
        # About -21 LUFS with -3 dBTP bursts: reaching -14.5 LUFS would need +6 dB.
        song = self._make("quiet.wav",
                          "anoisesrc=color=pink:sample_rate=48000:duration=20:seed=3:amplitude=0.2,"
                          "aformat=channel_layouts=stereo[n];"
                          "sine=f=220:sample_rate=48000:duration=20,volume=6.5,aformat=channel_layouts=stereo,"
                          "volume='if(lt(mod(t,4),0.1),1,0)':eval=frame[s];[n][s]amix=inputs=2:normalize=0")
        before, _ = radio.measure(song, self.ffmpeg)
        out = self.root / "quiet.mp3"
        radio.encode(song, out, self.ffmpeg, level=True)
        loudness, peak = radio.measure(out, self.ffmpeg)
        self.assertLessEqual(peak, radio.LEVEL_CEILING + .5)
        self.assertLess(loudness, radio.LEVEL_TARGET - 3)
        self.assertLessEqual(loudness, before + .5)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe are not installed")
class RadioTagsFFprobeTests(unittest.TestCase):
    """Tags of generated test audio; no music is used."""

    def test_artist_and_title_tags_are_read(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("tone.flac", "tone.ogg", "tone.wav"):
                song = Path(temp) / name
                subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi",
                                "-i", "sine=f=440:duration=1", "-metadata", "artist=Test Artist",
                                "-metadata", "title=Test Tone", str(song)], check=True, timeout=60)
                self.assertEqual(radio.read_tags(song, shutil.which("ffprobe")),
                                 {"artist": "Test Artist", "title": "Test Tone"}, name)


if __name__ == "__main__":
    unittest.main()
