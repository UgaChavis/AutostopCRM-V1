"""Content-addressed online transport cache; never backup/closure acceptance.

Every final payload is re-bound to the independently pinned quiet manifest and
assembled as an ordinary private copy. The backup verifier remains mandatory.
Only a fixed, hash-verified sibling module supplies the approved source plan.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import time
import types
import zlib
from pathlib import Path

BACKUP_SHA256 = "11be895320d1163134620cafa0a7f3a2ffce7eb73e18f05fcd8319340e56efd9"
PROFILE_SHA256 = "66c61b117a07c96a7fa3dc89b35ced9e5a9d840364bf976a0056897ba97bde09"
STAGE_SCHEMA = "autostop.j1.online-transport-cache.v1"
CACHE_SCHEMA = "autostop.j1.verified-transport-inventory.v1"
MAX_MEMBERS, MAX_FILE, MAX_TOTAL = 100_000, 2 * 1024**3, 16 * 1024**3
MAX_MANIFEST, MAX_ENVELOPE = 64 * 1024**2, 16 * 1024**3 + 256 * 1024**2
CHUNK = 1024**2
NAME = re.compile(r"(?:manifest\.json|[0-9a-f]{64}\.blob|[0-9]{6}\.bin)\Z")
CLAIMS = {
    "transport_only": True,
    "all_writers_quiescent_proven": False,
    "backup_acceptance_granted": False,
    "activation_acceptance_granted": False,
}
_CORE = None


def core():
    global _CORE
    if _CORE is None:
        path = Path(__file__).absolute().with_name("j1_forward_backup.py")
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and not getattr(info, "st_file_attributes", 0) & 0x400
            and info.st_size <= CHUNK,
            "backup_module_physical_required",
        )
        with path.open("rb") as stream:
            raw = stream.read(CHUNK + 1)
        require(len(raw) == info.st_size <= CHUNK, "backup_module_size_changed")
        require(hashlib.sha256(raw).hexdigest() == BACKUP_SHA256, "backup_code_hash_mismatch")
        module = types.ModuleType("_j1_transport_verified_backup")
        sys.modules[module.__name__] = module
        exec(compile(raw, str(path), "exec"), module.__dict__)
        require(module.profile_sha256() == PROFILE_SHA256, "backup_profile_hash_mismatch")
        _CORE = module
    return _CORE


class TransportRefusal(ValueError):
    """Non-sensitive failure code."""


def require(condition, code):
    if not condition:
        raise TransportRefusal(code)


def deadline(timeout):
    require(0 < timeout <= 7200, "timeout_invalid")
    return time.monotonic() + timeout


def read_document(path, expected, end):
    b = core()
    require(isinstance(expected, str) and b.HEX.fullmatch(expected), "expected_hash_required")
    require(
        b.physical(path).st_size <= MAX_MANIFEST and b.digest(path, end) == expected,
        "manifest_hash_mismatch",
    )
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected, "manifest_changed")
    return raw, b.document(raw)


def new_directory(path):
    b = core()
    b.protected_parent(path.parent)
    require(not os.path.lexists(path), "new_private_directory_required")
    path.mkdir(mode=0o700)
    b.sync_directory(path.parent)


def stage_assets(outputprivate: Path, *, timeout=3600):
    """Online stable copies of fixed-profile non-SQLite files; no fence API."""
    b, end = core(), deadline(timeout)
    require(b.native_root(), "native_root_stage_required")
    require(
        not any(outputprivate.is_relative_to(Path(path)) for _, path in b.ROOTS),
        "stage_inside_profile_refused",
    )
    items = b.build_profile(deadline=end)
    new_directory(outputprivate)
    sources, blobs, skipped, total = [], {}, [], 0
    for index, item in enumerate(items):
        if item.kind not in ("file", "json", "jsonl"):
            continue
        b.budget(end)
        temporary = outputprivate / f"{index:06d}.pending"
        try:
            require(
                tuple(b.version(b.physical(item.source))) == item.source_version,
                "source_changed_before_stage",
            )
            record = b.copy_stable_file(item.source, temporary, end, MAX_TOTAL - total)
            require(
                tuple(b.version(b.physical(item.source))) == item.source_version,
                "source_changed_after_stage",
            )
        except (TransportRefusal, b.BackupRefusal, FileNotFoundError) as exc:
            code = str(exc) if not isinstance(exc, FileNotFoundError) else "source_disappeared"
            require(
                code
                in {
                    "source_changed_before_stage",
                    "source_changed_after_stage",
                    "source_changed_open",
                    "source_changed_during_copy",
                    "source_disappeared",
                },
                "stage_source_refused",
            )
            if temporary.exists():
                b.physical(temporary)
                temporary.unlink()
            skipped.append(
                {"component": item.component, "logical_name": item.logical_name, "reason": code}
            )
            continue
        sha = record["sha256"]
        target = outputprivate / (sha + ".blob")
        if sha in blobs:
            require(
                blobs[sha] == record["bytes"] and b.digest(target, end) == sha,
                "duplicate_blob_mismatch",
            )
            temporary.unlink()
        else:
            require(not os.path.lexists(target), "unexpected_blob_exists")
            temporary.rename(target)
            blobs[sha] = record["bytes"]
            total += record["bytes"]
        sources.append({"component": item.component, "logical_name": item.logical_name, **record})
    require(len(blobs) + 1 <= MAX_MEMBERS and total <= MAX_TOTAL, "stage_capacity_exceeded")
    b.sync_directory(outputprivate)
    manifest = {
        "schema": STAGE_SCHEMA,
        "profile_sha256": PROFILE_SHA256,
        "sealed": True,
        "blobs": blobs,
        "sources": sources,
        "skipped": skipped,
        **CLAIMS,
    }
    ref = b.publish(outputprivate / "manifest.json", manifest)
    return {
        "manifest": ref,
        "files": len(blobs),
        "bytes": total,
        "skipped_files": len(skipped),
        **CLAIMS,
    }


def validate_blobs(value):
    b = core()
    require(type(value) is dict and len(value) + 1 <= MAX_MEMBERS, "blob_inventory_invalid")
    total = 0
    for sha, size in value.items():
        require(
            isinstance(sha, str)
            and b.HEX.fullmatch(sha)
            and type(size) is int
            and 0 <= size <= MAX_FILE,
            "blob_record_invalid",
        )
        total += size
    require(total <= MAX_TOTAL, "blob_total_limit")
    return value


def stage_manifest(directory, expected, end):
    raw, value = read_document(directory / "manifest.json", expected, end)
    require(
        type(value) is dict
        and value.get("schema") == STAGE_SCHEMA
        and value.get("sealed") is True
        and value.get("profile_sha256") == PROFILE_SHA256
        and all(value.get(k) is v for k, v in CLAIMS.items()),
        "stage_manifest_invalid",
    )
    return raw, validate_blobs(value.get("blobs"))


def pack(output, files, end):
    """Deterministic single-member gzip containing only canonical flat USTAR."""
    b = core()
    b.protected_parent(output.parent)
    require(
        not os.path.lexists(output) and len(files) <= MAX_MEMBERS, "archive_destination_invalid"
    )
    require(len({name for name, _, _, _ in files}) == len(files), "archive_duplicate_name")
    total = 0
    with b.new_file(output) as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=1, mtime=0
        ) as compressed:
            for name, source, size, sha in files:
                b.budget(end)
                require(NAME.fullmatch(name) and 0 <= size <= MAX_FILE, "archive_member_invalid")
                total += size
                require(total <= MAX_TOTAL + MAX_MANIFEST, "archive_total_limit")
                before = b.physical(source)
                require(before.st_size == size, "pack_source_size_mismatch")
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime, info.uid, info.gid = size, 0o600, 0, 0, 0
                compressed.write(info.tobuf(format=tarfile.USTAR_FORMAT))
                digest, copied = hashlib.sha256(), 0
                with source.open("rb") as stream:
                    while block := stream.read(CHUNK):
                        b.budget(end)
                        copied += len(block)
                        require(copied <= size, "pack_source_grew")
                        digest.update(block)
                        compressed.write(block)
                require(
                    copied == size
                    and digest.hexdigest() == sha
                    and b.version(source.lstat()) == b.version(before),
                    "pack_source_changed",
                )
                compressed.write(b"\0" * (-size % 512))
            compressed.write(b"\0" * 1024)
        raw.flush()
        os.fsync(raw.fileno())
    require(output.stat().st_size <= MAX_ENVELOPE, "compressed_size_limit")
    b.sync_directory(output.parent)
    return {
        "archive": {"path": str(output), "sha256": b.digest(output, end)},
        "archive_bytes": output.stat().st_size,
        "payload_bytes": total,
        "members": len(files),
        **CLAIMS,
    }


def bundle_assets(stagedir, manifestsha, outputarchive, *, timeout=3600):
    end = deadline(timeout)
    require(not outputarchive.is_relative_to(stagedir), "archive_inside_evidence")
    raw, blobs = stage_manifest(stagedir, manifestsha, end)
    require(
        {p.name for p in stagedir.iterdir()}
        == {"manifest.json", *(sha + ".blob" for sha in blobs)},
        "stage_inventory_mismatch",
    )
    files = [("manifest.json", stagedir / "manifest.json", len(raw), manifestsha)]
    files.extend(
        (sha + ".blob", stagedir / (sha + ".blob"), size, sha)
        for sha, size in sorted(blobs.items())
    )
    return pack(outputarchive, files, end)


class GzipReader:
    """Bounded streaming inflater; exactly one gzip member and physical EOF."""

    def __init__(self, stream, end):
        self.stream, self.end = stream, end
        self.decoder = zlib.decompressobj(31)
        self.pending, self.produced, self.finished = b"", 0, False

    def read(self, size):
        b = core()
        require(0 < size <= CHUNK, "decompression_read_limit")
        result = bytearray()
        while len(result) < size and not self.finished:
            b.budget(self.end)
            if not self.pending:
                self.pending = self.stream.read(65536)
                require(self.pending, "gzip_truncated")
            try:
                block = self.decoder.decompress(self.pending, size - len(result))
            except zlib.error as exc:
                raise TransportRefusal("gzip_invalid") from exc
            self.pending = self.decoder.unconsumed_tail
            self.produced += len(block)
            require(self.produced <= MAX_ENVELOPE, "decompression_total_limit")
            result.extend(block)
            if self.decoder.eof:
                require(
                    not self.decoder.unused_data and not self.pending and not self.stream.read(1),
                    "gzip_extra_member_or_tail",
                )
                self.finished = True
        return bytes(result)


def extract(archive, archivesha, destination, end):
    b = core()
    require(
        b.physical(archive).st_size <= MAX_ENVELOPE and b.digest(archive, end) == archivesha,
        "archive_hash_mismatch",
    )
    require(not archive.is_relative_to(destination), "archive_output_overlap")
    new_directory(destination)
    seen, total = {}, 0
    with archive.open("rb") as raw:
        reader = GzipReader(raw, end)
        while True:
            header = reader.read(512)
            require(len(header) == 512, "tar_header_truncated")
            if header == b"\0" * 512:
                require(
                    reader.read(512) == b"\0" * 512 and reader.read(1) == b"", "tar_end_invalid"
                )
                break
            require(
                header[257:265] == b"ustar\x0000"
                and not header[345:500].strip(b"\0")
                and header[156:157] == b"0"
                and not header[157:257].strip(b"\0"),
                "tar_extension_or_type_refused",
            )
            try:
                info = tarfile.TarInfo.frombuf(header, "ascii", "strict")
            except (tarfile.HeaderError, UnicodeError, ValueError) as exc:
                raise TransportRefusal("tar_header_invalid") from exc
            require(
                NAME.fullmatch(info.name)
                and info.name not in seen
                and not info.sparse
                and info.mode == 0o600
                and info.uid == info.gid == info.mtime == 0
                and not info.uname
                and not info.gname,
                "tar_name_or_metadata_invalid",
            )
            require(info.tobuf(format=tarfile.USTAR_FORMAT) == header, "tar_noncanonical_header")
            require(
                len(seen) < MAX_MEMBERS
                and 0 <= info.size <= (MAX_MANIFEST if info.name == "manifest.json" else MAX_FILE),
                "tar_member_limit",
            )
            total += info.size
            require(total <= MAX_TOTAL + MAX_MANIFEST, "tar_total_limit")
            target, digest, remaining = destination / info.name, hashlib.sha256(), info.size
            with b.new_file(target) as output:
                while remaining:
                    block = reader.read(min(CHUNK, remaining))
                    require(block, "tar_payload_truncated")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
                output.flush()
                os.fsync(output.fileno())
            padding = reader.read(-info.size % 512) if info.size % 512 else b""
            require(padding == b"\0" * (-info.size % 512), "tar_padding_invalid")
            seen[info.name] = {"bytes": info.size, "sha256": digest.hexdigest()}
    require(b.digest(archive, end) == archivesha, "archive_changed")
    b.sync_directory(destination)
    return seen


def import_assets(archive, archivesha, manifestsha, newcache, *, timeout=3600):
    b, end = core(), deadline(timeout)
    extracted = extract(archive, archivesha, newcache, end)
    _, blobs = stage_manifest(newcache, manifestsha, end)
    require(
        set(extracted) == {"manifest.json", *(sha + ".blob" for sha in blobs)},
        "cache_inventory_mismatch",
    )
    for sha, size in blobs.items():
        require(
            extracted[sha + ".blob"] == {"bytes": size, "sha256": sha}
            and b.digest(newcache / (sha + ".blob"), end) == sha,
            "cache_blob_mismatch",
        )
    inventory = {
        "schema": CACHE_SCHEMA,
        "profile_sha256": PROFILE_SHA256,
        "manifest_sha256": manifestsha,
        "archive_sha256": archivesha,
        "blobs": blobs,
        **CLAIMS,
    }
    return {
        "inventory": b.publish(newcache / "inventory.json", inventory),
        "files": len(blobs),
        "bytes": sum(blobs.values()),
        **CLAIMS,
    }


def cache_inventory(reference, end):
    if reference is None:
        return {}
    require(
        type(reference) is dict and set(reference) == {"path", "sha256"}, "cache_reference_invalid"
    )
    _, value = read_document(Path(reference["path"]), reference["sha256"], end)
    require(
        type(value) is dict
        and value.get("schema") == CACHE_SCHEMA
        and value.get("profile_sha256") == PROFILE_SHA256
        and all(value.get(k) is v for k, v in CLAIMS.items()),
        "cache_inventory_invalid",
    )
    return validate_blobs(value.get("blobs"))


def final_manifest(directory, expected, end):
    b = core()
    raw, value = read_document(directory / "manifest.json", expected, end)
    require(
        type(value) is dict
        and value.get("schema") == b.SCHEMA
        and value.get("complete") is True
        and b.canonical(value.get("profile")) == b.canonical(b.profile_document()),
        "final_manifest_invalid",
    )
    b.check_context(value.get("context"))
    require(all(value.get(key) is False for key in b.FALSE_CLAIMS), "final_manifest_claim_invalid")
    require(
        type(value.get("artifacts")) is list and len(value["artifacts"]) <= b.MAX_FILES,
        "final_inventory_invalid",
    )
    files, total = [], 0
    for item in value["artifacts"]:
        require(type(item) is dict, "final_artifact_invalid")
        if "payload" not in item:
            continue
        name, sha, size = item["payload"], item.get("sha256"), item.get("bytes")
        require(
            name == f"{len(files):06d}.bin"
            and type(size) is int
            and 0 <= size <= MAX_FILE
            and isinstance(sha, str)
            and b.HEX.fullmatch(sha),
            "final_payload_invalid",
        )
        total += size
        require(total <= MAX_TOTAL, "final_total_limit")
        files.append((name, size, sha))
    require(files and len(files) + 1 <= MAX_MEMBERS, "final_payload_count_invalid")
    return raw, files


def bundle_delta(backupdir, manifestsha, known_cache_inventory_ref, outputarchive, *, timeout=3600):
    b, end = core(), deadline(timeout)
    require(not outputarchive.is_relative_to(backupdir), "archive_inside_evidence")
    raw, files = final_manifest(backupdir, manifestsha, end)
    require(
        {p.name for p in backupdir.iterdir()} == {"manifest.json", *(name for name, _, _ in files)},
        "final_directory_mismatch",
    )
    known = cache_inventory(known_cache_inventory_ref, end)
    packed = [("manifest.json", backupdir / "manifest.json", len(raw), manifestsha)]
    for name, size, sha in files:
        source = backupdir / name
        require(
            b.physical(source).st_size == size and b.digest(source, end) == sha,
            "final_payload_hash_mismatch",
        )
        # The remote inventory is an untrusted bandwidth hint, never acceptance.
        if known.get(sha) != size:
            packed.append((name, source, size, sha))
    result = pack(outputarchive, packed, end)
    require(b.digest(backupdir / "manifest.json", end) == manifestsha, "final_manifest_changed")
    return {**result, "payloads_from_cache_hint": len(files) + 1 - len(packed)}


def assemble_delta(archive, archivesha, manifestsha, cache_dir, newbackupdir, *, timeout=3600):
    b, end = core(), deadline(timeout)
    b.physical(cache_dir, directory=True)
    require(
        not newbackupdir.is_relative_to(cache_dir) and not cache_dir.is_relative_to(newbackupdir),
        "cache_output_overlap",
    )
    extracted = extract(archive, archivesha, newbackupdir, end)
    _, files = final_manifest(newbackupdir, manifestsha, end)
    require(
        set(extracted) <= {"manifest.json", *(name for name, _, _ in files)},
        "unexpected_delta_payload",
    )
    from_cache = 0
    for name, size, sha in files:
        target = newbackupdir / name
        if name in extracted:
            require(extracted[name] == {"bytes": size, "sha256": sha}, "delta_payload_mismatch")
        else:
            cached = cache_dir / (sha + ".blob")
            require(
                b.physical(cached).st_size == size and b.digest(cached, end) == sha,
                "required_cache_blob_invalid",
            )
            require(
                b.copy_stable_file(cached, target, end) == {"bytes": size, "sha256": sha},
                "cache_copy_mismatch",
            )
            from_cache += 1
        require(
            b.physical(target).st_size == size and b.digest(target, end) == sha,
            "assembled_payload_mismatch",
        )
    require(
        b.digest(newbackupdir / "manifest.json", end) == manifestsha
        and {p.name for p in newbackupdir.iterdir()}
        == {"manifest.json", *(name for name, _, _ in files)},
        "assembled_inventory_mismatch",
    )
    b.sync_directory(newbackupdir)
    return {
        "manifest_sha256": manifestsha,
        "files": len(files),
        "bytes": sum(size for _, size, _ in files),
        "payloads_from_cache": from_cache,
        "backup_verification_required": True,
        **CLAIMS,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("stage", "bundle-assets", "import-assets", "bundle-delta", "assemble-delta"),
    )
    for name in ("source", "output", "archive", "cache", "inventory-ref"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    try:
        if args.operation == "stage":
            result = stage_assets(args.output)
        elif args.operation == "bundle-assets":
            result = bundle_assets(args.source, args.manifest_sha256, args.output)
        elif args.operation == "import-assets":
            result = import_assets(
                args.archive, args.archive_sha256, args.manifest_sha256, args.output
            )
        elif args.operation == "bundle-delta":
            ref = core().document(args.inventory_ref.read_bytes()) if args.inventory_ref else None
            result = bundle_delta(args.source, args.manifest_sha256, ref, args.output)
        else:
            result = assemble_delta(
                args.archive, args.archive_sha256, args.manifest_sha256, args.cache, args.output
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print('{"ok":false,"error":"transport_operation_refused"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
