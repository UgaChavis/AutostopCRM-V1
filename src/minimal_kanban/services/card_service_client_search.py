"""Client matching and ranking helpers shared by the client service."""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Any

from ..models import Card, ClientProfile, normalize_text

_PHONE_PATTERN = re.compile(
    r"(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[\- ]?\s*\d{3}\s*[\- ]?\s*\d{2}\s*[\- ]?\s*\d{2}"
)


@lru_cache(maxsize=128)
def _prepared_search_variant(variant: str) -> tuple[tuple[str, ...], str]:
    return tuple(variant.split()), re.sub(r"[\W_]+", "", variant)


class CardServiceClientSearchMixin:
    def _client_match_keys(self, client: ClientProfile) -> set[str]:
        values = {
            client.name(),
            client.full_name(),
            client.display_name,
            client.phone,
            client.email,
            *client.emails,
            client.inn,
            client.contact_person,
            *client.phones,
        }
        for vehicle in client.vehicles:
            values.update(
                {
                    vehicle.vehicle,
                    vehicle.brand,
                    vehicle.model,
                    self._client_vehicle_search_vin(vehicle.vin),
                    vehicle.license_plate,
                    vehicle.year,
                    vehicle.body_number,
                    vehicle.chassis_number,
                    vehicle.engine_code,
                    vehicle.engine_model,
                    vehicle.gearbox_model,
                    vehicle.drivetrain,
                }
            )
        keys: set[str] = set()
        for value in values:
            normalized = self._normalize_search_text(value)
            if normalized:
                keys.add(normalized)
            keys.update(self._phone_match_keys(value))
        return keys

    def _client_search_digits_blob(self, client: ClientProfile) -> str:
        values = [
            client.name(),
            client.full_name(),
            client.display_name,
            client.inn,
            client.ogrn,
            client.contact_person,
        ]
        for phone in [client.phone, *client.phones]:
            digits = re.sub(r"\D+", "", str(phone or ""))
            if len(digits) < 7:
                continue
            values.append(digits)
            if len(digits) >= 10:
                last_ten = digits[-10:]
                values.extend([last_ten, "7" + last_ten, "8" + last_ten])
        for vehicle in client.vehicles:
            values.extend(
                [
                    vehicle.vehicle,
                    vehicle.brand,
                    vehicle.model,
                    self._client_vehicle_search_vin(vehicle.vin),
                    vehicle.license_plate,
                    vehicle.year,
                    vehicle.body_number,
                    vehicle.chassis_number,
                    vehicle.engine_code,
                    vehicle.engine_model,
                    vehicle.gearbox_model,
                    vehicle.drivetrain,
                ]
            )
        return "".join(re.sub(r"\D+", "", str(value or "")) for value in values)

    def _client_direct_digit_values(self, client: ClientProfile) -> set[str]:
        values = [
            client.name(),
            client.full_name(),
            client.display_name,
            client.phone,
            *client.phones,
            client.inn,
            client.ogrn,
            client.contact_person,
        ]
        digits_values: set[str] = set()
        for value in values:
            digits = re.sub(r"\D+", "", str(value or ""))
            if len(digits) < 3:
                continue
            digits_values.add(digits)
            if len(digits) >= 4 and digits[0] in "78":
                digits_values.add(digits[1:])
            if len(digits) >= 10:
                last_ten = digits[-10:]
                digits_values.update({last_ten, "7" + last_ten, "8" + last_ten})
        return digits_values

    def _card_client_values(self, card: Card) -> set[str]:
        values = [
            card.vehicle_profile.customer_name,
            card.vehicle_profile.customer_phone,
            *list(card.vehicle_profile.customer_phones or []),
            card.repair_order.client,
            card.repair_order.phone,
        ]
        normalized_values: set[str] = set()
        seen_strings: set[str] = set()
        for value in values:
            if not value:
                continue
            if type(value) is str:
                if value in seen_strings:
                    continue
                seen_strings.add(value)
            normalized = self._normalize_search_text(value)
            if normalized:
                normalized_values.add(normalized)
            normalized_values.update(self._phone_match_keys(value))
        return normalized_values

    def _phone_match_keys(self, value: Any) -> set[str]:
        text = str(value or "")
        candidates = [text]
        candidates.extend(match.group(0) for match in _PHONE_PATTERN.finditer(text))
        candidates.extend(match.group(0) for match in re.finditer(r"\d[\d\s()+-]{7,}\d", text))
        keys: set[str] = set()
        for candidate in candidates:
            digits = re.sub(r"\D+", "", candidate)
            if len(digits) < 7:
                continue
            keys.add(digits)
            if len(digits) >= 10:
                last_ten = digits[-10:]
                keys.update((last_ten, "7" + last_ten, "8" + last_ten))
        return keys

    def _phone_search_variants(self, value: Any) -> set[str]:
        text = str(value or "")
        digits = re.sub(r"\D+", "", text)
        if len(digits) < 3:
            return set()
        variants = {digits}
        if len(digits) >= 4 and digits[0] in "78":
            variants.add(digits[1:])
        if len(digits) >= 10:
            last_ten = digits[-10:]
            variants.add(last_ten)
            if len(last_ten) >= 4 and last_ten[0] in "78":
                variants.add(last_ten[1:])
        return {variant for variant in variants if len(variant) >= 3}

    def _client_search_index_key(self, client: ClientProfile) -> tuple[Any, ...]:
        vehicle_keys = tuple(
            (
                vehicle.vehicle,
                vehicle.brand,
                vehicle.model,
                vehicle.vin,
                vehicle.license_plate,
                vehicle.year,
                vehicle.body_number,
                vehicle.chassis_number,
                vehicle.engine_code,
                vehicle.engine_model,
                vehicle.gearbox_model,
                vehicle.drivetrain,
            )
            for vehicle in client.vehicles
        )
        return (
            client.id,
            client.updated_at,
            client.client_type,
            client.last_name,
            client.first_name,
            client.middle_name,
            client.display_name,
            client.phone,
            tuple(client.phones),
            client.email,
            tuple(client.emails),
            client.inn,
            client.ogrn,
            client.contact_person,
            vehicle_keys,
        )

    def _client_search_index_for(
        self, clients: list[ClientProfile], *, signature: tuple[Any, ...] | None = None
    ) -> dict[str, dict[str, Any]]:
        if signature is None:
            signature = tuple(self._client_search_index_key(client) for client in clients)
        if signature == self._client_search_index_signature:
            return self._client_search_index

        index: dict[str, dict[str, Any]] = {}
        for client in clients:
            fields = [
                client.name(),
                client.full_name(),
                client.display_name,
                client.phone,
                " ".join(client.phones),
                client.email,
                " ".join(client.emails),
                client.inn,
                client.ogrn,
                client.contact_person,
            ]
            vehicle_fields: list[str] = []
            for vehicle in client.vehicles:
                vehicle_fields.extend(
                    [
                        vehicle.vehicle,
                        vehicle.brand,
                        vehicle.model,
                        self._client_vehicle_search_vin(vehicle.vin),
                        vehicle.license_plate,
                        vehicle.year,
                        vehicle.body_number,
                        vehicle.chassis_number,
                        vehicle.engine_code,
                        vehicle.engine_model,
                        vehicle.gearbox_model,
                        vehicle.drivetrain,
                    ]
                )
            searchable = [self._normalize_search_text(value) for value in fields if value]
            vehicle_searchable = [
                self._normalize_search_text(value) for value in vehicle_fields if value
            ]
            compact_searchable = [
                re.sub(r"[\W_]+", "", value)
                for value in [*searchable, *vehicle_searchable]
                if value
            ]
            compact_searchable = list(dict.fromkeys(compact_searchable))
            phone_variants: set[str] = set()
            for phone in [client.phone, *client.phones]:
                phone_variants.update(self._phone_search_variants(phone))
            index[client.id] = {
                "searchable": searchable,
                "searchable_counts": self._search_value_counts(searchable),
                "vehicle_searchable": vehicle_searchable,
                "vehicle_searchable_counts": self._search_value_counts(vehicle_searchable),
                "compact_searchable": compact_searchable,
                "digits_blob": self._client_search_digits_blob(client),
                "direct_digit_values": self._client_direct_digit_values(client),
                "match_keys": self._client_match_keys(client),
                "phone_variants": phone_variants,
            }

        self._client_search_index_signature = signature
        self._client_search_index = index
        return index

    def _search_value_counts(self, values: list[str]) -> dict[str, int]:
        return dict(Counter(values))

    def _client_vehicle_search_vin(self, value: Any) -> str:
        raw = normalize_text(value, default="", limit=160).upper()
        if not raw:
            return ""
        compact = re.sub(r"[^A-Z0-9]+", "", raw)
        if len(compact) < 6:
            return ""
        if len(set(compact)) <= 1:
            return ""
        return raw

    def _rank_client_matches(
        self,
        clients: list[ClientProfile],
        query: str,
        cards: list[Card] | None = None,
        *,
        client_search_index: dict[str, dict[str, Any]] | None = None,
        related_fields_by_client_id: dict[str, list[str]] | None = None,
        related_search_index_by_client_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[tuple[int, ClientProfile]]:
        query = normalize_text(query, default="", limit=500)
        if not query:
            return [(1, client) for client in self._ordered_clients(clients)]
        query_variants = self._search_text_variants(query)
        query_digits = re.sub(r"\D+", "", query)
        query_phone_variants = self._phone_search_variants(query)
        phone_like_query = bool(query_digits) and not re.search(r"[A-Za-zА-Яа-я]", query)
        client_signature = (
            tuple(self._client_search_index_key(client) for client in clients)
            if client_search_index is None or related_fields_by_client_id is None
            else None
        )
        if client_search_index is None:
            client_search_index = self._client_search_index_for(clients, signature=client_signature)
        if related_fields_by_client_id is None:
            related_fields_by_client_id = (
                self._client_related_vehicle_fields_index_for(
                    clients, cards, client_signature=client_signature
                )
                if cards
                else {}
            )
        if related_search_index_by_client_id is None:
            related_search_index_by_client_id = self._client_related_search_index(
                related_fields_by_client_id
            )

        if phone_like_query:
            return self._rank_client_matches_phone_like(
                clients,
                query_digits=query_digits,
                query_phone_variants=query_phone_variants,
                client_search_index=client_search_index,
                related_search_index_by_client_id=related_search_index_by_client_id,
                query_variants=query_variants,
            )

        return self._rank_client_matches_text(
            clients,
            query_variants=query_variants,
            query_digits=query_digits,
            query_phone_variants=query_phone_variants,
            client_search_index=client_search_index,
            related_search_index_by_client_id=related_search_index_by_client_id,
        )

    def _rank_client_matches_phone_like(
        self,
        clients: list[ClientProfile],
        *,
        query_digits: str,
        query_phone_variants: set[str],
        client_search_index: dict[str, dict[str, Any]],
        related_search_index_by_client_id: dict[str, dict[str, Any]],
        query_variants: list[str],
    ) -> list[tuple[int, ClientProfile]]:
        ranked: list[tuple[int, ClientProfile]] = []
        for client in clients:
            score = self._score_client_phone_like_match(
                client,
                query_digits=query_digits,
                query_phone_variants=query_phone_variants,
                client_search_index=client_search_index,
                related_search_index_by_client_id=related_search_index_by_client_id,
                query_variants=query_variants,
            )
            if score > 0:
                ranked.append((score, client))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True)
        return ranked

    def _score_client_phone_like_match(
        self,
        client: ClientProfile,
        *,
        query_digits: str,
        query_phone_variants: set[str],
        client_search_index: dict[str, dict[str, Any]],
        related_search_index_by_client_id: dict[str, dict[str, Any]],
        query_variants: list[str],
    ) -> int:
        score = 0
        indexed = client_search_index.get(client.id, {})
        digits_blob = indexed.get("digits_blob", "")
        direct_digit_values = indexed.get("direct_digit_values", set())
        if direct_digit_values:
            if query_digits in direct_digit_values:
                score += 1000
            elif any(
                query_digits in value or value in query_digits for value in direct_digit_values
            ):
                score += 200
            if query_phone_variants.intersection(direct_digit_values):
                score += 1000
        if digits_blob and query_digits in digits_blob:
            score += 10
        score += self._score_client_related_search_fields(
            related_search_index_by_client_id.get(client.id),
            query_variants=query_variants,
            query_digits=query_digits,
            query_phone_variants=query_phone_variants,
        )
        return score

    def _rank_client_matches_text(
        self,
        clients: list[ClientProfile],
        *,
        query_variants: list[str],
        query_digits: str,
        query_phone_variants: set[str],
        client_search_index: dict[str, dict[str, Any]],
        related_search_index_by_client_id: dict[str, dict[str, Any]],
    ) -> list[tuple[int, ClientProfile]]:
        ranked: list[tuple[int, ClientProfile]] = []
        for client in clients:
            score = self._score_client_text_match(
                client,
                query_variants=query_variants,
                query_digits=query_digits,
                query_phone_variants=query_phone_variants,
                client_search_index=client_search_index,
                related_search_index_by_client_id=related_search_index_by_client_id,
            )
            if score > 0:
                ranked.append((score, client))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True)
        return ranked

    def _score_client_text_match(
        self,
        client: ClientProfile,
        *,
        query_variants: list[str],
        query_digits: str,
        query_phone_variants: set[str],
        client_search_index: dict[str, dict[str, Any]],
        related_search_index_by_client_id: dict[str, dict[str, Any]],
    ) -> int:
        indexed = client_search_index.get(client.id, {})
        searchable = indexed.get("searchable", [])
        vehicle_searchable = indexed.get("vehicle_searchable", [])
        compact_searchable = indexed.get("compact_searchable", [])
        searchable_counts = indexed.get("searchable_counts")
        vehicle_searchable_counts = indexed.get("vehicle_searchable_counts")
        score = 0
        score += self._score_client_text_variants(
            query_variants,
            searchable=searchable,
            vehicle_searchable=vehicle_searchable,
            compact_searchable=compact_searchable,
            searchable_counts=searchable_counts,
            vehicle_searchable_counts=vehicle_searchable_counts,
        )
        if len(query_digits) >= 4:
            phone_digits = " ".join(re.sub(r"\D+", "", phone) for phone in client.phones)
            if query_digits in phone_digits:
                score += 10
        if query_phone_variants:
            client_phone_variants = indexed.get("phone_variants", set())
            if client_phone_variants and any(
                query_variant in client_variant or client_variant in query_variant
                for query_variant in query_phone_variants
                for client_variant in client_phone_variants
            ):
                score += 10
            elif query_phone_variants.intersection(indexed.get("match_keys", set())):
                score += 10
        score += self._score_client_related_search_fields(
            related_search_index_by_client_id.get(client.id),
            query_variants=query_variants,
            query_digits=query_digits,
            query_phone_variants=query_phone_variants,
        )
        return score

    def _score_client_text_variants(
        self,
        query_variants: list[str],
        *,
        searchable: list[str],
        vehicle_searchable: list[str],
        compact_searchable: list[str],
        searchable_counts: dict[str, int] | None = None,
        vehicle_searchable_counts: dict[str, int] | None = None,
    ) -> int:
        score = 0
        for variant in query_variants:
            if not variant:
                continue
            score += self._score_client_text_variant(
                variant,
                searchable=searchable,
                vehicle_searchable=vehicle_searchable,
                compact_searchable=compact_searchable,
                searchable_counts=searchable_counts,
                vehicle_searchable_counts=vehicle_searchable_counts,
            )
        return score

    def _score_client_text_variant(
        self,
        variant: str,
        *,
        searchable: list[str],
        vehicle_searchable: list[str],
        compact_searchable: list[str],
        searchable_counts: dict[str, int] | None = None,
        vehicle_searchable_counts: dict[str, int] | None = None,
    ) -> int:
        score = 0
        parts, compact_variant = _prepared_search_variant(variant)
        searchable_values = (
            searchable_counts.items()
            if searchable_counts is not None
            else ((value, 1) for value in searchable)
        )
        for value, count in searchable_values:
            if value == variant:
                score += 8 * count
            elif variant in value:
                score += 4 * count
            elif all(part in value for part in parts):
                score += 2 * count
        vehicle_values = (
            vehicle_searchable_counts.items()
            if vehicle_searchable_counts is not None
            else ((value, 1) for value in vehicle_searchable)
        )
        for value, count in vehicle_values:
            if value == variant:
                score += 7 * count
            elif variant in value:
                score += 5 * count
            elif all(part in value for part in parts):
                score += 3 * count
        if compact_variant and any(compact_variant in value for value in compact_searchable):
            score += 5
        return score
