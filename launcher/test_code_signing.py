"""Code signing of the Windows release through SignPath Foundation (#354): off until the repository
variable is set, never on pull requests, the launcher and uninstaller signed before the installer is
compiled, the installer after, every signature checked, and the unsigned uploads never released.
Run: python3 -m unittest launcher/test_code_signing.py"""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGING = HERE / "packaging"
RELEASE_YML = HERE.parent / ".github" / "workflows" / "release.yml"
SIGNPATH_ACTION = "signpath/github-action-submit-signing-request@"
NS = {"a": "http://signpath.io/artifact-configuration/v1"}


class Step(dict):
    """One step of a job, read from the workflow text (the tests need no YAML library): name, if,
    uses, id, run (the block's text) and the with: keys, all as strings."""


def _value(text):
    text = text.split(" #", 1)[0].strip() if not text.lstrip().startswith(("'", '"')) else text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text


def _step(chunk):
    step = Step()
    lines = chunk.splitlines()
    lines[0] = "        " + lines[0][8:]  # "      - name: x" -> "        name: x"
    section, block = None, []
    for line in lines:
        if re.match(r"^        [a-z-]+:", line):
            key, _, rest = line.strip().partition(":")
            section = key
            if rest.strip() in ("|", ""):
                block = []
                step[key] = "" if key != "with" else {}
                if key == "with":
                    step["with"] = {}
            else:
                step[key] = _value(rest)
        elif section == "with" and re.match(r"^          [a-z-]+:", line):
            key, _, rest = line.strip().partition(":")
            if rest.strip() == "|":
                step["with"][key] = ""
                block = key
            else:
                step["with"][key] = _value(rest)
                block = None
        elif section == "with" and block:
            step["with"][block] += line.strip() + "\n"
        elif section == "run":
            step["run"] += line[10:] + "\n"
    step.setdefault("with", {})
    return step


def job_text(name):
    text = RELEASE_YML.read_text(encoding="utf-8")
    start = text.index(f"\n  {name}:\n")
    following = re.compile(r"^  [a-z][a-z0-9-]*:$", re.M).search(text, start + len(name) + 5)
    return text[start:following.start() if following else len(text)]


def steps(job):
    text = job_text(job)
    body = text[text.index("    steps:\n") + len("    steps:\n"):]
    # comment lines between steps and between jobs are not part of them
    body = "\n".join(line for line in body.splitlines() if not re.match(r"^ {0,8}#", line)).rstrip() + "\n"
    chunks = re.split(r"(?m)^(?=      - )", body)
    return [_step(chunk) for chunk in chunks if chunk.startswith("      - ")]


def workflow_env():
    text = RELEASE_YML.read_text(encoding="utf-8")
    block = re.search(r"(?m)^env:\n((?:  .*\n)+)", text).group(1)
    return {key: _value(value) for key, value in re.findall(r"(?m)^  ([A-Z_]+): (.*)$", block)}


def index(job_steps, name):
    return next(i for i, step in enumerate(job_steps) if step.get("name") == name)


class Switch(unittest.TestCase):
    def test_signing_is_off_without_the_variable_and_on_pull_requests(self):
        env = workflow_env()
        self.assertIn("vars.SIGNPATH_ORGANIZATION_ID != ''", env["SIGN"])
        self.assertIn("github.event_name != 'pull_request'", env["SIGN"])
        self.assertEqual(env["SIGN_MODE"], "${{ github.ref_type == 'tag' && 'release' || 'test' }}")

    def test_every_signing_step_is_behind_the_switch(self):
        job = steps("launcher-windows")
        signing = [s for s in job if SIGNPATH_ACTION in s.get("uses", "") or "unsigned-" in str(s.get("with", {}).get("name", ""))
                   or "sign" in s.get("name", "").lower()]
        self.assertGreaterEqual(len(signing), 7)
        for step in signing:
            self.assertEqual(step.get("if"), "env.SIGN == 'true'", step.get("name") or step.get("uses"))
        # the unsigned build is the one that always runs
        installer = job[index(job, "Installer (Inno Setup, with the Visual C++ 2015-2022 x64 redistributable)")]
        self.assertNotIn("if", installer)
        self.assertIn("if ($env:SIGN -eq 'true') { $signed.SignedUninstallerDir = 'signed-uninstaller' }", installer["run"])


class Order(unittest.TestCase):
    def test_launcher_and_uninstaller_are_signed_before_the_manifest_and_the_installer(self):
        job = steps("launcher-windows")
        package = index(job, "Package")
        prepare = index(job, "Files to sign (the launcher and the uninstaller)")
        sign = index(job, "Sign the launcher and the uninstaller (SignPath)")
        put = index(job, "Put the signed launcher and uninstaller in place")
        installer = index(job, "Installer (Inno Setup, with the Visual C++ 2015-2022 x64 redistributable)")
        sign_installer = index(job, "Sign the installer (SignPath)")
        put_installer = index(job, "Put the signed installer in place")
        upload = next(i for i, s in enumerate(job) if s.get("with", {}).get("name") == "launcher-windows")
        self.assertLess(package, prepare)
        self.assertLess(prepare, sign)
        self.assertLess(sign, put)
        self.assertLess(put, installer)
        self.assertLess(installer, sign_installer)
        self.assertLess(sign_installer, put_installer)
        self.assertLess(put_installer, upload)
        self.assertIn("windows_build_manifest.py", job[installer]["run"])
        self.assertIn("-PrepareUninstaller", job[prepare]["run"])

    def test_each_request_signs_the_artifact_uploaded_just_before_it(self):
        job = steps("launcher-windows")
        requests = [i for i, s in enumerate(job) if SIGNPATH_ACTION in s.get("uses", "")]
        self.assertEqual(len(requests), 2)
        for i, configuration in zip(requests, ("binaries", "installer")):
            upload = job[i - 1]
            self.assertTrue(upload["uses"].startswith("actions/upload-artifact@"))
            self.assertTrue(upload["with"]["name"].startswith("unsigned-"))
            self.assertEqual(upload["with"]["if-no-files-found"], "error")
            request = job[i]
            # a third-party action that is handed the API token: pinned to a commit
            self.assertRegex(request["uses"], r"@[0-9a-f]{40}$")
            w = request["with"]
            self.assertEqual(w["github-artifact-id"], f"${{{{ steps.{upload['id']}.outputs.artifact-id }}}}")
            self.assertEqual(w["artifact-configuration-slug"], configuration)
            self.assertEqual(w["signing-policy-slug"], "${{ env.SIGN_MODE }}-signing")
            self.assertEqual(w["api-token"], "${{ secrets.SIGNPATH_API_TOKEN }}")
            self.assertEqual(str(w["wait-for-completion"]).lower(), "true")
            self.assertIn('version: "${{ env.VERSION }}"', w["parameters"])
            # and what comes back is checked before it is used
            self.assertIn("verify_signature.ps1 -Mode $env:SIGN_MODE", job[i + 1]["run"])
            self.assertIn("throw", job[i + 1]["run"])

    def test_the_signed_launcher_still_runs(self):
        job = steps("launcher-windows")
        put = job[index(job, "Put the signed launcher and uninstaller in place")]["run"]
        self.assertIn("forza-launcher.exe --version", put)
        self.assertLess(put.index("verify_signature.ps1"), put.index("Copy-Item"))

    def test_signpath_can_read_the_run(self):
        job = job_text("launcher-windows")
        permissions = job[job.index("    permissions:\n"):job.index("    steps:\n")]
        self.assertEqual(re.findall(r"(?m)^      ([a-z-]+): ([a-z]+)", permissions), [("contents", "read"), ("actions", "read")])


class Release(unittest.TestCase):
    def test_unsigned_uploads_are_never_published(self):
        job = steps("publish")
        download = next(s for s in job if s.get("uses", "").startswith("actions/download-artifact@"))
        self.assertNotIn("merge-multiple", download["with"])
        collect = job[index(job, "Collect the release files")]["run"]
        self.assertIn("rm -rf artifacts/unsigned-*", collect)
        self.assertLess(index(job, "Collect the release files"), index(job, "Release"))
        self.assertLess(collect.index("rm -rf artifacts/unsigned-*"), collect.index("mv"))

    def test_installer_test_checks_the_signatures_of_a_signed_build(self):
        run = steps("installer-test")[-1]["run"]
        self.assertIn("-Signature $signature", run)
        script = (PACKAGING / "test_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("[ValidateSet('none', 'test', 'release')][string]$Signature = 'none'", script)
        self.assertIn('"unins000.exe"', script)
        self.assertIn("verify_signature.ps1", script)


class Inno(unittest.TestCase):
    def test_signed_uninstaller_mode_only_with_the_define(self):
        script = (PACKAGING / "installer.iss").read_text(encoding="utf-8")
        block = re.search(r"^#ifdef SignedUninstallerDir\n(.*?)^#endif\n", script, re.M | re.S)
        self.assertIsNotNone(block)
        lines = [line for line in block.group(1).splitlines() if not line.startswith(";")]
        self.assertEqual(lines, ["SignedUninstaller=yes", "SignedUninstallerDir={#SignedUninstallerDir}"])
        # signing after compiling, never a SignTool that would need a certificate on the runner
        self.assertNotRegex(script, r"(?m)^SignTool=")

    def test_build_script_stops_only_for_the_signature(self):
        build = (PACKAGING / "build_installer.ps1").read_text(encoding="utf-8")
        # Inno Setup's message (SCompilerSignatureNeeded) when the uninstaller still needs its signature
        self.assertIn("'Signed uninstaller mode is enabled'", build)
        self.assertIn('"/DSignedUninstallerDir=$uninstallerDir"', build)


class ArtifactConfigurations(unittest.TestCase):
    def files(self, name):
        root = ET.parse(PACKAGING / "signpath" / f"{name}.xml").getroot()
        self.assertEqual(root.tag, "{%s}artifact-configuration" % NS["a"])
        self.assertEqual([p.get("name") for p in root.findall("a:parameters/a:parameter", NS)], ["version"])
        # actions/upload-artifact uploads a ZIP file
        return root.findall("a:zip-file/a:pe-file", NS)

    def check(self, pe):
        # SignPath Foundation: every signed file names the project and this version
        self.assertEqual(pe.get("product-name"), "Forza Horizon 1: ReImagined")
        self.assertEqual(pe.get("product-version"), "${version}")
        sign = pe.find("a:authenticode-sign", NS)
        self.assertEqual(sign.get("hash-algorithm"), "sha256")

    def test_binaries(self):
        files = self.files("binaries")
        self.assertEqual([pe.get("path") for pe in files], ["forza-launcher.exe", "uninst-*.e32"])
        for pe in files:
            self.check(pe)

    def test_installer(self):
        files = self.files("installer")
        self.assertEqual([pe.get("path") for pe in files], ["ForzaHorizon1-ReImagined-Setup-*.exe"])
        self.check(files[0])

    def test_product_name_is_the_one_in_the_version_resources(self):
        script = (PACKAGING / "installer.iss").read_text(encoding="utf-8")
        self.assertIn('#define AppName "Forza Horizon 1: ReImagined"', script)
        self.assertIn('PRODUCT = "Forza Horizon 1: ReImagined"', (PACKAGING / "windows_version_info.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
