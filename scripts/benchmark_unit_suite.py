"""Measure the unchanged unittest runner and retain explicit skip/failure evidence."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import unittest
from pathlib import Path


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append(
            {"test": test.id(), "seconds": round(time.perf_counter() - self.started, 4)}
        )
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.source_root.resolve(), args.output_dir.resolve()
    if not (root / "tests" / "test_service.py").is_file():
        parser.error("source root must contain CRM tests")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    sys.path[:0] = [str(root), str(root / "src")]
    os.chdir(root)
    with (output / "unittest.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            suite = unittest.defaultTestLoader.discover(str(root / "tests"))
            discovered = suite.countTestCases()
            started = time.perf_counter()
            result = unittest.TextTestRunner(stream=log, verbosity=1, resultclass=TimedResult).run(
                suite
            )
            elapsed = time.perf_counter() - started
    summary = {
        "source_root": str(root),
        "python": sys.version.split()[0],
        "discovered": discovered,
        "run": result.testsRun,
        "seconds": round(elapsed, 3),
        "successful": result.wasSuccessful(),
        "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "failures": [test.id() for test, _ in result.failures],
        "errors": [test.id() for test, _ in result.errors],
        "slowest_tests": sorted(result.timings, key=lambda item: item["seconds"], reverse=True)[
            :25
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                **{
                    name: summary[name]
                    for name in ("discovered", "run", "seconds", "successful", "failures", "errors")
                },
                "skipped": len(summary["skipped"]),
                "evidence": str(output / "summary.json"),
            },
            ensure_ascii=False,
        )
    )
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
