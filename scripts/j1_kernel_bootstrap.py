#!/usr/bin/env python3
"""Read-only kernel-bootstrap consumer; never start, arm, reset or approve."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import select
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MODE = "kernel_ro_cold_v1"
CONTRACT_SHA = "eac7e93f3e2499a11d909444d928374b1ca328282de9562bceb8c3419058f0ac"
DECISION_SHA = "12489c8b350ac1ace7e06aa49c47963069afafceecbd72a2f4035e61ec2b5a85"
MANAGER = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
GUARD_SHA = "d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6"
INSTALL = Path("/usr/local/libexec/autostop-j1-kernel-bootstrap")
CONSUMER = Path("/usr/local/libexec/autostop-crm-j1-duty/kernel.py")
POLICY = Path("/etc/autostop-j1-kernel-bootstrap/policy.json")
APPROVAL = POLICY.with_name("approval.json")
OBSERVER_INTENT = Path("/etc/autostop-crm-j1-duty/intent.json")
STATE = Path("/var/lib/autostop-j1-kernel-bootstrap/state.json")
CACHE_POLICY = Path("/etc/autostop-j1-cache-view/policy.json")
CACHE_CODE = Path("/usr/local/libexec/autostop-j1-cache-view/controller.py")
CACHE_STATE = Path("/var/lib/autostop-j1-cache-view/state.json")
CACHE_PENDING = CACHE_STATE.with_name("preparing-pending.json")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
UNIT = "autostop-codex-start.service"
CACHE_UNIT = "autostop-j1-cache-view.service"
GATE_DROPIN = "z1-j1-kernel-readiness.conf"
RECOVERY_UNIT = "autostop-j1-native-recover.service"
RECOVERY_DROPIN = "z1-j1-native-recovery.conf"
LIMIT = 262144
HEX = re.compile(r"[0-9a-f]{64}\Z")
ELIGIBILITY_FIELDS = set(
    "schema daemon_mode attempt_id crm_revision crm_tree manager_revision guard_v2_sha256 "
    "bootstrap_package_manifest_sha256 consumer_contract_sha256 cache_view_package_manifest_sha256 "
    "baseline_ref instruction_ref history_recovery_ref isolated_native_capability_refs coordinator".split()
)
NATIVE_FIELDS = set(
    "schema daemon_mode attempt_id crm_revision crm_tree manager_revision guard_v2_sha256 "
    "eligibility_ref bootstrap_package_manifest_sha256 consumer_contract_sha256 "
    "cache_view_package_manifest_sha256 bootstrap_policy_sha256 base_policy_sha256 "
    "base_armed_state_sha256 boot_id generation ready_state_ref daemon preservation_ref "
    "global_writer_closure_ref primary_native_evidence_refs reviewer accepted".split()
)
PRESERVATION_FIELDS = set(
    "schema daemon_mode attempt_id eligibility_ref native_acceptance_ref start_or_restart_performed "
    "preserved_thread_ref preserved_instruction_ref preserved_history_ref".split()
)
WRITER_CLASSES = sorted(
    "native_units detached_daemons active_turns direct_cli crm_gateway containers "
    "scheduled_triggers browser".split()
)


class KernelError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise KernelError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate_json_key")
        value[key] = item
    return value


def decode(raw):
    def invalid(_value):
        raise KernelError("nonfinite_json_number")

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    require(type(value) is dict, "object_required")
    return value


def protected(path):
    require(path.is_absolute() and ".." not in path.parts, "physical_path_required")
    for entry in (*reversed(path.parents), path):
        info = entry.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "root_protected_path_required",
        )


def read_file(path, limit=LIMIT):
    protected(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
            "bounded_single_link_file_required",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, named = os.fstat(fd), path.lstat()

        def stamp(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

        require(
            len(raw) <= limit and stamp(before) == stamp(after) == stamp(named),
            "evidence_changed_during_read",
        )
        if os.name == "posix":
            require(
                before.st_ctime_ns == after.st_ctime_ns == named.st_ctime_ns,
                "evidence_changed_during_read",
            )
        return raw
    finally:
        os.close(fd)


def reference(record, root, *, document=True):
    require(type(record) is dict and set(record) == {"path", "sha256"}, "reference_invalid")
    require(
        type(record["path"]) is str
        and type(record["sha256"]) is str
        and HEX.fullmatch(record["sha256"]),
        "reference_invalid",
    )
    path = Path(record["path"])
    require(path.is_relative_to(root), "outside_attempt_evidence_root")
    raw = read_file(path)
    require(digest(raw) == record["sha256"], "evidence_hash_mismatch")
    return decode(raw) if document else raw


def references(records, root):
    require(type(records) is list and 1 <= len(records) <= 128, "primary_evidence_required")
    require(
        all(type(record) is dict and type(record.get("path")) is str for record in records),
        "reference_invalid",
    )
    require(len({record["path"] for record in records}) == len(records), "duplicate_evidence")
    return [reference(record, root, document=False) for record in records]


def context(value, schema, preparation):
    require(
        value.get("schema") == schema
        and value.get("daemon_mode") == MODE
        and value.get("attempt_id") == preparation["attempt_id"]
        and value.get("crm_revision") == preparation["crm"]["revision"]
        and value.get("crm_tree") == preparation["crm"]["tree"]
        and value.get("manager_revision") == MANAGER
        and value.get("guard_v2_sha256") == GUARD_SHA
        and value.get("consumer_contract_sha256") == CONTRACT_SHA,
        "kernel_context_mismatch",
    )
    for name in ("bootstrap_package_manifest_sha256", "cache_view_package_manifest_sha256"):
        require(
            type(value.get(name)) is str and HEX.fullmatch(value[name]), "package_hash_required"
        )


def eligibility(record, preparation, root):
    value = reference(record, root)
    require(set(value) == ELIGIBILITY_FIELDS, "eligibility_fields_invalid")
    context(value, "autostop.j1.kernel-bootstrap-eligibility.v1", preparation)
    require(
        type(value["coordinator"]) is str and value["coordinator"].strip(), "coordinator_required"
    )
    for name in ("baseline_ref", "instruction_ref", "history_recovery_ref"):
        reference(value[name], root, document=False)
    references(value["isolated_native_capability_refs"], root)
    return value


def native_acceptance(record, eligibility_ref, preparation, root, base_policy, base_state):
    eligible = eligibility(eligibility_ref, preparation, root)
    wrapper = reference(record, root)
    require(set(wrapper) == PRESERVATION_FIELDS, "kernel_preservation_fields_invalid")
    require(
        wrapper["schema"] == "autostop.j1.kernel-bootstrap-preservation.v1"
        and wrapper["daemon_mode"] == MODE
        and wrapper["attempt_id"] == preparation["attempt_id"]
        and wrapper["eligibility_ref"] == eligibility_ref
        and wrapper["start_or_restart_performed"] is True,
        "actual_cold_start_preservation_required",
    )
    for name in ("preserved_thread_ref", "preserved_instruction_ref", "preserved_history_ref"):
        reference(wrapper[name], root, document=False)
    require(
        wrapper["preserved_instruction_ref"] == eligible["instruction_ref"],
        "instruction_ref_changed",
    )
    native = reference(wrapper["native_acceptance_ref"], root)
    require(set(native) == NATIVE_FIELDS, "native_acceptance_fields_invalid")
    context(native, "autostop.j1.kernel-bootstrap-native-acceptance.v1", preparation)
    require(
        native["accepted"] is True
        and type(native["reviewer"]) is str
        and native["reviewer"].strip()
        and native["eligibility_ref"] == eligibility_ref
        and native["base_policy_sha256"] == base_policy
        and native["base_armed_state_sha256"] == base_state
        and all(
            native[name] == eligible[name]
            for name in ("bootstrap_package_manifest_sha256", "cache_view_package_manifest_sha256")
        ),
        "independent_native_acceptance_required",
    )
    for key in ("boot_id", "generation"):
        require(
            type(native[key]) is str and str(uuid.UUID(native[key])) == native[key],
            "native_uuid_invalid",
        )
    require(
        type(native["bootstrap_policy_sha256"]) is str
        and HEX.fullmatch(native["bootstrap_policy_sha256"]),
        "bootstrap_policy_hash_required",
    )
    ready = reference(native["ready_state_ref"], root)
    daemon = native["daemon"]
    require(
        type(daemon) is dict
        and set(daemon)
        == {"pid", "start_ticks", "argv_sha256", "environment_sha256", "mount_namespace"}
        and type(daemon["pid"]) is int
        and daemon["pid"] > 1
        and type(daemon["start_ticks"]) is int
        and daemon["start_ticks"] > 0
        and all(
            type(daemon[key]) is str and HEX.fullmatch(daemon[key])
            for key in ("argv_sha256", "environment_sha256")
        )
        and type(daemon["mount_namespace"]) is str
        and re.fullmatch(r"mnt:\[[0-9]+\]", daemon["mount_namespace"]),
        "native_daemon_stamp_invalid",
    )
    require(
        ready.get("schema") == "autostop.j1.kernel-bootstrap-state.v1"
        and ready.get("phase") == "ready"
        and ready.get("policy_sha256") == native["bootstrap_policy_sha256"]
        and all(ready.get(name) == native[name] for name in ("boot_id", "generation", "daemon"))
        and ready.get("terminal_exit_code") is None,
        "initial_ready_snapshot_mismatch",
    )
    reference(native["preservation_ref"], root, document=False)
    closure = reference(native["global_writer_closure_ref"], root)
    require(
        closure.get("accepted") is True and closure.get("closed_writer_classes") == WRITER_CLASSES,
        "global_writer_closure_required",
    )
    references(closure.get("files"), root)
    references(native["primary_native_evidence_refs"], root)
    for key in ("bootstrap_package_manifest_sha256", "cache_view_package_manifest_sha256"):
        require(
            any(ref["sha256"] == native[key] for ref in native["primary_native_evidence_refs"]),
            "accepted_package_manifest_reference_required",
        )
    return native


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "fixed_module_required")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def producer(native, root):
    """Load only the independently accepted protected package, never a receipt command."""
    refs = native["primary_native_evidence_refs"]
    matches = [
        record for record in refs if record["sha256"] == native["bootstrap_package_manifest_sha256"]
    ]
    require(len(matches) == 1, "accepted_bootstrap_manifest_reference_required")
    manifest = reference(matches[0], root)
    require(
        manifest.get("schema") == "autostop.admin-prepared-package.v1", "package_manifest_invalid"
    )
    files = manifest.get("files")
    require(type(files) is list and 1 <= len(files) <= 128, "package_inventory_required")
    inventory = {entry["path"]: entry["sha256"] for entry in files}
    require(len(inventory) == len(files), "duplicate_package_file")
    for name in ("bootstrap.py", "rpc.py", "render.py"):
        require(digest(read_file(INSTALL / name)) == inventory.get(name), "bootstrap_code_changed")
    rpc = load_module("accepted_kernel_rpc", INSTALL / "rpc.py")
    previous = sys.modules.get("rpc")
    try:
        sys.modules["rpc"] = rpc
        module = load_module("accepted_kernel_bootstrap", INSTALL / "bootstrap.py")
    finally:
        if previous is None:
            sys.modules.pop("rpc", None)
        else:
            sys.modules["rpc"] = previous
    for name in ("bootstrap.py", "rpc.py", "render.py"):
        require(digest(read_file(INSTALL / name)) == inventory[name], "bootstrap_code_changed")
    return module


def policy_inputs(module, native, preparation):
    raw = read_file(POLICY)
    require(digest(raw) == native["bootstrap_policy_sha256"], "bootstrap_policy_changed")
    policy = decode(raw)
    module.keys(
        policy,
        "schema crm_revision crm_tree base_policy_sha256 base_state_sha256 sealed_cli "
        "cli_environment source_root source_identity static_files unit_directories unit_properties "
        "metadata_paths cache wake",
    )
    require(
        policy["schema"] == "autostop.j1.kernel-bootstrap-policy.v1"
        and policy["crm_revision"] == preparation["crm"]["revision"]
        and policy["crm_tree"] == preparation["crm"]["tree"]
        and policy["base_policy_sha256"] == native["base_policy_sha256"]
        and policy["base_state_sha256"] == native["base_armed_state_sha256"],
        "bootstrap_policy_context_mismatch",
    )
    module.approval_check(decode(read_file(APPROVAL)), digest(raw))
    module.verify_source(policy)
    source = preparation["sources"]["manager"]
    require(
        policy["source_root"] == source["path"] and policy["source_identity"] == source["identity"],
        "kernel_source_root_changed",
    )
    module.credentials_and_history(policy)
    require(
        type(policy["static_files"]) is list and 1 <= len(policy["static_files"]) <= 128,
        "static_inventory_required",
    )
    paths = [record["path"] for record in policy["static_files"]]
    mandatory = {str(INSTALL / name) for name in ("bootstrap.py", "rpc.py", "render.py")}
    mandatory |= {
        str(module.WAKE),
        "/root/.codex/config.toml",
        "/root/.codex/app-server-daemon/settings.json",
        module.INSTRUCTION_ROOT + "/REVISION",
        f"/etc/systemd/system/{UNIT}",
        f"/etc/systemd/system/{UNIT}.d/z0-j1-kernel-bootstrap.conf",
        str(CACHE_POLICY),
        str(CACHE_CODE),
        f"/etc/systemd/system/{CACHE_UNIT}",
        f"/etc/systemd/system/{RECOVERY_UNIT}",
        f"/etc/systemd/system/{UNIT}.d/{RECOVERY_DROPIN}",
    }
    require(
        len(paths) == len(set(paths)) and mandatory <= set(paths), "static_inventory_incomplete"
    )
    for record in policy["static_files"]:
        module.reference(record)
    for record in policy["metadata_paths"]:
        require(
            all(
                path != record["path"]
                and not (record["kind"] == "directory" and path.startswith(record["path"] + "/"))
                for path in paths
            ),
            "mutable_content_must_not_be_statically_hashed",
        )
    for folder, entries in policy["unit_directories"].items():
        require(folder == f"/etc/systemd/system/{UNIT}.d", "unit_directory_changed")
        module.protected(Path(folder), directory=True)
        require(
            sorted(item.name for item in Path(folder).iterdir()) == sorted(entries)
            and all(str(Path(folder) / name) in paths for name in entries),
            "unit_inventory_changed",
        )
    require(
        set(policy["unit_directories"]) == {f"/etc/systemd/system/{UNIT}.d"},
        "unit_inventory_required",
    )
    environment = decode(module.reference(policy["cli_environment"]))
    module.keys(environment, "schema environment")
    require(
        environment["schema"] == "autostop.j1.observed-cli-environment.v1",
        "cli_environment_required",
    )
    expected = module.stable_environment(environment["environment"])
    require(
        module.file_hash(Path(policy["sealed_cli"])) == module.CODEX_SHA
        and policy["sealed_cli"] != "/usr/local/bin/codex"
        and module.file_hash(module.DAEMON) == module.CODEX_SHA,
        "sealed_native_code_changed",
    )
    selector = Path("/root/.codex/packages/app-server-daemon/current")
    module.physical(selector)
    require(
        selector.is_symlink() and selector.resolve(strict=True) == module.DAEMON.parent.parent,
        "daemon_selector_changed",
    )
    require(
        all(
            decode(module.read_file(module.WAKE)).get(key) == value
            for key, value in policy["wake"].items()
        )
        and policy["wake"]["instruction_root"] == module.INSTRUCTION_ROOT
        and policy["wake"]["instruction_revision"] == module.INSTRUCTION_REVISION
        and module.read_file(Path(module.INSTRUCTION_ROOT) / "REVISION", 80)
        == (module.INSTRUCTION_REVISION + "\n").encode(),
        "instruction_or_wake_changed",
    )
    module.base_arm(policy)
    return policy, expected


def cache_view(module, boot):
    """Observe PID1's durable view even when called from a native RW namespace."""
    try:
        CACHE_PENDING.lstat()
    except FileNotFoundError:
        pass
    else:
        raise KernelError("cache_view_pending")
    raw = read_file(CACHE_STATE)
    value = decode(raw)
    policy_raw = read_file(CACHE_POLICY)
    policy = decode(policy_raw)
    require(
        value.get("schema") == "autostop.j1.cache-view-state.v1"
        and value.get("phase") == "ready"
        and value.get("boot_id") == boot
        and value.get("policy_sha256") == digest(policy_raw)
        and policy.get("schema") == "autostop.j1.cache-view-policy.v1"
        and policy.get("controller_sha256") == digest(read_file(CACHE_CODE)),
        "current_cache_view_required",
    )
    private = Path("/proc/1/root/run/autostop-j1-cache-view-private")
    info = private.stat()
    require(
        info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, "private_cache_source_unprotected"
    )
    for name, path in (
        ("cache_identity", private / "rw"),
        ("database_identity", private / "rw/research.sqlite3"),
    ):
        info = path.stat()
        require(
            [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
            == policy.get(name)
            == value.get(name),
            "private_cache_identity_changed",
        )
        require(not os.statvfs(path).f_flag & os.ST_RDONLY, "native_cache_source_not_writable")
    mounts = []
    for row in module.proc_bytes(Path("/proc/1/mountinfo")).decode().splitlines():
        left, right = row.split(" - ", 1)
        fields, backing = left.split(), right.split()
        target = fields[4]
        if (
            target == "/var/cache/autostop-j1"
            or target == "/run/autostop-j1-cache-view-private"
            or target.startswith("/run/autostop-j1-cache-view-private/")
        ):
            mounts.append(
                {
                    "id": int(fields[0]),
                    "parent": int(fields[1]),
                    "root": fields[3],
                    "target": target,
                    "options": fields[5].split(","),
                    "optional": fields[6:],
                    "filesystem": backing[0],
                    "device": fields[2],
                }
            )
    require(mounts and mounts == value.get("mounts"), "cache_mount_view_changed")
    return raw


def main_process(module, pid, environment, namespace):
    require(type(pid) is int and pid > 1, "live_bootstrap_main_required")
    root = Path("/proc") / str(pid)
    fields = module.proc_bytes(root / "stat").rsplit(b")", 1)[1].split()
    require(len(fields) >= 20 and fields[0] not in (b"Z", b"X"), "bootstrap_main_dead")
    require(
        module.proc_bytes(root / "cmdline").split(b"\0")[:-1]
        == [
            b"/usr/bin/python3",
            b"-I",
            b"-S",
            b"-B",
            str(INSTALL / "bootstrap.py").encode(),
            b"run",
        ]
        and os.readlink(root / "exe") == str(Path("/usr/bin/python3").resolve(strict=True))
        and module.proc_bytes(root / "cgroup").decode().strip() == "0::" + module.CGROUP
        and os.readlink(root / "ns/mnt") == namespace
        and module.stable_environment(module.environment_bytes(module.proc_bytes(root / "environ")))
        == environment,
        "bootstrap_main_identity_changed",
    )
    uid = next(
        row
        for row in module.proc_bytes(root / "status").decode().splitlines()
        if row.startswith("Uid:")
    )
    require(uid.split()[1:] == ["0"] * 4, "bootstrap_main_uid_changed")
    return pid, int(fields[19]), namespace


@contextmanager
def observation_lock(preparation, deadline):
    """Serialize owned version/RPC probes without changing the immutable intent.

    Another observer's fixed same-HOME version CLI must never be mistaken for
    a foreign app-server. The producer lifetime lock cannot serve this purpose.
    Linux flock works on this existing read-only file and releases on death.
    """
    import fcntl

    raw = read_file(OBSERVER_INTENT)
    value = decode(raw)
    require(
        value.get("schema") == "autostop.crm.j1-work-duty-intent.v1"
        and value.get("attempt_id") == preparation["attempt_id"]
        and value.get("crm_revision") == preparation["crm"]["revision"]
        and value.get("manager_revision") == MANAGER,
        "observer_intent_context_mismatch",
    )
    fd = os.open(OBSERVER_INTENT, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o444,
            "immutable_observer_intent_required",
        )
        while True:
            left = deadline - time.monotonic()
            require(left > 0, "readiness_observer_lock_timeout")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.025, left))
        named = OBSERVER_INTENT.lstat()
        require(
            (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino)
            and read_file(OBSERVER_INTENT) == raw,
            "observer_intent_changed",
        )
        yield
    finally:
        os.close(fd)


def observe(native, preparation, root, *, initial):
    """Fresh liveness even after commit; initial snapshots are never rewritten."""
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_observer_required")
    module = producer(native, root)
    deadline = time.monotonic() + 10
    try:
        policy, environment = policy_inputs(module, native, preparation)
        before, raw = module.read_state(native["bootstrap_policy_sha256"])
        require(before is not None and before["phase"] == "ready", "current_ready_required")
        boot = BOOT_ID.read_text().strip()
        require(before["boot_id"] == boot, "current_boot_required")
        cache_raw = cache_view(module, boot)
        if initial:
            require(
                all(before[name] == native[name] for name in ("boot_id", "generation", "daemon")),
                "initial_native_generation_changed",
            )
        module.actual_unit(policy, environment, deadline)
        argv = ["/usr/bin/systemctl", "show", UNIT, "--no-pager"] + [
            "--property=" + key for key in ("Type", "ActiveState", "SubState", "MainPID")
        ]
        output = module.fixed_command(argv, environment, "/", deadline, output=True)
        fields = dict(row.split("=", 1) for row in output.decode().splitlines())
        require(
            fields.get("Type") == "notify"
            and fields.get("ActiveState") == "active"
            and fields.get("SubState") == "running"
            and re.fullmatch(r"[1-9][0-9]*", fields.get("MainPID", "")),
            "bootstrap_notify_running_required",
        )
        main = main_process(
            module, int(fields["MainPID"]), environment, before["daemon"]["mount_namespace"]
        )
        # Observe PID1's canonical view, not this legitimate native writer's RW bind.
        module.cache_check(policy, Path("/proc/1/root"))
        with observation_lock(preparation, deadline):
            stamp, pidfd = module.ready_daemon(policy, environment, deadline, cold=False)
        try:
            require(
                stamp == before["daemon"] and not select.select([pidfd], [], [], 0)[0],
                "current_daemon_changed",
            )
            after, after_raw = module.read_state(native["bootstrap_policy_sha256"])
            require(raw == after_raw and after == before, "ready_generation_raced")
            require(
                main_process(module, main[0], environment, stamp["mount_namespace"]) == main,
                "bootstrap_main_raced",
            )
            module.base_arm(policy)
            require(cache_view(module, boot) == cache_raw, "cache_view_raced")
            for record in policy.get("static_files", []):
                module.reference(record)
            module.actual_unit(policy, environment, deadline)
            require(
                module.fixed_command(argv, environment, "/", deadline, output=True) == output,
                "bootstrap_unit_raced",
            )
            require(
                digest(read_file(POLICY)) == native["bootstrap_policy_sha256"],
                "bootstrap_policy_changed",
            )
        finally:
            os.close(pidfd)
        return before
    except module.Deny as exc:
        raise KernelError("kernel_native_observation_denied") from exc


def gate_command(intent_hash):
    require(type(intent_hash) is str and HEX.fullmatch(intent_hash), "intent_hash_required")
    return (
        "/usr/bin/python3 -I -S /usr/local/libexec/autostop-crm-j1-duty/restore.py "
        f"ready --intent-sha256 {intent_hash}"
    )


def gate_bytes(intent_hash, *, unit=None):
    # Source89 wake has a single Requires=bootstrap. Use the same explicit
    # BindsTo/Wants/After edges as the other five; recovery restarts stopped roles.
    reset = "Requires=\n" if unit == "autostop-codex-wake.service" else ""
    return (
        "[Unit]\n"
        + reset
        + "Requires="
        + CACHE_UNIT
        + "\nWants="
        + UNIT
        + "\nBindsTo="
        + UNIT
        + "\nAfter="
        + CACHE_UNIT
        + " "
        + UNIT
        + "\n[Service]\nExecCondition=+"
        + gate_command(intent_hash)
        + "\n"
    ).encode()


def recovery_bytes(intent_hash):
    command = gate_command(intent_hash).replace(
        " ready --intent-sha256 ", " recover --intent-sha256 "
    )
    return (
        "[Unit]\nDescription=Restore committed J1 native roles after current bootstrap READY\n"
        "After=" + UNIT + " " + CACHE_UNIT + "\nBindsTo=" + UNIT + "\n"
        "[Service]\nType=oneshot\nUser=root\nGroup=root\n"
        "ExecStart=" + command + "\nTimeoutStartSec=20s\n"
    ).encode()


def recovery_dropin_bytes():
    return ("[Unit]\nWants=" + RECOVERY_UNIT + "\n").encode()
