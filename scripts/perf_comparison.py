"""Run identical, serial baseline/candidate harnesses and retain synthetic evidence."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRINTING_METRICS = (
    "shell_ms",
    "documents_ms",
    "preview_ms",
    "service_lock_wait_ms",
    "service_lock_hold_ms",
)
PRINTING_REDUCTION_TARGETS = {"shell_ms": 0.75, "documents_ms": 0.5, "service_lock_wait_ms": 0.8}


def compare_rows(baseline, candidate):
    """Compare median series p95, not a selectively chosen fastest run."""
    grouped = {}
    for label, runs in (("baseline", baseline), ("candidate", candidate)):
        for result in runs:
            for row in result["rows"]:
                if row.get("skipped"):
                    continue
                scenario = row["scenario"]
                grouped.setdefault((scenario, "duration_ms"), {}).setdefault(label, []).append(
                    row["p95_ms"]
                )
                if scenario in {"printing.immediate", "printing.concurrent_payroll"}:
                    for metric in PRINTING_METRICS:
                        grouped.setdefault((scenario, metric), {})
                        value = row.get(metric, {}).get("p95_ms")
                        if value is not None:
                            grouped[(scenario, metric)].setdefault(label, []).append(value)
    comparisons = []
    for (scenario, metric), values in sorted(grouped.items()):
        if (
            metric == "service_lock_hold_ms"
            and not values.get("baseline")
            and candidate
            and len(values.get("candidate", [])) == len(candidate)
        ):
            comparisons.append(
                {
                    "scenario": scenario,
                    "metric": metric,
                    "status": "not_comparable",
                    "reason": "baseline_telemetry_unavailable",
                    "candidate_p95_ms": statistics.median(values["candidate"]),
                    "candidate_series_p95": values["candidate"],
                }
            )
            continue
        if (
            len(values.get("baseline", [])) != len(baseline)
            or len(values.get("candidate", [])) != len(candidate)
            or not baseline
            or not candidate
        ):
            comparisons.append({"scenario": scenario, "metric": metric, "status": "missing"})
            continue
        before = statistics.median(values["baseline"])
        after = statistics.median(values["candidate"])
        reduction = (
            PRINTING_REDUCTION_TARGETS.get(metric)
            if scenario == "printing.concurrent_payroll"
            else None
        )
        ceiling = (
            before * (1 - reduction)
            if reduction is not None
            else (before + 2 if before < 10 else before * 1.1)
        )
        ceiling = round(ceiling, 9)
        comparisons.append(
            {
                "scenario": scenario,
                "metric": metric,
                "baseline_p95_ms": before,
                "candidate_p95_ms": after,
                "change_percent": round((after / before - 1) * 100, 2) if before else None,
                "regression_ceiling_ms": round(ceiling, 3),
                "required_reduction_percent": reduction * 100 if reduction is not None else 0,
                "status": "passed" if after <= ceiling else "regressed",
                "baseline_series_p95": values["baseline"],
                "candidate_series_p95": values["candidate"],
            }
        )
    return comparisons or [{"scenario": "all", "status": "missing"}]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--series", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--scale", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument(
        "--scenario",
        action="append",
        help="Panel scenario to measure with the candidate harness; may be repeated.",
    )
    browser_modes = parser.add_mutually_exclusive_group()
    browser_modes.add_argument("--browser", action="store_true")
    browser_modes.add_argument("--panels", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.series <= 10 or not 1 <= args.iterations <= 100:
        parser.error("invalid bounded series/iterations")
    if args.panels and args.scale != 1:
        parser.error("panel measurements currently require scale 1")
    if args.scenario and not args.panels:
        parser.error("scenario selection requires --panels")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"baseline": [], "candidate": []}
    for series in range(1, args.series + 1):
        # Alternate order to reduce systematic temperature/time-of-day bias.
        labels = ("baseline", "candidate") if series % 2 else ("candidate", "baseline")
        for label in labels:
            command = [
                sys.executable,
                str(ROOT / "scripts" / "perf_workflows.py"),
                "--source-root",
                str(getattr(args, label).resolve()),
                "--synthetic-state-profile",
                "current-production",
                "--stage1-only",
                "--synthetic-state-scale",
                str(args.scale),
                "--warmup-iterations",
                "2",
                "--iterations",
                str(args.iterations),
            ]
            if args.panels:
                command = [
                    sys.executable,
                    str(ROOT / "scripts" / "perf_browser_panels.py"),
                    "--source-root",
                    str(getattr(args, label).resolve()),
                    "--series",
                    "1",
                    "--iterations",
                    str(args.iterations),
                ]
                for scenario in args.scenario or []:
                    command.extend(("--scenario", scenario))
            elif args.browser:
                command += [
                    "--local-temp-server",
                    "--representative-browser",
                    "--browser-timeout-seconds",
                    "1800",
                ]
            else:
                command += ["--skip-browser"]
            print(
                f"Measuring {label} series {series}/{args.series}, scale={args.scale}", flush=True
            )
            process = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2100,
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            result = json.loads(process.stdout)
            if args.panels:
                result["rows"] = [
                    row
                    for series_result in result.get("series", [])
                    for row in series_result["rows"]
                ]
            result["process_exit_code"] = process.returncode
            (args.output_dir / f"{label}-{series}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            evidence[label].append(result)
            if (
                process.returncode
                or result.get("violations")
                or (args.panels and not result.get("ok"))
            ):
                raise RuntimeError(f"{label} series {series} failed; see retained evidence")
    summary = {
        "series": args.series,
        "iterations": args.iterations,
        "scale": args.scale,
        "scenarios": args.scenario,
        "comparisons": compare_rows(evidence["baseline"], evidence["candidate"]),
    }
    (args.output_dir / "comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(
        any(row["status"] not in {"passed", "not_comparable"} for row in summary["comparisons"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
