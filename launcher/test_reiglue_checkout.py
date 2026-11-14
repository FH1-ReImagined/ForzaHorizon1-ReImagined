"""Exercise SDK source preparation with isolated local Git repositories; no build or network."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "tools/build_rexglue_sdk.sh"
TAG = "fixture-sdk-tag"
BASH = (str(Path("C:/Program Files/Git/bin/bash.exe")) if os.name == "nt" and
        Path("C:/Program Files/Git/bin/bash.exe").is_file() else shutil.which("bash"))


@unittest.skipUnless(BASH, "requires bash")
class CodegenStampInvalidation(unittest.TestCase):
    def check_tool_replacement(self, updated, blocked=False):
        text = SCRIPT.read_text()
        start = text.index('if cmp -s "$tool_new" "$OVERLAY/bin/rexglue"; then')
        end = text.index('"$OVERLAY/bin/rexglue" --help', start)
        with tempfile.TemporaryDirectory(prefix="codegen stamp fixture ") as d:
            root = Path(d)
            overlay = root / "SDK overlay"
            installed = overlay / "bin/rexglue"
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b"old")
            tool_new = overlay / "bin/.rexglue.new"
            tool_new.write_bytes(b"new" if updated else b"old")

            stamps = [root / "forza-recomp" / tree / module / "codegen.stamp"
                      for tree in ("generated", "generated_tu4")
                      for module in ("default", "xmediafacade", "speechfacade")]
            keep = root / "forza-recomp/generated_tu4/default/forza_recomp.cpp"
            unrelated = root / "forza-recomp/generated_backup/default/codegen.stamp"
            for path in [*stamps, keep, unrelated]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            if blocked:
                stamps[-1].unlink()
                stamps[-1].mkdir()

            result = subprocess.run(
                [BASH, "-eu", "-c", 'ROOT=$1; OVERLAY=$2; tool_new=$3\n' + text[start:end],
                 "test", root.as_posix(), overlay.as_posix(), tool_new.as_posix()],
                text=True, capture_output=True, timeout=10,
            )
            if blocked:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(installed.read_bytes(), b"old")
                self.assertEqual(tool_new.read_bytes(), b"new")
                return

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(installed.read_bytes(), b"new" if updated else b"old")
            self.assertFalse(tool_new.exists())
            for stamp in stamps:
                self.assertEqual(stamp.exists(), not updated, stamp)
            self.assertEqual(keep.read_bytes(), b"fixture")
            self.assertEqual(unrelated.read_bytes(), b"fixture")

    def test_changed_tool_invalidates_base_and_tu4(self):
        self.check_tool_replacement(updated=True)

    def test_invalidation_failure_keeps_old_tool_for_retry(self):
        self.check_tool_replacement(updated=True, blocked=True)

    def test_identical_tool_preserves_base_and_tu4(self):
        self.check_tool_replacement(updated=False)


@unittest.skipUnless(BASH and shutil.which("git"), "requires bash and git")
class ReiglueCheckout(unittest.TestCase):
    with_ffmpeg = True  # the SDK has the FFmpeg submodule and a patch for it (0413)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_ALLOW_PROTOCOL="file", REIGLUE_DEV="0")
        self.sdl = self.root / "sdl-origin"
        self.init(self.sdl)
        (self.sdl / "sdl.txt").write_text("base SDL\n")
        self.commit(self.sdl, "SDL base")
        (self.sdl / "sdl.txt").write_text("patched SDL\n")
        patch = self.git(self.sdl, "diff").stdout
        (self.sdl / "sdl.txt").write_text("base SDL\n")
        if self.with_ffmpeg:
            self.ffmpeg = self.root / "ffmpeg-origin"
            self.init(self.ffmpeg)
            (self.ffmpeg / "ffmpeg.txt").write_text("base FFmpeg\n")
            self.commit(self.ffmpeg, "FFmpeg base")
            (self.ffmpeg / "ffmpeg.txt").write_text("patched FFmpeg\n")
            ffmpeg_patch = self.git(self.ffmpeg, "diff").stdout
            (self.ffmpeg / "ffmpeg.txt").write_text("base FFmpeg\n")
        self.origin = self.root / "origin"
        self.init(self.origin)
        (self.origin / "sdk.txt").write_text("base SDK\n")
        (self.origin / "README.md").write_text("upstream README\n")
        self.git(self.origin, "submodule", "add", "--quiet", str(self.sdl), "thirdparty/sdl3")
        if self.with_ffmpeg:
            self.git(self.origin, "submodule", "add", "--quiet", str(self.ffmpeg), "thirdparty/FFmpeg")
        self.base = self.commit(self.origin, "SDK base")
        (self.origin / "sdk.txt").write_text("patched SDK\n")
        (self.origin / "new-runtime.txt").write_text("new runtime source\n")
        (self.origin / "README.md").write_text("ReIGlue README\n")
        (self.origin / "NOTICE").write_text("ReIGlue notice\n")
        patches = self.origin / "patches/sdl3"
        patches.mkdir(parents=True)
        (patches / "0147.patch").write_text(patch)
        if self.with_ffmpeg:
            patches = self.origin / "patches/ffmpeg"
            patches.mkdir(parents=True)
            (patches / "0413.patch").write_text(ffmpeg_patch)
        self.target = self.commit(self.origin, "SDK changes")
        self.git(self.origin, "tag", "--no-sign", "-a", TAG, "-m", "fixture tag")
        self.source = self.root / "checkout with spaces"
        text = SCRIPT.read_text()
        text = re.sub(r"(?m)^REIGLUE_TAG=.*", "REIGLUE_TAG=" + TAG, text)
        text = re.sub(r"(?m)^SDK_COMMIT=[0-9a-f]+", "SDK_COMMIT=" + self.base, text)
        text = re.sub(r"(?m)^REIGLUE_COMMIT=[0-9a-f]+", "REIGLUE_COMMIT=" + self.target, text)
        self.script = self.root / "project/tools/build_rexglue_sdk.sh"
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes(text.encode())

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], env=self.env, text=True,
                              capture_output=True, check=True, timeout=20)

    def init(self, repo):
        repo.mkdir()
        self.git(repo, "init", "--quiet")
        for key, value in [("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.autocrlf", "false")]:
            self.git(repo, "config", key, value)

    def commit(self, repo, message):
        self.git(repo, "add", ".")
        self.git(repo, "-c", "commit.gpgsign=false", "-c", "user.name=Fixture",
                 "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", message)
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def prepare(self, dev=False):
        env = dict(self.env, REIGLUE_URL=str(self.origin), REXSDK_SRC=str(self.source),
                   REIGLUE_DEV=str(int(dev)))
        return subprocess.run([BASH, str(self.script), "--source-only"], env=env,
                              text=True, capture_output=True, timeout=30)

    def ready(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.target)
        self.assertEqual((self.source / "thirdparty/sdl3/sdl.txt").read_text(), "patched SDL\n")
        if self.with_ffmpeg:
            self.assertEqual((self.source / "thirdparty/FFmpeg/ffmpeg.txt").read_text(),
                             "patched FFmpeg\n")

    def old_checkout(self):
        self.git(self.root, "clone", "--quiet", "--no-checkout", str(self.origin), str(self.source))
        self.git(self.source, "checkout", "--quiet", "--detach", self.base)
        (self.source / "sdk.txt").write_text("patched SDK\n")
        (self.source / "new-runtime.txt").write_text("new runtime source\n")

    def snapshot(self):
        return {p.relative_to(self.source).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.source.rglob("*") if p.is_file() and
                ".git" not in p.relative_to(self.source).parts}

    def test_fresh_checkout_and_sdl_patch(self):
        self.ready()

    def test_local_ffmpeg_edit_is_refused_without_mutation(self):
        if not self.with_ffmpeg:
            self.skipTest("needs the FFmpeg submodule")
        self.ready()
        (self.source / "thirdparty/FFmpeg/ffmpeg.txt").write_text("my FFmpeg work\n")
        before = self.snapshot()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_repeat_keeps_source_timestamps(self):
        self.ready()
        before = self.snapshot()
        self.ready()
        self.assertEqual(self.snapshot(), before)

    def test_old_applied_series_takeover_keeps_matching_files(self):
        self.old_checkout()
        path = self.source / "sdk.txt"
        before = path.stat().st_mtime_ns
        self.ready()
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_staged_applied_series_is_adopted(self):
        self.old_checkout()
        self.git(self.source, "add", "sdk.txt", "new-runtime.txt")
        before = (self.source / "sdk.txt").stat().st_mtime_ns
        self.ready()
        self.assertEqual((self.source / "sdk.txt").stat().st_mtime_ns, before)

    def test_unrelated_working_edit_is_refused_without_mutation(self):
        self.old_checkout()
        path = self.source / "sdk.txt"
        path.write_text("my SDK work\n")
        before = self.snapshot()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)

    def test_staged_edit_is_refused_without_mutation(self):
        self.old_checkout()
        (self.source / "sdk.txt").write_text("staged SDK work\n")
        self.git(self.source, "add", "sdk.txt")
        before = self.git(self.source, "write-tree").stdout
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.source, "write-tree").stdout, before)
        self.assertEqual((self.source / "sdk.txt").read_text(), "staged SDK work\n")
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)

    def test_local_sdl_edit_is_refused_without_mutation(self):
        self.ready()
        (self.source / "thirdparty/sdl3/sdl.txt").write_text("my SDL work\n")
        before = self.snapshot()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_untracked_file_is_preserved_during_takeover(self):
        self.old_checkout()
        path = self.source / "my-notes.txt"
        path.write_text("my notes\n")
        before = path.stat().st_mtime_ns
        self.ready()
        self.assertEqual(path.read_text(), "my notes\n")
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_untracked_collision_is_refused_before_head_moves(self):
        self.old_checkout()
        (self.source / "NOTICE").write_text("my own notice\n")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual((self.source / "NOTICE").read_text(), "my own notice\n")

    def test_ignored_target_collision_is_refused_before_head_moves(self):
        self.old_checkout()
        (self.source / ".git/info/exclude").write_text("NOTICE\n")
        (self.source / "NOTICE").write_text("my ignored notice\n")
        self.assertNotIn("NOTICE", self.git(self.source, "status", "--porcelain").stdout)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual((self.source / "NOTICE").read_text(), "my ignored notice\n")

    def test_dev_keeps_sdk_and_sdl_work_as_checked_out(self):
        self.ready()
        (self.source / "sdk.txt").write_text("my SDK work\n")
        (self.source / "thirdparty/sdl3/sdl.txt").write_text("my SDL work\n")
        before = self.snapshot()
        result = self.prepare(dev=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_dev_accepts_git_worktree(self):
        self.ready()
        worktree = self.root / "development"
        self.git(self.source, "worktree", "add", "--quiet", "--detach", str(worktree), self.target)
        self.source = worktree
        before = self.snapshot()
        result = self.prepare(dev=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_different_local_tag_is_rejected(self):
        self.ready()
        (self.source / "sdk.txt").write_text("other revision\n")
        other = self.commit(self.source, "Different SDK")
        self.git(self.source, "tag", "--no-sign", "--force", TAG, other)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), other)

    def commit_pin(self, commit=None):
        text = re.sub(r"(?m)^REIGLUE_TAG=.*", "REIGLUE_TAG=", self.script.read_text())
        if commit is not None:
            text = re.sub(r"(?m)^REIGLUE_COMMIT=.*", "REIGLUE_COMMIT=" + commit, text)
        self.script.write_bytes(text.encode())

    def test_commit_pin_without_any_published_tag(self):
        self.git(self.origin, "tag", "--delete", TAG)
        self.commit_pin()
        self.ready()
        self.assertEqual(self.git(self.source, "tag").stdout, "")
        before = self.snapshot()
        self.ready()
        self.assertEqual(self.snapshot(), before)

    def test_commit_pin_fetches_missing_revision_into_existing_checkout(self):
        self.init(self.source)
        self.git(self.source, "fetch", "--quiet", str(self.origin), self.base)
        self.git(self.source, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        self.commit_pin()
        self.ready()
        self.assertEqual(self.git(self.source, "tag").stdout, "")

    def test_commit_pin_rejects_branch_name_without_moving_head(self):
        self.old_checkout()
        self.commit_pin("main")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("full immutable commit", result.stderr)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)

    def test_commit_pin_preserves_old_checkout_work(self):
        self.old_checkout()
        self.commit_pin()
        (self.source / "sdk.txt").write_text("my SDK work\n")
        before = self.snapshot()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)

    def test_commit_pin_requires_nightly_ancestry_before_checkout(self):
        self.old_checkout()
        self.commit_pin()
        unrelated = self.root / "unrelated"
        self.init(unrelated)
        (unrelated / "file").write_text("different history\n")
        other_base = self.commit(unrelated, "Unrelated SDK")
        text = re.sub(r"(?m)^SDK_COMMIT=.*", "SDK_COMMIT=" + other_base, self.script.read_text())
        self.script.write_bytes(text.encode())
        self.git(self.source, "fetch", "--quiet", str(unrelated), other_base)
        before = self.snapshot()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not based on the nightly", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD").stdout.strip(), self.base)


if __name__ == "__main__":
    unittest.main()


class ReiglueCheckoutWithoutFFmpeg(ReiglueCheckout):
    """An SDK with no FFmpeg submodule (and so no patches/ffmpeg/) builds as before."""
    with_ffmpeg = False
