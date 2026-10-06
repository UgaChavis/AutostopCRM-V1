"""Guarded CRM-only release from an exact, clean Git worktree.

The coordinated deploy.sh also replaces Manager, scheduler and Telegram code.
This entrypoint keeps their revisions pinned for a CRM-only source change.
No mutation occurs without --apply and --confirm-sha.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GATEWAY_SAFE_CHECKS = frozenset(
    {
        "anonymous_access_blocked",
        "required_tools_present",
        "unexpected_tools_absent",
        "legacy_tools_absent",
        "tool_count_exactly_24",
        "tool_count_within_budget",
        "tools_payload_within_budget",
        "bootstrap_ok",
        "bootstrap_payload_within_budget",
        "board_digest_ok",
        "board_digest_payload_within_budget",
        "search_ok",
        "entity_context_ok",
        "workflow_registry_ok",
        "store_runtime_ready",
        "store_state_read_ok",
        "store_quote_adapter_configured",
        "store_quote_full_read_enabled",
        "store_quote_draft_write_enabled",
        "store_supplier_lookup_enabled",
        "store_sourcing_read_ok",
        "store_owner_capability_contract_ready",
        "all_tools_invoked",
        "all_tool_invocations_ok",
        "synthetic_workflow_terminal",
        "change_feed_bootstrap_and_ack_ledgers_ok",
        "change_feed_replay_exact",
        "change_feed_projection_pii_free",
        "fetch_page_browser_safe_state",
        *(
            f"{name}_{suffix}"
            for name in (
                "search_web_multi",
                "fetch_page_excerpt",
                "fetch_page_browser",
                "research_drive2_cases",
                "research_part_public_evidence",
            )
            for suffix in ("discoverable", "schema_ok", "call_ok")
        ),
    }
)


def safe_gateway_failed_checks(stdout: bytes) -> tuple[str, ...]:
    """Read only fixed check names from bounded gateway JSON, never raw output."""
    if len(stdout) > 262_144:
        return ()
    try:
        payload = json.loads(stdout)
    except (UnicodeDecodeError, ValueError):
        return ()
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
        return ()
    checks = payload["checks"]
    return tuple(sorted(name for name in GATEWAY_SAFE_CHECKS if checks.get(name) is False))


ALLOWED_CHANGED_PATHS = {
    "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js",
    "tests/test_mobile_client_search_draft.py",
    "scripts/browser_smoke.py",
    "scripts/browser_smoke_core.py",
    "scripts/browser_smoke_profiles.py",
    "tests/test_contracts.py",
    "scripts/deploy_crm_only.py",
    "tests/test_deploy_crm_only.py",
    "tests/test_card_workspace_context.py",
    "tests/test_printing_hydration_overlap_browser.py",
    "scripts/reconcile_automation_crm_revision.py",
    "tests/test_reconcile_automation_crm_revision.py",
    ".github/workflows/quality.yml",
    "docs/OPERATIONS_RUNBOOK.md",
    "docs/agent/module_operations/crm_commands.md",
}
MANAGER_LINK = Path("/opt/autostop-manager-releases/current")
MANAGER_ENV = Path("/opt/AutostopManager/.crm-mcp.env")
MANAGER_DB = Path("/opt/AutostopManager/data/autostop_manager.sqlite3")
TELEGRAM_LINK = Path("/opt/autostop-work-telegram-releases/current")
TELEGRAM_DUTY = TELEGRAM_LINK / "scripts/set-work-telegram-duty.sh"
AUTOMATION_DB = Path("/var/lib/autostop-manager-scheduler/registry.sqlite3")
AUTOMATION_SOCKET = Path("/run/autostop-manager-automation/control.sock")
BACKUP_ROOT = Path("/root/autostopcrm-backups/crm-only")
TELEGRAM_STATE = Path("/var/lib/autostop-work-telegram")
TELEGRAM_RUNTIME = Path("/run/autostop-work-telegram")
TELEGRAM_UNITS = ("autostop-work-telegram.service", "autostop-codex-wake.service")
NON_CRM_CONTAINERS = ("autostop-searxng", "autostop-crawl4ai", "autostop-app", "autostop-db")


class ReleaseError(RuntimeError):
    """A release invariant failed; caller decides whether rollback is safe."""


class CommandFailure(ReleaseError):
    """A command failed; only an exit code and fixed gateway checks are retained."""

    def __init__(self, exit_code: int, failed_checks: tuple[str, ...] = ()) -> None:
        self.exit_code = exit_code
        self.failed_checks = failed_checks
        super().__init__(f"command exited {exit_code}")


class SmokeStageFailure(ReleaseError):
    """A named release gate failed; command output remains private."""

    def __init__(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        super().__init__(f"smoke stage {stage} failed{detail}")


@dataclass(frozen=True)
class Baseline:
    old_revision: str
    old_image_id: str
    old_container_id: str
    manager_revision: str
    manager_link_target: str
    telegram_link_target: str
    other_container_ids: dict[str, str]
    mounts: dict[str, str]
    manager_mcp_pid: str
    scheduler_pid: str
    telegram_unit_states: dict[str, tuple[str, str]]


class Commands:
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 60,
        input_bytes: bytes | None = None,
    ) -> str:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            # stderr may contain credentials or customer data; never echo it.
            failed_checks = (
                safe_gateway_failed_checks(result.stdout)
                if "scripts/check_agent_gateway_v2.py" in argv
                else ()
            )
            raise CommandFailure(result.returncode, failed_checks)
        return result.stdout.decode("utf-8", errors="replace").strip()


class CrmOnlyRelease:
    def __init__(
        self,
        *,
        source: Path,
        production_root: Path,
        sha: str,
        commands: Commands | None = None,
        guarded_controller_repo: Path | None = None,
        guarded_controller_sha: str | None = None,
        expected_telegram_release_dir: str | None = None,
    ) -> None:
        if not SHA_RE.fullmatch(sha):
            raise ReleaseError("--sha must be an exact 40-character lowercase commit SHA")
        self.source = source.resolve()
        self.production_root = production_root.resolve()
        self.sha = sha
        self.commands = commands or Commands()
        self.guarded_controller_repo = guarded_controller_repo
        self.guarded_controller_sha = guarded_controller_sha
        self.expected_telegram_release_dir = expected_telegram_release_dir
        self.guarded_controller_hash = ""
        self.guarded_controller_bytes = b""
        self.sealed_guarded_controller: Path | None = None
        self.telegram_link_target = ""
        self.image_tag = f"autostopcrm:crm-only-{sha[:12]}"
        self.release_id = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{sha[:12]}-{os.getpid()}"
        )
        self.marker = self.production_root / "data/.agent-gateway-maintenance"
        self.lock_path = self.production_root / ".autostop-deploy.lock"
        self.backup_dir = BACKUP_ROOT / self.release_id
        self.hold_key = f"crm-only:{self.release_id}"
        self.rollback_hold_key = f"{self.hold_key}:rollback"
        self._rehold_count = 0
        self._maintenance_started: float | None = None
        self._rollback_active = False

    def run(
        self,
        *argv: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 60,
        input_bytes: bytes | None = None,
    ) -> str:
        if self._maintenance_started is not None:
            remaining = 600 - int(time.monotonic() - self._maintenance_started)
            reserve = 5 if self._rollback_active else 120
            if remaining <= reserve:
                raise ReleaseError("maintenance budget exhausted")
            timeout = min(timeout, remaining - reserve)
        return self.commands.run(
            list(argv), cwd=cwd, env=env, timeout=timeout, input_bytes=input_bytes
        )

    def git(self, *argv: str) -> str:
        return self.run("git", "-C", str(self.source), *argv)

    def compose_env(self, image: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["AUTOSTOP_DATA_DIR"] = str(self.production_root / "data")
        env["AUTOSTOP_MANAGER_DATA_HOST_DIR"] = "/opt/AutostopManager/data"
        env["AUTOSTOP_MANAGER_HOST_DIR"] = str(MANAGER_LINK)
        if image:
            env["AUTOSTOP_RELEASE_IMAGE"] = image
        return env

    def compose(self, *argv: str, image: str | None = None, timeout: int = 60) -> str:
        return self.run(
            "docker",
            "compose",
            "-p",
            "autostopcrm",
            "--project-directory",
            str(self.production_root),
            "--env-file",
            str(self.production_root / ".env"),
            "-f",
            str(self.production_root / "docker-compose.yml"),
            *argv,
            cwd=self.production_root,
            env=self.compose_env(image),
            timeout=timeout,
        )

    def inspect_container(self, name: str) -> dict:
        value = self.run("docker", "inspect", name)
        records = json.loads(value)
        if len(records) != 1:
            raise ReleaseError(f"unexpected Docker inspect result for {name}")
        return records[0]

    def service_pid(self, name: str) -> str:
        pid = self.run("systemctl", "show", "-p", "MainPID", "--value", name)
        if not pid.isdecimal() or int(pid) < 1:
            raise ReleaseError(f"service is not running: {name}")
        return pid

    def unit_state(self, name: str) -> tuple[str, str]:
        return (
            self.run("systemctl", "show", "-p", "ActiveState", "--value", name),
            self.run("systemctl", "show", "-p", "UnitFileState", "--value", name),
        )

    def check_telegram_ready(self) -> None:
        status = self.telegram_status()
        if (
            status.get("ok") is not True
            or status.get("transport_ready") is not True
            or status.get("owner_notification_configured") is not True
            or status.get("inbound_enabled") is not True
            or status.get("wake_active") is not True
            or status.get("state") != "inbound_enabled"
        ):
            raise ReleaseError("work Telegram duty is not in the expected active state")
        for unit in TELEGRAM_UNITS:
            if self.unit_state(unit) != ("active", "enabled"):
                raise ReleaseError(f"unexpected Telegram unit state: {unit}")
        media = self.run(
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

    def check_store_network(self) -> None:
        network = json.loads(self.run("docker", "network", "inspect", "autostop-store-agent"))
        if len(network) != 1 or network[0].get("Internal") is not True:
            raise ReleaseError("Store agent network is missing or not internal")
        members = {
            container.get("Name", "") for container in network[0].get("Containers", {}).values()
        }
        if not {"autostop-app", "autostopcrm"} <= members:
            raise ReleaseError("CRM or Store is detached from the private network")
        if members - {"autostop-app", "autostopcrm"}:
            raise ReleaseError("unexpected container on Store agent network")

    @staticmethod
    def mounts_of(record: dict) -> dict[str, str]:
        return {item["Destination"]: item["Source"] for item in record.get("Mounts", [])}

    def preflight(self) -> Baseline:
        if self.source == self.production_root:
            raise ReleaseError("release source must be an isolated worktree")
        if self.git("status", "--porcelain=v1", "--untracked-files=all"):
            raise ReleaseError("release source worktree is dirty")
        if self.git("rev-parse", "HEAD") != self.sha:
            raise ReleaseError("worktree HEAD does not match --sha")
        remote = self.git("ls-remote", "origin", "refs/heads/autostopcrm-v1").split()
        if not remote or remote[0] != self.sha:
            raise ReleaseError("GitHub production branch does not match --sha")
        self._guarded_controller_source()
        candidate_compose = (self.source / "docker-compose.yml").read_bytes()
        active_compose = (self.production_root / "docker-compose.yml").read_bytes()
        if hashlib.sha256(candidate_compose).digest() != hashlib.sha256(active_compose).digest():
            raise ReleaseError("candidate and active Compose bytes differ")
        record = self.inspect_container("autostopcrm")
        labels = record.get("Config", {}).get("Labels", {})
        if labels.get("com.docker.compose.project") != "autostopcrm":
            raise ReleaseError("unexpected production Compose project")
        if labels.get("com.docker.compose.project.config_files") != str(
            self.production_root / "docker-compose.yml"
        ):
            raise ReleaseError("unexpected production Compose config path")
        old_revision = labels.get("org.opencontainers.image.revision", "")
        if not SHA_RE.fullmatch(old_revision):
            raise ReleaseError("installed CRM revision label is missing")
        self.git("merge-base", "--is-ancestor", old_revision, self.sha)
        changed = set(self.git("diff", "--name-only", old_revision, self.sha).splitlines())
        if not changed or not changed <= ALLOWED_CHANGED_PATHS:
            raise ReleaseError("candidate includes unreviewed paths")
        mounts = self.mounts_of(record)
        required_mounts = {
            "/home/autostop/.minimal-kanban": str(self.production_root / "data"),
            "/opt/AutostopManager": str(MANAGER_LINK),
            "/opt/AutostopManager/data": "/opt/AutostopManager/data",
            "/run/autostop-manager-automation": "/run/autostop-manager-automation",
        }
        if any(mounts.get(target) != source for target, source in required_mounts.items()):
            raise ReleaseError("production CRM mount contract changed")
        if record.get("State", {}).get("Health", {}).get("Status") != "healthy":
            raise ReleaseError("production CRM is not healthy before release")
        for path in (
            self.production_root / ".env",
            self.production_root / "data",
            MANAGER_ENV,
            MANAGER_DB,
            AUTOMATION_DB,
            AUTOMATION_SOCKET,
            TELEGRAM_DUTY,
            self.production_root / "data/change_feed.sqlite3",
        ):
            if not path.exists() or path.is_symlink():
                raise ReleaseError(f"required production path unavailable: {path.name}")
        if not MANAGER_LINK.is_symlink() or not TELEGRAM_LINK.is_symlink():
            raise ReleaseError("Manager or Telegram release link missing")
        self.telegram_link_target = str(TELEGRAM_LINK.resolve())
        if (
            self.expected_telegram_release_dir is not None
            and self.telegram_link_target != self.expected_telegram_release_dir
        ):
            raise ReleaseError("work Telegram release differs from approved target")
        manager_revision = (MANAGER_LINK / "REVISION").read_text().strip()
        if not SHA_RE.fullmatch(manager_revision):
            raise ReleaseError("installed Manager revision is invalid")
        self.run(
            sys.executable,
            str(self.source / "scripts/check_automotive_tool_catalog.py"),
            "--expected-source-revision",
            manager_revision,
            "--manager-root",
            str(MANAGER_LINK.resolve()),
            "--manager-python",
            str(MANAGER_ENV.parent / ".venv/bin/python"),
        )
        if self.marker.exists():
            raise ReleaseError("production maintenance marker already exists")
        if shutil.disk_usage(self.production_root).free < 3 * 1024**3:
            raise ReleaseError("less than 3 GiB free before image build and backup")
        self.check_store_network()
        self.compose("config", "--quiet")
        python = sys.executable
        self.run(
            python,
            str(self.source / "scripts/configure_mcp_oauth.py"),
            "--env-file",
            str(self.production_root / ".env"),
            "check",
        )
        self.run(
            python,
            str(self.source / "scripts/configure_codex_mcp_auth.py"),
            "--server-env",
            str(self.production_root / ".env"),
            "check",
        )
        self.run(
            python,
            str(self.source / "scripts/probe_manager_crm_feed_auth.py"),
            "--manager-env",
            str(MANAGER_ENV),
        )
        stable_id = json.loads(
            self.run("docker", "image", "inspect", "autostopcrm-autostopcrm:latest")
        )[0]["Id"]
        if stable_id != record["Image"]:
            raise ReleaseError("stable tag differs from running CRM image")
        self.check_telegram_ready()
        if self.automation_status()["enabled"] is not False:
            raise ReleaseError("scheduler already has a global hold")
        other_ids = {name: self.inspect_container(name)["Id"] for name in NON_CRM_CONTAINERS}
        return Baseline(
            old_revision=old_revision,
            old_image_id=record["Image"],
            old_container_id=record["Id"],
            manager_revision=manager_revision,
            manager_link_target=str(MANAGER_LINK.resolve()),
            telegram_link_target=self.telegram_link_target,
            other_container_ids=other_ids,
            mounts=mounts,
            manager_mcp_pid=self.service_pid("autostop-manager-mcp.service"),
            scheduler_pid=self.service_pid("autostop-manager-scheduler.service"),
            telegram_unit_states={unit: self.unit_state(unit) for unit in TELEGRAM_UNITS},
        )

    def build_candidate(self) -> str:
        # Commands.run returns text for normal probes. A tar stream is binary,
        # so collect it directly and never write a working-tree copy of secrets.
        archive_result = subprocess.run(
            ["git", "-C", str(self.source), "archive", "--format=tar", self.sha],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if archive_result.returncode:
            raise ReleaseError("git archive of exact candidate failed")
        self.run(
            "docker",
            "build",
            "--label",
            f"org.opencontainers.image.revision={self.sha}",
            "--tag",
            self.image_tag,
            "-",
            timeout=1800,
            input_bytes=archive_result.stdout,
        )
        image = json.loads(self.run("docker", "image", "inspect", self.image_tag))
        if (
            len(image) != 1
            or image[0].get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
            != self.sha
        ):
            raise ReleaseError("candidate image revision label mismatch")
        return image[0]["Id"]

    def automation_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(MANAGER_LINK),
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "AUTOSTOP_MANAGER_ENV_FILE": "/dev/null",
                "AUTOSTOP_AUTOMATION_DB": str(AUTOMATION_DB),
                "AUTOSTOP_AUTOMATION_CONTROL_SOCKET": str(AUTOMATION_SOCKET),
            }
        )
        return env

    def automation_status(self) -> dict:
        # The installed Manager owns this socket. Return only hold metadata.
        script = (
            "import json; from autostop_manager.automation_release import _socket_client,_client_status; "
            "from autostop_manager.config import get_automation_control_socket_path,load_runtime_env; "
            "load_runtime_env(); s=_client_status(_socket_client(get_automation_control_socket_path())); "
            "h=s['global_hold']; print(json.dumps({'enabled':h['enabled'],'reason':h.get('reason'),"
            "'attempt_hash':h.get('attempt_hash')}))"
        )
        value = json.loads(
            self.run(
                "/opt/AutostopManager/.venv/bin/python",
                "-c",
                script,
                env=self.automation_env(),
                timeout=20,
            )
        )
        if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
            raise ReleaseError("scheduler hold status invalid")
        return value

    def automation(self, operation: str) -> None:
        output = self.run(
            "/opt/AutostopManager/.venv/bin/python",
            "-m",
            "autostop_manager.automation_release",
            operation,
            "--release-attempt-key",
            self.hold_key,
            env=self.automation_env(),
            timeout=50,
        )
        if operation == "hold":
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="crm-only-hold-", dir="/root"
            ) as handle:
                handle.write(output)
                handle.flush()
                self.run(
                    sys.executable,
                    str(self.source / "scripts/check_automation_center_release.py"),
                    "hold",
                    "--input",
                    handle.name,
                    "--release-attempt-key",
                    self.hold_key,
                    timeout=20,
                )

    def reacquire_hold_for_rollback(self) -> None:
        status = self.automation_status()
        original_hash = hashlib.sha256(f"release-attempt:{self.hold_key}".encode()).hexdigest()
        if status["enabled"] is True:
            if status.get("reason") != "release" or status.get("attempt_hash") != original_hash:
                raise ReleaseError("scheduler hold ownership changed before rollback")
            self.automation("hold")
            return
        self._rehold_count += 1
        self.hold_key = f"{self.rollback_hold_key}:{self._rehold_count}"
        self.automation("hold")

    def _guarded_controller_source(self) -> Path:
        repo = self.guarded_controller_repo
        sha = self.guarded_controller_sha
        if repo is None or sha is None or not SHA_RE.fullmatch(sha):
            raise ReleaseError("exact guarded Telegram controller source is required")
        repo = repo.resolve()
        source = repo / "scripts/set-work-telegram-duty.sh"
        if not source.is_file() or source.is_symlink():
            raise ReleaseError("guarded Telegram controller source is missing or unsafe")
        if self.run("git", "-C", str(repo), "rev-parse", "HEAD") != sha:
            raise ReleaseError("guarded Telegram controller checkout differs from target SHA")
        if self.run("git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"):
            raise ReleaseError("guarded Telegram controller checkout is dirty")
        remote = self.run(
            "git", "-C", str(repo), "ls-remote", "origin", "refs/heads/AutostopManager"
        ).split()
        if not remote or remote[0] != sha:
            raise ReleaseError("published Manager branch differs from guarded controller SHA")
        tracked_hash = self.run(
            "git", "-C", str(repo), "rev-parse", f"{sha}:scripts/set-work-telegram-duty.sh"
        )
        source_bytes = source.read_bytes()
        if tracked_hash != self.run(
            "git", "-C", str(repo), "hash-object", "--stdin", input_bytes=source_bytes
        ):
            raise ReleaseError("guarded Telegram controller differs from published blob")
        self.guarded_controller_bytes = source_bytes
        self.guarded_controller_hash = hashlib.sha256(source_bytes).hexdigest()
        return source

    def _seal_guarded_controller(self, effects_dir: Path) -> None:
        self._guarded_controller_source()
        sealed = effects_dir / "guarded-telegram-duty.sh"
        descriptor = os.open(sealed, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o500)
        with os.fdopen(descriptor, "wb") as output:
            output.write(self.guarded_controller_bytes)
            output.flush()
            os.fsync(output.fileno())
        self.sealed_guarded_controller = sealed
        self._verify_sealed_guarded_controller()

    def _verify_sealed_guarded_controller(self) -> None:
        sealed = self.sealed_guarded_controller
        if sealed is None:
            raise ReleaseError("guarded Telegram controller is not sealed")
        info = sealed.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o500
            or hashlib.sha256(sealed.read_bytes()).hexdigest() != self.guarded_controller_hash
        ):
            raise ReleaseError("guarded Telegram controller artifact changed")

    def _call_guarded_duty(self, operation: str, *, timeout: int = 45) -> str:
        self._verify_sealed_guarded_controller()
        return self.run(
            str(self.sealed_guarded_controller),
            operation,
            "--expected-release-dir",
            self.telegram_link_target,
            timeout=timeout,
        )

    def telegram_status(self) -> dict:
        if self.sealed_guarded_controller is None:
            self._guarded_controller_source()
            with tempfile.TemporaryDirectory(prefix="crm-duty-status-") as directory:
                pinned = Path(directory) / "guarded-telegram-duty.sh"
                descriptor = os.open(pinned, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o500)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(self.guarded_controller_bytes)
                    output.flush()
                    os.fsync(output.fileno())
                return json.loads(
                    self.run(
                        str(pinned),
                        "--status",
                        "--expected-release-dir",
                        self.telegram_link_target,
                        timeout=30,
                    )
                )
        return json.loads(self._call_guarded_duty("--status", timeout=30))

    def check_unchanged_neighbors(self, baseline: Baseline) -> None:
        if (MANAGER_LINK / "REVISION").read_text().strip() != baseline.manager_revision:
            raise ReleaseError("Manager revision changed during CRM-only release")
        if str(MANAGER_LINK.resolve()) != baseline.manager_link_target:
            raise ReleaseError("Manager release link changed")
        if str(TELEGRAM_LINK.resolve()) != baseline.telegram_link_target:
            raise ReleaseError("Telegram release link changed")
        for name, previous_id in baseline.other_container_ids.items():
            if self.inspect_container(name)["Id"] != previous_id:
                raise ReleaseError(f"non-CRM container changed: {name}")
        if self.service_pid("autostop-manager-mcp.service") != baseline.manager_mcp_pid:
            raise ReleaseError("Manager MCP process changed")
        if self.service_pid("autostop-manager-scheduler.service") != baseline.scheduler_pid:
            raise ReleaseError("scheduler process changed")

    def telegram_target_unchanged(self, baseline: Baseline) -> bool:
        return (
            TELEGRAM_LINK.is_symlink()
            and str(TELEGRAM_LINK.resolve()) == baseline.telegram_link_target
        )

    def check_telegram_units(self, baseline: Baseline) -> None:
        for unit, expected in baseline.telegram_unit_states.items():
            if self.unit_state(unit) != expected:
                raise ReleaseError(f"Telegram unit state changed: {unit}")

    def effect_probe(self, operation: str, *, baseline: Path) -> None:
        argv = [
            sys.executable,
            str(self.source / "scripts/check_automation_center_release.py"),
            operation,
        ]
        if operation.endswith("feed"):
            argv.extend(["--database", str(self.production_root / "data/change_feed.sqlite3")])
        else:
            argv.extend(
                ["--state-dir", str(TELEGRAM_STATE), "--runtime-dir", str(TELEGRAM_RUNTIME)]
            )
        argv.extend(
            ["--output" if operation.startswith("capture") else "--baseline", str(baseline)]
        )
        self.run(*argv, timeout=30)

    def wait_health(self, image_id: str, baseline: Baseline) -> None:
        for _ in range(20):
            record = self.inspect_container("autostopcrm")
            healthy = record.get("State", {}).get("Health", {}).get("Status") == "healthy"
            if record.get("Image") == image_id and healthy:
                if self.mounts_of(record) != baseline.mounts:
                    raise ReleaseError("CRM mounts changed after replacement")
                return
            time.sleep(3)
        raise ReleaseError("CRM container did not become healthy on expected image")

    def _smoke_run(self, stage: str, *argv: str, compose: bool = False, timeout: int = 60) -> str:
        try:
            if compose:
                return self.compose(*argv, timeout=timeout)
            return self.run(*argv, timeout=timeout)
        except Exception as exc:
            # Subprocess output can include live records or credentials. Only
            # fixed gate names, exit codes and allowlisted checks are reported.
            if isinstance(exc, CommandFailure):
                detail = f" (exit {exc.exit_code})"
                if exc.failed_checks:
                    detail += "; checks=" + ",".join(exc.failed_checks)
            elif isinstance(exc, subprocess.TimeoutExpired):
                detail = " (timeout)"
            elif isinstance(exc, ReleaseError) and str(exc) == "maintenance budget exhausted":
                detail = " (maintenance budget exhausted)"
            else:
                detail = f" ({type(exc).__name__})"
            raise SmokeStageFailure(stage, detail) from None

    def smoke(self, *, revision: str) -> None:
        # A GET-only public auth guard remains safe while domain writes are held.
        self._smoke_run(
            "public-auth",
            sys.executable,
            "-c",
            "import urllib.request,urllib.error; "
            "u='https://crm.autostopcrm.ru/api/get_card'; "
            "r=urllib.request.Request(u,method='GET'); "
            "status=0; "
            "\ntry: urllib.request.urlopen(r,timeout=10)\n"
            "except urllib.error.HTTPError as e: status=e.code\n"
            "assert status==401, f'anonymous read returned {status}'",
            timeout=20,
        )
        self._smoke_run(
            "live-connector",
            "exec",
            "-T",
            "-e",
            "AUTOSTOP_SMOKE_OPERATOR_USERNAME=",
            "-e",
            "AUTOSTOP_SMOKE_OPERATOR_PASSWORD=",
            "autostopcrm",
            "python",
            "scripts/check_live_connector.py",
            "--strict",
            "--site-url",
            "https://crm.autostopcrm.ru",
            "--expect-https",
            "--skip-mcp",
            "--skip-public-write-protection",
            "--local-api-url",
            "http://127.0.0.1:41731",
            compose=True,
            timeout=60,
        )
        self._smoke_run(
            "agent-gateway",
            "exec",
            "-T",
            "autostopcrm",
            "python",
            "scripts/check_agent_gateway_v2.py",
            "--mcp-url",
            "https://crm.autostopcrm.ru/mcp",
            "--require-store",
            "--require-web",
            compose=True,
            timeout=90,
        )
        # OAuth metadata is a GET; the full OAuth smoke registers a client and
        # writes token state, which cannot be rolled back safely in this window.
        self._smoke_run(
            "oauth-metadata",
            sys.executable,
            "-c",
            "import json,urllib.request; "
            "u='https://crm.autostopcrm.ru/.well-known/oauth-authorization-server'; "
            "d=json.load(urllib.request.urlopen(u,timeout=10)); "
            "assert 'S256' in d.get('code_challenge_methods_supported',[]); "
            "assert 'refresh_token' in d.get('grant_types_supported',[])",
            timeout=20,
        )
        self._smoke_run(
            "manager-feed-auth",
            sys.executable,
            str(self.source / "scripts/probe_manager_crm_feed_auth.py"),
            "--manager-env",
            str(MANAGER_ENV),
            timeout=30,
        )

    def verify_stopped(self) -> None:
        record = self.inspect_container("autostopcrm")
        if record.get("State", {}).get("Running") is not False:
            raise ReleaseError("CRM container is not stopped; refusing data restore")

    def backup(self) -> None:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.run(
            sys.executable,
            str(self.source / "scripts/agent_release_backup.py"),
            "create",
            "--output-root",
            str(BACKUP_ROOT),
            "--crm-data-dir",
            str(self.production_root / "data"),
            "--manager-db",
            str(MANAGER_DB),
            "--backup-id",
            self.release_id,
            timeout=90,
        )
        self.run(
            sys.executable,
            str(self.source / "scripts/agent_release_backup.py"),
            "verify",
            "--backup-dir",
            str(self.backup_dir),
            timeout=45,
        )

    def restore_crm_only(self) -> None:
        self.verify_stopped()
        self.run(
            sys.executable,
            str(self.source / "scripts/agent_release_backup.py"),
            "verify",
            "--backup-dir",
            str(self.backup_dir),
            timeout=45,
        )
        self.run(
            sys.executable,
            str(self.source / "scripts/agent_release_backup.py"),
            "restore-crm-changed",
            "--backup-dir",
            str(self.backup_dir),
            timeout=90,
        )

    def apply(self) -> dict[str, str]:
        if os.geteuid() != 0:
            raise ReleaseError("CRM-only release requires root")
        if not self.expected_telegram_release_dir:
            raise ReleaseError("approved work Telegram release target is required for apply")
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReleaseError("another CRM deployment owns the shared lock") from exc
            baseline = self.preflight()
            candidate_id = self.build_candidate()
            rollback_tag = f"autostopcrm-rollback:{self.release_id}"
            self.run("docker", "image", "tag", baseline.old_image_id, rollback_tag)
            self.check_telegram_ready()
            marker_created = False
            hold_acquired = False
            hold_attempted = False
            duty_paused = False
            crm_stopped = False
            backup_verified = False
            opened = False
            hold_may_be_released = False
            effects_dir = self.backup_dir.parent / f"{self.release_id}-effects"
            effects_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            self._seal_guarded_controller(effects_dir)
            feed_baseline = effects_dir / "feed.json"
            telegram_baseline = effects_dir / "telegram.json"
            feed_captured = False
            telegram_captured = False
            try:
                self._maintenance_started = time.monotonic()
                with self.marker.open("x") as marker:
                    marker_created = True
                    marker.write(self.release_id + "\n")
                self.marker.chmod(0o600)
                hold_attempted = True
                self.automation("hold")
                hold_acquired = True
                duty_paused = True
                self._call_guarded_duty("--disable")
                if self.telegram_status().get("inbound_enabled") is not False:
                    raise ReleaseError("Telegram inbound duty did not pause")
                self.effect_probe("capture-telegram-effects", baseline=telegram_baseline)
                telegram_captured = True
                crm_stopped = True
                self.compose("stop", "--timeout", "20", "autostopcrm", timeout=35)
                self.verify_stopped()
                self.backup()
                backup_verified = True
                self.effect_probe("capture-feed", baseline=feed_baseline)
                feed_captured = True
                self.compose(
                    "up",
                    "-d",
                    "--no-deps",
                    "--no-build",
                    "--force-recreate",
                    "autostopcrm",
                    image=self.image_tag,
                    timeout=90,
                )
                self.wait_health(candidate_id, baseline)
                self.check_store_network()
                self.smoke(revision=self.sha)
                self.effect_probe("verify-feed", baseline=feed_baseline)
                self.effect_probe("verify-telegram-effects", baseline=telegram_baseline)
                self.check_unchanged_neighbors(baseline)
                self.run("docker", "image", "tag", candidate_id, "autostopcrm-autostopcrm:latest")
                hold_may_be_released = True
                self.automation("release-hold")
                hold_acquired = False
                self.marker.unlink()
                marker_created = False
                opened = True
                self._call_guarded_duty("--enable")
                duty_paused = False
                self.check_telegram_ready()
                self.check_telegram_units(baseline)
                self.check_unchanged_neighbors(baseline)
                return {
                    "release_id": self.release_id,
                    "crm_revision": self.sha,
                    "manager_revision": baseline.manager_revision,
                    "previous_crm_revision": baseline.old_revision,
                    "backup_dir": str(self.backup_dir),
                    "rollback_image": rollback_tag,
                }
            except Exception as error:
                if opened:
                    raise ReleaseError(
                        f"release opened but post-open check failed: {type(error).__name__}; "
                        "automatic data rollback forbidden after writes may resume"
                    ) from error
                if (
                    marker_created
                    and hold_acquired
                    and not self.telegram_target_unchanged(baseline)
                ):
                    raise ReleaseError(
                        "work Telegram release switched during CRM update; maintenance marker "
                        "and scheduler hold retained for manual recovery"
                    ) from error
                if hold_attempted and not hold_acquired:
                    try:
                        status = self.automation_status()
                    except Exception as exc:
                        raise ReleaseError(
                            "scheduler hold outcome unknown; maintenance marker retained"
                        ) from exc
                    expected_hash = hashlib.sha256(
                        f"release-attempt:{self.hold_key}".encode()
                    ).hexdigest()
                    hold_acquired = (
                        status["enabled"] is True
                        and status.get("reason") == "release"
                        and status.get("attempt_hash") == expected_hash
                    )
                failure_label = (
                    str(error) if isinstance(error, SmokeStageFailure) else type(error).__name__
                )
                rollback_error: Exception | None = None
                rollback_release_attempted = False
                rollback_reopened = False
                try:
                    self._rollback_active = True
                    if hold_may_be_released:
                        # release-hold may have applied even if its response was
                        # lost. Reacquire and prove quiescence before any restore.
                        self.reacquire_hold_for_rollback()
                        hold_acquired = True
                    if crm_stopped:
                        self.compose("stop", "--timeout", "20", "autostopcrm", timeout=35)
                        self.verify_stopped()
                        if backup_verified:
                            self.restore_crm_only()
                        self.compose(
                            "up",
                            "-d",
                            "--no-deps",
                            "--no-build",
                            "--force-recreate",
                            "autostopcrm",
                            image=rollback_tag,
                            timeout=90,
                        )
                        self.wait_health(baseline.old_image_id, baseline)
                        self.check_store_network()
                        self.smoke(revision=baseline.old_revision)
                        if feed_captured:
                            self.effect_probe("verify-feed", baseline=feed_baseline)
                    if telegram_captured:
                        self.effect_probe("verify-telegram-effects", baseline=telegram_baseline)
                    self.run(
                        "docker",
                        "image",
                        "tag",
                        baseline.old_image_id,
                        "autostopcrm-autostopcrm:latest",
                    )
                    self.check_unchanged_neighbors(baseline)
                    if hold_acquired:
                        rollback_release_attempted = True
                        self.automation("release-hold")
                        hold_acquired = False
                    if marker_created:
                        self.marker.unlink()
                        marker_created = False
                        rollback_reopened = True
                    if duty_paused:
                        self._call_guarded_duty("--enable")
                        duty_paused = False
                        self.check_telegram_ready()
                        self.check_telegram_units(baseline)
                except Exception as exc:
                    if rollback_reopened:
                        raise ReleaseError(
                            "previous CRM restored and reopened; post-open Telegram recovery failed; "
                            "automatic data rollback forbidden"
                        ) from exc
                    rollback_error = exc
                    if rollback_release_attempted and marker_created:
                        try:
                            self.reacquire_hold_for_rollback()
                        except Exception:
                            pass
                if rollback_error:
                    raise ReleaseError(
                        f"candidate failed ({failure_label}); rollback incomplete "
                        f"({rollback_error if isinstance(rollback_error, SmokeStageFailure) else type(rollback_error).__name__}); "
                        "inspect maintenance marker and scheduler hold before recovery"
                    ) from rollback_error
                raise ReleaseError(
                    f"candidate failed and prior CRM restored: {failure_label}"
                ) from error
        finally:
            os.close(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
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
            source=args.source,
            production_root=args.production_root,
            sha=args.sha,
            guarded_controller_repo=args.guarded_controller_repo,
            guarded_controller_sha=args.guarded_controller_sha,
            expected_telegram_release_dir=args.expected_telegram_release_dir,
        )
        if args.preflight:
            baseline = release.preflight()
            print(
                json.dumps(
                    {
                        "ready": True,
                        "candidate_revision": args.sha,
                        "installed_crm_revision": baseline.old_revision,
                        "installed_manager_revision": baseline.manager_revision,
                        "telegram_release_dir": baseline.telegram_link_target,
                        "compose_unchanged": True,
                        "source_clean": True,
                    }
                )
            )
            return 0
        if args.confirm_sha != args.sha:
            raise ReleaseError("--apply requires matching --confirm-sha")
        if not args.expected_telegram_release_dir:
            raise ReleaseError("--apply requires --expected-telegram-release-dir from preflight")
        print(json.dumps(release.apply()))
        return 0
    except (ReleaseError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"CRM-only release failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
