# PyInstaller spec: one directory with the launcher executable.
#   pyinstaller --noconfirm forza-launcher.spec          (from the launcher/ folder)
# packaging/make_package.py then adds the project sources, the Forza Live server and the SDK
# files beside it. The Forza Live server runs inside this executable (--live-server), so
# the stdlib modules it imports are collected as hidden imports.
import ast
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).resolve()
repo = root.parent
images = repo / "docs" / "images"

stdlib = set(sys.stdlib_module_names)
hidden = set()
if not (repo / "server" / "forza_live_server.py").is_file():
    raise SystemExit("server/ is empty: the Forza Live server is a git submodule (git submodule update --init server)")
for f in (repo / "server").glob("*.py"):
    if f.name.startswith("test_"):
        continue
    for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            hidden.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            hidden.add(node.module.split(".")[0])
hidden = sorted(m for m in hidden if m in stdlib)
hidden += ["zipfile", "tempfile", "shutil", "subprocess", "io", "sqlite3", "xml.etree.ElementTree", "http.server", "ssl", "hashlib", "hmac", "zlib"]

datas = [
    (str(root / "forza_launcher" / "update_helper.py"), "forza_launcher"),
    (str(images / "logo.svg"), "assets"),
    (str(images / "logo.png"), "assets"),
    (str(repo / "forza-recomp" / "resources" / "forza.ico"), "assets"),
    (str(images / "social-preview.png"), "assets"),
    (str(root / "forza_launcher" / "ui" / "down.svg"), "forza_launcher/ui"),
    (str(root / "forza_launcher" / "ui" / "check.svg"), "forza_launcher/ui"),
    (str(root / "forza_launcher" / "ui" / "controller_art"), "forza_launcher/ui/controller_art"),
    (str(repo / "tools" / "xcontent_extract.py"), "tools"),
    (str(repo / "tools" / "xiso_extract.py"), "tools"),
    (str(repo / "tools" / "install_dlc.py"), "tools"),
]
# certifi's cacert.pem (the fallback CA bundle of fetch.py) and truststore (the OS certificate store on
# Windows/macOS), both imported lazily and so invisible to the import scan (#85).
datas += collect_data_files("certifi")
datas += [(str(root / 'forza_launcher' / 'haptics' / 'vendor' / name), 'forza_launcher/haptics/vendor')
          for name in ('LICENSE', 'README.md')]
if sys.platform != 'linux':
    hidden += ['hid']
hidden += ["certifi"] + collect_submodules("truststore")
# PySDL2 loads SDL through ctypes; retain the wheel's DLL directory and licenses.
datas += collect_data_files("sdl2dll")
hidden += ["sdl2dll"]

# The title update step (a Python tool the frozen app runs itself with --run-script), and the shader
# prebuild (#226), which imports pipeline_list, fxobj_shaders, xxh3, lzx_delta and apply_title_update.
for _tool in ("apply_title_update.py", "lzx_delta.py", "xdc_decode.py", "extract_map_points.py", "livery_codec.py", "pipeline_list.py", "fxobj_shaders.py", "xxh3.py", "native_menu_scene.py", "offline_cars_flow.py", "native_tuning_assets.py"):
    if (repo / "tools" / _tool).is_file():
        datas.append((str(repo / "tools" / _tool), "tools"))
datas.append((str(repo / "tools" / "native_menu" / "fh1_donors.json"), "tools/native_menu"))
for name in ("change_car", "tune_car", "garage", "autoshow", "social_friends", "social_world", "social_game", "social_requests", "social_chat", "social_connection"):
    datas.append((str(repo / "tools" / "native_menu" / "icons" / (name + ".xds")), "tools/native_menu/icons"))
for name in ("ATTRIBUTION.md", "FONT-AWESOME-LICENSE.txt"):
    datas.append((str(repo / "tools" / "native_menu" / "icons" / name), "tools/native_menu/icons"))
# The licence of the RFC 8032 code forza_launcher/ed25519.py is derived from (#515).
datas.append((str(root / "forza_launcher" / "RFC8032-LICENSE.txt"), "forza_launcher"))
# The shipped pipeline list (hashes and render state only).
datas.append((str(root / "forza_launcher" / "data" / "pipelines-4D5309C9.json"), "forza_launcher/data"))
# The known-good DLC files (sizes and SHA-1 only, forza_launcher/dlccheck.py, #422).
datas.append((str(root / "forza_launcher" / "data" / "dlc-4D5309C9.json"), "forza_launcher/data"))

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root), str(repo / "tools")],
    datas=datas,
    binaries=collect_dynamic_libs("sdl2dll"),
    hiddenimports=hidden + ["install_dlc", "xcontent_extract", "xiso_extract", "xdc_decode", "livery_codec", "tomllib", "pipeline_list",
                            "fxobj_shaders", "xxh3", "lzx_delta", "apply_title_update", "offline_cars_flow", "extract_map_points"],
    excludes=["tkinter", "unittest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtNetwork", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest"],
    noarchive=False,
)
# Recent Qt releases use Windows' ICU API. A tool on the build machine's PATH
# (for example Poppler) can supply a different icuuc.dll with versioned exports.
# Bundling it shadows the OS DLL and prevents QtCore from loading at startup.
# Keep version-suffixed ICU libraries used by older Qt builds; these three names
# belong to the Windows API and must be resolved on the player's system.
if sys.platform == "win32":
    a.binaries = [entry for entry in a.binaries
                  if Path(entry[0]).name.lower() not in {"icu.dll", "icuuc.dll", "icuin.dll"}]
pyz = PYZ(a.pure)
# The Windows version resource (company, product, description, versions, original file name) of
# forza-launcher.exe, generated from forza_launcher/_version.py (#482, #354). Windows only: the Linux
# build has no version resource and does not run packaging/windows_version_info.py.
version_file = None
if sys.platform == "win32":
    _wvi_spec = importlib.util.spec_from_file_location("windows_version_info", root / "packaging" / "windows_version_info.py")
    _wvi = importlib.util.module_from_spec(_wvi_spec)
    _wvi_spec.loader.exec_module(_wvi)
    _version_ns = {}
    exec((root / "forza_launcher" / "_version.py").read_text(encoding="utf-8"), _version_ns)
    _work = Path(globals().get("workpath") or root / "build")
    _work.mkdir(parents=True, exist_ok=True)
    version_file = _work / "forza-launcher-version.txt"
    version_file.write_text(_wvi.version_file_text(_version_ns["__version__"]), encoding="utf-8", newline="\n")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="forza-launcher",
    console=False,
    icon=str(repo / "forza-recomp" / "resources" / "forza.ico") if sys.platform == "win32" else None,
    version=str(version_file) if version_file else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="forza-launcher")
