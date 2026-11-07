"""Literal direct content; existing selected templates keep library semantics."""
from copy import deepcopy
from uuid import UUID, uuid5

from app.subscription_direct_schemas import literal_content
from app.subscription_chatbot_schemas import UrlButtonContent
from app.subscription_templates_schemas import LibraryTemplate, media_filename
from app.whatsapp_footer import action_footer

MEDIA_KINDS = frozenset({"image", "video", "audio", "document"})


def expanded_definition(message, key):
    recipients = message["recipients"]
    phones = [recipients["phone"]] if recipients["mode"] == "phone" else [
        value.removeprefix("phone:") for value in recipients["contact_keys"]]
    namespace = UUID(str(key))
    return {"interval_seconds": 10, "items": [
        {"row_key": str(uuid5(namespace, f"{index}:{phone}")), "device_id": message["device_id"],
         "source": "manual" if recipients["mode"] == "phone" else "catalog",
         "phone": phone if recipients["mode"] == "phone" else None,
         "contact_key": "phone:" + phone if recipients["mode"] == "contacts" else None,
         "message_type": message["kind"], "text": message.get("text"),
         "template_id": message.get("template_id")}
        for index, phone in enumerate(phones, 1)]}


def media_plan(message, descriptor):
    kind = descriptor["kind"]
    if kind not in MEDIA_KINDS:
        raise ValueError("O arquivo não é permitido nesta aba")
    filename = media_filename(message["file_name"], descriptor["mime"])
    if filename != message["file_name"]:
        raise ValueError("Use o nome validado durante o upload")
    caption = literal_content(message.get("caption", ""), empty=True)
    warnings = []
    if kind == "audio":
        if caption:
            warnings.append("AUDIO_CAPTION_OMITTED")
        caption = ""
    else:
        # Preserve authored paragraphs, including an explicitly typed footer.
        caption = caption.replace("\r\n", "\n").replace("\r", "\n")
    # Audio deliberately remains a single media action. A caption is never
    # converted to a follow-up text as it is in some saved library templates.
    return [{"type": kind, "asset": deepcopy(descriptor), "caption": caption,
             "file_name": filename}], warnings


def url_button_plan(message):
    # Revalidate the frozen direct content without interpolation or URL I/O.
    value = UrlButtonContent.model_validate({key: message.get(key, "") for key in
        ("text", "footer", "button_text", "url")}).model_dump()
    return [action_footer({"type": "url_button", **value})]


def optional_template(message, plan):
    save = message.get("save_template")
    if save is None:
        return None
    first = plan[0]
    value = {"name": save["name"], "kind": first["type"],
             "text": first.get("text") or first.get("caption") or ""}
    if message["kind"] == "media":
        value.update(asset_id=message["asset_id"], file_name=message["file_name"])
    # The direct message stays literal. An explicitly saved template obeys
    # the existing five-tag library validator when used in future sends.
    return LibraryTemplate.model_validate(value).model_dump(mode="json")
