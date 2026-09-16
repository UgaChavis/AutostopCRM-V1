from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.automotive_tools import (  # noqa: E402
    AutomotiveLookupService,
    InternetToolError,
)
from minimal_kanban.agent.source_registry import public_part_evidence_domains  # noqa: E402
from minimal_kanban.agent.tools import AgentToolExecutor, _json_dumps  # noqa: E402


class _FakeSearchClient:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []
        self.search_multi_calls: list[dict[str, object]] = []
        self.search_multi_payload: dict[str, object] | None = None
        self.fetch_calls: list[dict[str, object]] = []
        self.browser_fetch_calls: list[dict[str, object]] = []

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
    ) -> list[object]:
        self.search_calls.append(
            {"query": query, "limit": limit, "allowed_domains": allowed_domains or []}
        )
        return []

    def search_multi(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        providers: list[str] | None = None,
    ) -> dict[str, object]:
        self.search_multi_calls.append(
            {
                "query": query,
                "limit": limit,
                "allowed_domains": allowed_domains or [],
                "providers": providers or [],
            }
        )
        payload = dict(self.search_multi_payload or {"results": [], "providers": []})
        payload.setdefault("query", query)
        return payload

    def fetch_page_excerpt(self, url: str, *, max_chars: int = 2500) -> dict[str, object]:
        self.fetch_calls.append({"url": url, "max_chars": max_chars})
        return {"url": url, "excerpt": "x" * max_chars}

    def fetch_page_browser(
        self, url: str, *, max_chars: int = 2500, wait_ms: int = 750
    ) -> dict[str, object]:
        self.browser_fetch_calls.append({"url": url, "max_chars": max_chars, "wait_ms": wait_ms})
        return {"ok": True, "url": url, "excerpt": "x" * max_chars, "wait_ms": wait_ms}


def _service_with_fake_search() -> tuple[AutomotiveLookupService, _FakeSearchClient]:
    service = AutomotiveLookupService()
    fake_search = _FakeSearchClient()
    service._search = fake_search  # type: ignore[assignment]
    return service, fake_search


def _client_factory(payload: object):
    if isinstance(payload, bytes):
        raw_payload = payload
    else:
        raw_payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, *, chunk_size=None):
            _ = chunk_size
            yield raw_payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, *args, **kwargs) -> _Response:
            return _Response()

    return _Client


class AutomotiveLookupServiceTests(unittest.TestCase):
    def test_cache_key_sanitizes_non_finite_numbers(self) -> None:
        service = AutomotiveLookupService()

        key = service._cache_key("lookup", {"value": math.nan, "items": [math.inf, -math.inf]})
        sanitized_key = service._cache_key("lookup", {"value": None, "items": [None, None]})

        self.assertNotIn("NaN", key)
        self.assertNotIn("Infinity", key)
        self.assertEqual(key, sanitized_key)

    def test_constructor_normalizes_non_finite_and_boolean_timeouts(self) -> None:
        non_finite = AutomotiveLookupService(timeout_seconds=math.inf)
        boolean = AutomotiveLookupService(timeout_seconds=True)

        self.assertEqual(non_finite._timeout_seconds, 12.0)
        self.assertEqual(boolean._timeout_seconds, 12.0)
        self.assertEqual(non_finite._search._timeout_seconds, 12.0)

    def test_web_tools_fall_back_for_non_finite_limits(self) -> None:
        service, fake_search = _service_with_fake_search()

        service.search_web(query="oil filter", limit=float("inf"))
        service.search_web_multi(query="gearbox issue", limit=float("inf"))
        service.fetch_page_excerpt(url="https://example.com/specs", max_chars=float("inf"))
        service.fetch_page_browser(
            url="https://example.com/rendered",
            max_chars=float("inf"),
            wait_ms=float("inf"),
        )

        self.assertEqual(fake_search.search_calls[0]["limit"], 5)
        self.assertEqual(fake_search.search_multi_calls[0]["limit"], 5)
        self.assertEqual(fake_search.fetch_calls[0]["max_chars"], 2500)
        self.assertEqual(fake_search.browser_fetch_calls[0]["max_chars"], 2500)
        self.assertEqual(fake_search.browser_fetch_calls[0]["wait_ms"], 750)

    def test_web_tools_fall_back_for_boolean_and_fractional_limits(self) -> None:
        service, fake_search = _service_with_fake_search()

        service.search_web(query="oil filter", limit=True)
        service.search_web(query="air filter", limit=1.5)  # type: ignore[arg-type]
        service.search_web_multi(query="spark plugs", limit=True, providers=["brave", "ddg"])
        service.fetch_page_excerpt(url="https://example.com/specs", max_chars=True)
        service.fetch_page_browser(
            url="https://example.com/rendered",
            max_chars=True,
            wait_ms=True,
        )

        self.assertEqual(fake_search.search_calls[0]["limit"], 5)
        self.assertEqual(fake_search.search_calls[1]["limit"], 5)
        self.assertEqual(fake_search.search_multi_calls[0]["limit"], 5)
        self.assertEqual(fake_search.search_multi_calls[0]["providers"], ["brave", "ddg"])
        self.assertEqual(fake_search.fetch_calls[0]["max_chars"], 2500)
        self.assertEqual(fake_search.browser_fetch_calls[0]["max_chars"], 2500)
        self.assertEqual(fake_search.browser_fetch_calls[0]["wait_ms"], 750)

    def test_part_lookup_clamps_large_limits_before_searching(self) -> None:
        service, fake_search = _service_with_fake_search()

        service.search_part_numbers(vehicle_context={}, part_query="масляный фильтр", limit=1e308)

        self.assertTrue(fake_search.search_multi_calls)
        self.assertTrue(all(call["limit"] == 12 for call in fake_search.search_multi_calls))

    def test_price_summary_ignores_unbounded_ruble_amounts(self) -> None:
        service = AutomotiveLookupService()

        self.assertIsNone(service._rub_amount("1e308", "руб"))
        self.assertEqual(service._rub_amount("12500", "руб"), 12500)

    def test_part_lookup_falls_back_for_fractional_limit_before_searching(self) -> None:
        service, fake_search = _service_with_fake_search()

        service.search_part_numbers(
            vehicle_context={},
            part_query="масляный фильтр",
            limit=1.5,  # type: ignore[arg-type]
        )

        self.assertTrue(fake_search.search_multi_calls)
        self.assertTrue(all(call["limit"] == 8 for call in fake_search.search_multi_calls))

    def test_part_evidence_gateway_is_vin_safe_and_fetches_only_static_sources(self) -> None:
        service, fake_search = _service_with_fake_search()
        vin = "WBA/000000/00000000"
        self.assertEqual(
            public_part_evidence_domains(),
            ["partsouq.com", "amayama.com", "emex.ru", "exist.ru"],
        )
        fake_search.search_multi_payload = {
            "results": [
                {
                    "title": f"Ford 1712024 {vin}",
                    "url": "https://partsouq.com/catalog/1712024",
                    "snippet": f"Front brake pads {vin}",
                    "domain": "partsouq.com",
                    "provider": "searxng",
                },
                {
                    "title": "Legacy MegaZip 1712024",
                    "url": "https://megazip.net/catalog/1712024",
                    "snippet": "Legacy catalog result",
                    "domain": "megazip.net",
                    "provider": "searxng",
                },
            ],
            "provider_order": ["searxng", "duckduckgo"],
            "providers": [{"provider": "searxng", "status": "success"}],
            "fallback_used": False,
        }

        result = service.research_part_public_evidence(
            query=f"{vin} Ford 1712024 front brake pads",
            allowed_domains=["https://www.partsouq.com", "megazip.net", "untrusted.example"],
            max_pages=2,
        )

        rendered = json.dumps(result, ensure_ascii=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["contract_version"], "autostop.web-research.v1")
        self.assertTrue(result["vin_redacted"])
        self.assertFalse(result["fitment_confirmed"])
        self.assertEqual(result["next_step"], "confirm_with_vin_specific_epc")
        self.assertEqual(result["allowed_domains"], ["partsouq.com"])
        self.assertEqual(result["rejected_domains"], ["megazip.net", "untrusted.example"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["source_id"], "partsouq_catalog")
        self.assertEqual(result["results"][0]["source_type"], "oem_catalog")
        self.assertTrue(result["results"][0]["source_authorized"])
        self.assertNotIn(vin, rendered)
        self.assertNotIn("WBA00000000000000", rendered)
        self.assertNotIn(vin, str(fake_search.search_multi_calls[0]["query"]))
        self.assertEqual(
            fake_search.fetch_calls,
            [{"url": "https://partsouq.com/catalog/1712024", "max_chars": 1200}],
        )

    def test_existing_part_lookup_keeps_its_fields_and_uses_vin_safe_gateway(self) -> None:
        service, fake_search = _service_with_fake_search()
        vin = "WBA00000000000000"
        fake_search.search_multi_payload = {
            "results": [
                {
                    "title": "Ford 1712024",
                    "url": "https://partsouq.com/catalog/1712024",
                    "snippet": "Front brake pads",
                    "domain": "partsouq.com",
                    "provider": "searxng",
                }
            ],
            "providers": [],
        }

        result = service.search_part_numbers(
            vehicle_context={"vin": vin, "make": "Ford", "model": f"Focus {vin}"},
            part_query=f"передние тормозные колодки {vin}",
        )

        self.assertEqual(
            set(result).intersection(
                {
                    "vehicle_context",
                    "part_query",
                    "query_variants",
                    "results",
                    "part_numbers",
                    "source_group",
                }
            ),
            {
                "vehicle_context",
                "part_query",
                "query_variants",
                "results",
                "part_numbers",
                "source_group",
            },
        )
        self.assertEqual(result["vehicle_context"]["vin"], "[vin-redacted]")
        self.assertEqual(result["part_query"], "передние тормозные колодки")
        self.assertEqual(result["web_research"]["contract_version"], "autostop.web-research.v1")
        self.assertNotIn(vin, json.dumps(result, ensure_ascii=False))
        self.assertNotIn(vin, str(fake_search.search_multi_calls[0]["query"]))

    def test_decode_vin_treats_non_object_json_as_empty_decode(self) -> None:
        service = AutomotiveLookupService()

        with patch("minimal_kanban.agent.automotive_tools.httpx.Client", _client_factory([])):
            decoded = service.decode_vin("JSAZC72S001234567")

        self.assertEqual(decoded["vin"], "JSAZC72S001234567")
        self.assertEqual(decoded["make"], "")
        self.assertEqual(decoded["source"], "NHTSA vPIC")

    def test_decode_vin_wraps_invalid_json_as_internet_tool_error(self) -> None:
        service = AutomotiveLookupService()

        with patch(
            "minimal_kanban.agent.automotive_tools.httpx.Client",
            _client_factory(b"{broken"),
        ):
            with self.assertRaises(InternetToolError):
                service.decode_vin("JSAZC72S001234567")

    def test_decode_vin_rejects_nonstandard_json_constants(self) -> None:
        service = AutomotiveLookupService()

        with patch(
            "minimal_kanban.agent.automotive_tools.httpx.Client",
            _client_factory(b'{"Results": [{"Make": NaN}]}'),
        ):
            with self.assertRaises(InternetToolError):
                service.decode_vin("JSAZC72S001234567")

    def test_decode_vin_rejects_oversized_response(self) -> None:
        service = AutomotiveLookupService()

        with (
            patch("minimal_kanban.agent.automotive_tools.httpx.Client", _client_factory(b"x" * 16)),
            patch("minimal_kanban.agent.automotive_tools.AUTOMOTIVE_VIN_RESPONSE_MAX_BYTES", 8),
            self.assertRaisesRegex(InternetToolError, "response is too large"),
        ):
            service.decode_vin("JSAZC72S001234567")

    def test_decode_vin_does_not_follow_redirects(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                _ = chunk_size
                yield b'{"Results":[]}'

        class FakeClient:
            stream_kwargs: dict[str, object] = {}

            def __init__(self, *args, **kwargs) -> None:
                _ = (args, kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def stream(self, *args, **kwargs) -> FakeResponse:
                _ = args
                type(self).stream_kwargs = dict(kwargs)
                return FakeResponse()

        service = AutomotiveLookupService()

        with patch("minimal_kanban.agent.automotive_tools.httpx.Client", FakeClient):
            service.decode_vin("JSAZC72S001234567")

        self.assertIs(FakeClient.stream_kwargs["follow_redirects"], False)

    def test_decode_vin_reader_requests_bounded_chunks(self) -> None:
        class FakeResponse:
            chunk_size: int | None = None

            def iter_bytes(self, *, chunk_size: int | None = None):
                self.chunk_size = chunk_size
                yield b"{}"

        service = AutomotiveLookupService()
        response = FakeResponse()

        content = service._read_response_bytes(response, max_bytes=8)  # type: ignore[arg-type]

        self.assertEqual(content, b"{}")
        self.assertEqual(response.chunk_size, 9)

    def test_rub_amount_rejects_non_finite_values(self) -> None:
        service = AutomotiveLookupService()

        self.assertIsNone(service._rub_amount("inf", "руб."))
        self.assertIsNone(service._rub_amount("nan", "руб."))


class AgentToolExecutorTests(unittest.TestCase):
    def test_tool_schema_json_dumps_sanitizes_non_finite_numbers(self) -> None:
        encoded = _json_dumps({"value": math.nan, "items": [math.inf, -math.inf]})

        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)
        self.assertEqual(encoded, '{"value": null, "items": [null, null]}')

    def test_maybe_int_ignores_overflowing_values(self) -> None:
        executor = object.__new__(AgentToolExecutor)

        self.assertIsNone(executor._maybe_int(float("inf")))
        self.assertIsNone(executor._maybe_int(1e308))
        self.assertIsNone(executor._maybe_int(True))
        self.assertIsNone(executor._maybe_int(1.5))


if __name__ == "__main__":
    unittest.main()
