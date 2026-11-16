"""Complete synthetic files and delivery plans; no network or customer sends."""
from copy import deepcopy
import io
import shutil
import struct
import subprocess
import wave
import zipfile

from fastapi import HTTPException
import pytest

from app import subscription_chatbot_media as media
from app import subscription_chatbot_rules as rules
from app import subscription_chatbot_broker as broker
from app import subscription_template_documents as documents
from tests.unit.test_subscription_chatbot_rules import action, rule, sequence, graph, FACTS, CONTEXT, identity


def pdf_bytes():
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate((b"<< /Type /Catalog /Pages 2 0 R >>",
                                  b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                                  b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>"), 1):
        offsets.append(len(output))
        output.extend(str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n")
    start = len(output)
    output.extend(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n")
    return bytes(output)


def zipped(entries):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return target.getvalue()


def office_entries(kind):
    word = kind == "docx"
    part = "word/document.xml" if word else "xl/workbook.xml"
    ctype = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" if word else
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    namespace = ("http://schemas.openxmlformats.org/wordprocessingml/2006/main" if word else
                 "http://schemas.openxmlformats.org/spreadsheetml/2006/main")
    tag = "document" if word else "workbook"
    return {
        "[Content_Types].xml": f'<Types xmlns="{documents.CONTENT_NAMESPACE}"><Override PartName="/{part}" ContentType="{ctype}"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{documents.REL_NAMESPACE}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{part}"/></Relationships>',
        part: f'<{tag} xmlns="{namespace}"/>',
    }


@pytest.mark.parametrize("filename,raw,mime", [
    ("teste.pdf", pdf_bytes(), "application/pdf"),
    ("teste.docx", zipped(office_entries("docx")), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("teste.xlsx", zipped(office_entries("xlsx")), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("teste.apk", zipped({"AndroidManifest.xml": '<manifest package="com.example.synthetic"/>'}), "application/vnd.android.package-archive"),
    ("teste.csv", "nome,quantidade\nTeste,2\n".encode(), "text/plain; charset=utf-8"),
])
def test_supported_attachments_are_content_checked_and_preserved(filename, raw, mime):
    result = media.prepare_media(raw, "document", filename=filename)
    assert result.data == raw and result.mime == mime
    assert result.width is result.height is result.duration_ms is result.thumbnail is None
    # An import sees the same canonical contents without trusting a filename.
    restored = media.prepare_media(result.data, "document")
    assert restored.data == result.data and restored.mime == result.mime


@pytest.mark.parametrize("filename,raw", [
    ("fake.pdf", b"this is plain text, not a PDF"),
    ("fake.docx", zipped({"word/document.xml": "<document/>"})),
    ("fake.apk", zipped({"AndroidManifest.xml": b"\x03\x00\x08\x00\x00\x00\x00\x00"})),
    ("empty.apk", zipped({"AndroidManifest.xml": b"\x03\x00\x08\x00\x08\x00\x00\x00"})),
    ("fake.xlsx", zipped({**office_entries("xlsx"), "xl/workbook.xml": "<!DOCTYPE x [<!ENTITY x 'x'>]><x>&x;</x>"})),
    ("fake.docx", zipped({**office_entries("docx"), "../outside": "forbidden"})),
    ("fake.pdf", pdf_bytes().replace(b"startxref", b"notaxref")),
    ("fake.csv", b"binary\x00not-text"),
    ("fake.exe", b"not an allowed filename"),
])
def test_forged_or_unsafe_document_structure_is_refused(filename, raw):
    with pytest.raises(HTTPException) as error:
        media.prepare_media(raw, "document", filename=filename)
    assert error.value.status_code == 415


def test_zip_expansion_and_input_limits_are_checked_before_contents_are_used(monkeypatch):
    monkeypatch.setattr(documents, "MAX_EXPANDED_BYTES", 32)
    with pytest.raises(HTTPException):
        media.prepare_media(zipped(office_entries("docx")), "document")
    monkeypatch.setattr(media, "MAX_DOCUMENT_BYTES", 4)
    with pytest.raises(HTTPException) as error:
        media.prepare_media(b"12345", "document")
    assert error.value.status_code == 413


def test_corrupt_deflate_in_an_apk_returns_a_validation_error_instead_of_a_server_error():
    raw = bytearray(zipped({"AndroidManifest.xml": "<manifest/>"}))
    name_length, extra_length = struct.unpack_from("<HH", raw, 26)
    raw[30 + name_length + extra_length] = 0xFF
    with pytest.raises(HTTPException) as error:
        media.prepare_media(bytes(raw), "document", filename="corrupt.apk")
    assert error.value.status_code == 415


def wave_bytes(seconds=0.3):
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * round(16000 * seconds))
    return target.getvalue()


@pytest.mark.skipif(not media.video_available(), reason="real ffmpeg/ffprobe are required")
@pytest.mark.parametrize("extension,codec,format", [("wav", None, None), ("mp3", "libmp3lame", "mp3"),
                                                     ("ogg", "libvorbis", "ogg"), ("opus", "libopus", "ogg")])
def test_supported_audio_formats_decode_and_retain_complete_duration(tmp_path, extension, codec, format):
    raw = wave_bytes()
    if codec:
        source, target = tmp_path / "input.wav", tmp_path / ("input." + extension)
        source.write_bytes(raw)
        subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error", "-y", "-i", str(source),
                        "-c:a", codec, "-f", format, str(target)], check=True, timeout=20)
        raw = target.read_bytes()
    output = media.prepare_media(raw, "audio", filename="audio." + extension)
    assert output.mime == "audio/mpeg" and 250 <= output.duration_ms <= 450
    assert output.width is output.height is output.thumbnail is None
    # The actual decoder must read our normalized output, not a mocked blob.
    path = tmp_path / "normalized.mp3"
    path.write_bytes(output.data)
    verified = media._probe_audio(path, "mp3", shutil.which("ffprobe"))
    assert verified["codec"] == "mp3" and .25 <= verified["duration"] <= .45


@pytest.mark.parametrize("raw", [b"https://example.invalid/audio.mp3", b"#EXTM3U\nfile:///secret", b"RIFFfakeWAVE", b"OggSbroken"])
def test_audio_cannot_be_a_url_playlist_or_invalid_header(raw):
    with pytest.raises(HTTPException):
        media.prepare_media(raw, "audio", filename="audio.mp3")


@pytest.mark.skipif(not media.video_available(), reason="real codecs required")
def test_overlong_audio_is_refused_before_encoding_instead_of_truncated(monkeypatch):
    monkeypatch.setattr(media, "MAX_AUDIO_SECONDS", .1)
    actual_run = media._run
    def run(arguments, **kwargs):
        assert "libmp3lame" not in arguments, "duration must be refused before the encoder starts"
        return actual_run(arguments, **kwargs)
    monkeypatch.setattr(media, "_run", run)
    with pytest.raises(HTTPException) as error:
        media.prepare_media(wave_bytes(), "audio")
    assert error.value.status_code == 413


@pytest.mark.skipif(not media.video_available(), reason="real codecs required")
def test_ogg_with_a_moving_video_track_is_not_silently_converted_to_audio(tmp_path):
    target = tmp_path / "video.ogg"
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=0.3",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "0.3", "-c:v", "libtheora", "-c:a", "libvorbis",
                    "-threads", "1", str(target)], check=True, timeout=20)
    with pytest.raises(HTTPException) as error:
        media.prepare_media(target.read_bytes(), "audio", filename="video.ogg")
    assert error.value.status_code == 415


@pytest.mark.skipif(not media.video_available(), reason="real codecs required")
def test_mp3_album_art_is_recognized_as_a_cover_not_a_moving_video(tmp_path):
    from PIL import Image
    source, cover, target = tmp_path / "audio.wav", tmp_path / "cover.jpg", tmp_path / "audio.mp3"
    source.write_bytes(wave_bytes())
    Image.new("RGB", (32, 32), "blue").save(cover, format="JPEG")
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error", "-y",
                    "-i", str(source), "-i", str(cover), "-map", "0:a:0", "-map", "1:v:0",
                    "-c:a", "libmp3lame", "-c:v", "copy", "-id3v2_version", "3",
                    "-disposition:v", "attached_pic", str(target)], check=True, timeout=20)
    result = media.prepare_media(target.read_bytes(), "audio", filename="audio.mp3")
    assert result.mime == "audio/mpeg" and 250 <= result.duration_ms <= 450


def compiled(kind, *, body="", followup="", custom_name=None, count=1, location=None):
    template_id = identity(100)
    entry = rule(response=sequence(*[action("template", template_id=template_id) for _ in range(count)]))
    templates = {template_id: {"kind": kind, "body": body, "followup_body": followup,
        "file_name": custom_name, "location": location,
        "asset": {"id": identity(101), "kind": kind, "file_name": "arquivo.txt"}}}
    return rules.compile_plan(entry["id"], graph(entry), templates, FACTS, CONTEXT)[0]


def test_file_caption_and_followup_have_independent_order_and_contact_substitution():
    plan = compiled("document", body="Legenda {name}", followup="Depois {my_name}", custom_name="tabela.csv")
    assert [item["action"]["type"] for item in plan] == ["document", "text"]
    assert [item["delay_seconds"] for item in plan] == [1, 0]
    assert plan[0]["action"]["file_name"] == "tabela.csv"
    assert plan[0]["action"]["caption"] == "Legenda " + FACTS["name"]
    assert plan[1]["action"]["text"] == "Depois " + FACTS["my_name"]


def test_audio_caption_is_not_dropped_and_precedes_separate_followup():
    plan = compiled("audio", body="Legenda", followup="Mensagem posterior")
    assert [item["action"]["type"] for item in plan] == ["audio", "text", "text"]
    assert plan[0]["action"]["caption"] == ""
    assert [item["action"].get("text") for item in plan[1:]] == ["Legenda", "Mensagem posterior"]
    assert sum(item["delay_seconds"] for item in plan) == 1


def test_followup_is_included_in_expanded_action_budget_before_execution():
    with pytest.raises(ValueError, match="trinta"):
        compiled("image", followup="Após arquivo", count=16)


def test_location_is_a_native_numeric_action_without_a_file_or_external_map():
    assert compiled("location", location={"latitude": -22.9, "longitude": -43.2}) == [
        {"delay_seconds": 1, "action": {"type": "location", "latitude": -22.9, "longitude": -43.2}}]
    for location in ({"latitude": True, "longitude": 0}, {"latitude": 91, "longitude": 0},
                     {"latitude": 0, "longitude": float("nan")}, {"latitude": 0, "longitude": 0, "url": "https://example.invalid"}):
        with pytest.raises(ValueError):
            compiled("location", location=location)


def test_private_audio_bytes_are_granted_only_to_the_asset_in_the_current_effect():
    asset_id = identity(700)
    effect = {"payload": {"action": {"type": "audio", "asset": {"id": asset_id}}}}
    assert broker._referenced_asset(effect, asset_id)
    assert not broker._referenced_asset(effect, identity(701))
    assert not broker._referenced_asset({"payload": {"action": {"type": "location", "asset": {"id": asset_id}}}}, asset_id)
