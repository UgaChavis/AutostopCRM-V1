"""Detached mutation inputs; JsonStore still owns locking, CAS and persistence."""

from copy import copy, deepcopy


class DraftCards(list):
    def __init__(self, values, source):
        super().__init__(values)
        self.shared = {id(item) for item in source}


class DraftEvents(list):
    """New audit details stay in memory until all domain validation completes."""


class BundleDraft(dict):
    def __init__(self, source: dict, *, domains=(), card_id=None, client_id=None, signature=None):
        super().__init__(
            (name, list(value) if isinstance(value, list) else deepcopy(value))
            for name, value in source.items()
        )
        self.source = source
        self.signature = signature
        for name in domains:
            self[name] = deepcopy(source[name])
        if card_id is not None:
            selected = {str(card_id)}
            if "cards" not in domains:
                self["cards"] = [
                    deepcopy(card) if card.id in selected else card for card in source["cards"]
                ]
            linked_ids = {card.client_id for card in self["cards"] if card.id in selected}
        else:
            linked_ids = set()
        if client_id is not None:
            linked_ids.add(str(client_id))
        if linked_ids and "clients" not in domains:
            self["clients"] = [
                deepcopy(client) if client.id in linked_ids else client
                for client in source["clients"]
            ]
        self["cards"] = DraftCards(self["cards"], source["cards"])
        self["events"] = DraftEvents(self["events"])


def detach_card(cards: list, card):
    """Copy a neighbour before changing only its ordering/numbering fields."""
    if not isinstance(cards, DraftCards) or id(card) not in cards.shared:
        return card
    detached = copy(card)
    for index, item in enumerate(cards):
        if item is card:
            cards[index] = detached
            return detached
    return card
