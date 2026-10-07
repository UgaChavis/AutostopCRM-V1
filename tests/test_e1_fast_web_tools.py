from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from tests.source_path_support import ensure_repository_root_path, ensure_source_path

ensure_repository_root_path()
ensure_source_path()

from minimal_kanban.agent.web_tools import DuckDuckGoSearchClient, InternetToolError
from minimal_kanban.mcp.web_gateway import invoke_web_research


@pytest.mark.parametrize(
    "status,media,code,retry",
    [
        (404, "application/pdf", "http_not_found", False),
        (403, "text/html", "access_restricted", False),
        (429, "text/html", "rate_limited", True),
        (503, "text/html", "http_server_error", True),
        (200, "application/pdf", "unsupported_media", False),
    ],
)
def test_status_and_pdf_header_survive_without_reading_binary(status, media, code, retry):
    client = DuckDuckGoSearchClient()
    response = httpx.Response(
        status,
        headers={"content-type": media},
        content=b"%PDF body",
        request=httpx.Request("GET", "https://example.com/disc"),
    )

    @contextmanager
    def stream(*_args):
        yield response

    with (
        patch.object(client, "_validated_public_http_url", side_effect=lambda u, **_: u),
        patch.object(client, "_stream_public_request", side_effect=stream),
        patch.object(
            client, "_read_limited_response_bytes", side_effect=AssertionError("must not read")
        ),
    ):
        result = client.fetch_page_excerpt("https://example.com/disc", max_chars=200)
    assert result["ok"] is False
    assert result["status_code"] == status
    assert result["content_type"] == media
    assert result["error"] == {"code": code, "retryable": retry}


def test_html_metadata_records_actual_acquisition_and_truncation():
    client = DuckDuckGoSearchClient()
    response = httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=b"<html><p>abcdefghijklmnopqrstuvwxyz</p></html>",
        request=httpx.Request("GET", "https://example.com/disc"),
    )

    @contextmanager
    def stream(*_args):
        yield response

    with (
        patch.object(client, "_validated_public_http_url", side_effect=lambda u, **_: u),
        patch.object(client, "_stream_public_request", side_effect=stream),
    ):
        result = client.fetch_page_excerpt("https://example.com/disc", max_chars=12)
    assert result["excerpt"] == "abcdefghijkl"
    assert result["status_code"] == 200
    assert result["requested_chars"] == result["effective_chars"] == 12
    assert result["truncated"] is True
    assert result["content_type"] == "text/html"
    assert result["acquisition_method"] == "live_http"
    assert result["extraction_method"] == "html_text"
    assert result["retrieved_at"]
    assert result["extracted_chars"] == 26
    assert result["delivered_chars"] == result["excerpt_limit_chars"] == 12


@pytest.mark.parametrize(
    "body,code",
    [
        ("<html>New unknown response template</html>", "search_markup_unknown"),
        ("<html>Please solve captcha</html>", "search_challenge"),
    ],
)
def test_empty_unknown_markup_and_challenge_are_distinct(body, code):
    with pytest.raises(InternetToolError) as caught:
        DuckDuckGoSearchClient()._parse_results(body, limit=5, allowed_domains=[])
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert (
        DuckDuckGoSearchClient()._parse_results(
            "<div>No results found</div>", limit=5, allowed_domains=[]
        )
        == []
    )


def test_gateway_preserves_safe_error_and_marks_legacy_unknown():
    class Executor:
        def reset_task_budget(self):
            pass

        def execute(self, *_args):
            raise InternetToolError(
                "private upstream details", code="http_not_found", status_code=404
            )

    result = invoke_web_research(Executor(), "fetch_page_excerpt", {})
    assert result["error"] == {"code": "http_not_found", "retryable": False}
    assert result["status_code"] == 404
    assert "private" not in str(result)
    with patch.object(Executor, "execute", side_effect=RuntimeError("private cause")):
        unknown = invoke_web_research(Executor(), "fetch_page_excerpt", {})
    assert unknown["cause_unknown"] is True
    assert "private cause" not in str(unknown)


def test_json_provider_retains_status_without_error_body_or_url():
    response = httpx.Response(429, request=httpx.Request("GET", "https://example.com/private-key"))

    def loader():
        response.raise_for_status()

    rows, attempt = DuckDuckGoSearchClient()._try_json_search_provider("searxng", loader)
    assert rows == []
    assert attempt["error_code"] == "rate_limited"
    assert attempt["status_code"] == 429
    assert attempt["retryable"] is True
    assert "private-key" not in str(attempt)


def test_search_counts_dispatched_http_separately_from_skipped_providers():
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        @contextmanager
        def stream(self, *_args, **_kwargs):
            yield httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("GET", "http://searxng:8080/search"),
            )

    with (
        patch.dict(
            os.environ,
            {
                "AUTOSTOP_SEARXNG_BASE_URL": "http://searxng:8080",
                "AUTOSTOP_SEARCH_DISABLED_PROVIDERS": "tavily google_cse marginalia duckduckgo",
            },
            clear=True,
        ),
        patch("minimal_kanban.agent.web_tools.httpx.Client", FakeClient),
    ):
        result = DuckDuckGoSearchClient().search_multi("brake disc", providers=["brave", "searxng"])
    assert result["providers"][0]["status"] == "skipped"
    assert result["providers"][0]["network_request_count"] == 0
    assert result["providers"][1]["network_request_count"] == 1
    assert result["execution"]["attempt_count"] == 2
    assert result["execution"]["provider_network_attempt_count"] == 1
    assert result["execution"]["network_request_count"] == 1
    assert result["execution"]["completeness"] == "complete"
    assert result["execution"]["reused"] is False


def test_page_counts_redirect_requests_and_returns_requested_8000_chars():
    class FakeClient:
        def __init__(self, **_kwargs):
            self.requests = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        @contextmanager
        def stream(self, _method, url, **_kwargs):
            self.requests += 1
            yield httpx.Response(
                302 if self.requests == 1 else 200,
                headers={"location": "https://example.com/final", "content-type": "text/html"},
                text="x" * 12000,
                request=httpx.Request("GET", url),
            )

    client = DuckDuckGoSearchClient()
    with (
        patch("minimal_kanban.agent.web_tools.httpx.Client", FakeClient),
        patch.object(client, "_resolve_public_host", return_value=["93.184.216.34"]),
    ):
        result = client.fetch_page_excerpt("https://example.com/disc", max_chars=8000)
    assert result["excerpt"] == "x" * 8000
    assert result["extracted_chars"] == 12000
    assert result["delivered_chars"] == result["excerpt_limit_chars"] == 8000
    assert result["truncated"] is True
    assert result["execution"]["network_request_count"] == 2
    assert result["execution"]["completeness"] == "partial"
