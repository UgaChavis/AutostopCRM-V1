from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.drive2_research import Drive2CaseResearch  # noqa: E402


class _FakeSearch:
    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []

    def search_multi(self, query, *, limit, allowed_domains):  # noqa: ANN001
        self.search_calls.append(query)
        self.assert_request(limit, allowed_domains)
        return {
            "results": [
                {
                    "title": "Ремонт DSG DQ200 — Drive2",
                    "url": "https://www.drive2.ru/l/123456789/",
                    "snippet": "Причина найдена, ремонт завершен.",
                },
                {
                    "title": "Нерелевантная страница",
                    "url": "https://www.drive2.ru/r/skoda/octavia/123/",
                    "snippet": "profile",
                },
            ],
            "providers": [{"provider": "searxng", "status": "success"}],
        }

    @staticmethod
    def assert_request(limit, allowed_domains):  # noqa: ANN001
        assert limit == 10
        assert allowed_domains == ["drive2.ru"]

    def fetch_page_excerpt(self, url, *, max_chars):  # noqa: ANN001
        self.fetch_calls.append(url)
        assert max_chars == 8000
        return {
            "ok": True,
            "url": url,
            "excerpt": (
                "[Бензин 1.8 л, робот, 2013]\n# Ремонт DSG DQ200\n"
                "7 октября 2020\nДиагностика показала стружку в масле. "
                "Причина найдена после разборки, заменили синхронизатор. "
                "Итог: коробка работает штатно."
            ),
            "access_flags": ["login_required"],
            "engine": "crawl4ai",
        }


class _RankingSearch(_FakeSearch):
    def search_multi(self, query, *, limit, allowed_domains):  # noqa: ANN001
        self.search_calls.append(query)
        self.assert_request(limit, allowed_domains)
        return {
            "results": [
                {
                    "title": "Стук при включении задней передачи",
                    "url": "https://www.drive2.ru/l/111111111/",
                    "snippet": "механика",
                },
                {
                    "title": "Ремонт DSG DQ200",
                    "url": "https://www.drive2.ru/l/222222222/",
                    "snippet": "робот DQ200",
                },
            ],
            "providers": [{"provider": "searxng", "status": "success"}],
        }

    def fetch_page_excerpt(self, url, *, max_chars):  # noqa: ANN001
        self.fetch_calls.append(url)
        transmission = "механика" if url.endswith("111111111/") else "робот DQ200"
        return {
            "ok": True,
            "url": url,
            "excerpt": f"[Skoda Octavia, {transmission}]\n# Repair\nПричина найдена.",
            "access_flags": [],
            "engine": "crawl4ai",
        }


class Drive2CaseResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        Drive2CaseResearch.clear_cache()
        self.search = _FakeSearch()
        self.service = Drive2CaseResearch(self.search)  # type: ignore[arg-type]

    def test_research_builds_bounded_queries_and_structures_public_case(self) -> None:
        result = self.service.research(
            query="рывок при включении задней передачи",
            vehicle="Skoda Octavia A7",
            engine="CZDA",
            transmission="DQ200",
            dtc_codes=["P17BF"],
            max_cases=3,
        )

        self.assertEqual(3, len(self.search.search_calls))
        self.assertEqual(1, len(self.search.fetch_calls))
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual("Ремонт DSG DQ200", result["cases"][0]["title"])
        self.assertTrue(result["cases"][0]["access"]["article_available"])
        self.assertTrue(result["cases"][0]["access"]["comments_limited"])
        self.assertFalse(result["cases"][0]["access"]["requires_human"])
        self.assertEqual("public_forum_case", result["cases"][0]["source_kind"])
        self.assertFalse(result["cache"]["hit"])

    def test_research_reuses_ttl_cache_without_retaining_page_payload(self) -> None:
        first = self.service.research(query="ремонт DQ200", vehicle="Skoda Octavia")
        second = self.service.research(query="ремонт DQ200", vehicle="Skoda Octavia")

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(3, len(self.search.search_calls))
        self.assertEqual(1, len(self.search.fetch_calls))
        self.assertNotIn("excerpt", second["cases"][0])

    def test_research_ranks_fetched_case_content_before_returning_result(self) -> None:
        ranking_search = _RankingSearch()
        service = Drive2CaseResearch(ranking_search)  # type: ignore[arg-type]

        result = service.research(query="рывок задней передачи", transmission="DQ200", max_cases=1)

        self.assertEqual("https://www.drive2.ru/l/222222222/", result["cases"][0]["url"])
        self.assertEqual(2, len(ranking_search.fetch_calls))
