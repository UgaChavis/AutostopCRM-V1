#!/usr/bin/env python3
"""Fail-closed release checks for CRM Automation Center."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MANAGED_TIMERS = {
    "managed_pc_fleet_health",
    "managed_pc_health",
    "managed_pc_pending_cleanup",
}
LOCKED_TIMERS = {"database_backup", "app_watchdog"}
ALL_TIMERS = MANAGED_TIMERS | LOCKED_TIMERS
TIMER_UNITS = {
    "managed_pc_fleet_health": "autostop-managed-pc-fleet-health.timer",
    "managed_pc_health": "autostop-managed-pc-health.timer",
    "managed_pc_pending_cleanup": "autostop-managed-pc-pending-cleanup.timer",
    "database_backup": "autostop24-db-backup.timer",
    "app_watchdog": "autostop-app-watchdog.timer",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("automation_release_json_invalid")
    return value


def _private_output(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise RuntimeError("automation_release_output_invalid")
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise RuntimeError("automation_release_output_directory_invalid")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _feed_state(database: Path) -> dict[str, int]:
    resolved = database.resolve(strict=True)
    info = database.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or resolved != database.absolute()
    ):
        raise RuntimeError("automation_release_feed_database_invalid")
    with sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10) as connection:
        metadata = connection.execute(
            "SELECT value FROM metadata WHERE key = 'high_water'"
        ).fetchone()
        audit_consumer = connection.execute(
            "SELECT COUNT(*) FROM consumers WHERE consumer_id = 'audit-probe'"
        ).fetchone()
        audit_deliveries = connection.execute(
            "SELECT COUNT(*) FROM deliveries WHERE consumer_id = 'audit-probe'"
        ).fetchone()
        return {
            "event_count": int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            "high_water": int(metadata[0]) if metadata else -1,
            "audit_probe_consumers": int(audit_consumer[0]),
            "audit_probe_deliveries": int(audit_deliveries[0]),
        }


def capture_feed(database: Path, output: Path) -> dict[str, Any]:
    state = _feed_state(database)
    _private_output(output, {"format": "automation_release_feed_baseline_v1", **state})
    return {"ok": True, **state}


def verify_feed(database: Path, baseline_path: Path) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    if baseline.get("format") != "automation_release_feed_baseline_v1":
        raise RuntimeError("automation_release_feed_baseline_invalid")
    current = _feed_state(database)
    if (current["event_count"], current["high_water"]) != (
        baseline.get("event_count"),
        baseline.get("high_water"),
    ):
        raise RuntimeError("automation_release_business_feed_changed")
    if current["audit_probe_consumers"] or current["audit_probe_deliveries"]:
        raise RuntimeError("automation_release_audit_probe_present")
    return {"ok": True, **current}


def _technical_file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"state": "absent"}
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_size > 32 * 1024 * 1024
    ):
        raise RuntimeError("automation_release_telegram_state_invalid")
    return {
        "state": "present",
        "size": info.st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _technical_outbox_fingerprint(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {
            "file_count": 0,
            "aggregate_sha256": hashlib.sha256(b"").hexdigest(),
        }
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("automation_release_telegram_outbox_invalid")
    digest = hashlib.sha256()
    count = 0
    for candidate in sorted(path.iterdir(), key=lambda item: item.name):
        candidate_info = candidate.lstat()
        if (
            not stat.S_ISREG(candidate_info.st_mode)
            or stat.S_ISLNK(candidate_info.st_mode)
            or candidate_info.st_size > 32 * 1024 * 1024
        ):
            raise RuntimeError("automation_release_telegram_outbox_invalid")
        count += 1
        if count > 128:
            raise RuntimeError("automation_release_telegram_outbox_invalid")
        digest.update(candidate.name.encode())
        digest.update(b"\0")
        digest.update(str(candidate_info.st_size).encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(candidate.read_bytes()).digest())
    return {
        "file_count": count,
        "aggregate_sha256": digest.hexdigest(),
    }


def capture_telegram_effects(state_dir: Path, runtime_dir: Path, output: Path) -> dict[str, Any]:
    value = {
        "format": "automation_release_telegram_effects_v1",
        "idempotency": _technical_file_fingerprint(state_dir / "idempotency.json"),
        "outbox": _technical_outbox_fingerprint(runtime_dir / "outbox"),
    }
    _private_output(output, value)
    return {
        "ok": True,
        "idempotency_present": value["idempotency"]["state"] == "present",
        "outbox_file_count": value["outbox"]["file_count"],
    }


def verify_telegram_effects(
    state_dir: Path, runtime_dir: Path, baseline_path: Path
) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    if baseline.get("format") != "automation_release_telegram_effects_v1":
        raise RuntimeError("automation_release_telegram_effects_baseline_invalid")
    current_idempotency = _technical_file_fingerprint(state_dir / "idempotency.json")
    current_outbox = _technical_outbox_fingerprint(runtime_dir / "outbox")
    if current_idempotency != baseline.get("idempotency"):
        raise RuntimeError("automation_release_telegram_idempotency_changed")
    if current_outbox != baseline.get("outbox"):
        raise RuntimeError("automation_release_telegram_outbox_changed")
    return {
        "ok": True,
        "idempotency_unchanged": True,
        "outbox_unchanged": True,
        "outbox_file_count": current_outbox["file_count"],
    }


def _manager_data(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("ok") is not True:
        raise RuntimeError("automation_release_manager_status_failed")
    data = document.get("data")
    if not isinstance(data, dict):
        # Offline test/readback helpers may emit the data object directly.
        data = document
    return data


def validate_hold(input_path: Path, *, release_attempt_key: str) -> dict[str, Any]:
    value = _load_json(input_path)
    hold = value.get("global_hold")
    expected_attempt = hashlib.sha256(f"release-attempt:{release_attempt_key}".encode()).hexdigest()
    if (
        value.get("ok") is not True
        or value.get("quiescent") is not True
        or not isinstance(hold, dict)
        or hold.get("enabled") is not True
        or hold.get("reason") != "release"
        or hold.get("attempt_hash") != expected_attempt
    ):
        raise RuntimeError("automation_release_hold_ownership_failed")
    return {"ok": True, "held": True, "quiescent": True, "owner_verified": True}


def _baseline_timer_states(snapshot: Path) -> dict[str, dict[str, Any]]:
    manifest = _load_json(snapshot / "manifest.json")
    if manifest.get("format") != "autostop_coordinated_release_state_v1":
        raise RuntimeError("automation_release_timer_baseline_invalid")
    services = manifest.get("services")
    if not isinstance(services, dict):
        raise RuntimeError("automation_release_timer_baseline_invalid")
    baseline: dict[str, dict[str, Any]] = {}
    for timer_id, unit in TIMER_UNITS.items():
        service = services.get(unit)
        if not isinstance(service, dict) or service.get("load_state") != "loaded":
            raise RuntimeError("automation_release_timer_baseline_invalid")
        unit_file_state = str(service.get("unit_file_state") or "")
        active_state = str(service.get("active_state") or "")
        monotonic = str(service.get("timers_monotonic") or "")
        period_minutes: int | None = None
        match = re.search(r"OnUnitActiveUSec=(\d+)(s|min|h)\b", monotonic)
        if match:
            seconds = int(match.group(1)) * {"s": 1, "min": 60, "h": 3600}[match.group(2)]
            if seconds % 60 == 0:
                period_minutes = seconds // 60
        baseline[timer_id] = {
            "desired_state": "on" if unit_file_state in {"enabled", "enabled-runtime"} else "off",
            "actual_state": "active" if active_state == "active" else active_state,
            "period_minutes": period_minutes,
        }
    return baseline


def validate_manager(
    input_path: Path,
    *,
    manager_revision: str,
    crm_revision: str,
    crm_version: str,
    release_attempt_key: str,
    expect_held: bool,
    baseline_snapshot: Path,
    require_dependencies_ready: bool = False,
) -> dict[str, Any]:
    data = _manager_data(_load_json(input_path))
    hold = data.get("global_hold")
    if not isinstance(hold, dict) or bool(hold.get("enabled")) != expect_held:
        raise RuntimeError("automation_release_hold_readback_failed")
    if expect_held:
        expected_attempt = hashlib.sha256(
            f"release-attempt:{release_attempt_key}".encode()
        ).hexdigest()
        if hold.get("reason") != "release" or hold.get("attempt_hash") != expected_attempt:
            raise RuntimeError("automation_release_hold_ownership_failed")
    jobs = [item for item in data.get("jobs", []) if isinstance(item, dict)]
    digest = [item for item in jobs if item.get("template_id") == "crm_digest_v1"]
    if len(digest) != 1 or digest[0].get("desired_state") != "off":
        raise RuntimeError("automation_release_digest_not_off")
    if digest[0].get("schedule") != {
        "kind": "interval",
        "every_minutes": 20,
        "timezone": "Asia/Krasnoyarsk",
        "active_window": "24/7",
    }:
        raise RuntimeError("automation_release_digest_schedule_invalid")
    if digest[0].get("lease_until"):
        raise RuntimeError("automation_release_digest_lease_present")
    timers = [item for item in data.get("system_timers", []) if isinstance(item, dict)]
    timer_ids = {str(item.get("timer_id")) for item in timers}
    if timer_ids != ALL_TIMERS:
        raise RuntimeError("automation_release_timer_set_invalid")
    baseline_timers = _baseline_timer_states(baseline_snapshot)
    for timer in timers:
        timer_id = str(timer["timer_id"])
        expected_mode = "managed" if timer_id in MANAGED_TIMERS else "read_only"
        if timer.get("control_mode") != expected_mode:
            raise RuntimeError("automation_release_timer_policy_invalid")
        if (timer_id in LOCKED_TIMERS) != bool(timer.get("locked")):
            raise RuntimeError("automation_release_timer_lock_invalid")
        if timer.get("reconcile_state") != "in_sync" or timer.get("error_code") is not None:
            raise RuntimeError("automation_release_timer_drift")
        baseline = baseline_timers[timer_id]
        if (
            timer.get("desired_state") != baseline["desired_state"]
            or timer.get("actual_state") != baseline["actual_state"]
        ):
            raise RuntimeError("automation_release_timer_mode_changed")
        if timer_id in MANAGED_TIMERS and (
            timer.get("period_minutes") != baseline["period_minutes"]
            or timer.get("actual_period_minutes") != baseline["period_minutes"]
        ):
            raise RuntimeError("automation_release_timer_period_changed")
    templates = [item for item in data.get("templates", []) if isinstance(item, dict)]
    if len([item for item in templates if item.get("template_id") == "crm_digest_v1"]) != 1:
        raise RuntimeError("automation_release_template_missing")
    readiness = data.get("readiness")
    packet = readiness.get("execution_packet") if isinstance(readiness, dict) else None
    checks = readiness.get("checks") if isinstance(readiness, dict) else None
    if require_dependencies_ready and (
        not isinstance(checks, dict)
        or not checks
        or any(value != "ready" for value in checks.values())
    ):
        raise RuntimeError("automation_release_manager_dependencies_not_ready")
    if not expect_held and (not isinstance(readiness, dict) or readiness.get("ready") is not True):
        raise RuntimeError("automation_release_manager_not_ready")
    if not isinstance(packet, dict) or packet.get("manager_revision") != manager_revision:
        raise RuntimeError("automation_release_manager_revision_mismatch")
    crm_identity = packet.get("crm")
    if (
        not isinstance(crm_identity, dict)
        or crm_identity.get("revision") != crm_revision
        or crm_identity.get("version") != crm_version
    ):
        raise RuntimeError("automation_release_crm_revision_mismatch")
    outbox = packet.get("outbox")
    by_status = outbox.get("by_status") if isinstance(outbox, dict) else None
    if (
        not isinstance(by_status, dict)
        or int(by_status.get("sending", 0)) != 0
        or int(outbox.get("total_count", -1)) != 0
    ):
        raise RuntimeError("automation_release_outbox_not_quiescent")
    runs = packet.get("runs")
    if not isinstance(runs, dict) or int(runs.get("total_count", -1)) != 0:
        raise RuntimeError("automation_release_digest_run_present")
    controller = data.get("controller")
    if not expect_held and (
        not isinstance(controller, dict) or controller.get("state") != "active"
    ):
        raise RuntimeError("automation_release_scheduler_not_active")
    return {
        "ok": True,
        "held": expect_held,
        "digest": "off",
        "timer_count": len(timers),
        "manager_revision": manager_revision,
        "crm_revision": crm_revision,
        "crm_version": crm_version,
    }


def _post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            status = int(response.status)
            body = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read(1024 * 1024)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("automation_release_crm_response_invalid")
    return status, value


def validate_crm(
    *,
    base_url: str,
    username: str,
    password: str,
    manager_revision: str,
    expect_held: bool,
    socket_path: Path,
    manager_revision_path: Path,
) -> dict[str, Any]:
    socket_info = socket_path.lstat()
    if not stat.S_ISSOCK(socket_info.st_mode) or stat.S_IMODE(socket_info.st_mode) != 0o660:
        raise RuntimeError("automation_release_socket_mount_invalid")
    if Path("/var/lib/autostop-manager-scheduler/registry.sqlite3").exists():
        raise RuntimeError("automation_release_registry_exposed_to_crm")
    if manager_revision_path.read_text(encoding="utf-8").strip() != manager_revision:
        raise RuntimeError("automation_release_crm_manager_revision_mismatch")
    login_status, login = _post_json(
        base_url,
        "/api/login_operator",
        {"username": username, "password": password},
    )
    token = ((login.get("data") or {}).get("session") or {}).get("token")
    if login_status != 200 or login.get("ok") is not True or not isinstance(token, str):
        raise RuntimeError("automation_release_crm_login_failed")
    status_code, response = _post_json(
        base_url,
        "/api/automation_center/status",
        {},
        headers={"X-Operator-Session": token},
    )
    data = response.get("data")
    if status_code != 200 or response.get("ok") is not True or not isinstance(data, dict):
        raise RuntimeError("automation_release_crm_status_failed")
    controller = data.get("controller")
    hold = controller.get("hold") if isinstance(controller, dict) else None
    if not isinstance(hold, dict) or bool(hold.get("enabled")) != expect_held:
        raise RuntimeError("automation_release_crm_hold_mismatch")
    jobs = [item for item in data.get("jobs", []) if isinstance(item, dict)]
    digest = [item for item in jobs if item.get("template_id") == "crm_digest_v1"]
    if len(digest) != 1 or digest[0].get("desired_state") != "off":
        raise RuntimeError("automation_release_crm_digest_not_off")
    timers = [item for item in data.get("system_timers", []) if isinstance(item, dict)]
    if {str(item.get("id")) for item in timers} != ALL_TIMERS:
        raise RuntimeError("automation_release_crm_timer_set_invalid")
    if any(not bool(item.get("locked")) for item in timers if item.get("id") in LOCKED_TIMERS):
        raise RuntimeError("automation_release_crm_locked_timer_mutable")
    return {
        "ok": True,
        "held": expect_held,
        "digest": "off",
        "timer_count": len(timers),
        "socket": "uds",
    }


def validate_telegram(
    *,
    expect_inbound: bool | None,
    expected_revision: str | None,
    release_link: Path,
    owner_config: Path,
) -> dict[str, Any]:
    # Raw bridge status contains account id/name/username. Consume it only from
    # the pipe and deliberately retain/echo only technical booleans and hashes.
    raw = os.read(0, 64 * 1024)
    if len(raw) == 64 * 1024:
        raise RuntimeError("automation_release_telegram_status_too_large")
    status_value = json.loads(raw)
    if not isinstance(status_value, dict):
        raise RuntimeError("automation_release_telegram_status_invalid")
    if (
        status_value.get("ok") is not True
        or status_value.get("transport_ready") is not True
        or status_value.get("owner_notification_configured") is not True
        or (
            expect_inbound is not None
            and bool(status_value.get("inbound_enabled")) != expect_inbound
        )
    ):
        raise RuntimeError("automation_release_telegram_not_ready")
    if not release_link.is_symlink():
        raise RuntimeError("automation_release_telegram_link_invalid")
    release_target = release_link.resolve(strict=True)
    if release_target.parent != release_link.parent.resolve(strict=True):
        raise RuntimeError("automation_release_telegram_link_invalid")
    if expected_revision and not release_target.name.endswith(f"-{expected_revision[:12]}"):
        raise RuntimeError("automation_release_telegram_revision_mismatch")
    owner_info = owner_config.lstat()
    if (
        not stat.S_ISREG(owner_info.st_mode)
        or stat.S_ISLNK(owner_info.st_mode)
        or owner_info.st_uid != 0
        or stat.S_IMODE(owner_info.st_mode) & 0o027
    ):
        raise RuntimeError("automation_release_telegram_owner_config_invalid")
    return {
        "ok": True,
        "transport_ready": True,
        "owner_notification_configured": True,
        "inbound_enabled": bool(status_value.get("inbound_enabled")),
        "release_revision_verified": expected_revision is not None,
        "owner_config_verified": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    feed_capture = subparsers.add_parser("capture-feed")
    feed_capture.add_argument("--database", type=Path, required=True)
    feed_capture.add_argument("--output", type=Path, required=True)
    feed_verify = subparsers.add_parser("verify-feed")
    feed_verify.add_argument("--database", type=Path, required=True)
    feed_verify.add_argument("--baseline", type=Path, required=True)
    telegram_capture = subparsers.add_parser("capture-telegram-effects")
    telegram_capture.add_argument("--state-dir", type=Path, required=True)
    telegram_capture.add_argument("--runtime-dir", type=Path, required=True)
    telegram_capture.add_argument("--output", type=Path, required=True)
    telegram_verify = subparsers.add_parser("verify-telegram-effects")
    telegram_verify.add_argument("--state-dir", type=Path, required=True)
    telegram_verify.add_argument("--runtime-dir", type=Path, required=True)
    telegram_verify.add_argument("--baseline", type=Path, required=True)
    hold_check = subparsers.add_parser("hold")
    hold_check.add_argument("--input", type=Path, required=True)
    hold_check.add_argument("--release-attempt-key", required=True)
    manager = subparsers.add_parser("manager")
    manager.add_argument("--input", type=Path, required=True)
    manager.add_argument("--manager-revision", required=True)
    manager.add_argument("--crm-revision", required=True)
    manager.add_argument("--crm-version", required=True)
    manager.add_argument("--release-attempt-key", required=True)
    manager.add_argument("--baseline-snapshot", type=Path, required=True)
    manager.add_argument("--require-dependencies-ready", action="store_true")
    held = manager.add_mutually_exclusive_group(required=True)
    held.add_argument("--expect-held", action="store_true")
    held.add_argument("--expect-released", action="store_true")
    crm = subparsers.add_parser("crm")
    crm.add_argument("--base-url", required=True)
    crm.add_argument("--username", required=True)
    crm.add_argument("--password", required=True)
    crm.add_argument("--manager-revision", required=True)
    crm.add_argument("--socket", type=Path, required=True)
    crm.add_argument("--manager-revision-path", type=Path, required=True)
    crm_held = crm.add_mutually_exclusive_group(required=True)
    crm_held.add_argument("--expect-held", action="store_true")
    crm_held.add_argument("--expect-released", action="store_true")
    telegram = subparsers.add_parser("telegram")
    telegram.add_argument("--expected-revision")
    telegram.add_argument("--release-link", type=Path, required=True)
    telegram.add_argument("--owner-config", type=Path, required=True)
    telegram_inbound = telegram.add_mutually_exclusive_group(required=True)
    telegram_inbound.add_argument("--expect-inbound", action="store_true")
    telegram_inbound.add_argument("--expect-outbound-only", action="store_true")
    telegram_inbound.add_argument("--allow-either-inbound", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "capture-feed":
            result = capture_feed(args.database, args.output)
        elif args.operation == "verify-feed":
            result = verify_feed(args.database, args.baseline)
        elif args.operation == "capture-telegram-effects":
            result = capture_telegram_effects(args.state_dir, args.runtime_dir, args.output)
        elif args.operation == "verify-telegram-effects":
            result = verify_telegram_effects(args.state_dir, args.runtime_dir, args.baseline)
        elif args.operation == "hold":
            result = validate_hold(args.input, release_attempt_key=args.release_attempt_key)
        elif args.operation == "manager":
            result = validate_manager(
                args.input,
                manager_revision=args.manager_revision,
                crm_revision=args.crm_revision,
                crm_version=args.crm_version,
                release_attempt_key=args.release_attempt_key,
                expect_held=args.expect_held,
                baseline_snapshot=args.baseline_snapshot,
                require_dependencies_ready=args.require_dependencies_ready,
            )
        elif args.operation == "crm":
            result = validate_crm(
                base_url=args.base_url,
                username=args.username,
                password=args.password,
                manager_revision=args.manager_revision,
                expect_held=args.expect_held,
                socket_path=args.socket,
                manager_revision_path=args.manager_revision_path,
            )
        else:
            result = validate_telegram(
                expect_inbound=(
                    True if args.expect_inbound else False if args.expect_outbound_only else None
                ),
                expected_revision=args.expected_revision,
                release_link=args.release_link,
                owner_config=args.owner_config,
            )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
