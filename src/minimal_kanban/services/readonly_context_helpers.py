"""Small helpers for detached, checked context responses."""

from copy import deepcopy


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
    service, bundle, events, event_limit, linked_client, matches, read_warnings
):
    for event in events[:event_limit]:
        bundle.require_event(event)
    for client in [linked_client, *(client for _, client in matches)]:
        if client is not None:
            bundle.require_client(client)
            for card in service._client_related_cards(client, bundle["cards"]):
                bundle.require_card(card)
                warning = "related_card_client_match_unverified"
                if card.client_id != client.id and warning not in read_warnings:
                    read_warnings.append(warning)


def checked_card_warnings(bundle, card):
    bundle.require_card(card, include_attachments=True)
    return bundle.card_warnings(card)


def maybe_card_warnings(bundle, card, read_only):
    return checked_card_warnings(bundle, card) if read_only else []


def detached_card_response(card, read_warnings):
    result = {"card": card}
    if read_warnings:
        result["read_warnings"] = read_warnings
    return deepcopy(result)
