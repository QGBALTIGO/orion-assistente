"""Real responsive onboarding goals and five-section review in Chromium/WebKit.

Browser state and requests are synthetic; no tenant data is modified.
"""
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from browser_onboarding_rhythm import ARTIFACTS, ASSETS, HTML, METRICS
from browser_onboarding_profile import QUICK_INITIALIZE


GOALS_INITIALIZE=QUICK_INITIALIZE.replace(
    "channels:'Recepção e canais'",
    "goals:'Desafios e objetivos'",
).replace(
    "  const decision={",
    """  state.configuration.business_metrics={
    operating_age:'1_to_3_years',team_size:'3_5',
    monthly_revenue:'50k_100k',monthly_volume:'101_300'
  };
  state.configuration.business_priorities={
    main_challenge:'whatsapp',desired_outcome:'automate_whatsapp',
    respondent_role:'partner'
  };
  const decision={""",
)
SIZES=[(320,693),(360,800),(390,844),(430,932),(768,1024),(820,1180),(1024,768),(1440,900)]


@pytest.mark.parametrize("engine",["chromium","webkit"])
@pytest.mark.parametrize("width,height",SIZES)
@pytest.mark.parametrize("theme",["light","dark"])
def test_onboarding_goals_and_review_are_legible_and_responsive(engine,width,height,theme):
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch(headless=True)
        context=browser.new_context(
            viewport={"width":width,"height":height},device_scale_factor=2,
            is_mobile=engine=="webkit" and width<=430,
            has_touch=width<=430,color_scheme=theme,locale="pt-BR")
        page=context.new_page()
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
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
                else:
                    route.abort()
            page.route("https://orion.test/brand/*",asset)
            page.route("https://orion.test/panel",lambda route:route.fulfill(
                status=200,content_type="text/html; charset=utf-8",
                body=HTML.replace('data-theme="light"',f'data-theme="{theme}"')))
            page.goto("https://orion.test/panel",wait_until="domcontentloaded")
            for name in ("orion-suite.css","orion-fields.css","orion-onboarding.css"):
                page.add_style_tag(content=(ASSETS/name).read_text(encoding="utf-8"))
            for name in ("orion-fields.js","orion-onboarding.js"):
                page.add_script_tag(content=(ASSETS/name).read_text(encoding="utf-8"))
            page.evaluate(GOALS_INITIALIZE)
            page.locator('[data-ob-step=goals]').click()
            form=page.locator('[data-ob-form=goals]')
            form.wait_for(state="visible")
            page.evaluate('document.fonts.ready')
            assert page.locator('#ob-step-title').inner_text()=='Desafios e objetivos'
            assert form.locator('select').count()==3
            for name,value in (
                ("main_challenge","whatsapp"),
                ("desired_outcome","automate_whatsapp"),
                ("respondent_role","partner"),
            ):
                field=form.locator(f'[name={name}]')
                assert field.input_value()==value
                assert field.get_attribute('required') is not None
                assert field.locator('option').first.get_attribute('disabled') is not None
                assert field.locator('option').first.get_attribute('hidden') is not None
                assert len(field.locator('label').all_text_contents())==0
            metrics=page.evaluate(METRICS)
            assert len(metrics['fields'])==3,metrics
            assert metrics['numberOfActions']==2,metrics
            assert metrics['scrollWidth']<=width+2,metrics
            assert 16<=metrics['dividerGap']<=20,metrics
            assert 19<=metrics['afterDivider']<=21,metrics
            assert abs(metrics['actionsPadding']-20)<=1,metrics
            for field in metrics['fields']:
                assert field['control'],(width,field)
                assert 6<=field['labelGap']<=8.5,(width,field)
                assert field['controlLeft']>=-2,(width,field)
                assert field['controlRight']<=width+2,(width,field)
                if width<=430:
                    assert float(field['font'].removesuffix('px'))>=16,field
            for gap in metrics['rowGaps']:
                assert 20<=gap<=24,(width,gap)

            page.locator('[data-ob-step=review]').click()
            page.locator('.ob-review').wait_for(state='visible')
            assert page.locator('.ob-review-section').count()==5
            assert page.locator('.ob-review-edit').count()==5
            assert page.locator('[data-ob-form=complete]').count()==1
            assert page.locator('.ob-review .ob-status').count()==0
            assert page.locator('.ob-review [data-ob-target=goals]').count()==1
            assert page.locator('.ob-review').get_attribute('aria-label')=='Resumo do cadastro'
            text=page.locator('.ob-review').inner_text()
            for snippet in ('Empresa','Atendimento','Perfil da empresa','Horário comercial','Desafios e objetivos',
                            'Responder clientes no WhatsApp','Automatizar o WhatsApp',
                            'Sócio','R$ 50 mil a R$ 100 mil'):
                assert snippet in text,(snippet,text)
            layout=page.evaluate("""() => {
              const viewport=innerWidth;
              const root=document.querySelector('.ob-review');
              const sections=[...root.querySelectorAll('.ob-review-section')];
              const panel=root.getBoundingClientRect();
              const width=panel.width;
              const columns=getComputedStyle(root.querySelector('.ob-review-data')).gridTemplateColumns;
              const elements=[...root.querySelectorAll('.ob-review-section-head h3, .ob-review-edit, .ob-review-pair')];
              return {
                viewport,rootWidth:width,columns,
                scroll:document.documentElement.scrollWidth,
                sections:sections.map(section=>{
                  const r=section.getBoundingClientRect();
                  const title=section.querySelector('h3'),svg=section.querySelector('h3 svg'),
                    button=section.querySelector('.ob-review-edit');
                  const tr=title.getBoundingClientRect(),sr=svg.getBoundingClientRect(),
                    br=button.getBoundingClientRect();
                  return {
                    left:r.left,right:r.right,width:r.width,
                    aligned:Math.abs((tr.top+tr.height/2)-(sr.top+sr.height/2)),
                    editLeft:br.left,editRight:br.right
                  };
                }),
                elements:elements.map(el=>{
                  const r=el.getBoundingClientRect();
                  return {left:r.left,right:r.right,width:r.width};
                })
              };
            }""")
            assert layout['scroll']<=width+2,layout
            assert len(layout['sections'])==5,layout
            assert all(item['left']>=-2 and item['right']<=width+2
                       for item in layout['elements']),layout
            for section in layout['sections']:
                assert section['left']>=-2 and section['right']<=width+2,section
                assert section['width']>0,section
                assert section['aligned']<=5,section
                assert section['editLeft']>=-2 and section['editRight']<=width+2,section
            column_count=len(layout['columns'].split())
            assert column_count==(1 if layout['rootWidth']<=560 else 2),layout
            assert not page.evaluate('window.__qaWrites'),"Browser sent an unexpected write"
            assert not errors,errors
            if (width,theme) in {(390,'light'),(1440,'dark')}:
                page.screenshot(
                    path=str(ARTIFACTS/f'goals-review-{engine}-{width}-{theme}.png'),
                    full_page=True)
        finally:
            context.close()
            browser.close()
