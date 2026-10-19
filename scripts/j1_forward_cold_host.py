"""Native CRM container boundary for an explicitly configured cold attempt."""

from __future__ import annotations

import os
import time
from pathlib import Path

from scripts import j1_forward_cold_container as containers
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
from scripts.j1_forward_host import BASE_ENV, DOCKER, Host, MixedHost


def _read(path, maximum=2 * 1024**2):
    with Path(path).open("rb") as stream:
        value = stream.read(maximum + 1)
    io.require(len(value) <= maximum, "cold_crm_proc_limit")
    return value


def _process(instance, spec):
    init_pid = instance["init_pid"]
    root = Path("/proc") / str(init_pid)
    init_birth = native.birth(init_pid)
    children = _read(root / f"task/{init_pid}/children", 8192).split()
    io.require(len(children) == 1 and children[0].isdigit(), "cold_crm_main_process_ambiguous")
    pid = int(children[0])
    process = Path("/proc") / str(pid)
    birth = native.birth(pid)
    status = dict(line.split(":", 1) for line in _read(process / "status").decode().splitlines())
    io.require(
        int(status["PPid"]) == init_pid
        and set(status["Uid"].split()) == {"10001"}
        and set(status["Gid"].split()) == {"10001"}
        and _read(process / "cgroup") == _read(root / "cgroup")
        and os.readlink(process / "ns/mnt") == os.readlink(root / "ns/mnt")
        and os.readlink(process / "cwd") == "/app"
        and _read(process / "cmdline") == b"/usr/local/bin/python\0/app/main_mcp.py\0",
        "cold_crm_main_process_identity",
    )
    environment = native._environment(_read(process / "environ"))
    expected = spec["compose"]["services"][containers.SERVICE]["environment"]
    for key in (
        containers.BINDING_ENV,
        containers.HASH_ENV,
        "AUTOSTOP_MANAGER_PATH",
        "AUTOSTOP_MANAGER_ENV_FILE",
        "AUTOSTOP_MANAGER_DB",
        "AUTOSTOP_AUTOMATION_CONTROL_SOCKET",
        "MINIMAL_KANBAN_AGENT_ENABLED",
    ):
        io.require(environment.get(key) == expected[key], "cold_crm_main_binding_environment")
    # Read the actual process mount namespace, rather than a new docker-exec
    # Python process which could import a different Manager after startup.
    source = expected["AUTOSTOP_MANAGER_PATH"]
    source_revision = _read(process / "root" / source.lstrip("/") / "REVISION", 128)
    manifest = _read(process / "root" / expected[containers.BINDING_ENV].lstrip("/"), 64 * 1024)
    io.require(
        source_revision == (spec["manager_source_revision"] + "\n").encode()
        and io.sha(manifest) == spec["binding_ref"]["sha256"],
        "cold_crm_actual_source_binding",
    )
    executable = process / "exe"
    executable_sha256 = io.sha(_read(executable, 64 * 1024**2))
    io.require(
        native.birth(pid) == birth
        and native.birth(init_pid) == init_birth
        and _read(root / f"task/{init_pid}/children", 8192).split() == children,
        "cold_crm_process_changed",
    )
    return {
        "main_pid": pid,
        "start_ticks": birth,
        "init_start_ticks": init_birth,
        "executable_sha256": executable_sha256,
    }


def observe(adapter, spec):
    before = adapter.inspect(spec)
    instance = containers.validate_inspection(spec, before)
    process = _process(instance, spec)
    after = adapter.inspect(spec)
    io.require(containers.validate_inspection(spec, after) == instance, "cold_crm_inspect_raced")
    return {**instance, **process}


def _validated_inputs(spec_ref, epoch_ref, package_ref):
    from scripts import j1_forward_cold_epoch as epochs

    spec = containers.validate_spec(io.document(io.referenced(spec_ref)))
    provider = native.NativeProvider()
    verified = native.verify_package(
        package_ref, provider=provider, required_scope="production_native"
    )
    epoch = epochs.read_epoch(epoch_ref, provider)
    epochs.verify(epoch, verified["package"], provider)
    binding = verified["package"]["binding"]
    io.require(
        spec["epoch_ref"] == epoch_ref
        and spec["package_ref"] == package_ref
        and spec["attempt_id"] == binding["attempt_id"]
        and spec["candidate"]["revision"] == binding["candidate_crm_sha"]
        and spec["manager_source_revision"] == binding["candidate_manager_sha"]
        and spec["binding_ref"] == epoch["runtime_bindings"]["crm-embedded"],
        "cold_crm_spec_context",
    )
    return spec


def verify_crm(reference, *, epoch_ref, package_ref):
    """A stored receipt selects exact inputs; it cannot replace fresh readback."""
    import subprocess

    previous = io.document(io.referenced(reference))
    spec_ref = previous["spec_ref"]
    spec = _validated_inputs(spec_ref, epoch_ref, package_ref)

    def run(arguments, *, label, timeout=30):
        io.require(arguments[: len(DOCKER)] == DOCKER, "cold_crm_observer_command")
        result = subprocess.run(
            arguments, env=BASE_ENV, capture_output=True, timeout=timeout, check=False
        )
        io.require(
            result.returncode == 0 and len(result.stdout) <= 4 * 1024**2, "cold_crm_observer_failed"
        )
        return result.stdout

    fresh = acceptance(containers.Adapter(run), spec, spec_ref)
    io.require(fresh == previous, "cold_crm_accepted_instance_changed")
    return fresh


def acceptance(adapter, spec, spec_ref):
    first, second = observe(adapter, spec), observe(adapter, spec)
    io.require(first == second, "cold_crm_independent_observations_disagree")
    return {
        "schema": "autostop.j1.cold-crm-runtime-acceptance.v1",
        "role": "crm-embedded",
        "epoch_ref": spec["epoch_ref"],
        "package_ref": spec["package_ref"],
        "spec_ref": spec_ref,
        "instance": first,
        "env_sha256": spec["expected_env_sha256"],
        "mounts_sha256": io.sha(io.canonical(spec["expected_mounts"])),
        "independent_observations": 2,
        "manager_source_revision": spec["manager_source_revision"],
        "binding_ref": spec["binding_ref"],
        "business_acceptance": False,
    }


class ColdHost(MixedHost):
    def __init__(self, native, directory, **kwargs):
        io.require(
            set(native) == {"cold_authority_package_ref", "dispatch_epoch_ref"},
            "cold_host_native_schema",
        )
        Host.__init__(self, native, directory, **kwargs)

    def _warm_forbidden(self, *args, **kwargs):
        raise io.Refusal("cold_host_warm_operation_forbidden")

    # Do not silently reuse a warm RPC, accepted-old-PID baseline or duty helper.
    capture_baseline = _warm_forbidden
    _baseline = _warm_forbidden
    manager = _warm_forbidden
    validate_manager = _warm_forbidden
    authority = _warm_forbidden
    start_manager_writers = _warm_forbidden
    source = _warm_forbidden

    def probes(self, candidate, attempt_id, *, held):
        io.require(held is True, "cold_crm_closed_probe_requires_hold")
        return self.readonly_probes(candidate, attempt_id, phase="closed")

    def readonly_probes(self, candidate, attempt_id, *, phase):
        from scripts import j1_forward_cold_runtime as runtime

        io.require(hasattr(self, "cold_spec"), "cold_crm_configuration_required")
        selected = {key: candidate[key] for key in ("image", "revision", "tree")}
        io.require(selected == self.cold_spec["candidate"], "cold_crm_probe_candidate")
        expected = io.document(io.read_protected(self.directory / "cold-crm-started.json"))
        adapter = containers.Adapter(self.run)
        io.require(
            acceptance(adapter, self.cold_spec, self.cold_spec_ref) == expected,
            "cold_crm_probe_instance_changed",
        )
        report = io.document(
            self.run(
                DOCKER
                + [
                    "exec",
                    "autostopcrm",
                    "/usr/local/bin/python",
                    "-B",
                    "scripts/j1_forward_cold_runtime.py",
                    "--candidate",
                    io.canonical(selected).decode(),
                    "--attempt-id",
                    attempt_id,
                    "--phase",
                    phase,
                ],
                label="cold-crm-readonly-" + phase,
                timeout=90,
            )
        )
        runtime.validate_report(report, candidate=selected, attempt=attempt_id, phase=phase)
        io.require(
            acceptance(adapter, self.cold_spec, self.cold_spec_ref) == expected,
            "cold_crm_probe_instance_changed",
        )
        return report

    def prepare_spec(self, candidate, *, epoch_ref, package_ref):
        package = io.document(io.referenced(package_ref))
        epoch = io.document(io.referenced(epoch_ref))
        image = io.document(
            self.run(DOCKER + ["image", "inspect", candidate["image"]], label="cold-crm-image")
        )
        previous = io.document(
            self.run(DOCKER + ["inspect", "autostopcrm"], label="cold-crm-previous")
        )
        io.require(
            type(image) is list
            and len(image) == 1
            and type(previous) is list
            and len(previous) == 1,
            "cold_crm_prepare_identity",
        )
        model = io.document(
            self.run(DOCKER + ["compose", "config", "--format", "json"], label="cold-crm-model")
        )
        spec = containers.compile_spec(
            model,
            image[0],
            previous[0],
            epoch,
            package,
            epoch_ref=epoch_ref,
            package_ref=package_ref,
        )
        io.require(
            spec["candidate"] == {key: candidate[key] for key in ("image", "revision", "tree")},
            "cold_crm_prepare_candidate",
        )
        spec_ref, _compose_ref = containers.write_spec(self.directory, spec)
        return spec_ref

    def configure_cold(self, epoch_ref, package_ref, spec_ref):
        io.require(
            self.native
            == {"cold_authority_package_ref": package_ref, "dispatch_epoch_ref": epoch_ref},
            "cold_crm_host_context",
        )
        self.cold_spec = _validated_inputs(spec_ref, epoch_ref, package_ref)
        self.cold_spec_ref = spec_ref

    def replace_crm(self, candidate):
        io.require(hasattr(self, "cold_spec"), "cold_crm_configuration_required")
        io.require(
            {key: candidate[key] for key in ("image", "revision", "tree")}
            == self.cold_spec["candidate"],
            "cold_crm_candidate_changed",
        )
        # The caller has consumed parent E before this method. A timeout or lost
        # reply propagates; it never causes a second submission here.
        compose_ref = io.reference(self.directory / "cold-crm-compose.json")
        adapter = containers.Adapter(self.run)
        adapter.replace_once(self.cold_spec_ref, compose_ref)
        deadline = min(self.deadline, time.monotonic() + 80)
        while True:
            try:
                receipt = acceptance(adapter, self.cold_spec, self.cold_spec_ref)
                break
            except (io.Refusal, FileNotFoundError, ProcessLookupError):
                io.require(time.monotonic() < deadline, "cold_crm_startup_unconfirmed")
                time.sleep(0.25)
        return io.write_new(self.directory / "cold-crm-started.json", receipt)
