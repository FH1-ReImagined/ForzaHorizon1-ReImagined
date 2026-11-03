import struct
import unittest
from fh2_cars_labels import LABELS, X, Y, patch_bgf


def fixture():
    data = bytearray(b"untouched-prefix")
    writable = set()
    for obj, name, parent, node, title in LABELS:
        data.extend(struct.pack(">III", name, parent, node) + bytes((4, 1)) + struct.pack(">I", 48))
        for prop, value in ((X, -152.0 if title else -163.0), (Y, 70.0 if title else -73.0)):
            writable.update(range(len(data) + 4, len(data) + 8))
            data.extend(struct.pack(">If", prop | (3 << 27), value))
        data.extend(bytes(46 * 8))
        for prop, value in ((X, -163.0), (Y, 71.0 if title else -73.0)):
            for _ in range(2):
                data.extend(struct.pack(">III", obj, prop, 0x01000002))
                for time in (0, 100):
                    controls = value if time == 0 else -1.0
                    for offset in ((4, 12, 20) if time == 0 else (4,)):
                        writable.update(range(len(data) + offset, len(data) + offset + 4))
                    data.extend(struct.pack(">IfIfIf", time, value, 0, controls, 0, controls))
        data.extend(b"preserve-other-curves")
    return bytes(data), writable


class CarsLabelsTests(unittest.TestCase):
    def test_changes_only_authorized_scalar_values(self):
        data, writable = fixture()
        result = patch_bgf(data)
        self.assertEqual(len(result), len(data))
        changes = {i for i, (a, b) in enumerate(zip(data, result)) if a != b}
        self.assertTrue(changes)
        self.assertLessEqual(changes, writable)
        self.assertEqual(data, fixture()[0])
        for obj, _, _, _, title in LABELS:
            for prop, value in ((X, -162.0), (Y, 38.0 if title else -38.0)):
                marker = struct.pack(">III", obj, prop, 0x01000002)
                at = result.index(marker) + 12
                self.assertEqual(struct.unpack_from(">IfIfIf", result, at), (0, value, 0, value, 0, value))
                self.assertEqual(struct.unpack_from(">IfIfIf", result, at + 24), (100, value, 0, -1.0, 0, -1.0))

    def test_reject_already_patched(self):
        with self.assertRaises(ValueError):
            patch_bgf(patch_bgf(fixture()[0]))

    def test_reject_extra_curve(self):
        data, _ = fixture()
        marker = struct.pack(">III", LABELS[0][0], Y, 0x01000002)
        at = data.index(marker)
        with self.assertRaises(ValueError):
            patch_bgf(data + data[at:at + 60])

    def test_reject_wrong_key_time_or_endpoint(self):
        for offset, value in ((12, 1), (16, 0)):
            data = bytearray(fixture()[0])
            at = data.index(struct.pack(">III", LABELS[0][0], Y, 0x01000002))
            struct.pack_into(">I", data, at + offset, value)
            with self.assertRaises(ValueError):
                patch_bgf(data)

    def test_reject_changed_identity_or_base_property(self):
        for which in (0, 1):
            data = bytearray(fixture()[0])
            obj, name, parent, node, _ = LABELS[0]
            at = data.index(struct.pack(">III", name, parent, node))
            struct.pack_into(">I", data, at + (8 if which == 0 else 22), 0)
            with self.assertRaises(ValueError):
                patch_bgf(data)


if __name__ == "__main__":
    unittest.main()
