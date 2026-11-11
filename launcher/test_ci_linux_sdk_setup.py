"""Exercise CI dependency retries without installing packages or downloading tools."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parent / "packaging/ci_linux_sdk_setup.sh"
APT = Path(__file__).parent / "packaging/ci_apt.sh"


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires a POSIX shell")
class LinuxSdkSetupTests(unittest.TestCase):
    def run_setup(self, failures, *args, index_failures=0, headers=True,
                  local_debs_install_headers=False, crlf_manifest=False, ppa_fails=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packaging = root / "packaging"
            packaging.mkdir()
            script = packaging / SCRIPT.name
            script.write_bytes(SCRIPT.read_bytes())
            (packaging / APT.name).write_bytes(APT.read_bytes())
            (packaging / APT.name).chmod(0o755)
            (root / "etc-apt").mkdir()
            manifest = SCRIPT.with_name("ci_toolchain_debs.txt").read_text()
            if crlf_manifest:
                manifest = manifest.rstrip("\n").replace("\n", "\r\n")
            (packaging / "ci_toolchain_debs.txt").write_bytes(manifest.encode())
            log = root / "commands"
            stub = r'''#!/usr/bin/env bash
name="${0##*/}"
printf '%s %s\n' "$name" "$*" >> "$CI_TEST_LOG"
if [ "$name" = id ]; then echo 0; exit 0; fi
if [ "$name" = add-apt-repository ] && [ "$CI_TEST_PPA_FAILS" = 1 ]; then exit 1; fi
if [ "$name" = apt-get ] && [[ "$*" == *g++-14* ]]; then
    n=0; [ ! -f "$CI_TEST_COUNT" ] || read -r n < "$CI_TEST_COUNT"
    n=$((n + 1)); echo "$n" > "$CI_TEST_COUNT"
    if [ "$n" -le "$CI_TEST_FAILURES" ]; then exit 100; fi
    if [ "$CI_TEST_LOCAL_HEADERS" = 1 ] && [[ "$*" == *.deb* ]]; then
        mkdir -p "$CI_CXX_HEADERS"
    fi
fi
if [ "$name" = apt-cache ]; then
    n=0; [ ! -f "$CI_TEST_INDEX" ] || read -r n < "$CI_TEST_INDEX"
    n=$((n + 1)); echo "$n" > "$CI_TEST_INDEX"
    if [ "$n" -le "$CI_TEST_INDEX_FAILURES" ]; then echo "Package: clang-14"; exit 0; fi
    echo "Package: g++-14"
fi
'''
            for name in ("id", "apt-get", "apt-cache", "add-apt-repository", "sed", "sleep",
                         "python3", "curl"):
                target = root / name
                target.write_text(stub)
                target.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       CI_TEST_LOG=str(log), CI_TEST_COUNT=str(root / "count"),
                       CI_TEST_FAILURES=str(failures), CI_TEST_INDEX=str(root / "index"),
                       CI_TEST_INDEX_FAILURES=str(index_failures),
                       CI_TEST_LOCAL_HEADERS=str(int(local_debs_install_headers)),
                       CI_TEST_PPA_FAILS=str(int(ppa_fails)), CI_APT_ETC=str(root / "etc-apt"),
                       CI_CXX_HEADERS=str(root if headers else root / "missing"))
            result = subprocess.run(["bash", str(script), *args], env=env, text=True,
                                    capture_output=True, timeout=10)
            return result, log.read_text().splitlines()

    def test_transient_install_failure_recovers(self):
        result, commands = self.run_setup(2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(row.startswith("apt-get ") and "g++-14" in row for row in commands), 3)
        self.assertEqual([row for row in commands if row.startswith("sleep ")],
                         ["sleep 10", "sleep 20"])
        self.assertEqual(sum(row.startswith("python3 ") for row in commands), 3)
        self.assertTrue(all("Acquire::Retries=3" in row for row in commands
                            if row.startswith("apt-get ")))

    def test_persistent_failure_stops_before_downloads(self):
        result, commands = self.run_setup(99)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(row.startswith("apt-get ") and "g++-14" in row for row in commands), 9)
        self.assertTrue(any(row.startswith("sed ") for row in commands))
        self.assertTrue(any(row.startswith("curl ") for row in commands))
        self.assertFalse(any(row.startswith("python3 ") for row in commands))

    def test_failed_downloads_fall_back_to_old_host(self):
        result, commands = self.run_setup(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(row.startswith("apt-get ") and "g++-14" in row for row in commands), 4)
        sed = [i for i, row in enumerate(commands) if row.startswith("sed ")]
        self.assertEqual(len(sed), 1)
        after = [row for row in commands[sed[0] + 1:] if row.startswith("apt-get ")]
        self.assertTrue(after[0].endswith(" update"))
        self.assertEqual(sum(row.startswith("python3 ") for row in commands), 3)

    def test_success_does_not_retry(self):
        result, commands = self.run_setup(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(row.startswith("apt-get ") and "g++-14" in row for row in commands), 1)
        self.assertFalse(any(row.startswith("sleep ") for row in commands))

    def test_cache_hit_installs_compiler_without_sdk_downloads(self):
        result, commands = self.run_setup(0, "--compiler-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        installs = [row for row in commands if row.startswith("apt-get ") and "install" in row]
        self.assertTrue(any(row.endswith("install -y g++-14") for row in installs))
        self.assertFalse(any("libvulkan-dev" in row for row in installs))
        self.assertFalse(any(row.startswith("python3 ") for row in commands))

    def test_unreachable_ppa_falls_back_to_old_host(self):
        result, commands = self.run_setup(0, index_failures=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(row.startswith("sed ") and "http://ppa.launchpad.net/" in row
                            for row in commands))
        self.assertEqual(sum(row.endswith(" update") for row in commands), 3)

    def test_both_ppa_hosts_down_use_launchpad_files(self):
        result, commands = self.run_setup(6)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = [row for row in commands if row.startswith("curl ")]
        listed = [line for line in SCRIPT.with_name("ci_toolchain_debs.txt").read_text().splitlines()
                  if line and not line.startswith("#")]
        self.assertEqual(len(files), len(listed))
        self.assertTrue(all("launchpad.net/~ubuntu-toolchain-r/+archive/ubuntu/test/+files/" in row
                            for row in files))
        installs = [row for row in commands if row.startswith("apt-get ") and "install" in row]
        self.assertIn(".deb", installs[-1])
        self.assertEqual(sum(row.startswith("python3 ") for row in commands), 3)

    def test_unaddable_ppa_uses_launchpad_files(self):
        # add-apt-repository fails when api.launchpad.net does not resolve.
        result, commands = self.run_setup(0, ppa_fails=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(row.startswith("add-apt-repository ") for row in commands), 3)
        self.assertEqual([row for row in commands if row.startswith("sleep ")],
                         ["sleep 10", "sleep 20"])
        self.assertFalse(any(row.startswith("sed ") for row in commands))
        installs = [row for row in commands if row.startswith("apt-get ") and "g++-14" in row]
        self.assertEqual(len(installs), 1)
        self.assertIn(".deb", installs[0])
        self.assertTrue(any(row.startswith("curl ") for row in commands))
        self.assertEqual(sum(row.startswith("python3 ") for row in commands), 3)

    def test_reachable_ppa_keeps_its_host(self):
        result, commands = self.run_setup(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(row.startswith("sed ") for row in commands))

    def test_apt_success_without_headers_uses_file_fallback(self):
        # APT can install clang-14 for a missing g++-14 pattern and still exit 0.
        # Only the local package install supplies the headers in this scenario.
        result, commands = self.run_setup(
            0, "--compiler-only", headers=False, local_debs_install_headers=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        installs = [row for row in commands if row.startswith("apt-get ") and "g++-14" in row]
        self.assertEqual(len(installs), 3)
        self.assertNotIn(".deb", installs[0])
        self.assertNotIn(".deb", installs[1])
        self.assertIn(".deb", installs[2])
        self.assertTrue(any(row.startswith("curl ") for row in commands))
        self.assertFalse(any(row.startswith("python3 ") for row in commands))

    def test_crlf_manifest_without_final_newline_downloads_every_package(self):
        result, commands = self.run_setup(
            0, "--compiler-only", headers=False, local_debs_install_headers=True,
            crlf_manifest=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        downloads = [row for row in commands if row.startswith("curl ")]
        packages = [line for line in SCRIPT.with_name("ci_toolchain_debs.txt").read_text().splitlines()
                    if line and not line.startswith("#")]
        self.assertEqual(len(downloads), len(packages))
        for command, package in zip(downloads, packages):
            self.assertIn(f"/+files/{package}_amd64.deb", command)

    def test_missing_headers_stop_before_downloads(self):
        for args in ((), ("--compiler-only",)):
            result, commands = self.run_setup(0, *args, headers=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("C++23 headers", result.stderr)
            self.assertFalse(any(row.startswith("python3 ") for row in commands))


MIRRORS = ("http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
           "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
           "https://security.ubuntu.com/ubuntu/\tpriority:3\n")
AZURE_ERROR = ("W: Failed to fetch mirror+file:/etc/apt/apt-mirrors.txt/dists/jammy/InRelease"
               "  Temporary failure resolving 'azure.archive.ubuntu.com'")


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires a POSIX shell")
class CiAptTests(unittest.TestCase):
    def run_apt(self, *args, resolves=True, azure_errors=0, azure_status=100, failures=0,
                mirrors=MIRRORS):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = root / "etc-apt"
            (etc / "sources.list.d").mkdir(parents=True)
            if mirrors:
                (etc / "apt-mirrors.txt").write_text(mirrors)
            (etc / "sources.list").write_text("deb mirror+file:/etc/apt/apt-mirrors.txt jammy main\n")
            log = root / "commands"
            stub = r'''#!/usr/bin/env bash
name="${0##*/}"
printf '%s %s\n' "$name" "$*" >> "$CI_TEST_LOG"
if [ "$name" = id ]; then echo 0; exit 0; fi
if [ "$name" = getent ]; then exit "$CI_TEST_GETENT"; fi
if [ "$name" = apt-get ]; then
    n=0; [ ! -f "$CI_TEST_COUNT" ] || read -r n < "$CI_TEST_COUNT"
    n=$((n + 1)); echo "$n" > "$CI_TEST_COUNT"
    if [ "$n" -le "$CI_TEST_AZURE" ]; then echo "$CI_TEST_AZURE_ERROR"; exit "$CI_TEST_AZURE_STATUS"; fi
    if [ "$n" -le $((CI_TEST_AZURE + CI_TEST_FAILURES)) ]; then exit 100; fi
fi
'''
            for name in ("id", "getent", "apt-get", "sleep"):
                target = root / name
                target.write_text(stub)
                target.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       CI_TEST_LOG=str(log), CI_TEST_COUNT=str(root / "count"),
                       CI_TEST_GETENT="0" if resolves else "2", CI_TEST_AZURE=str(azure_errors),
                       CI_TEST_AZURE_ERROR=AZURE_ERROR, CI_TEST_AZURE_STATUS=str(azure_status),
                       CI_TEST_FAILURES=str(failures), CI_APT_ETC=str(etc))
            result = subprocess.run(["bash", str(APT), *args], env=env, text=True,
                                    capture_output=True, timeout=10)
            mirror_file = etc / "apt-mirrors.txt"
            return (result, log.read_text().splitlines(),
                    mirror_file.read_text() if mirror_file.exists() else "")

    def apt_calls(self, commands):
        return [row.split(" ", 3)[-1] for row in commands if row.startswith("apt-get ")]

    def test_unresolved_azure_mirror_is_replaced_up_front(self):
        result, commands, mirrors = self.run_apt("update", resolves=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("azure", mirrors)
        self.assertIn("http://archive.ubuntu.com/ubuntu/", mirrors)
        self.assertIn("https://security.ubuntu.com/ubuntu/", mirrors)
        self.assertEqual(self.apt_calls(commands), ["update"])

    def test_azure_failure_during_install_switches_and_updates(self):
        result, commands, mirrors = self.run_apt("install", "-y", "libegl1", azure_errors=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("azure", mirrors)
        self.assertEqual(self.apt_calls(commands),
                         ["install -y libegl1", "update", "install -y libegl1"])
        self.assertFalse(any(row.startswith("sleep ") for row in commands))

    def test_update_warning_about_azure_switches(self):
        # apt-get update exits 0 when an index fails to download.
        result, commands, mirrors = self.run_apt("update", azure_errors=1, azure_status=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("azure", mirrors)
        self.assertEqual(self.apt_calls(commands), ["update", "update"])

    def test_without_azure_mirror_retries_with_backoff(self):
        result, commands, _ = self.run_apt("update", failures=99, mirrors="")
        self.assertEqual(result.returncode, 100)
        self.assertFalse(any(row.startswith("getent ") for row in commands))
        self.assertEqual(len(self.apt_calls(commands)), 3)
        self.assertEqual([row for row in commands if row.startswith("sleep ")],
                         ["sleep 10", "sleep 20"])
        self.assertTrue(all("Acquire::Retries=3" in row for row in commands
                            if row.startswith("apt-get ")))

    def test_transient_failure_recovers(self):
        result, commands, mirrors = self.run_apt("update", failures=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("azure", mirrors)
        self.assertEqual(len(self.apt_calls(commands)), 2)


if __name__ == "__main__":
    unittest.main()
