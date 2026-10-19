"""Fresh read-only measurements for the separate all-profile cold contract.

Production locations and observer commands are fixed. Nothing here installs a
policy, stops a production process, invokes a guard/RPC, opens a fence or execs
an application. A dispatcher result is a *closed-stage inspection*, not a start
permit. The fixture provider uses this same validation sequence with a private,
closed process topology; its scope cannot satisfy a production caller.
"""

from __future__ import annotations

import hashlib
import os
import re
import select
import socket
import stat
import subprocess
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_io as io

HOST = "vps26457.mnogoweb.in"
CONFIG = Path("/etc/autostop-crm-forward/cold/v1")
INSTALL = Path("/usr/local/libexec/autostop-crm-forward-cold/v1")
STATE = Path("/var/lib/autostop-crm-forward/cold/v1")
DEPLOY_LOCK = Path("/opt/autostopcrm/.autostop-deploy.lock")
DISPATCH_LOCK = STATE / "dispatch.lock"
UNIT_PROPERTIES = (
    "Id",
    "Type",
    "LoadState",
    "NeedDaemonReload",
    "ActiveState",
    "SubState",
    "MainPID",
    "ControlGroup",
    "ExecCondition",
    "ExecStart",
    "ExecStartPre",
    "ExecStartPost",
    "ExecStop",
    "ExecStopPost",
    "ExecReload",
    "DropInPaths",
    "User",
    "Group",
    "SupplementaryGroups",
    "Environment",
    "EnvironmentFiles",
    "WorkingDirectory",
)
ACTOR_FIELDS = set(
    "pid start_ticks uid gid groups cgroup mount_namespace exe_ref argv_sha256 "
    "environment_sha256".split()
)


def package_path(attempt):
    contract.need(contract._uuid(attempt), "native_package_attempt")
    return CONFIG / "packages" / attempt / "authority.json"


def kernel_unit(generation):
    contract.need(contract._uuid(generation), "native_kernel_generation")
    return "autostop-j1-cold-kernel-" + generation + ".service"


def hold_path(attempt):
    contract.need(contract._uuid(attempt), "native_hold_attempt")
    return STATE / "packages" / attempt / "ingress-held.json"


def birth(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_bytes()
    return int(raw[raw.rfind(b")") + 2 :].split()[19])


def controller(pid):
    return {"pid": pid, "start_ticks": birth(pid)}


def _environment(raw):
    rows = [row.split(b"=", 1) for row in raw.split(b"\0") if row]
    contract.need(
        all(len(row) == 2 for row in rows) and len({row[0] for row in rows}) == len(rows),
        "native_environment_shape",
    )
    return {key.decode(): value.decode() for key, value in rows}


def actor(pid):
    """Only a caller-selected owned/accepted actor is read; never adopt a PID."""
    root = Path("/proc") / str(pid)
    before = birth(pid)
    status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines())
    uid, gid = (
        [int(row) for row in status["Uid"].split()],
        [int(row) for row in status["Gid"].split()],
    )
    contract.need(len(set(uid)) == len(set(gid)) == 1, "native_actor_credentials")
    executable = Path(os.readlink(root / "exe"))
    executable_sha = executable_digest(executable)
    value = {
        "pid": pid,
        "start_ticks": before,
        "uid": uid[0],
        "gid": gid[0],
        "groups": sorted(int(group) for group in status["Groups"].split()),
        "cgroup": (root / "cgroup").read_text().strip(),
        "mount_namespace": os.readlink(root / "ns/mnt"),
        "exe_ref": {"path": str(executable), "sha256": executable_sha},
        "argv_sha256": io.sha((root / "cmdline").read_bytes()),
        "environment_sha256": contract.digest(_environment((root / "environ").read_bytes())),
    }
    contract.need(
        birth(pid) == before
        and os.readlink(root / "exe") == str(executable)
        and executable_digest(executable) == value["exe_ref"]["sha256"],
        "native_actor_changed",
    )
    return value


def executable_digest(path):
    """Hash a stable executable with bounded memory and a full version cut.

    The accepted policy's reference reader separately checks directory custody.
    This process observation supplies bytes/identity, never source acceptance.
    """
    path = Path(path)
    before = path.lstat()
    contract.need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_uid in {0, os.geteuid()}
        and not before.st_mode & 0o022
        and 0 < before.st_size <= contract.MAX_RUNTIME_BYTES,
        "native_executable_file",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
    )
    version = tuple(getattr(before, key) for key in fields)
    try:
        contract.need(
            tuple(getattr(os.fstat(descriptor), key) for key in fields) == version,
            "native_executable_changed",
        )
        digest, count = hashlib.sha256(), 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while block := stream.read(1024**2):
                count += len(block)
                contract.need(count <= before.st_size, "native_executable_changed")
                digest.update(block)
        contract.need(
            count == before.st_size
            and tuple(getattr(os.fstat(descriptor), key) for key in fields) == version
            and tuple(getattr(path.lstat(), key) for key in fields) == version,
            "native_executable_changed",
        )
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def actor_shape(value):
    contract.fields(value, ACTOR_FIELDS, "actor_fields")
    contract.need(
        all(type(value[key]) is int and value[key] > 0 for key in ("pid", "start_ticks")),
        "actor_birth",
    )
    contract.reference(value["exe_ref"])


def retired(expected):
    """A reused number is not the retired incarnation; signals are never sent."""
    actor_shape(expected)
    retired_incarnation(expected["pid"], expected["start_ticks"])


def retired_incarnation(pid, expected_start):
    contract.need(
        type(pid) is int and pid > 0 and type(expected_start) is int and expected_start > 0,
        "retired_identity",
    )
    try:
        start = birth(pid)
    except (FileNotFoundError, ProcessLookupError):
        return
    if start != expected_start:
        return
    descriptor = os.pidfd_open(pid, 0)
    try:
        contract.need(
            birth(pid) == start and bool(select.select([descriptor], [], [], 0)[0]),
            "old_incarnation_live",
        )
    except (FileNotFoundError, ProcessLookupError):
        contract.need(bool(select.select([descriptor], [], [], 0)[0]), "old_incarnation_unstable")
    finally:
        os.close(descriptor)


def held_descriptor(owner, descriptor, expected):
    contract.fields(owner, {"pid", "start_ticks"}, "hold_controller_fields")
    contract.need(controller(owner["pid"]) == owner, "hold_controller_changed")
    path = Path("/proc") / str(owner["pid"]) / "fd" / str(descriptor)
    info = path.stat()
    contract.need([info.st_dev, info.st_ino] == expected, "hold_lock_identity")
    details = path.parent.parent / "fdinfo" / str(descriptor)
    lines = details.read_text().splitlines()
    contract.need(
        any(re.fullmatch(r"lock:\s+\d+: FLOCK\s+ADVISORY\s+WRITE\s+.*", line) for line in lines),
        "hold_lock_not_owned",
    )
    contract.need(controller(owner["pid"]) == owner, "hold_controller_raced")


def commands(raw):
    records = re.findall(r"\{([^{}]*)\}", raw)
    contract.need(not re.sub(r"\{[^{}]*\}", "", raw).strip(), "unit_command_encoding")
    result = []
    for record in records:
        rows = [part.strip().partition("=") for part in record.split(";") if part.strip()]
        contract.need(
            all(separator for _name, separator, _value in rows)
            and len({row[0] for row in rows}) == len(rows),
            "unit_command_fields",
        )
        values = {name: value for name, _separator, value in rows}
        contract.need(
            values.get("ignore_errors") == "no"
            and values.get("path") == values.get("argv[]", "").split(" ", 1)[0],
            "unit_command_ignored_or_path",
        )
        result.append(values["argv[]"])
    return result


class NativeProvider:
    """Fixed future production observer. Missing new cold inputs fail normally.

    This provider is implemented rather than a permanent refusal: after an
    independently accepted cold installation its exact files, native units,
    process scope, continuously held descriptors and new kernel are measured.
    Installation and the independent acceptance are outside this module.
    """

    scope = "production_native"

    def read_reference(self, reference):
        contract.reference(reference)
        raw = io.read_protected(reference["path"], limit=contract.MAX_SOURCE_BYTES)
        contract.need(io.sha(raw) == reference["sha256"], "native_reference_hash")
        return raw

    def read_opaque_reference(self, reference):
        contract.reference(reference)
        raw = io.read_protected(reference["path"], limit=contract.MAX_RUNTIME_BYTES)
        contract.need(io.sha(raw) == reference["sha256"], "native_reference_hash")
        return contract.runtime_bytes(reference, raw)

    def read_runtime_capsule(self, reference):
        contract.reference(reference)
        raw = io.read_protected(reference["path"], limit=192 * 1024**2)
        contract.need(io.sha(raw) == reference["sha256"], "native_reference_hash")
        return contract.runtime_bytes(reference, raw, capsule=True)

    def boundary(self, reference):
        contract.need(
            os.name == "posix"
            and hasattr(os, "pidfd_open")
            and os.geteuid() == 0
            and socket.gethostname() == HOST,
            "native_fixed_boundary",
        )
        contract.reference(reference)
        path = Path(reference["path"])
        contract.need(
            path.parent.parent == CONFIG / "packages"
            and path.name == "authority.json"
            and contract._uuid(path.parent.name),
            "native_package_location",
        )
        document = io.document(self.read_reference(reference))
        contract.need(
            path == package_path(document["binding"]["attempt_id"]),
            "native_package_attempt_binding",
        )

    def boot(self):
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def systemd(self, unit):
        allowed_kernel = (
            type(unit) is str
            and unit.startswith("autostop-j1-cold-kernel-")
            and unit.endswith(".service")
            and contract._uuid(
                unit.removeprefix("autostop-j1-cold-kernel-").removesuffix(".service")
            )
        )
        contract.need(unit in contract.UNITS or allowed_kernel, "native_unit_scope")
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                unit,
                "--no-pager",
                "--property=" + ",".join(UNIT_PROPERTIES),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        contract.need(
            result.returncode == 0 and len(result.stdout) <= 256 * 1024, "native_unit_observation"
        )
        from scripts import j1_forward_authority

        values = j1_forward_authority._systemd_fields(result.stdout)
        contract.need(
            set(values) == set(UNIT_PROPERTIES)
            and values["Id"] == unit
            and values["LoadState"] == "loaded"
            and values["NeedDaemonReload"] == "no",
            "native_unit_not_loaded",
        )
        return values

    def verify_files(self, validated):
        package, policies = validated["package"], validated["policies"]
        source = io.document(self.read_reference(package["source_package_ref"]))
        consumer = {
            "authority": io.sha(Path(contract.__file__).read_bytes()),
            "native": io.sha(Path(__file__).read_bytes()),
            "guard": io.sha(self.read_reference(source["guard_ref"])),
        }
        for name in ("history", "hold"):
            consumer[name] = io.sha(
                Path(contract.__file__).with_name("j1_forward_cold_" + name + ".py").read_bytes()
            )
        contract.need(
            package["binding"]["consumer_sha256"] == contract.digest(consumer),
            "native_consumer_binding",
        )
        contract.need(
            source["guard_ref"]["path"] == str(INSTALL / "guard.py")
            and source["native_ref"]["path"] == str(INSTALL / "native.py")
            and source["native_ref"]["sha256"] == io.sha(Path(__file__).read_bytes()),
            "native_consumer_source",
        )
        for unit, policy in policies.items():
            self._exact_source_tree(policy)
            runtime = io.document(self.read_reference(policy["runtime_index_ref"]))
            if runtime.get("schema") == "autostop.j1.cold-runtime-index.v2":
                from scripts import j1_forward_cold_runtime_capsule

                j1_forward_cold_runtime_capsule.verify_installed(runtime)
            unit_doc = io.document(self.read_reference(policy["unit_ref"]))
            actual = self.systemd(unit)
            expected = unit_doc["properties"]
            contract.need(
                set(expected) == set(UNIT_PROPERTIES) and actual == expected,
                "native_profile_unit_changed",
            )
            # This is the preserved original unit, not the new execution slot.
            # Its exact old policy remains pinned and an effective final FALSE
            # prevents any old application entry. New-slot CLI/A/C/E is checked
            # separately by the lifecycle; no old unit/drop-in is rewritten.
            contract.need(
                commands(actual["ExecCondition"])[-1:] == ["/usr/bin/false"],
                "native_original_deny_not_loaded",
            )
            contract.need(
                all(
                    not actual[key]
                    for key in (
                        "ExecStartPre",
                        "ExecStartPost",
                        "ExecStop",
                        "ExecStopPost",
                        "ExecReload",
                    )
                ),
                "native_cold_hooks",
            )
            contract.need(
                actual["ActiveState"] == "inactive"
                and actual["SubState"] == "dead"
                and actual["MainPID"] == "0",
                "native_old_unit_active",
            )

    def _exact_source_tree(self, policy):
        root = Path(policy["source_root"])
        contract.need(
            root
            == Path("/opt/autostop-manager-cold-releases")
            / policy["binding"]["candidate_manager_sha"]
            / "source",
            "native_source_location",
        )
        index = io.document(self.read_reference(policy["source_index_ref"]))
        expected = {row["path"] for row in index["files"]}
        actual = set()
        for directory, folders, files in os.walk(root, followlinks=False):
            io.protected_directory(directory)
            for name in folders:
                io.protected_directory(Path(directory) / name)
            for name in files:
                path = Path(directory) / name
                io.read_protected(path, limit=128 * 1024**2)
                actual.add(str(path))
        contract.need(actual == expected, "native_source_inventory_changed")

    def verify_closure(self, validated):
        from scripts import j1_forward_authority as historical_authority

        package, closure, hold = (validated[key] for key in ("package", "closure", "hold"))
        info = DEPLOY_LOCK.lstat()
        contract.need(
            [info.st_dev, info.st_ino] == package["binding"]["deployment_lock_identity"],
            "native_deployment_lock_changed",
        )
        contract.need(
            hold["marker_ref"]["path"] == str(hold_path(package["binding"]["attempt_id"]))
            and hold["dispatch_lock_ref"]["path"] == str(DISPATCH_LOCK),
            "native_hold_path",
        )
        lock = Path(hold["dispatch_lock_ref"]["path"]).stat()
        held_descriptor(hold["controller"], hold["descriptor"], [lock.st_dev, lock.st_ino])
        marker = io.document(self.read_reference(hold["marker_ref"]))
        contract.fields(marker, {"schema", "binding", "deployment_descriptor"}, "hold_marker")
        contract.need(
            marker["schema"] == "autostop.j1.cold-ingress-held.v1"
            and marker["binding"] == package["binding"],
            "hold_marker_binding",
        )
        held_descriptor(
            hold["controller"],
            marker["deployment_descriptor"],
            package["binding"]["deployment_lock_identity"],
        )
        original = io.document(self.read_reference(validated["history"]["anchor"]["closure_ref"]))
        expected = {
            row["unit"]: {key: row[key] for key in ("path", "sha256")}
            for row in original["dropins"]
        }
        contract.need(
            closure["barriers"] == expected and set(expected) == set(contract.UNITS),
            "native_historical_barrier_binding",
        )
        for pinned in (historical_authority.GUARD, historical_authority.CAPSULE):
            self.read_reference({"path": str(pinned), "sha256": historical_authority.PINS[pinned]})
        capsule = io.document(
            self.read_reference(
                {
                    "path": str(historical_authority.CAPSULE),
                    "sha256": historical_authority.PINS[historical_authority.CAPSULE],
                }
            )
        )
        self.read_reference(capsule["policy"])
        for unit, reference in closure["barriers"].items():
            contract.need(
                reference["path"] == "/etc/systemd/system/" + unit + ".d/zz-j1-forward-closed.conf"
                and self.read_reference(reference) == contract.DENY_BYTES,
                "native_owned_barrier_path",
            )
            fields = self.systemd(unit)
            contract.need(
                commands(fields["ExecCondition"])
                == [
                    "/usr/bin/python3 -I -S "
                    + str(historical_authority.GUARD)
                    + " check --policy-sha256 "
                    + capsule["policy"]["sha256"]
                    + " --unit "
                    + unit,
                    "/usr/bin/false",
                ]
                and fields["MainPID"] == "0"
                and fields["ActiveState"] == "inactive"
                and all(
                    not fields[key]
                    for key in (
                        "ExecStartPre",
                        "ExecStartPost",
                        "ExecStop",
                        "ExecStopPost",
                        "ExecReload",
                    )
                ),
                "native_barrier_not_effective",
            )

    def verify_retirement(self, validated):
        from scripts import j1_forward_cold_history as history

        preserved = validated["history"]
        contract.need(
            preserved["scope"]["boot_id"] == self.boot(), "history_boot_requires_fresh_scope"
        )
        containment = io.document(self.read_reference(validated["origin"]["containment_ref"]))
        paths = history.check_containment(containment, validated, preserved)
        for expected in preserved["inventory"]["actors"]:
            retired_incarnation(expected["pid"], expected["start_ticks"])
        for path in paths:
            contract.need(
                path.is_relative_to("/sys/fs/cgroup") and ".." not in path.parts,
                "native_retired_cgroup_path",
            )
            try:
                info = path.lstat()
            except FileNotFoundError:
                # Only a complete, semantically derived original scope root
                # may be collected after retirement. Arbitrary roots fail
                # check_containment before any absence is inspected.
                continue
            contract.need(
                path == path.resolve(strict=True) and stat.S_ISDIR(info.st_mode),
                "native_retired_cgroup_path",
            )
            events = dict(
                line.split() for line in (path / "cgroup.events").read_text().splitlines()
            )
            contract.need(events.get("populated") == "0", "native_retired_cgroup_populated")
            for directory, _folders, _files in os.walk(path):
                contract.need(
                    not (Path(directory) / "cgroup.procs").read_text().strip(),
                    "native_retired_population_not_empty",
                )
        contract.need(
            validated["quarantine"]["closed_population_ref"]
            == validated["origin"]["containment_ref"],
            "native_containment_ref",
        )

    def verify_ingress(self, validated):
        # No connection is made. Retired listeners/pipe capabilities must have
        # disappeared from the whole observed kernel inventory, not just app jobs.
        quarantine = validated["quarantine"]
        sockets = Path("/proc/net/unix").read_text().splitlines()[1:]
        rows = [line.split(maxsplit=7) for line in sockets]
        live = {int(row[6]) for row in rows if len(row) >= 7}
        paths = {row[7] for row in rows if len(row) == 8}
        for endpoint in quarantine["endpoints"]:
            key = "path" if self.scope == "native_fixture" else "path_sha256"
            contract.fields(endpoint, {key, "inode"}, "endpoint_fields")
            contract.need(
                endpoint["inode"] not in live
                and (
                    endpoint["path"] not in paths
                    if key == "path"
                    else endpoint["path_sha256"] not in {io.sha(path.encode()) for path in paths}
                ),
                "native_retired_listener_present",
            )
        forbidden = {"pipe:[" + str(inode) + "]" for inode in quarantine["pipe_inodes"]}
        if forbidden:
            for process in Path("/proc").iterdir():
                if not process.name.isdigit():
                    continue
                try:
                    for descriptor in (process / "fd").iterdir():
                        contract.need(
                            os.readlink(descriptor) not in forbidden,
                            "native_retired_pipe_owner_present",
                        )
                except FileNotFoundError:
                    # Disappearance cannot add a new holder; permission/I/O
                    # errors remain errors, never equivalent to an empty scan.
                    continue

    def verify_pending(self, validated):
        """Read the new dispatch inventory independently; never infer no effect.

        The producer must snapshot the complete accepted ingress/dispatch
        catalog in the future window. Existing warm jobs/counts are not used.
        Positive outcomes require native terminal records, not missing files.
        """
        pending = validated["pending"]
        inventory = io.document(self.read_reference(pending["inventory_ref"]))
        contract.fields(
            inventory,
            {"schema", "scope", "binding", "coverage", "rows"},
            "native_pending_inventory_fields",
        )
        contract.need(
            inventory["schema"] == "autostop.j1.cold-pending-inventory.v1"
            and inventory["scope"] == self.scope
            and inventory["binding"] == validated["package"]["binding"]
            and inventory["coverage"] == "accepted_ingress_and_durable_dispatch_inventory",
            "native_pending_inventory_scope",
        )
        requests = {}
        for row in inventory["rows"]:
            contract.fields(row, {"request", "evidence_refs"}, "native_inventory_row")
            request = row["request"]
            contract.fields(request, {"op_id", "payload_sha256"}, "native_inventory_request")
            contract.need(request["op_id"] not in requests, "native_inventory_duplicate")
            requests[request["op_id"]] = row
        contract.need(
            set(requests) == {row["op_id"] for row in pending["requests"]},
            "native_pending_inventory_incomplete",
        )
        for row in pending["requests"]:
            catalog = requests[row["op_id"]]
            contract.need(
                catalog["request"] == {key: row[key] for key in ("op_id", "payload_sha256")}
                and catalog["evidence_refs"] == row["evidence_refs"],
                "native_pending_identity",
            )
            # Unknown is a first-class terminal *disposition*, not permission
            # to repeat the operation. No effect is deduced from an empty log.
            if row["outcome"] == "unknown":
                continue
            terminal_path = (
                STATE / "requests" / contract.operation_key(row["op_id"]) / "terminal.json"
            )
            terminals = [ref for ref in row["evidence_refs"] if ref["path"] == str(terminal_path)]
            contract.need(len(terminals) == 1, "native_positive_terminal_missing")
            terminal = io.document(self.read_reference(terminals[0]))
            contract.fields(
                terminal,
                {
                    "schema",
                    "binding",
                    "op_id",
                    "payload_sha256",
                    "outcome",
                    "checkpoint_ref",
                    "producer_ref",
                },
                "native_terminal_fields",
            )
            contract.need(
                terminal["schema"] == "autostop.j1.cold-request-terminal.v1"
                and terminal["binding"] == validated["package"]["binding"]
                and all(
                    terminal[key] == row[key] for key in ("op_id", "payload_sha256", "outcome")
                ),
                "native_terminal_binding",
            )
            # This is a mandatory independent checkpoint from the accepted
            # producer, not a result fabricated by this read-only observer.
            self.read_reference(terminal["checkpoint_ref"])
            producer = self.read_reference(terminal["producer_ref"])
            source = io.document(self.read_reference(validated["package"]["source_package_ref"]))
            index = io.document(self.read_reference(source["source_index_ref"]))
            contract.need(
                terminal["producer_ref"] in index["files"] and producer,
                "native_terminal_producer_not_accepted",
            )

    def verify_original_hold(self, validated):
        from scripts import j1_forward_cold_hold as hold

        hold.verify(validated, self)

    def kernel_profile(self, validated):
        kernel, policy = validated["kernel"], validated["kernel_policy"]
        expected = kernel["actor"]
        actor_shape(expected)
        actual = actor(expected["pid"])
        environment = io.document(self.read_reference(policy["environment_ref"]))["values"]
        contract.need(
            type(policy["argv"]) is list
            and policy["argv"]
            and all(type(value) is str and value and "\0" not in value for value in policy["argv"]),
            "kernel_argv",
        )
        raw_argv = b"\0".join(arg.encode() for arg in policy["argv"]) + b"\0"
        contract.need(
            actual == expected
            and actual["exe_ref"] == policy["interpreter_ref"]
            and actual["argv_sha256"] == io.sha(raw_argv)
            and actual["environment_sha256"] == contract.digest(environment)
            and {key: actual[key] for key in ("uid", "gid", "groups")} == policy["credentials"]
            and os.readlink(Path("/proc") / str(expected["pid"]) / "cwd") == policy["cwd"],
            "kernel_actual_profile",
        )
        contract.need(policy["source_ref"]["path"] in policy["argv"], "kernel_source_not_executed")
        return policy

    def verify_kernel(self, validated):
        kernel, binding = validated["kernel"], validated["package"]["binding"]
        unit = kernel_unit(binding["generation"])
        contract.need(kernel["unit"] == unit, "native_new_kernel_unit")
        policy = self.kernel_profile(validated)
        expected = kernel["actor"]
        actor_shape(expected)
        descriptor = os.pidfd_open(expected["pid"], 0)
        try:
            contract.need(
                not select.select([descriptor], [], [], 0)[0]
                and actor(expected["pid"]) == expected,
                "native_new_kernel_changed",
            )
            fields = self.systemd(unit)
            expected_unit = io.document(self.read_reference(policy["unit_ref"]))
            contract.need(
                fields == expected_unit["properties"]
                and fields["Type"] == "notify"
                and commands(fields["ExecStart"]) == [" ".join(policy["argv"])]
                and fields["ActiveState"] == "active"
                and fields["SubState"] == "running"
                and fields["MainPID"] == str(expected["pid"])
                and expected["cgroup"] == "0::" + fields["ControlGroup"],
                "native_new_kernel_process",
            )
            ready = io.document(self.read_reference(kernel["ready_ref"]))
            contract.fields(
                ready, {"schema", "binding", "phase", "pid", "start_ticks"}, "kernel_ready_fields"
            )
            contract.need(
                ready
                == {
                    "schema": "autostop.j1.cold-kernel-ready.v1",
                    "binding": binding,
                    "phase": "ready",
                    "pid": expected["pid"],
                    "start_ticks": expected["start_ticks"],
                },
                "native_kernel_ready",
            )
            view = io.document(self.read_reference(kernel["cache_view_ref"]))
            contract.fields(
                view, {"schema", "binding", "kind", "path", "mount_id"}, "cache_view_fields"
            )
            contract.need(
                view["schema"] == "autostop.j1.cold-cache-view.v1"
                and view["binding"] == binding
                and view["kind"] == "kernel_read_only_mount",
                "native_cache_view_kind",
            )
            for pid in (1, expected["pid"]):
                rows = (Path("/proc") / str(pid) / "mountinfo").read_text().splitlines()
                matches = [row.split() for row in rows if row.split()[4] == view["path"]]
                contract.need(
                    len(matches) == 1
                    and "ro" in matches[0][5].split(",")
                    and (pid != 1 or int(matches[0][0]) == view["mount_id"]),
                    "native_cache_view_not_read_only",
                )
            contract.need(
                actor(expected["pid"]) == expected
                and not select.select([descriptor], [], [], 0)[0],
                "native_kernel_raced",
            )
        finally:
            os.close(descriptor)


def verify_package(reference, *, provider=None, required_scope="production_native"):
    provider = NativeProvider() if provider is None else provider
    provider.boundary(reference)
    validated = contract.validate_package(
        reference, read_reference=provider.read_reference, required_scope=required_scope
    )
    contract.need(
        provider.scope == required_scope
        and provider.boot() == validated["package"]["binding"]["boot_id"],
        "native_scope_or_boot",
    )
    for operation in (
        provider.verify_files,
        provider.verify_closure,
        provider.verify_retirement,
        provider.verify_ingress,
        provider.verify_original_hold,
        provider.verify_pending,
        provider.verify_kernel,
    ):
        operation(validated)
    # Establish the same interval at exit, including source and proof rereads.
    contract.need(
        contract.validate_package(
            reference, read_reference=provider.read_reference, required_scope=required_scope
        )
        == validated,
        "native_package_changed",
    )
    provider.verify_closure(validated)
    return validated


class CheckedDispatcher:
    """Closed-stage exact-profile inspection; never an implicit activation grant."""

    def __init__(self, reference, *, provider=None, required_scope="production_native"):
        self.reference, self.provider, self.scope = reference, provider, required_scope

    def inspect(self, unit):
        contract.need(unit in contract.ALLOWED, "dispatch_unit_permanently_denied")
        value = verify_package(self.reference, provider=self.provider, required_scope=self.scope)
        policy = value["policies"][unit]
        return {
            "schema": "autostop.j1.cold-closed-dispatch-inspection.v1",
            "scope": self.scope,
            "package_ref": self.reference,
            "binding_sha256": contract.digest(value["package"]["binding"]),
            "unit": unit,
            "policy_ref": value["package"]["policies"][unit],
            "argv": policy["argv"],
            "cwd": policy["cwd"],
        }
