"""Stage the renderer-bound native scene capability, independently of DLSS."""
import hashlib
import json
import os
import shutil
from pathlib import Path

NAME = "fh1-native-scene.json"


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stage(marker, source_renderer, staged_renderer, destination, *, pre_rpath_sha256=None):
    """Remove stale metadata; copy a verified marker or rebind after an RPATH rewrite.

    A caller that rewrites RPATH must provide the hash measured on the copied
    renderer before the rewrite; it must match the attested SDK renderer.
    """
    marker, source_renderer = Path(marker), Path(source_renderer)
    staged_renderer, destination = Path(staged_renderer), Path(destination)
    destination.unlink(missing_ok=True)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or type(data.get("api")) is not int or data["api"] != 1 or
                data.get("renderer_sha256") != _sha256(source_renderer)):
            return False
        if pre_rpath_sha256 is not None and pre_rpath_sha256 != data["renderer_sha256"]:
            return False
        staged_hash = _sha256(staged_renderer)
        if staged_hash == data["renderer_sha256"]:
            shutil.copy2(marker, destination)
        elif pre_rpath_sha256 == data["renderer_sha256"]:
            temp = destination.with_name("." + destination.name + ".new")
            try:
                temp.write_text(json.dumps({"api": 1, "renderer_sha256": staged_hash}) + "\n",
                                encoding="utf-8")
                os.replace(temp, destination)
            finally:
                temp.unlink(missing_ok=True)
        else:
            return False
        return True
    except (OSError, ValueError, TypeError):
        destination.unlink(missing_ok=True)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("marker")
    parser.add_argument("source_renderer")
    parser.add_argument("staged_renderer")
    parser.add_argument("destination")
    parser.add_argument("--pre-rpath-sha256")
    args = parser.parse_args()
    stage(args.marker, args.source_renderer, args.staged_renderer, args.destination,
          pre_rpath_sha256=args.pre_rpath_sha256)
