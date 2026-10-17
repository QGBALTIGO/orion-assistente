"""Verificação opcional de autenticação Groq sem registrar chaves nem conteúdo."""
import logging
import os

import httpx

logger = logging.getLogger("orion.groq.probe")


async def probe_groq_provider():
    if os.getenv("GROQ_PROBE_ON_START") != "1":
        return
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        logger.warning("probe_result=missing_key")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": "Bearer " + key},
            )
        # Somente código HTTP; nunca imprimir corpo, token, usuário ou cabeçalhos.
        logger.warning("probe_result=http_%s", response.status_code)
        if response.status_code == 200:
            async with httpx.AsyncClient(timeout=12) as client:
                completion = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                        "messages": [
                            {"role": "system", "content": "Responda com um objeto JSON válido."},
                            {"role": "user", "content": "Devolva JSON com status ok."},
                        ],
                        "temperature": 0,
                        "max_tokens": 50,
                        "response_format": {"type": "json_object"},
                    },
                )
            logger.warning("probe_chat_result=http_%s", completion.status_code)
    except (httpx.RequestError, httpx.TimeoutException):
        logger.warning("probe_result=network_error")
