"""Synthetic file-backed gate checks; no native systemd or application acceptance."""

from __future__ import annotations

import copy
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_release_boundary as boundary


class J1ReleaseAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        self.documents = {}
        self.refs = {}
        self.attempt = "89a53aae-20b0-410d-862c-838691f40c6c"
        self.loaded_changes = {}
        self.loaded_calls = []
        repository = self.root / "repository"
        repository.mkdir()
        (repository / "autostop_manager").mkdir()
        (repository / "autostop_manager" / "__init__.py").write_text("# Synthetic source fixture\n")
        (repository / "README.md").write_text(
            "Synthetic acceptance fixture, not a production release.\n"
        )
        self.git(repository, "init", "--quiet")
        self.git(repository, "add", ".")
        self.git(
            repository,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        )
        self.revision = self.git(repository, "rev-parse", "HEAD").strip()
        tree = self.git(repository, "rev-parse", "HEAD^{tree}").strip()
        source_files = boundary.source_files(repository, self.revision)
        self.sources = {
            name: boundary.seal(repository, self.root / name, self.revision, source_files)
            for name in ("manager", "work-telegram", "personal-telegram")
        }
        self.guard_root = self.root / "guard"
        self.guard_root.mkdir()
        self.guard_bytes = b"# Synthetic package identity only; never executed.\n"
        (self.guard_root / "guard.py").write_bytes(self.guard_bytes)
        self.index_bytes = boundary.canonical(
            {"revision": self.revision, "tree": tree, "files": sorted(source_files)}
        )
        (self.guard_root / "source-index.json").write_bytes(self.index_bytes)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.policy_path = self.root / "policy.json"
        self.cache = self.root / "ordinary-cache" / "research.sqlite3"
        self.cache.parent.mkdir()
        self.cache.write_bytes(b"Synthetic stat-only cache; deliberately not SQLite.\n")
        info, parent = self.cache.stat(), self.cache.parent.stat()
        self.cache_identity = {
            "path": str(self.cache),
            "observed_path": str(self.cache),
            "identity": [
                info.st_dev,
                info.st_ino,
                info.st_uid,
                info.st_gid,
                stat.S_IMODE(info.st_mode),
            ],
            "parent_identity": [
                parent.st_dev,
                parent.st_ino,
                parent.st_uid,
                stat.S_IMODE(parent.st_mode),
            ],
        }
        constants = patch.multiple(
            boundary,
            MANAGER_REVISION=self.revision,
            MANAGER_TREE=tree,
            GUARD_ROOT=self.guard_root,
            GUARD_STATE=self.state_dir,
            GUARD_POLICY=self.policy_path,
            CACHE_PATH=self.cache,
            SYSTEMD_ROOT=self.root / "systemd",
            DUTY_CONTROLLER=self.root / "duty" / "restore.py",
            DUTY_INTENT=self.root / "duty" / "intent.json",
            GUARD_SHA256=boundary.digest(self.guard_bytes),
            INDEX_SHA256=boundary.digest(self.index_bytes),
        )
        constants.start()
        self.addCleanup(constants.stop)
        # Test-owned files cannot assert production root ownership. Physical-path,
        # actual byte/hash, schema, source and semantic validators remain real.
        protection = patch.object(boundary, "protected", side_effect=boundary.physical)
        protection.start()
        self.addCleanup(protection.stop)
        self._build_documents(tree)

    @staticmethod
    def git(repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout

    def put(self, name, value, path=None):
        path = path or self.root / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.documents[name] = value
        self.refs[name] = {"path": str(path), "sha256": ""}
        self.flush(name)
        return self.refs[name]

    def flush(self, name):
        raw = boundary.canonical(self.documents[name])
        Path(self.refs[name]["path"]).write_bytes(raw)
        self.refs[name]["sha256"] = boundary.digest(raw)

    def reviewed(self, name, value):
        reviewer = {
            "schema": "autostop.manager.j1-independent-acceptance.v3",
            "scope": boundary.SCOPE,
            "attempt_id": self.attempt,
            "accepted": True,
            "synthetic": True,
            "accepted_refs": list(value["files"]),
        }
        for binding in ("preparation_sha256", "handoff_sha256"):
            if binding in value:
                reviewer[binding] = value[binding]
        ref = self.put(f"reviewer-{name}", reviewer)
        value["reviewer_manifest"] = ref
        value["files"].append(ref)
        return self.put(name, value)

    def group(self, name, kind, values, links, extra_files=()):
        common = {
            "schema": f"autostop.manager.j1-{kind}-acceptance.v3",
            "scope": boundary.SCOPE,
            "attempt_id": self.attempt,
            "handoff_sha256": self.handoff_hash,
            "accepted": True,
        }
        common.update(values)
        common.update({key: self.refs[target] for key, target in links.items()})
        common["files"] = [self.refs[target] for target in links.values()] + list(extra_files)
        return self.reviewed(name, common)

    def _build_documents(self, tree):
        proof = self.put("primary-proof", {"synthetic": True, "no_native_acceptance": True})
        preparation = self.put(
            "preparation",
            {"synthetic": True, "scope": boundary.SCOPE, "attempt_id": self.attempt},
        )
        self.put(
            "wake-config",
            {
                "instruction_revision": boundary.INSTRUCTION_REVISION,
                "instruction_root": boundary.INSTRUCTION_ROOT,
            },
        )
        self.put("thread-identity", {"synthetic": True, "history_preserved": True})
        self.put(
            "daemon-identity",
            {"synthetic": True, "mode": "warm", "process_identity": "fixture-only"},
        )
        self.put(
            "writer-closure",
            {
                "accepted": True,
                "closed_writer_classes": sorted(
                    [
                        "native_units",
                        "detached_daemons",
                        "active_turns",
                        "direct_cli",
                        "crm_gateway",
                        "containers",
                        "scheduled_triggers",
                        "browser",
                    ]
                ),
                "files": [proof],
            },
        )
        self.put("bootstrap", {"start_or_restart_performed": False})
        self.put("browser", {"activation_allowed": False})
        self.lifecycle_links = {
            "wake_config": "wake-config",
            "thread_identity_receipt": "thread-identity",
            "daemon_identity_receipt": "daemon-identity",
            "writer_admission_closure_receipt": "writer-closure",
            "bootstrap_preservation_receipt": "bootstrap",
            "browser_hold_receipt": "browser",
        }
        lifecycle = {
            "instruction_revision": boundary.INSTRUCTION_REVISION,
            "instruction_root": boundary.INSTRUCTION_ROOT,
            "history_preserved": True,
            "denied_units": sorted(boundary.UNSUPPORTED_UNITS),
            "daemon_mode": "accepted_warm",
            "attempt_id": self.attempt,
            "files": [self.refs[name] for name in self.lifecycle_links.values()],
        }
        lifecycle.update({key: self.refs[name] for key, name in self.lifecycle_links.items()})
        self.reviewed(
            "pre-effect",
            {
                **lifecycle,
                "files": list(lifecycle["files"]),
                "schema": "autostop.manager.j1-lifecycle-eligibility.v3",
                "scope": boundary.SCOPE,
                "preparation_sha256": preparation["sha256"],
                "accepted": True,
            },
        )
        self.put(
            "closure", {"synthetic": True, "attempt_id": self.attempt, "work_inbound_before": True}
        )
        native = {unit: True for unit in boundary.NATIVE_UNITS}
        boundary.DUTY_CONTROLLER.parent.mkdir()
        boundary.DUTY_CONTROLLER.write_bytes(b"# Synthetic fixed controller, never executed.\n")
        controller_ref = {
            "path": str(boundary.DUTY_CONTROLLER),
            "sha256": boundary.file_hash(boundary.DUTY_CONTROLLER),
        }
        intent_ref = self.put(
            "work-duty-intent",
            {
                "schema": "autostop.crm.j1-work-duty-intent.v1",
                "scope": boundary.SCOPE,
                "attempt_id": self.attempt,
                "preparation_sha256": preparation["sha256"],
                "preparation_path": preparation["path"],
                "closure_sha256": self.refs["closure"]["sha256"],
                "crm_revision": "c" * 40,
                "manager_revision": self.revision,
                "inbound_enabled": True,
                "controller_sha256": controller_ref["sha256"],
            },
            boundary.DUTY_INTENT,
        )
        duty_dropin = (
            boundary.SYSTEMD_ROOT / "autostop-work-telegram.service.d" / boundary.DUTY_DROPIN
        )
        duty_dropin.parent.mkdir(parents=True)
        duty_dropin.write_bytes(
            (
                "[Service]\nExecStartPost=\nExecStartPost=+"
                + boundary.duty_command(intent_ref["sha256"])
                + "\n"
            ).encode()
        )
        self.duty_files = [
            controller_ref,
            intent_ref,
            {"path": str(duty_dropin), "sha256": boundary.file_hash(duty_dropin)},
        ]
        self.handoff = {
            "schema": "autostop.crm.j1-activation-handoff.v1",
            "scope": boundary.SCOPE,
            "attempt_id": self.attempt,
            "preparation_sha256": preparation["sha256"],
            "crm": {"revision": "c" * 40},
            "work_duty_intent": intent_ref,
            "work_duty_controller_sha256": controller_ref["sha256"],
            "sources": self.sources,
            "closure_sha256": self.refs["closure"]["sha256"],
            "pre_effect_lifecycle": self.refs["pre-effect"],
            "desired_native": native,
            "desired_native_enabled": dict(native),
            "files": [proof, *self.duty_files],
        }
        self.put("handoff", self.handoff)
        self.handoff_hash = self.refs["handoff"]["sha256"]
        profiles = {}
        modules = {
            "autostop-j1.service": ("j1_research", ["worker"], "writer"),
            "autostop-manager-mcp.service": ("mcp_server", [], "writer"),
            "autostop-manager-scheduler.service": ("automation_daemon", ["daemon"], "writer"),
            "autostop-work-telegram.service": (
                "telegram_bridge",
                ["--account", "work", "daemon"],
                "client",
            ),
            "autostop-telegram.service": (
                "telegram_bridge",
                ["--account", "personal", "daemon"],
                "client",
            ),
            "autostop-codex-wake.service": ("telegram_wake", ["daemon"], "writer"),
        }
        for unit, (module, arguments, access) in modules.items():
            source_name = "manager"
            if unit in {"autostop-work-telegram.service", "autostop-codex-wake.service"}:
                source_name = "work-telegram"
            elif unit == "autostop-telegram.service":
                source_name = "personal-telegram"
            source = self.sources[source_name]
            environment = {
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONPATH": source["path"],
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONSAFEPATH": "1",
                "PYTHONNOUSERSITE": "1",
                "AUTOSTOP_MANAGER_ENV_FILE": "/dev/null",
                "AUTOSTOP_J1_CACHE_DIR": str(self.cache.parent),
            }
            if unit == "autostop-work-telegram.service":
                environment.update(
                    AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING="0",
                    AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX="1",
                )
            environment_hash = boundary.digest(boundary.canonical(environment))
            runtime = self.put(
                f"runtime-{unit}",
                {
                    "schema": "autostop.j1.runtime-acceptance.v2",
                    "scope": boundary.SCOPE,
                    "revision": self.revision,
                    "source_root": source["path"],
                    "synthetic": True,
                    "stable_input_environment": environment,
                    "stable_input_environment_sha256": environment_hash,
                },
            )
            profiles[unit] = {
                "source_root": source["path"],
                "source_identity": source["identity"],
                "manifest_sha256": source["manifest_sha256"],
                "module": f"autostop_manager.{module}",
                "arguments": arguments,
                "access": access,
                "ordinary_database": copy.deepcopy(self.cache_identity),
                "runtime_receipt": runtime["path"],
                "runtime_files": [runtime],
                "unit_files": list(self.duty_files)
                if unit == "autostop-work-telegram.service"
                else [],
                "stable_environment_sha256": environment_hash,
            }
        self.put(
            "policy",
            {
                "schema": "autostop.j1.candidate-policy.v2",
                "revision": self.revision,
                "tree": tree,
                "guard_sha256": boundary.GUARD_SHA256,
                "index_sha256": boundary.INDEX_SHA256,
                "attempt_id": self.attempt,
                "host": boundary.HOST,
                "bindings": profiles,
            },
            self.policy_path,
        )
        quiet = {
            "schema": "autostop.j1.quiet-window-acceptance.v2",
            "scope": boundary.SCOPE,
            "attempt_id": self.attempt,
            "policy_sha256": self.refs["policy"]["sha256"],
        }
        quiet.update(
            {
                key: True
                for key in (
                    "all_writers_closed",
                    "detached_daemons_and_turns_closed",
                    "direct_cli_closed",
                    "snapshot_verified",
                    "offhost_disposable_restore_verified",
                    "effective_units_and_runtime_accepted",
                    "rollback_fence_accepted",
                )
            }
        )
        self.put("quiet_window", quiet)
        self.put(
            "state",
            {
                "schema": "autostop.j1.candidate-state.v2",
                "scope": boundary.SCOPE,
                "attempt_id": self.attempt,
                "policy_sha256": self.refs["policy"]["sha256"],
                "guard_sha256": boundary.GUARD_SHA256,
                "index_sha256": boundary.INDEX_SHA256,
                "revision": self.revision,
                "candidate_may_have_written": True,
                "old_writer_allowed": False,
                "prerequisite_sha256": self.refs["quiet_window"]["sha256"],
            },
            self.state_dir / "armed.json",
        )
        artifact = self.root / "snapshot" / "research.sqlite3"
        artifact.parent.mkdir()
        artifact.write_bytes(b"Synthetic snapshot bytes; no SQLite connection is performed.\n")
        self.artifact = artifact
        artifact_hash = boundary.file_hash(artifact)
        self.put(
            "snapshot-manifest",
            {
                "schema": "autostop.j1.admin-snapshot.v1",
                "target_manager_revision": self.revision,
                "source_identity": {"path": str(self.cache)},
                "inspection": {"sqlite_integrity": "ok", "foreign_key_check": "ok"},
                "fts_integrity_check": "passed_on_disposable_staging_copy",
                "artifact": {
                    "name": "research.sqlite3",
                    "bytes": artifact.stat().st_size,
                    "sha256": artifact_hash,
                },
            },
            artifact.parent / "manifest.json",
        )
        self.put(
            "snapshot-receipt",
            {
                "manifest_sha256": self.refs["snapshot-manifest"]["sha256"],
                "artifact_sha256": artifact_hash,
            },
        )
        self.put(
            "quiet-inventory",
            {
                "closure_sha256": self.handoff["closure_sha256"],
                "continuous_closure_through_arm": True,
            },
        )
        self.group(
            "checkpoint",
            "checkpoint",
            {
                "checkpoint_id": "synthetic-quiet-checkpoint",
                "source_cache": copy.deepcopy(self.cache_identity),
            },
            {
                "snapshot_manifest": "snapshot-manifest",
                "quiet_inventory": "quiet-inventory",
                "snapshot_receipt": "snapshot-receipt",
            },
            extra_files=[{"path": str(artifact), "sha256": artifact_hash}],
        )
        self.put(
            "offhost-archive",
            {"synthetic": True, "archive_sha256": artifact_hash, "offhost_readback_verified": True},
        )
        self.put("offhost-integrity", {"synthetic": True, "sqlite_integrity": "ok"})
        self.put(
            "disposable-restore",
            {
                "snapshot_manifest_sha256": self.refs["snapshot-manifest"]["sha256"],
                "sqlite_integrity": "ok",
                "fts_integrity": "ok",
                "retained_data_verified": True,
                "production_restore_performed": False,
            },
        )
        self.group(
            "offhost_restore",
            "offhost-restore",
            {
                "checkpoint_id": "synthetic-quiet-checkpoint",
                "snapshot_manifest_sha256": self.refs["snapshot-manifest"]["sha256"],
            },
            {
                "offhost_archive": "offhost-archive",
                "integrity_receipt": "offhost-integrity",
                "disposable_restore_receipt": "disposable-restore",
            },
        )
        self.put(
            "native-baseline",
            {"native_active": copy.deepcopy(native), "native_enabled": dict(native)},
        )
        self.put("effective-units", {"synthetic": True, "units": sorted(boundary.ALL_UNITS)})
        self.put("source-modes", {"synthetic": True, "sources": self.sources})
        self.put(
            "rollback-fence", {"old_writer_allowed": False, "controller_death_test_passed": True}
        )
        self.group(
            "runtime_units",
            "runtime-unit",
            {
                "policy_sha256": self.refs["policy"]["sha256"],
                "source_index_sha256": boundary.INDEX_SHA256,
                "sources": self.sources,
                "profiles": profiles,
            },
            {
                "baseline_desired_state_receipt": "native-baseline",
                "effective_units_receipt": "effective-units",
                "source_modes_receipt": "source-modes",
                "rollback_fence_receipt": "rollback-fence",
            },
        )
        self.group(
            "lifecycle",
            "lifecycle",
            {**lifecycle, "pre_effect_acceptance": self.refs["pre-effect"]},
            self.lifecycle_links,
        )
        self.put(
            "response",
            {
                "schema": "autostop.crm.j1-activation-acceptance.v1",
                "scope": boundary.SCOPE,
                "attempt_id": self.attempt,
                "handoff_sha256": self.handoff_hash,
                "evidence": {
                    name: self.refs[name]
                    for name in (
                        "policy",
                        "state",
                        "quiet_window",
                        "checkpoint",
                        "offhost_restore",
                        "runtime_units",
                        "lifecycle",
                    )
                },
            },
        )
        self.response = Path(self.refs["response"]["path"])
        self.refresh()

    def refresh(self):
        """Resign changed fixture files without bypassing any production validator."""
        for name in self.documents:
            if "handoff_sha256" in self.documents[name]:
                self.documents[name]["handoff_sha256"] = self.handoff_hash
            if name == "quiet_window":
                self.documents[name]["policy_sha256"] = self.refs["policy"]["sha256"]
            elif name == "state":
                self.documents[name]["policy_sha256"] = self.refs["policy"]["sha256"]
                self.documents[name]["prerequisite_sha256"] = self.refs["quiet_window"]["sha256"]
            elif name == "snapshot-receipt":
                self.documents[name]["manifest_sha256"] = self.refs["snapshot-manifest"]["sha256"]
            elif name in {"offhost_restore", "disposable-restore"}:
                self.documents[name]["snapshot_manifest_sha256"] = self.refs["snapshot-manifest"][
                    "sha256"
                ]
            elif name == "runtime_units":
                self.documents[name]["policy_sha256"] = self.refs["policy"]["sha256"]
            self.flush(name)
            if name == "handoff":
                self.handoff_hash = self.refs[name]["sha256"]

    def systemctl_show(self, arguments, **kwargs):
        self.assertEqual(
            kwargs, {"check": True, "capture_output": True, "text": True, "timeout": 10}
        )
        self.assertEqual(arguments[:2], ["systemctl", "show"])
        unit = arguments[2]
        self.assertIn(unit, boundary.ALL_UNITS)
        self.assertEqual(
            arguments[3:],
            [
                "--property=NeedDaemonReload,ExecCondition,ExecStart,ExecStartPre,ExecStartPost,ExecReload,ExecStop,ExecStopPost",
                "--no-pager",
            ],
        )
        self.loaded_calls.append(unit)
        prefix = f"/usr/bin/python3 -I -S {self.guard_root / 'guard.py'}"
        policy_hash = self.refs["policy"]["sha256"]
        fields = {
            "NeedDaemonReload": "no",
            "ExecCondition": f"{{ argv[]={prefix} check --policy-sha256 {policy_hash} --unit {unit} ; }}",
            "ExecStart": f"{{ argv[]={prefix} launch --policy-sha256 {policy_hash} --unit {unit} ; }}",
        }
        fields.update(
            {
                key: ""
                for key in (
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecReload",
                    "ExecStop",
                    "ExecStopPost",
                )
            }
        )
        if unit == "autostop-work-telegram.service":
            fields["ExecStartPost"] = (
                "{ argv[]="
                + boundary.duty_command(self.refs["work-duty-intent"]["sha256"])
                + " ; }"
            )
        fields.update(self.loaded_changes)
        return subprocess.CompletedProcess(
            arguments, 0, "\n".join(f"{key}={value}" for key, value in fields.items()), ""
        )

    def validate(self):
        with patch.object(boundary.subprocess, "run", side_effect=self.systemctl_show):
            boundary.verify_acceptance(self.handoff, self.handoff_hash, self.response)

    def refuse(self, error):
        with self.assertRaisesRegex(boundary.BoundaryError, error):
            self.validate()

    def test_file_backed_synthetic_acceptance_runs_all_validators(self):
        boundary.lifecycle_proofs(boundary.evidence(self.refs["pre-effect"]))
        self.assertNotEqual(
            self.refs["reviewer-pre-effect"]["path"], self.refs["reviewer-lifecycle"]["path"]
        )
        self.validate()
        self.assertEqual(self.loaded_calls, sorted(boundary.ALL_UNITS))
        self.assertEqual(
            self.cache.read_bytes(), b"Synthetic stat-only cache; deliberately not SQLite.\n"
        )

    def test_pending_arm_refuses_even_valid_state(self):
        (self.state_dir / "arming-pending.json").write_text("{}")
        self.refuse("guard_arm_pending")

    def test_unresigned_proof_tampering_refuses(self):
        Path(self.refs["quiet-inventory"]["path"]).write_text("{}")
        self.refuse("checkpoint_evidence_changed")

    def test_source_tampering_reaches_real_sealed_source_validator(self):
        (Path(self.sources["manager"]["path"]) / "README.md").write_text("changed")
        self.refuse("source_content_changed")
        self.assertEqual(self.loaded_calls, sorted(boundary.ALL_UNITS))

    def test_wrong_response_scope_and_attempt_refuse(self):
        for key, value in (
            ("scope", "synthetic"),
            ("attempt_id", "other-attempt"),
            ("handoff_sha256", "0" * 64),
        ):
            with self.subTest(key=key):
                original = self.documents["response"][key]
                self.documents["response"][key] = value
                self.flush("response")
                self.refuse("acceptance_binding_mismatch")
                self.documents["response"][key] = original

    def test_wrong_policy_revision_and_source_refuse(self):
        self.documents["policy"]["revision"] = "0" * 40
        self.refresh()
        self.refuse("guard_policy_mismatch")
        self.documents["policy"]["revision"] = self.revision
        self.documents["policy"]["bindings"]["autostop-j1.service"]["source_root"] = str(
            self.root / "old"
        )
        self.refresh()
        self.refuse("guard_source_binding_mismatch")

    def test_wrong_or_missing_policy_host_refuses(self):
        for value in ("other-host.invalid", None):
            with self.subTest(host=value):
                if value is None:
                    self.documents["policy"].pop("host")
                else:
                    self.documents["policy"]["host"] = value
                self.refresh()
                self.refuse("guard_policy_mismatch")

    def test_work_startup_requires_paused_durable_environment(self):
        binding = self.documents["policy"]["bindings"]["autostop-work-telegram.service"]
        receipt = self.documents["runtime-autostop-work-telegram.service"]
        original = dict(receipt["stable_input_environment"])
        for key, value in (
            ("AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING", "1"),
            ("AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING", None),
            ("AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX", None),
            ("AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX", "0"),
        ):
            with self.subTest(key=key, value=value):
                environment = dict(original)
                if value is None:
                    del environment[key]
                else:
                    environment[key] = value
                receipt["stable_input_environment"] = environment
                # Preserve valid hashes to reach the actual startup policy rule.
                environment_hash = boundary.digest(boundary.canonical(environment))
                receipt["stable_input_environment_sha256"] = environment_hash
                binding["stable_environment_sha256"] = environment_hash
                self.refresh()
                self.refuse("work_paused_durable_startup_required")

    def test_work_duty_controller_must_be_bound_in_guard_and_handoff(self):
        binding = self.documents["policy"]["bindings"]["autostop-work-telegram.service"]
        complete = list(binding["unit_files"])
        for missing in complete:
            with self.subTest(missing=missing["path"]):
                binding["unit_files"] = [item for item in complete if item != missing]
                self.refresh()
                self.refuse("work_duty_guard_inventory_required")
        binding["unit_files"] = complete
        self.handoff["work_duty_controller_sha256"] = "0" * 64
        self.refresh()
        self.refuse("work_duty_intent_binding_required")

    def test_work_duty_controller_bytes_cannot_be_substituted(self):
        boundary.DUTY_CONTROLLER.write_bytes(b"arbitrary executable replacement")
        self.refuse("work_duty_controller_changed")

    def test_work_startup_environment_and_hash_substitution_refuse(self):
        binding = self.documents["policy"]["bindings"]["autostop-work-telegram.service"]
        receipt = self.documents["runtime-autostop-work-telegram.service"]
        original = dict(receipt["stable_input_environment"])
        original_hash = binding["stable_environment_sha256"]
        for substitution in ("environment", "binding_hash", "receipt_hash", "not_a_map", "boolean"):
            with self.subTest(substitution=substitution):
                receipt["stable_input_environment"] = dict(original)
                receipt["stable_input_environment_sha256"] = original_hash
                binding["stable_environment_sha256"] = original_hash
                if substitution == "environment":
                    receipt["stable_input_environment"]["PATH"] = "/unaccepted/bin"
                elif substitution == "binding_hash":
                    binding["stable_environment_sha256"] = "0" * 64
                elif substitution == "receipt_hash":
                    receipt["stable_input_environment_sha256"] = "0" * 64
                else:
                    if substitution == "not_a_map":
                        receipt["stable_input_environment"] = list(original.items())
                    else:
                        receipt["stable_input_environment"][
                            "AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX"
                        ] = False
                    environment_hash = boundary.digest(
                        boundary.canonical(receipt["stable_input_environment"])
                    )
                    receipt["stable_input_environment_sha256"] = environment_hash
                    binding["stable_environment_sha256"] = environment_hash
                self.refresh()
                self.refuse("work_startup_environment_binding_required")

    def test_work_startup_receipt_must_be_in_runtime_files(self):
        binding = self.documents["policy"]["bindings"]["autostop-work-telegram.service"]
        for files in ([], [self.refs["primary-proof"]]):
            with self.subTest(files=files):
                binding["runtime_files"] = files
                self.refresh()
                self.refuse("work_startup_receipt_hash_required")

    def test_work_startup_receipt_hash_substitution_refuses(self):
        binding = self.documents["policy"]["bindings"]["autostop-work-telegram.service"]
        binding["runtime_files"] = [dict(binding["runtime_files"][0], sha256="0" * 64)]
        self.refresh()
        self.refuse("hash_mismatch")

    def test_work_startup_receipt_bytes_tampering_refuses(self):
        Path(self.refs["runtime-autostop-work-telegram.service"]["path"]).write_bytes(b"{}\n")
        self.refuse("hash_mismatch")

    def test_work_startup_receipt_file_absence_refuses(self):
        Path(self.refs["runtime-autostop-work-telegram.service"]["path"]).unlink()
        with self.assertRaises(FileNotFoundError):
            self.validate()
        self.assertEqual([], self.loaded_calls)

    def test_unrelated_reviewer_refs_refuse_for_every_final_proof(self):
        for subject in ("checkpoint", "offhost_restore", "runtime_units", "lifecycle"):
            with self.subTest(subject=subject):
                reviewer = self.documents[f"reviewer-{subject}"]
                original = reviewer["accepted_refs"]
                reviewer["accepted_refs"] = [self.refs["primary-proof"]]
                self.refresh()
                self.refuse("reviewer_does_not_accept_exact_inputs")
                reviewer["accepted_refs"] = original

    def test_reviewer_requires_snapshot_artifact_and_exact_input_hash(self):
        reviewer = self.documents["reviewer-checkpoint"]
        original = reviewer["accepted_refs"]
        for refs in (
            [ref for ref in original if ref["path"] != str(self.artifact)],
            [dict(ref, sha256="0" * 64) for ref in original],
        ):
            with self.subTest(accepted_refs=refs):
                reviewer["accepted_refs"] = refs
                self.refresh()
                self.refuse("reviewer_does_not_accept_exact_inputs")

    def test_final_reviewer_identity_and_handoff_binding_refuse(self):
        reviewer = self.documents["reviewer-checkpoint"]
        for key, value, error in (
            ("schema", "unrelated.acceptance", "independent_reviewer_required"),
            ("scope", "synthetic", "independent_reviewer_required"),
            ("attempt_id", "other-attempt", "independent_reviewer_required"),
            ("accepted", False, "independent_reviewer_required"),
            ("handoff_sha256", "0" * 64, "reviewer_context_mismatch"),
        ):
            with self.subTest(key=key):
                original = reviewer[key]
                reviewer[key] = value
                self.flush("reviewer-checkpoint")
                self.flush("checkpoint")
                self.flush("response")
                self.refuse(error)
                reviewer[key] = original

    def test_pre_effect_reviewer_requires_preparation_and_exact_inputs(self):
        reviewer = self.documents["reviewer-pre-effect"]
        for key, value, error in (
            ("preparation_sha256", "0" * 64, "reviewer_context_mismatch"),
            (
                "accepted_refs",
                [self.refs["primary-proof"]],
                "reviewer_does_not_accept_exact_inputs",
            ),
        ):
            with self.subTest(key=key):
                original = reviewer[key]
                reviewer[key] = value
                self.flush("reviewer-pre-effect")
                self.flush("pre-effect")
                with self.assertRaisesRegex(boundary.BoundaryError, error):
                    boundary.lifecycle_proofs(boundary.evidence(self.refs["pre-effect"]))
                reviewer[key] = original

    def test_state_scope_attempt_policy_and_old_writer_allow_refuse(self):
        for key, value in (
            ("scope", "synthetic"),
            ("attempt_id", "other"),
            ("policy_sha256", "0" * 64),
            ("old_writer_allowed", True),
            ("candidate_may_have_written", False),
        ):
            with self.subTest(key=key):
                self.refresh()
                original = self.documents["state"][key]
                self.documents["state"][key] = value
                self.flush("state")
                self.flush("response")
                self.refuse("immutable_guard_state_required")
                self.documents["state"][key] = original

    def test_quiet_window_false_flag_refuses(self):
        self.documents["quiet_window"]["all_writers_closed"] = False
        self.refresh()
        self.refuse("quiet_window_not_accepted")

    def test_wrong_cache_path_identity_and_policy_cache_refuse(self):
        cache = self.documents["checkpoint"]["source_cache"]
        original = copy.deepcopy(cache)
        cache["path"] = str(self.root / "vin-cache.sqlite3")
        self.refresh()
        self.refuse("ordinary_cache_identity_required")
        cache.update(copy.deepcopy(original))
        cache["identity"][1] += 1
        self.refresh()
        self.refuse("checkpoint_cache_identity_changed")
        cache.update(copy.deepcopy(original))
        self.documents["policy"]["bindings"]["autostop-j1.service"]["ordinary_database"][
            "identity"
        ][1] += 1
        self.refresh()
        self.refuse("policy_checkpoint_cache_mismatch")

    def test_snapshot_integrity_revision_and_artifact_refuse(self):
        snapshot = self.documents["snapshot-manifest"]
        snapshot["inspection"]["sqlite_integrity"] = "corrupt"
        self.refresh()
        self.refuse("verified_ordinary_snapshot_required")
        snapshot["inspection"]["sqlite_integrity"] = "ok"
        snapshot["target_manager_revision"] = "0" * 40
        self.refresh()
        self.refuse("verified_ordinary_snapshot_required")
        snapshot["target_manager_revision"] = self.revision
        snapshot["artifact"]["bytes"] += 1
        self.refresh()
        self.refuse("snapshot_artifact_not_verified")

    def test_snapshot_bytes_tampering_refuses(self):
        self.artifact.write_bytes(b"tampered snapshot")
        self.refuse("checkpoint_evidence_changed")

    def test_quiet_snapshot_closure_linkage_refuses(self):
        self.documents["quiet-inventory"]["closure_sha256"] = "0" * 64
        self.refresh()
        self.refuse("quiet_snapshot_linkage_required")
        self.documents["quiet-inventory"]["closure_sha256"] = self.handoff["closure_sha256"]
        self.documents["quiet-inventory"]["continuous_closure_through_arm"] = False
        self.refresh()
        self.refuse("quiet_snapshot_linkage_required")

    def test_wrong_checkpoint_and_offhost_restore_refuse(self):
        self.documents["offhost_restore"]["checkpoint_id"] = "wrong-checkpoint"
        self.refresh()
        self.refuse("offhost_checkpoint_mismatch")
        self.documents["offhost_restore"]["checkpoint_id"] = self.documents["checkpoint"][
            "checkpoint_id"
        ]
        self.documents["disposable-restore"]["production_restore_performed"] = True
        self.refresh()
        self.refuse("exact_disposable_restore_required")

    def test_runtime_baseline_and_rollback_proof_refuse(self):
        self.documents["native-baseline"]["native_active"]["autostop-telegram.service"] = False
        self.refresh()
        self.refuse("native_baseline_changed")
        self.documents["native-baseline"]["native_active"]["autostop-telegram.service"] = True
        self.documents["rollback-fence"]["old_writer_allowed"] = True
        self.refresh()
        self.refuse("rollback_fence_acceptance_required")

    def test_null_previously_active_role_refuses(self):
        for unit in boundary.NATIVE_UNITS:
            with self.subTest(unit=unit):
                original = self.documents["policy"]["bindings"][unit]
                self.documents["policy"]["bindings"][unit] = None
                self.refresh()
                self.refuse("required_profile_unbound")
                self.documents["policy"]["bindings"][unit] = original

    def test_cold_lifecycle_and_instruction_drift_refuse(self):
        self.documents["lifecycle"]["daemon_mode"] = "accepted_cold"
        self.refresh()
        self.refuse("explicit_warm_lifecycle_required")
        self.documents["lifecycle"]["daemon_mode"] = "accepted_warm"
        self.documents["wake-config"]["instruction_revision"] = "0" * 40
        self.refresh()
        self.refuse("wake_instruction_pin_changed")

    def test_missing_writer_class_and_bootstrap_start_refuse(self):
        self.documents["writer-closure"]["closed_writer_classes"].remove("direct_cli")
        self.refresh()
        self.refuse("all_writer_admission_closure_required")
        self.documents["writer-closure"]["closed_writer_classes"].append("direct_cli")
        self.documents["writer-closure"]["closed_writer_classes"].sort()
        self.documents["bootstrap"]["start_or_restart_performed"] = True
        self.refresh()
        self.refuse("bootstrap_start_forbidden")

    def test_changed_pre_effect_lifecycle_refuses(self):
        self.documents["lifecycle"]["pre_effect_acceptance"] = self.refs["primary-proof"]
        self.refresh()
        self.refuse("pre_effect_lifecycle_changed")

    def test_stale_loaded_guard_wrong_dispatch_and_side_effects_refuse(self):
        for key, value in (
            ("NeedDaemonReload", "yes"),
            ("ExecStart", "{ argv[]=/bin/true ; }"),
            ("ExecCondition", "{ argv[]=/bin/true ; }"),
            ("ExecStopPost", "{ argv[]=/bin/false ; }"),
        ):
            with self.subTest(key=key):
                self.loaded_changes = {key: value}
                self.refuse("loaded_guard_dispatch_required")


if __name__ == "__main__":
    unittest.main()
