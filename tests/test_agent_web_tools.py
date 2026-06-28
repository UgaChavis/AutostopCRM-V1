from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.web_tools import DuckDuckGoSearchClient, InternetToolError  # noqa: E402


def _client_factory(*, text: str = "", url: str, chunks: list[bytes] | None = None):
    class _Response:
        def __init__(self) -> None:
            self.url = url
            self.encoding = "utf-8"

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, *, chunk_size=None):
            _ = chunk_size
            if chunks is not None:
                yield from chunks
                return
            yield text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

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


def _result_html(count: int) -> str:
    return "\n".join(
        f"""
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="https://example.com/{index}">Title {index}</a>
            <div class="result__snippet">Snippet {index}</div>
          </div>
        </div>
        """
        for index in range(count)
    )


class AgentWebToolsTests(unittest.TestCase):
    def test_search_bounds_large_limit_and_treats_domain_string_as_one_domain(self) -> None:
        client = DuckDuckGoSearchClient(timeout_seconds=float("inf"))

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=_result_html(12), url="https://html.duckduckgo.com/html/"),
        ):
            results = client.search("oil filter", limit=1e308, allowed_domains="example.com")  # type: ignore[arg-type]

        self.assertEqual(client._timeout_seconds, 12.0)
        self.assertEqual(len(results), 10)
        self.assertTrue(all(item.domain == "example.com" for item in results))

    def test_malformed_public_url_is_rejected_without_urlparse_escape(self) -> None:
        client = DuckDuckGoSearchClient()

        with self.assertRaises(InternetToolError):
            client.fetch_page_excerpt("https://[::1")
        self.assertEqual(client._url_hostname("https://[::1"), "")

    def test_search_allowed_domains_match_hostname_without_port(self) -> None:
        html_text = """
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="https://example.com:443/specs">Specs</a>
            <div class="result__snippet">Snippet</div>
          </div>
        </div>
        """
        client = DuckDuckGoSearchClient()

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=html_text, url="https://html.duckduckgo.com/html/"),
        ):
            results = client.search("oil filter", limit=5, allowed_domains=["example.com"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].domain, "example.com")
        self.assertEqual(results[0].url, "https://example.com:443/specs")

    def test_search_allowed_domains_accept_url_inputs_and_trailing_dots(self) -> None:
        html_text = """
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="https://example.com/specs">Specs</a>
            <div class="result__snippet">Snippet</div>
          </div>
        </div>
        """
        client = DuckDuckGoSearchClient()

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=html_text, url="https://html.duckduckgo.com/html/"),
        ):
            results = client.search(
                "oil filter",
                limit=5,
                allowed_domains=["https://Example.COM.:443/path", ".example.com."],
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].domain, "example.com")

    def test_search_falls_back_for_boolean_and_fractional_numeric_options(self) -> None:
        client = DuckDuckGoSearchClient(timeout_seconds=True)

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=_result_html(12), url="https://html.duckduckgo.com/html/"),
        ):
            bool_results = client.search("oil filter", limit=True)
            fractional_results = client.search("oil filter", limit=1.5)  # type: ignore[arg-type]

        self.assertEqual(client._timeout_seconds, 12.0)
        self.assertEqual(len(bool_results), 5)
        self.assertEqual(len(fractional_results), 5)

    def test_search_skips_non_public_result_urls(self) -> None:
        html_text = """
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="http://127.0.0.1:41731/status">Loopback</a>
            <div class="result__snippet">Private result</div>
          </div>
        </div>
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="javascript:alert(1)">Script</a>
            <div class="result__snippet">Script result</div>
          </div>
        </div>
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="/l/?uddg=http%3A%2F%2Flocalhost%3A41731%2Fstatus">Local redirect</a>
            <div class="result__snippet">Local redirect result</div>
          </div>
        </div>
        <div class="result">
          <div>
            <a rel="nofollow" class="result__a" href="https://example.com/specs">Public</a>
            <div class="result__snippet">Public result</div>
          </div>
        </div>
        """
        client = DuckDuckGoSearchClient()

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=html_text, url="https://html.duckduckgo.com/html/"),
        ):
            results = client.search("oil filter", limit=10)

        self.assertEqual([item.url for item in results], ["https://example.com/specs"])

    def test_fetch_page_excerpt_bounds_non_finite_max_chars(self) -> None:
        client = DuckDuckGoSearchClient()
        page_text = "<html><body>" + ("x" * 3000) + "</body></html>"

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=page_text, url="https://example.com/specs"),
        ):
            payload = client.fetch_page_excerpt("https://example.com/specs", max_chars=float("inf"))

        self.assertEqual(payload["domain"], "example.com")
        self.assertEqual(len(payload["excerpt"]), 2500)

    def test_fetch_page_excerpt_domain_omits_port(self) -> None:
        client = DuckDuckGoSearchClient()

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(
                text="<html><body>ok</body></html>", url="https://example.com:8443/specs"
            ),
        ):
            payload = client.fetch_page_excerpt("https://example.com:8443/specs")

        self.assertEqual(payload["domain"], "example.com")

    def test_fetch_page_excerpt_falls_back_for_boolean_max_chars(self) -> None:
        client = DuckDuckGoSearchClient()
        page_text = "<html><body>" + ("x" * 3000) + "</body></html>"

        with patch(
            "minimal_kanban.agent.web_tools.httpx.Client",
            _client_factory(text=page_text, url="https://example.com/specs"),
        ):
            payload = client.fetch_page_excerpt("https://example.com/specs", max_chars=True)

        self.assertEqual(len(payload["excerpt"]), 2500)

    def test_fetch_page_excerpt_rejects_local_and_private_urls_before_http_client(self) -> None:
        client = DuckDuckGoSearchClient()
        blocked_urls = [
            "file:///etc/passwd",
            "http://localhost:41731/api/health",
            "http://127.0.0.1:41731/api/health",
            "http://10.0.0.1/status",
            "http://[::1]/status",
            "http://intranet/status",
            "https://printer.local/status",
        ]

        with patch("minimal_kanban.agent.web_tools.httpx.Client") as http_client:
            for url in blocked_urls:
                with self.subTest(url=url), self.assertRaises(InternetToolError):
                    client.fetch_page_excerpt(url)

        http_client.assert_not_called()

    def test_fetch_page_excerpt_rejects_redirect_to_private_url(self) -> None:
        class RedirectResponse:
            url = "https://example.com/start"
            status_code = 302
            headers = {"location": "http://127.0.0.1:41731/api/health"}
            encoding = "utf-8"

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                _ = chunk_size
                yield b""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeClient:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def stream(self, method: str, url: str, **kwargs) -> RedirectResponse:
                _ = (method, kwargs)
                self.urls.append(url)
                return RedirectResponse()

        http_client = FakeClient()
        client = DuckDuckGoSearchClient()

        with self.assertRaisesRegex(InternetToolError, "Local or private URLs"):
            client._fetch_limited_text_with_url(http_client, "https://example.com/start", 128)

        self.assertEqual(http_client.urls, ["https://example.com/start"])

    def test_fetch_page_excerpt_follows_public_relative_redirect(self) -> None:
        class Response:
            encoding = "utf-8"

            def __init__(
                self, url: str, *, status_code: int, location: str = "", body: bytes = b""
            ):
                self.url = url
                self.status_code = status_code
                self.headers = {"location": location} if location else {}
                self._body = body

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                _ = chunk_size
                yield self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeClient:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def stream(self, method: str, url: str, **kwargs) -> Response:
                _ = (method, kwargs)
                self.urls.append(url)
                if url.endswith("/start"):
                    return Response(url, status_code=302, location="/final")
                return Response(url, status_code=200, body=b"<html>ok</html>")

        http_client = FakeClient()
        client = DuckDuckGoSearchClient()

        url, text = client._fetch_limited_text_with_url(
            http_client, "https://example.com/start", 128
        )

        self.assertEqual(url, "https://example.com/final")
        self.assertEqual(text, "<html>ok</html>")
        self.assertEqual(
            http_client.urls, ["https://example.com/start", "https://example.com/final"]
        )

    def test_search_rejects_oversized_response(self) -> None:
        client = DuckDuckGoSearchClient()

        with (
            patch("minimal_kanban.agent.web_tools._MAX_SEARCH_RESPONSE_BYTES", 4),
            patch(
                "minimal_kanban.agent.web_tools.httpx.Client",
                _client_factory(chunks=[b"123", b"45"], url="https://html.duckduckgo.com/html/"),
            ),
            self.assertRaises(InternetToolError),
        ):
            client.search("oil filter")

    def test_fetch_page_excerpt_rejects_oversized_response(self) -> None:
        client = DuckDuckGoSearchClient()

        with (
            patch("minimal_kanban.agent.web_tools._MAX_PAGE_RESPONSE_BYTES", 4),
            patch(
                "minimal_kanban.agent.web_tools.httpx.Client",
                _client_factory(chunks=[b"123", b"45"], url="https://example.com/specs"),
            ),
            self.assertRaises(InternetToolError),
        ):
            client.fetch_page_excerpt("https://example.com/specs")

    def test_fetch_limited_text_requests_bounded_chunks(self) -> None:
        class FakeResponse:
            url = "https://example.com/specs"
            encoding = "utf-8"
            chunk_size: int | None = None

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size: int | None = None):
                self.chunk_size = chunk_size
                yield b"ok"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeClient:
            def __init__(self, response: FakeResponse) -> None:
                self.response = response

            def stream(self, *args, **kwargs) -> FakeResponse:
                _ = (args, kwargs)
                return self.response

        response = FakeResponse()
        client = DuckDuckGoSearchClient()

        url, text = client._fetch_limited_text_with_url(
            FakeClient(response), "https://example.com/specs", 8
        )

        self.assertEqual((url, text), ("https://example.com/specs", "ok"))
        self.assertEqual(response.chunk_size, 9)


if __name__ == "__main__":
    unittest.main()
