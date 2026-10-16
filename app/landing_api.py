"""Landing estática: nunca inventar número, preço aprovado ou depoimentos."""
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router=APIRouter()


@router.get("/",response_class=HTMLResponse,include_in_schema=False)
async def landing():
    content=(Path.cwd()/"landing"/"index.html").read_text(encoding="utf-8")
    phone=os.getenv("SALES_WHATSAPP_E164","")
    if re.fullmatch(r"\+55[1-9][0-9]{9,10}",phone):
        url="https://wa.me/"+phone[1:]+"?text="+quote("Quero conhecer o Orion para minha barbearia.")
        cta=f'<a class="cta" href="{url}" rel="noopener noreferrer" target="_blank">Quero testar na minha barbearia ↗</a>'
    else:
        cta='<span class="cta cta-disabled" aria-label="Contato comercial em configuração">Contato comercial em configuração</span>'
    return HTMLResponse(content.replace("__SALES_CTA__",cta),
        headers={"Cache-Control":"public, max-age=60","X-Content-Type-Options":"nosniff"})
