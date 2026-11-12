"""Prebuild shaders (#226): the shipped list, the files written for the SDK, the prebuild-only game run
and when the step is offered again. No game files or GPU: shaders and the game are stand-ins."""
import json
import os
import struct
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))
from forza_launcher import paths, shader_prebuild  # noqa: E402
import fxobj_shaders  # noqa: E402
import pipeline_list  # noqa: E402
from xxh3 import xxh3_64  # noqa: E402

# The sample pipeline of tools/test_pipeline_prebuild.cpp and the bytes both encoders must write.
SAMPLE = {"vs": "9B5EE39E84345522", "vs_mod": "0000000000000003", "ps": "8CDF6BDE45CB52DD",
          "ps_mod": "0000400000000003", "partitions": ["main"],
          "fields": [1, 3, 1, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 1, 1, 0, 0, 7, 1, 0, 0, 0, 2, 0, 0, 0, 2, 0,
                     6, 7, 0, 6, 7, 0, 7] + [0] * 21}
SAMPLE_XPL = bytes.fromhex(
    "5845504c010000000000000028022620181026203a0000000100000060000000b180d814877653ac"
    "225534849ee35e9b0300000000000000dd52cb45de6bdf8c03000000004000000100000001030100"
    "00000000000300000000000101000007010000000200000002000607000607000700000000000000"
    "00000000000000000000000000000000")


def blob(magic, ucode):
    """A D3D shader blob as the game files hold it (fxobj_shaders.find_blobs)."""
    vsize = 0x24 + 8
    header = struct.pack(">9I", magic, vsize, len(ucode), 0, 0, 0, 0x24, 0, 0)
    return header + struct.pack(">2I", 0, len(ucode)) + ucode


class FakePlan(SimpleNamespace):
    pass


# The GPU plugin the prebuild stamp tracks, named as on this OS.
PLUGIN = "rexgpu-xenos.dll" if paths.IS_WINDOWS else "librexgpu-xenos.so"


def plan_in(tmp, *extra):
    cwd = Path(tmp) / "game"
    cwd.mkdir(exist_ok=True)
    if not (cwd / PLUGIN).exists():
        (cwd / PLUGIN).write_bytes(b"plugin")
    return FakePlan(argv=[str(cwd / "forza"), f"--user_data_root={tmp}/ud",
                          f"--log_file={tmp}/forza.log", *extra],
                    cwd=cwd, env={}, unset=[], log_before_start=None)


class ListTests(unittest.TestCase):
    def test_shipped_list_is_hashes_and_render_state_only(self):
        path = shader_prebuild.list_path()
        self.assertIsNotNone(path, "the launcher ships data/pipelines-4D5309C9.json")
        lst = pipeline_list.load_list(str(path))
        self.assertEqual(pipeline_list.check_list(lst), [])
        api, desc, mod = pipeline_list.list_versions(lst)
        self.assertEqual(api, "fbo")
        self.assertGreater(len(lst["pipelines"]), 500)
        text = path.read_text()
        # Only short hex hashes and small integers: no blob could hide in it.
        self.assertNotRegex(text, r"[0-9A-Fa-f]{17,}")

    def test_encoder_matches_the_sdk_test(self):
        self.assertEqual(pipeline_list.encode_xpl([SAMPLE], "fbo", 0x20260228, 0x20261018), SAMPLE_XPL)

    def test_check_refuses_microcode_and_bad_values(self):
        lst = {"format": 2, "title": "4D5309C9", "storage_versions": ["fbo:20260228/20261018"],
               "pipelines": [dict(SAMPLE)], "shaders": {}}
        self.assertEqual(pipeline_list.check_list(lst), [])
        bad = json.loads(json.dumps(lst))
        bad["pipelines"][0]["fields"][30] = 15  # blend factor 15 does not exist
        self.assertTrue(pipeline_list.check_list(bad))
        bad = json.loads(json.dumps(lst))
        bad["shaders"]["0000000000000001"] = {"from": "file", "ucode": "00" * 64}
        self.assertTrue(pipeline_list.check_list(bad))
        bad = json.loads(json.dumps(lst))
        bad["pipelines"][0]["extra"] = 1
        self.assertTrue(pipeline_list.check_list(bad))

    def test_format1_lists_convert(self):
        p = pipeline_list.decode_description(pipeline_list.encode_description(SAMPLE, "clang"), "clang")
        self.assertEqual(p["fields"], SAMPLE["fields"])
        self.assertEqual(pipeline_list.encode_description(SAMPLE, "msvc")[36:44].hex(), "0c300f4000040000")


class WritePrebuildTests(unittest.TestCase):
    def test_rebuilds_listed_shaders_from_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ps = bytes(range(64))
            vs_base = bytes(48)
            (Path(tmp) / "a.fxobj").write_bytes(b"\0" * 7 + blob(0x102A1100, ps) + blob(0x102A1101, vs_base))
            # A bound vertex shader: the blob with one export destination changed.
            w = list(struct.unpack(">12I", vs_base))
            w[3] = (w[3] & ~fxobj_shaders.EXPORT_MASK) | 0x00000102
            vs = struct.pack(">12I", *w)
            p = dict(SAMPLE, vs=f"{xxh3_64(vs):016X}", ps=f"{xxh3_64(ps):016X}")
            lost = dict(SAMPLE, vs="0000000000000042", ps=f"{xxh3_64(ps):016X}")
            lst = {"format": 2, "title": "4D5309C9", "storage_versions": ["fbo:20260228/20261018"],
                   "pipelines": [p, lost], "shaders": {
                       p["vs"]: {"type": "vs", "from": "bound", "base": f"{xxh3_64(vs_base):016X}",
                                 "export": [[1, 0x00000102]]},
                       p["ps"]: {"type": "ps", "from": "file"},
                       "0000000000000042": {"type": "vs", "from": "missing", "why": "not in the game files"}}}
            self.assertEqual(pipeline_list.check_list(lst), [])
            out = Path(tmp) / "prebuild"
            stats = pipeline_list.write_prebuild(lst, [str(Path(tmp) / "a.fxobj")], str(out))
            self.assertEqual((stats["shaders"], stats["pipelines"], stats["shaders_missing"]), (2, 1, 1))
            xsh = fxobj_shaders.read_xsh(str(out / "4D5309C9.xsh"))
            self.assertEqual(xsh[int(p["vs"], 16)], (0, vs))
            self.assertEqual(xsh[int(p["ps"], 16)], (1, ps))
            xpl = (out / "4D5309C9.fbo.vk.xpl").read_bytes()
            self.assertEqual(struct.unpack_from("<I", xpl, 24)[0], 1)
            self.assertEqual(len(xpl), 40 + 96)

    def test_image_recipes_find_headerless_shaders_in_the_executable(self):
        # #614: the D3D library's own shaders sit in the executable without a blob header. An
        # "image" recipe gives their size and an offset per known executable, never the code.
        vs, ps = bytes(range(4, 100)), bytes(range(200, 236))
        tu4 = b"\0" * 64 + vs + ps + b"\0" * 8
        disc = b"\1" * 16 + vs + ps
        p = dict(SAMPLE, vs=f"{xxh3_64(vs):016X}", ps=f"{xxh3_64(ps):016X}")
        with tempfile.TemporaryDirectory() as tmp:
            xsh = Path(tmp) / "own.xsh"
            xsh.write_bytes(pipeline_list.xsh_bytes({xxh3_64(vs): (0, vs), xxh3_64(ps): (1, ps)}))
            hashes = {xxh3_64(vs): 0, xxh3_64(ps): 1}
            # Player uploads keep the older recipe kinds.
            plain = pipeline_list.make_shader_recipes(hashes, [str(xsh)], [("default.xex", tu4)])
            self.assertEqual({r["from"] for r in plain.values()}, {"missing"})
            recipes = pipeline_list.make_shader_recipes(
                hashes, [str(xsh)], [("default.xex", tu4), ("default.xex", disc)], images=True)
            self.assertEqual(recipes[p["vs"]], {"type": "vs", "from": "image", "size": 96, "offsets": [64, 16]})
            self.assertEqual(recipes[p["ps"]], {"type": "ps", "from": "image", "size": 36, "offsets": [160, 112]})
            lst = {"format": 2, "title": "4D5309C9", "storage_versions": ["fbo:20260228/20261018"],
                   "pipelines": [p], "shaders": recipes}
            self.assertEqual(pipeline_list.check_list(lst), [])
            for image in (tu4, disc):
                out = Path(tmp) / "prebuild"
                stats = pipeline_list.write_prebuild(lst, [("default.xex", image)], str(out))
                self.assertEqual((stats["shaders"], stats["pipelines"]), (2, 1))
                built = fxobj_shaders.read_xsh(str(out / "4D5309C9.xsh"))
                self.assertEqual(built[int(p["vs"], 16)], (0, vs))
                self.assertEqual(built[int(p["ps"], 16)], (1, ps))
            # Another executable: the hash does not match at any listed offset.
            stats = pipeline_list.write_prebuild(lst, [("default.xex", b"\0" * 256)], str(out))
            self.assertEqual((stats["shaders"], stats["pipelines"], stats["shaders_missing"]), (0, 0, 2))
        for bad_recipe in ({"offsets": [64, 16, 8, 4, 0]}, {"offsets": [65]}, {"offsets": []},
                           {"size": 3}, {"size": 1 << 20}, {"type": None}, {"ucode": "00" * 32}):
            bad = json.loads(json.dumps(lst))
            bad["shaders"][p["vs"]].update(bad_recipe)
            self.assertTrue(pipeline_list.check_list(bad), bad_recipe)

    def test_image_sources_in_memory(self):
        ps = bytes(range(32))
        found, _ = fxobj_shaders.collect([("default.xex", b"\0" * 5 + blob(0x102A1100, ps))])
        self.assertIn(xxh3_64(ps), found)


FAKE_GAME = textwrap.dedent('''
    import sys, time
    log = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--log_file=")][0]
    with open(log, "a") as f:
        f.write("[i] Initializing shader storage for title 4D5309C9...\\n")
        f.write("[i] SHADER_WARMUP: Vulkan begin\\n")
        f.flush()
        for done in (0, 16, 32):
            f.write(f"[i] SHADER_WARMUP: Vulkan progress {done}/32\\n"); f.flush(); time.sleep(0.05)
        if MODE == "hang":
            time.sleep(60)
        f.write("[i] Created 32 graphics pipelines from Vulkan storage (32 requested)\\n")
        f.write("[i] SHADER_WARMUP: Vulkan complete in 5 ms\\n")
        if MODE == "ok":
            f.write("[i] SHADER_PREBUILD: complete, exiting\\n")
    sys.exit(0 if MODE == "ok" else 3)
''')


class RunGameTests(unittest.TestCase):
    def run_fake(self, mode, cancel_after=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        script = Path(tmp) / "fake.py"
        script.write_text(FAKE_GAME.replace("MODE", repr(mode)))
        plan = plan_in(tmp)
        plan.argv = [sys.executable, str(script), f"--log_file={tmp}/forza.log"]
        seen, start = [], [0]

        def cancelled():
            start[0] += 1
            return cancel_after is not None and start[0] > cancel_after
        with mock.patch("forza_launcher.game.clean_shm"):
            result = shader_prebuild.run_game(plan, lambda d, t, part: seen.append((d, t, part)),
                                              cancelled, poll=0.02)
        return result, seen

    def test_success_reports_progress_and_count(self):
        result, seen = self.run_fake("ok")
        self.assertEqual(result["pipelines"], 32)
        self.assertTrue(seen and seen[-1][1] == 32 and seen[-1][2] == 1)

    def test_exit_without_the_done_line_fails(self):
        with self.assertRaises(shader_prebuild.PrebuildError):
            self.run_fake("fail")

    def test_cancel_stops_the_game(self):
        with self.assertRaises(InterruptedError):
            self.run_fake("hang", cancel_after=20)

    def test_command_adds_the_flag(self):
        plan = plan_in(tempfile.mkdtemp())
        self.assertEqual(shader_prebuild.prebuild_command(plan)[-1], "--shader_prebuild_only=true")


class OfferAgainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def stamp(self, plan, vkpc=True):
        local = shader_prebuild.cache_root(plan) / "shaders" / "local"
        local.mkdir(parents=True, exist_ok=True)
        if vkpc:
            (local / "4D5309C9-00001002-000073DF-00800000-2026022820261018.vkpc").write_bytes(b"x")
        shader_prebuild.write_stamp(plan, {"pipelines": 1500, "seconds": 61.0}, {"pipelines": 1500})

    def test_cache_root(self):
        plan = plan_in(self.tmp)
        self.assertEqual(shader_prebuild.cache_root(plan), Path(self.tmp) / "ud" / "cache")
        other = Path(self.tmp) / "x" / "cache"
        plan = plan_in(self.tmp, f"--cache_root={other}")
        self.assertEqual(shader_prebuild.cache_root(plan), other)

    def test_native_scene_size_changes_prebuild_store(self):
        output = ("--fh1_output_width=1920", "--fh1_output_height=1080")
        default = plan_in(self.tmp, *output)
        base_key = shader_prebuild.store_key(default)
        self.assertEqual(base_key, {"fh1_output_width": "1920", "fh1_output_height": "1080"})
        first = plan_in(self.tmp, *output, "--fh1_native_scene_width=1920",
                        "--fh1_native_scene_height=1080")
        wider = plan_in(self.tmp, *output, "--fh1_native_scene_width=2560",
                        "--fh1_native_scene_height=1080")
        taller = plan_in(self.tmp, *output, "--fh1_native_scene_width=1920",
                         "--fh1_native_scene_height=1440")
        self.assertNotEqual(shader_prebuild.store_key(first), base_key)
        self.assertNotEqual(shader_prebuild.store_key(first), shader_prebuild.store_key(wider))
        self.assertNotEqual(shader_prebuild.store_key(first), shader_prebuild.store_key(taller))
        self.stamp(first)
        self.assertEqual(shader_prebuild.status(first)[0], "done")
        self.assertEqual(shader_prebuild.status(wider)[0], "stale")
        self.assertEqual(shader_prebuild.status(taller)[0], "stale")

    def test_states(self):
        plan = plan_in(self.tmp)
        self.assertEqual(shader_prebuild.status(plan)[0], "none")
        self.stamp(plan)
        state, text = shader_prebuild.status(plan)
        self.assertEqual(state, "done")
        self.assertIn("1,500 pipelines", text)
        # A graphics setting that selects other stores.
        smaa = plan_in(self.tmp, "--fh1_upscaler=smaa", "--fh1_native_scene_scale=2")
        self.assertEqual(shader_prebuild.status(smaa), ("stale", "Your graphics settings changed since the last prebuild."))
        # A new driver: the SDK deleted the driver cache file of the old one and wrote another.
        for f in (shader_prebuild.cache_root(plan) / "shaders" / "local").glob("*.vkpc"):
            f.unlink()
        self.assertIn("driver", shader_prebuild.status(plan)[1])
        # A launcher or game update.
        self.stamp(plan)
        (plan.cwd / PLUGIN).write_bytes(b"new plugin")
        self.assertIn("updated", shader_prebuild.status(plan)[1])
        with mock.patch.object(shader_prebuild, "__version__", "9.9.9"):
            self.stamp(plan)
            self.assertEqual(shader_prebuild.status(plan)[0], "done")
        self.assertIn("updated", shader_prebuild.status(plan)[1])

    def test_unavailable(self):
        self.assertEqual(shader_prebuild.status(plan_in(self.tmp, "--gpu_backend=d3d12"))[0], "unavailable")
        # Twice the same path option: the game falls back to its default folders.
        twice = plan_in(self.tmp, f"--user_data_root={self.tmp}/other")
        self.assertEqual(shader_prebuild.status(twice)[0], "unavailable")
        with mock.patch.object(shader_prebuild, "list_path", return_value=None):
            self.assertEqual(shader_prebuild.status(plan_in(self.tmp))[0], "unavailable")


if __name__ == "__main__":
    unittest.main()
