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

from scripts import j1_forward_io as io
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
            "native": {"base_sha256": "1" * 64, "sha256": "2" * 64},
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
        if stage == "closed":
            evidence["closure"] = {"schema": "synthetic.closure"}
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
            inspection[name + "_ref"] = self.ref("inspected-" + name + ".json", document)
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

    def rebind(self, response):
        response["inspection_ref"] = self.ref("inspection-fixture.json", response["inspection"])

    def validate(self, response):
        return worker.validate_response(response, attempt=self.attempt, stage=response["stage"])

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
