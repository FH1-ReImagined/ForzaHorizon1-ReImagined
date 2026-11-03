"""Extraction boundary checks using synthetic translations, without game files."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import extract_horizon2_grid_events as grid


class GridExtraction(unittest.TestCase):
    def fixture(self, root, suffix=""):
        generated = root / "generated"
        generated.mkdir()
        (root / "default.xex").write_bytes(b"synthetic")
        bodies = {name: f"DEFINE_REX_FUNC({name}) {{\n  REX_FUNC_PROLOGUE();\n}}"
                  for name in grid.FUNCTIONS}
        bodies["sub_82964000"] = (
            "DEFINE_REX_FUNC(sub_82964000) {\n"
            "  REX_FUNC_PROLOGUE();\n"
            "  sub_824F46E0(ctx, base);\n"
            "  sub_83000500(ctx, base);\n"
            "  sub_83001A88(ctx, base);\n" + suffix + "}")
        source = generated / "horizon2_ui_recomp.0.cpp"
        source.write_text("\n\n".join(bodies.values()), encoding="utf-8")
        return generated, source, bodies

    def extract(self, generated, output):
        with patch.object(grid, "EXECUTABLE_SHA256", hashlib.sha256(b"synthetic").hexdigest()):
            return grid.extract(generated, output)

    def test_only_symbols_change_and_inputs_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated, source, bodies = self.fixture(root)
            original = source.read_bytes()
            output = root / "extracted/grid"
            report = self.extract(generated, output)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((root / "default.xex").read_bytes(), b"synthetic")
            translated = (output / "grid_events.inc").read_text(encoding="utf-8")
            for name, body in bodies.items():
                self.assertEqual(report["functions"][name]["translation_sha256"],
                                 hashlib.sha256(body.encode()).hexdigest())
                for old, new in {**grid.FUNCTIONS, **grid.BOUNDARIES}.items():
                    body = body.replace(old, new)
                self.assertIn(body, translated)
            with self.assertRaises(FileExistsError):
                self.extract(generated, output)

    def test_reject_unreviewed_dependencies_before_output(self):
        for suffix in ("  sub_81234567 ( ctx, base);\n",
                       "  REX_CALL_INDIRECT_FUNC(0);\n",
                       "  REX_CALL_INDIRECT_FUNC (0);\n"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                generated, _, _ = self.fixture(root, suffix)
                output = root / "extracted/grid"
                with self.assertRaises(ValueError):
                    self.extract(generated, output)
                self.assertFalse(output.exists())

    def test_reject_duplicate_or_missing_function(self):
        for duplicate in (True, False):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                generated, source, bodies = self.fixture(root)
                text = source.read_text(encoding="utf-8")
                body = bodies["sub_826994E0"]
                source.write_text(text + "\n" + body if duplicate else text.replace(body, ""),
                                  encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.extract(generated, root / "extracted/grid")

    def test_reject_public_output_or_unverified_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated, _, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                self.extract(generated, root / "public")
            with self.assertRaises(ValueError):
                grid.extract(generated, root / "extracted/grid")
            self.assertFalse((root / "extracted/grid").exists())


if __name__ == "__main__":
    unittest.main()
