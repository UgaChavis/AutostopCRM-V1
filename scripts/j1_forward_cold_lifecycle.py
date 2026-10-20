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
import threading
import time
import types
import uuid
from contextlib import contextmanager
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as journal

STATE = native.STATE / "epochs"
NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")
BOOTSTRAP_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
PARENT_REQUESTS = Path("/var/lib/autostop-crm-forward/cold-requests")
PARENT_JOURNALS = Path("/var/lib/autostop-crm-forward/cold-controller-launch")
PARENT_SOURCE = Path("/opt/autostopcrm")


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


def _transition_once(store, ticket, event, payload):
    # Called while parent O is held. Busy is a closed outcome, never an excuse
    # to wait under O or replay a child with a different operation identity.
    return store.transition(
        event,
        request_id=str(uuid.uuid5(uuid.UUID(ticket["nonce"]), event)),
        payload=payload,
        boot_id=ticket["boot_id"],
        generation=ticket["owner_generation"],
    )


def _transition_child(store, ticket, event, payload):
    return store._transition_locked(
        event,
        request_id=str(uuid.uuid5(uuid.UUID(ticket["nonce"]), event)),
        payload=payload,
        boot_id=ticket["boot_id"],
        generation=ticket["owner_generation"],
    )


def _parent_material(controller, admission):
    provider = controller.provider
    read = provider.read_reference
    plan = io.document(read(admission["plan_ref"]))
    baseline = io.document(read(plan["baseline_ref"]))
    request_ref, capsule_ref = (
        baseline["controller_request_ref"],
        baseline["controller_launch_ref"],
    )
    request, capsule = io.document(read(request_ref)), io.document(read(capsule_ref))
    binding = io.document(read(controller.package_ref))["binding"]
    fixture = provider.scope == "native_fixture"
    root = (
        provider.root / "parent-executor/journal"
        if fixture
        else PARENT_JOURNALS / binding["attempt_id"]
    )
    if fixture:
        contract.fields(
            capsule,
            {
                "schema",
                "scope",
                "request_ref",
                "context_ref",
                "acceptance_ref",
                "journal_files",
                "systemd_admission_claimed",
            },
            "fixture_parent_capsule_fields",
        )
        contract.need(
            capsule["schema"] == "autostop.j1.private-parent-execution.v1"
            and capsule["scope"] == "native_fixture"
            and capsule["systemd_admission_claimed"] is False
            and capsule["request_ref"] == request_ref
            and request["schema"] == "autostop.j1.private-parent-request.v1"
            and request["scope"] == "native_fixture"
            and request["binding"] == binding
            and request["consumer_sha256"] == plan["candidate"]["consumer_sha256"],
            "fixture_parent_binding",
        )
        names = {
            Path(ref["path"]).relative_to(root).as_posix(): ref for ref in capsule["journal_files"]
        }
        contract.need(len(names) == len(capsule["journal_files"]), "fixture_parent_duplicate_file")
        ticket = journal.validate_ticket(io.document(read(names["ticket.json"])))
        context_ref = capsule["context_ref"]
        evidence = io.document(read(capsule["acceptance_ref"]))
        acceptance = {key: evidence[key] for key in ("pid", "start_ticks", "invocation_id")}
        acceptance["evidence_ref"] = capsule["acceptance_ref"]
        clock = time.monotonic
    else:
        contract.fields(
            capsule,
            {
                "schema",
                "attempt_id",
                "request_ref",
                "captured_sequence",
                "files",
                "current_authority_asserted",
            },
            "parent_capsule_fields",
        )
        contract.need(
            capsule["schema"] == "autostop.j1.cold-controller-history-cut.v1"
            and capsule["current_authority_asserted"] is False
            and capsule["request_ref"] == request_ref
            and capsule["attempt_id"] == binding["attempt_id"]
            and request_ref["path"] == str(PARENT_REQUESTS / binding["attempt_id"] / "request.json")
            and request["schema"] == "autostop.j1.cold-controller-request.v1"
            and request["scope"] == "bounded_cold_prepare"
            and request["candidate"] == plan["candidate"]
            and request["candidate_manager_sha"] == plan["candidate_manager_sha"]
            and request["attempt_id"] == binding["attempt_id"]
            and request["boot_id"] == binding["boot_id"]
            and request["owner_generation"] == binding["generation"],
            "parent_request_binding",
        )
        names = capsule["files"]
        ticket = journal.validate_ticket(io.document(read(names["ticket.json"])))
        context_ref = names["context.json"]
        captured = journal._state(
            io.document(read(names["state.json"])), io.sha(io.canonical(ticket))
        )
        contract.need(
            type(capsule["captured_sequence"]) is int
            and 1 <= capsule["captured_sequence"] <= 128
            and captured["sequence"] == capsule["captured_sequence"]
            and captured["execution_possible"] is True,
            "parent_capsule_not_entered",
        )
        acceptance = captured["acceptance"]
        contract.need(
            set(names)
            == {
                "ticket.json",
                "context.json",
                "state.json",
                "native-acceptance.json",
                *(
                    "events/" + f"{number:020d}.json"
                    for number in range(1, captured["sequence"] + 1)
                ),
            },
            "parent_capsule_inventory",
        )
        tip = io.document(read(names["events/" + f"{captured['sequence']:020d}.json"]))
        contract.need(tip.get("state") == captured, "parent_capsule_tip_changed")
        clock = time.time
    context = io.document(read(context_ref))
    contract.fields(
        context,
        {"schema", "request_ref", "actor", "identity", "deployment_lock_identity"}
        if fixture
        else {
            "schema",
            "plan_ref",
            "consumers",
            "deployment_lock_identity",
            "interpreter",
            "worker_argv",
            "launch_argv",
            "worker_cwd",
            "launcher_cwd",
            "launcher_environment",
            "worker_environment",
            "issuer",
        },
        "parent_context_fields",
    )
    contract.need(
        ticket["attempt_id"] == binding["attempt_id"]
        and ticket["boot_id"] == binding["boot_id"]
        and ticket["owner_generation"] == binding["generation"]
        and ticket["plan_ref"] == request_ref
        and ticket["context_sha256"] == context_ref["sha256"]
        and ticket["source_sha256"] == plan["candidate"]["consumer_sha256"],
        "parent_ticket_binding",
    )
    store = controller.kernel.LaunchStore(root, owner=os.geteuid(), clock=clock)
    return store, ticket, context, acceptance, request, names


def _parent_native_actor(ticket, context, *, measure_executable=None):
    properties = ("Id", "LoadState", "ActiveState", "InvocationID", "MainPID", "ControlGroup")
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            ticket["unit"],
            "--no-pager",
            "--property=" + ",".join(properties),
        ],
        env=BOOTSTRAP_ENVIRONMENT,
        capture_output=True,
        check=False,
        timeout=3,
    )
    contract.need(result.returncode == 0 and len(result.stdout) <= 16384, "parent_unit_unavailable")
    rows = [line.partition("=") for line in result.stdout.decode("utf-8", "strict").splitlines()]
    values = {key: value for key, separator, value in rows if separator}
    contract.need(
        len(rows) == len(properties)
        and set(values) == set(properties)
        and values["Id"] == ticket["unit"]
        and values["LoadState"] == "loaded"
        and values["ActiveState"] in {"active", "activating"}
        and re.fullmatch(r"[0-9a-f]{32}", values["InvocationID"])
        and values["MainPID"].isdecimal(),
        "parent_unit_changed",
    )
    pid = int(values["MainPID"])
    observed = (
        native.actor(pid) if measure_executable is None else native._actor(pid, measure_executable)
    )
    proc = Path("/proc") / str(observed["pid"])
    environment = native._environment((proc / "environ").read_bytes())
    contract.need(
        observed["uid"] == 0
        and observed["exe_ref"] == context["interpreter"]
        and observed["argv_sha256"]
        == io.sha(b"\0".join(os.fsencode(arg) for arg in context["worker_argv"]) + b"\0")
        and os.readlink(proc / "cwd") == "/"
        and environment.get("LD_LIBRARY_PATH") == "/opt/autostop-sqlite/3.51.3/lib"
        and not any(key in environment for key in ("LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"))
        and values["ControlGroup"].endswith("/" + ticket["unit"])
        and any(
            line.split(":", 2)[-1] == values["ControlGroup"]
            for line in observed["cgroup"].splitlines()
        ),
        "parent_native_incarnation",
    )
    return {
        "pid": observed["pid"],
        "start_ticks": observed["start_ticks"],
        "invocation_id": values["InvocationID"],
    }


def _active_parent(controller):
    """Qualify once before O; remeasure parent and immutable versions under O."""
    admission = controller.admitted()
    material = native._ObservationSources(controller.provider)
    material.read_reference(
        {
            "path": str(controller.root / "release-admission.json"),
            "sha256": contract.digest(admission),
        }
    )
    plan = io.document(material.read_reference(admission["plan_ref"]))
    baseline = io.document(material.read_reference(plan["baseline_ref"]))
    for name in ("controller_request_ref", "controller_launch_ref"):
        material.read_reference(baseline[name])
    store, ticket, context, acceptance, request, names = _parent_material(controller, admission)
    current = coherent(store)
    journal.require(
        current["ticket"] == ticket
        and current["state"]["execution_possible"] is True
        and current["state"]["revoked"] is False
        and current["state"]["admission_closed"] is False
        and current["state"]["acceptance"] == current["state"]["claim"] == acceptance,
        "launch_parent_not_active",
    )
    for reference in names.values():
        material.read_reference(reference)
    fixture = controller.provider.scope == "native_fixture"
    interpreter = context["actor"]["exe_ref"] if fixture else context["interpreter"]
    material.read_opaque_reference(interpreter)
    parent_actor = native.actor(acceptance["pid"])
    contract.need(
        parent_actor["pid"] == acceptance["pid"]
        and parent_actor["start_ticks"] == acceptance["start_ticks"]
        and parent_actor["exe_ref"] == interpreter
        and (not fixture or parent_actor == context["actor"]),
        "fixture_parent_incarnation_changed" if fixture else "parent_native_incarnation",
    )
    if not fixture:
        for name, digest in context["consumers"].items():
            contract.need(
                re.fullmatch(
                    r"scripts/(?:j1_forward_[a-z_]+|j1_manager_[a-z_]+(?:_v2)?)\.py", name
                ),
                "parent_source_name",
            )
            material.read_reference({"path": str(PARENT_SOURCE / name), "sha256": digest})
        for name, reference in names.items():
            if name != "state.json":
                material.read_reference(
                    {"path": str(store.root / name), "sha256": reference["sha256"]}
                )
    # The one full immutable/native qualification belongs immediately before
    # paired O acquisition. Pure ticket/admission reads grant no application E.
    value, sources = native._observe_package(
        controller.package_ref,
        provider=controller.provider,
        required_scope=controller.provider.scope,
    )
    controller._verify_epoch(value)
    material.check_versions()
    binding, hold = value["package"]["binding"], value["hold"]
    marker = io.document(controller.provider.read_reference(hold["marker_ref"]))

    def executable(path):
        version = native._executable_version(Path(path).lstat())
        contract.need(
            str(path) == interpreter["path"] and material.versions[str(path)] == version,
            "parent_executable_changed",
        )
        return interpreter["sha256"], version

    def quiet_hold():
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(value["hold_checkpoint"]["database_ref"]["path"] + suffix).lstat()
            except FileNotFoundError:
                continue
            raise io.Refusal("cold_hold_database_not_quiet_checkpoint")

    def check_current(_snapshot):
        contract.need(controller.provider.boot() == binding["boot_id"], "parent_boot_changed")
        sources.check_versions()
        material.check_versions()
        quiet_hold()
        controller._verify_epoch(value)
        contract.need(controller.admitted() == admission, "parent_final_plan_changed")
        contract.need(
            not (controller.root / "safety-closed.json").exists(), "slot_safety_terminal_closed"
        )
        contract._binding(binding, now_ns=time.monotonic_ns())
        contract.need(
            io.document(controller.provider.read_reference(hold["marker_ref"])) == marker,
            "parent_hold_marker_changed",
        )
        actual_context = _read(
            store.root.parent / "context.json" if fixture else store.root / "context.json",
            controller.provider.root if fixture else PARENT_JOURNALS,
        )
        contract.need(io.sha(actual_context) == ticket["context_sha256"], "parent_context_changed")
        if fixture:
            contract.need(
                native._actor(acceptance["pid"], executable) == context["actor"]
                and context["request_ref"] == ticket["plan_ref"],
                "fixture_parent_incarnation_changed",
            )
            observed = context["identity"]
        else:
            expected_argv = [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                str(PARENT_SOURCE / "scripts/j1_forward_cold_release.py"),
                "controller",
                "--attempt",
                binding["attempt_id"],
            ]
            contract.need(
                context["schema"] == "autostop.j1.cold-controller-launch-context.v1"
                and context["plan_ref"] == ticket["plan_ref"]
                and context["worker_argv"] == expected_argv
                and context["worker_cwd"] == "/"
                and context["consumers"] == request["source"]["consumers"]
                and contract.digest(context["consumers"]) == ticket["source_sha256"]
                and context["deployment_lock_identity"] == binding["deployment_lock_identity"]
                and ticket["host"] == request["host"] == socket.gethostname(),
                "parent_context_scope",
            )
            observed = _parent_native_actor(ticket, context, measure_executable=executable)
        contract.need(
            observed == {key: acceptance[key] for key in observed}
            and hold["controller"] == {key: acceptance[key] for key in ("pid", "start_ticks")}
            and marker["binding"] == binding,
            "parent_acceptance_changed",
        )
        native.held_descriptor(
            hold["controller"], marker["deployment_descriptor"], binding["deployment_lock_identity"]
        )
        dispatch = Path(hold["dispatch_lock_ref"]["path"]).stat()
        native.held_descriptor(
            hold["controller"], hold["descriptor"], [dispatch.st_dev, dispatch.st_ino]
        )
        sources.check_versions()
        material.check_versions()
        quiet_hold()
        contract.need(controller.provider.boot() == binding["boot_id"], "parent_boot_changed")

    return store, ticket, acceptance, check_current


@contextmanager
def active_parent(controller):
    """D is held already; borrow parent O across only a new child admission."""
    store, ticket, acceptance, check_current = _active_parent(controller)
    with store.active_parent(ticket=ticket, acceptance=acceptance, check_current=check_current):
        yield (
            {key: acceptance[key] for key in ("pid", "start_ticks", "invocation_id")},
            acceptance["evidence_ref"],
        )


def _child_material(controller, store, ticket):
    sources = native._ObservationSources(controller.provider)
    sources.read_reference(
        {
            "path": str(store.root / "ticket.json"),
            "sha256": journal.sha(journal.canonical(ticket)),
        }
    )
    context = io.document(
        sources.read_reference(
            {"path": str(store.root / "context.json"), "sha256": ticket["context_sha256"]}
        )
    )
    contract.need(
        context["package_ref"] == controller.package_ref
        and context["epoch_ref"] == controller.epoch_ref,
        "slot_child_context_binding",
    )
    return sources


class _ChildAdmissionCut:
    """A private callback valid only while this operation owns both O locks."""

    def __init__(self, parent, check):
        self.parent, self._check = parent, check
        self.owner = (os.getpid(), threading.get_ident())
        self.active = True

    def checkpoint(self):
        contract.need(
            self.active and self.owner == (os.getpid(), threading.get_ident()),
            "slot_child_checkpoint_closed",
        )
        self._check()


@contextmanager
def child_admission(controller, store, ticket):
    """Acquire D -> parent O -> child O without waiting under either O.

    Only a Busy acquisition before any mutation can wait outside both locks.
    Once C can be published, errors are terminal/unknown and are never retried.
    """
    entered = False
    child_sources = _child_material(controller, store, ticket)
    parent_store, parent_ticket, acceptance, check_current = _active_parent(controller)
    until = min(time.monotonic() + 2, ticket["expires_at"])
    while True:
        try:
            with parent_store.active_parent(
                ticket=parent_ticket,
                acceptance=acceptance,
                check_current=check_current,
                child=store,
            ):
                child_sources.check_versions()
                contract.need(store.read()["ticket"] == ticket, "slot_child_ticket_changed")
                entered = True
                parent = (
                    {key: acceptance[key] for key in ("pid", "start_ticks", "invocation_id")},
                    acceptance["evidence_ref"],
                )

                def checkpoint():
                    snapshot = parent_store.read()
                    parent_store._fresh(
                        parent_ticket,
                        boot_id=parent_ticket["boot_id"],
                        generation=parent_ticket["owner_generation"],
                    )
                    contract.need(
                        snapshot["ticket"] == parent_ticket
                        and snapshot["state"]["execution_possible"] is True
                        and snapshot["state"]["revoked"] is False
                        and snapshot["state"]["admission_closed"] is False
                        and snapshot["state"]["acceptance"]
                        == snapshot["state"]["claim"]
                        == acceptance,
                        "slot_parent_changed_before_entry",
                    )
                    check_current(snapshot)
                    child_sources.check_versions()
                    contract.need(store.read()["ticket"] == ticket, "slot_child_ticket_changed")

                cut = _ChildAdmissionCut(parent, checkpoint)
                try:
                    yield cut
                finally:
                    cut.active = False
            return
        except BlockingIOError:
            if entered:
                raise
            contract.need(time.monotonic() < until, "slot_parent_child_acquisition_busy")
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
        raw = (
            "[Unit]\nDescription=AutoStop cold guarded execution slot\n[Service]\nType=exec\nUser=root\nWorkingDirectory=/\nRestart=no\n"
            + "".join(entries)
            + "ExecStart="
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
                "--all",
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
        contract.need(
            fields["Id"] == ticket["unit"]
            and fields["LoadState"] == "loaded"
            and fields["NeedDaemonReload"] == "no"
            and fields["Type"] == "exec"
            and fields["Restart"] == "no"
            and fields["FragmentPath"] == "/etc/systemd/system/" + ticket["unit"]
            and native.commands(fields["ExecStart"]) == [command]
            and not fields["ExecCondition"]
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
            context["plan_ref"] == request_ref
            and snapshot["state"]["execution_possible"] is True
            and snapshot["state"]["revoked"] is False
            and snapshot["state"]["admission_closed"] is False
            and snapshot["ticket"]["not_before"] <= time.time() < snapshot["ticket"]["expires_at"],
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


def _index_references(provider, reference):
    document = io.document(provider.read_reference(reference))
    if document.get("schema") == "autostop.j1.cold-runtime-index.v2":
        from scripts import j1_forward_cold_runtime_capsule

        return [
            {"path": path, "sha256": digest}
            for path, digest in j1_forward_cold_runtime_capsule.allowed_paths(document).items()
        ]
    return document["files"]


def _loaded_libraries(pid, context, provider, *, kernel_appserver_ref=None):
    policy = io.document(provider.read_reference(context["policy_ref"]))
    accepted = {}
    contract.need(
        {"runtime_index_ref", "startup_index_ref"} <= set(policy)
        and ("source_index_ref" in policy or "protocol_index_ref" in policy),
        "slot_library_index_scope",
    )
    for key in ("source_index_ref", "protocol_index_ref", "runtime_index_ref", "startup_index_ref"):
        if key not in policy:
            continue
        for reference in _index_references(provider, policy[key]):
            contract.need(
                reference["path"] not in accepted or accepted[reference["path"]] == reference,
                "slot_loaded_library_alias",
            )
            accepted[reference["path"]] = reference
    if kernel_appserver_ref is not None:
        contract.reference(kernel_appserver_ref)
        contract.need(
            policy.get("schema") == "autostop.j1.cold-kernel-policy.v2"
            and policy.get("appserver_ref") == kernel_appserver_ref,
            "kernel_appserver_library_binding",
        )
        path = kernel_appserver_ref["path"]
        contract.need(
            path not in accepted or accepted[path] == kernel_appserver_ref,
            "slot_loaded_library_alias",
        )
        accepted[path] = kernel_appserver_ref
    maps = Path("/proc") / str(pid) / "maps"
    observed, checked = _library_mappings(maps.read_text()), {}
    for fields in observed:
        path = fields[5]
        contract.need(path in accepted, "slot_unaccepted_loaded_library")
        info = Path(path).lstat()
        try:
            device = [int(part, 16) for part in fields[3].split(":")]
            inode = int(fields[4])
        except ValueError:
            raise io.Refusal("cold_slot_loaded_library_identity") from None
        contract.need(
            stat.S_ISREG(info.st_mode)
            and device == [os.major(info.st_dev), os.minor(info.st_dev)]
            and inode == info.st_ino,
            "slot_loaded_library_identity",
        )
        version = native._executable_version(info)
        if path not in checked:
            contract.opaque_reader(provider.read_reference)(accepted[path])
            checked[path] = version
        contract.need(checked[path] == version, "slot_loaded_library_changed")
    for path, version in checked.items():
        contract.need(
            native._executable_version(Path(path).lstat()) == version, "slot_loaded_library_changed"
        )
    contract.need(_library_mappings(maps.read_text()) == observed, "slot_loaded_mappings_changed")


def _library_mappings(raw):
    rows = [line.split(None, 5) for line in raw.splitlines()]
    return [
        row
        for row in rows
        if len(row) == 6 and row[5].startswith("/") and ("x" in row[1] or ".so" in row[5])
    ]


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

    def verify(self, observation=None):
        contract.need(
            not (self.root / "safety-closed.json").exists(), "slot_safety_terminal_closed"
        )
        if observation is None:
            value = native.verify_package(
                self.package_ref, provider=self.provider, required_scope=self.provider.scope
            )
        else:
            contract.need(type(observation) is native.PackageObservation, "slot_observation_type")
            value = observation.observe(self.package_ref, self.provider)
        return self._verify_epoch(value)

    def _verify_epoch(self, value):
        contract.need(
            not (self.root / "safety-closed.json").exists(), "slot_safety_terminal_closed"
        )
        contract._binding(value["package"]["binding"], now_ns=time.monotonic_ns())
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
                accepted_runtime.extend(_index_references(self.provider, policy[key]))
            refs = [reference for reference in accepted_runtime if reference["path"] == bootstrap]
            contract.need(
                refs and all(reference == refs[0] for reference in refs),
                "slot_bootstrap_runtime_unaccepted",
            )
            contract.opaque_reader(self.provider.read_reference)(refs[0])
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
        return self._closed_slots()

    def _closed_slots(self):
        """Read every closed slot; caller separately establishes native authority."""
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

    def accept(self, unit, *, _observation=None):
        self.verify() if _observation is None else self.verify(_observation)
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

    def telegram_frontiers(self, accepted, *, _observation=None):
        """Read both actual transport cuts; a started guard alone is insufficient."""
        from scripts import j1_forward_cold_telegram as telegram

        contract.need(self.provider.scope == "production_native", "telegram_native_scope")
        value, _fence = self.verify() if _observation is None else self.verify(_observation)
        package, binding = value["package"], value["package"]["binding"]
        source = io.document(self.provider.read_reference(package["source_package_ref"]))
        inputs = io.document(self.provider.read_reference(source["producer_inputs_ref"]))
        policy_ref = inputs["telegram_frontier_policy_ref"]
        policy = io.document(self.provider.read_reference(policy_ref))
        actors = {}
        for role, unit in telegram.UNITS.items():
            observation = io.document(self.provider.read_reference(accepted[unit]))
            contract.need(
                observation["package_ref"] == self.package_ref
                and observation["epoch_ref"] == self.epoch_ref
                and observation["policy_ref"] == package["policies"][unit],
                "telegram_slot_binding",
            )
            actors[role] = {key: observation[key] for key in telegram.ACTOR_FIELDS}

        def sample(role):
            unit = telegram.UNITS[role]
            _store, snapshot, context = self.context(unit)
            observed = self.backend.observe(snapshot["ticket"], context, started=True)
            contract.need(
                snapshot["state"]["execution_possible"] is True, "telegram_slot_not_entered"
            )
            return {
                **actors[role],
                **observed,
                "boot_id": self.provider.boot(),
                "source_revision": binding["candidate_manager_sha"],
                "started_monotonic_ns": observed["start_ticks"] * 10**9 // os.sysconf("SC_CLK_TCK"),
            }

        def publish(name, document):
            return _publish(
                self.root / (name + "-" + contract.digest(document) + ".json"), document, self.root
            )

        until = min(time.monotonic() + 10, binding["deadline_ns"] / 1e9)
        while True:
            try:
                result = telegram.observe(
                    binding,
                    policy_ref=policy_ref,
                    policy=policy,
                    actors_by_role=actors,
                    sample_process=sample,
                    publish=publish,
                    now_ns=time.monotonic_ns(),
                )
                break
            except FileNotFoundError:
                contract.need(time.monotonic() < until, "telegram_transport_not_ready")
                time.sleep(0.1)
        self.verify() if _observation is None else self.verify(_observation)
        result["source"] = {
            "source_package_ref": package["source_package_ref"],
            "source_package": source,
            "producer_inputs_ref": source["producer_inputs_ref"],
            "producer_inputs": inputs,
        }
        return result

    def ready(self):
        with native.PackageObservation(
            self.package_ref, provider=self.provider, required_scope=self.provider.scope
        ) as observation:
            result = self._ready(observation)
            value = observation.value
            # The context's full-byte exit must succeed before result escapes.
        self._verify_epoch(value)
        return result

    def _ready(self, observation):
        self.verify(observation)
        accepted = {unit: self.accept(unit, _observation=observation) for unit in contract.ALLOWED}
        crm_raw = _read(self.root / "crm-accepted.json", self.root)
        crm_ref = {"path": str(self.root / "crm-accepted.json"), "sha256": io.sha(crm_raw)}
        crm = self.backend.observe_crm(crm_ref, self.epoch_ref, self.package_ref)
        contract.need(io.canonical(crm) == crm_raw, "slot_crm_acceptance_changed")
        contract.need(
            crm["epoch_ref"] == self.epoch_ref and crm["package_ref"] == self.package_ref,
            "slot_crm_acceptance_required",
        )
        result = {
            "ok": True,
            "accepted": accepted,
            "crm": crm,
            "acceptance_sha256": contract.digest(accepted),
            "activation_authorized": False,
        }
        if self.provider.scope == "production_native":
            result["telegram_frontiers"] = self.telegram_frontiers(
                accepted, _observation=observation
            )
        return result

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
        self.admitted()
        with child_admission(self, store, ticket) as cut:
            contract.need(cut.parent == (observed, parent_acceptance_ref), "crm_parent_raced")
            claim = _transition_child(store, ticket, "claim", {"worker": worker})
            contract.need(not claim["historical"], "crm_entry_already_claimed")
            cut.checkpoint()
            entered = _transition_child(store, ticket, "enter", {"worker": worker})
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
    with child_admission(controller, store, ticket) as cut:
        result = _transition_child(store, ticket, "claim", {"worker": worker})
        contract.need(not result["historical"], "slot_claim_already_consumed")
        cut.checkpoint()
        result = _transition_child(store, ticket, "enter", {"worker": worker})
        contract.need(result["action_authorized"], "slot_execution_already_consumed")
    return context
