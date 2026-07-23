from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from .web_tools import DuckDuckGoSearchClient

_DRIVE2_DOMAIN = "drive2.ru"
_DRIVE2_PATH = "/l/"
_CACHE_TTL_SECONDS = 15 * 60
_CACHE_MAX_ENTRIES = 96
_MAX_CASES = 5
_MAX_QUERIES = 3
_EVIDENCE_MAX_CHARS = 720
_EVIDENCE_KEYWORDS = (
    "диагност",
    "ошиб",
    "причин",
    "обнаруж",
    "замен",
    "ремонт",
    "итог",
    "результ",
    "решен",
    "устран",
    "пробег",
)
_HUMAN_ONLY_FLAGS = frozenset({"captcha_required", "ip_blocked", "access_denied", "js_challenge"})


class Drive2CaseResearch:
    """Bounded public Drive2 case research without account access or raw-page retention."""

    _cache_lock = threading.RLock()
    _cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def __init__(self, search: DuckDuckGoSearchClient) -> None:
        self._search = search

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()

    def research(
        self,
        *,
        query: str,
        vehicle: str = "",
        engine: str = "",
        transmission: str = "",
        dtc_codes: list[str] | None = None,
        max_cases: int = 3,
    ) -> dict[str, Any]:
        request = self._normalize_request(
            query=query,
            vehicle=vehicle,
            engine=engine,
            transmission=transmission,
            dtc_codes=dtc_codes,
            max_cases=max_cases,
        )
        cache_key = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cached = self._cache_get(cache_key)
        if cached is not None:
            cached["cache"] = {"hit": True, "ttl_seconds": _CACHE_TTL_SECONDS}
            return cached

        query_plan = self._query_plan(request)
        candidates: dict[str, dict[str, Any]] = {}
        provider_attempts: list[dict[str, Any]] = []
        for search_query in query_plan:
            response = self._search.search_multi(
                search_query,
                limit=10,
                allowed_domains=[_DRIVE2_DOMAIN],
            )
            provider_attempts.extend(
                dict(item) for item in response.get("providers", []) if isinstance(item, dict)
            )
            for item in response.get("results", []):
                if not isinstance(item, dict) or not self._is_drive2_logbook(item.get("url")):
                    continue
                url = str(item["url"])
                current = candidates.get(url)
                if current is None:
                    candidates[url] = dict(item)
                else:
                    current["snippet"] = self._prefer_text(
                        str(current.get("snippet") or ""), str(item.get("snippet") or "")
                    )

        ranked = sorted(
            candidates.values(),
            key=lambda item: (-self._candidate_score(item, request), str(item.get("url") or "")),
        )
        cases: list[dict[str, Any]] = []
        for candidate in ranked[: request["max_cases"]]:
            page = self._search.fetch_page_excerpt(str(candidate["url"]), max_chars=8000)
            cases.append(self._case_from_page(candidate, page, request))

        result = {
            "ok": True,
            "source": "Drive2 public logbooks",
            "request": {key: value for key, value in request.items() if key not in {"max_cases"}},
            "query_plan": query_plan,
            "candidate_count": len(candidates),
            "cases": cases,
            "provider_attempts": provider_attempts,
            "limits": {
                "max_queries": _MAX_QUERIES,
                "max_cases": _MAX_CASES,
                "public_only": True,
                "account_access": "not_used",
                "raw_page_retention": "not_persisted",
            },
            "safety": {
                "forum_evidence": "hypotheses_and_practical_cases_only",
                "requires_oem_or_licensed_confirmation_for": [
                    "torque",
                    "timing_procedure",
                    "wiring",
                    "fluid_specification",
                    "programming",
                    "adas_srs_hv",
                    "exact_fitment",
                ],
            },
            "cache": {"hit": False, "ttl_seconds": _CACHE_TTL_SECONDS},
        }
        self._cache_put(cache_key, result)
        return deepcopy(result)

    def _normalize_request(
        self,
        *,
        query: str,
        vehicle: str,
        engine: str,
        transmission: str,
        dtc_codes: list[str] | None,
        max_cases: int,
    ) -> dict[str, Any]:
        normalized_codes = [
            re.sub(r"\s+", "", str(code or "").upper())[:16]
            for code in (dtc_codes or [])
            if str(code or "").strip()
        ][:8]
        return {
            "query": self._text(query, 480),
            "vehicle": self._text(vehicle, 240),
            "engine": self._text(engine, 80),
            "transmission": self._text(transmission, 80),
            "dtc_codes": normalized_codes,
            "max_cases": max(1, min(int(max_cases), _MAX_CASES)),
        }

    def _query_plan(self, request: dict[str, Any]) -> list[str]:
        context = " ".join(
            part
            for part in (
                request["vehicle"],
                request["engine"],
                request["transmission"],
                " ".join(request["dtc_codes"]),
                request["query"],
            )
            if part
        ).strip()
        base = f"site:drive2.ru/l/ {context}".strip()
        variants = [base, f"{base} причина ремонт", f"{base} итог решение"]
        return list(dict.fromkeys(variants))[:_MAX_QUERIES]

    def _case_from_page(
        self, candidate: dict[str, Any], page: dict[str, Any], request: dict[str, Any]
    ) -> dict[str, Any]:
        excerpt = str(page.get("excerpt") or "")
        flags = [str(flag) for flag in page.get("access_flags", []) if str(flag)]
        article_available = bool(excerpt.strip())
        human_required = bool(set(flags).intersection(_HUMAN_ONLY_FLAGS)) or not article_available
        title = self._title(excerpt) or str(candidate.get("title") or "")
        return {
            "title": title,
            "url": str(candidate.get("url") or ""),
            "vehicle_hint": self._vehicle_hint(excerpt),
            "published_hint": self._published_hint(excerpt),
            "relevance_score": self._candidate_score(
                {**candidate, "snippet": f"{candidate.get('snippet', '')} {excerpt[:3500]}"},
                request,
            ),
            "evidence": self._evidence(excerpt, fallback=str(candidate.get("snippet") or "")),
            "access": {
                "article_available": article_available,
                "comments_limited": "login_required" in flags,
                "requires_human": human_required,
                "flags": flags,
                "engine": str(page.get("engine") or ""),
            },
            "source_kind": "public_forum_case",
            "confidence": "low_to_medium",
        }

    @staticmethod
    def _is_drive2_logbook(url: object) -> bool:
        parsed = urlparse(str(url or ""))
        host = str(parsed.hostname or "").casefold()
        return (
            host == _DRIVE2_DOMAIN or host.endswith(f".{_DRIVE2_DOMAIN}")
        ) and parsed.path.startswith(_DRIVE2_PATH)

    @staticmethod
    def _candidate_score(candidate: dict[str, Any], request: dict[str, Any]) -> int:
        haystack = " ".join(
            str(candidate.get(key) or "") for key in ("title", "snippet", "url")
        ).casefold()
        score = 0
        for token in re.findall(
            r"[\w-]+",
            " ".join(
                [request["query"], request["vehicle"], request["engine"], request["transmission"]]
            ).casefold(),
        ):
            if len(token) >= 3 and token in haystack:
                score += 4
        for code in request["dtc_codes"]:
            if code.casefold() in haystack:
                score += 10
        for marker in ("итог", "результ", "причин", "ремонт", "замен"):
            if marker in haystack:
                score += 2
        return score

    @staticmethod
    def _title(text: str) -> str:
        match = re.search(r"(?m)^#\s+(.{1,220})$", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _vehicle_hint(text: str) -> str:
        match = re.search(r"(?m)^\[\s*([^\]]{3,240})\s*\]", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _published_hint(text: str) -> str:
        match = re.search(
            r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b",
            text,
            flags=re.I,
        )
        return match.group(0) if match else ""

    @staticmethod
    def _evidence(text: str, *, fallback: str) -> list[str]:
        compact = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", compact)
        selected = [
            sentence.strip()
            for sentence in sentences
            if any(marker in sentence.casefold() for marker in _EVIDENCE_KEYWORDS)
        ]
        if not selected and fallback.strip():
            selected = [fallback.strip()]
        evidence: list[str] = []
        used = 0
        for sentence in selected:
            normalized = sentence[:280].strip()
            if not normalized or normalized in evidence:
                continue
            if used + len(normalized) > _EVIDENCE_MAX_CHARS:
                break
            evidence.append(normalized)
            used += len(normalized)
            if len(evidence) >= 3:
                break
        return evidence

    @classmethod
    def _cache_get(cls, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with cls._cache_lock:
            item = cls._cache.get(key)
            if item is None:
                return None
            created_at, value = item
            if now - created_at >= _CACHE_TTL_SECONDS:
                cls._cache.pop(key, None)
                return None
            cls._cache.move_to_end(key)
            return deepcopy(value)

    @classmethod
    def _cache_put(cls, key: str, value: dict[str, Any]) -> None:
        with cls._cache_lock:
            cls._cache[key] = (time.monotonic(), deepcopy(value))
            cls._cache.move_to_end(key)
            while len(cls._cache) > _CACHE_MAX_ENTRIES:
                cls._cache.popitem(last=False)

    @staticmethod
    def _text(value: object, max_length: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]

    @staticmethod
    def _prefer_text(first: str, second: str) -> str:
        return second if len(second.strip()) > len(first.strip()) else first
