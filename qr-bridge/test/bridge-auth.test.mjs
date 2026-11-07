import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {timingSafeEqual} from "node:crypto";

const server = fs.readFileSync(new URL("../src/server.mjs", import.meta.url), "utf8");
const declaration = server.match(/^function auth\(req\)\{[\s\S]*?^\}/m);
assert.ok(declaration, "The production bridge must define its credential guard");
function authorize(secret, header) {
  const auth = vm.runInNewContext(declaration[0] + ";auth", {KEY:secret, Buffer, timingSafeEqual});
  return auth({headers: header === undefined ? {} : {"x-orion-bridge-key":header}});
}

const key = "orion-synthetic-bridge-credential-" + "A".repeat(48);
test("production bridge rejects empty or weak configured keys, including empty headers", () => {
  for (const missing of [undefined, null, "", " ", "short", "A".repeat(31)]) {
    for (const header of [undefined, "", " ", key]) {
      assert.equal(authorize(missing,header), false);
    }
  }
});
test("production bridge uses exact constant-time comparison for configured key", () => {
  assert.equal(authorize(key,key), true);
  assert.equal(authorize(key,key.slice(0,-1) + "B"), false);
  assert.equal(authorize(key,key + "x"), false);
  assert.equal(authorize(key,undefined), false);
  assert.equal(authorize("A".repeat(257),"A".repeat(257)), false);
});
test("recover route uses imported node:path helper instead of request path string", () => {
  assert.match(server, /directory:joinPath\(authDir,"retry-encrypted"\),secret:KEY/);
});
