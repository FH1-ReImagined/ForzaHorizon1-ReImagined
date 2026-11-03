"""The Servers page's list (#587): the signed list of Forza Live servers, their status and ping, and
picking one.

The main server publishes the servers it lists at /servers.json (server/forza_live/core/server_list.py
has the format), signed with its list key: the launcher takes the list only when LIST_KEY signed it
and it has not expired, so a list changed on the way (plain HTTP) is refused. The main server is
always on the page, first, even when the list cannot be had; so are "This PC" and the addresses the
player added by hand (Config.online["added_servers"]).

Status and ping: GET /status of each server's HTTP port (the UDP port + 1, shader_share.http_base),
its "players" and the time from the request to the answer on an open connection. A server that does
not answer is shown as down; nothing is taken off the page for it.

Picking a server saves it like Settings > Online: the main server is "Public server", This PC is
"This PC", any other is "My own server" with its address, so Play passes it to the game
(FORZA_SERVER, forza.toml's live_server).

Community servers keep their own leaderboards: only the main server's are the official ones."""
import concurrent.futures
import http.client
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import PUBLIC_SERVER, __version__, ed25519
from .shader_share import http_base

# The public half of the main server's list key (its data folder's server_list_key.txt). Empty: no
# list is fetched, and the page shows the main server, This PC and the added addresses.
LIST_KEY = ""
LIST_PATH = "/servers.json"
FORMAT = "forza-live-servers-1"
TAG = b"FORZA-SERVERS-V1\n"
PING_SECONDS = 30       # the page pings every server this often while it is open
FETCH_TIMEOUT = 6
PING_TIMEOUT = 4
MAX_LIST = 256 << 10
MAX_STATUS = 4 << 20
MAX_ADDED = 50
LOCAL = "local"         # the address of the This PC entry
USER_AGENT = "forza-reimagined-launcher/" + __version__


class ListError(Exception):
    pass


@dataclass
class Server:
    name: str
    address: str            # host[:port] as players type it; LOCAL for This PC
    region: str = ""
    contact: str = ""
    verified: bool = False
    kind: str = "listed"    # main, listed, local or added
    server_id: str = ""


@dataclass
class Status:
    up: bool
    players: int | None = None
    ping_ms: int | None = None
    error: str = ""


def normalize(address: str) -> str:
    """A typed address as it is saved (host[:port], the host lowercased), or '' when it is not one."""
    address = (address or "").strip().lower()
    return address if http_base(address) else ""


def check_document(document, public: bytes, now=None) -> list:
    """The servers (dicts) of a /servers.json document when `public` signed it and it has not
    expired. ListError otherwise."""
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ListError("not a server list")
    text, signature = document.get("list"), document.get("signature")
    if not isinstance(text, str) or not isinstance(signature, str):
        raise ListError("not a server list")
    try:
        signature = bytes.fromhex(signature)
    except ValueError:
        raise ListError("the list's signature is not valid") from None
    if not ed25519.verify(public, TAG + text.encode(), signature):
        raise ListError("the list's signature is not valid")
    try:
        data = json.loads(text)
        expires, servers = float(data["expires"]), data["servers"]
        if not isinstance(servers, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise ListError("not a server list") from None
    if (time.time() if now is None else now) > expires:
        raise ListError("the list has expired (is this PC's clock right?)")
    return servers


def parse_servers(items) -> list:
    """[Server] of the list's entries: those without a name or a valid address are left out, as
    are later entries with an address already listed."""
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())[:48]
        address = normalize(str(item.get("address") or ""))
        if not name or not address or address in seen:
            continue
        seen.add(address)
        text = lambda k, n: " ".join(str(item.get(k) or "").split())[:n]  # noqa: E731
        out.append(Server(name, address, text("region", 32), text("contact", 120),
                          item.get("verified") is True, "main" if item.get("main") is True and not out else "listed",
                          text("id", 64)))
    return out


def list_url(server: str = PUBLIC_SERVER) -> str:
    base = http_base(server)
    return base + LIST_PATH if base else ""


def fetch_list(url: str | None = None, key: str | None = None, timeout=FETCH_TIMEOUT, now=None) -> list:
    """[Server] of the main server's signed list; [] when the launcher has no list key (nothing is
    fetched). ListError when it cannot be had or is not signed by the key."""
    key = LIST_KEY if key is None else key
    if not key:
        return []
    try:
        public = bytes.fromhex(key)
    except ValueError:
        raise ListError("the launcher's list key is damaged") from None
    url = url or list_url()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            body = r.read(MAX_LIST + 1)
    except OSError as e:
        if hasattr(e, "close"):
            e.close()  # an HTTPError holds its response
        raise ListError(f"could not get the server list ({getattr(e, 'reason', None) or e})") from None
    if len(body) > MAX_LIST:
        raise ListError("the server list is too large")
    try:
        document = json.loads(body)
    except ValueError:
        raise ListError("not a server list") from None
    return parse_servers(check_document(document, public, now))


def ping(address: str, timeout=PING_TIMEOUT) -> Status:
    """The server's /status: up, players online and the request's round trip in ms."""
    base = http_base(address)
    if not base:
        return Status(False, error="not an address")
    parts = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.connect()
        start = time.perf_counter()
        conn.request("GET", "/status", headers={"User-Agent": USER_AGENT})
        r = conn.getresponse()
        elapsed = time.perf_counter() - start
        body = r.read(MAX_STATUS)
        if r.status != 200:
            return Status(False, error=f"HTTP {r.status}")
        data = json.loads(body)
        if not isinstance(data, dict) or data.get("server") != "forza-live":
            return Status(False, error="not a Forza Live server")
        players = data.get("players")
        return Status(True, players if isinstance(players, int) and players >= 0 else None,
                      max(1, round(elapsed * 1000)))
    except (OSError, http.client.HTTPException, ValueError) as e:
        return Status(False, error=type(e).__name__ if not str(e) else str(e))
    finally:
        conn.close()


def ping_all(addresses, timeout=PING_TIMEOUT) -> dict:
    """{address: Status} of every address, pinged at the same time (This PC's is skipped)."""
    addresses = [a for a in dict.fromkeys(addresses) if a and a != LOCAL]
    if not addresses:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(addresses))) as pool:
        return dict(zip(addresses, pool.map(lambda a: ping(a, timeout), addresses)))


# --- What the page shows, and the player's choice -------------------------------------------------

def main_server(listed=()) -> Server:
    for s in listed:
        if s.kind == "main":
            return s
    return Server("Forza Live (main server)", PUBLIC_SERVER, verified=True, kind="main")


def entries(listed, added) -> list:
    """The page's rows: the main server, the listed servers, This PC, then the added addresses
    (each address once)."""
    main = main_server(listed)
    rows, seen = [main], {main.address, PUBLIC_SERVER}
    for s in listed:
        if s.address not in seen:
            rows.append(s)
            seen.add(s.address)
    rows.append(Server("This PC", LOCAL, kind="local"))
    for address in added:
        if address not in seen:
            rows.append(Server(address, address, kind="added"))
            seen.add(address)
    return rows


def added(cfg) -> list:
    items = cfg.online.get("added_servers")
    return [a for a in (normalize(x) for x in items if isinstance(x, str)) if a] if isinstance(items, list) else []


def page_addresses(cfg) -> list:
    """The addresses the page lists besides the main server's list: those added by hand, and the
    "My own server" address of Settings > Online when it is not one of them."""
    have = added(cfg)
    own = normalize(cfg.online.get("custom_server", ""))
    return have + [own] if own and own not in have and not is_main(own) else have


def add_address(cfg, text: str) -> str:
    """Adds a typed address to the page's list. Returns it as saved; ValueError when it is not
    host[:port]."""
    address = normalize(text)
    if not address:
        raise ValueError("Type a server address as host or host:port, for example example.org:21200.")
    have = added(cfg)
    if address not in have:
        cfg.online["added_servers"] = (have + [address])[-MAX_ADDED:]
    return address


def remove_address(cfg, address: str):
    cfg.online["added_servers"] = [a for a in added(cfg) if a != address]


def is_main(address: str) -> bool:
    return normalize(address) == normalize(PUBLIC_SERVER)


def chosen(cfg) -> str:
    """The address of the server Play uses: PUBLIC_SERVER, LOCAL, a custom address, or '' offline."""
    mode = cfg.online.get("mode")
    if mode == "public":
        return PUBLIC_SERVER
    if mode == "local":
        return LOCAL
    if mode == "custom":
        return normalize(cfg.online.get("custom_server", "")) or cfg.online.get("custom_server", "").strip()
    return ""


def choose(cfg, address: str):
    """Saves `address` as the server Play uses, as Settings > Online does."""
    if address == LOCAL:
        cfg.online["mode"] = "local"
    elif is_main(address):
        cfg.online["mode"] = "public"
    else:
        address = normalize(address)
        if not address:
            raise ValueError("not a server address")
        cfg.online["mode"], cfg.online["custom_server"] = "custom", address
