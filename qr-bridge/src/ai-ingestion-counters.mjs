/** Aggregate-only ingestion health. Never store sender IDs or message contents. */
export function makeIngestionCounters(clock=()=>Date.now()){
 const values={
   upserts:0,notify:0,append:0,ignoredTypeOrAge:0,
   noMessageOrId:0,ownMessages:0,unresolvedIdentity:0,
   unresolvedLid:0,processedText:0,unreadableMedia:0,duplicates:0,
   pilotAccepted:0,notAllowed:0,aiForwardAttempted:0,
   aiForwardFailed:0,aiSilent:0,aiReplySent:0,aiSendFailed:0,
 };
 let lastUpsertAt=null,lastPilotMessageAt=null,lastAiAttemptAt=null;
 function tick(name){
   if(!(name in values))throw Error("Unknown aggregate");
   values[name]++;
   if(name==="upserts")lastUpsertAt=clock();
   if(name==="pilotAccepted")lastPilotMessageAt=clock();
   if(name==="aiForwardAttempted")lastAiAttemptAt=clock();
 }
 function snapshot(){return {counters:{...values},lastUpsertAt,lastPilotMessageAt,lastAiAttemptAt}}
 return {tick,snapshot};
}
