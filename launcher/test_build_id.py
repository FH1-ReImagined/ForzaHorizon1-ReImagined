"""The build the game names to the Forza Live server (X-Forza-Build, #517): the launcher passes its version to
CMake as FORZA_BUILD_ID. Run: python3 -m unittest launcher/test_build_id.py"""
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import builder  # noqa: E402


class BuildId(unittest.TestCase):
    def test_the_launcher_version_is_the_build(self):
        with mock.patch.object(builder, "__version__", "0.2.1"):
            self.assertEqual(builder.build_id_arg(), "-DFORZA_BUILD_ID=0.2.1")

    def test_odd_versions_keep_to_the_characters_the_server_keeps(self):
        with mock.patch.object(builder, "__version__", "0.2.1 dev/\"x\"" + "y" * 60):
            arg = builder.build_id_arg()
        self.assertRegex(arg, r"^-DFORZA_BUILD_ID=[A-Za-z0-9._+-]{1,40}$")
        self.assertTrue(arg.startswith("-DFORZA_BUILD_ID=0.2.1_dev_"))

    def test_every_build_passes_it(self):
        self.assertIn("build_id_arg()", inspect.getsource(builder._run_build))


if __name__ == "__main__":
    unittest.main()
