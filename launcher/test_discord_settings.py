"""Discord sharing is optional and does not require a token or network sign-in."""
from pathlib import Path
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher.config import Config, import_toml, render_toml
from forza_launcher import builder


class DiscordSettingsTests(unittest.TestCase):
    def test_disabled_by_default_and_omitted_from_toml(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Config(Path(root) / "launcher.json")
            self.assertTrue(cfg.settings["discord_presence"])  # on by default
            self.assertEqual(cfg.settings["discord_application_id"], "1563015276583460934")  # the project's application
            self.assertNotIn("discord_", render_toml(cfg))

    def test_details_on_by_default_and_only_off_is_written(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "launcher.json"
            cfg = Config(path)
            self.assertTrue(cfg.settings["discord_presence_details"])
            self.assertNotIn("discord_presence_details", render_toml(cfg))
            cfg.settings["discord_presence_details"] = False  # the old, activity-only status
            cfg.save()
            text = render_toml(Config(path))
            self.assertIn("discord_presence_details = false", text)
            imported = Config(Path(root) / "another.json")
            import_toml(imported, text)
            self.assertFalse(imported.settings["discord_presence_details"])

    def test_first_run_clear_is_kept(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "launcher.json"
            cfg = Config(path)  # no launcher.json yet
            cfg.settings["discord_application_id"] = ""
            cfg.save()
            self.assertEqual(Config(path).settings["discord_application_id"], "")

    def test_empty_id_saved_by_an_older_launcher_becomes_the_default_once(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "launcher.json"
            path.write_text('{"version": 1, "settings": {"discord_presence": true, "discord_application_id": ""}}')
            cfg = Config(path)
            self.assertEqual(cfg.settings["discord_application_id"], "1563015276583460934")
            cfg.settings["discord_application_id"] = ""  # a deliberate choice after the fix sticks
            cfg.save()
            self.assertEqual(Config(path).settings["discord_application_id"], "")

    def test_sharing_and_public_id_survive_save_reload_and_toml_import(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "launcher.json"
            cfg = Config(path)
            cfg.settings["discord_presence"] = False  # on is the default, so only off is written
            cfg.settings["discord_application_id"] = "123456789012345678"
            cfg.online["mode"] = "offline"
            cfg.save()
            other = Config(path)
            text = render_toml(other)
            self.assertIn('discord_presence = false', text)
            self.assertIn('discord_application_id = "123456789012345678"', text)
            imported = Config(Path(root) / "another.json")
            import_toml(imported, text)
            self.assertFalse(imported.settings["discord_presence"])
            self.assertEqual(imported.settings["discord_application_id"], "123456789012345678")
            self.assertEqual(other.online["mode"], "offline")

    def test_runtime_retains_dependency_licenses_after_build_files_are_removed(self):
        for windows in (True, False):
            with self.subTest(windows=windows), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                output, sdk = root / "output", root / "sdk"
                (output / "licenses").mkdir(parents=True)
                (sdk / "bin").mkdir(parents=True)
                (sdk / "lib").mkdir(parents=True)
                for name in ("discord-rpc.txt", "rapidjson.txt"):
                    (output / "licenses" / name).write_text("Fixture notice: " + name)
                for name in ("forza.exe", "forza", "forza_XMediaFacade_default", "forza_SpeechFacade_default"):
                    (output / name).write_bytes(b"fixture")
                for name in ("librexruntime.so", "librexgpu-xenos.so"):
                    (sdk / "lib" / name).write_bytes(b"fixture")
                with patch.object(builder, "IS_WIN", windows), patch.object(builder.subprocess, "run"):
                    runtime = builder.stage_runtime(SimpleNamespace(root=root), builder.Reporter(), output, sdk)
                import shutil
                shutil.rmtree(output)
                for name in ("discord-rpc.txt", "rapidjson.txt"):
                    self.assertEqual((runtime / "licenses" / name).read_text(), "Fixture notice: " + name)


try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class DiscordUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sharing_explains_missing_or_invalid_application_setup(self):
        from forza_launcher.ui.settings_page import SettingsPage
        window = SimpleNamespace(status=lambda *_: None, write_toml=lambda: None,
                                 reset_settings=lambda: None, preview_text=lambda: "")
        with tempfile.TemporaryDirectory() as root:
            cfg = Config(Path(root) / "launcher.json")
            cfg.advanced["data_root"] = root
            page = SettingsPage(cfg, window)
            try:
                page.set_value("discord_presence", True)
                warning = page.rows["discord_presence"].warning
                self.assertTrue(warning.isHidden())  # the project's application ID is the default
                page.set_value("discord_application_id", "")
                self.assertFalse(warning.isHidden())
                self.assertIn("Advanced", warning.text())
                page.set_value("discord_application_id", "123456789012345678")
                self.assertTrue(warning.isHidden())
                page.set_value("discord_application_id", "not-a-public-id")
                self.assertFalse(warning.isHidden())
                page.set_value("discord_presence", False)
                self.assertTrue(warning.isHidden())
                self.assertFalse(page.rows["discord_presence_details"].control.isEnabled())
                page.set_value("discord_presence", True)
                self.assertTrue(page.rows["discord_presence_details"].control.isEnabled())
            finally:
                page._save_timer.stop()
                page.close()
                page.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
