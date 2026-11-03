"""CI helper: download one of the launcher's pinned archives (checked against its sha256) and
unpack it, or copy a pinned single file (kind "file") to DEST. The same pins the launcher uses.
  ci_fetch.py NAME DEST [--strip N]"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forza_launcher import fetch, pins  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("name")
ap.add_argument("dest")
ap.add_argument("--strip", type=int, default=0)
a = ap.parse_args()
cache = Path(tempfile.gettempdir()) / "forza-ci-downloads"
try:
    archive = fetch.download(a.name, cache, lambda d, t: None)
except Exception as e:
    sys.exit(f"{a.name}: the pinned download failed: {e}\n"
             f"URL: {pins.DOWNLOADS[a.name][0]}\n"
             "If the publisher moved or replaced the file, update its URL and SHA-256 in "
             "launcher/forza_launcher/pins.py (the comment above the entry says how).")
if pins.DOWNLOADS[a.name][2] == "file":
    Path(a.dest).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, a.dest)
else:
    fetch.unpack(a.name, archive, Path(a.dest), strip=a.strip)
print(f"{a.name} -> {a.dest}")
