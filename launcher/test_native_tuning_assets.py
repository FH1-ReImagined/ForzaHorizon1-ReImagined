"""Run the shipped tuning preparation on authored XEX-header/ZIP fixtures only."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from forza_launcher import builder, config, game

spec = importlib.util.spec_from_file_location("tuning_assets_fixture", HERE.parent / "tools/native_tuning_assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class TuningAssets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.update = self.root / "tu4"
        self.update.mkdir()
        # Authored, bounded execution-info metadata, not executable game code.
        header = bytearray(64)
        header[:4] = b"XEX2"
        struct.pack_into(">I", header, 8, 64)
        struct.pack_into(">I", header, 20, 1)
        struct.pack_into(">II", header, 24, 0x40006, 32)
        struct.pack_into(">I", header, 36, assets.TU4_VERSION)
        struct.pack_into(">I", header, 44, assets.TITLE_ID)
        (self.update / "default.xex").write_bytes(header)
        for name in builder.TU_EXECUTABLES[1:]:
            (self.update / name).write_bytes(b"authored facade fixture")
        self.members = {"ui/scenes/ui4/108_c_buy_carselect." + ext:
                        ("authored " + ext).encode() for ext in ("bgf", "bsg", "fbf")}
        self.members.update({"gamemodes/offline.xml": b"<pause><stock-actions/></pause>",
                             "ui/flow/stock.xml": b"<flow id='stock'/>",
                             "ui/scenes/ui4/stock.bsg": b"authored stock scene"})
        with zipfile.ZipFile(self.update / "media.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.members.items():
                archive.writestr(name, data)
        self.original = {p.name: p.read_bytes() for p in self.update.iterdir()}
        patch = mock.patch.object(assets, "ORIGINAL_ARCHIVE_SHA256", assets.digest(self.update / "media.zip"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_actual_build_preparation_adds_only_66_tuning_aliases_and_preserves_stock(self):
        class Reporter(builder.Reporter):
            def log(self, text):
                pass
        rep = Reporter()
        opts = builder.Options(self.root, jobs=1)

        def prepare(cmd, *args, **kwargs):
            assets.stage(self.update, cmd[cmd.index("--output") + 1])

        with mock.patch.object(builder, "native_tuning_script", return_value=Path(assets.__file__)), \
                mock.patch.object(builder, "run", side_effect=prepare):
            self.assertTrue(builder.stage_runtime_update(opts, rep, self.update))
        runtime = builder.runtime_dir(self.root)
        self.assertEqual({p.name: p.read_bytes() for p in self.update.iterdir()}, self.original)
        self.assertEqual({p.name: p.read_bytes() for p in (runtime / "update").iterdir()}, self.original)
        aliases = {f"ui/scenes/ui4/native_menu_tuning_{i}.{ext}"
                   for i in range(22) for ext in ("bgf", "bsg", "fbf")}
        with zipfile.ZipFile(runtime / "update-native/media.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(self.members) | aliases)
            self.assertIsNone(archive.testzip())
            for name, data in self.members.items():
                self.assertEqual(archive.read(name), data)  # Includes every stock XML and scene.
            for name in aliases:
                ext = name.rsplit(".", 1)[1]
                self.assertEqual(archive.read(name), self.members["ui/scenes/ui4/108_c_buy_carselect." + ext])
        for name in builder.TU_EXECUTABLES:
            self.assertEqual((runtime / "update-native" / name).read_bytes(), self.original[name])
        report = json.loads((runtime / "update-native/native-tuning-provenance.json").read_text())
        self.assertEqual({entry["alias"] for entry in report["entries"]}, aliases)
        cfg = config.Config(self.root / "launcher.json")
        install = game.GameInstall(runtime, runtime / game.EXE)
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for enabled in (False, True, False):
                cfg.settings["forza_car_tuning"] = enabled
                chosen = game.update_data_root(cfg, install, config.render_toml(cfg))
                self.assertEqual(chosen.name, "update-native" if enabled else "update")
                if not enabled:
                    self.assertEqual((chosen / "media.zip").read_bytes(), self.original["media.zip"])

    def test_invalid_input_never_creates_a_partial_overlay(self):
        output = self.root / "prepared"
        (self.update / "default.xex").write_bytes(b"not an XEX")
        with self.assertRaises(ValueError):
            assets.stage(self.update, output)
        self.assertFalse(output.exists())

    def test_wrong_update_reports_found_version_and_repair_action(self):
        path = self.update / "default.xex"
        header = bytearray(path.read_bytes())
        struct.pack_into(">I", header, 36, 0x100)
        path.write_bytes(header)
        with self.assertRaises(assets.PreparationError) as caught:
            assets.stage(self.update, self.root / "prepared")
        status = assets.error_diagnostic(caught.exception)
        self.assertEqual(status["code"], "wrong_title_update")
        self.assertIn("00000100", status["detail"])
        self.assertIn("Setup", status["action"])

    def test_missing_and_access_errors_have_distinct_actions(self):
        for error, code in ((FileNotFoundError("missing media.zip"), "missing_file"),
                            (PermissionError("output denied"), "access_denied")):
            with self.subTest(code=code):
                status = assets.error_diagnostic(error)
                self.assertEqual(status["code"], code)
                self.assertEqual(status["detail"], str(error))


if __name__ == "__main__":
    unittest.main()
