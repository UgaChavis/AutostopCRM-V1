from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.settings_models import IntegrationSettings  # noqa: E402
from minimal_kanban.settings_service import (  # noqa: E402
    ConnectionCheckResult,
    ConnectionTestSummary,
    SettingsService,
)
from minimal_kanban.settings_store import SettingsStore  # noqa: E402
from minimal_kanban.storage.file_lock import ProcessFileLock  # noqa: E402


class SettingsWriteIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "settings.json"
        self.logger = logging.getLogger(__name__)
        self.store = SettingsStore(self.path, self.logger)
        self.service = SettingsService(self.store, self.logger)

    def test_initialization_rechecks_file_after_waiting_for_peer(self):
        desired = replace(
            IntegrationSettings.defaults(),
            local_api=replace(IntegrationSettings.defaults().local_api, local_api_port=44001),
        )
        self.path.unlink()
        waiting = threading.Event()
        errors = []
        acquire = ProcessFileLock.acquire

        @contextmanager
        def observed_acquire(lock):
            waiting.set()
            with acquire(lock):
                yield

        def initialize_peer():
            try:
                SettingsStore(self.path, self.logger)
            except Exception as exc:
                errors.append(exc)

        with self.store._process_lock.acquire():
            with patch.object(ProcessFileLock, "acquire", new=observed_acquire):
                worker = threading.Thread(target=initialize_peer, daemon=True)
                worker.start()
                self.assertTrue(waiting.wait(timeout=2))
                self.store._write_settings(desired)
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.store.read(), desired)

    def test_read_io_error_does_not_quarantine_valid_settings(self):
        desired = replace(
            IntegrationSettings.defaults(),
            local_api=replace(IntegrationSettings.defaults().local_api, local_api_port=44002),
        )
        self.service.save(desired)
        before = self.path.read_bytes()
        with (
            patch.object(self.store, "_read_settings_text", side_effect=PermissionError("busy")),
            self.assertRaises(PermissionError),
        ):
            self.service.load()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.corrupted*.json")), [])
        self.assertEqual(self.service.load(), desired)

    def test_load_normalization_cannot_overwrite_peer_save(self):
        legacy = IntegrationSettings.defaults().to_dict()
        legacy["local_api"]["local_api_bearer_token"] = "synthetic-legacy-token"
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        peer_store = SettingsStore(self.path, self.logger)
        peer_service = SettingsService(peer_store, self.logger)
        peer_settings = IntegrationSettings.from_dict(legacy)
        peer_settings = replace(
            peer_settings, local_api=replace(peer_settings.local_api, local_api_port=44003)
        )
        normalize = self.service.normalize
        committed = threading.Event()
        errors = []
        workers = []

        def save_peer():
            try:
                peer_service.save(peer_settings)
                committed.set()
            except Exception as exc:
                errors.append(exc)

        def normalize_while_peer_saves(settings):
            worker = threading.Thread(target=save_peer, daemon=True)
            workers.append(worker)
            worker.start()
            # Old load released its lock before normalization, so the peer
            # commits here and is then overwritten. Atomic load blocks the peer
            # until its own normalization commits, after which the peer wins.
            committed.wait(timeout=0.3)
            return normalize(settings)

        with patch.object(self.service, "normalize", side_effect=normalize_while_peer_saves):
            self.service.load()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(committed.is_set())
        self.assertEqual(peer_service.load().local_api.local_api_port, 44003)
        self.assertEqual(self.store.read().auth.local_api_bearer_token, "synthetic-legacy-token")

    def test_section_token_updates_can_rotate_and_clear_both_aliases(self):
        cases = (
            ("local_api", "local_api_bearer_token"),
            ("mcp", "mcp_bearer_token"),
            ("auth", "local_api_bearer_token"),
            ("auth", "mcp_bearer_token"),
        )
        for section, field in cases:
            with self.subTest(section=section, field=field):
                for token in ("synthetic-old-token", "synthetic-new-token", ""):
                    self.service.update_section(section, {field: token}, persist=True)
                    loaded = self.service.load()
                    self.assertEqual(getattr(loaded.auth, field), token)
                    alias = loaded.local_api if field.startswith("local_api") else loaded.mcp
                    self.assertEqual(getattr(alias, field), token)

    def test_normalizer_failure_preserves_file_and_releases_locks(self):
        before = self.path.read_bytes()
        with (
            patch.object(self.service, "normalize", side_effect=ValueError("normalization failed")),
            self.assertRaisesRegex(ValueError, "normalization failed"),
        ):
            self.service.load()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(SettingsStore(self.path, self.logger).read(), self.service.load())

    def test_parallel_section_updates_preserve_both_changes(self):
        peer = SettingsService(SettingsStore(self.path, self.logger), self.logger)
        validated = self.service._validated_settings
        committed = threading.Event()
        errors = []
        workers = []

        def save_peer():
            try:
                peer.update_section("general", {"test_mode": False}, persist=True)
                committed.set()
            except Exception as exc:
                errors.append(exc)

        def validate_while_peer_saves(settings):
            worker = threading.Thread(target=save_peer, daemon=True)
            workers.append(worker)
            worker.start()
            committed.wait(timeout=0.3)
            return validated(settings)

        with patch.object(
            self.service, "_validated_settings", side_effect=validate_while_peer_saves
        ):
            self.service.update_section("openai", {"model": "synthetic-model"}, persist=True)
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        loaded = peer.load()
        self.assertFalse(loaded.general.test_mode)
        self.assertEqual(loaded.openai.model, "synthetic-model")

    def test_explicit_settings_draft_keeps_unrelated_edits(self):
        current = self.service.load()
        draft = replace(current, general=replace(current.general, test_mode=False))
        result = self.service.update_section(
            "openai", {"model": "synthetic-draft"}, settings=draft, persist=True
        )
        self.assertFalse(result.general.test_mode)
        self.assertEqual(self.service.load(), result)

    def test_late_diagnostics_keep_new_configuration_and_do_not_report_stale_success(self):
        tested = self.service.load()
        self.service.update_section("local_api", {"local_api_port": 44345}, persist=True)
        result = ConnectionCheckResult("local_api", "success", "Old endpoint OK")
        saved = self.service.apply_test_result(tested, "local_api", result, persist=True)
        self.assertEqual(saved.local_api.local_api_port, 44345)
        self.assertEqual(saved.diagnostics.local_api_status, "warning")
        self.assertIn("изменились", saved.diagnostics.local_api_message)

        summary = ConnectionTestSummary(
            "synthetic-time", result, result, result, result, "success", (), ()
        )
        saved = self.service.apply_test_summary(tested, summary, persist=True)
        self.assertEqual(saved.local_api.local_api_port, 44345)
        self.assertEqual(saved.diagnostics.overall_status, "warning")
        for target in ("local_api", "mcp", "external", "openai"):
            self.assertEqual(getattr(saved.diagnostics, f"{target}_status"), "warning")

    def test_independent_diagnostic_results_are_merged_without_false_staleness(self):
        tested = self.service.load()
        self.service.apply_test_result(
            tested, "mcp", ConnectionCheckResult("mcp", "success", "MCP OK"), persist=True
        )
        saved = self.service.apply_test_result(
            tested,
            "local_api",
            ConnectionCheckResult("local_api", "success", "API OK"),
            persist=True,
        )
        self.assertEqual(saved.diagnostics.local_api_status, "success")
        self.assertEqual(saved.diagnostics.mcp_status, "success")
