from pathlib import Path

from project_source_path import add_project_source_path

ROOT = Path(__file__).resolve().parent
add_project_source_path(ROOT)

from minimal_kanban.deployment_security import reject_retired_release_configuration

reject_retired_release_configuration()

from minimal_kanban.mcp.main import run

if __name__ == "__main__":
    raise SystemExit(run())
