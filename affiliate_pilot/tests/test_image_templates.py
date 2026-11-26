"""Image templates: real PNG/JPEG renders and tenant-isolated API storage."""
import base64
import io
import json
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from affiliate_pilot.card_style import ImageStyle,contrast,palette
from affiliate_pilot import offers
from affiliate_pilot.main import create_app
from affiliate_pilot.store import Store,DEAL_TEMPLATE,DEAL_TEMPLATE_ID,DEFAULT_TEMPLATE

BASE="http://testserver"
A="a"*32
B="b"*32
T="c"*32

def png(color="#aa3311",size=(50,50)):
    image=Image.new("RGB",size,color)
    output=io.BytesIO();image.save(output,"PNG")
    return output.getvalue()

def logo():
    return "data:image/png;base64,"+base64.b64encode(png()).decode()

def payload(**style):
    return {"name":"Meu modelo","brand":"Luna Ofertas","layout":"discount","media":"card",
            "style":"plain","text":"{produto}\n{preco}\n{link}",
            "image_style":{"theme":"dark","accent":"#aa3311","background":"#101318",
                           "surface":"#1e222a","price":"#ffcc44","handle":"@luna","footer":"Ofertas diárias",
                           "cta":"Aproveite agora","logo":logo(),"show_brand":True,
                           "show_discount":True,"show_previous":True,"show_store":False,
                           "show_coupon":False,**style}}

@pytest.fixture
def ctx(tmp_path,monkeypatch):
    key=Fernet.generate_key()
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY",key.decode())
    store=Store(tmp_path/"templates.sqlite3",Fernet.generate_key().decode())
    signer=Fernet(key)
    def headers(tenant,method,path):
        return {"Origin":BASE,"X-Orion-Affiliate-Access":signer.encrypt(json.dumps(
            {"tenant":tenant,"name":"Luna Ofertas" if tenant==A else "Outra empresa","method":method,"path":path}
        ).encode()).decode()}
    with TestClient(create_app(store,origin=BASE,start_worker=False,sending=False)) as client:
        yield store,client,headers

def call(ctx,tenant,method,path,body=None):
    store,client,headers=ctx
    return client.request(method,path,headers=headers(tenant,method,path),json=body)

def get_state(ctx,tenant):
    r=call(ctx,tenant,"GET","/api/state")
    assert r.is_success,r.text
    return r.json()

def test_default_brand_and_legacy_seed_migration(ctx):
    store,_,_=ctx
    a=store.orion_workspace(A,"Luna Ofertas")["id"]
    b=store.orion_workspace(B,"Outra empresa")["id"]
    legacy={**DEAL_TEMPLATE,"name":"PromoTech com IA","brand":"PromoTech"}
    store.put(a,"template",DEAL_TEMPLATE_ID,legacy)
    store.put(b,"template",DEAL_TEMPLATE_ID,{**legacy,"name":"Marca escolhida"})
    store.ensure_deal_templates()
    assert store.get(a,"template",DEAL_TEMPLATE_ID)["brand"]=="Luna Ofertas"
    assert store.get(a,"template",DEAL_TEMPLATE_ID)["text"]==legacy["text"]
    assert store.get(b,"template",DEAL_TEMPLATE_ID)["brand"]=="PromoTech"
    assert store.get(a,"template","default")["brand"]=="Luna Ofertas"
    store.ensure_deal_templates()
    assert store.get(a,"template",DEAL_TEMPLATE_ID)["name"]=="Oferta com IA"

def test_save_preview_default_and_isolation(ctx):
    store,client,headers=ctx
    before=get_state(ctx,A)
    assert before["image_templates_v1"] is True
    data=payload()
    preview=call(ctx,A,"POST","/api/templates/preview-image",data)
    assert preview.is_success,preview.text
    raw=base64.b64decode(preview.json()["image"].split(",",1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size==(900,970)
        assert image.getpixel((0,0))==(16,19,24)
        assert image.getpixel((70,70))!=(16,19,24)
    assert get_state(ctx,A)["jobs"]==before["jobs"]
    assert get_state(ctx,A)["offer"]==before["offer"]
    assert get_state(ctx,A)["template"]==before["template"]
    saved=call(ctx,A,"PUT","/api/templates/"+T,data)
    assert saved.is_success,saved.text
    assert saved.json()["image_style"]["logo"]==data["image_style"]["logo"]
    assert call(ctx,B,"POST","/api/templates/"+T+"/default",{}).status_code==404
    assert call(ctx,A,"POST","/api/templates/"+T+"/default",{}).json()["default"]==T
    assert get_state(ctx,A)["deal_template"]==T
    assert get_state(ctx,B)["deal_template"]!=T
    assert not any(t["id"]==T for t in get_state(ctx,B)["template"])
    # The exact saved template drives the existing offer preview.
    assert call(ctx,A,"POST","/api/offers/demo",None).is_success
    result=client.get("/api/offers/demo/card.png?template="+T,headers=headers(A,"GET","/api/offers/demo/card.png"))
    assert result.is_success and result.headers["content-type"]=="image/png"
    assert Image.open(io.BytesIO(result.content)).getpixel((0,0))==(16,19,24)
    assert client.post("/api/templates/preview-image",headers={"Origin":BASE},json=data).status_code==401

def test_empty_brand_uses_company_name_and_old_client_preserves_style(ctx):
    get_state(ctx,A)
    data=payload()
    data["brand"]=""
    response=call(ctx,A,"PUT","/api/templates/"+T,data)
    assert response.json()["brand"]=="Luna Ofertas"
    old={k:v for k,v in data.items() if k!="image_style"}
    old["brand"]="Nova marca"
    saved=call(ctx,A,"PUT","/api/templates/"+T,old).json()
    assert saved["brand"]=="Nova marca"
    assert saved["image_style"]["cta"]=="Aproveite agora"

@pytest.mark.parametrize("fields",[
    {"accent":"red"},{"background":"url(https://example.invalid/)"},
    {"logo":"data:image/svg+xml;base64,PHN2Zz4="},
    {"logo":"data:image/png;base64,AAAA"},{"logo":"https://example.invalid/logo.png"},
    {"logo":"data:image/png;base64,"+base64.b64encode(png(size=(300,300))).decode()},
    {"footer":"x"*65},{"theme":"unknown"},{"unexpected":True},
])
def test_bad_image_fields_rejected(ctx,fields):
    response=call(ctx,A,"PUT","/api/templates/"+T,payload(**fields))
    assert response.status_code==422

@pytest.mark.asyncio
async def test_same_renderer_used_for_publication_formats(monkeypatch):
    photo=png("#888888")
    async def image(url):
        return photo
    monkeypatch.setattr(offers,"product_image",image)
    offer={"title":"Produto de teste","price":"49.90","price_max":"49.90",
           "previous_price":"79.90","currency":"BRL","image":"https://example.invalid/synthetic.png","retailer_name":"Loja de exemplo"}
    template=payload()
    manual=await offers.render_card(offer,template)
    assert manual==offers.card_png(offer,template,photo)
    card,thumb=await offers.render_channel_card(offer,template)
    with Image.open(io.BytesIO(card)) as image:
        assert image.size==(900,970) and image.format=="JPEG"
        assert max(abs(a-b) for a,b in zip(image.getpixel((0,0)),(16,19,24)))<=4
    assert max(Image.open(io.BytesIO(thumb)).size)<=140
    other=offers.card_png(offer,payload(accent="#663399"),photo)
    assert other!=manual

def test_price_and_header_contrast():
    for background in ["#000000","#ffffff","#777777","#808080","#ffcc00","#202020"]:
        style=ImageStyle(surface=background,accent=background,price=background)
        colors=palette({"layout":"simple"},style)
        assert contrast(colors["price"],colors["surface"])>=4.5
        assert contrast(colors["header_ink"],colors["accent"])>=4.5
