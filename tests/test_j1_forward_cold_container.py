"""Exact cold image/env/mount selection and independent acceptance boundaries."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_container as container
from scripts import j1_forward_cold_host as host
from scripts import j1_forward_cold_http as http_contract
from scripts import j1_forward_io as io


def reference(name):
    return {"path": "/sealed/" + name, "sha256": "f" * 64}


def http_document(binding):
    document = {
        "schema": http_contract.SCHEMA,
        "scope": "production_native",
        **{
            name: binding[name]
            for name in (
                "attempt_id",
                "boot_id",
                "generation",
                "candidate_crm_sha",
                "candidate_manager_sha",
            )
        },
        "epoch_id": binding["generation"],
        "public_origin": http_contract.PUBLIC_ORIGIN,
    }
    document.update(http_contract.expected_prefixes(document))
    return document


def http_ref(binding):
    return {
        "path": f"/etc/autostop-manager-cold/{binding['generation']}/crm-embedded/http-ingress.json",
        "sha256": io.sha(io.canonical(http_document(binding))),
    }


def fixture():
    revision, generation = "1" * 40, "00000000-0000-4000-8000-000000000002"
    epoch_ref, package_ref = reference("epoch"), reference("package")
    binding = {
        "attempt_id": "00000000-0000-4000-8000-000000000003",
        "generation": generation,
        "boot_id": "00000000-0000-4000-8000-000000000004",
        "candidate_manager_sha": revision,
        "candidate_crm_sha": "2" * 40,
    }
    binding_ref = {
        "path": f"/etc/autostop-manager-cold/{generation}/crm-embedded/binding.json",
        "sha256": "e" * 64,
    }
    epoch = {
        "epoch_id": generation,
        "attempt_id": binding["attempt_id"],
        "package_ref": package_ref,
        "runtime_bindings": {"crm-embedded": binding_ref},
    }
    image = {
        "Id": "sha256:" + "3" * 64,
        "Config": {
            "Labels": {
                "org.opencontainers.image.revision": "2" * 40,
                "org.autostop.crm.tree": "4" * 40,
            },
            "User": "10001:10001",
            "WorkingDir": "/app",
            "Cmd": ["python", "scripts/container_entrypoint.py"],
            "Env": ["PATH=/usr/local/bin:/usr/bin", "PYTHONDONTWRITEBYTECODE=1"],
        },
    }
    previous = {
        "Id": "5" * 64,
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/opt/autostopcrm/data",
                "Destination": container.DATA,
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": "/old/source",
                "Destination": "/opt/AutostopManager",
                "RW": False,
            },
        ],
        "HostConfig": {
            "PortBindings": {"41831/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8001"}]}
        },
        "NetworkSettings": {"Networks": {"autostopcrm_default": {}, "autostop-store-agent": {}}},
    }
    model = {
        "services": {
            "autostopcrm": {
                "build": {"context": "/old/source"},
                "depends_on": {"searxng": {}},
                "env_file": [".env"],
                "environment": {
                    "AUTOSTOP_MANAGER_PATH": "/opt/AutostopManager",
                    "CRM_PRIVATE_TOKEN": "synthetic-only",
                },
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "networks": {"default": {}, "store": {}},
                "restart": "always",
            }
        },
        "networks": {
            "default": {"name": "autostopcrm_default"},
            "store": {"name": "autostop-store-agent"},
        },
    }
    spec = container.compile_spec(
        model,
        image,
        previous,
        epoch,
        {"binding": binding},
        epoch_ref=epoch_ref,
        package_ref=package_ref,
        http_ingress_ref=http_ref(binding),
        http_ingress=http_document(binding),
    )
    return spec, model, image, previous, epoch, binding


def inspection(spec):
    service = spec["compose"]["services"][container.SERVICE]
    return {
        "Id": "6" * 64,
        "Image": spec["candidate"]["image"],
        "RestartCount": 0,
        "Config": {
            "User": "10001:10001",
            "Env": [key + "=" + value for key, value in service["environment"].items()],
            "Labels": {
                "org.opencontainers.image.revision": spec["candidate"]["revision"],
                "org.autostop.crm.tree": spec["candidate"]["tree"],
                "com.docker.compose.project": "autostopcrm",
                "com.docker.compose.service": "autostopcrm",
            },
        },
        "HostConfig": {
            "RestartPolicy": {"Name": "no"},
            "ReadonlyRootfs": True,
            "Tmpfs": dict(row.split(":", 1) for row in service["tmpfs"]),
            "Init": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PortBindings": spec["preserved"]["ports"],
        },
        "State": {
            "Running": True,
            "Health": {"Status": "healthy"},
            "Pid": 1234,
            "StartedAt": "fixture-start",
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": row["source"],
                "Destination": row["destination"],
                "RW": row["rw"],
            }
            for row in spec["expected_mounts"]
        ],
        "NetworkSettings": {"Networks": {name: {} for name in spec["preserved"]["networks"]}},
    }


class ColdContainerTests(unittest.TestCase):
    def test_old_environment_cannot_inject_code_before_cold_source_validation(self):
        spec, model, image, previous, epoch, binding = fixture()
        for key in (
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "LD_AUDIT",
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "PYTHONUSERBASE",
            "PYTHONPLATLIBDIR",
            "PATH",
        ):
            changed = copy.deepcopy(model)
            changed["services"][container.SERVICE]["environment"][key] = "/mutable/old-code"
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(io.Refusal, "cold_crm_startup_environment_injection"),
            ):
                container.compile_spec(
                    changed,
                    image,
                    previous,
                    epoch,
                    {"binding": binding},
                    epoch_ref=spec["epoch_ref"],
                    package_ref=spec["package_ref"],
                    http_ingress_ref=http_ref(binding),
                    http_ingress=http_document(binding),
                )

    def test_compilation_replaces_old_manager_mounts_and_never_starts(self):
        spec, model, _image, _previous, _epoch, _binding = fixture()
        service = spec["compose"]["services"][container.SERVICE]
        self.assertEqual("always", model["services"][container.SERVICE]["restart"])
        self.assertEqual("no", service["restart"])
        self.assertEqual("0", service["environment"]["MINIMAL_KANBAN_AGENT_ENABLED"])
        self.assertNotIn("build", service)
        self.assertNotIn("depends_on", service)
        self.assertNotIn(
            "/opt/AutostopManager", [row["destination"] for row in spec["expected_mounts"]]
        )
        self.assertTrue(
            all(
                not row["rw"] for row in spec["expected_mounts"] if "dispatch" in row["destination"]
            )
        )
        self.assertEqual("synthetic-only", service["environment"]["CRM_PRIVATE_TOKEN"])

    def test_extra_mount_and_missing_seventh_binding_are_rejected(self):
        spec, model, image, previous, epoch, binding = fixture()
        for failure in ("extra-mount", "missing-role", "wrong-image"):
            candidate, old, configured = (
                copy.deepcopy(image),
                copy.deepcopy(previous),
                copy.deepcopy(epoch),
            )
            if failure == "extra-mount":
                old["Mounts"].append(
                    {"Type": "bind", "Source": "/private", "Destination": "/private", "RW": True}
                )
            elif failure == "missing-role":
                configured["runtime_bindings"].clear()
            else:
                candidate["Config"]["Labels"]["org.opencontainers.image.revision"] = "a" * 40
            with self.subTest(failure=failure), self.assertRaises(io.Refusal):
                container.compile_spec(
                    model,
                    candidate,
                    old,
                    configured,
                    {"binding": binding},
                    epoch_ref=spec["epoch_ref"],
                    package_ref=spec["package_ref"],
                    http_ingress_ref=http_ref(binding),
                    http_ingress=http_document(binding),
                )

    def test_actual_readback_rejects_drift_and_automatic_restart(self):
        spec, *_ = fixture()
        observed = inspection(spec)
        self.assertEqual(1234, container.validate_inspection(spec, observed)["init_pid"])
        mutations = [
            lambda value: value.update(RestartCount=1),
            lambda value: value.update(Id=spec["previous_container_id"]),
            lambda value: value["HostConfig"].update(RestartPolicy={"Name": "always"}),
            lambda value: value["HostConfig"].update(ReadonlyRootfs=False),
            lambda value: value["HostConfig"]["Tmpfs"].pop("/tmp"),
            lambda value: value["Mounts"][0].update(RW=not value["Mounts"][0]["RW"]),
            lambda value: value["Config"]["Env"].append("UNPLANNED=1"),
            lambda value: value["State"].update(Running=False),
        ]
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(observed)
            mutate(changed)
            with self.subTest(index=index), self.assertRaises(io.Refusal):
                container.validate_inspection(spec, changed)

    def test_unknown_submission_never_retries(self):
        spec, *_ = fixture()
        run = Mock(side_effect=TimeoutError("lost compose reply"))
        adapter = container.Adapter(run)
        adapter.inspect = Mock(return_value={"Id": spec["previous_container_id"]})
        spec_ref, compose_ref = reference("spec"), reference("compose")
        with patch.object(
            io, "referenced", side_effect=[io.canonical(spec), io.canonical(spec["compose"])]
        ):
            with self.assertRaises(TimeoutError):
                adapter.replace_once(spec_ref, compose_ref)
        self.assertEqual(1, run.call_count)
        self.assertIn("--force-recreate", run.call_args.args[0])

    def test_acceptance_requires_two_identical_independent_process_observations(self):
        spec, *_ = fixture()
        adapter = object()
        first = {"container_id": "6" * 64, "main_pid": 100, "start_ticks": 200}
        with patch.object(host, "observe", side_effect=[first, {**first, "start_ticks": 201}]):
            with self.assertRaises(io.Refusal):
                host.acceptance(adapter, spec, reference("spec"))
        with patch.object(host, "observe", side_effect=[first, first]):
            receipt = host.acceptance(adapter, spec, reference("spec"))
        self.assertEqual(2, receipt["independent_observations"])
        self.assertEqual("crm-embedded", receipt["role"])
        self.assertFalse(receipt["business_acceptance"])

    def test_cold_host_cannot_use_warm_rpc_or_baseline(self):
        candidate = host.ColdHost(
            {
                "cold_authority_package_ref": reference("package"),
                "dispatch_epoch_ref": reference("epoch"),
            },
            __import__("pathlib").Path("/private"),
        )
        for method in (
            candidate.capture_baseline,
            candidate.source,
            candidate.manager,
            candidate.authority,
            candidate.start_manager_writers,
        ):
            with self.assertRaisesRegex(io.Refusal, "warm_operation_forbidden"):
                method()
        with self.assertRaisesRegex(io.Refusal, "configuration_required"):
            candidate.replace_crm({})


if __name__ == "__main__":
    unittest.main()
