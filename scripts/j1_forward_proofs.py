"""Verify off-host Ed25519 evidence without hosting a signing credential.

This verifies the envelope's provenance and attempt binding only. The caller
must validate each kind's payload and independently decide acceptance.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import j1_forward_io as io
from scripts.j1_forward_state import validate_plan

SCHEMA = "autostop.j1.crm-forward-signed-proof.v1"
KINDS = frozenset({"offhost_restore", "native_acceptance", "runtime_acceptance"})
FIELDS = frozenset(
    {
        "schema",
        "kind",
        "attempt_id",
        "plan_sha256",
        "observed_at",
        "key_sha256",
        "payload",
        "signature_base64",
    }
)
REVIEW_KEY_PATH = "/etc/autostop-crm-forward/reviewer.pem"
OPENSSL = "/usr/bin/openssl"
VERIFY_TIMEOUT = 10.0
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _utc(value, code):
    io.require(type(value) is str and len(value) <= 40, code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise io.Refusal(code) from None
    io.require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), code)
    return parsed


def _signature(value):
    io.require(type(value) is str and len(value) == 88, "proof_signature_encoding")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise io.Refusal("proof_signature_encoding") from None
    io.require(
        len(decoded) == 64 and base64.b64encode(decoded).decode("ascii") == value,
        "proof_signature_encoding",
    )
    return decoded


def _ed25519_public_key(raw):
    io.require(len(raw) <= 4096, "proof_public_key_invalid")
    match = re.fullmatch(
        rb"-----BEGIN PUBLIC KEY-----\r?\n([A-Za-z0-9+/=\r\n]+)"
        rb"-----END PUBLIC KEY-----\r?\n?",
        raw,
    )
    io.require(match is not None, "proof_public_key_invalid")
    try:
        der = base64.b64decode(match[1].replace(b"\r", b"").replace(b"\n", b""), validate=True)
    except (ValueError, binascii.Error):
        raise io.Refusal("proof_public_key_invalid") from None
    io.require(
        len(der) == len(ED25519_SPKI_PREFIX) + 32 and der.startswith(ED25519_SPKI_PREFIX),
        "proof_public_key_not_ed25519",
    )


def _verify_signature(body, signature, parent, *, timeout):
    """Expose only private, exclusive temporary inputs to the fixed verifier."""
    io.protected_directory(parent)
    temporary = Path(tempfile.mkdtemp(prefix=".j1-proof-", dir=parent))
    owned_files = []
    try:
        io.protected_directory(temporary, private=True)
        for name, raw in (("body.json", body), ("signature.bin", signature)):
            path = temporary / name
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            owned_files.append(path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
        try:
            result = subprocess.run(
                [
                    OPENSSL,
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    REVIEW_KEY_PATH,
                    "-rawin",
                    "-in",
                    str(temporary / "body.json"),
                    "-sigfile",
                    str(temporary / "signature.bin"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"LANG": "C", "LC_ALL": "C", "OPENSSL_CONF": os.devnull},
                shell=False,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise io.Refusal("proof_verifier_timeout") from None
        except OSError:
            raise io.Refusal("proof_verifier_unavailable") from None
        io.require(result.returncode == 0, "proof_signature_invalid")
    finally:
        for path in owned_files:
            path.unlink()
        temporary.rmdir()


def verify_proof(
    ref,
    kind: str,
    plan_ref: dict,
    review_key_ref: dict,
    *,
    now: datetime | None = None,
    plan_validator=None,
) -> dict:
    """Return the authenticated payload, never a payload-derived acceptance flag."""
    started = time.monotonic()
    current = datetime.now(UTC) if now is None else now
    io.require(
        isinstance(current, datetime)
        and current.tzinfo is not None
        and current.utcoffset() == timedelta(0),
        "proof_now_utc_required",
    )
    io.require(type(kind) is str and kind in KINDS, "proof_kind_invalid")
    io.require(
        type(review_key_ref) is dict
        and set(review_key_ref) == {"path", "sha256"}
        and review_key_ref["path"] == REVIEW_KEY_PATH,
        "proof_key_path_invalid",
    )
    plan = io.document(io.referenced(plan_ref))
    io.require(
        type(plan) is dict
        and type(plan.get("base_sha256")) is str
        and HEX64.fullmatch(plan["base_sha256"]),
        "proof_plan_invalid",
    )
    (validate_plan if plan_validator is None else plan_validator)(plan, plan["base_sha256"])
    created = _utc(plan["created_at"], "proof_plan_time_invalid")
    deadline = _utc(plan["deadline_utc"], "proof_plan_time_invalid")
    io.require(current <= deadline, "proof_deadline_expired")
    envelope = io.document(io.referenced(ref))
    io.require(type(envelope) is dict and set(envelope) == FIELDS, "proof_fields")
    io.require(envelope["schema"] == SCHEMA, "proof_schema")
    io.require(
        envelope["kind"] == kind
        and envelope["attempt_id"] == plan["attempt_id"]
        and envelope["plan_sha256"] == plan_ref["sha256"]
        and envelope["key_sha256"] == review_key_ref["sha256"],
        "proof_binding_mismatch",
    )
    observed = _utc(envelope["observed_at"], "proof_observation_time_invalid")
    io.require(created <= observed <= deadline, "proof_observation_outside_plan")
    io.require(observed - current <= timedelta(seconds=5), "proof_observation_in_future")
    io.require(type(envelope["payload"]) is dict, "proof_payload_invalid")
    signature = _signature(envelope["signature_base64"])
    key = io.referenced(review_key_ref)
    _ed25519_public_key(key)
    try:
        body = io.canonical(
            {key: value for key, value in envelope.items() if key != "signature_base64"}
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise io.Refusal("proof_canonical_invalid") from None
    remaining = (deadline - current).total_seconds() - (time.monotonic() - started)
    io.require(remaining > 0, "proof_deadline_expired")
    _verify_signature(
        body, signature, Path(ref["path"]).parent, timeout=min(VERIFY_TIMEOUT, remaining)
    )
    # Reject key replacement while OpenSSL was reading its fixed path. No
    # subprocess output, key bytes, or payload content is included in errors.
    io.referenced(review_key_ref)
    finished = current + timedelta(seconds=time.monotonic() - started)
    if now is None:
        finished = max(finished, datetime.now(UTC))
    io.require(finished <= deadline, "proof_deadline_expired")
    return envelope["payload"]
