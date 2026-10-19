"""Private filesystem/crypto substrate for the concrete cold controller rehearsal.

Only native/HTTP/business boundaries are replaced. State, backup, archive,
restore, signature primitives and immutable evidence are the reviewed source
implementations. This module never points a provider at production locations.
"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_backup as backup
from scripts import j1_forward_cold_fixture as cold_fixture
from scripts import j1_forward_io as io
from scripts import j1_forward_proofs as signatures
from scripts import j1_forward_state as state

SOURCE = Path(__file__).resolve().parents[1]


class PrivateFilesystem:
    """Custodian for one captured private directory, not a global root waiver."""

    def __init__(self, root):
        self.root = Path(root)
        info = self.root.lstat()
        io.require(
            stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == os.geteuid(),
            "rehearsal_private_root",
        )
        self.identity = (info.st_dev, info.st_ino, info.st_uid)
        self.stack = ExitStack()
        self.depth = 0

    def directory(self, path, *, private=False):
        path = Path(path)
        current = self.root.lstat()
        io.require(
            (current.st_dev, current.st_ino, current.st_uid) == self.identity,
            "rehearsal_root_replaced",
        )
        io.require(
            path.is_absolute() and path.is_relative_to(self.root) and ".." not in path.parts,
            "rehearsal_outside_custody",
        )
        cursor = self.root
        for part in ((), *[(part,) for part in path.relative_to(self.root).parts]):
            if part:
                cursor /= part[0]
            info = cursor.lstat()
            io.require(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == self.identity[2]
                and not info.st_mode & 0o022,
                "rehearsal_directory_custody",
            )
        if private:
            io.require(stat.S_IMODE(path.stat().st_mode) == 0o700, "rehearsal_private_directory")
        return path

    def read(self, path, limit=128 * 1024**2):
        return cold_fixture._safe_file(path, self.root, limit=limit)

    def __call__(self, path, *, directory=False):
        if directory:
            return self.directory(path)
        return self.read(path)

    def __enter__(self):
        self.depth += 1
        if self.depth > 1:
            return self
        self.stack.enter_context(patch.object(io, "protected_directory", self.directory))
        self.stack.enter_context(patch.object(io, "read_protected", self.read))
        self.stack.enter_context(patch.object(io, "ROOT_UID", os.geteuid()))
        # This is the private OS custody boundary. Capture/restore themselves
        # are actual functions, including SQLite backup and content inspection.
        self.stack.enter_context(patch.object(backup, "protected_parent", self.directory))
        self.stack.enter_context(patch.object(backup, "native_root", lambda: os.name == "posix"))
        return self

    def __exit__(self, *args):
        self.depth -= 1
        return self.stack.__exit__(*args) if self.depth == 0 else False


def source_inventory():
    result = subprocess.run(
        ["git", "-C", str(SOURCE), "ls-files", "scripts/j1_forward_*.py"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    names = set(result.stdout.decode().splitlines()) | {"scripts/" + Path(__file__).name}
    io.require(
        8 <= len(names) <= 128
        and all(
            name.startswith("scripts/j1_forward_")
            and name.endswith(".py")
            and ".." not in Path(name).parts
            for name in names
        ),
        "rehearsal_source_inventory",
    )
    return {name: (SOURCE / name).read_bytes() for name in sorted(names)}


def checkout_identity():
    def git(argument):
        return (
            subprocess.run(
                ["git", "-C", str(SOURCE), "rev-parse", argument],
                check=True,
                capture_output=True,
                timeout=10,
            )
            .stdout.decode()
            .strip()
        )

    return {"revision": git("HEAD"), "tree": git("HEAD^{tree}")}


class TraceFixture(cold_fixture.Fixture):
    """Full failed Store attempt anchored to the same real six-process cohort."""

    def __init__(self, **kwargs):
        self.origin = None
        super().__init__(**kwargs)
        try:
            self.control = self._mkdir("forward")
            self.base_sha256 = io.sha(b"private cold release base, not a production authority")
            self.custody = PrivateFilesystem(self.root)
            self.source_bytes = source_inventory()
            self.consumers = {name: io.sha(raw) for name, raw in self.source_bytes.items()}
            self.source = checkout_identity()
        except BaseException:
            self.close()
            raise

    def _write(self, path, value):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return io.write_new(path, value)

    def _copy(self, path, raw):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        io.fsync_directory(path.parent)
        return io.reference(path)

    def _plan(self, attempt, *, created=None):
        now = datetime.now(UTC)
        return {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": "private-native-fixture.invalid",
            "attempt_id": attempt,
            "sequence": 1,
            "predecessor_sha256": self.base_sha256,
            "base_sha256": self.base_sha256,
            "candidate": {
                "revision": self.source["revision"],
                "tree": self.source["tree"],
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": io.sha(io.canonical(self.consumers)),
            },
            "created_at": (created or now - timedelta(seconds=1)).isoformat(),
            "deadline_utc": (now + timedelta(seconds=500)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }

    def _history(self, endpoints, pipes):
        # Reuse the real read-only SQL producer, then bind its same bytes to
        # actual durable Store paths rather than a simplified historical plan.
        old_anchor, _scope, held_ref, checkpoint_ref = super()._history(endpoints, pipes)
        old = io.document(self.provider.read_reference(old_anchor))
        attempt = old["origin_attempt_id"]
        directory = self.control / "attempts" / attempt
        retained = self.control / "origins" / attempt
        new_directory = self.control / "attempts" / self.binding["attempt_id"]
        for path in (directory, retained, new_directory):
            path.mkdir(mode=0o700, parents=True)
        old_scope = io.document(self.provider.read_reference(old["scope_ref"]))
        scope_ref = self._write(directory / "process-scope.json", old_scope)
        baseline = {
            "schema": "autostop.j1.crm-forward-baseline.v1",
            "attempt_id": attempt,
            "native": {
                "base_sha256": self.base_sha256,
                "desired_inbound": True,
                "scope": "native_fixture_historical_cohort",
            },
            "source_consumers": self.consumers,
            "deployment_lock_identity": self.binding["deployment_lock_identity"],
            "host": {},
            "previous_container": {"image": "synthetic-original", "running": False},
        }
        baseline_ref = self._write(directory / "baseline.json", baseline)
        plan = self._plan(attempt)
        plan["baseline_ref"] = baseline_ref
        plan_ref = self._write(directory / "plan.json", plan)
        self.store = state.Store(self.control / "state", self.base_sha256)
        pending_ref = self.store.begin(plan)
        self._write(retained / "pending.json", plan)
        closure_ref = self._write(
            directory / "closure-owned.json",
            {
                "attempt_id": attempt,
                "process_scope_ref": scope_ref,
                "baseline_sha256": io.sha(io.canonical(baseline["native"])),
                "consumer_sha256": plan["candidate"]["consumer_sha256"],
            },
        )
        failure_ref = self._write(
            directory / "failure.json",
            {"ok": False, "plan_ref": plan_ref, "production_restore_attempted": False},
        )
        files = {
            name: self._copy(retained / "consumers" / name, raw)
            for name, raw in self.source_bytes.items()
        }
        bundle_ref = self._write(
            retained / "bundle.json",
            {
                "schema": "autostop.j1.crm-forward-origin-bundle.v2",
                "origin_attempt_id": attempt,
                "origin_plan_ref": plan_ref,
                "origin_baseline_ref": baseline_ref,
                "source_revision": plan["candidate"]["revision"],
                "source_tree": plan["candidate"]["tree"],
                "source_consumers": self.consumers,
                "consumer_sha256": plan["candidate"]["consumer_sha256"],
                "files": files,
            },
        )
        hold_ref = self._copy(
            new_directory / "recovery-hold.json", self.provider.read_reference(held_ref)
        )
        checkpoint = io.document(self.provider.read_reference(checkpoint_ref))
        checkpoint["original_hold_ref"] = hold_ref
        checkpoint_ref = self._write(new_directory / "original-hold-checkpoint.json", checkpoint)
        anchor = self._write(
            new_directory / "predecessor-anchor.json",
            {
                "schema": "autostop.j1.cold-predecessor-anchor.v1",
                "scope": "native_fixture",
                "origin_attempt_id": attempt,
                "plan_ref": plan_ref,
                "baseline_ref": baseline_ref,
                "scope_ref": scope_ref,
                "closure_ref": closure_ref,
                "original_hold_ref": hold_ref,
            },
        )
        self.origin = {
            "nonce": "a" * 64,
            "origin_pending_ref": pending_ref,
            "origin_plan_ref": plan_ref,
            "origin_scope_ref": scope_ref,
            "origin_closure_ref": closure_ref,
            "origin_failure_ref": failure_ref,
            "origin_bundle_ref": bundle_ref,
            "hold_ref": hold_ref,
            "deployment_lock_identity": self.binding["deployment_lock_identity"],
        }
        return anchor, scope_ref, hold_ref, checkpoint_ref

    def produce(self):
        with self.custody:
            return super().produce()


class TemporaryCustodian:
    """Real disposable Ed25519 key, separate from the captured host graph."""

    def __init__(self, root, public_path):
        self.root, self.public = Path(root), Path(public_path)
        self.root.mkdir(mode=0o700, parents=True)
        self.private = self.root / "ed25519.pem"
        self._run(["genpkey", "-algorithm", "Ed25519", "-out", str(self.private)])
        self.private.chmod(0o600)
        self.public.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._run(["pkey", "-in", str(self.private), "-pubout", "-out", str(self.public)])
        self.public.chmod(0o600)

    @staticmethod
    def _run(arguments):
        result = subprocess.run(
            ["/usr/bin/openssl", *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "OPENSSL_CONF": os.devnull},
        )
        io.require(result.returncode == 0, "rehearsal_crypto_primitive_failed")
        return result

    def sign(self, kind, plan_ref, payload, output):
        io.read_protected(self.private, 4096)
        plan = io.document(io.referenced(plan_ref))
        public_ref = io.reference(self.public)
        body = {
            "schema": signatures.SCHEMA,
            "kind": kind,
            "attempt_id": plan["attempt_id"],
            "plan_sha256": plan_ref["sha256"],
            "observed_at": datetime.now(UTC).isoformat(),
            "key_sha256": public_ref["sha256"],
            "payload": payload,
        }
        directory = self.root / (kind + "-" + str(time.monotonic_ns()))
        directory.mkdir(mode=0o700)
        body_path, signature = directory / "body.json", directory / "signature.bin"
        body_path.write_bytes(io.canonical(body))
        self._run(
            [
                "pkeyutl",
                "-sign",
                "-inkey",
                str(self.private),
                "-rawin",
                "-in",
                str(body_path),
                "-out",
                str(signature),
            ]
        )
        raw = signature.read_bytes()
        io.require(len(raw) == 64, "rehearsal_crypto_signature_size")
        envelope = {**body, "signature_base64": base64.b64encode(raw).decode()}
        return io.write_new(Path(output), envelope)


def synthetic_data(fixture):
    import sqlite3

    root = fixture._mkdir("business-fixture")
    crm = root / "crm"
    crm.mkdir(mode=0o700)
    (crm / "state.json").write_text('{"scope":"artificial-data-only"}\n')
    (crm / "attachment.bin").write_bytes(b"private synthetic attachment\x00\x01")
    for name in ("crm/kanban.sqlite3", "manager.sqlite3", "research.sqlite3"):
        connection = sqlite3.connect(root / name)
        try:
            connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY,value TEXT)")
            connection.execute("INSERT INTO records VALUES(1,'synthetic')")
            connection.execute("CREATE VIRTUAL TABLE records_fts USING fts5(value)")
            connection.execute("INSERT INTO records_fts VALUES('synthetic')")
            connection.commit()
        finally:
            connection.close()
        (root / name).chmod(0o600)
    return (
        ("crm", str(crm)),
        ("manager", str(root / "manager.sqlite3")),
        ("scheduler", str(fixture.root / "registry.sqlite3")),
        ("j1", str(root / "research.sqlite3")),
        ("forward_state", str(fixture.control / "state")),
    )


def measure_private_bytes(root):
    return sum(
        path.lstat().st_size for path in Path(root).rglob("*") if stat.S_ISREG(path.lstat().st_mode)
    )


def run_custodian_request(root, request_path):
    """A separate private process restores first, then signs measured results."""
    from scripts import j1_forward_archive as archive
    from scripts import j1_forward_cold_native as native

    root, request_path = Path(root), Path(request_path)
    provider = cold_fixture.FixtureProvider(root)
    io.require(request_path == root / "custodian-request.json", "rehearsal_request_path")
    request = io.document(cold_fixture._safe_file(request_path, root))
    io.require(
        type(request) is dict
        and set(request)
        == {
            "schema",
            "package_ref",
            "plan_ref",
            "roots",
            "backup",
            "manifest_sha256",
            "context",
            "output",
            "public_key",
            "private_key",
            "proof",
            "control",
        }
        and request["schema"] == "autostop.j1.private-custodian-request.v1",
        "rehearsal_request_fields",
    )
    for key in ("backup", "output", "public_key", "private_key", "proof"):
        path = Path(request[key])
        io.require(
            path.is_absolute() and path.is_relative_to(root) and ".." not in path.parts,
            "rehearsal_request_outside_custody",
        )
    roots = tuple(tuple(row) for row in request["roots"])
    io.require(
        all(
            len(row) == 2 and Path(row[1]).is_relative_to(root) and ".." not in Path(row[1]).parts
            for row in roots
        ),
        "rehearsal_source_roots",
    )
    io.require(
        all(
            Path(request["private_key"]) != Path(path)
            and not Path(request["private_key"]).is_relative_to(path)
            for _name, path in roots
        ),
        "rehearsal_private_signer_not_a_backup_source",
    )
    begin = time.perf_counter()
    native.verify_package(
        request["package_ref"], provider=provider, required_scope="native_fixture"
    )
    with PrivateFilesystem(root), patch.object(backup, "ROOTS", roots):
        output = Path(request["output"])
        output.mkdir(mode=0o700)
        data_receipt = backup.verify_and_restore(
            Path(request["backup"]),
            request["manifest_sha256"],
            output / "data",
            request["context"],
            timeout=60,
        )
        control_receipt = None
        if request["control"] is not None:
            spec = request["control"]
            io.require(
                type(spec) is dict
                and set(spec)
                == {
                    "package",
                    "manifest_sha256",
                    "logical_root",
                    "backup_manifest_ref",
                    "stage",
                    "closed_inspection_ref",
                },
                "rehearsal_control_request",
            )
            control_receipt = archive.verify_and_restore(
                Path(spec["package"]),
                spec["manifest_sha256"],
                output / "control",
                plan_ref=request["plan_ref"],
                backup_manifest_ref=spec["backup_manifest_ref"],
                stage=spec["stage"],
                logical_root=spec["logical_root"],
                custody=PrivateFilesystem(root),
            )
            payload = {
                "plan_sha256": request["plan_ref"]["sha256"],
                "baseline_sha256": io.document(io.referenced(request["plan_ref"]))["baseline_ref"][
                    "sha256"
                ],
                "phase": "closed",
                "actual_restore": data_receipt["provided_artifacts_restored_exactly"],
                "manifest_sha256": request["manifest_sha256"],
                "profile_sha256": request["context"]["scope_profile_sha256"],
                "control_archive_manifest_sha256": spec["manifest_sha256"],
                "control_graph_restored": control_receipt["semantic_graph_verified"],
                "control_restore_receipt_sha256": io.sha(io.canonical(control_receipt)),
                "closed_inspection_sha256": spec["closed_inspection_ref"]["sha256"],
            }
        else:
            payload = {"scope": "private_data_restore_only", "data_restore": data_receipt}
        native.verify_package(
            request["package_ref"], provider=provider, required_scope="native_fixture"
        )
        signer = TemporaryCustodian.__new__(TemporaryCustodian)
        signer.root, signer.private, signer.public = (
            Path(request["private_key"]).parent,
            Path(request["private_key"]),
            Path(request["public_key"]),
        )
        proof = signer.sign("offhost_restore", request["plan_ref"], payload, request["proof"])
        result = {
            "schema": "autostop.j1.private-custodian-result.v1",
            "pid": os.getpid(),
            "wall_seconds": time.perf_counter() - begin,
            "data_restore": data_receipt,
            "control_restore": control_receipt,
            "proof_ref": proof,
            "same_host_private_fixture": True,
        }
        io.write_new(root / "custodian-result.json", result)


def independent_restore(fixture, plan_ref, captured, context, roots, signer, *, control=None):
    """Fixed child command; no shell, inherited credentials or effect commands."""
    request = {
        "schema": "autostop.j1.private-custodian-request.v1",
        "package_ref": fixture.package_ref,
        "plan_ref": plan_ref,
        "roots": list(roots),
        "backup": str(Path(captured["manifest"]["path"]).parent),
        "manifest_sha256": captured["manifest_sha256"],
        "context": context,
        "output": str(fixture.root / "peer-restore"),
        "public_key": str(signer.public),
        "private_key": str(signer.private),
        "proof": str(fixture.root / "peer-offhost-proof.json"),
        "control": control,
    }
    path = fixture.root / "custodian-request.json"
    fixture._write(path, request)
    command = "\n".join(
        (
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "from scripts.j1_forward_release_rehearsal import run_custodian_request",
            "run_custodian_request(sys.argv[2], sys.argv[3])",
        )
    )
    result = subprocess.run(
        [
            str(Path(os.sys.executable).resolve()),
            "-I",
            "-S",
            "-B",
            "-c",
            command,
            str(SOURCE),
            str(fixture.root),
            str(path),
        ],
        cwd=fixture.root,
        env=cold_fixture.ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=90,
        check=False,
    )
    io.require(result.returncode == 0, "rehearsal_independent_custodian_failed")
    receipt = io.document(io.read_protected(fixture.root / "custodian-result.json"))
    io.require(receipt["pid"] != os.getpid(), "rehearsal_restore_process_not_independent")
    return receipt
