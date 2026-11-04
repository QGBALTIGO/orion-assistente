"""Synthetic files, real codecs, and an in-memory private bucket. No sends."""
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from botocore.exceptions import ClientError
from fastapi import HTTPException
from PIL import Image, PngImagePlugin
import pytest

from app import subscription_chatbot_media as media


def photo(size=(180, 120)):
    image = Image.new("RGBA", size, (44, 88, 132, 190))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "synthetic-secret-metadata")
    target = io.BytesIO()
    image.save(target, format="PNG", pnginfo=metadata)
    return target.getvalue()


def animated(*, duration=120, count=4):
    frames = [Image.new("RGBA", (80, 60), (index * 20, 120, 180, 255)) for index in range(count)]
    target = io.BytesIO()
    frames[0].save(target, format="GIF", append_images=frames[1:], save_all=True,
                   duration=duration, loop=0, comment=b"synthetic-secret-metadata")
    return target.getvalue()


def test_image_has_real_jpeg_pixels_and_private_metadata_removed():
    result = media.prepare_media(photo((2200, 1100)), "image")
    assert result.mime == "image/jpeg" and result.size == len(result.data)
    assert result.sha256 == sha256(result.data).hexdigest()
    assert (result.width, result.height) == (1600, 800)
    assert b"synthetic-secret-metadata" not in result.data
    with Image.open(io.BytesIO(result.data)) as image:
        image.load()
        assert image.format == "JPEG" and image.size == (1600, 800)
    with Image.open(io.BytesIO(result.thumbnail)) as thumb:
        assert thumb.size == (320, 160)
    assert result.thumbnail_mime == "image/jpeg"
    assert result.thumbnail_sha256 == sha256(result.thumbnail).hexdigest()


@pytest.mark.parametrize("format", ["JPEG", "PNG"])
def test_comment_metadata_cannot_survive_in_main_image_or_thumbnail(format):
    image = Image.new("RGB", (180, 120), "blue")
    stream = io.BytesIO()
    if format == "JPEG":
        image.save(stream, format=format, comment=b"synthetic-private-comment")
    else:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("comment", "synthetic-private-comment")
        image.save(stream, format=format, pnginfo=metadata)
    result = media.prepare_media(stream.getvalue(), "image")
    for data in (result.data, result.thumbnail):
        assert b"synthetic-private-comment" not in data
        with Image.open(io.BytesIO(data)) as output:
            assert not output.info.get("comment") and not output.info.get("exif")


def test_narrow_normalized_image_can_be_decoded_again_for_archive_import():
    first = media.prepare_media(photo((16, 4096)), "image")
    assert (first.width, first.height) == (6, 1600)
    second = media.prepare_media(first.data, "image")
    assert (second.width, second.height) == (6, 1600)
    assert second.size == len(second.data) and second.sha256 == sha256(second.data).hexdigest()
    with Image.open(io.BytesIO(second.data)) as decoded:
        decoded.load()
        assert decoded.size == (6, 1600)


def test_sticker_keeps_transparency_and_512_canvas():
    result = media.prepare_media(photo(), "sticker")
    assert result.mime == "image/webp" and result.duration_ms is None
    assert result.size <= media.MAX_STICKER_STATIC_BYTES
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.size == (512, 512)
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0
    assert b"synthetic-secret-metadata" not in result.data


def test_animated_sticker_preserves_frames_and_duration_without_metadata():
    result = media.prepare_media(animated(), "sticker")
    assert result.duration_ms == 480
    assert result.size <= media.MAX_STICKER_ANIMATED_BYTES
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.n_frames == 4 and image.size == (512, 512)
    assert b"synthetic-secret-metadata" not in result.data


@pytest.mark.parametrize("raw,kind", [
    (b"<svg><script>not-an-image</script></svg>", "image"),
    (b"#EXTM3U\nhttps://127.0.0.1/internal", "video"),
    (b"\x89PNG\r\n\x1a\nnot-real-pixels", "image"),
    (b"not-a-video", "ptv"),
    (b"\xff\x00binary", "document"),
    (b"secret\x00hidden", "document"),
    (b" \r\n\t", "document"),
    (b"", "image"),
])
def test_invalid_or_active_input_is_rejected_without_storage(raw, kind, monkeypatch):
    monkeypatch.setattr(media, "_storage", lambda: pytest.fail("must not access storage"))
    with pytest.raises(HTTPException) as error:
        media.prepare_media(raw, kind)
    assert error.value.status_code in {415, 422}


def test_oversize_and_busy_are_bounded_before_decoding(monkeypatch):
    monkeypatch.setattr(media, "MAX_IMAGE_INPUT_BYTES", 4)
    monkeypatch.setattr(media, "_photo", lambda *a, **k: pytest.fail("must not decode"))
    with pytest.raises(HTTPException) as error:
        media.prepare_media(b"12345", "image")
    assert error.value.status_code == 413
    assert media._slot.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            media.prepare_media(b"123", "image")
        assert error.value.status_code == 429
    finally:
        media._slot.release()


def test_animation_duration_and_decoded_pixel_limits(monkeypatch):
    with pytest.raises(HTTPException) as error:
        media.prepare_media(animated(duration=2000), "sticker")
    assert error.value.status_code == 413
    monkeypatch.setattr(media, "MAX_IMAGE_PIXELS", 200)
    with pytest.raises(HTTPException) as error:
        media.prepare_media(photo(), "image")
    assert error.value.status_code == 413


def test_text_document_keeps_complete_utf8_with_newlines():
    raw = b"\xef\xbb\xbf" + "Resposta integral.\nAção\t✅\n".encode()
    result = media.prepare_media(raw, "document")
    assert result.data.decode() == "Resposta integral.\nAção\t✅\n"
    assert result.mime == "text/plain; charset=utf-8"
    assert result.thumbnail is None and result.duration_ms is None


class MemoryBucket:
    def __init__(self):
        self.values = {}
        self.writes = []
        self.deleted = []
        self.read_sizes = []

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        assert kwargs["IfNoneMatch"] == "*"
        if key in self.values:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.writes.append(kwargs)
        self.values[key] = bytes(kwargs["Body"])

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs["Key"])
        self.values.pop(kwargs["Key"], None)

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.values:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        parent = self

        class Reader(io.BytesIO):
            def read(self, size=-1):
                parent.read_sizes.append(size)
                return super().read(size)
        return {"Body": Reader(self.values[key])}


@pytest.fixture
def bucket(monkeypatch):
    value = MemoryBucket()
    monkeypatch.setattr(media, "_storage", lambda: (value, "synthetic-private-bucket"))
    return value


def test_storage_is_private_immutable_and_not_a_catalog_photo(bucket):
    tenant, asset = uuid4(), uuid4()
    result = media.prepare_media(photo(), "image")
    media.put_media(tenant, asset, result)
    assert len(bucket.writes) == 2
    for write in bucket.writes:
        assert write["CacheControl"] == "private, no-store"
        assert write["ContentDisposition"] == "attachment"
        assert write["Key"].startswith(f"tenant/{tenant}/subscription-chatbot/{asset}/")
        assert "ACL" not in write
    assert media.get_media(tenant, asset, result.size, result.sha256) == result.data
    assert bucket.read_sizes == [result.size + 1]
    assert media.get_media(tenant, asset, len(result.thumbnail), result.thumbnail_sha256, "thumbnail") == result.thumbnail
    with pytest.raises(HTTPException) as error:
        media.get_media(uuid4(), asset, result.size, result.sha256)
    assert error.value.status_code == 404
    media.put_media(tenant, asset, result)
    # A retry verifies identical bytes without rewriting or removing the object.
    assert len(bucket.writes) == 2
    assert bucket.deleted == []
    assert media.get_media(tenant, asset, result.size, result.sha256) == result.data


def test_partial_storage_retry_completes_only_missing_thumbnail(bucket):
    tenant, asset = uuid4(), uuid4()
    result = media.prepare_media(photo(), "image")
    original, thumbnail = media._key(tenant, asset, "original"), media._key(tenant, asset, "thumbnail")
    bucket.values[original] = result.data
    media.put_media(tenant, asset, result)
    assert [write["Key"] for write in bucket.writes] == [thumbnail]
    assert bucket.deleted == [] and bucket.values[original] == result.data
    media.put_media(tenant, asset, result)
    assert len(bucket.writes) == 1


def test_preexisting_different_content_is_never_overwritten_or_deleted(bucket):
    tenant, asset = uuid4(), uuid4()
    result = media.prepare_media(photo(), "image")
    original = media._key(tenant, asset, "original")
    bucket.values[original] = b"x" * result.size
    with pytest.raises(HTTPException) as error:
        media.put_media(tenant, asset, result)
    assert error.value.status_code == 409
    assert bucket.values[original] == b"x" * result.size
    assert bucket.deleted == [] and bucket.writes == []


def test_thumbnail_conflict_compensates_only_the_original_created_by_this_attempt(bucket):
    tenant, asset = uuid4(), uuid4()
    result = media.prepare_media(photo(), "image")
    original, thumbnail = media._key(tenant, asset, "original"), media._key(tenant, asset, "thumbnail")
    bucket.values[thumbnail] = b"x" * len(result.thumbnail)
    with pytest.raises(HTTPException) as error:
        media.put_media(tenant, asset, result)
    assert error.value.status_code == 409
    assert bucket.deleted == [original] and original not in bucket.values
    assert bucket.values[thumbnail] == b"x" * len(result.thumbnail)


@pytest.mark.parametrize("preexisting", [False, True])
def test_storage_failure_cleans_up_only_keys_created_by_this_attempt(bucket, monkeypatch, preexisting):
    tenant, asset = uuid4(), uuid4()
    result = media.prepare_media(photo(), "image")
    original, thumbnail = media._key(tenant, asset, "original"), media._key(tenant, asset, "thumbnail")
    if preexisting:
        bucket.values[original] = result.data
    actual_put = bucket.put_object

    def fail_thumbnail(**kwargs):
        if kwargs["Key"] == thumbnail:
            raise ClientError({"Error": {"Code": "InternalError"}}, "PutObject")
        actual_put(**kwargs)

    monkeypatch.setattr(bucket, "put_object", fail_thumbnail)
    with pytest.raises(HTTPException) as error:
        media.put_media(tenant, asset, result)
    assert error.value.status_code == 503
    assert bucket.deleted == ([] if preexisting else [original])
    assert (original in bucket.values) == preexisting


@pytest.mark.parametrize("corrupt", ["size", "hash", "extra"])
def test_changed_stored_bytes_never_reach_transport(bucket, corrupt):
    tenant, asset = uuid4(), uuid4()
    result = media.prepare_media(b"Synthetic text only", "document")
    media.put_media(tenant, asset, result)
    key = media._key(tenant, asset, "original")
    bucket.values[key] = {"size": result.data[:-1], "hash": b"x" * result.size,
                          "extra": result.data + b"x"}[corrupt]
    with pytest.raises(HTTPException) as error:
        media.get_media(tenant, asset, result.size, result.sha256)
    assert error.value.status_code == 409


@pytest.mark.parametrize("variant", ["../../original", "https://example.com", "file:///secret", "original?x=1"])
def test_no_user_path_or_url_can_select_storage(bucket, variant):
    with pytest.raises(HTTPException) as error:
        media.get_media(uuid4(), uuid4(), 10, "a" * 64, variant)
    assert error.value.status_code == 422 and bucket.read_sizes == []


def test_declared_mime_checksum_and_size_cannot_bypass_preparation(bucket):
    result = media.prepare_media(b"Synthetic document", "document")
    for bad in (replace(result, mime="text/html"), replace(result, sha256="0" * 64),
                replace(result, size=1)):
        with pytest.raises(HTTPException):
            media.put_media(uuid4(), uuid4(), bad)
    assert bucket.writes == []


@pytest.fixture(scope="module")
def real_video(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("real FFmpeg is required by the release job")
    folder = tmp_path_factory.mktemp("synthetic-chatbot-video")
    path = folder / "test.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x100:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                    "-t", "1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1",
                    "-c:a", "aac", "-metadata", "title=synthetic-secret-metadata", str(path)],
                   check=True, timeout=15)
    return path.read_bytes()


@pytest.mark.parametrize("kind", ["video", "ptv"])
def test_real_video_and_ptv_are_complete_h264_aac_and_metadata_free(real_video, tmp_path, kind):
    result = media.prepare_media(real_video, kind)
    assert result.mime == "video/mp4" and 1150 <= result.duration_ms <= 1450
    assert b"synthetic-secret-metadata" not in result.data
    if kind == "ptv":
        assert result.width == result.height == 512
    else:
        assert max(result.width, result.height) <= 720
    target = tmp_path / "output.mp4"
    target.write_bytes(result.data)
    actual = media._probe(target, "mov", shutil.which("ffprobe"))
    assert actual["video_codec"] == "h264" and actual["audio_codecs"] == ["aac"]
    with Image.open(io.BytesIO(result.thumbnail)) as thumb:
        thumb.load()
        assert thumb.format == "JPEG" and max(thumb.size) <= 320


def test_overlong_video_is_rejected_before_transcode(real_video, monkeypatch):
    monkeypatch.setattr(media, "MAX_VIDEO_SECONDS", .25)
    with pytest.raises(HTTPException) as error:
        media.prepare_media(real_video, "video")
    assert error.value.detail["code"] == "CHATBOT_MEDIA_DURATION"


def test_decoder_timeout_is_explicit_and_slot_is_released(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(media.subprocess, "run", timeout)
    with pytest.raises(HTTPException) as error:
        media.prepare_media(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20, "video")
    assert error.value.detail["code"] == "CHATBOT_MEDIA_TIMEOUT"
    assert media._slot.acquire(blocking=False)
    media._slot.release()
