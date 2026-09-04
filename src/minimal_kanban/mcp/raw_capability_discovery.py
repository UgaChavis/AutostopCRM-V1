from __future__ import annotations

import re
from collections.abc import Mapping

_RAW_CAPABILITY_DISCOVERY_ALIASES: dict[str, tuple[str, ...]] = {
    "recommend_automotive_sources": (
        "грм",
        "метки",
        "фазы",
        "цепь",
        "ремень",
        "timing belt",
        "timing chain",
        "момент затяжки",
        "torque",
        "dtc",
        "ошибка",
        "электросхема",
        "tsb",
        "отзыв",
        "масло",
        "жидкость",
        "допуск",
        "вязкость",
        "объем",
        "объём",
        "заправочная емкость",
        "заправочная ёмкость",
        "акпп",
        "техническое обслуживание",
        "то",
    ),
    "lookup_public_automotive_evidence": (
        "отзывная кампания",
        "отзыв производителя",
        "отзыв автомобиля",
        "официальный отзыв",
        "recall status",
        "tsb metadata",
        "manufacturer communication",
    ),
    "search_web_multi": (
        "интернет",
        "форум",
        "найди",
        "исследуй",
        "web search",
    ),
    "research_drive2_cases": (
        "drive2",
        "драйв2",
        "бортжурнал",
        "журнал ремонта",
        "форумные кейсы",
        "реальные случаи ремонта",
    ),
}

_RAW_CAPABILITY_DISCOVERY_STOPWORDS = frozenset(
    {
        "и",
        "или",
        "в",
        "во",
        "на",
        "по",
        "для",
        "как",
        "что",
        "какой",
        "какие",
        "про",
        "the",
        "and",
        "or",
        "for",
        "with",
    }
)


def _discovery_phrase(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _discovery_tokens(value: object) -> set[str]:
    token_source = _discovery_phrase(value).replace("_", " ")
    return {
        token
        for token in re.findall(r"[\w-]+", token_source, flags=re.UNICODE)
        if len(token) >= 3 or token in {"то", "tsb", "dtc", "hv"}
    } - _RAW_CAPABILITY_DISCOVERY_STOPWORDS


def _schema_discovery_terms(schema: object) -> str:
    if not isinstance(schema, Mapping):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    return " ".join(str(name).replace("_", " ") for name in properties)


def discovery_phrase(value: object) -> str:
    return _discovery_phrase(value)


def raw_capability_discovery_score(
    query: str,
    *,
    name: str,
    description: str,
    schema: object,
) -> tuple[int, list[str], bool]:
    """Score one raw capability for a conservative natural-language search."""
    normalized_query = _discovery_phrase(query)
    normalized_name = _discovery_phrase(name)
    virtual_operation_name = (
        _discovery_phrase(name.rsplit("/", 1)[-1]) if name.startswith("api:") else ""
    )
    if normalized_query and normalized_query in {normalized_name, virtual_operation_name}:
        return 1_000, [name], True
    if not normalized_query:
        return 0, [], False

    aliases = _RAW_CAPABILITY_DISCOVERY_ALIASES.get(name, ())
    normalized_aliases = tuple(_discovery_phrase(alias) for alias in aliases)
    name_tokens = _discovery_tokens(name.replace("_", " "))
    alias_tokens = _discovery_tokens(" ".join(normalized_aliases))
    schema_tokens = _discovery_tokens(_schema_discovery_terms(schema))
    description_tokens = _discovery_tokens(description)
    query_tokens = _discovery_tokens(normalized_query)
    matched_terms: list[str] = []
    score = 0

    for alias in normalized_aliases:
        if alias and alias in normalized_query:
            score += 36 if " " in alias else 24
            matched_terms.append(alias)

    for token in query_tokens:
        if token in name_tokens:
            score += 18
            matched_terms.append(token)
        elif token in alias_tokens:
            score += 14
            matched_terms.append(token)
        elif token in schema_tokens:
            score += 8
            matched_terms.append(token)
        elif token in description_tokens:
            score += 4
            matched_terms.append(token)

    return score, list(dict.fromkeys(matched_terms)), False
