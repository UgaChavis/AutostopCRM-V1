import io
import unittest

from scripts.benchmark_unit_suite import TimedResult


class BenchmarkUnitSuiteTests(unittest.TestCase):
    def test_timing_preserves_results_and_skip_reason(self):
        def skipped():
            raise unittest.SkipTest("fixture platform limitation")

        suite = unittest.TestSuite(
            [
                unittest.FunctionTestCase(lambda: None),
                unittest.FunctionTestCase(skipped),
            ]
        )
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=TimedResult).run(suite)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 2)
        self.assertEqual([reason for _, reason in result.skipped], ["fixture platform limitation"])
        self.assertEqual(len(result.timings), 2)
        self.assertTrue(all(row["seconds"] >= 0 for row in result.timings))
