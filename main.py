from pathlib import Path

from project_source_path import add_project_source_path

ROOT = Path(__file__).resolve().parent
add_project_source_path(ROOT)

from minimal_kanban.app import run

if __name__ == "__main__":
    raise SystemExit(run())
