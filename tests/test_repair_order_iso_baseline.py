"""ISO date fields retain their text and date-only rows do not shift."""

import os
import shutil
import subprocess
import unittest

from tests.web_asset_function_support import functions


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RepairOrderIsoBaselineTests(unittest.TestCase):
    def test_iso_fields_and_date_only_list_in_two_timezones(self):
        source = functions(
            "app_main_before_printing.js",
            "repairOrderPadDatePart",
            "repairOrderCanonicalDateValue",
            "repairOrderFormDateDisplayValue",
            "repairOrderInputDateDisplayValue",
            "repairOrderFormDateValue",
            "repairOrderIsoValid",
            "repairOrderIsoFieldsValid",
            "repairOrderListDateDisplayValue",
        )
        script = (
            source
            + r"""
const assert = require('node:assert/strict');
const statuses = [];
function setStatus(message, error) { statuses.push([message, error]); }
const els = {repairOrderDate: null, repairOrderOpenedAt: null};
for (const value of ['2026-11-04T19:30:42.123+07:00', '2026-11-04t19:30:42Z',
  '2026-11-04', '2026-', '2026-11', '2026-02-30']) {
  const field = {value};
  assert.equal(repairOrderInputDateDisplayValue(value), value);
  assert.equal(repairOrderFormDateValue(field), value);
  assert.deepEqual(field, {value});
}
assert.equal(repairOrderFormDateValue({value: '4.1.2026 9:3:4'}), '04.01.2026 09:03:04');
assert.equal(repairOrderListDateDisplayValue('2026-11-04'), '04.11.26');
assert.equal(repairOrderListDateDisplayValue('1968-11-04'), '04.11.1968');
for (const value of ['2024-02-29', '2026-11-04T05:30:42.123456+07:00',
  '2026-11-04t05:30Z', '2026-11-04 05:30:00-04:00',
  '2026-11-04T05:30+07', '2026-11-04T05:30-0430',
  '0002-01-01T00:00+14:00', '9998-12-31T23:59-14:00']) {
  assert.equal(repairOrderIsoValid(value), true, value);
}
for (const value of ['2026-', '2026-11', '0000-01-01', '0001-01-01T00:00:00+14:00',
  '9999-12-31T23:59:59-14:00', '2026-02-29', '2024-04-31',
  '2026-11-04T24:00Z', '2026-11-04T23:60Z', '2026-11-04T23:00:60Z',
  '2026-11-04T23:00+24:00', '2026-11-04T23:00+07:60', '2026-11-04T23:00-0460', '2026-11-04T23:00+',
  '2026-11-04T23:00z']) {
  assert.equal(repairOrderIsoValid(value), false, value);
}
const original = '2026-11-04T05:30:42.123+07:00';
const date = {value: original, dataset: {initialDate: original}, focus() { this.focused = true; }};
els.repairOrderDate = date;
assert.equal(repairOrderIsoFieldsValid(), true);
for (const invalid of ['2026', '2026-', '2026-02-30', '2026-11-04T05:30', '04.11.2026 05:30']) {
  date.value = invalid;
  assert.equal(repairOrderIsoFieldsValid(), false, invalid);
}
date.value = '2026-11-04T05:31:42.123+07:00';
assert.equal(repairOrderIsoFieldsValid(), true);
for (const value of ['2026-11-04T05:31+07', '2026-11-04T05:31+0700', '2026-11-04T05:31Z']) {
  date.value = value;
  assert.equal(repairOrderIsoFieldsValid(), true, value);
}
date.dataset.initialDate = '2026-11-04T05:30';
date.value = '05.11.2026 08:00';
assert.equal(repairOrderIsoFieldsValid(), true, 'naive ISO may become a business date');
date.value = '29.02.24 10:00';
assert.equal(repairOrderIsoFieldsValid(), true);
for (const invalid of ['31.02.26 10:00', '29.02.23 10:00', '04.11.26 25:00']) {
  date.value = invalid;
  assert.equal(repairOrderIsoFieldsValid(), false, invalid);
}
date.value = '05.11.2026 invalid';
assert.equal(repairOrderIsoFieldsValid(), false);
date.dataset.initialDate = '2026-02-30';
date.value = '2026-02-30';
assert.equal(repairOrderIsoFieldsValid(), true, 'unchanged malformed legacy must remain readable');
date.dataset.initialDate = '0001-01-01T00:00:00+14:00';
date.value = date.dataset.initialDate;
assert.equal(repairOrderIsoFieldsValid(), true, 'unchanged extreme legacy must not be rewritten');
date.dataset.initialDate = '2026-11-04T05:30+07:00:30';
date.value = date.dataset.initialDate;
assert.equal(repairOrderIsoFieldsValid(), true, 'unchanged second-precision offset remains readable');
date.value = '2026-11-04T05:31+07:00:30';
assert.equal(repairOrderIsoFieldsValid(), false, 'editing second-precision offset is outside this gate');
date.dataset.initialDate = '04.11.26 05:30';
date.value = '05.11.26 05:30';
assert.equal(repairOrderIsoFieldsValid(), true, 'legacy dotted editing is unchanged');
for (const value of ['2', '20', '202', '2026', '2026-']) {
  date.value = value;
  assert.equal(repairOrderIsoFieldsValid(), false, value);
}
assert.ok(statuses.some(([message, error]) => error && message.includes('ЧАСОВОЙ ПОЯС')));
"""
        )
        for timezone in ("America/New_York", "Asia/Krasnoyarsk"):
            with self.subTest(timezone=timezone):
                result = subprocess.run(
                    ["node"],
                    input=script,
                    text=True,
                    capture_output=True,
                    timeout=15,
                    env={**os.environ, "TZ": timezone},
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
