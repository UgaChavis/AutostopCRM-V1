"""Real private parent/child journals; manual child A isolates the admission cut.

The existing six actual CLI entry tests supply their independently observed A.
These fault cases declare that OS observation at its explicit test seam; they
do not claim a production unit or a real application accepted this test process.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_guard as guard
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
from tests import test_j1_forward_cold_lifecycle as parents


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class ColdConsumeCutTests(unittest.TestCase):
    def accepted_child(self):
        fixture, controller = self.enterContext(
            parents.ColdNativeLifecycleTests().parent_controller()
        )
        controller.backend = lifecycle.FixtureBackend(fixture)
        controller.stage()
        unit = cold.ALLOWED[0]
        store, snapshot, context = controller.context(unit)
        ticket = snapshot["ticket"]
        lifecycle._transition(store, ticket, "submit", {"owner": native.controller(os.getpid())})
        identity = fixture.parent.identity
        evidence = {
            "schema": "autostop.j1.forward-launch-native-acceptance.v1",
            "ticket_sha256": snapshot["state"]["ticket_sha256"],
            **{
                key: ticket[key]
                for key in (
                    "attempt_id",
                    "nonce",
                    "boot_id",
                    "unit",
                    "context_sha256",
                    "source_sha256",
                )
            },
            **identity,
        }
        reference = fixture._write(store.root / "native-acceptance.json", evidence)
        worker = {**identity, "evidence_ref": reference}
        lifecycle._transition(store, ticket, "accept", {"receipt": worker})
        return fixture, controller, unit, store, ticket, worker, context

    def test_one_full_qualification_precedes_paired_c_e_without_a_second_decode(self):
        fixture, controller, unit, store, _ticket, _worker, expected = self.accepted_child()
        with patch.object(native, "_observe_package", wraps=native._observe_package) as observe:
            context = lifecycle.consume(controller, unit)
        self.assertEqual(context, expected)
        self.assertEqual(observe.call_count, 1)
        self.assertTrue(lifecycle.coherent(store)["state"]["execution_possible"])
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])

    def test_source_mutation_reverted_after_durable_c_refuses_e(self):
        fixture, controller, _unit, store, ticket, worker, _context = self.accepted_child()
        source = fixture.root / "source/worker.py"
        with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
            with lifecycle.child_admission(controller, store, ticket) as cut:
                claim = lifecycle._transition_child(store, ticket, "claim", {"worker": worker})
                self.assertFalse(claim["action_authorized"])
                self.assertFalse(claim["historical"])
                raw = source.read_bytes()
                source.write_bytes(raw + b"\n")
                source.write_bytes(raw)
                cut.checkpoint()
                self.fail("A changed source cannot reach E")
        snapshot = lifecycle.coherent(store)
        self.assertIsNotNone(snapshot["state"]["claim"])
        self.assertFalse(snapshot["state"]["execution_possible"])
        with fixture.parent.store.locked(), store.locked():
            self.assertFalse(store.read()["state"]["execution_possible"])
        with self.assertRaisesRegex(io.Refusal, "checkpoint_closed"):
            cut.checkpoint()

    def test_replaced_child_context_or_ticket_after_c_refuses_e(self):
        for name in ("context.json", "ticket.json"):
            with self.subTest(name=name):
                _fixture, controller, _unit, store, ticket, worker, _context = self.accepted_child()
                with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
                    with lifecycle.child_admission(controller, store, ticket) as cut:
                        lifecycle._transition_child(store, ticket, "claim", {"worker": worker})
                        path = store.root / name
                        replacement = path.with_name("identical-replacement.json")
                        replacement.write_bytes(path.read_bytes())
                        replacement.chmod(0o600)
                        replacement.replace(path)
                        cut.checkpoint()
                        self.fail("Identical bytes cannot replace the pinned child input")
                snapshot = lifecycle.coherent(store)
                self.assertIsNotNone(snapshot["state"]["claim"])
                self.assertFalse(snapshot["state"]["execution_possible"])

    def test_boot_or_original_dispatch_owner_loss_after_c_refuses_e(self):
        import fcntl

        for drift in ("boot", "dispatch"):
            with self.subTest(drift=drift):
                fixture, controller, _unit, store, ticket, worker, _context = self.accepted_child()
                with self.assertRaises(io.Refusal):
                    with lifecycle.child_admission(controller, store, ticket) as cut:
                        lifecycle._transition_child(store, ticket, "claim", {"worker": worker})
                        if drift == "dispatch":
                            fcntl.flock(fixture.dispatch_fd, fcntl.LOCK_UN)
                            cut.checkpoint()
                        else:
                            with patch.object(fixture.provider, "boot", return_value="drifted"):
                                cut.checkpoint()
                        self.fail("Fresh ownership is mandatory before E")
                self.assertIsNotNone(lifecycle.coherent(store)["state"]["claim"])
                self.assertFalse(lifecycle.coherent(store)["state"]["execution_possible"])

    def test_new_sqlite_sidecar_after_c_is_never_an_unchanged_hold_snapshot(self):
        fixture, controller, _unit, store, ticket, worker, _context = self.accepted_child()
        with self.assertRaisesRegex(io.Refusal, "hold_database_not_quiet_checkpoint"):
            with lifecycle.child_admission(controller, store, ticket) as cut:
                lifecycle._transition_child(store, ticket, "claim", {"worker": worker})
                # No database write is needed to violate the no-sidecar cut.
                (fixture.root / "registry.sqlite3-wal").write_bytes(b"uncertain private WAL")
                cut.checkpoint()
                self.fail("The old immutable database cannot acquire a new WAL")
        self.assertIsNotNone(lifecycle.coherent(store)["state"]["claim"])
        self.assertFalse(lifecycle.coherent(store)["state"]["execution_possible"])

    def test_guard_kernel_refusal_never_consumes_c_e_or_enters_application(self):
        fixture, controller, unit, store, _ticket, _worker, _context = self.accepted_child()
        value = native.verify_package(
            fixture.package_ref, provider=fixture.provider, required_scope="native_fixture"
        )
        actor = value["kernel"]["actor"]
        child = next(row for row in fixture.children if row.pid == actor["pid"])
        fixture._retire(child, actor)
        with (
            patch.object(guard, "_PRIVATE_ROOT", fixture.root),
            patch.object(guard.os, "execve") as execute,
            patch.object(guard.os, "setuid") as credentials,
            patch.object(guard.os, "chdir") as directory,
            patch.object(
                lifecycle, "_transition_child", wraps=lifecycle._transition_child
            ) as transition,
            self.assertRaises((OSError, ValueError)),
        ):
            guard.main(
                [
                    "launch",
                    "--unit",
                    unit,
                    "--epoch",
                    fixture.binding["generation"],
                    "--binding-sha256",
                    cold.digest(fixture.binding),
                ]
            )
        execute.assert_not_called()
        credentials.assert_not_called()
        directory.assert_not_called()
        transition.assert_not_called()
        self.assertIsNone(lifecycle.coherent(store)["state"]["claim"])
        self.assertFalse(lifecycle.coherent(store)["state"]["execution_possible"])

    def test_parent_revoke_during_full_qualification_denies_child_and_preserves_parent_e(self):
        fixture, controller, unit, store, _ticket, _worker, _context = self.accepted_child()
        original = native._observe_package

        def revoked(*args, **kwargs):
            result = original(*args, **kwargs)
            snapshot = lifecycle.coherent(fixture.parent.store)
            lifecycle._transition_once(fixture.parent.store, snapshot["ticket"], "revoke", {})
            return result

        with patch.object(native, "_observe_package", side_effect=revoked):
            with self.assertRaises(ValueError):
                lifecycle.consume(controller, unit)
        self.assertIsNone(lifecycle.coherent(store)["state"]["claim"])
        self.assertFalse(lifecycle.coherent(store)["state"]["execution_possible"])
        self.assertTrue(lifecycle.coherent(fixture.parent.store)["state"]["execution_possible"])
        self.assertTrue(lifecycle.coherent(fixture.parent.store)["state"]["revoked"])
