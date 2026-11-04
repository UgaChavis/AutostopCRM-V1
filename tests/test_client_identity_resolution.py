"""Client aliases must never redirect a read or write to another client."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


class ClientIdentityResolutionTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="synthetic-client-identities-")
        self.addCleanup(self.app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        for client_id, name in (
            ("12345678-0000-0000-0000-000000000001", "AAA SYNTHETIC A"),
            ("CL-12345678", "BBB SYNTHETIC B"),
            ("abcdef12-0000-0000-0000-000000000001", "CCC SYNTHETIC C"),
            ("abcdef12-0000-0000-0000-000000000002", "DDD SYNTHETIC D"),
            ("88888888-0000-0000-0000-000000000001", "EEE SYNTHETIC E"),
        ):
            self.service.create_client({"id": client_id, "display_name": name})
        self.card_id = self.service.create_card({"title": "SYNTHETIC CARD"})["card"]["id"]

    def test_exact_id_precedes_another_clients_short_alias_for_read_and_update(self):
        requested = "CL-12345678"
        self.assertEqual(
            self.service.get_client({"client_id": requested})["client"]["id"], requested
        )
        updated = self.service.update_client({"client_id": requested, "comment": "SYNTHETIC"})
        self.assertEqual(updated["client"]["id"], requested)
        clients = {client.id: client for client in self.store.read_bundle()["clients"]}
        self.assertEqual(clients[requested].comment, "SYNTHETIC")
        self.assertEqual(clients["12345678-0000-0000-0000-000000000001"].comment, "")

    def test_exact_id_delete_does_not_delete_a_client_with_the_same_alias(self):
        deleted = self.service.delete_client({"client_id": "CL-12345678"})
        self.assertEqual(deleted["client"]["id"], "CL-12345678")
        clients = {client.id for client in self.store.read_bundle()["clients"]}
        self.assertIn("12345678-0000-0000-0000-000000000001", clients)
        self.assertNotIn("CL-12345678", clients)

    def test_unique_short_alias_is_case_insensitive_and_unknown_is_not_found(self):
        result = self.service.get_client({"client_id": "cl-88888888"})
        self.assertEqual(result["client"]["id"], "88888888-0000-0000-0000-000000000001")
        for requested in ("CL-NOTFOUND", "ABCDEF12-0000-0000-0000-000000000001"):
            with self.subTest(requested=requested), self.assertRaises(ServiceError) as failure:
                self.service.get_client({"client_id": requested})
            self.assertEqual(failure.exception.code, "not_found")

    def test_case_insensitive_full_id_and_another_short_alias_are_ambiguous(self):
        before = self.state_file.read_bytes()
        for operation in (self.service.get_client, self.service.update_client):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ServiceError) as failure:
                    operation({"client_id": "cl-12345678", "comment": "MUST NOT SAVE"})
                self.assertEqual(failure.exception.code, "client_ambiguous")
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_client_id_length_limit_is_not_truncated_into_an_existing_client(self):
        client_id = "x" * 128
        self.service.create_client({"id": client_id, "display_name": "SYNTHETIC LIMIT"})
        self.assertEqual(
            self.service.get_client({"client_id": client_id})["client"]["id"], client_id
        )
        before = self.state_file.read_bytes()
        for operation, payload in (
            (self.service.get_client, {"client_id": client_id + "x"}),
            (self.service.get_client_stats, {"client_id": client_id + "x"}),
            (
                self.service.update_client,
                {"client_id": client_id + "x", "comment": "MUST NOT SAVE"},
            ),
            (self.service.delete_client, {"client_id": client_id + "x"}),
            (self.service.create_card, {"client_id": client_id + "x", "title": "MUST NOT CREATE"}),
            (
                self.service.link_card_to_client,
                {"client_id": client_id + "x", "card_id": self.card_id},
            ),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ServiceError) as failure:
                    operation(payload)
                self.assertEqual(failure.exception.code, "validation_error")
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_ambiguous_existing_card_link_fails_closed_without_writes(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["client_id"] = "CL-ABCDEF12"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self.store.read_bundle()
        before = self.state_file.read_bytes()
        for operation, payload, error_type in (
            (
                self.service.get_card_context,
                {"card_id": self.card_id, "read_only": True},
                StateFileCorruptedError,
            ),
            (self.service.get_completion_act_form, {"card_id": self.card_id}, ServiceError),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(error_type) as failure:
                    operation(payload)
                if error_type is ServiceError:
                    self.assertEqual(failure.exception.code, "client_ambiguous")
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_ambiguous_short_alias_refuses_reads_and_writes_without_changing_state(self):
        alias = "cl-abcdef12"

        def files():
            result = {}
            for label, root in (
                ("state", Path(self.temp_dir.name)),
                ("app", Path(self.app_data.name)),
            ):
                for path in root.rglob("*"):
                    stat = path.stat()
                    result[(label, str(path.relative_to(root)))] = (
                        stat.st_ino,
                        stat.st_mode,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                        path.read_bytes() if path.is_file() else None,
                    )
            return result

        before = files()
        for operation, payload in (
            (self.service.get_client, {"client_id": alias}),
            (self.service.get_client_stats, {"client_id": alias}),
            (self.service.update_client, {"client_id": alias, "comment": "MUST NOT SAVE"}),
            (self.service.delete_client, {"client_id": alias}),
            (self.service.create_card, {"client_id": alias, "title": "MUST NOT CREATE"}),
            (self.service.link_card_to_client, {"client_id": alias, "card_id": self.card_id}),
            (
                self.service.upsert_client_vehicle,
                {"client_id": alias, "vehicle": {"vehicle": "MUST NOT SAVE"}},
            ),
            (self.service.delete_client_vehicle, {"client_id": alias, "client_vehicle_id": "v"}),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ServiceError) as failure:
                    operation(payload)
                self.assertEqual(failure.exception.code, "client_ambiguous")
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(files(), before)
