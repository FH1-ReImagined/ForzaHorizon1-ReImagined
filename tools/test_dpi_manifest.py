"""forza.exe must stay per-monitor-v2 DPI aware (#418): the manifest is well-formed, asks for it,
and the resource script embeds it. Run: python tools/test_dpi_manifest.py"""
import pathlib
import unittest
import xml.etree.ElementTree as ET

RES = pathlib.Path(__file__).resolve().parent.parent / "forza-recomp" / "resources"


class DpiManifest(unittest.TestCase):
    def test_manifest_asks_for_per_monitor_v2(self):
        root = ET.parse(RES / "forza.manifest").getroot()
        text = {el.tag.split("}")[1]: (el.text or "") for el in root.iter()
                if el.tag.endswith("}dpiAware") or el.tag.endswith("}dpiAwareness")}
        self.assertEqual(text["dpiAware"].strip(), "true/pm")
        self.assertTrue(text["dpiAwareness"].strip().startswith("PerMonitorV2"))

    def test_resource_script_embeds_it(self):
        rc = (RES / "app.rc.in").read_text()
        self.assertIn('1 24 "@CMAKE_CURRENT_SOURCE_DIR@/resources/forza.manifest"', rc)


if __name__ == "__main__":
    unittest.main()
