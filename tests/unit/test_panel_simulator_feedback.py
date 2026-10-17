"""Run the real panel controller against the existing deferred DOM/HTTP harness.

No browser session, network, WhatsApp send, or persistent conversation storage.
Node is available in the main CI runner; the Python-only Compose image skips
these DOM cases explicitly. Markup checks still run there.
"""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "app/static/panel.html"
NODE = shutil.which("node")


def test_feedback_markup_preserves_dialogs_and_accessible_export():
    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = {}
            self.dialogs = 0

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            self.dialogs += tag == "dialog"
            if "id" in attributes:
                assert attributes["id"] not in self.ids, "Duplicate panel ID"
                self.ids[attributes["id"]] = (tag, attributes)

    parsed = Markup()
    parsed.feed(PANEL.read_text(encoding="utf-8"))
    assert parsed.dialogs == 4  # Existing dialog elements; shared action dialog serves five flows.
    tag, export = parsed.ids["aiExportEvaluation"]
    assert tag == "button" and export["type"] == "button" and "disabled" in export
    assert all(item in parsed.ids for item in export["aria-describedby"].split())
    assert parsed.ids["aiEvaluationStatus"][1]["role"] == "status"


DOM_RUNNER = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {randomUUID}=require('node:crypto');
const panel=fs.readFileSync('app/static/panel.html','utf8');
const sections=[...panel.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(sections.length,2);
for(const section of sections)new vm.Script(section[1]);
const fixture=fs.readFileSync('qr-bridge/test/panel-ui.test.mjs','utf8');
const start=fixture.indexOf('function panelHarness(){'),end=fixture.indexOf('\nconst tick=',start);
assert.ok(start>=0&&end>start,'Existing panel DOM harness must remain available');
const downloads=[],blobs=[],revoked=[],storageWrites=[];
let forbidHtml=false;
let failDownload=false;
const downloadURL={
 createObjectURL(blob){if(failDownload)throw Error('synthetic download failure');blobs.push(blob);return 'blob:synthetic-'+blobs.length},
 revokeObjectURL(url){revoked.push(url)}
};
// Extend only browser primitives in the shared harness, not controller logic.
const harness=fixture.slice(start,end)
 .replace('setAttribute(){}','setAttribute(name,value){(this.attributes??={})[name]=String(value)} getAttribute(name){return this.attributes?.[name]??null} set innerHTML(value){if(htmlForbidden())throw Error("Unsafe HTML sink")}')
 .replace('focus(){this.focusCount++;activeElement=this}',
  'click(){if(this.tagName==="A")downloads.push({href:this.href,name:this.download});for(const fn of this.listeners.click||[])fn()} focus(){this.focusCount++;activeElement=this}')
 .replace('get activeElement(){return activeElement}', 'body:new Element("body"),get activeElement(){return activeElement}')
 .replace('confirm:()=>true,URLSearchParams',
  'Blob,URL:downloadURL,localStorage:{getItem:()=>null,setItem:(...args)=>storageWrites.push(args)},confirm:()=>true,URLSearchParams');
const sandbox={assert,vm,panel,sections,randomUUID,Intl,URLSearchParams,console,Blob,downloadURL,downloads,storageWrites,htmlForbidden:()=>forbidHtml};
vm.runInNewContext(harness+'\nglobalThis.makeHarness=panelHarness;',sandbox);
const makeHarness=sandbox.makeHarness;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const profile={runtime_connected:true,
 published:{id:'published-2',revision:2,profile:{style_rules:{style:'warm'}}},
 draft:{id:'draft-3',revision:3,profile:{style_rules:{style:'direct'}}}};
const previewUrl=tenant=>'/api/v1/tenants/'+tenant+'/ai/conversation-preview';
async function ready(h,tenant='tenant-a'){
 h.ui.authenticate();h.node('aiTenant').value=tenant;
 const loading=h.ui.aiLoadProfiles();h.take('/api/v1/tenants/'+tenant+'/business/profile').respond(profile);await loading;
 const checking=h.ui.aiCheckStatus();h.take('/api/v1/tenants/'+tenant+'/ai/status').respond({configured:true,preview_enabled:true,audio_preview_enabled:true});await checking;
}
function submit(h,text){h.node('aiInput').value=text;return h.node('aiForm').listeners.submit[0]({preventDefault(){}})}
async function reply(h,answer='Synthetic answer',stage='catalog'){
 const pending=submit(h,'PRIVATE INPUT NEVER EXPORTED');h.take(previewUrl(h.node('aiTenant').value),'POST').respond({answer,step:stage});await pending;
 return h.node('aiMessages').children.at(-1);
}
function group(bubble){return bubble.children.find(child=>child?.tagName==='FIELDSET')}
function buttons(bubble){return group(bubble).children.find(child=>child?.className==='ai-evaluation-options').children}
const click=node=>node.listeners.click[0]();
async function exportReport(h){click(h.node('aiExportEvaluation'));return JSON.parse(await blobs.at(-1).text())}
const noEvaluationWrites=(h,before)=>{
 assert.equal(h.requests.length,before,'Ratings/export must not make HTTP requests');
 assert.equal(storageWrites.length,0,'No permanent browser storage');
};

(async()=>{
 const scenario=process.argv[1];
 if(scenario==='compile')return;
 const h=makeHarness();await ready(h);forbidHtml=true;
 if(scenario==='ratings'){
  const answer='*<img src=x onerror=alert(1)>* _Synthetic answer_';
  const bubble=await reply(h,answer),user=h.node('aiMessages').children[0];
  assert.equal(group(user),undefined,'User messages must not be rated');
  const choices=buttons(bubble);assert.deepEqual(Array.from(choices,b=>b.textContent),['Seca','Insistente','Incorreta','Confusa','Fora do contexto']);
  assert.equal(group(bubble).children[0].tagName,'LEGEND');
  assert.match(group(bubble).children[0].textContent,/resposta de teste 1/);
  assert.ok(bubble.children.some(child=>child?.tagName==='STRONG'&&child.textContent.includes('<img')));
  const before=h.requests.length;choices[0].focus();click(choices[0]);click(choices[3]);
  assert.equal(choices[0].getAttribute('aria-pressed'),'true');
  assert.equal(choices[3].getAttribute('aria-pressed'),'true');
  assert.match(choices[0].getAttribute('aria-label'),/resposta de teste 1/);
  assert.equal(h.activeElement(),choices[0],'Toggle must retain keyboard focus');
  assert.equal(h.node('aiExportEvaluation').disabled,false);
  click(choices[0]);assert.equal(choices[0].getAttribute('aria-pressed'),'false');
  click(choices[3]);assert.equal(h.node('aiExportEvaluation').disabled,true);
  click(h.node('aiExportEvaluation'));assert.equal(downloads.length,0);
  noEvaluationWrites(h,before);return;
 }
 if(scenario==='export'){
  h.node('aiProfile').value='draft-3';h.node('aiProfile').listeners.change[0]();
  h.node('aiStyle').value='professional';h.node('aiStyle').listeners.change[0]();
  const bubble=await reply(h,'Marked factual answer','price');click(buttons(bubble)[2]);click(buttons(bubble)[4]);
  await reply(h,'UNMARKED RESPONSE NEVER EXPORTED');
  assert.equal(blobs.length,0,'Download must require explicit export');
  const before=h.requests.length,report=await exportReport(h);
  assert.equal(report.kind,'orion_simulation_feedback');assert.equal(report.simulation,true);assert.equal(report.production_changed,false);
  assert.equal(report.scope.tenant_id,'tenant-a');assert.equal(report.scope.profile_selection,'draft-3');
  assert.equal(report.scope.profile_version_id,'draft-3');assert.equal(report.scope.profile_revision,3);assert.equal(report.scope.style,'professional');
  assert.equal(report.evaluations.length,1);assert.equal(report.evaluations[0].answer,'Marked factual answer');
  assert.equal(report.evaluations[0].stage,'price');assert.deepEqual(report.evaluations[0].labels,['incorrect','off_context']);
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE INPUT|UNMARKED RESPONSE|synthetic-csrf/);
  assert.equal(blobs[0].type,'application/json');assert.equal(downloads.length,1);
  assert.equal(h.activeElement(),h.node('aiExportEvaluation'));
  await exportReport(h);assert.deepEqual(revoked,['blob:synthetic-1']);
  noEvaluationWrites(h,before);return;
 }
 if(scenario.startsWith('clear-')){
  const mode=scenario.slice(6),bubble=await reply(h,'OLD TENANT RESPONSE'),oldButton=buttons(bubble)[0];click(oldButton);
  await exportReport(h);const pending=submit(h,'late old input'),late=h.take(previewUrl('tenant-a'),'POST');
  if(mode==='conversation')h.node('aiReset').listeners.click[0]();
  if(mode==='tenant'){
   h.node('aiTenant').value='tenant-b';const changed=h.node('aiTenant').listeners.change[0]();
   h.take('/api/v1/tenants/tenant-b/business/profile').respond(profile);
   h.take('/api/v1/tenants/tenant-b/ai/status').respond({configured:true,preview_enabled:true});await changed;await tick();
  }
  if(mode==='version'){h.node('aiProfile').value='draft-3';h.node('aiProfile').listeners.change[0]()}
  if(mode==='style'){h.node('aiStyle').value='direct';h.node('aiStyle').listeners.change[0]()}
  if(mode==='logout')h.ui.showLogin();
  assert.equal(h.node('aiExportEvaluation').disabled,true);assert.deepEqual(revoked,['blob:synthetic-1']);
  click(oldButton);click(h.node('aiExportEvaluation'));assert.equal(downloads.length,1,'Detached feedback must not be exported again');
  late.respond({answer:'STALE ASSISTANT ANSWER',step:'confirm'});await pending;
  assert.equal(h.node('aiMessages').children.length,0);
  assert.equal(h.node('aiExportEvaluation').disabled,true);assert.equal(storageWrites.length,0);return;
 }
 if(scenario==='audio'){
  h.node('aiAudioFile').files=[{name:'PRIVATE-AUDIO-FILENAME.wav',type:'audio/wav',size:100}];
  const sending=h.node('aiAudioForm').listeners.submit[0]({preventDefault(){}});
  const request=h.requests.find(r=>r.url.includes('/conversation-preview/audio?'));
  assert.ok(request);request.respond({answer:'Audio response to evaluate',transcript:'PRIVATE TRANSCRIPT',step:'clarify_time'});await sending;
  const messages=h.node('aiMessages').children;assert.equal(messages.length,3);
  assert.equal(group(messages[0]),undefined);assert.equal(group(messages[1]),undefined);
  click(buttons(messages[2])[3]);const before=h.requests.length,report=await exportReport(h);
  assert.equal(report.evaluations[0].answer,'Audio response to evaluate');assert.equal(report.scope.style,'warm');
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE TRANSCRIPT|PRIVATE-AUDIO/);noEvaluationWrites(h,before);return;
 }
 if(scenario==='download-failure'){
  const bubble=await reply(h);click(buttons(bubble)[0]);const before=h.requests.length;
  failDownload=true;click(h.node('aiExportEvaluation'));
  assert.equal(h.node('aiExportEvaluation').disabled,false);assert.match(h.node('aiEvaluationStatus').textContent,/marcações continuam/);
  failDownload=false;const report=await exportReport(h);assert.deepEqual(report.evaluations[0].labels,['dry']);
  noEvaluationWrites(h,before);return;
 }
 if(scenario==='scope-drift'){
  const bubble=await reply(h),button=buttons(bubble)[0];click(button);
  h.node('aiStyle').value='direct'; // Guard even a selector change before its event runs.
  click(button);click(h.node('aiExportEvaluation'));assert.equal(downloads.length,0);
  h.node('aiStyle').value='';h.node('aiProfile').value='draft-3';
  click(h.node('aiExportEvaluation'));assert.equal(downloads.length,0);
  h.node('aiProfile').value='';const report=await exportReport(h);
  assert.equal(report.scope.style,'warm');assert.equal(report.scope.profile_version_id,'published-2');return;
 }
 throw Error('Unknown test scenario '+scenario);
})().catch(error=>{console.error(error);process.exitCode=1});
"""


@pytest.mark.skipif(NODE is None, reason="Node required for the real panel DOM/controller; main CI runs these cases")
@pytest.mark.parametrize("scenario", [
    "compile", "ratings", "export", "audio", "download-failure", "scope-drift",
    "clear-conversation", "clear-tenant", "clear-version", "clear-style", "clear-logout",
])
def test_panel_simulator_feedback_controller(scenario):
    result = subprocess.run(
        [NODE, "-e", DOM_RUNNER, scenario], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
