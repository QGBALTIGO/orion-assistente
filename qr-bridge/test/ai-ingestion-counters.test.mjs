import test from "node:test";
import assert from "node:assert/strict";
import {makeIngestionCounters} from "../src/ai-ingestion-counters.mjs";

test("aggregate metrics store counts only, not recipient or content",()=>{
 const counter=makeIngestionCounters(()=>1760710000000);
 for(const category of ["upserts","notify","unresolvedIdentity","unresolvedLid",
   "processedText","pilotAccepted","aiForwardAttempted","aiSilent"])counter.tick(category);
 const snapshot=counter.snapshot();
 assert.equal(snapshot.counters.upserts,1);
 assert.equal(snapshot.counters.unresolvedLid,1);
 assert.equal(snapshot.counters.pilotAccepted,1);
 assert.equal(snapshot.lastUpsertAt,1760710000000);
 assert.equal(snapshot.lastAiAttemptAt,1760710000000);
 assert.ok(!JSON.stringify(snapshot).includes("5511999999999"));
});
test("unknown counter names rejected",()=>{
 assert.throws(()=>makeIngestionCounters().tick("phone"),/Unknown aggregate/);
});
