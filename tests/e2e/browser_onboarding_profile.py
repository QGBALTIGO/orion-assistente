"""Real Chromium/WebKit checks of quick-profile step, with synthetic state only."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from browser_onboarding_rhythm import (
    ARTIFACTS, ASSETS, HTML, INITIALIZE, METRICS
)

# Keep the existing rendered Orion shell and backend-shaped fixture. This
# variant replaces only the old catalogue step with the new survey step.
QUICK_INITIALIZE = INITIALIZE.replace(
    "catalog:'O que você oferece'", "profile:'Perfil da empresa'"
)
SIZES = [(320,693),(360,800),(390,844),(430,932),(768,1024),(1024,768),(1440,900)]


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_business_profile_layout_and_selection(engine,width,height,theme):
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch(headless=True)
        context=browser.new_context(viewport={"width":width,"height":height},
            color_scheme=theme,has_touch=width<=430,
            is_mobile=(engine=="webkit" and width<=430),
            device_scale_factor=2,locale="pt-BR")
        page=context.new_page()
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        try:
            page.route("**/*",lambda route:route.abort())
            page.route("https://orion.test/panel",lambda route:route.fulfill(
                status=200,content_type="text/html; charset=utf-8",
                body=HTML.replace('data-theme="light"',f'data-theme="{theme}"')))
            page.goto("https://orion.test/panel",wait_until="domcontentloaded")
            for name in ("orion-suite.css","orion-fields.css","orion-onboarding.css"):
                page.add_style_tag(content=(ASSETS/name).read_text(encoding="utf-8"))
            for name in ("orion-fields.js","orion-onboarding.js"):
                page.add_script_tag(content=(ASSETS/name).read_text(encoding="utf-8"))
            page.evaluate(QUICK_INITIALIZE)
            page.locator("[data-ob-step=profile]").click()
            page.locator("[data-ob-form=profile]").wait_for(state="visible")
            page.evaluate("document.fonts.ready")
            form=page.locator('[data-ob-form=profile]')
            assert form.locator('select').count()==4
            assert page.locator('#ob-step-title').inner_text()=='Perfil da empresa'
            assert page.locator('[data-ob-action=add-service]').count()==0
            metrics=page.evaluate(METRICS)
            assert metrics["scrollWidth"]<=width+2,metrics
            assert metrics["numberOfActions"]==2,metrics
            assert 16<=metrics["dividerGap"]<=20,metrics
            assert 19<=metrics["afterDivider"]<=21,metrics
            assert abs(metrics["actionsPadding"]-20)<=1,metrics
            assert len(metrics["fields"])==4,metrics
            for field in metrics["fields"]:
                assert 6<=field["labelGap"]<=8.5,(width,field)
                if field["helpGap"] is not None:
                    assert 6<=field["helpGap"]<=8.5,(width,field)
                assert field["controlLeft"]>=-2,(width,field)
                assert field["controlRight"]<=width+2,(width,field)
                if width<=430:assert float(field["font"].removesuffix("px"))>=16
            for gap in metrics["rowGaps"]:
                assert 20<=gap<=24,(width,gap)
            choices={
                "operating_age":"not_started","team_size":"none",
                "monthly_revenue":"50k_100k","monthly_volume":"over_1000",
            }
            for key,value in choices.items():
                element=form.locator(f'[name={key}]')
                assert element.get_attribute('required') is not None
                assert element.locator('option').first.get_attribute('disabled') is not None
                element.select_option(value)
                assert element.input_value()==value
            assert form.evaluate('(form)=>form.checkValidity()')
            assert page.evaluate('window.__qaWrites.length')==0
            assert not errors,errors
            if (width,theme) in {(390,'light'),(1440,'dark')}:
                ARTIFACTS.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(ARTIFACTS/f'profile-{engine}-{width}-{theme}.png'),full_page=True)
        finally:
            context.close()
            browser.close()
