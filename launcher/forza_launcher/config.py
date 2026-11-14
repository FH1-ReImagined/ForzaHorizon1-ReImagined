"""The launcher's own configuration (launcher.json in the per-user config dir) and the
forza.toml it generates from it."""
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from . import OLD_PUBLIC_SERVERS, PUBLIC_SERVER, display, gpu, languages, paths, regpu, schema, specs
from .haptics.settings import DEFAULTS as HAPTICS_DEFAULTS, normalize as normalize_haptics

# Settings of the launcher itself: not game cvars, never written to forza.toml.
LAUNCHER_ONLY = ("share_shader_list", "coop_results", "challenges", "battery_saver", "game_language",
                 "vr_mode", "vr_picture", "vr_seat_height_cm", "vr_swap_eyes",
                 "reimagined_loading_tips")
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
        # FM4 circuits (#648, fm4tracks.py, experimental): the one Play starts on ("": FH1's Colorado).
        "fm4_tracks": {"selected": ""},
        "custom_radio": {"version": 1, "ffmpeg": "", "level": True, "stations": {}},
        "check_updates": True,    # one background check per packaged launcher session
        "game_package": "",       # the Games on Demand package (file)
        "tu_package": "",         # the player's title update package (TU4)
        "dlc_dir": "",            # folder of DLC packages (4D5309C9/00000002)
        "game_root": "",          # an already extracted folder to use instead of data/game
        "dlc_installed": 0,
        # The server Play uses, and the addresses added by hand on the Servers page (#587).
        "online": {"mode": "public", "custom_server": "", "added_servers": []},
        "profile": {"mode": "own", "xenia_content": "", "xenia_xuid": ""},
        "advanced": {"game_dir": "", "data_root": "", "extra_args": "", "extra_env": "",
                     "extras_toml": "", "pad_per_user": False,
                     "system_file_dialogs": False,   # the system's file dialogs, not the launcher's own (#884)
                     "accept_ms_license": False, "build_jobs": 0,
                     "blocked_root": "",       # a data folder the last build could not use (access denied, #38)
                     "data_root_asked": False},  # the player answered the offer to leave Documents\\forza
        "settings": {**schema.defaults(), **FIRST_RUN},
        # The graphics card the player has (#52): '' until detected; the player can change it.
        # vulkan: its Vulkan devices and their driver's API version (gpu.vulkan_devices, #672); None: unknown.
        "gpu": {"vendor": "", "detected": "", "name": "", "checked": False, "vulkan": None},
        # The PC's CPU, RAM and card (#206): the detected specs plus what the player already answered
        # (dismissed: the warnings, world_detail: the offer of higher World detail; each a hardware signature).
        "specs": {"checked": False, "fingerprint": "", "dismissed": "", "world_detail": "", "values": None},
        # Battery saver (#140): the settings it forced at the last start (power.py); None: none.
        "power": {"applied": None},
        # Play in VR (vr.py): the first-time checklist was answered with "Don't show again".
        "vr": {"checklist_hidden": False},
        # The "Online features" choice (online_features.py, PRIVACY.md): on (None until made), the
        # first-run question still to ask, and the installer run whose choice was applied.
        "online_features": {"on": None, "ask": False, "installer": ""},
        # Online > Online > Website account (web_account.py): this launcher linked the game to a
        # website account, so the card asks the service for the link's state.
        "web_account": {"linked": False},
        # One-time fixes of values an older launcher saved: done once, so a later choice sticks.
        "migrated": {"discord_application_id": False, "gpu_wait_blocking": False,
                     "nvidia_pipeline_cache": False, "scene_540": False,
                     "paint_job_2x": False},
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
        self.notices = []   # one-time notices of changes this launcher made to the saved settings (Play page)
        self.load()

    # --- persistence ---
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A new launcher.json starts with today's defaults: nothing to migrate later.
            self.d["migrated"].update(discord_application_id=True, gpu_wait_blocking=True,
                                      nvidia_pipeline_cache=True, scene_540=True, paint_job_2x=True)
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
        # Up to 0.2.6 "Sleep while waiting for the GPU" was off by default on Windows (not measured
        # there yet) and every launcher.json saved that off; it is on by default now (#640).
        if not migrated.get("gpu_wait_blocking"):
            self.d["settings"]["gpu_wait_blocking"] = True
            migrated["gpu_wait_blocking"] = True
        # The NVIDIA preset stopped keeping the driver pipeline file to limit startup time and memory
        # use (#640). Older launcher configs still have the previous preset value.
        if not migrated.get("nvidia_pipeline_cache"):
            if self.d["gpu"].get("vendor") == gpu.NVIDIA:
                self.d["settings"]["vulkan_store_pipeline_cache"] = False
            migrated["nvidia_pipeline_cache"] = True
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
        self._migrate_scene_540()
        self._migrate_paint_job_2x()
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

    # --- the Vulkan driver (#672) ---
    def detect_vulkan(self, devices=None) -> bool:
        """Ask the Vulkan driver which API version it has (each launcher start: a driver update changes it).
        A new launcher.json on Windows whose Intel iGPU has a driver below Vulkan 1.3 gets Direct3D 12;
        an existing one keeps its Graphics API (Setup and Play warn and offer "Use Direct3D 12").
        Returns True when what is known changed."""
        found = gpu.vulkan_devices() if devices is None else devices
        if not found:
            return False   # unknown: keep what was known
        first = self.gpu.get("vulkan") is None
        changed = self.gpu.get("vulkan") != found
        self.gpu["vulkan"] = found
        if first and not self.loaded and gpu.IS_WINDOWS and self.old_intel_vulkan() is not None:
            self.settings["gpu_backend"] = "d3d12"
        return changed

    def old_intel_vulkan(self):
        """The game's Vulkan device when it is an Intel iGPU on a driver below Vulkan 1.3, else None."""
        devices = self.gpu.get("vulkan")
        if not isinstance(devices, list):
            return None
        try:
            index = int(self.settings.get("vulkan_device", -1))
        except (TypeError, ValueError):
            index = -1
        return gpu.old_intel_vulkan(devices, self.gpu_vendor(), index)

    def intel_vulkan_warning(self) -> str:
        """The warning for Setup and Play: '' when the driver is fine or unknown, or when the game already
        uses Direct3D 12 or ReGPU (Windows): neither needs the Vulkan driver."""
        device = self.old_intel_vulkan()
        if device is None or (gpu.IS_WINDOWS and self.settings.get("gpu_backend", "vulkan") != "vulkan"):
            return ""
        return gpu.old_intel_vulkan_text(device, gpu.IS_WINDOWS)

    def intel_vulkan_fix(self) -> dict:
        """What "Use Direct3D 12" sets ({} where it does not apply: Linux, or nothing to warn about)."""
        return {"gpu_backend": "d3d12"} if gpu.IS_WINDOWS and self.intel_vulkan_warning() else {}

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
            cache["world_detail"] = self.pc_specs().signature()   # it has them now: a later Console is the choice
        elif self.loaded:
            self._migrate_scene_540()   # a launcher.json from before the specs were detected (#206)
        return True

    def _migrate_scene_540(self):
        """Up to 0.2.6 every launcher.json saved the 720p scene; on an entry card the 540p one is the
        recommendation since #640 (specs.entry_card, the benchmark 47 -> 54 fps on a GTX 1650 Ti laptop). Done
        once, with a notice, so a player who sets 720p again keeps it. Only where 540p is drawn: FSR 3, DLSS, a
        separate anti-aliasing method and Ultrawide gameplay keep 720p. Waits for the specs when a launcher.json
        has none yet."""
        migrated = self.d["migrated"]
        if migrated.get("scene_540") or not self.d["specs"].get("values"):
            return
        s = self.pc_specs()
        if not s.known():
            return
        migrated["scene_540"] = True
        if (specs.entry_card(s) and str(self.settings.get("scene_resolution")) == "720"
                and specs.scene_540_applies(self.settings)):
            self.settings["scene_resolution"] = "540"
            self.notices.append(specs.scene_540_notice(s))

    def _migrate_paint_job_2x(self):
        """0.3.2 started high-end PCs at Paint job detail 2x, and at 2x the game's own memory heap ran out after
        the Race Central cutscene and the game crashed (#779). Done once, with a notice: Paint job detail goes
        back to Console, and a player who sets 2x again keeps it."""
        migrated = self.d["migrated"]
        if migrated.get("paint_job_2x"):
            return
        migrated["paint_job_2x"] = True
        if str(self.settings.get("livery_bake_scale")) == "2":
            self.settings["livery_bake_scale"] = 1
            self.notices.append("Paint job detail is back to Console: at 2x the game could run out of memory and "
                                "crash after the Race Central cutscene. To use 2x again: Settings > Graphics > "
                                "World detail > Paint job detail.")

    def effective_settings(self, log=None) -> dict:
        """The settings as the game will get them: "Match display" (frame_rate 0) as a number (#215)."""
        return display.effective(self.settings, self.pc_specs() if self.d["specs"].get("values") else None, log=log)

    def specs_warnings(self) -> list:
        return specs.warnings(self.pc_specs(), self.effective_settings())

    def _warnings_due(self, s) -> bool:
        return (self.d["specs"].get("dismissed") != s.signature()
                and bool(any(w.fix for w in self.specs_warnings()) or specs.deck_offer(s, self.effective_settings())))

    def world_detail_offer(self) -> dict:
        """High-end graphics whose launcher.json still has the console's World detail: the higher values for
        the settings still at their default, until the player answers for this hardware ({} then)."""
        s = self.pc_specs()
        if not s.known() or self.d["specs"].get("world_detail") == s.signature():
            return {}
        return specs.world_detail_offer(s, self.effective_settings(), self.gpu_vendor())

    def specs_banner_due(self) -> bool:
        """Some settings are heavy for this PC (or a Steam Deck's differ from its preset), or high-end graphics
        could run higher World detail, and the player has not answered for this hardware."""
        s = self.pc_specs()
        return s.known() and (self._warnings_due(s) or bool(self.world_detail_offer()))

    def specs_banner_text(self) -> str:
        s = self.pc_specs()
        offer = self.world_detail_offer()
        if not self._warnings_due(s):
            return specs.world_detail_text(s, offer) if offer else specs.banner_text(s)
        if not offer:
            return specs.banner_text(s)
        return specs.banner_text(s) + " " + specs.world_detail_text(s, offer, also=True)

    def dismiss_specs_banner(self):
        sig = self.pc_specs().signature()
        self.d["specs"]["dismissed"] = sig
        self.d["specs"]["world_detail"] = sig

    def use_recommended(self) -> list:
        """Apply the fixes of the warnings only, on a Steam Deck its preset, and on high-end graphics the World
        detail offer (never another setting); returns the keys changed. What the player answered before (Keep
        mine) is not applied."""
        s = self.pc_specs()
        fixes = {}
        if self._warnings_due(s):
            for w in self.specs_warnings():
                for key, value in w.fix.items():   # the CPU and the card may both limit the frame rate: the lower wins
                    fixes[key] = min(fixes[key], value) if key == "frame_rate" and key in fixes else value
            fixes.update(specs.deck_offer(s, {**self.effective_settings(), **fixes}))
        fixes.update(self.world_detail_offer())
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
        if s.key == "gpu_backend" and v == regpu.CHOICE:   # ReGPU: the d3d12 backend with its plugin
            lines += [f"{k} = {toml_value(x)}" for k, x in regpu.toml_entries(v)]
        elif v != s.default:
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
    backend = regpu.backend_from_toml(flat)
    if backend is not None and "gpu_backend" in schema.BY_KEY:
        flat.pop("gpu_plugin")
        flat["gpu_backend"] = backend
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
