"""Shared validation for Manager operations dispatched through the service."""

from __future__ import annotations


class CardServiceManagerMixin:
    def _validate_manager_operation_modes(self, payload, operation_payload):
        supplied_modes = [
            self._manager_operation_mode({"mode": source[field]})
            for source, fields in (
                (payload, ("mode",)),
                (operation_payload, ("mode", "apply_mode")),
            )
            for field in fields
            if field in source
        ]
        if len(set(supplied_modes)) > 1:
            self._fail(
                "validation_error",
                "Режимы операции и вложенных данных должны совпадать.",
                details={"field": "mode"},
            )
