"""Online > Servers > Test connection: can this PC reach a Forza Live server the way the game does?

Three checks, off the GUI thread:
- the server's name, looked up for IPv4 as the game does (the game has no IPv6);
- its status page, GET /status on the HTTP port (the UDP port + 1, server_list.ping);
- the game's own traffic: a Forza Live datagram to the UDP port. The server answers a packet from an
  address it has no player for with error 1, "not registered" (forza-live server.py), so a BYE from
  this throwaway socket gets an answer without signing in anyone. Any answer proves UDP gets through
  both ways (server/tools/server_probe.py checks the same port with a HELLO).

The result is one of RESULTS, each with a one-line fix (message())."""
import concurrent.futures
import os
import socket
import time
from dataclasses import dataclass

from . import server_list
from .shader_share import DEFAULT_UDP_PORT

MAGIC = b"RLV1"
BYE = 10                # forza-live's packet kinds: HELLO 1 ... BYE 10
UDP_TRIES = 3
UDP_WAIT = 1.5          # seconds for each try's answer
STATUS_TIMEOUT = 5

REACHABLE, UDP_BLOCKED, WEB_BLOCKED, NO_DNS, DOWN, NOT_AN_ADDRESS = (
    "reachable", "udp_blocked", "web_blocked", "no_dns", "down", "not_an_address")
RESULTS = (REACHABLE, UDP_BLOCKED, WEB_BLOCKED, NO_DNS, DOWN, NOT_AN_ADDRESS)


@dataclass
class Result:
    kind: str               # one of RESULTS
    address: str
    host: str = ""
    port: int = 0           # the UDP port
    ping_ms: int | None = None
    detail: str = ""        # the status page's error, the lookup's error


def split(address: str):
    """(host, UDP port) of a host[:port] address; None when it is not one."""
    address = (address or "").strip()
    if not server_list.normalize(address):
        return None
    host, port = address.split(":") if address.count(":") == 1 else (address, "")
    if host.startswith("["):   # an IPv6 literal: the game has no IPv6, so it does not resolve
        host, _, port = host[1:].partition("]")
        port = port.lstrip(":")
    return host, int(port) if port else DEFAULT_UDP_PORT


def resolve(host: str, port: int) -> str:
    """The server's IPv4 address, as the game looks it up. OSError (socket.gaierror) when it has none."""
    return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]


def udp_answers(ip: str, port: int, tries=UDP_TRIES, wait=UDP_WAIT) -> bool:
    """Whether the server answers a Forza Live datagram on its UDP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(wait)
        for _ in range(tries):
            try:
                s.sendto(MAGIC + bytes([BYE]), (ip, port))
                until = time.monotonic() + wait
                while time.monotonic() < until:
                    data, _ = s.recvfrom(2048)
                    if data[:4] == MAGIC:
                        return True
            except socket.timeout:
                continue
            except OSError:   # Windows: an ICMP "port unreachable" from the last send; try again
                time.sleep(min(wait, 0.3))
                continue
    return False


def classify(udp_ok: bool, status_up: bool) -> str:
    """The result of a name that resolved: what answered."""
    if udp_ok:
        return REACHABLE if status_up else WEB_BLOCKED
    return UDP_BLOCKED if status_up else DOWN


def run(address: str, resolve=resolve, ping=server_list.ping, udp=udp_answers) -> Result:
    """Tests `address` (host[:port]). The callables are the network (tests replace them)."""
    parts = split(address)
    if parts is None:
        return Result(NOT_AN_ADDRESS, address)
    host, port = parts
    try:
        ip = resolve(host, port)
    except OSError as e:   # socket.gaierror, UnicodeError's cousins come as OSError too
        return Result(NO_DNS, address, host, port, detail=str(e) or type(e).__name__)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        status = pool.submit(ping, address, STATUS_TIMEOUT)
        answered = pool.submit(udp, ip, port)
        st, udp_ok = status.result(), answered.result()
    return Result(classify(udp_ok, st.up), address, host, port,
                  st.ping_ms if st.up else None, "" if st.up else st.error)


def _game():
    return "forza.exe" if os.name == "nt" else "the game"


def message(r: Result) -> tuple:
    """(headline, the fix in one line) for the page."""
    if r.kind == REACHABLE:
        ping = f" ({r.ping_ms} ms)" if r.ping_ms else ""
        return (f"Reachable{ping}: the server answers the game's traffic and its status page.",
                "Nothing to fix: the game signs in when you press Play.")
    if r.kind == UDP_BLOCKED:
        return (f"UDP blocked: the server is up, but the game's traffic (UDP port {r.port}) gets no answer.",
                f"Allow {_game()} in your firewall and antivirus, turn off any VPN or proxy, or try a "
                "phone hotspot (some networks and ISPs block it).")
    if r.kind == WEB_BLOCKED:
        return (f"The game's traffic gets through, but the server's web port (TCP {r.port + 1}) does not answer.",
                f"Allow {_game()} in your firewall and antivirus and turn off any VPN or proxy: leaderboards, "
                "friends and sign-in use that port.")
    if r.kind == NO_DNS:
        return (f"Name doesn't resolve: this PC can't look up {r.host}.",
                "A DNS filter (router parental controls, Pi-hole, AdGuard, NextDNS or an antivirus) may block "
                "it: allow the name or switch to a public DNS. Or this PC is not connected.")
    if r.kind == DOWN:
        return ("Server down: neither its game port nor its status page answers.",
                "Try again in a few minutes or pick another server. If every server is down, check this "
                "PC's internet connection.")
    return ("Not a server address.", "Type it as host or host:port, for example example.org:21200.")
