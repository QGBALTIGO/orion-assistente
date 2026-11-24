"""The live booking wizard restores only remembered or verified customer data."""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from browser_public_cache_profile import boot, routes, enter_confirmation, SLUG

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


@pytest.mark.parametrize("browser_name", ["chromium","webkit"])
def test_real_browser_accepts_first_booking_cookie_and_autofills_return(browser_name):
    with sync_playwright() as playwright:
        browser=getattr(playwright,browser_name).launch(headless=True)
        context=browser.new_context(locale="pt-BR",viewport={"width":375,"height":667},
                                    is_mobile=True,has_touch=True)
        page=context.new_page()
        errors,posted,profile_calls=[],[],[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        routes(page,{"offline":False},posted)

        def profile(route):
            cookie=route.request.all_headers().get("cookie","")
            profile_calls.append((route.request.method,COOKIE+"=verified" in cookie))
            assert route.request.method=="GET"
            route.fulfill(status=200,json=profile_result(COOKIE+"=verified" in cookie))

        def book(route):
            payload=route.request.post_data_json
            posted.append(payload)
            route.fulfill(status=201,headers={
                "content-type":"application/json",
                "set-cookie":COOKIE+"=verified; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=2592000",
                "cache-control":"private, no-store",
            },json={
                "status":"confirmed","protocol":"TESTE-AUTOFILL","start_at":"2030-01-07T10:30:00-04:00",
                "end_at":"2030-01-07T11:10:00-04:00","price_cents":4000,"professional":"Guilherme",
                "services":["Corte masculino"],"timezone":"America/Campo_Grande",
                "customer_area_connected":True,
            })
        page.route(ACCOUNT,profile)
        page.route(APPOINTMENTS,book)
        try:
            boot(page)
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
            boot(page)
            enter_confirmation(page)
            form,phone,name,birth=form_fields(page)
            expect(phone).to_have_value("(86) 97267-6282")
            expect(name).to_have_value(PROFILE["name"])
            expect(birth).to_have_value(DOB)
            expect(form.locator(".booking-customer-status")).to_contain_text("preenchidos automaticamente")
            assert any(verified for _,verified in profile_calls)
            assert page.evaluate("(key)=>localStorage.getItem(key)",KEY) is None
            # A different phone must never receive the current customer's data.
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
