import test from 'node:test';
import assert from 'node:assert/strict';
import {makeTestButtonPayload} from '../src/test-url-button.mjs';
import {testReplyFor} from '../src/trigger-test.mjs';

test('pilot reply creates one URL button and omits raw URL',()=>{
  const reply=testReplyFor({text:'ORIONTESTE',phone:'5521997557364',allowedPhone:'5521997557364'});
  const payload=makeTestButtonPayload(reply);
  assert.equal(payload.templateButtons.length,1);
  assert.equal(payload.templateButtons[0].urlButton.displayText,'VER AGENDAMENTO');
  assert.equal(payload.templateButtons[0].urlButton.url,'https://orionagenda.pro');
  assert.doesNotMatch(payload.text,/https:\/\//);
  assert.match(payload.text,/AGENDAMENTO FICTÍCIO/);
});
test('rejects invalid payload',()=>assert.throws(()=>makeTestButtonPayload('other text')));
