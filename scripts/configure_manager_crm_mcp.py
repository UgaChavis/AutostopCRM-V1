"""Maintain the private Manager credentials for the loopback CRM MCP route."""

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
from urllib.parse import urlsplit

SERVER_TOKEN_KEY = "MINIMAL_KANBAN_MCP_BEARER_TOKEN"
MANAGER_URL_KEY = "AUTOSTOP_CRM_MCP_URL"
MANAGER_TOKEN_KEY = "AUTOSTOP_CRM_MCP_BEARER_TOKEN"
DEFAULT_SERVER_ENV = Path("/opt/autostopcrm/.env")
DEFAULT_MANAGER_ENV = Path("/opt/AutostopManager/.crm-mcp.env")
LOOPBACK_CRM_MCP_URL = "http://127.0.0.1:8001/mcp"
BACKUP_VERSION = 1
MAX_FILE_BYTES = 2 * 1024 * 1024
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")

os.umask(0o077)


class ManagerCrmMcpConfigError(RuntimeError):
    pass


def _validate_token(token: str) -> None:
    if not _SAFE_TOKEN.fullmatch(token):
        raise ManagerCrmMcpConfigError("CRM MCP bearer token format is invalid")
    length = len(token)
    entropy = sum(count * math.log2(length / count) for count in Counter(token).values())
    if len(set(token)) < 20 or entropy < 200.0:
        raise ManagerCrmMcpConfigError("CRM MCP bearer token entropy is invalid")


def _validate_loopback_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ManagerCrmMcpConfigError("CRM MCP URL is invalid") from exc
    if (
        value != LOOPBACK_CRM_MCP_URL
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 8001
        or parsed.path != "/mcp"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ManagerCrmMcpConfigError("CRM MCP URL must be the fixed loopback endpoint")


def _require_regular(path: Path, *, required: bool = False) -> bool:
    if path.parent.is_symlink():
        raise ManagerCrmMcpConfigError("Refusing a symbolic-link credential directory")
    if path.is_symlink():
        raise ManagerCrmMcpConfigError("Refusing a symbolic-link credential path")
    if not path.exists():
        if required:
            raise ManagerCrmMcpConfigError("Required credential file is missing")
        return False
    if not path.is_file():
        raise ManagerCrmMcpConfigError("Credential path must be a regular file")
    return True


def _read_bounded(path: Path, *, required: bool = False) -> bytes | None:
    if not _require_regular(path, required=required):
        return None
    with path.open("rb") as handle:
        payload = handle.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise ManagerCrmMcpConfigError("Credential file exceeds the bounded size")
    return payload


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_private(path: Path, payload: bytes) -> None:
    _require_regular(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ManagerCrmMcpConfigError("Credential parent directory is invalid")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        path.chmod(0o600)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _remove_private(path: Path) -> None:
    if not path.exists():
        return
    if not _require_regular(path):
        return
    path.unlink()
    _fsync_directory(path.parent)


def _server_token(server_env: Path) -> str:
    payload = _read_bounded(server_env, required=True)
    assert payload is not None
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ManagerCrmMcpConfigError("CRM server environment is not UTF-8") from exc
    token = ""
    for line in lines:
        match = _ENV_LINE.fullmatch(line.strip())
        if match and match.group(1) == SERVER_TOKEN_KEY:
            token = match.group(2).strip()
    _validate_token(token)
    return token


def _managed_values(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ManagerCrmMcpConfigError("Manager CRM MCP environment is not UTF-8") from exc
    values: dict[str, str] = {}
    for line in lines:
        match = _ENV_LINE.fullmatch(line)
        if not match or match.group(1) not in {MANAGER_URL_KEY, MANAGER_TOKEN_KEY}:
            raise ManagerCrmMcpConfigError("Manager CRM MCP environment contains unmanaged data")
        key, value = match.groups()
        if key in values:
            raise ManagerCrmMcpConfigError("Manager CRM MCP environment contains duplicate data")
        values[key] = value
    if set(values) != {MANAGER_URL_KEY, MANAGER_TOKEN_KEY}:
        raise ManagerCrmMcpConfigError("Manager CRM MCP environment is incomplete")
    _validate_loopback_url(values[MANAGER_URL_KEY])
    _validate_token(values[MANAGER_TOKEN_KEY])
    return values


def _managed_payload(token: str) -> bytes:
    _validate_token(token)
    return f"{MANAGER_URL_KEY}={LOOPBACK_CRM_MCP_URL}\n{MANAGER_TOKEN_KEY}={token}\n".encode()


def _private_mode(path: Path) -> bool:
    return os.name == "nt" or stat.S_IMODE(path.stat().st_mode) == 0o600


def snapshot(*, manager_env: Path, backup_dir: Path) -> dict[str, object]:
    if backup_dir.exists():
        raise ManagerCrmMcpConfigError("Manager CRM MCP backup directory already exists")
    backup_dir.mkdir(parents=True, mode=0o700)
    backup_dir.chmod(0o700)
    try:
        payload = _read_bounded(manager_env)
        entry: dict[str, object] = {"path": str(manager_env), "present": payload is not None}
        if payload is not None:
            _atomic_write_private(backup_dir / "manager_crm_mcp_env", payload)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest = {"version": BACKUP_VERSION, "manager_env": entry}
        _atomic_write_private(
            backup_dir / "manifest.json",
            (json.dumps(manifest, sort_keys=True, ensure_ascii=False) + "\n").encode(),
        )
    except Exception:
        for child in backup_dir.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        backup_dir.rmdir()
        raise
    return {"ok": True, "backup_dir": str(backup_dir), "token_printed": False}


def restore(*, manager_env: Path, backup_dir: Path) -> dict[str, object]:
    manifest_payload = _read_bounded(backup_dir / "manifest.json", required=True)
    assert manifest_payload is not None
    manifest = json.loads(manifest_payload.decode("utf-8"))
    entry = manifest.get("manager_env") if isinstance(manifest, dict) else None
    if not isinstance(entry, dict) or manifest.get("version") != BACKUP_VERSION:
        raise ManagerCrmMcpConfigError("Manager CRM MCP backup manifest is invalid")
    if entry.get("path") != str(manager_env):
        raise ManagerCrmMcpConfigError("Manager CRM MCP backup target mismatch")
    previous = _read_bounded(manager_env)
    payload: bytes | None = None
    if entry.get("present") is True:
        payload = _read_bounded(backup_dir / "manager_crm_mcp_env", required=True)
        assert payload is not None
        if not secrets.compare_digest(
            hashlib.sha256(payload).hexdigest(), str(entry.get("sha256") or "")
        ):
            raise ManagerCrmMcpConfigError("Manager CRM MCP backup checksum mismatch")
    elif entry.get("present") is not False:
        raise ManagerCrmMcpConfigError("Manager CRM MCP backup presence is invalid")
    try:
        if payload is None:
            _remove_private(manager_env)
        else:
            _atomic_write_private(manager_env, payload)
    except Exception:
        if previous is None:
            _remove_private(manager_env)
        else:
            _atomic_write_private(manager_env, previous)
        raise
    return {"ok": True, "restored": True, "token_printed": False}


def sync(*, server_env: Path, manager_env: Path) -> dict[str, object]:
    token = _server_token(server_env)
    previous = _read_bounded(manager_env)
    if previous is not None:
        _managed_values(previous)
    try:
        _atomic_write_private(manager_env, _managed_payload(token))
    except Exception:
        if previous is None:
            _remove_private(manager_env)
        else:
            _atomic_write_private(manager_env, previous)
        raise
    return {"ok": True, "manager_env": str(manager_env), "token_printed": False}


def check(*, server_env: Path, manager_env: Path) -> dict[str, object]:
    server_token = _server_token(server_env)
    payload = _read_bounded(manager_env, required=True)
    assert payload is not None
    values = _managed_values(payload)
    private_mode = _private_mode(manager_env)
    return {
        "ok": private_mode and secrets.compare_digest(values[MANAGER_TOKEN_KEY], server_token),
        "private_file_mode": private_mode,
        "loopback_url_valid": values[MANAGER_URL_KEY] == LOOPBACK_CRM_MCP_URL,
        "credential_matches_server": secrets.compare_digest(
            values[MANAGER_TOKEN_KEY], server_token
        ),
        "managed_keys_only": True,
        "token_printed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync the private Manager E8 CRM MCP credential")
    parser.add_argument("--server-env", type=Path, default=DEFAULT_SERVER_ENV)
    parser.add_argument("--manager-env", type=Path, default=DEFAULT_MANAGER_ENV)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = commands.add_parser("snapshot")
    snapshot_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--backup-dir", type=Path, required=True)
    commands.add_parser("sync")
    commands.add_parser("check")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(manager_env=args.manager_env, backup_dir=args.backup_dir)
        elif args.command == "restore":
            result = restore(manager_env=args.manager_env, backup_dir=args.backup_dir)
        elif args.command == "sync":
            result = sync(server_env=args.server_env, manager_env=args.manager_env)
        else:
            result = check(server_env=args.server_env, manager_env=args.manager_env)
    except (ManagerCrmMcpConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
