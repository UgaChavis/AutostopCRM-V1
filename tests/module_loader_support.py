from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module_from_file(
    name: str,
    path: Path,
    *,
    load_error: Exception | None = None,
) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        if load_error is not None:
            raise load_error
        raise AssertionError(f"{path.name} is importable")
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(spec.name)
    had_previous_module = spec.name in sys.modules
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        if had_previous_module:
            sys.modules[spec.name] = previous_module
        else:
            sys.modules.pop(spec.name, None)
