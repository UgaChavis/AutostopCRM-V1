"""Legacy repair-order dates retain the same calendar date on save."""

import shutil
import subprocess
import unittest

from tests.web_asset_function_support import functions


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RepairOrderDateCanonicalizationTests(unittest.TestCase):
    def test_business_dates_and_iso(self):
        source = functions(
            "app_main_before_printing.js",
            "repairOrderPadDatePart",
            "repairOrderCanonicalDateValue",
            "repairOrderFormDateDisplayValue",
            "repairOrderListDateDisplayValue",
        )
        script = (
            source
            + r"""
const assert = require('node:assert/strict');
for (const [input, canonical, listed] of [
  ['4.11.2026 19:30', '04.11.2026 19:30', '04.11.26'],
  ['4.1.26 9:3', '04.01.2026 09:03', '04.01.26'],
  ['04.11.68 19:30', '04.11.2068 19:30', '04.11.68'],
  ['04.11.69 19:30', '04.11.1969 19:30', '04.11.69'],
  ['04.11.1968 19:30', '04.11.1968 19:30', '04.11.1968'],
  ['04.11.2069 19:30', '04.11.2069 19:30', '04.11.2069'],
  ['04.11.99', '04.11.1999 00:00', '04.11.99'],
  ['04.11.2026 19:30:45', '04.11.2026 19:30:45', '04.11.26'],
  ['4.11.69 9:3:00', '04.11.1969 09:03:00', '04.11.69'],
  ['4.1.2026 9:3:4', '04.01.2026 09:03:04', '04.01.26'],
  ['4.1.2026 9:3:0', '04.01.2026 09:03:00', '04.01.26'],
  ['04.11.2069 19:30:45', '04.11.2069 19:30:45', '04.11.2069'],
]) {
  assert.equal(repairOrderCanonicalDateValue(input), canonical);
  assert.equal(repairOrderCanonicalDateValue(repairOrderFormDateDisplayValue(input)), canonical);
  assert.equal(repairOrderListDateDisplayValue(input), listed);
}
const iso = '2026-11-04T19:30:00Z';
const date = new Date(iso);
const expected = [date.getDate(), date.getMonth() + 1, date.getFullYear()]
  .map((part, index) => String(part).padStart(index === 2 ? 4 : 2, '0')).join('.')
  + ' ' + [date.getHours(), date.getMinutes()].map(part => String(part).padStart(2, '0')).join(':');
assert.equal(repairOrderCanonicalDateValue(iso), expected);
assert.equal(repairOrderCanonicalDateValue('invalid date'), 'invalid date');
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, timeout=15, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
