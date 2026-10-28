from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import http.client
import json
import os
import socket
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from tests.services_case import CardServiceCase
from minimal_kanban.api.route_registry import build_service_routes
from minimal_kanban.api.server import ApiServer, RequestContextFactory
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage import json_store
from minimal_kanban.storage.json_store import StateFileCorruptedError
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV


class ClientSearchReadSafetyTests(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        self.appdata = tempfile.TemporaryDirectory(prefix="synthetic-client-search-appdata-")
        self.addCleanup(self.appdata.cleanup)
        appdata = patch.dict("os.environ", {"APPDATA": self.appdata.name})
        appdata.start()
        self.addCleanup(appdata.stop)
        super().setUp()
        self.service = self._build_service()
        self.client_a = self.service.create_client(
            {"display_name": "SYNTHETIC_A", "vehicles": [{"vehicle": "SYNTHETIC_A_CAR"}]}
        )["client"]
        self.client_b = self.service.create_client(
            {"display_name": "SYNTHETIC_B", "vehicles": [{"vehicle": "SYNTHETIC_B_CAR"}]}
        )["client"]
        self.cards = []
        for title, client, vehicle in (
            ("SYNTHETIC_BOUND_A", self.client_a, "SYNTHETIC_A_CAR"),
            ("SYNTHETIC_BOUND_B", self.client_b, "SYNTHETIC_B_CAR"),
            ("SYNTHETIC_UNBOUND", None, "SYNTHETIC_INFERRED_CAR"),
        ):
            payload = {"title": title, "vehicle": vehicle}
            if client is not None:
                payload.update(
                    {"client_id": client["id"], "client_vehicle_id": client["vehicles"][0]["id"]}
                )
            card_id = self.service.create_card(payload)["card"]["id"]
            self.service.update_card(
                {"card_id": card_id, "repair_order": {"client": "SYNTHETIC_A"}}
            )
            self.cards.append(card_id)

        routes = build_service_routes(
            self.service, MagicMock(), paste_shared_files_from_clipboard=lambda _: {}
        )
        calls = self.calls = []

        class LocalBoard(FakeBoardApi):
            _request_readonly_query = BoardApiClient._request_readonly_query
            _client_search_readonly_request = BoardApiClient._client_search_readonly_request
            list_clients_readonly = BoardApiClient.list_clients_readonly
            search_clients_readonly = BoardApiClient.search_clients_readonly

            def _request(self, path, payload=None, *, method="POST"):
                parsed = urlsplit(path)
                request = (
                    RequestContextFactory.query_payload(parsed.query)
                    if method == "GET"
                    else dict(payload or {})
                )
                calls.append((parsed.path, method, request))
                if self.old_server and parsed.path.endswith("_readonly"):
                    return {
                        "ok": False,
                        "error": {
                            "code": "not_found",
                            "message": "Synthetic unknown route",
                            "details": {"path": parsed.path + "?<redacted>"},
                        },
                    }
                if self.missing_contract and parsed.path.endswith("_readonly"):
                    return {"ok": True, "data": {"clients": [], "meta": {}}}
                try:
                    return {"ok": True, "data": routes[parsed.path](request)}
                except ServiceError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": "synthetic"}}
                except StateFileCorruptedError:
                    return {"ok": False, "error": {"code": "state_file_corrupted"}}

        self.board = LocalBoard()
        self.board.old_server = False
        self.board.missing_contract = False
        env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        manager = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        manager.start()
        self.addCleanup(manager.stop)
        self.gateway = create_mcp_server(
            self.board, self.logger, host="127.0.0.1", port=41841, path="/mcp"
        )
        with patch.dict("os.environ", {"AUTOSTOP_AGENT_GATEWAY_ENABLED": "0"}):
            self.raw = create_mcp_server(
                self.board, self.logger, host="127.0.0.1", port=41841, path="/mcp"
            )

    def snapshot(self):
        result = {}
        for label, root in (
            ("state", Path(self.temp_dir.name)),
            ("appdata", Path(self.appdata.name)),
        ):
            for path in root.rglob("*"):
                stat = path.stat()
                key = (label, str(path.relative_to(root)))
                meta = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
                result[key] = (meta, path.read_bytes() if path.is_file() else None)
        return result

    def edit_state(self, edit):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        edit(state)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    async def call(self, name, arguments, *, raw=False):
        token = OwnerAccessToken(
            token="synthetic",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        ctx = auth_context_var.set(AuthenticatedUser(token))
        try:
            tool = (self.raw if raw else self.gateway)._tool_manager.get_tool(name)
            result = await tool.run(arguments, convert_result=False)
            return result.model_dump() if raw else result.structuredContent
        finally:
            auth_context_var.reset(ctx)

    async def call_hidden(self, name, arguments):
        schema = await self.call("get_raw_capability_schema", {"name": name})
        return await self.call(
            "call_raw_capability",
            {"name": name, "arguments": arguments, "schema_hash": schema["summary"]["schema_hash"]},
        )

    async def test_real_agent_raw_and_hidden_reads_do_not_repair_legacy_state(self):
        def make_legacy(state):
            state["settings"] = "SYNTHETIC_LEGACY"
            state["columns"].append({"bad": "SYNTHETIC_LEGACY"})

        self.edit_state(make_legacy)
        before = self.snapshot()
        for raw, name, arguments in (
            (False, "agent_search", {"entity": "client", "query": "SYNTHETIC_A"}),
            (True, "search_clients", {"query": "SYNTHETIC_A"}),
            (True, "list_clients", {"include_stats": True}),
            (True, "list_clients", {"include_stats": False}),
        ):
            with self.subTest(name=name, raw=raw, arguments=arguments):
                result = await self.call(name, arguments, raw=raw)
                self.assertTrue(result["ok"], result)
                self.assertEqual(before, self.snapshot())
        hidden = await self.call_hidden("search_clients", {"query": "SYNTHETIC_A"})
        self.assertTrue(hidden["ok"], hidden)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(all(path.endswith("_readonly") for path, _, _ in self.calls))
        for name in ("search_clients", "list_clients"):
            self.assertTrue(self.raw._tool_manager.get_tool(name).annotations.readOnlyHint)
        self.assertTrue(
            self.gateway._tool_manager.get_tool("agent_search").annotations.readOnlyHint
        )

    async def test_foreign_explicit_link_is_not_inferred_for_another_client(self):
        # The UI retains its historical heuristic behavior.
        ui_ids = {
            item["id"]
            for item in self.service.search_clients({"query": "SYNTHETIC_B_CAR"})["clients"]
        }
        self.assertIn(self.client_a["id"], ui_ids)
        before = self.snapshot()
        for raw, name, arguments in (
            (False, "agent_search", {"entity": "client", "query": "SYNTHETIC_B_CAR"}),
            (True, "search_clients", {"query": "SYNTHETIC_B_CAR"}),
        ):
            result = await self.call(name, arguments, raw=raw)
            self.assertTrue(result["ok"], result)
            rows = result["data"]["clients" if raw else "items"]
            self.assertEqual([row["id"] for row in rows], [self.client_b["id"]])
            self.assertEqual(before, self.snapshot())
        listed = await self.call("list_clients", {"limit": 10, "include_stats": True}, raw=True)
        client_a = next(
            row for row in listed["data"]["clients"] if row["id"] == self.client_a["id"]
        )
        self.assertEqual(client_a["stats"]["cards_total"], 1)
        self.assertNotIn(self.client_b["vehicles"][0]["id"], json.dumps(client_a))
        self.assertNotIn(self.cards[1], json.dumps(client_a))
        self.assertEqual(before, self.snapshot())

    async def test_unbound_card_is_a_marked_candidate_without_a_vehicle_id(self):
        before = self.snapshot()
        for raw, name, arguments in (
            (False, "agent_search", {"entity": "client", "query": "SYNTHETIC_INFERRED_CAR"}),
            (True, "search_clients", {"query": "SYNTHETIC_INFERRED_CAR"}),
        ):
            result = await self.call(name, arguments, raw=raw)
            self.assertTrue(result["ok"], result)
            rows = result["data"]["clients" if raw else "items"]
            row = next(item for item in rows if item["id"] == self.client_a["id"])
            self.assertIn("related_card_client_match_unverified", row["read_warnings"])
            candidate = next(
                item for item in row["related_card_candidates"] if item["card_id"] == self.cards[2]
            )
            self.assertEqual(candidate["client_relation"], "unverified")
            self.assertEqual(candidate["vehicle_relation"], "unverified")
            self.assertNotIn("id", candidate)
            self.assertEqual(row["stats"]["cards_total"], 1)
            self.assertEqual(before, self.snapshot())

    async def test_absent_new_route_or_missing_marker_fails_without_old_route_fallback(self):
        before = self.snapshot()
        for missing_contract in (False, True):
            self.board.old_server = not missing_contract
            self.board.missing_contract = missing_contract
            for raw, name, arguments in (
                (False, "agent_search", {"entity": "client", "query": "SYNTHETIC_A"}),
                (True, "search_clients", {"query": "SYNTHETIC_A"}),
                (True, "list_clients", {}),
            ):
                result = await self.call(name, arguments, raw=raw)
                self.assertFalse(result["ok"], result)
                expected = (
                    "client_read_safety_unverified"
                    if missing_contract
                    else "client_readonly_route_unavailable"
                )
                if raw:
                    self.assertEqual(result["error"]["code"], expected)
                else:
                    self.assertIn(expected, result["warnings"])
                self.assertEqual(before, self.snapshot())
        self.assertTrue(all(path.endswith("_readonly") for path, _, _ in self.calls))

    async def test_limits_and_has_more_reflect_actual_client_results(self):
        before = self.snapshot()
        gateway = await self.call("agent_search", {"entity": "client", "limit": 1})
        self.assertTrue(gateway["ok"])
        self.assertEqual(len(gateway["data"]["items"]), 1)
        self.assertTrue(gateway["page"]["has_more"])
        raw = await self.call("list_clients", {"limit": 1, "include_stats": True}, raw=True)
        self.assertTrue(raw["ok"])
        self.assertEqual(raw["data"]["meta"]["total"], 2)
        self.assertEqual(raw["data"]["meta"]["returned"], 1)
        self.assertTrue(raw["data"]["meta"]["has_more"])
        self.assertEqual(before, self.snapshot())

    async def test_gateway_limit_does_not_truncate_saved_vehicles_inside_a_result(self):
        self.service.update_client(
            {
                "client_id": self.client_a["id"],
                "vehicles": [self.client_a["vehicles"][0], {"vehicle": "SYNTHETIC_SECOND_CAR"}],
            }
        )
        before = self.snapshot()
        result = await self.call(
            "agent_search", {"entity": "client", "query": "SYNTHETIC_A", "limit": 1}
        )
        self.assertTrue(result["ok"], result)
        row = result["data"]["items"][0]
        self.assertEqual(row["id"], self.client_a["id"])
        self.assertEqual(len(row["vehicles_preview"]), 2)
        self.assertTrue(all(item["id"] for item in row["vehicles_preview"]))
        self.assertEqual(before, self.snapshot())

    async def test_gateway_fails_closed_if_payload_budget_loses_a_client_identity(self):
        original = self.service.search_clients_readonly({"query": "SYNTHETIC_A", "limit": 1})
        item = original["clients"][0]
        oversized = {
            "read_warnings": item["read_warnings"],
            "related_card_candidates": item["related_card_candidates"],
            "synthetic_filler": ["x" * 4000] * 30,
            **{
                key: value
                for key, value in item.items()
                if key not in {"read_warnings", "related_card_candidates"}
            },
        }
        original["clients"] = [oversized]
        self.board.search_clients_readonly = lambda **_: {"ok": True, "data": original}
        before = self.snapshot()
        result = await self.call(
            "agent_search", {"entity": "client", "query": "SYNTHETIC_A", "limit": 1}
        )
        self.assertFalse(result["ok"], result)
        self.assertIn("client_search_response_truncated", result["warnings"])
        self.assertEqual(result["data"]["items"], [])
        self.assertEqual(before, self.snapshot())

    async def test_backend_error_cannot_leak_a_client_in_gateway_or_raw_output(self):
        before = self.snapshot()
        unsafe_error = {
            "ok": False,
            "data": {"clients": [{"id": "SYNTHETIC_SHOULD_NOT_APPEAR"}]},
            "error": {"code": "synthetic_backend_error", "message": "synthetic"},
        }
        with patch.object(self.board, "_request", return_value=unsafe_error):
            gateway = await self.call("agent_search", {"entity": "client"})
            raw = await self.call("search_clients", {}, raw=True)
        self.assertFalse(gateway["ok"])
        self.assertEqual(gateway["data"]["items"], [])
        self.assertIn("synthetic_backend_error", gateway["warnings"])
        self.assertFalse(raw["ok"])
        self.assertNotIn("SYNTHETIC_SHOULD_NOT_APPEAR", json.dumps(raw))
        self.assertEqual(before, self.snapshot())

    async def test_query_matching_candidate_remains_visible_when_candidates_are_limited(self):
        for index in range(3):
            self.service.create_card(
                {
                    "title": f"SYNTHETIC_NEW_{index}",
                    "vehicle": f"SYNTHETIC_OTHER_CAR_{index}",
                    "vehicle_profile": {"customer_name": "SYNTHETIC_A"},
                }
            )
        before = self.snapshot()
        result = await self.call(
            "agent_search", {"entity": "client", "query": "SYNTHETIC_INFERRED_CAR"}
        )
        self.assertTrue(result["ok"], result)
        row = next(item for item in result["data"]["items"] if item["id"] == self.client_a["id"])
        self.assertGreater(row["related_card_candidates_total"], 2)
        self.assertIn(self.cards[2], [item["card_id"] for item in row["related_card_candidates"]])
        self.assertEqual(before, self.snapshot())

    async def test_invalid_client_identity_or_vehicle_link_fails_closed(self):
        original = json.loads(self.state_file.read_text())
        for corruption in (
            "duplicate_client",
            "missing_client_id",
            "normalized_client_id",
            "missing_vehicle_id",
            "foreign_vehicle",
            "duplicate_card_id",
        ):
            with self.subTest(corruption=corruption):
                state = json.loads(json.dumps(original))
                client = next(
                    item for item in state["clients"] if item["id"] == self.client_a["id"]
                )
                card = next(item for item in state["cards"] if item["id"] == self.cards[0])
                if corruption == "duplicate_client":
                    state["clients"].append(dict(client))
                elif corruption == "missing_client_id":
                    client.pop("id")
                elif corruption == "normalized_client_id":
                    client["id"] = " " + client["id"] + " "
                elif corruption == "missing_vehicle_id":
                    client["vehicles"][0].pop("id")
                elif corruption == "foreign_vehicle":
                    card["client_vehicle_id"] = self.client_b["vehicles"][0]["id"]
                else:
                    state["cards"].append(dict(card))
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for raw, name, args in (
                    (False, "agent_search", {"entity": "client", "query": "SYNTHETIC_A"}),
                    (True, "search_clients", {"query": "SYNTHETIC_A"}),
                ):
                    result = await self.call(name, args, raw=raw)
                    self.assertFalse(result["ok"], result)
                    if raw:
                        self.assertEqual(result["error"]["code"], "state_file_corrupted")
                    else:
                        self.assertIn("state_file_corrupted", result["warnings"])
                    self.assertEqual(before, self.snapshot())

    async def test_duplicate_client_beyond_limit_cannot_inflate_verified_totals(self):
        def duplicate_unselected(state):
            client_b = next(item for item in state["clients"] if item["id"] == self.client_b["id"])
            state["clients"].append(dict(client_b))

        self.edit_state(duplicate_unselected)
        before = self.snapshot()
        gateway = await self.call("agent_search", {"entity": "client", "limit": 1})
        raw = await self.call("list_clients", {"limit": 1}, raw=True)
        self.assertFalse(gateway["ok"])
        self.assertIn("state_file_corrupted", gateway["warnings"])
        self.assertEqual(gateway["data"]["items"], [])
        self.assertFalse(raw["ok"])
        self.assertEqual(raw["error"]["code"], "state_file_corrupted")
        self.assertEqual(before, self.snapshot())

    async def test_unparseable_raw_client_cannot_disappear_from_counts(self):
        original = json.loads(self.state_file.read_text())
        for raw_client in (
            {"display_name": "SYNTHETIC_DROPPED", "phones": 7},
            {"id": "SYNTHETIC_DROPPED_ID", "display_name": "SYNTHETIC_DROPPED", "phones": 7},
        ):
            with self.subTest(raw_client=raw_client):
                state = json.loads(json.dumps(original))
                state["clients"].append(raw_client)
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for raw, name, arguments in (
                    (False, "agent_search", {"entity": "client", "limit": 2}),
                    (True, "search_clients", {"limit": 2}),
                    (True, "list_clients", {"limit": 2, "include_stats": True}),
                    (True, "list_clients", {"limit": 2, "include_stats": False}),
                ):
                    result = await self.call(name, arguments, raw=raw)
                    self.assertFalse(result["ok"], result)
                    if raw:
                        self.assertEqual(result["error"]["code"], "state_file_corrupted")
                    else:
                        self.assertIn("state_file_corrupted", result["warnings"])
                        self.assertEqual(result["data"]["items"], [])
                    self.assertEqual(before, self.snapshot())
                hidden = await self.call_hidden("search_clients", {"limit": 2})
                self.assertFalse(hidden["ok"])
                self.assertIn("state_file_corrupted", json.dumps(hidden))
                self.assertEqual(before, self.snapshot())

    async def test_missing_raw_ids_fail_before_client_or_card_normalization(self):
        original = json.loads(self.state_file.read_text())
        for entity in ("client", "card"):
            with self.subTest(entity=entity):
                state = json.loads(json.dumps(original))
                target = state["clients"][0] if entity == "client" else state["cards"][0]
                target.pop("id")
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                target_method = (
                    "minimal_kanban.storage.readonly_context_snapshot.ClientProfile.from_dict"
                    if entity == "client"
                    else "minimal_kanban.storage.json_store.JsonStore._normalize_cards"
                )
                with patch(
                    target_method, side_effect=AssertionError("must not normalize")
                ) as method:
                    result = await self.call("search_clients", {}, raw=True)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "state_file_corrupted")
                method.assert_not_called()
                self.assertEqual(before, self.snapshot())

    async def test_noncanonical_raw_card_ids_fail_closed(self):
        original = json.loads(self.state_file.read_text())
        for bad_id in ("", " " + self.cards[0] + " ", 7, "x" * 129):
            with self.subTest(bad_id=bad_id):
                state = json.loads(json.dumps(original))
                state["cards"][0]["id"] = bad_id
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                with patch.object(self.store, "_normalize_cards") as normalize_cards:
                    result = await self.call("search_clients", {}, raw=True)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "state_file_corrupted")
                normalize_cards.assert_not_called()
                self.assertEqual(before, self.snapshot())

    async def test_foreign_duplicate_cannot_mask_an_explicit_client_card(self):
        def duplicate_cross_client(state):
            a = next(card for card in state["cards"] if card["id"] == self.cards[0])
            b = next(card for card in state["cards"] if card["id"] == self.cards[1])
            b["id"] = a["id"]
            state["cards"] = [b, a]

        self.edit_state(duplicate_cross_client)
        before = self.snapshot()
        for raw, name, arguments in (
            (False, "agent_search", {"entity": "client", "query": "SYNTHETIC_A"}),
            (True, "search_clients", {"query": "SYNTHETIC_A"}),
            (True, "list_clients", {"limit": 1, "include_stats": True}),
        ):
            result = await self.call(name, arguments, raw=raw)
            self.assertFalse(result["ok"], result)
            if raw:
                self.assertEqual(result["error"]["code"], "state_file_corrupted")
            else:
                self.assertIn("state_file_corrupted", result["warnings"])
                self.assertEqual(result["data"]["items"], [])
            self.assertEqual(before, self.snapshot())

    async def test_unrelated_duplicate_does_not_hide_or_block_selected_client(self):
        def duplicate_unrelated(state):
            card_b = next(card for card in state["cards"] if card["id"] == self.cards[1])
            state["cards"].append(dict(card_b))

        self.edit_state(duplicate_unrelated)
        before = self.snapshot()
        result = await self.call("list_clients", {"limit": 1, "include_stats": True}, raw=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["clients"][0]["id"], self.client_a["id"])
        self.assertEqual(result["data"]["clients"][0]["stats"]["cards_total"], 1)
        self.assertEqual(before, self.snapshot())

    async def test_collision_remains_visible_if_retention_omits_one_raw_card(self):
        def create_collision_beyond_retention(state):
            active = next(card for card in state["cards"] if card["id"] == self.cards[0])
            archived = {
                **active,
                "archived": True,
                "client_id": "",
                "client_vehicle_id": "",
                "vehicle_profile": {},
                "repair_order": {},
                "updated_at": "2000-01-01T00:00:00+00:00",
            }
            newer = [
                {
                    **archived,
                    "id": f"SYNTHETIC_ARCHIVED_{index}",
                    "updated_at": "2025-01-01T00:00:00+00:00",
                }
                for index in range(300)
            ]
            state["cards"] = [active, *newer, archived]

        self.edit_state(create_collision_beyond_retention)
        before = self.snapshot()
        observed = {}
        original_retention = self.store._apply_card_retention

        def record_retention(cards):
            retained, changed = original_retention(cards)
            observed["changed"] = changed
            observed["raw_id_count"] = sum(card.id == self.cards[0] for card in cards)
            observed["retained_id_count"] = sum(card.id == self.cards[0] for card in retained)
            return retained, changed

        with patch.object(self.store, "_apply_card_retention", side_effect=record_retention):
            result = await self.call("list_clients", {"limit": 1, "include_stats": True}, raw=True)
        self.assertEqual(observed, {"changed": True, "raw_id_count": 2, "retained_id_count": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "state_file_corrupted")
        self.assertEqual(before, self.snapshot())

    async def test_profile_only_list_does_not_normalize_an_unrelated_bad_card(self):
        def break_unrelated_card(state):
            card_b = next(card for card in state["cards"] if card["id"] == self.cards[1])
            card_b.pop("id")

        self.edit_state(break_unrelated_card)
        before = self.snapshot()
        with patch.object(
            self.store, "_normalize_cards", side_effect=AssertionError("must not normalize cards")
        ) as normalize_cards:
            profiles = await self.call(
                "list_clients", {"limit": 1, "include_stats": False}, raw=True
            )
        normalize_cards.assert_not_called()
        self.assertTrue(profiles["ok"], profiles)
        self.assertEqual(profiles["data"]["clients"][0]["id"], self.client_a["id"])
        self.assertEqual(profiles["data"]["meta"]["total"], 2)
        with_stats = await self.call("list_clients", {"limit": 1, "include_stats": True}, raw=True)
        self.assertFalse(with_stats["ok"])
        self.assertEqual(with_stats["error"]["code"], "state_file_corrupted")
        self.assertEqual(before, self.snapshot())

    async def test_repeated_search_reuses_only_the_current_readonly_snapshot(self):
        self.edit_state(lambda state: state.update(settings="SYNTHETIC_LEGACY"))
        before = self.snapshot()
        with (
            patch.object(self.store, "_read_state", wraps=self.store._read_state) as read,
            patch.object(
                self.service,
                "_client_readonly_related_cards",
                wraps=self.service._client_readonly_related_cards,
            ) as related,
        ):
            first = self.service.search_clients_readonly({"query": "SYNTHETIC_A"})
            first["clients"][0]["name"] = "SYNTHETIC_MUTATION"
            first["clients"][0]["vehicles"][0]["vehicle"] = "SYNTHETIC_MUTATION"
            first["clients"][0]["related_card_candidates"][0]["vehicle"] = "SYNTHETIC_MUTATION"
            second = self.service.search_clients_readonly({"query": "SYNTHETIC_A"})
            third = self.service.search_clients_readonly({"query": "SYNTHETIC_B"})
        self.assertEqual(read.call_count, 3 if json_store._SNAPSHOT_REQUIRES_FILE_LOCK else 1)
        self.assertEqual(related.call_count, 3 if json_store._SNAPSHOT_REQUIRES_FILE_LOCK else 1)
        self.assertNotEqual(second["clients"][0]["name"], "SYNTHETIC_MUTATION")
        self.assertNotEqual(second["clients"][0]["vehicles"][0]["vehicle"], "SYNTHETIC_MUTATION")
        self.assertNotEqual(
            second["clients"][0]["related_card_candidates"][0]["vehicle"], "SYNTHETIC_MUTATION"
        )
        self.assertTrue(third["clients"])
        self.assertEqual(before, self.snapshot())

    async def test_external_same_size_and_mtime_edit_invalidates_the_search_index(self):
        first = await self.call("agent_search", {"entity": "client", "query": "SYNTHETIC_A"})
        self.assertTrue(first["data"]["items"])
        old = self.state_file.stat()
        data = self.state_file.read_bytes()
        changed = data.replace(b"SYNTHETIC_A", b"SYNTHETIC_Z")
        self.assertNotEqual(changed, data)
        self.state_file.write_bytes(changed)
        os.utime(self.state_file, ns=(old.st_atime_ns, old.st_mtime_ns))
        current = self.state_file.stat()
        self.assertEqual((old.st_size, old.st_mtime_ns), (current.st_size, current.st_mtime_ns))
        before = self.snapshot()
        stale = await self.call("agent_search", {"entity": "client", "query": "SYNTHETIC_A"})
        fresh = await self.call("agent_search", {"entity": "client", "query": "SYNTHETIC_Z"})
        self.assertTrue(stale["ok"])
        self.assertNotIn("SYNTHETIC_A", [item.get("name") for item in stale["data"]["items"]])
        self.assertTrue(fresh["ok"])
        refreshed = next(
            item for item in fresh["data"]["items"] if item["id"] == self.client_a["id"]
        )
        self.assertEqual(refreshed["name"], "SYNTHETIC_Z")
        self.assertEqual(before, self.snapshot())

    async def test_new_http_routes_require_bearer_auth_and_preserve_readonly_files(self):
        server = ApiServer(self.service, self.logger, bearer_token="synthetic-api-token")
        handler = server._make_handler()
        fake_server = SimpleNamespace(api_logger=self.logger)
        before = self.snapshot()

        def request(path, authorization):
            server_socket, client_socket = socket.socketpair()
            thread = threading.Thread(
                target=handler, args=(server_socket, ("127.0.0.1", 1), fake_server)
            )
            thread.start()
            try:
                header = f"Authorization: {authorization}\r\n" if authorization else ""
                client_socket.sendall(
                    f"GET {path} HTTP/1.1\r\nHost: localhost\r\n{header}Connection: close\r\n\r\n".encode()
                )
                response = http.client.HTTPResponse(client_socket)
                response.begin()
                return response.status, json.loads(response.read())
            finally:
                client_socket.close()
                thread.join(timeout=5)
                server_socket.close()
                self.assertFalse(thread.is_alive())

        for path in (
            "/api/search_clients_readonly?query=SYNTHETIC_A",
            "/api/list_clients_readonly?include_stats=false",
        ):
            status, payload = request(path, "Bearer wrong-token")
            self.assertEqual(status, 401)
            self.assertEqual(payload["error"]["code"], "unauthorized")
            status, payload = request(path, "Bearer synthetic-api-token")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["meta"]["read_only_contract"], "client-search-v1")
        self.assertEqual(before, self.snapshot())

        def duplicate_unselected(state):
            client_b = next(item for item in state["clients"] if item["id"] == self.client_b["id"])
            state["clients"].append(dict(client_b))

        self.edit_state(duplicate_unselected)
        before = self.snapshot()
        for path in ("/api/search_clients_readonly?limit=1", "/api/list_clients_readonly?limit=1"):
            status, payload = request(path, "Bearer synthetic-api-token")
            self.assertEqual(status, 503)
            self.assertEqual(payload["error"]["code"], "state_file_corrupted")
        self.assertEqual(before, self.snapshot())
