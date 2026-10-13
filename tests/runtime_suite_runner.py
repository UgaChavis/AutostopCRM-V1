"""Run complete runtime coverage with isolated browser lifetimes on Windows."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unittest
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    commands.extend([*coverage, "unittest", *group, "-v"] for group in plan.browser_groups)
    print(
        f"Runtime plan: {plan.count} tests; {len(plan.core_ids)} core; "
        f"{len(plan.browser_groups)} isolated browser groups; no retries.",
        flush=True,
    )
    for command in commands:
        result = subprocess.run(command, cwd=root, env=environment, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-only", action="store_true")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    if args.core_only:
        core = unittest.TestSuite(case for case in iter_cases(suite) if not is_browser_case(case))
        result = unittest.TextTestRunner(verbosity=2).run(core)
        return 0 if result.wasSuccessful() else 1
    return run_plan(build_plan(suite))


if __name__ == "__main__":
    raise SystemExit(main())
