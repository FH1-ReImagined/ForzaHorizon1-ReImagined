"""The Game language choice (#434) sets the game's Language ID and Country ID together."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, languages, schema

# What the game opens (game:\media\stringtables\<folder>.zip) for each (Language ID, Country ID),
# read from its XGetLanguage/XGetLocale tables and confirmed by running it (docs/launcher.md).
EXPECTED = {
    "en": (1, 103, "EN"), "en-gb": (1, 35, "GB"), "ja": (2, 103, "JP"), "de": (3, 103, "DE"),
    "fr": (4, 103, "FR"), "es": (5, 31, "ES"), "es-mx": (5, 71, "MX"), "it": (6, 103, "IT"),
    "ko": (7, 103, "KO"), "zh-tw": (8, 101, "CHT"), "pt-br": (9, 103, "BR"), "pl": (11, 103, "PL"),
    "ru": (12, 103, "RU"), "cs": (1, 23, "CZ"), "hu": (1, 42, "HU"), "nl": (1, 74, "NL"),
    "da": (1, 25, "DA"), "fi": (1, 32, "FI"), "nb": (1, 75, "NB"), "sv": (1, 90, "SV"),
}


class GameLanguageTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def lines(self, cfg):
        return config.render_toml(cfg).splitlines()

    def test_automatic_by_default_and_changes_nothing(self):
        setting = schema.BY_KEY["game_language"]
        self.assertEqual((setting.kind, setting.default, setting.group), ("choice", "", "Profile"))
        cfg = self.config()
        text = config.render_toml(cfg)
        self.assertNotIn("game_language", text)
        self.assertNotIn("user_language", text)
        self.assertNotIn("user_country", text)

    def test_saved_ids_still_apply_when_automatic(self):
        cfg = self.config()
        cfg.settings.update(user_language=3, user_country=42)
        lines = self.lines(cfg)
        self.assertIn("user_language = 3", lines)
        self.assertIn("user_country = 42", lines)

    def test_every_language_writes_its_pair(self):
        for value, (lang, country, _folder) in EXPECTED.items():
            cfg = self.config()    # the player's own IDs: 1 and 103
            cfg.settings["game_language"] = value
            toml = config.parse_toml(config.render_toml(cfg))
            self.assertEqual(toml.get("user_language", 1), lang, value)
            self.assertEqual(toml.get("user_country", 103), country, value)

    def test_table_and_folders(self):
        for value, (lang, country, folder) in EXPECTED.items():
            got = languages.BY_VALUE[value]
            self.assertEqual(got[0], lang, value)
            self.assertEqual(got[1], None if value in ("ja", "de", "fr", "it", "ko", "pt-br", "pl", "ru") else country, value)
            self.assertEqual(got[2], folder, value)
        self.assertEqual({v: languages.table_folder(v) for v in EXPECTED}, {v: e[2] for v, e in EXPECTED.items()})
        self.assertEqual(languages.table_folder(""), "")

    def test_hungarian_is_english_with_the_country(self):
        cfg = self.config()
        cfg.settings["game_language"] = "hu"
        lines = self.lines(cfg)
        self.assertEqual(languages.resolve("hu", 5, 7), (1, 42))
        self.assertNotIn("user_language", config.render_toml(cfg))
        self.assertIn("user_country = 42", lines)

    def test_languages_without_a_country_keep_the_players_country(self):
        cfg = self.config()
        cfg.settings.update(game_language="de", user_country=42)
        lines = self.lines(cfg)
        self.assertIn("user_language = 3", lines)
        self.assertIn("user_country = 42", lines)
        self.assertEqual(languages.resolve("de", 1, 42), (3, 42))

    def test_unknown_choice_is_automatic(self):
        cfg = self.config()
        cfg.settings.update(game_language="xx", user_language=3)
        self.assertEqual(schema.coerce(schema.BY_KEY["game_language"], "xx"), "")
        self.assertEqual(languages.resolve("xx", 3, 42), (3, 42))

    def test_choices_use_native_names(self):
        labels = dict(schema.BY_KEY["game_language"].choices)
        self.assertEqual(labels["hu"], "Magyar")
        self.assertEqual(labels["cs"], "Čeština")
        self.assertEqual(labels["nl"], "Nederlands")
        self.assertEqual(len(labels), len(EXPECTED) + 1)

    def test_the_game_has_a_table_for_every_folder(self):
        root = Path(__file__).resolve().parents[1] / "extracted" / "game" / "media" / "stringtables"
        if not root.is_dir():
            self.skipTest("no extracted game")
        # The disc names some tables in lower case (br.zip, cz.zip, nl.zip); the game's file
        # lookups ignore case, so compare that way.
        have = {p.name.lower() for p in root.glob("*.zip")}
        for value, (_l, _c, folder) in EXPECTED.items():
            self.assertIn(f"{folder}.zip".lower(), have, folder)


if __name__ == "__main__":
    unittest.main()
