from __future__ import annotations

import json
import socket
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.automotive_tools import AutomotiveLookupService  # noqa: E402
from minimal_kanban.agent.source_registry import public_catalog_page_read_policy  # noqa: E402
from minimal_kanban.agent.web_tools import (  # noqa: E402
    DuckDuckGoSearchClient,
    InternetToolError,
    _PublicBrowserRequestGuard,
)

BLOCKED_CATALOG_URLS = (
    "https://www.elcats.ru/vw/parts.aspx?id=DEMO",
    "https://japancats.ru/toyota/parts.aspx?id=DEMO",
    "https://WWW.ELCATS.RU./vw/",
    "https:// elcats.ru/vw/parts.aspx?id=DEMO",
    "https://www.elcats.ru /vw/parts.aspx?id=DEMO",
    "https:// WWW.ELCATS.RU. /vw/parts.aspx?id=DEMO",
    "https:// japancats.ru/toyota/parts.aspx?id=DEMO",
    "https:// JAPANCATS.RU. /toyota/parts.aspx?id=DEMO",
)


class ElcatsSourcePolicyTests(unittest.TestCase):
    def test_redirect_failure_remains_denied_evidence_without_target_dns_or_http(self) -> None:
        for target_url in BLOCKED_CATALOG_URLS:
            with self.subTest(target_url=target_url):
                self._assert_redirect_denied(target_url)

    def _assert_redirect_denied(self, target_url: str) -> None:
        original_url = "https://partsouq.com/redirect/DEMO"
        requests = []

        class RedirectClient:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @contextmanager
            def stream(self, method, url, **_kwargs):
                requests.append((method, url))
                if url != original_url:
                    raise AssertionError("denied target must never be fetched")
                yield httpx.Response(
                    302,
                    headers={"location": target_url},
                    request=httpx.Request(method, url),
                )

        service = AutomotiveLookupService()
        search = service._search
        with (
            patch.object(
                search,
                "search_multi",
                return_value={
                    "results": [
                        {
                            "title": "DEMO brake pads",
                            "url": original_url,
                            "snippet": "DEMO brake pads",
                            "domain": "partsouq.com",
                            "provider": "searxng",
                        }
                    ],
                    "providers": [],
                },
            ),
            patch(
                "minimal_kanban.agent.web_tools.socket.getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
            ) as dns,
            patch(
                "minimal_kanban.agent.web_tools.socket.socket.connect",
                side_effect=AssertionError("live network is forbidden"),
            ),
            patch("minimal_kanban.agent.web_tools.httpx.Client", RedirectClient),
            patch("minimal_kanban.agent.web_tools._load_sync_playwright") as browser,
        ):
            result = service.research_part_public_evidence(
                query="DEMO brake pads", allowed_domains=["partsouq.com"], max_pages=1
            )
        self.assertEqual(requests, [("GET", original_url)])
        self.assertTrue(dns.called)
        self.assertTrue(all(call.args[0] == "partsouq.com" for call in dns.call_args_list))
        browser.assert_not_called()
        self.assertEqual(result["status"], "partial_evidence")
        self.assertFalse(result["fitment_confirmed"])
        self.assertEqual(result["results"][0]["source_id"], "partsouq_catalog")
        evidence = result["evidence"][0]
        self.assertEqual(evidence["access_status"], "robots_disallowed")
        self.assertEqual(evidence["access_flags"], ["robots_disallowed"])
        self.assertEqual(evidence["error"], {"code": "robots_disallowed", "retryable": False})
        self.assertNotIn("excerpt", evidence)

    def test_failure_payload_preserves_only_safe_error_and_never_becomes_available(self) -> None:
        failures = (
            ({"code": "robots_disallowed", "retryable": True}, "robots_disallowed", False),
            ({"code": "rate_limited", "retryable": True}, "rate_limited", True),
            ({"code": "http_not_found", "retryable": False}, "http_not_found", False),
            ({"code": "timeout", "retryable": "true"}, "timeout", False),
            ({"code": "private-canary", "retryable": True}, "cause_unknown", False),
            ({"code": [], "retryable": True}, "cause_unknown", False),
            ("private-canary", "cause_unknown", False),
            (None, "cause_unknown", False),
        )
        for error, expected_code, retryable in failures:
            for failure_kind in ("ok_false", "missing_ok", "exception"):
                if failure_kind == "missing_ok" and error is None:
                    continue
                if failure_kind == "exception" and not isinstance(error, dict):
                    continue
                with self.subTest(error=error, failure_kind=failure_kind):
                    service = AutomotiveLookupService()
                    search = Mock()
                    search.search_multi.return_value = {
                        "results": [
                            {
                                "title": "DEMO brake pads",
                                "url": "https://partsouq.com/catalog/DEMO",
                                "snippet": "DEMO",
                                "domain": "partsouq.com",
                                "provider": "searxng",
                            }
                        ],
                        "providers": [],
                    }
                    search.fetch_page_excerpt.return_value = {
                        "error": error,
                        "excerpt": "private-canary",
                        "access_flags": ["private-canary"],
                        "requires_human": False,
                        **({"ok": False} if failure_kind == "ok_false" else {}),
                    }
                    if failure_kind == "exception":
                        search.fetch_page_excerpt.side_effect = InternetToolError(
                            "private-canary",
                            code=error.get("code"),
                            retryable=error.get("retryable"),
                        )
                    service._search = search
                    result = service.research_part_public_evidence(query="DEMO brake pads")
                    evidence = result["evidence"][0]
                    self.assertEqual(result["status"], "partial_evidence")
                    self.assertFalse(result["fitment_confirmed"])
                    self.assertEqual(
                        evidence["access_status"],
                        "robots_disallowed"
                        if expected_code == "robots_disallowed"
                        else "unavailable",
                    )
                    self.assertEqual(evidence["access_flags"], [expected_code])
                    self.assertEqual(
                        evidence["error"], {"code": expected_code, "retryable": retryable}
                    )
                    self.assertNotIn("excerpt", evidence)
                    self.assertNotIn("private-canary", json.dumps(result))

    def test_static_and_browser_deny_known_catalog_pages_before_dns_or_network(self) -> None:
        client = DuckDuckGoSearchClient()
        with (
            patch("minimal_kanban.agent.web_tools.socket.getaddrinfo") as dns,
            patch("minimal_kanban.agent.web_tools.httpx.Client") as http,
            patch("minimal_kanban.agent.web_tools._load_sync_playwright") as browser,
        ):
            for url in BLOCKED_CATALOG_URLS:
                for method in (client.fetch_page_excerpt, client.fetch_page_browser):
                    with self.subTest(url=url, method=method.__name__):
                        with self.assertRaises(InternetToolError) as raised:
                            method(url)
                        self.assertEqual(raised.exception.code, "robots_disallowed")
                        self.assertFalse(raised.exception.retryable)
            dns.assert_not_called()
            http.assert_not_called()
            browser.assert_not_called()

    def test_http_redirect_sink_and_browser_request_guard_cannot_acquire_blocked_source(
        self,
    ) -> None:
        client = DuckDuckGoSearchClient()
        transport = Mock()
        with patch.object(client, "_resolve_public_host") as dns:
            for url in BLOCKED_CATALOG_URLS:
                with self.subTest(url=url):
                    with self.assertRaises(InternetToolError) as raised:
                        with client._stream_public_request(transport, "GET", url):
                            self.fail("blocked stream opened")
                    self.assertEqual(raised.exception.code, "robots_disallowed")
                    route = Mock()
                    request = Mock(url=url, method="GET", resource_type="document")
                    _PublicBrowserRequestGuard(client)(route, request)
                    route.abort.assert_called_once()
                    route.continue_.assert_not_called()
            dns.assert_not_called()
        transport.stream.assert_not_called()

    def test_only_plain_robots_file_is_exempt_and_other_sources_keep_their_policy(self) -> None:
        client = DuckDuckGoSearchClient()
        for url in (
            "https://elcats.ru/robots.txt",
            "https://japancats.ru/robots.txt",
            "https://partsouq.com/catalog/DEMO",
            "https://ssangyong.exist.ru/Catalog/?id=DEMO",
            "https://elcats.ru.example.com/catalog/DEMO",
        ):
            with self.subTest(url=url):
                self.assertIsNone(public_catalog_page_read_policy(url))
                self.assertEqual(
                    client._validated_public_http_url(url, resolve_dns=False, acquire=True), url
                )
        self.assertIsNotNone(
            public_catalog_page_read_policy("https://elcats.ru/robots.txt?catalog=DEMO")
        )
        self.assertIsNotNone(public_catalog_page_read_policy("https://elcats.ru/%72obots.txt"))

    def test_search_links_keep_distinct_source_ids_without_fetching_denied_bodies(self) -> None:
        service = AutomotiveLookupService()
        fake_search = Mock()
        fake_search.search_multi.return_value = {
            "results": [
                {
                    "title": "DEMO brake pad",
                    "url": "https://elcats.ru/vw/parts.aspx?id=DEMO",
                    "snippet": "DEMO front brake pads",
                    "domain": "elcats.ru",
                    "provider": "searxng",
                },
                {
                    "title": "DEMO brake pad",
                    "url": "https://japancats.ru/toyota/parts.aspx?id=DEMO",
                    "snippet": "DEMO front brake pads",
                    "domain": "japancats.ru",
                    "provider": "searxng",
                },
            ],
            "providers": [],
        }
        service._search = fake_search
        result = service.research_part_public_evidence(
            query="DEMO brake pads", allowed_domains=["elcats.ru", "japancats.ru"], max_pages=2
        )
        self.assertEqual(result["status"], "partial_evidence")
        self.assertFalse(result["fitment_confirmed"])
        self.assertEqual(
            [item["source_id"] for item in result["results"]],
            ["elcats_catalog", "japancats_catalog"],
        )
        self.assertTrue(
            all(item["access_status"] == "robots_disallowed" for item in result["evidence"])
        )
        self.assertTrue(
            all(
                item["error"] == {"code": "robots_disallowed", "retryable": False}
                for item in result["evidence"]
            )
        )
        fake_search.fetch_page_excerpt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
