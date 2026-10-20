"""Owned offline kernel2/pending2 rehearsal, never production authority.

The appserver, parent/child processes, socket peers, durable catalog bytes,
runtime capsule, source measurements, signatures and restores are real. Unit
and cache-mount observations are explicitly private process/permission mappings.
Only account/read is replaced with an artificial fixture assumption, after
the actual offline endpoint independently reports that it has no credentials.
No current account files, model turns or production services are accessed.
"""

from __future__ import annotations

import hashlib
import os
import re
import select
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_catalog as catalog
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal

MANAGER = "5bf9570cb2029deda70b80a775d6c534d0dca56d"
CAPSULE_SHA256 = "612a4da4beea96664d960b8783c6c14292aad7082680066d4d5e755248ede43c"
REPORTS = Path("/var/tmp/autostop-release-prep-20261019T183954Z/reports/manager-cold")
ARTIFACT = REPORTS / "installed-runtime-5bf9570c/cold-installed-runtime"
INSTALLED = REPORTS / "installed-runtime-5bf9570c-independent-restore"
BINARY = Path(
    "/root/.codex/packages/standalone/releases/0.163.0-alpha.11-x86_64-unknown-linux-musl/bin/codex"
)
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_FILES = {
    "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
}
MODEL_ROLES = ("work-telegram", "personal-telegram")


def _voice_leaf(path, size, digest, *, target=None, deadline=None):
    """Stream a single public fixture-data leaf; never execute a model."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    output = None
    try:
        before = os.fstat(descriptor)
        cold.need(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == size,
            "fixture_model_leaf",
        )
        if target is not None:
            output = os.fdopen(
                os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
            )
        value, count = hashlib.sha256(), 0
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            while block := source.read(1024**2):
                cold.need(deadline is None or time.monotonic() < deadline, "fixture_model_deadline")
                count += len(block)
                cold.need(count <= size, "fixture_model_grew")
                value.update(block)
                if output is not None:
                    output.write(block)
        cold.need(
            count == size
            and value.hexdigest() == digest
            and io.unchanged_read(before, before, os.fstat(descriptor), Path(path).lstat()),
            "fixture_model_changed_or_hash",
        )
        if output is not None:
            output.flush()
            os.fsync(output.fileno())
    finally:
        if output is not None:
            output.close()
        os.close(descriptor)


def stage_voice_models(fixture, source):
    """Two actual private role copies; current aliases intentionally capture each three times."""
    source = Path(source)
    cold.need(
        source.is_dir()
        and not source.is_symlink()
        and set(p.name for p in source.iterdir()) == set(MODEL_FILES),
        "fixture_model_inventory",
    )
    for name, (size, digest) in MODEL_FILES.items():
        _voice_leaf(source / name, size, digest)
    entries = []
    for role in MODEL_ROLES:
        state = fixture.root / "execution/roles" / role / "state"
        fixture.custody.directory(state)
        destination = state / "models/faster-whisper-small"
        destination.mkdir(mode=0o700, parents=True)
        fixture.custody.directory(destination)
        for name, (size, digest) in MODEL_FILES.items():
            target = destination / name
            _voice_leaf(source / name, size, digest, target=target)
            _voice_leaf(target, size, digest)
            entries.append({"role": role, "source": str(target), "bytes": size, "sha256": digest})
    return entries


def verify_restored_models(entries, manifest, restored, *, deadline):
    """Check every logical occurrence in an actual independent DATA restore."""
    total, files = 0, 0
    for entry in entries:
        rows = [row for row in manifest["artifacts"] if row["source"] == entry["source"]]
        cold.need(
            len(rows) == 3
            and {row["component"] for row in rows}
            == {"cold_config", "cold_state", "cold_controller_state"},
            "fixture_model_backup_coverage",
        )
        for row in rows:
            cold.need(
                row.get("sha256") == entry["sha256"]
                and row.get("bytes") == entry["bytes"]
                and type(row.get("payload")) is str
                and re.fullmatch(r"[0-9]{6}\.bin", row["payload"]),
                "fixture_model_backup_binding",
            )
            _voice_leaf(
                Path(restored) / row["payload"], entry["bytes"], entry["sha256"], deadline=deadline
            )
            total += entry["bytes"]
            files += 1
    return {
        "model_revision": MODEL_REVISION,
        "roles": len({entry["role"] for entry in entries}),
        "physical_model_bytes": sum(entry["bytes"] for entry in entries),
        "logical_model_payload_bytes": total,
        "logical_model_payload_files": files,
        "all_payload_hashes_verified": bool(entries),
        "model_loaded": False,
    }


SUPERVISOR = r"""
import json, os, pathlib, select, signal, subprocess, sys, time
root = pathlib.Path(sys.argv[1]); config=json.loads((root/'kernel-launch.json').read_bytes())
child=subprocess.Popen(['/usr/bin/unshare','--net','--',*config['argv']],
    cwd=config['cwd'],env=config['environment'],stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
fd=os.pidfd_open(child.pid)
def cleanup(*_):
    if not select.select([fd],[],[],0)[0]: signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
    child.wait(timeout=5); os.close(fd)
    raise SystemExit(0)
signal.signal(signal.SIGTERM,cleanup);signal.signal(signal.SIGINT,cleanup)
(root/'kernel-child.pid').write_text(str(child.pid)); print('READY',flush=True)
try:
    while child.poll() is None: time.sleep(.05)
finally: cleanup()
"""


def _mapped_refs(pid):
    rows = lifecycle._library_mappings((Path("/proc") / str(pid) / "maps").read_text())
    return [kernel.file_digest(path) for path in sorted({row[5] for row in rows})]


class TypedFixture(rehearsal.TraceFixture):
    """The v2 branch has fixed owned topology and bounded artifact inputs."""

    def __init__(self, *, artifact=None, installed=None, binary=None, **kwargs):
        cold.need(sys.dont_write_bytecode, "v2_requires_no_bytecode_writes")
        self.artifact = Path(artifact or os.environ.get("CRM_COLD_V2_ARTIFACT", ARTIFACT))
        self.installed = Path(installed or os.environ.get("CRM_COLD_V2_INSTALLED", INSTALLED))
        self.binary = Path(binary or os.environ.get("CRM_COLD_V2_CODEX", BINARY))
        self.old_crm = self.supervisor = None
        self.old_crm_fd = None
        kwargs.setdefault("candidate_manager_sha", MANAGER)
        kwargs.setdefault("candidates", True)
        kwargs.setdefault("ttl_seconds", 600)
        super().__init__(**kwargs)
        try:
            document = io.document((self.artifact / "runtime-index.json").read_bytes())
            cold.need(
                document["binding"]["candidate_manager_sha"] == MANAGER, "v2_runtime_revision"
            )
            cold.need(document["capsule_ref"]["sha256"] == CAPSULE_SHA256, "v2_capsule_pin")
            physical = str((self.installed / "python/bin/python3.12").resolve())
            cold.need(str(Path(sys.executable).resolve()) == physical, "v2_run_with_sealed_python")
            self.runtime = {
                **document,
                "scope": "native_fixture",
                "binding": self.binding,
                "runtime_roots": {name: str(self.installed / name) for name in ("python", "venv")},
                "capsule_ref": {
                    "path": str(self.artifact / "manager-runtime.tar.gz"),
                    "sha256": CAPSULE_SHA256,
                },
            }
            self.runtime_ref = self.publish("evidence/typed-runtime-index.json", self.runtime)
            self.appserver_ref = kernel.file_digest(self.binary)
            cold.need(self.appserver_ref["sha256"] == kernel.BINARY_SHA256, "v2_appserver_pin")
            self.startup_refs = _mapped_refs(os.getpid())
            refs = {
                row["path"]: row
                for row in [*self.startup_refs, self.appserver_ref, self.runtime["capsule_ref"]]
            }
            self.publish(
                "typed-fixture.json",
                {
                    "schema": "autostop.j1.private-typed-cold-fixture.v1",
                    "scope": "native_fixture",
                    "binding": self.binding,
                    "runtime_index_ref": self.runtime_ref,
                    "external_refs": [refs[path] for path in sorted(refs)],
                },
            )
            self.provider = topology.FixtureProvider(self.root)
            self._start_old_crm()
        except BaseException:
            self.close()
            raise

    def _plan(self, attempt, **kwargs):
        plan = super()._plan(attempt, **kwargs)
        now = datetime.now(UTC)
        if attempt != self.binding["attempt_id"]:
            plan["created_at"] = (now - timedelta(seconds=601)).isoformat()
            plan["deadline_utc"] = (now - timedelta(seconds=1)).isoformat()
        else:
            remaining = (self.binding["deadline_ns"] - time.monotonic_ns()) / 1e9
            plan["deadline_utc"] = (now + timedelta(seconds=min(600, remaining))).isoformat()
        return plan

    def _start_old_crm(self):
        self.old_crm_source = self.publish(
            "old-crm.py",
            b"import time\nprint('READY',flush=True)\nwhile True: time.sleep(.05)\n",
            raw=True,
        )
        self.old_crm = subprocess.Popen(
            [self.interpreter["path"], "-I", "-S", "-B", self.old_crm_source["path"]],
            cwd=self.root,
            env=topology.ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.old_crm_fd = os.pidfd_open(self.old_crm.pid)
        cold.need(
            select.select([self.old_crm.stdout], [], [], 5)[0]
            and self.old_crm.stdout.readline() == b"READY\n",
            "v2_old_crm_ready",
        )
        self.old_crm_actor = native.actor(self.old_crm.pid)

    def _pending(self, quarantine):
        cold.need(native.actor(self.old_crm.pid) == self.old_crm_actor, "v2_old_crm_incarnation")
        signal.pidfd_send_signal(self.old_crm_fd, signal.SIGTERM, None, 0)
        self.old_crm.wait(timeout=5)
        old_crm_ref = self.publish(
            "evidence/old-crm-retired.json",
            {
                "schema": "autostop.j1.private-old-crm-retired.v1",
                "scope": "native_fixture",
                "binding": self.binding,
                "actor": self.old_crm_actor,
                "source_ref": self.old_crm_source,
            },
        )
        profiles = {}
        for unit, kinds in sorted(catalog.REQUIRED_KINDS.items()):
            directory = self._mkdir("legacy-catalog/" + unit)
            profiles[unit] = []
            for kind in sorted(kinds):
                path = directory / (kind + (".json" if kind == "idempotency_json" else ".sqlite3"))
                if kind == "idempotency_json":
                    self.publish(
                        str(path.relative_to(self.root)),
                        {"legacy/text-id:" + unit: {"unknown_fixture_record": True}},
                    )
                else:
                    with sqlite3.connect(path) as db:
                        for table, columns in catalog.DECODERS[kind]:
                            db.execute(
                                "CREATE TABLE "
                                + table
                                + " ("
                                + ",".join(name + " TEXT" for name in columns)
                                + ")"
                            )
                            values = []
                            for name in columns:
                                values.append(
                                    "{}"
                                    if name.endswith("_json")
                                    else (
                                        "legacy/text-id:" + unit
                                        if name in catalog.IDENTITIES or name == "id"
                                        else "0"
                                    )
                                )
                            db.execute(
                                "INSERT INTO "
                                + table
                                + " VALUES("
                                + ",".join("?" for _ in columns)
                                + ")",
                                values,
                            )
                    path.chmod(0o600)
                binding = self.publish(
                    "evidence/path-" + unit + "-" + kind + ".json",
                    {
                        "schema": "autostop.j1.private-catalog-path.v1",
                        "scope": "native_fixture",
                        "binding": self.binding,
                        "unit": unit,
                        "kind": kind,
                        "source_ref": self.provider.reference(path),
                        "predecessor_ref": self._cold_predecessor,
                    },
                )
                profiles[unit].append(
                    {"kind": kind, "path": str(path), "path_binding_ref": binding}
                )
        policy = self.publish(
            "evidence/catalog-policy.json",
            {
                "schema": "autostop.j1.cold-legacy-catalog-policy.v1",
                "scope": "native_fixture",
                "binding": self.binding,
                "predecessor_ref": self._cold_predecessor,
                "profiles": profiles,
            },
        )
        measurement = catalog.measure(
            io.document(self.provider.read_reference(policy)),
            self.binding,
            provider=self.provider,
            verify_path_binding=self.provider._typed_catalog_path,
        )
        checkpoint = self.publish(
            "evidence/catalog-checkpoint.json",
            {
                "schema": "autostop.j1.cold-legacy-catalog-checkpoint.v1",
                "scope": "native_fixture",
                "binding": self.binding,
                "policy_ref": policy,
                "measurement": measurement,
                "old_crm_ingress_ref": old_crm_ref,
            },
        )
        requests = [{**row, "evidence_refs": [checkpoint]} for row in measurement["rows"]]
        inventory = self.publish(
            "evidence/pending-inventory.json",
            {
                **self.doc("pending-inventory"),
                "schema": "autostop.j1.cold-pending-inventory.v2",
                "coverage": "source_defined_durable_catalogs_with_retired_unenumerated_ram",
                "payload_semantics": "observed_record_fingerprint_unknown_only",
                "source_checkpoint_ref": checkpoint,
                "old_crm_ingress_ref": old_crm_ref,
                "rows": [
                    {
                        "request": {key: row[key] for key in ("op_id", "fingerprint")},
                        "evidence_refs": [checkpoint],
                    }
                    for row in requests
                ],
            },
        )
        return self.publish(
            "evidence/pending.json",
            {
                **self.doc("pending-disposition"),
                "schema": "autostop.j1.cold-pending-disposition.v2",
                "quarantine_ref": quarantine,
                "inventory_ref": inventory,
                "requests": requests,
            },
        )

    def _policies(self):
        source, policies = super()._policies()
        # Recompile every profile against the same exact full runtime. The
        # fixture applications remain private, source-pinned stand-ins.
        for unit, reference in policies.items():
            document = io.document(self.provider.read_reference(reference))
            document["runtime_index_ref"] = self.runtime_ref
            startup = io.document(self.provider.read_reference(document["startup_index_ref"]))
            by_path = {row["path"]: row for row in [*startup["files"], *self.startup_refs]}
            startup_ref = self.publish(
                "evidence/typed-startup-" + unit + ".json",
                {**startup, "files": [by_path[path] for path in sorted(by_path)]},
            )
            document["startup_index_ref"] = startup_ref
            policies[unit] = self.publish("evidence/typed-policy-" + unit + ".json", document)
        return source, policies

    def _produce_kernel(self, predecessor, closure, hold):
        source, policies = self._policies()
        relative = "execution/packages/" + self.binding["attempt_id"] + "/kernel"
        base = self._mkdir(relative)
        home = self._mkdir(relative + "/home")
        codex = self._mkdir(relative + "/home/.codex")
        self.publish(
            relative + "/home/.codex/config.toml",
            b'cli_auth_credentials_store="file"\n[analytics]\nenabled=false\n',
            raw=True,
        )
        endpoint = base / "app.sock"
        environment = {
            "HOME": str(home),
            "CODEX_HOME": str(codex),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        argv = [
            str(self.binary),
            "app-server",
            "--remote-control",
            "--listen",
            "unix://" + str(endpoint),
            "--managed-daemon",
            "-c",
            "analytics.enabled=false",
            "-c",
            'cli_auth_credentials_store="file"',
        ]
        self.publish(
            "kernel-launch.json",
            {"argv": argv, "cwd": str(self.root / "source"), "environment": environment},
        )
        supervisor_source = self.publish("kernel-supervisor.py", SUPERVISOR.encode(), raw=True)
        supervisor_argv = [
            self.interpreter["path"],
            "-I",
            "-S",
            "-B",
            supervisor_source["path"],
            str(self.root),
        ]
        self.supervisor = subprocess.Popen(
            supervisor_argv,
            cwd=self.root,
            env=topology.ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.children.append(self.supervisor)
        self.pidfds[self.supervisor.pid] = os.pidfd_open(self.supervisor.pid)
        cold.need(
            select.select([self.supervisor.stdout], [], [], 5)[0]
            and self.supervisor.stdout.readline() == b"READY\n",
            "v2_supervisor_ready",
        )
        child_pid = int((self.root / "kernel-child.pid").read_text())
        until = time.monotonic() + 15
        while not endpoint.is_symlink():
            cold.need(
                self.supervisor.poll() is None and time.monotonic() < until, "v2_appserver_ready"
            )
            time.sleep(0.02)
        appserver, supervisor = native.actor(child_pid), native.actor(self.supervisor.pid)
        unit = native.kernel_unit(self.binding["generation"])
        self.publish("typed-kernel-state.json", {"unit": unit, "actor": supervisor})
        # The RPC source is copied into the accepted private source tree too.
        rpc_source = self.provider.reference(
            self.root / "source/scripts/j1_forward_cold_kernel_rpc.py"
        )
        protocol_body = self.doc(
            "kernel-protocol-index",
            files=sorted([supervisor_source, rpc_source], key=lambda row: row["path"]),
        )
        protocol = self.publish("evidence/kernel-protocol-private.json", protocol_body)
        supervisor_env = self.publish(
            "evidence/kernel-environment.json",
            self.doc("environment", unit=unit, values=topology.ENVIRONMENT),
        )
        app_env = self.publish(
            "evidence/appserver-environment.json",
            self.doc("environment", unit=unit, values=environment),
        )
        kernel_startup = self.publish(
            "evidence/kernel-startup.json",
            self.doc(
                "startup-index",
                files=sorted(self.startup_refs, key=lambda row: row["path"]),
            ),
        )
        unit_ref = self.publish(
            "evidence/kernel-unit.json",
            self.doc(
                "unit", unit=unit, files=[supervisor_source], properties=self.provider.systemd(unit)
            ),
        )
        policy = {
            **self.doc("kernel-policy"),
            "schema": "autostop.j1.cold-kernel-policy.v2",
            "unit": unit,
            "source_ref": supervisor_source,
            "rpc_ref": rpc_source,
            "protocol_index_ref": protocol,
            "interpreter_ref": self.interpreter,
            "runtime_index_ref": self.runtime_ref,
            "startup_index_ref": kernel_startup,
            "environment_ref": supervisor_env,
            "unit_ref": unit_ref,
            "argv": supervisor_argv,
            "cwd": str(self.root),
            "credentials": {key: supervisor[key] for key in ("uid", "gid", "groups")},
            "appserver_ref": self.appserver_ref,
            "appserver_argv": argv,
            "appserver_environment_ref": app_env,
        }
        policy_ref = self.publish("evidence/kernel-policy.json", policy)
        context = self.publish(
            "evidence/kernel-context.json",
            self.doc(
                "kernel-context", closure_ref=closure, hold_ref=hold, predecessor_ref=predecessor
            ),
        )
        home_ref = self.publish(
            "evidence/kernel-home.json",
            self.doc(
                "kernel-home",
                home=str(home),
                codex_home=str(codex),
                cwd=str(self.root / "source"),
                prestart_files={
                    name: io.sha((codex / name).read_bytes()) for name in ("config.toml",)
                },
                authorization_ref=self.provider.reference(self.root / "typed-fixture.json"),
                created_ns=time.monotonic_ns(),
            ),
        )
        with self.custody:
            prepared = kernel.prepare_owned_thread(
                self.binding,
                policy,
                home_ref,
                provider=self.provider,
                directory=self._mkdir(relative + "/records"),
                socket_path=endpoint,
                appserver_actor=appserver,
                supervisor_actor=supervisor,
                context_ref=context,
            )
        for directory in (relative + "/cache-source", relative + "/cache-view"):
            self._mkdir(directory)
            self.publish(
                directory + "/cache.json",
                b'{"scope":"artificial_private_cache"}\n',
                raw=True,
                mode=0o400,
            )
        checkpoint = self.publish(
            "evidence/cache-checkpoint.json",
            self.doc(
                "cache-checkpoint",
                root=str(base / "cache-source"),
                files=[
                    {
                        "name": "cache.json",
                        "sha256": io.sha((base / "cache-source/cache.json").read_bytes()),
                        "bytes": (base / "cache-source/cache.json").stat().st_size,
                    }
                ],
            ),
        )
        view = self.publish(
            "evidence/cache-view.json",
            {
                "schema": "autostop.j1.cold-cache-view.v2",
                "binding": self.binding,
                "kind": "private_permission_checkpoint_directories",
                "path": str(base / "cache-view"),
                "mount_id": None,
                "checkpoint_ref": checkpoint,
                "source_identity": None,
                "target_identity": None,
            },
        )
        ready = self.publish(
            "kernel-ready.json",
            {
                "schema": "autostop.j1.cold-kernel-ready.v2",
                "binding": self.binding,
                "phase": "ready",
                "actor": appserver,
                "supervisor_actor": supervisor,
                **prepared,
            },
        )
        kernel_ref = self.publish(
            "evidence/kernel.json",
            {
                **self.doc("kernel"),
                "schema": "autostop.j1.cold-kernel.v2",
                "actor": appserver,
                "supervisor_actor": supervisor,
                "unit": unit,
                "policy_ref": policy_ref,
                "ready_ref": ready,
                "cache_view_ref": view,
                "preparation_ref": prepared["preparation_ref"],
                "empty_thread_ref": prepared["empty_thread_ref"],
            },
        )
        return source, policies, kernel_ref

    def close(self):
        if self.old_crm is not None and self.old_crm.poll() is None:
            signal.pidfd_send_signal(self.old_crm_fd, signal.SIGKILL, None, 0)
            self.old_crm.wait(timeout=5)
        if self.old_crm_fd is not None:
            os.close(self.old_crm_fd)
            self.old_crm_fd = None
        return super().close()


def verify_runtime_data(document):
    """An inert standalone probe useful before the timed controller trace."""
    with Path(document["capsule_ref"]["path"]).open("rb") as source:
        restored = capsule.verify_archive(document, source)
    capsule.verify_installed(document)
    return restored
