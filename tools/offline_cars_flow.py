"""Build a private TU4 pause-flow overlay for the native offline garage.

No game XML is bundled here. The owner's pause-safe gift-car reload flow is
copied from their update, with the load source changed to the selected garage
car. The ordinary resume path remains the no-reload branch.
"""
import copy
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib


ENTRY = "gamemodes/pause.xml"
PREFIX = "offline_cars_"


def one(parent, path):
    matches = parent.findall(path)
    if len(matches) != 1:
        raise ValueError(f"Unsupported pause flow: expected one {path}")
    return matches[0]


def patch_pause_xml(data):
    root = ET.fromstring(data, parser=ET.XMLParser(
        target=ET.TreeBuilder(insert_comments=True)))
    if root.tag != "StateGroup" or root.get("id") != "pausesystem":
        raise ValueError("Expected the TU4 pausesystem state group")
    if any(e.get("id", "").startswith(PREFIX) for e in root.iter()):
        raise ValueError("Offline Cars flow is already installed")
    parent = one(root, "State[@id='show_pause_screen']")
    resume = one(parent, "Node[@id='pause_screen']/Exit[@id='exit']")
    if resume.get("target") != "show_hud_screen.enter":
        raise ValueError("Unsupported pause resume target")
    one(parent, "Node[@id='show_hud_screen']")
    prepare = one(root, "State[@id='gift_car_prepare']")
    unprepare = one(root, "State[@id='gift_car_unprepare']")
    # Validate the native sequencing that makes reloading from pause safe.
    chains = (
        (prepare, [
            ("place_car_to_last_valid_position", "global.place_car_to_last_valid_position"),
            ("store_car_position", "global.store_car_position"),
            ("disable_streaming", "global.disable_streaming"),
            ("unpause_game", "global.unpause_game_retain_audio_snapshot"),
            ("remove_all_cars", "global.remove_all_cars"),
            ("disable_presentationrenderandshadows", "global.disable_presentationrenderandshadows"),
            ("push_dynamic_render_ui_nocar", "global.push_dynamic_render_ui_nocar")]),
        (unprepare, [
            ("pop_dynamic_render_ui_nocar", "global.pop_dynamic_render_ui_nocar"),
            ("load_player_choices_car", "global.load_player_choices_car"),
            ("restore_car_position", "global.restore_car_position"),
            ("enable_presentationrender", "global.enable_presentationrender"),
            ("reset_traffic_cars", "global.reset_traffic_cars"),
            ("enable_streaming", "global.enable_streaming"),
            ("pause_game", "global.pause_game")]))
    for state, chain in chains:
        if one(state, "Entry[@id='enter']").get("target") != chain[0][0] + ".enter":
            raise ValueError("Unsupported native reload entry")
        for index, (name, native_state) in enumerate(chain):
            node = one(state, f"Node[@id='{name}']")
            target = chain[index + 1][0] + ".enter" if index + 1 < len(chain) else ".exit"
            if node.get("state") != native_state or one(node, "Exit[@id='exit']").get("target") != target:
                raise ValueError(f"Unsupported native reload sequence at {name}")

    restore = copy.deepcopy(unprepare)
    restore.set("id", PREFIX + "restore")
    one(restore, "Node[@id='load_player_choices_car']").set(
        "state", "global.reload_player_car_and_livery")
    root.append(restore)
    decision = ET.SubElement(root, "State", id=PREFIX + "decision")
    ET.SubElement(decision, "Entry", id="enter")
    for name in ("exit", "reload"):
        ET.SubElement(decision, "Exit", id=name)
    behavior = ET.SubElement(decision, "Behaviour", id="CGarageReloadDecision")
    ET.SubElement(behavior, "Transition", id="onExit", target=".exit")
    ET.SubElement(behavior, "Transition", id="onExitReload", target=".reload")
    # CGarageReloadDecision consumes the flag set by the scoped native hook.
    # Its TU4 constructor (829BB3E8) enables flag reset by default.
    resume.set("target", PREFIX + "decision.enter")
    nodes = [
        ("decision", PREFIX + "decision", {"exit": "show_hud_screen.enter", "reload": PREFIX + "fade_out.enter"}),
        ("fade_out", "global.fade_out", {"exit": PREFIX + "prepare.enter"}),
        ("prepare", "gift_car_prepare", {"exit": PREFIX + "restore.enter"}),
        ("restore", PREFIX + "restore", {"exit": PREFIX + "fade_in.enter"}),
        ("fade_in", "global.fade_in", {"exit": "show_hud_screen.enter"})]
    for name, state, exits in nodes:
        node = ET.SubElement(parent, "Node", id=PREFIX + name, state=state)
        ET.SubElement(node, "Entry", id="enter")
        for exit_name, target in exits.items():
            ET.SubElement(node, "Exit", id=exit_name, target=target)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def replacement(update_zip, unxmem, compressed_payload):
    """Return (ZipInfo, stored XML) for patch_archive's replacements mapping.

    ``compressed_payload`` is the existing scene stager's raw ZIP reader.
    Decompression happens only in a temporary directory. The source archive
    is opened read-only; the caller writes into a new private update copy.
    """
    with zipfile.ZipFile(update_zip) as archive:
        entries = [i for i in archive.infolist() if i.filename.lower() == ENTRY]
        if len(entries) != 1:
            raise ValueError("Expected one TU4 gamemodes/pause.xml update entry")
        info = copy.copy(entries[0])
        if info.compress_type in (0, 8):
            data = archive.read(info)
        elif info.compress_type == 21:
            if not unxmem or not Path(unxmem).is_file():
                raise ValueError("Offline Cars flow requires --unxmem for the TU4 XMem XML")
            payload = compressed_payload(archive, info)
            with tempfile.TemporaryDirectory(prefix="fh1-offline-cars-") as temporary:
                packed = Path(temporary) / "pause.xmem"
                unpacked = Path(temporary) / "pause.xml"
                packed.write_bytes(payload)
                result = subprocess.run([str(Path(unxmem).resolve()), str(packed),
                                         str(unpacked), str(info.file_size)],
                                        capture_output=True, check=False)
                if result.returncode or not unpacked.is_file():
                    raise ValueError("Failed to decode TU4 pause.xml")
                data = unpacked.read_bytes()
            if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
                raise ValueError("Decoded TU4 pause.xml failed size/CRC validation")
        else:
            raise ValueError("Unsupported TU4 pause.xml compression")
    data = patch_pause_xml(data)
    info.compress_type = zipfile.ZIP_STORED
    info.file_size = len(data)
    info.CRC = zlib.crc32(data)
    return info, data
