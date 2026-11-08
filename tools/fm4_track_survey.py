#!/usr/bin/env python3
"""Survey FM4's circuits for the track converter (#648): per circuit and layout, the PVS
version, instances, models, texture records, the atlas-only records (an atlas tile, flag 0x8,
without a texture file of its own: FH1 gives these no texture, Laguna Seca's road drew black),
the road pieces (road/rdline/rdedg shaders) that use one, the models that use one, FM4's AI line
(for the spawn), the static pack, and the shader names Suzuka's conversion has not seen.

Usage: tools/fm4_track_survey.py   (paths below: the FM4 disc and Suzuka's converted mod)
Prints one '|'-separated row per layout.
"""
import os, sys, struct, zipfile, glob, re, collections
sys.path.insert(0, '/var/home/dyotson/Documents/RecompiledForzaHorizon/scratchpad/festival/trk/tools')
import fm4_pvs as m
D1 = '/var/home/dyotson/Documents/RecompiledForzaMotorsport4/extracted/disc1/Media'
known = set()
for root in ('/var/home/dyotson/Documents/RecompiledForzaMotorsport4/trk-spike/mods/FM4SuzukaFinal/media/tracks/suzuka/bin/shaders/track',):
    known |= {n[:-6].lower() for n in os.listdir(root)}
rows = []
for t in sorted(os.listdir(D1 + '/tracks')):
    p = os.path.join(D1, 'tracks', t)
    if not os.path.isdir(p) or t.lower().startswith(('homespace', 'ui')): continue
    try:
        z = zipfile.ZipFile(os.path.join(p, 'bin.zip'))
        names = z.namelist()
    except Exception as e:
        rows.append((t, 'no bin.zip: %s' % e)); continue
    own = {int(n.split('_0x')[1][:8], 16) for n in names if '_0x' in n and n.lower().endswith('.bix') and '_b.bix' not in n.lower()}
    stx = any(n.lower().endswith('.stx.bin') for n in names)
    shaders = {os.path.basename(n)[:-6].lower() for n in names if n.lower().endswith('.fxobj')}
    ribbons = sorted(d for d in os.listdir(p) if d.lower().startswith('ribbon_'))
    for rb in ribbons:
        pv = glob.glob(os.path.join(p, rb, '*.pvs'))
        if not pv: continue
        d = open(pv[0], 'rb').read()
        ver = struct.unpack_from('>I', d, 4)[0]
        if ver != 27:
            rows.append((t, rb, 'PVS version %d' % ver)); continue
        F = m.read_fm4(d)
        T = F['textures']
        atlas_only = [i for i, r in enumerate(T) if (r[6] & 0x8) and not (r[6] & 0x4) and r[0] not in own]
        ao = set(atlas_only)
        road = [M for M in F['models'] if any(F['shaders'][s].split('\\')[-1].lower().startswith(('road', 'rdline', 'rdedg')) for s in M['shaders'])]
        road_bad = sum(1 for M in road if any((t2 & 0xFFFFFF) in ao for t2 in M['textures']))
        allm = len(F['models']); all_bad = sum(1 for M in F['models'] if any((t2 & 0xFFFFFF) in ao for t2 in M['textures']))
        geo = os.path.exists(os.path.join(D1, 'AI', 'Tracks', '%s_%s.geo' % (t.lower(), rb[-2:])))
        unknown = sorted({F['shaders'][s].split('\\')[-1].lower() for M in F['models'] for s in M['shaders']} - known)
        rows.append((t, rb, len(F['instances']), allm, len(T), len(atlas_only), road_bad, len(road), all_bad, geo, stx, len(unknown), ','.join(unknown[:4])))
for r in rows: print('|'.join(str(x) for x in r))
