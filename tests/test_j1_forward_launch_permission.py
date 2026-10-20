"""Real private-file transactions; no host services, sockets or application IO."""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_launch_permission as launch


class LogicalTicketRestoreTests(unittest.TestCase):
    def ticket(self, path):
        attempt = str(uuid.uuid4())
        return {
            "schema": launch.SCHEMA,
            "attempt_id": attempt,
            "nonce": str(uuid.uuid4()),
            "plan_ref": {"path": path, "sha256": "1" * 64},
            "source_sha256": "2" * 64,
            "context_sha256": "3" * 64,
            "host": "isolated.invalid",
            "boot_id": str(uuid.uuid4()),
            "owner_generation": str(uuid.uuid4()),
            "unit": f"autostop-crm-forward-{attempt}.service",
            "not_before": 1,
            "expires_at": 2,
        }

    def test_captured_linux_and_drive_qualified_fixture_paths_remain_logical(self):
        for path in ("/etc/autostop-crm-forward/plan.json", r"C:\private\plan.json"):
            with self.subTest(path=path):
                self.assertEqual(
                    launch.validate_ticket(self.ticket(path))["plan_ref"]["path"], path
                )

    def test_relative_traversing_and_unc_paths_never_become_absolute_authority(self):
        for path in (
            "plan.json",
            "/private/../plan.json",
            r"C:\private\..\plan.json",
            r"\\host\share\plan.json",
        ):
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(launch.Refusal, "launch_plan_path"),
            ):
                launch.validate_ticket(self.ticket(path))


@unittest.skipUnless(os.name == "posix", "Linux root-owned launch journal and systemd admission")
class LaunchPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="launch-permission-", dir=Path.home())
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.base.chmod(0o700)
        self.now = 1000.0
        self.owner = os.getuid() if hasattr(os, "getuid") else 0
        self.attempt, self.boot, self.generation = (str(uuid.uuid4()) for _ in range(3))
        self.ticket = {
            "schema": launch.SCHEMA,
            "attempt_id": self.attempt,
            "nonce": str(uuid.uuid4()),
            "plan_ref": {"path": str(self.base / "plan.json"), "sha256": "1" * 64},
            "source_sha256": "2" * 64,
            "context_sha256": "3" * 64,
            "host": "isolated.invalid",
            "boot_id": self.boot,
            "owner_generation": self.generation,
            "unit": f"autostop-crm-forward-{self.attempt}.service",
            "not_before": 999.0,
            "expires_at": 1100.0,
        }
        self.store = launch.LaunchStore(
            self.base / self.attempt, owner=self.owner, clock=lambda: self.now
        )
        self.store.create(self.ticket)
        self.worker = None

    def event(self, name, payload=None, request_id=None):
        return self.store.transition(
            name,
            request_id=request_id or str(uuid.uuid4()),
            payload=payload or {},
            boot_id=self.boot,
            generation=self.generation,
        )

    def submit(self, request_id=None):
        return self.event("submit", {"owner": {"pid": 101, "start_ticks": 20}}, request_id)

    def accepted(self):
        self.submit()
        evidence = {
            "schema": "autostop.j1.forward-launch-native-acceptance.v1",
            "ticket_sha256": launch.sha(launch.canonical(self.ticket)),
            **{
                key: self.ticket[key]
                for key in (
                    "attempt_id",
                    "nonce",
                    "boot_id",
                    "unit",
                    "context_sha256",
                    "source_sha256",
                )
            },
            "invocation_id": "a" * 32,
            "pid": 102,
            "start_ticks": 21,
        }
        path = self.base / "acceptance.json"
        launch._new(path, launch.canonical(evidence))
        self.worker = {key: evidence[key] for key in ("invocation_id", "pid", "start_ticks")}
        self.worker["evidence_ref"] = {"path": str(path), "sha256": launch.sha(path.read_bytes())}
        return self.event("accept", {"receipt": self.worker})

    def claimed(self):
        self.accepted()
        return self.event("claim", {"worker": self.worker})

    def test_full_sequence_uses_real_files_and_sticky_dispatch_and_entry(self):
        self.claimed()
        entered = self.event("enter", {"worker": self.worker})
        self.assertTrue(entered["action_authorized"])
        state = self.store.read()["state"]
        self.assertTrue(state["dispatch_possible"])
        self.assertTrue(state["execution_possible"])
        self.assertEqual(state["sequence"], 4)

    def active_parent(self, check=None, **overrides):
        arguments = {
            "ticket": self.ticket,
            "acceptance": self.worker,
            "check_current": (lambda _snapshot: None) if check is None else check,
        }
        return self.store.active_parent(**{**arguments, **overrides})

    def entered_parent(self):
        self.claimed()
        self.event("enter", {"worker": self.worker})

    def test_child_admission_serializes_with_revoke_without_erasing_parent_e(self):
        self.entered_parent()
        checks = []
        with self.active_parent(lambda snapshot: checks.append(snapshot["state"]["sequence"])):
            with self.assertRaises(BlockingIOError):
                self.event("revoke")
            self.assertFalse(self.store.read()["state"]["revoked"])
        self.assertEqual(checks, [4, 4])
        self.event("revoke")
        self.assertTrue(self.store.read()["state"]["execution_possible"])
        with self.assertRaisesRegex(launch.Refusal, "parent_not_active"):
            with self.active_parent():
                self.fail("A later separate child cannot inherit the historical E")

    def test_real_child_c_and_e_are_inside_the_parent_revoke_cut(self):
        self.entered_parent()
        child_attempt = str(uuid.uuid4())
        child_ticket = {
            **self.ticket,
            "attempt_id": child_attempt,
            "nonce": str(uuid.uuid4()),
            "unit": "autostop-crm-forward-" + child_attempt + ".service",
        }
        child = launch.LaunchStore(
            self.base / child_attempt, owner=self.owner, clock=lambda: self.now
        )
        child.create(child_ticket)

        def child_event(event, payload):
            return child.transition(
                event,
                request_id=str(uuid.uuid4()),
                payload=payload,
                boot_id=self.boot,
                generation=self.generation,
            )

        child_event("submit", {"owner": {"pid": 201, "start_ticks": 30}})
        evidence = {
            **launch.document(Path(self.worker["evidence_ref"]["path"]).read_bytes()),
            "ticket_sha256": launch.sha(launch.canonical(child_ticket)),
            "attempt_id": child_attempt,
            "nonce": child_ticket["nonce"],
            "unit": child_ticket["unit"],
            "pid": 202,
            "start_ticks": 31,
        }
        path = self.base / "child-acceptance.json"
        launch._new(path, launch.canonical(evidence))
        worker = {
            "pid": 202,
            "start_ticks": 31,
            "invocation_id": evidence["invocation_id"],
            "evidence_ref": {"path": str(path), "sha256": launch.sha(path.read_bytes())},
        }
        child_event("accept", {"receipt": worker})
        with self.active_parent():
            child_event("claim", {"worker": worker})
            with self.assertRaises(BlockingIOError):
                self.event("revoke")
            self.assertTrue(child_event("enter", {"worker": worker})["action_authorized"])
        self.event("revoke")
        self.assertTrue(child.read()["state"]["execution_possible"])
        with self.assertRaisesRegex(launch.Refusal, "parent_not_active"):
            with self.active_parent():
                self.fail("Accepted R must precede any subsequent child's E")

    def test_busy_child_releases_parent_before_native_measurement_or_any_mutation(self):
        self.entered_parent()
        child_attempt = str(uuid.uuid4())
        child_ticket = {
            **self.ticket,
            "attempt_id": child_attempt,
            "nonce": str(uuid.uuid4()),
            "unit": "autostop-crm-forward-" + child_attempt + ".service",
        }
        child = launch.LaunchStore(
            self.base / child_attempt, owner=self.owner, clock=lambda: self.now
        )
        child.create(child_ticket)
        before, checks = child.read(), []
        with child.locked():
            with self.assertRaises(BlockingIOError):
                with self.store.active_parent(
                    ticket=self.ticket,
                    acceptance=self.worker,
                    check_current=lambda snapshot: checks.append(snapshot),
                    child=child,
                ):
                    self.fail("Busy child must not admit or wait under parent O")
            # Parent O is already released despite the child still being busy.
            self.event("revoke")
        self.assertEqual(checks, [])
        self.assertEqual(child.read(), before)

    def test_private_transition_requires_the_same_instance_and_thread_owned_o(self):
        arguments = {
            "request_id": str(uuid.uuid4()),
            "payload": {"owner": {"pid": 101, "start_ticks": 20}},
            "boot_id": self.boot,
            "generation": self.generation,
        }
        before = self.store.read()
        with self.assertRaisesRegex(launch.Refusal, "transition_lock_required"):
            self.store._transition_locked("submit", **arguments)
        self.assertEqual(self.store.read(), before)
        with self.store.locked():
            other = launch.LaunchStore(self.store.root, owner=self.owner, clock=lambda: self.now)
            with self.assertRaisesRegex(launch.Refusal, "transition_lock_required"):
                other._transition_locked("submit", **arguments)
            self.assertTrue(
                self.store._transition_locked("submit", **arguments)["action_authorized"]
            )
            self.assertFalse(
                self.store._transition_locked("submit", **arguments)["action_authorized"]
            )

    def test_parent_not_entered_ticket_or_acceptance_drift_cannot_authorize_child(self):
        self.claimed()
        with self.assertRaisesRegex(launch.Refusal, "parent_not_active"):
            with self.active_parent():
                self.fail("Claim is not entry")
        self.event("enter", {"worker": self.worker})
        with self.assertRaisesRegex(launch.Refusal, "ticket_changed"):
            with self.active_parent(ticket={**self.ticket, "nonce": str(uuid.uuid4())}):
                self.fail("Another parent ticket is not interchangeable")
        with self.assertRaisesRegex(launch.Refusal, "parent_not_active"):
            with self.active_parent(acceptance={**self.worker, "start_ticks": 999}):
                self.fail("PID reuse is not the accepted executor")

    def test_parent_expiry_or_native_observer_failure_denies_before_child(self):
        self.entered_parent()
        self.now = self.ticket["expires_at"]
        with self.assertRaisesRegex(launch.Refusal, "expired"):
            with self.active_parent():
                self.fail("Expired parent cannot start another child")
        self.now -= 1

        def deny(_snapshot):
            raise launch.Refusal("native_parent_D_lost")

        with self.assertRaisesRegex(launch.Refusal, "D_lost"):
            with self.active_parent(deny):
                self.fail("Missing held D is not authority")

    def test_parent_expiry_after_child_cut_preserves_unknown_and_sticky_e(self):
        self.entered_parent()
        with self.assertRaisesRegex(launch.Refusal, "expired"):
            with self.active_parent():
                self.now = self.ticket["expires_at"]
        self.assertTrue(self.store.read()["state"]["execution_possible"])
        self.assertFalse(self.store.read()["state"]["revoked"])

    def test_parent_pending_publication_never_recovers_or_authorizes_child(self):
        self.entered_parent()
        launch._new(self.store.root / "publication-pending.json", b"{}\n")
        with self.assertRaisesRegex(launch.Refusal, "requires_reconciliation"):
            with self.active_parent():
                self.fail("Pending publication must remain closed")
        self.assertTrue((self.store.root / "publication-pending.json").exists())

    def test_revoke_wins_before_submit(self):
        receipt = self.event("revoke")
        self.assertFalse(receipt["dispatch_possible"])
        with self.assertRaisesRegex(launch.Refusal, "revoked"):
            self.submit()

    def test_submit_then_revoke_does_not_claim_no_dispatch(self):
        self.submit()
        receipt = self.event("revoke")
        self.assertTrue(receipt["dispatch_possible"])
        self.assertFalse(receipt["execution_possible"])
        with self.assertRaisesRegex(launch.Refusal, "revoked"):
            self.event("claim", {"worker": {}})

    def test_claim_is_not_entry_and_revoke_wins_final_gate(self):
        result = self.claimed()
        self.assertFalse(result["action_authorized"])
        self.event("revoke")
        with self.assertRaisesRegex(launch.Refusal, "revoked"):
            self.event("enter", {"worker": self.worker})

    def test_revoke_after_entry_is_honestly_too_late(self):
        self.claimed()
        self.event("enter", {"worker": self.worker})
        result = self.event("revoke")
        self.assertEqual(result["current_admission"], "too_late_for_current_admission")
        self.assertTrue(result["execution_possible"])

    def test_lost_submit_ack_never_authorizes_another_wire_attempt(self):
        request = str(uuid.uuid4())
        self.assertTrue(self.submit(request)["action_authorized"])
        self.assertFalse(self.submit(request)["action_authorized"])
        self.assertEqual(self.store.read()["state"]["sequence"], 1)

    def test_historical_entry_ack_after_revoke_never_reopens(self):
        self.claimed()
        request = str(uuid.uuid4())
        self.event("enter", {"worker": self.worker}, request)
        self.event("revoke")
        self.assertFalse(self.event("enter", {"worker": self.worker}, request)["action_authorized"])
        self.assertTrue(self.store.read()["state"]["admission_closed"])

    def test_request_identity_cannot_change_payload(self):
        request = str(uuid.uuid4())
        self.submit(request)
        with self.assertRaisesRegex(launch.Refusal, "request_reused"):
            self.event("revoke", request_id=request)

    def test_duplicate_submit_and_duplicate_worker_are_rejected(self):
        self.claimed()
        with self.assertRaisesRegex(launch.Refusal, "already_reserved"):
            self.submit()
        with self.assertRaisesRegex(launch.Refusal, "not_accepted"):
            self.event("claim", {"worker": self.worker})

    def test_worker_cannot_claim_without_independent_acceptance(self):
        self.submit()
        with self.assertRaisesRegex(launch.Refusal, "not_accepted"):
            self.event("claim", {"worker": {"pid": 102}})

    def test_other_worker_and_replacement_incarnation_are_rejected(self):
        self.accepted()
        changed = copy.deepcopy(self.worker)
        changed["pid"] += 1
        with self.assertRaisesRegex(launch.Refusal, "not_accepted"):
            self.event("claim", {"worker": changed})
        with self.assertRaises(launch.Refusal):
            self.event("accept", {"receipt": changed})

    def test_expiry_between_claim_and_entry_is_exclusive(self):
        self.claimed()
        self.now = self.ticket["expires_at"]
        with self.assertRaisesRegex(launch.Refusal, "expired"):
            self.event("enter", {"worker": self.worker})
        self.assertFalse(self.store.read()["state"]["execution_possible"])

    def test_expiry_during_fsync_consumes_intent_without_returning_authority(self):
        original = self.store._publish

        def delayed(*args):
            result = original(*args)
            self.now = self.ticket["expires_at"]
            return result

        with patch.object(self.store, "_publish", side_effect=delayed):
            with self.assertRaisesRegex(launch.Refusal, "expired"):
                self.submit()
        self.assertTrue(self.store.read()["state"]["dispatch_possible"])
        with self.assertRaises(launch.Refusal):
            self.submit()

    def test_boot_and_owner_change_refuse_before_mutation(self):
        for name in ("boot_id", "generation"):
            kwargs = {"boot_id": self.boot, "generation": self.generation}
            kwargs[name] = str(uuid.uuid4())
            with self.subTest(name=name), self.assertRaisesRegex(launch.Refusal, "owner_or_boot"):
                self.store.transition("revoke", request_id=str(uuid.uuid4()), payload={}, **kwargs)
        self.assertEqual(self.store.read()["state"]["sequence"], 0)

    def test_slot_cannot_be_reissued_and_ticket_cannot_be_replaced(self):
        with self.assertRaisesRegex(launch.Refusal, "already_exists"):
            self.store.create(self.ticket)
        raw = launch.canonical({**self.ticket, "nonce": str(uuid.uuid4())})
        launch._replace(self.store.root / "ticket.json", raw)
        with self.assertRaisesRegex(launch.Refusal, "state_binding"):
            self.store.read()

    def test_terminal_closure_prevents_delayed_entry(self):
        self.claimed()
        self.event("deny")
        with self.assertRaisesRegex(launch.Refusal, "revoked"):
            self.event("enter", {"worker": self.worker})

    def test_deny_after_entry_is_not_a_negative_effect_receipt(self):
        self.claimed()
        self.event("enter", {"worker": self.worker})
        with self.assertRaisesRegex(launch.Refusal, "denial_after"):
            self.event("deny")

    def test_failure_before_state_publication_stops_and_recovers_without_authority(self):
        with patch.object(launch, "_replace", side_effect=OSError("simulated durability failure")):
            with self.assertRaises(OSError):
                self.submit()
        with self.assertRaisesRegex(launch.Refusal, "reconciliation"):
            self.store.read()
        self.assertFalse(self.store.recover_publication()["action_authorized"])
        self.assertTrue(self.store.read()["state"]["dispatch_possible"])

    def test_failure_after_state_publication_is_not_retried_as_fresh(self):
        original = launch._replace

        def fail_after(path, raw):
            original(path, raw)
            raise OSError("lost fsync acknowledgement")

        request = str(uuid.uuid4())
        with patch.object(launch, "_replace", side_effect=fail_after), self.assertRaises(OSError):
            self.submit(request)
        self.store.recover_publication()
        self.assertFalse(self.submit(request)["action_authorized"])

    def test_mutated_receipt_and_orphan_history_are_refused(self):
        self.accepted()
        path = Path(self.worker["evidence_ref"]["path"])
        original = path.read_bytes()
        path.write_bytes(b"{}")
        with self.assertRaisesRegex(launch.Refusal, "receipt_changed"):
            self.event("accept", {"receipt": self.worker})
        path.write_bytes(original)
        launch._new(self.store.root / "events" / "00000000000000000099.json", b"{}")
        with self.assertRaisesRegex(launch.Refusal, "inventory"):
            self.store.read()

    def test_symlink_and_hardlink_ticket_are_not_authority(self):
        if os.name != "posix":
            self.skipTest("native link ownership is covered on Linux")
        ticket = self.store.root / "ticket.json"
        other = self.base / "linked.json"
        os.link(ticket, other)
        with self.assertRaisesRegex(launch.Refusal, "private_file"):
            self.store.read()
        other.unlink()
        ticket.rename(other)
        ticket.symlink_to(other)
        with self.assertRaisesRegex(launch.Refusal, "private_file"):
            self.store.read()

    def test_resealed_history_cannot_skip_acceptance_claim_and_entry_events(self):
        self.submit()
        state = self.store.read()["state"]
        state["acceptance"] = {"untrusted": "fabricated"}
        state["claim"] = state["acceptance"]
        state["execution_possible"] = True
        request = next(iter(state["requests"].values()))
        request["result"]["execution_possible"] = True
        launch._replace(self.store.root / "state.json", launch.canonical(state))
        launch._replace(
            self.store.root / "events/00000000000000000001.json",
            launch.canonical({"schema": "autostop.j1.forward-launch-event.v1", "state": state}),
        )
        with self.assertRaisesRegex(launch.Refusal, "history_transition"):
            self.store.read()


if __name__ == "__main__":
    unittest.main()
