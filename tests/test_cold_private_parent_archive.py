"""Pure historical replay of the private parent; no processes or host readers."""

from __future__ import annotations

import copy
import unittest
import uuid
from pathlib import PureWindowsPath
from unittest.mock import patch

from scripts import j1_forward_cold_archive as archive
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as launch


class PrivateParentArchiveTests(unittest.TestCase):
    def fixture(self, *, identity_pid=101):
        saved, root = {}, "/captured/private/parent-executor"

        def reference(path, body):
            raw = io.canonical(body)
            saved[path] = raw
            return {"path": path, "sha256": io.sha(raw)}

        binding = {
            "attempt_id": str(uuid.uuid4()),
            "boot_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "deployment_lock_identity": {"device": 1, "inode": 2, "ctime_ns": 3},
        }
        consumers = {"scripts/j1_forward_cold_release.py": "1" * 64}
        candidate = {
            "revision": "2" * 40,
            "tree": "3" * 40,
            "consumer_sha256": io.sha(io.canonical(consumers)),
        }
        request = reference(
            root + "/request.json",
            {
                "schema": "autostop.j1.private-parent-request.v1",
                "scope": "native_fixture",
                "binding": binding,
                "source": {key: candidate[key] for key in ("revision", "tree")},
                "consumer_sha256": candidate["consumer_sha256"],
                "production_native_acceptance": False,
            },
        )
        identity = {"pid": identity_pid, "start_ticks": 20, "invocation_id": "a" * 32}
        context = reference(
            root + "/context.json",
            {
                "schema": "autostop.j1.private-parent-context.v1",
                "request_ref": request,
                "actor": {"pid": 101, "start_ticks": 20},
                "identity": identity,
                "deployment_lock_identity": binding["deployment_lock_identity"],
            },
        )
        ticket = {
            "schema": launch.SCHEMA,
            "attempt_id": binding["attempt_id"],
            "nonce": str(uuid.uuid4()),
            "plan_ref": request,
            "source_sha256": candidate["consumer_sha256"],
            "context_sha256": context["sha256"],
            "host": "private.invalid",
            "boot_id": binding["boot_id"],
            "owner_generation": binding["generation"],
            "unit": "autostop-crm-forward-" + binding["attempt_id"] + ".service",
            "not_before": 100,
            "expires_at": 200,
        }
        ticket_ref = reference(root + "/journal/ticket.json", ticket)
        acceptance_ref = reference(
            root + "/native-acceptance.json",
            {
                "schema": "autostop.j1.forward-launch-native-acceptance.v1",
                "ticket_sha256": ticket_ref["sha256"],
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
                "pid": 101,
                "start_ticks": 20,
                "invocation_id": "a" * 32,
            },
        )
        accepted = {
            "pid": 101,
            "start_ticks": 20,
            "invocation_id": "a" * 32,
            "evidence_ref": acceptance_ref,
        }
        current = launch._initial(ticket_ref["sha256"])
        files = [ticket_ref]
        for event, payload, changes in (
            (
                "submit",
                {"owner": {"pid": 101, "start_ticks": 20}},
                {"dispatch_possible": True, "submit_owner": {"pid": 101, "start_ticks": 20}},
            ),
            ("accept", {"receipt": accepted}, {"acceptance": accepted}),
            ("claim", {"worker": accepted}, {"claim": accepted}),
            ("enter", {"worker": accepted}, {"execution_possible": True}),
        ):
            previous = copy.deepcopy(current)
            current.update(changes)
            current["sequence"] += 1
            current["previous_sha256"] = io.sha(io.canonical(previous))
            current["requests"][str(uuid.uuid4())] = {
                "digest": io.sha(
                    io.canonical(
                        {
                            "event": event,
                            "payload": payload,
                            "boot_id": ticket["boot_id"],
                            "generation": ticket["owner_generation"],
                        }
                    )
                ),
                "result": {
                    "event": event,
                    "sequence": current["sequence"],
                    "dispatch_possible": current["dispatch_possible"],
                    "execution_possible": current["execution_possible"],
                },
            }
            files.append(
                reference(
                    root + f"/journal/events/{current['sequence']:020d}.json",
                    {
                        "schema": "autostop.j1.forward-launch-event.v1",
                        "state": current,
                    },
                )
            )
        files.append(reference(root + "/journal/state.json", current))
        body = {
            "schema": "autostop.j1.private-parent-execution.v1",
            "scope": "native_fixture",
            "request_ref": request,
            "context_ref": context,
            "acceptance_ref": acceptance_ref,
            "journal_files": files,
            "systemd_admission_claimed": False,
        }
        manifest = reference(root + "/execution.json", body)
        baseline = {
            "controller_request_ref": request,
            "controller_launch_ref": manifest,
            "source_consumers": consumers,
        }
        return (
            saved,
            body,
            {"candidate": candidate},
            baseline,
            {"scope": "native_fixture", "binding": binding},
        )

    def replay(self, material):
        saved, body, plan, baseline, package = material
        raw = io.canonical(body)
        path = baseline["controller_launch_ref"]["path"]
        saved[path] = raw
        baseline["controller_launch_ref"]["sha256"] = io.sha(raw)

        def read(reference):
            raw = saved[reference["path"]]
            self.assertEqual(io.sha(raw), reference["sha256"])
            return raw

        return archive._controller_history(plan, baseline, package, read)

    def test_actual_sace_history_replays_as_private_evidence_with_no_live_authority(self):
        material = self.fixture()
        self.assertEqual(self.replay(material), material[1]["acceptance_ref"])

    def test_captured_posix_acceptance_replays_on_a_windows_path_consumer(self):
        material = self.fixture()
        with patch.object(launch, "Path", PureWindowsPath):
            self.assertEqual(self.replay(material), material[1]["acceptance_ref"])

    def test_private_envelope_cannot_be_used_for_production_or_claim_systemd(self):
        for target, key, value in (
            (4, "scope", "production_native"),
            (1, "scope", "production_native"),
            (1, "systemd_admission_claimed", True),
        ):
            with self.subTest(target=target, key=key):
                material = self.fixture()
                material[target][key] = value
                with self.assertRaisesRegex(io.Refusal, "fixture_parent_scope"):
                    self.replay(material)

    def test_missing_duplicate_and_outside_journal_references_are_refused(self):
        for change in ("missing", "duplicate", "outside"):
            with self.subTest(change=change):
                material = self.fixture()
                files = material[1]["journal_files"]
                if change == "missing":
                    del files[2]
                elif change == "duplicate":
                    files.append(dict(files[0]))
                else:
                    files[2] = {**files[2], "path": "/another/journal/events/one.json"}
                with self.assertRaisesRegex(io.Refusal, "fixture_journal|fixture_parent_ticket"):
                    self.replay(material)

    def test_unmatched_context_incarnation_and_source_owner_are_refused(self):
        material = self.fixture(identity_pid=999)
        with self.assertRaisesRegex(io.Refusal, "fixture_parent_incarnation"):
            self.replay(material)
        for key, value in (
            ("generation", str(uuid.uuid4())),
            ("deployment_lock_identity", {"device": 8, "inode": 9}),
        ):
            with self.subTest(key=key):
                material = self.fixture()
                material[4]["binding"][key] = value
                with self.assertRaisesRegex(io.Refusal, "fixture_request_binding"):
                    self.replay(material)

    def test_corrupt_history_cannot_be_replaced_by_execution_possible_flag(self):
        material = self.fixture()
        saved, body, _plan, _baseline, _package = material
        last = body["journal_files"][-2]
        event = io.document(saved[last["path"]])
        event["state"]["previous_sha256"] = "f" * 64
        saved[last["path"]] = io.canonical(event)
        last["sha256"] = io.sha(saved[last["path"]])
        with self.assertRaisesRegex(launch.Refusal, "history_chain"):
            self.replay(material)

    def test_terminal_crm_entry_remains_bound_to_same_parent_acceptance(self):
        material = self.fixture()
        saved, body, _plan, baseline, _package = material
        parent_a = self.replay(material)
        evidence = io.document(saved[parent_a["path"]])
        context = {
            "request_ref": baseline["controller_request_ref"],
            "parent_acceptance_ref": body["acceptance_ref"],
        }
        current = {
            "acceptance": {key: evidence[key] for key in ("pid", "start_ticks", "invocation_id")}
        }

        def read(ref):
            return saved[ref["path"]]

        archive._crm_parent(context, current, baseline, parent_a, read)
        for key, value in (("pid", 999), ("start_ticks", 999), ("invocation_id", "b" * 32)):
            with self.subTest(key=key):
                changed = copy.deepcopy(current)
                changed["acceptance"][key] = value
                with self.assertRaisesRegex(io.Refusal, "crm_parent_incarnation"):
                    archive._crm_parent(context, changed, baseline, parent_a, read)
        changed = {**context, "parent_acceptance_ref": {**parent_a, "sha256": "f" * 64}}
        with self.assertRaisesRegex(io.Refusal, "crm_parent_acceptance"):
            archive._crm_parent(changed, current, baseline, parent_a, read)


if __name__ == "__main__":
    unittest.main()
