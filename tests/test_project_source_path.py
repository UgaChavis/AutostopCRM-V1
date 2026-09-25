from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_repository_root_path
else:
    from source_path_support import ensure_repository_root_path

ensure_repository_root_path()

from project_source_path import add_project_source_path  # noqa: E402


class ProjectSourcePathTests(unittest.TestCase):
    def test_adds_source_path_once(self) -> None:
        source_path = str(Path("project") / "src")

        with patch.object(sys, "path", ["existing"]):
            add_project_source_path(Path("project"))
            add_project_source_path(Path("project"))

            self.assertEqual(sys.path, [source_path, "existing"])

    def test_keeps_existing_source_path_position(self) -> None:
        source_path = str(Path("project") / "src")
        original_path = ["existing", source_path, "later"]

        with patch.object(sys, "path", original_path):
            add_project_source_path(Path("project"))

            self.assertEqual(sys.path, original_path)


if __name__ == "__main__":
    unittest.main()
