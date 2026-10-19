from __future__ import annotations

import io
import unittest

from tests.runtime_test_result import ImmediateTracebackResult


class RuntimeTestResultTests(unittest.TestCase):
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
