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
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

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
    "Restart",
    "RuntimeMaxUSec",
    "KillMode",
    "NotifyAccess",
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
    return _actor(pid, _executable_snapshot)


def _actor(pid, measure_executable):
    """Fresh process facts, with an explicitly supplied executable measurement."""
    root = Path("/proc") / str(pid)
    before = birth(pid)
    status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines())
    uid, gid = (
        [int(row) for row in status["Uid"].split()],
        [int(row) for row in status["Gid"].split()],
    )
    contract.need(len(set(uid)) == len(set(gid)) == 1, "native_actor_credentials")
    executable = Path(os.readlink(root / "exe"))
    executable_sha, executable_version = measure_executable(executable)
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
        and _executable_version(executable.lstat()) == executable_version
        and _executable_version((root / "exe").stat()) == executable_version,
        "native_actor_changed",
    )
    return value


def _executable_version(value):
    return tuple(
        getattr(value, key)
        for key in (
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
    )


def executable_digest(path):
    return _executable_snapshot(path)[0]


def _executable_snapshot(path):
    """Hash a stable executable with bounded memory and a full version cut.

    The accepted policy's reference reader separately checks directory custody.
    This process observation supplies bytes/identity, never source acceptance.
    """
    path = Path(path)
    before = path.lstat()
    contract.need(
        stat.S_ISREG(before.st_mode) and 0 < before.st_size <= contract.MAX_RUNTIME_BYTES,
        "native_executable_file",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    version = _executable_version(before)
    try:
        contract.need(
            _executable_version(os.fstat(descriptor)) == version,
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
            and _executable_version(os.fstat(descriptor)) == version
            and _executable_version(path.lstat()) == version,
            "native_executable_changed",
        )
        return digest.hexdigest(), version
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


@contextmanager
def borrow_dispatch(provider, descriptor):
    """Borrow an existing real EX for observation; do not acquire or extend it."""
    contract.need(type(descriptor) is int and descriptor >= 0, "native_borrowed_dispatch")
    info = os.fstat(descriptor)
    linked = Path(os.readlink(Path("/proc") / str(os.getpid()) / "fd" / str(descriptor))).lstat()
    contract.need(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == linked.st_nlink == 1
        and info.st_uid in {0, os.geteuid()}
        and not info.st_mode & 0o022
        and (info.st_dev, info.st_ino, info.st_ctime_ns)
        == (linked.st_dev, linked.st_ino, linked.st_ctime_ns),
        "native_borrowed_dispatch_replaced",
    )
    held_descriptor(controller(os.getpid()), descriptor, [info.st_dev, info.st_ino])
    previous = getattr(provider, "_borrowed_dispatch_fd", None)
    contract.need(previous is None or previous == descriptor, "native_dispatch_borrow_conflict")
    provider._borrowed_dispatch_fd = descriptor
    try:
        yield
    finally:
        provider._borrowed_dispatch_fd = previous


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
        trees = _SharedTrees()
        for unit, policy in policies.items():
            root = self._source_tree_root(policy)
            trees.measure(
                (
                    "source",
                    contract.digest(policy["source_index_ref"]),
                    contract.digest(policy["binding"]),
                ),
                (root,),
                lambda: self._exact_source_tree(policy),
            )
            runtime = io.document(self.read_reference(policy["runtime_index_ref"]))
            if runtime.get("schema") == "autostop.j1.cold-runtime-index.v2":
                from scripts import j1_forward_cold_runtime_capsule

                j1_forward_cold_runtime_capsule.validate_index(
                    runtime, binding=policy["binding"], scope=package["scope"]
                )
                trees.measure(
                    (
                        "runtime",
                        contract.digest(policy["runtime_index_ref"]),
                        contract.digest(policy["binding"]),
                    ),
                    tuple(runtime["runtime_roots"].values()),
                    lambda: j1_forward_cold_runtime_capsule.verify_installed(runtime),
                )
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
        trees.finish()
        return trees

    def _source_tree_root(self, policy):
        root = Path(policy["source_root"])
        contract.need(
            root
            == Path("/opt/autostop-manager-cold-releases")
            / policy["binding"]["candidate_manager_sha"]
            / "source",
            "native_source_location",
        )
        io.protected_directory(root)
        return root

    def _exact_source_tree(self, policy):
        root = self._source_tree_root(policy)
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
        contract.need(
            self.scope != "production_native"
            or pending["schema"] == "autostop.j1.cold-pending-disposition.v2",
            "native_production_pending_v2_required",
        )
        if pending["schema"] == "autostop.j1.cold-pending-disposition.v2":
            return self._verify_pending_v2(validated)
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

    def _verify_pending_v2(self, validated):
        from scripts import j1_forward_cold_catalog as catalog

        pending, binding = validated["pending"], validated["package"]["binding"]
        inventory = io.document(self.read_reference(pending["inventory_ref"]))
        contract.fields(
            inventory,
            {
                "schema",
                "scope",
                "binding",
                "coverage",
                "rows",
                "source_checkpoint_ref",
                "old_crm_ingress_ref",
                "payload_semantics",
            },
            "native_pending_inventory_v2_fields",
        )
        contract.need(
            inventory["schema"] == "autostop.j1.cold-pending-inventory.v2"
            and inventory["scope"] == self.scope
            and inventory["binding"] == binding
            and inventory["coverage"]
            == "source_defined_durable_catalogs_with_retired_unenumerated_ram"
            and inventory["payload_semantics"] == "observed_record_fingerprint_unknown_only",
            "native_pending_inventory_v2_scope",
        )
        measured = catalog.verify_checkpoint(
            inventory["source_checkpoint_ref"], provider=self, binding=binding
        )
        contract.need(
            measured["old_crm_ingress_ref"] == inventory["old_crm_ingress_ref"]
            and measured["payload_semantics"] == inventory["payload_semantics"]
            and measured["ram_disposition"] == "unenumerated_retired_without_replay",
            "native_pending_measurement_scope",
        )
        actual = {}
        for row in measured["rows"]:
            contract.need(
                row["op_id"] not in actual
                and row["outcome"] == "unknown"
                and row["retry"] == "forbidden",
                "native_pending_measured_identity",
            )
            contract.fingerprint(row["fingerprint"], outcome="unknown")
            actual[row["op_id"]] = {key: row[key] for key in ("op_id", "fingerprint")}
        requests = {}
        contract.need(
            type(inventory["rows"]) is list and len(inventory["rows"]) <= 8192,
            "native_pending_inventory_v2_rows",
        )
        for row in inventory["rows"]:
            contract.fields(row, {"request", "evidence_refs"}, "native_inventory_row")
            request = contract.fields(
                row["request"], {"op_id", "fingerprint"}, "native_inventory_v2_request"
            )
            contract.need(
                contract.operation_id(request["op_id"])
                and request["op_id"] not in requests
                and row["evidence_refs"] == [inventory["source_checkpoint_ref"]],
                "native_pending_inventory_v2_identity",
            )
            requests[request["op_id"]] = request
        contract.need(
            requests == actual and set(actual) == {row["op_id"] for row in pending["requests"]},
            "native_pending_inventory_incomplete",
        )
        for row in pending["requests"]:
            contract.need(
                row["outcome"] == "unknown"
                and row["retry"] == "forbidden"
                and row["evidence_refs"] == [inventory["source_checkpoint_ref"]]
                and {key: row[key] for key in ("op_id", "fingerprint")} == actual[row["op_id"]],
                "native_pending_positive_outcome_requires_separate_terminal_protocol",
            )

    def verify_original_hold(self, validated):
        from scripts import j1_forward_cold_hold as hold

        hold.verify(validated, self)

    def kernel_profile(self, validated):
        kernel, policy = validated["kernel"], validated["kernel_policy"]
        expected = (
            kernel["supervisor_actor"]
            if kernel["schema"] == "autostop.j1.cold-kernel.v2"
            else kernel["actor"]
        )
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
        contract.need(
            self.scope != "production_native"
            or validated["kernel"]["schema"] == "autostop.j1.cold-kernel.v2",
            "native_production_kernel_v2_required",
        )
        if validated["kernel"]["schema"] == "autostop.j1.cold-kernel.v2":
            return self._verify_kernel_v2(validated)
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

    @contextmanager
    def _runtime_kernel_admitted(self, validated):
        """A public OPEN snapshot alone cannot relax the pre-start thread gate."""
        from scripts import j1_forward_cold_epoch as epochs
        from scripts import j1_forward_cold_state as cold_state

        binding = validated["package"]["binding"]
        path = STATE / "epochs" / binding["generation"] / "epoch.json"
        if not path.exists():
            yield False
            return
        epoch_ref = io.reference(path)
        epoch = io.document(self.read_reference(epoch_ref))
        package_ref = {
            "path": str(package_path(binding["attempt_id"])),
            "sha256": contract.digest(validated["package"]),
        }
        contract.need(epoch["package_ref"] == package_ref, "kernel_runtime_epoch_package")
        fence = epochs.verify(epoch, validated["package"], self)
        before = io.document(io.read_protected(fence["state_path"]))
        if before == epochs.closed_state(epoch):
            yield False
            return
        borrowed = getattr(self, "_borrowed_dispatch_fd", None)
        descriptor = epochs.locked_fence(fence, exclusive=False) if borrowed is None else borrowed
        try:
            if borrowed is not None:
                info, linked, pin = (
                    os.fstat(descriptor),
                    Path(fence["dispatch_lock_path"]).lstat(),
                    fence["dispatch_lock_identity"],
                )
                contract.need(
                    (info.st_dev, info.st_ino, info.st_ctime_ns)
                    == (linked.st_dev, linked.st_ino, linked.st_ctime_ns)
                    == (pin["device"], pin["inode"], pin["ctime_ns"]),
                    "kernel_runtime_lock_changed",
                )
                held_descriptor(controller(os.getpid()), descriptor, [info.st_dev, info.st_ino])
            current = io.document(io.read_protected(fence["state_path"]))
            contract.fields(
                current,
                {"schema", *epochs.COMMON, "state", "plan_ref", "commit_ref", "admission_ref"},
                "kernel_runtime_dispatch_fields",
            )
            contract.need(
                current["schema"] == "autostop.j1.cold-dispatch-state.v1"
                and current["state"] == "open"
                and current["admission_ref"] is not None
                and all(current[key] == epoch[key] for key in epochs.COMMON),
                "kernel_runtime_dispatch_not_open",
            )
            plan = io.document(self.read_reference(current["plan_ref"]))
            cold_state.validate_plan(plan, plan["base_sha256"])
            commit = io.document(self.read_reference(current["commit_ref"]))
            store = cold_state.ColdStore(STATE.parent.parent / "state", plan["base_sha256"])
            head = store.head()
            contract.need(
                head["sha256"] == current["commit_ref"]["sha256"]
                and current["commit_ref"]["path"]
                == str(store.history / f"{head['sequence']:020d}.json")
                and head["record"] == commit
                and commit["plan"] == plan
                and plan["cold_authority_package_ref"] == package_ref
                and plan["dispatch_epoch_ref"] == epoch_ref
                and plan["attempt_id"] == binding["attempt_id"],
                "kernel_runtime_actual_head",
            )
            projection = io.document(self.read_reference(current["admission_ref"]))
            contract.fields(
                projection,
                {"schema", *epochs.COMMON, "source_refs", "projected_refs"},
                "kernel_runtime_projection_fields",
            )
            contract.need(
                projection["schema"] == "autostop.j1.cold-dispatch-admission.v1"
                and all(projection[key] == epoch[key] for key in epochs.COMMON)
                and projection["source_refs"]["plan"] == current["plan_ref"]
                and projection["source_refs"]["commit"] == current["commit_ref"]
                and projection["source_refs"]["epoch"] == epoch_ref,
                "kernel_runtime_projection_binding",
            )
            yield True
            contract.need(
                io.document(io.read_protected(fence["state_path"])) == current
                and store.head() == head,
                "kernel_runtime_publication_raced",
            )
        finally:
            if borrowed is None:
                os.close(descriptor)

    def _verify_kernel_v2(self, validated):
        from scripts import j1_forward_cold_kernel as kernel_source

        kernel, binding = validated["kernel"], validated["package"]["binding"]
        policy = self.kernel_profile(validated)
        expected = kernel["supervisor_actor"]
        contract.need(
            kernel["unit"] == kernel_unit(binding["generation"]), "native_new_kernel_unit"
        )
        for key, name in (
            ("source_ref", "j1_forward_cold_kernel_supervisor.py"),
            ("rpc_ref", "j1_forward_cold_kernel_rpc.py"),
        ):
            contract.need(
                policy[key]["path"] == str(INSTALL / name)
                and policy[key]["sha256"] == io.sha(Path(__file__).with_name(name).read_bytes()),
                "native_kernel_reviewed_source",
            )
        contract.need(
            policy["argv"]
            == [
                policy["interpreter_ref"]["path"],
                "-I",
                "-S",
                "-B",
                policy["source_ref"]["path"],
                "--attempt",
                binding["attempt_id"],
            ],
            "native_kernel_fixed_command",
        )
        actual = self.systemd(kernel["unit"])
        expected_unit = io.document(self.read_reference(policy["unit_ref"]))
        contract.need(
            actual == expected_unit["properties"]
            and actual["Type"] == "notify"
            and actual["Restart"] == "no"
            and actual["RuntimeMaxUSec"] == "infinity"
            and actual["KillMode"] == "control-group"
            and actual["NotifyAccess"] == "main"
            and not actual["DropInPaths"]
            and all(
                not actual[key]
                for key in (
                    "ExecCondition",
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecStop",
                    "ExecStopPost",
                    "ExecReload",
                )
            )
            and actual["ActiveState"] == "active"
            and actual["SubState"] == "running"
            and actual["MainPID"] == str(expected["pid"])
            and commands(actual["ExecStart"]) == [" ".join(policy["argv"])]
            and kernel["actor"]["cgroup"] == expected["cgroup"] == "0::" + actual["ControlGroup"],
            "native_new_kernel_supervisor",
        )
        ready = io.document(self.read_reference(kernel["ready_ref"]))
        proof = io.document(self.read_reference(kernel["empty_thread_ref"]))
        contract.need(
            ready
            == {
                "schema": "autostop.j1.cold-kernel-ready.v2",
                "binding": binding,
                "phase": "ready",
                "actor": kernel["actor"],
                "supervisor_actor": expected,
                "preparation_ref": kernel["preparation_ref"],
                "empty_thread_ref": kernel["empty_thread_ref"],
                "thread_id": proof["thread_id"],
            },
            "native_kernel_ready_v2",
        )
        self._verify_kernel_cache(validated)
        with self._runtime_kernel_admitted(validated) as admitted:
            if admitted:
                kernel_source.verify_runtime_identity(validated, self)
            else:
                kernel_source.verify_preparation(validated, self)
            self._verify_kernel_libraries(validated)
        contract.need(self.systemd(kernel["unit"]) == actual, "native_kernel_unit_raced")
        self._verify_kernel_cache(validated)

    def _verify_kernel_libraries(self, validated):
        from scripts import j1_forward_cold_lifecycle

        kernel = validated["kernel"]
        context = {"policy_ref": kernel["policy_ref"]}
        for key in ("supervisor_actor", "actor"):
            if key == "actor":
                reference = validated["kernel_policy"]["appserver_ref"]
                contract.need(
                    kernel[key]["exe_ref"] == reference,
                    "kernel_mapped_appserver_binding",
                )
                j1_forward_cold_lifecycle._loaded_libraries(
                    kernel[key]["pid"], context, self, kernel_appserver_ref=reference
                )
            else:
                j1_forward_cold_lifecycle._loaded_libraries(kernel[key]["pid"], context, self)

    def _verify_kernel_cache(self, validated, *, check_tree=None):
        kernel, binding = validated["kernel"], validated["package"]["binding"]
        view = io.document(self.read_reference(kernel["cache_view_ref"]))
        contract.fields(
            view,
            {
                "schema",
                "binding",
                "kind",
                "path",
                "mount_id",
                "checkpoint_ref",
                "source_identity",
                "target_identity",
            },
            "cache_view_v2_fields",
        )
        base = STATE / "packages" / binding["attempt_id"] / "kernel"
        contract.need(
            view["schema"] == "autostop.j1.cold-cache-view.v2"
            and view["binding"] == binding
            and view["kind"] == "kernel_read_only_checkpoint_mount"
            and view["path"] == str(base / "cache-view"),
            "native_cache_view_kind",
        )
        checkpoint = io.document(self.read_reference(view["checkpoint_ref"]))
        contract.fields(
            checkpoint, {"schema", "scope", "binding", "root", "files"}, "cache_checkpoint_fields"
        )
        contract.need(
            checkpoint["schema"] == "autostop.j1.cold-cache-checkpoint.v1"
            and checkpoint["scope"] == self.scope
            and checkpoint["binding"] == binding
            and checkpoint["root"] == str(base / "cache-source"),
            "cache_checkpoint_binding",
        )
        source, target = Path(checkpoint["root"]), Path(view["path"])
        source_info, target_info = source.lstat(), target.lstat()
        contract.need(
            view["source_identity"]
            == view["target_identity"]
            == [source_info.st_dev, source_info.st_ino]
            == [target_info.st_dev, target_info.st_ino],
            "cache_bind_identity",
        )
        tree = self._cache_checkpoint_tree if check_tree is None else check_tree
        tree(checkpoint, source)
        tree(checkpoint, target)
        for pid in (1, kernel["actor"]["pid"], kernel["supervisor_actor"]["pid"]):
            rows = (Path("/proc") / str(pid) / "mountinfo").read_text().splitlines()
            matches = [row.split() for row in rows if row.split()[4] == view["path"]]
            contract.need(
                len(matches) == 1
                and {"ro", "nosuid", "nodev", "noexec"} <= set(matches[0][5].split(","))
                and (pid != 1 or int(matches[0][0]) == view["mount_id"]),
                "native_cache_view_not_read_only",
            )

    def _cache_checkpoint_tree(self, checkpoint, root):
        from scripts import j1_forward_cold_kernel as kernel_source

        io.protected_directory(root)
        rows = checkpoint["files"]
        contract.need(type(rows) is list and len(rows) <= 10000, "cache_checkpoint_limit")
        expected, total = {}, 0
        for row in rows:
            contract.fields(row, {"name", "bytes", "sha256", "mode"}, "cache_checkpoint_file")
            name = row["name"]
            contract.need(
                type(name) is str
                and name
                and str(PurePosixPath(name)) == name
                and not PurePosixPath(name).is_absolute()
                and ".." not in PurePosixPath(name).parts
                and "\\" not in name
                and "\0" not in name
                and type(row["bytes"]) is int
                and 0 <= row["bytes"] <= contract.MAX_RUNTIME_BYTES
                and type(row["sha256"]) is str
                and contract.HEX.fullmatch(row["sha256"])
                and type(row["mode"]) is int
                and 0 <= row["mode"] <= 0o777
                and not row["mode"] & 0o022,
                "cache_checkpoint_file_identity",
            )
            contract.need(name not in expected, "cache_checkpoint_duplicate")
            expected[name] = row
            total += row["bytes"]
        contract.need(
            list(expected) == sorted(expected) and total <= 512 * 1024**2,
            "cache_checkpoint_order_or_budget",
        )
        actual = set()
        for directory, folders, files in os.walk(root, followlinks=False):
            io.protected_directory(directory)
            for name in folders:
                io.protected_directory(Path(directory) / name)
            for name in files:
                path = Path(directory) / name
                logical = path.relative_to(root).as_posix()
                contract.need(logical in expected, "cache_checkpoint_unindexed_file")
                row, info = expected[logical], path.lstat()
                contract.need(
                    info.st_uid == 0
                    and not info.st_mode & 0o022
                    and info.st_nlink == 1
                    and info.st_size == row["bytes"]
                    and stat.S_IMODE(info.st_mode) == row["mode"]
                    and kernel_source.file_digest(path)["sha256"] == row["sha256"],
                    "cache_checkpoint_file_changed",
                )
                actual.add(logical)
        contract.need(actual == set(expected), "cache_checkpoint_inventory_incomplete")


def _tree_versions(roots, *, protect=None):
    versions = {}
    for root in roots:
        root = (io.protected_directory if protect is None else protect)(root)
        version = _executable_version(root.lstat())
        versions[str(root)] = version
        pending = [(str(root), version)]
        while pending:
            directory, expected = pending.pop()
            descriptor = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            try:
                contract.need(
                    _executable_version(os.fstat(descriptor)) == expected,
                    "native_tree_directory_changed",
                )
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        info = entry.stat(follow_symlinks=False)
                        path = directory + "/" + entry.name
                        contract.need(len(versions) < 32768, "native_tree_inventory_limit")
                        versions[path] = _executable_version(info)
                        if stat.S_ISDIR(info.st_mode):
                            pending.append((path, versions[path]))
                contract.need(
                    _executable_version(os.fstat(descriptor)) == expected
                    and _executable_version(os.stat(directory, follow_symlinks=False)) == expected,
                    "native_tree_directory_changed",
                )
            finally:
                os.close(descriptor)
    return versions


class _SharedTrees:
    """A shared physical tree is measured once, with a complete version exit cut."""

    def __init__(self, *, protect=None):
        self.measured = {}
        self.protect = protect

    def measure(self, key, roots, operation):
        roots = tuple(sorted(str(Path(path)) for path in roots))
        if key in self.measured:
            contract.need(self.measured[key][0] == roots, "native_tree_root_alias")
            return
        versions = _tree_versions(roots, protect=self.protect)
        operation()
        self.measured[key] = (roots, versions)

    def finish(self):
        for roots, before in self.measured.values():
            contract.need(
                _tree_versions(roots, protect=self.protect) == before,
                "native_installed_tree_changed",
            )


class _ObservationSources:
    """Pin source versions for one observation without caching native facts."""

    def __init__(self, provider):
        self.provider, self.versions, self.trees = provider, {}, None
        self.references = {}

    def _read(self, reference, method):
        contract.reference(reference)
        path = reference["path"]
        contract.need(
            path not in self.references or self.references[path] == reference,
            "native_source_reference_alias",
        )
        before = _executable_version(Path(path).lstat())
        contract.need(
            path not in self.versions or self.versions[path] == before,
            "native_source_version_changed",
        )
        raw = getattr(self.provider, method)(reference)
        contract.need(
            _executable_version(Path(path).lstat()) == before,
            "native_source_version_changed",
        )
        self.versions[path] = before
        self.references[path] = dict(reference)
        return raw

    def read_reference(self, reference):
        return self._read(reference, "read_reference")

    def read_opaque_reference(self, reference):
        return self._read(reference, "read_opaque_reference")

    def read_runtime_capsule(self, reference):
        return self._read(reference, "read_runtime_capsule")

    def check_versions(self):
        for path, version in self.versions.items():
            contract.need(
                _executable_version(Path(path).lstat()) == version,
                "native_source_version_changed",
            )
        if self.trees is not None:
            self.trees.finish()


def verify_package(reference, *, provider=None, required_scope="production_native"):
    return _observe_package(reference, provider=provider, required_scope=required_scope)[0]


def _observe_package(reference, *, provider=None, required_scope="production_native"):
    provider = NativeProvider() if provider is None else provider
    provider.boundary(reference)
    # Captured semantic results belong to this observation only. Every typed
    # source is physically reread after the independent native measurements.
    sources = _ObservationSources(provider)
    reader = contract.Reader(sources.read_reference)
    validated = contract._decode_package(reference, reader, required_scope=required_scope)
    contract.need(
        provider.scope == required_scope
        and provider.boot() == validated["package"]["binding"]["boot_id"],
        "native_scope_or_boot",
    )
    sources.trees = provider.verify_files(validated)
    for operation in (
        provider.verify_closure,
        provider.verify_retirement,
        provider.verify_ingress,
        provider.verify_original_hold,
        provider.verify_pending,
        provider.verify_kernel,
    ):
        operation(validated)
    # Establish the same interval at exit, including source and proof rereads.
    reader.recheck()
    sources.check_versions()
    provider.verify_closure(validated)
    contract._binding(validated["package"]["binding"], now_ns=time.monotonic_ns())
    return validated, sources


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


class CaptureKernelCut:
    def __init__(self, value, provider, sources):
        from scripts import j1_forward_cold_kernel, j1_forward_cold_lifecycle

        self.kernel_source, self.lifecycle = j1_forward_cold_kernel, j1_forward_cold_lifecycle
        self.value, self.provider, self.sources = value, provider, sources
        self.kernel, self.policy = value["kernel"], value["kernel_policy"]
        self.actors = [self.kernel["actor"]]
        if "supervisor_actor" in self.kernel:
            self.actors.append(self.kernel["supervisor_actor"])
        self.descriptors, self.cache, self.maps = [], {}, {}
        self._pin_graph(self.kernel)
        self.cwds = {expected["pid"]: self.policy["cwd"] for expected in self.actors}
        self.view = io.document(provider.read_reference(self.kernel["cache_view_ref"]))
        try:
            for actor in self.actors:
                self.descriptors.append(os.pidfd_open(actor["pid"], 0))
            if self.kernel["schema"] == "autostop.j1.cold-kernel.v2":
                checkpoint = io.document(provider.read_reference(self.view["checkpoint_ref"]))
                self.cache = {
                    root: _tree_versions((root,))
                    for root in (checkpoint["root"], self.view["path"])
                }
                self.preparation = io.document(
                    provider.read_reference(self.kernel["preparation_ref"])
                )
                home = io.document(provider.read_reference(self.preparation["home_ref"]))
                self.cwds[self.kernel["actor"]["pid"]] = home["cwd"]
                self.maps = {actor["pid"]: self._maps(actor["pid"]) for actor in self.actors}
            else:
                self.cache = {
                    self.view["path"]: {self.view["path"]: self._version(self.view["path"])}
                }
            # The physical cache cut precedes this full content verification.
            provider.verify_kernel(value)
            self.verify()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _version(path):
        return _executable_version(Path(path).lstat())

    def _pin_graph(self, document):
        pending, seen = [document], {}
        while pending:
            value = pending.pop()
            if type(value) is list:
                pending.extend(value)
            elif type(value) is dict:
                if set(value) != {"path", "sha256"}:
                    pending.extend(value.values())
                    continue
                contract.reference(value)
                path = value["path"]
                if path in seen:
                    contract.need(seen[path] == value, "capture_kernel_reference_alias")
                    continue
                seen[path] = dict(value)
                contract.need(len(seen) <= 32768, "capture_kernel_graph_limit")
                contract.need(
                    path not in self.sources.references or self.sources.references[path] == value,
                    "capture_kernel_reference_alias",
                )
                if path not in self.sources.versions:
                    method = "read_reference"
                    if Path(path).stat().st_size > contract.MAX_SOURCE_BYTES:
                        method = (
                            "read_runtime_capsule"
                            if path.endswith((".gz", ".tgz"))
                            else "read_opaque_reference"
                        )
                    self.sources._read(value, method)
                if path.endswith(".json"):
                    pending.append(io.document(self.sources.read_reference(value)))

    def _executable(self, path):
        expected = [
            actor["exe_ref"] for actor in self.actors if actor["exe_ref"]["path"] == str(path)
        ]
        contract.need(
            expected and all(row == expected[0] for row in expected), "capture_kernel_executable"
        )
        version = self.sources.versions.get(str(path))
        contract.need(
            version is not None and self._version(path) == version, "capture_kernel_source_changed"
        )
        return expected[0]["sha256"], version

    def _maps(self, pid):
        return self.lifecycle._library_mappings((Path("/proc") / str(pid) / "maps").read_text())

    def _mappings(self, pid):
        rows = self._maps(pid)
        contract.need(rows == self.maps[pid], "capture_kernel_mappings_changed")
        for row in rows:
            version = self.sources.versions.get(row[5])
            if version is None and self.sources.trees is not None:
                for _roots, versions in self.sources.trees.measured.values():
                    version = versions.get(row[5], version)
            info = Path(row[5]).lstat()
            contract.need(
                version == _executable_version(info)
                and stat.S_ISREG(info.st_mode)
                and [int(part, 16) for part in row[3].split(":")]
                == [os.major(info.st_dev), os.minor(info.st_dev)]
                and int(row[4]) == info.st_ino,
                "capture_kernel_mapped_library_changed",
            )
        contract.need(self._maps(pid) == rows, "capture_kernel_mappings_changed")

    def _cache_tree(self, _checkpoint, root):
        key = str(root)
        contract.need(
            key in self.cache and _tree_versions((root,)) == self.cache[key],
            "capture_cache_changed",
        )

    def verify(self):
        contract.need(not select.select(self.descriptors, [], [], 0)[0], "capture_kernel_exited")
        for expected in self.actors:
            contract.need(
                _actor(expected["pid"], self._executable) == expected
                and os.readlink(Path("/proc") / str(expected["pid"]) / "cwd")
                == self.cwds[expected["pid"]],
                "capture_kernel_actor_changed",
            )
        self.provider.read_reference(self.kernel["ready_ref"])
        if self.kernel["schema"] == "autostop.j1.cold-kernel.v2":
            unit = io.document(self.provider.read_reference(self.policy["unit_ref"]))
            contract.need(
                self.provider.systemd(self.kernel["unit"]) == unit["properties"],
                "capture_kernel_unit_changed",
            )
            fields = (
                (Path("/proc") / str(self.kernel["actor"]["pid"]) / "stat")
                .read_text()
                .rsplit(")", 1)[1]
                .split()
            )
            contract.need(
                int(fields[1]) == self.kernel["supervisor_actor"]["pid"],
                "capture_kernel_parent_changed",
            )
            expected = self.preparation["socket"]
            contract.need(
                self.kernel_source.socket_identity(expected["logical_path"])
                == {key: value for key, value in expected.items() if key != "peer"},
                "capture_kernel_socket_changed",
            )
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(1)
                peer.connect(expected["logical_path"])
                import struct

                contract.need(
                    list(
                        struct.unpack(
                            "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        )
                    )
                    == expected["peer"],
                    "capture_kernel_peer_changed",
                )
            self.provider._verify_kernel_cache(self.value, check_tree=self._cache_tree)
            for actor in self.actors:
                self._mappings(actor["pid"])
        elif self.provider.scope == "native_fixture":
            path = self.view["path"]
            contract.need(
                self._version(path) == self.cache[path][path]
                and stat.S_IMODE(Path(path).stat().st_mode) == 0o400,
                "capture_cache_changed",
            )
        else:
            unit = io.document(self.provider.read_reference(self.policy["unit_ref"]))
            contract.need(
                self.provider.systemd(self.kernel["unit"]) == unit["properties"],
                "capture_kernel_unit_changed",
            )
            for pid in (1, self.kernel["actor"]["pid"]):
                rows = [
                    row.split()
                    for row in (Path("/proc") / str(pid) / "mountinfo").read_text().splitlines()
                ]
                rows = [row for row in rows if row[4] == self.view["path"]]
                contract.need(
                    len(rows) == 1
                    and "ro" in rows[0][5].split(",")
                    and (pid != 1 or int(rows[0][0]) == self.view["mount_id"]),
                    "capture_cache_mount_changed",
                )
        contract.need(not select.select(self.descriptors, [], [], 0)[0], "capture_kernel_exited")

    def close(self):
        for descriptor in self.descriptors:
            os.close(descriptor)
        self.descriptors = []
