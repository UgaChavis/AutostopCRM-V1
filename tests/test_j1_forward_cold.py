from __future__ import annotations

import copy
import json
import os
import select
import socket
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io


class ColdContractTests(unittest.TestCase):
    def test_all_six_inputs_required_and_two_denied_never_compiled(self):
        template = contract.profile_input_template()
        self.assertEqual(set(template), set(contract.ALLOWED))
        self.assertTrue(set(template).isdisjoint(contract.DENIED))
        for unit in contract.ALLOWED:
            partial = copy.deepcopy(template)
            partial.pop(unit)
            with self.assertRaisesRegex(io.Refusal, "compile_six_profiles"):
                contract.compile_policies(
                    {}, "native_fixture", partial, {}, read_reference=lambda ref: b""
                )
        for unit in contract.DENIED:
            with self.assertRaisesRegex(io.Refusal, "compile_scope"):
                contract.compile_policy(
                    {}, "native_fixture", unit, {}, {}, read_reference=lambda ref: b""
                )

    def test_default_observer_refuses_fixture_before_native_observations(self):
        with (
            patch.object(native.socket, "gethostname", return_value="unrelated-host"),
            patch.object(
                native.NativeProvider, "systemd", side_effect=AssertionError("must not run")
            ),
        ):
            with self.assertRaisesRegex(io.Refusal, "native_fixed_boundary"):
                native.verify_package(
                    {"path": "/tmp/not-production/authority.json", "sha256": "a" * 64}
                )

    def test_loaded_ignore_error_condition_cannot_count_as_a_barrier(self):
        valid = "{ path=/usr/bin/false ; argv[]=/usr/bin/false ; ignore_errors=no ; }"
        self.assertEqual(native.commands(valid), ["/usr/bin/false"])
        for raw in (
            valid.replace("ignore_errors=no", "ignore_errors=yes"),
            valid.replace("path=/usr/bin/false", "path=/usr/bin/true"),
            "-" + valid,
            valid.replace("argv[]=", "argv[]=/bin/true ; argv[]="),
        ):
            with self.assertRaises(io.Refusal):
                native.commands(raw)


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class ColdNativeFixtureTests(unittest.TestCase):
    def fixture(self, **kwargs):
        from scripts.j1_forward_cold_fixture import Fixture

        fixture = Fixture(**kwargs)
        self.addCleanup(fixture.close)
        return fixture

    def validate(self, fixture):
        return native.verify_package(
            fixture.package_ref, provider=fixture.provider, required_scope="native_fixture"
        )

    def test_six_real_guarded_entrypoints_retire_old_processes_and_preserve_eight_fences(self):
        fixture = self.fixture()
        request = fixture.submit(contract.ALLOWED[0])
        reference = fixture.produce()
        validated = self.validate(fixture)
        self.assertEqual(len(validated["policies"]), 6)
        self.assertEqual(set(validated["closure"]["barriers"]), set(contract.UNITS))
        self.assertEqual(validated["pending"]["requests"][0]["op_id"], request["op_id"])
        self.assertEqual(validated["pending"]["requests"][0]["outcome"], "applied_readback")
        for process in fixture.children[:6]:
            self.assertIsNotNone(process.poll())
            self.assertTrue(select.select([fixture.pidfds[process.pid]], [], [], 0)[0])
        for unit in contract.ALLOWED:
            result = fixture.probe(unit)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            receipt = json.loads((fixture.root / "probe-effects" / unit).read_text())
            self.assertEqual(receipt["policy_ref"], validated["package"]["policies"][unit])
        self.assertEqual(fixture.produce(), reference)  # lost reply, no new generation/effects
        self.assertEqual(len(list((fixture.root / "probe-effects").iterdir())), 6)
        for unit in contract.DENIED:
            with self.assertRaisesRegex(io.Refusal, "dispatch_unit_permanently_denied"):
                fixture.probe(unit)
        for unit in contract.UNITS:
            self.assertEqual((fixture.root / "barriers" / unit).read_bytes(), contract.DENY_BYTES)
        self.assertTrue((fixture.root / "ingress-held.json").is_file())

    def test_pending_unknown_late_backlog_and_new_peer_never_become_replay(self):
        fixture = self.fixture()
        (fixture.root / "pause-before-effect").touch(mode=0o600)
        request = fixture.submit(contract.ALLOWED[0], wait=False)
        intent = fixture.root / "requests" / request["op_id"] / "intent.json"
        deadline = time.monotonic() + 5
        while not intent.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(intent.is_file())
        # Kernel backlog and late arrivals are distinct from the persisted app
        # intent. Neither is interpreted as an empty queue or a replay permit.
        late = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(late.close)
        late.settimeout(2)
        late.connect(str(fixture.root / "ingress" / contract.ALLOWED[0]))
        late.sendall(io.canonical({"op_id": str(uuid.uuid4()), "payload_sha256": "b" * 64}))
        fixture.produce()
        value = self.validate(fixture)
        row = value["pending"]["requests"][0]
        self.assertEqual((row["outcome"], row["retry"]), ("unknown", "forbidden"))
        self.assertFalse((intent.parent / "effect.json").exists())
        self.assertEqual(value["quarantine"]["unparsed_ingress"], "retired_without_replay")
        with self.assertRaises(OSError):
            fresh = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                fresh.connect(str(fixture.root / "ingress" / contract.ALLOWED[0]))
            finally:
                fresh.close()
        with self.assertRaisesRegex(io.Refusal, "submit_closed"):
            fixture.submit(contract.ALLOWED[0], op_id=request["op_id"])
        self.assertEqual(fixture.probe(contract.ALLOWED[0]).returncode, 0)
        self.assertFalse((intent.parent / "effect.json").exists())

    def test_unknown_peer_same_uid_is_not_adopted_as_owned_ingress(self):
        fixture = self.fixture()
        script = (
            "import socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
            "s.connect(sys.argv[1]);s.sendall(b'{}\\n');print(s.recv(64).decode().strip())"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                script,
                str(fixture.root / "ingress" / contract.ALLOWED[0]),
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), b"FOREIGN")
        self.assertEqual(list((fixture.root / "requests").iterdir()), [])
        fixture.produce()
        self.assertEqual(self.validate(fixture)["pending"]["requests"], [])

    def test_dispatched_intent_without_registered_client_is_not_hidden(self):
        fixture = self.fixture()
        (fixture.root / "pause-before-effect").touch(mode=0o600)
        op_id = str(uuid.uuid4())
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(peer.close)
        peer.connect(str(fixture.root / "ingress" / contract.ALLOWED[0]))
        peer.sendall(io.canonical({"op_id": op_id, "payload_sha256": "b" * 64}))
        deadline = time.monotonic() + 5
        while (
            not (fixture.root / "requests" / op_id / "intent.json").exists()
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        with self.assertRaisesRegex(io.Refusal, "unregistered_intent"):
            fixture.produce()
        self.assertFalse((fixture.root / "authority.json").exists())
        self.assertTrue((fixture.root / "ingress-held.json").exists())

    def test_lost_hold_even_if_another_descriptor_relocks_cannot_validate(self):
        import fcntl

        fixture = self.fixture()
        fixture.produce()
        self.validate(fixture)
        fcntl.flock(fixture.dispatch_fd, fcntl.LOCK_UN)
        other = os.open(fixture.root / "dispatch.lock", os.O_RDONLY)
        self.addCleanup(os.close, other)
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(io.Refusal, "hold_lock_not_owned"):
            self.validate(fixture)
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])

    def test_barrier_or_source_drift_and_expiry_refuse_without_opening(self):
        fixture = self.fixture()
        fixture.produce()
        barrier = fixture.root / "barriers" / contract.ALLOWED[0]
        original = barrier.read_bytes()
        barrier.write_bytes(b"[Service]\nExecCondition=/usr/bin/true\n")
        with self.assertRaisesRegex(io.Refusal, "reference_digest"):
            self.validate(fixture)
        barrier.write_bytes(original)
        self.validate(fixture)
        with patch.object(
            contract.time, "monotonic_ns", return_value=fixture.binding["deadline_ns"]
        ):
            with self.assertRaisesRegex(io.Refusal, "binding_expired_or_window"):
                self.validate(fixture)
        extra = fixture.root / "source" / "unreviewed.py"
        extra.write_text("raise RuntimeError('not accepted')\n")
        with self.assertRaisesRegex(io.Refusal, "source_inventory"):
            self.validate(fixture)
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])
        self.assertEqual(len(list((fixture.root / "barriers").iterdir())), 8)

    def test_fixture_cannot_be_accepted_for_production_and_pending_proof_is_not_truth(self):
        fixture = self.fixture()
        (fixture.root / "pause-before-effect").touch(mode=0o600)
        fixture.submit(contract.ALLOWED[0], wait=False)
        fixture.produce()
        with self.assertRaisesRegex(io.Refusal, "package_scope"):
            contract.validate_package(
                fixture.package_ref,
                read_reference=fixture.provider.read_reference,
                required_scope="production_native",
            )
        original = self.validate(fixture)
        tampered = copy.deepcopy(original)
        tampered["pending"]["requests"][0]["outcome"] = "not_applied_terminal"
        with self.assertRaises(io.Refusal):
            fixture.provider.verify_pending(tampered)
        tampered = copy.deepcopy(original)
        tampered["retirement"]["actors"].append(original["kernel"]["actor"])
        with self.assertRaisesRegex(io.Refusal, "cannot_adopt_actor"):
            fixture.provider.verify_retirement(tampered)
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])

    def test_ready_bytes_cannot_replace_the_actual_new_kernel_profile(self):
        fixture = self.fixture()
        fixture.produce()
        original = self.validate(fixture)
        for key, change in (
            ("argv", lambda value: [*value, "stale-generation"]),
            ("credentials", lambda value: {**value, "uid": value["uid"] + 1}),
        ):
            tampered = copy.deepcopy(original)
            tampered["kernel_policy"][key] = change(tampered["kernel_policy"][key])
            with self.assertRaisesRegex(io.Refusal, "kernel_actual_profile"):
                fixture.provider.verify_kernel(tampered)
        # A valid READY snapshot and genuine live PID remain present throughout;
        # neither makes an incorrect new kernel execution binding acceptable.
        self.validate(fixture)
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])

    def test_preserved_scope_cannot_omit_actor_or_substitute_empty_cgroup(self):
        from scripts import j1_forward_cold_history as history

        fixture = self.fixture()
        fixture.produce()
        original = self.validate(fixture)
        incomplete = copy.deepcopy(original)
        incomplete["origin"]["actors"] = incomplete["origin"]["actors"][:-1]
        incomplete["retirement"]["actors"] = incomplete["origin"]["actors"]
        with self.assertRaisesRegex(io.Refusal, "history_actor_coverage"):
            history.check_coverage(incomplete, original["history"])
        containment = io.document(
            fixture.provider.read_reference(original["origin"]["containment_ref"])
        )
        detached = copy.deepcopy(containment)
        detached["cgroup_roots"] = ["/sys/fs/cgroup/autostop-j1-cold-retired.slice/unrelated-empty"]
        with self.assertRaisesRegex(io.Refusal, "containment_history_coverage"):
            history.check_containment(detached, original, original["history"])
        no_descriptors = copy.deepcopy(original)
        no_descriptors["quarantine"]["pipe_inodes"] = []
        with self.assertRaisesRegex(io.Refusal, "history_descriptor_coverage"):
            history.check_coverage(no_descriptors, original["history"])
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])

    def test_original_hold_remeasures_raw_sqlite_instead_of_saved_true_receipt(self):
        import sqlite3

        from scripts import j1_forward_cold_hold as hold

        fixture = self.fixture()
        fixture.produce()
        original = self.validate(fixture)
        database = fixture.root / "registry.sqlite3"
        before = database.stat()
        paths = sorted(str(path) for path in fixture.root.rglob("*"))
        fixture.provider.verify_original_hold(original)
        self.assertEqual(database.stat(), before)
        self.assertEqual(sorted(str(path) for path in fixture.root.rglob("*")), paths)
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE manager_automation_controller_runtime SET hold_enabled=0")
            connection.commit()
        finally:
            connection.close()
        forged = copy.deepcopy(original)
        ref = fixture.provider.reference(database)
        backup = fixture.publish("evidence/forged-quiet.sqlite3", database.read_bytes(), raw=True)
        forged["hold_checkpoint"].update(
            database_ref=ref,
            quiet_backup_ref=backup,
            database_identity=hold.file_identity(database),
        )
        # Old saved hold=True and new HOLD marker remain. Fresh SQL semantics
        # still refuse, even with consistent caller-chosen file hashes.
        with self.assertRaisesRegex(io.Refusal, "hold_original_ownership_lost"):
            fixture.provider.verify_original_hold(forged)
        self.assertTrue((fixture.root / "ingress-held.json").exists())
        self.assertEqual(len(list((fixture.root / "barriers").iterdir())), 8)

    def test_duplicate_guarded_entry_does_not_replace_a_completed_probe(self):
        fixture = self.fixture()
        fixture.produce()
        unit = contract.ALLOWED[0]
        self.assertEqual(fixture.probe(unit).returncode, 0)
        path = fixture.root / "probe-effects" / unit
        before = path.read_bytes()
        result = fixture.probe(unit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
