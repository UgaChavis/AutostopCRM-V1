"""Real native admission protocol on a disposable GitHub-hosted Linux VM only.

This exercises the reviewed native journal and observer with transient units,
root-owned /run fixtures and one synthetic file effect after E. It does not
execute the production release consumer or attest production release readiness.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
PRODUCTION_PATHS = (
    "/opt/autostopcrm",
    "/opt/AutostopManager",
    "/opt/autostopapp",
    "/opt/autostop-app",
    "/var/lib/autostop-crm-forward",
)
CONSUMERS = (
    "release",
    "launch_native",
    "launch_permission",
    "io",
    "state",
    "host",
    "backup",
    "closure",
)

WORKER_SOURCE = r'''"""Private protocol fixture, never a production release consumer."""
import contextlib, fcntl, json, os, sys, time
from pathlib import Path
crm = Path(__file__).resolve().parents[1]
base = crm.parent
sys.path.insert(0, str(crm))
from scripts import j1_forward_launch_native as native
from scripts import j1_forward_launch_permission as journal
config = json.loads((base / 'fixture.json').read_text())
native.CRM, native.FORWARD = crm, Path(__file__).resolve()
native.ATTEMPTS = base / 'attempts'
native.LOCK = crm / '.autostop-deploy.lock'
native.MARKER = crm / 'data/maintenance'
native.WORKER_ENVIRONMENT = {'LD_LIBRARY_PATH': config['sqlite_directory']}
journal.ROOT = base / 'launch-permits'
attempt = sys.argv[sys.argv.index('--attempt') + 1]

def wait(path):
    until = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() >= until:
            raise RuntimeError('fixture_gate_timeout')
        time.sleep(.01)

@contextlib.contextmanager
def deployment_lock(expected):
    fd = os.open(native.LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        journal.require([info.st_dev, info.st_ino] == expected, 'fixture_lock_identity')
        until = time.monotonic() + 20
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise RuntimeError('fixture_d_timeout')
                time.sleep(.01)
        journal.require([native.LOCK.stat().st_dev, native.LOCK.stat().st_ino] == expected,
                        'fixture_lock_replaced')
        yield
    finally:
        os.close(fd)

if sys.argv[1] == 'settle':
    # Preserve old acceptance deliberately for the stale-incarnation scenario.
    # Other unentered fixtures execute the real native clean-scope denial.
    # The full production safety settle is outside this protocol stand.
    if config['mode'] not in ('stale_identity', 'corrupt_receipt'):
        native.settle_unentered(attempt, deployment_lock)
    raise SystemExit(0)

result = {'ok': False, 'code': 'not_entered', 'native_sqlite': False}
try:
    if config['mode'] in ('stale_identity', 'corrupt_receipt'):
        wait(base / 'go')
    with native.Admission(attempt, deployment_lock) as admission:
        with admission.execution(admission.context['deployment_lock_identity'], on_failure=lambda: None):
            import sqlite3
            if sqlite3.sqlite_version != '3.51.3':
                raise RuntimeError('fixture_native_sqlite_wrong')
            connection = sqlite3.connect(':memory:')
            source = connection.execute('select sqlite_source_id()').fetchone()[0]
            connection.close()
            if source != '2026-03-13 10:38:09 737ae4a34738ffa0c3ff7f9bb18df914dd1cad163f28fd6b6e114a344fe6d618':
                raise RuntimeError('fixture_native_sqlite_source_wrong')
            result['native_sqlite'] = True
            pending_effect = base / 'effect-publishing'
            fd = os.open(pending_effect, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'synthetic-effect-after-native-E')
                stream.flush()
                os.fsync(stream.fileno())
            # Publish complete bytes without replacing an earlier effect. A
            # reader can never observe the in-progress empty sentinel.
            os.link(pending_effect, base / 'effect', follow_symlinks=False)
            pending_effect.unlink()
            journal._sync(base)
            if config['mode'] == 'late_revoke':
                wait(base / 'exit')
    if config['mode'] == 'duplicate':
        try:
            with native.Admission(attempt, deployment_lock):
                raise RuntimeError('fixture_duplicate_entered')
        except journal.Refusal:
            result['duplicate_refused'] = True
    result.update(ok=True, code='entered_once')
except BaseException as error:
    result['code'] = str(error) if isinstance(error, journal.Refusal) else type(error).__name__
finally:
    path = base / 'worker-results' / (str(os.getpid()) + '-' + str(native.owner()['start_ticks']) + '.json')
    journal._new(path, journal.canonical(result))
raise SystemExit(0 if result['ok'] else 1)
'''


class StandRefusal(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise StandRefusal(code)


def validate_labels(environment, platform, uid):
    require(
        environment.get("GITHUB_ACTIONS") == "true"
        and environment.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and environment.get("RUNNER_OS") == "Linux"
        and platform.startswith("linux")
        and uid == 0,
        "github_hosted_linux_root_required",
    )


def validate_host(pid1, production_paths_present):
    require(pid1.strip() == "systemd", "real_pid1_systemd_required")
    require(not production_paths_present, "production_host_paths_refused")


def boundary():
    # Reject an ordinary/local invocation before any /proc, unit or host-path IO.
    validate_labels(os.environ, sys.platform, os.geteuid() if hasattr(os, "geteuid") else -1)
    validate_host(
        Path("/proc/1/comm").read_text(), any(os.path.lexists(path) for path in PRODUCTION_PATHS)
    )
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(workspace == SOURCE and Path.cwd().resolve() == SOURCE, "exact_checkout_required")
    sha = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "exact_source_sha_required")
    result = subprocess.run(
        ["git", "-c", f"safe.directory={SOURCE}", "rev-parse", "HEAD"],
        cwd=SOURCE,
        capture_output=True,
        check=False,
        timeout=10,
    )
    require(
        result.returncode == 0 and result.stdout.decode().strip() == sha, "checkout_sha_changed"
    )
    library = Path(os.environ["CRM_REHEARSAL_SQLITE_LIBRARY"]).resolve(strict=True)
    require(
        str(library.parent) in os.environ.get("LD_LIBRARY_PATH", "").split(":"),
        "private_sqlite_loader_required",
    )
    require(not library.is_relative_to("/opt"), "production_sqlite_path_refused")
    return sha, str(library.parent)


def safe_private_root(path):
    suffix = path.name.removeprefix("autostop-crm-rehearsal-")
    try:
        valid_uuid = str(uuid.UUID(suffix)) == suffix
    except ValueError:
        valid_uuid = False
    require(
        path.parent == Path("/run")
        and path.name.startswith("autostop-crm-rehearsal-")
        and valid_uuid,
        "private_cleanup_scope",
    )
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid == 0,
        "private_cleanup_identity",
    )


def wait_until(predicate, *, seconds=12):
    until = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        require(time.monotonic() < until, "fixture_observation_timeout")
        time.sleep(0.02)


def unit_status(unit):
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            "--no-pager",
            "--property=LoadState,ActiveState,MainPID",
        ],
        capture_output=True,
        check=False,
        timeout=3,
    )
    values = dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)
    require(set(values) == {"LoadState", "ActiveState", "MainPID"}, "fixture_unit_state_unknown")
    require(
        result.returncode == 0 or values["LoadState"] == "not-found", "fixture_unit_state_unknown"
    )
    return values


class Fixture:
    def __init__(self, root, native, journal, sqlite_directory, *, mode="normal", ttl=60):
        initialized = time.monotonic()
        self.wait_phase = "initialization"
        self.root, self.native, self.journal = root, native, journal
        root.mkdir(mode=0o700)
        self.crm, self.attempts = root / "crm", root / "attempts"
        self.crm.mkdir(mode=0o700)
        (self.crm / "scripts").mkdir(mode=0o700)
        (self.crm / "data").mkdir(mode=0o700)
        self.attempts.mkdir(mode=0o700)
        (root / "worker-results").mkdir(mode=0o700)
        self.attempt = str(uuid.uuid4())
        self.unit = "autostop-crm-forward-" + self.attempt + ".service"
        self.wire_processes, self.threads, self.submit_results = [], [], []
        self.last_worker_observation = None
        self.original_observe_worker = native.observe_worker

        def observed_worker(*args, **kwargs):
            try:
                result = self.original_observe_worker(*args, **kwargs)
            except BaseException as error:
                self.last_worker_observation = {
                    "error_type": type(error).__name__,
                    "protocol_code": str(error) if isinstance(error, journal.Refusal) else None,
                }
                raise
            self.last_worker_observation = {"ok": True}
            return result

        native.observe_worker = observed_worker
        native.CRM, native.FORWARD = self.crm, self.crm / "scripts/j1_forward_release.py"
        native.ATTEMPTS = self.attempts
        native.LOCK, native.MARKER = (
            self.crm / ".autostop-deploy.lock",
            self.crm / "data/maintenance",
        )
        native.WORKER_ENVIRONMENT = {"LD_LIBRARY_PATH": sqlite_directory}
        journal.ROOT = root / "launch-permits"
        journal._new(
            root / "fixture.json",
            journal.canonical({"mode": mode, "sqlite_directory": sqlite_directory}),
        )
        self.consumers = {}
        for name in CONSUMERS:
            relative = f"scripts/j1_forward_{name}.py"
            raw = WORKER_SOURCE.encode() if name == "release" else (SOURCE / relative).read_bytes()
            journal._new(self.crm / relative, raw)
            self.consumers[relative] = journal.sha(raw)
        journal._new(native.LOCK, b"")
        info = native.LOCK.stat()
        self.identity = [info.st_dev, info.st_ino]
        self.directory = self.attempts / self.attempt
        self.directory.mkdir(mode=0o700)
        baseline_path = self.directory / "baseline.json"
        baseline = {"deployment_lock_identity": self.identity}
        journal._new(baseline_path, journal.canonical(baseline))
        self.deadline = datetime.now(UTC) + timedelta(seconds=ttl)
        plan = {
            "attempt_id": self.attempt,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
            "host": socket.gethostname(),
            "baseline_ref": {
                "path": str(baseline_path),
                "sha256": journal.sha(baseline_path.read_bytes()),
            },
            "candidate": {"consumer_sha256": journal.sha(journal.canonical(self.consumers))},
            "deadline_utc": self.deadline.isoformat(),
        }
        plan_path = self.directory / "plan.json"
        journal._new(plan_path, journal.canonical(plan))
        plan_ref = {"path": str(plan_path), "sha256": journal.sha(plan_path.read_bytes())}
        journal._new(
            self.directory / "progress.json",
            journal.canonical(
                {"phase": "prepared", "observed_at": "synthetic", "plan_ref": plan_ref}
            ),
        )
        with self.deployment_lock():
            native.issue(plan_ref, self.consumers, baseline)
        self.store, self.snapshot, self.context = native.validate(self.attempt)
        self.initialization_seconds = time.monotonic() - initialized

    def failure_observation(self):
        observation = {
            "wait_phase": self.wait_phase,
            "initialization_seconds": round(self.initialization_seconds, 3),
            "seconds_to_expiry": round(self.deadline.timestamp() - time.time(), 3),
            "last_worker_observation": self.last_worker_observation,
            "submit_results": [
                {
                    "ok": result.get("ok", False),
                    "error_type": type(result["error"]).__name__ if "error" in result else None,
                    "protocol_code": (
                        str(result["error"])
                        if isinstance(result.get("error"), self.journal.Refusal)
                        else None
                    ),
                }
                for result in self.submit_results
            ],
            "worker_results": self.workers(),
        }
        try:
            current = self.state()
            observation["launch_state"] = {
                "sequence": current["sequence"],
                "accepted": current["acceptance"] is not None,
                "claimed": current["claim"] is not None,
                "dispatch_possible": current["dispatch_possible"],
                "execution_possible": current["execution_possible"],
            }
            observation["unit"] = unit_status(self.unit)
        except BaseException as error:
            observation["observation_failure_type"] = type(error).__name__
        return observation

    @contextlib.contextmanager
    def deployment_lock(self):
        import fcntl

        fd = os.open(self.native.LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            require([info.st_dev, info.st_ino] == self.identity, "fixture_d_replaced")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def asynchronous_submit(self):
        result = {}
        self.submit_results.append(result)

        def submit():
            try:
                self.native.submit_and_wait(self.attempt, timeout=25)
                result["ok"] = True
            except BaseException as error:
                result["error"] = error

        thread = threading.Thread(target=submit, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, result

    def join(self, thread, result, *, succeeds):
        thread.join(timeout=35)
        require(not thread.is_alive(), "fixture_submit_thread_unfinished")
        require(bool(result.get("ok")) == succeeds, "fixture_unexpected_submit_outcome")
        if not succeeds:
            require(
                isinstance(result.get("error"), self.journal.Refusal),
                "fixture_nonprotocol_submit_failure",
            )

    def manual_submit(self):
        result = self.native._transition(
            self.store, self.snapshot["ticket"], "submit", {"owner": self.native.owner()}
        )
        require(result["action_authorized"], "fixture_s_not_authorized")
        return self.spawn_wire()

    def spawn_wire(self):
        process = subprocess.Popen(
            self.context["launch_argv"],
            cwd=self.crm,
            env=self.context["launcher_environment"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.wire_processes.append(process)
        return process

    def current_worker(self):
        try:
            return self.native.observe_worker(self.snapshot["ticket"], self.context)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def accept(self):
        wait_until(self.current_worker)
        return self.native.publish_acceptance(self.store, self.store.read(), self.context)

    def state(self):
        return self.native.coherent_snapshot(self.store)["state"]

    def effect_count(self):
        path = self.root / "effect"
        if not path.exists():
            return 0
        require(
            path.read_bytes() == b"synthetic-effect-after-native-E",
            "fixture_effect_content_changed",
        )
        return 1

    def workers(self):
        return [
            json.loads(path.read_text()) for path in (self.root / "worker-results").glob("*.json")
        ]

    def stop(self):
        subprocess.run(
            ["/usr/bin/systemctl", "stop", self.unit], capture_output=True, check=False, timeout=15
        )
        for process in self.wire_processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=35)
            require(not thread.is_alive(), "fixture_thread_cleanup_unfinished")
        self.native.observe_worker = self.original_observe_worker
        values = unit_status(self.unit)
        require(
            values["ActiveState"] not in {"active", "activating", "deactivating"}
            and values["MainPID"] == "0",
            "fixture_unit_cleanup_unfinished",
        )


def refuse(call, error_type):
    try:
        call()
    except error_type as error:
        return str(error)
    raise StandRefusal("expected_native_refusal_missing")


def run_case(name, fixture):
    n, j, f = fixture.native, fixture.journal, fixture
    if name == "good_sace":
        thread, result = f.asynchronous_submit()
        f.join(thread, result, succeeds=True)
        require(
            f.state()["execution_possible"] and f.effect_count() == 1, "good_admission_not_effected"
        )
        require(
            f.workers() and all(worker["native_sqlite"] for worker in f.workers()),
            "worker_native_sqlite_not_verified",
        )
    elif name == "revoke_before_submit":
        n.revoke(f.attempt)
        refuse(lambda: n.submit_and_wait(f.attempt, timeout=10), j.Refusal)
        require(
            not f.state()["dispatch_possible"] and f.effect_count() == 0,
            "revoked_before_s_dispatched",
        )
    elif name in {"revoke_between_claim_and_enter", "expiry_before_enter"}:
        with f.deployment_lock():
            thread, result = f.asynchronous_submit()
            f.wait_phase = "waiting_for_claim"
            wait_until(lambda: f.state()["claim"] is not None)
            require(
                not f.state()["execution_possible"] and f.effect_count() == 0, "effect_before_d"
            )
            if name == "expiry_before_enter":
                f.wait_phase = "waiting_for_real_expiry"
                wait_until(lambda: time.time() > f.deadline.timestamp() + 0.05, seconds=10)
            else:
                n.revoke(f.attempt)
        f.join(thread, result, succeeds=False)
        require(
            not f.state()["execution_possible"] and f.effect_count() == 0, "denied_final_e_effected"
        )
    elif name == "enter_before_late_revoke":
        thread, result = f.asynchronous_submit()
        wait_until(lambda: f.effect_count() == 1)
        receipt = n.revoke(f.attempt)
        require(
            receipt["current_admission"] == "too_late_for_current_admission",
            "late_revoke_claimed_undo",
        )
        require(
            f.state()["execution_possible"] and f.effect_count() == 1, "late_revoke_erased_effect"
        )
        j._new(f.root / "exit", b"")
        f.join(thread, result, succeeds=True)
    elif name == "lost_ack_no_resubmit":
        process = f.manual_submit()
        wait_until(f.current_worker)
        process.kill()
        process.wait(timeout=5)
        require(
            f.state()["dispatch_possible"] and f.state()["acceptance"] is None,
            "lost_ack_was_not_unknown",
        )
        refuse(lambda: n.submit_and_wait(f.attempt, timeout=10), j.Refusal)
        n.revoke(f.attempt)
        require(f.effect_count() == 0, "unaccepted_worker_effected")
    elif name == "duplicate_entry":
        thread, result = f.asynchronous_submit()
        f.join(thread, result, succeeds=True)
        require(
            f.effect_count() == 1
            and any(worker.get("duplicate_refused") for worker in f.workers()),
            "duplicate_entry_not_refused",
        )
        refuse(lambda: n.submit_and_wait(f.attempt, timeout=10), j.Refusal)
    elif name == "stale_worker_identity":
        first = f.manual_submit()
        acceptance = f.accept()
        subprocess.run(
            ["/usr/bin/systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", f.unit],
            check=True,
            capture_output=True,
            timeout=10,
        )
        first.wait(timeout=15)
        wait_until(lambda: unit_status(f.unit)["LoadState"] == "not-found")
        j._new(f.root / "go", b"")
        second = f.spawn_wire()
        require(second.wait(timeout=20) != 0, "replacement_worker_accepted")
        require(
            f.state()["acceptance"] == acceptance
            and f.state()["claim"] is None
            and f.effect_count() == 0,
            "stale_acceptance_rebound",
        )
        require(
            any(worker["code"] == "launch_worker_not_accepted" for worker in f.workers()),
            "stale_identity_not_observed",
        )
        n.revoke(f.attempt)
    elif name == "corrupt_receipt":
        process = f.manual_submit()
        acceptance = f.accept()
        j._replace(Path(acceptance["evidence_ref"]["path"]), b'{"corrupt":true}')
        refuse(lambda: n.validate(f.attempt), j.Refusal)
        j._new(f.root / "go", b"")
        require(process.wait(timeout=20) != 0 and f.effect_count() == 0, "corrupt_receipt_effected")
        require(
            any(worker["code"] == "launch_receipt_changed" for worker in f.workers()),
            "corrupt_receipt_not_observed",
        )
    else:
        raise StandRefusal("unknown_fixture_case")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    sha, sqlite_directory = boundary()
    output = args.output_dir.resolve()
    require(
        output.is_relative_to(SOURCE / "output/release-rehearsal") and output.name == "systemd",
        "sanitized_report_scope",
    )
    sys.path.insert(0, str(SOURCE))
    from scripts import j1_forward_launch_native as native
    from scripts import j1_forward_launch_permission as journal

    private = Path("/run") / ("autostop-crm-rehearsal-" + str(uuid.uuid4()))
    private.mkdir(mode=0o700)
    wires = []
    known_units = set()
    fixtures = []

    def observe_wire(event, arguments):
        if event == "subprocess.Popen" and os.fsdecode(arguments[0]) == "/usr/bin/systemd-run":
            argv = arguments[1]
            units = [arg for arg in argv if str(arg).startswith("--unit=")]
            require(len(units) == 1 and str(units[0]) in known_units, "unowned_wire_submission")
            wires.append(str(units[0]))

    sys.addaudithook(observe_wire)
    report = {
        "schema": "autostopcrm.isolated-native-admission-rehearsal.v1",
        "source_sha": sha,
        "scope": "Disposable hosted native admission protocol; no production release consumer or production acceptance.",
        "tested_source_sha256": {
            name: hashlib.sha256((SOURCE / "scripts" / name).read_bytes()).hexdigest()
            for name in ("j1_forward_launch_native.py", "j1_forward_launch_permission.py")
        },
        "fixture_consumer_sha256": hashlib.sha256(WORKER_SOURCE.encode()).hexdigest(),
        "boundary": {
            "hosted_linux_root": True,
            "pid1_systemd": True,
            "canonical_production_paths_absent": True,
        },
        "cases": [],
        "ok": False,
        "cleanup_complete": False,
        "limitations": [
            "The fixed consumer body is a synthetic sentinel, not the production release consumer.",
            "No full archive, recovery, business-state or production release acceptance is established.",
            "Stale incarnation is real; kernel PID-number reuse is not forced.",
            "The stale case deliberately injects a second raw fixed fixture wire; it does not authorize a retry.",
            "Plan and baseline are minimal synthetic fixtures; original production backup and release semantics are not exercised.",
            "Private root-owned journals and unit evidence are removed, not uploaded.",
        ],
    }
    cases = (
        ("good_sace", "normal", 60),
        ("revoke_before_submit", "normal", 60),
        ("revoke_between_claim_and_enter", "normal", 60),
        ("enter_before_late_revoke", "late_revoke", 60),
        ("lost_ack_no_resubmit", "normal", 60),
        ("duplicate_entry", "duplicate", 60),
        ("stale_worker_identity", "stale_identity", 60),
        ("expiry_before_enter", "normal", 8),
        ("corrupt_receipt", "corrupt_receipt", 60),
    )
    try:
        for name, mode, ttl in cases:
            started = time.monotonic()
            fixture = Fixture(
                private / str(uuid.uuid4()), native, journal, sqlite_directory, mode=mode, ttl=ttl
            )
            fixtures.append(fixture)
            report["current_case"] = name
            known_units.add("--unit=" + fixture.unit.removesuffix(".service"))
            wire_before = len(wires)
            try:
                run_case(name, fixture)
                count = len(wires) - wire_before
                require(
                    count
                    == (
                        0
                        if name == "revoke_before_submit"
                        else 2
                        if name == "stale_worker_identity"
                        else 1
                    ),
                    "wire_count_or_blind_retry",
                )
            except BaseException:
                report["case_failure_observation"] = fixture.failure_observation()
                raise
            finally:
                fixture.stop()
            effects = fixture.effect_count()
            require(
                effects
                == (
                    1 if name in {"good_sace", "enter_before_late_revoke", "duplicate_entry"} else 0
                ),
                "post_cleanup_effect_count_changed",
            )
            report["cases"].append(
                {
                    "name": name,
                    "passed": True,
                    "wire_submissions": count,
                    "sentinel_effects": effects,
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
        report.pop("current_case", None)
        report["ok"] = True
    except BaseException as error:
        report["failure_type"] = type(error).__name__
        message = str(error)
        report["failure_code"] = (
            message if re.fullmatch(r"[a-z0-9_]+", message) else "native_or_fixture_refusal"
        )
    finally:
        cleaned = True
        for fixture in fixtures:
            try:
                fixture.stop()
            except BaseException:
                cleaned = False
        if cleaned:
            safe_private_root(private)
            shutil.rmtree(private)
        report["cleanup_complete"] = cleaned
        report["private_evidence_removed"] = cleaned
        report["ok"] = report["ok"] and cleaned
        output.mkdir(parents=True, exist_ok=True, mode=0o755)
        os.chmod(output, 0o755)
        path = output / "report.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] and report["cleanup_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
