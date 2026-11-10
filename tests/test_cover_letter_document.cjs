const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
require('../static/cover_letter_builder.js');
const {validate, mergeForm} = globalThis.CoverLetterDocument;
const sample = JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'../cover_letter_builder.json'),'utf8'));
test('form round trip preserves every document field and additional JSON properties', () => {
  const original = structuredClone(sample);
  original.metadata = {notes:['keep']}; original.applicant.extra = 'keep nested';
  const form = structuredClone(sample);
  assert.deepEqual(mergeForm(original, form), original);
  form.recipient.company = 'Updated & Co'; form.paragraphs.reverse();
  const result = mergeForm(original, form);
  assert.equal(result.recipient.company, 'Updated & Co');
  assert.deepEqual(result.paragraphs, form.paragraphs);
  assert.deepEqual(result.metadata, original.metadata);
  assert.equal(result.applicant.extra, 'keep nested');
  assert.deepEqual(JSON.parse(JSON.stringify(validate(result))), result);
});
test('invalid field types and calendar dates are rejected', () => {
  for(const patch of [{paragraphs:'bad'},{paragraphs:[]},{date:'2026-02-30'},{recipient:[]},{signature:42}]) {
    assert.throws(()=>validate({...sample,...patch}));
  }
});
