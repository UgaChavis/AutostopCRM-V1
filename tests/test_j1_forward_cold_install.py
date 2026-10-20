from __future__ import annotations

import copy
import time
import unittest
import uuid

from scripts import j1_forward_cold_authority as authority
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_guard as guard
from scripts import j1_forward_cold_install as install
from scripts import j1_forward_io as io


class InertInstallerTests(unittest.TestCase):
    def test_real_operation_ids_are_preserved_and_only_hashed_for_paths(self):
        for operation in ("tg-auto-reply:chat:message", "effect/request/../old", "операция:17"):
            self.assertTrue(authority.operation_id(operation))
            key = authority.operation_key(operation)
            self.assertRegex(key, "^[0-9a-f]{64}$")
            self.assertEqual(key, io.sha(operation.encode("utf-8")))
        for operation in (None, True, "", "x" * 201, "bad\x00id", "bad\nid", "\udfff"):
            self.assertFalse(authority.operation_id(operation))
            with self.assertRaises(io.Refusal):
                authority.operation_key(operation)

    def setUp(self):
        self.binding = {
            "attempt_id": str(uuid.uuid4()),
            "boot_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "consumer_sha256": "1" * 64,
            "candidate_crm_sha": "2" * 40,
            "candidate_manager_sha": "3" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": time.monotonic_ns(),
            "deadline_ns": time.monotonic_ns() + 30 * 10**9,
        }
        self.raw, self.effects, self.holds = {}, [], []

        def reference(name, raw):
            self.raw[name] = raw
            return {"path": name, "sha256": io.sha(raw)}

        self.manager = {
            name: reference("/input/manager/" + name, raw)
            for name, raw in {
                "REVISION": (self.binding["candidate_manager_sha"] + "\n").encode(),
                "scripts/cold-role-start.py": b"# accepted fixture bootstrap\n",
                "autostop_manager/cold_runtime.py": b"# accepted fixture library\n",
            }.items()
        }
        self.controllers = {
            name: reference(
                "/input/" + name + ".py", (install.SOURCE / (name + ".py")).read_bytes()
            )
            for name in {*guard.BOOTSTRAP_HASHES, "j1_forward_cold_guard"}
        }
        self.env, self.bindings, self.credentials = {}, {}, {}
        tombstones = reference("/input/tombstones.json", b"{}\n")
        fence = reference("/input/fence.json", b"{}\n")
        for role in epochs.CONSUMERS:
            self.env[role] = reference("/input/" + role + ".env", b"NO_SECRETS=fixture\n")
            self.bindings[role] = epochs.runtime_binding(
                self.binding,
                role,
                epochs.expected_paths(
                    self.binding["generation"], self.binding["candidate_manager_sha"], role
                ),
                tombstones,
                fence,
            )
            self.credentials[role] = {"uid": 10001, "gid": 10001, "groups": [10001]}
        fixture = self

        class Backend:
            scope = "native_fixture"

            def hold(self, descriptor, binding):
                fixture.holds.append((descriptor, copy.deepcopy(binding)))

            def read(self, ref):
                raw = fixture.raw[ref["path"]]
                io.require(io.sha(raw) == ref["sha256"], "fixture_input_changed")
                return raw

            def publish(self, path, raw, **permissions):
                fixture.effects.append((str(path), raw, permissions))
                return {"path": str(path), "sha256": io.sha(raw)}

            def directory(self, path, **permissions):
                fixture.effects.append((str(path), None, permissions))

        self.backend = Backend()

    def stage(self, **options):
        return install.inert_stage(
            self.binding,
            manager_files=self.manager,
            controller_files=self.controllers,
            runtime_bindings=self.bindings,
            runtime_env=self.env,
            credentials=self.credentials,
            deployment_fd=42,
            backend=self.backend,
            **options,
        )

    def test_qualified_controller_closure_includes_fixed_kernel_bootstrap_and_every_consumer(self):
        for name in (
            "j1_forward_cold_kernel_supervisor",
            "j1_forward_cold_kernel_bootstrap",
            "j1_manager_transition_source",
        ):
            raw = (install.SOURCE / (name + ".py")).read_bytes()
            path = "/input/" + name + ".py"
            self.raw[path] = raw
            self.controllers[name] = {"path": path, "sha256": io.sha(raw)}
        source = {
            "revision": self.binding["candidate_crm_sha"],
            "tree": "4" * 40,
            "consumers": {
                "scripts/" + name + ".py": reference["sha256"]
                for name, reference in self.controllers.items()
            },
        }
        self.stage(controller_source=source)
        self.assertTrue(
            any(
                path.endswith("/j1_forward_cold_kernel_supervisor.py")
                for path, _raw, _permissions in self.effects
            )
        )
        self.effects.clear()
        source["consumers"]["scripts/j1_forward_missing_dependency.py"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "controller_inventory"):
            self.stage(controller_source=source)
        self.assertEqual(self.effects, [])

    def test_seven_fresh_bindings_and_exact_bytes_without_units_or_authority(self):
        receipt = self.stage()
        self.assertEqual(set(receipt["runtime_bindings"]), epochs.CONSUMERS)
        self.assertEqual(len(self.holds), 3)
        self.assertFalse(receipt["activation_authorized"])
        self.assertFalse(receipt["application_imported"])
        paths = [path for path, _raw, _permissions in self.effects]
        self.assertFalse(any("/etc/systemd" in path or "selector" in path for path in paths))
        fresh = [entry for entry in self.effects if entry[1] is None]
        self.assertEqual(len(fresh), 14)
        self.assertTrue(all(entry[2]["empty"] is True for entry in fresh))
        self.assertNotIn("NO_SECRETS", str(receipt))

    def test_bad_seventh_binding_or_bytes_are_rejected_before_any_write(self):
        cases = []
        cases.append(
            lambda: self.bindings["crm-embedded"]["paths"].update(
                manager_db="/legacy/manager.sqlite3"
            )
        )
        cases.append(lambda: self.manager.update({"../outside": self.manager["REVISION"]}))
        cases.append(lambda: self.raw.update({"/input/manager/REVISION": b"old\n"}))
        cases.append(lambda: self.controllers.pop("j1_forward_cold_guard"))
        for damage in cases:
            with self.subTest(damage=damage):
                self.setUp()
                damage()
                with self.assertRaises(io.Refusal):
                    self.stage()
                self.assertEqual(self.effects, [])

    def test_start_argv_uses_physical_candidate_venv_and_explicit_bootstrap(self):
        root = "/opt/autostop-manager-cold-releases/" + self.binding["candidate_manager_sha"]
        argv = install.policy_argv(
            self.binding, "j1", {"path": root + "/venv/bin/python3.12", "sha256": "4" * 64}
        )
        self.assertEqual(argv[-2:], ["--role", "j1"])
        self.assertEqual(argv[1:3], ["-I", "-B"])
        for role, executable in (
            ("crm-embedded", root + "/venv/bin/python"),
            ("j1", "/usr/bin/python3"),
        ):
            with self.assertRaises(io.Refusal):
                install.policy_argv(self.binding, role, {"path": executable, "sha256": "4" * 64})


if __name__ == "__main__":
    unittest.main()
