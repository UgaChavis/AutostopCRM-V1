"""Keep the first test failure visible even when later native cleanup stalls."""

from __future__ import annotations

import unittest


class ImmediateTracebackResult(unittest.TextTestResult):
    def _print_failure_now(self, test, error) -> None:
        self.stream.writeln(f"\nImmediate failure detail: {test.id()}")
        self.stream.writeln(self._exc_info_to_string(error, test))
        self.stream.flush()

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
