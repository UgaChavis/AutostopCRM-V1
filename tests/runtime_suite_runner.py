"""Run complete runtime coverage with isolated browser lifetimes on either OS."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unittest
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from tests.runtime_test_result import ImmediateTracebackResult

ROOT = Path(__file__).resolve().parents[1]
CORE_TIMEOUT_SECONDS = 30 * 60
BROWSER_TIMEOUT_SECONDS = 5 * 60


def iter_cases(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def is_browser_case(case: unittest.TestCase) -> bool:
    cls = type(case)
    owner = getattr(cls.setUpClass, "__module__", "")
    return "browser" in cls.__module__ or "browser" in owner


@dataclass(frozen=True)
class RuntimePlan:
    core_ids: tuple[str, ...]
    browser_groups: tuple[tuple[str, ...], ...]

    @property
    def count(self) -> int:
        return len(self.core_ids) + sum(len(group) for group in self.browser_groups)


def build_plan(suite: unittest.TestSuite) -> RuntimePlan:
    core: list[str] = []
    browsers: OrderedDict[type, list[str]] = OrderedDict()
    for case in iter_cases(suite):
        if is_browser_case(case):
            browsers.setdefault(type(case), []).append(case.id())
        else:
            core.append(case.id())
    return RuntimePlan(tuple(core), tuple(tuple(ids) for ids in browsers.values()))


def run_plan(plan: RuntimePlan, *, root: Path = ROOT) -> int:
    environment = os.environ.copy()
    previous = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(root / "tests") + (os.pathsep + previous if previous else "")
    coverage = [sys.executable, "-m", "coverage", "run", "--parallel-mode", "-m"]
    commands = [[*coverage, "tests.runtime_suite_runner", "--core-only"]]
    commands.extend(
        [*coverage, "tests.runtime_suite_runner", "--browser-group", *group]
        for group in plan.browser_groups
    )
    print(
        f"Runtime plan: {plan.count} tests; {len(plan.core_ids)} core; "
        f"{len(plan.browser_groups)} isolated browser groups; no retries.",
        flush=True,
    )
    for index, command in enumerate(commands):
        timeout = CORE_TIMEOUT_SECONDS if index == 0 else BROWSER_TIMEOUT_SECONDS
        controlled = [
            sys.executable,
            "-m",
            "tests.runtime_process",
            "--timeout-seconds",
            str(timeout),
            "--label",
            "core" if index == 0 else f"browser-{index}",
            "--",
            *command,
        ]
        result = subprocess.run(controlled, cwd=root, env=environment, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--core-only", action="store_true")
    selection.add_argument("--browser-group", nargs="+")
    args = parser.parse_args()
    if args.browser_group:
        suite = unittest.defaultTestLoader.loadTestsFromNames(args.browser_group)
        if not all(is_browser_case(case) for case in iter_cases(suite)):
            parser.error("browser group must resolve only to browser tests")
    else:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        if args.core_only:
            suite = unittest.TestSuite(
                case for case in iter_cases(suite) if not is_browser_case(case)
            )
    if args.core_only or args.browser_group:
        result = unittest.TextTestRunner(verbosity=2, resultclass=ImmediateTracebackResult).run(
            suite
        )
        return 0 if result.wasSuccessful() else 1
    return run_plan(build_plan(suite))


if __name__ == "__main__":
    raise SystemExit(main())
