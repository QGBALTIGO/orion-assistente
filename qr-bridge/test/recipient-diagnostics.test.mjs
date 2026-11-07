import test from "node:test";
import assert from "node:assert/strict";
import {inspectLookup,inspectDelivery,brazilLegacyPN,verifiedRecipientFromSingleLookup} from "../src/recipient-diagnostics.mjs";
const p="5521997557364",me=p+":4@s.whatsapp.net";

test("lookup validates PN and detects connected own account",()=>{
  assert.deepEqual(inspectLookup(p,[{jid:p+"@s.whatsapp.net",exists:true,
    lid:"100022233@lid"}],me),{
    registered:true,identity:"pn_confirmed",hasLid:true,self:true,lookupKind:"exact"
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
  assert.equal(a.registered,null);
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
 assert.equal(r.registered,null);
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

test("a canonical Brazilian legacy PN returned to the same query has bounded evidence",()=>{
 const nine="5567999999922",eight="556799999922",lid="100022233@lid";
 assert.equal(brazilLegacyPN(nine),eight);
 const rows=[{jid:eight+"@s.whatsapp.net",exists:true,lid}];
 assert.deepEqual(verifiedRecipientFromSingleLookup(nine,rows),{
   verified:true,kind:"provider_normalized",jid:eight+"@s.whatsapp.net",lid});
 assert.deepEqual(inspectLookup(nine,rows,null),{
   registered:true,identity:"phone_normalized",hasLid:true,self:false,lookupKind:"provider_normalized"});
 // A separate positive legacy lookup is NOT an identity binding for the 9-digit PN.
 assert.equal(verifiedRecipientFromSingleLookup(nine,[]).verified,false);
 assert.equal(inspectLookup(nine,[],null).registered,false);
});
test("unknown, unrelated, ambiguous and legacy-negative results cannot authorize routing",()=>{
 const phone="5567999999922",alias="556799999922";
 for(const rows of [
   [],null,[{jid:alias+"@s.whatsapp.net",exists:false}],
   [{jid:"5567888888888@s.whatsapp.net",exists:true}],
   [{jid:alias+"@s.whatsapp.net",exists:true},{jid:phone+"@s.whatsapp.net",exists:true}],
   [{jid:"100022233@lid",exists:true}],
 ])assert.equal(verifiedRecipientFromSingleLookup(phone,rows).verified,false);
 assert.equal(brazilLegacyPN("556788888888"),null);
 assert.equal(brazilLegacyPN("595991234567"),null);
});
