from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ..storage.change_feed_store import (
    CHANGE_FEED_PAGE_DEFAULT,
    ChangeFeedProtocolError,
    ChangeFeedStore,
)
from .errors import ServiceError


class ChangeFeedService:
    """Bounded service contract for the durable CRM owner change feed."""

    def __init__(
        self,
        store: ChangeFeedStore,
        *,
        reconcile: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._reconcile = reconcile

    def _before_read(self) -> None:
        if self._reconcile is not None:
            self._reconcile()

    @staticmethod
    def _payload(payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ServiceError(
                "validation_error",
                "Change-feed payload must be a JSON object.",
                status_code=400,
            )
        return payload

    @staticmethod
    def _consumer(payload: dict[str, Any]) -> object:
        if "consumer_id" not in payload:
            raise ServiceError(
                "validation_error",
                "consumer_id is required.",
                status_code=400,
                details={"field": "consumer_id"},
            )
        return payload.get("consumer_id")

    @staticmethod
    def _translate_error(exc: Exception) -> ServiceError:
        if isinstance(exc, ChangeFeedProtocolError):
            return ServiceError(exc.code, exc.message, status_code=exc.status_code)
        if isinstance(exc, sqlite3.Error):
            return ServiceError(
                "change_feed_unavailable",
                "The durable CRM change feed is temporarily unavailable.",
                status_code=503,
            )
        return ServiceError(
            "change_feed_unavailable",
            "The durable CRM change feed could not reconcile its state.",
            status_code=503,
        )

    def bootstrap(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return feed metadata without opening a delivery or advancing ACK state."""

        request = self._payload(payload)
        try:
            self._before_read()
            result = self._store.bootstrap(self._consumer(request))
        except (ChangeFeedProtocolError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
            raise self._translate_error(exc) from exc
        return {"format": "crm_change_feed_bootstrap_v1", **result}

    def read(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = self._payload(payload)
        cursor = request.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ServiceError(
                "validation_error",
                "cursor must be an opaque string or null.",
                status_code=400,
                details={"field": "cursor"},
            )
        limit = request.get("limit", CHANGE_FEED_PAGE_DEFAULT)
        try:
            self._before_read()
            result = self._store.read_page(
                self._consumer(request),
                cursor=cursor,
                limit=limit,
            )
        except (ChangeFeedProtocolError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
            raise self._translate_error(exc) from exc
        return {"format": "crm_change_feed_page_v1", **result}

    def ack(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = self._payload(payload)
        ack_token = request.get("ack")
        if not isinstance(ack_token, str) or not ack_token.strip():
            raise ServiceError(
                "validation_error",
                "ack must be the opaque token returned with a change-feed page.",
                status_code=400,
                details={"field": "ack"},
            )
        try:
            self._before_read()
            result = self._store.acknowledge(self._consumer(request), ack_token)
        except (ChangeFeedProtocolError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
            raise self._translate_error(exc) from exc
        return {"format": "crm_change_feed_ack_v1", **result}
