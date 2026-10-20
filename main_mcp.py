from pathlib import Path

from project_source_path import add_project_source_path

ROOT = Path(__file__).resolve().parent
add_project_source_path(ROOT)

from minimal_kanban.cold_http_ingress import load_http_ingress
from minimal_kanban.cold_manager import prepare_cold_manager

prepare_cold_manager()
load_http_ingress()

from minimal_kanban.mcp.main import run

if __name__ == "__main__":
    raise SystemExit(run())
