"""Shader lists from play (#276): the stores turned into entries, what is sent once, the setting and the
online mode that stop the upload, and the downloaded list merged with the shipped one (falling back to it).
No network, game files or GPU: the server is a stand-in."""
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))
from forza_launcher import schema, shader_prebuild, shader_share  # noqa: E402
from forza_launcher.config import Config, render_toml  # noqa: E402
import pipeline_list  # noqa: E402
from xxh3 import xxh3_64  # noqa: E402

VERSION = "fbo:20260228/20261018"
KEY = "AB" * 16
SEED = "CD" * 32
XUID = "0009000000000001"


def pipe(n, parts=("main",)):
    fields = [0] * len(pipeline_list.FIELDS)
    fields[1] = n % 32
    return {"vs": "%016X" % (0x1000 + n), "vs_mod": "0000000000000003", "ps": "%016X" % (0x2000 + n),
            "ps_mod": "0000000000000001", "fields": fields, "partitions": list(parts)}


def write_store(path, pipelines, version=(0x20260228, 0x20261018), api=0):
    """A pipeline storage file in the SDK's format 2 (patch 0102), Clang/GCC layout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = struct.pack("<II", pipeline_list.XPSO_MAGIC, api) + struct.pack(">III", 2, *version)
    for p in pipelines:
        d = pipeline_list.encode_description(p, "clang")
        data += struct.pack("<Q", xxh3_64(d)) + d
    path.write_bytes(data)


def shipped(pipelines):
    return {"format": 2, "title": "4D5309C9", "storage_versions": [VERSION], "pipelines": pipelines,
            "shaders": {h: {"type": "vs", "from": "file"} for p in pipelines for h in (p["vs"], p["ps"])}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root / "data")  # never the real data folder
        self.cfg.online["mode"] = "public"
        self.cache = self.root / "ud" / "cache"
        self.plan = SimpleNamespace(argv=[f"--cache_root={self.cache}"], cwd=self.root)
        self.store = self.cache / "shaders" / "shareable" / "4D5309C9.fbo.vk.xpso"
        self.sent = []
        self.status = 200
        self.ship = shipped([pipe(100)])
        patches = [
            mock.patch.object(shader_share, "identity_dirs", lambda cfg: [self.root / "id"]),
            mock.patch.object(shader_share, "shipped_list", lambda: self.ship),
            mock.patch.object(shader_prebuild, "game_sources", lambda *a: []),
            mock.patch.object(shader_share, "recipes_for", self.recipes),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        (self.root / "id").mkdir()
        (self.root / "id" / "live_identity.txt").write_text(f"# x\nxuid={XUID}\ngamertag=Me\nfriends_key={KEY}\nsigning_seed={SEED}\n")
        self.unrebuildable = set()

    def tearDown(self):
        self.tmp.cleanup()

    def recipes(self, pipelines, xsh, sources):
        out = {}
        for p in pipelines:
            for h in (p["vs"], p["ps"]):
                out[h] = ({"from": "missing", "why": "not in the game files"} if h in self.unrebuildable
                          else {"type": "vs", "from": "file"})
        return out

    def post(self, base, identity, body, timeout=0):
        self.sent.append((base, identity, json.loads(body)))
        return self.status

    def share(self):
        return shader_share.share(self.cfg, self.plan, None, lambda *_: None, post_fn=self.post)

    def sent_pipelines(self):
        return [p for _b, _i, body in self.sent for p in body["pipelines"]]


class ConversionTests(Base):
    def test_stores_become_entries_with_partitions(self):
        write_store(self.store, [pipe(1), pipe(2)])
        ui = self.cache / "fh1-live-v1-2560x1440-1x1" / "ui" / "shaders" / "shareable" / "4D5309C9.fbo.vk.xpso"
        scene = ui.parents[3] / "scene" / "shaders" / "shareable" / "4D5309C9.fbo.vk.xpso"
        write_store(ui, [pipe(2), pipe(3)])
        write_store(scene, [pipe(3)])
        files = shader_share.store_files(self.cache)
        self.assertEqual(len(files), 3)
        new = shader_share.collect_new(files, {VERSION}, {}, set())[VERSION]
        got = {p["vs"]: p["partitions"] for p in new.values()}
        # the same pipeline in two stores is one entry per partition set
        self.assertEqual(len(new), 3)
        self.assertEqual(sorted(got), ["%016X" % (0x1000 + n) for n in (1, 2, 3)])
        self.assertEqual({tuple(p["partitions"]) for p in new.values() if p["vs"].endswith("1003")},
                         {("scene", "ui")})

    def test_other_versions_are_not_shared(self):
        write_store(self.store, [pipe(1)], version=(0x20260101, 0x20261018))
        self.assertEqual(shader_share.collect_new(shader_share.store_files(self.cache), {VERSION}, {}, set()), {})

    def test_damaged_store_gives_what_is_valid(self):
        write_store(self.store, [pipe(1), pipe(2)])
        data = bytearray(self.store.read_bytes())
        data[-5] ^= 0xFF  # the second record fails its hash
        self.store.write_bytes(bytes(data))
        new = shader_share.collect_new(shader_share.store_files(self.cache), {VERSION}, {}, set())[VERSION]
        self.assertEqual(len(new), 1)

    def test_no_game_data_in_an_upload(self):
        write_store(self.store, [pipe(1)])
        self.share()
        body = self.sent[0][2]
        self.assertEqual(set(body), {"version", "pipelines", "shaders"})
        self.assertEqual(pipeline_list.check_list({"format": 2, "storage_versions": [body["version"]],
                                                    "pipelines": body["pipelines"], "shaders": body["shaders"]}), [])


class UploadTests(Base):
    def test_sends_once_and_only_what_is_new(self):
        write_store(self.store, [pipe(1), pipe(2)])
        self.assertEqual(self.share(), "sent 2")
        base, identity, body = self.sent[0]
        self.assertEqual(base, "http://forza-reimagined.playit.plus:56678")
        self.assertEqual(identity, (XUID, KEY, SEED))
        self.assertEqual(body["version"], VERSION)
        self.assertEqual(sorted(p["vs"] for p in body["pipelines"]), ["%016X" % 0x1001, "%016X" % 0x1002])
        self.assertIn("%016X" % 0x2001, body["shaders"])
        self.assertEqual(self.share(), "nothing new")
        self.assertEqual(len(self.sent), 1)
        write_store(self.store, [pipe(1), pipe(2), pipe(3)])
        self.assertEqual(self.share(), "sent 1")
        self.assertEqual([p["vs"] for p in self.sent[1][2]["pipelines"]], ["%016X" % 0x1003])

    def test_record_is_per_server(self):
        write_store(self.store, [pipe(1)])
        self.share()
        self.cfg.online.update(mode="custom", custom_server="example.org:21200")
        self.assertEqual(self.share(), "sent 1")
        self.assertEqual(self.sent[1][0], "http://example.org:21201")

    def test_shipped_entries_and_unrebuildable_ones_are_not_sent(self):
        write_store(self.store, [pipe(100), pipe(1), pipe(2)])
        self.unrebuildable = {"%016X" % 0x2002}
        self.assertEqual(self.share(), "sent 1")
        self.assertEqual([p["vs"] for p in self.sent_pipelines()], ["%016X" % 0x1001])
        self.assertEqual(self.share(), "nothing new")  # the unrebuildable one is not tried again

    def test_server_refusal_keeps_the_entries_for_next_time(self):
        write_store(self.store, [pipe(1)])
        self.status = 429
        self.assertTrue(self.share().startswith("stopped (429)"))
        self.status = 200
        self.assertEqual(self.share(), "sent 1")

    def test_batches_and_per_run_cap(self):
        n = shader_share.BATCH * 2 + 5
        write_store(self.store, [pipe(i) for i in range(1000, 1000 + n)])
        with mock.patch.object(shader_share, "PER_RUN", shader_share.BATCH + 10):
            self.assertEqual(self.share(), f"sent {shader_share.BATCH + 10}")
        self.assertEqual([len(b["pipelines"]) for _a, _i, b in self.sent], [shader_share.BATCH, 10])
        self.assertEqual(self.share(), f"sent {n - shader_share.BATCH - 10}")

    def test_setting_off_means_no_upload(self):
        write_store(self.store, [pipe(1)])
        self.cfg.settings["share_shader_list"] = False
        self.assertEqual(self.share(), "off")
        self.assertIsNone(shader_share.share_in_background(self.cfg, self.plan))
        self.assertEqual(self.sent, [])

    def test_offline_and_this_pc_mean_no_upload(self):
        write_store(self.store, [pipe(1)])
        for mode in ("offline", "local"):
            self.cfg.online["mode"] = mode
            self.assertEqual(self.share(), "no server")
            self.assertIsNone(shader_share.share_in_background(self.cfg, self.plan))
        self.cfg.online.update(mode="custom", custom_server="")
        self.assertEqual(self.share(), "no server")
        self.assertEqual(self.sent, [])

    def test_no_identity_no_upload(self):
        write_store(self.store, [pipe(1)])
        (self.root / "id" / "live_identity.txt").unlink()
        self.assertEqual(self.share(), "no Forza Live identity yet")
        self.assertEqual(self.sent, [])

    def test_failures_are_silent(self):
        write_store(self.store, [pipe(1)])
        def boom(*a, **k):
            raise OSError("network down")
        with mock.patch.object(shader_share.paths, "log_error") as logged:
            with mock.patch.object(shader_share, "post", boom):
                self.assertEqual(shader_share.share_quietly(self.cfg, self.plan), "failed")
        logged.assert_called_once()
        self.assertEqual(self.share(), "sent 1")  # the record kept nothing of the failed run

    def test_background_does_not_block(self):
        write_store(self.store, [pipe(1)])
        started = []
        with mock.patch.object(shader_share, "share_quietly", lambda *a, **k: started.append(a)):
            t = shader_share.share_in_background(self.cfg, self.plan)
            t.join(5)
        self.assertTrue(t.daemon)
        self.assertEqual(len(started), 1)

    def test_post_has_a_short_timeout(self):
        seen = {}
        class Reply:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b""
        def fake(req, timeout=None):
            seen.update(timeout=timeout, headers=dict(req.header_items()), url=req.full_url)
            return Reply()
        with mock.patch.object(shader_share.urllib.request, "urlopen", fake):
            self.assertEqual(shader_share.post("http://h:1", (XUID, KEY, SEED), b"{}"), 200)
        self.assertLessEqual(seen["timeout"], 10)
        self.assertEqual(seen["headers"]["X-xuid"], XUID)
        self.assertIn("Signature=", seen["headers"]["X-forza-auth"])
        self.assertNotIn("X-friends-key", seen["headers"])  # the key never leaves the PC (#515)
        self.assertEqual(seen["url"], "http://h:1/shader-lists/upload")


class SettingTests(Base):
    def test_default_on_in_online_and_not_in_forza_toml(self):
        s = next(s for s in schema.S if s.key == "share_shader_list")
        self.assertIs(s.default, True)
        self.assertEqual(s.group, "Online")
        # What the request carries is said (#497): not "nothing personal", since it sends the XUID and key.
        for words in ("shader hashes", "render settings", "no game data", "player id and a signature made with "
                      "your signing key"):
            self.assertIn(words, s.help.lower().replace("\n", " "))
        self.assertNotIn("nothing personal", s.help.lower())
        self.assertTrue(self.cfg.settings["share_shader_list"])
        self.cfg.settings["share_shader_list"] = False
        self.assertNotIn("share_shader_list", render_toml(self.cfg))

    def test_http_base(self):
        for spec, want in (("forza-reimagined.playit.plus:56677", "http://forza-reimagined.playit.plus:56678"),
                           ("host", "http://host:21201"), ("1.2.3.4:21200", "http://1.2.3.4:21201"),
                           ("[::1]:5000", "http://[::1]:5001"), ("", ""), ("a b", ""), ("h:x", ""),
                           ("h:0", ""), ("h/x:1", ""), ("::1", "")):
            self.assertEqual(shader_share.http_base(spec), want, spec)


class DownloadTests(Base):
    def community(self, **kw):
        lst = shipped([pipe(1), pipe(2, ("scene",))])
        lst.update(kw)
        return lst

    def merged(self, community):
        return shader_share.community_list(self.cfg, self.ship, lambda *_: None, lambda base, v: community)

    def test_merge_adds_to_the_shipped_list(self):
        got = self.merged(self.community())
        self.assertEqual(len(got["pipelines"]), 3)
        self.assertEqual({p["vs"] for p in got["pipelines"]}, {"%016X" % (0x1000 + n) for n in (1, 2, 100)})
        self.assertEqual(pipeline_list.check_list(got), [])
        self.assertEqual(len(self.ship["pipelines"]), 1)  # the shipped list is not changed

    def test_partitions_join(self):
        got = self.merged(self.community(pipelines=[pipe(100, ("scene",))]))
        self.assertEqual(got["pipelines"][0]["partitions"], ["main", "scene"])

    def test_fetch_uses_the_server_and_the_version(self):
        seen = []
        shader_share.community_list(self.cfg, self.ship, lambda *_: None,
                                    lambda base, v: seen.append((base, v)) or self.community())
        self.assertEqual(seen, [("http://forza-reimagined.playit.plus:56678", VERSION)])

    def test_falls_back_to_the_shipped_list(self):
        def down(base, version):
            raise OSError("offline")
        logged = []
        self.assertIs(shader_share.community_list(self.cfg, self.ship, logged.append, down), self.ship)
        self.assertEqual(len(logged), 1)
        for bad in (None, [1], {"x": 1}, self.community(storage_versions=["fbo:20260101/20261018"]),
                    self.community(format=3)):
            self.assertIs(self.merged(bad), self.ship, bad)
        broken = self.community()
        broken["pipelines"][0]["fields"][0] = 99  # an entry outside the list format: nothing of it is used
        self.assertIs(self.merged(broken), self.ship)
        import urllib.error
        def timeout(base, version):
            raise urllib.error.URLError("timed out")
        self.assertIs(shader_share.community_list(self.cfg, self.ship, lambda *_: None, timeout), self.ship)

    def test_offline_never_asks(self):
        for mode in ("offline", "local"):
            self.cfg.online["mode"] = mode
            def never(*a):
                raise AssertionError("asked the server")
            self.assertIs(shader_share.community_list(self.cfg, self.ship, lambda *_: None, never), self.ship)

    def test_shipped_recipe_wins_a_conflict(self):
        shipped_recipe = {"type": "vs", "from": "file"}
        other = {"type": "vs", "from": "bound", "base": "00000000000000AA"}
        h = self.ship["pipelines"][0]["vs"]
        com = self.community(shaders={h: other}, pipelines=[pipe(100), pipe(1)])
        got = self.merged(com)
        self.assertEqual(got["shaders"][h], shipped_recipe)

    def test_download_failure_is_logged_and_a_slow_server_times_out(self):
        seen = {}
        def fake(url, timeout=None):
            seen["timeout"] = timeout
            raise OSError("slow")
        with mock.patch.object(shader_share.urllib.request, "urlopen", fake):
            self.assertIs(shader_share.community_list(self.cfg, self.ship, lambda *_: None), self.ship)
        self.assertLessEqual(seen["timeout"], 10)

    def test_write_files_prebuilds_the_merged_list(self):
        with mock.patch.object(shader_prebuild, "list_path", lambda: self.root / "list.json"), \
                mock.patch.object(shader_share, "fetch", lambda b, v: self.community()), \
                mock.patch.object(shader_prebuild, "game_sources", lambda *a: ["src"]), \
                mock.patch("pipeline_list.write_prebuild", lambda lst, srcs, out, st, c: {"n": len(lst["pipelines"])}):
            pipeline_list.write_list(str(self.root / "list.json"), dict(self.ship))
            self.assertEqual(shader_prebuild.write_files(self.cfg, None, self.plan, log=lambda *_: None), {"n": 3})
            self.cfg.online["mode"] = "offline"
            self.assertEqual(shader_prebuild.write_files(self.cfg, None, self.plan, log=lambda *_: None), {"n": 1})


if __name__ == "__main__":
    unittest.main()
