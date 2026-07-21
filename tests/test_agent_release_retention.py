from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "agent_release_retention.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agent_release_retention", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("agent_release_retention.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AgentReleaseRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    @staticmethod
    def _release_id(index: int) -> str:
        return f"202607{index:02d}T120000Z-{'a' * 12}-{index}"

    def test_post_success_prune_is_bounded_and_protects_exact_release_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_root = root / "release-backups"
            manager_root = root / "manager-releases"
            backup_root.mkdir()
            manager_root.mkdir()
            backups: list[Path] = []
            managers: list[Path] = []
            for index in range(1, 6):
                release_id = self._release_id(index)
                backup = backup_root / release_id
                backup.mkdir()
                (backup / "manifest.json").write_text("{}", encoding="utf-8")
                backups.append(backup)
                manager = manager_root / f"{release_id}-manager-{'b' * 12}"
                (manager / "autostop_manager").mkdir(parents=True)
                managers.append(manager)

            docker_rows = [
                {
                    "Repository": "autostopcrm",
                    "Tag": f"{'c' * 11}{index}",
                    "CreatedAt": f"2026-07-{index:02d} 12:00:00 +0000 UTC",
                }
                for index in range(1, 5)
            ]
            docker_rows.extend(
                {
                    "Repository": "autostopcrm-rollback",
                    "Tag": self._release_id(index),
                    "CreatedAt": f"2026-07-{index:02d} 12:00:00 +0000 UTC",
                }
                for index in range(1, 4)
            )
            removed_tags: list[str] = []

            def fake_run(arguments, **_kwargs):
                removed_tags.append(arguments[-1])
                return object()

            protected_release_tag = f"autostopcrm:{'c' * 11}1"
            protected_rollback_tag = f"autostopcrm-rollback:{self._release_id(1)}"
            with (
                patch.object(self.module, "_docker_image_rows", return_value=docker_rows),
                patch.object(self.module.subprocess, "run", side_effect=fake_run),
            ):
                result = self.module.prune_release_artifacts(
                    backup_root=backup_root,
                    manager_release_root=manager_root,
                    protected_backup=backups[0],
                    protected_manager_releases=(managers[0], managers[-1]),
                    protected_image_tags=(
                        protected_release_tag,
                        protected_rollback_tag,
                        "autostopcrm-autostopcrm:latest",
                    ),
                    keep_backups=2,
                    keep_manager_releases=2,
                    keep_release_images=2,
                    keep_rollback_images=2,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(backups[0].is_dir())
            self.assertTrue(backups[-1].is_dir())
            self.assertEqual(2, sum(path.is_dir() for path in backups))
            self.assertTrue(managers[0].is_dir())
            self.assertTrue(managers[-1].is_dir())
            self.assertEqual(2, sum(path.is_dir() for path in managers))
            self.assertNotIn(protected_release_tag, removed_tags)
            self.assertNotIn(protected_rollback_tag, removed_tags)
            self.assertEqual(
                2, len([tag for tag in removed_tags if tag.startswith("autostopcrm:c")])
            )
            self.assertEqual(
                1,
                len([tag for tag in removed_tags if tag.startswith("autostopcrm-rollback:")]),
            )

    def test_filesystem_plan_rejects_symlinks_mountpoints_and_device_crossing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "release-backups"
            root.mkdir()
            release_id = self._release_id(1)
            external = Path(temp_dir) / "external"
            external.mkdir()
            try:
                (root / release_id).symlink_to(external, target_is_directory=True)
            except OSError as exc:
                if sys.platform == "win32" and exc.winerror == 1314:
                    self.skipTest("directory symlinks require Developer Mode or elevation")
                raise
            with self.assertRaisesRegex(self.module.RetentionError, "removable directory"):
                self.module._filesystem_prune_plan(
                    root=root,
                    name_pattern=self.module.BACKUP_NAME_PATTERN,
                    marker_path="manifest.json",
                    protected=(),
                    keep=2,
                )

            (root / release_id).unlink()
            candidate = root / release_id
            candidate.mkdir()
            (candidate / "manifest.json").write_text("{}", encoding="utf-8")
            with patch.object(self.module.os.path, "ismount", return_value=True):
                with self.assertRaisesRegex(self.module.RetentionError, "removable directory"):
                    self.module._filesystem_prune_plan(
                        root=root,
                        name_pattern=self.module.BACKUP_NAME_PATTERN,
                        marker_path="manifest.json",
                        protected=(),
                        keep=2,
                    )

            with self.assertRaisesRegex(self.module.RetentionError, "filesystem"):
                self.module._validate_candidate_tree(
                    candidate,
                    root_device=candidate.stat().st_dev + 1,
                )

            canonical_parent = Path(temp_dir) / "canonical-parent"
            canonical_parent.mkdir()
            canonical_root = canonical_parent / "root"
            canonical_root.mkdir()
            alias_parent = Path(temp_dir) / "alias-parent"
            alias_parent.symlink_to(canonical_parent, target_is_directory=True)
            with self.assertRaisesRegex(self.module.RetentionError, "already be canonical"):
                self.module._validated_root(alias_parent / "root")

    def test_docker_plan_accepts_only_exact_bounded_tag_families(self) -> None:
        rows = [
            {
                "Repository": "autostopcrm",
                "Tag": "a" * 12,
                "CreatedAt": "2026-07-01",
            },
            {
                "Repository": "autostopcrm",
                "Tag": "b" * 12,
                "CreatedAt": "2026-07-02",
            },
            {
                "Repository": "unrelated",
                "Tag": "a" * 12,
                "CreatedAt": "2026-07-03",
            },
        ]
        plan = self.module._docker_prune_plan(
            rows,
            pattern=self.module.CRM_RELEASE_TAG_PATTERN,
            protected=(f"autostopcrm:{'a' * 12}",),
            keep=1,
        )
        self.assertEqual([f"autostopcrm:{'b' * 12}"], plan)
        with self.assertRaisesRegex(self.module.RetentionError, "invalid Docker reference"):
            self.module._docker_prune_plan(
                rows,
                pattern=self.module.CRM_RELEASE_TAG_PATTERN,
                protected=("autostopcrm:bad tag",),
                keep=1,
            )

    def test_attempt_cleanup_removes_only_exact_owned_paths_and_restores_prior_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager_root = root / "manager-releases"
            manager_root.mkdir()
            release_id = self._release_id(1)
            manager_revision = "b" * 40
            final_path = manager_root / f"{release_id}-manager-{manager_revision[:12]}"
            staging_path = manager_root / f"{final_path.name}.partial-1"
            (final_path / "autostop_manager").mkdir(parents=True)
            staging_path.mkdir()
            previous_manager = root / "previous-manager"
            (previous_manager / "autostop_manager").mkdir(parents=True)

            previous_image_id = "sha256:" + "1" * 64
            candidate_image_id = "sha256:" + "2" * 64
            release_tag = "autostopcrm:" + "a" * 12
            rollback_tag = f"autostopcrm-rollback:{release_id}"
            images = {
                previous_image_id: previous_image_id,
                release_tag: candidate_image_id,
                rollback_tag: previous_image_id,
            }

            def fake_image_id(reference: str):
                return images.get(reference)

            def fake_run(arguments, **_kwargs):
                if arguments[1] == "tag":
                    images[arguments[3]] = arguments[2]
                elif arguments[1:3] == ["image", "rm"]:
                    images.pop(arguments[3], None)
                return object()

            with (
                patch.object(self.module, "_docker_image_id", side_effect=fake_image_id),
                patch.object(self.module.subprocess, "run", side_effect=fake_run),
            ):
                result = self.module.cleanup_owned_attempt_artifacts(
                    manager_release_root=manager_root,
                    release_id=release_id,
                    manager_revision=manager_revision,
                    owned_manager_paths=(staging_path, final_path),
                    protected_manager_paths=(previous_manager,),
                    owned_image_tags=((rollback_tag, previous_image_id),),
                    restored_image_tags=((release_tag, candidate_image_id, previous_image_id),),
                    protected_image_tags=("autostopcrm-autostopcrm:latest",),
                )

            self.assertTrue(result["ok"])
            self.assertFalse(staging_path.exists())
            self.assertFalse(final_path.exists())
            self.assertTrue(previous_manager.is_dir())
            self.assertEqual(previous_image_id, images[release_tag])
            self.assertNotIn(rollback_tag, images)
            self.assertEqual([release_tag], result["restored_image_tags"])

            wrong_path = manager_root / "unowned"
            wrong_path.mkdir()
            with self.assertRaisesRegex(self.module.RetentionError, "identity mismatch"):
                self.module.cleanup_owned_attempt_artifacts(
                    manager_release_root=manager_root,
                    release_id=release_id,
                    manager_revision=manager_revision,
                    owned_manager_paths=(wrong_path,),
                    protected_manager_paths=(previous_manager,),
                    owned_image_tags=(),
                    restored_image_tags=(),
                    protected_image_tags=(),
                )


if __name__ == "__main__":
    unittest.main()
