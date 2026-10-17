from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from scripts import j1_release_boundary as boundary


class EmptyExecFieldsTests(unittest.TestCase):
    unit = "autostop-j1.service"

    def result(self, stdout="a(sasbttttuii) 0\n", *, returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_omitted_vectors_require_exact_same_unit_typed_empty_readback(self):
        # Calibrated against PID1: even systemctl show --all omits empty vectors.
        with patch.object(boundary.subprocess, "run", return_value=self.result()) as command:
            self.assertTrue(
                boundary.empty_exec_fields(self.unit, {"Id": self.unit}, boundary.EMPTY_EXEC_FIELDS)
            )
        self.assertEqual(len(boundary.EMPTY_EXEC_FIELDS), command.call_count)
        for field, call in zip(boundary.EMPTY_EXEC_FIELDS, command.call_args_list, strict=True):
            self.assertEqual(
                call.args[0],
                [
                    "busctl",
                    "--system",
                    "get-property",
                    "org.freedesktop.systemd1",
                    "/org/freedesktop/systemd1/unit/autostop_2dj1_2eservice",
                    "org.freedesktop.systemd1.Service",
                    field,
                ],
            )
            self.assertEqual(
                call.kwargs, {"check": True, "capture_output": True, "text": True, "timeout": 10}
            )

    def test_explicit_empty_property_remains_valid_without_fallback(self):
        with patch.object(boundary.subprocess, "run") as command:
            self.assertTrue(
                boundary.empty_exec_fields(
                    self.unit, {"Id": self.unit, "ExecStop": ""}, ("ExecStop",)
                )
            )
        command.assert_not_called()

    def test_nonempty_show_hook_never_falls_back_to_empty(self):
        with patch.object(boundary.subprocess, "run") as command:
            self.assertFalse(
                boundary.empty_exec_fields(
                    self.unit, {"Id": self.unit, "ExecStop": "writer hook"}, ("ExecStop",)
                )
            )
        command.assert_not_called()

    def test_wrong_unit_identity_field_or_property_is_rejected_before_getter(self):
        with patch.object(boundary.subprocess, "run") as command:
            for unit, fields, names in (
                ("docker.service", {"Id": "docker.service"}, ("ExecStop",)),
                (self.unit, {}, ("ExecStop",)),
                (self.unit, {"Id": "autostop-telegram.service"}, ("ExecStop",)),
                (self.unit, {"Id": self.unit}, ("Environment",)),
            ):
                with (
                    self.subTest(unit=unit, fields=fields, names=names),
                    self.assertRaises(boundary.BoundaryError),
                ):
                    boundary.empty_exec_fields(unit, fields, names)
        command.assert_not_called()

    def test_nonzero_nonempty_wrong_type_partial_or_stderr_are_not_empty(self):
        for result in (
            self.result(returncode=1),
            self.result("a(sasbttttuii) 1\n"),
            self.result('s ""\n'),
            self.result("a(sasbttttuii) 0"),
            self.result(""),
            self.result(stderr="getter warning"),
        ):
            with (
                self.subTest(result=result),
                patch.object(boundary.subprocess, "run", return_value=result),
            ):
                with self.assertRaisesRegex(boundary.BoundaryError, "typed_empty_exec_required"):
                    boundary.empty_exec_fields(self.unit, {"Id": self.unit}, ("ExecStop",))

    def test_timeout_unsupported_or_command_failure_propagates(self):
        for error in (
            subprocess.TimeoutExpired("busctl", 10),
            FileNotFoundError("busctl"),
            subprocess.CalledProcessError(1, "busctl"),
        ):
            with (
                self.subTest(error=error),
                patch.object(boundary.subprocess, "run", side_effect=error),
            ):
                with self.assertRaises(type(error)):
                    boundary.empty_exec_fields(self.unit, {"Id": self.unit}, ("ExecStop",))

    def test_fixed_recovery_unit_uses_its_own_object(self):
        unit = boundary.KERNEL_RECOVERY_UNIT
        with patch.object(boundary.subprocess, "run", return_value=self.result()) as command:
            self.assertTrue(boundary.empty_exec_fields(unit, {"Id": unit}, ("ExecCondition",)))
        self.assertEqual(
            command.call_args.args[0][4],
            "/org/freedesktop/systemd1/unit/autostop_2dj1_2dnative_2drecover_2eservice",
        )

    def test_loaded_guards_verify_omitted_hooks_without_changing_dispatch(self):
        policy_hash, intent_hash = "a" * 64, "b" * 64
        prefix = f"/usr/bin/python3 -I -S {boundary.GUARD_ROOT / 'guard.py'}"

        def read(arguments, **_):
            if arguments[0] == "busctl":
                return self.result()
            unit = arguments[2]
            fields = {
                "Id": unit,
                "NeedDaemonReload": "no",
                "ExecCondition": f"{{ argv[]={prefix} check --policy-sha256 {policy_hash} --unit {unit} ; }}",
                "ExecStart": f"{{ argv[]={prefix} launch --policy-sha256 {policy_hash} --unit {unit} ; }}",
            }
            if unit == "autostop-work-telegram.service":
                fields["ExecStartPost"] = "{ argv[]=" + boundary.duty_command(intent_hash) + " ; }"
            return self.result("\n".join(key + "=" + value for key, value in fields.items()))

        with patch.object(boundary.subprocess, "run", side_effect=read) as command:
            boundary.verify_loaded_guards(policy_hash, intent_hash)
        typed = [call for call in command.call_args_list if call.args[0][0] == "busctl"]
        self.assertEqual(len(boundary.ALL_UNITS) * 5 - 1, len(typed))

    def test_loaded_recovery_verifies_all_six_omitted_hooks(self):
        intent_hash = "b" * 64
        expected = boundary.duty_command(intent_hash).replace(" restore ", " recover ")

        def read(arguments, **_):
            if arguments[0] == "busctl":
                return self.result()
            if arguments[2] == "autostop-codex-start.service":
                return self.result("Wants=" + boundary.KERNEL_RECOVERY_UNIT)
            fields = {
                "Id": boundary.KERNEL_RECOVERY_UNIT,
                "Type": "oneshot",
                "User": "root",
                "Group": "root",
                "NeedDaemonReload": "no",
                "FragmentPath": str(boundary.SYSTEMD_ROOT / boundary.KERNEL_RECOVERY_UNIT),
                "DropInPaths": "",
                "After": "autostop-codex-start.service autostop-j1-cache-view.service",
                "BindsTo": "autostop-codex-start.service",
                "ExecStart": "{ argv[]=" + expected + " ; }",
            }
            return self.result("\n".join(key + "=" + value for key, value in fields.items()))

        with patch.object(boundary.subprocess, "run", side_effect=read) as command:
            boundary.verify_loaded_recovery(intent_hash)
        self.assertEqual(6, sum(call.args[0][0] == "busctl" for call in command.call_args_list))


if __name__ == "__main__":
    unittest.main()
