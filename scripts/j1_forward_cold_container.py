"""Exact CRM container model for the seventh cold Manager runtime binding.

Compilation is pure. Submission requires the caller's already consumed parent E
and never retries. Native observation is independent of the submitted command.
Resolved Compose bytes may contain secrets and remain private controller inputs.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path, PurePosixPath

from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.cold-crm-container-spec.v2"
ROLE = "crm-embedded"
BINDING_ENV = "AUTOSTOP_COLD_RUNTIME_BINDING"
HASH_ENV = "AUTOSTOP_COLD_RUNTIME_BINDING_SHA256"
HTTP_ENV = "AUTOSTOP_COLD_HTTP_INGRESS_CONFIG"
HTTP_HASH_ENV = HTTP_ENV + "_SHA256"
DATA = "/home/autostop/.minimal-kanban"
SERVICE = "autostopcrm"


def environment(rows):
    io.require(type(rows) is list and all(type(row) is str for row in rows), "cold_crm_env")
    pairs = [row.split("=", 1) for row in rows]
    io.require(
        all(len(row) == 2 and row[0] for row in pairs)
        and len({row[0] for row in pairs}) == len(pairs),
        "cold_crm_env_duplicate",
    )
    return dict(pairs)


def mounts(rows):
    io.require(type(rows) is list and len(rows) <= 32, "cold_crm_mount_count")
    result = []
    for row in rows:
        if row.get("Type") == "tmpfs":
            continue  # Independently checked against HostConfig.Tmpfs below.
        io.require(row.get("Type") == "bind", "cold_crm_bind_required")
        result.append({"source": row["Source"], "destination": row["Destination"], "rw": row["RW"]})
    io.require(
        len({row["destination"] for row in result}) == len(result), "cold_crm_duplicate_mount"
    )
    return sorted(result, key=lambda row: row["destination"])


def _tmpfs(rows):
    result = {}
    for path, options in rows.items():
        normalized = []
        for value in options.split(","):
            if value.startswith("size="):
                amount = value[5:].lower()
                factor = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(amount[-1:], 1)
                amount = amount[:-1] if factor != 1 else amount
                value = "size=" + str(int(amount) * factor)
            normalized.append(value)
        result[path] = sorted(normalized)
    return result


def compile_spec(
    model,
    image,
    previous,
    epoch,
    package,
    *,
    epoch_ref,
    package_ref,
    http_ingress_ref=None,
    http_ingress=None,
):
    """Bind a resolved model and exact image to immutable epoch/package inputs."""
    binding = package["binding"]
    generation, revision = epoch["epoch_id"], binding["candidate_manager_sha"]
    from scripts.j1_forward_cold_http import parse_document

    ingress = parse_document(
        http_ingress,
        {
            **binding,
            "role": ROLE,
            "epoch_id": generation,
            "source_revision": revision,
        },
    )
    io.require(
        http_ingress_ref
        == {
            "path": f"/etc/autostop-manager-cold/{generation}/crm-embedded/http-ingress.json",
            "sha256": ingress.sha256,
        }
        and ingress.document["candidate_crm_sha"] == binding["candidate_crm_sha"],
        "cold_crm_http_binding",
    )
    io.require(
        epoch["package_ref"] == package_ref
        and epoch["attempt_id"] == binding["attempt_id"]
        and generation == binding["generation"]
        and ROLE in epoch["runtime_bindings"],
        "cold_crm_epoch",
    )
    config, labels = image["Config"], image["Config"].get("Labels") or {}
    io.require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"])
        and labels.get("org.opencontainers.image.revision") == binding["candidate_crm_sha"]
        and re.fullmatch(r"[0-9a-f]{40}", labels.get("org.autostop.crm.tree", ""))
        and config["User"] == "10001:10001"
        and config["WorkingDir"] == "/app"
        and config["Cmd"] == ["python", "scripts/container_entrypoint.py"]
        and not config.get("Entrypoint")
        and not config.get("Volumes"),
        "cold_crm_image_contract",
    )
    service = copy.deepcopy(model["services"][SERVICE])
    io.require(
        set(service)
        <= {
            "build",
            "image",
            "container_name",
            "init",
            "restart",
            "security_opt",
            "cap_drop",
            "pids_limit",
            "mem_limit",
            "cpus",
            "logging",
            "depends_on",
            "extra_hosts",
            "env_file",
            "ports",
            "environment",
            "volumes",
            "networks",
            "healthcheck",
            "user",
            "labels",
            "stop_grace_period",
            "shm_size",
        },
        "cold_crm_service_fields",
    )
    current_mounts = mounts(previous["Mounts"])
    data_mounts = [row for row in current_mounts if row["destination"] == DATA]
    legacy_source = service["environment"].get("AUTOSTOP_MANAGER_PATH")
    io.require(
        len(data_mounts) == 1
        and data_mounts[0]["rw"] is True
        and type(legacy_source) is str
        and all(
            row["destination"]
            in {DATA, legacy_source, legacy_source + "/data", "/run/autostop-manager-automation"}
            for row in current_mounts
        ),
        "cold_crm_previous_mount_scope",
    )
    source = f"/opt/autostop-manager-cold-releases/{revision}/source"
    etc = f"/etc/autostop-manager-cold/{generation}"
    state = f"/var/lib/autostop-manager-cold/{generation}"
    run = f"/run/autostop-manager-cold/{generation}"
    expected_mounts = data_mounts + [
        {"source": path, "destination": path, "rw": writable}
        for path, writable in (
            (source, False),
            (etc + "/crm-embedded", False),
            (etc + "/dispatch", False),
            (state + "/crm-embedded", True),
            (state + "/dispatch", False),
            (run + "/scheduler", False),
        )
    ]
    expected_mounts.sort(key=lambda row: row["destination"])
    binding_ref = epoch["runtime_bindings"][ROLE]
    io.require(binding_ref["path"] == etc + "/crm-embedded/binding.json", "cold_crm_binding_path")
    image_environment = environment(config["Env"])
    values = {**image_environment, **service["environment"]}
    io.require(
        not {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONPLATLIBDIR"}
        & values.keys()
        and not any(key.startswith("LD_") for key in values)
        and values.get("PATH") == image_environment.get("PATH"),
        "cold_crm_startup_environment_injection",
    )
    values.update(
        {
            "AUTOSTOP_MANAGER_PATH": source,
            BINDING_ENV: binding_ref["path"],
            HASH_ENV: binding_ref["sha256"],
            HTTP_ENV: http_ingress_ref["path"],
            HTTP_HASH_ENV: http_ingress_ref["sha256"],
            "MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": ingress.base_url("mcp"),
            "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL": ingress.base_url("mcp") + "/mcp",
            "MINIMAL_KANBAN_MCP_PATH": "/mcp",
            "MINIMAL_KANBAN_BOARD_API_URL": "http://127.0.0.1:41731" + ingress.prefix("board"),
            "MINIMAL_KANBAN_API_BASE_URL": "http://127.0.0.1:41731" + ingress.prefix("board"),
            "MINIMAL_KANBAN_AGENT_BOARD_API_URL": "http://127.0.0.1:41731"
            + ingress.prefix("board"),
            "AUTOSTOP_MANAGER_ENV_FILE": etc + "/crm-embedded/runtime.env",
            "AUTOSTOP_MANAGER_DB": state + "/crm-embedded/autostop_manager.sqlite3",
            "AUTOSTOP_AUTOMATION_CONTROL_SOCKET": run + "/scheduler/control.sock",
            # The embedded autonomous agent must not inherit old queued work.
            "MINIMAL_KANBAN_AGENT_ENABLED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    io.require(
        all(type(k) is str and type(v) is str and "\x00" not in k + v for k, v in values.items()),
        "cold_crm_environment_values",
    )
    for field in ("build", "depends_on", "env_file"):
        service.pop(field, None)
    service.update(
        image=image["Id"],
        container_name=SERVICE,
        user="10001:10001",
        init=True,
        restart="no",
        read_only=True,
        tmpfs=[
            "/tmp:rw,nosuid,nodev,size=256m",
            "/home/autostop/.cache:rw,nosuid,nodev,size=128m",
            "/home/autostop/.config:rw,nosuid,nodev,size=16m",
        ],
        environment=values,
        volumes=[
            {
                "type": "bind",
                "source": row["source"],
                "target": row["destination"],
                "read_only": not row["rw"],
                "bind": {"create_host_path": False},
            }
            for row in expected_mounts
        ],
    )
    io.require(
        "no-new-privileges:true" in service["security_opt"] and service["cap_drop"] == ["ALL"],
        "cold_crm_container_security",
    )
    networks = {}
    for key in service["networks"]:
        name = model["networks"][key]["name"]
        networks[key] = {"name": name, "external": True}
    io.require(
        sorted(row["name"] for row in networks.values())
        == sorted(previous["NetworkSettings"]["Networks"]),
        "cold_crm_networks_changed",
    )
    compose = {"name": "autostopcrm", "services": {SERVICE: service}, "networks": networks}
    spec = {
        "schema": SCHEMA,
        "role": ROLE,
        "attempt_id": binding["attempt_id"],
        "epoch_ref": epoch_ref,
        "package_ref": package_ref,
        "binding_ref": binding_ref,
        "http_ingress_ref": http_ingress_ref,
        "manager_source_revision": revision,
        "candidate": {
            "image": image["Id"],
            "revision": binding["candidate_crm_sha"],
            "tree": labels["org.autostop.crm.tree"],
        },
        "previous_container_id": previous["Id"],
        "compose": compose,
        "expected_mounts": expected_mounts,
        "expected_env_sha256": io.sha(io.canonical(values)),
        "preserved": {
            "ports": previous["HostConfig"]["PortBindings"],
            "networks": sorted(previous["NetworkSettings"]["Networks"]),
        },
    }
    validate_spec(spec)
    return spec


def validate_spec(spec):
    io.require(
        type(spec) is dict
        and set(spec)
        == {
            "schema",
            "role",
            "attempt_id",
            "epoch_ref",
            "package_ref",
            "binding_ref",
            "http_ingress_ref",
            "manager_source_revision",
            "candidate",
            "previous_container_id",
            "compose",
            "expected_mounts",
            "expected_env_sha256",
            "preserved",
        }
        and spec["schema"] == SCHEMA
        and spec["role"] == ROLE,
        "cold_crm_spec_shape",
    )
    service = spec["compose"]["services"][SERVICE]
    io.require(
        service["image"] == spec["candidate"]["image"]
        and service["restart"] == "no"
        and service["read_only"] is True
        and service["init"] is True
        and service["user"] == "10001:10001"
        and io.sha(io.canonical(service["environment"])) == spec["expected_env_sha256"]
        and service["environment"][BINDING_ENV] == spec["binding_ref"]["path"]
        and service["environment"][HASH_ENV] == spec["binding_ref"]["sha256"],
        "cold_crm_spec_inconsistent",
    )
    io.require(
        service["environment"][HTTP_ENV] == spec["http_ingress_ref"]["path"]
        and service["environment"][HTTP_HASH_ENV] == spec["http_ingress_ref"]["sha256"]
        and spec["http_ingress_ref"]["path"]
        == str(PurePosixPath(spec["binding_ref"]["path"]).with_name("http-ingress.json")),
        "cold_crm_http_spec_inconsistent",
    )
    return spec


def validate_inspection(spec, observed):
    validate_spec(spec)
    labels = observed["Config"].get("Labels") or {}
    host, state = observed["HostConfig"], observed["State"]
    expected_tmpfs = dict(
        row.split(":", 1) for row in spec["compose"]["services"][SERVICE]["tmpfs"]
    )
    io.require(
        observed["Id"] != spec["previous_container_id"]
        and re.fullmatch(r"[0-9a-f]{64}", observed["Id"])
        and observed["Image"] == spec["candidate"]["image"]
        and labels.get("org.opencontainers.image.revision") == spec["candidate"]["revision"]
        and labels.get("org.autostop.crm.tree") == spec["candidate"]["tree"]
        and labels.get("com.docker.compose.project") == "autostopcrm"
        and labels.get("com.docker.compose.service") == SERVICE
        and state["Running"] is True
        and state.get("Health", {}).get("Status") == "healthy"
        and not state.get("Paused")
        and not state.get("Restarting")
        and type(state["Pid"]) is int
        and state["Pid"] > 0
        and type(state["StartedAt"]) is str
        and state["StartedAt"]
        and observed["RestartCount"] == 0
        and host["RestartPolicy"]["Name"] == "no"
        and host["ReadonlyRootfs"] is True
        and _tmpfs(host["Tmpfs"]) == _tmpfs(expected_tmpfs)
        and host["Init"] is True
        and not host.get("Privileged")
        and host.get("CapDrop") == ["ALL"]
        and "no-new-privileges:true" in host["SecurityOpt"]
        and observed["Config"]["User"] == "10001:10001"
        and io.sha(io.canonical(environment(observed["Config"]["Env"])))
        == spec["expected_env_sha256"]
        and mounts(observed["Mounts"]) == spec["expected_mounts"]
        and host["PortBindings"] == spec["preserved"]["ports"]
        and sorted(observed["NetworkSettings"]["Networks"]) == spec["preserved"]["networks"],
        "cold_crm_actual_container_mismatch",
    )
    return {
        "container_id": observed["Id"],
        "image_id": observed["Image"],
        "init_pid": state["Pid"],
        "started_at": state["StartedAt"],
        "restart_count": 0,
    }


class Adapter:
    def __init__(self, run):
        self.run = run

    def inspect(self, spec):
        from scripts.j1_forward_host import DOCKER

        raw = io.document(self.run(DOCKER + ["inspect", SERVICE], label="cold-crm-inspect"))
        io.require(type(raw) is list and len(raw) == 1, "cold_crm_inspection_ambiguous")
        return raw[0]

    def replace_once(self, spec_ref, compose_ref):
        from scripts.j1_forward_host import DOCKER

        spec = validate_spec(io.document(io.referenced(spec_ref)))
        io.require(
            io.document(io.referenced(compose_ref)) == spec["compose"], "cold_crm_compose_changed"
        )
        previous = self.inspect(spec)
        io.require(
            previous["Id"] == spec["previous_container_id"], "cold_crm_previous_instance_changed"
        )
        self.run(
            DOCKER
            + [
                "compose",
                "--project-name",
                "autostopcrm",
                "--file",
                compose_ref["path"],
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--force-recreate",
                SERVICE,
            ],
            label="cold-crm-replace-once",
            timeout=90,
        )


def write_spec(directory, spec):
    validate_spec(spec)
    directory = io.protected_directory(Path(directory), private=True)
    compose_ref = io.write_new(directory / "cold-crm-compose.json", spec["compose"])
    return io.write_new(directory / "cold-crm-spec.json", spec), compose_ref
