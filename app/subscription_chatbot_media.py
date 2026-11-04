"""Bounded, metadata-free media for the subscription WhatsApp chatbot.

This library has no HTTP fetch, public route or tenant authorization shortcut.
Callers authenticate, enforce durable quotas and reauthorize before publishing
the registry. Only UUID-derived private keys and verified bytes leave here.
Run these synchronous helpers with ``asyncio.to_thread`` from async handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from threading import BoundedSemaphore
from typing import Literal
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError


MediaKind = Literal["image", "sticker", "video", "ptv", "document"]
KINDS = frozenset({"image", "sticker", "video", "ptv", "document"})
MAX_INPUT_BYTES = 16_000_000
MAX_IMAGE_INPUT_BYTES = 8_000_000
MAX_OUTPUT_BYTES = 16_000_000
MAX_IMAGE_BYTES = 5_000_000
MAX_DOCUMENT_BYTES = 1_000_000
MAX_THUMBNAIL_BYTES = 100_000
MAX_IMAGE_PIXELS = 18_000_000
MAX_VIDEO_PIXELS = 9_000_000
MAX_VIDEO_SECONDS = 180
MAX_PTV_SECONDS = 60
MAX_STICKER_SECONDS = 6
MAX_STICKER_FRAMES = 150
MAX_STICKER_STATIC_BYTES = 100_000
MAX_STICKER_ANIMATED_BYTES = 500_000
_slot = BoundedSemaphore(1)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


@dataclass(frozen=True)
class PreparedMedia:
    data: bytes
    mime: str
    size: int
    sha256: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    thumbnail: bytes | None = None
    thumbnail_mime: str | None = None
    thumbnail_sha256: str | None = None


def _result(data: bytes, mime: str, *, width=None, height=None,
            duration_ms=None, thumbnail: bytes | None = None) -> PreparedMedia:
    if not data or len(data) > MAX_OUTPUT_BYTES:
        raise _error(413, "CHATBOT_MEDIA_TOO_LARGE", "O arquivo preparado ultrapassa o limite de 16 MB.")
    if thumbnail and len(thumbnail) > MAX_THUMBNAIL_BYTES:
        raise _error(413, "CHATBOT_MEDIA_INVALID", "Não foi possível preparar a miniatura.")
    return PreparedMedia(data, mime, len(data), sha256(data).hexdigest(), width,
                         height, duration_ms, thumbnail,
                         "image/jpeg" if thumbnail else None,
                         sha256(thumbnail).hexdigest() if thumbnail else None)


def _rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA" or "transparency" in image.info:
        alpha = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(alpha, mask=alpha.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _jpeg(image: Image.Image, *, edge: int, limit: int) -> bytes:
    photo = _rgb(image)
    # Pillow carries COM/PNG "comment" through convert()/thumbnail() and uses
    # it as a JPEG save default even with exif=b"". Drop every inherited field.
    photo.info.clear()
    photo.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    for quality in (85, 75, 65, 50):
        output = io.BytesIO()
        photo.save(output, format="JPEG", quality=quality, optimize=True, exif=b"",
                   comment=b"", xmp=b"", icc_profile=b"")
        if output.tell() <= limit:
            return output.getvalue()
    raise _error(413, "CHATBOT_MEDIA_TOO_LARGE", "A imagem ultrapassa o tamanho permitido.")


def _photo(raw: bytes, *, sticker: bool) -> PreparedMedia:
    try:
        formats = ("JPEG", "PNG", "WEBP", "GIF") if sticker else ("JPEG", "PNG", "WEBP")
        with Image.open(io.BytesIO(raw), formats=formats) as source:
            if (source.width * source.height > MAX_IMAGE_PIXELS
                    or min(source.size) < 1 or max(source.size) > 16_384):
                raise _error(413, "CHATBOT_MEDIA_DIMENSIONS", "As dimensões da imagem não são permitidas.")
            frame_count = getattr(source, "n_frames", 1)
            if frame_count > 1 and not sticker:
                raise _error(415, "CHATBOT_MEDIA_ANIMATED", "Use o tipo Figurinha para imagens animadas.")
            if frame_count > MAX_STICKER_FRAMES:
                raise _error(413, "CHATBOT_MEDIA_ANIMATION", "A figurinha tem quadros demais.")
            if not sticker:
                source.load()
                photo = ImageOps.exif_transpose(source)
                photo.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                data = _jpeg(photo, edge=1600, limit=MAX_IMAGE_BYTES)
                return _result(data, "image/jpeg", width=photo.width, height=photo.height,
                               thumbnail=_jpeg(photo, edge=320, limit=MAX_THUMBNAIL_BYTES))
            # Keep animation when present. Never silently flatten it to frame 1.
            frames, durations = [], []
            for index in range(frame_count):
                source.seek(index)
                source.load()
                duration = source.info.get("duration", 100) if frame_count > 1 else 0
                if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                        or not math.isfinite(duration) or duration < 0):
                    raise ValueError()
                duration = max(20, round(duration)) if frame_count > 1 else 0
                durations.append(duration)
                if sum(durations) > MAX_STICKER_SECONDS * 1000:
                    raise _error(413, "CHATBOT_MEDIA_ANIMATION", "A figurinha deve ter até 6 segundos.")
                frame = ImageOps.exif_transpose(source).convert("RGBA")
                frame.thumbnail((512, 512), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
                canvas.paste(frame, ((512 - frame.width) // 2, (512 - frame.height) // 2))
                frames.append(canvas)
            limit = MAX_STICKER_ANIMATED_BYTES if frame_count > 1 else MAX_STICKER_STATIC_BYTES
            for quality in (80, 65, 50, 35):
                output = io.BytesIO()
                frames[0].save(output, format="WEBP", quality=quality, method=4,
                               save_all=frame_count > 1, append_images=frames[1:],
                               duration=durations, loop=0, exif=b"", xmp=b"", icc_profile=b"")
                if output.tell() <= limit:
                    return _result(output.getvalue(), "image/webp", width=512, height=512,
                                   duration_ms=sum(durations) if frame_count > 1 else None,
                                   thumbnail=_jpeg(frames[0], edge=320, limit=MAX_THUMBNAIL_BYTES))
            raise _error(413, "CHATBOT_MEDIA_TOO_LARGE", "A figurinha ultrapassa o tamanho permitido.")
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, EOFError, OSError, ValueError, TypeError):
        raise _error(415, "CHATBOT_MEDIA_INVALID", "Não foi possível ler a imagem completa.") from None


def _run(arguments: list[str], *, capture=False, timeout=60) -> bytes:
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise _error(422, "CHATBOT_MEDIA_TIMEOUT", "O arquivo demorou demais para ser preparado.") from None
    except OSError:
        raise _error(503, "CHATBOT_MEDIA_UNAVAILABLE", "O preparo de mídia está indisponível.") from None
    if result.returncode or (capture and len(result.stdout) > 65_536):
        raise _error(415, "CHATBOT_MEDIA_INVALID", "Não foi possível ler o vídeo completo.")
    return result.stdout if capture else b""


def video_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _probe(path: Path, demuxer: str, executable: str) -> dict:
    data = _run([executable, "-v", "error", "-max_alloc", "33554432",
                 "-protocol_whitelist", "file", "-f", demuxer,
                 "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,duration",
                 "-of", "json", str(path)], capture=True, timeout=5)
    try:
        value = json.loads(data)
        streams = value["streams"]
        videos = [s for s in streams if s.get("codec_type") == "video"]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        duration = float(value["format"]["duration"])
        if (not math.isfinite(duration) or duration <= 0 or len(streams) > 4
                or len(videos) != 1 or len(audios) > 1):
            raise ValueError()
        video = videos[0]
        width, height = int(video["width"]), int(video["height"])
        rate = video.get("avg_frame_rate", "0/1")
        if not isinstance(rate, str) or len(rate) > 24:
            raise ValueError()
        fps = float(Fraction(rate))
        if (not math.isfinite(fps) or fps <= 0 or fps > 120
                or min(width, height) < 16 or max(width, height) > 4096
                or width * height > MAX_VIDEO_PIXELS
                or video.get("codec_name") not in {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4"}):
            raise ValueError()
        return {"width": width, "height": height, "duration": duration,
                "video_codec": video["codec_name"], "audio_codecs": [s.get("codec_name") for s in audios]}
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        raise _error(415, "CHATBOT_MEDIA_INVALID", "Não foi possível verificar o formato e a duração do vídeo.") from None


def _video(raw: bytes, *, ptv: bool) -> PreparedMedia:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise _error(503, "CHATBOT_MEDIA_UNAVAILABLE", "O preparo de vídeo está indisponível.")
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        demuxer = "mov"
    elif raw[:4] == b"\x1a\x45\xdf\xa3":
        demuxer = "matroska"
    else:
        raise _error(415, "CHATBOT_MEDIA_FORMAT", "Escolha um vídeo MP4 ou WebM.")
    max_seconds = MAX_PTV_SECONDS if ptv else MAX_VIDEO_SECONDS
    with tempfile.TemporaryDirectory(prefix="orion-chatbot-media-") as directory:
        source, target, thumb = [Path(directory) / name for name in ("source.bin", "video.mp4", "thumb.jpg")]
        for path, data in ((source, raw), (target, b""), (thumb, b"")):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        original = _probe(source, demuxer, ffprobe)
        if original["duration"] > max_seconds + 0.05:
            raise _error(413, "CHATBOT_MEDIA_DURATION", f"Este vídeo deve ter até {max_seconds} segundos.")
        filters = ("scale=512:512:force_original_aspect_ratio=decrease,"
                   "pad=512:512:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30" if ptv else
                   "scale=720:720:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30")
        input_options = ["-enable_drefs", "0", "-use_absolute_path", "0"] if demuxer == "mov" else []
        _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
              "-max_alloc", "33554432", "-protocol_whitelist", "file", "-err_detect", "explode",
              "-threads", "1", "-f", demuxer, *input_options, "-i", str(source),
              "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1",
              "-sn", "-dn", "-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
              "-pix_fmt", "yuv420p", "-maxrate", "1500k", "-bufsize", "3000k",
              "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "44100",
              "-threads", "1", "-t", str(max_seconds + 1), "-fs", str(MAX_OUTPUT_BYTES * 2),
              "-max_muxing_queue_size", "64", "-movflags", "+faststart", "-f", "mp4", str(target)], timeout=60)
        if target.stat().st_size > MAX_OUTPUT_BYTES:
            raise _error(413, "CHATBOT_MEDIA_TOO_LARGE", "O vídeo preparado ultrapassa 16 MB.")
        verified = _probe(target, "mov", ffprobe)
        if (verified["duration"] > max_seconds + 0.05
                or abs(verified["duration"] - original["duration"]) > max(0.25, original["duration"] * .01)
                or verified["video_codec"] != "h264"
                or any(codec != "aac" for codec in verified["audio_codecs"])):
            raise _error(415, "CHATBOT_MEDIA_INCOMPLETE", "O vídeo não pôde ser preparado por completo.")
        _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
              "-max_alloc", "33554432", "-protocol_whitelist", "file", "-threads", "1",
              "-f", "mov", "-i", str(target), "-map", "0:v:0", "-frames:v", "1", "-an",
              "-vf", "scale=320:320:force_original_aspect_ratio=decrease", "-map_metadata", "-1",
              "-q:v", "5", "-threads", "1", "-f", "image2", str(thumb)], timeout=5)
        # Decode the thumbnail too, stripping encoder metadata before persistence.
        with Image.open(thumb, formats=("JPEG",)) as photo:
            photo.load()
            thumbnail = _jpeg(photo, edge=320, limit=MAX_THUMBNAIL_BYTES)
        return _result(target.read_bytes(), "video/mp4", width=verified["width"],
                       height=verified["height"], duration_ms=round(verified["duration"] * 1000),
                       thumbnail=thumbnail)


def prepare_media(raw: bytes, kind: MediaKind) -> PreparedMedia:
    """Normalize actual contents, never MIME labels, paths, URLs or extensions."""
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(raw, bytes) or not raw:
        raise _error(422, "CHATBOT_MEDIA_INVALID", "Escolha um arquivo e um tipo de mídia válidos.")
    limit = (MAX_IMAGE_INPUT_BYTES if kind in {"image", "sticker"}
             else MAX_DOCUMENT_BYTES if kind == "document" else MAX_INPUT_BYTES)
    if len(raw) > limit:
        raise _error(413, "CHATBOT_MEDIA_TOO_LARGE", "O arquivo ultrapassa o tamanho permitido para este tipo.")
    if not _slot.acquire(blocking=False):
        raise _error(429, "CHATBOT_MEDIA_BUSY", "Outro arquivo está sendo preparado. Tente novamente em instantes.")
    try:
        if kind in {"image", "sticker"}:
            return _photo(raw, sticker=kind == "sticker")
        if kind in {"video", "ptv"}:
            return _video(raw, ptv=kind == "ptv")
        try:
            text = raw.decode("utf-8-sig")
            if not text.strip() or any(ord(c) < 32 and c not in "\t\n\r" for c in text):
                raise ValueError()
        except (UnicodeDecodeError, ValueError):
            raise _error(415, "CHATBOT_MEDIA_INVALID", "O documento deve conter texto UTF-8 válido.") from None
        return _result(text.encode("utf-8"), "text/plain; charset=utf-8")
    finally:
        _slot.release()


def _key(tenant: UUID, asset: UUID, variant: str) -> str:
    if not isinstance(tenant, UUID) or not isinstance(asset, UUID) or variant not in {"original", "thumbnail"}:
        raise _error(422, "CHATBOT_MEDIA_INVALID", "Referência de mídia inválida.")
    return f"tenant/{tenant}/subscription-chatbot/{asset}/{variant}"


def _storage():
    # Reuse private bucket credentials/connection, never public catalog routes.
    from app.media_api import _storage as catalog_storage
    return catalog_storage()


def put_media(tenant: UUID, asset: UUID, prepared: PreparedMedia) -> None:
    original = _key(tenant, asset, "original")
    if (not isinstance(prepared, PreparedMedia) or not isinstance(prepared.data, bytes)
            or not 0 < prepared.size <= MAX_OUTPUT_BYTES or prepared.size != len(prepared.data)
            or prepared.sha256 != sha256(prepared.data).hexdigest()
            or prepared.mime not in {"image/jpeg", "image/webp", "video/mp4", "text/plain; charset=utf-8"}):
        raise _error(422, "CHATBOT_MEDIA_INVALID", "A mídia não foi preparada corretamente.")
    variants = [(original, prepared.data, prepared.mime, prepared.sha256)]
    if prepared.thumbnail:
        if (not isinstance(prepared.thumbnail, bytes) or len(prepared.thumbnail) > MAX_THUMBNAIL_BYTES
                or prepared.thumbnail_mime != "image/jpeg"
                or prepared.thumbnail_sha256 != sha256(prepared.thumbnail).hexdigest()):
            raise _error(422, "CHATBOT_MEDIA_INVALID", "A miniatura não foi preparada corretamente.")
        variants.append((_key(tenant, asset, "thumbnail"), prepared.thumbnail,
                         prepared.thumbnail_mime, prepared.thumbnail_sha256))
    client, bucket = _storage()
    written = []
    try:
        for key, data, mime, checksum in variants:
            try:
                client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=mime,
                                  CacheControl="private, no-store", ContentDisposition="attachment",
                                  Metadata={"sha256": checksum}, IfNoneMatch="*")
            except ClientError as error:
                if (error.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}
                        and error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412):
                    raise
                # Storage may have committed before the registry transaction
                # lost its lease. Retry is idempotent only for identical bytes.
                # A pre-existing object never enters this call's cleanup list.
                get_media(tenant, asset, len(data), checksum,
                          "original" if key == original else "thumbnail")
            else:
                written.append(key)
    except (ClientError, BotoCoreError, OSError, HTTPException) as error:
        # Only compensate keys this invocation created, never a pre-existing asset.
        for key in written:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except (ClientError, BotoCoreError, OSError):
                pass
        if isinstance(error, HTTPException):
            raise
        raise _error(503, "CHATBOT_MEDIA_STORAGE", "Não foi possível guardar a mídia.") from None


def get_media(tenant: UUID, asset: UUID, expected_size: int, expected_sha256: str,
              variant: Literal["original", "thumbnail"] = "original") -> bytes:
    key = _key(tenant, asset, variant)
    limit = MAX_THUMBNAIL_BYTES if variant == "thumbnail" else MAX_OUTPUT_BYTES
    if (type(expected_size) is not int or not 0 < expected_size <= limit
            or not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
        raise _error(422, "CHATBOT_MEDIA_INVALID", "Referência de mídia inválida.")
    client, bucket = _storage()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        stream = response["Body"]
        try:
            data = stream.read(expected_size + 1)
        finally:
            stream.close()
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "NotFound", "404"}:
            raise _error(404, "CHATBOT_MEDIA_MISSING", "Mídia não encontrada.") from None
        raise _error(503, "CHATBOT_MEDIA_STORAGE", "Não foi possível carregar a mídia.") from None
    except (BotoCoreError, OSError):
        raise _error(503, "CHATBOT_MEDIA_STORAGE", "Não foi possível carregar a mídia.") from None
    if len(data) != expected_size or not hmac.compare_digest(sha256(data).hexdigest(), expected_sha256):
        raise _error(409, "CHATBOT_MEDIA_CHANGED", "A integridade da mídia não pôde ser confirmada.")
    return data
