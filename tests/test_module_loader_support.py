from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from uuid import uuid4

if __package__:
    from tests.module_loader_support import load_module_from_file
else:
    from module_loader_support import load_module_from_file


class ModuleLoaderSupportTests(unittest.TestCase):
    def test_successful_load_restores_absent_sys_modules_entry(self) -> None:
        name = f"_test_loaded_module_{uuid4().hex}"
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sample.py"
            module_path.write_text("VALUE = 42\n", encoding="utf-8")

            module = load_module_from_file(name, module_path)

        self.assertEqual(module.VALUE, 42)
        self.assertNotIn(name, sys.modules)

    def test_failed_load_restores_existing_sys_modules_entry(self) -> None:
        name = f"_test_failed_module_{uuid4().hex}"
        previous_module = ModuleType(name)
        sys.modules[name] = previous_module
        self.addCleanup(sys.modules.pop, name, None)
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sample.py"
            module_path.write_text(
                "raise RuntimeError('intentional module failure')\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "intentional module failure"):
                load_module_from_file(name, module_path)

        self.assertIs(sys.modules[name], previous_module)

    def test_failed_load_restores_none_sys_modules_sentinel(self) -> None:
        name = f"_test_none_sentinel_{uuid4().hex}"
        sys.modules[name] = None
        self.addCleanup(sys.modules.pop, name, None)
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sample.py"
            module_path.write_text(
                "raise RuntimeError('intentional module failure')\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "intentional module failure"):
                load_module_from_file(name, module_path)

        self.assertIn(name, sys.modules)
        self.assertIsNone(sys.modules[name])


if __name__ == "__main__":
    unittest.main()
