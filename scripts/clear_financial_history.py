from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.storage.financial_history_cleanup import sanitize_financial_history_state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear historical cash and payroll data from a Minimal Kanban state file."
    )
    parser.add_argument(
        "--state-file",
        default=str(get_state_file()),
        help="Path to the state.json file to sanitize.",
    )
    parser.add_argument(
        "--backup-suffix",
        default=".financial-history.backup.json",
        help="Suffix appended to the backup copy before overwriting the state file.",
    )
    args = parser.parse_args()

    state_file = Path(args.state_file)
    if not state_file.exists():
        raise SystemExit(f"State file not found: {state_file}")

    raw_state = json.loads(state_file.read_text(encoding="utf-8"))
    sanitized_state = sanitize_financial_history_state(raw_state)
    backup_file = state_file.with_suffix(state_file.suffix + args.backup_suffix)
    backup_file.write_text(json.dumps(raw_state, ensure_ascii=False, indent=2), encoding="utf-8")
    state_file.write_text(json.dumps(sanitized_state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Sanitized {state_file}")
    print(f"Backup written to {backup_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
