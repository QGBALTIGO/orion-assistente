"""Bounded, tenant-owned image styling for affiliate publication cards."""
from __future__ import annotations
import base64
import binascii
import io
import re
from typing import Literal

from PIL import Image, ImageColor, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")
LOGO = re.compile(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)\Z")

def decode_logo(value):
    if not value:
        return None
    if not isinstance(value,str) or len(value)>17000:
        raise ValueError("Logo acima do tamanho permitido")
    match=LOGO.fullmatch(value)
    if not match:
        raise ValueError("Use um logo PNG ou JPEG")
    try:
        raw=base64.b64decode(match[2],validate=True)
        if len(raw)>12288:
            raise ValueError
        with Image.open(io.BytesIO(raw)) as im:
            if im.format != {"png":"PNG","jpeg":"JPEG"}[match[1]] or not (1<=im.width<=256 and 1<=im.height<=256):
                raise ValueError
            if getattr(im,"is_animated",False):
                raise ValueError
            im.load()
            return ImageOps.exif_transpose(im).convert("RGBA")
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError,binascii.Error):
        raise ValueError("Logo inválido ou maior que 12 KB / 256 px") from None

class ImageStyle(BaseModel):
    model_config=ConfigDict(extra="forbid")
    theme: Literal["classic","clean","dark"]="classic"
    accent: str|None=None
    background: str|None=None
    surface: str|None=None
    price: str|None=None
    handle: str=Field(default="",max_length=40)
    footer: str=Field(default="",max_length=64)
    cta: str=Field(default="",max_length=40)
    logo: str=Field(default="",max_length=17000)
    show_brand: bool=True
    show_discount: bool=True
    show_previous: bool=False
    show_store: bool=True
    show_coupon: bool=True

    @field_validator("accent","background","surface","price",mode="before")
    @classmethod
    def color(cls,v):
        if v is None or v=="":
            return None
        if not isinstance(v,str) or not HEX.fullmatch(v):
            raise ValueError("Cor inválida")
        return v.lower()

    @field_validator("handle","footer","cta")
    @classmethod
    def single_line(cls,v):
        return " ".join(v.split())

    @field_validator("logo")
    @classmethod
    def valid_logo(cls,v):
        if v:
            decode_logo(v)
        return v

def luminance(color):
    channels=ImageColor.getrgb(color)
    linear=[v/255/12.92 if v/255<=.04045 else ((v/255+.055)/1.055)**2.4 for v in channels]
    return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))

def contrast(a,b):
    x,y=sorted((luminance(a),luminance(b)),reverse=True)
    return (x+.05)/(y+.05)

def ink(background):
    return max(("#ffffff","#000000","#0f1b33"),key=lambda color:contrast(color,background))

def readable(color,background):
    return color if contrast(color,background)>=4.5 else ink(background)

def palette(template,style):
    dark=style.theme=="dark"
    accent=style.accent or {"simple":"#2e7bf6","discount":"#176e54","coupon":"#6750bb"}.get(template.get("layout"),"#2e7bf6")
    bg=style.background or ("#101318" if dark else "#f5f8fc" if style.theme=="classic" else "#ffffff")
    surface=style.surface or ("#1e222a" if dark else "#ffffff")
    return {"accent":accent,"background":bg,"surface":surface,"ink":ink(surface),
            "header_ink":ink(accent),"price":readable(style.price or accent,surface)}
