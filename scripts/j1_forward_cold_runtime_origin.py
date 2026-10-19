"""Measured Manager runtime origin, separate from native release permission.

The immutable builder and fresh GitHub artifact identity authenticate exact
private ZIP members. No download, execution or native admission occurs on import.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

BUILDER_REVISION = "245b12308c9310a4a0cecaaeb2e052e9ac9d8b34"
BUILDER_TREE = "0f539eea21f0e57e3763e8418dbafec718632b86"
BUILDER_WORKFLOW = ".github/workflows/cold-installed-runtime.yml"
BUILDER_BRANCH = "codex/cold-runtime-artifact-20261019"
BUILDER_SOURCES = {
    ".github/workflows/cold-installed-runtime.yml": "fd1a71808efada3f158bee4892c63592602571addcea1e68adb073602d141724",
    "scripts/ci_cold_installed_runtime.py": "99c44cef813ae6bfb424dbb4554c267ed8032e5edc2b02b0b9e8ff3e102f4346",
    "scripts/test_ci_cold_installed_runtime.py": "7f997fe753e967ffd20f0453495e5317bd39f784b4016204cc80163b80929d7d",
}
CAPSULE_HELPER_REVISION = "e285da961b2e5f0f004f1428a268735090faf667"
PHYSICAL_PYTHON_SHA256 = "eaaa308032c4aa063df5880c4c293cd458b4629d503fbc2c03ec163d71f7f790"


def verify_artifact(backend, document, runtime):
    """Verify fresh GitHub ZIP identity and actual member bytes, no exec.

    The reviewed request explicitly names the immutable builder source.
    This is separate from Manager source's production-push quality gate.
    The large outer ZIP remains private tooling, never another control leaf.
    """
    import hashlib
    import zipfile

    from scripts import j1_forward_cold_kernel as kernel

    provenance = kernel._read(backend.provider, document["runtime_artifact_provenance_ref"])
    cold.fields(
        provenance,
        {
            "schema",
            "repository",
            "source_revision",
            "source_tree",
            "builder_revision",
            "builder_tree",
            "builder_sources",
            "workflow_path",
            "event",
            "run_id",
            "run_attempt",
            "artifact_id",
            "archive_path",
            "archive_sha256",
            "archive_bytes",
            "capsule_member",
            "index_member",
            "manifest_member",
        },
        "runtime_artifact_provenance_fields",
    )
    cold.need(
        provenance["schema"] == "autostop.j1.cold-runtime-artifact-provenance.v1"
        and provenance["repository"] == "UgaChavis/AutostopManager"
        and provenance["source_revision"] == document["manager"]["revision"]
        and provenance["source_tree"] == document["manager"]["tree"]
        and provenance["event"] == "workflow_dispatch",
        "runtime_artifact_source_domains",
    )
    cold.need(
        all(
            type(provenance[key]) is int and provenance[key] > 0
            for key in ("run_id", "run_attempt", "artifact_id")
        )
        and all(
            type(provenance[key]) is str and cold.SHA1.fullmatch(provenance[key])
            for key in ("builder_revision", "builder_tree")
        ),
        "runtime_artifact_identity",
    )
    cold.need(
        provenance["builder_revision"] == BUILDER_REVISION
        and provenance["builder_tree"] == BUILDER_TREE
        and provenance["workflow_path"] == BUILDER_WORKFLOW
        and provenance["builder_sources"] == BUILDER_SOURCES,
        "runtime_artifact_approved_builder",
    )
    workflow = provenance["workflow_path"]
    cold.need(
        type(workflow) is str
        and re.fullmatch(r"\.github/workflows/[a-z0-9_-]+\.yml", workflow)
        and type(provenance["builder_sources"]) is dict
        and workflow in provenance["builder_sources"]
        and 2 <= len(provenance["builder_sources"]) <= 16,
        "runtime_artifact_reviewed_builder_sources",
    )
    repository = provenance["repository"]

    get = backend.github_runtime_artifact

    run = get("/actions/runs/" + str(provenance["run_id"]))
    cold.need(
        run["id"] == provenance["run_id"]
        and run["repository"]["full_name"] == repository
        and run["head_repository"]["full_name"] == repository
        and run["head_sha"] == provenance["builder_revision"]
        and run["path"] == workflow
        and run["event"] == provenance["event"]
        and run["head_branch"] == BUILDER_BRANCH
        and run["run_attempt"] == provenance["run_attempt"]
        and run["status"] == "completed"
        and run["conclusion"] == "success",
        "runtime_artifact_builder_run",
    )
    commit = get("/git/commits/" + provenance["builder_revision"])
    cold.need(
        commit["sha"] == provenance["builder_revision"]
        and commit["tree"]["sha"] == provenance["builder_tree"],
        "runtime_artifact_builder_tree",
    )
    import base64

    for name, expected in provenance["builder_sources"].items():
        cold.need(
            type(name) is str
            and not name.startswith("/")
            and ".." not in name.split("/")
            and (name == workflow or re.fullmatch(r"scripts/[a-z0-9_]+\.py", name))
            and type(expected) is str
            and cold.HEX.fullmatch(expected),
            "runtime_artifact_builder_member",
        )
        item = get("/contents/" + name + "?ref=" + provenance["builder_revision"])
        cold.need(
            item["type"] == "file"
            and item["encoding"] == "base64"
            and io.sha(base64.b64decode(item["content"])) == expected,
            "runtime_artifact_builder_source_changed",
        )
    artifact = get("/actions/artifacts/" + str(provenance["artifact_id"]))
    cold.need(
        artifact["id"] == provenance["artifact_id"]
        and artifact["name"]
        == "manager-installed-runtime-"
        + provenance["source_revision"]
        + "-"
        + str(provenance["run_id"])
        and artifact["expired"] is False
        and artifact["workflow_run"]["id"] == provenance["run_id"]
        and artifact["workflow_run"]["head_sha"] == provenance["builder_revision"]
        and artifact["digest"] == "sha256:" + provenance["archive_sha256"]
        and artifact["size_in_bytes"] == provenance["archive_bytes"],
        "runtime_artifact_authenticated_digest",
    )
    archive = Path(provenance["archive_path"])
    cold.need(
        archive
        == native.CONFIG / "inputs" / backend.binding["attempt_id"] / "runtime-artifact.zip",
        "runtime_artifact_private_fixed_zip",
    )
    io.protected_directory(archive.parent, private=True)
    before = kernel.file_version(archive.lstat())
    cold.need(
        0 < provenance["archive_bytes"] <= 256 * 1024**2
        and before[2] == provenance["archive_bytes"]
        and kernel.file_digest(archive, limit=256 * 1024**2)["sha256"]
        == provenance["archive_sha256"],
        "runtime_artifact_zip_bytes",
    )
    names = {key: provenance[key] for key in ("capsule_member", "index_member", "manifest_member")}
    cold.need(
        names
        == {
            "capsule_member": "cold-installed-runtime/manager-runtime.tar.gz",
            "index_member": "cold-installed-runtime/runtime-index.json",
            "manifest_member": "cold-installed-runtime/manifest.json",
        }
        and len(set(names.values())) == 3
        and all(
            type(name) is str
            and name.startswith("cold-installed-runtime/")
            and not name.startswith("/")
            and ".." not in name.split("/")
            for name in names.values()
        ),
        "runtime_artifact_members",
    )
    with zipfile.ZipFile(archive) as stream:
        entries = stream.infolist()
        cold.need(
            len(entries) <= 32 and len({entry.filename for entry in entries}) == len(entries),
            "runtime_artifact_zip_inventory",
        )

        def member(name, limit):
            entry = stream.getinfo(name)
            cold.need(
                not entry.flag_bits & 1 and not entry.is_dir() and entry.file_size <= limit,
                "runtime_artifact_zip_member_size",
            )
            with stream.open(entry) as inner:
                value = inner.read(limit + 1)
            cold.need(len(value) == entry.file_size <= limit, "runtime_artifact_zip_member_changed")
            return value

        index = io.document(member(names["index_member"], 16 * 1024**2))
        cold.need(
            {
                key: value
                for key, value in index.items()
                if key not in {"binding", "scope", "capsule_ref"}
            }
            == {
                key: value
                for key, value in runtime.items()
                if key not in {"binding", "scope", "capsule_ref"}
            },
            "runtime_artifact_installed_index",
        )
        entry = stream.getinfo(names["capsule_member"])
        cold.need(
            entry.file_size <= 192 * 1024**2 and not entry.flag_bits & 1,
            "runtime_artifact_capsule_size",
        )
        digest = hashlib.sha256()
        with stream.open(entry) as inner:
            while chunk := inner.read(1024**2):
                digest.update(chunk)
        cold.need(
            digest.hexdigest()
            == index["capsule_ref"]["sha256"]
            == runtime["capsule_ref"]["sha256"],
            "runtime_artifact_capsule_member",
        )
        manifest = io.document(member(names["manifest_member"], 4 * 1024**2))
        imports = io.document(member("cold-installed-runtime/dependency-check.json", 4096))
        cold.need(
            imports
            == {
                "dependency_imports": True,
                "roles_started": False,
                "cold_binding_acceptance": False,
            },
            "runtime_artifact_dependency_scope",
        )
    files = [row for row in runtime["entries"] if row["kind"] == "file"]
    cold.need(
        manifest["schema"] == "autostop.manager.installed-runtime-artifact.v1"
        and manifest["controller_revision"]
        == manifest["workflow_revision"]
        == provenance["builder_revision"]
        and manifest["run_id"] == str(provenance["run_id"])
        and manifest["run_attempt"] == str(provenance["run_attempt"])
        and manifest["source_revision"] == provenance["source_revision"]
        and manifest["source_tree"] == provenance["source_tree"]
        and manifest["helper_revision"] == CAPSULE_HELPER_REVISION
        and manifest["runtime_roots"] == runtime["runtime_roots"]
        and manifest["entries"] == len(runtime["entries"])
        and manifest["files"] == len(files)
        and manifest["expanded_bytes"] == runtime["unpacked_bytes"]
        and manifest["capsule_bytes"] == entry.file_size
        and manifest["physical_python_sha256"] == PHYSICAL_PYTHON_SHA256
        and all(
            next(row for row in files if row["name"] == name)["sha256"] == PHYSICAL_PYTHON_SHA256
            for name in ("python/bin/python3.12", "venv/bin/python3.12")
        )
        and manifest["final_prefix_bytes_created_before_seal"] is True
        and manifest["final_modes_sealed_before_index"] is True
        and manifest["cfg_rewritten_on_restore"] is False
        and manifest["six_role_dependency_imports"] is True
        and all(
            manifest[key] is False
            for key in ("native_acceptance", "production_updated", "release_authority")
        )
        and manifest["independent_full_restore"]
        == {
            "schema": "autostop.j1.cold-runtime-restored.v1",
            "members": len(runtime["entries"]),
            "files": len(files),
            "bytes": runtime["unpacked_bytes"],
            "archived_code_executed": False,
        },
        "runtime_artifact_manifest_source_or_scope",
    )
    dependency_id = manifest["dependency_run_id"]
    cold.need(
        (type(dependency_id) is int and dependency_id > 0)
        or (type(dependency_id) is str and re.fullmatch(r"[1-9][0-9]{0,19}", dependency_id)),
        "runtime_artifact_dependency_run",
    )
    dependency_run = get("/actions/runs/" + str(dependency_id))
    cold.need(
        dependency_run["repository"]["full_name"] == repository
        and dependency_run["head_repository"]["full_name"] == repository
        and dependency_run["status"] == "completed"
        and dependency_run["conclusion"] == "success",
        "runtime_artifact_dependency_run_failed",
    )
    cold.need(
        kernel.file_version(archive.lstat()) == before
        and kernel.file_digest(archive, limit=256 * 1024**2)["sha256"]
        == provenance["archive_sha256"],
        "runtime_artifact_zip_changed",
    )
    cold.need(
        get("/actions/runs/" + str(provenance["run_id"])) == run
        and get("/actions/artifacts/" + str(provenance["artifact_id"])) == artifact,
        "runtime_artifact_origin_changed_during_read",
    )
    return backend.evidence(
        "runtime-artifact-authority",
        {
            "schema": "autostop.j1.cold-runtime-artifact-verification.v1",
            "scope": backend.scope,
            "binding": backend.binding,
            "provenance_ref": document["runtime_artifact_provenance_ref"],
            "runtime_index_ref": document["runtime_artifact_ref"],
            "run": {
                key: run[key]
                for key in (
                    "id",
                    "run_attempt",
                    "head_sha",
                    "path",
                    "event",
                    "status",
                    "conclusion",
                )
            },
            "artifact_id": artifact["id"],
            "zip_digest": artifact["digest"],
            "zip_bytes": artifact["size_in_bytes"],
            "member_mapping": names,
            "manifest_sha256": cold.digest(manifest),
            "offline_zip_provenance_restored": False,
        },
    )
