import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import schema
from forza_launcher.config import Config, render_toml


class GamertagTests(unittest.TestCase):
    def test_loaded_and_exported_configuration_obey_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.json"
            path.write_text(json.dumps({"settings": {"gamertag": "ABCDEFGHIJKLMNOPQRST"}}))
            cfg = Config(path)
            self.assertEqual(cfg.settings["gamertag"], "ABCDEFGHIJKLMNO")
            cfg.settings["gamertag"] = "W" * 20
            self.assertEqual(tomllib.loads(render_toml(cfg))["gamertag"], "W" * 15)

    def test_short_empty_and_unrelated_strings_preserved(self):
        setting = schema.BY_KEY["gamertag"]
        for name in ("", "Driver", "ABCDEFGHIJKLMNO"):
            self.assertEqual(schema.coerce(setting, name), name)
        other = next(s for s in schema.S if s.kind == "str" and s.key != "gamertag")
        self.assertEqual(schema.coerce(other, "x" * 80), "x" * 80)

    def test_widget_limits_typing_and_pasting(self):
        from PySide6.QtWidgets import QApplication
        from forza_launcher.ui.settings_page import make_control
        app = QApplication.instance() or QApplication([])
        values = []
        widget = make_control(schema.BY_KEY["gamertag"], "", values.append)
        self.assertEqual(widget.maxLength(), 15)
        widget.insert("W" * 20)
        self.assertEqual(widget.text(), "W" * 15)
        widget.editingFinished.emit()
        self.assertEqual(values[-1], "W" * 15)
        widget.setText("ABCDEFGHIJKLMNOPQRST")
        self.assertEqual(widget.text(), "ABCDEFGHIJKLMNO")
        widget.clear()
        widget.editingFinished.emit()
        self.assertEqual(values[-1], "")
        widget.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
