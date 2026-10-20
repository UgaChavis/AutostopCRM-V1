"""Bounded private preparation negatives; OS collaborators are explicit doubles."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_catalog_native as catalog_native
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_kernel_supervisor as supervisor
from scripts import j1_forward_cold_producer as producer
from scripts import j1_forward_cold_producer_inputs as inputs
from scripts import j1_forward_cold_producer_native as effectors
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "attempt_id": str(uuid.uuid4()),
            "boot_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "consumer_sha256": producer.consumer_digest(),
            "candidate_crm_sha": "1" * 40,
            "candidate_manager_sha": "2" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": time.monotonic_ns() - 10**9,
            "deadline_ns": time.monotonic_ns() + 20 * 10**9,
        }
        self.request = {
            "attempt_id": self.binding["attempt_id"],
            "boot_id": self.binding["boot_id"],
            "owner_generation": self.binding["generation"],
            "candidate_manager_sha": "2" * 40,
            "candidate": {"revision": "1" * 40},
            "deadline_utc": (datetime.now(UTC) + timedelta(seconds=25)).isoformat(),
        }

    def validate(self, binding=None, **kwargs):
        return producer.validate_binding(
            {"binding": self.binding if binding is None else binding},
            self.request,
            [1, 2],
            boot_id=self.binding["boot_id"],
            **kwargs,
        )

    def test_declared_binding_keeps_original_interval(self):
        self.assertEqual(self.binding, self.validate())

    def test_expiry_D_source_generation_or_boot_change_refuse(self):
        for key, value in (
            ("deadline_ns", time.monotonic_ns() - 1),
            ("deployment_lock_identity", [1, 3]),
            ("consumer_sha256", "0" * 64),
            ("generation", str(uuid.uuid4())),
            ("boot_id", str(uuid.uuid4())),
        ):
            with self.subTest(key=key):
                other = {**self.binding, key: value}
                with self.assertRaises(io.Refusal):
                    self.validate(other)

    def test_deadline_cannot_extend_request(self):
        self.binding["deadline_ns"] = time.monotonic_ns() + 100 * 10**9
        with self.assertRaisesRegex(io.Refusal, "deadline_extension"):
            self.validate()

    def test_http_namespaces_are_fresh_independent_and_bound(self):
        first, second = (
            producer.http_configuration(self.binding),
            producer.http_configuration(self.binding),
        )
        self.assertRegex(first["board_prefix"], r"^/__cold/[0-9a-f]{64}$")
        self.assertRegex(first["mcp_prefix"], r"^/mcp/__cold/[0-9a-f]{64}$")
        self.assertNotEqual(
            first["board_prefix"].rsplit("/", 1)[-1], first["mcp_prefix"].rsplit("/", 1)[-1]
        )
        self.assertEqual(first["board_prefix"], second["board_prefix"])
        next_binding = {**self.binding, "generation": str(uuid.uuid4())}
        self.assertNotEqual(
            first["board_prefix"], producer.http_configuration(next_binding)["board_prefix"]
        )
        self.assertEqual(self.binding["generation"], first["epoch_id"])
        self.assertEqual("https://crm.autostopcrm.ru", first["public_origin"])

    def test_environment_is_not_a_shell_or_python_loader(self):
        self.assertEqual({"KEY": "$(not-executed)"}, inputs.environment(b"KEY=$(not-executed)\n"))
        for raw in (b"HOME=/root\n", b"PYTHONPATH=/tmp\n", b"LD_PRELOAD=x\n", b"KEY=a\nKEY=b\n"):
            with self.assertRaises(io.Refusal):
                inputs.environment(raw)

    def test_production_inputs_require_root_wake_but_preserve_its_qualified_group(self):
        ref = {"path": "/private/source-bound-input.json", "sha256": "3" * 64}
        document = {
            "schema": inputs.SCHEMA,
            "scope": "production_native",
            "binding": self.binding,
            **{
                name: ref
                for name in inputs.FIELDS
                - {
                    "schema",
                    "scope",
                    "binding",
                    "manager",
                    "runtime_env",
                    "credentials",
                    "kernel",
                }
            },
            "manager": {
                "checkout": str(Path(__file__).resolve().parents[1]),
                "revision": "2" * 40,
                "tree": "4" * 40,
                "ci_ref": ref,
            },
            "runtime_env": {role: ref for role in epochs.CONSUMERS},
            "credentials": {
                role: {"uid": 10001, "gid": 10001, "groups": [10001]} for role in epochs.CONSUMERS
            },
            "kernel": {name: ref for name in inputs.KERNEL_FIELDS},
        }
        with self.assertRaisesRegex(io.Refusal, "producer_wake_root_required"):
            inputs.validate(document, self.request)
        document["credentials"]["wake"]["uid"] = 0
        self.assertIs(inputs.validate(document, self.request), document)
        self.assertEqual(
            document["credentials"]["wake"], {"uid": 0, "gid": 10001, "groups": [10001]}
        )
        document["binding"]["candidate_manager_sha"] = "5" * 40
        with self.assertRaisesRegex(io.Refusal, "producer_declared_binding"):
            inputs.validate(document, self.request)

    def test_production_v1_telegram_policy_refuses_before_material_installation(self):
        from scripts import j1_forward_cold_telegram as telegram

        policy = {
            "schema": "autostop.j1.cold-telegram-frontier-policy.v1",
            "scope": "production_native",
            "binding": self.binding,
            "source_revision": self.binding["candidate_manager_sha"],
            "roles": list(telegram.ROLES),
            "cut": telegram.FRONTIER_CUT,
        }
        raw = io.canonical(policy)
        ref = {"path": "/private/frontier-policy.json", "sha256": io.sha(raw)}
        backend = effectors.NativeBackend({}, self.request, 9)
        backend.provider = SimpleNamespace(read_reference=lambda _ref: raw)
        backend.publish = Mock()
        with self.assertRaisesRegex(io.Refusal, "telegram_policy_fields"):
            backend.install_fresh_role_material(
                {"telegram_frontier_policy_ref": ref}, self.binding, {}
            )
        backend.publish.assert_not_called()

    def test_model_manifest_only_accepts_complete_source_pinned_payload_names(self):
        raw = "# qualified Manager manifest\n" + "".join(
            "1" * 64 + "  " + name + "\n" for name in sorted(effectors.MODEL_NAMES)
        )
        self.assertEqual(set(effectors.model_hashes(raw.encode())), effectors.MODEL_NAMES)
        for damaged in (
            raw + "1" * 64 + "  config.json\n",
            raw.replace("model.bin", "other.bin"),
            raw.replace("model.bin", "../model.bin"),
            raw.replace("model.bin\n", "model.bin extra\n"),
            raw.replace("1" * 64, "z" * 64),
        ):
            with self.subTest(damaged=damaged[-80:]), self.assertRaises(io.Refusal):
                effectors.model_hashes(damaged.encode())

    def test_fresh_crm_uses_image_runtime_while_manager_uses_pinned_python_library(self):
        documents = {}

        def publish(path, payload, **_kwargs):
            documents[str(path)] = payload
            return {"path": str(path), "sha256": io.sha(payload)}

        backend = SimpleNamespace(
            directory=Path("/private"),
            provider=SimpleNamespace(
                read_reference=lambda _ref: b"PATH=/usr/bin:/bin\nLD_LIBRARY_PATH=/legacy/runtime\n"
            ),
            publish=publish,
        )
        document = {"runtime_env": {role: {} for role in producer.epochs.CONSUMERS}}
        config_ref = {"path": "/private/http.json", "sha256": "3" * 64}
        result = producer.configure_fresh_http(
            document,
            self.binding,
            producer.http_configuration(self.binding),
            config_ref,
            backend=backend,
        )
        for role, ref in result["runtime_env"].items():
            values = inputs.environment(documents[ref["path"]])
            if role == "crm-embedded":
                self.assertFalse(any(key.startswith("LD_") for key in values))
            else:
                self.assertEqual(
                    "/opt/autostop-manager-cold-releases/"
                    + self.binding["candidate_manager_sha"]
                    + "/python/lib",
                    values["LD_LIBRARY_PATH"],
                )

    def test_crm_push_gate_rejects_other_branches_and_dispatch(self):
        request = {
            **self.request,
            "source": {
                "revision": "1" * 40,
                "tree": "2" * 40,
                "consumers": {"private.py": "3" * 64},
            },
            "qualification_ref": {"path": "/private/qualification.json", "sha256": "4" * 64},
        }
        backend = effectors.NativeBackend({}, request, 9)
        for branch, event, accepted in (
            ("autostopcrm-v1", "push", True),
            ("main", "push", False),
            ("codex/private", "push", False),
            ("autostopcrm-v1", "workflow_dispatch", False),
        ):
            with (
                self.subTest(branch=branch, event=event),
                patch.object(effectors, "_read", return_value={}),
                patch(
                    "scripts.j1_forward_cold_qualification.validate_document",
                    return_value={"checks": {"quality": {"run_id": 11}}},
                ),
                patch(
                    "scripts.j1_forward_cold_qualification._run",
                    return_value={"head_branch": branch, "event": event},
                ),
            ):
                if accepted:
                    backend.verify_crm_production_ci()
                else:
                    with self.assertRaisesRegex(io.Refusal, "production_push_ci"):
                        backend.verify_crm_production_ci()

    def test_production_parent_boundary_precedes_every_effect(self):
        with patch.object(effectors, "NativeBackend") as factory:
            backend = factory.return_value
            backend.provider = SimpleNamespace(scope="production_native")
            backend.boundary.side_effect = io.Refusal("private missing parent E")
            item = producer.NativeProducer({}, self.request, deployment_fd=9)
            with self.assertRaises(io.Refusal):
                item.prepare()
            backend.close_original_ingress.assert_not_called()
            backend.publish.assert_not_called()

    def test_cleanup_also_occurs_after_exception_following_adoption(self):
        with patch.object(effectors, "NativeBackend") as factory:
            backend = factory.return_value
            item = producer.NativeProducer({}, self.request, deployment_fd=9)
            item.prepared = {"private": True}
            backend.adopted = True
            item.__exit__(RuntimeError, RuntimeError("private post-adopt failure"), None)
            backend.close.assert_called_once_with(success=False)

    def test_arbitrary_catalog_path_not_from_original_input_is_denied(self):
        profile = {"input": {"path": "/accepted/input", "sha256": "3" * 64}}
        original = {"source_root": "/accepted/source", "arguments": []}
        source = {
            "kind": "manager_sqlite",
            "path": "/arbitrary/empty.sqlite3",
            "path_binding_ref": profile["input"],
        }
        with patch.object(
            catalog_native,
            "accepted_original_inputs",
            return_value=(profile, original, {"AUTOSTOP_MANAGER_DB": "/accepted/actual.sqlite3"}),
        ):
            with self.assertRaisesRegex(io.Refusal, "not_effective"):
                effectors.verify_catalog_path(
                    "autostop-manager-mcp.service",
                    source,
                    {},
                    provider=SimpleNamespace(scope="production_native"),
                    history={},
                )

    def test_old_crm_stop_lost_reply_never_retries(self):
        # Only the one-shot stop seam is exercised; no Docker/proc is reached.
        with tempfile.TemporaryDirectory(prefix="cold-old-crm-") as directory:
            backend = effectors.NativeBackend({}, self.request, 9)
            backend.directory = Path(directory)
            (backend.directory / "evidence").mkdir()
            backend.binding = self.binding
            backend.provider = SimpleNamespace()
            backend.require_original_marker = Mock()
            backend.evidence = Mock(
                side_effect=lambda name, body: {
                    "path": str(Path(directory) / (name + ".json")),
                    "sha256": io.sha(io.canonical(body)),
                }
            )
            current = {
                "Id": "a" * 64,
                "Config": {"Labels": {"com.docker.compose.project": "autostopcrm"}},
                "State": {"Pid": 111, "Running": True},
            }
            calls = []

            def run(argv, **_kwargs):
                calls.append(argv)
                if "stop" in argv:
                    raise subprocess.TimeoutExpired(argv, 1)
                return io.canonical([current])

            backend.run = run
            history = {"anchor": {"plan_ref": {}, "baseline_ref": {}}}
            with (
                patch.object(
                    effectors, "_read", return_value={"previous_container": {"id": "a" * 64}}
                ),
                patch.object(effectors.native, "birth", return_value=123),
                patch.object(os, "pidfd_open", return_value=41, create=True),
                patch.object(os, "close"),
                patch.object(effectors.select, "select", return_value=([41], [], [])),
                patch.object(effectors, "verify_old_crm_ingress"),
                patch("scripts.j1_forward_host.Host.container", return_value={"id": "a" * 64}),
            ):
                backend.stop_old_crm(self.binding, history, {})
            self.assertEqual(1, sum("stop" in argv for argv in calls))

    def test_one_cleanup_failure_does_not_skip_other_owned_closures(self):
        backend = effectors.NativeBackend({}, self.request, 9)
        backend.close_new_dispatch = Mock(side_effect=io.Refusal("private closure failure"))
        backend.stop_owned_kernel_unit = Mock()
        backend.kernel_start_attempted = True
        backend.kernel_child_pidfd, backend.kernel_pidfd, backend.dispatch_fd = 40, 41, 42
        with (
            patch.object(
                effectors.select,
                "select",
                side_effect=lambda descriptors, *_args: (descriptors, [], []),
            ),
            patch.object(os, "close") as close,
        ):
            with self.assertRaisesRegex(io.Refusal, "closure failure"):
                backend.close(success=False)
        backend.stop_owned_kernel_unit.assert_called_once()
        self.assertEqual({40, 41, 42}, {call.args[0] for call in close.call_args_list})

    def test_adopted_success_closes_descriptors_without_stopping_runtime(self):
        backend = effectors.NativeBackend({}, self.request, 9)
        backend.adopted = True
        backend.close_new_dispatch, backend.stop_owned_kernel_unit = Mock(), Mock()
        backend.kernel_start_attempted = True
        backend.kernel_child_pidfd, backend.kernel_pidfd, backend.dispatch_fd = 40, 41, 42
        with patch.object(os, "close") as close:
            backend.close(success=True)
        backend.close_new_dispatch.assert_not_called()
        backend.stop_owned_kernel_unit.assert_not_called()
        self.assertEqual({40, 41, 42}, {call.args[0] for call in close.call_args_list})

    @patch.object(effectors.native, "STATE", PurePosixPath("/private/cold-state"))
    def test_kernel_custody_needs_actual_public_open_not_only_head_and_complete(self):
        documents = {}

        def reference(path, body):
            raw = io.canonical(body)
            value = {"path": str(path), "sha256": io.sha(raw)}
            documents[str(path)] = raw
            return value

        ready = {"path": "/private/ready.json", "sha256": "4" * 64}
        kernel_ref = reference("/private/kernel.json", {"ready_ref": ready})
        package_ref = reference(
            effectors.native.package_path(self.binding["attempt_id"]),
            {"binding": self.binding, "kernel_ref": kernel_ref},
        )
        epoch_ref = {"path": "/private/epoch.json", "sha256": "5" * 64}
        plan = {
            "base_sha256": "6" * 64,
            "attempt_id": self.binding["attempt_id"],
            "dispatch_epoch_ref": epoch_ref,
            "cold_authority_package_ref": package_ref,
        }
        commit = {"schema": "autostop.j1.crm-forward-commit.v2", "plan": plan}
        history = PurePosixPath("/private/history")
        commit_ref = reference(history / "00000000000000000001.json", commit)
        complete_ref = reference(
            effectors.native.STATE / "epochs" / self.binding["generation"] / "complete.json",
            {
                "ok": True,
                "plan_ref": {"path": "/private/plan.json", "sha256": io.sha(io.canonical(plan))},
                "commit_ref": commit_ref,
                "epoch_ref": epoch_ref,
                "old_hold_released": False,
                "old_barriers_opened": False,
            },
        )
        body = {
            "schema": "autostop.j1.cold-kernel-runtime-custody.v1",
            "scope": "production_native",
            "binding": self.binding,
            "package_ref": package_ref,
            "kernel_ref": kernel_ref,
            "commit_ref": commit_ref,
            "cold_completion_ref": complete_ref,
        }
        custody_ref = reference("/private/custody.json", body)
        store = SimpleNamespace(
            history=history,
            head=lambda: {"sha256": commit_ref["sha256"], "record": commit, "sequence": 1},
            pending=lambda: None,
            _active_recovery=lambda: None,
        )
        actual_open = False

        @contextmanager
        def admitted(_validated):
            yield actual_open

        provider = SimpleNamespace(
            read_reference=lambda ref: documents[ref["path"]], _runtime_kernel_admitted=admitted
        )
        with patch("scripts.j1_forward_cold_state.ColdStore", return_value=store):
            with self.assertRaisesRegex(io.Refusal, "actual_public_open"):
                supervisor.verify_custody(
                    custody_ref, {"binding": self.binding}, ready, provider=provider
                )
            actual_open = True
            self.assertEqual(
                body,
                supervisor.verify_custody(
                    custody_ref, {"binding": self.binding}, ready, provider=provider
                ),
            )
            store.pending = lambda: {"unknown": True}
            with self.assertRaisesRegex(io.Refusal, "durable_completion"):
                supervisor.verify_custody(
                    custody_ref, {"binding": self.binding}, ready, provider=provider
                )


@unittest.skipUnless(os.name == "posix", "Requires private native Linux fixture")
class NativeRoleModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cold-model-material-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.enterContext(rehearsal.PrivateFilesystem(self.root))
        self.uid = 65534 if os.geteuid() == 0 else os.geteuid()
        self.gid = os.getegid()
        self.base = self.root / "role"
        self.base.mkdir(mode=0o700)
        os.chown(self.base, self.uid, self.gid)
        self.source = self.root / "config.json"
        self.source.write_bytes(b"private synthetic model bytes\n")
        self.source.chmod(0o600)
        self.row = {
            "name": "config.json",
            "bytes": self.source.stat().st_size,
            "content_sha256": io.sha(self.source.read_bytes()),
        }
        self.binding = {"deadline_ns": time.monotonic_ns() + 20 * 10**9}
        self.backend = object.__new__(effectors.NativeBackend)
        self.backend.continuity = Mock()

    def install(self, *, work):
        self.backend.install_model_file(
            self.source, self.base, self.row, self.binding, uid=self.uid, gid=self.gid, work=work
        )
        return self.base / "models/faster-whisper-small/config.json"

    def test_work_material_is_system_owned_and_group_readable(self):
        target = self.install(work=True)
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        self.assertEqual((target.stat().st_uid, target.stat().st_gid), (io.ROOT_UID, self.gid))
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(target.parent.stat().st_uid, io.ROOT_UID)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o750)
        self.assertEqual(self.backend.continuity.call_count, 2)
        with self.assertRaises(FileExistsError):
            self.install(work=True)
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_personal_material_keeps_private_owner_and_modes(self):
        target = self.install(work=False)
        self.assertEqual((target.stat().st_uid, target.stat().st_gid), (self.uid, self.gid))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_uid, self.uid)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)

    def test_changed_source_does_not_publish_final_material(self):
        self.row["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "model_source_changed"):
            self.install(work=True)
        target = self.base / "models/faster-whisper-small/config.json"
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backend.continuity.call_count, 1)

    def test_expired_copy_does_not_publish_final_material(self):
        self.binding["deadline_ns"] = time.monotonic_ns() - 1
        with self.assertRaisesRegex(ValueError, "operation_deadline"):
            self.install(work=True)
        target = self.base / "models/faster-whisper-small/config.json"
        self.assertEqual(target.stat().st_size, 0)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_native_directory_ownership_uses_os_primitive(self):
        from scripts import j1_forward_cold_install as install

        target = self.root / "producer-directory"
        self.backend.directory_at(target, uid=os.geteuid(), gid=self.gid)
        self.assertEqual(target.stat().st_uid, os.geteuid())
        other = self.root / "installer-directory"
        installer = install.NativeBackend()
        installer.directory = Mock(side_effect=lambda path: self.assertEqual(path, self.root))
        install.NativeBackend.directory(
            installer, other, mode=0o700, uid=os.geteuid(), gid=self.gid
        )
        self.assertEqual(other.stat().st_mode & 0o777, 0o700)

    def test_first_evidence_initializes_exact_private_control_hierarchy(self):
        # Only ownership maps to this fixture's OS uid. The actual producer
        # directory/publish implementation performs all file and mode checks.
        root, uid, gid = self.root, os.geteuid(), os.getegid()
        actual_fchown = os.fchown

        class PrivateBackend(effectors.NativeBackend):
            def directory_at(self, path, *, mode=0o700, uid=0, gid=0):
                io.require(Path(path).is_relative_to(root), "fixture_evidence_path")
                return super().directory_at(path, mode=mode, uid=os.geteuid(), gid=os.getegid())

            def publish(self, path, body, *, raw=False, mode=0o600, gid=0):
                return super().publish(path, body, raw=raw, mode=mode, gid=os.getegid())

        backend = object.__new__(PrivateBackend)
        state = self.root / "state"
        backend.directory = state / "packages" / str(uuid.uuid4())

        def chown(fd, requested_uid, requested_gid):
            self.assertEqual((requested_uid, requested_gid), (0, gid))
            actual_fchown(fd, uid, gid)

        with patch.object(effectors.native, "STATE", state), patch.object(os, "fchown", chown):
            ref = backend.evidence("generation-custody", {"scope": "native_fixture"})
            before = Path(ref["path"]).read_bytes()
            for path in (
                state,
                state / "packages",
                backend.directory,
                backend.directory / "evidence",
            ):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                self.assertEqual(path.stat().st_uid, uid)
            # This is the next native ingress step's exact mode predicate.
            backend.directory_at(state)
            self.assertEqual(
                backend.evidence("generation-custody", {"scope": "native_fixture"}), ref
            )
            state.chmod(0o755)
            with self.assertRaisesRegex(io.Refusal, "producer_directory_conflict"):
                backend.evidence("later", {"scope": "native_fixture"})
            self.assertEqual(state.stat().st_mode & 0o777, 0o755)
            self.assertFalse((backend.directory / "evidence/later.json").exists())
            self.assertEqual(Path(ref["path"]).read_bytes(), before)

    def test_role_symlink_and_foreign_model_directory_are_refused(self):
        foreign = self.root / "foreign"
        foreign.mkdir(mode=0o700)
        (self.base / "models").symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(OSError):
            self.install(work=True)
        self.assertEqual(list(foreign.iterdir()), [])
