from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .config import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PATH,
    DEFAULT_MCP_PORT,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENAI_PROVIDER,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)


SETTINGS_SCHEMA_VERSION = 3
SECRET_REDACTION = "[скрыто]"
CONNECTION_STATUS_VALUES = ("not_tested", "success", "failed", "skipped", "warning")
AUTH_MODE_VALUES = ("none", "bearer")
DEFAULT_ALLOWED_HOST_PATTERNS = ("127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*")
DEFAULT_ALLOWED_ORIGIN_PATTERNS = ("http://127.0.0.1", "http://localhost", "http://[::1]", "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def normalize_text(value, *, default: str = "", limit: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    if limit is not None:
        text = text[:limit]
    return text


def normalize_bool(value, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return default


def normalize_int(value, *, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None and parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed


def normalize_choice(value, *, values: tuple[str, ...], default: str) -> str:
    normalized = normalize_text(value, default=default, limit=64).lower()
    if normalized not in values:
        return default
    return normalized


def normalize_host(value, *, default: str) -> str:
    host = normalize_text(value, default=default, limit=255)
    if " " in host:
        return default
    return host


def normalize_path(value, *, default: str) -> str:
    path = normalize_text(value, default=default, limit=255)
    if not path:
        return default
    return path if path.startswith("/") else f"/{path}"


def normalize_status(value, *, default: str = "not_tested") -> str:
    status = normalize_text(value, default=default, limit=32).lower()
    if status not in CONNECTION_STATUS_VALUES:
        return default
    return status


def normalize_messages(value, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = normalize_text(item, default="", limit=500)
        if text:
            result.append(text)
    return result


def normalize_string_list(value, *, limit: int = 32, item_limit: int = 255) -> list[str]:
    if isinstance(value, str):
        raw_values = [item for chunk in value.splitlines() for item in chunk.split(",")]
    elif isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_values[:limit]:
        text = normalize_text(item, default="", limit=item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def unique_strings(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value, default="", limit=255)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_external_http_url(value: str) -> bool:
    if not is_http_url(value):
        return False
    host = (urlsplit(value).hostname or "").lower()
    return host not in {"127.0.0.1", "localhost", ""}


def normalize_url(value, *, default: str = "", limit: int = 500) -> str:
    url = normalize_text(value, default=default, limit=limit)
    return url.rstrip("/")


def build_http_url(host: str, port: int, path: str = "") -> str:
    clean_path = path.strip()
    if clean_path and not clean_path.startswith("/"):
        clean_path = f"/{clean_path}"
    return f"http://{host}:{port}{clean_path}"


def build_endpoint_from_base(base_url: str, path: str) -> str:
    clean_base = normalize_url(base_url, default="")
    if not clean_base:
        return ""
    clean_path = normalize_path(path, default="/")
    return f"{clean_base}{clean_path}"


def _header_host(hostname: str) -> str:
    host = normalize_text(hostname, default="", limit=255)
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def derive_allowed_hosts(*urls: str, extra_hosts=None) -> list[str]:
    values: list[str] = list(DEFAULT_ALLOWED_HOST_PATTERNS)
    for url in urls:
        if not is_http_url(url):
            continue
        parsed = urlsplit(url)
        host = _header_host(parsed.hostname or "")
        if not host:
            continue
        values.append(host)
        if parsed.port:
            values.append(f"{host}:{parsed.port}")
        else:
            values.append(f"{host}:*")
    if extra_hosts:
        values.extend(normalize_string_list(extra_hosts))
    return unique_strings(values)


def derive_allowed_origins(*urls: str, extra_origins=None) -> list[str]:
    values: list[str] = list(DEFAULT_ALLOWED_ORIGIN_PATTERNS)
    for url in urls:
        if not is_http_url(url):
            continue
        parsed = urlsplit(url)
        scheme = (parsed.scheme or "").lower()
        host = _header_host(parsed.hostname or "")
        if not scheme or not host:
            continue
        if parsed.port:
            values.append(f"{scheme}://{host}:{parsed.port}")
        else:
            values.append(f"{scheme}://{host}")
            values.append(f"{scheme}://{host}:*")
    if extra_origins:
        values.extend(normalize_string_list(extra_origins))
    return unique_strings(values)


@dataclass(slots=True, frozen=True)
class GeneralSettings:
    integration_enabled: bool = True
    use_local_api: bool = True
    auto_connect_on_startup: bool = True
    test_mode: bool = True

    def to_dict(self) -> dict:
        return {
            "integration_enabled": self.integration_enabled,
            "use_local_api": self.use_local_api,
            "auto_connect_on_startup": self.auto_connect_on_startup,
            "test_mode": self.test_mode,
        }

    @classmethod
    def from_dict(cls, payload: dict | None, *, legacy_general: dict | None = None) -> "GeneralSettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_general = legacy_general if isinstance(legacy_general, dict) else {}
        return cls(
            integration_enabled=normalize_bool(payload.get("integration_enabled", legacy_general.get("integration_enabled")), default=True),
            use_local_api=normalize_bool(payload.get("use_local_api", legacy_general.get("use_local_api")), default=True),
            auto_connect_on_startup=normalize_bool(
                payload.get("auto_connect_on_startup", legacy_general.get("auto_connect_on_startup")),
                default=True,
            ),
            test_mode=normalize_bool(payload.get("test_mode", legacy_general.get("test_mode")), default=True),
        )


@dataclass(slots=True, frozen=True)
class LocalApiSettings:
    local_api_host: str = DEFAULT_API_HOST
    local_api_port: int = DEFAULT_API_PORT
    local_api_base_url_override: str = ""
    local_api_auth_mode: str = "none"
    local_api_bearer_token: str = ""

    @property
    def runtime_local_api_url(self) -> str:
        return build_http_url(self.local_api_host, self.local_api_port)

    @property
    def effective_local_api_url(self) -> str:
        return self.local_api_base_url_override or self.runtime_local_api_url

    @property
    def local_api_health_url(self) -> str:
        return f"{self.runtime_local_api_url}/api/health"

    def to_dict(self, *, redact_secrets: bool = False) -> dict:
        return {
            "local_api_host": self.local_api_host,
            "local_api_port": self.local_api_port,
            "runtime_local_api_url": self.runtime_local_api_url,
            "local_api_base_url_override": self.local_api_base_url_override,
            "effective_local_api_url": self.effective_local_api_url,
            "local_api_health_url": self.local_api_health_url,
            "local_api_auth_mode": self.local_api_auth_mode,
            "local_api_bearer_token": SECRET_REDACTION if redact_secrets and self.local_api_bearer_token else self.local_api_bearer_token,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict | None,
        *,
        legacy_general: dict | None = None,
        legacy_credentials: dict | None = None,
    ) -> "LocalApiSettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_general = legacy_general if isinstance(legacy_general, dict) else {}
        legacy_credentials = legacy_credentials if isinstance(legacy_credentials, dict) else {}
        token = normalize_text(
            payload.get("local_api_bearer_token", legacy_credentials.get("local_api_bearer_token")),
            default="",
            limit=500,
        )
        explicit_auth_mode = payload.get("local_api_auth_mode")
        inferred_auth_mode = "bearer" if token else "none"
        return cls(
            local_api_host=normalize_host(payload.get("local_api_host", legacy_general.get("local_api_host")), default=DEFAULT_API_HOST),
            local_api_port=normalize_int(
                payload.get("local_api_port", legacy_general.get("local_api_port")),
                default=DEFAULT_API_PORT,
                minimum=1,
                maximum=65535,
            ),
            local_api_base_url_override=normalize_url(
                payload.get("local_api_base_url_override", legacy_general.get("local_api_base_url")),
                default="",
            ),
            local_api_auth_mode=normalize_choice(explicit_auth_mode, values=AUTH_MODE_VALUES, default=inferred_auth_mode),
            local_api_bearer_token=token,
        )


@dataclass(slots=True, frozen=True)
class McpSettings:
    mcp_enabled: bool = True
    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = DEFAULT_MCP_PORT
    mcp_path: str = DEFAULT_MCP_PATH
    public_https_base_url: str = ""
    tunnel_url: str = ""
    full_mcp_url_override: str = ""
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    mcp_auth_mode: str = "none"
    mcp_bearer_token: str = ""

    @property
    def local_mcp_url(self) -> str:
        return build_http_url(self.mcp_host, self.mcp_port, self.mcp_path)

    @property
    def derived_public_mcp_url(self) -> str:
        return build_endpoint_from_base(self.public_https_base_url, self.mcp_path)

    @property
    def derived_tunnel_mcp_url(self) -> str:
        return build_endpoint_from_base(self.tunnel_url, self.mcp_path)

    @property
    def effective_mcp_url(self) -> str:
        return self.full_mcp_url_override or self.derived_public_mcp_url or self.derived_tunnel_mcp_url or self.local_mcp_url

    @property
    def resolved_allowed_hosts(self) -> tuple[str, ...]:
        return tuple(
            derive_allowed_hosts(
                self.local_mcp_url,
                self.public_https_base_url,
                self.tunnel_url,
                self.full_mcp_url_override,
                extra_hosts=self.allowed_hosts,
            )
        )

    @property
    def resolved_allowed_origins(self) -> tuple[str, ...]:
        return tuple(
            derive_allowed_origins(
                self.local_mcp_url,
                self.public_https_base_url,
                self.tunnel_url,
                self.full_mcp_url_override,
                extra_origins=self.allowed_origins,
            )
        )

    def to_dict(self, *, redact_secrets: bool = False) -> dict:
        return {
            "mcp_enabled": self.mcp_enabled,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "mcp_path": self.mcp_path,
            "local_mcp_url": self.local_mcp_url,
            "public_https_base_url": self.public_https_base_url,
            "tunnel_url": self.tunnel_url,
            "full_mcp_url_override": self.full_mcp_url_override,
            "derived_public_mcp_url": self.derived_public_mcp_url,
            "derived_tunnel_mcp_url": self.derived_tunnel_mcp_url,
            "effective_mcp_url": self.effective_mcp_url,
            "allowed_hosts": list(self.allowed_hosts),
            "allowed_origins": list(self.allowed_origins),
            "resolved_allowed_hosts": list(self.resolved_allowed_hosts),
            "resolved_allowed_origins": list(self.resolved_allowed_origins),
            "mcp_auth_mode": self.mcp_auth_mode,
            "mcp_bearer_token": SECRET_REDACTION if redact_secrets and self.mcp_bearer_token else self.mcp_bearer_token,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict | None,
        *,
        legacy_mcp: dict | None = None,
        legacy_credentials: dict | None = None,
    ) -> "McpSettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_mcp = legacy_mcp if isinstance(legacy_mcp, dict) else {}
        legacy_credentials = legacy_credentials if isinstance(legacy_credentials, dict) else {}
        token = normalize_text(
            payload.get("mcp_bearer_token", legacy_credentials.get("mcp_bearer_token")),
            default="",
            limit=500,
        )
        explicit_auth_mode = payload.get("mcp_auth_mode")
        inferred_auth_mode = "bearer" if token else "none"
        return cls(
            mcp_enabled=normalize_bool(payload.get("mcp_enabled", legacy_mcp.get("enabled")), default=False),
            mcp_host=normalize_host(payload.get("mcp_host", legacy_mcp.get("host")), default=DEFAULT_MCP_HOST),
            mcp_port=normalize_int(
                payload.get("mcp_port", legacy_mcp.get("port")),
                default=DEFAULT_MCP_PORT,
                minimum=1,
                maximum=65535,
            ),
            mcp_path=normalize_path(payload.get("mcp_path", legacy_mcp.get("endpoint_path")), default=DEFAULT_MCP_PATH),
            public_https_base_url=normalize_url(
                payload.get("public_https_base_url", legacy_mcp.get("public_base_url", legacy_mcp.get("public_url"))),
                default="",
            ),
            tunnel_url=normalize_url(payload.get("tunnel_url"), default=""),
            full_mcp_url_override=normalize_url(payload.get("full_mcp_url_override", legacy_mcp.get("full_endpoint_url")), default=""),
            allowed_hosts=tuple(normalize_string_list(payload.get("allowed_hosts", legacy_mcp.get("allowed_hosts")))),
            allowed_origins=tuple(normalize_string_list(payload.get("allowed_origins", legacy_mcp.get("allowed_origins")))),
            mcp_auth_mode=normalize_choice(explicit_auth_mode, values=AUTH_MODE_VALUES, default=inferred_auth_mode),
            mcp_bearer_token=token,
        )


@dataclass(slots=True, frozen=True)
class OpenAISettings:
    provider: str = DEFAULT_OPENAI_PROVIDER
    model: str = DEFAULT_OPENAI_MODEL
    base_url: str = DEFAULT_OPENAI_BASE_URL
    organization_id: str = ""
    project_id: str = ""
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, payload: dict | None, *, legacy_openai: dict | None = None) -> "OpenAISettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_openai = legacy_openai if isinstance(legacy_openai, dict) else {}
        return cls(
            provider=normalize_text(payload.get("provider", legacy_openai.get("provider")), default=DEFAULT_OPENAI_PROVIDER, limit=64),
            model=normalize_text(payload.get("model", legacy_openai.get("model")), default=DEFAULT_OPENAI_MODEL, limit=128),
            base_url=normalize_url(payload.get("base_url", legacy_openai.get("base_url")), default=DEFAULT_OPENAI_BASE_URL),
            organization_id=normalize_text(payload.get("organization_id", legacy_openai.get("organization_id")), default="", limit=128),
            project_id=normalize_text(payload.get("project_id", legacy_openai.get("project_id")), default="", limit=128),
            timeout_seconds=normalize_int(
                payload.get("timeout_seconds", legacy_openai.get("timeout_seconds")),
                default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                minimum=1,
                maximum=600,
            ),
        )


@dataclass(slots=True, frozen=True)
class AuthSettings:
    auth_mode: str = "none"
    access_token: str = ""
    local_api_bearer_token: str = ""
    mcp_bearer_token: str = ""
    openai_api_key: str = ""

    def to_dict(self, *, redact_secrets: bool = False) -> dict:
        return {
            "auth_mode": self.auth_mode,
            "access_token": SECRET_REDACTION if redact_secrets and self.access_token else self.access_token,
            "local_api_bearer_token": SECRET_REDACTION if redact_secrets and self.local_api_bearer_token else self.local_api_bearer_token,
            "mcp_bearer_token": SECRET_REDACTION if redact_secrets and self.mcp_bearer_token else self.mcp_bearer_token,
            "openai_api_key": SECRET_REDACTION if redact_secrets and self.openai_api_key else self.openai_api_key,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict | None,
        *,
        legacy_credentials: dict | None = None,
        local_api: LocalApiSettings | None = None,
        mcp: McpSettings | None = None,
    ) -> "AuthSettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_credentials = legacy_credentials if isinstance(legacy_credentials, dict) else {}
        access_token = normalize_text(payload.get("access_token"), default="", limit=500)
        local_api_bearer_token = normalize_text(
            payload.get("local_api_bearer_token", legacy_credentials.get("local_api_bearer_token", getattr(local_api, "local_api_bearer_token", ""))),
            default="",
            limit=500,
        )
        mcp_bearer_token = normalize_text(
            payload.get("mcp_bearer_token", legacy_credentials.get("mcp_bearer_token", getattr(mcp, "mcp_bearer_token", ""))),
            default="",
            limit=500,
        )
        openai_api_key = normalize_text(payload.get("openai_api_key", legacy_credentials.get("openai_api_key")), default="", limit=500)
        explicit_auth_mode = payload.get("auth_mode", legacy_credentials.get("auth_mode"))
        inferred_auth_mode = "bearer" if any((access_token, local_api_bearer_token, mcp_bearer_token)) else "none"
        return cls(
            auth_mode=normalize_choice(explicit_auth_mode, values=AUTH_MODE_VALUES, default=inferred_auth_mode),
            access_token=access_token,
            local_api_bearer_token=local_api_bearer_token,
            mcp_bearer_token=mcp_bearer_token,
            openai_api_key=openai_api_key,
        )


@dataclass(slots=True, frozen=True)
class DiagnosticsSettings:
    local_api_status: str = "not_tested"
    local_api_message: str = ""
    mcp_status: str = "not_tested"
    mcp_message: str = ""
    external_status: str = "not_tested"
    external_message: str = ""
    openai_status: str = "not_tested"
    openai_message: str = ""
    overall_status: str = "not_tested"
    last_local_api_check: str = ""
    last_mcp_check: str = ""
    last_external_endpoint_check: str = ""
    last_openai_check: str = ""
    last_full_check: str = ""
    last_errors: list[str] = field(default_factory=list)
    last_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "local_api_status": self.local_api_status,
            "local_api_message": self.local_api_message,
            "mcp_status": self.mcp_status,
            "mcp_message": self.mcp_message,
            "external_status": self.external_status,
            "external_message": self.external_message,
            "openai_status": self.openai_status,
            "openai_message": self.openai_message,
            "overall_status": self.overall_status,
            "last_local_api_check": self.last_local_api_check,
            "last_mcp_check": self.last_mcp_check,
            "last_external_endpoint_check": self.last_external_endpoint_check,
            "last_openai_check": self.last_openai_check,
            "last_full_check": self.last_full_check,
            "last_errors": self.last_errors,
            "last_warnings": self.last_warnings,
        }

    @classmethod
    def from_dict(cls, payload: dict | None, *, legacy_diagnostics: dict | None = None) -> "DiagnosticsSettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_diagnostics = legacy_diagnostics if isinstance(legacy_diagnostics, dict) else {}
        return cls(
            local_api_status=normalize_status(payload.get("local_api_status", legacy_diagnostics.get("local_api_status")), default="not_tested"),
            local_api_message=normalize_text(payload.get("local_api_message", legacy_diagnostics.get("local_api_message")), default="", limit=500),
            mcp_status=normalize_status(payload.get("mcp_status", legacy_diagnostics.get("mcp_status")), default="not_tested"),
            mcp_message=normalize_text(payload.get("mcp_message", legacy_diagnostics.get("mcp_message")), default="", limit=500),
            external_status=normalize_status(payload.get("external_status"), default="not_tested"),
            external_message=normalize_text(payload.get("external_message"), default="", limit=500),
            openai_status=normalize_status(payload.get("openai_status", legacy_diagnostics.get("openai_status")), default="not_tested"),
            openai_message=normalize_text(payload.get("openai_message", legacy_diagnostics.get("openai_message")), default="", limit=500),
            overall_status=normalize_status(payload.get("overall_status", legacy_diagnostics.get("overall_status")), default="not_tested"),
            last_local_api_check=normalize_text(payload.get("last_local_api_check", legacy_diagnostics.get("last_tested_at")), default="", limit=64),
            last_mcp_check=normalize_text(payload.get("last_mcp_check", legacy_diagnostics.get("last_tested_at")), default="", limit=64),
            last_external_endpoint_check=normalize_text(payload.get("last_external_endpoint_check"), default="", limit=64),
            last_openai_check=normalize_text(payload.get("last_openai_check", legacy_diagnostics.get("last_tested_at")), default="", limit=64),
            last_full_check=normalize_text(payload.get("last_full_check", legacy_diagnostics.get("last_tested_at")), default="", limit=64),
            last_errors=normalize_messages(payload.get("last_errors")),
            last_warnings=normalize_messages(payload.get("last_warnings")),
        )


@dataclass(slots=True, frozen=True)
class IntegrationSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    general: GeneralSettings = GeneralSettings()
    local_api: LocalApiSettings = LocalApiSettings()
    mcp: McpSettings = McpSettings()
    openai: OpenAISettings = OpenAISettings()
    auth: AuthSettings = AuthSettings()
    diagnostics: DiagnosticsSettings = DiagnosticsSettings()

    def to_dict(self, *, redact_secrets: bool = False) -> dict:
        return {
            "schema_version": self.schema_version,
            "general": self.general.to_dict(),
            "local_api": self.local_api.to_dict(redact_secrets=redact_secrets),
            "mcp": self.mcp.to_dict(redact_secrets=redact_secrets),
            "openai": self.openai.to_dict(),
            "auth": self.auth.to_dict(redact_secrets=redact_secrets),
            "diagnostics": self.diagnostics.to_dict(),
        }

    @classmethod
    def defaults(cls) -> "IntegrationSettings":
        return cls()

    @classmethod
    def from_dict(cls, payload: dict | None) -> "IntegrationSettings":
        payload = payload if isinstance(payload, dict) else {}
        legacy_general = payload.get("general") if isinstance(payload.get("general"), dict) else {}
        legacy_credentials = payload.get("credentials") if isinstance(payload.get("credentials"), dict) else {}
        legacy_openai = payload.get("openai") if isinstance(payload.get("openai"), dict) else {}
        legacy_mcp = payload.get("mcp") if isinstance(payload.get("mcp"), dict) else {}
        legacy_diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}

        local_api = LocalApiSettings.from_dict(payload.get("local_api"), legacy_general=legacy_general, legacy_credentials=legacy_credentials)
        mcp = McpSettings.from_dict(payload.get("mcp"), legacy_mcp=legacy_mcp, legacy_credentials=legacy_credentials)
        auth = AuthSettings.from_dict(payload.get("auth"), legacy_credentials=legacy_credentials, local_api=local_api, mcp=mcp)

        return cls(
            schema_version=normalize_int(payload.get("schema_version"), default=SETTINGS_SCHEMA_VERSION, minimum=1),
            general=GeneralSettings.from_dict(payload.get("general"), legacy_general=legacy_general),
            local_api=local_api,
            mcp=mcp,
            openai=OpenAISettings.from_dict(payload.get("openai"), legacy_openai=legacy_openai),
            auth=auth,
            diagnostics=DiagnosticsSettings.from_dict(payload.get("diagnostics"), legacy_diagnostics=legacy_diagnostics),
        )
