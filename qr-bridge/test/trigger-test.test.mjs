import test from 'node:test';
import assert from 'node:assert/strict';
import {testReplyFor} from '../src/trigger-test.mjs';

test('responds only to authorized exact keyword', () => {
  const args={phone:'5521997557364',allowedPhone:'5521997557364'};
  const reply=testReplyFor({...args,text:' orionteste '});
  assert.match(reply,/AGENDAMENTO FICTÍCIO/);
  assert.match(reply,/https:\/\/orionagenda\.pro/);
  assert.equal(testReplyFor({...args,text:'oi ORIONTESTE'}),null);
  assert.equal(testReplyFor({...args,text:'ORIONTESTE',phone:'5511999999999'}),null);
  assert.equal(testReplyFor({...args,text:'ORIONTESTE',fromMe:true}),null);
  assert.equal(testReplyFor({...args,text:'ORIONTESTE',allowedPhone:''}),null);
});
