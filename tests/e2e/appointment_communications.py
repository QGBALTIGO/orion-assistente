"""UI-only synthetic contract: both engines, six viewports, light/dark.

Network fixtures below are deliberate UI fixtures. PostgreSQL behavior lives in
integration tests; these screenshots are never evidence of real WhatsApp delivery.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

SIZES=[(320,568),(375,667),(390,844),(768,1024),(1024,768),(1440,900)]
OUT=Path(os.getenv('APPOINTMENT_E2E_OUT','test-results/appointment-communications'))
BASE='http://127.0.0.1:8876'
TOKEN='00000000-0000-4000-8000-000000000001.'+'a'*43
FIXTURE={'company':'Barbearia de teste','customer':'Cliente Sintético','professional':'Profissional de teste',
 'status':'confirmed','protocol':'AG-TEST22','start_at':'2026-11-15T18:00:00Z','end_at':'2026-11-15T18:40:00Z',
 'timezone':'America/Campo_Grande','updated_at':'2026-10-29T18:00:00Z','services':['Corte','Barba'],
 'duration_min':40,'can_change':True,'can_review':False,'review':None,'has_future_booking':False,
 'new_booking_url':'https://orionagenda.pro/s/teste','contact_phone':'+5567999999922',
 'address':'Endereço sintético com informações longas para validar telas pequenas'}


def main():
 OUT.mkdir(parents=True,exist_ok=True)
 log=(OUT/'server.log').open('w')
 env={**os.environ,'APPOINTMENT_COMMUNICATIONS_ENABLED':'1','ORION_EMAIL_DELIVERY_PROBE':'0','GROQ_PROBE_ON_START':'0'}
 process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8876'],env=env,stdout=log,stderr=log)
 results=[]
 try:
  for _ in range(100):
   try:
    if httpx.get(BASE+'/a',timeout=1).status_code==200:break
   except httpx.HTTPError:pass
   time.sleep(.1)
  else:raise RuntimeError('UI server did not start')
  with sync_playwright() as p:
   for engine in ('chromium','webkit'):
    browser=getattr(p,engine).launch()
    for width,height in SIZES:
     for theme in ('light','dark'):
      context=browser.new_context(viewport={'width':width,'height':height},color_scheme=theme)
      page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
      state=dict(FIXTURE);calls=[]
      def respond(route):
       data=route.request.post_data_json;calls.append(data['action'])
       result=state.copy()
       if data['action']=='slots':result={'slots':['2026-11-16T18:00:00Z'],'timezone':state['timezone']}
       elif data['action']=='cancel':state.update(status='cancelled',can_change=False);result=state.copy()
       elif data['action']=='review':state.update(can_review=False,review={'rating':data['rating'],'comment':data.get('comment')});result=state.copy()
       elif data['action']=='reschedule':state.update(start_at=data['start_at']);result=state.copy()
       elif data['action']=='accept_change':state.update(start_at=state['pending_change']['new_start_at'],pending_change=None);result=state.copy()
       elif data['action']=='decline_change':state.update(pending_change=None);result=state.copy()
       route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
      page.route('**/api/appointment-access',respond)
      page.goto(BASE+'/a#'+TOKEN);page.get_by_role('heading',name='Agendamento confirmado!',exact=True).wait_for()
      assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(engine,width,theme,'overflow')
      page.screenshot(path=str(OUT/f'{engine}-{width}-{theme}-confirmed.png'),full_page=True)
      page.get_by_role('button',name='Remarcar atendimento',exact=True).click()
      page.locator('#day').fill('2026-11-16');page.get_by_role('button',name='Consultar horários').click()
      page.locator('#slot').select_option('2026-11-16T18:00:00Z');page.get_by_role('button',name='Confirmar remarcação').click()
      page.get_by_role('heading',name='Agendamento confirmado!',exact=True).wait_for()
      page.get_by_role('button',name='Cancelar agendamento',exact=True).click()
      page.get_by_role('button',name='Voltar',exact=True).click()
      assert calls.count('cancel')==0
      page.get_by_role('button',name='Cancelar agendamento',exact=True).click()
      page.get_by_role('button',name='Confirmar cancelamento',exact=True).click()
      page.get_by_role('heading',name='Agendamento cancelado',exact=True).wait_for()
      assert calls.count('cancel')==1
      state.update(status='confirmed',can_change=True,pending_change={'id':'00000000-0000-4000-8000-000000000002','new_start_at':'2026-11-17T18:00:00Z','expires_at':'2026-11-16T18:00:00Z'})
      page.reload();page.get_by_role('button',name='Manter meu horário',exact=True).click()
      page.get_by_role('button',name='Cancelar agendamento',exact=True).wait_for()
      assert calls.count('decline_change')==1
      state.update(pending_change={'id':'00000000-0000-4000-8000-000000000003','new_start_at':'2026-11-18T18:00:00Z','expires_at':'2026-11-17T18:00:00Z'})
      page.reload();page.get_by_role('button',name='Aceitar novo horário',exact=True).click()
      page.get_by_role('button',name='Cancelar agendamento',exact=True).wait_for()
      assert calls.count('accept_change')==1
      state.update(status='completed',can_change=False,can_review=True,review=None)
      page.reload();page.get_by_role('heading',name='Atendimento concluído!',exact=True).wait_for()
      assert page.get_by_role('button',name='Cancelar agendamento',exact=True).count()==0
      page.get_by_role('radio',name='5 estrelas',exact=True).check()
      page.locator('#comment').fill('<script>alert(1)</script>')
      page.get_by_role('button',name='Enviar avaliação',exact=True).click()
      page.get_by_text('Sua avaliação foi recebida. Obrigado!',exact=True).wait_for()
      assert calls.count('review')==1 and not errors,errors
      state.update(has_future_booking=True,can_review=False)
      page.reload();page.get_by_text('Seu próximo horário já está garantido!',exact=True).wait_for()
      assert page.get_by_role('link',name='Agendar novamente').count()==0
      page.screenshot(path=str(OUT/f'{engine}-{width}-{theme}-completed.png'),full_page=True)
      state.update(status='confirmed',can_change=False,review=None)
      page.reload();page.get_by_text('Seu agendamento está próximo',exact=False).wait_for()
      assert page.get_by_role('button',name='Remarcar atendimento',exact=True).count()==0
      assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
      results.append({'engine':engine,'viewport':[width,height],'theme':theme,'result':'passed','synthetic_ui':True})
      context.close()
    browser.close()
 finally:
  process.terminate();process.wait(timeout=10);log.close()
  (OUT/'results.json').write_text(json.dumps(results,indent=2))
 print(json.dumps({'ui_cases_passed':len(results),'real_whatsapp_test':False}))


if __name__=='__main__':main()
