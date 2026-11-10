"""Tests of building in a folder whose path CMake cannot build under (issue #691): a data folder
such as D:\\[Games] Recomps\\Forza Horizon is built through a link to it without [ ] ; in its path
(python3 -m unittest launcher/test_cmake_unsafe_path.py from the repository root)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from forza_launcher import builder  # noqa: E402


class Quiet(builder.Reporter):
    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)


class Tools:
    cc = cxx = cmake = ninja = "x"
    source = {"cmake": "downloaded"}


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.links = self.tmp / "links"
        patcher = mock.patch.object(builder, "link_home", lambda: self.links)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.tmp / "[Games] Recomps" / "Forza Horizon"
        (self.root / "build").mkdir(parents=True)

    def _cleanup(self):
        # The links first, so that removing the folder never goes through one of them.
        if self.links.is_dir():
            for p in self.links.iterdir():
                if builder._is_link(p):
                    builder._remove_link(p)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unsafe_characters(self):
        self.assertEqual(builder.cmake_unsafe_chars(r"D:\[Games] Recomps\Forza Horizon"), "[]")
        self.assertEqual(builder.cmake_unsafe_chars("/home/a;b/[x]/[y]"), ";[]")
        self.assertEqual(builder.cmake_unsafe_chars(r"C:\Games\Forza Horizon (1) #2 & more"), "")
        self.assertEqual(builder.cmake_unsafe_chars("/data/100%"), "%" if builder.IS_WIN else "")

    def test_a_safe_folder_is_built_where_it_is(self):
        plain = self.tmp / "Forza Horizon"
        plain.mkdir()
        rep = Quiet()
        self.assertEqual(builder.build_root(plain, rep), plain)
        self.assertFalse(self.links.exists())
        self.assertEqual(rep.lines, [])

    def test_a_folder_with_brackets_is_built_through_a_link(self):
        rep = Quiet()
        link = builder.build_root(self.root, rep)
        self.assertNotEqual(link, self.root)
        self.assertEqual(link.parent, self.links)
        self.assertEqual(builder.cmake_unsafe_chars(link), "")
        self.assertTrue(builder._is_link(link))
        (link / "build" / "made-through-the-link").write_text("x")
        self.assertTrue((self.root / "build" / "made-through-the-link").is_file())   # the folder's own files
        self.assertIn("[ ]", rep.lines[0])
        self.assertIn(str(link), rep.lines[0])
        self.assertEqual(builder.build_root(self.root, Quiet()), link)   # kept for the next build

    def test_a_link_to_another_folder_is_remade(self):
        link = builder.build_root(self.root, Quiet())
        builder._remove_link(link)
        other = self.tmp / "other"
        (other / "keep").mkdir(parents=True)
        builder._make_link(other, link)
        self.assertEqual(builder.build_root(self.root, Quiet()), link)
        self.assertEqual(os.path.normcase(os.path.realpath(link)), os.path.normcase(os.path.realpath(self.root)))
        self.assertTrue((other / "keep").is_dir())   # only the link went

    def test_a_folder_in_the_way_is_left_alone(self):
        link = builder.build_root(self.root, Quiet())
        builder._remove_link(link)
        (link / "mine").mkdir(parents=True)
        with self.assertRaises(builder.BuildError) as e:
            builder.build_root(self.root, Quiet())
        self.assertIn("Choose a folder without these characters", str(e.exception))
        self.assertTrue((link / "mine").is_dir())

    def test_run_build_goes_through_the_link_and_answers_with_the_folder(self):
        seen = {}

        def fake(opts, rep, game_files):
            seen["root"] = opts.root
            return opts.root / "runtime"

        with mock.patch.object(builder, "_run_build", fake), \
                mock.patch.object(builder.datafolder, "check_folder", lambda *a, **k: []):
            rt = builder.run_build(builder.Options(self.root, jobs=1), Quiet(), self.root / "game")
        self.assertEqual(seen["root"].parent, self.links)
        self.assertEqual(rt, self.root / "runtime")
        self.assertEqual(builder.CURRENT_ROOT, self.root)   # messages name the folder itself

    def test_the_stamp_names_the_link_only(self):
        stamp = builder.configure_stamp(Tools, "sdk", False)
        self.assertEqual(stamp, builder.configure_stamp(Tools, "sdk", False, self.tmp / "plain"))
        link = builder.build_root(self.root, Quiet())
        self.assertNotEqual(stamp, builder.configure_stamp(Tools, "sdk", False, link))

    @unittest.skipUnless(shutil.which("cmake"), "no cmake")
    def test_cmake_globs_through_the_link(self):
        # What #691 is: file(GLOB) on a folder with [ ] in its path finds nothing.
        (self.root / "rexglueTargets-release.cmake").write_text("")
        script = self.root / "glob.cmake"
        script.write_text('file(GLOB f "${CMAKE_CURRENT_LIST_DIR}/rexglueTargets-*.cmake")\n'
                          'list(LENGTH f n)\nmessage("found ${n}")\n')

        def found(folder):
            r = subprocess.run(["cmake", "-P", str(folder / "glob.cmake")], capture_output=True, text=True)
            return r.stderr.strip()

        self.assertEqual(found(self.root), "found 0")
        self.assertEqual(found(builder.build_root(self.root, Quiet())), "found 1")


if __name__ == "__main__":
    unittest.main()
