"""Crash reports use only disposable logs/configs; never run or read a player's game."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import crash_report, game
from forza_launcher.config import Config

FAULT = "[critical] [core] [t1] Unhandled read fault = guest 0x0AF048B8 (pc forza.exe+0x123 sub_82012340)"
CHAIN = "[error] [core] [t1] Guest call chain (return addresses): 82012340 82056780"
STACK = "[error] [core] [t1]   #0 forza.exe+0x456 sub_82012340"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root / "private-data")
        self.cfg.online.update(mode="custom", custom_server="private.example.test:21200")
        self.cfg.gpu.update(name=r"NVIDIA Test GPU PCI\VEN_10DE&UNIQUE_ID")
        (self.root / "version.txt").write_text("fixture-build-123")
        self.log = self.root / "crash.log"
        install = game.GameInstall(self.root, self.root / "forza")
        self.plan = game.LaunchPlan(install, self.root, ["forza", f"--log_file={self.log}", "--frame_rate=144"],
                                    {}, [], self.root / "forza.toml", "frame_rate = 120\n")

    def report(self):
        return crash_report.collect(self.plan, 134, crash_report.launch_context(self.cfg, self.plan))

    def test_fault_chain_build_and_launch_settings_survive_later_setting_changes(self):
        self.log.write_text("[info] Vulkan device 'Test GPU': driver version 0x1234\n" +
                            "\n".join((FAULT, STACK, CHAIN)))
        context = crash_report.launch_context(self.cfg, self.plan)
        self.cfg.settings["frame_rate"] = 30
        report = crash_report.collect(self.plan, 134, context)
        for text in ("Unhandled read fault", "0x0AF048B8", "82056780", "#0 forza.exe", "fixture-build-123",
                     "frame_rate = 144", "driver version 0x1234", "NVIDIA Test GPU", "0x00000086"):
            self.assertIn(text, report.body)
        self.assertIn("Unhandled read fault", report.title)
        self.assertNotIn("UNIQUE_ID", report.body)
        self.assertNotIn("private.example", report.body)

    def test_private_paths_profiles_endpoints_and_secrets_are_removed(self):
        self.cfg.profile.update(xenia_xuid="E000123456789ABC", xenia_content="D:/my-private-content")
        self.cfg.advanced.update(extra_env="PRIVATE_TOKEN=secret-value", extras_toml='private = "secret-value"')
        self.log.write_text("\n".join((FAULT, str(self.cfg.data_home()), "D:/my-private-content/save",
            r"C:\Users\Alice\Games\forza", "/home/bob/.local/data", "E000123456789ABC",
            "private.example.test:21200", "192.168.1.99:21200", "https://secret.test/key",
            "token=do-not-share", "password=also-private", "Authorization: Bearer private-bearer", "```injection")))
        report = self.report()
        for private in (str(self.root), "Alice", "bob", "E000123456789ABC", "private.example",
                        "192.168.1.99", "secret.test", "do-not-share", "also-private", "secret-value",
                        "my-private-content", "private-bearer", "```injection"):
            self.assertNotIn(private, report.body)
        self.assertIn("<private>", report.body)

    def test_missing_log_still_produces_usable_report(self):
        report = self.report()
        self.assertIn("could not be read", report.body)
        self.assertIn("Exit code: 134", report.body)
        self.assertIn("Not recorded", report.body)

    def test_native_tuning_is_recorded_at_launch_and_cli_overrides_config(self):
        self.cfg.settings["forza_car_tuning"] = True
        self.plan.toml_text += "forza_car_tuning = true\n"
        context = crash_report.launch_context(self.cfg, self.plan)
        self.cfg.settings["forza_car_tuning"] = False
        self.assertIs(context["settings"]["forza_car_tuning"], True)
        self.assertIn("forza_car_tuning = True", crash_report.collect(self.plan, 134, context).body)
        self.plan.argv.append("--forza_car_tuning=false")
        self.assertEqual(crash_report.launch_context(self.cfg, self.plan)["settings"]["forza_car_tuning"], "false")

    def test_known_internal_heap_stop_is_explained_and_raw_evidence_is_preserved(self):
        from test_game_exit import HEAP_FAILURE
        self.log.write_text("\n".join(HEAP_FAILURE))
        report = self.report()
        self.assertIn("## Internal heap allocation failure", report.body)
        self.assertIn("heap 0x21", report.body)
        self.assertIn("16,384 bytes", report.body)
        self.assertIn("r29=00004000", report.body)
        self.assertIn("82423108 82422E50", report.body)

    def test_quoted_secrets_and_authorization_values_are_removed_in_full(self):
        self.log.write_text(r'''{"token": "private token with spaces", "password": "private password"}
"Authorization": "Bearer private bearer"
apiKey='private API key'
refresh_token=private-refresh
Authorization: Bearer private-unquoted
{"client_secret": "private escaped \"quotes\" and spaces"}
''')
        report = self.report()
        for private in ('private token', 'private password', 'private bearer',
                        'private API key', 'private-refresh', 'private-unquoted', 'private escaped'):
            self.assertNotIn(private, report.body)
        self.assertIn('<private>', report.body)

    def test_large_log_is_bounded_and_preserves_final_fault(self):
        with self.log.open("wb") as f:
            f.write(b"old line\n" * 150000)
            f.write((FAULT + "\n" + CHAIN + "\n").encode())
        lines = game.read_log_tail(self.log, lines=20, max_bytes=4096)
        self.assertEqual(lines[-1], CHAIN)
        self.assertLessEqual(len("\n".join(lines).encode()), 4096)
        report = self.report()
        self.assertIn("82056780", report.body)
        self.assertLess(len(report.body), 75000)

    def test_relative_and_split_log_flag_uses_game_cwd(self):
        self.log.write_text(FAULT)
        self.plan.argv = ["forza", "--log_file", "crash.log"]
        self.assertIn("Unhandled read fault", self.report().body)
        self.assertIn("Unhandled read fault", game.crash_reason(self.plan, 134))

    def test_private_paths_match_either_separator(self):
        result = crash_report.redact(r"D:\private\game\data D:/private/game/data", ["D:/private/game"])
        self.assertNotIn("private/game", result)
        self.assertNotIn(r"private\game", result)

    def test_startup_failure_does_not_reuse_previous_runs_close_or_fault(self):
        self.log.write_text(FAULT + "\n[info] Execution complete\n")
        self.plan.log_before_start = game.log_signature(self.plan)
        self.assertTrue(game.exit_is_crash(self.plan, 1))
        report = self.report()
        self.assertIn("previous run's log has been excluded", report.body)
        self.assertNotIn("0x0AF048B8", report.body)
        self.log.write_text("New run\n" + FAULT + "\n" + CHAIN)
        self.assertIn("0x0AF048B8", self.report().body)

    def test_issue_link_roundtrips_unicode_and_query_characters(self):
        body = "GPU & settings = test\nsteps: café + 日本語 #fragment"
        url = crash_report.issue_url("Crash: test & check", body)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["body"], [body])
        self.assertEqual(query["title"], ["Crash: test & check"])
        self.assertEqual(query["labels"], ["bug"])
        self.assertTrue(url.startswith("https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/issues/new?"))
        self.assertIsNone(crash_report.issue_url("Crash", "日本語" * 5000))


try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.ui.crash_report import CrashReportDialog
        self.dialog = CrashReportDialog(crash_report.CrashReport("Crash: fixture", "fixture report", None))
        self.addCleanup(self.dialog.close)
        self.addCleanup(self.dialog.deleteLater)

    def test_open_is_explicit_and_uses_edited_report(self):
        with patch("forza_launcher.ui.crash_report.QDesktopServices.openUrl", return_value=True) as browser:
            self.dialog.show()
            self.app.processEvents()
            browser.assert_not_called()
            self.dialog.title.setText("Edited title")
            self.dialog.body.setPlainText("Edited report")
            self.dialog.github.click()
        query = parse_qs(urlparse(browser.call_args.args[0].toString()).query)
        self.assertEqual(query["body"], ["Edited report"])
        self.assertEqual(query["title"], ["Edited title"])

    def test_long_report_is_copied_intact_and_draft_explains_paste(self):
        body = "日本語\n" * 8000
        self.dialog.body.setPlainText(body)
        with patch("forza_launcher.ui.crash_report.QDesktopServices.openUrl", return_value=True) as browser:
            self.dialog.github.click()
        self.assertEqual(self.app.clipboard().text(), body)
        self.assertIn("paste it", self.dialog.notice.text())
        url = browser.call_args.args[0].toString()
        self.assertLessEqual(len(url), crash_report.MAX_ISSUE_URL)
        self.assertIn("Paste the crash report", parse_qs(urlparse(url).query)["body"][0])

    def test_browser_failure_preserves_report_and_offers_copy(self):
        with patch("forza_launcher.ui.crash_report.QDesktopServices.openUrl", return_value=False):
            self.dialog.github.click()
        self.assertIn("Could not open", self.dialog.notice.text())
        self.dialog.copy.click()
        self.assertEqual(self.app.clipboard().text(), "fixture report")

    def test_save_writes_reviewed_text_and_cancel_does_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "report.md"
            self.dialog.body.setPlainText("Reviewed report")
            with patch("forza_launcher.ui.crash_report.QFileDialog.getSaveFileName", return_value=(str(dest), "")):
                self.dialog.save.click()
            self.assertEqual(dest.read_text(encoding="utf-8"), "Reviewed report")
            with patch("forza_launcher.ui.crash_report.QFileDialog.getSaveFileName", return_value=("", "")):
                self.dialog.save.click()
            self.assertEqual(list(Path(temp).iterdir()), [dest])


if __name__ == "__main__":
    unittest.main()
