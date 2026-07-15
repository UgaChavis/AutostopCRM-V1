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
CODEX_TOKEN_ENV_KEY = "AUTOSTOPCRM_MCP_TOKEN"
CODEX_SECTION = "mcp_servers.autostopcrm"
DEFAULT_SERVER_ENV = Path("/opt/autostopcrm/.env")
DEFAULT_CODEX_CONFIG = Path("/root/.codex/config.toml")
DEFAULT_RUNTIME_ENV = Path("/root/.config/autostopcrm/codex-mcp.env")
DEFAULT_MCP_URL = "https://crm.autostopcrm.ru/mcp"
MAX_SECRET_FILE_BYTES = 4096
MAX_AUTH_FILE_BYTES = 2 * 1024 * 1024
AUTH_BACKUP_VERSION = 1
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SECTION_RE = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")
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
    if match and match.group(1) in {SERVER_TOKEN_KEY, CODEX_TOKEN_ENV_KEY}:
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


def _normalized_mcp_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise AuthConfigError("MCP URL must be a valid HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/mcp"
    ):
        raise AuthConfigError(
            "MCP URL must be an HTTPS /mcp endpoint without credentials, query, or fragment"
        )
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    return f"https://{authority}/mcp"


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
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


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


def _upsert_codex_oauth_config(path: Path, *, mcp_url: str) -> None:
    normalized_mcp_url = _normalized_mcp_url(mcp_url)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    section_start: int | None = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if not match:
            continue
        if match.group(1).strip() == CODEX_SECTION:
            section_start = index
            continue
        if section_start is not None and index > section_start:
            section_end = index
            break

    url_line = f'url = "{normalized_mcp_url}"'
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(
            [
                f"[{CODEX_SECTION}]",
                url_line,
            ]
        )
    else:
        removable = re.compile(r"^\s*(?:bearer_token_env_var|http_headers|env_http_headers)\s*=")
        kept_section = [
            line for line in lines[section_start + 1 : section_end] if not removable.match(line)
        ]
        url_re = re.compile(r"^\s*url\s*=")
        url_index = next(
            (index for index, line in enumerate(kept_section) if url_re.match(line)), None
        )
        if url_index is None:
            kept_section.insert(0, url_line)
        else:
            kept_section[url_index] = url_line
        lines[section_start + 1 : section_end] = kept_section
    _atomic_write_private(path, "\n".join(lines).rstrip() + "\n")


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
    codex_config: Path,
    runtime_env: Path,
    backup_dir: Path,
) -> dict[str, object]:
    if backup_dir.exists():
        raise AuthConfigError(f"Auth backup directory already exists: {backup_dir}")
    backup_dir.mkdir(parents=True, mode=0o700)
    backup_dir.chmod(0o700)
    targets = {
        "server_env": server_env,
        "codex_config": codex_config,
        "runtime_env": runtime_env,
    }
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
    codex_config: Path,
    runtime_env: Path,
    backup_dir: Path,
) -> dict[str, object]:
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(_read_bounded_file(manifest_path).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != AUTH_BACKUP_VERSION:
        raise AuthConfigError("Unsupported auth backup manifest")
    entries = manifest.get("files")
    if not isinstance(entries, dict):
        raise AuthConfigError("Invalid auth backup manifest")
    targets = {
        "server_env": server_env,
        "codex_config": codex_config,
        "runtime_env": runtime_env,
    }
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
    codex_config: Path,
    runtime_env: Path,
    token: str,
    mcp_url: str,
) -> dict[str, object]:
    _validate_token(token)
    normalized_mcp_url = _normalized_mcp_url(mcp_url)
    paths = (server_env, runtime_env, codex_config)
    previous_state = _capture_auth_state(paths)
    try:
        _upsert_env(server_env, SERVER_TOKEN_KEY, token)
        _upsert_env(runtime_env, CODEX_TOKEN_ENV_KEY, token)
        _upsert_codex_oauth_config(codex_config, mcp_url=normalized_mcp_url)
    except Exception:
        _restore_auth_state(previous_state)
        raise
    return {
        "ok": True,
        "server_env": str(server_env),
        "codex_config": str(codex_config),
        "runtime_env": str(runtime_env),
        "token_printed": False,
        "restart_required": True,
        "mcp_url_updated": True,
    }


def check(
    *,
    server_env: Path,
    codex_config: Path,
    runtime_env: Path,
    mcp_url: str = DEFAULT_MCP_URL,
) -> dict[str, object]:
    normalized_mcp_url = _normalized_mcp_url(mcp_url)
    server_token = _env_value(server_env, SERVER_TOKEN_KEY)
    runtime_token = _env_value(runtime_env, CODEX_TOKEN_ENV_KEY)
    config_text = codex_config.read_text(encoding="utf-8") if codex_config.is_file() else ""
    section_start = None
    section_end = len(config_text.splitlines())
    config_lines = config_text.splitlines()
    for index, line in enumerate(config_lines):
        match = _SECTION_RE.match(line)
        if not match:
            continue
        if match.group(1).strip() == CODEX_SECTION:
            section_start = index
            continue
        if section_start is not None and index > section_start:
            section_end = index
            break
    section_text = (
        "\n".join(config_lines[section_start + 1 : section_end])
        if section_start is not None
        else ""
    )
    has_bearer_reference = bool(
        re.search(
            rf'^\s*bearer_token_env_var\s*=\s*"{re.escape(CODEX_TOKEN_ENV_KEY)}"\s*$',
            section_text,
            flags=re.MULTILINE,
        )
    )
    has_static_headers = bool(
        re.search(r"^\s*(?:http_headers|env_http_headers)\s*=", section_text, flags=re.MULTILINE)
    )
    url_match = re.search(r'^\s*url\s*=\s*"([^"\r\n]+)"\s*$', section_text, flags=re.MULTILINE)
    configured_url = url_match.group(1) if url_match else ""
    try:
        url_matches = bool(
            configured_url and _normalized_mcp_url(configured_url) == normalized_mcp_url
        )
    except AuthConfigError:
        url_matches = False
    secure_modes = all(
        path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (server_env, codex_config, runtime_env)
    )
    matches = bool(server_token and secrets.compare_digest(server_token, runtime_token))
    codex_oauth_ready = not has_bearer_reference and not has_static_headers
    token_is_strong = False
    try:
        _validate_token(server_token)
        token_is_strong = True
    except AuthConfigError:
        pass
    return {
        "ok": bool(
            matches and codex_oauth_ready and secure_modes and url_matches and token_is_strong
        ),
        "tokens_match": matches,
        "codex_uses_oauth": codex_oauth_ready,
        "codex_uses_bearer_env": has_bearer_reference,
        "codex_has_static_auth_fallback": has_static_headers,
        "private_file_modes": secure_modes,
        "mcp_url_matches": url_matches,
        "token_entropy_valid": token_is_strong,
        "token_printed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Rotate the internal AutoStop CRM bearer while keeping Codex on stable OAuth.")
    )
    parser.add_argument("--server-env", type=Path, default=DEFAULT_SERVER_ENV)
    parser.add_argument("--codex-config", type=Path, default=DEFAULT_CODEX_CONFIG)
    parser.add_argument("--runtime-env", type=Path, default=DEFAULT_RUNTIME_ENV)
    subparsers = parser.add_subparsers(dest="command", required=True)

    rotate_parser = subparsers.add_parser("rotate")
    token_source = rotate_parser.add_mutually_exclusive_group(required=True)
    token_source.add_argument("--generate", action="store_true")
    token_source.add_argument("--token-file", type=Path)
    rotate_parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
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
                codex_config=args.codex_config,
                runtime_env=args.runtime_env,
                token=token,
                mcp_url=args.mcp_url,
            )
        elif args.command == "check":
            result = check(
                server_env=args.server_env,
                codex_config=args.codex_config,
                runtime_env=args.runtime_env,
                mcp_url=args.mcp_url,
            )
        elif args.command == "snapshot":
            result = snapshot(
                server_env=args.server_env,
                codex_config=args.codex_config,
                runtime_env=args.runtime_env,
                backup_dir=args.backup_dir,
            )
        else:
            result = restore(
                server_env=args.server_env,
                codex_config=args.codex_config,
                runtime_env=args.runtime_env,
                backup_dir=args.backup_dir,
            )
    except (AuthConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
