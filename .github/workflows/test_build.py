import pathlib
import os
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = pathlib.Path(__file__).with_name("build.yml")


class ReleaseWorkflowTest(unittest.TestCase):
    def test_missing_signing_secrets_do_not_block_desktop_release(self):
        jobs = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["jobs"]
        steps = jobs["build-android-release"]["steps"]
        signing = next(step for step in steps if step.get("id") == "signing")
        self.assertEqual(signing["working-directory"], ".")
        self.assertIn("ANDROID_KEYSTORE_BASE64", signing["env"])
        self.assertIn("GITHUB_OUTPUT", signing["run"])
        self.assertIn("available=false", signing["run"])
        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "output"
            env = os.environ.copy()
            env.update({name: "" for name in signing["env"]})
            env["GITHUB_OUTPUT"] = str(output)
            subprocess.run(["bash", "-e", "-c", signing["run"]], env=env, check=True)
            self.assertEqual(output.read_text(), "available=false\n")
            env.update({name: "present" for name in signing["env"]})
            output.unlink()
            subprocess.run(["bash", "-e", "-c", signing["run"]], env=env, check=True)
            self.assertEqual(output.read_text(), "available=true\n")
        for step in steps[steps.index(signing) + 1 :]:
            self.assertEqual(step.get("if"), "steps.signing.outputs.available == 'true'")
        self.assertIn("build-android-release", jobs["release"]["needs"])
        release_if = jobs["release"]["if"]
        self.assertIn("!cancelled()", release_if)
        for job_name in ("build-windows-x64", "build-windows-arm64", "build-win7", "build-macos", "build-linux"):
            self.assertIn(f"needs.{job_name}.result == 'success'", release_if)
        self.assertNotIn("needs.build-android-release.result == 'success'", release_if)
        self.assertNotIn("needs.validate-android.result == 'success'", release_if)
        self.assertIn("dist/tg-ws-proxy-android-*.apk", jobs["release"]["steps"][-1]["with"]["files"])

    def test_android_upload_is_optional_but_build_errors_remain_fatal(self):
        jobs = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["jobs"]
        steps = jobs["build-android-release"]["steps"]
        self.assertNotIn("continue-on-error", jobs["build-android-release"])
        self.assertTrue(any("assembleStandardRelease" in step.get("run", "") for step in steps))
        self.assertEqual(jobs["release"]["steps"][-1]["with"].get("fail_on_unmatched_files"), "false")
        for job_name in ("validate-android", "build-android-release"):
            versions = [step["with"]["python-version"] for step in jobs[job_name]["steps"]
                        if step.get("uses") == "actions/setup-python@v6"]
            self.assertEqual(versions, ["3.12", "3.11"])

    def test_desktop_assets_remain_required_before_release(self):
        jobs = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["jobs"]
        steps = jobs["release"]["steps"]
        release_index = next(i for i, step in enumerate(steps)
                             if step.get("uses") == "softprops/action-gh-release@v2")
        verify = next(step for step in steps[:release_index]
                      if step.get("name") == "Verify desktop release assets")
        desktop_assets = [line.strip() for line in steps[release_index]["with"]["files"].splitlines()
                          if line.strip() and "android" not in line]
        for asset in desktop_assets:
            self.assertIn(asset, verify["run"])
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(["bash", "-e", "-c", verify["run"]], cwd=temp_dir)
            self.assertNotEqual(result.returncode, 0)
            for asset in desktop_assets:
                path = pathlib.Path(temp_dir) / asset
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            subprocess.run(["bash", "-e", "-c", verify["run"]], cwd=temp_dir, check=True)


if __name__ == "__main__":
    unittest.main()
