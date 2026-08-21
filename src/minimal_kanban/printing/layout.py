from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompletionActLayoutMetrics:
    """Physical A4 measurements shared by CSS and logical pagination."""

    page_content_height_mm: float = 260.0
    footer_reserve_mm: float = 8.0
    table_header_height_mm: float = 10.5
    row_extra_line_height_mm: float = 3.4
    row_vertical_padding_mm: float = 0.75
    table_border_mm: float = 0.22
    first_header_base_height_mm: float = 32.0
    final_block_base_height_mm: float = 74.0
    final_block_margin_top_mm: float = 2.5

    @staticmethod
    def units(value_mm: float) -> int:
        return round(value_mm * 10)

    @property
    def page_content_units(self) -> int:
        return self.units(self.page_content_height_mm)

    @property
    def footer_reserve_units(self) -> int:
        return self.units(self.footer_reserve_mm)

    @property
    def table_header_units(self) -> int:
        return self.units(self.table_header_height_mm)

    @property
    def row_base_units(self) -> int:
        return self.units(
            self.row_extra_line_height_mm + 2 * self.row_vertical_padding_mm + self.table_border_mm
        )

    @property
    def row_extra_line_units(self) -> int:
        return self.units(self.row_extra_line_height_mm)

    @property
    def first_header_base_units(self) -> int:
        return self.units(self.first_header_base_height_mm)

    @property
    def final_block_base_units(self) -> int:
        return self.units(self.final_block_base_height_mm)

    @property
    def regular_table_body_units(self) -> int:
        return self.page_content_units - self.footer_reserve_units - self.table_header_units


COMPLETION_ACT_LAYOUT = CompletionActLayoutMetrics()


__all__ = ["COMPLETION_ACT_LAYOUT", "CompletionActLayoutMetrics"]
