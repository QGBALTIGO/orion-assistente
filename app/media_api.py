"""Tenant-authenticated photo upload with private S3 persistence and public opaque URLs.

Incoming bytes are decoded and re-encoded as WebP: uploads are never served
with attacker-supplied MIME, filename, EXIF, or active SVG/HTML content.
The S3 bucket remains private; only unguessable, tenant-prefixed media keys
can be fetched from /media. Railway redeploys do not erase uploaded photos.
"""
import asyncio
import io
import os
from collections import OrderedDict
from functools import lru_cache
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from redis.exceptions import RedisError

from app.catalog_api import Actor, authenticate
from app.media_paths import media_key
from app.panel_auth import _redis
from app.tenant_access import resolve_tenant_access

router = APIRouter(tags=["Imagens Orion"])
MAX_INPUT = 8 * 1024 * 1024
MAX_RESULT = 1_500_000
MAX_PIXELS = 18_000_000
UPLOAD_PER_TENANT_PER_HOUR = 80
VALID_MIMES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/octet-stream"}
KINDS = {"service", "professional", "package", "logo", "cover"}
LIMITS = {"service": (1200, 1200), "professional": (1200, 1200),
          "package": (1200, 1200), "logo": (900, 900), "cover": (2000, 1200)}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
register_heif_opener()
_gate = asyncio.Semaphore(3)

# Media paths are immutable UUIDs, so a bounded RAM cache avoids repeated
# private-bucket round trips on busy public booking pages. Never cache errors.
MEDIA_VARIANTS = {
    "thumb": (320, 320),
    "portrait": (512, 512),
    "logo": (320, 320),
    "hero": (1600, 960),
}
MEDIA_CACHE_MAX_BYTES = 48 * 1024 * 1024
_media_cache: OrderedDict[tuple[UUID, UUID, str | None], bytes] = OrderedDict()
_media_cache_bytes = 0
_media_cache_lock = Lock()


@lru_cache(maxsize=1)
def _storage():
    config = {k: os.getenv(k, "") for k in (
        "ORION_MEDIA_S3_ENDPOINT", "ORION_MEDIA_S3_BUCKET",
        "ORION_MEDIA_S3_REGION", "ORION_MEDIA_S3_ACCESS_KEY_ID",
        "ORION_MEDIA_S3_SECRET_ACCESS_KEY")}
    if not all(config.values()):
        raise HTTPException(503, "Armazenamento de imagens não configurado. Configure o bucket de imagens na Railway.")
    import boto3
    client = boto3.client(
        "s3", endpoint_url=config["ORION_MEDIA_S3_ENDPOINT"],
        aws_access_key_id=config["ORION_MEDIA_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=config["ORION_MEDIA_S3_SECRET_ACCESS_KEY"],
        region_name=config["ORION_MEDIA_S3_REGION"],
        config=Config(signature_version="s3v4", s3={"addressing_style": os.getenv("ORION_MEDIA_S3_URL_STYLE", "virtual")},
                      connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
    )
    return client, config["ORION_MEDIA_S3_BUCKET"]


async def _upload_limit(tenant: UUID) -> None:
    redis = _redis()
    try:
        key = f"orion:media:upload:{tenant}"
        pipe = redis.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, 3600, nx=True)
        count, _ = await pipe.execute()
        if count > UPLOAD_PER_TENANT_PER_HOUR:
            raise HTTPException(429, "Muitos envios de imagens. Aguarde antes de tentar novamente.")
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível verificar o limite de imagens.") from None
    finally:
        await redis.aclose()


def _encode(raw: bytes, kind: str) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}:
                raise HTTPException(415, "Escolha uma foto JPG, PNG, WebP ou HEIC.")
            if source.width * source.height > MAX_PIXELS or min(source.size) < 16:
                raise HTTPException(413, "A imagem tem dimensões não permitidas.")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in {"P", "RGBA", "LA"} or "transparency" in oriented.info:
                image = oriented.convert("RGBA")
            else:
                image = oriented.convert("RGB")
            image.thumbnail(LIMITS[kind], Image.Resampling.LANCZOS)
            for quality in (84, 74, 64, 54):
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=quality, method=5, exif=b"")
                if output.tell() <= MAX_RESULT:
                    return output.getvalue()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(415, "Não consegui ler essa imagem. Tente JPG, PNG, WebP ou HEIC.") from None
    raise HTTPException(413, "A imagem é grande demais mesmo após otimização.")


def _put(tenant: UUID, image: UUID, data: bytes) -> None:
    client, bucket = _storage()
    client.put_object(Bucket=bucket, Key=media_key(tenant, image),
                      Body=data, ContentType="image/webp",
                      CacheControl="public, max-age=31536000, immutable",
                      )


def _get(tenant: UUID, image: UUID) -> bytes:
    client, bucket = _storage()
    try:
        stream = client.get_object(Bucket=bucket, Key=media_key(tenant, image))["Body"]
        try:
            body = stream.read(MAX_RESULT + 1)
        finally:
            stream.close()
        if not body or len(body) > MAX_RESULT:
            raise HTTPException(404, "Imagem indisponível")
        return body
    except ClientError as ex:
        if ex.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(404, "Imagem não encontrada") from None
        raise


def _cached_media(tenant: UUID, image: UUID, variant: str | None) -> bytes:
    """Return a display-sized WebP; cache by immutable ID + rendition.

    Work runs in a thread so S3 and Pillow never block FastAPI's event loop.
    The lock is held only for the short cache lookup/eviction, never for S3 I/O.
    """
    global _media_cache_bytes
    key = (tenant, image, variant)
    with _media_cache_lock:
        cached = _media_cache.get(key)
        if cached is not None:
            _media_cache.move_to_end(key)
            return cached

    data = _get(tenant, image)
    if variant is not None:
        try:
            with Image.open(io.BytesIO(data)) as picture:
                picture.thumbnail(MEDIA_VARIANTS[variant], Image.Resampling.LANCZOS)
                output = io.BytesIO()
                picture.save(output, format="WEBP", quality=76, method=4, exif=b"")
                data = output.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(503, "Foto temporariamente indisponível") from None

    if len(data) <= MEDIA_CACHE_MAX_BYTES:
        with _media_cache_lock:
            # Another request can have filled the same entry while S3 was read.
            cached = _media_cache.get(key)
            if cached is not None:
                _media_cache.move_to_end(key)
                return cached
            while _media_cache and _media_cache_bytes + len(data) > MEDIA_CACHE_MAX_BYTES:
                _, evicted = _media_cache.popitem(last=False)
                _media_cache_bytes -= len(evicted)
            _media_cache[key] = data
            _media_cache_bytes += len(data)
    return data


@router.post("/api/v1/tenants/{tenant_id}/media", status_code=201)
async def upload_media(tenant_id: UUID, request: Request,
                       actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "draft")
    kind = request.headers.get("x-orion-image-kind", "").strip().lower()
    if kind not in KINDS:
        raise HTTPException(422, "Tipo de imagem inválido")
    mimetype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if mimetype not in VALID_MIMES:
        raise HTTPException(415, "Escolha uma foto JPG, PNG, WebP ou HEIC.")
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Tamanho do arquivo inválido") from None
    if length > MAX_INPUT or length < 0:
        raise HTTPException(413, "A foto deve ter até 8 MB.")
    _storage()  # Report missing bucket before consuming the input.
    if _gate.locked():
        raise HTTPException(429, "Há muitos uploads simultâneos. Tente novamente.")
    async with _gate:
        await _upload_limit(tenant_id)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_INPUT:
                raise HTTPException(413, "A foto deve ter até 8 MB.")
            data.extend(chunk)
        if not data:
            raise HTTPException(422, "Selecione uma foto antes de enviar.")
        image_bytes = await asyncio.to_thread(_encode, bytes(data), kind)
        media_id = uuid4()
        try:
            await asyncio.to_thread(_put, tenant_id, media_id, image_bytes)
        except HTTPException:
            raise
        except (ClientError, BotoCoreError, OSError):
            raise HTTPException(503, "Falha ao guardar a foto. Tente novamente.") from None
    return {"url": f"/media/{tenant_id}/{media_id}.webp",
            "content_type": "image/webp", "bytes": len(image_bytes)}


@router.get("/media/{tenant_id}/{media_id}.webp", include_in_schema=False)
async def serve_media(tenant_id: UUID, media_id: UUID,
                      variant: Literal["thumb", "portrait", "logo", "hero"] | None = None):
    # IDs must be unpredictable; these files are intended for public catalogues.
    try:
        body = await asyncio.to_thread(_cached_media, tenant_id, media_id, variant)
    except HTTPException:
        raise
    except (ClientError, BotoCoreError, OSError):
        raise HTTPException(503, "Foto temporariamente indisponível") from None
    return Response(content=body, media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'",
                             "Referrer-Policy": "no-referrer"})
