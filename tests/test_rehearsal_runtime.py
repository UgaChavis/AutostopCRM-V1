"""Synthetic bootstrap refusals; actual Windows native acceptance runs on the stand."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import rehearsal_runtime as rehearsal


class RehearsalRuntimeTests(unittest.TestCase):
    def test_bootstrap_cannot_write_outside_its_disposable_windows_runner(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}):
                with self.assertRaisesRegex(RuntimeError, "disposable GitHub runner"):
                    rehearsal.prepare_windows_bootstrap(root / "sqlite3.dll", root / "bootstrap")
            self.assertFalse((root / "bootstrap").exists())

    def test_changed_dll_refuses_before_publishing_bootstrap(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            library = root / "sqlite3.dll"
            library.write_bytes(b"synthetic changed DLL")
            with (
                patch.object(rehearsal.sys, "platform", "win32"),
                patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "pinned official DLL"):
                    rehearsal.prepare_windows_bootstrap(library, root / "bootstrap")
            self.assertFalse((root / "bootstrap").exists())

    def test_sitecustomize_mismatch_is_fatal_before_the_python_entrypoint(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            library = root / "sqlite3.dll"
            library.write_bytes(b"synthetic payload; never loaded")
            with (
                patch.object(rehearsal.sys, "platform", "win32"),
                patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
                patch.object(
                    rehearsal,
                    "WINDOWS_DLL_SHA256",
                    hashlib.sha256(library.read_bytes()).hexdigest(),
                ),
            ):
                rehearsal.prepare_windows_bootstrap(library, root / "bootstrap")
            environment = {
                **os.environ,
                "GITHUB_ACTIONS": "false",
                "PYTHONPATH": str(root / "bootstrap"),
            }
            probe = subprocess.run(
                [sys.executable, "-c", "raise RuntimeError('ENTRYPOINT_MUST_NOT_RUN')"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(probe.returncode, 126, probe.stdout + probe.stderr)
            self.assertIn("Rehearsal SQLite bootstrap failed", probe.stderr)
            self.assertNotIn("ENTRYPOINT_MUST_NOT_RUN", probe.stdout + probe.stderr)

    def test_unsupported_actual_sqlite_is_rejected_before_any_database_is_created(self):
        import sqlite3

        with (
            patch.object(sqlite3, "sqlite_version", "0.0.0"),
            patch.object(rehearsal.tempfile, "TemporaryDirectory") as directory,
        ):
            with self.assertRaisesRegex(RuntimeError, "actual linked SQLite"):
                rehearsal.sqlite_receipt(Path("not-a-selected-library"))
            directory.assert_not_called()
