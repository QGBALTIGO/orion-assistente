import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const panel=fs.readFileSync(new URL("../../app/static/panel.html",import.meta.url),"utf8");
const sections=[...panel.matchAll(/<script>([\s\S]*?)<\/script>/g)];

test("painel tem script válido em JavaScript",()=>{
  assert.equal(sections.length,2,"bootstrap de tema e controlador do painel devem existir");
  for(const [index,section] of sections.entries())
    assert.doesNotThrow(()=>new vm.Script(section[1],{filename:"panel-inline-"+index+".js"}));
});

test("QR indica atraso de 25 segundos, expiração e falha",()=>{
  for(const state of ["qr_delayed","qr_expired","pair_failed","logged_out"]){
    assert.ok(panel.includes(state),state+" missing from UI");
  }
  assert.match(panel,/Atualizar ou tentar novamente/);
  assert.match(panel,/Refazer vínculo/);
  assert.match(panel,/25 segundos/);
});

test("código não é mantido visível depois que expira",()=>{
  assert.match(panel,/pairExpiresAt/);
  assert.match(panel,/pairExpiresAt>0&&Date\.now\(\)>pairExpiresAt/);
  assert.match(panel,/Código expirado/);
});

test("a página requer cookie autenticado e não expõe tokens",()=>{
  assert.doesNotMatch(panel,/SUPPORT_API_TOKEN/);
  assert.doesNotMatch(panel,/QR_BRIDGE_TOKEN/);
  assert.match(panel,/\/auth\/login/);
  assert.match(panel,/X-Orion-CSRF/);
});
