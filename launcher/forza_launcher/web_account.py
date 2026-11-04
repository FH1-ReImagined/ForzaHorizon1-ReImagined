"""Linking the player's game to a website account on forza.dyotson.dev (website #4, M2).

A website account (Discord, GitHub or an emailed code) is optional. Linking it to the game is its
second factor: only a linked account can do what later website features need (likes, uploads).
The launcher starts the link, the browser claims it for the signed-in account, and the launcher
confirms the account it was shown. Both sides approve every link; nothing typed or shown alone
can link a game.

Every call here is signed with the game's own key (live_auth.py: the signing_seed of
live_identity.txt) and goes to the website's accounts service, whose address is built in and never
taken from the server setting. forza.dyotson.dev is one of live_auth.MAIN_NAMES, so no friends key
or derived key is ever sent. HTTPS goes through fetch.urlopen (the operating system's
certificates on Windows and macOS). The service's API is forza-live's accounts/README.md.

  start(identity)              -> {"id", "url", "code", "expires"}
  poll(identity, id)           -> {"state", "account", "replaces"}
  wait(identity, id, ...)      polls every POLL_SECONDS until the request is no longer pending
  confirm(identity, id, acct)  links the game to the account the player was shown
  cancel(identity, id)         "Not me"
  account(identity)            -> {"linked", "state", "account"}
  unlink(identity), signout(identity)
"""
import json
import time
import urllib.error

from . import fetch, live_auth

SERVICE = "https://forza.dyotson.dev/api/"
SITE = "forza.dyotson.dev/account.html"  # where a code is typed on another device
POLL_SECONDS = 3
RETRY_SECONDS = 3  # a key bound a moment ago reaches the service within about 2 s
TIMEOUT = 15
METHODS = {"discord": "Discord", "github": "GitHub", "email": "email"}

MESSAGES = {
    "refused": "The public server does not know this game's key yet. Play online on the public "
               "server once with this version of the game, then try again.",
    "play online first": "Play online on the public server once, then try again.",
    "not available": "This game cannot link a website account.",
    "changed in the past day": "This game's website link changed in the past day. Try again tomorrow.",
    "too many requests": "Too many tries. Wait a few minutes, then try again.",
    "too many links today": "Too many games were linked today. Try again tomorrow.",
    "not claimed": "The link is no longer waiting for you. Start again.",
    "the account does not match": "The website account changed. Start again.",
    "the key changed": "This game's key changed. Start again.",
    "this link needs the operator": "This game's website link was made by another installation. "
                                    "To get it back, ask on Discord.",
    "not linked": "This game is not linked to a website account.",
    "not found": "The link is over. Start again.",
    "linking is not available": "Website accounts are not available right now. Try again later.",
}


class WebAccountError(Exception):
    """A call the service refused or could not answer; str() is the player's message."""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def _error(e):
    try:
        doc = json.loads(e.read(1 << 16) or b"{}")
    except (OSError, ValueError):
        doc = {}
    finally:
        e.close()
    code = doc.get("error") if isinstance(doc, dict) and isinstance(doc.get("error"), str) else ""
    return WebAccountError(MESSAGES.get(code) or f"The website answered {e.code}. Try again later.", code)


def call(identity, path, body=None, base=SERVICE, opener=None):
    """The service's JSON answer to a signed call (a POST when `body` is given), or
    WebAccountError."""
    data = None if body is None else json.dumps(body).encode()
    try:
        with live_auth.urlopen(base + path, identity, data, timeout=TIMEOUT, opener=opener or fetch.urlopen) as r:
            doc = json.loads(r.read(1 << 20) or b"{}")
    except urllib.error.HTTPError as e:
        raise _error(e) from None
    except fetch.CertificateError as e:
        raise WebAccountError(str(e)) from None
    except (OSError, ValueError) as e:
        raise WebAccountError(f"Could not reach {base.split('/')[2]}: {getattr(e, 'reason', e)}") from None
    if not isinstance(doc, dict):
        raise WebAccountError("The website's answer was not understood. Try again later.")
    return doc


def start(identity, base=SERVICE, opener=None, sleep=time.sleep):
    """A new link request. A refused signature is tried once more after RETRY_SECONDS: the game
    may have bound its key on the game server a moment ago."""
    try:
        doc = call(identity, "game/link/start", {"v": 1}, base, opener)
    except WebAccountError as e:
        if e.code != "refused":
            raise
        sleep(RETRY_SECONDS)
        doc = call(identity, "game/link/start", {"v": 1}, base, opener)
    if not all(isinstance(doc.get(k), str) for k in ("id", "url", "code")) or \
            not doc["url"].startswith(base.rsplit("/api/", 1)[0] + "/"):
        raise WebAccountError("The website's answer was not understood. Try again later.")
    return doc


def poll(identity, rid, base=SERVICE, opener=None):
    return call(identity, f"game/link/{rid}", None, base, opener)


def wait(identity, rid, cancelled=lambda: False, base=SERVICE, opener=None, sleep=time.sleep,
         seconds=POLL_SECONDS, limit=None):
    """The request once it is no longer pending (claimed, declined, expired...), or None when
    cancelled() turns true first."""
    deadline = None if limit is None else time.monotonic() + limit
    while not cancelled():
        doc = poll(identity, rid, base, opener)
        if doc.get("state") != "pending":
            return doc
        if deadline is not None and time.monotonic() > deadline:
            return {"state": "expired"}
        for _ in range(int(seconds * 10)):
            if cancelled():
                return None
            sleep(0.1)
    return None


def confirm(identity, rid, account_id, base=SERVICE, opener=None):
    return call(identity, f"game/link/{rid}/confirm", {"account": account_id}, base, opener)


def cancel(identity, rid, base=SERVICE, opener=None):
    return call(identity, f"game/link/{rid}/cancel", {}, base, opener)


def account(identity, base=SERVICE, opener=None):
    return call(identity, "game/account", None, base, opener)


def unlink(identity, base=SERVICE, opener=None):
    return call(identity, "game/unlink", {}, base, opener)


def signout(identity, base=SERVICE, opener=None):
    return call(identity, "game/signout", {}, base, opener)


# --- What the player reads ---------------------------------------------------------------------

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def shown_date(iso):
    """'2026-11-02' -> '2 Nov 2026' (in English, as the rest of the launcher)."""
    try:
        t = time.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return f"{t.tm_mday} {MONTHS[t.tm_mon - 1]} {t.tm_year}"


def describe(acct):
    """'K7Q2-XM4P (Discord, created 2 Nov 2026)'."""
    if not isinstance(acct, dict):
        return ""
    method = METHODS.get(acct.get("method"), acct.get("method") or "")
    created = shown_date(acct.get("created"))
    detail = ", ".join(x for x in (method, f"created {created}" if created else "") if x)
    return f"{acct.get('id', '')} ({detail})" if detail else str(acct.get("id", ""))


def confirm_question(doc):
    """The launcher's question once the browser claimed the link."""
    text = (f"Link this game to website account {describe(doc.get('account'))}? Only if you just "
            "signed in to it yourself.")
    if doc.get("replaces"):
        text += f" This moves the link from account {doc['replaces']}."
    return text


def status_text(doc):
    """The Website account row's text for account()'s answer."""
    if not doc.get("linked"):
        return "Not linked. Link a website account to use the website's features for players."
    if doc.get("state") == "frozen":
        return MESSAGES["this link needs the operator"]
    return f"Linked to website account {describe(doc.get('account'))}."
