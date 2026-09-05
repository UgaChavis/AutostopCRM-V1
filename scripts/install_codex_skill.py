"""Check or install the versioned CRM skill; retain replaced packages for rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

SOURCE = Path(__file__).resolve().parents[1] / "tools/codex/skills/autostopcrm-maintain"
SKILL_NAMES = (
    "autostopcrm-maintain",
    "autostopcrm-code-maintain",
    "autostopcrm-optimize",
    "autostopcrm-ui-optimize",
)


def manifest(directory: Path) -> dict[str, str]:
    """Read only plain trees; never traverse links or Windows junctions."""
    absolute = Path(os.path.abspath(directory))
    if absolute.resolve() != absolute or not absolute.is_dir():
        raise ValueError(f"Expected a non-linked directory: {directory}")
    files: dict[str, str] = {}
    pending = [absolute]
    while pending:
        path = pending.pop()
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Skill paths cannot be links: {path}")
        if path.is_dir():
            pending.extend(path.iterdir())
        elif path.is_file():
            files[path.relative_to(absolute).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        else:
            raise ValueError(f"Unsupported skill entry: {path}")
    return dict(sorted(files.items()))


def synchronize(source: Path, skills_root: Path, *, apply: bool = False) -> dict[str, object]:
    source_files = manifest(source)
    if "SKILL.md" not in source_files:
        raise ValueError("Source package has no SKILL.md")
    skills_root = Path(os.path.abspath(skills_root.expanduser()))
    if skills_root.resolve() != skills_root:
        raise ValueError("Installed skills root cannot traverse links")
    destinations = [skills_root / name for name in SKILL_NAMES]
    existing = [path for path in destinations if path.exists() or path.is_symlink()]
    installed = {path.name: manifest(path) for path in existing}
    current = installed == {SKILL_NAMES[0]: source_files}
    result: dict[str, object] = {"current": current, "files": source_files}
    if current or not apply:
        return result

    # Every moved path is a verified direct child of this explicit skills root.
    for path in existing:
        if path.resolve().parent != skills_root or path.name not in SKILL_NAMES:
            raise ValueError(f"Skill destination escaped its scope: {path}")
    backup = skills_root.parent / "skill-backups" / f"autostopcrm-{uuid4().hex}"
    if backup.resolve() != backup:
        raise ValueError("Backup path cannot traverse links")
    skills_root.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True)
    staging = Path(tempfile.mkdtemp(prefix=".autostopcrm-install-", dir=skills_root.parent))
    shutil.copytree(source, staging, dirs_exist_ok=True)
    if manifest(staging) != source_files:
        raise ValueError("Source changed while staging the skill")
    moved: list[Path] = []
    try:
        for path in existing:
            if manifest(path) != installed[path.name]:
                raise ValueError(f"Installed skill changed during synchronization: {path.name}")
            path.replace(backup / path.name)
            moved.append(path)
        staging.replace(destinations[0])
    except BaseException:
        for path in reversed(moved):
            (backup / path.name).replace(path)
        raise
    return {"current": True, "backup": str(backup), "files": source_files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Install and back up replaced CRM skills"
    )
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills",
        help="Explicit installation root (default: CODEX_HOME/skills)",
    )
    args = parser.parse_args()
    try:
        result = synchronize(SOURCE, args.skills_root, apply=args.apply)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"CRM skill synchronization failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["current"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
