from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_telegram as telegram
from scripts import j1_forward_io as io


class TelegramFrontierTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "attempt_id": "11111111-1111-4111-8111-111111111111",
            "boot_id": "22222222-2222-4222-8222-222222222222",
            "generation": "33333333-3333-4333-8333-333333333333",
            "consumer_sha256": "c" * 64,
            "candidate_crm_sha": "d" * 40,
            "candidate_manager_sha": "e" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": 100,
            "deadline_ns": 1000,
        }
        self.policy = {
            "schema": telegram.FRONTIER_SCHEMA,
            "scope": "production_native",
            "binding": self.binding,
            "source_revision": self.binding["candidate_manager_sha"],
            "roles": list(telegram.ROLES),
            "cut": telegram.FRONTIER_CUT,
            "account_self_sha256": {
                role: letter * 64 for role, letter in zip(telegram.ROLES, "ab")
            },
        }
        self.policy_ref = {
            "path": "/protected/frontier-policy.json",
            "sha256": cold.digest(self.policy),
        }
        self.actors, self.samples, self.raw, self.published = {}, {}, {}, []
        for index, role in enumerate(telegram.ROLES, 1):
            actor = {
                "logical_unit": telegram.UNITS[role],
                "execution_unit": f"autostop-crm-forward-00000000-0000-4000-8000-{index:012d}.service",
                "policy_ref": {"path": f"/protected/{role}.json", "sha256": "f" * 64},
                "pid": index + 100,
                "start_ticks": index + 1000,
                "invocation_id": str(index) * 32,
            }
            self.actors[role] = actor
            self.samples[role] = {
                **actor,
                "boot_id": self.binding["boot_id"],
                "source_revision": self.binding["candidate_manager_sha"],
                "started_monotonic_ns": 150,
            }
            cut_path, quarantine_path = telegram._paths(self.binding, role, actor)
            cut = {
                "schema": telegram.CUT_SCHEMA,
                **{key: self.binding[key] for key in ("attempt_id", "boot_id", "generation")},
                "epoch_id": self.binding["generation"],
                "source_revision": self.binding["candidate_manager_sha"],
                "role": role,
                "main_pid": actor["pid"],
                "start_ticks": actor["start_ticks"],
                "observed_monotonic_ns": 200,
                "account_self_sha256": self.policy["account_self_sha256"][role],
                "pts": 10,
                "qts": 0,
                "seq": 1,
                "supported_updates": "private_account_global_pts_only",
                "historical_replay": "denied",
                "observation_only": True,
                "release_authority": False,
                "quarantine_path": quarantine_path,
            }
            quarantine = {key: value for key, value in cut.items() if key != "quarantine_path"}
            quarantine.update(
                schema=telegram.QUARANTINE_SCHEMA,
                status="clear",
                records=[],
                overflow_count=0,
                overflow_chain_sha256="0" * 64,
                complete_observation_log=True,
                historical_inventory_complete=False,
                replay="forbidden",
            )
            self.raw[cut_path] = io.canonical(cut)
            self.raw[quarantine_path] = io.canonical(quarantine)

    def observe(self, **changes):
        def publish(name, document):
            self.published.append(copy.deepcopy(document))
            return {"path": "/protected/" + name + ".json", "sha256": cold.digest(document)}

        options = dict(
            policy_ref=self.policy_ref,
            policy=self.policy,
            actors_by_role=self.actors,
            sample_process=lambda role: copy.deepcopy(self.samples[role]),
            publish=publish,
            now_ns=300,
            read_bytes=lambda path: self.raw[path],
        )
        options.update(changes)
        return telegram.observe(self.binding, **options)

    def mutate(self, role, filename, change):
        path = telegram._paths(self.binding, role, self.actors[role])[filename == "quarantine"]
        document = io.document(self.raw[path])
        change(document)
        self.raw[path] = io.canonical(document)

    def test_both_actual_cuts_and_process_bookends_are_required(self):
        result = self.observe()
        self.assertEqual(set(telegram.ROLES), set(result["document"]["roles"]))
        self.assertEqual(result["reference"]["sha256"], result["digest"])
        self.assertFalse(result["document"]["native_acceptance"])
        self.assertFalse(result["document"]["historical_inventory_complete"])
        self.assertNotIn("peer_id", json.dumps(result["document"]))
        telegram.validate_document(
            result["document"],
            binding=self.binding,
            policy_ref=self.policy_ref,
            policy=self.policy,
            actors_by_role=self.actors,
            now_ns=301,
        )

    def test_expected_history_hold_is_preserved_without_full_inventory_claim(self):
        def held(document):
            identity = {"source": "history_not_queried", "peer": None, "message": None}
            document.update(
                status="needs_review",
                records=[
                    {
                        "locator": {
                            "role": telegram.ROLES[0],
                            "peer_id": None,
                            "message_id": None,
                            "pts": None,
                        },
                        "source_identity_sha256": io.sha(
                            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
                        ),
                        "observed_record_sha256": "1" * 64,
                        "source": "history_not_queried",
                        "reason": "cold_history_requires_review",
                        "status": "needs_review",
                    }
                ],
            )

        self.mutate(telegram.ROLES[0], "quarantine", held)
        result = self.observe()["document"]["roles"][telegram.ROLES[0]]["quarantine"]
        self.assertEqual("needs_review", result["status"])
        self.assertEqual(1, result["record_count"])
        self.assertFalse(result["historical_inventory_complete"])
        self.assertNotIn("records", result)

    def test_strict_cut_mutations_refuse_before_publication(self):
        for key, value in [
            ("main_pid", True),
            ("main_pid", 999),
            ("start_ticks", 2),
            ("source_revision", "a" * 40),
            ("role", "scheduler"),
            ("boot_id", self.binding["attempt_id"]),
            ("epoch_id", self.binding["attempt_id"]),
            ("account_self_sha256", "f" * 64),
            ("pts", True),
            ("pts", -1),
            ("pts", 2**31),
            ("seq", 1.0),
            ("qts", "0"),
            ("observed_monotonic_ns", 149),
            ("observed_monotonic_ns", 301),
            ("observation_only", 1),
            ("release_authority", 0),
            ("extra", None),
            ("quarantine_path", "/old/elsewhere.json"),
        ]:
            with self.subTest(key=key, value=value):
                self.setUp()
                self.mutate(telegram.ROLES[0], "cut", lambda d: d.update({key: value}))
                with self.assertRaises(ValueError):
                    self.observe()
                self.assertFalse(self.published)

    def test_quarantine_overflow_unknown_context_and_extra_records_deny(self):
        for key, value in [
            ("overflow_count", 1),
            ("overflow_count", False),
            ("overflow_chain_sha256", "a" * 64),
            ("complete_observation_log", False),
            ("historical_inventory_complete", True),
            ("main_pid", 999),
            ("release_authority", 0),
            ("replay", "allowed"),
            ("records", [None]),
            ("status", "needs_review"),
            ("extra", None),
        ]:
            with self.subTest(key=key):
                self.setUp()
                self.mutate(telegram.ROLES[1], "quarantine", lambda d: d.update({key: value}))
                with self.assertRaises(ValueError):
                    self.observe()
                self.assertFalse(self.published)

    def test_reused_pid_source_change_and_cross_role_process_race_deny(self):
        for field, value in [
            ("start_ticks", 9999),
            ("invocation_id", "a" * 32),
            ("source_revision", "a" * 40),
            ("boot_id", self.binding["attempt_id"]),
            ("started_monotonic_ns", 201),
        ]:
            with self.subTest(field=field):
                self.setUp()
                calls = {}

                def sample(role):
                    calls[role] = calls.get(role, 0) + 1
                    result = copy.deepcopy(self.samples[role])
                    if role == telegram.ROLES[0] and calls[role] == 2:
                        result[field] = value
                    return result

                with self.assertRaises(ValueError):
                    self.observe(sample_process=sample)
                self.assertFalse(self.published)

    def test_volatile_file_second_read_must_match_and_missing_role_denies(self):
        for filename in ("cut", "quarantine"):
            with self.subTest(filename=filename):
                self.setUp()
                target = telegram._paths(
                    self.binding, telegram.ROLES[0], self.actors[telegram.ROLES[0]]
                )[filename == "quarantine"]
                calls = {}

                def read(path):
                    calls[path] = calls.get(path, 0) + 1
                    if path == target and calls[path] == 2:
                        return self.raw[path] + b" "
                    return self.raw[path]

                with self.assertRaises(ValueError):
                    self.observe(read_bytes=read)
                self.assertFalse(self.published)
        self.setUp()
        del self.raw[
            telegram._paths(self.binding, telegram.ROLES[1], self.actors[telegram.ROLES[1]])[0]
        ]
        with self.assertRaises(KeyError):
            self.observe()
        self.assertFalse(self.published)

    def test_v1_or_self_unqualified_policy_and_expired_binding_deny(self):
        for change in (
            lambda p: p.update(schema="autostop.j1.cold-telegram-frontier-policy.v1"),
            lambda p: p.pop("account_self_sha256"),
            lambda p: p["account_self_sha256"].pop(telegram.ROLES[0]),
            lambda p: p.update(scope="native_fixture"),
        ):
            self.setUp()
            change(self.policy)
            with self.assertRaises(ValueError):
                self.observe()
            self.assertFalse(self.published)
        self.setUp()
        for now in (True, 99, 1000):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.observe(now_ns=now)

    def test_observation_mutation_or_deadline_cannot_be_reused(self):
        result = self.observe()["document"]
        for change in (
            lambda d: d.update(native_acceptance=True),
            lambda d: d["roles"].pop(telegram.ROLES[0]),
            lambda d: d["roles"][telegram.ROLES[0]].update(cut_sha256="0" * 64),
            lambda d: d["roles"][telegram.ROLES[0]]["quarantine"].update(overflow_count=1),
        ):
            document = copy.deepcopy(result)
            change(document)
            with self.assertRaises(ValueError):
                telegram.validate_document(
                    document,
                    binding=self.binding,
                    policy_ref=self.policy_ref,
                    policy=self.policy,
                    actors_by_role=self.actors,
                    now_ns=301,
                )
        with self.assertRaises(ValueError):
            telegram.validate_document(
                result,
                binding=self.binding,
                policy_ref=self.policy_ref,
                policy=self.policy,
                actors_by_role=self.actors,
                now_ns=1000,
            )

    @unittest.skipUnless(os.name == "posix", "Native metadata permissions are POSIX")
    def test_private_filesystem_reader_checks_modes_links_and_actual_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {}
            for index, (logical, raw) in enumerate(self.raw.items()):
                path = root / str(index)
                path.write_bytes(raw)
                path.chmod(0o600)
                paths[logical] = path
            with (
                patch.object(io, "protected_directory", side_effect=lambda path: Path(path)),
                patch.object(io, "ROOT_UID", os.geteuid()),
            ):
                self.observe(read_bytes=lambda logical: telegram.protected_read(paths[logical]))
                target = next(iter(paths.values()))
                target.chmod(0o644)
                with self.assertRaises(ValueError):
                    telegram.protected_read(target)
                target.chmod(0o600)
                link = root / "hardlink"
                os.link(target, link)
                with self.assertRaises(ValueError):
                    telegram.protected_read(target)
                link.unlink()
                link.symlink_to(target)
                with self.assertRaises(ValueError):
                    telegram.protected_read(link)


if __name__ == "__main__":
    unittest.main()
