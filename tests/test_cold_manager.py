"""A cold embedded process cannot select an installed or preloaded Manager."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from minimal_kanban import cold_manager as cold


class ColdManagerTests(unittest.TestCase):
    def test_legacy_absence_is_explicit_and_does_not_import_manager(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                cold.importlib, "import_module", side_effect=AssertionError("unexpected import")
            ),
        ):
            self.assertFalse(cold.prepare_cold_manager())

    def test_partial_binding_cannot_fall_back(self):
        for key in (cold.BINDING_ENV, cold.HASH_ENV):
            with self.subTest(key=key), patch.dict(os.environ, {key: "present"}, clear=True):
                with self.assertRaises(cold.ColdManagerUnavailableError):
                    cold.prepare_cold_manager()

    def test_cold_source_without_manifest_is_rejected_before_application_imports(self):
        with patch.dict(
            os.environ,
            {"AUTOSTOP_MANAGER_PATH": str(cold.SOURCE_PARENT / ("1" * 40) / "source")},
            clear=True,
        ):
            with self.assertRaises(cold.ColdManagerUnavailableError):
                cold.prepare_cold_manager()

    def test_missing_explicit_source_cannot_fall_back(self):
        environment = {
            cold.BINDING_ENV: "/missing/binding.json",
            cold.HASH_ENV: "a" * 64,
            "AUTOSTOP_MANAGER_PATH": "/opt/AutostopManager",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                cold.importlib, "import_module", side_effect=AssertionError("unexpected import")
            ),
        ):
            with self.assertRaises(cold.ColdManagerUnavailableError):
                cold.prepare_cold_manager()

    def fixture(self, folder):
        source = folder / ("1" * 40) / "source"
        source.mkdir(parents=True)
        (source / "REVISION").write_text("1" * 40 + "\n")
        manifest = folder / "binding.json"
        document = {
            "source_revision": "1" * 40,
            "role": "crm-embedded",
            "paths": {"source_root": str(source)},
        }
        raw = json.dumps(document).encode()
        manifest.write_bytes(raw)
        environment = {
            cold.BINDING_ENV: str(manifest),
            cold.HASH_ENV: hashlib.sha256(raw).hexdigest(),
            "AUTOSTOP_MANAGER_PATH": str(source),
        }
        return source, document, environment

    def test_sealed_source_calls_independent_manager_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source, document, environment = self.fixture(folder)
            roles = []
            runtime = types.SimpleNamespace(
                __file__=str(source / "autostop_manager/cold_runtime.py"),
                binding=lambda: types.SimpleNamespace(document=document),
                require_role=roles.append,
            )
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(cold, "SOURCE_PARENT", folder),
                patch.object(cold, "_protected"),
                patch.object(cold.importlib, "import_module", return_value=runtime),
                patch.object(sys, "path", list(sys.path)),
            ):
                self.assertTrue(cold.prepare_cold_manager())
                self.assertEqual(["crm-embedded"], roles)
                self.assertEqual(str(source), sys.path[0])

    def test_preloaded_legacy_module_and_manifest_drift_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            _source, _document, environment = self.fixture(folder)
            for failure in ("preloaded", "hash"):
                current = dict(environment)
                modules = {}
                if failure == "hash":
                    current[cold.HASH_ENV] = "b" * 64
                else:
                    modules["autostop_manager"] = types.SimpleNamespace(
                        __file__="/opt/AutostopManager/autostop_manager/__init__.py"
                    )
                with (
                    self.subTest(failure=failure),
                    patch.dict(os.environ, current, clear=True),
                    patch.dict(sys.modules, modules),
                    patch.object(cold, "SOURCE_PARENT", folder),
                    patch.object(cold, "_protected"),
                    patch.object(
                        cold.importlib, "import_module", side_effect=AssertionError("unsafe import")
                    ),
                ):
                    with self.assertRaises(cold.ColdManagerUnavailableError):
                        cold.prepare_cold_manager()


if __name__ == "__main__":
    unittest.main()
