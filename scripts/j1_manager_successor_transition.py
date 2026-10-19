"""Native producer for an explicit, warm-only MCP successor authority v2.

There is no operational CLI. The release controller calls these boundaries
while retaining its exact deployment-lock descriptor. Inert staging precedes
the backup; selector/drop-in publication and admission follow independently
verified closure, data/control restore and Store stop. No cold authority or
historical exited-process waiver is implemented here.
"""

from __future__ import annotations

import copy
import os
import stat
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_authority as authority
from scripts import j1_forward_closure as closure
from scripts import j1_forward_io as io
from scripts import j1_forward_mixed_authority as mixed
from scripts import j1_forward_proofs as proofs
from scripts import j1_forward_state as state
from scripts import j1_manager_successor_guard_v2 as guard
from scripts import j1_manager_successor_native_v2 as native
from scripts import j1_manager_transition_source as source

INSTALL_RECEIPT = Path(guard.CONFIG) / "installation.json"
SOURCE_INPUTS = Path(guard.CONFIG) / "source-inputs.json"
DEPLOY_LOCK = Path("/opt/autostopcrm/.autostop-deploy.lock")


def derive_capsule(original):
    """The only v2 source delta: explicit contract and versioned guard path."""
    value = copy.deepcopy(original)
    io.require(
        value.get("contract") == "manager_mcp_only_preserved_warm_v1", "successor_source_contract"
    )
    value["contract"] = guard.CONTRACT
    value["target"]["guard"] = guard.GUARD
    value["sha256"] = io.sha(
        io.canonical({key: item for key, item in value.items() if key != "sha256"})
    )
    return value


def _profile(old, capsule, identity, runtime_ref):
    old_refs = [row for row in old["runtime_files"] if row["path"] == old["runtime_receipt"]]
    io.require(len(old_refs) == 1, "successor_old_runtime_reference")
    return {
        **old,
        "manager_revision": guard.REVISION,
        "source_root": guard.SOURCE_ROOT,
        "source_identity": identity,
        "selector": guard.SELECTOR,
        "manifest_sha256": capsule["target"]["manifest_sha256"],
        "stable_environment_sha256": capsule["target"]["environment"]["after_sha256"],
        "runtime_receipt": guard.RUNTIME,
        "runtime_files": [
            runtime_ref if row == old_refs[0] else row for row in old["runtime_files"]
        ],
        "startup_locations": [
            {**row, "path": guard.SOURCE_ROOT} if row["path"] == old["source_root"] else row
            for row in old["startup_locations"]
        ],
    }


def _runtime(old, capsule_ref, capsule, identity):
    old_ref = [row for row in old["runtime_files"] if row["path"] == old["runtime_receipt"]]
    io.require(len(old_ref) == 1, "successor_old_runtime_reference")
    return {
        "schema": "autostop.j1.manager-mcp-runtime-acceptance.v1",
        "scope": guard.CONTRACT,
        "revision": guard.REVISION,
        "source_root": guard.SOURCE_ROOT,
        "source_identity": identity,
        "manager_origin": guard.SOURCE_ROOT + "/autostop_manager/__init__.py",
        "manifest_sha256": capsule["target"]["manifest_sha256"],
        "interpreter": old["interpreter"],
        "interpreter_sha256": old["interpreter_sha256"],
        "stable_input_environment_sha256": capsule["target"]["environment"]["after_sha256"],
        "startup_paths": [
            guard.SOURCE_ROOT if row["path"] == old["source_root"] else row["path"]
            for row in old["startup_locations"]
        ],
        "complete_startup_path_enumerated": True,
        "startup_hooks_reviewed": True,
        "dependency_runtime_sealed": True,
        "application_imported": False,
        "source_capsule_sha256": capsule_ref["sha256"],
        "predecessor_runtime_sha256": old_ref[0]["sha256"],
    }


class NativeBackend:
    """Effects are fixed physical file publication; no start, stop or guard call."""

    @staticmethod
    def _directory(path):
        path = Path(path)
        if not path.exists():
            NativeBackend._directory(path.parent)
            path.mkdir(mode=0o700)
            io.fsync_directory(path.parent)
        io.protected_directory(path)

    @contextmanager
    def locked(self, deployment_fd, *, deadline):
        import fcntl

        io.require(type(deployment_fd) is int and deployment_fd > 2, "successor_deployment_fd")
        io.read_protected(DEPLOY_LOCK)
        expected, actual = DEPLOY_LOCK.lstat(), os.fstat(deployment_fd)
        io.require(
            (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino)
            and stat.S_ISREG(actual.st_mode),
            "successor_deployment_lock_identity",
        )
        fcntl.flock(deployment_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        io.require(time.monotonic() < deadline, "successor_deadline")
        self.deadline = deadline
        # The controller keeps this borrowed descriptor/lock after return.
        self._directory(Path(guard.STATE_DIR))
        lock = Path(guard.STATE_DIR) / "lifecycle.lock"
        self.publish(lock, b"")
        before = lock.lstat()
        descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            io.require(
                (before.st_dev, before.st_ino)
                == (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino),
                "successor_lifecycle_replaced",
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            io.require(time.monotonic() < deadline, "successor_deadline")
            yield
            io.require(
                time.monotonic() < deadline and lock.lstat().st_ino == before.st_ino,
                "successor_lifecycle_changed",
            )
        finally:
            os.close(descriptor)

    def publish(self, path, raw, *, mode=0o600):
        io.require(time.monotonic() < self.deadline, "successor_deadline")
        path = Path(path)
        self._directory(path.parent)
        if path.exists() or path.is_symlink():
            io.require(
                io.read_protected(path) == raw and stat.S_IMODE(path.lstat().st_mode) == mode,
                "successor_existing_artifact_changed",
            )
        else:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            io.fsync_directory(path.parent)
        io.require(io.read_protected(path) == raw, "successor_artifact_readback")
        return {"path": str(path), "sha256": io.sha(raw)}

    def selector(self):
        io.require(time.monotonic() < self.deadline, "successor_deadline")
        path, target = Path(guard.SELECTOR), guard.SOURCE_ROOT
        self._directory(path.parent)
        if path.exists() or path.is_symlink():
            io.require(
                path.is_symlink() and os.readlink(path) == target, "successor_selector_foreign"
            )
        else:
            os.symlink(target, path)
            io.fsync_directory(path.parent)
        io.require(path.is_symlink() and os.readlink(path) == target, "successor_selector_readback")

    @staticmethod
    def read_optional(path):
        path = Path(path)
        return io.read_protected(path) if path.exists() or path.is_symlink() else None

    @staticmethod
    def closure(ownership):
        return closure.observe_closed(ownership, force_full=True)

    @staticmethod
    def preserve(predecessor, ownership, *, running=False):
        return authority.verify_preserved(
            predecessor,
            running=running,
            owned_closure=None
            if running
            else {key: ownership[key] for key in ("dropins", "bytes_sha256")},
        )

    @staticmethod
    def reload(deadline):
        closure.RUNNER.reload(deadline)


def stage_locked(
    inputs_ref,
    package,
    predecessor_ref,
    ownership,
    *,
    deployment_fd,
    deadline,
    backend=None,
    mode="closed",
):
    """Inert staging only. Fresh original warm authority is checked before effects."""
    backend = NativeBackend() if backend is None else backend
    io.require(
        mode in {"closed", "inert_before_prepare"}
        and (type(ownership) is dict if mode == "closed" else ownership is None),
        "successor_staging_mode",
    )
    inert = mode == "inert_before_prepare"
    io.require(time.monotonic() < deadline, "successor_deadline")
    predecessor = io.document(io.referenced(predecessor_ref))

    # This is the compatibility STOP for exited accepted actors, unknown warm
    # ingress, source drift or cold replacement. No new file is published first.
    def observe():
        if inert:
            return backend.preserve(predecessor, None, running=True)
        backend.preserve(predecessor, ownership)
        return backend.closure(ownership)

    observe()
    source.verify(inputs_ref["path"], inputs_ref["sha256"], package)
    package = Path(package)
    capsule = derive_capsule(io.document(source.stable_read(package / "capsule.json")))
    index = io.document(source.stable_read(package / "source-index.json"))
    io.require(
        capsule["predecessor"]["baseline_sha256"] == predecessor_ref["sha256"]
        and capsule["candidate"]["revision"] == guard.REVISION,
        "successor_package_predecessor",
    )
    policy_ref = next(
        row for row in capsule["predecessor"]["references"] if row["path"] == guard.POLICY
    )
    old = io.document(io.referenced(policy_ref))["bindings"][guard.UNIT]
    names = [row["path"] for row in index["files"]] + [
        "REVISION",
        "MANIFEST.sha256",
        "MANIFEST.files0",
    ]
    files = {name: source.stable_read(package / "source" / name) for name in names}
    controller_files = mixed.consumer_inventory()
    package_hash = io.sha(
        io.canonical(
            {
                "source": {name: io.sha(raw) for name, raw in files.items()},
                "capsule": io.sha(source.stable_read(package / "capsule.json")),
                "source_index": io.sha(source.stable_read(package / "source-index.json")),
                "controller_index": io.sha(
                    source.stable_read(package / "controller-source-index.json")
                ),
                "inputs": inputs_ref["sha256"],
            }
        )
    )
    with backend.locked(deployment_fd, deadline=deadline):
        observe()
        mode_map = {row["path"]: int(row["mode"][-3:], 8) for row in index["files"]}
        for name, raw in files.items():
            backend.publish(Path(guard.SOURCE_ROOT) / name, raw, mode=mode_map.get(name, 0o644))
        identity = authority._identity(Path(guard.SOURCE_ROOT))
        # Load the exact reviewed base bytes, never the configured namespace.
        backend.publish(
            Path(guard.INSTALL) / "base-guard.py",
            io.read_protected(Path(guard.__file__).with_name("j1_manager_successor_guard.py")),
        )
        backend.publish(
            Path(guard.INSTALL) / "base-native.py",
            io.read_protected(Path(native.__file__).with_name("j1_manager_successor_native.py")),
        )
        guard_ref = backend.publish(guard.GUARD, io.read_protected(Path(guard.__file__)))
        native_ref = backend.publish(guard.NATIVE, io.read_protected(Path(native.__file__)))
        capsule_ref = backend.publish(guard.CAPSULE, io.canonical(capsule))
        index_ref = backend.publish(guard.INDEX, io.canonical(index))
        baseline_ref = backend.publish(guard.BASELINE, io.referenced(predecessor_ref))
        runtime_ref = backend.publish(
            guard.RUNTIME, io.canonical(_runtime(old, capsule_ref, capsule, identity))
        )
        binding = {
            "schema": "autostop.j1.manager-mcp-native-binding.v1",
            "contract": guard.CONTRACT,
            "attempt_id": capsule["attempt_id"],
            "host": guard.HOST,
            "unit": guard.UNIT,
            "guard": guard_ref,
            "native": native_ref,
            "capsule": capsule_ref,
            "source_index": index_ref,
            "predecessor_baseline": baseline_ref,
            "profile": _profile(old, capsule, identity, runtime_ref),
        }
        binding_ref = backend.publish(guard.BINDING, io.canonical(binding))
        installed_inputs = backend.publish(SOURCE_INPUTS, io.referenced(inputs_ref))
        receipt = {
            "schema": mixed.INSTALL_SCHEMA,
            "contract": guard.CONTRACT,
            "attempt_id": capsule["attempt_id"],
            "host": guard.HOST,
            "manager_revision": guard.REVISION,
            "binding_ref": binding_ref,
            "source_inputs_ref": installed_inputs,
            "source_package_sha256": package_hash,
            "predecessor_ref": predecessor_ref,
            "native_acceptance": False,
            "activation_authorized": False,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
            "controller_files": controller_files,
        }
        observe()
        io.require(mixed.consumer_inventory() == controller_files, "successor_controller_changed")
        return backend.publish(INSTALL_RECEIPT, io.canonical(receipt))


def _context(binding, binding_ref, boot):
    return {
        "contract": guard.CONTRACT,
        "attempt_id": binding["attempt_id"],
        "host": guard.HOST,
        "boot_id": boot,
        "unit": guard.UNIT,
        "binding_sha256": binding_ref["sha256"],
        "guard_sha256": binding["guard"]["sha256"],
        "native_sha256": binding["native"]["sha256"],
        "source_capsule_sha256": binding["capsule"]["sha256"],
        "predecessor_baseline_sha256": binding["predecessor_baseline"]["sha256"],
    }


def issue_locked(
    transition_ref,
    plan_ref,
    ownership,
    backup_ref,
    offhost_ref,
    store_stop_ref,
    *,
    deployment_fd,
    deadline,
    backend=None,
):
    """Publish real evidence wrappers and prestart admission, armed state last."""
    backend = NativeBackend() if backend is None else backend
    plan = io.document(io.referenced(plan_ref))
    mixed.validate_plan(plan, plan["base_sha256"])
    io.require(
        plan["manager_transition_ref"] == transition_ref
        and plan["attempt_id"] == ownership["attempt_id"],
        "successor_plan_binding",
    )
    now = datetime.now(UTC)
    io.require(
        state_time(plan["created_at"]) <= now < state_time(plan["deadline_utc"]),
        "successor_release_window",
    )
    receipt, binding = mixed.load_transition(transition_ref)
    io.require(binding["attempt_id"] == plan["attempt_id"], "successor_attempt_binding")
    baseline = io.document(io.referenced(plan["baseline_ref"]))
    captured = io.document(io.referenced(backup_ref))
    archived = io.document(
        io.read_protected(Path(ownership["attempt_dir"]) / "control-closed.json")
    )
    payload = proofs.verify_proof(
        offhost_ref,
        "offhost_restore",
        plan_ref,
        baseline["review_key_ref"],
        plan_validator=mixed.validate_plan,
    )
    io.require(
        payload.get("plan_sha256") == plan_ref["sha256"]
        and payload.get("baseline_sha256") == plan["baseline_ref"]["sha256"]
        and payload.get("actual_restore") is True
        and payload.get("phase") == "closed"
        and payload.get("manifest_sha256") == captured.get("manifest_sha256")
        and payload.get("profile_sha256") == baseline["backup_profile_sha256"]
        and payload.get("control_graph_restored") is True
        and payload.get("control_archive_manifest_sha256") == archived.get("manifest_sha256")
        and state._hash(payload.get("control_restore_receipt_sha256")),
        "successor_independent_restore_required",
    )
    drain = io.document(io.referenced(store_stop_ref))
    io.require(
        drain.get("attempt_id") == plan["attempt_id"]
        and drain.get("baseline_ref") == plan["baseline_ref"]
        and drain.get("consumer_sha256") == ownership["consumer_sha256"]
        and drain.get("native_baseline_sha256") == ownership["baseline_sha256"]
        and drain.get("process_scope_ref") == ownership["process_scope_ref"]
        and drain.get("result", {}).get("ok") is True,
        "successor_store_receipt_binding",
    )
    facts = {
        key: drain["result"].get(key)
        for key in (
            "manager_revision",
            "mode",
            "stop_permitted",
            "store_workers_drained",
            "worker_drain_supported",
        )
    }
    facts["manager_revision"] = drain["result"].get("identity", {}).get("revision")
    guard._store_facts(facts)
    with backend.locked(deployment_fd, deadline=deadline):
        observation = backend.closure(ownership)
        io.require(
            observation.get("attempt_id") == plan["attempt_id"]
            and observation.get("boot_id") == ownership["boot_id"],
            "successor_closure_binding",
        )
        backend.selector()
        backend.publish(guard.DROPIN, guard.render_dropin(receipt["binding_ref"]["sha256"]))
        backend.reload(deadline)
        observed = mixed.observe(
            transition_ref,
            running=False,
            owned_closure={key: ownership[key] for key in ("dropins", "bytes_sha256")},
        )
        closure_path = Path(ownership["attempt_dir"]) / "manager-successor-closure.json"
        previous = backend.read_optional(closure_path)
        if previous is not None:
            prior = io.document(previous)
            io.require(
                prior.get("attempt_id") == ownership["attempt_id"]
                and prior.get("boot_id") == ownership["boot_id"]
                and prior.get("process_scope_ref") == ownership["process_scope_ref"],
                "successor_prior_closure_binding",
            )
        closure_ref = backend.publish(closure_path, previous or io.canonical(observation))
        context = _context(binding, receipt["binding_ref"], ownership["boot_id"])
        evidence = {
            "source_verification": [transition_ref, binding["capsule"], binding["source_index"]],
            "runtime": [
                next(
                    row
                    for row in binding["profile"]["runtime_files"]
                    if row["path"] == guard.RUNTIME
                )
            ],
            "preserved_authority": [transition_ref, binding["predecessor_baseline"]],
            "closure": [closure_ref],
            "backup": [backup_ref],
            "offhost_restore": [offhost_ref],
            "store_stop": [store_stop_ref],
        }
        wrapper_refs = {}
        for kind, required in guard.PROOF_FACTS.items():
            wrapper = {
                "schema": f"autostop.j1.manager-mcp-prestart-{kind}.v1",
                **context,
                "facts": facts if kind == "store_stop" else required,
                "evidence": evidence[kind],
                "native_acceptance": False,
                "old_writer_allowed": False,
            }
            wrapper_refs[kind] = backend.publish(
                Path(guard.STATE_DIR) / "proofs" / (kind + ".json"), io.canonical(wrapper)
            )
        # These are observations from actual native producers, not caller bools.
        io.require(
            set(observed["immutable"]["profiles"]) == set(guard.UNITS), "successor_profile_count"
        )
        backend.closure(ownership)
        io.require(datetime.now(UTC) < state_time(plan["deadline_utc"]), "successor_release_window")
        admission = {
            "schema": "autostop.j1.manager-mcp-prestart-admission.v1",
            **context,
            "proofs": wrapper_refs,
            "prestart_authorized": True,
            "native_acceptance": False,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        admission_ref = backend.publish(guard.ADMISSION, io.canonical(admission))
        armed = {
            "schema": "autostop.j1.manager-mcp-successor-state.v1",
            **context,
            "admission": admission_ref,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        return backend.publish(guard.STATE, io.canonical(armed))


def state_time(value):
    from scripts.j1_forward_state import _timestamp

    return _timestamp(value)
