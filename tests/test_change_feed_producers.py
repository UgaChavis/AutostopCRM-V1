from __future__ import annotations

import base64
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.shared_files_service import SharedFilesService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ChangeFeedProducerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.change_feed.producers.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(self.base_dir / "state.json", logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def events(self) -> list[dict]:
        return self.store.change_feed_store.raw_events_for_test()

    def events_after(self, sequence: int) -> list[dict]:
        return [event for event in self.events() if event["sequence"] > sequence]

    def high_water(self) -> int:
        rows = self.events()
        return rows[-1]["sequence"] if rows else 0

    def assert_single_entity_change(
        self,
        events: list[dict],
        *,
        entity_type: str,
        change_type: str,
        tombstone: bool = False,
    ) -> dict:
        matches = [
            event
            for event in events
            if event["entity_type"] == entity_type and event["change_type"] == change_type
        ]
        self.assertEqual(1, len(matches), matches)
        self.assertIs(matches[0]["tombstone"], tombstone)
        return matches[0]

    def feed_database_bytes(self) -> bytes:
        return b"".join(
            path.read_bytes()
            for path in sorted(self.base_dir.glob("change_feed.sqlite3*"))
            if path.is_file()
        )

    def test_repair_order_children_emit_exact_commit_bound_create_update_delete(self) -> None:
        private_work = "PRIVATE-WORK-NAME-7C17"
        private_material = "PRIVATE-MATERIAL-NAME-91E2"
        private_payment_note = "PRIVATE-PAYMENT-NOTE-A128"
        card_id = self.service.create_card(
            {"vehicle": "PRIVATE-VEHICLE-93A0", "title": "Producer coverage"}
        )["card"]["id"]

        for route, entity_type, private_name in (
            (self.service.replace_repair_order_works, "repair_order_work", private_work),
            (
                self.service.replace_repair_order_materials,
                "repair_order_material",
                private_material,
            ),
        ):
            before = self.high_water()
            route(
                {
                    "card_id": card_id,
                    "rows": [{"name": private_name, "quantity": "1", "price": "1000"}],
                }
            )
            created_events = self.events_after(before)
            created = self.assert_single_entity_change(
                created_events, entity_type=entity_type, change_type="create"
            )
            self.assertEqual("state_projection", created["producer"])
            self.assertEqual(1, len({event["correlation_ref"] for event in created_events}))
            self.assertEqual(1, len({event["idempotency_ref"] for event in created_events}))

            before = self.high_water()
            route(
                {
                    "card_id": card_id,
                    "rows": [{"name": private_name, "quantity": "2", "price": "1000"}],
                }
            )
            self.assert_single_entity_change(
                self.events_after(before), entity_type=entity_type, change_type="update"
            )

            before = self.high_water()
            route({"card_id": card_id, "rows": []})
            self.assert_single_entity_change(
                self.events_after(before),
                entity_type=entity_type,
                change_type="delete",
                tombstone=True,
            )

        payment = {
            "id": "payment-contract-1",
            "amount": "2500",
            "paid_at": "21.07.2026",
            "note": private_payment_note,
            "payment_method": "cash",
        }
        before = self.high_water()
        self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"payments": [payment]}}
        )
        created_payment = self.assert_single_entity_change(
            self.events_after(before),
            entity_type="repair_order_payment",
            change_type="create",
        )
        self.assertEqual(f"{card_id}:payment:payment-contract-1", created_payment["entity_id"])

        before = self.high_water()
        self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {"payments": [{**payment, "amount": "2600"}]},
            }
        )
        self.assert_single_entity_change(
            self.events_after(before),
            entity_type="repair_order_payment",
            change_type="update",
        )

        before = self.high_water()
        self.service.update_repair_order({"card_id": card_id, "repair_order": {"payments": []}})
        self.assert_single_entity_change(
            self.events_after(before),
            entity_type="repair_order_payment",
            change_type="delete",
            tombstone=True,
        )

        database = self.feed_database_bytes()
        for private_value in (
            private_work,
            private_material,
            private_payment_note,
            "PRIVATE-VEHICLE-93A0",
        ):
            self.assertNotIn(private_value.encode(), database)

    def test_attachment_and_client_vehicle_children_use_parent_scoped_ids_and_tombstones(
        self,
    ) -> None:
        card_id = self.service.create_card({"title": "Scoped child producers"})["card"]["id"]
        before = self.high_water()
        attachment = self.service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "PRIVATE-ATTACHMENT-2A19.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"PRIVATE-ATTACHMENT-BODY-91C0").decode(),
            }
        )["attachment"]
        created_attachment = self.assert_single_entity_change(
            self.events_after(before), entity_type="attachment", change_type="create"
        )
        self.assertEqual(
            f"{card_id}:attachment:{attachment['id']}", created_attachment["entity_id"]
        )

        before = self.high_water()
        self.service.remove_card_attachment({"card_id": card_id, "attachment_id": attachment["id"]})
        removed_events = self.events_after(before)
        projected_removals = [
            event
            for event in removed_events
            if event["entity_type"] == "attachment"
            and event["change_type"] == "delete"
            and event["producer"] == "state_projection"
        ]
        self.assertEqual(1, len(projected_removals), removed_events)
        removed_attachment = projected_removals[0]
        self.assertTrue(removed_attachment["tombstone"])
        self.assertEqual(1, len({event["correlation_ref"] for event in removed_events}))
        self.assertEqual(1, len({event["idempotency_ref"] for event in removed_events}))
        self.assertEqual(created_attachment["entity_id"], removed_attachment["entity_id"])

        before = self.high_water()
        client = self.service.create_client(
            {
                "display_name": "PRIVATE-CLIENT-8A31",
                "vehicles": [
                    {
                        "vehicle": "PRIVATE-VEHICLE-11C9",
                        "vin": "JN1TANT32U0012345",
                    }
                ],
            }
        )["client"]
        vehicle_id = client["vehicles"][0]["id"]
        created_vehicle = self.assert_single_entity_change(
            self.events_after(before), entity_type="client_vehicle", change_type="create"
        )
        self.assertEqual(f"{client['id']}:vehicle:{vehicle_id}", created_vehicle["entity_id"])

        before = self.high_water()
        self.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "vehicle": {
                    "vehicle": "PRIVATE-VEHICLE-UPDATED-3B82",
                    "vin": "JN1TANT32U0099999",
                },
            }
        )
        updated_vehicle_events = self.events_after(before)
        updated_vehicle = [
            event
            for event in updated_vehicle_events
            if event["entity_type"] == "client_vehicle"
            and event["change_type"] == "update"
            and event["producer"] == "state_projection"
        ]
        self.assertEqual(1, len(updated_vehicle), updated_vehicle_events)
        self.assertEqual(created_vehicle["entity_id"], updated_vehicle[0]["entity_id"])

        before = self.high_water()
        self.service.delete_client_vehicle(
            {"client_id": client["id"], "client_vehicle_id": vehicle_id}
        )
        deleted_vehicle_events = self.events_after(before)
        projected_deletions = [
            event
            for event in deleted_vehicle_events
            if event["entity_type"] == "client_vehicle"
            and event["change_type"] == "delete"
            and event["producer"] == "state_projection"
        ]
        self.assertEqual(1, len(projected_deletions), deleted_vehicle_events)
        deleted_vehicle = projected_deletions[0]
        self.assertTrue(deleted_vehicle["tombstone"])
        self.assertEqual(created_vehicle["entity_id"], deleted_vehicle["entity_id"])

        database = self.feed_database_bytes()
        for private_value in (
            "PRIVATE-ATTACHMENT-2A19",
            "PRIVATE-ATTACHMENT-BODY-91C0",
            "PRIVATE-CLIENT-8A31",
            "PRIVATE-VEHICLE-11C9",
            "PRIVATE-VEHICLE-UPDATED-3B82",
        ):
            self.assertNotIn(private_value.encode(), database)

    def test_failed_state_replace_aborts_outbox_and_next_commit_is_exact_and_gapless(self) -> None:
        card_id = self.service.create_card({"title": "Rollback producer"})["card"]["id"]
        before = self.high_water()
        with patch("pathlib.Path.replace", side_effect=OSError("simulated state replace failure")):
            with self.assertRaises(OSError):
                self.service.update_card({"card_id": card_id, "title": "MUST-NOT-COMMIT-PRIVATE"})

        self.assertEqual(before, self.high_water())
        self.assertFalse(self.store.change_feed_store.has_pending_state_write())
        self.assertNotIn(b"MUST-NOT-COMMIT-PRIVATE", self.feed_database_bytes())

        self.service.update_card({"card_id": card_id, "title": "Committed"})
        committed = self.events_after(before)
        self.assertEqual(
            list(range(before + 1, before + 1 + len(committed))),
            [event["sequence"] for event in committed],
        )
        self.assert_single_entity_change(committed, entity_type="card", change_type="update")

    def test_validation_error_and_notification_seen_state_have_correct_event_semantics(
        self,
    ) -> None:
        card_id = self.service.create_card({"title": "Notification state"})["card"]["id"]
        before_error = self.high_water()
        with self.assertRaises(ServiceError):
            self.service.replace_repair_order_works({"card_id": card_id, "rows": "invalid"})
        self.assertEqual(before_error, self.high_water())

        before_seen = self.high_water()
        self.service.mark_card_seen({"card_id": card_id, "actor_name": "OPERATOR-1"})
        seen_event = self.assert_single_entity_change(
            self.events_after(before_seen), entity_type="card", change_type="update"
        )
        self.assertEqual("state_projection", seen_event["producer"])
        self.assertNotIn(b"OPERATOR-1", self.feed_database_bytes())

    def test_employee_lifecycle_has_precise_create_update_delete_and_tombstone(self) -> None:
        before = self.high_water()
        employee = self.service.save_employee(
            {"name": "PRIVATE-EMPLOYEE-17C2", "position": "Mechanic"}
        )["employee"]
        created = self.assert_single_entity_change(
            self.events_after(before), entity_type="employee", change_type="create"
        )
        self.assertEqual(employee["id"], created["entity_id"])
        self.assertEqual("state_projection", created["producer"])

        before = self.high_water()
        self.service.save_employee(
            {
                "employee_id": employee["id"],
                "name": "PRIVATE-EMPLOYEE-17C2",
                "position": "Senior mechanic",
            }
        )
        self.assert_single_entity_change(
            self.events_after(before), entity_type="employee", change_type="update"
        )

        before = self.high_water()
        self.service.delete_employee({"employee_id": employee["id"]})
        deleted = self.assert_single_entity_change(
            self.events_after(before),
            entity_type="employee",
            change_type="delete",
            tombstone=True,
        )
        self.assertEqual(employee["id"], deleted["entity_id"])
        self.assertNotIn(b"PRIVATE-EMPLOYEE-17C2", self.feed_database_bytes())

    def test_state_lifecycle_covers_move_archive_restore_and_tombstone(self) -> None:
        card_id = self.service.create_card({"title": "Lifecycle matrix"})["card"]["id"]
        target_column = self.store.read_bundle()["columns"][1].id
        before = self.high_water()
        self.service.move_card({"card_id": card_id, "column": target_column, "position": 0})
        moved = self.assert_single_entity_change(
            self.events_after(before), entity_type="card", change_type="move"
        )
        self.assertEqual("card_moved", moved["action"])

        before = self.high_water()
        self.service.archive_card({"card_id": card_id})
        archived = self.assert_single_entity_change(
            self.events_after(before),
            entity_type="card",
            change_type="archive",
            tombstone=True,
        )
        self.assertEqual("card_archived", archived["action"])

        before = self.high_water()
        self.service.restore_card({"card_id": card_id})
        restored = self.assert_single_entity_change(
            self.events_after(before), entity_type="card", change_type="restore"
        )
        self.assertEqual("card_restored", restored["action"])

    def test_shared_file_projection_covers_create_update_move_delete_and_deferred_recovery(
        self,
    ) -> None:
        shared = SharedFilesService(
            storage_dir=self.base_dir / "shared-files",
            index_file=self.base_dir / "shared_files_index.json",
            logger=self.logger,
            change_feed_store=self.store.change_feed_store,
        )
        before = self.high_water()
        uploaded = shared.upload_shared_file(
            {
                "file_name": "PRIVATE-FILE-NAME-74D1.txt",
                "content_base64": base64.b64encode(b"PRIVATE-FILE-CONTENT-981A").decode(),
                "x": 10,
                "y": 20,
            }
        )["file"]
        created = self.assert_single_entity_change(
            self.events_after(before), entity_type="shared_file", change_type="create"
        )
        self.assertEqual("shared_files", created["producer"])

        before = self.high_water()
        with patch.object(
            self.store.change_feed_store,
            "reconcile_external_projection",
            side_effect=OSError("simulated feed outage after index commit"),
        ):
            shared.rename_shared_file(
                {"file_id": uploaded["id"], "file_name": "PRIVATE-RENAMED-8D91.txt"}
            )
        self.assertEqual(before, self.high_water())
        shared.reconcile_change_feed()
        self.assert_single_entity_change(
            self.events_after(before), entity_type="shared_file", change_type="update"
        )

        before = self.high_water()
        shared.update_shared_file_position({"file_id": uploaded["id"], "x": 30, "y": 40})
        self.assert_single_entity_change(
            self.events_after(before), entity_type="shared_file", change_type="move"
        )

        before = self.high_water()
        shared.delete_shared_file({"file_id": uploaded["id"]})
        self.assert_single_entity_change(
            self.events_after(before),
            entity_type="shared_file",
            change_type="delete",
            tombstone=True,
        )
        database = self.feed_database_bytes()
        for private_value in (
            "PRIVATE-FILE-NAME-74D1.txt",
            "PRIVATE-RENAMED-8D91.txt",
            "PRIVATE-FILE-CONTENT-981A",
        ):
            self.assertNotIn(private_value.encode(), database)

    def test_print_projection_covers_templates_settings_and_inspection_drafts(self) -> None:
        card_id = self.service.create_card({"title": "Print producer"})["card"]["id"]
        before = self.high_water()
        saved = self.service.save_print_template(
            {
                "document_type": "repair_order",
                "name": "PRIVATE-TEMPLATE-NAME-7D31",
                "content": "<div>PRIVATE-TEMPLATE-CONTENT-C128</div>",
            }
        )
        template_id = saved["template"]["id"]
        created = self.assert_single_entity_change(
            self.events_after(before), entity_type="print_template", change_type="create"
        )
        self.assertEqual("print_module", created["producer"])

        before = self.high_water()
        self.service.set_default_print_template(
            {"document_type": "repair_order", "template_id": template_id}
        )
        self.assert_single_entity_change(
            self.events_after(before), entity_type="print_settings", change_type="update"
        )

        before = self.high_water()
        self.service.save_inspection_sheet_form(
            {
                "card_id": card_id,
                "form_data": {"client": "PRIVATE-INSPECTION-CLIENT-409A"},
            }
        )
        self.assert_single_entity_change(
            self.events_after(before),
            entity_type="inspection_sheet_form",
            change_type="create",
        )

        before = self.high_water()
        self.service.delete_print_template({"template_id": template_id})
        self.assert_single_entity_change(
            self.events_after(before),
            entity_type="print_template",
            change_type="delete",
            tombstone=True,
        )
        database = self.feed_database_bytes()
        for private_value in (
            "PRIVATE-TEMPLATE-NAME-7D31",
            "PRIVATE-TEMPLATE-CONTENT-C128",
            "PRIVATE-INSPECTION-CLIENT-409A",
        ):
            self.assertNotIn(private_value.encode(), database)

    def test_print_command_audits_card_scope_and_exempts_manual_external_printer_scope(
        self,
    ) -> None:
        card_id = self.service.create_card({"title": "Printed card"})["card"]["id"]
        before = self.high_water()
        with patch("minimal_kanban.printing.service.print_html"):
            self.service.print_repair_order_documents(
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order"],
                    "printer_name": "Contract printer",
                }
            )
        card_print = [
            event
            for event in self.events_after(before)
            if event["action"] == "repair_order_printed"
        ]
        self.assertEqual(1, len(card_print), card_print)
        self.assertEqual("repair_order", card_print[0]["entity_type"])
        self.assertEqual(card_id, card_print[0]["entity_id"])
        self.assertEqual("audit_event", card_print[0]["producer"])

        before_manual = self.high_water()
        with patch("minimal_kanban.printing.service.print_html"):
            self.service.print_repair_order_documents(
                {
                    "document_without_card": True,
                    "manual_document": {
                        "document_number": "MANUAL-EXTERNAL-1",
                        "client": "PRIVATE-MANUAL-PRINT-CLIENT",
                    },
                    "selected_document_ids": ["repair_order"],
                    "printer_name": "Contract printer",
                }
            )
        self.assertEqual(before_manual, self.high_water())
        self.assertNotIn(b"PRIVATE-MANUAL-PRINT-CLIENT", self.feed_database_bytes())

    def test_operator_projection_excludes_sessions_but_tracks_user_lifecycle(self) -> None:
        users_file = self.base_dir / "users.json"
        environment = {
            "MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME": "feed-admin",
            "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD": "strong-feed-password-391A",
        }
        with patch.dict(os.environ, environment, clear=False):
            operators = OperatorAuthService(
                self.store,
                self.service,
                users_file=users_file,
                logger=self.logger,
            )
            before_login = self.high_water()
            login = operators.login(
                {"username": "feed-admin", "password": "strong-feed-password-391A"}
            )
            self.assertEqual(before_login, self.high_water())
            admin_session = login["session"]

            before = self.high_water()
            operators.save_user(
                {
                    "_operator_session": admin_session,
                    "username": "PRIVATE-OPERATOR-71B9",
                    "password": "private-password-912B",
                }
            )
            created = self.assert_single_entity_change(
                self.events_after(before), entity_type="operator_user", change_type="create"
            )
            self.assertEqual("operator_users", created["producer"])
            self.assertNotIn("PRIVATE-OPERATOR", created["entity_id"])

            before_logout = self.high_water()
            operators.logout({"_operator_session": admin_session})
            self.assertEqual(before_logout, self.high_water())

            login = operators.login(
                {"username": "feed-admin", "password": "strong-feed-password-391A"}
            )
            before = self.high_water()
            operators.delete_user(
                {
                    "_operator_session": login["session"],
                    "username": "PRIVATE-OPERATOR-71B9",
                }
            )
            self.assert_single_entity_change(
                self.events_after(before),
                entity_type="operator_user",
                change_type="delete",
                tombstone=True,
            )

        database = self.feed_database_bytes()
        for private_value in (
            "PRIVATE-OPERATOR-71B9",
            "private-password-912B",
            "strong-feed-password-391A",
        ):
            self.assertNotIn(private_value.encode(), database)

    def test_agent_runtime_routes_are_external_authority_and_do_not_forge_crm_events(self) -> None:
        class ExternalAgentControl:
            def __getattr__(self, name: str):
                def delegated(payload=None):
                    return {"delegated": name, "payload": payload or {}}

                return delegated

        self.service.attach_agent_control(ExternalAgentControl())
        before = self.high_water()
        calls = (
            self.service.agent_enqueue_task,
            self.service.save_agent_scheduled_task,
            self.service.delete_agent_scheduled_task,
            self.service.pause_agent_scheduled_task,
            self.service.resume_agent_scheduled_task,
            self.service.run_agent_scheduled_task,
        )
        for call in calls:
            self.assertIn("delegated", call({"technical_ref": "task-1"}))
        self.assertEqual(before, self.high_water())

    def test_artifact_preview_clipboard_and_retired_routes_do_not_forge_mutation_events(
        self,
    ) -> None:
        shared = SharedFilesService(
            storage_dir=self.base_dir / "nonmutating-shared-files",
            index_file=self.base_dir / "nonmutating_shared_files_index.json",
            logger=self.logger,
            change_feed_store=self.store.change_feed_store,
        )
        uploaded = shared.upload_shared_file(
            {
                "file_name": "artifact.txt",
                "content_base64": base64.b64encode(b"artifact").decode(),
            }
        )["file"]
        card_id = self.service.create_card({"title": "Artifact routes"})["card"]["id"]
        before = self.high_water()

        shared.copy_shared_file({"file_id": uploaded["id"]})
        self.service.preview_repair_order_print_documents(
            {"card_id": card_id, "selected_document_ids": ["repair_order"]}
        )
        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 producer-contract",
        ):
            self.service.export_repair_order_print_pdf(
                {"card_id": card_id, "selected_document_ids": ["repair_order"]}
            )
        with self.assertRaises(ServiceError) as retired:
            self.service.correct_repair_order_number({"card_id": card_id})
        self.assertEqual("repair_order_number_immutable", retired.exception.code)
        self.assertEqual(before, self.high_water())


if __name__ == "__main__":
    unittest.main()
