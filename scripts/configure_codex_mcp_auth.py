from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
import sys
from collections import Counter
from pathlib import Path

SERVER_TOKEN_KEY = "MINIMAL_KANBAN_MCP_BEARER_TOKEN"
DEFAULT_SERVER_ENV = Path("/opt/autostopcrm/.env")
MAX_SECRET_FILE_BYTES = 4096
MAX_AUTH_FILE_BYTES = 2 * 1024 * 1024
AUTH_BACKUP_VERSION = 1
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")

# Secrets created anywhere in this process must remain private, including when a
# caller supplies a permissive shell umask.
os.umask(0o077)


class AuthConfigError(RuntimeError):
    pass


def _read_small_secret_file(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(MAX_SECRET_FILE_BYTES + 1)
    if len(raw) > MAX_SECRET_FILE_BYTES:
        raise AuthConfigError("Token file is too large")
    text = raw.decode("utf-8").strip()
    match = _KEY_VALUE_RE.fullmatch(text)
    if match and match.group(1) == SERVER_TOKEN_KEY:
        text = match.group(2).strip()
    return text


def _validate_token(token: str) -> None:
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise AuthConfigError(
            "Bearer token must contain 32-512 URL-safe characters (letters, digits, . _ ~ -)"
        )
    length = len(token)
    entropy_bits = sum(count * math.log2(length / count) for count in Counter(token).values())
    if len(set(token)) < 20 or entropy_bits < 200.0:
        raise AuthConfigError("Bearer token must have at least 200 bits of estimated entropy")


def _atomic_write_private_bytes(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise AuthConfigError(f"Refusing to replace symlinked auth file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(path.parent.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    """Persist a rename on POSIX; Windows does not expose directory fsync."""

    if os.name == "nt":
        return
    directory_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_private(path: Path, text: str) -> None:
    _atomic_write_private_bytes(path, text.encode("utf-8"))


def _read_bounded_file(path: Path) -> bytes:
    if path.is_symlink():
        raise AuthConfigError(f"Refusing to read symlinked auth file: {path}")
    with path.open("rb") as handle:
        payload = handle.read(MAX_AUTH_FILE_BYTES + 1)
    if len(payload) > MAX_AUTH_FILE_BYTES:
        raise AuthConfigError(f"Auth file is too large: {path}")
    return payload


def _env_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = _KEY_VALUE_RE.fullmatch(raw_line.strip())
        if match and match.group(1) == key:
            return match.group(2).strip()
    return ""


def _upsert_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    updated: list[str] = []
    replaced = False
    for line in lines:
        match = _KEY_VALUE_RE.fullmatch(line.strip())
        if match and match.group(1) == key:
            if not replaced:
                updated.append(f"{key}={value}")
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(f"{key}={value}")
    _atomic_write_private(path, "\n".join(updated).rstrip() + "\n")


def _capture_auth_state(paths: tuple[Path, ...]) -> dict[Path, bytes | None]:
    return {path: _read_bounded_file(path) if path.is_file() else None for path in paths}


def _restore_auth_state(state: dict[Path, bytes | None]) -> None:
    for path, payload in state.items():
        if payload is None:
            if path.exists():
                if not path.is_file() or path.is_symlink():
                    raise AuthConfigError(f"Refusing to remove non-regular auth path: {path}")
                path.unlink()
            continue
        _atomic_write_private_bytes(path, payload)


def snapshot(
    *,
    server_env: Path,
    backup_dir: Path,
) -> dict[str, object]:
    if backup_dir.exists():
        raise AuthConfigError(f"Auth backup directory already exists: {backup_dir}")
    backup_dir.mkdir(parents=True, mode=0o700)
    backup_dir.chmod(0o700)
    targets = {"server_env": server_env}
    manifest: dict[str, object] = {"version": AUTH_BACKUP_VERSION, "files": {}}
    try:
        entries = manifest["files"]
        assert isinstance(entries, dict)
        for name, path in targets.items():
            present = path.is_file()
            entry: dict[str, object] = {"path": str(path), "present": present}
            if present:
                payload = _read_bounded_file(path)
                backup_path = backup_dir / name
                _atomic_write_private_bytes(backup_path, payload)
                entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entries[name] = entry
        _atomic_write_private(
            backup_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        )
    except Exception:
        for child in backup_dir.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        backup_dir.rmdir()
        raise
    return {"ok": True, "backup_dir": str(backup_dir), "token_printed": False}


def restore(
    *,
    server_env: Path,
    backup_dir: Path,
) -> dict[str, object]:
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(_read_bounded_file(manifest_path).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != AUTH_BACKUP_VERSION:
        raise AuthConfigError("Unsupported auth backup manifest")
    entries = manifest.get("files")
    if not isinstance(entries, dict):
        raise AuthConfigError("Invalid auth backup manifest")
    targets = {"server_env": server_env}
    restored_state: dict[Path, bytes | None] = {}
    for name, path in targets.items():
        entry = entries.get(name)
        if not isinstance(entry, dict) or entry.get("path") != str(path):
            raise AuthConfigError(f"Auth backup target mismatch: {name}")
        present = entry.get("present") is True
        if not present:
            restored_state[path] = None
            continue
        payload = _read_bounded_file(backup_dir / name)
        if not secrets.compare_digest(
            hashlib.sha256(payload).hexdigest(), str(entry.get("sha256") or "")
        ):
            raise AuthConfigError(f"Auth backup checksum mismatch: {name}")
        restored_state[path] = payload
    current_state = _capture_auth_state(tuple(targets.values()))
    try:
        _restore_auth_state(restored_state)
    except Exception:
        _restore_auth_state(current_state)
        raise
    return {"ok": True, "restored": True, "token_printed": False}


def rotate(
    *,
    server_env: Path,
    token: str,
) -> dict[str, object]:
    _validate_token(token)
    previous_state = _capture_auth_state((server_env,))
    try:
        _upsert_env(server_env, SERVER_TOKEN_KEY, token)
    except Exception:
        _restore_auth_state(previous_state)
        raise
    return {
        "ok": True,
        "server_env": str(server_env),
        "token_printed": False,
        "restart_required": True,
    }


def check(
    *,
    server_env: Path,
) -> dict[str, object]:
    server_token = _env_value(server_env, SERVER_TOKEN_KEY)
    secure_mode = server_env.is_file()
    if os.name != "nt":
        secure_mode = secure_mode and stat.S_IMODE(server_env.stat().st_mode) == 0o600
    token_is_strong = False
    try:
        _validate_token(server_token)
        token_is_strong = True
    except AuthConfigError:
        pass
    return {
        "ok": bool(secure_mode and token_is_strong),
        "private_file_mode": secure_mode,
        "token_entropy_valid": token_is_strong,
        "token_printed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rotate the internal AutoStop CRM compatibility bearer."
    )
    parser.add_argument("--server-env", type=Path, default=DEFAULT_SERVER_ENV)
    subparsers = parser.add_subparsers(dest="command", required=True)

    rotate_parser = subparsers.add_parser("rotate")
    token_source = rotate_parser.add_mutually_exclusive_group(required=True)
    token_source.add_argument("--generate", action="store_true")
    token_source.add_argument("--token-file", type=Path)
    subparsers.add_parser("check")
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "rotate":
            token = (
                secrets.token_urlsafe(48)
                if args.generate
                else _read_small_secret_file(args.token_file)
            )
            result = rotate(
                server_env=args.server_env,
                token=token,
            )
        elif args.command == "check":
            result = check(server_env=args.server_env)
        elif args.command == "snapshot":
            result = snapshot(
                server_env=args.server_env,
                backup_dir=args.backup_dir,
            )
        else:
            result = restore(
                server_env=args.server_env,
                backup_dir=args.backup_dir,
            )
    except (AuthConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
