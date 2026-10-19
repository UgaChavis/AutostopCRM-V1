from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_authority as authority
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_guard as guard
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


class ColdBootstrapPins(unittest.TestCase):
    def test_all_fixed_bootstrap_sources_are_exact(self):
        self.assertGreaterEqual(len(guard.BOOTSTRAP_HASHES), 10)
        for name, expected in guard.BOOTSTRAP_HASHES.items():
            self.assertEqual(
                hashlib.sha256(
                    Path(guard.__file__).with_name(name + ".py").read_bytes()
                ).hexdigest(),
                expected,
                name,
            )


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class ColdNativeLifecycleTests(unittest.TestCase):
    def test_actual_cli_six_one_shot_entries_keep_original_eight_and_ingress_closed(self):
        # This isolates native entry/dispatch, with independent restore-signature
        # acceptance replaced at its explicit boundary. The joined rehearsal
        # exercises actual backup, custodian crypto and semantic graph restore.
        with rehearsal.TraceFixture(candidates=True) as fixture:
            package_ref = fixture.produce()
            provider = fixture.provider
            with fixture.custody:
                epoch_ref = lifecycle.provision_epoch(
                    package_ref, fixture.runtime_bindings, provider=provider
                )
                backend = lifecycle.FixtureBackend(fixture)
                controller = lifecycle.Controller(
                    package_ref, epoch_ref, provider=provider, backend=backend
                )
                controller.stage()
                self.assertEqual(len(controller.epoch["runtime_bindings"]), 7)
                directory = fixture.control / "attempts" / fixture.binding["attempt_id"]
                spec = fixture._write(directory / "crm-spec.json", {"scope": "native_fixture"})
                parent_request = fixture._write(
                    directory / "parent-request.json", {"scope": "native_fixture"}
                )
                baseline = fixture._write(
                    directory / "baseline.json",
                    {
                        "review_key_ref": spec,
                        "backup_profile_sha256": "3" * 64,
                        "controller_request_ref": parent_request,
                    },
                )
                plan = fixture._plan(fixture.binding["attempt_id"])
                plan.update(
                    schema=authority.PLAN_SCHEMA,
                    scope=authority.PLAN_SCOPE,
                    baseline_ref=baseline,
                    candidate_manager_sha=fixture.binding["candidate_manager_sha"],
                    cold_authority_package_ref=package_ref,
                    dispatch_epoch_ref=epoch_ref,
                    cold_crm_spec_ref=spec,
                )
                plan["candidate"]["revision"] = fixture.binding["candidate_crm_sha"]
                plan_ref = fixture._write(directory / "plan.json", plan)
                backup = fixture._write(
                    directory / "backup-evidence.json", {"manifest_sha256": "4" * 64}
                )
                fixture._write(directory / "control-closed.json", {"manifest_sha256": "5" * 64})
                payload = {
                    "actual_restore": True,
                    "control_graph_restored": True,
                    "phase": "closed",
                    "manifest_sha256": "4" * 64,
                    "plan_sha256": plan_ref["sha256"],
                    "baseline_sha256": baseline["sha256"],
                    "profile_sha256": "3" * 64,
                    "control_archive_manifest_sha256": "5" * 64,
                    "control_restore_receipt_sha256": "6" * 64,
                }
                with patch("scripts.j1_forward_proofs.verify_proof", return_value=payload):
                    controller.issue(plan_ref, backup, spec)
                closed = Path(provider.root / "execution/dispatch/state.json").read_bytes()
                for unit in authority.ALLOWED:
                    controller.submit(unit)
                    try:
                        reference = controller.accept(unit)
                    except Exception:
                        details = {
                            name: child.stderr.read(4096).decode(errors="replace")
                            for name, (child, _invocation) in backend.started.items()
                            if child.poll() is not None
                        }
                        self.fail(unit + ": " + repr(details))
                    observed = io.document(provider.read_reference(reference))
                    self.assertEqual(observed["logical_unit"], unit)
                    self.assertFalse(observed["business_acceptance"])
                    slot, snapshot, context = controller.context(unit)
                    self.assertTrue(snapshot["state"]["execution_possible"])
                    before = slot.read()
                    self.assertTrue(controller.submit(unit)["historical"])
                    self.assertEqual(slot.read(), before)
                    role = epochs.ROLES[unit]
                    endpoint = Path(provider.root / "execution/roles" / role / "run/control.sock")
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(3)
                        client.connect(str(endpoint))
                        client.sendall(
                            io.canonical({"op_id": str(uuid.uuid4()), "payload_sha256": "f" * 64})
                        )
                        self.assertEqual(client.recv(128), b"HELD\n")
                    duplicate = subprocess.run(
                        context["worker_argv"],
                        env=context["bootstrap_environment"],
                        cwd=context["worker_cwd"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertNotEqual(duplicate.returncode, 0)
                    self.assertEqual(slot.read(), before)
                # The seventh entry consumes its own final-plan permission.
                # This external native-boundary fixture supplies parent A;
                # neither Controller nor the request ticket invents it.
                parent_actor = {
                    "pid": os.getpid(),
                    "start_ticks": native.birth(os.getpid()),
                    "invocation_id": uuid.uuid4().hex,
                }
                parent_a = fixture._write(directory / "parent-acceptance.json", parent_actor)
                with patch.object(
                    backend, "parent_executor", create=True, return_value=(parent_actor, parent_a)
                ):
                    entered = controller.consume_crm_start(parent_request)
                    self.assertTrue(entered["entry_consumed"])
                    crm_store = controller.kernel.LaunchStore(
                        controller.root / "crm-start" / fixture.binding["attempt_id"],
                        owner=os.geteuid(),
                        clock=lifecycle.time.monotonic,
                    )
                    before = crm_store.read()
                    with self.assertRaisesRegex(io.Refusal, "already_consumed_no_retry"):
                        controller.consume_crm_start(parent_request)
                    self.assertEqual(crm_store.read(), before)
                self.assertEqual(
                    Path(provider.root / "execution/dispatch/state.json").read_bytes(), closed
                )
                for unit in authority.UNITS:
                    self.assertEqual(
                        (fixture.root / "barriers" / unit).read_bytes(), authority.DENY_BYTES
                    )
                self.assertFalse(
                    list((fixture.root / "execution/roles").glob("*/state/requests/*/effect.json"))
                )


if __name__ == "__main__":
    unittest.main()
