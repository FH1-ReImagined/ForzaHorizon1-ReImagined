"""FH1-only donor preparation contracts, using entirely authored fixtures."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
import zlib

import native_menu_scene as menu


SOURCE = "ui/scenes/ui4/test_donor"
ALIAS = "ui/scenes/ui4/native_menu_test"


def declaration(source=SOURCE, alias=ALIAS):
    return {"schema": 1, "scenes": [{"source": source, "alias": alias}]}


def xex(title=menu.TITLE_ID, version=menu.TU4_VERSION):
    """A synthetic XEX container header; it contains no instructions."""
    data = bytearray(128)
    struct.pack_into(">4sIIIII", data, 0, b"XEX2", 0, len(data), 0, 0, 1)
    struct.pack_into(">II", data, 24, 0x40006, 40)
    struct.pack_into(">IIII", data, 40, 0, version, 0, title)
    return bytes(data)


def synthetic_scene(suffix):
    # Distinct authored geometry values exercise exact preservation, including
    # transform values, negative zero, text and opaque trailing data. These are
    # test bytes, not a claim to implement the game's complete scene format.
    return (suffix.encode() + b"\0original synthetic scene\0" +
            struct.pack(">8f", -12.5, 77.25, -0.0, 1.5, 0.75, 90.0, 400.0, 240.0) +
            b"font reference, mesh data and animation are opaque here")


def pause_flow():
    """Minimal authored native-flow contract, including an unrelated map exit."""
    root = ET.Element("StateGroup", id="pausesystem")
    parent = ET.SubElement(root, "State", id="show_pause_screen")
    pause = ET.SubElement(parent, "Node", id="pause_screen")
    ET.SubElement(pause, "Exit", id="exit", target="show_hud_screen.enter")
    ET.SubElement(pause, "Exit", id="map", target="map.enter")
    ET.SubElement(parent, "Node", id="show_hud_screen")
    ET.SubElement(parent, "Node", id="map")
    chains = {
        "gift_car_prepare": ["place_car_to_last_valid_position", "store_car_position",
                             "disable_streaming", "unpause_game", "remove_all_cars",
                             "disable_presentationrenderandshadows", "push_dynamic_render_ui_nocar"],
        "gift_car_unprepare": ["pop_dynamic_render_ui_nocar", "load_player_choices_car",
                               "restore_car_position", "enable_presentationrender",
                               "reset_traffic_cars", "enable_streaming", "pause_game"],
    }
    for identifier, names in chains.items():
        state = ET.SubElement(root, "State", id=identifier)
        ET.SubElement(state, "Entry", id="enter", target=names[0] + ".enter")
        ET.SubElement(state, "Exit", id="exit")
        for index, name in enumerate(names):
            native = "unpause_game_retain_audio_snapshot" if name == "unpause_game" else name
            node = ET.SubElement(state, "Node", id=name, state="global." + native)
            ET.SubElement(node, "Entry", id="enter")
            target = names[index + 1] + ".enter" if index + 1 < len(names) else ".exit"
            ET.SubElement(node, "Exit", id="exit", target=target)
    return ET.tostring(root)


def xmem_uncompressed(data):
    """One authored LZX uncompressed block in XMem framing, at most 32 KiB."""
    assert 0 < len(data) <= 32768
    # Intel flag 0, block type 3, 24-bit length; MSB-first bits are stored in
    # little-endian 16-bit words. The remaining four bits are alignment padding.
    header = (3 << 28) | (len(data) << 4)
    stream = struct.pack("<HHIII", header >> 16, header & 0xFFFF, 1, 1, 1) + data
    if len(data) % 2:
        stream += b"\0"
    return b"\xff" + struct.pack(">HH", len(data), len(stream)) + stream + bytes(5)


def patch_codec(path, name, codec, size, crc):
    """Produce an opaque method-21 fixture without using the alias writer."""
    with zipfile.ZipFile(path) as archive:
        local = archive.getinfo(name).header_offset
    raw = bytearray(path.read_bytes())
    struct.pack_into("<H", raw, local + 8, codec)
    struct.pack_into("<I", raw, local + 14, crc)
    struct.pack_into("<I", raw, local + 22, size)
    cursor = 0
    while True:
        cursor = raw.find(b"PK\x01\x02", cursor)
        if cursor < 0:
            raise AssertionError("Missing synthetic central directory record")
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, cursor + 28)
        if raw[cursor + 46:cursor + 46 + name_length].decode() == name:
            struct.pack_into("<H", raw, cursor + 10, codec)
            struct.pack_into("<I", raw, cursor + 16, crc)
            struct.pack_into("<I", raw, cursor + 24, size)
            break
        cursor += 46 + name_length + extra_length + comment_length
    path.write_bytes(raw)


class ManifestTests(unittest.TestCase):
    def test_original_default_contains_only_fh1_donor_stems(self):
        scenes = menu.parse_manifest(menu.DEFAULT_MANIFEST.read_bytes())
        expected = [
            ("ui/scenes/ui4/108_c_buy_carselect", "ui/scenes/ui4/native_menu_header"),
            ("ui/scenes/ui4/h_map", "ui/scenes/ui4/native_menu_map"),
            ("ui/scenes/ui4/925_pause_menu", "ui/scenes/ui4/native_menu_pause"),
        ]
        expected += [("ui/scenes/ui4/108_c_buy_carselect", f"ui/scenes/ui4/native_menu_tab_{i}")
                     for i in range(4)]
        expected += [("ui/scenes/ui4/108_c_buy_carselect", f"ui/scenes/ui4/native_menu_tile_{i}")
                     for i in range(6)]
        expected += [("ui/scenes/ui4/108_c_buy_carselect", f"ui/scenes/ui4/native_menu_tuning_{i}")
                     for i in sorted(range(22), key=str)]
        self.assertEqual([(scene.source, scene.alias) for scene in scenes], expected)

    def test_canonical_round_trip_ignores_declaration_order(self):
        data = declaration()
        data["scenes"].append({"source": SOURCE, "alias": ALIAS + "_second"})
        original = menu.parse_manifest(json.dumps(data))
        encoded = menu.manifest_bytes(original)
        data["scenes"].reverse()
        reversed_scenes = menu.parse_manifest(json.dumps(data))
        self.assertEqual(menu.manifest_bytes(reversed_scenes), encoded)
        self.assertEqual(menu.parse_manifest(encoded), original)
        self.assertEqual(menu.manifest_bytes(tuple(reversed(original))), encoded)

    def test_traversal_extensions_unicode_and_alias_escape_rejected(self):
        invalid = ("../scene", "/ui/scenes/ui4/scene", "C:/scene", "ui/scenes/ui4/../scene",
                   "ui/scenes/ui4/test.bgf", "ui\\scenes\\ui4\\test", "ui/scenes/ui4/NAME",
                   "ui/scenes/ui4/test\0", "ui/scenes/ui4/test\n", "ui/scenes/ui4/caf\u00e9")
        for value in invalid:
            for field in ("source", "alias"):
                with self.subTest(value=value, field=field):
                    data = declaration()
                    data["scenes"][0][field] = value
                    with self.assertRaises(ValueError):
                        menu.parse_manifest(json.dumps(data))
        with self.assertRaises(ValueError):
            menu.parse_manifest(json.dumps(declaration(alias=SOURCE)))

    def test_schema_types_unknown_fields_and_duplicate_keys_rejected(self):
        invalid = [[], None, {}, {"schema": True, "scenes": []},
                   {"schema": 2, "scenes": []}, {"schema": 1, "scenes": {}},
                   {"schema": 1, "scenes": []}, {**declaration(), "extra": 1},
                   {"schema": 1, "scenes": [{"source": SOURCE, "alias": ALIAS, "x": 1}]},
                   {"schema": 1, "scenes": [True]},
                   {"schema": 1, "scenes": [{"source": 1, "alias": ALIAS}]},
                   {"schema": 1, "scenes": [{"source": SOURCE, "alias": None}]}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                menu.parse_manifest(json.dumps(data))
        for raw in ('{"schema":1,"schema":1,"scenes":[]}', b"\xff", "{", " " * 65537,
                    "[" * 2000 + "]" * 2000):
            with self.subTest(raw=repr(raw)[:70]), self.assertRaises(ValueError):
                menu.parse_manifest(raw)

    def test_duplicate_self_and_chained_aliases_rejected(self):
        data = declaration()
        for scenes in (
                data["scenes"] * 2,
                [{"source": ALIAS, "alias": ALIAS}],
                data["scenes"] + [{"source": ALIAS, "alias": ALIAS + "_child"}],
                data["scenes"] * (menu.MAX_SCENES + 1)):
            with self.subTest(scenes=scenes[:2]), self.assertRaises(ValueError):
                menu.parse_manifest(json.dumps({"schema": 1, "scenes": scenes}))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-menu-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.update = self.root / "source"
        self.update.mkdir()
        self.output = self.root / "output"
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps(declaration()), encoding="utf-8")
        (self.update / "default.xex").write_bytes(xex())
        (self.update / "SpeechFacade_default.xex").write_bytes(b"synthetic facade")
        self.archive = self.update / "media.zip"
        self.make_archive()

    def make_archive(self, codec=0, missing=None):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.comment = b"untouched archive comment"
            archive.writestr("unrelated.txt", b"keep me", compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr(menu.offline_cars_flow.ENTRY, pause_flow(), compress_type=zipfile.ZIP_DEFLATED)
            for suffix in menu.SUFFIXES:
                if suffix == missing:
                    continue
                info = zipfile.ZipInfo(f"{SOURCE}.{suffix}", (2020, 1, 2, 3, 4, 6))
                info.external_attr = 0x12345678
                archive.writestr(info, synthetic_scene(suffix), compress_type=codec if codec != 21 else 0)
        if codec == 21:
            for suffix in menu.SUFFIXES:
                if suffix != missing:
                    patch_codec(self.archive, f"{SOURCE}.{suffix}", 21, 4096, 0xABCDEF01)

    def stage(self, output=None):
        # Injection is confined to synthetic tests; the CLI always verifies the
        # recognized original FH1 TU4 archive digest.
        with patch.object(menu, "ORIGINAL_ARCHIVE_SHA256", menu.digest(self.archive)):
            return menu.stage(self.update, output or self.output, self.manifest)

    def source_snapshot(self):
        return {path.name: path.read_bytes() for path in self.update.iterdir()}


class ArchiveContracts(Fixture):
    def test_attributed_action_art_is_staged_with_native_offsets(self):
        report = self.stage()
        with zipfile.ZipFile(self.output / "media.zip") as archive:
            for name, record in zip(menu.ACTION_ICONS, report["action_icons"]):
                info = archive.getinfo(record["entry"])
                expected = (menu.DEFAULT_MANIFEST.parent / "icons" / (name + ".xds")).read_bytes()
                self.assertEqual(archive.read(info), expected)
                _, _, offset = struct.unpack("<HHI", info.extra)
                raw = (self.output / "media.zip").read_bytes()
                self.assertEqual(raw[offset:offset + info.file_size], expected)
        attribution = (self.output / "native-menu-icons-license.txt").read_text(encoding="utf-8")
        self.assertIn("Font Awesome Free", attribution)
        self.assertIn("CC BY 4.0", attribution)

    def test_stored_and_deflated_geometry_fonts_and_unrelated_payloads_preserved(self):
        for codec in (0, 8):
            with self.subTest(codec=codec):
                self.make_archive(codec)
                before = self.source_snapshot()
                output = self.root / f"output-{codec}"
                report = self.stage(output)
                self.assertEqual(self.source_snapshot(), before)
                self.assertEqual(len(report["entries"]), 3)
                with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(output / "media.zip") as result:
                    self.assertEqual(result.comment, source.comment)
                    for info in source.infolist():
                        if info.filename == menu.offline_cars_flow.ENTRY:
                            continue
                        copied = result.getinfo(info.filename)
                        self.assertEqual(menu.compressed_payload(result, copied), menu.compressed_payload(source, info))
                        self.assertEqual(result.read(copied), source.read(info))
                    for suffix in menu.SUFFIXES:
                        donor, alias = source.getinfo(f"{SOURCE}.{suffix}"), result.getinfo(f"{ALIAS}.{suffix}")
                        self.assertEqual(result.read(alias), synthetic_scene(suffix))
                        self.assertEqual(menu.compressed_payload(result, alias), menu.compressed_payload(source, donor))
                        self.assertEqual((alias.compress_type, alias.CRC, alias.file_size, alias.date_time, alias.external_attr),
                                         (donor.compress_type, donor.CRC, donor.file_size, donor.date_time, donor.external_attr))
                        tag, size, offset = struct.unpack("<HHI", alias.extra)
                        self.assertEqual((tag, size), (0x1123, 4))
                        raw = (output / "media.zip").read_bytes()
                        self.assertEqual(raw[offset:offset + alias.compress_size], menu.compressed_payload(result, alias))
                for name in ("default.xex", "SpeechFacade_default.xex"):
                    self.assertEqual((output / name).read_bytes(), before[name])

    def test_method_21_metadata_and_opaque_bytes_are_preserved_without_decoder(self):
        self.make_archive(21)
        before = self.source_snapshot()
        report = self.stage()
        with zipfile.ZipFile(self.output / "media.zip") as archive:
            for suffix in menu.SUFFIXES:
                alias = archive.getinfo(f"{ALIAS}.{suffix}")
                self.assertEqual((alias.compress_type, alias.file_size, alias.CRC), (21, 4096, 0xABCDEF01))
                self.assertEqual(menu.compressed_payload(archive, alias), synthetic_scene(suffix))
        self.assertEqual(self.source_snapshot(), before)
        self.assertTrue(all(record["compression"] == 21 for record in report["entries"]))

    def test_repeated_runs_have_byte_identical_output_and_provenance(self):
        # Several independently named runtime instances may reuse one donor.
        # Exercise the complete default alias set with the authored fixture.
        aliases = menu.parse_manifest(menu.DEFAULT_MANIFEST.read_bytes())
        self.manifest.write_text(json.dumps({"schema": 1, "scenes": [
            {"source": SOURCE, "alias": scene.alias} for scene in aliases]}), encoding="utf-8")
        one = self.stage()
        two_output = self.root / "output-two"
        two = self.stage(two_output)
        self.assertEqual(one, two)
        self.assertEqual(len(one["entries"]), 105)
        with zipfile.ZipFile(self.output / "media.zip") as archive:
            for scene in aliases:
                for suffix in menu.SUFFIXES:
                    self.assertEqual(archive.read(scene.alias + "." + suffix), synthetic_scene(suffix))
        self.assertEqual({path.name: path.read_bytes() for path in self.output.iterdir()},
                         {path.name: path.read_bytes() for path in two_output.iterdir()})
        self.assertNotIn(str(self.root), (self.output / "native-menu-provenance.json").read_text())

    def test_existing_output_and_source_overlap_never_write(self):
        before = self.source_snapshot()
        for output in (self.update, self.update / "child", self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.stage(output)
        self.output.mkdir()
        (self.output / "keep").write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual((self.output / "keep").read_bytes(), b"preserve")
        self.assertEqual(self.source_snapshot(), before)

    def test_modified_or_unknown_archive_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, "original extracted FH1 title-update 4"):
            menu.stage(self.update, self.output, self.manifest)
        self.assertFalse(self.output.exists())
        expected = menu.digest(self.archive)
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.writestr("modified", b"unexpected")
        before = self.source_snapshot()
        with patch.object(menu, "ORIGINAL_ARCHIVE_SHA256", expected):
            with self.assertRaisesRegex(ValueError, "Unsupported or modified"):
                menu.stage(self.update, self.output, self.manifest)
        self.assertEqual(self.source_snapshot(), before)
        self.assertFalse(self.output.exists())

    def test_missing_triplet_rejected_before_output(self):
        self.make_archive(missing="fbf")
        with self.assertRaisesRegex(ValueError, "triplet is incomplete"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_case_ambiguity_or_existing_alias_rejected(self):
        for name, error in ((SOURCE.upper() + ".BGF", "Ambiguous"), (ALIAS + ".bsg", "already exists")):
            with self.subTest(name=name):
                self.make_archive()
                with zipfile.ZipFile(self.archive, "a") as archive:
                    archive.writestr(name, b"duplicate scene")
                with self.assertRaisesRegex(ValueError, error):
                    self.stage()
                self.assertFalse(self.output.exists())

    def test_unexpected_zip_flags_codecs_and_payload_bounds_rejected(self):
        with zipfile.ZipFile(self.archive) as archive:
            original = archive.getinfo(SOURCE + ".bgf")
            for field, value in (("flag_bits", 1), ("flag_bits", 0x40), ("compress_type", 12),
                                 ("compress_size", 0), ("compress_size", menu.MAX_PAYLOAD_BYTES + 1),
                                 ("file_size", menu.MAX_PAYLOAD_BYTES + 1), ("header_offset", 0xFFFFFFF)):
                info = copy.copy(original)
                setattr(info, field, value)
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    menu.compressed_payload(archive, info)

    def test_local_central_mismatch_and_native_offset_corruption_rejected(self):
        for mode in ("codec", "crc", "offset", "duplicate-offset"):
            with self.subTest(mode=mode):
                self.make_archive()
                if mode in ("codec", "crc"):
                    with zipfile.ZipFile(self.archive) as archive:
                        offset = archive.getinfo(SOURCE + ".bgf").header_offset
                    raw = bytearray(self.archive.read_bytes())
                    struct.pack_into("<H" if mode == "codec" else "<I", raw,
                                     offset + (8 if mode == "codec" else 14), 8 if mode == "codec" else 0)
                    self.archive.write_bytes(raw)
                else:
                    with zipfile.ZipFile(self.archive, "w") as archive:
                        for suffix in menu.SUFFIXES:
                            info = zipfile.ZipInfo(SOURCE + "." + suffix)
                            info.extra = struct.pack("<HHI", 0x1123, 4, 123)
                            if mode == "duplicate-offset":
                                info.extra *= 2
                            archive.writestr(info, synthetic_scene(suffix))
                with self.assertRaises(ValueError):
                    self.stage()
                self.assertFalse(self.output.exists())

    def test_archive_changes_while_copying_do_not_publish_output(self):
        original_copy = menu.shutil.copyfile

        def changed_copy(source, destination):
            result = original_copy(source, destination)
            if Path(source) == self.archive:
                with Path(destination).open("ab") as stream:
                    stream.write(b"changed during copy")
            return result

        with patch.object(menu.shutil, "copyfile", side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError, "changed during preparation"):
                self.stage()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".native-menu-*")), [])


class FlowContracts(Fixture):
    def test_reload_patch_replaces_one_entry_and_preserves_native_chains(self):
        report = self.stage()
        with zipfile.ZipFile(self.output / "media.zip") as result:
            entry = menu.offline_cars_flow.ENTRY
            self.assertEqual(result.namelist().count(entry), 1)
            info = result.getinfo(entry)
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            patched = result.read(entry)
            self.assertEqual(patched, menu.offline_cars_flow.patch_pause_xml(pause_flow()))
            self.assertEqual(zlib.crc32(patched), info.CRC)
            self.assertEqual(struct.unpack("<HHI", info.extra),
                             (0x1123, 4, report["offline_cars_flow"]["payload_offset"]))
        original, modified = ET.fromstring(pause_flow()), ET.fromstring(patched)
        for name in ("gift_car_prepare", "gift_car_unprepare"):
            selector = f"State[@id='{name}']"
            self.assertEqual(ET.tostring(original.find(selector)), ET.tostring(modified.find(selector)))
        parent = modified.find("State[@id='show_pause_screen']")
        self.assertEqual(parent.find("Node[@id='pause_screen']/Exit[@id='map']").get("target"), "map.enter")
        self.assertEqual(parent.find("Node[@id='pause_screen']/Exit[@id='exit']").get("target"),
                         "offline_cars_decision.enter")
        reload = modified.find("State[@id='offline_cars_restore']/Node[@id='load_player_choices_car']")
        self.assertEqual(reload.get("state"), "global.reload_player_car_and_livery")

    def test_real_python_lzx_decode_integration_with_synthetic_xml(self):
        # This runs the actual repository decoder, without mocks or an external
        # binary, and checks that staging changes only the intended flow entry.
        entry, xml = menu.offline_cars_flow.ENTRY, pause_flow()
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.filelist = [info for info in archive.filelist if info.filename != entry]
            archive.NameToInfo = {info.filename: info for info in archive.filelist}
            archive.writestr(entry, xmem_uncompressed(xml))
        patch_codec(self.archive, entry, 21, len(xml), zlib.crc32(xml))
        before = self.source_snapshot()
        report = self.stage()
        with zipfile.ZipFile(self.output / "media.zip") as result:
            self.assertEqual(result.read(entry), menu.offline_cars_flow.patch_pause_xml(xml))
        self.assertEqual(self.source_snapshot(), before)
        self.assertEqual(report["offline_cars_flow"]["source_compression"], 21)

    def test_decoder_stored_deflate_and_full_frame_variants(self):
        for data in (b"authored XML payload", b"X" * 32768):
            for codec in (0, 8, 21):
                with self.subTest(size=len(data), codec=codec):
                    if codec == 0:
                        packed = data
                    elif codec == 8:
                        compressor = zlib.compressobj(wbits=-15)
                        packed = compressor.compress(data) + compressor.flush()
                    else:
                        packed = xmem_uncompressed(data)
                        if len(data) == 32768:
                            packed = packed[3:]  # Full-size frames use a two-byte header.
                    info = zipfile.ZipInfo("flow")
                    info.compress_type, info.file_size = codec, len(data)
                    info.compress_size, info.CRC = len(packed), zlib.crc32(data)
                    self.assertEqual(menu.decode_flow(info, packed), data)

    def test_decoder_rejects_truncation_size_crc_and_invalid_blocks(self):
        data = b"synthetic XML content"
        payload = xmem_uncompressed(data)
        cases = [payload[:-8], b"\xff", b"\x01", b"\x00\x00", b"\xff\x00\x00\x00\x01x",
                 payload + b"garbage", payload[:3] + b"\xff\xff" + payload[5:]]
        for broken in cases:
            info = zipfile.ZipInfo("flow")
            info.compress_type, info.file_size = 21, len(data)
            info.compress_size, info.CRC = len(broken), zlib.crc32(data)
            with self.subTest(broken=broken[:8]), self.assertRaises(ValueError):
                menu.decode_flow(info, broken)
        for field, value in (("file_size", len(data) + 1), ("file_size", menu.MAX_FLOW_BYTES + 1),
                             ("CRC", 0), ("compress_size", len(payload) + 1), ("compress_type", 12)):
            info = zipfile.ZipInfo("flow")
            info.compress_type, info.file_size = 21, len(data)
            info.compress_size, info.CRC = len(payload), zlib.crc32(data)
            setattr(info, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                menu.decode_flow(info, payload)

    def test_wrong_reload_contract_or_invalid_xml_does_not_publish(self):
        for data in (pause_flow().replace(b"global.disable_streaming", b"global.changed_behavior"), b"<broken"):
            self.make_archive()
            entry = menu.offline_cars_flow.ENTRY
            with zipfile.ZipFile(self.archive, "a") as archive:
                archive.filelist = [info for info in archive.filelist if info.filename != entry]
                archive.NameToInfo = {info.filename: info for info in archive.filelist}
                archive.writestr(entry, data)
            before = self.source_snapshot()
            with self.assertRaises(ValueError):
                self.stage()
            self.assertFalse(self.output.exists())
            self.assertEqual(self.source_snapshot(), before)


class ExecutableContracts(Fixture):
    def test_recognized_title_and_version(self):
        menu.verify_executable(self.update / "default.xex")

    def test_other_game_or_update_rejected_without_output(self):
        for title, version in ((0x12345678, menu.TU4_VERSION), (menu.TITLE_ID, 0x40A)):
            with self.subTest(title=title, version=version):
                (self.update / "default.xex").write_bytes(xex(title, version))
                with self.assertRaisesRegex(ValueError, "Forza Horizon 1 TU4"):
                    self.stage()
                self.assertFalse(self.output.exists())

    def test_truncated_ambiguous_and_unbounded_xex_headers_rejected(self):
        cases = [b"XEX2", b"OTHER" + bytes(123)]
        for offset, value in ((20, 0), (20, 1025), (8, 129), (28, 120), (28, 4)):
            data = bytearray(xex())
            struct.pack_into(">I", data, offset, value)
            cases.append(data)
        duplicate = bytearray(xex())
        struct.pack_into(">I", duplicate, 20, 2)
        struct.pack_into(">II", duplicate, 32, 0x40006, 64)
        cases.append(duplicate)
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                (self.update / "default.xex").write_bytes(data)
                with self.assertRaises(ValueError):
                    self.stage()
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
