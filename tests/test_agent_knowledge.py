from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent import knowledge as knowledge_module  # noqa: E402
from minimal_kanban.agent.knowledge import (  # noqa: E402
    _build_document_excerpt,
    _load_document_text,
    _lookup_controlled_internet,
    build_ai_chat_knowledge_packet,
)


class _FakeLookupService:
    def __init__(self) -> None:
        self.search_limits: list[int] = []
        self.fetch_max_chars: list[int] = []

    def search_web(self, *, query: str, limit: int, allowed_domains=None):  # noqa: ANN001
        self.search_limits.append(limit)
        return {
            "results": [
                {
                    "title": f"Result {index}",
                    "url": f"https://example.com/{index}",
                    "domain": "example.com",
                    "snippet": "VIN specs and catalog",
                }
                for index in range(10)
            ]
        }

    def fetch_page_excerpt(self, *, url: str, max_chars: int):
        self.fetch_max_chars.append(max_chars)
        return {"url": url, "excerpt": "detailed excerpt"}


class _MalformedUrlLookupService:
    def search_web(self, *, query: str, limit: int, allowed_domains=None):  # noqa: ANN001
        return {
            "results": [
                {
                    "title": "Bad URL",
                    "url": "https://[::1",
                    "snippet": "malformed URL should not crash",
                },
                {
                    "title": "Port URL",
                    "url": "https://example.com.:443/path",
                    "snippet": "domain fallback should be host only",
                },
            ]
        }

    def fetch_page_excerpt(self, *, url: str, max_chars: int):  # noqa: ANN001
        raise AssertionError("malformed first URL should not be fetched")


class AgentKnowledgeTests(unittest.TestCase):
    def test_chat_knowledge_packet_bounds_bad_document_and_internet_limits(self) -> None:
        lookup = _FakeLookupService()

        packet = build_ai_chat_knowledge_packet(
            prompt="Найди VIN specs и проверь guide документы",
            lookup_service=lookup,  # type: ignore[arg-type]
            document_limit=1e308,
            internet_limit="bad",  # type: ignore[arg-type]
        )

        self.assertLessEqual(packet["documents"]["count"], 3)
        self.assertEqual(lookup.search_limits, [3])
        self.assertEqual(packet["internet"]["count"], 3)

    def test_document_excerpt_falls_back_for_non_finite_max_chars(self) -> None:
        excerpt = _build_document_excerpt("x" * 5000, [], max_chars=float("inf"))

        self.assertEqual(len(excerpt), 1200)

    def test_load_document_text_rejects_oversized_curated_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "huge.md").write_text("x" * 32, encoding="utf-8")

            _load_document_text.cache_clear()
            with (
                patch.object(knowledge_module, "_REPO_ROOT", root),
                patch.object(knowledge_module, "CURATED_DOCUMENT_MAX_BYTES", 8),
            ):
                self.assertEqual(_load_document_text("huge.md"), "")
            _load_document_text.cache_clear()

    def test_load_document_text_rejects_paths_outside_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            root.mkdir()
            outside = Path(temp_dir) / "outside.md"
            outside.write_text("secret", encoding="utf-8")

            _load_document_text.cache_clear()
            with patch.object(knowledge_module, "_REPO_ROOT", root):
                self.assertEqual(_load_document_text("../outside.md"), "")
            _load_document_text.cache_clear()

    def test_controlled_internet_lookup_bounds_bad_limit(self) -> None:
        lookup = _FakeLookupService()

        packet = _lookup_controlled_internet(
            prompt="vin specs",
            context={},
            lookup_service=lookup,  # type: ignore[arg-type]
            limit="bad",  # type: ignore[arg-type]
        )

        self.assertEqual(lookup.search_limits, [3])
        self.assertEqual(packet["count"], 3)
        self.assertEqual(lookup.fetch_max_chars, [1200])

    def test_controlled_internet_lookup_handles_malformed_result_urls(self) -> None:
        packet = _lookup_controlled_internet(
            prompt="vin specs",
            context={},
            lookup_service=_MalformedUrlLookupService(),  # type: ignore[arg-type]
            limit=2,
        )

        self.assertEqual(packet["count"], 2)
        self.assertEqual(packet["items"][0]["domain"], "")
        self.assertEqual(packet["items"][1]["domain"], "example.com")


if __name__ == "__main__":
    unittest.main()
