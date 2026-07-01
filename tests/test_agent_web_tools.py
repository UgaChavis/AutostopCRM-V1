from __future__ import annotations

import os
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


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


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


class _FakeBrowserResponse:
    status = 403


class _FakeBrowserLocator:
    def inner_text(self, *, timeout=None) -> str:
        _ = timeout
        return "Rendered specs text. CAPTCHA required. Checking your browser before access. " + (
            "x" * 100
        )


class _FakeBrowserPage:
    url = "https://example.com/rendered"

    def __init__(self) -> None:
        self.waited_ms: list[int] = []

    def goto(self, *args, **kwargs) -> _FakeBrowserResponse:
        _ = (args, kwargs)
        return _FakeBrowserResponse()

    def wait_for_timeout(self, wait_ms: int) -> None:
        self.waited_ms.append(wait_ms)

    def wait_for_load_state(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def title(self) -> str:
        return "Rendered Page"

    def locator(self, selector: str) -> _FakeBrowserLocator:
        self.selector = selector
        return _FakeBrowserLocator()

    def content(self) -> str:
        return "<html><body>fallback</body></html>"

    def eval_on_selector_all(self, *args, **kwargs) -> list[dict[str, str]]:
        _ = (args, kwargs)
        return [
            {"text": "Public link", "url": "https://example.org/part"},
            {"text": "Private link", "url": "http://127.0.0.1/admin"},
            {"text": "Duplicate", "url": "https://example.org/part"},
        ]


class _FakeBrowserContext:
    def __init__(self, page: _FakeBrowserPage) -> None:
        self.page = page
        self.closed = False
        self.routes: list[tuple[str, object]] = []

    def route(self, pattern: str, handler) -> None:  # noqa: ANN001
        self.routes.append((pattern, handler))

    def new_page(self) -> _FakeBrowserPage:
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakeBrowserPage) -> None:
        self.page = page
        self.closed = False
        self.contexts: list[_FakeBrowserContext] = []

    def new_context(self, **kwargs) -> _FakeBrowserContext:
        self.context_kwargs = kwargs
        context = _FakeBrowserContext(self.page)
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.launch_kwargs: dict[str, object] = {}

    def launch(self, **kwargs) -> _FakeBrowser:
        self.launch_kwargs = dict(kwargs)
        return self.browser


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)


class _FakePlaywrightContextManager:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.playwright = _FakePlaywright(browser)

    def __enter__(self) -> _FakePlaywright:
        return self.playwright

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


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

    def test_search_multi_skips_missing_api_keys_and_uses_duckduckgo(self) -> None:
        client = DuckDuckGoSearchClient()

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "minimal_kanban.agent.web_tools.httpx.Client",
                _client_factory(text=_result_html(2), url="https://html.duckduckgo.com/html/"),
            ),
        ):
            payload = client.search_multi("oil filter", limit=2)

        self.assertEqual(
            [item["provider"] for item in payload["providers"]],
            ["brave", "tavily", "google_cse", "duckduckgo"],
        )
        self.assertEqual(
            [item["status"] for item in payload["providers"][:3]], ["skipped", "skipped", "skipped"]
        )
        self.assertEqual(payload["providers"][-1]["status"], "success")
        self.assertTrue(payload["fallback_used"])
        self.assertEqual(
            [item["provider"] for item in payload["results"]], ["duckduckgo", "duckduckgo"]
        )

    def test_search_multi_uses_brave_first_when_configured(self) -> None:
        class BraveClient:
            calls: list[dict[str, object]] = []

            def __init__(self, *args, **kwargs) -> None:
                _ = (args, kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def get(self, url: str, *, params=None, headers=None) -> _JsonResponse:  # noqa: ANN001
                self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
                return _JsonResponse(
                    {
                        "web": {
                            "results": [
                                {
                                    "title": "<b>Specs</b>",
                                    "url": "https://example.com/specs",
                                    "description": "Main description",
                                    "extra_snippets": ["Extra snippet"],
                                }
                            ]
                        }
                    }
                )

        client = DuckDuckGoSearchClient()
        with (
            patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-key"}, clear=True),
            patch("minimal_kanban.agent.web_tools.httpx.Client", BraveClient),
        ):
            payload = client.search_multi("oil filter", limit=1)

        self.assertEqual(
            payload["providers"],
            [{"provider": "brave", "status": "success", "result_count": 1, "added_count": 1}],
        )
        self.assertFalse(payload["fallback_used"])
        self.assertEqual(payload["results"][0]["provider"], "brave")
        self.assertEqual(payload["results"][0]["title"], "Specs")
        self.assertIn("Extra snippet", payload["results"][0]["snippet"])
        self.assertEqual(BraveClient.calls[0]["headers"]["X-Subscription-Token"], "brave-key")

    def test_search_multi_falls_back_after_provider_error_without_secret_leak(self) -> None:
        class MixedClient:
            def __init__(self, *args, **kwargs) -> None:
                _ = (args, kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def get(self, *args, **kwargs):  # noqa: ANN001
                _ = (args, kwargs)
                raise RuntimeError("brave-key should not leak")

            def stream(self, *args, **kwargs):  # noqa: ANN001
                _ = (args, kwargs)
                return _client_factory(
                    text=_result_html(1),
                    url="https://html.duckduckgo.com/html/",
                )().stream()

        client = DuckDuckGoSearchClient()
        with (
            patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-key"}, clear=True),
            patch("minimal_kanban.agent.web_tools.httpx.Client", MixedClient),
        ):
            payload = client.search_multi("oil filter", limit=1)

        self.assertEqual(payload["providers"][0]["provider"], "brave")
        self.assertEqual(payload["providers"][0]["status"], "error")
        self.assertNotIn("brave-key", payload["providers"][0]["error"])
        self.assertEqual(payload["results"][0]["provider"], "duckduckgo")

    def test_search_multi_parses_tavily_and_google_cse_results(self) -> None:
        class ProviderClient:
            def __init__(self, *args, **kwargs) -> None:
                _ = (args, kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def post(self, *args, **kwargs) -> _JsonResponse:  # noqa: ANN001
                _ = (args, kwargs)
                return _JsonResponse(
                    {
                        "results": [
                            {
                                "title": "Tavily",
                                "url": "https://forum.example/item",
                                "content": "Forum result",
                            }
                        ]
                    }
                )

            def get(self, *args, **kwargs) -> _JsonResponse:  # noqa: ANN001
                _ = (args, kwargs)
                return _JsonResponse(
                    {
                        "items": [
                            {
                                "title": "Google",
                                "link": "https://catalog.example/item",
                                "snippet": "Catalog result",
                            }
                        ]
                    }
                )

        client = DuckDuckGoSearchClient()
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-key"}, clear=True),
            patch("minimal_kanban.agent.web_tools.httpx.Client", ProviderClient),
        ):
            tavily_payload = client.search_multi("oil filter", limit=1, providers=["tavily"])
        with (
            patch.dict(
                os.environ,
                {"GOOGLE_CUSTOM_SEARCH_API_KEY": "google-key", "GOOGLE_CUSTOM_SEARCH_CX": "cx"},
                clear=True,
            ),
            patch("minimal_kanban.agent.web_tools.httpx.Client", ProviderClient),
        ):
            google_payload = client.search_multi("oil filter", limit=1, providers=["google"])

        self.assertEqual(tavily_payload["results"][0]["provider"], "tavily")
        self.assertEqual(tavily_payload["results"][0]["domain"], "forum.example")
        self.assertEqual(google_payload["results"][0]["provider"], "google_cse")
        self.assertEqual(google_payload["results"][0]["domain"], "catalog.example")

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

    def test_fetch_page_browser_rejects_private_urls_before_loading_playwright(self) -> None:
        client = DuckDuckGoSearchClient()

        with patch("minimal_kanban.agent.web_tools._load_sync_playwright") as load_playwright:
            with self.assertRaises(InternetToolError):
                client.fetch_page_browser("http://127.0.0.1:41731/api/health")

        load_playwright.assert_not_called()

    def test_fetch_page_browser_reports_missing_playwright_without_http_client(self) -> None:
        client = DuckDuckGoSearchClient()

        with (
            patch("minimal_kanban.agent.web_tools._load_sync_playwright", return_value=None),
            patch("minimal_kanban.agent.web_tools.httpx.Client") as http_client,
        ):
            payload = client.fetch_page_browser("https://example.com/specs")

        http_client.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "playwright_missing")
        self.assertEqual(payload["access_flags"], ["browser_unavailable"])

    def test_fetch_page_browser_extracts_rendered_text_links_and_access_flags(self) -> None:
        page = _FakeBrowserPage()
        browser = _FakeBrowser(page)

        def sync_playwright():
            return _FakePlaywrightContextManager(browser)

        client = DuckDuckGoSearchClient()
        with patch(
            "minimal_kanban.agent.web_tools._load_sync_playwright", return_value=sync_playwright
        ):
            payload = client.fetch_page_browser(
                "https://example.com/specs",
                max_chars=30,
                wait_ms=0,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["final_url"], "https://example.com/rendered")
        self.assertEqual(payload["domain"], "example.com")
        self.assertEqual(payload["status_code"], 403)
        self.assertEqual(payload["excerpt"], "Rendered specs text. CAPTCHA r")
        self.assertEqual(
            payload["links"],
            [
                {
                    "text": "Public link",
                    "url": "https://example.org/part",
                    "domain": "example.org",
                }
            ],
        )
        self.assertIn("captcha_required", payload["access_flags"])
        self.assertIn("js_challenge", payload["access_flags"])
        self.assertIn("access_denied", payload["access_flags"])
        self.assertTrue(payload["requires_human"])
        self.assertEqual(page.waited_ms, [])
        self.assertTrue(browser.closed)
        self.assertTrue(browser.contexts[0].closed)


if __name__ == "__main__":
    unittest.main()
