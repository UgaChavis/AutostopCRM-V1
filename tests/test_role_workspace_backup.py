"""Role evidence is bounded and cannot follow links or overwrite live journals."""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban import deployment_role_workspace as workspace  # noqa: E402
from tests.test_agent_release_backup import symlink_or_skip  # noqa: E402


class RoleWorkspaceBackupTests(unittest.TestCase):
    def test_snapshot_round_trip_reads_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "M2"
            (source / "journal").mkdir(parents=True)
            (source / "journal" / "2026-W40.jsonl").write_text("Technical log.\n", encoding="utf-8")
            archive = root / "snapshot.tar.gz"
            self.assertTrue(workspace.snapshot_workspace(source, archive))
            workspace.verify_workspace_snapshot(archive)
            with tarfile.open(archive) as saved:
                member = saved.getmember("journal/2026-W40.jsonl")
                self.assertEqual(saved.extractfile(member).read(), b"Technical log.\n")

    def test_empty_workspace_and_absent_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "M2"
            archive = root / "snapshot.tar.gz"
            self.assertFalse(workspace.snapshot_workspace(source, archive))
            self.assertFalse(archive.exists())
            source.mkdir()
            self.assertTrue(workspace.snapshot_workspace(source, archive))
            workspace.verify_workspace_snapshot(archive)

    def test_root_and_directory_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "M2"
            source.mkdir()
            symlink_or_skip(self, root / "linked-role", source)
            with self.assertRaises(workspace.RoleWorkspaceError):
                workspace.snapshot_workspace(root / "linked-role", root / "snapshot.tar.gz")
            symlink_or_skip(self, source / "nested", root)
            with self.assertRaises(workspace.RoleWorkspaceError):
                workspace.snapshot_workspace(source, root / "snapshot.tar.gz")

    def test_file_count_file_size_and_total_size_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "M2"
            source.mkdir()
            (source / "a.md").write_bytes(b"abcd")
            (source / "b.md").write_bytes(b"abcd")
            for option, limit in (("MAX_FILES", 1), ("MAX_FILE_BYTES", 3), ("MAX_TOTAL_BYTES", 7)):
                with self.subTest(option=option), patch.object(workspace, option, limit):
                    with self.assertRaises(workspace.RoleWorkspaceError):
                        workspace.snapshot_workspace(source, root / "snapshot.tar.gz")

    def test_changed_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "record.md"
            source.write_bytes(b"original")
            real_open = os.open

            def changing_open(path, flags):
                source.write_bytes(b"updated after inventory")
                return real_open(path, flags)

            with patch.object(workspace.os, "open", side_effect=changing_open):
                with self.assertRaisesRegex(workspace.RoleWorkspaceError, "changed while reading"):
                    workspace._regular_file_bytes(source)

    def test_unsafe_archive_members_are_rejected_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "snapshot.tar.gz"
            for name in ("../outside.md", "/absolute.md", "a/../b.md", "a\\b.md", "C:record.md"):
                with self.subTest(name=name):
                    with tarfile.open(archive, "w:gz") as saved:
                        member = tarfile.TarInfo(name)
                        member.size = 1
                        saved.addfile(member, io.BytesIO(b"x"))
                    with self.assertRaises(workspace.RoleWorkspaceError):
                        workspace.verify_workspace_snapshot(archive)
            with tarfile.open(archive, "w:gz") as saved:
                member = tarfile.TarInfo("linked.md")
                member.type = tarfile.SYMTYPE
                member.linkname = "outside.md"
                saved.addfile(member)
            with self.assertRaises(workspace.RoleWorkspaceError):
                workspace.verify_workspace_snapshot(archive)

    def test_duplicate_and_oversized_archives_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "snapshot.tar.gz"
            with tarfile.open(archive, "w:gz") as saved:
                for _ in range(2):
                    member = tarfile.TarInfo("record.md")
                    member.size = 1
                    saved.addfile(member, io.BytesIO(b"x"))
            with self.assertRaises(workspace.RoleWorkspaceError):
                workspace.verify_workspace_snapshot(archive)
            with patch.object(workspace, "MAX_ARCHIVE_BYTES", 1):
                with self.assertRaises(workspace.RoleWorkspaceError):
                    workspace.verify_workspace_snapshot(archive)

    def test_invalid_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "snapshot.tar.gz"
            archive.write_bytes(b"invalid gzip")
            with self.assertRaises(workspace.RoleWorkspaceError):
                workspace.verify_workspace_snapshot(archive)


if __name__ == "__main__":
    unittest.main()
