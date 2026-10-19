"""Fake boundary checks only; real native cases are required on the hosted VM."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_isolated_systemd_rehearsal as stand


class IsolatedSystemdBoundaryTests(unittest.TestCase):
    def labels(self):
        return {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
        }

    def test_local_invocation_refuses_before_proc_paths_or_commands(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(stand.Path, "read_text") as read,
            patch.object(stand.os.path, "lexists") as exists,
            patch.object(stand.subprocess, "run") as command,
        ):
            with self.assertRaisesRegex(stand.StandRefusal, "github_hosted_linux_root_required"):
                stand.boundary()
        read.assert_not_called()
        exists.assert_not_called()
        command.assert_not_called()

    def test_each_hosted_platform_and_privilege_condition_is_required(self):
        stand.validate_labels(self.labels(), "linux", 0)
        for key in self.labels():
            with self.subTest(key=key), self.assertRaises(stand.StandRefusal):
                stand.validate_labels({**self.labels(), key: "wrong"}, "linux", 0)
        with self.assertRaises(stand.StandRefusal):
            stand.validate_labels(self.labels(), "win32", 0)
        with self.assertRaises(stand.StandRefusal):
            stand.validate_labels(self.labels(), "linux", 1001)

    def test_systemd_and_absent_production_paths_are_independently_required(self):
        stand.validate_host("systemd\n", False)
        with self.assertRaisesRegex(stand.StandRefusal, "real_pid1_systemd_required"):
            stand.validate_host("python", False)
        with self.assertRaisesRegex(stand.StandRefusal, "production_host_paths_refused"):
            stand.validate_host("systemd", True)

    def test_unowned_cleanup_path_refuses_before_filesystem_read(self):
        for path in (Path("/opt/autostopcrm"), Path("/run/autostop-crm-rehearsal-" + "-" * 36)):
            with self.subTest(path=path), patch.object(stand.Path, "lstat") as read:
                with self.assertRaisesRegex(stand.StandRefusal, "private_cleanup_scope"):
                    stand.safe_private_root(path)
                read.assert_not_called()

    def test_nested_fixed_worker_compiles_without_importing_or_executing_it(self):
        compile(stand.WORKER_SOURCE, "isolated-native-protocol-worker", "exec")
