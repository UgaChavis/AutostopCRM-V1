from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path, prepend_scripts_path
else:
    from source_path_support import ensure_source_path, prepend_scripts_path

ensure_source_path()
prepend_scripts_path()

from manager_structure_edit import build_parser, edit, instruction_text  # noqa: E402

from minimal_kanban.services.manager_structure import ManagerStructureService  # noqa: E402


class LocalClient:
    def __init__(self, file: Path) -> None:
        self.service = ManagerStructureService(file)
        self.calls = 0

    def read(self) -> dict:
        return self.service.read()

    def apply(self, version: int, operation: str, **fields) -> dict:
        self.calls += 1
        return self.service.apply(
            {
                "expected_version": version,
                "idempotency_key": f"test-edit-{self.calls:04d}",
                "operation": operation,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
                **fields,
            }
        )


class ManagerStructureEditTests(unittest.TestCase):
    def setUp(self) -> None:
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        self.addCleanup(owner.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = LocalClient(Path(self.temp.name) / "manager_structure.json")
        self.parser = build_parser()

    def run_edit(self, *arguments: str) -> str:
        return edit(self.client, self.parser.parse_args(list(arguments)))

    def test_module_instruction_file_and_relation_are_saved_with_readback(self) -> None:
        self.run_edit("module", "M1", "--title", "Менеджер", "--x", "40", "--y", "50")
        self.run_edit("module", "M2", "--title", "Заявка", "--x", "450", "--y", "240")
        path = Path(self.temp.name) / "instruction.md"
        path.write_text("# Инструкция\nТочный текст.\n", encoding="utf-8")
        confirmation = self.run_edit("instruction", "M1", "--file", str(path))
        self.assertIn("инструкция сохранена", confirmation)
        self.run_edit("relation", "L1", "--from", "M1", "--to", "M2", "--label", "Заявка")
        snapshot = self.client.read()
        self.assertEqual(snapshot["version"], 4)
        self.assertEqual(snapshot["elements"][0]["instruction"], "# Инструкция\nТочный текст.\n")
        self.assertEqual(snapshot["relations"][0]["label"], "Заявка")
        self.assertTrue(snapshot["relations"][0]["path"].startswith("M"))
        initial_route = snapshot["relations"][0]["path"]
        self.run_edit("module", "M2", "--x", "500")
        self.assertNotEqual(self.client.read()["relations"][0]["path"], initial_route)
        self.assertNotIn("Точный текст", self.run_edit("list"))

    def test_invalid_file_or_missing_endpoint_does_not_write(self) -> None:
        self.run_edit("module", "M1", "--title", "Менеджер", "--x", "40", "--y", "50")
        path = Path(self.temp.name) / "instruction.pdf"
        path.write_text("private", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, ".txt или .md"):
            instruction_text(path)
        with self.assertRaisesRegex(ValueError, "не найден"):
            self.run_edit("relation", "L1", "--from", "M1", "--to", "M9")
        self.assertEqual(self.client.read()["version"], 1)


if __name__ == "__main__":
    unittest.main()
