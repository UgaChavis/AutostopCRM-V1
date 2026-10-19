"""Offline, source-only preparation; never imports or launches Manager code.

Cached GitHub responses are consistency evidence, not authenticated fresh CI or
native acceptance. This command deliberately has no install/apply operation.
Checkouts must be byte-clean: CRLF conversion and Git filters are not applied.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.j1_forward_io import Refusal, canonical, document, require, sha  # noqa: E402

INPUT_SCHEMA = "autostop.j1.manager-transition-source-inputs.v1"
PACKAGE_SCHEMA = "autostop.j1.manager-predecessor-package.v1"
INDEX_SCHEMA = "autostop.j1.manager-git-source.v1"
LIMIT = 128 * 1024**2
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
REPOSITORIES = {"manager": "UgaChavis/AutostopManager", "crm": "UgaChavis/AutostopCRM-V1"}
JOBS = {
    "manager": {"quality"},
    "crm": {"docker-runtime-assets", "static", "unit", "browser-performance", "quality"},
}


def physical_directory(path: Path) -> Path:
    require(path.is_absolute() and ".." not in path.parts, "absolute_physical_path_required")
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400,
            "physical_directory_required",
        )
    return path


def stable_read(path: Path | str, limit: int = LIMIT) -> bytes:
    """Rootless source input reader; rejects aliases, hardlinks and changed files."""
    path = Path(path)
    physical_directory(path.parent)
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and not getattr(before, "st_file_attributes", 0) & 0x400
        and before.st_size <= limit,
        "source_regular_file_required",
    )
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, final = os.fstat(descriptor), path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        require(
            len(raw) == before.st_size <= limit
            and all(
                getattr(before, field)
                == getattr(opened, field)
                == getattr(after, field)
                == getattr(final, field)
                for field in fields
            )
            and before.st_ctime_ns == final.st_ctime_ns
            and opened.st_ctime_ns == after.st_ctime_ns,
            "source_file_changed",
        )
        return raw
    finally:
        os.close(descriptor)


def reference_bytes(ref: dict, base: Path | None = None) -> bytes:
    require(type(ref) is dict and set(ref) == {"path", "sha256"}, "source_reference_shape")
    require(
        type(ref["path"]) is str and type(ref["sha256"]) is str and HEX64.fullmatch(ref["sha256"]),
        "source_reference_invalid",
    )
    path = Path(ref["path"])
    if not path.is_absolute() and base is not None:
        path = base / relative_name(ref["path"])
    raw = stable_read(path)
    require(sha(raw) == ref["sha256"], "source_reference_hash_mismatch")
    return raw


def relative_name(name: str) -> str:
    require(type(name) is str and bool(name), "source_path_invalid")
    parts = name.split("/")
    require(
        not PurePosixPath(name).is_absolute()
        and all(part and part not in {".", ".."} for part in parts)
        and not any(character in name for character in "\\:")
        and all(ord(character) >= 32 and ord(character) != 127 for character in name)
        and all(
            not part.endswith((".", " "))
            and part.casefold() != ".git"
            and not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
            for part in parts
        ),
        "source_path_invalid",
    )
    return name


def _git(repository: Path, arguments: list[str], payload: bytes | None = None) -> bytes:
    executable = shutil.which("git")
    require(executable is not None, "git_required")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_OPTIONAL_LOCKS="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
        LC_ALL="C",
    )
    command = [executable, "--no-pager", "-C", str(repository)]
    for setting in (
        "core.fsmonitor=false",
        "core.untrackedCache=false",
        "core.hooksPath=/dev/null",
        "protocol.allow=never",
        "gc.auto=0",
    ):
        command.extend(("-c", setting))
    try:
        result = subprocess.run(
            [*command, *arguments],
            input=payload,
            capture_output=True,
            timeout=45,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal("source_git_unavailable") from exc
    require(result.returncode == 0, "source_git_failed")
    require(len(result.stdout) <= LIMIT, "source_git_output_limit")
    return result.stdout


def _clean(repository: Path, revision: str) -> None:
    require(
        _git(repository, ["rev-parse", "HEAD"]).decode().strip() == revision, "source_head_changed"
    )
    require(
        not _git(repository, ["ls-files", "--others", "--exclude-standard", "-z"]),
        "source_checkout_untracked",
    )
    flags = _git(repository, ["ls-files", "-v", "-z"]).split(b"\0")
    require(all(not item or item.startswith(b"H ") for item in flags), "source_hidden_index_flags")


def _safe_git_config(repository: Path) -> None:
    # No status/diff/checkout or other worktree conversion command is ever used.
    keys = _git(
        repository, ["config", "--local", "--no-includes", "--name-only", "--null", "--list"]
    )
    names = [key.decode("utf-8", "strict").casefold() for key in keys.split(b"\0") if key]
    if "extensions.worktreeconfig" in names:
        keys = _git(
            repository, ["config", "--worktree", "--no-includes", "--name-only", "--null", "--list"]
        )
        names.extend(key.decode("utf-8", "strict").casefold() for key in keys.split(b"\0") if key)
    require(
        not any(
            key.startswith(("filter.", "include.", "includeif.")) or key == "core.attributesfile"
            for key in names
        ),
        "source_git_executable_configuration",
    )


def _working_copy(repository: Path, files: list[dict], contents: dict[str, bytes]) -> None:
    expected = {}
    for item in files:
        path = repository / item["path"]
        require(stable_read(path) == contents[item["path"]], "source_checkout_bytes_changed")
        if os.name != "nt":
            require(
                bool(path.stat().st_mode & 0o111) == (item["mode"] == "100755"),
                "source_checkout_mode_changed",
            )
        expected[item["path"]] = (item["mode"], item["git_oid"], "0")
    actual = {}
    for row in filter(None, _git(repository, ["ls-files", "--stage", "-z"]).split(b"\0")):
        fields, name = row.split(b"\t", 1)
        name = name.decode("utf-8", "strict")
        require(name not in actual, "source_index_conflict")
        actual[name] = tuple(fields.decode("ascii").split())
    require(actual == expected, "source_index_changed")


def _objects(repository: Path, identifiers: list[str]) -> dict[str, tuple[str, bytes]]:
    raw = _git(repository, ["cat-file", "--batch"], "\n".join(identifiers).encode() + b"\n")
    result, offset = {}, 0
    for identifier in identifiers:
        end = raw.find(b"\n", offset)
        require(end >= offset, "git_object_header_invalid")
        header = raw[offset:end].decode("ascii").split()
        require(len(header) == 3 and header[0] == identifier, "git_object_missing")
        kind, size = header[1], int(header[2])
        require(kind in {"commit", "tree", "blob"} and 0 <= size <= LIMIT, "git_object_invalid")
        payload = raw[end + 1 : end + 1 + size]
        require(
            len(payload) == size
            and raw[end + 1 + size : end + 2 + size] == b"\n"
            and hashlib.sha1(f"{kind} {size}\0".encode() + payload).hexdigest() == identifier,
            "git_object_content_changed",
        )
        result[identifier] = (kind, payload)
        offset = end + 2 + size
    require(offset == len(raw), "git_object_trailing_data")
    return result


def collect_source(
    repository: Path | str, revision: str, tree: str
) -> tuple[dict, dict[str, bytes]]:
    repository = physical_directory(Path(repository))
    require(type(revision) is str and HEX40.fullmatch(revision), "source_revision_invalid")
    require(type(tree) is str and HEX40.fullmatch(tree), "source_tree_invalid")
    actual_root = _git(repository, ["rev-parse", "--show-toplevel"]).decode().strip()
    require(Path(actual_root).resolve() == repository, "source_repository_root_required")
    _safe_git_config(repository)
    _clean(repository, revision)
    rows = _git(repository, ["ls-tree", "-rztl", "--full-tree", revision]).split(b"\0")
    entries, names, total = [], set(), 0
    for row in filter(None, rows):
        fields, name = row.split(b"\t", 1)
        mode, kind, identifier, size = fields.decode("ascii").split()
        name = relative_name(name.decode("utf-8", "strict"))
        require(name.casefold() not in names, "source_path_collision")
        names.add(name.casefold())
        require(
            (kind == "tree" and mode == "040000")
            or (kind == "blob" and mode in {"100644", "100755"}),
            "source_nonregular_entry",
        )
        require(HEX40.fullmatch(identifier), "source_object_invalid")
        total += 0 if kind == "tree" else int(size)
        require(total <= LIMIT // 2, "source_size_limit")
        entries.append((name, mode, kind, identifier))
    objects = _objects(repository, list(dict.fromkeys([revision, tree, *(e[3] for e in entries)])))
    require(
        objects[revision][0] == "commit"
        and objects[revision][1].startswith(f"tree {tree}\n".encode()),
        "source_tree_mismatch",
    )
    require(objects[tree][0] == "tree", "source_tree_mismatch")
    files, contents = [], {}
    for name, mode, kind, identifier in entries:
        actual_kind, raw = objects[identifier]
        require(actual_kind == kind, "source_object_type_mismatch")
        if kind == "blob":
            contents[name] = raw
            files.append(
                {
                    "path": name,
                    "mode": mode,
                    "size": len(raw),
                    "sha256": sha(raw),
                    "git_oid": identifier,
                }
            )
    require(bool(files), "source_empty")
    _working_copy(repository, files, contents)
    _safe_git_config(repository)
    _clean(repository, revision)
    return {
        "schema": INDEX_SCHEMA,
        "revision": revision,
        "tree": tree,
        "files": sorted(files, key=lambda item: item["path"]),
    }, contents


def cached_ci(value: dict, project: str, revision: str) -> dict:
    require(type(value) is dict and set(value) == {"run", "jobs"}, "cached_ci_shape")
    run, jobs = value["run"], value["jobs"]
    require(type(run) is dict and type(jobs) is dict, "cached_ci_shape")
    require(
        type(run.get("repository")) is dict and type(run.get("head_repository")) is dict,
        "cached_ci_repository_shape",
    )
    repository = REPOSITORIES[project]
    run_id, attempt = run.get("id"), run.get("run_attempt")
    require(
        type(run_id) is int
        and run_id > 0
        and type(attempt) is int
        and attempt > 0
        and run.get("repository", {}).get("full_name") == repository
        and run.get("head_repository", {}).get("full_name") == repository
        and run.get("head_sha") == revision
        and type(run.get("head_branch")) is str
        and bool(run["head_branch"])
        and type(run.get("event")) is str
        and run["event"] in {"push", "pull_request", "workflow_dispatch"}
        and run.get("path") == ".github/workflows/quality.yml"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("html_url") == f"https://github.com/{repository}/actions/runs/{run_id}",
        "cached_ci_run_mismatch",
    )
    records = jobs.get("jobs")
    require(
        type(records) is list
        and type(jobs.get("total_count")) is int
        and jobs["total_count"] == len(JOBS[project]) == len(records)
        and all(
            type(item) is dict and type(item.get("name")) is str and type(item.get("id")) is int
            for item in records
        )
        and {item.get("name") for item in records} == JOBS[project]
        and len({item.get("id") for item in records}) == len(records)
        and all(
            type(item.get("id")) is int
            and item["id"] > 0
            and type(item.get("run_id")) is int
            and type(item.get("run_attempt")) is int
            and item["run_id"] == run_id
            and item["run_attempt"] == attempt
            and item.get("head_sha") == revision
            and item.get("status") == "completed"
            and item.get("conclusion") == "success"
            for item in records
        ),
        "cached_ci_jobs_mismatch",
    )
    return {
        "repository": repository,
        "revision": revision,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_branch": run["head_branch"],
        "event": run["event"],
        "jobs": sorted(JOBS[project]),
        "run_url": run["html_url"],
        "conclusion": "success",
        "evidence_kind": "cached_offline_consistency",
        "fresh_github_verified": False,
        "native_acceptance": False,
        "activation_authorized": False,
    }


class PredecessorPackage:
    """Map immutable logical references to pinned local copies, without rewriting receipts."""

    def __init__(self, reference: dict):
        self.reference = reference
        self.raw = reference_bytes(reference)
        value = document(self.raw)
        require(
            type(value) is dict and set(value) == {"schema", "export", "documents"},
            "predecessor_package_shape",
        )
        require(
            value["schema"] == PACKAGE_SCHEMA and type(value["export"]) is dict,
            "predecessor_package_schema",
        )
        require(
            type(value["documents"]) is list and bool(value["documents"]),
            "predecessor_raw_documents_required",
        )
        self.export = value["export"]
        self.records: dict[str, tuple[dict, Path, bytes]] = {}
        names = set()
        for record in value["documents"]:
            require(
                type(record) is dict and set(record) == {"reference", "file"},
                "predecessor_document_shape",
            )
            name = relative_name(record["file"])
            ref = record["reference"]
            require(
                type(ref) is dict and set(ref) == {"path", "sha256"}, "predecessor_reference_shape"
            )
            require(
                type(ref["path"]) is str
                and ref["path"].startswith("/")
                and str(PurePosixPath(ref["path"])) == ref["path"]
                and ".." not in ref["path"].split("/")
                and "\\" not in ref["path"]
                and all(ord(character) >= 32 for character in ref["path"]),
                "predecessor_logical_path",
            )
            require(
                ref["path"] not in self.records and name.casefold() not in names,
                "predecessor_document_duplicate",
            )
            path = Path(reference["path"]).parent / name
            raw = reference_bytes({"path": str(path), "sha256": ref["sha256"]})
            self.records[ref["path"]] = (dict(ref), path, raw)
            names.add(name.casefold())

    def read(self, ref: dict) -> bytes:
        require(
            type(ref) is dict and set(ref) == {"path", "sha256"} and type(ref["path"]) is str,
            "predecessor_reference_shape",
        )
        record = self.records.get(ref["path"])
        require(record is not None and record[0] == ref, "predecessor_original_bytes_required")
        require(stable_read(record[1]) == record[2], "predecessor_document_changed")
        return record[2]

    def recheck(self) -> None:
        require(reference_bytes(self.reference) == self.raw, "predecessor_package_changed")
        for ref, _, _ in self.records.values():
            self.read(ref)


CONSUMERS = (
    "scripts/j1_forward_io.py",
    "scripts/j1_manager_transition.py",
    "scripts/j1_manager_transition_source.py",
)


def _source_input(value: dict, project: str) -> tuple[dict, dict[str, bytes], dict]:
    require(
        type(value) is dict and set(value) == {"checkout", "revision", "tree", "ci"},
        "source_input_shape",
    )
    require(type(value["checkout"]) is str, "source_checkout_path")
    index, contents = collect_source(value["checkout"], value["revision"], value["tree"])
    ci = cached_ci(document(reference_bytes(value["ci"])), project, value["revision"])
    return index, contents, ci


def _compile(input_ref: dict) -> tuple[dict, dict[str, bytes]]:
    from scripts import j1_manager_transition as transition

    raw = reference_bytes(input_ref)
    value = document(raw)
    require(
        type(value) is dict
        and set(value)
        == {"schema", "attempt_id", "predecessor", "manager", "controller", "proposal"},
        "transition_inputs_shape",
    )
    require(value["schema"] == INPUT_SCHEMA, "transition_inputs_schema")
    package = PredecessorPackage(value["predecessor"])
    source, contents, ci = _source_input(value["manager"], "manager")
    controller_source, controller_contents, controller_ci = _source_input(
        value["controller"], "crm"
    )
    root = Path(__file__).resolve().parent.parent
    for name in CONSUMERS:
        require(
            name in controller_contents and stable_read(root / name) == controller_contents[name],
            "controller_consumer_source_mismatch",
        )
    controller = {
        "revision": controller_source["revision"],
        "source_sha256": sha(canonical(controller_source)),
        "ci": controller_ci,
    }
    candidate = {"source": source, "ci": ci}
    capsule = transition.compile_capsule(
        package.export,
        candidate,
        value["proposal"],
        attempt_id=value["attempt_id"],
        controller=controller,
        read=package.read,
    )
    transition.verify_capsule(
        capsule,
        package.export,
        candidate,
        value["proposal"],
        controller=controller,
        read=package.read,
    )
    require(
        capsule.get("native_acceptance") is False and capsule.get("activation_authorized") is False,
        "source_capsule_cannot_authorize_activation",
    )
    package.recheck()
    require(reference_bytes(input_ref) == raw, "transition_inputs_changed")
    for item in (value["manager"], value["controller"]):
        _clean(Path(item["checkout"]), item["revision"])
        reference_bytes(item["ci"])
    artifacts = {
        "capsule.json": canonical(capsule),
        "source-index.json": canonical(source),
        "controller-source-index.json": canonical(controller_source),
        "input-reference.json": canonical(input_ref),
        "inputs.json": raw,
    }
    for name, data in contents.items():
        require(
            name.casefold() not in {"revision", "manifest.sha256", "manifest.files0"},
            "source_reserved_metadata_path",
        )
        artifacts["source/" + name] = data
    artifacts["source/REVISION"] = (source["revision"] + "\n").encode()
    names = sorted([*contents, "REVISION"])
    manifest = "".join(f"{sha(artifacts['source/' + name])}  ./{name}\n" for name in names).encode()
    require(sha(manifest) == capsule["target"]["manifest_sha256"], "source_manifest_contract")
    artifacts["source/MANIFEST.sha256"] = manifest
    artifacts["source/MANIFEST.files0"] = b"".join(f"./{name}".encode() + b"\0" for name in names)
    return {
        "capsule": capsule,
        "source": source,
        "checkouts": [value[name]["checkout"] for name in ("manager", "controller")],
    }, artifacts


def _write_new(path: Path, raw: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        path.chmod(mode)


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _publish(staging: Path, destination: Path) -> None:
    """One atomic no-replace publication; never replaces an existing directory."""
    if os.name == "nt":
        os.rename(staging, destination)
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        require(rename is not None, "atomic_noreplace_unavailable")
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1) != 0:
            raise OSError(ctypes.get_errno(), "atomic_new_directory_failed")
    else:
        raise Refusal("atomic_noreplace_unavailable")


def prepare(inputs: Path | str, expected_sha256: str, output: Path | str) -> dict:
    output = Path(output)
    physical_directory(output.parent)
    require(not output.exists() and not output.is_symlink(), "new_output_directory_required")
    evidence, artifacts = _compile({"path": str(inputs), "sha256": expected_sha256})
    require(
        not any(output.is_relative_to(Path(checkout)) for checkout in evidence["checkouts"]),
        "output_inside_source_checkout",
    )
    modes = {
        "source/" + item["path"]: int(item["mode"][-3:], 8) for item in evidence["source"]["files"]
    }
    modes.update(
        {"source/" + name: 0o644 for name in ("REVISION", "MANIFEST.sha256", "MANIFEST.files0")}
    )
    with tempfile.TemporaryDirectory(
        prefix="." + output.name + "-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "artifact"
        staging.mkdir(mode=0o700)
        for name, raw in artifacts.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_new(path, raw, modes.get(name, 0o600))
        for directory in sorted(
            (path for path in staging.rglob("*") if path.is_dir()), reverse=True
        ):
            _fsync_directory(directory)
        _fsync_directory(staging)
        _publish(staging, output)
        _fsync_directory(output.parent)
    return {
        "ok": True,
        "capsule_sha256": sha(artifacts["capsule.json"]),
        "native_acceptance": False,
        "activation_authorized": False,
    }


def verify(inputs: Path | str, expected_sha256: str, output: Path | str) -> dict:
    output = physical_directory(Path(output))
    evidence, artifacts = _compile({"path": str(inputs), "sha256": expected_sha256})
    actual = set()
    directories = set()
    for name in artifacts:
        directories.update(
            str(parent).replace("\\", "/")
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        )
    for path in output.rglob("*"):
        name = path.relative_to(output).as_posix()
        if path.is_dir():
            physical_directory(path)
            require(name in directories, "source_artifact_extra_directory")
        else:
            actual.add(name)
    require(actual == set(artifacts), "source_artifact_inventory_changed")
    for name, raw in artifacts.items():
        require(stable_read(output / name) == raw, "source_artifact_bytes_changed")
    if os.name != "nt":
        for item in evidence["source"]["files"]:
            require(
                stat.S_IMODE((output / "source" / item["path"]).stat().st_mode)
                == int(item["mode"][-3:], 8),
                "source_artifact_mode_changed",
            )
    return {
        "ok": True,
        "capsule_sha256": sha(artifacts["capsule.json"]),
        "native_acceptance": False,
        "activation_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("prepare", "verify"))
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (prepare if args.operation == "prepare" else verify)(
            args.inputs, args.inputs_sha256, args.output
        )
    except (Refusal, OSError, UnicodeError, ValueError) as exc:
        result = {
            "ok": False,
            "error_code": str(exc) if isinstance(exc, Refusal) else "source_input_unavailable",
            "native_acceptance": False,
            "activation_authorized": False,
        }
    print(canonical(result).decode(), end="")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
