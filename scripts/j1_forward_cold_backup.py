"""Explicit cold profiles, diagnostic snapshots and immutable Store backups."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import sys
import time
import types
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath

from scripts import j1_forward_backup as original
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

PROFILE = "crm_manager_all_profiles_cold_v3"
PROFILE_V4 = "crm_manager_all_profiles_cold_v4"
PROFILE_V5 = "crm_manager_store_all_profiles_cold_v5"
STORE_ARTIFACT_SCHEMA = "autostop.j1.cold-store-data-artifacts.v1"
STORE_FILES = {
    "database": "database.dump",
    "uploads": "uploads.tar.gz",
    "photos": "photos.tar.gz",
    "env": "env.bin",
    "source": "source.tar.gz",
    "manifest": "manifest.json",
}
BASE_SHA256 = "e9691dd10f4a655ebe87b89fede6ba530f45361d0ef6602c35e22f2129f42157"
NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")
DIAGNOSTIC_CODEX_SHA256 = "ff3a6a3ed13735b8a8938c40141eb38b688efd6b02ee0fd5358187111fe9966a"
DIAGNOSTIC_MAX_BYTES = 64 * 1024**2
LOG_COLUMNS = (
    "id ts ts_nanos level target feedback_log_body module_path file line thread_id "
    "process_uuid estimated_bytes"
).split()
LOG_INDEXES = {
    "idx_logs_ts",
    "idx_logs_thread_id",
    "idx_logs_thread_id_ts",
    "idx_logs_process_uuid_threadless_ts",
    "sqlite_autoindex__sqlx_migrations_1",
}
LOG_MIGRATIONS = (
    (
        1,
        "logs",
        1,
        "009639EAFE599BE97D49D1D712E51671BF1BE1C6B8CB7BF1A4136DA88FB0E19308FF3CBCFBFBF589065E0EAFEF2CA164",
    ),
    (
        2,
        "logs feedback log body",
        1,
        "CF6C93AF074A90224564010F49BC9CF905F10BA4C3C4B8B25C12E014EE1522F0B561A1C3D8188F6D2F4BE764F141DAF8",
    ),
)


def _diagnostic_rule(package_ref, attempt):
    return {
        "schema": "autostop.j1.cold-diagnostic-sqlite-policy.v1",
        "package_ref": package_ref,
        "component": "cold_controller_state",
        "relative_path": f"packages/{attempt}/kernel/home/.codex/logs_2.sqlite",
        "codex_sha256": DIAGNOSTIC_CODEX_SHA256,
        "max_bytes": DIAGNOSTIC_MAX_BYTES,
        "snapshot": "readonly_read_transaction_sqlite_backup",
        "source_cut": "held_fd_identity_with_observed_mutable_content_versions",
        "schema_policy_sha256": cold.digest(
            {"columns": LOG_COLUMNS, "indexes": sorted(LOG_INDEXES), "migrations": LOG_MIGRATIONS}
        ),
        "kernel_home_schema": "autostop.j1.cold-kernel-home.v2",
        "precreated_maintenance_lock": {
            "name": ".sqlite-maintenance.lock",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "mode": 0o600,
            "upstream_revision": "5425d3e625daa58eea3ad1de27c087eb3b2ffbd3",
        },
        "business_quiet_claimed": False,
        "cross_database_atomic_claimed": False,
    }


def _store_rule(package_ref, attempt):
    return {
        "schema": "autostop.j1.cold-store-backup-policy.v1",
        "package_ref": package_ref,
        "component": "store_backup",
        "root": f"/var/lib/autostop-store-cold/attempts/{attempt}/backup",
        "selector_schema": STORE_ARTIFACT_SCHEMA,
        "files": dict(STORE_FILES),
        "optional": ["photos"],
        "snapshot": "exact_closed_preupdate_artifacts",
        "restore": "opaque_new_destination_then_independent_control_graph_binding",
        "production_restore_authorized": False,
    }


def store_selector(package_ref, read_reference):
    """Pure reconstruction from the Store source3/control graph, never live data."""
    from scripts import j1_forward_cold_store

    return j1_forward_cold_store.data_artifacts(package_ref, read_reference)


def _store_selector_shape(selector, rule):
    original.require(
        type(selector) is dict
        and set(selector) == {"schema", "store_prepared_ref", "backup_ref", "root", "files"}
        and selector["schema"] == STORE_ARTIFACT_SCHEMA
        and selector["root"] == rule["root"]
        and type(selector["files"]) is dict
        and set(selector["files"]) == set(STORE_FILES),
        "store_backup_selector",
    )
    for name in ("store_prepared_ref", "backup_ref"):
        cold.reference(selector[name])
    for name, leaf in STORE_FILES.items():
        value = selector["files"][name]
        if name == "photos" and value is None:
            continue
        original.require(
            type(value) is dict
            and set(value) == {"file", "sha256", "bytes"}
            and value["file"] == str(PurePosixPath(rule["root"]) / leaf)
            and type(value["sha256"]) is str
            and original.HEX.fullmatch(value["sha256"])
            and type(value["bytes"]) is int
            and 0 <= value["bytes"] <= original.MAX_FILE_BYTES,
            "store_backup_artifact_selector",
        )
    return selector


def _validate_store_manifest(manifest, profile):
    try:
        return _store_manifest(manifest, profile)
    except (original.BackupRefusal, io.Refusal):
        raise
    except (KeyError, TypeError, IndexError, ValueError) as error:
        raise original.BackupRefusal("store_backup_manifest_shape") from error


def _store_manifest(manifest, profile):
    original.require(
        type(manifest) is dict
        and type(profile) is dict
        and profile.get("name") == PROFILE_V5
        and original.canonical(manifest.get("profile")) == original.canonical(profile),
        "store_backup_profile",
    )
    rule = profile.get("store_backup")
    original.require(
        rule
        == _store_rule(
            profile["diagnostic_sqlite"]["package_ref"], manifest["context"]["attempt_id"]
        )
        and dict(profile["roots"]).get("store_backup") == rule["root"],
        "store_backup_scope",
    )
    selector = _store_selector_shape(manifest.get("store_backup"), rule)
    rows = [row for row in manifest["artifacts"] if row.get("component") == "store_backup"]
    expected = {value["file"]: value for value in selector["files"].values() if value is not None}
    original.require(
        len(rows) == len(expected) + 1
        and len({row.get("source") for row in rows}) == len(rows)
        and {row.get("source") for row in rows} == {rule["root"], *expected},
        "store_backup_inventory",
    )
    for row in rows:
        if row["source"] == rule["root"]:
            original.require(
                row["logical_name"] == "." and row["kind"] == "directory" and "payload" not in row,
                "store_backup_directory",
            )
        else:
            value = expected[row["source"]]
            original.require(
                row["logical_name"] == PurePosixPath(row["source"]).name
                and row["kind"] == "file"
                and row["sha256"] == value["sha256"]
                and row["bytes"] == value["bytes"],
                "store_backup_artifact_binding",
            )
    return selector


def _install_store_engine(module, package_ref, attempt):
    profile, capture, restore, publish = (
        module.profile_document,
        module.capture,
        module.verify_and_restore,
        module.publish,
    )
    rule = _store_rule(package_ref, attempt)
    module.PROFILE = PROFILE_V5
    module.ROOTS = (*module.ROOTS, ("store_backup", rule["root"]))
    module.profile_document = lambda: {
        **profile(),
        "policy": "recursive_quiet_data_with_owned_kernel_diagnostic_and_store_backup_v5",
        "store_backup": rule,
    }

    def publish_v5(path, value):
        if type(value) is dict and value.get("schema") == module.SCHEMA:
            module.require(hasattr(module, "_store_capture_selector"), "store_backup_capture_scope")
            value = {**value, "store_backup": module._store_capture_selector}
            _validate_store_manifest(value, module.profile_document())
        return publish(path, value)

    def capture_v5(output, context, observe_closure, *, timeout=1800, diagnostic_interval=None):
        module.require(0 < timeout <= 300, "cold_capture_budget")
        module.require(type(diagnostic_interval) is CaptureInterval, "store_native_interval")
        interval = diagnostic_interval
        module.require(
            interval.status == "active"
            and interval.controller.package_ref == package_ref
            and observe_closure == interval.observe
            and context["attempt_id"] == attempt,
            "store_capture_binding",
        )
        module.require(not hasattr(module, "_store_capture_selector"), "store_capture_reentered")
        deadline = time.monotonic() + timeout
        interval.gate()
        selector = _store_selector_shape(
            store_selector(package_ref, interval.provider.read_reference), rule
        )
        module.budget(deadline)
        module._store_capture_selector = selector
        try:
            result = capture(
                output,
                context,
                observe_closure,
                timeout=deadline - time.monotonic(),
                diagnostic_interval=interval,
            )
            module.require(
                store_selector(package_ref, interval.provider.read_reference) == selector,
                "store_capture_source_changed",
            )
            interval.gate()
            module.budget(deadline)
            return result
        finally:
            del module._store_capture_selector

    def restore_v5(backupdir, expectedmanifestSHA, outputnew, context, *, timeout=3600):
        module.require(0 < timeout <= 3600, "new_disposable_destination_required")
        deadline = time.monotonic() + timeout
        path = backupdir / "manifest.json"
        module.require(
            module.physical(path).st_size <= module.MAX_JSON_BYTES
            and module.digest(path, deadline) == expectedmanifestSHA,
            "manifest_hash_mismatch",
        )
        _validate_store_manifest(module.document(path.read_bytes()), module.profile_document())
        module.budget(deadline)
        return restore(
            backupdir, expectedmanifestSHA, outputnew, context, timeout=deadline - time.monotonic()
        )

    module.publish, module.capture, module.verify_and_restore = publish_v5, capture_v5, restore_v5


def validate_restored_store(manifest, response, read_reference):
    selector = _validate_store_manifest(manifest, manifest["profile"])
    expected = store_selector(
        response["plan"]["cold_authority_package_ref"],
        getattr(read_reference, "read_source", read_reference),
    )
    original.require(
        expected == selector
        and io.sha(original.canonical(manifest["profile"])) == response["scope_profile_sha256"],
        "store_restored_source_binding",
    )
    return selector


def diagnostic_selector(package_ref, read_reference):
    """Derive the single permitted source from its immutable kernel2 graph.

    Native capture additionally requires the actual CaptureInterval. During
    restore these bytes carry no execution authority; the complete control
    graph must already have passed the independent semantic restore.
    """
    from scripts import j1_forward_cold_kernel as kernel_contract

    reader = cold.Reader(read_reference)
    package = reader.doc(package_ref)
    cold.fields(package, cold.PACKAGE_FIELDS, "diagnostic_package_fields")
    binding, scope = package["binding"], package["scope"]
    cold.need(
        package["schema"] == cold.SCHEMA
        and package["contract"] == cold.CONTRACT
        and scope in cold.SCOPES,
        "diagnostic_package",
    )
    kernel = reader.bound(
        package["kernel_ref"],
        "kernel",
        binding,
        scope,
        {
            "actor",
            "unit",
            "policy_ref",
            "ready_ref",
            "cache_view_ref",
            "supervisor_actor",
            "preparation_ref",
            "empty_thread_ref",
        },
        version=2,
    )
    policy = reader.bound(
        kernel["policy_ref"],
        "kernel-policy",
        binding,
        scope,
        set(
            "unit source_ref interpreter_ref runtime_index_ref startup_index_ref environment_ref unit_ref argv cwd credentials appserver_ref appserver_argv appserver_environment_ref protocol_index_ref rpc_ref".split()
        ),
        version=2,
    )
    preparation = reader.bound(
        kernel["preparation_ref"],
        "kernel-preparation",
        binding,
        scope,
        kernel_contract.PREPARATION_FIELDS - {"schema", "scope", "binding"},
    )
    home = reader.bound(
        preparation["home_ref"],
        "kernel-home",
        binding,
        scope,
        kernel_contract.HOME_V2_FIELDS - {"schema", "scope", "binding"},
        version=2,
    )
    maintenance = kernel_contract.maintenance_lock_shape(home)
    cold.need(reader.raw(maintenance["reference"]) == b"", "diagnostic_maintenance_not_empty")
    environment = reader.bound(
        policy["appserver_environment_ref"], "environment", binding, scope, {"unit", "values"}
    )
    native.actor_shape(kernel["actor"])
    cold.need(
        kernel["actor"] == preparation["actor"]
        and kernel["actor"]["exe_ref"] == policy["appserver_ref"]
        and policy["appserver_ref"]["sha256"]
        == DIAGNOSTIC_CODEX_SHA256
        == kernel_contract.BINARY_SHA256
        and preparation["source_ref"] == policy["source_ref"]
        and preparation["rpc_ref"] == policy["rpc_ref"]
        and preparation["protocol_index_ref"] == policy["protocol_index_ref"]
        and environment["unit"] == policy["unit"] == kernel["unit"]
        and environment["values"]["HOME"] == home["home"]
        and environment["values"]["CODEX_HOME"] == home["codex_home"]
        and home["codex_home"] == str(PurePosixPath(home["home"]) / ".codex")
        and maintenance["source_version"][6:8] == [kernel["actor"]["uid"], kernel["actor"]["gid"]],
        "diagnostic_kernel_source_binding",
    )
    reader.recheck()
    return {
        "schema": "autostop.j1.cold-diagnostic-sqlite-selector.v1",
        "scope": scope,
        "binding": binding,
        "package_ref": package_ref,
        "kernel_ref": package["kernel_ref"],
        "policy_ref": kernel["policy_ref"],
        "preparation_ref": kernel["preparation_ref"],
        "home_ref": preparation["home_ref"],
        "actor": kernel["actor"],
        "database_path": str(PurePosixPath(home["codex_home"]) / "logs_2.sqlite"),
    }


def _install_diagnostic_engine(module, package_ref, attempt):
    profile, capture, restore = module.profile_document, module.capture, module.verify_and_restore
    rule = _diagnostic_rule(package_ref, attempt)
    module.PROFILE = PROFILE_V4
    module.DIAGNOSTIC_RULE = rule
    module.profile_document = lambda: {
        **profile(),
        "policy": "recursive_quiet_data_with_owned_kernel_diagnostic_snapshot_v4",
        "diagnostic_sqlite": rule,
    }

    def capture_v4(output, context, observe_closure, *, timeout=1800, diagnostic_interval=None):
        module.require(0 < timeout <= 300, "cold_capture_budget")
        module.require(type(diagnostic_interval) is CaptureInterval, "diagnostic_native_interval")
        interval = diagnostic_interval
        module.require(
            interval.status == "active"
            and interval.value is not None
            and interval.entry_ref is not None
            and observe_closure == interval.observe
            and interval.kernel.descriptors,
            "diagnostic_active_interval",
        )
        selector = diagnostic_selector(package_ref, interval.provider.read_reference)
        module.require(
            interval.controller.package_ref == package_ref
            and selector["binding"] == interval.value["package"]["binding"]
            and context["attempt_id"] == attempt,
            "diagnostic_capture_binding",
        )
        with _DiagnosticSnapshot(module, selector, gate=interval.gate):
            return capture(output, context, observe_closure, timeout=timeout)

    def restore_v4(backupdir, expectedmanifestSHA, outputnew, context, *, timeout=3600):
        module.require(0 < timeout <= 3600, "new_disposable_destination_required")
        deadline = time.monotonic() + timeout
        path = backupdir / "manifest.json"
        module.require(
            type(expectedmanifestSHA) is str
            and module.HEX.fullmatch(expectedmanifestSHA)
            and module.physical(path).st_size <= module.MAX_JSON_BYTES
            and module.digest(path, deadline) == expectedmanifestSHA,
            "manifest_hash_mismatch",
        )
        manifest = module.document(path.read_bytes())
        diagnostic = _validate_diagnostic_manifest(manifest, module.profile_document())
        module.budget(deadline)
        result = restore(
            backupdir, expectedmanifestSHA, outputnew, context, timeout=deadline - time.monotonic()
        )
        row = next(
            row
            for row in manifest["artifacts"]
            if row["source"] == diagnostic["selector"]["database_path"]
        )
        for root in (backupdir, outputnew):
            with closing(
                sqlite3.connect((root / row["payload"]).as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
                _logs_schema(connection, module.require)
                module.budget(deadline)
        return result

    module.capture, module.verify_and_restore = capture_v4, restore_v4


def _diagnostic_identity(version):
    return [version[index] for index in (0, 1, 5, 6, 7)]


def _diagnostic_mask(version):
    return [*version[:2], 0, 0, 0, *version[5:]]


def _logs_schema(connection, require):
    objects = connection.execute(
        "SELECT type,name FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    require(
        set(objects)
        == {("index", name) for name in LOG_INDEXES}
        | {("table", name) for name in ("_sqlx_migrations", "logs", "sqlite_sequence")},
        "diagnostic_sqlite_objects",
    )
    columns = connection.execute("PRAGMA table_info(logs)").fetchall()
    expected_types = (
        "INTEGER INTEGER INTEGER TEXT TEXT TEXT TEXT TEXT INTEGER TEXT TEXT INTEGER".split()
    )
    require(
        [row[1] for row in columns] == LOG_COLUMNS
        and [row[2] for row in columns] == expected_types
        and [row[5] for row in columns] == [1, *([0] * 11)]
        and [row[3] for row in columns] == [0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1]
        and [row[4] for row in columns] == [*([None] * 11), "0"]
        and connection.execute(
            "SELECT version,description,success,hex(checksum) FROM _sqlx_migrations ORDER BY version"
        ).fetchall()
        == list(LOG_MIGRATIONS),
        "diagnostic_sqlite_schema",
    )


class _DiagnosticSnapshot:
    """A capture-local adapter for one source-bound live diagnostic DB only."""

    def __init__(self, module, selector, *, gate):
        self.module, self.selector, self.gate = module, selector, gate
        self.original = {
            name: getattr(module, name) for name in ("version", "snapshot_sqlite", "publish")
        }
        self.database = Path(selector["database_path"])
        self.files, self.inodes, self.snapshot = {}, {}, None

    def _check(self):
        self.gate()
        cold._binding(self.selector["binding"], now_ns=time.monotonic_ns())
        self.module.require(
            native.actor(self.selector["actor"]["pid"]) == self.selector["actor"],
            "diagnostic_kernel_changed",
        )
        result = {}
        for path, (descriptor, expected) in self.files.items():
            try:
                actual, linked = os.fstat(descriptor), Path(path).lstat()
            except OSError as error:
                raise self.module.BackupRefusal("diagnostic_source_identity_changed") from error
            versions = [self.original["version"](value) for value in (actual, linked)]
            self.module.require(
                all(
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and info.st_size <= DIAGNOSTIC_MAX_BYTES
                    for info in (actual, linked)
                )
                and all(
                    _diagnostic_identity(value) == _diagnostic_identity(expected)
                    for value in versions
                ),
                "diagnostic_source_identity_changed",
            )
            result[path] = versions[1]
        self.gate()
        cold._binding(self.selector["binding"], now_ns=time.monotonic_ns())
        return result

    def __enter__(self):
        module, rule = self.module, self.module.DIAGNOSTIC_RULE
        expected = Path(dict(module.ROOTS)[rule["component"]]) / rule["relative_path"]
        module.require(
            not hasattr(module, "_diagnostic_capture")
            and self.database == expected
            and self.selector["package_ref"] == rule["package_ref"]
            and self.selector["actor"]["exe_ref"]["sha256"] == DIAGNOSTIC_CODEX_SHA256,
            "diagnostic_exact_source_required",
        )
        io.protected_directory(self.database.parent)
        try:
            for suffix in ("", "-wal", "-shm"):
                path = str(self.database) + suffix
                try:
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                except OSError as error:
                    raise module.BackupRefusal("diagnostic_source_files_required") from error
                info = os.fstat(descriptor)
                self.files[path] = (descriptor, self.original["version"](info))
                module.require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and not info.st_mode & 0o022
                    and info.st_uid == self.selector["actor"]["uid"]
                    and info.st_gid == self.selector["actor"]["gid"],
                    "diagnostic_source_protection",
                )
                self.inodes[(info.st_dev, info.st_ino)] = self.files[path][1]
            module.require(len(self.inodes) == 3, "diagnostic_source_alias")
            self.initial = self._check()
            module._diagnostic_capture = self
            module.version, module.snapshot_sqlite, module.publish = (
                self.version,
                self.copy,
                self.publish,
            )
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def version(self, info):
        version = self.original["version"](info)
        expected = self.inodes.get((info.st_dev, info.st_ino))
        if expected is not None:
            self.module.require(
                info.st_nlink == 1
                and info.st_size <= DIAGNOSTIC_MAX_BYTES
                and _diagnostic_identity(version) == _diagnostic_identity(expected),
                "diagnostic_source_identity_changed",
            )
            return _diagnostic_mask(version)
        return version

    def copy(self, source, target, deadline, remaining):
        if source != self.database:
            return self.original["snapshot_sqlite"](source, target, deadline, remaining)
        module = self.module
        module.require(self.snapshot is None, "diagnostic_duplicate_snapshot")
        before, started = self._check(), time.monotonic_ns()
        module.require(module.sqlite3.sqlite_version_info >= (3, 51, 3), "sqlite_runtime_too_old")
        with module.new_file(target):
            pass
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0)) as src:
            src.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            src.execute("PRAGMA query_only=ON")
            src.execute("BEGIN")
            _logs_schema(src, module.require)
            module.require(
                src.execute("PRAGMA page_size").fetchone()[0]
                * src.execute("PRAGMA page_count").fetchone()[0]
                <= min(DIAGNOSTIC_MAX_BYTES, remaining),
                "sqlite_size_limit",
            )
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst, pages=256, progress=lambda *_: module.budget(deadline), sleep=0.01)
                module.require(
                    dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete",
                    "standalone_snapshot_required",
                )
            src.rollback()
        with target.open("rb+") as stream:
            os.fsync(stream.fileno())
        after, completed = self._check(), time.monotonic_ns()
        result = {
            "bytes": target.stat().st_size,
            "sha256": module.digest(target, deadline),
            "inspection": module.inspect_sqlite(target, deadline),
        }
        module.require(result["bytes"] <= min(DIAGNOSTIC_MAX_BYTES, remaining), "sqlite_size_limit")
        self.snapshot = {
            "started_monotonic_ns": started,
            "completed_monotonic_ns": completed,
            "before_transaction": before,
            "after_transaction": after,
            "payload_sha256": result["sha256"],
            "schema_sha256": result["inspection"]["schema_sha256"],
        }
        return result

    def publish(self, path, document):
        if path.name == "manifest.json":
            self.module.require(self.snapshot is not None, "diagnostic_snapshot_missing")
            document = {
                **document,
                "diagnostic_snapshot": {
                    "schema": "autostop.j1.cold-diagnostic-sqlite-snapshot.v1",
                    "selector": self.selector,
                    "initial": self.initial,
                    "final": self._check(),
                    **self.snapshot,
                    "source_readonly": True,
                    "business_quiet_claimed": False,
                    "cross_database_atomic_claimed": False,
                },
            }
            _validate_diagnostic_manifest(document, self.module.profile_document())
        return self.original["publish"](path, document)

    def __exit__(self, *_):
        for name, value in self.original.items():
            setattr(self.module, name, value)
        if getattr(self.module, "_diagnostic_capture", None) is self:
            del self.module._diagnostic_capture
        for descriptor, _version in self.files.values():
            os.close(descriptor)
        self.files.clear()


def _validate_diagnostic_manifest(manifest, profile):
    """Check the v4 wire contract without reading a live source or granting authority."""
    try:
        return _diagnostic_manifest(manifest, profile)
    except (original.BackupRefusal, io.Refusal):
        raise
    except (KeyError, TypeError, IndexError, ValueError) as error:
        raise original.BackupRefusal("diagnostic_snapshot_shape") from error


def _diagnostic_manifest(manifest, profile):
    original.require(
        type(manifest) is dict
        and type(profile) is dict
        and original.canonical(manifest.get("profile")) == original.canonical(profile)
        and profile.get("name") in {PROFILE_V4, PROFILE_V5},
        "diagnostic_profile",
    )
    value = manifest.get("diagnostic_snapshot")
    fields = set(
        "schema selector initial final started_monotonic_ns completed_monotonic_ns before_transaction after_transaction payload_sha256 schema_sha256 source_readonly business_quiet_claimed cross_database_atomic_claimed".split()
    )
    original.require(type(value) is dict and set(value) == fields, "diagnostic_snapshot_fields")
    selector, rule = value["selector"], profile["diagnostic_sqlite"]
    selector_fields = set(
        "schema scope binding package_ref kernel_ref policy_ref preparation_ref home_ref actor database_path".split()
    )
    original.require(
        type(selector) is dict and set(selector) == selector_fields, "diagnostic_selector_fields"
    )
    binding = selector["binding"]
    original.require(type(binding) is dict and "attempt_id" in binding, "diagnostic_binding")
    native.actor_shape(selector["actor"])
    expected_path = str(
        PurePosixPath(dict(profile["roots"])[rule["component"]]) / rule["relative_path"]
    )
    original.require(
        value["schema"] == "autostop.j1.cold-diagnostic-sqlite-snapshot.v1"
        and selector["schema"] == "autostop.j1.cold-diagnostic-sqlite-selector.v1"
        and selector["scope"] in cold.SCOPES
        and selector["package_ref"] == rule["package_ref"]
        and rule == _diagnostic_rule(selector["package_ref"], binding["attempt_id"])
        and selector["database_path"] == expected_path
        and selector["actor"]["exe_ref"]["sha256"] == DIAGNOSTIC_CODEX_SHA256
        and value["source_readonly"] is True
        and value["business_quiet_claimed"] is value["cross_database_atomic_claimed"] is False,
        "diagnostic_snapshot_binding",
    )
    for key in ("package_ref", "kernel_ref", "policy_ref", "preparation_ref", "home_ref"):
        cold.reference(selector[key])
    start, end = value["started_monotonic_ns"], value["completed_monotonic_ns"]
    original.require(
        type(start) is int and type(end) is int and start <= end, "diagnostic_snapshot_interval"
    )
    cold._binding(binding, now_ns=start)
    cold._binding(binding, now_ns=end)
    family = {expected_path + suffix for suffix in ("", "-wal", "-shm")}
    for key in ("initial", "before_transaction", "after_transaction", "final"):
        versions = value[key]
        original.require(
            type(versions) is dict and set(versions) == family, "diagnostic_source_inventory"
        )
        for path, version in versions.items():
            original.require(
                type(version) is list
                and len(version) == 8
                and all(type(number) is int and number >= 0 for number in version)
                and 0 < version[1]
                and version[2] <= DIAGNOSTIC_MAX_BYTES
                and version[5:7] == [selector["actor"]["uid"], selector["actor"]["gid"]]
                and not version[7] & 0o022
                and _diagnostic_identity(version) == _diagnostic_identity(value["initial"][path]),
                "diagnostic_source_identity",
            )
    artifacts = manifest.get("artifacts")
    original.require(
        type(artifacts) is list
        and all(type(row) is dict and type(row.get("source")) is str for row in artifacts),
        "diagnostic_artifacts",
    )
    rows = [row for row in artifacts if row["source"] in family]
    original.require(
        len(rows) == 3 and {row["source"] for row in rows} == family,
        "diagnostic_artifact_inventory",
    )
    for row in rows:
        source = row["source"]
        original.require(
            row["component"] == rule["component"]
            and row["source_version"] == _diagnostic_mask(value["initial"][source])
            and row["kind"] == ("sqlite" if source == expected_path else "runtime"),
            "diagnostic_artifact_binding",
        )
        if source == expected_path:
            inspection = row["inspection"]
            original.require(
                row["sha256"] == value["payload_sha256"]
                and inspection["schema_sha256"] == value["schema_sha256"]
                and inspection["integrity"] == inspection["foreign_keys"] == "ok"
                and set(inspection["table_rows"]) == {"logs", "_sqlx_migrations", "sqlite_sequence"}
                and inspection["table_rows"]["_sqlx_migrations"] == 2,
                "diagnostic_snapshot_inspection",
            )
    return value


def validate_restored_diagnostic(manifest, response, read_reference):
    """Bind v4 data to the independently restored, source-validated kernel graph."""
    if manifest["profile"]["name"] not in {PROFILE_V4, PROFILE_V5}:
        original.require(manifest["profile"]["name"] == PROFILE, "cold_backup_profile")
        return None
    value = _validate_diagnostic_manifest(manifest, manifest["profile"])
    selector = diagnostic_selector(
        response["plan"]["cold_authority_package_ref"],
        getattr(read_reference, "read_source", read_reference),
    )
    original.require(
        value["selector"] == selector
        and selector["binding"] == response["evidence"]["package"]["binding"]
        and selector["binding"]["attempt_id"] == response["plan"]["attempt_id"]
        and hashlib.sha256(original.canonical(manifest["profile"])).hexdigest()
        == response["scope_profile_sha256"],
        "diagnostic_restored_source_binding",
    )
    return value


def engine(revision, generation, attempt, *, version=3, package_ref=None):
    original.require(
        type(revision) is str and cold.SHA1.fullmatch(revision), "cold_backup_revision"
    )
    original.require(cold._uuid(generation) and cold._uuid(attempt), "cold_backup_epoch")
    original.require(type(version) is int and version in {3, 4, 5}, "cold_backup_version")
    if version in {4, 5}:
        cold.reference(package_ref)
    key = f"v{version}_" + revision + "_" + generation + "_" + attempt
    if package_ref is not None:
        key += "_" + package_ref["sha256"]
    name = "_autostop_cold_backup_" + key
    if name in sys.modules:
        return sys.modules[name]
    path = Path(original.__file__)
    raw = path.read_bytes()
    original.require(hashlib.sha256(raw).hexdigest() == BASE_SHA256, "cold_backup_base_changed")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
        module.PROFILE = PROFILE
        roots = (
            ("cold_source", f"/opt/autostop-manager-cold-releases/{revision}/source"),
            ("cold_config", f"/etc/autostop-manager-cold/{generation}"),
            ("cold_state", f"/var/lib/autostop-manager-cold/{generation}"),
            ("cold_controller_code", "/usr/local/libexec/autostop-crm-forward-cold/v1"),
            ("cold_controller_state", "/var/lib/autostop-crm-forward/cold/v1"),
        )
        units = tuple(
            (
                "unit_cold_" + str(index),
                "/etc/systemd/system/autostop-crm-forward-"
                + str(uuid.uuid5(NAMESPACE, attempt + generation + unit))
                + ".service",
            )
            for index, unit in enumerate(cold.ALLOWED)
        )
        module.ROOTS = (*original.ROOTS, *roots, *units)
        module.COLD_VERSION = version
        if version in {4, 5}:
            _install_diagnostic_engine(module, package_ref, attempt)
        if version == 5:
            _install_store_engine(module, package_ref, attempt)
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise


def for_plan(plan, *, profile_sha256=None):
    path = plan["dispatch_epoch_ref"]["path"]
    match = re.fullmatch(
        r"/var/lib/autostop-crm-forward/cold/v1/epochs/([0-9a-f-]{36})/epoch\.json", path
    )
    original.require(match is not None, "cold_backup_production_epoch_path")
    arguments = (plan["candidate_manager_sha"], match[1], plan["attempt_id"])
    legacy = engine(*arguments)
    if profile_sha256 is None or legacy.profile_sha256() == profile_sha256:
        return legacy
    for version in (4, 5):
        current = engine(
            *arguments, version=version, package_ref=plan["cold_authority_package_ref"]
        )
        if current.profile_sha256() == profile_sha256:
            return current
    raise original.BackupRefusal("cold_backup_profile")


def transport_engine(plan, *, profile_sha256=None):
    from scripts import j1_forward_transport as transport

    backup = for_plan(plan, profile_sha256=profile_sha256)
    name = backup.__name__.replace("_backup_", "_transport_")
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__file__ = transport.__file__
    sys.modules[name] = module
    try:
        exec(
            compile(Path(transport.__file__).read_bytes(), transport.__file__, "exec"),
            module.__dict__,
        )
        module.PROFILE_SHA256 = backup.profile_sha256()
        module._CORE = backup
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise


ENTRY = "autostop.j1.cold-capture-entry.v1"
BOUNDARY = "autostop.j1.cold-capture-boundary.v1"
COMPLETE = "autostop.j1.cold-capture-completed.v1"
COMMON = {
    "scope",
    "attempt_id",
    "plan_ref",
    "package_ref",
    "epoch_ref",
    "binding_sha256",
    "boot_id",
    "observed_monotonic_ns",
}
NATIVE_FIELDS = {
    "schema",
    "scope",
    "ok",
    "phase",
    "attempt_id",
    "package_ref",
    "epoch_ref",
    "binding_sha256",
    "boot_id",
    "observed_monotonic_ns",
    "original_barriers_closed",
    "original_hold_retained",
    "old_writer_allowed",
    "candidate_running",
    "all_six_policy_refs",
    "retired_scope_sha256",
}


def full_native(value, controller):
    binding = value["package"]["binding"]
    observed = time.monotonic_ns()
    cold._binding(binding, now_ns=observed)
    return {
        "schema": "autostop.j1.cold-native-observation.v1",
        "scope": controller.provider.scope,
        "ok": True,
        "phase": "closed",
        "attempt_id": binding["attempt_id"],
        "package_ref": controller.package_ref,
        "epoch_ref": controller.epoch_ref,
        "binding_sha256": cold.digest(binding),
        "boot_id": binding["boot_id"],
        "observed_monotonic_ns": observed,
        "original_barriers_closed": True,
        "original_hold_retained": True,
        "old_writer_allowed": False,
        "candidate_running": False,
        "all_six_policy_refs": value["package"]["policies"],
        "retired_scope_sha256": cold.digest(value["history"]["inventory"]),
    }


class CaptureInterval:
    def __init__(self, controller, plan_ref, directory, *, gate):
        self.controller, self.provider = controller, controller.provider
        self.plan_ref, self.directory, self.gate = plan_ref, Path(directory), gate
        self.witnesses, self.kernel, self.completion_ref = [], None, None
        self.entry_ref, self.status = None, "new"

    def _common(self):
        binding = self.value["package"]["binding"]
        observed = time.monotonic_ns()
        cold._binding(binding, now_ns=observed)
        return {
            "scope": self.provider.scope,
            "attempt_id": binding["attempt_id"],
            "plan_ref": self.plan_ref,
            "package_ref": self.controller.package_ref,
            "epoch_ref": self.controller.epoch_ref,
            "binding_sha256": cold.digest(binding),
            "boot_id": binding["boot_id"],
            "observed_monotonic_ns": observed,
        }

    def _save(self, name, document):
        return io.write_new(self.directory / (name + ".json"), document)

    def _closed(self):
        controller, binding = self.controller, self.value["package"]["binding"]
        cold.need(not (controller.root / "safety-closed.json").exists(), "capture_safety_closed")
        cold.need(self.provider.boot() == binding["boot_id"], "capture_boot_changed")
        cold._binding(binding, now_ns=time.monotonic_ns())
        cold.need(
            epochs.read_epoch(controller.epoch_ref, self.provider) == controller.epoch,
            "capture_epoch_changed",
        )
        fence = epochs.verify(controller.epoch, self.value["package"], self.provider)
        read = (
            self.provider.read_path
            if self.provider.scope == "native_fixture"
            else io.read_protected
        )
        cold.need(
            io.document(read(fence["state_path"])) == epochs.closed_state(controller.epoch),
            "capture_epoch_not_closed",
        )
        controller._closed_slots()

    def _versions(self):
        self.sources.check_versions()
        for root, versions in self.kernel.cache.items():
            if self.value["kernel"]["schema"] == "autostop.j1.cold-kernel.v2":
                cold.need(native._tree_versions((root,)) == versions, "capture_cache_changed")
            else:
                cold.need(
                    native._executable_version(Path(root).lstat()) == versions[root],
                    "capture_cache_changed",
                )

    def __enter__(self):
        cold.need(self.status == "new", "capture_interval_already_used")
        self.status = "entering"
        self.gate()
        self.value, self.sources = native._observe_package(
            self.controller.package_ref, provider=self.provider, required_scope=self.provider.scope
        )
        try:
            plan = io.document(self.provider.read_reference(self.plan_ref))
            cold.need(
                Path(self.plan_ref["path"]).parent == self.directory
                and plan["attempt_id"] == self.value["package"]["binding"]["attempt_id"]
                and plan["cold_authority_package_ref"] == self.controller.package_ref
                and plan["dispatch_epoch_ref"] == self.controller.epoch_ref,
                "capture_plan_binding",
            )
            self.kernel = native.CaptureKernelCut(self.value, self.provider, self.sources)
            self._closed()
            self._versions()
            self.cut = cold.digest(
                {
                    "sources": self.sources.versions,
                    "trees": []
                    if self.sources.trees is None
                    else [
                        {"key": list(key), "roots": roots, "versions": versions}
                        for key, (roots, versions) in sorted(self.sources.trees.measured.items())
                    ],
                    "cache": self.kernel.cache,
                }
            )
            proof = self._save(
                "cold-capture-entry-native", full_native(self.value, self.controller)
            )
            self.gate()
            self.entry_ref = self._save(
                "cold-capture-entry",
                {
                    "schema": ENTRY,
                    **self._common(),
                    "native_ref": proof,
                    "source_cut_sha256": self.cut,
                },
            )
            self.status = "active"
            return self
        except BaseException:
            if self.kernel is not None:
                self.kernel.close()
            raise

    def observe(self):
        cold.need(self.status == "active", "capture_interval_not_active")
        self.gate()
        for operation in (
            self.provider.verify_closure,
            self.provider.verify_retirement,
            self.provider.verify_ingress,
            self.provider.verify_original_hold,
        ):
            operation(self.value)
        self._old_crm()
        self.kernel.verify()
        self._versions()
        self._closed()
        self.gate()
        index = len(self.witnesses)
        cold.need(index < 200002, "capture_boundary_limit")
        document = {
            "schema": BOUNDARY,
            **self._common(),
            "entry_ref": self.entry_ref,
            "index": index,
            "source_cut_sha256": self.cut,
            "original_barriers_closed": True,
            "original_hold_retained": True,
            "old_writer_allowed": False,
            "candidate_running": False,
        }
        reference = self._save(f"cold-capture-boundary-{index:06d}", document)
        result = {"attempt_id": document["attempt_id"], **reference}
        self.witnesses.append(result)
        return result

    def _old_crm(self):
        if self.value["pending"]["schema"] == "autostop.j1.cold-pending-disposition.v2":
            from scripts import j1_forward_cold_catalog_native

            inventory = io.document(
                self.provider.read_reference(self.value["pending"]["inventory_ref"])
            )
            if self.provider.scope == "native_fixture":
                self.provider.verify_old_crm_ingress(
                    inventory["old_crm_ingress_ref"], binding=self.value["package"]["binding"]
                )
            else:
                j1_forward_cold_catalog_native.verify_old_crm_ingress(
                    inventory["old_crm_ingress_ref"],
                    provider=self.provider,
                    binding=self.value["package"]["binding"],
                )

    def __exit__(self, error_type, _error, _trace):
        try:
            if error_type is None:
                self.gate()
                cold.need(len(self.witnesses) >= 2, "capture_boundaries_missing")
                self._versions()
                value = native.verify_package(
                    self.controller.package_ref,
                    provider=self.provider,
                    required_scope=self.provider.scope,
                )
                cold.need(value == self.value, "capture_package_changed")
                self._closed()
                self.kernel.verify()
                self._versions()
                self.gate()
                proof = self._save("cold-capture-exit-native", full_native(value, self.controller))
                self.completion_ref = self._save(
                    "cold-capture-completed",
                    {
                        "schema": COMPLETE,
                        **self._common(),
                        "entry_ref": self.entry_ref,
                        "native_ref": proof,
                        "source_cut_sha256": self.cut,
                        "boundary_count": len(self.witnesses),
                        "witnesses_sha256": cold.digest(self.witnesses),
                    },
                )
        finally:
            if self.kernel is not None:
                self.kernel.close()
            self.status = "closed"


def _document(read_reference, reference):
    cold.reference(reference)
    raw = read_reference(reference)
    cold.need(
        type(raw) is bytes
        and len(raw) <= cold.MAX_SOURCE_BYTES
        and io.sha(raw) == reference["sha256"],
        "capture_restored_reference",
    )
    return io.document(raw)


def _bound(value, schema, extra, response):
    cold.fields(value, {"schema", *COMMON, *extra}, "capture_document_fields")
    plan, package, epoch = (
        response["plan"],
        response["evidence"]["package"],
        response["evidence"]["epoch"],
    )
    binding = package["binding"]
    cold.need(
        value["schema"] == schema
        and value["scope"] == package["scope"] == epoch["scope"]
        and value["scope"] in cold.SCOPES
        and value["attempt_id"] == plan["attempt_id"] == binding["attempt_id"]
        and value["plan_ref"] == response["plan_ref"]
        and value["package_ref"] == plan["cold_authority_package_ref"]
        and value["epoch_ref"] == plan["dispatch_epoch_ref"]
        and value["binding_sha256"] == cold.digest(binding)
        and value["boot_id"] == binding["boot_id"] == epoch["boot_id"]
        and type(value["observed_monotonic_ns"]) is int,
        "capture_document_binding",
    )
    cold._binding(binding, now_ns=value["observed_monotonic_ns"])


def _native(read_reference, reference, common, response):
    value = _document(read_reference, reference)
    cold.fields(value, NATIVE_FIELDS, "capture_native_fields")
    package = response["evidence"]["package"]
    cold.need(
        value["schema"] == "autostop.j1.cold-native-observation.v1"
        and value["ok"] is True
        and value["phase"] == "closed"
        and all(
            value[key] == common[key]
            for key in (
                "scope",
                "attempt_id",
                "package_ref",
                "epoch_ref",
                "binding_sha256",
                "boot_id",
            )
        )
        and value["original_barriers_closed"] is True
        and value["original_hold_retained"] is True
        and value["old_writer_allowed"] is False
        and value["candidate_running"] is False
        and value["all_six_policy_refs"] == package["policies"]
        and type(value["retired_scope_sha256"]) is str
        and cold.HEX.fullmatch(value["retired_scope_sha256"])
        and type(value["observed_monotonic_ns"]) is int
        and value["observed_monotonic_ns"] <= common["observed_monotonic_ns"],
        "capture_full_native_binding",
    )
    cold._binding(package["binding"], now_ns=value["observed_monotonic_ns"])
    return value


def validate_restored_interval(data_manifest, response, read_reference):
    """Pure verification over independently restored opaque bytes; no execution."""
    captured = response["evidence"]["backup_capture"]
    complete = _document(read_reference, captured["capture_interval_ref"])
    _bound(
        complete,
        COMPLETE,
        {"entry_ref", "native_ref", "source_cut_sha256", "boundary_count", "witnesses_sha256"},
        response,
    )
    entry = _document(read_reference, complete["entry_ref"])
    _bound(entry, ENTRY, {"native_ref", "source_cut_sha256"}, response)
    cold.need(
        type(entry["source_cut_sha256"]) is str
        and cold.HEX.fullmatch(entry["source_cut_sha256"])
        and complete["source_cut_sha256"] == entry["source_cut_sha256"],
        "capture_source_cut",
    )
    first = _native(read_reference, entry["native_ref"], entry, response)
    last = _native(read_reference, complete["native_ref"], complete, response)
    cold.need(
        first["retired_scope_sha256"] == last["retired_scope_sha256"],
        "capture_retired_scope_changed",
    )
    witnesses = data_manifest["external_closure_observations"]
    cold.need(
        type(witnesses) is list
        and 2 <= len(witnesses) <= 200002
        and len(witnesses) == 2 * len(data_manifest["artifacts"]) + 2
        and type(complete["boundary_count"]) is int
        and complete["boundary_count"] == len(witnesses)
        and complete["witnesses_sha256"] == cold.digest(witnesses),
        "capture_witness_inventory",
    )
    previous = entry["observed_monotonic_ns"]
    seen = set()
    for index, witness in enumerate(witnesses):
        cold.fields(witness, {"attempt_id", "path", "sha256"}, "capture_witness_fields")
        cold.need(
            witness["attempt_id"] == entry["attempt_id"] and witness["path"] not in seen,
            "capture_witness_identity",
        )
        seen.add(witness["path"])
        boundary = _document(read_reference, {key: witness[key] for key in ("path", "sha256")})
        _bound(
            boundary,
            BOUNDARY,
            {
                "entry_ref",
                "index",
                "source_cut_sha256",
                "original_barriers_closed",
                "original_hold_retained",
                "old_writer_allowed",
                "candidate_running",
            },
            response,
        )
        cold.need(
            boundary["entry_ref"] == complete["entry_ref"]
            and type(boundary["index"]) is int
            and boundary["index"] == index
            and boundary["source_cut_sha256"] == entry["source_cut_sha256"]
            and boundary["original_barriers_closed"] is True
            and boundary["original_hold_retained"] is True
            and boundary["old_writer_allowed"] is False
            and boundary["candidate_running"] is False
            and previous <= boundary["observed_monotonic_ns"] <= last["observed_monotonic_ns"],
            "capture_boundary_binding",
        )
        previous = boundary["observed_monotonic_ns"]
    cold.need(
        previous <= last["observed_monotonic_ns"] <= complete["observed_monotonic_ns"],
        "capture_completion_order",
    )
    if data_manifest.get("profile", {}).get("name") in {PROFILE_V4, PROFILE_V5}:
        diagnostic = validate_restored_diagnostic(data_manifest, response, read_reference)
        cold.need(
            entry["observed_monotonic_ns"]
            <= diagnostic["started_monotonic_ns"]
            <= diagnostic["completed_monotonic_ns"]
            <= complete["observed_monotonic_ns"],
            "diagnostic_capture_interval_binding",
        )
    if data_manifest.get("profile", {}).get("name") == PROFILE_V5:
        validate_restored_store(data_manifest, response, read_reference)
    return {
        "boundary_count": len(witnesses),
        "source_cut_sha256": entry["source_cut_sha256"],
        "native_acceptance": False,
    }
