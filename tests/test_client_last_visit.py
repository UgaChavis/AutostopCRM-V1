"""Client search dates keep repair-order day/month and ISO local time."""

import shutil
import subprocess
import unittest

from tests.web_asset_function_support import functions


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ClientLastVisitTests(unittest.TestCase):
    def test_last_visit_renderers_preserve_business_dates_and_iso_behavior(self):
        source = functions(
            "app_main_before_printing.js",
            "formatDate",
            "clientLastVisit",
            "clientMetaLine",
            "clientRowAriaLabel",
            "clientRowChipsHtml",
        )
        script = (
            source
            + r"""
const assert = require('node:assert/strict');
const compactPhoneLine = () => '';
const clientDisplayName = () => 'Synthetic';
const clientTypeDisplayLabel = () => 'Физ. лицо';
const clientHasUnverifiedLinks = () => false;
const escapeHtml = value => value;
for (const date of ['04.11.2026 19:30', '13.11.2026 19:30', '4.11.2026 19:30', '04.11.26 19:30']) {
  const client = {stats: {last_visit: date}};
  assert.ok(clientMetaLine(client).includes(date));
  assert.ok(clientRowAriaLabel(client).includes(date));
  assert.ok(clientRowChipsHtml(client).includes(date));
}
const iso = '2026-11-04T19:30:00Z';
const expected = formatDate(iso);
assert.ok(clientMetaLine({stats: {last_visit: iso}}).includes(expected));
assert.equal(clientMetaLine({stats: {last_visit: ''}}), 'нет данных');
assert.ok(clientMetaLine({stats: {last_visit: 'invalid'}}).includes('invalid'));
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, timeout=15, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
