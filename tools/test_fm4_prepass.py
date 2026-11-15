"""tools/fm4_pvs.py's depth pre-pass fix (#827): FM4's group 0 command buffers no longer leave
their 256 x 256 window scissor in FH1's depth pre-pass. Synthetic model files (the builder of
test_fm4_decals.py; no game files)."""
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_pvs  # noqa: E402
from test_fm4_decals import NOP, ROAD_DEPTH, buffer, draw, model, parts, words  # noqa: E402

SCISSOR = [0x00022080, 0, 0, 0x01000100]   # PA_SC_WINDOW_OFFSET, _SCISSOR_TL, _SCISSOR_BR: 256 x 256
TAIL = [0x00000000, 0]                     # FM4's buffers end with a write of register 0


def road(group0=None, others=True):
    """A road model: FM4's depth pass in group 0 (two draws, then the scissor write), and its
    colour passes."""
    if group0 is None:
        group0 = buffer(draw(ROAD_DEPTH), draw(ROAD_DEPTH), nops=0) + SCISSOR + TAIL
    groups = {0: group0}
    if others:
        groups.update({3: buffer(draw(ROAD_DEPTH)), 12: buffer(draw(ROAD_DEPTH), nops=3)})
    return model(groups)


class PrepassScissor(unittest.TestCase):
    def test_the_scissor_write_becomes_nops(self):
        data = road()
        out = fm4_pvs.prepass_scissor(data)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), len(data))
        before, tail0 = parts(data)
        after, tail = parts(out)
        self.assertEqual(tail, tail0)
        for g in before:
            if g != 0:
                self.assertEqual(after[g], before[g])     # the colour passes keep everything
        self.assertEqual(after[0][:2] + after[0][3:], before[0][:2] + before[0][3:])
        old, new = words(before[0][2]), words(after[0][2])
        at = len(old) - len(SCISSOR) - len(TAIL)
        self.assertEqual(old[at:at + 4], SCISSOR)
        self.assertEqual(new[at:at + 4], [NOP] * 4)
        self.assertEqual(new[:at] + new[at + 4:], old[:at] + old[at + 4:])
        self.assertEqual(fm4_pvs._pm4_packets(after[0][2]) is not None, True)   # it still walks
        self.assertIsNone(fm4_pvs.prepass_scissor(out))   # nothing left to do

    def test_only_a_group_0_buffer_is_changed(self):
        # a model with no group 0 (most of FM4's), and one whose group 0 never writes the scissor
        self.assertIsNone(fm4_pvs.prepass_scissor(model({3: buffer(draw(ROAD_DEPTH)) + SCISSOR,
                                                          12: buffer(draw(ROAD_DEPTH)) + SCISSOR})))
        self.assertIsNone(fm4_pvs.prepass_scissor(road(buffer(draw(ROAD_DEPTH)))))
        self.assertIsNone(fm4_pvs.prepass_scissor(b"no material here"))

    def test_the_scissor_registers_alone(self):
        # the two scissor registers without the window offset, and every value to one of them
        for packet in ([0x00012081, 0, 0x01000100], [0x0001A082, 0x01000100, 0x01000100]):
            data = road(buffer(draw(ROAD_DEPTH), nops=0) + packet + TAIL)
            out = fm4_pvs.prepass_scissor(data)
            self.assertIsNotNone(out, packet)
            new = words(parts(out)[0][0][2])
            self.assertNotIn(0x01000100, new)
            self.assertEqual(new.count(NOP), len(packet))
        # the window offset alone is not the scissor: left
        self.assertIsNone(fm4_pvs.prepass_scissor(road(buffer(draw(ROAD_DEPTH), nops=0) + [0x00002080, 0] + TAIL)))

    def test_a_write_the_scan_does_not_read_is_left(self):
        d = buffer(draw(ROAD_DEPTH), nops=0)
        for what, packet in (
                ("other registers with them", [0x0003207F, 0, 0, 0, 0x01000100]),
                ("past them", [0x00022081, 0, 0x01000100, 0]),
                ("SET_CONSTANT", [0xC0022D00, 0x00040081, 0, 0x01000100]),
                ("SET_CONSTANT2", [0xC0015500, 0x00002082, 0x01000100]),
                ("INDIRECT_BUFFER", [0xC0013F00, 0x01000000, 16])):
            self.assertIsNone(fm4_pvs.prepass_scissor(road(d + packet + TAIL)), what)
        # one such packet beside a plain one: the file is left whole
        self.assertIsNone(fm4_pvs.prepass_scissor(road(d + SCISSOR + [0xC0015500, 0x00002082, 0x01000100] + TAIL)))

    def test_a_scissor_the_game_fills_is_left(self):
        # a scissor fixup (record type 6) of the group, as FM4's colour passes have: the game
        # writes the scissor into the buffer itself
        data = bytearray(road())
        g = fm4_pvs.pass_groups(bytes(data), data.rfind(b".fx") + 35)
        first = g[0][0] + 8
        struct.pack_into(">I", data, first, (struct.unpack_from(">I", data, first)[0] & ~0xF) | 6)
        self.assertIsNone(fm4_pvs.prepass_scissor(bytes(data)))

    def test_a_place_the_game_patches_in_the_packet_is_left(self):
        # a patch place of the group's table, or a buffer fixup, on a word of the packet
        data = road()
        p = data.rfind(b".fx") + 3
        head = struct.unpack_from(">8I", data, p)
        g = fm4_pvs.pass_groups(data, p + 32)
        a = g[-1][1]
        n_words = g[0][3][1] // 4
        value = n_words - len(TAIL) - 1                       # the scissor's last value
        patched = bytearray(data)
        entry = struct.unpack_from(">I", patched, a + 0xC8)[0]
        struct.pack_into(">I", patched, a + 0xC8, (entry & ~(0xFFFFF << 6)) | (value << 6))
        self.assertIsNone(fm4_pvs.prepass_scissor(bytes(patched)))
        fixed = bytearray(data)
        c = a + head[3] + head[4]
        word = struct.unpack_from(">I", fixed, c)[0]
        struct.pack_into(">I", fixed, c, (word & ~(0xFFFFF << 6)) | (value << 6))
        self.assertIsNone(fm4_pvs.prepass_scissor(bytes(fixed)))
        self.assertIsNotNone(fm4_pvs.prepass_scissor(data))

    def test_a_folder_of_models(self):
        with tempfile.TemporaryDirectory() as d:
            files = {"T.00001.rmb.bin": road(), "T.00002.rmb.bin": model({12: buffer(draw(ROAD_DEPTH))}),
                     "T.00003.RMB.BIN": road(), "other.bin": road()}
            for name, data in files.items():
                with open(os.path.join(d, name), "wb") as f:
                    f.write(data)
            with mock.patch.object(fm4_pvs, "PREPASS_SCISSOR", False):
                self.assertEqual(fm4_pvs.fix_prepass_scissors(d), 0)
            self.assertEqual(fm4_pvs.fix_prepass_scissors(d), 2)

            def read(name):
                with open(os.path.join(d, name), "rb") as f:
                    return f.read()
            self.assertEqual(read("T.00001.rmb.bin"), fm4_pvs.prepass_scissor(files["T.00001.rmb.bin"]))
            self.assertEqual(read("T.00002.rmb.bin"), files["T.00002.rmb.bin"])
            self.assertEqual(read("other.bin"), files["other.bin"])       # not a model file
            self.assertEqual(fm4_pvs.fix_prepass_scissors(d), 0)          # done once


if __name__ == "__main__":
    unittest.main()
