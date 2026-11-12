"""The toolchain mirror helper (ci-toolchain-mirror.yml) without the network, gpg or gh."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

HELPER = Path(__file__).parent / "packaging/ci_toolchain_mirror.py"
spec = importlib.util.spec_from_file_location("ci_toolchain_mirror", HELPER)
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)

PACKAGES = """\
Package: g++-14
Architecture: amd64
Version: 14.3.0-12ubuntu1~22~ppa2
Source: gcc-14
Filename: pool/main/g/gcc-14/g++-14_14.3.0-12ubuntu1~22~ppa2_amd64.deb
Size: 10
SHA256: {gpp}

Package: libstdc++6
Architecture: amd64
Version: 16-20260315-1ubuntu1~22~ppa1
Source: gcc-16 (16-20260315-1ubuntu1~22~ppa1)
Filename: pool/main/g/gcc-16/libstdc++6_16-20260315-1ubuntu1~22~ppa1_amd64.deb
Size: 20
SHA256: {lib}
Description: GNU Standard C++ Library v3
 continuation line
"""
SOURCES = """\
Package: gcc-14
Version: 14.3.0-12ubuntu1~22~ppa2
Directory: pool/main/g/gcc-14
Checksums-Sha256:
 {a} 30 gcc-14_14.3.0-12ubuntu1~22~ppa2.dsc
 {b} 40 gcc-14_14.3.0.orig.tar.gz

Package: gcc-16
Version: 16-20260315-1ubuntu1~22~ppa1
Directory: pool/main/g/gcc-16
Checksums-Sha256:
 {c} 50 gcc-16_16-20260315-1ubuntu1~22~ppa1.dsc
"""
H = {k: hashlib.sha256(k.encode()).hexdigest() for k in ("gpp", "lib", "a", "b", "c")}
ENTRIES = [("g++-14", "14.3.0-12ubuntu1~22~ppa2"), ("libstdc++6", "16-20260315-1ubuntu1~22~ppa1")]


class ParsingTests(unittest.TestCase):
    def test_asset_names_avoid_characters_github_renames(self):
        self.assertEqual(mirror.asset_name("g++-14_14.3.0-12ubuntu1~22~ppa2_amd64.deb"),
                         "gplusplus-14_14.3.0-12ubuntu1tilde22tildeppa2_amd64.deb")
        with self.assertRaises(SystemExit):
            mirror.asset_name("gcc:1.0.deb")

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires a POSIX shell")
    def test_asset_names_match_the_setup_script(self):
        # ci_linux_sdk_setup.sh spells the names itself; both must agree.
        script = HELPER.with_name("ci_linux_sdk_setup.sh").read_text()
        start = script.index("asset_name() {")
        function = script[start:script.index("\n}\n", start) + 3]
        name = "g++-14_14.3.0-12ubuntu1~22~ppa2_amd64.deb"
        out = subprocess.run(["bash", "-c", function + 'asset_name "$1"', "-", name],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, mirror.asset_name(name))

    def test_manifest_matches_the_checked_in_list(self):
        entries = mirror.manifest_entries(mirror.MANIFEST.read_text())
        self.assertIn(("g++-14", "14.3.0-12ubuntu1~22~ppa2"), entries)
        self.assertTrue(all(version for _, version in entries))

    def test_checked_in_pins_parse_and_name_release_files(self):
        pins = mirror.parse_sums(mirror.PINS.read_text())
        for name in pins:
            self.assertEqual(name, mirror.asset_name(name))

    def test_sums_reject_conflicting_duplicates(self):
        with self.assertRaises(SystemExit):
            mirror.parse_sums(f"{H['a']}  x.deb\n{H['b']}  x.deb\n")
        self.assertEqual(mirror.parse_sums(f"# comment\n{H['a']} *x.deb\n"), {"x.deb": H["a"]})

    def test_resolve_finds_debs_and_their_source_packages(self):
        files = mirror.resolve(ENTRIES, PACKAGES.format(**H), SOURCES.format(**H))
        self.assertEqual(files["g++-14_14.3.0-12ubuntu1~22~ppa2_amd64.deb"],
                         ("pool/main/g/gcc-14/g++-14_14.3.0-12ubuntu1~22~ppa2_amd64.deb", H["gpp"], 10))
        self.assertEqual(files["gcc-14_14.3.0.orig.tar.gz"],
                         ("pool/main/g/gcc-14/gcc-14_14.3.0.orig.tar.gz", H["b"], 40))
        self.assertIn("gcc-16_16-20260315-1ubuntu1~22~ppa1.dsc", files)
        self.assertEqual(len(files), 5)

    def test_resolve_names_superseded_versions(self):
        with self.assertRaises(SystemExit) as raised:
            mirror.resolve([("g++-14", "14.2.0-1")], PACKAGES.format(**H), SOURCES.format(**H))
        self.assertIn("it has 14.3.0-12ubuntu1~22~ppa2", str(raised.exception))

    def test_resolve_needs_the_source_package(self):
        with self.assertRaises(SystemExit) as raised:
            mirror.resolve(ENTRIES, PACKAGES.format(**H), "")
        self.assertIn("source gcc-14", str(raised.exception))

    def test_release_hashes_read_the_sha256_section(self):
        text = ("Origin: x\nMD5Sum:\n 00 1 main/source/Sources.xz\nSHA256:\n"
                f" {H['a']} 10608 main/source/Sources.xz\n")
        self.assertEqual(mirror.release_hashes(text), {"main/source/Sources.xz": (H["a"], 10608)})

    def test_pins_conflicts_and_new_files(self):
        conflicts, unpinned = mirror.compare_pins({"a.deb": H["a"], "b.deb": H["b"]},
                                                  {"a.deb": H["c"]})
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(unpinned, {"b.deb": H["b"]})


class InReleaseTests(unittest.TestCase):
    def run_gpgv(self, status, returncode=0):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)

            def fake_download(url, dest, attempts=3):
                Path(dest).write_text("signed and unsigned text")

            def fake_run(args, **kwargs):
                Path(args[args.index("--output") + 1]).write_text("Origin: signed\n")
                return subprocess.CompletedProcess(args, returncode, status, "gpgv says")

            with mock.patch.object(mirror, "download", fake_download), \
                    mock.patch.object(mirror.subprocess, "run", fake_run):
                return mirror.verified_inrelease(work, work / "key.gpg")

    @staticmethod
    def good(fpr):
        return (f"[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG {fpr[-16:]} Launchpad PPA\n"
                f"[GNUPG:] VALIDSIG {fpr} 2026-03-17 1773700000 0 4 0 1 10 01 {fpr}\n")

    # The PPA's second signature, by its old 1024-bit key that the keyring leaves out.
    OLD_KEY = ("[GNUPG:] NEWSIG\n[GNUPG:] ERRSIG 1E9377A2BA9EF27F 1 10 01 1773700000 9 -\n"
               "[GNUPG:] NO_PUBKEY 1E9377A2BA9EF27F\n")

    def test_only_the_ppa_key_counts(self):
        fpr = mirror.PPA_KEY_FINGERPRINT
        self.assertEqual(self.run_gpgv(self.good(fpr)), "Origin: signed\n")
        with self.assertRaises(SystemExit):
            self.run_gpgv(self.good("A" * 40))
        with self.assertRaises(SystemExit):
            self.run_gpgv(f"[GNUPG:] VALIDSIG {fpr} 2026-03-17 x 0 4 0 1 10 01 {fpr}\n")

    def test_the_old_keys_unchecked_signature_does_not_fail_it(self):
        # gpgv exits 2 because it can't check the old key's signature; the PPA key's is good.
        fpr = mirror.PPA_KEY_FINGERPRINT
        self.assertEqual(self.run_gpgv(self.good(fpr) + self.OLD_KEY, returncode=2),
                         "Origin: signed\n")
        self.assertEqual(self.run_gpgv(self.OLD_KEY + self.good(fpr), returncode=2),
                         "Origin: signed\n")
        with self.assertRaises(SystemExit):
            self.run_gpgv(self.OLD_KEY, returncode=2)

    def test_a_bad_signature_fails_it(self):
        fpr = mirror.PPA_KEY_FINGERPRINT
        with self.assertRaises(SystemExit):
            self.run_gpgv(f"[GNUPG:] NEWSIG\n[GNUPG:] BADSIG {fpr[-16:]} Launchpad PPA\n", returncode=1)
        with self.assertRaises(SystemExit):  # a good one next to a bad one
            self.run_gpgv(self.good(fpr) + "[GNUPG:] BADSIG 1E9377A2BA9EF27F old\n", returncode=1)
        for status in ("EXPKEYSIG", "REVKEYSIG", "EXPSIG"):
            with self.assertRaises(SystemExit):
                self.run_gpgv(self.good(fpr).replace("GOODSIG", status), returncode=0)


# The mirror job runs on Linux; Git for Windows' gpg can't make keys in a temporary home there.
@unittest.skipUnless(os.name != "nt" and shutil.which("gpg") and shutil.which("gpgv"),
                     "needs gpg and gpgv on Linux")
class RealGpgvTests(unittest.TestCase):
    """InRelease signed by two keys, only one in the keyring, checked by the real gpgv."""
    def test_two_signers_one_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            home = work / "gnupg"
            home.mkdir(mode=0o700)
            env = {**os.environ, "GNUPGHOME": str(home)}
            self.addCleanup(subprocess.run, ["gpgconf", "--kill", "gpg-agent"], env=env,
                            capture_output=True)

            def gpg(*args, **kwargs):
                return subprocess.run(["gpg", "--batch", "--passphrase", "", "--pinentry-mode",
                                       "loopback", *args], env=env, check=True,
                                      capture_output=True, **kwargs)
            for uid in ("new@test", "old@test"):
                gpg("--quick-gen-key", uid, "ed25519", "sign", "never")
            fpr = next(line.split(":")[9] for line in
                       gpg("--with-colons", "--list-keys", "new@test", text=True).stdout.splitlines()
                       if line.startswith("fpr:"))
            (work / "msg").write_text("Origin: signed\n")
            gpg("--clearsign", "-u", "new@test", "-u", "old@test", "-o", str(work / "good.asc"),
                str(work / "msg"))
            good = (work / "good.asc").read_text()
            bad = good.replace("Origin: signed", "Origin: forged")
            keyring = work / "new.gpg"
            keyring.write_bytes(gpg("--export", "new@test").stdout)

            def check(text):
                def fake_download(url, dest, attempts=3):
                    Path(dest).write_text(text)
                inner = work / "inner"
                inner.mkdir(exist_ok=True)
                with mock.patch.object(mirror, "download", fake_download), \
                        mock.patch.object(mirror, "PPA_KEY_FINGERPRINT", fpr):
                    return mirror.verified_inrelease(inner, keyring)

            self.assertEqual(check(good), "Origin: signed\n")
            with self.assertRaises(SystemExit):
                check(bad)


class FakeGh:
    """gh release/api calls against an in-memory release."""
    def __init__(self, assets=None, prerelease=True, exists=True):
        self.assets = dict(assets or {})
        self.prerelease = prerelease
        self.exists = exists
        self.calls = []

    def __call__(self, *args, check=True):
        self.calls.append(args)
        if args[0] == "api":
            if not self.exists:
                return subprocess.CompletedProcess(args, 1, '{"message":"Not Found"}', "HTTP 404")
            assets = [{"name": n, "digest": "sha256:" + hashlib.sha256(b).hexdigest()}
                      for n, b in self.assets.items()]
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"prerelease": self.prerelease, "assets": assets}), "")
        if args[:2] == ("release", "create"):
            self.exists = True
            self.prerelease = "--prerelease" in args and "--latest=false" in args
        elif args[:2] == ("release", "upload"):
            path = Path(args[3])
            if path.name in self.assets and "--clobber" not in args:
                raise AssertionError(f"{path.name} uploaded twice without --clobber")
            self.assets[path.name] = path.read_bytes()
        elif args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            Path(args[args.index("--dir") + 1], name).write_bytes(self.assets[name])
        return subprocess.CompletedProcess(args, 0, "", "")


class UploadTests(unittest.TestCase):
    def upload(self, fake, files):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "toolchain"
            out.mkdir()
            sums = {}
            for name, data in files.items():
                (out / name).write_bytes(data)
                sums[name] = hashlib.sha256(data).hexdigest()
            (out / mirror.SUMS).write_text(mirror.format_sums(sums))
            with mock.patch.object(mirror, "gh", fake):
                mirror.cmd_upload(out, "o/r", "t", "abc")

    def test_new_release_is_a_prerelease_and_gets_every_file(self):
        fake = FakeGh(exists=False)
        self.upload(fake, {"a.deb": b"a", "a.dsc": b"src"})
        self.assertTrue(fake.prerelease)
        self.assertEqual(fake.assets["a.deb"], b"a")
        self.assertEqual(mirror.parse_sums(fake.assets[mirror.SUMS].decode()),
                         {"a.deb": hashlib.sha256(b"a").hexdigest(),
                          "a.dsc": hashlib.sha256(b"src").hexdigest()})

    def test_same_bytes_are_skipped_and_sums_keep_older_files(self):
        old = mirror.format_sums({"old.deb": hashlib.sha256(b"old").hexdigest()}).encode()
        fake = FakeGh({"old.deb": b"old", "a.deb": b"a", mirror.SUMS: old})
        self.upload(fake, {"a.deb": b"a", "b.deb": b"b"})
        uploads = [c for c in fake.calls if c[:2] == ("release", "upload")]
        self.assertEqual([Path(c[3]).name for c in uploads], ["b.deb", mirror.SUMS])
        self.assertEqual(set(mirror.parse_sums(fake.assets[mirror.SUMS].decode())),
                         {"old.deb", "a.deb", "b.deb"})

    def test_different_bytes_stop_before_any_upload(self):
        fake = FakeGh({"a.deb": b"tampered"})
        with self.assertRaises(SystemExit) as raised:
            self.upload(fake, {"a.deb": b"a", "b.deb": b"b"})
        self.assertIn("a.deb", str(raised.exception))
        self.assertFalse(any(c[:2] == ("release", "upload") for c in fake.calls))

    def test_a_conflicting_sums_entry_stops(self):
        sums = mirror.format_sums({"a.deb": hashlib.sha256(b"x").hexdigest()}).encode()
        fake = FakeGh({mirror.SUMS: sums})
        with self.assertRaises(SystemExit):
            self.upload(fake, {"a.deb": b"a"})
        self.assertFalse(any(c[:2] == ("release", "upload") for c in fake.calls))

    def test_a_full_release_is_never_made_latest(self):
        with self.assertRaises(SystemExit):
            self.upload(FakeGh(prerelease=False), {"a.deb": b"a"})


class FetchTests(unittest.TestCase):
    """fetch: the PPA's signed index against the checked-in pins (ci-toolchain-mirror.yml)."""
    def fetch(self, pins):
        self.fetched = fetched = []
        with tempfile.TemporaryDirectory() as tmp:
            out, pins_file, manifest_file = Path(tmp) / "out", Path(tmp) / "pins", Path(tmp) / "manifest"
            pins_file.write_text(mirror.format_sums(pins))
            manifest_file.write_text("".join(f"{p}_{v}\n" for p, v in ENTRIES))
            texts = {f"main/binary-{mirror.ARCH}/Packages.xz": PACKAGES.format(**H),
                     "main/source/Sources.xz": SOURCES.format(**H)}
            with mock.patch.object(mirror, "PINS", pins_file), \
                    mock.patch.object(mirror, "MANIFEST", manifest_file), \
                    mock.patch.object(mirror, "signing_key", lambda work: work / "key"), \
                    mock.patch.object(mirror, "verified_inrelease", lambda work, key: ""), \
                    mock.patch.object(mirror, "release_hashes", lambda text: {}), \
                    mock.patch.object(mirror, "index", lambda work, hashes, path: texts[path]), \
                    mock.patch.object(mirror, "fetch_checked",
                                      lambda path, sha256, size, dest: fetched.append(dest.name)):
                mirror.cmd_fetch(out)
            return fetched, mirror.parse_sums((out / "UNPINNED").read_text())

    def test_new_files_are_fetched_and_listed_as_unpinned(self):
        fetched, unpinned = self.fetch({})
        self.assertEqual(len(fetched), 5)
        self.assertEqual(set(unpinned), set(fetched))

    def test_an_index_that_disagrees_with_a_pin_stops_before_any_download(self):
        deb = mirror.asset_name("g++-14_14.3.0-12ubuntu1~22~ppa2_amd64.deb")
        with self.assertRaises(SystemExit) as raised:
            self.fetch({deb: H["c"]})
        self.assertIn("disagree; nothing was downloaded", str(raised.exception))
        self.assertIn(deb, str(raised.exception))
        self.assertEqual(self.fetched, [])


class SumsTests(unittest.TestCase):
    """sums: the GCC 14 build's files (ci-toolchain-gcc.yml) before the upload."""
    def sums(self, files, pins, repin=False):
        with tempfile.TemporaryDirectory() as tmp:
            out, pins_file = Path(tmp) / "upload", Path(tmp) / "pins"
            out.mkdir()
            (out / "nested").mkdir()  # folders are not release files
            for name, data in files.items():
                (out / name).write_bytes(data)
            pins_file.write_text(mirror.format_sums(pins))
            with mock.patch.object(mirror, "PINS", pins_file):
                mirror.cmd_sums(out, repin)
            return ((out / mirror.SUMS).read_text(), (out / "UNPINNED").read_text())

    def test_sums_list_every_file_and_the_unpinned_ones(self):
        tar = "gcc-14.3.0-jammy-x86_64.tar.xz"
        files = {tar: b"gcc", "gcc-14.3.0.tar.xz": b"src"}
        pins = {"gcc-14.3.0.tar.xz": hashlib.sha256(b"src").hexdigest()}
        sums, unpinned = self.sums(files, pins)
        self.assertEqual(mirror.parse_sums(sums),
                         {name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
        self.assertEqual(mirror.parse_sums(unpinned), {tar: hashlib.sha256(b"gcc").hexdigest()})

    def test_a_build_that_differs_from_its_pin_stops(self):
        tar = "gcc-14.3.0-jammy-x86_64.tar.xz"
        with self.assertRaises(SystemExit) as raised:
            self.sums({tar: b"rebuilt"}, {tar: hashlib.sha256(b"pinned").hexdigest()})
        self.assertIn(tar, str(raised.exception))

    def test_repin_lists_the_new_bytes_for_a_pull_request(self):
        tar = "gcc-14.3.0-jammy-x86_64.tar.xz"
        sums, unpinned = self.sums({tar: b"rebuilt"}, {tar: hashlib.sha256(b"pinned").hexdigest()},
                                   repin=True)
        self.assertEqual(mirror.parse_sums(unpinned), {tar: hashlib.sha256(b"rebuilt").hexdigest()})
        self.assertEqual(mirror.parse_sums(sums), {tar: hashlib.sha256(b"rebuilt").hexdigest()})

    def test_a_name_github_would_change_stops(self):
        with self.assertRaises(SystemExit):
            self.sums({"g++-14.tar.xz": b"x"}, {})


class StatusTests(unittest.TestCase):
    def status(self, fake, pins, manifest):
        with tempfile.TemporaryDirectory() as tmp:
            pins_file, manifest_file = Path(tmp) / "pins", Path(tmp) / "manifest"
            pins_file.write_text(mirror.format_sums(pins))
            manifest_file.write_text(manifest)
            with mock.patch.object(mirror, "gh", fake), \
                    mock.patch.object(mirror, "PINS", pins_file), \
                    mock.patch.object(mirror, "MANIFEST", manifest_file):
                return mirror.cmd_status("o/r", "t")

    def test_status(self):
        deb = "a_1tilde1_amd64.deb"
        pins = {deb: hashlib.sha256(b"a").hexdigest()}
        self.assertEqual(self.status(FakeGh({deb: b"a"}), pins, "a_1~1\n"), 0)
        self.assertEqual(self.status(FakeGh({}), pins, "a_1~1\n"), mirror.INCOMPLETE)
        self.assertEqual(self.status(FakeGh(exists=False), pins, "a_1~1\n"), mirror.INCOMPLETE)
        self.assertEqual(self.status(FakeGh({deb: b"a"}), pins, "a_1~1\nb_2\n"), mirror.INCOMPLETE)
        with self.assertRaises(SystemExit):
            self.status(FakeGh({deb: b"tampered"}), pins, "a_1~1\n")


if __name__ == "__main__":
    unittest.main()
