from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = PROJECT_ROOT / "scripts" / "release_git_preflight.sh"


def _release_shell_available() -> bool:
    bash = shutil.which("bash")
    if os.name != "posix" or not shutil.which("git") or not bash:
        return False
    try:
        result = subprocess.run(
            [bash, "-c", "command -v timeout >/dev/null && command -v flock >/dev/null"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@unittest.skipUnless(_release_shell_available(), "POSIX git, bash, timeout and flock are required")
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

    def test_invalid_fetch_timeout_fails_before_network_access(self) -> None:
        checkout, _remote = self._create_checkout("timeout", "autostopcrm-v1")

        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; AUTOSTOP_GIT_FETCH_TIMEOUT_SECONDS=0 '
                'release_git_verify_fetched_checkout "$2" "$3" "$4" origin "$5"',
                "bash",
                str(PREFLIGHT_SCRIPT),
                "AutoStop CRM",
                str(checkout),
                "autostopcrm-v1",
                "autostopcrm-v1",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("fetch timeout must be between 5 and 300 seconds", result.stderr)

    def test_hanging_fetch_is_bounded_redacted_and_releases_outer_lock(self) -> None:
        checkout, _remote = self._create_checkout("hanging", "autostopcrm-v1")
        upload_pack = self.base_dir / "private-hanging-upload-pack"
        upload_pack.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        upload_pack.chmod(0o700)
        self._run(
            "git",
            "config",
            "remote.origin.uploadpack",
            str(upload_pack),
            cwd=checkout,
        )
        deploy_lock = self.base_dir / "deploy.lock"

        started_at = time.monotonic()
        result = subprocess.run(
            [
                "bash",
                "-c",
                'exec {lock_fd}>"$1"; flock -n "$lock_fd"; source "$2"; '
                "AUTOSTOP_GIT_FETCH_TIMEOUT_SECONDS=5 release_git_verify_fetched_checkout "
                '"AutoStop CRM" "$3" autostopcrm-v1 origin autostopcrm-v1',
                "bash",
                str(deploy_lock),
                str(PREFLIGHT_SCRIPT),
                str(checkout),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
        elapsed = time.monotonic() - started_at

        self.assertEqual(2, result.returncode)
        self.assertLess(elapsed, 10)
        self.assertEqual("", result.stdout)
        self.assertIn("exact remote branch fetch failed", result.stderr)
        self.assertNotIn(str(upload_pack), result.stderr)
        released = subprocess.run(
            ["flock", "-n", str(deploy_lock), "true"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, released.returncode)

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
