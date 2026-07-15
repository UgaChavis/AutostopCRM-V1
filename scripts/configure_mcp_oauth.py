from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

OAUTH_ENABLED_KEY = "AUTOSTOP_MCP_OAUTH_ENABLED"
EMBEDDED_OAUTH_KEY = "AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED"
STATE_KEY = "AUTOSTOP_MCP_OAUTH_STATE_KEY"


def _read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def _valid_key(value: str) -> bool:
    try:
        Fernet(str(value or "").encode("ascii"))
    except (TypeError, ValueError, UnicodeEncodeError):
        return False
    return len(str(value or "")) == 44


def _upsert_values(path: Path, replacements: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(replacements)
    updated: list[str] = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        name = match.group(1) if match else ""
        if name in pending:
            updated.append(f"{name}={pending.pop(name)}")
        else:
            updated.append(line)
    if updated and updated[-1].strip():
        updated.append("")
    updated.extend(f"{name}={value}" for name, value in pending.items())
    payload = "\n".join(updated).rstrip() + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        os.chmod(temp_path, 0o600)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)
    os.chmod(path, 0o600)


def ensure(path: Path) -> dict[str, bool]:
    values = _read_values(path)
    existing = values.get(STATE_KEY, "")
    if existing and not _valid_key(existing):
        raise ValueError(f"{STATE_KEY} exists but is invalid")
    state_key = existing or Fernet.generate_key().decode("ascii")
    _upsert_values(
        path,
        {
            OAUTH_ENABLED_KEY: "1",
            EMBEDDED_OAUTH_KEY: "0",
            STATE_KEY: state_key,
        },
    )
    return {
        "ok": True,
        "oauth_enabled": True,
        "embedded_development_oauth_disabled": True,
        "state_key_valid": True,
        "state_key_reused": bool(existing),
    }


def check(path: Path) -> dict[str, bool]:
    values = _read_values(path)
    return {
        "ok": (
            values.get(OAUTH_ENABLED_KEY, "").casefold() in {"1", "true", "yes", "on"}
            and values.get(EMBEDDED_OAUTH_KEY, "").casefold() in {"0", "false", "no", "off"}
            and _valid_key(values.get(STATE_KEY, ""))
        ),
        "oauth_enabled": values.get(OAUTH_ENABLED_KEY, "").casefold() in {"1", "true", "yes", "on"},
        "embedded_development_oauth_disabled": values.get(EMBEDDED_OAUTH_KEY, "").casefold()
        in {"0", "false", "no", "off"},
        "state_key_valid": _valid_key(values.get(STATE_KEY, "")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision stable production MCP OAuth settings without printing secrets."
    )
    parser.add_argument("command", choices=("ensure", "check"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    result = ensure(args.env_file) if args.command == "ensure" else check(args.env_file)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
