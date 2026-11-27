"""Original catalog packshots and tenant-owned Orion product photos."""
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.media_paths import is_media_url


PRODUCT_IMAGES = frozenset({
    "agua-20l", "agua-10l", "gas-p13", "gas-p45", "fardo-agua", "agua-pequena",
    "garrafa-2l", "bebidas", "gelo", "lata", "mangueira-gas", "mercearia",
    "registro-gas", "carvao", "descartaveis", "outros",
})
IMAGE_DIRECTORY = Path(__file__).parent / "static" / "delivery-products"


def tenant_photo(tenant, url):
    return is_media_url(url) and url.startswith(f"/media/{tenant}/")


def image_url(tenant, preset, photo, *, thumbnail=True):
    if tenant_photo(tenant, photo):
        return photo.removesuffix(".webp") + "/thumb.webp" if thumbnail else photo
    if preset in PRODUCT_IMAGES:
        return f"/delivery-store-assets/products/{preset}.webp"
    return None


def product_image_response(preset):
    if preset not in PRODUCT_IMAGES:
        raise HTTPException(404, "Imagem de produto não encontrada")
    return FileResponse(IMAGE_DIRECTORY / f"{preset}.webp", media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=604800",
                                 "X-Content-Type-Options": "nosniff"})
