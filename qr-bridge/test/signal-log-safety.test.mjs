import test from "node:test";
import assert from "node:assert/strict";
import {protectSignalSessionLogs} from "../src/signal-log-safety.mjs";

test("legacy libsignal session logs never print private key objects",()=>{
  const emitted=[];
  const fakeConsole={
    info(...args){emitted.push(["info",...args]);},
    warn(...args){emitted.push(["warn",...args]);},
  };
  const originalInfo=fakeConsole.info,originalWarn=fakeConsole.warn;
  const restore=protectSignalSessionLogs(fakeConsole);
  const secret={
    currentRatchet:{ephemeralKeyPair:{privKey:Buffer.from("do-not-log-this-secret")}},
    rootKey:Buffer.from("another-secret")
  };
  fakeConsole.info("Closing session:",secret);
  fakeConsole.info("Opening session:",secret);
  fakeConsole.info("Removing old closed session:",secret);
  fakeConsole.warn("Session already closed",secret);
  assert.equal(emitted.length,4);
  for(const event of emitted){
    assert.equal(event.length,2);
    assert.equal(event[1],"Orion QR: atualização interna de sessão Signal (dados omitidos)");
    assert.equal(event.includes(secret),false);
  }
  fakeConsole.info("Orion QR: dispositivo conectado");
  assert.deepEqual(emitted[4],["info","Orion QR: dispositivo conectado"]);
  restore();
  assert.equal(fakeConsole.info,originalInfo);
  assert.equal(fakeConsole.warn,originalWarn);
});

test("session prefixes without object and unrelated warnings remain unchanged",()=>{
  const lines=[],target={
    info(...args){lines.push(["info",...args]);},
    warn(...args){lines.push(["warn",...args]);}
  };
  const restore=protectSignalSessionLogs(target);
  target.info("Closing session:");
  target.warn("Closing stale open session for new outgoing prekey bundle");
  assert.deepEqual(lines,[
    ["info","Closing session:"],
    ["warn","Closing stale open session for new outgoing prekey bundle"]
  ]);
  restore();
});
