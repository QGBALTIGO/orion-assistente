import test from 'node:test';
import assert from 'node:assert/strict';
import {fullState,makeOnboarding,flush} from './onboarding-harness.mjs';

function quickHours(saved=null){
 const state=fullState({current_step:'hours'});
 state.steps=state.steps.map(row=>row.id==='catalog'
   ?{...row,id:'profile',label:'Perfil da empresa'}
   :row.id==='channels'?{...row,id:'goals',label:'Desafios e objetivos'}:row);
 if(saved)state.configuration.business_opening_hours=saved;
 return state;
}

test('business opening hours has seven days, no professional selector and responsive card layout',async t=>{
 const h=await makeOnboarding({data:quickHours()});t.after(()=>h.close());
 assert.equal(h.element('#ob-step-title').textContent,'Quando você atende');
 assert.equal(h.document.querySelector('[name=professional_id]'),null);
 assert.equal(h.document.querySelector('[name=weekdays]'),null);
 assert.equal(h.document.querySelectorAll('[data-ob-business-day]').length,7);
 assert.equal(h.element('[data-ob-form=business-hours]').dataset.obForm,'business-hours');
 assert.equal(h.element('[name=hours_0_start]').disabled,true);
 h.click('[name=hours_0_open]');await flush();
 assert.equal(h.element('[name=hours_0_start]').disabled,false);
 assert.equal(h.element('[name=hours_0_break_start]').disabled,true);
 h.click('[name=hours_0_has_break]');await flush();
 assert.equal(h.element('[name=hours_0_break_start]').disabled,false);
 assert.deepEqual(h.errors,[]);
});

test('saved company hours reopen without altering professional schedules',async t=>{
 const days=Array.from({length:7},(_,weekday)=>weekday===0
  ?{weekday,open:true,start_time:'09:00',end_time:'18:00',break_start:'12:00',break_end:'13:00'}
  :{weekday,open:false});
 const h=await makeOnboarding({data:quickHours({days})});t.after(()=>h.close());
 assert.equal(h.element('[name=hours_0_open]').checked,true);
 assert.equal(h.element('[name=hours_0_start]').value,'09:00');
 assert.equal(h.element('[name=hours_0_break_start]').value,'12:00');
 assert.equal(h.element('[name=hours_6_open]').checked,false);
 assert.equal(h.document.querySelector('[name=professional_id]'),null);
 assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
});
