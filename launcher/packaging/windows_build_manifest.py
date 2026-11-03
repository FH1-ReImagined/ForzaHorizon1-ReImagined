"""Record the final Windows package's file hashes and public build provenance.

Run after staging/signing payload files and before compiling the installer. This
does not scan files, attest that they are safe, or submit anything to a vendor.
"""
import argparse
import hashlib
from importlib import metadata
import json
import platform
from pathlib import Path
import re


MANIFEST = "windows-build-manifest.json"
PACKAGES = ("pyinstaller", "pyinstaller-hooks-contrib", "PySide6-Essentials",
            "shiboken6", "PySDL2", "pysdl2-dll", "hidapi", "certifi",
            "truststore", "pillow")


def build_manifest(stage, *, version, commit, run_url):
    stage = Path(stage)
    if not version or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("A release version and full source commit are required")
    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/actions/runs/[0-9]+", run_url):
        raise ValueError("A public GitHub Actions run URL is required")
    if not (stage / "forza-launcher.exe").is_file():
        raise ValueError("The staged Windows launcher is missing")
    files = []
    for path in sorted(stage.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Linked package entry: {path.relative_to(stage)}")
        relative = path.relative_to(stage).as_posix()
        if relative == MANIFEST or path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"Unsupported package entry: {relative}")
        with path.open("rb") as stream:
            size = path.stat().st_size
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files.append({"path": relative, "size": size, "sha256": digest})
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return {"schema": 1, "version": version, "source_commit": commit,
            "workflow_run": run_url, "python": platform.python_version(),
            "build_packages": packages, "files": files}


def write_manifest(stage, **build):
    # Complete collection before replacing a previous manifest, including on a rerun.
    document = build_manifest(stage, **build)
    path = Path(stage) / MANIFEST
    if path.is_symlink():
        raise ValueError("The manifest must be a regular package file")
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    print(write_manifest(args.stage, version=args.version, commit=args.commit,
                         run_url=args.run_url))
