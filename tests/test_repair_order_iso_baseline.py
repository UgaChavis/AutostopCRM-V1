"""Unchanged ISO fields retain their original precision and offset."""

import shutil
import subprocess
import unittest

from tests.web_asset_function_support import functions


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RepairOrderIsoBaselineTests(unittest.TestCase):
    def test_read_is_pure_and_only_uses_the_matching_baseline(self):
        source = functions(
            "app_main_before_printing.js",
            "repairOrderPadDatePart",
            "repairOrderCanonicalDateValue",
            "repairOrderFormDateDisplayValue",
            "repairOrderFormDateValue",
        )
        script = (
            source
            + r"""
const assert = require('node:assert/strict');
const original = '2026-11-04T19:30:42.123+07:00';
const display = repairOrderFormDateDisplayValue(original);
const field = {value: display, dataset: {sourceDate: original, displayDate: display}};
assert.equal(repairOrderFormDateValue(field), original);
assert.deepEqual(field, {value: display, dataset: {sourceDate: original, displayDate: display}});
field.value = '4.1.2026 9:3:4';
assert.equal(repairOrderFormDateValue(field), '04.01.2026 09:03:04');
field.value = display;
assert.equal(repairOrderFormDateValue(field), original);
field.dataset = {sourceDate: '', displayDate: display};
assert.equal(repairOrderFormDateValue(field), repairOrderCanonicalDateValue(display));
field.dataset = {sourceDate: '04.11.69 19:30', displayDate: display};
assert.equal(repairOrderFormDateValue(field), repairOrderCanonicalDateValue(display));
const lowercase = '2026-11-04t19:30:42.123+07:00';
field.dataset = {sourceDate: lowercase, displayDate: display};
assert.equal(repairOrderFormDateValue(field), lowercase);
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, timeout=15, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
