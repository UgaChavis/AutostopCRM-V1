from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from scripts.coordinated_release_state import (
    Artifact,
    ReleaseLayout,
    capture,
    restore,
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


def test_capture_verify_and_restore_exact_files_links_database_and_services(
    tmp_path: Path,
) -> None:
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
    assert result["scheduler_database_preexisting"] is True
    assert verify(snapshot, layout=layout)["ok"] is True

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

    restored = restore(
        snapshot,
        database_backup=database_backup,
        layout=layout,
        runner=systemctl,
    )

    assert restored["scheduler_database_restored"] is True
    assert paths["unit"].read_text(encoding="utf-8") == "old-unit\n"
    assert paths["owner"].read_text(encoding="utf-8") == "private-target\n"
    assert stat.S_IMODE(paths["unit"].stat().st_mode) == 0o640
    assert not (paths["unit"].parent / "optional.env").exists()
    assert paths["current"].resolve() == paths["release_a"]
    assert paths["database"].read_bytes() == b"held-online-backup"
    assert not Path(f"{paths['database']}-wal").exists()
    assert states["scheduler.service"]["active_state"] == "active"
    assert states["scheduler.service"]["unit_file_state"] == "enabled"
    assert states["telegram.service"]["active_state"] == "inactive"
    assert states["telegram.service"]["unit_file_state"] == "disabled"


def test_first_install_rollback_restores_absence(tmp_path: Path) -> None:
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

    assert result["first_install_absence_restored"] is True
    assert not paths["unit"].exists()
    assert not paths["current"].exists()
    assert not paths["database"].exists()
