"""Signed-envelope protocol tests using only disposable local review keys."""

from __future__ import annotations

import base64
import copy
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_proofs as proofs
from scripts import j1_forward_state as state


def _openssl():
    candidates = [shutil.which("openssl")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\mingw64\bin\openssl.exe",
                r"C:\Program Files\Git\usr\bin\openssl.exe",
            ]
        )
    return next((path for path in candidates if path and Path(path).is_file()), None)


class ForwardProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openssl = _openssl()
        cls.private_key = None
        cls.public_key = (
            b"-----BEGIN PUBLIC KEY-----\n"
            + base64.b64encode(proofs.ED25519_SPKI_PREFIX + b"x" * 32)
            + b"\n-----END PUBLIC KEY-----\n"
        )
        if cls.openssl:
            with tempfile.TemporaryDirectory() as temporary:
                private_path = Path(temporary) / "disposable-private.pem"
                public_path = Path(temporary) / "disposable-public.pem"
                try:
                    for argv in (
                        [
                            cls.openssl,
                            "genpkey",
                            "-algorithm",
                            "ED25519",
                            "-out",
                            str(private_path),
                        ],
                        [
                            cls.openssl,
                            "pkey",
                            "-in",
                            str(private_path),
                            "-pubout",
                            "-out",
                            str(public_path),
                        ],
                    ):
                        subprocess.run(
                            argv,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=10,
                            check=True,
                        )
                    cls.private_key = private_path.read_bytes()
                    cls.public_key = public_path.read_bytes()
                except (OSError, subprocess.SubprocessError):
                    cls.private_key = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = datetime(2026, 10, 19, 1, tzinfo=UTC)
        self.plan = {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": "synthetic.example",
            "attempt_id": "f458ea53-a673-4e74-ad42-24cfd6b19107",
            "sequence": 1,
            "predecessor_sha256": "1" * 64,
            "base_sha256": "1" * 64,
            "candidate": {
                "revision": "2" * 40,
                "tree": "3" * 40,
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": "5" * 64,
            },
            "baseline_ref": {"path": str(self.root / "baseline.json"), "sha256": "6" * 64},
            "created_at": (self.now - timedelta(seconds=30)).isoformat(),
            "deadline_utc": (self.now + timedelta(minutes=5)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        self.plan_ref = self.write("plan.json", io.canonical(self.plan))
        self.key_ref = self.write("reviewer.pem", self.public_key)
        if self.private_key:
            self.write("disposable-private.pem", self.private_key)
        for replacement in (
            patch.object(proofs, "REVIEW_KEY_PATH", self.key_ref["path"]),
            patch.object(io, "protected_directory", self.physical_directory),
            patch.object(io, "ROOT_UID", getattr(os, "getuid", lambda: 0)()),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        if self.openssl:
            replacement = patch.object(proofs, "OPENSSL", self.openssl)
            replacement.start()
            self.addCleanup(replacement.stop)
        if os.name == "nt":
            replacement = patch.object(io, "read_protected", self.windows_read)
            replacement.start()
            self.addCleanup(replacement.stop)

    @staticmethod
    def physical_directory(path, *, private=False):
        path = Path(path)
        io.require(path.is_absolute() and ".." not in path.parts, "fixture_physical_path")
        for part in (path, *path.parents):
            info = part.lstat()
            io.require(
                stat.S_ISDIR(info.st_mode)
                and not part.is_symlink()
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "fixture_physical_directory",
            )
        if private and os.name != "nt":
            io.require(stat.S_IMODE(path.stat().st_mode) == 0o700, "fixture_private_directory")
        return path

    @classmethod
    def windows_read(cls, path, limit=2 * 1024**2):
        path = Path(path)
        cls.physical_directory(path.parent)
        before = path.lstat()
        io.require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not path.is_symlink()
            and not getattr(before, "st_file_attributes", 0) & 0x400
            and before.st_size <= limit,
            "fixture_protected_file",
        )
        raw = path.read_bytes()
        after = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        io.require(
            len(raw) == before.st_size
            and all(getattr(before, key) == getattr(after, key) for key in fields),
            "fixture_file_changed",
        )
        return raw

    def write(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        path.chmod(0o600)
        return {"path": str(path), "sha256": io.sha(raw)}

    def envelope(self, kind="offhost_restore"):
        return {
            "schema": proofs.SCHEMA,
            "kind": kind,
            "attempt_id": self.plan["attempt_id"],
            "plan_sha256": self.plan_ref["sha256"],
            "observed_at": self.now.isoformat(),
            "key_sha256": self.key_ref["sha256"],
            "payload": {"schema": "synthetic.restore.v1", "checked_items": ["database", "media"]},
            "signature_base64": base64.b64encode(b"\0" * 64).decode("ascii"),
        }

    def proof_ref(self, envelope):
        return self.write("proof.json", io.canonical(envelope))

    def verify(self, envelope, *, kind="offhost_restore", now=None):
        return proofs.verify_proof(
            self.proof_ref(envelope),
            kind,
            self.plan_ref,
            self.key_ref,
            now=self.now if now is None else now,
        )

    def sign(self, envelope):
        if not self.private_key:
            self.skipTest("An Ed25519-capable local OpenSSL is unavailable")
        signed = copy.deepcopy(envelope)
        body = io.canonical(
            {key: value for key, value in signed.items() if key != "signature_base64"}
        )
        self.write("sign-body.json", body)
        signature_path = self.root / "sign-signature.bin"
        subprocess.run(
            [
                self.openssl,
                "pkeyutl",
                "-sign",
                "-inkey",
                str(self.root / "disposable-private.pem"),
                "-rawin",
                "-in",
                str(self.root / "sign-body.json"),
                "-out",
                str(signature_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
        )
        signed["signature_base64"] = base64.b64encode(signature_path.read_bytes()).decode("ascii")
        return signed

    def assert_no_temporary_inputs(self):
        self.assertEqual(list(self.root.glob(".j1-proof-*")), [])

    def test_actual_ed25519_accepts_all_supported_kinds_and_cleans_inputs(self):
        for kind in sorted(proofs.KINDS):
            with self.subTest(kind=kind):
                envelope = self.sign(self.envelope(kind))
                self.assertEqual(self.verify(envelope, kind=kind), envelope["payload"])
                self.assert_no_temporary_inputs()

    def test_actual_ed25519_refuses_tampered_body_and_signature(self):
        signed = self.sign(self.envelope())
        altered_body = copy.deepcopy(signed)
        altered_body["payload"]["checked_items"].append("invented")
        altered_signature = copy.deepcopy(signed)
        signature = bytearray(base64.b64decode(signed["signature_base64"]))
        signature[0] ^= 1
        altered_signature["signature_base64"] = base64.b64encode(signature).decode("ascii")
        for envelope in (altered_body, altered_signature):
            with self.assertRaisesRegex(io.Refusal, "proof_signature_invalid"):
                self.verify(envelope)
            self.assert_no_temporary_inputs()

    def test_actual_ed25519_does_not_trust_unsigned_accepted_flag(self):
        if not self.private_key:
            self.skipTest("An Ed25519-capable local OpenSSL is unavailable")
        envelope = self.envelope()
        envelope["payload"] = {"accepted": True}
        with self.assertRaisesRegex(io.Refusal, "proof_signature_invalid"):
            self.verify(envelope)

    def test_plan_attempt_kind_and_key_binding_cannot_be_replayed(self):
        mutations = [
            ("attempt_id", "9df88c7f-2cba-49ba-b292-f2ddac62fd24"),
            ("plan_sha256", "a" * 64),
            ("kind", "native_acceptance"),
            ("key_sha256", "b" * 64),
        ]
        with patch.object(proofs.subprocess, "run") as runner:
            for key, value in mutations:
                envelope = self.envelope()
                envelope[key] = value
                with self.subTest(field=key):
                    with self.assertRaisesRegex(io.Refusal, "proof_binding_mismatch"):
                        self.verify(envelope)
            runner.assert_not_called()

    def test_unknown_missing_fields_and_non_object_payload_are_rejected(self):
        changes = [
            lambda value: value.update(accepted=True),
            lambda value: value.pop("signature_base64"),
            lambda value: value.update(schema="other"),
            lambda value: value.update(payload=[{"accepted": True}]),
        ]
        with patch.object(proofs.subprocess, "run") as runner:
            for change in changes:
                envelope = self.envelope()
                change(envelope)
                with self.assertRaises(io.Refusal):
                    self.verify(envelope)
            runner.assert_not_called()

    def test_duplicate_keys_and_nonfinite_json_are_rejected_before_verification(self):
        samples = [
            b'{"schema":"first","schema":"second"}',
            b'{"payload":{"nested":NaN}}',
            b'{"payload":{"nested":Infinity}}',
            b'{"payload":{"same":1,"same":2}}',
        ]
        with patch.object(proofs.subprocess, "run") as runner:
            for raw in samples:
                reference = self.write("proof.json", raw)
                with self.assertRaises(io.Refusal):
                    proofs.verify_proof(
                        reference, "offhost_restore", self.plan_ref, self.key_ref, now=self.now
                    )
            runner.assert_not_called()

    def test_plan_is_strict_and_hash_bound(self):
        for mutation in (
            lambda value: value.update(extra=True),
            lambda value: value.update(sequence=True),
        ):
            plan = copy.deepcopy(self.plan)
            mutation(plan)
            self.plan_ref = self.write("plan.json", io.canonical(plan))
            with self.assertRaises(io.Refusal):
                self.verify(self.envelope())
        self.plan_ref = self.write("plan.json", io.canonical(self.plan))
        Path(self.plan_ref["path"]).write_bytes(
            io.canonical({**self.plan, "host": "changed.example"})
        )
        with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
            self.verify(self.envelope())

    def test_proof_and_key_references_are_hash_bound(self):
        reference = self.proof_ref(self.envelope())
        reference["sha256"] = "f" * 64
        with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
            proofs.verify_proof(
                reference, "offhost_restore", self.plan_ref, self.key_ref, now=self.now
            )
        Path(self.key_ref["path"]).write_bytes(self.public_key + b"\n")
        with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
            self.verify(self.envelope())

    def test_reviewer_path_is_fixed_even_for_an_identical_public_key(self):
        other = self.write("alternative-reviewer.pem", self.public_key)
        with self.assertRaisesRegex(io.Refusal, "proof_key_path_invalid"):
            proofs.verify_proof(
                self.proof_ref(self.envelope()),
                "offhost_restore",
                self.plan_ref,
                other,
                now=self.now,
            )

    def test_observation_requires_utc_plan_window_and_at_most_five_seconds_future(self):
        for observed in (
            (self.now - timedelta(minutes=1)).isoformat(),
            (self.now + timedelta(minutes=6)).isoformat(),
            (self.now + timedelta(seconds=5, microseconds=1)).isoformat(),
            "2026-10-19T01:00:00",
            "2026-10-19T08:00:00+07:00",
            True,
        ):
            envelope = self.envelope()
            envelope["observed_at"] = observed
            with self.subTest(observed=observed):
                with self.assertRaises(io.Refusal):
                    self.verify(envelope)
        with patch.object(
            proofs.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ):
            for observed in (
                self.plan["created_at"],
                (self.now + timedelta(seconds=5)).isoformat(),
            ):
                envelope = self.envelope()
                envelope["observed_at"] = observed
                self.assertEqual(self.verify(envelope), envelope["payload"])

    def test_expired_deadline_naive_now_and_unknown_kind_are_rejected(self):
        with patch.object(proofs.subprocess, "run") as runner:
            with self.assertRaisesRegex(io.Refusal, "proof_deadline_expired"):
                self.verify(self.envelope(), now=self.now + timedelta(minutes=6))
            with self.assertRaisesRegex(io.Refusal, "proof_now_utc_required"):
                self.verify(self.envelope(), now=self.now.replace(tzinfo=None))
            with self.assertRaisesRegex(io.Refusal, "proof_kind_invalid"):
                self.verify(self.envelope(), kind="backup")
            runner.assert_not_called()

    def test_signature_has_strict_canonical_base64_and_exact_ed25519_length(self):
        valid = self.envelope()["signature_base64"]
        noncanonical = valid[:-3] + "B=="
        self.assertEqual(base64.b64decode(noncanonical), base64.b64decode(valid))
        for value in (
            "",
            valid + "\n",
            base64.b64encode(b"x" * 63).decode(),
            noncanonical,
            "!" * 88,
        ):
            envelope = self.envelope()
            envelope["signature_base64"] = value
            with self.assertRaisesRegex(io.Refusal, "proof_signature_encoding"):
                self.verify(envelope)

    def test_public_key_must_be_ed25519_spki_and_never_a_private_key(self):
        for raw in (
            b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
            b"-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n",
            self.public_key + b"arbitrary trailer",
        ):
            self.key_ref = self.write("reviewer.pem", raw)
            with self.assertRaisesRegex(io.Refusal, "proof_public_key"):
                self.verify(self.envelope())

    def test_verifier_has_fixed_argv_private_inputs_minimal_environment_and_cleanup(self):
        envelope = self.envelope()

        def inspect(argv, **kwargs):
            self.assertEqual(
                argv[:7],
                [
                    proofs.OPENSSL,
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    self.key_ref["path"],
                    "-rawin",
                ],
            )
            body = Path(argv[8])
            signature = Path(argv[10])
            self.assertEqual(body.parent, signature.parent)
            self.assertEqual(body.parent.parent, self.root)
            self.assertTrue(body.parent.name.startswith(".j1-proof-"))
            self.assertEqual(
                io.document(body.read_bytes()),
                {key: value for key, value in envelope.items() if key != "signature_base64"},
            )
            self.assertEqual(signature.read_bytes(), b"\0" * 64)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(body.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(body.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(signature.stat().st_mode), 0o600)
            self.assertEqual(
                kwargs["env"], {"LANG": "C", "LC_ALL": "C", "OPENSSL_CONF": os.devnull}
            )
            self.assertIs(kwargs["shell"], False)
            self.assertLessEqual(kwargs["timeout"], 10)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(proofs.subprocess, "run", side_effect=inspect):
            self.assertEqual(self.verify(envelope), envelope["payload"])
        self.assert_no_temporary_inputs()

    def test_timeout_failure_and_unavailable_verifier_cleanup_without_output_leaks(self):
        cases = [
            (
                subprocess.TimeoutExpired("sensitive-command", 10, output=b"secret output"),
                "proof_verifier_timeout",
            ),
            (OSError("secret operating-system detail"), "proof_verifier_unavailable"),
            (
                subprocess.CompletedProcess([], 1, stdout=b"secret output"),
                "proof_signature_invalid",
            ),
        ]
        for outcome, code in cases:
            settings = (
                {"side_effect": outcome}
                if isinstance(outcome, Exception)
                else {"return_value": outcome}
            )
            with patch.object(proofs.subprocess, "run", **settings):
                with self.assertRaisesRegex(io.Refusal, f"^{code}$"):
                    self.verify(self.envelope())
            self.assert_no_temporary_inputs()

    def test_replaced_key_during_verification_is_rejected(self):
        def replace_key(*_args, **_kwargs):
            Path(self.key_ref["path"]).write_bytes(self.public_key + b"\n")
            return subprocess.CompletedProcess([], 0)

        with patch.object(proofs.subprocess, "run", side_effect=replace_key):
            with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
                self.verify(self.envelope())
        self.assert_no_temporary_inputs()

    def test_verification_cannot_finish_after_the_plan_deadline(self):
        self.plan["deadline_utc"] = (self.now + timedelta(seconds=0.5)).isoformat()
        self.plan_ref = self.write("plan.json", io.canonical(self.plan))

        def delayed(*_args, **kwargs):
            self.assertEqual(kwargs["timeout"], 0.5)
            return subprocess.CompletedProcess([], 0)

        with patch.object(proofs.subprocess, "run", side_effect=delayed) as runner:
            with patch.object(proofs.time, "monotonic", side_effect=[100.0, 100.0, 101.0]):
                with self.assertRaisesRegex(io.Refusal, "proof_deadline_expired"):
                    self.verify(self.envelope())
            runner.assert_called_once()
        self.assert_no_temporary_inputs()

    def test_proof_with_invalid_unicode_cannot_reach_the_verifier(self):
        envelope = self.envelope()
        envelope["payload"] = {"text": "placeholder"}
        raw = io.canonical(envelope).replace(b'"placeholder"', b'"\\ud800"')
        ref = self.write("proof.json", raw)
        with patch.object(proofs.subprocess, "run") as runner:
            with self.assertRaisesRegex(io.Refusal, "proof_canonical_invalid"):
                proofs.verify_proof(
                    ref, "offhost_restore", self.plan_ref, self.key_ref, now=self.now
                )
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
