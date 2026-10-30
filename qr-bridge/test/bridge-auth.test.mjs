import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {bridgeKeyAuthorized} from "../src/bridge-auth.mjs";

const key = "orion-synthetic-bridge-credential-" + "A".repeat(48);

test("bridge authentication fails closed when the secret is absent, blank or short", () => {
  for (const missing of [undefined, null, "", " ", "short", "A".repeat(31)]) {
    for (const header of [undefined, "", " ", missing, key]) {
      assert.equal(bridgeKeyAuthorized(header, missing), false);
    }
  }
});

test("bridge authentication accepts only the exact nonempty configured key", () => {
  assert.equal(bridgeKeyAuthorized(key, key), true);
  assert.equal(bridgeKeyAuthorized(key.slice(0, -1) + "B", key), false);
  assert.equal(bridgeKeyAuthorized(key + "x", key), false);
  assert.equal(bridgeKeyAuthorized(key.slice(0, -1), key), false);
  assert.equal(bridgeKeyAuthorized(undefined, key), false);
  assert.equal(bridgeKeyAuthorized(["not", "a", "header"], key), false);
  assert.equal(bridgeKeyAuthorized("A".repeat(257), "A".repeat(257)), false);
});

test("recovery uses node:path join rather than the request pathname string", () => {
  const file = fs.readFileSync(path.resolve("src/server.mjs"), "utf8");
  assert.match(file, /directory:joinPath\(authDir,"retry-encrypted"\),secret:KEY/);
  assert.match(file, /return bridgeKeyAuthorized\(req\.headers\["x-orion-bridge-key"\],KEY\)/);
});
