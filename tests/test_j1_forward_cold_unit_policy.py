from __future__ import annotations

import subprocess
import unittest
import uuid
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
            ExecCondition=command(argv.replace(" launch ", " check ", 1)),
        )
        self.backend = lifecycle.NativeBackend(native.NativeProvider())

    def check(self):
        raw = "".join(key + "=" + value + "\n" for key, value in self.fields.items()).encode()
        with patch.object(
            lifecycle.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, raw, b"")
        ) as execute:
            self.backend.closed(self.ticket, self.context)
        self.assertEqual(execute.call_args.args[0][:2], ["/usr/bin/systemctl", "show"])

    def test_exact_no_restart_root_bootstrap_has_no_application_environment(self):
        self.check()

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
        ):
            with self.subTest(key=key):
                self.fields = {**original, key: value}
                with self.assertRaises(io.Refusal):
                    self.check()


if __name__ == "__main__":
    unittest.main()
