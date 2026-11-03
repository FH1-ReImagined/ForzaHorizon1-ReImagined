"""The game language choice (#434): one setting that sets Language ID and Country ID together.

The game picks its string table (game:\\media\\stringtables\\<folder>.zip) from two Xbox
settings, not from the language alone (docs/launcher.md, "Game language"): XC_LANGUAGE
(user_language) and XC_COUNTRY (user_country). The Xbox dashboard has twelve languages, but the
game's own table maps (language, country) to a locale tag ("hu-hu") and the tag to a folder
("HU"). English plus the country picks the languages the dashboard lacks: Hungarian, Czech,
Dutch, Danish, Finnish, Norwegian and Swedish.
"""

# value -> (label in the language itself, Language ID, Country ID or None, table folder)
# A Country ID of None keeps the player's own Country ID: the language alone picks the folder.
LANGUAGES = [
    ("en", "English", 1, 103, "EN"),
    ("en-gb", "English (United Kingdom)", 1, 35, "GB"),
    ("ja", "日本語", 2, None, "JP"),
    ("de", "Deutsch", 3, None, "DE"),
    ("fr", "Français", 4, None, "FR"),
    ("es", "Español (España)", 5, 31, "ES"),
    ("es-mx", "Español (Latinoamérica)", 5, 71, "MX"),
    ("it", "Italiano", 6, None, "IT"),
    ("ko", "한국어", 7, None, "KO"),
    ("zh-tw", "繁體中文", 8, 101, "CHT"),
    ("pt-br", "Português (Brasil)", 9, None, "BR"),
    ("pl", "Polski", 11, None, "PL"),
    ("ru", "Русский", 12, None, "RU"),
    ("cs", "Čeština", 1, 23, "CZ"),
    ("hu", "Magyar", 1, 42, "HU"),
    ("nl", "Nederlands", 1, 74, "NL"),
    ("da", "Dansk", 1, 25, "DA"),
    ("fi", "Suomi", 1, 32, "FI"),
    ("nb", "Norsk", 1, 75, "NB"),
    ("sv", "Svenska", 1, 90, "SV"),
]

CHOICES = [("", "Automatic (Language ID and Country ID)")] + [(v, label) for v, label, *_ in LANGUAGES]
BY_VALUE = {v: (lang, country, folder) for v, _label, lang, country, folder in LANGUAGES}


def resolve(game_language, language_id, country_id):
    """(Language ID, Country ID) to give the game. An empty or unknown choice passes the
    player's own two IDs through unchanged."""
    entry = BY_VALUE.get(game_language)
    if entry is None:
        return language_id, country_id
    lang, country, _folder = entry
    return lang, country_id if country is None else country


def table_folder(game_language):
    """The string table folder the game opens for a choice, or '' for automatic."""
    entry = BY_VALUE.get(game_language)
    return entry[2] if entry else ""
