"""Immutable address generations reject buffered requests before application work.

The address is a cut between generations, never an authentication credential.
Normal operator, bearer and OAuth checks still apply after the cut.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

SCHEMA = "autostop.j1.cold-http-ingress.v1"
STATUS_SCHEMA = "autostop.j1.cold-http-ingress-status.v1"
CONFIG_ENV = "AUTOSTOP_COLD_HTTP_INGRESS_CONFIG"
HASH_ENV = CONFIG_ENV + "_SHA256"
PUBLIC_ORIGIN = "https://crm.autostopcrm.ru"
STATUS_PATH = "/_cold/ingress-status"
_FIELDS = {
    "schema",
    "scope",
    "attempt_id",
    "boot_id",
    "generation",
    "epoch_id",
    "candidate_crm_sha",
    "candidate_manager_sha",
    "public_origin",
    "board_prefix",
    "mcp_prefix",
}


class ColdHttpIngressUnavailableError(RuntimeError):
    """An incomplete or changed cold HTTP cut cannot fall back to legacy URLs."""


def _require(value: object) -> None:
    if not value:
        raise ColdHttpIngressUnavailableError("Cold HTTP ingress is unavailable.")


def _canonical(document: dict) -> bytes:
    return (
        json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        + "\n"
    ).encode()


def expected_prefixes(document: dict) -> dict:
    """Bind address generations independently of a producer's nonce choices."""
    context = {
        key: value for key, value in document.items() if key not in {"board_prefix", "mcp_prefix"}
    }
    return {
        transport + "_prefix": ("/__cold/" if transport == "board" else "/mcp/__cold/")
        + hashlib.sha256(_canonical({"http_ingress": context, "transport": transport})).hexdigest()
        for transport in ("board", "mcp")
    }


@dataclass(frozen=True)
class ColdHttpIngress:
    document: dict
    sha256: str

    def prefix(self, transport: str) -> str:
        _require(transport in {"board", "mcp"})
        return self.document[transport + "_prefix"]

    def base_url(self, transport: str) -> str:
        return self.document["public_origin"] + self.prefix(transport)

    def mcp_urls(self) -> tuple[str, str, str]:
        return self.base_url("mcp"), self.base_url("mcp") + "/mcp", "/mcp"

    def strip_path(self, raw_path: str, transport: str) -> str | None:
        # Compare the actual request target before any URL decoding. No proxy
        # header, Referer or redirect can upgrade a request from an older epoch.
        if not isinstance(raw_path, str) or not raw_path.startswith("/"):
            return None
        try:
            parsed = urlsplit(raw_path)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in raw_path:
            return None
        prefix = self.prefix(transport)
        if parsed.path != prefix and not parsed.path.startswith(prefix + "/"):
            return None
        suffix = parsed.path[len(prefix) :] or "/"
        # Routing aliases and dot segments may be normalized differently by
        # upstream servers. They cannot enter the new application namespace.
        if (
            "//" in suffix
            or re.search(r"%(?:2e|2f|5c|25)", suffix, re.I)
            or any(part in {".", ".."} for part in suffix.split("/"))
        ):
            return None
        return suffix + ("?" + parsed.query if parsed.query else "")

    def status(self, transport: str) -> dict:
        return {
            "schema": STATUS_SCHEMA,
            "config_sha256": self.sha256,
            "transport": transport,
            "candidate_crm_sha": self.document["candidate_crm_sha"],
            "epoch_id": self.document["epoch_id"],
        }


def parse_document(
    document: object, binding: dict, *, allow_fixture: bool = False
) -> ColdHttpIngress:
    try:
        _require(type(document) is dict and set(document) == _FIELDS)
        _require(document["schema"] == SCHEMA)
        _require(
            document["scope"]
            in ({"production_native", "native_fixture"} if allow_fixture else {"production_native"})
        )
        for name in ("attempt_id", "boot_id", "generation", "epoch_id"):
            _require(
                type(document[name]) is str and str(uuid.UUID(document[name])) == document[name]
            )
            _require(document[name] == binding[name])
        _require(document["generation"] == document["epoch_id"])
        _require(binding["role"] == "crm-embedded")
        for name in ("candidate_crm_sha", "candidate_manager_sha"):
            _require(type(document[name]) is str and re.fullmatch(r"[0-9a-f]{40}", document[name]))
        _require(document["candidate_manager_sha"] == binding["source_revision"])
        origin = document["public_origin"]
        _require(type(origin) is str)
        parsed = urlsplit(origin)
        _require(
            origin == PUBLIC_ORIGIN
            or (
                allow_fixture
                and document["scope"] == "native_fixture"
                and parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "::1", "testserver"}
                and parsed.netloc
                and not parsed.username
                and not parsed.password
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
            )
        )
        board, mcp = document["board_prefix"], document["mcp_prefix"]
        _require(type(board) is str and re.fullmatch(r"/__cold/[0-9a-f]{64}", board))
        _require(type(mcp) is str and re.fullmatch(r"/mcp/__cold/[0-9a-f]{64}", mcp))
        _require(board.rsplit("/", 1)[1] != mcp.rsplit("/", 1)[1])
        _require(expected_prefixes(document) == {"board_prefix": board, "mcp_prefix": mcp})
        value = json.loads(_canonical(document))
        return ColdHttpIngress(value, hashlib.sha256(_canonical(value)).hexdigest())
    except ColdHttpIngressUnavailableError:
        raise
    except Exception:
        raise ColdHttpIngressUnavailableError("Cold HTTP ingress is unavailable.") from None
