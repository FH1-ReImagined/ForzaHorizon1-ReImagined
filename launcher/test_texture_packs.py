import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, crash_report, game, power, schema, texture_compress, texture_packs  # noqa: E402
from forza_launcher.config import Config  # noqa: E402

try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

FP = ["%016x" % (0x1111111111111111 * i) for i in range(1, 9)]


def dds(width=8, height=8, four_cc=b"DXT1", caps2=0, data=None, dx10=None):
    """A DDS file: a legacy header with a four CC (or DX10 and its extension)."""
    header = [0] * 31
    header[0:4] = [124, 0x1007, height, width]
    header[6] = 1
    header[18:21] = [32, 0x4, struct.unpack("<I", four_cc)[0]]
    header[26:28] = [0x1000, caps2]
    head = b"DDS " + struct.pack("<31I", *header)
    if dx10 is not None:
        head += struct.pack("<5I", *dx10)
    blocks = ((width + 3) // 4) * ((height + 3) // 4) * (8 if four_cc in (b"DXT1", b"ATI1") else 16)
    return head + (data if data is not None else b"\0" * blocks)


def png(width=8, height=8):
    """A PNG header and IHDR (enough for the launcher's check; the game reads the rest)."""
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sIIBBBBB", 13, b"IHDR", width, height, 8, 6, 0, 0, 0) + b"\0" * 16


def make_pack(root: Path, folder: str, files: dict, manifest: str | None = None) -> Path:
    d = root / folder
    d.mkdir(parents=True)
    if manifest is not None:
        (d / "pack.toml").write_text(manifest, encoding="utf-8")
    for rel, data in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return d


def make_zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            if isinstance(data, zipfile.ZipInfo):
                z.writestr(data, b"target")
            else:
                z.writestr(name, data)
    return path


class TexturePackTests(unittest.TestCase):
    """Settings > Display > Texture packs (#384, docs/texture_packs.md)."""

    def setUp(self):
        platform = patch.object(power, "IS_WINDOWS", False)
        platform.start()
        self.addCleanup(platform.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root / "home")
        self.cfg.online["mode"] = "offline"
        self.install = game.GameInstall(self.root / "rt", self.root / "rt" / game.EXE)
        (self.root / "rt").mkdir()

    def setting(self, key):
        return next(s for s in schema.S if s.key == key)

    def plan(self):
        return game.build_plan(self.cfg, self.install, write=False, log=lambda _t: None)

    def test_two_options_in_their_own_section(self):
        for key, label in (("texture_dump", "Dump textures"),
                           ("texture_replace", "Use texture packs")):
            with self.subTest(key=key):
                s = self.setting(key)
                self.assertEqual((s.label, s.group, s.section, s.kind),
                                 (label, "Display", "Texture packs", "bool"))
                self.assertIs(s.default, False)
                self.assertTrue(s.restart)
                self.assertIn(key, crash_report.SETTING_KEYS)
        sections = [s.section for s in schema.S if s.group == "Display"]
        start = sections.index("Texture packs")
        self.assertEqual(sections[start:start + 2], ["Texture packs", "Texture packs"])
        self.assertNotIn("Texture packs", sections[start + 2:])

    def test_off_writes_nothing_and_passes_no_folder(self):
        plan = self.plan()
        self.assertNotIn("texture_", plan.toml_text)
        self.assertFalse(any(a.startswith("--texture_pack_root") for a in plan.argv))

    def test_either_option_reaches_forza_toml_with_the_data_folder(self):
        folder = self.root / "home" / "textures"
        self.assertEqual(game.texture_pack_folder(self.cfg), folder)
        for key in ("texture_dump", "texture_replace"):
            with self.subTest(key=key):
                self.cfg.settings.update(texture_dump=False, texture_replace=False)
                self.cfg.settings[key] = True
                plan = self.plan()
                self.assertEqual(config.parse_toml(plan.toml_text)[key], True)
                self.assertIn(f"--texture_pack_root={folder}", plan.argv)

    def test_a_folder_in_the_extra_arguments_is_the_only_one(self):
        self.cfg.settings["texture_replace"] = True
        self.cfg.advanced["extra_args"] = "--texture_pack_root=/elsewhere"
        argv = self.plan().argv
        self.assertEqual([a for a in argv if a.startswith("--texture_pack_root")],
                         ["--texture_pack_root=/elsewhere"])

    def test_settings_page_has_the_folder_button_and_needs_vulkan(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 is not installed")
        from unittest.mock import MagicMock
        from forza_launcher.ui.settings_page import SettingsPage
        app = QApplication.instance() or QApplication([])
        window = MagicMock()
        window.preview_text.return_value = ""
        page = SettingsPage(self.cfg, window)
        self.addCleanup(page.close)
        self.assertEqual(page.texture_folder_button.text(), "Open textures folder")
        self.assertEqual(page.texture_folder_button.toolTip(), str(self.root / "home" / "textures"))
        self.assertTrue(page.rows["texture_replace"].control.isEnabled())
        self.cfg.settings["gpu_backend"] = "d3d12"
        page._apply_dependencies()
        self.assertFalse(page.rows["texture_replace"].control.isEnabled())
        self.assertTrue(page.rows["texture_dump"].control.isEnabled())
        app.processEvents()



class PackFolders(unittest.TestCase):
    """The packs in the textures folder (texture_packs.py) and what the game is told."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.cfg = Config(self.t / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.t / "home")
        self.cfg.online["mode"] = "offline"
        self.dir = texture_packs.textures_dir(self.cfg)
        self.dir.mkdir(parents=True)

    def test_files_are_checked_as_the_game_reads_them(self):
        make_pack(self.dir, "Rocks", {
            f"{FP[0]}.dds": dds(),
            f"sub/{FP[1].upper()}.PNG": png(16, 8),
            f"{FP[2]}.dds": dds(caps2=0x200),                       # cube map
            f"{FP[3]}.dds": dds(four_cc=b"DX10", dx10=(2, 3, 0, 1, 0)),  # DXGI 2: float
            f"{FP[4]}.dds": dds(width=64, data=b"\0" * 8),           # shorter than its header
            f"{FP[5]}.dds": dds(four_cc=b"DX10", dx10=(98, 3, 0, 1, 0), data=b"\0" * 64),  # BC7
            f"{FP[6]}.png": png(20000, 4),
            f"{FP[0]}.png": png(),                                   # the .dds wins
            "preview.jpg": b"x", "readme.txt": b"hi",
        }, 'name = "Rock pack"\nauthor = "Me"\nversion = "2"\n')
        pack = texture_packs.read_pack(self.dir / "Rocks")
        self.assertEqual((pack.title, pack.author, pack.version, pack.problem), ("Rock pack", "Me", "2", ""))
        self.assertEqual(pack.names, sorted([FP[0], FP[1], FP[5]]))
        self.assertEqual(pack.files, 3)
        self.assertIn("4 file(s) the game cannot use", pack.note)
        self.assertIn("a cube map", pack.note)
        self.assertIn("1 texture(s) are in it twice", pack.note)
        self.assertIn("1 file(s) are not named by a fingerprint", pack.note)
        # BC1 8x8 (32 bytes), a 16x8 PNG as RGBA8 (512), BC7 8x8 (64); with mips (4/3).
        self.assertEqual(pack.gpu_bytes, 32 * 4 // 3 + 512 * 4 // 3 + 64 * 4 // 3)
        make_pack(self.dir, "Empty", {"notes.txt": b"x"})
        self.assertIn("no texture files", texture_packs.read_pack(self.dir / "Empty").problem)
        if os.name != "nt":  # Windows folders cannot have a | anyway
            make_pack(self.dir, "a|b", {f"{FP[0]}.dds": dds()})
            self.assertIn("|", texture_packs.read_pack(self.dir / "a|b").problem)

    def test_listed_in_order_with_the_made_pack_first(self):
        for name in ("Beta", "Alpha", texture_packs.HD_PACK, ".adding-x", "dump"):
            make_pack(self.dir, name, {f"{FP[0]}.dds": dds()})
        listed = texture_packs.listed(self.cfg)
        self.assertEqual([p.folder for p in listed], [texture_packs.HD_PACK, "Alpha", "Beta"])
        self.assertEqual(listed[0].title, "HD pack (made on this PC)")
        listed.reverse()
        listed[0].enabled = False
        texture_packs.remember(self.cfg, listed)
        self.assertEqual(self.cfg.d["texture_packs"], {"order": ["Beta", "Alpha", texture_packs.HD_PACK],
                                                       "disabled": ["Beta"]})
        self.assertEqual([p.folder for p in texture_packs.enabled(self.cfg)], ["Alpha", texture_packs.HD_PACK])
        # All three have FP[0]: the lowest one in the list wins.
        clashes = texture_packs.conflicts(texture_packs.listed(self.cfg))
        self.assertEqual(clashes["Alpha"], [(FP[0], texture_packs.HD_PACK, False)])
        self.assertEqual(clashes[texture_packs.HD_PACK], [(FP[0], "Alpha", True)])
        self.assertNotIn("Beta", clashes)

    def test_loose_files_are_the_first_pack_and_stay(self):
        """Milestone 1's layout: texture files directly in textures/ keep working, as the pack "."
        before every other (any pack wins over them); it can be turned off, not removed."""
        make_pack(self.dir, "Alpha", {f"{FP[0]}.dds": dds()})
        (self.dir / f"{FP[0]}.png").write_bytes(png())
        (self.dir / f"{FP[1]}.dds").write_bytes(dds())
        (self.dir / "notes.txt").write_text("x")
        listed = texture_packs.listed(self.cfg)
        self.assertEqual([p.folder for p in listed], [texture_packs.LOOSE, "Alpha"])
        loose = listed[0]
        self.assertEqual((loose.title, loose.files, loose.problem, loose.note), ("Loose files (textures folder)",
                                                                                 2, "", ""))
        self.assertEqual(loose.names, [FP[0], FP[1]])  # not Alpha's files: only the folder's own
        # A saved order cannot move it: it stays first.
        self.cfg.d["texture_packs"] = {"order": ["Alpha", texture_packs.LOOSE], "disabled": []}
        self.assertEqual([p.folder for p in texture_packs.listed(self.cfg)], [texture_packs.LOOSE, "Alpha"])
        self.assertEqual(texture_packs.conflicts(texture_packs.listed(self.cfg))[texture_packs.LOOSE],
                         [(FP[0], "Alpha", False)])
        self.cfg.settings["texture_replace"] = True
        self.assertEqual(texture_packs.launch_args(self.cfg), ["--texture_packs=.|Alpha"])
        self.cfg.d["texture_packs"]["disabled"] = [texture_packs.LOOSE]
        self.assertEqual(texture_packs.launch_args(self.cfg), ["--texture_packs=Alpha"])
        with self.assertRaises(texture_packs.InstallError) as e:
            texture_packs.remove(self.cfg, texture_packs.LOOSE)
        self.assertIn("not a pack folder", str(e.exception))
        self.assertTrue((self.dir / f"{FP[0]}.png").is_file())
        # Without loose files there is no such entry.
        for name in (f"{FP[0]}.png", f"{FP[1]}.dds"):
            (self.dir / name).unlink()
        self.assertEqual([p.folder for p in texture_packs.listed(self.cfg)], ["Alpha"])

    def test_the_game_gets_the_packs_in_order(self):
        make_pack(self.dir, "Alpha", {f"{FP[0]}.dds": dds()})
        make_pack(self.dir, "Beta Two", {f"{FP[0]}.png": png()})
        install = game.GameInstall(self.t / "rt", self.t / "rt" / game.EXE)
        (self.t / "rt").mkdir()

        def argv():
            return game.build_plan(self.cfg, install, write=False, log=lambda _t: None).argv

        self.assertFalse(any(a.startswith("--texture_packs") for a in argv()))
        self.cfg.settings["texture_replace"] = True
        self.assertIn("--texture_packs=Alpha|Beta Two", argv())
        self.cfg.d["texture_packs"] = {"order": ["Beta Two", "Alpha"], "disabled": ["Alpha"]}
        self.assertIn("--texture_packs=Beta Two", argv())
        self.cfg.d["texture_packs"]["disabled"] = ["Alpha", "Beta Two"]
        self.assertIn("--texture_packs=|", argv())
        # The player's own folder or list: theirs alone.
        self.cfg.advanced["extra_args"] = "--texture_pack_root=/elsewhere"
        self.assertFalse(any(a.startswith("--texture_packs") for a in argv()))


class AddingPacks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.cfg = Config(self.t / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.t / "home")
        self.dir = texture_packs.textures_dir(self.cfg)
        self.src = self.t / "downloads"
        self.src.mkdir()

    def names(self):
        return sorted(p.name for p in self.dir.iterdir()) if self.dir.exists() else []

    def test_a_zip_in_one_folder_and_only_its_textures(self):
        z = make_zip(self.src / "rocks-v2.zip", {
            "Rocks HD/pack.toml": 'name = "Rocks HD"\n',
            f"Rocks HD/textures/{FP[0]}.dds": dds(),
            "Rocks HD/README.md": "hi",
            "Rocks HD/run-me.exe": "MZ",
        })
        pack = texture_packs.install(z, self.dir)
        self.assertEqual((pack.folder, pack.title, pack.files), ("Rocks HD", "Rocks HD", 1))
        got = sorted(p.relative_to(self.dir / "Rocks HD").as_posix() for p in (self.dir / "Rocks HD").rglob("*")
                     if p.is_file())
        self.assertEqual(got, ["README.md", "pack.toml", f"textures/{FP[0]}.dds"])
        self.assertEqual(self.names(), ["Rocks HD"])  # no staging folder left
        with self.assertRaises(texture_packs.PackExists):
            texture_packs.install(z, self.dir)
        self.assertEqual(texture_packs.install(z, self.dir, replace=True).folder, "Rocks HD")
        texture_packs.remove(self.cfg, "Rocks HD")
        self.assertEqual(self.names(), [])

    def test_refused_in_plain_words(self):
        slip = make_zip(self.src / "slip.zip", {f"../{FP[0]}.dds": dds()})
        absolute = make_zip(self.src / "abs.zip", {f"/tmp/{FP[0]}.dds": dds()})
        link_info = zipfile.ZipInfo(f"{FP[0]}.dds")
        link_info.external_attr = (0o120777 << 16)
        linked = make_zip(self.src / "link.zip", {"x": link_info})
        dump = make_zip(self.src / "mydump.zip", {"dump/index.tsv": "x", f"dump/{FP[0]}.dds": dds()})
        nothing = make_zip(self.src / "nothing.zip", {"readme.txt": "x", "a.dds": dds()})
        unusable = make_zip(self.src / "cube.zip", {f"{FP[0]}.dds": dds(caps2=0x200)})
        bad = self.src / "bad.zip"
        bad.write_bytes(b"nope")
        cases = {slip: "outside its own folder", absolute: "outside its own folder", linked: "link",
                 dump: "texture dump", nothing: "holds no texture pack", unusable: "would not be used",
                 bad: "not a zip", self.src / "gone.zip": "not there"}
        for source, words in cases.items():
            with self.subTest(source=source.name):
                with self.assertRaises(texture_packs.InstallError) as e:
                    texture_packs.install(source, self.dir)
                self.assertIn(words, str(e.exception))
        self.assertFalse((self.t / f"{FP[0]}.dds").exists())
        self.assertEqual([n for n in self.names() if not n.startswith(".")], [])
        with patch.object(texture_packs, "MAX_INSTALL", 10):
            big = make_zip(self.src / "big.zip", {f"{FP[0]}.dds": dds()})
            with self.assertRaises(texture_packs.InstallError) as e:
                texture_packs.install(big, self.dir)
            self.assertIn("32 GB", str(e.exception))

    def test_a_folder_a_made_pack_and_the_dump_check(self):
        make_pack(self.src, "hd-pack", {f"{FP[0]}.png": png(), f"{FP[1]}.png": png()})
        (self.dir / "dump").mkdir(parents=True)
        (self.dir / "dump" / f"{FP[0]}.png").write_bytes(png())
        pack = texture_packs.install(self.src / "hd-pack", self.dir)
        self.assertEqual(pack.folder, "hd-pack (added)")  # Make HD pack's folder stays its own
        self.assertIn("1 file(s) are the game's own textures unchanged", pack.note)
        with self.assertRaises(texture_packs.InstallError) as e:
            texture_packs.install(self.dir / "hd-pack (added)", self.dir)
        self.assertIn("already in the textures folder", str(e.exception))
        with self.assertRaises(texture_packs.InstallError):
            texture_packs.remove(self.cfg, "dump")
        self.assertTrue((self.dir / "dump").is_dir())


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TexturePacksPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.ui.texture_packs_page import TexturePacksPage
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.cfg = Config(self.t / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.t / "home")
        self.dir = texture_packs.textures_dir(self.cfg)
        make_pack(self.dir, "Alpha", {f"{FP[0]}.dds": dds()}, 'name = "Alpha pack"\n')
        make_pack(self.dir, "Beta", {f"{FP[0]}.png": png(), f"{FP[1]}.png": png()})
        # A build of the game without the compressor unless a test says otherwise.
        self.compressor = patch.object(texture_compress, "available", return_value=False)
        self.compressor.start()
        self.addCleanup(self.compressor.stop)   # stopping twice (with_compressor) is harmless
        self.page = TexturePacksPage(self.cfg, window=None)

    def with_compressor(self):
        self.compressor.stop()
        self.compressor = patch.object(texture_compress, "available", return_value=True)
        self.compressor.start()
        self.addCleanup(self.compressor.stop)
        self.page.refresh()

    def wait_for_task(self):
        task = self.page.task
        self.assertIsNotNone(task)
        task.wait(10000)
        for _ in range(20):
            QCoreApplication.processEvents()
            if self.page.task is None:
                break

    def tearDown(self):
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def names(self):
        return [self.page.table.item(r, 0).text() for r in range(self.page.table.rowCount())]

    def test_lists_orders_and_shows_the_clashes(self):
        self.assertEqual(self.names(), ["Alpha pack", "Beta"])
        self.assertIn("2 of 2 texture pack(s) will be used", self.page.message.text())
        self.assertIn("Use texture packs is off", self.page.message.text())
        self.page.table.selectRow(0)
        self.assertIn("Same texture(s) as Beta: " + FP[0] + "; Beta's copy is used", self.page.details.text())
        self.page.move(1)
        self.assertEqual(self.cfg.d["texture_packs"]["order"], ["Beta", "Alpha"])
        self.page.table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertEqual(self.cfg.d["texture_packs"]["disabled"], ["Beta"])

    def test_the_switches_are_the_settings(self):
        self.page.switches["texture_replace"].setChecked(True)
        self.assertTrue(self.cfg.settings["texture_replace"])
        self.page.switches["texture_dump"].setChecked(True)
        self.assertTrue(self.cfg.settings["texture_dump"])
        self.cfg.settings["gpu_backend"] = "d3d12"
        self.page.refresh()
        self.assertFalse(self.page.switches["texture_replace"].isEnabled())
        self.assertFalse(self.page.vulkan_note.isHidden())

    def test_add_a_dropped_zip_and_remove_it(self):
        from PySide6.QtCore import QMimeData, QPointF, QUrl
        from PySide6.QtGui import QDropEvent
        from unittest import mock
        z = make_zip(self.t / "Gamma.zip", {f"{FP[2]}.dds": dds()})
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(z))])
        self.page.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(self.names(), ["Alpha pack", "Beta", "Gamma"])
        self.assertIn("Added Gamma (1 texture(s))", self.page.message.text())
        self.page.table.selectRow(2)
        with mock.patch.object(self.page, "_confirm", return_value=True):
            self.page.remove_selected()
        self.assertEqual(self.names(), ["Alpha pack", "Beta"])

    def test_the_loose_files_row_cannot_move_or_go(self):
        (self.dir / f"{FP[3]}.dds").write_bytes(dds())
        self.page.refresh()
        self.assertEqual(self.names()[0], "Loose files (textures folder)")
        self.page.table.selectRow(0)
        self.assertFalse(self.page.down_button.isEnabled())
        self.assertFalse(self.page.remove_button.isEnabled())
        self.page.table.selectRow(1)
        self.assertFalse(self.page.up_button.isEnabled())   # nothing goes above it
        self.page.move(-1)
        self.assertEqual(self.names()[0], "Loose files (textures folder)")
        self.page.table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertEqual(self.cfg.d["texture_packs"]["disabled"], ["."])

    def test_three_packs_with_one_texture_name_the_one_used(self):
        (self.dir / f"{FP[0]}.dds").write_bytes(dds())   # loose, Alpha and Beta all have FP[0]
        self.page.refresh()
        self.page.table.selectRow(1)  # Alpha, in the middle
        text = self.page.details.text()
        self.assertIn("Same texture(s) as Loose files (textures folder): " + FP[0] + "; Beta's copy is used "
                      "(it is the lowest in the list).", text)
        self.assertIn("Same texture(s) as Beta: " + FP[0] + "; Beta's copy is used (it is lower in the list).", text)
        self.page.table.selectRow(2)
        self.assertIn("this texture pack's copy is used (it is lower than Alpha pack)", self.page.details.text())

    def test_4x_needs_10_gb_of_graphics_memory(self):
        dump = self.dir / "dump"
        dump.mkdir()
        (dump / "index.tsv").write_text(f"{FP[0]}\tk_DXT1\t512x512\t1\tBC1\n")
        (dump / f"{FP[0]}.png").write_bytes(png())
        for vram, integrated, offered in ((12, False, True), (8, False, False), (0, None, False),
                                          (16, True, False)):
            with self.subTest(vram=vram, integrated=integrated):
                self.cfg.d["specs"]["values"] = {"gpu_name": "Card", "vram_gb": vram, "integrated": integrated}
                self.page.refresh()
                four = self.page.scale.model().item(self.page.scale.findData(4))
                self.assertEqual(four.isEnabled(), offered)
                self.assertEqual(self.page.scale_note.isHidden(), offered)
                # The pack's graphics memory next to each choice: 1024x1024 or 2048x2048 RGBA8, mips.
                self.assertIn(f"about {1024 * 1024 * 4 * 4 // 3 / 2**20:.0f} MB", self.page.scale.itemText(0))
                self.assertIn(f"about {2048 * 2048 * 4 * 4 // 3 / 2**20:.0f} MB", four.text())
        self.assertIn("at least 10 GB", self.page.scale_note.text())
        self.cfg.d["specs"]["values"] = {"gpu_name": "Card", "vram_gb": 12, "integrated": False}
        self.page.refresh()
        self.page.scale.setCurrentIndex(self.page.scale.findData(4))
        self.cfg.d["specs"]["values"]["vram_gb"] = 6
        self.page.refresh()
        self.assertEqual(self.page.scale.currentData(), 2)   # back to 2x when 4x is not offered

    def test_4x_needs_6_gb_when_the_pack_is_compressed(self):
        dump = self.dir / "dump"
        dump.mkdir()
        (dump / "index.tsv").write_text(f"{FP[0]}\tk_DXT1\t512x512\t1\tBC1\n")
        (dump / f"{FP[0]}.png").write_bytes(png())
        self.with_compressor()
        for vram, offered in ((8, True), (6, True), (4, False)):
            with self.subTest(vram=vram):
                self.cfg.d["specs"]["values"] = {"gpu_name": "Card", "vram_gb": vram, "integrated": False}
                self.page.refresh()
                four = self.page.scale.model().item(self.page.scale.findData(4))
                self.assertEqual(four.isEnabled(), offered)
                # BC7 with mips: a byte a texel and a third.
                self.assertIn(f"about {1024 * 1024 * 4 // 3 / 2**20:.0f} MB", self.page.scale.itemText(0))
                self.assertIn(f"about {2048 * 2048 * 4 // 3 / 2**20:.0f} MB", four.text())
        self.assertIn("at least 6 GB", self.page.scale_note.text())
        self.assertNotIn("PNGs, which take 4 times", self.page.estimate.text())

    def test_without_the_compressor_the_hd_pack_says_how_to_get_it(self):
        dump = self.dir / "dump"
        dump.mkdir()
        (dump / "index.tsv").write_text(f"{FP[0]}\tk_DXT1\t512x512\t1\tBC1\n")
        (dump / f"{FP[0]}.png").write_bytes(png())
        self.page.refresh()
        self.assertIn("Written as PNGs, which take 4 times the graphics memory", self.page.estimate.text())
        self.assertIn("build the game again in Setup", self.page.estimate.text())

    def test_compress_pngs_of_a_pack(self):
        self.page.table.selectRow(1)   # Beta: two PNGs
        self.assertIn("2 of them are PNGs", self.page.details.text())
        self.assertIn("no texture compressor", self.page.details.text())
        self.assertFalse(self.page.compress_button.isEnabled())
        self.with_compressor()
        self.page.table.selectRow(1)
        self.assertTrue(self.page.compress_button.isEnabled())
        self.assertNotIn("no texture compressor", self.page.details.text())
        done = texture_compress.FolderResult(compressed=2, png_bytes=3 << 20, dds_bytes=1 << 20)
        with patch.object(texture_compress, "compress_folder", return_value=done) as compress, \
                patch.object(self.page, "_confirm", return_value=True):
            self.page.compress_button.click()
            self.assertFalse(self.page.make_button.isEnabled() and self.page.compress_button.isEnabled())
            self.wait_for_task()
        folder, work = compress.call_args.args[1], compress.call_args.args[2]
        self.assertEqual(folder, self.dir / "Beta")
        self.assertEqual(work.parent, self.dir)
        self.assertTrue(work.name.startswith("."))   # hidden: not listed as a pack
        self.assertIn('Compressed 2 texture(s) of "Beta": 3 MB of graphics memory as PNGs, 1 MB now.',
                      self.page.compress_status.text())

    def test_added_packs_are_compressed_when_asked(self):
        self.with_compressor()
        self.assertTrue(self.page.compress_added.isChecked())
        source = make_pack(self.t / "elsewhere", "Gamma", {f"{FP[3]}.png": png()})
        with patch.object(self.page, "compress_pack") as compress:
            self.assertTrue(self.page.add_path(source))
        compress.assert_called_once()
        self.assertEqual(compress.call_args.args[0].folder, "Gamma")
        self.assertEqual(compress.call_args.kwargs, {"ask": False})
        self.page.compress_added.setChecked(False)
        self.assertFalse(self.cfg.d["texture_packs_compress"])
        source = make_pack(self.t / "elsewhere", "Delta", {f"{FP[4]}.png": png()})
        with patch.object(self.page, "compress_pack") as compress:
            self.assertTrue(self.page.add_path(source))
        compress.assert_not_called()

    def test_make_hd_pack_needs_a_dump_and_estimates_it(self):
        self.assertIn("No texture dump yet", self.page.estimate.text())
        self.assertFalse(self.page.make_button.isEnabled())
        dump = self.dir / "dump"
        dump.mkdir()
        rows = [(FP[0], "k_DXT1", 512, 512, "BC1"), (FP[1], "k_DXN", 256, 256, "BC5"),
                (FP[2], "k_DXT1", 16, 16, "BC1"), (FP[3], "k_DXT4_5", 1024, 512, "BC3")]
        (dump / "index.tsv").write_text("".join(f"{a}\t{b}\t{c}x{d}\t1\t{e}\n" for a, b, c, d, e in rows))
        for fp in (FP[0], FP[3]):
            (dump / f"{fp}.png").write_bytes(png())
        self.page.refresh()
        text = self.page.estimate.text()
        self.assertIn("4 textures dumped: 2 to upscale; 2 left as they are (1 normal maps, 1 small).", text)
        self.assertTrue(self.page.make_button.isEnabled())
        # 2x: (1024x1024 + 2048x1024) RGBA8 with mips.
        self.assertIn(f"{(1024 * 1024 + 2048 * 1024) * 4 * 4 // 3 / 2**20:.0f} MB of graphics", text)
        self.cfg.d["specs"]["values"] = {"gpu_name": "Radeon Graphics", "vram_gb": 0.5, "integrated": True}
        self.page.refresh()
        self.assertIn("integrated", self.page.hd_warning.text())

    def test_completed_hd_pack_can_switch_scales_through_confirmation(self):
        dump = self.dir / "dump"
        dump.mkdir()
        (dump / "index.tsv").write_text(f"{FP[0]}\tk_DXT1\t64x64\t1\tBC1\n")
        (dump / f"{FP[0]}.png").write_bytes(png())
        self.cfg.d["specs"]["values"] = {"gpu_name": "Card", "vram_gb": 12, "integrated": False}
        make_pack(self.dir, "hd-pack", {f"{FP[0]}.png": png()}, "scale = 2\n")
        for old, new in ((2, 4), (4, 2)):
            with self.subTest(old=old, new=new):
                (self.dir / "hd-pack" / "pack.toml").write_text(f"scale = {old}\n")
                self.page.scale.setCurrentIndex(self.page.scale.findData(old))
                self.page.refresh()
                self.assertFalse(self.page.make_button.isEnabled())  # same scale is complete
                self.page.scale.setCurrentIndex(self.page.scale.findData(new))
                self.page.refresh()
                self.assertTrue(self.page.make_button.isEnabled())
                self.assertEqual(len(self.page.plan.todo), 1)
                self.assertEqual(self.page.plan.done, 0)
                self.assertIn(f"Making it at {new}x replaces your {old}x HD pack.", self.page.estimate.text())
                with patch.object(self.page, "_confirm", return_value=False) as confirm:
                    self.page.make_button.click()
                confirm.assert_called_once()
                self.assertIn(f"made at {old}x", confirm.call_args.args[1])
                self.assertIn(f"at {new}x", confirm.call_args.args[1])
                self.assertTrue((self.dir / "hd-pack" / f"{FP[0]}.png").is_file())


if __name__ == "__main__":
    unittest.main()
