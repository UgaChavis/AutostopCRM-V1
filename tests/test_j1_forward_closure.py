"""Synthetic effect tests: never signal a process or control real services."""

from __future__ import annotations

import copy
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from scripts import j1_forward_closure as closure
from scripts.j1_forward_io import Refusal

ATTEMPT = "7767b188-209a-49a3-8aac-854cf2d4336f"


def actor(pid):
    return {
        "pid": pid,
        "start_ticks": pid + 10000,
        "exe": "/root/codex",
        "exe_sha256": "a" * 64,
        "exe_identity": [1, pid],
        "argv_sha256": "b" * 64,
        "cgroup": "0::/user.slice/accepted-warm",
        "uid": 0,
        "mount_namespace": "mnt:[12345]",
    }


class FakeRunner:
    def __init__(self):
        self.events = []
        self.saved = {}
        self.barriers = {}
        self.actors = {pid: {**actor(pid), "stopped": False} for pid in closure.ACCEPTED_PIDS}
        self.procs = {
            pid: {
                "start_ticks": row["start_ticks"],
                "ppid": 1,
                "state": "S",
                "cgroup": row["cgroup"],
                "suspect": True,
            }
            for pid, row in self.actors.items()
        }
        self.procs[os.getpid()] = {
            "start_ticks": 999,
            "ppid": 1,
            "state": "R",
            "cgroup": "0::/test",
            "suspect": True,
        }
        self.stopped = False
        self.fail_put = None
        self.fail_save = False
        self.fail_thaw = None
        self.extra_after_stop = False
        self.on_signal = None
        self.bad_loaded_hook = False
        self.scan_error = False
        self.boot = "boot-1"

    def boot_id(self):
        return self.boot

    def save(self, directory, value):
        self.events.append("save")
        if self.fail_save:
            raise OSError("durability failed")
        if str(directory) in self.saved:
            raise FileExistsError()
        self.saved[str(directory)] = copy.deepcopy(value)

    def load(self, directory):
        try:
            return copy.deepcopy(self.saved[str(directory)])
        except KeyError:
            raise FileNotFoundError() from None

    def read_barrier(self, path):
        return self.barriers.get(str(path))

    def put_barrier(self, path):
        self.events.append(("put", str(path)))
        if str(path) == self.fail_put:
            self.fail_put = None
            raise OSError("write failed")
        if str(path) in self.barriers:
            raise FileExistsError()
        self.barriers[str(path)] = closure.DENY_BYTES

    def remove_barrier(self, path):
        self.events.append(("remove", str(path)))
        if self.barriers[str(path)] != closure.DENY_BYTES:
            raise Refusal("barrier_not_owned")
        del self.barriers[str(path)]

    def reload(self, deadline):
        self.events.append("reload")

    def stop(self, deadline):
        self.events.append("stop")
        self.stopped = True
        if self.extra_after_stop:
            self.procs[888] = {
                "start_ticks": 8888,
                "ppid": 1,
                "state": "R",
                "cgroup": "0::/unrelated",
                "suspect": True,
            }

    def actor(self, expected, *, full=False):
        self.events.append(("actor", expected["pid"], full))
        return copy.deepcopy(self.actors[expected["pid"]])

    def signal_actor(self, expected, stop):
        closure._same_actor(expected, self.actors[expected["pid"]], full=True)
        if not stop and expected["pid"] == self.fail_thaw:
            self.fail_thaw = None
            raise OSError("thaw failed")
        self.events.append(("signal", expected["pid"], stop))
        self.actors[expected["pid"]]["stopped"] = stop
        self.procs[expected["pid"]]["state"] = "T" if stop else "S"
        if self.on_signal:
            self.on_signal(expected, stop)

    def processes(self):
        return copy.deepcopy(self.procs)

    def control_processes(self, owned, processes):
        return set()

    def source_roots(self):
        return [
            {"path": name, "present": True, "dev": 1, "ino": index}
            for index, name in enumerate(closure.WRITER_ROOTS)
        ]

    def units(self, deadline):
        self.events.append("units")
        result = {}
        for unit in closure.UNITS:
            path = str(closure.SYSTEMD / (unit + ".d") / closure.DROPIN)
            result[unit] = {
                "LoadState": "loaded",
                "NeedDaemonReload": "no",
                "ExecCondition": "path=/existing/guard ; path=/usr/bin/false"
                if path in self.barriers
                else "path=/existing/guard",
                "ActiveState": "inactive" if self.stopped else "active",
                "MainPID": "0" if self.stopped else "99",
                "Job": "",
                **{hook: "" for hook in closure.HOOKS},
            }
        if self.bad_loaded_hook:
            result[closure.UNITS[0]]["ExecStop"] = "path=/dangerous-hook"
        return result

    def containers(self, deadline):
        self.events.append("containers")
        return []

    def cache_views(self, deadline):
        self.events.append("cache_views")

    def descriptor_scan(self, processes, frozen, own):
        self.events.append("fds")
        if self.scan_error:
            raise Refusal("unexpected_source_write_descriptor")
        return {"frozen_owned_write_fds": 2, "owned_sqlite_coordination_fds": 0}


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.mock_runner = patch.object(closure, "RUNNER", self.runner)
        self.mock_runner.start()
        self.addCleanup(self.mock_runner.stop)
        closure._CACHE.clear()
        self.directory = Path.cwd() / ATTEMPT
        self.baseline = {
            "attempt_id": "old-authority-attempt",
            "warm_actors": [actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)],
        }

    def close(self):
        return closure.close_writers(self.baseline, self.directory, deadline=time.monotonic() + 30)

    def owned(self):
        return self.runner.saved[str(self.directory)]

    def test_prepare_saves_ownership_without_runtime_effects(self):
        result = closure.prepare_closure(self.baseline, self.directory)
        self.assertEqual(result, self.owned())
        self.assertEqual(result["attempt_id"], ATTEMPT)
        self.assertEqual(self.runner.barriers, {})
        self.assertFalse(self.runner.stopped)
        self.assertFalse(any(row["stopped"] for row in self.runner.actors.values()))
        self.assertEqual(
            [event for event in self.runner.events if not isinstance(event, tuple)], ["save"]
        )
        self.assertTrue(
            all(event[0] == "actor" for event in self.runner.events if isinstance(event, tuple))
        )

    def test_prepared_ownership_fences_early_drain_failure(self):
        owned = closure.prepare_closure(self.baseline, self.directory)
        result = closure.fail_closed(owned)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["observation"])
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(self.runner.stopped)
        self.assertTrue(all(row["stopped"] for row in self.runner.actors.values()))

    def test_close_reuses_prepared_record_without_republication(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        closed = self.close()
        self.assertEqual(prepared, closed)
        self.assertEqual(self.runner.events.count("save"), 1)
        self.assertEqual(len(self.runner.barriers), 8)

    def test_prepare_never_overwrites_existing_ownership(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        with self.assertRaises(FileExistsError):
            closure.prepare_closure(self.baseline, self.directory)
        self.assertEqual(self.owned(), prepared)
        self.assertEqual(self.runner.barriers, {})

    def test_stale_or_foreign_prepared_record_is_refused_before_effects(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        mutations = {
            "boot_id": "different-boot",
            "attempt_id": "b921389d-490c-4608-852b-b5b9918798e0",
            "attempt_dir": str(self.directory.parent / "other"),
            "baseline_sha256": "c" * 64,
            "source_roots": [],
            "warm_actors": [actor(pid + 1) for pid in sorted(closure.ACCEPTED_PIDS)],
        }
        for key, value in mutations.items():
            with self.subTest(field=key):
                self.runner.saved[str(self.directory)] = {**copy.deepcopy(prepared), key: value}
                with self.assertRaisesRegex(Refusal, "prepared_closure_changed"):
                    self.close()
                self.assertEqual(self.runner.barriers, {})
                self.assertFalse(self.runner.stopped)

    def test_prepared_actor_reuse_is_rechecked_before_installation(self):
        closure.prepare_closure(self.baseline, self.directory)
        self.runner.actors[538314]["start_ticks"] += 1
        with self.assertRaisesRegex(Refusal, "warm_actor_changed"):
            self.close()
        self.assertEqual(self.runner.barriers, {})
        self.assertFalse(self.runner.stopped)

    def test_barrier_appearing_after_prepare_is_not_adopted(self):
        closure.prepare_closure(self.baseline, self.directory)
        path = closure._paths()[0]["path"]
        self.runner.barriers[path] = b"foreign-owner"
        with self.assertRaisesRegex(Refusal, "barrier_already_exists"):
            self.close()
        self.assertEqual(self.runner.barriers, {path: b"foreign-owner"})
        self.assertFalse(self.runner.stopped)

    def test_close_persists_before_fixed_effects_and_preserves_warm_identity(self):
        result = self.close()
        self.assertEqual(result["attempt_id"], ATTEMPT)
        self.assertEqual(result["warm_actors"], self.baseline["warm_actors"])
        effect = [
            event
            for event in self.runner.events
            if event == "save"
            or event == "stop"
            or isinstance(event, tuple)
            and event[0] in {"put", "signal"}
        ]
        self.assertEqual(effect[0], "save")
        self.assertEqual(sum(item[0] == "put" for item in effect if isinstance(item, tuple)), 8)
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(item["stopped"] for item in self.runner.actors.values()))
        self.assertNotIn("start", self.runner.events)

    def test_failed_ownership_durability_produces_no_runtime_effect(self):
        self.runner.fail_save = True
        with self.assertRaises(OSError):
            self.close()
        self.assertEqual(self.runner.barriers, {})
        self.assertNotIn("stop", self.runner.events)

    def test_preexisting_equal_barrier_is_not_claimed(self):
        self.runner.barriers[closure._paths()[0]["path"]] = closure.DENY_BYTES
        with self.assertRaisesRegex(Refusal, "barrier_already_exists"):
            self.close()
        self.assertEqual(self.runner.saved, {})

    def test_partial_install_failure_restores_all_owned_barriers_without_thaw(self):
        self.runner.fail_put = closure._paths()[3]["path"]
        with self.assertRaises(OSError):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(self.runner.stopped)
        self.assertTrue(all(row["stopped"] for row in self.runner.actors.values()))
        self.assertFalse(
            any(
                e[0] == "signal" and e[2] is False
                for e in self.runner.events
                if isinstance(e, tuple)
            )
        )

    def test_reused_pid_refused_before_ownership(self):
        self.runner.actors[538314]["start_ticks"] += 1
        with self.assertRaisesRegex(Refusal, "warm_actor_changed"):
            self.close()
        self.assertEqual(self.runner.saved, {})

    def test_unknown_warm_descendant_is_never_signaled(self):
        self.runner.procs[777] = {
            "start_ticks": 100,
            "ppid": 538285,
            "state": "R",
            "cgroup": "0::/accepted",
            "suspect": True,
        }
        with self.assertRaisesRegex(Refusal, "unknown_warm_descendant"):
            self.close()
        self.assertEqual(self.runner.saved, {})
        self.assertFalse(any(e[0] == "signal" for e in self.runner.events if isinstance(e, tuple)))

    def test_unknown_writer_after_unit_stop_cannot_be_reported_closed(self):
        self.runner.extra_after_stop = True
        with self.assertRaisesRegex(Refusal, "unexpected_runtime_actor"):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertFalse(
            any(
                e[0] == "signal" and e[1] == 888 for e in self.runner.events if isinstance(e, tuple)
            )
        )

    def test_descendant_fork_during_freeze_remains_unowned_and_refuses(self):
        def fork(expected, stop):
            if stop:
                self.runner.procs[777] = {
                    "start_ticks": 100,
                    "ppid": 538285,
                    "state": "R",
                    "cgroup": "0::/accepted",
                    "suspect": True,
                }

        self.runner.on_signal = fork
        with self.assertRaisesRegex(Refusal, "unknown_warm_descendant"):
            self.close()
        self.assertFalse(
            any(
                e[0] == "signal" and e[1] == 777 for e in self.runner.events if isinstance(e, tuple)
            )
        )

    def test_loaded_mutating_stop_hook_prevents_stop_command(self):
        self.runner.bad_loaded_hook = True
        with self.assertRaisesRegex(Refusal, "loaded_barrier_invalid"):
            self.close()
        self.assertNotIn("stop", self.runner.events)

    def test_fd_writer_failure_retains_fence(self):
        self.runner.scan_error = True
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(row["stopped"] for row in self.runner.actors.values()))

    def test_reopen_removes_only_owned_files_and_thaws_without_start(self):
        result = self.close()
        self.runner.barriers["/etc/unrelated.conf"] = b"unrelated"
        closure.reopen_native(result)
        self.assertEqual(self.runner.barriers, {"/etc/unrelated.conf": b"unrelated"})
        self.assertFalse(any(row["stopped"] for row in self.runner.actors.values()))
        self.assertNotIn("start", self.runner.events)

    def test_thaw_failure_reinstalls_barriers_and_refreezes_prior_thaw(self):
        result = self.close()
        self.runner.fail_thaw = 538314
        with self.assertRaises(OSError):
            closure.reopen_native(result)
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(row["stopped"] for row in self.runner.actors.values()))

    def test_foreign_barrier_bytes_never_overwritten(self):
        result = self.close()
        path = result["dropins"][0]["path"]
        self.runner.barriers[path] = b"foreign"
        response = closure.fail_closed(result)
        self.assertIn("barrier_restore_failed", response["errors"])
        self.assertEqual(self.runner.barriers[path], b"foreign")
        self.assertIsNone(response["observation"])

    def test_forged_receipt_cannot_select_file_or_pid(self):
        result = self.close()
        forged = copy.deepcopy(result)
        forged["dropins"][0]["path"] = "/etc/unrelated.conf"
        with self.assertRaisesRegex(Refusal, "closure_fixed_barriers"):
            closure.reopen_native(forged)
        forged = copy.deepcopy(result)
        forged["warm_actors"][0]["start_ticks"] += 1
        with self.assertRaisesRegex(Refusal, "closure_ownership_changed"):
            closure.fail_closed(forged)

    def test_lightweight_checks_detect_thaw_even_with_full_scan_cached(self):
        result = self.close()
        before = self.runner.events.count("fds")
        observed = closure.observe_closed(result)
        self.assertFalse(observed["full_scan_this_sample"])
        self.assertEqual(self.runner.events.count("fds"), before)
        self.runner.actors[538487]["stopped"] = False
        with self.assertRaisesRegex(Refusal, "warm_actor_not_frozen"):
            closure.observe_closed(result)

    def test_force_full_refreshes_heavy_scan_and_reports_count(self):
        result = self.close()
        observed = closure.observe_closed(result, force_full=True)
        self.assertEqual(observed["full_scan_count"], 2)
        self.assertTrue(observed["full_scan_this_sample"])
        self.assertNotIn("accepted", observed)

    def test_boot_change_blocks_old_pid_ownership(self):
        result = self.close()
        self.runner.boot = "boot-2"
        with self.assertRaisesRegex(Refusal, "closure_boot_changed"):
            closure.fail_closed(result)

    def test_concurrent_observers_share_one_scan_without_skipping_identity_checks(self):
        result = self.close()
        errors = []

        def inspect():
            try:
                closure.observe_closed(result)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=inspect) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.runner.events.count("fds"), 1)
        self.assertFalse(any(thread.is_alive() for thread in threads))


class LinuxBackendTests(unittest.TestCase):
    def units_fixture(
        self,
        *,
        missing=closure.HOOKS,
        values=None,
        trigger=None,
        trigger_raw=b"ActiveState=inactive\nJob=\n",
        typed=None,
        jobs=None,
        extra=b"",
    ):
        runner = closure.LinuxRunner()
        fields = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "SubState": "dead",
            "NeedDaemonReload": "no",
            "MainPID": "0",
            "ExecCondition": "path=/original/guard ; path=/usr/bin/false",
            **dict.fromkeys(closure.HOOKS, ""),
            "TriggeredBy": trigger or "",
            "Job": "",
        }
        fields.update(values or {})
        raw = (
            "".join(
                key + "=" + value + "\n" for key, value in fields.items() if key not in missing
            ).encode()
            + extra
        )

        def command(argv, deadline):
            if tuple(argv) == closure.JOB_QUERY:
                return (
                    jobs
                    if jobs is not None
                    else closure.io.canonical({"type": "a(usssoo)", "data": [[]]})
                )
            if argv[0] == "/usr/bin/busctl":
                if typed is not None:
                    if isinstance(typed, BaseException):
                        raise typed
                    return typed
                return b"".join(
                    closure.io.canonical({"type": "a(sasbttttuii)", "data": []}) for _ in argv[7:]
                )
            if argv[2] == trigger:
                return trigger_raw
            return raw

        runner.command = Mock(side_effect=command)
        return runner

    def test_all_eight_units_require_typed_proof_for_omitted_empty_hooks(self):
        runner = self.units_fixture()
        units = runner.units(time.monotonic() + 30)
        self.assertEqual(set(units), set(closure.UNITS))
        self.assertTrue(all(set(closure.HOOKS) <= set(fields) for fields in units.values()))
        self.assertTrue(
            all(fields["Job"] == fields["TriggeredBy"] == "" for fields in units.values())
        )
        calls = [call.args[0] for call in runner.command.call_args_list]
        typed = [
            command
            for command in calls
            if command[0] == "/usr/bin/busctl" and command[3] == "get-property"
        ]
        self.assertEqual(len(typed), 8)
        self.assertEqual(
            {command[5] for command in typed},
            {closure._unit_object_path(unit) for unit in closure.UNITS},
        )
        self.assertTrue(
            all(tuple(command[7:]) == tuple(sorted(closure.HOOKS)) for command in typed)
        )
        self.assertTrue(all("--all" in command for command in calls if command[1] == "show"))
        closure._unit_policy(units)

    def test_explicit_empty_hooks_need_no_typed_fallback(self):
        runner = self.units_fixture(missing=())
        closure._unit_policy(runner.units(time.monotonic() + 30))
        self.assertFalse(
            any(
                call.args[0][0] == "/usr/bin/busctl" and call.args[0][3] == "get-property"
                for call in runner.command.call_args_list
            )
        )

    def test_nonempty_original_hook_and_guard_are_preserved_and_not_accepted_closed(self):
        condition = "path=/original/guard ; path=/usr/bin/false"
        runner = self.units_fixture(
            missing=tuple(hook for hook in closure.HOOKS if hook != "ExecStartPost"),
            values={"ExecStartPost": "path=/original/duty-hook", "ExecCondition": condition},
        )
        units = runner.units(time.monotonic() + 30)
        self.assertTrue(
            all(
                row["ExecStartPost"] == "path=/original/duty-hook"
                and row["ExecCondition"] == condition
                for row in units.values()
            )
        )
        with self.assertRaisesRegex(Refusal, "loaded_barrier_invalid"):
            closure._unit_policy(units)

    def test_missing_scalar_job_trigger_or_guard_never_defaulted(self):
        for name in (
            "LoadState",
            "ActiveState",
            "SubState",
            "NeedDaemonReload",
            "MainPID",
            "ExecCondition",
            "Job",
            "TriggeredBy",
        ):
            with self.subTest(name=name):
                runner = self.units_fixture(missing=(*closure.HOOKS, name))
                with self.assertRaisesRegex(Refusal, "unit_status_incomplete"):
                    runner.units(time.monotonic() + 30)
                self.assertEqual(runner.command.call_count, 1)

    def test_typed_empty_proof_rejects_nonempty_wrong_type_missing_rows_and_read_failure(self):
        cases = [
            (
                closure.io.canonical({"type": "a(sasbttttuii)", "data": [["/mutable/hook"]]}),
                "unit_empty_hook_not_proven",
            ),
            (closure.io.canonical({"type": "as", "data": []}), "unit_empty_hook_not_proven"),
            (
                closure.io.canonical({"type": "a(sasbttttuii)", "data": [], "extra": True}),
                "unit_empty_hook_not_proven",
            ),
            (b"", "unit_empty_hooks_incomplete"),
            (Refusal("closure_command_failed"), "closure_command_failed"),
        ]
        for typed, error in cases:
            with self.subTest(error=error, typed=typed):
                runner = self.units_fixture(missing=("ExecStop",), typed=typed)
                with self.assertRaisesRegex(Refusal, error):
                    runner.units(time.monotonic() + 30)

    def test_duplicate_and_malformed_systemd_fields_are_refused(self):
        for extra in (b"Job=unsafe\n", b"malformed\n", b"Unexpected=\n"):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(Refusal, "unit_status_incomplete"):
                    self.units_fixture(extra=extra).units(time.monotonic() + 30)

    def test_trigger_requires_explicit_empty_job_and_inactive_state(self):
        for raw, error in (
            (b"ActiveState=inactive\n", "unit_status_incomplete"),
            (b"Job=\n", "unit_status_incomplete"),
            (b"ActiveState=active\nJob=\n", "trigger_active"),
            (b"ActiveState=inactive\nJob=0\n", "trigger_active"),
            (b"ActiveState=inactive\nJob=123/start\n", "trigger_active"),
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(Refusal, error):
                self.units_fixture(trigger="owned.timer", trigger_raw=raw).units(
                    time.monotonic() + 30
                )
        runner = self.units_fixture(trigger="owned.timer")
        self.assertEqual(len(runner.units(time.monotonic() + 30)), 8)
        self.assertTrue(
            all(
                "--all" in call.args[0]
                for call in runner.command.call_args_list
                if call.args[0][1] == "show"
            )
        )

    def test_job_zero_is_not_silently_treated_as_empty_and_native_queue_still_refuses(self):
        units = self.units_fixture(values={"Job": "0"}).units(time.monotonic() + 30)
        with self.assertRaisesRegex(Refusal, "unit_not_stopped"):
            closure._unit_policy(units)
        with self.assertRaisesRegex(Refusal, "native_job_queued"):
            self.units_fixture(
                jobs=closure.io.canonical(
                    {
                        "type": "a(usssoo)",
                        "data": [
                            [
                                [
                                    1,
                                    closure.UNITS[0],
                                    "start",
                                    "waiting",
                                    "/org/freedesktop/systemd1/job/1",
                                    closure._unit_object_path(closure.UNITS[0]),
                                ]
                            ]
                        ],
                    }
                )
            ).units(time.monotonic() + 30)

    def test_job_list_requires_typed_complete_rows_and_preserves_unrelated_jobs(self):
        unrelated = [
            1,
            "other.service",
            "start",
            "waiting",
            "/org/freedesktop/systemd1/job/1",
            "/org/freedesktop/systemd1/unit/other_2eservice",
        ]
        raw = closure.io.canonical({"type": "a(usssoo)", "data": [[unrelated]]})
        self.assertEqual(len(self.units_fixture(jobs=raw).units(time.monotonic() + 30)), 8)
        for value in (
            {"type": "as", "data": [[]]},
            {"type": "a(usssoo)", "data": []},
            {"type": "a(usssoo)", "data": [[unrelated[:-1]]]},
            {"type": "a(usssoo)", "data": [[[False, *unrelated[1:]]]]},
        ):
            with self.subTest(value=value), self.assertRaises(Refusal):
                self.units_fixture(jobs=closure.io.canonical(value)).units(time.monotonic() + 30)

    def test_readonly_child_forms_include_exact_all_and_typed_hooks_only(self):
        show = ("/usr/bin/systemctl", "show", closure.UNITS[0], "--no-pager")
        for flags in ((), ("--all",)):
            self.assertEqual(
                closure._readonly_control_child((*show, *flags, "--property=ActiveState,Job")),
                "/usr/bin/systemctl",
            )
        typed = tuple(closure._empty_hooks_command(closure.UNITS[0], ("ExecStartPre", "ExecStop")))
        self.assertEqual(closure._readonly_control_child(typed), "/usr/bin/busctl")
        self.assertEqual(closure._readonly_control_child(closure.JOB_QUERY), "/usr/bin/busctl")
        for command in (
            (*show, "--all", "--extra", "--property=Job"),
            (*typed[:-2], "Environment"),
            (*typed[:-2], "ExecStop", "ExecStop"),
            (*typed[:3], "set-property", *typed[4:]),
            (*typed[:5], "/org/freedesktop/systemd1/unit/other", *typed[6:]),
            (*closure.JOB_QUERY[:-1], "StartUnit"),
            (*closure.JOB_QUERY, "unexpected"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(closure._readonly_control_child(command))

    def test_transient_workers_require_explicit_bytecode_suppression(self):
        allowed = closure._forward_commands(ATTEMPT)
        prefix = ("/usr/bin/python3", "-I", "-S")
        for operation in ("run", "settle"):
            tail = (closure.FORWARD_SCRIPT, operation, "--attempt", ATTEMPT)
            with self.subTest(operation=operation):
                self.assertNotIn((*prefix, *tail), allowed)
                self.assertEqual(allowed[(*prefix, "-B", *tail)], operation)
        for flags in ((), ("-B",)):
            self.assertEqual(
                allowed[(*prefix, *flags, closure.FORWARD_SCRIPT, "apply", "--attempt", ATTEMPT)],
                "apply",
            )

    def test_current_release_launcher_matches_admitted_fixed_waiter(self):
        from scripts import j1_forward_release as release

        host = Mock()
        host.run.side_effect = RuntimeError("captured-before-effects")
        with (
            patch.object(release, "load_attempt", return_value=(None, None, {}, None, host)),
            patch.object(release, "remaining", return_value=600),
            patch.object(release, "CRM", PurePosixPath("/opt/autostopcrm")),
        ):
            with self.assertRaisesRegex(RuntimeError, "captured-before-effects"):
                release.launch(ATTEMPT)
        self.assertEqual(
            tuple(host.run.call_args.args[0]), closure._systemd_launch_command(ATTEMPT)
        )

    def test_current_worker_commands_match_admitted_control_forms(self):
        from scripts import j1_forward_worker as worker

        instance = object.__new__(worker.Worker)
        instance.ssh = Path("ssh.exe")
        instance.ssh_key = Path("private-key")
        allowed = closure._forward_commands(ATTEMPT)
        for operation, options in (
            ("inspect", {"stage": "closed"}),
            ("inspect", {"stage": "activated"}),
            ("export-delta", {}),
            ("receive-proof", {"kind": "offhost_restore"}),
            ("receive-proof", {"kind": "native_acceptance"}),
            ("receive-proof", {"kind": "runtime_acceptance"}),
        ):
            with self.subTest(operation=operation, options=options):
                command = instance._ssh(operation, ATTEMPT, **options)
                remote = tuple(command[command.index(worker.HOST) + 1 :])
                self.assertEqual(allowed.get(remote), operation)

    def test_control_source_and_baseline_are_bound_before_admission(self):
        directory = closure.ATTEMPTS / ATTEMPT
        native = {"native": "accepted"}
        consumers = {
            "scripts/j1_forward_" + name + ".py": "a" * 64
            for name in ("release", "closure", "io", "host", "state", "worker", "backup", "proofs")
        }
        baseline = {"native": native, "attempt_id": ATTEMPT, "source_consumers": consumers}
        plan = {
            "attempt_id": ATTEMPT,
            "baseline_ref": {"path": str(directory / "baseline.json"), "sha256": "b" * 64},
            "candidate": {"consumer_sha256": closure.io.sha(closure.io.canonical(consumers))},
        }
        owned = {
            "attempt_id": ATTEMPT,
            "attempt_dir": str(directory),
            "baseline_sha256": closure.io.sha(closure.io.canonical(native)),
        }
        with (
            patch.object(closure.io, "read_protected", return_value=closure.io.canonical(plan)),
            patch.object(closure.io, "referenced", return_value=closure.io.canonical(baseline)),
            patch.object(closure.io, "reference", return_value={"sha256": "a" * 64}) as refs,
        ):
            closure.LinuxRunner().control_bindings(owned)
            self.assertEqual(refs.call_count, len(consumers))
            refs.return_value = {"sha256": "c" * 64}
            with self.assertRaisesRegex(Refusal, "control_consumer_changed"):
                closure.LinuxRunner().control_bindings(owned)
            with self.assertRaisesRegex(Refusal, "control_baseline_binding"):
                closure.LinuxRunner().control_bindings({**owned, "baseline_sha256": "d" * 64})

    def test_proc_argv_preserves_empty_argument_boundaries(self):
        self.assertEqual(closure._argv(b"python\0arg\0\0"), ("python", "arg", ""))
        self.assertEqual(closure._argv(b"python\0arg\0"), ("python", "arg"))
        self.assertEqual(closure._argv(b""), ())

    def control_fixture(self):
        runner = closure.LinuxRunner()
        runner.control_bindings = Mock()
        runner.control_identity = Mock(return_value=True)
        commands = closure._forward_commands(ATTEMPT)
        argv = {operation: args for args, operation in commands.items()}

        def process(args, parent=1, group="0::/ssh.scope"):
            return {
                "argv": args,
                "ppid": parent,
                "state": "S",
                "start_ticks": 100,
                "cgroup": group,
                "suspect": True,
            }

        return runner, argv, process

    def test_concurrent_exact_main_and_inspection_are_authenticated(self):
        runner, argv, process = self.control_fixture()
        rows = {101: process(argv["run"]), 102: process(argv["inspect"])}
        result = runner.control_processes({"attempt_id": ATTEMPT}, rows)
        self.assertEqual(result, {101, 102})
        runner.control_bindings.assert_called_once()
        self.assertEqual(runner.control_identity.call_count, 2)
        self.assertEqual(
            runner.control_identity.call_args_list[0].kwargs,
            {"unit": "autostop-crm-forward-" + ATTEMPT + ".service"},
        )
        self.assertEqual(runner.control_identity.call_args_list[1].kwargs, {"unit": None})

    def test_changed_attempt_operation_or_extra_argument_never_admitted(self):
        runner, argv, process = self.control_fixture()
        wrong_attempt = tuple(
            "b921389d-490c-4608-852b-b5b9918798e0" if x == ATTEMPT else x for x in argv["inspect"]
        )
        wrong_role = tuple("arbitrary-write" if x == "inspect" else x for x in argv["inspect"])
        rows = {
            101: process(wrong_attempt),
            102: process(wrong_role),
            103: process((*argv["inspect"], "--force")),
            104: process(("/tmp/python", *argv["inspect"][1:])),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), set())
        runner.control_bindings.assert_not_called()
        runner.control_identity.assert_not_called()

    def test_consumer_binding_failure_cannot_admit_process(self):
        runner, argv, process = self.control_fixture()
        runner.control_bindings.side_effect = Refusal("control_consumer_changed")
        with self.assertRaisesRegex(Refusal, "control_consumer_changed"):
            runner.control_processes({"attempt_id": ATTEMPT}, {101: process(argv["inspect"])})
        runner.control_identity.assert_not_called()

    def test_systemd_waiter_requires_exact_command_and_authenticated_apply_parent(self):
        runner, argv, process = self.control_fixture()
        launch = closure._systemd_launch_command(ATTEMPT)
        rows = {
            101: process(argv["apply"]),
            102: process(launch, parent=101),
            103: process(launch, parent=999),
            104: process((*launch, "--extra"), parent=101),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102})
        self.assertEqual(runner.control_identity.call_args_list[-1].args[2], "/usr/bin/systemd-run")

    def test_shell_wrapper_must_be_literal_and_actual_authenticated_parent(self):
        runner, argv, process = self.control_fixture()
        command = " ".join(argv["inspect"])
        rows = {
            101: process(argv["inspect"], parent=102),
            102: process(("bash", "-c", command)),
            103: process(("bash", "-c", command)),
            104: process(("bash", "-c", command + "; true")),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102})
        self.assertEqual(runner.control_identity.call_args_list[-1].args[2], "/bin/bash")

    def test_only_readonly_fixed_command_children_are_admitted(self):
        runner, argv, process = self.control_fixture()
        git = ("/usr/bin/git", "-C", closure.CRM.as_posix())
        rows = {
            101: process(argv["inspect"]),
            102: process((*git, "status", "--porcelain"), parent=101),
            103: process((*git, "reset", "--hard"), parent=101),
            104: process((*git, "status", "--porcelain"), parent=999),
            105: process(
                (
                    "/usr/bin/systemctl",
                    "show",
                    closure.UNITS[0],
                    "--no-pager",
                    "--property=ActiveState,Job",
                ),
                parent=101,
            ),
            106: process(("/usr/bin/systemctl", "start", closure.UNITS[0]), parent=101),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102, 105})

    def test_fixed_bus_metadata_children_require_parent_and_actual_busctl_identity(self):
        runner, argv, process = self.control_fixture()
        hooks = tuple(closure._empty_hooks_command(closure.UNITS[0], ("ExecStop",)))
        rows = {
            101: process(argv["inspect"]),
            102: process(hooks, parent=101),
            103: process(closure.JOB_QUERY, parent=101),
            104: process(hooks, parent=999),
            105: process((*closure.JOB_QUERY[:-1], "StartUnit"), parent=101),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102, 103})
        self.assertEqual(
            [(call.args[0], call.args[2]) for call in runner.control_identity.call_args_list],
            [(101, "/usr/bin/python3"), (102, "/usr/bin/busctl"), (103, "/usr/bin/busctl")],
        )
        runner.control_identity.side_effect = [True, Refusal("control_executable_changed")]
        with self.assertRaisesRegex(Refusal, "control_executable_changed"):
            runner.control_processes({"attempt_id": ATTEMPT}, rows)

    def test_control_admission_is_not_a_write_descriptor_or_native_cgroup_exemption(self):
        runner = FakeRunner()
        runner.procs[777] = {
            "start_ticks": 100,
            "ppid": 1,
            "state": "S",
            "cgroup": "0::/ssh.scope",
            "suspect": True,
        }
        runner.control_processes = Mock(return_value={777})
        owned = {"warm_actors": [actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)]}
        with patch.object(closure, "RUNNER", runner):
            frozen, own = closure._process_policy(owned, runner.procs)
            self.assertNotIn(777, own)
            self.assertNotIn(777, frozen)
            runner.procs[777]["cgroup"] = "0::/system.slice/" + closure.UNITS[0]
            with self.assertRaisesRegex(Refusal, "native_cgroup_not_empty"):
                closure._process_policy(owned, runner.procs)
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.descriptor_fixture("2", target="/opt/autostopcrm/data/state.sqlite3-shm")

    def control_identity_fixture(
        self, *, uid=b"0 0 0 0", inode=9, final_birth=100, cgroup="0::/system.slice/owned.service"
    ):
        proc, root, program, trusted, hostroot = (MagicMock() for _ in range(5))
        nodes = {
            name: MagicMock() for name in ("cmdline", "status", "exe", "ns/mnt", "root", "cgroup")
        }
        global_namespace = MagicMock()
        proc.__truediv__.return_value = root
        root.__truediv__.side_effect = nodes.__getitem__
        program.resolve.return_value = trusted
        trusted.__str__.return_value = "/usr/bin/python3.12"
        trusted.stat.return_value = SimpleNamespace(st_dev=1, st_ino=9)
        nodes["exe"].stat.return_value = SimpleNamespace(st_dev=1, st_ino=inode)
        hostroot.stat.return_value = nodes["root"].stat.return_value = SimpleNamespace(
            st_dev=1, st_ino=1
        )
        argv = (
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            closure.FORWARD_SCRIPT,
            "run",
            "--attempt",
            ATTEMPT,
        )
        raw = {
            nodes["cmdline"]: b"\0".join(x.encode() for x in argv) + b"\0",
            nodes["status"]: b"Uid:\t" + uid + b"\n",
            nodes["exe"]: b"protected interpreter",
            nodes["cgroup"]: cgroup.encode(),
        }
        paths = {
            "/proc": proc,
            "/usr/bin/python3": program,
            "/": hostroot,
            "/proc/self/ns/mnt": global_namespace,
        }
        links = {
            nodes["exe"]: "/usr/bin/python3.12",
            nodes["ns/mnt"]: "mnt:[1]",
            global_namespace: "mnt:[1]",
        }
        row = {"start_ticks": 100, "ppid": 1, "argv": argv, "cgroup": cgroup}
        with (
            patch.object(closure, "Path", side_effect=paths.__getitem__),
            patch.object(closure, "_stat", side_effect=[(100, 1, "S"), (final_birth, 1, "S")]),
            patch.object(closure, "_proc_bytes", side_effect=lambda path, *args: raw[path]),
            patch.object(closure.io, "read_protected", return_value=b"protected interpreter"),
            patch.object(closure.os, "readlink", side_effect=links.__getitem__),
        ):
            return closure.LinuxRunner.control_identity(
                123, row, "/usr/bin/python3", unit="owned.service"
            )

    def test_control_identity_checks_root_program_pid_birth_and_service(self):
        self.assertTrue(self.control_identity_fixture())
        for changes, error in (
            ({"uid": b"0 1000 0 0"}, "control_root_required"),
            ({"inode": 10}, "control_executable_changed"),
            ({"final_birth": 101}, "control_pid_changed"),
            ({"cgroup": "0::/system.slice/other.service"}, "control_service_required"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(Refusal, error):
                self.control_identity_fixture(**changes)

    def test_container_stop_is_attempted_even_if_unit_stop_fails(self):
        runner = closure.LinuxRunner()
        runner.command = Mock(side_effect=[Refusal("unit-stop"), b""])
        with self.assertRaisesRegex(Refusal, "writer_stop_failed"):
            runner.stop(time.monotonic() + 10)
        first, second = [call.args[0] for call in runner.command.call_args_list]
        self.assertEqual(first, ["/usr/bin/systemctl", "stop", *closure.UNITS])
        self.assertEqual(second[:3], closure.DOCKER)
        self.assertEqual(second[-2:], ["stop", "autostopcrm"])
        self.assertNotIn("start", second)

    def test_pidfd_is_opened_before_identity_recheck_and_reused_pid_not_signaled(self):
        runner = closure.LinuxRunner()
        expected = actor(538285)
        runner.actor = Mock(return_value={**expected, "start_ticks": 999, "stopped": False})
        with (
            patch.object(closure.os, "pidfd_open", return_value=42, create=True) as opened,
            patch.object(closure.signal, "pidfd_send_signal", create=True) as sent,
            patch.object(closure.os, "close") as closed,
        ):
            with self.assertRaisesRegex(Refusal, "warm_actor_changed"):
                runner.signal_actor(expected, True)
        opened.assert_called_once_with(538285, 0)
        sent.assert_not_called()
        closed.assert_called_once_with(42)

    def test_container_inspection_excludes_environment_and_rejects_parent_mount_writer(self):
        runner = closure.LinuxRunner()
        identity = "a" * 64
        runner.command = Mock(
            side_effect=[
                identity.encode() + b"\n",
                closure.io.canonical(
                    {
                        "Id": identity,
                        "Name": "/another-project",
                        "Service": "other",
                        "Mounts": [{"Source": "/opt", "RW": True}],
                    }
                ),
            ]
        )
        with self.assertRaisesRegex(Refusal, "alternative_container_writer"):
            runner.containers(time.monotonic() + 10)
        inspection = runner.command.call_args_list[1].args[0]
        self.assertIn("--format", inspection)
        self.assertNotIn("Env", inspection[inspection.index("--format") + 1])

    def test_readonly_mount_from_another_project_is_not_stopped(self):
        runner = closure.LinuxRunner()
        identity = "a" * 64
        runner.command = Mock(
            side_effect=[
                identity.encode() + b"\n",
                closure.io.canonical(
                    {
                        "Id": identity,
                        "Name": "/other",
                        "Service": "other",
                        "Mounts": [{"Source": "/opt/autostopcrm/data", "RW": False}],
                    }
                ),
            ]
        )
        self.assertEqual(runner.containers(time.monotonic() + 10), [{"id": identity}])
        self.assertFalse(any("stop" in call.args[0] for call in runner.command.call_args_list))

    def descriptor_fixture(
        self, flags, *, target="/opt/autostopcrm/data/state.json", frozen=(), own=(), alias=False
    ):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "123"
            (root / "fd").mkdir(parents=True)
            (root / "fdinfo").mkdir()
            descriptor = root / "fd/5"
            descriptor.write_bytes(b"synthetic")
            (root / "fdinfo/5").write_text("flags:\t" + flags + "\n")
            (root / "maps").write_bytes(b"")
            metadata = descriptor.stat()
            runner = closure.LinuxRunner()
            runner.source_inodes = Mock(
                return_value={(metadata.st_dev, metadata.st_ino)} if alias else set()
            )
            original_path = Path

            def redirected(value):
                return base if str(value) == "/proc" else original_path(value)

            with (
                patch.object(closure, "Path", side_effect=redirected),
                patch.object(closure.os, "readlink", return_value=target),
                patch.object(closure.os, "O_ACCMODE", 3, create=True),
                patch.object(closure, "_stat", return_value=(100, 1, "S")),
            ):
                return runner.descriptor_scan({123: {"start_ticks": 100}}, set(frozen), set(own))

    def test_readonly_source_descriptor_is_permitted(self):
        result = self.descriptor_fixture("0")
        self.assertEqual(result["frozen_owned_write_fds"], 0)

    def test_primary_source_write_descriptor_is_rejected_even_for_backup_process(self):
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.descriptor_fixture("2", own={123})

    def test_unknown_hardlink_alias_writer_is_detected_by_inode(self):
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.descriptor_fixture("2", target="/tmp/not-the-source-name", alias=True)

    def test_exact_frozen_actor_descriptor_is_recorded_not_called_active(self):
        result = self.descriptor_fixture("2", frozen={123})
        self.assertEqual(result["frozen_owned_write_fds"], 1)

    def test_only_real_sqlite_auxiliary_can_be_coordination_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "synthetic.sqlite3"
            with patch.object(closure, "_under", return_value=True):
                primary.write_bytes(b"not a database")
                self.assertFalse(closure.LinuxRunner.coordination_path(str(primary) + "-shm"))
                primary.write_bytes(b"SQLite format 3\0")
                self.assertTrue(closure.LinuxRunner.coordination_path(str(primary) + "-shm"))
                self.assertFalse(closure.LinuxRunner.coordination_path(str(primary) + "-wal"))

    def test_source_inventory_does_not_hide_walk_errors(self):
        def incomplete(path, *, followlinks, onerror):
            onerror(PermissionError("synthetic"))
            return []

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(closure, "WRITER_ROOTS", (directory,)),
            patch.object(closure.os, "walk", side_effect=incomplete),
        ):
            with self.assertRaisesRegex(Refusal, "source_inventory_incomplete"):
                closure.LinuxRunner.source_inodes()


if __name__ == "__main__":
    unittest.main()
