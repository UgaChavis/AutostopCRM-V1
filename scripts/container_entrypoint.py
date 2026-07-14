from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.deployment_security import (  # noqa: E402
    DeploymentSecurityError,
    assert_production_environment,
)


def main() -> int:
    try:
        assert_production_environment(require_production=True)
    except DeploymentSecurityError as exc:
        print(f"FATAL: production security validation failed: {exc}", file=sys.stderr)
        return 78

    entrypoint = ROOT / "main_mcp.py"
    os.execv(sys.executable, [sys.executable, str(entrypoint)])
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
