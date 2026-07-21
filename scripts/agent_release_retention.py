from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

RELEASE_ID_PATTERN = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9]+"
BACKUP_NAME_PATTERN = re.compile(rf"{RELEASE_ID_PATTERN}")
MANAGER_RELEASE_NAME_PATTERN = re.compile(rf"{RELEASE_ID_PATTERN}-manager-[0-9a-f]{{12}}")
CRM_RELEASE_TAG_PATTERN = re.compile(r"autostopcrm:[0-9a-f]{12}")
CRM_ROLLBACK_TAG_PATTERN = re.compile(rf"autostopcrm-rollback:{RELEASE_ID_PATTERN}")
DOCKER_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}")
DOCKER_IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
MAX_RETENTION_COUNT = 100


class RetentionError(RuntimeError):
    pass


def _retention_count(value: int, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not minimum <= value <= MAX_RETENTION_COUNT:
        raise RetentionError(f"retention count must be between {minimum} and {MAX_RETENTION_COUNT}")
    return value


def _validated_root(path: Path) -> tuple[Path, int]:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise RetentionError(f"retention root must be an existing absolute directory: {path}")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise RetentionError(f"retention root must already be canonical: {path}")
    forbidden = {Path("/"), Path("/root"), Path("/opt"), Path.home().resolve()}
    if resolved in forbidden or len(resolved.parts) < 3:
        raise RetentionError(f"retention root is too broad: {resolved}")
    return resolved, resolved.stat().st_dev


def _validated_docker_reference(reference: str) -> str:
    normalized = str(reference or "").strip()
    if DOCKER_REFERENCE_PATTERN.fullmatch(normalized) is None:
        raise RetentionError("invalid Docker reference")
    return normalized


def _validated_image_id(image_id: str) -> str:
    normalized = str(image_id or "").strip().casefold()
    if DOCKER_IMAGE_ID_PATTERN.fullmatch(normalized) is None:
        raise RetentionError("invalid Docker image identity")
    return normalized


def _validate_candidate_tree(path: Path, *, root_device: int) -> None:
    for current_root, directory_names, file_names in os.walk(path, followlinks=False):
        current = Path(current_root)
        current_stat = current.lstat()
        if stat.S_ISLNK(current_stat.st_mode) or current_stat.st_dev != root_device:
            raise RetentionError(f"release artifact crosses a symlink or filesystem: {current}")
        if current != path and os.path.ismount(current):
            raise RetentionError(f"release artifact contains a mountpoint: {current}")
        for name in (*directory_names, *file_names):
            child = current / name
            child_stat = child.lstat()
            if stat.S_ISLNK(child_stat.st_mode) or child_stat.st_dev != root_device:
                raise RetentionError(f"release artifact crosses a symlink or filesystem: {child}")
            if os.path.ismount(child):
                raise RetentionError(f"release artifact contains a mountpoint: {child}")


def _protected_direct_child_names(root: Path, protected: Iterable[Path]) -> set[str]:
    names: set[str] = set()
    for raw_path in protected:
        if not raw_path.is_absolute():
            raise RetentionError(f"protected release path must be absolute: {raw_path}")
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError as exc:
            raise RetentionError(f"protected release path is unavailable: {raw_path}") from exc
        if resolved.parent == root:
            names.add(resolved.name)
    return names


def _filesystem_prune_plan(
    *,
    root: Path,
    name_pattern: re.Pattern[str],
    marker_path: str,
    protected: Iterable[Path],
    keep: int,
) -> tuple[Path, list[Path]]:
    resolved_root, root_device = _validated_root(root)
    keep = _retention_count(keep)
    protected_names = _protected_direct_child_names(resolved_root, protected)
    candidates: list[Path] = []
    for child in resolved_root.iterdir():
        if name_pattern.fullmatch(child.name) is None:
            continue
        if child.is_symlink() or not child.is_dir() or os.path.ismount(child):
            raise RetentionError(f"release artifact is not a removable directory: {child}")
        if not (child / marker_path).exists():
            raise RetentionError(f"release artifact marker is missing: {child / marker_path}")
        _validate_candidate_tree(child, root_device=root_device)
        candidates.append(child)

    unknown_protected = protected_names.difference(item.name for item in candidates)
    if unknown_protected:
        raise RetentionError(
            "protected direct child is not a validated release artifact: "
            + ", ".join(sorted(unknown_protected))
        )
    if len(protected_names) > keep:
        raise RetentionError("retention count is below the protected release count")

    retained = set(protected_names)
    for candidate in sorted(candidates, key=lambda item: item.name, reverse=True):
        if len(retained) >= keep:
            break
        retained.add(candidate.name)
    return resolved_root, [item for item in candidates if item.name not in retained]


def _docker_prune_plan(
    rows: Iterable[Mapping[str, Any]],
    *,
    pattern: re.Pattern[str],
    protected: Iterable[str],
    keep: int,
) -> list[str]:
    keep = _retention_count(keep)
    protected_tags = {
        _validated_docker_reference(str(item)) for item in protected if str(item).strip()
    }

    by_tag: dict[str, str] = {}
    for row in rows:
        repository = str(row.get("Repository") or "").strip()
        tag = str(row.get("Tag") or "").strip()
        reference = f"{repository}:{tag}"
        if pattern.fullmatch(reference) is None:
            continue
        by_tag[reference] = str(row.get("CreatedAt") or "")

    matched_protected = protected_tags.intersection(by_tag)
    if len(matched_protected) > keep:
        raise RetentionError("Docker retention count is below the protected reference count")
    retained = set(matched_protected)
    newest_first = sorted(
        by_tag,
        key=lambda reference: (by_tag[reference], reference),
        reverse=True,
    )
    for reference in newest_first:
        if len(retained) >= keep:
            break
        retained.add(reference)
    return [reference for reference in newest_first if reference not in retained]


def _docker_image_rows() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["docker", "image", "ls", "--no-trunc", "--format", "{{json .}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RetentionError("Docker image inventory row is not an object")
        rows.append(value)
    return rows


def _docker_image_id(reference: str) -> str | None:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return None
    image_id = completed.stdout.strip().casefold()
    return _validated_image_id(image_id)


def _validated_owned_attempt_paths(
    *,
    manager_release_root: Path,
    release_id: str,
    manager_revision: str,
    owned_paths: Sequence[Path],
    protected_paths: Sequence[Path],
) -> list[Path]:
    root, root_device = _validated_root(manager_release_root)
    if BACKUP_NAME_PATTERN.fullmatch(release_id) is None:
        raise RetentionError("invalid owned release identity")
    normalized_revision = str(manager_revision or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{40,64}", normalized_revision) is None:
        raise RetentionError("invalid Manager revision identity")
    attempt_pid = release_id.rsplit("-", 1)[-1]
    final_name = f"{release_id}-manager-{normalized_revision[:12]}"
    expected = {
        root / final_name,
        root / f"{final_name}.partial-{attempt_pid}",
    }
    protected_resolved: set[Path] = set()
    for protected in protected_paths:
        if not protected.is_absolute():
            raise RetentionError(f"protected release path must be absolute: {protected}")
        try:
            protected_resolved.add(protected.resolve(strict=True))
        except OSError as exc:
            raise RetentionError(f"protected release path is unavailable: {protected}") from exc

    planned: list[Path] = []
    for owned_path in owned_paths:
        if not owned_path.is_absolute() or owned_path not in expected:
            raise RetentionError(f"owned Manager artifact identity mismatch: {owned_path}")
        if not owned_path.exists() and not owned_path.is_symlink():
            continue
        if owned_path.is_symlink() or not owned_path.is_dir() or os.path.ismount(owned_path):
            raise RetentionError(f"owned Manager artifact is not removable: {owned_path}")
        resolved = owned_path.resolve(strict=True)
        if resolved != owned_path or resolved in protected_resolved:
            raise RetentionError(
                f"owned Manager artifact is protected or non-canonical: {owned_path}"
            )
        _validate_candidate_tree(owned_path, root_device=root_device)
        planned.append(owned_path)
    return planned


def cleanup_owned_attempt_artifacts(
    *,
    manager_release_root: Path,
    release_id: str,
    manager_revision: str,
    owned_manager_paths: Sequence[Path],
    protected_manager_paths: Sequence[Path],
    owned_image_tags: Sequence[tuple[str, str]],
    restored_image_tags: Sequence[tuple[str, str, str]],
    protected_image_tags: Sequence[str],
) -> dict[str, Any]:
    paths_to_delete = _validated_owned_attempt_paths(
        manager_release_root=manager_release_root,
        release_id=release_id,
        manager_revision=manager_revision,
        owned_paths=owned_manager_paths,
        protected_paths=protected_manager_paths,
    )
    protected_tags = {_validated_docker_reference(reference) for reference in protected_image_tags}
    remove_actions: list[tuple[str, str]] = []
    restore_actions: list[tuple[str, str | None, str]] = []
    action_tags: set[str] = set()
    for reference, owned_image_id in owned_image_tags:
        normalized_reference = _validated_docker_reference(reference)
        normalized_image_id = _validated_image_id(owned_image_id)
        if normalized_reference in protected_tags or normalized_reference in action_tags:
            raise RetentionError("owned Docker reference is protected or duplicated")
        action_tags.add(normalized_reference)
        actual_image_id = _docker_image_id(normalized_reference)
        if actual_image_id is None:
            continue
        if actual_image_id != normalized_image_id:
            raise RetentionError("owned Docker reference identity changed before cleanup")
        remove_actions.append((normalized_reference, normalized_image_id))

    for reference, observed_image_id, previous_image_id in restored_image_tags:
        normalized_reference = _validated_docker_reference(reference)
        normalized_previous_id = _validated_image_id(previous_image_id)
        normalized_observed_id = (
            None
            if str(observed_image_id or "").strip().casefold() == "absent"
            else _validated_image_id(observed_image_id)
        )
        if normalized_reference in protected_tags or normalized_reference in action_tags:
            raise RetentionError("restored Docker reference is protected or duplicated")
        action_tags.add(normalized_reference)
        if _docker_image_id(normalized_previous_id) != normalized_previous_id:
            raise RetentionError("previous Docker image identity is unavailable")
        actual_image_id = _docker_image_id(normalized_reference)
        if actual_image_id == normalized_previous_id:
            continue
        if actual_image_id != normalized_observed_id:
            raise RetentionError("replaced Docker reference identity changed before cleanup")
        restore_actions.append(
            (normalized_reference, normalized_observed_id, normalized_previous_id)
        )

    for path in paths_to_delete:
        shutil.rmtree(path)
    removed_tags: list[str] = []
    for reference, _owned_image_id in remove_actions:
        subprocess.run(
            ["docker", "image", "rm", reference],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        removed_tags.append(reference)
    restored_tags: list[str] = []
    for reference, observed_image_id, previous_image_id in restore_actions:
        subprocess.run(
            ["docker", "tag", previous_image_id, reference],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if _docker_image_id(reference) != previous_image_id:
            raise RetentionError("Docker reference restoration did not verify")
        restored_tags.append(reference)
        if observed_image_id is not None and observed_image_id != previous_image_id:
            subprocess.run(
                ["docker", "image", "rm", observed_image_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

    return {
        "ok": True,
        "removed_manager_artifacts": [path.name for path in paths_to_delete],
        "removed_image_tags": removed_tags,
        "restored_image_tags": restored_tags,
    }


def prune_release_artifacts(
    *,
    backup_root: Path,
    manager_release_root: Path,
    protected_backup: Path,
    protected_manager_releases: Sequence[Path],
    protected_image_tags: Sequence[str],
    keep_backups: int,
    keep_manager_releases: int,
    keep_release_images: int,
    keep_rollback_images: int,
) -> dict[str, Any]:
    backup_root, backups_to_delete = _filesystem_prune_plan(
        root=backup_root,
        name_pattern=BACKUP_NAME_PATTERN,
        marker_path="manifest.json",
        protected=(protected_backup,),
        keep=keep_backups,
    )
    manager_release_root, manager_releases_to_delete = _filesystem_prune_plan(
        root=manager_release_root,
        name_pattern=MANAGER_RELEASE_NAME_PATTERN,
        marker_path="autostop_manager",
        protected=protected_manager_releases,
        keep=keep_manager_releases,
    )
    docker_rows = _docker_image_rows()
    release_tags_to_delete = _docker_prune_plan(
        docker_rows,
        pattern=CRM_RELEASE_TAG_PATTERN,
        protected=protected_image_tags,
        keep=keep_release_images,
    )
    rollback_tags_to_delete = _docker_prune_plan(
        docker_rows,
        pattern=CRM_ROLLBACK_TAG_PATTERN,
        protected=protected_image_tags,
        keep=keep_rollback_images,
    )

    for path in backups_to_delete:
        shutil.rmtree(path)
    for path in manager_releases_to_delete:
        shutil.rmtree(path)
    for reference in (*release_tags_to_delete, *rollback_tags_to_delete):
        subprocess.run(
            ["docker", "image", "rm", reference],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    return {
        "ok": True,
        "backup_root": str(backup_root),
        "manager_release_root": str(manager_release_root),
        "removed_backups": [item.name for item in backups_to_delete],
        "removed_manager_releases": [item.name for item in manager_releases_to_delete],
        "removed_image_tags": [*release_tags_to_delete, *rollback_tags_to_delete],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage validated release artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prune = subparsers.add_parser("prune")
    prune.add_argument("--backup-root", type=Path, required=True)
    prune.add_argument("--manager-release-root", type=Path, required=True)
    prune.add_argument("--protected-backup", type=Path, required=True)
    prune.add_argument(
        "--protected-manager-release", type=Path, action="append", default=[], required=True
    )
    prune.add_argument("--protected-image-tag", action="append", default=[], required=True)
    prune.add_argument("--keep-backups", type=int, default=8)
    prune.add_argument("--keep-manager-releases", type=int, default=6)
    prune.add_argument("--keep-release-images", type=int, default=6)
    prune.add_argument("--keep-rollback-images", type=int, default=4)

    cleanup = subparsers.add_parser("cleanup-attempt")
    cleanup.add_argument("--manager-release-root", type=Path, required=True)
    cleanup.add_argument("--release-id", required=True)
    cleanup.add_argument("--manager-revision", required=True)
    cleanup.add_argument("--owned-manager-path", type=Path, action="append", default=[])
    cleanup.add_argument("--protected-manager-path", type=Path, action="append", default=[])
    cleanup.add_argument("--owned-image-tag", nargs=2, action="append", default=[])
    cleanup.add_argument("--restore-image-tag", nargs=3, action="append", default=[])
    cleanup.add_argument("--protected-image-tag", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "cleanup-attempt":
            result = cleanup_owned_attempt_artifacts(
                manager_release_root=args.manager_release_root,
                release_id=args.release_id,
                manager_revision=args.manager_revision,
                owned_manager_paths=args.owned_manager_path,
                protected_manager_paths=args.protected_manager_path,
                owned_image_tags=[tuple(item) for item in args.owned_image_tag],
                restored_image_tags=[tuple(item) for item in args.restore_image_tag],
                protected_image_tags=args.protected_image_tag,
            )
        else:
            result = prune_release_artifacts(
                backup_root=args.backup_root,
                manager_release_root=args.manager_release_root,
                protected_backup=args.protected_backup,
                protected_manager_releases=args.protected_manager_release,
                protected_image_tags=args.protected_image_tag,
                keep_backups=args.keep_backups,
                keep_manager_releases=args.keep_manager_releases,
                keep_release_images=args.keep_release_images,
                keep_rollback_images=args.keep_rollback_images,
            )
    except (
        RetentionError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
