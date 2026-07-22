from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from math import log2
from pathlib import Path
from urllib.parse import urlsplit

from .config import get_app_data_dir

PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
DEFAULT_SERVICE_IDENTITY = "codex-owner-agent"
DEFAULT_MAINTENANCE_MARKER_NAME = ".agent-gateway-maintenance"
EXPECTED_STORE_API_URL = "http://autostop-app:8000"
STORE_TOKEN_ENV_NAMES = (
    "AUTOSTOP_STORE_READ_TOKEN",
    "AUTOSTOP_STORE_QUOTE_TOKEN",
    "AUTOSTOP_STORE_MANAGE_TOKEN",
    "AUTOSTOP_STORE_OWNER_TOKEN",
)

GATEWAY_SWITCH_ENV_NAMES = (
    "AUTOSTOP_AGENT_GATEWAY_ENABLED",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED",
)

_SERVICE_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_SAFE_BEARER_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")
_RELEASE_SMOKE_REVISION_RE = re.compile(r"[0-9a-f]{40,64}")
_PLACEHOLDER_PARTS = (
    "change-me",
    "changeme",
    "example",
    "placeholder",
    "replace-me",
    "secret-here",
    "test-token",
)


class DeploymentSecurityError(RuntimeError):
    """Raised when production security settings are incomplete or unsafe."""


def bearer_token_entropy_bits(token: str) -> float:
    """Return a conservative Shannon estimate for a supplied bearer token."""

    if not token:
        return 0.0
    length = len(token)
    counts = Counter(token)
    return sum(count * log2(length / count) for count in counts.values())


def bearer_token_is_strong(token: str) -> bool:
    """Reject syntactically valid but obviously low-entropy production tokens."""

    return bool(
        _SAFE_BEARER_TOKEN_RE.fullmatch(token)
        and len(set(token)) >= 20
        and bearer_token_entropy_bits(token) >= 200.0
    )


def release_smoke_proof(token: str, revision: str) -> str:
    """Build the bounded maintenance-smoke proof without exposing its token."""

    normalized_revision = str(revision or "").strip().casefold()
    return hmac.new(
        str(token or "").encode("utf-8"),
        f"autostop-gateway-v2-release-smoke:v1:{normalized_revision}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def release_smoke_proof_matches(token: str, revision: str, proof: str) -> bool:
    """Validate one release-only proof while failing closed on malformed input."""

    normalized_revision = str(revision or "").strip().casefold()
    if not token or _RELEASE_SMOKE_REVISION_RE.fullmatch(normalized_revision) is None:
        return False
    return hmac.compare_digest(release_smoke_proof(token, normalized_revision), str(proof or ""))


def oauth_state_key_is_valid(value: str) -> bool:
    """Validate a 32-byte URL-safe base64 state-encryption key without exposing it."""

    try:
        decoded = base64.urlsafe_b64decode(str(value or "").encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return False
    return len(decoded) == 32 and len(str(value or "")) == 44


@dataclass(frozen=True)
class AgentGatewaySecurityPolicy:
    deployment_environment: str
    service_identity: str
    gateway_enabled: bool
    writes_enabled: bool
    finance_enabled: bool
    mail_enabled: bool
    destructive_enabled: bool
    raw_enabled: bool

    @property
    def production(self) -> bool:
        return self.deployment_environment in PRODUCTION_ENVIRONMENTS

    def public_dict(self) -> dict[str, object]:
        """Return the non-secret policy fields safe for bootstrap/status responses."""

        return {
            "deployment_environment": self.deployment_environment,
            "service_identity": self.service_identity,
            "gateway_enabled": self.gateway_enabled,
            "writes_enabled": self.writes_enabled,
            "finance_enabled": self.finance_enabled,
            "mail_enabled": self.mail_enabled,
            "destructive_enabled": self.destructive_enabled,
            "raw_enabled": self.raw_enabled,
            "maintenance_mode": is_maintenance_mode(),
        }


def _clean_environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _normalized_environment(environ: Mapping[str, str]) -> str:
    return (
        str(environ.get("AUTOSTOP_DEPLOYMENT_ENV", "development") or "development").strip().lower()
    )


def _parse_switch(
    environ: Mapping[str, str],
    name: str,
    *,
    default: bool,
    strict: bool,
) -> bool:
    raw_value = environ.get(name)
    if raw_value is None or not str(raw_value).strip():
        if strict:
            raise DeploymentSecurityError(f"{name} must be set explicitly to 0 or 1")
        return default
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise DeploymentSecurityError(f"{name} must be a boolean value (0 or 1)")


def load_agent_gateway_security_policy(
    environ: Mapping[str, str] | None = None,
) -> AgentGatewaySecurityPolicy:
    source = _clean_environment(environ)
    deployment_environment = _normalized_environment(source)
    production = deployment_environment in PRODUCTION_ENVIRONMENTS
    service_identity = str(
        source.get("AUTOSTOP_AGENT_SERVICE_IDENTITY", DEFAULT_SERVICE_IDENTITY)
        or DEFAULT_SERVICE_IDENTITY
    ).strip()
    values = {
        name: _parse_switch(source, name, default=False, strict=production)
        for name in GATEWAY_SWITCH_ENV_NAMES
    }
    return AgentGatewaySecurityPolicy(
        deployment_environment=deployment_environment,
        service_identity=service_identity,
        gateway_enabled=values["AUTOSTOP_AGENT_GATEWAY_ENABLED"],
        writes_enabled=values["AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED"],
        finance_enabled=values["AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED"],
        mail_enabled=values["AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED"],
        destructive_enabled=values["AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED"],
        raw_enabled=values["AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED"],
    )


def _validate_https_url(name: str, value: str, *, expected_path: str | None = None) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return f"{name} must be a valid HTTPS URL"
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return f"{name} must be an HTTPS URL without credentials, query, or fragment"
    if expected_path and parsed.path.rstrip("/") != expected_path.rstrip("/"):
        return f"{name} must use the {expected_path} path"
    return None


def validate_store_integration_environment(
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Validate the optional Store adapter release gate without making CRM startup depend on it."""

    source = _clean_environment(environ)
    errors: list[str] = []
    store_api_url = str(source.get("AUTOSTOP_STORE_API_URL", "") or "").strip().rstrip("/")
    if store_api_url != EXPECTED_STORE_API_URL:
        errors.append(
            f"AUTOSTOP_STORE_API_URL must be the internal service URL {EXPECTED_STORE_API_URL}"
        )
    store_tokens: dict[str, str] = {}
    for name in STORE_TOKEN_ENV_NAMES:
        store_token = str(source.get(name, "") or "").strip()
        store_tokens[name] = store_token
        if not store_token:
            errors.append(f"{name} is required for a Store-enabled release")
        elif not bearer_token_is_strong(store_token):
            errors.append(f"{name} must be a strong URL-safe service token")
    if all(store_tokens.values()) and len(set(store_tokens.values())) != len(store_tokens):
        errors.append(
            "Store read, quote, manage, and owner service tokens must be pairwise distinct"
        )
    return errors


def validate_production_environment(
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return production configuration errors without exposing secret values."""

    source = _clean_environment(environ)
    if _normalized_environment(source) not in PRODUCTION_ENVIRONMENTS:
        return []

    errors: list[str] = []
    embedded_oauth = (
        str(source.get("AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED", "") or "").strip().lower()
    )
    if embedded_oauth not in {"0", "false", "no", "off"}:
        errors.append("AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED must be explicitly 0 in production")
    production_oauth = str(source.get("AUTOSTOP_MCP_OAUTH_ENABLED", "") or "").strip().lower()
    if production_oauth not in {"1", "true", "yes", "on"}:
        errors.append("AUTOSTOP_MCP_OAUTH_ENABLED must be explicitly 1 in production")
    oauth_state_key = str(source.get("AUTOSTOP_MCP_OAUTH_STATE_KEY", "") or "").strip()
    if not oauth_state_key:
        errors.append("AUTOSTOP_MCP_OAUTH_STATE_KEY is required in production")
    elif not oauth_state_key_is_valid(oauth_state_key):
        errors.append("AUTOSTOP_MCP_OAUTH_STATE_KEY must be a valid 32-byte Fernet key")
    token = str(source.get("MINIMAL_KANBAN_MCP_BEARER_TOKEN", "") or "").strip()
    token_lower = token.lower()
    if not token:
        errors.append("MINIMAL_KANBAN_MCP_BEARER_TOKEN is required in production")
    elif not _SAFE_BEARER_TOKEN_RE.fullmatch(token):
        errors.append("MINIMAL_KANBAN_MCP_BEARER_TOKEN must contain 32-512 URL-safe characters")
    elif not bearer_token_is_strong(token):
        errors.append(
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN must have at least 200 bits of estimated entropy"
        )
    elif any(part in token_lower for part in _PLACEHOLDER_PARTS):
        errors.append("MINIMAL_KANBAN_MCP_BEARER_TOKEN must not be a placeholder")

    service_identity = str(source.get("AUTOSTOP_AGENT_SERVICE_IDENTITY", "") or "").strip()
    if not _SERVICE_IDENTITY_RE.fullmatch(service_identity):
        errors.append(
            "AUTOSTOP_AGENT_SERVICE_IDENTITY must be a stable lowercase service identifier"
        )

    public_base_url = str(source.get("MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL", "") or "").strip()
    public_endpoint_url = str(
        source.get("MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL", "") or ""
    ).strip()
    if not public_base_url:
        errors.append("MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL is required in production")
    else:
        error = _validate_https_url("MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL", public_base_url)
        if error:
            errors.append(error)
    if not public_endpoint_url:
        errors.append("MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL is required in production")
    else:
        error = _validate_https_url(
            "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL", public_endpoint_url, expected_path="/mcp"
        )
        if error:
            errors.append(error)
    if public_base_url and public_endpoint_url:
        try:
            base_parts = urlsplit(public_base_url)
            endpoint_parts = urlsplit(public_endpoint_url)
            base_authority = (base_parts.hostname, base_parts.port)
            endpoint_authority = (endpoint_parts.hostname, endpoint_parts.port)
            if base_authority != endpoint_authority:
                errors.append(
                    "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL must use the public base authority"
                )
        except ValueError:
            pass

    for name in GATEWAY_SWITCH_ENV_NAMES:
        try:
            _parse_switch(source, name, default=False, strict=True)
        except DeploymentSecurityError as exc:
            errors.append(str(exc))

    marker = str(source.get("AUTOSTOP_MAINTENANCE_MARKER", "") or "").strip()
    if not marker:
        errors.append("AUTOSTOP_MAINTENANCE_MARKER is required in production")
    elif not Path(marker).is_absolute():
        errors.append("AUTOSTOP_MAINTENANCE_MARKER must be an absolute path in production")
    return errors


def assert_production_environment(
    environ: Mapping[str, str] | None = None,
    *,
    require_production: bool = False,
) -> None:
    source = _clean_environment(environ)
    if require_production and _normalized_environment(source) not in PRODUCTION_ENVIRONMENTS:
        raise DeploymentSecurityError(
            "AUTOSTOP_DEPLOYMENT_ENV must be production for the production container entrypoint"
        )
    errors = validate_production_environment(source)
    if errors:
        raise DeploymentSecurityError("; ".join(errors))


def get_maintenance_marker_path(environ: Mapping[str, str] | None = None) -> Path:
    source = _clean_environment(environ)
    configured = str(source.get("AUTOSTOP_MAINTENANCE_MARKER", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return get_app_data_dir() / DEFAULT_MAINTENANCE_MARKER_NAME


def is_maintenance_mode(environ: Mapping[str, str] | None = None) -> bool:
    try:
        return get_maintenance_marker_path(environ).is_file()
    except OSError:
        return True
