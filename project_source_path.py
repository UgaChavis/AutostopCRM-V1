from __future__ import annotations

import sys
from pathlib import Path


def add_project_source_path(project_root: Path) -> None:
    source_path = str(project_root / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
