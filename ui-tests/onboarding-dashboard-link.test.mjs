import test from 'node:test';
import assert from 'node:assert/strict';
import {fullState,makeOnboarding,flush} from './onboarding-harness.mjs';

for(const activity of ['services','products','both']){
  test('completed '+activity+' onboarding opens Dashboard, never a legacy operator route',async t=>{
    const data=fullState({current_step:'review',status:'complete'});
    data.configuration.activity=activity;
    const h=await makeOnboarding({data});t.after(()=>h.close());
    const visited=[];
    h.bridge.onOpenPanel=async(route,snapshot)=>visited.push({route,snapshot});
    h.click('[data-ob-action=open-dashboard]');await flush();
    assert.equal(visited.length,1);
    assert.equal(visited[0].route,'dashboard');
    assert.equal(visited[0].snapshot.configuration.activity,activity);
    assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
    assert.deepEqual(h.errors,[]);
  });
}
