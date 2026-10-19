from __future__ import annotations

import copy
import unittest
import uuid

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io


class ProducerSourceGraphTests(unittest.TestCase):
    def fixture(self, damage=None):
        files = {}

        def ref(name, value):
            raw = value if type(value) is bytes else io.canonical(value)
            path = "/private/" + name
            files[path] = raw
            return {"path": path, "sha256": io.sha(raw)}

        leaf = ref("leaf.json", {})
        guard, native = ref("guard.py", b"guard"), ref("native.py", b"native")
        consumers = {
            "scripts/j1_forward_cold_" + name + ".py": io.sha(name.encode())
            for name in (
                "authority",
                "native",
                "guard",
                "history",
                "hold",
                "epoch",
                "state",
                "release",
            )
        }
        binding = {
            "attempt_id": str(uuid.uuid4()),
            "boot_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "candidate_crm_sha": "1" * 40,
            "candidate_manager_sha": "2" * 40,
            "consumer_sha256": cold.digest(
                {
                    name: consumers["scripts/j1_forward_cold_" + name + ".py"]
                    for name in ("authority", "native", "guard", "history", "hold")
                }
            ),
            "deployment_lock_identity": [1, 2],
            "not_before_ns": 1,
            "deadline_ns": 100,
        }

        def bound(name, **extra):
            return {
                "schema": "autostop.j1.cold-" + name + ".v1",
                "scope": "production_native",
                "binding": binding,
                **extra,
            }

        inputs = bound("producer-inputs", **{key: leaf for key in cold.PRODUCER_INPUT_FIELDS})
        inputs.update(manager={}, runtime_env={}, credentials={}, kernel={})
        if damage == "predecessor":
            inputs["predecessor_ref"] = ref("other-origin.json", {})
        inputs_ref = ref("inputs.json", inputs)
        request = {
            "schema": "autostop.j1.cold-controller-request.v1",
            "scope": "bounded_cold_prepare",
            "attempt_id": binding["attempt_id"],
            "host": "isolated.invalid",
            "boot_id": binding["boot_id"],
            "owner_generation": binding["generation"],
            "source": {
                "revision": binding["candidate_crm_sha"],
                "tree": "3" * 40,
                "consumers": consumers,
            },
            "candidate": {
                "revision": binding["candidate_crm_sha"],
                "tree": "3" * 40,
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": cold.digest(consumers),
            },
            "candidate_manager_sha": binding["candidate_manager_sha"],
            "inputs_ref": inputs_ref,
            "ci_ref": leaf,
            "qualification_ref": leaf,
            "created_at": "2026-10-19T00:00:00+00:00",
            "deadline_utc": "2026-10-19T00:05:00+00:00",
            "runtime_seconds": 900,
            "old_writer_allowed": False,
        }
        if damage == "inputs":
            request["inputs_ref"] = leaf
        elif damage == "candidate":
            request["candidate_manager_sha"] = "4" * 40
        elif damage == "consumer":
            consumers["scripts/j1_forward_cold_guard.py"] = "5" * 64
            request["candidate"]["consumer_sha256"] = cold.digest(consumers)
        elif damage == "extra_request":
            request["allow_unknown"] = True
        receipt = bound("producer-receipt", **{key: leaf for key in cold.PRODUCER_RECEIPT_REFS})
        receipt["request_ref"] = ref("request.json", request)
        if damage == "receipt":
            del receipt["runtime_installation_ref"]
        source = bound(
            "source-package",
            revision=binding["candidate_manager_sha"],
            source_index_ref=leaf,
            guard_ref=guard,
            native_ref=native,
            producer_inputs_ref=inputs_ref,
            producer_receipt_ref=ref("receipt.json", receipt),
        )
        source["schema"] = "autostop.j1.cold-source-package.v2"
        if damage == "guard":
            source["guard_ref"] = leaf
        return (
            cold.Reader(lambda row: files[row["path"]]),
            ref("source.json", source),
            binding,
            leaf,
        )

    def test_full_preparation_provenance_is_captured_without_becoming_a_permit(self):
        reader, reference, binding, predecessor = self.fixture()
        value = cold._source_package(
            reader, reference, binding, "production_native", predecessor_ref=predecessor
        )
        self.assertEqual(value["schema"], "autostop.j1.cold-source-package.v2")
        self.assertIn(value["producer_inputs_ref"]["path"], reader.seen)
        self.assertIn(value["producer_receipt_ref"]["path"], reader.seen)
        self.assertIn("/private/request.json", reader.seen)
        reader.recheck()
        self.assertNotIn("activation_authorized", value)

    def test_omitted_or_cross_bound_preparation_evidence_is_refused(self):
        for damage in (
            "predecessor",
            "inputs",
            "candidate",
            "consumer",
            "guard",
            "receipt",
            "extra_request",
        ):
            with self.subTest(damage=damage):
                reader, reference, binding, predecessor = self.fixture(damage)
                with self.assertRaises(io.Refusal):
                    cold._source_package(
                        reader, reference, binding, "production_native", predecessor_ref=predecessor
                    )

    def test_fixture_source_v1_keeps_its_original_exact_shape(self):
        reader, reference, binding, _ = self.fixture()
        value = copy.deepcopy(reader.doc(reference))
        value.update(schema="autostop.j1.cold-source-package.v1", scope="native_fixture")
        del value["producer_inputs_ref"], value["producer_receipt_ref"]
        raw = io.canonical(value)
        reference = {"path": "/private/fixture.json", "sha256": io.sha(raw)}
        reader = cold.Reader(lambda ref: raw)
        self.assertEqual(cold._source_package(reader, reference, binding, "native_fixture"), value)
        with self.assertRaises(io.Refusal):
            cold._source_package(reader, reference, binding, "production_native")
