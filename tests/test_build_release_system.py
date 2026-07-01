import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildReleaseSystemTests(unittest.TestCase):
    def test_build_script_has_valid_powershell_syntax(self) -> None:
        script = ROOT / "build.ps1"
        command = (
            "$errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref] $null, [ref] $errors) | Out-Null; "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_python_discovery_contract_is_present(self) -> None:
        script = (ROOT / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("[string] $PythonPath", script)
        self.assertIn("Test-WindowsAppsPython", script)
        self.assertIn("Python Launcher py -3.13", script)
        self.assertIn("Python Launcher py -3.12", script)
        self.assertIn("Python Launcher py -3.11", script)
        self.assertIn("python.exe from PATH", script)
        self.assertIn("No usable 64-bit Python installation was found.", script)
        self.assertIn("Primary failure: Python environment discovery", script)
        self.assertIn("Candidate accepted:", script)
        self.assertIn("Candidate rejected:", script)
        self.assertIn("struct.calcsize('P') * 8", script)

    def test_build_script_checks_onedir_executable_path(self) -> None:
        script = (ROOT / "build.ps1").read_text(encoding="utf-8")
        self.assertIn('"$script:AppName\\$script:AppName.exe"', script)
        self.assertIn('Join-Path $tempAppDir $targetExeName', script)
        self.assertNotIn('Join-Path $script:DistDir "$script:AppName.exe"', script)

    def test_spec_relies_on_pyinstaller_hooks_for_heavy_dependencies(self) -> None:
        spec = (ROOT / "RacingTelemetry.spec").read_text(encoding="utf-8")
        self.assertNotIn("collect_data_files(\"PySide6\"", spec)
        self.assertNotIn("collect_data_files(\"numpy\"", spec)
        self.assertNotIn("collect_dynamic_libs(\"PySide6\"", spec)
        self.assertNotIn("collect_dynamic_libs(\"numpy\"", spec)

    def test_windows_release_workflow_contract(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-windows-release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn('tags:', workflow)
        self.assertIn('"v*"', workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("actions/setup-python@v5", workflow)
        self.assertIn('architecture: "x64"', workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("RacingTelemetry-Windows-x64.zip", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)

    def test_generated_release_outputs_are_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            ".venv/",
            "build/",
            "dist/",
            "dist_new/",
            "release/",
            "build_smoke_test/",
            "build_logs/",
            "__pycache__/",
            "*.pyc",
            "RacingTelemetry-Windows-x64.zip",
        ):
            self.assertIn(pattern, gitignore)


if __name__ == "__main__":
    unittest.main()
