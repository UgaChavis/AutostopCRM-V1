"""Private ZIP and primary-source boundary tests; never native acceptance.

The tiny capsule is an opaque synthetic member here. The separate capsule
suite verifies tar contents and restore; these tests exercise origin binding.
"""

from __future__ import annotations

import base64
import copy
import os
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_producer_native as effectors
from scripts import j1_forward_io as io


@unittest.skipUnless(os.name == "posix", "Requires private native Linux fixture")
class NativeRuntimeArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cold-artifact-boundary-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binding = {"attempt_id": str(uuid.uuid4())}
        self.archive = self.root / "inputs" / self.binding["attempt_id"] / "runtime-artifact.zip"
        self.archive.parent.mkdir(mode=0o700, parents=True)
        self.capsule = b"explicit synthetic capsule member; not executable"
        executable_sha = io.sha(b"synthetic executable")
        self.runtime = {
            "schema": "autostop.j1.cold-runtime-index.v2",
            "scope": "production_native",
            "binding": self.binding,
            "runtime_roots": {"python": "/final/python", "venv": "/final/venv"},
            "entries": [
                {"kind": "file", "name": root + "/bin/python3.12", "sha256": executable_sha}
                for root in ("python", "venv")
            ],
            "unpacked_bytes": 40,
            "capsule_ref": {
                "path": "/protected/relocated/capsule.gz",
                "sha256": io.sha(self.capsule),
            },
        }
        self.builder_files = {
            effectors.BUILDER_WORKFLOW: b"workflow",
            "scripts/builder.py": b"builder",
        }
        self.builder_sources = {name: io.sha(raw) for name, raw in self.builder_files.items()}
        self.manifest = {
            "schema": "autostop.manager.installed-runtime-artifact.v1",
            "controller_revision": effectors.BUILDER_REVISION,
            "workflow_revision": effectors.BUILDER_REVISION,
            "run_id": "11",
            "run_attempt": "1",
            "dependency_run_id": "12",
            "source_revision": "1" * 40,
            "source_tree": "2" * 40,
            "helper_revision": effectors.CAPSULE_HELPER_REVISION,
            "runtime_roots": self.runtime["runtime_roots"],
            "entries": 2,
            "files": 2,
            "expanded_bytes": 40,
            "capsule_bytes": len(self.capsule),
            "physical_python_sha256": executable_sha,
            "final_prefix_bytes_created_before_seal": True,
            "final_modes_sealed_before_index": True,
            "cfg_rewritten_on_restore": False,
            "six_role_dependency_imports": True,
            "native_acceptance": False,
            "production_updated": False,
            "release_authority": False,
            "independent_full_restore": {
                "schema": "autostop.j1.cold-runtime-restored.v1",
                "members": 2,
                "files": 2,
                "bytes": 40,
                "archived_code_executed": False,
            },
        }
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("cold-installed-runtime/manager-runtime.tar.gz", self.capsule)
            index = copy.deepcopy(self.runtime)
            index["capsule_ref"]["path"] = "/hosted/runner/output/capsule.gz"
            archive.writestr("cold-installed-runtime/runtime-index.json", io.canonical(index))
            archive.writestr("cold-installed-runtime/manifest.json", io.canonical(self.manifest))
            archive.writestr(
                "cold-installed-runtime/dependency-check.json",
                io.canonical(
                    {
                        "dependency_imports": True,
                        "roles_started": False,
                        "cold_binding_acceptance": False,
                    }
                ),
            )
        self.archive.chmod(0o600)
        self.provenance = {
            "schema": "autostop.j1.cold-runtime-artifact-provenance.v1",
            "repository": "UgaChavis/AutostopManager",
            "source_revision": "1" * 40,
            "source_tree": "2" * 40,
            "builder_revision": effectors.BUILDER_REVISION,
            "builder_tree": effectors.BUILDER_TREE,
            "builder_sources": self.builder_sources,
            "workflow_path": effectors.BUILDER_WORKFLOW,
            "event": "workflow_dispatch",
            "run_id": 11,
            "run_attempt": 1,
            "artifact_id": 21,
            "archive_path": str(self.archive),
            "archive_sha256": io.sha(self.archive.read_bytes()),
            "archive_bytes": self.archive.stat().st_size,
            "capsule_member": "cold-installed-runtime/manager-runtime.tar.gz",
            "index_member": "cold-installed-runtime/runtime-index.json",
            "manifest_member": "cold-installed-runtime/manifest.json",
        }
        raw = io.canonical(self.provenance)
        reference = {"path": str(self.root / "origin.json"), "sha256": io.sha(raw)}
        self.document = {
            "manager": {"revision": "1" * 40, "tree": "2" * 40},
            "runtime_artifact_provenance_ref": reference,
            "runtime_artifact_ref": {"path": "/protected/index.json", "sha256": "3" * 64},
        }
        self.backend = effectors.NativeBackend({}, self.binding, 9)
        self.backend.binding = self.binding
        self.backend.provider = SimpleNamespace(read_reference=lambda _reference: raw)
        self.backend.evidence = lambda _name, body: body
        repository = {"full_name": "UgaChavis/AutostopManager"}
        run = {
            "id": 11,
            "repository": repository,
            "head_repository": repository,
            "head_sha": effectors.BUILDER_REVISION,
            "path": effectors.BUILDER_WORKFLOW,
            "event": "workflow_dispatch",
            "head_branch": effectors.BUILDER_BRANCH,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
        }
        self.responses = {
            "/actions/runs/11": run,
            "/git/commits/" + effectors.BUILDER_REVISION: {
                "sha": effectors.BUILDER_REVISION,
                "tree": {"sha": effectors.BUILDER_TREE},
            },
            "/actions/artifacts/21": {
                "id": 21,
                "name": "manager-installed-runtime-" + "1" * 40 + "-11",
                "expired": False,
                "workflow_run": {"id": 11, "head_sha": effectors.BUILDER_REVISION},
                "digest": "sha256:" + self.provenance["archive_sha256"],
                "size_in_bytes": self.provenance["archive_bytes"],
            },
            "/actions/runs/12": {
                "repository": repository,
                "head_repository": repository,
                "status": "completed",
                "conclusion": "success",
            },
        }
        for name, body in self.builder_files.items():
            self.responses["/contents/" + name + "?ref=" + effectors.BUILDER_REVISION] = {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(body).decode(),
            }
        self.backend.github_runtime_artifact = Mock(
            side_effect=lambda suffix: copy.deepcopy(self.responses[suffix])
        )
        for name, value in (
            ("BUILDER_SOURCES", self.builder_sources),
            ("PHYSICAL_PYTHON_SHA256", executable_sha),
        ):
            context = patch.object(effectors, name, value)
            context.start()
            self.addCleanup(context.stop)
        for target, attribute, value in (
            (effectors.native, "CONFIG", self.root),
            (io, "ROOT_UID", os.getuid()),
        ):
            context = patch.object(target, attribute, value)
            context.start()
            self.addCleanup(context.stop)

        # Production custody rejects a world-writable /tmp ancestor. This
        # explicit private-filesystem seam checks the fixture subtree only.
        def private_directory(path, *, private=False):
            path = Path(path)
            self.assertTrue(path.is_relative_to(self.root))
            self.assertFalse(path.is_symlink())
            self.assertEqual(os.getuid(), path.stat().st_uid)
            self.assertEqual(0, path.stat().st_mode & 0o022)
            if private:
                self.assertEqual(0o700, path.stat().st_mode & 0o777)
            return path

        context = patch.object(io, "protected_directory", side_effect=private_directory)
        context.start()
        self.addCleanup(context.stop)

    def test_member_relocation_preserves_exact_final_installed_index_and_no_offline_claim(self):
        result = self.backend.verify_runtime_artifact(self.document, self.runtime)
        self.assertFalse(result["offline_zip_provenance_restored"])
        self.assertEqual(21, result["artifact_id"])

    def test_other_builder_source_or_failed_run_is_not_origin_authority(self):
        self.responses["/actions/runs/11"]["conclusion"] = "failure"
        with self.assertRaisesRegex(io.Refusal, "builder_run"):
            self.backend.verify_runtime_artifact(self.document, self.runtime)

    def test_authenticated_zip_digest_does_not_allow_modified_member_index(self):
        other = copy.deepcopy(self.runtime)
        other["runtime_roots"]["venv"] = "/different/final-prefix"
        with self.assertRaisesRegex(io.Refusal, "installed_index"):
            self.backend.verify_runtime_artifact(self.document, other)

    def test_zip_modified_after_primary_digest_is_rejected(self):
        with self.archive.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(io.Refusal, "zip_bytes"):
            self.backend.verify_runtime_artifact(self.document, self.runtime)
