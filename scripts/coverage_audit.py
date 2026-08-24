from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "scripts" / "coverage_baseline.json"
MAX_INPUT_BYTES = 64 * 1024 * 1024
PERCENT_EPSILON = 1e-9


@dataclass(frozen=True)
class CoverageMetrics:
    covered_lines: int
    num_statements: int
    covered_branches: int
    num_branches: int

    @property
    def percent(self) -> float:
        denominator = self.num_statements + self.num_branches
        if denominator <= 0:
            raise ValueError("coverage denominator must be positive")
        return 100.0 * (self.covered_lines + self.covered_branches) / denominator

    def __add__(self, other: CoverageMetrics) -> CoverageMetrics:
        return CoverageMetrics(
            covered_lines=self.covered_lines + other.covered_lines,
            num_statements=self.num_statements + other.num_statements,
            covered_branches=self.covered_branches + other.covered_branches,
            num_branches=self.num_branches + other.num_branches,
        )

    @classmethod
    def from_summary(cls, value: object, *, label: str) -> CoverageMetrics:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        fields: dict[str, int] = {}
        for name in (
            "covered_lines",
            "num_statements",
            "covered_branches",
            "num_branches",
        ):
            raw = value.get(name)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ValueError(f"{label}.{name} must be a non-negative integer")
            fields[name] = raw
        if fields["covered_lines"] > fields["num_statements"]:
            raise ValueError(f"{label}.covered_lines exceeds num_statements")
        if fields["covered_branches"] > fields["num_branches"]:
            raise ValueError(f"{label}.covered_branches exceeds num_branches")
        metrics = cls(**fields)
        _ = metrics.percent
        return metrics


@dataclass(frozen=True)
class CoverageFloorResult:
    floor_id: str
    measurement: str
    scope: str
    paths: tuple[str, ...]
    minimum_percent: float
    baseline_percent: float
    current_percent: float
    metrics: CoverageMetrics
    passed: bool


@dataclass(frozen=True)
class CoverageIssue:
    code: str
    target: str
    detail: str


@dataclass(frozen=True)
class CoverageAuditResult:
    results: tuple[CoverageFloorResult, ...]
    issues: tuple[CoverageIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues and all(result.passed for result in self.results)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "summary": {
                "floors": len(self.results),
                "passed": sum(result.passed for result in self.results),
                "issues": len(self.issues),
            },
            "floors": [
                {
                    **asdict(result),
                    "metrics": asdict(result.metrics),
                }
                for result in self.results
            ],
            "issues": [asdict(issue) for issue in self.issues],
        }


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}: {exc}") from exc
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"{label} exceeds {MAX_INPUT_BYTES} bytes: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _normalize_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty path")
    normalized = value.replace("\\", "/").removeprefix("./")
    if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
        raise ValueError(f"{label} must stay relative to the repository: {value}")
    return normalized


def _report_files(report: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    files = report.get("files")
    if not isinstance(files, dict):
        raise ValueError(f"{label}.files must be an object")
    normalized: dict[str, Any] = {}
    for raw_path, details in files.items():
        path = _normalize_path(raw_path, label=f"{label}.files key")
        if path in normalized:
            raise ValueError(f"{label} contains duplicate normalized path: {path}")
        normalized[path] = details
    return normalized


def _aggregate_file_metrics(
    report: Mapping[str, Any], paths: Sequence[str], *, label: str
) -> CoverageMetrics:
    files = _report_files(report, label=label)
    total = CoverageMetrics(0, 0, 0, 0)
    for path in paths:
        details = files.get(path)
        if not isinstance(details, dict):
            raise ValueError(f"{label} has no measured file: {path}")
        total += CoverageMetrics.from_summary(
            details.get("summary"), label=f"{label}.files[{path}].summary"
        )
    _ = total.percent
    return total


def _validate_report(report: Mapping[str, Any], *, label: str) -> None:
    meta = report.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise ValueError(f"{label} must be generated with branch coverage enabled")
    CoverageMetrics.from_summary(report.get("totals"), label=f"{label}.totals")
    _report_files(report, label=label)


def _coverage_overrides(values: Sequence[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        measurement, separator, raw_path = value.partition("=")
        if not separator or not measurement.strip() or not raw_path.strip():
            raise ValueError("--coverage-json must use MEASUREMENT=PATH")
        key = measurement.strip()
        if key in overrides:
            raise ValueError(f"duplicate --coverage-json measurement: {key}")
        overrides[key] = Path(raw_path).resolve()
    return overrides


def audit_coverage(
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    report_overrides: Mapping[str, Path] | None = None,
) -> CoverageAuditResult:
    issues: list[CoverageIssue] = []
    results: list[CoverageFloorResult] = []
    try:
        manifest = _read_json(manifest_path, label="coverage manifest")
    except ValueError as exc:
        return CoverageAuditResult((), (CoverageIssue("manifest_invalid", "manifest", str(exc)),))

    if manifest.get("schema_version") != 1:
        issues.append(
            CoverageIssue(
                "manifest_schema_invalid",
                "manifest",
                "schema_version must equal 1",
            )
        )
        return CoverageAuditResult((), tuple(issues))

    measurements = manifest.get("measurements")
    floors = manifest.get("floors")
    if not isinstance(measurements, dict) or not isinstance(floors, list):
        issues.append(
            CoverageIssue(
                "manifest_shape_invalid",
                "manifest",
                "measurements must be an object and floors must be an array",
            )
        )
        return CoverageAuditResult((), tuple(issues))

    overrides = dict(report_overrides or {})
    unknown_overrides = sorted(set(overrides) - set(measurements))
    for measurement in unknown_overrides:
        issues.append(
            CoverageIssue(
                "unknown_measurement_override",
                measurement,
                "override does not exist in the manifest",
            )
        )

    reports: dict[str, dict[str, Any]] = {}
    for measurement, config in measurements.items():
        if not isinstance(measurement, str) or not isinstance(config, dict):
            issues.append(
                CoverageIssue(
                    "measurement_invalid",
                    str(measurement),
                    "measurement configuration must be an object",
                )
            )
            continue
        try:
            default_json = _normalize_path(
                config.get("default_json"),
                label=f"measurements.{measurement}.default_json",
            )
            report_path = overrides.get(measurement, ROOT / default_json)
            report = _read_json(report_path, label=f"coverage report {measurement}")
            _validate_report(report, label=f"coverage report {measurement}")
            reports[measurement] = report
        except ValueError as exc:
            issues.append(CoverageIssue("coverage_report_invalid", measurement, str(exc)))

    seen_floor_ids: set[str] = set()
    for index, floor in enumerate(floors):
        target = f"floors[{index}]"
        if not isinstance(floor, dict):
            issues.append(CoverageIssue("floor_invalid", target, "floor must be an object"))
            continue
        floor_id = floor.get("id")
        measurement = floor.get("measurement")
        scope = floor.get("scope")
        if not isinstance(floor_id, str) or not floor_id:
            issues.append(CoverageIssue("floor_invalid", target, "id must be non-empty"))
            continue
        target = floor_id
        if floor_id in seen_floor_ids:
            issues.append(CoverageIssue("floor_duplicate", target, "floor id is duplicated"))
            continue
        seen_floor_ids.add(floor_id)
        if not isinstance(measurement, str) or measurement not in measurements:
            issues.append(CoverageIssue("floor_invalid", target, "measurement is not declared"))
            continue
        report = reports.get(measurement)
        if report is None:
            continue
        try:
            minimum = floor.get("minimum_percent")
            if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                raise ValueError("minimum_percent must be numeric")
            minimum_percent = float(minimum)
            if not 0.0 <= minimum_percent <= 100.0:
                raise ValueError("minimum_percent must be between 0 and 100")
            baseline = CoverageMetrics.from_summary(
                floor.get("baseline"), label=f"{target}.baseline"
            )
            baseline_percent = baseline.percent
            if minimum_percent > baseline_percent + PERCENT_EPSILON:
                raise ValueError("minimum_percent exceeds the measured baseline")
            if baseline_percent - minimum_percent > 0.5 + PERCENT_EPSILON:
                raise ValueError("minimum_percent is more than 0.5 pp below baseline")

            raw_paths = floor.get("paths", [])
            if not isinstance(raw_paths, list):
                raise ValueError("paths must be an array")
            paths = tuple(_normalize_path(path, label=f"{target}.paths") for path in raw_paths)
            if len(paths) != len(set(paths)):
                raise ValueError("paths must not contain duplicates")
            if scope == "global":
                if paths:
                    raise ValueError("global floor must not declare paths")
                current = CoverageMetrics.from_summary(
                    report.get("totals"), label=f"coverage report {measurement}.totals"
                )
            elif scope == "files":
                if not paths:
                    raise ValueError("files floor must declare at least one path")
                current = _aggregate_file_metrics(
                    report, paths, label=f"coverage report {measurement}"
                )
            else:
                raise ValueError("scope must be global or files")

            current_percent = current.percent
            passed = current_percent + PERCENT_EPSILON >= minimum_percent
            results.append(
                CoverageFloorResult(
                    floor_id=floor_id,
                    measurement=measurement,
                    scope=scope,
                    paths=paths,
                    minimum_percent=minimum_percent,
                    baseline_percent=baseline_percent,
                    current_percent=current_percent,
                    metrics=current,
                    passed=passed,
                )
            )
            if not passed:
                issues.append(
                    CoverageIssue(
                        "coverage_below_floor",
                        target,
                        f"{current_percent:.2f}% is below {minimum_percent:.2f}%",
                    )
                )
        except ValueError as exc:
            issues.append(CoverageIssue("floor_invalid", target, str(exc)))

    return CoverageAuditResult(tuple(results), tuple(issues))


def _text_report(result: CoverageAuditResult) -> str:
    lines = [f"coverage audit: {'PASS' if result.ok else 'FAIL'}"]
    for floor in result.results:
        lines.append(
            f"[{('PASS' if floor.passed else 'FAIL')}] {floor.floor_id}: "
            f"current={floor.current_percent:.2f}% "
            f"floor={floor.minimum_percent:.2f}% "
            f"baseline={floor.baseline_percent:.2f}%"
        )
    for issue in result.issues:
        lines.append(f"[{issue.code}] {issue.target}: {issue.detail}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enforce branch-coverage ratchets.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--coverage-json",
        action="append",
        default=[],
        metavar="MEASUREMENT=PATH",
        help="Override one measurement report path; may be repeated.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        overrides = _coverage_overrides(args.coverage_json)
    except ValueError as exc:
        print(f"coverage audit: FAIL\n[arguments_invalid] arguments: {exc}", file=sys.stderr)
        return 2
    result = audit_coverage(args.manifest.resolve(), report_overrides=overrides)
    if args.format == "json":
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_text_report(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
