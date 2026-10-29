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
    # Real browser: every feature stays aligned under the same three tier columns.
    geometry=await page.evaluate("""()=>{
      const matrix=document.querySelector('.op-matrix');
      const header=[...matrix.querySelectorAll('thead th')];
      const row=matrix.querySelector('.op-matrix-row');
      const cells=[...row.children];
      return {columns:header.length,bodyColumns:cells.length,
              widths:header.map(n=>n.getBoundingClientRect().width),
              headerX:header.map(n=>n.getBoundingClientRect().x),
              rowX:cells.map(n=>n.getBoundingClientRect().x),
              right:matrix.getBoundingClientRect().right};
    }""")
    assert geometry["columns"]==geometry["bodyColumns"]==4,(width,mode,geometry)
    assert geometry["right"]<=width+1,(width,mode,geometry)
    assert all(abs(a-b)<2 for a,b in zip(geometry["headerX"],geometry["rowX"])),(width,mode,geometry)
    await page.locator('[data-op-category-toggle="0"]').click()
    assert await page.locator('[data-op-category="0"] .op-matrix-row:visible').count()==0
    await page.locator('[data-op-category-toggle="0"]').click()
    assert await page.locator('[data-op-category="0"] .op-matrix-row:visible').count()>0
    await page.locator("[data-op-trial]").click()
    await page.locator(".op-dialog").wait_for(state="visible")
    rect=await page.locator(".op-dialog").bounding_box()
    assert rect and rect["x"]>=0 and rect["x"]+rect["width"]<=width+1,(width,mode,"dialog",rect)
    assert await page.locator("#opFullName").input_value()=="Pessoa Sintética"
    assert await page.locator("#opCompanyName").input_value()=="Empresa Sintética"
    assert await page.locator("[name=privacy_ack]").count()==1
    await page.locator(".op-dialog .op-dialog-close").click()
    assert await page.locator(".op-dialog").count()==0,(width,mode,"dialog close")


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
