"""Bounded read-only legacy catalogs; no status is interpreted as no effect.

The source policy names every configured durable catalog in the six profiles.
Its native caller checks those paths against the original effective unit and
accepted source configuration. RAM and unparsed kernel input are not invented
as request IDs: their separate retirement witness forbids replay.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io

MAX_ROWS = 100000
MAX_BYTES = 128 * 1024**2
DECODERS = {
    "manager_sqlite": (
        ("manager_runs", ("request_id", "idempotency_key", "checkpoint_json")),
        ("manager_run_external_steps", ("step_id", "request_refs_json")),
    ),
    "scheduler_sqlite": (
        ("manager_automation_commands", ("command_id", "idempotency_key", "request_hash")),
        ("manager_automation_runs", ("run_id", "command_id")),
        ("manager_automation_outbox", ("outbox_id", "run_id", "idempotency_key", "payload_json")),
    ),
    "telegram_sqlite": (
        ("jobs", ("job_id", "thread_id", "turn_id")),
        ("events", ("sequence", "job_id")),
        (
            "effects",
            ("effect_key", "job_id", "receipt_key", "text_sha256", "attempted", "verified"),
        ),
    ),
    "j1_sqlite": (("jobs", ("id",)),),
}
REQUIRED_KINDS = {
    "autostop-manager-mcp.service": {"manager_sqlite"},
    "autostop-manager-scheduler.service": {"scheduler_sqlite"},
    "autostop-j1.service": {"j1_sqlite"},
    "autostop-telegram.service": {"telegram_sqlite", "idempotency_json"},
    "autostop-work-telegram.service": {"telegram_sqlite", "idempotency_json"},
    # Wake has no independent complete durable queue. It consumes the work
    # inbox, while its volatile pending/FIFO is covered by actual retirement.
    "autostop-codex-wake.service": {"telegram_sqlite"},
}
IDENTITIES = frozenset(
    {
        "request_id",
        "idempotency_key",
        "operation_id",
        "op_id",
        "step_id",
        "command_id",
        "run_id",
        "outbox_id",
        "job_id",
        "thread_id",
        "turn_id",
        "sequence",
        "effect_key",
        "receipt_key",
    }
)


def operation_id(value):
    cold.need(
        type(value) is str
        and 1 <= len(value) <= 200
        and len(value.encode("utf-8")) <= 800
        and not any(ord(char) < 32 or ord(char) == 127 for char in value),
        "catalog_operation_identity",
    )
    return value


def _version(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
    ]


def _reference(provider, reference):
    cold.reference(reference)
    raw = provider.read_reference(reference)
    cold.need(type(raw) is bytes and io.sha(raw) == reference["sha256"], "catalog_reference_hash")
    return io.document(raw)


def validate_policy(document, binding, *, scope):
    cold.fields(
        document,
        {"schema", "scope", "binding", "predecessor_ref", "profiles"},
        "catalog_policy_fields",
    )
    cold.need(
        document["schema"] == "autostop.j1.cold-legacy-catalog-policy.v1"
        and document["scope"] == scope
        and document["binding"] == binding,
        "catalog_policy_binding",
    )
    cold.reference(document["predecessor_ref"])
    cold.fields(document["profiles"], cold.ALLOWED, "catalog_six_profiles")
    for unit, sources in document["profiles"].items():
        cold.need(type(sources) is list and 1 <= len(sources) <= 8, "catalog_source_count")
        cold.need(
            {source.get("kind") for source in sources} == REQUIRED_KINDS[unit],
            "catalog_required_source_families",
        )
        for source in sources:
            cold.fields(source, {"kind", "path", "path_binding_ref"}, "catalog_source_fields")
            path = source["path"]
            cold.need(
                type(path) is str
                and Path(path).is_absolute()
                and ".." not in Path(path).parts
                and str(Path(path)) == path,
                "catalog_source_path",
            )
            cold.reference(source["path_binding_ref"])
    return document


def _ids(value, *, field=None, depth=0):
    cold.need(depth <= 16, "catalog_record_depth")
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in IDENTITIES and item not in (None, ""):
                if isinstance(item, str):
                    result.add(operation_id(item))
            if key.endswith("_json") and type(item) is str:
                item = io.document(item.encode())
            if isinstance(item, (dict, list)):
                result.update(_ids(item, field=key, depth=depth + 1))
    elif isinstance(value, list):
        cold.need(len(value) <= MAX_ROWS, "catalog_record_items")
        for item in value:
            result.update(_ids(item, depth=depth + 1))
    elif field == "id" and type(value) is str:
        result.add(operation_id(value))
    return result


def _sqlite(path, kind):
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            Path(str(path) + suffix).lstat()
        except FileNotFoundError:
            continue
        cold.need(False, "catalog_requires_quiet_checkpoint")
    connection = sqlite3.connect(
        "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1", uri=True, timeout=2
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        total, records = 0, []
        for table, columns in DECODERS[kind]:
            # The identifiers are constants above. No query or identifier is
            # taken from an input document, a database row or an environment.
            cursor = connection.execute("SELECT * FROM " + table + " LIMIT 100001")
            actual_columns = [column[0] for column in cursor.description]
            cold.need(
                set(columns) <= set(actual_columns)
                and len(actual_columns) == len(set(actual_columns))
                and all(
                    type(name) is str and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name)
                    for name in actual_columns
                ),
                "catalog_source_schema",
            )
            rows = cursor.fetchall()
            total += len(rows)
            cold.need(total <= MAX_ROWS, "catalog_row_budget")
            for row in rows:
                value = dict(zip(actual_columns, row, strict=True))
                ids = _ids(value)
                if kind == "j1_sqlite":
                    ids.add(operation_id(value["id"]))
                records.append((ids, {"table": table, "record_sha256": cold.digest(value)}))
        return records
    finally:
        connection.close()


def _source(source, *, read_file):
    path = Path(source["path"])
    parent = path.parent.lstat()
    cold.need(
        stat.S_ISDIR(parent.st_mode)
        and not parent.st_mode & 0o022
        and not path.parent.is_symlink(),
        "catalog_parent_custody",
    )
    try:
        before = path.lstat()
    except FileNotFoundError:
        # A missing file means no readable durable IDs in that source. It
        # never means that a request was never received, dispatched or applied.
        return [], {"path": str(path), "present": False, "parent_identity": _version(parent)}
    cold.need(
        stat.S_ISREG(before.st_mode)
        and not before.st_mode & 0o022
        and before.st_nlink == 1
        and before.st_size <= MAX_BYTES,
        "catalog_file_custody",
    )
    raw = read_file(path, MAX_BYTES)
    cold.need(len(raw) == before.st_size, "catalog_source_bytes")
    if source["kind"] in DECODERS:
        rows = _sqlite(path, source["kind"])
    else:
        value = io.document(raw)
        cold.need(type(value) is dict and len(value) <= MAX_ROWS, "catalog_idempotency_object")
        rows = [
            ({operation_id(key)}, {"record_sha256": cold.digest(body)})
            for key, body in sorted(value.items())
        ]
    cold.need(
        _version(path.lstat()) == _version(before) and read_file(path, MAX_BYTES) == raw,
        "catalog_source_changed",
    )
    return rows, {
        "path": str(path),
        "present": True,
        "identity": _version(before),
        "sha256": io.sha(raw),
    }


def read_catalog_bytes(path, limit):
    """Stable old-data read, preserving its actual owner in the checkpoint.

    Old application data need not be owned by root. The caller independently
    binds this exact path to the original unit and retires its writers; this
    reader checks stable physical bytes and never confers native permission.
    """
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        cold.need(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not before.st_mode & 0o022
            and before.st_size <= limit,
            "catalog_read_custody",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        cold.need(
            len(raw) == before.st_size <= limit
            and _version(os.fstat(descriptor)) == _version(before) == _version(path.lstat()),
            "catalog_read_changed",
        )
        return raw
    finally:
        os.close(descriptor)


def measure(policy, binding, *, provider, verify_path_binding, read_file=read_catalog_bytes):
    """All known sources, all rows, stable before/after; outcome stays unknown."""
    validate_policy(policy, binding, scope=provider.scope)
    observations, identities = [], {}
    for unit, sources in sorted(policy["profiles"].items()):
        for source in sources:
            # Native validation must prove that this source belongs to the
            # original actual unit/configuration, not an arbitrary empty file.
            verify_path_binding(unit, source, policy)
            records, observed = _source(source, read_file=read_file)
            observation = {"unit": unit, "kind": source["kind"], **observed}
            observations.append(observation)
            source_hash = cold.digest(observation)
            for ids, fingerprint in records:
                for op_id in ids:
                    identities.setdefault(op_id, set()).add(
                        cold.digest({"source": source_hash, **fingerprint})
                    )
    rows = [
        {
            "op_id": op_id,
            "fingerprint": {
                "kind": "observed_record_sha256",
                "sha256": cold.digest(sorted(values)),
            },
            "outcome": "unknown",
            "retry": "forbidden",
        }
        for op_id, values in sorted(identities.items())
    ]
    cold.need(len(rows) <= MAX_ROWS, "catalog_identity_budget")
    return {
        "schema": "autostop.j1.cold-legacy-catalog-measurement.v1",
        "scope": provider.scope,
        "binding": binding,
        "rows": rows,
        "sources": observations,
        "payload_semantics": "observed_record_fingerprint_unknown_only",
        "ram_disposition": "unenumerated_retired_without_replay",
    }


def verify_checkpoint(reference, *, provider, binding):
    checkpoint = _reference(provider, reference)
    cold.fields(
        checkpoint,
        {"schema", "scope", "binding", "policy_ref", "measurement", "old_crm_ingress_ref"},
        "catalog_checkpoint_fields",
    )
    cold.need(
        checkpoint["schema"] == "autostop.j1.cold-legacy-catalog-checkpoint.v1"
        and checkpoint["scope"] == provider.scope
        and checkpoint["binding"] == binding,
        "catalog_checkpoint_binding",
    )
    policy = _reference(provider, checkpoint["policy_ref"])
    from scripts import j1_forward_cold_catalog_native, j1_forward_cold_history

    history = j1_forward_cold_history.load(
        policy["predecessor_ref"],
        read_reference=provider.read_reference,
        fixture=provider.scope == "native_fixture",
    )

    actual = measure(
        policy,
        binding,
        provider=provider,
        verify_path_binding=lambda unit, source, policy: (
            j1_forward_cold_catalog_native.verify_catalog_path(
                unit, source, policy, provider=provider, history=history
            )
        ),
    )
    cold.need(actual == checkpoint["measurement"], "catalog_checkpoint_changed")
    j1_forward_cold_catalog_native.verify_old_crm_ingress(
        checkpoint["old_crm_ingress_ref"], provider=provider, binding=binding
    )
    return {**actual, "old_crm_ingress_ref": checkpoint["old_crm_ingress_ref"]}
