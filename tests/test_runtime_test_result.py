from __future__ import annotations

import io
import unittest

from tests.runtime_test_result import ImmediateTracebackResult


class RuntimeTestResultTests(unittest.TestCase):
    def test_parallel_verbose_results_keep_each_status_with_its_test(self):
        class Example(unittest.TestCase):
            def test_slow(self):
                pass

            def test_fast(self):
                pass

        stream = io.StringIO()
        slow_result = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=ImmediateTracebackResult
        )._makeResult()
        fast_result = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=ImmediateTracebackResult
        )._makeResult()
        slow = Example("test_slow")
        fast = Example("test_fast")

        slow_result.startTest(slow)
        fast_result.startTest(fast)
        try:
            self.fail("Synthetic cohort failure")
        except AssertionError as error:
            slow_result.addFailure(slow, (type(error), error, error.__traceback__))
        fast_result.addSuccess(fast)

        lines = stream.getvalue().splitlines()
        self.assertIn(f"START {slow.id()}", lines)
        self.assertIn(f"START {fast.id()}", lines)
        self.assertIn(f"{slow.id()} ... FAIL", lines)
        self.assertIn(f"{fast.id()} ... ok", lines)
        self.assertIn(f"Immediate failure detail: {slow.id()}", lines)

    def test_errors_failures_and_subtests_are_visible_during_cleanup(self):
        for kind in ("error", "failure", "subtest"):
            with self.subTest(kind=kind):
                output = io.StringIO()
                observed = []

                class Broken(unittest.TestCase):
                    def test_first(self):
                        self.addCleanup(lambda: observed.append(output.getvalue()))
                        if kind == "error":
                            raise RuntimeError("original error")
                        if kind == "failure":
                            self.fail("original assertion")
                        with self.subTest(value="synthetic"):
                            self.fail("original subtest")

                    def test_second(self):
                        observed.append("later test ran once")

                result = unittest.TextTestRunner(
                    stream=output, verbosity=2, resultclass=ImmediateTracebackResult
                ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Broken))
                self.assertEqual(result.testsRun, 2)
                self.assertFalse(result.wasSuccessful())
                self.assertEqual(observed[-1], "later test ran once")
                self.assertIn("Immediate failure detail:", observed[0])
                self.assertIn("Traceback (most recent call last):", observed[0])
                self.assertIn("original ", observed[0])

    def test_success_and_skip_do_not_become_failures(self):
        class Passing(unittest.TestCase):
            def test_success(self):
                pass

            @unittest.skip("explicit fixture skip")
            def test_skip(self):
                self.fail("unreachable")

        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output, resultclass=ImmediateTracebackResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Passing)
        )
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 2)
        self.assertEqual(len(result.skipped), 1)
        self.assertNotIn("Immediate failure detail:", output.getvalue())
