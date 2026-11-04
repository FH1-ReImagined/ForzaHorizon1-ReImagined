"""Daily and weekly challenges (forza-live #1): the Play page's card of the main server's current
objectives and the player's progress.

The Forza Live server sets three objectives a day and three a week, the same for every player, and
counts each player's progress from the results their game sends while signed in
(server/forza_live/competition/objectives.py): races won or finished, speed traps and zones, 1000
Club medals, big shots. Only the main server's challenges are shown: a community server may run its
own, and nothing a player does on one counts on the main server. The main server may also send a
weekly credits gift through the game's Message Center for finished challenges; the card says how
far the player is.

The card asks `GET /challenges/progress` of the main server, signed like the co-op results
(live_auth: the XUID and a signature, never a key), when Server is "Public server", Settings >
Online > "Daily and weekly challenges" is on (the "Online features" choice turns it off) and this
PC has a Forza Live identity (the game makes it at its first online start). Otherwise nothing is
sent."""
from dataclasses import dataclass
import json
import time

from . import PUBLIC_SERVER, live_auth, shader_share

PATH = "/challenges/progress"
FETCH_TIMEOUT = 6
FETCH_EVERY = 300          # seconds between two fetches while the card stays open
MAX_BYTES = 256 << 10
MAX_SETS = 4
MAX_OBJECTIVES = 8
MAX_TITLE = 120
KIND_NAMES = {"daily": "Today", "weekly": "This week"}


@dataclass(frozen=True)
class Objective:
    title: str
    target: int
    progress: int
    done: bool
    completed: int         # players who finished it


@dataclass(frozen=True)
class ChallengeSet:
    kind: str
    ends: int
    objectives: tuple


def enabled(cfg) -> bool:
    """Settings > Online > "Daily and weekly challenges" (the "Online features" choice turns it off)."""
    return cfg.settings.get("challenges", True) is True


def state(cfg) -> str:
    """What the card does: 'hidden' (Offline, This PC), 'other' (My own server: challenges count on
    the main server only), 'off' (the setting is off) or 'on' (it may ask the main server)."""
    mode = cfg.online.get("mode")
    if mode not in ("public", "custom"):
        return "hidden"
    if mode == "custom":
        return "other"
    return "on" if enabled(cfg) else "off"


def base() -> str:
    """The main server's title services."""
    return shader_share.http_base(PUBLIC_SERVER)


def identity(cfg):
    return shader_share.read_identity(shader_share.identity_dirs(cfg))


def fetch(url_base, ident, timeout=FETCH_TIMEOUT) -> dict:
    with live_auth.urlopen(url_base + PATH, ident, timeout=timeout) as r:
        data = json.loads(r.read(MAX_BYTES) or b"{}")
    if not isinstance(data, dict):
        raise ValueError("not a challenges document")
    return data


def _int(v, lo=0, hi=1 << 40):
    return v if type(v) is int and lo <= v <= hi else None


def parse(doc) -> list:
    """The sets of a /challenges(/progress) answer that check out; objectives with a bad field are
    left out, text is cut to MAX_TITLE characters and shown as plain text."""
    out = []
    sets = doc.get("sets") if isinstance(doc, dict) else None
    for s in sets if isinstance(sets, list) else []:
        if not isinstance(s, dict) or s.get("kind") not in KIND_NAMES or _int(s.get("ends")) is None:
            continue
        objectives = []
        for o in s.get("objectives") if isinstance(s.get("objectives"), list) else []:
            if not isinstance(o, dict) or not isinstance(o.get("title"), str):
                continue
            target = _int(o.get("target"), 1, 1 << 20)
            if target is None:
                continue
            progress = _int(o.get("progress", 0), 0, 1 << 20)
            title = " ".join(o["title"].split())[:MAX_TITLE]
            if progress is None or not title:
                continue
            done = _int(o.get("done")) is not None or progress >= target
            objectives.append(Objective(title, target, min(progress, target), done,
                                        _int(o.get("completed", 0)) or 0))
        if objectives:
            out.append(ChallengeSet(s["kind"], s["ends"], tuple(objectives[:MAX_OBJECTIVES])))
    return out[:MAX_SETS]


def gift_text(doc) -> str:
    """The weekly gift's line when the server sends one (objectives.py --daily-challenges-gifts):
    how far the player is, or that this week's was sent; '' without one."""
    gift = doc.get("gift") if isinstance(doc, dict) else None
    mine = (doc.get("player") or {}).get("gift") if isinstance(doc.get("player"), dict) else None
    if not isinstance(gift, dict) or not isinstance(mine, dict):
        return ""
    credits, after = _int(gift.get("credits"), 1, 10 ** 7), _int(gift.get("after"), 1, 100)
    finished = _int(mine.get("finished", 0), 0, 10 ** 4)
    if credits is None or after is None or finished is None:
        return ""
    if _int(mine.get("sent")) is not None:
        return f"This week's {credits:,} CR gift was sent: collect it in the game's Message Center."
    return (f"Finish {after} challenges this week, daily or weekly, for a {credits:,} CR gift in the "
            f"Message Center: {min(finished, after)} of {after} so far.")


def time_left(ends, now=None) -> str:
    """'5 h 12 min left', '3 days left', 'ends now'."""
    left = int(ends - (time.time() if now is None else now))
    if left <= 60:
        return "ends now"
    if left >= 2 * 86400:
        return f"{left // 86400} days left"
    if left >= 3600:
        return f"{left // 3600} h {left % 3600 // 60} min left"
    return f"{left // 60} min left"


def load(cfg, fetch_fn=None):
    """(sets, note, gift line) for the card: the main server's sets with this player's progress,
    or no sets and why. Runs off the GUI thread. Sends nothing unless state(cfg) is 'on' and the PC
    has a Forza Live identity."""
    if state(cfg) != "on":
        return [], "", ""
    ident = identity(cfg)
    if ident is None:
        return [], ("Play online once to take part: the game makes the Forza Live identity your "
                    "progress is counted for."), ""
    try:
        doc = (fetch_fn or fetch)(base(), ident)
    except (OSError, ValueError) as e:   # URLError and HTTPError are OSErrors
        code = getattr(e, "code", None)
        if code == 403:
            return [], ("The main server does not know this PC's Forza Live identity yet: play "
                        "online once, then your progress shows here."), ""
        if code == 404:
            return [], "The main server has no challenges at the moment.", ""
        return [], "The main server is not answering. The challenges show when it is back.", ""
    sets = parse(doc)
    if not sets:
        return [], "The main server has no challenges at the moment.", ""
    return sets, "", gift_text(doc)
