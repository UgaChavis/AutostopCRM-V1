from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.client import BoardApiClient


class BoardApiClientTests(unittest.TestCase):
    def test_compose_url_does_not_duplicate_api_segment(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        self.assertEqual(
            client._compose_url("/api/get_board_snapshot"),
            "https://board.example/api/get_board_snapshot",
        )
        self.assertEqual(
            client._compose_url("api/get_cards"), "https://board.example/api/get_cards"
        )

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
