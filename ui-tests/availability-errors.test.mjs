import test from 'node:test';
import assert from 'node:assert/strict';
import {flush} from './harness.mjs';
import {makePublic, response, available} from './public-harness.mjs';

async function openDate(h){
  await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next');
  await flush();
}

test('a closed workday is not confused with connection failure',async t=>{
  const h=await makePublic({respond:url=>url.includes('/availability?')
    ?response({slots:[],availability_reason:'no_working_hours',timezone:'America/Campo_Grande',quote:null}):undefined});
  t.after(()=>h.close()); await openDate(h);
  assert.match(h.document.getElementById('time-slots').textContent,/não tem expediente cadastrado neste dia/);
  assert.equal(h.document.getElementById('booking-next').disabled,true);
  assert.equal(h.calls.some(x=>x.options.method==='POST'),false);
});
test('a valid empty grid describes service duration and shift without inventing a booking',async t=>{
  const h=await makePublic({respond:url=>url.includes('/availability?')
    ?response({slots:[],availability_reason:'no_matching_slots',timezone:'America/Campo_Grande',quote:null}):undefined});
  t.after(()=>h.close()); await openDate(h);
  assert.match(h.document.getElementById('time-slots').textContent,/Não há vagas compatíveis com a duração/);
  assert.equal(h.document.getElementById('booking-next').disabled,true);
});
test('a server error is explicitly a failed lookup with a usable retry, not empty availability',async t=>{
  let tries=0;
  const h=await makePublic({respond:url=>{
    if(!url.includes('/availability?'))return undefined;
    return ++tries===1?response({detail:'Consulta temporariamente indisponível'},503):response(available());
  }});
  t.after(()=>h.close()); await openDate(h);
  const slots=h.document.getElementById('time-slots');
  assert.match(slots.textContent,/Consulta temporariamente indisponível/);
  assert.doesNotMatch(slots.textContent,/Nenhum horário disponível|Nenhum horário livre/);
  assert.equal(h.document.getElementById('booking-next').disabled,true);
  h.click('#time-slots .outline');await flush();
  assert.match(slots.textContent,/09:00/);
  assert.equal(tries,2);
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0);
  assert.deepEqual(h.errors,[]);
});
