"""tools/fm4_caff.py on made-up textures (no game files)."""
import struct
import unittest
from unittest import mock

import fm4_caff as fc

DXT5 = 0x1A207F54   # DXT5, 8in16, tiled (as FM4's CAFF textures)
DXT1 = 0x1A207F52
COLOUR = (200, 100, 48, 255)


def caff(fmt=DXT5, kind=0, w=256, h=256, levels=1, gpu=b"", mips=0xFFFFFFFF, assets=1):
    """A CAFF texture file in the layout fm4_caff.caff_texture reads: the header, a block table
    of .data and .gpu, the texture asset at the start of .data, then the GPU data."""
    head = bytearray(400)
    struct.pack_into(">II", head, 24, assets, 1)
    struct.pack_into(">I", head, 72, len(head))
    head[77] = 2
    asset = bytearray(64)
    asset[:8] = b"texture\0"
    struct.pack_into(">II", asset, 24, fmt, kind)
    struct.pack_into(">HHII", asset, 36, w, h, 0, mips)
    asset[48] = levels
    for k, (name, size) in enumerate(((b".data", len(asset)), (b".gpu", len(gpu)))):
        head[80 + 40 * k:80 + 40 * k + len(name)] = name
        struct.pack_into(">I", head, 80 + 40 * k + 16, size)
    return bytes(head) + bytes(asset) + gpu


def solid_dxt5(size, colour=COLOUR):
    """size bytes of one solid DXT5 block repeated, as the GPU holds it (8in16): the same texels
    wherever the tiling puts a block."""
    blk = fc.encode_alpha([colour[3]] * 16) + fc.encode_colour([colour] * 16, False)
    return bytes(fc._swap16(blk)) * (size // 16)


def texels(fmt, w, h, tiled):
    planes = fc.decode_level(fmt, w, h, tiled)
    return {tuple(p[i] for p in planes) for i in range(w * h)}


def near(a, b, d=8):
    return all(abs(x - y) <= d for x, y in zip(a, b))


class Blocks(unittest.TestCase):
    def test_solid_colour_round_trip(self):
        for dxt1 in (True, False):
            px = fc.decode_colour(fc.encode_colour([COLOUR] * 16, dxt1), dxt1)
            self.assertTrue(all(near(p, COLOUR) for p in px))

    def test_gradient_keeps_its_ends(self):
        px = [(16 * i, 255 - 16 * i, 128, 255) for i in range(16)]
        out = fc.decode_colour(fc.encode_colour(px, False), False)
        self.assertTrue(near(out[0], px[0], 24) and near(out[15], px[15], 24))

    def test_dxt1_cut_out_stays_transparent(self):
        px = [COLOUR if i % 2 else (0, 0, 0, 0) for i in range(16)]
        out = fc.decode_colour(fc.encode_colour(px, True), True)
        self.assertEqual([p[3] for p in out], [255 if i % 2 else 0 for i in range(16)])

    def test_alpha_round_trip(self):
        al = [i * 17 for i in range(16)]
        out = fc.decode_alpha(fc.encode_alpha(al))
        self.assertTrue(all(abs(a - b) <= 20 for a, b in zip(al, out)))


class Tiling(unittest.TestCase):
    def test_each_block_its_own_place(self):
        for wb, hb, bpb in ((32, 32, 8), (64, 32, 16), (32, 64, 8)):
            offs = {fc.tiled_offset(x, y, wb, bpb) for y in range(hb) for x in range(wb)}
            self.assertEqual(len(offs), wb * hb)
            self.assertTrue(all(o % bpb == 0 and o < wb * hb * bpb for o in offs))

    def test_offset_table_matches(self):
        for wb, hb, bpb in ((32, 32, 8), (96, 64, 16), (75, 40, 16), (512, 64, 8)):
            want = [fc.tiled_offset(x, y, wb, bpb) for y in range(hb) for x in range(wb)]
            self.assertEqual(fc.block_offsets(wb, hb, bpb), want)

    def test_half_decode_is_the_box_filter(self):
        planes = [bytearray((x * 7 + y * 3 + c * 50) & 255 for y in range(128) for x in range(128)) for c in range(4)]
        tiled = fc.encode_level(DXT5, 128, 128, planes)
        full = fc.decode_level(DXT5, 128, 128, tiled)
        self.assertEqual(fc.decode_half(DXT5, 128, 128, tiled), fc.halve(full, 128, 128))

    def test_encode_then_decode(self):
        planes = [bytearray([c]) * (128 * 128) for c in COLOUR]
        tiled = fc.encode_level(DXT5, 128, 128, planes)
        self.assertEqual(len(tiled), 32 * 32 * 16)
        self.assertTrue(all(near(t, COLOUR) for t in texels(DXT5, 128, 128, tiled)))


class BixOfCaff(unittest.TestCase):
    def test_one_level_gets_mips(self):
        gpu = solid_dxt5(65536)                                      # 256 x 256 DXT5
        tex = fc.caff_texture(caff(gpu=gpu))
        self.assertEqual(tex[:4], (DXT5, 256, 256, 1))
        bix, base = fc.bix_of_caff(tex)
        self.assertEqual(struct.unpack_from(">6I", bix, 4), (256, 256, 2, DXT5, 65536 + 16384, 65536))
        self.assertEqual(bix[:4], b"BIX1")
        self.assertEqual(base, gpu)                                  # the base is kept as it is
        self.assertTrue(all(near(t, COLOUR) for t in texels(DXT5, 128, 128, bix[28:])))

    def test_a_long_chain_stops_at_128(self):
        tex = fc.caff_texture(caff(w=1024, h=512, gpu=solid_dxt5(1024 * 512)))
        bix, base = fc.bix_of_caff(tex)
        w, h, levels, fmt, total, size = struct.unpack_from(">6I", bix, 4)
        self.assertEqual((w, h, levels), (1024, 512, 3))             # 512 x 256, 256 x 128
        self.assertEqual(total - size, 512 * 256 + 256 * 128)
        self.assertEqual(len(bix) - 28, total - size)

    def test_not_a_power_of_two_is_resized(self):
        size = fc.base_size(DXT5, 300, 256)                          # 75 blocks wide, pitch 96
        tex = fc.caff_texture(caff(w=300, gpu=solid_dxt5(size)))
        bix, base = fc.bix_of_caff(tex)
        self.assertEqual(struct.unpack_from(">3I", bix, 4), (256, 256, 2))
        self.assertEqual(len(base), 65536)
        self.assertTrue(all(near(t, COLOUR) for t in texels(DXT5, 256, 256, base)))

    def test_a_large_texture_is_halved_first(self):
        with mock.patch.object(fc, "MAX_BASE_BYTES", 65536):
            for w, size in ((512, 512 * 512), (600, fc.base_size(DXT5, 600, 512))):   # 1 MB: over
                bix, base = fc.bix_of_caff(fc.caff_texture(caff(w=w, h=512, gpu=solid_dxt5(size))))
                self.assertEqual(struct.unpack_from(">4I", bix, 4), (256, 256, 2, DXT5))
                self.assertEqual(len(base), 65536)
                self.assertTrue(all(near(t, COLOUR) for t in texels(DXT5, 256, 256, base)))

    def test_mips_already_there_are_split(self):
        gpu = b"\1" * 32768 + b"\2" * 12288                          # 256 x 256 DXT1 and its mips
        bix, base = fc.bix_of_caff(fc.caff_texture(caff(fmt=DXT1, levels=9, gpu=gpu)))
        self.assertEqual(struct.unpack_from(">6I", bix, 4), (256, 256, 9, DXT1, 45056, 32768))
        self.assertEqual(bix[28:], b"\2" * 12288)
        self.assertEqual(base, b"\1" * 32768)

    def test_left_alone(self):
        gpu = b"\0" * 65536
        self.assertIsNone(fc.caff_texture(caff(kind=2, gpu=gpu)))            # a cube map
        self.assertIsNone(fc.caff_texture(caff(mips=0x1000, gpu=gpu)))       # mips apart
        self.assertIsNone(fc.caff_texture(caff(fmt=0x1A200186, gpu=gpu)))    # A8R8G8B8
        self.assertIsNone(fc.caff_texture(caff(assets=2, gpu=gpu)))
        self.assertIsNone(fc.caff_texture(b"not a CAFF file"))
        small = fc.caff_texture(caff(w=64, h=64, levels=7, gpu=b"\0" * 36864))
        self.assertIsNone(fc.bix_of_caff(small))          # its small mips' layout is not modelled
        tiny = fc.caff_texture(caff(w=128, h=128, gpu=solid_dxt5(16384)))
        self.assertIsNone(fc.bix_of_caff(tiny))           # no mip of 128 texels
        wrong = fc.caff_texture(caff(gpu=b"\0" * 70000))
        self.assertIsNone(fc.bix_of_caff(wrong))          # not one whole base level
        dxn = fc.caff_texture(caff(fmt=0x1A200171, gpu=gpu))
        self.assertIsNone(fc.bix_of_caff(dxn))            # one level, not DXT1/DXT5: no mips made


if __name__ == "__main__":
    unittest.main()
