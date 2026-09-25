from __future__ import annotations

import unittest

if __package__:
    from tests.http_fixture_support import FakeReadableResponse
else:
    from http_fixture_support import FakeReadableResponse


class HttpFixtureSupportTests(unittest.TestCase):
    def test_read_returns_and_consumes_the_requested_bytes(self) -> None:
        response = FakeReadableResponse(b"abcdef")

        self.assertEqual(response.read(2), b"ab")
        self.assertEqual(response.read(1), b"c")
        self.assertEqual(response.read(), b"def")
        self.assertEqual(response.read(), b"")


if __name__ == "__main__":
    unittest.main()
