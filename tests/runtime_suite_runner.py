"""Run complete runtime coverage with isolated browser lifetimes on either OS."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from tests.runtime_core_cohorts import load_plan, run_core
from tests.runtime_skip_contract import unexpected_skips
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


def load_selected(identities):
    suite = unittest.TestSuite()
    prefix = "unittest.loader.ModuleSkipped."
    for identity in identities:
        selected = identity
        if identity.startswith(prefix):
            module = identity.removeprefix(prefix)
            # unittest creates this private class dynamically for module-level
            # SkipTest. Reload its real module; the generated class has no
            # importable attribute in unittest.loader. Exact ID checks remain.
            if re.fullmatch(r"test_[A-Za-z0-9_]+", module):
                selected = module
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(selected))
    return suite


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
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root / "tests"), *([previous] if previous else [])]
    )
    environment["PYTHONUTF8"] = "1"
    coverage = [sys.executable, "-m", "coverage", "run", "--parallel-mode", "-m"]
    commands = [
        [*coverage, "tests.runtime_suite_runner", "--browser-group", *group]
        for group in plan.browser_groups
    ]
    print(
        f"Runtime plan: {plan.count} tests; {len(plan.core_ids)} core; "
        f"{len(plan.browser_groups)} isolated browser groups; no retries.",
        flush=True,
    )
    core_result = run_core(
        plan.core_ids, root=root, environment=environment, budget=CORE_TIMEOUT_SECONDS
    )
    if core_result:
        return core_result
    for index, command in enumerate(commands, 1):
        controlled = [
            sys.executable,
            "-m",
            "tests.runtime_process",
            "--timeout-seconds",
            str(BROWSER_TIMEOUT_SECONDS),
            "--label",
            f"browser-{index}",
            "--",
            *command,
        ]
        result = subprocess.run(controlled, cwd=root, env=environment, check=False)
        if result.returncode:
            return result.returncode
    return 0


class MeasuredResult(ImmediateTracebackResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_ids, self.class_seconds = [], {}

    def startTest(self, test):
        self.executed_ids.append(test.id())
        self.test_started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        name = test.id().rsplit(".", 1)[0]
        self.class_seconds[name] = (
            self.class_seconds.get(name, 0) + time.monotonic() - self.test_started
        )
        super().stopTest(test)


def record_result(result: unittest.TestResult, *, selected_ids=None, cohort=None) -> int:
    unexplained = unexpected_skips(result.skipped, sys.platform)
    strict = os.environ.get("AUTOSTOP_REHEARSAL_STRICT_SKIPS") == "1"
    exact = selected_ids is None or list(selected_ids) == getattr(result, "executed_ids", [])
    if directory := os.environ.get("AUTOSTOP_REHEARSAL_RECEIPT_DIR"):
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"runtime-suite-{uuid.uuid4().hex}.json").write_text(
            json.dumps(
                {
                    "platform": sys.platform,
                    "pid": os.getpid(),
                    "fixture_temp_root": str(Path(tempfile.gettempdir()).resolve()),
                    "run": result.testsRun,
                    "successful": result.wasSuccessful() and exact and not (strict and unexplained),
                    "tests_successful": result.wasSuccessful(),
                    "strict_skip_contract": strict,
                    "skipped": [
                        {"test": test.id(), "reason": reason} for test, reason in result.skipped
                    ],
                    "unexplained_skips": unexplained,
                    "failures": [test.id() for test, _ in result.failures],
                    "errors": [test.id() for test, _ in result.errors],
                    "selected_ids": list(selected_ids or ()),
                    "executed_ids": getattr(result, "executed_ids", []),
                    "exact_identity_coverage": exact,
                    "class_seconds": getattr(result, "class_seconds", {}),
                    **(cohort or {}),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if strict and unexplained:
        print(
            f"Unexpected rehearsal test skips: {json.dumps(unexplained)}",
            file=sys.stderr,
            flush=True,
        )
    return 0 if result.wasSuccessful() and exact and not (strict and unexplained) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--core-only", action="store_true")
    selection.add_argument("--core-cohort", type=int)
    selection.add_argument("--browser-group", nargs="+")
    parser.add_argument("--core-plan", type=Path)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    # A cohort imports only its selected classes. Do not rely on a different
    # discovery module having inserted this checkout's src directory first.
    sys.path.insert(0, str(ROOT / "src"))
    cohort = None
    if args.core_cohort is not None:
        if args.core_plan is None or args.plan_sha256 is None:
            parser.error("a core cohort requires the immutable plan and its hash")
        _plan, selected = load_plan(args.core_plan, args.plan_sha256, args.core_cohort)
        suite = load_selected(selected)
        actual = tuple(case.id() for case in iter_cases(suite))
        if actual != selected:
            missing = sorted(set(selected) - set(actual))[:3]
            unexpected = sorted(set(actual) - set(selected))[:3]
            parser.error(
                "loaded core test identities differ from the immutable plan: "
                f"missing={missing!r}; unexpected={unexpected!r}"
            )
        if any(is_browser_case(case) for case in iter_cases(suite)):
            parser.error("a core cohort cannot contain browser tests")
        cohort = {"core_plan_sha256": args.plan_sha256, "core_cohort_index": args.core_cohort}
    elif args.browser_group:
        suite = unittest.defaultTestLoader.loadTestsFromNames(args.browser_group)
        if not all(is_browser_case(case) for case in iter_cases(suite)):
            parser.error("browser group must resolve only to browser tests")
    else:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        if args.core_only:
            suite = unittest.TestSuite(
                case for case in iter_cases(suite) if not is_browser_case(case)
            )
    if args.core_only or args.browser_group or args.core_cohort is not None:
        selected = tuple(case.id() for case in iter_cases(suite))
        result = unittest.TextTestRunner(verbosity=2, resultclass=MeasuredResult).run(suite)
        return record_result(result, selected_ids=selected, cohort=cohort)
    return run_plan(build_plan(suite))


if __name__ == "__main__":
    raise SystemExit(main())
