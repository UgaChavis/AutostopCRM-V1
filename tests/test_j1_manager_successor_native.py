"""Native validation against owned files; no service, network, signal or exec effects.

Windows fixtures project POSIX ownership/modes and symlinks onto real temporary
files. File contents, inventories, hashes, descriptors and replacement races are
real; procfs/systemctl are explicit data seams, never external operations.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_manager_successor_native as native


class OwnedNative(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        self.modes, self.links = {}, {}
        self.original_stat = Path.lstat
        original_fstat, original_resolve, original_readlink = os.fstat, Path.resolve, os.readlink

        def projected(info):
            value = SimpleNamespace(
                **{key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
            )
            value.st_mode = stat.S_IFMT(info.st_mode) | (
                0o755 if stat.S_ISDIR(info.st_mode) else 0o644
            )
            value.st_uid = value.st_gid = 0
            # Windows lstat reports birth time while fstat reports change time
            # for ctime. Production is Linux; project the POSIX field here.
            if os.name == "nt":
                value.st_ctime_ns = value.st_mtime_ns
            for key, item in self.modes.get((info.st_dev, info.st_ino), {}).items():
                setattr(value, key, item)
            return value

        def lstat(path, *args, **kwargs):
            value = projected(self.original_stat(path, *args, **kwargs))
            if path in self.links:
                value.st_mode = stat.S_IFLNK | 0o777
            return value

        def resolve(path, *args, **kwargs):
            return self.links.get(path) or original_resolve(path, *args, **kwargs)

        for target, value in (
            ("lstat", lstat),
            ("resolve", resolve),
        ):
            self.start(patch.object(Path, target, value))
        self.start(patch.object(os, "fstat", side_effect=lambda fd: projected(original_fstat(fd))))
        self.start(
            patch.object(
                os,
                "readlink",
                side_effect=lambda path: (
                    str(self.links[Path(path)])
                    if Path(path) in self.links
                    else original_readlink(path)
                ),
            )
        )
        for name in ("SYSTEMD", "STATE", "PROC"):
            folder = self.root / name.lower()
            folder.mkdir()
            self.start(patch.object(native, name, folder))
        self.start(patch.object(native, "CACHE", self.root / "cache" / "research.sqlite3"))
        self.start(
            patch.object(
                native,
                "UNIT_ROOTS",
                (
                    native.SYSTEMD,
                    self.root / "run-units",
                    self.root / "usr-units",
                    self.root / "local-units",
                ),
            )
        )
        self.io = native.NativeIO(time.monotonic() + 60)
        for target in (
            "socket.socket.connect",
            "socket.socket.bind",
            "socket.getaddrinfo",
            "os.system",
            "os.kill",
            "os.execve",
            "subprocess.Popen",
        ):
            self.start(patch(target, side_effect=AssertionError("external operation forbidden")))
        for name in ("setitimer", "alarm", "signal"):
            self.start(
                patch.object(
                    signal, name, side_effect=AssertionError("signals forbidden"), create=True
                )
            )
        self.start(
            patch.object(signal, "ITIMER_REAL", getattr(signal, "ITIMER_REAL", 0), create=True)
        )

    def start(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def put(self, name, value=b""):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else self.json(value))
        return path

    def json(self, value):
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    def ref(self, path):
        return {"path": str(path), "sha256": native.sha(path.read_bytes())}

    def mode(self, path, **values):
        info = self.original_stat(path)
        self.modes[(info.st_dev, info.st_ino)] = values

    def link(self, name, target):
        path = self.put(name, b"synthetic POSIX link")
        self.links[path] = target
        return path

    def source(self, name="source", *, legacy=False):
        root = self.root / name
        raw = b"raise RuntimeError('candidate must never be imported')\n"
        module = self.put(name + "/autostop_manager/mcp_server.py", raw)
        revision = "1" * 40
        record = {"path": "autostop_manager/mcp_server.py", "sha256": native.sha(raw)}
        record.update(
            {"bytes": len(raw)}
            if legacy
            else {
                "size": len(raw),
                "mode": "100644",
                "git_oid": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
            }
        )
        index = {"revision": revision, "tree": "2" * 40, "files": [record]}
        revision_raw = (revision + "\n").encode()
        hashes = {record["path"]: record["sha256"], "REVISION": native.sha(revision_raw)}
        manifest = "".join(f"{hashes[path]}  ./{path}\n" for path in sorted(hashes)).encode()
        self.put(name + "/REVISION", revision_raw)
        self.put(name + "/MANIFEST.sha256", manifest)
        self.put(
            name + "/MANIFEST.files0",
            b"".join(f"./{path}".encode() + b"\0" for path in sorted(hashes)),
        )
        selector = self.link(name + "-current", root)
        info = root.lstat()
        profile = {
            "source_root": str(root),
            "source_identity": [info.st_dev, info.st_ino],
            "selector": str(selector),
            "manager_revision": revision,
            "manifest_sha256": native.sha(manifest),
            "module": "autostop_manager.mcp_server",
        }
        return profile, index, module

    def inventory(self):
        root = self.root / "dependency"
        path = self.put("dependency/lib/module.py", b"dependency bytes")
        record = {
            "relative": "lib/module.py",
            "kind": "file",
            "sha256": native.sha(path.read_bytes()),
        }
        value = {
            "schema": "autostop.public-runtime-tree-inventory.v1",
            "root": str(root),
            "records": [{"relative": "lib", "kind": "directory"}, record],
        }
        return value, path

    def runtime(self):
        profile, _, _ = self.source()
        executable = self.put("runtime/python", b"not executable test bytes")
        self.mode(executable, st_mode=stat.S_IFREG | 0o755)
        interpreter = self.link("runtime/python-link", executable)
        cwd = self.root / "original-working-directory"
        cwd.mkdir()
        inventory, _ = self.inventory()
        inventory_path = self.put(
            "autostop-runtime-inventories-fixture/dependencies.json", inventory
        )
        startup = self.root / "startup"
        startup.mkdir()
        receipt = {
            "startup_paths": [
                profile["source_root"],
                str(startup),
                str(self.root / "absent"),
                str(self.root / "stdlib.zip"),
            ]
        }
        receipt_path = self.put("runtime/receipt.json", receipt)
        archive = self.put("stdlib.zip", b"not an imported archive")
        environment = self.put("runtime/environment", b"not secrets")
        info, cwd_info = executable.lstat(), cwd.lstat()
        profile.update(
            {
                "interpreter": str(interpreter),
                "interpreter_realpath": str(executable),
                "interpreter_identity": [info.st_dev, info.st_ino],
                "interpreter_sha256": native.sha(executable.read_bytes()),
                "cwd": str(cwd),
                "cwd_identity": [cwd_info.st_dev, cwd_info.st_ino],
                "runtime_receipt": str(receipt_path),
                "runtime_files": [self.ref(receipt_path), self.ref(inventory_path)],
                "startup_locations": [
                    {"path": profile["source_root"], "kind": "directory", "files": {}},
                    {"path": str(startup), "kind": "directory", "files": {}},
                    {"path": str(self.root / "absent"), "kind": "absent"},
                    {
                        "path": str(archive),
                        "kind": "zip",
                        "sha256": native.sha(archive.read_bytes()),
                    },
                ],
                "environment_files": [
                    self.ref(environment),
                    {"path": str(self.root / "missing.env"), "sha256": None},
                ],
            }
        )
        self.start(patch.object(os, "access", return_value=True))
        return profile, receipt


class ProtectedIOTests(OwnedNative):
    def test_read_is_bounded_stable_and_never_creates(self):
        path = self.put("data", b"exact raw bytes")
        self.assertEqual(path.read_bytes(), self.io.read(str(path)))
        with self.assertRaisesRegex(native.NativeError, "protected_file"):
            self.io.read(str(path), 2)
        with self.assertRaises(FileNotFoundError):
            self.io.read(str(self.root / "missing"))
        self.assertFalse((self.root / "missing").exists())

    def test_owner_mode_hardlink_special_and_ancestor_are_rejected(self):
        path = self.put("nested/data", b"data")
        for values in (
            {"st_uid": 1},
            {"st_mode": stat.S_IFREG | 0o666},
            {"st_nlink": 2},
            {"st_mode": stat.S_IFIFO | 0o644},
            {"st_mode": stat.S_IFREG | 0o4644},
        ):
            with self.subTest(values=values):
                self.mode(path, **values)
                with self.assertRaisesRegex(native.NativeError, "protected_file"):
                    self.io.read(str(path))
        self.mode(path)
        self.mode(path.parent, st_mode=stat.S_IFDIR | 0o777)
        with self.assertRaisesRegex(native.NativeError, "protected_directory"):
            self.io.read(str(path))

    def test_symlinks_are_not_data_or_ancestry(self):
        real = self.put("real", b"data")
        alias = self.link("alias", real)
        with self.assertRaisesRegex(native.NativeError, "protected_file"):
            self.io.read(str(alias))
        folder = self.root / "folder"
        folder.mkdir()
        self.links[folder] = self.root
        with self.assertRaisesRegex(native.NativeError, "protected_directory"):
            native._directory(folder)

    def test_replacement_during_read_is_refused(self):
        path = self.put("replace", b"first")
        read = os.read
        called = False

        def replace(fd, size):
            nonlocal called
            raw = read(fd, size)
            if not called:
                called = True
                path.write_bytes(b"changed-length")
            return raw

        with patch.object(os, "read", side_effect=replace):
            with self.assertRaisesRegex(native.NativeError, "file_changed"):
                self.io.read(str(path))

    def test_exists_distinguishes_absent_alias_and_unreadable(self):
        self.assertFalse(self.io.exists(str(self.root / "missing" / "data")))
        path = self.put("present")
        self.assertTrue(self.io.exists(str(path)))
        alias = self.link("alias", path)
        self.assertTrue(self.io.exists(str(alias)))
        original = Path.lstat
        with patch.object(Path, "lstat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.io.exists(str(path))
        self.assertIsNotNone(original)

    def test_deadline_paths_limits_and_json_are_fail_closed(self):
        with self.assertRaisesRegex(native.NativeError, "deadline_invalid"):
            native.NativeIO(float("inf"))
        self.io.deadline = 0
        with self.assertRaisesRegex(native.NativeError, "deadline_exceeded"):
            self.io.exists(str(self.root))
        self.io.deadline = time.monotonic() + 60
        for value in (-1, True, 256 * 1024**2 + 1):
            with self.assertRaisesRegex(native.NativeError, "read_limit"):
                self.io.read(str(self.root), value)
        with self.assertRaisesRegex(native.NativeError, "absolute_path"):
            self.io.read("relative")
        for raw in (b'{"a":1,"a":2}', b"{", b'{"x":NaN}'):
            with self.assertRaises(native.NativeError):
                native._document(raw)
        self.assertEqual({"a": 1}, native._document(b'{"a":1}'))
        for name in ("../x", "/x", "x\\y", "x//y", "", "./x", "x\x00y"):
            with self.assertRaisesRegex(native.NativeError, "relative_path"):
                native._relative(name)

    def test_existing_empty_lock_is_shared_and_never_created(self):
        path = self.put("state/lifecycle.lock")
        calls = []
        fake = SimpleNamespace(
            LOCK_SH=1, LOCK_NB=4, flock=lambda fd, mode: calls.append((fd, mode))
        )
        self.start(patch.object(os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), create=True))
        self.start(patch.object(os, "O_CLOEXEC", getattr(os, "O_CLOEXEC", 0), create=True))
        with patch.object(sys, "platform", "linux"), patch.dict(sys.modules, {"fcntl": fake}):
            with self.io.lock():
                self.assertEqual(5, calls[0][1])
            with self.assertRaises(OSError):
                os.fstat(calls[0][0])
            path.write_bytes(b"unknown")
            with self.assertRaisesRegex(native.NativeError, "protected_file"):
                with self.io.lock():
                    self.fail("nonempty lock acquired")

    @unittest.skipUnless(sys.platform == "linux", "Linux flock/open flags require Linux")
    def test_kernel_lock_conflict_blocks_shared_validation_and_cloexec_is_set(self):
        import fcntl

        path = self.put("state/lifecycle.lock")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                with self.io.lock():
                    self.fail("exclusive mutation lock was bypassed")
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            opened = []
            original = os.open

            def capture(*args, **kwargs):
                result = original(*args, **kwargs)
                opened.append(result)
                return result

            with patch.object(os, "open", side_effect=capture), self.io.lock():
                self.assertFalse(os.get_inheritable(opened[-1]))
        finally:
            os.close(descriptor)


class SourceTests(OwnedNative):
    def test_full_source_bytes_git_hash_and_manifest_pass_without_import(self):
        profile, index, _ = self.source()
        self.io.verify_source(profile, index)

    def test_extra_pyc_is_denied_even_when_manifest_and_files_are_unchanged(self):
        profile, index, _ = self.source()
        self.put("source/autostop_manager/__pycache__/mcp_server.cpython-313.pyc", b"unaccepted")
        with self.assertRaisesRegex(native.NativeError, "unknown_source_file"):
            self.io.verify_source(profile, index)

    def test_old_three_roots_are_all_checked_and_extra_old_pyc_denies(self):
        fixtures = [self.source(name, legacy=True) for name in ("manager", "work", "personal")]
        policy = {"bindings": {str(i): fixtures[i % 3][0] for i in range(6)}}
        policy["bindings"]["unused"] = None
        self.io.verify_preserved_sources(policy, fixtures[0][1])
        self.put("work/__pycache__/unaccepted.pyc", b"extra")
        with self.assertRaisesRegex(native.NativeError, "unknown_source_file"):
            self.io.verify_preserved_sources(policy, fixtures[0][1])

    def test_missing_preserved_root_cannot_be_omitted(self):
        profile, index, _ = self.source(legacy=True)
        with self.assertRaisesRegex(native.NativeError, "preserved_source_roots"):
            self.io.verify_preserved_sources({"bindings": {"only": profile}}, index)

    def test_changed_blob_oid_mode_selector_revision_and_manifest_refuse(self):
        for kind in (
            "blob",
            "oid",
            "mode",
            "selector",
            "revision",
            "manifest",
            "identity",
            "entrypoint",
            "index",
        ):
            with self.subTest(kind=kind):
                profile, index, module = self.source(kind)
                if kind == "blob":
                    module.write_bytes(b"tampered")
                elif kind == "oid":
                    index["files"][0]["git_oid"] = "0" * 40
                elif kind == "mode":
                    self.mode(module, st_mode=stat.S_IFREG | 0o755)
                elif kind == "selector":
                    self.links[Path(profile["selector"])] = self.root
                elif kind == "revision":
                    index["revision"] = "0" * 40
                elif kind == "manifest":
                    (Path(profile["source_root"]) / "MANIFEST.sha256").write_bytes(b"rehashed lie")
                elif kind == "identity":
                    profile["source_identity"] = [0, 0]
                elif kind == "entrypoint":
                    profile["module"] = "not_present"
                else:
                    index["files"] *= 2
                with self.assertRaises(native.NativeError):
                    self.io.verify_source(profile, index)


class RuntimeTests(OwnedNative):
    def test_runtime_receipt_inventory_startup_and_environment_actual_bytes(self):
        profile, receipt = self.runtime()
        self.io.verify_runtime(profile, receipt)

    def test_runtime_content_and_inventory_missing_extra_alias_fail(self):
        for kind in ("content", "missing", "extra", "duplicate", "schema", "kind", "alias"):
            with self.subTest(kind=kind):
                value, path = self.inventory()
                if kind == "content":
                    path.write_bytes(b"changed")
                elif kind == "missing":
                    value["records"].append(
                        {"relative": "absent", "kind": "file", "sha256": "0" * 64}
                    )
                elif kind == "extra":
                    value["records"] = value["records"][:1]
                elif kind == "duplicate":
                    value["records"] *= 2
                elif kind == "schema":
                    value["schema"] = "unknown"
                elif kind == "kind":
                    value["records"][1]["kind"] = "directory"
                else:
                    self.links[path] = self.root
                    value["records"][1] = {
                        "relative": "lib/module.py",
                        "kind": "symlink",
                        "target": "wrong",
                        "physical_target": str(self.root),
                    }
                with self.assertRaises(native.NativeError):
                    self.io._runtime_inventory(value)
                self.links.pop(path, None)

    def test_exact_runtime_symlink_is_verified_without_traversal(self):
        value, path = self.inventory()
        self.links[path] = self.root
        value["records"][1] = {
            "relative": "lib/module.py",
            "kind": "symlink",
            "target": str(self.root),
            "physical_target": str(self.root),
        }
        self.io._runtime_inventory(value)

    def test_startup_hooks_include_packages_pth_and_cached_customization(self):
        folder = self.root / "hooks"
        files = [
            self.put("hooks/load.pth", b"import sentinel"),
            self.put("hooks/sitecustomize/sub/a.py", b"hook"),
            self.put("hooks/__pycache__/usercustomize.cpython-313.pyc", b"cached hook"),
        ]
        self.put("hooks/__pycache__/ordinary.cpython-313.pyc", b"ordinary")
        result = self.io._startup(str(folder))
        self.assertEqual(None, result["sitecustomize/"])
        self.assertEqual(None, result["sitecustomize/sub/"])
        for path in files:
            self.assertEqual(
                native.sha(path.read_bytes()), result[path.relative_to(folder).as_posix()]
            )
        self.assertNotIn("__pycache__/ordinary.cpython-313.pyc", result)

    def test_new_hook_and_environment_are_not_masked_by_receipt_flags(self):
        profile, receipt = self.runtime()
        self.put("startup/new.pth", b"import untrusted")
        with self.assertRaisesRegex(native.NativeError, "unknown_startup_hook"):
            self.io.verify_runtime(profile, receipt)
        profile["startup_locations"][1]["files"] = {"new.pth": native.sha(b"import untrusted")}
        self.put("missing.env", b"unaccepted environment")
        with self.assertRaisesRegex(native.NativeError, "new_environment_file"):
            self.io.verify_runtime(profile, receipt)

    def test_changed_runtime_bindings_receipt_cwd_and_interpreter_refuse(self):
        for kind in (
            "path",
            "identity",
            "cwd",
            "unbound",
            "hash",
            "receipt",
            "no_inventory",
            "startup",
            "absent",
            "zip",
            "kind",
            "environment",
        ):
            with self.subTest(kind=kind):
                profile, receipt = self.runtime_once(kind)
                if kind == "path":
                    profile["interpreter_realpath"] = str(self.root)
                elif kind == "identity":
                    profile["interpreter_identity"] = [0, 0]
                elif kind == "cwd":
                    profile["cwd_identity"] = [0, 0]
                elif kind == "unbound":
                    profile["runtime_receipt"] += ".missing"
                elif kind == "hash":
                    profile["runtime_files"][0]["sha256"] = "0" * 64
                elif kind == "receipt":
                    receipt = {**receipt, "untrusted": True}
                elif kind == "no_inventory":
                    profile["runtime_files"] = profile["runtime_files"][:1]
                elif kind == "startup":
                    receipt["startup_paths"] = []
                elif kind == "absent":
                    self.put("absent")
                elif kind == "zip":
                    profile["startup_locations"][-1]["sha256"] = "0" * 64
                elif kind == "kind":
                    profile["startup_locations"][-1]["kind"] = "unknown"
                elif kind == "environment":
                    profile["environment_files"][0]["sha256"] = "0" * 64
                with self.assertRaises(native.NativeError):
                    self.io.verify_runtime(profile, receipt)

    def runtime_once(self, label):
        # All cases share no altered on-disk fixture except the intentionally
        # absent startup path, which is the final filesystem mutation.
        if not hasattr(self, "runtime_fixture"):
            self.runtime_fixture = self.runtime()
        if label == "zip":
            (self.root / "absent").unlink()
        return copy.deepcopy(self.runtime_fixture)


class UnitTests(OwnedNative):
    def units(self):
        folder = native.SYSTEMD / (native.UNIT + ".d")
        folder.mkdir()
        original = folder / "99-j1-candidate-guard.conf"
        policy_hash, binding_hash = "a" * 64, "b" * 64
        prefix = f"/usr/bin/python3 -I -S {native.PUBLIC_GUARD}"
        old = (
            "# PREPARED ONLY: apply after accepted quiet window and exact bindings.\n[Service]\nSuccessExitStatus=\n"
            + "".join(f"Environment={key}={value}\n" for key, value in native.FIXED_ENV.items())
            + f"ExecCondition=+{prefix} check --policy-sha256 {policy_hash} --unit {native.UNIT}\n"
            + f"ExecStart=\nExecStart={prefix} launch --policy-sha256 {policy_hash} --unit {native.UNIT}\n"
        ).encode()
        original.write_bytes(old)
        successor = folder / native.NEW_DROPIN
        successor.write_bytes(b"pinned successor dropin bytes")
        preserved = folder / "10-original.conf"
        preserved.write_bytes(b"original service constraints")
        refs = {
            native.PUBLIC_GUARD: self.ref(self.put("original-guard.py", b"old guard")),
            native.PUBLIC_DUTY: self.ref(self.put("original-duty.py", b"old duty")),
        }
        profile = {
            "unit_files": [
                {"path": public, "sha256": ref["sha256"]} for public, ref in refs.items()
            ],
            "guard_dropin": str(original),
            "dropin_directories": [
                {
                    "path": str(folder),
                    "files": {preserved.name: native.sha(preserved.read_bytes())},
                },
                {"path": str(self.root / "absent-overlay"), "files": None},
            ],
        }
        fields = {key: "" for key in native.SHOW_FIELDS.split(",")}
        fields.update(
            {
                "Id": native.UNIT,
                "Names": native.UNIT,
                "LoadState": "loaded",
                "Type": "simple",
                "NotifyAccess": "none",
                "FragmentPath": str(native.SYSTEMD / native.UNIT),
                "NeedDaemonReload": "no",
            }
        )
        prefix = f"/usr/bin/python3 -I -S -B {native.INSTALL}/guard.py"
        for key, operation in (("ExecCondition", "check"), ("ExecStart", "launch")):
            fields[key] = (
                f"{{ path=/usr/bin/python3 ; argv[]={prefix} {operation} --binding-sha256 {binding_hash} --unit {native.UNIT} ; ignore_errors=no ; }}"
            )
        kwargs = {
            "binding_sha256": binding_hash,
            "old_policy_sha256": policy_hash,
            "old_refs": refs,
            "dropin_bytes": successor.read_bytes(),
        }
        return profile, kwargs, fields

    def test_exact_loaded_commands_and_immutable_old_files_are_checked(self):
        profile, kwargs, fields = self.units()
        with patch.object(self.io, "_systemd", return_value=fields):
            self.io.verify_units(profile, **kwargs)
        kwargs["old_refs"][native.PUBLIC_GUARD]["sha256"] = "0" * 64
        with self.assertRaisesRegex(native.NativeError, "unit_file_changed"):
            self.io.verify_units(profile, **kwargs)

    def test_durable_dropin_does_not_authorize_stale_loaded_units_or_hooks(self):
        profile, kwargs, fields = self.units()
        for key, value in (
            ("NeedDaemonReload", "yes"),
            ("Type", "notify"),
            ("ExecStart", "old guard"),
            ("ExecCondition", fields["ExecCondition"] * 2),
            ("ExecStop", "cleanup kill"),
        ):
            with (
                self.subTest(key=key),
                patch.object(self.io, "_systemd", return_value={**fields, key: value}),
            ):
                with self.assertRaises(native.NativeError):
                    self.io.verify_units(profile, **kwargs)

    def test_unexpected_dropin_or_original_bytes_are_refused(self):
        profile, kwargs, _ = self.units()
        own = Path(profile["guard_dropin"])
        extra = own.with_name("zz-unknown.conf")
        extra.write_bytes(b"unaccepted hook")
        with self.assertRaisesRegex(native.NativeError, "unknown_dropin"):
            self.io.verify_units(profile, **kwargs)
        extra.unlink()
        own.write_bytes(b"changed original")
        with self.assertRaisesRegex(native.NativeError, "original_dropin_changed"):
            self.io.verify_units(profile, **kwargs)

    def test_systemd_command_is_exact_read_only_and_bounded(self):
        _, _, fields = self.units()

        def command(args, **kwargs):
            self.assertEqual(
                [
                    "/usr/bin/systemctl",
                    "show",
                    native.UNIT,
                    "--no-pager",
                    "--property=" + native.SHOW_FIELDS,
                ],
                args,
            )
            self.assertLessEqual(kwargs["timeout"], 2)
            self.assertEqual({"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, kwargs["env"])
            return SimpleNamespace(
                returncode=0,
                stdout="".join(f"{key}={value}\n" for key, value in fields.items()).encode(),
            )

        with patch.object(subprocess, "run", side_effect=command):
            self.assertEqual(fields, self.io._systemd())
        for raw in (b"Unknown=1\n", b"Id=x\nId=x\n", b"missing equals\n", b""):
            with patch.object(
                subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=raw)
            ):
                with self.assertRaisesRegex(native.NativeError, "loaded_unit_shape"):
                    self.io._systemd()
        with patch.object(
            subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"")
        ):
            with self.assertRaisesRegex(native.NativeError, "loaded_unit_failed"):
                self.io._systemd()


class PreservedAuthorityTests(OwnedNative):
    units = UnitTests.units

    def preserved(self):
        mcp, kwargs, _ = self.units()
        runtime_profile, _ = self.runtime()
        mcp.update(runtime_profile)
        runtime = self.put("shared/runtime", b"shared immutable runtime")
        environment = self.put("peer/environment", b"fixture-only environment")
        mcp["runtime_files"].append(self.ref(runtime))
        mcp["environment_files"] = [
            self.ref(environment),
            {"path": str(self.root / "absent.env"), "sha256": None},
        ]
        bindings = {native.UNIT: mcp}
        for unit in (
            "autostop-j1.service",
            "autostop-manager-scheduler.service",
            "autostop-work-telegram.service",
            "autostop-telegram.service",
            "autostop-codex-wake.service",
        ):
            folder = native.SYSTEMD / (unit + ".d")
            folder.mkdir()
            own = folder / "99-j1-candidate-guard.conf"
            own.write_bytes(native._old_dropin(unit, kwargs["old_policy_sha256"]))
            preserved = folder / "10-accepted.conf"
            preserved.write_bytes(b"peer constraint")
            bindings[unit] = {
                **copy.deepcopy(mcp),
                "guard_dropin": str(own),
                "dropin_directories": [
                    {
                        "path": str(folder),
                        "files": {preserved.name: native.sha(preserved.read_bytes())},
                    }
                ],
            }
        for unit in native.DENIED_UNITS:
            folder = native.SYSTEMD / (unit + ".d")
            folder.mkdir()
            (folder / "99-j1-candidate-guard.conf").write_bytes(
                native._old_dropin(unit, kwargs["old_policy_sha256"])
            )
            accepted = folder / "10-accepted.conf"
            accepted.write_bytes(b"denied role original constraint")
            for profile in bindings.values():
                profile["unit_files"].append(self.ref(accepted))
        forward = {
            "policy": {"sha256": kwargs["old_policy_sha256"]},
            "original_guard": kwargs["old_refs"][native.PUBLIC_GUARD],
            "original_duty_controller": kwargs["old_refs"][native.PUBLIC_DUTY],
        }
        bindings["unused"] = None
        return {"bindings": bindings}, forward, runtime, environment

    def test_all_peer_static_files_and_denied_barriers_checked_without_services(self):
        policy, forward, _, _ = self.preserved()
        with patch.object(
            self.io, "_runtime_inventory", wraps=self.io._runtime_inventory
        ) as inventory:
            self.io.verify_preserved_authority(policy, forward)
        self.assertEqual(1, inventory.call_count)

    def test_changed_peer_env_shared_runtime_or_dropin_denies(self):
        policy, forward, runtime, environment = self.preserved()
        peer = native.SYSTEMD / "autostop-work-telegram.service.d" / "10-accepted.conf"
        for path in (runtime, environment, peer):
            raw = path.read_bytes()
            with self.subTest(path=path.name):
                path.write_bytes(b"changed preserved dependency")
                with self.assertRaises(native.NativeError):
                    self.io.verify_preserved_authority(policy, forward)
                path.write_bytes(raw)

    def test_extra_peer_dropin_denied_barrier_missing_and_overlay_deny(self):
        policy, forward, _, _ = self.preserved()
        extra = native.SYSTEMD / "autostop-work-telegram.service.d" / "zz-injected.conf"
        extra.write_bytes(b"unexpected command")
        with self.assertRaisesRegex(native.NativeError, "unknown_dropin"):
            self.io.verify_preserved_authority(policy, forward)
        extra.unlink()
        barrier = native.SYSTEMD / (native.DENIED_UNITS[0] + ".d") / "99-j1-candidate-guard.conf"
        raw = barrier.read_bytes()
        barrier.unlink()
        with self.assertRaises(FileNotFoundError):
            self.io.verify_preserved_authority(policy, forward)
        barrier.write_bytes(raw)
        overlay = native.UNIT_ROOTS[1] / (native.DENIED_UNITS[0] + ".d")
        overlay.mkdir(parents=True)
        with self.assertRaisesRegex(native.NativeError, "denied_unit_overlay"):
            self.io.verify_preserved_authority(policy, forward)

    def test_conflicting_shared_pin_and_unknown_environment_are_not_deduplicated_away(self):
        policy, forward, _, _ = self.preserved()
        self.put("absent.env", b"unknown")
        with self.assertRaisesRegex(native.NativeError, "new_environment_file"):
            self.io.verify_preserved_authority(policy, forward)
        (self.root / "absent.env").unlink()
        policy["bindings"]["autostop-work-telegram.service"]["runtime_files"][0]["sha256"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(native.NativeError, "conflicting_pin"):
            self.io.verify_preserved_authority(policy, forward)

    def test_peer_startup_and_interpreter_identity_are_fresh_static_dependencies(self):
        policy, forward, _, _ = self.preserved()
        peer = policy["bindings"]["autostop-work-telegram.service"]
        changed = self.put("startup/late.pth", b"unaccepted startup code")
        with self.assertRaisesRegex(native.NativeError, "unknown_startup_hook"):
            self.io.verify_preserved_authority(policy, forward)
        changed.unlink()
        original = peer["interpreter_identity"]
        peer["interpreter_identity"] = [0, 0]
        with self.assertRaisesRegex(native.NativeError, "interpreter_changed"):
            self.io.verify_preserved_authority(policy, forward)
        peer["interpreter_identity"] = original
        peer["cwd_identity"] = [0, 0]
        with self.assertRaisesRegex(native.NativeError, "working_directory_changed"):
            self.io.verify_preserved_authority(policy, forward)


class ProcessTests(OwnedNative):
    def mount(self, pid, *, rw=False, stack=False):
        mountpoint = native.CACHE.parent.as_posix()
        raw = f"10 2 8:1 /cache {mountpoint} {'rw' if rw else 'ro'},relatime - ext4 /dev/root rw\n"
        if stack:
            raw += f"11 10 8:1 /cache {mountpoint} rw,relatime - ext4 /dev/root rw\n"
        self.put(f"proc/{pid}/mountinfo", raw.encode())
        return self.io._cache_mount(pid, "native" if stack else "rw" if rw else "ro")

    def cache(self):
        path = self.put("cache/research.sqlite3", b"never open a database")
        info, parent = path.lstat(), path.parent.lstat()
        profile = {
            "ordinary_database": {
                "path": str(path),
                "observed_path": str(path),
                "identity": native._identity(info),
                "parent_identity": [
                    parent.st_dev,
                    parent.st_ino,
                    parent.st_uid,
                    stat.S_IMODE(parent.st_mode),
                ],
            }
        }
        self.mount("self", stack=True)
        host = self.mount("1")
        cache = {
            "database_identity": native._identity(info),
            "parent_identity": native._identity(parent),
            "pid1": host,
        }
        for key in ("policy", "native_proof", "state"):
            cache[key] = self.ref(self.put("evidence/" + key, b"accepted proof"))
        return profile, cache

    def warm(self):
        _, cache = self.cache()
        boot = "00000000-0000-0000-0000-000000000001"
        self.put("proc/sys/kernel/random/boot_id", (boot + "\n").encode())
        executable = self.put("daemon", b"accepted daemon executable")
        info = executable.lstat()
        self.link("proc/42/exe", executable)
        self.link("proc/42/ns/mnt", Path("mnt:[42]"))
        # Kernel magic link content is represented by this exact regular file
        # for descriptor hashing; readlink still exposes its accepted target.
        (native.PROC / "42" / "exe").write_bytes(executable.read_bytes())
        self.put(
            "proc/42/stat",
            ("42 (actor name) " + " ".join(["S"] + ["0"] * 18 + ["123"]) + "\n").encode(),
        )
        self.put("proc/42/cmdline", b"daemon\0server\0")
        self.put("proc/42/cgroup", b"0::/accepted-warm\n")
        self.put("proc/42/status", b"Name:\tdaemon\nUid:\t0\t0\t0\t0\n")
        self.mount("42")
        actor = {
            "pid": 42,
            "start_ticks": 123,
            "argv_sha256": native.sha(b"daemon\0server\0"),
            "cgroup": "0::/accepted-warm",
            "exe": str(executable),
            "mount_namespace": "mnt:[42]",
            "exe_sha256": native.sha(executable.read_bytes()),
            "exe_identity": [info.st_dev, info.st_ino],
            "exe_file_identity": native._identity(info),
            "uid": 0,
        }
        original = os.open
        self.start(
            patch.object(
                os,
                "open",
                side_effect=lambda path, flags, *args, **kwargs: original(
                    executable if Path(path) == native.PROC / "42/exe" else path,
                    flags,
                    *args,
                    **kwargs,
                ),
            )
        )
        return {"boot_id": boot, "actors": [actor], "cache": cache}

    def test_cache_identity_and_exact_mount_stack_use_only_metadata(self):
        profile, _ = self.cache()
        self.io.verify_cache(profile)
        profile["ordinary_database"]["identity"][1] += 1
        with self.assertRaisesRegex(native.NativeError, "cache_identity_changed"):
            self.io.verify_cache(profile)

    def test_mount_mode_stack_origin_and_proc_bounds_refuse(self):
        self.mount("self")
        with self.assertRaisesRegex(native.NativeError, "mount_ambiguous"):
            self.io._cache_mount("self", "native")
        self.mount("self", stack=True)
        path = native.PROC / "self/mountinfo"
        path.write_bytes(path.read_bytes().replace(b"11 10 8:1", b"11 9 8:2"))
        with self.assertRaisesRegex(native.NativeError, "mount_stack"):
            self.io._cache_mount("self", "native")
        with self.assertRaisesRegex(native.NativeError, "proc_too_large"):
            self.io._proc("self", "mountinfo", 1)
        with self.assertRaisesRegex(native.NativeError, "pid_invalid"):
            self.io._proc("../1", "stat")
        with self.assertRaisesRegex(native.NativeError, "proc_name"):
            self.io._proc("self", "unknown")

    def test_warm_actor_exact_executable_birth_and_cache_are_rechecked(self):
        warm = self.warm()
        self.io.verify_warm(warm)
        (native.PROC / "42/cmdline").write_bytes(b"changed actor\0")
        with self.assertRaisesRegex(native.NativeError, "warm_actor_changed"):
            self.io.verify_warm(warm)

    def test_warm_boot_pid_reuse_uid_executable_and_host_mount_changes_deny(self):
        warm = self.warm()
        for kind in ("boot", "birth", "uid", "exe", "mount", "evidence", "duplicate"):
            with self.subTest(kind=kind):
                value = copy.deepcopy(warm)
                if kind == "boot":
                    value["boot_id"] = "wrong"
                elif kind == "birth":
                    value["actors"][0]["start_ticks"] += 1
                elif kind == "uid":
                    value["actors"][0]["uid"] = 1
                elif kind == "exe":
                    value["actors"][0]["exe_sha256"] = "0" * 64
                elif kind == "mount":
                    value["cache"]["pid1"]["mode"] = "rw"
                elif kind == "evidence":
                    value["cache"]["state"]["sha256"] = "0" * 64
                else:
                    value["actors"] *= 2
                with self.assertRaises(native.NativeError):
                    self.io.verify_warm(value)

    def test_pid_replaced_during_warm_check_is_denied(self):
        warm = self.warm()
        with patch.object(self.io, "_start", side_effect=[123, 124]):
            with self.assertRaisesRegex(native.NativeError, "actor_replaced"):
                self.io.verify_warm(warm)

    def test_observation_is_actual_environment_and_unified_cgroup(self):
        self.put("proc/self/cgroup", b"0::/system.slice/autostop-manager-mcp.service\n")
        self.put("proc/sys/kernel/random/boot_id", b"00000000-0000-0000-0000-000000000001\n")
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(os, "geteuid", return_value=0, create=True),
            patch.object(os, "getegid", return_value=0, create=True),
            patch.object(os, "getgroups", return_value=[], create=True),
        ):
            actual = self.io.observation()
            self.assertEqual(dict(os.environ), actual["environment"])
            self.assertEqual(os.getcwd(), actual["cwd"])
            self.assertEqual("/system.slice/" + native.UNIT, actual["cgroup"])
            self.put("proc/self/cgroup", b"2:cpu:legacy\n")
            with self.assertRaisesRegex(native.NativeError, "unified_cgroup"):
                self.io.observation()

    def test_dispatch_only_fixed_entrypoint_and_no_chdir_or_shell(self):
        plan = {
            "executable": "/sealed/python",
            "argv": ["/sealed/python", "-P", "-m", "autostop_manager.mcp_server"],
            "environment": {"ONLY": "accepted"},
            "cwd": os.getcwd(),
            "unit": native.UNIT,
            "revision": "1" * 40,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(os, "execve", return_value=None) as execute,
            patch.object(signal, "setitimer", return_value=None) as timer,
        ):
            with self.assertRaisesRegex(native.NativeError, "exec_returned"):
                self.io.dispatch(plan)
            execute.assert_called_once_with(plan["executable"], plan["argv"], plan["environment"])
            timer.assert_called_once_with(signal.ITIMER_REAL, 0)
            execute.reset_mock()
            timer.reset_mock()
            for changed in ({"argv": ["/bin/sh"]}, {"cwd": "wrong"}, {"old_writer_allowed": True}):
                with self.assertRaises(native.NativeError):
                    self.io.dispatch({**plan, **changed})
            execute.assert_not_called()
            timer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
