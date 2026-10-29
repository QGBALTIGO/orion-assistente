"""Image upload security, conversion, and public persistence without real buckets."""
import io
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image, PngImagePlugin
from pydantic import ValidationError

from app import media_api as media
from app.catalog_api import Actor, authenticate
from app.catalog_schemas import Professional, Service
from app.management_schemas import SitePreferences
from app.media_paths import is_media_url, media_key


def photo(kind="PNG", size=(100, 90)):
    img = Image.new("RGB", size, "#5285c6")
    out = io.BytesIO()
    if kind == "PNG":
        exif = PngImagePlugin.PngInfo()
        exif.add_text("private_secret", "never-export-secret")
        img.save(out, format=kind, pnginfo=exif)
    else:
        img.save(out, format=kind)
    return out.getvalue()


def test_converts_real_pixels_to_webp_with_no_metadata():
    result = media._encode(photo(), "service")
    assert b"never-export-secret" not in result
    assert len(result) < 100_000
    with Image.open(io.BytesIO(result)) as img:
        assert img.format == "WEBP"
        assert img.size == (100, 90)


@pytest.mark.parametrize("raw", [
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<html><img src=x onerror=alert(1)></html>",
    b"\x89PNG\r\n\x1a\n" + b"broken",
    b"",
])
def test_bad_image_rejected_before_storage(raw):
    with pytest.raises(HTTPException) as error:
        media._encode(raw, "service")
    assert error.value.status_code in {413, 415}


def test_safe_media_paths_only_and_https_legacy_unchanged():
    tenant, image = uuid4(), uuid4()
    url = f"/media/{tenant}/{image}.webp"
    assert is_media_url(url)
    assert media_key(tenant, image) == f"tenant/{tenant}/media/{image}.webp"
    for bad in ("javascript:alert(1)", f"/media/{tenant}/../{image}.webp",
                f"//evil.example/{tenant}/{image}.webp", f"/media/{tenant}/{image}.svg",
                f"/media/{tenant}/{image}.webp?x=1"):
        assert not is_media_url(bad)
    assert Service(name="Corte", duration_min=30, price_cents=4000, image_url=url).image_url == url
    assert Professional(name="Barbeiro", image_url=url).image_url == url
    assert SitePreferences(title="Studio", logo_url=url, cover_url=url).cover_url == url
    assert Service(name="Corte", duration_min=30, price_cents=4000,
                   image_url="https://example.invalid/image.png").image_url
    with pytest.raises(ValidationError):
        SitePreferences(title="Studio", external_url=url)
    with pytest.raises(ValidationError):
        Service(name="Corte", duration_min=30, price_cents=4000, image_url="/etc/passwd")


class MemoryBucket:
    def __init__(self):
        self.items = {}
        self.uploads = []
        self.reads = 0
    def put_object(self, **kwargs):
        self.uploads.append(kwargs)
        self.items[kwargs["Key"]] = bytes(kwargs["Body"])
        return {"ETag": '"test"'}
    def get_object(self, **kwargs):
        self.reads += 1
        key = kwargs["Key"]
        if key not in self.items:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.items[key])}


@pytest.fixture
def server(monkeypatch):
    bucket=MemoryBucket()
    monkeypatch.setattr(media, "_storage", lambda: (bucket, "unit-test-bucket"))
    async def quota(_tenant):
        return None
    monkeypatch.setattr(media, "_upload_limit", quota)
    app=FastAPI()
    app.include_router(media.router)
    app.dependency_overrides[authenticate] = lambda: Actor("support")
    return app,bucket


@pytest.mark.asyncio
async def test_auth_upload_writes_opaque_tenant_prefix_and_serves_webp(server):
    app, bucket=server
    tenant=uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.test") as client:
        response=await client.post(f"/api/v1/tenants/{tenant}/media",
                                   content=photo(),headers={"content-type":"image/png","x-orion-image-kind":"service"})
        assert response.status_code == 201, response.text
        result=response.json()
        url=result["url"]
        assert is_media_url(url)
        assert result["content_type"] == "image/webp"
        assert len(bucket.uploads)==1
        assert bucket.uploads[0]["Bucket"]=="unit-test-bucket"
        assert bucket.uploads[0]["Key"].startswith(f"tenant/{tenant}/media/")
        assert bucket.uploads[0]["ContentType"]=="image/webp"
        got=await client.get(url)
        assert got.status_code==200
        assert got.headers["content-type"]=="image/webp"
        assert "nosniff" in got.headers["x-content-type-options"]
        assert "immutable" in got.headers["cache-control"]
        assert got.content == bucket.items[bucket.uploads[0]["Key"]]
        foreign=await client.get(url.replace(str(tenant),str(uuid4())))
        assert foreign.status_code==404


@pytest.mark.asyncio
async def test_readonly_role_cannot_upload_even_with_valid_image(server):
    app,bucket=server
    tenant=uuid4()
    app.dependency_overrides[authenticate]=lambda:Actor("viewer", tenant)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.test") as client:
        response=await client.post(f"/api/v1/tenants/{tenant}/media",content=photo(),
                                   headers={"content-type":"image/png","x-orion-image-kind":"logo"})
    assert response.status_code==403
    assert bucket.uploads==[]


@pytest.mark.asyncio
async def test_large_bytes_and_invalid_mime_rejected_without_bucket_writes(server):
    app,bucket=server
    tenant=uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.test") as client:
        big=await client.post(f"/api/v1/tenants/{tenant}/media",content=b"x"*(media.MAX_INPUT+1),
                              headers={"content-type":"image/png","x-orion-image-kind":"service"})
        assert big.status_code==413
        wrong=await client.post(f"/api/v1/tenants/{tenant}/media",content=b"<svg/>",
                                headers={"content-type":"image/svg+xml","x-orion-image-kind":"service"})
        assert wrong.status_code==415
        fake=await client.post(f"/api/v1/tenants/{tenant}/media",content=b"<svg/>",
                               headers={"content-type":"image/png","x-orion-image-kind":"service"})
        assert fake.status_code==415
    assert bucket.uploads==[]


@pytest.mark.asyncio
async def test_quota_denies_upload_before_writing(monkeypatch,server):
    app,bucket=server
    async def denied(_tenant):
        raise HTTPException(429,"Muitos uploads")
    monkeypatch.setattr(media,"_upload_limit",denied)
    tenant=uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.test") as client:
        response=await client.post(f"/api/v1/tenants/{tenant}/media",
                                   content=photo(),headers={"content-type":"image/png","x-orion-image-kind":"logo"})
    assert response.status_code==429
    assert bucket.uploads==[]


@pytest.mark.asyncio
async def test_public_display_variants_are_smaller_and_cached(server):
    app, bucket = server
    tenant, image = uuid4(), uuid4()
    url = f"/media/{tenant}/{image}.webp"
    original = media._encode(photo(size=(1800, 1200)), "cover")
    bucket.items[media_key(tenant, image)] = original
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://orion.test") as client:
        thumb = await client.get(url + "?variant=thumb")
        assert thumb.status_code == 200
        assert "immutable" in thumb.headers["cache-control"]
        with Image.open(io.BytesIO(thumb.content)) as decoded:
            assert decoded.format == "WEBP"
            assert decoded.width <= 320 and decoded.height <= 320
        before = bucket.reads
        again = await client.get(url + "?variant=thumb")
        assert again.content == thumb.content
        assert bucket.reads == before  # A second viewer needs no S3 round trip.

        hero = await client.get(url + "?variant=hero")
        assert hero.status_code == 200
        with Image.open(io.BytesIO(hero.content)) as decoded:
            assert decoded.width <= 1600 and decoded.height <= 960

        original_response = await client.get(url)
        assert original_response.status_code == 200
        assert original_response.content == original
        assert (await client.get(url + "?variant=unbounded")).status_code == 422
