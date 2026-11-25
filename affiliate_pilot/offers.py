from __future__ import annotations

import asyncio
import io
import re
import unicodedata
from datetime import datetime, time as wall_time, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .niches import NICHES, niche_for_offer
from .providers import ProviderError, product_image


TOKENS = {
    "produto", "loja", "preco", "preco_anterior", "desconto", "cupom",
    "condicoes", "link", "canal", "publicidade", "observacao",
    "frase_ia", "avaliacao_vendas", "preco_destaque", "hashtags",
}
TOKEN = re.compile(r"\{([a-z_]+)\}")
FONT_ROOT = Path(__file__).resolve().parent / "static" / "fonts"


def retailer_label(offer):
    return str(offer.get("retailer_name") or offer.get("platform_name") or
               ("Shopee" if str(offer.get("identity", "")).startswith("shopee:") or offer.get("shop") else "Loja"))


def money(price, currency="BRL"):
    if price is None:
        return ""
    value = f"{Decimal(price):,.2f}".translate(str.maketrans({",": ".", ".": ","}))
    return f"{'R$' if currency == 'BRL' else currency} {value}"


def price_label(row):
    currency = row.get("currency") or ("BRL" if not row.get("platform") else None)
    if row.get("price") is not None and currency is None:
        return "Preço sem moeda confirmada"
    price = money(row.get("price"), currency)
    end = row.get("price_max")
    return f"A partir de {price}" if price and end and Decimal(end) > Decimal(row["price"]) else price


def fixed_price(row):
    try:
        price = Decimal(str(row.get("price")))
        maximum = Decimal(str(row.get("price_max")))
    except InvalidOperation:
        return None
    return price if price.is_finite() and maximum.is_finite() and price > 0 and price == maximum else None


def previous_price(row):
    price = fixed_price(row)
    try:
        previous = Decimal(str(row.get("previous_price")))
    except InvalidOperation:
        return None
    return previous if price is not None and previous.is_finite() and previous > price else None


def discount_rate(row):
    if (old := previous_price(row)) is not None:
        return int((old - fixed_price(row)) * 100 / old)
    rate = row.get("discount_rate")
    return rate if type(rate) is int and 0 < rate < 100 else 0


def preference(row):
    rate = discount_rate(row)
    count = row.get("sales") or 0
    has_previous = previous_price(row) is not None
    return (bool(rate) or has_previous, has_previous, count >= 1000, rate, count)


def sold_label(count):
    if type(count) is not int or count <= 0:
        return ""
    if count < 1000:
        return f"{count} vendido" if count == 1 else f"{count} vendidos"
    thousands, tenths = divmod(count // 100, 10)
    label = f"{thousands},{tenths}" if tenths else str(thousands)
    return f"+{label} mil vendidos"


def validate_template(text):
    stripped = TOKEN.sub("", text)
    if "{" in stripped or "}" in stripped or set(TOKEN.findall(text)) - TOKENS:
        raise ProviderError("template", "O template contém uma variável desconhecida.")


def caption(offer, template, destination=""):
    text = template["text"]
    validate_template(text)
    price = price_label(offer)
    if price.startswith("A partir de "):
        text = text.replace("\n✅ Por *{preco_destaque}*\n", "\n✅ *{preco_destaque}*\n")
    rate = discount_rate(offer)
    rating = f"{Decimal(offer['rating']):.1f}".replace(".", ",") if offer.get("rating") else ""
    if rating and offer.get("rating_scale"):
        rating += "/" + format(Decimal(str(offer["rating_scale"])).normalize(), "f")
    highlights = [rating + " de avaliação"] if rating else []
    if sales := sold_label(offer.get("sales")):
        highlights.append(sales)
    niche = offer.get("niche") or niche_for_offer({**offer, "title": offer.get("title") or "",
                                                 "categories": offer.get("categories") or []})
    currency = offer.get("currency") or ("BRL" if not offer.get("platform") else None)
    fields = {
        "produto": offer.get("title") or "", "loja": offer.get("store") or "",
        "preco": price, "preco_anterior": money(previous_price(offer), currency) if currency else "",
        "desconto": f"{rate}% OFF" if rate and not price.startswith("A partir") else "",
        "cupom": (offer.get("coupon") or {}).get("code", ""),
        "condicoes": (offer.get("coupon") or {}).get("conditions", ""),
        "link": offer.get("affiliate_url") or "", "canal": destination,
        "publicidade": "Publicidade • link de afiliado" if offer.get("affiliate_url") else "",
        "observacao": offer.get("note") or "",
        "frase_ia": offer.get("phrase") or "",
        "avaliacao_vendas": " ".join(highlights),
        "preco_destaque": f"{price} ({rate}% OFF)" if rate and price and not price.startswith("A partir") else price,
        "hashtags": " ".join(("#Oferta", "#" + re.sub(r"\W", "", retailer_label(offer)),
                              "#" + NICHES[niche]["tag"], "#Promoção")) if niche in NICHES
                    else "#Oferta #" + re.sub(r"\W", "", retailer_label(offer)) + " #Promoção",
    }
    result = []
    for line in text.splitlines():
        variables = TOKEN.findall(line)
        if any(not fields[var] for var in variables):
            continue
        def interpolate(match):
            value = fields[match[1]]
            if template.get("style") == "whatsapp" and match[1] != "link":
                return re.sub(r"[*_~`<>\r\n]", " ", value).strip()
            return value
        result.append(TOKEN.sub(interpolate, line).strip())
    final = "\n".join(result).strip()
    final = re.sub(r"\n{3,}", "\n\n", final)
    if fields["link"] and fields["link"] not in final:
        final = f"{final}\n{fields['link']}".strip()
    return final or offer.get("title") or ""

def formatted_html(text):
    pattern = re.compile(r"https://[^\s<>]+|([*_~])([^*_~\n]+)\1")
    tags = {"*": "strong", "_": "em", "~": "s"}
    parts = []
    end = 0
    for match in pattern.finditer(text):
        parts.append(escape(text[end:match.start()]))
        if match[1] is None:
            parts.append(escape(match[0]))
        else:
            tag = tags[match[1]]
            parts.append(f"<{tag}>{escape(match[2])}</{tag}>")
        end = match.end()
    parts.append(escape(text[end:]))
    return "".join(parts)


def validate_length(text, media):
    # The conservative UTF-16 count protects emoji-heavy Telegram captions.
    limit = 1024 if media == "card" else 4096
    if len(text.encode("utf-16-le")) // 2 > limit:
        raise ProviderError("template", f"O texto está acima do limite de {limit} caracteres para este formato.")


def safe_product_photo(raw):
    if not raw:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 10_000_000 or image.format not in {"JPEG", "PNG", "WEBP"}:
                return None
            image.load()
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None


@lru_cache(maxsize=64)
def card_font(size, bold=False):
    # Runtime assets are bundled with their SIL OFL; no system font/download is
    # required, and accented Portuguese glyphs are available in both weights.
    return ImageFont.truetype(str(FONT_ROOT / ("Figtree-Bold.ttf" if bold else "Figtree-Regular.ttf")), size)


def card_text(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def fit_text(draw, text, width, size, *, bold=False, minimum=18):
    text = card_text(text)
    while size > minimum and draw.textlength(text, font=card_font(size, bold)) > width:
        size -= 1
    font = card_font(size, bold)
    while text and draw.textlength(text, font=font) > width:
        text = text[:-2].rstrip() + "…" if len(text) > 1 else ""
    return text, font


def wrap_card_text(draw, text, font, width, max_lines=3):
    """Wrap by rendered glyph width, including long unbroken product names."""
    words = card_text(text).split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while word and draw.textlength(word, font=font) > width:
            end = 1
            while end < len(word) and draw.textlength(word[:end + 1], font=font) <= width:
                end += 1
            lines.append(word[:end])
            word = word[end:]
        current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1].rstrip()
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def card_png(offer, template, raw_photo=None):
    image = Image.new("RGB", (900, 970), "#f5f8fc")
    draw = ImageDraw.Draw(image)
    accent = {"simple": "#2e7bf6", "discount": "#176e54", "coupon": "#6750bb"}[template["layout"]]
    draw.rounded_rectangle((24, 24, 876, 946), radius=35, fill="#ffffff", outline="#dce3ee", width=3)
    draw.rounded_rectangle((53, 52, 847, 128), radius=20, fill=accent)
    brand, brand_font = fit_text(draw, (template.get("brand") or "Orion Afiliados")[:32], 748, 33, bold=True)
    draw.text((76, 73), brand, font=brand_font, fill="#ffffff", anchor="lt")
    photo = safe_product_photo(raw_photo)
    if photo:
        photo.thumbnail((760, 498))
        image.paste(photo, ((900 - photo.width) // 2, 147 + (498 - photo.height) // 2))
    else:
        draw.rounded_rectangle((53, 146, 847, 645), radius=24, fill="#eaf2ff")
        draw.text((450, 395), "Imagem não disponível", anchor="mm", font=card_font(31), fill="#4e5a72")
    title_font = card_font(32, True)
    for index, line in enumerate(wrap_card_text(draw, offer["title"], title_font, 786)):
        draw.text((57, 666 + index * 43), line, font=title_font, fill="#0f1b33", anchor="lt")
    price, price_font = fit_text(draw, price_label(offer) or "Preço não confirmado", 786, 44, bold=True, minimum=28)
    draw.text((57, 806), price, font=price_font, fill=accent, anchor="lt")
    rate = discount_rate(offer)
    badge = (f"{rate}% OFF" if template["layout"] == "discount" and rate
             and not price_label(offer).startswith("A partir") else None)
    if template["layout"] == "coupon" and offer.get("coupon"):
        badge = "Cupom: " + offer["coupon"]["code"]
    if badge:
        badge, badge_font = fit_text(draw, badge, 786, 25)
        draw.text((57, 877), badge, font=badge_font, fill="#0f1b33", anchor="lt")
    footer, footer_font = fit_text(draw, "Demonstração" if offer.get("demo") else "Publicidade • " + retailer_label(offer), 786, 16, minimum=14)
    draw.text((57, 917), footer, font=footer_font, fill="#4e5a72", anchor="lt")
    saved = io.BytesIO()
    image.save(saved, "PNG", optimize=False)
    return saved.getvalue()


async def render_card(offer, template):
    try:
        raw = await asyncio.wait_for(product_image(offer["image"]), 10) if offer.get("image") else None
    except (ProviderError, TimeoutError):
        raw = None
    return await asyncio.to_thread(card_png, offer, template, raw)


async def render_channel_card(offer, template):
    if not offer.get("image"):
        raise ProviderError("image", "O produto não possui uma foto para o card.")
    raw = await product_image(offer["image"])
    if safe_product_photo(raw) is None:
        raise ProviderError("image", "A foto da oferta não pôde ser usada no card.")
    png = await asyncio.to_thread(card_png, offer, template, raw)
    with Image.open(io.BytesIO(png)) as image:
        card, thumb = io.BytesIO(), io.BytesIO()
        image.save(card, format="JPEG", quality=89, optimize=True)
        image.thumbnail((140, 140))
        image.save(thumb, format="JPEG", quality=78)
    return card.getvalue(), thumb.getvalue()


def next_slot(rule, after):
    tz = ZoneInfo(rule["timezone"])
    local = datetime.fromtimestamp(after, tz)
    opens, closes = wall_time.fromisoformat(rule["opens"]), wall_time.fromisoformat(rule["closes"])
    full_day = opens == closes == wall_time(0)
    for day in range(8):
        date = (local + timedelta(days=day)).date()
        if date.weekday() not in rule["days"]:
            continue
        start = datetime.combine(date, opens, tz)
        end = datetime.combine(date + timedelta(days=int(full_day)), closes, tz)
        pick = max(local, start)
        if pick < end:
            return int(pick.timestamp())
    raise ValueError("Não foi possível encontrar uma janela de publicação.")


def local_day_start(epoch, tz):
    local = datetime.fromtimestamp(epoch, ZoneInfo(tz))
    return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def eligible(row, rule):
    price = row.get("price")
    if not price:
        return False
    text = f"{row['title']} {row.get('store','')}".casefold()
    if any(word.casefold() in text for word in rule["blocked"]):
        return False
    if row["identity"] in rule.get("blocked_ids", []):
        return False
    if rule.get("min_price") and Decimal(price) < Decimal(str(rule["min_price"])):
        return False
    if rule.get("max_price") and Decimal(row.get("price_max") or price) > Decimal(str(rule["max_price"])):
        return False
    if rule.get("min_rating") and Decimal(row.get("rating") or "0") < Decimal(str(rule["min_rating"])):
        return False
    if rule.get("min_commission") and Decimal(row.get("commission") or "0") * 100 < Decimal(str(rule["min_commission"])):
        return False
    if rule.get("min_sales") and (row.get("sales") or 0) < rule["min_sales"]:
        return False
    if rule.get("min_discount") and discount_rate(row) < rule["min_discount"]:
        return False
    return True
