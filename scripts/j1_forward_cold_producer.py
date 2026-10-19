"""Executable cold preparation under the original qualified controller's E/D.

No production work occurs on import or construction. This is not an issuer of
final application permission. Six entries and CRM replacement still require
the sealed final plan, actual backup/independent restore and launch journal.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_catalog as catalog
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_history as historical
from scripts import j1_forward_cold_install as install
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_producer_inputs as inputs
from scripts import j1_forward_cold_producer_native as effectors
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_io as io
from scripts import j1_forward_state as state

RESULT_FIELDS = {
    "package_ref",
    "runtime_bindings",
    "origin",
    "host_baseline",
    "previous_container",
    "review_key_ref",
    "ci",
    "maintenance_parent",
}


def consumer_digest():
    files = {
        name: Path(__file__).with_name("j1_forward_cold_" + name + ".py")
        for name in ("authority", "native", "guard", "history", "hold")
    }
    return cold.digest({key: io.sha(path.read_bytes()) for key, path in files.items()})


def validate_binding(document, request, identity, *, boot_id, now_ns=None, now=None):
    current_ns = time.monotonic_ns() if now_ns is None else now_ns
    current = datetime.now(UTC) if now is None else now
    binding = document["binding"]
    cold._binding(binding, now_ns=current_ns)
    cold.need(
        binding["deployment_lock_identity"] == identity
        and binding["boot_id"] == boot_id
        and binding["consumer_sha256"] == consumer_digest(),
        "producer_binding_native_source_D",
    )
    pairs = (
        ("attempt_id", "attempt_id"),
        ("generation", "owner_generation"),
        ("candidate_manager_sha", "candidate_manager_sha"),
    )
    cold.need(
        all(binding[key] == request[value] for key, value in pairs)
        and binding["candidate_crm_sha"] == request["candidate"]["revision"],
        "producer_binding_request",
    )
    remaining = (state._timestamp(request["deadline_utc"]) - current).total_seconds()
    cold.need(
        0 < remaining <= 600 and binding["deadline_ns"] <= current_ns + int(remaining * 1e9),
        "producer_binding_deadline_extension",
    )
    return binding


def http_configuration(binding):
    """Generate only after closure; never rewrite an old request to this epoch."""
    from scripts import j1_forward_cold_http as cut

    document = {
        "schema": "autostop.j1.cold-http-ingress.v1",
        "scope": "production_native",
        **epochs.common(binding),
        "candidate_crm_sha": binding["candidate_crm_sha"],
        "candidate_manager_sha": binding["candidate_manager_sha"],
        "public_origin": "https://crm.autostopcrm.ru",
    }
    document.update(cut.expected_prefixes(document))
    return cut.parse_document(
        document,
        {
            **epochs.common(binding),
            "source_revision": binding["candidate_manager_sha"],
            "role": "crm-embedded",
        },
    ).document


def configure_fresh_http(document, binding, config, config_ref, *, backend):
    """Only fresh owned executors receive newly configured generation URLs."""
    result = {**document, "runtime_env": {}}
    for role, reference in document["runtime_env"].items():
        values = inputs.environment(backend.provider.read_reference(reference))
        values["AUTOSTOP_CRM_MCP_URL"] = "http://127.0.0.1:8001" + config["mcp_prefix"] + "/mcp"
        values["AUTOSTOP_AUTOMATION_CRM_API_URL"] = "http://127.0.0.1:8000" + config["board_prefix"]
        values["LD_LIBRARY_PATH"] = (
            "/opt/autostop-manager-cold-releases/"
            + binding["candidate_manager_sha"]
            + "/python/lib"
        )
        if role == "crm-embedded":
            values["AUTOSTOP_COLD_HTTP_INGRESS_CONFIG"] = config_ref["path"]
            values["AUTOSTOP_COLD_HTTP_INGRESS_CONFIG_SHA256"] = config_ref["sha256"]
        raw = "".join(key + "=" + value + "\n" for key, value in sorted(values.items())).encode()
        result["runtime_env"][role] = backend.publish(
            backend.directory / "inputs/fresh-environment" / (role + ".env"), raw, raw=True
        )
    return result


class NativeProducer:
    """Fixed production constructor; tests substitute named private boundaries."""

    def __init__(self, request_ref, request, *, deployment_fd):
        self.request_ref, self.request, self.deployment_fd = request_ref, request, deployment_fd
        self.backend = effectors.NativeBackend(request_ref, request, deployment_fd)
        self.provider = self.backend.provider
        self.prepared = None
        self.closed = False
        self.receipts = {}

    def __enter__(self):
        self.backend.boundary()
        return self

    def _document(self, name, binding, **fields):
        return {
            "schema": "autostop.j1.cold-" + name + ".v1",
            "scope": self.provider.scope,
            "binding": binding,
            **fields,
        }

    def _origin(self, reference, history, binding):
        origin = inputs.read(reference, self.provider)
        cold.fields(origin, inputs.ORIGIN_FIELDS, "producer_origin_fields")
        for key in inputs.ORIGIN_FIELDS - {"nonce", "deployment_lock_identity"}:
            cold.reference(origin[key])
            self.provider.read_reference(origin[key])
        cold.need(
            all(
                origin[key] == history["anchor"][value]
                for key, value in (
                    ("origin_plan_ref", "plan_ref"),
                    ("origin_scope_ref", "scope_ref"),
                    ("origin_closure_ref", "closure_ref"),
                    ("hold_ref", "original_hold_ref"),
                )
            )
            and origin["deployment_lock_identity"] == binding["deployment_lock_identity"]
            and type(origin["nonce"]) is str
            and cold.HEX.fullmatch(origin["nonce"]),
            "producer_origin_coverage",
        )
        return origin

    def _pending(self, document, binding, history, quarantine_ref, old_crm_ref):
        policy = inputs.read(document["catalog_policy_ref"], self.provider)
        cold.need(
            policy["predecessor_ref"] == document["predecessor_ref"], "producer_catalog_history"
        )
        measured = catalog.measure(
            policy,
            binding,
            provider=self.provider,
            verify_path_binding=lambda unit, source, policy: effectors.verify_catalog_path(
                unit, source, policy, provider=self.provider, history=history
            ),
        )
        checkpoint = self.backend.evidence(
            "source-checkpoint",
            {
                "schema": "autostop.j1.cold-legacy-catalog-checkpoint.v1",
                "scope": self.provider.scope,
                "binding": binding,
                "policy_ref": document["catalog_policy_ref"],
                "measurement": measured,
                "old_crm_ingress_ref": old_crm_ref,
            },
        )
        self.receipts["source_checkpoint_ref"] = checkpoint
        # Repeat the actual source measurement, old actor/CRM retirement and
        # source pins. The producer's measured object alone is not evidence.
        verified = catalog.verify_checkpoint(checkpoint, provider=self.provider, binding=binding)
        cold.need(verified["rows"] == measured["rows"], "producer_catalog_recheck")
        inventory = self.backend.evidence(
            "pending-inventory",
            {
                "schema": "autostop.j1.cold-pending-inventory.v2",
                "scope": self.provider.scope,
                "binding": binding,
                "coverage": "source_defined_durable_catalogs_with_retired_unenumerated_ram",
                "payload_semantics": "observed_record_fingerprint_unknown_only",
                "source_checkpoint_ref": checkpoint,
                "old_crm_ingress_ref": old_crm_ref,
                "rows": [
                    {
                        "request": {key: row[key] for key in ("op_id", "fingerprint")},
                        "evidence_refs": [checkpoint],
                    }
                    for row in measured["rows"]
                ],
            },
        )
        pending = {
            "schema": "autostop.j1.cold-pending-disposition.v2",
            "scope": self.provider.scope,
            "binding": binding,
            "quarantine_ref": quarantine_ref,
            "inventory_ref": inventory,
            "requests": [{**row, "evidence_refs": [checkpoint]} for row in measured["rows"]],
        }
        return self.backend.evidence("pending", pending), pending

    def _dispatch(self, binding, pending, history):
        base = Path("/var/lib/autostop-manager-cold") / binding["generation"] / "dispatch"
        self.backend.directory_at(base, mode=0o755)
        lock_ref = self.backend.publish(base / "dispatch.lock", b"", raw=True, mode=0o644)
        info = Path(lock_ref["path"]).lstat()
        tombstones = epochs.tombstones(binding, pending, history["anchor"]["origin_attempt_id"])
        tombstone_ref = self.backend.publish(base / "tombstones.json", tombstones, mode=0o644)
        self.backend.publish(base / "state.json", epochs.closed_state(binding), mode=0o644)
        fence_ref = self.backend.publish(
            base / "fence.json",
            {
                "schema": "autostop.j1.cold-dispatch-fence.v1",
                **epochs.common(binding),
                "state_path": str(base / "state.json"),
                "dispatch_lock_path": str(base / "dispatch.lock"),
                "dispatch_lock_identity": {
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "ctime_ns": info.st_ctime_ns,
                },
            },
            mode=0o644,
        )
        return {
            role: epochs.runtime_binding(
                binding,
                role,
                epochs.expected_paths(
                    binding["generation"], binding["candidate_manager_sha"], role
                ),
                tombstone_ref,
                fence_ref,
            )
            for role in sorted(epochs.CONSUMERS)
        }

    def _stage(self, document, binding, manager_files, bindings):
        refs = {}
        for name, raw in sorted(manager_files.items()):
            refs[name] = self.backend.publish(
                self.backend.directory / "inputs/manager-source" / name, raw, raw=True, mode=0o644
            )
        controller = {
            Path(name).stem: {
                "path": str(Path(__file__).parent / Path(name).name),
                "sha256": digest,
            }
            for name, digest in self.request["source"]["consumers"].items()
        }
        # Confirm all environments before the first installed application byte.
        for reference in document["runtime_env"].values():
            inputs.environment(self.provider.read_reference(reference))
        receipt = install.inert_stage(
            binding,
            manager_files=refs,
            controller_files=controller,
            runtime_bindings=bindings,
            runtime_env=document["runtime_env"],
            credentials=document["credentials"],
            deployment_fd=self.deployment_fd,
            controller_source=self.request["source"],
        )
        self.receipts["inert_installation_ref"] = self.backend.evidence(
            "inert-installation", receipt
        )
        runtime = inputs.read(document["runtime_artifact_ref"], self.provider)
        capsule.validate_index(runtime, binding=binding, scope=self.provider.scope)
        self.receipts["runtime_artifact_verification_ref"] = self.backend.verify_runtime_artifact(
            document, runtime
        )
        installed = capsule.materialize(runtime, read_reference=self.provider.read_reference)
        self.receipts["runtime_installation_ref"] = self.backend.evidence(
            "runtime-installation", installed
        )
        capsule.verify_installed(runtime)
        return receipt

    def _policies(self, document, binding, staged, producer_receipt_ref):
        source_files = sorted(staged["manager_files"].values(), key=lambda row: row["path"])
        source_index = self.backend.evidence(
            "source-index", self._document("source-index", binding, files=source_files)
        )
        guard_ref, native_ref = (
            staged["controller_files"][name]
            for name in ("j1_forward_cold_guard", "j1_forward_cold_native")
        )
        source_package = self.backend.evidence(
            "source-package",
            {
                "schema": "autostop.j1.cold-source-package.v2",
                "scope": self.provider.scope,
                "binding": binding,
                "revision": binding["candidate_manager_sha"],
                "source_index_ref": source_index,
                "guard_ref": guard_ref,
                "native_ref": native_ref,
                "producer_inputs_ref": self.request["inputs_ref"],
                "producer_receipt_ref": producer_receipt_ref,
            },
        )
        runtime = inputs.read(document["runtime_artifact_ref"], self.provider)
        executable = Path(runtime["runtime_roots"]["venv"]) / "bin/python3.12"
        from scripts import j1_forward_cold_kernel as kernel

        interpreter = kernel.file_digest(executable)
        host_libraries = self.backend.host_startup_references(runtime, interpreter)
        startup = self.backend.evidence(
            "startup-index",
            self._document(
                "startup-index",
                binding,
                files=sorted(
                    [*staged["controller_files"].values(), interpreter, *host_libraries],
                    key=lambda row: row["path"],
                ),
            ),
        )
        policies = {}
        for unit in cold.ALLOWED:
            role = epochs.ROLES[unit]
            path = epochs.expected_paths(
                binding["generation"], binding["candidate_manager_sha"], role
            )
            values = inputs.environment(self.provider.read_reference(document["runtime_env"][role]))
            binding_ref = staged["runtime_bindings"][role]
            values.update(
                AUTOSTOP_COLD_RUNTIME_BINDING=binding_ref["path"],
                AUTOSTOP_COLD_RUNTIME_BINDING_SHA256=binding_ref["sha256"],
            )
            environment = self.backend.evidence(
                "environment-" + role,
                self._document("environment", binding, unit=unit, values=values),
            )
            actual = self.provider.systemd(unit)
            paths = [actual["FragmentPath"], *effectors.systemd_paths(actual["DropInPaths"])]
            files = [
                {"path": str(path), "sha256": io.sha(io.read_protected(path))}
                for path in sorted(set(paths))
            ]
            unit_ref = self.backend.evidence(
                "unit-" + role,
                self._document("unit", binding, unit=unit, properties=actual, files=files),
            )
            profile = {
                "source_root": path["source_root"],
                "source_index_ref": source_index,
                "interpreter_ref": interpreter,
                "runtime_index_ref": document["runtime_artifact_ref"],
                "startup_index_ref": startup,
                "environment_ref": environment,
                "unit_ref": unit_ref,
                "guard_ref": guard_ref,
                "native_ref": native_ref,
                "argv": install.policy_argv(binding, role, interpreter),
                "cwd": path["source_root"],
                "credentials": document["credentials"][role],
            }
            policies[unit] = self.backend.evidence(
                "policy-" + role,
                cold.compile_policy(
                    binding,
                    self.provider.scope,
                    unit,
                    profile,
                    source_package,
                    read_reference=self.provider.read_reference,
                ),
            )
        return source_package, policies, interpreter, startup

    def prepare(self):
        cold.need(not self.closed, "producer_closed")
        if self.prepared is not None:
            native.verify_package(self.prepared["package_ref"], provider=self.provider)
            return dict(self.prepared)
        identity = self.backend.boundary()
        document = inputs.validate(
            inputs.read(self.request["inputs_ref"], self.provider), self.request
        )
        binding = validate_binding(document, self.request, identity, boot_id=self.provider.boot())
        self.backend.binding = binding
        self.backend.verify_crm_production_ci()
        self.receipts["generation_custody_ref"] = self.backend.generation_custody(document, binding)
        history = historical.load(
            document["predecessor_ref"], read_reference=self.provider.read_reference
        )
        origin = self._origin(document["origin_ref"], history, binding)
        # Exact production-branch CI and Git bytes must precede effects.
        _index, manager_files, _manager_ci = inputs.manager_source(
            document, verify_github=self.backend.github_manager
        )
        ci = inputs.read(self.request["ci_ref"], self.provider)
        self.provider.read_reference(document["review_key_ref"])
        maintenance_parent = self.backend.require_original_marker(history)
        closure, hold = self.backend.close_original_ingress(binding, history)
        retirement, quarantine = self.backend.retire_original(
            binding, history, closure_ref=closure, hold_ref=hold
        )
        config = http_configuration(binding)
        config_path = (
            Path("/etc/autostop-manager-cold")
            / binding["generation"]
            / "crm-embedded/http-ingress.json"
        )
        config_ref = self.backend.publish(config_path, config, mode=0o644)
        self.receipts["http_address_publication_ref"] = self.backend.evidence(
            "http-address-publication",
            {
                "schema": "autostop.j1.cold-http-address-publication.v1",
                "scope": self.provider.scope,
                "binding": binding,
                "request_ref": self.request_ref,
                "retirement_ref": retirement,
                "quarantine_ref": quarantine,
                "http_ingress_config_ref": config_ref,
                "publication_ns": time.monotonic_ns(),
                "temporal_scope": "private_uuid_generation_until_retirement_then_address_publication",
                "historical_nonpublication": "source_and_operator_premise_not_deduced_from_parser",
            },
        )
        old_crm, previous, host = self.backend.stop_old_crm(binding, history, config_ref)
        self.receipts["old_crm_ingress_ref"] = old_crm
        document = configure_fresh_http(document, binding, config, config_ref, backend=self.backend)
        hold_checkpoint = self.backend.original_hold_checkpoint(binding, history)
        pending_ref, pending = self._pending(document, binding, history, quarantine, old_crm)
        runtime_bindings = self._dispatch(binding, pending, history)
        staged = self._stage(document, binding, manager_files, runtime_bindings)
        self.receipts["fresh_role_material_ref"] = self.backend.install_fresh_role_material(
            document, binding, staged
        )
        checkpoint = self.backend.prepare_cache_checkpoint(
            binding, history, document["kernel"]["cache_source_ref"]
        )
        cache = self.backend.install_cache(binding, checkpoint)
        self.receipts["cache_checkpoint_source_ref"] = self.backend.cache_checkpoint_source_ref
        producer_receipt_ref = self.backend.evidence(
            "producer-receipt",
            self._document(
                "producer-receipt", binding, request_ref=self.request_ref, **self.receipts
            ),
        )
        source_package, policies, interpreter, startup = self._policies(
            document, binding, staged, producer_receipt_ref
        )
        context = self.backend.evidence(
            "kernel-context",
            self._document(
                "kernel-context",
                binding,
                closure_ref=closure,
                hold_ref=hold,
                predecessor_ref=document["predecessor_ref"],
            ),
        )
        kernel_ref = self.backend.start_kernel(
            document,
            binding,
            interpreter=interpreter,
            startup_ref=startup,
            cache_view_ref=cache,
            context_ref=context,
        )
        self.backend.configure_wake(binding, kernel_ref)
        lifecycle = self.backend.evidence(
            "lifecycle",
            self._document(
                "lifecycle",
                binding,
                phase="cold_quarantined",
                kernel_ref=kernel_ref,
                closure_ref=closure,
                hold_ref=hold,
                policies=policies,
                pending_ref=pending_ref,
            ),
        )
        raw = cold.compile_package(
            str(native.package_path(binding["attempt_id"])),
            scope=self.provider.scope,
            binding=binding,
            predecessor_ref=document["predecessor_ref"],
            source_package_ref=source_package,
            policies=policies,
            evidence_refs={
                "closure_ref": closure,
                "hold_ref": hold,
                "retirement_ref": retirement,
                "quarantine_ref": quarantine,
                "pending_ref": pending_ref,
                "kernel_ref": kernel_ref,
                "lifecycle_ref": lifecycle,
                "original_hold_ref": history["anchor"]["original_hold_ref"],
                "hold_checkpoint_ref": hold_checkpoint,
            },
            read_reference=self.provider.read_reference,
        )
        package_ref = self.backend.publish(
            native.package_path(binding["attempt_id"]), raw, raw=True
        )
        native.verify_package(package_ref, provider=self.provider)
        result = {
            "package_ref": package_ref,
            "runtime_bindings": staged["runtime_bindings"],
            "origin": origin,
            "host_baseline": host,
            "previous_container": previous,
            "review_key_ref": document["review_key_ref"],
            "ci": ci,
            "maintenance_parent": maintenance_parent,
        }
        cold.fields(result, RESULT_FIELDS, "producer_result")
        self.prepared = result
        return dict(result)

    def adopt_runtime(self, commit_ref, cold_completion_ref):
        cold.need(self.prepared is not None and not self.closed, "producer_runtime_not_prepared")
        validated = native.verify_package(self.prepared["package_ref"], provider=self.provider)
        body = {
            "schema": "autostop.j1.cold-kernel-runtime-custody.v1",
            "scope": self.provider.scope,
            "binding": validated["package"]["binding"],
            "package_ref": self.prepared["package_ref"],
            "kernel_ref": validated["package"]["kernel_ref"],
            "commit_ref": commit_ref,
            "cold_completion_ref": cold_completion_ref,
        }
        from scripts import j1_forward_cold_kernel_supervisor as supervisor

        seed_ref = io.reference(
            supervisor.paths(body["binding"])["directory"] / "supervisor-seed.json"
        )
        seed = inputs.read(seed_ref, self.provider)
        # Check the actual HEAD/COMMIT/complete graph before publication. The
        # supervisor independently repeats this against its own ready ref.
        temporary = self.backend.evidence("runtime-custody-candidate", body)
        supervisor.verify_custody(
            temporary, seed, validated["kernel"]["ready_ref"], provider=self.provider
        )
        ref = self.backend.publish(
            supervisor.paths(body["binding"])["directory"] / "runtime-custody.json", body
        )
        self.backend.adopted = True
        return ref

    def __exit__(self, exc_type, _exc, _traceback):
        self.closed = True
        self.backend.close(success=exc_type is None and self.prepared is not None)
