"""The live booking wizard restores only remembered or verified customer data."""
import json
import ssl
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from browser_public_cache_profile import boot, routes, enter_confirmation, SLUG, CATALOGUE

PHONE = "+5586972676282"
OTHER = "67987654321"
DOB = "1990-07-06"
PROFILE = {"name": "Cliente sintético", "phone": PHONE, "birth_date": DOB}
COOKIE = "orion_synthetic_booking_session"
KEY = "orion:public:customer:v1:" + SLUG
ACCOUNT = f"**/api/v1/public/sites/{SLUG}/account/booking-profile"
APPOINTMENTS = f"**/api/v1/public/sites/{SLUG}/appointments"


def profile_result(authenticated=False):
    return {"authenticated":authenticated,"login_method":"birth_date",
            **({"profile":PROFILE} if authenticated else {})}


def form_fields(page):
    form=page.locator("#customer-form")
    return (form,form.locator('[name="customer_phone"]'),
            form.locator('[name="customer_name"]'),form.locator('[name="customer_birth_date"]'))


@contextmanager
def https_booking_site(posted, profile_calls):
    """Use real HTTPS responses so WebKit routing cannot hide request cookies."""
    static=Path(__file__).resolve().parents[2]/"app/static"
    prefix=f"/api/v1/public/sites/{SLUG}"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def respond(self,status,body,content_type="application/json",headers=None):
            if isinstance(body,dict):
                body=json.dumps(body,ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            for key,value in (headers or {}).items():
                self.send_header(key,value)
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path==f"/s/{SLUG}":
                return self.respond(200,(static/"orion-public.html").read_bytes(),"text/html; charset=utf-8")
            if path.startswith(("/public-assets/","/brand/")):
                filename=path.rsplit("/",1)[-1]
                asset=static/filename
                kinds={".js":"application/javascript",".css":"text/css",".woff2":"font/woff2",".svg":"image/svg+xml"}
                if asset.is_file() and asset.suffix in kinds:
                    return self.respond(200,asset.read_bytes(),kinds[asset.suffix])
                return self.respond(404,{})
            if path==prefix:
                return self.respond(200,CATALOGUE)
            if path==prefix+"/availability":
                return self.respond(200,{"slots":["2030-01-07T10:30:00-04:00"],
                    "quote":"q"*55,"price_cents":4000,"price_mode":"fixed","duration_min":40,
                    "services":["Corte masculino"],"timezone":"America/Campo_Grande"})
            if path==prefix+"/account/booking-profile":
                verified=COOKIE+"=verified" in self.headers.get("Cookie","")
                profile_calls.append(("GET",verified))
                return self.respond(200,profile_result(verified))
            return self.respond(404,{})
        def do_POST(self):
            if urlsplit(self.path).path!=prefix+"/appointments":
                return self.respond(404,{})
            payload=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posted.append(payload)
            return self.respond(201,{"status":"confirmed","protocol":"TESTE-AUTOFILL",
                "start_at":"2030-01-07T10:30:00-04:00","end_at":"2030-01-07T11:10:00-04:00",
                "price_cents":4000,"professional":"Guilherme","services":["Corte masculino"],
                "timezone":"America/Campo_Grande","customer_area_connected":True},
                headers={"Set-Cookie":COOKIE+"=verified; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=2592000"})
    with tempfile.TemporaryDirectory() as directory:
        cert,key=Path(directory)/"cert.pem",Path(directory)/"key.pem"
        subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes",
                        "-keyout",str(key),"-out",str(cert),"-days","1",
                        "-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost,IP:127.0.0.1"],
                        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        server.socket=tls.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            yield f"https://localhost:{server.server_port}/s/{SLUG}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.parametrize("browser_name", ["chromium","webkit"])
def test_real_browser_accepts_first_booking_cookie_and_autofills_return(browser_name):
    with sync_playwright() as playwright:
        browser=getattr(playwright,browser_name).launch(headless=True)
        context=browser.new_context(locale="pt-BR",viewport={"width":375,"height":667},
                                    is_mobile=True,has_touch=True,ignore_https_errors=True)
        page=context.new_page()
        errors,posted,profile_calls=[],[],[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        try:
            with https_booking_site(posted,profile_calls) as url:
                page.goto(url,wait_until="domcontentloaded")
                page.locator("#site-content").wait_for(state="visible")
                assert not profile_calls
                enter_confirmation(page)
                form,phone,name,birth=form_fields(page)
                assert form.locator(".field>input").evaluate_all("(nodes)=>nodes.map(n=>n.name)") == [
                    "customer_phone","customer_name","customer_birth_date"]
                expect(name).to_have_value("")
                expect(birth).to_have_value("")
                phone.fill("86972676282")
                name.fill(PROFILE["name"])
                birth.fill(DOB)
                form.locator('[name="remember_customer"]').check()
                page.locator("#booking-next").click()
                expect(page.locator(".success")).to_be_visible()
                assert len(posted)==1 and posted[0]["customer_portal_remember"] is True
                assert posted[0]["customer_phone"]==PHONE
                assert any(c["name"]==COOKIE and c["value"]=="verified" and c["httpOnly"]
                           for c in context.cookies()), "The actual browser must accept Set-Cookie"
                # Only the authenticated session remains; no local form cache.
                page.evaluate("(key)=>localStorage.removeItem(key)",KEY)
                page.goto(url,wait_until="domcontentloaded")
                page.locator("#site-content").wait_for(state="visible")
                enter_confirmation(page)
                form,phone,name,birth=form_fields(page)
                expect(phone).to_have_value("(86) 97267-6282",timeout=12000)
                expect(name).to_have_value(PROFILE["name"])
                expect(birth).to_have_value(DOB)
                expect(form.locator(".booking-customer-status")).to_contain_text("preenchidos automaticamente")
                assert any(verified for _,verified in profile_calls),profile_calls
                assert page.evaluate("(key)=>localStorage.getItem(key)",KEY) is None
                calls_before=len(profile_calls)
                phone.fill(OTHER)
                expect(name).to_have_value("")
                expect(birth).to_have_value("")
                name.fill("Outra pessoa")
                birth.fill("1995-01-02")
                phone.fill("86972676282")
                expect(name).to_have_value(PROFILE["name"])
                expect(birth).to_have_value(DOB)
                assert len(profile_calls)==calls_before
                assert not errors,errors
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("browser_name", ["chromium","webkit"])
def test_new_device_recovers_only_after_explicit_birthdate_verification(browser_name):
    with sync_playwright() as playwright:
        browser=getattr(playwright,browser_name).launch(headless=True)
        context=browser.new_context(locale="pt-BR",viewport={"width":390,"height":820},
                                    is_mobile=True,has_touch=True)
        page=context.new_page()
        errors,posted,attempts=[],[],[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        routes(page,{"offline":False},posted)

        def profile(route):
            if route.request.method=="GET":
                route.fulfill(status=200,json=profile_result())
                return
            payload=route.request.post_data_json
            attempts.append(payload)
            assert payload["phone"]==PHONE
            if payload["birth_date"]!=DOB:
                route.fulfill(status=403,json={"detail":"Acesso inválido"})
                return
            route.fulfill(status=200,json=profile_result(True))
        page.route(ACCOUNT,profile)
        try:
            boot(page)
            enter_confirmation(page)
            form,phone,name,birth=form_fields(page)
            phone.fill("86972676282")
            expect(form.locator(".booking-customer-status")).to_contain_text("Já é cliente?")
            expect(name).to_have_value("")
            expect(birth).to_have_value("")
            expect(form.locator(".booking-customer-recover")).not_to_be_visible()
            birth.fill("1990-07-07")
            assert not attempts,"Never query a customer on keystrokes or DOB changes"
            form.locator(".booking-customer-recover").click()
            expect(form.locator(".booking-customer-status")).to_contain_text("Não foi possível recuperar")
            expect(name).to_have_value("")
            birth.fill(DOB)
            form.locator(".booking-customer-recover").click()
            expect(name).to_have_value(PROFILE["name"])
            expect(birth).to_have_value(DOB)
            expect(form.locator(".booking-customer-status")).to_contain_text("preenchidos automaticamente")
            assert len(attempts)==2
            assert not posted and not errors,(posted,errors)
            assert page.evaluate("(key)=>localStorage.getItem(key)",KEY) is None
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("browser_name", ["chromium","webkit"])
def test_local_cache_and_late_session_never_mix_two_people(browser_name):
    with sync_playwright() as playwright:
        browser=getattr(playwright,browser_name).launch(headless=True)
        context=browser.new_context(locale="pt-BR",viewport={"width":320,"height":568})
        page=context.new_page()
        errors,posted,pending=[],[],[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        routes(page,{"offline":False},posted)
        page.route(ACCOUNT,lambda route:pending.append(route))
        try:
            boot(page)
            page.evaluate("""({key,profile})=>localStorage.setItem(key,JSON.stringify({
              version:1,expiresAt:Date.now()+86400000,value:{
                phone:profile.phone,name:profile.name,birthDate:profile.birth_date
              }
            }))""",{"key":KEY,"profile":PROFILE})
            enter_confirmation(page)
            form,phone,name,birth=form_fields(page)
            expect(name).to_have_value(PROFILE["name"])
            expect(birth).to_have_value(DOB)
            phone.fill(OTHER)
            expect(name).to_have_value("")
            expect(birth).to_have_value("")
            assert not form.locator('[name="remember_customer"]').is_checked()
            name.fill("Cliente diferente")
            birth.fill("2000-01-01")
            assert pending
            pending.pop().fulfill(status=200,json=profile_result(True))
            # Let the intercepted response be processed before checking the fields.
            page.evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
            expect(name).to_have_value("Cliente diferente")
            expect(birth).to_have_value("2000-01-01")
            phone.fill("86972676282")
            expect(name).to_have_value(PROFILE["name"])
            expect(birth).to_have_value(DOB)
            form.locator(".forget-customer").click()
            assert not form.locator('[name="remember_customer"]').is_checked()
            assert page.evaluate("(key)=>localStorage.getItem(key)",KEY) is None
            phone.fill(OTHER)
            phone.fill("86972676282")
            expect(name).to_have_value("")
            expect(birth).to_have_value("")
            assert not errors and not posted,(errors,posted)
        finally:
            context.close()
            browser.close()
