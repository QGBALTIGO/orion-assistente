import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('admin pairing phone has one accessible focus ring and keyboard-safe sizing',async()=>{
 const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
 assert.match(html,/#page-dispositivo \.wizard-phone-input:focus-within/);
 assert.match(html,/#page-dispositivo \.wizard-phone-input input:focus-visible/);
 assert.match(html,/outline:0!important;box-shadow:none!important;-webkit-appearance:none!important/);
 assert.match(html,/font-size:16px!important/);
 assert.match(html,/env\(safe-area-inset-bottom\)/);
});
test('revoked WhatsApp session cannot issue stale pairing code',async()=>{
 const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
 assert.match(html,/const needsNewLink=\["logged_out","pair_failed"\]\.includes\(result\.status\)/);
 assert.match(html,/\$\("wizardGenerate"\)\.disabled=needsNewLink\|\|result\.status==="connected"/);
 assert.match(html,/Sessão desvinculada\. Use Refazer vínculo/);
});
test('member and admin connection cards use same Orion responsive density',async()=>{
 const css=await readFile(new URL('../app/static/orion-channels.css',import.meta.url),'utf8');
 const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
 assert.match(css,/\.qc-root \.qc-card\{box-sizing:border-box;min-width:0;border-radius:18px/);
 assert.match(css,/\.qc-root \.qc-field input:focus-visible/);
 assert.match(css,/@media\(max-width:480px\)/);
 assert.match(html,/#page-dispositivo \.wizard-card\{box-sizing:border-box/);
});
