from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_release as release
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


class ColdObservationTests(unittest.TestCase):
    def test_expiry_during_closed_slot_readback_cannot_emit_a_native_proof(self):
        binding = {
            **dict.fromkeys(("attempt_id", "boot_id", "generation"), str(uuid.uuid4())),
            "consumer_sha256": "1" * 64,
            "candidate_crm_sha": "2" * 40,
            "candidate_manager_sha": "3" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": 1,
            "deadline_ns": 100,
        }
        package = {"binding": binding, "policies": {}}
        clock = [99]
        controller = SimpleNamespace(
            verify=lambda: ({"package": package, "history": {"inventory": {}}}, None),
            _closed_slots=lambda: clock.__setitem__(0, 100),
            provider=SimpleNamespace(scope="native_fixture"),
        )
        with patch.object(release.time, "monotonic_ns", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(io.Refusal, "binding_expired_or_window"):
                release._native_observation({}, controller, "closed")


@unittest.skipUnless(os.name == "posix", "Requires real Linux flock, AF_UNIX and SO_PEERCRED")
class ColdExportTests(unittest.TestCase):
    def test_bounded_stream_hashes_before_stdout_and_rejects_bad_digest(self):
        with tempfile.TemporaryDirectory(dir=Path.home(), prefix="cold-export-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            path = root / "delta.tar.gz"
            raw = b"private synthetic envelope" * 150_000
            with rehearsal.PrivateFilesystem(root):
                path.write_bytes(raw)
                path.chmod(0o600)
                stdout = SimpleNamespace(buffer=Mock())
                reference = {"path": str(path), "sha256": io.sha(raw)}
                with patch.object(release.sys, "stdout", stdout):
                    release._export(path, reference, len(raw))
                chunks = [call.args[0] for call in stdout.buffer.write.call_args_list]
                self.assertEqual(b"".join(chunks), raw)
                self.assertGreater(len(chunks), 1)
                self.assertLessEqual(max(map(len, chunks)), 1024**2)
                stdout.buffer.reset_mock()
                with (
                    patch.object(release.sys, "stdout", stdout),
                    self.assertRaisesRegex(io.Refusal, "digest"),
                ):
                    release._export(path, {**reference, "sha256": "0" * 64}, len(raw))
                stdout.buffer.write.assert_not_called()
                with (
                    patch.object(release.sys, "stdout", stdout),
                    self.assertRaisesRegex(io.Refusal, "file"),
                ):
                    release._export(path, reference, len(raw) - 1)
                stdout.buffer.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
