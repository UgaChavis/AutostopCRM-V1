from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.automotive_tools import AutomotiveLookupService  # noqa: E402
from minimal_kanban.agent.web_tools import (  # noqa: E402
    DuckDuckGoSearchClient,
    InternetToolError,
    _PublicBrowserRequestGuard,
    sanitize_public_search_query,
)


def _dns_record(address: str, port: int = 443):
    return [(2, 1, 6, "", (address, port))]


class _StreamResponse:
    def __init__(self, body: bytes = b"ok", *, status_code: int = 200, location: str = "") -> None:
        self.url = "https://public.example/page"
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.encoding = "utf-8"
        self._body = body
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, *, chunk_size=None):
        _ = chunk_size
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def close(self) -> None:
        self.closed = True


class E8WebSafetyTests(unittest.TestCase):
    def test_direct_generic_search_never_sends_sensitive_query_tokens(self) -> None:
        class CapturingClient:
            def __init__(self, *args, **kwargs) -> None:
                _ = (args, kwargs)
                self.url = ""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def stream(self, method: str, url: str, **kwargs):  # noqa: ANN001
                _ = (method, kwargs)
                self.__class__.url = url
                return _StreamResponse(body=b"<html><body>No results</body></html>")

        raw_query = "колодки X9FKXXEEBKDJ82493 customer_id=client-77 +7 999 111-22-33"
        with (
            patch(
                "minimal_kanban.agent.web_tools.socket.getaddrinfo",
                return_value=_dns_record("93.184.216.34"),
            ),
            patch("minimal_kanban.agent.web_tools.httpx.Client", CapturingClient),
        ):
            DuckDuckGoSearchClient().search(raw_query)

        self.assertIn("%D0%BA%D0%BE%D0%BB%D0%BE%D0%B4%D0%BA%D0%B8", CapturingClient.url)
        self.assertNotIn("X9FKXXEEBKDJ82493", CapturingClient.url)
        self.assertNotIn("client-77", CapturingClient.url)
        self.assertNotIn("999", CapturingClient.url)

    def test_generic_search_redacts_vin_customer_tokens_and_cache_key(self) -> None:
        class CapturingSearch:
            def __init__(self) -> None:
                self.query = ""

            def search_multi(self, query: str, **kwargs):  # noqa: ANN001
                _ = kwargs
                self.query = query
                return {
                    "query": query,
                    "results": [],
                    "provider_order": [],
                    "providers": [],
                    "fallback_used": False,
                }

        raw_query = "передние колодки X9FKXXEEBKDJ82493 customer_id=client-77 +7 999 111-22-33"
        service = AutomotiveLookupService()
        search = CapturingSearch()
        service._search = search  # type: ignore[assignment]

        payload = service.search_web_multi(query=raw_query)

        self.assertEqual(search.query, "передние колодки")
        self.assertEqual(payload["query"], "передние колодки")
        self.assertNotIn("X9FKXXEEBKDJ82493", str(service._task_cache))
        self.assertNotIn("client-77", str(service._task_cache))
        self.assertNotIn("999 111", str(service._task_cache))

    def test_generic_search_rejects_query_that_only_contains_sensitive_tokens(self) -> None:
        with self.assertRaisesRegex(InternetToolError, "after redaction"):
            sanitize_public_search_query("X9FKXXEEBKDJ82493 customer_id=client-77")

    def test_private_dns_result_is_rejected_before_a_page_request(self) -> None:
        client = DuckDuckGoSearchClient()
        with (
            patch(
                "minimal_kanban.agent.web_tools.socket.getaddrinfo",
                return_value=_dns_record("10.0.0.4"),
            ),
            patch("minimal_kanban.agent.web_tools.httpx.Client") as http_client,
            self.assertRaisesRegex(InternetToolError, "Local or private URLs"),
        ):
            client.fetch_page_excerpt("https://public.example/page")
        http_client.assert_not_called()

    def test_redirect_to_private_dns_target_is_rejected_before_followup(self) -> None:
        class RedirectClient:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def stream(self, method: str, url: str, **kwargs):  # noqa: ANN001
                _ = (method, kwargs)
                self.urls.append(url)
                return _StreamResponse(status_code=302, location="https://redirect.example/private")

        def resolve(host: str, *args, **kwargs):  # noqa: ANN001
            _ = (args, kwargs)
            return _dns_record("93.184.216.34" if host == "public.example" else "10.0.0.4")

        http_client = RedirectClient()
        client = DuckDuckGoSearchClient()
        with (
            patch("minimal_kanban.agent.web_tools.socket.getaddrinfo", side_effect=resolve),
            self.assertRaisesRegex(InternetToolError, "Local or private URLs"),
        ):
            client._fetch_limited_text_with_url(http_client, "https://public.example/page", 128)
        self.assertEqual(http_client.urls, ["https://public.example/page"])

    def test_public_http_request_uses_resolved_address_with_original_host_and_sni(self) -> None:
        class PinnedClient:
            def __init__(self) -> None:
                self.request: httpx.Request | None = None
                self.response = _StreamResponse()

            def build_request(self, method: str, url: str, **kwargs) -> httpx.Request:  # noqa: ANN001
                return httpx.Request(method, url, **kwargs)

            def send(self, request: httpx.Request, **kwargs) -> _StreamResponse:  # noqa: ANN001
                _ = kwargs
                self.request = request
                return self.response

        http_client = PinnedClient()
        client = DuckDuckGoSearchClient()
        with patch(
            "minimal_kanban.agent.web_tools.socket.getaddrinfo",
            return_value=_dns_record("93.184.216.34"),
        ):
            final_url, text = client._fetch_limited_text_with_url(
                http_client, "https://public.example/page", 128
            )

        self.assertEqual((final_url, text), ("https://public.example/page", "ok"))
        assert http_client.request is not None
        self.assertEqual(str(http_client.request.url.host), "93.184.216.34")
        self.assertEqual(http_client.request.headers["host"], "public.example")
        self.assertEqual(http_client.request.extensions["sni_hostname"], "public.example")
        self.assertTrue(http_client.response.closed)

    def test_browser_guard_blocks_private_dns_and_keeps_safe_get(self) -> None:
        class Route:
            def __init__(self) -> None:
                self.action = ""

            def abort(self) -> None:
                self.action = "abort"

            def continue_(self) -> None:
                self.action = "continue"

        class Request:
            def __init__(
                self, url: str, *, method: str = "GET", resource_type: str = "script"
            ) -> None:
                self.url = url
                self.method = method
                self.resource_type = resource_type

        def resolve(host: str, *args, **kwargs):  # noqa: ANN001
            _ = (args, kwargs)
            return _dns_record("10.0.0.4" if host == "private.example" else "93.184.216.34")

        guard = _PublicBrowserRequestGuard(DuckDuckGoSearchClient())
        private_route = Route()
        public_route = Route()
        post_route = Route()
        overflow_route = Route()
        with patch("minimal_kanban.agent.web_tools.socket.getaddrinfo", side_effect=resolve):
            guard(private_route, Request("https://private.example/x"))
            guard(public_route, Request("https://public.example/app.js"))
            guard(post_route, Request("https://public.example/post", method="POST"))
            for _ in range(45):
                guard(Route(), Request("https://public.example/next"))
            guard(overflow_route, Request("https://public.example/overflow"))

        self.assertEqual(private_route.action, "abort")
        self.assertEqual(public_route.action, "continue")
        self.assertEqual(post_route.action, "abort")
        self.assertEqual(overflow_route.action, "abort")

    def test_public_json_response_has_a_byte_limit(self) -> None:
        class OversizedClient:
            def stream(self, method: str, url: str, **kwargs):  # noqa: ANN001
                _ = (method, url, kwargs)
                return _StreamResponse(body=b'{"payload":"too large"}')

        client = DuckDuckGoSearchClient()
        with (
            patch(
                "minimal_kanban.agent.web_tools.socket.getaddrinfo",
                return_value=_dns_record("93.184.216.34"),
            ),
            self.assertRaisesRegex(InternetToolError, "too large"),
        ):
            client._fetch_limited_json(
                OversizedClient(), "GET", "https://public.example/api", max_bytes=4
            )


if __name__ == "__main__":
    unittest.main()
