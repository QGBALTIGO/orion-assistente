"""Real CSS layout regression for financial management, using synthetic data only.

Tests iPhone WebKit + Android/desktop Chromium. No production access required.
"""
from pathlib import Path
import os
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
HTML = """<!doctype html><html lang="pt-BR" data-theme="dark">
<head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><style>*{box-sizing:border-box}html,body{margin:0;padding:0}.hidden{display:none!important}</style></head>
<body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside>
<main class="content"><section id="page-original" class="page">Original</section></main>
</div></body></html>"""

MOCK = """() => {
  const tenant='10000000-0000-4000-8000-000000000001';
  const person='57000000-0000-4000-8000-000000000001';
  const ledger=[
    {id:'57000000-0000-4000-8000-000000000002',kind:'income',
      description:'Atendimento AG-QQS05XW',amount_cents:100000,category:'Serviços',
      payment_method:'pix',status:'paid',occurred_at:'2026-10-26T11:00:00-04:00',version:2},
    {id:'57000000-0000-4000-8000-000000000003',kind:'income',
      description:'Atendimento AG-2H5W3P',amount_cents:10000,category:'Serviços',
      payment_method:'pix',status:'paid',occurred_at:'2026-10-26T12:00:00-04:00',version:3},
    {id:'57000000-0000-4000-8000-000000000004',kind:'expense',
      description:'Produtos e materiais',amount_cents:10000,category:'Geral',
      payment_method:'pix',status:'paid',occurred_at:'2026-10-21T12:00:00-04:00',version:1}
  ];
  const commission={id:person,name:'Profissional com sobrenomes longos',commission_mode:'percent',
     commission_percent:10,completed:2,revenue_cents:110000,earned_cents:11000,
     advances_cents:1000,payable_cents:10000};
  const request=async (path,method='GET',data)=>{
    if(path.includes('/management/finance/summary'))return {
      summary:{income_cents:110000,expense_cents:10000,balance_cents:100000,count:3,
        average_ticket_cents:4125,by_category:[{category:'Serviços',income_cents:110000,expense_cents:0},
        {category:'Geral',expense_cents:10000,income_cents:0}]},
      daily:[{date:'2026-10-26',income_cents:110000,expense_cents:0},
        {date:'2026-10-21',income_cents:0,expense_cents:10000}]
    };
    if(path.includes('/management/finance/dre'))return {summary:{
      service_revenue_cents:110000,other_income_cents:0,gross_revenue_cents:110000,
      commissions_cents:11000,operating_expenses_cents:10000,net_result_cents:89000}};
    if(path.includes('/management/finance/commissions'))return {items:[commission]};
    if(path.includes('/management/finance/advances'))return {items:[{
      id:'57000000-0000-4000-8000-000000000005',professional_id:person,
      professional_name:commission.name,description:'Vale semanal',
      amount_cents:1000,allocated_cents:500,pending_cents:500,settlement_status:'partial',
      occurred_at:'2026-10-25T11:00:00-04:00'}],total:1};
    if(path.includes('/management/finance/settlements'))return {items:[{
      id:'57000000-0000-4000-8000-000000000006',professional_name:commission.name,
      allocated_cents:500,payment_cents:700,settled_at:'2026-10-25T10:00:00-04:00'}],total:1};
    if(path.includes('/management/finance?')){
      const params=new URL(path,location.href).searchParams;
      const entries=ledger.filter(e=>!params.get('kind')||params.get('kind')===e.kind);
      return {items:entries,total:entries.length};
    }
    if(path.endsWith('/professionals'))return [{id:person,name:commission.name,active:true}];
    return [];
  };
  window.OrionSuite.mount({
    user:{role:'member',name:'Equipe sintética',email:'synthetic@example.invalid'},
    tenants:[{id:tenant,name:'Empresa sintética',slug:'synthetic',timezone:'America/Campo_Grande',role:'owner'}],
    tenantId:tenant,initialRoute:'financeiro',request,openLegacy(){},onRouteChange(){}
  });
}"""

@pytest.mark.parametrize("browser_name,width,height,theme", [
  ("webkit", 320, 693, "dark"),
  ("webkit", 375, 812, "dark"),
  ("webkit", 430, 932, "light"),
  ("chromium", 360, 800, "dark"),
  ("chromium", 768, 1024, "light"),
  ("chromium", 1024, 768, "dark"),
  ("chromium", 1366, 768, "light"),
])
def test_finance_loses_no_horizontal_content_or_actions(browser_name,width,height,theme):
    with sync_playwright() as playwright:
        browser=getattr(playwright,browser_name).launch(
            headless=True,
            **({"channel":"chrome"} if browser_name=="chromium"
               and os.getenv("ORION_TEST_SYSTEM_CHROME")=="1" else {})
        )
        page=browser.new_page(viewport={"width":width,"height":height},device_scale_factor=2)
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        try:
            page.route("https://orion.test/panel",lambda route:route.fulfill(
                status=200,content_type="text/html; charset=utf-8",
                body=HTML.replace('data-theme="dark"',f'data-theme="{theme}"')))
            page.goto("https://orion.test/panel")
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=JS)
            assert page.evaluate("window.isSecureContext && typeof crypto.randomUUID === 'function'")
            page.evaluate(MOCK)
            page.locator(".os-finance-ledger-table tbody tr").first.wait_for()
            assert page.locator(".os-finance-metrics>.os-metric").count()==6
            assert page.locator(".os-finance-tabs>.os-pill-tab").count()==5
            assert page.locator(".os-finance-ledger-table tbody tr").count()==3

            def check_layout():
                geometry=page.evaluate("""() => {
                  const main=document.querySelector('.os-main[data-os-current-route=financeiro]');
                  const rect=main.getBoundingClientRect(),viewport=innerWidth;
                  const selected=['.os-header','.os-finance-heading','.os-finance-toolbar',
                    '.os-finance-metrics','.os-finance-tabs','.os-finance-layout',
                    '.os-finance-layout>.os-card','.os-series-scroll',
                    '.os-finance-table-scroll'];
                  // Native desktop/tablet tables scroll inside their own containers;
                  // only mobile cards must individually fit the viewport.
                  if(innerWidth<=767)selected.push('.os-finance-data-table tbody tr');
                  const boxes=selected.flatMap(s=>[...document.querySelectorAll(s)].map(x=>{
                    const box=x.getBoundingClientRect();
                    return {selector:s,left:box.left,right:box.right,width:box.width,scroll:x.scrollWidth,
                      client:x.clientWidth,display:getComputedStyle(x).display};
                  }));
                  const offenders=[...document.querySelectorAll('body *')].map(node=>{
                    const rect=node.getBoundingClientRect();
                    return {tag:node.tagName,cls:String(node.className||'').slice(0,70),
                      left:Math.round(rect.left),right:Math.round(rect.right),
                      innerScroll:!!node.closest('.os-finance-table-scroll,.os-series-scroll,.os-horizontal-tabs')};
                  }).filter(x=>x.right>viewport+2&&!x.innerScroll)
                    .sort((a,b)=>b.right-a.right).slice(0,12);
                  return {viewport,doc:document.documentElement.scrollWidth,
                    body:document.body.scrollWidth,main:{left:rect.left,right:rect.right,width:rect.width},
                    boxes,offenders};
                }""")
                assert geometry["doc"]<=width+2,geometry
                assert geometry["body"]<=width+2,geometry
                assert geometry["main"]["left"]>=-2,geometry
                assert geometry["main"]["right"]<=width+2,geometry
                if width<=767:
                    # The user's screenshot had ~25% of the viewport unused to the right.
                    assert geometry["main"]["width"]>=width-3,geometry
                for box in geometry["boxes"]:
                    assert box["left"]>=-2 and box["right"]<=width+2,geometry
                    assert box["width"]>0,geometry
                return geometry

            geometry=check_layout()
            if width<=767:
                tabs=page.locator(".os-finance-tabs>.os-pill-tab")
                for i in range(5):
                    assert tabs.nth(i).is_visible()
                ledger=page.locator(".os-finance-ledger-table tbody tr").first
                assert ledger.evaluate("(row)=>getComputedStyle(row).display === 'grid'")
                assert ledger.locator(".os-finance-value").is_visible()
                assert ledger.locator("[data-os-finance]").is_visible()
                assert geometry["boxes"][0]["width"]>=width-3
            else:
                assert page.locator(".os-finance-ledger-table").evaluate(
                    "(table)=>getComputedStyle(table).display==='table'")

            # Filters must remain open after an API refresh.
            page.locator('[data-os-action=finance-filters]').click()
            page.locator('#osFinanceKind').select_option('expense')
            assert page.locator('#osFinanceKind').input_value()=='expense'
            assert not page.locator('#osFinanceFilters').evaluate('(node)=>node.classList.contains("hidden")')
            assert page.locator('.os-finance-ledger-table tbody tr').count()==1
            check_layout()
            page.locator('[data-os-action=finance-clear-filters]').click()
            assert page.locator('.os-finance-ledger-table tbody tr').count()==3

            for value,selector in [
              ("dre",'.os-dre-report'),("commissions",'.os-finance-commissions-table'),
              ("advances",'.os-finance-advances-table'),
              ("history",'.os-finance-ledger-table')]:
                page.locator(f'[data-os-finance-tab="{value}"]').click()
                page.locator(selector).wait_for()
                check_layout()

            assert not errors,errors
        finally:
            browser.close()
