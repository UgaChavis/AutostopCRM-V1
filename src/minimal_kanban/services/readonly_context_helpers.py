"""Small helpers for detached, checked context responses."""

from copy import deepcopy

from ..models import parse_datetime
from ..storage.json_store import StateFileCorruptedError


def _retention_choice_key(card):
    if not card.archived or not card.repair_order.is_empty():
        return (0,)
    updated_at = parse_datetime(card.updated_at) or parse_datetime(card.created_at)
    if updated_at is None:
        raise StateFileCorruptedError("Недостоверная дата карточки в снимке поиска.")
    return (1, updated_at)


def _least_retention_pressure(cards):
    # The oldest plain archive entry, or a non-plain entry, leaves the most
    # archive slots available for every unrelated client simultaneously.
    selected = {}
    for card in cards:
        previous = selected.get(card.id)
        if previous is None or _retention_choice_key(card) < _retention_choice_key(previous):
            selected[card.id] = card
    return list(selected.values())


def _search_card_clients(service, card, related, client_ids_by_key):
    if card.client_id:
        return {card.client_id} if card.client_id in related else set()
    matching = set()
    for key in service._card_client_values(card):
        matching.update(client_ids_by_key.get(key, set()))
    return matching


def client_readonly_related_cards(service, bundle, clients, cards):
    related = {client.id: [] for client in clients}
    if not related:
        return related
    client_ids_by_key = service._client_ids_by_match_key(clients)
    if bundle.search_cards_need_retention:
        for card in cards:
            if _search_card_clients(service, card, related, client_ids_by_key):
                bundle.require_card_identity(card)
        # Retention is pure; positions are not used by client search or stats.
        retained, _ = service._store._apply_card_retention(cards)
        least_pressure, _ = service._store._apply_card_retention(_least_retention_pressure(cards))

        def related_ids(rows):
            return {
                card.id
                for card in rows
                if _search_card_clients(service, card, related, client_ids_by_key)
            }

        if related_ids(retained) != related_ids(least_pressure):
            raise StateFileCorruptedError("Дубли карточек влияют на результаты архива клиента.")
        cards = retained
    for card in cards:
        matching = _search_card_clients(service, card, related, client_ids_by_key)
        if not matching:
            continue
        # Validate against every raw card with this ID before any deduplication.
        bundle.require_card_identity(card)
        for client_id in matching:
            related[client_id].append(card)
    for cards_for_client in related.values():
        cards_for_client.sort(
            key=lambda item: (item.updated_at, item.created_at, item.id), reverse=True
        )
    return related


def prepare_card_context(service, payload):
    include_text = service._validated_optional_bool(
        payload, "include_repair_order_text", default=True
    )
    read_only = service._validated_optional_bool(payload, "read_only", default=False)
    strict_links = service._validated_optional_bool(payload, "strict_client_links", default=False)
    snapshot_read = read_only or strict_links or not include_text
    bundle = (
        service._store._read_context_bundle_snapshot(
            domains=("clients", "stickies", "events", "settings")
        )
        if snapshot_read
        else service._store.read_bundle()
    )
    return bundle, include_text, snapshot_read, strict_links


def ranked_context_clients(service, card, clients):
    search_parts = (
        card.vehicle_profile.customer_name,
        card.vehicle_profile.customer_phone,
        *list(card.vehicle_profile.customer_phones or []),
        card.repair_order.client,
        card.repair_order.phone,
    )
    if not any(search_parts):
        return []
    return service._rank_client_matches(clients, " ".join(part for part in search_parts if part))[
        :5
    ]


def validate_context_relations(
    service, bundle, events, event_limit, linked_client, matches, read_warnings, strict_links
):
    for event in events[:event_limit]:
        bundle.require_event(event)
    related_by_client = {}
    for client in [linked_client, *(client for _, client in matches)]:
        if client is not None:
            bundle.require_client(client)
            related = service._client_related_cards(client, bundle["cards"])
            for card in related:
                warning = "related_card_client_match_unverified"
                if card.client_id != client.id and warning not in read_warnings:
                    read_warnings.append(warning)
            if strict_links:
                related = [card for card in related if card.client_id == client.id]
            for card in related:
                bundle.require_card(card)
                if strict_links:
                    bundle.require_card_client_links(card)
            related_by_client[client.id] = related
    return related_by_client


def serialize_context_client(service, client, bundle, related_by_client, *, compact=False):
    if client is None:
        return None
    return service._serialize_client(
        client,
        bundle["cards"],
        include_stats=True,
        compact=compact,
        related_cards=related_by_client.get(client.id),
    )


def checked_card_warnings(bundle, card):
    bundle.require_card(card, include_attachments=True)
    bundle.require_card_client_links(card)
    return bundle.card_warnings(card)


def maybe_card_warnings(bundle, card, read_only):
    return checked_card_warnings(bundle, card) if read_only else []


def detached_card_response(card, read_warnings):
    result = {"card": card}
    if read_warnings:
        result["read_warnings"] = read_warnings
    return deepcopy(result)
