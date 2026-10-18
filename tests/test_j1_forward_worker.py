"""Workstation protocol tests: no SSH calls and no production credentials."""

from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_host as host_policy
from scripts import j1_forward_io as io
from scripts import j1_forward_process_scope as process_scope
from scripts import j1_forward_proofs as proofs
from scripts import j1_forward_state as state
from scripts import j1_forward_transport as transport
from scripts import j1_forward_worker as worker
from tests.test_j1_forward_proofs import _openssl


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.attempt = "33220918-5be9-48c1-9a6e-122045f3a4ac"
        self.prefix = worker.ATTEMPTS + "/" + self.attempt + "/"
        self.instance = object.__new__(worker.Worker)
        self.instance.ssh_key = self.root / "ssh-key"
        self.instance.reviewer_key = self.root / "reviewer.pem"
        self.instance.cache = self.cache
        self.instance.ssh = self.root / "ssh.exe"
        self.instance.openssl = self.root / "openssl.exe"
        self.public = b"test fixture public bytes"
        custody = patch.object(worker, "custody", lambda path, **_: Path(path))
        custody.start()
        self.addCleanup(custody.stop)

    def ref(self, name, value):
        return {"path": self.prefix + name, "sha256": io.sha(io.canonical(value))}

    def response(self, stage="closed"):
        now = datetime.now(UTC)
        consumers = {
            "scripts/" + path.name: io.sha(path.read_bytes())
            for path in Path(worker.__file__).parent.glob("j1_forward_*.py")
        }
        review = {"path": proofs.REVIEW_KEY_PATH, "sha256": io.sha(self.public)}
        baseline = {
            "schema": "autostop.j1.crm-forward-baseline.v1",
            "attempt_id": self.attempt,
            "native": {
                "base_sha256": "1" * 64,
                "sha256": "2" * 64,
                "warm_actors": [self.actor(pid) for pid in process_scope.ORIGINAL],
                "profiles": {
                    "autostop-codex-wake.service": {
                        "interpreter": "/usr/bin/python3",
                        "interpreter_realpath": "/usr/bin/python3.12",
                        "interpreter_sha256": "8" * 64,
                        "module": "fixture.wake",
                        "arguments": [],
                        "credentials": [0, 0, [0]],
                    }
                },
            },
            "host": {},
            "previous_container": {},
            "source_consumers": consumers,
            "review_key_ref": review,
            "backup_profile_sha256": transport.PROFILE_SHA256,
            "deployment_lock_identity": [1, 2],
            "ci": {
                "run_id": 1234,
                "revision": "3" * 40,
                "run_url": "https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/1234",
                "jobs": worker.CI_JOBS,
                "conclusion": "success",
            },
        }
        baseline_ref = self.ref("baseline.json", baseline)
        plan = {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": worker.NATIVE_HOST,
            "attempt_id": self.attempt,
            "sequence": 1,
            "predecessor_sha256": "1" * 64,
            "base_sha256": "1" * 64,
            "candidate": {
                "revision": "3" * 40,
                "tree": "4" * 40,
                "image": "sha256:" + "5" * 64,
                "consumer_sha256": io.sha(io.canonical(consumers)),
            },
            "baseline_ref": baseline_ref,
            "created_at": (now - timedelta(minutes=1)).isoformat(),
            "deadline_utc": (now + timedelta(minutes=10)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        plan_ref = self.ref("plan.json", plan)
        evidence = {
            "native": {
                "ok": True,
                "baseline_sha256": baseline["native"]["sha256"],
                "running": stage == "activated",
                "closed": stage == "closed",
            }
        }
        inspection = {
            "schema": worker.INSPECTION_SCHEMA,
            "stage": stage,
            "attempt_id": self.attempt,
            "plan_sha256": plan_ref["sha256"],
            "baseline_sha256": baseline_ref["sha256"],
            "candidate": plan["candidate"],
            "native_preserved": True,
        }
        scope = self.scope(plan, baseline["native"])
        scope_ref = self.ref("process-scope.json", scope)
        evidence["process_scope"] = scope
        staged = {
            "schema": "autostop.j1.crm-forward-process-scope-staging.v1",
            "scope_sha256": scope_ref["sha256"],
            "auxiliary_released": False,
            "freeze_actors": [
                {
                    **row,
                    "stopped": row["pid"] != 538285,
                    "thread_states": ["S"] if row["pid"] == 538285 else ["T", "T"],
                }
                for row in scope["freeze_actors"]
            ],
            "transport_actors": [
                {**row, "stopped": False, "thread_states": ["S"]}
                for row in scope["transport_actors"]
            ],
            "native_control_peer": None,
            "topology_sha256": io.sha(io.canonical(scope["observation"])),
        }
        evidence["ingress_idle"] = {
            "observed_at": (now - timedelta(seconds=15)).isoformat(),
            "loaded_count": 7,
            "active_turns": 0,
            "all_loaded_and_wake_idle": True,
            "metadata_only": True,
            "rpc_sha256": host_policy.RPC_SHA,
            "wake_sha256": host_policy.WAKE_SHA,
        }
        idle_filename = "ingress-idle-" + "a" * 32 + ".json"
        evidence["ingress_drain"] = {
            "schema": "autostop.j1.crm-forward-ingress-drain.v1",
            "attempt_id": self.attempt,
            "process_scope_ref": scope_ref,
            "idle_observation_ref": self.ref(idle_filename, evidence["ingress_idle"]),
            "staging_observation": staged,
            "observed_at": (now - timedelta(seconds=10)).isoformat(),
        }
        drain_ref = self.ref("ingress-drain.json", evidence["ingress_drain"])
        if stage == "closed":
            sample = {
                "schema": process_scope.OBSERVATION_SCHEMA,
                "scope_sha256": scope_ref["sha256"],
                "frozen": True,
                "freeze_actors": [
                    {**row, "stopped": True, "thread_states": ["T", "T"]}
                    for row in scope["freeze_actors"]
                ],
                "transport_actors": [
                    {**row, "stopped": False, "thread_states": ["S"]}
                    for row in scope["transport_actors"]
                ],
                "native_control_peer": None,
                "topology_sha256": io.sha(io.canonical(scope["observation"])),
            }
            evidence["process_scope"] = scope
            evidence["closure"] = {
                "schema": "autostop.j1.crm-forward-closure-observation.v2",
                "process_scope_ref": scope_ref,
                "scope_sha256": scope_ref["sha256"],
                "process_scope_observation": sample,
                "ingress_drain_ref": drain_ref,
                **{
                    key: [
                        {"pid": row["pid"], "start_ticks": row["start_ticks"], "stopped": stopped}
                        for row in scope[key]
                    ]
                    for key, stopped in (("freeze_actors", True), ("transport_actors", False))
                },
            }
            inspection["writer_closure_confirmed"] = True
        else:
            evidence["guard_starts"] = {
                name: {
                    "invocation_id": f"{i + 1:032x}",
                    "main_pid": i + 2,
                    "active_enter_monotonic_us": 1000,
                    "guard_execution_sha256": "6" * 64,
                }
                for i, name in enumerate(sorted(worker.NATIVE_UNITS))
            }
            peer_actor = {
                **self.actor(538285),
                **{
                    key: value
                    for key, value in process_scope.native_profile(baseline["native"]).items()
                    if key in process_scope.ACTOR_FIELDS
                },
                "pid": evidence["guard_starts"]["autostop-codex-wake.service"]["main_pid"],
                "cgroup": "0::/system.slice/autostop-codex-wake.service",
            }
            activation = {
                **copy.deepcopy(staged),
                "schema": "autostop.j1.crm-forward-process-scope-activation.v1",
                "native_control_peer": {
                    "actor": peer_actor,
                    "profile": process_scope.native_profile(baseline["native"]),
                },
            }
            evidence["activation_cohort"] = {
                "schema": "autostop.j1.crm-forward-activation-observation.v2",
                "process_scope_ref": scope_ref,
                "scope_sha256": scope_ref["sha256"],
                "process_scope_observation": activation,
                "ingress_drain_ref": drain_ref,
                **{
                    key: [
                        {
                            "pid": row["pid"],
                            "start_ticks": row["start_ticks"],
                            "stopped": row["stopped"],
                        }
                        for row in activation[key]
                    ]
                    for key in ("freeze_actors", "transport_actors")
                },
            }
            evidence["container"] = {
                "image": plan["candidate"]["image"],
                "revision": plan["candidate"]["revision"],
                "running": True,
                "health": "healthy",
            }
            evidence["runtime"] = {
                name: {"ok": True}
                for name in ("runtime", "gateway", "oauth", "performance", "feed")
            }
            evidence["runtime"]["actual_crm_revision"] = plan["candidate"]["revision"]
            inspection.update(original_guards_executed=True, runtime_checks_passed=True)
        for name, document in evidence.items():
            filename = {
                "process_scope": "process-scope.json",
                "ingress_drain": "ingress-drain.json",
                "ingress_idle": idle_filename,
            }.get(name, "inspected-" + name + ".json")
            inspection[name + "_ref"] = self.ref(filename, document)
        result = {
            "ok": True,
            "stage": stage,
            "plan": plan,
            "plan_ref": plan_ref,
            "baseline": baseline,
            "baseline_ref": baseline_ref,
            "review_key_ref": review,
            "inspection": inspection,
            "inspection_ref": self.ref("inspection-fixture.json", inspection),
            "evidence": evidence,
            "backup_context": {
                "attempt_id": self.attempt,
                "predecessor_sha256": plan["predecessor_sha256"],
                "candidate_crm_sha": plan["candidate"]["revision"],
                "candidate_manager_sha": worker.MANAGER_REVISION,
                "scope_profile_sha256": transport.PROFILE_SHA256,
            },
            "scope_profile_sha256": transport.PROFILE_SHA256,
        }
        if stage == "closed":
            result["transport"] = {
                "path": self.prefix + "delta.tar.gz",
                "sha256": io.sha(b"delta"),
                "manifest_sha256": "a" * 64,
            }
        return result

    def actor(self, pid):
        return {
            "pid": pid,
            "start_ticks": process_scope.STARTS[pid],
            "exe": "/usr/bin/fixture",
            "exe_sha256": process_scope._program_sha(pid),
            "exe_identity": [1, pid],
            "argv_sha256": process_scope.ARGV[process_scope._kind(pid)],
            "cgroup": process_scope.CGROUPS[pid],
            "uid": 996 if pid == 698406 else 0,
            "mount_namespace": "mnt:[99]",
        }

    def scope(self, plan, native):
        roles = {str(pid): "helper_mcp" for pid in process_scope.FREEZE}
        roles.update(
            {
                "538285": "daemon",
                "538487": "proxy",
                "699029": "proxy",
                "699118": "proxy",
                "919446": "helper_code",
                "538314": "ssh_original",
                "698928": "ssh_proxy_parent",
                "699028": "ssh_proxy_parent",
                "698344": "ssh_tunnel_monitor",
                "698406": "ssh_tunnel",
            }
        )
        return {
            "schema": process_scope.SCHEMA,
            "profile_sha256": process_scope.PROFILE_SHA256,
            "attempt_id": self.attempt,
            "attempt_dir": self.prefix.rstrip("/"),
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "base_sha256": plan["base_sha256"],
            "native_sha256": io.sha(io.canonical(native)),
            "boot_id": "c9a73e36-4570-44c3-ab34-a07d68ff386f",
            "original_actors": [self.actor(pid) for pid in sorted(process_scope.ORIGINAL)],
            "freeze_actors": [self.actor(pid) for pid in process_scope.FREEZE],
            "transport_actors": [self.actor(pid) for pid in process_scope.TRANSPORTS],
            "observation": {"roles": roles},
            "native_control_peer": {"profile": process_scope.native_profile(native)},
        }

    def rebind(self, response):
        response["inspection_ref"] = self.ref("inspection-fixture.json", response["inspection"])

    def validate(self, response):
        return worker.validate_response(response, attempt=self.attempt, stage=response["stage"])

    def rebind_document(self, response, name):
        reference = response["inspection"][name + "_ref"]
        response["inspection"][name + "_ref"] = {
            "path": reference["path"],
            "sha256": io.sha(io.canonical(response["evidence"][name])),
        }
        self.rebind(response)

    def test_closed_requires_all_thirteen_writers_stopped_and_transports_live(self):
        for key, index, mutation in (
            ("freeze_actors", 0, "live"),
            ("freeze_actors", -1, "thread_live"),
            ("transport_actors", 0, "stopped"),
            ("freeze_actors", 1, "missing"),
            ("freeze_actors", 0, "birth_changed"),
        ):
            response = self.response()
            observed = response["evidence"]["closure"]["process_scope_observation"][key]
            row = observed[index]
            if mutation == "live":
                row.update(stopped=False, thread_states=["S"])
            elif mutation == "thread_live":
                row["thread_states"].append("R")
            elif mutation == "stopped":
                row.update(stopped=True, thread_states=["T"])
            elif mutation == "missing":
                observed.pop(index)
            else:
                row["start_ticks"] += 1
            self.rebind_document(response, "closure")
            with self.subTest(mutation=mutation), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_activation_rejects_early_auxiliary_release_or_frozen_daemon(self):
        for index in (0, 1, -1):
            response = self.response("activated")
            rows = response["evidence"]["activation_cohort"]["process_scope_observation"][
                "freeze_actors"
            ]
            rows[index]["stopped"] = not rows[index]["stopped"]
            rows[index]["thread_states"] = ["T" if rows[index]["stopped"] else "S"]
            self.rebind_document(response, "activation_cohort")
            with self.subTest(index=index), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_activation_native_peer_must_match_fresh_guarded_wake_process(self):
        for field, value in (
            ("pid", 762195),
            ("cgroup", "0::/foreign.service"),
            ("uid", 1000),
            ("argv_sha256", "0" * 64),
        ):
            response = self.response("activated")
            peer = response["evidence"]["activation_cohort"]["process_scope_observation"][
                "native_control_peer"
            ]
            peer["actor"][field] = value
            self.rebind_document(response, "activation_cohort")
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(io.Refusal, "worker_native_peer_guard_binding"),
            ):
                self.validate(response)

    def test_hash_valid_drain_cannot_hide_absent_or_non_idle_metadata(self):
        for stage in ("closed", "activated"):
            for field, value in (
                ("active_turns", 1),
                ("metadata_only", False),
                ("rpc_sha256", "0" * 64),
                ("observed_at", "2020-01-01T00:00:00Z"),
            ):
                response = self.response(stage)
                response["evidence"]["ingress_idle"][field] = value
                self.rebind_document(response, "ingress_idle")
                response["evidence"]["ingress_drain"]["idle_observation_ref"] = response[
                    "inspection"
                ]["ingress_idle_ref"]
                self.rebind_document(response, "ingress_drain")
                name = "closure" if stage == "closed" else "activation_cohort"
                response["evidence"][name]["ingress_drain_ref"] = response["inspection"][
                    "ingress_drain_ref"
                ]
                self.rebind_document(response, name)
                with (
                    self.subTest(stage=stage, field=field),
                    self.assertRaisesRegex(io.Refusal, "worker_ingress_idle_not_proven"),
                ):
                    self.validate(response)

    def test_staging_receipt_cannot_substitute_emergency_daemon_first_freeze(self):
        response = self.response()
        staged = response["evidence"]["ingress_drain"]["staging_observation"]
        staged["freeze_actors"][0].update(stopped=True, thread_states=["T"])
        self.rebind_document(response, "ingress_drain")
        response["evidence"]["closure"]["ingress_drain_ref"] = response["inspection"][
            "ingress_drain_ref"
        ]
        self.rebind_document(response, "closure")
        with self.assertRaisesRegex(io.Refusal, "worker_ingress_actor_state"):
            self.validate(response)

    def test_scope_identity_partition_must_match_original_authority(self):
        response = self.response()
        response["evidence"]["process_scope"]["original_actors"][0]["exe_identity"][1] += 1
        self.rebind_document(response, "process_scope")
        with self.assertRaises(io.Refusal):
            self.validate(response)

    def test_release_contract_closed_and_activated_fixtures_validate(self):
        for stage in ("closed", "activated"):
            response = self.response(stage)
            self.assertIs(self.validate(response), response)

    def test_plan_inspection_baseline_and_evidence_are_independently_hash_bound(self):
        for field in ("plan", "baseline", "inspection"):
            response = self.response()
            response[field]["injected"] = True
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(io.Refusal, "worker_document_hash_mismatch"),
            ):
                self.validate(response)
        response = self.response()
        response["evidence"]["native"]["ok"] = False
        with self.assertRaisesRegex(io.Refusal, "worker_document_hash_mismatch"):
            self.validate(response)

    def test_legacy_contract_mismatches_and_unknown_fields_are_rejected(self):
        response = self.response()
        response["baseline"]["review_key"] = response["baseline"].pop("review_key_ref")
        response["baseline_ref"] = self.ref("baseline.json", response["baseline"])
        with self.assertRaises(io.Refusal):
            self.validate(response)
        response = self.response("activated")
        response["evidence"].pop("guard_starts")
        with self.assertRaisesRegex(io.Refusal, "worker_evidence_fields"):
            self.validate(response)
        response = self.response()
        response["accepted"] = True
        with self.assertRaisesRegex(io.Refusal, "worker_inspect_response_invalid"):
            self.validate(response)

    def test_ci_must_bind_exact_revision_successful_job_set_and_official_run(self):
        for key, value in (
            ("run_id", True),
            ("revision", "f" * 40),
            ("conclusion", "failure"),
            ("jobs", ["unit"]),
            ("run_url", "https://example.invalid/run/1234"),
        ):
            response = self.response()
            response["baseline"]["ci"][key] = value
            response["baseline_ref"] = self.ref("baseline.json", response["baseline"])
            response["plan"]["baseline_ref"] = response["baseline_ref"]
            response["plan_ref"] = self.ref("plan.json", response["plan"])
            with self.subTest(key=key), self.assertRaisesRegex(io.Refusal, "worker_ci_invalid"):
                self.validate(response)

    def test_failed_native_guard_runtime_and_container_checks_cannot_be_attested(self):
        cases = [
            ("native", "ok", False),
            ("runtime", "actual_crm_revision", "f" * 40),
            ("container", "health", "unhealthy"),
        ]
        for name, key, value in cases:
            response = self.response("activated")
            response["evidence"][name][key] = value
            response["inspection"][name + "_ref"] = self.ref(
                "inspected-" + name + ".json", response["evidence"][name]
            )
            self.rebind(response)
            with self.assertRaises(io.Refusal):
                self.validate(response)
        response = self.response("activated")
        del response["evidence"]["guard_starts"][sorted(worker.NATIVE_UNITS)[0]]
        response["inspection"]["guard_starts_ref"] = self.ref(
            "guards.json", response["evidence"]["guard_starts"]
        )
        self.rebind(response)
        with self.assertRaisesRegex(io.Refusal, "worker_native_guard_set"):
            self.validate(response)

    def test_reference_aliases_and_different_attempt_paths_are_rejected(self):
        for path in ("/tmp/plan.json", self.prefix + "../plan.json", self.prefix + "//plan.json"):
            response = self.response()
            response["plan_ref"]["path"] = path
            with self.assertRaises(io.Refusal):
                self.validate(response)

    def test_source_bundle_changes_and_wrong_backup_context_are_rejected(self):
        response = self.response()
        with patch.object(worker.Path, "read_bytes", return_value=b"changed helper"):
            with self.assertRaisesRegex(io.Refusal, "worker_consumer_hash_mismatch"):
                worker._source_consumers(response["baseline"], response["plan"])
        response["backup_context"]["candidate_manager_sha"] = "f" * 40
        with self.assertRaisesRegex(io.Refusal, "worker_backup_context_invalid"):
            self.validate(response)

    def test_acl_custody_rejects_other_readers_owners_and_writers(self):
        current = "S-1-5-21-123"
        value = {
            "current": current,
            "owner": current,
            "rules": [{"sid": current, "allow": True, "rights": 2032127, "inherit_only": False}],
        }
        worker._check_acl(value, private=True)
        owner_rights = copy.deepcopy(value)
        owner_rights["rules"].append(
            {"sid": "S-1-3-4", "allow": True, "rights": 2032127, "inherit_only": False}
        )
        worker._check_acl(owner_rights, private=True)
        foreign_owner = {**value, "owner": "S-1-1-0"}
        with self.assertRaisesRegex(io.Refusal, "worker_acl_owner_invalid"):
            worker._check_acl(foreign_owner, private=True)
        for sid in worker.SYSTEM_SIDS:
            allowed = copy.deepcopy(value)
            allowed["rules"].append(
                {"sid": sid, "allow": True, "rights": 2032127, "inherit_only": False}
            )
            worker._check_acl(allowed, private=True)
        value["rules"].append(
            {"sid": "S-1-1-0", "allow": True, "rights": 131209, "inherit_only": False}
        )
        with self.assertRaisesRegex(io.Refusal, "worker_acl_other_principal"):
            worker._check_acl(value, private=True)
        worker._check_acl(value, private=False)
        value["rules"][-1]["rights"] |= 2
        with self.assertRaisesRegex(io.Refusal, "worker_acl_other_principal"):
            worker._check_acl(value, private=False)

    def test_remote_operations_have_fixed_host_command_and_no_response_paths(self):
        command = self.instance._ssh("inspect", self.attempt, stage="closed")
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertEqual(
            command[command.index(worker.HOST) + 1 :],
            [
                "/usr/bin/python3",
                "-B",
                worker.REMOTE,
                "inspect",
                "--attempt",
                self.attempt,
                "--stage",
                "closed",
            ],
        )
        for operation, attempt in (("arbitrary", self.attempt), ("inspect", self.attempt + ";id")):
            with self.assertRaises(io.Refusal):
                self.instance._ssh(operation, attempt, stage="closed")

    def test_download_bounded_stream_and_input_use_real_local_subprocesses(self):
        destination = self.root / "stream.bin"
        result = self.instance._download(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            destination,
            limit=4096,
            timeout=5,
            input_bytes=b"test-data",
        )
        self.assertEqual(result["sha256"], io.sha(b"test-data"))
        self.assertEqual(destination.read_bytes(), b"test-data")
        with self.assertRaisesRegex(io.Refusal, "worker_remote_failed"):
            self.instance._download(
                [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*8192)"],
                self.root / "too-large",
                limit=4096,
                timeout=5,
            )

    def test_stalled_subprocess_and_stalled_stdin_are_bounded(self):
        with self.assertRaisesRegex(io.Refusal, "worker_remote_failed"):
            self.instance._download(
                [sys.executable, "-c", "import time;time.sleep(5)"],
                self.root / "stalled",
                limit=4096,
                timeout=0.1,
                input_bytes=b"x" * (1024**2),
            )

    def fake_execution(self, response, *, restore_fails=False):
        calls, delivered = [], {}

        def download(argv, destination, **kwargs):
            if "inspect" in argv:
                calls.append("inspect")
                raw = io.canonical(response)
            elif "export-delta" in argv:
                calls.append("download")
                raw = b"delta"
            else:
                calls.append("deliver")
                signed = io.document(kwargs["input_bytes"])
                delivered[signed["kind"]] = signed
                raw = io.canonical(
                    {
                        "ok": True,
                        "proof": {
                            "path": self.prefix + signed["kind"] + ".json",
                            "sha256": io.sha(kwargs["input_bytes"]),
                        },
                    }
                )
            return worker._new_file(destination, raw)

        def crypto(args, **_kwargs):
            if args[0] == "pkey":
                worker._new_file(Path(args[-1]), self.public)
            elif "-sign" in args:
                calls.append("sign")
                worker._new_file(Path(args[-1]), b"x" * 64)

        def assemble(archive, archive_sha, manifest_sha, cache, output, **_kwargs):
            calls.append("assemble")
            self.assertEqual(archive.read_bytes(), b"delta")
            self.assertEqual(archive_sha, io.sha(b"delta"))
            self.assertEqual(cache, self.cache)
            output.mkdir()

        def restore(assembled, manifest_sha, output, context, **_kwargs):
            calls.append("restore")
            if restore_fails:
                raise io.Refusal("synthetic_restore_failed")
            self.assertEqual(context, response["backup_context"])
            output.mkdir()
            receipt = {
                "provided_artifacts_restored_exactly": True,
                "source_evidence_unchanged": True,
            }
            reference = worker._new_file(output / "verification.json", io.canonical(receipt))
            return {**receipt, "receipt": reference}

        backup = SimpleNamespace(
            sqlite3=SimpleNamespace(sqlite_version_info=worker.MIN_SQLITE),
            profile_sha256=lambda: transport.PROFILE_SHA256,
            verify_and_restore=restore,
        )
        patches = [
            patch.object(self.instance, "_download", side_effect=download),
            patch.object(self.instance, "_crypto", side_effect=crypto),
            patch.object(proofs, "_ed25519_public_key"),
            patch.object(transport, "core", return_value=backup),
            patch.object(transport, "assemble_delta", side_effect=assemble),
            patch.object(worker.sqlite3, "sqlite_version_info", worker.MIN_SQLITE),
        ]
        for replacement in patches:
            replacement.start()
            self.addCleanup(replacement.stop)
        return calls, delivered

    def test_closed_signs_only_after_actual_restore_call_and_delivers_bound_receipt(self):
        response = self.response()
        calls, delivered = self.fake_execution(response)
        result = self.instance.run(self.attempt, "closed")
        self.assertEqual(calls, ["inspect", "download", "assemble", "restore", "sign", "deliver"])
        payload = delivered["offhost_restore"]["payload"]
        self.assertTrue(payload["actual_restore"])
        self.assertEqual(payload["closed_inspection_sha256"], response["inspection_ref"]["sha256"])
        self.assertEqual(set(result["proof_sha256"]), {"offhost_restore"})

    def test_restore_failure_never_signs_or_delivers(self):
        calls, delivered = self.fake_execution(self.response(), restore_fails=True)
        with self.assertRaisesRegex(io.Refusal, "synthetic_restore_failed"):
            self.instance.run(self.attempt, "closed")
        self.assertNotIn("sign", calls)
        self.assertEqual(delivered, {})

    def test_activated_signs_two_freshly_inspected_acceptances_without_restore_shortcut(self):
        response = self.response("activated")
        calls, delivered = self.fake_execution(response)
        self.instance.run(self.attempt, "activated")
        self.assertEqual(calls, ["inspect", "sign", "deliver", "sign", "deliver"])
        self.assertEqual(set(delivered), {"native_acceptance", "runtime_acceptance"})
        self.assertEqual(
            delivered["runtime_acceptance"]["payload"]["candidate_image"],
            response["plan"]["candidate"]["image"],
        )

    def test_wrong_signer_key_never_signs(self):
        response = self.response()
        calls, _ = self.fake_execution(response)
        self.public += b"different key"
        with self.assertRaisesRegex(io.Refusal, "worker_signer_key_mismatch"):
            self.instance.run(self.attempt, "closed")
        self.assertNotIn("sign", calls)

    def test_wrong_archive_never_assembles_restores_or_signs(self):
        response = self.response()
        response["transport"]["sha256"] = "f" * 64
        calls, _ = self.fake_execution(response)
        with self.assertRaisesRegex(io.Refusal, "worker_archive_hash_mismatch"):
            self.instance.run(self.attempt, "closed")
        self.assertEqual(calls, ["inspect", "download"])

    def test_old_sqlite_runtime_is_rejected_before_download(self):
        calls, _ = self.fake_execution(self.response())
        with patch.object(worker.sqlite3, "sqlite_version_info", (3, 50, 0)):
            with self.assertRaisesRegex(io.Refusal, "worker_sqlite_runtime_too_old"):
                self.instance.run(self.attempt, "closed")
        self.assertEqual(calls, ["inspect"])

    def test_delivery_acknowledgement_must_match_exact_proof_hash(self):
        calls, _ = self.fake_execution(self.response("activated"))
        original = self.instance._download

        def corrupt(argv, destination, **kwargs):
            reference = original(argv, destination, **kwargs)
            if "receive-proof" in argv:
                value = io.document(destination.read_bytes())
                value["proof"]["sha256"] = "f" * 64
                destination.write_bytes(io.canonical(value))
            return reference

        with patch.object(self.instance, "_download", side_effect=corrupt):
            with self.assertRaisesRegex(io.Refusal, "worker_delivery_reference_mismatch"):
                self.instance.run(self.attempt, "activated")
        self.assertEqual(calls, ["inspect", "sign", "deliver"])

    def test_actual_local_ed25519_signing_uses_constructed_payload(self):
        openssl = _openssl()
        if openssl is None:
            self.skipTest("Local OpenSSL unavailable")
        self.instance.openssl = Path(openssl)
        subprocess.run(
            [openssl, "genpkey", "-algorithm", "ED25519", "-out", str(self.instance.reviewer_key)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        directory = self.root / "actual-sign"
        directory.mkdir()
        self.instance._crypto(
            [
                "pkey",
                "-in",
                str(self.instance.reviewer_key),
                "-pubout",
                "-out",
                str(directory / "reviewer-public.pem"),
            ],
            timeout=10,
        )
        self.public = (directory / "reviewer-public.pem").read_bytes()
        response = self.response("activated")
        payload = {"phase": "activated", "inspection_sha256": response["inspection_ref"]["sha256"]}
        signed = io.document(self.instance._sign(response, "native_acceptance", payload, directory))
        self.assertEqual(signed["payload"], payload)
        self.assertEqual(len(signed["signature_base64"]), 88)


if __name__ == "__main__":
    unittest.main()
