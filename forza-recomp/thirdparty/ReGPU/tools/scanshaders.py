"""Finds every shader object in the game data without running the game.

scanshaders.py <game dir> [out.json]

Shader objects embed an IM_LOAD_IMMEDIATE packet: big-endian header 0xC0nn2B00
(type 3, opcode 0x2B), then the type (0 vertex, 1 pixel), the size in dwords,
and the microcode. Scans loose files and the contents of every zip.
"""
import json
import os
import struct
import sys
import zipfile
from collections import Counter


def find(data, source, found):
    n = len(data)
    i = 0
    # header bytes: C0..C3, count low, 2B, 00
    while True:
        i = data.find(b'\x2b\x00', i)
        if i < 0:
            break
        start = i - 2
        i += 1
        if start < 0 or start % 4 or (data[start] & 0xFC) != 0xC0:
            continue
        count = ((data[start] & 0x3F) << 8) | data[start + 1]
        if start + 12 > n:
            continue
        kind, size = struct.unpack_from('>II', data, start + 4)
        if kind > 1 or (size >> 16) or size < 2 or size > 0x2000 or count != size + 1:
            continue
        end = start + 12 + size * 4
        if end > n:
            continue
        code = data[start + 12:end]
        key = (kind, code)
        if key not in found:
            found[key] = source


def main():
    root = sys.argv[1]
    found = {}
    files = zips = entries = 0
    for dirpath, _, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            files += 1
            try:
                if name.lower().endswith('.zip'):
                    zips += 1
                    with zipfile.ZipFile(path) as z:
                        for info in z.infolist():
                            if info.file_size == 0 or info.file_size > 200 << 20:
                                continue
                            entries += 1
                            find(z.read(info), '%s!%s' % (os.path.relpath(path, root), info.filename), found)
                elif os.path.getsize(path) < 400 << 20 and not name.lower().endswith(('.bik', '.fsb', '.xma', '.dds')):
                    with open(path, 'rb') as f:
                        find(f.read(), os.path.relpath(path, root), found)
            except Exception as e:  # unreadable or odd zip: skip
                print('skip', path, e, file=sys.stderr)
    kinds = Counter(k for k, _ in found)
    print('files', files, 'zips', zips, 'zip entries', entries)
    print('distinct shaders', len(found), 'vertex', kinds[0], 'pixel', kinds[1])
    sources = Counter(s.split('!')[0].split(os.sep)[-1] if '!' in s else 'loose' for s in found.values())
    print(sources.most_common(8))
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'w') as f:
            json.dump([{'type': k, 'words': len(c) // 4, 'source': found[(k, c)], 'hex': c.hex()} for k, c in found], f)


main()
