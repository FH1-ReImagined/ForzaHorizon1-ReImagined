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
for f in [repo / "server" / "forza_live_server.py", *sorted((repo / "server" / "forza_live").rglob("*.py"))]:
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
    (str(images / "logo-mark.png"), "assets"),
    (str(root / "forza_launcher" / "ui" / "down.svg"), "forza_launcher/ui"),
    (str(root / "forza_launcher" / "ui" / "check.svg"), "forza_launcher/ui"),
    # Bitter ExtraBold and Saira Semi Condensed with their OFL texts (ui/theme.py)
    (str(root / "forza_launcher" / "ui" / "fonts"), "forza_launcher/ui/fonts"),
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

# The title update step (a Python tool the frozen app runs itself with --run-script), Import FM4 cars
# (fm4_*, carbin_dump, #648), Import FM4 tracks (fm4_track_mod, fm4_pvs and what they import, #648), and the shader
# prebuild (#226), which imports pipeline_list, fxobj_shaders, xxh3, lzx_delta and apply_title_update.
for _tool in ("apply_title_update.py", "fm4_cars.py", "fm4_carbin.py", "fm4_physdef.py", "carbin_dump.py", "lzx_delta.py", "xdc_decode.py", "extract_map_points.py", "livery_codec.py", "pipeline_list.py", "fxobj_shaders.py", "xxh3.py", "native_menu_scene.py", "offline_cars_flow.py", "native_tuning_assets.py", "fm4_track_mod.py", "fm4_pvs.py", "fm4_col.py", "fm4_shaders.py", "fm4_spawn.py", "fm4_timing.py", "fm4_routes.py", "fh2ff_world.py"):
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
# The ReImagined loading-screen tips (forza_launcher/loading_tips.py).
datas.append((str(root / "forza_launcher" / "data" / "loading_tips.json"), "forza_launcher/data"))

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root), str(repo / "tools")],
    datas=datas,
    binaries=collect_dynamic_libs("sdl2dll"),
    hiddenimports=hidden + ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "install_dlc", "xcontent_extract", "xiso_extract", "xdc_decode", "livery_codec", "tomllib", "pipeline_list",
                            "fxobj_shaders", "xxh3", "lzx_delta", "apply_title_update", "offline_cars_flow", "extract_map_points",
                            "fm4_cars", "fm4_carbin", "fm4_physdef", "carbin_dump",
                            "fm4_spawn", "fm4_timing", "fm4_routes", "fh2ff_world", "fm4_track_mod", "fm4_pvs", "fm4_col", "fm4_shaders", "sqlite3", "argparse", "zlib"],
    # Qt WebEngine (PySide6_WebEngine in requirements-build.txt) is the website sign-in window,
    # ui/web_view.py, which imports it only when the window opens; it needs QtNetwork and
    # QtWebChannel. A build without it still works: the window then opens the player's browser.
    excludes=["tkinter", "unittest", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest"],
    noarchive=False,
)


def _webengine_unused(dest):
    """Qt WebEngine files the launcher does not use: every locale but en-US (the launcher is in
    English), the devtools and, on Windows, the .debug.pak files (about 120 MB)."""
    p = Path(dest)
    if "qtwebengine_locales" in p.parts:
        return p.name != "en-US.pak"
    return p.name.startswith("qtwebengine_devtools_resources") or p.name.endswith(".debug.pak")


a.datas = [entry for entry in a.datas if not _webengine_unused(entry[0])]
# On Linux, PyInstaller's Qt WebEngine hook also collects the NSS modules that libnss3 loads at run
# time (libsoftokn3, libfreebl3, libfreeblpriv3, libnssckbi), which its dependency scan cannot see.

# Recent Qt releases use Windows' ICU API. A tool on the build machine's PATH
# (for example Poppler) can supply a different icuuc.dll with versioned exports.
# Bundling it shadows the OS DLL and prevents QtCore from loading at startup.
# Keep version-suffixed ICU libraries used by older Qt builds; these three names
# belong to the Windows API and must be resolved on the player's system.
if sys.platform == "win32":
    a.binaries = [entry for entry in a.binaries
                  if Path(entry[0]).name.lower() not in {"icu.dll", "icuuc.dll", "icuin.dll"}]
# Linux: the system's own C++ runtime and GBM, never the build machine's (#852). Once one libstdc++ is
# loaded every later library shares it, and the system's Mesa (EGL, Vulkan drivers, VA-API) needs the
# system's, newer one: with 22.04's in _internal, EGL and Vulkan failed to start and Qt WebEngine
# aborted the launcher. 22.04's libgbm looks for the drivers in Debian's folder only. The bundle needs
# GLIBCXX_3.4.29 and GCC_4.0.0 at most (22.04 has 3.4.30), and every Linux with a desktop has all three.
LINUX_SYSTEM_LIBS = {"libstdc++.so.6", "libgcc_s.so.1", "libgbm.so.1"}
if sys.platform.startswith("linux"):
    a.binaries = [entry for entry in a.binaries if Path(entry[0]).name not in LINUX_SYSTEM_LIBS]
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
