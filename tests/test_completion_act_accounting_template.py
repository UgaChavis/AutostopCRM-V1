from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.printing.defaults import (  # noqa: E402
    PRINT_BASE_STYLES,
    builtin_template_records,
)
from minimal_kanban.printing.template_engine import render_template  # noqa: E402


def _completion_act_template() -> str:
    return next(
        record.content
        for record in builtin_template_records()
        if record.document_type == "completion_act"
    )


def _party(*, name: str, signer_position: str, signer_name: str) -> dict[str, str]:
    return {
        "legal_name_display": name,
        "address_display": "660049, г. Красноярск, пр-т Мира, 1",
        "inn_display": "2400000000",
        "kpp_display": "240001001",
        "ogrn_display": "1202400000000",
        "bank_name_display": "ПАО Банк",
        "bik_display": "040000000",
        "settlement_account_display": "40702810000000000001",
        "correspondent_account_display": "30101810000000000001",
        "signer_position_display": signer_position,
        "signer_name_display": signer_name,
    }


def _item(index: int) -> dict[str, str | int]:
    return {
        "index": index,
        "id": f"item-{index}",
        "section": "works" if index <= 2 else "materials",
        "name": f"ПОЗИЦИЯ-{index:02d}",
        "unit_display": "ч" if index <= 2 else "шт",
        "quantity_display": "1",
        "price_without_vat_display": "1 000,00",
        "sum_without_vat_display": "1 000,00",
    }


def _context(pages: list[dict[str, object]]) -> dict[str, object]:
    normalized_pages = [
        {
            **page,
            "show_table": page.get("show_table", True),
            "show_table_header": page.get("show_table_header", True),
            "show_empty_items": page.get("show_empty_items", False),
            "show_totals": page.get("show_totals", page.get("is_final", False)),
            "show_closing": page.get("show_closing", page.get("is_final", False)),
            "show_summary": page.get("show_summary", page.get("is_final", False)),
            "show_acceptance": page.get("show_acceptance", page.get("is_final", False)),
            "show_requisites": page.get("show_requisites", page.get("is_final", False)),
            "acceptance_text": page.get(
                "acceptance_text",
                (
                    "Вышеперечисленные работы (услуги) выполнены полностью и в срок. "
                    "Заказчик претензий по объему, качеству и срокам оказания услуг не имеет."
                ),
            ),
        }
        for page in pages
    ]
    return {
        "completion_act": {
            "document_number_display": "10700",
            "document_date_display": "02 марта 2026 г.",
            "basis_display": "",
            "performer": _party(
                name="Индивидуальный предприниматель Исполнитель И.И.",
                signer_position="ИП",
                signer_name="Исполнитель И.И.",
            ),
            "customer": _party(
                name='ООО "Заказчик"',
                signer_position="Директор",
                signer_name="Заказчик З.З.",
            ),
            "items": [item for page in normalized_pages for item in page.get("items", [])],
            "items_count": sum(len(page.get("items", [])) for page in normalized_pages),
            "items_count_words_display": "двадцать шесть",
            "totals": {
                "base_display": "20 000,00",
                "vat_rate_display": "5%",
                "vat_display": "1 000,00",
                "gross_display": "21 000,00",
                "base_words_display": "Двадцать тысяч рублей 00 копеек",
                "vat_words_display": "Одна тысяча рублей 00 копеек",
                "gross_words_display": "Двадцать одна тысяча рублей 00 копеек",
            },
            "acceptance_text": (
                "Вышеперечисленные работы (услуги) выполнены полностью и в срок. "
                "Заказчик претензий по объему, качеству и срокам оказания услуг не имеет."
            ),
            "pages": normalized_pages,
        }
    }


class CompletionActAccountingTemplateTests(unittest.TestCase):
    def test_renders_reference_accounting_structure_and_vat_five_percent(self) -> None:
        pages = [
            {
                "page_number": 1,
                "page_count": 1,
                "page_break_before": False,
                "page_break_marker": "",
                "is_first": True,
                "is_final": True,
                "items": [_item(1), _item(2), _item(3)],
            }
        ]

        rendered = render_template(_completion_act_template(), _context(pages))

        self.assertIn(
            "Акт о сдаче-приемке выполненных работ № 10700 от 02 марта 2026 г.",
            rendered,
        )
        self.assertIn("Исполнитель выполнил следующие работы (услуги):", rendered)
        self.assertEqual(1, rendered.count('class="completion-act__items"'))
        self.assertIn("Наименование работ (услуг)", rendered)
        self.assertIn("Цена<br>(без НДС)", rendered)
        self.assertIn("Сумма<br>(без НДС)", rendered)
        self.assertIn("ПОЗИЦИЯ-01", rendered)
        self.assertIn("ПОЗИЦИЯ-03", rendered)
        self.assertIn("Итого:", rendered)
        self.assertIn("НДС (5%):", rendered)
        self.assertIn("Всего:", rendered)
        self.assertIn("Двадцать одна тысяча рублей 00 копеек", rendered)
        self.assertIn("Одна тысяча рублей 00 копеек", rendered)
        self.assertIn("претензий по объему, качеству и срокам", rendered)
        self.assertIn("Индивидуальный предприниматель Исполнитель И.И.", rendered)
        self.assertIn("ООО &quot;Заказчик&quot;", rendered)
        self.assertIn("страница 1 из 1", rendered)
        self.assertNotIn("Основание:", rendered)

    def test_two_page_context_omits_header_on_totals_only_page(self) -> None:
        pages = [
            {
                "page_number": 1,
                "page_count": 2,
                "page_break_before": False,
                "page_break_marker": "",
                "is_first": True,
                "is_final": False,
                "items": [_item(index) for index in range(1, 27)],
            },
            {
                "page_number": 2,
                "page_count": 2,
                "page_break_before": True,
                "page_break_marker": "<!-- AUTOSTOPCRM_PAGE_BREAK -->",
                "is_first": False,
                "is_final": True,
                "items": [],
                "show_table_header": False,
            },
        ]

        rendered = render_template(_completion_act_template(), _context(pages))

        self.assertEqual(1, rendered.count("<!-- AUTOSTOPCRM_PAGE_BREAK -->"))
        self.assertEqual(1, rendered.count('class="completion-act__title"'))
        self.assertEqual(1, rendered.count("Наименование работ (услуг)"))
        self.assertEqual(1, rendered.count('class="completion-act__final"'))
        self.assertEqual(1, rendered.count("НДС (5%):"))
        self.assertIn("страница 1 из 2", rendered)
        self.assertIn("страница 2 из 2", rendered)
        for index in range(1, 27):
            self.assertEqual(1, rendered.count(f"ПОЗИЦИЯ-{index:02d}"))

    def test_template_drops_branded_service_and_cashless_markup_sections(self) -> None:
        template = _completion_act_template()

        for removed_fragment in (
            "brand_logo_data_uri",
            "doc-brand-mark",
            "doc-banner-table",
            "Телефон ресепшена",
            "Ключевые условия",
            "terms_summary_html",
            "налоги и сборы 15%",
            "Стоимость заказ-наряда за наличный расчет",
            "Стоимость заказ-наряда по безналичному расчету",
        ):
            self.assertNotIn(removed_fragment, template)

    def test_print_css_enforces_a4_logical_pages_and_repeatable_table_headers(self) -> None:
        self.assertIn(
            "@page completion-act-page { size: A4 portrait; margin: 9mm; }", PRINT_BASE_STYLES
        )
        self.assertIn(".completion-act__items thead", PRINT_BASE_STYLES)
        self.assertIn("display: table-header-group;", PRINT_BASE_STYLES)
        self.assertIn(".completion-act-page + .completion-act-page", PRINT_BASE_STYLES)
        self.assertIn("page-break-before: always;", PRINT_BASE_STYLES)
        self.assertIn(".completion-act__page-footer", PRINT_BASE_STYLES)
        self.assertIn(".completion-act__signature-value", PRINT_BASE_STYLES)
        self.assertIn("overflow-wrap: anywhere;", PRINT_BASE_STYLES)
        self.assertIn("word-break: break-word;", PRINT_BASE_STYLES)
        self.assertIn("--completion-act-page-content-height: 260.0mm;", PRINT_BASE_STYLES)
        self.assertIn("--completion-act-table-header-height: 10.5mm;", PRINT_BASE_STYLES)
        self.assertIn("--completion-act-row-padding-y: 0.75mm;", PRINT_BASE_STYLES)
        self.assertIn("--completion-act-row-line-height: 3.4mm;", PRINT_BASE_STYLES)
        self.assertIn("--completion-act-table-border: 0.22mm;", PRINT_BASE_STYLES)
        self.assertIn("--completion-act-final-min-height: 74.0mm;", PRINT_BASE_STYLES)


if __name__ == "__main__":
    unittest.main()
