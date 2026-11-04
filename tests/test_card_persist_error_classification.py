"""Ambiguous card creation responses must not cause a blind repeat."""

import shutil
import subprocess
import unittest

from tests.web_asset_function_support import functions


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class CardPersistErrorClassificationTests(unittest.TestCase):
    def test_ambiguous_statuses_keep_create_checkpoint(self):
        source = functions("app_main_before_printing.js", "persistCardPayload")
        script = (
            source
            + r"""
const assert = require('node:assert/strict');
const window = globalThis;
const state = {
  viewerStateGeneration: 1, operatorSessionToken: 'synthetic', cardEditingGeneration: 1,
  pendingCardClientId: '', pendingCardClientVehicleId: '', pendingCreateClientVehicleFromCard: false,
  editingId: '', cardPersistCheckpoint: null,
};
let status, code, creates, reads;
async function api(path) {
  if (path === '/api/create_card') {
    creates++;
    throw Object.assign(new Error('synthetic'), {status, code});
  }
  assert.equal(path, '/api/get_board_snapshot?compact=1&include_archive=0');
  reads++;
  return {};
}
async function attempt() {
  try { await persistCardPayload({title: 'SYNTHETIC', tags: [], vehicle_profile: {}}); }
  catch (_) { return; }
  assert.fail('Expected failure');
}
(async () => {
  for (const value of [408, 429, 500]) {
    status = value; code = ''; creates = 0; reads = 0; state.cardPersistCheckpoint = null;
    await attempt();
    assert.equal(state.cardPersistCheckpoint.uncertain, 'card', String(value));
    await attempt();
    assert.equal(creates, 1, String(value));
    assert.equal(reads, 2, String(value));
  }
  status = 400; code = 'validation_error'; creates = 0; reads = 0; state.cardPersistCheckpoint = null;
  await attempt(); await attempt();
  assert.equal(creates, 2);
  assert.equal(reads, 0);
  for (const value of [400, 401, 403, 404, 409, 422, 499]) {
    assert.equal(cardPersistErrorIsDefinite({status: value, code: 'other'}), true, String(value));
  }
  assert.equal(cardPersistErrorIsDefinite({status: 400, code: 'invalid_json'}), false);
  assert.equal(cardPersistErrorIsDefinite({}), false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, timeout=15, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
