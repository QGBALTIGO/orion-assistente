import test from "node:test";
import assert from "node:assert/strict";
import {inspectLookup,inspectDelivery,attachDeliveryDiagnostics} from "../src/recipient-diagnostics.mjs";
const p="5521997557364",me=p+":4@s.whatsapp.net";

test("lookup validates PN and detects connected own account",()=>{
  assert.deepEqual(inspectLookup(p,[{jid:p+"@s.whatsapp.net",exists:true,
    lid:"100022233@lid"}],me),{
    registered:true,identity:"pn_confirmed",hasLid:true,self:true
  });
});
test("no LID cannot be invented from a PN",()=>{
  const a=inspectLookup(p,[{jid:p+"@s.whatsapp.net",exists:true}],null);
  assert.equal(a.registered,true);
  assert.equal(a.hasLid,false);
  assert.equal(a.self,false);
});
test("mismatched or unknown JID is unverified; do not use another recipient",()=>{
  const a=inspectLookup(p,[{jid:"5521888888888@s.whatsapp.net",exists:true}],null);
  assert.equal(a.registered,false);
  assert.equal(a.identity,"unverified");
  assert.equal(inspectLookup(p,null,null).identity,"unknown");
});
test("contact not registered is explicitly distinguishable",()=>{
  assert.equal(inspectLookup(p,[{jid:p+"@s.whatsapp.net",exists:false}],null).registered,false);
});
test("validates E.164 digits and rejects malicious input",()=>{
  for(const input of ["", "1", "55+21", "../../auth","5521997557364@lid", "0".repeat(13)]){
    assert.throws(()=>inspectLookup(input,[],null));
  }
});
test("delivery counts are per-contact and pending never means delivered",()=>{
  const rows=[
    {phone:p,direction:"out",delivery:"pending"},
    {phone:p,direction:"out",delivery:"server_accepted"},
    {phone:p,direction:"out",delivery:"delivered"},
    {phone:p,direction:"out",delivery:"read"},
    {phone:p,direction:"in",delivery:"pending"},
    {phone:"5521999999999",direction:"out",delivery:"read"}
  ];
  assert.deepEqual(inspectDelivery(rows,p),{
    count:4,pending:1,serverAccepted:1,delivered:1,read:1,failed:0,retryRequests:0,messagesWithRetry:0,lastReceipt:"read"
  });
});
test("no receipt available is not proof of failed decryption",()=>{
  assert.deepEqual(inspectDelivery([],p),{
    count:0,pending:0,serverAccepted:0,delivered:0,read:0,failed:0,retryRequests:0,messagesWithRetry:0,lastReceipt:"none"
  });
});

test("Baileys may return an object for a valid contact, not only boolean true",()=>{
 const r=inspectLookup(p,[{jid:p+"@s.whatsapp.net",
   exists:{type:"in"},lid:"100022233@lid"}],null);
 assert.equal(r.registered,true);
});
test("identity mismatch cannot be mistaken for a valid destination",()=>{
 const r=inspectLookup(p,[{jid:"5521888888888@lid",exists:true}],null);
 assert.equal(r.identity,"unverified");
 assert.equal(r.registered,false);
});

test("retry requests are counted by sent message ID for this recipient only",()=>{
 const rows=[{id:"out:A1",phone:p,direction:"out",delivery:"pending"},
   {id:"out:B2",phone:p,direction:"out",delivery:"delivered"},
   {id:"out:C3",phone:"5521888888888",direction:"out",delivery:"pending"}];
 const retries=new Map([["A1",2],["B2",1],["C3",10]]);
 const result=inspectDelivery(rows,p,retries);
 assert.equal(result.retryRequests,3);
 assert.equal(result.messagesWithRetry,2);
 assert.equal(result.count,2);
});


test("first lookup, cached lookup, and failed lookup all report live retry counts",()=>{
  const rows=[{id:"out:MSG1",phone:p,direction:"out",delivery:"pending"},
    {id:"out:MSG2",phone:p,direction:"out",delivery:"delivered"},
    {id:"out:OTHER",phone:"5521888888888",direction:"out",delivery:"pending"}];
  const retries=new Map([["MSG1",2],["MSG2",1],["OTHER",9]]);
  const summary={registered:true,identity:"pn_confirmed",lookupMs:25};
  const first=attachDeliveryDiagnostics(summary,rows,p,retries);
  const cached=attachDeliveryDiagnostics(summary,rows,p,retries);
  const failed=attachDeliveryDiagnostics({identity:"lookup_failed"},rows,p,retries);
  for(const report of [first,cached,failed]){
    assert.equal(report.delivery.retryRequests,3);
    assert.equal(report.delivery.messagesWithRetry,2);
    assert.equal(report.delivery.count,2);
  }
  assert.deepEqual(first,cached);
  assert.equal(summary.delivery,undefined);
  retries.set("MSG1",4);
  assert.equal(attachDeliveryDiagnostics(summary,rows,p,retries).delivery.retryRequests,5);
});
