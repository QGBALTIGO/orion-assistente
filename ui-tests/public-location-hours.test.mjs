import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makePublic,catalogue} from './public-harness.mjs';
import {flush} from './harness.mjs';

async function fixture(t,data=catalogue(),path='/s/empresa-sintetica'){
  const h=await makePublic({data,path}); t.after(()=>h.close());await flush();return h;
}
const getHours=h=>[...h.document.querySelectorAll('#hours-list .hours-row')].map(row=>({
  day:row.querySelector('.hours-day')?.textContent,
  hours:row.querySelector('.hours-time')?.textContent
}));

test('horários de funcionamento aparecem exatamente entre Sobre nós e Onde estamos',async t=>{
  const h=await fixture(t);
  const container=h.document.querySelector('.info-grid');
  const cards=[...container.children];
  assert.deepEqual(cards.map(n=>n.id),['about-card','hours-card','address-card']);
  assert.equal(h.document.getElementById('hours-card').hidden,false);
  assert.deepEqual(getHours(h),[
    {day:'Segunda-feira a sexta-feira',hours:'09:00–18:00'},
    {day:'Sábado',hours:'09:00–13:00'},
    {day:'Domingo',hours:'Fechado'}
  ]);
  assert.match(h.document.querySelector('.hours-note').textContent,/disponibilidade pode variar/);
  assert.equal(h.errors.length,0);
});

test('mais de um período é exibido sem anunciar abertura num intervalo fechado',async t=>{
  const data=catalogue();
  data.opening_hours[0].intervals=[
    {open:'08:00',close:'12:00'},{open:'14:00',close:'18:00'}
  ];
  const h=await fixture(t,data);
  const hours=getHours(h);
  assert.equal(hours[0].day,'Segunda-feira');
  assert.equal(hours[0].hours,'08:00–12:00  ·  14:00–18:00');
  assert.ok(hours.some(x=>x.day==='Terça-feira a sexta-feira'));
});

test('horários ausentes ou inválidos não fabricam horários no site',async t=>{
  const data=catalogue();delete data.opening_hours;
  const h=await fixture(t,data);
  assert.equal(h.document.getElementById('hours-card').hidden,true);
  assert.equal(h.document.querySelectorAll('.hours-row').length,0);
});

test('mapa incorporado e botões de rota levam ao mesmo destino no Google Maps',async t=>{
  const h=await fixture(t);
  const iframe=h.document.getElementById('location-map');
  const preview=h.document.getElementById('map-preview');
  assert.equal(preview.hidden,false);
  assert.equal(iframe.getAttribute('loading'),'lazy');
  assert.equal(iframe.getAttribute('referrerpolicy'),'no-referrer');
  assert.match(iframe.getAttribute('title'),/Google Maps/);
  const embedded=new URL(iframe.getAttribute('src'));
  assert.equal(embedded.origin,'https://maps.google.com');
  assert.equal(embedded.searchParams.get('output'),'embed');
  assert.equal(embedded.searchParams.get('z'),'16');
  assert.match(embedded.searchParams.get('q'),/Empresa sintética, Endereço sintético, São Paulo, SP/);
  const route=h.document.getElementById('maps-link');
  const hero=h.document.getElementById('hero-map');
  assert.equal(route.href,hero.href);
  assert.equal(route.getAttribute('target'),'_blank');
  assert.match(route.getAttribute('rel'),/noopener/);
  assert.match(route.textContent,/Traçar rota no Google Maps/);
  const params=new URL(route.href).searchParams;
  assert.equal(params.get('api'),'1');
  assert.equal(params.get('dir_action'),'navigate');
  assert.equal(params.get('travelmode'),'driving');
  assert.equal(params.has('origin'),false,'origem fica sob controle da localização do visitante no Maps');
  assert.equal(params.get('destination'),embedded.searchParams.get('q'));
  assert.equal(h.errors.length,0);
});

test('mapas têm destino estável usando coordenadas do próprio link Google Maps',async t=>{
  const data=catalogue();
  data.site.maps_url='https://www.google.com/maps/place/Sintetico/@-16.7153,-43.8613,17z/data=!3d-16.71458!4d-43.86438';
  const h=await fixture(t,data);
  const params=new URL(h.document.getElementById('maps-link').href).searchParams;
  assert.equal(params.get('destination'),'-16.71458,-43.86438');
  assert.equal(new URL(h.document.getElementById('location-map').src).searchParams.get('q'),'-16.71458,-43.86438');
});

test('URL insegura e endereço ausente ocultam o mapa, sem carregar iframe remoto',async t=>{
  const data=catalogue();
  data.site.address='';data.site.city='';data.site.state='';data.site.postal_code='';
  data.site.maps_url='javascript:alert(1)';
  const h=await fixture(t,data);
  assert.equal(h.document.getElementById('address-card').hidden,true);
  assert.equal(h.document.getElementById('map-preview').hidden,true);
  assert.equal(h.document.getElementById('hero-map').hidden,true);
  assert.equal(h.document.getElementById('maps-link').hidden,true);
  assert.equal(h.document.getElementById('location-map').hasAttribute('src'),false);
  assert.equal(h.document.querySelector('a[href^="javascript:"]'),null);
  assert.equal(h.errors.length,0);
});

test('Maps Embed gerado pelo compartilhamento mantém hospedagem aprovada, sem executar HTML informado',async t=>{
  const data=catalogue();
  data.site.maps_url='https://www.google.com/maps/embed?pb=synthetic-map-parameters';
  data.site.address='Praça Central';
  const h=await fixture(t,data);
  const embedded=new URL(h.document.getElementById('location-map').src);
  assert.equal(embedded.origin,'https://www.google.com');
  assert.equal(embedded.pathname,'/maps/embed');
  assert.equal(embedded.searchParams.get('pb'),'synthetic-map-parameters');
  assert.ok(new URL(h.document.getElementById('maps-link').href).pathname==='/maps/dir/');
});

test('estética segue o tema, mapa tem altura adequada e cartões não se aninham',async()=>{
  const css=await readFile(new URL('../app/static/orion-public.css',import.meta.url),'utf8');
  for(const rule of [
    '.hours-card,.location-card{border-radius:20px}',
    '.hours-list{display:grid;',
    '.hours-row{display:flex;',
    '.location-card{padding:20px;overflow:hidden}',
    '.map-preview{position:relative;height:228px;min-height:220px;',
    '.map-preview>iframe{display:block;width:100%;height:100%;border:0;',
    '.location-card .route-action{box-sizing:border-box;display:flex;',
    '@media(min-width:720px)'
  ])assert.ok(css.includes(rule),'estilo ausente: '+rule);
  const source=await readFile(new URL('../app/public_site_api.py',import.meta.url),'utf8');
  assert.match(source,/frame-src https:\/\/www\.google\.com https:\/\/maps\.google\.com;/);
});
