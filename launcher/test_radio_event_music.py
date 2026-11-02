"""The opt-in event radio policy is saved for the native game and requires restart."""
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from forza_launcher import config,schema
class EventRadioSettingTest(unittest.TestCase):
    def test_original_default_is_omitted(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config.Config(Path(d)/'launcher.toml')
            self.assertFalse(cfg.settings['radio_keep_in_events'])
            self.assertNotIn('radio_keep_in_events',config.render_toml(cfg))
    def test_opt_in_reaches_game_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config.Config(Path(d)/'launcher.toml');cfg.settings['radio_keep_in_events']=True
            self.assertIn('radio_keep_in_events = true',config.render_toml(cfg).splitlines())
    def test_policy_is_radio_setting_with_restart(self):
        setting=schema.BY_KEY['radio_keep_in_events']
        self.assertEqual((setting.kind,setting.group,setting.section),('bool','Audio','Radio'))
        self.assertTrue(setting.restart);self.assertTrue(setting.applies())
if __name__=='__main__':unittest.main()
