from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "attest_agent_gateway_v2.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("attest_agent_gateway_v2", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("attest_agent_gateway_v2.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tool_result(payload: dict, *, is_error: bool = False):
    return SimpleNamespace(
        structuredContent=payload,
        isError=is_error,
        model_dump=lambda **_kwargs: {
            "structuredContent": payload,
            "isError": is_error,
        },
    )


def test_manifest_covers_exact_public_and_crm_operation_contracts() -> None:
    module = load_script_module()

    module._validate_static_contract()
    cases = module.build_case_specs()

    assert len(module.PUBLIC_CASE_ORDER) == 24
    assert len(module.BOARD_OPERATION_ORDER) == 10
    assert len(module.INVENTORY_OPERATION_ORDER) == 8
    assert len(module.DOCUMENT_OPERATION_ORDER) == 8
    assert len(module.FINANCE_OPERATION_ORDER) == 17
    assert len(cases) == 70
    assert len({case.case_id for case in cases}) == len(cases)
    assert "download_store_quote_vin_photo" not in module.DOCUMENT_OPERATION_ORDER
    assert module.MANAGER_RAW_CRM_CAPABILITIES == (
        "create_client",
        "create_card",
        "link_card_to_client",
    )


def test_runtime_evidence_never_serializes_request_or_response_payloads() -> None:
    module = load_script_module()
    arguments = {
        "card_id": "card-ref",
        "secret": "must-not-appear",
        "client_phone": "+7 999 111-22-33",
    }
    response = tool_result(
        {
            "ok": True,
            "format": "agent_envelope_v2",
            "status": "completed",
            "data": {
                "description": "private CRM body",
                "card_id": "card-ref",
            },
        }
    )

    evidence = module._call_evidence(
        name="agent_entity_context",
        arguments=arguments,
        result=response,
        duration_ms=7,
    )
    serialized = json.dumps(evidence, ensure_ascii=False)

    assert evidence["request_bytes"] > 0
    assert len(evidence["request_sha256"]) == 64
    assert len(evidence["response_sha256"]) == 64
    assert evidence["refs"] == {"card_id": "card-ref"}
    assert "must-not-appear" not in serialized
    assert "+7 999" not in serialized
    assert "private CRM body" not in serialized


def test_entity_mapping_prefers_exact_id_over_relationship_reference() -> None:
    module = load_script_module()
    payload = {
        "data": {
            "card": {
                "id": "card-1",
                "updated_at": "revision-1",
                "repair_order": {
                    "materials": [
                        {
                            "id": "row-1",
                            "card_id": "card-1",
                            "inventory_movement_id": "movement-1",
                        }
                    ]
                },
            }
        }
    }

    mapping = module._mapping_for_entity(payload, "card-1")

    assert mapping is not None
    assert mapping["id"] == "card-1"
    assert mapping["updated_at"] == "revision-1"


def test_state_is_stop_the_line_and_contains_no_business_payload(tmp_path) -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }

    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    path = tmp_path / "state.json"
    module._atomic_json_write(path, state)
    loaded = module._load_state(path)

    assert loaded["status"] == "ready"
    assert loaded["summary"] == {
        "total": 70,
        "passed": 0,
        "pending": 70,
        "blocked": 0,
    }
    assert loaded["data_included"] is False
    assert path.stat().st_mode & 0o777 == 0o600


def test_parser_requires_one_explicit_campaign_action() -> None:
    module = load_script_module()

    inventory = module.build_parser().parse_args(
        ["--run-id", "AST-GWAT-20260728T165722Z", "--inventory"]
    )
    next_case = module.build_parser().parse_args(
        ["--run-id", "AST-GWAT-20260728T165722Z", "--next"]
    )

    assert inventory.inventory is True
    assert next_case.next is True
    assert inventory.apply_synthetic is False


def test_completed_pending_cleanup_is_successful_but_not_verified() -> None:
    module = load_script_module()
    state = {
        "format": module.ATTESTATION_FORMAT,
        "run_id": "AST-GWAT-20260728T165722Z",
        "status": "completed_pending_cleanup",
        "manifest_sha256": "manifest",
        "summary": {"total": 70, "passed": 70, "pending": 0, "blocked": 0},
        "cleanup": {"status": "not_started", "verified": False},
    }

    summary = module._safe_summary(state)

    assert summary["ok"] is True
    assert summary["status"] == "completed_pending_cleanup"
    assert summary["cleanup"]["verified"] is False


def test_safe_summary_omits_cleanup_call_evidence() -> None:
    module = load_script_module()
    state = {
        "format": module.ATTESTATION_FORMAT,
        "run_id": "AST-GWAT-20260728T165722Z",
        "status": "completed",
        "manifest_sha256": "manifest",
        "summary": {"total": 70, "passed": 70, "pending": 0, "blocked": 0},
        "cleanup": {
            "status": "completed",
            "verified": True,
            "entity_counts": {"cards": 13},
            "last_evidence": [{"refs": {"cashbox_id": "private-runtime-ref"}}],
        },
    }

    summary = module._safe_summary(state)

    assert summary["cleanup"] == {
        "status": "completed",
        "verified": True,
        "entity_counts": {"cards": 13},
    }
    assert "private-runtime-ref" not in json.dumps(summary)


def test_cleanup_helpers_require_exact_one_ruble_effect() -> None:
    module = load_script_module()

    assert module._payment_amount_minor({"amount": "1"}) == 100
    assert (
        module._cash_transaction_effect_minor(
            [
                {"direction": "income", "amount_minor": 100},
                {"direction": "income", "amount_minor": 100},
                {"direction": "expense", "amount_minor": 100},
            ]
        )
        == 100
    )


def test_cleanup_orchestrator_persists_terminal_verified_state(tmp_path) -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }
    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    for case in state["cases"]:
        case["status"] = "passed"
    state["summary"] = module._state_summary(state)
    state["status"] = "completed_pending_cleanup"
    state_path = tmp_path / "state.json"
    calls = []

    async def cleanup_payment(_session, *, state, evidence):
        calls.append("payment")
        evidence.append({"tool": "payment", "ok": True})
        return {"removed_effect_minor": 100, "removed_transaction_count": 3}

    async def cleanup_employee(_session, *, state, evidence):
        calls.append("employee")
        evidence.append({"tool": "employee", "ok": True})
        return 1

    async def verify_cleanup(_session, *, state, evidence):
        calls.append("verify")
        evidence.append({"tool": "verify", "ok": True})
        return {
            "cards": 1,
            "clients": 1,
            "inventory_items": 1,
            "files": 1,
            "cashboxes": 1,
            "cash_transactions": 3,
            "employees": 1,
            "shift_accruals": 1,
        }

    module._cleanup_payment_fixture = cleanup_payment
    module._cleanup_employee_fixture = cleanup_employee
    module._verify_global_cleanup = verify_cleanup

    result = asyncio.run(
        module._run_cleanup(
            object(),
            state=state,
            state_path=state_path,
        )
    )
    stored = module._load_state(state_path)

    assert calls == ["payment", "employee", "verify"]
    assert result["status"] == "completed"
    assert stored["cleanup"]["status"] == "completed"
    assert stored["cleanup"]["verified"] is True
    assert stored["cleanup"]["payment"]["removed_effect_minor"] == 100
    assert stored["cleanup"]["removed_shift_accruals"] == 1


def test_employee_snapshot_matches_backend_active_then_name_order() -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }
    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    item = module._find_case(
        state,
        "operation:finance:create_employee_salary_transaction",
    )
    item["attempts"] = 1

    async def raw_invoke(*_args, **_kwargs):
        return {
            "data": {
                "employees": [
                    {
                        "id": "inactive-a",
                        "name": "Алексей",
                        "updated_at": "r3",
                        "is_active": False,
                    },
                    {
                        "id": "active-b",
                        "name": "Борис",
                        "updated_at": "r2",
                        "is_active": True,
                    },
                    {
                        "id": "active-a",
                        "name": "Алексей",
                        "updated_at": "r1",
                        "is_active": True,
                    },
                ]
            }
        }

    module._raw_invoke = raw_invoke
    employees = asyncio.run(
        module._finance_employee_snapshot(
            object(),
            spec=module._case_spec(item),
            state=state,
            purpose="test",
            evidence=[],
        )
    )

    assert [item["id"] for item in employees] == ["active-a", "active-b", "inactive-a"]


def test_error_codes_are_allowlisted_before_persistence() -> None:
    module = load_script_module()

    safe = module._result_error_code(
        {"ok": False, "warnings": ["expected_revision_required_reread_exact_card_first"]}
    )
    unsafe = module._result_error_code(
        {"ok": False, "warnings": ["backend leaked customer +7 999 111-22-33"]}
    )

    assert safe == "expected_revision_required_reread_exact_card_first"
    assert unsafe == "remote_error"


def test_expected_call_failure_carries_safe_evidence() -> None:
    module = load_script_module()

    class Session:
        async def call_tool(self, _name, _arguments):
            return tool_result(
                {
                    "ok": False,
                    "status": "blocked",
                    "warnings": ["expected_revision_conflict"],
                    "data": {"private": "must-not-persist"},
                },
                is_error=True,
            )

    try:
        asyncio.run(module._attested_call(Session(), "workflow_checkpoint", {}))
    except module.AttestationError as exc:
        assert exc.code == "workflow_checkpoint_expected_revision_conflict"
        assert len(exc.evidence) == 1
        serialized = json.dumps(exc.evidence)
        assert "must-not-persist" not in serialized
        assert exc.evidence[0]["error_code"] == "expected_revision_conflict"
    else:
        raise AssertionError("failed tool call must stop the attestation")


def test_read_operation_retry_uses_new_attempt_key_and_proves_deduplication() -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }
    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    item = module._find_case(state, "operation:board:audit_client_links")
    item["attempts"] = 3
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, dict(arguments)))
            if not arguments["idempotency_key"]:
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "warnings": ["idempotency_key_required"],
                    },
                    is_error=True,
                )
            if len(calls) == 2:
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "summary": {"deduplicated": False},
                        "verification": {"passed": True},
                    }
                )
            return tool_result(
                {
                    "ok": True,
                    "status": "completed",
                    "summary": {"deduplicated": True},
                    "verification": {
                        "idempotency_reused": True,
                        "prior_terminal_state": True,
                    },
                    "warnings": ["idempotency_reused_completed_result"],
                }
            )

    spec = module._case_spec(item)
    evidence = asyncio.run(
        module._operation_case(
            Session(),
            spec=spec,
            state=state,
            apply_synthetic=False,
        )
    )

    assert len(calls) == 3
    assert calls[0][1]["idempotency_key"] == ""
    assert calls[1][1]["idempotency_key"].endswith("-read-a3")
    assert calls[2][1]["idempotency_key"] == calls[1][1]["idempotency_key"]
    assert len(evidence) == 3
    assert evidence[0]["error_code"] == "idempotency_key_required"


def test_board_write_executor_scopes_apply_and_archives_fixture() -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }
    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    item = module._find_case(state, "operation:board:bulk_set_deadline_if_below")
    item["attempts"] = 1
    card = {
        "id": "card-1",
        "title": "fixture",
        "updated_at": "2026-07-28T18:00:00+00:00",
        "remaining_seconds": 60,
        "archived": False,
    }
    calls = []
    applied_keys = set()

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, dict(arguments)))
            if name == "discover_raw_capabilities":
                raw_name = arguments["query"]
                risk = "destructive" if raw_name == "archive_card" else "write"
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "capabilities": [
                                {
                                    "name": raw_name,
                                    "risk": risk,
                                    "schema_hash": "hash-1",
                                }
                            ]
                        },
                    }
                )
            if name == "get_raw_capability_schema":
                return tool_result(
                    {
                        "ok": True,
                        "summary": {
                            "name": arguments["name"],
                            "schema_hash": "hash-1",
                        },
                        "data": {"input_schema": {}},
                    }
                )
            if name == "call_raw_capability":
                raw_name = arguments["name"]
                if raw_name == "archive_card":
                    card["archived"] = True
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "data": {"card": dict(card)},
                        "verification": {
                            "schema_hash_verified": True,
                            "executor_ok": True,
                            "ledger_closed": True,
                        },
                    }
                )
            if name == "agent_entity_context":
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "data": {"card": dict(card)},
                    }
                )
            if name == "agent_board_workflow":
                key = arguments["idempotency_key"]
                if not key:
                    return tool_result(
                        {
                            "ok": False,
                            "status": "failed",
                            "warnings": ["idempotency_key_required"],
                        },
                        is_error=True,
                    )
                payload = arguments["payload"]
                if payload.get("expected_updated_at_by_card_id") == {
                    "card-1": "2000-01-01T00:00:00+00:00"
                }:
                    return tool_result(
                        {
                            "ok": False,
                            "status": "failed",
                            "warnings": ["card_update_conflict"],
                        },
                        is_error=True,
                    )
                if arguments["mode"] == "apply" and key not in applied_keys:
                    applied_keys.add(key)
                    card["remaining_seconds"] = 600
                    card["updated_at"] = "2026-07-28T18:01:00+00:00"
                if key in applied_keys and arguments["mode"] == "apply":
                    repeat_count = sum(
                        1
                        for call_name, call_arguments in calls
                        if call_name == name and call_arguments.get("idempotency_key") == key
                    )
                    if repeat_count > 1:
                        return tool_result(
                            {
                                "ok": True,
                                "status": "completed",
                                "summary": {"deduplicated": True},
                                "verification": {
                                    "idempotency_reused": True,
                                    "prior_terminal_state": True,
                                },
                            }
                        )
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "summary": {"deduplicated": False},
                        "verification": {"passed": True},
                    }
                )
            raise AssertionError(f"unexpected tool: {name}")

    spec = module._case_spec(item)
    evidence = asyncio.run(
        module._board_write_case(
            Session(),
            spec=spec,
            state=state,
        )
    )

    board_calls = [arguments for name, arguments in calls if name == "agent_board_workflow"]
    assert len(board_calls) == 5
    assert board_calls[3]["mode"] == "apply"
    assert board_calls[3]["payload"]["card_ids"] == ["card-1"]
    assert board_calls[3]["payload"]["expected_updated_at_by_card_id"] == {
        "card-1": "2026-07-28T18:00:00+00:00"
    }
    assert state["synthetic_entities"]["cards"] == [
        {
            "id": "card-1",
            "case_id": "operation:board:bulk_set_deadline_if_below",
            "status": "archived",
        }
    ]
    assert len(evidence) > len(board_calls)


def test_inventory_save_executor_proves_conflict_replay_and_exact_reread() -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }
    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    item_case = module._find_case(state, "operation:inventory:save_inventory_item")
    item_case["attempts"] = 1
    inventory_item = {
        "id": "inventory-1",
        "name": "",
        "catalog_number": "",
        "unit": "шт",
        "quantity": "0",
        "cost_price": "0",
        "sale_price": "0",
        "updated_at": "2026-07-28T18:00:00+00:00",
    }
    applied_keys: set[str] = set()
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, dict(arguments)))
            assert name == "agent_inventory_workflow"
            operation = arguments["operation"]
            key = arguments["idempotency_key"]
            if not key:
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "warnings": ["idempotency_key_required"],
                    },
                    is_error=True,
                )
            if operation == "get_inventory_item":
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "data": {"item": dict(inventory_item), "movements": []},
                    }
                )
            payload = arguments["payload"]
            if not payload.get("name"):
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "error": {"code": "validation_error"},
                    },
                    is_error=True,
                )
            if payload.get("expected_updated_at") == "2000-01-01T00:00:00+00:00":
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "data": {"error": {"code": "inventory_item_update_conflict"}},
                        "warnings": ["executor_failed"],
                    },
                    is_error=True,
                )
            if key in applied_keys:
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "summary": {"deduplicated": True},
                        "verification": {
                            "idempotency_reused": True,
                            "prior_terminal_state": True,
                        },
                    }
                )
            applied_keys.add(key)
            inventory_item.update(
                {
                    "name": payload["name"],
                    "catalog_number": payload["catalog_number"],
                    "updated_at": "2026-07-28T18:01:00+00:00",
                }
            )
            return tool_result(
                {
                    "ok": True,
                    "status": "completed",
                    "data": {"item": dict(inventory_item)},
                    "summary": {"deduplicated": False},
                    "verification": {"passed": True},
                }
            )

    evidence = asyncio.run(
        module._inventory_save_case(
            Session(),
            spec=module._case_spec(item_case),
            state=state,
        )
    )

    assert state["refs"]["synthetic_inventory_item_id"] == "inventory-1"
    assert state["synthetic_entities"]["inventory_items"] == [
        {
            "id": "inventory-1",
            "case_id": "operation:inventory:save_inventory_item",
            "status": "active",
        }
    ]
    assert any(
        item.get("expected_error_code") == "inventory_item_update_conflict"
        for item in evidence
    )
    assert len(evidence) == 7


def test_document_delete_executor_proves_conflict_replay_and_absence() -> None:
    module = load_script_module()
    manifest = {
        "format": module.ATTESTATION_MANIFEST_FORMAT,
        "tools": [],
        "operations": {},
        "case_ids": [],
    }
    state = module._new_state(
        run_id="AST-GWAT-20260728T165722Z",
        mcp_url="https://crm.autostopcrm.ru/mcp",
        manifest=manifest,
    )
    case = module._find_case(state, "operation:document:delete_shared_file")
    case["attempts"] = 1
    state["refs"]["synthetic_file_id"] = "file-1"
    module._synthetic_entities(state)["files"] = [
        {
            "id": "file-1",
            "case_id": "operation:document:upload_shared_file",
            "status": "active",
        }
    ]
    file_item = {
        "id": "file-1",
        "original_name": "fixture.pdf",
        "updated_at": "2026-07-28T18:00:00+00:00",
        "size_bytes": 42,
        "exists_on_disk": True,
    }
    exists = True
    applied_keys: set[str] = set()
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            nonlocal exists
            calls.append((name, dict(arguments)))
            if name == "agent_entity_context":
                if exists:
                    return tool_result(
                        {
                            "ok": True,
                            "status": "completed",
                            "data": {"file": dict(file_item)},
                        }
                    )
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "warnings": ["not_found"],
                    },
                    is_error=True,
                )
            assert name == "agent_document_workflow"
            key = arguments["idempotency_key"]
            if not key:
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "warnings": ["idempotency_key_required"],
                    },
                    is_error=True,
                )
            payload = arguments["payload"]
            if payload["file_id"] != "file-1":
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "warnings": ["not_found"],
                    },
                    is_error=True,
                )
            if payload["expected_updated_at"] != file_item["updated_at"]:
                return tool_result(
                    {
                        "ok": False,
                        "status": "failed",
                        "warnings": ["shared_file_update_conflict"],
                    },
                    is_error=True,
                )
            if key in applied_keys:
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "summary": {"deduplicated": True},
                        "verification": {
                            "idempotency_reused": True,
                            "prior_terminal_state": True,
                        },
                    }
                )
            applied_keys.add(key)
            exists = False
            return tool_result(
                {
                    "ok": True,
                    "status": "completed",
                    "data": {"deleted": True, "file_id": "file-1"},
                    "summary": {"deduplicated": False},
                    "verification": {"passed": True},
                }
            )

    evidence = asyncio.run(
        module._document_delete_case(
            Session(),
            spec=module._case_spec(case),
            state=state,
        )
    )

    assert state["refs"]["synthetic_file_id"] == ""
    assert state["synthetic_entities"]["files"][0]["status"] == "deleted"
    assert any(
        item.get("expected_error_code") == "shared_file_update_conflict"
        for item in evidence
    )
    assert evidence[-1]["error_code"] == "not_found"
    assert len(calls) == 8


def test_frozen_manifest_check_stops_on_public_schema_drift() -> None:
    module = load_script_module()
    manifest = {
        "tools": [
            {
                "name": "ping_connector",
                "schema_sha256": module._sha256({"type": "object"}),
                "annotations_sha256": module._sha256({}),
                "request_schema_bytes": len(module._canonical_json({"type": "object"})),
            }
        ],
        "manager_raw_crm_capabilities": [],
    }

    class Session:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="ping_connector",
                        inputSchema={"type": "object", "properties": {}},
                        annotations=None,
                    )
                ]
            )

    try:
        asyncio.run(module._assert_frozen_manifest_live(Session(), manifest))
    except module.AttestationError as exc:
        assert exc.code == "live_public_tool_schema_drift_ping_connector"
        assert exc.classification == "schema"
    else:
        raise AssertionError("schema drift must stop the campaign")
