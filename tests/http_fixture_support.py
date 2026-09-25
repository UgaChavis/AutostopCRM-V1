from __future__ import annotations

from types import TracebackType


class FakeReadableResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._position = 0

    def __enter__(self) -> FakeReadableResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    def read(self, size: int | None = -1) -> bytes:
        end = (
            len(self._body)
            if size is None or size < 0
            else min(self._position + size, len(self._body))
        )
        chunk = self._body[self._position : end]
        self._position = end
        return chunk
