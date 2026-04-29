from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..mcp.client import BoardApiClient
from ..services.snapshot_service import GPT_WALL_AGENT_EVENT_LIMIT
from .models import DownloadedAttachment


class CRMToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class CRMToolDefinition:
    name: str
    description: str
    args_schema: dict[str, Any]
    write: bool = False
    min_role: str = "owner"

    def for_model(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_schema,
            "write": self.write,
            "min_role": self.min_role,
        }


class CRMToolRegistry:
    def __init__(
        self,
        board_api: BoardApiClient,
        *,
        actor_name: str = "TELEGRAM_AI",
        max_batch_cards: int = 20,
        image_analyzer: Callable[..., dict[str, Any]] | None = None,
        internet_searcher: Callable[..., str] | None = None,
    ) -> None:
        self._board_api = board_api
        self._actor_name = actor_name
        self._max_batch_cards = max(1, int(max_batch_cards))
        self._image_analyzer = image_analyzer
        self._internet_searcher = internet_searcher
        self._run_media: list[DownloadedAttachment] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "get_board_snapshot": self._get_board_snapshot,
            "get_board_context": self._get_board_context,
            "get_board_content": self._get_board_content,
            "get_board_events": self._get_board_events,
            "review_board": self._review_board,
            "get_gpt_wall": self._get_gpt_wall,
            "get_cards": self._get_cards,
            "get_card": self._get_card,
            "search_cards": self._search_cards,
            "get_card_context": self._get_card_context,
            "get_card_log": self._get_card_log,
            "list_card_attachments": self._list_card_attachments,
            "get_card_attachment": self._get_card_attachment,
            "read_card_attachment": self._read_card_attachment,
            "analyze_card_image_attachment": self._analyze_card_image_attachment,
            "list_columns": self._list_columns,
            "list_archived_cards": self._list_archived_cards,
            "list_repair_orders": self._list_repair_orders,
            "get_repair_order_text": self._get_repair_order_text,
            "list_clients": self._list_clients,
            "search_clients": self._search_clients,
            "get_client": self._get_client_profile,
            "link_card_to_client": self._link_card_to_client,
            "upsert_client_vehicle": self._upsert_client_vehicle,
            "delete_client_vehicle": self._delete_client_vehicle,
            "list_cashboxes": self._list_cashboxes,
            "get_cashbox": self._get_cashbox,
            "create_card": self._create_card,
            "update_card": self._update_card,
            "move_card": self._move_card,
            "mark_card_ready": self._mark_card_ready,
            "bulk_move_cards": self._bulk_move_cards,
            "archive_card": self._archive_card,
            "restore_card": self._restore_card,
            "cleanup_card_content": self._cleanup_card_content,
            "attach_telegram_photo_to_card": self._attach_telegram_photo_to_card,
            "create_column": self._create_column,
            "rename_column": self._rename_column,
            "create_sticky": self._create_sticky,
            "update_sticky": self._update_sticky,
            "move_sticky": self._move_sticky,
            "create_cashbox": self._create_cashbox,
            "create_cash_transaction": self._create_cash_transaction,
            "update_board_settings": self._update_board_settings,
            "set_card_deadline": self._set_card_deadline,
            "set_card_indicator": self._set_card_indicator,
            "list_overdue_cards": self._list_overdue_cards,
            "get_repair_order": self._get_repair_order,
            "update_repair_order": self._update_repair_order,
            "replace_repair_order_works": self._replace_repair_order_works,
            "replace_repair_order_materials": self._replace_repair_order_materials,
            "set_repair_order_status": self._set_repair_order_status,
        }

    @property
    def definitions(self) -> list[CRMToolDefinition]:
        return [
            CRMToolDefinition(
                "get_board_snapshot", "Read compact board snapshot.", {"compact": "optional bool"}
            ),
            CRMToolDefinition("get_board_context", "Read board schema and counters.", {}),
            CRMToolDefinition(
                "get_board_content",
                "Read board machine wall markdown.",
                {"include_archived": "optional bool", "view_mode": "optional string"},
            ),
            CRMToolDefinition(
                "get_board_events",
                "Read board event log.",
                {"event_limit": "optional int", "include_archived": "optional bool"},
            ),
            CRMToolDefinition(
                "review_board",
                "Read operational board review with alerts and priority cards.",
                {
                    "stale_hours": "optional int",
                    "overload_threshold": "optional int",
                    "priority_limit": "optional int",
                    "recent_event_limit": "optional int",
                },
            ),
            CRMToolDefinition(
                "get_gpt_wall",
                "Read combined board content and event log. Use only when a broad dump is needed; the agent path keeps this compact.",
                {"include_archived": "optional bool", "event_limit": "optional int"},
            ),
            *(
                [
                    CRMToolDefinition(
                        "internet_search",
                        (
                            "Search external web sources for parts, prices, official sites, "
                            "sources, and other non-CRM research."
                        ),
                        {"query": "required string"},
                    )
                ]
                if self._internet_searcher is not None
                else []
            ),
            CRMToolDefinition(
                "get_cards",
                "Read cards list.",
                {"include_archived": "optional bool", "compact": "optional bool"},
            ),
            CRMToolDefinition(
                "get_card", "Read one full card by id.", {"card_id": "required string"}
            ),
            CRMToolDefinition(
                "search_cards",
                "Search cards by query.",
                {
                    "query": "required string",
                    "limit": "optional int",
                    "include_archived": "optional bool",
                    "column": "optional string",
                    "tag": "optional string",
                    "indicator": "optional string",
                    "status": "optional string",
                },
            ),
            CRMToolDefinition(
                "get_card_context", "Read one focused card context.", {"card_id": "required string"}
            ),
            CRMToolDefinition(
                "get_card_log",
                "Read one card audit log.",
                {"card_id": "required string", "limit": "optional int"},
            ),
            CRMToolDefinition(
                "list_card_attachments",
                "List card attachments without file bytes.",
                {"card_id": "required string", "include_removed": "optional bool"},
            ),
            CRMToolDefinition(
                "get_card_attachment",
                "Read attachment metadata without file bytes.",
                {"card_id": "required string", "attachment_id": "required string"},
            ),
            CRMToolDefinition(
                "read_card_attachment",
                "Read bounded attachment content. Images return metadata unless include_base64 is true.",
                {
                    "card_id": "required string",
                    "attachment_id": "required string",
                    "mode": "optional string",
                    "include_base64": "optional bool",
                    "max_chars": "optional int",
                },
            ),
            CRMToolDefinition(
                "analyze_card_image_attachment",
                "Read an image attachment from a card and analyze it with vision.",
                {
                    "card_id": "required string",
                    "attachment_id": "required string",
                    "caption": "optional string",
                },
            ),
            CRMToolDefinition(
                "list_overdue_cards", "List overdue cards.", {"include_archived": "optional bool"}
            ),
            CRMToolDefinition(
                "list_columns",
                "List CRM board columns.",
                {},
            ),
            CRMToolDefinition(
                "list_archived_cards",
                "List archived cards.",
                {"limit": "optional int", "compact": "optional bool"},
            ),
            CRMToolDefinition(
                "list_repair_orders",
                "List repair orders.",
                {
                    "limit": "optional int",
                    "status": "optional string",
                    "query": "optional string",
                    "sort_by": "optional string",
                    "sort_dir": "optional string",
                },
            ),
            CRMToolDefinition(
                "get_repair_order", "Read repair order by card id.", {"card_id": "required string"}
            ),
            CRMToolDefinition(
                "get_repair_order_text",
                "Read printable repair order text by card id.",
                {"card_id": "required string"},
            ),
            CRMToolDefinition(
                "list_clients",
                "Read compact clients list.",
                {"limit": "optional int", "include_stats": "optional bool"},
            ),
            CRMToolDefinition(
                "search_clients",
                "Search clients by name, phone, INN, vehicle, VIN, or license plate. Use before creating or linking clients; choose vehicles_preview[].id when a concrete car is known.",
                {"query": "required string", "limit": "optional int"},
            ),
            CRMToolDefinition(
                "get_client",
                "Read one client profile with all vehicles and recent repair orders.",
                {"client_id": "required string", "order_limit": "optional int"},
            ),
            CRMToolDefinition(
                "link_card_to_client",
                "Link a card to a client and optionally a concrete client vehicle. If the operator identifies a known car, pass client_vehicle_id; if it is a new car for that client, use create_vehicle_from_card=true.",
                {
                    "card_id": "required string",
                    "client_id": "required string",
                    "client_vehicle_id": "optional string",
                    "create_vehicle_from_card": "optional bool",
                    "sync_vehicle_fields": "optional bool",
                    "sync_fields": "optional bool",
                    "overwrite_card_fields": "optional bool",
                },
                write=True,
            ),
            CRMToolDefinition(
                "upsert_client_vehicle",
                "Create or update a vehicle inside an existing client profile. Use it to fix model, VIN, or license plate; linked card passports are synced by default.",
                {
                    "client_id": "required string",
                    "client_vehicle_id": "optional string",
                    "card_id": "optional string",
                    "vehicle": "optional object",
                    "sync_linked_cards": "optional bool",
                },
                write=True,
            ),
            CRMToolDefinition(
                "delete_client_vehicle",
                "Delete a vehicle from a client profile without deleting cards or repair orders.",
                {
                    "client_id": "required string",
                    "client_vehicle_id": "required string",
                    "unlink_cards": "optional bool",
                },
                write=True,
            ),
            CRMToolDefinition(
                "list_cashboxes",
                "List cashboxes and balances.",
                {"limit": "optional int"},
            ),
            CRMToolDefinition(
                "get_cashbox",
                "Read cashbox details and transactions.",
                {"cashbox_id": "required string", "transaction_limit": "optional int"},
            ),
            CRMToolDefinition(
                "create_card",
                "Create CRM card.",
                {
                    "title": "required string",
                    "vehicle": "optional string",
                    "description": "optional string",
                    "column": "optional string",
                    "tags": "optional array",
                    "deadline": "optional object",
                    "vehicle_profile": "optional object",
                },
                write=True,
            ),
            CRMToolDefinition(
                "update_card",
                "Update CRM card fields.",
                {
                    "card_id": "required string",
                    "title": "optional string",
                    "vehicle": "optional string",
                    "description": "optional string",
                    "tags": "optional array",
                    "deadline": "optional object",
                    "vehicle_profile": "optional object",
                },
                write=True,
            ),
            CRMToolDefinition(
                "move_card",
                "Move CRM card to a column.",
                {
                    "card_id": "required string",
                    "column": "required string",
                    "before_card_id": "optional string",
                },
                write=True,
            ),
            CRMToolDefinition(
                "mark_card_ready",
                "Mark a vehicle card as ready: move it to 'Готовые автомобили', add the ready tag, and move the repair order to the ready list. Use this when the operator says the car is ready; do not close the repair order unless explicitly requested.",
                {"card_id": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "bulk_move_cards",
                "Move several cards to one column.",
                {"card_ids": "required array", "column": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "archive_card",
                "Archive CRM card; never hard delete.",
                {"card_id": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "restore_card",
                "Restore archived CRM card.",
                {"card_id": "required string", "column": "optional string"},
                write=True,
            ),
            CRMToolDefinition(
                "cleanup_card_content",
                "Normalize one card content through CRM service cleanup.",
                {"card_id": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "attach_telegram_photo_to_card",
                "Attach a photo from the current Telegram message to a CRM card.",
                {
                    "card_id": "required string",
                    "media_index": "optional int, default 0",
                    "file_name": "optional string",
                },
                write=True,
            ),
            CRMToolDefinition(
                "create_column",
                "Create CRM board column.",
                {"label": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "rename_column",
                "Rename CRM board column.",
                {"column_id": "required string", "label": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "create_sticky",
                "Create board sticky note.",
                {
                    "text": "required string",
                    "x": "optional int",
                    "y": "optional int",
                    "deadline": "required object",
                },
                write=True,
            ),
            CRMToolDefinition(
                "update_sticky",
                "Update sticky text or deadline.",
                {
                    "sticky_id": "required string",
                    "text": "optional string",
                    "deadline": "optional object",
                },
                write=True,
            ),
            CRMToolDefinition(
                "move_sticky",
                "Move sticky note on board.",
                {"sticky_id": "required string", "x": "required int", "y": "required int"},
                write=True,
            ),
            CRMToolDefinition(
                "create_cashbox",
                "Create cashbox.",
                {"name": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "create_cash_transaction",
                "Create cashbox income or expense transaction.",
                {
                    "cashbox_id": "required string",
                    "direction": "required string income|expense",
                    "amount_minor": "optional int",
                    "amount": "optional string|number",
                    "note": "optional string",
                },
                write=True,
            ),
            CRMToolDefinition(
                "update_board_settings",
                "Update board settings. Currently supports board_scale.",
                {"board_scale": "required number"},
                write=True,
            ),
            CRMToolDefinition(
                "set_card_deadline",
                "Set card deadline.",
                {"card_id": "required string", "deadline": "required object"},
                write=True,
            ),
            CRMToolDefinition(
                "set_card_indicator",
                "Set card indicator.",
                {"card_id": "required string", "indicator": "required string"},
                write=True,
            ),
            CRMToolDefinition(
                "update_repair_order",
                "Update full repair order object.",
                {"card_id": "required string", "repair_order": "required object"},
                write=True,
            ),
            CRMToolDefinition(
                "replace_repair_order_works",
                "Replace repair order works rows.",
                {"card_id": "required string", "rows": "required array"},
                write=True,
            ),
            CRMToolDefinition(
                "replace_repair_order_materials",
                "Replace repair order material rows.",
                {"card_id": "required string", "rows": "required array"},
                write=True,
            ),
            CRMToolDefinition(
                "set_repair_order_status",
                "Set repair order status.",
                {"card_id": "required string", "status": "required string"},
                write=True,
            ),
        ]

    def catalog_for_model(self) -> list[dict[str, Any]]:
        return [definition.for_model() for definition in self.definitions]

    def set_run_media(self, media: list[DownloadedAttachment]) -> None:
        self._run_media = list(media or [])

    def clear_run_media(self) -> None:
        self._run_media = []

    def execute(self, action: dict[str, Any], *, role: str) -> dict[str, Any]:
        tool_name = str(action.get("tool") or "").strip()
        arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        if tool_name == "internet_search":
            if self._internet_searcher is None:
                raise CRMToolError("Internet search tool is not configured.")
            query = str(
                arguments.get("query")
                or arguments.get("command_text")
                or arguments.get("text")
                or ""
            ).strip()
            if not query:
                raise CRMToolError("Internet search query is missing.")
            result_text = self._internet_searcher(command_text=query, role=role)
            answer = str(result_text or "").strip()
            if not answer:
                raise CRMToolError("Internet search returned empty text.")
            result = {"ok": True, "data": {"answer": answer, "query": query}}
            return {
                "tool": tool_name,
                "arguments": {"query": query},
                "before": {},
                "result": result,
                "verify": {"passed": True, "message": "internet search completed"},
            }
        definition = self._definition(tool_name)
        if definition is None or tool_name not in self._handlers:
            raise CRMToolError(f"Unknown CRM tool: {tool_name}")
        if definition.write and role != "owner":
            raise CRMToolError(f"Role {role} cannot execute write tool {tool_name}.")
        self._validate_batch(tool_name, arguments)
        before = self._before_snapshot(tool_name, arguments) if definition.write else {}
        result = self._handlers[tool_name](dict(arguments))
        if not _api_ok(result):
            raise CRMToolError(_api_error_message(result, default=f"CRM tool failed: {tool_name}"))
        verify = self.verify(tool_name, arguments, result) if definition.write else {"passed": True}
        if definition.write and not verify.get("passed"):
            raise CRMToolError(
                f"CRM write verification failed for {tool_name}: {verify.get('message') or ''}"
            )
        return {
            "tool": tool_name,
            "arguments": arguments,
            "before": before,
            "result": result,
            "verify": verify,
        }

    def rollback_tool_result(self, tool_result: dict[str, Any], *, role: str) -> dict[str, Any]:
        if role != "owner":
            raise CRMToolError(f"Role {role} cannot rollback CRM writes.")
        tool_name = str(tool_result.get("tool") or "").strip()
        before = tool_result.get("before") if isinstance(tool_result.get("before"), dict) else {}
        result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
        if tool_name == "create_card":
            card_id = str(_api_data(result).get("card", {}).get("id") or "")
            if not card_id:
                raise CRMToolError("Cannot rollback create_card without created card id.")
            rollback_result = self._board_api.archive_card(
                card_id=card_id, actor_name=self._actor_name
            )
            return {"tool": "rollback_create_card", "result": rollback_result}
        if tool_name == "move_card":
            card = _api_data(before).get("card", {})
            card_id = str(card.get("id") or "")
            column = str(card.get("column") or "")
            if not card_id or not column:
                raise CRMToolError("Cannot rollback move_card without before column.")
            rollback_result = self._board_api.move_card(
                card_id=card_id,
                column=column,
                actor_name=self._actor_name,
            )
            return {"tool": "rollback_move_card", "result": rollback_result}
        if tool_name == "archive_card":
            card = _api_data(before).get("card", {})
            card_id = str(card.get("id") or "")
            column = str(card.get("column") or "")
            if not card_id:
                raise CRMToolError("Cannot rollback archive_card without before card id.")
            rollback_result = self._board_api.restore_card(
                card_id=card_id,
                column=column or None,
                actor_name=self._actor_name,
            )
            return {"tool": "rollback_archive_card", "result": rollback_result}
        if tool_name == "attach_telegram_photo_to_card":
            data = _api_data(result)
            attachment = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
            arguments = (
                tool_result.get("arguments")
                if isinstance(tool_result.get("arguments"), dict)
                else {}
            )
            card_id = str(attachment.get("card_id") or arguments.get("card_id") or "")
            attachment_id = str(attachment.get("id") or "")
            if not card_id or not attachment_id:
                raise CRMToolError("Cannot rollback attachment without card and attachment id.")
            rollback_result = self._board_api.remove_card_attachment(
                card_id=card_id,
                attachment_id=attachment_id,
                actor_name=self._actor_name,
            )
            return {"tool": "rollback_attach_telegram_photo", "result": rollback_result}
        if tool_name in {"update_card", "set_card_deadline", "set_card_indicator"}:
            card = _api_data(before).get("card", {})
            card_id = str(card.get("id") or "")
            if not card_id:
                raise CRMToolError("Cannot rollback update_card without before card.")
            rollback_result = self._board_api.update_card(
                card_id=card_id,
                title=str(card.get("title") or ""),
                vehicle=str(card.get("vehicle") or ""),
                description=str(card.get("description") or ""),
                tags=card.get("tags") if isinstance(card.get("tags"), list) else None,
                vehicle_profile=card.get("vehicle_profile")
                if isinstance(card.get("vehicle_profile"), dict)
                else None,
                actor_name=self._actor_name,
            )
            return {"tool": "rollback_update_card", "result": rollback_result}
        if tool_name in {
            "update_repair_order",
            "replace_repair_order_works",
            "replace_repair_order_materials",
            "set_repair_order_status",
        }:
            data = _api_data(before)
            repair_order = (
                data.get("repair_order") if isinstance(data.get("repair_order"), dict) else {}
            )
            card = data.get("card") if isinstance(data.get("card"), dict) else {}
            card_id = str(card.get("id") or repair_order.get("card_id") or "")
            if not card_id or not repair_order:
                raise CRMToolError("Cannot rollback repair order without before snapshot.")
            rollback_result = self._board_api.update_repair_order(
                card_id=card_id,
                repair_order=repair_order,
                actor_name=self._actor_name,
            )
            return {"tool": "rollback_repair_order", "result": rollback_result}
        raise CRMToolError(f"Rollback is not supported for {tool_name}.")

    def verify(
        self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return self._verify(tool_name, arguments, result)
        except Exception as exc:  # pragma: no cover - defensive verifier path
            return {"passed": False, "message": str(exc)}

    def _verify(
        self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_name == "create_card":
            card = _api_data(result).get("card", {})
            card_id = str(card.get("id") or "")
            if not card_id:
                return {"passed": False, "message": "created card id is missing"}
            return self._verify_card_exists(card_id)
        if tool_name in {"update_card", "set_card_deadline", "set_card_indicator"}:
            return self._verify_card_exists(str(arguments.get("card_id") or ""))
        if tool_name == "bulk_move_cards":
            card_ids = (
                arguments.get("card_ids") if isinstance(arguments.get("card_ids"), list) else []
            )
            expected_column = str(arguments.get("column") or "")
            payloads = [self._read_card(str(card_id)) for card_id in card_ids]
            cards = [_api_data(payload).get("card", {}) for payload in payloads]
            return {
                "passed": bool(cards)
                and all(str(card.get("column") or "") == expected_column for card in cards),
                "message": "all cards matched column",
            }
        if tool_name == "move_card":
            card_payload = self._read_card(str(arguments.get("card_id") or ""))
            card = _api_data(card_payload).get("card", {})
            expected_column = str(arguments.get("column") or "")
            return {
                "passed": bool(card) and str(card.get("column") or "") == expected_column,
                "message": "column matched"
                if str(card.get("column") or "") == expected_column
                else "column mismatch",
            }
        if tool_name == "archive_card":
            card_payload = self._read_card(str(arguments.get("card_id") or ""))
            card = _api_data(card_payload).get("card", {})
            return {"passed": bool(card.get("archived")), "message": "archived flag checked"}
        if tool_name in {"restore_card", "cleanup_card_content"}:
            return self._verify_card_exists(str(arguments.get("card_id") or ""))
        if tool_name == "attach_telegram_photo_to_card":
            data = _api_data(result)
            attachment = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
            attachment_id = str(attachment.get("id") or "")
            card_id = str(arguments.get("card_id") or "")
            if not attachment_id or not card_id:
                return {"passed": False, "message": "attachment id is missing"}
            payload = self._board_api.list_card_attachments(card_id, include_removed=False)
            if not _api_ok(payload):
                return {"passed": False, "message": "attachment list read-back failed"}
            attachments = _api_data(payload).get("attachments")
            if not isinstance(attachments, list):
                return {"passed": False, "message": "attachment list is invalid"}
            matched = next(
                (
                    item
                    for item in attachments
                    if isinstance(item, dict)
                    and str(item.get("id") or "") == attachment_id
                    and not bool(item.get("removed"))
                ),
                None,
            )
            if matched is None:
                return {"passed": False, "message": "attachment not found in card listing"}
            return {"passed": True, "message": "attachment listed on card"}
        if tool_name in {
            "update_repair_order",
            "replace_repair_order_works",
            "replace_repair_order_materials",
            "set_repair_order_status",
        }:
            payload = self._board_api.get_repair_order(str(arguments.get("card_id") or ""))
            return {"passed": _api_ok(payload), "message": "repair order read-back checked"}
        if tool_name in {
            "create_column",
            "rename_column",
            "create_sticky",
            "update_sticky",
            "move_sticky",
            "create_cashbox",
            "create_cash_transaction",
            "update_board_settings",
        }:
            return {"passed": _api_ok(result), "message": "CRM API accepted write"}
        return {"passed": True, "message": "no specific verifier"}

    def _verify_card_exists(self, card_id: str) -> dict[str, Any]:
        payload = self._read_card(card_id)
        card = _api_data(payload).get("card", {})
        return {"passed": bool(card.get("id")), "message": "card read-back checked"}

    def _before_snapshot(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "create_card":
            return {}
        card_id = str(arguments.get("card_id") or "")
        if tool_name in {
            "update_repair_order",
            "replace_repair_order_works",
            "replace_repair_order_materials",
            "set_repair_order_status",
        }:
            return self._board_api.get_repair_order(card_id) if card_id else {}
        return self._read_card(card_id) if card_id else {}

    def _read_card(self, card_id: str) -> dict[str, Any]:
        if not card_id:
            return {"ok": False, "error": {"message": "card_id is missing"}}
        return self._board_api.get_card(card_id)

    def _definition(self, tool_name: str) -> CRMToolDefinition | None:
        for definition in self.definitions:
            if definition.name == tool_name:
                return definition
        return None

    def _validate_batch(self, tool_name: str, arguments: dict[str, Any]) -> None:
        if tool_name == "move_card":
            card_ids = arguments.get("card_ids")
        elif tool_name == "bulk_move_cards":
            card_ids = arguments.get("card_ids")
        else:
            return
        if isinstance(card_ids, list) and len(card_ids) > self._max_batch_cards:
            raise CRMToolError(
                f"Batch card limit exceeded: {len(card_ids)} > {self._max_batch_cards}"
            )

    def _get_board_snapshot(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_board_snapshot(compact=bool(arguments.get("compact", True)))

    def _get_board_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_board_context()

    def _get_board_content(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_board_content(
            include_archived=bool(arguments.get("include_archived", True)),
            view_mode=str(arguments.get("view_mode") or "agent"),
        )

    def _get_board_events(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_board_events(
            event_limit=int(arguments.get("event_limit") or 100),
            include_archived=bool(arguments.get("include_archived", True)),
        )

    def _review_board(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.review_board(
            stale_hours=_optional_int(arguments, "stale_hours"),
            overload_threshold=_optional_int(arguments, "overload_threshold"),
            priority_limit=_optional_int(arguments, "priority_limit"),
            recent_event_limit=_optional_int(arguments, "recent_event_limit"),
        )

    def _get_gpt_wall(self, arguments: dict[str, Any]) -> dict[str, Any]:
        event_limit = int(arguments.get("event_limit") or GPT_WALL_AGENT_EVENT_LIMIT)
        effective_event_limit = max(1, min(event_limit, GPT_WALL_AGENT_EVENT_LIMIT))
        return self._board_api.get_gpt_wall(
            include_archived=bool(arguments.get("include_archived", True)),
            event_limit=effective_event_limit,
            compact=True,
        )

    def _get_cards(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_cards(
            include_archived=bool(arguments.get("include_archived", False)),
            compact=bool(arguments.get("compact", True)),
        )

    def _get_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_card(str(arguments.get("card_id") or ""))

    def _search_cards(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.search_cards(
            query=str(arguments.get("query") or ""),
            limit=int(arguments.get("limit") or 10),
            include_archived=bool(arguments.get("include_archived", False)),
            column=_optional_text(arguments, "column"),
            tag=_optional_text(arguments, "tag"),
            indicator=_optional_text(arguments, "indicator"),
            status=_optional_text(arguments, "status"),
        )

    def _get_card_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_card_context(str(arguments.get("card_id") or ""))

    def _get_card_log(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_card_log(
            str(arguments.get("card_id") or ""),
            limit=_optional_int(arguments, "limit"),
        )

    def _list_card_attachments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_card_attachments(
            str(arguments.get("card_id") or ""),
            include_removed=bool(arguments.get("include_removed", False)),
        )

    def _get_card_attachment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_card_attachment(
            str(arguments.get("card_id") or ""),
            str(arguments.get("attachment_id") or ""),
        )

    def _read_card_attachment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.read_card_attachment(
            str(arguments.get("card_id") or ""),
            str(arguments.get("attachment_id") or ""),
            mode=str(arguments.get("mode") or "preview"),
            max_chars=int(arguments.get("max_chars") or 12_000),
            include_base64=bool(arguments.get("include_base64", False)),
        )

    def _analyze_card_image_attachment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._image_analyzer is None:
            return {"ok": False, "error": {"message": "image analyzer is not configured"}}
        payload = self._board_api.read_card_attachment(
            str(arguments.get("card_id") or ""),
            str(arguments.get("attachment_id") or ""),
            mode="preview",
            include_base64=True,
        )
        if not _api_ok(payload):
            return payload
        content = _api_data(payload).get("content", {})
        if not isinstance(content, dict) or not content.get("base64"):
            return {"ok": False, "error": {"message": "attachment image bytes are unavailable"}}
        facts = self._image_analyzer(
            image_bytes=base64.b64decode(str(content.get("base64") or "")),
            mime_type=_mime_from_attachment_content(content),
            caption=str(arguments.get("caption") or ""),
        )
        return {"ok": True, "data": {"image_facts": facts, "source_attachment": content}}

    def _list_columns(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_columns()

    def _list_archived_cards(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_archived_cards(
            limit=_optional_int(arguments, "limit"),
            compact=_optional_bool(arguments, "compact"),
        )

    def _list_repair_orders(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_repair_orders(
            limit=_optional_int(arguments, "limit"),
            status=_optional_text(arguments, "status"),
            query=_optional_text(arguments, "query"),
            sort_by=_optional_text(arguments, "sort_by"),
            sort_dir=_optional_text(arguments, "sort_dir"),
        )

    def _get_repair_order_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_repair_order_text(str(arguments.get("card_id") or ""))

    def _list_clients(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_clients(
            limit=_optional_int(arguments, "limit"),
            include_stats=bool(arguments.get("include_stats", True)),
        )

    def _search_clients(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.search_clients(
            query=str(arguments.get("query") or ""),
            limit=_optional_int(arguments, "limit"),
        )

    def _get_client_profile(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_client(
            str(arguments.get("client_id") or ""),
            order_limit=_optional_int(arguments, "order_limit"),
        )

    def _link_card_to_client(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.link_card_to_client(
            str(arguments.get("card_id") or ""),
            str(arguments.get("client_id") or ""),
            client_vehicle_id=_optional_text(arguments, "client_vehicle_id"),
            create_vehicle_from_card=bool(arguments.get("create_vehicle_from_card", False)),
            sync_vehicle_fields=bool(arguments.get("sync_vehicle_fields", True)),
            sync_fields=bool(arguments.get("sync_fields", True)),
            overwrite_card_fields=bool(arguments.get("overwrite_card_fields", False)),
            actor_name=self._actor_name,
        )

    def _upsert_client_vehicle(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.upsert_client_vehicle(
            str(arguments.get("client_id") or ""),
            arguments.get("vehicle") if isinstance(arguments.get("vehicle"), dict) else None,
            client_vehicle_id=_optional_text(arguments, "client_vehicle_id"),
            card_id=_optional_text(arguments, "card_id"),
            sync_linked_cards=_optional_bool(arguments, "sync_linked_cards"),
            actor_name=self._actor_name,
        )

    def _delete_client_vehicle(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unlink_cards = _optional_bool(arguments, "unlink_cards")
        return self._board_api.delete_client_vehicle(
            str(arguments.get("client_id") or ""),
            str(arguments.get("client_vehicle_id") or arguments.get("vehicle_id") or ""),
            unlink_cards=True if unlink_cards is None else unlink_cards,
            actor_name=self._actor_name,
        )

    def _list_cashboxes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_cashboxes(limit=_optional_int(arguments, "limit"))

    def _get_cashbox(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_cashbox(
            str(arguments.get("cashbox_id") or ""),
            transaction_limit=_optional_int(arguments, "transaction_limit"),
        )

    def _create_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.create_card(
            title=str(arguments.get("title") or "").strip(),
            vehicle=str(arguments.get("vehicle") or ""),
            description=str(arguments.get("description") or ""),
            column=str(arguments.get("column") or "") or None,
            tags=arguments.get("tags") if isinstance(arguments.get("tags"), list) else None,
            deadline=arguments.get("deadline")
            if isinstance(arguments.get("deadline"), dict)
            else None,
            vehicle_profile=arguments.get("vehicle_profile")
            if isinstance(arguments.get("vehicle_profile"), dict)
            else None,
            actor_name=self._actor_name,
        )

    def _update_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.update_card(
            card_id=str(arguments.get("card_id") or ""),
            title=_optional_text(arguments, "title"),
            vehicle=_optional_text(arguments, "vehicle"),
            description=_optional_text(arguments, "description"),
            tags=arguments.get("tags") if isinstance(arguments.get("tags"), list) else None,
            deadline=arguments.get("deadline")
            if isinstance(arguments.get("deadline"), dict)
            else None,
            vehicle_profile=arguments.get("vehicle_profile")
            if isinstance(arguments.get("vehicle_profile"), dict)
            else None,
            actor_name=self._actor_name,
        )

    def _move_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.move_card(
            card_id=str(arguments.get("card_id") or ""),
            column=str(arguments.get("column") or ""),
            before_card_id=_optional_text(arguments, "before_card_id"),
            actor_name=self._actor_name,
        )

    def _mark_card_ready(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.mark_card_ready(
            card_id=str(arguments.get("card_id") or ""),
            actor_name=self._actor_name,
        )

    def _bulk_move_cards(self, arguments: dict[str, Any]) -> dict[str, Any]:
        card_ids = arguments.get("card_ids") if isinstance(arguments.get("card_ids"), list) else []
        return self._board_api.bulk_move_cards(
            card_ids=[str(card_id) for card_id in card_ids],
            column=str(arguments.get("column") or ""),
            actor_name=self._actor_name,
        )

    def _archive_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.archive_card(
            card_id=str(arguments.get("card_id") or ""), actor_name=self._actor_name
        )

    def _restore_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.restore_card(
            card_id=str(arguments.get("card_id") or ""),
            column=_optional_text(arguments, "column"),
            actor_name=self._actor_name,
        )

    def _cleanup_card_content(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.cleanup_card_content(
            card_id=str(arguments.get("card_id") or ""),
            actor_name=self._actor_name,
        )

    def _attach_telegram_photo_to_card(self, arguments: dict[str, Any]) -> dict[str, Any]:
        media_index = int(arguments.get("media_index") or 0)
        photos = [item for item in self._run_media if item.attachment.kind == "photo"]
        if media_index < 0 or media_index >= len(photos):
            return {"ok": False, "error": {"message": "telegram photo media_index is invalid"}}
        item = photos[media_index]
        file_name = str(arguments.get("file_name") or "").strip() or _telegram_photo_name(item)
        return self._board_api.add_card_attachment(
            card_id=str(arguments.get("card_id") or ""),
            file_name=file_name,
            mime_type=item.mime_type or "image/jpeg",
            content=item.content,
            actor_name=self._actor_name,
        )

    def _set_card_deadline(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.set_card_deadline(
            card_id=str(arguments.get("card_id") or ""),
            deadline=arguments.get("deadline")
            if isinstance(arguments.get("deadline"), dict)
            else {},
            actor_name=self._actor_name,
        )

    def _set_card_indicator(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.set_card_indicator(
            card_id=str(arguments.get("card_id") or ""),
            indicator=str(arguments.get("indicator") or ""),
            actor_name=self._actor_name,
        )

    def _create_column(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.create_column(
            label=str(arguments.get("label") or arguments.get("name") or ""),
            actor_name=self._actor_name,
        )

    def _rename_column(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.rename_column(
            str(arguments.get("column_id") or ""),
            str(arguments.get("label") or ""),
            actor_name=self._actor_name,
        )

    def _create_sticky(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.create_sticky(
            text=str(arguments.get("text") or ""),
            x=int(arguments.get("x") or 0),
            y=int(arguments.get("y") or 0),
            deadline=arguments.get("deadline")
            if isinstance(arguments.get("deadline"), dict)
            else {},
            actor_name=self._actor_name,
        )

    def _update_sticky(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.update_sticky(
            sticky_id=str(arguments.get("sticky_id") or ""),
            text=_optional_text(arguments, "text"),
            deadline=arguments.get("deadline")
            if isinstance(arguments.get("deadline"), dict)
            else None,
            actor_name=self._actor_name,
        )

    def _move_sticky(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.move_sticky(
            sticky_id=str(arguments.get("sticky_id") or ""),
            x=int(arguments.get("x") or 0),
            y=int(arguments.get("y") or 0),
            actor_name=self._actor_name,
        )

    def _create_cashbox(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.create_cashbox(
            str(arguments.get("name") or ""),
            actor_name=self._actor_name,
        )

    def _create_cash_transaction(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.create_cash_transaction(
            cashbox_id=str(arguments.get("cashbox_id") or ""),
            direction=str(arguments.get("direction") or ""),
            amount_minor=_optional_int(arguments, "amount_minor"),
            amount=arguments.get("amount"),
            note=str(arguments.get("note") or ""),
            actor_name=self._actor_name,
        )

    def _update_board_settings(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.update_board_settings(
            board_scale=float(arguments.get("board_scale") or 1.0),
            actor_name=self._actor_name,
        )

    def _list_overdue_cards(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.list_overdue_cards(
            include_archived=bool(arguments.get("include_archived", False))
        )

    def _get_repair_order(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.get_repair_order(str(arguments.get("card_id") or ""))

    def _update_repair_order(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.update_repair_order(
            card_id=str(arguments.get("card_id") or ""),
            repair_order=arguments.get("repair_order")
            if isinstance(arguments.get("repair_order"), dict)
            else {},
            actor_name=self._actor_name,
        )

    def _replace_repair_order_works(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.replace_repair_order_works(
            card_id=str(arguments.get("card_id") or ""),
            rows=arguments.get("rows") if isinstance(arguments.get("rows"), list) else [],
            actor_name=self._actor_name,
        )

    def _replace_repair_order_materials(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.replace_repair_order_materials(
            card_id=str(arguments.get("card_id") or ""),
            rows=arguments.get("rows") if isinstance(arguments.get("rows"), list) else [],
            actor_name=self._actor_name,
        )

    def _set_repair_order_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._board_api.set_repair_order_status(
            card_id=str(arguments.get("card_id") or ""),
            status=str(arguments.get("status") or ""),
            actor_name=self._actor_name,
        )


def _api_ok(payload: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and payload.get("ok") is not False


def _api_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else {}
    return data if isinstance(data, dict) else {}


def _api_error_message(payload: dict[str, Any], *, default: str) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or default)
    return default


def _optional_text(arguments: dict[str, Any], key: str) -> str | None:
    if key not in arguments:
        return None
    value = arguments.get(key)
    if value is None:
        return None
    return str(value)


def _optional_int(arguments: dict[str, Any], key: str) -> int | None:
    if key not in arguments:
        return None
    try:
        return int(arguments.get(key))
    except (TypeError, ValueError):
        return None


def _optional_bool(arguments: dict[str, Any], key: str) -> bool | None:
    if key not in arguments:
        return None
    return bool(arguments.get(key))


def _telegram_photo_name(item: DownloadedAttachment) -> str:
    if item.attachment.file_name:
        return item.attachment.file_name
    unique = (item.attachment.file_unique_id or "").strip()
    suffix = ".jpg"
    mime_type = (item.mime_type or "").lower()
    if mime_type == "image/png":
        suffix = ".png"
    elif mime_type == "image/webp":
        suffix = ".webp"
    elif mime_type == "image/gif":
        suffix = ".gif"
    marker = unique[:12] if unique else "photo"
    return f"telegram-{marker}{suffix}"


def _mime_from_attachment_content(content: dict[str, Any]) -> str:
    data_url = str(content.get("data_url") or "")
    if data_url.startswith("data:") and ";base64," in data_url:
        return data_url[5:].split(";base64,", 1)[0] or "image/jpeg"
    content_type = str(content.get("content_type") or "").lower()
    if content_type == "png":
        return "image/png"
    if content_type == "webp":
        return "image/webp"
    if content_type == "gif":
        return "image/gif"
    return "image/jpeg"
