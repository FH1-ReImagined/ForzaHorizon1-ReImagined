"""An analog handbrake (#303): the settings match the SDK's cvars (patch 0133), the Handbrake row
binds a button and an axis (on the wheel or on a handbrake device) without changing the existing
wheel_buttons / wheel_axes formats, the handbrake device is picked like the driver picks it, and
the Bindings editor binds both by pulling a virtual handbrake and pressing a wheel button (SDL
virtual joysticks, no hardware). Runs alone from the repository root too:
python3 launcher/test_wheel_handbrake.py"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import bindings as b
from forza_launcher import schema
from forza_launcher.config import Config, import_toml, render_toml
from forza_launcher.gamepad import DeviceInfo

try:
    from PySide6.QtWidgets import QApplication
    import sdl2
    from test_wheel_devices import VirtualDevice
except ImportError:
    QApplication = None

PATCH = Path(__file__).resolve().parent.parent / "patches" / "rexglue-sdk" / \
    "0133-wheel-analog-handbrake.patch"
KEYS = ("wheel_handbrake_device", "wheel_handbrake_axis", "wheel_invert_handbrake")
ROW = next(a for a in b.WHEEL_ACTIONS if a.key == "b")
WHEEL = b.WheelInfo(0x3670, 0x0502, 4, 20, 1, False)
TBRS = b.AuxInfo("SIMAGIC TB-RS Handbrake", 0x3670, 0x0A04, 1, 0)


def settings(**values):
    out = schema.defaults()
    out.update(values)
    return out


def dev(name, vid, pid, instance=0, sdl_wheel=False):
    return DeviceInfo(instance, name, "", vid, pid, False, sdl_wheel)


class SettingsMatchTheSdk(unittest.TestCase):
    def test_cvars(self):
        text = "\n".join(line[1:] for line in PATCH.read_text().splitlines() if line.startswith("+"))
        found = {}
        for m in re.finditer(r"REXCVAR_DEFINE_(STRING|INT32|BOOL)\((\w+), (\"[^\"]*\"|\w+),(.*?);",
                             text, re.S):
            kind, name, value, rest = m.groups()
            found[name] = ({"STRING": lambda v: v.strip('"'), "INT32": int,
                            "BOOL": lambda v: v == "true"}[kind](value),
                           re.search(r"\.range\((\d+), (\d+)\)", rest))
        self.assertEqual(set(found), set(KEYS))
        for key in KEYS:
            with self.subTest(key=key):
                default, bounds = found[key]
                setting = schema.BY_KEY[key]
                self.assertEqual(setting.default, default)
                self.assertEqual(setting.section, "Racing wheel")
                if bounds:
                    self.assertEqual((setting.lo, setting.hi), tuple(int(v) for v in bounds.groups()))
        keys = [s.key for s in schema.for_group("Controls")]
        start = keys.index("wheel_pedal_axes") + 1
        self.assertEqual(tuple(keys[start:start + len(KEYS)]), KEYS)

    def test_defaults_stay_out_and_values_roundtrip(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cfg = Config(Path(folder.name) / "launcher.json")
        text = render_toml(cfg)
        for key in KEYS:
            self.assertNotIn(key, text)
        expected = dict(wheel_handbrake_device="SIMAGIC TB-RS Handbrake", wheel_handbrake_axis=2,
                        wheel_invert_handbrake=True, wheel_axes="steer=0,handbrake=3")
        cfg.settings.update(expected)
        imported = Config(Path(folder.name) / "other.json")
        import_toml(imported, render_toml(cfg))
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(imported.settings[key], value)


class Bindings(unittest.TestCase):
    def test_formats_stay_compatible(self):
        # Old strings read as before; a handbrake axis is one more wheel_axes key, after the pedals.
        self.assertEqual(b.parse_kv("steer=0,throttle=2,brake=3,clutch=1"),
                         {"steer": 0, "throttle": 2, "brake": 3, "clutch": 1})
        ch = b.set_handbrake_binding(settings(wheel_axes="steer=0,throttle=2"), "axis", 4)
        self.assertEqual(ch, {"wheel_axes": "steer=0,throttle=2,handbrake=4"})
        ch = b.set_handbrake_binding(settings(wheel_buttons="a=0,b=1"), "button", 7)
        self.assertEqual(ch, {"wheel_buttons": "a=0,b=7"})
        # The pedal rows and their text ignore the new key.
        s = settings(wheel_axes="throttle=2,handbrake=4")
        throttle = next(a for a in b.WHEEL_ACTIONS if a.key == "throttle")
        self.assertEqual(b.wheel_binding_text(throttle, s, WHEEL), "Axis 2")

    def test_row_text_button_and_axis(self):
        self.assertEqual(b.wheel_binding_text(ROW, settings(), WHEEL), "Button 1 (default)")
        self.assertEqual(b.wheel_binding_text(ROW, settings(), WHEEL, {"handbrake": TBRS}),
                         "Button 1 (default) + Axis 0 on SIMAGIC TB-RS Handbrake (default)")
        s = settings(wheel_axes="handbrake=3", wheel_buttons="b=5")
        self.assertEqual(b.wheel_binding_text(ROW, s, WHEEL, {"handbrake": TBRS}),
                         "Button 5 + Axis 3")  # the wheel's axis wins over the device
        s = settings(wheel_axes="handbrake=99", wheel_buttons="b=-1")
        self.assertEqual(b.wheel_binding_text(ROW, s, WHEEL, {"handbrake": TBRS}), "Not bound")
        s = settings(wheel_handbrake_device="SIMAGIC TB-RS Handbrake", wheel_handbrake_axis=1)
        self.assertEqual(b.wheel_binding_text(ROW, s, WHEEL, {"handbrake": TBRS}),
                         "Button 1 (default) + Axis 1 on SIMAGIC TB-RS Handbrake")

    def test_bind_one_keeps_the_other_clear_and_reset(self):
        s = settings(wheel_buttons="b=5")
        ch = b.set_handbrake_binding(s, "axis", 0, "SIMAGIC TB-RS Handbrake")
        self.assertEqual(ch, {"wheel_handbrake_device": "SIMAGIC TB-RS Handbrake",
                              "wheel_handbrake_axis": 0, "wheel_axes": ""})
        s.update(ch)
        self.assertEqual(s["wheel_buttons"], "b=5")
        ch = b.set_handbrake_binding(s, "axis", 3)
        self.assertEqual(ch, {"wheel_axes": "handbrake=3"})
        s.update(ch)
        ch = b.set_handbrake_binding(s, "axis", 0, "SIMAGIC TB-RS Handbrake")
        self.assertEqual(ch["wheel_axes"], "")  # the device again: the wheel's axis goes
        ch = b.clear_handbrake(settings(wheel_axes="steer=0", wheel_buttons="b=5"))
        self.assertEqual(ch, {"wheel_buttons": "b=-1", "wheel_axes": "steer=0,handbrake=99"})
        ch = b.reset_row(settings(wheel_axes="steer=0,handbrake=99", wheel_buttons="a=0,b=-1",
                                  wheel_handbrake_device="none", wheel_handbrake_axis=3,
                                  wheel_invert_handbrake=True), ROW)
        self.assertEqual(ch, {"wheel_buttons": "a=0", "wheel_axes": "steer=0",
                              "wheel_handbrake_device": "", "wheel_handbrake_axis": 0,
                              "wheel_invert_handbrake": False})

    def test_conflicts_include_the_axis(self):
        s = settings(wheel_axes="throttle=2,handbrake=2")
        self.assertIn(("Axis 2", ["Throttle", ROW.label]), b.wheel_conflicts(s, WHEEL))
        s = settings(wheel_handbrake_device="SIMAGIC P1000", wheel_handbrake_axis=1,
                     wheel_pedal_axes="brake=1")
        p1000 = b.AuxInfo("SIMAGIC P1000", 0x0483, 0x0525, 3, 0)
        conflicts = b.wheel_conflicts(s, WHEEL, {"pedals": p1000, "handbrake": p1000})
        self.assertIn(("Axis 1 on SIMAGIC P1000", ["Brake", ROW.label]), conflicts)

    def test_pick_like_the_driver(self):
        devices = [dev("SIMAGIC Alpha EVO Pro", 0x3670, 0x0501, sdl_wheel=True),
                   dev("SIMAGIC P1000", 0x0483, 0x0525), dev("SIMAGIC TB-RS Handbrake", 0x3670, 0x0A04)]
        self.assertEqual(b.pick_handbrake(devices, settings()), 2)
        self.assertIsNone(b.pick_handbrake(devices, settings(wheel_handbrake_device="none")))
        self.assertEqual(b.pick_handbrake(devices, settings(wheel_handbrake_device="p1000")), 1)
        self.assertEqual(b.pick_handbrake(devices, settings(wheel_handbrake_device="0483:0525")), 1)
        self.assertIsNone(b.pick_handbrake(devices, settings(wheel_handbrake_device="missing")))
        self.assertEqual(b.pick_handbrake([dev("Sim Hand Brake", 0x1234, 0x5678)], settings()), 0)
        rs = [dev("Logitech RS Shifter and Handbrake", 0x046D, 0xC278)]
        self.assertIsNone(b.pick_handbrake(rs, settings()))
        self.assertEqual(b.pick_handbrake(rs, settings(wheel_handbrake_device="rs shifter")), 0)

    def test_detector_takes_a_button_or_an_axis(self):
        base = b.Snapshot((0, -32768), (False, False))
        d = b.Detector(base, "any")
        self.assertIsNone(d.feed(base))
        self.assertEqual(d.feed(b.Snapshot((0, 0), (False, False))).kind, "axis")
        d = b.Detector(base, "any")
        self.assertEqual(d.feed(b.Snapshot((0, -32768), (False, True))), b.Detection("button", 1))


@unittest.skipIf(QApplication is None, "PySide6 or PySDL2 is not installed")
class EditorWithAHandbrake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.gamepad import Joysticks
        from forza_launcher.ui.controls_page import ControlsPage
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Config(Path(self.temp.name) / "launcher.json")
        self.backend = Joysticks()
        self.wheel = VirtualDevice("SIMAGIC EVO PRO", 0x3670, 0x0502, 4, 20, 1,
                                   sdl2.SDL_JOYSTICK_TYPE_WHEEL)
        self.handbrake = VirtualDevice("SIMAGIC TB-RS Handbrake", 0x3670, 0x0A04, 1, 2)
        for d in (self.handbrake, self.wheel):
            self.addCleanup(d.detach)
        for n, v in ((0, 0), (1, 32767), (2, 32767), (3, 32767)):
            self.wheel.axis(n, v)
        self.handbrake.axis(0, -32768)
        self.page = ControlsPage(self.cfg)
        self.page.bindings.start(self.backend)
        self.addCleanup(self.page.bindings.stop)
        self.ed = self.page.bindings
        self.ed.picker.setCurrentIndex(self.ed.picker.findData(self.wheel.instance))
        self.step()

    def step(self, n=2):
        for _ in range(n):
            self.ed.poll()
            self.app.processEvents()

    def row(self):
        return next(r for r in self.ed.rows if r.action.key == "b")

    def test_bind_the_handbrake_axis_and_a_button(self):
        ed, st = self.ed, self.cfg.settings
        label = ed.picker.itemText(ed.picker.findData(self.handbrake.instance))
        self.assertEqual(label, "SIMAGIC TB-RS Handbrake (handbrake, read with the wheel)")
        self.assertEqual(self.row().value.text(),
                         "Button 1 (default) + Axis 0 on SIMAGIC TB-RS Handbrake (default)")
        # A button on the handbrake device is not read: the bind ends with a hint, nothing set.
        ed.begin(self.row())
        self.step()
        self.handbrake.button(0, True)
        self.step()
        self.assertIn("are not read", ed.status.text())
        self.assertEqual(st.get("wheel_buttons", ""), "")
        self.handbrake.button(0, False)
        # Pull the handbrake.
        ed.begin(self.row())
        self.assertIn("Pull the handbrake", ed.status.text())
        self.step()
        self.handbrake.axis(0, 32767)
        self.step()
        self.assertIsNone(ed.binding)
        self.assertEqual((st["wheel_handbrake_device"], st["wheel_handbrake_axis"]),
                         ("3670:0a04", 0))
        self.assertFalse(st["wheel_invert_handbrake"])
        self.handbrake.axis(0, -32768)
        # A button on the wheel joins it.
        ed.begin(self.row())
        self.step()
        self.wheel.button(9, True)
        self.step()
        self.wheel.button(9, False)
        self.assertEqual(st["wheel_buttons"], "b=9")
        self.assertEqual(self.row().value.text(), "Button 9 + Axis 0 on SIMAGIC TB-RS Handbrake")
        # An axis on the wheel replaces the device's.
        ed.begin(self.row())
        self.step()
        self.wheel.axis(3, -32768)
        self.step()
        self.wheel.axis(3, 32767)
        self.assertEqual(st["wheel_axes"], "handbrake=3")
        self.assertFalse(st["wheel_invert_handbrake"])  # rests at the maximum, pulled down
        self.assertEqual(self.row().value.text(), "Button 9 + Axis 3")
        ed.clear(self.row())
        self.assertEqual((st["wheel_buttons"], st["wheel_axes"]), ("b=-1", "handbrake=99"))
        self.assertEqual(self.row().value.text(), "Not bound")
        ed.reset(self.row())
        self.assertEqual(self.row().value.text(),
                         "Button 1 (default) + Axis 0 on SIMAGIC TB-RS Handbrake (default)")
        # The handbrake device on its own: no rows, a pointer to the wheel.
        ed.picker.setCurrentIndex(ed.picker.findData(self.handbrake.instance))
        self.step()
        self.assertEqual(ed.rows, [])
        self.assertIn("Handbrake row", ed.intro.text())


if __name__ == "__main__":
    unittest.main()
