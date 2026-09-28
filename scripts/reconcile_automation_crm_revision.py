"""Guarded scheduler identity reconciliation after a CRM-only release.

This command changes one non-secret revision key and restarts only the
Automation Center scheduler. It must run after the CRM image is installed.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deploy_crm_only import (  # noqa: E402
    AUTOMATION_DB,
    BACKUP_ROOT,
    MANAGER_ENV,
    MANAGER_LINK,
    NON_CRM_CONTAINERS,
    SHA_RE,
    TELEGRAM_LINK,
    TELEGRAM_UNITS,
    CrmOnlyRelease,
    ReleaseError,
)

CONTROL_ENV = Path("/etc/autostop-manager/automation-control.env")
SCHEDULER_UNIT = "autostop-manager-scheduler.service"
CRM_KEY = b"AUTOSTOP_AUTOMATION_CRM_REVISION"
MANAGER_KEY = b"AUTOSTOP_MANAGER_REVISION"
TIMER_UNITS = {
    "managed_pc_health": "autostop-managed-pc-health.timer",
    "managed_pc_fleet_health": "autostop-managed-pc-fleet-health.timer",
    "managed_pc_pending_cleanup": "autostop-managed-pc-pending-cleanup.timer",
    "database_backup": "autostop24-db-backup.timer",
    "app_watchdog": "autostop-app-watchdog.timer",
}
SNAPSHOT_UNITS = (SCHEDULER_UNIT, *TELEGRAM_UNITS, *TIMER_UNITS.values())


@dataclass(frozen=True)
class EnvSnapshot:
    contents: bytes
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    mtime_ns: int
    ctime_ns: int


class AtomicReplaceOutcomeError(ReleaseError):
    """The replacement syscall succeeded, but durable readback did not."""

    def __init__(self, device: int, inode: int) -> None:
        super().__init__("scheduler identity replacement outcome needs readback")
        self.device = device
        self.inode = inode


def _single_value(contents: bytes, key: bytes) -> bytes:
    values = [
        line.split(b"=", 1)[1] for line in contents.splitlines() if line.startswith(key + b"=")
    ]
    if len(values) != 1:
        raise ReleaseError(f"expected exactly one {key.decode('ascii')} key")
    return values[0]


def updated_identity(contents: bytes, *, old_sha: str, new_sha: str, manager_sha: str) -> bytes:
    """Replace exactly one CRM identity line and preserve every other byte."""

    if not all(SHA_RE.fullmatch(value) for value in (old_sha, new_sha, manager_sha)):
        raise ReleaseError("invalid identity SHA")
    if _single_value(contents, CRM_KEY) != old_sha.encode("ascii"):
        raise ReleaseError("scheduler config CRM identity differs from expected SHA")
    if _single_value(contents, MANAGER_KEY) != manager_sha.encode("ascii"):
        raise ReleaseError("scheduler config Manager identity differs from installed SHA")
    old_line = CRM_KEY + b"=" + old_sha.encode("ascii")
    new_line = CRM_KEY + b"=" + new_sha.encode("ascii")
    result = []
    for line in contents.splitlines(keepends=True):
        if line.startswith(CRM_KEY + b"="):
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
            if line != old_line + ending:
                raise ReleaseError("scheduler CRM identity line has unexpected formatting")
            result.append(new_line + ending)
        else:
            result.append(line)
    return b"".join(result)


def secure_snapshot(path: Path) -> EnvSnapshot:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ReleaseError("scheduler identity file has unsafe type, owner or mode")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ReleaseError("scheduler identity path changed during open")
        with os.fdopen(descriptor, "rb") as source:
            contents = source.read()
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ):
        raise ReleaseError("scheduler identity file changed during read")
    return EnvSnapshot(
        contents,
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def atomic_replace_exact(path: Path, expected: EnvSnapshot, replacement: bytes) -> EnvSnapshot:
    current = secure_snapshot(path)
    if current != expected:
        raise ReleaseError("scheduler identity file changed concurrently")
    fd, temporary = tempfile.mkstemp(prefix=".automation-control-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, expected.uid, expected.gid)
        with os.fdopen(fd, "wb") as output:
            output.write(replacement)
            output.flush()
            os.fsync(output.fileno())
        if secure_snapshot(path) != expected:
            raise ReleaseError("scheduler identity file changed before replacement")
        replacement_info = os.stat(temporary, follow_symlinks=False)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            result = secure_snapshot(path)
            if result.contents != replacement:
                raise ReleaseError("scheduler identity write readback failed")
            return result
        except Exception as error:
            raise AtomicReplaceOutcomeError(
                replacement_info.st_dev, replacement_info.st_ino
            ) from error
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AutomationRevisionReconciler:
    def __init__(
        self,
        release: CrmOnlyRelease,
        *,
        expected_reported_sha: str,
        control_env: Path = CONTROL_ENV,
        guarded_controller_repo: Path | None = None,
        guarded_controller_sha: str | None = None,
        expected_telegram_release_dir: str | None = None,
    ) -> None:
        if not SHA_RE.fullmatch(expected_reported_sha):
            raise ReleaseError("expected reported SHA must be exact")
        self.release = release
        self.expected_reported_sha = expected_reported_sha
        self.control_env = control_env
        self.guarded_controller_repo = guarded_controller_repo
        self.guarded_controller_sha = guarded_controller_sha
        self.guarded_controller_hash = ""
        self.expected_telegram_release_dir = expected_telegram_release_dir
        self.backup_dir = BACKUP_ROOT / "scheduler-revision" / release.release_id
        self.telegram_unit_states: dict[str, tuple[str, str]] = {}
        self.unit_definitions: dict[str, str] = {}
        self.manager_link_target = ""
        self.manager_mcp_pid = ""
        self.other_container_ids: dict[str, str] = {}
        self.telegram_link_target = ""
        self.pinned_telegram_duty_path = Path("/nonexistent/pinned-telegram-duty")

    def scheduler_packet(self) -> dict:
        script = (
            "import json; from autostop_manager.automation_release import _socket_client,_client_status; "
            "from autostop_manager.config import get_automation_control_socket_path,load_runtime_env; "
            "load_runtime_env(); s=_client_status(_socket_client(get_automation_control_socket_path())); "
            "r=s.get('readiness',{}); p=r.get('execution_packet',{}); h=s.get('global_hold',{}); "
            "print(json.dumps({'ready':r.get('ready'),'hold':{'enabled':h.get('enabled'),"
            "'reason':h.get('reason'),'attempt_hash':h.get('attempt_hash')},"
            "'crm_revision':p.get('crm',{}).get('revision'),"
            "'crm_version':p.get('crm',{}).get('version'),"
            "'manager_revision':p.get('manager_revision'),"
            "'dependencies':p.get('dependencies',{}),"
            "'outbox':p.get('outbox',{}),"
            "'timers':[{k:t.get(k) for k in ('timer_id','desired_state','actual_state',"
            "'reconcile_state','period_minutes','actual_period_minutes','revision')} "
            "for t in s.get('system_timers',[])],"
            "'runs':p.get('runs',{}),'cursors':p.get('cursors',[])}))"
        )
        return json.loads(
            self.release.run(
                "/opt/AutostopManager/.venv/bin/python",
                "-c",
                script,
                env=self.release.automation_env(),
                timeout=20,
            )
        )

    def _process_identity(self, pid: str, key: bytes) -> bytes:
        values = [
            entry[len(key) + 1 :]
            for entry in Path(f"/proc/{pid}/environ").read_bytes().split(b"\x00")
            if entry.startswith(key + b"=")
        ]
        if len(values) != 1:
            raise ReleaseError("running scheduler has missing or duplicate identity")
        return values[0]

    def _source_and_runtime(self) -> tuple[str, str]:
        if self.release.source == self.release.production_root:
            raise ReleaseError("identity source must be an isolated worktree")
        if self.release.git("status", "--porcelain=v1", "--untracked-files=all"):
            raise ReleaseError("identity source worktree is dirty")
        if self.release.git("rev-parse", "HEAD") != self.release.sha:
            raise ReleaseError("identity source HEAD differs from target SHA")
        remote = self.release.git("ls-remote", "origin", "refs/heads/autostopcrm-v1").split()
        if not remote or remote[0] != self.release.sha:
            raise ReleaseError("GitHub production branch differs from target SHA")
        record = self.release.inspect_container("autostopcrm")
        installed = (
            record.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
        )
        if (
            installed != self.release.sha
            or record.get("State", {}).get("Health", {}).get("Status") != "healthy"
        ):
            raise ReleaseError("installed CRM is not healthy on target SHA")
        if not MANAGER_LINK.is_symlink():
            raise ReleaseError("installed Manager release link is missing")
        self.manager_link_target = str(MANAGER_LINK.resolve())
        manager_sha = (MANAGER_LINK / "REVISION").read_text().strip()
        if not SHA_RE.fullmatch(manager_sha):
            raise ReleaseError("installed Manager SHA is invalid")
        self.manager_mcp_pid = self.release.service_pid("autostop-manager-mcp.service")
        self.other_container_ids = {
            name: self.release.inspect_container(name)["Id"] for name in NON_CRM_CONTAINERS
        }
        return installed, manager_sha

    def preflight(self) -> tuple[EnvSnapshot, dict, str]:
        _, manager_sha = self._source_and_runtime()
        self.pinned_telegram_duty_path = self._guarded_controller_source()
        environment_files = self.release.run(
            "systemctl", "show", "-p", "EnvironmentFiles", "--value", SCHEDULER_UNIT
        ).splitlines()
        if environment_files.count(f"{self.control_env} (ignore_errors=no)") != 1:
            raise ReleaseError("scheduler unit does not require the expected identity file")
        pid = self.release.service_pid(SCHEDULER_UNIT)
        snapshot = secure_snapshot(self.control_env)
        updated_identity(
            snapshot.contents,
            old_sha=self.expected_reported_sha,
            new_sha=self.release.sha,
            manager_sha=manager_sha,
        )
        if self._process_identity(pid, CRM_KEY) != self.expected_reported_sha.encode():
            raise ReleaseError("running scheduler CRM SHA differs from identity file")
        if self._process_identity(pid, MANAGER_KEY) != manager_sha.encode():
            raise ReleaseError("running scheduler Manager SHA differs from installed Manager")
        packet = self.scheduler_packet()
        if (
            packet.get("crm_revision") != self.expected_reported_sha
            or packet.get("manager_revision") != manager_sha
            or packet.get("hold", {}).get("enabled") is not False
            or packet.get("ready") is not True
            or {timer.get("timer_id") for timer in packet.get("timers", [])} != set(TIMER_UNITS)
            or any(timer.get("reconcile_state") != "in_sync" for timer in packet["timers"])
            or packet.get("dependencies", {}).get("crm_change_feed") != "ready"
            or packet.get("outbox", {}).get("by_status", {}).get("sending", 0) != 0
        ):
            raise ReleaseError("scheduler readiness baseline is inconsistent")
        if not TELEGRAM_LINK.is_symlink():
            raise ReleaseError("work Telegram release link is missing")
        self.telegram_link_target = str(TELEGRAM_LINK.resolve())
        if (
            self.expected_telegram_release_dir is not None
            and self.telegram_link_target != self.expected_telegram_release_dir
        ):
            raise ReleaseError("work Telegram release differs from approved target")
        self._check_pinned_telegram_ready()
        self.telegram_unit_states = {unit: self.release.unit_state(unit) for unit in TELEGRAM_UNITS}
        return snapshot, packet, pid

    def _guarded_controller_source(self) -> Path:
        repo = self.guarded_controller_repo
        sha = self.guarded_controller_sha
        if repo is None or sha is None or not SHA_RE.fullmatch(sha):
            raise ReleaseError("exact guarded Telegram controller source is required")
        repo = repo.resolve()
        source = repo / "scripts/set-work-telegram-duty.sh"
        if not source.is_file() or source.is_symlink():
            raise ReleaseError("guarded Telegram controller source is missing or unsafe")
        if self.release.run("git", "-C", str(repo), "rev-parse", "HEAD") != sha:
            raise ReleaseError("guarded Telegram controller checkout differs from target SHA")
        if self.release.run(
            "git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"
        ):
            raise ReleaseError("guarded Telegram controller checkout is dirty")
        remote = self.release.run(
            "git", "-C", str(repo), "ls-remote", "origin", "refs/heads/AutostopManager"
        ).split()
        if not remote or remote[0] != sha:
            raise ReleaseError("published Manager branch differs from guarded controller SHA")
        tracked_hash = self.release.run(
            "git", "-C", str(repo), "rev-parse", f"{sha}:scripts/set-work-telegram-duty.sh"
        )
        actual_hash = self.release.run("git", "hash-object", str(source))
        if tracked_hash != actual_hash:
            raise ReleaseError("guarded Telegram controller differs from published blob")
        self.guarded_controller_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        return source

    def _backup(self, snapshot: EnvSnapshot, baseline: dict) -> None:
        self.backup_dir.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.backup_dir.parent.chmod(0o700)
        self.backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        config_backup = self.backup_dir / "automation-control.env"
        descriptor = os.open(config_backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(snapshot.contents)
            output.flush()
            os.fsync(output.fileno())
        if config_backup.read_bytes() != snapshot.contents:
            raise ReleaseError("scheduler config backup verification failed")
        registry_backup = self.backup_dir / "registry.sqlite3"
        descriptor = os.open(registry_backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        source = sqlite3.connect(f"file:{AUTOMATION_DB}?mode=ro", uri=True)
        target = sqlite3.connect(registry_backup)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ReleaseError("scheduler registry backup failed integrity check")
        finally:
            source.close()
            target.close()
        registry_backup.chmod(0o600)
        controller_source = self._guarded_controller_source()
        controller_copy = self.backup_dir / "guarded-telegram-duty.sh"
        descriptor = os.open(controller_copy, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o500)
        with os.fdopen(descriptor, "wb") as output:
            output.write(controller_source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        self.pinned_telegram_duty_path = controller_copy
        self._verify_sealed_controller()
        technical = self.backup_dir / "technical-baseline.json"
        descriptor = os.open(technical, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "timers": baseline["timers"],
                    "telegram_units": self.telegram_unit_states,
                    "unit_definitions": self.unit_definitions,
                    "runs": baseline["runs"],
                    "outbox": baseline["outbox"],
                    "cursors": baseline["cursors"],
                },
                output,
                sort_keys=True,
            )
            output.flush()
            os.fsync(output.fileno())

    def _require_owned_hold(self) -> None:
        hold = self.scheduler_packet().get("hold", {})
        expected = hashlib.sha256(f"release-attempt:{self.release.hold_key}".encode()).hexdigest()
        if (
            hold.get("enabled") is not True
            or hold.get("reason") != "release"
            or hold.get("attempt_hash") != expected
        ):
            raise ReleaseError("scheduler release hold ownership was lost")

    def _capture_unit_definitions(self) -> dict[str, str]:
        return {
            unit: self.release.run("systemctl", "cat", unit, "--no-pager", timeout=15)
            for unit in SNAPSHOT_UNITS
        }

    def _verify_neighbors(self, manager_sha: str) -> None:
        if (
            str(MANAGER_LINK.resolve()) != self.manager_link_target
            or (MANAGER_LINK / "REVISION").read_text().strip() != manager_sha
            or self.release.service_pid("autostop-manager-mcp.service") != self.manager_mcp_pid
        ):
            raise ReleaseError("Manager neighbor changed during scheduler reconciliation")
        for name, expected in self.other_container_ids.items():
            if self.release.inspect_container(name)["Id"] != expected:
                raise ReleaseError(f"non-CRM container changed: {name}")
        self._require_telegram_link()

    def _require_telegram_link(self) -> None:
        if (
            not TELEGRAM_LINK.is_symlink()
            or str(TELEGRAM_LINK.resolve()) != self.telegram_link_target
        ):
            raise ReleaseError("work Telegram release link changed")

    def _pinned_telegram_status(self) -> dict:
        return json.loads(self._call_guarded_duty("--status", timeout=30))

    def _verify_sealed_controller(self) -> None:
        source = self.pinned_telegram_duty_path
        if (
            self.guarded_controller_repo is not None
            and source
            == self.guarded_controller_repo.resolve() / "scripts/set-work-telegram-duty.sh"
        ):
            self._guarded_controller_source()
            return
        info = source.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o500
            or hashlib.sha256(source.read_bytes()).hexdigest() != self.guarded_controller_hash
        ):
            raise ReleaseError("guarded Telegram controller artifact changed")

    def _call_guarded_duty(self, operation: str, *, timeout: int = 45) -> str:
        self._verify_sealed_controller()
        return self.release.run(
            str(self.pinned_telegram_duty_path),
            operation,
            "--expected-release-dir",
            self.telegram_link_target,
            timeout=timeout,
        )

    def _check_pinned_telegram_ready(self) -> None:
        status = self._pinned_telegram_status()
        if (
            status.get("ok") is not True
            or status.get("transport_ready") is not True
            or status.get("owner_notification_configured") is not True
            or status.get("inbound_enabled") is not True
            or status.get("wake_active") is not True
            or status.get("state") != "inbound_enabled"
        ):
            raise ReleaseError("work Telegram duty is not in expected active state")
        for unit in TELEGRAM_UNITS:
            if self.release.unit_state(unit) != ("active", "enabled"):
                raise ReleaseError(f"unexpected work Telegram unit state: {unit}")
        media = self.release.run(
            "systemctl",
            "list-units",
            "--type=service",
            "--all",
            "--no-legend",
            "--no-pager",
            "autostop-work-telegram-media-*",
        )
        if any(
            len(parts := line.split()) >= 3 and parts[2] in {"active", "activating", "deactivating"}
            for line in media.splitlines()
        ):
            raise ReleaseError("work Telegram media worker is active")

    def _offline_owned_hold(self) -> bool:
        connection = sqlite3.connect(f"file:{AUTOMATION_DB}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT hold_enabled, hold_reason, hold_attempt_hash "
                "FROM manager_automation_controller_runtime WHERE singleton = 1"
            ).fetchone()
        finally:
            connection.close()
        expected = hashlib.sha256(f"release-attempt:{self.release.hold_key}".encode()).hexdigest()
        return bool(row and row == (1, "release", expected))

    def _stop_scheduler_fail_closed(self) -> None:
        self.release.run("systemctl", "stop", SCHEDULER_UNIT, timeout=45)
        if self.release.unit_state(SCHEDULER_UNIT)[0] != "inactive":
            raise ReleaseError("scheduler stop could not be verified")

    def _fail_closed_after_open(self) -> None:
        recovery_errors = []
        try:
            self._stop_scheduler_fail_closed()
        except Exception as stop_error:
            recovery_errors.append(type(stop_error).__name__)
        try:
            self._require_telegram_link()
            self._call_guarded_duty("--disable")
            if self._pinned_telegram_status().get("inbound_enabled") is not False:
                raise ReleaseError("work Telegram duty pause was not verified")
        except Exception as duty_error:
            recovery_errors.append(type(duty_error).__name__)
        if recovery_errors:
            raise ReleaseError(
                "fail-closed recovery incomplete (" + ", ".join(recovery_errors) + ")"
            )

    def _recover_hold_or_stop(self) -> None:
        try:
            self._require_owned_hold()
            return
        except Exception:
            pass
        try:
            self.release.reacquire_hold_for_rollback()
            self._require_owned_hold()
            return
        except Exception:
            self._stop_scheduler_fail_closed()
            if not self._offline_owned_hold():
                raise ReleaseError("scheduler stopped after hold loss; manual recovery required")

    def _restart_and_verify(self, *, expected_sha: str, old_pid: str | None, baseline: dict) -> str:
        try:
            self._require_owned_hold()
        except Exception:
            if (
                not self._offline_owned_hold()
                or self.release.unit_state(SCHEDULER_UNIT)[0] != "inactive"
            ):
                raise
        self.release.run("systemctl", "restart", SCHEDULER_UNIT, timeout=45)
        for _ in range(20):
            try:
                pid = self.release.service_pid(SCHEDULER_UNIT)
                packet = self.scheduler_packet()
                if (old_pid is None or pid != old_pid) and packet.get(
                    "crm_revision"
                ) == expected_sha:
                    break
            except (ReleaseError, OSError, ValueError):
                pass
            time.sleep(0.5)
        else:
            raise ReleaseError("scheduler did not restart on expected CRM SHA")
        self._require_owned_hold()
        if (
            self._process_identity(pid, CRM_KEY) != expected_sha.encode()
            or packet.get("manager_revision") != baseline.get("manager_revision")
            or packet.get("crm_version") != baseline.get("crm_version")
            or packet.get("timers") != baseline.get("timers")
            or packet.get("dependencies", {}).get("crm_change_feed") != "ready"
            or packet.get("outbox") != baseline.get("outbox")
            or packet.get("runs") != baseline.get("runs")
            or packet.get("cursors") != baseline.get("cursors")
            or self._capture_unit_definitions() != self.unit_definitions
        ):
            raise ReleaseError("scheduler identity or timer readback differs after restart")
        self._verify_neighbors(baseline["manager_revision"])
        self._verify_feed_auth()
        return pid

    def _verify_feed_auth(self) -> None:
        self.release.run(
            sys.executable,
            str(self.release.source / "scripts/probe_manager_crm_feed_auth.py"),
            "--manager-env",
            str(MANAGER_ENV),
            timeout=30,
        )

    def apply(self) -> dict[str, str]:
        if os.geteuid() != 0:
            raise ReleaseError("scheduler identity reconciliation requires root")
        if not self.expected_telegram_release_dir:
            raise ReleaseError("approved work Telegram release target is required for apply")
        fd = os.open(self.release.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            original, baseline, old_pid = self.preflight()
            written: EnvSnapshot | None = None
            candidate: bytes | None = None
            opened = False
            duty_paused = False
            telegram_captured = False
            telegram_baseline = self.backup_dir / "telegram-effects.json"
            release_outcome_reconciled = False
            release_attempted = False
            post_open_ready_verified = False
            try:
                self.release.automation("hold")
                self._require_owned_hold()
                held_baseline = self.scheduler_packet()
                if (
                    held_baseline.get("crm_revision") != baseline.get("crm_revision")
                    or held_baseline.get("manager_revision") != baseline.get("manager_revision")
                    or held_baseline.get("timers") != baseline.get("timers")
                ):
                    raise ReleaseError("scheduler identity changed while acquiring hold")
                baseline = held_baseline
                self.unit_definitions = self._capture_unit_definitions()
                self._backup(original, baseline)
                self._require_telegram_link()
                duty_paused = True
                self._call_guarded_duty("--disable")
                if self._pinned_telegram_status().get("inbound_enabled") is not False:
                    raise ReleaseError("work Telegram inbound duty did not pause")
                self.release.effect_probe("capture-telegram-effects", baseline=telegram_baseline)
                telegram_captured = True
                candidate = updated_identity(
                    original.contents,
                    old_sha=self.expected_reported_sha,
                    new_sha=self.release.sha,
                    manager_sha=baseline["manager_revision"],
                )
                written = atomic_replace_exact(self.control_env, original, candidate)
                self._restart_and_verify(
                    expected_sha=self.release.sha, old_pid=old_pid, baseline=baseline
                )
                self._require_owned_hold()
                if secure_snapshot(self.control_env) != written:
                    raise ReleaseError("scheduler config changed before hold release")
                self._require_telegram_link()
                self.release.effect_probe("verify-telegram-effects", baseline=telegram_baseline)
                try:
                    release_attempted = True
                    self.release.automation("release-hold")
                except Exception:
                    observed = self.scheduler_packet()
                    if observed.get("hold", {}).get("enabled") is False:
                        opened = True
                    if not (
                        observed.get("hold", {}).get("enabled") is False
                        and observed.get("ready") is True
                        and observed.get("crm_revision") == self.release.sha
                    ):
                        raise ReleaseError("scheduler hold release outcome uncertain")
                    release_outcome_reconciled = True
                opened = True
                final = self.scheduler_packet()
                if final.get("ready") is not True or final.get("crm_revision") != self.release.sha:
                    raise ReleaseError("scheduler post-open readiness mismatch")
                if secure_snapshot(self.control_env) != written:
                    raise ReleaseError("scheduler config changed after hold release")
                self._require_telegram_link()
                self._call_guarded_duty("--enable")
                self._check_pinned_telegram_ready()
                for unit, expected in self.telegram_unit_states.items():
                    if self.release.unit_state(unit) != expected:
                        raise ReleaseError(f"work Telegram unit changed: {unit}")
                final = self.scheduler_packet()
                if final.get("ready") is not True or final.get("crm_revision") != self.release.sha:
                    raise ReleaseError("scheduler final readiness mismatch")
                if secure_snapshot(self.control_env) != written:
                    raise ReleaseError("scheduler config changed during duty restoration")
                self._verify_neighbors(baseline["manager_revision"])
                post_open_ready_verified = True
                duty_paused = False
                return {
                    "crm_revision": self.release.sha,
                    "previous_reported_revision": self.expected_reported_sha,
                    "manager_revision": baseline["manager_revision"],
                    "backup_dir": str(self.backup_dir),
                    "release_outcome_reconciled": str(release_outcome_reconciled).lower(),
                }
            except Exception as error:
                if opened:
                    if not post_open_ready_verified:
                        try:
                            self._fail_closed_after_open()
                        except Exception as recovery_error:
                            raise ReleaseError(
                                f"scheduler identity post-open check failed; {recovery_error}"
                            ) from error
                    raise ReleaseError(
                        f"scheduler identity applied but post-open check failed: {type(error).__name__}"
                    ) from error
                if release_attempted:
                    try:
                        observed = self.scheduler_packet()
                    except Exception as probe_error:
                        self._fail_closed_after_open()
                        raise ReleaseError(
                            "scheduler hold release outcome unknown; scheduler stopped for manual recovery"
                        ) from probe_error
                    if observed.get("hold", {}).get("enabled") is False:
                        self._fail_closed_after_open()
                        raise ReleaseError(
                            "scheduler hold already released; scheduler stopped and duty paused, "
                            "no automatic config rollback after reopening"
                        ) from error
                if candidate is not None and written is None:
                    try:
                        observed_file = secure_snapshot(self.control_env)
                    except Exception as probe_error:
                        raise ReleaseError(
                            "scheduler config write outcome unknown; hold must remain for manual recovery"
                        ) from probe_error
                    if observed_file == original:
                        pass
                    elif (
                        isinstance(error, AtomicReplaceOutcomeError)
                        and (observed_file.device, observed_file.inode)
                        == (error.device, error.inode)
                        and observed_file.contents == candidate
                    ):
                        written = observed_file
                    else:
                        raise ReleaseError(
                            "scheduler config changed outside the owned replacement; hold must remain"
                        ) from error
                try:
                    self._recover_hold_or_stop()
                    if written is not None:
                        atomic_replace_exact(self.control_env, written, original.contents)
                        self._restart_and_verify(
                            expected_sha=self.expected_reported_sha,
                            old_pid=None,
                            baseline=baseline,
                        )
                    if telegram_captured:
                        self.release.effect_probe(
                            "verify-telegram-effects", baseline=telegram_baseline
                        )
                    self.release.automation("release-hold")
                    if duty_paused:
                        self._require_telegram_link()
                        self._call_guarded_duty("--enable")
                        self._check_pinned_telegram_ready()
                        for unit, expected in self.telegram_unit_states.items():
                            if self.release.unit_state(unit) != expected:
                                raise ReleaseError(f"work Telegram unit changed: {unit}")
                        self._require_telegram_link()
                except Exception as rollback_error:
                    raise ReleaseError(
                        f"scheduler identity failed ({type(error).__name__}); rollback incomplete "
                        f"({type(rollback_error).__name__}); inspect hold before manual recovery"
                    ) from rollback_error
                raise ReleaseError(
                    f"scheduler identity failed and prior config restored: {type(error).__name__}"
                ) from error
        finally:
            os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--expected-reported-sha", required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--production-root", type=Path, default=Path("/opt/autostopcrm"))
    parser.add_argument("--guarded-controller-repo", type=Path, required=True)
    parser.add_argument("--guarded-controller-sha", required=True)
    parser.add_argument("--expected-telegram-release-dir")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-sha", default="")
    args = parser.parse_args()
    try:
        release = CrmOnlyRelease(
            source=args.source, production_root=args.production_root, sha=args.sha
        )
        reconciler = AutomationRevisionReconciler(
            release,
            expected_reported_sha=args.expected_reported_sha,
            guarded_controller_repo=args.guarded_controller_repo,
            guarded_controller_sha=args.guarded_controller_sha,
            expected_telegram_release_dir=args.expected_telegram_release_dir,
        )
        if args.preflight:
            _, packet, _ = reconciler.preflight()
            print(
                json.dumps(
                    {
                        "ready": True,
                        "installed_crm_revision": args.sha,
                        "reported_crm_revision": packet["crm_revision"],
                        "telegram_release_dir": reconciler.telegram_link_target,
                    }
                )
            )
            return 0
        if args.confirm_sha != args.sha:
            raise ReleaseError("--apply requires matching --confirm-sha")
        if not args.expected_telegram_release_dir:
            raise ReleaseError("--apply requires --expected-telegram-release-dir from preflight")
        print(json.dumps(reconciler.apply()))
        return 0
    except (ReleaseError, OSError, ValueError, sqlite3.Error) as error:
        print(
            f"Scheduler revision reconciliation failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
