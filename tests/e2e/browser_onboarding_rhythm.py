"""Render the real onboarding scripts and CSS in browser engines without a live account.

Synthetic tenant state; no network traffic, database writes or messages.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "app" / "static"
ARTIFACTS = ROOT / "test-artifacts" / "onboarding-rhythm"
SIZES = [(320, 693), (360, 800), (390, 844), (428, 926),
         (768, 1024), (820, 1180), (1024, 768), (1440, 900)]
HTML = """<!doctype html><html lang="pt-BR" data-theme="light"><head>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
</head><body><div id="appView" class="app orion-suite-enabled ob-gated">
<main class="content"><header id="setupHeader"></header>
<section class="page" id="orionSetup"></section></main></div></body></html>"""

INITIALIZE = r"""async () => {
  const tenant='10000000-0000-4000-8000-000000000001',
        professional='30000000-0000-4000-8000-000000000003',
        service='40000000-0000-4000-8000-000000000004';
  const stepNames={
    business:'Seu negócio',location:'Onde você atende',professionals:'Quem atende',
    catalog:'O que você oferece',hours:'Quando você atende',
    channels:'Recepção e canais',review:'Revisar e concluir'
  };
  const state={
    tenant_id:tenant,name:'Empresa de teste',timezone:'America/Campo_Grande',
    version:3,snapshot:'e'.repeat(64),can_edit:true,status:'incomplete',
    current_step:'business',pending:[],
    steps:Object.entries(stepNames).map(([id,label])=>({id,label,required:true,complete:false})),
    configuration:{
      niche:'barbearia',activity:'services',contact_phone:'+5567999999901',
      modality:'onsite',private_address:{
        postal_code:null,street:'Rua Exemplo',number:'123',no_number:false,
        complement:null,neighborhood:'Centro',city:'Campo Grande',state:'MS'
      }
    },
    catalog:{
      professionals:[{id:professional,name:'Profissional de teste',active:false,
        requested_active:true,draft:true,accepts_appointments:true,
        fingerprint:'a'.repeat(64)}],
      services:[{id:service,name:'Corte de teste',description:null,price_cents:3500,
        duration_min:40,prep_min:5,cleanup_min:5,active:false,
        requested_active:true,draft:true,professional_ids:[professional],
        fingerprint:'b'.repeat(64)}],
      products:[],
      hours:[{id:'60000000-0000-4000-8000-000000000006',
        professional_id:professional,weekday:0,start_time:'09:00:00',
        end_time:'18:00:00',breaks:[],fingerprint:'c'.repeat(64)}]
    },
    channels:{
      whatsapp:{status:'disconnected',message:'Conexão pendente',can_connect:false},
      assistant:{status:'draft',generation:2,profile:{
        assistant_name:'Recepção',style_rules:{
          style:'warm',length:'balanced',emoji:'none',formality:'formal'
        },capabilities:['catalog_answers']
      }},
      site:{status:'not_published'},google:{status:'not_connected'}
    }
  };
  const decision={
    enabled:true,kind:'onboarding',create_allowed:false,
    tenant_id:tenant,current_step:'business',businesses:[{id:tenant}],
    account:{name:'Conta de teste',email:'teste@example.invalid'}
  };
  window.__qaWrites=[];
  const request=async (path,method='GET',payload)=>{
    if(method!=='GET'){
      window.__qaWrites.push({path,method,payload});
      throw Error('Writes blocked in isolated browser test');
    }
    if(path==='/api/v1/onboarding/entry')return structuredClone(decision);
    if(path.endsWith('/onboarding'))return structuredClone(state);
    throw Error('Unexpected read '+path);
  };
  window.OrionOnboarding.mount({
    host:document.getElementById('orionSetup'),request,
    user:{name:'Conta de teste',email:'teste@example.invalid',authenticated:true,role:'member'}
  });
  await window.OrionOnboarding.resolveEntry();
}"""

METRICS = r"""() => {
  const visible=el=>el && el.getClientRects().length>0 &&
    getComputedStyle(el).visibility!=='hidden';
  const rect=el=>el.getBoundingClientRect();
  const round=n=>Math.round(n*100)/100;
  const form=document.querySelector('form.ob-form');
  const fieldset=form?.querySelector(':scope > fieldset');
  const actions=fieldset?.querySelector(':scope > .ob-actions');
  const siblings=fieldset?[...fieldset.children].filter(el=>el!==actions&&visible(el)):[];
  const last=siblings.at(-1);
  const inputs=[...document.querySelectorAll('.ob-form .ob-field')].filter(visible);
  const fields=inputs.map(field=>{
    const label=field.querySelector(':scope > label');
    const control=field.querySelector(
      ':scope > input,:scope > select,:scope > textarea,:scope > .os-field-unit');
    const help=field.querySelector(':scope > .ob-help');
    return {
      label:label?.textContent,control:!!control,
      labelGap:label&&control?round(rect(control).top-rect(label).bottom):null,
      helpGap:control&&help?round(rect(help).top-rect(control).bottom):null,
      controlLeft:control?rect(control).left:null,
      controlRight:control?rect(control).right:null,
      font:control?getComputedStyle(control.matches('input,select,textarea')?
        control:control.querySelector('input')).fontSize:null
    };
  });
  const rowGaps=[];
  for(const grid of document.querySelectorAll('.ob-form .ob-fields')){
    if(!visible(grid))continue;
    const children=[...grid.children].filter(visible);
    const tops=[...new Set(children.map(el=>round(rect(el).top)))].sort((a,b)=>a-b);
    for(let index=1;index<tops.length;index++){
      const previous=children.filter(el=>Math.abs(rect(el).top-tops[index-1])<2);
      const current=children.filter(el=>Math.abs(rect(el).top-tops[index])<2);
      if(previous.length&&current.length){
        rowGaps.push(round(Math.min(...current.map(el=>rect(el).top))-
          Math.max(...previous.map(el=>rect(el).bottom))));
      }
    }
  }
  const firstButton=actions?.querySelector('.ob-button');
  const border=parseFloat(actions?getComputedStyle(actions).borderTopWidth:'0');
  return {
    width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
    fields,rowGaps,
    dividerGap:last&&actions?round(rect(actions).top-rect(last).bottom):null,
    actionsPadding:actions?parseFloat(getComputedStyle(actions).paddingTop):null,
    afterDivider:actions&&firstButton?
      round(rect(firstButton).top-rect(actions).top-border):null,
    numberOfActions:actions?.querySelectorAll('button').length??0
  };
}"""


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_actual_setup_field_spacing_in_device_viewports(engine, width, height, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    rows=[]
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch(headless=True)
        context=browser.new_context(
            viewport={"width":width,"height":height},device_scale_factor=2,
            is_mobile=(engine=="webkit" and width<=428),has_touch=width<=428,
            color_scheme=theme,locale="pt-BR")
        page=context.new_page()
        failures=[]
        page.on("pageerror",lambda error:failures.append(str(error)))
        try:
            page.route("**/*",lambda route:route.abort())
            def asset(route):
                name=urlsplit(route.request.url).path.rsplit("/",1)[-1]
                source=ASSETS/name
                if source.is_file() and name in (
                    "figtree-latin.woff2","outfit-latin.woff2",
                    "orion-brand-dark.svg","orion-brand-light.svg","orion-symbol.svg"
                ):
                    route.fulfill(status=200,
                        content_type="font/woff2" if name.endswith(".woff2") else "image/svg+xml",
                        body=source.read_bytes())
                else:route.abort()
            page.route("https://orion.test/brand/*",asset)
            page.route("https://orion.test/panel",lambda route:route.fulfill(
                status=200,content_type="text/html; charset=utf-8",
                body=HTML.replace('data-theme="light"',f'data-theme="{theme}"')))
            page.goto("https://orion.test/panel",wait_until="domcontentloaded")
            for name in ("orion-suite.css","orion-fields.css","orion-onboarding.css"):
                page.add_style_tag(content=(ASSETS/name).read_text(encoding="utf-8"))
            for name in ("orion-fields.js","orion-onboarding.js"):
                page.add_script_tag(content=(ASSETS/name).read_text(encoding="utf-8"))
            page.evaluate(INITIALIZE)
            page.locator("[data-ob-form=business]").wait_for(state="visible")
            page.evaluate("document.fonts.ready")
            for step in ("business","location","professionals","catalog","hours","channels","review"):
                if step!="business":
                    page.locator(f"[data-ob-step={step}]").click()
                if step=="professionals":
                    page.locator("[data-ob-action=add-professional]").click()
                if step=="catalog":
                    page.locator("[data-ob-action=edit][data-ob-kind=service]").click()
                metrics=page.evaluate(METRICS)
                metrics.update(step=step,theme=theme,engine=engine)
                rows.append(metrics)
                assert metrics["numberOfActions"]>=2,metrics
                assert metrics["scrollWidth"]<=width+2,metrics
                assert 16<=metrics["dividerGap"]<=20,metrics
                assert abs(metrics["actionsPadding"]-20)<=1,metrics
                assert 19<=metrics["afterDivider"]<=21,metrics
                if step!="review":assert metrics["fields"],metrics
                for field in metrics["fields"]:
                    assert field["control"],(metrics,field)
                    assert 6<=field["labelGap"]<=8.5,(metrics,field)
                    if field["helpGap"] is not None:
                        assert 6<=field["helpGap"]<=8.5,(metrics,field)
                    assert field["controlLeft"]>=-2,(metrics,field)
                    assert field["controlRight"]<=width+2,(metrics,field)
                for gap in metrics["rowGaps"]:
                    assert 20<=gap<=24,(metrics,gap)
                if width==390 and step=="location" and theme=="light":
                    page.screenshot(path=str(ARTIFACTS/f"{engine}-location-390.png"),full_page=True)
                if width==1440 and step=="business" and theme=="light":
                    page.screenshot(path=str(ARTIFACTS/f"{engine}-business-1440.png"),full_page=True)
            assert not failures,failures
            assert not page.evaluate("window.__qaWrites"),"UI did a real write"
        finally:
            (ARTIFACTS/f"{engine}-{width}x{height}-{theme}.json").write_text(
                json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
            context.close()
            browser.close()
