from __future__ import annotations

import unittest

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.mcp.raw_gateway import (  # noqa: E402
    RAW_API_READ_ROUTES,
    RAW_API_WRITE_ROUTES,
    schema_hash,
    verify_virtual_api_write_readback,
    virtual_api_argument_errors,
    virtual_api_schema,
)


class ManagerStructureGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_raw_capabilities_are_versioned_and_discoverable(self) -> None:
        self.assertIn("/api/manager_structure", RAW_API_READ_ROUTES)
        self.assertIn("/api/manager_structure/apply", RAW_API_WRITE_ROUTES)
        schema = virtual_api_schema("/api/manager_structure/apply")
        self.assertTrue(schema_hash(schema))
        self.assertEqual(
            virtual_api_argument_errors("/api/manager_structure/apply", {}),
            [
                "arguments.expected_version:required",
                "arguments.idempotency_key:required",
                "arguments.operation:required",
            ],
        )
        self.assertFalse(
            virtual_api_argument_errors(
                "/api/manager_structure/apply",
                {
                    "operation": "upsert_element",
                    "expected_version": 3,
                    "idempotency_key": "example-001",
                    "element": {"id": "M1"},
                },
            )
        )

    async def test_raw_write_checks_exact_module_text_on_readback(self) -> None:
        async def invoke(name: str, arguments: dict) -> dict:
            self.assertEqual(name, "api:/api/manager_structure")
            self.assertEqual(arguments, {})
            return {
                "ok": True,
                "data": {
                    "version": 2,
                    "elements": [{"id": "M1", "instruction": "Сохранённый полный текст"}],
                    "relations": [],
                },
            }

        result = {"ok": True, "data": {"version": 2}}
        good = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply",
            {
                "operation": "upsert_element",
                "element": {"id": "M1", "instruction": "Сохранённый полный текст"},
            },
            result,
            invoke,
        )
        self.assertTrue(good["passed"])
        bad = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply",
            {"operation": "upsert_element", "element": {"id": "M1", "instruction": "Другой текст"}},
            result,
            invoke,
        )
        self.assertFalse(bad["passed"])


if __name__ == "__main__":
    unittest.main()
