"""Current Orion styles and controls with independent same-time appointments."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import sync_playwright, expect

from test_appointment_operations_responsive import Responses, boot, booking, FUTURE_DAY, PRO, SERVICE, EXTRA

ARTIFACTS=Path(__file__).resolve().parents[2]/'test-artifacts/exceptional-overlap'


@pytest.mark.parametrize('browser_name,width,height,theme',[
    ('webkit',320,693,'dark'),('webkit',375,812,'dark'),('webkit',430,932,'light'),
    ('chromium',360,800,'light'),('chromium',768,1024,'dark'),
    ('chromium',1024,768,'light'),('chromium',1366,900,'dark')])
def test_shared_appointments(browser_name,width,height,theme,monkeypatch):
    original=Responses.result
    today=datetime.now(ZoneInfo('America/Cuiaba')).date().isoformat()
    first=booking()
    first.update(customer_name='Cliente Corte',start_at=today+'T10:00:00-04:00',end_at=today+'T10:40:00-04:00')
    second=deepcopy(first)
    second.update(id='91000000-0000-4000-8000-000000000009',customer_name='Cliente Barba',
        end_at=today+'T10:20:00-04:00',booking_mode='exceptional',allow_overlap=True,total_cents=2000,
        items=[{'service_id':EXTRA,'name_snapshot':'Barba','duration_snapshot_min':20,
                'price_snapshot_cents':2000,'price_mode_snapshot':'fixed','item_origin':'booked'}])
    def responses(self,path,method,data):
        if method=='GET' and path.endswith('/appointments'):
            return {'items':[first,second],'total':2,'offset':0,'limit':50,'has_more':False}
        return original(self,path,method,data)
    monkeypatch.setattr(Responses,'result',responses)
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=getattr(p,browser_name).launch()
        context=browser.new_context(viewport={'width':width,'height':height},color_scheme=theme,
            locale='pt-BR',timezone_id='America/Cuiaba',has_touch=width<768)
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            result=boot(page,theme)
            page.locator('[data-os-agenda-mode=calendar]').click()
            cards=page.locator('.os-calendar-booking')
            expect(cards).to_have_count(2)
            bounds=cards.evaluate_all('nodes=>nodes.map(el=>{let r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,height:r.height}})')
            assert bounds[0]['right']<=bounds[1]['left']+1,bounds
            assert abs(bounds[0]['top']-bounds[1]['top'])<1,bounds
            assert min(r['height'] for r in bounds)>=44,bounds
            cards.first.scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS/f'{browser_name}-{width}-{theme}-calendar.png'),full_page=False)
            page.locator('[data-os-action=new-appointment]').click()
            form=page.locator('[data-os-form=appointment]')
            expect(form.locator('[name=allow_overlap]')).to_be_hidden()
            form.locator('[name=booking_mode]').select_option('exceptional')
            choice=form.locator('[name=allow_overlap]')
            expect(choice).not_to_be_checked();choice.check()
            form.locator('[name=booking_mode]').select_option('retroactive')
            expect(choice).to_be_hidden();expect(choice).not_to_be_checked()
            form.locator('[name=booking_mode]').select_option('exceptional')
            choice.check()
            form.locator('[name=customer_name]').fill('Segundo cliente')
            form.locator('[name=walk_in]').check()
            form.locator('[name=day]').fill(FUTURE_DAY)
            form.locator('[name=clock]').fill('19:00')
            form.locator(f'[data-os-book-service="{SERVICE}"]').click()
            form.locator('[name=professional_id]').select_option(PRO)
            assert page.locator('#osDialog').evaluate('el=>el.scrollWidth<=el.clientWidth+2')
            choice.scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS/f'{browser_name}-{width}-{theme}-option.png'),full_page=False)
            form.locator('[type=submit]').click()
            expect(page.locator('#osDialog')).not_to_be_visible()
            sent=[r for r in result.writes if r['path'].endswith('/appointments/manual')]
            assert len(sent)==1 and sent[0]['body']['allow_overlap'] is True
            assert sent[0]['body']['mode']=='exceptional'
            page.close();page=context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            result=boot(page,theme,professional=True)
            page.locator('[data-page=agenda]:visible').first.click()
            page.locator('[data-action=new-appointment]').click()
            form=page.locator('#modalForm')
            form.locator('[name=booking_mode]').select_option('exceptional')
            expect(form.locator('[name=allow_overlap]')).not_to_be_checked()
            form.locator('[name=allow_overlap]').check()
            form.locator('[name=customer_name]').fill('Cliente do profissional')
            form.locator('[name=walk_in]').check()
            form.locator('[name=day]').fill(FUTURE_DAY)
            form.locator('[name=clock]').fill('19:00')
            form.locator(f'[name=service_ids][value="{SERVICE}"]').check()
            page.locator('#modalSubmit').click()
            expect(page.locator('#modalReview')).to_contain_text('até dois clientes simultâneos')
            assert not any(r['path'].endswith('/appointments/manual') for r in result.writes)
            assert page.locator('#modal .dialog').evaluate('el=>el.scrollWidth<=el.clientWidth+2')
            page.locator('#modalReview').scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS/f'{browser_name}-{width}-{theme}-professional-review.png'),full_page=False)
            page.locator('#modalSubmit').click()
            expect(page.locator('#modal')).to_be_hidden()
            assert result.writes[-1]['body']['allow_overlap'] is True
            assert 'professional_id' not in result.writes[-1]['body']
            assert not errors,errors
        finally:
            context.close();browser.close()
