"""File-backed source preparation; only fixture-owned Git and isolated help subprocesses."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_manager_transition_source as source
from scripts.j1_forward_io import Refusal, canonical, document, sha


class OwnedFiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        self.commands = []
        original = subprocess.Popen

        def spawn(args, *positional, **kwargs):
            self.assertIsInstance(args, list)
            self.assertFalse(kwargs.get("shell"))
            if Path(args[0]).name.casefold() in {"git", "git.exe"}:
                directory = Path(args[args.index("-C") + 1]).resolve()
                self.assertTrue(directory.is_relative_to(self.root), args)
                allowed = {
                    "init",
                    "add",
                    "commit",
                    "config",
                    "rev-parse",
                    "ls-tree",
                    "ls-files",
                    "cat-file",
                    "update-index",
                }
                offset = args.index("-C") + 2
                while args[offset] == "-c":
                    offset += 2
                self.assertIn(args[offset], allowed)
            else:
                self.assertEqual(Path(sys.executable), Path(args[0]))
                self.assertEqual(["-I", "-S", "-B"], args[1:4])
                self.assertTrue(Path(args[4]).is_relative_to(self.root))
                self.assertEqual(["--help"], args[5:])
            self.commands.append(args)
            return original(args, *positional, **kwargs)

        for target in (
            "socket.socket.connect",
            "socket.socket.bind",
            "socket.getaddrinfo",
            "os.system",
            "os.kill",
        ):
            guard = patch(target, side_effect=AssertionError("external operation forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        process = patch.object(subprocess, "Popen", side_effect=spawn)
        process.start()
        self.addCleanup(process.stop)

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else canonical(value))
        return {"path": str(path), "sha256": sha(path.read_bytes())}

    def git(self, repository, *arguments):
        return source._git(repository, list(arguments)).decode().strip()

    def repository(self, name="manager", files=None):
        repository = self.root / name
        repository.mkdir()
        self.git(repository, "init", "--quiet")
        self.git(repository, "config", "core.autocrlf", "false")
        self.git(repository, "config", "user.name", "Synthetic source fixture")
        self.git(repository, "config", "user.email", "fixture@example.invalid")
        for name, raw in (
            files
            or {
                "autostop_manager/mcp_server.py": b"raise RuntimeError('never import candidate')\n",
                "README.md": b"Synthetic only.\n",
            }
        ).items():
            path = repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.git(repository, "add", ".")
        self.git(
            repository, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Synthetic source"
        )
        return (
            repository,
            self.git(repository, "rev-parse", "HEAD"),
            self.git(repository, "rev-parse", "HEAD^{tree}"),
        )

    def ci(self, project, revision):
        repository = source.REPOSITORIES[project]
        run = {
            "id": 17,
            "run_attempt": 2,
            "repository": {"full_name": repository},
            "head_repository": {"full_name": repository},
            "head_sha": revision,
            "head_branch": "codex/source-only",
            "event": "push",
            "path": ".github/workflows/quality.yml",
            "status": "completed",
            "conclusion": "success",
            "html_url": f"https://github.com/{repository}/actions/runs/17",
        }
        jobs = [
            {
                "id": index + 40,
                "name": name,
                "run_id": 17,
                "run_attempt": 2,
                "head_sha": revision,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(sorted(source.JOBS[project]))
        ]
        return {"run": run, "jobs": {"total_count": len(jobs), "jobs": jobs}}


class GitSourceTests(OwnedFiles):
    def test_committed_blobs_modes_and_nested_tree_are_real_and_not_imported(self):
        repository, revision, tree = self.repository()
        index, contents = source.collect_source(repository, revision, tree)
        self.assertEqual(revision, index["revision"])
        self.assertEqual(tree, index["tree"])
        self.assertEqual(2, len(index["files"]))
        for item in index["files"]:
            raw = contents[item["path"]]
            self.assertEqual(sha(raw), item["sha256"])
            self.assertEqual(
                hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(), item["git_oid"]
            )
            self.assertEqual("100644", item["mode"])
        self.assertFalse(any("status" in command or "diff" in command for command in self.commands))

    def test_changed_worktree_bytes_are_not_hidden_by_valid_git_objects(self):
        repository, revision, tree = self.repository()
        (repository / "README.md").write_bytes(b"changed\n")
        with self.assertRaisesRegex(Refusal, "checkout_bytes_changed"):
            source.collect_source(repository, revision, tree)

    def test_crlf_normalized_checkout_deliberately_requires_byte_clean_copy(self):
        repository, revision, tree = self.repository()
        self.git(repository, "config", "core.autocrlf", "true")
        (repository / "README.md").write_bytes(b"Synthetic only.\r\n")
        with self.assertRaisesRegex(Refusal, "checkout_bytes_changed"):
            source.collect_source(repository, revision, tree)

    def test_untracked_staged_hidden_index_and_wrong_revision_are_rejected(self):
        for kind in ("untracked", "staged", "assume", "skip", "revision", "tree"):
            with self.subTest(kind=kind):
                repository, revision, tree = self.repository(kind)
                expected = {
                    "untracked": "checkout_untracked",
                    "staged": "index_changed",
                    "assume": "hidden_index_flags",
                    "skip": "hidden_index_flags",
                    "revision": "head_changed",
                    "tree": "git_failed",
                }[kind]
                if kind == "untracked":
                    (repository / "extra").write_bytes(b"untracked")
                elif kind == "staged":
                    (repository / "README.md").write_bytes(b"staged\n")
                    self.git(repository, "add", ".")
                    (repository / "README.md").write_bytes(b"Synthetic only.\n")
                elif kind in {"assume", "skip"}:
                    self.git(
                        repository,
                        "update-index",
                        "--assume-unchanged" if kind == "assume" else "--skip-worktree",
                        "README.md",
                    )
                elif kind == "revision":
                    revision = "a" * 40
                else:
                    tree = "a" * 40
                    expected = "git_object_missing"
                with self.assertRaisesRegex(Refusal, expected):
                    source.collect_source(repository, revision, tree)

    def test_filter_configuration_refused_without_running_configured_command(self):
        repository, revision, tree = self.repository()
        sentinel = self.root / "filter-executed"
        self.git(repository, "config", "filter.evil.clean", f"touch {sentinel}")
        (repository / ".gitattributes").write_bytes(b"* filter=evil\n")
        with self.assertRaisesRegex(Refusal, "executable_configuration"):
            source.collect_source(repository, revision, tree)
        self.assertFalse(sentinel.exists())

    def test_include_and_attributes_configuration_are_not_followed(self):
        for key in ("include.path", "includeIf.gitdir:*.path", "core.attributesFile"):
            with self.subTest(key=key):
                repository, revision, tree = self.repository("repository" + str(len(self.commands)))
                self.git(repository, "config", key, str(self.root / "absent"))
                with self.assertRaisesRegex(Refusal, "executable_configuration"):
                    source.collect_source(repository, revision, tree)

    def test_managed_worktree_configuration_is_supported(self):
        repository, revision, tree = self.repository()
        self.git(repository, "config", "extensions.worktreeConfig", "true")
        self.git(repository, "config", "--worktree", "core.autocrlf", "false")
        source.collect_source(repository, revision, tree)
        self.git(repository, "config", "--worktree", "filter.evil.process", "never-run")
        with self.assertRaisesRegex(Refusal, "executable_configuration"):
            source.collect_source(repository, revision, tree)

    def test_wrong_tree_and_subdirectory_and_empty_repository_are_rejected(self):
        repository, revision, tree = self.repository()
        with self.assertRaisesRegex(Refusal, "repository_root_required"):
            source.collect_source(repository / "autostop_manager", revision, tree)
        with self.assertRaisesRegex(Refusal, "source_tree_mismatch"):
            source.collect_source(repository, revision, revision)
        with self.assertRaisesRegex(Refusal, "revision_invalid"):
            source.collect_source(repository, "HEAD", tree)
        with self.assertRaisesRegex(Refusal, "tree_invalid"):
            source.collect_source(repository, revision, "HEAD^{tree}")

    def test_git_object_hash_is_recomputed_instead_of_trusting_batch_header(self):
        identifier = hashlib.sha1(b"blob 1\0A").hexdigest()
        payload = f"{identifier} blob 1\n".encode() + b"B\n"
        with patch.object(source, "_git", return_value=payload):
            with self.assertRaisesRegex(Refusal, "object_content_changed"):
                source._objects(self.root, [identifier])

    def test_git_errors_are_bounded_without_exposing_stderr(self):
        with patch.object(
            source.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 45)
        ):
            with self.assertRaisesRegex(Refusal, "git_unavailable"):
                source._git(self.root, ["rev-parse", "HEAD"])
        with patch.object(source.shutil, "which", return_value=None):
            with self.assertRaisesRegex(Refusal, "git_required"):
                source._git(self.root, ["rev-parse", "HEAD"])


class InputTests(OwnedFiles):
    def test_stable_bytes_reference_and_relative_reference(self):
        ref = self.put("input", b"exact\n")
        self.assertEqual(b"exact\n", source.reference_bytes(ref))
        self.assertEqual(b"exact\n", source.reference_bytes({**ref, "path": "input"}, self.root))
        with self.assertRaisesRegex(Refusal, "reference_hash_mismatch"):
            source.reference_bytes({**ref, "sha256": "a" * 64})
        for invalid in ({}, {"path": 1, "sha256": "a" * 64}, {**ref, "extra": True}):
            with self.subTest(invalid=invalid), self.assertRaises(Refusal):
                source.reference_bytes(invalid)

    def test_hardlink_directory_oversize_and_changed_input_are_rejected(self):
        ref = self.put("input", b"exact\n")
        with self.assertRaisesRegex(Refusal, "regular_file_required"):
            source.stable_read(Path(ref["path"]), 1)
        with self.assertRaisesRegex(Refusal, "regular_file_required"):
            source.stable_read(self.root)
        os.link(ref["path"], self.root / "hardlink")
        with self.assertRaisesRegex(Refusal, "regular_file_required"):
            source.stable_read(ref["path"])
        (self.root / "hardlink").unlink()
        original = os.fstat
        calls = 0

        def changed(descriptor):
            nonlocal calls
            calls += 1
            if calls == 1:
                Path(ref["path"]).write_bytes(b"changed file\n")
            return original(descriptor)

        with patch.object(source.os, "fstat", side_effect=changed):
            with self.assertRaisesRegex(Refusal, "source_file_changed"):
                source.stable_read(ref["path"])

    def test_paths_reject_traversal_aliases_and_windows_devices(self):
        for value in (
            "",
            "../x",
            "/x",
            "C:/x",
            "a\\b",
            "a//b",
            ".git/config",
            "a/CON",
            "a/NUL.txt",
            "a.",
            "a ",
            "a\n",
            "a\t",
            "a\x7f",
        ):
            with self.subTest(value=value), self.assertRaises(Refusal):
                source.relative_name(value)
        with self.assertRaisesRegex(Refusal, "absolute_physical"):
            source.physical_directory(Path("relative"))

    def test_symbolic_alias_refused_when_platform_can_create_one(self):
        ref = self.put("input", b"raw")
        alias = self.root / "alias"
        try:
            alias.symlink_to(ref["path"])
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc.errno}")
        with self.assertRaisesRegex(Refusal, "regular_file_required"):
            source.stable_read(alias)

    def package(self):
        raw = b'{"actual":"raw original receipt"}\n'
        self.put("raw/receipt.json", raw)
        original = {"path": "/original/receipt.json", "sha256": sha(raw)}
        value = {
            "schema": source.PACKAGE_SCHEMA,
            "export": {"synthetic": True},
            "documents": [{"reference": original, "file": "raw/receipt.json"}],
        }
        return self.put("package.json", value), original, value

    def test_original_logical_refs_resolve_without_rewriting_receipts(self):
        ref, original, _ = self.package()
        package = source.PredecessorPackage(ref)
        self.assertIn(b"raw original receipt", package.read(original))
        package.recheck()
        with self.assertRaisesRegex(Refusal, "original_bytes_required"):
            package.read({**original, "path": "/different/receipt.json"})
        with self.assertRaisesRegex(Refusal, "original_bytes_required"):
            package.read({**original, "sha256": "c" * 64})
        (self.root / "raw/receipt.json").write_bytes(b"changed")
        with self.assertRaisesRegex(Refusal, "document_changed"):
            package.read(original)

    def test_export_without_raw_bytes_or_duplicate_or_traversal_is_refused(self):
        _, _, value = self.package()
        for kind in ("empty", "duplicate", "escape", "unknown", "schema", "hash", "logical"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(value)
                if kind == "empty":
                    changed["documents"] = []
                elif kind == "duplicate":
                    changed["documents"] *= 2
                elif kind == "escape":
                    changed["documents"][0]["file"] = "../receipt.json"
                elif kind == "unknown":
                    changed["private_fallback"] = True
                elif kind == "schema":
                    changed["schema"] = "future"
                elif kind == "hash":
                    changed["documents"][0]["reference"]["sha256"] = "f" * 64
                else:
                    changed["documents"][0]["reference"]["path"] = "/original/../receipt.json"
                with self.assertRaises(Refusal):
                    source.PredecessorPackage(self.put("changed.json", changed))

    def test_cached_ci_has_separate_exact_project_jobs_and_no_permission(self):
        for project in source.JOBS:
            result = source.cached_ci(self.ci(project, "a" * 40), project, "a" * 40)
            self.assertEqual(sorted(source.JOBS[project]), result["jobs"])
            self.assertFalse(result["native_acceptance"])
            self.assertFalse(result["activation_authorized"])
            self.assertFalse(result["fresh_github_verified"])

    def test_cached_ci_rejects_wrong_repository_revision_attempt_or_partial_jobs(self):
        for kind in (
            "repository",
            "head_repository",
            "revision",
            "branch",
            "event",
            "path",
            "status",
            "conclusion",
            "url",
            "attempt",
            "job_attempt",
            "job_revision",
            "job_id",
            "job_status",
            "job_conclusion",
            "missing_job",
            "extra_job",
            "duplicate_job",
            "total",
            "boolean_run",
            "repository_shape",
        ):
            with self.subTest(kind=kind):
                value = self.ci("crm", "a" * 40)
                run, jobs = value["run"], value["jobs"]
                if kind in {"repository", "head_repository"}:
                    run[kind]["full_name"] = source.REPOSITORIES["manager"]
                elif kind in {"revision", "branch", "event", "path", "status", "conclusion", "url"}:
                    key = {"revision": "head_sha", "branch": "head_branch", "url": "html_url"}.get(
                        kind, kind
                    )
                    run[key] = "" if kind == "branch" else "different"
                elif kind == "attempt":
                    run["run_attempt"] = 9
                elif kind.startswith("job_"):
                    key = {
                        "job_attempt": "run_attempt",
                        "job_revision": "head_sha",
                        "job_id": "run_id",
                        "job_status": "status",
                        "job_conclusion": "conclusion",
                    }[kind]
                    jobs["jobs"][0][key] = "different"
                elif kind == "missing_job":
                    jobs["jobs"].pop()
                elif kind == "extra_job":
                    jobs["jobs"].append(dict(jobs["jobs"][0]))
                elif kind == "duplicate_job":
                    jobs["jobs"][1] = dict(jobs["jobs"][0])
                elif kind == "total":
                    jobs["total_count"] = 900
                elif kind == "boolean_run":
                    run["id"] = True
                else:
                    run["repository"] = []
                with self.assertRaises(Refusal):
                    source.cached_ci(value, "crm", "a" * 40)


class PublicationTests(OwnedFiles):
    def test_atomic_new_directory_never_replaces_existing_output(self):
        output = self.root / "published"
        output.mkdir()
        (output / "existing").write_bytes(b"preserve")
        staging = self.root / "staged"
        staging.mkdir()
        (staging / "new").write_bytes(b"new")
        with self.assertRaises(OSError):
            source._publish(staging, output)
        self.assertEqual(b"preserve", (output / "existing").read_bytes())
        self.assertTrue(staging.exists())
        with self.assertRaisesRegex(Refusal, "new_output_directory_required"):
            source.prepare(self.root / "missing", "a" * 64, output)

    def test_isolated_direct_cli_help_uses_only_stdlib_and_local_consumer(self):
        script = self.root / "consumer/scripts/j1_manager_transition_source.py"
        script.parent.mkdir(parents=True)
        for name in ("j1_manager_transition_source.py", "j1_forward_io.py"):
            (script.parent / name).write_bytes(
                source.stable_read(Path(source.__file__).with_name(name))
            )
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(script), "--help"],
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertIn(b"prepare,verify", completed.stdout)

    def test_cli_has_no_activation_or_replace_operation_and_fails_closed_on_missing_input(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            source.main(
                [
                    "apply",
                    "--inputs",
                    str(self.root / "missing"),
                    "--inputs-sha256",
                    "a" * 64,
                    "--output",
                    str(self.root / "output"),
                ]
            )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = source.main(
                [
                    "prepare",
                    "--inputs",
                    str(self.root / "missing"),
                    "--inputs-sha256",
                    "a" * 64,
                    "--output",
                    str(self.root / "output"),
                ]
            )
        self.assertEqual(2, result)
        report = document(stream.getvalue().encode())
        self.assertFalse(report["native_acceptance"])
        self.assertFalse(report["activation_authorized"])
        self.assertFalse((self.root / "output").exists())


class SourceConsumerTests(OwnedFiles):
    def setUp(self):
        super().setUp()
        from scripts import j1_manager_transition as transition
        from tests.test_j1_manager_transition import ATTEMPT, TransitionFixture

        self.transition = transition
        self.fixture = TransitionFixture()
        manager_files = {path: b"" for path in transition.PRESERVED_SOURCE_FILES}
        manager_files.update(
            {
                "autostop_manager/mcp_server.py": b"raise RuntimeError('never import candidate')\n",
                "README.md": b"Synthetic only.\n",
            }
        )
        manager, revision, tree = self.repository(files=manager_files)
        self.fixture.tree = tree
        self.fixture.proposal["source_root"] = (
            f"/opt/autostop-manager-mcp-releases/{revision}/source"
        )
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(self.fixture.patches())
        stack.enter_context(patch.object(transition, "REVISION", revision))
        stack.enter_context(patch.object(transition, "PROPOSAL", self.fixture.proposal))
        consumer_files = {
            name: source.stable_read(Path(source.__file__).resolve().parent.parent / name)
            for name in source.CONSUMERS
        }
        controller, controller_revision, controller_tree = self.repository(
            "controller", consumer_files
        )
        records = []
        for number, (path, raw) in enumerate(self.fixture.raw.items()):
            name = f"predecessor/raw/{number}.bin"
            self.put(name, raw)
            records.append(
                {"reference": {"path": path, "sha256": sha(raw)}, "file": f"raw/{number}.bin"}
            )
        self.package = {
            "schema": source.PACKAGE_SCHEMA,
            "export": self.fixture.predecessor,
            "documents": records,
        }
        self.value = {
            "schema": source.INPUT_SCHEMA,
            "attempt_id": ATTEMPT,
            "predecessor": self.put("predecessor/package.json", self.package),
            "manager": {
                "checkout": str(manager),
                "revision": revision,
                "tree": tree,
                "ci": self.put("manager-ci.json", self.ci("manager", revision)),
            },
            "controller": {
                "checkout": str(controller),
                "revision": controller_revision,
                "tree": controller_tree,
                "ci": self.put("controller-ci.json", self.ci("crm", controller_revision)),
            },
            "proposal": self.fixture.proposal,
        }
        self.inputs = self.put("inputs.json", self.value)
        self.output = self.root / "artifact"

    def flush(self):
        self.inputs = self.put("inputs.json", self.value)

    def prepare(self):
        return source.prepare(self.inputs["path"], self.inputs["sha256"], self.output)

    def verify(self):
        return source.verify(self.inputs["path"], self.inputs["sha256"], self.output)

    def test_real_prepare_and_reconstruction_preserve_old_bytes_and_source_only_flags(self):
        original = {
            path: sha(path.read_bytes())
            for path in (self.root / "predecessor").rglob("*")
            if path.is_file()
        }
        prepared = self.prepare()
        self.assertEqual(prepared, self.verify())
        capsule = document((self.output / "capsule.json").read_bytes())
        self.assertFalse(capsule["native_acceptance"])
        self.assertFalse(capsule["activation_authorized"])
        self.assertTrue(capsule["candidate_may_have_written"])
        self.assertFalse(capsule["old_writer_allowed"])
        self.assertEqual(5, len(capsule["preserved"]["profiles"]))
        manifest = (self.output / "source/MANIFEST.sha256").read_bytes()
        self.assertEqual(capsule["target"]["manifest_sha256"], sha(manifest))
        index = document((self.output / "source-index.json").read_bytes())
        expected_names = sorted(["REVISION", *(item["path"] for item in index["files"])])
        self.assertEqual(
            b"".join(f"./{name}\0".encode() for name in expected_names),
            (self.output / "source/MANIFEST.files0").read_bytes(),
        )
        self.assertEqual(original, {path: sha(path.read_bytes()) for path in original})
        self.assertEqual(
            b"raise RuntimeError('never import candidate')\n",
            (self.output / "source/autostop_manager/mcp_server.py").read_bytes(),
        )

    def test_rehashed_capsule_cannot_grant_activation_or_old_writer_permission(self):
        self.prepare()
        path = self.output / "capsule.json"
        original = path.read_bytes()
        for key in ("activation_authorized", "native_acceptance", "old_writer_allowed"):
            with self.subTest(key=key):
                value = document(original)
                value[key] = True
                value["sha256"] = sha(
                    canonical({name: item for name, item in value.items() if name != "sha256"})
                )
                path.write_bytes(canonical(value))
                with self.assertRaisesRegex(Refusal, "artifact_bytes_changed"):
                    self.verify()
        path.write_bytes(original)
        self.verify()

    def test_tampered_source_and_rehashed_manifest_index_are_rederived_from_git(self):
        self.prepare()
        path = self.output / "source/autostop_manager/mcp_server.py"
        path.write_bytes(b"not the reviewed Git blob\n")
        index = document((self.output / "source-index.json").read_bytes())
        for item in index["files"]:
            if item["path"] == "autostop_manager/mcp_server.py":
                item["sha256"] = sha(path.read_bytes())
                item["size"] = path.stat().st_size
        (self.output / "source-index.json").write_bytes(canonical(index))
        hashes = {item["path"]: item["sha256"] for item in index["files"]}
        hashes["REVISION"] = sha((self.output / "source/REVISION").read_bytes())
        manifest = "".join(f"{hashes[name]}  ./{name}\n" for name in sorted(hashes)).encode()
        (self.output / "source/MANIFEST.sha256").write_bytes(manifest)
        capsule_path = self.output / "capsule.json"
        capsule = document(capsule_path.read_bytes())
        capsule["target"]["manifest_sha256"] = sha(manifest)
        capsule["candidate"]["source_index_sha256"] = sha(canonical(index))
        capsule["sha256"] = sha(
            canonical({key: value for key, value in capsule.items() if key != "sha256"})
        )
        capsule_path.write_bytes(canonical(capsule))
        with self.assertRaisesRegex(Refusal, "artifact_bytes_changed"):
            self.verify()

    def test_extra_file_empty_directory_missing_file_and_alias_are_rejected(self):
        self.prepare()
        extra = self.output / "source/extra"
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(Refusal, "inventory_changed"):
            self.verify()
        extra.unlink()
        extra.mkdir()
        with self.assertRaisesRegex(Refusal, "extra_directory"):
            self.verify()
        extra.rmdir()
        path = self.output / "source/README.md"
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(Refusal, "inventory_changed"):
            self.verify()
        path.write_bytes(raw)
        os.link(path, self.root / "alias")
        with self.assertRaisesRegex(Refusal, "regular_file_required"):
            self.verify()

    def test_verify_rechecks_original_inputs_git_and_ci_instead_of_artifact_digest(self):
        self.prepare()
        manager = Path(self.value["manager"]["checkout"])
        readme = manager / "README.md"
        raw = readme.read_bytes()
        readme.write_bytes(b"uncommitted source\n")
        with self.assertRaisesRegex(Refusal, "checkout_bytes_changed"):
            self.verify()
        readme.write_bytes(raw)
        ci = Path(self.value["manager"]["ci"]["path"])
        original_ci = ci.read_bytes()
        ci.write_bytes(b"changed cached evidence")
        with self.assertRaisesRegex(Refusal, "reference_hash_mismatch"):
            self.verify()
        ci.write_bytes(original_ci)
        original_input = Path(self.inputs["path"])
        original_input.write_bytes(canonical({**self.value, "unknown": True}))
        with self.assertRaisesRegex(Refusal, "reference_hash_mismatch"):
            self.verify()

    def test_actual_raw_policy_bytes_are_required_even_if_all_outer_hashes_are_updated(self):
        policy = self.package["export"]["documents"]["policy"]
        self.package["documents"] = [
            item for item in self.package["documents"] if item["reference"] != policy
        ]
        self.value["predecessor"] = self.put("predecessor/package.json", self.package)
        self.flush()
        with self.assertRaisesRegex(Refusal, "original_bytes_required"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_controller_ci_for_old_code_cannot_cover_current_dirty_compiler(self):
        controller = Path(self.value["controller"]["checkout"])
        (controller / "scripts/j1_manager_transition_source.py").write_bytes(b"# old compiler\n")
        self.git(controller, "add", ".")
        self.git(
            controller,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "Different compiler",
        )
        revision = self.git(controller, "rev-parse", "HEAD")
        self.value["controller"].update(
            revision=revision,
            tree=self.git(controller, "rev-parse", "HEAD^{tree}"),
            ci=self.put("controller-ci.json", self.ci("crm", revision)),
        )
        self.flush()
        with self.assertRaisesRegex(Refusal, "consumer_source_mismatch"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_cached_ci_malformed_nested_repository_is_bounded_json_refusal(self):
        ci = self.ci("manager", self.value["manager"]["revision"])
        ci["run"]["repository"] = []
        self.value["manager"]["ci"] = self.put("manager-ci.json", ci)
        self.flush()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = source.main(
                [
                    "prepare",
                    "--inputs",
                    self.inputs["path"],
                    "--inputs-sha256",
                    self.inputs["sha256"],
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(2, result)
        self.assertEqual(
            "cached_ci_repository_shape", document(stream.getvalue().encode())["error_code"]
        )
        self.assertFalse(self.output.exists())

    def test_output_cannot_be_inside_source_or_replace_an_existing_artifact(self):
        self.output = Path(self.value["manager"]["checkout"]) / "new-output"
        with self.assertRaisesRegex(Refusal, "output_inside_source"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.output = self.root / "artifact"
        self.prepare()
        with self.assertRaisesRegex(Refusal, "new_output_directory_required"):
            self.prepare()

    def test_failed_publish_never_leaves_a_partial_final_directory(self):
        with patch.object(source, "_publish", side_effect=OSError("owned failure")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".artifact-*")))

    def test_failure_after_publication_recovers_by_verifying_the_same_output(self):
        original = source._fsync_directory

        def parent_barrier(path):
            if path == self.output.parent and self.output.exists():
                raise OSError("parent durability outcome unknown")
            original(path)

        with patch.object(source, "_fsync_directory", side_effect=parent_barrier):
            with self.assertRaisesRegex(OSError, "durability outcome unknown"):
                self.prepare()
        self.assertTrue((self.output / "capsule.json").is_file())
        self.assertTrue((self.output / "source/MANIFEST.sha256").is_file())
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = source.main(
                [
                    "verify",
                    "--inputs",
                    self.inputs["path"],
                    "--inputs-sha256",
                    self.inputs["sha256"],
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(0, status)
        result = document(stream.getvalue().encode())
        self.assertTrue(result["ok"])
        self.assertFalse(result["activation_authorized"])
        with self.assertRaisesRegex(Refusal, "new_output_directory_required"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
