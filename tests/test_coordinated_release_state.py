from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if __package__:
    from tests.source_path_support import ensure_repository_root_path
else:
    from source_path_support import ensure_repository_root_path

ensure_repository_root_path()

from scripts.coordinated_release_state import (
    Artifact,
    ReleaseLayout,
    _read_service_state,
    capture,
    restore,
    stop_candidate_services,
    verify,
)


class FakeSystemctl:
    def __init__(self, states: dict[str, dict[str, str]]) -> None:
        self.states = states
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[:2] == ("systemctl", "show"):
            state = self.states[command[2]]
            stdout = (
                f"LoadState={state['load_state']}\n"
                f"ActiveState={state['active_state']}\n"
                f"UnitFileState={state['unit_file_state']}\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout, "")
        if command[:2] == ("systemctl", "daemon-reload"):
            return subprocess.CompletedProcess(command, 0, "", "")
        action = command[1]
        unit = command[-1]
        state = self.states[unit]
        if action == "enable":
            state["unit_file_state"] = "enabled-runtime" if "--runtime" in command else "enabled"
        elif action == "disable":
            state["unit_file_state"] = "disabled"
            if "--now" in command:
                state["active_state"] = "inactive"
        elif action == "start":
            state["active_state"] = "active"
        elif action == "stop":
            state["active_state"] = "inactive"
        return subprocess.CompletedProcess(command, 0, "", "")


def _layout(
    tmp_path: Path, *, database_present: bool = True
) -> tuple[ReleaseLayout, dict[str, Path]]:
    system = tmp_path / "system"
    releases = tmp_path / "releases"
    runtimes = tmp_path / "runtimes"
    system.mkdir()
    releases.mkdir()
    runtimes.mkdir()
    release_a = releases / "release-a"
    release_b = releases / "release-b"
    runtime_a = runtimes / "runtime-a"
    runtime_b = runtimes / "runtime-b"
    for path in (release_a, release_b, runtime_a, runtime_b):
        path.mkdir()
    unit = system / "scheduler.service"
    unit.write_text("old-unit\n", encoding="utf-8")
    os.chmod(unit, 0o640)
    current = system / "telegram-current"
    current.symlink_to(release_a)
    database = system / "registry.sqlite3"
    if database_present:
        database.write_bytes(b"candidate-held-database")
        os.chmod(database, 0o600)
    owner = system / "owner.env"
    owner.write_text("private-target\n", encoding="utf-8")
    os.chmod(owner, 0o600)
    layout = ReleaseLayout(
        scheduler_db=database,
        artifacts=(
            Artifact("scheduler_unit", unit, "file"),
            Artifact("optional_config", system / "optional.env", "file"),
            Artifact("owner_config", owner, "file"),
            Artifact("telegram_current", current, "symlink", releases),
        ),
        services=("scheduler.service", "telegram.service"),
    )
    return layout, {
        "unit": unit,
        "current": current,
        "database": database,
        "owner": owner,
        "release_a": release_a,
        "release_b": release_b,
    }


@unittest.skipUnless(os.name == "posix", "Release state requires POSIX ownership and permissions")
class CoordinatedReleaseStateTests(unittest.TestCase):
    def test_service_state_preserves_repeated_timer_properties(self) -> None:
        def repeated_timer_state(
            command: tuple[str, ...],
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "UnitFileState=enabled\n"
                "TimersMonotonic={ OnUnitActiveUSec=5min ; next_elapse=5min }\n"
                "TimersMonotonic={ OnBootUSec=7min ; next_elapse=7min }\n",
                "",
            )

        state = _read_service_state("example.timer", repeated_timer_state)

        self.assertIn("OnUnitActiveUSec=5min", state["timers_monotonic"])
        self.assertIn("OnBootUSec=7min", state["timers_monotonic"])

    def test_stop_candidates_skips_only_unit_confirmed_not_found(self) -> None:
        states = {
            "autostop-codex-wake.service": {
                "load_state": "loaded",
                "active_state": "active",
                "unit_file_state": "enabled",
            },
            "autostop-work-telegram.service": {
                "load_state": "loaded",
                "active_state": "active",
                "unit_file_state": "enabled",
            },
            "autostop-manager-scheduler.service": {
                "load_state": "not-found",
                "active_state": "inactive",
                "unit_file_state": "disabled",
            },
        }
        systemctl = FakeSystemctl(states)
        layout = ReleaseLayout(services=tuple(states))

        result = stop_candidate_services(layout=layout, runner=systemctl)

        self.assertEqual(
            result["stopped"],
            [
                "autostop-codex-wake.service",
                "autostop-work-telegram.service",
            ],
        )
        self.assertIn(
            (
                "systemctl",
                "show",
                "autostop-manager-scheduler.service",
                *(
                    "--property=LoadState",
                    "--property=ActiveState",
                    "--property=UnitFileState",
                    "--property=TimersCalendar",
                    "--property=TimersMonotonic",
                    "--no-pager",
                ),
            ),
            systemctl.calls,
        )
        self.assertNotIn(
            ("systemctl", "stop", "autostop-manager-scheduler.service"),
            systemctl.calls,
        )

    def test_stop_candidates_preserves_loaded_unit_stop_failure(self) -> None:
        class FailingStopSystemctl(FakeSystemctl):
            def __call__(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                if command == ("systemctl", "stop", "autostop-work-telegram.service"):
                    self.calls.append(command)
                    return subprocess.CompletedProcess(command, 1, "", "stop failed")
                return super().__call__(command)

        states = {
            unit: {
                "load_state": "loaded",
                "active_state": "active",
                "unit_file_state": "enabled",
            }
            for unit in (
                "autostop-codex-wake.service",
                "autostop-work-telegram.service",
                "autostop-manager-scheduler.service",
            )
        }
        systemctl = FailingStopSystemctl(states)
        layout = ReleaseLayout(services=tuple(states))

        with self.assertRaisesRegex(RuntimeError, "coordinated_release_service_stop_failed"):
            stop_candidate_services(layout=layout, runner=systemctl)

    def test_stop_candidates_fails_closed_without_exact_load_state(self) -> None:
        commands: list[tuple[str, ...]] = []

        def unavailable_systemctl(
            command: tuple[str, ...],
        ) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "show failed")

        layout = ReleaseLayout(services=("autostop-codex-wake.service",))

        with self.assertRaisesRegex(RuntimeError, "coordinated_release_service_state_unavailable"):
            stop_candidate_services(layout=layout, runner=unavailable_systemctl)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:3], ("systemctl", "show", "autostop-codex-wake.service"))

    def test_capture_verify_and_restore_exact_files_links_database_and_services(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            layout, paths = _layout(tmp_path)
            states = {
                "scheduler.service": {
                    "load_state": "loaded",
                    "active_state": "active",
                    "unit_file_state": "enabled",
                },
                "telegram.service": {
                    "load_state": "loaded",
                    "active_state": "inactive",
                    "unit_file_state": "disabled",
                },
            }
            systemctl = FakeSystemctl(states)
            backup_root = tmp_path / "backups"
            backup_root.mkdir(mode=0o700)
            snapshot = backup_root / "coordinated"

            result = capture(snapshot, layout=layout, runner=systemctl)
            self.assertTrue(result["scheduler_database_preexisting"])
            self.assertTrue(verify(snapshot, layout=layout)["ok"])

            database_backup = backup_root / "registry-backup.sqlite3"
            database_backup.write_bytes(b"held-online-backup")
            os.chmod(database_backup, 0o600)
            paths["unit"].write_text("candidate-unit\n", encoding="utf-8")
            paths["owner"].write_text("candidate-target\n", encoding="utf-8")
            (paths["unit"].parent / "optional.env").write_text("candidate\n", encoding="utf-8")
            paths["current"].unlink()
            paths["current"].symlink_to(paths["release_b"])
            paths["database"].write_bytes(b"migrated")
            Path(f"{paths['database']}-wal").write_bytes(b"wal")
            states["scheduler.service"].update(active_state="inactive", unit_file_state="disabled")
            states["telegram.service"].update(active_state="active", unit_file_state="enabled")

            with mock.patch("scripts.coordinated_release_state.os.chown") as chown:
                restored = restore(
                    snapshot,
                    database_backup=database_backup,
                    layout=layout,
                    runner=systemctl,
                )
            self.assertGreaterEqual(chown.call_count, 1)
            registry_chowns = [
                call
                for call in chown.call_args_list
                if Path(call.args[0]).name.startswith(".registry.restore-")
            ]
            self.assertEqual(len(registry_chowns), 1)
            self.assertEqual(registry_chowns[0].args[1:3], (0, 0))

            self.assertTrue(restored["scheduler_database_restored"])
            self.assertEqual(paths["unit"].read_text(encoding="utf-8"), "old-unit\n")
            self.assertEqual(paths["owner"].read_text(encoding="utf-8"), "private-target\n")
            self.assertEqual(stat.S_IMODE(paths["unit"].stat().st_mode), 0o640)
            self.assertFalse((paths["unit"].parent / "optional.env").exists())
            self.assertEqual(paths["current"].resolve(), paths["release_a"])
            self.assertEqual(paths["database"].read_bytes(), b"held-online-backup")
            self.assertFalse(Path(f"{paths['database']}-wal").exists())
            self.assertEqual(states["scheduler.service"]["active_state"], "active")
            self.assertEqual(states["scheduler.service"]["unit_file_state"], "enabled")
            self.assertEqual(states["telegram.service"]["active_state"], "inactive")
            self.assertEqual(states["telegram.service"]["unit_file_state"], "disabled")

    def test_first_install_rollback_restores_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            layout, paths = _layout(tmp_path, database_present=False)
            paths["unit"].unlink()
            paths["current"].unlink()
            states = {
                unit: {
                    "load_state": "not-found",
                    "active_state": "inactive",
                    "unit_file_state": "disabled",
                }
                for unit in layout.services
            }
            systemctl = FakeSystemctl(states)
            backup_root = tmp_path / "backups"
            backup_root.mkdir(mode=0o700)
            snapshot = backup_root / "coordinated"
            capture(snapshot, layout=layout, runner=systemctl)

            paths["unit"].write_text("candidate\n", encoding="utf-8")
            paths["current"].symlink_to(paths["release_b"])
            paths["database"].write_bytes(b"created-by-release")

            result = restore(
                snapshot,
                database_backup=None,
                layout=layout,
                runner=systemctl,
            )

            self.assertTrue(result["first_install_absence_restored"])
            self.assertFalse(paths["unit"].exists())
            self.assertFalse(paths["current"].exists())
            self.assertFalse(paths["database"].exists())


if __name__ == "__main__":
    unittest.main()
