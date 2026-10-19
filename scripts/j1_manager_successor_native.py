"""Pinned stdlib-only Linux IO for the MCP successor guard.

Validation reads files, procfs and one exact systemctl show invocation. It never
imports Manager, opens a database, writes state, or starts/stops a service.
Only dispatch, called by the guard while holding its shared lifecycle lock,
replaces the already-authorized process with the fixed MCP entry point.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

UNIT = "autostop-manager-mcp.service"
INSTALL = Path("/usr/local/libexec/autostop-manager-mcp-successor/v1")
STATE = Path("/var/lib/autostop-manager-mcp-successor/v1")
SYSTEMD = Path("/etc/systemd/system")
PROC = Path("/proc")
CACHE = Path("/var/cache/autostop-j1/research.sqlite3")
PUBLIC_GUARD = "/usr/local/libexec/autostop-j1-candidate-guard/guard.py"
PUBLIC_DUTY = "/usr/local/libexec/autostop-crm-j1-duty/restore.py"
NEW_DROPIN = "zz-j1-authority-mcp.conf"
DENIED_UNITS = ("autostop-codex-start.service", "autostop-j1-browser.service")
UNIT_ROOTS = (
    SYSTEMD,
    Path("/run/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/usr/local/lib/systemd/system"),
)
SHOW_FIELDS = (
    "Id,Names,LoadState,Type,NotifyAccess,FragmentPath,SourcePath,NeedDaemonReload,"
    "ExecCondition,ExecStart,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,ExecReload"
)
FIXED_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONSAFEPATH": "1",
    "PYTHONNOUSERSITE": "1",
    "AUTOSTOP_MANAGER_ENV_FILE": "/dev/null",
    "AUTOSTOP_J1_CACHE_DIR": "/var/cache/autostop-j1",
}
WARM_CODEX = Path(
    "/root/.codex/packages/standalone/releases/0.163.0-alpha.11-x86_64-unknown-linux-musl/bin/codex"
)


class NativeError(ValueError):
    """A content-free native refusal suitable for the guard's DENY response."""


def require(value, code):
    if not value:
        raise NativeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "native_duplicate_json_key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise NativeError("native_nonfinite_json")


def _document(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise NativeError("native_json_invalid") from exc


def _metadata(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]


def _absolute(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "native_absolute_path_required")
    return path


def _directory(path):
    path = _absolute(path)
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400
            and info.st_uid == 0
            and not info.st_mode & 0o7022,
            "native_protected_directory_required",
        )
    return info


def _relative(path):
    require(
        type(path) is str and path and "\\" not in path and "\x00" not in path,
        "native_relative_path_invalid",
    )
    parsed = PurePosixPath(path)
    require(
        not parsed.is_absolute()
        and parsed.as_posix() == path
        and all(part not in {".", ".."} for part in parsed.parts),
        "native_relative_path_invalid",
    )
    return path


def _old_dropin(unit, policy_hash):
    return (
        "# PREPARED ONLY: apply after accepted quiet window and exact bindings.\n[Service]\n"
        "SuccessExitStatus=\n"
        + "".join(f"Environment={key}={value}\n" for key, value in FIXED_ENV.items())
        + f"ExecCondition=+/usr/bin/python3 -I -S {PUBLIC_GUARD} check --policy-sha256 {policy_hash} --unit {unit}\n"
        + f"ExecStart=\nExecStart=/usr/bin/python3 -I -S {PUBLIC_GUARD} launch --policy-sha256 {policy_hash} --unit {unit}\n"
    ).encode()


class NativeIO:
    def __init__(self, deadline: float):
        require(
            type(deadline) in {float, int} and math.isfinite(deadline), "native_deadline_invalid"
        )
        self.deadline = deadline

    def _check(self):
        require(time.monotonic() < self.deadline, "native_deadline_exceeded")

    def read(self, path: str, limit: int = 32 * 1024**2) -> bytes:
        self._check()
        require(type(limit) is int and 0 <= limit <= 256 * 1024**2, "native_read_limit_invalid")
        path = _absolute(path)
        _directory(path.parent)
        before = path.lstat()
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_uid == 0
            and not before.st_mode & 0o7022
            and not getattr(before, "st_file_attributes", 0) & 0x400
            and 0 <= before.st_size <= limit,
            "native_protected_file_required",
        )
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            require(_metadata(os.fstat(descriptor)) == _metadata(before), "native_file_replaced")
            blocks, remaining = [], limit + 1
            while remaining:
                self._check()
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                blocks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(blocks)
            require(
                len(raw) == before.st_size <= limit
                and _metadata(before) == _metadata(os.fstat(descriptor)) == _metadata(path.lstat()),
                "native_file_changed",
            )
            _directory(path.parent)
            return raw
        finally:
            os.close(descriptor)

    def exists(self, path: str) -> bool:
        self._check()
        path = _absolute(path)
        # Missing ancestors mean absence; an existing alias or inaccessible
        # ancestor is never silently interpreted as an absent marker.
        for parent in [*reversed(path.parents), path.parent]:
            try:
                _directory(parent)
            except FileNotFoundError:
                return False
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    @contextlib.contextmanager
    def lock(self):
        require(sys.platform == "linux", "native_linux_required")
        import fcntl

        path = STATE / "lifecycle.lock"
        require(self.read(str(path), 0) == b"", "native_lifecycle_lock_invalid")
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            require(_metadata(os.fstat(descriptor)) == _metadata(before), "native_lock_replaced")
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            require(_metadata(path.lstat()) == _metadata(before), "native_lock_replaced")
            self._check()
            yield
        finally:
            os.close(descriptor)

    def _proc(self, pid, name, limit=2 * 1024**2):
        self._check()
        require(pid in {"self", "1"} or re.fullmatch(r"[1-9][0-9]*", pid), "native_pid_invalid")
        require(
            name in {"stat", "cmdline", "cgroup", "status", "mountinfo"}, "native_proc_name_invalid"
        )
        with (PROC / pid / name).open("rb") as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, "native_proc_too_large")
        return raw

    def _boot(self):
        self._check()
        raw = (PROC / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        require(
            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", raw),
            "native_boot_identity_invalid",
        )
        return raw

    def observation(self):
        require(sys.platform == "linux", "native_linux_required")
        rows = self._proc("self", "cgroup").decode("utf-8", "strict").splitlines()
        require(len(rows) == 1 and rows[0].startswith("0::/"), "native_unified_cgroup_required")
        return {
            "host": socket.gethostname(),
            "boot_id": self._boot(),
            "cgroup": rows[0][3:],
            "uid": os.geteuid(),
            "gid": os.getegid(),
            "groups": os.getgroups(),
            "cwd": os.getcwd(),
            "environment": dict(os.environ),
        }

    def verify_source(self, profile, index):
        self._verify_source(profile, index, legacy=False)

    def verify_preserved_sources(self, policy, old_index):
        checked = set()
        for profile in policy["bindings"].values():
            if profile is None:
                continue
            key = (profile["source_root"], profile["selector"], tuple(profile["source_identity"]))
            if key not in checked:
                self._verify_source(
                    {**profile, "manager_revision": old_index["revision"]}, old_index, legacy=True
                )
                checked.add(key)
        require(len(checked) == 3, "native_preserved_source_roots_required")

    def _verify_source(self, profile, index, *, legacy):
        self._check()
        root = _absolute(profile["source_root"])
        info = _directory(root)
        require(
            [info.st_dev, info.st_ino] == profile["source_identity"],
            "native_source_identity_changed",
        )
        selector = _absolute(profile["selector"])
        _directory(selector.parent)
        link = selector.lstat()
        require(
            stat.S_ISLNK(link.st_mode)
            and link.st_uid == 0
            and selector.resolve(strict=True) == root,
            "native_source_selector_changed",
        )
        require(_metadata(selector.lstat()) == _metadata(link), "native_source_selector_changed")
        revision = (profile["manager_revision"] + "\n").encode("ascii")
        require(
            index["revision"] == profile["manager_revision"]
            and self.read(str(root / "REVISION"), 80) == revision,
            "native_source_revision_changed",
        )
        records = {_relative(record["path"]): record for record in index["files"]}
        require(
            len(records) == len(index["files"])
            and bool(records)
            and not set(records) & {"REVISION", "MANIFEST.sha256", "MANIFEST.files0"},
            "native_source_index_invalid",
        )
        hashes = {name: record["sha256"] for name, record in records.items()}
        hashes["REVISION"] = sha(revision)
        names = sorted(hashes)
        manifest = "".join(f"{hashes[name]}  ./{name}\n" for name in names).encode()
        inventory = b"".join(f"./{name}".encode() + b"\0" for name in names)
        require(
            sha(manifest) == profile["manifest_sha256"]
            and self.read(str(root / "MANIFEST.sha256")) == manifest
            and self.read(str(root / "MANIFEST.files0")) == inventory,
            "native_source_manifest_changed",
        )
        expected = set(records) | {"REVISION", "MANIFEST.sha256", "MANIFEST.files0"}
        found = set()
        for folder, dirs, files in os.walk(root, followlinks=False):
            self._check()
            _directory(folder)
            for name in dirs:
                _directory(Path(folder) / name)
            for name in files:
                found.add((Path(folder) / name).relative_to(root).as_posix())
        require(found == expected, "native_unknown_source_file")
        for name, record in records.items():
            path = root / name
            raw = self.read(str(path))
            require(
                len(raw) == record["bytes" if legacy else "size"] and sha(raw) == record["sha256"],
                "native_source_blob_changed",
            )
            if not legacy:
                require(
                    hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                    == record["git_oid"],
                    "native_source_git_blob_changed",
                )
                require(
                    record["mode"] in {"100644", "100755"}
                    and stat.S_IMODE(path.lstat().st_mode) == int(record["mode"][-3:], 8),
                    "native_source_mode_changed",
                )
        require(
            profile["module"].replace(".", "/") + ".py" in records,
            "native_source_entrypoint_missing",
        )

    def _startup(self, folder):
        folder = _absolute(folder)
        _directory(folder)
        result = {}
        for path in folder.iterdir():
            self._check()
            if path.name.endswith(".pth") or path.name.split(".", 1)[0] in {
                "sitecustomize",
                "usercustomize",
            }:
                if path.is_dir() and not path.is_symlink():
                    _directory(path)
                    result[path.name + "/"] = None
                    for parent, dirs, files in os.walk(path, followlinks=False):
                        _directory(parent)
                        for name in dirs:
                            child = Path(parent) / name
                            _directory(child)
                            result[child.relative_to(folder).as_posix() + "/"] = None
                        for name in files:
                            child = Path(parent) / name
                            result[child.relative_to(folder).as_posix()] = sha(
                                self.read(str(child))
                            )
                else:
                    result[path.name] = sha(self.read(str(path)))
            elif path.name == "__pycache__":
                _directory(path)
                for child in path.iterdir():
                    if child.name.split(".", 1)[0] in {"sitecustomize", "usercustomize"}:
                        result[child.relative_to(folder).as_posix()] = sha(self.read(str(child)))
        return result

    def _runtime_inventory(self, inventory):
        require(
            type(inventory) is dict
            and inventory.get("schema") == "autostop.public-runtime-tree-inventory.v1",
            "native_runtime_inventory_schema",
        )
        root = _absolute(inventory["root"])
        _directory(root)
        expected = {_relative(record["relative"]): record for record in inventory["records"]}
        require(len(expected) == len(inventory["records"]), "native_runtime_inventory_duplicate")
        found = set()
        for folder, dirs, files in os.walk(root, followlinks=False):
            _directory(folder)
            for name in (*dirs, *files):
                self._check()
                path = Path(folder) / name
                relative = path.relative_to(root).as_posix()
                require(relative in expected, "native_runtime_unexpected_file")
                record, info = expected[relative], path.lstat()
                kind = (
                    "symlink"
                    if stat.S_ISLNK(info.st_mode)
                    else "directory"
                    if stat.S_ISDIR(info.st_mode)
                    else "file"
                )
                require(
                    kind == record["kind"]
                    and info.st_uid == 0
                    and (kind == "symlink" or not info.st_mode & 0o7022),
                    "native_runtime_kind_changed",
                )
                if kind == "symlink":
                    require(
                        os.readlink(path) == record["target"]
                        and str(path.resolve(strict=True)) == record["physical_target"],
                        "native_runtime_alias_changed",
                    )
                elif kind == "file":
                    require(
                        sha(self.read(str(path))) == record["sha256"],
                        "native_runtime_content_changed",
                    )
                require(
                    _metadata(path.lstat()) == _metadata(info),
                    "native_runtime_changed_during_check",
                )
                found.add(relative)
        require(found == set(expected), "native_runtime_missing_file")

    def _runtime_identity(self, profile):
        interpreter = _absolute(profile["interpreter"])
        _directory(interpreter.parent)
        real = interpreter.resolve(strict=True)
        require(str(real) == profile["interpreter_realpath"], "native_interpreter_path_changed")
        info = real.lstat()
        require(
            [info.st_dev, info.st_ino] == profile["interpreter_identity"]
            and os.access(interpreter, os.X_OK)
            and sha(self.read(str(real))) == profile["interpreter_sha256"],
            "native_interpreter_changed",
        )
        cwd = _directory(profile["cwd"])
        require(
            [cwd.st_dev, cwd.st_ino] == profile["cwd_identity"], "native_working_directory_changed"
        )

    def verify_runtime(self, profile, receipt):
        self._runtime_identity(profile)
        require(
            bool(profile["runtime_files"])
            and any(
                record["path"] == profile["runtime_receipt"] for record in profile["runtime_files"]
            ),
            "native_runtime_receipt_unbound",
        )
        inventories = 0
        for record in profile["runtime_files"]:
            raw = self.read(record["path"])
            require(sha(raw) == record["sha256"], "native_runtime_binding_changed")
            if record["path"] == profile["runtime_receipt"]:
                require(_document(raw) == receipt, "native_runtime_receipt_changed")
            if any(
                part.startswith("autostop-runtime-inventories-")
                for part in Path(record["path"]).parts
            ):
                self._runtime_inventory(_document(raw))
                inventories += 1
        require(inventories > 0, "native_dependency_inventory_required")
        self._startup_locations(profile, receipt)
        for record in profile["environment_files"]:
            if record["sha256"] is None:
                require(not self.exists(record["path"]), "native_new_environment_file")
            else:
                require(
                    sha(self.read(record["path"])) == record["sha256"],
                    "native_environment_file_changed",
                )

    def _startup_locations(self, profile, receipt):
        require(
            receipt["startup_paths"] == [record["path"] for record in profile["startup_locations"]]
            and profile["source_root"] in receipt["startup_paths"],
            "native_startup_inventory_changed",
        )
        for record in profile["startup_locations"]:
            path = record["path"]
            if record["kind"] == "absent":
                require(not self.exists(path), "native_new_startup_location")
            elif record["kind"] == "zip":
                require(sha(self.read(path)) == record["sha256"], "native_startup_archive_changed")
            elif record["kind"] == "directory":
                require(self._startup(path) == record["files"], "native_unknown_startup_hook")
            else:
                raise NativeError("native_startup_kind_invalid")

    def _systemd(self):
        self._check()
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", UNIT, "--no-pager", "--property=" + SHOW_FIELDS],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            close_fds=True,
            check=False,
            timeout=min(2, max(0.001, self.deadline - time.monotonic())),
        )
        require(result.returncode == 0 and len(result.stdout) <= 32768, "native_loaded_unit_failed")
        rows = [
            line.partition("=") for line in result.stdout.decode("utf-8", "strict").splitlines()
        ]
        require(
            all(separator and key in SHOW_FIELDS.split(",") for key, separator, _ in rows)
            and len({row[0] for row in rows}) == len(rows),
            "native_loaded_unit_shape",
        )
        fields = {key: value for key, _, value in rows}
        require(set(fields) == set(SHOW_FIELDS.split(",")), "native_loaded_unit_shape")
        return fields

    def _dropins(self, profile, unit, policy_hash):
        own = SYSTEMD / (unit + ".d") / "99-j1-candidate-guard.conf"
        require(profile["guard_dropin"] == str(own), "native_original_dropin_path_changed")
        require(
            self.read(str(own)) == _old_dropin(unit, policy_hash), "native_original_dropin_changed"
        )
        require(
            any(record["path"] == str(own.parent) for record in profile["dropin_directories"]),
            "native_dropin_inventory_missing",
        )
        for record in profile["dropin_directories"]:
            folder = Path(record["path"])
            if record["files"] is None:
                require(not self.exists(str(folder)), "native_unknown_dropin_directory")
                continue
            _directory(folder)
            expected = set(record["files"])
            if folder == own.parent:
                expected.add(own.name)
                if unit == UNIT:
                    expected.add(NEW_DROPIN)
            actual = {path.name for path in folder.iterdir() if path.suffix == ".conf"}
            require(actual == expected, "native_unknown_dropin")
            for name, digest in record["files"].items():
                require(
                    Path(name).name == name and sha(self.read(str(folder / name))) == digest,
                    "native_dropin_changed",
                )

    def verify_preserved_authority(self, policy, forward):
        """Recheck static dependencies; this is not peer-process acceptance."""
        relocated = {
            PUBLIC_GUARD: forward["original_guard"],
            PUBLIC_DUTY: forward["original_duty_controller"],
        }
        seen, pinned = {}, {}
        for unit, profile in policy["bindings"].items():
            if profile is None:
                continue
            self._dropins(profile, unit, forward["policy"]["sha256"])
            for record in [
                *profile["unit_files"],
                *profile["runtime_files"],
                *profile["environment_files"],
            ]:
                if record in profile["unit_files"]:
                    require(
                        record["path"] not in pinned or pinned[record["path"]] == record["sha256"],
                        "native_preserved_conflicting_pin",
                    )
                    pinned[record["path"]] = record["sha256"]
                actual = relocated.get(record["path"], record)
                require(actual["sha256"] == record["sha256"], "native_original_relocation_changed")
                path, digest = actual["path"], actual["sha256"]
                if path in seen:
                    require(seen[path] == digest, "native_preserved_conflicting_pin")
                    continue
                if digest is None:
                    require(not self.exists(path), "native_new_environment_file")
                else:
                    raw = self.read(path)
                    require(sha(raw) == digest, "native_preserved_file_changed")
                    if any(
                        part.startswith("autostop-runtime-inventories-")
                        for part in Path(path).parts
                    ):
                        self._runtime_inventory(_document(raw))
                seen[path] = digest
            self._runtime_identity(profile)
            receipt = self.read(profile["runtime_receipt"])
            require(
                seen.get(profile["runtime_receipt"]) == sha(receipt),
                "native_runtime_receipt_unbound",
            )
            self._startup_locations(profile, _document(receipt))
        for unit in DENIED_UNITS:
            for root in UNIT_ROOTS:
                folder = root / (unit + ".d")
                expected = {
                    Path(path).name: digest
                    for path, digest in pinned.items()
                    if Path(path).parent == folder
                }
                if root != SYSTEMD:
                    require(
                        not expected and not self.exists(str(folder)), "native_denied_unit_overlay"
                    )
                    continue
                _directory(folder)
                own = folder / "99-j1-candidate-guard.conf"
                require(
                    self.read(str(own)) == _old_dropin(unit, forward["policy"]["sha256"]),
                    "native_denied_unit_barrier_changed",
                )
                require(
                    {path.name for path in folder.iterdir() if path.suffix == ".conf"}
                    == set(expected) | {own.name},
                    "native_denied_unit_dropin_changed",
                )
                for name, digest in expected.items():
                    require(
                        sha(self.read(str(folder / name))) == digest,
                        "native_denied_unit_file_changed",
                    )

    def verify_units(self, profile, *, binding_sha256, old_policy_sha256, old_refs, dropin_bytes):
        require(
            set(old_refs) == {PUBLIC_GUARD, PUBLIC_DUTY}, "native_original_relocations_required"
        )
        records = profile["unit_files"]
        require(
            records and len({record["path"] for record in records}) == len(records),
            "native_unit_inventory_invalid",
        )
        for record in records:
            actual = old_refs.get(record["path"], record)
            require(
                actual["sha256"] == record["sha256"]
                and sha(self.read(actual["path"])) == record["sha256"],
                "native_unit_file_changed",
            )
        self._dropins(profile, UNIT, old_policy_sha256)
        new = SYSTEMD / (UNIT + ".d") / NEW_DROPIN
        require(self.read(str(new)) == dropin_bytes, "native_successor_dropin_changed")
        fields = self._systemd()
        expected = {
            "Id": UNIT,
            "Names": UNIT,
            "LoadState": "loaded",
            "Type": "simple",
            "NotifyAccess": "none",
            "FragmentPath": str(SYSTEMD / UNIT),
            "SourcePath": "",
            "NeedDaemonReload": "no",
        }
        require(
            all(fields.get(key) == value for key, value in expected.items()),
            "native_loaded_context_changed",
        )
        prefix = f"/usr/bin/python3 -I -S -B {INSTALL}/guard.py"
        for key, operation in (("ExecCondition", "check"), ("ExecStart", "launch")):
            commands = re.findall(r"argv\[\]=(.*?) ;", fields[key])
            require(
                commands
                == [f"{prefix} {operation} --binding-sha256 {binding_sha256} --unit {UNIT}"],
                "native_loaded_guard_changed",
            )
        require(
            all(
                fields[key] == ""
                for key in (
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecStop",
                    "ExecStopPost",
                    "ExecReload",
                )
            ),
            "native_loaded_hook_changed",
        )

    def _cache_mount(self, pid, expected):
        matches = []
        for line in self._proc(pid, "mountinfo").decode("utf-8", "strict").splitlines():
            before, separator, after = line.partition(" - ")
            fields = before.split()
            if len(fields) >= 6 and fields[4] == CACHE.parent.as_posix():
                options = set(fields[5].split(","))
                require(
                    separator and len(options & {"ro", "rw"}) == 1 and after.split(),
                    "native_cache_mount_shape",
                )
                matches.append(
                    {
                        "id": fields[0],
                        "parent": fields[1],
                        "device": fields[2],
                        "root": fields[3],
                        "mountpoint": fields[4],
                        "mode": "ro" if "ro" in options else "rw",
                        "filesystem": after.split()[0],
                    }
                )
        if expected == "native":
            require(len(matches) == 2, "native_cache_mount_ambiguous")
            lower = next((item for item in matches if item["mode"] == "ro"), None)
            upper = next((item for item in matches if item["mode"] == "rw"), None)
            require(
                lower
                and upper
                and upper["parent"] == lower["id"]
                and all(
                    lower[key] == upper[key]
                    for key in ("device", "root", "mountpoint", "filesystem")
                ),
                "native_cache_mount_stack",
            )
            return {
                "layers": [
                    {key: value for key, value in item.items() if key not in {"id", "parent"}}
                    for item in (lower, upper)
                ]
            }
        require(len(matches) == 1 and matches[0]["mode"] == expected, "native_cache_mount_mode")
        return {key: value for key, value in matches[0].items() if key not in {"id", "parent"}}

    def verify_cache(self, profile):
        cache = profile["ordinary_database"]
        require(cache["path"] == cache["observed_path"] == str(CACHE), "native_cache_alias_denied")
        parent = _directory(CACHE.parent)
        require(
            [parent.st_dev, parent.st_ino, parent.st_uid, stat.S_IMODE(parent.st_mode)]
            == cache["parent_identity"],
            "native_cache_parent_changed",
        )
        info = CACHE.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and _identity(info) == cache["identity"],
            "native_cache_identity_changed",
        )
        self._cache_mount("self", "native")

    def _start(self, pid):
        raw = self._proc(pid, "stat").decode("ascii", "strict")
        require(") " in raw, "native_process_stat_invalid")
        fields = raw.rsplit(") ", 1)[1].split()
        require(len(fields) >= 20 and fields[19].isdigit(), "native_process_stat_invalid")
        return int(fields[19])

    def _warm_file(self, path, accepted):
        path = _absolute(path)
        owner = 1001 if path == WARM_CODEX else 0
        for parent in reversed(path.parents):
            info = parent.lstat()
            expected = 1001 if path == WARM_CODEX and parent == path.parent else 0
            require(
                stat.S_ISDIR(info.st_mode) and info.st_uid == expected and not info.st_mode & 0o022,
                "native_warm_ancestry_changed",
            )
            if parent == Path("/root"):
                require(stat.S_IMODE(info.st_mode) == 0o700, "native_warm_private_root_changed")
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == owner
            and info.st_nlink == 1
            and not info.st_mode & 0o7022
            and _identity(info) == accepted["exe_file_identity"],
            "native_warm_file_identity_changed",
        )
        self._hash_mapped(path, info, accepted["exe_sha256"], nofollow=True)
        require(_metadata(path.lstat()) == _metadata(info), "native_warm_file_changed")
        return info

    def _hash_mapped(self, path, expected, digest, *, nofollow=False):
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | (getattr(os, "O_NOFOLLOW", 0) if nofollow else 0),
        )
        try:
            require(
                _metadata(os.fstat(descriptor)) == _metadata(expected),
                "native_warm_mapped_file_changed",
            )
            hashed = hashlib.sha256()
            while True:
                self._check()
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                hashed.update(block)
            require(
                hashed.hexdigest() == digest
                and _metadata(os.fstat(descriptor)) == _metadata(expected),
                "native_warm_mapped_file_changed",
            )
        finally:
            os.close(descriptor)

    def verify_warm(self, warm):
        require(self._boot() == warm["boot_id"], "native_warm_boot_changed")
        cache = warm["cache"]
        require(
            _identity(CACHE.lstat()) == cache["database_identity"]
            and _identity(CACHE.parent.lstat()) == cache["parent_identity"],
            "native_warm_cache_identity_changed",
        )
        require(self._cache_mount("1", "ro") == cache["pid1"], "native_host_cache_mount_changed")
        for key in ("policy", "native_proof", "state"):
            require(
                sha(self.read(cache[key]["path"])) == cache[key]["sha256"],
                "native_cache_evidence_changed",
            )
        require(
            warm["actors"]
            and len({actor["pid"] for actor in warm["actors"]}) == len(warm["actors"]),
            "native_warm_actors_required",
        )
        executables = {}
        for accepted in warm["actors"]:
            pid = str(accepted["pid"])
            start = self._start(pid)
            actual = {
                "pid": int(pid),
                "start_ticks": start,
                "argv_sha256": sha(self._proc(pid, "cmdline")),
                "cgroup": self._proc(pid, "cgroup").decode().strip(),
                "exe": os.readlink(PROC / pid / "exe"),
                "mount_namespace": os.readlink(PROC / pid / "ns/mnt"),
            }
            require(actual == {key: accepted[key] for key in actual}, "native_warm_actor_changed")
            executable = actual["exe"]
            if executable not in executables:
                executables[executable] = self._warm_file(executable, accepted)
            info = executables[executable]
            require(
                [info.st_dev, info.st_ino] == accepted["exe_identity"],
                "native_warm_executable_changed",
            )
            self._hash_mapped(PROC / pid / "exe", info, accepted["exe_sha256"])
            rows = dict(
                line.split(":", 1)
                for line in self._proc(pid, "status").decode().splitlines()
                if ":" in line
            )
            require(
                rows.get("Uid", "").split() == ["0"] * 4 and accepted["uid"] == 0,
                "native_warm_credentials_changed",
            )
            require(
                self._cache_mount(pid, "ro") == cache["pid1"], "native_warm_cache_mount_changed"
            )
            require(self._start(pid) == start, "native_warm_actor_replaced")
        require(self._boot() == warm["boot_id"], "native_warm_boot_changed")

    def dispatch(self, plan):
        require(sys.platform == "linux", "native_linux_required")
        self._check()
        require(
            set(plan)
            == {
                "executable",
                "argv",
                "environment",
                "cwd",
                "unit",
                "revision",
                "candidate_may_have_written",
                "old_writer_allowed",
            }
            and plan["unit"] == UNIT
            and plan["candidate_may_have_written"] is True
            and plan["old_writer_allowed"] is False
            and plan["argv"] == [plan["executable"], "-P", "-m", "autostop_manager.mcp_server"],
            "native_dispatch_plan_invalid",
        )
        require(os.getcwd() == plan["cwd"], "native_dispatch_cwd_changed")
        self._check()
        # execve preserves ITIMER_REAL. The guard watchdog must not become an
        # unsolicited termination timer in the accepted Manager process.
        signal.setitimer(signal.ITIMER_REAL, 0)
        os.execve(plan["executable"], plan["argv"], plan["environment"])
        raise NativeError("native_exec_returned")
