"""The launcher's own configuration (launcher.json in the per-user config dir) and the
forza.toml it generates from it."""
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from . import OLD_PUBLIC_SERVERS, PUBLIC_SERVER, display, gpu, languages, paths, schema, specs
from .haptics.settings import DEFAULTS as HAPTICS_DEFAULTS, normalize as normalize_haptics

# Settings of the launcher itself: not game cvars, never written to forza.toml.
LAUNCHER_ONLY = ("share_shader_list", "coop_results", "battery_saver", "game_language", "vr_mode",
                 "vr_picture", "vr_seat_height_cm")
ONLINE_MODES = ["public", "local", "custom", "offline"]
# First-run values that differ from the game's own defaults.
FIRST_RUN = {"frame_rate": 60, "present_effect": "fsr"}


def _default():
    return {
        "version": 1,
        "haptics": dict(HAPTICS_DEFAULTS),
        "save_manager": {"automatic": True, "keep_automatic": 10},
        # Mods (#149, mods.py): folder names in load order, and those turned off.
        "mods": {"order": [], "disabled": []},
        # Texture packs (#384, texture_packs.py): the same for the folders in <data>/textures.
        "texture_packs": {"order": [], "disabled": []},
        "custom_radio": {"version": 1, "ffmpeg": "", "stations": {}},
        "check_updates": True,    # one background check per packaged launcher session
        "game_package": "",       # the Games on Demand package (file)
        "tu_package": "",         # the player's title update package (TU4)
        "dlc_dir": "",            # folder of DLC packages (4D5309C9/00000002)
        "game_root": "",          # an already extracted folder to use instead of data/game
        "dlc_installed": 0,
        "online": {"mode": "public", "custom_server": ""},
        "profile": {"mode": "own", "xenia_content": "", "xenia_xuid": ""},
        "advanced": {"game_dir": "", "data_root": "", "extra_args": "", "extra_env": "",
                     "extras_toml": "", "pad_per_user": False,
                     "accept_ms_license": False, "build_jobs": 0,
                     "blocked_root": "",       # a data folder the last build could not use (access denied, #38)
                     "data_root_asked": False},  # the player answered the offer to leave Documents\\forza
        "settings": {**schema.defaults(), **FIRST_RUN},
        # The graphics card the player has (#52): '' until detected; the player can change it.
        "gpu": {"vendor": "", "detected": "", "name": "", "checked": False},
        # The PC's CPU, RAM and card (#206): the detected specs plus what the player already answered.
        "specs": {"checked": False, "fingerprint": "", "dismissed": "", "values": None},
        # Battery saver (#140): the settings it forced at the last start (power.py); None: none.
        "power": {"applied": None},
        # Play in VR (vr.py): the first-time checklist was answered with "Don't show again".
        "vr": {"checklist_hidden": False},
        # The "Online features" choice (online_features.py, PRIVACY.md): on (None until made), the
        # first-run question still to ask, and the installer run whose choice was applied.
        "online_features": {"on": None, "ask": False, "installer": ""},
        # One-time fixes of values an older launcher saved: done once, so a later choice sticks.
        "migrated": {"discord_application_id": False},
    }


def _merge(base, over):
    for k, v in over.items():
        if isinstance(base.get(k), dict) and isinstance(v, dict):
            _merge(base[k], v)
        elif k in base:
            base[k] = v
    return base


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_dir() / "launcher.json"
        self.d = _default()
        self.loaded = False
        self.gamertag_cleaned = None
        self.load()

    # --- persistence ---
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A new launcher.json starts with today's defaults: nothing to migrate later.
            self.d["migrated"]["discord_application_id"] = True
            return
        if isinstance(data, dict):
            _merge(self.d, data)
            # Station slot names are dynamic keys, unlike the fixed settings schema.
            custom = data.get("custom_radio")
            if isinstance(custom, dict) and isinstance(custom.get("stations"), dict):
                self.d["custom_radio"]["stations"] = custom["stations"]
            self.loaded = True
        # Migrate the former combined AA/upscaling selectors before choice coercion.
        if self.d["settings"].get("fh1_upscaler") == "dlaa":
            self.d["settings"].update(fh1_upscaler="off", forza_antialiasing="dlaa")
        elif (self.d["settings"].get("present_effect") == "fsr3" and
              self.d["settings"].get("fsr3_mode") == "native"):
            self.d["settings"].update(present_effect="bilinear", fsr3_mode="quality",
                                      forza_antialiasing="fsr3_native")
        # 0.1.28-0.1.29 saved the then-default empty Discord application ID, which turned the
        # connection off; since 0.1.30 the project's own ID is the default (#312).
        migrated = self.d["migrated"]
        if not migrated.get("discord_application_id"):
            if not str(self.d["settings"].get("discord_application_id") or "").strip():
                self.d["settings"]["discord_application_id"] = schema.BY_KEY["discord_application_id"].default
            migrated["discord_application_id"] = True
        self.d['haptics'] = normalize_haptics(self.d.get('haptics'))
        prefs = self.d.get('save_manager')
        if not isinstance(prefs, dict):
            prefs = {}
        try:
            keep = max(1, min(50, int(prefs.get('keep_automatic', 10))))
        except (TypeError, ValueError, OverflowError):
            keep = 10
        self.d['save_manager'] = dict(automatic=prefs.get('automatic', True) is True, keep_automatic=keep)
        saved_tag = self.d["settings"].get("gamertag")
        for key, setting in schema.BY_KEY.items():
            self.d["settings"][key] = schema.coerce(setting, self.d["settings"].get(key))
        self.gamertag_cleaned = None  # (saved, cleaned) when the saved Gamertag was not plain ASCII
        if isinstance(saved_tag, str) and saved_tag.strip() != self.d["settings"]["gamertag"]:
            self.gamertag_cleaned = (saved_tag, self.d["settings"]["gamertag"])
        online = self.d["online"]
        if online.get("mode") == "custom" and online.get("custom_server", "").strip() in OLD_PUBLIC_SERVERS:
            online["mode"], online["custom_server"] = "public", ""
        from . import ingame
        ingame.import_pending(self)

    def save(self):
        _atomic_write(self.path, json.dumps(self.d, indent=2) + "\n")

    # --- GPU (#52) ---
    @property
    def gpu(self):
        return self.d["gpu"]

    def gpu_vendor(self) -> str:
        v = self.gpu.get("vendor", "")
        return v if v in gpu.VENDORS else ""

    def detect_gpu(self, detected=None):
        """Detect the graphics card once and preselect it. A new launcher.json gets the vendor's
        preset (the first-run defaults); one that already holds the player's settings keeps them
        and only learns the vendor, so warnings can show. Returns the vendor."""
        if self.gpu.get("vendor") or self.gpu.get("checked"):
            return self.gpu_vendor()
        found = detected or gpu.detect()
        self.gpu["detected"], self.gpu["name"] = found.vendor, found.name
        self.gpu["vendor"] = found.vendor
        self.gpu["checked"] = True
        if found.vendor and not self.loaded:
            gpu.apply_preset(self.settings, found.vendor)
        return found.vendor

    # --- PC specs (#206) ---
    def pc_specs(self) -> specs.Specs:
        return specs.specs_from_dict(self.d["specs"].get("values"))

    def detect_specs(self, detected=None, force=False) -> bool:
        """Detect the PC's specs once; again when the graphics card changed or on request (force). A new
        launcher.json gets the recommended frame rate and render scale on top of the vendor's preset; one
        that already holds the player's settings keeps them (the banner offers the recommendation).
        Returns True when the specs were (re)detected."""
        cache = self.d["specs"]
        first = not cache.get("checked")
        if not first and not force and detected is None and cache.get("fingerprint") == specs.fingerprint():
            return False
        found = detected or specs.detect()
        cache.update(checked=True, fingerprint=specs.fingerprint(), values=asdict(found))
        if first and not self.loaded:
            self.settings.update(specs.recommended(found, display.effective(self.settings, found), self.gpu_vendor()))
        return True

    def effective_settings(self, log=None) -> dict:
        """The settings as the game will get them: "Match display" (frame_rate 0) as a number (#215)."""
        return display.effective(self.settings, self.pc_specs() if self.d["specs"].get("values") else None, log=log)

    def specs_warnings(self) -> list:
        return specs.warnings(self.pc_specs(), self.effective_settings())

    def specs_banner_due(self) -> bool:
        """Some settings are heavy for this PC (or a Steam Deck's differ from its preset) and the player has
        not answered for this hardware."""
        s = self.pc_specs()
        return (s.known() and self.d["specs"].get("dismissed") != s.signature()
                and bool(any(w.fix for w in self.specs_warnings()) or specs.deck_offer(s, self.effective_settings())))

    def specs_banner_text(self) -> str:
        return specs.banner_text(self.pc_specs())

    def dismiss_specs_banner(self):
        self.d["specs"]["dismissed"] = self.pc_specs().signature()

    def use_recommended(self) -> list:
        """Apply the fixes of the warnings only, and on a Steam Deck its preset (never another setting);
        returns the keys changed."""
        fixes = {}
        for w in self.specs_warnings():
            for key, value in w.fix.items():   # the CPU and the card may both limit the frame rate: the lower wins
                fixes[key] = min(fixes[key], value) if key == "frame_rate" and key in fixes else value
        fixes.update(specs.deck_offer(self.pc_specs(), {**self.effective_settings(), **fixes}))
        changed = [k for k, v in fixes.items() if self.settings.get(k) != v]
        self.settings.update(fixes)
        self.dismiss_specs_banner()
        return changed

    def set_gpu_vendor(self, vendor: str, apply_defaults: bool) -> list:
        """The player's choice ('' = other / not sure). Returns the settings the preset changed."""
        self.gpu["vendor"] = vendor if vendor in gpu.VENDORS else ""
        self.gpu["checked"] = True
        return gpu.apply_preset(self.settings, vendor) if apply_defaults else []

    # --- accessors ---
    def __getitem__(self, key):
        return self.d[key]

    def __setitem__(self, key, value):
        self.d[key] = value

    @property
    def settings(self):
        return self.d["settings"]

    @property
    def online(self):
        return self.d["online"]

    @property
    def profile(self):
        return self.d["profile"]

    @property
    def advanced(self):
        return self.d["advanced"]

    def data_home(self) -> Path:
        override = self.advanced.get("data_root", "").strip()
        return Path(override) if override else paths.data_home()

    def game_files(self) -> Path:
        override = self.d.get("game_root", "").strip()
        return Path(override) if override else self.data_home() / "game"

    def game_files_ready(self) -> bool:
        return (self.game_files() / "default.xex").is_file()

    def server_spec(self):
        """The Forza Live server the game should use: 'host[:port]', or '' for none."""
        mode = self.online["mode"]
        if mode == "public":
            return PUBLIC_SERVER
        if mode == "custom":
            return self.online.get("custom_server", "").strip()
        return ""


# --- forza.toml -----------------------------------------------------------------------

def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(round(v, 6))
    return json.dumps(str(v))  # a TOML basic string


def parse_toml(text: str) -> dict:
    import tomllib
    return tomllib.loads(text)


def flatten(table: dict, prefix="") -> dict:
    """Nested tables join their names with '_' (a [gpu] table's vsync is gpu_vsync): the SDK's rule."""
    out = {}
    for k, v in table.items():
        key = prefix + k
        if isinstance(v, dict):
            out.update(flatten(v, key + "_"))
        else:
            out[key] = v
    return out


def render_toml(cfg: Config) -> str:
    lines = ["# Written by the Forza Horizon 1: ReImagined launcher. Change settings there;",
             "# keys added under 'Other settings' (Advanced) are kept."]
    settings = cfg.effective_settings()
    for s in schema.S:
        if not s.applies() or s.key in ("fh1_output_resolution", "fsr3_mode", "fsr3_output_resolution", "forza_antialiasing") \
                or s.key in LAUNCHER_ONLY:
            continue
        v = schema.coerce(s, settings.get(s.key, s.default))
        if s.key in ("user_language", "user_country"):    # the Game language choice (#434) sets both
            v = dict(zip(("user_language", "user_country"), languages.resolve(
                settings.get("game_language", ""), settings.get("user_language", s.default),
                settings.get("user_country", s.default))))[s.key]
        if v != s.default:
            lines.append(f"{s.key} = {toml_value(v)}")
    # public and custom servers are named here too; local (an environment variable at start)
    # and offline leave live_server empty.
    server = cfg.server_spec()
    if server:
        lines.append(f"live_server = {toml_value(server)}")
    extras = cfg.advanced.get("extras_toml", "").strip()
    if extras:
        lines += ["", "# Other settings", extras]
    return "\n".join(lines) + "\n"


def validate_extras(text: str) -> str | None:
    """An error message when the 'other settings' text is not valid TOML, else None."""
    if not text.strip():
        return None
    try:
        parse_toml(text)
    except Exception as e:  # tomllib.TOMLDecodeError
        return str(e)
    return None


def import_toml(cfg: Config, text: str) -> None:
    """Adopt an existing forza.toml (first launch): known keys into settings, the rest kept."""
    try:
        flat = flatten(parse_toml(text))
    except Exception:
        return
    if flat.get("fh1_upscaler") == "dlaa":
        flat.update(fh1_upscaler="off", forza_antialiasing="dlaa")
    if flat.get("present_effect") == "fsr3" and flat.get("fsr3_mode") == "native":
        flat.update(present_effect="bilinear", fsr3_mode="quality", forza_antialiasing="fsr3_native")
    extras = []
    for key, val in flat.items():
        s = schema.BY_KEY.get(key)
        if s is not None:
            cfg.settings[key] = schema.coerce(s, val)
        elif key == "live_server":
            if isinstance(val, str) and val:
                if val == PUBLIC_SERVER or val in OLD_PUBLIC_SERVERS:
                    cfg.online["mode"] = "public"
                else:
                    cfg.online["mode"], cfg.online["custom_server"] = "custom", val
            else:
                cfg.online["mode"] = "local"
        elif isinstance(val, (bool, int, float, str)):
            extras.append(f"{key} = {toml_value(val)}")
    if extras:
        cfg.advanced["extras_toml"] = "\n".join(extras)


def write_toml(cfg: Config, toml_path: Path) -> str:
    text = render_toml(cfg)
    try:
        if toml_path.is_file() and toml_path.read_text(encoding="utf-8") == text:
            return text
    except OSError:
        pass
    _atomic_write(toml_path, text)
    return text
