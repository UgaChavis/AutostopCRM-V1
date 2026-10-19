from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.cold_manager import prepare_cold_manager  # noqa: E402

# A cold container must resolve its independently accepted seventh binding
# before loading application modules or running application initialization.
prepare_cold_manager()

from minimal_kanban.cold_http_ingress import load_http_ingress  # noqa: E402

load_http_ingress()

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


if __name__ == "__main__":
    raise SystemExit(main())
