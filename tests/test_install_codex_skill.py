from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.install_codex_skill import SKILL_NAMES, manifest, synchronize


class InstallCodexSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("versioned CRM skill", encoding="utf-8")
        self.skills = self.root / "skills"

    def seed_installed(self) -> dict[str, dict[str, str]]:
        for name in (*SKILL_NAMES, "other-project"):
            directory = self.skills / name
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(name, encoding="utf-8")
        return {path.name: manifest(path) for path in self.skills.iterdir()}

    def test_check_is_read_only_for_missing_or_superseded_installations(self) -> None:
        self.assertFalse(synchronize(self.source, self.skills)["current"])
        self.assertFalse(self.skills.exists())
        before = self.seed_installed()
        self.assertFalse(synchronize(self.source, self.skills)["current"])
        self.assertEqual(before, {path.name: manifest(path) for path in self.skills.iterdir()})
        self.assertFalse((self.root / "skill-backups").exists())

    def test_install_preserves_all_replaced_packages_and_is_idempotent(self) -> None:
        before = self.seed_installed()
        result = synchronize(self.source, self.skills, apply=True)
        backup = Path(str(result["backup"]))

        self.assertTrue(result["current"])
        self.assertEqual(manifest(self.source), manifest(self.skills / SKILL_NAMES[0]))
        self.assertEqual(
            {name: before[name] for name in SKILL_NAMES},
            {path.name: manifest(path) for path in backup.iterdir()},
        )
        self.assertEqual(before["other-project"], manifest(self.skills / "other-project"))
        self.assertEqual(
            {SKILL_NAMES[0], "other-project"}, {path.name for path in self.skills.iterdir()}
        )
        repeated = synchronize(self.source, self.skills, apply=True)
        self.assertTrue(repeated["current"])
        self.assertNotIn("backup", repeated)
        self.assertEqual(1, len(list((self.root / "skill-backups").iterdir())))

    def test_failed_publication_restores_installed_packages(self) -> None:
        before = self.seed_installed()
        original = Path.replace

        def fail_publication(path: Path, target: Path) -> Path:
            if path.name.startswith(".autostopcrm-install-"):
                raise OSError("publication failed")
            return original(path, target)

        with patch.object(Path, "replace", fail_publication):
            with self.assertRaisesRegex(OSError, "publication failed"):
                synchronize(self.source, self.skills, apply=True)
        self.assertEqual(before, {path.name: manifest(path) for path in self.skills.iterdir()})

    def test_linked_skill_is_rejected_before_other_packages_move(self) -> None:
        before = self.seed_installed()
        linked = self.skills / SKILL_NAMES[1]
        with patch.object(Path, "is_junction", lambda path: path == linked):
            with self.assertRaisesRegex(ValueError, "cannot be links"):
                synchronize(self.source, self.skills, apply=True)
        self.assertFalse((self.root / "skill-backups").exists())
        self.assertEqual(before, {path.name: manifest(path) for path in self.skills.iterdir()})


if __name__ == "__main__":
    unittest.main()
