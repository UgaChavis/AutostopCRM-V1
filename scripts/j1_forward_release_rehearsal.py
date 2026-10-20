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
import zlib
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from scripts import j1_forward_backup as backup
from scripts import j1_forward_cold_fixture as cold_fixture
from scripts import j1_forward_io as io
from scripts import j1_forward_proofs as signatures
from scripts import j1_forward_state as state
from scripts import j1_forward_variant as variant

SOURCE = Path(__file__).resolve().parents[1]
# A genuine locally retained source snapshot before the native launch-journal
# protocol existed. This is a synthetic historical attempt, not the current
# candidate's source or a claim about an installed production revision.
PREDECESSOR_SOURCE = "30d872e2b7c5659b53b260092773f81e7b8adb6f"
PREDECESSOR_TREE = "d99adcf1ba473d7a3671c0453f6c84e48134799f"
PREDECESSOR_CAPSULE_SHA256 = "363edefaba5f26074844adb8c774be85644b4a76abfd847f559d697110402be4"


class PrivateFilesystem:
    """Custodian for one captured private directory, not a global root waiver."""

    def __init__(self, root, *, engines=()):
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
        self.engines = tuple(dict.fromkeys((backup, *engines)))

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
        for engine in self.engines:
            self.stack.enter_context(patch.object(engine, "protected_parent", self.directory))
            self.stack.enter_context(
                patch.object(engine, "native_root", lambda: os.name == "posix")
            )
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
    names = (
        set(result.stdout.decode().splitlines())
        | {"scripts/" + Path(__file__).name}
        | variant.COLD_SUPPLEMENTAL
    )
    io.require(
        8 <= len(names) <= 128
        and all(
            (
                name.startswith("scripts/j1_forward_")
                and name.endswith(".py")
                and ".." not in Path(name).parts
            )
            or name in variant.COLD_SUPPLEMENTAL
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


def predecessor_source():
    """Load a pinned full historical capsule; never execute its source.

    The fixture contains all scripts/j1_forward_*.py entries of the exact
    PREDECESSOR_SOURCE Git tree. Its bytes travel with shallow/offline clones.
    """
    packed = (SOURCE / "tests/fixtures/j1-forward-predecessor-capsule.json.gz").read_bytes()
    io.require(
        len(packed) <= 256 * 1024 and io.sha(packed) == PREDECESSOR_CAPSULE_SHA256,
        "rehearsal_predecessor_capsule_digest",
    )
    decoder = zlib.decompressobj(31)
    raw = decoder.decompress(packed, 2 * 1024**2 + 1)
    io.require(
        len(raw) <= 2 * 1024**2
        and decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail,
        "rehearsal_predecessor_capsule_bound",
    )
    document = io.document(raw)
    io.require(
        type(document) is dict
        and set(document) == {"schema", "revision", "tree", "files"}
        and document["schema"] == "autostop.j1.private-predecessor-source-capsule.v1"
        and document["revision"] == PREDECESSOR_SOURCE
        and document["tree"] == PREDECESSOR_TREE,
        "rehearsal_predecessor_capsule_schema",
    )
    files = document["files"]
    io.require(
        type(files) is dict
        and len(files) == 20
        and "scripts/j1_forward_launch_native.py" not in files
        and all(
            type(name) is str
            and type(value) is str
            and name.startswith("scripts/j1_forward_")
            and name.endswith(".py")
            and len(Path(name).parts) == 2
            and ".." not in Path(name).parts
            for name, value in files.items()
        ),
        "rehearsal_predecessor_source_protocol",
    )
    return {"revision": PREDECESSOR_SOURCE, "tree": PREDECESSOR_TREE}, {
        name: value.encode("utf-8") for name, value in files.items()
    }


class TraceFixture(cold_fixture.Fixture):
    """Full failed Store attempt anchored to the same real six-process cohort."""

    def __init__(self, **kwargs):
        self.origin = None
        self.source = checkout_identity()
        kwargs.setdefault("candidate_crm_sha", self.source["revision"])
        super().__init__(**kwargs)
        try:
            self.control = self._mkdir("forward")
            self.base_sha256 = io.sha(b"private cold release base, not a production authority")
            self.custody = PrivateFilesystem(self.root)
            self.source_bytes = source_inventory()
            self.consumers = {name: io.sha(raw) for name, raw in self.source_bytes.items()}
            self.predecessor_source, self.predecessor_bytes = predecessor_source()
            self.predecessor_consumers = {
                name: io.sha(raw) for name, raw in self.predecessor_bytes.items()
            }
            from scripts import rehearsal_parent

            with self.custody:
                self.parent = rehearsal_parent.PrivateParent(self)
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

    def _plan(self, attempt, *, created=None, source=None, consumers=None):
        now = datetime.now(UTC)
        source = self.source if source is None else source
        consumers = self.consumers if consumers is None else consumers
        return {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": "private-native-fixture.invalid",
            "attempt_id": attempt,
            "sequence": 1,
            "predecessor_sha256": self.base_sha256,
            "base_sha256": self.base_sha256,
            "candidate": {
                "revision": source["revision"],
                "tree": source["tree"],
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": io.sha(io.canonical(consumers)),
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
            "source_consumers": self.predecessor_consumers,
            "deployment_lock_identity": self.binding["deployment_lock_identity"],
            "host": {},
            "previous_container": {"image": "synthetic-original", "running": False},
        }
        baseline_ref = self._write(directory / "baseline.json", baseline)
        plan = self._plan(
            attempt, source=self.predecessor_source, consumers=self.predecessor_consumers
        )
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
            for name, raw in self.predecessor_bytes.items()
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
                "source_consumers": self.predecessor_consumers,
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


def backup_settings(engine, roots, root):
    """Translate OS paths while preserving every cold profile component name."""
    aliases = dict(roots)
    components = {path: name for name, path in engine.ROOTS}
    return {
        "ROOTS": roots,
        "SELECTORS": tuple(aliases[components[path]] for path in engine.SELECTORS),
        "OPTIONAL_ROOTS": tuple((name, aliases[name]) for name, _path in engine.OPTIONAL_ROOTS),
        "J1_ALIAS": Path(root) / "business-fixture/research.sqlite3",
    }


def _restored_diagnostic_capture(request, spec, output):
    """The private custodian uses only bytes restored by its own archive pass."""
    from scripts import j1_forward_archive as archive
    from scripts import j1_forward_cold_backup as cold_backup
    from scripts import j1_forward_cold_worker as cold_worker
    from scripts import j1_forward_worker as worker

    manifest_path = Path(spec["package"]) / "manifest.json"
    raw = archive._package_read(manifest_path, worker._physical, archive.MAX_FILE_BYTES)
    io.require(io.sha(raw) == spec["manifest_sha256"], "rehearsal_control_manifest_changed")
    rows = io.document(raw)["artifacts"]
    entries = {row["logical_name"]: row for row in rows}
    io.require(len(entries) == len(rows), "rehearsal_control_inventory")
    prefix = str(PurePosixPath(spec["logical_root"]) / "attempts")
    # This maps the fixture's actual State root. All payload reads retain the
    # production worker's protected, size/hash-bound restored-entry decoder.
    plan = io.document(io.referenced(request["plan_ref"]))
    response = {"plan": plan, "plan_ref": request["plan_ref"]}
    with patch.object(worker, "ATTEMPTS", prefix):
        reader = cold_worker._restored_capture_reader(entries, output / "control", response)
        io.require(io.document(reader(request["plan_ref"])) == plan, "rehearsal_restored_plan")
        path = str(PurePosixPath(request["plan_ref"]["path"]).parent / "backup-evidence.json")
        entry = entries[
            "control/" + PurePosixPath(path).relative_to(spec["logical_root"]).as_posix()
        ]
        response.update(
            scope_profile_sha256=request["context"]["scope_profile_sha256"],
            evidence={
                "package": io.document(reader.read_source(plan["cold_authority_package_ref"])),
                "epoch": io.document(reader.read_source(plan["dispatch_epoch_ref"])),
                "backup_capture": io.document(reader({"path": path, "sha256": entry["sha256"]})),
            },
        )
        data = backup.document((Path(request["backup"]) / "manifest.json").read_bytes())
        return cold_backup.validate_restored_interval(data, response, reader)


def run_custodian_request(root, request_path):
    """A separate private process restores first, then signs measured results."""
    import sqlite3

    from scripts import j1_forward_archive as archive
    from scripts import j1_forward_cold_native as native

    io.require(sqlite3.sqlite_version_info >= (3, 51, 3), "rehearsal_restore_sqlite_runtime")

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
            "backup_profile",
            "sqlite_runtime_ref",
        }
        and request["schema"] == "autostop.j1.private-custodian-request.v1",
        "rehearsal_request_fields",
    )
    sqlite_readback = verify_private_sqlite(root, request["sqlite_runtime_ref"])
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
    verified = native.verify_package(
        request["package_ref"], provider=provider, required_scope="native_fixture"
    )
    engine = backup
    if request["backup_profile"] != backup.PROFILE:
        from scripts import j1_forward_cold_backup

        io.require(
            request["backup_profile"]
            in {j1_forward_cold_backup.PROFILE, j1_forward_cold_backup.PROFILE_V4},
            "rehearsal_backup_profile",
        )
        binding = verified["package"]["binding"]
        version = 4 if request["backup_profile"] == j1_forward_cold_backup.PROFILE_V4 else 3
        engine = j1_forward_cold_backup.engine(
            binding["candidate_manager_sha"],
            binding["generation"],
            binding["attempt_id"],
            version=version,
            package_ref=request["package_ref"] if version == 4 else None,
        )
        io.require(
            [name for name, _path in roots] == [name for name, _path in engine.ROOTS],
            "rehearsal_cold_backup_exact_alias_inventory",
        )
    settings = {"ROOTS": roots}
    if engine is not backup:
        settings = backup_settings(engine, roots, root)
    with PrivateFilesystem(root, engines=(engine,)), ExitStack() as aliases:
        for name, value in settings.items():
            aliases.enter_context(patch.object(engine, name, value))
        output = Path(request["output"])
        output.mkdir(mode=0o700)
        data_receipt = engine.verify_and_restore(
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
            if engine is not backup and engine.COLD_VERSION == 4:
                _restored_diagnostic_capture(request, spec, output)
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
            "sqlite_version": sqlite3.sqlite_version,
            "sqlite_runtime": sqlite_readback,
            "wall_seconds": time.perf_counter() - begin,
            "data_restore": data_receipt,
            "control_restore": control_receipt,
            "proof_ref": proof,
            "same_host_private_fixture": True,
        }
        io.write_new(root / "custodian-result.json", result)


def independent_restore(
    fixture, plan_ref, captured, context, roots, signer, *, control=None, proof_path=None
):
    """Fixed child command; no shell, inherited credentials or effect commands."""
    runtime_ref = private_sqlite_runtime(fixture)
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
        "proof": str(proof_path or fixture.root / "peer-offhost-proof.json"),
        "control": control,
        "backup_profile": io.document(io.referenced(captured["manifest"]))["profile"]["name"],
        "sqlite_runtime_ref": runtime_ref,
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
        env=private_sqlite_environment(fixture),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        print(
            "PRIVATE_CUSTODIAN_DIAGNOSTIC "
            + io.canonical(
                {
                    "returncode": result.returncode,
                    "stderr": result.stderr[-8192:]
                    .decode(errors="replace")
                    .replace(str(fixture.root), "$FIXTURE"),
                }
            )
            .decode()
            .strip(),
            flush=True,
        )
    io.require(result.returncode == 0, "rehearsal_independent_custodian_failed")
    receipt = io.document(io.read_protected(fixture.root / "custodian-result.json"))
    io.require(receipt["pid"] != os.getpid(), "rehearsal_restore_process_not_independent")
    return receipt


def private_sqlite_runtime(fixture):
    """Copy only the stand-selected SQLite library into this private fixture.

    The hosted builder supplies CRM_REHEARSAL_SQLITE_LIBRARY. The fixed local
    fallback is the already approved runtime; arbitrary inherited LD settings
    are never forwarded. This is a test runtime receipt, not native authority.
    """
    from scripts import j1_forward_cold_kernel as kernel

    selected = Path(
        os.environ.get(
            "CRM_REHEARSAL_SQLITE_LIBRARY", "/opt/autostop-sqlite/3.51.3/lib/libsqlite3.so.0"
        )
    )
    io.require(
        selected.is_absolute() and ".." not in selected.parts, "rehearsal_sqlite_library_path"
    )
    selected = selected.resolve(strict=True)
    before = kernel.file_version(selected.lstat())
    digest = kernel.file_digest(selected, limit=32 * 1024**2)
    descriptor = os.open(selected, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(32 * 1024**2 + 1)
        io.require(
            len(raw) <= 32 * 1024**2
            and io.sha(raw) == digest["sha256"]
            and kernel.file_version(os.fstat(descriptor))
            == before
            == kernel.file_version(selected.lstat()),
            "rehearsal_sqlite_library_changed",
        )
    finally:
        os.close(descriptor)
    directory = fixture._mkdir("custodian-runtime")
    target = directory / "libsqlite3.so.0"
    if target.exists():
        io.require(
            cold_fixture._safe_file(target, fixture.root, limit=32 * 1024**2) == raw,
            "rehearsal_sqlite_copy_changed",
        )
        library_ref = fixture.provider.reference(target)
    else:
        library_ref = fixture._copy(target, raw)
    value = {
        "schema": "autostop.j1.private-sqlite-runtime.v1",
        "scope": "native_fixture",
        "source_path": str(selected),
        "source_sha256": digest["sha256"],
        "library_ref": library_ref,
        "expected_version": "3.51.3",
    }
    receipt = directory / "receipt.json"
    if receipt.exists():
        io.require(
            io.document(cold_fixture._safe_file(receipt, fixture.root)) == value,
            "rehearsal_sqlite_receipt_changed",
        )
        return fixture.provider.reference(receipt)
    return fixture._write(receipt, value)


def private_sqlite_environment(fixture):
    return {
        **cold_fixture.ENVIRONMENT,
        "LD_LIBRARY_PATH": str(fixture.root / "custodian-runtime"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def verify_private_sqlite(root, reference):
    """Actual fresh child version and mapped inode of its single copied library."""
    import sqlite3

    root = Path(root)
    io.require(
        reference["path"] == str(root / "custodian-runtime/receipt.json"),
        "rehearsal_sqlite_receipt_path",
    )
    raw = cold_fixture._safe_file(reference["path"], root)
    io.require(io.sha(raw) == reference["sha256"], "rehearsal_sqlite_receipt_hash")
    document = io.document(raw)
    io.require(
        set(document)
        == {"schema", "scope", "source_path", "source_sha256", "library_ref", "expected_version"}
        and document["schema"] == "autostop.j1.private-sqlite-runtime.v1"
        and document["scope"] == "native_fixture"
        and document["expected_version"] == sqlite3.sqlite_version == "3.51.3",
        "rehearsal_sqlite_actual_version",
    )
    library = Path(document["library_ref"]["path"])
    io.require(
        library == root / "custodian-runtime/libsqlite3.so.0", "rehearsal_sqlite_copied_path"
    )
    loaded = cold_fixture._safe_file(library, root, limit=32 * 1024**2)
    io.require(
        io.sha(loaded) == document["source_sha256"] == document["library_ref"]["sha256"],
        "rehearsal_sqlite_actual_bytes",
    )
    identity = library.stat()
    rows = [line.split(None, 5) for line in Path("/proc/self/maps").read_text().splitlines()]
    matches = [row for row in rows if len(row) == 6 and row[5] == str(library)]
    io.require(
        matches
        and all(
            int(row[4]) == identity.st_ino
            and tuple(int(value, 16) for value in row[3].split(":"))
            == (os.major(identity.st_dev), os.minor(identity.st_dev))
            for row in matches
        ),
        "rehearsal_sqlite_actual_mapping",
    )
    return {
        "sqlite_version": sqlite3.sqlite_version,
        "library_ref": document["library_ref"],
        "mapping_identity": [identity.st_dev, identity.st_ino],
    }
