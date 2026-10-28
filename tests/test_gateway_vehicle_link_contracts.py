from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import test_agent_gateway_v2 as gateway_fixtures

FakeBoardApi = gateway_fixtures.FakeBoardApi


class GatewayVehicleLinkContractTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only fixture methods, so the original test class is not rediscovered.
    setUp = gateway_fixtures.AgentGatewayV2Tests.setUp
    _create_store_server = gateway_fixtures.AgentGatewayV2Tests._create_store_server

    async def _manager_raw_call(self, server, name, arguments, idempotency_key):
        discovered = await server._tool_manager.get_tool("discover_raw_capabilities").run(
            {"query": name, "limit": 10}, convert_result=False
        )
        selected = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == name
        )
        return await server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": name,
                "arguments": arguments,
                "schema_hash": selected["schema_hash"],
                "idempotency_key": idempotency_key,
            },
            convert_result=False,
        )

    async def test_created_card_profile_drift_is_unverified_and_replay_does_not_duplicate(self):
        class DriftBoard(FakeBoardApi):
            def get_card(self, card_id):
                result = super().get_card(card_id)
                result["data"]["card"]["vehicle_profile"] = {"engine_model": "other engine"}
                return result

        board = DriftBoard()
        server, state = self._create_store_server(board_api=board)
        arguments = {
            "title": "Synthetic preliminary request",
            "vehicle": "AutoStop Synthetic",
            "description": "Preliminary service request, client agreed; specification pending.",
            "vehicle_profile": {"engine_model": "synthetic engine"},
        }
        for attempt in range(2):
            result = await self._manager_raw_call(
                server, "create_card", arguments, "raw-create-card-profile-drift"
            )
            self.assertFalse(result.structuredContent["ok"])
            if attempt == 0:
                self.assertFalse(result.structuredContent["verification"]["passed"])
            else:
                self.assertTrue(result.structuredContent["verification"]["idempotency_reused"])
        self.assertEqual(len(board.created_cards), 1)
        self.assertEqual(len(state["workflow_runs"]), 1)

    async def test_explicit_vehicle_link_requires_membership_in_exact_client(self):
        for vehicles, passed in (
            ([{"id": "vehicle-synthetic-1"}], True),
            ([{"id": "vehicle-other-client"}], False),
            ([{"id": "vehicle-synthetic-1"}, {"id": "vehicle-synthetic-1"}], False),
        ):
            with self.subTest(vehicles=vehicles):
                board = FakeBoardApi()
                client = board.create_client({"display_name": "Synthetic", "vehicles": vehicles})[
                    "data"
                ]["client"]
                card = board.create_card(title="Synthetic request")["data"]["card"]
                server, _ = self._create_store_server(board_api=board)
                result = await self._manager_raw_call(
                    server,
                    "link_card_to_client",
                    {
                        "card_id": card["id"],
                        "client_id": client["id"],
                        "client_vehicle_id": "vehicle-synthetic-1",
                        "expected_card_updated_at": card["updated_at"],
                        "expected_client_updated_at": client["updated_at"],
                        "sync_fields": False,
                    },
                    "raw-link-vehicle-membership",
                )
                self.assertEqual(result.structuredContent["ok"], passed)
                self.assertEqual(
                    result.structuredContent["verification"]["evidence"]["vehicle_link_exact"],
                    passed,
                )

    async def test_real_vehicle_link_rejects_ambiguity_and_verifies_canonical_alias(self):
        from minimal_kanban.services.card_service import CardService, ServiceError
        from minimal_kanban.storage.json_store import JsonStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CardService(
                JsonStore(state_file=root / "state.json", logger=self.logger),
                self.logger,
                attachments_dir=root / "attachments",
                repair_orders_dir=root / "repair-orders",
            )
            vehicle_ids = (
                "11111111-1111-4111-8111-111111111111",
                "11111111-2222-4222-8222-222222222222",
                "22222222-1111-4111-8111-111111111111",
            )
            client = service.create_client(
                {
                    "display_name": "Synthetic identity client",
                    "vehicles": [
                        {"id": identifier, "vehicle": f"Synthetic vehicle {index}"}
                        for index, identifier in enumerate(vehicle_ids)
                    ],
                }
            )["client"]

            class RealBoard(FakeBoardApi):
                def __init__(self):
                    super().__init__()
                    self.persisted_links = 0

                def get_card(self, card_id):
                    return {"ok": True, "data": service.get_card({"card_id": card_id})}

                def get_client(self, client_id, **_kwargs):
                    return {"ok": True, "data": service.get_client({"client_id": client_id})}

                def link_card_to_client(self, card_id, client_id, **kwargs):
                    try:
                        data = service.link_card_to_client(
                            {"card_id": card_id, "client_id": client_id, **kwargs}
                        )
                    except ServiceError as error:
                        return {
                            "ok": False,
                            "error": {"code": error.code, "message": error.message},
                        }
                    self.persisted_links += 1
                    return {"ok": True, "data": data}

            board = RealBoard()
            server, state = self._create_store_server(board_api=board)
            for index, (identifier, canonical) in enumerate(
                (
                    ("CV-11111111", None),
                    ("cv-22222222", vehicle_ids[2]),
                    (vehicle_ids[1], vehicle_ids[1]),
                )
            ):
                with self.subTest(identifier=identifier):
                    card = service.create_card(
                        {"title": "Synthetic preserved request", "vehicle": "Before"}
                    )["card"]
                    arguments = {
                        "card_id": card["id"],
                        "client_id": client["id"],
                        "client_vehicle_id": identifier,
                        "expected_card_updated_at": card["updated_at"],
                        "expected_client_updated_at": client["updated_at"],
                        "sync_fields": False,
                    }
                    baseline = {path.name: path.read_bytes() for path in root.glob("*.json")}
                    key = f"raw-real-vehicle-identity-{index}"
                    result = (
                        await self._manager_raw_call(server, "link_card_to_client", arguments, key)
                    ).structuredContent
                    self.assertEqual(result["ok"], canonical is not None)
                    if canonical is None:
                        self.assertEqual(
                            {path.name: path.read_bytes() for path in root.glob("*.json")}, baseline
                        )
                        self.assertEqual(
                            service.get_card({"card_id": card["id"]})["card"]["client_vehicle_id"],
                            "",
                        )
                        self.assertEqual(board.persisted_links, 0)
                        continue
                    self.assertEqual(result["status"], "completed")
                    self.assertTrue(result["verification"]["evidence"]["vehicle_link_exact"])
                    self.assertEqual(
                        service.get_card({"card_id": card["id"]})["card"]["client_vehicle_id"],
                        canonical,
                    )
                    replay = (
                        await self._manager_raw_call(server, "link_card_to_client", arguments, key)
                    ).structuredContent
                    self.assertTrue(replay["ok"])
                    self.assertTrue(replay["verification"]["idempotency_reused"])
                    self.assertEqual(board.persisted_links, index)
            self.assertEqual(len(state["workflow_runs"]), 3)
