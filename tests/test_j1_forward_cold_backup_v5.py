"""Exact Store exports in disposable files; no PostgreSQL/production claims."""

from __future__ import annotations

import copy
import os
import tempfile
import types
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_backup as original
from scripts import j1_forward_cold_backup as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


def scope():
    attempt, generation = str(uuid.uuid4()), str(uuid.uuid4())
    package = {"path": f"/private/{attempt}/authority.json", "sha256": "1" * 64}
    return "2" * 40, generation, attempt, package


def selector(rule):
    return {
        "schema": cold.STORE_ARTIFACT_SCHEMA,
        "store_prepared_ref": {"path": "/private/prepared.json", "sha256": "3" * 64},
        "backup_ref": {"path": "/private/backup.json", "sha256": "4" * 64},
        "root": rule["root"],
        "files": {
            name: {"file": rule["root"] + "/" + leaf, "sha256": "5" * 64, "bytes": 13}
            for name, leaf in cold.STORE_FILES.items()
        },
    }


class StoreBackupProfileTests(unittest.TestCase):
    def manifest(self):
        revision, generation, attempt, package = scope()
        self.engine = cold.engine(revision, generation, attempt, version=5, package_ref=package)
        profile = self.engine.profile_document()
        selected = selector(profile["store_backup"])
        return {
            "profile": profile,
            "context": {"attempt_id": attempt},
            "store_backup": selected,
            "artifacts": [
                {
                    "component": "store_backup",
                    "source": selected["root"],
                    "logical_name": ".",
                    "kind": "directory",
                },
                *[
                    {
                        "component": "store_backup",
                        "source": value["file"],
                        "logical_name": Path(value["file"]).name,
                        "kind": "file",
                        "sha256": value["sha256"],
                        "bytes": value["bytes"],
                    }
                    for value in selected["files"].values()
                ],
            ],
        }

    def test_all_three_profiles_are_distinct_and_exact_hash_selects_without_source_io(self):
        revision, generation, attempt, package = scope()
        plan = {
            "candidate_manager_sha": revision,
            "attempt_id": attempt,
            "cold_authority_package_ref": package,
            "dispatch_epoch_ref": {
                "path": f"/var/lib/autostop-crm-forward/cold/v1/epochs/{generation}/epoch.json",
                "sha256": "9" * 64,
            },
        }
        engines = [
            cold.engine(
                revision, generation, attempt, version=v, package_ref=package if v > 3 else None
            )
            for v in (3, 4, 5)
        ]
        self.assertEqual(len({engine.profile_sha256() for engine in engines}), 3)
        with patch.object(io, "referenced", side_effect=AssertionError("live IO forbidden")):
            for engine in engines:
                self.assertIs(cold.for_plan(plan, profile_sha256=engine.profile_sha256()), engine)
            with self.assertRaisesRegex(original.BackupRefusal, "cold_backup_profile"):
                cold.for_plan(plan, profile_sha256="f" * 64)
        rule = engines[-1].profile_document()["store_backup"]
        self.assertEqual(rule["root"], f"/var/lib/autostop-store-cold/attempts/{attempt}/backup")
        self.assertEqual(rule["optional"], ["photos"])
        self.assertEqual(
            engines[-1].profile_document()["diagnostic_sqlite"],
            engines[-2].profile_document()["diagnostic_sqlite"],
        )

    def test_only_the_six_exact_closed_artifacts_and_optional_photos_are_accepted(self):
        manifest = self.manifest()
        self.assertEqual(
            cold._validate_store_manifest(manifest, self.engine.profile_document()),
            manifest["store_backup"],
        )
        photos = manifest["store_backup"]["files"]["photos"]
        manifest["store_backup"]["files"]["photos"] = None
        manifest["artifacts"] = [
            row for row in manifest["artifacts"] if row["source"] != photos["file"]
        ]
        cold._validate_store_manifest(manifest, self.engine.profile_document())

    def test_extra_missing_duplicate_changed_and_cross_attempt_artifacts_refuse(self):
        for damage in (
            lambda m: m["artifacts"].append(copy.deepcopy(m["artifacts"][-1])),
            lambda m: m["artifacts"].pop(),
            lambda m: m["artifacts"][-1].update(sha256="a" * 64),
            lambda m: m["artifacts"][-1].update(bytes=14),
            lambda m: m["artifacts"][-1].update(kind="link"),
            lambda m: m["store_backup"]["files"].update(env=None),
            lambda m: m["store_backup"]["files"]["env"].update(file="/private/other/env.bin"),
            lambda m: m["store_backup"]["files"]["database"].update(bytes=True),
            lambda m: m["store_backup"].update(restored=True),
            lambda m: m["store_backup"].update(
                root="/var/lib/autostop-store-cold/attempts/other/backup"
            ),
        ):
            with self.subTest(damage=damage):
                manifest = self.manifest()
                damage(manifest)
                with self.assertRaises((original.BackupRefusal, io.Refusal)):
                    cold._validate_store_manifest(manifest, self.engine.profile_document())

    def test_independent_restore_rederives_from_restored_control_not_live_paths(self):
        manifest = self.manifest()
        response = {
            "plan": {
                "cold_authority_package_ref": manifest["profile"]["diagnostic_sqlite"][
                    "package_ref"
                ]
            },
            "scope_profile_sha256": io.sha(original.canonical(manifest["profile"])),
        }

        def read(_ref):
            self.fail("Live/interval source paths are forbidden")

        read.read_source = lambda _ref: b"independently restored source-qualified control"

        def derive(reference, restored):
            self.assertEqual(reference, response["plan"]["cold_authority_package_ref"])
            self.assertEqual(
                restored(reference), b"independently restored source-qualified control"
            )
            return manifest["store_backup"]

        with patch.object(cold, "store_selector", side_effect=derive):
            self.assertEqual(
                cold.validate_restored_store(manifest, response, read), manifest["store_backup"]
            )
        forged = copy.deepcopy(manifest["store_backup"])
        forged["backup_ref"]["sha256"] = "a" * 64
        with patch.object(cold, "store_selector", return_value=forged):
            with self.assertRaisesRegex(original.BackupRefusal, "store_restored_source_binding"):
                cold.validate_restored_store(manifest, response, read)

    def test_caller_boolean_cannot_replace_actual_native_capture_interval(self):
        self.manifest()
        with self.assertRaisesRegex(self.engine.BackupRefusal, "store_native_interval"):
            self.engine.capture(
                Path("/private/unreachable"), {}, lambda: {"closed": True}, timeout=10
            )


@unittest.skipUnless(os.name == "posix", "Release checks require POSIX ownership and permissions")
class ActualStoreOpaqueBackupTests(unittest.TestCase):
    def test_actual_capture_two_independent_restores_and_tamper_denial(self):
        # This tests the opaque artifact algorithm with synthetic exports. Real
        # PostgreSQL logical restore is a separate Store Docker qualification.
        with (
            tempfile.TemporaryDirectory(prefix="store-data-profile-") as temporary,
            ExitStack() as stack,
        ):
            root = Path(temporary).resolve()
            root.chmod(0o700)
            backup_root = root / "store-backup"
            backup_root.mkdir(mode=0o700)
            revision, generation, attempt, package = scope()
            rule = cold._store_rule(package, attempt)
            rule["root"] = str(backup_root)
            stack.enter_context(patch.object(cold, "_store_rule", return_value=rule))
            engine = cold.engine(revision, generation, attempt, version=5, package_ref=package)
            engine.ROOTS, engine.OPTIONAL_ROOTS = (("store_backup", str(backup_root)),), ()
            selected = selector(rule)
            for name, leaf in cold.STORE_FILES.items():
                raw = ("synthetic Store export " + name + "\n").encode()
                path = backup_root / leaf
                path.write_bytes(raw)
                path.chmod(0o600)
                selected["files"][name].update(sha256=io.sha(raw), bytes=len(raw))
            engine._store_capture_selector = selected
            stack.enter_context(rehearsal.PrivateFilesystem(root, engines=(engine,)))
            context = {
                "attempt_id": attempt,
                "predecessor_sha256": "1" * 64,
                "candidate_crm_sha": "3" * 40,
                "candidate_manager_sha": revision,
                "scope_profile_sha256": engine.profile_sha256(),
            }

            def fence():
                return {
                    "attempt_id": attempt,
                    "path": str(root / "fence.json"),
                    "sha256": "4" * 64,
                }

            capture = types.FunctionType(original.capture.__code__, engine.__dict__)
            restore = types.FunctionType(original.verify_and_restore.__code__, engine.__dict__)
            output = root / "captured"
            receipt = capture(output, context, fence, timeout=10)
            manifest = io.document((output / "manifest.json").read_bytes())
            cold._validate_store_manifest(manifest, engine.profile_document())
            for name in ("local", "independent"):
                restored = root / name
                result = restore(output, receipt["manifest_sha256"], restored, context, timeout=10)
                self.assertTrue(result["provided_artifacts_restored_exactly"])
                for row in manifest["artifacts"]:
                    if "payload" in row:
                        self.assertEqual(
                            io.sha((restored / row["payload"]).read_bytes()), row["sha256"]
                        )
            target = next(row for row in manifest["artifacts"] if row.get("payload"))
            (output / target["payload"]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(engine.BackupRefusal, "artifact_bytes_mismatch"):
                restore(output, receipt["manifest_sha256"], root / "corrupt", context, timeout=10)
            self.assertFalse((root / "corrupt").exists())
