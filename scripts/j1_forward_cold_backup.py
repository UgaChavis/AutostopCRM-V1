"""Explicit cold-v3 data profile; the original fixed profile is unchanged."""

from __future__ import annotations

import hashlib
import re
import sys
import time
import types
import uuid
from pathlib import Path

from scripts import j1_forward_backup as original
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

PROFILE = "crm_manager_all_profiles_cold_v3"
BASE_SHA256 = "e9691dd10f4a655ebe87b89fede6ba530f45361d0ef6602c35e22f2129f42157"
NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")


def engine(revision, generation, attempt):
    original.require(
        type(revision) is str and cold.SHA1.fullmatch(revision), "cold_backup_revision"
    )
    original.require(cold._uuid(generation) and cold._uuid(attempt), "cold_backup_epoch")
    key = revision + "_" + generation + "_" + attempt
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
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise


def for_plan(plan):
    path = plan["dispatch_epoch_ref"]["path"]
    match = re.fullmatch(
        r"/var/lib/autostop-crm-forward/cold/v1/epochs/([0-9a-f-]{36})/epoch\.json", path
    )
    original.require(match is not None, "cold_backup_production_epoch_path")
    return engine(plan["candidate_manager_sha"], match[1], plan["attempt_id"])


def transport_engine(plan):
    from scripts import j1_forward_transport as transport

    backup = for_plan(plan)
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
            return self
        except BaseException:
            if self.kernel is not None:
                self.kernel.close()
            raise

    def observe(self):
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
    return {
        "boundary_count": len(witnesses),
        "source_cut_sha256": entry["source_cut_sha256"],
        "native_acceptance": False,
    }
