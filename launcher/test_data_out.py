"""Per-session opt-in output settings and launcher-only serialization."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import config, data_out, schema


class DataOutSettings(unittest.TestCase):
    def test_off_overrides_inherited_destination(self):
        env = data_out.environment({})
        self.assertEqual(env['FORZA_DATA_OUT_ENABLE'], '0')
        self.assertEqual(env['FORZA_DATA_OUT_IP'], '127.0.0.1')

    def test_numeric_lan_destination_and_matching_format(self):
        env = data_out.environment(dict(data_out=True, data_out_ip='192.168.1.2', data_out_port=12345,
                                        data_out_rate=30, data_out_format='fm7'))
        self.assertEqual(env, dict(FORZA_DATA_OUT_ENABLE='1', FORZA_DATA_OUT_IP='192.168.1.2',
                                   FORZA_DATA_OUT_PORT='12345', FORZA_DATA_OUT_RATE='30', FORZA_DATA_OUT_FORMAT='fm7'))

    def test_rejects_ambiguous_destinations_and_invalid_ranges(self):
        for values in ({'data_out_ip': 'example.com'}, {'data_out_ip': '::1'}, {'data_out_port': 0},
                       {'data_out_port': 65536}, {'data_out_rate': 0}, {'data_out_rate': 61},
                       {'data_out_format': 'x'}, {'data_out_ip': '255.255.255.255'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                data_out.environment({'data_out': True, **values})

    def test_settings_never_become_unknown_game_cvars(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = config.Config(Path(folder) / 'launcher.json')
            cfg.settings.update(data_out=True, data_out_ip='192.168.1.2', data_out_port=12345,
                                data_out_rate=30, data_out_format='fm7')
            text = config.render_toml(cfg)
            self.assertNotIn('data_out', text)
            self.assertTrue(all(s.restart for s in schema.S if s.key.startswith('data_out')))

    def test_old_game_build_cannot_silently_ignore_enabled_export(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'Rebuild'):
                data_out.environment({'data_out': True}, folder)
            (Path(folder) / 'forza-telemetry.json').write_text('{"data_out":1}')
            self.assertEqual(data_out.environment({'data_out': True}, folder)['FORZA_DATA_OUT_ENABLE'], '1')


if __name__ == '__main__':
    unittest.main()
