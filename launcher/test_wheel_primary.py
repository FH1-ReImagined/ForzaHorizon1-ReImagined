"""Which device is the wheel (#306): the setting matches the SDK's cvar (patch 0134), the launcher
orders the wheels as the driver does (wheel_device, then force feedback, then SDL's wheels, then
SDL's order; pedal, shifter and handbrake devices are no wheels unless named), and the Bindings
editor's "Use as the wheel" sets wheel_device (SDL virtual joysticks, no hardware). Runs alone from
the repository root too: python3 launcher/test_wheel_primary.py"""
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
from forza_launcher.config import Config
from forza_launcher.gamepad import DeviceInfo

try:
    from PySide6.QtWidgets import QApplication
    import sdl2
    from test_wheel_devices import VirtualDevice
except ImportError:
    QApplication = None

PATCH = Path(__file__).resolve().parent.parent / "patches" / "rexglue-sdk" / \
    "0134-wheel-primary-device.patch"


def settings(**values):
    out = schema.defaults()
    out.update(values)
    return out


def dev(name, vid, pid, instance, sdl_wheel=False, gamepad=False, guid=""):
    return DeviceInfo(instance, name, guid, vid, pid, gamepad, sdl_wheel)


# #306: vJoy (SimHub's pedals, handbrake and shifter) and a button box before the Fanatec base.
VJOY = dev("Shaul Eizikovich vJoy - Virtual Joystick", 0x1234, 0xBEAD, 10)
MAPLE = dev("LeafLabs Maple", 0x1EAF, 0x0024, 11)
FANATEC = dev("FANATEC Wheel", 0x0EB7, 0x0020, 12, sdl_wheel=True)
FANATEC_FFB = dev("FANATEC Wheel", 0x0EB7, 0x0020, 13, sdl_wheel=True)
PAD = dev("Xbox Controller", 0x045E, 0x028E, 14, gamepad=True)
DEVICES = [VJOY, MAPLE, FANATEC, FANATEC_FFB, PAD]


class SettingMatchesTheSdk(unittest.TestCase):
    def test_cvar(self):
        text = "\n".join(line[1:] for line in PATCH.read_text().splitlines() if line.startswith("+"))
        m = re.search(r'REXCVAR_DEFINE_STRING\(wheel_device, "([^"]*)",', text)
        self.assertIsNotNone(m)
        setting = schema.BY_KEY["wheel_device"]
        self.assertEqual((setting.default, setting.kind, setting.section), (m.group(1), "str", "Racing wheel"))
        keys = [s.key for s in schema.for_group("Controls")]
        self.assertEqual(keys[keys.index("wheel_mode") + 1], "wheel_device")


class Order(unittest.TestCase):
    def test_like_the_driver(self):
        on = settings(wheel_mode="on")
        self.assertEqual(b.wheel_order(DEVICES, on, ffb={13: True}), [3, 2, 0, 1],
                         "force feedback first, then SDL's wheels, then SDL's order")
        self.assertEqual(b.wheel_order(DEVICES, on), [2, 3, 0, 1], "without it: SDL's wheels")
        self.assertEqual(b.primary_wheel(DEVICES, settings()), 2, "auto mode: only the Fanatec")
        self.assertEqual(b.wheel_order(DEVICES, settings(wheel_mode="off")), [])
        self.assertIsNone(b.primary_wheel([VJOY, PAD], settings()))

    def test_pedal_shifter_and_handbrake_devices_are_no_wheels(self):
        name = VJOY.name
        for key in ("wheel_pedal_device", "wheel_shifter_device", "wheel_handbrake_device"):
            with self.subTest(key=key):
                order = b.wheel_order(DEVICES, settings(wheel_mode="on", **{key: name}))
                self.assertNotIn(0, order)
                self.assertEqual(order[0], 2)
        p1000 = dev("SIMAGIC P1000", 0x0483, 0x0525, 20)
        self.assertEqual(b.wheel_order([p1000, FANATEC], settings(wheel_mode="on")), [1],
                         "a known pedal set found by itself")

    def test_wheel_device(self):
        on = settings(wheel_mode="on")
        self.assertEqual(b.wheel_order(DEVICES, dict(on, wheel_device="LeafLabs Maple"),
                                       ffb={13: True}), [1, 3, 2, 0])
        self.assertEqual(b.primary_wheel(DEVICES, dict(on, wheel_device="1eaf:0024")), 1)
        self.assertEqual(b.primary_wheel(DEVICES, settings(wheel_device="vjoy")), 0,
                         "named, it is a wheel even in auto mode")
        self.assertEqual(b.primary_wheel(DEVICES, settings(wheel_device="vjoy",
                                                           wheel_pedal_device=VJOY.name)), 0,
                         "and even when it is named as the pedal device")
        self.assertEqual(b.wheel_order(DEVICES, dict(on, wheel_device="fanatec"), ffb={13: True})[:2],
                         [3, 2], "part of a name: of two matches the one with force feedback")
        self.assertEqual(b.primary_wheel(DEVICES, dict(on, wheel_device="none")), 2, "none: auto")


@unittest.skipIf(QApplication is None, "PySide6 or PySDL2 is not installed")
class Editor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.gamepad import Joysticks
        from forza_launcher.ui.controls_page import ControlsPage
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Config(Path(self.temp.name) / "launcher.json")
        self.cfg.settings["wheel_mode"] = "on"
        self.vjoy = VirtualDevice("vJoy - Virtual Joystick", 0x1234, 0xBEAD, 8, 32)
        self.wheel = VirtualDevice("FANATEC Wheel", 0x0EB7, 0x0020, 4, 20, 1,
                                   sdl2.SDL_JOYSTICK_TYPE_WHEEL)
        for d in (self.wheel, self.vjoy):
            self.addCleanup(d.detach)
        self.page = ControlsPage(self.cfg)
        self.page.bindings.start(Joysticks())
        self.addCleanup(self.page.bindings.stop)
        self.ed = self.page.bindings
        self.step()

    def step(self, n=2):
        for _ in range(n):
            self.ed.poll()
            self.app.processEvents()

    def label(self, device):
        return self.ed.picker.itemText(self.ed.picker.findData(device.instance))

    def choose(self, device):
        self.ed.picker.setCurrentIndex(self.ed.picker.findData(device.instance))
        self.step()

    def test_use_as_the_wheel(self):
        ed = self.ed
        self.assertEqual(ed.device.instance, self.wheel.instance, "the wheel is shown first")
        self.assertEqual(self.label(self.wheel), "FANATEC Wheel (wheel)")
        self.assertEqual(self.label(self.vjoy), "vJoy - Virtual Joystick (wheel, a second controller)")
        self.assertTrue(ed.use_wheel.isHidden(), "the wheel needs no button")
        self.choose(self.vjoy)
        self.assertFalse(ed.use_wheel.isHidden())
        self.assertEqual(ed.use_wheel.text(), "Use as the wheel")
        ed.use_wheel.click()
        self.assertEqual(self.cfg.settings["wheel_device"], "1234:bead")
        self.assertEqual(self.label(self.vjoy), "vJoy - Virtual Joystick (wheel)")
        self.assertEqual(self.label(self.wheel), "FANATEC Wheel (wheel, a second controller)")
        self.assertEqual(ed.use_wheel.text(), "Choose the wheel automatically")
        self.assertTrue(ed.rows, "the wheel's rows")
        ed.use_wheel.click()
        self.assertEqual(self.cfg.settings["wheel_device"], "")
        self.assertEqual(self.label(self.wheel), "FANATEC Wheel (wheel)")
        # As the pedal device the vJoy is no wheel; named as the wheel it is one again.
        self.cfg.settings["wheel_pedal_device"] = "vJoy - Virtual Joystick"
        ed.refresh()
        self.assertEqual(self.label(self.vjoy), "vJoy - Virtual Joystick (pedals, read with the wheel)")
        self.choose(self.vjoy)
        ed.use_wheel.click()
        self.assertEqual(self.label(self.vjoy), "vJoy - Virtual Joystick (wheel)")

    def test_live_view_reads_vjoy_pedals(self):
        # The reported setup: a wheel, and pedals (plus a handbrake and a shifter) through vJoy,
        # saved under SDL2's "vJoy Device" name. The graph follows the vJoy, not only the wheel.
        self.cfg.settings.update(wheel_pedal_device="vJoy Device",
                                 wheel_pedal_axes="throttle=1,brake=2,clutch=3",
                                 wheel_handbrake_device="vJoy Device", wheel_handbrake_axis=4,
                                 wheel_shifter_device="vJoy Device", wheel_shifter="sequential",
                                 wheel_shifter_device_buttons="up=5,down=6")
        self.choose(self.wheel)
        self.vjoy.axis(1, 21000)
        self.vjoy.axis(2, -21000)
        self.vjoy.axis(3, 1000)
        self.vjoy.axis(4, 30000)
        self.vjoy.button(5, True)
        self.step()
        live = self.ed.view.inputs
        self.assertEqual(live.axes[4:], (
            ("Throttle", 21000), ("Brake", -21000), ("Clutch", 1000), ("Handbrake", 30000)))
        self.assertEqual([lamp for lamp in live.buttons if lamp[0] in ("Up", "Down")],
                         [("Up", True), ("Down", False)])
        self.assertEqual(len(self.ed.view.snap.axes), 4)


if __name__ == "__main__":
    unittest.main()
