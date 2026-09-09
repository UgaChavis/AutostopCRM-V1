from __future__ import annotations

from typing import Any

from ..models import normalize_text, utc_now
from .bundle_draft import BundleDraft
from .errors import ServiceError


class CardServiceBulkMixin:
    """Atomic best-effort operations spanning multiple cards."""

    def bulk_move_cards(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._read_bundle_for_update(
                "cards", "columns", "cashboxes", "cash_transactions", "inventory_items"
            )
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            response_mode = self._validated_response_mode(payload, default="full")
            ready_column_id, ready_column_changed = self._ensure_ready_column_for_bundle(
                bundle, actor_name=actor_name, source=source
            )
            next_column = self._validated_column(payload.get("column"), columns)
            raw_card_ids = payload.get("card_ids")
            if not isinstance(raw_card_ids, list) or not raw_card_ids:
                self._fail(
                    "validation_error",
                    "Нужно передать непустой список card_ids для пакетного переноса.",
                    details={"field": "card_ids"},
                )

            seen_ids: set[str] = set()
            normalized_card_ids: list[str] = []
            for raw_id in raw_card_ids:
                card_id = normalize_text(raw_id, default="", limit=128)
                if not card_id:
                    self._fail(
                        "validation_error",
                        "Список card_ids содержит пустое значение.",
                        details={"field": "card_ids"},
                    )
                if card_id in seen_ids:
                    continue
                seen_ids.add(card_id)
                normalized_card_ids.append(card_id)

            moved_results: list[tuple[str, dict[str, Any]]] = []
            unchanged_results: list[tuple[str, dict[str, Any]]] = []
            errors: list[dict] = []
            warnings: list[dict] = []
            changed_any = False
            column_labels = self._column_labels(columns)

            for card_id in normalized_card_ids:
                try:
                    source_card = self._find_card(cards, card_id)
                    self._ensure_not_archived(source_card)
                    previous_column = source_card.column
                    # Ready transitions also touch mutable finance/payroll branches. Detach
                    # them with the card so a rejected sibling cannot leak partial state.
                    ready_domains = (
                        ("cashboxes", "cash_transactions", "settings")
                        if self._card_has_repair_order(source_card)
                        and (previous_column == ready_column_id or next_column == ready_column_id)
                        else ()
                    )
                    candidate_bundle = BundleDraft(bundle, domains=ready_domains, card_id=card_id)
                    candidate_cards = candidate_bundle["cards"]
                    candidate_events = candidate_bundle["events"]
                    card = self._find_card(candidate_cards, card_id)

                    changed = False
                    if card.column != next_column:
                        previous_position = card.position
                        self._reposition_card(candidate_cards, card, target_column=next_column)
                        self._touch_card(card, actor_name)
                        self._append_event(
                            candidate_events,
                            actor_name=actor_name,
                            source=source,
                            action="card_moved",
                            message=f"{actor_name} переместил карточку",
                            card_id=card.id,
                            details={
                                "before_column": previous_column,
                                "after_column": next_column,
                                "before_position": previous_position,
                                "after_position": card.position,
                                "before_card_id": None,
                            },
                        )
                        changed = True
                    ready_state_changed, ready_warnings = self._apply_ready_column_side_effects(
                        card,
                        candidate_cards,
                        candidate_events,
                        actor_name,
                        source,
                        before_column=previous_column,
                        after_column=card.column,
                        ready_column_id=ready_column_id,
                        bundle=candidate_bundle,
                    )
                    if ready_state_changed and not changed:
                        self._touch_card(card, actor_name)
                    changed |= ready_state_changed
                    warnings.extend({"card_id": card.id, **warning} for warning in ready_warnings)
                    result = (
                        card.id,
                        {
                            "before_column": previous_column,
                            "after_column": next_column,
                            "changed": changed,
                        },
                    )
                    cards[:] = candidate_cards
                    events[:] = candidate_events
                    for domain in ready_domains:
                        bundle[domain] = candidate_bundle[domain]
                    changed_any |= changed
                    (moved_results if changed else unchanged_results).append(result)
                except ServiceError as exc:
                    errors.append(
                        {
                            "card_id": card_id,
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        }
                    )

            numbering_changed = self._synchronize_repair_order_numbers(
                cards, exclude_card_ids={item["card_id"] for item in errors}
            )
            serialized_at = utc_now()
            cards_by_id = {card.id: card for card in cards}

            def serialize_result(item: tuple[str, dict[str, Any]]) -> dict[str, Any]:
                serialized = self._response_card(
                    cards_by_id[item[0]],
                    events,
                    column_labels,
                    actor_name,
                    response_mode,
                    now=serialized_at,
                )
                serialized["bulk_move"] = item[1]
                return serialized

            moved_cards = [serialize_result(item) for item in moved_results]
            unchanged_cards = [serialize_result(item) for item in unchanged_results]
            if changed_any or ready_column_changed or numbering_changed:
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)

            self._logger.info(
                "bulk_move_cards count=%s moved=%s unchanged=%s errors=%s column=%s actor=%s source=%s",
                len(normalized_card_ids),
                len(moved_cards),
                len(unchanged_cards),
                len(errors),
                next_column,
                actor_name,
                source,
            )
            return {
                "column": next_column,
                "moved_cards": moved_cards,
                "unchanged_cards": unchanged_cards,
                "errors": errors,
                "meta": {
                    "requested": len(normalized_card_ids),
                    "moved": len(moved_cards),
                    "unchanged": len(unchanged_cards),
                    "errors": len(errors),
                    "partial_failure": bool(errors),
                    "warnings": warnings,
                    "response_mode": response_mode,
                    "verification": {
                        "target_column": next_column,
                        "moved_card_ids": [card["id"] for card in moved_cards],
                        "errors": len(errors),
                    },
                },
            }
