from __future__ import annotations

import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io


def command(argv, *, ignore="no"):
    return (
        "{ path="
        + argv.split(" ", 1)[0]
        + " ; argv[]="
        + argv
        + " ; ignore_errors="
        + ignore
        + " ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0 }"
    )


class ColdLoadedUnitPolicy(unittest.TestCase):
    def test_package_and_kernel_slots_preserve_attempt_generation_identity(self):
        attempt, generation = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(
            native.package_path(attempt), native.CONFIG / "packages" / attempt / "authority.json"
        )
        self.assertEqual(
            native.kernel_unit(generation), "autostop-j1-cold-kernel-" + generation + ".service"
        )
        for identity in ("../previous", "", "A" * 36, "00000000-0000-0000-0000-000000000000-extra"):
            with self.assertRaises(io.Refusal):
                native.package_path(identity)
            with self.assertRaises(io.Refusal):
                native.kernel_unit(identity)

    def setUp(self):
        self.ticket = {"unit": "autostop-crm-forward-" + str(uuid.uuid4()) + ".service"}
        self.context = {
            "worker_argv": [
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "/fixed/guard.py",
                "launch",
                "--unit",
                "autostop-j1.service",
            ],
            "bootstrap_environment": lifecycle.BOOTSTRAP_ENVIRONMENT,
        }
        argv = " ".join(self.context["worker_argv"])
        self.fields = dict.fromkeys((*native.UNIT_PROPERTIES, "Restart", "FragmentPath", "Job"), "")
        self.fields.update(
            Id=self.ticket["unit"],
            Type="exec",
            LoadState="loaded",
            NeedDaemonReload="no",
            ActiveState="inactive",
            MainPID="0",
            User="root",
            Group="root",
            WorkingDirectory="/",
            Restart="no",
            FragmentPath="/etc/systemd/system/" + self.ticket["unit"],
            Job="0",
            Environment=" ".join(
                key + "=" + value for key, value in lifecycle.BOOTSTRAP_ENVIRONMENT.items()
            ),
            ExecStart=command(argv),
            ExecCondition="",
        )
        self.backend = lifecycle.NativeBackend(native.NativeProvider())

    def check(self):
        def show(argv, **_kwargs):
            # systemctl show suppresses empty hooks unless --all is requested.
            raw = "".join(
                key + "=" + value + "\n"
                for key, value in self.fields.items()
                if value or "--all" in argv
            ).encode()
            return subprocess.CompletedProcess([], 0, raw, b"")

        with patch.object(lifecycle.subprocess, "run", side_effect=show) as execute:
            self.backend.closed(self.ticket, self.context)
        self.assertEqual(execute.call_args.args[0][:2], ["/usr/bin/systemctl", "show"])
        self.assertIn("--all", execute.call_args.args[0])

    def test_exact_no_restart_root_bootstrap_has_no_application_environment(self):
        self.check()

    def test_missing_empty_hook_field_is_not_assumed_to_be_absent(self):
        del self.fields["ExecCondition"]
        with self.assertRaises(io.Refusal):
            self.check()

    def test_staged_private_unit_has_one_guard_entry_and_no_pre_entry_condition(self):
        with tempfile.TemporaryDirectory() as temporary:
            units = Path(temporary)
            context = {
                **self.context,
                "interpreter": str(Path("/usr/bin/python3").resolve()),
            }

            def path(value):
                return units if str(value) == "/etc/systemd/system" else Path(value)

            with (
                patch.object(lifecycle, "Path", side_effect=path),
                patch.object(io, "protected_directory", return_value=units),
                patch.object(lifecycle.subprocess, "run") as execute,
            ):
                self.backend.stage("autostop-j1.service", context, self.ticket, units)
            raw = (units / self.ticket["unit"]).read_text()
            self.assertNotIn("ExecCondition=", raw)
            self.assertEqual(raw.count("ExecStart="), 1)
            self.assertIn("\nGroup=root\nSupplementaryGroups=\n", raw)
            self.assertIn("ExecStart=" + " ".join(context["worker_argv"]), raw)
            self.assertEqual(execute.call_args.args[0], ["/usr/bin/systemctl", "daemon-reload"])

    def test_pending_jobs_restart_and_command_prefixes_are_refused(self):
        original = dict(self.fields)
        for key, value in (
            ("Restart", "always"),
            ("FragmentPath", "/run/systemd/transient/unknown.service"),
            ("Job", "123 start"),
            ("Environment", "AUTOSTOP_COLD_RUNTIME_BINDING=/untrusted"),
            (
                "ExecCondition",
                command("/usr/bin/touch /tmp/unwanted") + " " + original["ExecCondition"],
            ),
            ("ExecStart", command(" ".join(self.context["worker_argv"]), ignore="yes")),
            ("ExecStart", command("/usr/bin/touch /tmp/foreign-application")),
            ("ExecCondition", command(" ".join(self.context["worker_argv"]))),
        ):
            with self.subTest(key=key):
                self.fields = {**original, key: value}
                with self.assertRaises(io.Refusal):
                    self.check()


class ColdNativePropertyInventory(unittest.TestCase):
    def test_original_and_kernel_observers_explicitly_request_empty_properties(self):
        for unit in ("autostop-j1.service", native.kernel_unit(str(uuid.uuid4()))):
            with self.subTest(unit=unit):
                expected = dict.fromkeys(native.UNIT_PROPERTIES, "")
                expected.update(Id=unit, LoadState="loaded", NeedDaemonReload="no")

                def show(argv, **_kwargs):
                    raw = "".join(
                        key + "=" + value + "\n"
                        for key, value in expected.items()
                        if value or "--all" in argv
                    ).encode()
                    return subprocess.CompletedProcess([], 0, raw, b"")

                with patch.object(native.subprocess, "run", side_effect=show) as execute:
                    self.assertEqual(native.NativeProvider().systemd(unit), expected)
                self.assertIn("--all", execute.call_args.args[0])

    def test_still_rejects_an_omitted_or_unexpected_property(self):
        unit = "autostop-j1.service"
        expected = dict.fromkeys(native.UNIT_PROPERTIES, "")
        expected.update(Id=unit, LoadState="loaded", NeedDaemonReload="no")
        omitted = dict(expected)
        del omitted["ExecCondition"]
        for actual in (omitted, {**expected, "UnknownProperty": ""}):
            with self.subTest(keys=set(actual)):
                raw = "".join(key + "=" + value + "\n" for key, value in actual.items()).encode()
                with patch.object(
                    native.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, raw, b""),
                ):
                    with self.assertRaises(io.Refusal):
                        native.NativeProvider().systemd(unit)


class ColdTypedPropertyInventory(unittest.TestCase):
    """Typed JSON shape follows systemd v255 busctl json_transform_message/variant."""

    def setUp(self):
        self.unit = "autostop-crm-forward-" + str(uuid.uuid4()) + ".service"
        self.properties = (*native.UNIT_PROPERTIES, "FragmentPath", "Job")
        self.fields = dict.fromkeys(self.properties, "")
        self.fields.update(
            Id=self.unit,
            Type="exec",
            LoadState="loaded",
            NeedDaemonReload="no",
            ActiveState="inactive",
            SubState="dead",
            MainPID="0",
            Job="",
        )
        self.omitted = set(native._EMPTY_ARRAY_TYPES)
        self.service = {
            key: {"type": signature, "data": []}
            for key, signature in native._EMPTY_ARRAY_TYPES.items()
        }
        self.service.update(
            Type={"type": "s", "data": "exec"},
            MainPID={"type": "u", "data": 0},
            ControlGroup={"type": "s", "data": ""},
        )
        self.identity = {
            key: {"type": "s", "data": self.fields[key]}
            for key in ("Id", "LoadState", "ActiveState", "SubState")
        }
        self.identity.update(
            NeedDaemonReload={"type": "b", "data": False}, Job={"type": "(uo)", "data": [0, "/"]}
        )
        self.calls = []
        self.final = None
        self.raw_reply = None
        self.stderr = b""

    def execute(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] == "/usr/bin/systemctl":
            fields = self.final if len(self.calls) > 1 and self.final is not None else self.fields
            raw = "".join(
                key + "=" + value + "\n" for key, value in fields.items() if key not in self.omitted
            ).encode()
        else:
            self.assertEqual(argv[0], "/usr/bin/busctl")
            data = self.service if argv[-1].endswith(".Service") else self.identity
            raw = self.raw_reply or io.canonical({"type": "a{sv}", "data": [data]})
        return subprocess.CompletedProcess(argv, 0, raw, self.stderr)

    def check(self):
        with patch.object(native.subprocess, "run", side_effect=self.execute):
            return native.systemd_properties(self.unit, self.properties)

    def test_empty_arrays_and_no_job_are_proven_by_two_typed_getall_calls(self):
        self.assertEqual(self.check(), {**self.fields, "Job": "0"})
        self.assertEqual(len(self.calls), 4)
        bus_calls = [argv for argv, _ in self.calls if argv[0] == "/usr/bin/busctl"]
        self.assertEqual(
            [argv[-1] for argv in bus_calls],
            [
                "org.freedesktop.systemd1.Service",
                "org.freedesktop.systemd1.Unit",
            ],
        )
        for argv in bus_calls:
            self.assertIn("--auto-start=no", argv)
            self.assertIn("--allow-interactive-authorization=no", argv)
            self.assertEqual(argv[-4:-1], ["org.freedesktop.DBus.Properties", "GetAll", "s"])
            self.assertEqual(
                argv[-5],
                "/org/freedesktop/systemd1/unit/"
                + self.unit.replace("-", "_2d").replace(".", "_2e"),
            )

    def test_backend_accepts_real_omission_shape_without_ignoring_foreign_command(self):
        policy = ColdLoadedUnitPolicy()
        policy.setUp()
        self.unit = policy.ticket["unit"]
        self.fields = {**policy.fields, "Job": ""}
        for key in ("Id", "LoadState", "ActiveState", "SubState"):
            self.identity[key]["data"] = self.fields[key]
        with patch.object(native.subprocess, "run", side_effect=self.execute):
            policy.backend.closed(policy.ticket, policy.context)
        self.fields["ExecStart"] = command("/bin/false")
        with patch.object(native.subprocess, "run", side_effect=self.execute):
            with self.assertRaisesRegex(io.Refusal, "loaded_unit_changed"):
                policy.backend.closed(policy.ticket, policy.context)

    def test_original_and_kernel_provider_uses_same_typed_inventory(self):
        for unit in ("autostop-j1.service", native.kernel_unit(str(uuid.uuid4()))):
            with self.subTest(unit=unit):
                self.unit = unit
                self.fields["Id"] = unit
                self.identity["Id"]["data"] = unit
                del self.fields["Job"]
                self.fields.pop("FragmentPath", None)
                with patch.object(native.subprocess, "run", side_effect=self.execute):
                    self.assertEqual(native.NativeProvider().systemd(unit), self.fields)
                self.calls.clear()
                self.fields["Job"] = ""

    def test_present_empty_fields_need_no_fallback(self):
        self.omitted.clear()
        self.fields["Job"] = "0"
        self.assertEqual(self.check(), self.fields)
        self.assertEqual(len(self.calls), 1)

    def test_missing_nonarray_or_unknown_inventory_is_not_completed(self):
        for change in (lambda: self.omitted.add("User"), lambda: self.fields.update(Unknown="")):
            with self.subTest(change=change):
                self.setUp()
                change()
                with self.assertRaises(io.Refusal):
                    self.check()
                self.assertEqual(len(self.calls), 1)

    def test_nonempty_wrong_typed_or_absent_array_is_not_empty(self):
        for value in (
            {"type": "a(sasbttttuii)", "data": [["/bin/true"]]},
            {"type": "as", "data": []},
            {"type": "a(sasbttttuii)", "data": None},
            {"type": "a(sasbttttuii)", "data": [], "ignored": True},
            None,
        ):
            with self.subTest(value=value):
                self.setUp()
                self.service["ExecCondition"] = value
                with self.assertRaises(io.Refusal):
                    self.check()

    def test_job_missing_nonzero_boolean_or_wrong_object_is_not_no_job(self):
        for value in (
            {"type": "(uo)", "data": [123, "/org/freedesktop/systemd1/job/123"]},
            {"type": "(uo)", "data": [False, "/"]},
            {"type": "(uo)", "data": [0, "/different"]},
            {"type": "s", "data": ""},
            None,
        ):
            with self.subTest(value=value):
                self.setUp()
                self.identity["Job"] = value
                with self.assertRaises(io.Refusal):
                    self.check()

    def test_identity_or_process_change_in_typed_snapshot_is_denied(self):
        for group, key, value in (
            ("identity", "Id", {"type": "s", "data": "foreign.service"}),
            ("identity", "LoadState", {"type": "s", "data": "not-found"}),
            ("identity", "NeedDaemonReload", {"type": "b", "data": True}),
            ("identity", "ActiveState", {"type": "s", "data": "active"}),
            ("service", "MainPID", {"type": "u", "data": 4}),
            ("service", "MainPID", {"type": "u", "data": False}),
            ("service", "ControlGroup", {"type": "s", "data": "/foreign"}),
        ):
            with self.subTest(group=group, key=key):
                self.setUp()
                getattr(self, group)[key] = value
                with self.assertRaises(io.Refusal):
                    self.check()

    def test_show_replacement_after_typed_proof_is_denied(self):
        self.final = {**self.fields, "MainPID": "123"}
        with self.assertRaisesRegex(io.Refusal, "observation_changed"):
            self.check()

    def test_malformed_duplicated_or_untyped_getall_is_denied(self):
        for raw in (
            b'{"type":"a{sv}","data":[{}],"data":[{}]}',
            b'{"type":"a{sv}","data":{}}',
            b'{"type":"a{sv}","data":[{},{}]}',
            b'{"type":"s","data":[{}]}',
            b"not json",
        ):
            with self.subTest(raw=raw):
                self.setUp()
                self.raw_reply = raw
                with self.assertRaises(io.Refusal):
                    self.check()

    def test_one_total_five_second_budget_is_not_restarted_for_getall(self):
        times = iter((100, 100, 101, 101, 102, 102, 103, 103, 104))
        with patch.object(native.time, "monotonic", side_effect=lambda: next(times)):
            self.check()
        self.assertEqual([kwargs["timeout"] for _, kwargs in self.calls], [5, 4, 3, 2])
        self.setUp()
        with patch.object(native.time, "monotonic", side_effect=[100, 100, 106]):
            with self.assertRaisesRegex(io.Refusal, "unit_observation"):
                self.check()
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
