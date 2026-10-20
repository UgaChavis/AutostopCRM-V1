"""Pure boundary/context checks; actual systemd runs only on the hosted VM."""

from __future__ import annotations

import contextlib
import io as streams
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_io as io
from scripts import j1_isolated_systemd_rehearsal as hosted
from scripts import rehearse_cold_systemd as stand


class ColdSystemdBoundaryTests(unittest.TestCase):
    def test_local_call_stops_before_proc_systemd_fixture_or_directory_effects(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(hosted.Path, "read_text") as read,
            patch.object(hosted.os.path, "lexists") as exists,
            patch.object(hosted.subprocess, "run") as command,
            patch.object(stand, "run_case") as fixture,
            patch.object(stand.Path, "mkdir") as directory,
        ):
            with self.assertRaisesRegex(ValueError, "github_hosted_linux_root_required"):
                stand.main(["--output-dir", "/private/unreachable"])
        for operation in (read, exists, command, fixture, directory):
            operation.assert_not_called()

    def test_cleanup_refuses_an_unowned_namespace_before_reading_it(self):
        for path in (
            Path("/opt/autostopcrm"),
            Path("/run/autostop-cold-systemd-" + "-" * 36),
            Path("/tmp/autostop-cold-systemd-" + str(uuid.uuid4())),
            Path("/run/another-" + str(uuid.uuid4())),
        ):
            with self.subTest(path=path), patch.object(stand.Path, "lstat") as read:
                with self.assertRaisesRegex(stand.StandRefusal, "cleanup_scope"):
                    stand.private_root(path)
                read.assert_not_called()

    def test_production_backend_still_rejects_a_fixture_provider(self):
        with self.assertRaisesRegex(io.Refusal, "native_provider_required"):
            lifecycle.NativeBackend(SimpleNamespace(scope="native_fixture"))

    def test_private_temp_parent_preserves_the_actual_unix_socket_path_budget(self):
        stand.unix_path_budget(Path("/run/acs-0123456789abcdef"))
        with self.assertRaisesRegex(stand.StandRefusal, "unix_path_budget"):
            stand.unix_path_budget(Path("/run/autostop-cold-systemd-" + str(uuid.uuid4())))

    def inputs(self, temporary):
        """Declared pure values, with no native observation or service claim."""
        unit = cold.ALLOWED[0]
        root = Path(temporary)
        bootstrap = {"path": str(Path("/usr/bin/python3").resolve()), "sha256": "1" * 64}
        binding = {
            "attempt_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "boot_id": str(uuid.uuid4()),
            "consumer_sha256": "2" * 64,
            "deadline_ns": 10**30,
        }
        policy = {
            "interpreter_ref": bootstrap,
            "argv": [bootstrap["path"], "-B", "/private/fixture.py"],
            "cwd": temporary,
            "credentials": {"uid": 0, "gid": 0, "groups": [0]},
        }
        store = SimpleNamespace(root=root / "new-slot", create=Mock())
        controller = SimpleNamespace(
            root=root,
            package_ref={"path": "/private/authority.json", "sha256": "3" * 64},
            epoch_ref={"path": "/private/epoch.json", "sha256": "4" * 64},
            epoch={"slots": {unit: str(uuid.uuid4())}},
            store=lambda _unit: store,
            verify=lambda: (
                {
                    "policies": {unit: policy},
                    "package": {
                        "policies": {unit: {"path": "/private/policy.json", "sha256": "5" * 64}}
                    },
                },
                None,
            ),
        )
        fixture = SimpleNamespace(
            root=root, interpreter=bootstrap, binding=binding, provider=object()
        )
        return fixture, controller, store

    def test_synthetic_cwd_and_digest_are_fixed_before_initial_ticket_creation(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(lifecycle, "_environment", return_value={"PATH": "/usr/bin:/bin"}),
            patch.object(lifecycle, "_publish") as publish,
        ):
            fixture, controller, store = self.inputs(temporary)
            unit, context, ticket = stand.stage_slot(fixture, controller, object())
        store.create.assert_called_once_with(ticket)
        self.assertEqual(context["worker_cwd"], "/")
        self.assertEqual(context["scope"], "native_fixture")
        self.assertEqual(context["worker_argv"][5], "launch")
        self.assertEqual(ticket["context_sha256"], io.sha(io.canonical(context)))
        self.assertEqual(
            ticket["unit"], "autostop-crm-forward-" + controller.epoch["slots"][unit] + ".service"
        )
        self.assertEqual(publish.call_args.args[1], context)
        self.assertFalse(
            any(
                key.startswith("LD_") or key == "PYTHONPATH"
                for key in context["bootstrap_environment"]
            )
        )

    def test_existing_slot_is_never_rebound_to_another_context(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(lifecycle, "_environment", return_value={}),
            patch.object(lifecycle, "_publish") as publish,
        ):
            fixture, controller, store = self.inputs(temporary)
            store.root.mkdir()
            with self.assertRaisesRegex(stand.StandRefusal, "previously_created"):
                stand.stage_slot(fixture, controller, object())
        store.create.assert_not_called()
        publish.assert_not_called()

    def test_counts_require_a_validated_complete_journal_before_listing_files(self):
        with (
            patch.object(lifecycle, "coherent", side_effect=io.Refusal("corrupt_chain")),
            patch.object(stand.Path, "iterdir") as listing,
        ):
            with self.assertRaisesRegex(io.Refusal, "corrupt_chain"):
                stand.journal_counts(SimpleNamespace(root=Path("/private/unreachable")))
        listing.assert_not_called()

    def test_failure_diagnostic_excludes_environment_values_and_unparsed_logs(self):
        backend = object.__new__(stand.HostedFixtureBackend)
        backend.unit = "autostop-crm-forward-" + str(uuid.uuid4()) + ".service"
        raw = (
            "Id=" + backend.unit + "\nEnvironment=TOKEN=private-placeholder\nMainPID=0\n"
        ).encode()
        logs = b"Refusal: cold_fixture_reference_digest\nAuthorization: private-placeholder\nraw private output\n"

        def observe(argv, **_kwargs):
            payload = logs if argv[0] == "/usr/bin/journalctl" else raw
            return subprocess.CompletedProcess(argv, 0, payload, b"")

        with patch.object(stand.subprocess, "run", side_effect=observe) as command:
            result = backend.diagnostic()
        self.assertNotIn("private-placeholder", str(result))
        self.assertNotIn("raw private output", str(result))
        self.assertEqual(result["guard_errors"], ["Refusal: cold_fixture_reference_digest"])
        self.assertIn("ExecCondition", result["missing_properties"])
        self.assertIn("--all", command.call_args_list[0].args[0])
        self.assertIn("--unit=" + backend.unit, command.call_args_list[1].args[0])

    def test_observer_failure_preserves_the_original_refusal_and_only_counts_safe_codes(self):
        backend = object.__new__(stand.HostedFixtureBackend)
        backend.actual = SimpleNamespace(
            observe=Mock(side_effect=io.Refusal("slot_native_process"))
        )
        backend.observation_errors = {}
        with self.assertRaisesRegex(io.Refusal, "slot_native_process"):
            backend.observe({}, {})
        self.assertEqual(backend.observation_errors, {"slot_native_process": 1})
        backend.actual.observe.side_effect = OSError("private-placeholder")
        with self.assertRaisesRegex(OSError, "private-placeholder"):
            backend.observe({}, {})
        self.assertEqual(backend.observation_errors, {"slot_native_process": 1, "OSError": 1})


class ColdSystemdEvidenceGateTests(unittest.TestCase):
    def workflow_gate(self):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/release-rehearsal.yml"
        block = workflow.read_text().rsplit("python3 - <<'PY'", 1)[1]
        return compile(textwrap.dedent(block.split("\n          PY", 1)[0]), str(workflow), "exec")

    def evidence(self, root):
        source = "1" * 40
        base = root / "downloaded-rehearsals"
        for scenario in ("linux-profile", "windows-profile", "linux-systemd"):
            path = base / scenario / "rehearsal-complete.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "scenario": scenario,
                        "source_sha": source,
                        "complete": True,
                        "scope": "disposable-rehearsal-only",
                    }
                )
            )
        report = {
            "schema": "autostopcrm.hosted-cold-unit-rehearsal.v1",
            "source_sha": source,
            "ok": True,
            "cleanup_complete": True,
            "production_acceptance": False,
            "cases": [
                {
                    "name": name,
                    "passed": True,
                    "cleanup_complete": True,
                    "systemd_launches": 1,
                    "exec_condition": "",
                    "exact_guard_exec_start": True,
                    "mainpid_observed_within_seconds": 0.1,
                    "a_published_within_seconds": 0.2,
                    "guard_exit_status": None if name == "one_entry" else 2,
                    "journal": {
                        "S": 1,
                        "A": 1,
                        "C": int(name == "one_entry"),
                        "E": int(name == "one_entry"),
                    },
                }
                for name in stand.CASES
            ],
        }
        path = base / "linux-systemd/cold-systemd/report.json"
        path.parent.mkdir(parents=True)
        return path, report, source

    def test_required_exact_source_gate_accepts_complete_private_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, report, source = self.evidence(Path(temporary))
            path.write_text(json.dumps(report))
            with (
                contextlib.chdir(temporary),
                patch.dict(os.environ, {"GITHUB_SHA": source}),
                contextlib.redirect_stdout(streams.StringIO()),
            ):
                exec(self.workflow_gate(), {})

    def test_missing_wrong_source_skipped_or_unsafe_cold_cases_cannot_green_the_job(self):
        changes = (
            ("source_sha", "2" * 40),
            ("ok", False),
            ("cleanup_complete", False),
            ("production_acceptance", True),
            ("cases", []),
            ("case.passed", False),
            ("case.systemd_launches", 2),
            ("case.exec_condition", "/foreign/check"),
            ("case.exact_guard_exec_start", False),
            ("case.mainpid_observed_within_seconds", 10),
            ("case.a_published_within_seconds", 10),
            ("case.guard_exit_status", 0),
            ("case.journal", {"S": 1, "A": 1, "C": 1, "E": 1}),
        )
        for key, value in changes:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                path, report, source = self.evidence(Path(temporary))
                if key.startswith("case."):
                    report["cases"][1][key.removeprefix("case.")] = value
                else:
                    report[key] = value
                path.write_text(json.dumps(report))
                with (
                    contextlib.chdir(temporary),
                    patch.dict(os.environ, {"GITHUB_SHA": source}),
                    self.assertRaises(AssertionError),
                ):
                    exec(self.workflow_gate(), {})
        with tempfile.TemporaryDirectory() as temporary:
            _path, _report, source = self.evidence(Path(temporary))
            with (
                contextlib.chdir(temporary),
                patch.dict(os.environ, {"GITHUB_SHA": source}),
                self.assertRaises(AssertionError),
            ):
                exec(self.workflow_gate(), {})
