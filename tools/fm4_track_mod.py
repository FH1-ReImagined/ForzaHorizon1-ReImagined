#!/usr/bin/env python3
"""Make a mod that puts a Forza Motorsport 4 circuit into Forza Horizon 1, from the player's
own copies of both games (#648, "The Festival": FM4 tracks inside FH1).

Usage:
  tools/fm4_track_mod.py FM4_TRACK_DIR FH1_UPDATE_MEDIA_ZIP OUT_MOD_DIR
      [--fm4-db FM4_GAMEDB] [--free-roam TRACK_ID] [--layout N] [--fh1-disc DIR] [--loose DIR]

  FM4_TRACK_DIR         the circuit's folder on the FM4 disc, e.g. <disc1>/Media/tracks/Suzuka
  FH1_UPDATE_MEDIA_ZIP  FH1's Title Update 4 media.zip (its db/gamedb.slt is the base)
  OUT_MOD_DIR           the mod folder to write (e.g. <user data>/mods/FM4Suzuka); replaced
  --fm4-db              FM4's media/db/gamedb.slt: copies the circuit's Environments row (FH1
                        still has FM4's Suzuka Tracks rows, 37-39, but not their environment)
  --free-roam ID        dev test: the FH1 Tracks row the game loads for free roam (317 in TU4)
                        is pointed at the circuit, so SINGLE PLAYER loads it in place of Colorado
  --layout N            the circuit's ribbon (FM4 layout) for --free-roam, default 0
  --fh1-disc DIR        FH1's disc folder (with media/gamemodes.zip): the FH1-only files of a
                        track are added (empty forms, Colorado's ambience settings) and, with
                        --free-roam, FH1's Colorado game modes (cutscenes, activities...) as the
                        circuit's own, which the open world needs to load at all
  --loose DIR           with --fh1-disc: where to write the files that must be loose files of
                        the game folder (the track's sound banks, FMOD opens them by path)

What the mod holds, at the paths FH1 builds for a track (docs/modding.md):
  media/tracks/<name>/bin/...      the members of the circuit's bin.zip (FH1 mounts a track's
                                   bin.zip at tracks\\<name>\\bin\\), decompressed
  media/tracks/<name>/...          the loose files: TrackSettings*.xml, <name>_ribbonNN.sh,
                                   Ribbon_NN/ (PVS, collision, crowd, cameras, CollObjs.xml)
  media/db/gamedb.slt              the update's database with the environment row added
  media/gamemodes/<name>/...       (--fh1-disc) stand-ins: FH1's gamemodes/Colorado files

The files are the player's own and stay on their PC: never share the mod.

Next: tools/fm4_pvs.py converts the circuit's visibility data and writes the track archive
FH1 streams it from; see there for running it.
"""
import argparse
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
UNXMEM = os.path.join(HERE, "unxmem")


def member_data(zip_path, zf, info, fh, tmp):
    """The member's decompressed bytes. FH1's archives have no local headers: the 0x1123 extra
    field of the central directory gives the data offset; FM4's are plain zips."""
    off, ex, p = None, info.extra, 0
    while p + 4 <= len(ex):
        tag, ln = struct.unpack("<HH", ex[p:p + 4])
        if tag == 0x1123 and ln >= 4:
            off = struct.unpack("<I", ex[p + 4:p + 8])[0]
        p += 4 + ln
    if off is None:
        fh.seek(info.header_offset)
        h = fh.read(30)
        n, m = struct.unpack("<HH", h[26:30])
        off = info.header_offset + 30 + n + m
    fh.seek(off)
    raw = fh.read(info.compress_size)
    if info.compress_type == 0:
        return raw
    if info.compress_type != 21:
        return zf.read(info)
    if not os.path.exists(UNXMEM):
        raise SystemExit("tools/unxmem is missing: build it from tools/unxmem.c")
    src, dst = os.path.join(tmp, "m.lzx"), os.path.join(tmp, "m.out")
    with open(src, "wb") as f:
        f.write(raw)
    subprocess.run([UNXMEM, src, dst, str(info.file_size)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(dst, "rb") as f:
        data = f.read()
    if len(data) != info.file_size:
        raise SystemExit(f"{info.filename}: decoded {len(data)} bytes of {info.file_size}")
    return data


def find_member(zip_path, name):
    zf = zipfile.ZipFile(zip_path)
    for info in zf.infolist():
        if info.filename.replace("\\", "/").lower() == name:
            return zf, info
    raise SystemExit(f"{zip_path} has no {name}")


def write_db(update_zip, out_db, track, fm4_db, free_roam, layout, tmp):
    zf, info = find_member(update_zip, "db/gamedb.slt")
    with open(update_zip, "rb") as fh:
        data = member_data(update_zip, zf, info, fh, tmp)
    os.makedirs(os.path.dirname(out_db), exist_ok=True)
    with open(out_db, "wb") as f:
        f.write(data)
    db = sqlite3.connect(out_db)
    cols = [r[1] for r in db.execute("PRAGMA table_info(Environments)")]
    env = None
    if fm4_db:
        src = sqlite3.connect(f"file:{fm4_db}?mode=ro", uri=True)
        src_cols = [r[1] for r in src.execute("PRAGMA table_info(Environments)")]
        row = src.execute("SELECT * FROM Environments WHERE MediaName LIKE ?", (track,)).fetchone()
        if row:
            env = dict(zip(src_cols, row))
        src.close()
    if env is None:
        # FH1 keeps the circuit's Tracks rows; their EnvironmentId names the FM4 environment.
        r = db.execute("SELECT EnvironmentId FROM Tracks WHERE MediaName LIKE ? LIMIT 1",
                       (track,)).fetchone()
        if not r:
            raise SystemExit(f"no Environments row for {track}: pass --fm4-db")
        env = {"Id": r[0], "MediaName": track, "ThumbImageName": track, "CountryId": 10}
    env["ShowOnMap"] = 0  # not a pin on FH1's map
    taken = db.execute("SELECT MediaName FROM Environments WHERE Id=?", (env["Id"],)).fetchone()
    if taken and taken[0].lower() != track.lower():
        raise SystemExit(f"FH1's environment {env['Id']} is {taken[0]}: FM4's id clashes")
    names = [c for c in cols if c in env]
    db.execute(f"INSERT OR REPLACE INTO Environments ({','.join(names)}) VALUES "
               f"({','.join('?' * len(names))})", [env[c] for c in names])
    print(f"Environments {env['Id']} = {track}")
    if free_roam is not None:
        db.execute("UPDATE Tracks SET MediaName=?, EnvironmentId=?, RibbonIndex=? WHERE id=?",
                   (track, env["Id"], layout, free_roam))
        print(f"Tracks {free_roam} (free roam) -> {track} ribbon {layout}")
    db.commit()
    db.close()


def copy_gamemodes(fh1_disc, update_zip, out_dir, tmp):
    """FH1's GameModes\\Colorado\\ (the disc's, then the title update's copies) as the
    circuit's: the free-roam game builds GameModes\\<environment>\\... paths and faults on
    a missing cutscenes_default_freeroam.xml."""
    count = 0
    for zip_path, prefix in ((os.path.join(fh1_disc, "media", "gamemodes.zip"), "colorado/"),
                             (update_zip, "gamemodes/colorado/")):
        zf = zipfile.ZipFile(zip_path)
        with open(zip_path, "rb") as fh:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if info.is_dir() or not name.lower().startswith(prefix):
                    continue
                dst = os.path.join(out_dir, *name[len(prefix):].lower().split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(member_data(zip_path, zf, info, fh, tmp))
                count += 1
    print(f"{count} game mode files from Colorado")


# Files every FH1 track has and FM4's circuits lack, written empty (FH1's own UI levels ship
# the same empty forms): the open world reads them without checking that they opened.
EMPTY_TRACK_FILES = {
    "PhysicsDefinitions.bin": b"\0\0\0\0",
    "PVSZoneSpeeds.dat": struct.pack(">f", 120.0),
    "SmashableObjectTypes.xml": b"<SmashableObjectTypes>\n</SmashableObjectTypes>\n",
}
EMPTY_RIBBON_FILES = {
    "ParticleEmitters.xml": b'<?xml version="1.0" encoding="utf-8"?>\n<ParticleEmitters />\n',
    "SmashableEffects.xml": b'<?xml version="1.0" encoding="utf-8"?>\n<Track />\n',
    "GameObjs.xml": b'<?xml version="1.0" ?>\n<GameObjs>\n</GameObjs>\n',
    "PostProcessingZones.xml":
        b'<?xml version="1.0" encoding="utf-8" ?>\n<PostProcessingZones>\n</PostProcessingZones>\n',
}


def add_stand_ins(root, track, fh1_disc, loose):
    """The FH1-only files of a track: empty ones, and Colorado's audio (ambience, reverb and
    sound bank settings, the ambience sound banks) under the circuit's name."""
    count = 0
    def put(path, data):
        nonlocal count
        if os.path.exists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        count += 1
    for name, data in EMPTY_TRACK_FILES.items():
        put(os.path.join(root, name.lower()), data)
    ribbons = sorted(d for d in os.listdir(root) if d.lower().startswith("ribbon_"))
    for ribbon in ribbons:
        for name, data in EMPTY_RIBBON_FILES.items():
            put(os.path.join(root, ribbon, name.lower()), data)
    colorado = os.path.join(fh1_disc, "media", "tracks", "colorado")
    for kind in ("ambience", "reverb", "soundbank_lookup"):
        src = os.path.join(colorado, f"colorado_{kind}.xml")
        if os.path.exists(src):
            with open(src, "rb") as f:
                put(os.path.join(root, f"{track}_{kind}.xml".lower()), f.read())
    # The ambience names Colorado's sound banks, which FMOD opens by path from the track's
    # audio folder. They cannot be mod files: a file of the title update's archive makes the
    # game open update:\media\audio\... as a loose file, and its failure is the game's
    # fatal "title update file" error. They go to a folder of loose files instead, to lay
    # over the game folder (--loose).
    if not loose:
        print(f"{count} stand-in files (no --loose: the track's sound banks are missing)")
        return
    audio_src = os.path.join(fh1_disc, "media", "audio", "tracks", "colorado")
    audio_dst = os.path.join(loose, "media", "audio", "tracks", track)
    for n in os.listdir(audio_src):
        src = os.path.join(audio_src, n)
        if os.path.isfile(src):
            with open(src, "rb") as f:
                put(os.path.join(audio_dst, n.lower()), f.read())
    ribbon_xml = os.path.join(audio_src, "Ribbon_00", "ColoradoRibbon_00.xml")
    for ribbon in ribbons:
        with open(ribbon_xml, "rb") as f:
            put(os.path.join(audio_dst, ribbon.lower(), f"{track}{ribbon}.xml".lower()), f.read())
    print(f"{count} stand-in files")


def ribbon_as_zero(root, track, layout):
    """A circuit with no Ribbon_00 (Le Mans: 01 and 02): FH1 crashed loading its free roam
    on ribbon 1 (in the collision manager, right after the collision index). The layout's
    ribbon becomes ribbon 0: Ribbon_NN to Ribbon_00, its files' _NN to _00, the PVS's ribbon
    field to 0, <track>_ribbonNN.sh to ribbon00. Gives the layout to use (0 if moved)."""
    names = {d.lower(): d for d in os.listdir(root)}
    if "ribbon_00" in names:
        return layout
    nums = sorted(int(d[7:]) for d in names if d.startswith("ribbon_") and d[7:].isdigit())
    if not nums:
        return layout
    n = layout if layout in nums else nums[0]
    src = os.path.join(root, names[f"ribbon_{n:02d}"])
    dst = os.path.join(root, "Ribbon_00")
    os.rename(src, dst)
    for name in os.listdir(dst):
        new = name.replace(f"_{n:02d}.", "_00.")
        if new != name:
            os.rename(os.path.join(dst, name), os.path.join(dst, new))
        if new.lower().endswith(".pvs"):
            p = os.path.join(dst, new)
            d = bytearray(open(p, "rb").read())
            struct.pack_into(">H", d, 16, 0)  # FPVS, version, header (8 bytes), ribbon u16
            open(p, "wb").write(d)
    for name in list(os.listdir(root)):
        if name.lower() == f"{track.lower()}_ribbon{n:02d}.sh":
            os.rename(os.path.join(root, name), os.path.join(root, name[:-5] + "00.sh"))
    print(f"Ribbon_{n:02d} moved to Ribbon_00 (the circuit has no ribbon 0)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("track_dir")
    ap.add_argument("update_zip")
    ap.add_argument("out")
    ap.add_argument("--fm4-db")
    ap.add_argument("--free-roam", type=int)
    ap.add_argument("--layout", type=int, default=0)
    ap.add_argument("--fh1-disc")
    ap.add_argument("--loose")
    a = ap.parse_args()
    track = os.path.basename(os.path.normpath(a.track_dir))
    lower = track.lower()
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    root = os.path.join(a.out, "media", "tracks", lower)
    tmp = tempfile.mkdtemp(prefix="fm4track-")
    try:
        files = 0
        for dirpath, _, names in os.walk(a.track_dir):
            rel = os.path.relpath(dirpath, a.track_dir)
            for n in names:
                if rel == "." and n.lower() == "bin.zip":
                    continue
                dst = os.path.normpath(os.path.join(root, rel, n))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(dirpath, n), dst)
                files += 1
        print(f"{files} loose files")
        layout = a.layout
        if a.free_roam is not None:
            layout = ribbon_as_zero(root, track, layout)
        bz = os.path.join(a.track_dir, "bin.zip")
        zf = zipfile.ZipFile(bz)
        members = 0
        with open(bz, "rb") as fh:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                dst = os.path.join(root, "bin", *info.filename.replace("\\", "/").split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(member_data(bz, zf, info, fh, tmp))
                members += 1
        print(f"{members} bin.zip members")
        write_db(a.update_zip, os.path.join(a.out, "media", "db", "gamedb.slt"), track,
                 a.fm4_db, a.free_roam, layout, tmp)
        if a.fh1_disc:
            add_stand_ins(root, track, a.fh1_disc, a.loose)
        if a.fh1_disc and a.free_roam is not None:
            copy_gamemodes(a.fh1_disc, a.update_zip,
                           os.path.join(a.out, "media", "gamemodes", lower), tmp)
        with open(os.path.join(a.out, "mod.toml"), "w") as f:
            f.write(f'name = "FM4 {track} (from your own FM4 disc)"\n'
                    'description = "Made by tools/fm4_track_mod.py. Never share it."\n')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
