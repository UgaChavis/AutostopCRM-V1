"""Bounded cold execution slots using the existing S/A/C/E launch journal.

The original eight units/hold stay closed. A fresh dispatch epoch opens only
after six independently observed slot starts, CRM acceptance and durable HEAD.
The fixture backend owns only private children; production uses fixed units.
"""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import time
import types
import uuid
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as journal

STATE = native.STATE / "epochs"
NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")
BOOTSTRAP_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}


def slot_id(binding, unit):
    contract.need(unit in contract.ALLOWED, "slot_permanent_denied")
    return str(uuid.uuid5(NAMESPACE, binding["attempt_id"] + binding["generation"] + unit))


def _directory(path, root):
    path, root = Path(path), Path(root)
    contract.need(path.is_relative_to(root), "slot_path_outside_root")
    for current in (
        root,
        *[
            root.joinpath(*path.relative_to(root).parts[:index])
            for index in range(1, len(path.relative_to(root).parts) + 1)
        ],
    ):
        info = current.lstat()
        contract.need(
            stat.S_ISDIR(info.st_mode)
            and not current.is_symlink()
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o022,
            "slot_directory_custody",
        )


def _read(path, root, limit=2 * 1024**2):
    path = Path(path)
    _directory(path.parent, root)
    before = path.lstat()
    contract.need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_uid == os.geteuid()
        and not before.st_mode & 0o022
        and before.st_size <= limit,
        "slot_file_custody",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        contract.need(
            journal._metadata(before) == journal._metadata(os.fstat(descriptor)),
            "slot_read_open_changed",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        contract.need(
            len(raw) == before.st_size
            and journal._metadata(before)
            == journal._metadata(os.fstat(descriptor))
            == journal._metadata(path.lstat()),
            "slot_read_changed",
        )
        return raw
    finally:
        os.close(descriptor)


def _mkdir(path, root):
    path = Path(path)
    if path == Path(root):
        contract.need(path.is_dir(), "slot_root_missing")
        return
    _mkdir(path.parent, root)
    if not path.exists():
        path.mkdir(mode=0o700)
        journal._sync(path.parent)
    _directory(path, root)


def _publish(path, value, root, *, raw=False):
    path = Path(path)
    _mkdir(path.parent, root)
    data = value if raw else io.canonical(value)
    if path.exists():
        contract.need(_read(path, root, max(len(data), 1)) == data, "slot_immutable_conflict")
    else:
        journal._new(path, data)
    contract.need(_read(path, root, max(len(data), 1)) == data, "slot_publication_changed")
    return {"path": str(path), "sha256": io.sha(data)}


def _kernel(provider):
    if provider.scope == "production_native":
        return journal
    # Only custody differs in the private topology. The complete S/A/C/E
    # implementation remains the identical reviewed source, in its own namespace.
    root = provider.root
    module = types.ModuleType("_autostop_private_cold_launch")
    module.__file__ = journal.__file__
    exec(compile(Path(journal.__file__).read_bytes(), journal.__file__, "exec"), module.__dict__)

    def custody(path, owner):
        contract.need(owner == os.geteuid(), "fixture_slot_owner")
        _directory(path, root)
        return path.lstat()

    module._directory = custody
    return module


def coherent(store, wait=2):
    until = time.monotonic() + wait
    while True:
        try:
            with store.locked():
                return store.read()
        except BlockingIOError:
            contract.need(time.monotonic() < until, "slot_read_busy")
            time.sleep(0.02)


def _transition(store, ticket, event, payload):
    until = min(time.monotonic() + 2, ticket["expires_at"])
    while True:
        try:
            return store.transition(
                event,
                request_id=str(uuid.uuid5(uuid.UUID(ticket["nonce"]), event)),
                payload=payload,
                boot_id=ticket["boot_id"],
                generation=ticket["owner_generation"],
            )
        except BlockingIOError:
            # NB acquisition failed before reading/publishing any intent. Keep
            # identical operation identity; pending/corrupt journals never retry.
            contract.need(time.monotonic() < until, "slot_transition_busy")
            time.sleep(0.02)


def _state_root(provider, generation):
    return (
        STATE / generation if provider.scope == "production_native" else provider.root / "execution"
    )


def provision_epoch(package_ref, runtime_bindings, *, provider=None):
    """Bind already staged fresh configuration; no start, open or old-state copy."""
    provider = native.NativeProvider() if provider is None else provider
    value = native.verify_package(package_ref, provider=provider, required_scope=provider.scope)
    binding = value["package"]["binding"]
    root = _state_root(provider, binding["generation"])
    if provider.scope == "production_native":
        io.protected_directory(root.parent, private=True)
    if not root.exists():
        root.mkdir(mode=0o700)
        journal._sync(root.parent)
    document = {
        "schema": epochs.SCHEMA,
        "scope": provider.scope,
        **epochs.common(binding),
        "package_ref": package_ref,
        "runtime_bindings": runtime_bindings,
        "slots": {unit: slot_id(binding, unit) for unit in contract.ALLOWED},
        "journal_root": str(root / "journals"),
    }
    epochs.verify(document, value["package"], provider)
    _mkdir(root / "journals", root)
    return _publish(root / "epoch.json", document, root)


def _environment(policy, provider):
    return io.document(provider.read_reference(policy["environment_ref"]))["values"]


class NativeBackend:
    def __init__(self, provider):
        contract.need(
            provider.scope == "production_native" and type(provider) is native.NativeProvider,
            "slot_native_provider_required",
        )
        self.provider = provider

    def stage(self, unit, context, ticket, root):
        interpreter = context["interpreter"]
        contract.need(
            interpreter == str(Path("/usr/bin/python3").resolve()), "slot_bootstrap_interpreter"
        )
        environment = context["bootstrap_environment"]
        contract.need(
            not any(key in environment for key in ("LD_PRELOAD", "PYTHONHOME")),
            "slot_unsafe_bootstrap_environment",
        )
        entries = []
        for key, value in sorted(environment.items()):
            contract.need(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                and not any(c in value for c in "\n\r\x00"),
                "slot_environment_encoding",
            )
            escaped = (
                (key + "=" + value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
            )
            entries.append('Environment="' + escaped + '"\n')
        command = " ".join(context["worker_argv"])
        check = command.replace(" launch ", " check ", 1)
        raw = (
            "[Unit]\nDescription=AutoStop cold guarded execution slot\n[Service]\nType=exec\nUser=root\nWorkingDirectory=/\nRestart=no\n"
            + "".join(entries)
            + "ExecCondition="
            + check
            + "\nExecStart="
            + command
            + "\n"
        ).encode()
        path = Path("/etc/systemd/system") / ticket["unit"]
        io.protected_directory(path.parent)
        if path.exists():
            contract.need(io.read_protected(path) == raw, "slot_unit_conflict")
        else:
            journal._new(path, raw)
            path.chmod(0o644)
            journal._sync(path.parent)
        subprocess.run(
            ["/usr/bin/systemctl", "daemon-reload"],
            check=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )

    def launch(self, ticket, context):
        # S is already durable. Timeout/crash never implies never-dispatched.
        self._verify_unit(ticket, context, inactive=True)
        subprocess.run(
            ["/usr/bin/systemctl", "start", "--no-block", ticket["unit"]],
            check=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )

    def closed(self, ticket, context):
        self._verify_unit(ticket, context, inactive=True)

    def observe(self, ticket, context, *, started=False):
        from scripts import j1_forward_launch_native

        self._verify_unit(ticket, context, inactive=False)
        before = j1_forward_launch_native._unit(ticket["unit"])
        contract.need(
            before["Id"] == ticket["unit"]
            and before["LoadState"] == "loaded"
            and before["ActiveState"] in {"activating", "active"}
            and re.fullmatch("[0-9a-f]{32}", before["InvocationID"]),
            "slot_native_unit",
        )
        pid = int(before["MainPID"])
        result = _observe_process(pid, context, self.provider, started=started)
        contract.need(
            native.actor(pid)["cgroup"].split(":", 2)[-1] == before["ControlGroup"]
            and before["ControlGroup"].endswith("/" + ticket["unit"]),
            "slot_native_cgroup",
        )
        contract.need(
            j1_forward_launch_native._unit(ticket["unit"]) == before, "slot_native_unit_raced"
        )
        return {**result, "invocation_id": before["InvocationID"]}

    def _verify_unit(self, ticket, context, *, inactive):
        from scripts import j1_forward_authority

        contract.need(
            re.fullmatch(r"autostop-crm-forward-[0-9a-f-]{36}\.service", ticket["unit"]),
            "slot_unit_name",
        )
        properties = (*native.UNIT_PROPERTIES, "Restart", "FragmentPath", "Job")
        raw = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                ticket["unit"],
                "--no-pager",
                "--property=" + ",".join(properties),
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=5,
            check=False,
            env=BOOTSTRAP_ENVIRONMENT,
        )
        contract.need(raw.returncode == 0, "slot_loaded_unit_unavailable")
        fields = j1_forward_authority._systemd_fields(raw.stdout)
        contract.need(set(fields) == set(properties), "slot_loaded_unit_fields")
        command = " ".join(context["worker_argv"])
        check = command.replace(" launch ", " check ", 1)
        contract.need(
            fields["Id"] == ticket["unit"]
            and fields["LoadState"] == "loaded"
            and fields["NeedDaemonReload"] == "no"
            and fields["Type"] == "exec"
            and fields["Restart"] == "no"
            and fields["FragmentPath"] == "/etc/systemd/system/" + ticket["unit"]
            and native.commands(fields["ExecStart"]) == [command]
            and native.commands(fields["ExecCondition"]) == [check]
            and all(
                not fields[key]
                for key in (
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecStop",
                    "ExecStopPost",
                    "ExecReload",
                    "DropInPaths",
                    "EnvironmentFiles",
                )
            )
            and fields["User"] == "root"
            and fields["Group"] in {"", "root"}
            and not fields["SupplementaryGroups"]
            and fields["WorkingDirectory"] == "/",
            "slot_loaded_unit_changed",
        )
        import shlex

        rows = [entry.split("=", 1) for entry in shlex.split(fields["Environment"])]
        contract.need(
            all(len(row) == 2 for row in rows)
            and len({row[0] for row in rows}) == len(rows)
            and dict(rows) == context["bootstrap_environment"],
            "slot_loaded_environment_changed",
        )
        if inactive:
            contract.need(
                fields["ActiveState"] == "inactive" and fields["MainPID"] == "0",
                "slot_already_active_no_retry",
            )
            contract.need(fields["Job"] == "0", "slot_pending_job_no_retry")
        else:
            contract.need(fields["ActiveState"] in {"active", "activating"}, "slot_not_active")

    def observe_crm(self, reference, epoch_ref, package_ref):
        from scripts import j1_forward_cold_host

        return j1_forward_cold_host.verify_crm(
            reference, epoch_ref=epoch_ref, package_ref=package_ref
        )

    def parent_executor(self, request_ref):
        from scripts import j1_forward_cold_controller_native, j1_forward_launch_native

        request = io.document(io.referenced(request_ref))
        _store, snapshot, context = j1_forward_cold_controller_native.validate(
            request["attempt_id"]
        )
        contract.need(
            context["plan_ref"] == request_ref and snapshot["state"]["execution_possible"] is True,
            "crm_parent_not_entered",
        )
        accepted = snapshot["state"]["acceptance"]
        observed = j1_forward_launch_native.observe_worker(
            snapshot["ticket"], context, self_only=True
        )
        contract.need(
            observed == {key: accepted[key] for key in observed}, "crm_parent_incarnation_changed"
        )
        return observed, accepted["evidence_ref"]


def _observe_process(pid, context, provider, *, started):
    before = native.actor(pid)
    expected = context["application_argv"] if started else context["worker_argv"]
    cwd = context["application_cwd"] if started else context["worker_cwd"]
    credentials = context["credentials"] if started else context["bootstrap_credentials"]
    contract.need(
        before["argv_sha256"] == io.sha(b"\0".join(os.fsencode(arg) for arg in expected) + b"\0")
        and before["exe_ref"]
        == (
            context["application_interpreter_ref"]
            if started
            else context["bootstrap_interpreter_ref"]
        )
        and os.readlink(Path("/proc") / str(pid) / "cwd") == cwd
        and all(before[key] == credentials[key] for key in credentials),
        "slot_native_process",
    )
    environment = native._environment((Path("/proc") / str(pid) / "environ").read_bytes())
    volatile = {
        "INVOCATION_ID": r"[0-9a-f]{32}",
        "JOURNAL_STREAM": r"[0-9]+:[0-9]+",
        "SYSTEMD_EXEC_PID": r"[1-9][0-9]*",
    }
    for key, pattern in volatile.items():
        if key in environment:
            contract.need(
                re.fullmatch(pattern, environment.pop(key)), "slot_native_environment_volatile"
            )
    contract.need(
        environment == (context["environment"] if started else context["bootstrap_environment"]),
        "slot_native_environment",
    )
    if started and provider.scope == "production_native":
        _loaded_libraries(pid, context, provider)
    contract.need(native.actor(pid) == before, "slot_native_process_raced")
    return {"pid": pid, "start_ticks": before["start_ticks"]}


def _loaded_libraries(pid, context, provider):
    policy = io.document(provider.read_reference(context["policy_ref"]))
    accepted = {}
    for key in ("source_index_ref", "runtime_index_ref", "startup_index_ref"):
        for reference in io.document(provider.read_reference(policy[key]))["files"]:
            accepted[reference["path"]] = reference
    for line in (Path("/proc") / str(pid) / "maps").read_text().splitlines():
        fields = line.split(None, 5)
        if (
            len(fields) == 6
            and fields[5].startswith("/")
            and ("x" in fields[1] or ".so" in fields[5])
        ):
            path = fields[5]
            contract.need(path in accepted, "slot_unaccepted_loaded_library")
            provider.read_reference(accepted[path])


class FixtureBackend:
    """Only a known fixture's own fixed guard and candidate entrypoint execute."""

    def __init__(self, fixture):
        self.fixture, self.provider, self.started = fixture, fixture.provider, {}
        contract.need(self.provider.scope == "native_fixture", "slot_fixture_scope")

    def stage(self, _unit, _context, _ticket, _root):
        return None

    def closed(self, ticket, _context):
        contract.need(ticket["unit"] not in self.started, "fixture_slot_already_submitted")

    def launch(self, ticket, context):
        root = self.fixture.root
        contract.need(
            context["worker_argv"][4] == str(root / "source/scripts/j1_forward_cold_guard.py")
            and context["application_argv"][4:]
            == [str(root / "source/worker.py"), str(root), "candidate", context["logical_unit"]],
            "fixture_slot_fixed_command",
        )
        child = subprocess.Popen(
            context["worker_argv"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=root,
            env=context["bootstrap_environment"],
            close_fds=True,
        )
        self.fixture.children.append(child)
        self.fixture.pidfds[child.pid] = os.pidfd_open(child.pid)
        self.started[ticket["unit"]] = (child, uuid.uuid4().hex)

    def observe(self, ticket, context, *, started=False):
        contract.need(ticket["unit"] in self.started, "fixture_slot_unknown_worker")
        child, invocation = self.started[ticket["unit"]]
        contract.need(child.poll() is None, "fixture_slot_worker_exited")
        if started:
            ready = io.document(
                self.provider.read_path(
                    self.fixture.root / "probe-effects" / context["logical_unit"]
                )
            )
            contract.need(
                ready["policy_ref"] == context["policy_ref"]
                and ready["package_ref"] == context["package_ref"],
                "fixture_slot_application_not_ready",
            )
        return {
            **_observe_process(child.pid, context, self.provider, started=started),
            "invocation_id": invocation,
        }


class Controller:
    def __init__(self, package_ref, epoch_ref, *, provider=None, backend=None):
        self.provider = native.NativeProvider() if provider is None else provider
        self.package_ref, self.epoch_ref = package_ref, epoch_ref
        self.epoch = epochs.read_epoch(epoch_ref, self.provider)
        contract.need(self.epoch["package_ref"] == package_ref, "slot_epoch_package")
        self.root = _state_root(self.provider, self.epoch["generation"])
        contract.need(
            epoch_ref["path"] == str(self.root / "epoch.json")
            and self.epoch["journal_root"] == str(self.root / "journals"),
            "slot_epoch_fixed_path",
        )
        self.kernel = _kernel(self.provider)
        self.backend = NativeBackend(self.provider) if backend is None else backend
        contract.need(
            self.provider.scope == "native_fixture" or type(self.backend) is NativeBackend,
            "slot_native_backend_required",
        )

    def verify(self):
        contract.need(
            not (self.root / "safety-closed.json").exists(), "slot_safety_terminal_closed"
        )
        value = native.verify_package(
            self.package_ref, provider=self.provider, required_scope=self.provider.scope
        )
        contract.need(
            epochs.read_epoch(self.epoch_ref, self.provider) == self.epoch, "slot_epoch_changed"
        )
        fence = epochs.verify(self.epoch, value["package"], self.provider)
        return value, fence

    def store(self, unit):
        contract.need(
            unit in contract.ALLOWED and self.epoch["slots"][unit] == slot_id(self.epoch, unit),
            "slot_unit_identity",
        )
        return self.kernel.LaunchStore(
            Path(self.epoch["journal_root"]) / self.epoch["slots"][unit],
            owner=os.geteuid(),
            clock=time.monotonic,
        )

    def stage(self):
        value, _fence = self.verify()
        binding = value["package"]["binding"]
        for unit, policy in value["policies"].items():
            store = self.store(unit)
            bootstrap = (
                str(Path("/usr/bin/python3").resolve(strict=True))
                if self.provider.scope == "production_native"
                else policy["interpreter_ref"]["path"]
            )
            accepted_runtime = []
            for key in ("runtime_index_ref", "startup_index_ref"):
                accepted_runtime.extend(
                    io.document(self.provider.read_reference(policy[key]))["files"]
                )
            refs = [reference for reference in accepted_runtime if reference["path"] == bootstrap]
            contract.need(
                refs and all(reference == refs[0] for reference in refs),
                "slot_bootstrap_runtime_unaccepted",
            )
            self.provider.read_reference(refs[0])
            guard = (
                str(native.INSTALL / "guard.py")
                if self.provider.scope == "production_native"
                else str(self.provider.root / "source/scripts/j1_forward_cold_guard.py")
            )
            argv = [
                bootstrap,
                "-I",
                "-S",
                "-B",
                guard,
                "launch",
                "--epoch",
                binding["generation"],
                "--unit",
                unit,
                "--binding-sha256",
                contract.digest(binding),
            ]
            context = {
                "schema": "autostop.j1.cold-slot-context.v1",
                "scope": self.provider.scope,
                "logical_unit": unit,
                "binding": binding,
                "package_ref": self.package_ref,
                "epoch_ref": self.epoch_ref,
                "policy_ref": value["package"]["policies"][unit],
                "interpreter": bootstrap,
                "bootstrap_interpreter_ref": refs[0],
                "application_interpreter_ref": policy["interpreter_ref"],
                "bootstrap_environment": {
                    **BOOTSTRAP_ENVIRONMENT,
                    **(
                        dict.fromkeys(("TMPDIR", "TEMP", "TMP"), str(self.provider.root.parent))
                        if self.provider.scope == "native_fixture"
                        else {}
                    ),
                },
                "bootstrap_credentials": (
                    {"uid": 0, "gid": 0, "groups": [0]}
                    if self.provider.scope == "production_native"
                    else {
                        "uid": os.geteuid(),
                        "gid": os.getegid(),
                        "groups": sorted(os.getgroups()),
                    }
                ),
                "worker_argv": argv,
                "worker_cwd": "/"
                if self.provider.scope == "production_native"
                else str(self.provider.root),
                "application_argv": policy["argv"],
                "application_cwd": policy["cwd"],
                "credentials": policy["credentials"],
                "environment": _environment(policy, self.provider),
            }
            role_ref = self.epoch["runtime_bindings"][epochs.ROLES[unit]]
            contract.need(
                context["environment"].get(epochs.BINDING_ENV) == role_ref["path"]
                and context["environment"].get(epochs.SHA_ENV) == role_ref["sha256"],
                "slot_runtime_binding_required",
            )
            if store.root.exists():
                snapshot = coherent(store)
                contract.need(
                    _read(store.root / "context.json", self.root) == io.canonical(context),
                    "slot_context_conflict",
                )
                ticket = snapshot["ticket"]
            else:
                now = time.monotonic()
                ticket = {
                    "schema": journal.SCHEMA,
                    "attempt_id": self.epoch["slots"][unit],
                    "nonce": str(uuid.uuid4()),
                    "plan_ref": self.package_ref,
                    "source_sha256": binding["consumer_sha256"],
                    "context_sha256": io.sha(io.canonical(context)),
                    "host": socket.gethostname(),
                    "boot_id": binding["boot_id"],
                    "owner_generation": binding["generation"],
                    "unit": "autostop-crm-forward-" + self.epoch["slots"][unit] + ".service",
                    "not_before": now,
                    "expires_at": min(now + 600, binding["deadline_ns"] / 10**9),
                }
                store.create(ticket)
                _publish(store.root / "context.json", context, self.root)
            self.backend.stage(unit, context, ticket, self.root)
        return {"ok": True, "slots": self.epoch["slots"], "activation_authorized": False}

    prepare = stage

    def closed(self):
        self.verify()
        for unit in contract.ALLOWED:
            _store, snapshot, context = self.context(unit)
            contract.need(
                snapshot["state"]["dispatch_possible"] is False
                and snapshot["state"]["execution_possible"] is False,
                "slot_closed_stage_already_submitted",
            )
            self.backend.closed(snapshot["ticket"], context)
        return {"ok": True, "candidate_running": False}

    def issue(self, plan_ref, backup_ref, offhost_ref):
        from scripts import j1_forward_proofs as proofs
        from scripts import j1_forward_variant

        self.verify()
        plan = io.document(io.referenced(plan_ref))
        j1_forward_variant.validate_plan(plan, plan["base_sha256"])
        contract.need(
            plan["dispatch_epoch_ref"] == self.epoch_ref
            and plan["cold_authority_package_ref"] == self.package_ref,
            "slot_release_plan_binding",
        )
        baseline = io.document(io.referenced(plan["baseline_ref"]))
        payload = proofs.verify_proof(
            offhost_ref,
            "offhost_restore",
            plan_ref,
            baseline["review_key_ref"],
            plan_validator=j1_forward_variant.validate_plan,
        )
        captured = io.document(io.referenced(backup_ref))
        archived = io.document(
            io.referenced(io.reference(Path(plan_ref["path"]).parent / "control-closed.json"))
        )
        contract.need(
            payload.get("actual_restore") is True
            and payload.get("control_graph_restored") is True
            and payload.get("phase") == "closed"
            and payload.get("manifest_sha256") == captured["manifest_sha256"]
            and payload.get("plan_sha256") == plan_ref["sha256"]
            and payload.get("baseline_sha256") == plan["baseline_ref"]["sha256"]
            and payload.get("profile_sha256") == baseline["backup_profile_sha256"],
            "slot_independent_restore_required",
        )
        contract.need(
            payload.get("control_archive_manifest_sha256") == archived["manifest_sha256"]
            and type(payload.get("control_restore_receipt_sha256")) is str
            and contract.HEX.fullmatch(payload["control_restore_receipt_sha256"]),
            "slot_independent_control_restore_required",
        )
        return _publish(
            self.root / "release-admission.json",
            {
                "schema": "autostop.j1.cold-slot-release-admission.v1",
                "package_ref": self.package_ref,
                "epoch_ref": self.epoch_ref,
                "plan_ref": plan_ref,
                "backup_ref": backup_ref,
                "offhost_ref": offhost_ref,
                "candidate_may_have_written": True,
                "old_writer_allowed": False,
            },
            self.root,
        )

    def context(self, unit):
        store = self.store(unit)
        snapshot = coherent(store)
        raw = _read(store.root / "context.json", self.root)
        contract.need(io.sha(raw) == snapshot["ticket"]["context_sha256"], "slot_context_changed")
        context = io.document(raw)
        contract.need(
            context["package_ref"] == self.package_ref
            and context["epoch_ref"] == self.epoch_ref
            and context["logical_unit"] == unit,
            "slot_context_binding",
        )
        return store, snapshot, context

    def admitted(self, *, fresh=True):
        from scripts import j1_forward_cold_state

        admission = io.document(_read(self.root / "release-admission.json", self.root))
        contract.fields(
            admission,
            {
                "schema",
                "package_ref",
                "epoch_ref",
                "plan_ref",
                "backup_ref",
                "offhost_ref",
                "candidate_may_have_written",
                "old_writer_allowed",
            },
            "slot_admission_fields",
        )
        contract.need(
            admission["schema"] == "autostop.j1.cold-slot-release-admission.v1"
            and admission["candidate_may_have_written"] is True
            and admission["package_ref"] == self.package_ref
            and admission["epoch_ref"] == self.epoch_ref
            and admission["old_writer_allowed"] is False,
            "slot_admission_binding",
        )
        plan = io.document(self.provider.read_reference(admission["plan_ref"]))
        j1_forward_cold_state.validate_plan(plan, plan["base_sha256"])
        package = io.document(self.provider.read_reference(self.package_ref))
        contract.need(
            plan["cold_authority_package_ref"] == self.package_ref
            and plan["dispatch_epoch_ref"] == self.epoch_ref
            and plan["candidate"]["revision"] == package["binding"]["candidate_crm_sha"]
            and plan["candidate_manager_sha"] == package["binding"]["candidate_manager_sha"],
            "slot_final_plan_binding",
        )
        if fresh:
            from datetime import UTC, datetime

            from scripts import j1_forward_state

            now = datetime.now(UTC)
            contract.need(
                j1_forward_state._timestamp(plan["created_at"])
                <= now
                < j1_forward_state._timestamp(plan["deadline_utc"]),
                "slot_release_window_expired",
            )
        return admission

    def submit(self, unit):
        store, snapshot, context = self.context(unit)
        ticket = snapshot["ticket"]
        if snapshot["state"]["acceptance"] is not None:
            return {
                "ok": True,
                "submitted": True,
                "historical": True,
                "current_state_asserted": False,
                "native_acceptance": snapshot["state"]["acceptance"]["evidence_ref"],
            }
        self.verify()
        self.admitted()
        owner = snapshot["state"]["submit_owner"] or native.controller(os.getpid())
        result = _transition(store, ticket, "submit", {"owner": owner})
        if result["action_authorized"]:
            self.backend.launch(ticket, context)
        until = min(time.monotonic() + 10, ticket["expires_at"])
        while True:
            try:
                worker = self.backend.observe(ticket, context)
                break
            except (OSError, ValueError):
                contract.need(time.monotonic() < until, "slot_native_acceptance_unknown")
                time.sleep(0.02)
        evidence = {
            "schema": "autostop.j1.forward-launch-native-acceptance.v1",
            "ticket_sha256": snapshot["state"]["ticket_sha256"],
            **{
                key: ticket[key]
                for key in (
                    "attempt_id",
                    "nonce",
                    "boot_id",
                    "unit",
                    "context_sha256",
                    "source_sha256",
                )
            },
            **worker,
        }
        reference = _publish(store.root / "native-acceptance.json", evidence, self.root)
        contract.need(
            self.backend.observe(ticket, context) == worker, "slot_acceptance_worker_changed"
        )
        _transition(store, ticket, "accept", {"receipt": {**worker, "evidence_ref": reference}})
        return {
            "ok": True,
            "submitted": True,
            "historical": result["historical"],
            "native_acceptance": reference,
        }

    def revoke(self, unit):
        store, snapshot, _context = self.context(unit)
        return _transition(store, snapshot["ticket"], "revoke", {})

    def accept(self, unit):
        self.verify()
        store, snapshot, context = self.context(unit)
        ticket = snapshot["ticket"]
        until = min(time.monotonic() + 10, ticket["expires_at"])
        while True:
            try:
                snapshot = coherent(store)
                contract.need(snapshot["state"]["execution_possible"] is True, "slot_not_entered")
                observed = self.backend.observe(ticket, context, started=True)
                acceptance = snapshot["state"]["acceptance"]
                contract.need(
                    all(
                        observed[key] == acceptance[key]
                        for key in ("pid", "start_ticks", "invocation_id")
                    ),
                    "slot_started_incarnation_changed",
                )
                break
            except (OSError, ValueError):
                contract.need(time.monotonic() < until, "slot_runtime_acceptance_unknown")
                time.sleep(0.02)
        return _publish(
            self.root / "accepted" / (unit + ".json"),
            {
                "schema": "autostop.j1.cold-slot-runtime-observation.v1",
                "scope": self.provider.scope,
                "package_ref": self.package_ref,
                "epoch_ref": self.epoch_ref,
                "logical_unit": unit,
                "execution_unit": ticket["unit"],
                "policy_ref": context["policy_ref"],
                **observed,
                "business_acceptance": False,
            },
            self.root,
        )

    def ready(self):
        self.verify()
        accepted = {unit: self.accept(unit) for unit in contract.ALLOWED}
        crm_raw = _read(self.root / "crm-accepted.json", self.root)
        crm_ref = {"path": str(self.root / "crm-accepted.json"), "sha256": io.sha(crm_raw)}
        crm = self.backend.observe_crm(crm_ref, self.epoch_ref, self.package_ref)
        contract.need(io.canonical(crm) == crm_raw, "slot_crm_acceptance_changed")
        contract.need(
            crm["epoch_ref"] == self.epoch_ref and crm["package_ref"] == self.package_ref,
            "slot_crm_acceptance_required",
        )
        return {
            "ok": True,
            "accepted": accepted,
            "crm": crm,
            "acceptance_sha256": contract.digest(accepted),
            "activation_authorized": False,
        }

    def accept_crm(self, evidence_ref):
        # The native container observer is deliberately a separate boundary from
        # the six role tickets. Its fixed implementation is supplied by ColdHost.
        self.verify()
        entry = self.kernel.LaunchStore(
            self.root / "crm-start" / self.epoch["attempt_id"],
            owner=os.geteuid(),
            clock=time.monotonic,
        )
        entered = coherent(entry)
        contract.need(entered["state"]["execution_possible"] is True, "slot_crm_entry_not_consumed")
        entry_context = io.document(_read(entry.root / "context.json", self.root))
        contract.need(
            entry_context["plan_ref"] == self.admitted()["plan_ref"]
            and io.sha(io.canonical(entry_context)) == entered["ticket"]["context_sha256"],
            "slot_crm_entry_plan_changed",
        )
        evidence = self.backend.observe_crm(evidence_ref, self.epoch_ref, self.package_ref)
        plan = io.document(self.provider.read_reference(self.admitted()["plan_ref"]))
        contract.need(
            evidence.get("epoch_ref") == self.epoch_ref
            and evidence.get("package_ref") == self.package_ref
            and evidence.get("role") == "crm-embedded",
            "slot_crm_acceptance_required",
        )
        contract.need(
            evidence.get("spec_ref") == plan["cold_crm_spec_ref"], "slot_crm_spec_changed"
        )
        return _publish(self.root / "crm-accepted.json", evidence, self.root)

    def consume_crm_start(self, parent_request_ref):
        """Consume one final-plan CRM entry using the external parent A.

        Request E alone cannot reach this method's effect boundary. Lost reply
        after this E never permits a second replacement or a new operation ID.
        """
        self.verify()
        admission = self.admitted()
        plan = io.document(self.provider.read_reference(admission["plan_ref"]))
        baseline = io.document(self.provider.read_reference(plan["baseline_ref"]))
        contract.need(
            baseline["controller_request_ref"] == parent_request_ref, "crm_parent_request_binding"
        )
        observed, parent_acceptance_ref = self.backend.parent_executor(parent_request_ref)
        contract.need(
            observed["pid"] == os.getpid() and observed["start_ticks"] == native.birth(os.getpid()),
            "crm_not_parent_executor",
        )
        context = {
            "schema": "autostop.j1.cold-crm-entry-context.v1",
            "role": "crm-embedded",
            "request_ref": parent_request_ref,
            "parent_acceptance_ref": parent_acceptance_ref,
            "plan_ref": admission["plan_ref"],
            "spec_ref": plan["cold_crm_spec_ref"],
            "package_ref": self.package_ref,
            "epoch_ref": self.epoch_ref,
            "candidate": plan["candidate"],
        }
        folder = self.root / "crm-start"
        _mkdir(folder, self.root)
        store = self.kernel.LaunchStore(
            folder / self.epoch["attempt_id"], owner=os.geteuid(), clock=time.monotonic
        )
        if store.root.exists():
            snapshot = coherent(store)
            contract.need(
                _read(store.root / "context.json", self.root) == io.canonical(context),
                "crm_entry_context_changed",
            )
            contract.need(
                snapshot["state"]["execution_possible"] is False,
                "crm_replacement_already_consumed_no_retry",
            )
            ticket = snapshot["ticket"]
        else:
            from datetime import UTC, datetime

            from scripts import j1_forward_state

            now = time.monotonic()
            remaining = (
                j1_forward_state._timestamp(plan["deadline_utc"]) - datetime.now(UTC)
            ).total_seconds()
            ticket = {
                "schema": journal.SCHEMA,
                "attempt_id": self.epoch["attempt_id"],
                "nonce": str(uuid.uuid4()),
                "plan_ref": admission["plan_ref"],
                "source_sha256": plan["candidate"]["consumer_sha256"],
                "context_sha256": io.sha(io.canonical(context)),
                "host": socket.gethostname(),
                "boot_id": self.epoch["boot_id"],
                "owner_generation": self.epoch["generation"],
                "unit": "autostop-crm-forward-" + self.epoch["attempt_id"] + ".service",
                "not_before": now,
                "expires_at": min(
                    now + remaining,
                    io.document(self.provider.read_reference(self.package_ref))["binding"][
                        "deadline_ns"
                    ]
                    / 10**9,
                ),
            }
            store.create(ticket)
            _publish(store.root / "context.json", context, self.root)
        _transition(store, ticket, "submit", {"owner": native.controller(os.getpid())})
        evidence = {
            "schema": "autostop.j1.forward-launch-native-acceptance.v1",
            "ticket_sha256": coherent(store)["state"]["ticket_sha256"],
            **{
                key: ticket[key]
                for key in (
                    "attempt_id",
                    "nonce",
                    "boot_id",
                    "unit",
                    "context_sha256",
                    "source_sha256",
                )
            },
            **observed,
        }
        evidence_ref = _publish(store.root / "native-acceptance.json", evidence, self.root)
        contract.need(
            self.backend.parent_executor(parent_request_ref) == (observed, parent_acceptance_ref),
            "crm_parent_raced",
        )
        worker = {**observed, "evidence_ref": evidence_ref}
        _transition(store, ticket, "accept", {"receipt": worker})
        claim = _transition(store, ticket, "claim", {"worker": worker})
        contract.need(not claim["historical"], "crm_entry_already_claimed")
        self.verify()
        self.admitted()
        contract.need(
            self.backend.parent_executor(parent_request_ref) == (observed, parent_acceptance_ref),
            "crm_parent_raced",
        )
        entered = _transition(store, ticket, "enter", {"worker": worker})
        contract.need(entered["action_authorized"], "crm_replacement_already_consumed_no_retry")
        return {
            "ok": True,
            "entry_consumed": True,
            "plan_ref": admission["plan_ref"],
            "spec_ref": plan["cold_crm_spec_ref"],
            "ticket_ref": {
                "path": str(store.root / "ticket.json"),
                "sha256": coherent(store)["state"]["ticket_sha256"],
            },
        }

    def commit(self, commit_ref, *, store=None):
        from scripts import j1_forward_cold_state, j1_forward_variant
        from scripts import j1_forward_proofs as proofs

        ready, admission = self.ready(), self.admitted()
        plan = io.document(io.referenced(admission["plan_ref"]))
        commit = io.document(io.referenced(commit_ref))
        store = (
            j1_forward_cold_state.ColdStore(
                Path("/var/lib/autostop-crm-forward/state"), plan["base_sha256"]
            )
            if store is None
            else store
        )
        head = store.head()
        contract.need(
            head["sha256"] == commit_ref["sha256"]
            and head["record"] == commit
            and commit["plan"] == plan
            and commit["proofs"]["backup"] == admission["backup_ref"]
            and commit["proofs"]["offhost_restore"] == admission["offhost_ref"],
            "slot_durable_commit_required",
        )
        baseline = io.document(io.referenced(plan["baseline_ref"]))
        for kind in ("native_acceptance", "runtime_acceptance"):
            payload = proofs.verify_proof(
                commit["proofs"][kind],
                kind,
                admission["plan_ref"],
                baseline["review_key_ref"],
                plan_validator=j1_forward_variant.validate_plan,
            )
            contract.need(
                payload.get("phase") == "activated"
                and payload.get("plan_sha256") == admission["plan_ref"]["sha256"]
                and payload.get("baseline_sha256") == plan["baseline_ref"]["sha256"],
                "slot_independent_activation_required",
            )
            if kind == "native_acceptance":
                contract.need(
                    payload.get("cold_guards_executed") is True
                    and payload.get("execution_acceptance_sha256") == ready["acceptance_sha256"],
                    "slot_independent_native_required",
                )
            else:
                contract.need(
                    payload.get("runtime_checks_passed") is True
                    and payload.get("candidate_revision") == plan["candidate"]["revision"]
                    and payload.get("candidate_image") == plan["candidate"]["image"],
                    "slot_independent_runtime_required",
                )
        return _publish(
            self.root / "commit.json",
            {
                "schema": "autostop.j1.cold-slot-commit.v1",
                "epoch_ref": self.epoch_ref,
                "package_ref": self.package_ref,
                "commit_ref": commit_ref,
                "plan_ref": admission["plan_ref"],
                "acceptance_sha256": ready["acceptance_sha256"],
            },
            self.root,
        )

    def open_ingress(self, commit_ref, *, store=None):
        from scripts import j1_forward_cold_projection

        contract.need(store is not None, "slot_open_store_required")
        commit = io.document(io.referenced(self.commit(commit_ref, store=store)))
        _value, fence = self.verify()
        descriptor = epochs.locked_fence(fence, exclusive=True)
        try:
            contract.need(
                not (self.root / "safety-closed.json").exists(), "slot_safety_terminal_closed"
            )
            admission_ref = j1_forward_cold_projection.publish(
                commit["plan_ref"],
                commit_ref,
                self.epoch_ref,
                store=store,
                provider=self.provider,
                dispatch_fd=descriptor,
            )
            state = {
                "schema": "autostop.j1.cold-dispatch-state.v1",
                **epochs.common(self.epoch),
                "state": "open",
                "commit_ref": commit_ref,
                "plan_ref": commit["plan_ref"],
                "admission_ref": admission_ref,
            }
            current = io.document(_read(fence["state_path"], Path(fence["state_path"]).parent))
            contract.need(
                current == epochs.closed_state(self.epoch) or current == state,
                "slot_dispatch_state_conflict",
            )
            if current != state:
                temporary = Path(fence["state_path"]).with_name(".state-" + str(uuid.uuid4()))
                journal._new(temporary, io.canonical(state))
                temporary.chmod(0o644)
                os.replace(temporary, fence["state_path"])
                journal._sync(Path(fence["state_path"]).parent)
            contract.need(
                _read(fence["state_path"], Path(fence["state_path"]).parent) == io.canonical(state),
                "slot_dispatch_publication_changed",
            )
            return _publish(
                self.root / "opened.json",
                {
                    "ok": True,
                    "epoch_ref": self.epoch_ref,
                    "commit_ref": commit_ref,
                    "old_hold_released": False,
                    "old_barriers_opened": False,
                },
                self.root,
            )
        finally:
            os.close(descriptor)

    def finish(self, *, store=None):
        admission = self.admitted(fresh=False)
        committed = io.document(_read(self.root / "commit.json", self.root))
        opened = io.document(_read(self.root / "opened.json", self.root))
        contract.need(opened["commit_ref"] == committed["commit_ref"], "slot_finish_commit_binding")
        contract.need(store is not None, "slot_finish_store_required")
        contract.need(
            store.head()["sha256"] == committed["commit_ref"]["sha256"], "slot_finish_head_changed"
        )
        active = store._active_recovery()
        if active is not None:
            store.finish_recovery(active)
        contract.need(
            store.pending() is None and store._active_recovery() is None,
            "slot_finish_pending_not_retired",
        )
        return _publish(
            self.root / "complete.json",
            {
                "ok": True,
                "plan_ref": admission["plan_ref"],
                "commit_ref": committed["commit_ref"],
                "epoch_ref": self.epoch_ref,
                "old_hold_released": False,
                "old_barriers_opened": False,
            },
            self.root,
        )


def consume(controller, unit):
    """Actual worker gate: fresh native parent locks, then C/E under the short O."""
    controller.verify()
    controller.admitted()
    store, snapshot, context = controller.context(unit)
    ticket = snapshot["ticket"]
    until = min(time.monotonic() + 10, ticket["expires_at"])
    while snapshot["state"]["acceptance"] is None:
        contract.need(time.monotonic() < until, "slot_wait_acceptance_expired")
        time.sleep(0.02)
        snapshot = coherent(store)
    worker = snapshot["state"]["acceptance"]
    contract.need(
        worker["pid"] == os.getpid() and worker["start_ticks"] == native.birth(os.getpid()),
        "slot_unaccepted_worker",
    )
    result = _transition(store, ticket, "claim", {"worker": worker})
    contract.need(not result["historical"], "slot_claim_already_consumed")
    controller.verify()
    result = _transition(store, ticket, "enter", {"worker": worker})
    contract.need(result["action_authorized"], "slot_execution_already_consumed")
    return context
