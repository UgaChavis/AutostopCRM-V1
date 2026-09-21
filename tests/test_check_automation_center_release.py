from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import check_automation_center_release as release_check


def _feed(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE events(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE consumers(consumer_id TEXT PRIMARY KEY, acked_sequence INTEGER NOT NULL);
            CREATE TABLE deliveries(consumer_id TEXT NOT NULL, window_high_water INTEGER NOT NULL);
            INSERT INTO metadata VALUES('high_water', '2');
            INSERT INTO events VALUES(1, '{}');
            INSERT INTO events VALUES(2, '{}');
            INSERT INTO consumers VALUES('audit-probe', 0);
            INSERT INTO deliveries VALUES('audit-probe', 2);
            """
        )


def test_feed_baseline_proves_business_events_unchanged_and_probe_absent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "feed.sqlite3"
    _feed(database)
    baseline = tmp_path / "baseline.json"
    os.chmod(tmp_path, 0o700)
    captured = release_check.capture_feed(database, baseline)
    assert captured["event_count"] == 2
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM deliveries WHERE consumer_id = 'audit-probe'")
        connection.execute("DELETE FROM consumers WHERE consumer_id = 'audit-probe'")

    verified = release_check.verify_feed(database, baseline)
    assert verified["high_water"] == 2
    assert verified["audit_probe_consumers"] == 0

    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO events VALUES(3, '{}')")
        connection.execute("UPDATE metadata SET value = '3' WHERE key = 'high_water'")
    with pytest.raises(RuntimeError, match="automation_release_business_feed_changed"):
        release_check.verify_feed(database, baseline)


def test_telegram_effect_baseline_detects_idempotency_or_outbox_changes(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    outbox = runtime_dir / "outbox"
    state_dir.mkdir()
    outbox.mkdir(parents=True)
    (state_dir / "idempotency.json").write_text("{}\n", encoding="utf-8")
    baseline = tmp_path / "telegram-effects.json"
    os.chmod(tmp_path, 0o700)

    captured = release_check.capture_telegram_effects(state_dir, runtime_dir, baseline)
    assert captured == {
        "ok": True,
        "idempotency_present": True,
        "outbox_file_count": 0,
    }
    assert (
        release_check.verify_telegram_effects(state_dir, runtime_dir, baseline)["outbox_unchanged"]
        is True
    )

    (outbox / "unexpected").write_text("payload", encoding="utf-8")
    with pytest.raises(RuntimeError, match="automation_release_telegram_outbox_changed"):
        release_check.verify_telegram_effects(state_dir, runtime_dir, baseline)


def _timer_baseline(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    services = {}
    for timer_id, unit in release_check.TIMER_UNITS.items():
        enabled = timer_id != "app_watchdog"
        services[unit] = {
            "load_state": "loaded",
            "active_state": "active" if enabled else "inactive",
            "unit_file_state": "enabled" if enabled else "disabled",
            "timers_monotonic": "{ OnUnitActiveUSec=20min }"
            if timer_id in release_check.MANAGED_TIMERS
            else "",
            "timers_calendar": "",
        }
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "format": "autostop_coordinated_release_state_v1",
                "services": services,
            }
        ),
        encoding="utf-8",
    )
    return snapshot


def _manager_status(
    revision: str, crm_revision: str, attempt: str, *, held: bool
) -> dict[str, object]:
    timers = []
    for timer_id in sorted(release_check.ALL_TIMERS):
        managed = timer_id in release_check.MANAGED_TIMERS
        enabled = timer_id != "app_watchdog"
        timers.append(
            {
                "timer_id": timer_id,
                "control_mode": "managed" if managed else "read_only",
                "locked": not managed,
                "desired_state": "on" if enabled else "off",
                "actual_state": "active" if enabled else "inactive",
                "period_minutes": 20 if managed else None,
                "actual_period_minutes": 20 if managed else None,
                "reconcile_state": "in_sync",
                "error_code": None,
            }
        )
    return {
        "ok": True,
        "data": {
            "controller": {"state": "held" if held else "active"},
            "global_hold": {
                "enabled": held,
                "reason": "release" if held else None,
                "attempt_hash": hashlib.sha256(f"release-attempt:{attempt}".encode()).hexdigest()
                if held
                else None,
            },
            "jobs": [
                {
                    "template_id": "crm_digest_v1",
                    "desired_state": "off",
                    "lease_until": None,
                    "schedule": {
                        "kind": "interval",
                        "every_minutes": 20,
                        "timezone": "Asia/Krasnoyarsk",
                        "active_window": "24/7",
                    },
                }
            ],
            "system_timers": timers,
            "templates": [{"template_id": "crm_digest_v1"}],
            "readiness": {
                "ready": not held,
                "checks": {
                    "schema": "ready",
                    "controller": "ready",
                    "crm_digest_executor": "ready",
                    "crm_change_feed": "ready",
                    "notification_transport": "ready",
                    "owner_notification_target": "ready",
                },
                "execution_packet": {
                    "manager_revision": revision,
                    "crm": {"revision": crm_revision, "version": "test"},
                    "outbox": {"by_status": {"sending": 0}, "total_count": 0},
                    "runs": {"by_status": {}, "total_count": 0},
                },
            },
        },
    }


def test_manager_check_requires_owned_hold_off_digest_and_exact_timer_policy(
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    crm_revision = "c" * 40
    attempt = "release-attempt-123"
    baseline = _timer_baseline(tmp_path)
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(_manager_status(revision, crm_revision, attempt, held=True)),
        encoding="utf-8",
    )
    assert (
        release_check.validate_manager(
            status_path,
            manager_revision=revision,
            crm_revision=crm_revision,
            crm_version="test",
            release_attempt_key=attempt,
            expect_held=True,
            baseline_snapshot=baseline,
            require_dependencies_ready=True,
        )["digest"]
        == "off"
    )

    invalid = _manager_status(revision, crm_revision, attempt, held=True)
    invalid["data"]["jobs"][0]["desired_state"] = "on"  # type: ignore[index]
    status_path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(RuntimeError, match="automation_release_digest_not_off"):
        release_check.validate_manager(
            status_path,
            manager_revision=revision,
            crm_revision=crm_revision,
            crm_version="test",
            release_attempt_key=attempt,
            expect_held=True,
            baseline_snapshot=baseline,
        )


def test_telegram_check_discards_identity_and_keeps_only_safe_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision = "b" * 40
    releases = tmp_path / "releases"
    releases.mkdir()
    target = releases / f"20260921T000000Z-{revision[:12]}"
    target.mkdir()
    link = releases / "current"
    link.symlink_to(target)
    owner = tmp_path / "owner.json"
    owner.write_text('{"owner_peer_id":123}\n', encoding="utf-8")
    os.chown(owner, 0, 0)
    os.chmod(owner, 0o640)
    raw = json.dumps(
        {
            "ok": True,
            "transport_ready": True,
            "owner_notification_configured": True,
            "inbound_enabled": False,
            "account": {"id": 999, "name": "private", "username": "secret"},
        }
    ).encode()
    monkeypatch.setattr(release_check.os, "read", lambda _fd, _limit: raw)

    result = release_check.validate_telegram(
        expect_inbound=False,
        expected_revision=revision,
        release_link=link,
        owner_config=owner,
    )

    serialized = json.dumps(result)
    assert result["transport_ready"] is True
    assert "999" not in serialized
    assert "private" not in serialized
    assert "secret" not in serialized
    assert "owner_config_sha256" not in serialized
    assert "owner_config_sha256" not in serialized
