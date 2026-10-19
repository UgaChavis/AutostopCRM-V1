"""Raw read-only global_hold measurement from an already quiet SQLite image.

Never call AutomationStore.status/initialize: those open a writable connection.
The caller must first prove complete writer closure and continuous native hold.
No checkpoint, PRAGMA changing state, filesystem repair or live RPC occurs here.
"""

from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_io as io

DATABASE = Path("/var/lib/autostop-manager-scheduler/registry.sqlite3")


def file_identity(path):
    info = Path(path).lstat()
    contract.need(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022,
        "hold_database_file",
    )
    return [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)]


def measure(database, expected_identity, expected_sha256, origin_attempt, *, read_file, now=None):
    """Use immutable mode only after independently bound no-sidecar closure.

    All jobs, including archived jobs, are queried without a status API LIMIT.
    Missing/unknown/naive lease timestamps refuse rather than becoming quiet.
    """
    database = Path(database)
    contract.need(
        database.is_absolute()
        and ".." not in database.parts
        and database == database.resolve(strict=True),
        "hold_database_path",
    )
    before = database.stat()
    contract.need(file_identity(database) == expected_identity, "hold_database_identity")
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            Path(str(database) + suffix).lstat()
        except FileNotFoundError:
            continue
        contract.need(False, "hold_database_not_quiet_checkpoint")
    raw = read_file(database)
    contract.need(io.sha(raw) == expected_sha256, "hold_database_checkpoint_bytes")
    current = datetime.now(UTC) if now is None else now
    contract.need(
        current.tzinfo is not None and current.utcoffset().total_seconds() == 0,
        "hold_measurement_time",
    )
    connection = sqlite3.connect(
        "file:" + quote(str(database), safe="/") + "?mode=ro&immutable=1", uri=True, timeout=2
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT hold_enabled,hold_reason,hold_owner_hash,hold_attempt_hash,hold_revision "
            "FROM manager_automation_controller_runtime WHERE singleton=1"
        ).fetchall()
        contract.need(len(row) == 1, "hold_controller_singleton")
        enabled, reason, owner, attempt, revision = row[0]
        expected_hash = io.sha(("release-attempt:" + origin_attempt).encode())
        contract.need(
            type(enabled) is int
            and enabled == 1
            and reason == "release"
            and attempt == expected_hash
            and type(owner) is str
            and contract.HEX.fullmatch(owner)
            and type(revision) is int
            and revision > 0,
            "hold_original_ownership_lost",
        )
        leases = connection.execute(
            "SELECT lease_until FROM manager_automation_runtime LIMIT 100001"
        ).fetchall()
        contract.need(len(leases) <= 100000, "hold_lease_inventory_bound")
        active = 0
        for (value,) in leases:
            if value is None:
                continue
            contract.need(type(value) is str and len(value) <= 40, "hold_lease_unknown")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise io.Refusal("cold_hold_lease_unknown") from None
            contract.need(
                parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0,
                "hold_lease_unknown",
            )
            active += parsed > current
        sending = connection.execute(
            "SELECT COUNT(*) FROM manager_automation_outbox WHERE status='sending'"
        ).fetchone()[0]
        contract.need(active == sending == 0, "hold_scheduler_not_quiet")
    finally:
        connection.close()
    contract.need(
        database.stat() == before
        and file_identity(database) == expected_identity
        and read_file(database) == raw,
        "hold_database_changed_during_measurement",
    )
    return {
        "attempt_hash": attempt,
        "reason": reason,
        "owner_sha256": owner,
        "hold_revision": revision,
        "active_leases": active,
        "outbox_sending": sending,
    }


def verify(validated, provider, *, fixture=False):
    checkpoint = validated["hold_checkpoint"]
    reference = checkpoint["database_ref"]
    if not fixture:
        contract.need(reference["path"] == str(DATABASE), "hold_fixed_database")
    contract.need(
        checkpoint["original_hold_ref"] == validated["package"]["original_hold_ref"],
        "hold_checkpoint_origin",
    )
    backup = provider.read_reference(checkpoint["quiet_backup_ref"])
    current = provider.read_reference(reference)
    contract.need(current == backup, "hold_quiet_backup_not_current")
    measured = measure(
        reference["path"],
        checkpoint["database_identity"],
        reference["sha256"],
        checkpoint["origin_attempt_id"],
        read_file=lambda path: provider.read_reference(
            {"path": str(path), "sha256": reference["sha256"]}
        ),
    )
    contract.need(measured == checkpoint["measurement"], "hold_checkpoint_semantics_changed")
    return measured
