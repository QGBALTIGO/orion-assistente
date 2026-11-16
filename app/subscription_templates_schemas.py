"""Strict owner-facing template library requests, independent of transport."""
import re
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import TemplateDefinition, clean_text

MAX_TEMPLATES = 100
UPLOAD_BYTES = 10_000_000
STICKER_UPLOAD_BYTES = 200_000
UPLOAD_BODY_BYTES = ((UPLOAD_BYTES + 2) // 3) * 4 + 8192
KINDS = ("text", "image", "video", "audio", "document", "sticker", "ptv", "location")
EXTENSIONS = {
    "image": frozenset({"jpg", "jpeg", "png", "webp"}),
    "video": frozenset({"mp4"}),
    "audio": frozenset({"mp3", "ogg", "wav", "opus"}),
    "document": frozenset({"pdf", "docx", "xlsx", "csv", "txt", "apk"}),
    "sticker": frozenset({"jpg", "jpeg", "png"}),
    "ptv": frozenset({"mp4"}),
}
MIME_EXTENSIONS = {
    "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "audio/mpeg": "mp3",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.android.package-archive": "apk",
    "text/plain; charset=utf-8": "txt",
}


def safe_filename(value):
    value = clean_text(value, maximum=120)
    if (value in {".", ".."} or any(char in value for char in '/\\:')
            or re.search(r"[\x00-\x1f\x7f]", value) or value.endswith(".")):
        raise ValueError("Nome de arquivo inválido")
    return value


def media_filename(filename, mime):
    """A per-template display name; never a filesystem or object-store path."""
    filename = safe_filename(filename)
    suffix = MIME_EXTENSIONS.get(mime)
    if suffix is None:
        raise ValueError("Formato de arquivo não permitido")
    if suffix == "txt" and PurePosixPath(filename).suffix.lower() == ".csv":
        suffix = "csv"
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = stem.strip().strip(".") or "arquivo"
    # Changing an extension may increase its length. Reject instead of silently
    # cutting a name, and keep Unicode code-point pairs intact.
    return safe_filename(stem + "." + suffix)


class LibraryTemplate(StrictModel):
    name: str = Field(default="", max_length=100)
    kind: Literal["text", "image", "video", "audio", "document", "sticker", "ptv", "location"]
    text: str = Field(default="", max_length=1000)
    followup_text: str = Field(default="", max_length=1000)
    asset_id: UUID | None = None
    location: dict | None = None
    file_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def content(self):
        self.name = clean_text(self.name, empty=self.kind == "text", maximum=100)
        if not self.name:
            self.name = "Texto sem nome"
        self.text = clean_text(self.text, empty=True, maximum=1000)
        self.followup_text = clean_text(self.followup_text, empty=True, maximum=1000)
        if self.file_name is not None:
            self.file_name = safe_filename(self.file_name)
        if self.kind in {"text", "sticker", "ptv", "location"} and self.followup_text:
            raise ValueError("Esse formato não usa mensagem complementar")
        value = TemplateDefinition.model_validate(self.model_dump())
        self.name, self.text, self.followup_text = value.name, value.text, value.followup_text
        self.location = value.location.model_dump() if value.location else None
        return self


class TemplateRequest(StrictModel):
    request_id: UUID
    expected_chatbot_revision: int = Field(strict=True, ge=1)


class CreateTemplate(TemplateRequest):
    template: LibraryTemplate


class UpdateTemplate(CreateTemplate):
    expected_revision: int = Field(strict=True, ge=1)


class DeleteTemplate(TemplateRequest):
    expected_revision: int = Field(strict=True, ge=1)


class UploadAsset(TemplateRequest):
    kind: Literal["image", "video", "audio", "document", "sticker", "ptv"]
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1, max_length=((UPLOAD_BYTES + 2) // 3) * 4)

    @field_validator("filename")
    @classmethod
    def filename_valid(cls, value):
        return safe_filename(value)

    @model_validator(mode="after")
    def extension(self):
        if PurePosixPath(self.filename).suffix.lower().removeprefix(".") not in EXTENSIONS[self.kind]:
            raise ValueError("A extensão não corresponde ao tipo de arquivo")
        return self
