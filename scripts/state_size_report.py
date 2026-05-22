# ruff: noqa: E402,I001
from __future__ import annotations

import argparse
import json
import logging
import shutil
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.storage.json_store import JsonStore


def json_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def summarize(samples: list[float]) -> dict[str, float]:
    return {
        "iterations": float(len(samples)),
        "avg_ms": round(statistics.mean(samples), 1) if samples else 0.0,
        "min_ms": round(min(samples), 1) if samples else 0.0,
        "max_ms": round(max(samples), 1) if samples else 0.0,
        "p95_ms": round(percentile(samples, 0.95), 1),
    }


def load_state(state_file: Path) -> dict[str, Any]:
    return json.loads(state_file.read_text(encoding="utf-8"))


def section_report(state: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key in sorted(state.keys()):
        value = state.get(key)
        count = len(value) if isinstance(value, (list, dict)) else None
        sections.append(
            {
                "section": key,
                "bytes": json_bytes(value),
                "count": count,
            }
        )
    sections.sort(key=lambda item: int(item["bytes"]), reverse=True)
    return sections


def event_action_report(state: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = defaultdict(lambda: {"events": 0, "bytes": 0})
    for event in state.get("events") or []:
        if not isinstance(event, dict):
            continue
        action = str(event.get("action") or "unknown")
        actions[action]["events"] += 1
        actions[action]["bytes"] += json_bytes(event)
    rows = [
        {"action": action, "events": data["events"], "bytes": data["bytes"]}
        for action, data in actions.items()
    ]
    rows.sort(key=lambda item: int(item["bytes"]), reverse=True)
    return rows[:limit]


def benchmark_state_file(state_file: Path, *, iterations: int) -> dict[str, Any]:
    if iterations <= 0:
        return {}
    logger = logging.getLogger("state_size_report.benchmark")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory(prefix="autostop-state-report-") as temp_dir:
        temp_state = Path(temp_dir) / "state.json"
        shutil.copy2(state_file, temp_state)
        store = JsonStore(state_file=temp_state, logger=logger)
        read_samples: list[float] = []
        write_samples: list[float] = []
        bundle = None
        for _ in range(iterations):
            store._invalidate_read_cache()
            started_at = time.perf_counter()
            bundle = store.read_bundle()
            read_samples.append((time.perf_counter() - started_at) * 1000)
            started_at = time.perf_counter()
            store.write_bundle(
                columns=bundle["columns"],
                cards=bundle["cards"],
                clients=bundle["clients"],
                stickies=bundle["stickies"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=bundle["settings"],
            )
            write_samples.append((time.perf_counter() - started_at) * 1000)
        return {
            "read_bundle": summarize(read_samples),
            "write_bundle": summarize(write_samples),
        }


def build_report(state_file: Path, *, benchmark_iterations: int = 0) -> dict[str, Any]:
    state_file = state_file.expanduser().resolve()
    state = load_state(state_file)
    report = {
        "state_file": str(state_file),
        "state_bytes": state_file.stat().st_size,
        "schema_version": state.get("schema_version"),
        "sections": section_report(state),
        "top_event_actions_by_bytes": event_action_report(state),
    }
    if benchmark_iterations:
        report["benchmark"] = benchmark_state_file(state_file, iterations=benchmark_iterations)
    return report


def print_text_report(report: dict[str, Any]) -> None:
    print(f"state_file: {report['state_file']}")
    print(f"state_bytes: {report['state_bytes']}")
    print(f"schema_version: {report.get('schema_version')}")
    print("\nsections:")
    for item in report["sections"]:
        count = "" if item["count"] is None else f" count={item['count']}"
        print(f"  {item['section']}: bytes={item['bytes']}{count}")
    print("\ntop_event_actions_by_bytes:")
    for item in report["top_event_actions_by_bytes"]:
        print(f"  {item['action']}: bytes={item['bytes']} events={item['events']}")
    if report.get("benchmark"):
        print("\nbenchmark_on_copy:")
        for name, stats in report["benchmark"].items():
            print(
                "  "
                + name
                + f": avg_ms={stats['avg_ms']} p95_ms={stats['p95_ms']} iterations={int(stats['iterations'])}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report AutoStop CRM state.json size and write/read timing."
    )
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--benchmark-iterations", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.state_file, benchmark_iterations=max(0, args.benchmark_iterations))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
