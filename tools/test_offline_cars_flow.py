"""Native pause overlay safety and graph checks using synthetic XML only."""
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import offline_cars_flow as cars
from stage_horizon2_pause_scene import compressed_payload, patch_archive


def fixture():
    root = ET.Element("StateGroup", id="pausesystem")
    parent = ET.SubElement(root, "State", id="show_pause_screen")
    node = ET.SubElement(parent, "Node", id="pause_screen", state="pause_screen")
    ET.SubElement(node, "Exit", id="exit", target="show_hud_screen.enter")
    ET.SubElement(node, "Exit", id="map", target="map.enter")
    ET.SubElement(parent, "Node", id="show_hud_screen", state="global.show_hud_screen")
    ET.SubElement(parent, "Node", id="map", state="map.map_flow")
    sequences = {
        "gift_car_prepare": [
            ("place_car_to_last_valid_position", "place_car_to_last_valid_position"),
            ("store_car_position", "store_car_position"),
            ("disable_streaming", "disable_streaming"),
            ("unpause_game", "unpause_game_retain_audio_snapshot"),
            ("remove_all_cars", "remove_all_cars"),
            ("disable_presentationrenderandshadows", "disable_presentationrenderandshadows"),
            ("push_dynamic_render_ui_nocar", "push_dynamic_render_ui_nocar")],
        "gift_car_unprepare": [
            ("pop_dynamic_render_ui_nocar", "pop_dynamic_render_ui_nocar"),
            ("load_player_choices_car", "load_player_choices_car"),
            ("restore_car_position", "restore_car_position"),
            ("enable_presentationrender", "enable_presentationrender"),
            ("reset_traffic_cars", "reset_traffic_cars"),
            ("enable_streaming", "enable_streaming"),
            ("pause_game", "pause_game")]}
    for name, sequence in sequences.items():
        state = ET.SubElement(root, "State", id=name)
        ET.SubElement(state, "Entry", id="enter", target=sequence[0][0] + ".enter")
        ET.SubElement(state, "Exit", id="exit")
        for index, (identifier, native) in enumerate(sequence):
            node = ET.SubElement(state, "Node", id=identifier, state="global." + native)
            ET.SubElement(node, "Entry", id="enter")
            ET.SubElement(node, "Exit", id="exit", target=(
                sequence[index + 1][0] + ".enter" if index + 1 < len(sequence) else ".exit"))
    return ET.tostring(root)


class OfflineCarsFlow(unittest.TestCase):
    def test_only_resume_and_cloned_load_source_change(self):
        source = fixture()
        before = ET.fromstring(source)
        after = ET.fromstring(cars.patch_pause_xml(source))
        for name in ("gift_car_prepare", "gift_car_unprepare"):
            self.assertEqual(ET.tostring(before.find(f"State[@id='{name}']")),
                             ET.tostring(after.find(f"State[@id='{name}']")))
        parent = after.find("State[@id='show_pause_screen']")
        self.assertEqual(parent.find("Node[@id='pause_screen']/Exit[@id='map']").get("target"),
                         "map.enter")
        decision = parent.find("Node[@id='offline_cars_decision']")
        self.assertEqual(decision.find("Exit[@id='exit']").get("target"), "show_hud_screen.enter")
        self.assertEqual(decision.find("Exit[@id='reload']").get("target"), "offline_cars_fade_out.enter")
        restore = after.find("State[@id='offline_cars_restore']")
        self.assertEqual(restore.find("Node[@id='load_player_choices_car']").get("state"),
                         "global.reload_player_car_and_livery")
        # Every inserted transition resolves locally, or exits its enclosing state.
        for state in after.findall("State"):
            nodes = {n.get("id") for n in state.findall("Node")}
            for edge in state.iter():
                target = edge.get("target")
                if target and not target.startswith("."):
                    self.assertIn(target.rsplit(".", 1)[0], nodes)

    def test_reject_duplicate_overlay_or_unrecognized_reload_sequence(self):
        with self.assertRaisesRegex(ValueError, "already installed"):
            cars.patch_pause_xml(cars.patch_pause_xml(fixture()))
        changed = fixture().replace(b"global.disable_streaming", b"global.other_behavior")
        with self.assertRaisesRegex(ValueError, "reload sequence"):
            cars.patch_pause_xml(changed)
        changed = fixture().replace(b'target="show_hud_screen.enter"', b'target="other.enter"')
        with self.assertRaisesRegex(ValueError, "resume target"):
            cars.patch_pause_xml(changed)

    def test_archive_overlay_preserves_source_and_other_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.zip"
            output = Path(temporary) / "output.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(cars.ENTRY, fixture(), compress_type=zipfile.ZIP_DEFLATED)
                archive.writestr("unrelated.dat", b"untouched")
            original = source.read_bytes()
            replacement = cars.replacement(source, None, compressed_payload)
            patch_archive(source, output, {cars.ENTRY: replacement})
            self.assertEqual(source.read_bytes(), original)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("unrelated.dat"), b"untouched")
                self.assertEqual(archive.read(cars.ENTRY), replacement[1])
                self.assertEqual(archive.namelist().count(cars.ENTRY), 1)
            with self.assertRaises(FileExistsError):
                patch_archive(source, output, {cars.ENTRY: replacement})


if __name__ == "__main__":
    unittest.main()
