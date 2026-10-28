"""Execute synthetic client proposals through real local CRM and Telegram handlers.

No model, provider or live service is called. This optional POSIX/root rehearsal
requires both checkouts and their existing test dependencies; it is not part of
the ordinary CRM-only unit collection.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

CRM_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_SENTINEL = "INTERNAL_BUY_ONLY_700"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sources(manager_root):
    sys.path[:0] = [
        str(CRM_ROOT / "src"),
        str(CRM_ROOT),
        str(manager_root),
        str(manager_root / "tests"),
    ]
    import pytest
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
    from minimal_kanban.repair_order import RepairOrder
    from minimal_kanban.services.card_service import CardService
    from minimal_kanban.storage.json_store import JsonStore
    from tests import test_agent_gateway_v2 as gateway

    return SimpleNamespace(
        gateway=gateway,
        CardService=CardService,
        JsonStore=JsonStore,
        RepairOrder=RepairOrder,
        owner_context=auth_context_var,
        owner=AuthenticatedUser(
            OwnerAccessToken(
                token="synthetic-owner-token",
                client_id="synthetic-client",
                subject="CODEX",
                family_id="synthetic-family",
                scopes=["kanban:read", "kanban:write"],
                resource="https://crm.example/mcp",
            )
        ),
        pytest=pytest,
        telegram=importlib.import_module("test_telegram_durable_integration"),
        bridge=importlib.import_module("autostop_manager.telegram_bridge"),
        wake=importlib.import_module("autostop_manager.telegram_wake"),
        jpeg=importlib.import_module("test_telegram_media_native")._jpeg,
    )


def deny_internet(monkeypatch, temporary_root):
    counters = {"blocked_network_calls": 0}
    for method in ("bind", "connect", "connect_ex", "sendto"):
        original = getattr(socket.socket, method)

        def local_only(sock, *args, _original=original, **kwargs):
            address = args[-1] if args else kwargs.get("address")
            private_unix = (
                sock.family == socket.AF_UNIX
                and isinstance(address, (str, bytes))
                and not address.startswith(b"\x00" if isinstance(address, bytes) else "\x00")
                and Path(address.decode() if isinstance(address, bytes) else address)
                .resolve()
                .is_relative_to(temporary_root)
            )
            if not private_unix:
                counters["blocked_network_calls"] += 1
                raise AssertionError("Only private temporary Unix sockets are permitted")
            return _original(sock, *args, **kwargs)

        monkeypatch.setattr(socket.socket, method, local_only)

    def no_dns(*_args, **_kwargs):
        counters["blocked_network_calls"] += 1
        raise AssertionError("DNS is not permitted in a synthetic client rehearsal")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    return counters


def customer_safe(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    for marker in (
        PRIVATE_SENTINEL,
        "purchase_unit",
        "delivery_unit",
        "direct_expenses_unit",
        "profit",
    ):
        require(marker not in text, "Internal field reached a customer surface: " + marker)
    require(
        re.search(r"\b700(?:[.,]0+)?\b", text) is None, "Purchase price reached a customer surface"
    )


def one_page_pdf(text):
    """One visible ASCII page, unlike the zero-page transport-only fixture."""
    lines = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")
        for line in textwrap.wrap(text, width=85)
    ]
    stream = (
        b"BT /F1 10 Tf 30 760 Td\n"
        + b"0 -14 Td\n".join(b"(" + line + b") Tj\n" for line in lines)
        + b"ET\n"
    )
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def real_board(deps, directory):
    logger = logging.getLogger("synthetic.client.handoff")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    store = deps.JsonStore(state_file=directory / "state.json", logger=logger)
    service = deps.CardService(
        store,
        logger,
        attachments_dir=directory / "attachments",
        repair_orders_dir=directory / "orders",
    )
    client = service.create_client(
        {"display_name": "Synthetic client", "vehicles": [{"vehicle": "Synthetic car"}]}
    )["client"]
    vehicle_id = client["vehicles"][0]["id"]
    card = service.create_card(
        {
            "title": "Synthetic existing preliminary request",
            "description": "Prior customer request.",
            "column": "parts_store",
            "client_id": client["id"],
            "client_vehicle_id": vehicle_id,
            "tags": ["Preserve label"],
        }
    )["card"]
    state = deepcopy(store.read_bundle())
    stored = next(item for item in state["cards"] if item.id == card["id"])
    stored.repair_order = deps.RepairOrder.from_dict(
        {
            "number": "SYN-1",
            "works": [
                {
                    "id": "33333333-1111-4111-8111-111111111111",
                    "name": "Prior work",
                    "quantity": "2",
                    "price": "33",
                }
            ],
            "materials": [
                {
                    "id": "44444444-1111-4111-8111-111111111111",
                    "name": "Prior material",
                    "quantity": "1",
                    "cost_price": "7",
                    "price": "20",
                }
            ],
        }
    )
    store.write_bundle(**state)

    class RealBoard(deps.gateway.FakeBoardApi):
        mutations = 0
        lose_response = False

        def get_card(self, card_id):
            return {"ok": True, "data": service.get_card({"card_id": card_id})}

        def get_card_context(self, card_id, **kwargs):
            return {
                "ok": True,
                "data": service.get_card_context({"card_id": card_id, **kwargs, "read_only": True}),
            }

        def get_client(self, client_id, **kwargs):
            return {"ok": True, "data": service.get_client({"client_id": client_id, **kwargs})}

        def run_manager_operation(
            self, *, operation, payload=None, mode="dry_run", actor_name=None, limit=None
        ):
            require(operation == "cleanup_card", "Unexpected CRM operation")
            result = service.cleanup_card(
                {**(payload or {}), "mode": mode, "actor_name": actor_name}
            )
            if mode == "apply" and result["changed"]:
                self.mutations += 1
                if self.lose_response:
                    raise OSError("Synthetic response lost after persistence")
            return {"ok": True, "data": result}

    case = deps.gateway.AgentGatewayV2Tests(
        "test_manager_raw_client_card_link_writes_use_exact_readback"
    )
    case.setUp()
    board = RealBoard()
    server, ledger = case._create_store_server(board_api=board)
    return SimpleNamespace(
        service=service,
        store=store,
        client=client,
        card=card,
        board=board,
        server=server,
        ledger=ledger,
        fixture=case,
    )


async def crm_update(deps, crm, case, private_note, *, lose_response=False):
    context = await crm.server._tool_manager.get_tool("agent_entity_context").run(
        {"entity": "card", "entity_id": crm.card["id"], "detail": "full"}, convert_result=False
    )
    require(context.structuredContent["ok"], "Full card context unavailable")
    previous = crm.service.get_card({"card_id": crm.card["id"]})["card"]
    require(previous["column"] == "parts_store", "Preliminary request is outside parts_store")
    require(
        previous["client_id"] == crm.client["id"]
        and previous["client_vehicle_id"] == crm.client["vehicles"][0]["id"],
        "CRM links are not exact",
    )
    facts = case["inputs"]["facts"]
    description = previous["description"] + "\n" + case["inputs"]["events"][1]["text"]
    description += f"\n{facts['selected_part']}; quantity={facts['quantity']}; sale={facts['client_unit_price']}; next step: colleague verifies fitment, availability and lead time. {private_note}"
    payload = {
        "content_only": True,
        "card_id": previous["id"],
        "expected_updated_at": previous["updated_at"],
        "description": description,
    }
    arguments = {
        "operation": "cleanup_card",
        "payload": payload,
        "idempotency_key": "synthetic-client-consent",
        "mode": "apply",
    }
    token = deps.owner_context.set(deps.owner)
    try:
        tool = crm.server._tool_manager.get_tool("agent_board_workflow")
        preview = await tool.run(
            arguments | {"mode": "dry_run", "idempotency_key": "synthetic-client-preview"},
            convert_result=False,
        )
        require(
            preview.structuredContent["ok"] and crm.board.mutations == 0,
            "Preview performed a mutation",
        )
        before_order = next(
            item for item in crm.store.read_bundle()["cards"] if item.id == previous["id"]
        ).repair_order.to_storage_dict()
        crm.board.lose_response = lose_response
        applied = (await tool.run(arguments, convert_result=False)).structuredContent
        repeated = (await tool.run(arguments, convert_result=False)).structuredContent
    finally:
        deps.owner_context.reset(token)
    after = crm.service.get_card({"card_id": previous["id"]})["card"]
    require(after["column"] == "parts_store", "Preliminary request route changed")
    require(
        after["description"] == description and crm.board.mutations == 1,
        "CRM write/replay was not applied exactly once",
    )
    for field in ("tags", "column", "archived", "client_id", "client_vehicle_id"):
        require(after[field] == previous[field], "Unrelated card field changed: " + field)
    after_order = next(
        item for item in crm.store.read_bundle()["cards"] if item.id == previous["id"]
    ).repair_order.to_storage_dict()
    require(before_order == after_order, "Existing repair order was changed")
    require(PRIVATE_SENTINEL in after["description"], "Internal information was not kept in CRM")
    if lose_response:
        require(
            not applied["ok"] and not repeated["ok"], "Lost CRM response became a false success"
        )
    else:
        require(applied["ok"] and repeated["ok"], "CRM workflow or replay failed")
    return after


async def complete(deps, runtime, job, outcome, receipts):
    result = await runtime.jobs.request(
        "job_result",
        **deps.telegram.binding(job),
        outcome=outcome,
        event_ids=job["event_ids"],
        receipt_keys=receipts,
    )
    done = result["job"]
    await runtime.jobs.request(
        "job_execution_proof",
        **deps.telegram.binding(done),
        proof_kind="terminal",
        thread_id=done["thread_id"],
        turn_id=done["turn_id"],
        terminal_status="completed",
        local_tasks_done=True,
    )


async def text_send(deps, runtime, job, text, *, kind="result", slot=0):
    customer_safe(text)
    dry = await deps.telegram.send(runtime, job, kind=kind, text=text, slot=slot)
    return await deps.telegram.send(runtime, job, kind=kind, text=text, slot=slot, dry=dry)


async def consent_scenario(deps, directory, fixture, private_note, *, lose_response=False):
    crm_dir = directory / "crm"
    crm_dir.mkdir()
    crm = real_board(deps, crm_dir)
    initial_storage = {path: path.read_bytes() for path in crm_dir.rglob("*.json")}
    try:
        with deps.pytest.MonkeyPatch.context() as monkeypatch:
            bundle = deps.telegram.bundle.__wrapped__(directory, monkeypatch)
            async with deps.telegram.bridge_runtime(directory) as runtime:
                runtime.monitor.config = runtime.config
                runtime.monitor.self_peer_id = 900
                runtime.monitor.record(
                    runtime.client.inbound(100, text=fixture["inputs"]["events"][0]["text"])
                )
                job = await deps.telegram.claim_running(runtime, bundle)
                receipt = await text_send(
                    deps, runtime, job, fixture["client_replies"][0]["text"], kind="clarification"
                )
                await complete(deps, runtime, job, "awaiting_client", [receipt["receipt_key"]])
                require(crm.board.mutations == 0, "Ambiguous consent changed CRM")
                require(
                    initial_storage
                    == {path: path.read_bytes() for path in crm_dir.rglob("*.json")},
                    "Ambiguous consent changed CRM storage bytes",
                )
                runtime.monitor.record(
                    runtime.client.inbound(200, text=fixture["inputs"]["events"][1]["text"])
                )
                job = await deps.telegram.claim_running(runtime, bundle)
                saved = await crm_update(
                    deps, crm, fixture, private_note, lose_response=lose_response
                )
                if lose_response:
                    require(
                        not [
                            m
                            for m in runtime.client.messages.values()
                            if m.out and m.chat_id == deps.telegram.OWNER
                        ],
                        "Unknown CRM result triggered handoff",
                    )
                    require(
                        runtime.client.sent == 1,
                        "Unknown CRM result triggered a client confirmation",
                    )
                    await runtime.jobs.request(
                        "job_needs_review",
                        **deps.telegram.binding(job),
                        reason_code="crm_write_outcome_uncertain",
                    )
                    return {
                        "scenario": "lost_crm_response",
                        "passed": True,
                        "crm_mutations": 1,
                        "owner_notices": 0,
                        "client_confirmations": 0,
                    }
                notice = deps.telegram.binding(job) | {
                    "reply_to_message_id": 200,
                    "text": "Synthetic preliminary handoff " + saved["id"] + " " + private_note,
                    "mode": "dry_run",
                }
                dry = await runtime.jobs.request("job_handoff", **notice)
                owner = await runtime.jobs.request(
                    "job_handoff",
                    **(
                        notice
                        | {
                            "mode": "apply",
                            "contract_token": dry["contract_token"],
                            "idempotency_key": dry["idempotency_key"],
                        }
                    ),
                )
                closing = await text_send(deps, runtime, job, fixture["client_replies"][1]["text"])
                await complete(
                    deps, runtime, job, "handoff", [owner["receipt_key"], closing["receipt_key"]]
                )
                owners = [
                    m
                    for m in runtime.client.messages.values()
                    if m.out and m.chat_id == deps.telegram.OWNER
                ]
                require(
                    len(owners) == 1 and PRIVATE_SENTINEL in owners[0].message,
                    "Owner handoff was not verified once",
                )
                for message in runtime.client.messages.values():
                    if message.out and message.chat_id == deps.telegram.CLIENT:
                        customer_safe(message.message)
                require(runtime.inbox.status()["open_events"] == 0, "Client request remained open")
                return {
                    "scenario": "consent_exact_handoff",
                    "passed": True,
                    "crm_mutations": 1,
                    "owner_notices": 1,
                    "native_handoff_verified": owner["verified"],
                    "preserved_card_and_order": True,
                }
    finally:
        crm.fixture.doCleanups()


async def failed_turn_crm_scenario(deps, directory, fixture, private_note):
    """A real committed CRM write is invisible to the Telegram effects ledger."""
    crm_dir = directory / "crm"
    crm_dir.mkdir()
    crm = real_board(deps, crm_dir)
    try:
        with deps.pytest.MonkeyPatch.context() as monkeypatch:
            bundle = deps.telegram.bundle.__wrapped__(directory, monkeypatch)
            async with deps.telegram.bridge_runtime(directory) as runtime:
                runtime.monitor.config = runtime.config
                runtime.monitor.self_peer_id = 900
                original_event = runtime.monitor.record(
                    runtime.client.inbound(100, text=fixture["inputs"]["events"][1]["text"])
                )
                server = deps.telegram.MockServer(complete=False)
                async with deps.telegram.worker(runtime, directory, bundle, server) as dispatcher:
                    await deps.telegram.until(
                        lambda: (runtime.inbox.execution_slot() or {}).get("state") == "running"
                    )
                    job = runtime.inbox.execution_slot()
                    require(job["event_ids"] == [original_event], "Consent binding changed")
                    await crm_update(deps, crm, fixture, private_note, lose_response=True)
                    current = runtime.inbox.read_job(job["job_id"])
                    require(current["effects"] == [], "CRM effect leaked into Telegram ledger")
                    server.turn_statuses[job["turn_id"]] = "failed"
                    await server.sockets[-1].send(
                        json.dumps(
                            {
                                "method": "turn/completed",
                                "params": {
                                    "threadId": job["thread_id"],
                                    "turn": {"id": job["turn_id"], "status": "failed"},
                                },
                            }
                        )
                    )
                    await deps.telegram.until(
                        lambda: (
                            runtime.inbox.execution_slot() is None
                            and dispatcher.current_job is None
                            and dispatcher.status()["ready"]
                        )
                    )
                    current = runtime.inbox.read_job(job["job_id"])
                    require(
                        current["state"] == "needs_review"
                        and current["execution_proof"] == "failed"
                        and current["reason_code"] == "codex_turn_failed",
                        "Automatic failed-turn recovery released an unknown CRM write",
                    )
                    require(
                        current["event_ids"] == job["event_ids"],
                        "Failed-turn recovery changed the original event binding",
                    )
                    try:
                        await runtime.jobs.request(
                            "job_defer_failed", **deps.telegram.binding(current)
                        )
                    except deps.wake.WakeError as error:
                        require(
                            str(error) == "inbox_failure_recovery_unsafe",
                            "Unexpected failed-turn defer rejection",
                        )
                    else:
                        raise AssertionError(
                            "Native defer accepted a failed turn with unknown CRM write"
                        )
                new_event = runtime.monitor.record(
                    runtime.client.inbound(200, text="Synthetic client asks about the request")
                )
                claimed = await runtime.jobs.request(
                    "job_claim",
                    instruction_root=str(bundle),
                    instruction_revision=deps.telegram.REVISION,
                    worker_protocol=2,
                )
                require(claimed["job"] is None, "New input released the unknown original request")
                require(
                    [item["job_id"] for item in runtime.inbox.active_jobs()] == [job["job_id"]]
                    and runtime.inbox.read_job(job["job_id"])["event_ids"] == job["event_ids"]
                    and all(
                        runtime.inbox.event_state(event) == "open"
                        for event in (original_event, new_event)
                    )
                    and runtime.inbox.status()["open_events"] == 2,
                    "Original or new event escaped the held peer",
                )
                require(
                    crm.board.mutations == 1 and runtime.client.sent == 0 and server.counter == 1,
                    "Failed CRM turn caused another mutation, delivery or model turn",
                )
                return {
                    "scenario": "failed_turn_after_unknown_crm_write",
                    "passed": True,
                    "crm_mutations": 1,
                    "telegram_effects": 0,
                    "automatic_state": current["state"],
                    "native_failed_defer_rejected": True,
                    "original_binding_preserved": True,
                    "new_job_created": False,
                    "open_events": 2,
                }
    finally:
        crm.fixture.doCleanups()


async def media_scenario(deps, directory, fixture):
    exports, facts = fixture["client_export_proposals"], fixture["inputs"]["facts"]
    offer = next(item for item in facts["offers"] if item["id"] == facts["selected_part"])
    sale = sum(
        Decimal(str(offer[key]))
        for key in ("purchase_unit", "delivery_unit", "direct_expenses_unit")
    ) / Decimal("0.70")
    require(
        sale == fixture["claims"]["client_unit_price"] == 1000,
        "Fixture sale does not follow the existing F4 policy",
    )
    customer_safe(exports["table_draft"])
    customer_safe(exports["image_excerpt_layout_draft"]["visible_fields"])
    customer_safe(exports["document_excerpt_draft"])
    document_text = f"Preliminary quote: SynParts {facts['selected_part']}; quantity {facts['quantity']}; sale RUB {sale:.2f}; subject to confirmation."
    customer_safe(document_text)
    pdf = one_page_pdf(document_text)
    visible = " ".join(value.decode("ascii") for value in re.findall(rb"\(([^()]*)\) Tj", pdf))
    require(
        b"/Count 1" in pdf and visible == document_text and b") Tj" in pdf,
        "PDF has no visible quote page",
    )
    customer_safe(visible)
    with deps.pytest.MonkeyPatch.context() as monkeypatch:
        bundle = deps.telegram.bundle.__wrapped__(directory, monkeypatch)
        async with deps.telegram.bridge_runtime(directory) as runtime:
            runtime.monitor.config = runtime.config
            runtime.monitor.self_peer_id = 900
            runtime.monitor.record(
                runtime.client.inbound(100, text=fixture["inputs"]["events"][0]["text"])
            )
            job = await deps.telegram.claim_running(runtime, bundle)
            receipts = []
            receipts.append(
                (await text_send(deps, runtime, job, exports["main_text"]))["receipt_key"]
            )
            table = "\n".join(
                " | ".join(row)
                for row in [exports["table_draft"]["columns"], *exports["table_draft"]["rows"]]
            )
            receipts.append((await text_send(deps, runtime, job, table, slot=1))["receipt_key"])
            outbox = runtime.config.socket_path.parent / "outbox"
            outbox.mkdir(mode=0o700)
            original = runtime.client.send_file
            uploads = {}

            async def capture(entity, upload, **kwargs):
                position = upload.tell()
                uploads[upload.name] = upload.read()
                upload.seek(position)
                return await original(entity, upload, **kwargs)

            runtime.client.send_file = capture
            for slot, (kind, suffix, content) in enumerate(
                (("document", ".pdf", pdf), ("photo", ".jpg", deps.jpeg())), 2
            ):
                path = outbox / ("customer-quote" + suffix)
                path.write_bytes(content)
                path.chmod(0o600)
                request = {
                    "peer": str(deps.telegram.CLIENT),
                    kind: str(path),
                    "caption": exports["caption_draft"],
                    "mode": "dry_run",
                    "reply_to_message_id": 100,
                    "job_id": job["job_id"],
                    "lease": job["lease"],
                    "revision": job["revision"],
                    "kind": "result",
                    "effect_slot": slot,
                }
                dry = await runtime.jobs.request("send_" + kind, **request)
                applied = await runtime.jobs.request(
                    "send_" + kind,
                    **(
                        request
                        | {
                            "mode": "apply",
                            "contract_token": dry["contract_token"],
                            "idempotency_key": dry["idempotency_key"],
                        }
                    ),
                )
                message = runtime.client.messages[applied["message_id"]]
                require(
                    applied["verified"]
                    and uploads[path.name] == content
                    and await runtime.client.download_media(message, file=bytes) == content,
                    "Media upload/readback bytes differ",
                )
                receipts.append(applied["receipt_key"])
                path.unlink()
                require(not path.exists(), "Outbox file was not removed")
            missing = {
                "peer": str(deps.telegram.CLIENT),
                "photo": str(outbox / "missing.jpg"),
                "caption": exports["caption_draft"],
                "mode": "dry_run",
                "reply_to_message_id": 100,
                "job_id": job["job_id"],
                "lease": job["lease"],
                "revision": job["revision"],
                "kind": "result",
                "effect_slot": 4,
            }
            before = len(runtime.inbox.read_job(job["job_id"])["effects"])
            try:
                await runtime.jobs.request("send_photo", **missing)
            except deps.wake.WakeError:
                pass
            else:
                raise AssertionError("Unavailable media unexpectedly succeeded")
            require(
                len(runtime.inbox.read_job(job["job_id"])["effects"]) == before,
                "Unavailable media created a send intent",
            )
            receipts.append(
                (await text_send(deps, runtime, job, exports["main_text"], slot=4))["receipt_key"]
            )
            await complete(deps, runtime, job, "answered", receipts)
            for message in runtime.client.messages.values():
                if message.out and message.chat_id == deps.telegram.CLIENT:
                    customer_safe(message.message)
            customer_safe(pdf.decode("ascii"))
            for content in uploads.values():
                for marker in (
                    PRIVATE_SENTINEL,
                    "purchase_unit",
                    "delivery_unit",
                    "direct_expenses_unit",
                    "profit",
                ):
                    require(
                        marker.encode() not in content, "Internal marker reached uploaded media"
                    )
            require(not list(outbox.iterdir()), "Temporary outbox media remains")
            verified_messages = runtime.client.sent
            runtime.monitor.record(
                runtime.client.inbound(200, text="Synthetic second document request")
            )
            unknown_job = await deps.telegram.claim_running(runtime, bundle)
            path = outbox / "uncertain.pdf"
            path.write_bytes(pdf)
            path.chmod(0o600)
            request = {
                "peer": str(deps.telegram.CLIENT),
                "document": str(path),
                "caption": exports["caption_draft"],
                "mode": "dry_run",
                "reply_to_message_id": 200,
                "job_id": unknown_job["job_id"],
                "lease": unknown_job["lease"],
                "revision": unknown_job["revision"],
                "kind": "result",
            }
            dry = await runtime.jobs.request("send_document", **request)
            runtime.client.lose_send_response = True
            try:
                await runtime.jobs.request(
                    "send_document",
                    **(
                        request
                        | {
                            "mode": "apply",
                            "contract_token": dry["contract_token"],
                            "idempotency_key": dry["idempotency_key"],
                        }
                    ),
                )
            except deps.wake.WakeError:
                pass
            else:
                raise AssertionError("Lost media response unexpectedly succeeded")
            finally:
                runtime.client.lose_send_response = False
                path.unlink()
            try:
                await text_send(deps, runtime, unknown_job, exports["main_text"], slot=1)
            except deps.wake.WakeError as error:
                require(
                    str(error) in {"send_outcome_uncertain", "inbox_effect_outcome_unknown"},
                    "Unexpected unknown-media error",
                )
            else:
                raise AssertionError("Unknown media send allowed a fallback")
            require(
                runtime.client.sent == verified_messages + 1 and not list(outbox.iterdir()),
                "Unknown media was resent or temporary media remains",
            )
            return {
                "scenario": "customer_exports_and_media",
                "passed": True,
                "sale": f"{sale:.2f}",
                "customer_messages": verified_messages,
                "verified_binary_uploads": 2,
                "pdf_visible_pages": 1,
                "temporary_media_removed": True,
                "unknown_media_blocks_fallback": True,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager-root", required=True, type=Path)
    args = parser.parse_args()
    manager_root = args.manager_root.resolve()
    fixture_path = (
        manager_root / "tests/fixtures/telegram/client_candidate_observations_20261028.json"
    )
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    require(data["synthetic_only"] is True, "The observation fixture must be synthetic")
    cases = {case["case_id"]: case for case in data["observations"]}
    pricing_case = cases["candidate-02-price-and-media-fallback"]
    selected = next(
        offer
        for offer in pricing_case["inputs"]["facts"]["offers"]
        if offer["id"] == pricing_case["inputs"]["facts"]["selected_part"]
    )
    private_pricing = {
        key: selected[key] for key in ("purchase_unit", "delivery_unit", "direct_expenses_unit")
    }
    private_pricing["profit"] = pricing_case["claims"]["client_unit_price"] - sum(
        private_pricing.values()
    )
    private_note = PRIVATE_SENTINEL + " " + json.dumps(private_pricing, sort_keys=True)
    import pytest

    with tempfile.TemporaryDirectory(prefix="client-handoff-check-") as temporary:
        base = Path(temporary)
        with pytest.MonkeyPatch.context() as monkeypatch:
            counters = deny_internet(monkeypatch, base)
            deps = sources(manager_root)
            require(
                deps.telegram.os.geteuid() == 0,
                "The existing private Unix RPC fixture requires root",
            )

            async def run():
                results = []
                for name, loss in (("success", False), ("lost", True)):
                    directory = base / name
                    directory.mkdir()
                    results.append(
                        await consent_scenario(
                            deps,
                            directory,
                            cases["candidate-03-preliminary-consent-to-exact-handoff"],
                            private_note,
                            lose_response=loss,
                        )
                    )
                directory = base / "failed-turn"
                directory.mkdir()
                results.append(
                    await failed_turn_crm_scenario(
                        deps,
                        directory,
                        cases["candidate-03-preliminary-consent-to-exact-handoff"],
                        private_note,
                    )
                )
                directory = base / "media"
                directory.mkdir()
                results.append(await media_scenario(deps, directory, pricing_case))
                return results

            results = asyncio.run(run())
            require(
                counters["blocked_network_calls"] == 0,
                "An unexpected network attempt was blocked",
            )
    revision = subprocess.check_output(
        ["git", "-C", str(manager_root), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "-C", str(manager_root), "status", "--porcelain"], text=True
        ).strip()
    )
    print(
        json.dumps(
            {
                "ok": True,
                "manager_revision": revision,
                "manager_worktree_dirty": dirty,
                "synthetic_only": True,
                "controlled_model_proposals": True,
                "real_local_handlers": True,
                "scenarios": results,
                **counters,
                "limits": [
                    "No model or live delivery was exercised",
                    "JPEG is an unannotated reference fixture; no fitment or visual OCR claim",
                    "PDF/structured/text privacy checks cover the controlled proposal, not arbitrary future model outputs",
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
