import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {entry,fullState,makeOnboarding} from './onboarding-harness.mjs';

test('one shared spacing contract covers all setup forms',async()=>{
  const css=await readFile(new URL('../app/static/orion-onboarding.css',import.meta.url),'utf8');
  for(const token of [
    '--ob-control-gap:8px',
    '--ob-field-gap:22px',
    '--ob-divider-gap:18px',
    '--ob-actions-gap:20px',
    'gap:var(--ob-field-gap)',
    'gap:var(--ob-control-gap)',
    'margin-top:var(--ob-divider-gap)',
    'padding-top:var(--ob-actions-gap)',
  ])assert.ok(css.includes(token),'missing shared form token: '+token);
  assert.match(css,/\.ob-root \.ob-form > fieldset > :not\(\.ob-actions\) \+ :not\(\.ob-actions\)/);
  assert.match(css,/\.ob-root \.ob-form \.ob-field > label/);
  assert.match(css,/\.ob-root \.ob-form \[data-ob-address\] > \.ob-note/);
  assert.match(css,/@container orion-onboarding \(max-width:560px\)/);
  assert.match(css,/@media\(max-width:767px\)/);
});

test('the shared layout applies to each real configuration step without side effects',async()=>{
  const scenarios=[
    {step:'business'},
    {step:'location'},
    {step:'professionals',action:'[data-ob-action=add-professional]'},
    {step:'catalog',action:'[data-ob-action=edit][data-ob-kind=service]'},
    {step:'hours'},
    {step:'channels'},
    {step:'review'},
  ];
  for(const {step,action} of scenarios){
    const h=await makeOnboarding({data:fullState({current_step:step})});
    try{
      if(action)h.click(action);
      const form=h.element('form.ob-form');
      assert.ok(form.querySelector(':scope > fieldset > .ob-actions'),step+' has its own divider/actions');
      const fields=[...form.querySelectorAll('.ob-field')];
      if(step!=='review')assert.ok(fields.length>0,step+' has actual editable fields');
      for(const field of fields){
        const label=field.querySelector(':scope > label');
        const control=field.querySelector(':scope > input,:scope > select,:scope > textarea,:scope > .os-field-unit input');
        assert.ok(label&&control,step+' keeps its label/control association');
        assert.equal(label.htmlFor,control.id,step+' has an accessible label');
      }
      if(step==='location'){
        assert.ok(form.querySelector('[data-ob-address] .ob-postal-field'));
        assert.equal(form.querySelector('[name=postal_code]').dataset.orionField,'postal-code');
      }
      if(step==='business')
        assert.equal(form.querySelector('[name=contact_phone]').dataset.orionField,'phone');
      assert.deepEqual(h.errors,[],step+' has no DOM exceptions');
      assert.equal(h.calls.filter(call=>call.method!=='GET').length,0,step+' does not write data');
    }finally{h.close();}
  }
});

test('first-business creation uses the same layout as editing',async()=>{
  const h=await makeOnboarding({decision:entry({tenant_id:null,businesses:[]})});
  try{
    assert.ok(h.element('[data-ob-form=create] > fieldset > .ob-fields.ob-business-fields'));
    assert.ok(h.element('[data-ob-form=create] > fieldset > .ob-actions'));
    assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  }finally{h.close();}
});
