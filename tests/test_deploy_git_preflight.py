from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = PROJECT_ROOT / "scripts" / "release_git_preflight.sh"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git and bash are required")
class ReleaseGitPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run(self, *command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def _create_checkout(self, name: str, branch: str) -> tuple[Path, Path]:
        remote = self.base_dir / f"{name}-remote.git"
        checkout = self.base_dir / name
        self._run("git", "init", "--bare", f"--initial-branch={branch}", str(remote))
        self._run("git", "init", f"--initial-branch={branch}", str(checkout))
        self._run("git", "config", "user.name", "Release Test", cwd=checkout)
        self._run("git", "config", "user.email", "release-test@example.invalid", cwd=checkout)
        (checkout / "tracked.txt").write_text("release\n", encoding="utf-8")
        self._run("git", "add", "tracked.txt", cwd=checkout)
        self._run("git", "commit", "-m", "initial", cwd=checkout)
        self._run("git", "remote", "add", "origin", str(remote), cwd=checkout)
        self._run("git", "push", "--set-upstream", "origin", branch, cwd=checkout)
        return checkout, remote

    def _preflight(
        self,
        checkout: Path,
        *,
        branch: str,
        label: str = "Release Test",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; release_git_verify_fetched_checkout "$2" "$3" "$4" "$5" "$6"',
                "bash",
                str(PREFLIGHT_SCRIPT),
                label,
                str(checkout),
                branch,
                "origin",
                branch,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_clean_exact_branch_and_remote_head_returns_only_commit_sha(self) -> None:
        checkout, _remote = self._create_checkout("clean", "AutostopManager")
        expected = self._run("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()

        result = self._preflight(checkout, branch="AutostopManager")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, result.stdout.strip())
        self.assertEqual("", result.stderr)

    def test_untracked_file_fails_without_leaking_its_name(self) -> None:
        checkout, _remote = self._create_checkout("dirty", "AutostopManager")
        secret_like_name = "do-not-print-private-name.txt"
        (checkout / secret_like_name).write_text("private\n", encoding="utf-8")

        result = self._preflight(checkout, branch="AutostopManager")

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("not clean, including untracked files", result.stderr)
        self.assertNotIn(secret_like_name, result.stderr)

    def test_wrong_branch_fails_closed(self) -> None:
        checkout, _remote = self._create_checkout("wrong-branch", "AutostopManager")
        self._run("git", "switch", "-c", "codex/not-production", cwd=checkout)

        result = self._preflight(checkout, branch="AutostopManager")

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("must be on branch AutostopManager", result.stderr)

    def test_detached_head_fails_closed(self) -> None:
        checkout, _remote = self._create_checkout("wrong-branch", "AutostopManager")
        self._run("git", "switch", "--detach", cwd=checkout)

        result = self._preflight(checkout, branch="AutostopManager")

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("must not use a detached HEAD", result.stderr)

    def test_clean_checkout_behind_remote_fails_after_exact_fetch(self) -> None:
        checkout, remote = self._create_checkout("behind", "autostopcrm-v1")
        publisher = self.base_dir / "publisher"
        self._run(
            "git",
            "clone",
            "--branch",
            "autostopcrm-v1",
            str(remote),
            str(publisher),
        )
        self._run("git", "config", "user.name", "Release Publisher", cwd=publisher)
        self._run(
            "git",
            "config",
            "user.email",
            "publisher@example.invalid",
            cwd=publisher,
        )
        (publisher / "tracked.txt").write_text("remote update\n", encoding="utf-8")
        self._run("git", "commit", "-am", "remote update", cwd=publisher)
        self._run("git", "push", "origin", "autostopcrm-v1", cwd=publisher)

        result = self._preflight(checkout, branch="autostopcrm-v1", label="AutoStop CRM")

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("HEAD does not match the fetched remote branch", result.stderr)
        self.assertNotIn(str(remote), result.stderr)


if __name__ == "__main__":
    unittest.main()
