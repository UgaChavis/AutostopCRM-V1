#!/usr/bin/env python3
"""Capture and restore the non-database state of a coordinated CRM release.

The normal CRM backup owns CRM and Manager business data.  This helper owns
only the small, root-managed release surface that must move in lock-step with
Automation Center: the scheduler unit/config/timer drop-ins and the work
Telegram release/duty assets.  It never follows a captured symlink and never
prints file contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

FORMAT = "autostop_coordinated_release_state_v1"
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class Artifact:
    key: str
    path: Path
    kind: str
    target_prefix: Path | None = None


@dataclass(frozen=True)
class ReleaseLayout:
    scheduler_db: Path = Path("/var/lib/autostop-manager-scheduler/registry.sqlite3")
    artifacts: tuple[Artifact, ...] = (
        Artifact(
            "scheduler_unit",
            Path("/etc/systemd/system/autostop-manager-scheduler.service"),
            "file",
        ),
        Artifact(
            "scheduler_config",
            Path("/etc/autostop-manager/automation-control.env"),
            "file",
        ),
        Artifact(
            "managed_pc_fleet_health_dropin",
            Path(
                "/etc/systemd/system/autostop-managed-pc-fleet-health.timer.d/"
                "50-autostop-automation.conf"
            ),
            "file",
        ),
        Artifact(
            "managed_pc_health_dropin",
            Path(
                "/etc/systemd/system/autostop-managed-pc-health.timer.d/50-autostop-automation.conf"
            ),
            "file",
        ),
        Artifact(
            "managed_pc_pending_cleanup_dropin",
            Path(
                "/etc/systemd/system/autostop-managed-pc-pending-cleanup.timer.d/"
                "50-autostop-automation.conf"
            ),
            "file",
        ),
        Artifact(
            "work_telegram_unit",
            Path("/etc/systemd/system/autostop-work-telegram.service"),
            "file",
        ),
        Artifact(
            "codex_wake_unit",
            Path("/etc/systemd/system/autostop-codex-wake.service"),
            "file",
        ),
        Artifact(
            "codex_start_unit",
            Path("/etc/systemd/system/autostop-codex-start.service"),
            "file",
        ),
        Artifact(
            "work_telegram_monitor_env",
            Path("/etc/autostop-work-telegram/monitor.env"),
            "file",
        ),
        Artifact(
            "work_telegram_wake_config",
            Path("/etc/autostop-work-telegram/wake.json"),
            "file",
        ),
        Artifact(
            "work_telegram_owner_config",
            Path("/etc/autostop-work-telegram/owner.json"),
            "file",
        ),
        Artifact(
            "work_telegram_media_wrapper",
            Path("/usr/local/sbin/autostop-work-telegram-media"),
            "file",
        ),
        Artifact(
            "work_telegram_current",
            Path("/opt/autostop-work-telegram-releases/current"),
            "symlink",
            Path("/opt/autostop-work-telegram-releases"),
        ),
        Artifact(
            "work_telegram_venv",
            Path("/opt/autostop-work-telegram-venv"),
            "symlink",
            Path("/opt/autostop-work-telegram-runtimes"),
        ),
        Artifact(
            "work_telegram_model",
            Path("/opt/autostop-work-telegram-models/faster-whisper-small"),
            "symlink",
            Path("/opt/autostop-work-telegram-runtimes"),
        ),
    )
    services: tuple[str, ...] = (
        "autostop-manager-scheduler.service",
        "autostop-work-telegram.service",
        "autostop-codex-wake.service",
        "autostop-codex-start.service",
        "autostop-managed-pc-fleet-health.timer",
        "autostop-managed-pc-health.timer",
        "autostop-managed-pc-pending-cleanup.timer",
        "autostop24-db-backup.timer",
        "autostop-app-watchdog.timer",
    )


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _safe_parent(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise RuntimeError("coordinated_release_parent_invalid")


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("coordinated_release_snapshot_directory_invalid")


def _read_service_state(unit: str, runner: Runner) -> dict[str, str]:
    result = runner(
        (
            "systemctl",
            "show",
            unit,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=UnitFileState",
            "--property=TimersCalendar",
            "--property=TimersMonotonic",
            "--no-pager",
        )
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {
            "LoadState",
            "ActiveState",
            "UnitFileState",
            "TimersCalendar",
            "TimersMonotonic",
        }:
            values[key] = value
    load_state = values.get("LoadState", "")
    if load_state == "not-found":
        return {
            "load_state": "not-found",
            "active_state": values.get("ActiveState", "inactive") or "inactive",
            "unit_file_state": values.get("UnitFileState", "disabled") or "disabled",
        }
    if result.returncode != 0 or load_state != "loaded":
        raise RuntimeError("coordinated_release_service_state_unavailable")
    active_state = values.get("ActiveState", "")
    unit_file_state = values.get("UnitFileState", "")
    if active_state not in {
        "active",
        "inactive",
        "failed",
        "activating",
        "deactivating",
    } or unit_file_state not in {
        "enabled",
        "enabled-runtime",
        "disabled",
        "disabled-runtime",
        "static",
        "indirect",
        "generated",
        "masked",
    }:
        raise RuntimeError("coordinated_release_service_state_invalid")
    return {
        "load_state": load_state,
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "timers_calendar": values.get("TimersCalendar", "")[:4096],
        "timers_monotonic": values.get("TimersMonotonic", "")[:4096],
    }


def _capture_artifact(artifact: Artifact, payload_dir: Path) -> dict[str, Any]:
    try:
        info = artifact.path.lstat()
    except FileNotFoundError:
        return {"key": artifact.key, "kind": artifact.kind, "state": "absent"}
    if artifact.kind == "file":
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeError("coordinated_release_artifact_invalid")
        if info.st_size > 1024 * 1024:
            raise RuntimeError("coordinated_release_artifact_too_large")
        destination = payload_dir / artifact.key
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        source_fd = os.open(artifact.path, flags)
        try:
            with destination.open("xb") as target:
                while chunk := os.read(source_fd, 1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        finally:
            os.close(source_fd)
        os.chmod(destination, 0o600)
        return {
            "key": artifact.key,
            "kind": "file",
            "state": "present",
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "sha256": _sha256(destination),
        }
    if artifact.kind != "symlink" or not stat.S_ISLNK(info.st_mode):
        raise RuntimeError("coordinated_release_artifact_invalid")
    raw_target = os.readlink(artifact.path)
    resolved = (artifact.path.parent / raw_target).resolve(strict=True)
    prefix = artifact.target_prefix
    if prefix is None:
        raise RuntimeError("coordinated_release_symlink_policy_missing")
    resolved_prefix = prefix.resolve(strict=True)
    if resolved == resolved_prefix or not resolved.is_relative_to(resolved_prefix):
        raise RuntimeError("coordinated_release_symlink_target_invalid")
    return {
        "key": artifact.key,
        "kind": "symlink",
        "state": "present",
        "target": str(resolved),
    }


def capture(
    destination: Path,
    *,
    layout: ReleaseLayout = ReleaseLayout(),
    runner: Runner = _run,
) -> dict[str, Any]:
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise RuntimeError("coordinated_release_snapshot_target_invalid")
    _private_directory(destination.parent)
    staging = destination.parent / f".{destination.name}.partial-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    payload_dir = staging / "payload"
    payload_dir.mkdir(mode=0o700)
    try:
        artifacts = [_capture_artifact(item, payload_dir) for item in layout.artifacts]
        try:
            database_info = layout.scheduler_db.lstat()
        except FileNotFoundError:
            database_preexisting = False
        else:
            if not stat.S_ISREG(database_info.st_mode) or stat.S_ISLNK(database_info.st_mode):
                raise RuntimeError("coordinated_release_scheduler_database_invalid")
            database_preexisting = True
        services = {unit: _read_service_state(unit, runner) for unit in layout.services}
        manifest = {
            "format": FORMAT,
            "scheduler_database_preexisting": database_preexisting,
            "artifacts": artifacts,
            "services": services,
        }
        manifest_path = staging / MANIFEST_NAME
        with manifest_path.open("x", encoding="utf-8") as manifest_file:
            json.dump(manifest, manifest_file, sort_keys=True, separators=(",", ":"))
            manifest_file.write("\n")
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.chmod(manifest_path, 0o600)
        os.replace(staging, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        "ok": True,
        "format": FORMAT,
        "scheduler_database_preexisting": database_preexisting,
        "artifact_count": len(artifacts),
        "service_count": len(services),
    }


def _load_manifest(snapshot: Path, layout: ReleaseLayout) -> dict[str, Any]:
    _private_directory(snapshot)
    manifest_path = snapshot / MANIFEST_NAME
    info = manifest_path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("coordinated_release_manifest_invalid")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise RuntimeError("coordinated_release_manifest_invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeError("coordinated_release_manifest_invalid")
    if {item.get("key") for item in artifacts if isinstance(item, dict)} != {
        item.key for item in layout.artifacts
    }:
        raise RuntimeError("coordinated_release_manifest_invalid")
    if set(manifest.get("services", {})) != set(layout.services):
        raise RuntimeError("coordinated_release_manifest_invalid")
    return manifest


def verify(
    snapshot: Path,
    *,
    layout: ReleaseLayout = ReleaseLayout(),
) -> dict[str, Any]:
    manifest = _load_manifest(snapshot, layout)
    payload_dir = snapshot / "payload"
    for item in manifest["artifacts"]:
        if item["state"] != "present" or item["kind"] != "file":
            continue
        payload = payload_dir / item["key"]
        info = payload.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o077
            or _sha256(payload) != item.get("sha256")
        ):
            raise RuntimeError("coordinated_release_snapshot_verification_failed")
    return {
        "ok": True,
        "format": FORMAT,
        "scheduler_database_preexisting": bool(manifest["scheduler_database_preexisting"]),
    }


def _remove_exact(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise RuntimeError("coordinated_release_restore_target_invalid")
    path.unlink()


def _restore_file(path: Path, payload: Path, item: dict[str, Any]) -> None:
    _safe_parent(path)
    _remove_exact(path)
    descriptor, temporary_raw = tempfile.mkstemp(prefix=f".{path.name}.restore-", dir=path.parent)
    temporary = Path(temporary_raw)
    try:
        with payload.open("rb") as source, os.fdopen(descriptor, "wb", closefd=True) as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, int(item["mode"]), follow_symlinks=False)
        os.chown(temporary, int(item["uid"]), int(item["gid"]), follow_symlinks=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_symlink(artifact: Artifact, item: dict[str, Any]) -> None:
    target = Path(str(item.get("target", "")))
    prefix = artifact.target_prefix
    if (
        prefix is None
        or target == prefix
        or not target.is_relative_to(prefix)
        or not target.is_dir()
    ):
        raise RuntimeError("coordinated_release_symlink_target_invalid")
    _safe_parent(artifact.path)
    _remove_exact(artifact.path)
    temporary = artifact.path.parent / f".{artifact.path.name}.restore-{uuid4().hex}"
    temporary.symlink_to(target)
    try:
        os.replace(temporary, artifact.path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_registry(
    manifest: dict[str, Any],
    *,
    layout: ReleaseLayout,
    database_backup: Path | None,
) -> None:
    database = layout.scheduler_db
    _safe_parent(database)
    for suffix in ("-wal", "-shm"):
        _remove_exact(Path(f"{database}{suffix}"))
    if not manifest["scheduler_database_preexisting"]:
        _remove_exact(database)
        return
    if database_backup is None:
        raise RuntimeError("coordinated_release_scheduler_backup_required")
    backup_info = database_backup.lstat()
    if (
        not stat.S_ISREG(backup_info.st_mode)
        or stat.S_ISLNK(backup_info.st_mode)
        or backup_info.st_uid != os.geteuid()
        or stat.S_IMODE(backup_info.st_mode) & 0o077
    ):
        raise RuntimeError("coordinated_release_scheduler_backup_invalid")
    _remove_exact(database)
    descriptor, temporary_raw = tempfile.mkstemp(prefix=".registry.restore-", dir=database.parent)
    temporary = Path(temporary_raw)
    try:
        with (
            database_backup.open("rb") as source,
            os.fdopen(descriptor, "wb", closefd=True) as target,
        ):
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.chown(temporary, 0, 0)
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)


def stop_candidate_services(
    *,
    layout: ReleaseLayout = ReleaseLayout(),
    runner: Runner = _run,
) -> dict[str, Any]:
    # Stop consumers before the scheduler so no inbound event can race the DB
    # restore and no scheduled delivery can begin during link replacement.
    stopped: list[str] = []
    for unit in (
        "autostop-codex-wake.service",
        "autostop-work-telegram.service",
        "autostop-manager-scheduler.service",
    ):
        if unit not in layout.services:
            continue
        state = _read_service_state(unit, runner)
        if state["load_state"] == "not-found":
            continue
        result = runner(("systemctl", "stop", unit))
        if result.returncode != 0:
            raise RuntimeError("coordinated_release_service_stop_failed")
        stopped.append(unit)
    return {"ok": True, "stopped": stopped}


def _restore_services(manifest: dict[str, Any], runner: Runner) -> None:
    if runner(("systemctl", "daemon-reload")).returncode != 0:
        raise RuntimeError("coordinated_release_daemon_reload_failed")
    for unit, expected in manifest["services"].items():
        if expected["load_state"] == "not-found":
            runner(("systemctl", "disable", "--now", unit))
            continue
        unit_file_state = expected["unit_file_state"]
        if unit_file_state in {"enabled", "enabled-runtime"}:
            command = ["systemctl", "enable"]
            if unit_file_state == "enabled-runtime":
                command.append("--runtime")
            command.append(unit)
            if runner(tuple(command)).returncode != 0:
                raise RuntimeError("coordinated_release_service_enable_failed")
        elif unit_file_state in {"disabled", "disabled-runtime", "masked"}:
            if runner(("systemctl", "disable", unit)).returncode != 0:
                raise RuntimeError("coordinated_release_service_disable_failed")
        if expected["active_state"] in {"active", "activating"}:
            result = runner(("systemctl", "start", unit))
        else:
            result = runner(("systemctl", "stop", unit))
        if result.returncode != 0:
            raise RuntimeError("coordinated_release_service_restore_failed")
    for unit, expected in manifest["services"].items():
        actual = _read_service_state(unit, runner)
        if expected["load_state"] == "not-found":
            if actual["load_state"] != "not-found":
                raise RuntimeError("coordinated_release_service_readback_failed")
            continue
        if (actual["active_state"] == "active") != (
            expected["active_state"] in {"active", "activating"}
        ):
            raise RuntimeError("coordinated_release_service_readback_failed")
        expected_enabled = expected["unit_file_state"] in {"enabled", "enabled-runtime"}
        actual_enabled = actual["unit_file_state"] in {"enabled", "enabled-runtime"}
        if actual_enabled != expected_enabled:
            raise RuntimeError("coordinated_release_service_readback_failed")


def restore(
    snapshot: Path,
    *,
    database_backup: Path | None,
    layout: ReleaseLayout = ReleaseLayout(),
    runner: Runner = _run,
) -> dict[str, Any]:
    manifest = _load_manifest(snapshot, layout)
    verify(snapshot, layout=layout)
    artifacts_by_key = {item["key"]: item for item in manifest["artifacts"]}
    payload_dir = snapshot / "payload"
    for artifact in layout.artifacts:
        item = artifacts_by_key[artifact.key]
        if item["state"] == "absent":
            _remove_exact(artifact.path)
        elif artifact.kind == "file":
            _restore_file(artifact.path, payload_dir / artifact.key, item)
        else:
            _restore_symlink(artifact, item)
    _restore_registry(manifest, layout=layout, database_backup=database_backup)
    _restore_services(manifest, runner)
    return {
        "ok": True,
        "format": FORMAT,
        "scheduler_database_restored": bool(manifest["scheduler_database_preexisting"]),
        "first_install_absence_restored": not bool(manifest["scheduler_database_preexisting"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--snapshot", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--snapshot", type=Path, required=True)
    restore_parser.add_argument("--database-backup", type=Path)
    subparsers.add_parser("stop-candidates")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise RuntimeError("coordinated_release_root_required")
        if args.operation == "capture":
            result = capture(args.output)
        elif args.operation == "verify":
            result = verify(args.snapshot)
        elif args.operation == "restore":
            result = restore(
                args.snapshot,
                database_backup=args.database_backup,
            )
        else:
            result = stop_candidate_services()
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
