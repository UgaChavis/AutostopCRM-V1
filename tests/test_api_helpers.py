from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import (  # noqa: E402
    MAX_JSON_BODY_BYTES,
    _content_length_header,
    _json_response,
    _request_target_parts,
    _safe_request_target,
    _shared_file_clipboard_position,
)


class ApiHelperTests(unittest.TestCase):
    def test_shared_file_clipboard_position_rejects_non_finite_values(self) -> None:
        self.assertEqual(_shared_file_clipboard_position(float("inf")), 24)
        self.assertEqual(_shared_file_clipboard_position(float("nan")), 24)
        self.assertEqual(_shared_file_clipboard_position("-5"), 0)
        self.assertEqual(_shared_file_clipboard_position(1e308), 100_000)

    def test_safe_request_target_redacts_query_and_fragment(self) -> None:
        self.assertEqual(
            _safe_request_target("/missing?access_token=secret&x=1"),
            "/missing?<redacted>",
        )
        self.assertEqual(
            _safe_request_target("/missing#access_token=secret"),
            "/missing#<redacted>",
        )

    def test_request_target_parts_returns_none_for_malformed_absolute_target(self) -> None:
        self.assertIsNone(_request_target_parts("http://[::1"))

    def test_content_length_header_bounds_large_decimal_values(self) -> None:
        self.assertEqual(_content_length_header("123"), 123)
        self.assertEqual(_content_length_header("-1"), -1)
        self.assertEqual(_content_length_header("1e308"), None)
        self.assertEqual(_content_length_header(False), None)
        self.assertEqual(
            _content_length_header("9" * 128),
            MAX_JSON_BODY_BYTES + 1,
        )

    def test_json_response_handles_self_referential_data(self) -> None:
        data: dict[str, object] = {"ok": True, "scale": 1.25}
        data["self"] = data

        encoded = _json_response(ok=True, data=data, request_id="test-request")
        decoded = json.loads(encoded.decode("utf-8"))
        self.assertEqual(decoded["data"]["scale"], 1.25)
        node = decoded["data"]
        for _ in range(7):
            node = node["self"]

        self.assertIsInstance(node, str)


if __name__ == "__main__":
    unittest.main()
