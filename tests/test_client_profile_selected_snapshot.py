"""A profile snapshot must match full normalization for every client selection."""

# The fixture selects the checkout before application imports.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


A = "12345678-0000-0000-0000-000000000001"
B = "CL-12345678"
C = "87654321-0000-0000-0000-000000000003"


class SelectedClientSnapshotTests(CardServiceCase):
    def setUp(self):
        app_data = tempfile.TemporaryDirectory(prefix="synthetic-selected-client-")
        self.addCleanup(app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.addCleanup(self.store.close)
        for client_id in (A, B, C):
            self.service.create_client({"id": client_id, "display_name": f"Synthetic {client_id}"})
        self.service.upsert_client_vehicle(
            {"client_id": A, "vehicle": {"id": "vehicle-one", "vehicle": "Synthetic car"}}
        )
        self.service.create_card(
            {"title": "Synthetic card", "client_id": A, "client_vehicle_id": "vehicle-one"}
        )
        self.original = json.loads(self.state_file.read_text(encoding="utf-8"))

    def capture(self, operation, payload, *, full):
        read = self.store._read_context_bundle_snapshot
        with (
            patch.object(
                self.store,
                "_read_context_bundle_snapshot",
                side_effect=lambda **_: read(domains=("clients",)),
            )
            if full
            else patch.object(self.store, "_read_context_bundle_snapshot", wraps=read)
        ):
            try:
                return ("ok", operation(payload))
            except ServiceError as exc:
                return ("service_error", exc.code, exc.status_code, exc.details)
            except StateFileCorruptedError as exc:
                return ("state_error", str(exc))

    def install(self, state):
        temporary = self.state_file.with_name("synthetic-client-swap.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_file)

    def test_exact_short_invalid_and_fallback_match_full_snapshot(self):
        cases = (
            ("exact", B),
            ("short", "cl-87654321"),
            ("ambiguous", "cl-12345678"),
            ("unknown", "CL-DOESNOTEXIST"),
            ("too_long", "x" * 129),
            ("bad_exact", B),
            ("duplicate", A),
            ("missing", A),
            ("noncanonical", A),
            ("bad_other", A),
        )
        for scenario, requested in cases:
            with self.subTest(scenario=scenario):
                state = deepcopy(self.original)
                clients = state["clients"]
                target = next(row for row in clients if row["id"] == B)
                if scenario == "bad_exact":
                    target["phones"] = 123
                elif scenario == "duplicate":
                    clients.append(deepcopy(target))
                elif scenario == "missing":
                    target.pop("id")
                elif scenario == "noncanonical":
                    target["id"] = f" {B} "
                elif scenario == "bad_other":
                    target["phones"] = 123
                self.install(state)
                before = self.state_file.read_bytes()
                for operation in (self.service.get_client, self.service.get_client_stats):
                    for strict in (
                        (False, True) if operation == self.service.get_client else (False,)
                    ):
                        payload = {"client_id": requested, "strict_client_links": strict}
                        self.assertEqual(
                            self.capture(operation, payload, full=True),
                            self.capture(operation, payload, full=False),
                        )
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_cached_profile_switches_clients_without_changing_other_snapshots(self):
        before = self.state_file.read_bytes()
        for client_id in (A, C, B, A):
            payload = {"client_id": client_id, "strict_client_links": True}
            self.assertEqual(
                self.capture(self.service.get_client, payload, full=True),
                self.capture(self.service.get_client, payload, full=False),
            )
        self.assertEqual(self.state_file.read_bytes(), before)
