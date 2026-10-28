"""Read-only, loopback-only public-site fixture. No DB, credentials or outbound IO.

Run: python -m scripts.public_audit_preview
All POSTs fail closed, including appointment confirmation. Assets are read from
the current checkout on every request so each individual fix can be inspected.
"""
import json
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
S = [f'10000000-0000-4000-8000-{n:012d}' for n in range(1, 15)]
P = [f'20000000-0000-4000-8000-{n:012d}' for n in (1, 2)]
MEDIA = '/media/30000000-0000-4000-8000-000000000001/'
DATA = {
    'today': '2030-01-07', 'timezone': 'America/Sao_Paulo', 'booking_horizon_days': 60,
    'site': {'title': 'Barbearia sintética', 'theme_color': '#fdc700',
             'show_prices': True, 'description': 'Ambiente isolado de validação.',
             'address': 'Rua de teste, 100', 'city': 'São Paulo', 'state': 'SP',
             'postal_code': '01000-000', 'whatsapp_phone': '+5511900000000',
             'phone': '+551130000000', 'amenities': {'wifi': True, 'parking': True}},
    'professionals': [dict(id=p, name=n, image_url=MEDIA+p+'.webp')
                      for p,n in zip(P, ['Guilherme sintético', 'Peixoto sintético'])],
    'opening_hours': [dict(weekday=i, intervals=[dict(open='09:00', close='18:00')]
                          if i < 5 else [dict(open='09:00', close='13:00')] if i==5 else [])
                      for i in range(7)],
    'services': [dict(id=s, name=n, duration_min=d, price_cents=c, price_mode=m,
                     catalog_kind='base', category='Serviços', professional_ids=P,
                     image_url=MEDIA+s+'.webp') for s,n,d,c,m in zip(S,
        ['Corte Masculino','Hidratação Capilar','Corte + Barba + Sobrancelha na Navalha',
         'Barba Tradicional','Sobrancelha na navalha']+[f'Serviço sintético {i}' for i in range(6,15)],
        [40,30,90,30,20]+[30]*9,[4000,2000,6000,2500,1500]+[2000]*9,
        ['fixed','from','fixed','fixed','fixed']+['fixed']*9)]
}
DATA['services'][2].update(catalog_kind='combo', category='Combos', featured=True,
    components=[dict(service_id=S[i],name=DATA['services'][i]['name']) for i in [0,3,4]])

class Handler(BaseHTTPRequestHandler):
    def send_body(self, body, mime='application/json', status=200):
        if isinstance(body, (dict,list)): body=json.dumps(body,ensure_ascii=False).encode()
        elif isinstance(body,str): body=body.encode()
        self.send_response(status)
        self.send_header('Content-Type',mime+'; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'none'; form-action 'none'")
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        self.send_body({'detail':'Fixture read-only: all writes and external sends blocked.'},status=403)
    def do_GET(self):
        u=urlsplit(self.path); p=u.path; q=parse_qs(u.query)
        if p.startswith('/s/') or p.startswith('/app/site-preview/'):
            return self.send_body((ROOT/'app/static/orion-public.html').read_bytes(),'text/html')
        if p.startswith('/public-assets/') or p.startswith('/brand/'):
            name=p.rsplit('/',1)[-1]
            candidates=[ROOT/'app/static'/name] if p.startswith('/public-assets/') else list((ROOT/'app').rglob(name))
            if candidates and candidates[0].is_file():
                mime='text/css' if name.endswith('.css') else 'text/javascript' if name.endswith('.js') else 'font/woff2'
                return self.send_body(candidates[0].read_bytes(),mime)
        if p.startswith('/media/'):
            portrait='20000000' in p
            svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="1200"><rect width="960" height="1200" fill="#c4ced9"/><circle cx="480" cy="200" r="140" fill="#353f50"/><path d="M80 1200V700Q480 300 880 700V1200" fill="#17233b"/></svg>'
            return self.send_body(svg,'image/svg+xml')
        if p.endswith('/availability'):
            day=q.get('day',['2030-01-07'])[0]
            ids=q.get('service_ids',q.get('service_id',[S[0]]))
            selected=[s for s in DATA['services'] if s['id'] in ids]
            minutes=sum(s['duration_min'] for s in selected)
            limit=13 if date.fromisoformat(day).weekday()==5 else 18
            slots=[f'{day}T{h}:00-03:00' for h in ['09:00','11:00','14:30','15:00']
                   if int(h[:2])*60+int(h[3:])+minutes<=limit*60]
            if date.fromisoformat(day).weekday()==6: slots=[]
            return self.send_body(dict(slots=slots,quote='synthetic-quote-never-accepted-for-real-appointments',
                duration_min=minutes,price_cents=sum(s['price_cents'] for s in selected),
                price_mode='from' if any(s['price_mode']=='from' for s in selected) else 'fixed',
                services=[s['name'] for s in selected],timezone=DATA['timezone']))
        if p.startswith('/api/v1/public/sites/') or p.endswith('/management/site/preview'):
            return self.send_body(DATA)
        self.send_body({'detail':'Not found'},status=404)
    def log_message(self,*args): pass

if __name__=='__main__':
    print('Read-only fixture: http://127.0.0.1:8891/s/empresa-sintetica',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8891),Handler).serve_forever()
