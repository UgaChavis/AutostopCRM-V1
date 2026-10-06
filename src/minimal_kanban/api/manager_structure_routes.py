"""Route binding for the durable manager structure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..services.manager_structure import ManagerStructureService


def build_manager_structure_routes(service: Any) -> dict:
    store = getattr(service, "_store", None)
    base_dir = getattr(store, "base_dir", None)
    if not isinstance(base_dir, Path):
        raise RuntimeError("CRM storage does not expose a durable state directory.")
    diagram = ManagerStructureService(
        base_dir / "manager_structure.json",
        legacy_settings_loader=lambda: store.read_bundle()["settings"],
    )
    return manager_structure_route_handlers(diagram)


def manager_structure_route_handlers(diagram: Any) -> dict:
    return {
        "/api/manager_structure": diagram.read,
        "/api/manager_structure/tool_catalog": lambda payload: diagram.tool_catalog(payload),
        "/api/manager_structure/apply": diagram.apply,
    }
