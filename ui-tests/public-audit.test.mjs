import test from 'node:test';
import assert from 'node:assert/strict';
import {makePublic,catalogue,publicProfessional,publicService} from './public-harness.mjs';
import {flush} from './harness.mjs';

test('PUB-01: returning through services preserves the eligible chosen professional',async t=>{
  const data=catalogue(),second='70000000-0000-4000-8000-000000000027';
  data.professionals.push({id:second,name:'Segundo profissional'});
  data.services[0].professional_ids.push(second);
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');h.click('#booking-next');await flush();
  h.click(`[data-professional-id="${second}"]`);await flush();
  h.click('#time-slots .slot');
  h.click('#booking-back');h.click('#booking-next');await flush();
  assert.equal(h.document.querySelector(`[data-professional-id="${second}"]`).getAttribute('aria-pressed'),'true');
  assert.equal(h.document.querySelector(`[data-professional-id="${publicProfessional}"]`).getAttribute('aria-pressed'),'false');
  assert.match(h.calls.filter(c=>c.url.includes('/availability?')).at(-1).url,new RegExp(second));
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});
