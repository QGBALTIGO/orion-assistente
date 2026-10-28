import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('Google tenant 401 is checked against Orion login before declaring session expired',async()=>{
 const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
 assert.match(js,/const response=await fetch\('\/auth\/me'/);
 assert.match(js,/expired=session\?\.authenticated!==true/);
 assert.match(js,/O acesso a esta empresa não pôde ser validado/);
 assert.match(js,/state\.data\.googleAuthExpired=expired/);
 assert.match(js,/google-reload-context/);
 assert.match(js,/state\.data\.googleTenantInvalid=error\?\.status===401&&!expired/);
});
