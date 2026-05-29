from __future__ import annotations

from ..models import normalize_text
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED, RepairOrder, RepairOrderRow

WORK_SNAPSHOT_FIELDS = (
    "work_quantity_snapshot",
    "work_price_snapshot",
    "work_total_snapshot",
    "salary_mode_snapshot",
    "base_salary_snapshot",
    "work_percent_snapshot",
    "salary_amount",
    "salary_accrued_at",
)

MATERIAL_SNAPSHOT_FIELDS = (
    "material_quantity_snapshot",
    "material_price_snapshot",
    "material_cost_price_snapshot",
    "material_percent_snapshot",
    "material_profit",
    "material_salary_amount",
    "material_salary_accrued_at",
)


def _row_from(source: RepairOrderRow | dict[str, str]) -> RepairOrderRow:
    return RepairOrderRow.from_dict(
        source.to_dict() if isinstance(source, RepairOrderRow) else source
    )


def _work_salary_snapshot_signature(row: RepairOrderRow) -> tuple[str, ...]:
    if not row.salary_accrued_at:
        return ()
    return (
        normalize_text(row.work_executor_id_snapshot, default="", limit=64),
        normalize_text(row.work_executor_name_snapshot, default="", limit=80),
        normalize_text(row.work_quantity_snapshot, default="", limit=40),
        normalize_text(row.work_price_snapshot, default="", limit=40),
        normalize_text(row.work_total_snapshot, default="", limit=40),
        normalize_text(row.salary_mode_snapshot, default="", limit=40),
        normalize_text(row.base_salary_snapshot, default="", limit=40),
        normalize_text(row.work_percent_snapshot, default="", limit=40),
        normalize_text(row.salary_amount, default="", limit=40),
        normalize_text(row.salary_accrued_at, default="", limit=40),
    )


def _material_salary_snapshot_signature(row: RepairOrderRow) -> tuple[str, ...]:
    if not row.material_salary_accrued_at:
        return ()
    return (
        normalize_text(row.material_executor_id_snapshot, default="", limit=64),
        normalize_text(row.material_executor_name_snapshot, default="", limit=80),
        normalize_text(row.material_quantity_snapshot, default="", limit=40),
        normalize_text(row.material_price_snapshot, default="", limit=40),
        normalize_text(row.material_cost_price_snapshot, default="", limit=40),
        normalize_text(row.material_percent_snapshot, default="", limit=40),
        normalize_text(row.material_profit, default="", limit=40),
        normalize_text(row.material_salary_amount, default="", limit=40),
        normalize_text(row.material_salary_accrued_at, default="", limit=40),
    )


def preserve_repair_order_payroll_snapshots(
    previous_order: RepairOrder, next_order: RepairOrder
) -> RepairOrder:
    if next_order.status != REPAIR_ORDER_STATUS_CLOSED:
        return next_order
    next_rows: list[dict[str, str]] = []
    changed = False
    previous_rows = list(previous_order.works)
    represented_signatures = {
        signature
        for source_row in next_order.works
        for signature in (_work_salary_snapshot_signature(_row_from(source_row)),)
        if signature
    }
    for index, source_row in enumerate(next_order.works):
        row = _row_from(source_row)
        before = row.to_dict()
        if index < len(previous_rows):
            previous_row = _row_from(previous_rows[index])
            previous_signature = _work_salary_snapshot_signature(previous_row)
            current_signature = _work_salary_snapshot_signature(row)
            if (
                previous_signature
                and current_signature != previous_signature
                and previous_signature in represented_signatures
            ):
                next_rows.append(before)
                continue
            if previous_row.salary_accrued_at:
                row.work_executor_id_snapshot = (
                    row.work_executor_id_snapshot
                    or previous_row.work_executor_id_snapshot
                    or previous_row.executor_id
                )
                row.work_executor_name_snapshot = (
                    row.work_executor_name_snapshot
                    or previous_row.work_executor_name_snapshot
                    or previous_row.executor_name
                )
                row.work_quantity_snapshot = (
                    row.work_quantity_snapshot
                    or previous_row.work_quantity_snapshot
                    or previous_row.quantity
                )
                row.work_price_snapshot = (
                    row.work_price_snapshot
                    or previous_row.work_price_snapshot
                    or previous_row.price
                )
                row.work_total_snapshot = (
                    row.work_total_snapshot
                    or previous_row.work_total_snapshot
                    or previous_row.total
                )
                for field in WORK_SNAPSHOT_FIELDS:
                    if not getattr(row, field):
                        setattr(row, field, getattr(previous_row, field))
        after = row.to_dict()
        changed = changed or after != before
        next_rows.append(after)

    next_material_rows: list[dict[str, str]] = []
    previous_materials = list(previous_order.materials)
    represented_material_signatures = {
        signature
        for source_row in next_order.materials
        for signature in (_material_salary_snapshot_signature(_row_from(source_row)),)
        if signature
    }
    for index, source_row in enumerate(next_order.materials):
        row = _row_from(source_row)
        before = row.to_dict()
        if index < len(previous_materials):
            previous_row = _row_from(previous_materials[index])
            previous_signature = _material_salary_snapshot_signature(previous_row)
            current_signature = _material_salary_snapshot_signature(row)
            if (
                previous_signature
                and current_signature != previous_signature
                and previous_signature in represented_material_signatures
            ):
                next_material_rows.append(before)
                continue
            if previous_row.material_salary_accrued_at:
                row.material_executor_id_snapshot = (
                    row.material_executor_id_snapshot
                    or previous_row.material_executor_id_snapshot
                    or previous_row.executor_id
                )
                row.material_executor_name_snapshot = (
                    row.material_executor_name_snapshot
                    or previous_row.material_executor_name_snapshot
                    or previous_row.executor_name
                )
                for field in MATERIAL_SNAPSHOT_FIELDS:
                    if not getattr(row, field):
                        setattr(row, field, getattr(previous_row, field))
        after = row.to_dict()
        changed = changed or after != before
        next_material_rows.append(after)
    if not changed:
        return next_order
    return RepairOrder.from_dict(
        {**next_order.to_storage_dict(), "works": next_rows, "materials": next_material_rows}
    )
