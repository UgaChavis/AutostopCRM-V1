from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class ElcatsSourcePolicyTests(unittest.TestCase):
    def test_static_and_browser_deny_known_catalog_pages_before_dns_or_network(self) -> None:
        client = DuckDuckGoSearchClient()
        urls = (
            "https://www.elcats.ru/vw/parts.aspx?id=DEMO",
            "https://japancats.ru/toyota/parts.aspx?id=DEMO",
            "https://WWW.ELCATS.RU./vw/",
        )
        with (
            patch("minimal_kanban.agent.web_tools.socket.getaddrinfo") as dns,
            patch("minimal_kanban.agent.web_tools.httpx.Client") as http,
            patch("minimal_kanban.agent.web_tools._load_sync_playwright") as browser,
        ):
            for url in urls:
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
        url = "https://elcats.ru/vw/parts.aspx?id=DEMO"
        with patch.object(client, "_resolve_public_host") as dns:
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
