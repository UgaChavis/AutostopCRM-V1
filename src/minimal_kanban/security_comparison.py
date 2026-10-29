"""Constant-time comparison for protocol credentials and ASCII signatures."""

from __future__ import annotations

import hmac


def ascii_secret_matches(expected: str, supplied: str) -> bool:
    # Protocol tokens/signatures are ASCII. Malformed HTTP/JSON credentials
    # must fail authentication rather than raise in compare_digest.
    return expected.isascii() and supplied.isascii() and hmac.compare_digest(expected, supplied)
