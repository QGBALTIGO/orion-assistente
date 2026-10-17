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
            # Classificação completa, com dados sintéticos, sem nenhum cliente real.
            from uuid import UUID
            from app.groq_ai import GroqServiceError, interpret
            service = UUID("11111111-1111-4111-8111-111111111111")
            try:
                decision = await interpret(
                    message="Quero agendar um Corte amanhã",
                    history=[],
                    catalog=[{"id": service, "name": "Corte"}],
                    key=key, model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                )
                logger.warning("probe_classification_result=%s",
                               "ok" if decision.service_ids == [service]
                               and decision.intent == "agendar" else "unexpected")
            except GroqServiceError as exc:
                logger.warning("probe_classification_result=error_%s", exc.status)
    except (httpx.RequestError, httpx.TimeoutException):
        logger.warning("probe_result=network_error")
