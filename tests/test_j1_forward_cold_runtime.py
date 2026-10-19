"""Artificial HTTP/MCP peers for the bounded cold metadata protocol."""

from __future__ import annotations

import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import j1_forward_cold_runtime as runtime
from scripts import j1_forward_io as io
from scripts import j1_forward_runtime as http

CANDIDATE = {"image": "sha256:" + "1" * 64, "revision": "2" * 40, "tree": "3" * 40}
ATTEMPT = "00000000-0000-4000-8000-000000000004"


def report(candidate=CANDIDATE, attempt=ATTEMPT, phase="closed"):
    return {
        "schema": runtime.SCHEMA,
        "ok": True,
        "candidate": candidate,
        "attempt_id": attempt,
        "phase": phase,
        "business_acceptance": False,
        "store_effects_exercised": False,
        "elapsed_ms": 10,
        "public": {
            "health_http": 200,
            "ui_http": 200,
            "board_asset_sha256": "a" * 64,
            "anonymous_guards": {"public_api": 401, "local_api": 401, "public_mcp": 401},
        },
        "api": {"authorized_read_http": 200, "revision_sha256": "b" * 64, "metadata_only": True},
        "mcp": {
            "authenticated": True,
            "tool_catalog_sha256": "c" * 64,
            "tool_count": 1,
            "readonly_runtime_tool": True,
            "store_live_check": phase == "open",
            "crm_ready": True if phase == "open" else None,
            "store_ready": True if phase == "open" else None,
        },
    }


class HttpPeer:
    def __init__(self, _deadline):
        self.requests = []

    def request(self, url, **options):
        self.requests.append((url, options))
        script = b"synthetic /api/login_operator asset"
        digest = io.sha(script)
        if url.endswith("/api/health"):
            return http.Response(200, "application/json", io.canonical({"ok": True, "data": {}}))
        if url == runtime.PUBLIC + "/":
            return http.Response(
                200, "text/html", f'<script src="/assets/board.{digest}.js"></script>'.encode()
            )
        if url.endswith(f"/assets/board.{digest}.js"):
            return http.Response(200, "application/javascript", script)
        if url == runtime.API + runtime.REVISION_ROUTE and options.get("headers"):
            return http.Response(
                200,
                "application/json",
                io.canonical(
                    {
                        "ok": True,
                        "data": {
                            "revision": "synthetic-version",
                            "counts": {"synthetic-private-field": 123},
                        },
                    }
                ),
            )
        return http.Response(401, "application/json", b"{}")


class Tool:
    name = "get_runtime_status"

    def model_dump(self, **_options):
        return {
            "name": self.name,
            "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object"},
        }


class Session:
    def __init__(self):
        self.calls = []
        self.page = SimpleNamespace(tools=[Tool()], nextCursor=None)
        self.payload = {
            "ok": True,
            "status": "ready",
            "data": {"store": {"ok": True, "status": "ready"}},
        }

    async def initialize(self):
        self.calls.append(("initialize", None))

    async def list_tools(self, cursor=None):
        self.calls.append(("tools/list", cursor))
        return self.page

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(isError=False, structuredContent=self.payload)


class ColdMetadataTests(unittest.TestCase):
    def test_closed_probe_only_requests_fixed_metadata_and_never_a_tool(self):
        peer, session = HttpPeer(0), Session()

        async def inspect(_token, *, phase, deadline):
            self.assertGreater(deadline, 0)
            return await runtime.inspect_session(session, phase=phase)

        result = runtime.probe(
            CANDIDATE,
            ATTEMPT,
            "closed",
            environment={
                "MINIMAL_KANBAN_API_BEARER_TOKEN": "synthetic-api",
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "synthetic-mcp",
            },
            client_factory=lambda _deadline: peer,
            mcp_inspector=inspect,
        )
        self.assertEqual(session.calls, [("initialize", None), ("tools/list", None)])
        self.assertEqual(len(peer.requests), 7)
        self.assertTrue(
            all(
                options.get("method", "GET") == "GET" and "payload" not in options
                for _, options in peer.requests
            )
        )
        self.assertFalse(result["business_acceptance"])
        for private in (
            "synthetic-api",
            "synthetic-mcp",
            "synthetic-private-field",
            "synthetic-version",
        ):
            self.assertNotIn(private.encode(), io.canonical(result))

    def test_missing_existing_credentials_does_not_open_any_transport(self):
        factory = Mock(side_effect=AssertionError("transport opened"))
        with self.assertRaisesRegex(io.Refusal, "credentials_missing"):
            runtime.probe(CANDIDATE, ATTEMPT, "closed", environment={}, client_factory=factory)
        factory.assert_not_called()

    def test_open_phase_calls_only_existing_readonly_relay(self):
        session = Session()
        value = asyncio.run(runtime.inspect_session(session, phase="open"))
        self.assertEqual(session.calls[-1], ("get_runtime_status", {}))
        self.assertTrue(value["store_live_check"] and value["store_ready"])

    def test_degraded_store_cannot_be_masked_by_ready_crm(self):
        session = Session()
        session.payload["data"]["store"]["ok"] = False
        with self.assertRaisesRegex(io.Refusal, "store_degraded"):
            asyncio.run(runtime.inspect_session(session, phase="open"))

    def test_duplicate_or_unbounded_paginated_catalog_refuses_before_tool_call(self):
        for duplicate in (True, False):
            session = Session()
            session.page.tools = [Tool(), Tool()] if duplicate else []
            session.page.nextCursor = "synthetic-next"
            with self.subTest(duplicate=duplicate), self.assertRaises(io.Refusal):
                asyncio.run(runtime.inspect_session(session, phase="open"))
            self.assertFalse(any(name == "get_runtime_status" for name, _ in session.calls))

    def test_phase_scope_identity_and_acceptance_claims_are_exact(self):
        for phase in ("closed", "open"):
            self.assertEqual(
                runtime.validate_report(
                    report(phase=phase), candidate=CANDIDATE, attempt=ATTEMPT, phase=phase
                )["phase"],
                phase,
            )
        for field, value in (
            ("phase", "open"),
            ("attempt_id", "different"),
            ("business_acceptance", True),
            ("store_effects_exercised", True),
            ("elapsed_ms", 80001),
        ):
            changed = copy.deepcopy(report())
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(io.Refusal):
                runtime.validate_report(
                    changed, candidate=CANDIDATE, attempt=ATTEMPT, phase="closed"
                )


if __name__ == "__main__":
    unittest.main()
