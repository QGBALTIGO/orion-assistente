"""Real, synthetic PDF parsing; no document execution or external downloads."""
import io
import subprocess

from fastapi import HTTPException
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, IndirectObject, NameObject, NullObject

from app import subscription_template_pdf as pdf
from app.subscription_template_documents import inspect_document


def make_pdf(*, contents=None, metadata=False, null_object=False, incremental=False, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if contents is not None:
        stream = DecodedStreamObject()
        stream.set_data(contents)
        page[NameObject("/Contents")] = writer._add_object(stream)
    if metadata:
        writer.add_metadata({"/Title": "Contrato sintético", "/Author": "Teste"})
    if null_object:
        writer._add_object(NullObject())
    if encrypted:
        writer.encrypt("synthetic-password")
    result = io.BytesIO()
    writer.write(result)
    if incremental:
        writer = PdfWriter(result, incremental=True)
        writer.add_metadata({"/Title": "Atualização sintética"})
        result = io.BytesIO()
        writer.write(result)
    return result.getvalue()


@pytest.mark.parametrize("options", [
    {}, {"contents": b"q 0 0 100 100 re S Q"}, {"metadata": True},
    {"null_object": True}, {"incremental": True},
])
def test_common_pdf_structures_are_read_and_preserved_without_rewriting(options):
    raw = make_pdf(**options)
    assert pdf.inspect_pdf(raw) is True
    assert inspect_document(raw) == (raw, "application/pdf")


@pytest.mark.parametrize("damage", [
    lambda raw: raw[:-20],
    lambda raw: raw.replace(b"startxref", b"notaxref"),
    lambda raw: raw.replace(b"/Root 3 0 R", b"/Root 9 0 R"),
    lambda raw: raw.replace(b"/Kids [ 4 0 R ]", b"/Kids [ 9 0 R ]"),
    lambda raw: raw.replace(b"/Type /Catalog", b"/Type /Invalid"),
    lambda raw: raw + b"unexpected payload",
])
def test_incomplete_pdf_or_invalid_root_and_page_references_are_refused(damage):
    original = make_pdf()
    raw = damage(original)
    assert raw != original, "the fixture must actually exercise damaged input"
    with pytest.raises(ValueError):
        pdf.inspect_pdf(raw)
    with pytest.raises(HTTPException) as error:
        inspect_document(raw)
    assert error.value.status_code == 415


def test_declared_stream_cannot_hide_a_truncated_payload():
    raw = make_pdf(contents=b"q 0 0 100 100 re S Q")
    damaged = raw.replace(b"endstream", b"brokenend")
    assert damaged != raw
    with pytest.raises(ValueError):
        pdf.inspect_pdf(damaged)


def test_non_page_references_are_validated_too():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/Title": "Metadata"})
    writer._info.get_object()[NameObject("/Missing")] = IndirectObject(999, 0, writer)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(ValueError):
        pdf.inspect_pdf(stream.getvalue())


def test_password_protected_pdf_cannot_bypass_structure_inspection():
    with pytest.raises(ValueError):
        pdf.inspect_pdf(make_pdf(encrypted=True))


@pytest.mark.parametrize("raw", [b"", b"%PDF-1.4\n%%EOF", b"not a pdf", "not bytes",
                                  b"%PDF-1.4\n" + b" " * pdf.MAX_PDF_BYTES])
def test_file_envelope_and_ten_mb_bound_are_checked_before_starting_parser(raw, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("an invalid envelope must be rejected before spawning the parser")
    monkeypatch.setattr(pdf.subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        pdf.inspect_pdf(raw)


def test_process_timeout_is_a_controlled_invalid_document(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == pdf.PARSER_TIMEOUT_SECONDS
        assert kwargs["stderr"] is subprocess.DEVNULL
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(pdf.subprocess, "run", timeout)
    with pytest.raises(ValueError):
        pdf.inspect_pdf(make_pdf())


def test_object_count_is_bounded_after_reading_the_real_cross_reference(monkeypatch):
    raw = make_pdf()
    monkeypatch.setattr(pdf, "MAX_OBJECTS", 3)
    with pytest.raises(ValueError):
        pdf._inspect_structure(raw)
