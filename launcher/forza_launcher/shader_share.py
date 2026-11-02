"""Shader lists from players' play (#276, docs/shader_prebuild.md phase 3).

After a game session the launcher reads the player's own pipeline stores
(<cache root>/shaders/shareable/*.xpso, also the split scene/ui ones), turns the pipelines it has not
sent before into list entries (tools/pipeline_list.py: shader hashes, decoded render state, and for
each shader how to rebuild it from the player's own game files) and uploads them to the Forza Live
server the player plays on. The server publishes the entries several players reported;
`community_list` downloads that list before Prebuild shaders and merges it with the shipped one.

Nothing from the game leaves the PC: no shader code, no game files, nothing personal (the entries
carry hashes and render state; the upload is signed with the player's Forza Live XUID and friends
key, like the friends list, live_auth.py: the key itself is not sent). Only the public server and
the player's own server are used, never offline or the server on this PC, and only when
Settings > Online > "Share shader list" is on.
Everything here has short timeouts, fails silently (the launcher log has it) and runs in a
background thread: it never holds up Play."""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import TITLE_ID, live_auth, paths, shader_prebuild

SHARE_MODES = ("public", "custom")
DEFAULT_UDP_PORT = 21200
BATCH = 400             # pipelines per request (the server takes 500)
PER_RUN = 3000          # pipelines per session (the server takes 5000 per player and day)
UPLOAD_TIMEOUT = 8      # seconds per request
DOWNLOAD_TIMEOUT = 6
MAX_DOWNLOAD = 32 << 20
RECORD_NAME = "shader_share.json"
MAX_RECORD = 200000
_run_lock = threading.Lock()


# --- Where --------------------------------------------------------------------------------------

def http_base(spec: str) -> str:
    """The title servers' URL of a 'host[:port]' Forza Live address: the HTTP port follows the UDP
    port by one (21200 and 21201 by default; the public server's tunnel publishes 56677 and 56678)."""
    spec = (spec or "").strip()
    host, port = spec, str(DEFAULT_UDP_PORT)
    if spec.startswith("["):
        end = spec.find("]")
        host, rest = spec[:end + 1], spec[end + 1:]
        if end < 0 or (rest and not rest.startswith(":")):
            return ""
        port = rest[1:] or port
    elif spec.count(":") == 1:
        host, port = spec.split(":")
    elif ":" in spec:
        return ""
    if not host or any(c in host for c in "/ @?#"):
        return ""
    try:
        number = int(port)
    except ValueError:
        return ""
    if not 0 < number < 65535:
        return ""
    return f"http://{host}:{number + 1}"


def server_base(cfg) -> str:
    """The server shader lists go to and come from, or '' (offline, or the server on this PC)."""
    if cfg.online.get("mode") not in SHARE_MODES:
        return ""
    return http_base(cfg.server_spec())


def sharing_on(cfg) -> bool:
    return cfg.settings.get("share_shader_list", True) is True


# --- What ---------------------------------------------------------------------------------------

def read_identity(dirs) -> tuple | None:
    """(XUID, friends key, signing seed) in hex from the first live_identity.txt in `dirs`, or
    None. A file without a signing seed (a game before signed requests made it, #515) is none:
    the game adds it at its next start."""
    for d in dirs:
        try:
            lines = (Path(d) / "live_identity.txt").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        found = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
        xuid, key = found.get("xuid", "").strip().upper(), found.get("friends_key", "").strip().upper()
        seed = found.get("signing_seed", "").strip().upper()
        if len(xuid) == 16 and len(key) == 32 and len(seed) == 64 and \
                all(c in "0123456789ABCDEF" for c in xuid + key + seed):
            return xuid, key, seed
    return None


def identity_dirs(cfg) -> list:
    from . import datafolder
    return [datafolder.game_default_data_home() / TITLE_ID, cfg.data_home() / TITLE_ID]


def store_files(root: Path) -> list:
    """The pipeline stores under a cache root: the main one and the split scene/ui ones."""
    found = sorted(root.glob("shaders/shareable/*.xpso")) + sorted(root.glob("fh1-live-*/*/shaders/shareable/*.xpso"))
    return [p for p in found if p.is_file()]


def entry_id(p) -> str:
    """What the local record keeps of a sent entry: its key and partitions."""
    import pipeline_list
    text = pipeline_list.pipeline_key(p) + "|" + ",".join(sorted(p["partitions"]))
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def stores_by_version(files) -> dict:
    """{'fbo:20260228/20261018': [xpso paths]}: stores of different versions are not mixed."""
    import pipeline_list
    out = {}
    for f in files:
        try:
            with open(f, "rb") as fh:
                api, version, _ = pipeline_list.read_xpso_header(fh.read(32))
        except Exception:
            continue
        out.setdefault(f"{api}:{version}", []).append(f)
    return out


def shipped_list():
    paths.ensure_tools_importable()
    import pipeline_list
    f = shader_prebuild.list_path()
    return pipeline_list.load_list(str(f)) if f else None


def collect_new(files, versions, sent: dict, shipped_keys: set) -> dict:
    """{version: {id: pipeline}} of the listed versions' pipelines not yet sent (or in the shipped
    list, which every launcher has). Only the versions the shipped list is for are shared."""
    paths.ensure_tools_importable()
    import pipeline_list
    out = {}
    for version, group in stores_by_version(files).items():
        if version not in versions:
            continue
        pipelines, _v, _h = pipeline_list.stores_to_pipelines(group)
        done = set(sent.get(version, ()))
        new = {}
        for key, p in pipelines.items():
            ident = entry_id(p)
            if ident not in done and key not in shipped_keys:
                new[ident] = p
        if new:
            out[version] = new
    return out


def load_record(path: Path, base: str) -> dict:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("server") == base and isinstance(rec.get("sent"), dict):
            return rec
    except (OSError, ValueError, AttributeError):
        pass
    return {"server": base, "sent": {}}


def save_record(path: Path, rec: dict):
    for ids in rec["sent"].values():
        del ids[:-MAX_RECORD]  # in place: the caller keeps appending to these lists
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


# --- Upload -------------------------------------------------------------------------------------

def post(base: str, identity: tuple, body: bytes, timeout=UPLOAD_TIMEOUT) -> int:
    try:
        with live_auth.urlopen(base + "/shader-lists/upload", identity, body, timeout=timeout) as r:
            r.read(4096)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def recipes_for(pipelines, xsh_paths, sources) -> dict:
    """{hash: recipe} for the shaders of the pipelines (tools/pipeline_list.make_shader_recipes)."""
    import pipeline_list
    hashes = {}
    for p in pipelines:
        hashes[int(p["vs"], 16)] = 0
        if int(p["ps"], 16):
            hashes[int(p["ps"], 16)] = 1
    return pipeline_list.make_shader_recipes(hashes, xsh_paths, sources)


def usable(p, recipes) -> bool:
    """A pipeline is worth sharing when others can rebuild its shaders from their game files."""
    return all(h == "0" * 16 or recipes.get(h, {"from": "missing"})["from"] != "missing"
               for h in (p["vs"], p["ps"]))


def share(cfg, plan, game=None, log=print, post_fn=None) -> str:
    """Send the pipelines of this PC's stores that were not sent. Returns a short result."""
    post_fn = post_fn or post
    if not sharing_on(cfg):
        return "off"
    base = server_base(cfg)
    if not base:
        return "no server"
    identity = read_identity(identity_dirs(cfg))
    if identity is None:
        return "no Forza Live identity yet"
    shipped = shipped_list()
    if shipped is None:
        return "no list"
    import pipeline_list
    root = shader_prebuild.cache_root(plan)
    files = store_files(root)
    record_file = cfg.data_home() / RECORD_NAME
    rec = load_record(record_file, base)
    shipped_keys = {pipeline_list.pipeline_key(p) for p in shipped["pipelines"]}
    wanted = {v for v in shipped["storage_versions"]}
    new = collect_new(files, wanted, rec["sent"], shipped_keys)
    if not new:
        return "nothing new"
    sources = None
    sent_count = 0
    left = PER_RUN
    for version, entries in new.items():
        ids = list(entries)[:left]
        left -= len(ids)
        if sources is None:
            sources = shader_prebuild.game_sources(cfg, game, plan)
        xsh = sorted({str(f.with_suffix(".xsh")) for f in files if f.with_suffix(".xsh").is_file()})
        chosen = [entries[i] for i in ids]
        recipes = recipes_for(chosen, xsh, sources)
        done = rec["sent"].setdefault(version, [])
        for at in range(0, len(ids), BATCH):
            batch_ids = ids[at:at + BATCH]
            batch = [entries[i] for i in batch_ids]
            good = [p for p in batch if usable(p, recipes)]
            hashes = {h for p in good for h in (p["vs"], p["ps"]) if h != "0" * 16}
            body = json.dumps({"version": version, "pipelines": good,
                               "shaders": {h: recipes[h] for h in sorted(hashes)}},
                              separators=(",", ":")).encode()
            status = post_fn(base, identity, body) if good else 200
            if status != 200:
                log(f"Shader list: the server answered {status}; trying again after the next session")
                save_record(record_file, rec)
                return f"stopped ({status}) after {sent_count}"
            # Pipelines the player's files cannot rebuild are left out of the upload and noted as done.
            done.extend(batch_ids)
            sent_count += len(good)
            save_record(record_file, rec)
        if left <= 0:
            break
    return f"sent {sent_count}"


def share_quietly(cfg, plan, game=None, log=print) -> str:
    """share(), never raising and never twice at once; failures go to the launcher log."""
    if not _run_lock.acquire(blocking=False):
        return "busy"
    try:
        result = share(cfg, plan, game, log)
        if result.startswith(("sent", "stopped")):
            log(f"Shader list: {result}")
        return result
    except Exception as e:  # offline, a server that is down, a player without game files...
        paths.log_error("share the shader list", e)
        return "failed"
    finally:
        _run_lock.release()


def share_in_background(cfg, plan, log=print):
    """After a session: the upload in a daemon thread. Does nothing at all when sharing is off."""
    if not sharing_on(cfg) or not server_base(cfg):
        return None
    t = threading.Thread(target=share_quietly, args=(cfg, plan, None, log), name="shader-share", daemon=True)
    t.start()
    return t


# --- Download -----------------------------------------------------------------------------------

def fetch(base: str, version: str, timeout=DOWNLOAD_TIMEOUT) -> dict | None:
    q = urllib.parse.quote(version, safe="")
    with urllib.request.urlopen(f"{base}/shader-lists?version={q}", timeout=timeout) as r:
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        return None
    return json.loads(data)


def merge_community(shipped: dict, community: dict) -> dict:
    """The shipped list plus a downloaded one, which must be for the same storage version and pass
    the list check; a recipe that differs from the shipped one yields to it."""
    import pipeline_list
    if community.get("storage_versions") != shipped["storage_versions"]:
        raise ValueError("another storage version")
    problems = pipeline_list.check_list(community)
    if problems:
        raise ValueError(f"damaged list ({problems[0]})")
    own = shipped.get("shaders", {})
    community = dict(community, shaders={h: r for h, r in community.get("shaders", {}).items()
                                         if h not in own or own[h]["from"] == "missing"
                                         or own[h] == r})
    return pipeline_list.merge_lists([shipped, community])


def community_list(cfg, shipped: dict, log=print, fetch_fn=None) -> dict:
    """The list to prebuild: the shipped one, plus the server's when it can be had. Any failure
    (offline, no server, slow, damaged) gives the shipped list."""
    base = server_base(cfg)
    if not base or len(shipped.get("storage_versions", ())) != 1:
        return shipped
    try:
        community = (fetch_fn or fetch)(base, shipped["storage_versions"][0])
        if not isinstance(community, dict):
            return shipped
        merged = merge_community(shipped, community)
    except Exception as e:  # urllib errors, timeouts, bad JSON, a list that does not fit
        log(f"Shader list: using the list shipped with the launcher ({type(e).__name__}: {e})")
        return shipped
    added = len(merged["pipelines"]) - len(shipped["pipelines"])
    log(f"Shader list: {max(added, 0)} pipelines added from the server's list")
    return merged
