"""The "Online features" choice (#497, PRIVACY.md): the Windows installer's option, or the question
the launcher asks the first time it opens where no installer ran (Linux, Flatpak, a source checkout).

On (the default) changes nothing. Off turns off what sends data over the network by itself: online
play (Server: Offline), shader-list sharing, the co-op results fetch, the launcher's update check
and the game's Discord activity. Sharing and co-op results are turned off themselves too, so they
stay off when the player turns online play back on. Each one can be turned on again in Settings
or on the About page.

The installer writes its choice to install_options.json next to forza-launcher.exe, with a stamp
of that installer run. The launcher applies each stamp once: to the config it creates on its first
start, and to an existing config when an update's installer had the option unticked (ticked on an
update keeps the player's settings)."""
import json
from pathlib import Path

from . import paths

SEED_NAME = "install_options.json"


def state(cfg) -> dict:
    return cfg.d["online_features"]


def seed_path() -> Path | None:
    """Where the Windows installer leaves its choice: next to the packaged launcher."""
    return paths.app_dir() / SEED_NAME if paths.FROZEN else None


def read_seed(path) -> tuple | None:
    """(online features on, installer stamp) from the installer's file; None without a usable one."""
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("online_features"), bool):
        return None
    return data["online_features"], str(data.get("stamp") or "unstamped")


def turn_off(cfg, updates=True):
    """Every data-transferring feature off. A reset of the Settings page leaves the update check
    (on the About page) alone: updates=False."""
    cfg.online["mode"] = "offline"
    cfg.settings["share_shader_list"] = False
    cfg.settings["coop_results"] = False
    cfg.settings["discord_presence"] = False
    if updates:
        cfg.d["check_updates"] = False


def take_installer_choice(cfg, new_config, seed=None) -> bool:
    """Applies a choice the installer wrote and the launcher has not applied yet. Returns True when
    it did (the caller saves the config). `seed` is the file (default: next to the launcher)."""
    found = read_seed(seed_path() if seed is None else seed)
    st = state(cfg)
    if found is None or found[1] == st.get("installer"):
        return False
    on, stamp = found
    st["installer"] = stamp
    st["ask"] = False
    if new_config or not on:   # ticked on an update: the player's own settings stay
        st["on"] = on
    if not on:
        turn_off(cfg)
    return True


def question_due(cfg, new_config) -> bool:
    """Whether to ask the first-run question: a config the launcher just created that no installer
    choice covers, or one whose question was not answered (the launcher closed before)."""
    st = state(cfg)
    if new_config and st.get("on") is None:
        st["ask"] = True
    return st.get("ask") is True


def answer(cfg, on: bool):
    """The player's answer to the first-run question."""
    st = state(cfg)
    st["on"], st["ask"] = bool(on), False
    if not on:
        turn_off(cfg)


def keep_off_after_reset(cfg):
    """Reset settings: the defaults put online play, sharing, co-op results and Discord activity
    back on; a player who chose "Online features" off keeps them off."""
    if state(cfg).get("on") is False:
        turn_off(cfg, updates=False)
