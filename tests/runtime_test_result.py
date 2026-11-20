"""Keep the first test failure visible even when later native cleanup stalls."""

from __future__ import annotations

import unittest


class ImmediateTracebackResult(unittest.TextTestResult):
    def startTest(self, test) -> None:
        unittest.TestResult.startTest(self, test)
        if self.showAll:
            self._write_line(f"START {test.id()}")

    def _write_status(self, test, status) -> None:
        # Cohorts share a log, so each status carries its test ID on the same line.
        self._write_line(f"{test.id()} ... {status}")

    def _write_line(self, message: str) -> None:
        self.stream.write(f"{message}\n")
        self.stream.flush()

    def _print_failure_now(self, test, error) -> None:
        self._write_line(
            f"\nImmediate failure detail: {test.id()}\n{self._exc_info_to_string(error, test)}"
        )

    def addExpectedFailure(self, test, err) -> None:
        if self.showAll:
            unittest.TestResult.addExpectedFailure(self, test, err)
            self._write_status(test, "expected failure")
        else:
            super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test) -> None:
        if self.showAll:
            unittest.TestResult.addUnexpectedSuccess(self, test)
            self._write_status(test, "unexpected success")
        else:
            super().addUnexpectedSuccess(test)

    def addError(self, test, err) -> None:
        super().addError(test, err)
        self._print_failure_now(test, err)

    def addFailure(self, test, err) -> None:
        super().addFailure(test, err)
        self._print_failure_now(test, err)

    def addSubTest(self, test, subtest, err) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._print_failure_now(subtest, err)
