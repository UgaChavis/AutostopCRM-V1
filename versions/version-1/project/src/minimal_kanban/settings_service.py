from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, replace
from logging import Logger
import urllib.error
import urllib.request

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .connection_card import GPT_CONNECTOR_REQUIRED_TOOL_NAMES
from .mcp.client import BoardApiClient, BoardApiTransportError
from .models import utc_now_iso
from .settings_models import (
    AUTH_MODE_VALUES,
    AuthSettings,
    DiagnosticsSettings,
    GeneralSettings,
    IntegrationSettings,
    LocalApiSettings,
    McpSettings,
    OpenAISettings,
    is_external_http_url,
    is_http_url,
)
from .settings_store import SettingsStore


@dataclass(slots=True, frozen=True)
class ConnectionCheckResult:
    target: str
    status: str
    message: str
    checked_at: str = ""
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "success"


@dataclass(slots=True, frozen=True)
class ConnectionTestSummary:
    tested_at: str
    local_api: ConnectionCheckResult
    mcp: ConnectionCheckResult
    external: ConnectionCheckResult
    openai: ConnectionCheckResult
    overall_status: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


class SettingsValidationError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Настройки содержат ошибки.")
        self.errors = errors


class SettingsService:
    def __init__(self, store: SettingsStore, logger: Logger) -> None:
        self._store = store
        self._logger = logger.getChild("settings")

    @property
    def settings_path(self):
        return self._store.path

    def load(self) -> IntegrationSettings:
        settings = self._store.read()
        normalized = self.normalize(settings)
        if normalized.to_dict() != settings.to_dict():
            self._store.write(normalized)
        self._logger.info("settings.load payload=%s", normalized.to_dict(redact_secrets=True))
        return normalized

    def save(self, settings: IntegrationSettings) -> IntegrationSettings:
        normalized = self.normalize(settings)
        self._ensure_valid(normalized)
        self._store.write(normalized)
        self._logger.info("settings.save payload=%s", normalized.to_dict(redact_secrets=True))
        return normalized

    def reset_to_defaults(self) -> IntegrationSettings:
        settings = self._store.reset()
        normalized = self.normalize(settings)
        if normalized.to_dict() != settings.to_dict():
            self._store.write(normalized)
        self._logger.info("settings.reset_to_defaults")
        return normalized

    def normalize(self, settings: IntegrationSettings) -> IntegrationSettings:
        normalized = IntegrationSettings.from_dict(settings.to_dict())
        local_api_token = normalized.local_api.local_api_bearer_token or normalized.auth.local_api_bearer_token
        mcp_token = normalized.mcp.mcp_bearer_token or normalized.auth.mcp_bearer_token
        normalized = replace(
            normalized,
            local_api=replace(normalized.local_api, local_api_bearer_token=local_api_token),
            mcp=replace(normalized.mcp, mcp_bearer_token=mcp_token),
            auth=replace(
                normalized.auth,
                local_api_bearer_token=local_api_token,
                mcp_bearer_token=mcp_token,
            ),
        )
        return normalized

    def validate(self, settings: IntegrationSettings) -> dict[str, str]:
        settings = self.normalize(settings)
        errors: dict[str, str] = {}

        if settings.auth.auth_mode not in AUTH_MODE_VALUES:
            errors["auth.auth_mode"] = "Режим авторизации должен быть none или bearer."

        if not settings.local_api.local_api_host:
            errors["local_api.local_api_host"] = "Укажите хост локального API."
        elif not is_http_url(settings.local_api.runtime_local_api_url):
            errors["local_api.local_api_host"] = "Хост и порт локального API формируют некорректный URL."
        if settings.local_api.local_api_port < 1 or settings.local_api.local_api_port > 65535:
            errors["local_api.local_api_port"] = "Порт локального API должен быть в диапазоне от 1 до 65535."
        if settings.local_api.local_api_base_url_override and not is_http_url(settings.local_api.local_api_base_url_override):
            errors["local_api.local_api_base_url_override"] = "Base URL override локального API должен начинаться с http:// или https://."
        if settings.local_api.local_api_auth_mode not in AUTH_MODE_VALUES:
            errors["local_api.local_api_auth_mode"] = "Режим авторизации локального API должен быть none или bearer."

        if not settings.mcp.mcp_host:
            errors["mcp.mcp_host"] = "Укажите хост MCP."
        elif not is_http_url(settings.mcp.local_mcp_url):
            errors["mcp.mcp_host"] = "Хост, порт и path MCP формируют некорректный URL."
        if settings.mcp.mcp_port < 1 or settings.mcp.mcp_port > 65535:
            errors["mcp.mcp_port"] = "Порт MCP должен быть в диапазоне от 1 до 65535."
        if not settings.mcp.mcp_path.startswith("/"):
            errors["mcp.mcp_path"] = "Path MCP должен начинаться с /."
        if settings.mcp.public_https_base_url and not is_http_url(settings.mcp.public_https_base_url):
            errors["mcp.public_https_base_url"] = "Public HTTPS Base URL должен начинаться с http:// или https://."
        if settings.mcp.tunnel_url and not is_http_url(settings.mcp.tunnel_url):
            errors["mcp.tunnel_url"] = "Tunnel URL должен начинаться с http:// или https://."
        if settings.mcp.full_mcp_url_override and not is_http_url(settings.mcp.full_mcp_url_override):
            errors["mcp.full_mcp_url_override"] = "Full MCP URL override должен начинаться с http:// или https://."
        if settings.mcp.mcp_auth_mode not in AUTH_MODE_VALUES:
            errors["mcp.mcp_auth_mode"] = "Режим авторизации MCP должен быть none или bearer."

        if not settings.openai.provider:
            errors["openai.provider"] = "Укажите provider."
        if not settings.openai.model:
            errors["openai.model"] = "Укажите model."
        if not is_http_url(settings.openai.base_url):
            errors["openai.base_url"] = "Base URL OpenAI-compatible API должен начинаться с http:// или https://."
        if settings.openai.timeout_seconds <= 0:
            errors["openai.timeout_seconds"] = "Timeout должен быть больше нуля."

        return errors

    def update_section(
        self,
        section_name: str,
        values: dict,
        *,
        settings: IntegrationSettings | None = None,
        persist: bool = False,
    ) -> IntegrationSettings:
        current = self.normalize(settings or self.load())
        name = str(section_name or "").strip().lower()

        if name == "general":
            updated = replace(current, general=GeneralSettings.from_dict({**current.general.to_dict(), **values}))
        elif name == "local_api":
            updated = replace(
                current,
                local_api=LocalApiSettings.from_dict({**current.local_api.to_dict(redact_secrets=False), **values}),
            )
        elif name == "mcp":
            updated = replace(current, mcp=McpSettings.from_dict({**current.mcp.to_dict(redact_secrets=False), **values}))
        elif name == "openai":
            updated = replace(current, openai=OpenAISettings.from_dict({**current.openai.to_dict(), **values}))
        elif name == "auth":
            updated = replace(current, auth=AuthSettings.from_dict({**current.auth.to_dict(redact_secrets=False), **values}))
        elif name == "diagnostics":
            updated = replace(current, diagnostics=DiagnosticsSettings.from_dict({**current.diagnostics.to_dict(), **values}))
        else:
            raise ValueError(f"Неизвестная секция настроек: {section_name}")

        normalized = self.normalize(updated)
        if persist:
            return self.save(normalized)
        return normalized

    def generate_token(self, *, length_bytes: int = 32) -> str:
        token = secrets.token_urlsafe(length_bytes)
        self._logger.info("settings.generate_token length_bytes=%s", length_bytes)
        return token

    def test_connections(self, settings: IntegrationSettings) -> ConnectionTestSummary:
        self._ensure_valid(settings)
        settings = self.normalize(settings)
        tested_at = utc_now_iso()
        local_api = self.test_local_api(settings)
        mcp = self.test_mcp_endpoint(settings)
        external = self.test_external_endpoint(settings)
        openai = self.test_openai_endpoint(settings)
        warnings = tuple(item for result in (local_api, mcp, external, openai) for item in result.warnings)
        errors = tuple(item for result in (local_api, mcp, external, openai) for item in result.errors)
        overall_status = self._calculate_overall_status((local_api.status, mcp.status, external.status, openai.status))
        summary = ConnectionTestSummary(
            tested_at=tested_at,
            local_api=local_api,
            mcp=mcp,
            external=external,
            openai=openai,
            overall_status=overall_status,
            warnings=warnings,
            errors=errors,
        )
        self._logger.info(
            "check.full overall=%s local_api=%s mcp=%s external=%s openai=%s warnings=%s errors=%s",
            summary.overall_status,
            summary.local_api.status,
            summary.mcp.status,
            summary.external.status,
            summary.openai.status,
            len(summary.warnings),
            len(summary.errors),
        )
        return summary

    def test_target(self, settings: IntegrationSettings, target: str) -> ConnectionCheckResult:
        self._ensure_valid(settings)
        settings = self.normalize(settings)
        normalized = str(target or "").strip().lower()
        if normalized == "local_api":
            return self.test_local_api(settings)
        if normalized == "mcp":
            return self.test_mcp_endpoint(settings)
        if normalized == "external":
            return self.test_external_endpoint(settings)
        if normalized == "openai":
            return self.test_openai_endpoint(settings)
        raise ValueError(f"Неизвестная цель проверки: {target}")

    def apply_test_summary(self, settings: IntegrationSettings, summary: ConnectionTestSummary) -> IntegrationSettings:
        diagnostics = DiagnosticsSettings(
            local_api_status=summary.local_api.status,
            local_api_message=summary.local_api.message,
            mcp_status=summary.mcp.status,
            mcp_message=summary.mcp.message,
            external_status=summary.external.status,
            external_message=summary.external.message,
            openai_status=summary.openai.status,
            openai_message=summary.openai.message,
            overall_status=summary.overall_status,
            last_local_api_check=summary.local_api.checked_at,
            last_mcp_check=summary.mcp.checked_at,
            last_external_endpoint_check=summary.external.checked_at,
            last_openai_check=summary.openai.checked_at,
            last_full_check=summary.tested_at,
            last_errors=list(summary.errors),
            last_warnings=list(summary.warnings),
        )
        return replace(settings, diagnostics=diagnostics)

    def apply_test_result(
        self,
        settings: IntegrationSettings,
        target: str,
        result: ConnectionCheckResult,
        tested_at: str | None = None,
    ) -> IntegrationSettings:
        current = settings.diagnostics.to_dict()
        checked_at = tested_at or result.checked_at
        if target == "local_api":
            current["local_api_status"] = result.status
            current["local_api_message"] = result.message
            current["last_local_api_check"] = checked_at
        elif target == "mcp":
            current["mcp_status"] = result.status
            current["mcp_message"] = result.message
            current["last_mcp_check"] = checked_at
        elif target == "external":
            current["external_status"] = result.status
            current["external_message"] = result.message
            current["last_external_endpoint_check"] = checked_at
        elif target == "openai":
            current["openai_status"] = result.status
            current["openai_message"] = result.message
            current["last_openai_check"] = checked_at
        else:
            raise ValueError(f"Неизвестная цель проверки: {target}")

        current["last_errors"] = list(result.errors)
        current["last_warnings"] = list(result.warnings)
        current["overall_status"] = self._calculate_overall_status(
            (
                current.get("local_api_status", "not_tested"),
                current.get("mcp_status", "not_tested"),
                current.get("external_status", "not_tested"),
                current.get("openai_status", "not_tested"),
            )
        )
        return replace(settings, diagnostics=DiagnosticsSettings.from_dict(current))

    def test_local_api(self, settings: IntegrationSettings) -> ConnectionCheckResult:
        checked_at = utc_now_iso()
        warnings: list[str] = []
        errors: list[str] = []

        if not settings.general.use_local_api:
            message = "Проверка локального API пропущена: локальный API отключён в настройках."
            return ConnectionCheckResult("local_api", "skipped", message, checked_at, (), ())

        try:
            client = BoardApiClient(
                settings.local_api.runtime_local_api_url,
                bearer_token=self._local_api_token(settings),
                timeout_seconds=float(settings.openai.timeout_seconds),
                logger=self._logger,
            )
            health = client.health()
        except BoardApiTransportError as exc:
            text = str(exc)
            errors.append(text)
            return ConnectionCheckResult("local_api", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if not health.get("ok"):
            text = self._extract_error_message(health, fallback="Локальный API ответил ошибкой.")
            errors.append(text)
            return ConnectionCheckResult("local_api", "failed", text, checked_at, tuple(warnings), tuple(errors))

        smoke_summary = self._run_local_api_smoke(client, settings)
        if smoke_summary:
            warnings.extend(smoke_summary["warnings"])
            errors.extend(smoke_summary["errors"])

        if errors:
            return ConnectionCheckResult(
                "local_api",
                "failed",
                "Локальный API отвечает, но smoke check завершился ошибкой.",
                checked_at,
                tuple(warnings),
                tuple(errors),
            )

        message = f"Локальный API доступен по адресу {settings.local_api.runtime_local_api_url}."
        if smoke_summary and smoke_summary["steps"]:
            message += f" Smoke: {', '.join(smoke_summary['steps'])}."
        return ConnectionCheckResult("local_api", "success", message, checked_at, tuple(warnings), tuple(errors))

    def test_mcp_endpoint(self, settings: IntegrationSettings) -> ConnectionCheckResult:
        checked_at = utc_now_iso()
        warnings: list[str] = []
        errors: list[str] = []

        if not settings.mcp.mcp_enabled:
            message = "MCP check skipped: MCP is disabled in settings."
            return ConnectionCheckResult("mcp", "skipped", message, checked_at, (), ())

        try:
            probe = asyncio.run(
                self._probe_mcp_server(
                    settings.mcp.local_mcp_url,
                    bearer_token=self._mcp_token(settings),
                    timeout_seconds=float(settings.openai.timeout_seconds),
                    expect_oauth=settings.mcp.mcp_auth_mode == "bearer",
                )
            )
        except RuntimeError as exc:
            text = str(exc)
            errors.append(text)
            return ConnectionCheckResult("mcp", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if not probe["list_columns_ok"]:
            text = "MCP answered, but list_columns tool call failed."
            errors.append(text)
            return ConnectionCheckResult("mcp", "failed", text, checked_at, tuple(warnings), tuple(errors))

        missing_tools = probe["missing_required_tools"]
        if missing_tools:
            text = "MCP is missing GPT-critical tools: " + ", ".join(missing_tools) + "."
            errors.append(text)
            return ConnectionCheckResult("mcp", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if not probe["gpt_wall_ok"]:
            text = "MCP answered, but get_gpt_wall did not return a usable GPT context."
            errors.append(text)
            return ConnectionCheckResult("mcp", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if settings.mcp.mcp_auth_mode == "bearer" and (
            not probe["oauth_authorization_server_ok"] or not probe["oauth_protected_resource_ok"]
        ):
            text = (
                "Bearer-mode MCP does not expose complete OAuth metadata "
                "(.well-known/oauth-authorization-server / oauth-protected-resource)."
            )
            errors.append(text)
            return ConnectionCheckResult("mcp", "failed", text, checked_at, tuple(warnings), tuple(errors))

        message = (
            f"Local MCP is reachable, tools found: {probe['tools_count']}, "
            "GPT-critical tools are present, get_gpt_wall works."
        )
        if settings.mcp.mcp_auth_mode == "bearer":
            message += " OAuth metadata is available."
        return ConnectionCheckResult("mcp", "success", message, checked_at, tuple(warnings), tuple(errors))

    def test_external_endpoint(self, settings: IntegrationSettings) -> ConnectionCheckResult:
        checked_at = utc_now_iso()
        warnings: list[str] = []
        errors: list[str] = []
        url = settings.mcp.effective_mcp_url

        if not url:
            message = "External endpoint check skipped: no public URL, tunnel URL, or full MCP URL is configured."
            return ConnectionCheckResult("external", "skipped", message, checked_at, (), ())

        if not url.startswith("https://"):
            text = "Effective MCP URL must start with https:// for ChatGPT connector access."
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if not is_external_http_url(url):
            text = "Effective MCP URL still points to localhost/127.0.0.1 and is not usable from ChatGPT."
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        try:
            probe = asyncio.run(
                self._probe_mcp_server(
                    url,
                    bearer_token=self._mcp_token(settings),
                    timeout_seconds=float(settings.openai.timeout_seconds),
                    expect_oauth=settings.mcp.mcp_auth_mode == "bearer",
                )
            )
        except RuntimeError as exc:
            text = str(exc)
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if not probe["list_columns_ok"]:
            text = "External MCP endpoint answered, but list_columns tool call failed."
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        missing_tools = probe["missing_required_tools"]
        if missing_tools:
            text = "External MCP endpoint is missing GPT-critical tools: " + ", ".join(missing_tools) + "."
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if not probe["gpt_wall_ok"]:
            text = "External MCP endpoint answered, but get_gpt_wall did not return a usable GPT context."
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if settings.mcp.mcp_auth_mode == "bearer" and (
            not probe["oauth_authorization_server_ok"] or not probe["oauth_protected_resource_ok"]
        ):
            text = "External MCP endpoint does not expose complete OAuth metadata for connector linking."
            errors.append(text)
            return ConnectionCheckResult("external", "failed", text, checked_at, tuple(warnings), tuple(errors))

        message = (
            f"External MCP endpoint is reachable at {url}, "
            f"tools found: {probe['tools_count']}, get_gpt_wall works."
        )
        if settings.mcp.mcp_auth_mode == "bearer":
            message += " OAuth metadata is available."
        return ConnectionCheckResult("external", "success", message, checked_at, tuple(warnings), tuple(errors))

    def test_openai_endpoint(self, settings: IntegrationSettings) -> ConnectionCheckResult:
        checked_at = utc_now_iso()
        warnings: list[str] = []
        errors: list[str] = []

        if not settings.general.integration_enabled:
            message = "Проверка OpenAI-совместимого API пропущена: интеграция выключена."
            return ConnectionCheckResult("openai", "skipped", message, checked_at, (), ())

        token = settings.auth.openai_api_key or settings.auth.access_token
        if not token:
            text = "Для проверки OpenAI-совместимого API нужен OpenAI API key или access token."
            errors.append(text)
            return ConnectionCheckResult("openai", "failed", text, checked_at, tuple(warnings), tuple(errors))

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if settings.openai.organization_id:
            headers["OpenAI-Organization"] = settings.openai.organization_id
        if settings.openai.project_id:
            headers["OpenAI-Project"] = settings.openai.project_id

        try:
            status_code, payload = self._request_json(
                f"{settings.openai.base_url.rstrip('/')}/models",
                method="GET",
                headers=headers,
                timeout_seconds=settings.openai.timeout_seconds,
            )
        except RuntimeError as exc:
            text = str(exc)
            errors.append(text)
            return ConnectionCheckResult("openai", "failed", text, checked_at, tuple(warnings), tuple(errors))

        if status_code != 200:
            text = f"{self._extract_error_message(payload, fallback='OpenAI-compatible endpoint ответил ошибкой.')} Код HTTP: {status_code}."
            errors.append(text)
            return ConnectionCheckResult("openai", "failed", text, checked_at, tuple(warnings), tuple(errors))

        models = payload.get("data")
        if isinstance(models, list):
            message = f"OpenAI-compatible endpoint доступен, моделей в ответе: {len(models)}."
        else:
            message = "OpenAI-compatible endpoint доступен и вернул корректный JSON."
        return ConnectionCheckResult("openai", "success", message, checked_at, tuple(warnings), tuple(errors))

    def _ensure_valid(self, settings: IntegrationSettings) -> None:
        errors = self.validate(settings)
        if errors:
            self._logger.warning("settings.validation_failed errors=%s", errors)
            raise SettingsValidationError(errors)

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str] | None,
        timeout_seconds: int | float,
    ) -> tuple[int, dict]:
        request = urllib.request.Request(url, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return response.status, payload
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except json.JSONDecodeError:
                payload = {}
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Не удалось подключиться к адресу {url}: {type(exc).__name__}.") from exc

    async def _probe_mcp_server(
        self,
        url: str,
        *,
        bearer_token: str | None,
        timeout_seconds: float,
        expect_oauth: bool = False,
    ) -> dict[str, object]:
        headers = {}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        try:
            async with httpx.AsyncClient(headers=headers, timeout=timeout_seconds, follow_redirects=True) as http_client:
                preflight = await http_client.get(url)
                if preflight.status_code == 421 and "Invalid Host header" in preflight.text:
                    raise RuntimeError(
                        "MCP runtime rejects the external Host header. Allow the tunnel/external host in MCP settings."
                    )
                async with streamable_http_client(url, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        tool_names = {tool.name for tool in tools.tools}
                        result = await session.call_tool("list_columns", {})
                        gpt_wall_result = await session.call_tool("get_gpt_wall", {"include_archived": True, "event_limit": 8})
                        gpt_wall_payload = getattr(gpt_wall_result, "structuredContent", {}) or {}
                        gpt_wall_data = gpt_wall_payload.get("data", {}) if isinstance(gpt_wall_payload, dict) else {}
                        missing_required_tools = [
                            tool_name for tool_name in GPT_CONNECTOR_REQUIRED_TOOL_NAMES if tool_name not in tool_names
                        ]

                        oauth_authorization_server_ok = False
                        oauth_protected_resource_ok = False
                        if expect_oauth:
                            parsed = httpx.URL(url)
                            auth_base = f"{parsed.scheme}://{parsed.host}"
                            if parsed.port is not None:
                                auth_base += f":{parsed.port}"
                            protected_path = parsed.path or "/mcp"
                            metadata = await http_client.get(f"{auth_base}/.well-known/oauth-authorization-server")
                            protected = await http_client.get(
                                f"{auth_base}/.well-known/oauth-protected-resource{protected_path}"
                            )
                            oauth_authorization_server_ok = metadata.status_code == 200
                            oauth_protected_resource_ok = protected.status_code == 200

                        return {
                            "tools_count": len(tool_names),
                            "tool_names": sorted(tool_names),
                            "list_columns_ok": bool(getattr(result, "structuredContent", {}).get("ok")),
                            "missing_required_tools": missing_required_tools,
                            "gpt_wall_ok": bool(gpt_wall_payload.get("ok")) and bool(gpt_wall_data.get("text")),
                            "oauth_authorization_server_ok": oauth_authorization_server_ok,
                            "oauth_protected_resource_ok": oauth_protected_resource_ok,
                        }
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Unable to connect to MCP endpoint {url}: {type(exc).__name__}.") from exc

    def _run_local_api_smoke(self, client: BoardApiClient, settings: IntegrationSettings) -> dict[str, list[str]] | None:
        if not settings.general.test_mode:
            return None

        steps: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []

        try:
            columns = client.list_columns()
            if not columns.get("ok"):
                raise RuntimeError(self._extract_error_message(columns, fallback="list_columns вернул ошибку."))
            steps.append("list_columns")

            created = client.create_card(title="__selfcheck__", description="Автотест интеграции", deadline={"minutes": 5})
            if not created.get("ok"):
                raise RuntimeError(self._extract_error_message(created, fallback="create_card вернул ошибку."))
            card = created["data"]["card"]
            card_id = card["id"]
            steps.append("create_card")

            moved = client.move_card(card_id=card_id, column="done")
            if not moved.get("ok"):
                raise RuntimeError(self._extract_error_message(moved, fallback="move_card вернул ошибку."))
            steps.append("move_card")

            archived = client.archive_card(card_id=card_id)
            if not archived.get("ok"):
                raise RuntimeError(self._extract_error_message(archived, fallback="archive_card вернул ошибку."))
            steps.append("archive_card")
        except Exception as exc:
            errors.append(str(exc))

        return {
            "steps": steps,
            "warnings": warnings,
            "errors": errors,
        }

    def _extract_error_message(self, payload: dict, *, fallback: str) -> str:
        if not isinstance(payload, dict):
            return fallback
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return fallback

    def _calculate_overall_status(self, statuses: tuple[str, ...]) -> str:
        if any(status == "failed" for status in statuses):
            return "failed"
        if any(status == "warning" for status in statuses):
            return "warning"
        if any(status == "success" for status in statuses):
            return "success"
        if all(status in {"skipped", "not_tested"} for status in statuses):
            return "skipped" if any(status == "skipped" for status in statuses) else "not_tested"
        return "not_tested"

    def _local_api_token(self, settings: IntegrationSettings) -> str | None:
        if settings.local_api.local_api_auth_mode != "bearer":
            return None
        return settings.local_api.local_api_bearer_token or settings.auth.local_api_bearer_token or settings.auth.access_token or None

    def _mcp_token(self, settings: IntegrationSettings) -> str | None:
        if settings.mcp.mcp_auth_mode != "bearer":
            return None
        return settings.mcp.mcp_bearer_token or settings.auth.mcp_bearer_token or settings.auth.access_token or None
