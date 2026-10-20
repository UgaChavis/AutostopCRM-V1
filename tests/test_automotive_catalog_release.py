"""Fail-closed pinned package comparison before either release path mutates state."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.manager_tool_catalog import content_hash  # noqa: E402
from scripts import check_automotive_tool_catalog as checker  # noqa: E402
from tests.manager_tool_catalog_fixture import fixture_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class AutomotiveCatalogReleaseTests(unittest.TestCase):
    def test_sealed_producer_uses_isolated_state_and_rejects_registry_or_schema_drift(self):
        bundle = fixture_bundle()
        schema = {"type": "object", "properties": {"identifier": {"type": "string"}}}
        bundle["native_schemas"] = {"demo_inspect": schema}
        bundle["tools"][0]["input_schema_ref"] = "demo_inspect"
        bundle["content_hash"] = content_hash(bundle)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sealed"
            root.mkdir()
            for name in ("mcp", "mcp/server", "autostop_manager"):
                package = root / name
                package.mkdir(exist_ok=True)
                (package / "__init__.py").write_text("")
            (root / "mcp/server/fastmcp.py").write_text(
                "from types import SimpleNamespace\n"
                "class FastMCP:\n"
                "    def __init__(self, name):\n"
                "        self._tool_manager=SimpleNamespace(_tools={})\n"
            )
            (root / "autostop_manager/mcp_tools.py").write_text(
                "import os, json\nfrom pathlib import Path\nfrom types import SimpleNamespace\n"
                "def register_manager_tools(server):\n"
                "    assert os.environ['AUTOSTOP_MANAGER_ENV_FILE'] == os.devnull\n"
                "    target=Path(os.environ['AUTOSTOP_MANAGER_DB'])\n"
                "    assert target.parent == Path.cwd()\n"
                "    target.write_text('disposable schema state')\n"
                "    schema=json.loads((Path(__file__).parents[1]/'schema.json').read_text())\n"
                "    server._tool_manager._tools={'demo_inspect': SimpleNamespace(parameters=schema)}\n"
            )
            (root / "autostop_manager/catalog_clients.py").write_text("PARTSAPI_OPERATIONS=[]\n")
            (root / "autostop_manager/automotive_catalog.py").write_text(
                "import json, hashlib\n"
                "def build_bundle(root, registry, schemas, operations, revision):\n"
                "    assert operations == []\n"
                "    bundle=json.loads((root/'fixture.json').read_text())\n"
                "    bundle['source_revision']=revision\n"
                "    bundle['native_schemas']=schemas\n"
                "    bundle['tools'][0]['title']=registry['title']\n"
                "    content={k:v for k,v in bundle.items() if k not in ('source_revision','content_hash')}\n"
                "    bundle['content_hash']=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()\n"
                "    return bundle\n"
            )
            registry_path = root / "docs/agent/automotive_tools.json"
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps({"title": bundle["tools"][0]["title"]}))
            (root / "schema.json").write_text(json.dumps(schema))
            (root / "fixture.json").write_text(json.dumps(bundle))
            baseline = {
                str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            checker.compare_sealed_producer(bundle, root, Path(sys.executable))
            self.assertEqual(
                baseline,
                {
                    str(path.relative_to(root)): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                },
            )
            registry_path.write_text(json.dumps({"title": "changed canonical registry"}))
            with self.assertRaisesRegex(ValueError, "differs"):
                checker.compare_sealed_producer(bundle, root, Path(sys.executable))
            registry_path.write_text(json.dumps({"title": bundle["tools"][0]["title"]}))
            changed = copy.deepcopy(schema)
            changed["required"] = ["identifier"]
            (root / "schema.json").write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "differs"):
                checker.compare_sealed_producer(bundle, root, Path(sys.executable))

    def test_cli_refuses_wrong_release_revision_without_running_producer(self):
        with (
            patch.object(checker, "load_bundle", return_value=fixture_bundle()),
            patch.object(checker, "compare_sealed_producer") as producer,
            patch.object(sys, "argv", ["check", "--expected-source-revision", "1" * 40]),
            self.assertRaises(SystemExit),
        ):
            checker.main()
        producer.assert_not_called()
