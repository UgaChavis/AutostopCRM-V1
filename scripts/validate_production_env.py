from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.deployment_security import (  # noqa: E402
    assert_production_environment,
    load_agent_gateway_security_policy,
    validate_production_environment,
    validate_store_integration_environment,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate fail-closed AutoStop CRM production security settings."
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result.")
    parser.add_argument(
        "--require-production",
        action="store_true",
        help="Fail when AUTOSTOP_DEPLOYMENT_ENV is not production.",
    )
    parser.add_argument(
        "--require-store",
        action="store_true",
        help="Fail unless the internal Store URL and scoped service identities are provisioned.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_production_environment()
    if args.require_store:
        errors.extend(validate_store_integration_environment())
    if args.require_production:
        try:
            assert_production_environment(require_production=True)
        except RuntimeError as exc:
            if str(exc) not in errors:
                errors.append(str(exc))
    try:
        policy = load_agent_gateway_security_policy()
    except RuntimeError as exc:
        if str(exc) not in errors:
            errors.append(str(exc))
        policy = None

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "errors": errors,
                    "policy": policy.public_dict() if policy is not None else None,
                },
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    elif errors:
        print("Production security validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    else:
        print("Production security validation passed (secret values were not displayed).")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
