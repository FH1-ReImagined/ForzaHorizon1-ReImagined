#!/usr/bin/env python3
"""Rasterize the attributed SVG action icons into native XDS textures.
Developer-only dependencies: Pillow and resvg-py. Players use prebuilt XDS.
SVGs retain upstream attribution; see native_menu/icons/ATTRIBUTION.md.
"""
from pathlib import Path
import io
import struct
from PIL import Image
import resvg_py
from xdc_decode import tiled_offset


def main():
    root = Path(__file__).parent / "native_menu/icons"
    for source in sorted(root.glob("*.svg")):
        image = Image.open(io.BytesIO(resvg_py.svg_to_bytes(
            svg_string=source.read_text(), width=1024, skip_system_fonts=True))).convert("RGBA")
        image.thumbnail((896, 896), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (1024, 1024))
        canvas.alpha_composite(image, ((1024-image.width)//2, (1024-image.height)//2))
        image = canvas.resize((256, 256), Image.Resampling.LANCZOS)
        rgba = image.tobytes()
        pixels = bytearray(256*256*4)
        for y in range(256):
            for x in range(256):
                offset = tiled_offset(x, y, 256)
                pixels[offset:offset+4] = bytes((rgba[(y*256+x)*4+3], 255, 255, 255))
        header = struct.pack(">13I", 3, 1, 0, 0, 0, 0xffff0000, 0xffff0000,
                             0x820000fe, 0xb2, 0x1fe0ff, 0xc14, 0, 0xa00)
        source.with_suffix(".xds").write_bytes(header+pixels)
        print(source.stem)


if __name__ == "__main__":
    main()
