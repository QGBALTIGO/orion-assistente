import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite, flush, planState, tenantA} from './harness.mjs';

async function affiliateShell() {
  const lifecycle={mounts:0,unmounts:0,views:[]},scrolls=[];
  let revision=1;
  const h=await makeSuite({
    initialRoute:'dashboard',
    bridge:{tenants:[{id:tenantA,name:'Ofertas sintéticas',slug:'ofertas',timezone:'America/Campo_Grande',
      role:'owner',status:'active',workspace_profile:'affiliate'}]},
    permissions:()=>({effective_plan:planState('none',{
      workspace_profile:'affiliate',layout_modes:['classic','app'],revision:'synthetic-drawer-'+revision,
    })}),
    configureWindow(window){
      window.scrollTo=options=>scrolls.push(options);
      // Exercise the real shell lifecycle without loading data or a network.
      window.OrionAffiliateWorkspace={mount({host,view}){
        lifecycle.mounts++;
        host.dataset.view=view;
        host.innerHTML='<button id="syntheticDashboardControl" type="button">Métrica do Dashboard</button>';
        return {
          setView(next){lifecycle.views.push(next);host.dataset.view=next;},
          unmount(){lifecycle.unmounts++;host.replaceChildren();},
        };
      }};
    },
  });
  await flush();
  return Object.assign(h,{lifecycle,scrolls,changePermissions(){revision++;}});
}

function observeFocus(element) {
  const calls=[],focus=element.focus.bind(element);
  element.focus=options=>{calls.push(options);focus(options);};
  return calls;
}

test('backdrop closes the drawer once and restores focus without scrolling',async t=>{
  const h=await affiliateShell();t.after(()=>h.close());
  const menu=h.document.querySelector('#osMenu');
  menu.focus();h.click('#osMenu');
  assert.equal(menu.getAttribute('aria-expanded'),'true');
  assert.equal(h.document.querySelector('#appView').classList.contains('os-menu-open'),true);
  const focusCalls=observeFocus(menu);
  h.click('#osScrim');
  assert.equal(menu.getAttribute('aria-expanded'),'false');
  assert.equal(h.document.querySelector('#appView').classList.contains('os-menu-open'),false);
  assert.equal(h.document.activeElement,menu);
  assert.equal(focusCalls.length,1);
  assert.equal(focusCalls[0]?.preventScroll,true);

  const control=h.document.querySelector('#syntheticDashboardControl');control.focus();
  h.click('#osScrim');
  assert.equal(h.document.activeElement,control);
  assert.equal(focusCalls.length,1,'closing an already closed menu must not reuse its old trigger');
  assert.deepEqual(h.errors,[]);
});

test('permission reconciliation preserves Dashboard focus and its mounted controller',async t=>{
  const h=await affiliateShell();t.after(()=>h.close());
  const host=h.document.querySelector('#osAffiliateView'),menu=h.document.querySelector('#osMenu');
  menu.focus();h.click('#osMenu');h.click('#osScrim');
  const focusCalls=observeFocus(menu);
  const control=h.document.querySelector('#syntheticDashboardControl');control.focus();
  const scrollCount=h.scrolls.length;
  h.changePermissions();
  h.window.dispatchEvent(new h.window.Event('focus'));
  await flush();
  assert.equal(h.document.activeElement,control);
  assert.equal(focusCalls.length,0,'a background same-route reconciliation must not focus the header');
  assert.equal(h.scrolls.length,scrollCount);
  assert.equal(h.document.querySelector('#osAffiliateView'),host);
  assert.equal(h.document.querySelector('#syntheticDashboardControl'),control);
  assert.equal(h.lifecycle.mounts,1);
  assert.equal(h.lifecycle.unmounts,0);
  assert.deepEqual(h.errors,[]);
});

test('Escape keeps the drawer accessible and restores the original trigger',async t=>{
  const h=await affiliateShell();t.after(()=>h.close());
  const menu=h.document.querySelector('#osMenu');menu.focus();h.click('#osMenu');
  const focusCalls=observeFocus(menu);
  h.document.activeElement.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(menu.getAttribute('aria-expanded'),'false');
  assert.equal(h.document.activeElement,menu);
  assert.equal(focusCalls.length,1);
  assert.equal(focusCalls[0]?.preventScroll,true);
  assert.deepEqual(h.errors,[]);
});

test('sidebar navigation still closes the drawer and changes the affiliate view',async t=>{
  const h=await affiliateShell();t.after(()=>h.close());
  const host=h.document.querySelector('#osAffiliateView'),menu=h.document.querySelector('#osMenu');
  menu.focus();h.click('#osMenu');
  const focusCalls=observeFocus(menu),scrollCount=h.scrolls.length;
  h.click('#osNav [data-os-route="afiliados-ofertas"]');
  await flush();
  assert.equal(menu.getAttribute('aria-expanded'),'false');
  assert.equal(focusCalls.length,1);
  assert.equal(focusCalls[0]?.preventScroll,true);
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'afiliados-ofertas');
  assert.equal(h.document.querySelector('#osAffiliateView'),host);
  assert.equal(host.dataset.view,'ofertas');
  assert.equal(h.scrolls.length,scrollCount+1,'intentional page navigation still scrolls to its start');
  assert.equal(h.lifecycle.mounts,1);
  assert.equal(h.lifecycle.unmounts,0);
  assert.deepEqual(h.errors,[]);
});
