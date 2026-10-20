from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_cold_authority as authority
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_guard as guard
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


class ColdBootstrapPins(unittest.TestCase):
    def test_lock_wait_budget_starts_after_full_parent_qualification(self):
        clock, attempts = [1.0], []
        ticket = {"expires_at": 20.0}
        acceptance = {
            "pid": 1,
            "start_ticks": 2,
            "invocation_id": "synthetic",
            "evidence_ref": {"path": "/fixture/parent.json", "sha256": "1" * 64},
        }

        @contextmanager
        def paired(**_kwargs):
            attempts.append(clock[0])
            if len(attempts) == 1:
                raise BlockingIOError
            yield

        def prepare(_controller):
            clock[0] += 10  # Full source qualification precedes lock acquisition.
            return SimpleNamespace(active_parent=paired), {}, acceptance, lambda: None

        with (
            patch.object(
                lifecycle,
                "_child_material",
                return_value=SimpleNamespace(check_versions=lambda: None),
            ),
            patch.object(lifecycle, "_active_parent", side_effect=prepare),
            patch.object(lifecycle.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                lifecycle.time,
                "sleep",
                side_effect=lambda value: clock.__setitem__(0, clock[0] + value),
            ),
        ):
            with lifecycle.child_admission(
                None, SimpleNamespace(read=lambda: {"ticket": ticket}), ticket
            ) as result:
                self.assertEqual(result.parent[0]["pid"], 1)
        self.assertEqual(len(attempts), 2)

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
    @contextmanager
    def parent_controller(self):
        # Observer/serialization tests publish a fixture admission directly.
        # They make no independent-restore or production authority assertion.
        with rehearsal.TraceFixture(candidates=True) as fixture, fixture.custody:
            package_ref = fixture.produce()
            epoch_ref = lifecycle.provision_epoch(
                package_ref, fixture.runtime_bindings, provider=fixture.provider
            )
            controller = lifecycle.Controller(
                package_ref, epoch_ref, provider=fixture.provider, backend=object()
            )
            directory = fixture.control / "attempts" / fixture.binding["attempt_id"]
            baseline = fixture._write(
                directory / "parent-baseline.json",
                {
                    "controller_request_ref": fixture.parent.request_ref,
                    "controller_launch_ref": fixture.parent.manifest_ref,
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
                cold_crm_spec_ref=baseline,
            )
            plan["candidate"]["revision"] = fixture.binding["candidate_crm_sha"]
            plan_ref = fixture._write(directory / "parent-plan.json", plan)
            lifecycle._publish(
                controller.root / "release-admission.json",
                {
                    "schema": "autostop.j1.cold-slot-release-admission.v1",
                    "package_ref": package_ref,
                    "epoch_ref": epoch_ref,
                    "plan_ref": plan_ref,
                    "backup_ref": baseline,
                    "offhost_ref": baseline,
                    "candidate_may_have_written": True,
                    "old_writer_allowed": False,
                },
                controller.root,
            )
            yield fixture, controller

    def test_actual_parent_o_blocks_revoke_until_child_cut_finishes(self):
        with self.parent_controller() as (fixture, controller):
            with lifecycle.active_parent(controller) as (identity, acceptance_ref):
                self.assertEqual(identity, fixture.parent.identity)
                self.assertEqual(acceptance_ref, fixture.parent.acceptance_ref)
                with self.assertRaises(BlockingIOError):
                    lifecycle._transition_once(
                        fixture.parent.store, fixture.parent.store.read()["ticket"], "revoke", {}
                    )
            self.assertFalse(fixture.parent.store.read()["state"]["revoked"])

    def test_parent_revoke_denies_a_later_child_without_erasing_old_e(self):
        with self.parent_controller() as (fixture, controller):
            snapshot = lifecycle.coherent(fixture.parent.store)
            lifecycle._transition_once(fixture.parent.store, snapshot["ticket"], "revoke", {})
            with self.assertRaisesRegex(ValueError, "launch_parent_not_active"):
                with lifecycle.active_parent(controller):
                    self.fail("Historical parent E cannot admit another child")
            self.assertTrue(fixture.parent.store.read()["state"]["execution_possible"])
            self.assertFalse(list((controller.root / "journals").glob("*/state.json")))

    def test_current_parent_incarnation_is_remeasured_under_o(self):
        with self.parent_controller() as (fixture, controller):
            original = lifecycle.native.actor

            def replaced(pid):
                value = original(pid)
                if pid == fixture.parent.identity["pid"]:
                    return {**value, "start_ticks": value["start_ticks"] + 1}
                return value

            with patch.object(lifecycle.native, "actor", side_effect=replaced):
                with self.assertRaisesRegex(io.Refusal, "fixture_parent_incarnation_changed"):
                    with lifecycle.active_parent(controller):
                        self.fail("A stale accepted PID cannot admit a child")

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
                parent_request = fixture.parent.request_ref
                baseline = fixture._write(
                    directory / "baseline.json",
                    {
                        "review_key_ref": spec,
                        "backup_profile_sha256": "3" * 64,
                        "controller_request_ref": parent_request,
                        "controller_launch_ref": fixture.parent.manifest_ref,
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
                # This private observer repeats a real parent's incarnation,
                # held D and real S/A/C/E; it claims no systemd admission.
                with patch.object(
                    backend, "parent_executor", create=True, side_effect=fixture.parent.observe
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
