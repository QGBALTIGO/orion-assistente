"""Exercise the shipped Delivery connection UI using a synthetic provider.

No real account, message, QR secret, or pairing request leaves this fixture.
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]
STATIC=ROOT/"app/static"
ART=ROOT/"artifacts/delivery-whatsapp"
ART.mkdir(parents=True,exist_ok=True)
CONTRACT=json.loads((ROOT/"tests/fixtures/subscription_devices_contract.json").read_text())
PNG="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/cL8AAAAASUVORK5CYII="

def run(browser,width,method,theme):
    page=browser.new_page(viewport={"width":width,"height":844},is_mobile=width<768,has_touch=width<768)
    page.set_content('<!doctype html><html lang="pt-BR" data-theme="'+theme+'"><body><main class="os-main" id="page-suite" data-os-current-route="whatsapp-delivery"><div id="osPageBody"><section id="osSubscriptionDevices"></section></div></main></body></html>')
    page.add_style_tag(path=STATIC/"orion-suite.css")
    page.add_style_tag(path=STATIC/"orion-subscription-devices.css")
    page.add_style_tag(content="body{margin:0;padding:12px;box-sizing:border-box} #page-suite{max-width:100%;padding:0}")
    for name in ("orion-fields.js","orion-connect.js","orion-subscription-devices.js"):
        page.add_script_tag(path=STATIC/name)
    page.evaluate("""({contract,png})=>{
      const item=structuredClone(contract.list_response.items[0]),id=item.id,base='/api/v1/tenants/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/subscription-whatsapp/devices';
      let created=false,reads=0,qrReads=0,linked=false,attempted=false,pairing=false;
      window.qa={writes:[],connect:()=>{linked=true}};
      const ready=()=>({...item,status:'qr_ready',capabilities:{...item.capabilities,connect:false,qr:true,pair:true}});
      const state=()=>linked?{...ready(),status:'connected',phone_verified:true,phone:'5567999999901'}:pairing?{...ready(),status:'pairing'}:attempted?ready():item;
      window.OrionSubscriptionDevices.mount({host:document.querySelector('#osSubscriptionDevices'),connectionOnly:true,tenantId:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        async request(path,method='GET',payload){
          if(method==='POST')window.qa.writes.push({path,action:payload.action});
          if(path===base&&method==='GET')return {...contract.list_response,items:created?[state()]:[],usage:{used:Number(created),limit:1},can_create:!created};
          if(path===base&&method==='POST'){created=true;return {device:item};}
          if(path===base+'/'+id)return {device:state()};
          if(path.endsWith('/actions')&&payload.action==='connect'){attempted=true;return {...contract.operation_response,operation:{...contract.operation_response.operation,device_id:id,action:'connect',status:'succeeded'},device:ready()};}
          if(path.endsWith('/qr')){qrReads++;return {device_id:id,revision:item.revision,image:qrReads===1?null:png,expires_in_ms:qrReads===1?0:45000};}
          if(path.endsWith('/pair')&&payload.phone==='5567999999901'){pairing=true;return contract.pair_response;}
          throw Error('Unexpected request');
        }});
    }""",{"contract":CONTRACT,"png":PNG})
    connect=page.get_by_role("button",name="Conectar WhatsApp",exact=True)
    expect(connect).to_be_visible()
    assert page.evaluate("window.qa.writes.length")==0
    if method=="code":page.get_by_role("button",name="Código pelo número",exact=True).click()
    connect.click()
    expect(page.locator("dialog")).to_have_count(0)
    if method=="qr":
        expect(page.locator("[data-sd-qr] img")).to_be_visible(timeout=12000)
    else:
        page.get_by_label("Número do WhatsApp",exact=True).fill("(67) 99999-9901")
        page.get_by_role("button",name="Gerar código",exact=True).click()
        expect(page.locator("[data-sd-code]")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert page.evaluate("Array.from(document.querySelectorAll('.os-sd-simple-methods button')).every(b=>b.scrollWidth<=b.clientWidth+1)")
    page.screenshot(path=ART/f"{browser.browser_type.name}-{width}-{method}-{theme}.png")
    page.evaluate("window.qa.connect()")
    expect(page.get_by_role("heading",name="WhatsApp conectado",exact=True)).to_be_visible(timeout=12000)
    expect(page.locator("[data-sd-qr],[data-sd-code]")).to_have_count(0)
    assert page.evaluate("window.qa.writes.length")== (2 if method=="qr" else 3)
    assert page.evaluate("window.qa.writes.every(w=>!w.action||w.action==='connect')")
    page.close()

with sync_playwright() as p:
    for engine in (p.chromium,p.webkit):
        browser=engine.launch(headless=True)
        for width,method,theme in ((320,"qr","light"),(390,"code","dark"),(1440,"qr","light")):
            run(browser,width,method,theme)
        browser.close()
        print("PASS",engine.name,flush=True)
