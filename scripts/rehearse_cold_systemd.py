"""Cold execution-slot template and real guard on a disposable hosted VM only.

The package, old cohort, kernel and role are private native_fixture inputs.
Only the systemd execution/observation boundary is real. This is neither a
production package issuer nor a full cold recovery or business acceptance test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
CASES = ("one_entry", "source_corrupt_before_a", "parent_r_before_a", "kernel_dead_before_a")


class StandRefusal(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise StandRefusal(code)


def boundary():
    # The existing gate refuses a local call before /proc or systemd access.
    sys.path.insert(0, str(SOURCE))
    from scripts import j1_isolated_systemd_rehearsal as hosted

    sha, _sqlite_directory = hosted.boundary()
    require(
        Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve()
        and os.getegid() == 0
        and sorted(os.getgroups()) == [0],
        "fixed_host_root_bootstrap_required",
    )
    return sha


def private_root(path):
    require(
        path.parent == Path("/run") and re.fullmatch(r"acs-[0-9a-f]{16}", path.name),
        "cold_systemd_cleanup_scope",
    )
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and not path.is_symlink()
        and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o700,
        "cold_systemd_cleanup_identity",
    )


def unix_path_budget(root):
    # Authority uses full UUIDs. This exclusive short temporary name only keeps
    # the fixture's longest AF_UNIX endpoint below sun_path's byte limit.
    path = root / "autostop-cold-native-12345678/execution/roles/personal-telegram/run/control.sock"
    require(len(os.fsencode(path)) < 108, "cold_systemd_unix_path_budget")


def code(error):
    text = str(error)
    return text if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", text) else type(error).__name__


class HostedFixtureBackend:
    """Explicit test OS adapter; NativeBackend's production constructor is strict.

    Actual template, start, process, environment, unit and cgroup observers are
    delegated unchanged. The adapter owns one fresh random unit on this VM.
    Fault injection occurs after an actual wrapper observation and before A.
    """

    def __init__(self, fixture, mode):
        boundary()
        from scripts import j1_forward_cold_fixture as private
        from scripts import j1_forward_cold_lifecycle as lifecycle

        require(
            type(fixture.provider) is private.FixtureProvider
            and fixture.provider.scope == "native_fixture"
            and mode in CASES,
            "fixture_os_scope",
        )
        self.fixture, self.provider, self.mode = fixture, fixture.provider, mode
        self.actual = lifecycle.NativeBackend
        self.unit = None
        self.unit_bytes = None
        self.unit_identity = None
        self.launches = 0
        self.injected = False
        self.launch_started = self.launch_finished = self.wrapper_observed = None

    def _verify_unit(self, ticket, context, *, inactive):
        require(ticket["unit"] == self.unit, "unowned_cold_systemd_unit")
        return self.actual._verify_unit(self, ticket, context, inactive=inactive)

    def stage(self, unit, context, ticket, root):
        require(self.unit is None, "fixture_unit_already_staged")
        require(
            re.fullmatch(r"autostop-crm-forward-[0-9a-f-]{36}\.service", ticket["unit"])
            and context["scope"] == "native_fixture"
            and context["worker_cwd"] == "/"
            and context["worker_argv"][4]
            == str(self.fixture.root / "source/scripts/j1_forward_cold_guard.py"),
            "fixture_slot_contract",
        )
        path = Path("/etc/systemd/system") / ticket["unit"]
        require(not os.path.lexists(path), "fixture_unit_collision")
        self.unit = ticket["unit"]
        self.actual.stage(self, unit, context, ticket, root)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0, "fixture_unit_custody")
        self.unit_identity = (info.st_dev, info.st_ino)
        self.unit_bytes = path.read_bytes()
        require(
            b"ExecCondition=" not in self.unit_bytes
            and self.unit_bytes.count(b"ExecStart=") == 1
            and b"ExecStart=" + " ".join(context["worker_argv"]).encode() + b"\n"
            in self.unit_bytes,
            "fixture_unit_template",
        )
        self.closed(ticket, context)

    def closed(self, ticket, context):
        return self.actual.closed(self, ticket, context)

    def launch(self, ticket, context):
        require(self.launches == 0, "fixture_systemd_resubmit_forbidden")
        self.launches += 1
        self.launch_started = time.monotonic()
        self.actual.launch(self, ticket, context)
        self.launch_finished = time.monotonic()

    def observe(self, ticket, context, *, started=False):
        result = self.actual.observe(self, ticket, context, started=started)
        if not started and self.wrapper_observed is None:
            self.wrapper_observed = time.monotonic()
            self.before_a(ticket)
        return result

    def before_a(self, ticket):
        from scripts import j1_forward_cold_lifecycle as lifecycle
        from scripts import j1_forward_cold_native as native

        store = self.fixture.controller.store(self.fixture.logical_unit)
        snapshot = lifecycle.coherent(store)
        require(
            snapshot["ticket"] == ticket
            and snapshot["state"]["acceptance"] is None
            and snapshot["state"]["claim"] is None
            and snapshot["state"]["execution_possible"] is False,
            "fault_was_not_before_a",
        )
        if self.mode == "source_corrupt_before_a":
            path = self.fixture.root / "source/worker.py"
            with path.open("ab") as stream:
                stream.write(b"\n# private corruption after OS observation, before A\n")
                stream.flush()
                os.fsync(stream.fileno())
        elif self.mode == "parent_r_before_a":
            parent = self.fixture.parent.store
            with parent.locked():
                lifecycle._transition_child(parent, parent.read()["ticket"], "revoke", {})
        elif self.mode == "kernel_dead_before_a":
            package = json.loads(self.provider.read_reference(self.fixture.package_ref))
            kernel = json.loads(self.provider.read_reference(package["kernel_ref"]))
            actor = kernel["actor"]
            child = next(row for row in self.fixture.children if row.pid == actor["pid"])
            self.fixture._retire(child, native.actor(child.pid))
        self.injected = True

    def diagnostic(self):
        """Only owned-unit scalar facts and normalized guard errors are exported."""
        if self.unit is None:
            return {}
        from scripts import j1_forward_cold_native as native

        properties = (*native.UNIT_PROPERTIES, "FragmentPath", "Job", "ExecMainStatus")
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                self.unit,
                "--no-pager",
                "--all",
                "--property=" + ",".join(properties),
            ],
            capture_output=True,
            check=True,
            timeout=3,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.decode().splitlines())
        scalar = {
            "Id",
            "Type",
            "LoadState",
            "NeedDaemonReload",
            "ActiveState",
            "SubState",
            "MainPID",
            "User",
            "Group",
            "SupplementaryGroups",
            "WorkingDirectory",
            "Restart",
            "Job",
            "RuntimeMaxUSec",
            "KillMode",
            "NotifyAccess",
            "ExecMainStatus",
        }
        journal = subprocess.run(
            [
                "/usr/bin/journalctl",
                "--unit=" + self.unit,
                "--no-pager",
                "--output=cat",
                "--lines=12",
            ],
            capture_output=True,
            check=False,
            timeout=3,
        )
        errors = []
        for line in journal.stdout[:16384].decode("utf-8", "replace").splitlines():
            if re.fullmatch(
                r"(?:Refusal|ValueError|RuntimeError|FileNotFoundError|ProcessLookupError|PermissionError): [a-z][a-z0-9_]{0,127}",
                line,
            ):
                errors.append(line)
        return {
            "missing_properties": sorted(set(properties) - set(fields)),
            "unexpected_properties": sorted(set(fields) - set(properties)),
            "scalars": {name: fields[name] for name in sorted(scalar & set(fields))},
            "nonempty_hooks": [
                name
                for name in fields
                if name.startswith("Exec") and name != "ExecMainStatus" and fields[name]
            ],
            "guard_errors": errors,
        }

    def stop(self):
        if self.unit is None:
            return
        from scripts import j1_isolated_systemd_rehearsal as hosted

        result = subprocess.run(
            ["/usr/bin/systemctl", "stop", self.unit],
            capture_output=True,
            timeout=15,
            check=False,
        )
        values = hosted.unit_status(self.unit)
        require(
            result.returncode == 0
            and values["MainPID"] == "0"
            and values["ActiveState"] in {"inactive", "failed"},
            "fixture_systemd_cleanup_unfinished",
        )
        path = Path("/etc/systemd/system") / self.unit
        info = path.lstat()
        require(
            self.unit_identity == (info.st_dev, info.st_ino)
            and stat.S_ISREG(info.st_mode)
            and self.unit_bytes is not None
            and path.read_bytes() == self.unit_bytes,
            "fixture_systemd_unit_replaced",
        )
        path.unlink()
        subprocess.run(
            ["/usr/bin/systemctl", "daemon-reload"], capture_output=True, timeout=15, check=True
        )
        subprocess.run(
            ["/usr/bin/systemctl", "reset-failed", self.unit],
            capture_output=True,
            timeout=5,
            check=False,
        )
        require(hosted.unit_status(self.unit)["MainPID"] == "0", "fixture_unit_still_alive")
        self.unit = None


def stage_slot(fixture, controller, backend):
    """Create a synthetic OS slot once with cwd=/; no accepted-ticket rewrite.

    The ordinary process fixture uses cwd=its private root. Here the expected
    profile is declared before S to match the actual fixed systemd template.
    All journal, context validation and guard/consume implementations are real.
    """
    from scripts import j1_forward_cold_authority as cold
    from scripts import j1_forward_cold_lifecycle as lifecycle
    from scripts import j1_forward_io as io

    value, _fence = controller.verify()
    unit = cold.ALLOWED[0]
    policy = value["policies"][unit]
    bootstrap = fixture.interpreter
    require(
        bootstrap["path"] == str(Path("/usr/bin/python3").resolve())
        and policy["interpreter_ref"] == bootstrap,
        "fixture_host_interpreter",
    )
    binding = fixture.binding
    context = {
        "schema": "autostop.j1.cold-slot-context.v1",
        "scope": "native_fixture",
        "logical_unit": unit,
        "binding": binding,
        "package_ref": controller.package_ref,
        "epoch_ref": controller.epoch_ref,
        "policy_ref": value["package"]["policies"][unit],
        "interpreter": bootstrap["path"],
        "bootstrap_interpreter_ref": bootstrap,
        "application_interpreter_ref": policy["interpreter_ref"],
        "bootstrap_environment": {
            **lifecycle.BOOTSTRAP_ENVIRONMENT,
            **dict.fromkeys(("TMPDIR", "TEMP", "TMP"), str(fixture.root.parent)),
        },
        "bootstrap_credentials": {"uid": 0, "gid": 0, "groups": [0]},
        "worker_argv": [
            bootstrap["path"],
            "-I",
            "-S",
            "-B",
            str(fixture.root / "source/scripts/j1_forward_cold_guard.py"),
            "launch",
            "--epoch",
            binding["generation"],
            "--unit",
            unit,
            "--binding-sha256",
            cold.digest(binding),
        ],
        "worker_cwd": "/",
        "application_argv": policy["argv"],
        "application_cwd": policy["cwd"],
        "credentials": policy["credentials"],
        "environment": lifecycle._environment(policy, fixture.provider),
    }
    store = controller.store(unit)
    require(not store.root.exists(), "fixture_slot_previously_created")
    now = time.monotonic()
    ticket = {
        "schema": lifecycle.journal.SCHEMA,
        "attempt_id": controller.epoch["slots"][unit],
        "nonce": str(uuid.uuid4()),
        "plan_ref": controller.package_ref,
        "source_sha256": binding["consumer_sha256"],
        "context_sha256": io.sha(io.canonical(context)),
        "host": socket.gethostname(),
        "boot_id": binding["boot_id"],
        "owner_generation": binding["generation"],
        "unit": "autostop-crm-forward-" + controller.epoch["slots"][unit] + ".service",
        "not_before": now,
        "expires_at": min(now + 600, binding["deadline_ns"] / 1e9),
    }
    store.create(ticket)
    lifecycle._publish(store.root / "context.json", context, controller.root)
    fixture.controller, fixture.logical_unit = controller, unit
    return unit, context, ticket


def admission(fixture, controller):
    """Declare the restore-proof boundary of this OS-only fixture explicitly."""
    from scripts import j1_forward_cold_authority as cold
    from scripts import j1_forward_cold_lifecycle as lifecycle

    directory = fixture.control / "attempts" / fixture.binding["attempt_id"]
    baseline = fixture._write(
        directory / "systemd-parent-baseline.json",
        {
            "controller_request_ref": fixture.parent.request_ref,
            "controller_launch_ref": fixture.parent.manifest_ref,
        },
    )
    plan = fixture._plan(fixture.binding["attempt_id"])
    plan.update(
        schema=cold.PLAN_SCHEMA,
        scope=cold.PLAN_SCOPE,
        baseline_ref=baseline,
        candidate_manager_sha=fixture.binding["candidate_manager_sha"],
        cold_authority_package_ref=controller.package_ref,
        dispatch_epoch_ref=controller.epoch_ref,
        cold_crm_spec_ref=baseline,
    )
    plan["candidate"]["revision"] = fixture.binding["candidate_crm_sha"]
    plan_ref = fixture._write(directory / "systemd-plan.json", plan)
    lifecycle._publish(
        controller.root / "release-admission.json",
        {
            "schema": "autostop.j1.cold-slot-release-admission.v1",
            "package_ref": controller.package_ref,
            "epoch_ref": controller.epoch_ref,
            "plan_ref": plan_ref,
            "backup_ref": baseline,
            "offhost_ref": baseline,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        },
        controller.root,
    )


def journal_counts(store):
    from scripts import j1_forward_cold_lifecycle as lifecycle

    lifecycle.coherent(store)  # First require the real complete event chain.
    counts = dict.fromkeys("SACE", 0)
    old = dict.fromkeys("SACE", False)
    for path in sorted((store.root / "events").iterdir()):
        state = json.loads(path.read_bytes())["state"]
        flags = {
            "S": state["dispatch_possible"],
            "A": state["acceptance"] is not None,
            "C": state["claim"] is not None,
            "E": state["execution_possible"],
        }
        for key in counts:
            counts[key] += int(flags[key] and not old[key])
        old = flags
    return counts


def guard_exit_status(unit):
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            "--no-pager",
            "--property=Result,ExecMainCode,ExecMainStatus",
        ],
        capture_output=True,
        check=True,
        timeout=3,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.decode().splitlines())
    require(
        fields == {"Result": "exit-code", "ExecMainCode": "1", "ExecMainStatus": "2"},
        "fixture_guard_did_not_refuse",
    )
    return 2


def run_case(mode):
    from scripts import j1_forward_cold_authority as cold
    from scripts import j1_forward_cold_lifecycle as lifecycle
    from scripts import j1_forward_release_rehearsal as rehearsal
    from scripts import j1_isolated_systemd_rehearsal as hosted

    fixture = None
    backend = None
    clean = False
    result = {"name": mode, "passed": False}
    started = time.monotonic()
    try:
        fixture = rehearsal.TraceFixture(candidates=True, ttl_seconds=600)
        with fixture.custody:
            package = fixture.produce()
            epoch = lifecycle.provision_epoch(
                package, fixture.runtime_bindings, provider=fixture.provider
            )
            backend = HostedFixtureBackend(fixture, mode)
            controller = lifecycle.Controller(
                package, epoch, provider=fixture.provider, backend=backend
            )
            unit, context, ticket = stage_slot(fixture, controller, backend)
            admission(fixture, controller)
        # /etc/systemd/system is validated by the real protected-root reader,
        # outside the fixture custodian that only admits its private directory.
        backend.stage(unit, context, ticket, controller.root)
        with fixture.custody:
            store = controller.store(unit)
            submitted = controller.submit(unit)
            a_published = time.monotonic()
            require(submitted["submitted"] and backend.injected, "fixture_a_missing")
            require(
                0 <= backend.wrapper_observed - backend.launch_finished < 10
                and backend.wrapper_observed - backend.launch_started < 10
                and a_published - backend.launch_finished < 10,
                "fixture_a_exceeded_original_ten_seconds",
            )
            if mode == "one_entry":
                controller.accept(unit)
                path = fixture.root / "probe-effects" / unit
                hosted.wait_until(path.exists, seconds=10)
                receipt = json.loads(path.read_bytes())
                require(receipt["package_ref"] == package, "fixture_entry_sentinel_binding")
                duplicate = controller.submit(unit)
                require(
                    duplicate["historical"] and backend.launches == 1, "fixture_duplicate_start"
                )
                require(journal_counts(store) == dict.fromkeys("SACE", 1), "fixture_sace_count")
                require(
                    len(list((fixture.root / "probe-effects").iterdir())) == 1,
                    "fixture_entry_count",
                )
            else:
                hosted.wait_until(
                    lambda: hosted.unit_status(ticket["unit"])["MainPID"] == "0", seconds=10
                )
                snapshot = lifecycle.coherent(store)
                require(
                    snapshot["state"]["claim"] is None
                    and snapshot["state"]["execution_possible"] is False
                    and not list((fixture.root / "probe-effects").iterdir()),
                    "refused_guard_entered_application",
                )
                require(
                    journal_counts(store) == {"S": 1, "A": 1, "C": 0, "E": 0},
                    "fixture_denied_count",
                )
                guard_exit_status(ticket["unit"])
            require(
                all(
                    (fixture.root / "barriers" / name).read_bytes() == cold.DENY_BYTES
                    for name in cold.UNITS
                )
                and (fixture.root / "ingress-held.json").exists(),
                "original_eight_or_hold_changed",
            )
            result.update(
                passed=True,
                journal=journal_counts(store),
                systemd_launches=backend.launches,
                mainpid_observed_within_seconds=round(
                    backend.wrapper_observed - backend.launch_started, 6
                ),
                a_published_within_seconds=round(a_published - backend.launch_finished, 6),
                guard_exit_status=None if mode == "one_entry" else 2,
                entered=mode == "one_entry",
                exec_condition="",
                exact_guard_exec_start=True,
                qualification_count_contract="One consume _active_parent call; independently tested by ColdConsumeCutTests. This hosted run does not instrument Python call counts.",
            )
    except BaseException as error:
        result.update(failure_type=type(error).__name__, failure_code=code(error))
        if backend is not None:
            try:
                result["unit_observation"] = backend.diagnostic()
            except BaseException as diagnostic_error:
                result["unit_observation_error"] = code(diagnostic_error)
    finally:
        try:
            if backend is not None:
                backend.stop()
            if fixture is not None:
                fixture.close()
                clean = True
        except BaseException as error:
            result.update(
                cleanup_failure_type=type(error).__name__, cleanup_failure_code=code(error)
            )
        result.update(cleanup_complete=clean, seconds=round(time.monotonic() - started, 3))
        result["passed"] = result["passed"] and clean
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    sha = boundary()
    output = args.output_dir.resolve()
    require(output == SOURCE / "output/release-rehearsal/cold-systemd", "cold_report_scope")
    root = Path("/run") / ("acs-" + uuid.uuid4().hex[:16])
    unix_path_budget(root)
    root.mkdir(mode=0o700)
    # The unchanged fixture/standalone guard agree on this protected temp root.
    tempfile.tempdir = str(root)
    os.environ.update(dict.fromkeys(("TMPDIR", "TEMP", "TMP"), str(root)))
    report = {
        "schema": "autostopcrm.hosted-cold-unit-rehearsal.v1",
        "source_sha": sha,
        "scope": "native_fixture package and kernel; real hosted systemd template, wrapper A and guard C/E",
        "production_acceptance": False,
        "cases": [],
        "ok": False,
        "source_sha256": {
            name: hashlib.sha256((SOURCE / "scripts" / name).read_bytes()).hexdigest()
            for name in (
                "j1_forward_cold_guard.py",
                "j1_forward_cold_lifecycle.py",
                "j1_forward_launch_permission.py",
            )
        },
        "limitations": [
            "Restore signatures and production source eligibility are explicit fixture premises.",
            "The artificial owned kernel and candidate are not actual production roles or an authenticated app-server.",
            "Python call-count instrumentation is not installed in the checked standalone guard.",
        ],
    }
    try:
        for mode in CASES:
            case = run_case(mode)
            report["cases"].append(case)
            require(case["passed"], "cold_hosted_case_failed")
        report["ok"] = True
    except BaseException as error:
        report.update(failure_type=type(error).__name__, failure_code=code(error))
    finally:
        report["cleanup_complete"] = bool(report["cases"]) and all(
            row["cleanup_complete"] for row in report["cases"]
        )
        if report["cleanup_complete"]:
            private_root(root)
            shutil.rmtree(root)
        report["ok"] = report["ok"] and report["cleanup_complete"]
        output.mkdir(mode=0o755, parents=True, exist_ok=True)
        path = output / "report.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o644)
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
