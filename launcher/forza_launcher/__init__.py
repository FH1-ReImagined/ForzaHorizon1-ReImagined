"""Forza Horizon 1: ReImagined launcher."""
from ._version import __version__

APP_NAME = "Forza Horizon 1: ReImagined"
# The public Forza Live server, reached through a playit.gg tunnel (UDP here, title services
# over TCP on the next port). Earlier addresses are switched to it when a config names them.
PUBLIC_SERVER = "forza-reimagined.playit.plus:56677"
OLD_PUBLIC_SERVERS = ("dyotserver.ddns.net:21200",)
GITHUB_URL = "https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined"
# What the launcher, the game and the public server send and keep (#497).
PRIVACY_URL = GITHUB_URL + "/blob/main/PRIVACY.md"
TITLE_ID = "4D5309C9"
__all__ = ["__version__", "APP_NAME", "PUBLIC_SERVER", "OLD_PUBLIC_SERVERS", "GITHUB_URL", "PRIVACY_URL",
           "TITLE_ID"]
