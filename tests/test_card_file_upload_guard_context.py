"""Upload markers reject uncertain repeats without storing file or session data."""

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.web_asset_function_support import functions


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class CardFileUploadGuardContextTests(unittest.TestCase):
    def test_receipts_cannot_reuse_existing_or_batch_ids(self):
        definitions = functions(
            "card_save_state.js", "cardFileUploadInitialIds", "cardFileUploadReceipt"
        )
        script = (
            """
const assert=require('node:assert/strict');
function mobileCardHasRequiredFields(card){return Boolean(card && Array.isArray(card.attachments));}
function findCardAttachment(card,id){return card.attachments.find(item=>item.id===id&&!item.removed);}
"""
            + definitions
            + """
const card={id:'card',attachments:[{id:'active',removed:false},{id:'tombstone',removed:true}]};
const initial=cardFileUploadInitialIds(card,'card');
assert.deepEqual([...initial],['active','tombstone']);
for(const invalid of [[{}],[{id:' x ',removed:false}],[{id:'x',removed:'false'}],[{id:'x',removed:false},{id:'x',removed:true}]]){
 assert.throws(()=>cardFileUploadInitialIds({id:'card',attachments:invalid},'card'));
}
function response(id){return {attachment:{id,removed:false},card:{id:'card',attachments:[{id,removed:false}]}};}
assert.equal(cardFileUploadReceipt(response('active'),'card',initial,[]),'');
assert.equal(cardFileUploadReceipt(response('tombstone'),'card',initial,[]),'');
assert.equal(cardFileUploadReceipt(response('prior'),'card',initial,['prior']),'');
assert.equal(cardFileUploadReceipt(response('new'),'card',initial,['prior']),'new');
"""
        )
        result = subprocess.run(
            ["node"],
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_marker_ownership_storage_failures_and_error_classification(self):
        definitions = functions(
            "card_save_state.js",
            "beginCardFileUpload",
            "finishCardFileUpload",
            "cardFileUploadErrorIsDefinite",
        )
        script = (
            """
const assert=require('node:assert/strict');
const cardFileUploadEffects=new Map();
const entries=new Map();
let failSet=false,failRemove=false,failGet=false;
const window={sessionStorage:{
 getItem(key){if(failGet)throw Error('get');return entries.has(key)?entries.get(key):null;},
 setItem(key,value){if(failSet)throw Error('set');entries.set(key,value);},
 removeItem(key){if(failRemove)throw Error('remove');entries.delete(key);},
}};
"""
            + definitions
            + """
const a=beginCardFileUpload('opaque-card');
assert.deepEqual([...entries],[['autostop:card-file-upload:"opaque-card"','1']]);
assert.throws(()=>beginCardFileUpload('opaque-card'));
assert.equal(beginCardFileUpload('opaque-card',a),a);
const b=beginCardFileUpload('other-card');
assert.equal(finishCardFileUpload({key:a.key}),false);
failRemove=true;assert.equal(finishCardFileUpload(a),false);
assert.throws(()=>beginCardFileUpload('opaque-card'));failRemove=false;
assert.equal(finishCardFileUpload(a),true);assert.equal(finishCardFileUpload(b),true);
failSet=true;assert.throws(()=>beginCardFileUpload('fresh'));failSet=false;
assert.equal(finishCardFileUpload(beginCardFileUpload('fresh')),true);
failGet=true;assert.throws(()=>beginCardFileUpload('unavailable'));failGet=false;
for(const status of [408,429,500])assert.equal(Boolean(cardFileUploadErrorIsDefinite({status,code:'validation_error'})),false);
assert.equal(Boolean(cardFileUploadErrorIsDefinite({status:422,code:'validation_error'})),true);
assert.equal(Boolean(cardFileUploadErrorIsDefinite({status:422,code:'unknown'})),false);
"""
        )
        result = subprocess.run(
            ["node"],
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
