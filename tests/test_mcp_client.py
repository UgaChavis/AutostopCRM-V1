from __future__ import annotations

import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError, _normalize_int


class BoardApiClientTests(unittest.TestCase):
    def test_update_repair_order_forwards_card_and_cashbox_revisions(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(
            client, "_request_with_identity", return_value={"ok": True}
        ) as request:
            client.update_repair_order(
                card_id="card-1",
                repair_order={"payments": []},
                expected_updated_at="card-revision-1",
                expected_cashbox_id="cashbox-1",
                expected_cashbox_updated_at="cashbox-revision-1",
                actor_name="CODEX",
            )

        request.assert_called_once_with(
            "/api/update_repair_order",
            {
                "card_id": "card-1",
                "repair_order": {"payments": []},
                "expected_updated_at": "card-revision-1",
                "expected_cashbox_id": "cashbox-1",
                "expected_cashbox_updated_at": "cashbox-revision-1",
            },
            actor_name="CODEX",
        )

    def test_create_card_without_deadline_keeps_timer_inactive(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(title="Новая заявка")

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Новая заявка",
                "description": "",
                "source": "mcp",
            },
        )

    def test_normalize_int_clamps_large_finite_values_before_conversion(self) -> None:
        self.assertEqual(_normalize_int(1e308, default=5, minimum=1, maximum=30), 30)
        self.assertEqual(_normalize_int(-1e308, default=5, minimum=1, maximum=30), 1)

    def test_timeout_seconds_is_normalized_to_safe_finite_range(self) -> None:
        self.assertEqual(
            BoardApiClient("https://board.example/api", timeout_seconds=True)._timeout_seconds,
            10.0,
        )
        self.assertEqual(
            BoardApiClient(
                "https://board.example/api", timeout_seconds=float("inf")
            )._timeout_seconds,
            10.0,
        )
        self.assertEqual(
            BoardApiClient("https://board.example/api", timeout_seconds=-5)._timeout_seconds,
            10.0,
        )
        self.assertEqual(
            BoardApiClient("https://board.example/api", timeout_seconds=0.001)._timeout_seconds,
            0.1,
        )
        self.assertEqual(
            BoardApiClient("https://board.example/api", timeout_seconds=999)._timeout_seconds,
            60.0,
        )

    def test_compose_url_does_not_duplicate_api_segment(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        self.assertEqual(
            client._compose_url("/api/get_board_snapshot"),
            "https://board.example/api/get_board_snapshot",
        )
        self.assertEqual(
            client._compose_url("api/get_cards"), "https://board.example/api/get_cards"
        )

    def test_parse_json_payload_rejects_non_object_json(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with self.assertRaises(BoardApiTransportError):
            client._parse_json_payload(b"[]", path="/api/health")

    def test_parse_json_payload_rejects_non_standard_numeric_constants(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with self.assertRaises(BoardApiTransportError):
            client._parse_json_payload(b'{"ok": true, "value": NaN}', path="/api/health")

    def test_parse_json_payload_rejects_deeply_nested_json(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")
        deep_json = ("[" * 5000 + "]" * 5000).encode("utf-8")

        with self.assertRaises(BoardApiTransportError):
            client._parse_json_payload(deep_json, path="/api/health")

    def test_create_card_deadline_ignores_invalid_numeric_parts(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(
                title="Новая заявка",
                deadline={
                    "days": "bad",
                    "hours": float("inf"),
                    "minutes": "15",
                    "seconds": None,
                },
            )

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Новая заявка",
                "description": "",
                "deadline": {"days": 0, "hours": 0, "minutes": 15, "seconds": 0},
                "source": "mcp",
            },
        )

    def test_create_card_deadline_defaults_when_all_parts_are_invalid(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(
                title="Новая заявка",
                deadline={"days": "bad", "hours": "", "minutes": None, "seconds": []},
            )

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Новая заявка",
                "description": "",
                "deadline": {"days": 1, "hours": 0, "minutes": 0, "seconds": 0},
                "source": "mcp",
            },
        )

    def test_create_card_deadline_preserves_total_seconds(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(title="Запись", deadline={"total_seconds": 160_982})

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Запись",
                "description": "",
                "deadline": {
                    "days": 0,
                    "hours": 0,
                    "minutes": 0,
                    "seconds": 0,
                    "total_seconds": 160_982,
                },
                "source": "mcp",
            },
        )

    def test_create_card_deadline_rejects_bool_and_fractional_numeric_parts(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(
                title="Новая заявка",
                deadline={"days": True, "hours": "2.5", "minutes": 3.0, "seconds": "4"},
            )

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Новая заявка",
                "description": "",
                "deadline": {"days": 0, "hours": 0, "minutes": 3, "seconds": 4},
                "source": "mcp",
            },
        )

    def test_create_card_deadline_clamps_oversized_numeric_parts(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(
                title="Новая заявка",
                deadline={
                    "days": 1e308,
                    "hours": 1e308,
                    "minutes": 1e308,
                    "seconds": 1e308,
                },
            )

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Новая заявка",
                "description": "",
                "deadline": {"days": 365, "hours": 23, "minutes": 59, "seconds": 59},
                "source": "mcp",
            },
        )

    def test_create_card_deadline_clamps_negative_parts_to_default(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_card(
                title="Новая заявка",
                deadline={"days": -1, "hours": -2, "minutes": -3, "seconds": -4},
            )

        request.assert_called_once_with(
            "/api/create_card",
            {
                "vehicle": "",
                "title": "Новая заявка",
                "description": "",
                "deadline": {"days": 1, "hours": 0, "minutes": 0, "seconds": 0},
                "source": "mcp",
            },
        )

    def test_read_attachment_limits_are_clamped_before_request(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.read_card_attachment(
                "card-1",
                "attachment-1",
                max_chars=-50,
                max_base64_bytes=999_999_999,
            )

        request.assert_called_once_with(
            "/api/read_card_attachment",
            {
                "card_id": "card-1",
                "attachment_id": "attachment-1",
                "mode": "preview",
                "max_chars": 1,
                "include_base64": False,
                "max_base64_bytes": 4_194_304,
            },
        )

    def test_get_cashbox_pagination_limits_are_clamped_before_request(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_cashbox("cashbox-1", transaction_limit=1e308, transaction_offset=1e308)

        request.assert_called_once_with(
            "/api/get_cashbox",
            {
                "cashbox_id": "cashbox-1",
                "transaction_limit": 5000,
                "transaction_offset": 1_000_000,
            },
        )

    def test_read_attachment_limits_reject_bool_and_fractional_values(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.read_card_attachment(
                "card-1",
                "attachment-1",
                max_chars=True,
                max_base64_bytes="2048.5",
            )

        request.assert_called_once_with(
            "/api/read_card_attachment",
            {
                "card_id": "card-1",
                "attachment_id": "attachment-1",
                "mode": "preview",
                "max_chars": 12_000,
                "include_base64": False,
                "max_base64_bytes": 1_048_576,
            },
        )

    def test_download_shared_file_base64_limit_is_clamped_before_request(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.download_shared_file("file-1", max_base64_bytes=999_999_999)

        request.assert_called_once_with(
            "/api/fetch_shared_file",
            {
                "file_id": "file-1",
                "include_base64": True,
                "max_base64_bytes": 8_388_608,
            },
        )

    def test_request_rejects_unserializable_payload_as_transport_error(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with self.assertRaises(BoardApiTransportError):
            client._request("/api/test", {"bad": object()})

    def test_request_rejects_non_finite_payload_numbers_as_transport_error(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch("minimal_kanban.mcp.client._urlopen_no_redirect") as urlopen:
            with self.assertRaises(BoardApiTransportError):
                client._request("/api/test", {"bad": float("nan")})

        urlopen.assert_not_called()

    def test_request_rejects_bearer_token_with_header_breaks(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="good\r\nX-Bad: yes")

        with patch("minimal_kanban.mcp.client._urlopen_no_redirect") as urlopen:
            with self.assertRaises(BoardApiTransportError):
                client.health()

        urlopen.assert_not_called()

    def test_request_trims_bearer_token_before_header(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token=" secret ")

        class HealthyResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size: int = -1) -> bytes:
                _ = size
                return b'{"ok": true}'

        with patch(
            "minimal_kanban.mcp.client._urlopen_no_redirect", return_value=HealthyResponse()
        ) as urlopen:
            client.health()

        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    def test_request_wraps_urlopen_value_error_as_transport_error(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch(
            "minimal_kanban.mcp.client._urlopen_no_redirect",
            side_effect=ValueError("bad timeout"),
        ) as urlopen:
            with self.assertRaises(BoardApiTransportError):
                client.health()

        self.assertEqual(urlopen.call_count, 2)

    def test_request_rejects_oversized_success_body(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        class HugeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size: int = -1) -> bytes:
                return b"x" * max(0, size)

        with (
            patch("minimal_kanban.mcp.client._MAX_API_RESPONSE_BYTES", 4),
            patch(
                "minimal_kanban.mcp.client._urlopen_no_redirect",
                return_value=HugeResponse(),
            ),
            self.assertRaises(BoardApiTransportError) as error,
        ):
            client.health()

        self.assertIn("слишком большой JSON", str(error.exception))

    def test_request_rejects_oversized_error_body(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        class HugeHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="https://board.example/api/health",
                    code=500,
                    msg="Internal Server Error",
                    hdrs=None,
                    fp=None,
                )

            def read(self, size: int = -1) -> bytes:
                return b"x" * max(0, size)

        with (
            patch("minimal_kanban.mcp.client._MAX_API_RESPONSE_BYTES", 4),
            patch(
                "minimal_kanban.mcp.client._urlopen_no_redirect",
                side_effect=HugeHttpError(),
            ),
            self.assertRaises(BoardApiTransportError) as error,
        ):
            client.health()

        self.assertIn("слишком большой JSON", str(error.exception))

    def test_request_rejects_redirect_responses(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")
        redirect = urllib.error.HTTPError(
            url="https://board.example/api/health",
            code=302,
            msg="Found",
            hdrs={"Location": "https://elsewhere.example/api/health"},
            fp=None,
        )

        with (
            patch("minimal_kanban.mcp.client._urlopen_no_redirect", side_effect=redirect),
            self.assertRaises(BoardApiTransportError) as error,
        ):
            client.health()

        self.assertIn("перенаправление", str(error.exception))

    def test_optional_scalar_filter_uses_get_without_payload_and_post_with_payload(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_board_snapshot()
            client.list_archived_cards()
            client.list_repair_orders()

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call("/api/get_board_snapshot", method="GET"),
                unittest.mock.call("/api/list_archived_cards", method="GET"),
                unittest.mock.call("/api/list_repair_orders", method="GET"),
            ],
        )

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_board_snapshot(archive_limit=5)
            client.list_archived_cards(limit=10)
            client.list_repair_orders(limit=300)
            client.list_repair_orders(limit=25, status="closed")
            client.list_repair_orders(
                limit=20,
                status="all",
                query="срочно dsg",
                sort_by="closed_at",
                sort_dir="asc",
                compact=True,
                redact_private=True,
            )

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call("/api/get_board_snapshot", {"archive_limit": 5}, method="POST"),
                unittest.mock.call("/api/list_archived_cards", {"limit": 10}, method="POST"),
                unittest.mock.call("/api/list_repair_orders", {"limit": 300}, method="POST"),
                unittest.mock.call(
                    "/api/list_repair_orders", {"limit": 25, "status": "closed"}, method="POST"
                ),
                unittest.mock.call(
                    "/api/list_repair_orders",
                    {
                        "limit": 20,
                        "status": "all",
                        "query": "срочно dsg",
                        "sort_by": "closed_at",
                        "sort_dir": "asc",
                        "compact": True,
                        "redact_private": True,
                    },
                    method="POST",
                ),
            ],
        )

    def test_repair_order_pdf_export_uses_expected_api_payload(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.download_repair_order_print_pdf(
                card_id="card-1",
                selected_document_ids=["invoice", "completion_act"],
                selected_template_ids={"invoice": "tpl-invoice"},
                print_settings={"stamp_enabled": True},
            )

        request.assert_called_once_with(
            "/api/export_repair_order_print_pdf",
            {
                "card_id": "card-1",
                "selected_document_ids": ["invoice", "completion_act"],
                "selected_template_ids": {"invoice": "tpl-invoice"},
                "print_settings": {"stamp_enabled": True},
            },
        )

    def test_manual_document_pdf_export_uses_expected_api_payload(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_document_without_card_pdf(
                request_text="Счет MAN-55 от 15.06.2026 для ООО Ромашка. Работы: Диагностика 1 x 2500.",
                document_type="invoice",
                manual_document={"client": {"display_name": "ООО Ромашка"}},
                print_settings={"paper_size": "A4"},
            )

        request.assert_called_once_with(
            "/api/export_repair_order_print_pdf",
            {
                "document_without_card": True,
                "request_text": "Счет MAN-55 от 15.06.2026 для ООО Ромашка. Работы: Диагностика 1 x 2500.",
                "selected_document_ids": ["invoice"],
                "manual_document": {"client": {"display_name": "ООО Ромашка"}},
                "print_settings": {"paper_size": "A4"},
            },
        )

    def test_manual_document_pdf_export_infers_document_type_from_text_request(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_document_without_card_pdf(
                request_text=(
                    "Акт выполненных работ № ACT-55 от 15.06.2026 для ООО Ромашка. "
                    "Работы: Диагностика 1 x 2500."
                ),
                document_type="",
            )

        request.assert_called_once_with(
            "/api/export_repair_order_print_pdf",
            {
                "document_without_card": True,
                "request_text": (
                    "Акт выполненных работ № ACT-55 от 15.06.2026 для ООО Ромашка. "
                    "Работы: Диагностика 1 x 2500."
                ),
                "selected_document_ids": ["completion_act"],
            },
        )

    def test_manual_document_pdf_export_accepts_russian_document_type_alias(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_document_without_card_pdf(
                request_text="Дефектовка без карточки для ООО Ромашка",
                document_type="дефектовка",
            )

        request.assert_called_once_with(
            "/api/export_repair_order_print_pdf",
            {
                "document_without_card": True,
                "request_text": "Дефектовка без карточки для ООО Ромашка",
                "selected_document_ids": ["inspection_sheet"],
            },
        )

    def test_manual_document_pdf_export_infers_every_standard_document_family(self) -> None:
        cases = [
            ("Счет на оплату без карточки для ООО Ромашка", "invoice"),
            ("Счет-фактура без карточки для ООО Ромашка", "invoice_factura"),
            ("УПД без карточки для ООО Ромашка", "upd"),
            ("Акт выполненных работ без карточки для ООО Ромашка", "completion_act"),
            ("Акт приема автомобиля без карточки для ООО Ромашка", "vehicle_acceptance_act"),
            ("Заказ-наряд без карточки для ООО Ромашка", "repair_order"),
            ("Дефектовочная ведомость без карточки для ООО Ромашка", "inspection_sheet"),
            ("Продажа запчастей без карточки для ООО Ромашка", "parts_sale"),
        ]

        for request_text, expected_document_type in cases:
            with self.subTest(request_text=request_text):
                client = BoardApiClient("https://board.example/api", bearer_token="secret")
                with patch.object(client, "_request", return_value={"ok": True}) as request:
                    client.create_document_without_card_pdf(
                        request_text=request_text,
                        document_type="auto",
                    )

                request.assert_called_once_with(
                    "/api/export_repair_order_print_pdf",
                    {
                        "document_without_card": True,
                        "request_text": request_text,
                        "selected_document_ids": [expected_document_type],
                    },
                )

    def test_get_card_log_can_request_compact_payload(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_card_log("card-1", compact=True, limit=50, include_full_details=True)

        request.assert_called_once_with(
            "/api/get_card_log",
            {
                "card_id": "card-1",
                "limit": 50,
                "compact": True,
                "include_full_details": True,
            },
        )

    def test_wall_helpers_call_expected_api_endpoints(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_board_content()
            client.get_board_content(include_archived=False, view_mode="full")
            client.get_board_events()
            client.get_board_events(event_limit=25, include_archived=False)
            client.get_gpt_wall()
            client.get_gpt_wall(include_archived=False, event_limit=15)

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call(
                    "/api/get_board_content",
                    {"include_archived": True, "view_mode": "agent"},
                    method="POST",
                ),
                unittest.mock.call(
                    "/api/get_board_content",
                    {"include_archived": False, "view_mode": "full"},
                    method="POST",
                ),
                unittest.mock.call(
                    "/api/get_board_events",
                    {"event_limit": 100, "include_archived": True, "view_mode": "audit"},
                    method="POST",
                ),
                unittest.mock.call(
                    "/api/get_board_events",
                    {"event_limit": 25, "include_archived": False, "view_mode": "audit"},
                    method="POST",
                ),
                unittest.mock.call(
                    "/api/get_gpt_wall",
                    {"include_archived": True},
                    method="POST",
                ),
                unittest.mock.call(
                    "/api/get_gpt_wall",
                    {"include_archived": False, "event_limit": 15},
                    method="POST",
                ),
            ],
        )

    def test_client_read_helpers_use_get_queries_for_retryable_reads(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.list_clients(limit=10, include_stats=False)
            client.search_clients(query="Петров", limit=5)
            client.get_client("client-1", order_limit=3)
            client.get_client_stats("client-1")

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call("/api/list_clients?include_stats=false&limit=10", method="GET"),
                unittest.mock.call(
                    "/api/search_clients?query=%D0%9F%D0%B5%D1%82%D1%80%D0%BE%D0%B2&limit=5",
                    method="GET",
                ),
                unittest.mock.call(
                    "/api/get_client?client_id=client-1&order_limit=3", method="GET"
                ),
                unittest.mock.call("/api/get_client_stats?client_id=client-1", method="GET"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
