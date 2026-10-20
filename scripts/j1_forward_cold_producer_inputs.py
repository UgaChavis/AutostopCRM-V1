"""Strict source inputs for future cold preparation; references are not permits."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_manager_transition_source as source

SCHEMA = "autostop.j1.cold-producer-inputs.v1"
FIELDS = {
    "schema",
    "scope",
    "binding",
    "predecessor_ref",
    "origin_ref",
    "manager",
    "runtime_artifact_ref",
    "runtime_artifact_provenance_ref",
    "runtime_env",
    "credentials",
    "kernel",
    "catalog_policy_ref",
    "review_key_ref",
    "fresh_role_material_ref",
    "telegram_frontier_policy_ref",
}
KERNEL_FIELDS = {"appserver_ref", "protocol_index_ref", "authorization_ref", "cache_source_ref"}
ORIGIN_FIELDS = recovery.ORIGIN_FIELDS - {"recovery_code_ref", "ci_ref"}
AUTH_SESSION_SCHEMA = "autostop.j1.cold-telegram-auth-material.v1"
MAX_AUTH_SESSION_BYTES = 128 * 1024
SESSION_TABLES = {
    "version": "version integer primary key",
    "sessions": (
        "dc_id integer primary key, server_address text, port integer, "
        "auth_key blob, takeout_id integer, tmp_auth_key blob"
    ),
    "entities": (
        "id integer primary key, hash integer not null, username text, "
        "phone integer, name text, date integer"
    ),
    "sent_files": (
        "md5_digest blob, file_size integer, type integer, id integer, "
        "hash integer, primary key(md5_digest, file_size, type)"
    ),
    "update_state": "id integer primary key, pts integer, qts integer, date integer, seq integer",
}


def inspect_auth_session(raw):
    """Inspect inert Telethon-v8 bytes; never open a caller's database path."""
    cold.need(
        type(raw) is bytes
        and 0 < len(raw) <= MAX_AUTH_SESSION_BYTES
        and raw[:16] == b"SQLite format 3\x00"
        and raw[18:20] == b"\x01\x01",
        "producer_auth_session_format",
    )
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(raw)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(lambda: 1, 100_000)
            expected = {
                ("table", name, name, f"create table {name} ({columns})")
                for name, columns in SESSION_TABLES.items()
            }
            expected.add(("index", "sqlite_autoindex_sent_files_1", "sent_files", None))
            actual = {
                (kind, name, table, " ".join(sql.lower().split()) if sql is not None else None)
                for kind, name, table, sql in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master"
                )
            }
            cold.need(actual == expected, "producer_auth_session_schema")
            cold.need(
                connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
                and connection.execute("PRAGMA freelist_count").fetchone() == (0,)
                and connection.execute("SELECT version FROM version").fetchall() == [(8,)]
                and connection.execute("PRAGMA page_count").fetchone()[0]
                * connection.execute("PRAGMA page_size").fetchone()[0]
                == len(raw),
                "producer_auth_session_integrity",
            )
            for table in ("entities", "sent_files", "update_state"):
                cold.need(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,),
                    "producer_auth_session_old_state",
                )
            rows = connection.execute(
                "SELECT dc_id,server_address,port,auth_key,takeout_id,tmp_auth_key FROM sessions"
            ).fetchall()
            cold.need(len(rows) == 1, "producer_auth_session_account_count")
            dc_id, address, port, key, takeout, temporary = rows[0]
            cold.need(
                type(dc_id) is int
                and 1 <= dc_id <= 5
                and type(address) is str
                and ipaddress.ip_address(address).is_global
                and port == 443
                and type(key) is bytes
                and len(key) == 256
                and key != bytes(256)
                and takeout is None
                and temporary == b"",
                "producer_auth_session_values",
            )
            return {"schema_version": 8, "history_copied": False, "rows": 1}
        finally:
            connection.close()
    except (sqlite3.Error, ValueError, TypeError, IndexError) as error:
        raise io.Refusal("cold_producer_auth_session_invalid") from error


def auth_session_bytes(raw, *, binding, role, expected_account_sha256, now_ns=None):
    """Build a NEW empty session from separately staged auth-only material.

    This accepts no old session/database, entity, update cursor or message. The
    root-protected input is a release-window authorization premise; only the
    actual guarded daemon can later prove provider authorization and identity.
    """
    current = time.monotonic_ns() if now_ns is None else now_ns
    cold._binding(binding, now_ns=current)
    cold.need(type(raw) is bytes and len(raw) <= 8192, "producer_auth_material_size")
    document = io.document(raw)
    cold.fields(
        document,
        {
            "schema",
            "scope",
            "binding",
            "role",
            "account_self_sha256",
            "authorization_id",
            "created_ns",
            "expires_ns",
            "dc_id",
            "server_address",
            "port",
            "auth_key_b64",
        },
        "producer_auth_material_fields",
    )
    cold.need(
        document["schema"] == AUTH_SESSION_SCHEMA
        and document["scope"] == "production_native"
        and role in {"work-telegram", "personal-telegram"}
        and document["role"] == role
        and document["binding"] == binding
        and type(expected_account_sha256) is str
        and cold.HEX.fullmatch(expected_account_sha256)
        and document["account_self_sha256"] == expected_account_sha256
        and cold._uuid(document["authorization_id"])
        and type(document["created_ns"]) is int
        and type(document["expires_ns"]) is int
        and binding["not_before_ns"]
        <= document["created_ns"]
        <= current
        < document["expires_ns"]
        <= binding["deadline_ns"],
        "producer_auth_material_binding",
    )
    try:
        cold.need(
            type(document["dc_id"]) is int
            and 1 <= document["dc_id"] <= 5
            and type(document["server_address"]) is str
            and type(document["port"]) is int
            and document["port"] == 443,
            "producer_auth_endpoint",
        )
        encoded = document["auth_key_b64"]
        cold.need(type(encoded) is str and len(encoded) == 344, "producer_auth_key_encoding")
        key = base64.b64decode(encoded, validate=True)
        cold.need(base64.b64encode(key).decode("ascii") == encoded, "producer_auth_key_encoding")
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("PRAGMA page_size=4096")
            for name, columns in SESSION_TABLES.items():
                connection.execute(f"CREATE TABLE {name} ({columns})")
            connection.execute("INSERT INTO version VALUES (8)")
            connection.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,NULL,?)",
                (document["dc_id"], document["server_address"], document["port"], key, b""),
            )
            connection.commit()
            result = connection.serialize()
        finally:
            connection.close()
    except (sqlite3.Error, ValueError, TypeError, binascii.Error) as error:
        raise io.Refusal("cold_producer_auth_material_invalid") from error
    inspect_auth_session(result)
    return result


def read(reference, provider):
    cold.reference(reference)
    raw = provider.read_reference(reference)
    cold.need(type(raw) is bytes and io.sha(raw) == reference["sha256"], "producer_input_hash")
    return io.document(raw)


def validate(document, request):
    cold.fields(document, FIELDS, "producer_input_fields")
    cold.need(
        document["schema"] == SCHEMA and document["scope"] == "production_native",
        "producer_input_scope",
    )
    for key in FIELDS - {
        "schema",
        "scope",
        "binding",
        "manager",
        "runtime_env",
        "credentials",
        "kernel",
    }:
        cold.reference(document[key])
    cold._binding(document["binding"], now_ns=time.monotonic_ns())
    declared = document["binding"]
    cold.need(
        all(
            declared[key] == request[value]
            for key, value in (
                ("attempt_id", "attempt_id"),
                ("boot_id", "boot_id"),
                ("generation", "owner_generation"),
                ("candidate_manager_sha", "candidate_manager_sha"),
            )
        )
        and declared["candidate_crm_sha"] == request["candidate"]["revision"],
        "producer_declared_binding",
    )
    cold.fields(document["kernel"], KERNEL_FIELDS, "producer_kernel_inputs")
    for reference in document["kernel"].values():
        cold.reference(reference)
    manager = document["manager"]
    cold.fields(manager, {"checkout", "revision", "tree", "ci_ref"}, "producer_manager_source")
    cold.need(
        type(manager["checkout"]) is str
        and Path(manager["checkout"]).is_absolute()
        and ".." not in Path(manager["checkout"]).parts
        and manager["revision"] == request["candidate_manager_sha"]
        and type(manager["tree"]) is str
        and cold.SHA1.fullmatch(manager["tree"]),
        "producer_manager_identity",
    )
    cold.reference(manager["ci_ref"])
    for key in ("runtime_env", "credentials"):
        cold.fields(document[key], epochs.CONSUMERS, "producer_all_seven_" + key)
    for reference in document["runtime_env"].values():
        cold.reference(reference)
    for credentials in document["credentials"].values():
        cold.fields(credentials, {"uid", "gid", "groups"}, "producer_credentials")
        cold.need(
            all(type(credentials[key]) is int and credentials[key] >= 0 for key in ("uid", "gid"))
            and type(credentials["groups"]) is list
            and credentials["groups"] == sorted(set(credentials["groups"]))
            and all(type(group) is int and group >= 0 for group in credentials["groups"]),
            "producer_credentials_values",
        )
    # The qualified Manager wake service runs as root and connects to the
    # root-owned kernel listener. Its separately selected group is retained.
    cold.need(document["credentials"]["wake"]["uid"] == 0, "producer_wake_root_required")
    return document


def binding(request, deployment_identity, *, boot_id, consumer_sha256, now=None, now_ns=None):
    from scripts import j1_forward_state as state

    current = datetime.now(UTC) if now is None else now
    monotonic = time.monotonic_ns() if now_ns is None else now_ns
    left = (state._timestamp(request["deadline_utc"]) - current).total_seconds()
    cold.need(0 < left <= 600 and request["boot_id"] == boot_id, "producer_request_deadline_boot")
    # The original E deadline is never extended by monotonic conversion.
    document = {
        "attempt_id": request["attempt_id"],
        "boot_id": boot_id,
        "generation": request["owner_generation"],
        "consumer_sha256": consumer_sha256,
        "candidate_crm_sha": request["candidate"]["revision"],
        "candidate_manager_sha": request["candidate_manager_sha"],
        "deployment_lock_identity": deployment_identity,
        "not_before_ns": monotonic,
        "deadline_ns": monotonic + int(left * 1e9),
    }
    return document


def manager_source(document, *, verify_github):
    """Read actual Git objects and working bytes; no fetch, checkout or app import."""
    manager = document["manager"]
    index, contents = source.collect_source(
        manager["checkout"], manager["revision"], manager["tree"]
    )
    cached = io.document(source.reference_bytes(manager["ci_ref"]))
    source.cached_ci(cached, "manager", manager["revision"])
    current = verify_github(cached["run"]["id"])
    verified = source.cached_ci(current, "manager", manager["revision"])
    cold.need(
        current["run"]["head_branch"] == "AutostopManager"
        and current["run"]["event"] == "push"
        and current["run"]["run_attempt"] == cached["run"]["run_attempt"],
        "producer_manager_production_push_ci",
    )
    # Preserve exact byte-clean source and explicit metadata. Git's OIDs in
    # index were recomputed by collect_source; CI is independently reread above.
    cold.need(
        {"scripts/cold-role-start.py", "autostop_manager/cold_runtime.py"} <= set(contents),
        "producer_manager_cold_source_required",
    )
    files = dict(contents)
    files["REVISION"] = (manager["revision"] + "\n").encode()
    names = sorted(files)
    manifest = "".join(f"{io.sha(files[name])}  ./{name}\n" for name in names).encode()
    files["MANIFEST.sha256"] = manifest
    files["MANIFEST.files0"] = b"".join(("./" + name).encode() + b"\0" for name in names)
    return (
        index,
        files,
        {**verified, "evidence_kind": "fresh_fixed_github_read", "fresh_github_verified": True},
    )


def environment(raw):
    cold.need(
        type(raw) is bytes and len(raw) <= 1024**2 and b"\0" not in raw, "producer_environment_size"
    )
    values = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        cold.need(
            separator
            and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key)
            and key not in values
            and not any(char in value for char in "\r\n\0"),
            "producer_environment_encoding",
        )
        cold.need(
            key not in {"LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "CODEX_HOME", "HOME"},
            "producer_environment_unsafe",
        )
        cold.need(
            not key.startswith("LD_") or key == "LD_LIBRARY_PATH",
            "producer_loader_environment_unsafe",
        )
        values[key] = value
    return values
