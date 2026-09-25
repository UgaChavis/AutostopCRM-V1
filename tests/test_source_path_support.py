from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests import source_path_support
else:
    import source_path_support


class SourcePathSupportTests(unittest.TestCase):
    def test_ensure_repository_root_path_inserts_once_without_reordering(self) -> None:
        repository_root = str(Path(source_path_support.__file__).resolve().parents[1])
        scenarios = (
            ([], [repository_root]),
            (["existing-path", repository_root], ["existing-path", repository_root]),
        )
        for paths, expected in scenarios:
            with self.subTest(paths=paths):
                with patch.object(source_path_support.sys, "path", paths):
                    source_path_support.ensure_repository_root_path()
                    source_path_support.ensure_repository_root_path()
                self.assertEqual(expected, paths)

    def test_ensure_source_path_inserts_once_without_reordering(self) -> None:
        source_path = str(Path(source_path_support.__file__).resolve().parents[1] / "src")
        scenarios = (
            ([], [source_path]),
            (["existing-path", source_path], ["existing-path", source_path]),
        )
        for paths, expected in scenarios:
            with self.subTest(paths=paths):
                with patch.object(source_path_support.sys, "path", paths):
                    source_path_support.ensure_source_path()
                    source_path_support.ensure_source_path()
                self.assertEqual(expected, paths)

    def test_prepend_source_path_preserves_front_priority_and_duplicate(self) -> None:
        source_path = str(Path(source_path_support.__file__).resolve().parents[1] / "src")
        paths = [source_path, "existing-path"]

        with patch.object(source_path_support.sys, "path", paths):
            source_path_support.prepend_source_path()

        self.assertEqual([source_path, source_path, "existing-path"], paths)

    def test_prepend_scripts_path_preserves_front_priority_and_duplicate(self) -> None:
        scripts_path = str(Path(source_path_support.__file__).resolve().parents[1] / "scripts")
        paths = [scripts_path, "existing-path"]

        with patch.object(source_path_support.sys, "path", paths):
            source_path_support.prepend_scripts_path()

        self.assertEqual([scripts_path, scripts_path, "existing-path"], paths)

    def test_ensure_scripts_path_inserts_only_when_missing(self) -> None:
        scripts_path = str(Path(source_path_support.__file__).resolve().parents[1] / "scripts")
        scenarios = (
            ([], [scripts_path]),
            (["existing-path", scripts_path], ["existing-path", scripts_path]),
        )
        for paths, expected in scenarios:
            with self.subTest(paths=paths):
                with patch.object(source_path_support.sys, "path", paths):
                    source_path_support.ensure_scripts_path()
                    source_path_support.ensure_scripts_path()
                self.assertEqual(expected, paths)


if __name__ == "__main__":
    unittest.main()
