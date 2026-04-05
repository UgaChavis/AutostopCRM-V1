from __future__ import annotations

import json
import urllib.error
import urllib.request
from logging import Logger

from ..config import get_api_port, get_api_port_fallback_limit


class BoardApiTransportError(RuntimeError):
    pass


def candidate_api_urls() -> list[str]:
    start_port = get_api_port()
    fallback_limit = get_api_port_fallback_limit()
    return [f"http://127.0.0.1:{port}" for port in range(start_port, start_port + fallback_limit)]


def discover_board_api(*, bearer_token: str | None = None, timeout_seconds: float = 1.0) -> str | None:
    for base_url in candidate_api_urls():
        client = BoardApiClient(base_url, bearer_token=bearer_token, timeout_seconds=timeout_seconds)
        try:
            response = client.health()
        except BoardApiTransportError:
            continue
        if response.get("ok"):
            return base_url
    return None


class BoardApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        timeout_seconds: float = 10.0,
        logger: Logger | None = None,
        default_source: str = "mcp",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._logger = logger
        self._default_source = default_source

    def health(self) -> dict:
        return self._request("/api/health", method="GET")

    def list_columns(self) -> dict:
        return self._request("/api/list_columns", method="GET")

    def create_column(self, label: str, *, actor_name: str | None = None) -> dict:
        return self._request("/api/create_column", self._with_identity({"label": label}, actor_name=actor_name))

    def create_sticky(
        self,
        *,
        text: str,
        x: int = 0,
        y: int = 0,
        deadline: dict,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"text": text, "x": x, "y": y, "deadline": deadline}
        return self._request("/api/create_sticky", self._with_identity(payload, actor_name=actor_name))

    def get_cards(self, *, include_archived: bool = False) -> dict:
        return self._request("/api/get_cards", {"include_archived": include_archived})

    def get_card(self, card_id: str) -> dict:
        return self._request("/api/get_card", {"card_id": card_id})

    def get_board_snapshot(self, *, archive_limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if archive_limit is not None:
            payload["archive_limit"] = archive_limit
        return self._request("/api/get_board_snapshot", payload or None, method="GET" if not payload else "POST")

    def get_board_context(self) -> dict:
        return self._request("/api/get_board_context", method="GET")

    def update_board_settings(self, *, board_scale: float, actor_name: str | None = None) -> dict:
        payload: dict[str, object] = {"board_scale": board_scale}
        return self._request("/api/update_board_settings", self._with_identity(payload, actor_name=actor_name))

    def get_gpt_wall(self, *, include_archived: bool = True, event_limit: int | None = None) -> dict:
        payload: dict[str, object] = {"include_archived": include_archived}
        if event_limit is not None:
            payload["event_limit"] = event_limit
        return self._request("/api/get_gpt_wall", payload, method="POST")

    def autofill_vehicle_data(
        self,
        *,
        raw_text: str = "",
        image_base64: str | None = None,
        image_filename: str | None = None,
        image_mime_type: str | None = None,
        vehicle_profile: dict[str, object] | None = None,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"raw_text": raw_text}
        if image_base64 is not None:
            payload["image_base64"] = image_base64
        if image_filename is not None:
            payload["image_filename"] = image_filename
        if image_mime_type is not None:
            payload["image_mime_type"] = image_mime_type
        if vehicle_profile is not None:
            payload["vehicle_profile"] = vehicle_profile
        if vehicle is not None:
            payload["vehicle"] = vehicle
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        return self._request("/api/autofill_vehicle_data", payload)

    def get_card_log(self, card_id: str) -> dict:
        return self._request("/api/get_card_log", {"card_id": card_id})

    def list_archived_cards(self, *, limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        return self._request("/api/list_archived_cards", payload or None, method="GET" if not payload else "POST")

    def search_cards(
        self,
        *,
        query: str | None = None,
        include_archived: bool = False,
        column: str | None = None,
        tag: str | None = None,
        indicator: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"include_archived": include_archived}
        if query is not None:
            payload["query"] = query
        if column:
            payload["column"] = column
        if tag:
            payload["tag"] = tag
        if indicator:
            payload["indicator"] = indicator
        if status:
            payload["status"] = status
        if limit is not None:
            payload["limit"] = limit
        return self._request("/api/search_cards", payload)

    def create_card(
        self,
        *,
        vehicle: str = "",
        title: str,
        description: str = "",
        column: str | None = None,
        tags: list[str] | None = None,
        deadline: dict,
        vehicle_profile: dict[str, object] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "vehicle": vehicle,
            "title": title,
            "description": description,
            "deadline": deadline,
        }
        if column:
            payload["column"] = column
        if tags is not None:
            payload["tags"] = tags
        if vehicle_profile is not None:
            payload["vehicle_profile"] = vehicle_profile
        return self._request("/api/create_card", self._with_identity(payload, actor_name=actor_name))

    def update_card(
        self,
        *,
        card_id: str,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        deadline: dict | None = None,
        vehicle_profile: dict[str, object] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if vehicle is not None:
            payload["vehicle"] = vehicle
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = tags
        if deadline is not None:
            payload["deadline"] = deadline
        if vehicle_profile is not None:
            payload["vehicle_profile"] = vehicle_profile
        return self._request("/api/update_card", self._with_identity(payload, actor_name=actor_name))

    def update_sticky(
        self,
        *,
        sticky_id: str,
        text: str | None = None,
        deadline: dict | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"sticky_id": sticky_id}
        if text is not None:
            payload["text"] = text
        if deadline is not None:
            payload["deadline"] = deadline
        return self._request("/api/update_sticky", self._with_identity(payload, actor_name=actor_name))

    def move_sticky(self, *, sticky_id: str, x: int, y: int, actor_name: str | None = None) -> dict:
        payload: dict[str, object] = {"sticky_id": sticky_id, "x": x, "y": y}
        return self._request("/api/move_sticky", self._with_identity(payload, actor_name=actor_name))

    def delete_sticky(self, *, sticky_id: str, actor_name: str | None = None) -> dict:
        return self._request("/api/delete_sticky", self._with_identity({"sticky_id": sticky_id}, actor_name=actor_name))

    def set_card_deadline(self, *, card_id: str, deadline: dict, actor_name: str | None = None) -> dict:
        payload = {"card_id": card_id, "deadline": deadline}
        return self._request("/api/set_card_deadline", self._with_identity(payload, actor_name=actor_name))

    def set_card_indicator(self, *, card_id: str, indicator: str, actor_name: str | None = None) -> dict:
        payload = {"card_id": card_id, "indicator": indicator}
        return self._request("/api/set_card_indicator", self._with_identity(payload, actor_name=actor_name))

    def move_card(self, *, card_id: str, column: str, actor_name: str | None = None) -> dict:
        payload = {"card_id": card_id, "column": column}
        return self._request("/api/move_card", self._with_identity(payload, actor_name=actor_name))

    def bulk_move_cards(self, *, card_ids: list[str], column: str, actor_name: str | None = None) -> dict:
        payload: dict[str, object] = {"card_ids": card_ids, "column": column}
        return self._request("/api/bulk_move_cards", self._with_identity(payload, actor_name=actor_name))

    def archive_card(self, *, card_id: str, actor_name: str | None = None) -> dict:
        return self._request("/api/archive_card", self._with_identity({"card_id": card_id}, actor_name=actor_name))

    def restore_card(self, *, card_id: str, column: str | None = None, actor_name: str | None = None) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if column:
            payload["column"] = column
        return self._request("/api/restore_card", self._with_identity(payload, actor_name=actor_name))

    def list_overdue_cards(self, *, include_archived: bool = False) -> dict:
        return self._request("/api/list_overdue_cards", {"include_archived": include_archived}, method="POST")

    def _with_identity(self, payload: dict[str, object], *, actor_name: str | None = None) -> dict[str, object]:
        enriched = dict(payload)
        enriched["source"] = self._default_source
        if actor_name:
            enriched["actor_name"] = actor_name
        return enriched

    def _request(self, path: str, payload: dict | None = None, *, method: str = "POST") -> dict:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        request = urllib.request.Request(
            self._compose_url(path),
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                self._log("board_api_request path=%s status=%s", path, response.status)
                return parsed
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except json.JSONDecodeError as json_error:  # pragma: no cover
                raise BoardApiTransportError(f"Локальный API вернул некорректный JSON для {path}.") from json_error
            self._log("board_api_request path=%s status=%s error=%s", path, exc.code, payload.get("error"))
            return payload
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BoardApiTransportError(f"Не удалось подключиться к локальному API по адресу {self.base_url}.") from exc

    def _log(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _compose_url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        if self.base_url.endswith("/api") and normalized_path.startswith("/api/"):
            normalized_path = normalized_path[4:]
        return f"{self.base_url}{normalized_path}"
