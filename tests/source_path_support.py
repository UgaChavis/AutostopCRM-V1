from __future__ import annotations

import sys
from pathlib import Path


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_path(path: Path) -> None:
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)


def ensure_repository_root_path() -> None:
    _ensure_path(_repository_root())


def ensure_source_path() -> None:
    _ensure_path(_repository_root() / "src")


def prepend_source_path() -> None:
    sys.path.insert(0, str(_repository_root() / "src"))


def prepend_scripts_path() -> None:
    sys.path.insert(0, str(_repository_root() / "scripts"))


def ensure_scripts_path() -> None:
    _ensure_path(_repository_root() / "scripts")
