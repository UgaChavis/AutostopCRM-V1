"""Fixed host operations for a CRM successor; no native configuration edits."""

from __future__ import annotations

import datetime
import math
import os
import re
import socket
import stat
import struct
import subprocess
import time
import uuid
from pathlib import Path
from types import ModuleType

from scripts.j1_forward_io import (
    canonical,
    document,
    read_protected,
    reference,
    referenced,
    require,
    sha,
    write_new,
)

CRM = Path("/opt/autostopcrm")
DOCKER = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]
MANAGER_PYTHON = "/opt/AutostopManager/.venv/bin/python"
MANAGER_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
BASE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "LD_LIBRARY_PATH": "/opt/autostop-sqlite/3.51.3/lib",
}
RPC_PATH = Path("/usr/local/libexec/autostop-j1-kernel-bootstrap/rpc.py")
RPC_SHA = "66924e6477ed0bc6bb7d9a6f13745ed0d017508669de94c7bfc090a261efc6b7"
WAKE_PATH = Path("/etc/autostop-work-telegram/wake.json")
WAKE_SHA = "bbc329c4a1b8ea3fd2b11de262445b525bb8820489f2069dc6e7e7b82ca94c0d"
WORK_UNIT = "autostop-work-telegram.service"
WORK_SOCKET = "/run/autostop-work-telegram/bridge.sock"
WAKE_UNIT = "autostop-codex-wake.service"
WAKE_SOCKET = "/run/autostop-codex-wake/wake.sock"
APPSERVER_EXE = (
    "/root/.codex/packages/standalone/releases/0.163.0-alpha.11-x86_64-unknown-linux-musl/bin/codex"
)
# The immutable accepted-warm receipt also contains an SSH process and a Codex
# CLI process. Only this exact appserver argv is the metadata socket daemon.
APPSERVER_ARGV_SHA = "9383275e42881531784824264afe133ba8a005572197385171f2cf9be72897a1"
PERFORMANCE_URL = "http://127.0.0.1:41731"
PERFORMANCE_ITERATIONS = 20
PERFORMANCE_WARMUPS = 2
PERFORMANCE_LIMITS = (
    ("--max-snapshot-gzip-ms", "snapshot.gzip", "p95_ms", 800),
    ("--max-snapshot-gzip-bytes", "snapshot.gzip", "bytes", 80000),
    ("--max-revision-ms", "revision", "p95_ms", 500),
    ("--max-revision-server-ms", "revision", "server_timing_metrics.app.p95_ms", 20),
    ("--max-get-card-ms", "get_card", "p95_ms", 150),
)


def performance_report(report: dict) -> dict:
    """Adapt perf_probe's measured result after the command exited successfully."""
    require(
        type(report) is dict
        and "ok" not in report
        and report.get("base_url") == PERFORMANCE_URL
        and report.get("local_temp_server") is False
        and type(report.get("iterations")) is int
        and report["iterations"] == PERFORMANCE_ITERATIONS
        and type(report.get("warmup_iterations")) is int
        and report["warmup_iterations"] == PERFORMANCE_WARMUPS
        and report.get("threshold_status") == "passed"
        and report.get("violations") == [],
        "runtime_probe_failed_performance",
    )
    rows = report.get("rows")
    labels = {"snapshot.identity", "snapshot.gzip", "revision", "get_card"}
    require(
        type(rows) is list
        and len(rows) == len(labels)
        and all(type(row) is dict and type(row.get("label")) is str for row in rows)
        and {row["label"] for row in rows} == labels,
        "performance_rows_required",
    )

    def timing(value, fields):
        require(type(value) is dict, "performance_timing_required")
        for field in fields:
            metric = value.get(field)
            require(
                type(metric) in (int, float) and math.isfinite(metric) and metric >= 0,
                "performance_timing_required",
            )
        require(
            value["p50_ms"] <= value["p95_ms"] <= value["max_ms"]
            and value["avg_ms"] <= value["max_ms"],
            "performance_timing_inconsistent",
        )

    by_label = {row["label"]: row for row in rows}
    for row in rows:
        statuses = row.get("statuses")
        require(
            type(row.get("samples")) is int
            and row["samples"] == PERFORMANCE_ITERATIONS
            and type(row.get("status")) is int
            and row["status"] == 200
            and type(statuses) is list
            and len(statuses) == PERFORMANCE_ITERATIONS
            and all(type(status) is int and status == 200 for status in statuses)
            and type(row.get("bytes")) is int
            and row["bytes"] > 0,
            "performance_samples_required",
        )
        timing(row, ("min_ms", "avg_ms", "p50_ms", "p95_ms", "max_ms"))
        require(
            row["min_ms"] <= min(row["avg_ms"], row["p50_ms"]), "performance_timing_inconsistent"
        )
    require(by_label["snapshot.gzip"].get("encoding") == "gzip", "performance_gzip_required")
    metrics = by_label["revision"].get("server_timing_metrics")
    require(type(metrics) is dict, "performance_server_timing_required")
    app = metrics.get("app")
    require(
        type(app) is dict
        and type(app.get("samples")) is int
        and app["samples"] == PERFORMANCE_ITERATIONS,
        "performance_server_timing_required",
    )
    timing(app, ("avg_ms", "p50_ms", "p95_ms", "max_ms"))
    # Recheck the command's exact budgets. perf_probe's bytes metric is mean
    # wire size, and latency/server limits are p95 of the measured samples.
    for _flag, label, metric_path, limit in PERFORMANCE_LIMITS:
        metric = by_label[label]
        for part in metric_path.split("."):
            metric = metric[part]
        require(metric <= limit, "performance_budget_exceeded")
    return {**report, "ok": True}


def appserver_actor(native: dict) -> dict:
    actors = native.get("warm_actors")
    require(
        type(actors) is list and all(type(item) is dict for item in actors),
        "metadata_daemon_identity_unknown",
    )
    matches = [
        item
        for item in actors
        if item.get("exe") == APPSERVER_EXE
        and item.get("argv_sha256") == APPSERVER_ARGV_SHA
        and item.get("uid") == 0
    ]
    require(len(matches) == 1, "metadata_daemon_identity_unknown")
    actor = matches[0]
    require(
        type(actor.get("pid")) is int
        and actor["pid"] > 1
        and type(actor.get("start_ticks")) is int
        and actor["start_ticks"] > 0,
        "metadata_daemon_identity_unknown",
    )
    return actor


def verify_appserver_peer(peer: tuple[int, int, int], actor: dict) -> None:
    require(peer == (actor["pid"], 0, 0), "metadata_peer_changed")
    process = Path("/proc") / str(actor["pid"])
    # stat's comm field can contain spaces and parentheses; fields after its
    # final ')' begin at field 3, so starttime (22) has index 19 here.
    fields = (process / "stat").read_bytes().rsplit(b")", 1)[-1].split()
    require(len(fields) > 19 and fields[19].isdigit(), "metadata_peer_birth_unknown")
    actual = {
        "start_ticks": int(fields[19]),
        "argv_sha256": sha((process / "cmdline").read_bytes()),
        "exe": os.readlink(process / "exe"),
        "cgroup": (process / "cgroup").read_bytes().decode().strip(),
        "mount_namespace": os.readlink(process / "ns/mnt"),
    }
    require(actual == {key: actor.get(key) for key in actual}, "metadata_daemon_changed")


def manager_data(status: dict) -> dict:
    require(type(status) is dict and status.get("ok") is True, "scheduler_status_failed")
    data = status.get("data", status)
    require(type(data) is dict, "scheduler_status_shape")
    return data


def scheduler_counts(status: dict) -> dict:
    data = manager_data(status)
    jobs = data.get("jobs")
    require(type(jobs) is list, "scheduler_jobs_unknown")
    now = datetime.datetime.now(datetime.UTC)
    leases = 0
    for job in jobs:
        require(type(job) is dict and "lease_until" in job, "scheduler_lease_unknown")
        value = job["lease_until"]
        if value is not None:
            require(type(value) is str, "scheduler_lease_unknown")
            until = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(until.tzinfo is not None, "scheduler_lease_unknown")
            leases += until > now
    outbox = data.get("readiness", {}).get("execution_packet", {}).get("outbox", {})
    by_status = outbox.get("by_status")
    require(type(by_status) is dict, "scheduler_outbox_unknown")
    sending, total = by_status.get("sending", 0), outbox.get("total_count")
    require(
        type(sending) is int and sending >= 0 and type(total) is int and total >= 0,
        "scheduler_outbox_unknown",
    )
    return {"active_leases": leases, "outbox_sending": sending, "outbox_total": total}


def work_counts(response: dict) -> dict:
    status = response.get("status")
    require(response.get("ok") is True and type(status) is dict, "work_status_unknown")
    result = {}
    for key in (
        "pending_events",
        "active_jobs",
        "needs_review_jobs",
        "needs_review_events",
        "gap_count",
    ):
        value = status.get(key)
        require(type(value) is int and value >= 0, "work_counts_unknown")
        result[key] = value
    for key in ("enabled", "execution_paused"):
        require(type(status.get(key)) is bool, "work_duty_unknown")
        result[key] = status[key]
    # This RPC serializes with mutations, but does not expose a queue-length counter.
    return {
        **result,
        "outbound_queue_count_known": False,
        "serialized_mutation_barrier_passed": True,
    }


class Host:
    def __init__(
        self,
        native: dict,
        directory: Path,
        *,
        deadline: float = float("inf"),
        host_baseline: dict | None = None,
    ):
        self.native, self.directory, self.deadline = native, directory, deadline
        self.host_baseline = host_baseline

    def _save(self, label: str, value: dict) -> dict:
        require(re.fullmatch(r"[a-z0-9_-]+", label), "evidence_label_invalid")
        return write_new(self.directory / (label + "-" + uuid.uuid4().hex + ".json"), value)

    def capture_baseline(self) -> dict:
        require(self.host_baseline is None, "host_baseline_already_set")
        snapshot = self.directory / "coordinated"
        self.json(
            [
                "/usr/bin/python3",
                "-B",
                str(CRM / "scripts/coordinated_release_state.py"),
                "capture",
                "--output",
                str(snapshot),
            ],
            label="coordinated-capture",
            timeout=60,
        )
        self.json(
            [
                "/usr/bin/python3",
                "-B",
                str(CRM / "scripts/coordinated_release_state.py"),
                "verify",
                "--snapshot",
                str(snapshot),
            ],
            label="coordinated-verify",
            timeout=60,
        )
        scheduler = self.manager("status")
        data = manager_data(scheduler)
        require(data.get("global_hold", {}).get("enabled") is False, "preexisting_scheduler_hold")
        packet = data.get("readiness", {}).get("execution_packet", {})
        identity = packet.get("crm")
        require(
            packet.get("manager_revision") == MANAGER_REVISION
            and type(identity) is dict
            and identity.get("revision") == self.native["legacy_crm_revision"]
            and type(identity.get("version")) is str
            and identity["version"],
            "baseline_scheduler_identity_unknown",
        )
        work = self.job_status()
        counts = work_counts(work)
        require(
            counts["enabled"] is self.native["desired_inbound"], "preserved_duty_intent_mismatch"
        )
        self.host_baseline = {
            "schema": "autostop.crm.j1-forward-host-baseline.v1",
            "native_baseline_sha256": self.native["sha256"],
            "coordinated_snapshot": str(snapshot),
            "coordinated_manifest": reference(snapshot / "manifest.json"),
            "scheduler_status": self._save("scheduler-baseline", scheduler),
            "scheduler_identity": {
                "manager_revision": MANAGER_REVISION,
                "crm": {key: identity[key] for key in ("revision", "version")},
            },
            "scheduler_counts": scheduler_counts(scheduler),
            "work_status": self._save("work-baseline", work),
            "work": counts,
            "desired_inbound": counts["enabled"],
        }
        return self.host_baseline

    def _baseline(self) -> dict:
        value = self.host_baseline
        require(
            type(value) is dict
            and value.get("schema") == "autostop.crm.j1-forward-host-baseline.v1"
            and value.get("native_baseline_sha256") == self.native["sha256"],
            "host_baseline_required",
        )
        require(
            Path(value["coordinated_snapshot"]) == self.directory / "coordinated"
            and value["coordinated_manifest"]["path"]
            == str(self.directory / "coordinated/manifest.json"),
            "host_baseline_path_changed",
        )
        referenced(value["coordinated_manifest"])
        return value

    def observe_idle_appserver(self) -> dict:
        actor = appserver_actor(self.native)
        raw = read_protected(RPC_PATH)
        require(sha(raw) == RPC_SHA, "metadata_rpc_code_changed")
        wake_raw = read_protected(WAKE_PATH)
        require(sha(wake_raw) == WAKE_SHA, "metadata_wake_binding_changed")
        wake = document(wake_raw)
        module = ModuleType("verified_forward_metadata_rpc")
        module.__file__ = str(RPC_PATH)
        exec(compile(raw, str(RPC_PATH), "exec"), module.__dict__)
        require(sha(read_protected(RPC_PATH)) == RPC_SHA, "metadata_rpc_code_changed")
        deadline = min(self.deadline, time.monotonic() + 15)
        require(deadline > time.monotonic(), "forward_deadline")
        client = module.RPC(
            Path("/root/.codex/app-server-control/app-server-control.sock"), deadline
        )
        try:
            verify_appserver_peer(client.peer, actor)
            result = module.metadata(client, wake["thread_id"], wake["project_dir"], cold=True)
            require(
                result.get("metadata_only") is True
                and type(result.get("loaded_count")) is int
                and result["loaded_count"] >= 0,
                "metadata_idle_result_unknown",
            )
            require(
                sha(read_protected(WAKE_PATH)) == WAKE_SHA
                and sha(read_protected(RPC_PATH)) == RPC_SHA,
                "metadata_binding_changed",
            )
            verify_appserver_peer(client.peer, actor)
            return {
                "loaded_count": result["loaded_count"],
                "active_turns": 0,
                "all_loaded_and_wake_idle": True,
                "metadata_only": True,
                "rpc_sha256": RPC_SHA,
                "wake_sha256": WAKE_SHA,
            }
        finally:
            client.close()

    def begin_drain(self, attempt_id: str, baseline: dict) -> dict:
        require(
            self.host_baseline is None or self.host_baseline == baseline, "host_baseline_changed"
        )
        self.host_baseline = baseline
        self._baseline()
        from scripts.check_automation_center_release import validate_hold

        held = self.manager("hold", attempt_id)
        held_ref = self._save("scheduler-held", held)
        hold_check = validate_hold(Path(held_ref["path"]), release_attempt_key=attempt_id)
        status = self.manager("status")
        scheduler_check = self.validate_manager(status, attempt_id, held=True)
        counts = scheduler_counts(status)
        require(counts["active_leases"] == counts["outbox_sending"] == 0, "scheduler_not_drained")
        paused = self.pause_work()
        wake = self.local_request(WAKE_UNIT, WAKE_SOCKET, {"operation": "status"})
        require(
            wake.get("connected") is True
            and wake.get("outcome_unknown") is False
            and wake.get("active") is False,
            "wake_not_drained",
        )
        idle = self.observe_idle_appserver()
        return {
            "ok": True,
            "hold": hold_check,
            "scheduler": scheduler_check,
            "scheduler_counts": counts,
            "work": paused,
            "wake": {
                key: wake.get(key)
                for key in ("active", "queued", "needs_review", "execution_state")
            },
            "appserver": idle,
            "closure_continuity_proven": False,
        }

    def run(
        self,
        arguments: list[str],
        *,
        label: str,
        timeout: float = 30,
        env: dict | None = None,
        input_bytes: bytes | None = None,
    ) -> bytes:
        require(re.fullmatch(r"[a-z0-9_-]+", label), "command_label_invalid")
        remaining = min(timeout, self.deadline - time.monotonic())
        require(remaining > 0, "forward_deadline")
        result = subprocess.run(
            arguments,
            cwd=CRM,
            env={**BASE_ENV, **(env or {})},
            input=input_bytes,
            capture_output=True,
            timeout=remaining,
            check=False,
        )
        require(len(result.stdout) <= 64 * 1024**2, "command_output_limit")
        if result.returncode:
            # Error contents may contain provider/customer data. Retain only a
            # digest and bounded technical category; never echo raw stderr.
            write_new(
                self.directory / (label + "-failure.json"),
                {
                    "operation": label,
                    "returncode": result.returncode,
                    "stdout_sha256": sha(result.stdout),
                    "stderr_sha256": sha(result.stderr),
                },
            )
            require(False, "forward_command_failed_" + label)
        return result.stdout

    def json(self, arguments: list[str], **kwargs) -> dict:
        result = document(self.run(arguments, **kwargs))
        require(type(result) is dict, "command_json_object_required")
        return result

    def source(self) -> dict:
        def git(*args):
            return (
                self.run(["/usr/bin/git", "-C", str(CRM), *args], label="source").decode().strip()
            )

        revision, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
        require(
            re.fullmatch(r"[0-9a-f]{40}", revision) and re.fullmatch(r"[0-9a-f]{40}", tree),
            "source_identity_invalid",
        )
        require(
            git("branch", "--show-current") == "autostopcrm-v1"
            and not git("status", "--porcelain"),
            "clean_production_branch_required",
        )
        remote = git("ls-remote", "origin", "refs/heads/autostopcrm-v1").split()
        require(remote == [revision, "refs/heads/autostopcrm-v1"], "published_source_mismatch")
        return {"revision": revision, "tree": tree}

    def image(self, image: str) -> dict:
        raw = document(self.run(DOCKER + ["image", "inspect", image], label="image"))
        require(type(raw) is list and len(raw) == 1, "image_identity_unknown")
        value = raw[0]
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", value["Id"]), "image_digest_invalid")
        labels = value["Config"].get("Labels") or {}
        return {
            "image": value["Id"],
            "revision": labels.get("org.opencontainers.image.revision"),
            "tree": labels.get("org.autostop.crm.tree"),
        }

    def container(self) -> dict:
        values = document(self.run(DOCKER + ["inspect", "autostopcrm"], label="container"))
        require(type(values) is list and len(values) == 1, "crm_container_unknown")
        value = values[0]
        labels = value["Config"].get("Labels") or {}
        require(
            labels.get("com.docker.compose.project") == "autostopcrm", "compose_identity_changed"
        )
        return {
            "id": value["Id"],
            "image": value["Image"],
            "revision": labels.get("org.opencontainers.image.revision"),
            "running": value["State"]["Running"],
            "health": value["State"].get("Health", {}).get("Status"),
            "restart_count": value["RestartCount"],
            "mounts": sorted(
                [
                    {"source": m["Source"], "destination": m["Destination"], "rw": m["RW"]}
                    for m in value["Mounts"]
                ],
                key=lambda m: m["destination"],
            ),
            "ports": value["HostConfig"]["PortBindings"],
            "networks": sorted(value["NetworkSettings"]["Networks"]),
            "user": value["Config"]["User"],
            "security_opt": value["HostConfig"]["SecurityOpt"],
            "cap_drop": value["HostConfig"]["CapDrop"],
            "environment_sha256": sha(canonical(sorted(value["Config"]["Env"]))),
        }

    def manager(self, operation: str, attempt: str | None = None) -> dict:
        require(operation in {"hold", "release-hold", "status"}, "manager_operation_invalid")
        control = Path("/run/autostop-manager-automation/control.sock").lstat()
        require(
            stat.S_ISSOCK(control.st_mode) and control.st_uid == 0,
            "scheduler_socket_required_no_offline_fallback",
        )
        environment = {
            "PYTHONPATH": self.native["manager_root"],
            "PYTHONSAFEPATH": "1",
            "AUTOSTOP_MANAGER_ENV_FILE": "/dev/null",
            "AUTOSTOP_J1_CACHE_DIR": "/dev/null",
            "AUTOSTOP_J1_DATA_DIR": "/dev/null",
            "AUTOSTOP_AUTOMATION_DB": "/var/lib/autostop-manager-scheduler/registry.sqlite3",
            "AUTOSTOP_AUTOMATION_CONTROL_SOCKET": "/run/autostop-manager-automation/control.sock",
        }
        if operation == "status":
            command = [MANAGER_PYTHON, "-B", "-m", "autostop_manager.automation_control", "status"]
        else:
            require(
                type(attempt) is str and re.fullmatch(r"[0-9a-f-]{36}", attempt), "attempt_invalid"
            )
            command = [
                MANAGER_PYTHON,
                "-B",
                "-m",
                "autostop_manager.automation_release",
                operation,
                "--release-attempt-key",
                attempt,
            ]
        return self.json(command, label="scheduler-" + operation, env=environment)

    def validate_manager(self, status: dict, attempt: str, *, held: bool) -> dict:
        baseline = self._baseline()
        # This label belongs to the preserved native runtime; Docker separately
        # proves the actual current CRM revision. Never rewrite pinned env files.
        from scripts.check_automation_center_release import validate_manager

        status_ref = self._save("scheduler-readback", status)
        identity = baseline["scheduler_identity"]["crm"]
        return validate_manager(
            Path(status_ref["path"]),
            manager_revision=MANAGER_REVISION,
            crm_revision=identity["revision"],
            crm_version=identity["version"],
            release_attempt_key=attempt,
            expect_held=held,
            baseline_snapshot=Path(baseline["coordinated_snapshot"]),
            require_dependencies_ready=True,
        )

    def local_request(self, unit: str, path: str, request: dict) -> dict:
        require(
            (unit, path, request.get("operation"))
            in {
                (
                    "autostop-work-telegram.service",
                    "/run/autostop-work-telegram/bridge.sock",
                    "job_status",
                ),
                (
                    "autostop-work-telegram.service",
                    "/run/autostop-work-telegram/bridge.sock",
                    "duty_set",
                ),
                ("autostop-codex-wake.service", "/run/autostop-codex-wake/wake.sock", "status"),
            },
            "local_operation_invalid",
        )
        info = Path(path).lstat()
        credentials = self.native["profiles"][unit]["credentials"]
        require(
            type(credentials) is list
            and len(credentials) == 3
            and type(credentials[0]) is int
            and type(credentials[1]) is int
            and type(credentials[2]) is list,
            "local_credentials_shape",
        )
        expected_mode = 0o600 if unit == WORK_UNIT else 0o660
        require(
            stat.S_ISSOCK(info.st_mode)
            and stat.S_IMODE(info.st_mode) == expected_mode
            and info.st_uid == credentials[0],
            "local_socket_changed",
        )
        main_pid = int(
            self.run(
                ["/usr/bin/systemctl", "show", unit, "--value", "-p", "MainPID"], label="peer"
            ).strip()
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            left = min(10, self.deadline - time.monotonic())
            require(left > 0, "forward_deadline")
            client.settimeout(left)
            client.connect(path)
            pid, uid, _gid = struct.unpack(
                "3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            require(pid == main_pid > 0 and uid == credentials[0], "local_peer_changed")
            client.sendall(canonical(request))
            raw = bytearray()
            while b"\n" not in raw:
                left = min(10, self.deadline - time.monotonic())
                require(left > 0, "forward_deadline")
                client.settimeout(left)
                block = client.recv(4096)
                require(block, "local_response_truncated")
                raw.extend(block)
                require(len(raw) <= 65536, "local_response_limit")
        require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "local_response_framing")
        response = document(bytes(raw))
        require(type(response) is dict and response.get("ok") is True, "local_request_rejected")
        require(
            (info.st_dev, info.st_ino) == (Path(path).stat().st_dev, Path(path).stat().st_ino),
            "local_socket_replaced",
        )
        return response

    def pause_work(self) -> dict:
        result = self.local_request(
            "autostop-work-telegram.service",
            "/run/autostop-work-telegram/bridge.sock",
            {"operation": "duty_set", "enabled": False},
        )
        require(
            type(result.get("monitor")) is dict and result["monitor"].get("enabled") is False,
            "work_pause_not_confirmed",
        )
        status = self.job_status()
        counts = work_counts(status)
        require(counts["enabled"] is False, "work_pause_readback_failed")
        return {"paused": True, **counts, "status": self._save("work-paused", status)}

    def job_status(self) -> dict:
        return self.local_request(
            "autostop-work-telegram.service",
            "/run/autostop-work-telegram/bridge.sock",
            {"operation": "job_status"},
        )

    def replace_crm(self, candidate: dict) -> None:
        require(
            self.image(candidate["image"])
            == {k: candidate[k] for k in ("image", "revision", "tree")},
            "candidate_image_changed",
        )
        self.run(
            DOCKER
            + ["compose", "up", "-d", "--no-deps", "--no-build", "--force-recreate", "autostopcrm"],
            label="activate-crm",
            timeout=60,
            env={"AUTOSTOP_RELEASE_IMAGE": candidate["image"]},
        )
        end = min(time.monotonic() + 80, self.deadline)
        while time.monotonic() < end:
            value = self.container()
            require(
                value["image"] == candidate["image"] and value["revision"] == candidate["revision"],
                "live_candidate_mismatch",
            )
            if value["running"] and value["health"] == "healthy":
                return
            time.sleep(1)
        require(False, "candidate_health_timeout")

    def start_native(self) -> None:
        from scripts.j1_forward_authority import ALL_UNITS, NATIVE_UNITS

        desired = self.native["desired_units"]
        require(
            type(desired) is dict
            and set(desired) == set(ALL_UNITS)
            and all(
                type(state) is dict and type(state.get("active")) is bool
                for state in desired.values()
            ),
            "unsupported_native_activation",
        )
        units = sorted(unit for unit, state in desired.items() if state["active"])
        require(tuple(units) == NATIVE_UNITS, "unsupported_native_activation")
        # Preserve failed-state and start-limit diagnostics. A refusal to start
        # requires investigation, not an unconditional reset of those counters.
        self.run(["/usr/bin/systemctl", "start", *units], label="start-native", timeout=90)

    def restore_duty(self) -> dict:
        refs = self.native["authority"]
        intent = next(ref for ref in refs if ref["path"] == "/etc/autostop-crm-j1-duty/intent.json")
        return self.json(
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "/usr/local/libexec/autostop-crm-j1-duty/restore.py",
                "restore",
                "--intent-sha256",
                intent["sha256"],
            ],
            label="restore-duty",
            timeout=30,
        )

    def probes(self, candidate: dict, attempt_id: str, *, held: bool) -> dict:
        reports = {}
        commands = {
            "runtime": [
                "scripts/j1_forward_runtime.py",
                "--public-url",
                "https://crm.autostopcrm.ru",
                "--api-url",
                "http://127.0.0.1:41731",
            ],
            "gateway": [
                "scripts/check_agent_gateway_v2.py",
                "--mcp-url",
                "https://crm.autostopcrm.ru/mcp",
                "--exhaustive",
                "--require-store",
                "--require-web",
                "--maintenance-safe",
                "--release-revision",
                candidate["revision"],
                "--release-attempt-id",
                attempt_id,
            ],
            "oauth": ["scripts/check_mcp_oauth.py", "--mcp-url", "https://crm.autostopcrm.ru/mcp"],
            "performance": [
                "scripts/perf_probe.py",
                "--base-url",
                PERFORMANCE_URL,
                "--mcp-service-auth",
                "--warmup-iterations",
                str(PERFORMANCE_WARMUPS),
                "--iterations",
                str(PERFORMANCE_ITERATIONS),
                *[
                    value
                    for flag, _label, _metric, limit in PERFORMANCE_LIMITS
                    for value in (flag, str(limit))
                ],
            ],
        }
        for name, args in commands.items():
            reports[name] = document(
                self.run(
                    DOCKER + ["exec", "autostopcrm", "python", "-B", *args],
                    label="probe-" + name,
                    timeout=120,
                )
            )
            if name == "performance":
                reports[name] = performance_report(reports[name])
            require(
                type(reports[name]) is dict and reports[name].get("ok") is True,
                "runtime_probe_failed_" + name,
            )
        feed = self.json(
            [
                "/usr/bin/python3",
                "-B",
                str(CRM / "scripts/probe_manager_crm_feed_auth.py"),
                "--manager-env",
                "/opt/AutostopManager/.crm-mcp.env",
            ],
            label="probe-feed",
        )
        require(
            feed.get("ok") is True
            and feed.get("crm_change_feed_ready") is True
            and feed.get("consumer_unchanged") is True,
            "feed_probe_failed",
        )
        reports["feed"] = feed
        reports["scheduler"] = self.validate_manager(self.manager("status"), attempt_id, held=held)
        reports["actual_crm_revision"] = candidate["revision"]
        reports["preserved_native_crm_label"] = self.native["legacy_crm_revision"]
        return reports


class MixedHost(Host):
    """One MCP successor; shared scheduler/Work/hold operations stay preserved."""

    def __init__(self, native, directory, **kwargs):
        from scripts.j1_forward_mixed_authority import SCHEMA

        require(native.get("schema") == SCHEMA, "mixed_host_native_schema")
        super().__init__(native, directory, **kwargs)


def check_container_preserved(previous: dict, current: dict, candidate: dict) -> None:
    require(
        current["image"] == candidate["image"]
        and current["revision"] == candidate["revision"]
        and current["health"] == "healthy"
        and current["running"] is True,
        "live_release_identity_failed",
    )
    fields = (
        "mounts",
        "ports",
        "networks",
        "user",
        "security_opt",
        "cap_drop",
        "environment_sha256",
    )
    require(
        all(previous[key] == current[key] for key in fields),
        "persistent_container_bindings_changed",
    )
