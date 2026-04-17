from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def run() -> int:
    print("AutoStop CRM server AI runtime is disabled. Use the in-card cleanup action instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
