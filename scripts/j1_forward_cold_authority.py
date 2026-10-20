"""A separate all-profile cold contract; artifact validation grants no execution.

Warm authorities are historical references only. Fresh native measurements are
performed by ``j1_forward_cold_native`` while its caller retains the fence.
There is deliberately no installation, issuance, open-fence or replay API here.
"""

from __future__ import annotations

import copy
import re
import time
import uuid

from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.crm-manager-cold-authority-package.v1"
PLAN_SCHEMA = "autostop.j1.crm-manager-cold-forward-plan.v1"
PLAN_SCOPE = "crm_and_manager_all_profiles_cold"
CONTRACT = "all_six_profiles_cold_quarantined_v1"
SCOPES = {"native_fixture", "production_native"}
ALLOWED = tuple(
    sorted(
        (
            "autostop-j1.service",
            "autostop-manager-mcp.service",
            "autostop-manager-scheduler.service",
            "autostop-work-telegram.service",
            "autostop-telegram.service",
            "autostop-codex-wake.service",
        )
    )
)
DENIED = ("autostop-codex-start.service", "autostop-j1-browser.service")
UNITS = tuple(sorted((*ALLOWED, *DENIED)))
DENY_BYTES = (
    b"[Service]\nExecCondition=/usr/bin/false\nExecStartPre=\nExecStartPost=\n"
    b"ExecStop=\nExecStopPost=\nExecReload=\n"
)
BINDING_FIELDS = set(
    "attempt_id boot_id generation consumer_sha256 candidate_crm_sha candidate_manager_sha "
    "deployment_lock_identity not_before_ns deadline_ns".split()
)
PACKAGE_FIELDS = set(
    "schema scope contract binding predecessor_ref source_package_ref policies "
    "permanent_denied_units closure_ref hold_ref retirement_ref quarantine_ref "
    "pending_ref kernel_ref lifecycle_ref original_hold_ref hold_checkpoint_ref".split()
)
POLICY_FIELDS = set(
    "schema scope contract binding unit source_root source_index_ref interpreter_ref "
    "runtime_index_ref startup_index_ref environment_ref unit_ref guard_ref native_ref "
    "argv cwd credentials".split()
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
MAX_SOURCE_BYTES = 32 * 1024**2
MAX_RUNTIME_BYTES = 384 * 1024**2
PRODUCER_INPUT_FIELDS = set(
    "predecessor_ref origin_ref manager runtime_artifact_ref runtime_artifact_provenance_ref "
    "runtime_env credentials kernel catalog_policy_ref review_key_ref fresh_role_material_ref "
    "telegram_frontier_policy_ref".split()
)
PRODUCER_RECEIPT_REFS = set(
    "request_ref generation_custody_ref http_address_publication_ref "
    "runtime_artifact_verification_ref fresh_role_material_ref cache_checkpoint_source_ref "
    "old_crm_ingress_ref inert_installation_ref runtime_installation_ref source_checkpoint_ref".split()
)
PRODUCER_REQUEST_FIELDS = set(
    "schema scope attempt_id host boot_id owner_generation source candidate "
    "candidate_manager_sha inputs_ref ci_ref qualification_ref created_at deadline_utc "
    "runtime_seconds old_writer_allowed".split()
)


def need(condition, code):
    io.require(condition, "cold_" + code)


def fields(value, expected, code):
    need(type(value) is dict and set(value) == set(expected), code)
    return value


def digest(value):
    return io.sha(io.canonical(value))


def runtime_bytes(reference, raw, *, capsule=False):
    """Large v4 leaves are opaque native executables, never JSON/source code."""
    path = reference["path"].lower()
    need(type(raw) is bytes and len(raw) <= MAX_RUNTIME_BYTES, "runtime_blob_limit")
    if len(raw) > MAX_SOURCE_BYTES:
        need(
            not path.endswith(
                (".json", ".py", ".pyw", ".ps1", ".sh", ".md", ".yml", ".yaml", ".toml")
            )
            and (raw.startswith(b"\x1f\x8b") if capsule else raw.startswith((b"\x7fELF", b"MZ"))),
            "runtime_blob_kind",
        )
    return raw


def opaque_reader(reader, *, capsule=False):
    name = "read_runtime_capsule" if capsule else "read_opaque_reference"
    direct = getattr(reader, name, None)
    owner = getattr(reader, "__self__", None)
    return direct or getattr(owner, name, None) or reader


def identity(value):
    return (
        type(value) is list
        and len(value) == 2
        and all(type(item) is int and item >= 0 for item in value)
    )


def _uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def operation_id(value):
    """Preserve Manager's actual stable identity; never use it as a path."""
    if type(value) is not str or not 1 <= len(value) <= 200:
        return False
    try:
        return len(value.encode("utf-8")) <= 800 and not any(ord(char) < 32 for char in value)
    except UnicodeEncodeError:
        return False


def operation_key(value):
    need(operation_id(value), "operation_identity")
    return io.sha(value.encode("utf-8"))


def reference(value):
    fields(value, {"path", "sha256"}, "reference_fields")
    path = value["path"]
    need(
        type(path) is str
        and path.startswith("/")
        and ".." not in path.split("/")
        and "//" not in path
        and not path.endswith("/"),
        "reference_path",
    )
    need(type(value["sha256"]) is str and HEX.fullmatch(value["sha256"]), "reference_digest")
    return value


def _binding(value, *, now_ns):
    fields(value, BINDING_FIELDS, "binding_fields")
    need(all(_uuid(value[key]) for key in ("attempt_id", "boot_id", "generation")), "binding_uuid")
    need(
        type(value["consumer_sha256"]) is str and HEX.fullmatch(value["consumer_sha256"]),
        "consumer_digest",
    )
    need(
        all(
            type(value[key]) is str and SHA1.fullmatch(value[key])
            for key in ("candidate_crm_sha", "candidate_manager_sha")
        ),
        "candidate_revision",
    )
    need(identity(value["deployment_lock_identity"]), "deployment_lock_identity")
    need(
        all(type(value[key]) is int and value[key] > 0 for key in ("not_before_ns", "deadline_ns")),
        "binding_time",
    )
    need(
        value["not_before_ns"]
        <= now_ns
        < value["deadline_ns"]
        <= value["not_before_ns"] + 900 * 10**9,
        "binding_expired_or_window",
    )


class Reader:
    """Hash every leaf, then reread the complete reference graph after checking."""

    def __init__(self, read_reference):
        self.read = read_reference
        self.seen = {}
        self.opaque, self.capsules = set(), set()
        self._validated_indexes = set()

    def raw(self, ref, *, opaque=False):
        reference(ref)
        if ref["path"] in self.seen:
            previous, raw = self.seen[ref["path"]]
            need(previous == ref, "reference_alias")
            if opaque:
                runtime_bytes(ref, raw)
                self.opaque.add(ref["path"])
            else:
                need(len(raw) <= MAX_SOURCE_BYTES, "reference_source_limit")
            return raw
        raw = (opaque_reader(self.read) if opaque else self.read)(ref)
        need(type(raw) is bytes and io.sha(raw) == ref["sha256"], "reference_bytes")
        if opaque:
            runtime_bytes(ref, raw)
            self.opaque.add(ref["path"])
        else:
            need(len(raw) <= MAX_SOURCE_BYTES, "reference_source_limit")
        key = ref["path"]
        need(key not in self.seen or self.seen[key] == (ref, raw), "reference_alias")
        self.seen[key] = (dict(ref), raw)
        return raw

    def doc(self, ref):
        return io.document(self.raw(ref))

    def bound(self, ref, name, binding, scope, extra, *, version=1):
        value = self.doc(ref)
        fields(value, {"schema", "scope", "binding", *extra}, name + "_fields")
        need(
            value["schema"] == "autostop.j1.cold-" + name + ".v" + str(version)
            and value["scope"] == scope
            and value["binding"] == binding,
            name + "_binding",
        )
        return value

    def index(self, ref, name, binding, scope):
        header = self.doc(ref)
        key = (ref["path"], ref["sha256"], name, scope, digest(binding))
        # Reuse only a pure result for the same captured bytes in this reader.
        # Every actual leaf is still read again by recheck(), and a later
        # native observation constructs a new Reader.
        if key in self._validated_indexes:
            return header
        if (
            name == "runtime-index"
            and type(header) is dict
            and header.get("schema") == "autostop.j1.cold-runtime-index.v2"
        ):
            from scripts import j1_forward_cold_runtime_capsule

            j1_forward_cold_runtime_capsule.validate(
                header, binding=binding, scope=scope, read_reference=self.raw
            )
            self._validated_indexes.add(key)
            return header
        value = self.bound(ref, name, binding, scope, {"files"})
        files = value["files"]
        need(type(files) is list and files and len(files) <= 10000, name + "_files")
        paths = []
        for row in files:
            self.raw(row, opaque=name in {"runtime-index", "startup-index"})
            paths.append(row["path"])
        need(paths == sorted(set(paths)), name + "_order")
        self._validated_indexes.add(key)
        return value

    def recheck(self):
        for ref, raw in self.seen.values():
            if ref["path"] in self.capsules:
                read = opaque_reader(self.read, capsule=True)
            else:
                read = opaque_reader(self.read) if ref["path"] in self.opaque else self.read
            need(read(ref) == raw, "reference_changed")

    def read_runtime_capsule(self, reference):
        reference_path = reference["path"]
        if reference_path in self.seen:
            old, raw = self.seen[reference_path]
            need(old == reference, "reference_alias")
        else:
            raw = opaque_reader(self.read, capsule=True)(reference)
            need(type(raw) is bytes and io.sha(raw) == reference["sha256"], "reference_bytes")
            self.seen[reference_path] = (dict(reference), raw)
        runtime_bytes(reference, raw, capsule=True)
        self.capsules.add(reference_path)
        return raw


class _CapturedPackages:
    """One archive call's pure bytes; never reused by a native observation."""

    def __init__(self, capture_clock):
        fields(capture_clock, {"boot_id", "monotonic_ns"}, "captured_package_clock")
        need(
            type(capture_clock["boot_id"]) is str and type(capture_clock["monotonic_ns"]) is int,
            "captured_package_clock",
        )
        self._clock = (capture_clock["boot_id"], capture_clock["monotonic_ns"])
        self._current, self._active = None, False
        self._reader = Reader(self._read)
        self._results = {}

    def bind_clock(self, capture_clock):
        fields(capture_clock, {"boot_id", "monotonic_ns"}, "captured_package_clock")
        need(
            type(capture_clock["monotonic_ns"]) is int
            and (capture_clock["boot_id"], capture_clock["monotonic_ns"]) == self._clock,
            "captured_package_clock_changed",
        )

    def _read(self, reference):
        return self._current(reference)

    def read_opaque_reference(self, reference):
        return opaque_reader(self._current)(reference)

    def read_runtime_capsule(self, reference):
        return opaque_reader(self._current, capsule=True)(reference)

    def _enter(self, read_reference):
        need(not self._active and callable(read_reference), "captured_package_reader")
        self._active, self._current = True, read_reference
        # Register every original reference and its typed reader capability in
        # this NEW Store/collector, and compare all captured bytes before reuse.
        try:
            self._reader.recheck()
        except BaseException:
            self._current, self._active = None, False
            raise

    def validate(self, package_ref, *, read_reference, required_scope, now_ns):
        self._enter(read_reference)
        try:
            now_ns = time.monotonic_ns() if now_ns is None else now_ns
            need(type(now_ns) is int, "captured_package_clock")
            package = self._reader.doc(package_ref)
            fields(package, PACKAGE_FIELDS, "package_fields")
            need(required_scope is None or type(required_scope) is str, "package_scope")
            need(type(package["scope"]) is str, "package_scope")
            _binding(package["binding"], now_ns=now_ns)
            key = (
                package_ref["path"],
                package_ref["sha256"],
                digest(package["binding"]),
                package["scope"],
                required_scope,
                now_ns,
                self._clock[0],
                self._clock[1],
            )
            if key not in self._results:
                value = _decode_package(
                    package_ref, self._reader, required_scope=required_scope, now_ns=now_ns
                )
                self._reader.recheck()
                self._results[key] = copy.deepcopy(value)
            return copy.deepcopy(self._results[key])
        finally:
            self._current, self._active = None, False

    def runtime_index(self, reference, *, binding, scope, read_reference):
        self._enter(read_reference)
        try:
            result = self._reader.index(reference, "runtime-index", binding, scope)
            self._reader.recheck()
            return result
        finally:
            self._current, self._active = None, False


def compile_policy(binding, scope, unit, inputs, source_package, *, read_reference):
    """Compile one new policy from an exact source package and explicit inputs.

    A source package supplies bytes, never a native permit. Runtime/startup,
    environment and effective-unit inputs must be measured independently.
    """
    need(unit in ALLOWED and scope in SCOPES, "compile_scope")
    fields(
        inputs, POLICY_FIELDS - {"schema", "scope", "contract", "binding", "unit"}, "compile_inputs"
    )
    reader = Reader(read_reference)
    package = _source_package(reader, source_package, binding, scope)
    need(package["revision"] == binding["candidate_manager_sha"], "compile_revision")
    for key in ("source_index_ref", "guard_ref", "native_ref"):
        need(inputs[key] == package[key], "compile_package_" + key)
    reader.index(package["source_index_ref"], "source-index", binding, scope)
    reader.raw(package["guard_ref"])
    reader.raw(package["native_ref"])
    reader.recheck()
    return {
        "schema": "autostop.j1.cold-profile-policy.v1",
        "scope": scope,
        "contract": CONTRACT,
        "binding": binding,
        "unit": unit,
        **inputs,
    }


def compile_policies(binding, scope, inputs, source_package, *, read_reference):
    """No inherited five-profile exemption: all six inputs are mandatory."""
    fields(inputs, ALLOWED, "compile_six_profiles")
    return {
        unit: compile_policy(
            binding, scope, unit, inputs[unit], source_package, read_reference=read_reference
        )
        for unit in ALLOWED
    }


def compile_package(
    package_path,
    *,
    scope,
    binding,
    predecessor_ref,
    source_package_ref,
    policies,
    evidence_refs,
    read_reference,
):
    """Compile a complete immutable package before publishing any selector.

    This checks exact bytes and contracts; a native observer must then validate
    them while the deployment/dispatch descriptors remain continuously held.
    The returned bytes are not an issuer grant or native acceptance.
    """
    fields(
        evidence_refs,
        {
            "closure_ref",
            "hold_ref",
            "retirement_ref",
            "quarantine_ref",
            "pending_ref",
            "kernel_ref",
            "lifecycle_ref",
            "original_hold_ref",
            "hold_checkpoint_ref",
        },
        "compile_evidence",
    )
    value = {
        "schema": SCHEMA,
        "scope": scope,
        "contract": CONTRACT,
        "binding": binding,
        "predecessor_ref": predecessor_ref,
        "source_package_ref": source_package_ref,
        "policies": policies,
        "permanent_denied_units": list(DENIED),
        **evidence_refs,
    }
    raw = io.canonical(value)
    ref = {"path": package_path, "sha256": io.sha(raw)}

    def read(reference):
        if reference["path"] == package_path:
            need(reference == ref, "compile_package_alias")
            return raw
        return read_reference(reference)

    read.read_opaque_reference = opaque_reader(read_reference)
    read.read_runtime_capsule = opaque_reader(read_reference, capsule=True)
    validate_package(ref, read_reference=read, required_scope=scope)
    return raw


def profile_input_template():
    """Versioned input field names; None is deliberately invalid in a policy."""
    return {
        unit: {
            key: None
            for key in sorted(POLICY_FIELDS - {"schema", "scope", "contract", "binding", "unit"})
        }
        for unit in ALLOWED
    }


def _source_package(reader, source_ref, binding, scope, *, predecessor_ref=None):
    production = scope == "production_native"
    source = reader.bound(
        source_ref,
        "source-package",
        binding,
        scope,
        {"revision", "source_index_ref", "guard_ref", "native_ref"}
        | ({"producer_inputs_ref", "producer_receipt_ref"} if production else set()),
        version=2 if production else 1,
    )
    need(source["revision"] == binding["candidate_manager_sha"], "source_revision")
    for key in ("source_index_ref", "guard_ref", "native_ref"):
        reference(source[key])
    if production:
        inputs = reader.bound(
            source["producer_inputs_ref"], "producer-inputs", binding, scope, PRODUCER_INPUT_FIELDS
        )
        receipt = reader.bound(
            source["producer_receipt_ref"],
            "producer-receipt",
            binding,
            scope,
            PRODUCER_RECEIPT_REFS,
        )
        if predecessor_ref is not None:
            need(inputs["predecessor_ref"] == predecessor_ref, "producer_predecessor")
        for ref in (
            inputs["origin_ref"],
            inputs["predecessor_ref"],
            *(receipt[key] for key in sorted(PRODUCER_RECEIPT_REFS)),
        ):
            reader.raw(ref)
        request = reader.doc(receipt["request_ref"])
        fields(request, PRODUCER_REQUEST_FIELDS, "producer_request_fields")
        need(
            type(request) is dict
            and request.get("schema") == "autostop.j1.cold-controller-request.v1"
            and request.get("scope") == "bounded_cold_prepare"
            and request.get("old_writer_allowed") is False
            and request.get("inputs_ref") == source["producer_inputs_ref"]
            and request.get("attempt_id") == binding["attempt_id"]
            and request.get("boot_id") == binding["boot_id"]
            and request.get("owner_generation") == binding["generation"]
            and request.get("candidate_manager_sha") == binding["candidate_manager_sha"],
            "producer_request_binding",
        )
        _producer_source(request, source, binding)
        # The source/CI verifier is executed by preparation, so its installed
        # bytes belong to every fresh package observation and inert restore.
        reader.raw(
            {
                "path": "/usr/local/libexec/autostop-crm-forward-cold/v1/j1_manager_transition_source.py",
                "sha256": request["source"]["consumers"]["scripts/j1_manager_transition_source.py"],
            }
        )
    return source


def _producer_source(request, source, binding):
    selected, candidate = request.get("source"), request.get("candidate")
    fields(selected, {"revision", "tree", "consumers"}, "producer_request_source")
    fields(
        candidate,
        {"revision", "tree", "image", "consumer_sha256"},
        "producer_request_candidate",
    )
    need(
        selected["revision"] == candidate["revision"] == binding["candidate_crm_sha"]
        and selected["tree"] == candidate["tree"]
        and type(selected["tree"]) is str
        and SHA1.fullmatch(selected["tree"])
        and type(selected["consumers"]) is dict
        and 8 <= len(selected["consumers"]) <= 80
        and all(
            type(path) is str
            and re.fullmatch(r"scripts/j1_(?:forward|manager)_[a-z0-9_]+\.py", path)
            and type(sha) is str
            and HEX.fullmatch(sha)
            for path, sha in selected["consumers"].items()
        )
        and candidate["consumer_sha256"] == digest(selected["consumers"]),
        "producer_source_identity",
    )
    consumers = selected["consumers"]
    names = ("authority", "native", "guard", "history", "hold")
    need(
        all("scripts/j1_forward_cold_" + name + ".py" in consumers for name in names),
        "producer_controller_closure",
    )
    need("scripts/j1_manager_transition_source.py" in consumers, "producer_source_helper_missing")
    need(
        binding["consumer_sha256"]
        == digest({name: consumers["scripts/j1_forward_cold_" + name + ".py"] for name in names})
        and all(
            source[name + "_ref"]["sha256"] == consumers["scripts/j1_forward_cold_" + name + ".py"]
            for name in ("guard", "native")
        ),
        "producer_controller_binding",
    )


def _policies(package, reader):
    binding, scope = package["binding"], package["scope"]
    need(
        type(package["policies"]) is dict and set(package["policies"]) == set(ALLOWED),
        "six_policy_scope",
    )
    source = _source_package(
        reader,
        package["source_package_ref"],
        binding,
        scope,
        predecessor_ref=package["predecessor_ref"],
    )
    need(source["revision"] == binding["candidate_manager_sha"], "source_revision")
    reader.index(source["source_index_ref"], "source-index", binding, scope)
    for key in ("guard_ref", "native_ref"):
        reader.raw(source[key])
    policies = {}
    for unit in ALLOWED:
        policy = reader.doc(package["policies"][unit])
        fields(policy, POLICY_FIELDS, "profile_fields")
        need(
            policy["schema"] == "autostop.j1.cold-profile-policy.v1"
            and policy["contract"] == CONTRACT
            and policy["scope"] == scope
            and policy["binding"] == binding
            and policy["unit"] == unit,
            "profile_binding",
        )
        for key in ("source_index_ref", "guard_ref", "native_ref"):
            need(policy[key] == source[key], "profile_source_package")
        reader.raw(policy["interpreter_ref"], opaque=True)
        for key in ("runtime", "startup"):
            reader.index(policy[key + "_index_ref"], key + "-index", binding, scope)
        environment = reader.bound(
            policy["environment_ref"], "environment", binding, scope, {"unit", "values"}
        )
        need(
            environment["unit"] == unit
            and type(environment["values"]) is dict
            and all(
                type(key) is str
                and key
                and "=" not in key
                and "\0" not in key
                and type(value) is str
                and "\0" not in value
                for key, value in environment["values"].items()
            ),
            "environment_values",
        )
        unit_doc = reader.bound(
            policy["unit_ref"], "unit", binding, scope, {"unit", "properties", "files"}
        )
        need(
            unit_doc["unit"] == unit
            and type(unit_doc["properties"]) is dict
            and type(unit_doc["files"]) is list
            and unit_doc["files"],
            "unit_document",
        )
        for row in unit_doc["files"]:
            reader.raw(row)
        need(
            type(policy["source_root"]) is str
            and policy["source_root"].startswith("/")
            and type(policy["cwd"]) is str
            and policy["cwd"].startswith("/"),
            "profile_paths",
        )
        need(
            type(policy["argv"]) is list
            and policy["argv"]
            and all(type(arg) is str and arg and "\0" not in arg for arg in policy["argv"])
            and policy["argv"][0] == policy["interpreter_ref"]["path"],
            "profile_argv",
        )
        credentials = fields(policy["credentials"], {"uid", "gid", "groups"}, "credentials")
        need(
            all(type(credentials[key]) is int and credentials[key] >= 0 for key in ("uid", "gid"))
            and type(credentials["groups"]) is list
            and all(type(group) is int and group >= 0 for group in credentials["groups"]),
            "credential_values",
        )
        policies[unit] = policy
    return policies


def validate_package(package_ref, *, read_reference=None, required_scope=None, now_ns=None):
    """Pure artifact check. Call native.verify_package before using a dispatch plan.

    ``required_scope='production_native'`` strictly rejects fixture documents;
    changing a scope field does not replace the independent native verification.
    """
    view = getattr(getattr(read_reference, "__self__", None), "_captured_packages", None)
    if type(view) is _CapturedPackages:
        return view.validate(
            package_ref, read_reference=read_reference, required_scope=required_scope, now_ns=now_ns
        )
    reader = Reader(io.referenced if read_reference is None else read_reference)
    result = _decode_package(package_ref, reader, required_scope=required_scope, now_ns=now_ns)
    reader.recheck()
    return result


def _decode_package(package_ref, reader, *, required_scope=None, now_ns=None):
    """Decode one observation's captured bytes; its owner must recheck at exit."""
    package = reader.doc(package_ref)
    fields(package, PACKAGE_FIELDS, "package_fields")
    scope, binding = package["scope"], package["binding"]
    need(
        package["schema"] == SCHEMA
        and package["contract"] == CONTRACT
        and scope in SCOPES
        and (required_scope is None or scope == required_scope),
        "package_scope",
    )
    _binding(binding, now_ns=time.monotonic_ns() if now_ns is None else now_ns)
    need(package["permanent_denied_units"] == list(DENIED), "permanent_denied_scope")
    # Preserved provenance determines complete retirement coverage, never a
    # current warm permit. Its original hold requires a fresh raw DB measure.
    from scripts import j1_forward_cold_history as historical

    history = historical.load(
        package["predecessor_ref"], read_reference=reader.raw, fixture=scope == "native_fixture"
    )
    need(
        package["original_hold_ref"] == history["anchor"]["original_hold_ref"],
        "original_hold_anchor",
    )
    hold_checkpoint = reader.bound(
        package["hold_checkpoint_ref"],
        "hold-checkpoint",
        binding,
        scope,
        {
            "origin_attempt_id",
            "original_hold_ref",
            "database_ref",
            "database_identity",
            "quiet_backup_ref",
            "measurement",
        },
    )
    need(
        hold_checkpoint["origin_attempt_id"] == history["anchor"]["origin_attempt_id"]
        and hold_checkpoint["original_hold_ref"] == package["original_hold_ref"],
        "hold_checkpoint_binding",
    )
    reader.raw(hold_checkpoint["database_ref"])
    reader.raw(hold_checkpoint["quiet_backup_ref"])
    policies = _policies(package, reader)
    closure = reader.bound(package["closure_ref"], "closure", binding, scope, {"barriers"})
    need(
        type(closure["barriers"]) is dict and set(closure["barriers"]) == set(UNITS),
        "eight_barrier_scope",
    )
    for row in closure["barriers"].values():
        reference(row)
    need(len({row["path"] for row in closure["barriers"].values()}) == 8, "barrier_alias")
    for row in closure["barriers"].values():
        need(reader.raw(row) == DENY_BYTES, "barrier_bytes")
    hold = reader.bound(
        package["hold_ref"],
        "hold",
        binding,
        scope,
        {"marker_ref", "dispatch_lock_ref", "controller", "descriptor"},
    )
    reader.raw(hold["marker_ref"])
    reader.raw(hold["dispatch_lock_ref"])
    need(type(hold["descriptor"]) is int and hold["descriptor"] > 2, "hold_descriptor")
    retirement = reader.bound(
        package["retirement_ref"],
        "retirement",
        binding,
        scope,
        {"origin_actor_scope_ref", "actors", "completed_ns"},
    )
    origin = reader.bound(
        retirement["origin_actor_scope_ref"],
        "actor-scope",
        binding,
        scope,
        {"actors", "containment_ref", "predecessor_scope_ref"},
    )
    reader.raw(origin["containment_ref"])
    need(
        type(origin["actors"]) is list
        and origin["actors"]
        and retirement["actors"] == origin["actors"],
        "retirement_complete_scope",
    )
    need(
        type(retirement["completed_ns"]) is int
        and binding["not_before_ns"] <= retirement["completed_ns"] < binding["deadline_ns"],
        "retirement_interval",
    )
    quarantine = reader.bound(
        package["quarantine_ref"],
        "quarantine",
        binding,
        scope,
        {
            "retirement_ref",
            "hold_ref",
            "endpoints",
            "pipe_inodes",
            "unparsed_ingress",
            "closed_population_ref",
        },
    )
    need(
        quarantine["retirement_ref"] == package["retirement_ref"]
        and quarantine["hold_ref"] == package["hold_ref"]
        and quarantine["unparsed_ingress"] == "retired_without_replay",
        "quarantine_binding",
    )
    reader.raw(quarantine["closed_population_ref"])
    need(
        type(quarantine["endpoints"]) is list
        and quarantine["endpoints"]
        and type(quarantine["pipe_inodes"]) is list,
        "quarantine_endpoints",
    )
    pending = _pending(reader, package, binding, scope)
    kernel_header = reader.doc(package["kernel_ref"])
    need(type(kernel_header) is dict, "kernel_document")
    kernel_version = 2 if kernel_header.get("schema") == "autostop.j1.cold-kernel.v2" else 1
    kernel = reader.bound(
        package["kernel_ref"],
        "kernel",
        binding,
        scope,
        {"actor", "unit", "policy_ref", "ready_ref", "cache_view_ref"}
        | (
            {"supervisor_actor", "preparation_ref", "empty_thread_ref"}
            if kernel_version == 2
            else set()
        ),
        version=kernel_version,
    )
    kernel_policy = reader.bound(
        kernel["policy_ref"],
        "kernel-policy",
        binding,
        scope,
        {
            "unit",
            "source_ref",
            "interpreter_ref",
            "runtime_index_ref",
            "startup_index_ref",
            "environment_ref",
            "unit_ref",
            "argv",
            "cwd",
            "credentials",
        }
        | (
            {
                "appserver_ref",
                "appserver_argv",
                "appserver_environment_ref",
                "protocol_index_ref",
                "rpc_ref",
            }
            if kernel_version == 2
            else set()
        ),
        version=kernel_version,
    )
    need(kernel_policy["unit"] == kernel["unit"], "kernel_policy_unit")
    if kernel_version == 2:
        protocol = reader.index(
            kernel_policy["protocol_index_ref"], "kernel-protocol-index", binding, scope
        )
        need(
            all(kernel_policy[key] in protocol["files"] for key in ("source_ref", "rpc_ref")),
            "kernel_source_not_accepted",
        )
        reader.raw(kernel_policy["appserver_ref"], opaque=True)
        app_environment = reader.bound(
            kernel_policy["appserver_environment_ref"],
            "environment",
            binding,
            scope,
            {"unit", "values"},
        )
        need(
            app_environment["unit"] == kernel["unit"] and type(app_environment["values"]) is dict,
            "kernel_appserver_environment",
        )
        reader.doc(kernel["preparation_ref"])
        reader.doc(kernel["empty_thread_ref"])
    else:
        source_package = reader.doc(package["source_package_ref"])
        source_index = reader.doc(source_package["source_index_ref"])
        need(kernel_policy["source_ref"] in source_index["files"], "kernel_source_not_accepted")
    reader.raw(kernel_policy["interpreter_ref"], opaque=True)
    for name in ("runtime", "startup"):
        reader.index(kernel_policy[name + "_index_ref"], name + "-index", binding, scope)
    environment = reader.bound(
        kernel_policy["environment_ref"], "environment", binding, scope, {"unit", "values"}
    )
    need(
        environment["unit"] == kernel["unit"] and type(environment["values"]) is dict,
        "kernel_environment",
    )
    unit_doc = reader.bound(
        kernel_policy["unit_ref"], "unit", binding, scope, {"unit", "properties", "files"}
    )
    need(
        unit_doc["unit"] == kernel["unit"]
        and type(unit_doc["files"]) is list
        and unit_doc["files"],
        "kernel_unit",
    )
    for ref in unit_doc["files"]:
        reader.raw(ref)
    reader.raw(kernel["ready_ref"])
    reader.raw(kernel["cache_view_ref"])
    lifecycle = reader.bound(
        package["lifecycle_ref"],
        "lifecycle",
        binding,
        scope,
        {"phase", "kernel_ref", "closure_ref", "hold_ref", "policies", "pending_ref"},
    )
    need(
        lifecycle["phase"] == "cold_quarantined"
        and lifecycle["kernel_ref"] == package["kernel_ref"]
        and lifecycle["closure_ref"] == package["closure_ref"]
        and lifecycle["hold_ref"] == package["hold_ref"]
        and lifecycle["policies"] == package["policies"]
        and lifecycle["pending_ref"] == package["pending_ref"],
        "lifecycle_binding",
    )
    return {
        "package": package,
        "policies": policies,
        "closure": closure,
        "hold": hold,
        "origin": origin,
        "retirement": retirement,
        "quarantine": quarantine,
        "pending": pending,
        "kernel": kernel,
        "kernel_policy": kernel_policy,
        "history": history,
        "hold_checkpoint": hold_checkpoint,
        "lifecycle": lifecycle,
    }


def fingerprint(value, *, outcome):
    fields(value, {"kind", "sha256"}, "pending_fingerprint_fields")
    need(
        type(value["kind"]) is str
        and value["kind"] in {"payload_sha256", "observed_record_sha256"}
        and type(value["sha256"]) is str
        and HEX.fullmatch(value["sha256"]),
        "pending_fingerprint",
    )
    need(
        value["kind"] != "observed_record_sha256" or outcome == "unknown",
        "record_fingerprint_is_not_positive_outcome",
    )
    return value


def _pending(reader, package, binding, scope):
    pending_header = reader.doc(package["pending_ref"])
    pending_version = (
        2
        if type(pending_header) is dict
        and pending_header.get("schema") == "autostop.j1.cold-pending-disposition.v2"
        else 1
    )
    need(scope != "production_native" or pending_version == 2, "production_pending_v2_required")
    pending = reader.bound(
        package["pending_ref"],
        "pending-disposition",
        binding,
        scope,
        {"quarantine_ref", "inventory_ref", "scope", "requests"},
        version=pending_version,
    )
    need(pending["quarantine_ref"] == package["quarantine_ref"], "pending_quarantine")
    reader.raw(pending["inventory_ref"])
    need(pending["scope"] == scope, "pending_scope")
    need(type(pending["requests"]) is list and len(pending["requests"]) <= 8192, "pending_requests")
    ids = []
    for row in pending["requests"]:
        identity_key = "fingerprint" if pending_version == 2 else "payload_sha256"
        fields(
            row,
            {"op_id", identity_key, "outcome", "evidence_refs", "retry"},
            "pending_request_fields",
        )
        if pending_version == 2:
            fingerprint(row["fingerprint"], outcome=row["outcome"])
        else:
            need(
                type(row["payload_sha256"]) is str and HEX.fullmatch(row["payload_sha256"]),
                "pending_request_payload",
            )
        need(
            operation_id(row["op_id"]),
            "pending_request_identity",
        )
        need(
            row["outcome"] in {"applied_readback", "not_applied_terminal", "unknown"}
            and row["retry"] == "forbidden"
            and type(row["evidence_refs"]) is list,
            "pending_no_implicit_replay",
        )
        need(row["outcome"] == "unknown" or row["evidence_refs"], "pending_positive_evidence")
        for ref in row["evidence_refs"]:
            reader.raw(ref)
        ids.append(row["op_id"])
    need(len(ids) == len(set(ids)), "pending_duplicate_identity")
    return pending
