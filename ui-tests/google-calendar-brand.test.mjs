import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('Agenda and Google connection panel use official Google Calendar logo',async()=>{
 const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
 const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
 const official='https://www.gstatic.com/images/branding/productlogos/calendar_2026/v2/web/192px.svg';
 assert.ok(js.split(official).length>=3);
 assert.doesNotMatch(js,/<span class="os-google-mark" aria-hidden="true">G<\/span>/);
 assert.match(js,/<img src="https:\/\/www\.gstatic\.com\/images\/branding\/productlogos\/calendar_2026/);
 assert.match(css,/\.os-google-mark img\{display:block;width:26px;height:26px/);
});

test('Google OAuth errors are shown accessibly instead of raw HTTP 409',async()=>{
 const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
 assert.match(js,/message\.setAttribute\('role','alert'\)/);
 assert.match(js,/Não foi possível iniciar a conexão por conflito de configuração/);
});
