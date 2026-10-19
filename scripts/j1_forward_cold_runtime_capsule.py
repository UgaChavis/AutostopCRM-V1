"""A sealed Python runtime with a complete index and no archived-code execution.

The compressed artifact is one typed cold-v4 leaf. Its expanded files are
streamed and hashed against an exact index, not recursively kept in the control
archive's byte dictionary. Live admission separately compares the installed
tree with this index. Neither an index nor a restored tree is an activation
permission.
"""

from __future__ import annotations

import gzip
import hashlib
import io as streams
import os
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.cold-runtime-index.v2"
FIELDS = {"schema", "scope", "binding", "runtime_roots", "capsule_ref", "entries", "unpacked_bytes"}
ENTRY_FIELDS = {"name", "kind", "mode", "bytes", "sha256", "target"}
MAX_ENTRIES = 32768
MAX_EXPANDED_BYTES = 1536 * 1024**2
MAX_FILE_BYTES = 384 * 1024**2
MAX_CAPSULE_BYTES = 192 * 1024**2
CHUNK = 1024**2


def _uid():
    return getattr(os, "geteuid", lambda: 0)()


def _name(value):
    try:
        encoded = value.encode("utf-8") if type(value) is str else b""
    except UnicodeError as exc:
        raise io.Refusal("cold_runtime_member_name") from exc
    cold.need(
        type(value) is str
        and 0 < len(encoded) <= 512
        and str(PurePosixPath(value)) == value
        and not value.startswith("/")
        and not set(value.split("/")) & {"", ".", ".."}
        and not any(ord(char) < 32 or char in "\\:" for char in value)
        and value.split("/")[0] in {"python", "venv"},
        "runtime_member_name",
    )
    return value


def _target(name, target):
    cold.need(
        type(target) is str
        and 0 < len(target.encode("utf-8")) <= 512
        and not target.startswith("/")
        and not any(ord(c) < 32 or c in "\\:" for c in target),
        "runtime_symlink_target",
    )
    parts = list(PurePosixPath(name).parent.parts)
    for part in target.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            cold.need(len(parts) > 1, "runtime_symlink_escape")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def validate_index(document, *, binding, scope):
    cold.fields(document, FIELDS, "runtime_index_fields")
    cold.need(
        document["schema"] == SCHEMA
        and document["scope"] == scope
        and document["binding"] == binding
        and scope in cold.SCOPES,
        "runtime_index_binding",
    )
    cold.reference(document["capsule_ref"])
    roots = cold.fields(document["runtime_roots"], {"python", "venv"}, "runtime_roots")
    for name, value in roots.items():
        cold.need(
            type(value) is str
            and PurePosixPath(value).is_absolute()
            and str(PurePosixPath(value)) == value
            and ".." not in PurePosixPath(value).parts,
            "runtime_root_path",
        )
        if scope == "production_native":
            cold.need(
                value
                == "/opt/autostop-manager-cold-releases/"
                + binding["candidate_manager_sha"]
                + "/"
                + name,
                "runtime_production_root",
            )
    cold.need(
        not any(
            PurePosixPath(a).is_relative_to(b)
            for a, b in ((roots["python"], roots["venv"]), (roots["venv"], roots["python"]))
        ),
        "runtime_roots_overlap",
    )
    rows = document["entries"]
    cold.need(type(rows) is list and 2 <= len(rows) <= MAX_ENTRIES, "runtime_index_count")
    names, total, entries = [], 0, {}
    for row in rows:
        cold.fields(row, ENTRY_FIELDS, "runtime_entry_fields")
        name = _name(row["name"])
        kind, mode = row["kind"], row["mode"]
        cold.need(
            kind in {"file", "directory", "symlink"} and type(mode) is int and 0 <= mode <= 0o777,
            "runtime_entry_kind",
        )
        cold.need(
            type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_FILE_BYTES, "runtime_entry_size"
        )
        if kind == "file":
            cold.need(
                type(row["sha256"]) is str
                and cold.HEX.fullmatch(row["sha256"])
                and row["target"] is None
                and mode & 0o400
                and not mode & 0o022,
                "runtime_file_fields",
            )
            total += row["bytes"]
        else:
            cold.need(row["bytes"] == 0 and row["sha256"] is None, "runtime_metadata_fields")
            if kind == "directory":
                cold.need(
                    row["target"] is None and mode & 0o500 == 0o500 and not mode & 0o022,
                    "runtime_directory_fields",
                )
            else:
                cold.need(mode == 0o777, "runtime_symlink_mode")
                _target(name, row["target"])
        names.append(name)
        entries[name] = row
    cold.need(names == sorted(set(names)), "runtime_index_order")
    cold.need(
        type(document["unpacked_bytes"]) is int
        and document["unpacked_bytes"] == total <= MAX_EXPANDED_BYTES,
        "runtime_expanded_limit",
    )
    for name, row in entries.items():
        if name in roots:
            cold.need(row["kind"] == "directory", "runtime_root_kind")
        else:
            parent = str(PurePosixPath(name).parent)
            cold.need(
                parent in entries and entries[parent]["kind"] == "directory",
                "runtime_missing_parent",
            )
        if row["kind"] == "symlink":
            target, seen = _target(name, row["target"]), {name}
            while target in entries and entries[target]["kind"] == "symlink":
                cold.need(target not in seen and len(seen) < 32, "runtime_symlink_cycle")
                seen.add(target)
                target = _target(target, entries[target]["target"])
            cold.need(
                target in entries and entries[target]["kind"] in {"file", "directory"},
                "runtime_symlink_missing",
            )
    cold.need(all(name in entries for name in roots), "runtime_root_missing")
    if scope == "production_native":
        cold.need(
            all(
                name in entries and entries[name]["kind"] == "file"
                for name in ("python/bin/python3.12", "venv/bin/python3.12", "venv/pyvenv.cfg")
            ),
            "runtime_physical_python_and_cfg_required",
        )
        cold.need(entries["venv/pyvenv.cfg"]["bytes"] <= 65536, "runtime_cfg_size")
    return entries


def _metadata(info):
    return tuple(
        getattr(info, key)
        for key in (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
        )
    )


def _file_hash(path, *, copy=None):
    """The same held descriptor supplies the digest and optional capsule bytes."""
    before = path.lstat()
    cold.need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_uid in {0, _uid()}
        and (os.name == "nt" or not before.st_mode & 0o022)
        and before.st_size <= MAX_FILE_BYTES,
        "runtime_installed_file",
    )
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        cold.need(_metadata(os.fstat(fd)) == _metadata(before), "runtime_file_changed")
        digest, count = hashlib.sha256(), 0
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while raw := stream.read(CHUNK):
                count += len(raw)
                cold.need(count <= before.st_size, "runtime_file_changed")
                digest.update(raw)
                if copy is not None:
                    copy.write(raw)
        cold.need(
            count == before.st_size
            and _metadata(os.fstat(fd)) == _metadata(before)
            and _metadata(path.lstat()) == _metadata(before),
            "runtime_file_changed",
        )
        return digest.hexdigest()
    finally:
        os.close(fd)


def installed_entries(roots, *, permission_index=None):
    """Measure every file/link/directory. Extra dispatchable files are not hidden."""
    cold.fields(roots, {"python", "venv"}, "runtime_roots")
    result = []
    for key, root in sorted(roots.items()):
        root = Path(root)
        pending = [(key, root)]
        while pending:
            name, path = pending.pop()
            _name(name)
            info = path.lstat()
            cold.need(info.st_uid in {0, _uid()}, "runtime_installed_owner")
            mode = stat.S_IMODE(info.st_mode)
            if os.name == "nt":
                # Windows restore checks Unix permissions as archive metadata,
                # never as a live native custody observation.
                mode = (
                    permission_index[name]["mode"]
                    if permission_index is not None and name in permission_index
                    else 0o755
                    if stat.S_ISDIR(info.st_mode)
                    else 0o644
                )
            row = {
                "name": name,
                "kind": None,
                "mode": mode,
                "bytes": 0,
                "sha256": None,
                "target": None,
            }
            if stat.S_ISDIR(info.st_mode):
                cold.need(not mode & 0o022 and mode & 0o500 == 0o500, "runtime_installed_directory")
                row["kind"] = "directory"
                before = _metadata(info)
                pending.extend((name + "/" + item.name, item) for item in sorted(path.iterdir()))
                cold.need(_metadata(path.lstat()) == before, "runtime_directory_changed")
            elif stat.S_ISLNK(info.st_mode):
                row.update(kind="symlink", target=os.readlink(path))
                _target(name, row["target"])
                cold.need(_metadata(path.lstat()) == _metadata(info), "runtime_symlink_changed")
            else:
                row.update(kind="file", bytes=info.st_size, sha256=_file_hash(path))
            result.append(row)
            cold.need(len(result) <= MAX_ENTRIES, "runtime_index_count")
    return sorted(result, key=lambda row: row["name"])


def verify_installed(document):
    cold.need(
        document["scope"] != "production_native" or os.name == "posix", "runtime_native_platform"
    )
    expected = validate_index(document, binding=document["binding"], scope=document["scope"])
    observed = installed_entries(document["runtime_roots"], permission_index=expected)
    cold.need(observed == list(expected.values()), "runtime_installed_index_changed")
    if document["scope"] == "production_native":
        target = streams.BytesIO()
        digest = _file_hash(Path(document["runtime_roots"]["venv"]) / "pyvenv.cfg", copy=target)
        cold.need(digest == expected["venv/pyvenv.cfg"]["sha256"], "runtime_cfg_changed")
        _venv_configuration(target.getvalue(), document["runtime_roots"])
    return {
        "files": sum(row["kind"] == "file" for row in observed),
        "bytes": document["unpacked_bytes"],
    }


class _CompressedSource:
    def __init__(self, source):
        self.source, self.digest, self.count = source, hashlib.sha256(), 0

    def read(self, size=-1):
        raw = self.source.read(CHUNK if size < 0 else min(size, CHUNK))
        self.count += len(raw)
        cold.need(self.count <= MAX_CAPSULE_BYTES, "runtime_capsule_limit")
        self.digest.update(raw)
        return raw


def _tar_header(row):
    entry = tarfile.TarInfo(row["name"])
    entry.mode, entry.mtime, entry.uid, entry.gid = row["mode"], 0, 0, 0
    if row["kind"] == "file":
        entry.type, entry.size = tarfile.REGTYPE, row["bytes"]
    elif row["kind"] == "directory":
        entry.type = tarfile.DIRTYPE
    else:
        entry.type, entry.linkname = tarfile.SYMTYPE, row["target"]
    return entry


class _RuntimeTarInfo(tarfile.TarInfo):
    def _proc_pax(self, archive):
        cold.need(self.type == tarfile.XHDTYPE and 0 <= self.size <= 4096, "runtime_tar_pax_limit")
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        raise io.Refusal("cold_runtime_tar_gnu_metadata")

    def _proc_sparse(self, archive):
        raise io.Refusal("cold_runtime_tar_sparse")


def produce(path, *, roots, binding, scope):
    """Write a new private capsule from exact installed bytes, never execute them."""
    path = Path(path)
    entries = installed_entries(roots)
    document = {
        "schema": SCHEMA,
        "scope": scope,
        "binding": binding,
        "runtime_roots": dict(roots),
        "capsule_ref": {"path": str(path), "sha256": "0" * 64},
        "entries": entries,
        "unpacked_bytes": sum(row["bytes"] for row in entries),
    }
    validate_index(document, binding=binding, scope=scope)
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        with gzip.GzipFile(
            fileobj=output, mode="wb", filename="", mtime=0, compresslevel=6
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for row in entries:
                    header = _tar_header(row)
                    root, *parts = PurePosixPath(row["name"]).parts
                    source = Path(roots[root]).joinpath(*parts)
                    if row["kind"] == "file":
                        # The stream is bounded by TarInfo.size; remeasurement
                        # below detects any changed bytes or topology.
                        with source.open("rb") as stream:
                            archive.addfile(header, stream)
                    else:
                        archive.addfile(header)
                    cold.need(output.tell() <= MAX_CAPSULE_BYTES, "runtime_capsule_limit")
        output.flush()
        os.fsync(output.fileno())
    io.fsync_directory(path.parent)
    cold.need(path.stat().st_size <= MAX_CAPSULE_BYTES, "runtime_capsule_limit")
    document["capsule_ref"]["sha256"] = _file_hash(path)
    verify_installed(document)
    # Validate the actual compressed representation too, not just source data.
    with path.open("rb") as stream:
        verify_archive(document, stream)
    return document


def _venv_configuration(raw, roots):
    cold.need(len(raw) <= 65536 and b"\0" not in raw, "runtime_cfg_size")
    try:
        pairs = [line.split("=", 1) for line in raw.decode("utf-8").splitlines() if line.strip()]
        cold.need(all(len(pair) == 2 for pair in pairs), "runtime_cfg_shape")
        values = {key.strip(): value.strip() for key, value in pairs}
    except UnicodeError as exc:
        raise io.Refusal("cold_runtime_cfg_encoding") from exc
    cold.need(
        len(values) == len(pairs)
        and values.get("home") == roots["python"] + "/bin"
        and values.get("include-system-site-packages") == "false"
        and values.get("executable") == roots["python"] + "/bin/python3.12"
        and values.get("version", "").startswith("3.12."),
        "runtime_cfg_external_prefix",
    )


def _restore_file(archive, member, row, target, *, configuration=None):
    source = archive.extractfile(member)
    cold.need(source is not None, "runtime_tar_file_missing")
    destination = None if target is None else target.open("xb")
    digest, count, raw_config = hashlib.sha256(), 0, bytearray()
    try:
        while raw := source.read(CHUNK):
            count += len(raw)
            cold.need(count <= row["bytes"], "runtime_tar_file_overflow")
            digest.update(raw)
            if configuration is not None:
                raw_config.extend(raw)
                cold.need(len(raw_config) <= 65536, "runtime_cfg_size")
            if destination is not None:
                destination.write(raw)
        cold.need(
            count == row["bytes"] and digest.hexdigest() == row["sha256"], "runtime_tar_file_hash"
        )
        if destination is not None:
            destination.flush()
            os.fsync(destination.fileno())
    finally:
        source.close()
        if destination is not None:
            destination.close()
    if target is not None:
        target.chmod(row["mode"])
    if configuration is not None:
        _venv_configuration(bytes(raw_config), configuration)


def verify_archive(document, source, *, destination=None):
    """Independently decode/hash every member, optionally restore to an empty tree."""
    rows = validate_index(document, binding=document["binding"], scope=document["scope"])
    if destination is not None:
        destination = Path(destination)
        cold.need(
            destination.is_dir()
            and not destination.is_symlink()
            and not any(destination.iterdir()),
            "runtime_restore_empty_directory",
        )
    links, count, ordered = [], 0, list(rows.values())
    checked = _CompressedSource(source)
    try:
        with gzip.GzipFile(fileobj=checked, mode="rb") as compressed:
            with tarfile.open(fileobj=compressed, mode="r|", tarinfo=_RuntimeTarInfo) as archive:
                for member in archive:
                    cold.need(count < len(rows), "runtime_tar_extra_member")
                    row = ordered[count]
                    header = _tar_header(row)
                    cold.need(
                        member.name
                        in (
                            {row["name"], row["name"] + "/"}
                            if row["kind"] == "directory"
                            else {row["name"]}
                        )
                        and member.type == header.type
                        and member.mode == header.mode
                        and member.size == header.size
                        and member.linkname == header.linkname
                        and member.uid == member.gid == member.mtime == 0
                        and not member.uname
                        and not member.gname
                        and set(member.pax_headers) <= {"path"},
                        "runtime_tar_member_binding",
                    )
                    target = (
                        None
                        if destination is None
                        else destination.joinpath(*PurePosixPath(row["name"]).parts)
                    )
                    if row["kind"] == "file":
                        configuration = (
                            document["runtime_roots"]
                            if document["scope"] == "production_native"
                            and row["name"] == "venv/pyvenv.cfg"
                            else None
                        )
                        _restore_file(archive, member, row, target, configuration=configuration)
                    elif row["kind"] == "directory" and target is not None:
                        target.mkdir(mode=row["mode"])
                        target.chmod(row["mode"])
                    elif row["kind"] == "symlink" and target is not None:
                        links.append((target, row["target"]))
                    count += 1
                # Reject hidden second archives, nonzero padding and a gzip
                # expansion bomb after the declared member population.
                tail = archive.fileobj.read(10241)
                cold.need(
                    len(tail) <= 10240 and not tail.strip(b"\0"), "runtime_tar_trailing_bytes"
                )
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise io.Refusal("cold_runtime_capsule_encoding") from exc
    cold.need(count == len(rows), "runtime_tar_missing_member")
    cold.need(
        not checked.read(1) and checked.digest.hexdigest() == document["capsule_ref"]["sha256"],
        "runtime_capsule_hash",
    )
    for target, link in links:
        target.symlink_to(
            link,
            target_is_directory=rows[_target(str(target.relative_to(destination)), link)]["kind"]
            == "directory",
        )
    if destination is not None:
        restored = {name: str(destination / name) for name in document["runtime_roots"]}
        cold.need(
            installed_entries(restored, permission_index=rows) == list(rows.values()),
            "runtime_restored_index_changed",
        )
        for directory in sorted(
            (path for path in destination.rglob("*") if path.is_dir() and not path.is_symlink()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            io.fsync_directory(directory)
        io.fsync_directory(destination)
    return {
        "schema": "autostop.j1.cold-runtime-restored.v1",
        "members": count,
        "files": sum(row["kind"] == "file" for row in rows.values()),
        "bytes": document["unpacked_bytes"],
        "archived_code_executed": False,
    }


def materialize(document, *, read_reference):
    """Inert exact-root installation. Caller retains its original deployment lock.

    Existing roots are never replaced. A crash after one rename can resume only
    with the same complete index; a conflicting or partially populated root
    refuses before activation. No environment/cfg prefix is rewritten.
    """
    rows = validate_index(document, binding=document["binding"], scope=document["scope"])
    roots = {name: Path(path) for name, path in document["runtime_roots"].items()}
    parent = roots["python"].parent
    cold.need(roots["venv"].parent == parent, "runtime_install_common_parent")
    if document["scope"] == "production_native":
        cold.need(os.name == "posix" and _uid() == 0, "runtime_install_root_required")
        io.protected_directory(parent)
    else:
        info = parent.lstat()
        cold.need(
            stat.S_ISDIR(info.st_mode)
            and not parent.is_symlink()
            and info.st_uid == _uid()
            and (os.name == "nt" or not info.st_mode & 0o022),
            "runtime_install_private_parent",
        )
    raw = cold.opaque_reader(read_reference, capsule=True)(document["capsule_ref"])
    cold.need(
        type(raw) is bytes
        and len(raw) <= MAX_CAPSULE_BYTES
        and io.sha(raw) == document["capsule_ref"]["sha256"],
        "runtime_capsule_reference",
    )
    with tempfile.TemporaryDirectory(prefix=".runtime-stage-", dir=parent) as temporary:
        staging = Path(temporary)
        receipt = verify_archive(document, streams.BytesIO(raw), destination=staging)
        existing = {name: target.exists() or target.is_symlink() for name, target in roots.items()}
        for name, target in roots.items():
            if existing[name]:
                cold.need(
                    target.is_dir() and not target.is_symlink(), "runtime_install_existing_root"
                )
        observed = installed_entries(
            {
                name: str(target if existing[name] else staging / name)
                for name, target in roots.items()
            }
        )
        cold.need(observed == list(rows.values()), "runtime_install_existing_conflict")
        for name, target in roots.items():
            if not existing[name]:
                os.rename(staging / name, target)
                io.fsync_directory(parent)
    verify_installed(document)
    return {**receipt, "runtime_roots": document["runtime_roots"], "activation_authorized": False}


def validate(document, *, binding, scope, read_reference):
    validate_index(document, binding=binding, scope=scope)
    reader = cold.opaque_reader(read_reference, capsule=True)
    raw = reader(document["capsule_ref"])
    cold.need(
        type(raw) is bytes
        and len(raw) <= MAX_CAPSULE_BYTES
        and io.sha(raw) == document["capsule_ref"]["sha256"],
        "runtime_capsule_reference",
    )
    return verify_archive(document, streams.BytesIO(raw))


def allowed_paths(document):
    rows = validate_index(document, binding=document["binding"], scope=document["scope"])
    return {
        str(
            PurePosixPath(document["runtime_roots"][row["name"].split("/")[0]]).joinpath(
                *PurePosixPath(row["name"]).parts[1:]
            )
        ): row["sha256"]
        for row in rows.values()
        if row["kind"] == "file"
    }
