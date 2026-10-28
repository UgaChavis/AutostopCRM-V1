"""Fail closed if compact client search loses an ID or relation warning."""


def _valid_id(value):
    return isinstance(value, str) and bool(value) and value == value.strip() and len(value) <= 128


def _preserves_list(source, compact, field, identity_fields):
    original = source.get(field)
    result = compact.get(field)
    if not isinstance(original, list) or not isinstance(result, list):
        return False
    for index, item in enumerate(result):
        if isinstance(item, dict) and set(item) == {"truncated_items"}:
            return index == len(result) - 1 and item["truncated_items"] == len(original) - index
        if (
            index >= len(original)
            or not isinstance(item, dict)
            or not isinstance(original[index], dict)
        ):
            return False
        if field == "related_card_candidates":
            if (
                original[index].get("client_relation") not in {"confirmed", "unverified"}
                or original[index].get("vehicle_relation") != "unverified"
                or not _valid_id(original[index].get("card_id"))
                or "id" in original[index]
            ):
                return False
        elif not _valid_id(original[index].get("id")):
            return False
        if any(item.get(name) != original[index].get(name) for name in identity_fields):
            return False
    return len(result) == len(original)


def compact_search_items(ok, items, error, compact, limit):
    if not ok:
        return False, [], error
    result = compact(items, item_limit=max(25, limit))
    valid = isinstance(result, list) and len(result) == len(items)
    if valid:
        for source, row in zip(items, result, strict=True):
            warnings = source.get("read_warnings")
            if (
                not isinstance(row, dict)
                or not _valid_id(source.get("id"))
                or row.get("id") != source["id"]
                or not isinstance(warnings, list)
                or any(not isinstance(warning, str) for warning in warnings)
                or row.get("read_warnings") != warnings
                or not _preserves_list(source, row, "vehicles_preview", ("id",))
                or not _preserves_list(source, row, "vehicles", ("id",))
                or not _preserves_list(
                    source,
                    row,
                    "related_card_candidates",
                    ("client_relation", "vehicle_relation", "card_id"),
                )
            ):
                valid = False
                break
            if (
                any(
                    candidate.get("client_relation") == "unverified"
                    for candidate in source["related_card_candidates"]
                    if isinstance(candidate, dict)
                )
                and "related_card_client_match_unverified" not in warnings
            ):
                valid = False
                break
    if not valid:
        return (
            False,
            [],
            {
                "code": "client_search_response_truncated",
                "message": "Ответ поиска клиентов не удалось безопасно сократить.",
            },
        )
    return True, result, error


def page(limit, entity, data, ok):
    meta = data.get("meta") if ok and entity == "client" and isinstance(data, dict) else None
    return {
        "limit": limit,
        "has_more": bool(meta.get("has_more")) if isinstance(meta, dict) else False,
    }
