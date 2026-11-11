import struct
import unittest

from fm4_pvs import insert_fetch_constant, pass_groups


def model_with_draw_fixups():
    pm4 = struct.pack(">21I", 0x00054800, *([0] * 6),
                      0x000548BA, *([0] * 6), 0xC0032201, *([0] * 4),
                      0x80000000, 0x80000000)
    header = bytearray(0xE0)
    for offset, word in ((0x68, 0xD8), (0x70, 0xCC), (0x78, len(pm4) - 8),
                         (0x7C, 1), (0x90, 0xC8), (0xC0, 0x81000000 | ((len(pm4) - 8) // 4)),
                         (0xC8, 0x84000040)):
        struct.pack_into(">I", header, offset, word)
    header[-8:] = bytes.fromhex("1122334455667789")
    # The vertex fixup uses base 32 + 4 dwords, and the index fixup base 60 + 2 dwords.
    draw_fixups = struct.pack(">4I", 0x08000204, 0, 0x080003C0, 0)
    material = struct.pack(">8I", 1, 0, 1, len(header), len(pm4), len(draw_fixups), 0, 8)
    group = struct.pack(">8I", 2, 1, 3, 0x47000000, 0, len(header), len(pm4), len(draw_fixups))
    return b".fx" + material + group + struct.pack(">6I", 2, 0, 0, 0, 0, 0) * 20 + header + pm4 + draw_fixups


class FetchInsertion(unittest.TestCase):
    def test_relocates_vertex_and_index_fixups(self):
        model = model_with_draw_fixups()
        output = insert_fetch_constant(model, 7)
        self.assertIsNotNone(output)
        groups = pass_groups(output, 35)
        size_a, size_b, size_c = groups[0][3]
        self.assertEqual((size_b, size_c), (112, 16))
        start = groups[-1][1] + size_a
        self.assertEqual(struct.unpack_from(">I", output, start + 28)[0], 0x0005482A)
        self.assertEqual(struct.unpack_from(">4I", output, start + size_b),
                         (0x080003C4, 0, 0x08000580, 0))

    def test_refuses_unrecognized_draw_fixup(self):
        model = model_with_draw_fixups()
        self.assertIsNone(insert_fetch_constant(model[:-16] + b"\x0c" + model[-15:], 7))


if __name__ == "__main__":
    unittest.main()
