"""Verify the CRM's immutable automotive instruction package without API access."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.services.manager_tool_catalog import (  # noqa: E402
    MAX_BUNDLE_BYTES,
    bundle_metadata,
    load_bundle,
    validate_bundle,
)

PRODUCER_CODE = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from mcp.server.fastmcp import FastMCP
from autostop_manager.mcp_tools import register_manager_tools
from autostop_manager.catalog_clients import PARTSAPI_OPERATIONS
from autostop_manager.automotive_catalog import build_bundle
root = Path(sys.argv[1]); server = FastMCP('crm-catalog-release-check')
register_manager_tools(server)
schemas = {name: tool.parameters for name,tool in server._tool_manager._tools.items()}
registry = json.loads((root/'docs/agent/automotive_tools.json').read_text())
print(json.dumps(build_bundle(root,registry,schemas,PARTSAPI_OPERATIONS,sys.argv[2]),ensure_ascii=False))
"""


def compare_sealed_producer(bundle: dict, root: Path, python: Path) -> None:
    """Generate from sealed code with disposable state, never the host checkout."""
    with tempfile.TemporaryDirectory(prefix="autostopcrm-catalog-check-") as temp:
        environment = {
            **os.environ,
            "AUTOSTOP_MANAGER_ENV_FILE": os.devnull,
            "AUTOSTOP_MANAGER_DB": str(Path(temp) / "schema.sqlite3"),
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-B",
                    "-c",
                    PRODUCER_CODE,
                    str(root),
                    bundle["source_revision"],
                ],
                cwd=temp,
                env=environment,
                check=True,
                capture_output=True,
                timeout=60,
            )
            if len(result.stdout) > MAX_BUNDLE_BYTES:
                raise ValueError("Sealed Manager generated an oversized catalog")
            generated = validate_bundle(json.loads(result.stdout))
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ValueError("Sealed Manager catalog/schema generation failed") from error
    if generated != bundle:
        raise ValueError("CRM catalog differs from the sealed Manager registry/native schemas")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source-revision")
    parser.add_argument("--manager-root", type=Path)
    parser.add_argument("--manager-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    bundle = load_bundle()
    if args.expected_source_revision and bundle["source_revision"] != args.expected_source_revision:
        parser.error("CRM automotive catalog source revision differs from the release tuple")
    if args.manager_root:
        root = args.manager_root.resolve()
        revision_path = root / "REVISION"
        if (
            not revision_path.is_file()
            or revision_path.read_text().strip() != bundle["source_revision"]
        ):
            parser.error("Manager sealed snapshot revision differs from the CRM catalog pin")
        for record in [*bundle["modules"], *bundle["tools"]]:
            relative = Path(record["instruction_ref"])
            path = (root / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(root):
                parser.error("Instruction reference escapes the Manager snapshot")
            if not path.is_file() or path.read_text(encoding="utf-8") != record["instruction_text"]:
                parser.error("CRM instruction content differs from the sealed Manager snapshot")
        try:
            compare_sealed_producer(bundle, root, args.manager_python)
        except ValueError as error:
            parser.error(str(error))
    print(
        json.dumps(
            {
                "ok": True,
                **bundle_metadata(bundle),
                "modules": len(bundle["modules"]),
                "tools": len(bundle["tools"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
