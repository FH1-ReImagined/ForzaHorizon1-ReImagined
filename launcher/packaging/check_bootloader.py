"""Fail the Windows build when PyInstaller's bootloader is the stock binary and not one compiled here
(#482, #354).

PyInstaller's source distribution carries the same prebuilt Windows bootloaders as its wheels, and
only replaces them when the install ran with PYINSTALLER_COMPILE_BOOTLOADER=1 and a C compiler.
The stock bootloader is shared by every program PyInstaller has packed, malware included, which is
what antivirus machine learning keys on. This compares the installed run.exe and runw.exe with the
ones in the PyPI wheel of the same version and fails when either is byte-identical:

    python launcher/packaging/check_bootloader.py
"""
import hashlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PLATFORM = "Windows-64bit-intel"
BOOTLOADERS = ("run.exe", "runw.exe")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def installed_hashes(folder):
    """{name: sha256} of the bootloaders in a PyInstaller/bootloader/<platform> folder."""
    folder = Path(folder)
    missing = [name for name in BOOTLOADERS if not (folder / name).is_file()]
    if missing:
        raise SystemExit(f"No bootloader {', '.join(missing)} in {folder}")
    return {name: sha256((folder / name).read_bytes()) for name in BOOTLOADERS}


def stock_hashes(wheel):
    """{name: sha256} of the bootloaders inside a stock PyInstaller wheel."""
    with zipfile.ZipFile(wheel) as archive:
        return {name: sha256(archive.read(f"PyInstaller/bootloader/{PLATFORM}/{name}")) for name in BOOTLOADERS}


def stock_ones(installed, stock):
    """The names of the installed bootloaders that are byte-identical to the stock ones."""
    return sorted(name for name, digest in installed.items() if stock.get(name) == digest)


def main():
    import PyInstaller

    version = PyInstaller.__version__
    installed = installed_hashes(Path(PyInstaller.__file__).parent / "bootloader" / PLATFORM)
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:", "--no-deps",
                        "--dest", temp, f"pyinstaller=={version}"], check=True)
        stock = stock_hashes(next(Path(temp).glob("pyinstaller-*.whl")))
    for name in BOOTLOADERS:
        print(f"PyInstaller {version} {name}: installed {installed[name]}, stock {stock[name]}")
    same = stock_ones(installed, stock)
    if same:
        raise SystemExit(f"PyInstaller's stock {', '.join(same)} is installed: the bootloader was not compiled "
                         "from source (is PYINSTALLER_COMPILE_BOOTLOADER=1 set for the pip install?)")
    print("PyInstaller's bootloaders were compiled from source here")


if __name__ == "__main__":
    main()
