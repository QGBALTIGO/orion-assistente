"""Real headless Chromium responsive smoke, synthetic backend, no network or charges."""
from pathlib import Path
from playwright.async_api import async_playwright
import asyncio

ROOT=Path(__file__).resolve().parents[2]
WIDTHS=(320,375,390,430,768,1024,1440)
COLORS={
    "light":{"bg":"#f5f8fd","card":"#ffffff","text":"#15213b","muted":"#536079","border":"#dbe3ef"},
    "dark":{"bg":"#111827","card":"#19243b","text":"#f5f8fd","muted":"#aabbd1","border":"#35445b"},
}

async def inspect(page,width,mode):
    palette=COLORS[mode]
    await page.set_viewport_size({"width":width,"height":850})
    await page.set_content("""<!doctype html><html lang="pt-BR"><head><meta name="viewport"
        content="width=device-width,initial-scale=1"></head><body style="margin:0">
        <main id="osPageBody" style="padding:18px;box-sizing:border-box;min-width:0"></main>
        </body></html>""")
    await page.add_style_tag(content="""
      :root{font-family:system-ui;--os-primary:#347cff;--os-tint:rgba(52,124,255,.08);
      --os-font:system-ui,sans-serif;--os-display:system-ui,sans-serif;
      --os-danger:#c73636}
      *{box-sizing:border-box}
    """)
    await page.evaluate("""settings=>{
      const root=document.documentElement;
      Object.entries(settings).forEach(([name,value])=>
        root.style.setProperty('--os-'+name,value));
      document.body.style.background=settings.bg;
      document.body.style.color=settings.text;
    }""",palette)
    await page.add_style_tag(path=str(ROOT/"app/static/orion-plans.css"))
    await page.add_script_tag(path=str(ROOT/"app/static/orion-plans.js"))
    await page.evaluate("""()=>{
      window.OrionFields={phoneDisplay:value=>value,
        phoneValue:()=>'+5567999991111'};
      window.OrionPlans.mount(document.querySelector('#osPageBody'),{
        plans:[
          {code:'essencial',name:'Orion Essencial',monthly_cents:2990,max_professionals:2},
          {code:'profissional',name:'Orion Profissional',monthly_cents:8990,max_professionals:6},
          {code:'elite',name:'Orion Elite',monthly_cents:13990,max_professionals:10}
        ],subscription:null,trial:null,active_professionals:0,can_manage_plan:true,
        checkout_enabled:false
      },{
        canManage:true,isAdmin:false,user:{name:'Pessoa Sintética'},
        tenant:{name:'Empresa Sintética',timezone:'America/Campo_Grande'},
        request:async()=>{throw Error('Blocked in synthetic browser test');}
      });
    }""")
    assert await page.locator(".op-plan").count()==3, (width,mode)
    assert await page.locator(".op-matrix-footnote, .op-admin-trials, .op-legal-foot").count()==0, (width,mode)
    assert await page.evaluate("() => document.querySelector('.op-page').lastElementChild.classList.contains('op-compare')"), (width,mode)
    state=await page.evaluate("""()=>{
      return {
       total:document.documentElement.scrollWidth,
       viewport:window.innerWidth,
       cards:[...document.querySelectorAll('.op-plan')].map(e=>{
          const r=e.getBoundingClientRect();return {left:r.left,right:r.right};
       }),
       buttons:[...document.querySelectorAll('.op-plan-bottom>.op-btn')]
          .map(e=>e.getBoundingClientRect().bottom)
      };
    }""")
    assert state["total"]<=state["viewport"]+1,(width,mode,"overflow",state)
    for card in state["cards"]:
        assert card["left"]>=-1 and card["right"]<=width+1,(width,mode,"card",card)
    if width>=1200:
        assert max(state["buttons"])-min(state["buttons"])<2,(width,mode,"footer alignment")
    # Each category is a static heading. No expandable accordion or navigation.
    assert await page.locator('[data-op-category-toggle]').count()==0, (width,mode)
    assert await page.locator('[data-op-only-differences]').is_checked(), (width,mode)
    assert await page.locator('.op-matrix-row:not([hidden])').count()==15, (width,mode)
    assert await page.locator('.op-mobile-feature:not([hidden])').count()==15, (width,mode)
    if width<768:
        assert not await page.locator('.op-matrix-frame').is_visible(), (width,mode)
        assert await page.locator('.op-mobile-comparison').is_visible(), (width,mode)
        geometry=await page.evaluate("""()=>{
            const cards=[...document.querySelectorAll('.op-mobile-feature:not([hidden])')];
            const values=[...document.querySelectorAll('.op-mobile-feature:not([hidden]) .op-mobile-value')];
            const rects=cards.map(el=>el.getBoundingClientRect());
            return {left:Math.min(...rects.map(r=>r.left)),
                    right:Math.max(...rects.map(r=>r.right)),
                    cellWidth:Math.min(...values.map(el=>el.getBoundingClientRect().width)),
                    labelSize:parseFloat(getComputedStyle(cards[0].querySelector('.op-mobile-feature-label')).fontSize)};
        }""")
        assert geometry["left"]>=-1 and geometry["right"]<=width+1,(width,mode,geometry)
        assert geometry["cellWidth"]>=60,(width,mode,geometry)
        assert geometry["labelSize"]>=12,(width,mode,geometry)
        assert await page.locator('.op-mobile-group:visible').count()==9, (width,mode)
    else:
        assert await page.locator('.op-matrix-frame').is_visible(), (width,mode)
        assert not await page.locator('.op-mobile-comparison').is_visible(), (width,mode)
        geometry=await page.evaluate("""()=>{
            const matrix=document.querySelector('.op-matrix');
            const header=[...matrix.querySelectorAll('thead th')];
            const row=matrix.querySelector('.op-matrix-row:not([hidden])');
            const cells=[...row.children];
            return {columns:header.length,bodyColumns:cells.length,
                    headerX:header.map(n=>n.getBoundingClientRect().x),
                    rowX:cells.map(n=>n.getBoundingClientRect().x),
                    right:matrix.getBoundingClientRect().right};
        }""")
        assert geometry["columns"]==geometry["bodyColumns"]==4,(width,mode,geometry)
        assert geometry["right"]<=width+1,(width,mode,geometry)
        assert all(abs(a-b)<2 for a,b in zip(geometry["headerX"],geometry["rowX"])),(width,mode,geometry)
    await page.locator('[data-op-only-differences]').uncheck()
    assert await page.locator('.op-matrix-row:not([hidden])').count()==25, (width,mode)
    assert await page.locator('.op-mobile-feature:not([hidden])').count()==25, (width,mode)
    await page.locator('[data-op-only-differences]').check()
    assert await page.locator('.op-matrix-row:not([hidden])').count()==15, (width,mode)
    assert await page.locator('.op-mobile-feature:not([hidden])').count()==15, (width,mode)
    await page.locator("[data-op-trial]").click()
    await page.locator(".op-dialog").wait_for(state="visible")
    rect=await page.locator(".op-dialog").bounding_box()
    assert rect and rect["x"]>=0 and rect["x"]+rect["width"]<=width+1,(width,mode,"dialog",rect)
    assert await page.locator("#opFullName").input_value()=="Pessoa Sintética"
    assert await page.locator("#opCompanyName").input_value()=="Empresa Sintética"
    assert await page.locator("[name=privacy_ack]").count()==1
    await page.locator(".op-dialog .op-dialog-close").click()
    assert await page.locator(".op-dialog").count()==0,(width,mode,"dialog close")
    await page.locator('[data-op-checkout="profissional"]').click()
    await page.locator(".op-checkout-dialog").wait_for(state="visible")
    checkout=await page.locator(".op-checkout-dialog").bounding_box()
    assert checkout and checkout["x"]>=0 and checkout["x"]+checkout["width"]<=width+1,(width,mode,"checkout dialog",checkout)
    assert await page.locator(".op-payment-summary").count()==1,(width,mode)
    assert not await page.locator(".op-payment-body").is_visible(),(width,mode,"fail closed")
    await page.locator(".op-checkout-dialog .op-dialog-close").click()
    assert await page.locator(".op-checkout-dialog").count()==0,(width,mode,"checkout close")


async def main():
    async with async_playwright() as driver:
        browser=await driver.chromium.launch(headless=True)
        try:
            page=await browser.new_page()
            for mode in COLORS:
                for width in WIDTHS:
                    await inspect(page,width,mode)
            print("orion_plans_browser_7_widths_2_themes_pass")
        finally:
            await browser.close()


if __name__=="__main__":
    asyncio.run(main())
