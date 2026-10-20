"""Disposable real-process producer for the versioned cold quarantine contract.

Only processes started by this object's fixed worker source can be retired.
No caller-supplied PID, path, command, environment, service or business API is
accepted. Fixture references cannot pass the production consumer boundary.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

# Every child must retain this owned cohort's temporary boundary. Sanitizing
# away TEMP/TMP made a child compare its /tmp default with the parent's private
# cohort directory, refusing the same fixture before its real guard could run.
ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    **dict.fromkeys(("TMPDIR", "TEMP", "TMP"), tempfile.gettempdir()),
}
WORKER = r"""
import fcntl, json, os, pathlib, socket, struct, sys, time, uuid
root, mode, unit = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(root / 'source'))
from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_guard as guard
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_fixture as fixture
from scripts import j1_forward_io as io
config = json.loads((root / 'worker-config.json').read_text())
def publish(path, value):
    raw = io.canonical(value)
    pending = path.with_name(path.name + '.publishing-' + str(os.getpid()))
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.link(pending, path, follow_symlinks=False); pending.unlink()
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
if mode == 'probe':
    provider = fixture.FixtureProvider(root)
    ref = provider.reference(root / 'authority.json')
    receipt = guard.inspect(ref, unit, config['binding_sha256'], provider=provider,
                            required_scope='native_fixture', current_process=True)
    publish(root / 'probe-effects' / unit, receipt)
    raise SystemExit(0)
if mode == 'candidate':
    provider = fixture.FixtureProvider(root)
    ref = provider.reference(root / 'authority.json')
    receipt = guard.inspect(ref, unit, config['binding_sha256'], provider=provider,
                            required_scope='native_fixture', current_process=True)
    binding = json.loads(pathlib.Path(os.environ['AUTOSTOP_COLD_RUNTIME_BINDING']).read_bytes())
    fence = json.loads(provider.read_reference(binding['ingress_fence_ref']))
    tombstones = json.loads(provider.read_reference(binding['tombstones_ref']))
    forbidden = {row['op_id'] for row in tombstones['rows']}
    requests = pathlib.Path(binding['paths']['state_dir']) / 'requests'; requests.mkdir(mode=0o700)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(binding['paths']['socket_path']); listener.listen(8)
    publish(root / 'probe-effects' / unit, receipt)
    print('READY', flush=True)
    while True:
        peer, _ = listener.accept()
        try:
            peer.settimeout(3)
            request = json.loads(peer.recv(4096))
            if set(request) != {'op_id','payload_sha256'} or request['op_id'] in forbidden:
                peer.sendall(b'DENY\n'); continue
            if str(uuid.UUID(request['op_id'])) != request['op_id'] or not contract.HEX.fullmatch(request['payload_sha256']):
                peer.sendall(b'DENY\n'); continue
            lock = os.open(fence['dispatch_lock_path'], os.O_RDONLY | os.O_NOFOLLOW)
            try:
                identity = fence['dispatch_lock_identity']; info = os.fstat(lock)
                if [info.st_dev,info.st_ino,info.st_ctime_ns] != [identity['device'],identity['inode'],identity['ctime_ns']]:
                    peer.sendall(b'DENY\n'); continue
                fcntl.flock(lock, fcntl.LOCK_SH)
                linked = pathlib.Path(fence['dispatch_lock_path']).stat()
                if [linked.st_dev,linked.st_ino,linked.st_ctime_ns] != [info.st_dev,info.st_ino,info.st_ctime_ns]:
                    peer.sendall(b'DENY\n'); continue
                state = json.loads(pathlib.Path(fence['state_path']).read_bytes())
                if state['state'] != 'open' or any(state[key] != binding[key] for key in ('attempt_id','boot_id','generation','epoch_id')):
                    peer.sendall(b'HELD\n'); continue
                journal = requests / request['op_id']; journal.mkdir(mode=0o700)
                publish(journal / 'effect.json', {'unit':unit, **request})
                peer.sendall(b'ACK\n')
            finally: os.close(lock)
        finally: peer.close()
if mode == 'kernel':
    ready = {'schema':'autostop.j1.cold-kernel-ready.v1','binding':config['binding'],
             'phase':'ready','pid':os.getpid(),'start_ticks':native.birth(os.getpid())}
    publish(root / 'kernel-ready.json', ready)
    print('READY', flush=True)
    while True: time.sleep(0.05)
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(str(root / 'ingress' / unit)); listener.listen(8)
print('READY', flush=True)
while True:
    peer, _address = listener.accept()
    stream = None
    try:
        pid, uid, gid = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if [pid, uid, gid] != config['client_credentials']:
            peer.sendall(b'FOREIGN\n'); continue
        stream = peer.makefile('rb')
        raw = stream.readline(4097)
        if len(raw) > 4096: continue
        request = json.loads(raw)
        if set(request) != {'op_id','payload_sha256'}: continue
        if str(uuid.UUID(request['op_id'])) != request['op_id']: continue
        if not contract.HEX.fullmatch(request['payload_sha256']): continue
        journal = root / 'requests' / request['op_id']
        journal.mkdir(mode=0o700)
        publish(journal / 'intent.json', {'unit':unit, **request})
        while (root / 'pause-before-effect').exists(): time.sleep(0.01)
        lock = os.open(root / 'dispatch.lock', os.O_RDONLY)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (root / 'ingress-held.json').exists() or (root / 'barriers' / unit).exists():
                outcome = 'not_applied_terminal'
            else:
                publish(journal / 'effect.json', {'unit':unit, **request})
                outcome = 'applied_readback'
            publish(journal / 'terminal.json', {'unit':unit, **request, 'outcome':outcome})
        finally: os.close(lock)
        peer.sendall(b'ACK\n')
    finally:
        if stream is not None: stream.close()
        peer.close()
"""


def _safe_file(path, root, *, limit=128 * 1024**2):
    path, root = Path(path), Path(root)
    contract.need(
        path.is_absolute() and path.is_relative_to(root) and ".." not in path.parts,
        "fixture_reference_outside_root",
    )
    for part in [root, *reversed(path.parent.relative_to(root).parents)]:
        # Relative parents are checked by the component walk below.
        if not part.is_absolute():
            continue
        info = part.lstat()
        contract.need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            "fixture_root_changed",
        )
    current = root
    for name in path.relative_to(root).parts[:-1]:
        current /= name
        info = current.lstat()
        contract.need(
            stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022,
            "fixture_directory_changed",
        )
    before = path.lstat()
    contract.need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_uid == os.geteuid()
        and not before.st_mode & 0o022
        and before.st_size <= limit,
        "fixture_file_changed",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, final = os.fstat(descriptor), path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        contract.need(
            len(raw) == before.st_size <= limit
            and all(
                getattr(before, key)
                == getattr(opened, key)
                == getattr(after, key)
                == getattr(final, key)
                for key in fields
            ),
            "fixture_read_changed",
        )
        return raw
    finally:
        os.close(descriptor)


class FixtureProvider(native.NativeProvider):
    scope = "native_fixture"

    def __init__(self, root):
        self.root = Path(root)
        contract.need(
            self.root.parent == Path(tempfile.gettempdir())
            and self.root.name.startswith("autostop-cold-native-"),
            "fixture_root_boundary",
        )
        self.manifest = json.loads(_safe_file(self.root / "fixture.json", self.root))
        contract.need(
            self.manifest["kind"] == "private_owned_process_topology_v1"
            and self.manifest["root"] == str(self.root),
            "fixture_manifest",
        )
        self.typed = None
        self.external = {}
        typed = self.root / "typed-fixture.json"
        if typed.exists():
            from scripts import j1_forward_cold_runtime_capsule as capsule

            self.typed = io.document(_safe_file(typed, self.root))
            contract.fields(
                self.typed,
                {"schema", "scope", "binding", "runtime_index_ref", "external_refs"},
                "fixture_typed_fields",
            )
            contract.need(
                self.typed["schema"] == "autostop.j1.private-typed-cold-fixture.v1"
                and self.typed["scope"] == self.scope,
                "fixture_typed_scope",
            )
            ref = self.typed["runtime_index_ref"]
            raw = _safe_file(ref["path"], self.root)
            contract.need(io.sha(raw) == ref["sha256"], "fixture_runtime_index_changed")
            runtime = io.document(raw)
            capsule.validate_index(runtime, binding=self.typed["binding"], scope=self.scope)
            contract.need(
                type(self.typed["external_refs"]) is list
                and len(self.typed["external_refs"]) <= 128,
                "fixture_external_limit",
            )
            self.external = capsule.allowed_paths(runtime)
            for row in self.typed["external_refs"]:
                contract.reference(row)
                contract.need(
                    row["path"] not in self.external or self.external[row["path"]] == row["sha256"],
                    "fixture_external_alias",
                )
                self.external[row["path"]] = row["sha256"]

    def reference(self, path):
        raw = self.read_path(path)
        return {"path": str(path), "sha256": io.sha(raw)}

    def read_path(self, path):
        if not Path(path).is_relative_to(self.root) and str(path) in self.external:
            from scripts import j1_forward_cold_kernel as kernel

            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                before = os.fstat(descriptor)
                contract.need(
                    stat.S_ISREG(before.st_mode)
                    and before.st_nlink == 1
                    and not before.st_mode & 0o022
                    and before.st_size <= 384 * 1024**2,
                    "fixture_external_protection",
                )
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw = stream.read(384 * 1024**2 + 1)
                contract.need(
                    len(raw) == before.st_size
                    and kernel.file_version(before)
                    == kernel.file_version(os.fstat(descriptor))
                    == kernel.file_version(Path(path).lstat())
                    and io.sha(raw) == self.external[str(path)],
                    "fixture_external_changed",
                )
            finally:
                os.close(descriptor)
            return raw
        if str(path) == self.manifest["interpreter"]["path"]:
            before = Path(path).stat()
            raw = Path(path).read_bytes()
            after = Path(path).stat()
            contract.need(
                before == after and io.sha(raw) == self.manifest["interpreter"]["sha256"],
                "fixture_interpreter_changed",
            )
            return raw
        return _safe_file(path, self.root)

    def read_reference(self, reference):
        contract.reference(reference)
        raw = self.read_path(reference["path"])
        contract.need(io.sha(raw) == reference["sha256"], "fixture_reference_digest")
        return raw

    def read_opaque_reference(self, reference):
        return contract.runtime_bytes(reference, self.read_reference(reference))

    def read_runtime_capsule(self, reference):
        raw = (
            self.read_path(reference["path"])
            if reference["path"] in self.external
            else _safe_file(reference["path"], self.root, limit=192 * 1024**2)
        )
        contract.need(io.sha(raw) == reference["sha256"], "fixture_reference_digest")
        return contract.runtime_bytes(reference, raw, capsule=True)

    def boundary(self, reference):
        contract.need(
            sys.platform.startswith("linux")
            and hasattr(os, "pidfd_open")
            and reference["path"] == str(self.root / "authority.json"),
            "fixture_native_boundary",
        )
        _safe_file(self.root / "fixture.json", self.root)

    def verify_files(self, validated):
        package = validated["package"]
        source = io.document(self.read_reference(package["source_package_ref"]))
        expected_hashes = {}
        for name in ("authority", "native", "guard", "history", "hold"):
            filename = "j1_forward_cold_" + name + ".py"
            raw = Path(__file__).with_name(filename).read_bytes()
            expected_hashes[name] = io.sha(raw)
            contract.need(
                io.sha(_safe_file(self.root / "source" / "scripts" / filename, self.root))
                == io.sha(raw),
                "fixture_actual_consumer_changed",
            )
        contract.need(
            package["binding"]["consumer_sha256"] == contract.digest(expected_hashes),
            "fixture_consumer_binding",
        )
        contract.need(
            source["guard_ref"]
            == self.reference(self.root / "source" / "scripts" / "j1_forward_cold_guard.py")
            and source["native_ref"]
            == self.reference(self.root / "source" / "scripts" / "j1_forward_cold_native.py"),
            "fixture_dispatch_source",
        )
        for unit, policy in validated["policies"].items():
            index = io.document(self.read_reference(policy["source_index_ref"]))
            root = self.root / "source"
            actual = {str(path) for path in root.rglob("*") if path.is_file()}
            contract.need(
                actual == {row["path"] for row in index["files"]}, "fixture_source_inventory"
            )
            doc = io.document(self.read_reference(policy["unit_ref"]))
            contract.need(
                doc["properties"]
                == {
                    "unit": unit,
                    "binding_sha256": contract.digest(package["binding"]),
                    "guard_sha256": source["guard_ref"]["sha256"],
                    "argv": policy["argv"],
                },
                "fixture_effective_dispatch",
            )
        if self.typed is not None:
            from scripts import j1_forward_cold_runtime_capsule as capsule

            trees = native._SharedTrees(protect=self._typed_read_directory)
            reference = self.typed["runtime_index_ref"]
            contract.need(
                all(
                    row["runtime_index_ref"] == reference for row in validated["policies"].values()
                ),
                "fixture_shared_runtime_required",
            )
            runtime = io.document(self.read_reference(reference))
            trees.measure(
                ("runtime", contract.digest(reference)),
                tuple(runtime["runtime_roots"].values()),
                lambda: capsule.verify_installed(runtime),
            )
            trees.measure(("source",), (self.root / "source",), lambda: None)
            trees.finish()
            return trees

    def _typed_read_directory(self, path):
        if Path(path).is_relative_to(self.root):
            cursor = self.root
            for parts in ((), *((name,) for name in Path(path).relative_to(self.root).parts)):
                if parts:
                    cursor /= parts[0]
                info = cursor.lstat()
                contract.need(
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == os.geteuid()
                    and not info.st_mode & 0o022,
                    "fixture_runtime_read_boundary",
                )
            return Path(path)
        runtime = io.document(self.read_reference(self.typed["runtime_index_ref"]))
        info = Path(path).lstat()
        contract.need(
            str(path) in runtime["runtime_roots"].values()
            and stat.S_ISDIR(info.st_mode)
            and not Path(path).is_symlink()
            and not info.st_mode & 0o022,
            "fixture_runtime_read_boundary",
        )
        return Path(path)

    def verify_closure(self, validated):
        hold, binding = validated["hold"], validated["package"]["binding"]
        contract.need(
            hold["marker_ref"]["path"] == str(self.root / "ingress-held.json")
            and hold["dispatch_lock_ref"]["path"] == str(self.root / "dispatch.lock"),
            "fixture_hold_paths",
        )
        marker = io.document(self.read_reference(hold["marker_ref"]))
        contract.need(marker["binding"] == binding, "fixture_hold_binding")
        deployment = (self.root / "deployment.lock").stat()
        lock = (self.root / "dispatch.lock").stat()
        contract.need(
            [deployment.st_dev, deployment.st_ino] == binding["deployment_lock_identity"],
            "fixture_deployment_changed",
        )
        native.held_descriptor(hold["controller"], hold["descriptor"], [lock.st_dev, lock.st_ino])
        native.held_descriptor(
            hold["controller"], marker["deployment_descriptor"], binding["deployment_lock_identity"]
        )
        for unit, reference in validated["closure"]["barriers"].items():
            contract.need(
                reference["path"] == str(self.root / "barriers" / unit)
                and self.read_reference(reference) == contract.DENY_BYTES,
                "fixture_barrier_changed",
            )

    def verify_retirement(self, validated):
        from scripts import j1_forward_cold_history as history

        rows = validated["retirement"]["actors"]
        contract.need(rows == self.manifest["owned_actors"], "fixture_cannot_adopt_actor")
        contract.need(
            validated["history"]["inventory"]["actors"] == self.manifest["owned_actors"],
            "fixture_history_cannot_adopt",
        )
        containment = io.document(self.read_reference(validated["origin"]["containment_ref"]))
        history.check_containment(containment, validated, validated["history"])
        contract.need(
            validated["origin"]["containment_ref"]
            == validated["quarantine"]["closed_population_ref"],
            "fixture_population_binding",
        )
        for expected in rows:
            native.retired(expected)

    def verify_original_hold(self, validated):
        from scripts import j1_forward_cold_hold as hold

        contract.need(
            validated["hold_checkpoint"]["database_ref"]["path"]
            == str(self.root / "registry.sqlite3"),
            "fixture_hold_database",
        )
        hold.verify(validated, self, fixture=True)

    def verify_ingress(self, validated):
        # Inspect only this fixture's endpoint IDs and its controller's FDs.
        # The pinned worker never forks or passes descriptors. Production uses
        # the separate whole-kernel observer and accepted preserved population.
        rows = [
            line.split(maxsplit=7) for line in Path("/proc/net/unix").read_text().splitlines()[1:]
        ]
        inodes = {int(row[6]) for row in rows if len(row) >= 7}
        paths = {row[7] for row in rows if len(row) == 8}
        for endpoint in validated["quarantine"]["endpoints"]:
            contract.need(
                endpoint["inode"] not in inodes and endpoint["path"] not in paths,
                "fixture_retired_listener_present",
            )
        owner = self.manifest["controller"]
        contract.need(
            validated["hold"]["controller"] == owner and native.controller(owner["pid"]) == owner,
            "fixture_controller_changed",
        )
        forbidden = {
            "pipe:[" + str(inode) + "]" for inode in validated["quarantine"]["pipe_inodes"]
        }
        for descriptor in (Path("/proc") / str(owner["pid"]) / "fd").iterdir():
            try:
                contract.need(
                    os.readlink(descriptor) not in forbidden, "fixture_old_pipe_still_owned"
                )
            except FileNotFoundError:
                continue

    def verify_pending(self, validated):
        pending = validated["pending"]
        if self.typed is not None:
            return self._typed_pending(validated)
        inventory = io.document(self.read_reference(pending["inventory_ref"]))
        contract.need(
            inventory
            == {
                "schema": "autostop.j1.cold-pending-inventory.v1",
                "scope": self.scope,
                "binding": validated["package"]["binding"],
                "coverage": "fixture_client_catalog_and_published_intents_only",
                "rows": [
                    {
                        "request": {key: row[key] for key in ("op_id", "payload_sha256")},
                        "evidence_refs": row["evidence_refs"],
                    }
                    for row in pending["requests"]
                ],
            },
            "fixture_pending_inventory",
        )
        for row in pending["requests"]:
            request = {key: row[key] for key in ("op_id", "payload_sha256")}
            client = io.document(
                self.read_path(self.root / "requests" / ("client-" + row["op_id"] + ".json"))
            )
            contract.need(client == request, "fixture_pending_client_identity")
            directory = self.root / "requests" / row["op_id"]
            terminal = directory / "terminal.json"
            outcome = "unknown"
            refs = []
            for name in ("intent.json", "effect.json", "terminal.json"):
                if (directory / name).exists():
                    refs.append(self.reference(directory / name))
            if terminal.exists():
                result = io.document(self.read_path(terminal))
                contract.need(
                    {key: result[key] for key in request} == request,
                    "fixture_pending_terminal_identity",
                )
                outcome = result["outcome"]
                if outcome == "applied_readback":
                    effect = io.document(self.read_path(directory / "effect.json"))
                    contract.need(
                        effect == {key: result[key] for key in ("unit", "op_id", "payload_sha256")},
                        "fixture_pending_actual_readback",
                    )
                elif outcome == "not_applied_terminal":
                    contract.need(
                        not (directory / "effect.json").exists(), "fixture_terminal_contradiction"
                    )
                else:
                    contract.need(False, "fixture_terminal_outcome")
            contract.need(
                row["outcome"] == outcome and row["evidence_refs"] == refs,
                "fixture_pending_not_inferred",
            )

    def verify_kernel(self, validated):
        if self.typed is not None:
            return self._typed_kernel(validated)
        kernel, binding = validated["kernel"], validated["package"]["binding"]
        policy = self.kernel_profile(validated)
        expected = kernel["actor"]
        unit_doc = io.document(self.read_reference(policy["unit_ref"]))
        contract.need(
            unit_doc["properties"]
            == {
                "unit": "private-cold-kernel",
                "argv": policy["argv"],
                "source_ref": policy["source_ref"],
            },
            "fixture_kernel_dispatch",
        )
        contract.need(
            kernel["unit"] == "private-cold-kernel"
            and expected["pid"] not in {row["pid"] for row in self.manifest["owned_actors"]}
            and native.actor(expected["pid"]) == expected,
            "fixture_new_kernel",
        )
        descriptor = os.pidfd_open(expected["pid"], 0)
        try:
            contract.need(not select.select([descriptor], [], [], 0)[0], "fixture_kernel_dead")
            ready = io.document(self.read_reference(kernel["ready_ref"]))
            contract.need(
                ready
                == {
                    "schema": "autostop.j1.cold-kernel-ready.v1",
                    "binding": binding,
                    "phase": "ready",
                    "pid": expected["pid"],
                    "start_ticks": expected["start_ticks"],
                },
                "fixture_kernel_ready",
            )
            view = io.document(self.read_reference(kernel["cache_view_ref"]))
            contract.need(
                view["binding"] == binding
                and view["kind"] == "private_read_only_file"
                and view["path"] == str(self.root / "private-cache")
                and stat.S_IMODE(Path(view["path"]).stat().st_mode) == 0o400,
                "fixture_cache_mode",
            )
            contract.need(native.actor(expected["pid"]) == expected, "fixture_kernel_raced")
        finally:
            os.close(descriptor)

    def _typed_pending(self, validated):
        from scripts import j1_forward_cold_catalog as catalog

        pending, binding = validated["pending"], validated["package"]["binding"]
        contract.need(
            pending["schema"] == "autostop.j1.cold-pending-disposition.v2",
            "fixture_pending_v2_required",
        )
        inventory = io.document(self.read_reference(pending["inventory_ref"]))
        checkpoint_ref = inventory["source_checkpoint_ref"]
        checkpoint = io.document(self.read_reference(checkpoint_ref))
        policy = io.document(self.read_reference(checkpoint["policy_ref"]))
        actual = catalog.measure(
            policy, binding, provider=self, verify_path_binding=self._typed_catalog_path
        )
        contract.need(actual == checkpoint["measurement"], "fixture_catalog_changed")
        self.verify_old_crm_ingress(checkpoint["old_crm_ingress_ref"], binding=binding)
        requests = [{**row, "evidence_refs": [checkpoint_ref]} for row in actual["rows"]]
        contract.need(
            pending["requests"] == requests
            and inventory
            == {
                "schema": "autostop.j1.cold-pending-inventory.v2",
                "scope": self.scope,
                "binding": binding,
                "coverage": "source_defined_durable_catalogs_with_retired_unenumerated_ram",
                "payload_semantics": "observed_record_fingerprint_unknown_only",
                "source_checkpoint_ref": checkpoint_ref,
                "old_crm_ingress_ref": checkpoint["old_crm_ingress_ref"],
                "rows": [
                    {
                        "request": {key: row[key] for key in ("op_id", "fingerprint")},
                        "evidence_refs": [checkpoint_ref],
                    }
                    for row in requests
                ],
            },
            "fixture_pending_measurement_mismatch",
        )

    def _typed_catalog_path(self, unit, source, policy):
        contract.need(unit in contract.ALLOWED, "fixture_catalog_unit")
        directory = self.root / "legacy-catalog" / unit
        path = directory / (
            source["kind"] + (".json" if source["kind"] == "idempotency_json" else ".sqlite3")
        )
        binding = io.document(self.read_reference(source["path_binding_ref"]))
        contract.need(
            source["path"] == str(path)
            and binding
            == {
                "schema": "autostop.j1.private-catalog-path.v1",
                "scope": self.scope,
                "binding": policy["binding"],
                "unit": unit,
                "kind": source["kind"],
                "source_ref": self.reference(path),
                "predecessor_ref": policy["predecessor_ref"],
            },
            "fixture_catalog_path_binding",
        )

    def verify_old_crm_ingress(self, reference, *, binding):
        body = io.document(self.read_reference(reference))
        contract.fields(
            body, {"schema", "scope", "binding", "actor", "source_ref"}, "fixture_old_crm_fields"
        )
        contract.need(
            body["schema"] == "autostop.j1.private-old-crm-retired.v1"
            and body["scope"] == self.scope
            and body["binding"] == binding,
            "fixture_old_crm_binding",
        )
        self.read_reference(body["source_ref"])
        expected = body["actor"]
        try:
            same = native.birth(expected["pid"]) == expected["start_ticks"]
        except (FileNotFoundError, ProcessLookupError):
            same = False
        contract.need(not same, "fixture_old_crm_alive")

    def systemd(self, unit):
        contract.need(self.typed is not None, "fixture_no_systemd")
        body = io.document(self.read_path(self.root / "typed-kernel-state.json"))
        expected = body["actor"]
        contract.need(
            unit == body["unit"] and native.actor(expected["pid"]) == expected,
            "fixture_kernel_unit_changed",
        )
        return {"boundary": "private_process_without_systemd", "unit": unit, "actor": expected}

    def _verify_kernel_cache(self, validated, *, check_tree=None):
        if self.typed is None:
            return super()._verify_kernel_cache(validated, check_tree=check_tree)
        view = io.document(self.read_reference(validated["kernel"]["cache_view_ref"]))
        checkpoint = io.document(self.read_reference(view["checkpoint_ref"]))
        base = (
            self.root
            / "execution/packages"
            / validated["package"]["binding"]["attempt_id"]
            / "kernel"
        )
        contract.need(
            view["kind"] == "private_permission_checkpoint_directories"
            and view["binding"] == checkpoint["binding"] == validated["package"]["binding"]
            and checkpoint["root"] == str(base / "cache-source")
            and view["path"] == str(base / "cache-view"),
            "fixture_cache_binding",
        )
        for root in (checkpoint["root"], view["path"]):
            if check_tree is not None:
                check_tree(checkpoint, root)
            else:
                actual = []
                for path in sorted(Path(root).rglob("*")):
                    contract.need(
                        path.is_file()
                        and not path.is_symlink()
                        and stat.S_IMODE(path.stat().st_mode) == 0o400,
                        "fixture_cache_protection",
                    )
                    actual.append(
                        {
                            "name": str(path.relative_to(root)),
                            "sha256": io.sha(self.read_path(path)),
                            "bytes": path.stat().st_size,
                        }
                    )
                contract.need(actual == checkpoint["files"], "fixture_cache_changed")

    @contextmanager
    def _runtime_kernel_admitted(self, validated):
        # A fixture must not consult the production STATE namespace, even for
        # an absent random epoch. The actual private HEAD/projection is checked.
        from scripts import j1_forward_cold_epoch as epochs
        from scripts import j1_forward_cold_state as cold_state

        path = self.root / "execution/epoch.json"
        if self.typed is None or not path.exists():
            yield False
            return
        epoch = io.document(self.read_path(path))
        contract.need(
            epoch["package_ref"] == self.reference(self.root / "authority.json"),
            "fixture_runtime_package",
        )
        fence = epochs.verify(epoch, validated["package"], self)
        if io.document(self.read_path(fence["state_path"])) == epochs.closed_state(epoch):
            yield False
            return
        descriptor = epochs.locked_fence(fence, exclusive=False)
        try:
            state = io.document(self.read_path(fence["state_path"]))
            contract.need(
                state["state"] == "open" and state["admission_ref"] is not None,
                "fixture_runtime_not_open",
            )
            plan = io.document(self.read_reference(state["plan_ref"]))
            store = cold_state.ColdStore(self.root / "forward/state", plan["base_sha256"])
            head = store.head()
            contract.need(
                state["commit_ref"]["path"] == str(store.history / f"{head['sequence']:020d}.json")
                and state["commit_ref"]["sha256"] == head["sha256"]
                and head["record"]["plan"] == plan
                and plan["cold_authority_package_ref"] == epoch["package_ref"],
                "fixture_runtime_actual_head",
            )
            projection = io.document(self.read_reference(state["admission_ref"]))
            contract.need(
                projection["source_refs"]["plan"] == state["plan_ref"]
                and projection["source_refs"]["commit"] == state["commit_ref"]
                and projection["source_refs"]["head"] == self.reference(store.head_path),
                "fixture_runtime_projection",
            )
            for name, reference in projection["source_refs"].items():
                contract.need(
                    self.read_reference(reference)
                    == self.read_reference(projection["projected_refs"][name]),
                    "fixture_runtime_projection_changed",
                )
            yield True
            contract.need(
                store.head() == head and io.document(self.read_path(fence["state_path"])) == state,
                "fixture_runtime_head_raced",
            )
        finally:
            os.close(descriptor)

    def kernel_rpc_factory(self, path, deadline):
        contract.need(self.typed is not None, "fixture_artificial_auth_scope")
        return _ArtificialAccountRPC(path, deadline)

    def _typed_kernel(self, validated):
        from scripts import j1_forward_cold_kernel as kernel

        body, binding = validated["kernel"], validated["package"]["binding"]
        contract.need(body["schema"] == "autostop.j1.cold-kernel.v2", "fixture_kernel_v2_required")
        policy = self.kernel_profile(validated)
        unit = io.document(self.read_reference(policy["unit_ref"]))
        contract.need(self.systemd(body["unit"]) == unit["properties"], "fixture_kernel_unit")
        ready = io.document(self.read_reference(body["ready_ref"]))
        proof = io.document(self.read_reference(body["empty_thread_ref"]))
        contract.need(
            ready
            == {
                "schema": "autostop.j1.cold-kernel-ready.v2",
                "binding": binding,
                "phase": "ready",
                "actor": body["actor"],
                "supervisor_actor": body["supervisor_actor"],
                "preparation_ref": body["preparation_ref"],
                "empty_thread_ref": body["empty_thread_ref"],
                "thread_id": proof["thread_id"],
            },
            "fixture_kernel_ready_v2",
        )
        self._verify_kernel_cache(validated)
        with self._runtime_kernel_admitted(validated) as admitted:
            if admitted:
                kernel.verify_runtime_identity(validated, self)
            else:
                kernel.verify_preparation(validated, self)
            self._verify_kernel_libraries(validated)
        contract.need(self.systemd(body["unit"]) == unit["properties"], "fixture_kernel_raced")


class _ArtificialAccountRPC:
    """Actual offline socket/protocol; only account truth is an explicit fixture assumption."""

    def __init__(self, path, deadline):
        from scripts import j1_forward_cold_kernel_rpc

        self.actual = j1_forward_cold_kernel_rpc.RPC(path, deadline)
        self.peer, self.initialized = self.actual.peer, self.actual.initialized

    def call(self, method, params):
        value = self.actual.call(method, params)
        if method == "account/read":
            contract.need(
                value.get("account") is None and value.get("requiresOpenaiAuth") is True,
                "fixture_unexpected_real_auth",
            )
            return {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}
        return value

    def _exchange(self, method, params):
        return self.actual._exchange(method, params)

    def close(self):
        self.actual.close()


class Fixture:
    """Own every effect handle before spawning. No outside PID can be signalled."""

    def __init__(
        self,
        *,
        ttl_seconds=120,
        candidate_crm_sha="1" * 40,
        candidate_manager_sha="2" * 40,
        candidates=False,
    ):
        contract.need(
            sys.platform.startswith("linux")
            and hasattr(os, "pidfd_open")
            and hasattr(signal, "pidfd_send_signal"),
            "fixture_linux_pidfd_required",
        )
        self.root = Path(tempfile.mkdtemp(prefix="autostop-cold-native-"))
        self.children, self.pidfds, self.actors, self.clients = [], {}, [], []
        self.dispatch_fd = self.deployment_fd = None
        self.package_ref = None
        self.catalog = {}
        contract.need(type(candidates) is bool, "fixture_candidate_mode")
        self.candidates = candidates
        contract.need(
            all(
                type(value) is str and contract.SHA1.fullmatch(value)
                for value in (candidate_crm_sha, candidate_manager_sha)
            ),
            "fixture_candidate_revision",
        )
        self.candidate_crm_sha, self.candidate_manager_sha = (
            candidate_crm_sha,
            candidate_manager_sha,
        )
        try:
            self._prepare(ttl_seconds)
        except BaseException:
            self.close()
            raise

    def _mkdir(self, relative):
        path = self.root / relative
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def publish(self, relative, value, *, raw=False, mode=0o600):
        path = self.root / relative
        data = value if raw else io.canonical(value)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"path": str(path), "sha256": io.sha(data)}

    def doc(self, name, **extra):
        return {
            "schema": "autostop.j1.cold-" + name + ".v1",
            "scope": "native_fixture",
            "binding": self.binding,
            **extra,
        }

    def _prepare(self, ttl):
        import fcntl

        contract.need(type(ttl) is int and 0 < ttl <= 900, "fixture_ttl")
        for folder in (
            "source/scripts",
            "ingress",
            "barriers",
            "requests",
            "probe-effects",
            "evidence",
        ):
            self._mkdir(folder)
        for name in ("deployment.lock", "dispatch.lock"):
            self.publish(name, b"", raw=True)
        self.deployment_fd = os.open(self.root / "deployment.lock", os.O_RDONLY | os.O_CLOEXEC)
        fcntl.flock(self.deployment_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(self.deployment_fd)
        now = time.monotonic_ns()
        hashes = {
            name: io.sha(Path(__file__).with_name("j1_forward_cold_" + name + ".py").read_bytes())
            for name in ("authority", "native", "guard", "history", "hold")
        }
        self.binding = {
            "attempt_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "consumer_sha256": contract.digest(hashes),
            "candidate_crm_sha": self.candidate_crm_sha,
            "candidate_manager_sha": self.candidate_manager_sha,
            "deployment_lock_identity": [info.st_dev, info.st_ino],
            "not_before_ns": now,
            "deadline_ns": now + ttl * 10**9,
        }
        for name in (
            "j1_forward_cold_authority.py",
            "j1_forward_cold_native.py",
            "j1_forward_cold_guard.py",
            "j1_forward_cold_history.py",
            "j1_forward_cold_hold.py",
            "j1_forward_cold_fixture.py",
            "j1_forward_io.py",
        ):
            self.publish(
                "source/scripts/" + name, Path(__file__).with_name(name).read_bytes(), raw=True
            )
        if self.candidates:
            from scripts import j1_forward_cold_guard

            for name in j1_forward_cold_guard.BOOTSTRAP_HASHES:
                target = "source/scripts/" + name + ".py"
                if not (self.root / target).exists():
                    self.publish(
                        target, Path(__file__).with_name(name + ".py").read_bytes(), raw=True
                    )
        self.publish("source/scripts/__init__.py", b"", raw=True)
        self.publish("source/worker.py", WORKER.encode(), raw=True)
        interpreter = Path(sys.executable).resolve()
        self.interpreter = {"path": str(interpreter), "sha256": io.sha(interpreter.read_bytes())}
        self.publish(
            "worker-config.json",
            {
                "binding": self.binding,
                "binding_sha256": contract.digest(self.binding),
                "client_credentials": [os.getpid(), os.geteuid(), os.getegid()],
            },
        )
        for unit in contract.ALLOWED:
            self._spawn("old", unit)
        self.publish(
            "fixture.json",
            {
                "kind": "private_owned_process_topology_v1",
                "root": str(self.root),
                "interpreter": self.interpreter,
                "owned_actors": self.actors,
                "controller": native.controller(os.getpid()),
            },
        )
        self.provider = FixtureProvider(self.root)

    def _spawn(self, mode, unit):
        process = subprocess.Popen(
            [
                str(Path(sys.executable).resolve()),
                "-I",
                "-S",
                "-B",
                str(self.root / "source/worker.py"),
                str(self.root),
                mode,
                unit,
            ],
            cwd=self.root,
            env=ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        self.children.append(process)
        descriptor = os.pidfd_open(process.pid, 0)
        self.pidfds[process.pid] = descriptor
        ready, _, _ = select.select([process.stdout], [], [], 5)
        contract.need(ready and process.stdout.readline() == b"READY\n", "fixture_worker_start")
        record = native.actor(process.pid)
        if mode == "old":
            self.actors.append(record)
        return process, record

    def submit(self, unit, *, op_id=None, payload_sha256=None, wait=True):
        contract.need(
            self.package_ref is None and unit in contract.ALLOWED, "fixture_submit_closed"
        )
        op_id = str(uuid.uuid4()) if op_id is None else op_id
        payload_sha256 = io.sha(b"synthetic-request") if payload_sha256 is None else payload_sha256
        request = {"op_id": op_id, "payload_sha256": payload_sha256}
        contract.need(op_id not in self.catalog, "fixture_request_identity_reused")
        self.catalog[op_id] = request
        self.publish("requests/client-" + op_id + ".json", request)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.clients.append(client)
        client.settimeout(5)
        client.connect(str(self.root / "ingress" / unit))
        client.sendall(io.canonical(request))
        if wait:
            contract.need(client.recv(16) == b"ACK\n", "fixture_request_ack")
        return request

    def _retire(self, process, expected):
        descriptor = self.pidfds[process.pid]
        contract.need(
            expected["pid"] == process.pid and native.actor(process.pid) == expected,
            "fixture_owned_actor_changed",
        )
        signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
        contract.need(bool(select.select([descriptor], [], [], 5)[0]), "fixture_retire_timeout")
        process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        native.retired(expected)

    def produce(self):
        import fcntl

        if self.package_ref is not None:
            native.verify_package(
                self.package_ref, provider=self.provider, required_scope="native_fixture"
            )
            return dict(self.package_ref)
        contract._binding(self.binding, now_ns=time.monotonic_ns())
        barriers = {
            unit: self.publish("barriers/" + unit, contract.DENY_BYTES, raw=True)
            for unit in contract.UNITS
        }
        marker = self.publish(
            "ingress-held.json",
            {
                "schema": "autostop.j1.cold-ingress-held.v1",
                "binding": self.binding,
                "deployment_descriptor": self.deployment_fd,
            },
        )
        self.dispatch_fd = os.open(self.root / "dispatch.lock", os.O_RDONLY | os.O_CLOEXEC)
        fcntl.flock(self.dispatch_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        hold = self.publish(
            "evidence/hold.json",
            self.doc(
                "hold",
                marker_ref=marker,
                dispatch_lock_ref=self.provider.reference(self.root / "dispatch.lock"),
                controller=native.controller(os.getpid()),
                descriptor=self.dispatch_fd,
            ),
        )
        closure = self.publish("evidence/closure.json", self.doc("closure", barriers=barriers))
        endpoints = []
        socket_rows = [
            line.split(maxsplit=7) for line in Path("/proc/net/unix").read_text().splitlines()[1:]
        ]
        for unit in contract.ALLOWED:
            path = str(self.root / "ingress" / unit)
            matches = [row for row in socket_rows if len(row) == 8 and row[7] == path]
            contract.need(
                sum(row[3] == "00010000" for row in matches) == 1, "fixture_original_listener"
            )
            endpoints.extend({"path": path, "inode": int(row[6])} for row in matches)
        pipe_inodes = sorted(
            {
                int(os.readlink(Path("/proc") / str(actor["pid"]) / "fd" / str(fd))[6:-1])
                for actor in self.actors
                for fd in (1, 2)
            }
        )
        predecessor, predecessor_scope, original_hold, hold_checkpoint = self._history(
            endpoints, pipe_inodes
        )
        self._cold_predecessor = predecessor
        containment = self.publish(
            "evidence/population.json",
            self.doc(
                "closed-population",
                predecessor_scope_ref=predecessor_scope,
                actors_sha256=contract.digest(self.actors),
                cgroup_roots=sorted(
                    {"/sys/fs/cgroup" + row["cgroup"].removeprefix("0::") for row in self.actors}
                ),
            ),
        )
        origin = self.publish(
            "evidence/actors.json",
            self.doc(
                "actor-scope",
                actors=self.actors,
                containment_ref=containment,
                predecessor_scope_ref=predecessor_scope,
            ),
        )
        for process, expected in zip(self.children, self.actors, strict=True):
            self._retire(process, expected)
        retirement = self.publish(
            "evidence/retirement.json",
            self.doc(
                "retirement",
                origin_actor_scope_ref=origin,
                actors=self.actors,
                completed_ns=time.monotonic_ns(),
            ),
        )
        quarantine = self.publish(
            "evidence/quarantine.json",
            self.doc(
                "quarantine",
                retirement_ref=retirement,
                hold_ref=hold,
                endpoints=endpoints,
                pipe_inodes=pipe_inodes,
                unparsed_ingress="retired_without_replay",
                closed_population_ref=containment,
            ),
        )
        pending = self._pending(quarantine)
        if self.candidates:
            from scripts import j1_forward_cold_epoch

            j1_forward_cold_epoch.fixture_inputs(self, pending)
        source, policies, kernel = self._produce_kernel(predecessor, closure, hold)
        lifecycle = self.publish(
            "evidence/lifecycle.json",
            self.doc(
                "lifecycle",
                phase="cold_quarantined",
                kernel_ref=kernel,
                closure_ref=closure,
                hold_ref=hold,
                policies=policies,
                pending_ref=pending,
            ),
        )
        raw = contract.compile_package(
            str(self.root / "authority.json"),
            scope="native_fixture",
            binding=self.binding,
            predecessor_ref=predecessor,
            source_package_ref=source,
            policies=policies,
            evidence_refs={
                "closure_ref": closure,
                "hold_ref": hold,
                "retirement_ref": retirement,
                "quarantine_ref": quarantine,
                "pending_ref": pending,
                "kernel_ref": kernel,
                "lifecycle_ref": lifecycle,
                "original_hold_ref": original_hold,
                "hold_checkpoint_ref": hold_checkpoint,
            },
            read_reference=self.provider.read_reference,
        )
        self.package_ref = self.publish("authority.json", raw, raw=True)
        native.verify_package(
            self.package_ref, provider=self.provider, required_scope="native_fixture"
        )
        return dict(self.package_ref)

    def _produce_kernel(self, _predecessor, _closure, _hold):
        _kernel_process, kernel_actor = self._spawn("kernel", "private-cold-kernel")
        self.publish("private-cache", b"synthetic-only", raw=True, mode=0o400)
        view = self.publish(
            "evidence/cache-view.json",
            {
                "schema": "autostop.j1.cold-cache-view.v1",
                "binding": self.binding,
                "kind": "private_read_only_file",
                "path": str(self.root / "private-cache"),
                "mount_id": None,
            },
        )
        source, policies = self._policies()
        kernel_policy = self._kernel_policy()
        kernel = self.publish(
            "evidence/kernel.json",
            self.doc(
                "kernel",
                actor=kernel_actor,
                unit="private-cold-kernel",
                policy_ref=kernel_policy,
                ready_ref=self.provider.reference(self.root / "kernel-ready.json"),
                cache_view_ref=view,
            ),
        )
        return source, policies, kernel

    def _history(self, endpoints, pipes):
        import sqlite3

        from scripts import j1_forward_cold_hold as hold

        origin_id = str(uuid.uuid4())
        original_hash = io.sha(("release-attempt:" + origin_id).encode())
        database = self.root / "registry.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.executescript("""
                CREATE TABLE manager_automation_controller_runtime(singleton INTEGER PRIMARY KEY,
                    hold_enabled INTEGER,hold_reason TEXT,hold_owner_hash TEXT,hold_attempt_hash TEXT,hold_revision INTEGER);
                CREATE TABLE manager_automation_runtime(lease_until TEXT);
                CREATE TABLE manager_automation_outbox(status TEXT);
            """)
            connection.execute(
                "INSERT INTO manager_automation_controller_runtime VALUES(1,1,?,?,?,1)",
                ("release", "a" * 64, original_hash),
            )
            connection.commit()
        finally:
            connection.close()
        database.chmod(0o600)
        database_ref = self.provider.reference(database)
        backup_ref = self.publish(
            "evidence/registry-quiet.sqlite3", self.provider.read_reference(database_ref), raw=True
        )
        identity = hold.file_identity(database)
        measurement = hold.measure(
            database, identity, database_ref["sha256"], origin_id, read_file=self.provider.read_path
        )
        status = self.publish(
            "evidence/original-hold-status.json",
            {
                "global_hold": {
                    "enabled": True,
                    "reason": "release",
                    "attempt_hash": original_hash,
                },
                "active_leases": 0,
                "outbox_sending": 0,
            },
        )
        original_hold = self.publish(
            "evidence/original-hold.json",
            {
                "schema": "autostop.j1.crm-forward-recovery-hold.v2",
                "origin_attempt_id": origin_id,
                "attempt_hash": original_hash,
                "reason": "release",
                "enabled": True,
                "scheduler_counts": {"active_leases": 0, "outbox_sending": 0},
                "status_ref": status,
            },
        )
        checkpoint = self.publish(
            "evidence/original-hold-checkpoint.json",
            self.doc(
                "hold-checkpoint",
                origin_attempt_id=origin_id,
                original_hold_ref=original_hold,
                database_ref=database_ref,
                database_identity=identity,
                quiet_backup_ref=backup_ref,
                measurement=measurement,
            ),
        )
        scope = self.publish(
            "evidence/preserved-scope.json",
            {
                "schema": "autostop.j1.cold-fixture-preserved-scope.v1",
                "attempt_id": origin_id,
                "boot_id": self.binding["boot_id"],
                "actors": self.actors,
                "endpoints": endpoints,
                "pipe_inodes": pipes,
            },
        )
        baseline = self.publish("evidence/preserved-baseline.json", {"attempt_id": origin_id})
        plan = self.publish(
            "evidence/preserved-plan.json", {"attempt_id": origin_id, "baseline_ref": baseline}
        )
        closure = self.publish(
            "evidence/preserved-closure.json", {"attempt_id": origin_id, "process_scope_ref": scope}
        )
        anchor = self.publish(
            "evidence/historical-origin.json",
            {
                "schema": "autostop.j1.cold-predecessor-anchor.v1",
                "scope": "native_fixture",
                "origin_attempt_id": origin_id,
                "plan_ref": plan,
                "baseline_ref": baseline,
                "scope_ref": scope,
                "closure_ref": closure,
                "original_hold_ref": original_hold,
            },
        )
        return anchor, scope, original_hold, checkpoint

    def _kernel_policy(self):
        source_ref = self.provider.reference(self.root / "source" / "worker.py")
        argv = [
            self.interpreter["path"],
            "-I",
            "-S",
            "-B",
            source_ref["path"],
            str(self.root),
            "kernel",
            "private-cold-kernel",
        ]
        environment = self.publish(
            "evidence/kernel-environment.json",
            self.doc("environment", unit="private-cold-kernel", values=ENVIRONMENT),
        )
        unit_ref = self.publish(
            "evidence/kernel-unit.json",
            self.doc(
                "unit",
                unit="private-cold-kernel",
                files=[source_ref],
                properties={"unit": "private-cold-kernel", "argv": argv, "source_ref": source_ref},
            ),
        )
        return self.publish(
            "evidence/kernel-policy.json",
            self.doc(
                "kernel-policy",
                unit="private-cold-kernel",
                source_ref=source_ref,
                interpreter_ref=self.interpreter,
                runtime_index_ref=self.provider.reference(
                    self.root / "evidence/runtime-index.json"
                ),
                startup_index_ref=self.provider.reference(
                    self.root / "evidence/startup-index.json"
                ),
                environment_ref=environment,
                unit_ref=unit_ref,
                argv=argv,
                cwd=str(self.root),
                credentials={
                    "uid": os.geteuid(),
                    "gid": os.getegid(),
                    "groups": sorted(os.getgroups()),
                },
            ),
        )

    def _pending(self, quarantine):
        requests, inventory = [], []
        published = {path.parent.name for path in (self.root / "requests").glob("*/intent.json")}
        contract.need(published <= set(self.catalog), "fixture_unregistered_intent")
        for op_id, request in self.catalog.items():
            directory = self.root / "requests" / op_id
            refs = []
            terminal = directory / "terminal.json"
            outcome = "unknown"
            for name in ("intent.json", "effect.json", "terminal.json"):
                path = directory / name
                if path.exists():
                    refs.append(self.provider.reference(path))
            if terminal.exists():
                result = json.loads(_safe_file(terminal, self.root))
                contract.need(
                    result["op_id"] == op_id
                    and result["payload_sha256"] == request["payload_sha256"],
                    "fixture_terminal_identity",
                )
                outcome = result["outcome"]
                if outcome == "applied_readback":
                    effect = json.loads(_safe_file(directory / "effect.json", self.root))
                    contract.need(
                        {key: effect[key] for key in request} == request, "fixture_effect_identity"
                    )
            inventory.append({"request": request, "evidence_refs": refs})
            requests.append(
                {**request, "outcome": outcome, "evidence_refs": refs, "retry": "forbidden"}
            )
        catalog = self.publish(
            "evidence/pending-inventory.json",
            self.doc(
                "pending-inventory",
                coverage="fixture_client_catalog_and_published_intents_only",
                rows=inventory,
            ),
        )
        return self.publish(
            "evidence/pending.json",
            self.doc(
                "pending-disposition",
                quarantine_ref=quarantine,
                inventory_ref=catalog,
                requests=requests,
            ),
        )

    def _policies(self):
        source_root = self.root / "source"
        files = [
            self.provider.reference(path)
            for path in sorted(source_root.rglob("*"))
            if path.is_file()
        ]
        index = self.publish("evidence/source-index.json", self.doc("source-index", files=files))
        guard = self.provider.reference(source_root / "scripts/j1_forward_cold_guard.py")
        native_ref = self.provider.reference(source_root / "scripts/j1_forward_cold_native.py")
        source = self.publish(
            "evidence/source-package.json",
            self.doc(
                "source-package",
                revision=self.binding["candidate_manager_sha"],
                source_index_ref=index,
                guard_ref=guard,
                native_ref=native_ref,
            ),
        )
        runtime = self.publish(
            "evidence/runtime-index.json", self.doc("runtime-index", files=[self.interpreter])
        )
        startup = self.publish(
            "evidence/startup-index.json", self.doc("startup-index", files=files)
        )
        policies = {}
        for unit in contract.ALLOWED:
            argv = [
                self.interpreter["path"],
                "-I",
                "-S",
                "-B",
                str(source_root / "worker.py"),
                str(self.root),
                "candidate" if self.candidates else "probe",
                unit,
            ]
            values = dict(ENVIRONMENT)
            if self.candidates:
                from scripts import j1_forward_cold_epoch

                binding_ref = self.runtime_bindings[j1_forward_cold_epoch.ROLES[unit]]
                values.update(
                    AUTOSTOP_COLD_RUNTIME_BINDING=binding_ref["path"],
                    AUTOSTOP_COLD_RUNTIME_BINDING_SHA256=binding_ref["sha256"],
                )
            environment = self.publish(
                "evidence/environment-" + unit + ".json",
                self.doc("environment", unit=unit, values=values),
            )
            unit_file = self.publish(
                "evidence/unit-source-" + unit,
                {"unit": unit, "guard_ref": guard, "binding": self.binding},
            )
            unit_ref = self.publish(
                "evidence/unit-" + unit + ".json",
                self.doc(
                    "unit",
                    unit=unit,
                    properties={
                        "unit": unit,
                        "binding_sha256": contract.digest(self.binding),
                        "guard_sha256": guard["sha256"],
                        "argv": argv,
                    },
                    files=[unit_file],
                ),
            )
            inputs = {
                "source_root": str(source_root),
                "source_index_ref": index,
                "interpreter_ref": self.interpreter,
                "runtime_index_ref": runtime,
                "startup_index_ref": startup,
                "environment_ref": environment,
                "unit_ref": unit_ref,
                "guard_ref": guard,
                "native_ref": native_ref,
                "argv": argv,
                "cwd": str(self.root),
                "credentials": {
                    "uid": os.geteuid(),
                    "gid": os.getegid(),
                    "groups": sorted(os.getgroups()),
                },
            }
            policy = contract.compile_policy(
                self.binding,
                "native_fixture",
                unit,
                inputs,
                source,
                read_reference=self.provider.read_reference,
            )
            policies[unit] = self.publish("evidence/policy-" + unit + ".json", policy)
        return source, policies

    def probe(self, unit):
        contract.need(self.package_ref is not None, "fixture_not_produced")
        native.CheckedDispatcher(
            self.package_ref, provider=self.provider, required_scope="native_fixture"
        ).inspect(unit)
        process = subprocess.run(
            [
                self.interpreter["path"],
                "-I",
                "-S",
                "-B",
                str(self.root / "source/worker.py"),
                str(self.root),
                "probe",
                unit,
            ],
            cwd=self.root,
            env=ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
        return process

    def close(self):
        cleanup_complete = True
        for process in reversed(self.children):
            try:
                if process.poll() is None:
                    descriptor = self.pidfds.get(process.pid)
                    if descriptor is None:
                        cleanup_complete = False
                        continue
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_complete = False
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        for descriptor in self.pidfds.values():
            os.close(descriptor)
        self.pidfds.clear()
        for client in self.clients:
            client.close()
        self.clients.clear()
        for name in ("dispatch_fd", "deployment_fd"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)
        # Uncertain cleanup retains evidence; never remove another topology.
        if cleanup_complete:
            info = self.root.lstat()
            contract.need(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == os.geteuid()
                and self.root.parent == Path(tempfile.gettempdir())
                and self.root.name.startswith("autostop-cold-native-"),
                "fixture_cleanup_scope",
            )
            shutil.rmtree(self.root)
        else:
            raise io.Refusal("cold_fixture_cleanup_incomplete")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
