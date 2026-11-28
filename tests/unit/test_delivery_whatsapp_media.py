"""Local media decoding and provider contract tests. No external provider is called."""
import base64
import io
import json
from datetime import datetime,timezone

import httpx
from PIL import Image,ImageDraw
import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from app.delivery_whatsapp_media import DeliveryMedia,DeliveryAddress,DeliveryPin
from app.delivery_whatsapp_vision import PhotoAnalysis,normalize_image,analyze_image
from app import delivery_whatsapp as wa,delivery_whatsapp_vision as vision
from app.onboarding_schemas import Address


def photo():
    img=Image.new("RGB",(320,240),"white")
    draw=ImageDraw.Draw(img)
    draw.rectangle((100,55,220,210),fill="lightblue",outline="blue")
    draw.rectangle((135,25,185,55),fill="blue")
    draw.text((130,100),"20 L",fill="black")
    out=io.BytesIO()
    img.save(out,format="PNG")
    return out.getvalue()


@pytest.mark.parametrize("lat,lon",[(91,0),(0,181),(float("nan"),0),(True,0),("12",0)])
def test_reject_invalid_coordinates(lat,lon):
    with pytest.raises(ValidationError):
        DeliveryMedia(kind="location",latitude=lat,longitude=lon)


def test_legacy_address_keeps_existing_serialization():
    old=Address(street="Rua de teste",number="12",city="Campo Grande",state="MS",neighborhood="Centro")
    assert DeliveryAddress.model_validate(old).model_dump(mode="json")==old.model_dump(mode="json")


def test_valid_photo_is_decoded_and_sanitized():
    result=normalize_image(photo(),"image/png")
    with Image.open(io.BytesIO(result)) as image:
        assert image.format=="JPEG" and image.size==(320,240)
        assert not image.getexif()
    for raw,mime in [(b"not an image","image/png"),(photo(),"image/jpeg"),(photo(),"text/plain")]:
        with pytest.raises(HTTPException):
            normalize_image(raw,mime)


def test_model_output_cannot_contain_business_authority():
    with pytest.raises(ValidationError):
        PhotoAnalysis.model_validate({"category":"receipt","mark_paid":True})
    with pytest.raises(ValidationError):
        PhotoAnalysis(category="gas_container",volume_ml=20000)
    assert "20 L" in wa.photo_reply(PhotoAnalysis(category="water",volume_ml=20000))
    assert "conferido" in wa.photo_reply(PhotoAnalysis(category="receipt"))
    assert "equipe" in wa.photo_reply(PhotoAnalysis(category="gas_container"))
    assert "segurança" in wa.photo_reply(None)


@pytest.mark.asyncio
async def test_real_photo_bytes_reach_mocked_vision_http_and_schema(monkeypatch):
    raw=photo()
    original=httpx.AsyncClient
    observed=[]
    def handler(req):
        assert str(req.url)==vision.PROVIDER_URL
        data=json.loads(req.content)
        assert data["model"]==vision.MODEL
        assert data["response_format"]=={"type":"json_object"}
        img=data["messages"][0]["content"][1]["image_url"]["url"]
        assert img.startswith("data:image/jpeg;base64,")
        with Image.open(io.BytesIO(base64.b64decode(img.split(",",1)[1]))) as decoded:
            assert decoded.size==(320,240) and not decoded.getexif()
        observed.append("provider")
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(
            {"category":"water","volume_ml":20000,"address":None})}}]})
    monkeypatch.setenv("GROQ_API_KEY","synthetic-key-not-real")
    monkeypatch.setattr(vision.httpx,"AsyncClient",lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    class Request:
        headers={"content-type":"image/png","content-length":str(len(raw))}
        async def stream(self):
            yield raw
    async def authorize(phase):
        observed.append(phase)
    result=await analyze_image(Request(),authorize=authorize)
    assert result==PhotoAnalysis(category="water",volume_ml=20000)
    assert observed==["before_provider","provider","after_provider"]


@pytest.mark.asyncio
async def test_invalid_photo_never_reserves_quota_or_contacts_provider():
    class Request:
        headers={"content-type":"image/png"}
        async def stream(self):
            yield b"invalid"
    async def authorize(phase):
        pytest.fail("Invalid bytes must fail before authorization")
    with pytest.raises(HTTPException):
        await analyze_image(Request(),authorize=authorize)
